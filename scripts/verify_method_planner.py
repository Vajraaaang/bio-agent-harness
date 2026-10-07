"""Opt-in fixed-procedure OCI verification with recorded planner decisions.

Reuses the extensible-method execution verifier, then runs one additional real
OCI planner trial. MAD and threshold 4 are test-author fixture choices, not user
requirements or scientific selections. This verifies execution and evidence
boundaries; it cannot establish the best filtering method. No live model,
literature retrieval, public/private biological data, image pull, package
installation or clinical approval is performed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from devbench.analysis.method_planner import ScientificMethodPlanner
from devbench.analysis.method_planner_cli import RecordedMethodSelector
from devbench.analysis.method_validation import finite_json
from devbench.analysis.methods import MethodService
from devharness_core.provenance import canonical_json
from scripts.verify_extensible_methods import verify as verify_execution


def _write(path, value):
    path.write_bytes(canonical_json(value))


def _candidate(name, parameters):
    return {"name": name, "method_family": "quality_control",
        "source": {"type": "generated", "reference": "Recorded synthetic software-verification fixture"},
        "assumptions": [{"statement": "Sample labels are present for per-sample QC", "status": "satisfied",
                         "evidence": "Inspected synthetic input rows", "hard_precondition": True,
                         "field": "sample_labels_present", "operator": "equals", "expected": True},
                        {"statement": "The declared QC metric is available", "status": "satisfied",
                         "evidence": "Inspected synthetic input rows", "hard_precondition": True,
                         "field": "metric_names", "operator": "contains", "expected": "total_counts"}],
        "expected_inputs": ["dataset"], "dataset_fit": "plausible", "objective_fit": "strong",
        "known_failure_modes": ["The fixture does not establish scientific validity or optimality"],
        "parameter_candidates": [{"name": name, "value": value, "origin": "fixture",
                                  "rationale": "Fixed test-author choice, not user instruction or scientific selection"}
                                 for name, value in parameters.items()]}


def _execution_abstention(unavailable, experiment_id):
    return {"choice": None, "confidence": "no_recommendation", "scientific_confidence": "no_recommendation",
        "implementation_confidence": "low", "confidence_basis": "One fixed-procedure synthetic execution only",
        "rationale": "Abstain from scientific method selection: runtime and row-count checks verify execution, not comparative scientific quality",
        "tradeoffs": ["The recorded alternative was not executed",
                      "MAD and threshold 4 were fixed fixture choices, not user requirements",
                      "A method-unspecified comparison with independent scientific criteria remains necessary"],
        "alternatives": [unavailable], "params": {}, "required_environment": {},
        "tests_run": [experiment_id], "approval_actions": [
            {"action": action, "required": False, "status": "not_requested"}
            for action in ("approve_recommendation", "save_experimental_recipe", "approve_analysis_plan_change")]}


def _blocked_selection_actions(planner, session, *, actor):
    """Verify abstention cannot authorize scientific selection or reuse."""
    before = planner.get(session)
    assert before["recommendation"]["choice"] is None and before["approvals"] == {}
    rationale = "Negative control: fixed-procedure execution evidence cannot select a scientific method"
    actions = {
        "approve_recommendation": lambda: planner.approve_recommendation(session, actor=actor, rationale=rationale),
        "save_experimental_recipe": lambda: planner.save_recipe(session, "Blocked fixture recipe", actor=actor, rationale=rationale),
        "propose_selected": lambda: planner.propose_selected(session, actor=actor, rationale=rationale),
    }
    blocked = {}
    for name, action in actions.items():
        try:
            action()
        except (ValueError, PermissionError) as error:
            reason = str(error)
            if not any(word in reason.casefold() for word in ("recommendation", "approval", "execution verification")):
                raise
            blocked[name] = {"status": "blocked", "reason": reason}
        else:
            raise AssertionError(f"Execution-only abstention unexpectedly allowed {name}")
        assert planner.get(session) == before, f"Blocked action changed planner state: {name}"
    return blocked


def _implementation_response(source, marker):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", marker):
        raise ValueError("Use a single safe implementation-draft marker")
    return {"source_code": source + f"\n# devharness recorded implementation draft {marker}\n",
            "algorithm_summary": "Retain the existing synthetic per-sample raw-MAD fixture and isolation probes",
            "introduced_choices": ["Added an identifying comment only; executable statements remain unchanged"],
            "source_references": []}


def verify(output, image, *, allow_data_execution=False):
    if allow_data_execution is not True:
        raise PermissionError("Synthetic OCI verification requires explicit data-execution authorization")
    output = Path(output)
    if not output.is_absolute():
        raise ValueError("Use an absolute verification directory")
    if not isinstance(image, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}", image):
        raise ValueError("Use an already installed immutable OCI image digest")
    output.mkdir(parents=True, exist_ok=False)
    _write(output / "attempt.json", {"started_at": datetime.now(timezone.utc).isoformat(), "image": image,
        "data_scope": "synthetic software fixtures", "selector_backend": "recorded_fixture",
        "evaluation_purpose": "execution_verification", "method_selection_evaluated": False,
        "model_execution": False, "network_retrieval": False, "allow_data_execution": True})
    planner, session = None, None
    phase, checks = "execution_verifier", []
    try:
        execution_summary = verify_execution(output / "execution", image)
        service = MethodService(output / "execution" / "workspace")
        baseline_run = finite_json(output / "execution" / "run.json")
        method = service.method(baseline_run["method_version_id"])
        name = method["contract"]["name"]
        parameters = baseline_run["parameters"]
        inputs = baseline_run["inputs"]
        rows = finite_json(service.artifact_path(inputs["dataset"]))
        assert len(rows) == 10 and {row["sample"] for row in rows} == {"a", "b"}
        analysis_id = service.get("branch", baseline_run["branch_id"])["analysis_id"]
        original = service.analysis(analysis_id)
        original_bytes = canonical_json(original)
        _write(output / "canonical-before-planner.json", original)

        def unchanged(label):
            assert canonical_json(service.analysis(analysis_id)) == original_bytes, label
            checks.append(label)

        actor = "synthetic-planner-verification"
        reviewer = "synthetic-planner-reviewer"
        phase = "planner_setup"
        branch = service.create_branch(analysis_id, original["revision_id"], actor=actor)
        unchanged("branch_creation")
        planner = ScientificMethodPlanner(service)
        state = planner.create({"scientific_objective": "Verify fixed-procedure execution and evidence boundaries on synthetic QC rows",
                                "computational_task": "quality_control",
                                "constraints": ["MAD and threshold 4 are fixed test-author choices, not user requirements",
                                                "Synthetic software verification; method selection is not evaluated"]},
            {"observed": {"synthetic_data": True, "row_count": len(rows),
                          "sample_labels_present": all(isinstance(row.get("sample"), str) for row in rows),
                          "metric_names": ["total_counts"], "sample_groups": ["a", "b"]},
             "inferred": {}, "unknowns": [], "artifact_ids": list(inputs.values())},
            actor=actor, data_classification="synthetic", evaluation_purpose="execution_verification")
        session = state["session_id"]
        unchanged("planner_creation")
        unavailable = "Unavailable recorded synthetic QC alternative"
        experiment_id = "synthetic-full-library-qc"
        spec = {"id": experiment_id, "candidates": [name], "purpose": "Real OCI synthetic QC software verification",
            "subset_spec": {"kind": "full_dataset", "representation": "All ten original rows and both sample groups"},
            "subset_validity": {"preserves_preconditions": True,
                                "rationale": "Complete immutable synthetic input preserves sample labels and QC metrics"},
            "commands": [], "metrics": [
                {"name": "input_rows", "category": "other", "direction": "descriptive",
                 "interpretation": "Observed input row count from the QC report"},
                {"name": "retained_rows", "category": "signal_preservation", "direction": "descriptive",
                 "interpretation": "Observed retained row count; expected 8 of 10 for this fixture"},
                {"name": "runtime_seconds", "category": "runtime", "direction": "lower_better",
                 "interpretation": "Measured execution duration, not scientific quality"}],
            "metric_sources": {"input_rows": {"kind": "output_json", "output": "qc_report", "field": "/before"},
                               "retained_rows": {"kind": "output_json", "output": "qc_report", "field": "/after"},
                               "runtime_seconds": {"kind": "runtime", "field": "elapsed_seconds"}},
            "resource_limits": {"memory_mb": 512, "timeout_seconds": 60, "max_runs": 1},
            "inputs": {name: inputs}, "parameters": {name: parameters}}
        recommendation = _execution_abstention(unavailable, experiment_id)
        draft_response = _implementation_response(service.artifact_path(method["source_artifact"]).read_text(), session)
        decisions = [{"candidates": [_candidate(unavailable, parameters), _candidate(name, parameters)]},
            {"bindings": [{"candidate": unavailable, "method_version_id": None, "discovery": None},
                          {"candidate": name, "method_version_id": method["version_id"], "discovery": None}]},
            draft_response, {"experiments": [spec]}, recommendation]
        _write(output / "recorded-decisions.json", {"backend": "recorded_fixture", "model_execution": False,
                                                   "responses": decisions})
        selector = RecordedMethodSelector(decisions)
        phase = "scientific_generation"
        candidates = planner.generate_candidates(session, selector, actor=actor)
        assert [candidate["name"] for candidate in candidates] == [unavailable, name]
        unchanged("scientific_generation")
        phase = "capability_resolution"
        resolved = planner.resolve_capabilities(session, selector, [method["version_id"]], actor=actor)
        assert [candidate["name"] for candidate in resolved] == [unavailable, name]
        assert resolved[0]["method_version_id"] is None and resolved[1]["method_version_id"] == method["version_id"]
        unchanged("capability_resolution")
        phase = "implementation_draft"
        original_method = method
        drafted = planner.draft_implementation(session, name, selector, method["contract"],
            service.artifact_path(method["environment_artifact"]), actor=actor,
            justification="Exercise explicit source adaptation with an identifying comment only; no new scientific implementation claim")
        _write(output / "implementation-draft.json", drafted)
        _write(output / "implementation-response.json", draft_response)
        assert drafted["status"] == "generated_unexecuted"
        assert drafted["method_version_id"] != original_method["version_id"]
        method = service.method(drafted["method_version_id"])
        assert method["contract"]["lifecycle"] == "custom" and method["contract"]["implementation_trust"] == "unreviewed"
        assert method["contract"]["implementation"]["source_sha256"] != original_method["contract"]["implementation"]["source_sha256"]
        for key in ("parameters", "inputs", "outputs", "execution", "environment"):
            assert method["contract"][key] == original_method["contract"][key]
        current = planner.get(session)
        assert current["experiments"] == [] and current["results"] == []
        assert current["candidates"][0]["method_version_id"] is None
        assert current["candidates"][1]["method_version_id"] == method["version_id"]
        _write(output / "state-after-inert-draft.json", current)
        unchanged("implementation_draft")
        phase = "trial_design"
        proposals = planner.design_trials(session, selector, actor=actor)
        _write(output / "trial-designs.json", proposals)
        assert proposals == [spec]
        current = planner.get(session)
        assert current["experiments"] == [] and current["results"] == []
        assert current["trial_designs"][-1]["status"] == "unexecuted_drafts"
        _write(output / "state-after-inert-design.json", current)
        unchanged("trial_design")
        phase = "trial_declaration"
        _write(output / "trial-declaration.json", planner.declare_trial(session, branch["branch_id"], proposals[0], actor=actor))
        unchanged("trial_declaration")
        phase = "oci_trial"
        results = planner.run_trial(session, experiment_id, actor=actor, allow_data_execution=True)
        _write(output / "trial-results.json", results)
        unchanged("oci_trial")
        assert len(results) == 1 and results[0]["status"] == "success", results
        result = results[0]
        run = service.get("run", result["provenance"]["run_id"])
        assert run["method_version_id"] == drafted["method_version_id"]
        assert run["execution"]["runtime"] == "local_docker", "A real OCI execution is required"
        retained = finite_json(service.artifact_path(run["outputs"]["dataset"]))
        qc = finite_json(service.artifact_path(run["outputs"]["qc_report"]))
        assert len(retained) == qc["after"] == result["metric_values"]["retained_rows"] == 8
        assert len(rows) == qc["before"] == result["metric_values"]["input_rows"] == 10
        assert set(qc["removed_ids"]) == {"a-4", "b-4"}
        assert result["metric_values"]["runtime_seconds"] >= 0
        assert service.provenance(run["run_id"])["valid"] is True
        sandbox_checks = json.loads(run["execution"]["stdout"])["sandbox_checks"]
        assert sandbox_checks and all(sandbox_checks.values())
        phase = "recommendation"
        _write(output / "recommendation.json", planner.recommend(session, selector, actor=actor))
        unchanged("recommendation")
        phase = "scientific_selection_guards"
        blocked = _blocked_selection_actions(planner, session, actor=reviewer)
        _write(output / "blocked-selection-actions.json", blocked)
        approval = {"status": "not_requested", "identity_assurance": None,
                    "reason": "Execution-only fixture abstained from scientific method selection",
                    "negative_control": blocked["approve_recommendation"]}
        _write(output / "synthetic-review.json", approval)
        recipe = {"status": "not_requested", "recipe_id": None,
                  "reason": "No scientifically selected method is available for reuse",
                  "negative_control": blocked["save_experimental_recipe"]}
        _write(output / "experimental-recipe.json", recipe)
        proposal = {"status": "not_requested", "proposal_id": None,
                    "reason": "No scientific winner was selected",
                    "negative_control": blocked["propose_selected"]}
        _write(output / "pending-proposal.json", proposal)
        unchanged("scientific_selection_guards")
        state, exported = planner.get(session), planner.export(session)
        _write(output / "state.json", state)
        _write(output / "plan.json", exported)
        assert set(exported) == {"task", "dataset_summary", "candidates", "experiments", "results", "recommendation"}
        recorded_phases = [item["phase"] for item in state["selection_history"]]
        assert recorded_phases == ["method_scientific_planning", "method_capability_resolution", "method_implementation_draft",
                                   "method_trial_design", "method_recommendation"]
        assert all(item["status"] == "accepted" and item["evidence"]["model_execution"] is False
                   and item["evidence"]["backend"] == "recorded_fixture" for item in state["selection_history"])
        trust = service.method(method["version_id"])["contract"]["implementation_trust"]
        assert trust == "unreviewed"
        unchanged("export")
        summary = {"status": "passed", "scope": "fixed-procedure execution evidence with real OCI; scientific method selection not evaluated",
            "evaluation_purpose": "execution_verification", "method_selection_evaluated": False,
            "scientific_recommendation": None, "selection_actions_blocked": blocked,
            "selector_backend": "recorded_fixture", "model_execution": False, "live_codex_evaluation": False,
            "network_retrieval": False, "image": image, "session_id": session, "planner_trial_run_id": run["run_id"],
            "execution_bootstrap": execution_summary, "planner_oci_trials": 1,
            "recorded_phase_count": len(recorded_phases), "recorded_phases": recorded_phases,
            "authoring_inert_verified": True, "implementation_draft_status_at_registration": drafted["status"],
            "original_method_version_id": original_method["version_id"], "drafted_method_version_id": method["version_id"],
            "input_rows": 10, "retained_rows": 8, "metrics": result["metric_values"], "provenance_valid": True,
            "sandbox_checks": sandbox_checks, "unavailable_candidate_preserved": exported["candidates"][0]["name"],
            "canonical_revision_before": original["revision_id"], "canonical_revision_after": service.analysis(analysis_id)["revision_id"],
            "canonical_snapshot_sha256": hashlib.sha256(original_bytes).hexdigest(), "canonical_unchanged_checks": checks,
            "recipe_status": recipe["status"], "proposal_status": proposal["status"], "implementation_trust": trust,
            "review_identity_assurance": approval["identity_assurance"], "audit": service.audit(),
            "limitations": ["MAD and threshold 4 are test-author fixture choices, not user requirements",
                            "Recorded fixed-procedure decisions do not evaluate scientific method selection",
                            "Synthetic QC counts do not validate a biological method",
                            "Planner phase does not approve canonical analysis, full-data execution or trusted-registry promotion"]}
        _write(output / "summary.json", summary)
        return summary
    except Exception as error:
        failure = {"status": "failed", "phase": phase, "error": f"{type(error).__name__}: {error}"[:2000],
                   "image": image, "selector_backend": "recorded_fixture", "model_execution": False,
                   "evaluation_purpose": "execution_verification", "method_selection_evaluated": False,
                   "live_codex_evaluation": False, "canonical_unchanged_checks": checks}
        if planner is not None and session is not None:
            try:
                _write(output / "state.json", planner.get(session))
                _write(output / "plan.json", planner.export(session))
            except Exception as snapshot_error:
                failure["snapshot_error"] = f"{type(snapshot_error).__name__}: {snapshot_error}"[:1000]
        _write(output / "summary.json", failure)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--allow-data-execution", action="store_true", help="Authorize this synthetic OCI verification")
    args = parser.parse_args(argv)
    summary = verify(args.output, args.image, allow_data_execution=args.allow_data_execution)
    print(json.dumps(summary, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
