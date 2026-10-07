"""Deterministic figure rendering from explicit saved plotting tables."""
from pathlib import Path


def render_figures(embedding, qc, output):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with plt.rc_context({"font.family": "DejaVu Sans", "font.size": 10,
                         "figure.dpi": 100, "savefig.dpi": 100}):
        fig, ax = plt.subplots(figsize=(7, 5), constrained_layout=True)
        labels = sorted(embedding["cell_type"].astype(str).unique())
        palette = plt.get_cmap("tab20")
        for index, label in enumerate(labels):
            group = embedding.loc[embedding["cell_type"].astype(str) == label]
            ax.scatter(group["UMAP_1"], group["UMAP_2"], s=9, alpha=.75, linewidths=0,
                       label=label, color=palette(index % 20))
        ax.set(xlabel="UMAP 1 (arbitrary units)", ylabel="UMAP 2 (arbitrary units)",
               title="Computational cell-type predictions")
        ax.legend(loc="best", markerscale=2, frameon=False)
        fig.savefig(output / "embedding.png")
        plt.close(fig)
        fig, ax = plt.subplots(figsize=(7, 5), constrained_layout=True)
        points = ax.scatter(qc["log1p_total_counts"], qc["log1p_n_genes_by_counts"],
                            c=qc["pct_counts_mt"], s=9, alpha=.75, linewidths=0, cmap="viridis")
        ax.set(xlabel="log(1 + total counts)", ylabel="log(1 + detected genes)",
               title="Quality metrics for retained cells")
        fig.colorbar(points, ax=ax, label="Mitochondrial counts (%)")
        fig.savefig(output / "qc.png")
        plt.close(fig)

