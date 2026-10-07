"""Embedding-only integration with separate checkpointed numerical stages."""
from __future__ import annotations

import json
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd

from .checkpoints import atomic_json, sha256


def train_scvi(data, output, *, batch_key, seed, max_epochs=100, n_latent=20,
               identity_sha256, checkpoint_seconds=120, extra_callbacks=()):
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
    record_path = output/"training-state.json"
    resume = None
    resumed_epoch = None
    if record_path.exists():
        record = json.loads(record_path.read_text())
        candidate = output/record["file"]
        if (record["identity_sha256"] != identity_sha256 or Path(record["file"]).name != record["file"]
                or not candidate.is_file() or candidate.is_symlink() or sha256(candidate) != record["sha256"]):
            raise ValueError("scvi_training_checkpoint_identity_mismatch")
        resume, resumed_epoch = str(candidate), record["epoch"]

    class DurableTraining(Callback):
        def __init__(self):
            self.last = time.monotonic()

        def save(self, trainer):
            name = f"training-{trainer.global_step:09d}-{trainer.current_epoch:05d}.ckpt"
            temporary = output/("."+name)
            trainer.save_checkpoint(temporary)
            temporary.replace(output/name)
            previous = json.loads(record_path.read_text())["file"] if record_path.exists() else None
            atomic_json(record_path, {"file": name, "sha256": sha256(output/name),
                "identity_sha256": identity_sha256, "epoch": trainer.current_epoch,
                "global_step": trainer.global_step, "device": accelerator})
            # Old in-progress training states are replaceable, completed stage
            # receipts are not. Keep the latest verified state on Drive.
            if previous and previous != name:
                (output/previous).unlink(missing_ok=True)
            self.last = time.monotonic()

        def on_train_batch_end(self, trainer, pl_module, outputs, batch, batch_idx):
            if time.monotonic()-self.last >= checkpoint_seconds:
                self.save(trainer)

        def on_train_epoch_end(self, trainer, pl_module):
            self.save(trainer)

    model.train(max_epochs=max_epochs, accelerator=accelerator, devices=1, batch_size=128,
        train_size=.9, early_stopping=False, check_val_every_n_epoch=1,
        enable_checkpointing=False, callbacks=[DurableTraining(), *extra_callbacks],
        enable_progress_bar=False, benchmark=False, deterministic=True,
        default_root_dir=str(output), ckpt_path=resume,
        datasplitter_kwargs={"num_workers": 0})
    representation = model.get_latent_representation()
    if not np.isfinite(representation).all():
        raise ValueError("nonfinite_scvi_latent_representation")
    model.save(output/"model", overwrite=True, save_anndata=False)
    report = {"method": "scvi_SCVI_NB", "device_used": accelerator,
        "device_name": torch.cuda.get_device_name(0) if accelerator == "gpu" else "CPU",
        "epochs_requested": max_epochs, "epochs_completed": model.trainer.current_epoch,
        "resumed_from_epoch": resumed_epoch, "n_latent": n_latent, "seed": seed,
        "checkpoint_interval_seconds": checkpoint_seconds, "checkpoint_on_every_epoch": True,
        "bitwise_resume_equivalence_established": False,
        "scope": "unsupervised_latent_integration_not_classifier_reproduction_or_causal_correction"}
    atomic_json(output/"method.json", report)
    np.save(output/"latent.npy", representation, allow_pickle=False)
    return report


def run_embedding_stages(store, *, batch_key, seed, correction="harmony", n_pcs=30,
                         resolution=1.0, scvi_epochs=100):
    import scanpy as sc

    def pca(folder):
        data = ad.read_h5ad(store.path("selected")/"selected.h5ad")
        components = min(n_pcs, data.n_vars-1, data.n_obs-1)
        if components < 2:
            raise ValueError("insufficient_cells_or_features_for_PCA")
        sc.pp.pca(data, n_comps=components, svd_solver="arpack", random_state=seed)
        np.save(folder/"latent.npy", data.obsm["X_pca"], allow_pickle=False)
        return {"method": "PCA", "components": components, "device_used": "CPU"}
    store.run("pca", pca, dependencies=("selected",))
    conditions = {"uncorrected": "pca"}
    def integration_status(folder):
        data = ad.read_h5ad(store.path("selected")/"selected.h5ad", backed="r")
        try:
            if batch_key not in data.obs or data.obs[batch_key].isna().any():
                raise ValueError("Integration requires nonmissing batch identities")
            batches = int(data.obs[batch_key].nunique())
        finally:
            data.file.close()
        status = ("not_requested" if correction == "none" else
                  "not_applicable_single_observed_batch" if batches < 2 else "applicable")
        report = {"requested_method": correction, "status": status, "observed_batches": batches,
                  "scope": "technical_batch_structure_not_independent_biological_units"}
        atomic_json(folder/"report.json", report)
        return report
    status_path = store.run("integration_status", integration_status, dependencies=("selected",))
    status = json.loads((status_path/"report.json").read_text())
    if status["status"] == "not_applicable_single_observed_batch":
        correction = "none"
    if correction == "harmony":
        def harmony(folder):
            import harmonypy
            data = ad.read_h5ad(store.path("selected")/"selected.h5ad", backed="r")
            try:
                obs = data.obs[[batch_key]].copy()
            finally:
                data.file.close()
            latent = np.load(store.path("pca")/"latent.npy", allow_pickle=False)
            fit = harmonypy.run_harmony(latent, obs, batch_key, random_state=seed,
                nclust=max(2, min(100, len(obs)//30)), max_iter_harmony=10, verbose=False, device="cpu")
            corrected = np.asarray(fit.Z_corr)
            if corrected.shape == latent.T.shape:
                corrected = corrected.T
            if corrected.shape != latent.shape or not np.isfinite(corrected).all():
                raise ValueError("invalid_harmony_embedding")
            np.save(folder/"latent.npy", corrected, allow_pickle=False)
            return {"method": "harmonypy", "device_used": "CPU", "maximum_iterations": 10,
                    "scope": "embedding_only_not_expression_or_causal_batch_adjustment"}
        store.run("harmony", harmony, dependencies=("pca",))
        conditions["harmony"] = "harmony"
    elif correction == "scvi":
        def scvi_stage(folder):
            data = ad.read_h5ad(store.path("selected")/"selected.h5ad")
            return train_scvi(data, folder, batch_key=batch_key, seed=seed,
                max_epochs=scvi_epochs, identity_sha256=store.identity)
        store.run("scvi", scvi_stage, dependencies=("selected",))
        conditions["scvi"] = "scvi"
    elif correction != "none":
        raise ValueError("Unknown batch correction method")
    for condition, latent_stage in conditions.items():
        def graph(folder):
            source = ad.read_h5ad(store.path("selected")/"selected.h5ad", backed="r")
            try:
                data = ad.AnnData(obs=source.obs[[batch_key]].copy())
            finally:
                source.file.close()
            latent = np.load(store.path(latent_stage)/"latent.npy", allow_pickle=False)
            data.obsm["latent"] = latent
            sc.pp.neighbors(data, n_neighbors=min(15, data.n_obs-1), use_rep="latent", random_state=seed)
            sc.tl.leiden(data, flavor="igraph", directed=False, n_iterations=2,
                         resolution=resolution, random_state=seed)
            sc.tl.umap(data, random_state=seed)
            if not np.isfinite(data.obsm["X_umap"]).all():
                raise ValueError("nonfinite_UMAP")
            frame = pd.DataFrame(data.obsm["X_umap"], index=data.obs_names, columns=["UMAP_1", "UMAP_2"])
            frame["leiden"] = data.obs.leiden.astype(str)
            frame[batch_key] = data.obs[batch_key].astype(str)
            frame.to_csv(folder/"embedding.csv")
            from scipy import sparse
            sparse.save_npz(folder/"neighbors.npz", data.obsp["connectivities"])
            return {"condition": condition, "seed": seed, "resolution": resolution,
                    "clusters": int(data.obs.leiden.nunique()), "cells": data.n_obs}
        store.run(condition+"_graph", graph, dependencies=(latent_stage,))
    return conditions
