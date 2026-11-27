"""Evaluate registered measurements and biological-unit designs."""

from __future__ import annotations

import re
from typing import get_args

import numpy as np
import pandas as pd
import patsy

from devbench.core.rna_answerability import Assay, EntryPoint
from devbench.engine.workflow_operations import OperationBlocked

from ._sources import QUOTA, check_time, deadline
from .intake import verify_artifact_identity
from .query import authorized_artifact, table_source

_DESIGN_AXES = {"B1", "B3", "B4", "B6", "B8", "B9", "B10", "B11", "B12"}
_TECHNICAL = {
    "barcode",
    "cell_id",
    "cell",
    "library",
    "library_id",
    "capture",
    "capture_id",
    "observation_id",
    "sample",
    "sample_id",
    "replicate",
    "replicate_id",
}
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_MAX_UNITS = 20_000
_MAX_TERMS = 128


def _formula(formula):
    # Patsy's grammar parses signs, interactions and nesting. Only LookupFactor
    # objects reach dmatrix, so no Python expression or user function is evaluated.
    model = patsy.ModelDesc.from_formula(formula)
    if model.lhs_termlist or not model.rhs_termlist or len(model.rhs_termlist) > 32:
        raise ValueError("Expected a bounded one-sided design formula")
    terms = []
    names = []
    categorical = set()
    for term in model.rhs_termlist:
        members = []
        for factor in term.factors:
            token = factor.name()
            wrapped = re.fullmatch(r"C\(([a-zA-Z_]\w*)\)", token)
            name = wrapped.group(1) if wrapped else token
            if not _IDENTIFIER.fullmatch(name):
                raise ValueError("Use named metadata factors in design formulas")
            if wrapped:
                categorical.add(name)
            names.append(name)
            members.append(name)
        terms.append(members)
    return terms, list(dict.fromkeys(names)), categorical


def _design_frame(ctx, sid, design, names, until):
    artifact = authorized_artifact(ctx, sid, design["metadata_artifact_id"])
    unit = design["biological_unit"]
    sample_columns = [
        c for c in (design.get("capture_column"), design.get("specimen_column")) if c
    ]
    groups = design.get("batch_columns", [])
    columns = list(dict.fromkeys([unit, *names, *groups, *sample_columns]))
    slot = "obs" if artifact["ref"]["format"] in {"h5ad", "h5mu"} else "default"
    units = set()
    parent_maps = {name: {} for name in sample_columns}
    with table_source(ctx, artifact, slot, until) as source:
        if set(columns) - set(source.fields):
            return None, "Declared design columns are missing.", artifact
        for i, values in enumerate(source.rows(columns)):
            if i % 512 == 0:
                check_time(until)
            row = dict(zip(columns, values, strict=True))
            if any(v is None or v == "" for v in values):
                return None, "Missing values prevent a complete design.", artifact
            for key, parents in parent_maps.items():
                if parents.setdefault(row[key], row[unit]) != row[unit]:
                    return (
                        None,
                        "The specimen or capture maps to conflicting biological units.",
                        artifact,
                    )
                if len(parents) > _MAX_UNITS * 8:
                    raise OperationBlocked(QUOTA)
            units.add(tuple(row[n] for n in dict.fromkeys([unit, *names, *groups])))
            if len(units) > _MAX_UNITS:
                raise OperationBlocked(QUOTA)
    keys = list(dict.fromkeys([unit, *names, *groups]))
    frame = pd.DataFrame.from_records(sorted(units, key=str), columns=keys)
    return frame, None, artifact


def _make_matrix(terms, frame, categorical):
    levels = {}
    for name in frame:
        if name in categorical:
            levels[name] = frame[name].nunique()
        else:
            numeric = pd.to_numeric(frame[name], errors="coerce")
            if numeric.notna().all():
                frame[name] = numeric
                levels[name] = 1
            else:
                categorical.add(name)
                levels[name] = frame[name].nunique()
    # Reject excessive expansion before allocating a one-hot matrix.
    if (
        sum(
            int(np.prod([levels[name] for name in term], dtype=object))
            for term in terms
        )
        > _MAX_TERMS
    ):
        raise OperationBlocked(QUOTA)
    model = patsy.ModelDesc(
        [],
        [
            patsy.Term(
                [
                    patsy.LookupFactor(n, force_categorical=n in categorical)
                    for n in term
                ]
            )
            for term in terms
        ],
    )
    return patsy.dmatrix(model, frame, NA_action="raise", return_type="dataframe")


def _contrast_checks(design, frame, formula_names):
    contrast = design.get("contrast")
    if not contrast:
        return {}, None
    factor = contrast["factor"]
    if factor not in formula_names:
        return {}, "The contrast factor is absent from the fitting formula."
    groups = frame.assign(_group=frame[factor].map(str)).groupby(
        "_group", observed=True
    )
    by_group = {
        str(k): int(n) for k, n in groups[design["biological_unit"]].nunique().items()
    }
    arms = [contrast["numerator"], contrast["denominator"]]
    if len(set(arms)) != 2 or not set(arms) <= by_group.keys():
        return by_group, "The requested contrast levels are absent or identical."
    if min(by_group[k] for k in arms) < 2:
        return by_group, "The contrast lacks independent biological replication."
    unit = design["biological_unit"]
    if frame.groupby(unit).size().max() > 1 and unit not in formula_names:
        return by_group, "Repeated biological units require a within-unit design."
    return by_group, None


def _diagnostics(ctx, sid, design, until):
    terms, names, categorical = _formula(design["formula"])
    frame, problem, artifact = _design_frame(ctx, sid, design, names, until)
    if problem or frame is None or frame.empty:
        return None, problem or "The design table is empty.", []
    contrast = design.get("contrast") or {}
    unit = design["biological_unit"]
    categorical.update([unit, *design.get("batch_columns", [])])
    if contrast:
        categorical.add(contrast["factor"])
    counts, reason = _contrast_checks(design, frame, names)
    # Declared nuisance variables remain in the diagnostic even when an input
    # formula omits them; dropping a confounded batch cannot identify an effect.
    assessed = [
        *terms,
        *([b] for b in design.get("batch_columns", []) if b not in names),
    ]
    design_matrix = _make_matrix(assessed, frame.copy(), categorical)
    columns = design_matrix.shape[1]
    rank = int(np.linalg.matrix_rank(np.asarray(design_matrix)))
    residual = int(len(frame) - rank)
    diagnostics = {
        "full_rank": rank == columns,
        "rank": rank,
        "columns": columns,
        "residual_df": residual,
        "biological_units_per_group": counts,
        "confounded_terms": list(
            dict.fromkeys([*names, *design.get("batch_columns", [])])
        )
        if rank < columns
        else [],
    }
    if not diagnostics["full_rank"] or residual <= 0:
        reason = "The design is rank deficient or lacks residual degrees of freedom."
    missing = []
    provenance = artifact["detail"].get("provenance_declaration", {})
    declared = (provenance.get("biological_unit") or "").strip().casefold()
    source_unit = declared.removesuffix("_id")
    if not design.get(
        "forensics_verified"
    ) or source_unit != unit.casefold().removesuffix("_id"):
        missing.append(
            "Independent biological-unit identities and their source declaration."
        )
    if unit.casefold() in _TECHNICAL:
        missing.append(
            "A verified donor, subject, animal, or independent biological-unit key."
        )
    return diagnostics, reason, missing


def _recommendations(ctx, args, axis, records):
    from .registry import list_kernels_and_methods

    filters = {
        "session_id": args.get("session_id"),
        "axis_b_class": axis,
        "assay": args["assay"],
        "lane": "tier1_curated",
        "kernel_ids": args.get("candidate_kernels") or None,
    }
    representations = {r["ref"]["representation_class"] for r in records}
    if not any(r["detail"].get("count_1_verified") for r in records):
        representations -= {"count_1_raw_counts", "aggregated_unit_counts"}
    result = []
    for kernel in list_kernels_and_methods(ctx, filters)["kernels"]:
        needs = kernel["required_representations"]
        assumptions = [f"Required representation: {n}." for n in needs]
        if needs and not set(needs) <= representations:
            continue
        result.append(
            {
                "kernel_id": kernel["kernel_id"],
                "assumptions": assumptions,
                "runtime_ready": kernel["runtime_ready"],
                "calibration_status": kernel["calibration_status"],
            }
        )
    return result


def _inputs(ctx, args):
    sid = args.get("session_id")
    if sid:
        ctx.session(sid)
        ctx.require_public(sid)
    design = args.get("design")
    records = []
    if (args.get("artifact_ids") or design) and not sid:
        raise OperationBlocked("input_scope_violation")
    ids = list(args.get("artifact_ids", []))
    if design and design["metadata_artifact_id"] not in ids:
        ids.append(design["metadata_artifact_id"])
    for aid in ids:
        record = authorized_artifact(ctx, sid, aid)
        verify_artifact_identity(ctx, record)
        records.append(record)
    return records


def _conflicted_design(design, axis, records):
    return (design or axis in _DESIGN_AXES) and any(
        record["detail"].get("donor_conflict_halted")
        or (
            record["ref"]["representation_class"] == "count_1_raw_counts"
            and record["detail"]
            .get("count_1_forensics", {})
            .get("suspected_transformed")
        )
        for record in records
    )


def _answerability_status(problem, missing, axis):
    if problem:
        status, reason = "refused", problem
    elif missing:
        status, reason = (
            "awaiting_information",
            "Additional evidence is required for this question.",
        )
    elif axis == "B_OPEN":
        status, reason = (
            "tier2_exploratory_only",
            "The open question requires a governed exploratory plan.",
        )
    else:
        status, reason = (
            "ready",
            "The registered measurements, design and runtime support this analysis.",
        )
    return status, reason


def check_answerability(ctx, args):
    until = deadline()
    sid = args.get("session_id")
    design = args.get("design")
    axis = args.get("axis_b_class") or "B_OPEN"
    records = _inputs(ctx, args)
    missing = []
    if args["assay"] not in {*get_args(Assay), "open", "custom"}:
        missing.append("A supported assay or an explicit open/custom assay.")
    if args["entry_point"] not in {*get_args(EntryPoint), "open", "custom"}:
        missing.append(
            "A supported entry point or an explicit open/custom entry point."
        )
    diagnostics = problem = None
    if design:
        diagnostics, problem, needed = _diagnostics(ctx, sid, design, until)
        missing.extend(needed)
    elif axis in _DESIGN_AXES:
        missing.append("A biological-unit design and estimable contrast.")
    if _conflicted_design(design, axis, records):
        problem = (
            "Unresolved donor or count-provenance conflict prevents "
            "biological-unit inference."
        )
    kernels = _recommendations(ctx, args, axis, records)
    if axis != "B_OPEN" and not records:
        missing.append("Registered measurement artifacts for the specified question.")
    if axis != "B_OPEN" and not any(k["runtime_ready"] for k in kernels):
        missing.append(
            "A compatible kernel, verified input representation and available runtime."
        )
    status, reason = _answerability_status(problem, missing, axis)
    ctx.require_public(sid)
    return {
        "status": status,
        "reason": reason,
        "epistemic_state": "E_ABSTAIN" if problem else "E0",
        "axis_b_class": axis,
        "design_diagnostics": diagnostics,
        "missing_evidence": missing,
        "recommended_kernels": kernels,
    }
