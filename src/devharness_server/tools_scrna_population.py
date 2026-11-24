"""Registered supplied-population kernels under the existing worker supervisor.

The native governed caller owns execution authorization. Parameters, provenance
records and data files cannot grant access or select executable source code.
"""

from __future__ import annotations

from devharness_server._runtime_scope import require_coordinator_runtime


def _run(anndata_path, specification, stage):
    require_coordinator_runtime()
    from devbench.analysis.scrna_population_analysis import (
        bounded_result,
        run_population_analysis,
    )
    from devbench.analysis.scrna_population_contract import validate_population_spec

    validated = validate_population_spec(specification)
    result, diagnostics = _supervise(
        run_population_analysis, (anndata_path, validated, stage), validated
    )
    result["supervision"] = diagnostics
    return bounded_result(result, maximum=95_500_000)


def _supervise(worker, arguments, validated):
    from devharness_core.watchdog import run_sandboxed

    diagnostics = {}
    try:
        result = run_sandboxed(
            worker,
            arguments,
            timeout_sec=validated["limits"]["timeout_seconds"],
            max_mem_mb=validated["limits"]["memory_mb"],
            diagnostics=diagnostics,
        )
    except Exception as error:
        if (
            diagnostics
            and diagnostics.get("cleanup", {}).get("verified_stopped") is not True
        ):
            raise RuntimeError("Population worker cleanup was not verified") from error
        raise
    if diagnostics.get("cleanup", {}).get("verified_stopped") is not True:
        raise RuntimeError("Population worker cleanup was not verified")
    return result, diagnostics


def scrna_population_audit(anndata_path: str, specification: dict) -> dict:
    """Audit exact supplied-cell membership and biological-unit declarations.

Reads bounded selected metadata and identifiers only; it does not read count
values, fit models, authenticate embryo identity or validate raw-count provenance.
"""
    return _run(anndata_path, specification, "audit")


def scrna_population_design(anndata_path: str, specification: dict) -> dict:
    """Check a typed embryo fixed-effect and stage-population contrast design.

Uses metadata only. Technical columns must be one-to-one embryo aliases; stage
main effects are absorbed by embryo effects. No executable formula is accepted.
"""
    return _run(anndata_path, specification, "design")


def scrna_population_pseudobulk(anndata_path: str, specification: dict) -> dict:
    """Stream all raw count entries and sum observed embryo-population profiles.

Retains every stored feature, validates exact counts and overflow bounds, and
leaves missing population combinations absent. No statistical model is fitted.
"""
    return _run(anndata_path, specification, "pseudobulk")


def scrna_population_contrast(anndata_path: str, specification: dict) -> dict:
    """Experimental stage-specific supplied-population empirical-Bayes NB fits.

Runs and records the actual metadata audit, typed design, raw-count aggregation
and full gene-by-stage contrast family. Counts use the existing RLE/PyDESeq2
dispersion calibration and statsmodels fit. All failed hypotheses retain null
public p/q values and conservative membership of one complete BH family.
"""
    return _run(anndata_path, specification, "contrast")


def scrna_population_pseudobulk_hdf5(
    anndata_path: str, specification: dict, output_path: str
) -> dict:
    """Stage observed embryo-population counts as a verified HDF5 capsule.

Requires an explicit coordinator artifact-write grant and an unused output
path in its assigned workspace. The bounded receipt retains the file digest;
the worker fits no models and returns no inline count matrix.
"""
    require_coordinator_runtime()
    import os
    import tempfile
    from pathlib import Path

    from devbench.core.population_contract import validate_population_spec
    from devbench.platform.scrna_population_analysis import (
        _BINARY_PANEL_BYTES,
        _BINARY_SUMMARY_BYTES,
        bounded_result,
        stage_population_pseudobulk_hdf5,
    )
    from devharness_core.provenance import hash_artifact

    validated = validate_population_spec(specification)
    output = Path(output_path)
    if (
        not output.is_absolute()
        or output.parent.resolve() != output.parent
        or output.suffix != ".h5"
        or not output.parent.is_dir()
    ):
        raise ValueError(
            "Use an absolute HDF5 capsule path in a canonical staging directory"
        )
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    with tempfile.TemporaryDirectory(
        dir=output.parent, prefix=".population-capsule-"
    ) as temporary:
        staged = Path(temporary) / output.name
        result, diagnostics = _supervise(
            stage_population_pseudobulk_hdf5,
            (anndata_path, validated, staged),
            validated,
        )
        artifact = hash_artifact(staged, max_bytes=_BINARY_PANEL_BYTES, max_entries=1)
        receipt = result["matrix"]
        if (
            artifact["kind"] != "file"
            or receipt["name"] != output.name
            or receipt["sha256"] != artifact["sha256"]
            or receipt["size_bytes"] != artifact["size_bytes"]
        ):
            raise ValueError("Pseudobulk file differs from its verified worker receipt")
        result["supervision"] = diagnostics
        bounded_result(result, maximum=_BINARY_SUMMARY_BYTES)
        os.link(staged, output)
    return result


def scrna_population_contrast_parquet(
    anndata_path: str, specification: dict, output_path: str
) -> dict:
    (
        "Fit the frozen stage-specific supplied-population contrast "
        "to a Parquet family."
    )
    require_coordinator_runtime()
    import os
    import tempfile
    from pathlib import Path

    from devbench.core.population_contract import validate_population_spec
    from devbench.platform.population_parquet import verify_population_family
    from devbench.platform.scrna_population_analysis import (
        _BINARY_SUMMARY_BYTES,
        bounded_result,
        run_population_contrast_parquet,
    )

    validated = validate_population_spec(specification)
    output = Path(output_path)
    if (
        not output.is_absolute()
        or output.parent.resolve() != output.parent
        or output.suffix != ".parquet"
        or not output.parent.is_dir()
    ):
        raise ValueError(
            "Use an absolute Parquet path in a canonical staging directory"
        )
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    with tempfile.TemporaryDirectory(
        dir=output.parent, prefix=".population-family-"
    ) as temporary:
        staged = Path(temporary) / output.name
        result, diagnostics = _supervise(
            run_population_contrast_parquet,
            (anndata_path, validated, staged),
            validated,
        )
        verify_population_family(staged, result)
        result["supervision"] = diagnostics
        bounded_result(result, maximum=_BINARY_SUMMARY_BYTES)
        os.link(staged, output)
    return result
