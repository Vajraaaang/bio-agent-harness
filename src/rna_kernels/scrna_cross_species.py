"""Label-qualified integration and ADR-28 homology custody in the mounted worker."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal

import numpy as np
import pandas as pd
from anndata import AnnData, read_h5ad
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy import sparse
from sklearn.decomposition import PCA

from devbench.analysis.integration_metrics import (
    ExternalLabelEvidence,
    scib_benchmark,
    verify_external_labels,
)
from devbench.core._files import read_bounded_json
from rna_kernels.m2_contract import source_digest

PRIMARY = "include_in_paralogs_one2many_max_expr"
R4 = "integrated_latent_or_graph"
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Name = Annotated[str, Field(min_length=1, max_length=256)]


class IntegrationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["pca", "harmony", "scvi", "scanvi", "scpoli", "sysvi"] = "pca"
    batch_key: Name = "batch"
    training_label_key: Name | None = None
    n_pcs: int = Field(20, ge=1, le=128)
    epochs: int = Field(20, ge=1, le=200)
    seed: int = Field(0, ge=0, le=2**31 - 1)


class CrossSpeciesOptions(IntegrationOptions):
    regime: Literal["close_mammals", "distant_species"]
    comparator: Literal["saturn"] | None = None
    batch_key: Literal["species_taxon_id"] = "species_taxon_id"

    @model_validator(mode="after")
    def check_route(self):
        if self.comparator and self.regime != "distant_species":
            raise ValueError("SATURN requires the distant species regime")
        if (
            self.regime == "distant_species"
            and "method" in self.model_fields_set
            and self.method != "pca"
        ):
            raise ValueError("SAMap is the primary distant species method")
        return self


PARAMETERS = {
    "scrna_integrate_qualified": IntegrationOptions,
    "scrna_cross_species_integrate": CrossSpeciesOptions,
}


class CrossSpeciesHomologyManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    homology_policy: Literal["ensembl_in_paralogs", "samap", "saturn"]
    ensembl_release: Name
    assembly_ids: list[Name] = Field(min_length=2, max_length=32)
    species_taxon_ids: list[Annotated[int, Field(gt=0)]] = Field(
        min_length=2, max_length=32
    )
    paralog_collapse_rule: Literal[
        "include_in_paralogs_one2many_max_expr", "homology_graph", "macrogenes"
    ]
    samap_mapping_table_sha256: Digest | None = None
    proteome_fasta_sha256: Digest | None = None
    blast_params: dict[str, str | float | int] | None = None
    saturn_model_weights_sha256: Digest | None = None
    macrogene_map_sha256: Digest | None = None

    @model_validator(mode="after")
    def check_policy(self):
        if (
            len(set(self.species_taxon_ids)) != len(self.species_taxon_ids)
            or len(set(self.assembly_ids)) != len(self.species_taxon_ids)
            or len(self.assembly_ids) != len(self.species_taxon_ids)
        ):
            raise ValueError("Assembly and taxon axes must be unique and aligned")
        rules = {
            "ensembl_in_paralogs": PRIMARY,
            "samap": "homology_graph",
            "saturn": "macrogenes",
        }
        if rules[self.homology_policy] != self.paralog_collapse_rule:
            raise ValueError("Mismatch between homology policy and paralog rule")
        if self.homology_policy == "samap" and not all(
            (
                self.samap_mapping_table_sha256,
                self.proteome_fasta_sha256,
                self.blast_params,
            )
        ):
            raise ValueError("SAMap mapping, FASTA and BLAST parameters required")
        if self.homology_policy == "saturn" and not all(
            (
                self.saturn_model_weights_sha256,
                self.macrogene_map_sha256,
                self.proteome_fasta_sha256,
            )
        ):
            raise ValueError("Offline SATURN weights, FASTA and macrogene map required")
        return self

    def verify(self, paths, taxa):
        if set(map(str, self.species_taxon_ids)) != set(taxa):
            raise ValueError("Manifest species differ from observed taxa")
        for name in (
            "samap_mapping_table",
            "proteome_fasta",
            "saturn_model_weights",
            "macrogene_map",
        ):
            digest = getattr(self, name + "_sha256")
            if digest is not None and (
                name not in paths or source_digest(paths[name]) != digest
            ):
                raise ValueError("Missing or mismatched offline reference: " + name)


def _counts(path):
    data = read_h5ad(path, backed="r")
    try:
        if (
            not 4 <= data.n_obs <= 100_000
            or data.n_obs * data.n_vars > 24_000_000
            or not data.obs_names.is_unique
            or not data.var_names.is_unique
        ):
            raise ValueError("Invalid or oversize integration axes")
        if data.uns.get("representation_class") not in {None, "count_1_raw_counts"}:
            raise ValueError("Expected admitted raw-count representation")
        raw = data.X[:]
        vals = raw.data if sparse.issparse(raw) else raw
        if (
            not np.isfinite(vals).all()
            or np.any(vals < 0)
            or np.any(vals != np.floor(vals))
        ):
            raise ValueError("Expected admitted raw counts")
        return AnnData(raw, obs=data.obs.copy(), var=data.var.copy())
    finally:
        data.file.close()


def _normalized(raw):
    x = raw.toarray() if sparse.issparse(raw) else np.asarray(raw)
    totals = x.sum(axis=1, dtype=np.float64)
    if np.any(totals <= 0):
        raise ValueError("Zero library size in integration input")
    return np.log1p(x * (10_000 / totals[:, None]))


def _embedding(data, opt):
    x = _normalized(data.X)
    rank = min(opt.n_pcs, data.n_obs - 1, data.n_vars)
    if opt.method in {"pca", "harmony"}:
        coords = PCA(rank, random_state=opt.seed).fit_transform(x)
        if opt.method == "harmony":
            import harmonypy

            model = harmonypy.run_harmony(
                coords,
                data.obs,
                opt.batch_key,
                random_state=opt.seed,
                nclust=max(2, min(100, len(data) // 30)),
            )
            coords = np.asarray(model.Z_corr)
            if coords.shape != (data.n_obs, rank) and coords.shape == (
                rank,
                data.n_obs,
            ):
                coords = coords.T
        return coords
    if opt.method in {"scanvi", "scpoli"} and (
        not opt.training_label_key or opt.training_label_key not in data.obs
    ):
        raise ValueError("Supervised integration requires frozen training labels")
    if opt.method == "scpoli":
        import torch
        from scarches.models.scpoli import scPoli

        torch.manual_seed(opt.seed)
        model = scPoli(
            adata=data,
            condition_keys=[opt.batch_key],
            cell_type_keys=[opt.training_label_key],
            latent_dim=rank,
            recon_loss="nb",
        )
        model.train(n_epochs=opt.epochs, pretraining_epochs=opt.epochs // 2)
        return model.get_latent(data, mean=True)
    import scvi

    scvi.settings.seed = opt.seed
    if opt.method == "sysvi":
        from scvi.external import SysVI

        data.X = x
        SysVI.setup_anndata(data, batch_key=opt.batch_key)
        model = SysVI(data, n_latent=rank)
    elif opt.method == "scanvi":
        cls = scvi.model.SCANVI
        cls.setup_anndata(
            data,
            batch_key=opt.batch_key,
            labels_key=opt.training_label_key,
            unlabeled_category="Unknown",
        )
        model = cls(data, n_latent=rank)
    else:
        scvi.model.SCVI.setup_anndata(data, batch_key=opt.batch_key)
        model = scvi.model.SCVI(data, n_latent=rank)
    model.train(max_epochs=opt.epochs, accelerator="cpu", devices=1)
    return model.get_latent_representation()


def _orthogroup_indices(mapping, taxa, occupied, gene_axis):
    indices = {}
    for t in taxa:
        genes = mapping[t]
        if (
            not isinstance(genes, list)
            or not genes
            or len(genes) != len(set(genes))
            or occupied[t].intersection(genes)
        ):
            raise ValueError("Invalid or overlapping orthogroup membership")
        occupied[t].update(genes)
        positions = gene_axis.get_indexer(genes)
        if min(positions) < 0:
            raise ValueError("Orthogroup feature absent from measured axis")
        indices[t] = positions
    return indices


def _aligned(data, groups, taxa, strict):
    """Select a single measured representative of each orthogroup per species."""
    if not isinstance(groups, dict) or not groups:
        raise ValueError("Ensembl orthogroups required")
    species = data.obs["species_taxon_id"].astype(str).to_numpy()
    chosen = {}
    occupied = {tax: set() for tax in taxa}
    pieces = {t: [] for t in taxa}
    means_by_species = {
        t: np.asarray(data.X[species == t].mean(axis=0)).ravel() for t in taxa
    }
    for group, mapping in sorted(groups.items()):
        if set(mapping) != set(taxa):
            raise ValueError("Orthogroup taxa do not match manifest")
        indices = _orthogroup_indices(mapping, taxa, occupied, data.var_names)
        if strict and any(len(v) != 1 for v in indices.values()):
            continue
        chosen[group] = {}
        for t, cols in indices.items():
            means = means_by_species[t][cols]
            j = np.lexsort((np.asarray(mapping[t]), -means))[0]
            chosen[group][t] = mapping[t][j]
            pieces[t].append(cols[j])
    if len(chosen) < 2:
        raise ValueError("Insufficient orthogroups for required comparison")
    aligned = np.empty((len(data), len(chosen)), dtype=np.float64)
    for t in taxa:
        block = data.X[species == t][:, pieces[t]]
        aligned[species == t] = block.toarray() if sparse.issparse(block) else block
    return AnnData(
        aligned, obs=data.obs.copy(), var=pd.DataFrame(index=list(chosen))
    ), chosen


def _samap_graph(data, paths, taxa):
    table = pd.read_csv(paths["samap_mapping_table"], sep="\t")
    if list(table.columns) != ["gene_a", "gene_b", "score"] or not len(table):
        raise ValueError("Invalid SAMap homology mapping table")
    genes = np.unique(table[["gene_a", "gene_b"]].to_numpy().ravel())
    names = {
        tax: np.asarray([g for g in genes if g.startswith(tax + "_")]) for tax in taxa
    }
    if any(len(a) == 0 for a in names.values()) or sum(map(len, names.values())) != len(
        genes
    ):
        raise ValueError("SAMap gene names must use declared taxon prefixes")
    observed_pairs = set()
    for a, b in table[["gene_a", "gene_b"]].itertuples(index=False, name=None):
        species_a, gene_a = a.split("_", 1)
        species_b, gene_b = b.split("_", 1)
        if species_a == species_b or not gene_a or not gene_b:
            raise ValueError("SAMap requires cross-species homology edges")
        pair = tuple(sorted((a, b)))
        if pair in observed_pairs:
            raise ValueError("Duplicate unordered SAMap homology edge")
        observed_pairs.add(pair)
        if gene_a not in data.var_names or gene_b not in data.var_names:
            raise ValueError("SAMap gene absent from measured feature axis")
    with paths["proteome_fasta"].open() as source:
        present = {
            line[1:].split()[0]
            for line in source
            if line.startswith(">") and line[1:].strip()
        }
    if set(genes) - present:
        raise ValueError("SAMap mapping genes absent from staged proteome identifiers")
    i, j = (np.searchsorted(genes, table[key]) for key in ("gene_a", "gene_b"))
    scores = table.score.to_numpy(float)
    if not np.isfinite(scores).all() or (scores <= 0).any():
        raise ValueError("Invalid SAMap homology weights")
    graph = sparse.csr_matrix((scores, (i, j)), shape=(len(genes), len(genes)))
    return graph, genes, names


def _samap(data, paths, taxa, opt):
    """Run SAMap from reciprocal, precomputed homology edges without BLAST downloads."""
    from samap import SAMAP
    from sklearn.manifold import SpectralEmbedding

    graph, genes, names = _samap_graph(data, paths, taxa)
    with TemporaryDirectory() as tmp:
        datasets = {}
        for t in taxa:
            raw_names = [g.split("_", 1)[1] for g in names[t]]
            dataset = data[data.obs.species_taxon_id.astype(str) == t, raw_names].copy()
            path = Path(tmp) / (t + ".h5ad")
            dataset.write_h5ad(path)
            datasets[t] = str(path)
        model = SAMAP(
            datasets,
            gnnm=(graph.maximum(graph.T), genes, names),
            save_processed=False,
            backend="cpu",
        )
        merged = model.run(ncpus=1, umap=False).adata
        if not merged.obs_names.is_unique or set(merged.obs_names) != set(
            data.obs_names
        ):
            raise ValueError("SAMap cell axis mismatch")
        graph = merged[data.obs_names].obsp["connectivities"]
        return SpectralEmbedding(
            n_components=min(opt.n_pcs, len(data) - 2),
            affinity="precomputed",
            random_state=opt.seed,
        ).fit_transform(graph)


def _cross_species(data, paths, opt, batch, labels, report, side):
    source = read_bounded_json(paths["homology"], 16 * 1024**2)
    manifest = CrossSpeciesHomologyManifest.model_validate(source["manifest"])
    manifest.verify(paths, batch)
    taxa = list(map(str, manifest.species_taxon_ids))
    close = opt.regime == "close_mammals"
    if manifest.homology_policy != ("ensembl_in_paralogs" if close else "samap"):
        raise ValueError("Homology policy differs from frozen regime")
    receipt = manifest.model_dump(exclude_none=True)
    report["CrossSpeciesHomologyManifest"] = side["CrossSpeciesHomologyManifest"] = (
        receipt
    )
    report["homology_source_sha256"] = source_digest(paths["homology"])
    if close:
        primary, chosen = _aligned(data, source["orthogroups"], taxa, False)
        comparison, strict = _aligned(data, source["orthogroups"], taxa, True)
        baseline = _embedding(comparison, opt)
        report["strict_1to1"] = {
            "representatives": strict,
            "benchmark": scib_benchmark(baseline, batch, labels, opt.seed),
        }
        report["representatives"] = chosen
        coords = _embedding(primary, opt)
        report["method"] = opt.method
    else:
        coords = _samap(data, paths, taxa, opt)
        report["method"] = "samap"
        if opt.comparator:
            saturn = CrossSpeciesHomologyManifest.model_validate(
                source["saturn_manifest"]
            )
            if (
                saturn.homology_policy != "saturn"
                or saturn.assembly_ids != manifest.assembly_ids
                or saturn.species_taxon_ids != manifest.species_taxon_ids
                or saturn.ensembl_release != manifest.ensembl_release
                or saturn.proteome_fasta_sha256 != manifest.proteome_fasta_sha256
            ):
                raise ValueError("SATURN comparator manifest mismatch")
            saturn.verify(paths, batch)
            from rna_kernels.scrna_saturn import offline_saturn

            alternate = offline_saturn(data, paths, taxa, opt)
            report["saturn"] = {
                "epistemic_purpose": "E4",
                "CrossSpeciesHomologyManifest": saturn.model_dump(exclude_none=True),
                "benchmark": scib_benchmark(alternate, batch, labels, opt.seed),
            }
    return coords


def run_m2(op, paths, params, outputs):
    opt = PARAMETERS[op].model_validate(params)
    data = _counts(paths["expression"])
    evidence = ExternalLabelEvidence(
        paths["labels"],
        paths["label_qualification"],
        source_digest(paths["label_qualification"]),
    )
    labels, qual = verify_external_labels(
        evidence,
        cell_ids=list(data.obs_names),
        selected_sha256=source_digest(paths["expression"]),
    )
    if opt.batch_key not in data.obs or data.obs[opt.batch_key].isna().any():
        raise ValueError("Observed batch identities required")
    batch = data.obs[opt.batch_key].astype(str).to_numpy()
    side = {"format": "devbench.integration-axes/1"}
    report = {
        "status": "completed",
        "representation_class": R4,
        "label_qualification": qual,
        "calibration_status": "uncalibrated_L1",
        "clinical_release_allowed": False,
    }
    if opt.training_label_key and np.array_equal(
        pd.factorize(data.obs[opt.training_label_key])[0], pd.factorize(labels)[0]
    ):
        raise ValueError("Scoring labels cannot be reused as training labels")
    if isinstance(opt, CrossSpeciesOptions):
        coords = _cross_species(data, paths, opt, batch, labels, report, side)
    else:
        if any(
            data.obs[k].nunique(dropna=False) != 1
            for k in ("species_taxon_id", "organism_taxonomy_id", "taxon_id", "species")
            if k in data.obs
        ):
            raise ValueError("Cross-species input requires ADR-28 homology manifest")
        coords = _embedding(data, opt)
        report["method"] = opt.method
    report["benchmark"] = scib_benchmark(coords, batch, labels, opt.seed)
    side["metrics"] = report["benchmark"]
    side.update(
        axis_labels={"x": "Integration metric", "y": "Score"},
        transformations=["Evaluate full cohort in latent space"],
        sample_count=data.n_obs,
        donor_count=int(data.obs["donor_id"].nunique())
        if "donor_id" in data.obs
        else 0,
        columns=["metric", "score"],
        rows=[
            [key, value]
            for key, value in report["benchmark"]["metrics"].items()
            if value is not None
        ],
    )

    for comparison in ("strict_1to1", "saturn"):
        if comparison in report:
            side[comparison] = {
                k: v for k, v in report[comparison].items() if k != "representatives"
            }
    path = outputs / "embedding.h5ad"
    embedded = AnnData(np.asarray(coords, dtype=np.float32), obs=data.obs.copy())
    embedded.uns["representation_class"] = R4
    if "CrossSpeciesHomologyManifest" in report:
        embedded.uns["CrossSpeciesHomologyManifest"] = report[
            "CrossSpeciesHomologyManifest"
        ]
    embedded.write_h5ad(path)
    files = {"embedding": path}
    for port, result in (("result", report), ("plot_sidecar", side)):
        files[port] = outputs / (port + ".json")
        files[port].write_text(json.dumps(result, allow_nan=False))
    return files
