"""Deterministic offline views from explicit saved plotting tables."""
from __future__ import annotations

import math
from pathlib import Path

from .plot_palettes import category


def _validate_thresholds(thresholds):
    if thresholds is None:
        return []
    if not isinstance(thresholds, list) or len(thresholds) > 256:
        raise ValueError("bounded_saved_QC_thresholds_required")
    for item in thresholds:
        if (not isinstance(item, dict) or not isinstance(item.get("field"), str)
                or type(item.get("value")) not in (int, float) or not math.isfinite(item["value"])
                or not isinstance(item.get("label"), str) or not item["label"]
                or not isinstance(item.get("source_ref"), dict)):
            raise ValueError("QC_threshold_requires_finite_value_label_and_source_reference")
        from .figure_artifacts import _source_ref
        _source_ref(item["source_ref"])
    return thresholds


def render_figures(embedding, qc, output, *, thresholds=None, source_label=None, include_kde=True):
    """Render complete supplied rows; thresholds must be supplied with source custody."""
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from .checkpoints import atomic_json

    if type(include_kde) is not bool:
        raise ValueError("include_kde_must_be_boolean")
    thresholds = _validate_thresholds(thresholds)
    if embedding.index.has_duplicates or qc.index.has_duplicates or not set(embedding.index) <= set(qc.index):
        raise ValueError("offline_plot_source_identities_must_be_unique_and_aligned")
    if "qc_pass" in qc and not all(type(value) is bool for value in qc.qc_pass.tolist()):
        raise ValueError("offline_QC_membership_must_be_boolean")
    output = Path(output)
    if not output.is_absolute():
        raise ValueError("offline_plot_output_must_be_absolute")
    output.mkdir(parents=True, exist_ok=True)
    source_label = source_label or "Supplied saved tables; upstream analysis provenance not reverified"
    context = {"source_label": source_label, "embedding_ids": embedding.index.astype(str).tolist(),
        "qc_ids": qc.index.astype(str).tolist(), "thresholds": thresholds, "subsampling": False,
        "palette": "Okabe-Ito with shape fallback; viridis continuous", "dpi": 300,
        "populations": {"provided": len(qc), "saved_retained": int(qc.qc_pass.sum()) if "qc_pass" in qc else None},
        "distribution_fields": {}, "source_labels": {field: embedding[field].astype(str).to_dict()
            for field in ("sample_id", "donor_id", "batch") if field in embedding}}
    with plt.rc_context({"font.family": "DejaVu Sans", "font.size": 10,
                         "text.usetex": False, "text.parse_math": False,
                         "figure.dpi": 100, "savefig.dpi": 300}):
        fig, ax = plt.subplots(figsize=(8, 5), constrained_layout=True)
        labels = sorted(embedding["cell_type"].astype(str).unique())
        for index, label in enumerate(labels):
            group = embedding.loc[embedding["cell_type"].astype(str) == label]
            color, marker = category(index)
            ax.scatter(group["UMAP_1"], group["UMAP_2"], s=9, alpha=.75, linewidths=0,
                       marker=marker, label=f"{label} (n={len(group)})", color=color)
        ax.set(xlabel="UMAP 1 (arbitrary units)", ylabel="UMAP 2 (arbitrary units)",
               title="Computational cell-type predictions")
        ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1), markerscale=2, frameon=False, fontsize=8)
        identities = []
        for field in ("sample_id", "donor_id"):
            if field in embedding:
                labels = sorted(embedding[field].astype(str).unique())
                identities.append(field + ": " + (", ".join(labels) if len(labels) <= 8 else str(len(labels)) + " saved identities; see plotting-context.json"))
            else:
                identities.append(field + ": unavailable")
        fig.supxlabel(source_label + "\n" + "; ".join(identities), fontsize=8)
        fig.savefig(output / "embedding.png")
        plt.close(fig)
        has_membership = "qc_pass" in qc
        fig, axes = plt.subplots(1, 2 if has_membership else 1, figsize=(12 if has_membership else 7, 5),
                                  squeeze=False, sharex=True, sharey=True, constrained_layout=True)
        populations = [("All supplied QC cells", qc)]
        if has_membership:
            populations.append(("Saved QC-retained cells", qc.loc[qc.qc_pass]))
        all_values = qc.pct_counts_mt.to_numpy(dtype=float)
        finite = all_values[np.isfinite(all_values)]
        low, high = (float(finite.min()), float(finite.max())) if len(finite) else (0, 1)
        for ax, (title, frame) in zip(axes[0], populations):
            points = ax.scatter(frame["log1p_total_counts"], frame["log1p_n_genes_by_counts"],
                c=frame["pct_counts_mt"], s=9, alpha=.75, linewidths=0, cmap="viridis", vmin=low, vmax=high)
            for line in thresholds:
                for axis, field in (("x", "total_counts"), ("y", "n_genes_by_counts")):
                    if line["field"] in {field, "log1p_" + field}:
                        value = math.log1p(line["value"]) if line["field"] == field else line["value"]
                        (ax.axvline if axis == "x" else ax.axhline)(value, linestyle="--", color="#D55E00", label=line["label"])
            if ax.get_legend_handles_labels()[0]:
                ax.legend(loc="upper center", bbox_to_anchor=(.5, -.18), fontsize=7, frameon=False)
            ax.set(xlabel="ln(1 + total counts)", ylabel="ln(1 + detected genes)", title=f"{title} (n={len(frame)})")
            fig.colorbar(points, ax=ax, label="Mitochondrial counts (%) · viridis")
        fig.supxlabel(source_label, fontsize=8)
        fig.savefig(output / "qc.png")
        plt.close(fig)
        fields = [field for field in ("total_counts", "n_genes_by_counts", "pct_counts_mt") if field in qc]
        if fields:
            fig, axes = plt.subplots(1, len(fields), figsize=(5*len(fields), 4.5), squeeze=False, constrained_layout=True)
            for ax, field in zip(axes[0], fields):
                groups = [("All supplied cells", qc)] + ([("Saved QC-retained", qc.loc[qc.qc_pass])] if has_membership else [])
                values = qc[field].to_numpy(dtype=float)
                finite = values[np.isfinite(values)]
                edges = np.histogram_bin_edges(finite, bins="auto") if len(finite) else np.array([0., 1.])
                context["distribution_fields"][field] = {"bin_edges": edges.tolist(), "finite_ids": qc.index[np.isfinite(values)].astype(str).tolist(),
                    "missing_ids": qc.index[~np.isfinite(values)].astype(str).tolist(), "kde": []}
                for index, (name, frame) in enumerate(groups):
                    valid = frame[field].to_numpy(dtype=float)
                    valid = valid[np.isfinite(valid)]
                    color, _ = category(index)
                    if not len(valid):
                        continue
                    ax.hist(valid, bins=edges, density=True, alpha=.3, color=color, label=name + f" (n={len(valid)})")
                    if include_kde and len(valid) >= 3 and np.ptp(valid) > 0:
                        from scipy.stats import gaussian_kde
                        kde = gaussian_kde(valid, bw_method="scott")
                        grid = np.linspace(float(finite.min()), float(finite.max()), 128)
                        density = kde(grid)
                        ax.plot(grid, density, color=color, linewidth=1.2, label=name + " KDE (Scott)")
                        context["distribution_fields"][field]["kde"].append({"population": name,
                            "method": "Gaussian KDE; Scott bandwidth; descriptive over observed range", "factor": float(kde.factor),
                            "x": grid.tolist(), "density": density.tolist()})
                for line in thresholds:
                    if line["field"] == field:
                        ax.axvline(line["value"], linestyle="--", color="#D55E00", label=line["label"])
                ax.set(title=field, xlabel="Mitochondrial counts (%)" if field == "pct_counts_mt" else field + " (counts)", ylabel="Density")
                ax.legend(loc="upper center", bbox_to_anchor=(.5, -.2), fontsize=7, frameon=False)
            fig.supxlabel(source_label + "; KDE is descriptive smoothing, not a filtering rule.", fontsize=8)
            fig.savefig(output / "qc-distributions.png")
            plt.close(fig)
    atomic_json(output / "plotting-context.json", context)
    return context
