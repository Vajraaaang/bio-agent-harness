"""Run the pinned upstream SATURN CLI against local, manifest-verified inputs."""

import os
import pickle
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import anndata as ad
import numpy as np
import pandas as pd


def _proteins(path):
    header, pieces = None, []
    with path.open() as source:
        for line in source:
            line = line.strip()
            if line.startswith(">"):
                if header is not None:
                    yield header, "".join(pieces)
                header, pieces = line[1:].split()[0], []
            elif line:
                pieces.append(line)
        if header is not None:
            yield header, "".join(pieces)


def _esm_embeddings(data, paths, taxa):
    import torch
    from esm.data import Alphabet
    from esm.model.esm2 import ESM2

    checkpoint = torch.load(
        paths["saturn_model_weights"], map_location="cpu", weights_only=True
    )
    alphabet = Alphabet.from_architecture("ESM-1b")
    model = ESM2(
        num_layers=checkpoint["num_layers"],
        embed_dim=checkpoint["embed_dim"],
        attention_heads=checkpoint["attention_heads"],
        alphabet=alphabet,
        token_dropout=checkpoint.get("token_dropout", True),
    )
    model.load_state_dict(checkpoint["state_dict"], strict=True)
    model.cuda().eval()
    convert = alphabet.get_batch_converter()
    embeddings = {t: {} for t in taxa}
    with torch.inference_mode():
        for key, seq in _proteins(paths["proteome_fasta"]):
            t, gene = key.split("_", 1)
            if t not in embeddings or gene not in data.var_names:
                continue
            if not seq or gene in embeddings[t]:
                raise ValueError("Empty or duplicate staged SATURN protein")
            # Full-length mean pooling across nonoverlapping ESM context windows.
            total = None
            for offset in range(0, len(seq), 1022):
                piece = seq[offset : offset + 1022]
                _, _, tokens = convert([(key, piece)])
                result = model(tokens.cuda(), repr_layers=[model.num_layers])
                embedding = (
                    result["representations"][model.num_layers][0, 1 : 1 + len(piece)]
                    .sum(0)
                    .cpu()
                )
                total = embedding if total is None else total + embedding
            embeddings[t][gene] = total / len(seq)
    return embeddings


def _stage_species(folder, data, taxa, embeddings, save_embeddings):
    # SATURN obtains barcodes from CSV order but batches cells in sorted species order.
    rows = []
    for tax in sorted(taxa):
        genes = sorted(embeddings[tax])
        if len(genes) < 2:
            raise ValueError("Insufficient staged SATURN protein coverage")
        name = "tax" + tax
        expr = folder / (name + ".h5ad")
        dataset = data[data.obs.species_taxon_id.astype(str) == tax, genes].copy()
        dataset.write_h5ad(expr)
        protein = folder / (name + ".pt")
        save_embeddings(embeddings[tax], protein)
        rows.append(
            {"species": name, "path": str(expr), "embedding_path": str(protein)}
        )
    pd.DataFrame(rows).to_csv(folder / "input.csv", index=False)


def _recover_coordinates(data, result):
    if not result.obs_names.is_unique or set(result.obs_names) != set(data.obs_names):
        raise ValueError("SATURN cell axis mismatch")
    ordered = result[data.obs_names]
    expected = "tax" + data.obs["species_taxon_id"].astype(str)
    if "species" not in ordered.obs or not np.array_equal(
        ordered.obs["species"].astype(str), expected
    ):
        raise ValueError("SATURN species mismatch for returned barcode")
    return np.asarray(ordered.X)


def offline_saturn(data, paths, taxa, opt):
    """Encode staged taxon-prefixed proteins, then fit SATURN with frozen centroids.

    The local ESM2 checkpoint uses a weights-only state dict and primitive
    architecture metadata. No pretrained model loader or hub lookup is used.
    """
    import torch

    script = Path("/opt/SATURN/train-saturn.py")
    if not script.is_file() or not torch.cuda.is_available():
        raise RuntimeError("offline SATURN GPU runtime unavailable")
    if not opt.training_label_key or opt.training_label_key not in data.obs:
        raise ValueError("SATURN requires an independent training-label column")
    embeddings = _esm_embeddings(data, paths, taxa)
    with TemporaryDirectory() as tmp:
        folder = Path(tmp)
        _stage_species(folder, data, taxa, embeddings, torch.save)
        table = pd.read_csv(paths["macrogene_map"], sep="\t", index_col=0)
        if (
            not table.index.is_unique
            or not np.isfinite(table.to_numpy(float)).all()
            or np.any(table.to_numpy(float) < 0)
        ):
            raise ValueError("Invalid staged numerical macrogene weights")
        weights = {"tax" + str(k): v.to_numpy(float) for k, v in table.iterrows()}
        if any("tax" + t + "_" + g not in weights for t in taxa for g in embeddings[t]):
            raise ValueError("Missing macrogene weights for staged protein")
        centroid = folder / "centroids.pkl"
        with centroid.open("wb") as dest:
            pickle.dump(weights, dest)
        subprocess.run(
            [
                sys.executable,
                str(script),
                "--in_data",
                str(folder / "input.csv"),
                "--in_label_col",
                opt.training_label_key,
                "--ref_label_col",
                opt.training_label_key,
                "--work_dir",
                tmp,
                "--embedding_model",
                "ESM2",
                "--centroids_init_path",
                str(centroid),
                "--num_macrogenes",
                str(table.shape[1]),
                "--seed",
                str(opt.seed),
                "--epochs",
                str(opt.epochs),
                "--pretrain_epochs",
                str(opt.epochs),
            ],
            cwd=tmp,
            check=True,
            env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
        )
        final = [
            p
            for p in (folder / "saturn_results").glob("*.h5ad")
            if not p.stem.endswith("_pretrain") and "_epoch_" not in p.stem
        ]
        if len(final) != 1:
            raise ValueError("SATURN final embedding missing or ambiguous")
        return _recover_coordinates(data, ad.read_h5ad(final[0]))
