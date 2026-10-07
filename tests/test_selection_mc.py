"""Synthetic software checks for the Gaussian selection kernel, not calibration evidence."""

import hashlib
import importlib
import importlib.util
import json

import numpy as np
import pandas as pd
import pytest
from patsy import dmatrix
from scipy.stats import f


def _module():
    assert importlib.util.find_spec("devharness_server.selection_mc") is not None, (
        "The isolated Gaussian selection kernel has not been implemented"
    )
    return importlib.import_module("devharness_server.selection_mc")


def _designs(n=48, dfs=(1, 2, 3), *, nuisance=True, intercept=True):
    metadata = pd.DataFrame({
        "numeric_time": np.linspace(-1, 1, n),
        "batch": np.arange(n) % 2,
    }, index=[f"sample_{i}" for i in range(n)])
    designs = {}
    for df in dfs:
        terms = ["1" if intercept else "0"]
        if nuisance:
            terms.append("C(batch)")
        terms += ["numeric_time"] + [f"I(numeric_time ** {j})" for j in range(2, df + 1)]
        formula = " + ".join(terms)
        frame = dmatrix(formula, metadata, return_type="dataframe")
        designs[df] = (formula, frame, {"is_full_rank": True, "condition_number": 1.0})
    return designs


def _kernel(designs=None, criterion="aic", draws=31, seed=22):
    return _module().GaussianSelectionMC(
        _designs() if designs is None else designs, criterion, draws=draws, seed=seed
    )


def _response(n=48):
    rng = np.random.Generator(np.random.PCG64(481))
    return np.linspace(-1, 1, n) ** 2 + rng.standard_normal(n)


def _replace_frame(designs, df, values):
    formula, original, diagnostics = designs[df]
    frame = pd.DataFrame(values, columns=original.columns, index=original.index)
    frame.design_info = original.design_info
    designs[df] = formula, frame, diagnostics


def _independent_fit(designs, y, criterion):
    records = {}
    for df, (_, frame, _) in designs.items():
        x = np.asarray(frame)
        temporal = [i for term, sl in frame.design_info.term_name_slices.items()
                    if "numeric_time" in term for i in range(sl.start, sl.stop)]
        null = np.delete(x, temporal, axis=1)
        residual = y - x @ np.linalg.lstsq(x, y, rcond=None)[0]
        residual0 = y - null @ np.linalg.lstsq(null, y, rcond=None)[0]
        rss, rss0 = float(residual @ residual), float(residual0 @ residual0)
        n, rank = x.shape
        statistic = ((rss0 - rss) / len(temporal)) / (rss / (n - rank))
        ll = -n / 2 * (np.log(2 * np.pi) + 1 + np.log(rss / n))
        records[df] = {
            "aic": -2 * ll + 2 * (rank + 1),
            "bic": -2 * ll + np.log(n) * (rank + 1),
            "gcv": (rss / (n - rank)) * (n / (n - rank)),
            "log_likelihood": ll,
            "num_parameters": rank + 1,
            "f": statistic,
            "p": f.sf(statistic, len(temporal), n - rank),
            "df_num": len(temporal),
            "df_denom": n - rank,
        }
    selected = min(records, key=lambda df: (records[df][criterion], df))
    return selected, records


@pytest.mark.parametrize("criterion", ["aic", "bic", "gcv"])
def test_observed_scores_and_f_match_independent_least_squares(criterion):
    designs, y = _designs(), _response()
    actual = _kernel(designs, criterion).evaluate(y)
    selected, expected = _independent_fit(designs, y, criterion)
    assert actual["selected_df"] == selected
    assert list(actual["candidate_scores"]) == sorted(designs)
    for df, scores in actual["candidate_scores"].items():
        assert set(scores) == {"aic", "bic", "gcv", "log_likelihood", "num_parameters"}
        for name, value in scores.items():
            assert value == pytest.approx(expected[df][name], rel=2e-12, abs=2e-12)
            assert type(value) in (float, int)
    assert actual["conditional_p_value"] == pytest.approx(expected[selected]["p"], rel=2e-11)
    assert actual["conditional_f_statistic"] == pytest.approx(expected[selected]["f"], rel=2e-11)
    assert actual["conditional_df_num"] == expected[selected]["df_num"]
    assert actual["conditional_df_denom"] == expected[selected]["df_denom"]
    assert 1 / 32 <= actual["p_value"] <= 1
    assert type(actual["selected_df"]) is int
    assert all(type(actual[name]) is float for name in
               ("conditional_p_value", "conditional_f_statistic", "p_value"))
    json.dumps(actual, allow_nan=False)


@pytest.mark.parametrize("criterion", ["aic", "bic", "gcv"])
@pytest.mark.parametrize("selected_df", [2, 3, 4, 5])
def test_each_candidate_can_be_selected_with_purpose_built_signal(criterion, selected_df):
    designs = _designs(n=64, dfs=(2, 3, 4, 5))
    full = np.asarray(designs[5][1])
    basis = np.linalg.qr(full, mode="reduced")[0]
    noise = np.random.Generator(np.random.PCG64(12)).standard_normal(64)
    noise -= basis @ (basis.T @ noise)
    noise /= np.linalg.norm(noise)
    y = 5 * basis[:, selected_df + 1] + noise
    result = _kernel(designs, criterion, draws=7).evaluate(y)
    assert result["selected_df"] == selected_df


@pytest.mark.parametrize("name,value", [
    ("draws", True), ("draws", np.bool_(False)), ("draws", 1.0), ("draws", "7"),
    ("draws", None), ("draws", 0), ("draws", -1), ("draws", 1_000_001),
    ("seed", True), ("seed", np.bool_(True)), ("seed", 1.0), ("seed", "7"),
    ("seed", None), ("seed", -1), ("seed", 2 ** 64),
])
def test_draw_and_seed_type_and_range_guards(name, value):
    arguments = {"draws": 7, "seed": 0, name: value}
    with pytest.raises(ValueError, match=name):
        _module().GaussianSelectionMC(_designs(), "aic", **arguments)


def test_numpy_integer_arguments_and_inclusive_limits():
    first = _kernel(draws=np.int64(1), seed=np.uint64(2 ** 64 - 1))
    assert first.evaluate(_response())["p_value"] in (0.5, 1.0)
    second = _kernel(draws=np.int64(1_000_000), seed=0)
    assert second.metadata()["requested_draws"] == 1_000_000
    assert second.metadata()["completed_draws"] == 0


@pytest.mark.parametrize("criterion", ["fixed", "AIC", None, True, [], ""])
def test_unsupported_criterion_is_rejected(criterion):
    with pytest.raises(ValueError, match="criterion"):
        _kernel(criterion=criterion)


@pytest.mark.parametrize("designs", [{}, [], None, {True: None}, {1.5: None}, {0: None}])
def test_design_mapping_and_df_labels_are_validated(designs):
    with pytest.raises(ValueError, match="design|candidate|df"):
        _module().GaussianSelectionMC(designs, "aic", draws=7)


def test_single_feasible_candidate_and_empty_nuisance_space():
    designs = _designs(dfs=(2,), nuisance=False, intercept=False)
    result = _kernel(designs).evaluate(_response())
    assert result["selected_df"] == 2
    assert result["conditional_df_num"] == 2
    assert result["conditional_df_denom"] == 46
    assert _kernel(designs).metadata()["candidate_dimensions"]["2"]["reduced_columns"] == 0
    expected = _independent_fit(designs, _response(), "aic")[1][2]
    assert result["conditional_f_statistic"] == pytest.approx(expected["f"], rel=1e-12)


@pytest.mark.parametrize("kind", ["rank", "condition", "nan", "inf", "complex", "string"])
def test_actual_matrices_override_forged_positive_diagnostics(kind):
    designs = _designs(dfs=(2,))
    x = np.array(designs[2][1])
    if kind == "rank":
        x[:, -1] = x[:, 0]
    elif kind == "condition":
        x[:, -1] = x[:, 0] + 1e-8 * x[:, -1]
    elif kind in ("nan", "inf"):
        x[0, 0] = np.nan if kind == "nan" else np.inf
    elif kind == "complex":
        x = x.astype(complex)
        x[0, 0] += 1j
    else:
        x = x.astype(str)
    _replace_frame(designs, 2, x)
    with pytest.raises(ValueError, match="rank|condition|finite|numeric|real"):
        _kernel(designs)


def test_actual_feasible_geometry_does_not_depend_on_diagnostics_claims():
    designs = _designs(dfs=(2,))
    formula, frame, _ = designs[2]
    designs[2] = formula, frame, {"is_full_rank": False, "condition_number": None}
    assert _kernel(designs).evaluate(_response())["selected_df"] == 2


def test_candidate_rows_must_be_identically_aligned():
    designs = _designs(dfs=(1, 2))
    designs[2][1].index = list(reversed(designs[2][1].index))
    with pytest.raises(ValueError, match="row|index|align"):
        _kernel(designs)


def test_candidates_must_share_the_actual_reduced_space():
    designs = _designs(dfs=(1, 2))
    x = np.array(designs[2][1])
    x[:, 1] = np.random.Generator(np.random.PCG64(9)).standard_normal(len(x))
    _replace_frame(designs, 2, x)
    with pytest.raises(ValueError, match="reduced|nuisance|null"):
        _kernel(designs)


def test_reparameterized_common_nuisance_space_is_accepted():
    designs = _designs(dfs=(1, 2))
    x = np.array(designs[2][1])
    x[:, 1] = 3 + 2 * x[:, 1]
    _replace_frame(designs, 2, x)
    actual = _kernel(designs).evaluate(_response())
    expected = _kernel(_designs(dfs=(1, 2))).evaluate(_response())
    assert actual["p_value"] == expected["p_value"]
    assert actual["conditional_p_value"] == pytest.approx(expected["conditional_p_value"], rel=1e-12)


def test_missing_temporal_terms_and_patsy_information_are_rejected():
    frame = dmatrix("1 + batch", {"batch": np.arange(12)}, return_type="dataframe")
    with pytest.raises(ValueError, match="temporal|numeric_time"):
        _kernel({2: ("1 + batch", frame, {})})
    with pytest.raises(ValueError, match="Patsy|design_info"):
        _kernel({2: ("1 + numeric_time", pd.DataFrame(np.ones((12, 2))), {})})


def test_residual_degrees_of_freedom_and_dimension_limits():
    with pytest.raises(ValueError, match="residual|observations|rows"):
        _kernel(_designs(n=4, dfs=(2,)))
    with pytest.raises(ValueError, match="10.?000|rows|observations"):
        _kernel(_designs(n=10_001, dfs=(1,), nuisance=False))
    from patsy import DesignInfo
    frame = pd.DataFrame(np.eye(130, 129), columns=[f"numeric_time_{i}" for i in range(129)])
    frame.design_info = DesignInfo(list(frame.columns))
    with pytest.raises(ValueError, match="128|columns"):
        _kernel({2: ("numeric_time", frame, {})})


@pytest.mark.parametrize("value", [0.0, 7.0, 1e200, 1e-200])
def test_constant_input_is_explicit_no_information_and_does_not_generate_reference(value):
    kernel = _kernel()
    with pytest.raises(ValueError, match="no.information|unresolved|constant"):
        kernel.evaluate(np.full(48, value))
    assert kernel.metadata()["status"] == "not_run"
    assert kernel.metadata()["completed_draws"] == 0


@pytest.mark.parametrize("kind", ["nan", "inf", "complex", "string", "boolean", "short", "matrix"])
def test_response_shape_type_and_finiteness(kind):
    y = _response()
    if kind in ("nan", "inf"):
        y[0] = np.nan if kind == "nan" else np.inf
    elif kind == "complex":
        y = y.astype(complex)
    elif kind == "string":
        y = y.astype(str)
    elif kind == "boolean":
        y = y > 0
    elif kind == "short":
        y = y[:-1]
    else:
        y = y[:, None]
    with pytest.raises(ValueError, match="numeric|real|finite|vector|length|response"):
        _kernel().evaluate(y)


@pytest.mark.parametrize("scale", [1e308, 1e-320])
def test_extreme_unrepresentable_residual_variance_is_rejected(scale):
    y = np.tile([-0.75, 0.25, 0.5], 16) * scale
    with pytest.raises(ValueError, match="variance|finite|unresolved"):
        _kernel().evaluate(y)


def test_exact_model_fit_cannot_be_reported_as_success():
    designs = _designs()
    y = np.asarray(designs[3][1]) @ np.arange(5.0)
    kernel = _kernel(designs)
    with pytest.raises(ValueError, match="variance|unresolved"):
        kernel.evaluate(y)
    assert kernel.metadata()["completed_draws"] == 0


@pytest.mark.parametrize("criterion", ["aic", "bic", "gcv"])
def test_location_nuisance_and_nonzero_scale_invariance(criterion):
    kernel = _kernel(criterion=criterion)
    y = _response()
    first = kernel.evaluate(y)
    for scale in (-7.0, 0.25, 11.0):
        transformed = scale * y + 30 + 5 * (np.arange(48) % 2)
        other = kernel.evaluate(transformed)
        assert other["selected_df"] == first["selected_df"]
        assert other["p_value"] == first["p_value"]
        assert other["conditional_p_value"] == pytest.approx(first["conditional_p_value"], abs=1e-12)
        assert other["conditional_f_statistic"] == pytest.approx(first["conditional_f_statistic"], rel=1e-12)


def test_score_ties_select_lower_df_even_with_reversed_mapping():
    designs = _designs(dfs=(2,))
    entry = designs[2]
    result = _kernel({5: entry, 2: entry}).evaluate(_response())
    assert result["candidate_scores"][2] == result["candidate_scores"][5]
    assert result["selected_df"] == 2


def test_calibrated_rank_has_positive_floor():
    designs = _designs(dfs=(1,))
    x = np.asarray(designs[1][1])
    y = 1000 * x[:, -1] + _response()
    result = _kernel(designs, draws=7).evaluate(y)
    assert result["conditional_p_value"] < 1e-10
    assert result["p_value"] == 1 / 8


def test_inclusive_tie_with_the_single_reference_draw():
    seed = 51
    y = np.random.Generator(np.random.PCG64(seed)).standard_normal((1, 48))[0]
    assert _kernel(draws=1, seed=seed).evaluate(y)["p_value"] == 1.0


def test_reference_hash_rank_order_and_batch_exchangeability():
    seed, draws = 18, 17
    kernel = _kernel(draws=draws, seed=seed)
    reference_rows = np.random.Generator(np.random.PCG64(seed)).standard_normal((draws, 48))
    reference_probabilities = np.array([
        kernel.evaluate(row)["conditional_p_value"] for row in reference_rows
    ], dtype="<f8")
    expected_hash = hashlib.sha256(reference_probabilities.tobytes(order="C")).hexdigest()
    assert kernel.metadata()["reference_probability_hash"] == expected_hash
    for row, observed in zip(reference_rows, reference_probabilities):
        expected = (1 + int(np.count_nonzero(reference_probabilities <= observed))) / (draws + 1)
        assert kernel.evaluate(row)["p_value"] == expected


def test_seed_is_reproducible_without_global_rng_mutation():
    original = np.random.get_state()
    try:
        np.random.seed(381)
        before = np.random.get_state()
        first, second, other = _kernel(seed=55), _kernel(seed=55), _kernel(seed=56)
        assert first.evaluate(_response()) == second.evaluate(_response())
        other.evaluate(_response())
        assert first.metadata()["reference_probability_hash"] == second.metadata()["reference_probability_hash"]
        assert first.metadata()["reference_probability_hash"] != other.metadata()["reference_probability_hash"]
        after = np.random.get_state()
        assert before[0] == after[0] and before[2:] == after[2:]
        np.testing.assert_array_equal(before[1], after[1])
    finally:
        np.random.set_state(original)


def test_reference_is_lazy_and_reused_with_honest_finite_metadata():
    kernel = _kernel()
    before = kernel.metadata()
    assert before["status"] == "not_run"
    assert before["requested_draws"] == 31 and before["completed_draws"] == 0
    assert before["reference_probability_hash"] is None
    assert before["generator"] == "PCG64" and before["seed"] == 22
    assert before["p_value_resolution"] == 1 / 32
    assert before["num_observations"] == 48 and before["num_candidates"] == 3
    assert before["effective_chunk_size"] == 31
    kernel.evaluate(_response())
    after = kernel.metadata()
    assert after["status"] == "completed" and after["completed_draws"] == 31
    assert len(after["reference_probability_hash"]) == 64
    kernel.evaluate(_response() + 2)
    assert kernel.metadata() == after
    assert before["candidate_matrix_hashes"] == after["candidate_matrix_hashes"]
    words = json.dumps(after["assumptions"]).lower()
    for required in ("independent", "homoskedastic", "gaussian", "fixed", "marginal", "selection",
                     "shared", "clinical", "heteroskedastic", "count", "correlated", "interval",
                     "prespecified", "gene", "family"):
        assert required in words
    json.dumps(after, allow_nan=False)


def test_inputs_and_returned_metadata_are_defensively_copied():
    designs = _designs()
    kernel = _kernel(designs)
    expected = kernel.evaluate(_response())
    metadata = kernel.metadata()
    for _, frame, _ in designs.values():
        frame.iloc[:, :] = 0
        frame.columns = [f"changed_{i}" for i in range(len(frame.columns))]
    assert kernel.evaluate(_response()) == expected
    assert kernel.metadata() == metadata
    metadata["candidate_dimensions"]["1"]["full_columns"] = 999
    metadata["candidate_matrix_hashes"]["1"] = "changed"
    assert kernel.metadata()["candidate_dimensions"]["1"]["full_columns"] == 3
    assert kernel.metadata()["candidate_matrix_hashes"]["1"] != "changed"


def test_matrix_identity_hashes_include_actual_values_and_column_names():
    first = _kernel(_designs()).metadata()
    assert first["candidate_matrix_hashes"] == _kernel(_designs()).metadata()["candidate_matrix_hashes"]
    changed = _designs()
    x = np.array(changed[3][1])
    x[:, -1] *= 2
    _replace_frame(changed, 3, x)
    second = _kernel(changed).metadata()
    assert first["candidate_matrix_hashes"]["3"] != second["candidate_matrix_hashes"]["3"]
    assert first["reduced_matrix_hash"] == second["reduced_matrix_hash"]
    assert first["candidate_matrix_hashes"]["1"] == second["candidate_matrix_hashes"]["1"]


def test_effective_chunk_enforces_the_sample_draw_candidate_bound():
    kernel = _kernel(_designs(n=10_000, dfs=(1, 2), nuisance=False), draws=2000)
    metadata = kernel.metadata()
    assert metadata["effective_chunk_size"] == 100
    assert metadata["num_observations"] * metadata["num_candidates"] * metadata["effective_chunk_size"] <= 2_000_000
    assert metadata["status"] == "not_run"


def test_chunk_bound_does_not_change_reference_generation_or_numerics(monkeypatch):
    first = _kernel(draws=17, seed=71)
    expected = first.evaluate(_response())
    monkeypatch.setattr(_module(), "_MAX_BATCH_ELEMENTS", 48 * 3 * 4)
    chunked = _kernel(draws=17, seed=71)
    assert chunked.metadata()["effective_chunk_size"] == 4
    assert chunked.evaluate(_response()) == expected
    assert chunked.metadata()["reference_probability_hash"] == first.metadata()["reference_probability_hash"]


def test_failed_reference_generation_does_not_publish_partial_completion(monkeypatch):
    monkeypatch.setattr(_module(), "_MAX_BATCH_ELEMENTS", 48 * 3 * 4)
    kernel = _kernel()
    original = kernel._evaluate_batch
    calls = 0

    def fail_on_reference(values):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise ValueError("injected numerical reference failure")
        return original(values)

    monkeypatch.setattr(kernel, "_evaluate_batch", fail_on_reference)
    with pytest.raises(ValueError, match="reference failure"):
        kernel.evaluate(_response())
    assert kernel.metadata()["status"] == "failed"
    assert kernel.metadata()["attempted_draws"] == 8
    assert kernel.metadata()["evaluated_draws"] == 4
    assert kernel.metadata()["completed_draws"] == 4
    assert kernel.metadata()["reference_probability_hash"] is None
    assert "reference failure" in kernel.metadata()["reference_error"]
    from devharness_core.provenance import canonical_json
    assert json.loads(canonical_json(kernel.metadata())) == kernel.metadata()
    monkeypatch.setattr(kernel, "_evaluate_batch", original)
    with pytest.raises(ValueError, match="previously failed"):
        kernel.evaluate(_response())
    assert kernel.metadata()["attempted_draws"] == 8
    assert kernel.metadata()["evaluated_draws"] == 4


def test_thin_basis_containment_rejects_distinct_spaces():
    basis = np.linalg.qr(np.random.Generator(np.random.PCG64(12)).normal(size=(200, 5)))[0]
    _module()._assert_contained(basis[:, :2], basis, "valid fixture")
    _module()._assert_contained(np.empty((200, 0)), basis, "empty fixture")
    with pytest.raises(ValueError, match="containment"):
        _module()._assert_contained(basis[:, 2:3], basis[:, :2], "invalid fixture")


def test_only_roundoff_sized_negative_rss_improvement_is_clipped(monkeypatch):
    kernel = _kernel(_coordinate_design())
    response = np.arange(1.0, 9.0)[None, :]
    original = _module()._resolved_rss
    increment = 1e-13

    def inject_nested_arithmetic_error(values, operator, label):
        rss, error = original(values, operator, label)
        if label.startswith("Candidate"):
            reduced_rss, _ = original(values, kernel._null_operator, "reduced fixture")
            return reduced_rss + increment, error
        return rss, error

    # Deliberately corrupt the candidate RSS, retaining the arithmetic diagnostic
    # from real evaluation. This guard check is not a statistical experiment.
    monkeypatch.setattr(_module(), "_resolved_rss", inject_nested_arithmetic_error)
    tiny = kernel._evaluate_batch(response)
    assert tiny["conditional_f_statistics"][0] == 0
    assert tiny["conditional_p_values"][0] == 1
    increment = 0.1
    with pytest.raises(ValueError, match="roundoff"):
        kernel._evaluate_batch(response)


def test_patsy_slices_must_be_complete_and_nonoverlapping():
    designs = _designs(dfs=(1,))
    slices = designs[1][1].design_info.term_name_slices
    slices["numeric_time"] = slice(0, 3)
    with pytest.raises(ValueError, match="every design column"):
        _kernel(designs)


def test_maximum_column_count_and_condition_limit_are_supported():
    values = np.eye(129, 128)
    values[0, 0] = 1000
    frame = dmatrix("0 + numeric_time", {"numeric_time": values}, return_type="dataframe")
    metadata = _kernel({128: ("numeric_time", frame, {})}, draws=1).metadata()
    assert metadata["candidate_dimensions"]["128"]["full_columns"] == 128
    assert metadata["candidate_dimensions"]["128"]["condition_number"] == pytest.approx(1000)


def test_matrix_hash_is_canonical_little_endian_and_sensitive_to_names():
    designs = _designs(dfs=(2,))
    baseline = _kernel(designs).metadata()["candidate_matrix_hashes"]["2"]
    _replace_frame(designs, 2, np.asarray(designs[2][1], dtype=">f8"))
    assert _kernel(designs).metadata()["candidate_matrix_hashes"]["2"] == baseline

    formula, original, diagnostics = designs[2]
    from patsy import DesignInfo
    renamed = pd.DataFrame(np.asarray(original), columns=[
        "Intercept", "C(batch)[T.1]", "I(numeric_time)", "I(numeric_time ** 2)",
    ], index=original.index)
    renamed.design_info = DesignInfo(list(renamed.columns))
    # Preserve actual factor terms; expanded categorical column labels are not
    # parseable formula factors and must not be used as term identities.
    renamed.design_info.term_name_slices = original.design_info.term_name_slices.copy()
    changed = _kernel({2: (formula, renamed, diagnostics)}).metadata()["candidate_matrix_hashes"]["2"]
    assert changed != baseline


def test_method_metadata_records_generation_and_numerical_policy():
    metadata = _kernel().metadata()
    assert metadata["simulation_layout"] == "draw_major"
    assert metadata["reference_distribution"] == "standard_normal"
    assert "<=" in metadata["rank_rule"]
    assert "residual_relative_tolerance" not in metadata
    assert "householder" in metadata["numerical_resolution_policy"].lower()
    assert metadata["attempted_draws"] == 0
    assert metadata["evaluated_draws"] == 0
    assert metadata["reference_error"] is None


@pytest.mark.parametrize("target", ["response", "design"])
def test_finite_wider_float_values_must_fit_float64_without_warnings(target):
    if np.finfo(np.longdouble).max <= np.finfo(np.float64).max:
        pytest.skip("This platform has no wider floating-point type")
    wide = np.longdouble("1e400")
    if target == "response":
        y = np.arange(48, dtype=np.longdouble) * wide
        with pytest.raises(ValueError, match="float64|finite"):
            _kernel().evaluate(y)
    else:
        designs = _designs(dfs=(2,))
        _replace_frame(designs, 2, np.asarray(designs[2][1], dtype=np.longdouble) * wide)
        with pytest.raises(ValueError, match="float64|finite"):
            _kernel(designs)


@pytest.mark.parametrize("n", [8, 16, 48, 64, 99, 100])
@pytest.mark.parametrize("scale,location", [(1.0, 0.0), (-7.0, 0.0), (3.0, 29.0)])
def test_exact_nuisance_only_responses_have_no_usable_residual_information(n, scale, location):
    designs = _designs(n=n)
    y = location + scale * (np.arange(n) % 2).astype(float)
    kernel = _kernel(designs, seed=22)
    with pytest.raises(ValueError, match="no.information|no usable|unresolved"):
        kernel.evaluate(y)
    assert kernel.metadata()["status"] == "not_run"
    assert kernel.metadata()["attempted_draws"] == 0
    assert kernel.metadata()["reference_probability_hash"] is None


def _coordinate_design():
    # A multicolumn temporal basis with authentic Patsy term identity, rather
    # than unrelated variable names that only contain the time-name substring.
    frame = dmatrix("0 + numeric_time", {"numeric_time": np.eye(8, 7)}, return_type="dataframe")
    return {7: ("0 + numeric_time", frame, {})}


@pytest.mark.parametrize("residual", [1e-7, 1e-30, 1e-100])
def test_exactly_resolved_small_coordinate_residual_is_admissible(residual):
    y = np.array([1.0] * 7 + [residual])
    result = _kernel(_coordinate_design()).evaluate(y)
    expected_rss = residual * residual
    expected_f = 1 / expected_rss
    expected_p = f.sf(expected_f, 7, 1)
    assert result["conditional_f_statistic"] == pytest.approx(expected_f, rel=4e-15)
    assert result["conditional_p_value"] == pytest.approx(expected_p, rel=2e-14, abs=0)
    assert result["candidate_scores"][7]["gcv"] == pytest.approx(8 * expected_rss, rel=4e-15)


@pytest.mark.parametrize("seed", [14, 17])
def test_reviewer_coordinate_reference_seeds_keep_every_prescribed_draw(seed):
    # 8 x 65,535 values: bounded exact-coordinate software regression, not a
    # calibration-rate experiment or historical reference replay.
    kernel = _kernel(_coordinate_design(), draws=65535, seed=seed)
    result = kernel.evaluate(np.arange(1.0, 9.0))
    assert 1 / 65536 <= result["p_value"] <= 1
    metadata = kernel.metadata()
    assert metadata["status"] == "completed"
    assert metadata["attempted_draws"] == 65535
    assert metadata["evaluated_draws"] == 65535
    assert metadata["completed_draws"] == 65535
    assert metadata["reference_error"] is None
    assert len(metadata["reference_probability_hash"]) == 64


def test_small_coordinate_residual_survives_dense_fitted_space_reparameterization():
    designs = _coordinate_design()
    rotation = np.linalg.qr(np.random.Generator(np.random.PCG64(2)).normal(size=(7, 7)))[0]
    _replace_frame(designs, 7, np.vstack((rotation, np.zeros((1, 7)))))
    kernel = _kernel(designs)
    result = kernel.evaluate(np.array([1.0] * 7 + [1e-100]))
    assert result["conditional_f_statistic"] == pytest.approx(1e200, rel=2e-14)
    assert kernel.metadata()["candidate_dimensions"]["7"]["qr_residual_space_error"] == 0


def test_dense_high_rank_design_keeps_bounded_roundoff_diagnostics():
    rng = np.random.Generator(np.random.PCG64(812))
    values = np.linalg.qr(rng.normal(size=(129, 128)), mode="reduced")[0]
    frame = dmatrix("0 + numeric_time", {"numeric_time": values}, return_type="dataframe")
    designs = {128: ("0 + numeric_time", frame, {})}
    y = rng.standard_normal(129)
    kernel = _kernel(designs, draws=3)
    result = kernel.evaluate(y)
    expected = _independent_fit(designs, y, "aic")[1][128]
    assert result["conditional_f_statistic"] == pytest.approx(expected["f"], rel=2e-10)
    assert result["conditional_p_value"] == pytest.approx(expected["p"], rel=2e-10)
    assert np.isfinite(kernel.metadata()["candidate_dimensions"]["128"]["qr_residual_space_error"])


def test_multicolumn_nuisance_linear_combination_has_no_information():
    rng = np.random.Generator(np.random.PCG64(81))
    data = pd.DataFrame({
        "numeric_time": np.linspace(-1, 1, 48),
        "nuisance": rng.standard_normal(48),
        "other": rng.standard_normal(48),
    })
    formula = "1 + nuisance + other + numeric_time"
    frame = dmatrix(formula, data, return_type="dataframe")
    y = 5 + 3 * data["nuisance"] - 2 * data["other"]
    kernel = _kernel({1: (formula, frame, {})})
    with pytest.raises(ValueError, match="no usable|unresolved"):
        kernel.evaluate(y)
    assert kernel.metadata()["status"] == "not_run"


def test_running_reference_metadata_reports_batch_progress(monkeypatch):
    monkeypatch.setattr(_module(), "_MAX_BATCH_ELEMENTS", 48 * 3 * 4)
    kernel = _kernel(draws=9)
    original = kernel._evaluate_batch
    progress = []

    def observe(values):
        metadata = kernel.metadata()
        from devharness_core.provenance import canonical_json
        assert json.loads(canonical_json(metadata)) == metadata
        progress.append((metadata["status"], metadata["attempted_draws"], metadata["evaluated_draws"]))
        return original(values)

    monkeypatch.setattr(kernel, "_evaluate_batch", observe)
    kernel.evaluate(_response())
    assert progress == [("not_run", 0, 0), ("running", 4, 0), ("running", 8, 4), ("running", 9, 8)]
    assert kernel.metadata()["status"] == "completed"
    assert kernel.metadata()["completed_draws"] == 9


def test_metadata_passes_strict_provenance_canonical_json():
    from devharness_core.provenance import canonical_json

    kernel = _kernel()
    before = kernel.metadata()
    assert json.loads(canonical_json(before)) == before
    kernel.evaluate(_response())
    after = kernel.metadata()
    assert json.loads(canonical_json(after)) == after


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("phase", ["start", "initialization", "first_block", "finalization", "publication", "completion"])
def test_reference_interruptions_fail_terminally_and_reraise_original(monkeypatch, exception_type, phase):
    module = _module()
    interruption = exception_type("synthetic cancellation")

    class InterruptibleKernel(module.GaussianSelectionMC):
        def __setattr__(self, name, value):
            super().__setattr__(name, value)
            if ((phase == "start" and name == "_reference_status" and value == "running")
                    or (phase == "publication" and name == "_reference_probabilities" and value is not None)
                    or (phase == "completion" and name == "_reference_status" and value == "completed")):
                raise interruption

    kernel = InterruptibleKernel(_coordinate_design(), "aic", draws=7, seed=4)
    original = kernel._evaluate_batch
    calls = 0

    def cancel(*args, **kwargs):
        raise interruption

    def cancel_reference_block(values):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise interruption
        return original(values)

    with monkeypatch.context() as scoped:
        if phase == "initialization":
            scoped.setattr(module.np.random, "Generator", cancel)
        elif phase == "first_block":
            scoped.setattr(kernel, "_evaluate_batch", cancel_reference_block)
        elif phase == "finalization":
            scoped.setattr(module.hashlib, "sha256", cancel)
        with pytest.raises(exception_type) as raised:
            kernel.evaluate(np.arange(1.0, 9.0))
        assert raised.value is interruption

    failed = kernel.metadata()
    expected_attempted = 0 if phase in ("start", "initialization") else 7
    expected_evaluated = 7 if phase in ("finalization", "publication", "completion") else 0
    assert failed["status"] == "failed"
    assert failed["attempted_draws"] == expected_attempted
    assert failed["evaluated_draws"] == expected_evaluated
    assert failed["completed_draws"] == expected_evaluated
    assert failed["reference_error"] == f"{exception_type.__name__}: synthetic cancellation"
    assert failed["reference_probability_hash"] is None
    assert kernel._reference_probabilities is None
    assert kernel._reference_hash is None
    with pytest.raises(ValueError, match="previously failed"):
        kernel.evaluate(np.arange(1.0, 9.0))
    assert kernel.metadata() == failed
