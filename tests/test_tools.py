import json

import numpy as np
import pandas as pd
import pytest


def dataset(tmp_path, continuous=True):
    rng = np.random.default_rng(8)
    stages = np.repeat([8.5, 10.5, 12.5, 14.5], 6)
    samples = [f"s{i}" for i in range(len(stages))]
    values = 10 + np.arange(3)[:, None] + stages + rng.normal(0, 1, (3, len(stages)))
    if not continuous:
        values = rng.poisson(values)
    counts = tmp_path / "counts.csv"
    meta = tmp_path / "meta.csv"
    pd.DataFrame(values, index=["A", "B", "C"], columns=samples).to_csv(counts)
    pd.DataFrame({"sample_id": samples, "embryonic_age": [f"E{x}" for x in stages],
                  "batch": np.tile(["a", "b"], len(stages) // 2)}).to_csv(meta, index=False)
    return str(counts), str(meta)


def test_rank_detection_and_numeric_stages(tmp_path):
    from devharness_server.tools_stat import validate_design_matrix
    path = tmp_path / "meta.csv"
    pd.DataFrame({"embryonic_age": ["E8.5", "E8.5", "E10.5", "E10.5"], "batch": ["a", "a", "b", "b"]}).to_csv(path, index=False)
    bad = validate_design_matrix(str(path), "1 + numeric_time + C(batch)")
    assert not bad["is_full_rank"] and bad["rank"] == 2
    assert bad["aliased_columns"]
    assert validate_design_matrix(str(path), "1 + numeric_time")["is_full_rank"]


@pytest.mark.parametrize("continuous, family", [(True, "gaussian"), (False, "negative_binomial")])
def test_spline_glm_type_and_fdr(tmp_path, continuous, family):
    from devharness_server.tools_stat import fit_temporal_trajectory
    counts, metadata = dataset(tmp_path, continuous)
    result = fit_temporal_trajectory(counts, metadata, "1 + cr(numeric_time, df=3) + C(batch)", "embryonic_age", ["A", "B", "C"])
    assert result["family"] == family
    for gene in result["genes"].values():
        assert 0 <= gene["p_value"] <= gene["padj"] <= 1
        assert gene["degrees_of_freedom"] == gene["selected_df"]
        assert len(gene["coefficients"]) == gene["selected_df"] + 2
        feasible = [candidate for candidate in gene["candidate_scores"] if candidate["status"] == "ok"]
        assert gene["selected_df"] == min(feasible, key=lambda candidate: (candidate["aic"], candidate["df"]))["df"]


def test_bh_reference_and_alignment_errors(tmp_path):
    from devharness_server.tools_stat import bh_adjust, fit_temporal_trajectory
    assert bh_adjust([.01, .04, .03, .8]) == pytest.approx([.04, .053333333333, .053333333333, .8])
    counts, metadata = dataset(tmp_path)
    frame = pd.read_csv(metadata)
    frame.loc[0, "sample_id"] = "missing"
    frame.to_csv(metadata, index=False)
    with pytest.raises(Exception, match="sample"):
        fit_temporal_trajectory(counts, metadata, "1 + numeric_time", "embryonic_age", ["A"])


def test_monotonicity_and_dispatcher_schemas():
    from devharness_server.tools_trajectory import calculate_temporal_monotonicity as monotonicity
    from devharness_server.dispatcher import ToolDispatcher
    assert monotonicity({"E12.5": 3, "E8.5": 1, "E10.5": 2})["direction"] == "increasing"
    assert monotonicity({"E8.5": 1, "E10.5": 1})["is_monotonic"] is False
    with pytest.raises(ValueError, match="two distinct timepoints"):
        monotonicity({"E8.5": 1})
    dispatcher = ToolDispatcher()
    schema = next(x for x in dispatcher.get_schemas() if x["name"] == "fit_temporal_trajectory")
    assert "counts_path" in schema["inputSchema"]["required"]
    result = json.loads(dispatcher.execute("calculate_temporal_monotonicity", {"stage_values": {"E8.5": 2, "E10.5": 1}}))
    assert result["score"] == -1
    with pytest.raises(ValueError):
        dispatcher.execute("unknown", {})


def test_metadata_leading_zero_ids_are_preserved(tmp_path):
    from devharness_server.tools_stat import _metadata
    path = tmp_path / "metadata.csv"
    path.write_text("sample_id,embryonic_age\n001,E8.5\n002,E10.5\n")
    assert _metadata(str(path))["sample_id"].tolist() == ["001", "002"]


def test_spline_other_covariate_does_not_disable_time_spline():
    from devharness_server.tools_stat import _spline_formula
    formula = _spline_formula("1 + cr(other_time, df=3) + numeric_time")
    assert "cr(numeric_time, df=3, constraints='center')" in formula


def test_dispatcher_preserves_utf8_size():
    from devharness_server.dispatcher import ToolDispatcher
    def unicode_summary() -> dict:
        return {"driver": "基因" * 200}
    dispatcher = ToolDispatcher()
    dispatcher.register(unicode_summary)
    assert len(dispatcher.execute("unicode_summary", {}).encode()) < 1500


def test_mcp_discovery_and_real_tool_call():
    import asyncio
    from mcp.shared.memory import create_connected_server_and_client_session
    from devharness_server.server import create_server
    async def check():
        async with create_connected_server_and_client_session(create_server()) as client:
            schemas = await client.list_tools()
            assert {"validate_design_matrix", "fit_temporal_trajectory", "infer_lineage", "infer_regulatory_network",
                    "regulon_enrichment", "motif_enrichment", "analyze_batch_effects"} <= {tool.name for tool in schemas.tools}
            result = await client.call_tool("calculate_temporal_monotonicity", {"stage_values": {"E8.5": 1, "E10.5": 2}})
            assert not result.isError
            assert result.structuredContent["direction"] == "increasing"
    asyncio.run(check())


def test_mcp_stdio_from_an_unrelated_workspace(tmp_path):
    import asyncio
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    async def check():
        parameters = StdioServerParameters(command=sys.executable, args=["-m", "devharness_server.server"], cwd=str(tmp_path))
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {"validate_design_matrix", "fit_temporal_trajectory", "infer_lineage", "regulon_enrichment"} <= {tool.name for tool in tools.tools}
                response = await session.call_tool("calculate_temporal_monotonicity", {"stage_values": {"E8.5": 2, "E10.5": 1}})
                assert not response.isError
                assert response.structuredContent["direction"] == "decreasing"
    asyncio.run(check())
