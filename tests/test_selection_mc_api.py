"""Supervised inference-mode contracts, independent ranks, and legacy behavior."""
import hashlib
import json

import numpy as np
import pandas as pd
import pytest
from scipy.stats import f, false_discovery_control

from devharness_core.provenance import canonical_json
from devharness_core.storage import ChunkedExpressionStore
from devharness_server import tools_stat


def case(tmp_path, *, counts=False, constant=False):
    n = 24
    metadata = pd.DataFrame({
        "sample_id": [f"S{i}" for i in range(n)], "specimen_id": [f"U{i}" for i in range(n)],
        "time": np.repeat(np.linspace(0, 1, 6), 4), "batch": np.tile(["B0", "B1"], n // 2),
    })
    metadata["numeric_time"] = metadata["time"]
    values = 5 + np.random.default_rng(937).normal(size=(2, n))
    if counts:
        values = np.round(np.abs(values) * 10).astype(np.int64)
    if constant:
        values[:] = 5
    expression, meta = tmp_path / "expression.zarr", tmp_path / "metadata.csv"
    ChunkedExpressionStore.from_dataframe_to_zarr(
        pd.DataFrame(values, index=["g0", "g1"], columns=metadata.sample_id), expression)
    metadata.to_csv(meta, index=False)
    return expression.resolve(), meta.resolve(), metadata, values


def fit(paths, **kwargs):
    return tools_stat._fit_worker(str(paths[0]), str(paths[1]), "1 + C(batch) + numeric_time",
                                  "time", ["g0", "g1"], **({"data_type": "continuous"} | kwargs))


def independent_rank(metadata, y, criterion, draws, seed):
    """SVD least squares, independent of the kernel's Householder arithmetic."""
    designs, _ = tools_stat._candidate_designs(metadata, "1 + C(batch) + numeric_time", [2, 3, 4, 5])
    simulated = np.random.Generator(np.random.PCG64(seed)).normal(size=(draws, len(y)))
    responses = np.vstack((y, simulated)).T
    probabilities, scores = [], []
    for df in sorted(designs):
        _, frame, _ = designs[df]
        x = np.asarray(frame)
        columns = [i for term, sl in frame.design_info.term_name_slices.items()
                   if "numeric_time" in term for i in range(sl.start, sl.stop)]
        x0 = np.delete(x, columns, axis=1)
        beta = np.linalg.lstsq(x, responses, rcond=None)[0]
        beta0 = np.linalg.lstsq(x0, responses, rcond=None)[0]
        rss = np.sum((responses - x @ beta) ** 2, axis=0)
        rss0 = np.sum((responses - x0 @ beta0) ** 2, axis=0)
        n, p = x.shape
        statistic = np.maximum(rss0-rss, 0) / len(columns) / (rss/(n-p))
        probabilities.append(f.sf(statistic, len(columns), n-p))
        minus2ll = n * (np.log(2*np.pi) + 1 + np.log(rss/n))
        scores.append({"aic": minus2ll+2*(p+1), "bic": minus2ll+np.log(n)*(p+1),
                       "gcv": n*rss/(n-p)**2}[criterion])
    probability = np.asarray(probabilities)[np.argmin(scores, axis=0), np.arange(draws+1)]
    return (1 + int(np.sum(probability[1:] <= probability[0]))) / (draws+1)


@pytest.mark.parametrize("criterion", ["aic", "bic", "gcv"])
def test_mc_mode_matches_independent_full_selection_rank(tmp_path, criterion):
    paths = case(tmp_path)
    result = fit(paths, selection_criterion=criterion, spline_dfs=[2, 3, 4, 5],
                 inference_mode="gaussian_selection_mc", monte_carlo_draws=127, monte_carlo_seed=21)
    assert result["family"] == "gaussian"
    metadata = result["selection_adjustment"]
    assert metadata["status"] == "completed"
    assert metadata["requested_draws"] == metadata["completed_draws"] == 127
    assert metadata["simulation_layout"] == "draw_major"
    probabilities = []
    for index, name in enumerate(("g0", "g1")):
        gene = result["genes"][name]
        expected = independent_rank(paths[2], paths[3][index], criterion, 127, 21)
        assert gene["p_value"] == expected and gene["p_value"] >= 1/128
        assert gene["test_distribution"] == "selection_monte_carlo"
        assert gene["test_statistic"] == gene["conditional_p_value"]
        assert gene["test_df_num"] is None and gene["test_df_denom"] is None
        assert gene["conditional_df_num"] > 0 and gene["conditional_df_denom"] > 0
        probabilities.append(gene["p_value"])
    assert [result["genes"][g]["padj"] for g in ("g0", "g1")] == pytest.approx(
        false_discovery_control(probabilities, method="bh"))
    assert [result["genes"][g]["by_q_value"] for g in ("g0", "g1")] == pytest.approx(
        false_discovery_control(probabilities, method="by"))
    canonical_json(result)


def test_default_mode_is_numerically_and_structurally_unchanged(tmp_path):
    paths = case(tmp_path)
    before = fit(paths, selection_criterion="fixed", spline_dfs=[3])
    explicit = fit(paths, selection_criterion="fixed", spline_dfs=[3], inference_mode="conditional")
    assert before == explicit
    assert "selection_adjustment" not in before
    assert all("by_q_value" not in gene for gene in before["genes"].values())
    assert before["provenance"]["parameters"]["statistical_method_version"] == "temporal_glm/2_gaussian_F"


def test_constant_genes_do_not_claim_simulated_reference_or_informative_test(tmp_path):
    paths = case(tmp_path, constant=True)
    result = fit(paths, inference_mode="gaussian_selection_mc", monte_carlo_draws=31)
    assert result["selection_adjustment"]["status"] == "not_run"
    assert result["selection_adjustment"]["completed_draws"] == 0
    assert result["selection_adjustment"]["reference_probability_hash"] is None
    assert all(gene["status"] == "no_information" and gene["test_distribution"] == "not_tested"
               for gene in result["genes"].values())


def test_mc_counts_refusal_precedes_dispersion_calibration(tmp_path, monkeypatch):
    from devharness_server import normalization
    paths = case(tmp_path, counts=True)
    def forbidden(*args, **kwargs):
        raise AssertionError("Count calibration must not run in Gaussian MC mode")
    monkeypatch.setattr(normalization, "calibrate_counts", forbidden)
    with pytest.raises(ValueError, match="continuous Gaussian"):
        tools_stat._fit_worker(str(paths[0]), str(paths[1]), "1 + C(batch) + numeric_time", "time",
                              ["g0", "g1"], data_type="counts", inference_mode="gaussian_selection_mc")


@pytest.mark.parametrize("options,reason", [
    ({"inference_mode": "unknown"}, "inference_mode"),
    ({"inference_mode": "gaussian_selection_mc", "selection_criterion": "fixed", "spline_dfs": [3]}, "fixed"),
    ({"inference_mode": "conditional", "monte_carlo_draws": 127}, "require"),
])
def test_invalid_mode_combinations_fail_explicitly(tmp_path, options, reason):
    with pytest.raises(ValueError, match=reason):
        fit(case(tmp_path), **options)


def test_mc_does_not_drop_failed_candidates_in_partial_mode(tmp_path, monkeypatch):
    paths = case(tmp_path)
    original = tools_stat._fit_candidate
    def fail_one(values, design, alpha, offset):
        if design.shape[1] == 6:
            raise ValueError("injected_candidate_failure")
        return original(values, design, alpha, offset)
    monkeypatch.setattr(tools_stat, "_fit_candidate", fail_one)
    with pytest.raises(ValueError, match="candidate_fit_failed"):
        fit(paths, inference_mode="gaussian_selection_mc", monte_carlo_draws=31, strict=False)


def test_supervised_api_replays_reference_and_result(tmp_path):
    paths = case(tmp_path)
    options = dict(selection_criterion="bic", spline_dfs=[2, 3, 4, 5], data_type="continuous",
                   inference_mode="gaussian_selection_mc", monte_carlo_draws=127, monte_carlo_seed=21)
    first = tools_stat.fit_temporal_trajectory(str(paths[0]), str(paths[1]),
        "1 + C(batch) + numeric_time", "time", ["g0", "g1"], **options)
    second = tools_stat.fit_temporal_trajectory(str(paths[0]), str(paths[1]),
        "1 + C(batch) + numeric_time", "time", ["g0", "g1"], **options)
    assert first == second
    canonical_json(first)


@pytest.mark.parametrize("criterion", ["aic", "bic", "gcv"])
@pytest.mark.parametrize("selected_df", [2, 3, 4, 5])
def test_actual_spline_candidates_select_each_df(tmp_path, criterion, selected_df):
    """Purpose-built software fixtures, deliberately not calibration samples."""
    n = 48
    metadata = pd.DataFrame({
        "sample_id": [f"S{i}" for i in range(n)], "specimen_id": [f"U{i}" for i in range(n)],
        "time": np.repeat(np.linspace(0, 1, 6), 8), "batch": np.tile(["B0", "B1"], n // 2),
    })
    metadata["numeric_time"] = metadata.time
    designs, rejected = tools_stat._candidate_designs(metadata, "1 + C(batch) + numeric_time", [2, 3, 4, 5])
    assert not rejected
    full = np.asarray(designs[5][1])
    noise = np.random.Generator(np.random.PCG64(614)).normal(size=n)
    noise -= full @ np.linalg.lstsq(full, noise, rcond=None)[0]
    noise /= np.linalg.norm(noise)
    target = np.asarray(designs[selected_df][1])
    signal = target @ np.arange(1, target.shape[1]+1, dtype=float)
    signal /= np.linalg.norm(signal)
    y = 5 + 20*signal + noise
    expression, meta = tmp_path / "expression.zarr", tmp_path / "metadata.csv"
    with ChunkedExpressionStore.from_dataframe_to_zarr(
            pd.DataFrame([y], index=["g"], columns=metadata.sample_id), expression):
        pass
    metadata.to_csv(meta, index=False)
    actual = tools_stat._fit_worker(str(expression.resolve()), str(meta.resolve()),
        "1 + C(batch) + numeric_time", "time", ["g"], data_type="continuous",
        selection_criterion=criterion, inference_mode="gaussian_selection_mc",
        monte_carlo_draws=31, monte_carlo_seed=62)
    gene = actual["genes"]["g"]
    assert gene["selected_df"] == selected_df
    assert gene["conditional_df_num"] == selected_df
    assert gene["conditional_df_denom"] == n-target.shape[1]
    assert gene["p_value"] == independent_rank(metadata, y, criterion, 31, 62)


def test_sdk_executes_mc_and_records_exact_result_identity(tmp_path, monkeypatch):
    import devharness as dh
    paths = case(tmp_path)
    monkeypatch.setenv("DEVHARNESS_WORKSPACE_ROOT", str(tmp_path.resolve()))
    options = dict(selection_criterion="bic", spline_dfs=[2, 3, 4, 5], data_type="continuous",
                   inference_mode="gaussian_selection_mc", monte_carlo_draws=127, monte_carlo_seed=21)
    expected = fit(paths, **options)
    actual = dh.fit_trajectory(paths[0], paths[1], "1 + C(batch) + numeric_time",
                               ["g0", "g1"], time_col="time", **options)
    assert actual == expected
    events = [json.loads(line) for line in (tmp_path / ".devharness_trace.jsonl").read_text().splitlines()]
    assert len(events) == 1 and events[0]["succeeded"] is True
    assert events[0]["authenticated"] is False
    event = events[0]["tool_events"][0]
    assert event["tool_name"] == "fit_temporal_trajectory" and event["succeeded"] is True
    for key in ("inference_mode", "monte_carlo_draws", "monte_carlo_seed"):
        assert event["arguments"][key] == options[key]
    encoded = json.dumps(actual, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    assert event["result_sha256"] == hashlib.sha256(encoded).hexdigest()


def test_sdk_default_omits_mc_options_and_invalid_options_remain_failures(tmp_path, monkeypatch):
    import devharness as dh
    paths = case(tmp_path)
    monkeypatch.setenv("DEVHARNESS_WORKSPACE_ROOT", str(tmp_path.resolve()))
    dh.fit_trajectory(paths[0], paths[1], "1 + numeric_time", ["g0"], time_col="time",
                      selection_criterion="fixed", spline_dfs=[3])
    event = json.loads((tmp_path / ".devharness_trace.jsonl").read_text().splitlines()[0])
    assert not {"inference_mode", "monte_carlo_draws", "monte_carlo_seed"} & event["tool_events"][0]["arguments"].keys()
    with pytest.raises(Exception, match="monte_carlo_seed|integer"):
        dh.fit_trajectory(paths[0], paths[1], "1 + numeric_time", ["g0"], time_col="time",
                          monte_carlo_seed=False)
    failure = json.loads((tmp_path / ".devharness_trace.jsonl").read_text().splitlines()[-1])
    assert failure["succeeded"] is False


def test_real_stdio_mc_schema_result_and_refusals(tmp_path):
    from tests.test_mcp_protocol import _run
    paths = case(tmp_path)
    options = dict(selection_criterion="bic", spline_dfs=[2, 3, 4, 5], data_type="continuous",
                   inference_mode="gaussian_selection_mc", monte_carlo_draws=127, monte_carlo_seed=21)
    expected = fit(paths, **options)
    arguments = dict(counts_path=str(paths[0]), metadata_path=str(paths[1]),
                     formula_str="1 + C(batch) + numeric_time", time_col="time",
                     gene_list=["g0", "g1"], **options)

    async def check(session):
        schema = {tool.name: tool for tool in (await session.list_tools()).tools}["fit_temporal_trajectory"].inputSchema
        assert schema["properties"]["inference_mode"]["default"] == "conditional"
        assert schema["properties"]["monte_carlo_draws"]["type"] == "integer"
        assert schema["properties"]["monte_carlo_seed"]["type"] == "integer"
        for invalid in ({"monte_carlo_draws": True}, {"monte_carlo_draws": 127.0},
                        {"monte_carlo_seed": -1}, {"data_type": "counts"},
                        {"selection_criterion": "fixed", "spline_dfs": [3]}):
            result = await session.call_tool("fit_temporal_trajectory", arguments | invalid)
            assert result.isError and result.structuredContent is None
        result = await session.call_tool("fit_temporal_trajectory", arguments)
        assert not result.isError
        assert result.structuredContent == expected
        assert json.loads(result.content[0].text) == expected
        canonical_json(result.structuredContent)
    _run(tmp_path, check)
