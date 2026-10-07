"""Shared declared identity levels; labels alone never prove independence."""
BIOLOGICAL_UNIT_COLUMNS = ("biological_unit_id", "donor_id", "subject_id", "specimen_id")
UNKNOWN_UNIT_MARKERS = ("", "unknown", "unverified", "not_available", "n/a", "none", "null", "nan")


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
