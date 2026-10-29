"""Local, immutable FASTQ and additive-design admission before any engine call.

Checks here establish input identity and necessary design conditions. They do
not prove donor identity, library strandedness, or biological correctness.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import csv
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat

import numpy as np

from devharness_core.units import UNKNOWN_UNIT_MARKERS, biological_identity_audit
from .fastq_analysis_policy import FastqAdmissionError, validate_analysis_policy

FORMAT = "devbench.fastq-preflight/1"
PINS = {
    "workflow": "nf-core/rnaseq",
    "workflow_version": "3.26.0",
    "workflow_revision": "e7ca46272c8f9d5ceee3f71759f4ba551d3217a4",
    "nextflow_version": "25.10.4",
    "aligner": "star_salmon",
    "extra_star_align_args": "--quantMode TranscriptomeSAM GeneCounts",
    "count_artifact": "STAR.ReadsPerGene.out.tab",
    "count_semantics": "direct_integer_gene_counts",
    "count_model": "PyDESeq2",
    "count_model_version": "0.5.4",
    "count_model_revision": "4426e4db990db1c511de3b1b9b7a514989663dad",
}
MAX_TABLE_BYTES = 5_000_000
MAX_SAMPLES = 4096
MAX_INPUT_BYTES = 512 * 1024**3
MAX_FILE_BYTES = 128 * 1024**3
MAX_REFERENCE_DECODED_BYTES = 32 * 1024**3
MAX_REFERENCE_LINE_BYTES = 1024 * 1024
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
SAMPLE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\Z")
UNKNOWN = set(UNKNOWN_UNIT_MARKERS) | {"not available", "not known", "not_known", "missing", "na"}
BIOLOGICAL_ALIASES = {
    "biological_unit_id", "donor", "donor_id", "subject", "subject_id", "specimen", "specimen_id",
    "cell", "cell_line", "cell_line_id", "cellline", "cellline_id", "donor_derived_cell_line",
}
MEMBERSHIP_ALIASES = {"sample", "sample_id", "library", "library_id", "barcode", "cell_id", "cell"}
BATCH_ALIASES = {"batch", "batch_id", "sequencing_batch", "sequencing_batch_id", "processing_batch"}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _identifier(value, label):
    if type(value) is not str or not IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must be a simple explicit metadata column identifier")
    return value


def _text(value, label):
    if value is None or (type(value) is str and not value.strip()):
        raise FastqAdmissionError(f"{label} is missing", category="needs_information")
    if type(value) is str and value.strip().lower() in UNKNOWN:
        raise FastqAdmissionError(f"{label} is unknown", category="needs_information")
    if type(value) is not str or not value.strip() or value != value.strip() or len(value) > 512:
        raise ValueError(f"{label} requires a nonempty, unambiguous text value")
    return value


def _local_file(value, label):
    if type(value) is not str or not value or "\x00" in value or "://" in value:
        raise ValueError(f"{label} must be a local canonical absolute file path")
    path = Path(value)
    if any(character.isspace() for character in value):
        raise ValueError(f"{label} path contains whitespace unsupported by the pinned workflow schema")
    if not path.is_absolute():
        raise ValueError(f"{label} must be a local canonical absolute file path")
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or path.resolve(strict=True) != path:
            raise ValueError(f"{label} must be a canonical regular file without symlinks")
    except OSError as exc:
        raise ValueError(f"{label} local file is unavailable") from exc
    if not 0 < info.st_size <= MAX_FILE_BYTES:
        raise ValueError(f"{label} is empty or exceeds the explicit file-byte budget")
    return path


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def _open_regular(path):
    # Nonblocking avoids hanging if an input is replaced by a FIFO after stat.
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("Input is no longer an available regular local file") from exc
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Input must remain a regular file")
        yield stream


def _file_identity(path, kind):
    digest = hashlib.sha256()
    total = 0
    with _open_regular(path) as stream:
        before = os.fstat(stream.fileno())
        while body := stream.read(1024 * 1024):
            total += len(body)
            if total > MAX_FILE_BYTES:
                raise ValueError("Input exceeds the explicit file-byte budget")
            digest.update(body)
        after = os.fstat(stream.fileno())
    if (_signature(before) != _signature(after) or _signature(before) != _signature(path.stat())
            or total != before.st_size):
        raise ValueError("Input changed while preflight was reading it")
    return {"path": str(path), "size_bytes": total, "sha256": digest.hexdigest(), "kind": kind}


def _read_csv(path, label):
    if path.stat().st_size > MAX_TABLE_BYTES:
        raise ValueError(f"{label} exceeds the table-byte budget")
    with _open_regular(path) as stream:
        raw = stream.read(MAX_TABLE_BYTES + 1)
    if len(raw) > MAX_TABLE_BYTES:
        raise ValueError(f"{label} exceeds the table-byte budget")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
        fields = reader.fieldnames
        if (not fields or len(fields) > 128 or len(fields) != len(set(fields))
                or any(type(field) is not str or not IDENTIFIER.fullmatch(field) for field in fields)):
            raise ValueError(f"{label} headers must be distinct simple identifiers")
        result = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"{label} has an inconsistent CSV row")
            result.append(row)
            if len(result) > MAX_SAMPLES * 32:
                raise ValueError(f"{label} exceeds the row budget")
        if not result:
            raise ValueError(f"{label} is empty")
        return result, hashlib.sha256(raw).hexdigest()
    except (UnicodeError, csv.Error) as exc:
        raise ValueError(f"{label} must be a well-formed UTF-8 CSV") from exc


@contextmanager
def _binary_content(path):
    # Only gzip is a supported compressed stream; this never extracts archives.
    with _open_regular(path) as stream:
        if path.name.lower().endswith(".gz"):
            with gzip.GzipFile(fileobj=stream, mode="rb") as decoded:
                yield decoded
        else:
            yield stream


def _check_fastq_prefix(path):
    try:
        with _binary_content(path) as stream:
            record = [stream.readline(16385) for _ in range(4)]
        if (any(not line or len(line) > 16384 or not line.endswith(b"\n") for line in record)
                or not record[0].startswith(b"@") or not record[2].startswith(b"+")
                or not record[1].strip() or len(record[1].strip()) != len(record[3].strip())
                or not re.fullmatch(rb"[ACGTNacgtn]+", record[1].strip())
                or any(byte < 33 or byte > 126 for byte in record[3].strip())):
            raise ValueError("FASTQ first record is malformed or exceeds the bounded read policy")
    except (OSError, EOFError) as exc:
        raise ValueError("FASTQ cannot be read as the declared local stream") from exc


def _reference_lines(path):
    total = 0
    try:
        with _binary_content(path) as stream:
            while line := stream.readline(MAX_REFERENCE_LINE_BYTES + 1):
                total += len(line)
                if len(line) > MAX_REFERENCE_LINE_BYTES or total > MAX_REFERENCE_DECODED_BYTES:
                    raise ValueError("Reference exceeds the decoded-byte or line-byte budget")
                yield line.decode("utf-8").rstrip("\r\n")
    except (OSError, EOFError, UnicodeError) as exc:
        raise ValueError("Reference is not a readable UTF-8 FASTA/GTF stream") from exc


def _validate_reference(fasta, gtf):
    contigs = {}
    current = None
    bases = 0
    for line in _reference_lines(fasta):
        if not line:
            continue
        if line.startswith(">"):
            name = line[1:].split()[0] if line[1:].split() else ""
            if not name or name in contigs or (current is not None and bases == 0):
                raise ValueError("FASTA has empty or duplicate contig identities")
            if current is not None:
                contigs[current] = bases
            contigs[name] = 0
            if len(contigs) > 1_000_000:
                raise ValueError("FASTA exceeds the contig-count budget")
            current, bases = name, 0
        elif current is None or not re.fullmatch(r"[ACGTRYSWKMBDHVNacgtryswkmbdhvn]+", line):
            raise ValueError("FASTA sequence/header structure is invalid")
        else:
            bases += len(line)
    if not contigs or bases == 0:
        raise ValueError("FASTA requires at least one nonempty contig")
    contigs[current] = bases
    exons = 0
    annotation_contigs = set()
    for line in _reference_lines(gtf):
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 9 or fields[0] not in contigs:
            raise ValueError("GTF contigs/columns do not match the supplied FASTA reference")
        try:
            valid_coordinates = 1 <= int(fields[3]) <= int(fields[4]) <= contigs[fields[0]]
        except ValueError:
            valid_coordinates = False
        if not valid_coordinates or fields[6] not in {"+", "-", "."}:
            raise ValueError("GTF coordinates or strand are invalid")
        annotation_contigs.add(fields[0])
        if fields[2] == "exon":
            gene_id = re.search(r'(?:^|;\s*)gene_id\s+"([^";]+)"\s*;', fields[8])
            if gene_id is None or not gene_id.group(1).strip():
                raise ValueError("GTF exon lacks a gene_id for STAR gene counting")
            transcript_id = re.search(r'(?:^|;\s*)transcript_id\s+"([^";]+)"\s*;', fields[8])
            if transcript_id is None or not transcript_id.group(1).strip():
                raise ValueError("GTF exon lacks a transcript_id required by the star_salmon workflow")
            exons += 1
    if not exons:
        raise ValueError("GTF requires annotated exons with gene identities")
    return {"fasta_contig_count": len(contigs), "annotated_contig_count": len(annotation_contigs),
            "exon_count": exons, "assurance": "structural_compatibility_not_reference_scientific_validation"}


def _validate_design(metadata_rows, *, formula, contrast, biological_unit, batch_columns,
                    analysis_scope="bulk", objective="differential_expression", blocking=None,
                    biological_unit_type=None):
    """Freeze an explicit independent-unit categorical additive model and contrast."""
    if analysis_scope != "bulk" or objective != "differential_expression":
        raise FastqAdmissionError("This admission supports bulk differential_expression only", category="unsupported_analysis")
    blocking = [] if blocking is None else blocking
    if type(blocking) is not list or blocking not in ([], [biological_unit]):
        raise ValueError("blocking must be empty or exactly the declared biological_unit")
    paired = bool(blocking)
    if (type(metadata_rows) is not list or not 2 <= len(metadata_rows) <= MAX_SAMPLES
            or any(type(row) is not dict for row in metadata_rows)):
        raise ValueError("Metadata requires a bounded list of sample rows")
    rows = deepcopy(metadata_rows)
    fields = set(rows[0])
    if any(set(row) != fields for row in rows):
        raise ValueError("Metadata rows must contain the same explicit columns")
    if "sample_id" not in fields:
        raise ValueError("Metadata requires sample_id")
    ids = [_text(row["sample_id"], "sample_id") for row in rows]
    if len(ids) != len(set(ids)) or any(not SAMPLE_IDENTIFIER.fullmatch(value) for value in ids):
        raise ValueError("Metadata sample_id values must be distinct safe identifiers")
    unit = _identifier(biological_unit, "biological_unit")
    if unit.lower() in MEMBERSHIP_ALIASES and not (paired and unit.lower() == "cell" and biological_unit_type == "donor_derived_cell_line"):
        raise ValueError("A sample/library/single-cell membership is not an independent biological unit")
    if unit not in fields:
        raise FastqAdmissionError("Declared biological_unit column is missing", category="needs_information")
    identities = [_text(row[unit], "biological_unit") for row in rows]
    if not paired and len(set(identities)) != len(identities):
        raise ValueError("repeated_biological_units:" + unit)
    for column in sorted(name for name in fields if name.lower() in BIOLOGICAL_ALIASES):
        known = [_text(row[column], "biological identity " + column) for row in rows]
        if not paired and len(set(known)) != len(known):
            raise ValueError("repeated_biological_units:" + column)
        if paired:
            identity_units = {}
            for row, identity in zip(rows, known, strict=True):
                identity_units.setdefault(identity, set()).add(row[unit])
            if any(len(units)>1 for units in identity_units.values()):
                raise ValueError("repeated_biological_units:" + column)
    if (type(batch_columns) is not list or len(batch_columns) > 16
            or any(type(column) is not str for column in batch_columns)
            or len(set(batch_columns)) != len(batch_columns)):
        raise ValueError("batch_columns must be an explicit bounded list of distinct columns")
    for column in batch_columns:
        _identifier(column, "batch_columns")
    undeclared = {name for name in fields if name.lower() in BATCH_ALIASES} - set(batch_columns)
    if undeclared:
        raise ValueError("Metadata batch columns require explicit declaration: " + ",".join(sorted(undeclared)))
    if type(formula) is not str or len(formula) > 1024 or not formula.lstrip().startswith("~"):
        raise ValueError("An explicit additive formula is required")
    expression = formula.strip()[1:]
    terms = [term.strip() for term in expression.split("+")]
    if (not 1 <= len(terms) <= 17 or any(not IDENTIFIER.fullmatch(term) for term in terms)
            or len(set(terms)) != len(terms) or "Intercept" in terms):
        raise FastqAdmissionError("Only explicit unique additive categorical formula terms are supported", category="unsupported_analysis")
    if paired and (unit not in terms or unit in batch_columns):
        raise ValueError("Paired formula must include the biological-unit block separately from batches")
    if not set(batch_columns) <= set(terms):
        raise ValueError("The formula must retain every declared batch column")
    if not set(terms) <= fields:
        raise ValueError("A formula term is absent from metadata")
    if (type(contrast) is not dict or set(contrast) != {"factor", "numerator", "denominator"}
            or contrast["factor"] not in terms):
        raise ValueError("An explicit contrast factor in the formula is required")
    factor = contrast["factor"]
    numerator = _text(contrast["numerator"], "contrast numerator")
    denominator = _text(contrast["denominator"], "contrast denominator")
    if numerator == denominator or factor in batch_columns:
        raise ValueError("The contrast requires distinct levels of a non-batch factor")
    original_rows = deepcopy(rows)
    memberships = None
    if paired:
        if biological_unit_type not in {"donor", "subject", "specimen", "donor_derived_cell_line"}:
            raise FastqAdmissionError("Paired analysis requires biological_unit_type", category="needs_information")
        if biological_unit_type == "specimen":
            raise FastqAdmissionError("Paired specimen identity requires clarification of its independent population unit (donor, subject or donor-derived cell line)", category="needs_information", code="specimen_population_identity_required")
        # A declared subject denotes an individual organism. Preserve the
        # requested vocabulary while binding the existing core's canonical type.
        core_unit_type = "organism" if biological_unit_type == "subject" else biological_unit_type
        if not {"library_id", "specimen_id"} <= fields:
            raise FastqAdmissionError("Paired metadata requires explicit library_id and specimen_id memberships", category="needs_information")
        if set(terms) != {factor, unit, *batch_columns} or factor == unit:
            raise FastqAdmissionError("Paired existing core supports condition, declared batches and biological-unit block only", category="unsupported_analysis")
        for column in ("library_id", "specimen_id"):
            for row in rows:
                _text(row[column], column)
        audit = biological_identity_audit(rows, unit, population_unit=core_unit_type,
            library_label="library_id", design_columns=terms)
        if audit["problems"]:
            raise ValueError(";".join(audit["problems"]))
        for column in ("library_id", "specimen_id"):
            seen = {}
            for row in rows:
                identity = _text(row[column], column)
                seen.setdefault(identity, set()).add(row[unit])
            if any(len(units)>1 for units in seen.values()):
                raise ValueError(column + " crosses biological units")
        library_specimens = {}
        for row in rows:
            library_specimens.setdefault(row["library_id"], set()).add(row["specimen_id"])
        if any(len(specimens)>1 for specimens in library_specimens.values()):
            raise ValueError("library crosses specimens")
        groups = {}
        for row in rows:
            groups.setdefault((row[unit], row[factor]), []).append(row)
        if any({level for identity, level in groups if identity == donor} != {numerator,denominator}
               for donor in set(identities)):
            raise ValueError("Paired analysis requires complete_pairs of exactly the requested conditions")
        if len(set(identities)) < 3:
            raise FastqAdmissionError("Existing paired core requires at least three biological units", category="unsupported_analysis")
        rows, memberships = [], []
        for group in groups.values():
            if any(len({row[term] for row in group}) != 1 for term in terms):
                raise ValueError("Technical memberships cross modeled batch/design coordinates")
            rows.append(group[0])
            memberships.append([row["sample_id"] for row in group])
    categories = {}
    for term in terms:
        values = [_text(row[term], "formula term " + term) for row in rows]
        levels = sorted(set(values))
        if len(levels) < 2:
            raise ValueError("Requested formula term has only one constant level: " + term)
        if len(levels) > 128:
            raise ValueError("Formula categorical level budget exceeded")
        categories[term] = levels
    if not {numerator, denominator} <= set(categories[factor]):
        raise ValueError("Requested contrast levels are absent from metadata")
    categories[factor] = [denominator, *[level for level in categories[factor] if level != denominator]]
    replication = {level: len({row[unit] for row in rows if row[factor] == level})
                   for level in (denominator, numerator)}
    if min(replication.values()) < 2:
        raise ValueError("The contrast requires at least two distinct biological replicates per level")
    columns = ["Intercept"]
    vectors = [np.ones(len(rows), dtype=float)]
    for term in terms:
        for level in categories[term][1:]:
            columns.append(f"{term}[{level}]")
            vectors.append(np.asarray([row[term] == level for row in rows], dtype=float))
    matrix = np.column_stack(vectors)
    rank = int(np.linalg.matrix_rank(matrix))
    if rank != matrix.shape[1]:
        raise ValueError("Design rank deficiency/complete confounding: requested terms cannot be removed to force a fit")
    residual_df = len(rows) - rank
    if residual_df < (2 if paired else 1):
        raise ValueError("The full requested design has no residual degrees of freedom")
    condition_number = float(np.linalg.cond(matrix))
    if not math.isfinite(condition_number) or condition_number > 1e8:
        raise ValueError("Design condition-number budget exceeded")
    if paired:
        from devbench.study.biology_tool_io import require_design_condition
        require_design_condition(matrix, condition_number_limit=1000.0)
    vector = [0.0] * len(columns)
    vector[columns.index(f"{factor}[{numerator}]")] = 1.0
    result = {"formula": formula, "contrast": deepcopy(contrast), "terms": terms,
            "batch_columns": list(batch_columns), "categories": categories,
            "sample_ids": ids, "metadata_rows": original_rows, "design_columns": columns,
            "design_matrix": matrix.tolist(), "contrast_vector": vector, "rank": rank,
            "residual_df": residual_df, "condition_number": condition_number,
            "biological_unit": unit, "unit_count": len(set(identities)),
            "replication": replication, "analysis_scope": analysis_scope, "objective": objective,
            "identity_assurance": "supplied_declaration_and_metadata_consistency_not_verified_identity",
            "coding": "categorical_treatment_coding_with_contrast_denominator_baseline"}
    if paired:
        result.update(blocking=list(blocking), biological_unit_type=biological_unit_type,
                      core_biological_unit_type=core_unit_type,
                      biological_unit_type_mapping="declared_subject_as_individual_organism" if biological_unit_type == "subject" else "unchanged_declared_type",
                      modeled_metadata_rows=rows, observation_memberships=memberships,
                      analysis_observation_count=len(rows), count_model_route="registered_bulk_core")
    return result


def validate_design(metadata_rows, **kwargs):
    try:
        return _validate_design(metadata_rows, **kwargs)
    except FastqAdmissionError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise FastqAdmissionError(str(exc), category="invalid_design") from exc


def _preflight_fastq_plan(plan):
    """Validate the entire local request without invoking models or workflows."""
    required = {"samplesheet", "metadata", "reference", "formula", "contrast", "biological_unit",
                "batch_columns", "analysis_scope"}
    optional = {"alpha", "objective", "biological_unit_type", "blocking", "analysis_policy"}
    if type(plan) is not dict:
        raise ValueError("FASTQ plan must be an explicit object")
    missing = required - set(plan)
    if missing:
        raise FastqAdmissionError("Missing required FASTQ plan fields: " + ", ".join(sorted(missing)), category="needs_information")
    if set(plan) - required - optional:
        raise FastqAdmissionError("Unknown/unsupported FASTQ plan fields: " + ", ".join(sorted(set(plan) - required - optional)), category="unsupported_analysis")
    if plan["analysis_scope"] != "bulk":
        raise FastqAdmissionError("Only bulk differential-expression input is supported", category="unsupported_analysis")
    if "biological_unit_type" in plan and plan["biological_unit_type"] not in {
            "donor", "subject", "specimen", "donor_derived_cell_line"}:
        raise ValueError("Declare a biological unit, not a library, barcode, or single cell")
    analysis_policy = validate_analysis_policy(plan.get("analysis_policy"), paired=bool(plan.get("blocking")))
    alpha = plan.get("alpha", 0.05)
    if type(alpha) not in {int, float} or not math.isfinite(alpha) or not 0 < alpha < 1:
        raise ValueError("alpha must be a finite explicit probability strictly between zero and one")
    reference = plan["reference"]
    if type(reference) is not dict or set(reference) != {"fasta", "gtf"}:
        raise ValueError("reference requires exact local fasta and gtf paths")
    sheet = _local_file(plan["samplesheet"], "samplesheet")
    metadata = _local_file(plan["metadata"], "metadata")
    fasta, gtf = (_local_file(reference[key], key) for key in ("fasta", "gtf"))
    input_paths = [(sheet, "samplesheet"), (metadata, "metadata"), (fasta, "fasta"), (gtf, "gtf")]
    initial_signatures = {str(path): _signature(path.stat()) for path, _ in input_paths}
    samples, sheet_sha = _read_csv(sheet, "samplesheet")
    rows, metadata_sha = _read_csv(metadata, "metadata")
    if set(samples[0]) != {"sample", "fastq_1", "fastq_2", "strandedness"}:
        raise ValueError("samplesheet requires sample,fastq_1,fastq_2,strandedness headers")
    seen_paths, lane_settings, sample_ids = set(), {}, []
    if len(initial_signatures) != len(input_paths):
        raise ValueError("Distinct input roles cannot reuse the same file")
    for row in samples:
        sample = _text(row["sample"], "samplesheet sample")
        if not SAMPLE_IDENTIFIER.fullmatch(sample):
            raise ValueError("samplesheet sample must be a safe identifier")
        strandedness = row["strandedness"]
        if strandedness not in {"forward", "reverse", "unstranded"}:
            raise ValueError("Known explicit strandedness is required; automatic/guessed values are refused")
        paired = bool(row["fastq_2"])
        setting = (strandedness, paired)
        if sample in lane_settings and lane_settings[sample] != setting:
            raise ValueError("A sample's lanes cannot mix strandedness or paired-end status")
        if sample not in lane_settings:
            sample_ids.append(sample)
        lane_settings[sample] = setting
        for mate in ("fastq_1", "fastq_2") if paired else ("fastq_1",):
            path = _local_file(row[mate], mate)
            # Match the immutable workflow's assets/schema_input.json exactly
            # for the filename: uncompressed FASTQ is also supported upstream.
            if not re.fullmatch(r"[^\s/]+\.f(?:ast)?q(?:\.gz)?", path.name):
                raise ValueError("FASTQ extension must be .fq, .fastq, .fq.gz, or .fastq.gz")
            if str(path) in seen_paths or str(path) in initial_signatures:
                raise ValueError("FASTQ file is reused/duplicated across samples, lanes, or mates")
            seen_paths.add(str(path))
            initial_signatures[str(path)] = _signature(path.stat())
            input_paths.append((path, "fastq"))
    if len(sample_ids) > MAX_SAMPLES:
        raise ValueError("Sample count exceeds the explicit resource budget")
    if sum(item[2] for item in initial_signatures.values()) > MAX_INPUT_BYTES:
        raise ValueError("Input files exceed the explicit cumulative-byte budget")
    if any("sample_id" not in row for row in rows):
        raise ValueError("Metadata requires sample_id")
    by_id = {row["sample_id"]: row for row in rows}
    if len(by_id) != len(rows) or set(by_id) != set(sample_ids):
        raise ValueError("Metadata must map exactly once to every samplesheet sample")
    design = validate_design([by_id[sample] for sample in sample_ids], formula=plan["formula"],
        contrast=plan["contrast"], biological_unit=plan["biological_unit"], batch_columns=plan["batch_columns"],
        analysis_scope=plan["analysis_scope"], objective=plan.get("objective", "differential_expression"),
        blocking=plan.get("blocking"), biological_unit_type=plan.get("biological_unit_type"))
    identities, read_hashes, hashed_bytes = {}, set(), 0
    for path, kind in input_paths:
        if _signature(path.stat()) != initial_signatures[str(path)]:
            raise ValueError("Input changed during preflight")
        if kind == "fastq":
            _check_fastq_prefix(path)
        if _signature(path.stat()) != initial_signatures[str(path)]:
            raise ValueError("Input changed during preflight")
        identity = _file_identity(path, kind)
        hashed_bytes += identity["size_bytes"]
        if hashed_bytes > MAX_INPUT_BYTES:
            raise ValueError("Input files exceed the explicit cumulative-byte budget")
        if kind == "fastq":
            if identity["sha256"] in read_hashes:
                raise ValueError("Duplicate FASTQ content cannot count as independent samples, lanes, or mates")
            read_hashes.add(identity["sha256"])
        identities[str(path)] = identity
    if identities[str(sheet)]["sha256"] != sheet_sha or identities[str(metadata)]["sha256"] != metadata_sha:
        raise ValueError("CSV input changed during preflight")
    reference_audit = _validate_reference(fasta, gtf)
    for path, _ in input_paths:
        if _signature(path.stat()) != initial_signatures[str(path)]:
            raise ValueError("Input changed during preflight")
    result = {"format": FORMAT, "pins": deepcopy(PINS), "samples": deepcopy(samples),
            "sample_ids": sample_ids, "input_files": list(identities.values()),
            "reference": {"fasta": str(fasta), "gtf": str(gtf)}, "reference_audit": reference_audit,
            "design": design, "alpha": float(alpha), "alpha_source": "plan" if "alpha" in plan else "implementation_default",
            "analysis_scope": "bulk", "objective": design["objective"], "plan_sha256": _digest(plan),
            "biological_unit_type": plan.get("biological_unit_type"),
            "strandedness_assurance": "supplied_declaration_not_independently_verified",
            "fastq_validation": "bounded_first_record_and_full_file_identity_not_read_quality",
            "scientific_correctness": None, "biological_agreement": "not_run",
            "model_execution_authorized": False, "model_sessions": 0}
    if analysis_policy is not None:
        result["analysis_policy"] = analysis_policy
    if design.get("blocking"):
        result["pins"].update(count_model="devbench_existing_bulk_core", count_model_version="registered_bulk_condition_contrast/1",
                              count_model_revision="source_hashes_in_model_provenance")
    return result


def preflight_fastq_plan(plan):
    try:
        return _preflight_fastq_plan(plan)
    except FastqAdmissionError:
        raise
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise FastqAdmissionError(str(exc), category="input_error") from exc


validate_fastq_plan = preflight_fastq_plan
