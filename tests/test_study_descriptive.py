"""Only denial/configuration checks run; numerical execution is not authorized."""

import builtins
import importlib
from pathlib import Path

import pytest


def api():
    return importlib.import_module("devbench.study.descriptive")


def test_import_does_not_load_data_or_scientific_runtime(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert name.split(".")[0] not in {
            "numpy", "pandas", "scipy", "zarr", "h5py", "devharness_core", "devharness_server",
        }
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    importlib.reload(api())


@pytest.mark.parametrize("permission", [None, False, 1, "true", [], {}])
@pytest.mark.parametrize("entrypoint", ["summarize_observed_expression", "_summarize_worker"])
def test_default_denial_precedes_import_validation_files_and_worker_launch(monkeypatch, permission, entrypoint):
    module = api()
    original_import = builtins.__import__

    def forbidden(*args, **kwargs):
        pytest.fail("Default-denied summary touched an input, scientific import or execution path")

    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in {"numpy", "pandas", "zarr", "devharness_core", "devharness_server"}:
            forbidden()
        return original_import(name, *args, **kwargs)

    kwargs = {} if permission is None else {"allow_data_execution": permission}
    args = (object(), object())
    if entrypoint == "summarize_observed_expression":
        kwargs.update(gene_ids=object(), measurement_type=object())
    else:
        args += (object(), object())
    with monkeypatch.context() as blocked:
        blocked.setattr(builtins, "__import__", guarded_import)
        blocked.setattr(builtins, "open", forbidden)
        blocked.setattr(module, "_validate_request", forbidden)
        for name in ("resolve", "open", "stat", "exists", "is_absolute"):
            blocked.setattr(Path, name, forbidden)
        with pytest.raises(module.DataExecutionDisabled, match="allow_data_execution=True"):
            getattr(module, entrypoint)(*args, **kwargs)


@pytest.mark.parametrize("gene_ids,measurement_type,time_col", [
    ([], "continuous", "numeric_time"),
    (("g", "g"), "continuous", "numeric_time"),
    ((" ",), "continuous", "numeric_time"),
    ((1,), "continuous", "numeric_time"),
    (("g" * 201,), "continuous", "numeric_time"),
    ([str(index) for index in range(5001)], "continuous", "numeric_time"),
    ("g", "continuous", "numeric_time"),
    (("g",), "RPKM", "numeric_time"),
    (("g",), [], "numeric_time"),
    (("g",), "continuous", ""),
    (("g",), "continuous", object()),
])
def test_invalid_requests_are_configuration_only(monkeypatch, gene_ids, measurement_type, time_col):
    module = api()

    def forbidden(*args, **kwargs):
        pytest.fail("Request validation must not access data")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "resolve", forbidden)
    with pytest.raises(ValueError):
        module._validate_request(gene_ids, measurement_type, time_col)


def test_complete_family_validation_preserves_order_without_scientific_execution(monkeypatch):
    module = api()

    def forbidden(*args, **kwargs):
        pytest.fail("Configuration validation must not access files")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    assert module._validate_request(["gene-b", "gene-a"], "continuous", "numeric_time") == ("gene-b", "gene-a")
    assert module.MAX_GENES == 5000
    assert module.MAX_METADATA_BYTES == 5_000_000
    assert module.MAX_PANEL_BYTES == 16 * 1024 * 1024
    assert module.MAX_CHUNK_BYTES == 4 * 1024 * 1024
    assert module.MAX_MATRIX_BYTES == 100_000_000
    assert (module.MIN_TIMEPOINTS, module.MAX_TIMEPOINTS) == (3, 100)


def array_header(**updates):
    return {
        "zarr_format": 2, "shape": [3, 6], "chunks": [3, 6], "dtype": "<f8",
        "compressor": {"id": "zlib", "level": 1}, "filters": None,
        **updates,
    }


def test_zarr_header_validation_is_pure_configuration(monkeypatch):
    module = api()
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert name.split(".")[0] not in {"numpy", "zarr", "devharness_core", "devharness_server"}
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert module._validate_array_header(array_header(), name="expression", shape=(3, 6), measurement_type="continuous") == 144
    labels = array_header(shape=[3], chunks=[3], dtype="<U20")
    assert module._validate_array_header(labels, name="gene_names", shape=(3,), measurement_type="continuous") == 240


@pytest.mark.parametrize("updates", [
    {"zarr_format": 3}, {"shape": [3, 7]}, {"shape": [True, 6]},
    {"dtype": "|O"}, {"dtype": "<f16"}, {"dtype": "<c16"}, {"dtype": "<f1"},
    {"chunks": [1_000_000, 6]}, {"chunks": [0, 6]}, {"chunks": [True, 6]},
    {"chunks": None}, {"compressor": {"id": "pickle"}}, {"compressor": "zlib"},
    {"filters": [{"id": "vlen-utf8"}]},
])
def test_unsafe_zarr_declarations_are_rejected_without_opening_store(updates):
    with pytest.raises(ValueError):
        api()._validate_array_header(array_header(**updates), name="expression", shape=(3, 6), measurement_type="continuous")


def test_prepared_count_storage_does_not_coerce_floating_measurements_to_counts():
    with pytest.raises(ValueError, match="integer dtype"):
        api()._validate_array_header(array_header(), name="expression", shape=(3, 6), measurement_type="raw_counts")


numerical = pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Small local numerical/report fixtures require opt-in")


def _prepared_inputs(tmp_path, values, *, genes=None, times=None, batches=None, order=None):
    """Called only by skipped numerical cases, never during module collection."""
    import csv
    import numpy as np
    import pandas as pd
    from devharness_core.storage import ChunkedExpressionStore

    array = np.asarray(values)
    genes = genes or [f"gene-{index}" for index in range(len(array))]
    samples = [f"sample-{index}" for index in range(array.shape[1])]
    times = times or [0, 0, 1, 1, 2, 2]
    batches = batches or ["batch-a", "batch-b", "batch-a", "batch-b", "batch-b", "batch-b"]
    counts = tmp_path / "counts.zarr"
    with ChunkedExpressionStore.from_dataframe_to_zarr(pd.DataFrame(array, index=genes, columns=samples), counts):
        pass
    metadata = tmp_path / "metadata.csv"
    with metadata.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sample_id", "numeric_time", "batch", "biological_unit_id", "technical_replicate_id"])
        for index in (order if order is not None else range(len(samples))):
            writer.writerow([samples[index], times[index], batches[index], "unverified-unit", f"technical-{index}"])
    return counts, metadata, genes


def _assert_no_inferential_fields(value):
    if isinstance(value, dict):
        assert not set(value) & {"p_value", "q_value", "claimed_discovery", "confidence_interval", "confidence_level", "significant"}
        for nested in value.values():
            _assert_no_inferential_fields(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_inferential_fields(nested)


@numerical
def test_continuous_means_keep_fractional_negative_scale_and_observed_batch_mix(tmp_path):
    counts, metadata, genes = _prepared_inputs(tmp_path, [[-2.0, 1.0, 2.0, 6.0, 8.0, 10.0], [5.25] * 6], order=[5, 1, 3, 0, 4, 2])
    result = api().summarize_observed_expression(counts, metadata, gene_ids=genes,
                                                measurement_type="continuous", allow_data_execution=True)
    assert result["time_grid"] == [0.0, 1.0, 2.0]
    assert result["sample_counts"] == [2, 2, 2]
    assert result["genes"][genes[0]]["mean_curve"] == [-0.5, 4.0, 9.0]
    assert result["genes"][genes[0]]["effect"] == 9.5
    assert result["genes"][genes[0]]["effect_units"] == "expression_difference"
    assert result["genes"][genes[1]]["effect"] == 0.0
    assert result["reference"] == {"reference_batch": None, "library_factor": None,
                                  "batch_reference_policy": "observed_batch_mix", "biological_unit_reference": "observed_subset"}
    assert result["time_composition"][0]["batch_counts"] == {"batch-a": 1, "batch-b": 1}
    assert result["time_composition"][-1]["batch_counts"] == {"batch-b": 2}
    assert result["normalization"]["method"] == "none_continuous_input"
    assert result["size_factors"] is None
    assert result["inference_scope"] == "descriptive"
    assert any("technical replicates are not independent" in text for text in result["limitations"])
    _assert_no_inferential_fields(result)


@numerical
def test_counts_use_whole_family_rle_and_keep_zero_endpoint_effect_missing(tmp_path):
    values = [[10] * 6, [30] * 6, [20, 20, 40, 40, 80, 80], [0, 0, 1, 1, 2, 2], [0] * 6]
    counts, metadata, genes = _prepared_inputs(tmp_path, values)
    result = api().summarize_observed_expression(counts, metadata, gene_ids=list(reversed(genes)),
                                                measurement_type="raw_counts", allow_data_execution=True)
    assert set(result["normalization"]["gene_ids"]) == set(genes)
    assert result["normalization"]["panel_selection"] == "all_declared_genes"
    assert result["normalization"]["panel_bytes"] == 5 * 6 * 8
    assert list(result["size_factors"].values()) == pytest.approx([1.0] * 6)
    assert result["genes"][genes[2]]["mean_curve"] == pytest.approx([20, 40, 80])
    assert result["genes"][genes[2]]["effect"] == pytest.approx(2.0)
    for gene in genes[3:]:
        assert result["genes"][gene]["status"] == "described"
        assert result["genes"][gene]["effect"] is None
        assert result["genes"][gene]["effect_units"] is None
        assert result["genes"][gene]["missing_reason"] == "descriptive_summary_no_hypothesis_test"
        assert result["genes"][gene]["effect_missing_reason"] == "zero_endpoint_mean"
    _assert_no_inferential_fields(result)


@numerical
def test_counts_disclose_positive_count_rle_without_library_total_fallback(tmp_path):
    counts, metadata, genes = _prepared_inputs(tmp_path, [[1, 1, 0, 0, 1, 1], [0, 0, 1, 1, 1, 1]])
    result = api().summarize_observed_expression(counts, metadata, gene_ids=genes,
                                                measurement_type="raw_counts", allow_data_execution=True)
    assert result["normalization"]["method"] == "median_of_ratios_poscounts"
    assert result["normalization"]["scope"] == "entire_declared_gene_family"
    _assert_no_inferential_fields(result)


@numerical
@pytest.mark.parametrize("values,measurement_type", [
    ([[0] * 6], "raw_counts"),
    ([[-1, 1, 1, 1, 1, 1]], "raw_counts"),
    ([[2**53, 1, 1, 1, 1, 1]], "raw_counts"),
    ([[float("nan"), 1, 1, 1, 1, 1]], "continuous"),
])
def test_invalid_values_and_unavailable_normalization_fail_without_estimates(tmp_path, values, measurement_type):
    from devharness_core.watchdog import RemoteWorkerError

    counts, metadata, genes = _prepared_inputs(tmp_path, values)
    with pytest.raises(RemoteWorkerError):
        api().summarize_observed_expression(counts, metadata, gene_ids=genes,
                                            measurement_type=measurement_type, allow_data_execution=True)


@numerical
def test_complete_family_mismatch_is_not_silently_sampled(tmp_path):
    from devharness_core.watchdog import RemoteWorkerError

    counts, metadata, genes = _prepared_inputs(tmp_path, [[1.0] * 6, [2.0] * 6])
    with pytest.raises(RemoteWorkerError):
        api().summarize_observed_expression(counts, metadata, gene_ids=genes[:1],
                                            measurement_type="continuous", allow_data_execution=True)


@numerical
@pytest.mark.parametrize("order", [[0, 0, 2, 3, 4, 5], [0, 1, 2, 3, 4]])
def test_metadata_sample_alignment_is_bijective_without_dropping_libraries(tmp_path, order):
    from devharness_core.watchdog import RemoteWorkerError

    counts, metadata, genes = _prepared_inputs(tmp_path, [[1.0] * 6], order=order)
    with pytest.raises(RemoteWorkerError):
        api().summarize_observed_expression(counts, metadata, gene_ids=genes,
                                            measurement_type="continuous", allow_data_execution=True)


@numerical
def test_time_order_is_numeric_and_independent_of_metadata_row_order(tmp_path):
    counts, metadata, genes = _prepared_inputs(tmp_path, [[1.0, 1.0, 2.0, 2.0, 3.0, 3.0]],
                                               times=[10, 10, 2, 2, 1, 1])
    result = api().summarize_observed_expression(counts, metadata, gene_ids=genes,
                                                measurement_type="continuous", allow_data_execution=True)
    assert result["time_grid"] == [1.0, 2.0, 10.0]
    assert result["genes"][genes[0]]["mean_curve"] == [3.0, 2.0, 1.0]
    assert result["genes"][genes[0]]["effect"] == -2.0


@numerical
@pytest.mark.parametrize("times", [[0, 0, 0, 1, 1, 1], [0, 0, 1, 1, float("inf"), 2]])
def test_invalid_numeric_time_contract_is_refused(tmp_path, times):
    from devharness_core.watchdog import RemoteWorkerError

    counts, metadata, genes = _prepared_inputs(tmp_path, [[1.0] * 6], times=times)
    with pytest.raises(RemoteWorkerError):
        api().summarize_observed_expression(counts, metadata, gene_ids=genes,
                                            measurement_type="continuous", allow_data_execution=True)
