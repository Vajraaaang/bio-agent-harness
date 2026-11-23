"""Bounded descriptive figures from saved supplied-population metadata.

No expression array is consumed and no model, contrast, rank or significance
test is computed. Biological unit names remain declarations, not authenticated
independent replicates. The trusted artifact reader owns run/receipt custody.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy

from devbench.core.population_contract import content_sha256, validate_population_spec

from .scientific_plot_contract import panel, require_fields, require_unique, row

FORMATS = frozenset(
    "devbench.scrna-population-" + name + "/1"
    for name in ("audit", "design", "pseudobulk", "contrast", "summary")
)
_CONTRAST_RESULT_FORMATS = {
    "devbench.scrna-population-contrast/1",
    "devbench.scrna-population-contrast/2",
}
_FIELDS = (
    "populations",
    "stages",
    "biological_unit_column",
    "biological_unit_levels",
    "profiles",
    "missing_combinations",
    "contrasts",
    "evidence",
)
CAPABILITIES = {
    "scrna_population_cell_counts": {
        "required": list(_FIELDS),
        "optional": [],
        "description": (
            "Observed cells per declared biological unit and supplied population, with "
            "recorded collection stages; absent profiles and missing counts remain "
            "unavailable."
        ),
    },
    "scrna_population_contrast_support": {
        "required": list(_FIELDS),
        "optional": [],
        "description": (
            "Saved stage-specific contrast support and paired supplied-population "
            "membership per declared biological unit; no effect estimates or "
            "authenticated replication claim."
        ),
    },
}
LIMITATIONS = [
    "Pre-fit counts and membership only; no fitted effects or significance tests.",
    (
        "Embryo identities and stages are declared; independence is not authenticated, "
        "and cells are not biological replicates."
    ),
    "Missing population profiles or counts are unavailable, never zero-filled.",
]
DISPLAY_ORDER = "Declared stage order; original unit order within each stage."


def _text(value, label):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 200
        or "\x00" in value
    ):
        raise ValueError("scrna_plot_requires_bounded_" + label)
    return value


def _texts(value, label, maximum):
    require_unique(value, label)
    if not 1 <= len(value) <= maximum:
        raise ValueError("scrna_plot_category_budget_exceeded:" + label)
    return [_text(item, label) for item in value]


def _integer(value, label, *, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**53 - 1:
        raise ValueError("scrna_plot_requires_saved_integer:" + label)
    return value


def _sha(value, label):
    if not isinstance(value, str) or re.fullmatch("[a-f0-9]{64}", value) is None:
        raise ValueError("scrna_plot_requires_source_digest:" + label)
    return value


def _records(value, label, maximum, *, empty=False):
    if not isinstance(value, list) or not (0 if empty else 1) <= len(value) <= maximum:
        raise ValueError("scrna_plot_row_budget_exceeded:" + label)
    return value


def _validate_global_design(global_design):
    if global_design is not None:
        require_fields(
            global_design, ("identifiable", "refusal_codes", "diagnostics", "warnings")
        )
        if (
            global_design["identifiable"] is not None
            and type(global_design["identifiable"]) is not bool
        ):
            raise ValueError("scrna_plot_global_design_status_invalid")
        for code in _records(
            global_design["refusal_codes"], "design_refusal_codes", 64, empty=True
        ):
            _text(code, "design_refusal_code")
        if not isinstance(global_design["diagnostics"], dict):
            raise ValueError("scrna_plot_global_design_diagnostics_invalid")
        for warning in _records(
            global_design["warnings"], "design_warnings", 128, empty=True
        ):
            if not isinstance(warning, dict):
                raise ValueError("scrna_plot_global_design_warning_invalid")
            _text(warning.get("code"), "design_warning_code")


def _validate_plot_evidence(evidence):
    require_fields(
        evidence,
        (
            "source_sha256",
            "specification_sha256",
            "design_sha256",
            "result_format",
            "result_status",
            "source_kind",
            "biological_unit_identity_status",
            "independence_verified",
        ),
        ("source_limitations", "global_design"),
    )
    source_limits = _records(
        evidence.get("source_limitations", []), "source_limitations", 128, empty=True
    )
    if any(
        not isinstance(note, str)
        or not note.strip()
        or len(note) > 4000
        or "\x00" in note
        for note in source_limits
    ):
        raise ValueError("scrna_plot_source_limitations_invalid")
    _validate_global_design(evidence.get("global_design"))
    for name in ("source_sha256", "specification_sha256", "design_sha256"):
        _sha(evidence[name], name)
    if (
        evidence["source_kind"] != "pre_fit"
        or evidence["biological_unit_identity_status"] != "declared"
        or evidence["independence_verified"] is not False
        or evidence["result_format"]
        not in (FORMATS | _CONTRAST_RESULT_FORMATS)
        - {"devbench.scrna-population-audit/1", "devbench.scrna-population-summary/1"}
        or evidence["result_status"]
        not in {"completed", "scientific_refusal", "scientific_failure"}
    ):
        raise ValueError("scrna_plot_evidence_scope_invalid")


def _plot_membership(data, populations, stages, units):
    profiles = _records(data["profiles"], "profiles", 256)
    missing = _records(
        data["missing_combinations"], "missing_combinations", 512, empty=True
    )
    identities, membership, stages_by_unit = [], {}, {}
    for item in [*profiles, *missing]:
        observed = "profile_id" in item
        require_fields(
            item,
            ("profile_id", "biological_unit", "population", "stage", "cell_count")
            if observed
            else ("biological_unit", "population", "stage"),
        )
        unit, population, stage = (
            item[key] for key in ("biological_unit", "population", "stage")
        )
        if unit not in units or population not in populations or stage not in stages:
            raise ValueError("scrna_plot_profile_outside_declared_membership")
        if unit in stages_by_unit and stages_by_unit[unit] != stage:
            raise ValueError("scrna_plot_biological_unit_crosses_collection_stages")
        stages_by_unit[unit] = stage
        key = (unit, population)
        if key in membership:
            raise ValueError("scrna_plot_duplicate_population_profile")
        membership[key] = item if observed else None
        if observed:
            identities.append(_text(item["profile_id"], "profile_id"))
            if item["cell_count"] is not None:
                _integer(item["cell_count"], "cell_count", minimum=1)
    require_unique(identities, "population_profile_ids")
    if set(membership) != {
        (unit, population) for unit in units for population in populations
    }:
        raise ValueError("scrna_plot_missing_combinations_must_be_explicit")
    return membership, stages_by_unit


def _validate_saved_contrasts(
    data, populations, stages, units, membership, stages_by_unit
):
    contrasts = _records(data["contrasts"], "contrasts", 20)
    require_unique([item["contrast_id"] for item in contrasts], "contrast_ids")
    if [item["stage"] for item in contrasts] != stages:
        raise ValueError("scrna_plot_contrast_stage_order_mismatch")
    for contrast in contrasts:
        require_fields(
            contrast,
            (
                "contrast_id",
                "stage",
                "numerator",
                "denominator",
                "paired_unit_count",
                "minimum_paired_units",
                "estimable",
                "supported",
                "refusal_code",
            ),
            ("vector",),
        )
        _text(contrast["contrast_id"], "contrast_id")
        if [contrast["denominator"], contrast["numerator"]] != populations:
            raise ValueError("scrna_plot_contrast_population_mismatch")
        paired = sum(
            all(
                membership[(unit, population)] is not None for population in populations
            )
            for unit in units
            if stages_by_unit[unit] == contrast["stage"]
        )
        if _integer(contrast["paired_unit_count"], "paired_unit_count") != paired:
            raise ValueError("scrna_plot_paired_unit_count_mismatch")
        minimum = _integer(
            contrast["minimum_paired_units"], "minimum_paired_units", minimum=1
        )
        if (
            type(contrast["estimable"]) is not bool
            or type(contrast["supported"]) is not bool
            or contrast["supported"] != (contrast["estimable"] and paired >= minimum)
        ):
            raise ValueError("scrna_plot_saved_contrast_support_inconsistent")
        if contrast["refusal_code"] is not None:
            _text(contrast["refusal_code"], "refusal_code")


def _saved_summary_axes(document, design):
    if document.get("result_format") not in _CONTRAST_RESULT_FORMATS:
        raise ValueError("scrna_plot_summary_result_format_invalid")
    require_fields(document["result_artifact"], ("sha256", "size_bytes"))
    _sha(document["result_artifact"]["sha256"], "result_artifact")
    _integer(
        document["result_artifact"]["size_bytes"],
        "result_artifact_bytes",
        minimum=1,
    )
    populations = [
        design["contrasts"][0]["denominator"],
        design["contrasts"][0]["numerator"],
    ]
    stages = [item["stage"] for item in design["contrasts"]]
    return populations, stages


def _saved_specification_axes(document, design):
    specification = validate_population_spec(document["specification"])
    if (
        content_sha256(specification) != document["specification_sha256"]
        or specification["source_sha256"] != document["source_sha256"]
        or specification["biological_unit_column"] != design["biological_unit_column"]
    ):
        raise ValueError("scrna_plot_specification_binding_mismatch")
    if (
        content_sha256(
            {key: value for key, value in design.items() if key != "design_sha256"}
        )
        != design["design_sha256"]
    ):
        raise ValueError("scrna_plot_saved_design_digest_mismatch")
    populations = [item["label"] for item in specification["populations"]]
    stages = specification["stage_levels"]
    return populations, stages


def _validate(data):
    require_fields(data, _FIELDS)
    if len(json.dumps(data, allow_nan=False).encode()) > 512_000:
        raise ValueError("scrna_plot_metadata_byte_budget_exceeded")
    populations = _texts(data["populations"], "populations", 2)
    if len(populations) != 2:
        raise ValueError("scrna_plot_requires_two_supplied_populations")
    stages = _texts(data["stages"], "stages", 20)
    units = _texts(data["biological_unit_levels"], "biological_units", 256)
    _text(data["biological_unit_column"], "biological_unit_column")
    _validate_plot_evidence(data["evidence"])
    membership, stages_by_unit = _plot_membership(data, populations, stages, units)
    _validate_saved_contrasts(
        data, populations, stages, units, membership, stages_by_unit
    )
    return membership, stages_by_unit


def saved_population_plot_input(document):
    """Project only retained profile counts and contrast-support metadata."""
    from .scientific_plot_sources import MissingSavedPlotInputs

    if document.get("format") not in FORMATS:
        raise ValueError("scrna_plot_result_format_unsupported")
    design, audit = document.get("design"), document.get("audit")
    if (
        document["format"] == "devbench.scrna-population-audit/1"
        or not isinstance(design, dict)
        or not design.get("profiles")
        or not design.get("contrasts")
    ):
        raise MissingSavedPlotInputs(
            "scrna_saved_embryo_population_profiles_and_contrast_support_absent"
        )
    if not isinstance(audit, dict):
        raise ValueError("scrna_plot_saved_audit_required")
    summary = document["format"] == "devbench.scrna-population-summary/1"
    axes = _saved_summary_axes if summary else _saved_specification_axes
    populations, stages = axes(document, design)
    data = {
        name: deepcopy(design[name])
        for name in (
            "biological_unit_column",
            "biological_unit_levels",
            "profiles",
            "missing_combinations",
            "contrasts",
        )
    }
    for profile in data["profiles"]:
        profile.setdefault("cell_count", None)
    data.update(
        populations=populations,
        stages=stages,
        evidence={
            "source_sha256": document["source_sha256"],
            "specification_sha256": document["specification_sha256"],
            "design_sha256": design["design_sha256"],
            "result_format": document.get("result_format", document["format"]),
            "result_status": document["status"],
            "source_kind": "pre_fit",
            "biological_unit_identity_status": audit.get(
                "biological_unit_identity_status"
            ),
            "independence_verified": audit.get("independence_verified"),
            "source_limitations": deepcopy(document.get("limitations", [])),
            "global_design": {
                "identifiable": design.get("identifiable"),
                "refusal_codes": deepcopy(design.get("refusal_codes", [])),
                "diagnostics": deepcopy(design.get("diagnostics", {})),
                "warnings": deepcopy(design.get("warnings", [])),
            },
        },
    )
    _validate(data)
    if _integer(
        audit["declared_biological_unit_count"], "declared_biological_unit_count"
    ) != len(data["biological_unit_levels"]):
        raise ValueError("scrna_plot_declared_unit_count_mismatch")
    if set(audit["population_cell_counts"]) != set(populations):
        raise ValueError("scrna_plot_audit_population_membership_mismatch")
    for population in populations:
        count = _integer(
            audit["population_cell_counts"][population], "population_cell_count"
        )
        profiles = [
            item for item in data["profiles"] if item["population"] == population
        ]
        known_count = sum(
            item["cell_count"] for item in profiles if item["cell_count"] is not None
        )
        if known_count > count or (
            all(item["cell_count"] is not None for item in profiles)
            and count != known_count
        ):
            raise ValueError("scrna_plot_audit_population_count_mismatch")
    if _integer(audit["selected_cell_count"], "selected_cell_count") != sum(
        audit["population_cell_counts"].values()
    ):
        raise ValueError("scrna_plot_audit_selected_count_mismatch")
    return {
        "format": "devbench.scientific-plot-input/1",
        "title": "Saved supplied-population pre-fit diagnostics",
        "plots": [
            {"id": name, "capability": name, "data": deepcopy(data)}
            for name in CAPABILITIES
        ],
        "limitations": [],
    }


def _design_context(data):
    """Keep global admission separate from local saved pairing/estimability."""
    evidence = data["evidence"]
    global_design = evidence.get("global_design") or {}
    refusals = global_design.get("refusal_codes", [])
    notes, warnings, qualifier = [], [], None
    if global_design.get("identifiable") is False or refusals:
        qualifier = "design refused"
        notes.append(
            "Global design refused: "
            + (
                "; ".join(refusals)
                or "saved design/result refusal; detailed reason unavailable"
            )
            + "."
        )
        warnings.extend(refusals or ["global_design_refused"])
    elif evidence["result_status"] == "scientific_refusal":
        qualifier = "result refused"
        notes.append(
            "Saved result status: scientific_refusal; this does not change the "
            "saved global design admission."
        )
        warnings.append("scientific_refusal")
        stage_reasons = [
            item["stage"] + ": " + item["refusal_code"]
            for item in data["contrasts"]
            if item["refusal_code"]
        ]
        if stage_reasons:
            notes.append(
                "Saved stage refusal reasons: " + "; ".join(stage_reasons) + "."
            )
    elif evidence["result_status"] == "scientific_failure":
        qualifier = "result failed"
        notes.append(
            "Saved result status: scientific_failure; descriptive metadata does "
            "not establish contrast admission."
        )
        warnings.append("scientific_failure")
    elif global_design.get("identifiable") is not True:
        qualifier = "admission unknown"
        notes.append("Global design admission was not retained.")
        warnings.append("global_design_admission_unavailable")
    if qualifier:
        notes.append(
            "Local paired population presence and saved stage estimability do not "
            "establish contrast admission."
        )
    diagnostic_codes = list(
        dict.fromkeys(warning["code"] for warning in global_design.get("warnings", []))
    )
    if diagnostic_codes:
        notes.append(
            "Saved global design warnings: "
            + "; ".join(diagnostic_codes)
            + ". Details remain in the inspector."
        )
        warnings.extend("global_design_warning:" + code for code in diagnostic_codes)
    return qualifier, notes, warnings


def build_panels(capability, data):
    if capability not in CAPABILITIES:
        raise ValueError("scrna_plot_capability_unsupported")
    membership, stages_by_unit = _validate(data)
    units, populations, stages = (
        data[name] for name in ("biological_unit_levels", "populations", "stages")
    )
    units = sorted(units, key=lambda unit: stages.index(stages_by_unit[unit]))
    qualifier, annotations, warnings = _design_context(data)
    title_suffix = " · " + qualifier if qualifier else ""
    rows, marks = [], []
    if capability == "scrna_population_cell_counts":
        for item in [*data["profiles"], *data["missing_combinations"]]:
            unit, population, stage = (
                item[name] for name in ("biological_unit", "population", "stage")
            )
            count = item.get("cell_count")
            identity = item.get(
                "profile_id",
                "missing:"
                + str(units.index(unit))
                + ":"
                + str(populations.index(population)),
            )
            x = units.index(unit) + (populations.index(population) - 0.5) * 0.36
            entry = row(
                identity,
                x,
                count,
                entity_kind="declared_population_profile",
                group=population,
                reason=(
                    "Saved population profile absent; cell count unavailable."
                    if "profile_id" not in item
                    else "Saved cell count unavailable."
                )
                if count is None
                else None,
                details={
                    **deepcopy(item),
                    "cell_count": count,
                    "evidence": data["evidence"],
                    "display_order": DISPLAY_ORDER,
                },
                warnings=warnings,
            )
            rows.append(entry)
            if count is not None:
                marks.append(
                    {
                        "type": "rect",
                        "keys": [entry["key"]],
                        "group": population,
                        "x": x - 0.16,
                        "y": 0.0,
                        "width": 0.32,
                        "height": float(count),
                    }
                )
        return [
            panel(
                capability,
                "Observed cells by declared embryo and population" + title_suffix,
                rows,
                marks=marks,
                x_categories=[
                    unit
                    if re.search(
                        r"(?<![A-Za-z0-9])"
                        + re.escape(stages_by_unit[unit])
                        + r"(?![A-Za-z0-9])",
                        unit,
                    )
                    else unit + "\n" + stages_by_unit[unit]
                    for unit in units
                ],
                axes={
                    "x": {
                        "label": "Embryo and stage",
                        "unit": "declared labels",
                        "transform": "identity",
                    },
                    "y": {
                        "label": "Observed count",
                        "unit": "cells",
                        "transform": "identity",
                    },
                },
                annotations=annotations,
                limitations=LIMITATIONS,
            )
        ]
    contrasts = {item["stage"]: item for item in data["contrasts"]}
    for index, unit in enumerate(units):
        stage = stages_by_unit[unit]
        present = [
            population
            for population in populations
            if membership[(unit, population)] is not None
        ]
        paired = len(present) == len(populations)
        group = (
            "Both supplied populations recorded"
            if paired
            else "Population profile absent"
        )
        entry = row(
            unit,
            stages.index(stage),
            index,
            entity_kind="declared_biological_unit",
            group=group,
            details={
                "biological_unit": unit,
                "stage": stage,
                "populations_present": present,
                "populations_absent": [
                    population
                    for population in populations
                    if population not in present
                ],
                "paired_declared_unit": paired,
                "contrast": contrasts[stage],
                "evidence": data["evidence"],
                "display_order": DISPLAY_ORDER,
            },
            warnings=warnings,
        )
        rows.append(entry)
        marks.append(
            {
                "type": "point",
                "keys": [entry["key"]],
                "group": group,
                "x": entry["x"],
                "y": entry["y"],
                "radius": 6.0,
                "shape": "square" if paired else "triangle",
            }
        )
    stage_labels = [
        stage
        + "\n"
        + str(contrasts[stage]["paired_unit_count"])
        + " paired; "
        + (
            qualifier
            if qualifier and qualifier != "result refused"
            else "local criteria met"
            if contrasts[stage]["supported"]
            else "local criteria unmet"
        )
        for stage in stages
    ]
    return [
        panel(
            capability,
            "Saved population-contrast support" + title_suffix,
            rows,
            marks=marks,
            x_categories=stage_labels,
            y_categories=units,
            axes={
                "x": {
                    "label": "Collection stage",
                    "unit": "declared labels",
                    "transform": "identity",
                },
                "y": {
                    "label": "Declared embryo",
                    "unit": "identifier",
                    "transform": "identity",
                },
            },
            annotations=annotations,
            limitations=LIMITATIONS,
        )
    ]
