"""Replay the public Kang single-cell walkthrough using local tools."""

import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import os
import sys
import warnings
from importlib.metadata import version
from pathlib import Path

import anndata
import numpy as np
import pandas as pd
from matplotlib.colors import ListedColormap
from matplotlib.figure import Figure
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydeseq2.dds import DeseqDataSet
from pydeseq2.ds import DeseqStats
from scipy import sparse
from scipy.stats import false_discovery_control

from devbench.core.contrast_design import DesignSpec, compile_contrast_design
from devbench.core.count_profiles import CountProfile, CountProfiles, ProfileKey

PLAN = Path(__file__).resolve().with_name("kang-plan.json")
DATA_SHA256 = "e6a5adac64dcdeb36eaba27db49b63e0c64bb0ed4a64c6705971506b41c39830"


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, data):
    path.write_text(
        json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return sha256(path)


async def inspect_mcp(path, workspace, trace_path):
    request = {
        "inputs": [
            {"path": str(path), "data_classification": "public", "role": "expression"}
        ]
    }
    options = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "devbench.platform.cli",
            "plugin-mcp",
            "--input-root",
            str(path.parent),
            "--workspace",
            str(workspace),
        ],
        env={
            key: os.environ[key]
            for key in ("PATH", "PYTHONPATH", "DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH")
            if key in os.environ
        },
    )
    async with asyncio.timeout(60), stdio_client(options) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()
            reply = await client.call_tool("inspect_rna_inputs", request)
    result = reply.structuredContent
    write_json(
        trace_path,
        {"arguments": request, "is_error": reply.isError, "response": result},
    )
    if reply.isError:
        return {"status": "held", "reason": result["reason"]}
    item = result["inputs"][0]
    if item["sha256"] != sha256(path):
        raise ValueError("MCP returned a different input digest")
    return {
        "status": "inspected",
        "sha256": item["sha256"],
        "inspection_receipt_sha256": item["inspection_receipt_sha256"],
    }


def donor_support(adata, plan):
    source = plan["source"]
    obs = adata.obs[
        [source["donor_field"], source["condition_field"], source["population_field"]]
    ].copy()
    obs.columns = ["donor", "condition", "population"]
    if obs.isna().any().any() or not adata.var_names.is_unique:
        raise ValueError("The dataset requires complete labels and unique features")
    obs = obs.astype(str).reset_index(drop=True)
    donors = {
        label: f"D{index:02d}" for index, label in enumerate(sorted(set(obs.donor)), 1)
    }
    obs["donor"] = obs.donor.map(donors)
    levels = [plan["contrast"]["denominator"], plan["contrast"]["numerator"]]
    if set(obs.condition) != set(levels):
        raise ValueError(
            "The observed condition labels differ from the frozen contrast"
        )
    support = {}
    for population in plan["populations"]:
        subset = obs[obs.population == population]
        cells = (
            subset.groupby(["donor", "condition"])
            .size()
            .unstack(fill_value=0)
            .reindex(index=list(donors.values()), columns=levels, fill_value=0)
        )
        complete = cells.ge(plan["minimum_cells_per_donor_condition"]).all(axis=1)
        support[population] = {
            "cells": {
                unit: {key: int(value) for key, value in row.items()}
                for unit, row in cells.iterrows()
            },
            "complete_donors": cells.index[complete].tolist(),
        }
    return obs, support


def pseudobulk(adata, obs, population, donors, plan):
    source = plan["source"]
    levels = [plan["contrast"]["denominator"], plan["contrast"]["numerator"]]
    captures = dict(
        zip(
            levels,
            [source["control_capture"], source["stimulated_capture"]],
            strict=True,
        )
    )
    rows = []
    selected_cells = []
    library_indices = []
    for donor in donors:
        for condition in levels:
            matches = np.flatnonzero(
                obs.donor.eq(donor)
                & obs.condition.eq(condition)
                & obs.population.eq(population)
            )
            selected_cells.extend(matches)
            library_indices.extend([len(rows)] * len(matches))
            library_id = f"{donor}_{condition}"
            rows.append(
                (library_id, donor, condition, len(matches), captures[condition])
            )
    membership = sparse.csr_matrix(
        (
            np.ones(len(selected_cells), dtype=np.int64),
            (library_indices, selected_cells),
        ),
        shape=(len(rows), adata.n_obs),
    )
    counts = np.empty((len(rows), adata.n_vars), dtype=np.int64)
    cell_totals = np.zeros(adata.n_obs, dtype=np.int64)
    for start in range(0, adata.n_vars, 256):
        stop = min(start + 256, adata.n_vars)
        block = adata.X[:, start:stop]
        if not sparse.issparse(block):
            raise ValueError("Expected the pinned sparse X count matrix")
        values = block.data
        if not (
            np.isfinite(values).all()
            and (values >= 0).all()
            and np.equal(values, np.floor(values)).all()
        ):
            raise ValueError("X must contain nonnegative integer counts")
        integers = block.astype(np.int64)
        counts[:, start:stop] = (membership @ integers).toarray()
        cell_totals += np.asarray(integers.sum(axis=1)).ravel()
    if not np.array_equal(cell_totals, adata.obs["nCount_RNA"].to_numpy()):
        raise ValueError("The pinned X counts disagree with the RNA cell totals")
    profiles = tuple(
        CountProfile(
            profile_id=library_id,
            key=ProfileKey(donor, library_id, condition, population),
            status="observed",
            observation_count=cells,
            cell_count=cells,
            counts=tuple(map(int, values)),
            library_size=int(values.sum()),
            component_ids=(),
            technical_factors=(("capture", capture),),
            exclusion_reason=None,
        )
        for (library_id, donor, condition, cells, capture), values in zip(
            rows, counts, strict=True
        )
    )
    features = tuple(map(str, adata.var_names))
    panel = CountProfiles(
        features, "GSE96583 curated feature axis", "umi_counts", (), profiles
    )
    return panel, pd.DataFrame(counts, index=[row[0] for row in rows], columns=features)


def design_record(compiled):
    return {
        "status": compiled.status,
        "columns": len(compiled.columns),
        "rank": compiled.rank,
        "residual_df": compiled.residual_df,
        "estimable": compiled.estimable,
        "refusal_codes": list(compiled.refusal_codes),
    }


def fit_counts(counts, design, plan):
    rules = plan["fit"]
    counts = counts.loc[list(design.profile_ids)]
    keep = (
        counts.ge(rules["prefilter_minimum_counts"])
        .sum()
        .ge(rules["prefilter_minimum_libraries"])
    )
    counts = counts.loc[:, keep]
    if not (counts > 0).all().any():
        raise ValueError(
            "Median-ratio RLE requires genes with positive counts in every library"
        )
    matrix = pd.DataFrame(
        design.matrix,
        index=counts.index,
        columns=[f"coefficient_{i}" for i in range(len(design.columns))],
    )
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        model = DeseqDataSet(
            counts=counts,
            metadata=pd.DataFrame(index=counts.index),
            design=matrix,
            size_factors_fit_type="ratio",
            fit_type=rules["dispersion_trend"],
            refit_cooks=False,
            n_cpus=1,
            quiet=True,
            low_memory=True,
        )
        model.deseq2()
        stats = DeseqStats(
            model,
            contrast=np.asarray(design.contrast, dtype=float),
            cooks_filter=True,
            independent_filter=False,
            quiet=True,
            n_cpus=1,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            stats.summary()
    table = stats.results_df[["baseMean", "log2FoldChange", "lfcSE", "pvalue"]].copy()
    numbers = table[["log2FoldChange", "lfcSE", "pvalue"]].to_numpy()
    valid = (
        np.isfinite(numbers).all(axis=1)
        & table.lfcSE.gt(0)
        & table.pvalue.between(0, 1)
    )
    for field in ("_genewise_converged", "_MAP_converged", "_LFC_converged"):
        valid &= model.var[field].to_numpy(dtype=bool, na_value=False)
    if not valid.any():
        raise ValueError("No gene has a valid fitted Wald statistic")
    table.loc[~valid, ["log2FoldChange", "lfcSE", "pvalue"]] = np.nan
    q = false_discovery_control(table.pvalue.fillna(1).to_numpy(), method="bh")
    table["padj"] = np.where(valid, q, np.nan)
    table["status"] = np.where(valid, "estimated", "unavailable")
    finite = table.loc[valid]
    if not (finite.pvalue.le(finite.padj) & finite.padj.le(1)).all():
        raise ValueError("Invalid retained p/q ordering")
    return table, sorted({str(item.message) for item in recorded})


def plot_decision(support, table, population, plan, destination):
    figure = Figure(figsize=(11.8, 4.45), layout="constrained")
    cohort, fit = figure.subplots(1, 2, width_ratios=(1.1, 1))
    names = plan["populations"]
    donor_ids = list(support[names[0]]["cells"])
    pair_floor = np.array(
        [
            [min(support[name]["cells"][donor].values()) for donor in donor_ids]
            for name in names
        ]
    )
    minimum = plan["minimum_cells_per_donor_condition"]
    cohort.imshow(
        pair_floor >= minimum,
        aspect="auto",
        cmap=ListedColormap(["#fde0b4", "#a8ded8"]),
        vmin=0,
        vmax=1,
    )
    for row, values in enumerate(pair_floor):
        for column, count in enumerate(values):
            cohort.text(
                column, row, str(count), ha="center", va="center", color="#122536"
            )
    cohort.set(
        title="A  |  Donor support before fitting",
        xticks=range(len(donor_ids)),
        xticklabels=donor_ids,
        yticks=range(len(names)),
        yticklabels=names,
        xlabel=f"Smaller cell count across the two conditions (floor: {minimum})",
    )

    tested = table[table.status == "estimated"]
    significant = tested.padj.le(plan["fit"]["false_discovery_rate"])
    for mask, color, label in (
        (~significant, "#8393a5", "q > 0.05"),
        (significant, "#007e79", "q ≤ 0.05"),
    ):
        points = tested[mask]
        fit.scatter(
            np.log10(1 + points.baseMean),
            points.log2FoldChange,
            s=9,
            alpha=0.55,
            color=color,
            linewidths=0,
            label=label,
        )
    fit.axhline(0, color="#4a5b70", linewidth=0.8)
    fit.set(
        title=f"B  |  Revised {population} fit",
        xlabel="log₁₀(1 + mean normalized count)",
        ylabel="log₂ fold change (stim / ctrl)",
    )
    fit.legend(frameon=False, loc="upper right", markerscale=1.5)
    figure.savefig(destination, dpi=160, facecolor="white")


def run(path, output):
    if path.stat().st_size > 50_000_000 or sha256(path) != DATA_SHA256:
        raise ValueError("Input does not match the bounded, pinned Kang H5AD")
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    plan_hash = write_json(output / "plan-v1.json", plan)
    source_inspection = asyncio.run(
        inspect_mcp(
            path,
            output / "mcp-workspace",
            output / "mcp-inspection.json",
        )
    )
    if (
        source_inspection["status"] == "held"
        and source_inspection["reason"] != "local_workflow_unavailable"
    ):
        raise ValueError(f"Source inspection refused: {source_inspection['reason']}")

    adata = anndata.read_h5ad(path, backed="r")
    try:
        obs, support = donor_support(adata, plan)
        eligible = [
            name
            for name, data in support.items()
            if len(data["complete_donors"]) >= plan["minimum_complete_donor_pairs"]
        ]
        if len(eligible) != 1:
            raise ValueError(
                "The pinned case must leave exactly one supported population"
            )
        population = eligible[0]
        panel, counts = pseudobulk(
            adata, obs, population, support[population]["complete_donors"], plan
        )
        input_shape = list(adata.shape)
    finally:
        adata.file.close()

    contrast = plan["contrast"]
    original = compile_contrast_design(
        panel,
        DesignSpec(
            "S-P", **contrast, population=population, technical_factors=("capture",)
        ),
    )
    if original.estimable or "contrast_not_estimable" not in original.refusal_codes:
        raise ValueError(
            "Expected the aliased capture-adjusted contrast to be withheld"
        )
    diagnostics = {
        "dataset_sha256": DATA_SHA256,
        "mcp_source_inspection": source_inspection,
        "h5ad_cells_by_features": input_shape,
        "cells_matching_nCount_RNA": input_shape[0],
        "cell_counts_by_condition": {
            key: int(value) for key, value in obs.condition.value_counts().items()
        },
        "populations": support,
        "original_capture_adjusted_design": design_record(original),
    }
    diagnostic_hash = write_json(output / "diagnostics.json", diagnostics)
    revised = compile_contrast_design(
        panel, DesignSpec("S-P", **contrast, population=population)
    )
    if revised.status != "ready" or revised.rank != len(revised.columns):
        raise ValueError("Revised exploratory contrast failed the paired design check")
    revision = {
        "parent_plan_sha256": plan_hash,
        "reason_evidence_sha256": diagnostic_hash,
        "withheld_populations": [
            name for name in plan["populations"] if name not in eligible
        ],
        "population": population,
        "complete_donors": support[population]["complete_donors"],
        "design": plan["revised_design_if_capture_is_confounded"],
        "compiled_design": design_record(revised),
        "claim": "Exploratory within-donor association across these preparations",
    }
    revision_hash = write_json(output / "plan-v2.json", revision)
    derived = output / "derived"
    derived.mkdir()
    pseudobulk_path = derived / "pseudobulk-counts.csv"
    counts.T.rename_axis("gene_id").to_csv(pseudobulk_path)
    derived_inspection = asyncio.run(
        inspect_mcp(
            pseudobulk_path,
            output / "mcp-pseudobulk-workspace",
            output / "mcp-pseudobulk-inspection.json",
        )
    )
    if derived_inspection["status"] != "inspected":
        raise ValueError(
            f"Pseudobulk inspection refused: {derived_inspection['reason']}"
        )
    table, fit_warnings = fit_counts(counts, revised, plan)
    results_path = output / "differential-expression.tsv"
    table.to_csv(results_path, sep="\t", index_label="gene", na_rep="")
    top = table.dropna(subset=["padj"]).sort_values(["padj", "pvalue"]).head(5)
    figure_path = output / "kang-results.png"
    plot_decision(support, table, population, plan, figure_path)
    evidence = {
        **diagnostics,
        "plan_v1_sha256": plan_hash,
        "diagnostics_sha256": diagnostic_hash,
        "revision": revision,
        "plan_v2_sha256": revision_hash,
        "mcp_pseudobulk_inspection": derived_inspection,
        "fit": {
            "solver": f"PyDESeq2 {version('pydeseq2')}",
            "libraries": len(counts),
            "measured_features": len(panel.feature_ids),
            "prefiltered_family_size": len(table),
            "estimated_genes": int(table.status.eq("estimated").sum()),
            "unavailable_genes_in_family": int(table.status.ne("estimated").sum()),
            "genes_q_at_most_0_05": int(
                table.padj.le(plan["fit"]["false_discovery_rate"]).sum()
            ),
            "top_genes": top.reset_index(names="gene").to_dict("records"),
            "warnings": fit_warnings,
            "differential_expression_sha256": sha256(results_path),
            "figure_sha256": sha256(figure_path),
        },
    }
    write_json(output / "kang-evidence.json", evidence)
    print(json.dumps(evidence["revision"], indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Pinned public H5AD")
    parser.add_argument(
        "--output", required=True, type=Path, help="Fresh output directory"
    )
    args = parser.parse_args()
    if not args.input.is_absolute() or not args.output.is_absolute():
        parser.error("Use absolute input and output paths")
    args.output.mkdir(mode=0o700)
    run(args.input.resolve(strict=True), args.output.resolve(strict=True))


if __name__ == "__main__":
    main()
