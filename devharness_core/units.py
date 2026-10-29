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
