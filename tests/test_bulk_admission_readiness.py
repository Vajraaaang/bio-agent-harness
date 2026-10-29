"""Admission controls for complete bulk families and declared paired designs."""
import csv
import json

import pytest

from tests.test_registered_bulk_contrast import bulk_inputs


def test_episode_admission_retains_explicit_empty_batches(tmp_path):
    from devbench.analysis.episodes import normalize_episode_configuration

    task, _, _ = bulk_inputs(tmp_path)
    task["analysis_columns"]["batches"] = []
    normalized = normalize_episode_configuration(question="Compare B with A", assay="bulk", public_task=task)
    options = normalized["fixed_options"]["mean_condition_analysis"]
    assert options["batch_cols"] == []


def test_declared_block_survives_public_projection_and_episode_binding(tmp_path):
    from devbench.analysis.episodes import normalize_episode_configuration
    from devbench.study.biology_tool_io import validate_public_task

    task, _, _ = bulk_inputs(tmp_path, continuous=False)
    task["analysis_columns"]["blocking"] = ["donor_id"]
    assert validate_public_task(task)["analysis_columns"]["blocking"] == ["donor_id"]
    normalized = normalize_episode_configuration(question="Compare B with A within donor", assay="bulk", public_task=task)
    assert normalized["fixed_options"]["mean_condition_analysis"]["block_cols"] == ["donor_id"]


@pytest.mark.parametrize("blocking", [["batch"], ["donor_id", "batch"], "donor_id"])
def test_public_block_requires_one_declared_biological_unit(tmp_path, blocking):
    from devbench.study.biology_tool_io import validate_public_task

    task, _, _ = bulk_inputs(tmp_path, continuous=False)
    task["analysis_columns"]["blocking"] = blocking
    with pytest.raises(ValueError, match="block"):
        validate_public_task(task)


def test_registered_adapter_preserves_declared_block(tmp_path):
    from devharness_server.tools_bulk import validate_bulk_contrast_parameters

    task, _, options = bulk_inputs(tmp_path, continuous=False)
    task["analysis_columns"]["blocking"] = ["donor_id"]
    options["block_cols"] = ["donor_id"]
    actual_task, actual_options = validate_bulk_contrast_parameters(task, options)
    assert actual_task["analysis_columns"]["blocking"] == ["donor_id"]
    assert actual_options["block_cols"] == ["donor_id"]


def test_registered_adapter_cannot_drop_declared_block(tmp_path):
    from devharness_server.tools_bulk import validate_bulk_contrast_parameters

    task, _, options = bulk_inputs(tmp_path, continuous=False)
    task["analysis_columns"]["blocking"] = ["donor_id"]
    with pytest.raises(ValueError):
        validate_bulk_contrast_parameters(task, options)


def test_complete_5001_family_survives_admission_and_csv_loader(tmp_path):
    from devbench.analysis.episodes import normalize_episode_configuration
    from devbench.study.biology_tool_io import _read_csv_expression
    from devharness_server.tools_bulk import validate_bulk_contrast_parameters

    task, paths, options = bulk_inputs(tmp_path, continuous=False)
    genes = [f"gene_{index}" for index in range(5001)]
    task["gene_ids"] = genes
    with paths["expression"].open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["gene_id", *[f"s{index}" for index in range(8)]])
        writer.writerows([gene, *[10 + index for index in range(8)]] for gene in genes)
    normalized = normalize_episode_configuration(question="Compare B with A", assay="bulk", public_task=task)
    assert normalized["public_task"]["gene_ids"] == genes
    admitted, _ = validate_bulk_contrast_parameters(task, options)
    assert admitted["gene_ids"] == genes
    values, actual_genes, samples, _ = _read_csv_expression(paths["expression"], "raw_counts")
    assert actual_genes == tuple(genes)
    assert values.shape == (5001, 8)
    assert len(samples) == 8


def test_synthetic_generation_limit_is_not_enlarged():
    from devbench.study.biology_input_limits import PUBLIC_MAX_FEATURES

    assert PUBLIC_MAX_FEATURES == 5000


def test_complete_5001_bulk_family_is_supported_by_zarr_reader(tmp_path):
    import numpy as np
    import zarr
    from devbench.study.biology_tool_io import _read_zarr_expression
    from devbench.study.zarr_bounds import validate_prepared_zarr

    path = (tmp_path / "counts.zarr").resolve()
    group = zarr.open_group(str(path), mode="w")
    group.create_dataset("expression", data=np.ones((5001, 2), dtype="int64"), chunks=(5001, 2), compressor=None)
    genes = np.array([f"g{index}" for index in range(5001)], dtype="U5")
    group.create_dataset("gene_names", data=genes, chunks=(5001,), compressor=None)
    group.create_dataset("sample_names", data=np.array(["s0", "s1"], dtype="U2"), chunks=(2,), compressor=None)
    # The old default remains bounded for its existing generation/transport callers.
    with pytest.raises(ValueError, match="bounded declared family"):
        validate_prepared_zarr(path, measurement_type="raw_counts")
    values, actual_genes, _, _ = _read_zarr_expression(path, "raw_counts")
    assert values.shape == (5001, 2)
    assert actual_genes == tuple(genes)


def test_broker_rejects_removing_declared_fixed_block(tmp_path):
    from devbench.study.biology_broker import validate_biology_request

    task, _, _ = bulk_inputs(tmp_path, continuous=False)
    task["analysis_columns"]["blocking"] = ["donor_id"]
    with pytest.raises(ValueError, match="block"):
        validate_biology_request("mean_condition_analysis", {"options": {"block_cols": []}}, task, enforced=True)


def test_registered_paired_count_fit_keeps_block_and_four_biological_units(tmp_path):
    from tests.test_airway_bulk_readiness import paired_data
    from devharness_server.dispatcher import ToolDispatcher

    data = paired_data()
    task, paths, options = bulk_inputs(tmp_path, continuous=False)
    task["unit_hierarchy"] = data.unit_hierarchy
    task["population_unit_label"] = "cell"
    task["analysis_columns"] = data.context["analysis_columns"]
    task["gene_ids"] = list(data.gene_ids)
    options.update(batch_cols=[], block_cols=["cell"])
    data.metadata.to_csv(paths["metadata"], index=False)
    with paths["expression"].open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["gene_id", *data.sample_ids])
        writer.writerows([gene, *values.astype(int)] for gene, values in zip(data.gene_ids, data.expression))
    raw = ToolDispatcher(allowed_root=tmp_path).execute("bulk_condition_contrast", {
        "counts_path": str(paths["expression"]), "metadata_path": str(paths["metadata"]),
        "public_task": task, "options": options})
    result = json.loads(raw)
    assert result["method"]["count_model"] == "negative_binomial_empirical_bayes_dispersion"
    assert result["diagnostics"]["biological_units"]["analysis_unit_count"] == 4
    assert result["diagnostics"]["biological_units"]["analysis_observation_count"] == 8
    design = result["diagnostics"]["designs"][0]
    assert design["rank"] == 5 and design["residual_df"] == 3
    assert design["block_columns"] == ["cell"]
    rows = result["target_results"][0]["payload"]["gene_statistics"]
    assert [row["gene_id"] for row in rows] == list(data.gene_ids)
    assert rows[-1]["p_value"] is None and rows[-1]["q_value"] is None
    assert all(row["q_value"] >= row["p_value"] for row in rows if row["p_value"] is not None)


def test_episode_source_identity_binds_bulk_zarr_preflight(monkeypatch):
    from devbench.analysis import episodes

    key = "devbench/study/zarr_bounds.py"
    before = episodes._sources()
    assert key in before
    original = episodes.sha256
    monkeypatch.setattr(episodes, "sha256", lambda path: "f" * 64 if path.name == "zarr_bounds.py" else original(path))
    assert episodes._sources()[key] == "f" * 64
    assert before[key] != episodes._sources()[key]
