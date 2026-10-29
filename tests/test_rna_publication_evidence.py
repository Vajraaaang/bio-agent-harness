"""Artificial bibliographic fixtures; no paper/network/model/data campaign."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from devbench.analysis import rna_publication_evidence as api


IDENTITY = {"doi": "10.1234/artificial-fixture", "pmid": "123456", "pmcid": "PMC123456",
            "version": "fixture-v1", "title": "Artificial endpoint fixture"}
URL = "https://publication.example.invalid/fixture-v1"
EXPOSURE = {"prior_outcome_access": "unknown", "public_outcomes_accessible": "yes",
            "workflow_author_outcome_access": "no", "limitations": "Artificial fixture, no actual blindness claim."}


def publication():
    return {"identity": deepcopy(IDENTITY), "approved_urls": [URL]}


def locator(body, text, location="fixture section"):
    start = body.index(text.encode())
    return {"start_byte": start, "end_byte": start + len(text.encode()), "text": text, "location": location}


def evidence_record(tmp_path):
    body = ("\n".join(IDENTITY.values()) + "\nMethods use a Gaussian model.\n"
            "GENE1 effect 2.0; interval 1.0 to 3.0 at level 0.95.\n").encode()
    path = tmp_path / "source.txt"
    path.write_bytes(body)
    return {"format": api.FORMAT, "publication": publication(), "sources": [{"source_id": "source1",
        "path": str(path), "sha256": hashlib.sha256(body).hexdigest(), "source_url": URL,
        "identity": deepcopy(IDENTITY), "access": "full_text",
        "identity_locators": {key: locator(body, value, "fixture bibliographic metadata") for key, value in IDENTITY.items()},
        "identity_reviewed_by": "independent-curator", "limitations": "Artificial source; unauthenticated metadata."}],
        "excerpts": [{"excerpt_id": "methods1", "source_id": "source1",
                      "locator": locator(body, "Methods use a Gaussian model."), "visibility": "participant", "purpose": "methods"},
                     {"excerpt_id": "outcome1", "source_id": "source1",
                      "locator": locator(body, "GENE1 effect 2.0; interval 1.0 to 3.0 at level 0.95."),
                      "visibility": "evaluator", "purpose": "outcome"}],
        "exposure": deepcopy(EXPOSURE), "curator_id": "independent-curator", "participant_id": "workflow-author",
        "scope": api._SCOPE}


@pytest.fixture
def unit_storage(monkeypatch, tmp_path):
    # Unit seals are not qualification of actual participant/evaluator isolation.
    from devbench.study import permissions
    monkeypatch.setattr(permissions, "platform_scratch_roots", lambda: ())
    return tmp_path


def seal_evidence(unit_storage):
    record = evidence_record(unit_storage)
    binding = api.prepare_publication_evidence(unit_storage / "publication", record["publication"],
        record["sources"], record["excerpts"], record["exposure"], curator_id=record["curator_id"],
        participant_id=record["participant_id"], workspace=unit_storage, allow_evidence_read=True)
    return record, binding


def test_exact_identity_and_normalized_doi_are_resolved():
    candidate = {"identity": deepcopy(IDENTITY), "source_url": URL}
    candidate["identity"]["doi"] = "https://doi.org/10.1234/ARTIFICIAL-FIXTURE"
    assert api.resolve_publication(publication(), [candidate])["identity"]["doi"] == IDENTITY["doi"]


@pytest.mark.parametrize("field,value", [("doi", "10.1234/other"), ("pmid", "789"),
    ("pmcid", "PMC789"), ("version", "fixture-v2"), ("title", "A different publication")])
def test_conflicting_identity_is_rejected(field, value):
    candidate = {"identity": deepcopy(IDENTITY), "source_url": URL}
    candidate["identity"][field] = value
    with pytest.raises(ValueError):
        api.resolve_publication(publication(), [candidate])


def test_ambiguous_unrelated_unapproved_and_conflicting_duplicate_rejected():
    match = {"identity": deepcopy(IDENTITY), "source_url": URL}
    for candidates in ([], [match, match], [{**match, "source_url": "https://other.example.invalid/fixture"}],
                       [match, {"identity": {**IDENTITY, "version": "v2"}, "source_url": URL}]):
        with pytest.raises(ValueError):
            api.resolve_publication(publication(), candidates)
    unrelated = {"identity": {**IDENTITY, "doi": "10.1234/other", "pmid": "789", "pmcid": "PMC789"}, "source_url": URL}
    with pytest.raises(ValueError):
        api.resolve_publication(publication(), [unrelated])


def test_missing_identifier_or_version_cannot_be_invented():
    for replacement in ({"doi": None, "pmid": None, "pmcid": None}, {"version": None}):
        match = {"identity": {**IDENTITY, **replacement}, "source_url": URL}
        with pytest.raises(ValueError):
            api.validate_publication_identity(publication(), match)


def test_quote_and_byte_hash_validation_and_read_authority(tmp_path):
    record = evidence_record(tmp_path)
    with pytest.raises(PermissionError):
        api.validate_publication_evidence(record)
    assert api.validate_publication_evidence(record, workspace=tmp_path, allow_evidence_read=True) == record
    Path(record["sources"][0]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        api.validate_publication_evidence(record, allow_evidence_read=True)


@pytest.mark.parametrize("attack", ["forged_quote", "offset", "metadata", "outcome_visible", "same_curator", "unknown_source", "oversize"])
def test_forged_and_misbound_evidence_is_rejected(tmp_path, attack):
    record = evidence_record(tmp_path)
    if attack == "forged_quote":
        record["excerpts"][0]["locator"]["text"] = "Methods use a fabricated model."
    elif attack == "offset":
        record["excerpts"][0]["locator"]["start_byte"] += 1
    elif attack == "metadata":
        record["sources"][0]["identity_locators"]["doi"] = record["excerpts"][0]["locator"]
    elif attack == "outcome_visible":
        record["excerpts"][1]["visibility"] = "participant"
    elif attack == "same_curator":
        record["curator_id"] = record["participant_id"]
    elif attack == "unknown_source":
        record["excerpts"][0]["source_id"] = "unknown"
    else:
        record["excerpts"][0]["locator"]["text"] = "x" * (api.MAX_EXCERPT_BYTES + 1)
    with pytest.raises(ValueError):
        api.validate_publication_evidence(record, allow_evidence_read=True)


def test_sources_are_workspace_bounded_and_no_symlinks(tmp_path):
    record = evidence_record(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="outside"):
        api.validate_publication_evidence(record, workspace=other, allow_evidence_read=True)
    link = tmp_path / "link"
    link.symlink_to(record["sources"][0]["path"])
    record["sources"][0]["path"] = str(link)
    with pytest.raises(ValueError):
        api.validate_publication_evidence(record, allow_evidence_read=True)


def test_seal_projection_excludes_outcomes_and_original_paths(unit_storage):
    record, binding = seal_evidence(unit_storage)
    loaded = api.load_publication_evidence(binding["manifest_path"], binding["root_hash"], allow_evidence_read=True)
    assert loaded == record
    context = api.participant_publication_context(binding["manifest_path"], binding["root_hash"], allow_evidence_read=True)
    assert [item["excerpt_id"] for item in context["excerpts"]] == ["methods1"]
    assert "GENE1 effect" not in json.dumps(context) and str(unit_storage) not in json.dumps(context)
    assert context["exposure"]["public_outcomes_accessible"] == "yes"
    record_path = unit_storage / "publication/publication-evidence.json"
    record_path.write_text(record_path.read_text().replace("Gaussian", "fabricated"))
    with pytest.raises(ValueError):
        api.load_publication_evidence(binding["manifest_path"], binding["root_hash"], allow_evidence_read=True)


def crossref_body(**changes):
    return json.dumps({"status": "ok", "message-type": "work", "message-version": "1.0.0",
        "message": {"DOI": IDENTITY["doi"], "title": [IDENTITY["title"]], "type": "journal-article", **changes}}).encode()


def test_doi_lookup_builder_and_metadata_preserve_unverified_dimensions():
    assert api.crossref_doi_metadata_url(IDENTITY["doi"]) == "https://api.crossref.org/works/10.1234%2Fartificial-fixture"
    assert api.crossref_doi_metadata_url("10.1234/fixture?a#b&c") == "https://api.crossref.org/works/10.1234%2Ffixture%3Fa%23b%26c"
    decoded = api.decode_crossref_doi_metadata(crossref_body(), expected_doi=IDENTITY["doi"], expected_title=IDENTITY["title"])
    assert decoded["publication_version"] == "unverified"
    assert decoded["update_status"] == "unknown_not_assessed"
    assert decoded["full_text_acquired"] is False and decoded["full_text_links"] == []
    decoded = api.decode_crossref_doi_metadata(crossref_body(**{"update-to": [{"type": "retraction"}]}), expected_doi=IDENTITY["doi"])
    assert decoded["update_status"] == "notices_require_review"
    assert decoded["update_notices"]["update-to"][0]["type"] == "retraction"


@pytest.mark.parametrize("body", [crossref_body(DOI="10.1234/other"), crossref_body(title=["a", "b"]),
    crossref_body().replace(b'"1.0.0"', b'"2.0.0"'), crossref_body().replace(b'"work"', b'"work-list"'),
    b'{"status":"ok","status":"error"}', b"x" * 1_000_001])
def test_doi_metadata_conflict_ambiguity_and_schema_drift_rejected(body):
    with pytest.raises(ValueError):
        api.decode_crossref_doi_metadata(body, expected_doi=IDENTITY["doi"])


def test_default_denied_metadata_acquisition_does_not_touch_transport(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("unapproved lookup attempted transport")
    monkeypatch.setattr(api, "_retrieve_approved_text", forbidden)
    for kwargs in ({}, {"allow_network": True, "approved_url": "https://api.crossref.org/works/other"}):
        with pytest.raises(PermissionError):
            api.retrieve_crossref_doi_metadata(IDENTITY["doi"], tmp_path / "metadata.json",
                **({"approved_url": api.crossref_doi_metadata_url(IDENTITY["doi"])} | kwargs))


@pytest.mark.parametrize("media_type", ["application/json", "application/vnd.crossref-api-message+json"])
def test_metadata_lookup_reuses_bounded_safe_transport_offline(monkeypatch, tmp_path, media_type):
    from tests.test_method_sources import _offline, _Response
    body = crossref_body()
    calls = _offline(monkeypatch, [_Response(body, headers={"Content-Type": media_type})])
    url = api.crossref_doi_metadata_url(IDENTITY["doi"])
    result = api.retrieve_crossref_doi_metadata(IDENTITY["doi"], tmp_path / "metadata.json", approved_url=url,
                                              allow_network=True, expected_title=IDENTITY["title"])
    assert result["metadata"]["doi"] == IDENTITY["doi"]
    assert len(calls["connections"]) == 1 and result["retrieval"]["content_type"] == media_type


def test_default_source_mime_policy_unchanged_and_crossref_binary_rejected(monkeypatch, tmp_path):
    from devbench.analysis.method_sources import retrieve_source
    from tests.test_method_sources import _offline, _Response
    _offline(monkeypatch, [_Response(crossref_body(), headers={"Content-Type": "application/vnd.crossref-api-message+json"})])
    with pytest.raises(ValueError, match="content type"):
        retrieve_source(URL, tmp_path / "source", allowed_hosts=["publication.example.invalid"], allow_network=True)
    _offline(monkeypatch, [_Response(b"PDF", headers={"Content-Type": "application/pdf"})])
    url = api.crossref_doi_metadata_url(IDENTITY["doi"])
    with pytest.raises(ValueError, match="content type"):
        api.retrieve_crossref_doi_metadata(IDENTITY["doi"], tmp_path / "metadata", approved_url=url, allow_network=True)


def test_unapproved_same_host_redirect_is_denied_before_second_hop(monkeypatch, tmp_path):
    from tests.test_method_sources import _offline, _Response
    calls = _offline(monkeypatch, [_Response(status=302, headers={"Location": "/other"})])
    with pytest.raises(PermissionError, match="redirect"):
        api.retrieve_publication_source(publication(), {"identity": deepcopy(IDENTITY), "source_url": URL},
            tmp_path / "source", allow_network=True)
    assert len(calls["connections"]) == 1 and not (tmp_path / "source").exists()


def test_retired_encoded_oa_route_is_rejected_before_transport(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Retired OA route reached transport")
    from devbench.analysis import method_sources
    monkeypatch.setattr(method_sources, "_open_parent", forbidden)
    url = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa%2Efcgi?id=PMC123456"
    with pytest.raises(ValueError, match="retired"):
        api._retrieve_approved_text(url, [url], tmp_path / "source", allow_network=True)


@pytest.mark.parametrize("endpoint", ["oa.fcgi", "oa%2Efcgi"])
def test_retired_route_dns_dot_alias_is_rejected_before_transport(monkeypatch, tmp_path, endpoint):
    def forbidden(*args, **kwargs):
        pytest.fail("Canonical retired DNS-dot alias reached transport")
    from devbench.analysis import method_sources
    monkeypatch.setattr(method_sources, "_open_parent", forbidden)
    url = "https://www.ncbi.nlm.nih.gov./pmc/utils/oa/" + endpoint + "?id=PMC123456"
    with pytest.raises(ValueError, match="retired"):
        api._retrieve_approved_text(url, [url], tmp_path / "source", allow_network=True)
