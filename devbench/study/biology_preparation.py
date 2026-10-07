"""Explicit, supervised preparation of a frozen biological study.

The configuration functions never generate observations. Actual preparation
requires literal authorization; each cohort uses a separately supervised process
and retains failures. No public dataset is silently replaced by synthetic data.
"""
from pathlib import Path
import hashlib

from .biology_planning import biology_task_identity, validate_biology_plan
from .contracts import canonical_json_bytes, content_sha256


def validate_biology_preparation(plan):
    """Validate declarations only, before creating any data artifacts."""
    plan = validate_biology_plan(plan)
    if any(recipe.generation_seed is None for recipe in plan.recipes):
        raise ValueError("Explicit generation seeds must be frozen before preparing a study")
    if plan.budget.token_budget is not None:
        raise ValueError("The process supervisor cannot enforce a token ceiling")
    return plan


def _prepare_biology_cohort_worker(recipe, plan_identity, cohort_id, bundle_root, result_path,
                                  *, allow_data_execution=False):
    if allow_data_execution is not True:
        raise PermissionError("Biological data preparation is disabled")
    from .biology_datasets import prepare_biology_dataset
    from .supervision import write_coordinator_json
    bundle = prepare_biology_dataset(recipe, bundle_root, plan_identity=plan_identity,
                                    cohort_id=cohort_id, allow_data_execution=True)
    write_coordinator_json(result_path, bundle.as_dict(), allow_data_execution=True)


def prepare_biology_study_data(plan, output_dir, *, allow_data_execution=False, evaluation_only=False):
    """Prepare the selected roles without changing the frozen study plan.

    Call this from a main-guarded script because the worker runtime uses spawn.
    The returned receipt's SHA must be retained separately for later verification.
    A failed cohort has a retained directory and error; it is never regenerated
    in place. Parent bounded receipt verification is outside sampled worker RSS.
    """
    if allow_data_execution is not True:
        raise PermissionError("Biological data preparation is disabled")
    if type(evaluation_only) is not bool:
        raise ValueError("evaluation_only must be boolean")
    plan = validate_biology_preparation(plan)
    selected = tuple(recipe for recipe in plan.recipes if not evaluation_only or recipe.role == "evaluation")
    from .biology import load_biology_catalog
    from .biology_datasets import (_generator, _source_hashes, PreparedBiologyBundle,
                                   verify_prepared_biology_bundle)
    from .evidence import _read, decode_json
    from .permissions import require_protected_storage
    from .supervision import supervise_coordinator, write_coordinator_json
    output = Path(output_dir)
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError("Preparation output must not contain symlinks")
    output = require_protected_storage(output)
    catalog = load_biology_catalog()
    expected_sources = {}
    for recipe in selected:
        expected_sources[recipe.recipe_id] = _source_hashes(_generator(recipe, catalog).handler)[0]
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    (output / "cohorts").mkdir(mode=0o700)
    write_coordinator_json(output / "plan.json", plan.model_dump(mode="json"), allow_data_execution=True)
    rows, bundles = [], {}
    interrupted = False
    for recipe in selected:
        directory = output / "cohorts" / ("cohort-" + biology_task_identity(plan, recipe)[:24])
        directory.mkdir(mode=0o700)
        control = directory / "coordinator"
        control.mkdir(mode=0o700)
        row = {"recipe_id": recipe.recipe_id, "role": recipe.role,
               "status": "blocked_after_interruption" if interrupted else "preparation_pending",
               "bundle": None, "supervision": None, "error": None}
        if not interrupted:
            try:
                current = _source_hashes(_generator(recipe, catalog).handler)[0]
                if current != expected_sources[recipe.recipe_id]:
                    raise ValueError("Generator sources changed after study preparation was frozen")
                observation = supervise_coordinator(_prepare_biology_cohort_worker,
                    (recipe, plan.sha256(), recipe.recipe_id, directory / "bundle", control / "preparation_result.json"),
                    control, plan.budget, result_name="preparation_result.json", checkpoint_names=(),
                    allow_data_execution=True)
                row["supervision"] = observation.as_dict()
                interrupted = observation.interrupted
                if observation.failure_kind is not None:
                    raise RuntimeError(f"{observation.failure_kind}: {observation.error}")
                receipts = [artifact for artifact in observation.artifacts if artifact.kind == "result"]
                if len(receipts) != 1:
                    raise ValueError("Preparation worker did not retain exactly one result receipt")
                raw = _read(receipts[0].path, 10_000_000)
                if hashlib.sha256(raw).hexdigest() != receipts[0].sha256:
                    raise ValueError("Preparation result changed after supervisor capture")
                bundle = PreparedBiologyBundle.model_validate_json(canonical_json_bytes(decode_json(raw)))
                if (Path(bundle.root) != directory / "bundle" or bundle.cohort_id != recipe.recipe_id
                        or bundle.source_hashes != expected_sources[recipe.recipe_id]):
                    raise ValueError("Preparation result differs from its assigned cohort/source boundary")
                bundle = verify_prepared_biology_bundle(bundle, recipe, plan.sha256(), allow_data_execution=True)
                bundles[recipe.recipe_id] = bundle.as_dict()
                row.update(status="prepared", bundle=bundle.as_dict())
            except KeyboardInterrupt:
                interrupted = True
                row.update(status="preparation_interrupted", error="KeyboardInterrupt: parent preparation interrupted")
            except (ValueError, TypeError, OSError, RuntimeError, KeyError) as error:
                row.update(status="preparation_failed", error=f"{type(error).__name__}: {error}"[:2000])
        rows.append(row)
        write_coordinator_json(directory / "status.json", row, allow_data_execution=True)
    manifest = {"record_format": "devbench.biology.prepared-study/1", "plan_identity": plan.sha256(),
                "selection_scope": "evaluation_only" if evaluation_only else "all_roles",
                "excluded_recipe_ids": [recipe.recipe_id for recipe in plan.recipes if recipe not in selected],
                "cohorts": bundles, "source_freeze_sha256": content_sha256(expected_sources),
                "failed_or_blocked_cohort_ids": [row["recipe_id"] for row in rows if row["status"] != "prepared"]}
    write_coordinator_json(output / "bundles.json", manifest, allow_data_execution=True)
    receipt = {"record_format": "devbench.biology.preparation/1", "plan_identity": plan.sha256(),
        "status": "prepared" if len(bundles) == len(selected) else "preparation_incomplete",
        "planned_cohorts": len(selected), "frozen_plan_cohorts": len(plan.recipes), "prepared_cohorts": len(bundles),
        "selection_scope": manifest["selection_scope"], "excluded_recipe_ids": manifest["excluded_recipe_ids"],
        "model_participants_launched": 0, "source_freeze": expected_sources,
        "bundles": str(output / "bundles.json"),
        "bundles_sha256": hashlib.sha256(_read(output / "bundles.json", 32_000_000)).hexdigest(),
        "cohorts": rows, "measurement_scope": "data_preparation_only",
        "parent_receipt_verification_RSS_MB": None, "clinical_qualification": False}
    write_coordinator_json(output / "preparation.json", receipt, allow_data_execution=True)
    return {"status": receipt["status"], "plan_identity": plan.sha256(),
        "planned_cohorts": len(selected), "prepared_cohorts": len(bundles),
        "selection_scope": manifest["selection_scope"], "excluded_recipe_ids": manifest["excluded_recipe_ids"],
        "bundles": receipt["bundles"], "bundles_sha256": receipt["bundles_sha256"],
        "receipt": str(output / "preparation.json"),
        "receipt_sha256": hashlib.sha256(_read(output / "preparation.json", 64_000_000)).hexdigest(),
        "model_participants_launched": 0, "clinical_qualification": False}
