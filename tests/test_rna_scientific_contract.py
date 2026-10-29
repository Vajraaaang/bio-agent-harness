"""Synthetic P0 contracts and entrypoint integration; no scientific qualification."""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from devbench.analysis.method_planner import ScientificMethodPlanner
from devbench.analysis.workflow_state import initial_state, planning_binding
from devbench.analysis.workflow_scientific_reviews import stage_material
from tests.test_extensible_methods import prepared
from tests.test_method_planner import Selector
from tests.test_scientific_plan_context import workspace_context


TASK = {"scientific_objective": "Assess supplied synthetic observations", "computational_task": "quality_control"}


def scientific_contract(methods, artifact_ids):
    refs = [{key: methods.get("artifact", identity)[key]
             for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")} for identity in artifact_ids]
    return {
        "record_format": "devbench.rna-scientific-contract/1", "contract_id": "synthetic-plan",
        "input_artifact_refs": refs,
        "sources": [{"source_id": "request", "kind": "user_request", "text": TASK["scientific_objective"],
                     "reference": "Synthetic caller-declared request"}],
        "objective": {"statement": TASK["scientific_objective"], "estimand": None, "origin": "user",
                      "source_id": "request", "source_excerpt": TASK["scientific_objective"]},
        "requirements": [],
        "biological_units": [{"unit_id": "library-1", "kind": "library", "parent_ids": [],
                              "independence": "unknown", "evidence_source_ids": [], "condition": None, "time": None}],
        "assay": {"representation": "normalized_expression", "orientation": "feature_by_sample",
                  "feature_id_type": "synthetic_feature", "observation_id_type": "library",
                  "measurement_unit": "synthetic continuous value", "count_provenance": None,
                  "estimated_count_import": None,
                  "transformations": [{"name": "normalization", "version": "fixture-v1", "source_ids": ["request"]}]},
        "method_assumptions": [{"statement": "Synthetic controls do not establish biological validity",
                                "status": "unknown", "evidence_source_ids": []}],
        "statistical_plan": {"formula": None, "contrast": None, "normalization": None, "test": None,
                             "filtering": [], "multiplicity": None, "policy_source_ids": []},
        "execution": {"tool_refs": [], "environment_refs": [], "reference_refs": [],
                      "resource_requirements": {"memory_mb": 256, "cpu_threads": 1, "timeout_seconds": 30},
                      "capability_requirements": ["local numerical computation"]},
        "replay": {"environment_refs": [], "comparisons": [], "seeds": []},
        "unknowns": [{"field": "biological_units", "reason": "Donor identity was not supplied", "blocking": True},
                     {"field": "objective.estimand", "reason": "Descriptive objective only", "blocking": False}],
        "human_authorship_authenticated": False, "execution_authority_granted": False,
        "scientific_validity_established": False,
    }


def prepared_contract(tmp_path):
    methods, _, _, step, *_ = prepared(tmp_path)
    ids = list(step["inputs"].values())
    summary = {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": ids}
    return ScientificMethodPlanner(methods), summary, scientific_contract(methods, ids)


def validate(planner, summary, value, requirements=()):
    api = importlib.import_module("devbench.analysis.rna_scientific_contract")
    return api.validate_scientific_contract(value, methods=planner.methods, artifact_ids=summary["artifact_ids"],
        task=TASK, user_parameter_requirements=list(requirements))


def test_integrated_companion_persists_projects_and_exports_separately(tmp_path):
    planner, summary, companion = prepared_contract(tmp_path)
    state = planner.create(TASK, summary, actor="researcher", scientific_contract=companion)
    retained = planner.get(state["session_id"])
    assert retained["scientific_contract"] == companion
    assert len(retained["scientific_contract_sha256"]) == 64
    assert set(planner.export(state["session_id"])) == {"task", "dataset_summary", "candidates", "experiments", "results", "recommendation"}
    exported = planner.export_scientific_contract(state["session_id"])
    assert exported["scientific_contract"] == companion
    assert exported["sha256"] == state["scientific_contract_sha256"]
    companion["unknowns"][0]["reason"] = "caller mutation"
    assert planner.get(state["session_id"])["scientific_contract"] != companion
    selector = Selector({"candidates": []})
    planner.generate_candidates(state["session_id"], selector, actor="researcher")
    projection = selector.contexts[0]["scientific_contract"]
    assert projection["objective"]["statement"] == TASK["scientific_objective"]
    assert "execution" not in projection and "replay" not in projection
    assert projection["human_authorship_authenticated"] is False


def test_integrated_companion_binds_checkpoint_and_frozen_review(tmp_path):
    planner, summary, companion = prepared_contract(tmp_path)
    state = planner.create(TASK, summary, actor="researcher", scientific_contract=companion)
    baseline = planning_binding(state)
    changed = deepcopy(state)
    changed["scientific_contract"]["unknowns"][0]["reason"] = "different biological limitation"
    from devbench.study.contracts import content_sha256
    changed["scientific_contract_sha256"] = content_sha256(changed["scientific_contract"])
    assert planning_binding(changed) != baseline
    material = stage_material(state, "candidate_suitability")
    assert material["scientific_contract"]["assay"]["representation"] == "normalized_expression"
    assert "execution" not in material["scientific_contract"]
    assert stage_material(changed, "candidate_suitability") != material
    checkpoint = initial_state(state, companion["input_artifact_refs"])
    assert "scientific_contract" not in checkpoint
    assert len(json.dumps(checkpoint)) < 64000


def test_legacy_path_adds_no_fields_or_binding_changes(tmp_path):
    from devbench.analysis.workflow_state import digest
    planner, summary, _ = prepared_contract(tmp_path)
    state = planner.create(TASK, summary, actor="researcher")
    assert "scientific_contract" not in state and "scientific_contract_sha256" not in state
    assert planner.export_scientific_contract(state["session_id"]) is None
    assert planning_binding(state) == digest({"context": state["context"], "user_parameter_requirements": [],
        "data_classification": "unclassified", "limits": state["limits"], "scientific_anchors": None})
    assert "scientific_contract" not in stage_material(state, "candidate_suitability")


@pytest.mark.parametrize("mutation", [
    lambda c: c.update(unexpected=True),
    lambda c: c["objective"].update(unexpected=True),
    lambda c: c["execution"]["resource_requirements"].update(memory_mb=float("inf")),
    lambda c: c["unknowns"].append({"field": "x", "reason": "x", "blocking": "yes"}),
    lambda c: c["sources"][0].update(text="x" * 20000),
    lambda c: c.update(human_authorship_authenticated=True),
    lambda c: c.update(execution_authority_granted=True),
    lambda c: c["objective"].update(source_id="missing"),
    lambda c: c["objective"].update(statement="Unrequested objective"),
    lambda c: c["sources"][0].update(kind="assistant_suggestion"),
    lambda c: c["objective"].update(source_excerpt="not in source"),
    lambda c: c["input_artifact_refs"][0].update(sha256="f" * 64),
    lambda c: c["input_artifact_refs"].clear(),
    lambda c: c["input_artifact_refs"].append(deepcopy(c["input_artifact_refs"][0])),
    lambda c: c["biological_units"][0].update(independence="independent"),
    lambda c: c["biological_units"][0].update(parent_ids=["missing"]),
    lambda c: c["biological_units"][0].update(parent_ids=["library-1"]),
    lambda c: c["unknowns"].clear(),
    lambda c: c["assay"].update(orientation="unknown-positional-zip"),
    lambda c: c["assay"].update(representation="estimated_counts"),
])
def test_invalid_or_unbound_contract_is_rejected(tmp_path, mutation):
    planner, summary, companion = prepared_contract(tmp_path)
    mutation(companion)
    with pytest.raises(ValueError):
        validate(planner, summary, companion)


def test_explicit_parameter_requirement_requires_exact_source_and_value_match(tmp_path):
    planner, summary, companion = prepared_contract(tmp_path)
    companion["sources"].append({"source_id": "alpha-request", "kind": "user_request",
        "text": "Use alpha 0.05", "reference": "Synthetic declared request"})
    companion["requirements"] = [{"name": "alpha", "value": 0.05, "origin": "user",
        "source_id": "alpha-request", "source_excerpt": "Use alpha 0.05"}]
    requirement = {"name": "alpha", "value": 0.05, "evidence": "Use alpha 0.05"}
    assert validate(planner, summary, companion, [requirement])["human_authorship_authenticated"] is False
    with pytest.raises(ValueError, match="requirement"):
        validate(planner, summary, companion)
    wrong = {**requirement, "value": "0.05"}
    with pytest.raises(ValueError, match="requirement"):
        validate(planner, summary, companion, [wrong])
    companion["sources"][-1]["kind"] = "assistant_suggestion"
    with pytest.raises(ValueError, match="user|source"):
        validate(planner, summary, companion, [requirement])


def test_estimated_counts_have_explicit_import_and_offset_semantics(tmp_path):
    planner, summary, companion = prepared_contract(tmp_path)
    companion["assay"].update(representation="estimated_counts", count_provenance="Quantifier estimates; not rounded TPM",
        estimated_count_import={"quantifier": {"name": "salmon", "version": "fixture-1", "sha256": None},
            "importer": {"name": "tximport", "version": "fixture-1", "sha256": None},
            "offset_strategy": "sample-specific effective lengths", "source_artifact_ids": summary["artifact_ids"]})
    assert validate(planner, summary, companion)["assay"]["representation"] == "estimated_counts"
    # No alteration of the existing staged direct-count contract.
    from devbench.analysis.rna_input_contract import validate_bulk_rna_input
    with pytest.raises(ValueError, match="integer"):
        validate_bulk_rna_input({"format": "devbench.bulk-rna-input/1", "representation": "raw_counts",
            "axes": ["gene", "sample"], "provenance": {}, "biological_independence": None},
            gene_ids=["g"], sample_ids=["s"], metadata_rows=[{"sample_id": "s"}], matrix=[[1.5]])


def test_specimens_libraries_and_capture_units_do_not_become_independent_donors(tmp_path):
    planner, summary, companion = prepared_contract(tmp_path)
    companion["biological_units"] = [
        {"unit_id": "donor-1", "kind": "donor", "parent_ids": [], "independence": "unknown",
         "evidence_source_ids": [], "condition": None, "time": None},
        {"unit_id": "specimen-1", "kind": "specimen", "parent_ids": ["donor-1"], "independence": "dependent",
         "evidence_source_ids": [], "condition": "treated", "time": "day1"},
        {"unit_id": "capture-1", "kind": "capture", "parent_ids": ["specimen-1"], "independence": "dependent",
         "evidence_source_ids": [], "condition": None, "time": None},
        {"unit_id": "library-1", "kind": "library", "parent_ids": ["capture-1"], "independence": "dependent",
         "evidence_source_ids": [], "condition": None, "time": None}]
    result = validate(planner, summary, companion)
    assert result["biological_units"][0]["independence"] == "unknown"
    assert result["unknowns"][0]["blocking"] is True


def test_http_forwards_optional_scientific_contract_without_reinterpreting_task():
    workspace, body, calls, task = workspace_context()
    marker = {"record_format": "devbench.rna-scientific-contract/1", "contract_id": "caller-value"}
    workspace._create_plan({**body, "scientific_contract": marker}, "researcher")
    assert calls[0]["scientific_contract"] == marker
    assert calls[0]["task"] == task


def test_cli_forwards_optional_scientific_contract(tmp_path, monkeypatch, capsys):
    from devbench.analysis import method_planner_cli
    calls = []
    marker = {"record_format": "devbench.rna-scientific-contract/1", "contract_id": "caller-value"}
    request = {"task": TASK, "dataset_summary": {"observed": {}, "inferred": {}, "unknowns": []},
               "scientific_contract": marker}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    monkeypatch.setattr(method_planner_cli, "MethodService", lambda *args, **kw: object())
    def create(*args, **options):
        calls.append(options)
        return {"session_id": "f" * 32}
    monkeypatch.setattr(method_planner_cli, "ScientificMethodPlanner", lambda *args: SimpleNamespace(create=create))
    method_planner_cli.main(["--workspace", str(tmp_path / "store"), "create", "--request", str(path)])
    assert calls[0]["scientific_contract"] == marker
    assert json.loads(capsys.readouterr().out)["session_id"] == "f" * 32


def test_export_detects_changed_registered_artifact_and_contract_digest(tmp_path):
    planner, summary, companion = prepared_contract(tmp_path)
    state = planner.create(TASK, summary, actor="researcher", scientific_contract=companion)
    bad = deepcopy(state)
    bad["scientific_contract"]["unknowns"][0]["reason"] = "rewritten history"
    with planner.methods._db() as db:
        planner.methods._put(db, "method_plan", state["session_id"], bad, update=True)
    with pytest.raises(ValueError, match="digest|changed"):
        planner.export_scientific_contract(state["session_id"])


@pytest.mark.parametrize("field", ["human_authorship_authenticated", "execution_authority_granted", "scientific_validity_established"])
def test_authority_denials_require_boolean_false_not_zero(tmp_path, field):
    planner, summary, companion = prepared_contract(tmp_path)
    companion[field] = 0
    with pytest.raises(ValueError, match="false|boolean"):
        validate(planner, summary, companion)


@pytest.mark.parametrize("field", ["human_authorship_authenticated", "execution_authority_granted", "scientific_validity_established"])
def test_direct_typed_contract_authority_denials_require_boolean_false(tmp_path, field):
    planner, summary, companion = prepared_contract(tmp_path)
    companion[field] = 0
    api = importlib.import_module("devbench.analysis.rna_scientific_contract")
    with pytest.raises(ValueError, match="false|boolean"):
        api.ScientificContract.model_validate_json(json.dumps(companion))


def test_complete_statistical_and_replay_contract_survives_review_projection(tmp_path):
    planner, summary, companion = prepared_contract(tmp_path)
    companion["statistical_plan"] = {"formula": "~ donor + treatment",
        "contrast": {"factor": "treatment", "numerator": "treated", "denominator": "control"},
        "normalization": "Declared synthetic fixture normalization", "test": "Declared fixture test",
        "filtering": ["No post-hoc exclusions"], "multiplicity": {"method": "BH", "family_id": "all-tested",
            "hypothesis_ids_artifact_id": summary["artifact_ids"][0], "alpha": 0.05}, "policy_source_ids": ["request"]}
    companion["execution"]["tool_refs"] = [{"name": "fixture-engine", "version": "fixture-v1", "sha256": "a" * 64}]
    companion["replay"] = {"environment_refs": [{"name": "fixture-environment", "version": "1", "sha256": "b" * 64}],
        "comparisons": [{"output_name": "gene-statistics", "identifiers": ["gene_id", "contrast"],
                         "absolute_tolerance": 1e-10, "relative_tolerance": 1e-8, "missingness": "exact_mask"}], "seeds": [17]}
    state = planner.create(TASK, summary, actor="researcher", scientific_contract=companion)
    material = stage_material(state, "comparison_design")
    assert material["scientific_contract"] == companion
    assert planner.export_scientific_contract(state["session_id"])["scientific_contract"] == companion


def test_registered_input_mutation_is_rejected_on_separate_export(tmp_path):
    planner, summary, companion = prepared_contract(tmp_path)
    state = planner.create(TASK, summary, actor="researcher", scientific_contract=companion)
    path = planner.methods.artifact_path(summary["artifact_ids"][0])
    path.chmod(0o600)  # Deliberate corruption of this test's generated immutable object.
    path.write_text('{"changed":true}')
    with pytest.raises(ValueError):
        planner.export_scientific_contract(state["session_id"])


def test_get_and_review_reject_changed_companion_digest(tmp_path):
    planner, summary, companion = prepared_contract(tmp_path)
    state = planner.create(TASK, summary, actor="researcher", scientific_contract=companion)
    changed = deepcopy(state)
    changed["scientific_contract"]["unknowns"][0]["reason"] = "changed after freeze"
    with pytest.raises(ValueError, match="digest|changed"):
        stage_material(changed, "candidate_suitability")
    with planner.methods._db() as db:
        planner.methods._put(db, "method_plan", state["session_id"], changed, update=True)
    with pytest.raises(ValueError, match="digest|changed"):
        planner.get(state["session_id"])


def registered_bulk_contract(tmp_path):
    planner, _, _ = prepared_contract(tmp_path)
    matrix = tmp_path / "matrix.json"
    metadata = tmp_path / "metadata.json"
    matrix.write_text(json.dumps({"gene_ids": ["g1"], "sample_ids": ["library-1"], "matrix": [[7]]}))
    metadata.write_text(json.dumps({"samples": [{"sample_id": "library-1"}]}))
    refs = [planner.methods.import_artifact(path, artifact_type="json", actor="researcher") for path in (matrix, metadata)]
    ids = [ref["artifact_id"] for ref in refs]
    summary = {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": ids}
    binding = {"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": ids[0], "metadata_artifact_id": ids[1],
        "declarations": {"format": "devbench.bulk-rna-input/1", "representation": "raw_counts", "axes": ["gene", "sample"],
                         "provenance": {"source": "synthetic direct counts"}, "biological_independence": None}}
    from devbench.analysis.rna_input_contract import inspect_registered_bulk_rna_input
    inspection = inspect_registered_bulk_rna_input(planner.methods, binding)
    companion = scientific_contract(planner.methods, ids)
    companion["assay"].update(representation="direct_counts", count_provenance="Synthetic direct counts",
        feature_ids_sha256=inspection["gene_ids_sha256"], observation_ids_sha256=inspection["sample_ids_sha256"],
        metadata_sha256=inspection["metadata_sha256"])
    return planner, summary, binding, companion


@pytest.mark.parametrize("field,value", [("representation", "normalized_expression"),
    ("orientation", "sample_by_feature"), ("feature_ids_sha256", "a" * 64),
    ("observation_ids_sha256", "a" * 64), ("metadata_sha256", "a" * 64)])
def test_actual_registered_rna_declaration_rejects_contradictory_companion(tmp_path, field, value):
    planner, summary, binding, companion = registered_bulk_contract(tmp_path)
    companion["assay"][field] = value
    with pytest.raises(ValueError, match="assay|identifier|representation|orientation|metadata"):
        planner.create(TASK, summary, actor="researcher", rna_input_contract=binding, scientific_contract=companion)


def test_actual_registered_rna_unknown_independence_cannot_be_upgraded(tmp_path):
    planner, summary, binding, companion = registered_bulk_contract(tmp_path)
    companion["biological_units"] = [{"unit_id": "donor", "kind": "donor", "parent_ids": [],
        "independence": "independent", "evidence_source_ids": ["request"], "condition": None, "time": None}]
    with pytest.raises(ValueError, match="independence"):
        planner.create(TASK, summary, actor="researcher", rna_input_contract=binding, scientific_contract=companion)


@pytest.mark.parametrize('declaration', [
    {'independent_units': False}, {}, {'independent_units': None},
    {'independent_units': 1}, {'independent_units': 'true'}, {'independent_units': 0}])
def test_registered_independence_declaration_cannot_be_upgraded(tmp_path, declaration):
    planner, summary, binding, companion = registered_bulk_contract(tmp_path)
    binding['declarations']['biological_independence'] = declaration
    companion['biological_units'] = [{'unit_id': 'donor', 'kind': 'donor', 'parent_ids': [],
        'independence': 'independent', 'evidence_source_ids': ['request'], 'condition': None, 'time': None}]
    companion['unknowns'] = []
    with pytest.raises(ValueError, match='independence'):
        planner.create(TASK, summary, actor='researcher', rna_input_contract=binding, scientific_contract=companion)


@pytest.mark.parametrize('declaration', [None, {}, {'independent_units': None}, {'independent_units': 0}])
def test_missing_registered_independence_remains_explicitly_unknown(tmp_path, declaration):
    planner, summary, binding, companion = registered_bulk_contract(tmp_path)
    binding['declarations']['biological_independence'] = declaration
    companion['biological_units'][0]['independence'] = 'dependent'
    companion['unknowns'] = []
    with pytest.raises(ValueError, match='Unknown registered biological independence'):
        planner.create(TASK, summary, actor='researcher', rna_input_contract=binding, scientific_contract=companion)


@pytest.mark.parametrize('independent', [False, True])
def test_explicit_registered_independence_retains_matching_declaration(tmp_path, independent):
    planner, summary, binding, companion = registered_bulk_contract(tmp_path)
    binding['declarations']['biological_independence'] = {'independent_units': independent}
    companion['biological_units'] = [{'unit_id': 'donor', 'kind': 'donor', 'parent_ids': [],
        'independence': 'independent' if independent else 'dependent',
        'evidence_source_ids': ['request'], 'condition': None, 'time': None}]
    companion['unknowns'] = []
    state = planner.create(TASK, summary, actor='researcher', rna_input_contract=binding, scientific_contract=companion)
    retained = planner.get(state['session_id'])['scientific_contract']
    assert retained['biological_units'][0]['independence'] == ('independent' if independent else 'dependent')
    assert retained['unknowns'] == []
    assert retained['scientific_validity_established'] is False


def test_matching_registered_rna_semantics_and_offsets_are_retained(tmp_path):
    planner, summary, binding, companion = registered_bulk_contract(tmp_path)
    companion["statistical_plan"]["offsets"] = "RLE library-size offsets required by staged runtime"
    state = planner.create(TASK, summary, actor="researcher", rna_input_contract=binding, scientific_contract=companion)
    assert planner.get(state["session_id"])["scientific_contract"] == companion
    assert state["context"]["dataset_summary"]["observed"]["bulk_rna_input"]["biological_independence"] is None


def test_dense_acyclic_unit_hierarchy_has_bounded_validation(tmp_path):
    planner, summary, companion = prepared_contract(tmp_path)
    companion["biological_units"] = [{"unit_id": f"u{i}", "kind": "specimen", "parent_ids": [f"u{j}" for j in range(i)],
        "independence": "dependent", "evidence_source_ids": [], "condition": None, "time": None} for i in range(32)]
    completed = subprocess.run([sys.executable, "-c",
        "import sys; from devbench.analysis.rna_scientific_contract import ScientificContract; ScientificContract.model_validate_json(sys.stdin.read())"],
        input=json.dumps(companion), text=True, capture_output=True, timeout=3,
        cwd=str(Path(__file__).resolve().parents[1]))
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize('normalized_fits', [False, True])
def test_normalized_size_bound_is_enforced_before_plan_persistence(tmp_path, normalized_fits):
    from devharness_core.provenance import canonical_json
    api = importlib.import_module('devbench.analysis.rna_scientific_contract')
    planner, summary, companion = prepared_contract(tmp_path)
    companion['replay']['comparisons'] = [
        {'output_name': f'output-{i}', 'identifiers': ['gene_id'],
         'absolute_tolerance': 0, 'relative_tolerance': 0, 'missingness': 'exact_mask'}
        for i in range(64)]
    companion['sources'].extend(
        {'source_id': f'padding-{i}', 'kind': 'supplied_metadata', 'text': 'x',
         'reference': 'Synthetic bounded-size control'} for i in range(4))
    # Each two numeric tolerances expands from 0 to 0.0 during validation.
    growth = 64 * 4
    target = api.MAX_CONTRACT_BYTES - (growth if normalized_fits else 0)
    for source in companion['sources'][1:]:
        remaining = target - len(canonical_json(companion))
        source['text'] += 'x' * min(remaining, 12000 - len(source['text']))
    assert len(canonical_json(companion)) == target
    normalized = api.ScientificContract.model_validate_json(canonical_json(companion)).model_dump(
        mode='json', exclude_unset=True)
    assert len(canonical_json(normalized)) == target + growth
    with planner.methods._db() as db:
        before = db.execute("SELECT count(*) FROM records WHERE kind='method_plan'").fetchone()[0]
    if not normalized_fits:
        with pytest.raises(ValueError, match='bound|48 KB'):
            planner.create(TASK, summary, actor='researcher', scientific_contract=companion)
        with planner.methods._db() as db:
            assert db.execute("SELECT count(*) FROM records WHERE kind='method_plan'").fetchone()[0] == before
    else:
        state = planner.create(TASK, summary, actor='researcher', scientific_contract=companion)
        assert planner.get(state['session_id'])['scientific_contract'] == normalized
        assert planner.export_scientific_contract(state['session_id'])['scientific_contract'] == normalized
