"""Exercise full-family columnar transport independently of numerical fitting."""

import pyarrow.parquet as pq

from devbench.core.population_contract import content_sha256
from devbench.platform.population_parquet import (
    verify_population_family,
    write_population_family,
)


def test_population_parquet_retains_more_than_legacy_row_limit(workspace_tmp_path):
    genes = [f"gene_{index}" for index in range(50_001)]
    stages = tuple(f"stage_{index}" for index in range(4))
    contrasts = tuple(f"comparison_{index}" for index in range(4))
    data = [
        {
            "gene_id": gene,
            "stage": stage,
            "contrast_id": contrast,
            "estimate_status": "not_estimable",
            "failure_code": "synthetic_unavailable",
        }
        for gene in genes
        for stage, contrast in zip(stages, contrasts, strict=True)
    ]
    path = workspace_tmp_path / "family.parquet"
    result = {
        "source_sha256": "a" * 64,
        "specification_sha256": "b" * 64,
        "design": {"design_sha256": "c" * 64, "contrasts": [
            {"contrast_id": contrast} for contrast in contrasts
        ]},
        "testing_family": {
            "attempted_n": len(data),
            "stages": list(stages),
            "feature_ids_sha256": content_sha256(genes),
        },
        "summary": {
            "estimated_hypothesis_count": 0,
            "failed_hypothesis_count": 0,
            "refused_hypothesis_count": 0,
            "not_estimable_hypothesis_count": len(data),
        },
    }
    result["result_artifact"] = write_population_family(path, data, result)
    assert pq.ParquetFile(path).metadata.num_rows == 200_004
    verify_population_family(path, result)


def test_paired_200_gene_inference_matches_statsmodels_and_both_transports(
    workspace_tmp_path, monkeypatch,
):
    import numpy as np
    from threadpoolctl import threadpool_limits

    from devbench.core import population_statistics
    from devbench.core.estimators import StatsmodelsEstimator
    from devbench.core.family_fitting import NB_RESIDUAL_DF_REFERENCE
    from devbench.platform.scrna_population_analysis import (
        run_population_analysis,
        run_population_contrast_parquet,
    )
    from tests.test_workbench_singlecell import _paired_scale_fixture

    source, spec = _paired_scale_fixture(workspace_tmp_path, genes=200)
    original_fit = population_statistics._fit_family

    def fit_reference(*args, **kwargs):
        return original_fit(*args, estimator=StatsmodelsEstimator(), **kwargs)

    with threadpool_limits(limits=1, user_api="blas"):
        with monkeypatch.context() as patch:
            patch.setattr(population_statistics, "_fit_family", fit_reference)
            reference = run_population_analysis(source, spec, "contrast")
        legacy = run_population_analysis(source, spec, "contrast")
        path = workspace_tmp_path / "paired-family.parquet"
        binary = run_population_contrast_parquet(source, spec, path)

    rows = legacy["gene_statistics"]
    expected = reference["gene_statistics"]
    assert legacy["diagnostics"]["count_test_reference"] == NB_RESIDUAL_DF_REFERENCE
    assert binary["status"] == legacy["status"] == reference["status"] == "completed"
    assert len(rows) == 400
    assert pq.read_table(path).to_pylist() == rows
    assert all(row["estimate_status"] == "estimated" for row in rows)
    assert all(row["estimate_status"] == "estimated" for row in expected)
    for field in ("log2_fold_change", "standard_error", "p_value", "q_value"):
        np.testing.assert_allclose(
            [row[field] for row in rows], [row[field] for row in expected],
            rtol=0, atol=1e-6, err_msg=field,
        )
