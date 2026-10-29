"""Exact-ID source adapters and immutable collections of existing /1 evidence.

Routes are pinned to reviewed primary documentation, not qualified by a live
article campaign. Provider observations, rights decisions and reviewer identity
remain declarations. No lookup, acquisition, parsing or model call runs on import.
"""
from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
import hashlib
import re
import stat
from types import MappingProxyType
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .episode_proposals import _absolute, _load, _seal, require_disjoint_output
from . import rna_publication_evidence as legacy
from .rna_publication_evidence import _canonical_identifier, _copy, _identifier, _identity, _keys, _list, _locator, _sha, _text, _url
from devbench.study.evidence import _read, decode_json


SOURCE_ASSET_FORMAT = "devbench.rna-source-asset/2"
COLLECTION_FORMAT = "devbench.rna-publication-collection/2"
COLLECTION_SCOPE = "retained_source_custody_not_authenticity_semantic_support_or_scientific_validation"
MAX_REFERENCE_SNAPSHOT_BYTES = 20_000_000
_GROUNDING_FRAME_TOKEN = object()
_IDS = {"doi", "pmid", "pmcid"}
_ID_DOCUMENTATION = "https://pmc.ncbi.nlm.nih.gov/tools/id-converter-api/"
_CLOUD_DOCUMENTATION = "https://pmc-oa-opendata.s3.amazonaws.com/README.txt"
_CLOUD = "https://pmc-oa-opendata.s3.amazonaws.com/"
_CONTRACTS = {
    "crossref": {"enabled": True, "documentation": legacy.CROSSREF_DOCUMENTATION,
                 "schema": "singleton-work/1.0.0", "access": "metadata"},
    "pmc_id_converter": {"enabled": True, "documentation": _ID_DOCUMENTATION,
                         "schema": "idconv-api/v1/json-singleton-versions-no", "access": "metadata"},
    "pmc_cloud": {"enabled": True, "documentation": _CLOUD_DOCUMENTATION,
                  "schema": "article-version-single-object/xml-or-json", "access": "metadata_or_unreviewed_article_xml"},
    "pmc_oa_legacy": {"enabled": False, "documentation": "https://pmc.ncbi.nlm.nih.gov/tools/oa-service/",
                      "reason": "retired_2026-08-25"},
    "europe_pmc": {"enabled": False, "documentation": "https://europepmc.org/RestfulWebService",
                   "reason": "2026-10-11_documentation_403_exact_routes_unverified"},
    "geo": {"enabled": False, "documentation": "https://www.ncbi.nlm.nih.gov/geo/info/geo_paccess.html",
            "reason": "2026-10-11_documentation_browser_challenge_exact_routes_unverified"},
}
_ASSET_FIELDS = {"format", "asset_id", "publication_id", "source", "requested_identity", "resolved_identity",
    "identity_match_basis", "identity_reviewed_by",
    "identity_locators",
    "conflicts", "provider", "source_url", "retrieved_at", "content_type", "asset_kind", "availability",
    "origin_access", "version_type", "article_instance_id", "provider_checksums", "provider_etag",
    "provider_modified_at", "rights", "status", "lineage", "limitations"}


def provider_contracts():
    """Fresh documentation review snapshots; no claim of live API qualification."""
    return _copy({key: {**value, "reviewed_at": "2026-10-11",
                       "qualification": "primary_documentation_and_offline_contract_only"}
                  for key, value in _CONTRACTS.items()})


def exact_provider_url(provider, identifier, *, id_type=None, version=None, asset_kind=None):
    if type(provider) is not str or (id_type is not None and type(id_type) is not str):
        raise ValueError("Use an exact declared provider and identifier type")
    contract = _CONTRACTS.get(provider)
    if contract is None or not contract["enabled"]:
        raise ValueError("Unknown or disabled publication provider capability")
    if provider == "crossref":
        if id_type not in (None, "doi") or version is not None or asset_kind not in (None, "metadata"):
            raise ValueError("Crossref adapter supports exact DOI metadata only")
        return legacy.crossref_doi_metadata_url(identifier)
    if provider == "pmc_id_converter":
        if id_type not in _IDS or version is not None or asset_kind not in (None, "metadata"):
            raise ValueError("PMC ID mapping requires one exact DOI, PMID or PMCID")
        normalized = _canonical_identifier(id_type, identifier)
        if normalized is None:
            raise ValueError("PMC ID mapping requires an explicit identifier")
        return ("https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/?idtype=" + id_type +
                "&ids=" + quote(normalized, safe="") + "&format=json&versions=no&showaiid=no")
    pmcid = _canonical_identifier("pmcid", identifier)
    if (pmcid is None or id_type not in (None, "pmcid") or type(version) is not int or
            not 1 <= version <= 9999 or asset_kind not in (None, "article_xml", "metadata")):
        raise ValueError("PMC cloud capability requires exact PMCID/version and XML or metadata")
    prefix = pmcid + "." + str(version)
    return _CLOUD + prefix + "/" + prefix + (".json" if asset_kind == "metadata" else ".xml")


def discover_publication_candidates(requested_identity, candidates):
    """Review an already supplied bounded discovery response without resolving it.

    Title similarity never establishes identity. Exact-ID conflicts are retained;
    discovery and identifier observations never become a full-text admission.
    """
    requested = _identity(requested_identity)
    retained, conflicts = [], []
    for index, candidate in enumerate(_list(candidates, 32)):
        _keys(candidate, {"identity", "source_url", "access"})
        observed = _identity(candidate["identity"])
        _url(candidate["source_url"])
        if candidate["access"] not in {"metadata", "abstract"}:
            raise ValueError("Discovery only returns unresolved metadata/abstract candidates")
        retained.append(_copy(candidate))
        if any(requested[field] is not None and requested[field] == observed[field] for field in _IDS):
            fields = sorted(field for field in requested if requested[field] != observed[field])
            if fields:
                conflicts.append({"candidate_index": index, "fields": fields})
    return {"format": "devbench.rna-publication-discovery/2", "requested_identity": requested,
        "candidates": retained, "conflicts": conflicts, "resolution_status": "unresolved",
        "full_text_acquired": False, "scope": "discovery_observations_require_independent_exact_resolution"}


def _observed_ids(value):
    result = {}
    for field in sorted(_IDS):
        raw = value.get(field)
        if field == "pmid" and type(raw) is int:
            raw = str(raw)
        result[field] = _canonical_identifier(field, raw)
    return result


def decode_pmc_id_mapping(body, *, requested_id, id_type, expected_identifiers=None):
    """One PMC-covered identifier mapping; title/version/text remain unknown."""
    if type(body) is not bytes or len(body) > 1_000_000 or id_type not in _IDS:
        raise ValueError("Use bounded singleton PMC ID metadata bytes")
    requested = _canonical_identifier(id_type, requested_id)
    if requested is None:
        raise ValueError("Mapping requires an exact requested identifier")
    value = decode_json(body)
    _keys(value, {"status", "response-date", "request", "records"})
    if value["status"] != "ok":
        raise ValueError("Unsupported PMC ID provider status")
    _text(value["response-date"], 100)
    request = value["request"]
    fields = {"format", "ids", "idtype", "versions", "showaiid", "warnings", "email", "tool", "echo"}
    if (type(request) is not dict or set(request) - fields or
            not {"format", "ids", "idtype", "versions", "showaiid"} <= set(request) or
            request["format"] != "json" or request["idtype"] != id_type or
            request["versions"] != "no" or request["showaiid"] != "no" or
            type(request["ids"]) is not list or len(request["ids"]) != 1 or
            _canonical_identifier(id_type, request["ids"][0]) != requested):
        raise ValueError("PMC mapping response conflicts with the singleton request contract")
    records = _list(value["records"], 1)
    resolved = {field: None for field in sorted(_IDS)}
    if records:
        record = records[0]
        fields = {"requested-id", "doi", "pmid", "pmcid", "live", "release-date", "aiid"}
        if type(record) is not dict or "requested-id" not in record or set(record) - fields:
            raise ValueError("Unsupported PMC mapping record schema")
        if _canonical_identifier(id_type, record["requested-id"]) != requested:
            raise ValueError("PMC returned a different requested identifier")
        resolved = _observed_ids(record)
        if resolved[id_type] != requested:
            raise ValueError("PMC returned conflicting publication identifiers")
    if expected_identifiers is not None:
        _keys(expected_identifiers, _IDS)
        expected = _observed_ids(expected_identifiers)
        if any(expected[field] is not None and expected[field] != resolved[field] for field in _IDS):
            raise ValueError("PMC mapping conflicts with supplied publication identifiers")
    return {"format": "devbench.pmc-id-metadata/1", "requested_id": requested, "id_type": id_type,
        "resolved_identifiers": resolved, "title": None, "publication_version": None,
        "source_sha256": hashlib.sha256(body).hexdigest(), "provider_schema": _CONTRACTS["pmc_id_converter"]["schema"],
        "documentation": _ID_DOCUMENTATION, "coverage": "PMC_repository_only",
        "mapping_status": "observed" if records else "no_mapping_returned",
        "resolution_status": "unresolved", "access": "metadata", "full_text_acquired": False}


def decode_pmc_cloud_metadata(body, *, expected_pmcid, expected_version, expected_title=None):
    """Retain metadata flags/checksum links; never infer a version of record."""
    exact_provider_url("pmc_cloud", expected_pmcid, version=expected_version, asset_kind="metadata")
    if type(body) is not bytes or len(body) > 1_000_000:
        raise ValueError("Use bounded PMC cloud metadata bytes")
    value = decode_json(body)
    fields = {"pmcid", "version", "pmid", "doi", "title", "citation", "is_pmc_openaccess", "is_manuscript",
              "is_historical_ocr", "is_retracted", "license_code", "xml_url", "pdf_url", "media_urls", "text_url"}
    _keys(value, fields)
    identifiers = _observed_ids(value)
    if (identifiers["pmcid"] != _canonical_identifier("pmcid", expected_pmcid) or
            type(value["version"]) is not int or value["version"] != expected_version):
        raise ValueError("PMC cloud identifier/version conflict")
    title = _text(value["title"], 4000)
    if expected_title is not None and title != _text(expected_title, 4000):
        raise ValueError("PMC cloud title conflicts with the requested publication")
    _text(value["citation"])
    for field in ("is_pmc_openaccess", "is_manuscript", "is_historical_ocr", "is_retracted"):
        if value[field] not in {"yes", "no"}:
            raise ValueError("Unsupported PMC cloud flag schema")
    _text(value["license_code"], 100)
    links = []
    for field in ("xml_url", "pdf_url", "text_url"):
        if value[field] is not None:
            links.append({"kind": field, "url": value[field]})
    for url in _list(value["media_urls"], 128):
        links.append({"kind": "media_url", "url": url})
    prefix = identifiers["pmcid"] + "." + str(expected_version)
    for link in links:
        parsed = urlsplit(_url(link["url"]))
        path = unquote(parsed.path)
        if (parsed.hostname != "pmc-oa-opendata.s3.amazonaws.com" or not path.startswith("/" + prefix + "/")
                or any(part in {".", "..", ""} for part in path[1:].split("/"))):
            raise ValueError("PMC metadata link conflicts with the requested article version")
        query = parse_qs(parsed.query, strict_parsing=True)
        if set(query) != {"md5"} or len(query["md5"]) != 1 or re.fullmatch(r"[a-fA-F0-9]{32}", query["md5"][0]) is None:
            raise ValueError("PMC metadata URL lacks its documented MD5 parameter")
        link["md5"] = query["md5"][0].lower()
    kind = ("author_manuscript" if value["is_manuscript"] == "yes" else
            "historical_ocr" if value["is_historical_ocr"] == "yes" else "unknown_non_manuscript")
    return {"format": "devbench.pmc-cloud-metadata/1", "resolved_identifiers": identifiers,
        "title": title, "publication_version": expected_version, "version_type": kind,
        "source_sha256": hashlib.sha256(body).hexdigest(), "documentation": _CLOUD_DOCUMENTATION,
        "license_code": value["license_code"], "rights_decision": "requires_independent_review",
        "reported_retracted": value["is_retracted"] == "yes", "notice_coverage": "provider_metadata_flag_only",
        "observed_links": links, "links_are_retrieval_authority": False, "full_text_acquired": False,
        "resolution_status": "unresolved", "access": "metadata"}


def retrieve_exact_provider_source(provider, identifier, output, *, approved_url, id_type=None, version=None,
                                   asset_kind=None, expected_title=None, allow_network=False, max_bytes=1_000_000):
    """Opt-in singleton acquisition through the existing pinned public transport."""
    if allow_network is not True:
        raise PermissionError("Provider acquisition requires explicit exact-URL network authority")
    url = exact_provider_url(provider, identifier, id_type=id_type, version=version, asset_kind=asset_kind)
    if approved_url != url:
        raise PermissionError("Approval must bind the exact current provider object URL")
    xml = provider == "pmc_cloud" and asset_kind != "metadata"
    cap = 2_000_000 if xml else 1_000_000
    if type(max_bytes) is not int or not 1 <= max_bytes <= cap:
        raise ValueError("Provider response byte cap exceeds the selected decoder capability")
    types = {"application/xml", "text/xml", "application/jats+xml"} if xml else {"application/json"}
    if provider == "crossref":
        types.add("application/vnd.crossref-api-message+json")
    retrieval = legacy._retrieve_approved_text(url, [approved_url], output, allow_network=True,
                                              max_bytes=max_bytes, content_types=types)
    body = _read(_absolute(retrieval["path"]), max_bytes)
    if hashlib.sha256(body).hexdigest() != retrieval["content_sha256"]:
        raise ValueError("Provider source changed after retrieval")
    if xml:
        from .rna_reference_extraction import extract_reference_asset
        extraction = extract_reference_asset(body, content_type=retrieval["content_type"],
            source_sha256=retrieval["content_sha256"])
        identifiers = []
        observations = extraction["identity_observations"]
        for item in observations["article_ids"]:
            if item["pub_id_type"] in {"pmc", "pmcid"}:
                observed = item["text"]
                if re.fullmatch(r"[1-9][0-9]{0,11}", observed):
                    observed = "PMC" + observed
                identifiers.append(_canonical_identifier("pmcid", observed))
        if identifiers != [_canonical_identifier("pmcid", identifier)]:
            raise ValueError("PMC XML returned a conflicting or absent requested publication identifier")
        titles = observations["article_titles"]
        if expected_title is not None and (len(titles) != 1 or titles[0]["text"] != expected_title):
            raise ValueError("PMC XML returned a conflicting or ambiguous publication title")
        return {"retrieval": retrieval, "extraction": extraction,
                "identity_validation": "independent_curation_required", "access": "article_xml_unreviewed"}
    if provider == "crossref":
        metadata = legacy.decode_crossref_doi_metadata(body, expected_doi=identifier, expected_title=expected_title)
    elif provider == "pmc_id_converter":
        metadata = decode_pmc_id_mapping(body, requested_id=identifier, id_type=id_type)
    else:
        metadata = decode_pmc_cloud_metadata(body, expected_pmcid=identifier, expected_version=version, expected_title=expected_title)
    return {"retrieval": retrieval, "metadata": metadata}


def _validate_jats_identity(extraction, identity):
    expected = _identity(identity)
    matched = set()
    observed_fields = set()
    types = {"pmc": "pmcid", "pmcid": "pmcid", "doi": "doi", "pmid": "pmid"}
    for item in extraction["identity_observations"]["article_ids"]:
        field = types.get(item["pub_id_type"])
        if field is None:
            continue
        observed = item["text"]
        if field == "pmcid" and re.fullmatch(r"[1-9][0-9]{0,11}", observed):
            observed = "PMC" + observed
        observed = _canonical_identifier(field, observed)
        if field in observed_fields or expected[field] is None or observed != expected[field]:
            raise ValueError("JATS source contains a conflicting publication identifier")
        observed_fields.add(field)
        matched.add(field)
    titles = extraction["identity_observations"]["article_titles"]
    if not matched or len(titles) != 1 or titles[0]["text"] != expected["title"]:
        raise ValueError("JATS source identifier/title does not match its frozen declared identity")


def _source_ref(record, workspace):
    _keys(record, {"path", "sha256", "size_bytes"})
    path = _absolute(record["path"])
    if workspace is not None and not path.is_relative_to(workspace):
        raise ValueError("Source lies outside the approved workspace")
    if type(record["size_bytes"]) is not int or not 0 <= record["size_bytes"] <= legacy.MAX_SOURCE_BYTES:
        raise ValueError("Source byte bound exceeded")
    body = _read(path, record["size_bytes"])
    if len(body) != record["size_bytes"] or hashlib.sha256(body).hexdigest() != _sha(record["sha256"]):
        raise ValueError("Source size or hash mismatch")
    return body


def _time(value):
    _text(value, 100)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("Use an explicit timezone-aware observation time") from error
    if parsed.tzinfo is None:
        raise ValueError("Use an explicit timezone-aware observation time")


def validate_source_asset(record, *, publication, workspace=None, allow_evidence_read=False, parent_assets=None, _ancestry=()):
    """Validate immutable source declarations and raw/derived lineage separately."""
    if allow_evidence_read is not True:
        raise PermissionError("Source custody reading requires explicit authority")
    root = _absolute(workspace) if workspace is not None else None
    record = _copy(record)
    _keys(record, _ASSET_FIELDS)
    if record["format"] != SOURCE_ASSET_FORMAT:
        raise ValueError("Unsupported source asset format")
    for field in ("asset_kind", "origin_access", "availability", "version_type"):
        _text(record[field], 100)
    if record["availability"] in {"restricted", "absent"} and record["source"] is not None:
        raise ValueError("Unavailable assets cannot fabricate retained source bytes")
    for field in ("asset_id", "publication_id"):
        _identifier(record[field])
    # Independent use of this validator has the same quota as a collection;
    # caller-supplied parent declarations do not enlarge the source read grant.
    quota_refs = _asset_source_refs(record)
    if parent_assets is not None:
        parent_assets = _list(_copy(parent_assets), 64)
        for parent in parent_assets:
            quota_refs.extend(_asset_source_refs(parent))
    quota_sizes = {}
    for ref in quota_refs:
        _keys(ref, {"path", "sha256", "size_bytes"})
        path = _absolute(ref["path"])
        if root is not None and not path.is_relative_to(root):
            raise ValueError("Parent/source lies outside the approved workspace")
        info = path.lstat()
        if (type(ref["size_bytes"]) is not int or not 0 <= ref["size_bytes"] <= legacy.MAX_SOURCE_BYTES or
                not stat.S_ISREG(info.st_mode) or info.st_size != ref["size_bytes"]):
            raise ValueError("Parent/source size is not bounded before aggregate budget admission")
        quota_sizes[(str(path), _sha(ref["sha256"]))] = info.st_size
        if sum(quota_sizes.values()) > legacy.MAX_AGGREGATE_BYTES:
            raise ValueError("Source asset aggregate budget exceeded before body loading")
    if record["asset_id"] in _ancestry or len(_ancestry) >= 16:
        raise ValueError("Cyclic or oversized source parent lineage")
    # The collection may retain a newly reviewed source URL around an existing
    # frozen /1 manifest. An observed asset URL grants no retrieval authority.
    # Exact all-field identity validation reuses /1 without editing its URL list.
    _url(record["source_url"])
    expected = {"identity": publication["identity"], "approved_urls": [record["source_url"]]}
    for identity in (record["requested_identity"], record["resolved_identity"]):
        legacy.validate_publication_identity(expected, {"identity": identity, "source_url": record["source_url"]})
    if record["identity_match_basis"] != "exact_declared_identifiers_title_version":
        raise ValueError("Declare the exact source identity match basis")
    _identifier(record["identity_reviewed_by"])
    if _list(record["conflicts"], 32):
        raise ValueError("Conflicting source identities require unresolved discovery custody")
    _text(record["provider"], 100)
    _text(record["content_type"], 200)
    _text(record["limitations"])
    if record["retrieved_at"] is not None:
        _time(record["retrieved_at"])
    kind, access = record["asset_kind"], record["origin_access"]
    allowed = {"metadata": "metadata", "abstract": "abstract", "article_xml": "full_text",
               "article_text": "full_text", "supplement": "supplement"}
    if kind == "extracted_json":
        if type(record["lineage"]) is not dict or access != record["lineage"].get("parent_access"):
            raise ValueError("Derived source access cannot exceed original access")
    elif kind not in allowed or allowed[kind] != access or record["lineage"] is not None:
        raise ValueError("Source kind/access conflicts with the acquired representation")
    if access not in {"metadata", "abstract", "full_text", "supplement"}:
        raise ValueError("Declare actual original source access")
    media = {"metadata": {"application/json", "application/vnd.crossref-api-message+json"},
        "abstract": {"text/plain", "text/html"}, "article_xml": {"application/xml", "text/xml", "application/jats+xml"},
        "article_text": {"text/plain", "text/html"}, "extracted_json": {"application/json"}}
    if kind in media and record["content_type"] not in media[kind]:
        raise ValueError("Source MIME/content type cannot promote metadata or abstract access")
    if record["availability"] not in {"available", "restricted", "absent"}:
        raise ValueError("Declare source/supplement availability")
    body = None
    if record["availability"] == "available":
        if record["source"] is None or record["retrieved_at"] is None:
            raise ValueError("Available source requires retained bytes and retrieval time")
        body = _source_ref(record["source"], root)
        if kind == "article_xml":
            from .rna_reference_extraction import extract_reference_asset
            parsed = extract_reference_asset(body, content_type=record["content_type"], source_sha256=record["source"]["sha256"])
            if not parsed["coverage"]["article_body_nonempty"]:
                raise ValueError("Abstract/metadata-only JATS lacks a body for full_text admission")
            _validate_jats_identity(parsed, record["resolved_identity"])
        elif kind == "article_text" and body.removeprefix(b"\xef\xbb\xbf").lstrip().startswith((b"{", b"[", b"<article", b"<?xml")):
            raise ValueError("Structured metadata/XML cannot become full text through a text MIME declaration")
    elif record["source"] is not None:
        raise ValueError("Unavailable assets cannot fabricate retained source bytes")
    locators = record["identity_locators"]
    if body is not None and kind == "article_text" and locators is None:
        raise ValueError("Raw article text requires reviewed exact identity byte locators")
    if locators is not None:
        if body is None:
            raise ValueError("Identity locators lack retained source bytes")
        identity = _identity(record["resolved_identity"])
        _keys(locators, {field for field, value in identity.items() if value is not None})
        for field, locator in locators.items():
            _locator(locator, body)
            if _canonical_identifier(field, locator["text"]) != identity[field]:
                raise ValueError("Source identity locator conflicts with the frozen publication")
    if record["version_type"] not in {"unknown", "author_manuscript", "published_article", "version_of_record", "preprint", "historical_ocr"}:
        raise ValueError("Declare a reviewed version type, never infer from deposit order")
    for field in ("article_instance_id", "provider_etag", "provider_modified_at"):
        if record[field] is not None:
            _text(record[field], 500)
    for checksum in _list(record["provider_checksums"], 8):
        _keys(checksum, {"algorithm", "value"})
        algorithm = checksum["algorithm"]
        if algorithm not in {"md5", "sha256"} or type(checksum["value"]) is not str or re.fullmatch(
                r"[a-f0-9]{" + ("32" if algorithm == "md5" else "64") + r"}", checksum["value"]) is None:
            raise ValueError("Use a typed provider checksum observation")
        if body is not None and hashlib.new(algorithm, body).hexdigest() != checksum["value"]:
            raise ValueError("Provider checksum differs from retained bytes")
    rights = record["rights"]
    _keys(rights, {"license_text", "license_locator", "intended_use", "decision", "reviewed_by"})
    if rights["license_text"] is not None:
        _text(rights["license_text"])
    _text(rights["intended_use"])
    _identifier(rights["reviewed_by"])
    if rights["decision"] not in {"permitted", "restricted", "unknown"}:
        raise ValueError("Declare scoped rights review")
    if record["availability"] == "restricted" and rights["decision"] != "restricted":
        raise ValueError("Restricted assets cannot declare permitted access")
    if rights["license_locator"] is not None:
        if body is None:
            raise ValueError("License locator lacks retained source bytes")
        _locator(rights["license_locator"], body)
    status = record["status"]
    _keys(status, {"state", "checked_at", "checked_providers", "coverage", "notices", "reviewed_by"})
    _text(status["coverage"])
    _identifier(status["reviewed_by"])
    providers = _list(status["checked_providers"], 16)
    for provider in providers:
        _text(provider, 100)
    if len(set(providers)) != len(providers) or status["state"] not in {"unknown_not_assessed", "none_found_in_checked_sources", "notices_require_review"}:
        raise ValueError("Declare bounded scoped publication status checks")
    if status["checked_at"] is not None:
        _time(status["checked_at"])
    if status["state"] != "unknown_not_assessed" and (not providers or status["checked_at"] is None):
        raise ValueError("Publication notice assessment requires an actual scoped check declaration")
    notices = _list(status["notices"], 32)
    if (status["state"] == "none_found_in_checked_sources" and notices) or (status["state"] == "notices_require_review" and not notices):
        raise ValueError("Status claim conflicts with retained notice evidence")
    for notice in notices:
        _keys(notice, {"kind", "source_url", "source_ref", "locator"})
        if notice["kind"] not in {"correction", "retraction", "expression_of_concern", "other"}:
            raise ValueError("Declare notice type")
        _url(notice["source_url"])
        _locator(notice["locator"], _source_ref(notice["source_ref"], root))
    if record["lineage"] is not None:
        lineage = record["lineage"]
        _keys(lineage, {"parent_source_ref", "parent_access", "extractor", "extractor_version", "reviewed_by"})
        _source_ref(lineage["parent_source_ref"], root)
        if record["source"] is None or lineage["parent_source_ref"]["path"] == record["source"]["path"]:
            raise ValueError("Derived extraction must retain a distinct original source")
        for field in ("extractor", "extractor_version"):
            _text(lineage[field], 200)
        _identifier(lineage["reviewed_by"])
        if parent_assets is None:
            raise ValueError("Derived access requires an actually admitted parent asset")
        parents = _list(parent_assets, 64)
        matches = [parent for parent in parents if type(parent) is dict and
                   parent.get("source") == lineage["parent_source_ref"] and
                   parent.get("publication_id") == record["publication_id"]]
        if len(matches) != 1:
            raise ValueError("Derived access requires one unambiguous admitted parent source")
        parent = validate_source_asset(matches[0], publication=publication, workspace=root,
            allow_evidence_read=True, parent_assets=parents, _ancestry=(*_ancestry, record["asset_id"]))
        if (parent["origin_access"] != lineage["parent_access"] or parent["origin_access"] != access or
                parent["identity_reviewed_by"] != record["identity_reviewed_by"]):
            raise ValueError("Derived parent access/curation conflicts with the actually admitted parent")
    return record


def _asset_source_refs(asset):
    _keys(asset, _ASSET_FIELDS)
    refs = [asset["source"]] if asset["source"] is not None else []
    if asset["lineage"] is not None:
        _keys(asset["lineage"], {"parent_source_ref", "parent_access", "extractor", "extractor_version", "reviewed_by"})
        refs.append(asset["lineage"]["parent_source_ref"])
    status = asset["status"]
    _keys(status, {"state", "checked_at", "checked_providers", "coverage", "notices", "reviewed_by"})
    for notice in _list(status["notices"], 32):
        _keys(notice, {"kind", "source_url", "source_ref", "locator"})
        refs.append(notice["source_ref"])
    return refs


def _publication_identifier_in_text(identity, text):
    # A DOI suffix may contain punctuation, including hyphens. Match its whole
    # lexeme rather than accepting the supplied DOI as a prefix of another one.
    dois = re.findall(r"(?i)(?:https://doi\.org/|doi:)?10\.[0-9]{4,9}/[^\s<>\"']+", text)
    if identity["doi"] is not None and any(_canonical_identifier("doi", token) == _canonical_identifier("doi", identity["doi"]) for token in dois):
        return True
    pmcids = re.findall(r"(?<![A-Za-z0-9])PMC[1-9][0-9]{0,11}(?![A-Za-z0-9.])", text, re.I)
    if identity["pmcid"] is not None and any(_canonical_identifier("pmcid", token) == _canonical_identifier("pmcid", identity["pmcid"]) for token in pmcids):
        return True
    pmids = re.findall(r"(?i)(?:\bPMID[: ]+|https://pubmed\.ncbi\.nlm\.nih\.gov/)([1-9][0-9]{0,11})(?![A-Za-z0-9])", text)
    return identity["pmid"] is not None and identity["pmid"] in pmids


def validate_publication_collection(record, *, workspace=None, allow_evidence_read=False):
    """Load unchanged /1 manifests and bind related assets and direct data links."""
    if allow_evidence_read is not True:
        raise PermissionError("Publication collection reading requires explicit authority")
    root = _absolute(workspace) if workspace is not None else None
    record = _copy(record)
    _keys(record, {"format", "collection_id", "curator_id", "participant_id", "publications", "assets", "dataset_links", "scope"})
    if record["format"] != COLLECTION_FORMAT or record["scope"] != COLLECTION_SCOPE:
        raise ValueError("Unsupported publication collection format/scope")
    _identifier(record["collection_id"])
    curator, participant = _identifier(record["curator_id"]), _identifier(record["participant_id"])
    if curator == participant:
        raise ValueError("Collection requires independent declared curation")
    publications, source_bodies, source_sizes, cached_bodies = {}, {}, {}, {}
    def reserve(path, sha, declared_size=None):
        path = _absolute(path)
        if root is not None and not path.is_relative_to(root):
            raise ValueError("Source lies outside the approved workspace")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > legacy.MAX_SOURCE_BYTES:
            raise ValueError("Source is not a bounded regular file")
        if declared_size is not None and (type(declared_size) is not int or declared_size != info.st_size):
            raise ValueError("Source byte size changed before aggregate budget admission")
        key = (str(path), _sha(sha))
        source_sizes[key] = info.st_size
        if sum(source_sizes.values()) > legacy.MAX_AGGREGATE_BYTES:
            raise ValueError("Publication collection aggregate source byte budget exceeded before body loading")
        return info.st_size
    def reserve_ref(ref):
        _keys(ref, {"path", "sha256", "size_bytes"})
        return reserve(ref["path"], ref["sha256"], ref["size_bytes"])
    pending = []
    for publication in _list(record["publications"], 8, nonempty=True):
        _keys(publication, {"publication_id", "reference"})
        key = _identifier(publication["publication_id"])
        if key in publications:
            raise ValueError("Duplicate collection publication")
        reference = publication["reference"]
        _keys(reference, {"manifest_path", "root_hash"})
        path = _absolute(reference["manifest_path"])
        if root is not None and not path.is_relative_to(root):
            raise ValueError("Publication manifest lies outside the approved workspace")
        _, loaded, _ = _load(path, _sha(reference["root_hash"]), "publication-evidence.json")
        _keys(loaded, {"format", "publication", "sources", "excerpts", "exposure", "curator_id", "participant_id", "scope"})
        if loaded["curator_id"] != curator or loaded["participant_id"] != participant:
            raise ValueError("Collection custody conflicts with its unchanged publication manifests")
        publications[key] = loaded
        quota = 0
        for source in _list(loaded["sources"], 16, nonempty=True):
            if type(source) is not dict or not {"path", "sha256"} <= set(source):
                raise ValueError("Malformed original publication source record")
            quota += reserve(source["path"], source["sha256"])
        pending.append((key, loaded, quota))
    assets = _list(record["assets"], 64)
    for asset in assets:
        for ref in _asset_source_refs(asset):
            reserve_ref(ref)
    links = _list(record["dataset_links"], 64)
    for link in links:
        if type(link) is not dict or type(link.get("repository_evidence")) is not dict or "source" not in link["repository_evidence"]:
            raise ValueError("Malformed repository evidence record")
        reserve_ref(link["repository_evidence"]["source"])
    # The complete unique retained-source quota is admitted before body reads.
    for key, loaded, quota in pending:
        loaded = legacy.validate_publication_evidence(loaded, workspace=root, allow_evidence_read=True,
            max_aggregate_bytes=min(legacy.MAX_AGGREGATE_BYTES, max(1, quota)))
        publications[key] = loaded
        source_bodies[key] = {}
        for source in loaded["sources"]:
            cache_key = (source["path"], source["sha256"])
            if cache_key not in cached_bodies:
                cached_bodies[cache_key] = _read(_absolute(source["path"]), source_sizes[cache_key])
            body = cached_bodies[cache_key]
            if hashlib.sha256(body).hexdigest() != source["sha256"]:
                raise ValueError("Publication source changed after manifest review")
            source_bodies[key][source["source_id"]] = body
    seen_assets = set()
    for asset in assets:
        if type(asset) is not dict or asset.get("publication_id") not in publications:
            raise ValueError("Source asset references an unknown publication")
        validated = validate_source_asset(asset, publication=publications[asset["publication_id"]]["publication"],
                                          workspace=root, allow_evidence_read=True, parent_assets=assets)
        if validated["asset_id"] in seen_assets:
            raise ValueError("Duplicate source asset")
        seen_assets.add(validated["asset_id"])
        if any(validated[field]["reviewed_by"] != curator for field in ("rights", "status")) or (
                validated["identity_reviewed_by"] != curator or
                validated["lineage"] is not None and validated["lineage"]["reviewed_by"] != curator):
            raise ValueError("Asset review must identify the collection curator")
        refs = ([validated["source"]] if validated["source"] is not None else [])
        refs += ([validated["lineage"]["parent_source_ref"]] if validated["lineage"] is not None else [])
        refs += [notice["source_ref"] for notice in validated["status"]["notices"]]
        for ref in refs:
            source_sizes[(ref["path"], ref["sha256"])] = ref["size_bytes"]
    for link in links:
        _keys(link, {"publication_id", "accession", "relationship", "article_evidence", "repository_evidence",
                    "reviewed_by", "sample_scope", "limitations"})
        if link["publication_id"] not in publications or link["reviewed_by"] != curator or link["relationship"] != "direct_study_data":
            raise ValueError("Only independently reviewed direct article-study links are eligible")
        accession = link["accession"]
        if type(accession) is not str or re.fullmatch(r"(?:GSE|GSM|GDS|SRP|SRR|PRJNA|PRJEB)[1-9][0-9]{0,11}", accession) is None:
            raise ValueError("Use an exact supported study accession")
        _text(link["sample_scope"])
        _text(link["limitations"])
        article, repository = link["article_evidence"], link["repository_evidence"]
        _keys(article, {"source_id", "locator"})
        _keys(repository, {"source", "locator"})
        body = source_bodies[link["publication_id"]].get(article["source_id"])
        if body is None:
            raise ValueError("Article link references an unknown retained source")
        _locator(article["locator"], body)
        _locator(repository["locator"], _source_ref(repository["source"], root))
        pattern = r"(?<![A-Za-z0-9])" + re.escape(accession) + r"(?![A-Za-z0-9])"
        if any(re.search(pattern, location["text"]) is None for location in (article["locator"], repository["locator"])):
            raise ValueError("Article/repository evidence contains another study accession")
        identity = publications[link["publication_id"]]["publication"]["identity"]
        if not _publication_identifier_in_text(identity, repository["locator"]["text"]):
            raise ValueError("Repository evidence lacks the exact associated publication identifier")
        ref = repository["source"]
        source_sizes[(ref["path"], ref["sha256"])] = ref["size_bytes"]
    if sum(source_sizes.values()) > legacy.MAX_AGGREGATE_BYTES:
        raise ValueError("Publication collection aggregate source byte bound exceeded")
    return record


def prepare_publication_collection(output, record, *, workspace=None, allow_evidence_read=False):
    validated = validate_publication_collection(record, workspace=workspace, allow_evidence_read=allow_evidence_read)
    inputs = [publication["reference"]["manifest_path"] for publication in validated["publications"]]
    # All retained original source paths stay disjoint from the new seal.
    for publication in validated["publications"]:
        _, old, _ = _load(_absolute(publication["reference"]["manifest_path"]), publication["reference"]["root_hash"], "publication-evidence.json")
        inputs.extend(source["path"] for source in old["sources"])
    for asset in validated["assets"]:
        if asset["source"] is not None:
            inputs.append(asset["source"]["path"])
        if asset["lineage"] is not None:
            inputs.append(asset["lineage"]["parent_source_ref"]["path"])
        inputs.extend(notice["source_ref"]["path"] for notice in asset["status"]["notices"])
    inputs.extend(link["repository_evidence"]["source"]["path"] for link in validated["dataset_links"])
    output = require_disjoint_output(_absolute(output), inputs)
    receipt = _seal(output, "publication-collection.json", validated, "analysis.publication_collection", scope=COLLECTION_SCOPE)
    return {"manifest_path": str(output / "audit/manifest.json"), "root_hash": receipt["root_hash"]}


def load_publication_collection(manifest, expected_root, *, workspace=None, allow_evidence_read=False):
    if allow_evidence_read is not True:
        raise PermissionError("Publication collection reading requires explicit authority")
    path = _absolute(manifest)
    if workspace is not None and not path.is_relative_to(_absolute(workspace)):
        raise ValueError("Collection manifest lies outside the approved workspace")
    _, record, _ = _load(path, _sha(expected_root), "publication-collection.json")
    return validate_publication_collection(record, workspace=workspace, allow_evidence_read=True)


def _reference_grounding_records(publication_ref, source_collection_ref, workspace, allow_evidence_read):
    if allow_evidence_read is not True:
        raise PermissionError("Reference source grounding requires explicit evidence-read authority")
    root = _absolute(workspace) if workspace is not None else None
    publication_ref = _copy(publication_ref)
    _keys(publication_ref, {"manifest_path", "root_hash"})
    path = _absolute(publication_ref["manifest_path"])
    if root is not None and not path.is_relative_to(root):
        raise ValueError("Selected publication manifest lies outside the approved workspace")
    publication = legacy.load_publication_evidence(path, _sha(publication_ref["root_hash"]),
        workspace=root, allow_evidence_read=True)
    collection, publication_id = None, None
    if source_collection_ref is not None:
        source_collection_ref = _copy(source_collection_ref)
        _keys(source_collection_ref, {"manifest_path", "root_hash"})
        collection = load_publication_collection(source_collection_ref["manifest_path"], source_collection_ref["root_hash"],
            workspace=root, allow_evidence_read=True)
        matches = [item for item in collection["publications"] if item["reference"] == publication_ref]
        if len(matches) != 1:
            raise ValueError("Source collection must contain the exact selected publication reference once")
        publication_id = matches[0]["publication_id"]
    return root, publication_ref, source_collection_ref, publication, collection, publication_id


@dataclass(frozen=True, slots=True, init=False)
class _ReferenceGroundingFrame:
    """Coordinator-local verified frame; never reconstruct from caller JSON."""
    _token: object
    _root: object
    _records_json: bytes
    bodies: object

    def __init__(self, records, bodies, *, _token):
        if _token is not _GROUNDING_FRAME_TOKEN:
            raise PermissionError("Grounding frames require actual retained-source verification")
        from devharness_core.provenance import canonical_json
        # Each verified publication/collection record already has a 1 MB cap.
        encoded = canonical_json(list(records[1:]))
        if len(encoded) > 2_100_000:
            raise ValueError("Grounding snapshot metadata exceeds its combined proof-record bound")
        object.__setattr__(self, "_token", _token)
        object.__setattr__(self, "_root", records[0])
        object.__setattr__(self, "_records_json", encoded)
        object.__setattr__(self, "bodies", MappingProxyType(dict(bodies)))

    @property
    def records(self):
        return (self._root, *decode_json(self._records_json))


def _prepare_reference_grounding_frame(publication_ref, *, source_collection_ref=None, workspace=None,
                                       allow_evidence_read=False):
    """One local exact-byte snapshot, capped at 20 MB unique selected sources.

    The snapshot cap is narrower than general source-collection admission. Read
    bounded proof metadata and reserve deduplicated actual sizes before loading
    any selected body that would exceed this private-frame cap.
    """
    if allow_evidence_read is not True:
        raise PermissionError("Reference grounding snapshots require explicit evidence-read authority")
    root = _absolute(workspace) if workspace is not None else None
    selected = _copy(publication_ref)
    _keys(selected, {"manifest_path", "root_hash"})
    path = _absolute(selected["manifest_path"])
    if root is not None and not path.is_relative_to(root):
        raise ValueError("Selected publication proof is outside the approved workspace")
    _, preliminary, _ = _load(path, _sha(selected["root_hash"]), "publication-evidence.json")
    refs, collection_preliminary, selected_id = [], None, None
    for source in _list(preliminary.get("sources"), 16, nonempty=True):
        if type(source) is not dict or not {"path", "sha256"} <= set(source):
            raise ValueError("Malformed retained source snapshot identity")
        source_path = _absolute(source["path"])
        refs.append({"path": str(source_path), "sha256": source["sha256"], "size_bytes": source_path.lstat().st_size})
    if source_collection_ref is not None:
        collection_ref = _copy(source_collection_ref)
        _keys(collection_ref, {"manifest_path", "root_hash"})
        collection_path = _absolute(collection_ref["manifest_path"])
        if root is not None and not collection_path.is_relative_to(root):
            raise ValueError("Collection proof is outside the approved workspace")
        _, collection_preliminary, _ = _load(collection_path, _sha(collection_ref["root_hash"]), "publication-collection.json")
        publications = _list(collection_preliminary.get("publications"), 8, nonempty=True)
        matches = [item for item in publications if type(item) is dict and item.get("reference") == selected]
        if len(matches) != 1:
            raise ValueError("Collection must contain the exact selected publication once")
        selected_id = matches[0]["publication_id"]
        for asset in _list(collection_preliminary.get("assets"), 64):
            if type(asset) is dict and asset.get("publication_id") == selected_id:
                refs.extend(_asset_source_refs(asset))
    reserved, total = {}, 0
    for ref in refs:
        _keys(ref, {"path", "sha256", "size_bytes"})
        source_path = _absolute(ref["path"])
        if root is not None and not source_path.is_relative_to(root):
            raise ValueError("Snapshot source is outside the approved workspace")
        info = source_path.lstat()
        if (not stat.S_ISREG(info.st_mode) or type(ref["size_bytes"]) is not int or
                not 0 <= ref["size_bytes"] <= legacy.MAX_SOURCE_BYTES or info.st_size != ref["size_bytes"]):
            raise ValueError("Snapshot source is not an exact bounded regular file")
        key = (ref["path"], _sha(ref["sha256"]), ref["size_bytes"])
        if key not in reserved:
            if ref["size_bytes"] > MAX_REFERENCE_SNAPSHOT_BYTES - total:
                raise ValueError("Private reference snapshot exceeds its unique 20 MB byte bound before source reads")
            reserved[key], total = ref, total + ref["size_bytes"]
    records = _reference_grounding_records(publication_ref, source_collection_ref, root, True)
    if records[3] != preliminary or records[4] != collection_preliminary or records[5] != selected_id:
        raise ValueError("Grounding metadata changed during verified snapshot capture")
    bodies = {key: _source_ref(ref, root) for key, ref in reserved.items()}
    return _ReferenceGroundingFrame(records, bodies, _token=_GROUNDING_FRAME_TOKEN)


def _grounding_frame_records(publication_ref, collection_ref, workspace, allow_evidence_read, frame):
    if allow_evidence_read is not True:
        raise PermissionError("Reference grounding reuse requires explicit evidence-read authority")
    if type(frame) is not _ReferenceGroundingFrame or frame._token is not _GROUNDING_FRAME_TOKEN:
        raise ValueError("Use a genuinely verified private grounding frame, not caller JSON")
    root = _absolute(workspace) if workspace is not None else None
    records = frame.records
    if records[:3] != (root, publication_ref, collection_ref):
        raise ValueError("Grounding snapshot belongs to a different selected publication, collection or workspace")
    return records


def _grounding_body(source_ref, root, frame):
    if frame is None:
        return _source_ref(source_ref, root)
    _keys(source_ref, {"path", "sha256", "size_bytes"})
    key = (source_ref["path"], source_ref["sha256"], source_ref["size_bytes"])
    if key not in frame.bodies:
        raise ValueError("Source lacks exact byte identity in the verified grounding snapshot")
    body = frame.bodies[key]
    if type(body) is not bytes or len(body) != source_ref["size_bytes"] or hashlib.sha256(body).hexdigest() != source_ref["sha256"]:
        raise ValueError("Verified grounding snapshot byte size/hash identity changed")
    return body


def validate_reference_grounding_binding(publication_ref, *, source_collection_ref=None, workspace=None,
                                         allow_evidence_read=False, _grounding_frame=None):
    """Verify selected retained roots even when no reference findings exist.

    This trusted coordinator projection includes admitted paths, never quote
    text or outcomes. Paths require the existing analyst projection/denial gates.
    """
    records = (_reference_grounding_records(publication_ref, source_collection_ref, workspace, allow_evidence_read)
        if _grounding_frame is None else _grounding_frame_records(publication_ref, source_collection_ref, workspace,
                                                                 allow_evidence_read, _grounding_frame))
    _, selected, collection_ref, publication, collection, publication_id = records
    paths = {source["path"] for source in publication["sources"]}
    if collection is not None:
        for asset in collection["assets"]:
            paths.update(ref["path"] for ref in _asset_source_refs(asset))
        paths.update(link["repository_evidence"]["source"]["path"] for link in collection["dataset_links"])
    return _copy({"format": "devbench.rna-reference-grounding-binding/2", "publication_ref": selected,
        "source_collection_ref": collection_ref, "publication_id": publication_id,
        "identity": publication["publication"]["identity"], "curator_id": publication["curator_id"],
        "exposure": _copy(publication["exposure"]),
        "participant_id": publication["participant_id"], "retained_source_paths": sorted(paths),
        "scope": COLLECTION_SCOPE})


def validate_reference_source_membership(publication_ref, source_ref, excerpt, *, source_collection_ref=None,
                                         workspace=None, allow_evidence_read=False, _grounding_frame=None):
    """Bind a quote to a selected publication's actual retained source custody.

    /1 requires the exact admitted excerpt record. An expanded collection quote
    requires an admitted derived locator JSON artifact {source_ref, excerpt},
    with actual parent lineage to its same-publication body asset. Identity names
    and arbitrary local quote bytes cannot substitute for retained membership.
    """
    records = (_reference_grounding_records(publication_ref, source_collection_ref, workspace, allow_evidence_read)
        if _grounding_frame is None else _grounding_frame_records(publication_ref, source_collection_ref, workspace,
                                                                 allow_evidence_read, _grounding_frame))
    root, selected, collection_ref, publication, collection, publication_id = records
    source_ref, excerpt = _copy(source_ref), _copy(excerpt)
    _keys(source_ref, {"path", "sha256", "size_bytes"})
    _absolute(source_ref["path"])
    _sha(source_ref["sha256"])
    if type(source_ref["size_bytes"]) is not int or not 0 <= source_ref["size_bytes"] <= legacy.MAX_SOURCE_BYTES:
        raise ValueError("Reference source exceeds its bounded byte identity")
    _keys(excerpt, {"excerpt_id", "source_id", "locator", "visibility", "purpose"})
    _identifier(excerpt["excerpt_id"])
    _identifier(excerpt["source_id"])
    if (type(excerpt["visibility"]) is not str or excerpt["visibility"] not in {"participant", "evaluator"} or
            type(excerpt["purpose"]) is not str or excerpt["purpose"] not in {"question", "data", "methods", "outcome"} or
            excerpt["purpose"] == "outcome" and excerpt["visibility"] != "evaluator"):
        raise ValueError("Reference excerpt must preserve admitted publication purpose/custody")
    for source in publication["sources"]:
        if (source["source_id"] == excerpt["source_id"] and source["path"] == source_ref["path"] and
                source["sha256"] == source_ref["sha256"] and excerpt in publication["excerpts"]):
            _locator(excerpt["locator"], _grounding_body(source_ref, root, _grounding_frame))
            return _copy({"format": "devbench.rna-reference-source-membership/2", "publication_ref": selected,
                "source_collection_ref": collection_ref, "source_ref": source_ref, "source_id": source["source_id"],
                "excerpt_id": excerpt["excerpt_id"], "membership": "publication_excerpt",
                "origin_access": source["access"], "locator_record_ref": None, "scope": COLLECTION_SCOPE})
    if collection is not None:
        assets = [asset for asset in collection["assets"] if asset["publication_id"] == publication_id]
        bodies = [asset for asset in assets if asset["availability"] == "available" and
                  asset["source"] == source_ref and asset["asset_id"] == excerpt["source_id"]]
        if len(bodies) == 1:
            body_asset = bodies[0]
            _locator(excerpt["locator"], _grounding_body(body_asset["source"], root, _grounding_frame))
            matches = []
            for asset in assets:
                if (asset["availability"] != "available" or asset["asset_kind"] != "extracted_json" or
                        asset["lineage"] is None or asset["lineage"]["parent_source_ref"] != source_ref or
                        asset["origin_access"] != body_asset["origin_access"]):
                    continue
                retained = decode_json(_grounding_body(asset["source"], root, _grounding_frame))
                if (type(retained) is dict and set(retained) == {"source_ref", "excerpt"} and
                        retained["source_ref"] == source_ref and retained["excerpt"] == excerpt):
                    matches.append(asset["source"])
            if len(matches) == 1:
                return _copy({"format": "devbench.rna-reference-source-membership/2", "publication_ref": selected,
                    "source_collection_ref": collection_ref, "source_ref": source_ref, "source_id": body_asset["asset_id"],
                    "excerpt_id": excerpt["excerpt_id"], "membership": "collection_locator_artifact",
                    "origin_access": body_asset["origin_access"], "locator_record_ref": matches[0], "scope": COLLECTION_SCOPE})
    raise ValueError("Reference source/excerpt lacks exact admitted membership in the selected publication custody")
