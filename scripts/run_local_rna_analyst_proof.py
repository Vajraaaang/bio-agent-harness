"""Bounded synthetic execution proof using the existing episode/driver APIs.

All decisions and inputs are hand-authored software fixtures. Numerical replay
is a self-match, not independent numerical validation or a model activation gate.
No external dataset, model, network, or workflow runner is used.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import csv
import hashlib
import json
from pathlib import Path
import sys


def prepare_fixture(root, *, measurement, failure=None):
    """Write twelve genes and eight libraries, with explicit unit declarations."""
    root = Path(root)
    if not root.is_absolute() or root.exists():
        raise ValueError("Use a fresh absolute fixture directory")
    if measurement not in {"raw_counts", "continuous"} or failure not in {None, "confounded", "paired"}:
        raise ValueError("Use a declared bounded fixture scenario")
    root.mkdir(parents=True)
    samples = [f"s{i}" for i in range(8)]
    rows = [{"sample_id": sample, "donor_id": f"d{i % 4 if failure == 'paired' else i}",
             "specimen_id": f"p{i}", "library_id": f"l{i}",
             "condition": "A" if i < 4 else "B",
             "batch": ("x" if i < 4 else "y") if failure == "confounded" else "x"}
            for i, sample in enumerate(samples)]
    metadata = root / "metadata.csv"
    with metadata.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    genes = [f"gene_{j}" for j in range(12)]
    expression = root / "expression.csv"
    with expression.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["gene_id", *samples])
        for j, gene in enumerate(genes):
            values = ([10 + (i * 3 + j * 5) % 9 + (j % 3 if i >= 4 else 0) for i in range(8)]
                      if measurement == "raw_counts" else
                      [1.0 + (i % 2) + (4 if i >= 4 else 0) + j * .1 for i in range(8)])
            writer.writerow([gene, *values])
    annotations = root / "annotations.json"
    annotations.write_text("{}\n")
    task = {"measurement": measurement, "population_unit_label": "donor_id",
        "observation_id_label": "sample_id",
        "context": {"biological_unit": "donor", "condition_labels": ["A", "B"]},
        "analysis_columns": {"condition": "condition", "time": None, "dose": None,
            "covariate": None, "population_unit": "donor_id", "batches": ["batch"]},
        "questions": [{"target_id": "mean_contrast", "estimand": "Donor condition contrast",
            "units": "log2 count-rate contrast" if measurement == "raw_counts" else "input units",
            "contrast": "B minus A", "reference_population": "declared synthetic donors",
            "prediction_reference": "observed donor population"}], "gene_ids": genes}
    return task, {"expression": expression, "metadata": metadata, "annotations": annotations}


class FixtureSelector:
    """Recorded actions only; the existing driver owns all execution state."""
    def __init__(self, failure=None):
        tools = ["audit_expression_quality", "validate_biological_units", "validate_design_matrix"]
        if failure is None:
            tools.append("mean_condition_analysis")
        self.actions = iter([{"kind": "tool", "tool": tool} for tool in tools] +
                            ([{"kind": "finish"}] if failure is None else
                             [{"kind": "refuse", "reason": "Retain the diagnostic refusal; do not simplify the design"}]))

    def configuration(self):
        return {"backend": "offline_scripted_fixture", "model_execution": False}

    def select(self, context, output, *, timeout_seconds, memory_mb, allow_model_execution=False):
        from devbench.analysis.episode_agent import Selection
        if allow_model_execution:
            raise PermissionError("This proof never authorizes model execution")
        return Selection(next(self.actions), {"backend": "offline_scripted_fixture",
            "model_sessions_attempted": 0, "actual_model": None, "tokens": None, "cost_usd": None})


def software_proof_passed(results):
    """Accept only the exercised software controls, never scientific validity."""
    return bool(results) and all(row["actual_outcome"] == row["expected_outcome"] and row["artifact_integrity"]
        and row["input_bytes_unchanged"] and row["model_sessions_attempted"] == 0
        and (row["diagnostic_refusal"] == "pass" or
             row["numerical_replay"] == "pass" and row.get("replay_artifact_integrity") is True)
        for row in results)


def proof_passed(results):
    """Report a scientific trial pass only from a separately supplied evaluation.

    This fixture driver does not create independent evaluation records. A
    supported refusal or numerical replay alone cannot satisfy this predicate.
    The referenced evaluation must be verified by its owning evaluator; this
    helper does not confer scientific correctness or execution permission.
    """
    return software_proof_passed(results) and all(
        row["actual_outcome"] == "completed"
        and row.get("scientific_correctness") is True
        and row.get("scientific_correctness_source") == "independent_evaluation"
        and isinstance(row.get("scientific_evaluation_reference"), str)
        and bool(row["scientific_evaluation_reference"].strip())
        for row in results)


def run_proof(output, *, allow_data_execution=False):
    if allow_data_execution is not True:
        raise PermissionError("Synthetic proof requires explicit data execution authorization")
    root = Path(output)
    repository = Path(__file__).resolve().parents[1]
    if (not root.is_absolute() or root.resolve() != root or root.exists()
            or not root.is_relative_to(repository) or root == repository):
        raise ValueError("Use a fresh absolute output directory inside this repository")
    from devbench.analysis.resources import configure_threads
    configure_threads()
    from devbench.analysis.episode_agent import drive_episode
    from devbench.analysis.episodes import AnalysisEpisode, replay_episode, verify_episode
    from devharness_core.provenance import canonical_json

    root.mkdir(parents=True)
    results = []
    for label, measurement, failure in (("counts", "raw_counts", None),
            ("continuous", "continuous", None), ("confounded", "continuous", "confounded"),
            ("paired", "raw_counts", "paired")):
        case = root / label
        task, inputs = prepare_fixture(case / "source", measurement=measurement, failure=failure)
        original = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in inputs.items()}
        (case / "fixture.json").write_bytes(canonical_json({"origin": "hand_authored_software_fixture",
            "public_task": task, "inputs": {key: str(path) for key, path in inputs.items()},
            "expected_outcome": "refusal" if failure else "completed",
            "scope": "execution_control_not_method_comparison_or_biological_validation"}))
        episode = AnalysisEpisode.create(case / "episode", question="Estimate B minus A in declared synthetic donors",
            assay="bulk", public_task=task, inputs=inputs, allow_data_execution=True)
        final = drive_episode(episode, FixtureSelector(failure), allow_data_execution=True)
        if episode.receipt is None:
            raise ValueError("The proof did not retain a terminal receipt")
        verified = verify_episode(episode.root, episode.receipt["root_hash"])
        record = {"case": label, "measurement": measurement, "genes": 12, "libraries": 8,
            "declared_donors": 4 if failure == "paired" else 8,
            "expected_outcome": "refusal" if failure else "completed", "actual_outcome": final["outcome"],
            "execution": "not_run" if failure else "pass" if final["outcome"] == "completed" else "fail",
            "diagnostic_refusal": "pass" if failure and final["outcome"] == "refusal" else "not_applicable" if not failure else "fail",
            "numerical_replay": "not_run", "numerical_reference": "same_implementation_self_match",
            "biological_agreement": "not_run", "model_sessions_attempted": final["agent_execution"]["model_sessions_attempted"],
            "model_authority": "not_granted_by_software_proof",
            "harness_accepted": final["harness_accepted"],
            "harness_acceptance_scope": final["harness_acceptance_scope"],
            "scientific_correctness": final["scientific_correctness"],
            "scientific_correctness_source": None, "scientific_evaluation_reference": None,
            "scientific_trial_passed": False,
            "artifact_integrity": verified["valid"], "root_hash": episode.receipt["root_hash"],
            "input_bytes_unchanged": all(hashlib.sha256(path.read_bytes()).hexdigest() == original[key] for key, path in inputs.items())}
        if not failure and final["outcome"] == "completed":
            replay = replay_episode(episode.root, episode.receipt["root_hash"], case / "replay", allow_data_execution=True)
            replay_integrity = verify_episode(replay.root, replay.receipt["root_hash"])
            record["numerical_replay"] = "pass" if (replay.outcome == "completed" and
                replay.last_result["target_results"] == episode.last_result["target_results"]) else "fail"
            record["replay_root_hash"] = replay.receipt["root_hash"]
            record["replay_artifact_integrity"] = replay_integrity["valid"]
        results.append(record)
        (root / "result.json").write_bytes(canonical_json({"format": "devbench.local-rna-proof/2", "cases": results,
            "scope": "synthetic_software_proof_not_live_agent_efficacy_or_scientific_validation",
            "exit_status_scope": "software_acceptance_only"}))
    result = json.loads((root / "result.json").read_bytes())
    result["software_accepted"] = software_proof_passed(results)
    result["scientific_trial_passed"] = proof_passed(results)
    (root / "result.json").write_bytes(canonical_json(result))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-data-execution", action="store_true")
    args = parser.parse_args(argv)
    if not args.allow_data_execution:
        parser.error("Use --allow-data-execution for the bounded synthetic proof")
    with redirect_stdout(sys.stderr):
        result = run_proof(args.output, allow_data_execution=True)
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["software_accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
