"""Bounded, opt-in publication identity and verbatim evidence companions.

Bibliographic metadata is independently *declared*, not authenticated by this
module. Exact identifiers and byte locators reject mistaken/forged references;
they cannot decide whether a quote supports an interpretation or is true. No
model, search engine, dataset or network is accessed on import or local review.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import os
import re
import time
from urllib.parse import quote, unquote, urljoin, urlsplit

from .episode_proposals import _absolute, _load, _seal, require_disjoint_output
from devbench.study.evidence import _read, decode_json


FORMAT = "devbench.rna-publication-evidence/1"
MAX_SOURCE_BYTES = 10_000_000
MAX_AGGREGATE_BYTES = 20_000_000
MAX_EXCERPT_BYTES = 16_000
_IDENTITY_FIELDS = {"doi", "pmid", "pmcid", "version", "title"}
_SCOPE = "verbatim_publication_evidence_not_semantic_or_scientific_validation"
CROSSREF_DOCUMENTATION = "https://www.crossref.org/documentation/retrieve-metadata/rest-api/"
CROSSREF_UPDATE_DOCUMENTATION = "https://www.crossref.org/documentation/retrieve-metadata/retraction-watch/"


def _copy(value):
    from devharness_core.provenance import canonical_json
    encoded = canonical_json(value)
    if len(encoded) > 1_000_000:
        raise ValueError("Publication metadata exceeds its byte limit")
    return decode_json(encoded)


def _keys(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError("Use exactly the declared publication fields")


def _text(value, maximum=4000):
    if type(value) is not str or not value.strip() or len(value.encode("utf-8")) > maximum:
        raise ValueError("Use bounded nonempty publication text")
    return value


def _identifier(value):
    if type(value) is not str or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", value) is None:
        raise ValueError("Invalid publication evidence identifier")
    return value


def _sha(value):
    if type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ValueError("Use an externally retained SHA-256 identity")
    return value


def _list(value, maximum, *, nonempty=False):
    if type(value) is not list or len(value) > maximum or (nonempty and not value):
        raise ValueError("Publication list exceeds its declared bound")
    return value


def _url(value):
    from .method_sources import _check_url_text
    _check_url_text(value)
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.port not in (None, 443)):
        raise ValueError("Publication sources require credential-free HTTPS URLs")
    return value


def _canonical_identifier(field, value):
    if value is None:
        return None
    value = _text(value, 1000)
    if field == "doi":
        value = value.lower()
        for prefix in ("https://doi.org/", "doi:"):
            if value.startswith(prefix):
                value = value[len(prefix):]
        if (not re.fullmatch(r"10\.[0-9]{4,9}/[^\s]+", value)
                or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
            raise ValueError("Use an exact DOI, not a search query")
    elif field == "pmid":
        if not re.fullmatch(r"[1-9][0-9]{0,11}", value):
            raise ValueError("Use an exact PMID")
    elif field == "pmcid":
        value = value.upper()
        if not re.fullmatch(r"PMC[1-9][0-9]{0,11}", value):
            raise ValueError("Use an exact PMCID")
    return value


def _identity(value):
    _keys(value, _IDENTITY_FIELDS)
    result = {field: _canonical_identifier(field, value[field]) for field in sorted(_IDENTITY_FIELDS)}
    if not any(result[field] for field in ("doi", "pmid", "pmcid")):
        raise ValueError("Publication identity requires a DOI, PMID or PMCID")
    if result["version"] is None or result["title"] is None:
        raise ValueError("Freeze the exact publication title and version")
    return result


def validate_publication_identity(expected, candidate):
    """Exact approved identity matching; title-only/fuzzy matching is rejected.

    Expected is {identity, approved_urls}; candidate is {identity, source_url}.
    Every supplied identifier must agree. Additional identifiers may not be
    silently introduced: curator must first freeze a reconciled expected record.
    """
    _keys(expected, {"identity", "approved_urls"})
    _keys(candidate, {"identity", "source_url"})
    identity, observed = _identity(expected["identity"]), _identity(candidate["identity"])
    urls = [_url(url) for url in _list(expected["approved_urls"], 16, nonempty=True)]
    if len(set(urls)) != len(urls):
        raise ValueError("Duplicate approved publication URLs")
    if _url(candidate["source_url"]) not in urls or observed != identity:
        raise ValueError("Publication identifier, title, version or approved source mismatch")
    return {"identity": identity, "source_url": candidate["source_url"]}


def resolve_publication(expected, candidates):
    """Resolve an already bounded discovery response without acquiring papers.

    An empty or ambiguous exact match is rejected. Conflicting records sharing
    any supplied identifier are rejected rather than hidden by a good match.
    Candidate discovery is separate from source acquisition and needs its own
    explicit authority; this helper never searches the web.
    """
    _keys(expected, {"identity", "approved_urls"})
    identity = _identity(expected["identity"])
    matches = []
    for candidate in _list(candidates, 32, nonempty=True):
        _keys(candidate, {"identity", "source_url"})
        observed = _identity(candidate["identity"])
        _url(candidate["source_url"])
        related = any(identity[field] is not None and identity[field] == observed[field]
                      for field in ("doi", "pmid", "pmcid"))
        if related:
            matches.append(validate_publication_identity(expected, candidate))
    if len(matches) != 1:
        raise ValueError("Publication resolution requires one unambiguous exact approved match")
    return matches[0]


def validate_exposure(exposure):
    _keys(exposure, {"prior_outcome_access", "public_outcomes_accessible",
                     "workflow_author_outcome_access", "limitations"})
    for field in ("prior_outcome_access", "public_outcomes_accessible", "workflow_author_outcome_access"):
        if exposure[field] not in {"yes", "no", "unknown"}:
            raise ValueError("Declare observed, denied or unknown reference exposure")
    _text(exposure["limitations"])
    return _copy(exposure)


def _locator(locator, body):
    _keys(locator, {"start_byte", "end_byte", "text", "location"})
    start, end = locator["start_byte"], locator["end_byte"]
    if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(body)
            or end - start > MAX_EXCERPT_BYTES):
        raise ValueError("Use bounded half-open byte locators")
    text = _text(locator["text"], MAX_EXCERPT_BYTES)
    if body[start:end] != text.encode("utf-8"):
        raise ValueError("Publication excerpt differs from the exact retained source bytes")
    _text(locator["location"], 1000)
    return locator


def validate_publication_evidence(record, *, workspace=None, allow_evidence_read=False, max_aggregate_bytes=None):
    """Validate hash-bound source files and quote/metadata anchors.

    Sources stay read-only at their retained absolute paths. An optional workspace
    bounds all source reads. Independent curator identity is a declaration, not
    a signature; metadata anchors do not authenticate a publisher or article.
    """
    if allow_evidence_read is not True:
        raise PermissionError("Publication evidence reading requires explicit authority")
    cap = MAX_AGGREGATE_BYTES if max_aggregate_bytes is None else max_aggregate_bytes
    if type(cap) is not int or not 1 <= cap <= MAX_AGGREGATE_BYTES:
        raise ValueError("Publication aggregate byte budget exceeds the existing bound")
    record = _copy(record)
    _keys(record, {"format", "publication", "sources", "excerpts", "exposure",
                   "curator_id", "participant_id", "scope"})
    if record["format"] != FORMAT or record["scope"] != _SCOPE:
        raise ValueError("Unsupported publication evidence format/scope")
    curator, participant = _identifier(record["curator_id"]), _identifier(record["participant_id"])
    if curator == participant:
        raise ValueError("Participant cannot independently curate its reference evidence")
    publication = record["publication"]
    _keys(publication, {"identity", "approved_urls"})
    validate_publication_identity(publication, {"identity": publication["identity"],
                                  "source_url": publication["approved_urls"][0] if publication["approved_urls"] else ""})
    validate_exposure(record["exposure"])
    root = _absolute(workspace) if workspace is not None else None
    bodies, remaining = {}, cap
    for source in _list(record["sources"], 16, nonempty=True):
        _keys(source, {"source_id", "path", "sha256", "source_url", "identity", "access",
                       "identity_locators", "identity_reviewed_by", "limitations"})
        key = _identifier(source["source_id"])
        if key in bodies:
            raise ValueError("Duplicate publication source identifier")
        validate_publication_identity(publication, {"identity": source["identity"], "source_url": source["source_url"]})
        if source["identity_reviewed_by"] != curator:
            raise ValueError("Independent metadata review must identify the declared curator")
        if source["access"] not in {"full_text", "abstract", "metadata", "extracted_text"}:
            raise ValueError("Declare the actual publication access level")
        _text(source["limitations"])
        path = _absolute(source["path"])
        if root is not None and not path.is_relative_to(root):
            raise ValueError("Publication evidence source lies outside the approved workspace")
        body = _read(path, min(MAX_SOURCE_BYTES, remaining))
        remaining -= len(body)
        if hashlib.sha256(body).hexdigest() != _sha(source["sha256"]):
            raise ValueError("Retained publication source hash mismatch")
        locators = source["identity_locators"]
        _keys(locators, {field for field, value in _identity(source["identity"]).items() if value is not None})
        for field, locator in locators.items():
            _locator(locator, body)
            if _canonical_identifier(field, locator["text"]) != _identity(source["identity"])[field]:
                raise ValueError("Metadata locator does not match the declared publication identity")
        bodies[key] = body
    seen = set()
    for excerpt in _list(record["excerpts"], 64, nonempty=True):
        _keys(excerpt, {"excerpt_id", "source_id", "locator", "visibility", "purpose"})
        key = _identifier(excerpt["excerpt_id"])
        if key in seen or excerpt["source_id"] not in bodies:
            raise ValueError("Duplicate excerpt or unknown publication source")
        seen.add(key)
        _locator(excerpt["locator"], bodies[excerpt["source_id"]])
        if excerpt["purpose"] not in {"question", "data", "methods", "outcome"}:
            raise ValueError("Declare excerpt purpose")
        if excerpt["visibility"] not in {"participant", "evaluator"}:
            raise ValueError("Declare excerpt custody")
        if excerpt["purpose"] == "outcome" and excerpt["visibility"] != "evaluator":
            raise ValueError("Reference outcome excerpts must stay evaluator-held")
    return record


def prepare_publication_evidence(output, publication, sources, excerpts, exposure, *,
                                 curator_id, participant_id, workspace=None, allow_evidence_read=False):
    record = validate_publication_evidence({"format": FORMAT, "publication": publication,
        "sources": sources, "excerpts": excerpts, "exposure": exposure, "curator_id": curator_id,
        "participant_id": participant_id, "scope": _SCOPE},
        workspace=workspace, allow_evidence_read=allow_evidence_read)
    output = require_disjoint_output(_absolute(output), [source["path"] for source in sources])
    receipt = _seal(output, "publication-evidence.json", record, "analysis.publication_evidence", scope=_SCOPE)
    return {"manifest_path": str(output / "audit/manifest.json"), "root_hash": receipt["root_hash"]}


def load_publication_evidence(manifest, expected_root, *, workspace=None, allow_evidence_read=False):
    if allow_evidence_read is not True:
        raise PermissionError("Publication evidence reading requires explicit authority")
    _, record, _ = _load(_absolute(manifest), _sha(expected_root), "publication-evidence.json")
    return validate_publication_evidence(record, workspace=workspace, allow_evidence_read=True)


def participant_publication_context(manifest, expected_root, *, allow_evidence_read=False):
    """Project method/question/data quotes only; no held source paths or outcomes.

    Projection is not an OS permission boundary, a leakage detector or proof of
    blinding. Participant-visible labels are curator declarations.
    """
    record = load_publication_evidence(manifest, expected_root, allow_evidence_read=allow_evidence_read)
    return {"publication": record["publication"]["identity"], "exposure": record["exposure"],
            "excerpts": [{"excerpt_id": excerpt["excerpt_id"], "purpose": excerpt["purpose"],
                          "text": excerpt["locator"]["text"], "location": excerpt["locator"]["location"]}
                         for excerpt in record["excerpts"] if excerpt["visibility"] == "participant"],
            "reference_root": expected_root, "scope": _SCOPE}


def _retrieve_approved_text(url, approved_urls, output, *, allow_network=False, max_bytes=1_000_000,
                            content_types=None):
    """Reuse safe pinned public HTTPS transport with exact pre-hop URL admission."""
    from . import method_sources as transport
    if allow_network is not True:
        raise PermissionError("Publication acquisition requires explicit network authority")
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_SOURCE_BYTES:
        raise ValueError("Freeze a bounded publication response byte cap")
    urls = [_url(item) for item in _list(approved_urls, 16, nonempty=True)]
    for approved in urls:
        _reject_retired_publication_url(approved)
    if url not in urls:
        raise PermissionError("Publication URL was not explicitly approved")
    hosts = {transport._host(urlsplit(item).hostname) for item in urls}
    path, parent = transport._open_parent(_absolute(output))
    try:
        transport._absent(parent, path.name)
        deadline, current = time.monotonic() + transport.SOURCE_TIMEOUT_SECONDS, url
        for redirects in range(4):
            if current not in urls:
                raise PermissionError("Publication redirect URL was not explicitly approved")
            _reject_retired_publication_url(current)
            host, target = transport._check_url(current, hosts)
            kwargs = {} if content_types is None else {"content_types": content_types}
            location, body, content_type = transport._fetch_hop(host, target, deadline, max_bytes, **kwargs)
            if location is None:
                break
            if redirects == 3:
                raise ValueError("Publication redirect limit exceeded")
            transport._check_url_text(location)
            current = urljoin(current, location)
        transport._remaining(deadline)
        _, fresh_parent = transport._open_parent(path)
        try:
            before, after = os.fstat(parent), os.fstat(fresh_parent)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise ValueError("Publication output directory changed during acquisition")
            transport._write_once(fresh_parent, path.name, body)
        finally:
            os.close(fresh_parent)
        return {"requested_url": url, "final_url": current, "path": str(path),
            "content_sha256": hashlib.sha256(body).hexdigest(), "content_type": content_type,
            "retrieved_at": datetime.now(timezone.utc).isoformat(), "size_bytes": len(body),
            "evidence_scope": "retrieved_bytes_not_publication_authenticity_or_scientific_validation"}
    finally:
        os.close(parent)


def _reject_retired_publication_url(url):
    """Reject new acquisitions through the retired PMC OA service only.

    Existing retained /1 manifests and parser contracts are unaffected.
    """
    from .method_sources import _host
    parsed = urlsplit(url)
    if (_host(parsed.hostname) in {"www.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov"}
            and unquote(parsed.path).rstrip("/").split("/")[-1].lower() == "oa.fcgi"):
        raise ValueError("PMC OA service route is retired; use a current pinned provider capability")


def crossref_doi_metadata_url(doi):
    """Build one official singleton lookup; never infer an identifier from prose."""
    normalized = _canonical_identifier("doi", doi)
    if normalized is None:
        raise ValueError("Exact DOI lookup requires a supplied DOI")
    return "https://api.crossref.org/works/" + quote(normalized, safe="")


def decode_crossref_doi_metadata(body, *, expected_doi, expected_title=None):
    """Decode bounded singleton metadata without treating it as full publication.

    Only the declared 1.0.0 envelope is supported; schema drift fails closed.
    Curator still reconciles PMID/PMCID, publisher title/version, update notices,
    license/access and full-text identity. Missing update fields never mean that
    a paper is unretracted. Provider links are observations, never retrieval
    authority. Fixtures exercise this decoder; no deployed API is qualified.
    """
    if type(body) is not bytes or len(body) > 1_000_000:
        raise ValueError("Use bounded raw Crossref metadata bytes")
    value = decode_json(body)
    _keys(value, {"status", "message-type", "message-version", "message"})
    if value["status"] != "ok" or value["message-type"] != "work" or value["message-version"] != "1.0.0":
        raise ValueError("Unsupported Crossref singleton response schema")
    work = value["message"]
    if type(work) is not dict or not {"DOI", "title", "type"} <= set(work):
        raise ValueError("Crossref response lacks exact DOI/title/work-type metadata")
    doi = _canonical_identifier("doi", work["DOI"])
    if doi != _canonical_identifier("doi", expected_doi):
        raise ValueError("Crossref returned a different publication DOI")
    titles = _list(work["title"], 8, nonempty=True)
    if len(titles) != 1:
        raise ValueError("Crossref returned ambiguous publication titles")
    title = _text(titles[0], 1000)
    if expected_title is not None and title != _text(expected_title, 1000):
        raise ValueError("Crossref publication title conflicts with the frozen identity")
    work_type = _text(work["type"], 100)
    notices = _copy({key: work[key] for key in ("update-to", "relation", "update-policy") if key in work})
    links = _copy(work.get("link", []))
    _list(links, 32)
    for link in links:
        if type(link) is not dict or "URL" not in link:
            raise ValueError("Malformed Crossref full-text link metadata")
        # Do not grant network authority from provider-supplied links, including HTTP.
        _text(link["URL"], 2000)
    return {"format": "devbench.crossref-doi-metadata/1", "doi": doi, "title": title,
        "work_type": work_type, "source_sha256": hashlib.sha256(body).hexdigest(),
        "provider_schema": "singleton-work/1.0.0", "documentation": CROSSREF_DOCUMENTATION,
        "update_documentation": CROSSREF_UPDATE_DOCUMENTATION, "update_notices": notices,
        "update_status": "notices_require_review" if notices else "unknown_not_assessed",
        "full_text_links": links, "full_text_acquired": False,
        "publication_version": "unverified", "pmid": None, "pmcid": None,
        "resolution_status": "exact_doi_metadata_candidate_requires_independent_publication_review"}


def retrieve_crossref_doi_metadata(doi, output, *, approved_url, allow_network=False,
                                   expected_title=None, max_bytes=1_000_000):
    """One explicitly approved identifier lookup, no paper/model/archive access."""
    if allow_network is not True:
        raise PermissionError("DOI metadata lookup requires explicit network authority")
    url = crossref_doi_metadata_url(doi)
    if approved_url != url:
        raise PermissionError("Approval must bind the exact DOI metadata lookup URL")
    if type(max_bytes) is not int or not 1 <= max_bytes <= 1_000_000:
        raise ValueError("Crossref metadata byte cap exceeds decoder bounds")
    content_types = {"application/json", "application/vnd.crossref-api-message+json"}
    retrieval = _retrieve_approved_text(url, [approved_url], output,
        allow_network=True, max_bytes=max_bytes, content_types=content_types)
    if retrieval["content_type"] not in content_types:
        raise ValueError("Unsupported Crossref metadata content type")
    body = _read(_absolute(retrieval["path"]), max_bytes)
    if hashlib.sha256(body).hexdigest() != retrieval["content_sha256"]:
        raise ValueError("Crossref metadata changed after retrieval")
    return {"retrieval": retrieval, "metadata": decode_crossref_doi_metadata(body,
        expected_doi=doi, expected_title=expected_title)}


def retrieve_publication_source(publication, candidate, output, *, allow_network=False, max_bytes=1_000_000):
    """Opt-in acquisition of one approved public text URL via existing safe I/O.

    This is exact-URL resolution, not open-ended search or PDF/image extraction.
    Every redirect URL needs approval before that hop is contacted. Returned
    bytes need independent identity/excerpt curation before admission.
    """
    if allow_network is not True:
        raise PermissionError("Publication acquisition requires explicit network authority")
    match = validate_publication_identity(publication, candidate)
    result = _retrieve_approved_text(match["source_url"], publication["approved_urls"], output,
        allow_network=True, max_bytes=max_bytes)
    return {**result, "identity": match["identity"], "identity_validation": "independent_curation_required"}
