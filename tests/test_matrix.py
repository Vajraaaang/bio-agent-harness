import json
from collections import Counter

import numpy as np
import pandas as pd
from jsonschema import validate


def test_full_matrix_is_distinct_stageable_and_keeps_truth_private(tmp_path):
    from benchmarks.matrix import build_matrix, stage_matrix_task
    from devharness_core.storage import ChunkedExpressionStore
    from devbench_engine.runner import evaluate_grader
    paths = build_matrix(tmp_path / "definitions")
    specs = [json.loads(path.read_text()) for path in paths]
    assert Counter(spec["metadata"]["track"] for spec in specs) == {
        "trajectory": 15, "batch": 15, "grn": 10, "adversarial": 15}
    assert len({spec["id"] for spec in specs}) == 55
    mechanisms = [{key: value for key, value in spec["dataset"]["parameters"].items()
                   if key != "scenario_identity"} for spec in specs]
    assert len({json.dumps(parameters, sort_keys=True) for parameters in mechanisms}) == 55
    for spec in specs:
        work = tmp_path / spec["id"]
        staged = stage_matrix_task(work, spec["dataset"])
        truth = staged["ground_truth"]
        validate(truth["reference_answer"], spec["answer_schema"])
        grade = evaluate_grader(truth["reference_answer"], spec["grader"], {"ground_truth": truth})
        assert grade["passed"], (spec["id"], grade)
        assert len(staged["truth_digest"]) == 64
        assert staged["metadata"]["source_kind"] == "synthetic"
        assert not any("truth" in path.name or "gold" in path.name for path in work.iterdir())
        with ChunkedExpressionStore(work / "counts.zarr") as store:
            assert 80 <= store.shape[0] <= 200
            assert 24 <= store.shape[1] <= 60
            assert np.isfinite(store.get_chunk(slice(None), slice(None))).all()
            assert set(truth["pseudotime"]) == set(store.sample_names)
            metadata = pd.read_csv(work / "metadata.csv")
            assert metadata.sample_id.tolist() == store.sample_names
        assert "ground_truth" not in (work / "dataset_provenance.json").read_text()


def test_same_seed_preserves_reproducibility_not_scenario_equivalence(tmp_path):
    from benchmarks.matrix import build_matrix, stage_matrix_task
    from devharness_core.storage import ChunkedExpressionStore
    specs = [json.loads(p.read_text()) for p in build_matrix(tmp_path / "specs")]
    a = stage_matrix_task(tmp_path / "a", specs[0]["dataset"])
    b = stage_matrix_task(tmp_path / "b", specs[0]["dataset"])
    assert a["truth_digest"] == b["truth_digest"]
    with ChunkedExpressionStore(tmp_path / "a/counts.zarr") as left, ChunkedExpressionStore(tmp_path / "b/counts.zarr") as right:
        np.testing.assert_array_equal(left.get_chunk(slice(None), slice(None)), right.get_chunk(slice(None), slice(None)))
    branch = next(s for s in specs if s["dataset"]["scenario"] == "bifurcation")
    changed = stage_matrix_task(tmp_path / "branch", branch["dataset"])
    assert changed["truth_digest"] != a["truth_digest"]
    assert len(changed["ground_truth"]["lineage_graph"]["nodes"]) > len(a["ground_truth"]["lineage_graph"]["nodes"])
    observed = pd.read_csv(tmp_path / "a/metadata.csv")
    latent = np.asarray([a["ground_truth"]["pseudotime"][sample] for sample in observed.sample_id])
    assert not np.array_equal(observed.normalized_time.to_numpy(), latent)
    assert observed.sample_id.tolist() != sorted(observed.sample_id)


def test_complete_confounding_and_known_negative_universe(tmp_path):
    from benchmarks.matrix import build_matrix, stage_matrix_task
    specs = [json.loads(p.read_text()) for p in build_matrix(tmp_path / "specs")]
    alias = next(s for s in specs if s["dataset"]["scenario"] == "complete_alias")
    truth = stage_matrix_task(tmp_path / "alias", alias["dataset"])["ground_truth"]
    assert truth["rank_deficient"] is True
    assert truth["causal_batch_adjusted_identifiable"] is False
    grn = next(s for s in specs if s["metadata"]["track"] == "grn")
    truth = stage_matrix_task(tmp_path / "network", grn["dataset"])["ground_truth"]
    positives = {(e["tf"], e["target"]) for e in truth["edges"]}
    universe = {(e["tf"], e["target"]) for e in truth["candidate_universe"]}
    negatives = {(e["tf"], e["target"]) for e in truth["known_negative_edges"]}
    assert positives < universe and negatives == universe - positives
