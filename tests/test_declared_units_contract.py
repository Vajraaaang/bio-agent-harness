"""Metadata-only regression cases; no expression model or dataset generator.

These cases remain unexecuted until the no-data test scope is clarified.
"""


def test_unique_specimens_do_not_hide_one_shared_biological_unit():
    import pandas as pd
    from devharness_server.tools_stat import _biological_unit_diagnostics
    metadata = pd.DataFrame({"sample_id": ["a", "b", "c"],
        "specimen_id": ["s1", "s2", "s3"], "biological_unit_id": ["unit1"] * 3})
    assumption, diagnostics = _biological_unit_diagnostics(metadata)
    assert assumption["repeated_unit_columns"] == ["biological_unit_id"]
    assert assumption["verification_status"] == "repeated_units_detected"
    assert diagnostics[0]["severity"] == "error"


def test_incomplete_declared_units_are_not_counted_as_complete():
    import pandas as pd
    from devharness_core.units import declared_unit_counts
    counts, incomplete = declared_unit_counts(pd.DataFrame({"specimen_id": ["a", "b"],
                                                          "biological_unit_id": ["unknown", "x"]}))
    assert counts == {"specimen_id": 2}
    assert incomplete == ["biological_unit_id"]


def test_continuous_endpoint_outputs_do_not_invent_abundance_fold_changes():
    import numpy as np
    import pandas as pd
    from patsy import dmatrix
    from devharness_server.tools_stat import _effect_estimate

    class Fitted:
        params = np.array([5.0, 2.0])
        def cov_params(self):
            return np.eye(2)

    metadata = pd.DataFrame({"numeric_time": [0.0, 1.0, 2.0]})
    design = dmatrix("1 + numeric_time", metadata, return_type="dataframe")
    result = _effect_estimate(Fitted(), design, metadata, discrete=False)
    assert result["effect_difference"] == 4.0
    assert result["log2_fold_change"] is None
    assert result["effect_scale"] == "expression_units"
    assert result["time_grid"] == [0.0, 1.0, 2.0]
    assert result["predicted_curve"] == [5.0, 7.0, 9.0]
