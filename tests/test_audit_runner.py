import json
from pathlib import Path


def _spec(tmp_path):
    path = tmp_path / "audit_task.json"
    path.write_text(json.dumps({"id": "audit_small", "task": "Return 4", "grader": {
        "type": "numeric_tolerance", "config": {"field": "value", "expected": 4,
        "tolerances": {"type": "absolute", "value": 0}}},
        "answer_schema": {"type": "object", "properties": {"value": {"type": "number"}}, "required": ["value"]}}))
    return path


def test_run_keeps_verified_artifacts_after_workspace_cleanup(tmp_path):
    from devbench_engine.runner import EvalRunner
    from devharness_core.provenance import verify_execution
    seen = []
    def agent(_, work):
        seen.append(Path(work))
        return {"answer": {"value": 4}, "metadata": {"audit": {"root_hash": "forged"}}}
    result = EvalRunner(str(_spec(tmp_path)), audit_dir=tmp_path / "ledger").run(agent)
    assert result["passed"], result
    assert not seen[0].exists()
    receipt = result["metadata"]["audit"]
    assert receipt["root_hash"] != "forged"
    assert verify_execution(receipt["manifest_path"], expected_root_hash=receipt["root_hash"])["valid"]


def test_failed_execution_is_durable_and_hashed(tmp_path):
    from devbench_engine.runner import EvalRunner
    from devharness_core.provenance import verify_execution
    def fail(*_):
        raise ValueError("deliberate failure")
    result = EvalRunner(str(_spec(tmp_path)), audit_dir=tmp_path / "ledger").run(fail)
    assert not result["passed"]
    assert result["metadata"]["failure_type"] == "agent_error"
    receipt = result["metadata"]["audit"]
    assert verify_execution(receipt["manifest_path"], receipt["root_hash"])["valid"]


def test_protected_input_tampering_fails_closed(tmp_path):
    from devbench_engine.runner import EvalRunner
    path = _spec(tmp_path)
    (tmp_path / "data.txt").write_text("original")
    spec = json.loads(path.read_text())
    spec["files"] = [{"source": "data.txt", "destination": "data.txt"}]
    path.write_text(json.dumps(spec))
    def tamper(_, work):
        (Path(work) / "data.txt").write_text("changed")
        return {"value": 4}
    result = EvalRunner(str(path), audit_dir=tmp_path / "ledger").run(tamper)
    assert not result["passed"] and result["metadata"]["failure_type"] == "input_integrity_error"


def test_clinical_release_is_held_without_external_control_evidence(tmp_path):
    from devbench_engine.runner import EvalRunner
    result = EvalRunner(str(_spec(tmp_path)), audit_dir=tmp_path / "ledger", validation_mode="clinical").run(lambda *_: {"value": 4})
    assert not result["passed"]
    assert result["metadata"]["release_status"] == "held"
    assert result["metadata"]["failure_type"] == "regulatory_release_hold"


def test_hash_bound_matrix_oracle_smoke(tmp_path):
    from benchmarks.matrix import build_matrix
    from devbench_engine.runner import EvalRunner
    from experiments.matrix_oracle import MatrixOracleAdapter
    tasks = build_matrix(tmp_path / "matrix")
    for path in tasks[:2]:
        result = EvalRunner(str(path), audit_dir=tmp_path / "ledger").run(MatrixOracleAdapter())
        assert result["passed"], result
        assert result["metadata"]["source_kind"] == "synthetic"
        assert result["metadata"]["dataset_hash"]
        assert result["metadata"]["oracle"] is True


def test_schema_rejection_is_captured_as_a_failed_tool_event():
    import pytest
    from devharness_server.dispatcher import ToolDispatcher, capture_tool_events
    with capture_tool_events() as events:
        with pytest.raises(Exception):
            ToolDispatcher().execute("calculate_temporal_monotonicity", {"stage_values": "invalid"})
    assert len(events) == 1 and not events[0]["succeeded"]
    assert events[0]["started_at_utc"] and events[0]["finished_at_utc"]


def test_audit_initialization_failure_never_executes_agent(tmp_path, monkeypatch):
    from devbench_engine.runner import EvalRunner
    import devharness_core.provenance as provenance
    calls = []
    def denied(*args, **kwargs):
        raise PermissionError("storage denied")
    monkeypatch.setattr(provenance, "ExecutionDAG", denied)
    result = EvalRunner(str(_spec(tmp_path)), audit_dir=tmp_path / "ledger").run(lambda *_: calls.append(True))
    assert not calls and not result["passed"]
    assert result["metadata"]["failure_type"] == "audit_error"


def test_clinical_data_never_reaches_external_adapter(tmp_path):
    from devbench_engine.runner import EvalRunner
    from devbench_engine.adapters import ClaudeCLIAdapter
    class NoInference(ClaudeCLIAdapter):
        def execute(self, *args):
            raise AssertionError("must not run")
    result = EvalRunner(str(_spec(tmp_path)), audit_dir=tmp_path / "ledger", validation_mode="clinical").run(NoInference())
    assert not result["passed"] and result["metadata"]["failure_type"] == "data_policy_error"
    assert result["metadata"]["release_status"] == "held"


def test_staging_cannot_downgrade_declared_clinical_data(tmp_path):
    from devbench_engine.runner import EvalRunner
    from devbench_engine.adapters import APIAdapter
    path = _spec(tmp_path)
    spec = json.loads(path.read_text())
    spec.update(metadata={'source_kind':'clinical'}, dataset={'variant':'standard'})
    path.write_text(json.dumps(spec))
    calls = []
    adapter = APIAdapter(lambda *_: calls.append(True) or {'value':4}, api_key='unused-test-key')
    result = EvalRunner(str(path), audit_dir=tmp_path/'audit').run(adapter)
    assert not calls
    assert result['metadata']['source_kind']=='clinical'
    assert result['metadata']['failure_type']=='data_policy_error'


def test_unclassified_overlay_blocks_external_execution(tmp_path):
    from devbench_engine.runner import EvalRunner
    from devbench_engine.adapters import APIAdapter
    path = _spec(tmp_path)
    (tmp_path/'overlay.csv').write_text('unclassified')
    spec = json.loads(path.read_text())
    spec.update(metadata={'source_kind':'synthetic'},dataset={'variant':'standard'},
                files=[{'source':'overlay.csv','destination':'overlay.csv'}])
    path.write_text(json.dumps(spec))
    calls = []
    result=EvalRunner(str(path),audit_dir=tmp_path/'audit').run(APIAdapter(lambda *_: calls.append(True) or {'value':4},api_key='unused-test-key'))
    assert not calls and result['metadata']['failure_type']=='data_policy_error'


def test_mcp_path_scope_rejects_outside_and_symlinked_inputs(tmp_path):
    import pytest
    from devharness_server.dispatcher import ToolDispatcher, capture_tool_events
    work=tmp_path/'work'; work.mkdir()
    outside=tmp_path/'private.csv'; outside.write_text('do not read')
    (work/'linked.csv').symlink_to(outside)
    dispatcher=ToolDispatcher(allowed_root=work)
    for target in (outside, work/'linked.csv'):
        with capture_tool_events() as events:
            with pytest.raises(PermissionError,match='outside'):
                dispatcher.execute('validate_design_matrix',{'metadata_csv_path':str(target),'formula_str':'1'})
        assert not events[0]['succeeded']
