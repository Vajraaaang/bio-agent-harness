"""Freeze existing study contracts for offline review, never execute a study.

The output is coordinator-only configuration, not a participant export or an
approved/powered live study. It deliberately has no model or generation seeds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from devbench.analysis.episode_proposals import _load, _seal
from devbench.study.contracts import canonical_json_bytes
from devbench.study.biology_planning import (
    BiologyStudyPlan, default_biology_study, schedule_biology_attempts,
    validate_biology_plan,
)
from devbench.study.public_study import (
    PublicStudyPlan, default_public_plan, schedule_public_attempts,
)


ROOT = Path(__file__).resolve().parents[1]
CASES = ("T05-whole-null", "T05-weak-low", "T06-partial", "T06-aliased",
         "T07-unknown-identity", "T13-continuous", "T14-state-only", "T14-one-donor")
PROTOCOL = {
    "comparisons": {
        "optional-baseline": "availability",
        "enforced-optional": "enforcement_with_other_factors_matched",
        "enforced-baseline": "total_system_intervention",
        "optional-users-nonusers": "descriptive_self_selected_adoption_only",
    },
    "matched": ["input_identity", "public_question_and_evidence", "ordinary_libraries",
                "observed_model_and_runtime_block", "controllable_generation_settings",
                "nominal_resource_caps", "protected_evaluation_rules", "outcome_review"],
    "intentional_differences": ["harness_availability", "mandatory_acceptance_checks",
                               "necessary_intervention_instructions"],
    "reviewer_intervention": "none; outcome review held constant across arms",
    "outcomes": ["raw_scientific_correctness", "harness_acceptance", "finding_recovery",
                 "false_discoveries", "unsupported_claims", "appropriate_refusal",
                 "appropriate_clarification", "invalid_acceptance", "unnecessary_rejection",
                 "tool_error_recovery", "reproducibility", "completion", "wall_time",
                 "peak_rss", "observable_usage", "observed_tool_adoption"],
    "reference_scopes": ["synthetic_truth", "public_reference_concordance",
                         "experimental_technical_controls"],
    "missingness_rules": ["unknown_is_not_zero", "failed_attempts_are_retained",
        "honest_refusal_is_not_completed_estimation", "unavailable_truth_is_not_false",
        "full_attempted_hypothesis_family", "no_public_concordance_as_accuracy"],
    "analysis_units": "paired cases/cohorts; repeated model attempts nested within cohort; related variants clustered",
    "denominators": "prespecified eligible planned slots; failed/unlaunched/unknown slots reported separately; no completed-only benefit headline",
    "uncertainty": "case/cohort-aware paired uncertainty; insufficient independent units yields unavailable intervals",
    "precision": "one cohort and repetition here specify schedule structure only, not adequate precision or power",
    "protected_boundary": "references, hidden membership, evaluator implementation and held-out feedback never enter participant observations",
    "revision_feedback": "ordinary diagnostics only; no held-out scores or reference answers",
    "no_surrogate_rewards": ["significant_gene_count", "tool_call_count", "batch_mixing_alone", "model_label_agreement"],
    "threshold_policy": "existing plan defaults unchanged; uncalibrated; any future revision has a new identity and cannot rewrite previous results",
}


def _source_identity():
    names = (
        "scripts/prepare_workbench_evaluation.py", "devbench/study/contracts.py",
        "devbench/study/biology_planning.py", "devbench/study/biology_catalog.json",
        "devbench/study/public_study.py", "devbench/study/biology_cli.py",
        "devbench/study/public_cli.py", "devbench/analysis/episode_proposals.py",
        "devharness_core/provenance.py",
    )
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}


def prepare(output):
    """Prepare metadata only through the existing plans and execution-DAG seal."""
    biology = default_biology_study("rna-workbench-readiness", coverage="full")
    payload = biology.model_dump(mode="json")
    payload["recipes"] = [row for row in payload["recipes"] if row["case_id"] in CASES]
    biology = validate_biology_plan(BiologyStudyPlan.model_validate_json(canonical_json_bytes(payload)))
    public = default_public_plan("rna-public-readiness",
                                 datasets=("developmental_general", "seqc_controls"))
    record = {
        "format": "devbench.workbench-comparison-readiness/1",
        "status": "offline_readiness_not_launchable",
        "scope": "scientific-tool study preparation; not a measured full-product or reviewer intervention",
        "confidentiality": "coordinator_only; contains private plan nonces; do not publish or provide to participants",
        "execution": {"data_executed": False, "provider_sessions": 0,
                      "scientific_performance": None, "usage": None},
        "protocol": PROTOCOL,
        "plans": {"synthetic": biology.model_dump(mode="json"), "public": public.model_dump(mode="json")},
        "schedules": {"synthetic": [row.model_dump(mode="json") for row in schedule_biology_attempts(biology)],
                      "public": schedule_public_attempts(public)},
        "source_sha256": _source_identity(),
        "route_readiness": {
            "bulk_counts": {"interface": "devbench.study.cli biology-run", "plan_cases": list(CASES[:5])},
            "bulk_continuous": {"interface": "devbench.study.cli biology-run", "plan_cases": ["T13-continuous"]},
            "cellular_counts": {"interface": "devbench.study.cli biology-run", "plan_cases": list(CASES[6:])},
            "backed_scrna": {
                "interface": "devbench.analysis.workbench_proposals",
                "live_three_arm_dispatch": False,
                "scope": "operator-reviewed native workflow and offline bundle/replay fixtures only; not silently represented as a biology-run arm",
                "evaluation_requirements": ["fixed input and batch identities", "biological preservation plus batch diagnostics",
                    "external labels and confidence provenance", "concordance not accuracy", "separate native comparison dispatch qualification"],
            },
        },
        "public_scope": {
            "developmental_general": "observed RPKM library means; animal independence unknown; overlapping marker subset is not a second cohort",
            "seqc_controls": "held-out ERCC ratios and technical repeatability; no independent patients or all-gene truth",
            "e11": "not scheduled; prior incomplete workflow retained; computational labels support concordance only",
        },
        "launch_prerequisites": ["authorization", "exact_requested_model", "observed_model_and_cli_identity",
            "scientific_precision_design", "fresh_seeds_and_untouched_evaluation_inputs",
            "immutable_input_receipts", "frozen_methods_and_evaluators", "runtime_permission_preflights",
            "approved_provider_session_budget_including_probes", "unchanged_source_and_runtime"],
    }
    return _seal(Path(output), "comparison.json", record, "evaluation.readiness",
                 scope="offline_configuration_no_execution_authority")


def verify(manifest, expected_root):
    """Authenticate frozen metadata; neither execute nor certify scientific fitness."""
    _, record, _ = _load(Path(manifest), expected_root, "comparison.json")
    if record.get("format") != "devbench.workbench-comparison-readiness/1":
        raise ValueError("Unknown comparison package format")
    biology = validate_biology_plan(BiologyStudyPlan.model_validate_json(canonical_json_bytes(record["plans"]["synthetic"])))
    public = PublicStudyPlan.model_validate_json(canonical_json_bytes(record["plans"]["public"]))
    expected = {"synthetic": [row.model_dump(mode="json") for row in schedule_biology_attempts(biology)],
                "public": schedule_public_attempts(public)}
    if record["schedules"] != expected:
        raise ValueError("Schedule differs from the frozen existing plans")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path, help="New canonical protected directory; configuration only")
    mode.add_argument("--verify", type=Path, help="Saved audit/manifest.json")
    parser.add_argument("--expected-root")
    args = parser.parse_args()
    if args.output:
        if args.expected_root:
            parser.error("--expected-root is only for verification")
        result = prepare(args.output)
    else:
        if not args.expected_root:
            parser.error("Verification requires an externally retained root")
        record = verify(args.verify, args.expected_root)
        result = {"valid": True, "status": record["status"], "execution": record["execution"],
                  "source_matches_current": record["source_sha256"] == _source_identity(),
                  "live_readiness": False}
    print(json.dumps(result, allow_nan=False, sort_keys=True))


if __name__ == "__main__":
    main()
