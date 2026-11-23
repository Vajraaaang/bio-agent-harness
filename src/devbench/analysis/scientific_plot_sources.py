"""Pure projections of named saved outputs into the common plotting contract.

These functions never open paths, execute tools, fit models or authenticate an
artifact. The calling reader must verify artifact bytes and run membership.
The saved-tool envelope supplies the *actual* producing tool and parameters;
it is not evidence of execution on its own. Unknown arrays are not guessed.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

from devharness_core.provenance import canonical_json

from .scientific_plot_contract import MAX_BYTES, require_fields, require_unique

INPUT_FORMAT = "devbench.scientific-plot-input/1"
TOOL_FORMAT = "devbench.saved-tool-output/1"
SCRNA_FORMAT = "devbench.saved-scrna-tables/1"
SAVED_NETWORK_ALGORITHMS = {
    "lagged time-mean multivariable ridge association": "time_mean",
    "lagged sample-order multivariable ridge association": "sample",
    "lagged within-replicate multivariable ridge association": "replicate",
}


class UnsupportedSavedPlotSource(ValueError):
    """No explicit supported producer/schema was supplied."""


class MissingSavedPlotInputs(ValueError):
    """A known output did not retain the quantities needed by a plot."""


def _digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError("saved_plot_requires_" + name)
    return value


def _bounded(value):
    try:
        raw = json.dumps(value, allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError) as error:
        raise ValueError("saved_plot_requires_finite_JSON") from error
    if len(raw) > MAX_BYTES:
        raise ValueError("saved_plot_exceeds_byte_budget")


def _packet(plots, limitations=(), title="Saved scientific results"):
    if not plots:
        raise MissingSavedPlotInputs("required_saved_plot_inputs_absent")
    return {"format": INPUT_FORMAT, "title": title, "plots": plots,
            "limitations": list(limitations)}


def _request(capability, data, identity=None):
    return {"id": identity or capability, "capability": capability, "data": data}


def _validate_packet(packet):
    # Use the owning scientific adapters, including their domain/membership
    # checks, without making up a source receipt just to validate an input.
    from .scientific_plot_suite import _registry
    require_fields(packet, ("format", "plots"), ("title", "limitations"))
    if packet["format"] != INPUT_FORMAT or not isinstance(packet["plots"], list) or not 1 <= len(packet["plots"]) <= 64:
        raise ValueError("scientific_plot_input_envelope_invalid")
    if "title" in packet:
        _text(packet["title"], "plot_title")
    registry = _registry()
    identities = []
    for index, request in enumerate(packet["plots"]):
        require_fields(request, ("capability", "data"), ("id",))
        if request["capability"] not in registry:
            raise UnsupportedSavedPlotSource("saved_plot_capability_unsupported:" + str(request["capability"]))
        identity = request.get("id", request["capability"] + "-" + str(index + 1))
        import re
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,79}", identity):
            raise ValueError("saved_plot_request_identity_invalid")
        identities.append(identity)
        registry[request["capability"]][0].build_panels(request["capability"], deepcopy(request["data"]))
    require_unique(identities, "plot_request_ids")
    if "limitations" in packet and (not isinstance(packet["limitations"], list)
            or any(not isinstance(v, str) or not v for v in packet["limitations"])):
        raise ValueError("saved_plot_limitations_invalid")
    return deepcopy(packet)


def _display_histogram_edges(values):
    """Twenty full-range display bins; no scientific threshold is selected."""
    values = [value for value in values if value is not None]
    if not values:
        return [-.5, .5]  # Explicit empty-axis scaffold; no values are imputed.
    low, high = min(values), max(values)
    if low == high:
        width = max(abs(low) * .05, .5)
        lower, upper = low - width, high + width
        low = lower if math.isfinite(lower) else low
        high = upper if math.isfinite(upper) else high
    # Convex interpolation avoids overflowing high-low for extreme signed
    # effects. Deduplicate representationally identical edges without clipping.
    interior = {low * (1 - i / 20) + high * (i / 20) for i in range(1, 20)}
    return [low, *sorted(value for value in interior if low < value < high), high]


def _bulk_ma_marginals(payloads, evidence):
    """Actual saved scalar log2 effects, with independent marginal availability."""
    requests = []
    for index, payload in enumerate(payloads):
        contrast = payload.get("computed_contrast")
        if (payload.get("kind") != "effects" or not isinstance(contrast, dict)
                or contrast.get("operation") != "comparison_minus_reference"
                or contrast.get("effect_scale") not in {"log2_relative_expression", "log2_fold_change"}):
            continue
        saved_rows = payload.get("gene_statistics")
        if not isinstance(saved_rows, list) or not saved_rows:
            continue
        ids = [record.get("gene_id") for record in saved_rows]
        require_unique(ids, "bulk_MA_gene_ids")
        if set(ids) != set(evidence["gene_ids"]):
            raise ValueError("bulk_MA_effect_and_mean_gene_family_mismatch")
        if any(record.get("estimate_status") == "estimated" and len(record.get("values", [])) > 1 for record in saved_rows):
            continue  # Multi-coordinate effects require their own explicit recipe.
        means = dict(zip(evidence["gene_ids"], evidence["mean_normalized_counts"]))
        rows = []
        for saved in saved_rows:
            identity, values, status = saved["gene_id"], saved.get("values", []), saved.get("estimate_status")
            effect = values[0] if status == "estimated" and len(values) == 1 else None
            if effect is not None and (type(effect) not in (int, float) or not math.isfinite(effect)):
                raise ValueError("bulk_MA_requires_finite_saved_scalar_effect")
            source = {"id": identity, "key": "gene:" + identity, "entity_kind": "gene",
                "x": math.log10(means[identity] + 1), "y": effect,
                "group": "Saved effect " + str(status), "status": "recorded",
                "details": {"mean_normalized_count": means[identity], "saved_effect": effect,
                    "estimate_status": status, "gene_statistics": deepcopy(saved), "computed_contrast": deepcopy(contrast),
                    "coordinate_status_scope": "Recorded mean remains available independently of a failed or missing effect."}}
            if effect is None:
                source["reason"] = saved.get("missing_reason") or saved.get("failure_code") or saved.get("reason") or "Saved scalar effect unavailable; status=" + str(status)
            rows.append(source)
        requests.append(_request("scatter_marginals", {
            "x_measurement": {"id": "log10_mean_normalized_count_plus1", "name": "log10(saved mean normalized count + 1)",
                "unit": "log10_normalized_count_plus1", "declared_transform": "log10(mean_normalized_counts + 1); display only; already applied"},
            "y_measurement": {"id": "saved_log2_effect", "name": "Saved log2 effect", "unit": contrast["effect_scale"],
                "declared_transform": "identity; exact saved one-coordinate effect; no shrinkage or refit"},
            "rows": rows, "x_bin_edges": _display_histogram_edges([r["x"] for r in rows]),
            "y_bin_edges": _display_histogram_edges([r["y"] for r in rows]), "histogram_scale": "count"},
            "bulk-ma-marginals-" + str(index)))
    return requests


def _bulk(document):
    from .bulk_plot_data import _validate_saved_evidence
    diagnostics = document["diagnostics"]
    evidence = diagnostics.get("plot_evidence")
    if not isinstance(evidence, dict):
        raise MissingSavedPlotInputs("bulk_saved_aligned_count_evidence_absent")
    _validate_saved_evidence(evidence)
    if evidence["status"] != "available":
        raise MissingSavedPlotInputs("bulk_saved_evidence_unavailable:" + evidence["reason"])
    if (evidence["sample_ids"] != [s["sample_id"] for s in evidence["samples"]]
            or evidence["size_factors"] != [s["size_factor"] for s in evidence["samples"]]):
        raise ValueError("bulk_saved_sample_or_factor_alignment_mismatch")
    for counts, mean in zip(evidence["raw_counts"], evidence["mean_normalized_counts"]):
        expected = sum(value / factor for value, factor in zip(counts, evidence["size_factors"])) / len(counts)
        if not math.isclose(mean, expected, rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError("bulk_saved_normalized_mean_mismatch")
    payloads = [item["payload"] for item in document.get("target_results", [])
                if isinstance(item, dict) and isinstance(item.get("payload"), dict)]
    declared_units = {p["biological_unit"] for p in payloads if isinstance(p.get("biological_unit"), str)}
    unit = diagnostics.get("biological_units", {}).get("population_unit")
    if unit is not None:
        declared_units.add(unit)
    plots, limitations = _bulk_ma_marginals(payloads, evidence), []
    if plots:
        limitations.append("MA marginals use actual saved means and scalar effects. Failed/missing effects remain null while their available means still contribute independently to the X distribution.")
        limitations.append("Marginal bins are display-only equal-width full-range bins (up to 20); an empty coordinate uses [-0.5, 0.5] only as an empty axis scaffold, never an imputed estimate.")
    normalization = diagnostics.get("normalization", {})
    if len(declared_units) == 1 and normalization.get("method") and all(
            s.get("unit_id") and s.get("condition") for s in evidence["samples"]):
        plots.append(_request("bulk_library_qc", {"population_unit": "sample", "biological_unit": next(iter(declared_units)),
            "samples": [{"sample_id": s["sample_id"], "biological_unit_id": s["unit_id"], "condition": s["condition"]}
                        for s in evidence["samples"]], "measurement": "raw_integer_count",
            "gene_ids": evidence["gene_ids"], "counts": evidence["raw_counts"],
            "size_factors": [{"sample_id": name, "size_factor": factor}
                             for name, factor in zip(evidence["sample_ids"], evidence["size_factors"])],
            "size_factor_method": normalization["method"]}))
    else:
        limitations.append("Library QC projection unavailable: saved normalization method or declared biological/condition identities absent.")
    calibration = diagnostics.get("calibration", {})
    estimates = calibration.get("gene_estimates", {})
    if estimates and calibration.get("trend_type") and calibration.get("method"):
        if set(estimates) - set(evidence["gene_ids"]):
            raise ValueError("bulk_dispersion_gene_membership_mismatch")
        genes, trend = [], {}
        for identity, mean in zip(evidence["gene_ids"], evidence["mean_normalized_counts"]):
            saved = estimates.get(identity, {})
            final = saved.get("dispersion_alpha")
            lower, upper = calibration.get("min_disp"), calibration.get("max_disp")
            boundary = final is not None and ((lower is not None and final <= lower) or (upper is not None and final >= upper))
            gene = {"gene_id": identity, "mean": mean, "gene_wise": saved.get("genewise_dispersion"),
                    "final": final, "boundary_warning": bool(boundary)}
            if saved.get("dispersion_converged") is not True:
                gene["reason"] = "Saved dispersion unavailable or nonconverged."
            genes.append(gene)
            value = saved.get("trend_dispersion")
            if mean in trend and trend[mean] != value:
                raise ValueError("saved_dispersion_trend_conflicts_at_equal_mean")
            trend[mean] = value
        plots.append(_request("bulk_dispersion", {"population_unit": "gene", "genes": genes,
            "trend": [{"mean": mean, "dispersion": value} for mean, value in trend.items()],
            "trend_method": calibration["trend_type"], "dispersion_unit": "dimensionless",
            "mean_unit": "normalized_count", "model_id": calibration["method"]}))
        limitations.append("Dispersion boundary flags compare the saved final alpha to saved calibration bounds; no dispersion was fitted.")
    else:
        limitations.append("Three-layer dispersion projection unavailable: saved gene-wise/trend/final estimates or calibration identity absent.")
    return _packet(plots, limitations, "Saved bulk QC and dispersion")


def _lineage(result):
    graph = result["lineage_graph"]
    if result["provenance"].get("algorithm") != "variable genes / standardization / PCA / kNN diffusion / rooted geodesic / cluster MST":
        raise ValueError("saved_lineage_algorithm_unsupported")
    if not isinstance(result.get("pseudotime"), dict) or not isinstance(result.get("branch_assignments"), dict):
        raise ValueError("saved_lineage_sample_membership_required")
    if set(result["pseudotime"]) != set(result["branch_assignments"]):
        raise ValueError("saved_lineage_sample_membership_mismatch")
    require_unique(list(result["pseudotime"]), "lineage_sample_ids")
    if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1
           for value in result["pseudotime"].values()):
        raise ValueError("saved_lineage_pseudotime_domain_invalid")
    if "selected_sample_count" in result["provenance"] and result["provenance"]["selected_sample_count"] != len(result["pseudotime"]):
        raise ValueError("saved_lineage_selected_sample_count_mismatch")
    nodes = [{"id": n["id"], "label": n.get("label", n["id"]),
              **{key: n[key] for key in ("sample_count", "mean_pseudotime") if key in n}} for n in graph["nodes"]]
    if graph.get("root") not in {n["id"] for n in nodes}:
        raise ValueError("saved_lineage_root_missing")
    membership = {"root_id": graph["root"], "samples": [{"id": identity, "pseudotime": value,
        "branch_id": result["branch_assignments"][identity]} for identity, value in result["pseudotime"].items()]}
    if result["provenance"].get("root_sample_id") is not None:
        membership["root_sample_id"] = result["provenance"]["root_sample_id"]
    if isinstance(result["provenance"].get("subsampled"), bool):
        membership["complete"] = not result["provenance"]["subsampled"]
    data = {"nodes": nodes, "edges": [{"id": "edge-" + str(i), "source": e["source"], "target": e["target"],
            "evidence": "inferred"} for i, e in enumerate(graph["edges"])], "method": result["provenance"]["algorithm"],
            "membership": membership}
    return _packet([_request("lineage_schematic", data)], result.get("limitations", []), "Saved inferred lineage")


def _enrichment(tool, result, parameters):
    provenance = result["provenance"]
    expected = "supplied curated TF-target overlap" if tool == "regulon_enrichment" else "supplied sequence motif-hit calls"
    if provenance.get("evidence_type") != expected or provenance.get("test") != "one-sided hypergeometric overrepresentation":
        raise ValueError("saved_enrichment_evidence_type_mismatch")
    if provenance.get("fdr_method") != "Benjamini-Hochberg":
        raise ValueError("saved_enrichment_probability_semantics_unsupported")
    if (provenance.get("fdr_scope") != "all supplied tested sets before output truncation"
            or provenance.get("returned_sets") != len(result["results"])
            or type(provenance.get("tested_sets")) is not int
            or provenance["tested_sets"] < len(result["results"])
            or provenance.get("truncated") is not (provenance["tested_sets"] > len(result["results"]))):
        raise ValueError("saved_enrichment_testing_family_or_truncation_mismatch")
    membership = saved_tool_membership(tool, result, parameters)
    universe = {"id": "measured-background-" + _digest(membership or {
        "background_size": provenance["background_size"], "reference_sha256": provenance["reference_sha256"]})[:24],
        "size": provenance["background_size"]}
    if membership and membership["background_ids"] is not None:
        universe.update(member_ids=membership["background_ids"], membership_complete=True,
                        membership_source_id=membership["source_id"], unit="gene")
    terms = []
    for item in result["results"]:
        if item["background_size"] != universe["size"] or item["selected_size"] != provenance["selected_size"]:
            raise ValueError("saved_enrichment_denominator_mismatch")
        identity = item["tf" if tool == "regulon_enrichment" else "motif"]
        if tool == "regulon_enrichment":
            term = {"id": identity, "label": identity, "overlap_count": item["overlap"], "query_size": item["selected_size"],
                    "set_size": item["reference_size"], "p": item["p_value"], "q": item["q_value"], "direction": "none"}
            if item["reference_size"] == 0:
                term.update(status="unavailable", reason="Saved reference has no measured background members.")
            member_fields = ("query_member_ids", "set_member_ids", "overlap_member_ids")
        else:
            term = {"id": identity, "label": identity, "foreground_hits": item["overlap"], "foreground_size": item["selected_size"],
                    "background_hits": item["reference_size"], "background_size": item["background_size"],
                    "fold_enrichment": item["fold_enrichment"], "p": item["p_value"], "q": item["q_value"]}
            if item["fold_enrichment"] is None:
                term.update(status="unavailable", reason="Saved fold enrichment is unavailable for an empty reference set.")
            member_fields = ("foreground_member_ids", "background_member_ids", "foreground_hit_ids", "background_hit_ids")
        for key in (*member_fields, "membership_complete", "membership_source_id", "member_unit"):
            if key in item:
                term[key] = deepcopy(item[key])
        if membership:
            query_field = "query_member_ids" if tool == "regulon_enrichment" else "foreground_member_ids"
            if membership["selected_ids"] is not None:
                if query_field in term and set(term[query_field]) != set(membership["selected_ids"]):
                    raise ValueError("saved_enrichment_query_membership_mismatch")
                term[query_field] = deepcopy(membership["selected_ids"])
            if tool == "motif_enrichment" and membership["background_ids"] is not None:
                if "background_member_ids" in term and set(term["background_member_ids"]) != set(membership["background_ids"]):
                    raise ValueError("saved_motif_background_membership_mismatch")
                term["background_member_ids"] = deepcopy(membership["background_ids"])
            members = (membership.get("term_member_ids") or {}).get(identity)
            if members is not None:
                field = "set_member_ids" if tool == "regulon_enrichment" else "background_hit_ids"
                if field in term and set(term[field]) != set(members):
                    raise ValueError("saved_enrichment_term_membership_mismatch")
                term[field] = deepcopy(members)
                if membership["selected_ids"] is not None and membership["term_membership_complete"][identity]:
                    overlap = [gene for gene in members if gene in set(membership["selected_ids"])]
                    field = "overlap_member_ids" if tool == "regulon_enrichment" else "foreground_hit_ids"
                    if field in term and set(term[field]) != set(overlap):
                        raise ValueError("saved_enrichment_overlap_membership_mismatch")
                    term[field] = overlap
            if any(key in term for key in member_fields):
                complete = membership["term_membership_complete"].get(identity, True)
                if complete is not None:
                    term.setdefault("membership_complete", complete)
                term.setdefault("membership_source_id", membership["source_id"])
                term.setdefault("member_unit", "gene")
        if any(key in term for key in member_fields):
            term.setdefault("member_unit", "gene")
        terms.append(term)
    limits = list(result.get("limitations", []))
    limits.append("Displayed rows are the saved returned sets; BH used all tested sets before any output truncation.")
    if not membership or membership["selected_ids"] is None or membership["background_ids"] is None:
        limits.append("Full selected/background membership was not retained in this output; denominators are saved cardinalities only.")
    if not membership or membership["term_member_ids"] is None:
        limits.append("Complete per-term member IDs were not retained; any supplied partial lists remain source data, and missing IDs remain unknown.")
    elif membership["term_membership_status"] != "complete_for_returned_terms":
        limits.append("Per-term membership is partial or its completeness is unassessed; missing original IDs remain unknown.")
    retained_provenance = {key: deepcopy(provenance.get(key)) for key in (
        "species", "database", "background_definition", "promoter_definition", "promoter_source_id", "reference_sha256",
        "reference_validation", "evidence_type", "test", "fdr_method", "fdr_scope", "excluded_reference_genes", "background_source")}
    coverage = {"tested_sets": provenance["tested_sets"], "retained_sets": provenance["returned_sets"],
                "omitted_sets": provenance["tested_sets"] - provenance["returned_sets"], "truncated": provenance["truncated"]}
    if tool == "regulon_enrichment":
        return _packet([_request("enrichment_dotplot", {"method": "regulon", "terms": terms, "universe": universe,
            "x_metric": "GeneRatio", "provenance": retained_provenance, "coverage": coverage})], limits)
    data = {"terms": terms, "universe": universe, "provenance": retained_provenance, "coverage": coverage}
    if "promoters" in result:
        data["promoters"] = deepcopy(result["promoters"])
    return _packet([_request("motif_enrichment", data)], limits)


def saved_tool_membership(tool, result, parameters):
    """Exact retained IDs; cardinality-only output never creates member IDs.

    An optional saved membership extension is closed and versioned. Complete
    member lists permit deterministic intersection checks, not a new test.
    """
    if tool not in {"regulon_enrichment", "motif_enrichment"}:
        return None
    key = "selected_genes" if tool == "regulon_enrichment" else "foreground_genes"
    selected, background, terms, complete_terms = parameters.get(key), parameters.get("background_genes"), {}, {}
    if parameters and (selected is None or background is None):
        raise ValueError("saved_enrichment_dispatch_membership_required")
    extension = result.get("membership")
    if extension is not None:
        require_fields(extension, ("format", "selected_ids", "background_ids", "source_id"), ("term_member_ids",))
        if extension["format"] != "devbench.saved-enrichment-membership/1":
            raise ValueError("saved_enrichment_membership_format_unsupported")
        if selected is not None and (selected != extension["selected_ids"] or background != extension["background_ids"]):
            raise ValueError("saved_enrichment_membership_differs_from_dispatch_parameters")
        selected, background = extension["selected_ids"], extension["background_ids"]
        terms = deepcopy(extension.get("term_member_ids", {}))
        if not isinstance(terms, dict):
            raise ValueError("saved_enrichment_term_membership_requires_named_sets")
        complete_terms = {identity: True for identity in terms}
    if selected is not None:
        require_unique(selected, "selected_gene_membership")
    if background is not None:
        require_unique(background, "background_gene_membership")
    if selected is not None and background is not None and not set(selected) < set(background):
        raise ValueError("saved_enrichment_membership_not_proper_subset")
    p = result["provenance"]
    if ((selected is not None and len(selected) != p["selected_size"])
            or (background is not None and len(background) != p["background_size"])):
        raise ValueError("saved_enrichment_membership_denominator_mismatch")
    retained_ids = set()
    for item in result["results"]:
        identity = item["tf" if tool == "regulon_enrichment" else "motif"]
        retained_ids.add(identity)
        field = "set_member_ids" if tool == "regulon_enrichment" else "background_hit_ids"
        if field in item:
            if "membership_complete" in item and type(item["membership_complete"]) is not bool:
                raise ValueError("saved_enrichment_member_completeness_must_be_boolean")
            if identity in terms and set(terms[identity]) != set(item[field]):
                raise ValueError("saved_enrichment_term_membership_conflicts")
            terms[identity] = deepcopy(item[field])
            if "membership_complete" in item or identity not in complete_terms:
                complete_terms[identity] = item.get("membership_complete")
        if identity in terms:
            require_unique(terms[identity], "term_member_ids")
            if (len(terms[identity]) > item["reference_size"]
                    or complete_terms[identity] and len(terms[identity]) != item["reference_size"]
                    or background is not None and set(terms[identity]) - set(background)):
                raise ValueError("saved_enrichment_term_membership_denominator_or_background_mismatch")
            if selected is not None:
                overlap = len(set(terms[identity]) & set(selected))
                if overlap > item["overlap"] or complete_terms[identity] and overlap != item["overlap"]:
                    raise ValueError("saved_enrichment_overlap_membership_count_mismatch")
    if set(terms) - retained_ids:
        raise ValueError("saved_enrichment_membership_contains_unreturned_terms")
    if selected is None and background is None and not terms:
        return None
    source_id = _text(extension["source_id"], "membership_source_id") if extension else "retained-enrichment-membership-" + _digest({
        "selected": selected, "background": background, "terms": terms})[:24]
    return {"selected_ids": deepcopy(selected), "background_ids": deepcopy(background),
            "selected_ids_sha256": _digest(selected) if selected is not None else None,
            "background_ids_sha256": _digest(background) if background is not None else None,
            "term_member_ids": terms or None, "term_membership_complete": complete_terms, "source_id": source_id,
            "term_membership_status": "not_retained_by_registered_tool" if not terms else
                "completeness_unassessed" if any(value is None for value in complete_terms.values()) else
                "complete_for_returned_terms" if len(terms) == len(result["results"]) and all(complete_terms.values()) else
                "partial_returned_term_membership"}


def _temporal(result, parameters, context):
    require_fields(context, (), ("time_unit", "expression_unit"))
    for name, value in context.items():
        _text(value, name)
    family = result["family"]
    if family not in {"gaussian", "negative_binomial"} or not isinstance(result["genes"], dict):
        raise ValueError("saved_temporal_family_invalid")
    saved_parameters = result["provenance"]["parameters"]
    if not str(saved_parameters.get("statistical_method_version", "")).startswith("temporal_glm/"):
        raise ValueError("saved_temporal_producer_identity_missing")
    requested = parameters.get("gene_list", saved_parameters.get("gene_list"))
    require_unique(requested, "requested_temporal_genes")
    if set(result["genes"]) - set(requested):
        raise ValueError("saved_temporal_gene_family_mismatch")
    plots, limits, source_records = [], list(result.get("warnings", [])), []
    for i, gene in enumerate(requested):
        saved = result["genes"].get(gene)
        if saved is None:
            source_records.append({"id": gene, "status": "unavailable", "reason": "Requested gene absent from saved tool output."})
            continue
        if not isinstance(saved, dict):
            raise ValueError("saved_temporal_gene_record_invalid")
        curve_added = False
        candidates = saved.get("candidate_scores", [])
        if candidates:
            comparison_id = "saved-candidates-" + _digest({"gene": gene, "parameters": saved_parameters})[:24]
            criteria = [key.upper() for key in ("aic", "bic", "gcv")
                        if any(candidate.get(key) is not None for candidate in candidates)]
            rows = []
            for candidate in candidates:
                record = {"id": "df-" + str(candidate["df"]), "df": candidate["df"], "comparison_set_id": comparison_id,
                          **{key: candidate.get(key) for key in ("aic", "bic", "gcv")}}
                if candidate.get("status") != "ok":
                    record.update(status="failed", reason=candidate.get("reason", "Saved candidate unavailable: " + str(candidate.get("status"))))
                rows.append(record)
            if criteria:
                comparison = {"candidates": rows, "criteria": criteria,
                    "comparison_set_id": comparison_id, "comparison_basis": {"data_id": "saved-tool-output-" + _digest(result)[:24],
                        "response": gene, "likelihood": family + "; same retained response/design candidate family"}}
                chosen = [row["id"] for row in rows if row["df"] == saved.get("selected_df")]
                if len(chosen) == 1:
                    comparison["selected_candidate_id"] = chosen[0]
                if saved.get("selected_df") is not None:
                    comparison["selected_df"] = saved["selected_df"]
                if result.get("selection_criterion") is not None:
                    comparison["selection_criterion"] = result["selection_criterion"]
                comparison["selection_provenance"] = {"version": saved_parameters["statistical_method_version"]}
                plots.append(_request("temporal_model_comparison", comparison, "temporal-model-" + str(i)))
        # The optional narrow curve builder accepts saved predictions without
        # inventing the missing observations, knot locations or confidence band.
        if saved.get("time_grid") is not None or saved.get("predicted_curve") is not None:
            times, values = saved.get("time_grid"), saved.get("predicted_curve")
            if not isinstance(times, list) or not isinstance(values, list) or len(times) != len(values):
                raise ValueError("saved_temporal_curve_alignment_mismatch")
            points = [{"id": "point-" + str(j), "time": t, "value": v} for j, (t, v) in enumerate(zip(times, values))]
            if saved.get("status") != "ok":
                for point in points:
                    point.update(status="unavailable", reason="Saved gene status: " + str(saved.get("status")))
            if points:
                curve_added = True
                plots.append(_request("saved_temporal_curve", {"gene_id": gene,
                    "time_unit": context.get("time_unit", "numeric_time:" + str(saved_parameters.get("time_col")) + " (unit not retained)"),
                    "value_unit": context.get("expression_unit", "normalized_count" if family == "negative_binomial" else "unchanged input expression units"),
                    "points": points,
                    "prediction_definition": saved.get("contrast_note", "Saved fitted predictions; nuisance reference not retained"),
                    "model": {key: value for key, value in {"family": family, "selected_df": saved.get("selected_df"),
                        "formula": saved.get("formula", result.get("formula")), "effect_scale": saved.get("effect_scale"),
                        "statistical_method_version": saved_parameters["statistical_method_version"]}.items() if value is not None}}, "temporal-curve-" + str(i)))
        if not curve_added:
            reasons = [str(saved[key]) for key in ("failure_code", "reason") if saved.get(key)]
            reasons.extend(str(item["code"]) for item in saved.get("diagnostics", [])
                           if isinstance(item, dict) and item.get("code"))
            reasons.extend(str(item["reason"]) for item in candidates if item.get("reason"))
            source_records.append({"id": gene, "status": "failed" if saved.get("status") == "failed" else "unavailable",
                "reason": "; ".join(dict.fromkeys(reasons)) or "Saved gene status " + str(saved.get("status")) + ": prediction coordinates unavailable."})
    if source_records:
        curve = next((plot for plot in plots if plot["capability"] == "saved_temporal_curve"), None)
        if curve is None:
            curve = _request("saved_temporal_curve", {"gene_id": source_records[0]["id"], "points": [],
                "time_unit": context.get("time_unit", "time units not retained"),
                "value_unit": context.get("expression_unit", "expression units not retained"),
                "prediction_definition": "Requested temporal source records; no curve coordinates retained."}, "temporal-source-records")
            plots.append(curve)
        curve["data"]["source_records"] = source_records
        limits.append("Requested genes without prediction coordinates are retained as source records with no numeric glyphs; any saved candidate criteria are displayed separately.")
    limits.append("Observations, spline knot locations and curve uncertainty were not retained by this producer; no new fit or interval was computed.")
    return _packet(plots, limits, "Saved temporal model outputs")


def _regulatory(result):
    provenance = result["provenance"]
    aggregation = SAVED_NETWORK_ALGORITHMS.get(provenance.get("algorithm"))
    if aggregation is None:
        raise ValueError("saved_network_algorithm_unsupported")
    retained_aggregation = provenance.get("aggregation", "time_mean")
    if retained_aggregation != aggregation:
        raise ValueError("saved_network_algorithm_aggregation_mismatch")
    universe = result["candidate_universe"]
    pairs = [(item["tf"], item["target"]) for item in universe]
    if len(set(pairs)) != len(pairs) or any(tf == target for tf, target in pairs):
        raise ValueError("saved_network_candidate_universe_invalid")
    if provenance.get("candidate_edge_count") != len(pairs) or provenance.get("returned_edge_count") != len(result["edges"]):
        raise ValueError("saved_network_candidate_denominator_mismatch")
    edges = []
    for index, item in enumerate(result["edges"]):
        if (item["tf"], item["target"]) not in set(pairs):
            raise ValueError("saved_network_edge_outside_candidate_universe")
        edges.append({"id": "edge-" + str(index), "tf": item["tf"], "target": item["target"],
            "association_type": "ridge_association", "coefficient": item["coefficient"],
            "score": item["score"], "sign": item["sign"], "coefficient_definition": "standardized_ridge_coefficient",
            "association_method": provenance["algorithm"], "ridge_parameters": {key: provenance[key]
                for key in ("alpha", "lag_observed_intervals", "transform")}})
    return _packet([_request("regulatory_network", {"tf_ids": list(dict.fromkeys(tf for tf, _ in pairs)),
        "target_ids": list(dict.fromkeys(target for _, target in pairs)), "edges": edges})],
        [*result.get("limitations", []), "Curated membership and motif-hit evidence are unassessed by this ridge-association producer."],
        "Saved lagged ridge associations")


def _bound_table(value):
    require_fields(value["source"], ("artifact_id", "sha256", "run_id", "rows_sha256"))
    import re
    for key in ("sha256", "rows_sha256"):
        if not isinstance(value["source"][key], str) or not re.fullmatch(r"[a-f0-9]{64}", value["source"][key]):
            raise ValueError("saved_table_source_hash_invalid")
    for key in ("artifact_id", "run_id"):
        _text(value["source"][key], key)
    rows = value["rows"]
    if not isinstance(rows, list) or not rows or len(rows) > 100000:
        raise ValueError("saved_table_row_budget_invalid")
    if _digest(rows) != value["source"]["rows_sha256"]:
        raise ValueError("saved_table_rows_hash_mismatch")
    return rows


def _scrna(document):
    """Consume bounded tables already read by a trusted artifact reader.

    rows_sha256 binds canonical parsed records; sha256 binds original artifact
    bytes at the caller. Their distinction permits saved CSV tables without
    path traversal or loading a whole expression matrix in this pure adapter.
    """
    require_fields(document, ("format", "cohort"), ("qc_metrics", "group_gene_aggregates"))
    require_fields(document["cohort"], ("ids", "sha256"))
    ids = require_unique(document["cohort"]["ids"], "scrna_cohort_ids")
    if _digest(ids) != document["cohort"]["sha256"]:
        raise ValueError("saved_scrna_cohort_hash_mismatch")
    plots, limits = [], ["Table source identities are caller-verified artifact bindings, not authenticated biological identities."]
    if "qc_metrics" in document:
        value = document["qc_metrics"]
        require_fields(value, ("source", "rows", "sample_column", "batch_column", "group_by", "report"))
        rows = _bound_table(value)
        if len({r["cell_id"] for r in rows}) != len(rows) or {r["cell_id"] for r in rows} != set(ids):
            raise ValueError("saved_QC_cohort_membership_mismatch")
        report = value["report"]
        if report.get("method") != "scanpy_QC_log1p_MAD/1":
            raise ValueError("saved_QC_report_method_unsupported")
        excluded = [r["cell_id"] for r in rows if r.get("qc_pass") is False]
        if (report.get("cells_input") != len(rows) or report.get("cells_excluded") != len(excluded)
                or report.get("cells_retained") != len(rows) - len(excluded)
                or sorted(report.get("excluded_cell_ids", [])) != sorted(excluded)):
            raise ValueError("saved_QC_report_membership_mismatch")
        cells = []
        for r in rows:
            cells.append({"id": r["cell_id"], "sample": r[value["sample_column"]], "batch": r[value["batch_column"]],
                **{key: r[key] for key in ("total_counts", "n_genes_by_counts", "pct_counts_mt", "qc_pass")}})
        if value["group_by"] not in {"batch", "sample"}:
            raise ValueError("saved_QC_group_by_invalid")
        for index, batch in enumerate(dict.fromkeys(c["batch"] for c in cells)):
            thresholds = {"min_genes": report["min_genes"]}
            if report.get("max_mito_percent") is not None:
                thresholds["max_pct_mt"] = report["max_mito_percent"]
            for cutoff in report.get("thresholds", []):
                if cutoff["batch"] != batch:
                    continue
                metric = cutoff["metric"]
                names = {"log1p_total_counts": ("min_counts", "max_counts"),
                         "log1p_n_genes_by_counts": ("min_genes", "max_genes"),
                         "pct_counts_mt": (None, "max_pct_mt")}
                if metric not in names:
                    raise ValueError("saved_QC_threshold_metric_unsupported")
                for field, key in zip(("lower", "upper"), names[metric]):
                    if key is None or cutoff[field] is None:
                        continue
                    number = math.expm1(cutoff[field]) if metric.startswith("log1p_") else cutoff[field]
                    number = max(0., number)
                    if key in thresholds:
                        number = max(number, thresholds[key]) if field == "lower" else min(number, thresholds[key])
                    thresholds[key] = number
            data = {"cells": [c for c in cells if c["batch"] == batch], "group_by": value["group_by"],
                "units": {"total_counts": "count", "n_genes_by_counts": "gene", "pct_counts_mt": "percent"},
                "thresholds": thresholds, "threshold_source": "Saved scanpy_QC_log1p_MAD/1 report; batch " + str(batch)
                    + "; log1p cutoffs displayed with expm1; source " + value["source"]["sha256"]}
            for capability in ("qc_joint_scatter", "qc_violins", "qc_ridges"):
                plots.append(_request(capability, data, capability + "-" + str(index)))
        limits.append("Saved qc_pass membership is copied unchanged. Displayed nonnegative cutoffs do not re-run filtering or establish independent replication.")
        metrics = [("total_counts", "Total counts", "count"), ("n_genes_by_counts", "Detected genes", "gene"),
                   ("pct_counts_mt", "Mitochondrial counts", "percent")]
        plots.append(_request("measurement_correlation", {"mode": "observations", "observation_kind": "cell",
            "measurements": [{"id": name, "name": label, "unit": unit, "declared_transform": "identity; saved " + name}
                             for name, label, unit in metrics], "missingness_policy": "pairwise_complete",
            "observations": [{"id": cell["id"], "key": "cell:" + cell["id"],
                "values": {name: cell[name] for name, _, _ in metrics}, "group": cell[value["group_by"]], "status": "recorded",
                "details": {"sample_id": cell["sample"], "batch": cell["batch"], "qc_pass": cell["qc_pass"],
                            "source_ref": deepcopy(value["source"]), "population": "all supplied saved QC cells"}}
                for cell in cells]}, "saved-qc-measurement-correlation"))
        limits.append("QC correlations describe the three actual saved raw QC measurements across all supplied cells, including saved qc_pass=false cells; pairwise missingness is explicit and no fixed module values are substituted.")
    if "group_gene_aggregates" in document:
        value = document["group_gene_aggregates"]
        required = ("source", "rows", "unit", "source_scale", "layer", "expression_threshold", "denominator_kind")
        require_fields(value, required)
        rows = _bound_table(value)
        for row in rows:
            if not set(row["member_ids"]) <= set(ids):
                raise ValueError("saved_aggregate_membership_outside_cohort")
        plots.append(_request("marker_dotplot", {"group_gene_aggregates": rows,
            **{key: value[key] for key in required if key not in {"source", "rows"}}}))
    return _packet(plots, limits, "Saved single-cell QC and aggregates")


def prepare_report_plot_input(metadata, *, audit=None):
    """Engine report arrays with explicit names; never reads report paths."""
    _bounded(metadata)
    plots, limits = [], []
    artifacts = metadata.get("report_artifacts", {})
    if isinstance(artifacts, dict):
        if artifacts.get("format") == INPUT_FORMAT:
            plots.extend(_validate_packet(artifacts)["plots"])
        for index, curve in enumerate(artifacts.get("expression_curves", [])):
            # Legacy labels are retained as labels; they are not promoted to
            # experimentally known time/expression units.
            points = [{"id": "point-" + str(j), "time": point["x"], "value": point["prediction"]}
                      for j, point in enumerate(curve["points"])]
            plots.append(_request("saved_temporal_curve", {"gene_id": curve["gene"], "points": points,
                "time_unit": curve.get("time_unit", "unit not retained; saved label: " + str(curve.get("x_label", "unavailable"))),
                "value_unit": curve.get("expression_unit", "unit not retained; saved label: " + str(curve.get("y_label", "unavailable"))),
                "prediction_definition": "Predictions supplied in engine report_artifacts; model/reference design not retained."},
                "report-curve-" + str(index)))
            if any("ci_lower" in point or "ci_upper" in point for point in curve["points"]):
                limits.append("Legacy supplied interval values remain in the source and legacy SVG. They are not assigned a missing interval kind, coverage, or model by the typed curve projection.")
        lineage = artifacts.get("lineage")
        if isinstance(lineage, dict) and lineage.get("method") and all(e.get("evidence") for e in lineage.get("edges", [])):
            plots.append(_request("lineage_schematic", {"nodes": [{"id": n["id"], "label": n.get("label", n["id"])} for n in lineage["nodes"]],
                "edges": [{"id": "edge-" + str(i), **e} for i, e in enumerate(lineage["edges"])], "method": lineage["method"]}, "report-lineage"))
        elif lineage:
            limits.append("Legacy lineage lacks explicit edge evidence or method; retained only as a supplied schematic, without typed scientific authority.")
    samples = metadata.get("memory_samples", [])
    for key, unit in (("rss_bytes", "bytes"), ("rss_mb", metadata.get("memory_unit", "MB"))):
        points = [{"id": "memory-" + str(i), "time": sample["elapsed_sec"], "value": sample[key], "series": "Recorded RSS"}
                  for i, sample in enumerate(samples) if key in sample and (key == "rss_bytes" or "rss_bytes" not in sample)]
        if points:
            plots.append(_request("resource_curve", {"measurements": points, "metric": "Resident set size", "time_unit": "seconds",
                                                      "value_unit": unit}, "report-memory-" + key))
    if audit and audit.get("status") == "checked" and audit.get("nodes"):
        nodes = [{"id": n["id"], "label": str(n.get("tool_name") or n.get("kind") or n["id"])} for n in audit["nodes"]]
        edges = [{"id": "dependency-" + str(i) + "-" + str(j), "source": parent, "target": n["id"]}
                 for i, n in enumerate(audit["nodes"]) for j, parent in enumerate(n["parents"])]
        plots.append(_request("execution_dag", {"nodes": nodes, "edges": edges,
            "method": "Saved manifest/node dependency DAG; hashes checked against supplied receipt; no artifact replay"}, "report-execution-dag"))
    return _validate_packet(_packet(plots, limits, "Saved execution and scientific evidence"))


def prepare_plot_input_from_saved(document):
    """Return a validated input packet from explicit saved scientific schemas.

    No paths are followed. A caller must independently associate ``document``
    with the exact source artifact/receipt before attaching workspace authority.
    """
    _bounded(document)
    if not isinstance(document, dict):
        raise UnsupportedSavedPlotSource("saved_plot_requires_explicit_result_object")
    try:
        if document.get("format") == INPUT_FORMAT:
            packet = document
        elif document.get("format") == TOOL_FORMAT:
            require_fields(document, ("format", "tool_name", "result", "parameters"), ("context",))
            tool, result, parameters = document["tool_name"], document["result"], document["parameters"]
            if not isinstance(result, dict) or not isinstance(parameters, dict):
                raise ValueError("saved_tool_output_requires_result_and_parameters_objects")
            if tool == "fit_temporal_trajectory":
                packet = _temporal(result, parameters, document.get("context", {}))
            elif tool == "infer_lineage":
                packet = _lineage(result)
            elif tool in {"regulon_enrichment", "motif_enrichment"}:
                packet = _enrichment(tool, result, parameters)
            elif tool == "infer_regulatory_network":
                packet = _regulatory(result)
            else:
                raise UnsupportedSavedPlotSource("saved_tool_plot_adapter_unsupported:" + str(tool))
        elif document.get("format") == SCRNA_FORMAT:
            packet = _scrna(document)
        elif document.get("format") in {
                "devbench.scrna-population-audit/1", "devbench.scrna-population-design/1",
                "devbench.scrna-population-pseudobulk/1", "devbench.scrna-population-contrast/1",
                "devbench.scrna-population-summary/1"}:
            from .plot_adapters_scrna_population import saved_population_plot_input
            packet = saved_population_plot_input(document)
        elif isinstance(document.get("diagnostics"), dict) and document["diagnostics"].get("plot_evidence", {}).get("format") == "devbench.bulk-plot-evidence/1":
            packet = _bulk(document)
        elif "lineage_graph" in document and document.get("provenance", {}).get("sampling_time_used") is False:
            packet = _lineage(document)
        elif str(document.get("provenance", {}).get("parameters", {}).get("statistical_method_version", "")).startswith("temporal_glm/"):
            packet = _temporal(document, {}, {})
        elif document.get("provenance", {}).get("evidence_type") in {"supplied curated TF-target overlap", "supplied sequence motif-hit calls"}:
            tool = "regulon_enrichment" if document["provenance"]["evidence_type"] == "supplied curated TF-target overlap" else "motif_enrichment"
            packet = _enrichment(tool, document, {})
        elif document.get("provenance", {}).get("algorithm") in SAVED_NETWORK_ALGORITHMS:
            packet = _regulatory(document)
        else:
            raise UnsupportedSavedPlotSource("artifact_has_no_supported_saved_scientific_output_schema")
        _bounded(packet)
        return _validate_packet(packet)
    except (KeyError, TypeError, AttributeError, IndexError) as error:
        raise ValueError("saved_scientific_output_schema_incomplete_or_invalid") from error


def saved_plot_capabilities(document):
    """Actual renderable saved schemas, after the caller verifies artifact custody.

    A filename, MIME type or producer assertion never grants availability. This
    pure helper does not read paths or authenticate the supplied JSON. Known
    malformed evidence raises; unsupported or missing saved inputs return [].
    """
    if isinstance(document, dict) and document.get("format") == "devbench.scientific-plot-workspace/1":
        from .scientific_plot_contract import validate_workspace
        panels = validate_workspace(document)["panels"]
    else:
        try:
            packet = prepare_plot_input_from_saved(document)
        except (MissingSavedPlotInputs, UnsupportedSavedPlotSource):
            return []
        from .scientific_plot_suite import _registry
        registered = _registry()
        panels = [panel for request in packet["plots"]
                  for panel in registered[request["capability"]][0].build_panels(request["capability"], request["data"])]
    return list(dict.fromkeys(panel["capability"] for panel in panels
                             if panel["availability"]["status"] == "available" and panel["marks"]))
