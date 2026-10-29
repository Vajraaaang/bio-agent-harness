"""Pinned whole-family negative-binomial fit for workflow-emitted STAR counts.

This adapter does not change the in-process statistical core. It consumes direct
integer counts, rechecks the retained design and invokes PyDESeq2 0.5.4 with an
explicit numerical policy. No alternative model is available on failure.
"""
from __future__ import annotations

import csv
import hashlib
import importlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import warnings

import numpy as np
import pandas as pd

from .fastq_analysis_policy import validate_analysis_policy, apply_independent_filter, add_shrunken_effects

MODEL_VERSION = "0.5.4"
MODEL_COMMIT = "4426e4db990db1c511de3b1b9b7a514989663dad"
MODEL_WHEEL_SHA256 = "690458824f1c4df0d13dbf7e5bdc1298f6dfb444b04a6e2aef9e7d3f21ba30dd"
# Extracted from the official wheel only after its complete bytes matched the
# uv.lock hash above. Embedded so installed wheels retain the admission pin.
MODEL_SOURCE_SHA256 = {
    "pydeseq2/__init__.py": "4a41e78c6df04539a1e2d4bbce5834e8a4944d24872a9dc05442cd250ea309aa",
    "pydeseq2/__version__.py": "4e96d38f0879c0f18c42eb07f474d2d4037143fe56164068bdae58e4bd236462",
    "pydeseq2/dds.py": "9ed587b44cd93d72f7fe9a235b0916b56448f9adb9d76b5e5f7881f8ed05a8ce",
    "pydeseq2/default_inference.py": "561b79964d6c7458d29c001942937a372dab95a994af5450a40b847f9d8dc14d",
    "pydeseq2/ds.py": "4e3b229f3b16bba04f2d8f4ef0f675a751311a46b78706d7fc51e9135f20eb72",
    "pydeseq2/grid_search.py": "e916fba3e8f54e886b58f9bf3f8603197029bb198864bf4db04001d04a6f977e",
    "pydeseq2/inference.py": "922ea3fdfe2a90d1ffe1745083c51403b3089e31680c881acdc8db9eaf56ba1f",
    "pydeseq2/preprocessing.py": "d05c3dc8b1f3abb5233c6ab93820019ee736cb0c7b98e2eeb5a3a69f90f53ed8",
    "pydeseq2/utils.py": "90491819baca8d0329a27f559a107277577d16cc6ae2aae0a8a33739755cde88",
}
MAX_MATRIX_BYTES = 100_000_000
MAX_GENES = 100_000
MAX_SAMPLES = 4096


def check_count_model() -> dict:
    """Fail closed on a missing, different, or unimportable pinned dependency."""
    try:
        version = importlib.metadata.version("pydeseq2")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ValueError("Required count-model dependency PyDESeq2 0.5.4 is not installed") from exc
    if version != MODEL_VERSION:
        raise ValueError(f"Required count-model dependency is PyDESeq2 {MODEL_VERSION}, found {version}")
    distribution = importlib.metadata.distribution("pydeseq2")
    installed = {}
    for name, expected in MODEL_SOURCE_SHA256.items():
        source = Path(distribution.locate_file(name))
        try:
            actual = hashlib.sha256(source.read_bytes()).hexdigest()
        except OSError as exc:
            raise ValueError("Count-model source does not match the immutable wheel pin: " + name) from exc
        if actual != expected:
            raise ValueError("Count-model source does not match the immutable wheel pin: " + name)
        installed[name] = actual
    try:
        for name in MODEL_SOURCE_SHA256:
            module_name = name[:-3].replace("/", ".")
            if module_name.endswith(".__init__"):
                module_name = module_name[:-9]
            module = importlib.import_module(module_name)
            if Path(module.__file__).resolve() != Path(distribution.locate_file(name)).resolve():
                raise ValueError("Imported count-model source differs from its verified wheel pin")
    except Exception as exc:
        raise ValueError("Pinned PyDESeq2 0.5.4 count-model dependency cannot be imported") from exc
    return {
        "package": "pydeseq2", "version": version, "upstream_commit": MODEL_COMMIT,
        "expected_wheel_sha256": MODEL_WHEEL_SHA256,
        "admission_verification": "exact_version_and_all_package_sources_match_verified_wheel",
        "installed_source_sha256": installed,
        "dependency_versions": {name: importlib.metadata.version(name)
                                for name in ("numpy", "scipy", "pandas", "anndata", "formulaic")},
    }


def _absolute_path(value, *, exists=True) -> Path:
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path:
        raise ValueError("Count-model artifacts require canonical absolute local paths")
    if exists and not path.is_file():
        raise ValueError("Count-model input must be a regular local file")
    return path


def _count_bytes(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_MATRIX_BYTES:
            raise ValueError("Count matrix exceeds the 100 MB regular-file budget")
        body = handle.read(MAX_MATRIX_BYTES + 1)
    if len(body) > MAX_MATRIX_BYTES:
        raise ValueError("Count matrix exceeds the 100 MB byte budget")
    return body


def _parse_count_matrix(body: bytes, sample_ids: list[str]) -> pd.DataFrame:
    if (not isinstance(sample_ids, list) or not 2 <= len(sample_ids) <= MAX_SAMPLES
            or any(not isinstance(item, str) or not item for item in sample_ids)
            or len(set(sample_ids)) != len(sample_ids)):
        raise ValueError("Count-model samples must have distinct explicit identities")
    try:
        reader = csv.reader(io.StringIO(body.decode("utf-8")), delimiter="\t", strict=True)
        header = next(reader)
    except (UnicodeDecodeError, csv.Error, StopIteration) as exc:
        raise ValueError("Expected a UTF-8 gene-by-sample count TSV") from exc
    if (not header or header[0] != "gene_id" or len(header) != len(sample_ids) + 1
            or len(set(header[1:])) != len(sample_ids) or set(header[1:]) != set(sample_ids)):
        raise ValueError("Count matrix sample identities must exactly match metadata")
    column_indices = {identity: index + 1 for index, identity in enumerate(header[1:])}
    indices = [column_indices[identity] for identity in sample_ids]
    ids, values, seen = [], [], set()
    try:
        for row in reader:
            if len(row) != len(header) or not row[0].strip() or row[0] in seen:
                raise ValueError("Count matrix requires complete rows and distinct nonempty gene identities")
            if len(ids) >= MAX_GENES or (len(ids) + 1) * len(sample_ids) * 8 > MAX_MATRIX_BYTES:
                raise ValueError("Full count family exceeds the 100000-gene or 100 MB decoded budget; no subsetting allowed")
            selected = []
            for index in indices:
                token = row[index]
                if not re.fullmatch(r"[0-9]{1,16}", token):
                    raise ValueError("Direct counts must be finite nonnegative integer tokens; estimated counts cannot be rounded")
                count = int(token)
                if count > 2**53 - 1:
                    raise ValueError("Integer count exceeds the exact numerical representation budget")
                selected.append(count)
            ids.append(row[0]); seen.add(row[0]); values.append(selected)
    except csv.Error as exc:
        raise ValueError("Malformed count TSV") from exc
    if not ids:
        raise ValueError("The complete emitted gene family cannot be empty")
    return pd.DataFrame(np.asarray(values, dtype=np.int64).T, index=sample_ids, columns=ids)


def read_count_matrix(counts_path, sample_ids: list[str]) -> pd.DataFrame:
    """Read the complete bounded gene family into a sample-by-gene count frame."""
    return _parse_count_matrix(_count_bytes(_absolute_path(counts_path)), sample_ids)


def _checked_design(design: dict) -> dict:
    from .fastq_contract import validate_design

    if not isinstance(design, dict):
        raise ValueError("Count fit requires the retained audited design")
    try:
        expected = validate_design(design["metadata_rows"], formula=design["formula"],
            contrast=design["contrast"], biological_unit=design["biological_unit"],
            batch_columns=design.get("batch_columns", [term for term in design["terms"]
                                                       if term != design["contrast"]["factor"]]),
            analysis_scope=design.get("analysis_scope", "bulk"),
            objective=design.get("objective", "differential_expression"),
            blocking=design.get("blocking"), biological_unit_type=design.get("biological_unit_type"))
        for name in ("sample_ids", "formula", "contrast", "terms", "design_columns", "design_matrix",
                     "contrast_vector", "rank", "residual_df", "biological_unit", "unit_count"):
            if design[name] != expected[name]:
                raise ValueError("Retained count-model design differs from recomputed metadata: " + name)
    except (KeyError, TypeError) as exc:
        raise ValueError("Count fit requires a complete retained audited design") from exc
    if expected.get("blocking"):
        for name in ("blocking", "biological_unit_type", "core_biological_unit_type", "biological_unit_type_mapping", "modeled_metadata_rows", "observation_memberships", "analysis_observation_count", "count_model_route"):
            if design.get(name) != expected[name]:
                raise ValueError("Retained paired design differs from metadata: " + name)
    return expected


def _finite(value):
    value = float(value)
    return value if math.isfinite(value) else None


def _model_rows(dds, statistics, counts):
    from scipy.stats import false_discovery_control

    result = statistics.results_df
    if list(result.index) != list(counts.columns):
        raise ValueError("Pinned model changed the emitted gene-family identity or order")
    rows = []
    for gene in counts.columns:
        fitted = result.loc[gene]
        row = {"gene_id": gene, "baseMean": _finite(fitted["baseMean"]),
               "log2FoldChange": _finite(fitted["log2FoldChange"]), "lfcSE": _finite(fitted["lfcSE"]),
               "stat": _finite(fitted["stat"]), "pvalue": _finite(fitted["pvalue"]), "padj": None,
               "dispersion": _finite(dds.var.loc[gene, "dispersions"]), "status": "ok", "missing_reason": ""}
        convergence = dds.var.loc[gene, "_LFC_converged"]
        row["lfc_converged"] = False if pd.isna(convergence) else bool(convergence)
        reason = None
        if not bool(counts[gene].any()):
            reason, row["status"] = "all_zero_counts", "not_estimable"
        elif (not row["lfc_converged"] or row["dispersion"] is None or row["dispersion"] <= 0
              or row["log2FoldChange"] is None or row["lfcSE"] is None or row["lfcSE"] <= 0):
            reason, row["status"] = "count_model_did_not_estimate_valid_coefficients", "fit_failed"
        elif row["pvalue"] is None:
            cook_flag = dds.var.loc[gene, "_pvalue_cooks_outlier"] if "_pvalue_cooks_outlier" in dds.var else False
            cooked = False if pd.isna(cook_flag) else bool(cook_flag)
            reason = "cooks_outlier_suppressed_test" if cooked else "count_model_test_not_estimable"
            row["status"] = "outlier" if cooked else "not_estimable"
        elif not 0 <= row["pvalue"] <= 1 or row["stat"] is None:
            reason, row["status"] = "count_model_invalid_test_statistic", "fit_failed"
        if reason is not None:
            row["missing_reason"] = reason
            for key in ("log2FoldChange", "lfcSE", "stat", "pvalue", "padj"):
                row[key] = None
        rows.append(row)
    estimated = [row for row in rows if row["pvalue"] is not None]
    if estimated:
        # This is the pinned package's native finite-p BH policy, applied after
        # rejecting coefficient nonconvergence that its result table may expose.
        adjusted = false_discovery_control([row["pvalue"] for row in estimated], method="bh")
        for row, q in zip(estimated, adjusted, strict=True):
            row["padj"] = max(row["pvalue"], float(q))
    return rows


def _artifact(path: Path, body: bytes | None = None) -> dict:
    if body is None:
        body = path.read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body)}


def fit_count_matrix(counts_path, design: dict, output_dir, *, alpha=0.05, analysis_policy=None) -> dict:
    """Fit all emitted direct counts under the frozen, independently rechecked design."""
    design = _checked_design(design)
    analysis_policy = validate_analysis_policy(analysis_policy, paired=bool(design.get("blocking")))
    if design.get("blocking"):
        from .fastq_paired_model import fit_paired_counts
        return fit_paired_counts(counts_path, design, output_dir, alpha=alpha, analysis_policy=analysis_policy)
    identity = check_count_model()
    if isinstance(alpha, bool) or not isinstance(alpha, (float, int)) or not 0 < alpha < 1:
        raise ValueError("Declare a finite BH alpha strictly between zero and one")
    output = _absolute_path(output_dir, exists=False)
    if output.exists() or not output.parent.is_dir():
        raise ValueError("Count-model output must be a fresh directory under an existing absolute parent")
    path = _absolute_path(counts_path)
    count_bytes = _count_bytes(path)
    counts = _parse_count_matrix(count_bytes, design["sample_ids"])
    if not np.any(np.all(counts.to_numpy() > 0, axis=0)):
        raise ValueError("RLE ratio offsets require at least one gene positive in every sample; iterative fallback is not admitted")
    if np.any(np.all(counts.to_numpy() == 0, axis=1)):
        raise ValueError("A zero-depth library cannot supply a count-model offset")
    from pydeseq2.dds import DeseqDataSet
    from pydeseq2.ds import DeseqStats
    from pydeseq2.default_inference import DefaultInference

    x = pd.DataFrame(design["design_matrix"], index=design["sample_ids"], columns=design["design_columns"])
    metadata = pd.DataFrame(design["metadata_rows"]).set_index("sample_id").loc[design["sample_ids"]]
    inference = DefaultInference(n_cpus=1)
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        dds = DeseqDataSet(counts=counts, metadata=metadata, design=x,
            fit_type="parametric", size_factors_fit_type="ratio", refit_cooks=False,
            inference=inference, n_cpus=1, quiet=True)
        dds.deseq2()
        statistics = DeseqStats(dds, contrast=np.asarray(design["contrast_vector"], dtype=float),
            alpha=float(alpha), cooks_filter=True, independent_filter=False,
            inference=inference, n_cpus=1, quiet=True)
        statistics.summary()
    if not np.array_equal(dds.X, counts.to_numpy()):
        raise ValueError("Pinned count model replaced input counts despite the frozen no-replacement policy")
    size_factors = np.asarray(dds.obs["size_factors"], dtype=float)
    if not np.all(np.isfinite(size_factors)) or np.any(size_factors <= 0):
        raise ValueError("Pinned count model returned invalid library-size offsets")
    rows = _model_rows(dds, statistics, counts)
    optional_evidence = {}
    if analysis_policy and "independent_filter" in analysis_policy:
        optional_evidence["independent_filter"] = apply_independent_filter(rows, analysis_policy["independent_filter"])
    if analysis_policy and "lfc_shrinkage" in analysis_policy:
        with warnings.catch_warnings(record=True) as shrink_warnings:
            warnings.simplefilter("always")
            optional_evidence["lfc_shrinkage"] = add_shrunken_effects(rows, statistics, design)
        recorded.extend(shrink_warnings)
    estimated_count = sum(row["pvalue"] is not None for row in rows)
    if not estimated_count:
        raise ValueError("Pinned count model estimated no valid gene tests; no successful DE table is published")
    policy = {
        "alpha": float(alpha), "adjustment": "benjamini_hochberg", "independent_filtering": False,
        "failed_hypothesis_policy": "exclude_failed_hypotheses",
        "adjustment_implementation": "scipy.stats.false_discovery_control(method='bh')",
        "adjustment_scope": "finite p-values after model failure checks; every emitted gene retained as a result row",
        "cooks_filter": True, "refit_cooks": False, "count_replacement": False,
        "lfc_shrinkage": False, "lfc_nonconvergence": "null_statistics",
        "dispersion_grid_fallback": "pinned_native_policy_retained_and_recorded",
        "requested_dispersion_trend": "parametric", "normalization": "RLE_ratio",
        "test": "two_sided_Wald", "n_cpus": 1,
    }
    if analysis_policy is not None:
        policy["analysis_policy"] = analysis_policy
        policy["optional_policy_evidence"] = optional_evidence
        if "independent_filter" in optional_evidence:
            policy["independent_filtering"] = True
            policy["filtered_adjustment_scope"] = "padj_filtered only: finite p-values eligible under frozen base-mean threshold; original padj unchanged"
        if "lfc_shrinkage" in optional_evidence:
            policy["lfc_shrinkage"] = True
    diagnostics = {}
    for flag in ("_genewise_converged", "_MAP_converged", "_LFC_converged", "_outlier_genes", "_pvalue_cooks_outlier"):
        if flag in dds.var:
            diagnostics[flag] = {"true": int((dds.var[flag] == True).sum()),
                                 "false": int((dds.var[flag] == False).sum()),
                                 "missing": int(dds.var[flag].isna().sum())}
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n")
    writer.writeheader(); writer.writerows(rows)
    table_bytes = buffer.getvalue().encode("utf-8")
    table = _artifact(output / "differential_expression.tsv", table_bytes)
    count_artifact = _artifact(path, count_bytes)
    provenance = {
        "format": "devbench.fastq-count-model/1", "status": "completed", "model": identity,
        "input_representation": "direct_integer_STAR_GeneCounts", "design": design,
        "count_matrix": count_artifact, "differential_expression": table,
        "family": {"emitted_gene_count": len(rows), "result_row_count": len(rows),
                   "tested_gene_count": estimated_count, "failed_or_suppressed_gene_count": len(rows) - estimated_count,
                   "complete": True, "gene_ids_sha256": hashlib.sha256(json.dumps(list(counts.columns),
                       separators=(",", ":")).encode()).hexdigest()},
        "normalization": {"method": "RLE_ratio", "sample_ids": design["sample_ids"],
                          "size_factors": size_factors.tolist()},
        "actual_dispersion_trend": dds.uns.get("disp_function_type"),
        "diagnostics": diagnostics,
        "warnings": [{"category": item.category.__name__, "message": str(item.message)[:2000]}
                     for item in recorded[:100]], "warning_count": len(recorded),
        "numerical_policy": policy,
        "evidence": {"execution_success": True, "scientific_correctness": None,
                     "numerical_replay": "not_run", "biological_agreement": "not_run",
                     "model_sessions": 0, "clinical_release_authorized": False},
    }
    if analysis_policy is not None:
        provenance["family"]["bh_family_gene_ids"] = [r["gene_id"] for r in rows if r["padj"] is not None]
        provenance["family"]["bh_family_count"] = sum(r["padj"] is not None for r in rows)
        if "independent_filter" in optional_evidence:
            provenance["family"]["filtered_bh_family_gene_ids"] = optional_evidence["independent_filter"]["bh_family_gene_ids"]
            provenance["family"]["filtered_bh_family_count"] = optional_evidence["independent_filter"]["bh_family_count"]
    provenance_bytes = (json.dumps(provenance, indent=2, allow_nan=False) + "\n").encode()
    # Staging prevents an exception during fitting or serialization from leaving
    # a successful-looking table at the declared output path.
    with tempfile.TemporaryDirectory(prefix=".count-model-", dir=output.parent) as temporary:
        staged = Path(temporary) / "artifacts"
        staged.mkdir()
        (staged / "differential_expression.tsv").write_bytes(table_bytes)
        (staged / "provenance.json").write_bytes(provenance_bytes)
        if output.exists():
            raise ValueError("Count-model output was created concurrently")
        staged.rename(output)
    return {"format": provenance["format"], "status": "completed", "formula": design["formula"],
            "contrast": design["contrast"], "model": identity, "count_matrix": count_artifact,
            "differential_expression": table,
            "provenance": _artifact(output / "provenance.json", provenance_bytes),
            "family": provenance["family"], "numerical_policy": policy}
