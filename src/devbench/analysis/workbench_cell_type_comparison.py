"""Exact, local comparisons of bounded stored AnnData annotation histograms."""

from fractions import Fraction


def _histogram(profile, answer):
    tables = [
        row for row in answer["tables"]
        if row.get("source_ref") == "obs/celltypes" and not row.get("truncated")
    ]
    if answer.get("sha256") != profile["sha256"] or len(tables) != 1:
        return None
    table = tables[0]
    total = profile["observed"].get("cells")
    missing = table["missing_count"]
    counts = {label: number for label, number, _ in table["rows"]}
    if (
        type(total) is not int or total <= 0 or table["denominator"] != total
        or type(missing) is not int or missing < 0
        or len(counts) != len(table["rows"])
        or any(type(value) is not int or value <= 0 for value in counts.values())
        or not counts or sum(counts.values()) + missing != total
    ):
        return None
    return counts, total, missing


def compare_cell_types(profiles, answers, declared_days):
    if not 2 <= len(profiles) == len(answers) <= 8:
        raise ValueError("Attach between two and eight AnnData files to compare")
    if declared_days and (
        len(declared_days) != len(profiles) or sorted(set(declared_days)) != declared_days
    ):
        raise ValueError("Record one increasing day per file in attachment order")
    headings = (
        [f"Day {day}" for day in declared_days]
        if declared_days else [profile["filename"] for profile in profiles]
    )
    histograms = [_histogram(profile, answer) for profile, answer in zip(profiles, answers, strict=True)]
    checked = all(item is not None for item in histograms)
    steps = [
        {
            "title": "Verify attached sources",
            "status": "completed",
            "detail": f"Inspected {len(profiles)} original files and checked their identities.",
        },
        {
            "title": "Count stored cell types by file",
            "status": "completed" if checked else "partial",
            "detail": "Counted exact celltypes labels and checked counts against each file’s denominator.",
        },
        {
            "title": "Compare cell-type proportions",
            "status": "completed" if checked else "blocked",
            "detail": "Computed percentage-point changes." if checked else
            "Every file needs a complete, source-verified celltypes histogram.",
        },
        {
            "title": "Review biological replication",
            "status": "needs evidence",
            "detail": "Recorded age and sample-type metadata are shown when available. Independent biological units have not been established.",
        },
    ]
    if not checked:
        return steps, [], "A matched comparison requires complete stored cell-type counts for every file.", "partial"

    counts, totals, missing = zip(*histograms, strict=True)
    labels = set().union(*counts)
    rows = []
    for label in labels:
        fractions = [Fraction(counter.get(label, 0), total) for counter, total in zip(counts, totals, strict=True)]
        rows.append((label, fractions, fractions[-1] - fractions[0]))
    rows.sort(key=lambda row: (-abs(row[2]), row[0]))
    def percent(value):
        return round(float(100 * value), 4)

    first, last = headings[0], headings[-1]
    changes = {
        "table_title": "Cell-type proportions by " + ("declared day" if declared_days else "attached file"),
        "columns": ["Stored label", *[heading + " (%)" for heading in headings], f"{last} minus {first} (pp)"],
        "rows": [[label, *[percent(value) for value in values], percent(delta)]
                 for label, values, delta in rows[:64]],
        "truncated": len(rows) > 64,
        "source_ref": "source_verified_celltypes_histograms",
    }
    support = {
        "table_title": "Comparison denominators",
        "columns": ["Comparison group", "Attached file", "All observations", "Missing cell-type labels"],
        "rows": [[heading, profile["filename"], total, n_missing]
                 for heading, profile, total, n_missing in zip(headings, profiles, totals, missing, strict=True)],
        "truncated": False,
        "source_ref": "verified_obs_counts",
    }
    biggest = rows[0]
    message = (
        f"Across {len(profiles)} files, the largest observed change from {first} to {last} "
        f"was {biggest[0]} ({percent(biggest[2]):+.2f} percentage points). "
        "Each proportion uses all observations in that file as its denominator."
    )
    return steps, [changes, support], message, "completed"
