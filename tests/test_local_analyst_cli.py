"""Local analyst entry points use the retained workflow, never a second loop."""
import json

import pytest

from devbench.analysis.method_planner_cli import main


def analyst_request(tmp_path, *, paired=False, confounded=False):
    from devbench.analysis.methods import MethodService
    workspace = tmp_path / "methods"
    methods = MethodService(workspace, reconcile=False)
    samples = [f"s{i}" for i in range(8)]
    documents = {
        "matrix": {"gene_ids": ["g1", "g2"], "sample_ids": samples,
                   "matrix": [[10 + i for i in range(8)], [20 + i % 3 for i in range(8)]]},
        "metadata": {"samples": [{"sample_id": sample, "donor_id": f"d{i % 4 if paired else i}",
            "condition": "A" if i < 4 else "B", "batch": ("x" if i < 4 else "y") if confounded else "x"}
            for i, sample in enumerate(samples)]}}
    refs = {}
    for key, value in documents.items():
        path = tmp_path / (key + ".json")
        path.write_text(json.dumps(value))
        refs[key] = methods.import_artifact(path, artifact_type="json", actor="fixture")
    request = {"task": {"scientific_objective": "Estimate B minus A in independent synthetic donors",
        "computational_task": "bulk RNA contrast"}, "data_classification": "synthetic",
        "dataset_summary": {"observed": {}, "inferred": {}, "unknowns": [],
            "artifact_ids": [refs[key]["artifact_id"] for key in ("matrix", "metadata")]},
        "rna_input_contract": {"format": "devbench.bulk-rna-binding/1",
            "matrix_artifact_id": refs["matrix"]["artifact_id"], "metadata_artifact_id": refs["metadata"]["artifact_id"],
            "declarations": {"format": "devbench.bulk-rna-input/1", "representation": "raw_counts",
                "axes": ["gene", "sample"], "provenance": {"source": "hand-authored software fixture"},
                "biological_independence": {"independent_units": True},
                "metadata_roles": {"biological_unit": "donor_id", "condition": "condition", "batch": "batch"}}},
        "rna_design": {"format": "devbench.rna-design/1", "objective": {"intent": "infer"},
            "design": {"contrasts": [{"field": "condition", "reference": "A", "target": "B"}],
                "covariates": [], "chronological_field": None, "chronological_order": []},
            "selected_sample_ids": samples,
            "source": {"kind": "fixture", "reference": "test_local_analyst_cli",
                       "statement": "Test-author B-A contrast; not a user-prespecified scientific method"}}}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    return workspace, path


def test_create_analyst_requires_explicit_method_free_design(tmp_path):
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"task": {"scientific_objective": "Compare groups",
        "computational_task": "bulk RNA"}, "dataset_summary": {"observed": {}, "inferred": {}, "unknowns": []}}))
    with pytest.raises(ValueError, match="rna_design"):
        main(["--workspace", str(tmp_path / "methods"), "create-analyst", "--request", str(request)])


def test_audit_design_does_not_mislabel_legacy_integrity_check(tmp_path):
    from tests.test_langgraph_workflow import rna_plan
    planner, plan, *_ = rna_plan(tmp_path)
    with pytest.raises(ValueError, match="rna_design"):
        main(["--workspace", str(planner.methods.root), "audit-design", "--plan", plan["session_id"]])


def test_advance_requires_design_before_selector_or_scientific_runtime(tmp_path):
    from tests.test_langgraph_workflow import rna_plan
    planner, plan, *_ = rna_plan(tmp_path)
    with pytest.raises(ValueError, match="rna_design"):
        main(["--workspace", str(planner.methods.root), "advance", "--plan", plan["session_id"]])


@pytest.mark.parametrize("paired,confounded,expected", [(False, False, "ready"),
    (True, False, "abstained"), (False, True, "abstained")])
def test_cli_audit_is_durable_and_never_selects_or_fits(tmp_path, capsys, paired, confounded, expected):
    workspace, request = analyst_request(tmp_path, paired=paired, confounded=confounded)
    assert main(["--workspace", str(workspace), "create-analyst", "--request", str(request)]) == 0
    plan = json.loads(capsys.readouterr().out)
    command = ["--workspace", str(workspace), "audit-design", "--plan", plan["session_id"]]
    assert main(command) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == expected
    assert result["audit"] is not None
    assert result["execution"] == result["numerical_replay"] == result["biological_agreement"] == "not_run"
    assert main(command) == 0
    assert json.loads(capsys.readouterr().out) == result
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.methods import MethodService
    methods = MethodService(workspace, readonly=True, reconcile=False)
    retained = ScientificMethodPlanner(methods).get(plan["session_id"])
    assert retained["selection_history"] == [] and retained["results"] == []
    assert methods.list_records("run") == []


def test_audit_without_runtime_can_attach_scientific_runtime_for_advancement(tmp_path, capsys):
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.methods import MethodService
    from devbench.analysis.workflow_entrypoints import advance_workflow_to_gate
    from tests.support.workflow_science_fixtures import RecordingScientificRuntime

    workspace, request = analyst_request(tmp_path)
    assert main(["--workspace", str(workspace), "create-analyst", "--request", str(request)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert main(["--workspace", str(workspace), "audit-design", "--plan", plan["session_id"]]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"
    planner = ScientificMethodPlanner(MethodService(workspace, reconcile=False))
    result = advance_workflow_to_gate(planner, plan["session_id"], actor="fixture",
        scientific_workflow=RecordingScientificRuntime())
    assert result["status"] == "awaiting_information"
    assert result["required_action"] == "configure_planning_selector"
    assert planner.get(plan["session_id"])["selection_history"] == []
    assert planner.methods.list_records("run") == []


def test_method_selector_receives_exact_declared_design_and_verified_audit(tmp_path, capsys):
    from devbench.analysis.episode_agent import Selection
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.methods import MethodService

    workspace, request = analyst_request(tmp_path)
    expected_design = json.loads(request.read_text())["rna_design"]
    assert main(["--workspace", str(workspace), "create-analyst", "--request", str(request)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert main(["--workspace", str(workspace), "audit-design", "--plan", plan["session_id"]]) == 0
    audited = json.loads(capsys.readouterr().out)
    contexts = []

    class CapturingSelector:
        def configuration(self):
            return {"backend": "recorded_fixture"}

        def select(self, context, output, **kwargs):
            contexts.append(context)
            assert kwargs["allow_model_execution"] is False
            return Selection({"candidates": []}, {"backend": "recorded_fixture"})

    planner = ScientificMethodPlanner(MethodService(workspace, reconcile=False))
    assert planner.generate_candidates(plan["session_id"], CapturingSelector(), actor="fixture") == []
    assert len(contexts) == 1
    assert contexts[0]["rna_design"] == expected_design
    assert contexts[0]["rna_design_audit"] == audited["audit"]
    assert "registry" not in contexts[0]
    assert planner.methods.list_records("run") == []


def test_advance_reports_early_refusal_without_requiring_a_model_runtime(tmp_path, capsys):
    workspace, request = analyst_request(tmp_path, confounded=True)
    main(["--workspace", str(workspace), "create-analyst", "--request", str(request)])
    plan = json.loads(capsys.readouterr().out)
    main(["--workspace", str(workspace), "advance", "--plan", plan["session_id"]])
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "abstained" and result["audit"]["status"] == "refused"
