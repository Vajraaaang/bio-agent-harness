"""Fixed, public-input-only PyDESeq2 discovery comparators.

This bounded Python/Wald lane is distinct from the temporal R native contracts.
Receipts are software execution traces supplied by a trusted caller, not signatures.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import warnings

import numpy as np
import pandas as pd


MAX_EXACT_ENGINE_COUNT = 2**53 - 1


PARAMETERS = {
    "n_cpus": 1, "normalization": "ratio", "dispersion_fit_type": "parametric",
    "refit_cooks": False, "cooks_filter": True, "independent_filter": False,
    "low_memory": False,
    "alpha": 0.05, "multiple_testing": "BH_full_gene_times_contrast_family",
    "joint_sex_batch_contrasts": ["female_treated_vs_control", "male_treated_vs_control", "male_minus_female", "batch_2_vs_batch_1"],
    "parameter_source_kind": "assistant_procedure_choice",
    "parameter_source_ref": "baseline/specification.md",
}


def _hash(value):
    def convert(item):
        if isinstance(item, np.generic):
            return item.item()
        raise TypeError(f"non-JSON value: {type(item).__name__}")
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False, default=convert).encode()).hexdigest()


def _frame(frame):
    # pandas renders missing values as JSON null; source order remains explicit.
    return {"index": list(frame.index), "columns": list(frame.columns),
            "values": json.loads(frame.to_json(orient="values"))}


def _claim(claim_id, *, kind="design_issue", scope="design_diagnostic", direction="not_applicable",
           factor=None, contrast=None, genes=None, refs=None, issue=None, limitations=None,
           assertion="discovery", levels=None):
    return {"claim_id": claim_id, "type": kind, "assertion_status": assertion,
            "confidence": "moderate", "scope": scope, "direction": direction,
            "factor": factor, "contrast": contrast, "genes": genes or [], "samples": [],
            "evidence_refs": refs or [], "selection_scope": "fixed", "limitations": limitations or [],
            "issue_code": issue, "effect_direction_by_level": levels or {}}


def _receipt(input_id, model, result, *, contrast=None, scope="design_diagnostic",
             factor=None, adjustment_factors=None, method="design_diagnostics", units=0,
             aggregation=None, issue=None):
    model_id, result_id = _hash(model), _hash(result)
    receipt = {"status": "executed", "input_id": input_id, "model_id": model_id,
               "contrast_id": contrast, "result_id": result_id, "scope": scope,
               "factor": factor, "adjustment_factors": adjustment_factors or [], "method": method,
               "biological_unit_count": units, "aggregation": aggregation,
               "input_sha256": input_id, "model_sha256": model_id, "result_sha256": result_id,
               "issue_code": issue}
    receipt["model_record"] = json.loads(json.dumps(model, allow_nan=False))
    if scope == "design_diagnostic":
        receipt["diagnostic_result"] = result
    else:
        receipt["fit_result"] = json.loads(json.dumps(result, allow_nan=False))
    return "sha256:" + _hash(receipt), receipt


def _bh(rows):
    positions = [i for i, row in enumerate(rows) if row["status"] == "fitted"]
    positions.sort(key=lambda i: rows[i]["p"])
    if positions:
        adjusted = np.minimum.accumulate((len(rows) * np.array([rows[i]["p"] for i in positions])
                                         / np.arange(1, len(positions) + 1))[::-1])[::-1]
        for i, q in zip(positions, adjusted):
            rows[i]["q"] = max(rows[i]["p"], min(1.0, float(q)))


def run_baseline(counts, metadata, *, arm, provenance):
    """Return {payload, evidence_lookup, diagnostics}; never consume private truth.

Counts are gene-by-sample DataFrames; metadata has exact unique sample_id values.
Invalid inputs raise ValueError before fitting. Scientific refusals and fit failures
return distinct explicit terminal states. Every reference hashes actual local facts.
"""
    if arm not in {"naive", "design_aware"}:
        raise ValueError("arm must be naive or design_aware")
    if not isinstance(provenance, dict) or provenance.get("representation") != "raw_counts":
        raise ValueError("explicit raw_counts representation required")
    if set(provenance) & {"family", "seed", "truth", "evaluator_path", "planted_membership"}:
        raise ValueError("private evaluator arguments are not baseline provenance")
    if not isinstance(counts, pd.DataFrame) or not isinstance(metadata, pd.DataFrame):
        raise ValueError("counts and metadata must be DataFrames")
    if counts.empty or counts.shape[1] > 128 or counts.memory_usage(deep=True).sum() > 100_000_000:
        raise ValueError("count shape exceeds bounded adapter or is empty")
    if not counts.index.is_unique or not counts.columns.is_unique or not all(isinstance(x, str) for x in list(counts.index) + list(counts.columns)):
        raise ValueError("count gene/sample IDs must be unique strings")
    try:
        outside_engine_range = np.any(counts.to_numpy() > MAX_EXACT_ENGINE_COUNT)
    except (TypeError, ValueError) as exc:
        raise ValueError("counts must be numeric before engine conversion") from exc
    if outside_engine_range:
        raise ValueError("counts exceed exactly representable engine range 0..2**53-1; conversion is refused")
    try:
        values = counts.to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("counts must be numeric") from exc
    if not np.isfinite(values).all() or np.any(values < 0) or np.any(values != np.floor(values)) or np.any(values >= np.iinfo(np.int64).max):
        raise ValueError("counts must be finite nonnegative representable integers")
    if not metadata.columns.is_unique or "sample_id" not in metadata or metadata.sample_id.isna().any() or not metadata.sample_id.is_unique:
        raise ValueError("sample metadata IDs must be present and unique")
    if set(metadata.sample_id) != set(counts.columns):
        raise ValueError("sample metadata IDs must exactly match count columns")
    roles = provenance.get("metadata_roles")
    if not isinstance(roles, dict) or "condition" not in roles or any(not isinstance(col, str) or col not in metadata for col in roles.values()):
        raise ValueError("metadata_roles must map condition and supplied roles to public columns")
    metadata_source = metadata.copy(deep=True)
    meta = metadata.set_index("sample_id", drop=False).loc[counts.columns].copy(deep=True)
    if meta[list(roles.values())].isna().any().any():
        raise ValueError("mapped public design columns must not contain missing values")
    data = pd.DataFrame(values.astype(np.int64), index=counts.index, columns=counts.columns)
    alignment = {"source_order": list(metadata_source.sample_id), "aligned_order": list(counts.columns),
                 "source_positions": [list(metadata_source.sample_id).index(x) for x in counts.columns]}
    input_id = _hash({"counts": _frame(counts), "metadata": _frame(metadata_source),
                      "provenance": provenance, "alignment": alignment})
    runtime = {"python": platform.python_version(), "pydeseq2_version": importlib.metadata.version("pydeseq2"),
               "numpy_version": np.__version__, "pandas_version": pd.__version__,
               "scipy_version": importlib.metadata.version("scipy")}
    package = importlib.metadata.distribution("pydeseq2")
    runtime["pydeseq2_source_sha256"] = {
        name: hashlib.sha256(Path(package.locate_file(f"pydeseq2/{name}.py")).read_bytes()).hexdigest()
        for name in ("dds", "ds", "default_inference", "preprocessing", "utils")}
    diagnostics = {"input_id": input_id, "source_counts_sha256": _hash(_frame(counts)),
                   "source_metadata_sha256": _hash(_frame(metadata_source)), "alignment": alignment,
                   "alignment_sha256": _hash(alignment), "runtime": runtime, "parameters": dict(PARAMETERS),
                   "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   "warnings": [], "fallback_records": [], "aggregation": None, "execution_records": {}}
    payload = {"schema_version": "discovery_claims/1", "status": "null_result", "claims": [],
               "gene_statistics": [], "limitations": [], "error": None, "execution_evidence_refs": []}
    evidence = {}
    wrapper = {"payload": payload, "evidence_lookup": evidence, "diagnostics": diagnostics}
    condition_col = roles["condition"]
    independence = provenance.get("biological_independence")
    known_status = independence.get("status") if isinstance(independence, dict) else None
    provisional = known_status != "assumed_independent" and arm == "naive"
    units = len(meta) if known_status == "assumed_independent" else 0
    payload["limitations"].append("Fixed synthetic software-verification procedure; no scientific optimality, calibration or clinical validation established.")
    if known_status == "assumed_independent":
        payload["limitations"].append("Inference is conditional on the supplied synthetic independent-specimen assumption; unique sample IDs do not verify biological independence.")
    if arm == "naive" and set(roles) & {"sex", "batch"}:
        payload["limitations"].append("Deliberately naive condition-only comparator omits supplied sex/batch terms; its estimand is an unadjusted marginal association.")

    def diagnostic(issue, observations, *, refusal=False):
        result = {"issue_code": issue, "observations": observations}
        model = {"method": "design_diagnostics", "runtime": runtime, "source_sha256": diagnostics["source_sha256"],
                 "roles": roles, "independence": independence}
        ref, record = _receipt(input_id, model, result, factor=condition_col, units=units,
                               aggregation=diagnostics["aggregation"], issue=issue)
        evidence[ref] = record
        diagnostics["execution_records"][ref] = {"model": model, "result": result}
        diagnostics.setdefault("diagnostic_results", []).append(result)
        payload["execution_evidence_refs"].append(ref)
        payload["claims"].append(_claim(issue, issue=issue, refs=[ref], factor=condition_col,
                                        limitations=[str(observations)]))
        if refusal:
            payload["status"] = "scientific_refusal"
        return wrapper

    if known_status is None:
        if arm == "design_aware":
            return diagnostic("missing_independence_provenance", {"sample_count": len(meta), "declaration": independence}, refusal=True)
        payload["limitations"].append("Provisional run-level comparator: biological independence provenance is missing; fitted hypotheses do not establish biological discoveries.")
    elif known_status == "repeated_same_library":
        required = [independence.get(key) for key in ("biological_unit_column", "library_column", "run_column")]
        if any(not isinstance(col, str) or col not in meta for col in required) or meta[required].isna().any().any():
            raise ValueError("same-library declaration requires complete donor/library/run columns")
        donor, library, run = required
        if meta.duplicated([library, run]).any() or (meta.groupby(library)[[donor, condition_col]].nunique() != 1).any().any() or (meta.groupby(donor)[library].nunique() != 1).any():
            raise ValueError("library must map to one donor, one condition and unique runs; multiple specimens are unsupported")
        units = meta[donor].nunique()
        groups = {str(key): list(group.index) for key, group in meta.groupby(library, sort=False)}
        if arm == "design_aware":
            modeled_roles = [role for role in ("condition", "sex", "batch") if role in roles]
            inconsistent = []
            for key, ids in groups.items():
                variable = {role: {"column": roles[role], "levels": meta.loc[ids, roles[role]].drop_duplicates().tolist()}
                            for role in modeled_roles if meta.loc[ids, roles[role]].nunique() > 1}
                if variable:
                    inconsistent.append({"library_id": key, "varying_roles": variable, "source_sample_ids": ids})
            if inconsistent:
                return diagnostic("invalid_design", {
                    "modeled_roles": modeled_roles, "inconsistent_library_covariates": inconsistent,
                    "same_library_mapping": groups, "biological_unit_count": int(units),
                    "message": "Modeled covariates vary within a physical library; aggregation cannot choose one run's annotation. Run-level batch modeling requires a different procedure."}, refusal=True)
        aggregated = pd.DataFrame({key: data[ids].sum(axis=1) for key, ids in groups.items()})
        if arm == "design_aware" and np.any(aggregated.to_numpy() > MAX_EXACT_ENGINE_COUNT):
            raise ValueError("aggregated counts exceed exactly representable engine range 0..2**53-1; conversion is refused")
        aggregated_meta = meta.groupby(library, sort=False).first().copy()
        aggregated_meta.index = aggregated.columns
        aggregated_meta["sample_id"] = aggregated.columns
        donor_design = pd.get_dummies(aggregated_meta[donor], drop_first=True, dtype=float)
        cond_indicator = (aggregated_meta[condition_col] == sorted(aggregated_meta[condition_col].unique())[-1]).astype(float)
        nested = np.column_stack([np.ones(len(aggregated_meta)), donor_design.to_numpy(), cond_indicator])
        diagnostics["aggregation"] = {"method": "sum_same_library_runs", "biological_unit_count": int(units),
                                      "applied": arm == "design_aware",
                                      "library_to_source_samples": groups, "aggregated_counts_sha256": _hash(_frame(aggregated)),
                                      "library_to_biological_unit": {key: str(meta.loc[ids[0], donor]) for key, ids in groups.items()},
                                      "sample_to_library_run_unit": {str(sample): {"library": str(row[library]), "run": str(row[run]), "biological_unit": str(row[donor])} for sample, row in meta.iterrows()},
                                      "nested_donor_condition_rank": int(np.linalg.matrix_rank(nested)),
                                      "nested_donor_condition_columns": nested.shape[1]}
        diagnostic("technical_replicates", {"run_count": len(meta), "biological_unit_count": int(units),
                   "same_library_mapping": groups, "aggregation_applied": arm == "design_aware"})
        if arm == "design_aware":
            data, meta = aggregated, aggregated_meta
            payload["limitations"].append(f"Same-library raw sequencing runs summed; {units} biological units analyzed. Donor+condition is nested and is not fitted.")
        else:
            payload["limitations"].append(f"Provisional misspecified comparator treats sequencing runs as independent model rows; there are {units} biological units, not {len(meta)}.")
    elif known_status != "assumed_independent":
        raise ValueError("unsupported biological independence declaration")

    def binary(column, preferred):
        levels = sorted(meta[column].unique().tolist())
        if len(levels) != 2:
            raise ValueError("mapped design column must have two observed levels")
        if set(levels) == set(preferred):
            levels = list(preferred)
        return (meta[column] == levels[1]).astype(float).to_numpy(), levels

    c, c_levels = binary(condition_col, ("control", "treated"))
    design = pd.DataFrame({"Intercept": np.ones(len(meta)), "Condition": c}, index=meta.index)
    contrasts = {"treated_vs_control": np.array([0., 1.])}
    role_levels = {"condition": {"column": condition_col, "levels": c_levels}}
    has_sex = arm == "design_aware" and "sex" in roles
    has_batch = arm == "design_aware" and "batch" in roles
    adjustment = [roles[role] for role in ("sex", "batch") if (role == "sex" and has_sex) or (role == "batch" and has_batch)]
    if has_batch:
        b, levels = binary(roles["batch"], ("batch_1", "batch_2"))
        design = pd.DataFrame({"Intercept": np.ones(len(meta)), "Batch2": b, "Condition": c}, index=meta.index)
        role_levels["batch"] = {"column": roles["batch"], "levels": levels}
        contrasts = {"treated_vs_control": np.array([0., 0., 1.])}
    if has_sex:
        male, levels = binary(roles["sex"], ("female", "male"))
        design["Male"], design["ConditionMale"] = male, c * male
        role_levels["sex"] = {"column": roles["sex"], "levels": levels}
        female = np.zeros(design.shape[1])
        female[list(design.columns).index("Condition")] = 1.
        interaction = np.zeros(design.shape[1])
        interaction[list(design.columns).index("ConditionMale")] = 1.
        contrasts = {"female_treated_vs_control": female, "male_treated_vs_control": female + interaction,
                     "male_minus_female": interaction}
    if has_batch:
        batch_vector = np.zeros(design.shape[1])
        batch_vector[list(design.columns).index("Batch2")] = 1.
        contrasts["batch_2_vs_batch_1"] = batch_vector
    rank = int(np.linalg.matrix_rank(design.to_numpy()))
    diagnostics["design"] = {"columns": list(design.columns), "matrix": design.to_numpy().tolist(),
                             "row_ids": list(design.index), "rank": rank, "residual_df": len(design) - rank,
                             "role_levels": role_levels, "contrasts": {k: v.tolist() for k, v in contrasts.items()},
                             "condition_number": float(np.linalg.cond(design)) if rank == design.shape[1] else None}
    diagnostics["hypothesis_family"] = {"gene_ids": list(data.index), "contrast_ids": list(contrasts),
                                        "test_count": len(data.index) * len(contrasts)}
    if rank < design.shape[1]:
        return diagnostic("non_identifiable_design", diagnostics["design"], refusal=True)
    if len(design) <= rank or min(np.bincount(c.astype(int))) < 2:
        return diagnostic("insufficient_biological_units", diagnostics["design"], refusal=True)
    for vector in contrasts.values():
        if vector.shape != (design.shape[1],) or not np.isfinite(vector).all() or not np.any(vector):
            return diagnostic("invalid_contrast", {"vector": vector.tolist()}, refusal=True)
    if not np.any((data.to_numpy() > 0).all(axis=1)):
        payload.update(status="operational_failure", claims=[], gene_statistics=[],
                       error={"code": "ratio_normalization_unavailable", "message": "RLE ratio normalization has no positive reference gene; iterative fallback is forbidden."})
        return wrapper

    from pydeseq2.dds import DeseqDataSet
    from pydeseq2.ds import DeseqStats
    rows = []
    try:
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always")
            dds = DeseqDataSet(counts=data.T, metadata=meta, design=design, fit_type="parametric",
                               size_factors_fit_type="ratio", refit_cooks=False, n_cpus=1,
                               quiet=True, low_memory=False)
            dds.deseq2()
            diagnostics["normalization"] = {"size_factors": dds.obs["size_factors"].to_numpy().tolist(),
                                            "positive_reference_gene_count": int((data.to_numpy() > 0).all(axis=1).sum())}
            prior_available = bool(np.isfinite(dds.uns["prior_disp_var"]))
            diagnostics["dispersion"] = {"actual_fit_type": dds.uns.get("disp_function_type"),
                                         "prior_dispersion_variance": float(dds.uns["prior_disp_var"]) if prior_available else None,
                                         "empirical_bayes": prior_available}
            if dds.uns.get("disp_function_type") != "parametric":
                diagnostics["fallback_records"].append({"phase": "dispersion_trend", "requested": "parametric", "actual": dds.uns.get("disp_function_type")})
            if not np.isfinite(dds.uns["prior_disp_var"]) or not np.isfinite(dds.obs["size_factors"]).all() or np.any(dds.obs["size_factors"] <= 0):
                raise ValueError("nonfinite empirical-Bayes prior or invalid RLE offsets")
            convergence = [name for name in ("_genewise_converged", "_MAP_converged", "_LFC_converged") if name in dds.var]
            diagnostics["convergence"] = {name: [None if pd.isna(v) else bool(v) for v in dds.var[name]] for name in convergence}
            cooks = np.asarray(dds.layers["cooks"])
            diagnostics["cook_influence"] = {"available": True, "maximum_by_gene": [float(np.max(x[np.isfinite(x)])) if np.isfinite(x).any() else None for x in cooks.T],
                                              "filtered_gene_ids": list(dds.var.index[dds.var["_pvalue_cooks_outlier"].to_numpy(dtype=bool)])}
            for contrast_id, vector in contrasts.items():
                stat = DeseqStats(dds, contrast=vector, independent_filter=False, cooks_filter=True, n_cpus=1, quiet=True)
                stat.summary()
                for gene, row in stat.results_df.iterrows():
                    p, effect = float(row.pvalue), float(row.log2FoldChange)
                    failed = any(pd.notna(dds.var.loc[gene, name]) and not bool(dds.var.loc[gene, name]) for name in convergence)
                    reason = "fit_nonconvergence" if failed else ("unavailable_estimate_or_Cook_influence_filter" if not np.isfinite(p) or not 0 <= p <= 1 or not np.isfinite(effect) else None)
                    rows.append({"gene_id": gene, "contrast_id": contrast_id, "status": "failed" if reason else "fitted",
                                 "p": None if reason else p, "q": None, "effect": None if reason else effect,
                                 "effect_scale": "log2", "evidence_refs": [], "reason": reason})
            diagnostics["warnings"] = [{"category": type(w.message).__name__, "message": str(w.message)} for w in recorded]
    except Exception as exc:
        diagnostics["warnings"] = [{"category": type(w.message).__name__, "message": str(w.message)} for w in recorded]
        diagnostics["fit_error"] = {"type": type(exc).__name__, "message": str(exc)}
        payload.update(status="operational_failure", claims=[], gene_statistics=[],
                       error={"code": "fit_failure", "message": f"{type(exc).__name__}: {exc}"})
        return wrapper
    _bh(rows)
    model = {"kind": "statistical_model", "family": "negative_binomial", "method": "pydeseq2_wald", "runtime": runtime, "source_sha256": diagnostics["source_sha256"], "parameters": PARAMETERS,
             "design": diagnostics["design"], "normalization": diagnostics["normalization"],
             "hypothesis_family": diagnostics["hypothesis_family"],
             "dispersion": diagnostics["dispersion"], "convergence": diagnostics["convergence"],
             "aggregation": diagnostics["aggregation"], "provisional": provisional}
    refs = {}
    for contrast_id in contrasts:
        selected = [row for row in rows if row["contrast_id"] == contrast_id]
        interaction = contrast_id == "male_minus_female"
        batch_contrast = contrast_id == "batch_2_vs_batch_1"
        stratum = contrast_id in {"female_treated_vs_control", "male_treated_vs_control"}
        scope = "interaction" if interaction else "subgroup_association" if stratum else "adjusted_association" if adjustment else "marginal_association"
        factor = roles["sex"] if interaction else roles["batch"] if batch_contrast else condition_col
        adj = [condition_col] + ([roles["sex"]] if has_sex else []) if batch_contrast else adjustment
        ref, record = _receipt(input_id, model, selected, contrast=contrast_id, scope=scope, factor=factor,
                               adjustment_factors=adj, method="pydeseq2_wald", units=int(units), aggregation=diagnostics["aggregation"])
        refs[contrast_id] = ref
        evidence[ref] = record
        diagnostics["execution_records"][ref] = {"model": model, "result": json.loads(json.dumps(selected, allow_nan=False))}
        payload["execution_evidence_refs"].append(ref)
        for row in selected:
            row["evidence_refs"] = [ref]
        if not interaction:
            for direction, sign in (("positive", 1), ("negative", -1)):
                hits = [r["gene_id"] for r in selected if r["status"] == "fitted" and r["q"] <= PARAMETERS["alpha"] and r["effect"] * sign > 0]
                if hits:
                    payload["claims"].append(_claim(f"{contrast_id}_{direction}", kind="gene_set_effect", scope=scope,
                        direction=direction, factor=factor, contrast=contrast_id, genes=hits, refs=[ref],
                        assertion="hypothesis" if provisional else "discovery", limitations=payload["limitations"].copy()))
    if "male_minus_female" in contrasts:
        table = {(row["gene_id"], row["contrast_id"]): row for row in rows}
        for first, second in (("positive", "negative"), ("negative", "positive")):
            hits = []
            for gene in data.index:
                female, male, interaction = [table[gene, name] for name in ("female_treated_vs_control", "male_treated_vs_control", "male_minus_female")]
                if all(r["status"] == "fitted" and r["q"] <= PARAMETERS["alpha"] for r in (female, male, interaction)) and (female["effect"] > 0) == (first == "positive") and (male["effect"] > 0) == (second == "positive"):
                    hits.append(gene)
            if hits:
                payload["claims"].append(_claim(f"interaction_{first}_{second}", kind="interaction", scope="interaction", direction="mixed",
                    factor=roles["sex"], contrast="male_minus_female", genes=hits, refs=list(refs.values()),
                    levels={str(role_levels["sex"]["levels"][0]): first, str(role_levels["sex"]["levels"][1]): second}, limitations=payload["limitations"].copy()))
    payload["gene_statistics"] = rows
    if not any(row["status"] == "fitted" for row in rows):
        diagnostics["unavailable_gene_statistics"] = rows
        payload.update(status="operational_failure", claims=[], gene_statistics=[],
                       error={"code": "all_estimates_unavailable", "message": "Every prespecified estimate is unavailable; this is not a valid null analysis."})
        return wrapper
    payload["status"] = "analysis_complete" if any(c["assertion_status"] == "discovery" and c["type"] in {"gene_set_effect", "interaction"} for c in payload["claims"]) else "null_result"
    return wrapper
