"""Explicit startup study facts never become reusable execution permissions."""

import hashlib
import json
import os
from copy import deepcopy

import pytest

from tests.test_analysis_episodes import workspace

__all__ = ["workspace"]


def request_fixture(tmp_path):
    source = tmp_path / "source.h5ad"
    source.write_bytes(b"synthetic source identity fixture")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    question = (
        "Compare selected populations across declared stages with specimen blocking."
    )
    origin = {
        "origin": "synthetic_fixture",
        "population_reply": "Use A and B",
        "unit_declaration": "Independent synthetic units",
    }
    specification = {
        "format": "devbench.scrna-population-spec/1",
        "source_sha256": source_hash,
        "measurement": "umi_counts",
        "counts_layer": "counts",
        "populations": [
            {"label": "A", "column": "annotation", "values": ["a"]},
            {"label": "B", "column": "annotation", "values": ["b"]},
        ],
        "biological_unit_column": "unit",
        "biological_unit_identity": "declared",
        "stage_column": "stage",
        "stage_levels": ["early", "late"],
        "technical_alias_columns": ["batch"],
        "question": question,
        "requirement_source": origin,
        "procedure": "embryo_fixed_effect_stage_population_nb_v1",
        "choice_origin": "assistant_proposed",
    }
    return {
        "format": "devbench.workbench-singlecell-trial-request/1",
        "source": str(source),
        "source_sha256": source_hash,
        "question": question,
        "population_specification": specification,
        "user_declarations": {
            "assay": "scrna",
            "measurement": "umi_counts",
            "counts_layer": "counts",
            "data_classification": "synthetic",
            "count_provenance": "Artificial integer counts",
            "capture_unit_key": "batch",
            "capture_units_confirmed": True,
            "annotation_provenance": "Artificial labels",
            "biological_independence": {
                "independent": True,
                "unit_column": "unit",
                "evidence": "Constructed units",
            },
        },
        "requirement_source": origin,
        "authority_scope": "Facts only; runtime grants are separate.",
    }


def api():
    from devbench.analysis import workbench_singlecell

    return workbench_singlecell


@pytest.mark.parametrize("missing_count", [20, 21])
def test_population_conclusion_reports_bounded_unavailable_estimates_and_stage_gap(
    tmp_path, monkeypatch, missing_count
):
    from types import SimpleNamespace

    from devbench.platform import workbench_singlecell
    from devbench.platform.workbench_investigations import InvestigationService

    study = request_fixture(tmp_path)
    missing = [
        {"gene_id": f"gene_{i}", "estimate_status": "not_estimable"}
        for i in range(missing_count)
    ]
    outputs = {"result": "result", "summary": "summary"}
    (tmp_path / "result").write_text(json.dumps({"gene_statistics": missing}))
    stages = [
        {"stage": stage, "estimated_n": 1, "q_below_0_05_n": 0, "top_genes": []}
        for stage in study["population_specification"]["stage_levels"]
    ]
    (tmp_path / "summary").write_text(
        json.dumps({"summary": {"stage_summaries": stages}})
    )
    methods = SimpleNamespace(
        get=lambda kind, identity: {"outputs": outputs}
        if kind == "run"
        else {"size_bytes": (tmp_path / identity).stat().st_size},
        artifact_path=lambda identity: tmp_path / identity,
    )
    monkeypatch.setattr(
        workbench_singlecell,
        "read_singlecell_run",
        lambda *args: {
            "tool_name": "scrna_population_contrast",
            "status": "completed",
            "summary": {},
            "tables": [],
            "artifact_refs": [],
            "limitations": [],
        },
    )
    service = object.__new__(InvestigationService)
    service.app = SimpleNamespace(scientific=SimpleNamespace(methods=lambda: methods))
    service._binding = lambda record: {}
    evidence = service._population_conclusion(
        {"singlecell_trial": study},
        [
            {"experiment_id": "trial", "provenance": {"run_id": "fit"}},
        ],
    )

    unavailable = evidence["trials"][0]["unavailable_estimates"]
    assert unavailable == (
        missing
        if missing_count == 20
        else {
            "unestimable_count": missing_count,
            "truncated": True,
            "preview": missing[:20],
        }
    )
    first, second, gap = evidence["structured_findings"]
    assert [first["figure_link"], second["figure_link"]] == [
        "↗ early Volcano",
        "↗ late Volcano",
    ]
    assert gap["title"] == "Cross-stage shift (early → late) is untested"
    assert gap["figure_link"] is None
    assert (
        "direct comparison of the stage-specific population coefficients"
        in evidence["scope_limits"][0]
    )


def test_explicit_binding_matches_exact_project_question_and_input(tmp_path):
    request = request_fixture(tmp_path)
    binding = api().bind_singlecell_trial(
        request, project_id="a" * 32, input_roots=[tmp_path]
    )
    assert (
        binding["project_id"] == "a" * 32
        and binding["source_sha256"] == request["source_sha256"]
    )
    assert binding["request_sha256"] and binding["question_sha256"]
    assert (
        binding["population_specification"]["populations"]
        == request["population_specification"]["populations"]
    )
    assert not any("allow_" in key or "grant" in key for key in binding)


def test_unrelated_project_never_gets_startup_facts(tmp_path):
    request = request_fixture(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    assert (
        api().bind_singlecell_trial(request, project_id="b" * 32, input_roots=[other])
        is None
    )
    request["project_id"] = "a" * 32
    assert (
        api().bind_singlecell_trial(
            request, project_id="b" * 32, input_roots=[tmp_path]
        )
        is None
    )


@pytest.mark.parametrize(
    "field,value", [("source_sha256", "f" * 64), ("question", "Another question")]
)
def test_contradictory_specification_is_rejected(tmp_path, field, value):
    request = request_fixture(tmp_path)
    request[field] = value
    with pytest.raises(ValueError, match="match|differ"):
        api().validate_singlecell_trial_request(request)


def test_file_cannot_supply_runtime_authority(tmp_path):
    request = request_fixture(tmp_path)
    request["allow_model_execution"] = True
    with pytest.raises(ValueError, match="fields|authority"):
        api().validate_singlecell_trial_request(request)


def test_explicit_loader_checks_regular_canonical_file_and_size(tmp_path):
    request = request_fixture(tmp_path)
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    assert api().load_singlecell_trial_request(path)["question"] == request["question"]
    linked = tmp_path / "linked.json"
    linked.symlink_to(path)
    with pytest.raises(ValueError, match="canonical|symlink"):
        api().load_singlecell_trial_request(linked)


def test_matching_record_reuses_facts_without_grants_or_reasking(tmp_path):
    request = request_fixture(tmp_path)
    binding = api().bind_singlecell_trial(
        request, project_id="a" * 32, input_roots=[tmp_path]
    )
    record = {
        "id": "b" * 32,
        "project_id": "a" * 32,
        "question": request["question"],
        "source": request["source"],
        "profile": {
            "sha256": request["source_sha256"],
            "observed": {"file_type": "h5ad"},
        },
        "declarations": {},
        "scientific_context": {},
        "analyst_profile": "analyst_v1",
    }
    assert api().apply_singlecell_trial(record, binding) is True
    assert record["declarations"]["capture_units_confirmed"] is True
    assert (
        record["scientific_context"]["biological_independence"]["unit_column"] == "unit"
    )
    assert record["analyst_profile"] != "analyst_v1"
    assert "allow_model_execution" not in record["scientific_context"]
    assert record["singlecell_trial"]["request_sha256"] == binding["request_sha256"]
    from devbench.analysis.workbench_investigations import InvestigationService

    event_binding = InvestigationService.__new__(InvestigationService)._binding(record)
    assert event_binding["population"]["kind"] == "supplied_populations"
    assert (
        event_binding["population"]["specification_sha256"]
        == binding["specification_sha256"]
    )
    changed = deepcopy(record)
    changed["profile"]["sha256"] = "f" * 64
    with pytest.raises(ValueError, match="changed|source"):
        api().apply_singlecell_trial(changed, binding)


def test_nonmatching_question_gets_no_facts(tmp_path):
    request = request_fixture(tmp_path)
    binding = api().bind_singlecell_trial(
        request, project_id="a" * 32, input_roots=[tmp_path]
    )
    record = {
        "project_id": "a" * 32,
        "source": request["source"],
        "question": "Different objective",
        "declarations": {},
        "scientific_context": {},
    }
    assert api().apply_singlecell_trial(record, binding) is False
    assert record["declarations"] == {} and record["scientific_context"] == {}


def test_dynamic_review_packet_binds_actual_stage_without_claiming_execution(tmp_path):
    from devbench.analysis.checkpoints import digest
    from devbench.analysis.workflow_scientific_reviews import stage_material
    from tests.test_workflow_scientific_reviews import Planner, Selector

    request = request_fixture(tmp_path)
    binding = api().bind_singlecell_trial(
        request, project_id="a" * 32, input_roots=[tmp_path]
    )
    planner = Planner(tmp_path / "methods")
    planner.state["context"]["task"]["scientific_objective"] = request["question"]
    source = planner.methods.import_artifact(
        request["source"], artifact_type="anndata", actor="fixture"
    )
    planner.state["scrna_population_input"] = api().population_input_binding(
        {"singlecell_trial": binding}, source["artifact_id"]
    )
    runtime = api().make_singlecell_scientific_runtime(
        binding,
        methods_root=planner.methods.root,
        selector=Selector(),
        allow_model_execution=False,
    )
    material = stage_material(planner.state, "candidate_suitability")
    packet = runtime.reviewer.claim_packet_builder(
        planner, planner.state["session_id"], "candidate_suitability", material
    )
    assert packet.input_root == digest(material)
    assert packet.execution == "not_run" and packet.oversight == "pending"
    assert packet.assay_semantics == "unknown"
    assert any("declared" in value.lower() for value in packet.declarations)
    assert runtime.allow_review_model_execution is False
    previous = runtime.configuration()
    planner.state["scientific_candidates"][0]["name"] = "A changed proposal"
    revised = stage_material(planner.state, "candidate_suitability")
    changed = runtime.reviewer.claim_packet_builder(
        planner, planner.state["session_id"], "candidate_suitability", revised
    )
    assert (
        changed.input_root != packet.input_root and runtime.configuration() == previous
    )


def test_runtime_refuses_review_for_another_trial_binding(tmp_path):
    from devbench.analysis.workflow_scientific_reviews import stage_material
    from tests.test_workflow_scientific_reviews import Planner, Selector

    request = request_fixture(tmp_path)
    binding = api().bind_singlecell_trial(
        request, project_id="a" * 32, input_roots=[tmp_path]
    )
    planner = Planner(tmp_path / "methods")
    runtime = api().make_singlecell_scientific_runtime(
        binding,
        methods_root=planner.methods.root,
        selector=Selector(),
        allow_model_execution=False,
    )
    with pytest.raises(ValueError, match="scoped|binding|question"):
        runtime.reviewer.claim_packet_builder(
            planner,
            planner.state["session_id"],
            "candidate_suitability",
            stage_material(planner.state, "candidate_suitability"),
        )


def test_review_packet_dryrun_writes_only_explicit_material_output(tmp_path):
    from devbench.analysis.methods import MethodService
    from devbench.analysis.workflow_scientific_reviews import stage_material
    from tests.test_workflow_scientific_reviews import Planner, Selector

    request = request_fixture(tmp_path)
    binding = api().bind_singlecell_trial(
        request, project_id="a" * 32, input_roots=[tmp_path]
    )
    planner = Planner(tmp_path / "methods")
    planner.state["context"]["task"]["scientific_objective"] = request["question"]
    source = planner.methods.import_artifact(
        request["source"], artifact_type="anndata", actor="fixture"
    )
    planner.state["scrna_population_input"] = api().population_input_binding(
        {"singlecell_trial": binding}, source["artifact_id"]
    )
    planner.methods = MethodService(
        planner.methods.root, readonly=True, initialize=False, reconcile=False
    )
    selector = Selector()
    output = tmp_path / "packet-preview"
    runtime = api().make_singlecell_scientific_runtime(
        binding,
        methods_root=planner.methods.root,
        selector=selector,
        material_output_root=output,
        allow_model_execution=False,
    )
    packet = runtime.reviewer.claim_packet_builder(
        planner,
        planner.state["session_id"],
        "candidate_suitability",
        stage_material(planner.state, "candidate_suitability"),
    )
    assert list(output.glob("*/claim.json"))
    assert not (planner.methods.root / "singlecell-review-material").exists()
    assert selector.calls == 0
    assert (
        "conditional" in packet.claim_text.lower()
        and "experimental" in packet.claim_text.lower()
    )


def test_claim_packet_preserves_scientific_failure_after_successful_process(
    tmp_path, monkeypatch
):
    from devbench.analysis.workflow_scientific_reviews import stage_material
    from tests.test_workflow_scientific_reviews import Planner, Selector

    request = request_fixture(tmp_path)
    binding = api().bind_singlecell_trial(
        request, project_id="a" * 32, input_roots=[tmp_path]
    )
    planner = Planner(tmp_path / "methods")
    planner.state["context"]["task"]["scientific_objective"] = request["question"]
    source = planner.methods.import_artifact(
        request["source"], artifact_type="anndata", actor="fixture"
    )
    planner.state["scrna_population_input"] = api().population_input_binding(
        {"singlecell_trial": binding}, source["artifact_id"]
    )
    planner.state["results"] = [
        {"status": "success", "provenance": {"run_id": "d" * 32}}
    ]
    get = planner.methods.get
    monkeypatch.setattr(
        planner.methods,
        "get",
        lambda kind, identity: {"status": "succeeded", "run_id": identity}
        if kind == "run"
        else get(kind, identity),
    )
    monkeypatch.setattr(
        api(),
        "read_singlecell_run",
        lambda *args: {
            "status": "scientific_failure",
            "summary": {"failed_hypothesis_count": 1},
            "tables": [],
            "artifact_refs": [],
        },
    )
    runtime = api().make_singlecell_scientific_runtime(
        binding,
        methods_root=planner.methods.root,
        selector=Selector(),
        allow_model_execution=False,
    )
    packet = runtime.reviewer.claim_packet_builder(
        planner,
        planner.state["session_id"],
        "results_conclusions",
        stage_material(planner.state, "results_conclusions"),
    )
    assert packet.execution == "completed"
    assert packet.objective_validity == "failed"
    assert any(
        check.check == "statistics" and check.status == "failed"
        for check in packet.objective_checks
    )


def test_scientific_review_preserves_result_role_and_private_key_guard(
    workspace,
):
    from devbench.analysis.workflow_scientific_reviews import review, verify_review
    from tests.test_workflow_scientific_reviews import setup

    planner, adapter, selector, _ = setup(workspace)
    planner.state["results"] = [
        {"artifacts": [{"artifact_id": "a" * 64, "role": "result", "immutable": True}]}
    ]
    result = review(
        planner,
        "synthetic-session",
        stage="results_conclusions",
        reviewer=adapter,
        policy={},
        actor="fixture",
    )
    assert result["status"] == "accepted"
    public = selector.context["sections"]["limitations"]["reviewed_material"][
        "results"
    ][0]["artifacts"][0]
    assert public["artifact_role"] == "result" and "role" not in public
    assert planner.state["results"][0]["artifacts"][0]["role"] == "result"
    assert (
        verify_review(
            planner, "synthetic-session", "results_conclusions", result, policy={}
        )["status"]
        == "accepted"
    )


def test_primary_reference_bytes_are_bound_and_excerpts_are_bounded(tmp_path):
    request = request_fixture(tmp_path)
    source = tmp_path / "primary.txt"
    source.write_text("Primary source text. " * 4000)
    request["scientific_references"] = [
        {
            "path": str(source),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "title": "Synthetic primary-reference fixture",
            "url": "https://example.org/source",
            "version": "1",
            "access": "full_text",
            "accessed_on": "2026-11-02",
            "applicability": "Fixture-only reference binding",
            "limitations": "No scientific support inferred from this fixture.",
        }
    ]
    validated = api().validate_singlecell_trial_request(request)
    assert (
        validated["scientific_references"][0]["sha256"]
        == request["scientific_references"][0]["sha256"]
    )
    binding = api().bind_singlecell_trial(
        request, project_id="a" * 32, input_roots=[tmp_path]
    )
    assert binding["scientific_references"] == validated["scientific_references"]
    reference = api().verified_scientific_references(binding)[0]
    assert len(reference["excerpt"]) <= 8000
    assert reference["content_sha256"] == request["scientific_references"][0]["sha256"]
    source.write_text("changed")
    with pytest.raises(ValueError, match="changed|identity"):
        api().verified_scientific_references(binding)


@pytest.mark.wal
def test_scoped_native_intake_preserves_frozen_facts_and_current_campaign_grants(
    workspace,
):
    from devbench.analysis.workbench_ui import WorkbenchService
    from tests.test_scrna_population_analysis import fixture

    tmp_path = workspace
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    path, specification, _, _ = fixture(inputs)
    raw = {
        "format": api().REQUEST_FORMAT,
        "source": str(path),
        "source_sha256": specification["source_sha256"],
        "question": specification["question"],
        "population_specification": specification,
        "user_declarations": {
            "assay": "scrna",
            "measurement": "umi_counts",
            "count_provenance": "Synthetic counts",
            "counts_layer": "counts",
            "capture_unit_key": "capture",
            "capture_units_confirmed": True,
            "annotation_provenance": "Artificial supplied populations",
            "data_classification": "synthetic",
            "biological_independence": {
                "independent": True,
                "unit_column": "embryo",
                "evidence": "Artificial units",
            },
        },
        "requirement_source": specification["requirement_source"],
        "authority_scope": "Facts only.",
    }

    class NeverCalled:
        def configuration(self):
            return {"backend": "recorded_fixture"}

        def select(self, *args, **kwargs):
            raise AssertionError("No planning authorization was submitted")

    app = WorkbenchService(
        tmp_path / "service",
        [inputs],
        enable_scientific=True,
        method_selector=NeverCalled(),
        singlecell_trial_binding=raw,
        allow_data_execution=True,
    )
    try:
        record = app.investigations.create(
            {
                "source": str(path),
                "question": specification["question"],
                "analyst_profile": "analyst_v1",
            }
        )
        assert record["status"] == "awaiting_information", record.get("error")
        assert record["required_action"] == "authorize_data_execution"
        assert record["analyst_profile"] == "scientific_singlecell_v1"
        assert record["scientific_session_id"] is not None
        assert {field["field"] for field in record["clarification_fields"]} <= {
            "allow_data_execution",
            "allow_model_execution",
            "auto_advance",
            "auto_synthesis",
        }
        assert record["declarations"]["capture_unit_key"] == "capture"
        plan = app.scientific._plan(record["scientific_session_id"])
        assert (
            plan["scrna_population_input"]["specification"]["populations"]
            == specification["populations"]
        )
        assert not app.scientific.methods().list_records("run")
        followup = app.investigations.action(
            record["id"],
            {
                "action": "follow_up",
                "expected_revision": record["revision"],
                "question": "What columns are stored?",
            },
        )
        assert followup["status"] == "completed"
        assert followup["declarations"] == record["declarations"]
        assert "rna_input_contract" not in followup["scientific_context"]
        record = app.investigations.get(record["id"])
        cancelled = app.investigations.action(
            record["id"], {"action": "cancel", "expected_revision": record["revision"]}
        )
        assert cancelled["status"] == "cancelled"
        assert not app.scientific.methods().list_records("run")
    finally:
        app.close()


def test_retained_context_does_not_renew_current_scoped_data_or_model_grants():
    from devbench.analysis.workbench_investigations import InvestigationService

    service = InvestigationService.__new__(InvestigationService)
    service._campaign_grants = {}
    service._current_grants = {}
    record = {
        "id": "a" * 32,
        "scientific_context": {
            "data_classification": "public",
            "allow_model_execution": True,
            "allow_data_execution": True,
            "auto_synthesis": True,
        },
    }
    service._remember_campaign_grant(record, explicit_fields={"auto_advance"})
    assert service._current_grants.get(record["id"], {}) == {}
    service._remember_campaign_grant(record, explicit_fields={"allow_data_execution"})
    assert service._current_grants[record["id"]] == {"allow_data_execution": True}


def test_compact_singlecell_projection_keeps_full_family_ref_and_bounded_preview(
    tmp_path,
):
    request = request_fixture(tmp_path)
    binding = api().bind_singlecell_trial(
        request, project_id="a" * 32, input_roots=[tmp_path]
    )
    result = {
        "status": "completed",
        "source_sha256": binding["source_sha256"],
        "specification_sha256": binding["specification_sha256"],
        "audit": {
            "observation_count": 30,
            "feature_count": 1000,
            "selected_cell_count": 20,
            "declared_biological_unit_count": 6,
            "population_cell_counts": {"A": 10, "B": 10},
            "observation_ids": ["must-not-leak"],
            "population_membership": [0, 1],
        },
        "design": {
            "identifiable": True,
            "diagnostics": {"rank": 8, "residual_df": 4},
            "matrix": [[1]],
        },
        "pseudobulk": {"counts": [[1] * 1000], "count_validation": {"finite": True}},
        "gene_statistics": [{"gene_id": "full-result-only"}] * 1000,
        "summary": {
            "full_feature_count": 1000,
            "attempted_hypothesis_count": 2000,
            "estimated_hypothesis_count": 2000,
            "failed_hypothesis_count": 0,
            "stage_summaries": [
                {
                    "stage": "early",
                    "estimated_n": 1000,
                    "top_genes": [
                        {
                            "gene_id": "g1",
                            "log2_fold_change": 1.0,
                            "p_value": 0.01,
                            "q_value": 0.02,
                            "estimate_status": "estimated",
                        }
                    ],
                }
            ],
        },
        "testing_family": {"attempted_n": 2000},
        "limitations": ["Synthetic evidence only."],
    }
    ref = {
        "artifact_id": "c" * 64,
        "sha256": "d" * 64,
        "artifact_type": "json",
        "size_bytes": 900000,
    }
    projected = api().compact_singlecell_evidence(
        result,
        tool_name="scrna_population_contrast",
        binding={"source_sha256": binding["source_sha256"]},
        artifact_refs=[ref],
        run_id="e" * 32,
    )
    encoded = json.dumps(projected)
    assert len(encoded) < 12000
    assert "must-not-leak" not in encoded and "full-result-only" not in encoded
    assert projected["artifact_refs"] == [ref]
    assert projected["summary"]["full_feature_count"] == 1000
    assert projected["tables"][-1]["truncated"] is True


def test_prefit_review_projection_contains_verified_material_without_count_matrix(
    tmp_path,
):
    from devbench.analysis.scrna_population_analysis import run_population_analysis
    from tests.test_scrna_population_analysis import fixture

    path, spec, _, _ = fixture(tmp_path)
    documents = {
        stage: run_population_analysis(str(path), spec, stage)
        for stage in ("audit", "design", "pseudobulk")
    }
    projected = api().prefit_review_projection(
        documents, specification=spec, receipt_refs=[]
    )
    assert projected["audit"]["feature_count"] == 24
    assert projected["audit"]["declared_biological_unit_count"] == 6
    assert projected["audit"]["independence_verified"] is False
    assert projected["design"]["matrix"] == documents["design"]["design"]["matrix"]
    assert projected["design"]["diagnostics"]["rank"] == 8
    assert projected["design"]["diagnostics"]["residual_df"] == 4
    assert len(projected["design"]["profiles"]) == 12
    assert len(projected["design"]["contrasts"]) == 2
    assert projected["count_validation"]["finite"] is True
    assert projected["normalization"]["status"] == "not_run"
    assert projected["filtering"]["features_retained"] == 24
    assert len(projected["library_sizes"]) == 12
    encoded = json.dumps(projected)
    assert len(encoded.encode()) < 96000
    assert '"counts":' not in encoded and '"observation_ids":' not in encoded
    assert (
        '"population_membership":' not in encoded and "gene_statistics" not in encoded
    )
    changed = deepcopy(documents)
    changed["design"]["source_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="source|identity"):
        api().prefit_review_projection(changed, specification=spec, receipt_refs=[])


class _ScriptedScientificSelector:
    def __init__(self, continuation_mode):
        self.continuation_mode = continuation_mode
        self.kinds = []

    def configuration(self):
        return {
            "backend": "recorded_fixture",
            "scope": "scripted native single-cell graph test",
        }

    def select(self, context, output, **kwargs):
        from devbench.analysis.episode_agent import Selection
        from devbench.analysis.scientific_judgment import ClaimJudgment
        from tests.test_method_planner import recommendation
        from tests.test_scrna_population_admission import contrast_candidate

        assert kwargs.get("allow_model_execution") is False
        kind = context.get("decision_kind")
        self.kinds.append(kind or "scientific_review")
        if kind == "method_scientific_planning":
            action = {"candidates": [contrast_candidate()]}
        elif kind == "method_capability_resolution":
            identity = next(
                key
                for key, value in context["registry"].items()
                if value["implementation"].get("tool_name")
                == "scrna_population_contrast_parquet"
            )
            action = {
                "bindings": [
                    {
                        "candidate": "scrna population contrast",
                        "method_version_id": identity,
                        "discovery": None,
                    }
                ]
            }
        elif kind == "method_trial_design":
            frozen = context["scrna_population_admission"]["frozen_execution_binding"]
            action = {
                "experiments": [
                    {
                        "id": "synthetic-population-trial",
                        "candidates": ["scrna population contrast"],
                        "purpose": (
                            "Synthetic execution verification without "
                            "biological qualification"
                        ),
                        "subset_spec": {"kind": "full_dataset"},
                        "subset_validity": {
                            "preserves_preconditions": True,
                            "rationale": "Exact source and all genes",
                        },
                        "commands": [],
                        "metrics": [
                            {
                                "name": "full_feature_count",
                                "category": "other",
                                "direction": "descriptive",
                                "interpretation": (
                                    "Retained full feature family, not efficacy"
                                ),
                            }
                        ],
                        "metric_sources": {
                            "full_feature_count": {
                                "kind": "output_json",
                                "output": "summary",
                                "field": "/summary/full_feature_count",
                            }
                        },
                        "resource_limits": {
                            "memory_mb": 4096,
                            "timeout_seconds": 300,
                            "max_runs": 1,
                        },
                        "inputs": {"scrna population contrast": frozen["inputs"]},
                        "parameters": {
                            "scrna population contrast": frozen["parameters"]
                        },
                    }
                ]
            }
        elif kind == "method_recommendation":
            action = recommendation(choice=None)
        elif "claim_review" in context:
            claim = context["claim_review"]
            prefit = claim["claim_id"] != "scoped_results_conclusions"
            judgment = ClaimJudgment(
                packet_sha256=claim["packet_sha256"],
                claim_id=claim["claim_id"],
                reviewer_id="scoped-reviewer",
                stage="independent",
                scientific_status="insufficient_evidence"
                if prefit or self.continuation_mode == "review_refusal"
                else "supported",
                evidence_ids=("workflow_material",),
                rationale="Scripted synthetic protocol response; no qualification.",
                evidence_quality="verified_artifact",
                action_consequences="Synthetic gate verification only.",
                competing_explanations=(),
                missing_evidence="Authenticated independence and post-fit diagnostics."
                if prefit or self.continuation_mode == "review_refusal"
                else None,
            )
            refused = not prefit and self.continuation_mode == "review_refusal"
            action = {
                "kind": "scientific_review",
                "disposition": "needs_information" if prefit or refused else "accept",
                "rationale": "Scripted synthetic response.",
                "findings": [],
                "questions": [],
                "limitations": ["No scientific qualification."],
                "claim_judgment": judgment.model_dump(mode="json"),
            }
        else:
            raise AssertionError("Unexpected selection kind: " + str(kind))
        return Selection(
            action,
            {
                "backend": "recorded_fixture",
                "model_execution": False,
                "model_sessions_attempted": 0,
            },
        )


def _settle_scientific_operations(app, record, *, timeout=120):
    for identity in list(record["scientific_operations"]):
        if identity in app.scientific.futures:
            app.scientific.futures[identity].result(timeout=timeout)
    return app.investigations.get(record["id"])


def _exercise_native_synthesis_request(
    app, finished, body, continuation_mode, monkeypatch
):
    for invalid in ({**body, "extra": True}, {**body, "allow_model_execution": 1}):
        with pytest.raises(ValueError, match="exact synthesis request"):
            app.investigations.action(finished["id"], invalid)
    with pytest.raises(RuntimeError, match="changed"):
        app.investigations.action(finished["id"], {**body, "evidence_key": "0" * 64})
    with pytest.raises(RuntimeError, match="Refresh"):
        app.investigations.action(
            finished["id"], {**body, "expected_revision": finished["revision"] - 1}
        )
    if continuation_mode == "verify_failure":
        from devbench.platform.workflow_actions import WorkflowActions

        original = WorkflowActions.perform

        def fail_verify(self, node, cursor):
            if node == "verify":
                raise ValueError("Synthetic verification failure")
            return original(self, node, cursor)

        monkeypatch.setattr(WorkflowActions, "perform", fail_verify)
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as tabs:
        first = tabs.submit(app.investigations.action, finished["id"], body)
        second = tabs.submit(app.investigations.action, finished["id"], body)
        synthesis = first.result()
        assert (
            second.result()["synthesis_operation_id"]
            == synthesis["synthesis_operation_id"]
        )
    concluded = _settle_scientific_operations(app, synthesis)
    replay = app.investigations.action(finished["id"], body)
    assert replay["synthesis_operation_id"] == synthesis["synthesis_operation_id"]
    with pytest.raises(ValueError, match="Conflicting"):
        app.investigations.action(
            finished["id"], {**body, "allow_model_execution": False}
        )
    return concluded


def _paired_scale_fixture(inputs, *, genes=60_000):
    import anndata as ad
    import numpy as np
    import pandas as pd
    from scipy import sparse

    from devharness_core.provenance import hash_artifact
    from tests.test_scrna_population_analysis import fixture

    _, spec, _, _ = fixture(inputs)
    rows = [
        {
            "embryo": f"d{donor:03}",
            "stage": "E1" if donor < 64 else "E2",
            "type": population,
            "batch": f"b{donor:03}",
            "capture": f"c{donor:03}",
        }
        for donor in range(128)
        for population in ("P", "N")
        for _ in range(3)
    ]
    rng = np.random.default_rng(927)
    counts = np.empty((len(rows), genes), dtype=np.int32)
    for start in range(0, len(rows), 3):
        block = rng.negative_binomial(12, 0.4, (3, genes))
        if rows[start]["type"] == "N":
            block[:, :64] *= 2
        counts[start : start + 3] = block
    data = ad.AnnData(
        X=sparse.csr_matrix(counts.shape),
        obs=pd.DataFrame(rows, index=[f"cell{i}" for i in range(len(rows))]),
        var=pd.DataFrame(index=[f"gene{i:05}" for i in range(genes)]),
    )
    data.layers["counts"] = counts
    path = inputs / "paired-scale.h5ad"
    data.write_h5ad(path, compression="gzip")
    return path, {**spec, "source_sha256": hash_artifact(path)["sha256"]}


@pytest.mark.wal
@pytest.mark.parametrize(
    "continuation_mode",
    [
        "manual",
        "automatic",
        "verify_failure",
        "review_refusal",
        pytest.param(
            "paired-scale",
            marks=pytest.mark.skipif(
                os.environ.get("DEVBENCH_VERIFY_PAIRED_SCALE") != "1",
                reason="Opt in to the full 60,000-gene paired Workbench fit",
            ),
        ),
    ],
)
def test_native_graph_runs_registered_tools_through_real_review_adapter(
    workspace, monkeypatch, continuation_mode
):
    """Scripted reviewer responses test plumbing, never scientific qualification."""
    from devbench.analysis.workbench_investigations import SCIENTIFIC_CONFIRMATIONS
    from devbench.analysis.workbench_ui import WorkbenchService
    from tests.test_scrna_population_analysis import fixture

    inputs = workspace / "inputs"
    inputs.mkdir()
    is_scale = continuation_mode == "paired-scale"
    if is_scale:
        path, spec = _paired_scale_fixture(inputs)
        continuation_mode = "manual"
    else:
        path, spec, _, _ = fixture(inputs)
    features = 60_000 if is_scale else 24
    raw = {
        "format": api().REQUEST_FORMAT,
        "source": str(path),
        "source_sha256": spec["source_sha256"],
        "question": spec["question"],
        "population_specification": spec,
        "requirement_source": spec["requirement_source"],
        "authority_scope": "Synthetic facts only.",
        "user_declarations": {
            "assay": "scrna",
            "measurement": "umi_counts",
            "count_provenance": "Synthetic counts",
            "counts_layer": "counts",
            "capture_unit_key": "capture",
            "capture_units_confirmed": True,
            "annotation_provenance": "Artificial populations",
            "data_classification": "synthetic",
            "biological_independence": {
                "independent": True,
                "unit_column": "embryo",
                "evidence": "Artificial units",
            },
        },
    }
    selector = _ScriptedScientificSelector(continuation_mode)
    app = WorkbenchService(
        workspace / "service",
        [inputs],
        enable_scientific=True,
        method_selector=selector,
        singlecell_trial_binding=raw,
        allow_data_execution=True,
    )
    try:
        record = app.investigations.create(
            {
                "source": str(path),
                "question": spec["question"],
                "analyst_profile": "analyst_v1",
                "scientific_context": {
                    "allow_data_execution": True,
                    "auto_advance": True,
                    "auto_synthesis": continuation_mode == "automatic",
                },
            }
        )
        planned = _settle_scientific_operations(app, record)
        assert "gate_options" in planned, (
            planned.get("status"),
            planned.get("error"),
            planned.get("required_action"),
        )
        failure = planned.get("error") or {}
        assert planned["gate_options"]["gate"] == "review", (
            failure.get("technical_details", failure),
            planned.get("scientific_blocker_reason"),
        )
        design_view = next(
            turn["content"]
            for turn in planned["turns"]
            if turn["kind"] == "singlecell_evidence"
            and turn["content"]["tool_name"] == "scrna_population_design"
            and turn["content"]["status"] == "completed"
        )
        design_ref = design_view["artifact_refs"][0]
        descriptor = next(
            item
            for item in planned["artifacts"]
            if item["artifact_id"] == design_ref["artifact_id"]
        )
        availability = descriptor["plot_availability"]
        assert availability["available"] is True
        assert set(availability["capabilities"]) == {
            "scrna_population_cell_counts",
            "scrna_population_contrast_support",
        }
        assert availability["source"]["artifact_id"] == descriptor["artifact_id"]
        assert availability["source"]["sha256"] == descriptor["sha256"]
        assert app.scientific.store().list("result_version") == []
        from devbench.analysis import scrna_population_analysis
        from devbench.analysis.bulk_plot_service import scientific_bulk_workspace

        with monkeypatch.context() as context:
            context.setattr(
                scrna_population_analysis,
                "run_population_analysis",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    AssertionError("Saved plots must not run analysis")
                ),
            )
            plotted = scientific_bulk_workspace(
                app.scientific, design_ref["artifact_id"], mode="scientific"
            )
        assert len(plotted["panels"]) == 2
        assert (
            plotted["source"]["receipt_root"] == availability["source"]["receipt_root"]
        )
        assert plotted["source"]["artifact_id"] == design_ref["artifact_id"]
        proposal = next(
            row["spec"]
            for row in planned["gate_options"]["proposals"]
            if row["eligible"]
        )
        declared = app.investigations.action(
            planned["id"],
            {
                "action": "scientific",
                "expected_revision": planned["revision"],
                "request": {
                    "action": "declare_trial",
                    "session_id": planned["scientific_session_id"],
                    "spec": proposal,
                    "expected_revision": app.scientific.catalog()["revision"],
                },
                "operator_review": {
                    "reviewer": "Synthetic test operator",
                    "rationale": "Test exact graph review path.",
                    "confirmations": {key: True for key in SCIENTIFIC_CONFIRMATIONS},
                },
            },
        )
        assert declared["gate_options"]["gate"] == "execution_review"
        queued = app.investigations.action(
            declared["id"],
            {
                "action": "scientific",
                "expected_revision": declared["revision"],
                "request": {
                    "action": "run_trial",
                    "session_id": declared["scientific_session_id"],
                    "experiment_id": proposal["id"],
                    "idempotency_key": "synthetic-singlecell-run",
                    "allow_data_execution": True,
                    "expected_revision": app.scientific.catalog()["revision"],
                },
            },
        )
        finished = _settle_scientific_operations(
            app, queued, timeout=360 if is_scale else 120
        )
        plan = app.scientific._plan(finished["scientific_session_id"])
        assert (
            plan["results"]
            and plan["results"][0]["metric_values"].get("full_feature_count")
            == features
        ), (
            finished.get("status"),
            finished.get("error"),
            plan["results"],
        )
        views = [
            turn["content"]
            for turn in finished["turns"]
            if turn["kind"] == "singlecell_evidence"
        ]
        assert {
            "scrna_population_audit",
            "scrna_population_design",
            "scrna_population_pseudobulk",
            "scrna_population_contrast",
        } <= {view["tool_name"] for view in views}
        final = next(
            view
            for view in reversed(views)
            if view["tool_name"] == "scrna_population_contrast"
        )
        assert (
            final["summary"]["full_feature_count"] == features
            and len(final["artifact_refs"]) == 2
        )
        if is_scale:
            assert final["status"] == "completed", final["summary"]
            assert final["summary"]["attempted_hypothesis_count"] == 2 * features
            assert final["summary"]["estimated_hypothesis_count"] == 2 * features
        assert sorted(ref["artifact_type"] for ref in final["artifact_refs"]) == [
            "file",
            "json",
        ]
        assert "gene_statistics" not in json.dumps(final)
        for ref in final["artifact_refs"]:
            with pytest.raises(
                PermissionError, match="restricted_audit_entitlement_required"
            ):
                app.scientific.download(ref["artifact_id"])
            target, _, _ = app.scientific._linked_artifact(ref["artifact_id"])
            assert hashlib.sha256(target.read_bytes()).hexdigest() == ref["sha256"]
            if is_scale and ref["artifact_type"] == "json":
                result_summary = json.loads(target.read_bytes())
                assert len(result_summary["design"]["profiles"]) == 256
                calibration_optimizer = result_summary["diagnostics"]["calibration"][
                    "block_optimizer"
                ]
                assert calibration_optimizer["batched_gene_count"] > 0
                fit_execution = result_summary["diagnostics"][
                    "conditional_nb_execution"
                ]
                assert fit_execution["batched_fits"] > 0
                assert (
                    fit_execution["batched_fits"] + fit_execution["scalar_fits"]
                    == features
                )
                print(
                    json.dumps(
                        {
                            "paired_scale_supervision": result_summary["supervision"],
                            "calibration_optimizer": calibration_optimizer,
                            "conditional_nb_execution": fit_execution,
                            "paired_scale_phases": {
                                event["phase"]: event["elapsed_seconds"]
                                for event in result_summary["runtime_events"]
                            },
                        },
                        sort_keys=True,
                    )
                )
        if continuation_mode == "automatic":
            concluded = _settle_scientific_operations(app, finished)
            assert concluded["synthesis_continuation"]["status"] == "abstained", (
                concluded.get("error")
            )
            operations = app.investigations._synthesis_operations(
                concluded, app.scientific.catalog()
            )
            assert len(operations) == 1
            assert (
                "authorization"
                not in operations[0]["request"]["investigation_synthesis"]
            )
            retained = app.scientific.methods().get(
                "workspace_operation", operations[0]["operation_id"]
            )
            assert retained["request"]["investigation_synthesis"]["automatic"] is True
            assert selector.kinds.count("method_recommendation") == 1
            return
        assert selector.kinds.count("scientific_review") == 2
        continuation = finished["synthesis_continuation"]
        assert continuation["eligible"] and continuation["next_node"] == "verify", (
            continuation
        )
        assert (
            app.investigations.get(finished["id"])["synthesis_continuation"][
                "evidence_key"
            ]
            == continuation["evidence_key"]
        )
        assert "method_recommendation" not in selector.kinds
        original_receipts = plan["results"]
        run_count = len(app.scientific.catalog()["runs"])
        body = {
            "action": "continue_synthesis",
            "expected_revision": finished["revision"],
            "evidence_key": continuation["evidence_key"],
            "idempotency_key": "synthetic-singlecell-synthesis",
            "allow_model_execution": True,
            "reviewer_label": "Synthetic test operator",
        }
        concluded = _exercise_native_synthesis_request(
            app, finished, body, continuation_mode, monkeypatch
        )
        assert len(app.scientific.catalog()["runs"]) == run_count
        assert (
            app.scientific._plan(finished["scientific_session_id"])["results"]
            == original_receipts
        )
        if continuation_mode == "verify_failure":
            assert "method_recommendation" not in selector.kinds
            assert concluded["synthesis"] is None
            assert (
                concluded["synthesis_continuation"]["reason"] == "operation_unavailable"
            )
            assert "Synthetic verification failure" not in json.dumps(
                concluded["synthesis_continuation"]
            )
            assert (
                concluded["synthesis_continuation"]["required_action"]
                == "inspect_and_reconcile"
            )
            return
        assert selector.kinds.count("scientific_review") == 3, (
            selector.kinds,
            concluded.get("scientific_blocker_reason"),
        )
        if continuation_mode == "review_refusal":
            assert concluded["synthesis"] is None
            assert concluded["synthesis_continuation"]["status"] == "review_required"
            evidence = concluded["synthesis_continuation"]["population_evidence"]
            assert evidence["execution_status"] == "Fit complete"
            assert evidence["unanswered_parts"] == ["Stage-by-population interaction"]
            for stage in evidence["trials"][0]["stage_summaries"]:
                assert (
                    f"{stage['q_below_0_05_n']} of {stage['estimated_n']} "
                    "estimated genes"
                    in evidence["direct_answer"]
                )
            return
        assert concluded["synthesis_continuation"]["status"] == "abstained", concluded[
            "synthesis_continuation"
        ]
        result = concluded["synthesis"]["population_evidence"]["trials"][0]
        assert result["summary"]["attempted_hypothesis_count"] == 2 * features
        assert (
            result["testing_family"]["missing_hypothesis_policy"]
            == "internal_p_one_public_p_q_null"
        )
        assert result["design_diagnostics"]["rank"] > 0
        assert concluded["status"] in {
            "completed",
            "abstained",
            "awaiting_information",
        }, concluded.get("error")
        if final["status"] != "completed":
            assert concluded["status"] == "awaiting_information"
            assert "blocked" in concluded["scientific_blocker_reason"]
    finally:
        app.close()
