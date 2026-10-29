"""Registered, bounded bulk contrast adapter over the existing study core.

This module contains no estimator. Execution permission belongs to the calling
governed workflow; a parameter cannot confer it. The returned core result keeps
scientific refusals and failed genes distinct from successful tool execution.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import tempfile


def validate_bulk_contrast_parameters(public_task: dict, options: dict) -> tuple[dict, dict]:
    """Validate an explicit mean-contrast binding without reading any inputs."""
    from devbench.study.biology_tool_io import validate_public_task
    from devbench.study.biology_tools_bulk import validate_bulk_options
    from devbench.study.biology_input_limits import PUBLIC_MAX_BULK_FEATURES, public_json_size

    if not isinstance(public_task, dict) or not isinstance(options, dict):
        raise ValueError("Bulk contrast requires an explicit public task and fixed options")
    public_json_size(public_task)
    public_json_size(options)
    task = json.loads(json.dumps(public_task, allow_nan=False))
    settings = json.loads(json.dumps(options, allow_nan=False))
    if task.get("measurement") not in {"raw_counts", "continuous"}:
        raise ValueError("Bulk contrast requires explicit raw_counts or continuous measurement")
    targets = task.get("questions")
    if (not isinstance(targets, list) or len(targets) != 1 or not isinstance(targets[0], dict)
            or targets[0].get("target_id") != "mean_contrast" or "targets" in task):
        raise ValueError("Registered bulk contrast supports exactly one mean_contrast")
    genes = task.get("gene_ids")
    if (not isinstance(genes, list) or not 1 <= len(genes) <= PUBLIC_MAX_BULK_FEATURES
            or any(not isinstance(gene, str) or not gene.strip() for gene in genes)
            or len(set(genes)) != len(genes)):
        raise ValueError("Supply the complete distinct gene family within the 100000-gene transport bound")
    hierarchy = task.get("unit_hierarchy")
    hierarchy_fields = {"population_unit", "population_unit_label", "specimen_label", "library_label", "identity_status"}
    if (not isinstance(hierarchy, dict) or not hierarchy_fields <= hierarchy.keys()
            or hierarchy["identity_status"] not in {"declared", "verified"}):
        raise ValueError("Bulk contrast requires an explicit known biological-unit hierarchy")
    roles = task.get("analysis_columns")
    role_fields = {"condition", "population_unit", "batches", "time", "dose", "covariate"}
    if (not isinstance(roles, dict) or not role_fields <= set(roles) or set(roles) - role_fields - {"blocking"}
            or any(roles[key] is not None for key in ("time", "dose", "covariate"))):
        raise ValueError("Bulk contrast requires explicit condition/unit/batch roles and no temporal or dose terms")
    context = task.get("context")
    requested = context.get("requested_condition_contrast") if isinstance(context, dict) else None
    if (not isinstance(requested, dict) or set(requested) != {"denominator", "numerator"}
            or any(not isinstance(value, str) or not value.strip() for value in requested.values())
            or requested["denominator"] == requested["numerator"]):
        raise ValueError("Declare the exact distinct numerator and denominator conditions")
    if (context.get("condition_labels") != [requested["denominator"], requested["numerator"]]
            or context.get("design") or task.get("observation_id_label") != "sample_id"):
        raise ValueError("Bulk contrast requires matching condition labels and the original sample-ID axis")
    # The requested scientific objective remains in the retained plan/source.
    # These labels describe only this adapter's computed quantity: the core
    # copies them into result metadata, so they cannot assert causal effects,
    # a different scale, or generalization to an unobserved population.
    canonical_target = {"target_id": "mean_contrast", "estimand": "Condition mean contrast over observed biological units",
        "units": "input units" if task["measurement"] == "continuous" else "log2 count-rate contrast",
        "contrast": requested["numerator"] + " minus " + requested["denominator"],
        "reference_population": "observed biological units", "prediction_reference": "observed biological units"}
    target = targets[0]
    if (set(target) - (set(canonical_target) | {"measurement_units"})
            or any(target.get(key) != value for key, value in canonical_target.items())
            or ("measurement_units" in target and target["measurement_units"] != canonical_target["units"])):
        raise ValueError("Bulk contrast target must use canonical computed-quantity labels and units")
    projected = validate_public_task(task)
    if (projected["measurement"] != task["measurement"] or projected["unit_hierarchy"] != hierarchy
            or projected.get("analysis_columns") != roles):
        raise ValueError("Bulk declarations must survive public validation unchanged")
    required = {"condition_col", "reference_condition", "comparison_condition", "minimum_units", "condition_number_limit"}
    if roles["batches"]:
        required.add("batch_cols")
    if roles.get("blocking"):
        required.add("block_cols")
    if not required <= set(settings) or set(settings) - required - {"batch_cols", "block_cols"}:
        raise ValueError("Supply only the complete frozen bulk condition, batch, fixed-block and numerical-policy options")
    # Canonicalize a legacy absent empty list, so downstream design cannot
    # resurrect the core's historical implicit column named 'batch'.
    settings.setdefault("batch_cols", [])
    if (settings["condition_col"] != roles["condition"]
            or settings["reference_condition"] != requested["denominator"]
            or settings["comparison_condition"] != requested["numerator"]
            or settings["batch_cols"] != roles["batches"]
            or settings.get("block_cols", []) != roles.get("blocking", [])):
        raise ValueError("Fixed options cannot change declared condition, contrast, batch or fixed-block terms")
    return task, validate_bulk_options(settings)


def bulk_condition_contrast(counts_path: str, metadata_path: str, public_task: dict, options: dict) -> dict:
    """Fit the frozen two-condition biological-unit contrast with the existing core.

Counts use the existing RLE/empirical-Bayes negative-binomial implementation;
continuous measurements use its Gaussian implementation. All declared genes
    remain in the attempted testing family. Explicit paired fixed blocks use
    that same estimator; non-identifiable designs retain the core's refusal.
    This adapter cannot run arbitrary source or formulas.
"""
    task, settings = validate_bulk_contrast_parameters(public_task, options)
    for value in (counts_path, metadata_path):
        path = Path(value)
        if not path.is_absolute() or path.is_symlink() or path.resolve() != path or path.suffix.lower() != ".csv":
            raise ValueError("Registered bulk contrast accepts only canonical absolute CSV input paths")
    from devbench.study.biology_tool_io import bounded_bytes, MAX_METADATA_BYTES
    from devbench.study.biology_tools_bulk import analyze_bulk_tool

    # The existing core has a legacy default batch column. An undeclared column
    # must not silently become part of the frozen design in this new adapter.
    body = bounded_bytes(Path(metadata_path), MAX_METADATA_BYTES)
    reader = csv.DictReader(io.StringIO(body.decode("utf-8")), strict=True)
    fields = reader.fieldnames or []
    batches = task["analysis_columns"]["batches"]
    if not batches and "batch" in fields:
        raise ValueError("Metadata batch column must be explicitly declared")
    columns = [task["analysis_columns"]["condition"], *batches,
               *task["analysis_columns"].get("blocking", [])]
    if not set(columns) <= set(fields):
        raise ValueError("A declared design column is absent from metadata")
    for row in reader:
        if any(row.get(column) is None or not row[column].strip() for column in columns):
            raise ValueError("Declared design columns require complete values")
    # Mean-condition inference consumes no annotation resource. Keep its empty
    # input deterministic and transient; no user annotation or model is loaded.
    with tempfile.TemporaryDirectory(prefix="devharness-bulk-contrast-", dir=Path(counts_path).parent) as temporary:
        annotation = Path(temporary).resolve() / "annotations.json"
        annotation.write_text("{}", encoding="utf-8")
        return analyze_bulk_tool("mean_condition_analysis", task,
            counts_path=counts_path, metadata_path=metadata_path, annotations_path=str(annotation),
            options=settings, allow_data_execution=True)
