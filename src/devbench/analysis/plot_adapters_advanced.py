"""Source-preserving adapters for saved downstream and specialized RNA results.

No fitting, normalization, causal discovery, registration, or biological validation
is performed here. A missing saved quantity is not replaced by a plausible curve.
The synthetic packets at the end exercise geometry, not empirical validity.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import math
import re
from typing import Any

from .scientific_plot_contract import (
    finite_number, line_mark, panel, require_fields, require_unique, row,
)


def _spec(required: str, description: str, optional: str = "") -> dict:
    return {"required": required.split(), "optional": [*optional.split(), "source_label"],
            "description": description}


CAPABILITIES = {
    "gsea_running": _spec("pathway_id running_scores hits ranked_count universe ranking_metric",
        "Saved GSEA running scores and rank-aligned hit barcode with tested background/reference context.", "nes q provenance"),
    "enrichment_dotplot": _spec("method terms universe x_metric",
        "ORA, GSEA or regulon enrichment with exact available memberships/provenance and upstream coverage; dot area is -log10(q).", "provenance coverage"),
    "temporal_spline": _spec("gene_id family input_scale expression_unit time_unit observations fitted model",
        "Saved NB/Gaussian fitted spline and biological-unit observations; independently eligible saved interval ribbons.", "interval"),
    "temporal_model_comparison": _spec("candidates criteria comparison_set_id comparison_basis",
        "Actual saved AIC/BIC/GCV candidates; failed fits and saved selection remain visible.",
        "selected_candidate_id selected_df selection_criterion selection_provenance"),
    "saved_temporal_curve": _spec("gene_id points time_unit value_unit prediction_definition",
        "Saved time-grid predictions with absent replicate observations, knots and uncertainty explicitly disclosed.", "model source_records"),
    "pseudotime_monotonicity": _spec("observations age_unit pseudotime_unit ordering_method",
        "Collection-age reference versus saved inferred pseudotime, with biological-unit identity.", "statistics"),
    "lineage_schematic": _spec("nodes edges method",
        "Explicit lineage topology with declared schematic layout, node statistics and retained sample membership.", "membership"),
    "regulatory_network": _spec("tf_ids target_ids edges",
        "Bipartite TF/target graph distinguishes correlation, standardized ridge associations, curated membership and motif-hit evidence."),
    "motif_enrichment": _spec("terms universe",
        "Saved motif enrichment with hit denominators, exact available promoter/membership evidence and upstream coverage.", "provenance coverage promoters"),
    "resource_curve": _spec("measurements metric time_unit value_unit",
        "Saved resource or memory measurements against elapsed time; execution inventory only."),
    "execution_dag": _spec("nodes edges method",
        "Saved execution dependencies and statuses in a declared topological schematic."),
    "repertoire_abundance": _spec("clones sample_totals biological_units clone_definition metric",
        "Actual clone counts or sample-denominated fractions; biological units remain explicit."),
    "repertoire_overlap": _spec("clones samples biological_units clone_definition metric",
        "Descriptive Jaccard overlap from observed positive-count clone sets, with intersection/union membership."),
    "repertoire_diversity": _spec("metrics metric_name unit",
        "Saved diversity estimates with estimator and biological-unit identity; no fabricated intervals."),
    "multimodal_features": _spec("pairs x_assay y_assay paired_by",
        "Explicit same-cell paired features with separate assay units and saved transforms."),
    "multimodal_heatmap": _spec("assays paired_ids paired_by",
        "Paired-cell assay blocks with independent assay color scales and units."),
    "rna_velocity": _spec("cells embedding_unit vector_unit time_unit time_step projection_method count_unit",
        "Arrows use saved projected velocity vectors and actual spliced/unspliced evidence."),
    "spatial_expression": _spec("spots feature_id coordinate_unit expression_unit registration",
        "Saved spatial expression at physical coordinates with explicit registration metadata."),
    "splice_tracks": _spec("chromosome genome_build coordinate_system coverage_unit coverage junctions exons",
        "Canonical G20 coverage, junction/sashimi and strand-aware isoform tracks with declared sample/replicate metadata.", "track_metadata"),
    "perturbation_control": _spec("observations conditions expression_unit feature_id design",
        "Canonical G22 perturbation/control measurements with declared biological pairing."),
    "orthology_comparison": _spec("mappings values source_species target_species effect_unit mapping_policy",
        "Canonical G22 explicit versioned/namespaced orthology mapping; one-to-many pairs are retained without averaging.", "mapping_provenance"),
    "genomic_effect": _spec("genes genome_build coordinate_system effect_unit",
        "Saved genomic position and fold-change/effect views, faceted by chromosome."),
    "sva_association": _spec("associations association_metric unit",
        "Saved surrogate-variable associations with covariates; natural correlation domains are fixed.", "color_domain"),
}


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise ValueError(f"{label} must be a nonempty bounded string")
    return value


def _num(value: Any, label: str, low: float | None = None, high: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    value = finite_number(value)
    if (low is not None and value < low) or (high is not None and value > high):
        raise ValueError(f"{label} outside its scientific domain")
    return float(value)


def _int(value: Any, label: str, minimum: int = 0) -> int:
    number = _num(value, label, minimum)
    if not number.is_integer():
        raise ValueError(f"{label} must be an integer")
    return int(number)


def _records(value: Any, label: str, required: str, optional: str = "", *, empty: bool = False) -> list[dict]:
    if not isinstance(value, list) or (not value and not empty) or len(value) > 20000:
        raise ValueError(f"{label} requires a bounded list of source records")
    for record in value:
        require_fields(record, required.split(), [*optional.split(), "status", "reason", "warnings"])
        _text(record["id"], f"{label} id")
        if "status" in record and record["status"] not in {
            "recorded", "complete", "ok", "success", "failed", "excluded", "unavailable",
            "pending", "queued", "running", "cancelled", "skipped",
        }:
            raise ValueError("unknown saved source status; do not reinterpret nonconvergence as success")
    require_unique([r["id"] for r in value], f"{label} ids must be unique")
    return value


def _strings(value: Any, label: str, *, empty: bool = False, limit: int = 20000) -> list[str]:
    if not isinstance(value, list) or (not value and not empty) or len(value) > limit:
        raise ValueError(f"{label} must be a bounded list")
    for v in value:
        _text(v, label)
    require_unique(value, f"{label} must be unique")
    return value


def _unique_values(values: list, label: str) -> None:
    """Numeric coordinates and compound membership differ from contract ID lists."""
    if len(set(values)) != len(values):
        raise ValueError(f"{label}: duplicate values")


def _universe(data: dict) -> tuple[str, int]:
    require_fields(data, ["id", "size"], ["unit", "member_ids", "membership_complete", "membership_source_id"])
    identity, size = _text(data["id"], "universe id"), _int(data["size"], "universe size", 1)
    for field in ("unit", "membership_source_id"):
        if data.get(field) is not None:
            _text(data[field], f"universe {field}")
    complete = data.get("membership_complete")
    if complete is not None and not isinstance(complete, bool):
        raise ValueError("universe membership_complete must be boolean or null")
    members = data.get("member_ids")
    if members is not None:
        _strings(members, "universe member ids", empty=True, limit=200000)
        if len(members) > size or (complete and len(members) != size):
            raise ValueError("universe membership count disagrees with tested size")
    elif complete:
        raise ValueError("complete universe membership requires saved member ids")
    return identity, size


_PROVENANCE_FIELDS = (
    "species", "database", "background_definition", "promoter_definition", "promoter_source_id",
    "reference_sha256", "reference_validation", "evidence_type", "test", "fdr_method", "fdr_scope",
    "excluded_reference_genes", "background_source",
)


def _enrichment_context(data: dict, retained: int) -> tuple[dict, dict, dict, dict, dict, list[str], list[str]]:
    """Retain large tested-universe membership once, with inspectable row references."""
    identity, size = _universe(data["universe"])
    universe = {"id": identity, "size": size, "unit": None, "member_ids": None,
                "membership_complete": None, "membership_source_id": None, **deepcopy(data["universe"])}
    supplied = {} if data.get("provenance") is None else data["provenance"]
    require_fields(supplied, [], _PROVENANCE_FIELDS)
    provenance = {field: None for field in _PROVENANCE_FIELDS}
    provenance.update(deepcopy(supplied))
    database = {} if provenance["database"] is None else provenance["database"]
    require_fields(database, [], ["name", "version"])
    provenance["database"] = {"name": None, "version": None, **database}
    for field in ("name", "version"):
        if provenance["database"][field] is not None:
            _text(provenance["database"][field], f"database {field}")
    for field in set(_PROVENANCE_FIELDS) - {"database", "reference_validation", "excluded_reference_genes"}:
        if provenance[field] is not None:
            _text(provenance[field], f"provenance {field}")
    if provenance["reference_sha256"] is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", provenance["reference_sha256"]):
        raise ValueError("saved reference_sha256 must be a SHA-256 hex digest")
    excluded = provenance["excluded_reference_genes"]
    if isinstance(excluded, list):
        _strings(excluded, "excluded reference genes", empty=True, limit=200000)
    elif excluded is not None:
        _int(excluded, "excluded reference gene count")
    validation = provenance["reference_validation"]
    if validation is not None and not isinstance(validation, (str, dict)):
        raise ValueError("saved reference validation must be text or a structured record")
    coverage = {"tested_sets": None, "retained_sets": retained, "omitted_sets": None, "truncated": None}
    if data.get("coverage") is not None:
        supplied_coverage = data["coverage"]
        require_fields(supplied_coverage, ["tested_sets", "retained_sets", "omitted_sets"], ["truncated"])
        if _int(supplied_coverage["retained_sets"], "retained set count") != retained:
            raise ValueError("source retained set count must equal actual retained term rows")
        tested, omitted = supplied_coverage["tested_sets"], supplied_coverage["omitted_sets"]
        if (tested is None) != (omitted is None):
            raise ValueError("unknown tested/omitted set counts must remain jointly unknown")
        if tested is not None:
            tested, omitted = _int(tested, "tested sets"), _int(omitted, "omitted sets")
            if tested != retained + omitted:
                raise ValueError("tested sets must equal retained plus omitted sets")
        truncated = supplied_coverage.get("truncated")
        if truncated is not None and not isinstance(truncated, bool):
            raise ValueError("source truncation status must be boolean or null")
        if tested is not None and truncated is not None and truncated != (omitted > 0):
            raise ValueError("source truncation flag contradicts tested/retained/omitted counts")
        coverage = {"tested_sets": tested, "retained_sets": retained, "omitted_sets": omitted,
                    "truncated": (omitted > 0) if tested is not None else truncated}
    context = _r(universe, None, None, kind="universe", prefix="tested_universe",
                 reason="saved tested-universe context; no enrichment-dot coordinate",
                 details={**universe, "provenance": provenance, "source_coverage": coverage})
    summary = {k: v for k, v in universe.items() if k != "member_ids"}
    summary.update(member_ids_source_key=context["key"], member_ids_retained=len(universe["member_ids"]) if universe["member_ids"] is not None else None)
    membership = "unassessed" if universe["member_ids"] is None else ("complete" if universe["membership_complete"] else ("partial" if universe["membership_complete"] is False else "completeness unassessed"))
    db = provenance["database"]
    annotations = [f"Tested universe {identity}: {size} {universe['unit'] or '(unit unassessed)'}; membership {membership}; source {universe['membership_source_id'] or 'unassessed'}.",
                   f"Species: {provenance['species'] or 'unassessed'}; database: {db['name'] or 'unassessed'}; version: {db['version'] or 'unassessed'}.",
                   f"Background definition: {provenance['background_definition'] or 'unassessed'}; evidence scope: {provenance['evidence_type'] or 'unassessed'}.",
                   f"Source set coverage: tested={coverage['tested_sets'] if coverage['tested_sets'] is not None else 'unassessed'}; retained={retained}; omitted={coverage['omitted_sets'] if coverage['omitted_sets'] is not None else 'unassessed'}."]
    if provenance["fdr_scope"]:
        annotations.append(f"Saved FDR scope: {provenance['fdr_scope']}; method: {provenance['fdr_method'] or 'unassessed'}.")
    limitations = ["Species, database version, tested background and membership are supplied saved evidence; they are not independently validated by this renderer."]
    if coverage["omitted_sets"] != 0:
        limitations.append("Complete export contains every retained source row; upstream omitted sets cannot be shown. Untested or unretained sets are not invented.")
    return universe, summary, provenance, coverage, context, annotations, limitations


def _membership(record: dict, counts: dict[str, int], universe: dict) -> dict:
    """Validate optional full/partial identifiers without inferring absent members."""
    complete = record.get("membership_complete")
    if complete is not None and not isinstance(complete, bool):
        raise ValueError("term membership_complete must be boolean or null")
    for field in ("membership_source_id", "member_unit"):
        if record.get(field) is not None:
            _text(record[field], field)
    if record.get("member_unit") is not None and universe["unit"] is not None and record["member_unit"] != universe["unit"]:
        raise ValueError("term and tested-universe membership units disagree")
    known = (set(universe["member_ids"]) if universe["member_ids"] is not None
             and (universe["membership_complete"] or len(universe["member_ids"]) == universe["size"]) else None)
    evidence = {}
    for field, count in counts.items():
        members = record.get(field)
        if members is None:
            evidence[field] = "unavailable"
            continue
        _strings(members, field, empty=True, limit=200000)
        if len(members) > count or complete and len(members) != count:
            raise ValueError(f"{field} membership count disagrees with its denominator/count")
        if known is not None and not set(members) <= known:
            raise ValueError("term membership contains IDs outside the complete tested universe")
        evidence[field] = "complete" if complete else ("partial" if complete is False else "unassessed")
    if complete and all(value == "unavailable" for value in evidence.values()):
        raise ValueError("complete term membership requires at least one retained member list")
    return evidence


def _set_membership(record: dict, universe: dict) -> dict:
    evidence = _membership(record, {"query_member_ids": record["query_size"], "set_member_ids": record["set_size"],
                                    "overlap_member_ids": record["overlap_count"]}, universe)
    query, reference, overlap = (set(record[k]) if record.get(k) is not None else set()
                                  for k in ("query_member_ids", "set_member_ids", "overlap_member_ids"))
    # Known overlap IDs necessarily occupy both sets, even when completeness is
    # unassessed. Check feasible cardinalities without inventing the missing IDs.
    query_known, reference_known = query | overlap, reference | overlap
    query_remaining = record["query_size"] - len(query_known)
    reference_remaining = record["set_size"] - len(reference_known)
    forced_overlap = len(query_known & reference_known)
    possible_overlap = min(record["query_size"], record["set_size"], forced_overlap + query_remaining + reference_remaining)
    if min(query_remaining, reference_remaining) < 0 or not forced_overlap <= record["overlap_count"] <= possible_overlap:
        raise ValueError("saved query/reference/overlap memberships cannot satisfy declared cardinalities")
    return evidence


def _motif_membership(record: dict, universe: dict) -> dict:
    evidence = _membership(record, {"foreground_member_ids": record["foreground_size"], "background_member_ids": record["background_size"],
                                    "foreground_hit_ids": record["foreground_hits"], "background_hit_ids": record["background_hits"]}, universe)
    fg, bg, fh, bh = (set(record[k]) if record.get(k) is not None else None for k in
                      ("foreground_member_ids", "background_member_ids", "foreground_hit_ids", "background_hit_ids"))
    if len((fg or set()) | (fh or set())) > record["foreground_size"] or len((bg or set()) | (bh or set())) > record["background_size"]:
        raise ValueError("motif-hit IDs cannot fit in their declared foreground/background memberships")
    if fg is not None and bg is not None and bh is not None and len(fg) == record["foreground_size"] and len(bg) == record["background_size"] and len(bh) == record["background_hits"] and fg <= bg:
        expected = fg & bh
        if len(expected) != record["foreground_hits"] or fh is not None and not fh <= expected:
            raise ValueError("foreground motif hits disagree with saved background hits and membership")
    return evidence


def _missing(record: dict, fields: list[str]) -> str | None:
    if record.get("status") in {"failed", "excluded", "unavailable"}:
        return _text(record.get("reason"), "failed source reason")
    if any(record.get(f) is None for f in fields):
        return _text(record.get("reason"), "missing source value reason")
    return None


def _r(record: dict, x: float | None, y: float | None, *, kind: str, group: str = "",
       prefix: str = "", reason: str | None = None, link: dict | None = None, details: dict | None = None) -> dict:
    return row(record["id"], x, y, entity_kind=kind, group=group,
               key=f"{prefix or kind}:{record['id']}", reason=reason,
               warnings=record.get("warnings", []), details=deepcopy(record if details is None else details), link=link)


def _axes(x: str, xu: str, y: str, yu: str, xt: str = "identity", yt: str = "identity") -> dict:
    return {"x": {"label": x, "unit": _text(xu, "x unit"), "transform": xt},
            "y": {"label": y, "unit": _text(yu, "y unit"), "transform": yt}}


def _point(r: dict, *, radius: float = 3.5, **extra: Any) -> dict:
    return {"type": "point", "x": r["x"], "y": r["y"], "radius": radius,
            "keys": [r["key"]], "group": r["group"], **extra}


def _line(a: dict, b: dict, *, kind: str = "line", group: str | None = None, style: dict | None = None,
          keys: list[str] | None = None) -> dict:
    result = {"type": kind, "x1": a["x"], "y1": a["y"], "x2": b["x"], "y2": b["y"],
              "keys": keys if keys is not None else [a["key"], b["key"]],
              "group": a["group"] if group is None else group}
    if style:
        result["style"] = style
    return result


def _label(r: dict, text: str) -> dict:
    return {"type": "text", "x": r["x"], "y": r["y"], "text": text, "anchor": "start",
            "keys": [r["key"]], "group": r["group"]}


def _finish(cap: str, data: dict, title: str, rows: list[dict], **kwargs: Any) -> dict:
    limitations = list(kwargs.pop("limitations", ()))
    if "source_label" in data:
        limitations.append(f"Supplied source label: {_text(data['source_label'], 'source_label')}; this is not independent scientific validation.")
    return panel(cap, title, rows, limitations=limitations, **kwargs)


def _segments(rows: list[dict]) -> list[list[dict]]:
    result: list[list[dict]] = []
    current: list[dict] = []
    for r in rows:
        if r["x"] is None or r["y"] is None:
            if current:
                result.append(current)
            current = []
        else:
            current.append(r)
    if current:
        result.append(current)
    return result


def _curve_marks(rows: list[dict]) -> list[dict]:
    return [line_mark([[r["x"], r["y"]] for r in segment], keys=[r["key"] for r in segment],
                      group=segment[0]["group"]) for segment in _segments(rows) if len(segment) >= 2]


def _q(record: dict) -> tuple[float | None, str | None]:
    reason = _missing(record, ["q"])
    if reason:
        return None, reason
    q = _num(record["q"], "q", 0, 1)
    if record.get("p") is not None and q < _num(record["p"], "p", 0, 1):
        raise ValueError("BH q must be at least p")
    if q == 0:
        return None, "q underflow: a finite -log10(q) is unavailable; no arbitrary floor was substituted"
    return -math.log10(q), None


def _sized_dots(rows: list[dict], sizes: dict[str, float]) -> tuple[list[dict], dict]:
    maximum = max(sizes.values(), default=0.0)
    dots = [_point(r, radius=9 * math.sqrt(sizes[r["key"]] / maximum) if maximum else 0,
                   size_value=sizes[r["key"]]) for r in rows if r["key"] in sizes]
    return dots, {"label": "-log10(q)", "unit": "-log10 probability", "domain": [0, maximum or 1], "max_radius": 9}


def _color(values: list[float], label: str, unit: str, *, diverging: bool = False) -> dict:
    low, high = min(values, default=0), max(values, default=1)
    if diverging:
        span = max(abs(low), abs(high)) or 1
        low, high = -span, span
    elif low == high:
        low, high = (0, high or 1) if low >= 0 else (low - 0.5, high + 0.5)
    return {"label": label, "unit": unit, "palette": "RdBu_r" if diverging else "viridis",
            "domain": [low, high], "center": 0 if diverging else None}


def _gsea(cap: str, data: dict) -> list[dict]:
    records = _records(data["running_scores"], "running scores", "id rank score", "gene_id")
    hits = _records(data["hits"], "GSEA hits", "id rank gene_id", empty=True)
    n = _int(data["ranked_count"], "ranked_count", 1)
    universe_data, universe_summary, provenance, source_coverage, context_row, context_annotations, context_limitations = _enrichment_context(data, 1)
    universe, size = universe_data["id"], universe_data["size"]
    known_members = (set(universe_data["member_ids"]) if universe_data["member_ids"] is not None
                     and (universe_data["membership_complete"] or len(universe_data["member_ids"]) == size) else None)
    gene_ids = [r["gene_id"] for r in records if r.get("gene_id") is not None]
    require_unique(gene_ids, "ranked gene IDs must be unique when retained")
    if known_members is not None and any(gene not in known_members for gene in [*gene_ids, *[h["gene_id"] for h in hits]]):
        raise ValueError("ranked or hit gene IDs are outside the retained complete tested universe")
    if n > size:
        raise ValueError("ranked count exceeds tested universe")
    ranks = [_int(r["rank"], "rank", 1) for r in records]
    if len(records) != n or any(rank != expected for expected, rank in enumerate(sorted(ranks), 1)):
        raise ValueError("running scores must retain each ranked position exactly once")
    hit_ranks = [_int(r["rank"], "hit rank", 1) for r in hits]
    _unique_values(hit_ranks, "hit ranks must be unique")
    if any(r > n for r in hit_ranks):
        raise ValueError("hit rank is outside saved ranking")
    rows = []
    for record in sorted(records, key=lambda r: r["rank"]):
        reason = _missing(record, ["score"])
        rows.append(_r(record, None if reason else float(record["rank"]), None if reason else _num(record["score"], "running score"),
                       kind="rank", group=data["pathway_id"], reason=reason,
                       link={"kind": "gene", "id": record["gene_id"]} if record.get("gene_id") else None,
                       details={**record, "universe": universe_summary, "provenance": provenance, "source_coverage": source_coverage}))
    by_rank = {r["rank"]: r for r in records}
    rug_rows, rug_marks = [], []
    for hit in hits:
        if by_rank[hit["rank"]].get("gene_id") not in (None, hit["gene_id"]):
            raise ValueError("hit gene does not match ranked gene")
        r = _r(hit, float(hit["rank"]), 0.5, kind="gene", group=data["pathway_id"], link={"kind": "gene", "id": hit["gene_id"]},
               details={**hit, "universe": universe_summary, "provenance": provenance, "source_coverage": source_coverage})
        rug_rows.append(r)
        rug_marks.append({"type": "line", "x1": r["x"], "y1": 0, "x2": r["x"], "y2": 1,
                          "keys": [r["key"]], "group": r["group"]})
    annotations = [*context_annotations, f"Tested universe {universe}: {size}; ranked denominator {n}; saved hit count {len(hits)}.",
                   f"Saved ranking metric: {_text(data['ranking_metric'], 'ranking metric')}."]
    for field in ("nes", "q"):
        if field in data:
            annotations.append(f"Saved {field}: {_num(data[field], field, 0 if field == 'q' else None, 1 if field == 'q' else None):g}.")
    curve_marks = _curve_marks(rows)
    rows.append(context_row)
    rug_rows.append(deepcopy(context_row))
    return [_finish(cap, data, f"{data['pathway_id']}: running enrichment", rows,
                    axes=_axes("Rank", "rank", "Running enrichment score", "enrichment score"),
                    marks=curve_marks, annotations=annotations, limitations=context_limitations,
                    reference_lines=[{"axis": "y", "value": 0, "label": "Zero enrichment"}], panel_id=f"{cap}:curve"),
            _finish(cap, data, f"{data['pathway_id']}: hit barcode", rug_rows,
                    axes=_axes("Rank", "rank", "Gene-set hit", "membership"), marks=rug_marks,
                    annotations=annotations, limitations=context_limitations, panel_id=f"{cap}:barcode")]


def _enrichment(cap: str, data: dict) -> list[dict]:
    method = data["method"]
    if method not in {"ORA", "GSEA", "regulon"} or data["x_metric"] not in {"NES", "GeneRatio"}:
        raise ValueError("enrichment needs explicit ORA/GSEA/regulon method and NES/GeneRatio")
    if method == "ORA" and data["x_metric"] != "GeneRatio":
        raise ValueError("ORA has no NES")
    records = _records(data["terms"], "enrichment terms", "id label overlap_count query_size set_size q direction",
                       "nes p query_member_ids set_member_ids overlap_member_ids membership_complete membership_source_id member_unit")
    universe, universe_summary, provenance, source_coverage, context_row, context_annotations, context_limitations = _enrichment_context(data, len(records))
    size = universe["size"]
    rows, sizes = [], {}
    for i, record in enumerate(records):
        overlap = _int(record["overlap_count"], "overlap count")
        query = _int(record["query_size"], "query denominator")
        set_size = _int(record["set_size"], "set denominator")
        if (query == 0 or set_size == 0) and not _missing(record, []):
            raise ValueError("empty query/set denominator requires an explicit unavailable source reason")
        if max(query, set_size) > size or overlap > min(query, set_size) or overlap < max(0, query + set_size - size):
            raise ValueError("overlap/query/set denominator exceeds tested universe or set size")
        membership = _set_membership(record, universe)
        direction = record["direction"]
        if direction not in {"up", "down", "none"}:
            raise ValueError("direction must be up, down or none")
        score, reason = _q(record)
        if data["x_metric"] == "NES":
            if "nes" not in record:
                raise ValueError("saved NES required")
            reason = reason or _missing(record, ["nes"])
            x = None if reason else _num(record["nes"], "NES")
            if x is not None and ((x < 0 and direction != "down") or (x > 0 and direction != "up")):
                raise ValueError("NES sign conflicts with saved direction")
        else:
            x = None if reason else overlap / query
        r = _r(record, x, None if reason else float(i), kind="gene_set", group=direction, reason=reason,
               details={**record, "universe_id": universe["id"], "universe_size": size, "gene_ratio": overlap / query if query else None,
                        "universe": universe_summary, "provenance": provenance, "source_coverage": source_coverage,
                        "membership_evidence": membership})
        rows.append(r)
        if score is not None and not reason:
            sizes[r["key"]] = score
    dots, size_scale = _sized_dots(rows, sizes)
    for dot in dots:
        dot["shape"] = {"up": "circle", "down": "triangle", "none": "square"}[dot["group"]]
    rows.append(context_row)
    return [_finish(cap, data, f"{method} enrichment", rows,
                    axes=_axes(data["x_metric"], "normalized enrichment score" if data["x_metric"] == "NES" else "overlap / query size", "Gene set", "gene set"),
                    marks=dots, y_categories=[f"{r['label']} ({r['overlap_count']}/{r['query_size']}; set={r['set_size']})" for r in records], size_scale=size_scale,
                    annotations=[*context_annotations,
                                 "Term labels show overlap/query denominator and measured set size. Exact full/partial/unknown membership and source IDs remain inspectable; universe members are stored once in the linked context row.",
                                 "Dot area = -log10(q); group color and shape = saved direction. q=0 is retained as underflow, not floored."],
                    limitations=[*context_limitations, "Enrichment is conditional on the supplied tested universe; membership does not establish causal regulation."])]


def _temporal(cap: str, data: dict) -> list[dict]:
    family = data["family"]
    if family not in {"negative_binomial", "gaussian"}:
        raise ValueError("saved family must be negative_binomial or gaussian")
    require_fields(data["model"], ["df", "knots", "selection"], ["offset_method", "dispersion_method"])
    df = _int(data["model"]["df"], "spline df", 1)
    if not isinstance(data["model"]["knots"], list):
        raise ValueError("saved spline knots must be a list")
    knots = [_num(k, "knot") for k in data["model"]["knots"]]
    _unique_values(knots, "knots must be unique")
    if data["model"]["selection"] not in {"fixed", "candidate_selected"}:
        raise ValueError("model selection must be fixed or candidate_selected")
    if family == "negative_binomial":
        if data["input_scale"] != "raw_counts":
            raise ValueError("negative-binomial raw_counts evidence required")
        if data["model"].get("offset_method") != "RLE" or data["model"].get("dispersion_method") != "empirical_bayes":
            raise ValueError("saved RLE offsets and empirical_bayes dispersion method required for NB")
    elif data["input_scale"] not in {"continuous", "normalized"}:
        raise ValueError("Gaussian spline requires continuous/normalized input without count conversion")
    interval = data.get("interval")
    interval_metadata = {"method": None, "scope": None, "selection_conditional": None}
    if interval is not None:
        require_fields(interval, ["kind", "level"], interval_metadata)
        if interval["kind"] not in {"confidence", "credible"}:
            raise ValueError("saved interval kind must be confidence or credible")
        level = _num(interval["level"], "interval level", 0, 1)
        if not 0 < level < 1:
            raise ValueError("interval level must be strictly between zero and one")
        for field in ("method", "scope"):
            if interval.get(field) is not None:
                interval_metadata[field] = _text(interval[field], f"saved interval {field}")
        conditional = interval.get("selection_conditional")
        if conditional is not None and not isinstance(conditional, bool):
            raise ValueError("saved interval selection_conditional must be boolean or unrecorded")
        interval_metadata["selection_conditional"] = conditional
    observations = _records(data["observations"], "observations", "id biological_unit_id time value", "group")
    fitted = _records(data["fitted"], "fitted", "id time value", "group lower upper interval_status interval_reason")
    rows, marks, fit_groups = [], [], defaultdict(list)
    for record in observations:
        biological = _text(record["biological_unit_id"], "biological unit")
        reason = _missing(record, ["time", "value"])
        x = None if reason else _num(record["time"], "collection time")
        y = None if reason else _num(record["value"], "observation", 0 if family == "negative_binomial" else None)
        if y is not None and family == "negative_binomial" and not y.is_integer():
            raise ValueError("raw count observations must be integers")
        r = _r(record, x, y, kind="sample", group=record.get("group", "observations"), prefix="observation", reason=reason,
               link={"kind": "sample", "id": record["id"]}, details={**record, "gene_id": data["gene_id"], "biological_unit_id": biological})
        rows.append(r)
        if not reason:
            marks.append(_point(r))
    for record in fitted:
        reason = _missing(record, ["time", "value"])
        x = _num(record["time"], "fitted time")
        y = None if reason else _num(record["value"], "fitted mean", 0 if family == "negative_binomial" else None)
        if record.get("interval_status") not in {None, "available", "unavailable", "failed"}:
            raise ValueError("unsupported saved interval status")
        interval_reason = reason
        if not interval_reason and record.get("interval_status") in {"unavailable", "failed"}:
            interval_reason = _text(record.get("interval_reason"), "saved interval failure reason")
        if not interval_reason and interval is None:
            interval_reason = "saved interval kind and level unavailable; bounds cannot be labeled as uncertainty"
        if not interval_reason and (record.get("lower") is None or record.get("upper") is None):
            interval_reason = record.get("interval_reason") or record.get("reason") or "saved interval bounds unavailable"
        if not interval_reason:
            low = _num(record["lower"], "interval lower", 0 if family == "negative_binomial" else None)
            high = _num(record["upper"], "interval upper", 0 if family == "negative_binomial" else None)
            if not low <= y <= high:
                raise ValueError("saved interval does not contain fitted value")
        group = record.get("group", "saved fit")
        r = _r(record, None if reason else x, y, kind="gene", group=group, prefix="fit", reason=reason,
               link={"kind": "gene", "id": data["gene_id"]},
               details={**record, "interval_evidence": {"status": "unavailable" if interval_reason else "available",
                        "reason": interval_reason, "kind": interval["kind"] if interval else None,
                        "level": interval["level"] if interval else None, **interval_metadata}})
        rows.append(r)
        if not reason:
            marks.append(_point(r, radius=3, shape="diamond"))
            if interval_reason:
                r["warnings"].append(f"Interval unavailable: {interval_reason}")
        fit_groups[group].append((x, r))
    for group_rows in fit_groups.values():
        _unique_values([x for x, _ in group_rows], "fitted times per group must be unique")
        ordered = [r for _, r in sorted(group_rows, key=lambda item: item[0])]
        interval_rows = [r if r["details"]["interval_evidence"]["status"] == "available" else {**r, "x": None, "y": None} for r in ordered]
        for segment in _segments(interval_rows):
            if len(segment) >= 2:
                points = [[r["x"], r["details"]["upper"]] for r in segment]
                points += [[r["x"], r["details"]["lower"]] for r in reversed(segment)]
                marks.append({"type": "polygon", "points": points, "keys": [r["key"] for r in segment],
                              "group": segment[0]["group"], "style": {"fill": "#56B4E9", "opacity": 0.2}})
        marks.extend(_curve_marks(ordered))
    n_units = len({r["biological_unit_id"] for r in observations})
    interval_count = sum(r["details"].get("interval_evidence", {}).get("status") == "available" for r in rows)
    interval_label = f"{interval['kind']} interval level={interval['level']:g}" if interval else "interval kind/level unavailable"
    conditional_label = "unrecorded" if interval_metadata["selection_conditional"] is None else str(interval_metadata["selection_conditional"]).lower()
    interval_label += (f"; method={interval_metadata['method'] or 'unrecorded'}; scope={interval_metadata['scope'] or 'unrecorded'}"
                       f"; selection_conditional={conditional_label}")
    return [_finish(cap, data, f"{data['gene_id']}: saved {family} spline", rows,
                    axes=_axes("Collection time", data["time_unit"], "Expression", data["expression_unit"]), marks=marks,
                    annotations=[f"Saved df={df}; knots={knots}; {interval_label}.",
                                 f"{len(observations)} observations from {n_units} biological units; repeated observations are not independent replicates.",
                                 f"Saved prediction means are retained independently of uncertainty; {interval_count}/{len(fitted)} fitted points have eligible interval bounds. Ribbons split at missing uncertainty; mean lines split only at missing predictions."],
                    reference_lines=[{"axis": "x", "value": k, "label": "Saved knot"} for k in knots],
                    limitations=["Saved model and interval are displayed without refitting. Saved selection-conditional metadata is a source declaration; unrecorded method or conditional scope is not inferred. A plotted ribbon does not establish model adequacy."])]


def _saved_temporal(cap: str, data: dict) -> list[dict]:
    """Display the actual saved prediction grid without inventing richer evidence."""
    gene_id = _text(data["gene_id"], "gene id")
    definition = _text(data["prediction_definition"], "prediction definition")
    model = data.get("model", {})
    require_fields(model, [], ["family", "selected_df", "formula", "statistical_method_version", "effect_scale"])
    if model.get("family") not in (None, "gaussian", "negative_binomial"):
        raise ValueError("saved model family must be gaussian or negative_binomial")
    if model.get("selected_df") is not None:
        _int(model["selected_df"], "saved selected df", 1)
    for field in ("formula", "statistical_method_version", "effect_scale"):
        if field in model and model[field] is not None:
            _text(model[field], f"saved model {field}")
    records = _records(data["points"], "saved temporal predictions", "id time value", empty=True)
    diagnostics = _records(data.get("source_records", []), "saved temporal source records", "id status reason", empty=True)
    if not records and not diagnostics:
        raise ValueError("saved temporal curve requires prediction points or retained failed source records")
    _unique_values([_num(r["time"], "saved time-grid coordinate") for r in records], "saved time-grid coordinates must be unique")
    rows = []
    for record in sorted(records, key=lambda r: r["time"]):
        reason = _missing(record, ["value"])
        value = None if reason else _num(record["value"], "saved prediction")
        rows.append(_r(record, None if reason else float(record["time"]), value, kind="gene", group="saved prediction",
                       prefix="saved_prediction", reason=reason, link={"kind": "gene", "id": gene_id},
                       details={**record, "gene_id": gene_id, "prediction_definition": definition, "model": model}))
    marks = [_point(r) for r in rows if r["x"] is not None]
    marks.extend(_curve_marks(rows))
    for record in diagnostics:
        reason = _missing(record, [])
        if not reason:
            raise ValueError("source-only temporal records must explicitly describe unavailable/failed predictions")
        rows.append(_r(record, None, None, kind="gene", prefix="temporal_source", reason=reason,
                       link={"kind": "gene", "id": record["id"]}, details={**record, "model": model}))
    return [_finish(cap, data, f"{gene_id}: saved temporal predictions", rows,
                    axes=_axes("Saved time-grid coordinate", data["time_unit"], "Saved prediction", data["value_unit"]), marks=marks,
                    annotations=[f"Prediction definition: {definition}.", f"Saved model metadata: {model}.",
                                 "Individual biological-unit observations, spline knots and uncertainty bounds/level are unavailable in this saved-curve packet.",
                                 "Points show the saved prediction grid; straight segments connect adjacent available predictions with no new smoothing."],
                    limitations=["A saved prediction curve alone does not establish fit adequacy or comparative method performance. Selection-conditional inference remains conditional on upstream model selection; no confidence ribbon or biological replicate values were fabricated."])]


def _candidate_comparison(cap: str, data: dict) -> list[dict]:
    criteria = _strings(data["criteria"], "criteria")
    if set(criteria) - {"AIC", "BIC", "GCV"}:
        raise ValueError("only actual saved AIC/BIC/GCV candidates are supported")
    _text(data["comparison_set_id"], "comparison set")
    require_fields(data["comparison_basis"], ["data_id", "response", "likelihood"])
    for k, v in data["comparison_basis"].items():
        _text(v, f"comparison basis {k}")
    records = _records(data["candidates"], "candidates", "id df comparison_set_id", "aic bic gcv knots")
    selected_id = data.get("selected_candidate_id")
    if selected_id is not None and selected_id not in {r["id"] for r in records}:
        raise ValueError("saved selected candidate must reference a retained candidate id")
    selected_df = data.get("selected_df")
    if selected_df is not None:
        selected_df = _int(selected_df, "saved selected df", 1)
    if selected_id is not None and selected_df is not None and next(r["df"] for r in records if r["id"] == selected_id) != selected_df:
        raise ValueError("saved selected candidate and selected_df disagree")
    criterion = data.get("selection_criterion")
    if criterion is not None:
        _text(criterion, "saved selection criterion")
    provenance = {} if data.get("selection_provenance") is None else data["selection_provenance"]
    require_fields(provenance, [], ["source_id", "method", "version", "description"])
    for name, value in provenance.items():
        if value is not None:
            _text(value, f"selection provenance {name}")
    selection_evidence = {"selected_candidate_id": selected_id, "selected_df": selected_df, "criterion": criterion,
                          "provenance": {"source_id": None, "method": None, "version": None, "description": None, **provenance}}
    matches = [r["id"] for r in records if r["df"] == selected_df] if selected_df is not None else []
    selected = selected_id if selected_id is not None else (matches[0] if len(matches) == 1 else None)
    if selected is not None:
        selection_note = f"Saved selected candidate: {selected}; selected_df={selected_df if selected_df is not None else 'not separately retained'}; criterion={criterion or 'unassessed'}."
        if selected_id is None:
            selection_note += " Candidate identity is uniquely resolved from the saved selected_df, not from a minimum criterion."
    elif selected_df is not None:
        selection_note = f"Saved selected_df={selected_df}; candidate identity is ambiguous/unavailable among retained rows; criterion={criterion or 'unassessed'}. No candidate is chosen post hoc."
    else:
        selection_note = "Saved selected candidate and selected_df unavailable; no selection is inferred from minimum criterion."
    result = []
    for criterion in criteria:
        rows = []
        for record in records:
            if record["comparison_set_id"] != data["comparison_set_id"]:
                raise ValueError("candidate comparison sets must match")
            df = _int(record["df"], "candidate df", 1)
            reason = _missing(record, [criterion.lower()])
            value = None if reason else _num(record[criterion.lower()], criterion, 0 if criterion == "GCV" else None)
            df_match = df == selected_df if selected_df is not None else None
            group = "saved selection" if record["id"] == selected else ("saved df; candidate ambiguous" if selected is None and df_match else criterion)
            rows.append(_r(record, None if reason else float(df), value, kind="model", group=group,
                           reason=reason, details={**record, "selected": record["id"] == selected if selected is not None else None,
                                                  "selected_df_match": df_match, "selection_evidence": selection_evidence}))
        marks = [_point(r, shape="diamond" if r["details"]["selected"] else ("square" if r["details"]["selected_df_match"] else "circle"))
                 for r in rows if r["x"] is not None]
        result.append(_finish(cap, data, f"Saved candidate comparison: {criterion}", rows,
                              axes=_axes("Spline df", "degrees of freedom", criterion, criterion), marks=marks,
                              annotations=[f"Comparison set: {data['comparison_set_id']}; saved basis: {data['comparison_basis']}.",
                                           selection_note, f"Saved selection provenance: {selection_evidence['provenance']}.",
                                           "Lower recorded criterion values are descriptive; model appropriateness is not established by this plot."],
                              panel_id=f"{cap}:{criterion}"))
    return result


def _pseudotime(cap: str, data: dict) -> list[dict]:
    records = _records(data["observations"], "pseudotime observations", "id collection_age pseudotime biological_unit_id", "group")
    rows = []
    for record in records:
        _text(record["biological_unit_id"], "biological unit")
        reason = _missing(record, ["collection_age", "pseudotime"])
        rows.append(_r(record, None if reason else _num(record["collection_age"], "collection age"),
                       None if reason else _num(record["pseudotime"], "pseudotime"), kind="cell", group=record.get("group", ""), reason=reason,
                       link={"kind": "cell", "id": record["id"]}))
    annotations = [f"Saved ordering method: {_text(data['ordering_method'], 'ordering method')}."]
    for statistic in data.get("statistics", []):
        require_fields(statistic, ["name", "value", "method"])
        annotations.append(f"Saved {statistic['name']}={_num(statistic['value'], 'monotonicity statistic'):g}; method={statistic['method']}.")
    return [_finish(cap, data, "Collection age and inferred pseudotime", rows,
                    axes=_axes("Collection age (reference)", data["age_unit"], "Inferred pseudotime", data["pseudotime_unit"]),
                    annotations=annotations,
                    limitations=["Collection age is a reference, not ground truth for a universal pseudotime. Repeated cells/specimens are not independent biological replicates."])]


def _graph(cap: str, data: dict) -> list[dict]:
    nodes = _records(data["nodes"], "nodes", "id label", "x y sample_count mean_pseudotime" if cap == "lineage_schematic" else "x y")
    edges = _records(data["edges"], "edges", "id source target", "evidence", empty=True)
    ids = {n["id"] for n in nodes}
    incoming, outgoing = {i: 0 for i in ids}, defaultdict(list)
    for edge in edges:
        if edge["source"] not in ids or edge["target"] not in ids:
            raise ValueError("edge endpoint must reference a retained node")
        incoming[edge["target"]] += 1
        outgoing[edge["source"]].append(edge["target"])
        if cap == "lineage_schematic" and edge.get("evidence") not in {"inferred", "observed", "hypothesis"}:
            raise ValueError("lineage edges require explicit evidence scope")
    ready = sorted(i for i, count in incoming.items() if count == 0)
    ordered, depth = [], {i: 0 for i in ready}
    while ready:
        node_id = ready.pop(0)
        ordered.append(node_id)
        for target in sorted(outgoing[node_id]):
            depth[target] = max(depth.get(target, 0), depth[node_id] + 1)
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
                ready.sort()
    if len(ordered) != len(nodes):
        raise ValueError("a lineage/execution DAG cannot contain a cycle")
    supplied = ["x" in n or "y" in n for n in nodes]
    if any(supplied) and not all(supplied):
        raise ValueError("supply positions for every node or use the declared schematic layout")
    slots, rows, lookup = defaultdict(int), [], {}
    for node in nodes:
        if "sample_count" in node:
            _int(node["sample_count"], "lineage sample count")
        if "mean_pseudotime" in node:
            _num(node["mean_pseudotime"], "lineage mean pseudotime")
        if all(supplied):
            x, y = _num(node.get("x"), "schematic x"), _num(node.get("y"), "schematic y")
        else:
            x, y = float(depth[node["id"]]), float(slots[depth[node["id"]]])
            slots[depth[node["id"]]] += 1
        r = _r(node, x, y, kind="execution" if cap == "execution_dag" else "lineage", group=node.get("status", "node"), prefix="node")
        lookup[node["id"]] = r
        rows.append(r)
    marks = [_point(r, radius=6) for r in rows]
    marks += [_label(r, r["details"]["label"]) for r in rows]
    for edge in edges:
        a, b = lookup[edge["source"]], lookup[edge["target"]]
        reason = _missing(edge, [])
        r = _r(edge, None if reason else (a["x"] + b["x"]) / 2, None if reason else (a["y"] + b["y"]) / 2,
               kind="edge", group=edge.get("evidence", "dependency"), reason=reason)
        rows.append(r)
        if not reason:
            marks.append(_line(a, b, kind="arrow", group=r["group"], keys=[r["key"], a["key"], b["key"]]))
    membership_annotation = []
    if "membership" in data:
        membership = data["membership"]
        require_fields(membership, ["root_id", "samples"], ["root_sample_id", "pseudotime_unit", "complete"])
        if membership["root_id"] not in ids:
            raise ValueError("lineage root_id must refer to a retained graph node")
        members = _records(membership["samples"], "lineage samples", "id pseudotime branch_id", empty=True)
        if "root_sample_id" in membership and membership["root_sample_id"] not in {r["id"] for r in members}:
            raise ValueError("lineage root sample must belong to retained membership")
        if "complete" in membership and not isinstance(membership["complete"], bool):
            raise ValueError("lineage membership completeness must be boolean")
        unit = membership.get("pseudotime_unit")
        if unit is not None:
            _text(unit, "pseudotime unit")
        for record in members:
            _text(record["branch_id"], "saved branch label")
            reason = _missing(record, ["pseudotime"])
            if not reason:
                _num(record["pseudotime"], "saved sample pseudotime")
            rows.append(_r(record, None, None, kind="sample", prefix="lineage_membership", group=record["branch_id"],
                           reason=reason or "saved branch/pseudotime membership; schematic node coordinates do not represent sample coordinates",
                           link={"kind": "sample", "id": record["id"]},
                           details={**record, "root_id": membership["root_id"], "root_sample_id": membership.get("root_sample_id"),
                                    "pseudotime_unit": unit, "membership_complete": membership.get("complete")}))
        membership_annotation.append(f"Retained {len(members)} sample branch memberships; root node={membership['root_id']}; pseudotime unit={unit or 'unassessed'}. Branch labels are distinct from graph node IDs.")
    return [_finish(cap, data, "Execution DAG" if cap == "execution_dag" else "Lineage schematic", rows,
                    axes=_axes("Schematic depth", "layout units", "Schematic lane", "layout units"), marks=marks,
                    annotations=["Schematic layout: saved positions." if all(supplied) else "Schematic layout: deterministic topological depth and stable input-order lanes; coordinates are not biological measurements.",
                                 f"Saved method: {_text(data['method'], 'graph method')}.", *membership_annotation],
                    limitations=["Execution dependencies are an inventory surface, not authenticated signatures or scientific validation." if cap == "execution_dag" else "Lineage arrows encode the supplied evidence-labelled topology, not independently established causal differentiation."])]


def _regulatory(cap: str, data: dict) -> list[dict]:
    tfs, targets = _strings(data["tf_ids"], "TF ids"), _strings(data["target_ids"], "target ids")
    edges = _records(data["edges"], "regulatory edges", "id tf target",
                     "curated_membership motif_hit correlation correlation_method curated_source motif_source "
                     "association_type coefficient coefficient_definition association_method score sign ridge_parameters")
    rows, marks, lookup = [], [], {}
    for role, ids, x in (("TF", tfs, 0.0), ("target", targets, 1.0)):
        for i, identity in enumerate(ids):
            record = {"id": identity, "role": role}
            r = _r(record, x, float(i), kind="gene", group=role, prefix=role, link={"kind": "gene", "id": identity})
            rows.append(r)
            lookup[(role, identity)] = r
            marks.extend([_point(r, radius=6, shape="square" if role == "TF" else "circle"), _label(r, identity)])
    prepared = []
    for edge in edges:
        reason = _missing(edge, [])
        if edge["tf"] not in tfs or edge["target"] not in targets:
            raise ValueError("regulatory endpoints must reference supplied TF and target ids")
        curated, motif_hit = edge.get("curated_membership"), edge.get("motif_hit")
        if any(v is not None and not isinstance(v, bool) for v in (curated, motif_hit)):
            raise ValueError("curated_membership and motif_hit must be booleans or null (unassessed)")
        if curated:
            _text(edge.get("curated_source"), "curated membership source")
        if motif_hit:
            _text(edge.get("motif_source"), "motif-hit evidence source")
        association = edge.get("association_type", "correlation" if "correlation" in edge else "membership_only")
        if association not in {"correlation", "ridge_association", "membership_only"}:
            raise ValueError("explicit supported regulatory association_type required")
        value = None
        if association == "ridge_association":
            if "correlation" in edge or "correlation_method" in edge:
                raise ValueError("ridge coefficients cannot be relabeled as correlation")
            if edge.get("coefficient_definition") != "standardized_ridge_coefficient":
                raise ValueError("ridge coefficient_definition must be standardized_ridge_coefficient")
            _text(edge.get("association_method"), "ridge association method")
            if "coefficient" not in edge:
                raise ValueError("saved signed ridge coefficient required")
            reason = reason or _missing(edge, ["coefficient"])
            if not reason:
                value = _num(edge["coefficient"], "standardized ridge coefficient")
                if "score" in edge and not math.isclose(_num(edge["score"], "absolute ridge score", 0), abs(value), rel_tol=1e-10, abs_tol=1e-12):
                    raise ValueError("ridge score must equal the absolute standardized coefficient, not a probability")
                if "sign" in edge and _num(edge["sign"], "ridge sign", -1, 1) != ((value > 0) - (value < 0)):
                    raise ValueError("ridge sign must match the saved signed coefficient")
            if "ridge_parameters" in edge:
                params = edge["ridge_parameters"]
                require_fields(params, ["alpha", "lag_observed_intervals", "transform"])
                _num(params["alpha"], "ridge alpha", 0)
                _int(params["lag_observed_intervals"], "ridge lag observed intervals")
                _text(params["transform"], "ridge transform")
        else:
            if any(k in edge for k in ("coefficient", "coefficient_definition", "association_method", "score", "sign", "ridge_parameters")):
                raise ValueError("ridge fields require explicit ridge_association type")
            if association == "correlation":
                reason = reason or _missing(edge, ["correlation"])
                if not reason:
                    value = _num(edge["correlation"], "correlation weight", -1, 1)
                    _text(edge.get("correlation_method"), "correlation method")
            elif "correlation" in edge or "correlation_method" in edge:
                raise ValueError("membership-only edges cannot contain correlation fields")
        if not reason and value is None and not curated and not motif_hit:
            raise ValueError("each regulatory edge needs actual saved evidence")
        a, b = lookup[("TF", edge["tf"])], lookup[("target", edge["target"])]
        r = _r(edge, None if reason else 0.5, None if reason else (a["y"] + b["y"]) / 2,
               kind="edge", group=association, reason=reason,
               details={**edge, "association_type": association, "curated_membership": curated, "motif_hit": motif_hit})
        rows.append(r)
        if not reason:
            prepared.append((r, a, b, association, value, curated, motif_hit))
    ridge_max = max((abs(v) for _, _, _, kind, v, _, _ in prepared if kind == "ridge_association"), default=0) or 1
    for r, a, b, association, value, curated, motif_hit in prepared:
        if association == "ridge_association":
            stroke, width, group = ("#009E73" if value >= 0 else "#CC79A7"), 1 + 3 * abs(value) / ridge_max, "ridge association"
        elif association == "correlation":
            stroke, width, group = ("#0072B2" if value >= 0 else "#D55E00"), 1 + 3 * abs(value), "correlation association"
        else:
            stroke, width, group = "#777777", 1, "curated/motif evidence"
        style = {"stroke": stroke, "stroke_width": width, "dash": [] if curated is True else ([5, 3] if curated is False else [1, 3])}
        marks.append(_line(a, b, group=group,
                           keys=[r["key"], a["key"], b["key"]], style=style))
        if motif_hit is True:
            marks.append(_point(r, radius=5, shape="diamond", style={"fill": "#CC79A7"}))
        elif motif_hit is False:
            marks.append(_point(r, radius=2, shape="square", style={"fill": "none", "stroke": "#777777"}))
    return [_finish(cap, data, "TF–target evidence graph", rows,
                    axes=_axes("Bipartite role", "layout units", "Node lane", "layout units"), marks=marks,
                    x_categories=["TF", "Target"],
                    annotations=["Deterministic bipartite schematic; positions are not biological coordinates.",
                                 "Correlation: blue/orange sign and width 1+3|r|. Ridge: green/magenta sign; width 1+3|coefficient|/maximum |ridge coefficient| within this panel.",
                                 "Ridge values are saved signed standardized ridge coefficients, not correlations; their absolute score is not a probability.",
                                 "Curated membership: solid=present, dashed=absent, dotted=unassessed. Motif: diamond=hit, empty square=assessed no hit, no glyph=unassessed."],
                    limitations=["Correlation and standardized ridge coefficients are associations, curated membership is database evidence, and motif hits are sequence evidence. None alone establishes causal regulation or promoter binding."])]


def _motif(cap: str, data: dict) -> list[dict]:
    records = _records(data["terms"], "motif terms", "id label foreground_hits foreground_size background_hits background_size fold_enrichment q",
                       "p foreground_member_ids background_member_ids foreground_hit_ids background_hit_ids membership_complete membership_source_id member_unit")
    universe, universe_summary, provenance, source_coverage, context_row, context_annotations, context_limitations = _enrichment_context(data, len(records))
    total = universe["size"]
    rows, sizes = [], {}
    for i, record in enumerate(records):
        fg = _int(record["foreground_size"], "foreground size", 1)
        bg = _int(record["background_size"], "background size", 1)
        fh = _int(record["foreground_hits"], "foreground hits")
        bh = _int(record["background_hits"], "background hits")
        if fg > total or bg > total or fh > fg or bh > bg:
            raise ValueError("motif overlap denominators exceed tested universe")
        if bg == total and (fh > min(fg, bh) or fh < max(0, fg + bh - total)):
            raise ValueError("motif foreground hits contradict whole-universe background hit counts")
        membership = _motif_membership(record, universe)
        score, reason = _q(record)
        reason = reason or _missing(record, ["fold_enrichment"])
        value = None if reason else _num(record["fold_enrichment"], "fold enrichment", 0)
        if not reason and (bh == 0 or not math.isclose(value, (fh / fg) / (bh / bg), rel_tol=1e-6, abs_tol=1e-10)):
            raise ValueError("saved motif fold enrichment must agree with explicit foreground/background fractions")
        r = _r(record, value, None if reason else float(i), kind="motif", reason=reason,
               details={**record, "universe_id": universe["id"], "universe_size": total,
                        "universe": universe_summary, "provenance": provenance, "source_coverage": source_coverage,
                        "membership_evidence": membership})
        rows.append(r)
        if score is not None and not reason:
            sizes[r["key"]] = score
    dots, scale = _sized_dots(rows, sizes)
    rows.append(context_row)
    promoters = _records(data["promoters"] if data.get("promoters") is not None else [], "saved promoter intervals",
                         "id gene_id chromosome start end strand genome_build coordinate_system", "source_id", empty=True)
    known_members = set(universe["member_ids"]) if universe["member_ids"] is not None and universe["membership_complete"] and universe["unit"] == "promoter" else None
    for record in promoters:
        for field in ("gene_id", "chromosome", "genome_build"):
            _text(record[field], f"promoter {field}")
        if record["coordinate_system"] not in {"0-based-half-open", "1-based-inclusive"}:
            raise ValueError("promoter intervals require a declared genomic coordinate system")
        _interval(record, record["coordinate_system"])
        if record["strand"] not in {"+", "-", "."}:
            raise ValueError("promoter strand must be +, -, or explicitly unknown (.)")
        if record.get("source_id") is not None:
            _text(record["source_id"], "promoter source id")
        if known_members is not None and record["id"] not in known_members:
            raise ValueError("promoter interval is outside the complete tested promoter universe")
        rows.append(_r(record, None, None, kind="promoter", prefix="promoter_source",
                       reason="saved promoter interval metadata; no enrichment-dot coordinate",
                       link={"kind": "gene", "id": record["gene_id"]}, details={**record, "coordinate_unit": "bp"}))
    promoter_annotation = (f"{len(promoters)} saved promoter interval records (bp); other promoter coordinates are unassessed."
                           if promoters else "Promoter interval membership/coordinates are unavailable; promoter binding is not inferred.")
    return [_finish(cap, data, "Motif enrichment", rows,
                    axes=_axes("Saved fold enrichment", "foreground fraction / background fraction", "Motif", "motif"),
                    marks=dots, y_categories=[f"{r['label']} (foreground {r['foreground_hits']}/{r['foreground_size']}; background {r['background_hits']}/{r['background_size']})" for r in records], size_scale=scale,
                    annotations=[*context_annotations, promoter_annotation,
                                 f"Promoter definition: {provenance['promoter_definition'] or 'unassessed'}; promoter source: {provenance['promoter_source_id'] or 'unassessed'}.",
                                 "Term labels show observed hit counts/tested denominators. Dot area = -log10(q); exact available memberships are retained for inspection."],
                    limitations=[*context_limitations, "Motif enrichment does not establish physical binding or causal transcriptional regulation."])]


def _resource(cap: str, data: dict) -> list[dict]:
    records = _records(data["measurements"], "resource measurements", "id time value series")
    groups, rows = defaultdict(list), []
    for record in records:
        time = _num(record["time"], "elapsed time", 0)
        reason = _missing(record, ["value"])
        r = _r(record, None if reason else time, None if reason else _num(record["value"], "resource use", 0),
               kind="measurement", group=_text(record["series"], "resource series"), reason=reason)
        rows.append(r)
        groups[r["group"]].append((time, r))
    marks = [_point(r) for r in rows if r["x"] is not None]
    for values in groups.values():
        _unique_values([t for t, _ in values], "resource series times must be unique")
        marks.extend(_curve_marks([r for _, r in sorted(values, key=lambda pair: pair[0])]))
    return [_finish(cap, data, f"Saved {data['metric']} measurements", rows,
                    axes=_axes("Elapsed time", data["time_unit"], _text(data["metric"], "metric"), data["value_unit"]), marks=marks,
                    limitations=["Resource curves describe execution inventory; runtime or memory does not establish scientific method superiority."])]


def _clone_data(data: dict, samples: list[str]) -> list[dict]:
    records = _records(data["clones"], "clone records", "id sample_id clone_id count", empty=True)
    _text(data["clone_definition"], "clone definition")
    if not isinstance(data["biological_units"], dict) or set(data["biological_units"]) != set(samples):
        raise ValueError("each sample requires an explicit biological unit")
    for unit in data["biological_units"].values():
        _text(unit, "biological unit")
    pairs = []
    for record in records:
        if record["sample_id"] not in samples:
            raise ValueError("clone sample must be declared")
        _text(record["clone_id"], "clone id")
        if not _missing(record, ["count"]):
            _int(record["count"], "clone count")
        pairs.append((record["sample_id"], record["clone_id"]))
    _unique_values(pairs, "sample/clone membership must be unique")
    return records


def _abundance(cap: str, data: dict) -> list[dict]:
    if not isinstance(data["sample_totals"], dict) or not data["sample_totals"]:
        raise ValueError("sample totals are required")
    samples = list(data["sample_totals"])
    records = _clone_data(data, samples)
    totals = {s: _int(data["sample_totals"][s], "sample total", 1) for s in samples}
    for sample in samples:
        if sum(r["count"] for r in records if r["sample_id"] == sample and not _missing(r, ["count"])) > totals[sample]:
            raise ValueError("observed clone counts exceed the supplied sample total")
    if data["metric"] not in {"count", "fraction"}:
        raise ValueError("clone abundance metric must be count or fraction")
    clones = list(dict.fromkeys(r["clone_id"] for r in records))
    rows, marks = [], []
    for record in records:
        reason = _missing(record, ["count"])
        sample = record["sample_id"]
        value = None if reason else (float(record["count"]) if data["metric"] == "count" else record["count"] / totals[sample])
        r = _r(record, None if reason else float(clones.index(record["clone_id"])), None if reason else value,
               kind="clonotype", group=sample, reason=reason, link={"kind": "clonotype", "id": record["clone_id"]},
               details={**record, "sample_total": totals[sample], "biological_unit_id": data["biological_units"][sample]})
        rows.append(r)
        if not reason:
            marks.append(_point(r))
    return [_finish(cap, data, "Clonal abundance", rows,
                    axes=_axes("Clone", "clone", "Observed clone abundance", "clone count" if data["metric"] == "count" else "fraction of sample total"),
                    marks=marks, x_categories=clones,
                    annotations=[f"Saved clone definition: {data['clone_definition']}; sample totals: {totals}.",
                                 "Each point represents a clone within a sample; samples retain explicit biological units."],
                    limitations=["Clonal abundance does not establish antigen specificity or independent biological replication."])]


def _overlap(cap: str, data: dict) -> list[dict]:
    samples = _strings(data["samples"], "samples")
    if len(samples) ** 2 > 200000:
        raise ValueError("overlap matrix exceeds explicit source-row budget")
    records = _clone_data(data, samples)
    if len(samples) ** 2 + len(records) > 200000 or 2 * len(samples) * len(records) > 1000000:
        raise ValueError("overlap membership exceeds explicit row/reference budget")
    if data["metric"] != "jaccard":
        raise ValueError("this adapter requires explicit jaccard overlap")
    members = {s: {r["clone_id"] for r in records if r["sample_id"] == s and not _missing(r, ["count"]) and r["count"] > 0} for s in samples}
    rows, marks = [], []
    for i, sample_a in enumerate(samples):
        for j, sample_b in enumerate(samples):
            shared, union = sorted(members[sample_a] & members[sample_b]), sorted(members[sample_a] | members[sample_b])
            failed = [r for r in records if r["sample_id"] in {sample_a, sample_b} and _missing(r, ["count"])]
            reason = "failed clone records prevent a complete overlap calculation" if failed else (None if union else "empty clone-set union; Jaccard is undefined")
            overlap = None if reason else len(shared) / len(union)
            record = {"id": f"pair:{i}:{j}", "sample_a": sample_a, "sample_b": sample_b,
                      "shared_clone_ids": shared, "union_clone_ids": union, "shared_count": len(shared),
                      "union_count": len(union), "overlap": overlap,
                      "source_record_ids": [r["id"] for r in records if r["sample_id"] in {sample_a, sample_b}],
                      "biological_unit_a": data["biological_units"][sample_a], "biological_unit_b": data["biological_units"][sample_b]}
            r = _r(record, None if reason else float(j), None if reason else float(i), kind="sample_pair", reason=reason)
            rows.append(r)
            if not reason:
                marks.append({"type": "rect", "x": j - 0.5, "y": i - 0.5, "width": 1, "height": 1,
                              "value": overlap, "keys": [r["key"]], "group": "overlap"})
    for record in records:
        rows.append(_r(record, None, None, kind="clonotype", prefix="clone_source", group=record["sample_id"],
                       reason=_missing(record, ["count"]) or "saved clone membership input; represented by derived overlap cells",
                       link={"kind": "clonotype", "id": record["clone_id"]}))
    return [_finish(cap, data, "Observed repertoire overlap", rows,
                    axes=_axes("Sample B", "sample", "Sample A", "sample"), marks=marks,
                    x_categories=samples, y_categories=samples,
                    color_scale={"label": "Jaccard intersection / union", "unit": "fraction", "palette": "viridis", "domain": [0, 1], "center": None},
                    annotations=[f"Clone definition: {data['clone_definition']}. Positive-count clone membership only; intersection and union IDs are retained. Original clone records are retained as aggregation inputs without standalone matrix coordinates."],
                    limitations=["This is descriptive observed-set overlap; sampling depth affects observed membership."])]


def _diversity(cap: str, data: dict) -> list[dict]:
    records = _records(data["metrics"], "diversity metrics", "id sample_id biological_unit_id value estimator")
    samples = [r["sample_id"] for r in records]
    require_unique(samples, "diversity samples must be unique")
    rows = []
    for i, record in enumerate(records):
        _text(record["biological_unit_id"], "biological unit")
        _text(record["estimator"], "saved diversity estimator")
        reason = _missing(record, ["value"])
        rows.append(_r(record, None if reason else float(i), None if reason else _num(record["value"], "diversity estimate"),
                       kind="sample", group=record["biological_unit_id"], reason=reason, link={"kind": "sample", "id": record["sample_id"]}))
    return [_finish(cap, data, f"Saved repertoire diversity: {data['metric_name']}", rows,
                    axes=_axes("Sample", "sample", _text(data["metric_name"], "diversity metric"), data["unit"]), x_categories=samples,
                    annotations=["Saved estimator retained per sample; no uncertainty intervals were supplied or fabricated."],
                    limitations=["Diversity estimates depend on clone definition and sampling; observations are not independent unless biological units are independent."])]


def _assay(value: dict) -> dict:
    require_fields(value, ["id", "feature", "unit", "transform"])
    for field in value:
        _text(value[field], f"assay {field}")
    return value


def _multimodal_features(cap: str, data: dict) -> list[dict]:
    x_assay, y_assay = _assay(data["x_assay"]), _assay(data["y_assay"])
    if data["paired_by"] != "cell_id" or x_assay["id"] == y_assay["id"]:
        raise ValueError("paired distinct assays require explicit cell_id pairing")
    records = _records(data["pairs"], "paired assay observations", "id cell_id x_cell_id y_cell_id x_value y_value biological_unit_id", "group")
    require_unique([r["cell_id"] for r in records], "paired cell ids must be unique")
    rows = []
    for record in records:
        if not record["cell_id"] == record["x_cell_id"] == record["y_cell_id"]:
            raise ValueError("paired assay cell IDs do not match")
        _text(record["biological_unit_id"], "biological unit")
        reason = _missing(record, ["x_value", "y_value"])
        rows.append(_r(record, None if reason else _num(record["x_value"], "paired x assay value"),
                       None if reason else _num(record["y_value"], "paired y assay value"),
                       kind="cell", group=record.get("group", ""), reason=reason,
                       link={"kind": "cell", "id": record["cell_id"]}, details={**record, "x_assay": x_assay, "y_assay": y_assay}))
    return [_finish(cap, data, f"Paired {x_assay['id']} / {y_assay['id']} features", rows,
                    axes=_axes(x_assay["feature"], x_assay["unit"], y_assay["feature"], y_assay["unit"], x_assay["transform"], y_assay["transform"]),
                    annotations=["Coordinates are saved assay-specific values; transforms are declared, not recomputed. Same-cell pairing is explicit."],
                    limitations=["Assay association is descriptive and does not establish causal regulation; cells do not replace biological replicates."])]


def _multimodal_heatmap(cap: str, data: dict) -> list[dict]:
    paired = _strings(data["paired_ids"], "paired ids")
    if data["paired_by"] != "cell_id":
        raise ValueError("multimodal blocks require explicit cell_id pairing")
    assays = _records(data["assays"], "assays", "id unit transform features values cell_ids")
    if len(assays) < 2:
        raise ValueError("multimodal heatmap requires at least two paired assays")
    if len(assays) > 32 or any(not isinstance(a["features"], list) for a in assays) or sum(len(a["features"]) * len(paired) for a in assays) > 200000:
        raise ValueError("multimodal matrix exceeds explicit cell/panel budget")
    result = []
    for assay_index, assay in enumerate(assays):
        features = _strings(assay["features"], "assay features")
        if assay["cell_ids"] != paired:
            raise ValueError("paired cell IDs and their order must match in every assay block")
        if not isinstance(assay["values"], list) or len(assay["values"]) != len(features):
            raise ValueError("assay feature rows must match values")
        rows, marks, values = [], [], []
        for i, feature in enumerate(features):
            if not isinstance(assay["values"][i], list) or len(assay["values"][i]) != len(paired):
                raise ValueError("assay paired columns must match values")
            for j, cell_id in enumerate(paired):
                value = assay["values"][i][j]
                reason = _missing(assay, []) or ("saved assay value missing; no imputation performed" if value is None else None)
                if value is not None:
                    value = _num(value, "assay matrix value")
                    if not reason:
                        values.append(value)
                record = {"id": f"{assay['id']}:{i}:{j}", "assay_id": assay["id"], "feature_id": feature,
                          "cell_id": cell_id, "value": value, "unit": assay["unit"], "transform": assay["transform"]}
                r = _r(record, None if reason else float(j), None if reason else float(i), kind="cell_feature", reason=reason,
                       link={"kind": "cell", "id": cell_id})
                rows.append(r)
                if not reason:
                    marks.append({"type": "rect", "x": j - 0.5, "y": i - 0.5, "width": 1, "height": 1,
                                  "value": value, "keys": [r["key"]], "group": assay["id"]})
        result.append(_finish(cap, data, f"Paired assay block: {assay['id']}", rows,
                              axes=_axes("Paired cell", "cell", "Assay feature", "feature"), marks=marks,
                              x_categories=paired, y_categories=features,
                              color_scale=_color(values, f"{assay['id']} expression", _text(assay["unit"], "assay unit")),
                              annotations=[f"Saved assay transform: {_text(assay['transform'], 'assay transform')}. Each assay uses its own units and scale; pairing is same-cell."],
                              panel_id=f"{cap}:assay:{assay_index}"))
    return result


def _velocity(cap: str, data: dict) -> list[dict]:
    dt = _num(data["time_step"], "velocity display time step", 0)
    if dt <= 0:
        raise ValueError("velocity display time step must be positive")
    expected = f"{data['embedding_unit']}/{data['time_unit']}"
    if data["vector_unit"] != expected:
        raise ValueError(f"saved projected vector_unit must be {expected}")
    _text(data["projection_method"], "saved velocity projection method")
    _text(data["count_unit"], "spliced/unspliced count unit")
    records = _records(data["cells"], "velocity cells", "id x y spliced unspliced velocity_x velocity_y", "group")
    rows, marks = [], []
    for record in records:
        reason = _missing(record, ["x", "y", "spliced", "unspliced", "velocity_x", "velocity_y"])
        if not reason:
            _num(record["spliced"], "spliced", 0)
            _num(record["unspliced"], "unspliced", 0)
        r = _r(record, None if reason else _num(record["x"], "embedding x"), None if reason else _num(record["y"], "embedding y"),
               kind="cell", group=record.get("group", "saved velocity"), reason=reason, link={"kind": "cell", "id": record["id"]})
        rows.append(r)
        if not reason:
            marks.extend([_point(r), {"type": "arrow", "x1": r["x"], "y1": r["y"],
                "x2": r["x"] + _num(record["velocity_x"], "saved x velocity") * dt,
                "y2": r["y"] + _num(record["velocity_y"], "saved y velocity") * dt,
                "keys": [r["key"]], "group": r["group"]}])
    return [_finish(cap, data, "Saved RNA velocity projection", rows,
                    axes=_axes("Embedding 1", data["embedding_unit"], "Embedding 2", data["embedding_unit"]), marks=marks,
                    annotations=[f"Projected vector units: {data['vector_unit']}; display interval: {dt:g} {data['time_unit']}; method: {data['projection_method']}.",
                                 f"Spliced and unspliced evidence retained in {data['count_unit']}."],
                    limitations=["Arrows display the saved fitted/projection vectors; no velocity fitting or future cell-state validation is performed."])]


def _spatial(cap: str, data: dict) -> list[dict]:
    if data["coordinate_unit"] not in {"um", "µm", "micrometers", "mm", "millimeters", "nm", "cm", "m"}:
        raise ValueError("spatial positions require declared physical coordinate units")
    registration = data["registration"]
    require_fields(registration, ["coordinate_system", "method", "reference", "registered"])
    for field in ("coordinate_system", "method", "reference"):
        _text(registration[field], f"registration {field}")
    if not isinstance(registration["registered"], bool):
        raise ValueError("registration registered status must be boolean")
    records = _records(data["spots"], "spatial spots", "id x y expression section_id")
    sections = list(dict.fromkeys(r["section_id"] for r in records))
    result = []
    for section_index, section in enumerate(sections):
        _text(section, "section id")
        rows, marks, values = [], [], []
        for record in (r for r in records if r["section_id"] == section):
            reason = _missing(record, ["x", "y", "expression"])
            value = None if reason else _num(record["expression"], "spatial expression")
            r = _r(record, None if reason else _num(record["x"], "physical x"), None if reason else _num(record["y"], "physical y"),
                   kind="spot", group=section, reason=reason,
                   details={**record, "feature_id": data["feature_id"], "registration": registration, "coordinate_unit": data["coordinate_unit"]})
            rows.append(r)
            if not reason:
                values.append(value)
                marks.append(_point(r, radius=5, value=value))
        result.append(_finish(cap, data, f"{data['feature_id']}: spatial section {section}", rows,
                              axes=_axes("Physical x", data["coordinate_unit"], "Physical y", data["coordinate_unit"]), marks=marks,
                              color_scale=_color(values, data["feature_id"], _text(data["expression_unit"], "expression unit")),
                              annotations=[f"Saved registration metadata: {registration}. Sections are faceted; no alignment is inferred."],
                              limitations=["Coordinates and registration are supplied observations/declarations; rendering does not validate registration accuracy."],
                              panel_id=f"{cap}:section:{section_index}"))
    return result


def _interval(record: dict, coordinate_system: str) -> tuple[float, float, float]:
    minimum = 1 if coordinate_system == "1-based-inclusive" else 0
    start, end = _int(record["start"], "interval start", minimum), _int(record["end"], "interval end", minimum)
    width = end - start + (1 if coordinate_system == "1-based-inclusive" else 0)
    if width <= 0:
        raise ValueError("genomic interval end must follow its start")
    return float(start), float(end), float(width)


_TRACK_FIELDS = ("sample_id", "biological_unit_id", "replicate_id", "replicate_kind", "strand", "source_id")


def _track_context(record: dict, supplied: dict) -> dict:
    metadata = {field: None for field in _TRACK_FIELDS}
    require_fields(supplied, [], _TRACK_FIELDS)
    metadata.update(supplied)
    for field in _TRACK_FIELDS:
        if record.get(field) is not None:
            if metadata[field] is not None and metadata[field] != record[field]:
                raise ValueError("track-level and row-level sample/replicate/strand metadata disagree")
            metadata[field] = record[field]
        if metadata[field] is not None:
            _text(metadata[field], f"track {field}")
    if metadata["strand"] not in {None, "+", "-", "."}:
        raise ValueError("track strand must be +, -, ., or unavailable")
    if metadata["replicate_kind"] not in {None, "biological", "technical", "repeated_measure", "unknown"}:
        raise ValueError("replicate_kind must describe biological/technical/repeated_measure/unknown scope")
    return metadata


def _splice(cap: str, data: dict) -> list[dict]:
    system = data["coordinate_system"]
    if system not in {"0-based-half-open", "1-based-inclusive"}:
        raise ValueError("explicit genomic coordinate_system required")
    chromosome, build = _text(data["chromosome"], "chromosome"), _text(data["genome_build"], "genome build")
    coverage = _records(data["coverage"], "coverage", "id start end value track", " ".join(_TRACK_FIELDS))
    junctions = _records(data["junctions"], "junctions", "id start end count track", " ".join(_TRACK_FIELDS))
    exons = _records(data["exons"], "exons", "id start end transcript_id", "strand gene_id source_id")
    metadata = {} if data.get("track_metadata") is None else data["track_metadata"]
    if not isinstance(metadata, dict):
        raise ValueError("track_metadata must map saved track IDs to metadata")
    tracks = {_text(r["track"], "saved track id") for r in [*coverage, *junctions]}
    if set(metadata) - tracks:
        raise ValueError("track metadata must refer to retained coverage/junction tracks")
    track_notes = []
    seen_contexts = set()
    for record in [*coverage, *junctions]:
        context = _track_context(record, metadata.get(record["track"], {}))
        signature = (record["track"], *(context[k] for k in _TRACK_FIELDS))
        if signature not in seen_contexts:
            seen_contexts.add(signature)
            track_notes.append(f"Track {record['track']}: sample={context['sample_id'] or 'unassessed'}; biological unit={context['biological_unit_id'] or 'unassessed'}; replicate={context['replicate_id'] or 'unassessed'} ({context['replicate_kind'] or 'unassessed'}); strand={context['strand'] or 'unassessed'}.")
    rows, marks = [], []
    for record in coverage:
        start, _, width = _interval(record, system)
        reason = _missing(record, ["value"])
        value = None if reason else _num(record["value"], "saved coverage", 0)
        context = _track_context(record, metadata.get(record["track"], {}))
        r = _r(record, None if reason else start, value, kind="genomic_interval", group=record["track"], reason=reason,
               link={"kind": "sample", "id": context["sample_id"]} if context["sample_id"] is not None else None,
               details={**record, "track_metadata": context, "coordinate_unit": "bp"})
        rows.append(r)
        if not reason:
            marks.append({"type": "rect", "x": start, "y": 0, "width": width, "height": value,
                          "keys": [r["key"]], "group": r["group"], "style": {"opacity": 0.55}})
    annotation = [f"{build} {chromosome}; source coordinates: {system}; interval endpoints are preserved.", *track_notes,
                  "Sample/replicate metadata are supplied declarations. A track label alone does not establish a sample identity or independent biological replication."]
    result = [_finish(cap, data, f"{chromosome}: saved coverage", rows,
                      axes=_axes("Genomic position", "bp", "Coverage", data["coverage_unit"]), marks=marks,
                      annotations=annotation, panel_id=f"{cap}:coverage")]
    rows, marks = [], []
    for record in junctions:
        start, end, _ = _interval(record, system)
        if end <= start:
            raise ValueError("junction interval requires distinct donor/acceptor positions")
        reason = _missing(record, ["count"])
        count = None if reason else _int(record["count"], "junction count")
        context = _track_context(record, metadata.get(record["track"], {}))
        r = _r(record, None if reason else (start + end) / 2, None if reason else float(count),
               kind="junction", group=record["track"], reason=reason,
               link={"kind": "sample", "id": context["sample_id"]} if context["sample_id"] is not None else None,
               details={**record, "track_metadata": context, "coordinate_unit": "bp"})
        rows.append(r)
        if not reason:
            span = end - start
            marks.append({"type": "arc", "points": [[start, 0], [start + span / 3, count * 4 / 3],
                          [end - span / 3, count * 4 / 3], [end, 0]], "keys": [r["key"]], "group": r["group"]})
            marks.append(_label(r, str(count)))
    result.append(_finish(cap, data, f"{chromosome}: saved junction/sashimi track", rows,
                          axes=_axes("Genomic position", "bp", "Junction count (arc apex)", "junction reads"), marks=marks,
                          annotations=[*annotation, "Cubic arc apex equals the saved junction count; arc shape is a display construction, not a genomic path."],
                          panel_id=f"{cap}:junctions"))
    transcripts = list(dict.fromkeys(e["transcript_id"] for e in exons))
    rows, marks, spans = [], [], defaultdict(list)
    for record in exons:
        _text(record["transcript_id"], "transcript id")
        if record.get("strand") not in {None, "+", "-", "."}:
            raise ValueError("saved exon strand must be +, -, ., or unavailable")
        for field in ("gene_id", "source_id"):
            if record.get(field) is not None:
                _text(record[field], f"exon {field}")
        start, end, width = _interval(record, system)
        y = float(transcripts.index(record["transcript_id"]))
        reason = _missing(record, [])
        r = _r(record, None if reason else start, None if reason else y, kind="exon", group=record["transcript_id"], reason=reason,
               link={"kind": "gene", "id": record["gene_id"]} if record.get("gene_id") is not None else None,
               details={"strand": None, **record, "coordinate_unit": "bp"})
        rows.append(r)
        if not reason:
            marks.append({"type": "rect", "x": start, "y": y - 0.2, "width": width, "height": 0.4,
                          "keys": [r["key"]], "group": r["group"]})
            spans[record["transcript_id"]].append((start, end, r))
    for transcript, intervals in spans.items():
        if len(intervals) > 1:
            y = float(transcripts.index(transcript))
            strands = {v[2]["details"]["strand"] for v in intervals}
            strand = next(iter(strands)) if len(strands) == 1 else None
            first, last = min(v[0] for v in intervals), max(v[1] for v in intervals)
            if strand == "-":
                first, last = last, first
            marks.insert(0, {"type": "arrow" if strand in {"+", "-"} else "line", "x1": first, "y1": y,
                             "x2": last, "y2": y,
                             "keys": [v[2]["key"] for v in intervals], "group": transcript})
    result.append(_finish(cap, data, f"{chromosome}: saved isoform exons", rows,
                          axes=_axes("Genomic position", "bp", "Transcript", "transcript"), marks=marks, y_categories=transcripts,
                          annotations=[*annotation, "Thin transcript connectors indicate isoform membership, not measured junction support. Arrow direction uses consistently saved exon strand only; missing/inconsistent strand stays unassessed."],
                          panel_id=f"{cap}:exons"))
    return result


def _perturbation(cap: str, data: dict) -> list[dict]:
    conditions = _strings(data["conditions"], "conditions")
    if len(conditions) != 2 or data["design"] not in {"paired", "unpaired"}:
        raise ValueError("perturbation plot requires two declared conditions and paired/unpaired design")
    records = _records(data["observations"], "perturbation observations", "id biological_unit_id condition value")
    pairs = [(r["biological_unit_id"], r["condition"]) for r in records]
    _unique_values(pairs, "biological unit/condition must be unique; aggregate technical replicates upstream explicitly")
    rows, marks, groups = [], [], defaultdict(dict)
    for record in records:
        unit = _text(record["biological_unit_id"], "biological unit")
        if record["condition"] not in conditions:
            raise ValueError("observation condition is not declared")
        reason = _missing(record, ["value"])
        r = _r(record, None if reason else float(conditions.index(record["condition"])), None if reason else _num(record["value"], "saved expression"),
               kind="sample", group=unit, reason=reason, link={"kind": "sample", "id": record["id"]},
               details={**record, "feature_id": data["feature_id"]})
        rows.append(r)
        if not reason:
            marks.append(_point(r))
            groups[unit][record["condition"]] = r
    complete = 0
    if data["design"] == "paired":
        for values in groups.values():
            if all(c in values for c in conditions):
                marks.append(_line(values[conditions[0]], values[conditions[1]]))
                complete += 1
    elif len({r["biological_unit_id"] for r in records}) != len(records):
        raise ValueError("repeated biological units require declared paired design")
    return [_finish(cap, data, f"{data['feature_id']}: perturbation / control", rows,
                    axes=_axes("Declared condition", "condition", "Saved expression", data["expression_unit"]), marks=marks,
                    x_categories=conditions,
                    annotations=[f"Design: {data['design']}; complete biological pairs drawn: {complete}. Incomplete/failed observations remain in the table."],
                    limitations=["The comparison displays observed measurements and declared design; it does not independently establish perturbation causality."])]


def _orthology(cap: str, data: dict) -> list[dict]:
    if data["mapping_policy"] != "retain_all_pairs":
        raise ValueError("explicit retain_all_pairs mapping_policy required; averaging one-to-many mappings is unsupported")
    source, target = _text(data["source_species"], "source species"), _text(data["target_species"], "target species")
    if source == target:
        raise ValueError("cross-species comparison requires distinct species")
    provenance_fields = ["source_id", "mapping_version", "source_namespace", "target_namespace", "method", "source_genome_build", "target_genome_build"]
    supplied_provenance = {} if data.get("mapping_provenance") is None else data["mapping_provenance"]
    require_fields(supplied_provenance, [], provenance_fields)
    mapping_provenance = {field: None for field in provenance_fields}
    for field, value in supplied_provenance.items():
        mapping_provenance[field] = None if value is None else _text(value, f"orthology {field}")
    mappings = _records(data["mappings"], "orthology mappings", "id source_gene target_gene relationship evidence")
    values = _records(data["values"], "orthology values", "id species gene_id value")
    _unique_values([(r["species"], r["gene_id"]) for r in values], "species/gene values must be unique")
    lookup = {(r["species"], r["gene_id"]): r for r in values}
    if any(r["species"] not in {source, target} for r in values):
        raise ValueError("values contain an undeclared species")
    _unique_values([(r["source_gene"], r["target_gene"]) for r in mappings], "orthology pairs must be unique")
    source_degree = Counter(m["source_gene"] for m in mappings)
    target_degree = Counter(m["target_gene"] for m in mappings)
    rows, used = [], set()
    for mapping in mappings:
        _text(mapping["evidence"], "orthology mapping evidence")
        if mapping["relationship"] not in {"one_to_one", "one_to_many", "many_to_one", "many_to_many"}:
            raise ValueError("explicit orthology relationship required")
        if mapping["relationship"] == "one_to_one" and (source_degree[mapping["source_gene"]] != 1 or target_degree[mapping["target_gene"]] != 1):
            raise ValueError("declared one_to_one relationship conflicts with mapping multiplicity")
        a, b = (source, mapping["source_gene"]), (target, mapping["target_gene"])
        used.update((a, b))
        reason = _missing(mapping, [])
        if a not in lookup or b not in lookup:
            reason = reason or "mapped gene lacks a saved species-specific value"
        if not reason:
            reason = _missing(lookup[a], ["value"]) or _missing(lookup[b], ["value"])
        rows.append(_r(mapping, None if reason else _num(lookup[a]["value"], "source-species value"),
                       None if reason else _num(lookup[b]["value"], "target-species value"),
                       kind="orthology_pair", group=mapping["relationship"], reason=reason,
                       details={**mapping, "source_species": source, "target_species": target,
                                "source_value_record": lookup.get(a), "target_value_record": lookup.get(b),
                                "mapping_policy": data["mapping_policy"], "mapping_provenance": mapping_provenance}))
    for key, record in lookup.items():
        if key not in used:
            rows.append(_r(record, None, None, kind="gene", prefix="unmapped", reason="gene has no supplied orthology mapping",
                           link={"kind": "gene", "id": record["gene_id"]},
                           details={**record, "mapping_policy": data["mapping_policy"], "mapping_provenance": mapping_provenance}))
    return [_finish(cap, data, f"Explicit {source} / {target} orthology comparison", rows,
                    axes=_axes(f"{source} saved effect", data["effect_unit"], f"{target} saved effect", data["effect_unit"]),
                    annotations=["Each point is an explicit mapping pair. One-to-many values are retained and not averaged; unmapped source values remain in the table.",
                                 f"Mapping source={mapping_provenance['source_id'] or 'unassessed'}; version={mapping_provenance['mapping_version'] or 'unassessed'}; method={mapping_provenance['method'] or 'unassessed'}.",
                                 f"Source namespace={mapping_provenance['source_namespace'] or 'unassessed'}; target namespace={mapping_provenance['target_namespace'] or 'unassessed'}; mapping policy={data['mapping_policy']}."],
                    limitations=["Mapping provenance/namespaces are saved declarations, not independent mapping validation. Mapped genes are not automatically functionally equivalent; repeated one-to-many points are not independent tests."])]


def _genomic(cap: str, data: dict) -> list[dict]:
    if data["coordinate_system"] not in {"0-based-half-open", "1-based-inclusive"}:
        raise ValueError("explicit genomic coordinate_system required")
    _text(data["genome_build"], "genome build")
    records = _records(data["genes"], "genomic genes", "id gene_id chromosome position effect")
    chromosomes = list(dict.fromkeys(r["chromosome"] for r in records))
    result = []
    for chromosome_index, chromosome in enumerate(chromosomes):
        _text(chromosome, "chromosome")
        rows = []
        for record in (r for r in records if r["chromosome"] == chromosome):
            reason = _missing(record, ["position", "effect"])
            position = None if reason else float(_int(record["position"], "genome position", 1 if data["coordinate_system"] == "1-based-inclusive" else 0))
            rows.append(_r(record, position, None if reason else _num(record["effect"], "saved genomic effect"),
                           kind="gene", group=chromosome, reason=reason, link={"kind": "gene", "id": record["gene_id"]}))
        result.append(_finish(cap, data, f"{chromosome}: saved genomic effects", rows,
                              axes=_axes("Genomic position", "bp", "Saved effect", data["effect_unit"]),
                              annotations=[f"Genome build {data['genome_build']}; {data['coordinate_system']}; chromosome facets avoid fabricated interchromosomal coordinates."],
                              reference_lines=[{"axis": "y", "value": 0, "label": "Zero effect"}], panel_id=f"{cap}:chromosome:{chromosome_index}"))
    return result


def _sva(cap: str, data: dict) -> list[dict]:
    metric = data["association_metric"]
    if metric not in {"pearson_r", "spearman_r", "r_squared", "saved_effect"}:
        raise ValueError("explicit supported saved association metric required")
    records = _records(data["associations"], "SVA associations", "id surrogate covariate value method", "p q")
    _unique_values([(r["surrogate"], r["covariate"]) for r in records], "surrogate/covariate pairs must be unique")
    surrogates = list(dict.fromkeys(r["surrogate"] for r in records))
    covariates = list(dict.fromkeys(r["covariate"] for r in records))
    rows, marks, values = [], [], []
    for record in records:
        _text(record["method"], "association method")
        _text(record["surrogate"], "surrogate id")
        _text(record["covariate"], "covariate id")
        reason = _missing(record, ["value"])
        if not reason:
            value = _num(record["value"], "correlation" if metric.endswith("_r") else "association",
                         -1 if metric.endswith("_r") else (0 if metric == "r_squared" else None),
                         1 if metric.endswith("_r") or metric == "r_squared" else None)
            for probability in ("p", "q"):
                if probability in record:
                    _num(record[probability], probability, 0, 1)
            if "q" in record and "p" in record:
                if record["q"] < record["p"]:
                    raise ValueError("BH q must be at least p")
            values.append(value)
        x, y = float(covariates.index(record["covariate"])), float(surrogates.index(record["surrogate"]))
        r = _r(record, None if reason else x, None if reason else y, kind="association", reason=reason)
        rows.append(r)
        if not reason:
            marks.append({"type": "rect", "x": x - 0.5, "y": y - 0.5, "width": 1, "height": 1,
                          "value": value, "keys": [r["key"]], "group": record["surrogate"]})
    color = _color(values, metric, _text(data["unit"], "association unit"), diverging=metric in {"pearson_r", "spearman_r", "saved_effect"})
    if metric in {"pearson_r", "spearman_r"}:
        color["domain"] = [-1, 1]
    elif metric == "r_squared":
        color["domain"] = [0, 1]
    if "color_domain" in data:
        supplied = data["color_domain"]
        if not isinstance(supplied, list) or len(supplied) != 2:
            raise ValueError("saved association color_domain must contain two endpoints")
        supplied = [_num(v, "color domain") for v in supplied]
        if metric != "saved_effect" and supplied != color["domain"]:
            raise ValueError("correlation/r_squared color domain must preserve the full natural metric range")
        if supplied[0] >= supplied[1] or not math.isclose(-supplied[0], supplied[1]) and metric == "saved_effect":
            raise ValueError("saved effect color domain must be symmetric around zero")
        if any(v < supplied[0] or v > supplied[1] for v in values):
            raise ValueError("saved association values exceed declared color domain")
        color["domain"] = supplied
    return [_finish(cap, data, f"Saved surrogate-variable associations: {metric}", rows,
                    axes=_axes("Covariate", "covariate", "Surrogate variable", "surrogate variable"), marks=marks,
                    x_categories=covariates, y_categories=surrogates,
                    color_scale=color,
                    limitations=["Saved associations do not prove confounding removal, batch identifiability, or causal biological effects."])]


_BUILDERS = {
    "gsea_running": _gsea, "enrichment_dotplot": _enrichment,
    "temporal_spline": _temporal, "temporal_model_comparison": _candidate_comparison,
    "saved_temporal_curve": _saved_temporal,
    "pseudotime_monotonicity": _pseudotime, "lineage_schematic": _graph,
    "regulatory_network": _regulatory, "motif_enrichment": _motif,
    "resource_curve": _resource, "execution_dag": _graph,
    "repertoire_abundance": _abundance, "repertoire_overlap": _overlap,
    "repertoire_diversity": _diversity, "multimodal_features": _multimodal_features,
    "multimodal_heatmap": _multimodal_heatmap, "rna_velocity": _velocity,
    "spatial_expression": _spatial, "splice_tracks": _splice,
    "perturbation_control": _perturbation, "orthology_comparison": _orthology,
    "genomic_effect": _genomic, "sva_association": _sva,
}


def build_panels(capability: str, data: dict) -> list[dict]:
    """Project validated saved quantities; refuse scientifically missing inputs."""
    if capability not in CAPABILITIES:
        raise ValueError(f"unknown advanced plot capability: {capability}")
    spec = CAPABILITIES[capability]
    require_fields(data, spec["required"], spec["optional"])
    return _BUILDERS[capability](capability, data)
