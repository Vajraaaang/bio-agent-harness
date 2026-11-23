"""Local, source-bound embryonic mouse motor-neuron candidate programs."""

from base64 import b64decode, b64encode
import csv
import io
import re

import numpy as np

from devbench.analysis.checkpoints import digest
from devbench.analysis.rna_scrna_input_contract import _closed_storage
from devbench.analysis.workbench_count_layer_audit import count_layer_blocks
from devbench.core.h5ad_gene_blocks import _axis
from devbench.core.population_data import _annotation, _node

SOURCE = "layers/counts:mouse_motor_programs_v1"
REFERENCES = (
    "https://doi.org/10.1242/dev.127.7.1349",
    "https://pmc.ncbi.nlm.nih.gov/articles/PMC5851594/",
)
LABELS = (
    "Outside candidate screen",
    "Branchio-visceromotor-like",
    "Somatic-motor-like",
    "Conflicting motor or serotonergic evidence",
    "Stored motor annotation, markers unresolved",
    "Motor program outside stored motor annotations",
)
STORED_CANDIDATES = ("Neuron: Motor neuron", "Neuroblast: Motor neuron")
MARKERS = tuple(
    "Isl1 Isl2 Tubb3 Elavl3 Chat Slc18a3 Phox2b Tbx20 Mnx1 Lhx3 Lhx4 "
    "Tph2 Slc6a4 Fev Hoxb1 Egr2 Mafb Hoxb8 Hoxc8 Hoxc9".split()
)


def score_motor_programs(markers, stored_motor):
    def any_marker(*names):
        return np.logical_or.reduce([markers[name] for name in names])

    motor = any_marker("Isl1", "Isl2") & any_marker("Tubb3", "Elavl3", "Chat", "Slc18a3")
    branchial = motor & markers["Phox2b"] & markers["Tbx20"]
    somatic = (
        motor & markers["Mnx1"] & any_marker("Lhx3", "Lhx4", "Slc18a3", "Chat")
    )
    serotonergic = (
        markers["Tph2"] & any_marker("Slc6a4", "Fev")
    )
    conflicts = (branchial & somatic) | ((branchial | somatic) & serotonergic)
    codes = np.zeros(len(stored_motor), dtype=np.uint8)
    codes[stored_motor] = 4
    codes[stored_motor & branchial] = 1
    codes[stored_motor & somatic] = 2
    codes[stored_motor & conflicts] = 3
    codes[~stored_motor & (branchial | somatic) & ~conflicts] = 5
    return codes


def _column(frame, name, n_rows):
    return np.asarray(_annotation(_node(frame, name), n_rows, 16 * 1024**2)[0])


def audit_motor_programs(handle, observed, max_rows):
    _closed_storage(handle)
    cells, features = observed["cells"], observed["genes"]
    if not 1 <= cells <= max_rows or not 1 <= features <= 100_000:
        raise ValueError("Motor program screen exceeds its axis bounds")
    obs, var = _node(handle, "obs"), _node(handle, "var")
    genes = _axis(handle, "var", features)
    by_gene = {symbol: index for index, symbol in enumerate(genes)}
    if set(MARKERS) - by_gene.keys():
        raise ValueError("The complete marker panel must be present in every source")
    gene_ids = _column(var, "gene_ids", features)
    if not any(isinstance(value, str) and value.startswith("ENSMUSG") for value in gene_ids) or any(
        isinstance(value, str) and re.fullmatch(r"ENSG[0-9]+(?:\.[0-9]+)?", value)
        for value in gene_ids
    ):
        raise ValueError("The mouse marker panel needs supporting mouse gene identifiers")
    ages = set(_column(obs, "age", cells))
    if len(ages) != 1:
        raise ValueError("Every observation in a source needs the same recorded embryonic day")
    age = next(iter(ages))
    day = re.fullmatch(r"e([0-9]{1,2})(?:\.5)?", str(age), re.I)
    if day is None:
        raise ValueError("A recorded embryonic day could not be verified")
    stored = np.isin(_column(obs, "celltypes", cells), STORED_CANDIDATES) | np.isin(
        _column(obs, "predicted_labels", cells), STORED_CANDIDATES,
    )
    marker_order = [by_gene[name] for name in MARKERS]
    codes = np.zeros(cells, dtype=np.uint8)
    evidence = np.zeros((len(LABELS), len(MARKERS)), dtype=np.int64)
    entries = 0
    for start, block, stored_entries in count_layer_blocks(handle, observed, max_rows):
        stop = start + block.shape[0]
        detected = block[:, marker_order].toarray() > 0
        markers = dict(zip(MARKERS, detected.T, strict=True))
        block_codes = score_motor_programs(markers, stored[start:stop])
        codes[start:stop] = block_codes
        for code in range(1, len(LABELS)):
            evidence[code] += detected[block_codes == code].sum(axis=0)
        entries += stored_entries
    counts = np.bincount(codes, minlength=len(LABELS))
    return {
        "table_title": "Motor-neuron program evidence",
        "source_ref": SOURCE,
        "columns": ["Candidate program", "Observations", "Percent of all profiles"],
        "rows": [[label, int(count), round(100 * int(count) / cells, 4)]
                 for label, count in zip(LABELS, counts, strict=True)],
        "observed_embryonic_day": int(day[1]),
        "observed_stage": str(age),
        "denominator": cells,
        "missing_count": 0,
        "stored_entries": entries,
        "marker_names": list(MARKERS),
        "marker_positive_counts": evidence.tolist(),
        "row_codes_base64": b64encode(codes).decode("ascii"),
        "references": list(REFERENCES),
        "truncated": False,
    }


def compare_motor_programs(profiles, answers):
    if not 2 <= len(profiles) == len(answers) <= 8:
        raise ValueError("Attach two to eight day-specific AnnData files")
    sources = [
        next((row for row in answer["tables"] if row.get("source_ref") == SOURCE), None)
        if answer.get("sha256") == profile["sha256"] else None
        for profile, answer in zip(profiles, answers, strict=True)
    ]
    completed = sum(source is not None for source in sources)
    steps = [
        {"title": "Verify original AnnData files", "status": "completed",
         "detail": f"Checked the identities of {len(profiles)} attached originals."},
        {"title": "Stream counts and score marker programs",
         "status": "completed" if completed == len(sources) else "partial",
         "detail": f"Scored all observation rows in {completed} of {len(sources)} files."},
        {"title": "Compare candidates by embryonic day", "status": "blocked",
         "detail": "A complete day-by-day result requires verified screens for every file."},
        {"title": "Review anatomical and replicate evidence", "status": "needs evidence",
         "detail": "Hindbrain origin, cranial-nucleus assignments, and biological replicates require independent evidence."},
    ]
    if completed != len(sources):
        return steps, [], "The full-file motor-neuron screen completed for only part of the source set.", "partial"
    pairs = sorted(
        zip(sources, profiles, strict=True), key=lambda pair: pair[0]["observed_embryonic_day"]
    )
    sources, profiles = zip(*pairs, strict=True)
    days = [source["observed_embryonic_day"] for source in sources]
    if len(set(days)) != len(days):
        raise ValueError("The local comparison needs one original per recorded embryonic day")
    for source, profile in zip(sources, profiles, strict=True):
        codes = np.frombuffer(b64decode(source["row_codes_base64"], validate=True), dtype=np.uint8)
        if (
            len(codes) != source["denominator"] or len(codes) != profile["observed"]["cells"]
            or np.any(codes >= len(LABELS))
            or np.bincount(codes, minlength=len(LABELS)).tolist() != [row[1] for row in source["rows"]]
        ):
            raise ValueError("Motor program labels disagree with the verified source totals")

    labels = [f"Day {day}" for day in days]
    comparison = {
        "table_title": "Motor-neuron candidate programs by recorded day",
        "columns": ["Candidate program", *[label + " (%)" for label in labels],
                    f"{labels[-1]} minus {labels[0]} (pp)"],
        "rows": [[name, *[source["rows"][code][2] for source in sources],
                  round(100 * (sources[-1]["rows"][code][1] / sources[-1]["denominator"]
                               - sources[0]["rows"][code][1] / sources[0]["denominator"]), 4)]
                 for code, name in enumerate(LABELS) if code],
        "source_ref": "motor_program_group_proportions",
        "truncated": False,
    }
    denominator = {
        "table_title": "Motor program comparison denominators",
        "columns": ["Recorded group", "Attached original", "Observations screened", "Stored stage"],
        "rows": [[label, profile["filename"], source["denominator"], source["observed_stage"]]
                 for label, source, profile in zip(labels, sources, profiles, strict=True)],
        "source_ref": "motor_program_group_denominators",
        "truncated": False,
    }
    marker_set = ["Phox2b", "Tbx20", "Mnx1", "Hoxb1", "Egr2", "Mafb", "Hoxc8", "Hoxc9"]
    support = {
        "table_title": "Marker detection in each candidate group",
        "columns": ["Recorded day", "Candidate program", "Group observations", *marker_set],
        "rows": [[label, LABELS[code], source["rows"][code][1],
                  *[source["marker_positive_counts"][code][MARKERS.index(marker)] for marker in marker_set]]
                 for label, source in zip(labels, sources, strict=True)
                 for code in range(1, len(LABELS)) if source["rows"][code][1]],
        "source_ref": "motor_program_marker_positive_observations",
        "truncated": False,
    }
    steps[2].update(status="completed", detail="Compared marker-supported candidates and unresolved groups across recorded days.")
    return steps, [comparison, denominator, support], (
        f"Screened {sum(source['denominator'] for source in sources):,} observations across "
        f"{len(days)} original files. Provisional labels describe molecular programs. "
        "Hindbrain origin and cranial-nucleus identity need anatomical or reference evidence."
    ), "completed"


def motor_program_csv(record, result_ref, source_sha256):
    """Export a local, exact source member after verifying the retained result."""
    if (
        record.get("status") != "completed"
        or not record.get("request", {}).get("motor_program_comparison")
        or (record.get("description_operation") or {}).get("result_ref") != result_ref
    ):
        raise KeyError("The requested motor program result is unavailable")
    matches = [
        turn["content"] for turn in record["turns"]
        if turn["kind"] == "dataset_answer"
        and (turn["content"].get("binding") or {}).get("result_ref") == result_ref
    ]
    if len(matches) != 1:
        raise ValueError("Motor program result has no exact answer binding")
    answer = matches[0].copy()
    binding = dict(answer.pop("binding"), result_ref=None)
    if digest({"binding": binding, "answer": answer}) != result_ref:
        raise ValueError("Motor program result differs from its receipt")
    source = next(
        (profile for profile in record["source_profiles"] if profile["sha256"] == source_sha256),
        None,
    )
    tables = [table for table in answer["tables"] if table.get("source_ref") == SOURCE
              and table.get("source_sha256") == source_sha256]
    if source is None or len(tables) != 1:
        raise KeyError("The requested source is outside this motor program result")
    table = tables[0]
    codes = b64decode(table["row_codes_base64"], validate=True)
    if (
        len(codes) != table["denominator"] or len(codes) > 100_000
        or len(codes) != source["observed"]["cells"]
        or any(code >= len(LABELS) for code in codes)
    ):
        raise ValueError("Motor program labels differ from the source rows")
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["observation_row", "candidate_program", "source_sha256"])
    writer.writerows((row, LABELS[code], source_sha256) for row, code in enumerate(codes))
    return stream.getvalue().encode(), f"day-{table['observed_embryonic_day']}-motor-candidates.csv"
