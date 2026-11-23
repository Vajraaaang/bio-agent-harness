"""Shared declared identity levels; labels alone never prove independence."""
BIOLOGICAL_UNIT_COLUMNS = ("biological_unit_id", "donor_id", "subject_id", "specimen_id")
UNKNOWN_UNIT_MARKERS = ("", "unknown", "unverified", "not_available", "not_known", "not known",
                        "missing", "na", "n/a", "none", "null", "nan")
CANDIDATE_BIOLOGICAL_UNIT_COLUMNS = BIOLOGICAL_UNIT_COLUMNS + (
    "donor", "subject", "cell", "cell_line", "cell_line_id")


def biological_identity_audit(rows, population_unit_label, *, population_unit=None,
                              library_label=None, design_columns=()):
    """Detect declared units that split recognizable biological identities.

    Names are warning evidence, not proof of donor identity. In particular a
    repeated ``cell`` may be a donor-derived line; the caller must explicitly
    declare that unit and any repeated-measures design. Repeated identities
    nested inside one declared unit are retained as evidence rather than
    rejected merely because technical memberships repeat.
    """
    rows = list(rows)
    problems, findings, repeated = [], [], []
    if str(population_unit).strip().lower() in {
            "cell", "cells", "single_cell", "single-cell", "barcode", "barcodes",
            "library", "libraries", "technical_library", "technical_libraries"}:
        problems.append("biological_unit_is_membership")
    columns = set().union(*(row.keys() for row in rows)) if rows else set()
    # Column spelling cannot change the scientific identity audit. Preserve the
    # actual spelling in evidence so the refused source field remains traceable.
    names = sorted(name for name in columns if isinstance(name, str))
    candidates = list(dict.fromkeys([
        *(name for alias in CANDIDATE_BIOLOGICAL_UNIT_COLUMNS for name in names if name.casefold() == alias.casefold()),
        *([population_unit_label] if isinstance(population_unit_label, str) else [])]))
    for name in candidates:
        if name not in columns:
            continue
        values = [row.get(name) for row in rows]
        if any(not isinstance(value, str) or value.strip().lower() in UNKNOWN_UNIT_MARKERS for value in values):
            problems.append("biological_unit_identity_unknown_" + name)
            findings.append({"column": name, "status": "incomplete_identity"})
            continue
        groups = {}
        for row, value in zip(rows, values):
            groups.setdefault(value.strip(), []).append(row.get(population_unit_label))
        if any(len(members) > 1 for members in groups.values()):
            repeated.append(name)
        split = [identity for identity, members in groups.items() if len(set(members)) > 1]
        if split:
            problems.append("repeated_biological_unit_" + name)
        findings.append({"column": name, "identity_count": len(groups),
                         "split_identity_count": len(split),
                         "status": "split_across_declared_units" if split else "nested_in_declared_unit"})
    membership_findings = []
    if library_label is not None:
        if not isinstance(library_label, str) or library_label not in columns:
            problems.append("library_identity_column_missing")
        else:
            memberships = {}
            coordinates = [name for name in design_columns if isinstance(name, str) and name in columns]
            for row in rows:
                identity = row.get(library_label)
                if not isinstance(identity, str) or identity.strip().lower() in UNKNOWN_UNIT_MARKERS:
                    problems.append("library_identity_unknown")
                    continue
                memberships.setdefault(identity.strip(), set()).add(tuple(row.get(name) for name in coordinates))
            crossing = sum(len(values) > 1 for values in memberships.values())
            if crossing:
                problems.append("library_crosses_design_coordinates")
            membership_findings.append({"column": library_label, "design_columns": list(coordinates),
                "library_count": len(memberships), "crossing_library_count": crossing,
                "status": "crosses_design_coordinates" if crossing else "constant_within_library"})
    return {"problems": list(dict.fromkeys(problems)),
            "repeated_identity_columns": repeated, "identity_findings": findings,
            "library_coordinate_findings": membership_findings}


def declared_unit_counts(metadata):
    """Return complete identity-level counts and incomplete column names."""
    counts, incomplete = {}, []
    for name in BIOLOGICAL_UNIT_COLUMNS:
        if name not in metadata:
            continue
        values = metadata[name].astype("string").str.strip()
        observed = values[values.notna() & ~values.str.lower().isin(UNKNOWN_UNIT_MARKERS)]
        if len(observed) != len(metadata):
            incomplete.append(name)
        else:
            counts[name] = int(observed.nunique())
    return counts, incomplete


def validate_expression_selection(provenance, metadata, *, biological_unit_column):
    """Keep expression-derived hypotheses conditional unless a split is supported.

    Sample splitting checks declared biological units, never library or cell
    barcodes. It does not establish external provenance or label independence.
    Data thinning requires its own independently verified partition and count
    model, which the open fitters cannot verify from caller-supplied metadata.
    """
    if provenance is None:
        return None
    if (type(provenance) is not dict or provenance.get("source") not in ("hvg", "lineage")
            or provenance.get("strategy") not in ("exploratory", "sample_split", "data_thinning")):
        raise ValueError("invalid_expression_selection_provenance")
    source, strategy = provenance["source"], provenance["strategy"]
    if strategy == "data_thinning":
        raise ValueError("data_thinning_requires_verified_independent_count_partition")
    if strategy == "exploratory":
        if set(provenance) != {"source", "strategy"}:
            raise ValueError("invalid_exploratory_selection_provenance_fields")
        return {"source": source, "strategy": strategy, "scope": "exploratory_same_data_selection"}
    if set(provenance) != {"source", "strategy", "biological_unit_column", "discovery_unit_ids"}:
        raise ValueError("invalid_sample_split_provenance_fields")
    if (not biological_unit_column or biological_unit_column not in metadata
            or provenance["biological_unit_column"] != biological_unit_column):
        raise ValueError("sample_split_requires_declared_biological_unit_column")
    discovery = provenance["discovery_unit_ids"]
    if (type(discovery) is not list or not 1 <= len(discovery) <= 100_000 or any(
            type(unit) is not str or len(unit) > 200 or unit.strip().lower() in UNKNOWN_UNIT_MARKERS
            for unit in discovery)):
        raise ValueError("sample_split_requires_bounded_known_discovery_units")
    discovery_units = {unit.strip() for unit in discovery}
    if len(discovery_units) != len(discovery):
        raise ValueError("sample_split_requires_unique_discovery_units")
    inference = metadata[biological_unit_column].astype("string").str.strip()
    if inference.isna().any() or inference.str.lower().isin(UNKNOWN_UNIT_MARKERS).any():
        raise ValueError("sample_split_requires_known_inference_units")
    inference_units = set(inference)
    if discovery_units & inference_units:
        raise ValueError("discovery_and_inference_biological_units_overlap")
    return {"source": source, "strategy": strategy, "scope": "conditional_on_declared_disjoint_units",
            "biological_unit_column": biological_unit_column, "discovery_unit_count": len(discovery_units),
            "inference_unit_count": len(inference_units), "independent_provenance_verified": False}
