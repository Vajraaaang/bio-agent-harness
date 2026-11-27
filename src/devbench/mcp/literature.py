"""Operator-enabled literature metadata lookup and content-addressed offline replay."""

from __future__ import annotations

import hashlib
import http.client
import json
import queue
import re
import socket
import ssl
import threading
import unicodedata
from contextlib import contextmanager, suppress
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote, urlencode

import certifi

from devbench.engine.dispatch_deadline import check_deadline, remaining
from devbench.engine.workflow_operations import OperationBlocked
from devbench.mcp import LITERATURE_PROVIDERS as PROVIDERS
from devbench.platform.plugin_jobs import canonical, digest

from ._sources import PRIVATE, QUOTA, SCOPE, clinical
from .intake import verify_artifact_file

_ENDPOINTS = {
    "europe_pmc": ("www.ebi.ac.uk", "/europepmc/webservices/rest/search"),
    "pubmed_search": ("eutils.ncbi.nlm.nih.gov", "/entrez/eutils/esearch.fcgi"),
    "pubmed_summary": ("eutils.ncbi.nlm.nih.gov", "/entrez/eutils/esummary.fcgi"),
    "crossref": ("api.crossref.org", "/works"),
    "biorxiv": ("api.biorxiv.org", "/details/biorxiv"),
}
_SNAPSHOT = "devbench.mcp-literature/2"
_RESOLVERS = threading.BoundedSemaphore(4)
_SHA = re.compile(r"[0-9a-f]{64}")
_BEGIN = "BEGIN_UNTRUSTED_LITERATURE_DATA"
_END = "END_UNTRUSTED_LITERATURE_DATA"
_MAX_BYTES = 512 * 1024
_PII = re.compile(
    r"(?:@|[/\\]|https?:|file:|\b[0-9a-f]{24,}\b|"
    r"(?<![a-z])(?:private|confidential|secret|ssn)(?![a-z])|"
    r"\b\d{3}[- ]\d{2}[- ]\d{4}\b|\b(?:subject|sample|donor|case|mrn|patient)"
    r"[-_ :#]*[A-Z]*\d+\b)",
    re.I,
)
_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", re.I)
_PMID = re.compile(r"[1-9][0-9]{0,9}")
_SNAPSHOT_TABLE = """CREATE TABLE IF NOT EXISTS mcp_literature_snapshots(
    session_id TEXT NOT NULL,sha256 TEXT NOT NULL,payload TEXT NOT NULL,
    PRIMARY KEY(session_id,sha256))"""


def _outbound_query(value):
    query = " ".join(unicodedata.normalize("NFKC", value).split())
    if not query or len(query) > 500:
        raise ValueError("Invalid literature query")
    scrubbed = _DOI.sub("", query)
    if (
        clinical(query)
        or _PII.search(scrubbed)
        or any(unicodedata.category(c).startswith("C") for c in query)
    ):
        raise OperationBlocked(PRIVATE)
    return query


def _workspace_private(db):
    return bool(
        db.execute("SELECT 1 FROM mcp_private_digests LIMIT 1").fetchone()
        or db.execute(
            "SELECT 1 FROM mcp_sessions WHERE classification "
            "NOT IN ('public','synthetic') LIMIT 1"
        ).fetchone()
    )


def _legacy_private(legacy):
    from devbench.platform.plugin_jobs import JobStore

    # Retain the legacy writer fence across the outbound request so a simultaneous
    # older Studio cannot taint the workspace after this visibility decision.
    for row in legacy.execute("SELECT * FROM plugin_plans"):
        check_deadline()
        plan = JobStore._plan(row)
        if JobStore.protected_inputs(legacy, plan):
            return True
    return False


def _evidence(ctx, db, args, protected):
    if args["phase"] != "post_evidence":
        return
    from .receipts import verified_receipt

    binding = args.get("evidence_binding")
    if not binding:
        raise OperationBlocked("awaiting_information")
    sid, rid = args["session_id"], binding["run_id"]
    if protected:
        receipt = verified_receipt(ctx, sid, rid, db, local_offline=True)
    else:
        receipt = verified_receipt(ctx, sid, rid, db)
    actual = digest(receipt)
    if actual != binding["receipt_sha256"]:
        raise OperationBlocked("plan_identity_mismatch")


def _addresses(host):
    """Only a fixed provider hostname crosses this bounded DNS-only thread."""
    if not _RESOLVERS.acquire(blocking=False):
        raise OperationBlocked(QUOTA)
    replies = queue.Queue(maxsize=1)

    def lookup():
        try:
            replies.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except OSError:
            replies.put(())
        finally:
            _RESOLVERS.release()

    threading.Thread(target=lookup, daemon=True, name="literature-provider-dns").start()
    try:
        result = replies.get(timeout=remaining(1.25))
    except queue.Empty:
        raise OperationBlocked(QUOTA) from None
    check_deadline()
    return result


def _connect_socket(address, *unused):
    host, port = address
    if port != 443 or not any(host == origin for origin, _ in _ENDPOINTS.values()):
        raise OperationBlocked(SCOPE)
    for family, kind, protocol, _, endpoint in _addresses(host):
        sock = socket.socket(family, kind, protocol)
        try:
            sock.settimeout(remaining(1.25))
            sock.connect(endpoint)
            sock.settimeout(
                remaining(1.25)
            )  # TLS handshake still binds the original host.
            return sock
        except OSError:
            sock.close()
        except BaseException:
            sock.close()
            raise
    raise OperationBlocked("runtime_prerequisite_missing")


def _response_bytes(response, sock, budget):
    if response.status != 200:
        raise OperationBlocked("runtime_prerequisite_missing")
    expected = getattr(response, "length", None)
    if expected is not None and expected > budget:
        raise OperationBlocked(QUOTA)
    data = bytearray()
    # For Connection: close, read1 can release the underlying socket as soon
    # as it returns the last advertised byte. Never touch that closed descriptor.
    while expected is None or len(data) < expected:
        sock.settimeout(remaining(1.25))
        chunk = response.read1(min(32768, budget - len(data) + 1))
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > budget:
            raise OperationBlocked(QUOTA)
    if expected is not None and len(data) != expected:
        raise OperationBlocked("runtime_prerequisite_missing")
    check_deadline()
    return data


@contextmanager
def _response_deadline(sock):
    # A peer can trickle a header line without triggering a per-recv timeout.
    # This watchdog can only interrupt the already established TLS socket.
    def interrupt():
        with suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)

    alarm = threading.Timer(remaining(1.25), interrupt)
    alarm.daemon = True
    alarm.start()
    try:
        yield
    finally:
        alarm.cancel()


def _get_json(route, params=None, *, suffix="", budget=_MAX_BYTES):
    # Origin and path prefix come exclusively from the daemon. HTTPSConnection
    # never follows Location or reads proxy, cookie or netrc credentials.
    host, path = _ENDPOINTS[route]
    if suffix and route != "biorxiv":
        raise ValueError("Unexpected provider route")
    target = path + suffix + ("?" + urlencode(params) if params else "")
    check_deadline()
    connection = http.client.HTTPSConnection(
        host,
        timeout=remaining(1.25),
        context=ssl.create_default_context(cafile=certifi.where()),
    )
    connection._create_connection = _connect_socket
    try:
        connection.connect()
        sock = connection.sock
        with _response_deadline(sock):
            sock.settimeout(remaining(1.25))
            connection.request("GET", target, headers={"Accept": "application/json"})
            sock.settimeout(remaining(1.25))
            data = _response_bytes(connection.getresponse(), sock, budget)
        return json.loads(data), hashlib.sha256(data).hexdigest()
    except (OSError, ValueError, http.client.HTTPException):
        raise OperationBlocked("runtime_prerequisite_missing") from None
    finally:
        connection.close()


def _provider_rows(value, limit):
    if not isinstance(value, list) or any(not isinstance(v, dict) for v in value):
        raise ValueError("Invalid provider metadata rows")
    return value[:limit]


def _europe_pmc(query, limit):
    document, origin = _get_json(
        "europe_pmc",
        {"query": query, "pageSize": limit, "format": "json", "resultType": "lite"},
    )
    return _provider_rows(document["resultList"]["result"], limit), [origin]


def _pubmed(query, limit):
    small = 32 * 1024
    page, search_sha = _get_json(
        "pubmed_search",
        {
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retstart": 0,
            "retmax": limit,
        },
        budget=small,
    )
    result = page["esearchresult"]
    ids = result["idlist"]
    if (
        result.get("errorlist")
        or result.get("error")
        or not isinstance(ids, list)
        or len(ids) > limit
        or any(not isinstance(i, str) or not _PMID.fullmatch(i) for i in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("Invalid PubMed search identities")
    if not ids:
        return [], [search_sha]
    document, summary_sha = _get_json(
        "pubmed_summary",
        {"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
        budget=_MAX_BYTES - small,
    )
    summary = document["result"]
    if set(summary.get("uids", ())) != set(ids):
        raise ValueError("PubMed summary identities changed")
    return [_pubmed_record(summary[pmid], pmid) for pmid in ids], [
        search_sha,
        summary_sha,
    ]


def _pubmed_record(raw, pmid):
    if raw.get("uid") != pmid or raw.get("error"):
        raise ValueError("Unexpected PubMed metadata identity")
    doi = next(
        (
            item["value"]
            for item in raw.get("articleids", ())
            if item.get("idtype") == "doi"
        ),
        None,
    )
    return {
        "pmid": pmid,
        "doi": doi,
        "title": raw.get("title"),
        "pubType": raw.get("pubtype"),
    }


def _crossref(query, limit):
    data, origin = _get_json(
        "crossref",
        {"query.bibliographic": query, "rows": limit, "select": "DOI,title,relation"},
    )
    if data.get("status") != "ok":
        raise ValueError("Invalid Crossref response")
    rows = _provider_rows(data["message"]["items"], limit)
    result = []
    for row in rows:
        titles = row.get("title") or []
        # A notice that retracts another DOI does not make the notice retracted.
        retracted = bool((row.get("relation") or {}).get("is-retracted-by"))
        result.append(
            {
                "doi": row.get("DOI"),
                "title": titles[0] if titles else "",
                "retraction_status": "retracted" if retracted else "unknown",
            }
        )
    return result, [origin]


def _biorxiv_selector(query):
    # bioRxiv details accepts a DOI or date interval. Never interpret a free-text
    # request as an arbitrary URL, and never substitute another search provider.
    doi = re.fullmatch(r"(10[.]\d{4,9})/([a-z0-9][a-z0-9._()-]{0,180})", query, re.I)
    if doi:
        return "/" + quote(doi[1], safe=".") + "/" + quote(
            doi[2], safe=""
        ) + "/na/json", query.lower()
    dates = re.fullmatch(r"(\d{4}-\d\d-\d\d)(?:[.][.](\d{4}-\d\d-\d\d))?", query)
    if query.casefold() == "recent":
        end = datetime.now(UTC).date()
        begin = end - timedelta(days=1)
    elif dates:
        try:
            begin = date.fromisoformat(dates[1])
            end = date.fromisoformat(dates[2] or dates[1])
        except ValueError:
            raise OperationBlocked("invalid_request") from None
    else:
        raise OperationBlocked("invalid_request")
    if not timedelta(0) <= end - begin <= timedelta(days=7):
        raise OperationBlocked("invalid_request")
    return f"/{begin}/{end}/0/json", None


def _biorxiv(query, limit):
    selector, doi = _biorxiv_selector(query)
    data, origin = _get_json("biorxiv", suffix=selector)
    records = _provider_rows(data["collection"], limit)
    for record in records:
        if (
            (record.get("server") or "").casefold() != "biorxiv"
            or doi
            and (record.get("doi") or "").casefold() != doi
        ):
            raise ValueError("Unexpected bioRxiv metadata identity")
    return records, [origin]


def _metadata(provider, query, limit):
    functions = {
        "pubmed": _pubmed,
        "europe_pmc": _europe_pmc,
        "crossref": _crossref,
        "biorxiv": _biorxiv,
    }
    if provider not in functions:
        raise OperationBlocked("runtime_prerequisite_missing")
    if type(limit) is not int or not 1 <= limit <= 25:
        raise OperationBlocked("invalid_request")
    try:
        rows, roots = functions[provider](query, limit)
        return [{**row, "provider": provider} for row in rows], roots
    except (ValueError, KeyError, TypeError, AttributeError):
        raise OperationBlocked("runtime_prerequisite_missing") from None


def _text(value):
    if not isinstance(value, str):
        return ""
    cleaned = " ".join(value.split())[:512]
    return cleaned.replace(_BEGIN, "[delimiter]").replace(_END, "[delimiter]")


def _status(row):
    if row.get("retraction_status") in {"unknown", "not_flagged", "retracted"}:
        return row["retraction_status"]
    if str(row.get("isRetracted", "")).lower() in {"true", "y", "1"}:
        return "retracted"
    if "retracted publication" in str(row.get("pubType", "")).lower():
        return "retracted"
    if str(row.get("isRetracted", "")).lower() in {"false", "n", "0"}:
        return "not_flagged"
    return "unknown"


def _normalize(row, now, *, replay):
    if not isinstance(row, dict):
        raise ValueError("Invalid bibliographic record")
    pmid = str(row.get("pmid") or "")
    doi = str(row.get("doi") or "").strip().lower()
    if not _PMID.fullmatch(pmid):
        pmid = ""
    if not _DOI.fullmatch(doi) or len(doi) > 256:
        doi = ""
    if not (pmid or doi):
        return None
    title = _text(row.get("title", ""))
    if not title and isinstance(row.get("untrusted_literature_data"), list):
        wrapped = row["untrusted_literature_data"]
        if len(wrapped) == 3 and isinstance(wrapped[1], dict):
            title = _text(wrapped[1].get("title", ""))
    timestamp = (row.get("retrieved_at_utc") or now) if replay else now
    stamp = datetime.fromisoformat(timestamp)
    if stamp.tzinfo is None:
        raise ValueError("Literature timestamps require a time zone")
    return {
        "provider": row.get("provider")
        if row.get("provider") in PROVIDERS
        else "pinned_snapshot",
        "pmid": pmid or None,
        "doi": doi or None,
        "retrieved_at_utc": stamp.astimezone(UTC).isoformat(),
        "retrieval_timestamp_origin": (
            "supplied_snapshot"
            if row.get("retrieved_at_utc")
            else "first_local_acquisition"
        )
        if replay
        else "daemon_provider_retrieval",
        "retraction_status": _status(row),
        "untrusted_literature_data": [_BEGIN, {"title": title}, _END],
    }


def _registered_snapshot(ctx, db, sid, sha, protected):
    record_row = db.execute(
        "SELECT artifact_id FROM mcp_artifacts WHERE session_id=? AND sha256=?",
        (sid, sha),
    ).fetchone()
    if not record_row:
        raise OperationBlocked(SCOPE)
    record = ctx.artifact(sid, record_row[0], sha, db)
    if record["ref"]["format"] != "json" or record["ref"]["kind"] not in {
        "reference",
        "table",
        "metadata",
    }:
        raise OperationBlocked(SCOPE)
    if not protected:
        ctx.check_source(db, sid, sha)
    with verify_artifact_file(ctx, record, allow_private=protected) as stream:
        raw = stream.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise OperationBlocked(QUOTA)
        document = json.loads(raw)
    return document


def _snapshot_scope(ctx, db, sid, sha, origin, private):
    roots = origin.get("sha256s") if isinstance(origin, dict) else None
    if (
        not isinstance(roots, list)
        or not 1 <= len(roots) <= 32
        or any(not isinstance(root, str) or not _SHA.fullmatch(root) for root in roots)
    ):
        raise OperationBlocked(SCOPE)
    # The context walks the same transitive artifact roots as the scheduler.
    digests = [sha, *roots]
    ctx.check_embargo(digests, db=db)
    if ctx.protected(digests=digests, db=db) and not private:
        raise OperationBlocked(PRIVATE)
    if origin.get("kind") == "registered_input":
        row = db.execute(
            "SELECT artifact_id FROM mcp_artifacts WHERE session_id=? AND sha256=?",
            (sid, roots[0]),
        ).fetchone()
        if not row:
            raise OperationBlocked(SCOPE)
        ctx.artifact(sid, row[0], roots[0], db)
        if not private:
            ctx.check_source(db, sid, roots[0])
    elif (
        origin.get("kind") != "provider_response"
        or origin.get("provider") not in PROVIDERS
    ):
        raise OperationBlocked(SCOPE)


def _cached(ctx, db, args, sha, private, limit):
    sid = args["session_id"]
    row = db.execute(
        "SELECT payload FROM mcp_literature_snapshots WHERE session_id=? AND sha256=?",
        (sid, sha),
    ).fetchone()
    if row is None:
        return None
    doc = json.loads(row[0])
    if digest(doc) != sha:
        raise OperationBlocked("plan_identity_mismatch")
    # Older cache records retain no input lineage. Keep their bytes for custody
    # but require reacquisition from the original registered artifact/provider.
    if doc.get("format") != _SNAPSHOT:
        raise OperationBlocked(SCOPE)
    _snapshot_scope(ctx, db, sid, sha, doc.get("origin"), private)
    if (
        doc["query_sha256"] != digest(args["query"])
        or args["phase"] != doc["phase"]
        or args.get("evidence_binding") != doc["evidence_binding"]
    ):
        raise OperationBlocked("plan_identity_mismatch")
    if private:
        return []
    return [
        {**row, "snapshot_sha256": sha} for row in _provider_rows(doc["records"], limit)
    ]


def _first_acquisition(db, sha):
    db.execute("""CREATE TABLE IF NOT EXISTS mcp_literature_acquisitions(
        source_sha256 TEXT PRIMARY KEY, acquired_at_utc TEXT NOT NULL)""")
    db.execute(
        "INSERT OR IGNORE INTO mcp_literature_acquisitions VALUES (?,?)",
        (sha, datetime.now(UTC).isoformat()),
    )
    return db.execute(
        "SELECT acquired_at_utc FROM mcp_literature_acquisitions WHERE source_sha256=?",
        (sha,),
    ).fetchone()[0]


def _import(ctx, db, args, sha, private, limit):
    sid = args["session_id"]
    origin = {"kind": "registered_input", "sha256s": [sha]}
    document = _registered_snapshot(ctx, db, sid, sha, private)
    if isinstance(document, dict) and str(document.get("format", "")).startswith(
        "devbench.mcp-literature/"
    ):
        # A cache envelope must come from this session's verified cache row.
        raise OperationBlocked(SCOPE)
    _snapshot_scope(ctx, db, sid, sha, origin, private)
    rows = document.get("records") if isinstance(document, dict) else document
    records = [
        {**row, "provider": "pinned_snapshot"} for row in _provider_rows(rows, limit)
    ]
    return records, origin, _first_acquisition(db, sha)


def _online(ctx, db, sid, query, limit):
    provider = ctx.config.get("literature_provider")
    if ctx.offline_only or provider not in PROVIDERS:
        raise OperationBlocked("runtime_prerequisite_missing")
    for row in db.execute(
        "SELECT sha256 FROM mcp_artifacts WHERE session_id=?", (sid,)
    ):
        check_deadline()
        ctx.check_source(db, sid, row[0])
    check_deadline()
    return _metadata(provider, _outbound_query(query), limit)


def query_literature(ctx, args, db):
    """Return metadata-shaped, delimited records; persist exact local replay bytes."""
    sid = args["session_id"]
    sha = args.get("offline_snapshot_sha256")
    with ctx.jobs._write() as legacy:
        private = (
            ctx.protected(sid, db=db)
            or _workspace_private(db)
            or _legacy_private(legacy)
        )
        return _pinned_query(ctx, db, args, sid, sha, private)


def _pinned_query(ctx, db, args, sid, sha, private):
    if private and not (sha and ctx.offline_only):
        raise OperationBlocked(PRIVATE)
    _evidence(ctx, db, args, private)
    db.execute(_SNAPSHOT_TABLE)
    limit = args.get("max_results", 10)
    if sha:
        cached = _cached(ctx, db, args, sha, private, limit)
        if cached is not None:
            return cached
        raw, origin, now = _import(ctx, db, args, sha, private, limit)
    else:
        raw, roots = _online(ctx, db, sid, args["query"], limit)
        origin = {
            "kind": "provider_response",
            "provider": ctx.config["literature_provider"],
            "sha256s": roots,
        }
        now = datetime.now(UTC).isoformat()
    records = [
        result
        for row in raw[:limit]
        if (result := _normalize(row, now, replay=bool(sha))) is not None
    ]
    if private:
        return []
    snapshot = {
        "format": _SNAPSHOT,
        "origin": origin,
        "phase": args["phase"],
        "query_sha256": digest(args["query"]),
        "evidence_binding": args.get("evidence_binding"),
        "records": records,
    }
    result_sha = digest(snapshot)
    _snapshot_scope(ctx, db, sid, result_sha, origin, private)
    db.execute(
        "INSERT OR IGNORE INTO mcp_literature_snapshots VALUES (?,?,?)",
        (sid, result_sha, canonical(snapshot).decode()),
    )
    return [{**item, "snapshot_sha256": result_sha} for item in records]
