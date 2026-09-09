"""Lagged associations and supplied-reference enrichment with explicit scope.

No inferred edge establishes direct or causal TF regulation. Curated regulon
overlap and measured sequence-motif hits are separate forms of evidence.
"""
import csv
import math
from numbers import Real
from pathlib import Path
from typing import List

import numpy as np

from devharness_core.stages import parse_stages
from devharness_core.storage import ChunkedExpressionStore
from devharness_core.watchdog import run_sandboxed
from devharness_server.tools_lineage import _bounded_int, _bounded_metadata, _file_sha256, _matrix_sha256
from devharness_server.tools_stat import _diagnostics, bh_adjust


def _genes(values: List[str], name: str, maximum: int = 100000) -> List[str]:
    if not isinstance(values, list) or not 1 <= len(values) <= maximum:
        raise ValueError(f"{name} requires between 1 and {maximum} gene names")
    if any(not isinstance(gene, str) or not gene.strip() or gene != gene.strip() for gene in values):
        raise ValueError(name + " requires nonempty, trimmed gene names")
    if len(set(values)) != len(values):
        raise ValueError(name + " must contain distinct genes")
    return values


def _network_worker(counts_path: str, metadata_path: str, candidate_tfs: List[str],
                    target_genes: List[str], time_col: str, lag: int, alpha: float,
                    max_cells: int, max_edges: int) -> dict:
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler

    metadata = _bounded_metadata(metadata_path)
    if time_col not in metadata:
        raise ValueError("Metadata is missing requested time_col: " + time_col)
    genes = list(dict.fromkeys(candidate_tfs + target_genes))
    with ChunkedExpressionStore(counts_path) as store:
        samples = list(store.sample_names)
        if set(metadata["sample_id"]) != set(samples):
            raise ValueError("Expression and metadata sample IDs must match exactly")
        if len(samples) > max_cells:
            raise ValueError("Sample count exceeds max_cells; stage an explicitly selected analysis cohort")
        lookup = {gene: i for i, gene in enumerate(store.gene_names)}
        missing = sorted(set(genes) - lookup.keys())
        if missing:
            raise ValueError("Unknown gene: " + ", ".join(missing[:10]))
        expression = np.asarray(store.get_chunk([lookup[gene] for gene in genes], slice(None)), dtype=float)
    if not np.isfinite(expression).all():
        raise ValueError("Requested expression must contain finite values")
    metadata = metadata.set_index("sample_id").loc[samples]
    times = parse_stages(metadata[time_col])
    stages = np.unique(times)
    if len(stages) < lag + 3:
        raise ValueError("Lagged association requires at least three time pairs after the requested lag")
    is_counts = bool(np.all(expression >= 0) and np.all(expression == np.floor(expression)))
    selected_hash = _matrix_sha256(expression, genes, samples)
    transformed = np.log1p(expression) if is_counts else expression
    # Replicates are independent samples, not paired cell trajectories. Average
    # within observed time, then associate previous-time TF means with later
    # target means. Lag counts observed intervals, not equal physical durations.
    means = np.asarray([transformed[:, times == stage].mean(axis=1) for stage in stages])
    if not np.isfinite(means).all():
        raise ValueError("Temporal aggregation overflowed; rescale expression")
    gene_index = {gene: i for i, gene in enumerate(genes)}
    edges, constant_targets, constant_tfs, diagnostics = [], [], [], {}
    for target in target_genes:
        tfs = [tf for tf in candidate_tfs if tf != target]
        if not tfs:
            continue
        x = means[:-lag, [gene_index[tf] for tf in tfs]]
        y = means[lag:, gene_index[target]]
        variable = np.std(x, axis=0) > 1e-12
        constant_tfs.extend(tf for tf, varies in zip(tfs, variable) if not varies)
        coefficients = np.zeros(len(tfs))
        standardized_x = StandardScaler().fit_transform(x)
        design = np.column_stack([np.ones(len(x)), standardized_x])
        diagnostic = _diagnostics(design, ["Intercept"] + tfs)
        if diagnostic["condition_number"] is not None and not math.isfinite(diagnostic["condition_number"]):
            diagnostic["condition_number"] = None
        diagnostic["regularized"] = True
        diagnostics[target] = diagnostic
        if np.std(y) <= 1e-12:
            constant_targets.append(target)
        elif variable.any():
            standardized_y = StandardScaler().fit_transform(y.reshape(-1, 1))[:, 0]
            coefficients[variable] = Ridge(alpha=alpha, fit_intercept=True, solver="svd").fit(
                standardized_x[:, variable], standardized_y).coef_
        if not np.isfinite(coefficients).all():
            raise ValueError("Regularized association returned nonfinite coefficients")
        for tf, coefficient in zip(tfs, coefficients):
            edges.append({"tf": tf, "target": target, "score": float(abs(coefficient)),
                          "coefficient": float(coefficient), "sign": int(np.sign(coefficient))})
    edges.sort(key=lambda edge: (-edge["score"], edge["tf"], edge["target"]))
    candidates = [{"tf": tf, "target": target} for tf in candidate_tfs for target in target_genes if tf != target]
    return {"edges": edges[:max_edges], "candidate_universe": candidates,
        "provenance": {"algorithm": "lagged time-mean multivariable ridge association",
            "interpretation": "associative temporal prediction; no direct or causal regulatory claim",
            "counts_path": counts_path, "metadata_path": metadata_path, "time_col": time_col,
            "metadata_sha256": metadata.attrs["source_sha256"], "selected_expression_sha256": selected_hash,
            "hash_scope": "requested numeric expression and ordered gene/sample IDs; complete metadata file",
            "time_points": len(stages), "time_pairs": len(stages) - lag, "lag_observed_intervals": lag,
            "time_range": [float(stages[0]), float(stages[-1])], "sample_count": len(samples),
            "alpha": alpha, "transform": "log1p counts then z-score" if is_counts else "continuous expression z-score",
            "candidate_edge_count": len(candidates), "returned_edge_count": min(max_edges, len(edges)),
            "truncated": len(edges) > max_edges, "constant_targets": constant_targets,
            "constant_tfs": sorted(set(constant_tfs)), "design_diagnostics": diagnostics,
            "score": "absolute standardized ridge coefficient; not a probability",
            "candidate_universe_scope": "measured requested pairs; absence of a curated reference edge does not certify a negative"},
        "limitations": ["Lagged association is not direct binding or causal TF regulation; shared time trends, confounding and reverse regulation can produce edges.",
            "Time means mix cell states; no paired cell histories or cross-sectional causal identification are available.",
            "Regularized coefficients have no reported frequentist p-values or q-values; ranking is exploratory, not clinical biomarker validation.",
            "Ridge stabilizes collinear designs but does not identify distinct causal effects of aliased TFs; rank diagnostics are reported before fitting.",
            "Zero coefficients have sign 0 (undetermined), and lag counts observed intervals even when time spacing is unequal."]}


def infer_regulatory_network(counts_path: str, metadata_path: str, candidate_tfs: List[str],
                              target_genes: List[str], time_col: str = "embryonic_age",
                              lag: int = 1, alpha: float = 1.0, max_cells: int = 2000,
                              max_edges: int = 1000) -> dict:
    """Rank measured TF-target pairs by regularized lagged temporal association.

    TFs and targets must be explicit. Fits run in a supervised spawned worker;
    the finite returned scores are exploratory associations, never causal edges.
    """
    _genes(candidate_tfs, "candidate_tfs", 100)
    _genes(target_genes, "target_genes", 100)
    if not any(tf != target for tf in candidate_tfs for target in target_genes):
        raise ValueError("At least one non-self TF-target pair is required")
    if len(candidate_tfs) * len(target_genes) > 2500:
        raise ValueError("Request at most 2500 candidate TF-target pairs to bound output")
    _bounded_int(lag, "lag", 1, 50)
    _bounded_int(max_cells, "max_cells", 4, 5000)
    _bounded_int(max_edges, "max_edges", 1, 2500)
    if isinstance(alpha, bool) or not isinstance(alpha, Real) or not math.isfinite(alpha) or alpha <= 0:
        raise ValueError("alpha must be finite and positive")
    if not isinstance(time_col, str) or not time_col:
        raise ValueError("time_col must be nonempty")
    return run_sandboxed(_network_worker, (str(Path(counts_path).expanduser().resolve()),
        str(Path(metadata_path).expanduser().resolve()), candidate_tfs, target_genes, time_col,
        lag, float(alpha), max_cells, max_edges), max_mem_mb=2000, timeout_sec=120)


def _validate_enrichment(selected: List[str], background: List[str], max_results: int) -> None:
    _genes(selected, "selected_genes")
    _genes(background, "background_genes")
    if not set(selected).issubset(background):
        raise ValueError("Selected genes must be a subset of the explicitly measured background")
    if len(selected) >= len(background):
        raise ValueError("Enrichment requires background genes outside the selected set")
    _bounded_int(max_results, "max_results", 1, 500)


def _table_rows(path: str, required: set):
    source = Path(path)
    if source.stat().st_size > 100 * 1024 * 1024:
        raise ValueError("Reference table exceeds the 100 MB bounded limit")
    with source.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("TSV requires columns: " + ", ".join(sorted(required)))
        for index, row in enumerate(reader):
            if index >= 1000000:
                raise ValueError("Reference table exceeds one million bounded rows")
            if any(row.get(key) is None or not row[key].strip() for key in required):
                raise ValueError("Reference table contains empty required fields")
            yield {key: value.strip() if isinstance(value, str) else value for key, value in row.items()}


def _enrichment_rows(selected: set, background: set, gene_sets: dict, field: str) -> List[dict]:
    from scipy.stats import hypergeom
    rows = []
    for label, genes in sorted(gene_sets.items()):
        eligible = genes & background
        overlap = len(eligible & selected)
        p_value = float(hypergeom.sf(overlap - 1, len(background), len(eligible), len(selected)))
        rows.append({field: label, "overlap": overlap, "reference_size": len(eligible),
            "selected_size": len(selected), "background_size": len(background),
            "fold_enrichment": overlap * len(background) / (len(selected) * len(eligible)) if eligible else None,
            "p_value": p_value})
    for row, q_value in zip(rows, bh_adjust([row["p_value"] for row in rows])):
        row["q_value"] = q_value
    return sorted(rows, key=lambda row: (row["p_value"], row[field]))


def _enrichment_worker(selected_genes: List[str], background_genes: List[str], path: str,
                        kind: str, max_results: int) -> dict:
    selected, background = set(selected_genes), set(background_genes)
    sets, outside, seen, statuses = {}, set(), {}, {}
    required = {"tf", "target"} if kind == "regulon" else {"gene", "motif", "hit"}
    for row in _table_rows(path, required):
        label = row["tf"] if kind == "regulon" else row["motif"]
        gene = row["target"] if kind == "regulon" else row["gene"]
        if len(sets) >= 10000 and label not in sets:
            raise ValueError("Reference exceeds 10000 tested sets")
        sets.setdefault(label, set())
        if gene not in background:
            outside.add(gene)
        if kind == "regulon":
            sets[label].add(gene)
        else:
            normalized = row["hit"].lower()
            if normalized not in ("0", "1", "true", "false"):
                raise ValueError("Motif hit must be explicit 0/1 or true/false")
            hit = normalized in ("1", "true")
            key = (gene, label)
            if key in statuses and statuses[key] != hit:
                raise ValueError("Conflicting motif-hit status for a gene/motif pair")
            statuses[key] = hit
            seen.setdefault(label, set()).add(gene)
            if hit:
                sets[label].add(gene)
    if not sets:
        raise ValueError("Reference contains no testable sets")
    if kind == "motif" and any(not background.issubset(seen[label]) for label in sets):
        raise ValueError("Every background gene must have an explicit scanned hit/no-hit status for every tested motif")
    rows = _enrichment_rows(selected, background, sets, "tf" if kind == "regulon" else "motif")
    return {"results": rows[:max_results], "provenance": {
        "reference_path": path, "reference_sha256": _file_sha256(path),
        "reference_validation": "local table schema and content checked; source authenticity is caller-supplied",
        "evidence_type": "supplied curated TF-target overlap" if kind == "regulon" else "supplied sequence motif-hit calls",
        "test": "one-sided hypergeometric overrepresentation", "fdr_method": "Benjamini-Hochberg",
        "fdr_scope": "all supplied tested sets before output truncation", "tested_sets": len(rows),
        "returned_sets": min(max_results, len(rows)), "truncated": len(rows) > max_results,
        "excluded_reference_genes": len(outside), "background_size": len(background),
        "selected_size": len(selected), "background_source": "explicit caller-supplied measured genes"},
        "limitations": ["Enrichment is conditional on the supplied background, gene identifiers and reference coverage.",
            "TF-target overlap and sequence motif occurrence are separate evidence types; neither alone establishes active direct or causal regulation.",
            "Motif-call thresholds and sequence provenance must be verified upstream; this tool tests supplied calls and does not infer missing scans."]}


def regulon_enrichment(selected_genes: List[str], background_genes: List[str],
                        reference_path: str, max_results: int = 100) -> dict:
    """BH-corrected overlap with an explicit TF-target TSV (tf,target columns)."""
    _validate_enrichment(selected_genes, background_genes, max_results)
    path = str(Path(reference_path).expanduser().resolve())
    # Header validation is cheap and gives callers a direct schema error.
    next(_table_rows(path, {"tf", "target"}), None)
    return run_sandboxed(_enrichment_worker, (selected_genes, background_genes, path, "regulon", max_results),
                         max_mem_mb=2000, timeout_sec=60)


def motif_enrichment(foreground_genes: List[str], background_genes: List[str],
                      motif_hits_path: str, max_results: int = 100) -> dict:
    """Test genuine supplied sequence-hit calls, separately from regulon overlap.

    The TSV requires gene,motif,hit and explicit hit/no-hit status for every
    background gene and tested motif. Unmeasured genes cannot become negatives.
    """
    _validate_enrichment(foreground_genes, background_genes, max_results)
    path = str(Path(motif_hits_path).expanduser().resolve())
    next(_table_rows(path, {"gene", "motif", "hit"}), None)
    return run_sandboxed(_enrichment_worker, (foreground_genes, background_genes, path, "motif", max_results),
                         max_mem_mb=2000, timeout_sec=60)
