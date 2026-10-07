"""Protected integration contracts; offline tests never qualify native controls."""
import importlib.util
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
from tests.test_analysis_episodes import workspace


def backend_api():
    assert importlib.util.find_spec("devbench.analysis.rna_investigation_backend") is not None, \
        "Audited investigation backend is missing"
    from devbench.analysis.rna_investigation_backend import (
        AuditedInvestigationBackend, BoundedReadFacade, InvestigationExecutionAuthority)
    return AuditedInvestigationBackend, BoundedReadFacade, InvestigationExecutionAuthority


def native_backend(tmp_path):
    from devbench.analysis.methods import MethodService
    from devbench.analysis.episode_codex import CodexActionSelector
    Backend, _, _ = backend_api()
    private = tmp_path / "private"
    methods = MethodService(private)
    selector = CodexActionSelector(requested_model="fixture-model",
        workspace_parent=tmp_path / "actors", private_root=private,
        executable=str(tmp_path / "missing-cli"), memory_mb=256)
    return Backend(methods, selector)


def test_native_capabilities_never_claim_total_cap_or_parallel_qualification(tmp_path):
    backend = native_backend(tmp_path)
    record = backend.capabilities()
    assert record["backend"] == "codex-cli"
    assert record["enforced_total_token_cap"] is False
    assert record["usage_capture"] == "posthoc_optional"
    assert record["parallel_boundary_qualified"] is False
    assert record["direct_read_tools"] is False
    assert record["packet_only_reasoning"] is True
    assert not (tmp_path / "actors").exists()


def test_source_digest_binds_module_names_to_actual_origins(tmp_path,monkeypatch):
    from devbench.analysis import workflow_actions,workflow_state
    backend=native_backend(tmp_path)
    before=backend.capabilities()
    first,second=workflow_actions.__file__,workflow_state.__file__
    monkeypatch.setattr(workflow_actions,"__file__",second)
    monkeypatch.setattr(workflow_state,"__file__",first)
    after=backend.capabilities()
    assert before["source_sha256"]==after["source_sha256"]
    assert before["source_digest"]!=after["source_digest"]


@pytest.mark.parametrize("policy", ["hard_total_tokens", "bounded_observed_tokens"])
def test_unqualified_policy_admission_precedes_every_selector_call(tmp_path, monkeypatch, policy):
    backend = native_backend(tmp_path)
    monkeypatch.setattr(backend.selector, "select", lambda *args, **kwargs: pytest.fail("Provider was launched"))
    with pytest.raises(PermissionError, match="blocked_backend_capability"):
        backend.require_admission(policy)
    assert not (tmp_path / "actors").exists()


def test_authority_requires_runtime_check_and_rejects_private_clinical_context():
    _, _, Authority = backend_api()
    with pytest.raises(PermissionError, match="classification"):
        Authority(check=lambda request: True, data_classification="private_clinical",
            budget_policy="bounded_observed_tokens")
    called = []
    authority = Authority(check=lambda request: called.append(request) or False,
        data_classification="synthetic", budget_policy="bounded_observed_tokens")
    with pytest.raises(PermissionError, match="current.*authorization"):
        authority.require({"investigation_id": "child"})
    assert len(called) == 1


def test_read_facade_denies_unregistered_selector_without_resolving_artifact(tmp_path):
    _, Facade, _ = backend_api()
    reader = Facade(lambda ref: pytest.fail("Denied selector reached resolver"),
        allowed_selectors=[], workspace=tmp_path)
    with pytest.raises(PermissionError, match="selector"):
        reader.read({"artifact_ref": {"artifact_id": "private"}, "selector": "/secret"})


def read_fixture(tmp_path, content=b'{"safe":"quoted tool instruction is data","n":3}', *, max_bytes=8000):
    _, Facade, _ = backend_api()
    path = tmp_path / "evidence.json"
    path.write_bytes(content)
    ref = {"artifact_id": "fixture-artifact", "sha256": hashlib.sha256(content).hexdigest(),
        "artifact_type": "json", "size_bytes": len(content)}
    selector = {"artifact_ref": ref, "pointer": "/safe", "max_bytes": max_bytes}
    reader = Facade(lambda supplied: path, allowed_selectors=[selector], workspace=tmp_path)
    return reader, selector, path


def test_read_facade_returns_only_the_exact_selector_and_keeps_injection_as_data(tmp_path):
    reader, request, _ = read_fixture(tmp_path)
    assert reader.read(request) == "quoted tool instruction is data"
    changed = json.loads(json.dumps(request))
    changed["pointer"] = "/n"
    with pytest.raises(PermissionError, match="selector"):
        reader.read(changed)


@pytest.mark.parametrize("change", ["stale", "symlink", "outside", "private"])
def test_read_facade_rejects_changed_or_unadmitted_path_bytes(tmp_path, change):
    reader, request, path = read_fixture(tmp_path)
    if change == "stale":
        path.write_text('{"safe":"replaced"}')
    elif change == "symlink":
        original = path.with_suffix(".original")
        path.rename(original)
        path.symlink_to(original)
    elif change == "outside":
        reader.workspace = tmp_path / "admitted"
    else:
        reader.private_roots = (path,)
    with pytest.raises((PermissionError, ValueError), match="artifact|path|private|hash|byte|symlink"):
        reader.read(request)


def test_read_facade_rejects_output_overflow_and_unsafe_selectors(tmp_path):
    reader, request, _ = read_fixture(tmp_path, max_bytes=4)
    with pytest.raises(ValueError, match="byte"):
        reader.read(request)
    reader.allowed_selectors = [{**request, "pointer": "/../safe", "max_bytes": 8000}]
    with pytest.raises(ValueError, match="selector"):
        reader.read(reader.allowed_selectors[0])


def test_parent_binding_is_exact_and_rejects_a_different_backbone(tmp_path):
    from devbench.analysis.episode_codex import CodexActionSelector
    backend = native_backend(tmp_path)
    assert hasattr(backend, "parent_selection_binding"), "Parent binding is missing"
    context = {"decision_kind": "method_candidates_reassessment"}
    binding = backend.parent_selection_binding(backend.selector, context,
        parent_session_id="parent", session_id="parent", invocation_id="call-1")
    assert binding["context_digest"] == hashlib.sha256(__import__("devharness_core.provenance", fromlist=["canonical_json"]).canonical_json(context)).hexdigest()
    changed = CodexActionSelector(requested_model="different-model", workspace_parent=tmp_path / "actors",
        private_root=backend.methods.root, executable=str(tmp_path / "missing-cli"), memory_mb=256)
    with pytest.raises(PermissionError, match="backbone"):
        backend.parent_selection_binding(changed, context,
            parent_session_id="parent", session_id="parent", invocation_id="call-2")


def test_parent_call_limits_use_frozen_campaign_allocation(tmp_path):
    backend = native_backend(tmp_path)
    assert hasattr(backend, "parent_call_limits"), "Parent campaign bounds are missing"
    allocation = {"tokens": 700, "calls": 2, "seconds": 30, "context_bytes": 128000, "memory_mb": 256}
    campaign = {"identity": {"parent_call_limits": {"default": allocation}}}
    limits = {"session_timeout_seconds": 10, "memory_mb": 256, "max_context_bytes": 64000}
    assert backend.parent_call_limits({"decision_kind": "method_recommendation"}, campaign, limits) == {
        **allocation, "seconds": 10, "context_bytes": 64000}


def test_reconcile_capture_rejects_forged_reference_with_zero_model_calls(tmp_path, monkeypatch):
    backend = native_backend(tmp_path)
    assert hasattr(backend, "reconcile_capture"), "Read-only capture recovery is missing"
    monkeypatch.setattr(backend.selector, "select", lambda *args, **kwargs: pytest.fail("Recovery dispatched"))
    with pytest.raises((ValueError, PermissionError)):
        backend.reconcile_capture({"artifact_id": "forged", "sha256": "a" * 64,
            "artifact_type": "json", "size_bytes": 10})


def prepared_pair(tmp_path):
    from tests.test_rna_investigation_contract import fixture
    from devbench.analysis.rna_investigation_contract import build_investigation_packet
    backend = native_backend(tmp_path)
    capabilities = backend.capabilities()
    plan, evidence, policy = fixture()
    policy.update(source_digest=capabilities["source_digest"], runtime_digest=capabilities["runtime_digest"])
    refs = []
    for identity in ("2" * 32, "6" * 32):
        evidence["investigation_id"] = identity
        packet = build_investigation_packet(plan, evidence, stage="pre_fit_methods",
            task="independent_method_investigation", policy=policy)
        refs.append(backend.retain_packet(packet))
    prepared = backend.prepare_requests(refs, budget_policy="bounded_observed_tokens",
                                       reservation_refs=["model:child-one", "model:child-two"])
    return backend, prepared


def test_preparation_allocates_all_sibling_roots_and_exact_independent_contexts(tmp_path):
    backend, prepared = prepared_pair(tmp_path)
    assert len(prepared) == 2
    ids = {item["request"]["investigation_id"] for item in prepared}
    assert len({item["binding"]["session_id"] for item in prepared}) == 2
    assert len({item["binding"]["invocation_id"] for item in prepared}) == 2
    for item in prepared:
        descriptor = backend.read_dispatch(item["invocation_ref"])
        assert set(descriptor["child_roots"]) == ids
        assert all(backend._child_root(child).is_dir() for child in ids)
        packet = backend.read_packet(item["request"]["packet_ref"])
        assert "Do not expose" not in json.dumps(packet)
        assert descriptor["request"] == item["request"]


def test_unlaunched_prepared_invocation_recovery_is_read_only_and_uncertain(tmp_path, monkeypatch):
    backend, prepared = prepared_pair(tmp_path)
    before = sorted(str(path) for path in tmp_path.rglob("*"))
    monkeypatch.setattr(backend.selector, "select", lambda *args, **kwargs: pytest.fail("Replay dispatched"))
    for item in prepared:
        assert backend.reconcile_capture(item["invocation_ref"])["status"] == "uncertain"
    assert sorted(str(path) for path in tmp_path.rglob("*")) == before


def test_changed_prepared_packet_and_forged_terminal_completion_are_rejected(tmp_path):
    backend, prepared = prepared_pair(tmp_path)
    item = prepared[0]
    output = backend._output(item["binding"]["invocation_id"])
    output.mkdir(parents=True)
    (output / "completion.json").write_text(json.dumps({"status": "completed", "invocation_ref": item["invocation_ref"],
        "result_ref": None, "capture_ref": None, "usage_ref": None, "error": None}))
    with pytest.raises(ValueError, match="lacks a result"):
        backend.reconcile_capture(item["invocation_ref"])
    path = backend.methods.artifact_path(item["request"]["packet_ref"]["artifact_id"])
    path.chmod(0o600)
    path.write_text('{}')
    with pytest.raises(ValueError, match="hash|binding"):
        backend.read_dispatch(item["invocation_ref"])


def test_changed_request_at_actual_launch_is_rejected_before_selector(workspace, monkeypatch):
    backend,prepared,reservations,authority=protected_fixture(workspace,monkeypatch)
    from devbench.analysis.episode_codex import CodexActionSelector
    monkeypatch.setattr(CodexActionSelector,"select",lambda *args,**kwargs:pytest.fail("Changed request launched"))
    calls=[]
    request=prepared[0]["request"]
    def permission_check(value):
        calls.append(value)
        if len(calls)==2:
            request["stage"]="post_fit_explanation"
        return True
    authority=type(authority)(check=permission_check,data_classification="synthetic",budget_policy="bounded_observed_tokens")
    with pytest.raises(ValueError,match="request changed"):
        backend.investigate(request,execution_authority=authority,reservation=reservations[0])


def test_current_absolute_campaign_deadline_blocks_actual_launch(workspace, monkeypatch):
    backend,prepared,reservations,authority=protected_fixture(workspace,monkeypatch)
    from devbench.analysis.episode_codex import CodexActionSelector
    from devbench.analysis import workflow_operations
    monkeypatch.setattr(CodexActionSelector,"select",lambda *args,**kwargs:pytest.fail("Expired call launched"))
    deadline=backend.ledger.model_call_deadline(reservations[0])
    monkeypatch.setattr(workflow_operations.time,"time",lambda:deadline+1)
    with pytest.raises(PermissionError,match="deadline exhausted"):
        backend.investigate(prepared[0]["request"],execution_authority=authority,reservation=reservations[0])


def protected_fixture(workspace, monkeypatch, *, malformed=False, unattributed=False, invalid_preflight=False):
    """Installed synthetic CLI exercises real process/capture, never native proof."""
    from tests.test_rna_investigation_contract import result, packet, fixture
    from tests.test_rna_investigation_budget import _policy
    from devbench.analysis.methods import MethodService
    from devbench.analysis.episode_codex import CodexActionSelector
    from devbench.analysis.workflow_operations import WorkflowOperationLedger, model_reservation_ref
    from devbench.analysis.rna_investigation_contract import build_investigation_packet
    from devbench import staging
    from devbench.study import execution
    Backend, _, Authority = backend_api()
    fake = workspace / "offline-codex"
    template = result(packet(), transported=False)
    if unattributed:
        template["proposals"]["candidates"][0]["parameter_candidates"][0]["origin"]="user"
    fake.write_text(f'''#!{sys.executable}
import ast,json,shlex,sys,tomllib,uuid
from pathlib import Path
from urllib.request import Request,urlopen
args=sys.argv[1:]
if args==['--version']:
 print('codex-cli 0.0.0');raise SystemExit(0)
if args[:2]==['login','status']:
 print('Logged in using ChatGPT (offline fixture)');raise SystemExit(0)
if args and args[0]=='sandbox':
 print(json.dumps({{'private_read_denied':True,'protected_write_denied':True,'workspace_write_allowed':True}}));raise SystemExit(0)
if not args or args[0]!='exec':raise SystemExit(2)
prompt=sys.stdin.read();sid='offline-'+uuid.uuid4().hex
events=[{{'type':'thread.started','thread_id':sid}},{{'type':'turn.started'}}]
if 'unscored permission preflight' in prompt:
 invocation=prompt.splitlines()[-1];python,flag,script=shlex.split(invocation)
 config=ast.literal_eval(Path(script).read_text().splitlines()[0].removeprefix('CONFIG = '))
 checks={{name:True for name in ['private_read_denied','sibling_read_denied','protected_write_denied','workspace_write_allowed','unassigned_socket_denied','tcp_denied','isolated_python','child_environment_matches','tool_endpoint_absent']}}
 report={{'protocol':config['protocol'],'nonce':config['nonce'],'checks':checks,'errors':{{}}}}
 Path(config['workspace_canary']).write_bytes(b'probe-write')
 events.extend([{{'type':'item.started','item':{{'type':'command_execution','id':'probe','command':invocation,'status':'in_progress'}}}},{{'type':'item.completed','item':{{'type':'command_execution','id':'probe','command':invocation,'status':'completed','exit_code':0,'aggregated_output':json.dumps(report)}}}}])
 answer='Synthetic preflight text'
else:
 body=prompt.split('Task context:\\n',1)[1]
 context=json.JSONDecoder().raw_decode(body)[0];p=context['packet'];answer={template!r}
 answer.update(investigation_id=p['investigation_id'],parent_session_id=p['parent_session_id'],packet_sha256=context['packet_sha256'],stage=p['stage'])
 answer['data_assessment']['evidence_refs']=p['evidence_refs'];answer['requirements_assessment']['evidence_refs']=p['evidence_refs']
 answer=json.dumps(answer)
Path(args[args.index('--output-last-message')+1]).write_text(answer)
events.append({{'type':'turn.completed','model':'fixture-model','usage':{{'input_tokens':100,'output_tokens':30,'cached_input_tokens':80,'reasoning_tokens':20}}}})
for row in events:print(json.dumps(row),flush=True)
configurations=[tomllib.loads(args[i+1]) for i,arg in enumerate(args) if arg=='-c' and args[i+1].startswith('otel=')]
if configurations:
 otel=configurations[0]['otel']['exporter']['otlp-http']
 attrs={{'conversation.id':sid,'model':'fixture-model','event.name':'codex.sse_event','event.kind':'response.completed','input_token_count':'100','output_token_count':'30'}}
 packet={{'resourceLogs':[{{'scopeLogs':[{{'logRecords':[{{'attributes':[{{'key':key,'value':{{'stringValue':value}}}} for key,value in attrs.items()]}}]}}]}}]}}
 urlopen(Request(otel['endpoint'],data=json.dumps(packet).encode(),headers={{'Content-Type':'application/json',**otel['headers']}}),timeout=2).close()
''')
    if malformed:
        fake.write_text(fake.read_text().replace('answer=json.dumps(answer)', 'answer="{partial"'))
    if invalid_preflight:
        invalid_value={"too_many":"200","null":"None","float":"1.5"}.get(invalid_preflight,"200")
        fake.write_text(fake.read_text().replace("'cached_input_tokens':80", "'cached_input_tokens':"+invalid_value+" if 'unscored permission preflight' in prompt else 80"))
    fake.chmod(0o700)
    private = workspace / "private"
    methods = MethodService(private)
    short_actors=tempfile.TemporaryDirectory(prefix=".i",dir=Path(__file__).resolve().parents[1])
    selector = CodexActionSelector(requested_model="fixture-model", workspace_parent=Path(short_actors.name),
        private_root=private, executable=str(fake), memory_mb=256)
    ledger = WorkflowOperationLedger(private / "workflow.sqlite")
    backend = Backend(methods, selector, ledger=ledger)
    backend._fixture_workspace=short_actors
    source_capability = backend.capabilities
    def offline_capability():
        value = source_capability()
        value.update(scope="offline", qualification="qualified", capability_status="offline_qualified",
            usage_capture="verified_complete", verified_usage_capture=True,
            parallel_boundary_qualified=True, all_attributable_requests_accounted=True)
        value["capability_digest"] = hashlib.sha256(__import__("devharness_core.provenance", fromlist=["canonical_json"]).canonical_json({key:item for key,item in value.items() if key!='capability_digest'})).hexdigest()
        return value
    monkeypatch.setattr(backend, "capabilities", offline_capability)
    def stage(path, *, assisted):
        assert assisted is False
        (path / ".venv/bin").mkdir(parents=True)
        (path / ".venv/bin/python").symlink_to(sys.executable)
        return {"python": str(path / ".venv/bin/python"), "runtime_source_hashes": {}, "linked_dependency_entries": []}
    monkeypatch.setattr(staging, "stage_python", stage)
    monkeypatch.setattr(execution, "_runtime_roots", lambda *args: ())
    policy = _policy("bounded_observed_tokens", soft_token_target=1000, max_context_bytes=128000, memory_mb=256)
    plan, evidence, packet_policy = fixture()
    campaign = ledger.open_campaign("offline-campaign", session_id=plan["session_id"], policy=policy,
        capability=backend.capabilities(), identity={"purpose": "offline_cli_capture_only"})
    packet_policy.update(policy_digest=campaign["policy_digest"], source_digest=backend.capabilities()["source_digest"],
                         runtime_digest=backend.capabilities()["runtime_digest"])
    refs = []
    for identity in ("2" * 32, "6" * 32):
        evidence["investigation_id"] = identity
        p = build_investigation_packet(plan, evidence, stage="pre_fit_methods", task="independent_method_investigation", policy=packet_policy)
        refs.append(backend.retain_packet(p))
    prepared = backend.prepare_requests(refs, budget_policy="bounded_observed_tokens",
        reservation_refs=[model_reservation_ref("offline-campaign", "child-"+str(number),0) for number in (1,2)])
    ledger.reserve_investigation_fork("offline-campaign",fork_id="offline-fork",stage="pre_fit_methods",
        child_ids=[item["request"]["investigation_id"] for item in prepared],identity={"scope":"offline_fixture"})
    reservations=[]
    for number,item in enumerate(prepared,1):
        reservation=ledger.reserve_model_call("offline-campaign","child-"+str(number),0,item["binding"],
            limits={"tokens":300,"calls":2,"seconds":15,"context_bytes":128000,"memory_mb":256},purpose="child_investigation",child_id=item["request"]["investigation_id"])
        reservations.append(ledger.activate_model_call(reservation))
    authority=Authority(check=lambda request:True,data_classification="synthetic",budget_policy="bounded_observed_tokens")
    return backend,prepared,reservations,authority


@pytest.mark.skipif(os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Local capture fixture requires opt-in")
def test_installed_offline_cli_proves_private_packet_sessions_and_capture_bound_usage(workspace, monkeypatch):
    from devbench.analysis.episode_codex import CodexActionSelector
    selectors=[]
    original_select=CodexActionSelector.select
    def observe_selector(self,*args,**kwargs):
        selectors.append(self)
        return original_select(self,*args,**kwargs)
    monkeypatch.setattr(CodexActionSelector,"select",observe_selector)
    backend,prepared,reservations,authority=protected_fixture(workspace,monkeypatch)
    outcomes=[]
    for item,reservation in zip(prepared,reservations):
        outcome=backend.investigate(item["request"],execution_authority=authority,reservation=reservation)
        if outcome["status"] != "completed":
            path=backend._output(item["binding"]["invocation_id"]) / "exec-preflight/evidence.json"
            errors=json.loads(path.read_text()).get("validation_errors") if path.is_file() else None
            pytest.fail(str({"error":outcome["error"],"preflight_errors":errors}))
        receipt=backend.verify_capture(outcome["capture_ref"],reservation)
        manifest=backend._document(outcome["capture_ref"],artifact_type="rna_investigation_capture")
        metadata=json.loads(backend._artifact(manifest["files"]["model/metadata.json"],artifact_type="file",max_bytes=4_000_000))
        assert metadata["execution_role"]=="rna_investigation_selector"
        assert metadata["tool_evidence_source"]=="no_scientific_execution_during_investigation"
        assert receipt["actual_calls"] == 2
        assert receipt["usage"]["normalized_total_tokens"] == 260
        assert receipt["usage"]["cached_input_tokens"] == 160
        assert receipt["usage"]["reasoning_tokens"] == 40
        backend.ledger.settle_model_call(reservation,capture=outcome["capture_ref"],verify_capture=backend.verify_capture)
        result=backend.verify_child_result(item["request"]["packet_ref"],outcome["result_ref"])
        assert result["capture_ref"] == outcome["capture_ref"]
        assert "Do not expose" not in backend._capture_action(outcome["capture_ref"]).__repr__()
        outcomes.append((outcome,receipt))
    assert len(selectors)==2 and selectors[0] is not selectors[1]
    assert all(selector is not backend.selector for selector in selectors)
    assert selectors[0].workspace_parent != selectors[1].workspace_parent
    assert selectors[1].workspace_parent in selectors[0].denied_roots
    assert selectors[0].workspace_parent in selectors[1].denied_roots
    assert set(outcomes[0][1]["native_session_ids"]).isdisjoint(outcomes[1][1]["native_session_ids"])
    for item,(outcome,_) in zip(prepared,outcomes):
        assert backend.reconcile_capture(item["invocation_ref"]) == outcome
    with pytest.raises(ValueError,match="different exact reservation"):
        backend.verify_capture(outcomes[0][0]["capture_ref"],reservations[1])
    first=prepared[0]
    marker=backend._output(first["binding"]["invocation_id"]) / "completion.json"
    marker.unlink()
    before=sorted(str(path) for path in backend.methods.root.rglob("*"))
    recovered=backend.reconcile_capture(first["invocation_ref"])
    assert recovered["status"]=="incomplete"
    assert recovered["capture_ref"]==outcomes[0][0]["capture_ref"]
    assert len(selectors)==2
    assert sorted(str(path) for path in backend.methods.root.rglob("*"))==before
    assert backend.capabilities()["scope"] == "offline"


@pytest.mark.skipif(os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Local capture fixture requires opt-in")
def test_malformed_response_preserves_actual_failed_capture_usage_and_raw_bytes(workspace, monkeypatch):
    backend,prepared,reservations,authority=protected_fixture(workspace,monkeypatch,malformed=True)
    outcome=backend.investigate(prepared[0]["request"],execution_authority=authority,reservation=reservations[0])
    assert outcome["status"]=="incomplete"
    assert outcome["capture_ref"] is not None and outcome["usage_ref"] is not None
    assert outcome["result_ref"] is None
    receipt=backend.verify_capture(outcome["capture_ref"],reservations[0])
    assert receipt["status"]=="failed" and receipt["usage"]["normalized_total_tokens"]==260
    manifest=backend._document(outcome["capture_ref"],artifact_type="rna_investigation_capture")
    assert backend._artifact(manifest["files"]["model/answer.json"])==b"{partial"
    backend.ledger.settle_model_call(reservations[0],capture=outcome["capture_ref"],verify_capture=backend.verify_capture)
    assert backend.ledger.get_model_reservation(reservations[0])["status"]=="failed"
    assert backend.reconcile_capture(prepared[0]["invocation_ref"])==outcome


@pytest.mark.skipif(os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Local capture fixture requires opt-in")
def test_invalid_scientific_proposal_retains_known_transport_capture_and_usage(workspace, monkeypatch):
    backend,prepared,reservations,authority=protected_fixture(workspace,monkeypatch,unattributed=True)
    outcome=backend.investigate(prepared[0]["request"],execution_authority=authority,reservation=reservations[0])
    assert outcome["status"]=="incomplete"
    assert outcome["capture_ref"] is not None and outcome["usage_ref"] is not None
    assert outcome["result_ref"] is None and outcome["error"] is not None
    receipt=backend.verify_capture(outcome["capture_ref"],reservations[0])
    assert receipt["usage"]["normalized_total_tokens"]==260
    assert backend._capture_action(outcome["capture_ref"])["proposals"]["candidates"][0]["parameter_candidates"][0]["origin"]=="user"


@pytest.mark.skipif(os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Local capture fixture requires opt-in")
@pytest.mark.parametrize("invalid_kind",["too_many","null","float"])
def test_unknown_preflight_usage_stops_action_and_retains_positive_primary_floor(workspace, monkeypatch, invalid_kind):
    backend,prepared,reservations,authority=protected_fixture(workspace,monkeypatch,invalid_preflight=invalid_kind)
    outcome=backend.investigate(prepared[0]["request"],execution_authority=authority,reservation=reservations[0])
    if outcome["capture_ref"] is None:
        errors=[]
        with backend.methods._db() as database:
            rows=database.execute("SELECT payload FROM records WHERE kind='artifact' AND json_extract(payload,'$.artifact_type')='rna_investigation_capture' LIMIT 2").fetchall()
        for row in rows:
            record=json.loads(row[0]);ref={key:record[key] for key in ("artifact_id","sha256","artifact_type","size_bytes")}
            try:backend.verify_capture(ref,reservations[0])
            except Exception as error:errors.append(type(error).__name__+': '+str(error)[:500])
        pytest.fail(str({"provider_error":outcome["error"],"capture_errors":errors}))
    assert outcome["status"]=="incomplete" and outcome["capture_ref"] is not None, outcome["error"]
    receipt=backend.verify_capture(outcome["capture_ref"],reservations[0])
    assert receipt["actual_calls"]==1 and len(receipt["native_session_ids"])==1
    assert receipt["usage"]["normalized_total_tokens"] is None
    assert receipt["usage"]["native_usage"]["observed_token_lower_bound"]==130
    backend.ledger.settle_model_call(reservations[0],capture=outcome["capture_ref"],verify_capture=backend.verify_capture)
    assert backend.ledger.get_model_reservation(reservations[0])["status"]=="uncertain"
    assert backend.ledger.model_call_remaining(reservations[1])==0


@pytest.mark.skipif(os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Local capture fixture requires opt-in")
@pytest.mark.parametrize("transition",["uncertain","cancelled"])
def test_shared_admission_revocation_during_preflight_prevents_action(workspace,monkeypatch,transition):
    backend,prepared,reservations,authority=protected_fixture(workspace,monkeypatch)
    output=backend._output(prepared[0]["binding"]["invocation_id"])
    observed={}
    def revoke():
        end=time.monotonic()+10
        checkpoint=output/"exec-preflight/dispatch_checkpoint.json"
        while time.monotonic()<end and not checkpoint.is_file():time.sleep(.001)
        observed["preflight_seen"]=checkpoint.is_file()
        observed["action_before_revocation"]=(output/"selection-dispatch.json").exists()
        if transition=="uncertain":
            backend.ledger.mark_model_call_uncertain(reservations[1],error_type="FixtureUnknownSiblingSpend")
        else:
            backend.ledger.cancel_campaign(reservations[0].campaign_id)
        observed["remaining"]=backend.ledger.model_call_remaining(reservations[0])
    worker=threading.Thread(target=revoke)
    worker.start()
    outcome=backend.investigate(prepared[0]["request"],execution_authority=authority,reservation=reservations[0])
    worker.join(timeout=12)
    assert not worker.is_alive() and observed["preflight_seen"] and not observed["action_before_revocation"]
    assert observed["remaining"]==0
    assert not (output/"selection-dispatch.json").exists()
    assert outcome["status"]=="incomplete" and outcome["capture_ref"] is not None, outcome["error"]
    receipt=backend.verify_capture(outcome["capture_ref"],reservations[0])
    assert receipt["actual_calls"]==1 and len(receipt["native_session_ids"])==1
    assert receipt["usage"]["normalized_total_tokens"]==130
    assert "ledger_path" not in json.dumps(backend.read_packet(prepared[0]["request"]["packet_ref"]))


def protected_parent_fixture(workspace,monkeypatch,invalid_phase=None,invalid_value=None):
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.methods import _digest
    from tests.test_rna_investigation_budget import _policy
    backend,_,_,_=protected_fixture(workspace,monkeypatch)
    backend.ledger.cancel_campaign("offline-campaign")
    executable=Path(backend.selector.executable)
    script=executable.read_text()
    start=script.index("else:\n body=prompt.split")
    end=script.index("\nPath(args[args.index('--output-last-message')",start)
    script=script[:start]+"else:\n answer=json.dumps({'candidates':[]})"+script[end:]
    if invalid_phase is not None:
        expression=repr(invalid_value)+( " if 'unscored permission preflight' in prompt else 80"
            if invalid_phase=="preflight" else " if 'unscored permission preflight' not in prompt else 80")
        script=script.replace("'cached_input_tokens':80","'cached_input_tokens':"+expression)
    executable.write_text(script)
    planner=ScientificMethodPlanner(backend.methods)
    state=planner.create({"scientific_objective":"Verify metered parent transport accounting",
        "computational_task":"Offline software capture qualification"},
        {"observed":{},"inferred":{},"unknowns":[],"artifact_ids":[]},actor="offline-fixture",
        data_classification="synthetic",evaluation_purpose="execution_verification")
    session=state["session_id"]
    capability=backend.capabilities()
    allocation={"tokens":300,"calls":2,"seconds":15,"context_bytes":128000,"memory_mb":256}
    backend.ledger.open_campaign(session,session_id=session,
        policy=_policy("bounded_observed_tokens",soft_token_target=1000,max_context_bytes=128000,memory_mb=256),
        capability=capability,identity={"expected_plan_hash":_digest(state),
            "source_digest":capability["source_digest"],"runtime_digest":capability["runtime_digest"],
            "parent_call_limits":{"default":allocation}})
    planner.attach_model_campaign(session,backend.ledger,session,backend,expected_plan_hash=_digest(state),actor="offline-fixture")
    return backend,planner,session


@pytest.mark.skipif(os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Local capture fixture requires opt-in")
@pytest.mark.parametrize("phase,invalid",[("preflight",None),("preflight",200),("action",1.5)])
def test_attached_parent_invalid_usage_stays_unknown_and_cannot_accept_science(workspace,monkeypatch,phase,invalid):
    backend,planner,session=protected_parent_fixture(workspace,monkeypatch,phase,invalid)
    with pytest.raises(Exception):
        planner.generate_candidates(session,backend.selector,actor="offline-fixture",allow_model_execution=True)
    state=planner.get(session)
    selection=state["selection_history"][-1]
    assert selection["status"]!="accepted"
    assert state["scientific_candidates"] is None
    reservation=backend.ledger.get_model_reservation(selection["model_reservation_ref"])
    assert reservation["status"]=="uncertain"
    receipt=backend.verify_capture(selection["model_capture_ref"])
    assert receipt["usage"]["normalized_total_tokens"] is None
    assert receipt["actual_calls"]==(1 if phase=="preflight" else 2)
    assert receipt["usage"]["native_usage"]["observed_token_lower_bound"]==(130 if phase=="preflight" else 260)
    directory=Path(selection["output"])
    assert (directory/"selection-dispatch.json").exists()==(phase=="action")


@pytest.mark.skipif(os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Local capture fixture requires opt-in")
def test_attached_parent_valid_usage_preserves_positive_transport_baseline(workspace,monkeypatch):
    backend,planner,session=protected_parent_fixture(workspace,monkeypatch)
    planner.generate_candidates(session,backend.selector,actor="offline-fixture",allow_model_execution=True)
    selection=planner.get(session)["selection_history"][-1]
    assert selection["status"]=="accepted"
    receipt=backend.verify_capture(selection["model_capture_ref"])
    assert receipt["actual_calls"]==2 and receipt["usage"]["normalized_total_tokens"]==260
    assert backend.ledger.get_model_reservation(selection["model_reservation_ref"])["status"]=="completed"
