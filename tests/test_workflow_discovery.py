"""Offline discovery contracts; recorded providers never call network or models."""
from copy import deepcopy
import importlib
import json

import pytest

from tests.test_method_planner import setup


def api():
    return importlib.import_module("devbench.analysis.workflow_discovery")


class RecordedProvider:
    def __init__(self, *, fail=False):
        self.contexts = []
        self.fail = fail

    def configuration(self):
        return {"provider": "recorded_offline_fixture", "network": False}

    def search(self, context, request, *, allow_network):
        self.contexts.append(deepcopy(context))
        if self.fail:
            raise TimeoutError("recorded failure; no search performed")
        return {"raw_response": {"fixture": True, "items": ["synthetic lead"]},
            "candidates": [{"identity": {"doi": "10.1000/synthetic"}, "title": "Synthetic method",
                "url": "https://example.org/method", "kind": "primary_paper", "access": "abstract",
                "excerpt": "Recorded synthetic abstract; not a real publication."}],
            "consumption": {"requests": 0, "response_bytes": 72}}


def request(**overrides):
    return {"query": "RNA normalization alternatives", "allowed_hosts": ["example.org"],
            "acquire_indices": [], **overrides}


def test_requires_explicit_network_authority_before_provider_dispatch(tmp_path):
    planner, state, *_ = setup(tmp_path)
    provider = RecordedProvider()
    with pytest.raises(PermissionError, match="network"):
        api().discover(planner, state["session_id"], provider=provider, request=request(), actor="scientist")
    assert provider.contexts == []


def test_retains_raw_evidence_context_and_actual_plan_snapshots(tmp_path):
    planner, state, *_ = setup(tmp_path)
    provider = RecordedProvider()
    result = api().discover(planner, state["session_id"], provider=provider, request=request(), actor="scientist", allow_network=True)
    record = api().load_discovery(planner, result, session_id=state["session_id"])
    assert result["status"] == "completed"
    assert result["before_planner_hash"] == result["after_planner_hash"]
    assert record["sources"][0]["access"] == "abstract"
    assert record["sources"][0]["full_text_inspected"] is False
    assert record["consumption"]["requests"] == 0
    raw = json.loads(planner.methods.artifact_path(record["provider_response_ref"]["artifact_id"]).read_text())
    assert raw["raw_response"]["fixture"] is True
    context = provider.contexts[0]
    assert set(context) == {"task", "dataset_summary", "sources"}
    assert "registry" not in str(context) and "availability" not in str(context)
    with pytest.raises(ValueError, match="binding"):
        api().load_discovery(planner, {**result, "after_planner_hash": "0" * 64})


@pytest.mark.parametrize("mode", ["unavailable", "failure", "fabricated_fulltext"])
def test_provider_failures_are_retained_blocking_records(tmp_path, mode):
    planner, state, *_ = setup(tmp_path)
    provider = None if mode == "unavailable" else RecordedProvider(fail=mode == "failure")
    if mode == "fabricated_fulltext":
        original = provider.search
        def fabricated(*args, **kwargs):
            response = original(*args, **kwargs)
            response["candidates"][0]["access"] = "full_text"
            return response
        provider.search = fabricated
    result = api().discover(planner, state["session_id"], provider=provider, request=request(), actor="scientist", allow_network=True)
    record = api().load_discovery(planner, result)
    assert record["status"] == "blocked" and record["failures"]
    assert record["sources"] == []
    if mode == "fabricated_fulltext":
        assert record["provider_response_ref"] is not None


def test_late_discovery_requires_new_cycle_before_calling_provider(tmp_path):
    planner, state, selector, *_ = setup(tmp_path)
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    provider = RecordedProvider()
    with pytest.raises(ValueError, match="new planning cycle|reassessment"):
        api().discover(planner, state["session_id"], provider=provider, request=request(), actor="scientist", allow_network=True)
    assert not provider.contexts


def test_acquisition_uses_existing_bounded_retrieval_and_retains_failure(tmp_path, monkeypatch):
    planner, state, *_ = setup(tmp_path)
    calls = []
    def unavailable(session_id, url, **kwargs):
        calls.append((session_id, url, kwargs))
        raise OSError("recorded unavailable primary source")
    monkeypatch.setattr(planner, "retrieve_evidence", unavailable)
    result = api().discover(planner, state["session_id"], provider=RecordedProvider(), request=request(acquire_indices=[0]), actor="scientist", allow_network=True)
    record = api().load_discovery(planner, result)
    assert result["status"] == "blocked"
    assert calls[0][1] == "https://example.org/method"
    assert record["failures"][0]["operation"] == "acquire"
    assert record["sources"][0]["access"] == "abstract"
    assert record["acquired_sources"] == []


def test_crossref_decoder_keeps_metadata_and_abstract_separate():
    body = {"status": "ok", "message-type": "work-list", "message": {"items": [
        {"DOI": "10.1000/one", "title": ["One"], "abstract": "<p>An abstract</p>"},
        {"DOI": "10.1000/two", "title": ["Two"]}]}}
    result = api().decode_crossref_response(body, max_results=2)
    assert [source["access"] for source in result] == ["abstract", "metadata"]
    assert all(source["kind"] == "primary_paper" for source in result)


def test_rejects_extra_request_fields_and_source_budget_before_dispatch(tmp_path):
    planner, state, *_ = setup(tmp_path)
    provider = RecordedProvider()
    with pytest.raises(ValueError, match="request"):
        api().discover(planner, state["session_id"], provider=provider, request=request(registry={}), actor="scientist", allow_network=True)
    with pytest.raises(ValueError, match="three|budget|3"):
        api().discover(planner, state["session_id"], provider=provider, request=request(acquire_indices=[0, 1, 2, 3]), actor="scientist", allow_network=True)
    assert not provider.contexts


def test_explicit_supplied_only_scope_records_no_search_without_network(tmp_path):
    planner, state, *_ = setup(tmp_path)
    provider = RecordedProvider()
    result = api().discover(planner, state["session_id"], provider=provider,
        request={"mode": "supplied_only", "reason": "Offline synthetic contract check; descriptive evidence only",
                 "query": None, "allowed_hosts": [], "acquire_indices": []}, actor="scientist")
    record = api().load_discovery(planner, result)
    assert record["status"] == "descriptive"
    assert record["provider"] is None and record["provider_response_ref"] is None
    assert record["consumption"]["requests"] == 0 and provider.contexts == []
    assert record["scope"] == "supplied_evidence_only_no_external_search_or_inferential_qualification"


def test_successful_acquisition_checks_bytes_and_only_changes_sources(tmp_path, monkeypatch):
    from devbench.analysis.methods import _digest
    import hashlib
    planner, state, *_ = setup(tmp_path)
    target = tmp_path / "source.txt"
    target.write_text("Recorded synthetic source bytes; not a real paper")
    artifact = planner.methods.import_artifact(target, artifact_type="file", actor="scientist")
    source = {"requested_url": "https://example.org/method", "final_url": "https://example.org/method",
        "artifact_id": artifact["artifact_id"], "content_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "excerpt": target.read_text(), "evidence_scope": "recorded_offline_fixture"}
    def acquire(session_id, url, **kwargs):
        before = planner.get(session_id)
        after = deepcopy(before)
        after["sources"].append(source)
        planner._save(before, after, actor="scientist", action="recorded_offline_fixture_source")
        return deepcopy(source)
    monkeypatch.setattr(planner, "retrieve_evidence", acquire)
    result = api().discover(planner, state["session_id"], provider=RecordedProvider(), request=request(acquire_indices=[0]), actor="scientist", allow_network=True)
    record = api().load_discovery(planner, result)
    assert result["status"] == "completed"
    assert result["before_planner_hash"] != result["after_planner_hash"]
    assert record["acquired_sources"][0]["full_text_inspected"] is False
    assert result["after_planner_hash"] == _digest(planner.get(state["session_id"]))
    retained = planner.methods.artifact_path(artifact["artifact_id"])
    retained.chmod(0o600)
    retained.write_text("altered source")
    with pytest.raises(ValueError, match="hash|mismatch"):
        api().load_discovery(planner, result)


@pytest.mark.parametrize("malformed", [False, True])
def test_real_provider_adapter_uses_bounded_retrieval_and_retains_raw_failures(tmp_path, monkeypatch, malformed):
    import base64
    import hashlib
    from urllib.parse import parse_qs, urlsplit
    planner, state, *_ = setup(tmp_path)
    discovery = api()
    wire = b"malformed recorded response" if malformed else json.dumps({"status": "ok", "message-type": "work-list",
        "message": {"items": [{"DOI": "10.1000/recorded", "title": ["Recorded fixture"]}]}}).encode()
    def offline_retrieval(url, output, **kwargs):
        assert urlsplit(url).hostname == "api.crossref.org"
        assert parse_qs(urlsplit(url).query) == {"query": ["RNA normalization alternatives"], "rows": ["3"]}
        assert kwargs == {"allowed_hosts": ["api.crossref.org"], "allow_network": True, "max_bytes": 1_000_000}
        output.write_bytes(wire)
        return {"content_sha256": hashlib.sha256(wire).hexdigest(), "requested_url": url, "final_url": url}
    monkeypatch.setattr(discovery, "retrieve_source", offline_retrieval)
    provider = discovery.CrossrefDiscoveryProvider(tmp_path / "provider")
    result = discovery.discover(planner, state["session_id"], provider=provider, request=request(), actor="scientist", allow_network=True)
    record = discovery.load_discovery(planner, result)
    assert result["status"] == ("blocked" if malformed else "completed")
    assert record["provider_response_ref"] is not None
    raw = json.loads(planner.methods.artifact_path(record["provider_response_ref"]["artifact_id"]).read_text())
    assert base64.b64decode(raw["raw_response"]["body_base64"]) == wire
    assert record["consumption"]["requests"] == 1


def test_empty_search_is_not_successful_evidence(tmp_path):
    planner, state, *_ = setup(tmp_path)
    provider = RecordedProvider()
    provider.search = lambda *args, **kwargs: {"raw_response": {"fixture": "no hits"}, "candidates": [], "consumption": {"requests": 0}}
    result = api().discover(planner, state["session_id"], provider=provider, request=request(), actor="scientist", allow_network=True)
    record = api().load_discovery(planner, result)
    assert record["status"] == "blocked" and record["failures"]


@pytest.mark.parametrize("unsafe_owner", [False, True])
def test_github_provider_searches_declared_owner_and_retains_unverified_leads(tmp_path, monkeypatch, unsafe_owner):
    from urllib.parse import parse_qs, urlsplit
    discovery = api()
    owner = "outsider" if unsafe_owner else "declared-author"
    wire = json.dumps({"total_count": 1, "incomplete_results": False, "items": [
        {"id": 123, "private": False, "owner": {"login": owner}, "name": "rna-normalization",
         "full_name": owner + "/rna-normalization", "html_url": "https://github.com/" + owner + "/rna-normalization",
         "description": "Synthetic recorded repository metadata"}]}).encode()
    calls = []
    def offline(url, output, **kwargs):
        calls.append(url)
        query = parse_qs(urlsplit(url).query)
        assert urlsplit(url).hostname == "api.github.com"
        assert "user:declared-author" in query["q"][0]
        assert query["per_page"] == ["3"]
        assert kwargs["allowed_hosts"] == ["api.github.com"] and kwargs["max_bytes"] == 1_000_000
        output.write_bytes(wire)
        return {"requested_url": url, "final_url": url}
    monkeypatch.setattr(discovery, "retrieve_source", offline)
    planner, state, *_ = setup(tmp_path)
    provider = discovery.GitHubRepositoryDiscoveryProvider(tmp_path / "provider", owners=["declared-author"])
    result = discovery.discover(planner, state["session_id"], provider=provider, request=request(), actor="scientist", allow_network=True)
    record = discovery.load_discovery(planner, result)
    assert len(calls) == 1
    assert record["status"] == ("blocked" if unsafe_owner else "completed")
    assert record["provider_response_ref"] is not None
    if not unsafe_owner:
        assert record["sources"][0]["kind"] == "author_repository"
        assert record["sources"][0]["access"] == "metadata"
        assert "not_author_verified" in str(record["sources"][0]["identity"])


@pytest.mark.parametrize("index_format", ["html", "text"])
def test_official_documentation_searches_only_declared_index_metadata(tmp_path, monkeypatch, index_format):
    discovery = api()
    wire = (b'<html><a href="/rna-normalization">RNA normalization alternatives</a><a href="https://other.invalid/rna-normalization">RNA normalization alternatives</a></html>'
        if index_format == "html" else b"https://docs.example.org/rna-normalization-alternatives\n")
    def offline(url, output, **kwargs):
        assert url == "https://docs.example.org/index"
        assert kwargs["allowed_hosts"] == ["docs.example.org"]
        output.write_bytes(wire)
        return {"requested_url": url, "final_url": url}
    monkeypatch.setattr(discovery, "retrieve_source", offline)
    planner, state, *_ = setup(tmp_path)
    provider = discovery.OfficialDocumentationDiscoveryProvider(tmp_path / "provider", index_url="https://docs.example.org/index",
        official_hosts=["docs.example.org"], index_format=index_format)
    result = discovery.discover(planner, state["session_id"], provider=provider, request=request(), actor="scientist", allow_network=True)
    record = discovery.load_discovery(planner, result)
    assert record["status"] == "completed"
    assert len(record["sources"]) == 1
    source = record["sources"][0]
    assert source["kind"] == "official_documentation" and source["access"] == "metadata"
    assert source["full_text_inspected"] is False
    assert source["identity"]["official_status"] == "coordinator_declared_not_independently_authenticated"


def test_documentation_xml_and_unsafe_configs_fail_closed(tmp_path):
    discovery = api()
    with pytest.raises(ValueError, match="format"):
        discovery.OfficialDocumentationDiscoveryProvider(tmp_path, index_url="https://docs.example.org/sitemap.xml",
            official_hosts=["docs.example.org"], index_format="xml")
    with pytest.raises((ValueError, PermissionError)):
        discovery.OfficialDocumentationDiscoveryProvider(tmp_path, index_url="https://other.invalid/index", official_hosts=["docs.example.org"])
    with pytest.raises(ValueError, match="owner"):
        discovery.GitHubRepositoryDiscoveryProvider(tmp_path, owners=["author user:outsider"])


def test_documentation_xml_wire_failure_is_retained(tmp_path, monkeypatch):
    discovery = api()
    wire = b'<?xml version="1.0"?><!DOCTYPE urlset [<!ENTITY secret SYSTEM "file:///etc/passwd">]><urlset>&secret;</urlset>'
    def offline(url, output, **kwargs):
        output.write_bytes(wire)
        return {"requested_url": url, "final_url": url}
    monkeypatch.setattr(discovery, "retrieve_source", offline)
    planner, state, *_ = setup(tmp_path)
    provider = discovery.OfficialDocumentationDiscoveryProvider(tmp_path / "provider", index_url="https://docs.example.org/index",
        official_hosts=["docs.example.org"])
    result = discovery.discover(planner, state["session_id"], provider=provider, request=request(), actor="scientist", allow_network=True)
    record = discovery.load_discovery(planner, result)
    assert result["status"] == "blocked" and record["provider_response_ref"] is not None
    assert "XML" in record["failures"][0]["message"]


def test_provider_network_failure_retains_attempt_without_fabricated_bytes(tmp_path, monkeypatch):
    discovery = api()
    def offline(*args, **kwargs):
        raise TimeoutError("Recorded network timeout; no real request")
    monkeypatch.setattr(discovery, "retrieve_source", offline)
    planner, state, *_ = setup(tmp_path)
    provider = discovery.CrossrefDiscoveryProvider(tmp_path / "provider")
    result = discovery.discover(planner, state["session_id"], provider=provider, request=request(), actor="scientist", allow_network=True)
    record = discovery.load_discovery(planner, result)
    assert record["status"] == "blocked" and record["provider_response_ref"] is not None
    assert record["consumption"]["retrieval_attempts"] == 1
    assert "response_bytes" not in record["consumption"]


def test_repository_acquisition_cannot_leak_implementation_into_scientific_sources(tmp_path, monkeypatch):
    planner, state, *_ = setup(tmp_path)
    provider = RecordedProvider()
    response = provider.search({}, {}, allow_network=True)
    response["candidates"][0].update(kind="author_repository", access="metadata",
        identity={"repository": "declared-author/rna-method"}, url="https://github.com/declared-author/rna-method")
    provider.search = lambda *args, **kwargs: deepcopy(response)
    def forbidden(*args, **kwargs):
        pytest.fail("Repository bytes must not enter scientific plan.sources")
    monkeypatch.setattr(planner, "retrieve_evidence", forbidden)
    result = api().discover(planner, state["session_id"], provider=provider,
        request=request(allowed_hosts=["github.com"], acquire_indices=[0]), actor="scientist", allow_network=True)
    record = api().load_discovery(planner, result)
    assert record["status"] == "blocked"
    assert record["sources"][0]["kind"] == "author_repository"
    assert record["provider_response_ref"] is not None
    assert record["acquired_sources"] == []
    assert planner.get(state["session_id"])["sources"] == []
    assert record["failures"][0]["kind"] == "ImplementationEvidenceBoundary"
