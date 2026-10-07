import numpy as np
import pandas as pd
import pytest

# Protocol size: shrinking the transcriptome distorts composition (planted genes dominate CPM).
SIZE = {"n_genes": 8000}
FAMILIES = ["A", "B", "C", "D", "E", "F"]


@pytest.mark.parametrize("family", FAMILIES)
def test_same_seed_is_identical_and_different_seed_differs(family):
    from benchmarks.planted_discovery import make_case
    counts, metadata, truth = make_case(family, seed=7, **SIZE)
    again_counts, again_metadata, again_truth = make_case(family, seed=7, **SIZE)
    pd.testing.assert_frame_equal(counts, again_counts)
    pd.testing.assert_frame_equal(metadata, again_metadata)
    assert truth == again_truth
    other_counts, _, _ = make_case(family, seed=8, **SIZE)
    assert not counts.equals(other_counts)


VISIBLE_COLUMNS = {
    "A": {"sample_id", "condition"},
    "B": {"sample_id", "condition", "sex"},
    "C": {"sample_id", "condition", "batch"},
    "D": {"sample_id", "condition", "batch"},
    "E": {"sample_id", "condition"},
    "F": {"sample_id", "condition"},  # donor identity is deliberately hidden
}


@pytest.mark.parametrize("family", FAMILIES)
def test_agent_visible_metadata_exposes_only_declared_columns(family):
    from benchmarks.planted_discovery import make_case
    counts, metadata, _ = make_case(family, seed=7, **SIZE)
    assert set(metadata.columns) == VISIBLE_COLUMNS[family]
    assert list(metadata["sample_id"]) == list(counts.columns)
    assert counts.index.str.fullmatch(r"G\d{5}").all()
    assert metadata["sample_id"].str.fullmatch(r"S\d{2}").all()


def test_unknown_family_is_rejected():
    from benchmarks.planted_discovery import make_case
    with pytest.raises(ValueError):
        make_case("Z", seed=1, **SIZE)


def _log_cpm(counts):
    return np.log2(counts / counts.sum(axis=0) * 1e6 + 1)


def _mean_of(expression, metadata, **levels):
    mask = np.logical_and.reduce([metadata[column] == value for column, value in levels.items()])
    return expression[list(metadata.loc[mask, "sample_id"])].mean(axis=1)


def _signal(truth, kind):
    matches = [signal for signal in truth["signals"] if signal["type"] == kind]
    assert len(matches) == 1
    return matches[0]


def test_family_a_hides_a_treated_subgroup_expressing_a_module():
    from benchmarks.planted_discovery import make_case
    counts, metadata, truth = make_case("A", seed=7, **SIZE)
    signal = _signal(truth, "sample_subgroup")
    assert len(signal["genes"]) == 40 and len(signal["samples"]) == 4
    conditions = metadata.set_index("sample_id")["condition"]
    assert set(conditions[signal["samples"]]) == {"treated"}
    expression = _log_cpm(counts).loc[signal["genes"]]
    inside = expression[signal["samples"]].mean(axis=1)
    outside = expression.drop(columns=signal["samples"]).mean(axis=1)
    assert (inside - outside).median() > 1.0


def test_family_b_plants_opposite_stratum_log_ratios_with_zero_mean_log_ratio():
    from benchmarks.planted_discovery import make_case
    counts, metadata, truth = make_case("B", seed=7, **SIZE)
    signal = _signal(truth, "interaction")
    assert len(signal["genes"]) == 60 and signal["factor"] == "sex"
    expression = _log_cpm(counts).loc[signal["genes"]]
    female = (_mean_of(expression, metadata, condition="treated", sex="female")
              - _mean_of(expression, metadata, condition="control", sex="female"))
    male = (_mean_of(expression, metadata, condition="treated", sex="male")
            - _mean_of(expression, metadata, condition="control", sex="male"))
    marginal = _mean_of(expression, metadata, condition="treated") - _mean_of(expression, metadata, condition="control")
    assert female.median() > 1.0
    assert male.median() < -1.0
    assert abs(marginal.median()) < 0.4


def _within_batch_condition_difference(expression, metadata):
    return sum(_mean_of(expression, metadata, condition="treated", batch=batch)
               - _mean_of(expression, metadata, condition="control", batch=batch)
               for batch in ("batch_1", "batch_2")) / 2


def test_family_c_separates_a_real_condition_effect_from_a_batch_decoy():
    from benchmarks.planted_discovery import make_case
    counts, metadata, truth = make_case("C", seed=7, **SIZE)
    signal = _signal(truth, "gene_set_effect")
    assert len(signal["genes"]) == 50 and signal["contrast"] == "treated_vs_control"
    [decoy] = truth["decoys"]
    assert decoy["type"] == "batch_effect" and len(decoy["genes"]) == 200
    assert not set(signal["genes"]) & set(decoy["genes"])
    expression = _log_cpm(counts)
    real = expression.loc[signal["genes"]]
    assert _within_batch_condition_difference(real, metadata).median() > 1.0
    batch_genes = expression.loc[decoy["genes"]]
    marginal = (_mean_of(batch_genes, metadata, condition="treated")
                - _mean_of(batch_genes, metadata, condition="control"))
    assert marginal.median() > 0.4  # the decoy masquerades as a condition effect
    assert abs(_within_batch_condition_difference(batch_genes, metadata).median()) < 0.3


def test_family_d_confounds_batch_with_condition_and_expects_abstention():
    from benchmarks.planted_discovery import make_case
    counts, metadata, truth = make_case("D", seed=7, **SIZE)
    assert (metadata.groupby("condition")["batch"].nunique() == 1).all()
    assert metadata["batch"].nunique() == 2
    assert truth["signals"] == []
    assert truth["expected_design_issues"] == ["condition_batch_confounded"]
    [decoy] = truth["decoys"]
    assert decoy["type"] == "batch_effect" and len(decoy["genes"]) == 200
    shifted = _log_cpm(counts).loc[decoy["genes"]]
    apparent = _mean_of(shifted, metadata, condition="treated") - _mean_of(shifted, metadata, condition="control")
    assert apparent.median() > 1.0  # indistinguishable from a condition effect in these data


def _bh_discoveries(p_values, alpha=0.05):
    ordered = np.sort(np.asarray(p_values))
    passing = np.flatnonzero(ordered <= alpha * np.arange(1, len(ordered) + 1) / len(ordered))
    return 0 if passing.size == 0 else int(passing[-1] + 1)


def _condition_t_test(expression, metadata):
    from scipy.stats import ttest_ind
    treated = expression[list(metadata.loc[metadata["condition"] == "treated", "sample_id"])]
    control = expression[list(metadata.loc[metadata["condition"] == "control", "sample_id"])]
    return ttest_ind(treated, control, axis=1).pvalue


def test_development_f_oracle_ttest_detects_inflation_without_authenticating_identity():
    from benchmarks.planted_discovery import make_case
    counts, metadata, truth = make_case("F", seed=7, **SIZE)
    assert truth["signals"] == [] and truth["decoys"] == []
    assert truth["expected_design_issues"] == ["missing_independence_provenance"]
    donors = pd.Series(truth["donor_of_sample"])
    assert donors.value_counts().eq(3).all() and donors.nunique() == 8
    conditions = metadata.set_index("sample_id")["condition"]
    assert (conditions.groupby(donors).nunique() == 1).all()
    expression = _log_cpm(counts)
    expression = expression[expression.mean(axis=1) > 2]  # informative genes only
    naive = _bh_discoveries(_condition_t_test(expression, metadata))
    donor_means = expression.T.groupby(donors).mean().T
    donor_metadata = pd.DataFrame({"sample_id": donor_means.columns,
                                   "condition": conditions.groupby(donors).first()[donor_means.columns].to_numpy()})
    correct = _bh_discoveries(_condition_t_test(donor_means, donor_metadata))
    assert naive >= 50
    assert correct <= 5


def _discovered(expression, group_a, group_b, alpha=0.05):
    from scipy.stats import ttest_ind
    p_values = ttest_ind(expression[group_a], expression[group_b], axis=1).pvalue
    count = _bh_discoveries(p_values, alpha)
    return set(expression.index[np.argsort(p_values)[:count]])


def _samples(metadata, **levels):
    mask = np.logical_and.reduce([metadata[column] == value for column, value in levels.items()])
    return list(metadata.loc[mask, "sample_id"])


ORACLE_SEEDS = [1, 2, 3, 4, 5]


@pytest.mark.parametrize("seed", ORACLE_SEEDS)
def test_development_oracle_ttests_expose_bounded_fixture_signal(seed):
    """Known-membership/log-CPM oracle checks; neither discovery nor NB validity."""
    from benchmarks.planted_discovery import make_case

    def informative(counts):
        expression = _log_cpm(counts)
        return expression[expression.mean(axis=1) > 2]

    counts, metadata, truth = make_case("A", seed, **SIZE)
    module, members = set(_signal(truth, "sample_subgroup")["genes"]), _signal(truth, "sample_subgroup")["samples"]
    expression = informative(counts)
    found = _discovered(expression, members, [s for s in expression.columns if s not in members])
    assert len(found & module) / len(module) >= 0.9
    # BH at 5% with a 4-sample group admits a few ordinary false discoveries (0-6 seen over seeds 1-10).
    assert len(found & module) / len(found) >= 0.8

    counts, metadata, truth = make_case("B", seed, **SIZE)
    responding = set(_signal(truth, "interaction")["genes"])
    expression = informative(counts)
    found = _discovered(expression, _samples(metadata, condition="treated", sex="female"),
                        _samples(metadata, condition="control", sex="female"))
    assert len(found & responding) / len(responding) >= 0.9

    counts, metadata, truth = make_case("C", seed, **SIZE)
    real, decoy = set(_signal(truth, "gene_set_effect")["genes"]), set(truth["decoys"][0]["genes"])
    expression = informative(counts)
    batch_of = metadata.set_index("sample_id")["batch"]
    
    # Descriptive oracle smoke only; joint NB inference is tested in the baseline lane.
    adjusted = expression - expression.T.groupby(batch_of).transform("mean").T
    found = _discovered(adjusted, _samples(metadata, condition="treated"), _samples(metadata, condition="control"))
    assert len(found & real) / len(real) >= 0.9
    assert len(found & decoy) / len(decoy) <= 0.1

    counts, metadata, truth = make_case("E", seed, **SIZE)
    assert truth["signals"] == truth["decoys"] == truth["expected_design_issues"] == []
    expression = informative(counts)
    found = _discovered(expression, _samples(metadata, condition="treated"), _samples(metadata, condition="control"))
    assert len(found) <= 5


def test_stage_case_writes_agent_inputs_and_sealed_truth_separately(tmp_path):
    import hashlib
    import json
    from benchmarks.planted_discovery import make_case, stage_case
    from devharness_core.storage import ChunkedExpressionStore
    agent_dir, truth_dir = tmp_path / "agent", tmp_path / "evaluator"
    manifest = stage_case("C", 11, agent_dir, truth_dir, **SIZE)
    counts, metadata, truth = make_case("C", 11, **SIZE)
    assert sorted(path.name for path in agent_dir.iterdir()) == ["answer_schema.json", "counts.json", "counts.zarr", "input_manifest.json", "metadata.csv", "metadata.json"]
    pd.testing.assert_frame_equal(pd.read_csv(agent_dir / "metadata.csv"), metadata)
    with ChunkedExpressionStore(agent_dir / "counts.zarr") as store:
        assert store.gene_names == list(counts.index) and store.sample_names == list(counts.columns)
        for genes, values in store.iter_gene_chunks():
            np.testing.assert_array_equal(values, counts.loc[genes].to_numpy())
    truth_path = truth_dir / "truth.json"
    assert json.loads(truth_path.read_text()) == truth
    assert manifest["truth_sha256"] == hashlib.sha256(truth_path.read_bytes()).hexdigest()
    assert manifest == {"family": "C", "seed": 11, "agent_dir": str(agent_dir.resolve()),
                        "truth_path": str(truth_path.resolve()), "truth_sha256": manifest["truth_sha256"]}


@pytest.mark.parametrize("layout", ["same", "truth_inside_agent", "agent_inside_truth"])
def test_stage_case_refuses_overlapping_agent_and_truth_directories(tmp_path, layout):
    from benchmarks.planted_discovery import stage_case
    agent_dir, truth_dir = {
        "same": (tmp_path / "case", tmp_path / "case"),
        "truth_inside_agent": (tmp_path / "case", tmp_path / "case" / "hidden"),
        "agent_inside_truth": (tmp_path / "evaluator" / "case", tmp_path / "evaluator"),
    }[layout]
    with pytest.raises(ValueError, match="overlap"):
        stage_case("A", 1, agent_dir, truth_dir, **SIZE)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("seed", [-1, 2**32, 1.5, "7", True])
def test_generator_rejects_invalid_seed_before_staging(tmp_path, seed):
    from benchmarks.planted_discovery import stage_case
    with pytest.raises(ValueError, match="seed"):
        stage_case("A", seed, tmp_path / "agent", tmp_path / "truth", **SIZE)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("family,n_samples", [("A", 22), ("B", 22), ("C", 20), ("D", 23), ("E", 23), ("F", 22)])
def test_generator_rejects_unbalanced_or_indivisible_sample_sizes(family, n_samples):
    from benchmarks.planted_discovery import make_case
    with pytest.raises(ValueError, match="n_samples"):
        make_case(family, 7, n_samples=n_samples, **SIZE)


@pytest.mark.parametrize("family,n_genes", [("A", 50), ("B", 75), ("C", 300), ("D", 250)])
def test_generator_rejects_insufficient_gene_pool(family, n_genes):
    from benchmarks.planted_discovery import make_case
    with pytest.raises(ValueError, match="gene"):
        make_case(family, 7, n_genes=n_genes)


def test_generating_scale_truth_keeps_marginal_and_specific_estimands_distinct():
    from benchmarks.planted_discovery import make_case, STRONG_LOG2FC
    r = 2**STRONG_LOG2FC
    _, _, a = make_case("A", 7, **SIZE)
    _, _, b = make_case("B", 7, **SIZE)
    assert a["generating_summary"]["marginal_count_ratio"] == pytest.approx(1 + (r - 1) / 3)
    assert b["generating_summary"]["marginal_count_ratio"] == pytest.approx((r + 1/r) / 2)
    assert b["generating_summary"]["mean_sex_log2_ratio"] == 0
    assert b["generating_summary"]["male_minus_female_log2_interaction"] == -3
    assert _signal(b, "interaction")["log2fc_by_level"] == {"female": 1.5, "male": -1.5}


@pytest.mark.parametrize("family", list("ABCDE"))
def test_independent_sampling_is_a_public_synthetic_assumption(family):
    from benchmarks.planted_discovery import make_case
    counts, metadata, _ = make_case(family, 7, **SIZE)
    declaration = metadata.attrs["input_declaration"]["biological_independence"]
    assert declaration["status"] == "assumed_independent"
    assert declaration["source_kind"] == "assistant_fixture_declaration"
    assert declaration["unit"] == "specimen"
    assert metadata.attrs["input_declaration"]["metadata_roles"]["condition"] == "condition"
    assert len(counts.columns) == 24


def test_f_provenance_views_share_counts_but_known_view_declares_same_library_runs():
    from benchmarks.planted_discovery import make_case
    unknown, hidden, u_truth = make_case("F", 7, **SIZE)
    known, visible, k_truth = make_case("F", 7, provenance_view="known", **SIZE)
    pd.testing.assert_frame_equal(unknown, known)
    assert u_truth["donor_of_sample"] == k_truth["donor_of_sample"]
    assert set(hidden) == {"sample_id", "condition"}
    assert hidden.attrs["input_declaration"]["biological_independence"] is None
    assert set(visible) == {"sample_id", "condition", "donor_id", "library_id", "run_id"}
    assert visible["donor_id"].nunique() == 8
    assert visible.groupby("donor_id")["condition"].nunique().eq(1).all()
    assert visible.groupby("donor_id")["library_id"].nunique().eq(1).all()
    assert visible.groupby("library_id")["run_id"].nunique().eq(3).all()
    assert visible.attrs["input_declaration"]["biological_independence"]["status"] == "repeated_same_library"


@pytest.mark.parametrize("family,view", [("F", "invalid"), ("A", "known")])
def test_generator_rejects_inapplicable_provenance_views(family, view):
    from benchmarks.planted_discovery import make_case
    with pytest.raises(ValueError, match="provenance_view"):
        make_case(family, 7, provenance_view=view, **SIZE)


@pytest.mark.parametrize("side", ["agent", "truth"])
def test_stage_case_refuses_nonempty_destinations_and_preserves_bytes(tmp_path, side):
    from benchmarks.planted_discovery import stage_case
    occupied = tmp_path / side
    occupied.mkdir()
    sentinel = occupied / "keep.txt"
    sentinel.write_bytes(b"keep exactly")
    with pytest.raises(ValueError, match="empty"):
        stage_case("A", 7, tmp_path / "agent", tmp_path / "truth", **SIZE)
    assert sentinel.read_bytes() == b"keep exactly"
    assert not (tmp_path / ("truth" if side == "agent" else "agent")).exists()


@pytest.mark.parametrize("side", ["agent", "truth"])
def test_stage_case_refuses_symlink_components(tmp_path, side):
    from benchmarks.planted_discovery import stage_case
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    paths = {"agent": tmp_path / "agent", "truth": tmp_path / "truth"}
    paths[side] = link / side
    with pytest.raises(ValueError, match="symlink"):
        stage_case("A", 7, paths["agent"], paths["truth"], **SIZE)
    assert list(target.iterdir()) == []


def test_stage_case_public_json_and_manifest_match_counts_without_private_keys(tmp_path):
    import hashlib
    import json
    from benchmarks.planted_discovery import make_case, stage_case
    agent, private = tmp_path / "agent", tmp_path / "private"
    returned = stage_case("B", 7, agent, private, **SIZE)
    expected, metadata, _ = make_case("B", 7, **SIZE)
    matrix = json.loads((agent / "counts.json").read_text())
    rows = json.loads((agent / "metadata.json").read_text())
    public = json.loads((agent / "input_manifest.json").read_text())
    assert set(matrix) == {"gene_ids", "sample_ids", "matrix"}
    assert set(rows) == {"samples"}
    assert matrix["gene_ids"] == list(expected.index)
    assert matrix["sample_ids"] == list(expected.columns)
    np.testing.assert_array_equal(matrix["matrix"], expected.to_numpy())
    assert rows["samples"] == metadata.to_dict(orient="records")
    serialized = json.dumps(public)
    for forbidden in ["family", "seed", "truth_path", "truth_sha256", str(private)]:
        assert forbidden not in serialized
    assert public["counts"]["path"] == "counts.json"
    assert public["counts"]["sha256"] == hashlib.sha256((agent / "counts.json").read_bytes()).hexdigest()
    assert public["input_declaration"] == metadata.attrs["input_declaration"]
    assert returned["truth_path"] == str(private / "truth.json")
    published = __import__("pathlib").Path(__file__).parents[1] / "benchmarks/reference_data/discovery_claims.schema.json"
    assert (agent / "answer_schema.json").read_bytes() == published.read_bytes()
    assert public["answer_schema"]["sha256"] == hashlib.sha256(published.read_bytes()).hexdigest()


def test_stage_case_rolls_back_preparation_failure_and_keeps_existing_empty_dirs(tmp_path, monkeypatch):
    from benchmarks.planted_discovery import stage_case
    import benchmarks.planted_discovery as staging
    agent, private = tmp_path / "agent", tmp_path / "private"
    agent.mkdir(); private.mkdir()
    def fail(*args, **kwargs):
        raise RuntimeError("injected store failure")
    monkeypatch.setattr(staging, "_write_zarr", fail)
    with pytest.raises(RuntimeError, match="injected"):
        stage_case("A", 7, agent, private, **SIZE)
    assert agent.is_dir() and private.is_dir()
    assert list(agent.iterdir()) == list(private.iterdir()) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["agent", "private"]


def test_stage_case_rolls_back_partial_store_write(tmp_path, monkeypatch):
    from benchmarks.planted_discovery import stage_case
    import benchmarks.planted_discovery as staging
    def partial_write(counts, path):
        path.mkdir(parents=True)
        (path / "partial").write_text("partial write")
        raise RuntimeError("partial preparation failure")
    monkeypatch.setattr(staging, "_write_zarr", partial_write)
    with pytest.raises(RuntimeError, match="partial preparation"):
        stage_case("A", 7, tmp_path / "agent", tmp_path / "private", **SIZE)
    # Uncaptured store bytes have no ownership evidence; preserve the private residue.
    assert not (tmp_path / "agent").exists() and not (tmp_path / "private").exists()
    residue = list(tmp_path.iterdir())
    assert len(residue) == 1 and residue[0].name.startswith(".devbench-stage-")
    assert (residue[0].stat().st_mode & 0o777) == 0o700
    assert (residue[0] / "counts.zarr" / "partial").read_text() == "partial write"


def test_stage_case_concurrent_destination_mutation_fails_closed(tmp_path, monkeypatch):
    from benchmarks.planted_discovery import stage_case
    import benchmarks.planted_discovery as staging
    original = staging._write_zarr
    agent = tmp_path / "agent"
    agent.mkdir()
    def competing_write(counts, path):
        (agent / "other-writer.txt").write_bytes(b"other writer owns this")
        return original(counts, path)
    monkeypatch.setattr(staging, "_write_zarr", competing_write)
    with pytest.raises(ValueError, match="changed|empty|race"):
        stage_case("A", 7, agent, tmp_path / "private", **SIZE)
    assert (agent / "other-writer.txt").read_bytes() == b"other writer owns this"
    assert sorted(p.name for p in agent.iterdir()) == ["other-writer.txt"]
    assert not (tmp_path / "private").exists()


def test_staged_matrix_ids_sample_order_and_all_artifact_hashes_are_bound(tmp_path):
    import hashlib
    import json
    from benchmarks.planted_discovery import stage_case
    agent, private = tmp_path / "agent", tmp_path / "private"
    stage_case("F", 7, agent, private, provenance_view="known", **SIZE)
    public = json.loads((agent / "input_manifest.json").read_text())
    counts = json.loads((agent / "counts.json").read_text())
    rows = json.loads((agent / "metadata.json").read_text())["samples"]
    assert counts["sample_ids"] == [row["sample_id"] for row in rows]
    for key in ("counts", "metadata", "metadata_csv", "answer_schema"):
        artifact = public[key]
        assert artifact["sha256"] == hashlib.sha256((agent / artifact["path"]).read_bytes()).hexdigest()
    zarr_hashes = public["counts_zarr"]["files"]
    actual_files = [p for p in (agent / "counts.zarr").rglob("*") if p.is_file()]
    assert set(zarr_hashes) == {str(p.relative_to(agent / "counts.zarr")) for p in actual_files}
    for path in actual_files:
        assert zarr_hashes[str(path.relative_to(agent / "counts.zarr"))] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_c_and_d_have_distinct_condition_batch_estimability():
    from benchmarks.planted_discovery import make_case
    for family, expected_rank in [("C", 3), ("D", 2)]:
        _, metadata, _ = make_case(family, 7, **SIZE)
        design = np.column_stack([np.ones(len(metadata)), metadata["batch"].eq("batch_2"), metadata["condition"].eq("treated")]).astype(float)
        assert np.linalg.matrix_rank(design) == expected_rank
        if family == "C":
            assert metadata.groupby(["condition", "batch"]).size().min() >= 3


def test_b_contrast_geometry_has_both_strata_and_opposite_directions():
    from benchmarks.planted_discovery import make_case
    _, metadata, truth = make_case("B", 7, **SIZE)
    assert metadata.groupby(["condition", "sex"]).size().eq(6).all()
    treated = metadata["condition"].eq("treated").to_numpy()
    male = metadata["sex"].eq("male").to_numpy()
    design = np.column_stack([np.ones(24), treated, male, treated & male]).astype(float)
    assert np.linalg.matrix_rank(design) == 4
    beta = np.array([0, 1.5, 0, -3])
    assert np.dot([0, 1, 0, 0], beta) == 1.5
    assert np.dot([0, 1, 0, 1], beta) == -1.5
    assert np.dot([0, 0, 0, 1], beta) == -3
    assert _signal(truth, "interaction")["log2fc_by_level"] == {"female": 1.5, "male": -1.5}


@pytest.mark.parametrize("family", list("ABCDE"))
def test_nb_dispersion_is_gene_wise_shared_across_all_samples(family, monkeypatch):
    from benchmarks.planted_discovery import make_case
    actual = np.random.default_rng([7, list("ABCDEF").index(family)])
    shapes = []
    class CaptureRng:
        def gamma(self, shape, scale):
            shapes.append(np.broadcast_to(shape, np.shape(scale)).copy())
            return actual.gamma(shape, scale)
        def __getattr__(self, name):
            return getattr(actual, name)
    monkeypatch.setattr(np.random, "default_rng", lambda *args, **kwargs: CaptureRng())
    make_case(family, 7, **SIZE)
    assert len(shapes) == 1
    assert np.ptp(shapes[0], axis=1).max() == 0


def test_stage_case_rejects_concurrent_mutation_during_publication(tmp_path, monkeypatch):
    import os
    from benchmarks.planted_discovery import stage_case
    agent, private = tmp_path / "agent", tmp_path / "private"
    agent.mkdir()
    link = os.link
    def publish_then_compete(source, destination, *args, **kwargs):
        result = link(source, destination, *args, **kwargs)
        (agent / "competing.txt").write_bytes(b"foreign writer")
        return result
    monkeypatch.setattr(os, "link", publish_then_compete)
    with pytest.raises(ValueError, match="changed|race"):
        stage_case("A", 7, agent, private, **SIZE)
    assert sorted(p.name for p in agent.iterdir()) == ["competing.txt"]
    assert not private.exists()


@pytest.mark.parametrize("mutation", ["flat", "zarr_foreign", "rollback_foreign", "ancestor_symlink"])
def test_stage_case_rejects_content_tree_and_ancestor_races_without_foreign_loss(tmp_path, monkeypatch, mutation):
    import os
    from pathlib import Path
    from benchmarks.planted_discovery import stage_case
    parent = tmp_path / "safe"
    parent.mkdir()
    agent, private = parent / "agent", parent / "private"
    original_link, original_rename = os.link, os.rename
    injected = False
    def mutate_link(source, target, *args, **kwargs):
        nonlocal injected
        result = original_link(source, target, *args, **kwargs)
        target = Path(target)
        if mutation == "ancestor_symlink" and target.parent == private and not injected:
            original_rename(parent, tmp_path / "moved")
            parent.symlink_to(tmp_path / "moved", target_is_directory=True)
            injected = True
        elif mutation == "flat" and target == agent / "counts.json":
            target.write_bytes(b'{"foreign":"modified counts"}\n')
        elif mutation == "rollback_foreign" and target == agent / "input_manifest.json":
            (agent / "counts.zarr" / "foreign.txt").write_bytes(b"foreign nested bytes")
            (agent / "competing.txt").write_bytes(b"foreign root bytes")
        return result
    def mutate_rename(source, target, *args, **kwargs):
        nonlocal injected
        result = original_rename(source, target, *args, **kwargs)
        if mutation == "zarr_foreign" and Path(target).parent == agent / "counts.zarr" and not injected:
            (agent / "counts.zarr" / "foreign.txt").write_bytes(b"foreign nested bytes")
            injected = True
        return result
    monkeypatch.setattr(os, "link", mutate_link)
    monkeypatch.setattr(os, "rename", mutate_rename)
    with pytest.raises(ValueError, match="changed|race|symlink"):
        stage_case("A", 7, agent, private, **SIZE)
    if mutation == "flat":
        assert (agent / "counts.json").read_bytes() == b'{"foreign":"modified counts"}\n'
    if mutation in {"zarr_foreign", "rollback_foreign"}:
        assert (agent / "counts.zarr" / "foreign.txt").read_bytes() == b"foreign nested bytes"
    if mutation == "rollback_foreign":
        assert (agent / "competing.txt").read_bytes() == b"foreign root bytes"
    if mutation == "ancestor_symlink":
        assert parent.is_symlink()


def test_stage_case_cleanup_preserves_foreign_temporary_sibling_entries(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    from benchmarks.planted_discovery import stage_case
    link = os.link
    foreign = []
    def mutate_temporary(source, target, *args, **kwargs):
        result = link(source, target, *args, **kwargs)
        if Path(target).name == "counts.json":
            path = Path(source).parent / "foreign.tmp"
            path.write_bytes(b"foreign temporary bytes")
            foreign.append(path)
        return result
    monkeypatch.setattr(os, "link", mutate_temporary)
    stage_case("A", 7, tmp_path / "agent", tmp_path / "private", **SIZE)
    assert len(foreign) == 1 and foreign[0].read_bytes() == b"foreign temporary bytes"


def test_known_f_declaration_explicitly_names_same_physical_library_resequencing():
    from benchmarks.planted_discovery import make_case
    _, metadata, _ = make_case("F", 7, provenance_view="known", **SIZE)
    declaration = metadata.attrs["input_declaration"]["biological_independence"]
    assert declaration["relation"] == "sequencing_runs_of_same_physical_library"
    assert declaration["biological_unit_column"] == "donor_id"
    assert declaration["library_column"] == "library_id" and declaration["run_column"] == "run_id"
    assert not metadata[["library_id", "run_id"]].duplicated().any()


@pytest.mark.parametrize("artifact", ["counts.json", "metadata.json", "metadata.csv", "answer_schema.json", "zarr", "zarr_extra"])
def test_stage_case_rejects_prepared_mutations_after_manifest_hashes(tmp_path, monkeypatch, artifact):
    import json
    from pathlib import Path
    import benchmarks.planted_discovery as staging
    original_write = staging._write_json
    def mutate_after_manifest(path, payload):
        result = original_write(path, payload)
        if Path(path).name == "input_manifest.json":
            root = Path(path).parent
            if artifact == "counts.json":
                matrix = json.loads((root / artifact).read_text())
                matrix["matrix"][0][0] += 1
                (root / artifact).write_text(json.dumps(matrix))
            elif artifact == "zarr":
                target = root / "counts.zarr" / ".zgroup"
                target.write_bytes(target.read_bytes() + b" ")
            elif artifact == "zarr_extra":
                (root / "counts.zarr" / "foreign.txt").write_bytes(b"foreign prepared bytes")
            else:
                target = root / artifact
                target.write_bytes(target.read_bytes() + b" ")
        return result
    monkeypatch.setattr(staging, "_write_json", mutate_after_manifest)
    with pytest.raises(ValueError, match="manifest|changed|hash"):
        staging.stage_case("A", 7, tmp_path / "agent", tmp_path / "private", **SIZE)
    assert not (tmp_path / "agent" / "input_manifest.json").exists()
    assert not (tmp_path / "private" / "truth.json").exists()


@pytest.mark.parametrize("mutation", ["gene_names", "sample_names", "codec_expression", "codec_gene_names", "codec_sample_names"])
def test_stage_case_rejects_persisted_axes_and_unknown_codec_before_decode(tmp_path, monkeypatch, mutation):
    import json
    import zarr
    import benchmarks.planted_discovery as staging
    from benchmarks.planted_discovery import stage_case
    original = staging._write_zarr
    def corrupt_after_write(counts, path):
        store = original(counts, path)
        if mutation.startswith("codec_"):
            header = path / mutation.removeprefix("codec_") / ".zarray"
            payload = json.loads(header.read_text())
            payload["compressor"] = {"id": "inert_unknown_audit_codec"}
            header.write_text(json.dumps(payload))
            import numcodecs
            def forbid_decoder_construction(*args, **kwargs):
                raise AssertionError("Codec construction preceded persisted-header validation")
            monkeypatch.setattr(numcodecs, "Blosc", forbid_decoder_construction)
        else:
            array = zarr.open_group(str(path), mode="a")[mutation]
            values = array[:]
            values[[0, 1]] = values[[1, 0]]
            array[:] = values
        return store
    monkeypatch.setattr(staging, "_write_zarr", corrupt_after_write)
    with pytest.raises(ValueError, match="axes|header|codec|changed|values"):
        stage_case("E", 7, tmp_path / "agent", tmp_path / "private", n_genes=20)


def test_stage_case_rejects_truth_mutation_after_lock_cleanup_without_adopting_new_seal(tmp_path, monkeypatch):
    from pathlib import Path
    from benchmarks.planted_discovery import stage_case
    private = tmp_path / "private"
    unlink = Path.unlink
    def alter_truth_after_unlock(path, *args, **kwargs):
        result = unlink(path, *args, **kwargs)
        if path.name == ".stage.lock" and (private / "truth.json").exists():
            (private / "truth.json").write_bytes(b'{"foreign":true}\n')
        return result
    monkeypatch.setattr(Path, "unlink", alter_truth_after_unlock)
    with pytest.raises(ValueError, match="changed|hash|commit"):
        stage_case("E", 7, tmp_path / "agent", private, n_genes=20)
    assert (private / "truth.json").read_bytes() == b'{"foreign":true}\n'


@pytest.mark.parametrize("attempt", range(4))
def test_stage_case_supported_large_axis_boundary_publishes_frozen_compressed_buffers(tmp_path, attempt):
    import json
    from benchmarks.planted_discovery import stage_case
    agent, private = tmp_path / "agent", tmp_path / "private"
    returned = stage_case("E", 2**32 - 1, agent, private, n_genes=100_000, n_samples=4)
    assert (agent / "input_manifest.json").is_file()
    public = json.loads((agent / "input_manifest.json").read_text())
    assert public["input_declaration"]["representation"] == "raw_counts"
    assert returned["seed"] == 2**32 - 1
