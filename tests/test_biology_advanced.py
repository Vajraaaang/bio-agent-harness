"""Small synthetic checks for geometry, association, and deterministic grading."""
import json

import numpy as np
import pandas as pd
import pytest

from devbench_engine import graders_bio as bio


def test_pseudotime_aligns_ids_handles_ties_and_permuted_branch_labels():
    assert hasattr(bio, "grade_pseudotime"), "pseudotime grader is required"
    expected = {"a": 0, "b": 1, "c": 1, "d": 2}
    actual = {"d": 8, "c": 4, "a": 0, "b": 4}
    passed, score, detail = bio.grade_pseudotime(
        actual, expected, actual_branches={"a": 7, "b": 7, "c": 3, "d": 3},
        expected_branches={"a": "left", "b": "left", "c": "right", "d": "right"})
    assert passed and score == pytest.approx(1)
    assert "ARI" in detail
    assert not bio.grade_pseudotime({"a": 0}, expected)[0]
    assert not bio.grade_pseudotime(dict.fromkeys(expected, 1), expected)[0]
    assert not bio.grade_pseudotime({**actual, "a": float("nan")}, expected)[0]
    assert not bio.grade_pseudotime({k: -v for k, v in expected.items()}, expected)[0]


def test_graph_edit_distance_preserves_direction_labels_and_rejects_cycles():
    assert hasattr(bio, "grade_lineage_graph"), "lineage graph grader is required"
    graph = {"nodes": ["root", "left", "right"], "edges": [["root", "left"], ["root", "right"]]}
    assert bio.grade_lineage_graph(graph, graph)[:2] == (True, 1.0)
    reverse = {**graph, "edges": [["left", "root"], ["root", "right"]]}
    passed, score, detail = bio.grade_lineage_graph(reverse, graph, threshold=1)
    assert not passed and 0 <= score < 1 and "label-preserving" in detail
    cycle = {**graph, "edges": graph["edges"] + [["left", "root"]]}
    assert not bio.grade_lineage_graph(cycle, graph)[0]
    assert not bio.grade_lineage_graph({"nodes": ["root"], "edges": [["root", "missing"]]}, graph)[0]


def test_exact_graph_edit_distance_and_deadline_are_not_approximate_success():
    assert hasattr(bio, "grade_lineage_graph"), "exact graph grader is required"
    graph = {"nodes": ["a", "b"], "edges": [["a", "b"]]}
    changed = {"nodes": ["a", "b"], "edges": []}
    passed, score, detail = bio.grade_lineage_graph(changed, graph, method="exact", threshold=.7)
    assert passed and score == pytest.approx(.8) and "exact" in detail
    passed, score, detail = bio.grade_lineage_graph(changed, graph, method="exact", timeout_sec=1e-6)
    assert not passed and score == 0 and "unable" in detail.lower()


def test_network_average_precision_uses_declared_negatives_and_signed_direction():
    assert hasattr(bio, "grade_network_precision_recall"), "network grader is required"
    universe = [{"tf": "A", "target": gene} for gene in ["B", "C", "D", "E"]]
    expected = universe[:2]
    actual = [dict(edge, score=score) for edge, score in zip(universe, [4, 2, 3, 1])]
    passed, score, detail = bio.grade_network_precision_recall(actual, expected, universe, threshold=.8)
    assert passed and score == pytest.approx(5 / 6)
    assert "average precision" in detail.lower()
    assert not bio.grade_network_precision_recall(actual, expected, expected)[0]
    assert not bio.grade_network_precision_recall(actual, expected, [universe[0]])[0]
    positive = {"tf": "A", "target": "B", "sign": 1}
    negative = {"tf": "A", "target": "B", "sign": -1}
    assert not bio.grade_network_precision_recall([dict(negative, score=1)], [positive],
                                                [positive, negative], signed=True, threshold=.9)[0]
    assert not bio.grade_network_precision_recall([dict(positive, score=float("inf"))], [positive],
                                                [positive, negative], signed=True)[0]


def test_effect_calibration_checks_values_not_only_direction():
    assert hasattr(bio, "grade_effect_calibration"), "effect calibration grader is required"
    expected = {"T": -2.0, "Other": .5}
    assert bio.grade_effect_calibration({"T": -2.1, "Other": .52}, expected)[0]
    assert not bio.grade_effect_calibration({"T": -20, "Other": 5}, expected)[0]
    assert not bio.grade_effect_calibration({"T": -2.0}, expected)[0]
    assert not bio.grade_effect_calibration({"T": float("nan"), "Other": .5}, expected)[0]


def test_graders_fail_closed_for_numeric_overflow():
    enormous = 10 ** 1000
    assert not bio.grade_pseudotime({"a": enormous, "b": 1}, {"a": 0, "b": 1})[0]
    assert not bio.grade_effect_calibration({"gene": enormous}, {"gene": 1})[0]
    universe = [{"tf": "A", "target": "B"}, {"tf": "A", "target": "C"}]
    assert not bio.grade_network_precision_recall([dict(universe[0], score=enormous)], universe[:1], universe)[0]


def test_metadata_provenance_hashes_bytes_actually_parsed(tmp_path, monkeypatch):
    import hashlib
    from devharness_server.tools_lineage import _bounded_metadata
    path = tmp_path / "metadata.csv"
    original = b"sample_id,embryonic_age\n001,E8.5\n002,E10.5\n"
    path.write_bytes(original)
    real_read_csv = pd.read_csv

    def read_then_change(*args, **kwargs):
        frame = real_read_csv(*args, **kwargs)
        path.write_text("sample_id,embryonic_age\n003,E12.5\n")
        return frame

    monkeypatch.setattr(pd, "read_csv", read_then_change)
    metadata = _bounded_metadata(str(path))
    assert metadata["sample_id"].tolist() == ["001", "002"]
    assert metadata.attrs["source_sha256"] == hashlib.sha256(original).hexdigest()


def branching_dataset(tmp_path):
    # A stem leading to two orthogonal arms; chronology is deliberately shuffled.
    stem = np.column_stack([np.linspace(0, 1, 12), np.zeros(12)])
    left = np.column_stack([1 + np.linspace(.1, 1, 15), np.linspace(.1, 1, 15)])
    right = np.column_stack([1 + np.linspace(.1, 1, 15), -np.linspace(.1, 1, 15)])
    geometry = np.vstack([stem, left, right])
    samples = [f"s{i:03}" for i in range(len(geometry))]
    values = np.vstack([geometry[:, 0], geometry[:, 1], geometry[:, 0] + geometry[:, 1],
                        geometry[:, 0] - geometry[:, 1], np.ones(len(geometry))])
    counts, meta = tmp_path / "expression.csv", tmp_path / "metadata.csv"
    pd.DataFrame(values, index=["g0", "g1", "g2", "g3", "constant"], columns=samples).to_csv(counts)
    pd.DataFrame({"sample_id": samples, "embryonic_age": np.random.default_rng(3).permutation(len(samples))}).to_csv(meta, index=False)
    return str(counts), str(meta), samples


def test_lineage_uses_expression_geometry_in_worker_and_is_bounded(tmp_path):
    from devharness_server.tools_lineage import infer_lineage
    from scipy.stats import spearmanr
    from sklearn.metrics import adjusted_rand_score
    counts, meta, samples = branching_dataset(tmp_path)
    result = infer_lineage(counts, meta, samples[0], max_cells=50, top_genes=4,
                           n_neighbors=4, n_clusters=7)
    assert len(result["pseudotime"]) == len(samples)
    assert result["pseudotime"][samples[0]] == 0
    assert spearmanr([result["pseudotime"][s] for s in samples[:27]],
                     np.r_[np.linspace(0, 1, 12), np.linspace(1.1, 2, 15)]).statistic > .8
    assert result["branch_assignments"][samples[26]] != result["branch_assignments"][samples[-1]]
    assert adjusted_rand_score(["stem"] * 12 + ["left"] * 15 + ["right"] * 15,
                               [result["branch_assignments"][sample] for sample in samples]) > .65
    assert result["provenance"]["selected_gene_count"] == 4
    assert "causal" in " ".join(result["limitations"]).lower()
    assert bio.grade_lineage_graph(result["lineage_graph"], result["lineage_graph"], require_tree=True)[0]
    json.dumps(result, allow_nan=False)
    subset = infer_lineage(counts, meta, samples[0], max_cells=20, top_genes=4, n_clusters=4)
    assert len(subset["pseudotime"]) == 20 and samples[0] in subset["pseudotime"]
    assert subset["provenance"]["subsampled"]


def test_lineage_geometry_is_independent_of_metadata_sampling_times(tmp_path):
    from devharness_server.tools_lineage import infer_lineage
    counts, meta, samples = branching_dataset(tmp_path)
    first = infer_lineage(counts, meta, samples[0], n_neighbors=4)
    frame = pd.read_csv(meta)
    frame["embryonic_age"] = frame["embryonic_age"][::-1].to_numpy()
    frame.to_csv(meta, index=False)
    second = infer_lineage(counts, meta, samples[0], n_neighbors=4)
    assert first["pseudotime"] == second["pseudotime"]
    assert first["provenance"]["metadata_sha256"] != second["provenance"]["metadata_sha256"]
    assert first["provenance"]["selected_expression_sha256"] == second["provenance"]["selected_expression_sha256"]


def test_lineage_rejects_degenerate_data_and_unknown_root(tmp_path):
    from devharness_server.tools_lineage import infer_lineage
    counts, meta, samples = branching_dataset(tmp_path)
    with pytest.raises(Exception, match="root"):
        infer_lineage(counts, meta, "unknown")
    data = pd.read_csv(counts, index_col=0)
    data.loc[:] = 1
    data.to_csv(counts)
    with pytest.raises(Exception, match="variable"):
        infer_lineage(counts, meta, samples[0])


def test_lagged_network_recovers_synthetic_association_and_reports_scope(tmp_path):
    from devharness_server.tools_network import infer_regulatory_network
    rng = np.random.default_rng(91)
    x, noise = rng.normal(size=(2, 30))
    target = np.r_[0, 3 * x[:-1]]
    samples = [f"s{i}" for i in range(len(x))]
    counts, meta = tmp_path / "counts.csv", tmp_path / "metadata.csv"
    pd.DataFrame([x, noise, target], index=["TF", "noise", "target"], columns=samples).to_csv(counts)
    pd.DataFrame({"sample_id": samples, "embryonic_age": [f"E{i}" for i in range(len(x))]}).to_csv(meta, index=False)
    result = infer_regulatory_network(str(counts), str(meta), ["TF", "noise"], ["target"], alpha=.1)
    assert result["edges"][0]["tf"] == "TF" and result["edges"][0]["sign"] == 1
    assert result["edges"][0]["score"] > result["edges"][1]["score"] * 5
    assert len(result["candidate_universe"]) == 2
    assert result["provenance"]["time_points"] == 30
    assert "associat" in result["provenance"]["interpretation"]
    assert "not" in " ".join(result["limitations"])
    json.dumps(result, allow_nan=False)


def test_network_refuses_missing_genes_and_nonfinite_inputs(tmp_path):
    from devharness_server.tools_network import infer_regulatory_network
    counts, meta, _ = branching_dataset(tmp_path)
    with pytest.raises(Exception, match="Unknown gene"):
        infer_regulatory_network(counts, meta, ["absent"], ["g0"])
    with pytest.raises(ValueError, match="alpha"):
        infer_regulatory_network(counts, meta, ["g0"], ["g1"], alpha=float("nan"))


def test_network_reports_rank_deficiency_without_infinite_json(tmp_path):
    from devharness_server.tools_network import infer_regulatory_network
    counts, meta, _ = branching_dataset(tmp_path)
    frame = pd.read_csv(counts, index_col=0)
    frame.loc["copy"] = frame.loc["g0"]
    frame.to_csv(counts)
    result = infer_regulatory_network(counts, meta, ["g0", "copy"], ["g1"])
    diagnostic = result["provenance"]["design_diagnostics"]["g1"]
    assert not diagnostic["is_full_rank"] and diagnostic["condition_number"] is None
    assert diagnostic["regularized"]
    json.dumps(result, allow_nan=False)


def test_regulon_enrichment_deduplicates_targets_and_conditions_on_background(tmp_path):
    from devharness_server.tools_network import regulon_enrichment
    reference = tmp_path / "regulons.tsv"
    reference.write_text("tf\ttarget\tsign\nA\tg1\t1\nA\tg1\t1\nA\tg2\t1\nA\toutside\t1\nB\tg4\t-1\n")
    background = [f"g{i}" for i in range(1, 11)]
    result = regulon_enrichment(["g1", "g2"], background, str(reference))
    hit = next(row for row in result["results"] if row["tf"] == "A")
    assert hit["p_value"] == pytest.approx(1 / 45)
    assert hit["overlap"] == 2 and hit["reference_size"] == 2
    assert hit["q_value"] == pytest.approx(2 / 45)
    assert result["provenance"]["excluded_reference_genes"] == 1
    assert len(result["provenance"]["reference_sha256"]) == 64
    with pytest.raises(ValueError, match="subset"):
        regulon_enrichment(["outside"], background, str(reference))


def test_motif_enrichment_uses_sequence_hits_not_regulatory_edges(tmp_path):
    from devharness_server.tools_network import motif_enrichment
    hits = tmp_path / "hits.tsv"
    hits.write_text("gene\tmotif\thit\ng1\tM1\t1\ng1\tM1\t1\ng2\tM1\ttrue\ng3\tM1\t0\ng4\tM1\t0\n"
                    "g1\tM2\t0\ng2\tM2\t0\ng3\tM2\t0\ng4\tM2\t1\n")
    result = motif_enrichment(["g1", "g2"], ["g1", "g2", "g3", "g4"], str(hits))
    first = next(row for row in result["results"] if row["motif"] == "M1")
    assert first["p_value"] == pytest.approx(1 / 6)
    assert first["overlap"] == 2
    assert "sequence" in result["provenance"]["evidence_type"]
    wrong = tmp_path / "wrong.tsv"
    wrong.write_text("tf\ttarget\nM1\tg1\n")
    with pytest.raises(ValueError, match="gene.*motif"):
        motif_enrichment(["g1"], ["g1", "g2"], str(wrong))
    with pytest.raises(Exception, match="scanned"):
        motif_enrichment(["g1"], ["g1", "g2", "unmeasured"], str(hits))
