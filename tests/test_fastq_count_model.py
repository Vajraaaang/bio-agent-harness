"""Direct integer-count model checks; no provider, workflow or original data."""
from __future__ import annotations

import csv
import importlib
import json
from pathlib import Path

import numpy as np
import pytest


def _model():
    return importlib.import_module("devbench.analysis.fastq_count_model")


def _design():
    rows = []
    for batch in ("b0", "b1"):
        for condition in ("control", "treated"):
            for replicate in range(3):
                identity = f"{batch}_{condition}_{replicate}"
                rows.append({"sample_id": identity, "donor_id": identity,
                             "batch": batch, "condition": condition})
    matrix = [[1.0, float(row["batch"] == "b1"), float(row["condition"] == "treated")]
              for row in rows]
    return {"sample_ids": [row["sample_id"] for row in rows], "metadata_rows": rows,
            "formula": "~ batch + condition", "terms": ["batch", "condition"],
            "contrast": {"factor": "condition", "numerator": "treated", "denominator": "control"},
            "design_columns": ["Intercept", "batch[b1]", "condition[treated]"],
            "design_matrix": matrix, "contrast_vector": [0.0, 0.0, 1.0],
            "rank": 3, "residual_df": 9, "biological_unit": "donor_id", "unit_count": 12}


def _write_counts(path, design, counts):
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["gene_id", *design["sample_ids"]])
        for gene, values in counts.items():
            writer.writerow([gene, *values])
    return path


@pytest.fixture(scope="module")
def fitted(tmp_path_factory):
    root = tmp_path_factory.mktemp("fastq-count-model")
    design = _design()
    rng = np.random.default_rng(16383)
    x = np.asarray(design["design_matrix"])
    counts = {}
    # The large unaffected majority keeps RLE normalization identifiable.
    for gene in range(240):
        effect = np.log(4.0) if gene < 12 else 0.0
        mean = np.exp(4.5 + (gene % 7) / 8 + 0.9 * x[:, 1] + effect * x[:, 2])
        counts[f"gene_{gene:04}"] = rng.negative_binomial(40.0, 40.0 / (40.0 + mean))
    counts["zero"] = np.zeros(len(x), dtype=int)
    path = _write_counts(root / "counts.tsv", design, counts)
    result = _model().fit_count_matrix(path, design, root / "model", alpha=0.05)
    with Path(result["differential_expression"]["path"]).open() as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    provenance = json.loads(Path(result["provenance"]["path"]).read_text())
    return result, rows, provenance, counts, design


def test_count_model_version_is_exact_and_missing_dependency_is_not_a_fallback(monkeypatch):
    model = _model()
    actual = model.check_count_model()
    assert actual["version"] == "0.5.4"
    assert actual["upstream_commit"] == "4426e4db990db1c511de3b1b9b7a514989663dad"
    monkeypatch.setattr(model.importlib.metadata, "version", lambda name: "0.5.3")
    with pytest.raises(ValueError, match="0.5.4"):
        model.check_count_model()


def test_count_model_rejects_modified_same_version_source(tmp_path, monkeypatch):
    model = _model()
    distribution = model.importlib.metadata.distribution("pydeseq2")
    modified = tmp_path / "utils.py"
    modified.write_bytes(Path(distribution.locate_file("pydeseq2/utils.py")).read_bytes() + b"\n# changed\n")

    class ChangedDistribution:
        version = distribution.version

        def locate_file(self, name):
            return modified if str(name) == "pydeseq2/utils.py" else distribution.locate_file(name)

    original = model.importlib.metadata.distribution
    monkeypatch.setattr(model.importlib.metadata, "distribution",
                        lambda name: ChangedDistribution() if name == "pydeseq2" else original(name))
    with pytest.raises(ValueError, match="source.*pin"):
        model.check_count_model()


def test_missing_package_is_an_explicit_dependency_failure(monkeypatch):
    model = _model()

    def absent(name):
        raise model.importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(model.importlib.metadata, "version", absent)
    with pytest.raises(ValueError, match="not installed"):
        model.check_count_model()


def test_failed_coefficient_and_nullable_zero_gene_flags_do_not_become_estimates():
    from types import SimpleNamespace
    import pandas as pd

    genes = ["failed", "estimated", "zero"]
    counts = pd.DataFrame([[5, 8, 0], [10, 12, 0]], columns=genes)
    dds = SimpleNamespace(var=pd.DataFrame({"dispersions": [0.2, 0.2, np.nan],
        "_LFC_converged": pd.array([False, True, pd.NA], dtype="boolean")}, index=genes))
    statistics = SimpleNamespace(results_df=pd.DataFrame({"baseMean": [7.5, 10, 0],
        "log2FoldChange": [1.0, 0.6, np.nan], "lfcSE": [0.2, 0.2, np.nan],
        "stat": [5.0, 3.0, np.nan], "pvalue": [0.01, 0.04, np.nan]}, index=genes))
    rows = _model()._model_rows(dds, statistics, counts)
    assert rows[0]["status"] == "fit_failed"
    assert rows[0]["pvalue"] is rows[0]["padj"] is rows[0]["log2FoldChange"] is None
    assert rows[1]["pvalue"] == rows[1]["padj"] == 0.04
    assert rows[2]["status"] == "not_estimable"
    assert rows[2]["pvalue"] is rows[2]["padj"] is None


def test_real_fit_retains_formula_family_null_genes_and_effect_direction(fitted):
    result, rows, provenance, counts, design = fitted
    assert result["formula"] == "~ batch + condition"
    assert provenance["design"]["design_columns"] == design["design_columns"]
    assert len(rows) == len(counts) == 241
    by_id = {row["gene_id"]: row for row in rows}
    assert list(by_id) == list(counts)
    assert by_id["zero"]["status"] == "not_estimable"
    assert by_id["zero"]["pvalue"] == by_id["zero"]["padj"] == ""
    assert by_id["zero"]["log2FoldChange"] == ""
    assert by_id["zero"]["missing_reason"] == "all_zero_counts"
    effects = [float(by_id[f"gene_{gene:04}"]["log2FoldChange"]) for gene in range(12)]
    assert np.median(effects) == pytest.approx(2.0, abs=0.3)
    assert provenance["numerical_policy"]["independent_filtering"] is False
    assert provenance["numerical_policy"]["failed_hypothesis_policy"] == "exclude_failed_hypotheses"
    assert provenance["evidence"]["scientific_correctness"] is None
    assert provenance["evidence"]["biological_agreement"] == "not_run"
    assert provenance["evidence"]["model_sessions"] == 0


def test_bh_matches_independent_sort_calculation_on_the_reported_test_family(fitted):
    _, rows, _, _, _ = fitted
    finite = [row for row in rows if row["pvalue"]]
    p = np.asarray([float(row["pvalue"]) for row in finite])
    actual = np.asarray([float(row["padj"]) for row in finite])
    order = np.argsort(p)
    expected_sorted = np.minimum.accumulate((p[order] * len(p) / np.arange(1, len(p) + 1))[::-1])[::-1]
    expected = np.empty_like(p)
    expected[order] = np.minimum(1.0, expected_sorted)
    np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-15)
    assert np.all(actual >= p)


def test_condition_estimate_and_wald_test_match_independent_conditional_nb_glm(fitted):
    import statsmodels.api as sm
    from scipy.special import erfc

    _, rows, provenance, counts, design = fitted
    row = next(row for row in rows if row["gene_id"] == "gene_0000")
    x = np.asarray(design["design_matrix"])
    sf = np.asarray(provenance["normalization"]["size_factors"])
    y = np.asarray(counts[row["gene_id"]])
    dispersion = float(row["dispersion"])
    fit = sm.GLM(y, x, family=sm.families.NegativeBinomial(alpha=dispersion),
                 offset=np.log(sf)).fit(maxiter=200, tol=1e-12)
    assert float(row["log2FoldChange"]) == pytest.approx(fit.params[2] / np.log(2), abs=1e-4)
    mu = sf * np.exp(x @ fit.params)
    weights = mu / (1 + dispersion * mu)
    information = x.T @ (weights[:, None] * x)
    inverse = np.linalg.inv(information + np.eye(x.shape[1]) * 1e-6)
    se = np.sqrt((inverse @ information @ inverse)[2, 2])
    assert float(row["lfcSE"]) == pytest.approx(se / np.log(2), rel=1e-4)
    expected_p = erfc(abs(fit.params[2] / se) / np.sqrt(2))
    assert float(row["pvalue"]) == pytest.approx(expected_p, rel=2e-3, abs=1e-20)


def test_rle_offsets_match_independent_geometric_mean_calculation(fitted):
    _, _, provenance, counts, _ = fitted
    matrix = np.asarray(list(counts.values()), dtype=float)
    positive = matrix[np.all(matrix > 0, axis=1)]
    expected = np.exp(np.median(np.log(positive) - np.mean(np.log(positive), axis=1)[:, None], axis=0))
    np.testing.assert_allclose(provenance["normalization"]["size_factors"], expected, rtol=1e-12)


def test_transport_keeps_more_than_5000_genes(tmp_path):
    design = _design()
    counts = {f"gene_{index}": np.full(12, index % 7 + 1) for index in range(5001)}
    path = _write_counts(tmp_path / "counts.tsv", design, counts)
    frame = _model().read_count_matrix(path, design["sample_ids"])
    assert frame.shape == (12, 5001)
    assert list(frame.columns) == list(counts)


@pytest.mark.parametrize("token", ["-1", "1.2", "nan", "inf", "1e2"])
def test_count_reader_refuses_noninteger_nonfinite_and_negative_values(tmp_path, token):
    path = tmp_path / "counts.tsv"
    path.write_text("gene_id\ts0\ts1\nG1\t" + token + "\t2\n")
    with pytest.raises(ValueError, match="integer"):
        _model().read_count_matrix(path, ["s0", "s1"])


def test_count_reader_rejects_duplicated_gene_or_sample_identity(tmp_path):
    path = tmp_path / "counts.tsv"
    path.write_text("gene_id\ts0\ts1\nG1\t1\t2\nG1\t1\t2\n")
    with pytest.raises(ValueError, match="gene"):
        _model().read_count_matrix(path, ["s0", "s1"])
    path.write_text("gene_id\ts0\ts0\nG1\t1\t2\n")
    with pytest.raises(ValueError, match="sample"):
        _model().read_count_matrix(path, ["s0", "s1"])


def test_count_reader_preserves_sample_named_gene_id_and_metadata_order(tmp_path):
    path = tmp_path / "counts.tsv"
    path.write_text("gene_id\ts1\tgene_id\n1234\t200\t100\n")
    frame = _model().read_count_matrix(path, ["gene_id", "s1"])
    assert frame["1234"].to_dict() == {"gene_id": 100, "s1": 200}


def test_gene_specific_batch_effect_does_not_become_a_condition_effect(tmp_path):
    import statsmodels.api as sm
    from devbench.analysis.fastq_contract import validate_design

    rows = []
    for batch, condition, number in (("b0", "control", 8), ("b0", "treated", 2),
                                     ("b1", "control", 2), ("b1", "treated", 8)):
        for replicate in range(number):
            identity = f"{batch}_{condition}_{replicate}"
            rows.append({"sample_id": identity, "donor_id": identity,
                         "batch": batch, "condition": condition})
    design = validate_design(rows, formula="~ batch + condition",
        contrast={"factor": "condition", "numerator": "treated", "denominator": "control"},
        biological_unit="donor_id", batch_columns=["batch"])
    x = np.asarray(design["design_matrix"])
    rng = np.random.default_rng(47621)
    counts = {}
    for gene in range(160):
        mean = (1500 + 10 * gene) * np.exp(np.log(8) * x[:, 1] if gene == 0 else np.zeros(len(rows)))
        counts[f"gene_{gene}"] = rng.negative_binomial(250, 250 / (250 + mean))
    path = _write_counts(tmp_path / "counts.tsv", design, counts)
    result = _model().fit_count_matrix(path, design, tmp_path / "fit")
    with Path(result["differential_expression"]["path"]).open() as handle:
        gene = next(row for row in csv.DictReader(handle, delimiter="\t") if row["gene_id"] == "gene_0")
    provenance = json.loads(Path(result["provenance"]["path"]).read_text())
    # A one-factor fit sees the unequal batch composition as a condition signal.
    reduced = x[:, [0, 2]]
    naive = sm.GLM(counts["gene_0"], reduced,
        family=sm.families.NegativeBinomial(alpha=float(gene["dispersion"])),
        offset=np.log(provenance["normalization"]["size_factors"])).fit(maxiter=200, tol=1e-12)
    assert abs(float(gene["log2FoldChange"])) < 0.2
    assert float(naive.params[1] / np.log(2)) > 1.0


def test_retained_design_cannot_drop_the_frozen_batch_term(tmp_path):
    design = _design()
    design["design_matrix"] = [[row[0], 0.0, row[2]] for row in design["design_matrix"]]
    path = _write_counts(tmp_path / "counts.tsv", design, {"gene": np.ones(12, dtype=int)})
    with pytest.raises(ValueError, match="differs from recomputed metadata"):
        _model().fit_count_matrix(path, design, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_missing_dependency_or_invalid_design_writes_no_de_table(tmp_path, monkeypatch):
    model = _model()
    design = _design()
    path = _write_counts(tmp_path / "counts.tsv", design, {"gene": np.ones(12, dtype=int)})
    monkeypatch.setattr(model, "check_count_model", lambda: (_ for _ in ()).throw(ValueError("dependency absent")))
    with pytest.raises(ValueError, match="dependency absent"):
        model.fit_count_matrix(path, design, tmp_path / "out")
    assert not (tmp_path / "out" / "differential_expression.tsv").exists()


def test_rle_unavailable_is_not_silently_changed_to_iterative_normalization(tmp_path):
    design = _design()
    counts = {"g1": [0, *([10] * 11)], "g2": [10, 0, *([10] * 10)]}
    path = _write_counts(tmp_path / "counts.tsv", design, counts)
    with pytest.raises(ValueError, match="RLE"):
        _model().fit_count_matrix(path, design, tmp_path / "out")
    assert not (tmp_path / "out" / "differential_expression.tsv").exists()
