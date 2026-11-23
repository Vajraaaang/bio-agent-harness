import hashlib

import numpy as np
import pandas as pd
import pytest


def _perturbation_inputs(root, *, planted=True, donors=6, control="NTC"):
    from devbench.core.perturbation_analysis import PerturbationSpec

    rng = np.random.default_rng(71)
    cells_per_arm = 22
    size = donors * 2 * cells_per_arm
    assignments = np.tile(np.repeat(["NTC", "guide-target"], cells_per_arm), donors)
    response = (assignments == "guide-target") & (rng.random(size) > 0.25)
    mean = np.full((size, 40), 22.0)
    if planted:
        mean[response, 0] = 1
        mean[response, 1:4] = 160
    counts = rng.poisson(mean)
    ids = [f"cell-{i}" for i in range(size)]
    data = pd.DataFrame(counts, columns=[f"gene-{i}" for i in range(counts.shape[1])])
    data["cell_id"] = ids
    metadata = pd.DataFrame(
        {
            "cell_id": ids,
            "perturbation": assignments,
            "donor_id": np.repeat([f"d{i}" for i in range(donors)], 2 * cells_per_arm),
            "batch": np.repeat([f"b{i % 2}" for i in range(donors)], 2 * cells_per_arm),
        }
    )
    paths = [root / name for name in ("counts.csv", "metadata.csv")]
    for path, frame in zip(paths, (data, metadata), strict=True):
        frame.to_csv(path, index=False)
    return (
        paths,
        PerturbationSpec(
            counts_sha256=hashlib.sha256(paths[0].read_bytes()).hexdigest(),
            metadata_sha256=hashlib.sha256(paths[1].read_bytes()).hexdigest(),
            non_targeting_control=control,
            target_genes=(("guide-target", "gene-0"),),
            pca_components=6,
            n_neighbors=5,
            n_perm=99,
            seed=27,
        ),
        response,
        assignments,
    )


def test_planted_energy_efficacy_and_pseudobulk_de(tmp_path):
    from devbench.core.perturbation_analysis import fit_perturbation_response

    paths, spec, response, assignments = _perturbation_inputs(tmp_path)
    receipt = fit_perturbation_response(*paths, spec)
    (result,) = receipt["perturbations"]
    energy = result["energy_test"]
    assert energy["distance"] > 0.5
    assert 0 < energy["p_value"] <= energy["q_value"] <= 0.05
    efficacy = result["efficacy"]
    probs = np.array([row["probability"] for row in efficacy["cells"]])
    responding = response[assignments != "NTC"]
    assert probs[responding].mean() > 0.9
    assert probs[~responding].mean() < 0.2
    assert efficacy["target_gene_check"]["log1p_cpm10k_difference"] < -1
    de = result["pseudobulk_de"]
    assert de["design_diagnostics"]["paired"]
    assert de["design_diagnostics"]["biological_units_per_group"] == {
        "NTC": 6,
        "guide-target": 6,
    }
    genes = {row["gene_id"]: row for row in de["gene_statistics"]}
    assert genes["gene-0"]["log2_fold_change"] < -1
    assert 0 < genes["gene-0"]["p_value"] <= genes["gene-0"]["q_value"] < 0.05
    assert genes["gene-1"]["log2_fold_change"] > 1
    assert genes["gene-1"]["q_value"] < 0.05
    assert receipt["count1_unchanged"] and not receipt["clinical_release_allowed"]


def test_null_perturbation(tmp_path):
    from devbench.core.perturbation_analysis import fit_perturbation_response

    paths, spec, _, _ = _perturbation_inputs(tmp_path, planted=False)
    (result,) = fit_perturbation_response(*paths, spec)["perturbations"]
    assert result["energy_test"]["q_value"] > 0.05
    assert all(
        row["q_value"] is None or row["q_value"] > 0.05
        for row in result["pseudobulk_de"]["gene_statistics"]
    )


def test_chunked_energy_is_unbiased_u_statistic():
    from devbench.core.perturbation_analysis import _energy_test

    scores = np.array([[0.0], [1.0], [4.0], [5.0]])
    mask = np.array([False, False, True, True])
    result = _energy_test(scores, mask, [np.arange(4)], n_perm=19, seed=3)
    assert result["distance"] == pytest.approx(6.0)
    assert result["p_value"] >= 1 / 20


def test_refuses_missing_or_absent_non_targeting_control(tmp_path):
    from devbench.core.perturbation_analysis import fit_perturbation_response

    paths, spec, _, _ = _perturbation_inputs(tmp_path, control="unobserved")
    with pytest.raises(ValueError, match="non_targeting_control"):
        fit_perturbation_response(*paths, spec)
    with pytest.raises(ValueError, match="non_targeting_control"):
        fit_perturbation_response(
            *paths, spec.model_copy(update={"non_targeting_control": None})
        )


def test_refuses_single_replicate_confirmatory_inference(tmp_path):
    from devbench.core.perturbation_analysis import fit_perturbation_response

    paths, spec, _, _ = _perturbation_inputs(tmp_path, donors=1)
    with pytest.raises(ValueError, match="two_biological_units"):
        fit_perturbation_response(*paths, spec)
