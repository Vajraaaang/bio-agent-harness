"""Fresh normalization evidence describes the observations actually fitted."""
from dataclasses import replace

import numpy as np
import pandas as pd

from devbench.study.biology_tool_io import ObservedData
from devbench.study.biology_tools_bulk import _fit_family


def observed_counts():
    # Unsorted donor labels make accidental sorting/relabeling observable.
    sample_ids = ("donor7", "donor2", "donor8", "donor1", "donor6", "donor3", "donor5", "donor4")
    values = np.array([[10 + (i * 3 + j * 5) % 9 + (j % 3 if i >= 4 else 0)
                        for i in range(8)] for j in range(12)], dtype=float)
    values *= np.array([1, 2, 1, 3, 2, 1, 4, 1])
    metadata = pd.DataFrame({"condition": ["A"] * 4 + ["B"] * 4,
                             "donor_id": sample_ids}, index=sample_ids)
    data = ObservedData(expression=values, gene_ids=tuple(f"gene{j}" for j in range(12)),
        sample_ids=sample_ids, metadata=metadata, annotations={}, measurement="raw_counts",
        unit_hierarchy={}, targets=(), provenance={})
    design = pd.DataFrame({"intercept": np.ones(8), "condition_B": [0] * 4 + [1] * 4}, index=sample_ids)
    return data, design


def test_retained_factors_are_the_actual_glm_offsets_in_fitted_observation_order(monkeypatch):
    import statsmodels.api as sm

    data, design = observed_counts()
    glm = sm.GLM
    offsets = []

    def observed_glm(*args, **kwargs):
        if kwargs.get("offset") is not None:
            offsets.append(np.asarray(kwargs["offset"]).copy())
        return glm(*args, **kwargs)

    monkeypatch.setattr(sm, "GLM", observed_glm)
    diagnostics = {}
    fits, failures = _fit_family(data, design, diagnostics)
    assert fits and offsets, failures
    normalization = diagnostics["normalization"]
    factors = normalization["per_sample_size_factors"]
    assert list(factors) == list(data.sample_ids)
    retained = np.array([factors[sample] for sample in data.sample_ids])
    assert not np.allclose(retained, 1.0)
    assert np.isfinite(retained).all() and (retained > 0).all()
    for offset in offsets:
        np.testing.assert_array_equal(np.log(retained), offset)
    assert normalization["observation_ids"] == list(design.index)
    assert normalization["observation_scope"] == "fitted_observations"
    assert normalization["evidence_source"] == "fresh_fit"


def test_library_sizes_cover_the_actual_fitted_complete_declared_gene_family():
    data, design = observed_counts()
    diagnostics = {}
    _fit_family(data, design, diagnostics)
    normalization = diagnostics["normalization"]
    totals = normalization["per_sample_library_sizes"]
    assert list(totals) == list(data.sample_ids)
    assert totals == dict(zip(data.sample_ids, data.expression.sum(axis=0)))
    assert normalization["gene_universe"] == "complete_declared_gene_family"
    assert normalization["library_size_gene_count"] == len(data.gene_ids)


def test_continuous_fit_does_not_invent_count_factors_or_library_sizes():
    data, design = observed_counts()
    diagnostics = {}
    fits, failures = _fit_family(replace(data, measurement="continuous"), design, diagnostics)
    assert fits, failures
    assert "per_sample_size_factors" not in diagnostics.get("normalization", {})
    assert "per_sample_library_sizes" not in diagnostics.get("normalization", {})
