"""Explicit measurement semantics for small exploratory geometry fixtures."""
import inspect
import json

import numpy as np
import pandas as pd
import pytest


def integer_continuous_fixture(root):
    coordinate = np.arange(1, 25, dtype=float)
    values = np.vstack([coordinate * 1000, 2.0 ** (coordinate % 10), coordinate ** 2, coordinate])
    genes = ["large_linear", "cyclic", "quadratic", "small_linear"]
    samples = [f"s{i:03d}" for i in range(len(coordinate))]
    expression = root / "expression.csv"
    metadata = root / "metadata.csv"
    pd.DataFrame(values, index=genes, columns=samples).to_csv(expression, index_label="gene_id")
    pd.DataFrame({"sample_id": samples, "collection_order": np.arange(len(samples))}).to_csv(metadata, index=False)
    return expression, metadata, samples, genes, values


def test_lineage_exposes_measurement_in_public_and_sdk_signatures():
    from devharness_server.tools_lineage import infer_lineage
    from devharness.trajectory import infer_lineage as sdk
    assert "data_type" in inspect.signature(infer_lineage).parameters
    assert "data_type" in inspect.signature(sdk).parameters


def test_integer_valued_continuous_input_uses_raw_variance_and_no_log_transform(tmp_path):
    from devharness_server.tools_lineage import infer_lineage
    expression, metadata, samples, genes, values = integer_continuous_fixture(tmp_path)
    result = infer_lineage(str(expression), str(metadata), samples[0], top_genes=3,
                           n_components=2, n_neighbors=23, n_clusters=3, data_type="continuous")
    expected = sorted(range(len(genes)), key=lambda i: (-np.var(values[i]), genes[i]))[:3]
    assert result["provenance"]["selected_genes"] == [genes[i] for i in expected]
    assert result["provenance"]["transform"] == "continuous expression; gene-wise z-score"
    assert result["provenance"]["requested_data_type"] == "continuous"
    assert result["provenance"]["resolved_data_type"] == "continuous"
    assert result["provenance"]["measurement_declaration"] == "explicit_caller_declaration_not_experimental_provenance"
    assert result["warnings"] == []
    assert all(np.isfinite(list(result["pseudotime"].values())))


@pytest.mark.parametrize("bad_value", [-1, .5])
def test_declared_counts_reject_negative_or_fractional_values(tmp_path, bad_value):
    from devharness_server.tools_lineage import infer_lineage
    expression, metadata, samples, genes, values = integer_continuous_fixture(tmp_path)
    values[0, 0] = bad_value
    pd.DataFrame(values, index=genes, columns=samples).to_csv(expression, index_label="gene_id")
    with pytest.raises(Exception, match="declared counts require nonnegative integer"):
        infer_lineage(str(expression), str(metadata), samples[0], data_type="counts")


def test_legacy_auto_is_visible_and_matches_explicit_count_transform(tmp_path):
    from devharness_server.tools_lineage import infer_lineage
    expression, metadata, samples, _, _ = integer_continuous_fixture(tmp_path)
    parameters = {"top_genes": 3, "n_components": 2, "n_neighbors": 23, "n_clusters": 3}
    legacy = infer_lineage(str(expression), str(metadata), samples[0], **parameters)
    declared = infer_lineage(str(expression), str(metadata), samples[0], data_type="counts", **parameters)
    assert legacy["pseudotime"] == declared["pseudotime"]
    assert legacy["provenance"]["measurement_declaration"] == "legacy_auto_unverified"
    assert legacy["warnings"][0]["code"] == "measurement_type_inferred_from_numeric_values"
    assert declared["warnings"] == []


@pytest.mark.parametrize("invalid", [None, True, "normalized", [], {}])
def test_invalid_measurement_is_rejected_before_worker_start(monkeypatch, invalid):
    import devharness_server.tools_lineage as module
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid declaration must not start a worker")
    monkeypatch.setattr(module, "run_sandboxed", forbidden)
    with pytest.raises(ValueError, match="data_type"):
        module.infer_lineage("/nonexistent/expression.csv", "/nonexistent/metadata.csv", "s0", data_type=invalid)


def test_dispatcher_schema_and_sdk_forward_the_declaration(monkeypatch):
    from devharness_server.dispatcher import ToolDispatcher
    import devharness.trajectory as sdk
    schema = next(item for item in ToolDispatcher().get_schemas() if item["name"] == "infer_lineage")
    assert set(schema["inputSchema"]["properties"]["data_type"]["enum"]) == {"counts", "continuous", "auto"}
    captured = {}
    monkeypatch.setattr(sdk, "execute", lambda tool, **kwargs: captured.update(kwargs) or {"ok": True})
    # Test forwarding independently of the SDK's separately tested workspace decorator.
    sdk.infer_lineage.__wrapped__("/fixture/expression.csv", "/fixture/metadata.csv", "s0", data_type="continuous")
    assert captured["data_type"] == "continuous"
    json.dumps(captured, allow_nan=False)
