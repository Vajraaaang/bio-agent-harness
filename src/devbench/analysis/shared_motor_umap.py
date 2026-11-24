"""Compute one descriptive UMAP from three annotated developmental count layers."""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import scipy
import sklearn
from scipy import sparse
from sklearn.decomposition import PCA

from devbench.core.input_files import _open_file, _signature
from devbench.core.population_data import _annotation, _node, _sparse_components

from .checkpoints import atomic_json

STAGES = ("e09", "e10", "e11")
STATES = ("Neuroblast", "Neuron")
_LABELS = {f"{state}: Motor neuron": state for state in STATES}
_MARKERS = {
    "Early neuronal": ("Neurod2", "Ebf2", "Dcx"),
    "Neuronal": ("Stmn2", "Snap25", "Syt1"),
    "Cholinergic": ("Chat", "Slc18a3", "Slc5a7"),
    "Motor identity": ("Isl1", "Phox2b", "Mnx1"),
    "Cell cycle": ("Mki67", "Top2a"),
}
_GENES = tuple(gene for block in _MARKERS.values() for gene in block)
_COLORS = {"Neuroblast": "#147f8e", "Neuron": "#ac3f2d"}
_ARTIFACTS = {
    "umap": "shared_motor_umap.png",
    "markers": "motor_state_markers.png",
    "coordinates": "coordinates.csv",
    "composition": "composition.csv",
    "parameters": "parameters.json",
}
_SEED = 42
_MAX_AXIS = 100_000
_MAX_MOTOR_CELLS = 100_000
_MAX_CANDIDATES = 4096
_MAX_SELECTED_NNZ = 32_000_000
_METADATA_BUDGET = 32 * 1024**2
_READ_BLOCK_NNZ = 2_000_000
_LIMITS = (
    "Composition describes the captured cells with supplied motor-neuron labels.",
    "The fit applies no batch correction and does not test abundance changes.",
    "Independent embryo identities and lineage transitions are unverified.",
)


@dataclass(frozen=True)
class _Stage:
    path: Path
    stage: str
    features: tuple[str, ...]
    variable: frozenset[str]
    states: np.ndarray
    signature: tuple

    @property
    def selected(self):
        return self.states != ""

    def composition(self):
        neuroblasts = int(np.count_nonzero(self.states == "Neuroblast"))
        neurons = int(np.count_nonzero(self.states == "Neuron"))
        total = neuroblasts + neurons
        return {
            "stage": self.stage,
            "all_cells": len(self.states),
            "neuroblast": neuroblasts,
            "neuron": neurons,
            "motor_cells": total,
            "neuron_share_of_motor": neurons / total if total else None,
            "motor_share_of_all": total / len(self.states),
        }


@contextmanager
def _open_h5ad(path):
    if (
        not path.is_absolute()
        or path.resolve() != path
        or path.suffix.lower() != ".h5ad"
    ):
        raise ValueError("Use a canonical absolute H5AD input for every stage")
    info = path.parent.stat()
    with _open_file(path.parent, (path.name,), (info.st_dev, info.st_ino)) as fd:
        signature = _signature(os.fstat(fd))
        with os.fdopen(os.dup(fd), "rb") as stream, h5py.File(stream, "r") as handle:
            yield handle, signature
        if signature != _signature(os.fstat(fd)) or signature != _signature(
            path.stat()
        ):
            raise ValueError("An input file changed during shared UMAP analysis")


def _count_layer(handle, name):
    layer = _node(_node(handle, "layers"), name)
    if (
        not isinstance(layer, h5py.Group)
        or layer.attrs.get("encoding-type") != "csr_matrix"
    ):
        raise ValueError("The declared H5AD count layer must use CSR storage")
    shape = np.asarray(layer.attrs.get("shape", ()))
    if shape.shape != (2,) or shape.dtype.kind not in "iu":
        raise ValueError("Invalid CSR count-layer shape")
    if np.any(shape < 1) or np.any(shape > _MAX_AXIS):
        raise ValueError("The CSR count layer exceeds the shared UMAP axis bounds")
    return layer, (int(shape[0]), int(shape[1]))


def _variable_flags(group, expected):
    values = _node(group, "highly_variable")
    if isinstance(values, h5py.Group):
        codes = _node(values, "codes")
        categories = _node(values, "categories")
        if (
            not isinstance(codes, h5py.Dataset)
            or codes.shape != (expected,)
            or codes.dtype.kind not in "iu"
            or not isinstance(categories, h5py.Dataset)
            or categories.shape not in {(1,), (2,), (3,)}
        ):
            raise ValueError("Invalid highly variable feature flag encoding")
        indices = codes[:]
        if (indices < -1).any() or (indices >= len(categories)).any():
            raise ValueError(
                "Highly variable feature flag codes exceed their categories"
            )
        # AnnData's missing code and the stored "nan" label both leave the
        # feature unselected in this stage. Only an explicit true flag counts.
        flags = _flags(categories, len(categories))
        present = indices >= 0
        selected = np.zeros(expected, dtype=bool)
        selected[present] = flags[indices[present]]
        return selected
    return _flags(values, expected)


def _flags(values, expected):
    if values.shape != (expected,):
        raise ValueError("Highly variable feature flags must match the feature axis")
    if values.dtype.kind == "b":
        return values[:]
    raw, _ = _annotation(values, expected, _METADATA_BUDGET)
    text = np.char.lower(np.asarray(raw, dtype=str))
    if not np.isin(text, ("true", "false", "nan")).all():
        raise ValueError("Highly variable feature flags must be booleans or undefined")
    return text == "true"


def _inventory(path, stage, stage_column, label_column, counts_layer):
    with _open_h5ad(path) as (handle, signature):
        _, (n_cells, n_features) = _count_layer(handle, counts_layer)
        obs = _node(handle, "obs")
        var = _node(handle, "var")
        observed_stages, _ = _annotation(
            _node(obs, stage_column), n_cells, _METADATA_BUDGET
        )
        if set(observed_stages) not in ({stage}, {f"e{int(stage[1:])}"}):
            raise ValueError(
                f"Observed stage metadata do not match the {stage} input slot"
            )
        labels, _ = _annotation(_node(obs, label_column), n_cells, _METADATA_BUDGET)
        states = np.array([_LABELS.get(label, "") for label in labels])
        ids, _ = _annotation(_node(obs, obs.attrs["_index"]), n_cells, _METADATA_BUDGET)
        names, _ = _annotation(
            _node(var, var.attrs["_index"]), n_features, _METADATA_BUDGET
        )
        if any(not isinstance(value, str) for value in ids) or len(set(ids)) != n_cells:
            raise ValueError(
                "Observation identifiers must be nonmissing and unique per input"
            )
        if (
            any(not isinstance(value, str) for value in names)
            or len(set(names)) != n_features
        ):
            raise ValueError("Feature symbols must be nonmissing and unique per input")
        selected = _variable_flags(var, n_features)
        return _Stage(
            path,
            stage,
            tuple(names),
            frozenset(np.asarray(names)[selected]),
            states,
            signature,
        )


def _row_blocks(pointers):
    n_rows = len(pointers) - 1
    start = 0
    while start < n_rows:
        end = min(start + 256, n_rows)
        max_offset = pointers[start] + _READ_BLOCK_NNZ
        if pointers[end] > max_offset:
            end = int(np.searchsorted(pointers, max_offset, side="right") - 1)
        if end <= start:
            raise ValueError("A CSR count row exceeds the supported sparse read block")
        yield start, end
        start = end


def _normalized_block(observed, projected_indices):
    values = observed.data
    if (
        not np.isfinite(values).all()
        or (values < 0).any()
        or (values.dtype.kind == "f" and (values != np.floor(values)).any())
    ):
        raise ValueError("Motor-neuron rows require finite, nonnegative integer counts")
    totals = np.asarray(observed.sum(axis=1, dtype=np.float64)).ravel()
    if not np.isfinite(totals).all() or (totals <= 0).any():
        raise ValueError("Selected motor-neuron cells must have positive library sizes")
    block = observed[:, projected_indices].astype(np.float32)
    block.sum_duplicates()
    block.eliminate_zeros()
    block.data *= np.repeat((1e4 / totals).astype(np.float32), np.diff(block.indptr))
    return block


def _counts(stage, counts_layer, columns, blocks, used_entries):
    lookup = {gene: index for index, gene in enumerate(stage.features)}
    projected = [lookup[gene] for gene in columns]
    with _open_h5ad(stage.path) as (handle, signature):
        if signature != stage.signature:
            raise ValueError("The selected input changed after metadata inspection")
        layer, shape = _count_layer(handle, counts_layer)
        data, indices, offsets = _sparse_components(layer, shape, "csr", 1024**2)
        if data.dtype.kind not in "iuf":
            raise ValueError("The declared count layer must have numeric values")
        pointers = offsets[:].astype(np.int64, copy=False)
        selected = stage.selected
        for start, end in _row_blocks(pointers):
            retain = selected[start:end]
            if not retain.any():
                continue
            left, right = int(pointers[start]), int(pointers[end])
            enc = (
                data[left:right],
                indices[left:right],
                pointers[start : end + 1] - left,
            )
            observed = sparse.csr_matrix(enc, shape=(end - start, shape[1]))[retain]
            block = _normalized_block(observed, projected)
            used_entries += block.nnz
            if used_entries > _MAX_SELECTED_NNZ:
                raise ValueError(
                    "Selected motor-neuron expression exceeds the sparse fit budget"
                )
            blocks.append(block)
    return used_entries


def _usable_feature(gene):
    return (
        gene not in _GENES
        and not gene.lower().startswith(("mt-", "rpl", "rps"))
        and gene not in {"Xist", "Tsix", "Ddx3y", "Eif2s3y", "Kdm5d", "Uty"}
    )


def _joint_embedding(matrix, candidates):
    import umap

    keep = np.asarray(matrix.getnnz(axis=0) / matrix.shape[0] >= 0.01)
    matrix = matrix[:, keep]
    dims = min(30, min(matrix.shape) - 1)
    if dims < 2 or matrix.shape[0] < 4:
        raise ValueError(
            "Joint UMAP requires four motor-neuron cells and three detected HVGs"
        )
    matrix.data = np.log1p(matrix.data)
    average = np.asarray(matrix.mean(axis=0)).ravel()
    variance = np.asarray(matrix.power(2).mean(axis=0)).ravel() - average**2
    scale = np.maximum(np.sqrt(np.maximum(variance, 0)), 0.1)
    matrix = matrix.multiply(1 / scale).tocsr()
    pca = PCA(n_components=dims, svd_solver="arpack", random_state=_SEED)
    pcs = pca.fit_transform(matrix).astype(np.float32)
    if (
        not np.isfinite(pcs).all()
        or not np.isfinite(pca.explained_variance_ratio_).all()
    ):
        raise ValueError("The selected features do not support a finite PCA")
    if pca.explained_variance_ratio_[1] < 1e-8:
        raise ValueError("The selected features produce a collapsed joint PCA")
    neighbors = min(30, len(pcs) - 1)
    coords = umap.UMAP(
        n_neighbors=neighbors,
        min_dist=0.3,
        metric="euclidean",
        random_state=_SEED,
        n_jobs=1,
        low_memory=True,
    ).fit_transform(pcs)
    if coords.shape != (len(pcs), 2) or not np.isfinite(coords).all():
        raise ValueError("UMAP did not return a finite joint embedding")
    if (np.ptp(coords, axis=0) < 1e-6).any():
        raise ValueError("UMAP returned a collapsed joint embedding")
    return coords, {
        "features": np.asarray(candidates)[keep].tolist(),
        "pca_dimensions": dims,
        "pca_explained_variance_fraction": float(pca.explained_variance_ratio_.sum()),
        "umap": {
            "n_neighbors": neighbors,
            "min_dist": 0.3,
            "metric": "euclidean",
            "seed": _SEED,
        },
        "software": {
            "umap": umap.__version__,
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "scikit_learn": sklearn.__version__,
            "h5py": h5py.__version__,
        },
    }


def _new_figure(width, height):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(width, height), facecolor="#f8fafc")
    FigureCanvasAgg(figure)
    return figure


def _map_figure(motor, summary, path):
    from matplotlib.lines import Line2D

    fig = _new_figure(15, 6.2)
    axes = fig.subplots(1, 3, sharex=True, sharey=True)
    fig.text(
        0.05,
        0.943,
        "Motor-neuron states across e09, e10 and e11",
        fontsize=24,
        weight="bold",
        color="#152238",
    )
    fig.text(
        0.05,
        0.883,
        f"One joint UMAP  •  {len(motor):,} cells  •  supplied cell-state labels",
        fontsize=12,
        color="#475569",
    )
    for ax, row in zip(axes, summary, strict=True):
        subset = motor.stage == row["stage"]
        data = motor[subset].sample(frac=1, random_state=_SEED)
        other = motor[~subset]
        ax.scatter(
            other.umap_1, other.umap_2, s=2, c="#cbd5e1", alpha=0.12, linewidths=0
        )
        ax.scatter(
            data.umap_1,
            data.umap_2,
            s=3,
            c=data.state.map(_COLORS),
            alpha=0.59,
            linewidths=0,
        )
        ax.set_title(
            f"{row['stage']}  ·  {row['motor_cells']:,} cells",
            loc="left",
            fontsize=15,
            color="#152238",
            pad=9,
            fontweight="bold",
        )
        ax.set_xlabel(
            f"{row['neuroblast']:,} neuroblast   /   {row['neuron']:,} neuron",
            color="#475569",
            fontsize=10.5,
            labelpad=10,
        )
        ax.set(xticks=[], yticks=[])
        ax.set_aspect("equal", adjustable="box")
        for spine in ax.spines.values():
            spine.set_color("#dce3ea")
    fig.legend(
        handles=[
            Line2D(
                [],
                [],
                marker="o",
                color=color,
                linestyle="None",
                markersize=7,
                label=label,
            )
            for label, color in [*_COLORS.items(), ("Other stages", "#cbd5e1")]
        ],
        loc="lower left",
        bbox_to_anchor=(0.042, 0.069),
        ncol=3,
        frameon=False,
        fontsize=11,
    )
    fig.text(
        0.05,
        0.042,
        "Each panel uses the same coordinates and axis limits.",
        color="#475569",
        fontsize=10.5,
    )
    fig.subplots_adjust(left=0.05, right=0.97, bottom=0.18, top=0.81, wspace=0.08)
    fig.savefig(path, dpi=185)


def _marker_points(motor, profiles, genes):
    rows = []
    groups = motor.groupby(["stage", "state"], sort=True).indices
    for (stage, state), subset in groups.items():
        # Group indices refer to the same row order used to assemble the CSR matrix.
        subset_profiles = profiles[subset]
        means = np.asarray(subset_profiles.mean(axis=0)).ravel()
        fractions = subset_profiles.getnnz(axis=0) / len(subset)
        for index, gene in enumerate(genes):
            rows.append(
                (stage, state, gene, float(means[index]), float(fractions[index]))
            )
    return pd.DataFrame(rows, columns=["stage", "state", "gene", "mean", "fraction"])


def _marker_figure(table, genes, path):
    fig = _new_figure(13, 5.3)
    ax = fig.subplots()
    title = "Marker expression by stage and annotated state"
    fig.text(0.14, 0.927, title, fontsize=22, weight="bold", color="#152238")
    if table.empty:
        ax.set_axis_off()
        ax.text(
            0.5,
            0.5,
            "No panel markers are shared across the three feature axes.",
            ha="center",
            color="#475569",
        )
        fig.savefig(path, dpi=175)
        return
    x_positions = {gene: index for index, gene in enumerate(genes)}
    groups = list(dict.fromkeys(zip(table.stage, table.state, strict=True)))
    y_positions = {key: index for index, key in enumerate(groups)}
    y = [
        y_positions[(stage, state)]
        for stage, state in zip(table.stage, table.state, strict=True)
    ]
    points = ax.scatter(
        table.gene.map(x_positions),
        y,
        s=7 + 240 * table.fraction,
        c=np.log1p(table["mean"]),
        cmap="viridis",
        vmin=0,
        edgecolor="#536073",
        linewidth=0.35,
    )
    for group, markers in _MARKERS.items():
        positions = [x_positions[gene] for gene in markers if gene in x_positions]
        if positions:
            ax.text(float(np.mean(positions)), -0.95, group, ha="center", fontsize=9.5)
    ax.set(
        xlim=(-0.55, len(genes) - 0.45),
        ylim=(len(groups) - 0.45, -1.18),
        xticks=list(x_positions.values()),
        yticks=list(y_positions.values()),
    )
    ax.set_xticklabels(genes, rotation=42, ha="right", fontsize=10, fontstyle="italic")
    ax.set_yticklabels([f"{stage}  {state}" for stage, state in groups], fontsize=10.5)
    ax.tick_params(length=0)
    handles = [
        ax.scatter(
            [],
            [],
            s=7 + 240 * fraction,
            facecolor="none",
            edgecolor="#536073",
            label=f"{fraction:.0%}",
        )
        for fraction in (0.1, 0.5, 0.9)
    ]
    ax.legend(
        handles=handles,
        title="Cells with detected RNA",
        frameon=False,
        loc="upper left",
        bbox_to_anchor=(0.01, -0.25),
        ncol=3,
        fontsize=9,
    )
    fig.colorbar(points, ax=ax, shrink=0.78, pad=0.025).set_label(
        "log(1 + mean counts per 10,000)"
    )
    fig.subplots_adjust(left=0.14, right=0.96, bottom=0.29, top=0.85)
    fig.savefig(path, dpi=175)


def run_shared_motor_umap(
    sources: tuple[Path, Path, Path],
    output: Path,
    *,
    stage_column: str = "age",
    label_column: str = "celltypes",
    counts_layer: str = "counts",
) -> dict:
    """Fit one coordinate map, then facet the supplied motor-neuron cell states."""
    if len(sources) != 3 or len(set(sources)) != 3:
        raise ValueError("Supply distinct H5AD files in e09, e10, e11 order")
    if not output.is_absolute() or output.resolve() != output:
        raise ValueError("Shared UMAP output requires a canonical absolute directory")
    loaded = [
        _inventory(path, stage, stage_column, label_column, counts_layer)
        for path, stage in zip(sources, STAGES, strict=True)
    ]
    summary = [stage.composition() for stage in loaded]
    cells = sum(row["motor_cells"] for row in summary)
    if not 4 <= cells <= _MAX_MOTOR_CELLS:
        raise ValueError(
            "Selected motor-neuron cell count exceeds the bounds of this workflow"
        )
    common = set.intersection(*(set(stage.features) for stage in loaded))
    union = set().union(*(stage.variable for stage in loaded))
    candidates = sorted(gene for gene in common & union if _usable_feature(gene))
    if not 3 <= len(candidates) <= _MAX_CANDIDATES:
        raise ValueError(
            "Shared highly variable features exceed the bounds of this workflow"
        )
    genes = [gene for gene in _GENES if gene in common]
    blocks: list[sparse.csr_matrix] = []
    entries = 0
    for stage in loaded:
        entries = _counts(stage, counts_layer, candidates + genes, blocks, entries)
    matrix = sparse.vstack(blocks, format="csr")
    del blocks
    motor = pd.DataFrame(
        {
            "stage": np.repeat(STAGES, [row["motor_cells"] for row in summary]),
            "state": np.concatenate([stage.states[stage.selected] for stage in loaded]),
        }
    )
    marker_points = _marker_points(motor, matrix[:, len(candidates) :], genes)
    coords, method = _joint_embedding(matrix[:, : len(candidates)], candidates)
    motor[["umap_1", "umap_2"]] = coords
    method.update(
        {
            "stage_order": STAGES,
            "stage_column": stage_column,
            "label_column": label_column,
            "counts_layer": counts_layer,
            "population": list(_LABELS),
            "shared_feature_identity": "unique exact feature symbols",
            "common_symbols": len(common),
            "hvg_source": "union of explicit true flags among common symbols",
            "marker_genes_held_out": genes,
            "marker_genes_unavailable": [gene for gene in _GENES if gene not in common],
            "normalization": "counts per 10000 per cell, then log1p for PCA",
            "min_fraction_detected": 0.01,
            "pca_gene_sd_floor": 0.1,
            "cells": cells,
            "limitations": list(_LIMITS),
        }
    )
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    paths = {key: output / name for key, name in _ARTIFACTS.items()}
    if any(path.exists() or path.is_symlink() for path in paths.values()):
        raise ValueError("Use an empty output directory for a new shared UMAP run")
    _map_figure(motor, summary, paths["umap"])
    _marker_figure(marker_points, genes, paths["markers"])
    motor.to_csv(paths["coordinates"], index=False)
    pd.DataFrame(summary).to_csv(paths["composition"], index=False)
    atomic_json(paths["parameters"], method)
    return {
        "cells": cells,
        "features": len(method["features"]),
        "stages": summary,
        "artifacts": dict(_ARTIFACTS),
        "limitations": list(_LIMITS),
    }
