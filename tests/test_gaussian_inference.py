"""Fixed-design Gaussian inference checked against an independent R lm result."""
import numpy as np
import pandas as pd
import pytest
from scipy.stats import f

from devharness_core.storage import ChunkedExpressionStore
from devharness_server.tools_stat import _candidate_designs, _fit_worker


def gaussian_case(tmp_path):
    time = np.repeat(np.arange(4, dtype=float), 3)
    # A frozen diagnostic null fixture, not a clinical validation population.
    y = 5 + np.random.default_rng(20260907).normal(size=(12, 20000))[:, 1]
    metadata = pd.DataFrame({"sample_id": [f"S{i}" for i in range(12)], "time": time,
                             "numeric_time": time, "specimen_id": [f"U{i}" for i in range(12)]})
    path = tmp_path / "expression.zarr"
    ChunkedExpressionStore.from_dataframe_to_zarr(pd.DataFrame([y], index=["g"], columns=metadata.sample_id), path)
    meta = tmp_path / "metadata.csv"
    metadata.to_csv(meta, index=False)
    return path, meta, metadata, y


def test_fixed_gaussian_uses_f_reference_with_residual_degrees_of_freedom(tmp_path):
    path, meta, metadata, y = gaussian_case(tmp_path)
    result = _fit_worker(str(path), str(meta), "1 + numeric_time", "time", ["g"],
                         selection_criterion="fixed", spline_dfs=[3], data_type="continuous")
    gene = result["genes"]["g"]
    # Independently computed by R 4.3.2 anova(lm(y ~ factor(time))) on the same
    # four-stage fixture. The centered 3-df spline spans this same model space.
    assert gene["p_value"] == pytest.approx(0.102094822585375, abs=1e-12)
    assert gene["p_value"] > .05
    assert gene["test_distribution"] == "F"
    assert gene["test_df_num"] == 3 and gene["test_df_denom"] == 8
    assert gene["test_statistic"] == pytest.approx(2.89242418773421, abs=1e-12)
    assert gene["wald_statistic"] == pytest.approx(3 * gene["test_statistic"])
    assert gene["padj"] == pytest.approx(gene["p_value"])


def test_gaussian_fixed_design_matches_projection_based_f_test(tmp_path):
    path, meta, metadata, y = gaussian_case(tmp_path)
    _, design, _ = _candidate_designs(metadata, "1 + numeric_time", [3])[0][3]
    x = np.asarray(design)
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    sse = float(np.sum((y - x @ beta) ** 2))
    reduced_sse = float(np.sum((y - y.mean()) ** 2))
    statistic = ((reduced_sse - sse) / 3) / (sse / 8)
    expected = f.sf(statistic, 3, 8)
    actual = _fit_worker(str(path), str(meta), "1 + numeric_time", "time", ["g"],
                          selection_criterion="fixed", spline_dfs=[3], data_type="continuous")
    assert actual["genes"]["g"]["p_value"] == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize("criterion,dfs", [("fixed", [3]), ("aic", [2, 3, 4, 5])])
def test_nuisance_only_gaussian_has_no_usable_residual_information(tmp_path, criterion, dfs):
    n = 48
    metadata = pd.DataFrame({
        "sample_id": [f"S{i}" for i in range(n)], "specimen_id": [f"U{i}" for i in range(n)],
        "time": np.repeat(np.linspace(0, 1, 6), 8), "batch": np.tile(["B0", "B1"], n // 2),
    })
    y = (metadata.batch == "B1").to_numpy(dtype=float)
    expression, meta = tmp_path / "expression.zarr", tmp_path / "metadata.csv"
    ChunkedExpressionStore.from_dataframe_to_zarr(
        pd.DataFrame([y], index=["g"], columns=metadata.sample_id), expression)
    metadata.to_csv(meta, index=False)
    with pytest.raises(ValueError, match="residual information"):
        _fit_worker(str(expression.resolve()), str(meta.resolve()), "1 + C(batch) + numeric_time",
                    "time", ["g"], selection_criterion=criterion, spline_dfs=dfs, data_type="continuous")


def test_resolved_small_scale_gaussian_keeps_the_fixed_f_result(tmp_path):
    path, meta, metadata, y = gaussian_case(tmp_path)
    tiny = tmp_path / "small-units.zarr"
    ChunkedExpressionStore.from_dataframe_to_zarr(
        pd.DataFrame([y * 1e-10], index=["g"], columns=metadata.sample_id), tiny)
    actual = _fit_worker(str(tiny.resolve()), str(meta.resolve()), "1 + numeric_time", "time", ["g"],
                         selection_criterion="fixed", spline_dfs=[3], data_type="continuous")
    assert actual["genes"]["g"]["p_value"] == pytest.approx(0.102094822585375, abs=1e-12)
