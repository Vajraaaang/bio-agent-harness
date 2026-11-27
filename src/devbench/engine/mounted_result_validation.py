"""Verify the mounted worker's columnar inventory before it can be admitted."""

from devbench.analysis.episode_evidence_queries import (
    _open_evidence_parquet,
    _validate_parquet_rows,
)
from rna_kernels.canonical import canonical_json

_COLUMNS = (
    "gene_id",
    "hypothesis_id",
    "estimate_status",
    "coordinate_ids",
    "values",
    "intervals",
    "effect",
    "p_value",
    "q_value",
    "missing_reason",
)


def _validate_row_coordinates(data, estimated, effects):
    import pyarrow.compute as pc

    counts = pc.cast(effects, "int32")
    for field in ("coordinate_ids", "values"):
        if pc.any(pc.not_equal(pc.list_value_length(data[field]), counts)).as_py():
            raise ValueError(
                "Mounted result coordinates or values differ from estimation status"
            )
    if (
        pc.any(
            pc.not_equal(pc.list_flatten(data["coordinate_ids"]), "mean_contrast")
        ).as_py()
        or pc.any(
            pc.not_equal(pc.list_flatten(data["values"]), pc.drop_null(data["effect"]))
        ).as_py()
        or pc.any(pc.equal(pc.is_valid(data["missing_reason"]), estimated)).as_py()
    ):
        raise ValueError(
            "Mounted result effect coordinates or missing reasons are inconsistent"
        )


def _verify_routed_method(solver, details, eligible, columns):
    from devharness_controlled.native_contracts import KERNEL_VERSIONS

    stats = {"df.prior", "df.total", "s2.post"}
    if solver == "edger_v4":
        engine = "edgeR"
        stats.update(("F", "df.residual.adj"))
        configured = (
            details.get("robust") is True
            and details.get("legacy") is False
            and details.get("abundance_trend") is (eligible >= 500)
        )
    elif solver in ("limma_voom_qw", "limma_voom"):
        engine = "limma"
        weights = "voomWithQualityWeights" if solver == "limma_voom_qw" else "voom"
        stats.add("t")
        configured = details.get("robust") is True and details.get("weights") == weights
    else:
        engine = "variancePartition::dream"
        stats.add("t")
        configured = solver == "dream" and details.get("voom_weights") is True

    if (
        not configured
        or not stats <= columns
        or details.get("engine") != engine
        or not details.get("version")
        or engine in KERNEL_VERSIONS
        and details["version"] != KERNEL_VERSIONS[engine]
        or details.get("normalization") != "TMM"
        or details.get("filter") != "filterByExpr"
        or details.get("n_eligible_genes") != eligible
    ):
        raise ValueError("Mounted model evidence differs from the frozen count route")


def _verify_route(summary, spec, inputs):
    """Recompute arbitration from the original digest-bound design."""
    import csv
    import io

    from rna_kernels.mounted_bulk import _metadata, _read_bound_file, _request
    from rna_kernels.mounted_model import MountedCohort, freeze_mounted_solver

    request = _request(inputs, summary["request_sha256"])
    raw = _read_bound_file(inputs / "metadata.csv", spec.metadata_sha256, 5_000_000)
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8")), strict=True))
    arrays, condition, cohort = _metadata(raw, [r["sample_id"] for r in rows], request)
    if cohort is None:
        # Preserve the implicit two-arm protocol's own nuisance/design semantics.
        design, units, strata, covariates = arrays
        labels = [r[request["condition_column"]] for r in rows]
        cohort = MountedCohort(
            design, condition, units, labels, strata, covariates, None, None
        )
        request["model"] = {"units": "independent", "comparison": "coefficient"}
    choice = freeze_mounted_solver(cohort, request, spec.family.primary, "bioconductor")
    details = summary.get("solver_details", {})
    companion = summary.get("sensitivity_details") or {}
    if (
        not choice.regime_route
        or summary["regime_route"] != choice.regime_route
        or summary.get("calibration_status") != choice.calibration_status
        or summary.get("units_per_arm") != list(cohort.units_per_arm)
        or summary.get("rank_sensitivity_planned")
        is not choice.rank_sensitivity_planned
        or summary.get("sensitivity_solver") != choice.sensitivity_solver
        or companion.get("solver") != choice.sensitivity_solver
        or summary.get("solver_version") != details.get("version")
    ):
        raise ValueError("Mounted route or calibration differs from frozen design")
    eligible = summary["row_count"] - summary["status_counts"].get("filtered", 0)
    columns = set(summary.get("model_columns", ()))
    _verify_routed_method(choice.solver, details, eligible, columns)
    if choice.sensitivity_solver:
        secondary = {
            k.removeprefix("sensitivity_")
            for k in columns
            if k.startswith("sensitivity_")
        }
        if not {"effect", "p_value", "q_value", "code"} <= secondary:
            raise ValueError("Mounted comparator differs from the frozen count route")
        _verify_routed_method(choice.sensitivity_solver, companion, eligible, secondary)
    return choice


def verify_mounted_solver(summary, spec, *, inputs=None):
    from rna_kernels.bulk_solver_policy import freeze_bulk_solver

    model = spec.mounted_model
    if "calibration_status" in summary and not summary.get("regime_route"):
        raise ValueError("Calibration receipt requires a frozen regime route")
    if summary.get("regime_route"):
        if inputs is None:
            raise ValueError("Routed receipt requires the original staged design")
        choice = _verify_route(summary, spec, inputs)
    elif model is None:
        return
    else:
        heterogeneous = getattr(model, "heterogeneous_independent", False)
        if (
            heterogeneous
            and spec.family.primary
            and getattr(model, "sensitivity_method", None) is None
        ):
            raise ValueError(
                "Heterogeneous primary receipt requires a frozen regime route"
            )
        choice = freeze_bulk_solver(
            measurement=spec.measurement,
            runtime="bioconductor",
            comparison=model.comparison,
            units=model.units,
            primary=spec.family.primary,
            single_factor=not (
                spec.design.technical_factors or spec.continuous_covariates
            ),
            sensitivity_method=model.sensitivity_method,
            heterogeneous_independent=heterogeneous,
        )
    degrees = summary.get("test_df")
    if (
        (summary.get("solver"), summary.get("test"), summary.get("shrinkage"))
        != (choice.solver, choice.test, choice.shrinkage)
        or type(degrees) is not int
        or degrees < 1
        or (model is None or model.comparison in {"coefficient", "contrast"})
        and degrees != 1
    ):
        raise ValueError("Mounted solver and test differ from the frozen arbitration")
    if choice.solver == "limma_voom":
        from devharness_controlled.native_contracts import KERNEL_VERSIONS

        details = summary.get("solver_details")
        if (
            summary.get("solver_version") != KERNEL_VERSIONS["limma"]
            or not isinstance(details, dict)
            or details.get("normalization") != "full_family_RLE"
            or details.get("variance_model")
            != "voom_observation_weights_robust_empirical_bayes"
        ):
            raise ValueError("Mounted limma evidence differs from the pinned procedure")


def mounted_method_evidence(result):
    method = {
        key: result[key]
        for key in (
            "solver",
            "solver_version",
            "test",
            "test_df",
            "shrinkage",
            "comparison",
            "design_rank",
            "residual_df",
            "condition_number",
        )
        if key in result
    }
    if result.get("regime_route"):
        details = result["solver_details"]
        method.update(
            regime_route=result["regime_route"],
            calibration_status=result["calibration_status"],
            normalization=details.get("normalization"),
            filter=details.get("filter"),
            abundance_trend=details.get("abundance_trend"),
            engine=details.get("engine"),
            sensitivity_solver=result.get("sensitivity_solver"),
            sensitivity_version=(result.get("sensitivity_details") or {}).get(
                "version"
            ),
            units_per_arm=result.get("units_per_arm"),
        )
    if method.get("solver") == "limma_voom":
        method.update(
            analysis_role="nonprimary_sensitivity",
            normalization=result["solver_details"]["normalization"],
            variance_model=result["solver_details"]["variance_model"],
        )
    if len(canonical_json(method)) > 4_096:
        raise ValueError("Mounted method evidence exceeds its receipt bound")
    return method


def verify_mounted_primary(artifact, *, open_artifact, summary, spec):
    import pyarrow.compute as pc

    with _open_evidence_parquet(artifact, open_artifact) as (reader, footer):
        expected_footer = {
            "format": "devbench.tier-a-result/1",
            "target_id": "mean_contrast",
            "family_id": spec.family.family_id,
            "measurement": spec.measurement,
            "biological_unit": spec.biological_unit,
            "claim_scope": "admitted_research_inference",
            **{
                key: summary[key]
                for key in (
                    "row_count",
                    "status_counts",
                    "tested_count",
                    "request_sha256",
                    "family_sha256",
                )
            },
        }
        model = getattr(spec, "mounted_model", None)
        if model is not None:
            expected_footer["comparison"] = model.comparison
        if footer != expected_footer:
            raise ValueError(
                "Mounted result footer differs from the frozen request or summary"
            )
        model_columns = tuple(summary.get("model_columns", ()))
        if tuple(reader.schema_arrow.names) != (*_COLUMNS, *model_columns):
            raise ValueError("Mounted result has unexpected Parquet columns")
        data = reader.read(use_threads=False)
        _validate_parquet_rows(data, footer, ("effect", "p_value", "q_value"))
        if tuple(data["gene_id"].to_pylist()) != spec.family.feature_ids:
            raise ValueError(
                "Mounted result does not retain the original ordered feature family"
            )
        estimated = pc.equal(data["estimate_status"], "estimated")
        effects = (
            pc.and_(estimated, False)
            if footer.get("comparison") in {"nested", "lrt"}
            else estimated
        )
        observed = int(pc.sum(estimated).as_py())
        if (
            observed != footer["tested_count"]
            or any(
                pc.any(pc.not_equal(estimated, pc.is_valid(data[field]))).as_py()
                for field in ("p_value", "q_value")
            )
            or pc.any(pc.not_equal(effects, pc.is_valid(data["effect"]))).as_py()
        ):
            raise ValueError("Mounted result estimates do not match tested-row status")
        _validate_row_coordinates(data, estimated, effects)
