"""Synthetic source custody and offline provider contracts; no actual articles."""
from copy import deepcopy
import hashlib
import json

import pytest

from devbench.analysis import rna_publication_sources as api
from devbench.analysis import rna_publication_evidence as legacy


IDENTITY = {"doi": "10.1234/artificial-fixture", "pmid": "123456", "pmcid": "PMC123456",
            "version": "fixture-v1", "title": "Artificial endpoint fixture"}
URL = "https://publication.example.invalid/fixture-v1"


def asset(tmp_path, *, kind="article_text", availability="available"):
    body = ("\n".join(IDENTITY.values()) + "\nArtificial retained article. License for synthetic testing only.").encode()
    path = tmp_path / "source.txt"
    path.write_bytes(body)
    locators = {}
    for field, value in IDENTITY.items():
        start = body.index(value.encode())
        locators[field] = {"start_byte": start, "end_byte": start + len(value.encode()),
                           "text": value, "location": "Artificial source identity"}
    return {"format": api.SOURCE_ASSET_FORMAT, "asset_id": "asset1", "publication_id": "paper1",
        "source": {"path": str(path), "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body)}
                   if availability == "available" else None,
        "requested_identity": deepcopy(IDENTITY), "resolved_identity": deepcopy(IDENTITY),
        "identity_match_basis": "exact_declared_identifiers_title_version", "identity_reviewed_by": "curator",
        "identity_locators": locators if kind == "article_text" and availability == "available" else None,
        "conflicts": [], "provider": "curator_supplied", "source_url": URL,
        "retrieved_at": "2026-10-11T00:00:00+00:00", "content_type": "text/plain",
        "asset_kind": kind, "availability": availability, "origin_access": "full_text",
        "version_type": "unknown", "article_instance_id": None,
        "provider_checksums": [], "provider_etag": None, "provider_modified_at": None,
        "rights": {"license_text": "Synthetic fixture", "license_locator": None,
            "intended_use": "local synthetic verification", "decision": "permitted",
            "reviewed_by": "curator"},
        "status": {"state": "unknown_not_assessed", "checked_at": None, "checked_providers": [],
            "coverage": "No real provider notice check", "notices": [], "reviewed_by": "curator"},
        "lineage": None, "limitations": "Unauthenticated synthetic source declarations."}


def publication():
    return {"identity": deepcopy(IDENTITY), "approved_urls": [URL]}


def test_routes_pin_current_contracts_and_disable_unverified_providers():
    contracts = api.provider_contracts()
    assert contracts["pmc_oa_legacy"]["enabled"] is False
    assert contracts["europe_pmc"]["enabled"] is False
    assert contracts["geo"]["enabled"] is False
    assert api.exact_provider_url("crossref", IDENTITY["doi"]) == legacy.crossref_doi_metadata_url(IDENTITY["doi"])
    assert api.exact_provider_url("pmc_id_converter", "PMC123456", id_type="pmcid") == (
        "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
        "?idtype=pmcid&ids=PMC123456&format=json&versions=no&showaiid=no")
    assert api.exact_provider_url("pmc_cloud", "PMC123456", version=2, asset_kind="article_xml") == (
        "https://pmc-oa-opendata.s3.amazonaws.com/PMC123456.2/PMC123456.2.xml")
    for provider in ("pmc_oa_legacy", "europe_pmc", "geo"):
        with pytest.raises(ValueError, match="disabled"):
            api.exact_provider_url(provider, "PMC123456")


@pytest.mark.parametrize("identifier,kwargs", [("a title", {}), ("PMC123456/../x", {"version": 1}),
    ("PMC123456", {"version": True}), ("PMC123456", {"version": 1, "asset_kind": "pdf"})])
def test_cloud_rejects_queries_path_traversal_and_unsupported_capabilities(identifier, kwargs):
    with pytest.raises(ValueError):
        api.exact_provider_url("pmc_cloud", identifier, **kwargs)


def test_discovery_preserves_conflicts_without_resolving_or_promoting_metadata():
    candidates = [{"identity": deepcopy(IDENTITY), "source_url": URL, "access": "metadata"},
                  {"identity": {**IDENTITY, "title": "Artificial endpoint fixtures"},
                   "source_url": URL, "access": "abstract"}]
    result = api.discover_publication_candidates(IDENTITY, candidates)
    assert result["resolution_status"] == "unresolved"
    assert result["full_text_acquired"] is False
    assert result["conflicts"][0]["fields"] == ["title"]
    assert result["candidates"] == candidates


def mapping_body(**changes):
    return json.dumps({"status": "ok", "response-date": "2026-10-11 00:00:00",
        "request": {"format": "json", "ids": ["PMC123456"], "idtype": "pmcid", "versions": "no", "showaiid": "no"},
        "records": [{"requested-id": "PMC123456", "pmcid": "PMC123456", "pmid": 123456,
                     "doi": IDENTITY["doi"], **changes}]}).encode()


def test_pmc_mapping_is_exact_but_still_unresolved_metadata():
    result = api.decode_pmc_id_mapping(mapping_body(), requested_id="PMC123456", id_type="pmcid",
                                      expected_identifiers={key: IDENTITY[key] for key in ("doi", "pmid", "pmcid")})
    assert result["resolved_identifiers"]["pmid"] == "123456"
    assert result["access"] == "metadata" and result["full_text_acquired"] is False
    assert result["resolution_status"] == "unresolved"
    assert result["title"] is None and result["publication_version"] is None


def test_current_cloud_metadata_preserves_license_type_notices_and_observed_links():
    value = {"pmcid": "PMC123456", "version": 2, "pmid": "123456", "doi": IDENTITY["doi"],
        "title": IDENTITY["title"], "citation": "Synthetic", "is_pmc_openaccess": "yes",
        "is_manuscript": "yes", "is_historical_ocr": "no", "is_retracted": "no", "license_code": "CC_BY",
        "xml_url": "https://pmc-oa-opendata.s3.amazonaws.com/PMC123456.2/PMC123456.2.xml?md5=" + "a" * 32,
        "pdf_url": None, "media_urls": [], "text_url": None}
    result = api.decode_pmc_cloud_metadata(json.dumps(value).encode(), expected_pmcid="PMC123456",
        expected_version=2, expected_title=IDENTITY["title"])
    assert result["version_type"] == "author_manuscript"
    assert result["reported_retracted"] is False and result["notice_coverage"] == "provider_metadata_flag_only"
    assert result["license_code"] == "CC_BY" and result["rights_decision"] == "requires_independent_review"
    assert result["full_text_acquired"] is False and result["links_are_retrieval_authority"] is False
    value["title"] = "Similar artificial endpoint fixture"
    with pytest.raises(ValueError):
        api.decode_pmc_cloud_metadata(json.dumps(value).encode(), expected_pmcid="PMC123456",
            expected_version=2, expected_title=IDENTITY["title"])


def test_cloud_article_bytes_require_requested_id_and_title_before_returning_candidate(monkeypatch, tmp_path):
    from tests.test_method_sources import _offline, _Response
    body = b'<article><front><article-meta><article-id pub-id-type="pmc">789</article-id><title-group><article-title>Wrong artificial paper</article-title></title-group></article-meta></front><body><sec><p>Wrong study.</p></sec></body></article>'
    url = api.exact_provider_url("pmc_cloud", "PMC123456", version=1)
    _offline(monkeypatch, [_Response(body, headers={"Content-Type": "application/xml"})])
    with pytest.raises(ValueError, match="identifier"):
        api.retrieve_exact_provider_source("pmc_cloud", "PMC123456", tmp_path / "wrong.xml",
            version=1, approved_url=url, expected_title=IDENTITY["title"], allow_network=True)
    # Preserve failed acquisition bytes for diagnosis; they are never admitted.
    assert (tmp_path / "wrong.xml").read_bytes() == body


@pytest.mark.parametrize("body", [mapping_body(pmcid="PMC789"), mapping_body(**{"requested-id": "PMC789"}),
    mapping_body().replace(b'"records": [', b'"records": [null,'), b'{"status":"ok","status":"error"}',
    b"x" * 1_000_001])
def test_mapping_conflicts_and_drift_fail_closed(body):
    with pytest.raises(ValueError):
        api.decode_pmc_id_mapping(body, requested_id="PMC123456", id_type="pmcid")


def test_provider_transport_requires_exact_approval_and_cannot_overwrite(monkeypatch, tmp_path):
    from tests.test_method_sources import _offline, _Response
    url = api.exact_provider_url("pmc_id_converter", "PMC123456", id_type="pmcid")
    calls = _offline(monkeypatch, [_Response(mapping_body(), headers={"Content-Type": "application/json"})])
    with pytest.raises(PermissionError):
        api.retrieve_exact_provider_source("pmc_id_converter", "PMC123456", tmp_path / "metadata.json",
            id_type="pmcid", approved_url=url)
    assert calls["dns"] == []
    result = api.retrieve_exact_provider_source("pmc_id_converter", "PMC123456", tmp_path / "metadata.json",
        id_type="pmcid", approved_url=url, allow_network=True)
    assert result["metadata"]["full_text_acquired"] is False
    original = (tmp_path / "metadata.json").read_bytes()
    with pytest.raises(FileExistsError):
        api.retrieve_exact_provider_source("pmc_id_converter", "PMC123456", tmp_path / "metadata.json",
            id_type="pmcid", approved_url=url, allow_network=True)
    assert (tmp_path / "metadata.json").read_bytes() == original


@pytest.mark.parametrize("response", ["redirect", "truncated", "oversized", "legacy"])
def test_hostile_transport_is_rejected_before_asset_admission(monkeypatch, tmp_path, response):
    from tests.test_method_sources import _offline, _Response
    url = api.exact_provider_url("pmc_id_converter", "PMC123456", id_type="pmcid")
    if response == "redirect":
        fixture = _Response(status=302, headers={"Location": "https://127.0.0.1/private"})
    elif response == "legacy":
        fixture = _Response(status=302, headers={"Location": "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id=PMC123456"})
    elif response == "truncated":
        fixture = _Response(b"{}", headers={"Content-Type": "application/json", "Content-Length": "100"})
    else:
        fixture = _Response(b"x" * 101, headers={"Content-Type": "application/json"})
    calls = _offline(monkeypatch, [fixture])
    with pytest.raises((ValueError, PermissionError)):
        api.retrieve_exact_provider_source("pmc_id_converter", "PMC123456", tmp_path / "metadata.json",
            id_type="pmcid", approved_url=url, allow_network=True, max_bytes=100)
    assert len(calls["connections"]) == 1
    assert not (tmp_path / "metadata.json").exists()


def test_assets_bind_bytes_and_do_not_infer_version_of_record(tmp_path):
    record = asset(tmp_path)
    assert api.validate_source_asset(record, publication=publication(), workspace=tmp_path,
                                     allow_evidence_read=True) == record
    changed = deepcopy(record)
    changed["source"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        api.validate_source_asset(changed, publication=publication(), workspace=tmp_path, allow_evidence_read=True)
    assert record["version_type"] == "unknown"


def test_collection_asset_can_extend_source_urls_without_rewriting_prior_manifest(tmp_path):
    record = asset(tmp_path)
    record["source_url"] = "https://supplement.example.invalid/new-source"
    assert api.validate_source_asset(record, publication=publication(), workspace=tmp_path,
        allow_evidence_read=True)["source_url"] == record["source_url"]


def test_source_kind_cannot_relabel_metadata_mime_as_full_text(tmp_path):
    record = asset(tmp_path)
    record["content_type"] = "application/json"
    with pytest.raises(ValueError, match="MIME|content type|access"):
        api.validate_source_asset(record, publication=publication(), workspace=tmp_path, allow_evidence_read=True)


@pytest.mark.parametrize("representation", ["json", "xml"])
def test_utf8_bom_cannot_hide_metadata_representation_in_fulltext_asset(tmp_path, representation):
    record = asset(tmp_path)
    text = json.dumps(IDENTITY) if representation == "json" else (
        "<article><front><meta>" + "\n".join(IDENTITY.values()) + "</meta></front></article>")
    body = b"\xef\xbb\xbf \n" + text.encode()
    path = tmp_path / "bom-metadata.txt"
    path.write_bytes(body)
    record["source"] = {"path": str(path), "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body)}
    record["identity_locators"] = {}
    for field, value in IDENTITY.items():
        start = body.index(value.encode())
        record["identity_locators"][field] = {"start_byte": start, "end_byte": start + len(value.encode()),
            "text": value, "location": "Metadata representation, not article text"}
    with pytest.raises(ValueError, match="metadata|XML|MIME|representation"):
        api.validate_source_asset(record, publication=publication(), workspace=tmp_path, allow_evidence_read=True)


def test_abstract_only_jats_cannot_be_admitted_as_fulltext(tmp_path):
    body = b'<article><front><article-meta><article-id pub-id-type="pmc">123456</article-id><title-group><article-title>Artificial endpoint fixture</article-title></title-group><abstract><p>An abstract only.</p></abstract></article-meta></front></article>'
    record = asset(tmp_path, kind="article_xml")
    path = tmp_path / "abstract.xml"
    path.write_bytes(body)
    record["source"] = {"path": str(path), "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body)}
    record["content_type"] = "application/xml"
    with pytest.raises(ValueError, match="full.text|body|abstract"):
        api.validate_source_asset(record, publication=publication(), workspace=tmp_path, allow_evidence_read=True)


@pytest.mark.parametrize("pmcid,title", [("789", IDENTITY["title"]), ("123456", "Similar artificial endpoint fixture")])
def test_raw_jats_asset_rejects_actual_identity_conflicts(tmp_path, pmcid, title):
    body = ('<article><front><article-meta><article-id pub-id-type="pmc">' + pmcid +
        '</article-id><title-group><article-title>' + title + '</article-title></title-group></article-meta></front>'
        '<body><sec><p>Actual bounded article body.</p></sec></body></article>').encode()
    record = asset(tmp_path, kind="article_xml")
    path = tmp_path / "wrong-article.xml"
    path.write_bytes(body)
    record["source"] = {"path": str(path), "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body)}
    record["content_type"] = "application/xml"
    with pytest.raises(ValueError, match="identity|identifier|title"):
        api.validate_source_asset(record, publication=publication(), workspace=tmp_path, allow_evidence_read=True)


def test_raw_text_asset_requires_actual_identity_byte_locators(tmp_path):
    record = asset(tmp_path)
    path = tmp_path / "unrelated.txt"
    path.write_bytes(b"Unrelated placeholder bytes.")
    record["source"] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size_bytes": path.stat().st_size}
    with pytest.raises(ValueError, match="identity|locator|excerpt"):
        api.validate_source_asset(record, publication=publication(), workspace=tmp_path, allow_evidence_read=True)


def test_matching_raw_jats_identity_is_retained_without_certifying_completeness(tmp_path):
    body = b'<article><front><article-meta><article-id pub-id-type="pmc">123456</article-id><title-group><article-title>Artificial endpoint fixture</article-title></title-group></article-meta></front><body><sec><p>Actual bounded article body.</p></sec></body></article>'
    record = asset(tmp_path, kind="article_xml")
    path = tmp_path / "matched.xml"
    path.write_bytes(body)
    record["source"] = {"path": str(path), "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body)}
    record["content_type"] = "application/xml"
    assert api.validate_source_asset(record, publication=publication(), workspace=tmp_path,
        allow_evidence_read=True) == record


@pytest.mark.parametrize("body_content", ["<p/>", "<sec/>", "<sec><title>Results</title><p/></sec>"])
def test_empty_body_placeholders_cannot_promote_abstract_to_fulltext(tmp_path, body_content):
    body = ('<article><front><article-meta><article-id pub-id-type="pmc">123456</article-id>'
        '<title-group><article-title>Artificial endpoint fixture</article-title></title-group>'
        '<abstract><p>Only an abstract.</p></abstract></article-meta></front><body>' + body_content + '</body></article>').encode()
    record = asset(tmp_path, kind="article_xml")
    path = tmp_path / "empty-body.xml"
    path.write_bytes(body)
    record["source"] = {"path": str(path), "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body)}
    record["content_type"] = "application/xml"
    with pytest.raises(ValueError, match="body|abstract|full.text"):
        api.validate_source_asset(record, publication=publication(), workspace=tmp_path, allow_evidence_read=True)


@pytest.mark.parametrize("field,value", [("title", "Artificial endpoint fixtures"), ("doi", "10.1234/wrong"),
    ("pmid", "789"), ("pmcid", "PMC789"), ("version", "fixture-v2")])
def test_admitted_asset_cannot_hide_conflicting_identity(tmp_path, field, value):
    record = asset(tmp_path)
    record["resolved_identity"][field] = value
    with pytest.raises(ValueError):
        api.validate_source_asset(record, publication=publication(), workspace=tmp_path, allow_evidence_read=True)


def test_abstract_metadata_and_derived_assets_cannot_become_fulltext(tmp_path):
    record = asset(tmp_path, kind="metadata")
    with pytest.raises(ValueError, match="access"):
        api.validate_source_asset(record, publication=publication(), workspace=tmp_path, allow_evidence_read=True)
    record["asset_kind"] = "extracted_json"
    record["lineage"] = {"parent_source_ref": deepcopy(record["source"]), "parent_access": "metadata",
        "extractor": "synthetic", "extractor_version": "1", "reviewed_by": "curator"}
    with pytest.raises(ValueError, match="access"):
        api.validate_source_asset(record, publication=publication(), workspace=tmp_path, allow_evidence_read=True)


@pytest.mark.parametrize("availability", ["restricted", "absent"])
def test_unavailable_supplements_are_explicit_without_fabricated_bytes(tmp_path, availability):
    record = asset(tmp_path, kind="supplement", availability=availability)
    record["origin_access"] = "supplement"
    record["rights"]["decision"] = "restricted" if availability == "restricted" else "unknown"
    assert api.validate_source_asset(record, publication=publication(), workspace=tmp_path,
                                     allow_evidence_read=True)["source"] is None
    record["source"] = {"path": str(tmp_path / "missing"), "sha256": "0" * 64, "size_bytes": 0}
    with pytest.raises(ValueError):
        api.validate_source_asset(record, publication=publication(), workspace=tmp_path, allow_evidence_read=True)


def test_status_no_notice_claim_requires_scoped_actual_check(tmp_path):
    record = asset(tmp_path)
    record["status"]["state"] = "none_found_in_checked_sources"
    with pytest.raises(ValueError, match="check"):
        api.validate_source_asset(record, publication=publication(), workspace=tmp_path, allow_evidence_read=True)


@pytest.fixture
def collection_fixture(monkeypatch, tmp_path):
    from devbench.study import permissions
    from tests.test_rna_publication_evidence import evidence_record
    monkeypatch.setattr(permissions, "platform_scratch_roots", lambda: ())
    legacy_record = evidence_record(tmp_path)
    path = tmp_path / "source.txt"
    path.write_bytes(path.read_bytes() + b"Study accession GSE123456 in the specified contrast.\n")
    legacy_record["sources"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    binding = legacy.prepare_publication_evidence(tmp_path / "old-publication", legacy_record["publication"],
        legacy_record["sources"], legacy_record["excerpts"], legacy_record["exposure"],
        curator_id=legacy_record["curator_id"], participant_id=legacy_record["participant_id"],
        workspace=tmp_path, allow_evidence_read=True)
    record = {"format": api.COLLECTION_FORMAT, "collection_id": "collection1", "curator_id": "independent-curator",
        "participant_id": "workflow-author", "publications": [{"publication_id": "paper1", "reference": binding}],
        "assets": [], "dataset_links": [], "scope": api.COLLECTION_SCOPE}
    return tmp_path, legacy_record, record


def test_collection_references_unchanged_single_publication_manifest(collection_fixture):
    path, legacy_record, record = collection_fixture
    before = (path / "old-publication/publication-evidence.json").read_bytes()
    binding = api.prepare_publication_collection(path / "collection", record, workspace=path, allow_evidence_read=True)
    assert api.load_publication_collection(binding["manifest_path"], binding["root_hash"],
        workspace=path, allow_evidence_read=True) == record
    assert (path / "old-publication/publication-evidence.json").read_bytes() == before


def test_reference_membership_is_exact_retained_publication_source_and_excerpt(collection_fixture):
    path, retained, collection = collection_fixture
    original = retained["sources"][0]
    source = {"path": original["path"], "sha256": original["sha256"], "size_bytes": (path / "source.txt").stat().st_size}
    publication_ref = collection["publications"][0]["reference"]
    excerpt = retained["excerpts"][1]
    membership = api.validate_reference_source_membership(publication_ref, source, excerpt,
        workspace=path, allow_evidence_read=True)
    assert membership["membership"] == "publication_excerpt"
    assert membership["source_id"] == "source1" and membership["origin_access"] == "full_text"
    copied = path / "copied-source.txt"
    copied.write_bytes((path / "source.txt").read_bytes())
    with pytest.raises(ValueError, match="membership|admitted|retained"):
        api.validate_reference_source_membership(publication_ref, {**source, "path": str(copied)}, excerpt,
            workspace=path, allow_evidence_read=True)


def test_reference_membership_rejects_new_quote_and_forged_source_id(collection_fixture):
    path, retained, collection = collection_fixture
    original = retained["sources"][0]
    source = {"path": original["path"], "sha256": original["sha256"], "size_bytes": (path / "source.txt").stat().st_size}
    reference = collection["publications"][0]["reference"]
    for update in ({"excerpt_id": "not-admitted"}, {"source_id": "unrelated-source"}):
        with pytest.raises(ValueError, match="membership|admitted|retained"):
            api.validate_reference_source_membership(reference, source, {**retained["excerpts"][1], **update},
                workspace=path, allow_evidence_read=True)


def test_private_grounding_frame_reuses_verified_bytes_and_actual_exposure(collection_fixture, monkeypatch):
    path, retained, collection = collection_fixture
    original = retained["sources"][0]
    source = {"path": original["path"], "sha256": original["sha256"], "size_bytes": (path / "source.txt").stat().st_size}
    selected = collection["publications"][0]["reference"]
    frame = api._prepare_reference_grounding_frame(selected, workspace=path, allow_evidence_read=True)
    def forbidden(*args, **kwargs):
        pytest.fail("Grounding reuse reread/reparsed paths after a genuinely verified snapshot")
    monkeypatch.setattr(api, "_reference_grounding_records", forbidden)
    monkeypatch.setattr(api, "_read", forbidden)
    monkeypatch.setattr(legacy, "_read", forbidden)
    metadata = api.validate_reference_grounding_binding(selected, workspace=path,
        allow_evidence_read=True, _grounding_frame=frame)
    assert metadata["exposure"] == retained["exposure"]
    for _ in range(3):
        assert api.validate_reference_source_membership(selected, source, retained["excerpts"][1],
            workspace=path, allow_evidence_read=True, _grounding_frame=frame)["membership"] == "publication_excerpt"
    with pytest.raises(ValueError, match="frame|snapshot"):
        api.validate_reference_grounding_binding(selected, workspace=path, allow_evidence_read=True,
            _grounding_frame={"publication_ref": selected})


def test_private_snapshot_bound_is_preflighted_before_source_reads(collection_fixture, monkeypatch):
    path, retained, collection = collection_fixture
    source_path = path / "source.txt"
    monkeypatch.setattr(api, "MAX_REFERENCE_SNAPSHOT_BYTES", source_path.stat().st_size - 1)
    for module in (api, legacy):
        original = module._read
        def guarded_read(target, limit, original=original):
            if target == source_path:
                pytest.fail("Overbound private snapshot loaded source bytes before actual-size admission")
            return original(target, limit)
        monkeypatch.setattr(module, "_read", guarded_read)
    with pytest.raises(ValueError, match="snapshot|20 MB|bound"):
        api._prepare_reference_grounding_frame(collection["publications"][0]["reference"],
            workspace=path, allow_evidence_read=True)


def test_public_grounding_metadata_cannot_mutate_verified_frame_identity(collection_fixture, monkeypatch):
    path, retained, collection = collection_fixture
    selected = collection["publications"][0]["reference"]
    frame = api._prepare_reference_grounding_frame(selected, workspace=path, allow_evidence_read=True)
    first = api.validate_reference_grounding_binding(selected, workspace=path, allow_evidence_read=True, _grounding_frame=frame)
    def forbidden(*args, **kwargs):
        pytest.fail("Alias protection reread files rather than preserving the verified snapshot")
    monkeypatch.setattr(api, "_read", forbidden)
    monkeypatch.setattr(legacy, "_read", forbidden)
    first["identity"]["title"] = "Injected source title"
    first["exposure"]["prior_outcome_access"] = "no"
    first["retained_source_paths"].append("/unrelated")
    second = api.validate_reference_grounding_binding(selected, workspace=path, allow_evidence_read=True, _grounding_frame=frame)
    assert second["identity"] == retained["publication"]["identity"]
    assert second["exposure"] == retained["exposure"]
    assert "/unrelated" not in second["retained_source_paths"]
    second["publication_ref"]["root_hash"] = "0" * 64
    assert api.validate_reference_grounding_binding(selected, workspace=path, allow_evidence_read=True,
        _grounding_frame=frame)["publication_ref"] == selected


def test_private_grounding_frame_and_byte_map_are_immutable(collection_fixture):
    path, retained, collection = collection_fixture
    frame = api._prepare_reference_grounding_frame(collection["publications"][0]["reference"],
        workspace=path, allow_evidence_read=True)
    key = next(iter(frame.bodies))
    with pytest.raises(TypeError):
        frame.bodies[key] = b"changed"
    with pytest.raises((AttributeError, TypeError)):
        frame.bodies = {}


def test_grounding_binding_requires_collection_to_contain_exact_selected_publication(collection_fixture):
    path, retained, record = collection_fixture
    selected = record["publications"][0]["reference"]
    binding = api.prepare_publication_collection(path / "collection", record, workspace=path, allow_evidence_read=True)
    assert api.validate_reference_grounding_binding(selected, source_collection_ref=binding,
        workspace=path, allow_evidence_read=True)["publication_id"] == "paper1"
    from tests.test_rna_publication_evidence import evidence_record
    other_root = path / "other-paper"
    other_root.mkdir()
    other = evidence_record(other_root)
    other_binding = legacy.prepare_publication_evidence(path / "other-seal", other["publication"],
        other["sources"], other["excerpts"], other["exposure"], curator_id=other["curator_id"],
        participant_id=other["participant_id"], workspace=path, allow_evidence_read=True)
    with pytest.raises(ValueError, match="selected|publication|membership"):
        api.validate_reference_grounding_binding(other_binding, source_collection_ref=binding,
            workspace=path, allow_evidence_read=True)


def test_collection_quote_requires_admitted_locator_artifact_with_parent_lineage(collection_fixture):
    from tests.test_rna_publication_evidence import locator
    path, retained, record = collection_fixture
    extra = path / "extra-assets"
    extra.mkdir()
    article = asset(extra)
    article["asset_id"] = "articleAsset"
    article["identity_reviewed_by"] = article["rights"]["reviewed_by"] = article["status"]["reviewed_by"] = "independent-curator"
    body = (extra / "source.txt").read_bytes()
    excerpt = {"excerpt_id": "extraOutcome", "source_id": "articleAsset", "locator": locator(body,
        "Artificial retained article."), "visibility": "evaluator", "purpose": "outcome"}
    locator_path = extra / "locator.json"
    locator_path.write_text(json.dumps({"source_ref": article["source"], "excerpt": excerpt}))
    derived = deepcopy(article)
    derived["asset_id"], derived["asset_kind"], derived["content_type"] = "locatorAsset", "extracted_json", "application/json"
    derived["identity_locators"] = None
    derived["source"] = {"path": str(locator_path), "sha256": hashlib.sha256(locator_path.read_bytes()).hexdigest(),
                          "size_bytes": locator_path.stat().st_size}
    derived["lineage"] = {"parent_source_ref": deepcopy(article["source"]), "parent_access": "full_text",
        "extractor": "reviewed-locator", "extractor_version": "1", "reviewed_by": "independent-curator"}
    record["assets"] = [article, derived]
    collection_ref = api.prepare_publication_collection(path / "expanded-collection", record, workspace=path, allow_evidence_read=True)
    selected = record["publications"][0]["reference"]
    membership = api.validate_reference_source_membership(selected, article["source"], excerpt,
        source_collection_ref=collection_ref, workspace=path, allow_evidence_read=True)
    assert membership["membership"] == "collection_locator_artifact"
    assert membership["locator_record_ref"] == derived["source"]
    with pytest.raises(ValueError, match="locator|membership|admitted"):
        api.validate_reference_source_membership(selected, article["source"], {**excerpt, "excerpt_id": "unreviewedQuote"},
            source_collection_ref=collection_ref, workspace=path, allow_evidence_read=True)


def test_reviewed_direct_article_and_repository_link_is_bounded_evidence(collection_fixture):
    from tests.test_rna_publication_evidence import locator
    path, legacy_record, record = collection_fixture
    body = (path / "source.txt").read_bytes()
    repository = path / "repository.txt"
    repository.write_bytes(b"GSE123456 publication 10.1234/artificial-fixture")
    record["dataset_links"] = [{"publication_id": "paper1", "accession": "GSE123456",
        "relationship": "direct_study_data", "article_evidence": {"source_id": "source1",
            "locator": locator(body, "Study accession GSE123456 in the specified contrast.")},
        "repository_evidence": {"source": {"path": str(repository), "sha256": hashlib.sha256(repository.read_bytes()).hexdigest(),
            "size_bytes": repository.stat().st_size}, "locator": locator(repository.read_bytes(), repository.read_text())},
        "reviewed_by": "independent-curator", "sample_scope": "declared contrast",
        "limitations": "Direct link is not independent sample identity or biological compatibility proof."}]
    assert api.validate_publication_collection(record, workspace=path, allow_evidence_read=True) == record


def test_dataset_link_cannot_match_doi_prefix_inside_another_doi(collection_fixture):
    from tests.test_rna_publication_evidence import locator
    path, legacy_record, record = collection_fixture
    body = (path / "source.txt").read_bytes()
    repository = path / "other-study.txt"
    repository.write_bytes(b"GSE123456 publication 10.1234/artificial-fixture-other")
    record["dataset_links"] = [{"publication_id": "paper1", "accession": "GSE123456",
        "relationship": "direct_study_data", "article_evidence": {"source_id": "source1",
            "locator": locator(body, "Study accession GSE123456 in the specified contrast.")},
        "repository_evidence": {"source": {"path": str(repository), "sha256": hashlib.sha256(repository.read_bytes()).hexdigest(),
            "size_bytes": repository.stat().st_size}, "locator": locator(repository.read_bytes(), repository.read_text())},
        "reviewed_by": "independent-curator", "sample_scope": "declared contrast", "limitations": "synthetic"}]
    with pytest.raises(ValueError, match="publication identifier|DOI"):
        api.validate_publication_collection(record, workspace=path, allow_evidence_read=True)


def test_derived_access_is_bound_to_admitted_parent_asset(collection_fixture):
    path, legacy_record, record = collection_fixture
    extra = path / "new-assets"
    extra.mkdir()
    metadata = asset(extra)
    metadata["asset_id"], metadata["asset_kind"], metadata["origin_access"] = "metadata1", "metadata", "metadata"
    metadata["identity_locators"] = None
    metadata["content_type"] = "application/json"
    parent_path = path / "metadata.json"
    parent_path.write_bytes(b'{"abstract":"Artificial metadata only."}')
    metadata["source"] = {"path": str(parent_path), "sha256": hashlib.sha256(parent_path.read_bytes()).hexdigest(),
                           "size_bytes": parent_path.stat().st_size}
    metadata["identity_reviewed_by"] = metadata["rights"]["reviewed_by"] = metadata["status"]["reviewed_by"] = "independent-curator"
    derived = deepcopy(metadata)
    derived["asset_id"], derived["asset_kind"], derived["origin_access"] = "derived1", "extracted_json", "full_text"
    derived_path = path / "derived.json"
    derived_path.write_bytes(b'{"proposed_estimate":"1.2"}')
    derived["source"] = {"path": str(derived_path), "sha256": hashlib.sha256(derived_path.read_bytes()).hexdigest(),
                          "size_bytes": derived_path.stat().st_size}
    derived["lineage"] = {"parent_source_ref": deepcopy(metadata["source"]), "parent_access": "full_text",
        "extractor": "synthetic", "extractor_version": "1", "reviewed_by": "independent-curator"}
    record["assets"] = [metadata, derived]
    with pytest.raises(ValueError, match="parent|access"):
        api.validate_publication_collection(record, workspace=path, allow_evidence_read=True)


def test_aggregate_budget_is_checked_before_loading_source_bodies(collection_fixture, monkeypatch):
    path, legacy_record, record = collection_fixture
    size = (path / "source.txt").stat().st_size
    monkeypatch.setattr(legacy, "MAX_AGGREGATE_BYTES", size - 1)
    source_path = (path / "source.txt")
    for module in (api, legacy):
        original = module._read
        def guarded_read(target, limit, original=original):
            if target == source_path:
                pytest.fail("Source bodies loaded before collection aggregate quota admission")
            return original(target, limit)
        monkeypatch.setattr(module, "_read", guarded_read)
    with pytest.raises(ValueError, match="aggregate|budget"):
        api.validate_publication_collection(record, workspace=path, allow_evidence_read=True)


def test_direct_accession_link_rejects_another_study_and_weak_membership(collection_fixture):
    path, legacy_record, record = collection_fixture
    body = b"This article used accession GSE123456 in the specified contrast."
    repository = path / "repository.txt"
    repository.write_bytes(b"GSE123456 publication 10.1234/artificial-fixture")
    source = path / "source.txt"
    original = source.read_bytes()
    # Independent byte locators need actual retained /1 evidence. A fabricated
    # article link cannot be admitted even if the accession label is plausible.
    link = {"publication_id": "paper1", "accession": "GSE789", "relationship": "direct_study_data",
        "article_evidence": {"source_id": "source1", "locator": {"start_byte": 0, "end_byte": len(body),
            "text": body.decode(), "location": "data availability"}},
        "repository_evidence": {"source": {"path": str(repository), "sha256": hashlib.sha256(repository.read_bytes()).hexdigest(),
            "size_bytes": repository.stat().st_size}, "locator": {"start_byte": 0,
            "end_byte": repository.stat().st_size, "text": repository.read_text(), "location": "repository record"}},
        "reviewed_by": "independent-curator", "sample_scope": "declared contrast", "limitations": "fixture"}
    record["dataset_links"] = [link]
    with pytest.raises(ValueError):
        api.validate_publication_collection(record, workspace=path, allow_evidence_read=True)
    link["relationship"] = "bioproject_membership"
    with pytest.raises(ValueError):
        api.validate_publication_collection(record, workspace=path, allow_evidence_read=True)
    assert source.read_bytes() == original
