"""Coordinator-owned bounded discovery, separate from tool-less selection.

Provider adapters are trusted runtime capabilities, never supplied model code.
The caller must reserve discovery in its operation ledger before dispatch; this
module deliberately never retries a search or acquisition. Returned evidence
proves retained provider observations, not source authenticity or scientific merit.
"""
from __future__ import annotations

from copy import deepcopy
import base64
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import time
from urllib.parse import quote, urlencode, urljoin, urlsplit, unquote
import uuid

from devharness_core.provenance import canonical_json
from .method_executor import _path
from .method_sources import _check_url, _host, retrieve_source
from .methods import _digest, _label, _now


FORMAT = "devbench.workflow-discovery/1"
MAX_RESPONSE_BYTES = 1_000_000
_REF_FIELDS = ("artifact_id", "sha256", "artifact_type", "size_bytes")
_SOURCE_FIELDS = {"identity", "title", "url", "kind", "access", "excerpt"}


class DiscoveryProviderError(ValueError):
    """Failed interpretation with retained bounded provider bytes and usage."""
    def __init__(self, message, response):
        super().__init__(message)
        self.response = response


def _json(value, maximum=MAX_RESPONSE_BYTES):
    raw = canonical_json(value)
    if len(raw) > maximum:
        raise ValueError("Discovery JSON exceeds its byte budget")
    # Reject NaN/Infinity even if a provider's serializer would permit them.
    return json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite discovery JSON")))


def _text(value, maximum):
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ValueError("Discovery text must be nonempty and bounded")
    return value


def _request(value, existing_sources):
    if type(value) is dict and value.get("mode") == "supplied_only":
        if (set(value) != {"mode", "reason", "query", "allowed_hosts", "acquire_indices"}
                or value["query"] is not None or value["allowed_hosts"] != [] or value["acquire_indices"] != []):
            raise ValueError("Supplied-only discovery request must explicitly perform no search or acquisition")
        _text(value["reason"], 2000)
        return deepcopy(value)
    if type(value) is not dict or set(value) != {"query", "allowed_hosts", "acquire_indices"}:
        raise ValueError("Use the exact bounded discovery request")
    result = deepcopy(value)
    _text(result["query"], 1000)
    hosts = result["allowed_hosts"]
    if type(hosts) is not list or not 1 <= len(hosts) <= 32:
        raise ValueError("Discovery request requires explicit bounded acquisition hosts")
    result["allowed_hosts"] = sorted({_host(host) for host in hosts})
    indices = result["acquire_indices"]
    if (type(indices) is not list or len(indices) > 3 or len(indices) + existing_sources > 3
            or any(type(index) is not int or not 0 <= index < 10 for index in indices)
            or len(set(indices)) != len(indices)):
        raise ValueError("Discovery acquisition must preserve the three-source budget")
    return result


def _retain(planner, value, actor):
    body = canonical_json(_json(value, 2 * MAX_RESPONSE_BYTES))
    directory = _path(planner.methods.root / "workflow-discovery")
    directory.mkdir(mode=0o700, exist_ok=True)
    target = _path(directory / (uuid.uuid4().hex + ".json"))
    with target.open("xb") as stream:
        stream.write(body)
    artifact = planner.methods.import_artifact(target, artifact_type="json", actor=actor)
    return {key: artifact[key] for key in _REF_FIELDS}


def _read_ref(planner, ref):
    if type(ref) is not dict or set(ref) != set(_REF_FIELDS):
        raise ValueError("Discovery requires an exact immutable artifact reference")
    path = planner.methods.artifact_path(ref["artifact_id"])
    descriptor = planner.methods.get("artifact", ref["artifact_id"])
    if {key: descriptor[key] for key in _REF_FIELDS} != ref or ref["size_bytes"] > 2 * MAX_RESPONSE_BYTES:
        raise ValueError("Discovery artifact binding differs from retained bytes")
    return _json(json.loads(path.read_bytes()), 2 * MAX_RESPONSE_BYTES)


def _sources(value):
    if type(value) is not list or len(value) > 10:
        raise ValueError("Discovery returns at most ten source observations")
    result = []
    for source in value:
        if type(source) is not dict or set(source) != _SOURCE_FIELDS:
            raise ValueError("Use exact discovery source observations")
        if (source["kind"] not in {"primary_paper", "official_documentation", "author_repository"}
                or source["access"] not in {"metadata", "snippet", "abstract"}):
            raise ValueError("Search observations cannot establish inspected full text")
        _text(source["title"], 4000)
        _text(source["url"], 8192)
        _check_url(source["url"], {_host(urlsplit(source["url"]).hostname)})
        if type(source["excerpt"]) is not str or len(source["excerpt"]) > 8000:
            raise ValueError("Discovery excerpt exceeds its budget")
        if type(source["identity"]) is not dict or not source["identity"]:
            raise ValueError("Discovery sources require explicit source identities")
        _json(source["identity"], 4000)
        result.append({**deepcopy(source), "full_text_inspected": False,
            "scope": "provider_observation_not_authenticated_or_scientifically_validated"})
    return result


def discover(planner, session_id, *, provider, request, actor, allow_network=False):
    """Perform one authorized search and explicit acquisitions, preserving failure.

    Search only consumes objective/data/evidence. Sources frozen into an earlier
    candidate decision cannot be silently supplemented: use a new planning cycle
    or an explicit separate reassessment operation retaining its old baseline.
    """
    actor = _label(actor)
    before = planner.get(session_id)
    if (before["scientific_candidates"] is not None or before["experiments"]
            or before["selection_in_progress"] is not None):
        raise ValueError("Late evidence requires a new planning cycle or explicit reassessment")
    request = _request(request, len(before["sources"]))
    supplied_only = request.get("mode") == "supplied_only"
    if not supplied_only and allow_network is not True:
        raise PermissionError("Discovery requires explicit network authorization")
    context = {"task": deepcopy(before["context"]["task"]),
        "dataset_summary": deepcopy(before["context"]["dataset_summary"]), "sources": deepcopy(before["sources"])}
    record = {"format": FORMAT, "session_id": session_id, "actor": actor, "created_at": _now(),
        "request": request, "context": context, "context_sha256": _digest(context),
        "before_planner_hash": _digest(before), "baseline_sources": deepcopy(before["sources"]),
        "candidate_frozen": False, "provider": None, "provider_response_ref": None,
        "sources": [], "acquired_sources": [], "failures": [], "consumption": {},
        "status": "blocked", "scope": "retained_search_observations_not_scientific_validation"}
    started = time.monotonic()
    if supplied_only:
        record.update(status="descriptive", scope="supplied_evidence_only_no_external_search_or_inferential_qualification",
                      after_planner_hash=_digest(before), consumption={"requests": 0})
        ref = _retain(planner, record, actor)
        return {"record_ref": ref, "before_planner_hash": record["before_planner_hash"],
                "after_planner_hash": record["after_planner_hash"], "status": record["status"]}
    operation = "search"
    try:
        if provider is None or not callable(getattr(provider, "search", None)) or not callable(getattr(provider, "configuration", None)):
            raise ValueError("Discovery provider capability is unavailable")
        record["provider"] = _json(provider.configuration(), 8000)
        response = provider.search(deepcopy(context), deepcopy(request), allow_network=True)
        # Retain raw provider response before interpreting any source claims.
        record["provider_response_ref"] = _retain(planner, response, actor)
        if type(response) is not dict or set(response) != {"raw_response", "candidates", "consumption"}:
            raise ValueError("Provider must retain raw response, candidates and consumption")
        usage = response["consumption"]
        if (type(usage) is not dict or len(usage) > 16 or any(type(key) is not str or len(key) > 80
                or type(value) not in (int, float) or not math.isfinite(value) or value < 0 for key, value in usage.items())):
            raise ValueError("Provider consumption requires bounded finite nonnegative counters")
        record["consumption"] = deepcopy(usage)
        record["sources"] = _sources(response["candidates"])
        if not record["sources"]:
            raise ValueError("Discovery returned no source evidence; unresolved sources remain blocking")
        if any(index >= len(record["sources"]) for index in request["acquire_indices"]):
            raise ValueError("Requested acquisition references nonexistent discovery evidence")
        if planner.get(session_id) != before:
            raise ValueError("Discovery baseline changed during provider dispatch")
        operation = "acquire"
        for index in request["acquire_indices"]:
            source = record["sources"][index]
            if source["kind"] == "author_repository":
                # Planner sources enter the scientific candidate context. Keep
                # implementation leads in the retained discovery packet for the
                # later capability-resolution stage, never in that context.
                record["failures"].append({"operation": "acquire", "source_index": index,
                    "kind": "ImplementationEvidenceBoundary", "message":
                    "Repository acquisition cannot enter scientific sources; retain this lead for capability resolution after candidate freezing"})
                continue
            try:
                evidence = planner.retrieve_evidence(session_id, source["url"], allowed_hosts=request["allowed_hosts"],
                    actor=actor, allow_network=True)
                record["acquired_sources"].append({"source_index": index, "evidence": evidence,
                    "access": "retrieved_text_unreviewed", "full_text_inspected": False})
            except Exception as error:
                record["failures"].append({"operation": "acquire", "source_index": index,
                    "kind": type(error).__name__, "message": str(error)[:1000]})
        if not record["failures"]:
            record["status"] = "completed"
    except Exception as error:
        if isinstance(error, DiscoveryProviderError):
            record["provider_response_ref"] = _retain(planner, error.response, actor)
            record["consumption"] = deepcopy(error.response["consumption"])
        record["failures"].append({"operation": operation, "kind": type(error).__name__, "message": str(error)[:1000]})
    record["consumption"]["coordinator_elapsed_seconds"] = time.monotonic() - started
    after = planner.get(session_id)
    record["after_planner_hash"] = _digest(after)
    expected = deepcopy(before)
    expected["sources"] += [item["evidence"] for item in record["acquired_sources"]]
    if after != expected:
        record["status"] = "blocked"
        record["failures"].append({"operation": "verify", "kind": "UnexpectedPlannerEffect",
            "message": "Discovery changed fields outside explicit source acquisitions"})
    ref = _retain(planner, record, actor)
    return {"record_ref": ref, "before_planner_hash": record["before_planner_hash"],
        "after_planner_hash": record["after_planner_hash"], "status": record["status"]}


def load_discovery(planner, envelope, *, session_id=None):
    """Verify the record and all source bytes; loading never repeats dispatch."""
    if type(envelope) is not dict or set(envelope) != {"record_ref", "before_planner_hash", "after_planner_hash", "status"}:
        raise ValueError("Use an exact discovery envelope")
    record = _read_ref(planner, envelope["record_ref"])
    if (record.get("format") != FORMAT or any(record.get(key) != envelope[key]
            for key in ("before_planner_hash", "after_planner_hash", "status"))
            or (session_id is not None and record["session_id"] != session_id)
            or _digest(record["context"]) != record["context_sha256"]):
        raise ValueError("Discovery record binding mismatch")
    if record["provider_response_ref"] is not None:
        _read_ref(planner, record["provider_response_ref"])
    before = planner.methods.get("method_plan_snapshot", record["before_planner_hash"])
    after = planner.methods.get("method_plan_snapshot", record["after_planner_hash"])
    if (_digest(before) != record["before_planner_hash"] or _digest(after) != record["after_planner_hash"]
            or before["session_id"] != record["session_id"] or after["session_id"] != record["session_id"]
            or record["baseline_sources"] != before["sources"]
            or record["context"] != {"task": before["context"]["task"],
                "dataset_summary": before["context"]["dataset_summary"], "sources": before["sources"]}):
        raise ValueError("Discovery baseline snapshot binding mismatch")
    for source in record["baseline_sources"] + [item["evidence"] for item in record["acquired_sources"]]:
        planner.methods.artifact_path(source["artifact_id"])
        descriptor = planner.methods.get("artifact", source["artifact_id"])
        if descriptor["sha256"] != source["content_sha256"]:
            raise ValueError("Discovery source content hash mismatch")
    expected = deepcopy(before)
    expected["sources"] += [item["evidence"] for item in record["acquired_sources"]]
    if record["status"] in {"completed", "descriptive"} and after != expected:
        raise ValueError("Discovery planner effect binding mismatch")
    return record


def decode_crossref_response(body, *, max_results=3):
    """Decode publisher-deposited metadata observations, never paper inspection."""
    if type(max_results) is not int or not 1 <= max_results <= 10:
        raise ValueError("Crossref result limit must be between one and ten")
    body = _json(body)
    if (body.get("status") != "ok" or body.get("message-type") != "work-list"
            or type(body.get("message")) is not dict or type(body["message"].get("items")) is not list
            or len(body["message"]["items"]) > max_results):
        raise ValueError("Unsupported bounded Crossref work-list response")
    sources = []
    for item in body["message"]["items"]:
        doi = _text(item.get("DOI"), 512)
        if re.fullmatch(r"10\.[0-9]{4,9}/[^\s]+", doi) is None:
            raise ValueError("Crossref source requires an exact DOI")
        titles = item.get("title")
        if type(titles) is not list or not titles:
            raise ValueError("Crossref source title is missing")
        title = _text(titles[0], 4000)
        abstract = item.get("abstract", "")
        if type(abstract) is not str:
            raise ValueError("Crossref abstract must be text")
        sources.append({"identity": {"doi": doi}, "title": title, "url": "https://doi.org/" + quote(doi, safe="/"),
            "kind": "primary_paper", "access": "abstract" if abstract else "metadata", "excerpt": abstract[:8000]})
    return sources


class CrossrefDiscoveryProvider:
    """One bounded Crossref works request using existing SSRF-safe retrieval.

    This capability searches publisher-deposited metadata, including abstracts
    when available; it cannot search official docs/repositories or prove that
    a returned publication is an appropriate primary method study.
    """
    def __init__(self, output_directory, *, max_results=3):
        self.output_directory = _path(Path(output_directory))
        if type(max_results) is not int or not 1 <= max_results <= 10:
            raise ValueError("Crossref result limit must be between one and ten")
        self.max_results = max_results

    def configuration(self):
        return {"provider": "crossref", "endpoint": "https://api.crossref.org/works", "max_results": self.max_results,
            "documentation": "https://www.crossref.org/documentation/retrieve-metadata/rest-api/",
            "qualification": "primary_documentation_and_offline_contract_only", "max_response_bytes": MAX_RESPONSE_BYTES}

    def search(self, context, request, *, allow_network=False):
        url = "https://api.crossref.org/works?" + urlencode({"query": _text(request["query"], 1000), "rows": self.max_results})
        body, response = _provider_response(self.output_directory, url, ["api.crossref.org"], allow_network)
        try:
            response["candidates"] = decode_crossref_response(json.loads(body), max_results=self.max_results)
        except (ValueError, TypeError, UnicodeError) as error:
            raise DiscoveryProviderError("Crossref returned uninterpretable metadata: " + str(error)[:300], response) from error
        return response


def _provider_response(directory, url, hosts, allow_network):
    """One existing bounded retrieval; retain wire bytes even on parse failure."""
    directory.mkdir(mode=0o700, exist_ok=True)
    target = _path(directory / (uuid.uuid4().hex + ".txt"))
    try:
        retrieval = retrieve_source(url, target, allowed_hosts=hosts, allow_network=allow_network,
            max_bytes=MAX_RESPONSE_BYTES)
    except Exception as error:
        response = {"raw_response": {"requested_url": url, "response_received": False}, "candidates": [],
            "consumption": {"retrieval_attempts": 1}}
        raise DiscoveryProviderError("Bounded provider retrieval failed: " + str(error)[:300], response) from error
    body = target.read_bytes()
    return body, {"raw_response": {"body_base64": base64.b64encode(body).decode("ascii"), "retrieval": retrieval},
        "candidates": [], "consumption": {"requests": 1, "response_bytes": len(body)}}


class GitHubRepositoryDiscoveryProvider:
    """Public metadata search limited to coordinator-declared author accounts.

    An allowlisted account is a declaration, not evidence of publication-author
    identity or algorithm correspondence. No clone, code inspection, credentials,
    source execution, trust promotion or numerical verification occurs.
    """
    def __init__(self, output_directory, *, owners, max_results=3):
        self.output_directory = _path(Path(output_directory))
        if (type(owners) is not list or not 1 <= len(owners) <= 3
                or any(type(owner) is not str or re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?", owner) is None
                       for owner in owners)):
            raise ValueError("GitHub search requires one to three exact declared author-owner accounts")
        if type(max_results) is not int or not 1 <= max_results <= 10:
            raise ValueError("GitHub result limit must be between one and ten")
        self.owners = sorted({owner.lower() for owner in owners})
        self.max_results = max_results

    def configuration(self):
        return {"provider": "github_repository", "endpoint": "https://api.github.com/search/repositories",
            "owners": self.owners, "max_results": self.max_results, "max_response_bytes": MAX_RESPONSE_BYTES,
            "documentation": "https://docs.github.com/en/rest/search/search#search-repositories",
            "scope": "public_repository_metadata_declared_owners_not_verified_author_correspondence",
            "qualification": "primary_documentation_and_offline_contract_only"}

    def search(self, context, request, *, allow_network=False):
        # Strip operators/qualifiers from the scientific query; owners are only
        # constructed from the separately validated coordinator configuration.
        terms = re.findall(r"[\w-]+", _text(request["query"], 1000), flags=re.UNICODE)
        keywords = " ".join('"' + term + '"' for term in terms)
        if not keywords or len(keywords) > 200:
            raise ValueError("GitHub discovery requires at most 200 query keyword characters")
        query = keywords + " " + " ".join("user:" + owner for owner in self.owners) + " fork:false archived:false"
        url = "https://api.github.com/search/repositories?" + urlencode({"q": query, "per_page": self.max_results, "page": 1})
        body, response = _provider_response(self.output_directory, url, ["api.github.com"], allow_network)
        try:
            payload = json.loads(body)
            if (type(payload) is not dict or payload.get("incomplete_results") is not False
                    or type(payload.get("items")) is not list or len(payload["items"]) > self.max_results):
                raise ValueError("GitHub response is incomplete or outside the result bound")
            for item in payload["items"]:
                owner = _text(item["owner"]["login"], 39)
                name = _text(item["name"], 100)
                identity = owner + "/" + name
                if (owner.lower() not in self.owners or item.get("private") is not False
                        or type(item.get("id")) is not int or item["id"] <= 0
                        or item.get("full_name") != identity or item.get("html_url") != "https://github.com/" + identity
                        or re.fullmatch(r"[A-Za-z0-9_.-]+", name) is None):
                    raise ValueError("GitHub repository differs from declared public owner scope")
                description = item.get("description") or ""
                if type(description) is not str:
                    raise ValueError("GitHub description must be metadata text")
                response["candidates"].append({"identity": {"github_repository_id": item["id"], "owner": owner,
                    "repository": name, "author_correspondence": "coordinator_declared_owner_not_author_verified"},
                    "title": identity, "url": item["html_url"], "kind": "author_repository", "access": "metadata",
                    "excerpt": description[:8000]})
        except (ValueError, TypeError, KeyError, UnicodeError) as error:
            raise DiscoveryProviderError("GitHub metadata interpretation failed: " + str(error)[:300], response) from error
        return response


class _DocumentationLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.href, self.label = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.label = []

    def handle_data(self, data):
        if self.href is not None and sum(map(len, self.label)) < 4000:
            self.label.append(data[:4000])

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            if len(self.links) >= 10_000:
                raise ValueError("Documentation index exceeds its link budget")
            self.links.append((self.href, " ".join(self.label)[:4000]))
            self.href, self.label = None, []


class OfficialDocumentationDiscoveryProvider:
    """Search one coordinator-declared HTML link index or plain-text URL list.

    Matching uses URL and link label metadata, never target-page content. The
    official-host designation is a coordinator declaration, not independently
    authenticated authority. There is no recursive crawling or XML parsing.
    """
    def __init__(self, output_directory, *, index_url, official_hosts, index_format="html", max_results=3):
        self.output_directory = _path(Path(output_directory))
        if type(index_format) is not str or index_format not in {"html", "text"}:
            raise ValueError("Documentation index format must be html or text; XML is unsupported")
        if type(official_hosts) is not list or not 1 <= len(official_hosts) <= 8:
            raise ValueError("Documentation requires bounded explicitly declared official hosts")
        self.official_hosts = sorted({_host(host) for host in official_hosts})
        _check_url(index_url, set(self.official_hosts))
        if type(max_results) is not int or not 1 <= max_results <= 10:
            raise ValueError("Documentation result limit must be between one and ten")
        self.index_url, self.index_format, self.max_results = index_url, index_format, max_results

    def configuration(self):
        return {"provider": "official_documentation", "index_url": self.index_url, "official_hosts": self.official_hosts,
            "index_format": self.index_format, "max_results": self.max_results, "max_response_bytes": MAX_RESPONSE_BYTES,
            "scope": "declared_official_index_url_and_label_metadata_only_not_fulltext_search",
            "qualification": "offline_contract_only_no_independent_official_host_authentication"}

    def search(self, context, request, *, allow_network=False):
        terms = re.findall(r"[\w]+", _text(request["query"], 1000).casefold())
        if not terms:
            raise ValueError("Documentation search requires query keywords")
        body, response = _provider_response(self.output_directory, self.index_url, self.official_hosts, allow_network)
        try:
            text = body.decode("utf-8")
            # XML sitemaps are unsupported, including entity/doctype-bearing XML.
            if re.search(r"<\?(?:xml)|<!ENTITY|<urlset\b|<sitemapindex\b", text, re.IGNORECASE):
                raise ValueError("XML sitemap content is unsupported; use an HTML index or plain URL list")
            if self.index_format == "html":
                parser = _DocumentationLinks()
                parser.feed(text)
                parser.close()
                links = parser.links
            else:
                links = [(line.strip(), "") for line in text.splitlines() if line.strip()]
                if len(links) > 10_000:
                    raise ValueError("Documentation index exceeds its URL budget")
            seen = set()
            for href, label in links:
                url = urljoin(self.index_url, href)
                try:
                    _check_url(url, set(self.official_hosts))
                except (ValueError, PermissionError):
                    continue
                metadata = unquote(url) + " " + label
                if url in seen or not all(term in metadata.casefold() for term in terms):
                    continue
                seen.add(url)
                response["candidates"].append({"identity": {"index_url": self.index_url, "indexed_url": url,
                    "official_status": "coordinator_declared_not_independently_authenticated"},
                    "title": label.strip() or url, "url": url, "kind": "official_documentation", "access": "metadata",
                    "excerpt": "URL/link-label metadata matched within the declared index; target content was not inspected."})
                if len(response["candidates"]) == self.max_results:
                    break
        except (ValueError, TypeError, UnicodeError) as error:
            raise DiscoveryProviderError("Documentation index interpretation failed: " + str(error)[:300], response) from error
        return response
