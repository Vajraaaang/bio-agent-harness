"""Formula-only regression checks: no expression data or numerical fits."""
import pytest

from devharness_server import tools_stat


@pytest.mark.parametrize("formula", [
    "1 + numeric_time_proxy", "1 + C(numeric_time_batch)",
    "1 + Q('numeric_time_proxy')", "1 + Q('a numeric_time label')",
])
def test_temporal_presence_requires_the_actual_variable(formula):
    with pytest.raises(ValueError, match="numeric_time"):
        tools_stat._spline_formula(formula, 3)


def test_spline_transformation_preserves_quoted_nuisance_names():
    value = tools_stat._spline_formula("1 + numeric_time + Q('a numeric_time label')", 3)
    assert "Q('a numeric_time label')" in value
    assert "cr(numeric_time, df=3, constraints='center')" in value


@pytest.mark.parametrize("term, expected", [
    ("numeric_time_proxy", False), ("C(numeric_time_batch)", False),
    ("Q('a numeric_time label')", False), ("numeric_time", True),
    ("cr(numeric_time, df=3, constraints='center')", True),
    ("C(batch):numeric_time", True), ("Q('numeric_time')", True),
])
def test_temporal_hypothesis_uses_parsed_variables(term, expected):
    predicate = getattr(tools_stat, "_uses_temporal_variable", None)
    assert callable(predicate), "Temporal hypothesis needs parsed variable identity"
    assert predicate(term) is expected


@pytest.mark.parametrize("formula, expected", [
    ("1 + numeric_time", 3),
    ("1 + cr(Q('numeric_time'), df=2)", 2),
    ("1 + cr(numeric_time, constraints='center', df=4)", 4),
    ("1 + cr(numeric_time, 2) + cr(nuisance, df=7)", 2),
])
def test_fixed_df_uses_the_prespecified_temporal_formula(formula, expected):
    from devharness_core import design_terms
    reader = getattr(design_terms, "specified_temporal_df", None)
    assert callable(reader), "Fixed df must use parsed temporal declarations"
    assert reader(formula) == expected


@pytest.mark.parametrize("formula", [
    "cr(numeric_time, df=2) + cr(numeric_time, df=4)",
    "cr(numeric_time, df=unknown_df)", "cr(numeric_time, df=True)",
])
def test_fixed_df_rejects_ambiguous_or_nonliteral_choices(formula):
    from devharness_core import design_terms
    reader = getattr(design_terms, "specified_temporal_df", None)
    assert callable(reader), "Fixed df must use parsed temporal declarations"
    with pytest.raises(ValueError, match="df"):
        reader(formula)
