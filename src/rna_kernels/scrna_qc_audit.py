"""Bounded nuclear-fraction measurements and stratified filtering diagnostics."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
from scipy import sparse

from devbench.analysis.scrna_qc import declared_groups
from devbench.analysis.singlecell import gene_symbols, qc_feature_masks, validate_counts
from devbench.core.expression_io import open_h5ad

from .canonical import canonical_json
from .m2_contract import OUTPUTS
from .scrna_upstream_contract import MAX_TABLE_BYTES


class SolverUnavailable(RuntimeError):
    """A named solver is absent or could not fit this capture."""


def run_r(driver, scratch, *args):
    binary = shutil.which("Rscript")
    if binary is None:
        raise SolverUnavailable("rscript_unavailable")
    script = Path(__file__).resolve().parent / "r" / driver
    try:
        run = subprocess.run(
            [binary, "--vanilla", str(script), *map(str, args)],
            cwd=scratch,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1800,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise SolverUnavailable("solver_timeout") from error
    if run.returncode == 42:
        raise SolverUnavailable("package_unavailable")
    if run.returncode == 43:
        raise SolverUnavailable("soupx_autoestcont_warning_or_failure")
    if run.returncode:
        raise SolverUnavailable("solver_fit_failed")


def row_sums(matrix):
    return np.asarray(matrix.sum(axis=1, dtype=np.float64)).ravel()


def basic_metrics(source, p):
    masks = qc_feature_masks(gene_symbols(source)[0])
    parts = []
    for block in source.iter_rows():
        x = block.X
        validate_counts(x)
        if sparse.issparse(x):
            x = x.tocsr(copy=True)
            x.sum_duplicates()
            x.eliminate_zeros()
            genes = x.getnnz(axis=1)
        else:
            genes = np.count_nonzero(x, axis=1)
        total = row_sums(x)
        mito = np.full(len(total), np.nan)
        if masks["mt"].any():
            np.divide(
                100 * row_sums(x[:, masks["mt"]]), total, out=mito, where=total > 0
            )
        parts.append(
            pd.DataFrame(
                dict(
                    total_counts=total,
                    n_genes=genes,
                    pct_counts_mt=mito,
                    qc_pass=(total > 0) & (genes >= p.min_genes),
                ),
                index=block.obs_names,
            )
        )
    return pd.concat(parts), (
        [] if masks["mt"].any() else ["mitochondrial_annotation_unavailable"]
    )


def group_audit(obs, metrics, p):
    records, warnings = [], []
    for axis in ("donor", "capture", "condition", "cluster"):
        key = getattr(p, axis + "_key")
        if key not in obs:
            warnings.append(f"{axis}_stratum_unavailable")
            continue
        labels = declared_groups(obs, key, f"invalid_{axis}_stratum")
        if labels.nunique() + len(records) > 1900:
            raise ValueError("audit_strata_exceed_bound")
        start = len(records)
        for group, idx in labels.groupby(labels, sort=True).indices.items():
            rows = metrics.iloc[idx]
            calls = rows.get("predicted_doublet", pd.Series(dtype=bool)).dropna()
            records.append(
                dict(
                    axis=axis,
                    group=str(group),
                    cells=len(rows),
                    retained=int(rows.qc_pass.sum()),
                    retention_rate=float(rows.qc_pass.mean()),
                    doublets=int(calls.sum()),
                    assessed=len(calls),
                    doublet_rate=float(calls.mean()) if len(calls) else None,
                )
            )
        for rate in ("retention_rate", "doublet_rate"):
            values = [r[rate] for r in records[start:] if r[rate] is not None]
            if values and max(values) - min(values) >= p.rate_gap_warning:
                warnings.append(f"disproportionate_{rate}_across_{axis}")
    return records, warnings


def sidecar(obs, p, columns, rows, transforms):
    return dict(
        axis_labels={"x": "Stratum", "y": "Fraction"},
        transformations=transforms,
        sample_count=len(obs),
        donor_count=int(obs[p.donor_key].nunique()) if p.donor_key in obs else 0,
        columns=columns,
        rows=rows,
    )


def publish(outputs, result, metrics=None, plot=None):
    paths = {"result": outputs / OUTPUTS["result"].filename}
    result.update(evidence_level="L1", clinical_release_allowed=False)
    paths["result"].write_bytes(canonical_json(result))
    if metrics is not None:
        paths["metrics"] = outputs / OUTPUTS["metrics"].filename
        metrics.to_parquet(paths["metrics"])
    if plot is not None:
        paths["plot_sidecar"] = outputs / OUTPUTS["plot_sidecar"].filename
        paths["plot_sidecar"].write_bytes(canonical_json(plot))
    return paths


def publish_qc(outputs, obs, p, result, metrics):
    rows, warnings = group_audit(obs, metrics, p)
    result["warnings"].extend(warnings)
    result["differential_audit"] = rows
    result["rate_gap_warning"] = p.rate_gap_warning
    data = [
        [r["axis"], r["group"], name, r[name]]
        for r in rows
        for name in ("retention_rate", "doublet_rate")
        if r[name] is not None
    ]
    if not data:
        data = [["all", "all", "retention_rate", float(metrics.qc_pass.mean())]]
    plot = sidecar(
        obs,
        p,
        ["axis", "group", "measure", "fraction"],
        data,
        ["Rates use the number of assessed cells as denominator."],
    )
    return publish(outputs, result, metrics, plot)


def nuclear_fraction(intronic, exonic):
    intronic, exonic = np.asarray(intronic), np.asarray(exonic)
    if intronic.shape != exonic.shape:
        raise ValueError("nuclear_fraction_axes_differ")
    validate_counts(intronic)
    validate_counts(exonic)
    total = intronic.astype(np.float64) + exonic
    return np.divide(intronic, total, out=np.full(total.shape, np.nan), where=total > 0)


def _nuclear_counts(paths, obs, p):
    if "bam_summary" in paths:
        path = paths["bam_summary"]
        if path.stat().st_size > MAX_TABLE_BYTES:
            raise ValueError("bam_summary_exceeds_bound")
        df = pd.read_csv(path, dtype={"barcode": str}, keep_default_na=False).set_index(
            "barcode"
        )
        if not df.index.is_unique or set(df.index) != set(obs.index):
            raise ValueError("bam_summary_barcode_mismatch")
        counts = df.loc[obs.index]
        return counts.intronic, counts.exonic, "bam_summary"
    path = paths["expression"]
    if p.intronic_layer == p.exonic_layer:
        raise ValueError("intronic_and_exonic_layers_must_differ")
    layers = [p.intronic_layer, p.exonic_layer]
    with h5py.File(path, "r") as f:
        if any("layers/" + name not in f for name in layers):
            return None, None, "not_evaluable"
    totals = []
    for layer in layers:
        with open_h5ad(path, layer=layer) as source:
            blocks = []
            for block in source.iter_rows():
                validate_counts(block.X)
                blocks.append(row_sums(block.X))
            totals.append(np.concatenate(blocks))
    return *totals, "intronic_exonic_layers"


def nuclear_qc(paths, p, outputs):
    from devbench.core._files import hash_bounded_file

    with open_h5ad(paths["expression"]) as source:
        obs = source.obs.copy()
        metrics, warnings = basic_metrics(source, p)
    intronic, exonic, basis = _nuclear_counts(paths, obs, p)
    metrics["nuclear_fraction"] = (
        np.nan if intronic is None else nuclear_fraction(intronic, exonic)
    )
    metrics["droplet_class"] = "not_evaluable"
    if intronic is None:
        warnings.append("intronic_exonic_data_unavailable")
    elif p.classify_droplets and p.capture_key in obs and p.cluster_key in obs:
        captures = declared_groups(obs, p.capture_key, "invalid_capture")
        clusters = declared_groups(obs, p.cluster_key, "invalid_cluster")
        with tempfile.TemporaryDirectory(dir=outputs) as tmp:
            root = Path(tmp)
            for idx in captures.groupby(captures).indices.values():
                lane = metrics.iloc[idx]
                lane = lane.loc[
                    lane.nuclear_fraction.notna() & (lane.total_counts > 0),
                    ["nuclear_fraction", "total_counts"],
                ].copy()
                if lane.empty:
                    continue
                lane["cluster"] = clusters.loc[lane.index]
                lane.to_csv(root / "nuclear.csv", index=False)
                try:
                    run_r("scrna_dropletqc.R", root)
                except SolverUnavailable as error:
                    warnings.append("dropletqc_classification_" + str(error))
                    continue
                labels = pd.read_csv(root / "classes.csv")
                if (
                    list(labels.columns) != ["position", "class"]
                    or labels.position.tolist() != list(range(1, len(lane) + 1))
                    or not labels["class"]
                    .isin(["cell", "empty_droplet", "damaged_cell"])
                    .all()
                ):
                    raise ValueError("invalid_dropletqc_output")
                metrics.loc[lane.index, "droplet_class"] = labels["class"].to_numpy()
    if p.exclude_flagged_droplets:
        if (
            not p.classify_droplets
            or not (metrics.droplet_class != "not_evaluable").any()
        ):
            raise ValueError("dropletqc_filter_requires_classifications")
        metrics["qc_pass"] &= ~metrics.droplet_class.isin(
            ["empty_droplet", "damaged_cell"]
        )
    metrics.attrs["source_sha256"] = hash_bounded_file(
        paths["expression"], paths["expression"].stat().st_size
    )
    result = dict(
        status="completed",
        warnings=sorted(set(warnings)),
        nuclear_fraction_basis=basis,
        evaluated_nuclei=int(metrics.nuclear_fraction.count()),
        classification_scope="candidate_droplet_states",
        droplet_class_policy="exclude_flagged"
        if p.exclude_flagged_droplets
        else "flag_only",
    )
    return publish_qc(outputs, obs, p, result, metrics)
