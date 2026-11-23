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


def verify_mounted_solver(summary, spec):
    from rna_kernels.bulk_solver_policy import freeze_bulk_solver

    model = spec.mounted_model
    if model is None:
        return
    choice = freeze_bulk_solver(
        measurement=spec.measurement,
        runtime="bioconductor",
        comparison=model.comparison,
        units=model.units,
        primary=spec.family.primary,
        single_factor=not (spec.design.technical_factors or spec.continuous_covariates),
        sensitivity_method=model.sensitivity_method,
    )
    degrees = summary.get("test_df")
    if (
        (summary.get("solver"), summary.get("test"), summary.get("shrinkage"))
        != (choice.solver, choice.test, choice.shrinkage)
        or type(degrees) is not int
        or degrees < 1
        or model.comparison in {"coefficient", "contrast"}
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
            "solver", "solver_version", "test", "test_df", "shrinkage",
            "comparison", "design_rank", "residual_df", "condition_number",
        )
        if key in result
    }
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
        if tuple(reader.schema_arrow.names) != _COLUMNS:
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
