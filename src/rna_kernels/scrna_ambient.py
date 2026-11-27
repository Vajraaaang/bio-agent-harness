"""Per-capture ADR-27 fitting, linked R2 publication and donor-level sensitivity."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from contextlib import ExitStack
from dataclasses import asdict, replace
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import anndata as ad
import h5py
import numpy as np
import pandas as pd
from scipy import sparse

from devbench.analysis.scrna_qc import declared_groups
from devbench.analysis.singlecell import validate_counts
from devbench.core._files import hash_bounded_file
from devbench.core.expression_io import open_expression, open_h5ad

from .m2_contract import OUTPUTS
from .scrna_capture_doublets import write_csr
from .scrna_droplets import route_ambient_decontamination
from .scrna_qc_audit import SolverUnavailable, publish, row_sums, run_r, sidecar
from .scrna_upstream_contract import MAX_MATRIX_BYTES


def observed_hardware():
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def _version():
    try:
        return version("cellbender")
    except PackageNotFoundError:
        return None


def _reject_corrected_input(path):
    from devbench.core.count_forensics import declares_count_transform
    from devbench.core.hdf5_custody import validate_hdf5_tree

    with h5py.File(path, "r") as f:
        validate_hdf5_tree(f)
        if "X" in f:
            attrs = f["X"].attrs
            if (
                "uns/log1p" in f
                or attrs.get("representation") == "ambient_adjusted_counts"
                or declares_count_transform(
                    attrs.get("representation"), attrs.get("transformation")
                )
            ):
                raise ValueError("ambient_input_requires_uncorrected_counts")


def _capture_alignment(filtered, raw, p):
    labels = declared_groups(filtered.obs, p.capture_key, "missing_capture_identity")
    if raw is None:
        return labels, None
    if not raw.var.index.equals(filtered.var.index):
        raise ValueError("raw_filtered_gene_axis_mismatch")
    positions = raw.obs.index.get_indexer(filtered.obs.index)
    if min(positions) < 0:
        raise ValueError("raw_filtered_barcode_mismatch")
    # Cell Ranger stores the GEM well in each barcode. Map those exact wells
    # through shared barcodes to the declared physical capture of each R1 cell.
    raw_groups = declared_groups(raw.obs, p.capture_key, "missing_raw_capture_identity")
    if getattr(raw, "input_info", None):
        lookup = pd.DataFrame(
            dict(raw=raw_groups.iloc[positions].to_numpy(), filtered=labels.to_numpy())
        ).drop_duplicates()
        if lookup.raw.duplicated().any() or lookup.filtered.duplicated().any():
            raise ValueError("ambiguous_raw_capture_mapping")
        raw_groups = raw_groups.map(dict(zip(lookup.raw, lookup.filtered, strict=True)))
    if (
        raw_groups.isna().any()
        or set(raw_groups) != set(labels)
        or not np.array_equal(raw_groups.iloc[positions], labels)
    ):
        raise ValueError("raw_filtered_capture_mismatch")
    return labels, raw_groups


def _read_result(path, shape, group=""):
    if path.stat().st_size > MAX_MATRIX_BYTES:
        raise ValueError("adjusted_matrix_exceeds_bound")
    with h5py.File(path, "r") as f:
        from devbench.core.hdf5_custody import validate_hdf5_tree

        g = f[group] if group else f
        validate_hdf5_tree(g, max_materialized_bytes=MAX_MATRIX_BYTES)
        keys = (
            ("data", "indices", "indptr", "shape") if group else ("x", "i", "p", "Dim")
        )
        val, idx, ptr, dims = [np.asarray(g[k][:]).ravel() for k in keys]
    if (
        tuple(dims) != shape[::-1]
        or idx.dtype.kind not in "iu"
        or ptr.dtype.kind not in "iu"
    ):
        raise ValueError("invalid_adjusted_sparse_arrays")
    m = sparse.csr_matrix((val, idx, ptr), shape=shape)
    m.check_format(full_check=True)
    if not m.has_canonical_format or not np.isfinite(val).all() or np.any(val < 0):
        raise ValueError("invalid_adjusted_values")
    return m


def _run_cellbender(raw, barcodes, genes, positions, root, rate, cuda):
    x = sparse.csr_matrix(raw)
    validate_counts(x)
    if x.data.max(initial=0) > 2**31 - 1:
        raise ValueError("cellbender_signed_count_range")

    def encode(vals):
        return np.array([str(v).encode("utf-8") for v in vals])

    with h5py.File(root / "raw.h5", "x") as f:
        g = f.create_group("matrix")
        for k, v in {
            "data": x.data.astype("int32"),
            "indices": x.indices,
            "indptr": x.indptr,
            "shape": x.shape[::-1],
            "barcodes": encode(barcodes),
        }.items():
            g[k] = v
        features = g.create_group("features")
        features["id"] = encode(genes)
        features["name"] = encode(genes)
        features["feature_type"] = np.repeat(b"Gene Expression", len(genes))
        features["genome"] = np.repeat(b"genome", len(genes))
    command = [
        sys.executable,
        "-m",
        "cellbender.base_cli",
        "remove-background",
        "--input",
        str(root / "raw.h5"),
        "--output",
        str(root / "out.h5"),
        "--expected-cells",
        str(len(positions)),
        "--total-droplets-included",
        str(len(positions) + max(1, (len(barcodes) - len(positions)) // 2)),
        "--learning-rate",
        str(rate),
        "--cpu-threads",
        "1",
    ]
    if cuda:
        command.append("--cuda")
    try:
        subprocess.run(
            command,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=7200,
        )
    except (subprocess.SubprocessError, OSError) as error:
        raise SolverUnavailable("cellbender_fit_failed") from error
    path = root / "out.h5"
    corrected = _read_result(path, raw.shape, "matrix")
    with h5py.File(path, "r") as f:
        if list(f["matrix/barcodes"].asstr()[:]) != list(barcodes) or list(
            f["matrix/features/id"].asstr()[:]
        ) != list(genes):
            raise ValueError("cellbender_output_axes_differ")
    return corrected[positions]


def _fit_lane(source, raw_source, raw_rows, rows, p, route, root, rate):
    filtered = sparse.csr_matrix(source.subset(rows, max_bytes=MAX_MATRIX_BYTES // 2).X)
    validate_counts(filtered)
    raw = None
    positions = np.arange(len(rows))
    if raw_source is not None:
        barcodes = raw_source.obs.index[raw_rows]
        positions = barcodes.get_indexer(source.obs.index[rows])
        if np.any(positions < 0) or len(positions) >= len(barcodes):
            raise ValueError("raw_capture_requires_background_and_filtered_barcodes")
        raw = sparse.csr_matrix(
            raw_source.subset(raw_rows, max_bytes=MAX_MATRIX_BYTES // 2).X
        )
        validate_counts(raw)
        if (raw[positions] != filtered).nnz:
            raise ValueError("raw_filtered_umi_mismatch")
    if route.ambient_method == "cellbender":
        adjusted = _run_cellbender(
            raw,
            barcodes,
            source.var.index,
            positions,
            root,
            rate,
            route.hardware_backend == "cuda",
        )
    else:
        clusters = (
            pd.factorize(source.obs.iloc[rows][p.cluster_key], sort=True)[0] + 1
            if p.cluster_key in source.obs
            else np.ones(len(rows), dtype=int)
        )
        if np.any(clusters <= 0):
            raise ValueError("missing_cluster_assignment")
        if route.ambient_method == "soupx" and len(set(clusters)) < 2:
            raise SolverUnavailable("soupx_autoestcont_insufficient_clusters")
        with h5py.File(root / "counts.h5", "w") as f:
            write_csr(f, "filtered", filtered)
            f["filtered/barcodes"] = positions
            f["clusters"] = clusters
            if raw is not None:
                write_csr(f, "raw", raw)
                f["raw/barcodes"] = np.arange(len(raw_rows))
        run_r(
            "scrna_ambient.R",
            root,
            route.ambient_method,
            p.seed,
            route.input_matrix_tier,
        )
        adjusted = _read_result(root / "adjusted.h5", filtered.shape)
    if (adjusted - filtered).max() > 1e-6:
        raise ValueError("ambient_correction_increased_counts")
    return adjusted


def _fit_all(source, raw, labels, raw_groups, p, route, root, rate):
    blocks = []
    positions = []
    used = 0
    raw_lanes = (
        raw_groups.groupby(raw_groups, sort=True).indices
        if raw_groups is not None
        else {}
    )
    for group, rows in labels.groupby(labels, sort=True).indices.items():
        with tempfile.TemporaryDirectory(dir=root) as td:
            block = _fit_lane(
                source, raw, raw_lanes.get(group), rows, p, route, Path(td), rate
            )
        used += block.data.nbytes + block.indices.nbytes + block.indptr.nbytes
        if 4 * used > MAX_MATRIX_BYTES:
            raise ValueError("adjusted_cohort_exceeds_assembly_bound")
        blocks.append(block)
        positions.extend(rows)
    return sparse.vstack(blocks, format="csr")[np.argsort(positions)]


def _adjust(source, raw, labels, raw_groups, p, route, output):
    history = []
    if route.ambient_method == "cellbender":
        for factor in (1.0, 0.5):
            try:
                adjusted = _fit_all(
                    source,
                    raw,
                    labels,
                    raw_groups,
                    p,
                    route,
                    output,
                    p.learning_rate * factor,
                )
                history.append(
                    dict(
                        method="cellbender",
                        learning_rate=p.learning_rate * factor,
                        status="completed",
                    )
                )
                return route, adjusted, history
            except SolverUnavailable as error:
                history.append(
                    dict(
                        method="cellbender",
                        learning_rate=p.learning_rate * factor,
                        status=str(error),
                    )
                )
        method = "decontx" if p.modality == "snrna_seq" else "soupx"
        route = replace(
            route,
            ambient_method=method,
            decontamination_regime="r0_cpu_soupx_decontx",
            hardware_backend="cpu",
            cellbender_deferred_reason="cellbender_retry_exhausted",
        )
    try:
        return (
            route,
            _fit_all(
                source, raw, labels, raw_groups, p, route, output, p.learning_rate
            ),
            history,
        )
    except SolverUnavailable as error:
        history.append(dict(method=route.ambient_method, status=str(error)))
        if route.ambient_method != "soupx":
            return route, None, history
    route = replace(route, ambient_method="decontx")
    try:
        return (
            route,
            _fit_all(
                source, raw, labels, raw_groups, p, route, output, p.learning_rate
            ),
            history,
        )
    except SolverUnavailable as error:
        history.append(dict(method=route.ambient_method, status=str(error)))
        return route, None, history


def _sensitivity(source, adjusted, p, outputs, admitted):
    """Compare matched donor pseudobulk log-CPM contrasts on the two sibling layers."""
    from .scrna_primary_pseudobulk import pseudobulks, raw_primary

    obs = source.obs
    raw_totals = np.concatenate([row_sums(block.X) for block in source.iter_rows()])
    burdens = np.divide(
        raw_totals - row_sums(adjusted),
        raw_totals,
        out=np.full(len(obs), np.nan),
        where=raw_totals > 0,
    )
    burden = pd.DataFrame({"fraction": burdens}, index=obs.index)
    if p.condition_key not in obs or p.donor_key not in obs:
        return (
            burden,
            [],
            {"status": "not_evaluable", "reason": "missing_donor_or_condition"},
        )
    groups = declared_groups(obs, p.condition_key, "invalid_condition")
    donors = declared_groups(obs, p.donor_key, "invalid_donor")
    by_condition = (
        burden.assign(condition=groups, donor=donors)
        .groupby(["condition", "donor"])
        .fraction.mean()
        .groupby("condition")
        .mean()
    )
    condition_rows = [
        [str(k), float(v)] for k, v in by_condition.items() if np.isfinite(v)
    ]
    if len(by_condition) != 2:
        return (
            burden,
            condition_rows,
            {"status": "not_evaluable", "reason": "two_condition_contrast_required"},
        )
    den, num = sorted(by_condition.index)
    units, raw_counts, corrected = pseudobulks(source, adjusted, donors, groups)
    if np.any(raw_counts.sum(axis=1) <= 0):
        return (
            burden,
            condition_rows,
            {"status": "not_evaluable", "reason": "empty_raw_donor_library"},
        )
    captures = declared_groups(obs, p.capture_key, "missing_capture_identity")
    capture_confounded = (
        captures.nunique() > 1
        and groups.groupby(captures).nunique().max() == 1
        and donors.groupby(captures).nunique().max() > 1
    )
    estimates, primary = raw_primary(
        raw_counts,
        units,
        source.var.index,
        den,
        num,
        outputs,
        admitted,
        capture_confounded,
    )
    condition = np.array([group for _, group in units])
    if min(sum(condition == num), sum(condition == den)) < 2 or np.any(
        corrected.sum(axis=1) <= 0
    ):
        return (
            pd.DataFrame(dict(feature=source.var.index, **estimates)),
            condition_rows,
            {
                "status": "not_evaluable",
                "reason": "insufficient_nonempty_donor_profiles",
                "r1_primary": primary,
            },
        )
    effects = []
    for counts in (raw_counts, corrected):
        normalized = np.log2(1 + 1e6 * counts / counts.sum(axis=1, keepdims=True))
        effects.append(
            normalized[condition == num].mean(axis=0)
            - normalized[condition == den].mean(axis=0)
        )
    raw_delta, adjusted_delta = effects
    table = pd.DataFrame(
        dict(
            feature=source.var.index,
            raw_log2_cpm_difference=raw_delta,
            adjusted_log2_cpm_difference=adjusted_delta,
            **estimates,
        )
    )
    return (
        table,
        condition_rows,
        dict(
            status="completed",
            purpose="E3",
            numerator=num,
            denominator=den,
            method="difference_in_mean_donor_log2_cpm_plus_one",
            genes=len(table),
            inference="descriptive_effect_sensitivity_only",
            r1_primary=primary,
        ),
    )


def ambient(paths, p, outputs, *, primary_count_admitted=False):
    tier = "R0" if "raw_droplets" in paths else "R1"
    if tier == "R1" and p.requested_method in {"cellbender", "soupx"}:
        return publish(
            outputs, dict(status="refused", reason="missing_raw_droplet_matrix")
        )
    for path in paths.values():
        _reject_corrected_input(path)
    with ExitStack() as stack:
        source = stack.enter_context(open_h5ad(paths["expression"]))
        raw = (
            stack.enter_context(
                open_expression(paths["raw_droplets"], batch_key=p.capture_key)
            )
            if tier == "R0"
            else None
        )
        labels, raw_groups = _capture_alignment(source, raw, p)
        route = route_ambient_decontamination(
            input_matrix_tier=tier,
            hardware_backend=observed_hardware(),
            modality=p.modality,
            raw_droplets=raw.shape[0] if raw else 0,
            captures=labels.nunique(),
            allow_cellbender_cpu=p.allow_cellbender_cpu,
            cellbender_version=_version(),
            requested_method=p.requested_method,
        )
        route, adjusted, history = _adjust(
            source, raw, labels, raw_groups, p, route, outputs
        )
        receipt = {
            **asdict(route),
            "primary_de_representation": "count_1_raw_counts",
            "representation_class": "ambient_adjusted_counts",
            "epistemic_purpose": "E3",
            "cellbender_attempts_and_fallbacks": history,
            "count_1_eligible": False,
            "r1_sha256": hash_bounded_file(
                paths["expression"], paths["expression"].stat().st_size
            ),
        }
        if raw:
            receipt["r0_sha256"] = hash_bounded_file(
                paths["raw_droplets"], paths["raw_droplets"].stat().st_size
            )
        if adjusted is None:
            return publish(
                outputs,
                {
                    **receipt,
                    "status": "runtime_unavailable",
                    "reason": history[-1]["status"],
                },
            )
        metrics, conditions, sensitivity = _sensitivity(
            source, adjusted, p, outputs, primary_count_admitted
        )
        shift = (
            len(conditions) > 1
            and max(v for _, v in conditions) - min(v for _, v in conditions)
            >= p.rate_gap_warning
        )
        receipt.update(
            status="completed",
            r1_primary=sensitivity.pop(
                "r1_primary",
                {"status": "not_evaluable", "reason": sensitivity.get("reason")},
            ),
            sensitivity=sensitivity,
            ambient_burden_by_condition=conditions,
            condition_ambient_shift=shift,
            sensitivity_required=bool(
                shift
                or p.modality == "snrna_seq"
                and p.tissue in {"brain", "solid_tissue"}
            ),
        )
        path = outputs / OUTPUTS["adjusted"].filename
        data = ad.AnnData(X=adjusted, obs=source.obs.copy(), var=source.var.copy())
        data.write_h5ad(path)
        with h5py.File(path, "r+") as f:
            f["X"].attrs["representation"] = "ambient_adjusted_counts"
            f["X"].attrs["transformation"] = "corrected_expression"
            if "r0_sha256" in receipt:
                f.attrs["r0_sha256"] = receipt["r0_sha256"]
            for key, v in {**asdict(route), "r1_sha256": receipt["r1_sha256"]}.items():
                f.attrs[key] = v if v is not None else ""
        plot = (
            sidecar(
                source.obs,
                p,
                ["condition", "fraction"],
                conditions,
                ["Cell means within donors, then equal weights across donors."],
            )
            if conditions
            else None
        )
        provenance = {
            **asdict(route),
            "r1_sha256": receipt["r1_sha256"],
            "r0_sha256": receipt.get("r0_sha256"),
        }
        metrics.attrs["ambient_provenance"] = provenance
        if plot is not None:
            plot["ambient_provenance"] = provenance
        return {**publish(outputs, receipt, metrics, plot), "adjusted": path}
