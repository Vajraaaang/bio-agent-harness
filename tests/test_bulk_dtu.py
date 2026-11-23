import hashlib

import numpy as np
import pandas as pd
import pytest


def _dtu_inputs(root, *, zero=False, switch=True, gene_only=False, confounded=False):
    from devbench.core._rna_solver_inputs import ConditionContrast
    from devbench.core.bulk_dtu import BulkDTUSpec

    size = 12
    ids = [f"s{i}" for i in range(size)]
    groups = ["control"] * 6 + ["treated"] * 6
    batch = groups if confounded else ["b1", "b2"] * 6
    fractions = np.array([0.22, 0.28, 0.21, 0.26, 0.24, 0.29] * 2)
    if switch:
        fractions[6:] += 0.5
    totals = np.full(size, 200) if switch else np.repeat([200, 600], 6)
    if zero:
        totals[0] = 0
    first = np.rint(fractions * totals).astype(int)
    values = {"sample_id": ids, "tx1": first, "tx2": totals - first, "tx3": 45}
    if gene_only:
        values.pop("tx2")
    data = pd.DataFrame(values)
    meta = pd.DataFrame(
        {"sample_id": ids, "donor_id": ids, "condition": groups, "batch": batch}
    )
    mapping = pd.DataFrame(
        {
            "transcript_id": list(values)[1:],
            "gene_id": ["g1", "g2"] if gene_only else ["g1", "g1", "g2"],
        }
    )
    paths = [root / name for name in ("counts.csv", "metadata.csv", "mapping.csv")]
    for path, frame in zip(paths, (data, meta, mapping), strict=True):
        frame.to_csv(path, index=False)
    counts, metadata, feature_map = [
        hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    ]
    return paths, BulkDTUSpec(
        counts_sha256=counts,
        metadata_sha256=metadata,
        mapping_sha256=feature_map,
        contrast=ConditionContrast(numerator="treated", denominator="control"),
    )


def _fit(root, **options):
    from devbench.core.bulk_dtu import fit_bulk_dtu

    paths, spec = _dtu_inputs(root, **options)
    return fit_bulk_dtu(*paths, spec)


def test_recovers_isoform_switch_and_records_exact_two_transcript_adjustment(tmp_path):
    from statsmodels.genmod.families import Binomial
    from statsmodels.genmod.generalized_linear_model import GLM

    from devbench.core._hashing import canonical_sha256

    receipt = _fit(tmp_path)
    g1, g2 = receipt["gene_statistics"]
    assert g1["gene_p"] <= g1["gene_q"] < 0.05
    assert g2["status"] == "single_isoform_gene"
    tx1, tx2 = g1["transcript_statistics"]
    assert tx1["log_odds_ratio"] > 1 and tx2["log_odds_ratio"] < -1
    assert tx1["tx_p"] == pytest.approx(tx2["tx_p"])
    counts = pd.read_csv(tmp_path / "counts.csv")
    design = np.column_stack((np.ones(12), np.arange(12) >= 6, np.arange(12) % 2))
    fit = GLM(counts[["tx1", "tx2"]], design, family=Binomial()).fit()
    expected_scale = fit.pearson_chi2 / fit.df_resid
    for tx in (tx1, tx2):
        assert tx["tx_p"] == pytest.approx(tx["p_within_shaffer"])
        assert tx["pearson_dispersion"] == pytest.approx(expected_scale)
        assert tx["tx_q"] == max(g1["gene_q"], tx["p_within_shaffer"])
    assert receipt["target_ofdr"] == 0.05
    assert receipt["clinical_release_allowed"] is False
    digest = receipt.pop("receipt_sha256")
    assert digest == canonical_sha256(receipt)


def test_pure_gene_level_de_does_not_screen(tmp_path):
    g1 = _fit(tmp_path, switch=False)["gene_statistics"][0]
    assert g1["gene_q"] > 0.05
    assert all(
        tx["status"] == "not_screened" and tx["tx_q"] is None
        for tx in g1["transcript_statistics"]
    )


def test_zero_total_sample_is_excluded_for_its_gene(tmp_path):
    g1, g2 = _fit(tmp_path, zero=True)["gene_statistics"]
    assert g1["zero_total_samples_dropped"] == ["s0"]
    assert g1["biological_units_per_group"] == {"control": 5, "treated": 6}
    assert g1["gene_q"] < 0.05
    assert not g2["zero_total_samples_dropped"]


@pytest.mark.parametrize(
    "options, reason",
    [
        ({"gene_only": True}, "multi_feature_gene"),
        ({"confounded": True}, "rank_deficient_design"),
    ],
)
def test_refuses_gene_only_and_confounded_design(tmp_path, options, reason):
    with pytest.raises(ValueError, match=reason):
        _fit(tmp_path, **options)


def test_refuses_missing_mapping(tmp_path):
    from devbench.core.bulk_dtu import fit_bulk_dtu

    paths, spec = _dtu_inputs(tmp_path)
    with pytest.raises(ValueError, match="mapping_required"):
        fit_bulk_dtu(*paths[:2], None, spec)


def test_gene_losing_replicates_is_unestimable(tmp_path):
    from devbench.core.bulk_dtu import fit_bulk_dtu

    paths, spec = _dtu_inputs(tmp_path)
    data = pd.read_csv(paths[0])
    data.loc[:4, ["tx1", "tx2"]] = 0
    data.to_csv(paths[0], index=False)
    spec = spec.model_copy(
        update={
            "counts_sha256": hashlib.sha256(paths[0].read_bytes()).hexdigest(),
        }
    )
    gene = fit_bulk_dtu(*paths, spec)["gene_statistics"][0]
    assert gene["gene_p"] is gene["gene_q"] is None
    assert gene["missing_reason"] == "insufficient_units_after_zero_total_drop"


def test_effective_lengths_are_bound_and_exon_usage_is_labeled(tmp_path):
    from devbench.core.bulk_dtu import fit_bulk_dtu

    paths, spec = _dtu_inputs(tmp_path)
    lengths = tmp_path / "lengths.csv"
    lengths.write_text("exon_id,effective_length\ntx1,500\ntx2,1000\ntx3,750\n")
    mapping = paths[2].read_text().replace("transcript_id", "exon_id")
    paths[2].write_text(mapping)
    spec = spec.model_copy(
        update={
            "measurement": "exon_counts",
            "mapping_sha256": hashlib.sha256(paths[2].read_bytes()).hexdigest(),
            "effective_lengths_sha256": hashlib.sha256(
                lengths.read_bytes()
            ).hexdigest(),
        }
    )
    result = fit_bulk_dtu(*paths, spec, effective_lengths_path=lengths)
    assert result["gene_statistics"][0]["gene_q"] < 0.05
    assert "effective_lengths" in result["input_sha256s"]
    assert all("length bias" not in warning for warning in result["warnings"])
    assert any("exon usage" in warning for warning in result["warnings"])
