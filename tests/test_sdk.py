"""Public SDK checks on small synthetic data, without benchmark/grader imports."""
import importlib.util
import json

import numpy as np
import pandas as pd
import pytest


def test_public_sdk_is_importable():
    assert importlib.util.find_spec("devharness") is not None


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVHARNESS_WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


def _dataset(workspace, continuous=True):
    from devharness_core.storage import ChunkedExpressionStore
    rng = np.random.default_rng(8)
    ages = np.repeat([8.5, 10.5, 12.5, 14.5], 6)
    samples = [f"{i:03d}" for i in range(len(ages))]
    values = 10 + np.arange(3)[:, None] + ages + rng.normal(0, 1, (3, len(ages)))
    if not continuous:
        values = rng.poisson(values)
    with ChunkedExpressionStore.from_dataframe_to_zarr(
        pd.DataFrame(values, index=["A", "B", "C"], columns=samples), workspace / "counts.zarr"
    ):
        pass
    pd.DataFrame({"sample_id": samples, "specimen_id": samples,
                  "embryonic_age": [f"E{x}" for x in ages],
                  "batch": np.tile(["a", "b"], len(ages) // 2)}).to_csv(workspace / "metadata.csv", index=False)


def _trace(workspace):
    return [json.loads(line) for line in (workspace / ".devharness_trace.jsonl").read_text().splitlines()]


@pytest.mark.parametrize("continuous,family", [(True, "gaussian"), (False, "negative_binomial")])
def test_fit_uses_original_statistical_results(workspace, continuous, family):
    import devharness as dh
    _dataset(workspace, continuous)
    result = dh.fit_trajectory("counts.zarr", "metadata.csv", "1 + numeric_time + C(batch)", ["A", "B"])
    assert result["family"] == family
    assert result["selection_criterion"] == "bic"
    assert "selection" in result["inference_scope"]
    if continuous:
        assert result["normalization_method"] == "none"
    else:
        assert result["normalization_method"] != "none"
    for gene in result["genes"].values():
        assert 0 <= gene["p_value"] <= gene["padj"] == gene["q_value"] <= 1
        feasible = [item for item in gene["candidate_scores"] if item["status"] == "ok"]
        assert gene["selected_df"] == min(feasible, key=lambda item: (item["bic"], item["df"]))["df"]
        assert "stage_means" not in gene
    event = _trace(workspace)[-1]
    assert event["source"] == "participant_workspace_trace"
    assert event["authenticated"] is False
    assert event["tool_events"][0]["tool_name"] == "fit_temporal_trajectory"
    assert event["tool_events"][0]["arguments"]["counts_path"] == str(workspace / "counts.zarr")


def test_aliases_are_diagnosed_without_removing_covariates(workspace):
    import devharness as dh
    _dataset(workspace)
    metadata = pd.read_csv(workspace / "metadata.csv", dtype={"sample_id": str, "specimen_id": str})
    metadata["batch"] = metadata["embryonic_age"]
    metadata.to_csv(workspace / "metadata.csv", index=False)
    diagnosis = dh.validate_design("metadata.csv", "1 + numeric_time + C(batch)")
    assert diagnosis["is_full_rank"] is False
    assert diagnosis["aliased_columns"]
    with pytest.raises(Exception, match="Rank-deficient"):
        dh.fit_trajectory("counts.zarr", "metadata.csv", "1 + numeric_time + C(batch)", ["A"])
    assert _trace(workspace)[-1]["succeeded"] is False
    assert _trace(workspace)[-1]["tool_events"][0]["succeeded"] is False


def test_non_strict_failure_stays_explicit(workspace):
    import devharness as dh
    _dataset(workspace)
    metadata = pd.read_csv(workspace / "metadata.csv", dtype=str)
    metadata["specimen_id"] = "same_specimen"
    metadata.to_csv(workspace / "metadata.csv", index=False)
    result = dh.fit_trajectory("counts.zarr", "metadata.csv", "1 + numeric_time", ["A"], strict=False)
    assert result["status"] == "failed"
    assert result["model_fitting_performed"] is False
    assert result["genes"]["A"]["p_value"] is None
    assert result["genes"]["A"]["q_value"] is None


def test_data_and_analysis_wrappers(workspace):
    import devharness as dh
    _dataset(workspace)
    metadata = dh.data.read_metadata("metadata.csv")
    assert metadata["sample_id"].iloc[0] == "000"
    with dh.data.open_expression("counts.zarr") as store:
        assert store.shape == (3, 24)
        assert store.get_gene_vector("A").shape == (24,)
    manifold = dh.compact_manifold("counts.zarr", "metadata.csv", "embryonic_age")
    assert manifold["num_samples"] == 24
    quality = dh.audit_quality("counts.zarr", "metadata.csv")
    assert quality["status"] in {"pass", "warn", "fail"}
    lineage = dh.infer_lineage("counts.zarr", "metadata.csv", root_sample_id="000", n_clusters=3)
    assert lineage["provenance"]["root_sample_id"] == "000"
    assert len(lineage["pseudotime"]) == 24
    assert len(_trace(workspace)) == 5


def test_save_validates_local_schema_and_atomically_replaces_answer(workspace):
    import devharness as dh
    schema = {"type": "object", "required": ["conclusion"], "additionalProperties": False,
              "properties": {"conclusion": {"type": "string"}}}
    (workspace / "answer_schema.json").write_text(json.dumps(schema))
    result = dh.save_discovery(conclusion="observed association")
    assert result == workspace / "final_discovery.json"
    assert json.loads(result.read_text()) == {"conclusion": "observed association"}
    original = result.read_bytes()
    with pytest.raises(Exception):
        dh.save_discovery(other="missing required field")
    assert result.read_bytes() == original
    dh.save_discovery(conclusion="revised association")
    assert json.loads(result.read_text())["conclusion"] == "revised association"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_save_rejects_nonfinite_values(workspace, value):
    import devharness as dh
    with pytest.raises(ValueError, match="JSON|finite|compliant"):
        dh.save_discovery(estimate=value)
    assert not (workspace / "final_discovery.json").exists()
    trace_text = (workspace / ".devharness_trace.jsonl").read_text()
    json.loads(trace_text, parse_constant=lambda value: pytest.fail("Nonfinite token in trace: " + value))


def test_workspace_boundaries_and_symlinks(workspace):
    import devharness as dh
    outside = workspace.parent / (workspace.name + "-outside.json")
    outside.write_text("{}")
    with pytest.raises(PermissionError, match="workspace"):
        dh.save_discovery(path=outside, conclusion="invalid")
    (workspace / "alias.json").symlink_to(outside)
    with pytest.raises(PermissionError, match="[Ss]ymlink"):
        dh.save_discovery(path="alias.json", conclusion="invalid")
    (workspace / "local.json").write_text("{}")
    (workspace / "local_alias.json").symlink_to(workspace / "local.json")
    with pytest.raises(PermissionError, match="[Ss]ymlink"):
        dh.save_discovery(path="local_alias.json", conclusion="invalid")
    with pytest.raises(PermissionError):
        dh.save_discovery(path="answer_schema.json", conclusion="invalid")
    with pytest.raises(PermissionError, match="workspace"):
        dh.data.open_expression(outside)
    assert outside.read_text() == "{}"


def test_trace_is_append_only_bounded_and_rejects_symlinks(workspace):
    import devharness as dh
    dh.save_discovery(conclusion="first")
    trace = workspace / ".devharness_trace.jsonl"
    first = trace.read_bytes()
    dh.save_discovery(conclusion="second")
    assert trace.read_bytes().startswith(first)
    assert len(_trace(workspace)) == 2
    # Exhausted tracing fails before another output can be written.
    trace.write_bytes(b" " * (2 * 1024 * 1024))
    with pytest.raises(RuntimeError, match="[Tt]race.*limit"):
        dh.save_discovery(conclusion="third")
    assert trace.stat().st_size == 2 * 1024 * 1024
    assert json.loads((workspace / "final_discovery.json").read_text())["conclusion"] == "second"
    trace.unlink()
    target = workspace / "keep.txt"
    target.write_text("untouched")
    trace.symlink_to(target)
    with pytest.raises(PermissionError, match="[Ss]ymlink"):
        dh.save_discovery(conclusion="third")
    assert target.read_text() == "untouched"


def test_schema_never_fetches_external_references(workspace):
    import devharness as dh
    (workspace / "answer_schema.json").write_text(json.dumps({"$ref": "https://example.invalid/schema.json"}))
    with pytest.raises(ValueError, match="[Ll]ocal|[Ee]xternal"):
        dh.save_discovery(conclusion="none")
    assert not (workspace / "final_discovery.json").exists()


def test_default_workspace_is_cwd(tmp_path, monkeypatch):
    import devharness as dh
    monkeypatch.delenv("DEVHARNESS_WORKSPACE_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert dh.save_discovery(conclusion="cwd") == tmp_path / "final_discovery.json"


def test_save_preserves_readonly_inputs_and_case_variants(workspace):
    import devharness as dh
    supplied = workspace / "supplied_design.json"
    supplied.write_text('{"design":"original"}')
    supplied.chmod(0o444)
    try:
        with pytest.raises(PermissionError, match="[Rr]ead.only|[Ii]nput"):
            dh.save_discovery(path=supplied, conclusion="invalid")
        assert supplied.read_text() == '{"design":"original"}'
        with pytest.raises(PermissionError, match="schema|input"):
            dh.save_discovery(path="ANSWER_SCHEMA.JSON", conclusion="invalid")
    finally:
        supplied.chmod(0o644)


def test_schema_accepts_internal_references(workspace):
    import devharness as dh
    (workspace / "answer_schema.json").write_text(json.dumps({
        "$defs": {"conclusion": {"type": "string"}}, "type": "object",
        "properties": {"conclusion": {"$ref": "#/$defs/conclusion"}},
        "required": ["conclusion"],
    }))
    assert dh.save_discovery(conclusion="association").is_file()


def test_metadata_read_is_bounded_and_trace_rejects_hard_links(workspace):
    import devharness as dh
    import os
    metadata = workspace / "metadata.csv"
    with metadata.open("wb") as handle:
        handle.truncate(20 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="20 MB"):
        dh.read_metadata(metadata)
    trace = workspace / ".devharness_trace.jsonl"
    original = trace.read_bytes()
    os.link(trace, workspace / "trace-hardlink.jsonl")
    with pytest.raises(PermissionError, match="hard links"):
        dh.save_discovery(conclusion="invalid")
    assert trace.read_bytes() == original
