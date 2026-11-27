import json

import anndata as ad
import numpy as np
import pandas as pd
import pytest

from devbench.analysis.checkpoints import digest
from devbench.analysis.integration_metrics import scib_benchmark
from rna_kernels.m2_contract import source_digest
from rna_kernels.scrna_cross_species import (
    PRIMARY,
    R4,
    CrossSpeciesHomologyManifest,
    CrossSpeciesOptions,
    run_m2,
)


def fixtures(folder):
    n = 48
    rng = np.random.default_rng(49)
    taxa = np.resize([9606, 10090], n)
    labels = np.resize(["a", "a", "b", "b"], n)
    counts = rng.poisson(4, (n, 8))
    counts[labels == "a", 0] += 10
    counts[labels == "b", 1] += 10
    ids = list(map(str, range(n)))
    data = ad.AnnData(
        counts, obs=pd.DataFrame({"species_taxon_id": taxa, "batch": taxa}, index=ids)
    )
    paths = {
        name: folder / (name + suffix)
        for name, suffix in (
            ("expression", ".h5ad"),
            ("labels", ".json"),
            ("label_qualification", ".json"),
            ("homology", ".json"),
        )
    }
    data.write_h5ad(paths["expression"])
    paths["labels"].write_text(json.dumps(dict(zip(ids, labels, strict=True))))
    paths["label_qualification"].write_text(
        json.dumps(
            {
                "actor": "reviewer",
                "rationale": "Frozen orthogonal labels",
                "label_origin": "orthogonal_measurement",
                "source_sha256": "1" * 64,
                "format": "devbench.integration-label-verification/1",
                "labels_sha256": source_digest(paths["labels"]),
                "selected_sha256": source_digest(paths["expression"]),
                "cell_axis_sha256": digest(ids),
            }
        )
    )
    refs = {
        "manifest": {
            "homology_policy": "ensembl_in_paralogs",
            "species_taxon_ids": [9606, 10090],
            "assembly_ids": ["GRCh38", "GRCm39"],
            "ensembl_release": "112",
            "paralog_collapse_rule": PRIMARY,
        },
        "orthogroups": {
            "a": {"9606": ["0"], "10090": ["1"]},
            "b": {"9606": ["2"], "10090": ["3"]},
            "c": {"9606": ["4"], "10090": ["5", "6"]},
        },
    }
    paths["homology"].write_text(json.dumps(refs))
    return paths, refs


def test_close_manifest_comparator_and_sidecar(tmp_path):
    from devbench.mcp.plot_sidecars import verify_plot_sidecar

    paths, refs = fixtures(tmp_path)
    files = run_m2(
        "scrna_cross_species_integrate", paths, {"regime": "close_mammals"}, tmp_path
    )
    receipt = json.loads(files["result"].read_text())
    embedded = ad.read_h5ad(files["embedding"])
    side = json.loads(files["plot_sidecar"].read_text())
    verify_plot_sidecar(files["plot_sidecar"], "json")
    assert set(receipt["strict_1to1"]["representatives"]) == {"a", "b"}
    assert set(receipt["representatives"]) == {"a", "b", "c"}
    for obj in (receipt, embedded.uns, side):
        assert obj["CrossSpeciesHomologyManifest"]["paralog_collapse_rule"] == PRIMARY
    assert embedded.uns["representation_class"] == receipt["representation_class"] == R4
    for report in (receipt, receipt["strict_1to1"]):
        assert all(
            np.isfinite(report["benchmark"][k])
            for k in ("bio_conservation", "batch_correction")
        )
    with pytest.raises(ValueError, match="Cross-species"):
        run_m2("scrna_integrate_qualified", paths, {}, tmp_path)


def test_reference_uses_all_cells_dual_axes_and_real_labels():
    rng = np.random.default_rng(73)
    x = rng.normal(size=(516, 4))
    batch = np.resize(["d", "e"], len(x))
    labels = np.resize(["a", "a", "b", "b"], len(x))
    x[labels == "a", 0] += 10
    measured = scib_benchmark(x, batch, labels)
    assert measured["cells_evaluated"] == 516
    assert measured["metrics"]["silhouette_label"] > 0.8
    assert measured["metrics"]["ari"] > 0.9
    assert set(measured["axis_metrics"]) == {"bio_conservation", "batch_correction"}


def test_offline_sha_and_policy_checks(tmp_path):
    paths, refs = fixtures(tmp_path)
    manifest = dict(
        refs["manifest"],
        homology_policy="samap",
        paralog_collapse_rule="homology_graph",
    )
    with pytest.raises(ValueError, match="SAMap mapping"):
        CrossSpeciesHomologyManifest(**manifest)
    manifest.update(
        samap_mapping_table_sha256="0" * 64,
        proteome_fasta_sha256="a" * 64,
        blast_params={"evalue": 1e-6},
    )
    with pytest.raises(ValueError, match="offline reference"):
        CrossSpeciesHomologyManifest(**manifest).verify(paths, ["9606", "10090"])
    with pytest.raises(ValueError, match="SATURN requires"):
        CrossSpeciesOptions(regime="close_mammals", comparator="saturn")
    with pytest.raises(ValueError, match="weights"):
        CrossSpeciesHomologyManifest(
            **dict(
                refs["manifest"],
                homology_policy="saturn",
                paralog_collapse_rule="macrogenes",
            )
        )


def test_hash_verification_precedes_distant_solver_and_rejects_mismatched_weights(
    tmp_path,
):
    paths, refs = fixtures(tmp_path)
    metadata = dict(
        refs["manifest"], homology_policy="saturn", paralog_collapse_rule="macrogenes"
    )
    for key in ("proteome_fasta", "saturn_model_weights", "macrogene_map"):
        path = tmp_path / key
        path.write_bytes(("synthetic " + key).encode())
        paths[key] = path
        metadata[key + "_sha256"] = source_digest(path)
    record = CrossSpeciesHomologyManifest.model_validate(metadata)
    record.verify(paths, ["9606", "10090"])
    paths["saturn_model_weights"].write_bytes(b"changed")
    with pytest.raises(ValueError, match="mismatched offline reference"):
        record.verify(paths, ["9606", "10090"])
    with pytest.raises(ValueError, match="species differ"):
        record.verify(paths, ["9606", "10091"])


def test_missing_comparator_is_scientific_refusal(tmp_path):
    paths, refs = fixtures(tmp_path)
    for mapping in refs["orthogroups"].values():
        mapping["9606"].append("7")
    paths["homology"].write_text(json.dumps(refs))
    with pytest.raises(ValueError, match="overlapping|Insufficient orthogroups"):
        run_m2(
            "scrna_cross_species_integrate",
            paths,
            {"regime": "close_mammals"},
            tmp_path,
        )
    assert not (tmp_path / "result.json").exists()


def test_samap_edges_must_connect_measured_genes_and_staged_proteomes(tmp_path):
    from rna_kernels.scrna_cross_species import _samap_graph

    paths, _ = fixtures(tmp_path)
    data = ad.read_h5ad(paths["expression"])
    table = paths["samap_mapping_table"] = tmp_path / "edges.tsv"
    fasta = paths["proteome_fasta"] = tmp_path / "proteins.fa"
    taxa = ["9606", "10090"]
    fasta.write_text(">9606_0\nS\n>10090_1\nM\n")
    header = "gene_a\tgene_b\tscore\n"
    table.write_text(header + "9606_0\t10090_1\t2\n")
    graph, names, _ = _samap_graph(data, paths, taxa)
    assert tuple(names) == ("10090_1", "9606_0") and graph.nnz == 1
    table.write_text(header + "9606_0\t10090_7\t2\n")
    with pytest.raises(ValueError, match="staged proteome"):
        _samap_graph(data, paths, taxa)
    table.write_text(header + "9606_0\t10090_1\t2\n9606_0\t9606_1\t2\n")
    with pytest.raises(ValueError, match="cross-species homology edges"):
        _samap_graph(data, paths, taxa)


def test_saturn_unsorted_taxa_preserve_barcode_species(tmp_path):
    from rna_kernels.scrna_saturn import _recover_coordinates, _stage_species

    source = ad.AnnData(
        np.ones((4, 2)),
        obs=pd.DataFrame(
            {"species_taxon_id": [9606, 10090, 10090, 9606]},
            index=["cell4", "cell2", "cell3", "cell1"],
        ),
        var=pd.DataFrame(index=["a", "b"]),
    )
    taxa = ["9606", "10090"]
    _stage_species(
        tmp_path,
        source,
        taxa,
        {t: {"a": 0, "b": 1} for t in taxa},
        lambda values, path: path.write_text(json.dumps(values)),
    )
    staged = pd.read_csv(tmp_path / "input.csv")
    assert list(staged.species) == ["tax10090", "tax9606"]
    barcodes = [c for path in staged.path for c in ad.read_h5ad(path).obs_names]
    species = [
        name
        for name, path in zip(staged.species, staged.path, strict=True)
        for _ in ad.read_h5ad(path).obs_names
    ]
    result = ad.AnnData(
        np.arange(4)[:, None], obs=pd.DataFrame({"species": species}, index=barcodes)
    )
    np.testing.assert_array_equal(
        _recover_coordinates(source, result)[:, 0], [2, 0, 1, 3]
    )
    result.obs["species"] = species[::-1]
    with pytest.raises(ValueError, match="species mismatch"):
        _recover_coordinates(source, result)
