"""Frozen fresh-seed validation; all predictions precede all private scoring."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import sys


def _digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _save(path, value):
    with Path(path).open("x") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def build_holdout_plan(previous, *, templates=("T01", "T06")):
    from devbench.study.biology import BiologyRecipe, load_biology_catalog
    from devbench.study.biology_planning import default_biology_recipe
    from .validation import PROTOCOL

    if set(templates) != {"T01", "T06"} or len(templates) != 2:
        raise ValueError("The authorized held-out check covers T01 and T06 only")
    prior = {r["template_id"]: r for r in previous["recipes"] if r["role"] == "evaluation"}
    used = {r.get("generation_seed") for r in previous["recipes"]}
    cases = {"T01": "T01-dense", "T06": "T06-aliased"}
    recipes = []
    catalog = load_biology_catalog()
    for template in templates:
        base = prior.get(template) or default_biology_recipe(cases[template], num_genes=120).model_dump(mode="json")
        if base["case_id"] != cases[template]:
            raise ValueError("Development case differs from the authorized held-out template")
        for index in range(1, 4):
            seed = secrets.randbits(63)
            while seed in used:
                seed = secrets.randbits(63)
            used.add(seed)
            value = dict(base, recipe_id=f"{template}-fresh-{index}", generation_seed=seed, role="evaluation")
            recipe = BiologyRecipe.model_validate_json(json.dumps(value))
            recipe.validate_against(catalog)
            recipes.append(recipe.model_dump(mode="json"))
    return {"record_format": "devbench.analysis.fresh-seed-plan/1", "recipes": recipes,
            "workbench_gates": PROTOCOL, "tuning_after_generation_allowed": False,
            "prediction_policy": "seal_all_six_outputs_before_any_truth_scoring",
            "scope": "new_random_realizations_of_existing_T01_T06_mechanisms_not_mechanism_holdout"}


def source_freeze():
    root = Path(__file__).resolve().parents[1]
    files = [Path(__file__).resolve(), *[root / "analysis" / name for name in
             ("validation.py", "inference.py", "resources.py", "__init__.py")]]
    repo = root.parent
    for directory in (root / "study", repo / "devharness_core", repo / "devharness_server"):
        files.extend(directory.glob("*.py"))
    files.extend([root / "study/biology_catalog.json", repo / "pyproject.toml", repo / "uv.lock"])
    return {str(path): _digest(path) for path in sorted(set(files))}


def verify_source_freeze(frozen):
    for name, expected in frozen.items():
        if _digest(name) != expected:
            raise RuntimeError("Frozen inference or generator source changed: " + name)


def _blind_worker(inputs, destination, coordinator_root):
    """Fresh worker receives observed inputs only and cannot open private truth."""
    forbidden = Path(coordinator_root).resolve()

    def audit(event, arguments):
        if event != "open" or not arguments or not isinstance(arguments[0], (str, bytes)):
            return
        path = Path(arguments[0].decode() if isinstance(arguments[0], bytes) else arguments[0]).resolve()
        if path.name == "truth.json" or path.is_relative_to(forbidden):
            raise PermissionError("Private reference access is forbidden during blind inference")

    sys.addaudithook(audit)
    from .validation import analyze_observed_inputs
    from .inference import adjust_selected_model_tests
    from .resources import StageMeasurement
    with StageMeasurement("blind_adjusted_inference") as stage:
        target, raw = analyze_observed_inputs(Path(inputs))
        adjusted = adjust_selected_model_tests(raw)
    _save(Path(destination) / "analysis-original.json", raw)
    _save(Path(destination) / "analysis.json", adjusted)
    _save(Path(destination) / "measurement.json", stage.result)
    return {"target_id": target, "prediction_sha256": _digest(Path(destination) / "analysis.json"),
            "measurement": stage.result, "blindness": "separate_spawn_worker_public_inputs_and_private_read_audit"}


def run_holdout(previous_plan, output):
    from devbench.study.biology import BiologyRecipe
    from devbench.study.biology_datasets import prepare_biology_dataset
    from devbench.study.contracts import content_sha256
    from devharness_core.watchdog import run_sandboxed
    from .resources import StageMeasurement
    from .validation import score_known_truth

    output = Path(output)
    if not output.is_absolute():
        raise ValueError("Held-out output must be absolute")
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    coordinator, observed, predictions = (output / name for name in ("coordinator", "observed", "predictions"))
    for path in (coordinator, observed, predictions):
        path.mkdir(mode=0o700)
    plan = build_holdout_plan(json.loads(Path(previous_plan).read_text()))
    frozen = source_freeze()
    plan["source_freeze"] = frozen
    identity = content_sha256(plan)
    _save(coordinator / "plan.json", plan)
    _save(output / "freeze-receipt.json", {"plan_sha256": identity, "source_sha256": content_sha256(frozen),
          "gates_sha256": content_sha256(plan["workbench_gates"]), "truth_scoring_started": False})
    entries = []
    # Stage every new dataset without reading reference values in this process.
    for value in plan["recipes"]:
        verify_source_freeze(frozen)
        recipe = BiologyRecipe.model_validate_json(json.dumps(value))
        opaque = "cohort-" + secrets.token_hex(8)
        entry = {"cohort_id": recipe.recipe_id, "template_id": recipe.template_id, "opaque_id": opaque}
        try:
            with StageMeasurement("generate_and_stage") as phase:
                bundle = prepare_biology_dataset(recipe, coordinator / opaque, plan_identity=identity,
                    cohort_id=opaque, allow_data_execution=True)
                shutil.copytree(Path(bundle.root) / "inputs", observed / opaque)
            entry.update(status="prepared", bundle=bundle.as_dict(), preparation_measurement=phase.result)
        except Exception as error:
            entry.update(status="preparation_failed", error=f"{type(error).__name__}: {error}"[:2000])
        entries.append(entry)
    _save(coordinator / "preparation.json", entries)
    for entry in entries:
        verify_source_freeze(frozen)
        if entry["status"] != "prepared":
            continue
        destination = predictions / entry["opaque_id"]
        destination.mkdir(mode=0o700)
        try:
            result = run_sandboxed(_blind_worker,
                (str(observed / entry["opaque_id"]), str(destination), str(coordinator)),
                timeout_sec=75, max_mem_mb=3072)
            entry.update(status="inference_completed", **result)
        except Exception as error:
            entry.update(status="inference_failed", error=f"{type(error).__name__}: {error}"[:2000])
            _save(destination / "failure.json", {"status": entry["status"], "error": entry["error"]})
        print(json.dumps({"completed_attempts": sum(x["status"] in {"inference_completed", "inference_failed"} for x in entries),
                          "total_cohorts": len(entries), "truth_scoring_started": False}), flush=True)
    verify_source_freeze(frozen)
    seal = {entry["opaque_id"]: entry.get("prediction_sha256") for entry in entries}
    _save(output / "predictions-seal.json", {"plan_sha256": identity, "predictions": seal,
          "all_inference_attempts_finished_before_truth_read": True})
    for entry in entries:
        if entry["status"] != "inference_completed":
            entry["scorecard"] = None
            continue
        verify_source_freeze(frozen)
        destination = predictions / entry["opaque_id"]
        if _digest(destination / "analysis.json") != entry["prediction_sha256"]:
            raise RuntimeError("Held-out predictions changed before scoring")
        truth_path = Path(entry["bundle"]["root"]) / "private/truth.json"
        truth = json.loads(truth_path.read_text())
        entry["reference_sha256"] = _digest(truth_path)
        entry["scorecard"] = score_known_truth(json.loads((destination / "analysis.json").read_text()), truth, entry["target_id"])
        _save(destination / "scorecard.json", entry["scorecard"])
    verify_source_freeze(frozen)
    summary = {"record_format": "devbench.analysis.heldout-results/1", "plan_sha256": identity,
               "source_unchanged": True, "gates_unchanged": True, "cohorts": entries,
               "counts": dict(Counter(entry["status"] for entry in entries)),
               "passed": sum(bool(entry.get("scorecard", {}).get("passed")) for entry in entries if entry.get("scorecard")),
               "development_cohort_included": False, "model_calls": 0,
               "scope": "six_fresh_seed_numerical_checks_not_live_agent_performance_or_clinical_validation"}
    _save(output / "report.json", summary)
    lines = ["# Fresh-seed held-out numerical check", "", "Method and gates were frozen before generation. All predictions were sealed before private scoring. No further tuning was performed.", "",
             "| Cohort | Status | Gate | Direction | RMSE log2 | Null false positives | Realized FDP | Signal recall | Correct refusal |",
             "|---|---|---|---:|---:|---:|---:|---:|---|"]
    for entry in entries:
        score = entry.get("scorecard") or {}
        metrics = score.get("metrics", {})
        def value(name):
            item = metrics.get(name)
            return "unknown" if item is None else f"{item:.4f}" if type(item) is float else str(item)
        gate = str(score.get("passed", "unknown"))
        lines.append(f"| {entry['cohort_id']} | {entry['status']} | {gate} | {value('direction_accuracy')} | {value('effect_rmse_log2')} | {value('false_positives_on_nulls')} | {value('false_discovery_proportion')} | {value('signal_recall')} | {value('correct_refusal')} |")
    lines.extend(["", "Three cohorts per template provide limited new-seed evidence for these generator settings. They do not establish repeated-sampling error control, general biological validity, or benefit over a live unassisted agent.", ""])
    (output / "report.md").write_text("\n".join(lines))
    return summary


def main():
    from .resources import configure_threads
    configure_threads()
    parser = argparse.ArgumentParser(description="Six frozen blind numerical runs, not benchmark agents")
    parser.add_argument("--previous-plan", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    summary = run_holdout(args.previous_plan, args.out)
    print(json.dumps({"counts": summary["counts"], "passed": summary["passed"], "report": str(args.out / "report.md")}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
