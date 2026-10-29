"""Retained synthetic tool results, separate from any scientific/model trial."""
from contextlib import contextmanager
from copy import deepcopy
import json
import sqlite3

import pytest

from devbench.analysis.checkpoints import digest
from devbench.analysis import rna_publication_profiles as profiles
from devbench.analysis.rna_execution_inputs import prepare_execution_inputs
from devbench.analysis.rna_comparison_outputs import extract_comparison_outputs, load_comparison_outputs
from tests.test_extensible_methods import custom
from tests.test_rna_execution_inputs import inputs, ports
from tests.test_rna_publication_metrics import gene_table
from tests.test_rna_publication_profiles import fixture as intent_fixture, source


class RetainedTableExecutor:
    """Test-only injected artifact production; no host scientific code is run."""
    def __init__(self, table=None, *, fail=False):
        self.table = gene_table() if table is None else table
        self.fail = fail

    def execute(self, contract, inputs, parameters, method_source, lock, output_dir):
        if self.fail:
            return {"exit_code": 1, "runtime": "synthetic_fixture", "error": "retained synthetic failure"}
        (output_dir / "result.json").write_text(json.dumps(self.table), encoding="utf-8")
        return {"exit_code": 0, "runtime": "synthetic_fixture"}


def prepared(tmp_path, *, run_before_plan=False):
    matrix = {"gene_ids": ["g1", "g2", "g3"], "sample_ids": ["s1", "s2", "s3", "s4"],
              "matrix": [[1.0, 2.25, 3.5, 4.0], [5.0, 6.0, 7.0, 8.0], [0.0, 0.0, 0.0, 0.0]]}
    service, binding, _, _ = inputs(tmp_path, matrix=matrix)
    bridge = prepare_execution_inputs(binding, ports(encoding="json"), artifact_service=service, output=tmp_path / "bridge")
    bridge_record = json.loads(__import__("pathlib").Path(bridge["record_ref"]["path"]).read_bytes())
    contract, code, lock = custom(tmp_path)
    contract["inputs"] = {name: {"artifact_type": "json", "required": True} for name in bridge_record["execution_refs"]}
    contract["parameters"] = {"type": "object", "additionalProperties": False, "properties": {}, "required": []}
    method = service.register_method(contract, source=code, environment_lock=lock, actor="synthetic-test")
    analysis = service.create_analysis("synthetic output custody", actor="synthetic-test")
    branch = service.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="synthetic-test")
    step = service.add_step(branch["branch_id"], method["version_id"],
        {name: ref["artifact_id"] for name, ref in bridge_record["execution_refs"].items()}, {}, actor="synthetic-test")
    if run_before_plan:
        service.run_step(step["step_id"], actor="synthetic-test", executor=RetainedTableExecutor(), run_id="1" * 32)
    intent = intent_fixture(tmp_path, dataset_binding=binding)
    frozen_intent = profiles.prepare_assessment_intent(tmp_path / "intent", intent, workspace=tmp_path)
    governed = {"steps": [deepcopy(step)]}
    workflow = source(tmp_path / "workflow.json", {"format": "devbench.rna-assessment-workflow/2",
        "assessment_intent_ref": frozen_intent, "assessment_rules_sha256": profiles.assessment_rules_digest(intent),
        "governed_workflow": governed, "governed_workflow_sha256": digest(governed)})
    plan = profiles.finalize_assessment_plan(frozen_intent, workflow, bridge["record_ref"],
        output=tmp_path / "finalization", workspace=tmp_path, service=service)
    sealed = profiles.prepare_assessment_plan(tmp_path / "plan", plan, workspace=tmp_path, service=service)
    return service, step, sealed


def bound_run(service, step, plan, executor=None):
    import uuid
    from devbench.analysis.rna_comparison_outputs import assessment_run_binding
    allocated = assessment_run_binding(plan, artifact_service=service, project_id="a" * 32,
        scientific_run_id="b" * 32, attempt_id=uuid.uuid4().hex,
        dataset_version_ids=[digest({"synthetic_dataset_version": step["inputs"]})],
        controller_id=uuid.uuid4().hex, step_id=step["step_id"])
    return service.run_step(step["step_id"], actor="synthetic-test", executor=executor,
                            run_id=allocated["controller_id"], correlation=allocated["correlation"])


def repeated_actual_runs(service, step, plan):
    """Allocate both before dispatch; retain distinct actual tool artifacts."""
    import uuid
    from devbench.analysis.rna_comparison_outputs import assessment_run_binding
    allocations = [assessment_run_binding(plan, artifact_service=service, project_id="a" * 32,
        scientific_run_id="b" * 32, attempt_id=uuid.uuid4().hex,
        dataset_version_ids=[digest({"synthetic_dataset_version": step["inputs"]})],
        controller_id=uuid.uuid4().hex, step_id=step["step_id"]) for _ in range(2)]
    tables = [gene_table(), gene_table()]
    tables[1]["rows"][0]["effect"] += 10.0
    return [service.run_step(step["step_id"], actor="synthetic-test", executor=RetainedTableExecutor(table),
        run_id=binding["controller_id"], correlation=binding["correlation"])
        for table, binding in zip(tables, allocations)]


def history_service_with_progress_spy(tmp_path, monkeypatch, *, exhaust_budget=False):
    """Small real metadata DB; budget exhaustion needs no large run archive."""
    from devbench.analysis.methods import MethodService
    service = MethodService(tmp_path / "methods")
    step_id, run_id = "a" * 32, "f" * 32
    with service._db() as db:
        for index in range(8):
            service._put(db, "run", f"{index:032x}", {"step_id": "b" * 32})
        service._put(db, "run", run_id, {"step_id": step_id})
    original_db = service._db
    observed = []

    class ProgressSpy:
        def __init__(self, db):
            self.db, self.handler = db, None
            self.handlers, self.budget_callbacks = [], 0
            self.usable_after_query = False

        def set_progress_handler(self, handler, interval):
            self.handler = handler
            self.handlers.append((handler is not None, interval))
            self.db.set_progress_handler(handler, interval)

        def execute(self, query, parameters):
            if exhaust_budget:
                if self.handler is not None:
                    for _ in range(1000):
                        assert self.handler() == 0
                        self.budget_callbacks += 1
                    assert self.handler() == 1
                    self.budget_callbacks += 1
                raise sqlite3.OperationalError("interrupted")
            assert self.handler is not None, "Workflow history query lacks a SQLite work budget"
            return self.db.execute(query, parameters)

    @contextmanager
    def inspected_db():
        with original_db() as db:
            spy = ProgressSpy(db)
            observed.append(spy)
            try:
                yield spy
            finally:
                assert spy.handler is None
                spy.usable_after_query = db.execute("SELECT 1").fetchone() == (1,)

    monkeypatch.setattr(service, "_db", inspected_db)
    return service, {"steps": [{"step_id": step_id}]}, run_id, observed


def test_workflow_history_installs_sqlite_work_budget_before_metadata_query(tmp_path, monkeypatch):
    from devbench.analysis.rna_comparison_outputs import _complete_run_history
    service, workflow, run_id, observed = history_service_with_progress_spy(tmp_path, monkeypatch)
    _complete_run_history([run_id], workflow, service)
    assert observed[0].handlers == [(True, 1000), (False, 0)]
    assert observed[0].usable_after_query
    with pytest.raises(ValueError, match="omitted"):
        _complete_run_history([], workflow, service)
    assert observed[1].handlers == [(True, 1000), (False, 0)]
    assert observed[1].usable_after_query


def test_workflow_history_budget_interrupt_fails_closed_and_clears_handler(tmp_path, monkeypatch):
    from devbench.analysis.rna_comparison_outputs import _complete_run_history
    service, workflow, run_id, observed = history_service_with_progress_spy(tmp_path, monkeypatch, exhaust_budget=True)
    with pytest.raises(ValueError, match="[Bb]ounded.*history.*cannot be verified") as caught:
        _complete_run_history([run_id], workflow, service)
    assert isinstance(caught.value.__cause__, sqlite3.DatabaseError)
    assert observed[0].budget_callbacks == 1001
    assert observed[0].handlers == [(True, 1000), (False, 0)]
    assert observed[0].usable_after_query


@pytest.mark.parametrize("selection", ["first", "last", "complete"])
def test_explicit_frozen_step_cannot_select_among_unprespecified_actual_attempts(tmp_path, selection):
    service, step, plan = prepared(tmp_path)
    runs = repeated_actual_runs(service, step, plan)
    assert len({run["outputs"]["dataset"] for run in runs}) == 2
    assert all(service.provenance(run["run_id"])["valid"] for run in runs)
    selected = runs if selection == "complete" else [runs[0] if selection == "first" else runs[1]]
    with pytest.raises(ValueError, match="history|attempt|omitted|retry|prespecified"):
        extract_comparison_outputs(selected, plan, artifact_service=service)


def test_retained_bundle_rechecks_actual_step_history_after_another_dispatch(tmp_path):
    import uuid
    service, step, plan = prepared(tmp_path)
    first = bound_run(service, step, plan, RetainedTableExecutor())
    retained = extract_comparison_outputs([first], plan, artifact_service=service)
    changed = gene_table()
    changed["rows"][0]["effect"] += 10.0
    correlation = {**first["correlation"], "attempt_id": uuid.uuid4().hex}
    second = service.run_step(step["step_id"], actor="synthetic-test", executor=RetainedTableExecutor(changed),
        run_id=uuid.uuid4().hex, correlation=correlation)
    assert second["status"] == "succeeded" and service.provenance(first["run_id"])["valid"]
    with pytest.raises(ValueError, match="history|attempt|omitted|retry|prespecified"):
        load_comparison_outputs(retained, artifact_service=service)


def test_coordinator_extracts_actual_full_retained_results_and_seals_after_plan(tmp_path):
    service, step, plan = prepared(tmp_path)
    run = bound_run(service, step, plan, RetainedTableExecutor())
    result = extract_comparison_outputs([run["run_id"]], plan, artifact_service=service)
    bundle = load_comparison_outputs(result, artifact_service=service)
    assert bundle["format"] == "devbench.rna-analysis-output-bundle/2"
    assert bundle["assessment_plan_ref"] == plan
    assert bundle["runs"][0]["run_id"] == run["run_id"]
    assert bundle["runs"][0]["status"] == "succeeded"
    assert bundle["runs"][0]["evidence_root"] == run["evidence"]["root_hash"]
    assert len(bundle["gene_tables"]) == 1
    retained = bundle["gene_tables"][0]
    assert retained["artifact_ref"]["generated_by"] == run["run_id"]
    table = json.loads(__import__("pathlib").Path(retained["table_ref"]["path"]).read_bytes())
    assert table == gene_table()
    assert table["rows"][2]["p_value"] is None and table["rows"][2]["q_value"] is None
    assert table["family"]["eligible_ids"] == ["g1", "g2", "g3"]
    assert bundle["extraction_provenance"]["code_sha256"]
    assert bundle["sets"] == [] and bundle["curves"] == [] and bundle["graphs"] == []
    assert result["record_ref"] and result["root_hash"]
    assert bundle["runs"][0]["frozen_comparison_eligible"] is True
    assert bundle["gene_tables"][0]["frozen_comparison_eligible"] is True


def test_failed_run_is_retained_and_not_rewritten_as_a_zero_estimate(tmp_path):
    service, step, plan = prepared(tmp_path)
    run = bound_run(service, step, plan, RetainedTableExecutor(fail=True))
    result = extract_comparison_outputs([run], plan, artifact_service=service)
    bundle = load_comparison_outputs(result, artifact_service=service)
    assert bundle["runs"][0]["status"] == "failed"
    assert "retained synthetic failure" in bundle["runs"][0]["error"]
    assert bundle["gene_tables"] == []
    assert any(item["status"] == "failed" for item in bundle["diagnostics"])


@pytest.mark.parametrize("attack", ["summary", "forged_receipt", "altered_output_ref", "duplicate_run"])
def test_agent_summary_or_untrusted_run_receipt_is_not_numeric_authority(tmp_path, attack):
    service, step, plan = prepared(tmp_path)
    run = bound_run(service, step, plan, RetainedTableExecutor())
    if attack == "summary": receipts = [{"run_id": run["run_id"], "summary": "gene effect 99"}]
    elif attack == "forged_receipt": receipts = [{**run, "status": "failed"}]
    elif attack == "altered_output_ref": receipts = [{**run, "outputs": {"dataset": step["inputs"]["counts"]}}]
    else: receipts = [run["run_id"], run["run_id"]]
    with pytest.raises(ValueError, match="receipt|retained|unique|run"):
        extract_comparison_outputs(receipts, plan, artifact_service=service)


def test_unsealed_plan_and_unrelated_workflow_run_are_rejected(tmp_path):
    service, step, plan = prepared(tmp_path)
    run = bound_run(service, step, plan, RetainedTableExecutor())
    with pytest.raises(ValueError, match="reference|immutable|plan"):
        extract_comparison_outputs([run], {"format": "devbench.rna-assessment-plan/2"}, artifact_service=service)
    other = service.add_step(step["branch_id"], step["method_version_id"], step["inputs"], {}, actor="synthetic-test")
    other_run = service.run_step(other["step_id"], actor="synthetic-test", executor=RetainedTableExecutor())
    with pytest.raises(ValueError, match="workflow|step"):
        extract_comparison_outputs([other_run], plan, artifact_service=service)


@pytest.mark.parametrize("other_branch", [False, True])
def test_actual_planner_declaration_rejects_equivalent_cloned_step_identity(tmp_path, other_branch):
    from devbench.analysis.rna_comparison_outputs import _workflow_matches
    from devbench.analysis.rna_scientific_trial import workflow_snapshot
    from tests.test_method_planner import resolved, trial_spec
    from tests.test_extensible_methods import FixtureExecutor
    planner, state, _, _, branch, original = resolved(tmp_path)
    declaration = planner.declare_trial(state["session_id"], branch["branch_id"], trial_spec(original), actor="synthetic-test")
    workflow = workflow_snapshot(planner, {"planner_session_id": state["session_id"], "experiment_ids": [declaration["id"]]})
    exact = planner.methods.get("step", declaration["step_ids"]["Unknown QC"])
    destination = branch["branch_id"]
    if other_branch:
        destination = planner.methods.create_branch(branch["analysis_id"], branch["base_revision"], actor="synthetic-test")["branch_id"]
    clone = planner.methods.add_step(destination, exact["method_version_id"], exact["inputs"], exact["parameters"], actor="synthetic-test")
    run = planner.methods.run_step(clone["step_id"], actor="synthetic-test", executor=FixtureExecutor())
    assert planner.methods.provenance(run["run_id"])["valid"]
    assert clone["inputs"] == exact["inputs"] and clone["parameters"] == exact["parameters"]
    assert clone["method_version_id"] == exact["method_version_id"] and clone["step_id"] != exact["step_id"]
    assert _workflow_matches(run, clone, workflow) is False


def test_retained_controller_binding_rejects_an_equivalent_unallocated_retry(tmp_path):
    import uuid
    from devbench.analysis.rna_comparison_outputs import _execution_binding
    from devbench.analysis.rna_scientific_trial import workflow_snapshot
    from tests.test_method_planner import resolved, trial_spec
    from tests.test_extensible_methods import FixtureExecutor
    planner, state, _, _, branch, original = resolved(tmp_path)
    declaration = planner.declare_trial(state["session_id"], branch["branch_id"], trial_spec(original), actor="synthetic-test")
    workflow = workflow_snapshot(planner, {"planner_session_id": state["session_id"], "experiment_ids": [declaration["id"]]})
    allocated = {"controller_id": uuid.uuid4().hex, "correlation": {
        "project_id": "a" * 32, "scientific_run_id": "b" * 32, "attempt_id": "c" * 32,
        "analysis_version_id": "d" * 64, "dataset_version_ids": ["e" * 64]}}
    planner.run_trial(state["session_id"], declaration["id"], actor="synthetic-test", allow_data_execution=True,
        executor=FixtureExecutor(), execution_bindings={"Unknown QC": allocated})
    actual = planner.methods.get("run", allocated["controller_id"])
    observed = _execution_binding(actual, workflow, planner.methods)
    assert observed["controller_id"] == actual["run_id"] and observed["correlation"] == actual["correlation"]
    retry = planner.methods.run_step(declaration["step_ids"]["Unknown QC"], actor="synthetic-test", executor=FixtureExecutor())
    assert retry["inputs"] == actual["inputs"] and retry["parameters"] == actual["parameters"]
    with pytest.raises(ValueError, match="controller|allocation|binding|retry"):
        _execution_binding(retry, workflow, planner.methods)


def test_retained_result_content_tampering_fails_before_output_sealing(tmp_path):
    service, step, plan = prepared(tmp_path)
    run = bound_run(service, step, plan, RetainedTableExecutor())
    path = service.artifact_path(run["outputs"]["dataset"])
    path.chmod(0o644)
    path.write_bytes(path.read_bytes().replace(b"2.0", b"9.0"))
    with pytest.raises(ValueError, match="hash|evidence|changed"):
        extract_comparison_outputs([run], plan, artifact_service=service)


def test_incomplete_tested_family_is_retained_without_subset_bh_recomputation(tmp_path):
    service, step, plan = prepared(tmp_path)
    table = gene_table()
    table["family"]["complete"] = False
    table["rows"] = table["rows"][:1]
    table["mapping"]["records"] = table["mapping"]["records"][:1]
    run = bound_run(service, step, plan, RetainedTableExecutor(table))
    result = extract_comparison_outputs([run], plan, artifact_service=service)
    bundle = load_comparison_outputs(result, artifact_service=service)
    actual = json.loads(__import__("pathlib").Path(bundle["gene_tables"][0]["table_ref"]["path"]).read_bytes())
    assert actual["family"]["complete"] is False and actual["rows"][0]["q_value"] == .03
    assert any("incomplete_tested_family" in item["reason_codes"] for item in bundle["diagnostics"])


@pytest.mark.parametrize("attack", ["filtered_values", "duplicate_mapping", "invalid_bh", "missing_family"])
def test_false_success_statistics_are_rejected_from_retained_outputs(tmp_path, attack):
    service, step, plan = prepared(tmp_path)
    table = gene_table()
    if attack == "filtered_values": table["rows"][0]["status"] = "filtered"
    elif attack == "duplicate_mapping": table["mapping"]["records"][1]["target_id"] = "g1"
    elif attack == "invalid_bh": table["rows"][0]["q_value"] = .005
    else: del table["family"]
    run = bound_run(service, step, plan, RetainedTableExecutor(table))
    with pytest.raises(ValueError):
        extract_comparison_outputs([run], plan, artifact_service=service)


def test_actual_registered_temporal_result_retains_family_and_unknown_scientific_alignment(tmp_path):
    """A bounded artificial Gaussian worker check, not a publication/data trial."""
    from devbench.analysis.methods import MethodService
    from tests.test_rna_execution_inputs import registered_ports
    samples = ["s" + str(i) for i in range(8)]
    matrix = {"gene_ids": ["g1", "g2"], "sample_ids": samples,
              "matrix": [[float(i) + (.15 if i % 2 else -.1) for i in range(8)], [1.0] * 8]}
    rows = [{"sample_id": name, "biological_unit_id": "u" + str(i), "time": float(i), "numeric_time": float(i)}
            for i, name in enumerate(samples)]
    service = MethodService(tmp_path / "methods")
    originals = {}
    for name, data in (("matrix", matrix), ("metadata", {"samples": rows})):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(data))
        originals[name] = service.import_artifact(path, artifact_type="json", actor="synthetic-test")["artifact_id"]
    binding = {"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": originals["matrix"],
               "metadata_artifact_id": originals["metadata"], "declarations": {
                   "format": "devbench.bulk-rna-input/1", "representation": "normalized_expression", "axes": ["gene", "sample"],
                   "provenance": {"source": "eight artificial columns for installed worker verification"},
                   "biological_independence": {"independent_units": True},
                   "metadata_roles": {"biological_unit": "biological_unit_id", "time": "time"}}}
    selected = registered_ports(service)
    selected["parameters"].update(formula_str="~ numeric_time", time_col="time")
    selected["string_columns"] = ["sample_id", "biological_unit_id"]
    bridge = prepare_execution_inputs(binding, selected, artifact_service=service, output=tmp_path / "bridge")
    bridge_record = json.loads(__import__("pathlib").Path(bridge["record_ref"]["path"]).read_bytes())
    analysis = service.create_analysis("registered synthetic Gaussian verification", actor="synthetic-test")
    branch = service.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="synthetic-test")
    step = service.add_step(branch["branch_id"], selected["method_version_id"],
        {key: ref["artifact_id"] for key, ref in bridge_record["execution_refs"].items()}, selected["parameters"], actor="synthetic-test")
    intent = intent_fixture(tmp_path, dataset_binding=binding)
    frozen = profiles.prepare_assessment_intent(tmp_path / "intent", intent, workspace=tmp_path)
    governed = {"steps": [step]}
    workflow = source(tmp_path / "workflow.json", {"format": "devbench.rna-assessment-workflow/2", "assessment_intent_ref": frozen,
        "assessment_rules_sha256": profiles.assessment_rules_digest(intent), "governed_workflow": governed,
        "governed_workflow_sha256": digest(governed)})
    plan_record = profiles.finalize_assessment_plan(frozen, workflow, bridge["record_ref"], output=tmp_path / "finalization",
                                                   workspace=tmp_path, service=service)
    plan = profiles.prepare_assessment_plan(tmp_path / "plan", plan_record, workspace=tmp_path, service=service)
    run = bound_run(service, step, plan)
    assert run["status"] == "succeeded", run.get("error")
    assert run["execution"]["runtime"] == "trusted_dispatcher" and service.provenance(run["run_id"])["valid"]
    actual_raw = json.loads(service.artifact_path(run["outputs"]["result"]).read_bytes())
    extracted = extract_comparison_outputs([run["run_id"]], plan, artifact_service=service)
    bundle = load_comparison_outputs(extracted, artifact_service=service)
    table = json.loads(__import__("pathlib").Path(bundle["gene_tables"][0]["table_ref"]["path"]).read_bytes())
    assert table["family"]["eligible_ids"] == ["g1", "g2"] and table["family"]["complete"] is True
    assert table["rows"][0]["p_value"] == actual_raw["genes"]["g1"]["p_value"]
    assert table["rows"][0]["q_value"] == actual_raw["genes"]["g1"]["q_value"]
    assert table["alignment"]["contrast"] is None and table["alignment"]["estimand_id"] is None
    assert any("unknown_effect_comparability" in item["reason_codes"] for item in bundle["diagnostics"])
    assert "coefficients" in actual_raw["genes"]["g1"]
    assert json.loads(service.artifact_path(bundle["gene_tables"][0]["artifact_ref"]["artifact_id"]).read_bytes()) == actual_raw


def test_valid_run_before_plan_stays_retrospective_without_frozen_eligibility(tmp_path):
    from devbench.analysis.methods import MethodService
    with pytest.raises(ValueError, match="started|executed|run|pre-run|before"):
        prepared(tmp_path, run_before_plan=True)
    service = MethodService(tmp_path / "methods", readonly=True)
    historical = service.get("run", "1" * 32)
    assert historical["status"] == "succeeded" and service.provenance(historical["run_id"])["valid"]


def test_unbound_historical_run_retains_actual_outputs_without_frozen_eligibility(tmp_path):
    service, step, plan = prepared(tmp_path)
    historical = service.run_step(step["step_id"], actor="synthetic-test", executor=RetainedTableExecutor())
    assert historical["status"] == "succeeded" and service.provenance(historical["run_id"])["valid"]
    retained = extract_comparison_outputs([historical], plan, artifact_service=service)
    bundle = load_comparison_outputs(retained, artifact_service=service)
    assert bundle["runs"][0].get("frozen_comparison_eligible", True) is False
    assert bundle["gene_tables"][0].get("frozen_comparison_eligible", True) is False
    assert any("retrospective_unbound_assessment_plan" in item["reason_codes"] for item in bundle["diagnostics"])


def test_positive_binding_is_in_actual_method_started_evidence_before_backend(tmp_path):
    from devbench.analysis.rna_comparison_outputs import assessment_plan_identity
    service, step, plan = prepared(tmp_path)
    expected = assessment_plan_identity(plan, artifact_service=service)
    observed = []
    class ObserveStarted(RetainedTableExecutor):
        def execute(self, contract, inputs, parameters, method_source, lock, output_dir):
            nodes = list((output_dir.parent / "evidence" / "nodes").iterdir())
            assert len(nodes) == 1
            started = json.loads(nodes[0].read_bytes())
            assert started["kind"] == "method_started"
            observed.append(started["payload"]["run"]["correlation"]["analysis_version_id"])
            return super().execute(contract, inputs, parameters, method_source, lock, output_dir)
    run = bound_run(service, step, plan, ObserveStarted())
    assert run["status"] == "succeeded" and observed == [expected]
    bundle = load_comparison_outputs(extract_comparison_outputs([run], plan, artifact_service=service), artifact_service=service)
    assert bundle["runs"][0]["assessment_plan_binding"]["expected_plan_identity"] == expected
    assert bundle["runs"][0]["frozen_comparison_eligible"] is True
