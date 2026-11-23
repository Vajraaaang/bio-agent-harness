"""Render bounded matrix diagnostics and retain their exact numeric inputs."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np

from devbench.analysis.scientific_plot_verification import diagnostic_plot_state
from devbench.core._hashing import contract_json_bytes
from devbench.core.protected_partitions import PartitionVault

_ARTIFACT_DIR = "matrix-audit-artifacts"
_MAX_BYTES = 8 * 1024**2
ARTIFACT_MEDIA = {"batch-pca.png": "image/png", "batch-pca.json": "application/json"}


def _absent(reason: str) -> dict[str, Any]:
    return {
        "figure": None,
        "numeric_sidecar": None,
        "reason": reason,
        "plot_state": diagnostic_plot_state(
            rendered=False, render_verified=False, image_accessible=False,
            scientific_assessment="unavailable",
        ),
    }


def _absence_reason(batch: dict[str, Any] | None) -> str | None:
    if batch is None:
        return "batch_not_requested"
    if batch["status"] == "not_identifiable":
        return "batch_comparison_not_identifiable"
    if batch["status"] != "descriptive":
        return "batch_comparison_unavailable"
    pca = batch["pca"]
    if pca is None:
        return "pca_unavailable"
    if len(pca["variance_ratio"]) != 2 or pca["variance_ratio"][1] <= 0:
        return "pca_two_components_required"
    if not any(row["batch_partial_r2"] is not None for row in batch["genes"]):
        return "batch_partial_r2_unavailable"
    return None


def _numeric_inputs(batch: dict[str, Any], audit_id: str) -> dict[str, Any]:
    pca = batch["pca"]
    genes = batch["genes"]
    if not 2 <= len(pca["sample_ids"]) <= 500 or not 1 <= len(genes) <= 100:
        raise ValueError("Batch figure inputs exceed the diagnostic scope")
    evaluated = sorted(
        (row for row in genes if row["batch_partial_r2"] is not None),
        key=lambda row: (row["batch_partial_r2"], row["gene_id"]),
    )
    return {
        "format": "devbench.workbench-batch-pca/1",
        "audit_id": audit_id,
        "pca": pca,
        "batch": {
            key: batch[key] for key in ("column", "adjusted_for", "status", "genes")
        },
        "partial_r2_plot_order": [row["gene_id"] for row in evaluated],
    }


def _checked_scatter(ax: Any, points: np.ndarray, **kwargs: Any) -> None:
    drawn = ax.scatter(points[:, 0], points[:, 1], **kwargs).get_offsets()
    if not np.array_equal(np.asarray(drawn), points):
        raise ValueError("Rendered points differ from the numeric sidecar")


def _draw_pca(ax: Any, pca: dict[str, Any], palette: Any) -> None:
    coords = np.asarray(pca["coordinates"], dtype=float)
    labels = np.asarray(pca["batch_labels"], dtype=object)
    if (
        coords.shape != (len(pca["sample_ids"]), 2)
        or len(pca["sample_ids"]) != len(set(pca["sample_ids"]))
        or len(labels) != len(coords)
        or not np.isfinite(coords).all()
    ):
        raise ValueError("PCA coordinates require a finite, exact observation axis")
    groups = sorted(set(labels))
    if 1 < len(groups) <= 12:
        for index, group in enumerate(groups):
            _checked_scatter(
                ax, coords[labels == group], color=palette(index), label=group, s=24,
            )
        ax.legend(title="Batch", loc="best", fontsize="small")
    else:
        _checked_scatter(ax, coords, color=palette(0), s=24)
    variance = pca["variance_ratio"]
    ax.set(
        title="PCA of observed profiles",
        xlabel=f"PC1 ({variance[0]:.1%} of variance)",
        ylabel=f"PC2 ({variance[1]:.1%} of variance)",
    )


def _draw_partial_r2(ax: Any, numeric: dict[str, Any]) -> None:
    partial = {
        row["gene_id"]: row["batch_partial_r2"] for row in numeric["batch"]["genes"]
    }
    values = np.asarray([partial[gene] for gene in numeric["partial_r2_plot_order"]])
    if not np.isfinite(values).all() or not np.all((0 <= values) & (values <= 1)):
        raise ValueError("Batch partial R² values require finite unit interval bounds")
    _checked_scatter(
        ax, np.column_stack((1 + np.arange(len(values)), values)),
        color="#007f78", s=20,
    )
    ax.set(
        title=f"Batch partial R² ({len(values)} of {len(partial)} genes)",
        xlabel="Genes ordered by partial R²", ylabel="Partial R²", ylim=(-0.03, 1.03),
    )


def _render_png(numeric: dict[str, Any]) -> bytes:
    from matplotlib import colormaps, rc_context
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from PIL import Image

    from devbench.analysis.scientific_plot_artifacts import _LOCK

    with _LOCK, rc_context({"text.usetex": False, "text.parse_math": False}):
        figure = Figure(figsize=(9, 4), dpi=150, layout="constrained")
        FigureCanvasAgg(figure)
        pca, partial = figure.subplots(1, 2)
        _draw_pca(pca, numeric["pca"], colormaps["tab20"])
        _draw_partial_r2(partial, numeric)
        with io.BytesIO() as buffer:
            figure.savefig(buffer, format="png", metadata={"Software": "devbench"})
            png = buffer.getvalue()
    with Image.open(io.BytesIO(png)) as image:
        if image.format != "PNG":
            raise ValueError("Matrix diagnostic renderer did not produce a PNG")
        image.verify()
    return png


def _artifact_descriptor(
    vault: PartitionVault, url: str, name: str, raw: bytes,
) -> dict[str, str]:
    sha256 = vault.put(raw)
    return {"name": name, "sha256": sha256, "url": f"{url}/{sha256}/{name}"}


def retain_matrix_audit_plot(
    workspace: Path, investigation_id: str, audit_id: str, batch: dict[str, Any] | None,
) -> dict[str, Any]:
    """Use only the registered diagnostic output, with no refit or matrix reads."""
    reason = _absence_reason(batch)
    if reason is not None:
        return _absent(reason)
    assert batch is not None
    numeric = _numeric_inputs(batch, audit_id)
    try:
        png = _render_png(numeric)
    except ImportError:
        return _absent("plot_runtime_unavailable")
    url = f"/api/investigations/{investigation_id}/matrix-audit/{audit_id}"
    vault = PartitionVault(workspace / _ARTIFACT_DIR, max_bytes=_MAX_BYTES)
    return {
        "figure": _artifact_descriptor(vault, url, "batch-pca.png", png),
        "numeric_sidecar": _artifact_descriptor(
            vault, url, "batch-pca.json", contract_json_bytes(numeric),
        ),
        "reason": None,
        "plot_state": diagnostic_plot_state(
            rendered=True, render_verified=True, image_accessible=False,
            scientific_assessment="pending",
        ),
    }


def read_matrix_plot_blob(workspace: Path, sha256: str) -> bytes:
    root = workspace / _ARTIFACT_DIR
    if not root.is_dir():
        raise FileNotFoundError("Matrix diagnostic artifacts are unavailable")
    with PartitionVault(root, max_bytes=_MAX_BYTES).open(sha256) as handle:
        return handle.read()
