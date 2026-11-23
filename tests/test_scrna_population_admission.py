"""Synthetic conditional admission checks; no provider or public-data execution."""

from copy import deepcopy

import pytest

from devbench.analysis.checkpoints import digest
from devbench.analysis.method_planner import ScientificMethodPlanner
from devbench.analysis.methods import MethodService
from devbench.analysis.workflow_actions import WorkflowActions
from devbench.analysis.workflow_operations import WorkflowOperationLedger
from tests.test_method_planner import Selector, candidate, trial_spec
from tests.test_scrna_population_analysis import fixture


def prepared(tmp_path, *, profile_unknowns=(), **fixture_options):
    tmp_path = tmp_path.resolve()
    path, specification, _, _ = fixture(tmp_path, **fixture_options)
    methods = MethodService(tmp_path / "methods")
    artifact = methods.import_artifact(
        path, artifact_type="anndata", actor="synthetic-verifier"
    )
    binding = {
        "format": "devbench.scrna-population-input-binding/1",
        "anndata_artifact_id": artifact["artifact_id"],
        "declarations": {
            "assay": "scrna",
            "measurement": "umi_counts",
            "count_provenance": "Synthetic fixture",
            "counts_layer": "counts",
            "capture_unit_key": "capture",
            "capture_units_confirmed": True,
            "annotation_provenance": "Synthetic supplied labels",
            "biological_independence": {
                "independent": True,
                "unit_column": "embryo",
                "evidence": (
                    "Synthetic declared biological units; no authenticity claim"
                ),
            },
        },
        "specification": specification,
        "trial_binding": {
            "request_sha256": digest({"synthetic": True}),
            "project_id": "a" * 32,
            "question_sha256": digest(specification["question"]),
            "source_sha256": artifact["sha256"],
        },
    }
    planner = ScientificMethodPlanner(methods)
    task = {
        "scientific_objective": specification["question"],
        "computational_task": "experimental_scRNA_contrast",
    }
    summary = {
        "observed": {},
        "inferred": {},
        "unknowns": [
            {
                "field": key,
                "impact": "Unverified supplied declaration: " + key,
                "blocking": True,
            }
            for key in profile_unknowns
        ],
        "artifact_ids": [artifact["artifact_id"]],
    }
    plan = planner.create(
        task,
        summary,
        actor="synthetic-verifier",
        data_classification="synthetic",
        rna_input_contract=binding,
        evaluation_purpose="execution_verification",
    )
    return planner, plan, binding


def audit(planner, plan, *, authorized=True):
    actions = WorkflowActions(
        planner,
        WorkflowOperationLedger(planner.methods.root / "operations.sqlite"),
        actor="synthetic-verifier",
        selector=None,
        allow_data_execution=authorized,
    )
    state = actions.perform("audit", actions.create_state(plan["session_id"]))
    return actions, state


def contrast_candidate():
    result = candidate("scrna population contrast")
    result.update(
        method_family="differential_expression",
        assumptions=[],
        expected_inputs=["anndata"],
        parameter_candidates=[],
        action_scope={
            "format": "devbench.method-action/1",
            "intent": "infer",
            "interpretation": "none",
            "exclusions": False,
        },
    )
    return result


def resolve(planner, plan):
    method = planner.methods.register_dispatcher(
        "scrna_population_contrast", actor="synthetic-verifier"
    )
    proposal = contrast_candidate()
    selector = Selector(
        {"candidates": [proposal]},
        {
            "bindings": [
                {
                    "candidate": proposal["name"],
                    "method_version_id": method["version_id"],
                    "discovery": None,
                }
            ]
        },
    )
    planner.generate_candidates(
        plan["session_id"], selector, actor="synthetic-verifier"
    )
    candidates = planner.resolve_capabilities(
        plan["session_id"], selector, [method["version_id"]], actor="synthetic-verifier"
    )
    return candidates[0], selector


def test_explicit_population_binding_preserves_unknown_header_facts(tmp_path):
    planner, plan, binding = prepared(tmp_path)
    assert plan["scrna_population_input"]["trial_binding"] == binding["trial_binding"]
    observed = plan["context"]["dataset_summary"]["observed"]
    assert observed["metrics_finite"] is None
    assert observed["observation_ids_unique"] is None
    assert observed["biological_units_independent"] is None
    assert planner.methods.list_records("run") == []


def test_unobserved_population_counts_do_not_admit_inference(tmp_path):
    planner, plan, _ = prepared(tmp_path)
    result, _ = resolve(planner, plan)
    assert result["applicability"] == "blocked_unknown"
    assert result["action_scope"]["intent"] == "infer"
    assert any("preflight" in message for message in result["blockers"])
    assert planner.methods.list_records("run") == []


def test_graph_preflight_requires_fresh_authority_without_creating_attempt(tmp_path):
    planner, plan, _ = prepared(tmp_path)
    actions, state = audit(planner, plan, authorized=False)
    assert state["status"] == "review_required"
    assert state["failure"]["kind"] == "data_execution_authority_required"
    assert planner.methods.list_records("run") == []
    assert planner.methods.list_records("scrna_population_preflight") == []
    assert actions.ledger.get(plan["session_id"], "audit", 0) is None


def test_valid_actual_preflight_admits_only_conditional_contrast(tmp_path):
    planner, plan, _ = prepared(tmp_path)
    actions, state = audit(planner, plan)
    assert state["status"] == "ready", state
    runs = planner.methods.list_records("run")
    assert len(runs) == 3
    assert all(run["status"] == "succeeded" for run in runs)
    assert [run["parameters"]["specification"]["measurement"] for run in runs] == [
        "umi_counts"
    ] * 3
    result, _ = resolve(planner, plan)
    assert result["applicability"] == "eligible", result["blockers"]
    assert result["action_scope"]["intent"] == "infer"
    assert result["conditional_admission"]["independence_verified"] is False
    assert (
        result["conditional_admission"]["policy"]
        == "devbench.scrna-declaration-conditioned-admission/1"
    )
    assert (
        planner.get(plan["session_id"])["context"]["dataset_summary"]["observed"][
            "metrics_finite"
        ]
        is None
    )
    assert actions._audit(plan) == actions._audit(plan)
    assert len(planner.methods.list_records("run")) == 3


@pytest.mark.parametrize("options", [{"bad": -1}, {"absent": True}])
def test_failed_counts_or_design_cannot_reach_fit(tmp_path, options):
    planner, plan, _ = prepared(tmp_path, **options)
    _, state = audit(planner, plan)
    assert state["status"] == "abstained", state
    result, _ = resolve(planner, plan)
    assert result["applicability"] != "eligible"
    assert all(
        planner.methods.method(run["method_version_id"])["contract"]["implementation"][
            "tool_name"
        ]
        != "scrna_population_contrast"
        for run in planner.methods.list_records("run")
    )
    evidence = planner.methods.list_records("scrna_population_preflight")[0]["evidence"]
    if options.get("absent"):
        assert any(
            "rank_deficient_or_ill_conditioned_design" in failure
            for failure in evidence["failures"]
        )
    else:
        assert any(
            "counts" in (receipt.get("failure") or "")
            for receipt in evidence["receipts"]
        )


def test_original_global_inference_policy_still_requires_verified_independence():
    from devbench.analysis.method_planner import _applicability

    result = _applicability(
        contrast_candidate(),
        {
            "observed": {
                "input_integrity_valid": True,
                "observation_ids_unique": True,
                "metrics_finite": True,
                "assay_semantics_known": True,
                "biological_units_independent": None,
                "design_identifiable": True,
            },
            "unknowns": [],
        },
    )
    assert result["applicability"] == "blocked_unknown"
    assert (
        "Mandatory action prerequisite unknown: biological_units_independent"
        in result["blockers"]
    )


def test_workbench_audit_retains_verified_binary_pseudobulk(workspace_tmp_path):
    import h5py

    from devbench.analysis import scrna_population_admission as admission

    planner, plan, _ = prepared(workspace_tmp_path)
    _, state = audit(planner, plan)
    assert state["status"] == "ready", state
    proof = admission.verify_population_preflight(plan, planner.methods)
    receipt = next(item for item in proof["receipts"] if item["stage"] == "pseudobulk")
    capsule = receipt["capsule_ref"]
    result = admission._result_json(
        planner.methods.artifact_path(receipt["output_ref"]["artifact_id"])
    )
    assert "counts" not in result["pseudobulk"]
    assert capsule["sha256"] == result["pseudobulk"]["matrix"]["sha256"]
    with h5py.File(planner.methods.artifact_path(capsule["artifact_id"])) as store:
        assert store["expression"].shape == (24, 12)
    assert planner.methods.provenance(receipt["run_id"])["valid"]


def test_workbench_can_revalidate_a_legacy_preflight_without_redispatch(
    workspace_tmp_path, monkeypatch
):
    from devbench.analysis import scrna_population_admission as admission
    from devharness_server.method_adapter import dispatcher_contract

    planner, plan, _ = prepared(workspace_tmp_path)
    current_request = admission._request

    def legacy_request(*args):
        request = current_request(*args)
        request["format"] = "devbench.scrna-population-preflight-request/1"
        del request["method_versions"][admission.CAPSULE]
        del request["method_versions"][admission.PARQUET_CONTRAST]
        request["method_versions"]["scrna_population_pseudobulk"] = dispatcher_contract(
            "scrna_population_pseudobulk",
        ).version_id
        return request

    with monkeypatch.context() as patches:
        patches.setattr(admission, "_request", legacy_request)
        _, state = audit(planner, plan)
        assert state["status"] == "ready", state
        original = admission.verify_population_preflight(plan, planner.methods)
    assert (
        admission.verify_population_preflight(
            plan,
            planner.methods,
            historical_ref=original["record_ref"],
        )
        == original
    )
    assert len(planner.methods.list_records("run")) == 3


def test_governed_binary_contrast_retains_full_paired_family(workspace_tmp_path):
    import json

    import pyarrow.parquet as pq

    from devbench.core.population_contract import content_sha256
    from devbench.platform.scrna_population_analysis import run_population_analysis

    planner, plan, _ = prepared(workspace_tmp_path)
    audit(planner, plan)
    methods = planner.methods
    method = methods.register_dispatcher(
        "scrna_population_contrast_parquet", actor="synthetic-verifier"
    )
    proposal = contrast_candidate()
    selector = Selector(
        {"candidates": [proposal]},
        {
            "bindings": [
                {
                    "candidate": proposal["name"],
                    "method_version_id": method["version_id"],
                    "discovery": None,
                }
            ]
        },
    )
    planner.generate_candidates(
        plan["session_id"], selector, actor="synthetic-verifier"
    )
    resolved = planner.resolve_capabilities(
        plan["session_id"], selector, [method["version_id"]], actor="synthetic-verifier"
    )[0]
    assert resolved["applicability"] == "eligible", resolved["blockers"]
    specification = plan["scrna_population_input"]["specification"]
    declared = trial(planner, plan, resolved)
    rows = planner.run_trial(
        plan["session_id"],
        declared["id"],
        actor="synthetic-verifier",
        allow_data_execution=True,
    )
    assert rows[0]["status"] == "success", [
        run.get("error") for run in methods.list_records("run")
    ]
    run = methods.get("run", rows[0]["provenance"]["run_id"])
    result = methods.artifact_path(run["outputs"]["result"])
    summary = json.loads(methods.artifact_path(run["outputs"]["summary"]).read_bytes())
    table = pq.read_table(result).to_pylist()
    assert summary["result_format"] == "devbench.scrna-population-contrast/2"
    assert summary["specification_sha256"] == content_sha256(specification)
    assert len(table) == 48
    assert {row["stage"] for row in table} == set(specification["stage_levels"])
    assert all(
        row["p_value"] is None or 0 <= row["p_value"] <= row["q_value"] <= 1
        for row in table
    )
    assert methods.provenance(run["run_id"])["valid"]
    legacy = run_population_analysis(
        workspace_tmp_path / "synthetic.h5ad", specification, "contrast"
    )
    assert table == legacy["gene_statistics"]


def trial(planner, plan, result, *, parameters=None):
    methods = planner.methods
    analysis = methods.create_analysis(
        "Synthetic admitted contrast", actor="synthetic-verifier"
    )
    branch = methods.create_branch(
        analysis["analysis_id"], analysis["revision_id"], actor="synthetic-verifier"
    )
    binding = plan["scrna_population_input"]
    spec = trial_spec({"inputs": {"anndata": binding["anndata_artifact_id"]}})
    name = result["name"]
    spec.update(
        id="synthetic-population-contrast",
        candidates=[name],
        inputs={name: {"anndata": binding["anndata_artifact_id"]}},
        parameters={name: parameters or {"specification": binding["specification"]}},
        resource_limits={"memory_mb": 4096, "timeout_seconds": 300, "max_runs": 1},
    )
    return planner.declare_trial(
        plan["session_id"], branch["branch_id"], spec, actor="synthetic-verifier"
    )


def test_conditional_admission_reaches_actual_controlled_execution(tmp_path):
    planner, plan, _ = prepared(tmp_path)
    audit(planner, plan)
    result, selector = resolve(planner, plan)
    declared = trial(planner, plan, result)
    rows = planner.run_trial(
        plan["session_id"],
        declared["id"],
        actor="synthetic-verifier",
        allow_data_execution=True,
    )
    assert len(rows) == 1 and rows[0]["status"] == "success", rows
    run = planner.methods.get("run", rows[0]["provenance"]["run_id"])
    assert run["parameters"] == {
        "specification": plan["scrna_population_input"]["specification"]
    }
    assert (
        planner.methods.method(run["method_version_id"])["contract"]["action_scope"]
        == contrast_candidate()["action_scope"]
    )
    assert len(planner.methods.list_records("run")) == 4
    with pytest.raises(PermissionError, match="fresh"):
        planner.methods.reproduce(run["run_id"], actor="synthetic-verifier")
    assert len(planner.methods.list_records("run")) == 4
    assert (
        selector.contexts[0]["scrna_population_admission"]["independence_verified"]
        is False
    )
    assert selector.contexts[0]["dataset_summary"]["observed"]["metrics_finite"] is True
    assert (
        selector.contexts[0]["dataset_summary"]["observed"][
            "biological_units_independent"
        ]
        is None
    )


@pytest.mark.parametrize(
    "change", ["counts_layer", "population", "stages", "units", "options"]
)
def test_changed_trial_specification_never_dispatches(tmp_path, change):
    planner, plan, _ = prepared(tmp_path)
    audit(planner, plan)
    result, _ = resolve(planner, plan)
    spec = deepcopy(plan["scrna_population_input"]["specification"])
    if change == "counts_layer":
        spec["counts_layer"] = "X"
    elif change == "population":
        spec["populations"].reverse()
    elif change == "stages":
        spec["stage_levels"].reverse()
    elif change == "units":
        spec["biological_unit_column"] = "capture"
    else:
        spec["limits"]["max_profiles"] -= 1
    with pytest.raises(ValueError, match="exact frozen"):
        trial(planner, plan, result, parameters={"specification": spec})
    assert len(planner.methods.list_records("run")) == 3


def test_uncertain_preflight_attempt_is_never_dispatched_again(tmp_path, monkeypatch):
    planner, plan, _ = prepared(tmp_path)
    original = planner.methods.run_step
    calls = []

    def interrupted(*args, **kwargs):
        calls.append(kwargs["run_id"])
        original(*args, **kwargs)
        raise TimeoutError("Synthetic coordinator interruption after child execution")

    monkeypatch.setattr(planner.methods, "run_step", interrupted)
    actions, state = audit(planner, plan)
    assert state["status"] == "reconciliation_required"
    record = planner.methods.list_records("scrna_population_preflight")[0]
    assert record["status"] == "running" and len(record["attempts"]) == 1
    with pytest.raises(ValueError, match="occupied or uncertain"):
        actions._audit(plan)
    assert len(calls) == 1
    assert len(planner.methods.list_records("run")) == 1


def test_current_validator_source_change_invalidates_preflight_without_rewriting(
    tmp_path, monkeypatch
):
    from devbench.analysis import scrna_population_admission as admission

    planner, plan, _ = prepared(tmp_path)
    audit(planner, plan)
    proof = admission.verify_population_preflight(plan, planner.methods)
    before = planner.methods.list_records("scrna_population_preflight")[0]
    original = admission._request

    def changed(*args):
        return {**original(*args), "validator_sha256": "f" * 64}

    monkeypatch.setattr(admission, "_request", changed)
    with pytest.raises(ValueError, match="source changed"):
        admission.verify_population_preflight(plan, planner.methods)
    with pytest.raises(ValueError, match="retained audit receipt"):
        admission.verify_population_preflight(
            plan,
            planner.methods,
            historical_ref={**proof["record_ref"], "sha256": "0" * 64},
        )
    assert (
        admission.verify_population_preflight(
            plan, planner.methods, historical_ref=proof["record_ref"]
        )
        == proof
    )
    assert planner.methods.list_records("scrna_population_preflight")[0] == before
    assert len(planner.methods.list_records("run")) == 3


def test_a_different_registered_inference_cannot_use_the_population_admission(tmp_path):
    planner, plan, _ = prepared(tmp_path)
    audit(planner, plan)
    contract = planner.methods.register_dispatcher(
        "scrna_population_contrast", actor="synthetic-verifier"
    )["contract"]
    contract = {**contract, "name": "different inference"}
    result = planner._inspect_applicability(
        contrast_candidate(),
        plan["context"]["dataset_summary"],
        plan["context"]["dataset_summary"]["artifact_ids"],
        actor="synthetic-verifier",
        contract=contract,
        state=plan,
    )
    assert result["applicability"] == "blocked_unknown"
    assert "conditional_admission" not in result


def test_planning_permission_is_checked_before_any_preflight_attempt(tmp_path):
    from devbench.analysis.workflow_entrypoints import advance_workflow_to_gate

    planner, plan, _ = prepared(tmp_path)
    result = advance_workflow_to_gate(
        planner, plan["session_id"], actor="synthetic-verifier"
    )
    assert result["required_action"] == "authorize_data_execution"
    assert planner.methods.list_records("scrna_population_preflight") == []


def test_preflight_reader_accepts_bounded_full_feature_outputs_beyond_legacy_16mb(
    tmp_path,
):
    from devbench.analysis import scrna_population_admission as admission

    path = tmp_path.resolve() / "large-synthetic-result.json"
    path.write_text('{"synthetic_padding":"' + "0" * 16_000_001 + '"}')
    assert len(admission._result_json(path)["synthetic_padding"]) == 16_000_001
    path.write_text('{"counts_valid": true, "counts_valid": false}')
    with pytest.raises(ValueError, match="Duplicate"):
        admission._result_json(path)
    path.write_text('{"measurement": NaN}')
    with pytest.raises(ValueError, match="finite"):
        admission._result_json(path)
    path.write_text('{"measurement": 1e999}')
    with pytest.raises(ValueError, match="finite"):
        admission._result_json(path)


def test_scientific_candidate_cannot_omit_or_downgrade_infer_scope(tmp_path):
    planner, plan, _ = prepared(tmp_path)
    method = planner.methods.register_dispatcher(
        "scrna_population_contrast", actor="synthetic-verifier"
    )
    item = contrast_candidate()
    del item["action_scope"]
    selector = Selector(
        {"candidates": [item]},
        {
            "bindings": [
                {
                    "candidate": item["name"],
                    "method_version_id": method["version_id"],
                    "discovery": None,
                }
            ]
        },
    )
    planner.generate_candidates(
        plan["session_id"], selector, actor="synthetic-verifier"
    )
    with pytest.raises(ValueError, match="action scope must match exactly"):
        planner.resolve_capabilities(
            plan["session_id"],
            selector,
            [method["version_id"]],
            actor="synthetic-verifier",
        )
    assert planner.methods.list_records("run") == []


@pytest.mark.parametrize("extra_unknown", [False, True])
def test_native_profile_unknowns_remain_explicit_conditional_assumptions(
    tmp_path, extra_unknown
):
    declarations = [
        "assay",
        "measurement",
        "count_provenance",
        "capture_unit_identity",
        "annotation_provenance",
    ]
    unknowns = declarations + (
        ["unresolved_consent_or_cohort"] if extra_unknown else []
    )
    planner, plan, _ = prepared(tmp_path, profile_unknowns=unknowns)
    audit(planner, plan)
    result, selector = resolve(planner, plan)
    assert result["applicability"] == (
        "blocked_unknown" if extra_unknown else "eligible"
    ), result["blockers"]
    assert result["conditional_admission"]["unverified_assumptions"] == sorted(
        declarations
    )
    assert all(
        row["blocking"] is True
        for row in planner.get(plan["session_id"])["context"]["dataset_summary"][
            "unknowns"
        ]
    )
    assert selector.contexts[0]["scrna_population_admission"][
        "declaration_conditioned_unknowns"
    ] == sorted(declarations)
    if extra_unknown:
        assert any(
            "unresolved_consent_or_cohort" in blocker for blocker in result["blockers"]
        )


def test_unknown_or_invalid_count_storage_accounting_cannot_pass_preflight(tmp_path):
    from devbench.analysis import scrna_population_admission as admission

    planner, plan, _ = prepared(tmp_path)
    audit(planner, plan)
    record = planner.methods.list_records("scrna_population_preflight")[0]
    receipts = record["evidence"]["receipts"]
    actual = {
        receipt["stage"]: admission._result_json(
            planner.methods.artifact_path(receipt["output_ref"]["artifact_id"])
        )
        for receipt in receipts
    }
    for missing in (None, False):
        bodies = deepcopy(actual)
        bodies["pseudobulk"]["pseudobulk"]["count_validation"][
            "sparse_storage_structure_validated"
        ] = missing
        evidence = admission._assess(record["request"], receipts, bodies)
        assert evidence["status"] == "refused"
        assert any("count" in failure for failure in evidence["failures"])


def direct_step(planner, plan, stage):
    methods = planner.methods
    binding = plan["scrna_population_input"]
    method = methods.register_dispatcher(
        "scrna_population_" + stage, actor="synthetic-verifier"
    )
    analysis = methods.create_analysis(
        "Synthetic explicit caller", actor="synthetic-verifier"
    )
    branch = methods.create_branch(
        analysis["analysis_id"], analysis["revision_id"], actor="synthetic-verifier"
    )
    return methods.add_step(
        branch["branch_id"],
        method["version_id"],
        {"anndata": binding["anndata_artifact_id"]},
        {"specification": binding["specification"]},
        actor="synthetic-verifier",
    )


def test_every_population_tool_requires_fresh_service_authority(tmp_path):
    import threading

    planner, plan, _ = prepared(tmp_path)
    for stage in ("audit", "design", "pseudobulk", "contrast"):
        step = direct_step(planner, plan, stage)
        with pytest.raises(PermissionError, match="fresh"):
            planner.methods.run_step(step["step_id"], actor="synthetic-verifier")
        stopped = threading.Event()
        stopped.set()
        with pytest.raises(PermissionError, match="cancel"):
            planner.methods.run_step(
                step["step_id"],
                actor="synthetic-verifier",
                data_execution_authorized=True,
                cancellation_event=stopped,
            )
    assert planner.methods.list_records("run") == []


def test_population_cancel_before_publication_retains_failure_and_prevents_restart(
    tmp_path, monkeypatch
):
    import threading

    from devbench.analysis import methods as service_module

    planner, plan, _ = prepared(tmp_path)
    step = direct_step(planner, plan, "audit")
    stop = threading.Event()
    original = service_module.validate_outputs

    def cancel_after_validation(*args):
        result = original(*args)
        stop.set()
        return result

    monkeypatch.setattr(service_module, "validate_outputs", cancel_after_validation)
    run = planner.methods.run_step(
        step["step_id"],
        actor="synthetic-verifier",
        data_execution_authorized=True,
        cancellation_event=stop,
    )
    assert run["status"] == "failed"
    assert run["cancelled"] is True and run["outputs"] == {}
    assert run["worker_cancellation"]["workers"]
    assert all(
        row["cleanup"]["verified_stopped"]
        for row in run["worker_cancellation"]["workers"]
    )
    assert planner.methods.provenance(run["run_id"])["valid"]
    stop.clear()
    with pytest.raises(PermissionError, match="reconciliation"):
        planner.methods.run_step(
            step["step_id"],
            actor="synthetic-verifier",
            data_execution_authorized=True,
            cancellation_event=stop,
        )
    assert len(planner.methods.list_records("run")) == 1


def test_controlled_population_run_forwards_runtime_cancellation_and_authority(
    tmp_path,
):
    import threading

    planner, plan, _ = prepared(tmp_path)
    audit(planner, plan)
    candidate, _ = resolve(planner, plan)
    declared = trial(planner, plan, candidate)
    stop = threading.Event()
    stop.set()
    rows = planner.run_trial(
        plan["session_id"],
        declared["id"],
        actor="synthetic-verifier",
        allow_data_execution=True,
        cancellation_event=stop,
    )
    assert rows[0]["status"] == "failed"
    assert "cancel" in str(rows[0]["failures"]).lower()
    assert len(planner.methods.list_records("run")) == 3


def test_running_population_worker_is_cancelled_and_verified_stopped(
    tmp_path, monkeypatch
):
    import threading

    from devharness_core import watchdog

    planner, plan, _ = prepared(tmp_path)
    step = direct_step(planner, plan, "audit")
    stop = threading.Event()
    original = watchdog.MemoryWatchdog.start

    def cancel_after_worker_registration(monitor):
        result = original(monitor)
        stop.set()
        return result

    monkeypatch.setattr(
        watchdog.MemoryWatchdog, "start", cancel_after_worker_registration
    )
    run = planner.methods.run_step(
        step["step_id"],
        actor="synthetic-verifier",
        data_execution_authorized=True,
        cancellation_event=stop,
    )
    assert run["status"] == "failed" and run["cancelled"] is True
    assert run["outputs"] == {}
    workers = run["worker_cancellation"]["workers"]
    assert workers and all(
        worker["cleanup"]["verified_stopped"] is True for worker in workers
    )
    assert planner.methods.provenance(run["run_id"])["valid"]
