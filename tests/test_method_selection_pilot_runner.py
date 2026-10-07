"""Offline launch/admission tests, never recorded live planner evidence."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest


def runner():
    name = "scripts.run_method_selection_pilot"
    assert importlib.util.find_spec(name) is not None, "The native pilot runner is not implemented"
    return __import__(name, fromlist=["interface_for"])


def candidate():
    return {"name": "Fixture method", "parameter_candidates": [{"name": "scale", "value": 1.5, "origin": "assistant", "rationale": "Test fixture"}]}


def test_interface_preserves_candidate_chosen_parameters_without_defaults():
    from devharness_core.methods import MethodContract
    contract = runner().interface_for(candidate(), ["quality_a", "signal_a"], True, "f" * 64)
    parsed = MethodContract.from_dict(contract).to_dict()
    assert parsed["validation"]["comparator"] == "observation_selection"
    assert parsed["parameters"]["required"] == ["scale"]
    assert "default" not in json.dumps(parsed["parameters"])
    assert parsed["environment"]["image"] == runner().IMAGE
    assert parsed["implementation"]["kind"] == "oci"
    assert set(parsed["inputs"]) == set(parsed["outputs"]) == {"dataset"}


def test_diagnostic_interface_binds_action_and_coordinator_semantics():
    from devharness_core.methods import MethodContract
    item = candidate()
    item["action_scope"] = {"format": "devbench.method-action/1", "intent": "diagnostic_flagging",
                            "interpretation": "numerical_atypicality", "exclusions": False}
    contract = runner().interface_for(item, ["quality_a"], False, "f" * 64)
    parsed = MethodContract.from_dict(contract).to_dict()
    assert parsed["validation"]["comparator"] == "observation_diagnostic"
    assert parsed["action_scope"] == item["action_scope"]
    assert "input_sha256" in parsed["scientific"]["output_semantics"]
    item["action_scope"]["interpretation"] = "qc_review"
    with pytest.raises(ValueError, match="measurement semantics"):
        runner().interface_for(item, ["quality_a"], False, "f" * 64)
    parsed = runner().interface_for(item, ["quality_a"], False, "f" * 64,
                                    measurement_semantics={"quality_a": "Declared artificial library size"})
    assert parsed["validation"]["diagnostic_policy"]["measurement_semantics"]["quality_a"]


def profile_candidate(family="absolute_mad"):
    item = candidate()
    item["action_scope"] = {"format": "devbench.method-action/1", "intent": "diagnostic_flagging",
                            "interpretation": "numerical_atypicality", "exclusions": False}
    item["diagnostic_profile"] = {"family": family, "measurements": ["quality_a"],
                                  "group_by": [], "aggregate": "max", "flag_threshold": None}
    return item


def test_profile_factory_requires_exact_frozen_descriptor_without_defaults():
    from devharness_core.methods import MethodContract
    item = profile_candidate()
    parsed = MethodContract.from_dict(runner().interface_for(
        item, ["quality_a"], False, "f" * 64, diagnostic_profiles=True)).to_dict()
    assert parsed["validation"]["diagnostic_policy"]["format"] == "devbench.diagnostic-policy/2"
    assert parsed["validation"]["diagnostic_policy"]["profile"] == item["diagnostic_profile"]
    assert "Flag threshold unresolved" in parsed["scientific"]["output_semantics"]
    assert "midrank" in parsed["scientific"]["output_semantics"]
    assert runner().interface_for(item, ["quality_a"], False, "f" * 64)["validation"]["diagnostic_policy"]["format"] == "devbench.diagnostic-policy/1"
    for change in ("missing", "unknown"):
        value = deepcopy(item)
        if change == "missing": value.pop("diagnostic_profile")
        else: value["diagnostic_profile"]["family"] = "unavailable family"
        with pytest.raises(ValueError, match="profile|family|descriptor"):
            runner().interface_for(value, ["quality_a"], False, "f" * 64, diagnostic_profiles=True)


def test_profile_trial_catalog_is_compatible_with_each_candidate():
    module = runner()
    item = trial()
    item["metric_sources"]["quality_average"]["field"] = "/profile_metrics/aggregate_mean"
    assert module.admit_trial(item, [profile_candidate()], "input-id", ["quality_a"], diagnostic_profiles=True) is None
    item["metric_sources"]["quality_average"]["field"] = "/reference_qc_means/quality_a/after"
    with pytest.raises(ValueError, match="catalog"):
        module.admit_trial(item, [profile_candidate()], "input-id", ["quality_a"], diagnostic_profiles=True)
    item["candidates"].append("Retention candidate")
    retained = candidate()
    retained["name"] = "Retention candidate"
    item["inputs"][retained["name"]] = {"dataset": "input-id"}
    item["parameters"][retained["name"]] = {"scale": 1.5}
    item["resource_limits"].update(max_runs=2, timeout_seconds=40)
    item["metric_sources"]["quality_average"]["field"] = "/diagnostic_measurements/available_fraction"
    with pytest.raises(ValueError, match="catalog"):
        module.admit_trial(item, [profile_candidate(), retained], "input-id", ["quality_a"], diagnostic_profiles=True)
    item["metric_sources"]["quality_average"]["field"] = "/shape/before/0"
    assert module.admit_trial(item, [profile_candidate(), retained], "input-id", ["quality_a"], diagnostic_profiles=True) is None


@pytest.mark.parametrize("name", ["input_mean", "flagged_mean", "absolute_standardized_flagged_shift"])
def test_profile_catalog_rejects_legacy_measurement_pointers(name):
    module = runner()
    pointer = "/diagnostic_measurements/by_measurement/quality_a/" + name
    assert pointer in module.metric_catalog(["quality_a"], diagnostics=True)
    assert pointer not in module.metric_catalog(["quality_a"], diagnostics=True, profiles=True)
    item = trial()
    item["metric_sources"]["quality_average"]["field"] = pointer
    with pytest.raises(ValueError, match="catalog"):
        module.admit_trial(item, [profile_candidate()], "input-id", ["quality_a"], diagnostic_profiles=True)


def test_public_diagnostic_metrics_are_descriptive_and_never_hidden_truth():
    names = runner().metric_catalog(["quality_a"], diagnostics=True)
    assert "/diagnostic_measurements/flag_rate" in names
    assert "/diagnostic_measurements/by_measurement/quality_a/absolute_standardized_flagged_shift" in names
    assert not any("damage_sensitivity" in name for name in names)


def test_parameter_duplicates_and_invented_user_origins_fail_closed():
    item = candidate()
    item["parameter_candidates"].append(dict(item["parameter_candidates"][0]))
    with pytest.raises(ValueError):
        runner().frozen_parameters(item)
    item = candidate()
    item["parameter_candidates"][0]["origin"] = "user"
    with pytest.raises(ValueError):
        runner().frozen_parameters(item)


def trial():
    return {"id": "fixture", "candidates": ["Fixture method"], "subset_spec": {"kind": "full_dataset"},
            "inputs": {"Fixture method": {"dataset": "input-id"}}, "parameters": {"Fixture method": {"scale": 1.5}},
            "resource_limits": {"max_runs": 1, "memory_mb": 512, "timeout_seconds": 20},
            "metrics": [{"name": "quality_average", "category": "calibration", "direction": "descriptive", "interpretation": "Test"}],
            "metric_sources": {"quality_average": {"kind": "validation", "field": "/reference_qc_means/quality_a/after"}}}


def test_trial_is_admitted_only_for_matching_inputs_parameters_and_public_metrics():
    assert runner().admit_trial(trial(), [candidate()], "input-id", ["quality_a"]) is None


@pytest.mark.parametrize("change", ["input", "parameters", "names", "runs", "timeout", "private_metric", "self_report", "subset"])
def test_invalid_trial_is_preserved_as_failure_not_silently_repaired(change):
    item = trial()
    if change == "input": item["inputs"]["Fixture method"]["dataset"] = "wrong"
    if change == "parameters": item["parameters"]["Fixture method"]["scale"] = 4
    if change == "names": item["candidates"] = ["Different method"]
    if change == "runs": item["resource_limits"]["max_runs"] = 3
    if change == "timeout": item["resource_limits"]["timeout_seconds"] = 999
    if change == "private_metric": item["metric_sources"]["quality_average"]["field"] = "/reference/damage_sensitivity"
    if change == "self_report": item["metric_sources"]["quality_average"] = {"kind": "output_json", "output": "dataset", "field": "/accuracy"}
    if change == "subset": item["subset_spec"] = {"kind": "partial"}
    with pytest.raises(ValueError):
        runner().admit_trial(item, [candidate()], "input-id", ["quality_a"])


def test_live_dispatch_requires_explicit_flags_and_open_grading_boundary(tmp_path):
    module = runner()
    root = tmp_path / "pilot"
    root.mkdir()
    with pytest.raises(PermissionError):
        module.require_authorization(root, allow_model=False, allow_data=True)
    with pytest.raises(PermissionError):
        module.require_authorization(root, allow_model=True, allow_data=False)
    (root / "grading-started.json").write_text("{}")
    with pytest.raises(ValueError):
        module.require_authorization(root, allow_model=True, allow_data=True)


def test_public_diagnostic_catalog_has_no_hidden_reference_scores():
    names = runner().metric_catalog(["quality_a", "signal_a"])
    assert "/reference_qc_means/quality_a/after" in names
    assert "/reference_qc_means/signal_a/absolute_standardized_shift" in names
    assert all("damage_sensitivity" not in item and "valid_ids" not in item for item in names)


def test_user_requested_model_is_used_without_fallback():
    assert runner().BOUNDS["requested_model"] == "gpt-6.1-sol"
    assert runner().BOUNDS["reasoning_effort"] == "low"


def runtime_fixture(path):
    import hashlib
    path.write_bytes(b"fixture executable; never launched")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"executable_path": str(path), "executable_sha256": digest,
            "version": "codex-cli 0.159.2", "component_sha256": {str(path): digest},
            "runtime_path_prepends": []}


def test_runtime_is_frozen_without_exposure_in_public_context(tmp_path):
    from devbench.analysis.method_selection_evaluation import prepare_pilot, public_case, verify_preparation
    identity = runtime_fixture(tmp_path / "codex")
    root = tmp_path / "pilot"
    prepared = prepare_pilot(root, seed=782, runtime=identity)
    assert runner().pilot_runtime(root) == identity
    assert "private/evaluator/runtime.json" in prepared["files"]
    assert "executable_path" not in json.dumps(public_case(root, prepared["case_ids"][0]))
    (root / "private/evaluator/runtime.json").write_text("{}")
    with pytest.raises(ValueError, match="Frozen preparation file changed"):
        verify_preparation(root, prepared["content_sha256"])


def test_missing_runtime_and_changed_executable_fail_before_dispatch(tmp_path):
    from devbench.analysis.method_selection_evaluation import prepare_pilot
    root = tmp_path / "pilot"
    with pytest.raises((FileNotFoundError, ValueError)):
        runner().pilot_runtime(root)
    executable = tmp_path / "codex"
    identity = runtime_fixture(executable)
    prepare_pilot(root, seed=782, runtime=identity)
    executable.write_bytes(b"changed fixture executable")
    with pytest.raises(ValueError, match="Pinned Codex executable component changed"):
        runner().pilot_runtime(root)


def test_runtime_components_must_remain_outside_denied_report_root(tmp_path):
    from devbench.analysis.method_selection_evaluation import prepare_pilot
    root = tmp_path / "pilot"
    root.mkdir()
    identity = runtime_fixture(root / "codex")
    prepare_pilot(root, seed=782, runtime=identity)
    with pytest.raises(ValueError, match="outside the evaluator private root"):
        runner().pilot_runtime(root)


def test_runtime_added_after_preparation_cannot_authorize_execution(tmp_path):
    from devbench.analysis.method_selection_evaluation import prepare_pilot, verify_preparation
    root = tmp_path / "pilot"
    prepared = prepare_pilot(root, seed=782)
    identity = runtime_fixture(tmp_path / "codex")
    (root / "private/evaluator/runtime.json").write_text(json.dumps(identity))
    verify_preparation(root, prepared["content_sha256"])
    with pytest.raises(ValueError, match="runtime identity was not frozen"):
        runner().pilot_runtime(root)


@pytest.mark.parametrize("diagnostics,profiles,profile_freeze", [
    (False, False, False), (True, False, False), (True, True, True),
    (False, True, False), (True, True, False)])
def test_real_method_store_comparator_provenance_and_decision_seal(tmp_path, monkeypatch, diagnostics, profiles, profile_freeze):
    """Trusted fixture execution tests integration, not live model performance."""
    from devbench.analysis import method_selection_evaluation as evaluation
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.methods import MethodService
    from devharness_core.provenance import canonical_json
    from tests.test_method_planner import Selector, candidate as scientific_candidate

    module = runner()
    root = tmp_path / "offline-pilot"
    root.mkdir()
    manifest = evaluation.prepare_pilot(root, seed=832764, diagnostic_outputs=diagnostics, diagnostic_profiles=profile_freeze)
    identity = manifest["case_ids"][0]
    public = evaluation.public_case(root, identity)
    directory = root / "private/cases" / identity
    methods = MethodService(directory / "service")
    planner = ScientificMethodPlanner(methods)
    artifact = methods.import_artifact(directory / "dataset.json", artifact_type="json", actor="offline-test")
    public["dataset_summary"]["artifact_ids"] = [artifact["artifact_id"]]
    state = planner.create(public["task"], public["dataset_summary"], actor="offline-test", data_classification="synthetic")
    analysis = methods.create_analysis("Offline integration only", actor="offline-test")
    branch = methods.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="offline-test")
    lock = directory / "fixture.lock.json"
    lock.write_bytes(canonical_json({"runtime": "offline fixture"}))
    source = directory / "fixture.py"
    source.write_text("# Inert offline fixture; never a live planner candidate\n")
    choices, bindings = [], []
    measurements = public["dataset_summary"]["observed"]["measurement_names"]
    for name in ("Offline fixture A", "Offline fixture B"):
        item = scientific_candidate(name)
        item.update(assumptions=[], parameter_candidates=[])
        if diagnostics or profiles:
            item["action_scope"] = {"format": "devbench.method-action/1", "intent": "diagnostic_flagging",
                                    "interpretation": "numerical_atypicality", "exclusions": False}
        if profiles:
            item["diagnostic_profile"] = {"family": "empirical_two_tail", "measurements": [measurements[0]],
                                          "group_by": [], "aggregate": "max", "flag_threshold": None}
        choices.append(item)
        contract = module.interface_for(item, measurements, public["dataset_summary"]["observed"]["sample_labels_present"], evaluation.file_digest(lock), diagnostic_profiles=profiles)
        contract["implementation"]["source_sha256"] = evaluation.file_digest(source)
        method = methods.register_method(contract, source=source, environment_lock=lock, actor="offline-test")
        bindings.append({"candidate": name, "method_version_id": method["version_id"], "discovery": None})
    recorded = Selector({"candidates": choices}, {"bindings": bindings})
    planner.generate_candidates(state["session_id"], recorded, actor="offline-test")
    planner.resolve_capabilities(state["session_id"], recorded, [row["method_version_id"] for row in bindings], actor="offline-test")
    assert "registry" not in recorded.contexts[0]
    assert all("damaged_ids" not in json.dumps(context) for context in recorded.contexts)
    spec = {"id": "offline-comparison", "candidates": [row["name"] for row in choices], "purpose": "Offline integration fixture",
            "subset_spec": {"kind": "full_dataset"}, "subset_validity": {"preserves_preconditions": True, "rationale": "Complete immutable fixture"}, "commands": [],
            "inputs": {row["name"]: {"dataset": artifact["artifact_id"]} for row in choices}, "parameters": {row["name"]: {} for row in choices},
            "resource_limits": {"memory_mb": 512, "timeout_seconds": 40, "max_runs": 2},
            "metrics": [{"name": "mean_displacement", "category": "signal_preservation", "direction": "lower_better", "interpretation": "Visible distribution shift only; no quality truth"}],
            "metric_sources": {"mean_displacement": {"kind": "validation", "field": "/reference_qc_means/" + measurements[0] + "/absolute_standardized_shift"}}}
    if diagnostics or profiles:
        spec["metrics"][0].update(direction="descriptive", interpretation="Actual flag rate, without accuracy or robustness claims")
        spec["metric_sources"]["mean_displacement"]["field"] = "/diagnostic_measurements/flag_rate"
    if profiles:
        spec["metric_sources"]["mean_displacement"]["field"] = "/profile_metrics/aggregate_mean"
        spec["metrics"].append({"name": "profile_score_coverage", "category": "calibration", "direction": "descriptive", "interpretation": "Actual numerical score availability; no scientific winner claim"})
        spec["metric_sources"]["profile_score_coverage"] = {"kind": "validation", "field": "/profile_metrics/by_measurement/" + measurements[0] + "/available_fraction"}
    module.admit_trial(spec, choices, artifact["artifact_id"], measurements, diagnostic_profiles=profiles)
    planner.declare_trial(state["session_id"], branch["branch_id"], spec, actor="offline-test")

    class FixtureExecutor:
        def execute(self, contract, inputs, parameters, source, lock, output_dir):
            data = json.loads(inputs["dataset"].read_text())
            output = {"retained_ids": [row["cell_id"] for row in data["observations"]]}
            if diagnostics or profiles:
                output.update(format="devbench.observation-diagnostics/1", input_sha256=evaluation.file_digest(inputs["dataset"]),
                    evidence_scope="numerical_atypicality", records=[{
                        "observation_id": row["cell_id"], "status": "flagged" if i == 0 else "not_flagged", "score": float(i),
                        "reason": "Unusual under the supplied representation" if i == 0 else "Within reference",
                        "evidence_refs": ["/observations/" + str(i) + "/" + measurements[0]]}
                        for i, row in enumerate(data["observations"])])
            if profiles:
                # Offline fixture computes its own rank definition; no model/OCI call.
                values = [row[measurements[0]] for row in data["observations"]]
                records = []
                for i, row in enumerate(data["observations"]):
                    value = row[measurements[0]]
                    rank = (sum(other < value for other in values) + .5 * sum(other == value for other in values)) / len(values)
                    score = 1 - 2 * min(rank, 1 - rank)
                    records.append({"observation_id": row["cell_id"], "score": score, "score_reason": None,
                        "flag": None, "flag_reason": "Flag threshold unresolved",
                        "profiles": {measurements[0]: {"score": score, "reason": None, "reference_count": len(values), "reference_group": {}}},
                        "evidence_refs": [f"/observations/{i}/" + measurements[0]]})
                output.update(format="devbench.observation-diagnostics/2", records=records)
            (output_dir / "selection.json").write_text(json.dumps(output))
            return {"exit_code": 0, "runtime": "trusted_offline_fixture_only"}

    results = planner.run_trial(state["session_id"], spec["id"], actor="offline-test", allow_data_execution=True, executor=FixtureExecutor())
    assert all(row["status"] == "success" for row in results)
    if profiles:
        assert all(row["metric_values"]["profile_score_coverage"] == 1. for row in results)
    assessment = planner.evidence_assessment(state["session_id"])
    assert all(row["meets_evidence_floor"] is (not (diagnostics or profiles)) for row in assessment["candidates"])
    outputs = []
    for row in results:
        run = methods.get("run", row["provenance"]["run_id"])
        path = methods.artifact_path(run["outputs"]["dataset"])
        outputs.append({"candidate": row["candidate"], "run_id": run["run_id"], "status": "success", "path": str(path),
                        "sha256": evaluation.file_digest(path), "output_artifact_id": run["outputs"]["dataset"], "receipt_root": run["evidence"]["root_hash"]})
    def seal():
        return evaluation.seal_decision(root, identity, {"choice": None, "rationale": "Offline fixture has no scientific recommendation"},
            accepted=False, terminal_status="offline_fixture", outputs=outputs, state=planner.get(state["session_id"]))
    if profiles and not profile_freeze:
        with pytest.raises(ValueError, match="separately frozen v[23] rubric"):
            seal()
        assert not (root / "evidence/decisions" / (identity + ".json")).exists()
        return
    assert seal()
    if diagnostics or profiles:
        sealed = json.loads((root / "evidence/decisions" / (identity + ".json")).read_text())
        assert sealed["outputs"][0]["output_kind"] == "observation_diagnostic"
        assert len(sealed["outputs"][0]["diagnostics"]["flagged_ids"]) == (0 if profiles else 1)
        assert len(sealed["outputs"][0]["diagnostics"]["retained_ids"]) == 72
        assert all(row["comparable_experiments"] == [] for row in assessment["candidates"])
        plot_result = {"cases": [{"case_id": identity, "case_type": "offline_fixture",
            "terminal_status": "offline_fixture", "decision_sha256": evaluation.file_digest(
                root / "evidence/decisions" / (identity + ".json"))}]}
        if profiles:
            # Exercise normal grade routing with actual receipts and real evaluator/
            # renderers. Only source-snapshot qualification is an inert fixture.
            seals = {identity: plot_result["cases"][0]["decision_sha256"]}
            for other in manifest["case_ids"][1:]:
                seals[other] = evaluation.seal_decision(root, other, {"choice": None, "rationale": "Offline fixture: no candidate executed"},
                    accepted=False, terminal_status="offline_fixture", outputs=[], state={})
            for other, seal_hash in seals.items():
                evaluation.write_once(root / "private/cases" / other / "case-summary.json", {"decision_sha256": seal_hash})
            review = root / "offline-blinded-review.md"
            review.write_text("Offline test review fixture only; no scientific validation or formal winner is asserted.\n")
            evaluation.seal_blinded_review(root, seals, review)
            monkeypatch.setattr(module, "verify_source", lambda *args: {"offline_source_fixture": True})
            monkeypatch.setattr("sys.argv", ["run_method_selection_pilot.py", "grade", "--report", str(root),
                "--expected-freeze", manifest["content_sha256"], "--source-manifest", str(root / "inert-source-fixture.json")])
            module.main()
            plot_manifest = json.loads((root / "plot-manifest.json").read_text())
            assert "executed" in plot_manifest
            plot = plot_manifest["executed"]
            assert Path(plot["profile_plot"]["path"]).is_file()
        else:
            # Legacy diagnostic plotting fixture does not access reference labels.
            (root / "grading-started.json").write_text("{}")
            plot = evaluation.plot_executed_outputs(root, plot_result)
        assert Path(plot["path"]).is_file()
        plotted = json.loads((root / "executed-output-plot-data.json").read_text())
        assert plotted[0]["outputs"][0]["flagged"] == (0 if profiles else 1)
        assert plotted[0]["outputs"][0]["retained"] == 72
        assert plotted[0]["outputs"][0]["receipt_root"] == outputs[0]["receipt_root"]
        if profiles:
            assert plotted[0]["outputs"][0]["score_available"] == 72
            assert plotted[0]["outputs"][0]["flags_deferred"] == 72
            assert plotted[0]["outputs"][0]["arithmetic_fidelity"]["verified"] is True
            assert len(plotted[0]["outputs"][0]["numeric_profiles"]) == 72
            assert Path(plot["profile_plot"]["path"]).is_file()
        decision_path = root / "evidence/decisions" / (identity + ".json")
        decision_path.write_text(decision_path.read_text() + " ")
        with pytest.raises(ValueError, match="decision hash"):
            evaluation.plot_executed_outputs(root, plot_result)
