"""Lagged associations and supplied-reference enrichment with explicit scope.

No inferred edge establishes direct or causal TF regulation. Curated regulon
overlap and measured sequence-motif hits are separate forms of evidence.
"""
import csv
import math
from numbers import Real
from pathlib import Path
from typing import List, Literal, Optional, TypedDict

import numpy as np

from devharness_core.normalization import count_like, log1p_counts_per_10k
from devharness_core.stages import parse_stages
from devharness_core.storage import ChunkedExpressionStore
from devharness_core.units import BIOLOGICAL_UNIT_COLUMNS, biological_identity_audit, declared_unit_counts
from devharness_core.watchdog import run_sandboxed
from devharness_server.tools_lineage import _bounded_int, _bounded_metadata, _file_sha256, _matrix_sha256
from devharness_server.tools_stat import _diagnostics, bh_adjust

_REPLICATE_IDENTITY_COLUMNS = frozenset((*BIOLOGICAL_UNIT_COLUMNS, "donor", "subject"))


class _NetworkEdge(TypedDict):
    tf: str
    target: str
    score: float
    coefficient: float
    sign: int


def _genes(values: List[str], name: str, maximum: int = 100000) -> List[str]:
    if not isinstance(values, list) or not 1 <= len(values) <= maximum:
        raise ValueError(f"{name} requires between 1 and {maximum} gene names")
    if any(not isinstance(gene, str) or not gene.strip() or gene != gene.strip() for gene in values):
        raise ValueError(name + " requires nonempty, trimmed gene names")
    if len(set(values)) != len(values):
        raise ValueError(name + " must contain distinct genes")
    return values


def _count_library_sizes(store: ChunkedExpressionStore, data_type: str) -> Optional[np.ndarray]:
    if data_type == "continuous":
        return None
    totals = np.zeros(store.shape[1], dtype=float)
    for _, values in store.iter_gene_chunks(128):
        if not np.isfinite(values).all():
            raise ValueError("Expression used to assess count library sizes must be finite")
        if not count_like(values):
            if data_type == "counts":
                raise ValueError("declared counts require nonnegative integer-valued expression")
            return None
        with np.errstate(over="ignore"):
            totals += values.sum(axis=0, dtype=float)
    if not np.isfinite(totals).all() or np.any(totals <= 0):
        raise ValueError("Count network inference requires finite positive library sizes for all samples")
    return totals


def _require_replicate_identity_column(column: Optional[str]) -> None:
    if not isinstance(column, str) or column.casefold() not in _REPLICATE_IDENTITY_COLUMNS:
        supported = ", ".join(sorted(_REPLICATE_IDENTITY_COLUMNS))
        raise ValueError("replicate_col requires one of these biological identity columns (" + supported + ")")


def _replicate_pairs(expression, metadata, replicate_col, times, stages, lag):
    _require_replicate_identity_column(replicate_col)
    if replicate_col not in metadata:
        raise ValueError("replicate_col must identify a declared biological identity column")
    units = metadata[replicate_col].astype("string").str.strip()
    identity = biological_identity_audit(
        metadata.assign(**{replicate_col: units}).to_dict(orient="records"), replicate_col,
    )
    if identity["problems"]:
        raise ValueError("Incomplete or split biological identities: " + ", ".join(identity["problems"]))

    past, future, weights = [], [], []
    unit_ids = units.to_numpy()
    positions = np.searchsorted(stages, times)
    unique_units = sorted(set(unit_ids))
    for unit in unique_units:
        included = unit_ids == unit
        observed = np.unique(positions[included])
        means = {stage: expression[:, included & (positions == stage)].mean(axis=1) for stage in observed}
        pairs = [(means[stage], means[stage + lag]) for stage in observed if stage + lag in means]
        # A single lagged pair cannot identify variation within a unit.
        if len(pairs) < 2:
            continue
        x, y = map(np.asarray, zip(*pairs))
        past.append(x - x.mean(axis=0))
        future.append(y - y.mean(axis=0))
        weights.append(np.full(len(pairs), 1 / len(pairs)))
    if len(past) < 2:
        raise ValueError("Replicate-aware association requires two declared units with at least two lagged pairs each")
    return np.concatenate(past), np.concatenate(future), np.concatenate(weights), {
        "replicate_col": replicate_col,
        "participating_replicates": len(past),
        "replicates_excluded_for_insufficient_pairs": len(unique_units) - len(past),
        "replicate_weighting": "equal_total_weight_per_declared_unit",
        "same_unit_and_time_summary": "mean_log_expression",
    }


def _history_adjusted_fit_inputs(x, y, histories, target, terms, variable, weights):
    from sklearn.preprocessing import StandardScaler

    columns = [np.ones(len(x)), x]
    if histories is not None:
        scaler = StandardScaler().fit(histories, sample_weight=weights)
        history = scaler.transform(histories)[:, 0]
        columns.append(history)
        terms.append(f"previous target expression ({target})")
        # Partial out the unpenalized baseline before penalizing the TF terms.
        weighted = history if weights is None else history * weights
        history_ss = history @ weighted
        if scaler.var_[0] > 1e-24 and history_ss > 0:
            x = x - np.outer(history, (weighted @ x) / history_ss)
            y -= history * ((weighted @ y) / history_ss)
            variable &= np.average(x**2, axis=0, weights=weights) > 1e-24
    return x, y, np.column_stack(columns)


def _network_worker(counts_path: str, metadata_path: str, candidate_tfs: List[str],
                    target_genes: List[str], time_col: str, lag: int, alpha: float,
                    max_cells: int, max_edges: int, data_type: str = "auto",
                    aggregation: str = "time_mean", replicate_col: str | None = None,
                    control_target_history: bool = False) -> dict:
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler

    metadata = _bounded_metadata(metadata_path, (replicate_col,) if replicate_col else ())
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
        library_sizes = _count_library_sizes(store, data_type)
        expression = np.asarray(store.get_chunk([lookup[gene] for gene in genes], slice(None)), dtype=float)
    if not np.isfinite(expression).all() or (library_sizes is not None and not count_like(expression)):
        raise ValueError("Requested expression must contain finite values consistent with the resolved scale")
    metadata = metadata.set_index("sample_id").loc[samples]
    times = parse_stages(metadata[time_col])
    stages = np.unique(times)
    if len(stages) < lag + 3:
        raise ValueError("Lagged association requires at least three time pairs after the requested lag")
    selected_hash = _matrix_sha256(expression, genes, samples)
    transformed = log1p_counts_per_10k(expression, library_sizes) if library_sizes is not None else expression
    unit_counts, incomplete_units = declared_unit_counts(metadata)
    weights, replicate_provenance = None, {}
    if aggregation == "replicate":
        past, future, weights, replicate_provenance = _replicate_pairs(
            transformed, metadata, replicate_col, times, stages, lag,
        )
    else:
        if aggregation == "sample":
            if len(stages) != len(samples):
                raise ValueError("Sample ordering requires unique time or pseudotime coordinates")
            if any(count < len(samples) for count in unit_counts.values()):
                raise ValueError("Sample ordering crosses repeated biological identities; use replicate aggregation")
            ordered = transformed[:, np.argsort(times)].T
        else:
            # This legacy summary weights observed profiles by stage without an
            # independence claim or a pairing across unrelated samples.
            ordered = np.asarray([transformed[:, times == stage].mean(axis=1) for stage in stages])
        past, future = ordered[:-lag], ordered[lag:]
    if not np.isfinite(past).all() or not np.isfinite(future).all():
        raise ValueError("Temporal aggregation overflowed; rescale expression")
    gene_index = {gene: i for i, gene in enumerate(genes)}
    edges: list[_NetworkEdge] = []
    constant_targets: list[str] = []
    constant_tfs: list[str] = []
    diagnostics = {}
    for target in target_genes:
        tfs = [tf for tf in candidate_tfs if tf != target]
        if not tfs:
            continue
        x = past[:, [gene_index[tf] for tf in tfs]]
        y = future[:, gene_index[target]].reshape(-1, 1)
        scaler_x = StandardScaler().fit(x, sample_weight=weights)
        scaler_y = StandardScaler().fit(y, sample_weight=weights)
        variable = scaler_x.var_ > 1e-24
        constant_tfs.extend(tf for tf, varies in zip(tfs, variable) if not varies)
        coefficients = np.zeros(len(tfs))
        terms = ["Intercept", *tfs]
        standardized_x, standardized_y, design = _history_adjusted_fit_inputs(
            scaler_x.transform(x), scaler_y.transform(y)[:, 0],
            past[:, gene_index[target], None] if control_target_history else None,
            target, terms, variable, weights,
        )
        if weights is not None:
            design *= np.sqrt(weights)[:, None]
        diagnostic = _diagnostics(design, terms)
        if diagnostic["condition_number"] is not None and not math.isfinite(diagnostic["condition_number"]):
            diagnostic["condition_number"] = None
        diagnostic["regularized"] = True
        diagnostics[target] = diagnostic
        if scaler_y.var_[0] <= 1e-24:
            constant_targets.append(target)
        elif variable.any():
            coefficients[variable] = Ridge(alpha=alpha, fit_intercept=True, solver="svd").fit(
                standardized_x[:, variable], standardized_y, sample_weight=weights).coef_
        if not np.isfinite(coefficients).all():
            raise ValueError("Regularized association returned nonfinite coefficients")
        for tf, coefficient in zip(tfs, coefficients):
            edges.append({"tf": tf, "target": target, "score": float(abs(coefficient)),
                          "coefficient": float(coefficient), "sign": int(np.sign(coefficient))})
    edges.sort(key=lambda edge: (-edge["score"], edge["tf"], edge["target"]))
    candidates = [{"tf": tf, "target": target} for tf in candidate_tfs for target in target_genes if tf != target]
    algorithm = {
        "time_mean": "lagged time-mean multivariable ridge association",
        "sample": "lagged sample-order multivariable ridge association",
        "replicate": "lagged within-replicate multivariable ridge association",
    }[aggregation]
    pairing_scope = {
        "time_mean": "Stage means weight measured profiles equally. No pairing or independent-replicate inference is performed.",
        "sample": "Pairs follow a unique declared ordering of measured profiles. Pseudotime is an inferred coordinate.",
        "replicate": "Pairs match a common ordered time grid within declared identities and are centered within identity.",
    }[aggregation]
    return {"edges": edges[:max_edges], "candidate_universe": candidates,
        "provenance": {"algorithm": algorithm,
            "aggregation": aggregation, **replicate_provenance,
            "interpretation": "associative temporal prediction; no direct or causal regulatory claim",
            "control_target_history": control_target_history,
            "autoregressive_baseline": (
                "unpenalized target expression at the start of each lag pair"
                if control_target_history else None
            ),
            "counts_path": counts_path, "metadata_path": metadata_path, "time_col": time_col,
            "metadata_sha256": metadata.attrs["source_sha256"], "selected_expression_sha256": selected_hash,
            "hash_scope": "requested numeric expression and ordered gene/sample IDs; complete metadata file",
            "time_points": len(stages), "time_pairs": len(past), "lag_observed_intervals": lag,
            "time_range": [float(stages[0]), float(stages[-1])], "sample_count": len(samples),
            "declared_unit_counts": unit_counts, "incomplete_unit_columns": incomplete_units,
            "independence_established": False,
            "requested_data_type": data_type,
            "resolved_data_type": "counts" if library_sizes is not None else "continuous",
            "measurement_declaration": ("legacy_auto_unverified" if data_type == "auto"
                                        else "explicit_caller_declaration_not_experimental_provenance"),
            "alpha": alpha,
            "transform": ("counts per 10,000, log1p and z-score" if library_sizes is not None
                          else "continuous expression z-score"),
            "library_sizes_sha256": (_matrix_sha256(library_sizes[None, :], ["library_size"], samples)
                                     if library_sizes is not None else None),
            "library_size_scope": "all input genes" if library_sizes is not None else None,
            "candidate_edge_count": len(candidates), "returned_edge_count": min(max_edges, len(edges)),
            "truncated": len(edges) > max_edges, "constant_targets": constant_targets,
            "constant_tfs": sorted(set(constant_tfs)), "design_diagnostics": diagnostics,
            "score": "absolute standardized ridge coefficient; not a probability",
            "candidate_universe_scope": "measured requested pairs; absence of a curated reference edge does not certify a negative"},
        "limitations": ["Lagged association is not direct binding or causal TF regulation; shared time trends, confounding and reverse regulation can produce edges.",
            pairing_scope,
            "Regularized coefficients have no reported frequentist p-values or q-values; ranking is exploratory, not clinical biomarker validation.",
            "Ridge stabilizes collinear designs but does not identify distinct causal effects of aliased TFs; rank diagnostics are reported before fitting.",
            "Zero coefficients have sign 0 (undetermined), and lag counts observed intervals even when time spacing is unequal."]}


def infer_regulatory_network(counts_path: str, metadata_path: str, candidate_tfs: List[str],
                              target_genes: List[str], time_col: str = "embryonic_age",
                              lag: int = 1, alpha: float = 1.0, max_cells: int = 2000,
                              max_edges: int = 1000,
                              data_type: Literal["counts", "continuous", "auto"] = "auto",
                              aggregation: Literal["time_mean", "sample", "replicate"] = "time_mean",
                              replicate_col: str | None = None,
                              control_target_history: bool = False) -> dict:
    """Rank measured TF-target pairs by regularized lagged temporal association.

    TFs and targets must be explicit. Fits run in a supervised spawned worker;
    the finite scores are exploratory. The default uses observed stage means.
    Set control_target_history to true for a Granger-style comparison against
    an unpenalized autoregressive baseline using target expression at the start
    of each lag pair. This estimates incremental temporal association. The
    default retains the TF-only lag model.
    Sample aggregation requires unique time or pseudotime coordinates.
    Replicate aggregation requires a declared biological identity column and
    fits within-identity lagged associations on aligned observed coordinates.
    Supported columns are biological_unit_id, donor_id, subject_id, specimen_id,
    donor and subject. Metadata declarations leave independence unverified.
    Use continuous for reviewed normalized expression, including integral values.
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
    if not isinstance(data_type, str) or data_type not in {"counts", "continuous", "auto"}:
        raise ValueError("data_type must be counts, continuous or auto")
    if not isinstance(aggregation, str) or aggregation not in {"time_mean", "sample", "replicate"}:
        raise ValueError("aggregation must be time_mean, sample or replicate")
    if not isinstance(control_target_history, bool):
        raise ValueError("control_target_history must be Boolean")
    if aggregation == "replicate":
        _require_replicate_identity_column(replicate_col)
    elif replicate_col is not None:
        raise ValueError("replicate_col requires replicate aggregation")
    return run_sandboxed(_network_worker, (str(Path(counts_path).expanduser().resolve()),
        str(Path(metadata_path).expanduser().resolve()), candidate_tfs, target_genes, time_col,
        lag, float(alpha), max_cells, max_edges, data_type, aggregation, replicate_col,
        control_target_history),
        max_mem_mb=2000, timeout_sec=120)


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


def _enrichment_rows(
    selected: set, background: set, gene_sets: dict, field: str,
    *, untestable: list[dict] | None = None,
) -> list[dict]:
    from scipy.stats import hypergeom
    rows = []
    for label, genes in sorted(gene_sets.items()):
        eligible = genes & background
        if not eligible:
            if untestable is not None:
                untestable.append({
                    field: label,
                    "status": "untestable",
                    "reason": "no_eligible_reference_genes",
                })
            continue
        overlap = len(eligible & selected)
        p_value = float(hypergeom.sf(
            overlap - 1, len(background), len(eligible), len(selected)
        ))
        rows.append({field: label, "overlap": overlap, "reference_size": len(eligible),
            "selected_size": len(selected), "background_size": len(background),
            "fold_enrichment": (
                overlap * len(background) / (len(selected) * len(eligible))
            ),
            "p_value": p_value})
    for row, q_value in zip(rows, bh_adjust([row["p_value"] for row in rows])):
        row["q_value"] = q_value
    return sorted(rows, key=lambda row: (row["p_value"], row[field]))


def _enrichment_worker(selected_genes: List[str], background_genes: List[str], path: str,
                        kind: str, max_results: int) -> dict:
    selected, background = set(selected_genes), set(background_genes)
    sets: dict[str, set[str]] = {}
    outside: set[str] = set()
    seen: dict[str, set[str]] = {}
    statuses: dict[tuple[str, str], bool] = {}
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
    field = "tf" if kind == "regulon" else "motif"
    untestable: list[dict] = []
    rows = _enrichment_rows(selected, background, sets, field, untestable=untestable)
    return {"results": rows[:max_results],
        "untestable": untestable[:max_results], "provenance": {
        "reference_path": path, "reference_sha256": _file_sha256(path),
        "reference_validation": "local table schema and content checked; source authenticity is caller-supplied",
        "evidence_type": "supplied curated TF-target overlap" if kind == "regulon" else "supplied sequence motif-hit calls",
        "test": "one-sided hypergeometric overrepresentation", "fdr_method": "Benjamini-Hochberg",
        "fdr_scope": "all supplied tested sets before output truncation",
        "testability_rule": "at least one reference member in the measured background",
        "tested_sets": len(rows), "untestable_sets": len(untestable),
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
