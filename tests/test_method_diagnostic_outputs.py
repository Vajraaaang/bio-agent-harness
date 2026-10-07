"""Immutable diagnostic flags and coordinator public measurements only."""
from copy import deepcopy
import hashlib
import json
import pytest
from devharness_core.provenance import canonical_json
from devharness_core.methods import MethodContract
from tests.test_method_contracts import _payload


def contract(interpretation="numerical_atypicality"):
    value = _payload()
    value["inputs"] = {"dataset": {"artifact_type": "json", "required": True}}
    value["outputs"] = {"dataset": {"artifact_type": "json", "required": True, "path": "/outputs/diagnostics.json"}}
    value["action_scope"] = {"format": "devbench.method-action/1", "intent": "diagnostic_flagging",
                             "interpretation": interpretation, "exclusions": False}
    value["validation"] = {"comparator": "observation_diagnostic", "invariants": [], "counts_layer": "X",
                           "qc_metrics": ["measurement"], "sample_key": "sample",
                           "diagnostic_policy": {"format": "devbench.diagnostic-policy/1", "interpretation": interpretation,
                                                 "measurement_semantics": {}}}
    return value


def artifacts(tmp_path):
    before = tmp_path / "input.json"
    after = tmp_path / "output.json"
    rows = [{"cell_id": "opaque-a", "measurement": 1., "sample": "a"},
            {"cell_id": "opaque-b", "measurement": 3., "sample": "b"}]
    before.write_bytes(canonical_json({"format": "devbench.qc-observations/1", "observations": rows}))
    output = {"format": "devbench.observation-diagnostics/1", "input_sha256": hashlib.sha256(before.read_bytes()).hexdigest(),
              "evidence_scope": "numerical_atypicality", "retained_ids": [row["cell_id"] for row in rows],
              "records": [{"observation_id": row["cell_id"], "status": "flagged" if i else "not_flagged",
                           "score": float(i), "reason": "Unusual under the supplied representation" if i else "Within reference",
                           "evidence_refs": [f"/observations/{i}/measurement"]} for i, row in enumerate(rows)]}
    after.write_bytes(canonical_json(output))
    return before, after, output


def validate(tmp_path, change=None):
    from devbench.analysis.method_validation import validate_outputs
    before, after, output = artifacts(tmp_path)
    if change:
        change(output)
        after.write_text(json.dumps(output))
    return validate_outputs(contract(), {"dataset": before}, {"dataset": after})


def test_diagnostic_contract_is_additive_and_matches_intent():
    assert MethodContract.from_dict(contract()).to_dict() == contract()
    value = contract()
    value["action_scope"]["exclusions"] = True
    with pytest.raises(ValueError):
        MethodContract.from_dict(value)
    value = contract()
    value["validation"]["comparator"] = "observation_selection"
    with pytest.raises(ValueError):
        MethodContract.from_dict(value)


def test_actual_flags_are_validated_with_full_retention_and_independent_descriptions(tmp_path):
    result = validate(tmp_path)
    assert result["flagged_ids"] == ["opaque-b"]
    assert result["removed_ids"] == []
    assert result["retained_ids"] == ["opaque-a", "opaque-b"]
    assert result["diagnostic_measurements"]["flag_rate"] == .5
    assert result["diagnostic_measurements"]["by_measurement"]["measurement"]["flagged_mean"] == 3.
    assert result["diagnostic_stability"] == {"value": None, "reason": "No matched perturbation outputs were supplied"}
    assert all(result["invariants"].values())


@pytest.mark.parametrize("change", [
    lambda x: x["records"].pop(),
    lambda x: x["records"].append(deepcopy(x["records"][0])),
    lambda x: x["records"][0].update(observation_id="foreign"),
    lambda x: x["retained_ids"].pop(),
    lambda x: x["records"].reverse(),
    lambda x: x.update(input_sha256="0" * 64),
    lambda x: x.update(observations=[]),
    lambda x: x["records"][0].update(score=float("nan")),
    lambda x: x["records"][0].update(score=True),
    lambda x: x["records"][0].update(score=None),
    lambda x: x["records"][0].update(status="damaged"),
    lambda x: x["records"][0].update(reason="Proven technical damage"),
    lambda x: x["records"][0].update(evidence_refs=["/observations/1/measurement"]),
    lambda x: x["records"][0].update(evidence_refs=["/observations/0/cell_id"]),
    lambda x: x["records"][0].update(evidence_refs=["/private/truth"]),
    lambda x: x.update(evidence_scope="qc_review"),
])
def test_mutation_missing_coverage_and_fabricated_scores_rejected(tmp_path, change):
    with pytest.raises(ValueError):
        validate(tmp_path, change)


def test_unavailable_is_null_with_recorded_reason(tmp_path):
    result = validate(tmp_path, lambda x: x["records"][0].update(status="unavailable", score=None, reason="Reference unavailable"))
    assert result["unavailable_ids"] == ["opaque-a"]
    assert result["diagnostic_measurements"]["available_fraction"] == .5


def test_retention_output_is_not_diagnostic_evidence(tmp_path):
    with pytest.raises(ValueError):
        validate(tmp_path, lambda x: (x.clear(), x.update(retained_ids=["opaque-a", "opaque-b"])))


def test_qc_review_requires_coordinator_policy_semantics():
    with pytest.raises(ValueError, match="semantics"):
        MethodContract.from_dict(contract("qc_review"))


def test_computable_prerequisites_inspected_from_bounded_input(tmp_path):
    from devbench.analysis.method_validation import observation_prerequisite_inspection
    before, *_ = artifacts(tmp_path)
    record = observation_prerequisite_inspection(before, ["metrics_finite", "measurement_variance_positive", "measurement_rank"])
    assert record["observed"]["metrics_finite"] is True
    assert record["observed"]["measurement_variance_positive"] is True
    assert record["observed"]["measurement_rank"] == 1
    assert record["input_sha256"] == hashlib.sha256(before.read_bytes()).hexdigest()


def test_extractor_carries_actual_independent_diagnostics(tmp_path):
    from devbench.analysis.method_validation import extract_observation_diagnostics
    before, after, _ = artifacts(tmp_path)
    result = extract_observation_diagnostics(before, after, contract()["validation"])
    assert result["independent_diagnostics"]["measurements"]["flag_rate"] == .5
    assert result["independent_diagnostics"]["group_flag_rates"] == {"sample": {"a": 0., "b": 1.}}
    assert result["independent_diagnostics"]["stability"]["value"] is None


def test_non_exclusion_scope_cannot_remove_observations(tmp_path):
    from devbench.analysis.method_validation import validate_outputs
    before, after, _ = artifacts(tmp_path)
    value = contract()
    value["action_scope"].update(intent="retain", interpretation="none")
    value["validation"].pop("diagnostic_policy")
    value["validation"]["comparator"] = "observation_selection"
    after.write_bytes(canonical_json({"retained_ids": ["opaque-a"]}))
    with pytest.raises(ValueError, match="exclusion|retain"):
        validate_outputs(value, {"dataset": before}, {"dataset": after})


def test_scoped_retention_requires_actual_effect_validator():
    value = contract()
    value["action_scope"].update(intent="retain", interpretation="none")
    value["validation"] = {"comparator": "json", "invariants": [], "counts_layer": "X", "qc_metrics": []}
    with pytest.raises(ValueError, match="scope|effect|retention"):
        MethodContract.from_dict(value)


def test_qc_review_with_declared_semantics_remains_noncausal(tmp_path):
    from devbench.analysis.method_validation import validate_outputs
    before, after, output = artifacts(tmp_path)
    value = contract("qc_review")
    value["validation"]["diagnostic_policy"]["measurement_semantics"] = {"measurement": "Synthetic continuous intensity"}
    output["evidence_scope"] = "qc_review"
    output["records"][0]["reason"] = "No QC review flag under the supplied reference"
    output["records"][1]["reason"] = "Warrants QC review"
    after.write_bytes(canonical_json(output))
    result = validate_outputs(value, {"dataset": before}, {"dataset": after})
    assert result["evidence_scope"] == "qc_review" and not result["removed_ids"]
    output["records"][1]["reason"] = "Confirmed technical damage"
    after.write_bytes(canonical_json(output))
    with pytest.raises(ValueError, match="causation|damage"):
        validate_outputs(value, {"dataset": before}, {"dataset": after})


def test_evidence_reference_objects_rejected_as_validation_error(tmp_path):
    with pytest.raises(ValueError):
        validate(tmp_path, lambda x: x["records"][0].update(evidence_refs=[{}]))


def test_whitespace_measurement_semantics_do_not_authorize_qc_review():
    value = contract("qc_review")
    value["validation"]["diagnostic_policy"]["measurement_semantics"] = {"measurement": "   "}
    with pytest.raises(ValueError, match="semantics"):
        MethodContract.from_dict(value)


def test_null_choice_carries_separate_verified_diagnostic_next_step(tmp_path):
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from tests.test_extensible_methods import custom, service
    from tests.test_method_action_scope import item
    from tests.test_method_planner import Selector, recommendation, trial_spec
    api = service(tmp_path)
    value, source, lock = custom(tmp_path)
    declared = contract()
    for key in ("action_scope", "inputs", "outputs", "validation"):
        value[key] = declared[key]
    method = api.register_method(value, source=source, environment_lock=lock, actor="scientist")
    before, _, output = artifacts(tmp_path)
    artifact = api.import_artifact(before, artifact_type="json", actor="scientist")
    analysis = api.create_analysis("Synthetic diagnostic fixture", actor="scientist")
    branch = api.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="scientist")
    step = api.add_step(branch["branch_id"], method["version_id"], {"dataset": artifact["artifact_id"]},
                        {"threshold": 4}, actor="scientist")
    planner = ScientificMethodPlanner(api)
    state = planner.create({"scientific_objective": "Inspect numerical atypicality", "computational_task": "quality_control"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [artifact["artifact_id"]]},
        actor="scientist", data_classification="synthetic")
    selector = Selector({"candidates": [item()]}, {"bindings": [{"candidate": "Unknown QC",
        "method_version_id": method["version_id"], "discovery": None}]})
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    planner.resolve_capabilities(state["session_id"], selector, [method["version_id"]], actor="scientist")
    spec = planner.declare_trial(state["session_id"], branch["branch_id"], trial_spec(step), actor="scientist")
    class Executor:
        def execute(self, contract, inputs, parameters, source, lock, output_dir):
            (output_dir / "diagnostics.json").write_bytes(canonical_json(output))
            return {"exit_code": 0, "runtime": "synthetic_fixture"}
    rows = planner.run_trial(state["session_id"], spec["id"], actor="scientist", allow_data_execution=True,
                             executor=Executor())
    assert rows[0]["status"] == "success"
    frozen = planner.get(state["session_id"])
    assessment = planner.evidence_assessment(state["session_id"])
    assert planner.get(state["session_id"]) == frozen
    next_step = assessment["diagnostic_next_steps"][0]
    assert next_step["flagged_count"] == 1 and next_step["unavailable_count"] == 0
    assert next_step["exclusions_authorized"] is False
    assert next_step["run_id"] == rows[0]["provenance"]["run_id"]
    assert not assessment["candidates"][0]["meets_evidence_floor"]
    answer = planner.recommend(state["session_id"], Selector(recommendation(None)), actor="scientist")
    assert answer["choice"] is None and "diagnostic_next_steps" not in answer
    with pytest.raises(ValueError, match="evidence|comparison|comparable"):
        planner.recommend(state["session_id"], Selector(recommendation()), actor="scientist")


@pytest.mark.parametrize("reason", ["This observation is technically damaged", "This is damage from processing",
    "Damaged", "Warrants QC review; this observation is damaged", "This is caused by technical failure"])
def test_qc_review_admits_only_canonical_review_scope(tmp_path, reason):
    from devbench.analysis.method_validation import validate_outputs
    before, after, output = artifacts(tmp_path)
    value = contract("qc_review")
    value["validation"]["diagnostic_policy"]["measurement_semantics"] = {"measurement": "Synthetic intensity"}
    output["evidence_scope"] = "qc_review"
    output["records"][0]["reason"] = "No QC review flag under the supplied reference"
    output["records"][1]["reason"] = reason
    after.write_bytes(canonical_json(output))
    with pytest.raises(ValueError, match="canonical|scope"):
        validate_outputs(value, {"dataset": before}, {"dataset": after})


def test_coordinator_passes_declared_custom_sample_policy(tmp_path):
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from tests.test_extensible_methods import custom, service
    from tests.test_method_action_scope import item
    from tests.test_method_planner import Selector
    api = service(tmp_path)
    value, source, lock = custom(tmp_path)
    declared = contract()
    declared["validation"]["sample_key"] = "donor"
    for key in ("action_scope", "inputs", "outputs", "validation"):
        value[key] = declared[key]
    method = api.register_method(value, source=source, environment_lock=lock, actor="scientist")
    before, *_ = artifacts(tmp_path)
    data = json.loads(before.read_bytes())
    for row in data["observations"]:
        row["donor"] = row.pop("sample")
    before.write_bytes(canonical_json(data))
    artifact = api.import_artifact(before, artifact_type="json", actor="scientist")
    planner = ScientificMethodPlanner(api)
    state = planner.create({"scientific_objective": "Inspect typed measurements", "computational_task": "quality_control"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [artifact["artifact_id"]]},
        actor="scientist", data_classification="synthetic")
    selector = Selector({"candidates": [item()]}, {"bindings": [{"candidate": "Unknown QC",
        "method_version_id": method["version_id"], "discovery": None}]})
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    resolved = planner.resolve_capabilities(state["session_id"], selector, [method["version_id"]], actor="scientist")
    assert resolved[0]["applicability"] == "eligible"
    refreshed = planner._current_candidate(planner.get(state["session_id"]), "Unknown QC", actor="scientist")
    assert refreshed["applicability"] == "eligible"
    record = api.get("method_prerequisite_inspection", refreshed["prerequisite_inspections"][0])
    assert record["input_policy"]["sample_key"] == "donor"


def test_supported_scoped_anndata_uses_bounded_declared_representation(tmp_path):
    import anndata as ad
    import numpy as np
    import pandas as pd
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from tests.test_extensible_methods import custom, service
    from tests.test_method_action_scope import item
    from tests.test_method_planner import Selector
    api = service(tmp_path)
    value, source, lock = custom(tmp_path, comparator="single_cell_qc")
    value["inputs"]["dataset"]["artifact_type"] = "anndata"
    value["outputs"]["dataset"].update(artifact_type="anndata", path="/outputs/result.h5ad")
    value["action_scope"] = {"format": "devbench.method-action/1", "intent": "retain", "interpretation": "none", "exclusions": False}
    value["validation"].update(sample_key="donor", qc_metrics=["total_counts"])
    method = api.register_method(value, source=source, environment_lock=lock, actor="scientist")
    path = tmp_path / "input.h5ad"
    ad.AnnData(np.array([[1., 2.], [4., 5.]]),
        obs=pd.DataFrame({"donor": ["a", "b"], "total_counts": [3., 9.]}, index=["c1", "c2"]),
        var=pd.DataFrame(index=["g1", "g2"])).write_h5ad(path)
    artifact = api.import_artifact(path, artifact_type="anndata", actor="scientist")
    planner = ScientificMethodPlanner(api)
    state = planner.create({"scientific_objective": "Preserve typed measurements", "computational_task": "quality_control"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [artifact["artifact_id"]]},
        actor="scientist", data_classification="synthetic")
    selector = Selector({"candidates": [item("retain", "none")]}, {"bindings": [{"candidate": "Unknown QC",
        "method_version_id": method["version_id"], "discovery": None}]})
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    resolved = planner.resolve_capabilities(state["session_id"], selector, [method["version_id"]], actor="scientist")
    assert resolved[0]["applicability"] == "eligible"


@pytest.mark.parametrize("reason", ["Reference unavailable", "Measurement unavailable", "Score unavailable", "Insufficient evidence"])
def test_canonical_unavailable_reasons_retain_explicit_absence(tmp_path, reason):
    from devharness_core.methods import diagnostic_reasons
    result = validate(tmp_path, lambda x: x["records"][0].update(status="unavailable", score=None, reason=reason))
    assert result["unavailable_ids"] == ["opaque-a"]
    assert reason in diagnostic_reasons("qc_review", "unavailable")


@pytest.mark.parametrize("reason", ["Unusual under the supplied representation; proven damage", "Within reference",
                                   "Warrants QC review", "Unusual under the supplied representation "])
def test_reason_vocabulary_cannot_widen_status_or_numerical_scope(tmp_path, reason):
    with pytest.raises(ValueError, match="canonical"):
        validate(tmp_path, lambda x: x["records"][1].update(reason=reason))


def test_preimplementation_roles_must_be_explicit_coordinator_metadata(tmp_path):
    from tests.test_method_planner import setup
    from tests.test_method_action_scope import item, summary
    planner, *_ = setup(tmp_path)
    before, *_ = artifacts(tmp_path)
    data = json.loads(before.read_bytes())
    for row in data["observations"]:
        row["donor"] = row.pop("sample")
    before.write_bytes(canonical_json(data))
    artifact = planner.methods.import_artifact(before, artifact_type="json", actor="scientist")
    row = item()
    unbound = planner._inspect_applicability(row, summary(), [artifact["artifact_id"]], actor="scientist")
    assert unbound["applicability"] == "blocked_unknown"
    declared = summary(observation_input_policy={"sample_key": "donor", "qc_metrics": ["measurement"]})
    inspected = planner._inspect_applicability(row, declared, [artifact["artifact_id"]], actor="scientist")
    assert inspected["applicability"] == "eligible"


def test_anndata_numeric_prerequisites_follow_declared_layer_and_failures(tmp_path):
    import anndata as ad
    import numpy as np
    import pandas as pd
    from devbench.analysis.method_validation import observation_prerequisite_inspection
    data = ad.AnnData(np.full((2, 2), np.nan),
        obs=pd.DataFrame({"donor": ["a", "b"], "qc": [1., 3.]}, index=["a", "b"]),
        var=pd.DataFrame(index=["g1", "g2"]))
    data.layers["counts"] = np.array([[1., 2.], [3., 4.]])
    path = tmp_path / "declared-layer.h5ad"
    data.write_h5ad(path)
    fields = ["metrics_finite", "measurement_rank", "measurement_variance_positive"]
    policy = {"counts_layer": "counts", "sample_key": "donor", "qc_metrics": ["qc"]}
    result = observation_prerequisite_inspection(path, fields, policy=policy, artifact_type="anndata")
    assert result["observed"] == {"metrics_finite": True, "measurement_rank": 1, "measurement_variance_positive": True}
    failed = observation_prerequisite_inspection(path, fields, policy={**policy, "counts_layer": "X"}, artifact_type="anndata")
    assert failed["failures"] and not failed["observed"]


@pytest.mark.parametrize("name", ["flag_rate", "available_fraction", "by_measurement"])
def test_valid_measurement_names_cannot_overwrite_aggregate_diagnostics(tmp_path, name):
    from devbench.analysis.method_validation import validate_outputs
    before, after, output = artifacts(tmp_path)
    data = json.loads(before.read_bytes())
    for row in data["observations"]:
        row[name] = row.pop("measurement")
    before.write_bytes(canonical_json(data))
    output["input_sha256"] = hashlib.sha256(before.read_bytes()).hexdigest()
    for i, row in enumerate(output["records"]):
        row["evidence_refs"] = [f"/observations/{i}/{name}"]
    after.write_bytes(canonical_json(output))
    value = contract()
    value["validation"]["qc_metrics"] = [name]
    result = validate_outputs(value, {"dataset": before}, {"dataset": after})
    assert result["diagnostic_measurements"]["flag_rate"] == .5
    assert result["diagnostic_measurements"]["available_fraction"] == 1.
    assert result["diagnostic_measurements"]["by_measurement"][name]["flagged_mean"] == 3.


def test_secondary_filtered_output_cannot_bypass_nonexclusion_diagnostics(tmp_path):
    from devbench.analysis.method_validation import validate_outputs
    before, after, _ = artifacts(tmp_path)
    secondary = tmp_path / "secondary.json"
    secondary.write_bytes(canonical_json({"retained_ids": ["opaque-a"]}))
    value = contract()
    value["outputs"]["secondary"] = {"artifact_type": "json", "required": True, "path": "/outputs/secondary.json"}
    with pytest.raises(ValueError, match="output|dataset"):
        validate_outputs(value, {"dataset": before}, {"dataset": after, "secondary": secondary})


@pytest.mark.parametrize("kind", ["json", "anndata", "file"])
@pytest.mark.parametrize("intent", ["retain", "diagnostic_flagging"])
def test_scoped_nonexclusion_outputs_require_exact_validated_dataset_port(kind, intent):
    value = contract()
    if intent == "retain":
        value["action_scope"].update(intent="retain", interpretation="none")
        value["validation"].pop("diagnostic_policy")
        value["validation"]["comparator"] = "observation_selection"
    value["outputs"]["secondary"] = {"artifact_type": kind, "required": True, "path": "/outputs/secondary.json"}
    with pytest.raises(ValueError, match="output|dataset"):
        MethodContract.from_dict(value)


@pytest.mark.parametrize("mutation", ["X", "qc", "layer", "embedding", "raw"])
def test_scoped_anndata_retention_preserves_existing_measurements_and_representations(tmp_path, mutation):
    import anndata as ad
    import numpy as np
    import pandas as pd
    from devbench.analysis.method_validation import validate_outputs
    from tests.test_extensible_methods import custom
    value, *_ = custom(tmp_path, comparator="single_cell_qc")
    value["inputs"]["dataset"]["artifact_type"] = "anndata"
    value["outputs"]["dataset"].update(artifact_type="anndata", path="/outputs/result.h5ad")
    value["action_scope"] = {"format": "devbench.method-action/1", "intent": "retain", "interpretation": "none", "exclusions": False}
    value["validation"].update(counts_layer="counts", sample_key="donor", qc_metrics=["qc"])
    original = ad.AnnData(np.array([[1., 2.], [3., 4.]]),
        obs=pd.DataFrame({"donor": ["a", "b"], "qc": [3., 7.]}, index=["a", "b"]),
        var=pd.DataFrame(index=["g1", "g2"]))
    original.layers["counts"] = original.X.copy()
    original.layers["normalized"] = original.X / 2
    original.obsm["embedding"] = np.array([[0., 1.], [1., 0.]])
    original.raw = original.copy()
    changed = original.copy()
    if mutation == "X":
        changed.X *= 100
    elif mutation == "qc":
        changed.obs["qc"] *= 100
    elif mutation == "layer":
        changed.layers["normalized"] *= 100
    elif mutation == "embedding":
        changed.obsm["embedding"] *= 100
    else:
        changed.raw = None
    before, after = tmp_path / "before.h5ad", tmp_path / "after.h5ad"
    original.write_h5ad(before)
    changed.write_h5ad(after)
    with pytest.raises(ValueError, match="retain|representation|measurement"):
        validate_outputs(value, {"dataset": before}, {"dataset": after})
    unchanged = tmp_path / "reserialized.h5ad"
    original.write_h5ad(unchanged)
    assert validate_outputs(value, {"dataset": before}, {"dataset": unchanged})["passed"]
    value.pop("action_scope")
    # Historical comparator semantics preserve only the declared counts layer.
    assert validate_outputs(value, {"dataset": before}, {"dataset": after})["passed"]


def test_legacy_contract_secondary_outputs_remain_unchanged():
    value = _payload()
    value["outputs"]["secondary"] = {"artifact_type": "file", "required": True, "path": "/outputs/secondary.txt"}
    assert MethodContract.from_dict(value).to_dict() == value


def test_group_flag_rates_follow_declared_custom_sample_column(tmp_path):
    from devbench.analysis.method_validation import validate_outputs
    before, after, output = artifacts(tmp_path)
    data = json.loads(before.read_bytes())
    for row in data["observations"]:
        row["donor"] = row["sample"]
        row["sample"] = "other-metadata"
    before.write_bytes(canonical_json(data))
    output["input_sha256"] = hashlib.sha256(before.read_bytes()).hexdigest()
    after.write_bytes(canonical_json(output))
    value = contract()
    value["validation"]["sample_key"] = "donor"
    result = validate_outputs(value, {"dataset": before}, {"dataset": after})
    assert result["group_flag_rates"] == {"donor": {"a": 0., "b": 1.}}


@pytest.mark.parametrize("slot", ["varm", "raw_varm"])
def test_scoped_retention_accepts_complete_dataframe_slots_without_column_slicing(tmp_path, slot):
    import anndata as ad
    import numpy as np
    import pandas as pd
    from devbench.analysis.method_validation import validate_outputs
    from tests.test_extensible_methods import custom
    value, *_ = custom(tmp_path, comparator="single_cell_qc")
    value["inputs"]["dataset"]["artifact_type"] = "anndata"
    value["outputs"]["dataset"].update(artifact_type="anndata", path="/outputs/result.h5ad")
    value["action_scope"] = {"format": "devbench.method-action/1", "intent": "retain", "interpretation": "none", "exclusions": False}
    original = ad.AnnData(np.array([[1., 2.], [3., 4.]]), var=pd.DataFrame(index=["g1", "g2"]))
    loadings = pd.DataFrame([[.1, .2], [.3, .4]], columns=["PC1", "PC2"], index=original.var_names)
    original.varm["loadings"] = loadings
    if slot == "raw_varm":
        original.raw = original.copy()
        del original.varm["loadings"]
    before, after = tmp_path / "df-before.h5ad", tmp_path / "df-after.h5ad"
    original.write_h5ad(before)
    original.copy().write_h5ad(after)
    assert validate_outputs(value, {"dataset": before}, {"dataset": after})["passed"]
    changed = original.copy()
    target = changed.raw.varm if slot == "raw_varm" else changed.varm
    target["loadings"].iloc[0, 0] = 99.
    changed.write_h5ad(after)
    with pytest.raises(ValueError, match="representation|measurement"):
        validate_outputs(value, {"dataset": before}, {"dataset": after})


@pytest.mark.parametrize("mutation", ["direct", "nested", "remove"])
def test_scoped_retention_preserves_bounded_uns_measurements(tmp_path, mutation):
    import anndata as ad
    import numpy as np
    from devbench.analysis.method_validation import validate_outputs
    from tests.test_extensible_methods import custom
    value, *_ = custom(tmp_path, comparator="single_cell_qc")
    value["inputs"]["dataset"]["artifact_type"] = "anndata"
    value["outputs"]["dataset"].update(artifact_type="anndata", path="/outputs/result.h5ad")
    value["action_scope"] = {"format": "devbench.method-action/1", "intent": "retain", "interpretation": "none", "exclusions": False}
    original = ad.AnnData(np.array([[1., 2.], [3., 4.]]))
    original.uns["public_gene_signature_measurements"] = np.array([[.1, .2], [.3, .4]])
    original.uns["nested"] = {"measurements": {"values": np.array([1., 2.])}, "labels": ["a", "b"]}
    before, after = tmp_path / "uns-before.h5ad", tmp_path / "uns-after.h5ad"
    original.write_h5ad(before)
    original.copy().write_h5ad(after)
    assert validate_outputs(value, {"dataset": before}, {"dataset": after})["passed"]
    changed = original.copy()
    if mutation == "direct":
        changed.uns["public_gene_signature_measurements"] *= 100
    elif mutation == "nested":
        changed.uns["nested"]["measurements"]["values"] *= 100
    else:
        del changed.uns["public_gene_signature_measurements"]
    changed.write_h5ad(after)
    with pytest.raises(ValueError, match="representation|measurement"):
        validate_outputs(value, {"dataset": before}, {"dataset": after})
