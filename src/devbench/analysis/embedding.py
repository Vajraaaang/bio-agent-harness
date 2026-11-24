"""Embedding-only integration with separate checkpointed numerical stages."""

from __future__ import annotations

import io
import json
from hashlib import sha256 as byte_hash
from pathlib import Path
from zipfile import ZipFile

import anndata as ad
import numpy as np
import pandas as pd
from scipy import sparse

from devbench.core._files import read_bounded_file
from rna_kernels.embedding_qc import EMBEDDING_THRESHOLDS, embedding_rank

from .checkpoints import CheckpointStore, atomic_json, sha256
from .integration_metrics import (
    ExternalLabelEvidence,
    assess_integration,
    verify_external_labels,
)


def train_scvi(
    data,
    output,
    *,
    batch_key,
    seed,
    max_epochs=100,
    n_latent=20,
    identity_sha256,
    checkpoint_seconds=120,
    extra_callbacks=(),
):
    """Resume this run's Lightning training state on CPU or an observed CUDA GPU.

    Resumption restores optimizer/epoch state, not a promise of bitwise identical
    stochastic training after a disconnect or across different devices.
    """
    import time

    import scvi
    import torch
    from lightning.pytorch.callbacks import Callback

    from .singlecell import validate_counts

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    validate_counts(data.layers["counts"])
    scvi.settings.seed = seed
    scvi.settings.num_threads = 1
    scvi.settings.dl_num_workers = 0
    scvi.model.SCVI.setup_anndata(data, layer="counts", batch_key=batch_key)
    model = scvi.model.SCVI(data, n_latent=n_latent, gene_likelihood="nb")
    accelerator = "gpu" if torch.cuda.is_available() else "cpu"
    record_path = output / "training-state.json"
    resume = None
    resumed_epoch = None
    if record_path.exists():
        record = json.loads(record_path.read_text())
        candidate = output / record["file"]
        if (
            record["identity_sha256"] != identity_sha256
            or Path(record["file"]).name != record["file"]
            or not candidate.is_file()
            or candidate.is_symlink()
            or sha256(candidate) != record["sha256"]
        ):
            raise ValueError("scvi_training_checkpoint_identity_mismatch")
        resume, resumed_epoch = str(candidate), record["epoch"]

    class DurableTraining(Callback):
        def __init__(self):
            self.last = time.monotonic()

        def save(self, trainer):
            name = (
                f"training-{trainer.global_step:09d}-{trainer.current_epoch:05d}.ckpt"
            )
            temporary = output / ("." + name)
            trainer.save_checkpoint(temporary)
            temporary.replace(output / name)
            previous = (
                json.loads(record_path.read_text())["file"]
                if record_path.exists()
                else None
            )
            atomic_json(
                record_path,
                {
                    "file": name,
                    "sha256": sha256(output / name),
                    "identity_sha256": identity_sha256,
                    "epoch": trainer.current_epoch,
                    "global_step": trainer.global_step,
                    "device": accelerator,
                },
            )
            # Old in-progress training states are replaceable, completed stage
            # receipts are not. Keep the latest verified state on Drive.
            if previous and previous != name:
                (output / previous).unlink(missing_ok=True)
            self.last = time.monotonic()

        def on_train_batch_end(self, trainer, pl_module, outputs, batch, batch_idx):
            if time.monotonic() - self.last >= checkpoint_seconds:
                self.save(trainer)

        def on_train_epoch_end(self, trainer, pl_module):
            self.save(trainer)

    model.train(
        max_epochs=max_epochs,
        accelerator=accelerator,
        devices=1,
        batch_size=128,
        train_size=0.9,
        early_stopping=False,
        check_val_every_n_epoch=1,
        enable_checkpointing=False,
        callbacks=[DurableTraining(), *extra_callbacks],
        enable_progress_bar=False,
        benchmark=False,
        deterministic=True,
        default_root_dir=str(output),
        ckpt_path=resume,
        datasplitter_kwargs={"num_workers": 0},
    )
    representation = model.get_latent_representation()
    if not np.isfinite(representation).all():
        raise ValueError("nonfinite_scvi_latent_representation")
    model.save(output / "model", overwrite=True, save_anndata=False)
    report = {
        "method": "scvi_SCVI_NB",
        "device_used": accelerator,
        "device_name": torch.cuda.get_device_name(0) if accelerator == "gpu" else "CPU",
        "epochs_requested": max_epochs,
        "epochs_completed": model.trainer.current_epoch,
        "resumed_from_epoch": resumed_epoch,
        "n_latent": n_latent,
        "seed": seed,
        "checkpoint_interval_seconds": checkpoint_seconds,
        "checkpoint_on_every_epoch": True,
        "bitwise_resume_equivalence_established": False,
        "scope": (
            "unsupervised_latent_integration_not_classifier_reproduction_or_causal_correction"
        ),
    }
    atomic_json(output / "method.json", report)
    np.save(output / "latent.npy", representation, allow_pickle=False)
    return report


def _save_pca(store, folder, *, n_pcs, seed):
    import scanpy as sc

    data = ad.read_h5ad(store.path("selected") / "selected.h5ad")
    components = min(n_pcs, data.n_vars - 1, data.n_obs - 1)
    if components < 2:
        raise ValueError("insufficient_cells_or_features_for_PCA")
    sc.pp.pca(data, n_comps=components, svd_solver="arpack", random_state=seed)
    np.save(folder / "latent.npy", data.obsm["X_pca"], allow_pickle=False)
    return {"method": "PCA", "components": components, "device_used": "CPU"}


def _batch_obs(store, batch_key):
    data = ad.read_h5ad(store.path("selected") / "selected.h5ad", backed="r")
    try:
        if batch_key not in data.obs or data.obs[batch_key].isna().any():
            raise ValueError("Integration requires nonmissing batch identities")
        return data.obs[[batch_key]].copy()
    finally:
        data.file.close()


def _integration_status(store, folder, *, batch_key, correction):
    batches = int(_batch_obs(store, batch_key)[batch_key].nunique())
    if correction == "none":
        status = "not_requested"
    elif batches < 2:
        status = "not_applicable_single_observed_batch"
    else:
        status = "applicable"
    report = {
        "requested_method": correction,
        "status": status,
        "observed_batches": batches,
        "scope": "technical_batch_structure_not_independent_biological_units",
    }
    atomic_json(folder / "report.json", report)
    return report


def _latent(store, stage):
    return np.load(store.path(stage) / "latent.npy", allow_pickle=False)


def _save_harmony(store, folder, *, batch_key, seed):
    import harmonypy

    obs = _batch_obs(store, batch_key)
    latent = _latent(store, "pca")
    fit = harmonypy.run_harmony(
        latent,
        obs,
        batch_key,
        random_state=seed,
        nclust=max(2, min(100, len(obs) // 30)),
        max_iter_harmony=10,
        verbose=False,
        device="cpu",
    )
    corrected = np.asarray(fit.Z_corr)
    if corrected.shape != latent.shape and corrected.shape == latent.T.shape:
        corrected = corrected.T
    if corrected.shape != latent.shape or not np.isfinite(corrected).all():
        raise ValueError("invalid_harmony_embedding")
    np.save(folder / "latent.npy", corrected, allow_pickle=False)
    return {
        "method": "harmonypy",
        "device_used": "CPU",
        "maximum_iterations": 10,
        "scope": "embedding_only_not_expression_or_causal_batch_adjustment",
    }


def _fit_correction(
    store,
    *,
    method,
    batch_key,
    seed,
    scvi_epochs,
    governed,
):
    def estimate(folder):
        try:
            if method == "harmony":
                return _save_harmony(store, folder, batch_key=batch_key, seed=seed)
            data = ad.read_h5ad(store.path("selected") / "selected.h5ad")
            return train_scvi(
                data,
                folder,
                batch_key=batch_key,
                seed=seed,
                max_epochs=scvi_epochs,
                identity_sha256=store.identity,
            )
        except (ImportError, ValueError, np.linalg.LinAlgError) as error:
            if not governed:
                raise
            return _record_failed_computation(folder, error)

    dependency = "pca" if method == "harmony" else "selected"
    return store.run(method, estimate, dependencies=(dependency,))


def _record_failed_computation(folder, error):
    missing = isinstance(error, ImportError)
    failure = {
        "status": "unavailable" if missing else "fail",
        "embedding_valid": False,
        "diagnostics": [
            "integration_dependency_unavailable" if missing else str(error)[:200]
        ],
        "error_type": type(error).__name__,
    }
    atomic_json(folder / "failure.json", failure)
    return failure


def _save_graph(store, folder, *, condition, latent_stage, batch_key, seed, resolution):
    import scanpy as sc

    data = ad.AnnData(obs=_batch_obs(store, batch_key))
    data.obsm["latent"] = _latent(store, latent_stage)
    sc.pp.neighbors(
        data,
        n_neighbors=min(15, data.n_obs - 1),
        use_rep="latent",
        random_state=seed,
    )
    sc.tl.leiden(
        data,
        flavor="igraph",
        directed=False,
        n_iterations=2,
        resolution=resolution,
        random_state=seed,
    )
    sc.tl.umap(data, random_state=seed)
    coordinates = data.obsm["X_umap"]
    if not np.isfinite(coordinates).all():
        raise ValueError("nonfinite_UMAP")
    frame = pd.DataFrame(
        coordinates, index=data.obs_names, columns=["UMAP_1", "UMAP_2"]
    )
    frame["leiden"] = data.obs.leiden.astype(str)
    frame[batch_key] = data.obs[batch_key].astype(str)
    frame.to_csv(folder / "embedding.csv")
    sparse.save_npz(folder / "neighbors.npz", data.obsp["connectivities"])
    return {
        "condition": condition,
        "seed": seed,
        "resolution": resolution,
        "clusters": int(data.obs.leiden.nunique()),
        "cells": data.n_obs,
    }


def _graph_stage(store, condition, stage, *, batch_key, seed, resolution, governed):
    def graph(folder):
        try:
            if governed:
                values = _latent(store, stage)
                if not np.isfinite(values).all():
                    raise ValueError("nonfinite_embedding")
                if not embedding_rank(
                    values, EMBEDDING_THRESHOLDS["rank_relative_tolerance"]
                ):
                    raise ValueError("collapsed_embedding")
            return _save_graph(
                store,
                folder,
                condition=condition,
                latent_stage=stage,
                batch_key=batch_key,
                seed=seed,
                resolution=resolution,
            )
        except (ImportError, ValueError, np.linalg.LinAlgError) as error:
            if not governed:
                raise
            return _record_failed_computation(folder, error)

    return store.run(condition + "_graph", graph, dependencies=(stage,))


def _read_graph(path: Path, n_cells: int):
    # NPZ is compressed, so bound the decompressed inventory before reading it.
    with ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 7 or sum(item.file_size for item in entries) > (
            8192 + 16 * n_cells * 65
        ):
            raise ValueError("integration_saved_graph_exceeds_byte_bound")
    return sparse.load_npz(path)


def _read_embedding_csv(path: Path, batch_key: str):
    raw = read_bounded_file(path, 32 * 1024**2)
    frame = pd.read_csv(
        io.BytesIO(raw),
        dtype={0: str, "leiden": str, batch_key: str},
        keep_default_na=False,
        float_precision="round_trip",
    )
    identities = frame.pop(frame.columns[0])
    frame.index = pd.Index(identities, name=None)
    return frame, byte_hash(raw).hexdigest()


def render_integration_embedding(
    csv_path: Path,
    png_path: Path,
    *,
    batch_key: str,
    condition: str,
) -> dict:
    """Render the admitted UMAP sidecar and check exact plotted numeric points."""
    try:
        from matplotlib import colormaps
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from PIL import Image
    except ImportError:
        return {"status": "unavailable", "reason": "plot_runtime_unavailable"}

    frame, data_sha256 = _read_embedding_csv(csv_path, batch_key)
    locations = frame[["UMAP_1", "UMAP_2"]].to_numpy(dtype=float)
    batch = frame[batch_key]
    if frame.empty or not frame.index.is_unique or not np.isfinite(locations).all():
        raise ValueError("integration_plot_requires_unique_finite_points")
    figure = Figure(figsize=(7, 5), dpi=150, layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots()
    groups = sorted(batch.unique())
    labeled = 1 < len(groups) <= 12
    panels = (
        [(group, locations[batch == group]) for group in groups]
        if labeled
        else [(None, locations)]
    )
    palette = colormaps.get_cmap("tab20")
    for index, (group, points) in enumerate(panels):
        artist = axes.scatter(
            points[:, 0],
            points[:, 1],
            label=group,
            color=palette(index),
            s=4,
            alpha=0.75,
        )
        if not np.array_equal(np.asarray(artist.get_offsets()), points):
            raise ValueError("integration_plot_coordinate_binding_mismatch")
    axes.set(
        title=f"{condition}: declared technical batches" if labeled else condition,
        xlabel="UMAP 1",
        ylabel="UMAP 2",
        aspect="equal",
    )
    if labeled:
        axes.legend(loc="best", markerscale=2)
    with io.BytesIO() as buffer:
        figure.savefig(buffer, format="png", metadata={"Software": "devbench"})
        raw = buffer.getvalue()
    with Image.open(io.BytesIO(raw)) as image:
        if image.format != "PNG":
            raise ValueError("integration_plot_invalid_encoding")
        image.verify()
    if not png_path.is_absolute() or png_path.resolve() != png_path:
        raise ValueError("integration_plot_requires_canonical_path")
    with png_path.open("xb") as target:
        target.write(raw)
    return {
        "status": "rendered",
        "data_sha256": data_sha256,
        "image_sha256": byte_hash(raw).hexdigest(),
        "plotted_cells": len(frame),
        "legend_displayed": labeled,
        "color_encoding": "declared_batch" if labeled else "single_color",
        "plot_state": {
            "rendered": True,
            "render_verified": True,
            "image_accessible": False,
            "scientific_assessment": "pending",
            "matched_threats": [],
            "reference_or_topology_validity_established_by_rendering": False,
        },
    }


def _saved_graph_assessment(
    store,
    condition,
    stage,
    *,
    batch_key,
    obs,
    seed,
    labels,
    selected_sha256,
):
    folder = store.path(condition + "_graph")
    csv_path = folder / "embedding.csv"
    coordinates, csv_sha256 = _read_embedding_csv(csv_path, batch_key)
    if not coordinates[batch_key].astype(str).equals(obs[batch_key].astype(str)):
        raise ValueError("integration_plot_batch_axis_mismatch")
    umap = coordinates[["UMAP_1", "UMAP_2"]].to_numpy(dtype=float)
    if not np.isfinite(umap).all():
        raise ValueError("nonfinite_UMAP")
    graph_path = folder / "neighbors.npz"
    latent_path = store.path(stage) / "latent.npy"
    reference = _latent(store, "pca")
    candidate = reference if stage == "pca" else _latent(store, stage)
    report = assess_integration(
        reference,
        candidate,
        _read_graph(graph_path, len(obs)),
        cell_ids=obs.index.tolist(),
        graph_cell_ids=coordinates.index.tolist(),
        batches=obs[batch_key].astype(str).tolist(),
        seed=seed,
        selected_sha256=selected_sha256,
        verified_labels=labels,
    )
    report["provenance"] = {
        "selected_sha256": selected_sha256,
        "graph_sha256": sha256(graph_path),
        "latent_sha256": sha256(latent_path),
        "embedding_csv_sha256": csv_sha256,
    }
    return report


def _assess_saved_conditions(
    store,
    conditions,
    failures,
    status,
    *,
    batch_key,
    seed,
    verified_labels,
):
    obs = _batch_obs(store, batch_key)
    selected_sha256 = sha256(store.path("selected") / "selected.h5ad")
    qualification = None
    if verified_labels is not None:
        _, qualification = verify_external_labels(
            verified_labels,
            cell_ids=obs.index.tolist(),
            selected_sha256=selected_sha256,
        )

    def assess(folder):
        assessments = dict(failures)
        for condition, stage in conditions.items():
            assessments[condition] = _saved_graph_assessment(
                store,
                condition,
                stage,
                batch_key=batch_key,
                obs=obs,
                seed=seed,
                labels=verified_labels,
                selected_sha256=selected_sha256,
            )
        usable = [
            name for name, result in assessments.items() if result["embedding_valid"]
        ]
        overall = "fail"
        if "uncorrected" in usable:
            overall = "pass" if len(usable) == len(assessments) else "partial"
        report = {
            "format": "devbench.scrna-integration-assessment/1",
            "status": overall,
            "conditions": assessments,
            "accepted_conditions": usable,
            "integration_status": status,
            "label_qualification": qualification,
            "scope": "descriptive_technical_and_representation_checks",
        }
        atomic_json(folder / "assessment.json", report)
        return report

    dependencies = (
        "selected",
        "integration_status",
        *(name + "_graph" for name in conditions),
        *(record["stage"] for record in failures.values()),
    )
    folder = store.run("integration_assessment", assess, dependencies=dependencies)
    report = json.loads((folder / "assessment.json").read_bytes())
    if report["label_qualification"] != qualification:
        raise ValueError("integration_assessment_label_qualification_changed")
    if report["status"] == "fail":
        raise ValueError("invalid_uncorrected_embedding")
    return {
        name: stage
        for name, stage in conditions.items()
        if name in report["accepted_conditions"]
    }


def run_embedding_stages(
    store: CheckpointStore,
    *,
    batch_key: str,
    seed: int,
    correction: str = "harmony",
    n_pcs: int = 30,
    resolution: float = 1.0,
    scvi_epochs: int = 100,
    governed: bool = False,
    verified_labels: ExternalLabelEvidence | None = None,
) -> dict[str, str]:
    """Run baseline and selected integration, returning only usable governed graphs."""
    if correction not in {"none", "harmony", "scvi"}:
        raise ValueError("Unknown batch correction method")
    if verified_labels is not None and not governed:
        raise ValueError("Integration label verification requires governed assessment")
    if governed and batch_key in {"leiden", "UMAP_1", "UMAP_2"}:
        raise ValueError("integration_batch_key_conflicts_with_plot_fields")
    store.run(
        "pca",
        lambda folder: _save_pca(store, folder, n_pcs=n_pcs, seed=seed),
        dependencies=("selected",),
    )
    status_path = store.run(
        "integration_status",
        lambda folder: _integration_status(
            store,
            folder,
            batch_key=batch_key,
            correction=correction,
        ),
        dependencies=("selected",),
    )
    status = json.loads((status_path / "report.json").read_bytes())
    conditions = {"uncorrected": "pca"}
    failures = {}
    if status["status"] == "applicable":
        fitted = _fit_correction(
            store,
            method=correction,
            batch_key=batch_key,
            seed=seed,
            scvi_epochs=scvi_epochs,
            governed=governed,
        )
        if (fitted / "failure.json").is_file():
            failures[correction] = {
                **json.loads((fitted / "failure.json").read_bytes()),
                "stage": correction,
            }
        else:
            conditions[correction] = correction
    for condition, stage in list(conditions.items()):
        graph = _graph_stage(
            store,
            condition,
            stage,
            batch_key=batch_key,
            seed=seed,
            resolution=resolution,
            governed=governed,
        )
        if (graph / "failure.json").is_file():
            failures[condition] = {
                **json.loads((graph / "failure.json").read_bytes()),
                "stage": condition + "_graph",
            }
            del conditions[condition]
    if governed:
        return _assess_saved_conditions(
            store,
            conditions,
            failures,
            status,
            batch_key=batch_key,
            seed=seed,
            verified_labels=verified_labels,
        )
    return conditions
