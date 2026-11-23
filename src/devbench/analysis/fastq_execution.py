"""Local pinned Nextflow quantification and whole-family count-model execution.

STAR GeneCounts, emitted by nf-core/rnaseq, is the direct integer-count handoff.
Salmon estimates are deliberately not silently rounded or fitted without length
correction. This adapter launches no provider/model selector and grants no
scientific or clinical approval. Every attempt has a separate retained outcome.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import platform
import re
import selectors
import shutil
import signal
import stat
import subprocess
import threading
import time
import uuid
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory

WORKFLOW = {'name': 'nf-core/rnaseq', 'version': '3.26.0',
            'commit': 'e7ca46272c8f9d5ceee3f71759f4ba551d3217a4'}
NEXTFLOW_VERSION = '25.10.4'
NEXTFLOW_DIST_SHA256 = '60db869a7618783e3ee60183a2b0c6c50ffc67221eabec60800b904991786aaa'
MINIMAL_PROFILE = {
    'name': 'minimal-counts',
    'purpose': 'direct gene counts and explicit differential expression',
    'retained_checks': ['FASTQ lint', 'STAR alignment logs', 'upstream minimum uniquely mapped percentage'],
    'omitted_checks': ['read trimming', 'FastQC/RustQC', 'RSeQC', 'Qualimap', 'Preseq',
                       'dupRadar', 'biotype QC', 'DESeq2 exploratory QC', 'MultiQC',
                       'duplicate marking', 'StringTie assembly', 'bigWig tracks'],
    'qc_assurance': 'limited execution profile; comprehensive RNA-seq QC not established',
    'quantification_note': 'upstream alignment-based Salmon still executes; its estimates are not the DE input',
}
STANDARD_PROFILE = {
    'name': 'standard', 'purpose': 'pinned upstream RNA-seq QC, direct gene counts and explicit DE',
    'retained_checks': ['pinned upstream default trimming and QC', 'FASTQ lint', 'STAR alignment logs',
                        'upstream minimum uniquely mapped percentage'],
    'omitted_checks': [],
    'qc_assurance': 'upstream QC artifacts require scientific assessment; execution is not QC acceptance',
    'quantification_note': 'upstream alignment-based Salmon still executes; its estimates are not the DE input',
}
QC_SALMON_PROFILE = {
    'name': 'qc-salmon',
    'purpose': 'local read QC and Salmon transcript quantification',
    'retained_checks': [
        'pinned upstream default trimming and read QC',
        'FASTQ lint',
        'Salmon mapping summaries',
    ],
    'omitted_checks': ['STAR alignment and alignment-dependent QC'],
    'qc_assurance': 'upstream QC artifacts require scientific assessment',
    'quantification_note': 'fractional counts and sample-specific effective lengths',
}
SALMON_COUNT_SOURCE = 'Salmon_tximport_estimated_counts_with_length_offsets'
MAX_SALMON_TRANSCRIPTS = 500_000
MAX_SALMON_ENTRIES = 4_000_000
MAX_TRACE_BYTES = 256_000
MAX_TRACE_TASKS = 500
MAX_ARTIFACT_BYTES = 100_000_000
MAX_LOG_BYTES = 10_000_000
MAX_GENES = 100_000
SUMMARY_ROWS = ('N_unmapped', 'N_multimapping', 'N_noFeature', 'N_ambiguous')
STRAND_COLUMN = {'unstranded': 1, 'forward': 2, 'reverse': 3}


def preflight_fastq_plan(plan, **options):
    from .fastq_contract import preflight_fastq_plan as validate
    return validate(plan, **options)


def check_count_model():
    from .fastq_count_model import check_count_model as check
    return check()


def fit_count_matrix(counts_path, design, output_dir, *, alpha, analysis_policy=None):
    from .fastq_count_model import fit_count_matrix as fit
    return fit(counts_path, design, output_dir, alpha=alpha,
               **({'analysis_policy': analysis_policy} if analysis_policy is not None else {}))


def _check_model_for_design(design):
    if design.get('blocking'):
        from .fastq_paired_model import check_paired_model
        return check_paired_model()
    return check_count_model()


def _check_cancelled(event):
    if event is not None and event.is_set():
        from devharness_core.watchdog import WorkerCancelled
        raise WorkerCancelled('FASTQ execution cancelled by its owning coordinator')


def _absolute(path, *, exists=False):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or path.is_symlink() or path.resolve() != path:
        raise ValueError('Use canonical absolute paths without symlinks')
    if exists and not path.exists():
        raise ValueError('Required path is absent: ' + str(path))
    return path


def _identity(path, *, maximum=None):
    path = _absolute(path, exists=True)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or (maximum is not None and info.st_size > maximum):
        raise ValueError('Artifact must be a bounded regular file: ' + str(path))
    digest = hashlib.sha256()
    total = 0
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            total += len(block)
            if maximum is not None and total > maximum:
                raise ValueError('Artifact changed beyond byte budget during read')
            digest.update(block)
    after = path.stat()
    if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or total != info.st_size:
        raise ValueError('Artifact changed while its identity was read')
    return {'path': str(path), 'size_bytes': total, 'sha256': digest.hexdigest()}


def _write_json(path, value):
    path = _absolute(path)
    temporary = path.with_name(path.name + '.partial')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _read_json(path, maximum=1_000_000):
    path = _absolute(path, exists=True)
    _identity(path, maximum=maximum)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON field')
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Non-finite JSON value')))


def _attempt(output_root):
    root = _absolute(output_root)
    root.mkdir(parents=True, exist_ok=True)
    directory = root / ('fastq-' + uuid.uuid4().hex)
    directory.mkdir(exist_ok=False)
    return directory


def _initial_record(directory):
    return {'format': 'devbench.fastq-attempt/1', 'attempt_dir': str(directory),
            'status': 'preparing', 'reason': None, 'failure_stage': None,
            'workflow': dict(WORKFLOW), 'engine_launch_count': 0, 'engine_launch_attempt_count': 0,
            'engine_session_id': None,
            'execution_profile': None,
            'engine_execution': 'not_run', 'count_model_execution': 'not_run',
            'model_execution_authorized': False, 'model_sessions': 0,
            'scientific_correctness': None, 'scientific_trial_passed': False,
            'biological_agreement': 'not_run', 'numerical_replay': 'not_run',
            'clinical_release_authorized': False, 'artifacts': {}}


def _finish(directory, record):
    path = directory / 'result.json'
    _write_json(path, record)
    return {'status': record['status'], 'attempt_dir': str(directory), 'result_manifest': str(path),
            'reason': record.get('reason'), 'engine_launch_count': record['engine_launch_count'],
            'scientific_correctness': record['scientific_correctness']}


def record_input_refusal(error, output_root):
    """Retain CLI plan-read failures without entering a runtime or a selector."""
    directory = _attempt(output_root)
    record = _initial_record(directory)
    record.update(status='refused', failure_stage='plan_input', reason=str(error)[:4000])
    return _finish(directory, record)


def _verify_input_files(frozen):
    for expected in frozen['input_files']:
        actual = _identity(expected['path'])
        if any(actual[key] != expected[key] for key in ('path', 'size_bytes', 'sha256')):
            raise ValueError('Input changed after preflight: ' + expected['path'])


def _quantified_samples(frozen):
    """Lane rows are memberships; the workflow merges them to one sample."""
    by_id = {}
    for row in frozen['samples']:
        previous = by_id.get(row['sample'])
        if previous is not None and previous['strandedness'] != row['strandedness']:
            raise ValueError('Lanes of one sample have conflicting strandedness')
        by_id[row['sample']] = row
    if set(by_id) != set(frozen['sample_ids']) or len(frozen['sample_ids']) != len(by_id):
        raise ValueError('Frozen sample identities do not match the samplesheet lanes')
    return [by_id[identity] for identity in frozen['sample_ids']]


def merge_star_gene_counts(quantification_dir, samples, output_path):
    """Merge the complete STAR-emitted gene family using declared strand columns.

    Exactly the four leading STAR summary rows are excluded. All-zero genes and
    unestimated downstream genes remain part of the emitted family. Mismatched
    genes, extra samples, malformed counts and unsafe paths fail before output.
    """
    quantification_dir = _absolute(quantification_dir, exists=True)
    folder = _absolute(quantification_dir / 'star_salmon' / 'log', exists=True)
    output_path = _absolute(output_path)
    if output_path.exists():
        raise ValueError('Count output already exists')
    if not isinstance(samples, list) or not 1 <= len(samples) <= 4096:
        raise ValueError('Use a bounded explicit sample family')
    expected = {}
    for sample in samples:
        name = sample.get('sample')
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}', name)
                or name + '.ReadsPerGene.out.tab' in expected or sample.get('strandedness') not in STRAND_COLUMN):
            raise ValueError('Distinct safe samples and known strandedness are required')
        expected[name + '.ReadsPerGene.out.tab'] = sample
    observed = {path.name for path in folder.glob('*.ReadsPerGene.out.tab')}
    if observed != set(expected):
        raise ValueError('STAR count outputs do not match the complete declared sample family')
    genes = None
    columns = []
    sources = []
    total_bytes = 0
    for filename, sample in expected.items():
        path = folder / filename
        identity = _identity(path, maximum=MAX_ARTIFACT_BYTES)
        total_bytes += identity['size_bytes']
        if total_bytes > MAX_ARTIFACT_BYTES:
            raise ValueError('STAR count-family byte budget exceeded; no family subsetting is allowed')
        sample_genes, counts = [], []
        with path.open(encoding='utf-8', newline='') as stream:
            reader = csv.reader(stream, delimiter='\t', strict=True)
            for index, row in enumerate(reader):
                if len(row) != 4 or any(not re.fullmatch(r'0|[1-9][0-9]*', value) for value in row[1:]):
                    raise ValueError('STAR GeneCounts requires exactly three nonnegative integer count columns')
                values = [int(value) for value in row[1:]]
                if any(value > 2**63 - 1 for value in values):
                    raise ValueError('STAR count value exceeds integer budget')
                if index < 4:
                    if row[0] != SUMMARY_ROWS[index]:
                        raise ValueError('STAR count summary rows are missing or reordered')
                    continue
                gene = row[0]
                if (not gene or len(gene) > 300 or gene in SUMMARY_ROWS or gene.strip() != gene
                        or any(ord(char) < 32 for char in gene)):
                    raise ValueError('STAR gene identity is malformed')
                sample_genes.append(gene)
                counts.append(values[STRAND_COLUMN[sample['strandedness']] - 1])
                if len(sample_genes) > MAX_GENES:
                    raise ValueError('Full emitted family exceeds gene budget; no truncation is allowed')
        if not sample_genes or len(set(sample_genes)) != len(sample_genes):
            raise ValueError('STAR must emit a nonempty distinct gene family')
        if genes is None:
            genes = sample_genes
        elif genes != sample_genes:
            raise ValueError('STAR outputs differ in gene identities or order')
        if _identity(path, maximum=MAX_ARTIFACT_BYTES) != identity:
            raise ValueError('STAR count table changed during merge')
        columns.append(counts)
        sources.append({**identity, 'sample': sample['sample'], 'strandedness': sample['strandedness'],
                        'count_column_1_based': STRAND_COLUMN[sample['strandedness']] + 1})
    temporary = output_path.with_name(output_path.name + '.partial')
    try:
        with temporary.open('x', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream, delimiter='\t', lineterminator='\n')
            writer.writerow(['gene_id', *[sample['sample'] for sample in samples]])
            for index, gene in enumerate(genes):
                writer.writerow([gene, *[column[index] for column in columns]])
            stream.flush()
            os.fsync(stream.fileno())
        _identity(temporary, maximum=MAX_ARTIFACT_BYTES)
        temporary.replace(output_path)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise
    return {**_identity(output_path, maximum=MAX_ARTIFACT_BYTES), 'gene_count': len(genes),
            'sample_count': len(samples), 'gene_ids_sha256': hashlib.sha256(json.dumps(genes, separators=(',', ':')).encode()).hexdigest(),
            'count_semantics': 'STAR_GeneCounts_direct_integer', 'sources': sources,
            'excluded_summary_rows': list(SUMMARY_ROWS), 'workflow': dict(WORKFLOW)}


def _pinned_tx2gene(reference):
    if (
        type(reference) is not dict
        or set(reference) != {"path", "sha256"}
        or type(reference["path"]) is not str
        or type(reference["sha256"]) is not str
        or not re.fullmatch(r"[a-f0-9]{64}", reference["sha256"])
    ):
        raise ValueError("qc-salmon requires an explicit tx2gene path and SHA-256 pin")
    identity = _identity(reference["path"], maximum=5_000_000)
    if identity["sha256"] != reference["sha256"]:
        raise ValueError("Pinned tx2gene map changed or differs from its SHA-256")
    return {**identity, "kind": "tx2gene"}


def _salmon_inputs(input_root, tx2gene):
    if input_root is None:
        raise ValueError("qc-salmon requires a separate local input root")
    root = _absolute(input_root, exists=True)
    if not root.is_dir() or root in {Path(root.anchor), Path.home().resolve()}:
        raise ValueError("qc-salmon requires a dedicated local input directory")
    if type(tx2gene) is not dict or type(tx2gene.get("path")) is not str:
        raise ValueError("qc-salmon requires an explicit tx2gene path and SHA-256 pin")
    if not _absolute(tx2gene["path"]).is_relative_to(root):
        raise ValueError("tx2gene map lies outside the local input root")
    identity = _pinned_tx2gene(tx2gene)
    _read_tx2gene(identity)
    return identity


def _bound_salmon_rows(identity):
    from .fastq_contract import _open_regular

    digest = hashlib.sha256()
    remaining = identity["size_bytes"]
    with _open_regular(Path(identity["path"])) as stream:
        while raw := stream.readline(4097):
            remaining -= len(raw)
            if remaining < 0 or len(raw) > 4096:
                raise ValueError("Salmon input changed or exceeds its row bound")
            digest.update(raw)
            yield next(csv.reader([raw.decode("utf-8")], delimiter="\t", strict=True))
    if remaining != 0 or digest.hexdigest() != identity["sha256"]:
        raise ValueError("Pinned Salmon input changed while reading")


def _read_tx2gene(identity):
    mapping = {}
    reader = _bound_salmon_rows(identity)
    if next(reader, None) != ["transcript_id", "gene_id"]:
        raise ValueError(
            "Pinned tx2gene TSV requires transcript_id and gene_id headers"
        )
    for row in reader:
        if (
            len(row) != 2
            or any(
                not value or len(value) > 200 or any(ord(c) < 32 for c in value)
                for value in row
            )
            or row[0] in mapping
            or len(mapping) >= MAX_SALMON_TRANSCRIPTS
        ):
            raise ValueError(
                "Pinned tx2gene map contains invalid or duplicate transcript IDs"
            )
        mapping[row[0]] = row[1]
    if not mapping:
        raise ValueError("Pinned tx2gene map is empty")
    return mapping


def _salmon_sample(path, mapping, gene_index, cumulative_limit):
    import numpy as np

    identity = _identity(path, maximum=cumulative_limit)
    counts, abundances, weighted, lengths = np.zeros((4, len(gene_index)))
    seen: set[str] = set()
    reader = _bound_salmon_rows(identity)
    if next(reader, None) != ["Name", "Length", "EffectiveLength", "TPM", "NumReads"]:
        raise ValueError("Expected exact Salmon quant.sf columns")
    for row in reader:
        if len(row) != 5 or row[0] not in mapping or row[0] in seen:
            raise ValueError(
                "Salmon transcripts must have distinct exact pinned tx2gene mappings"
            )
        transcript = row[0]
        seen.add(transcript)
        size, length, abundance, count = map(float, row[1:])
        if not all(map(math.isfinite, (size, length, abundance, count))) or not (
            size > 0 and length > 0 and 0 <= abundance <= 1_000_001 and count >= 0
        ):
            raise ValueError(
                "Salmon quant.sf requires finite counts and positive lengths"
            )
        gene = gene_index[mapping[transcript]]
        counts[gene] += count
        abundances[gene] += abundance
        weighted[gene] += abundance * length
        lengths[gene] += length
    if not seen:
        raise ValueError("Salmon quant.sf is empty")
    if not all(
        np.isfinite(values).all() for values in (counts, abundances, weighted, lengths)
    ):
        raise ValueError("Salmon gene summaries exceed the numeric range")
    return identity, seen, counts, weighted, abundances, lengths


def merge_salmon_gene_counts(quantification_dir, sample_ids, tx2gene, output_dir):
    """Summarize the full observed transcript family with tximport length semantics."""
    import numpy as np

    root = _absolute(Path(quantification_dir) / "salmon", exists=True)
    destination = _absolute(output_dir, exists=True)
    if (
        type(sample_ids) is not list
        or not 1 <= len(sample_ids) <= 4096
        or any(
            type(name) is not str
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", name)
            for name in sample_ids
        )
        or len(set(sample_ids)) != len(sample_ids)
    ):
        raise ValueError("Use a bounded distinct Salmon sample family")
    observed = {
        path.name
        for path in root.iterdir()
        if (path / "quant.sf").exists() or (path / "quant.sf").is_symlink()
    }
    if observed != set(sample_ids):
        raise ValueError(
            "Salmon outputs do not match the complete declared sample family"
        )

    mapping_identity = _pinned_tx2gene(tx2gene)
    mapping = _read_tx2gene(mapping_identity)
    genes = sorted(set(mapping.values()))
    if len(genes) > MAX_GENES or len(genes) * len(sample_ids) > MAX_SALMON_ENTRIES:
        raise ValueError("Salmon count matrix exceeds the gene-by-sample bound")
    indices = {gene: index for index, gene in enumerate(genes)}
    counts = np.empty((len(genes), len(sample_ids)))
    effective_lengths = np.empty_like(counts)
    unweighted = np.zeros(len(genes))
    sources = []
    inventory: set[str] = set()
    total_bytes = 0
    for column, sample in enumerate(sample_ids):
        path = _absolute(root / sample / "quant.sf", exists=True)
        identity, seen, sample_counts, weighted, abundances, lengths = _salmon_sample(
            path, mapping, indices, MAX_ARTIFACT_BYTES - total_bytes
        )
        if column and inventory != seen:
            raise ValueError("Salmon samples have different transcript inventories")
        inventory = seen
        total_bytes += identity["size_bytes"]
        sources.append({"sample": sample, **identity})
        counts[:, column] = sample_counts
        effective_lengths[:, column] = np.divide(
            weighted, abundances, out=np.full(len(genes), np.nan), where=abundances > 0
        )
        unweighted += lengths / len(sample_ids)
    transcript_counts = np.zeros(len(genes), dtype=int)
    for name in inventory:
        transcript_counts[indices[mapping[name]]] += 1
    included = np.flatnonzero(transcript_counts)
    _salmon_missing_lengths(effective_lengths, unweighted, transcript_counts, included)
    provenance = {
        "transcript_count": len(inventory),
        "gene_count": len(included),
        "sample_ids": sample_ids,
        "tx2gene": mapping_identity,
        "quant_files": sources,
    }
    matrices = {"count_matrix": counts, "effective_lengths": effective_lengths}
    return _write_salmon_quant(destination, genes, included, matrices, provenance)


def _salmon_missing_lengths(effective_lengths, unweighted, transcript_counts, included):
    import numpy as np

    for row in included:
        lengths = effective_lengths[row]
        valid = np.isfinite(lengths)
        # Match tximport's zero-abundance policy across samples.
        if not valid.all():
            lengths[~valid] = (
                math.exp(float(np.log(lengths[valid]).mean()))
                if valid.any()
                else unweighted[row] / transcript_counts[row]
            )
        if not np.isfinite(lengths).all() or (lengths <= 0).any():
            raise ValueError(
                "Salmon gene effective lengths must be finite and positive"
            )


def _write_salmon_quant(destination, genes, included, matrices, provenance):
    names = {
        "count_matrix": "salmon_gene_counts.tsv",
        "effective_lengths": "salmon_gene_lengths.tsv",
        "salmon_quant_provenance": "salmon_quant_provenance.json",
    }
    if any(
        (destination / name).exists() or (destination / name).is_symlink()
        for name in names.values()
    ):
        raise ValueError("Salmon quantification artifacts already exist")
    with TemporaryDirectory(dir=destination, prefix=".salmon-merge-") as temporary:
        stage = Path(temporary)
        for key, matrix in matrices.items():
            with (stage / names[key]).open("x", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
                writer.writerow(["gene_id", *provenance["sample_ids"]])
                writer.writerows(
                    [genes[index], *matrix[index].tolist()] for index in included
                )
        artifacts = {
            key: _identity(stage / names[key], maximum=MAX_ARTIFACT_BYTES)
            for key in matrices
        }
        record = {
            "format": "devbench.salmon-gene-quantification/1",
            "count_source": SALMON_COUNT_SOURCE,
            "counts_from_abundance": "no",
            "length_estimator": "TPM_weighted_effective_length",
            "zero_abundance_length_policy": (
                "geometric_mean_over_expressed_samples_or_mean_of_transcript_lengths"
            ),
            **provenance,
            "artifacts": {
                key: {**item, "path": str(destination / names[key])}
                for key, item in artifacts.items()
            },
        }
        _write_json(stage / names["salmon_quant_provenance"], record)
        artifacts["salmon_quant_provenance"] = _identity(
            stage / names["salmon_quant_provenance"], maximum=MAX_ARTIFACT_BYTES
        )
        for name in names.values():
            (stage / name).rename(destination / name)
    return {
        key: {**item, "path": str(destination / names[key])}
        for key, item in artifacts.items()
    }


def read_nextflow_trace(path: Path) -> dict:
    """Return observed tasks only; a live trace cannot tell us the final DAG size."""
    path = _absolute(path)
    empty: dict[str, object] = {
        "completed": 0,
        "total": 0,
        "tasks": [],
        "truncated": False,
    }
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        return empty
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Nextflow trace must be a regular file")
        content = stream.read(MAX_TRACE_BYTES + 1)
    truncated = len(content) > MAX_TRACE_BYTES
    # A writer may be appending its last record. Ignore incomplete lines.
    lines = content[:MAX_TRACE_BYTES].split(b"\n")[:-1]
    if not lines:
        return {**empty, "truncated": truncated}
    fields = lines[0].decode("utf-8").rstrip("\r").split("\t")
    required = ("task_id", "process", "tag", "status", "exit", "realtime")
    if len(set(fields)) != len(fields) or not set(required) <= set(fields):
        raise ValueError("Nextflow trace lacks the required task fields")
    tasks: dict[int, dict] = {}
    for line in lines[1 : MAX_TRACE_TASKS + 1]:
        columns = line.decode("utf-8").rstrip("\r").split("\t")
        if len(columns) != len(fields):
            raise ValueError("Malformed Nextflow trace row")
        row = dict(zip(fields, columns, strict=True))
        if (
            not re.fullmatch(r"[0-9]{1,12}", row["task_id"])
            or not re.fullmatch(r"[A-Z_]{1,32}", row["status"])
            or any(
                len(row[key]) > 512
                or any(ord(c) < 32 or ord(c) == 127 for c in row[key])
                for key in ("process", "tag", "realtime")
            )
            or row["exit"] not in {"", "-"}
            and not re.fullmatch(r"-?[0-9]{1,10}", row["exit"])
        ):
            raise ValueError("Invalid Nextflow trace task fields")
        task_id = int(row["task_id"])
        tasks[task_id] = {
            "task_id": task_id,
            "process": row["process"],
            "tag": row["tag"],
            "status": row["status"],
            "exit": int(row["exit"]) if row["exit"] not in {"", "-"} else None,
            "realtime": row["realtime"] if row["realtime"] not in {"", "-"} else None,
        }
    return {
        "completed": sum(
            task["status"] in {"COMPLETED", "CACHED"} for task in tasks.values()
        ),
        "total": len(tasks),
        "tasks": list(tasks.values()),
        "truncated": truncated or len(lines) - 1 > MAX_TRACE_TASKS,
    }


def _process_environment(cwd):
    # No provider tokens, cloud credentials or caller Nextflow options are passed
    # into this numerical workflow. A fresh NXF_HOME isolates user config/cache.
    environment = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'JAVA_HOME', 'TMPDIR') if key in os.environ}
    environment.update(NXF_HOME=str(cwd / 'nextflow-home'), NXF_ASSETS=str(cwd / 'nextflow-assets'),
                       NXF_VER=NEXTFLOW_VERSION, NXF_ANSI_LOG='false')
    return environment


def _terminate(process):
    # Descendants may retain stdout after the leader exits. The process group
    # belongs to this start_new_session launch and still needs termination.
    errors = []
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError as error:
        errors.append('SIGTERM: ' + str(error))
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError as error:
        errors.append('SIGKILL: ' + str(error))
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        errors.append('Process leader did not exit after termination attempts')
    return errors


def _execute_command(command, *, cwd, log_path, timeout_sec, on_started=None, cancellation_event=None):
    """Run argv without a shell, with bounded retained logs and process deadline."""
    cwd, log_path = _absolute(cwd, exists=True), _absolute(log_path)
    started, total, retained = time.monotonic(), 0, 0
    timed_out = False
    termination_errors = []
    _check_cancelled(cancellation_event)
    process = subprocess.Popen(command, cwd=cwd, env=_process_environment(cwd),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        if on_started is not None:
            on_started(process.pid)
        with log_path.open('xb') as log:
            while selector.get_map():
                _check_cancelled(cancellation_event)
                if time.monotonic() - started > timeout_sec and not timed_out:
                    timed_out = True
                    termination_errors.extend(_terminate(process))
                events = selector.select(timeout=.1)
                for key, _ in events:
                    block = os.read(key.fileobj.fileno(), 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(block)
                    piece = block[:max(0, MAX_LOG_BYTES - retained)]
                    log.write(piece)
                    retained += len(piece)
                # A descendant must not keep our pipe alive beyond the deadline.
                if time.monotonic() - started > timeout_sec + 3:
                    timed_out = True
                    termination_errors.extend(_terminate(process))
                    break
            while process.poll() is None:
                _check_cancelled(cancellation_event)
                if time.monotonic() - started >= timeout_sec:
                    timed_out = True
                    termination_errors.extend(_terminate(process))
                    break
                time.sleep(.05)
            log.flush()
            os.fsync(log.fileno())
    except BaseException:
        _terminate(process)
        raise
    finally:
        selector.close()
        process.stdout.close()
    return {'exit_code': process.returncode, 'timed_out': timed_out,
            'log_truncated': total > MAX_LOG_BYTES, 'duration_seconds': round(time.monotonic() - started, 3),
            'termination_errors': termination_errors}


def _nextflow_identity(executable, attempt, *, expected_sha256=None):
    candidate = executable if executable is not None else shutil.which('nextflow')
    if candidate is None:
        raise ValueError('Pinned Nextflow executable is unavailable')
    path = Path(candidate)
    if not path.is_absolute():
        raise ValueError('Nextflow executable must be an absolute path')
    path = path.resolve(strict=True)
    identity = _identity(path, maximum=100_000_000)
    original_path = str(path)
    if expected_sha256 is not None:
        if (not isinstance(expected_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_sha256)
                or identity['sha256'] != expected_sha256):
            raise ValueError('Nextflow executable digest differs from its frozen runtime pin')
        # Execute verified private bytes, not a mutable external pathname.
        destination = attempt / 'nextflow-runtime'
        with path.open('rb') as source, destination.open('xb') as target:
            shutil.copyfileobj(source, target, 1024 * 1024)
        destination.chmod(0o500)
        retained = _identity(destination, maximum=100_000_000)
        if retained['sha256'] != expected_sha256:
            raise ValueError('Nextflow executable changed while retaining its pinned bytes')
        path, identity = destination, retained
    if not os.access(path, os.X_OK):
        raise ValueError('Nextflow executable is not executable')
    log = attempt / 'nextflow-version.log'
    result = _execute_command([str(path), '-version'], cwd=attempt, log_path=log, timeout_sec=30)
    body = log.read_text(encoding='utf-8', errors='replace')
    versions = re.findall(r'(?im)^\s*(?:N E X T F L O W\s+)?(?:nextflow\s+)?version\s+([0-9]+\.[0-9]+\.[0-9]+)(?:\s|$)', body)
    if result['exit_code'] != 0 or result['timed_out'] or versions != [NEXTFLOW_VERSION]:
        raise ValueError('Installed Nextflow must report exactly version ' + NEXTFLOW_VERSION)
    return {**identity, 'version': NEXTFLOW_VERSION, 'version_log': _identity(log), 'source_path': original_path}


def _groovy_string(value):
    return "'" + str(value).replace('\\', '\\\\').replace("'", "\\'").replace('\r', '\\r').replace('\n', '\\n') + "'"


def _runtime_profile():
    machine = platform.machine().lower()
    if machine in {'arm64', 'aarch64'}:
        return 'docker,arm64'
    if machine in {'x86_64', 'amd64'}:
        return 'docker'
    raise ValueError('The pinned local runtime supports only x86_64 and arm64 hosts')


def _resources(value, timeout_sec):
    value = {'cpus': 2, 'memory_gb': 6} if value is None else value
    if (not isinstance(value, dict) or set(value) != {'cpus', 'memory_gb'}
            or type(value['cpus']) is not int or not 1 <= value['cpus'] <= 64
            or type(value['memory_gb']) is not int or not 1 <= value['memory_gb'] <= 1024):
        raise ValueError('Declare bounded integer cpus (1..64) and memory_gb (1..1024)')
    return {**value, 'timeout_sec': timeout_sec, 'concurrent_tasks': 1,
            'scope': 'execution_caps_not_a_capacity_guarantee'}


def _execution_profile(name):
    profiles = {
        profile['name']: profile
        for profile in (STANDARD_PROFILE, MINIMAL_PROFILE, QC_SALMON_PROFILE)
    }
    if not isinstance(name, str) or name not in profiles:
        raise ValueError('Select standard, minimal-counts or qc-salmon')
    return json.loads(json.dumps(profiles[name]))


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        from devharness_core.watchdog import HarnessResourceInterrupt
        raise HarnessResourceInterrupt('FASTQ-to-DE total deadline exhausted before the next phase')
    return remaining


def _count_model_worker(counts_path, design, output_dir, alpha, analysis_policy=None):
    return fit_count_matrix(counts_path, design, output_dir, alpha=alpha,
                           **({'analysis_policy': analysis_policy} if analysis_policy is not None else {}))


def _run_count_model(counts_path, design, output_dir, *, alpha, deadline, resources, diagnostics,
                     analysis_policy=None, cancellation_event=None):
    from devharness_core.watchdog import cancel_workers_on, run_sandboxed, worker_limits
    maximum = resources['memory_gb'] * 1024
    cancellation = cancel_workers_on(cancellation_event) if cancellation_event is not None else nullcontext()
    with cancellation, worker_limits(deadline=deadline, max_mem_mb=maximum, scratch_dir=Path(output_dir).parent):
        return run_sandboxed(_count_model_worker,
            args=(str(counts_path), design, str(output_dir), alpha, analysis_policy),
            max_mem_mb=maximum, timeout_sec=_remaining(deadline), diagnostics=diagnostics)


def _cleanup_containers(attempt):
    """Stop only this attempt's labeled Docker containers after a failed launch."""
    docker = shutil.which('docker')
    if docker is None:
        return {'status': 'unavailable', 'reason': 'Docker unavailable for owned-container cleanup'}
    docker = str(Path(docker).resolve())
    label = 'devbench.fastq.attempt=' + attempt.name
    cleanup_id = uuid.uuid4().hex
    log = attempt / ('container-list-' + cleanup_id + '.log')
    result = _execute_command([docker, 'ps', '-aq', '--filter', 'label=' + label],
        cwd=attempt, log_path=log, timeout_sec=10)
    if result['exit_code'] != 0 or result['timed_out'] or result['log_truncated']:
        return {'status': 'failed', 'stage': 'list', 'process': result}
    ids = log.read_text().splitlines()
    if len(ids) > 256 or any(not re.fullmatch(r'[0-9a-f]{12,64}', value) for value in ids):
        return {'status': 'failed', 'reason': 'Unexpected labeled container identity list'}
    if not ids:
        return {'status': 'complete', 'matched_container_count': 0, 'label': label}
    stopped = _execute_command([docker, 'stop', '--time', '5', *ids],
        cwd=attempt, log_path=attempt / ('container-stop-' + cleanup_id + '.log'), timeout_sec=30)
    return {'status': 'complete' if stopped['exit_code'] == 0 and not stopped['timed_out'] else 'failed',
            'matched_container_count': len(ids), 'container_ids': ids, 'label': label, 'process': stopped}


def reconcile_fastq_runtime(events, expected_work_dir):
    """Reconcile only a dead coordinator's identity-bound runtime, never restart it.

    The owning MethodService must first hold its existing abandoned run lease.
    A missing/reused process identity remains uncertain; absence of an event is
    not proof that a launch effect never happened.
    """
    import psutil
    root = _absolute(expected_work_dir)
    if not isinstance(events, list) or len(events) > 64:
        raise ValueError('Use bounded retained runtime binding events')
    paths = set()
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get('attempt_dir'), str):
            raise ValueError('Malformed owned runtime binding event')
        path = _absolute(event['attempt_dir'])
        if path.parent != root or not re.fullmatch(r'fastq-[0-9a-f]{32}', path.name):
            raise ValueError('Runtime binding does not name an owned FASTQ attempt')
        paths.add(path)
    if len(paths) != 1:
        return {'status': 'uncertain', 'process_stopped': None, 'reason': 'No unique retained runtime identity'}
    attempt = paths.pop()
    starts = [event for event in events if event.get('event') == 'engine_started']
    stopped, reason = None, 'No retained engine process identity'
    if len(starts) == 1:
        event = starts[0]
        pid, created = event.get('pid'), event.get('process_create_time')
        if type(pid) is not int or pid <= 1 or type(created) not in {int, float}:
            reason = 'Incomplete engine process identity'
        else:
            try:
                process = psutil.Process(pid)
                if process.create_time() != created:
                    reason = 'Process identity was reused; no signal sent'
                elif Path(process.cwd()).resolve() != attempt or os.getpgid(pid) != pid:
                    reason = 'Process is not the retained owned group; no signal sent'
                else:
                    descendants = [(child, child.create_time()) for child in process.children(recursive=True)]
                    os.killpg(pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=2)
                    except psutil.TimeoutExpired:
                        os.killpg(pid, signal.SIGKILL)
                        process.wait(timeout=2)
                    for child, birth in descendants:
                        try:
                            if child.create_time() == birth and child.status() != psutil.STATUS_ZOMBIE:
                                child.kill()
                        except psutil.NoSuchProcess:
                            pass
                    psutil.wait_procs([child for child, _ in descendants], timeout=1)
                    # An unobserved survivor is uncertainty, not permission to
                    # restart or scan unrelated host process command lines.
                    try:
                        os.killpg(pid, 0)
                    except ProcessLookupError:
                        stopped, reason = True, 'Identity-bound process group terminated'
                    else:
                        stopped, reason = None, 'Retained process group still exists; cleanup unverified'
            except psutil.NoSuchProcess:
                # The leader (or a child inspected above) can disappear while
                # its process group still runs. Never equate that with cleanup.
                try:
                    os.killpg(pid, 0)
                except ProcessLookupError:
                    stopped, reason = True, 'Retained process group no longer exists'
                except PermissionError:
                    stopped, reason = None, 'Retained process group cannot be inspected'
                else:
                    stopped, reason = None, 'Process identity disappeared but its group still exists'
            except (psutil.AccessDenied, psutil.TimeoutExpired, OSError) as error:
                reason = 'Process cleanup unverified: ' + type(error).__name__
    cleanup = _cleanup_containers(attempt)
    _recover_salmon_model(attempt, events, cleanup)
    return {'status': 'reconciled' if stopped is True and cleanup.get('status') == 'complete' else 'uncertain',
            'process_stopped': stopped, 'reason': reason, 'attempt_dir': str(attempt),
            'container_cleanup': cleanup, 'execution_restarted': False}


def _recover_salmon_model(attempt, events, cleanup):
    mounted = [event for event in events if event.get("event") == "salmon_model_launch"]
    if mounted:
        from devbench.engine.mounted_kernel_oci import recover_mounted_container

        try:
            if len(mounted) != 1:
                raise ValueError("No unique Salmon model launch identity")
            bound = mounted[0]
            proof = recover_mounted_container(
                attempt / "count-model" / "mounted",
                request_sha=bound["request_sha"],
                image_id=bound["image_id"],
                attempt_id=bound["model_attempt_id"],
            )
            cleanup["salmon_model"] = proof
            if proof.get("cleanup_confirmed") is not True:
                cleanup["status"] = "failed"
        except Exception as error:
            cleanup.update(
                status="failed",
                salmon_model={
                    "cleanup_confirmed": False,
                    "error": type(error).__name__,
                },
            )


def _prepare_engine(frozen, attempt, resources, execution_profile):
    samplesheet = attempt / 'samplesheet.csv'
    with samplesheet.open('x', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['sample', 'fastq_1', 'fastq_2', 'strandedness'], lineterminator='\n')
        writer.writeheader()
        writer.writerows({key: row[key] for key in writer.fieldnames} for row in frozen['samples'])
    quantification = attempt / 'quantification'
    minimal = _execution_profile(execution_profile)['name'] == 'minimal-counts'
    params = {'input': str(samplesheet), 'fasta': frozen['reference']['fasta'], 'gtf': frozen['reference']['gtf'],
              'outdir': str(quantification), 'aligner': 'star_salmon',
              'extra_star_align_args': '--quantMode TranscriptomeSAM GeneCounts',
              'custom_config_base': '', 'igenomes_ignore': True,
              'skip_pseudo_alignment': True, 'skip_bbsplit': True,
              'skip_alignment': False, 'skip_quantification_merge': False,
              'skip_qc': minimal, 'skip_trimming': minimal, 'skip_markduplicates': minimal,
              'skip_stringtie': minimal, 'skip_bigwig': minimal, 'skip_multiqc': minimal,
              'remove_ribo_rna': False, 'with_umi': False, 'gffread_transcript_fasta': True,
              'save_merged_fastq': False, 'skip_linting': False}
    if execution_profile == 'qc-salmon':
        params.pop('extra_star_align_args')
        params.pop('aligner')
        params.update(
            skip_alignment=True, skip_pseudo_alignment=False, pseudo_aligner='salmon',
        )
    _write_json(attempt / 'params.json', params)
    runtime = _groovy_string(attempt / 'engine-runtime.json')
    config = """// Local policy for a fixed workflow revision.
process.executor = 'local'
process.maxRetries = 0
process.errorStrategy = 'terminate'
process.resourceLimits = [cpus: CPU_LIMIT, memory: MEMORY_LIMIT.GB, time: TIME_LIMIT.sec]
executor.queueSize = 1
docker.enabled = true
docker.runOptions = CONTAINER_LABEL
singularity.enabled = false
conda.enabled = false
wave.enabled = false
workflow.onComplete = {
    new File(RUNTIME_PATH).text = groovy.json.JsonOutput.toJson([
        workflow: workflow.manifest.name,
        commit_id: workflow.commitId,
        nextflow_version: workflow.nextflow.version.toString(),
        session_id: workflow.sessionId.toString(),
        success: workflow.success,
        exit_status: workflow.exitStatus
    ])
}
""".replace('RUNTIME_PATH', runtime).replace('CPU_LIMIT', str(resources['cpus'])).replace(
        'MEMORY_LIMIT', str(resources['memory_gb'])).replace('TIME_LIMIT', str(resources['timeout_sec'])).replace(
        'CONTAINER_LABEL',
        _groovy_string(
            '--pids-limit=256 --label devbench.fastq.attempt=' + attempt.name
            + (' --network none' if execution_profile == 'qc-salmon' else '')
        ),
    )
    if execution_profile == 'qc-salmon':
        config += "trace.fields = 'task_id,process,tag,status,exit,realtime'\n"
    (attempt / 'local.config').write_text(config, encoding='utf-8')
    return quantification


def _verify_runtime(attempt):
    runtime = _read_json(attempt / 'engine-runtime.json')
    if (not isinstance(runtime, dict) or runtime.get('workflow') != WORKFLOW['name']
            or runtime.get('commit_id') != WORKFLOW['commit'] or runtime.get('nextflow_version') != NEXTFLOW_VERSION
            or runtime.get('success') is not True or type(runtime.get('exit_status')) is not int
            or runtime['exit_status'] != 0):
        raise ValueError('Engine completion does not attest the pinned workflow, runtime and successful execution')
    session = runtime.get('session_id')
    try:
        if str(uuid.UUID(session)) != session:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Engine did not retain a valid Nextflow session identity') from None
    return runtime


def _salmon_model_options(frozen):
    import numpy as np

    from rna_kernels.mounted_model import compile_mounted_model

    design = frozen["design"]
    policy = frozen.get("analysis_policy")
    if policy is not None and set(policy) != {"format"}:
        raise ValueError(
            "Salmon length-offset DE cannot execute STAR count model analysis policies"
        )
    unit = {
        "subject": "organism",
        "donor": "donor",
        "donor_derived_cell_line": "donor_derived_cell_line",
    }.get(frozen.get("biological_unit_type"))
    if unit is None:
        raise ValueError(
            "Salmon DE requires an explicit donor, subject, or donor-derived cell-line unit"
        )
    contrast = design["contrast"]
    factor, numerator, denominator = (
        contrast[key] for key in ("factor", "numerator", "denominator")
    )
    levels = design["categories"][factor]
    model = {
        "units": "paired_fixed" if design.get("blocking") else "independent",
        "comparison": "coefficient" if len(levels) == 2 else "contrast",
    }
    if model["comparison"] == "contrast":
        model["condition_weights"] = {
            level: (1 if level == numerator else -1 if level == denominator else 0)
            for level in levels
        }
    request = {
        "measurement": "tximport_estimated_counts",
        "layer": None,
        "biological_unit": unit,
        "unit_column": design["biological_unit"],
        "condition_column": factor,
        "technical_factors": design["batch_columns"],
        "continuous_covariates": [],
        "numerator": numerator,
        "denominator": denominator,
        "model": model,
    }
    cohort = compile_mounted_model(design["metadata_rows"], request)
    original = np.asarray(design["design_matrix"])
    mounted = cohort.design
    if (
        original.shape != mounted.shape
        or cohort.contrast is None
        or not np.allclose(
            mounted @ np.linalg.lstsq(mounted, original, rcond=None)[0],
            original,
            atol=1e-10,
            rtol=1e-10,
        )
        or not np.allclose(
            cohort.contrast @ np.linalg.pinv(mounted),
            np.asarray(design["contrast_vector"]) @ np.linalg.pinv(original),
            atol=1e-10,
            rtol=1e-10,
        )
    ):
        raise ValueError(
            "Mounted Salmon DE does not preserve the frozen design and estimand"
        )
    return request


def _check_salmon_model(frozen, image, resources):
    from devharness_controlled.native_contracts import (
        KERNEL_VERSIONS,
        limma_voom_kernel_images,
    )

    if not isinstance(image, str) or image not in limma_voom_kernel_images():
        raise ValueError(
            "Salmon DE requires an installed locked Bioconductor image"
        )
    if resources["cpus"] < 2:
        raise ValueError(
            "The mounted Salmon DE worker requires two CPUs"
        )
    _salmon_model_options(frozen)
    return {
        "name": "edgeR",
        "version": KERNEL_VERSIONS["edgeR"],
        "image_id": image,
        "count_semantics": SALMON_COUNT_SOURCE,
        "normalization": "tximport_effective_length_and_TMM_2D_offset",
    }


def _stage_salmon_matrix(identity, samples, destination, *, positive=False):
    from .rna_execution_inputs import _write_hdf5

    path = _absolute(identity["path"], exists=True)
    if _identity(path, maximum=MAX_ARTIFACT_BYTES) != identity:
        raise ValueError("Salmon matrix changed before mounted staging")
    genes, matrix = [], []
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream, delimiter="\t", strict=True)
        if next(reader, None) != ["gene_id", *samples]:
            raise ValueError("Salmon matrix differs from the frozen sample order")
        for row in reader:
            if len(row) != len(samples) + 1 or len(genes) >= MAX_GENES:
                raise ValueError("Salmon matrix differs from the bounded gene family")
            values = [float(value) for value in row[1:]]
            if any(
                not math.isfinite(value) or value < 0 or positive and value == 0
                for value in values
            ):
                raise ValueError(
                    "Salmon counts and lengths must have valid finite values"
                )
            genes.append(row[0])
            matrix.append(values)
            if len(genes) * len(samples) > MAX_SALMON_ENTRIES:
                raise ValueError("Salmon mounted matrix exceeds its entry bound")
    if (
        not genes
        or len(set(genes)) != len(genes)
        or _identity(path, maximum=MAX_ARTIFACT_BYTES) != identity
    ):
        raise ValueError(
            "Salmon gene family is invalid or changed during mounted staging"
        )
    _write_hdf5(
        destination, {"gene_ids": genes, "sample_ids": samples, "matrix": matrix}
    )
    return genes


def _retain_salmon_de(outputs, destination, genes, request_sha, model_identity, design):
    import pyarrow.parquet as pq

    summary = _read_json(outputs / "summary.json")
    result = _identity(outputs / "genes.parquet", maximum=MAX_ARTIFACT_BYTES)
    if (
        summary.get("format") != "devbench.mounted-bulk-summary/1"
        or summary.get("request_sha256") != request_sha
        or summary.get("genes_sha256") != result["sha256"]
        or summary.get("row_count") != len(genes)
        or summary.get("design_rank") != design["rank"]
        or summary.get("residual_df") != design["residual_df"]
        or summary.get("measurement") != "tximport_estimated_counts"
        or summary.get("solver") != "edger_v4"
        or summary.get("solver_version") != model_identity["version"]
        or summary.get("test") != "qlf"
        or summary.get("normalization", {}).get("method")
        != model_identity["normalization"]
    ):
        raise ValueError(
            "Mounted DE did not attest the frozen Salmon model, inputs and gene family"
        )
    observed = []
    with (
        pq.ParquetFile(result["path"]) as parquet,
        destination.open("x", encoding="utf-8", newline="") as stream,
    ):
        if parquet.metadata.num_rows != len(genes):
            raise ValueError("Mounted DE does not have the expected gene count")
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(
            [
                "gene_id",
                "log2FoldChange",
                "pvalue",
                "padj",
                "estimate_status",
                "missing_reason",
            ]
        )
        columns = [
            "gene_id",
            "effect",
            "p_value",
            "q_value",
            "estimate_status",
            "missing_reason",
        ]
        for batch in parquet.iter_batches(batch_size=2048, columns=columns):
            for row in batch.to_pylist():
                gene, effect, pvalue, qvalue, status, reason = (
                    row[key] for key in columns
                )
                if status == "estimated":
                    if (
                        not all(
                            type(value) is float and math.isfinite(value)
                            for value in (effect, pvalue, qvalue)
                        )
                        or not 0 <= pvalue <= qvalue <= 1
                        or reason is not None
                    ):
                        raise ValueError(
                            "Invalid mounted effect or BH probability semantics"
                        )
                elif (
                    status not in {"filtered", "failed"}
                    or not reason
                    or any(value is not None for value in (effect, pvalue, qvalue))
                ):
                    raise ValueError(
                        "Unestimated mounted DE rows require an explicit missing reason"
                    )
                observed.append(gene)
                writer.writerow([gene, effect, pvalue, qvalue, status, reason])
    if (
        observed != genes
        or _identity(result["path"], maximum=MAX_ARTIFACT_BYTES) != result
    ):
        raise ValueError("Mounted DE gene family or output identity changed")
    return summary


def _run_salmon_model(
    artifacts,
    frozen,
    directory,
    model_identity,
    *,
    deadline,
    resources,
    cancellation_event=None,
    observe=None,
):
    from devbench.engine.mounted_kernel_oci import (
        _INTENT,
        _RECOVERED,
        recover_mounted_container,
        run_mounted_container,
    )
    from rna_kernels.mounted_bulk import REQUEST_FORMAT

    request = _salmon_model_options(frozen)
    design = frozen["design"]
    stage = directory / "mounted"
    inputs, outputs = stage / "inputs", stage / "outputs"
    directory.mkdir(mode=0o700)
    stage.mkdir(mode=0o700)
    inputs.mkdir(mode=0o700)
    outputs.mkdir(mode=0o700)
    genes = _stage_salmon_matrix(
        artifacts["count_matrix"], design["sample_ids"], inputs / "expression.h5"
    )
    length_genes = _stage_salmon_matrix(
        artifacts["effective_lengths"],
        design["sample_ids"],
        inputs / "lengths.h5",
        positive=True,
    )
    if genes != length_genes:
        raise ValueError("Salmon counts and lengths have different gene axes")
    rows = design["metadata_rows"]
    columns = [
        "sample_id",
        request["unit_column"],
        request["condition_column"],
        *request["technical_factors"],
    ]
    with (inputs / "metadata.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=columns, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    _write_json(
        inputs / "family.json",
        {
            "family_id": "fastq_salmon_gene_family",
            "primary": True,
            "feature_ids": genes,
            "numerator": request["numerator"],
            "denominator": request["denominator"],
        },
    )
    request["model"]["lengths_sha256"] = _identity(
        inputs / "lengths.h5", maximum=MAX_ARTIFACT_BYTES
    )["sha256"]
    request.update(
        format=REQUEST_FORMAT,
        expression="expression.h5",
        expression_sha256=_identity(
            inputs / "expression.h5", maximum=MAX_ARTIFACT_BYTES
        )["sha256"],
        metadata_sha256=_identity(inputs / "metadata.csv", maximum=5_000_000)["sha256"],
        family_sha256=_identity(inputs / "family.json", maximum=16_000_000)["sha256"],
    )
    _write_json(inputs / "request.json", request)
    digest = _identity(inputs / "request.json", maximum=1_000_000)["sha256"]
    attempt_id = uuid.uuid4().hex
    image_id = model_identity["image_id"]
    if observe is not None:
        observe(
            "salmon_model_launch",
            request_sha=digest,
            image_id=image_id,
            model_attempt_id=attempt_id,
        )
    _check_cancelled(cancellation_event)
    try:
        transport = run_mounted_container(
            stage,
            request_sha=digest,
            image_id=image_id,
            attempt_id=attempt_id,
            timeout=min(240, max(1, int(_remaining(deadline)))),
            memory_mb=resources["memory_gb"] * 1024,
        )
    except Exception:
        if (directory / _INTENT).exists() and not (directory / _RECOVERED).exists():
            recover_mounted_container(
                stage, request_sha=digest, image_id=image_id, attempt_id=attempt_id
            )
        raise
    if (
        transport.get("error_type") is not None
        or transport.get("cleanup_confirmed") is not True
    ):
        raise ValueError(
            "Locked Salmon differential-expression worker did not complete with verified cleanup"
        )
    _check_cancelled(cancellation_event)
    _remaining(deadline)
    result = directory / "differential-expression.tsv"
    summary = _retain_salmon_de(outputs, result, genes, digest, model_identity, design)
    result_identity = _identity(result, maximum=MAX_ARTIFACT_BYTES)
    provenance = {
        "format": "devbench.fastq-salmon-count-model/1",
        "status": "completed",
        "design": design,
        "count_source": SALMON_COUNT_SOURCE,
        "count_model": model_identity,
        "numerical_policy": {
            "analysis_policy": frozen.get("analysis_policy"),
            "alpha": frozen["alpha"],
            "estimated_count_rounding": False,
            "normalization": model_identity["normalization"],
        },
        "family": {
            "complete": True,
            "emitted_gene_count": len(genes),
            "result_row_count": len(genes),
            "gene_ids_sha256": hashlib.sha256(
                json.dumps(genes, separators=(",", ":")).encode()
            ).hexdigest(),
        },
        **artifacts,
        "differential_expression": result_identity,
        "mounted_request_sha256": digest,
        "mounted_summary": summary,
        "mounted_transport": transport,
    }
    _write_json(directory / "count-model.json", provenance)
    return {
        "status": "completed",
        "formula": design["formula"],
        "contrast": design["contrast"],
        "differential_expression": result_identity,
        "provenance": _identity(
            directory / "count-model.json", maximum=MAX_ARTIFACT_BYTES
        ),
        "family": provenance["family"],
    }


def _verified_model_artifact(value, model_directory):
    if not isinstance(value, dict) or not {'path', 'sha256'} <= set(value):
        raise ValueError('Count model did not return a verified artifact identity')
    path = _absolute(value['path'], exists=True)
    if not path.is_relative_to(model_directory):
        raise ValueError('Count model artifact escaped its attempt directory')
    actual = _identity(path, maximum=MAX_ARTIFACT_BYTES)
    if actual['sha256'] != value['sha256'] or ('size_bytes' in value and actual['size_bytes'] != value['size_bytes']):
        raise ValueError('Count model artifact changed or has a mismatched identity')
    return actual


def _publish(attempt, count_matrix, fit, model_identity, salmon_artifacts=None):
    paths = {'count_matrix': (count_matrix, 'counts.tsv')}
    if salmon_artifacts is not None:
        paths.update({
            key: (value, Path(value['path']).name)
            for key, value in salmon_artifacts.items()
        })
    if fit is not None:
        directory = attempt / 'count-model'
        paths.update({
            'differential_expression': (
                _verified_model_artifact(fit.get('differential_expression'), directory),
                'differential-expression.tsv',
            ),
            'count_model_provenance': (
                _verified_model_artifact(fit.get('provenance'), directory),
                'count-model.json',
            ),
        })
    staging, final = attempt / 'artifacts.partial', attempt / 'artifacts'
    staging.mkdir(exist_ok=False)
    for source, name in paths.values():
        shutil.copyfile(source['path'], staging / name, follow_symlinks=False)
        if _identity(staging / name, maximum=MAX_ARTIFACT_BYTES)['sha256'] != source['sha256']:
            raise ValueError('Artifact changed while publishing final results')
    staging.rename(final)
    return {key: {**_identity(final / name, maximum=MAX_ARTIFACT_BYTES), 'workflow': dict(WORKFLOW),
                  'count_model': model_identity} for key, (_, name) in paths.items()}


def _check_salmon_scope(input_root, output_root):
    if input_root is None:
        return
    source, destination = _absolute(input_root), _absolute(output_root)
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError("qc-salmon input root and output directory must be separate")


def _admit_fastq_inputs(plan, salmon, input_root, tx2gene, image, quantification_only):
    if (
        type(quantification_only) is not bool
        or not salmon
        and (
            quantification_only
            or any(value is not None for value in (input_root, tx2gene, image))
        )
    ):
        raise ValueError(
            "Salmon input, image and quantification-only options require qc-salmon"
        )
    if not salmon:
        return preflight_fastq_plan(plan)
    mapping = _salmon_inputs(input_root, tx2gene)
    frozen = preflight_fastq_plan(plan, input_root=Path(input_root))
    frozen["input_files"].append(mapping)
    frozen["tx2gene"] = mapping
    return frozen


def _salmon_attempt_artifacts(attempt, quantification, frozen, record):
    tx2gene = {key: frozen["tx2gene"][key] for key in ("path", "sha256")}
    artifacts = merge_salmon_gene_counts(
        quantification,
        frozen["sample_ids"],
        tx2gene,
        attempt,
    )
    provenance = _read_json(
        artifacts["salmon_quant_provenance"]["path"], MAX_ARTIFACT_BYTES
    )
    record["count_family"] = {
        "count_semantics": SALMON_COUNT_SOURCE,
        "gene_count": provenance["gene_count"],
    }
    return artifacts


def _fastq_fit(
    attempt,
    salmon_artifacts,
    frozen,
    model_identity,
    deadline,
    limits,
    diagnostics,
    stop,
    observe,
    supervised,
):
    design = frozen["design"]
    if salmon_artifacts is not None:
        fit = _run_salmon_model(
            salmon_artifacts,
            frozen,
            attempt / "count-model",
            model_identity,
            deadline=deadline,
            resources=limits,
            cancellation_event=stop,
            observe=observe,
        )
    else:
        # Recheck the pinned dependency immediately before the fit.
        if _check_model_for_design(design) != model_identity:
            raise ValueError("Pinned count-model identity changed after quantification")
        options = {}
        if frozen.get("analysis_policy") is not None:
            options["analysis_policy"] = frozen["analysis_policy"]
        if supervised:
            options["cancellation_event"] = stop
        fit = _run_count_model(
            attempt / "counts.tsv",
            design,
            attempt / "count-model",
            alpha=frozen["alpha"],
            deadline=deadline,
            resources=limits,
            diagnostics=diagnostics,
            **options,
        )
    if (
        fit.get("status") != "completed"
        or fit.get("formula") != design["formula"]
        or fit.get("contrast") != design["contrast"]
    ):
        raise ValueError("Count model did not preserve the frozen formula and contrast")
    return fit


def _retain_model_cleanup(attempt, record):
    from devbench.engine.mounted_kernel_oci import _INTENT, _RECOVERED

    directory = attempt / "count-model"
    if (directory / _INTENT).exists() and not (directory / _RECOVERED).exists():
        record["salmon_model_cleanup_required"] = True


def _complete_fastq_attempt(
    attempt, record, count_matrix, fit, model_identity, salmon_artifacts,
):
    record["artifacts"] = _publish(
        attempt, count_matrix, fit, model_identity, salmon_artifacts,
    )
    record.update(
        status="completed", software_acceptance=True, outcome_category="accepted",
    )
    if fit is not None:
        record.update(
            count_model_execution="verified", count_model_result=fit,
            execution_scope="specified_workflow_and_count_model_software_execution",
        )
    else:
        record["execution_scope"] = (
            "specified_workflow_and_quantification_software_execution"
        )


def run_fastq_plan(plan, output_root, *, nextflow_executable=None, timeout_sec=3600, preflight_only=False,
                   resources=None, execution_profile='standard', cancellation_event=None, execution_observer=None,
                   expected_nextflow_sha256=None, input_root=None, tx2gene=None,
                   mounted_image_id=None, quantification_only=False):
    """Run one immutable local attempt; refusals retain no workflow session.

    ``completed`` means the specified software ran and verified its output
    identities. It does not mean independent scientific correctness, a successful
    scientific trial, a model-provider authorization or a competitor benchmark.
    """
    started = time.monotonic()
    salmon = execution_profile == 'qc-salmon'
    _check_salmon_scope(input_root if salmon else None, output_root)
    attempt = _attempt(output_root)
    record = _initial_record(attempt)
    stage = 'preflight'
    stop = cancellation_event if cancellation_event is not None else threading.Event()
    def observe(event, **facts):
        if execution_observer is not None:
            value = {'event': event, 'attempt_dir': str(attempt),
                     'container_label': 'devbench.fastq.attempt=' + attempt.name, **facts}
            if execution_observer(value) is False:
                stop.set()
    try:
        if not isinstance(stop, threading.Event):
            raise TypeError('Cancellation requires a trusted coordinator threading.Event')
        if execution_observer is not None and not callable(execution_observer):
            raise TypeError('Use a trusted callable execution observer')
        observe('attempt_created')
        _check_cancelled(stop)
        if type(timeout_sec) is not int or not 1 <= timeout_sec <= 86400 or type(preflight_only) is not bool:
            raise ValueError('Use a bounded integer timeout and explicit preflight-only boolean')
        limits = _resources(resources, timeout_sec)
        deadline = started + timeout_sec
        record['execution_profile'] = _execution_profile(execution_profile)
        if any(character.isspace() for character in str(attempt)):
            raise ValueError('The pinned nf-core schema requires an output path without whitespace')
        frozen = _admit_fastq_inputs(
            plan, salmon, input_root, tx2gene, mounted_image_id, quantification_only,
        )
        _check_cancelled(stop)
        _write_json(attempt / 'preflight.json', frozen)
        record.update(formula=frozen['design']['formula'], contrast=frozen['design']['contrast'],
                      preflight=_identity(attempt / 'preflight.json'), input_files=frozen['input_files'])
        stage = 'count_model_admission'
        model_identity = (
            (None if quantification_only else _check_salmon_model(frozen, mounted_image_id, limits))
            if salmon else _check_model_for_design(frozen['design'])
        )
        record['count_model'] = model_identity
        record['resources'] = limits
        if preflight_only:
            record['status'] = 'preflight_passed'
            record['outcome_category'] = 'accepted'
            observe('terminal', status=record['status'], engine_launch_count=0)
            return _finish(attempt, record)
        stage = 'runtime_admission'
        runtime_identity = _nextflow_identity(nextflow_executable, attempt,
            **({'expected_sha256': expected_nextflow_sha256} if expected_nextflow_sha256 is not None else {}))
        record['nextflow'] = runtime_identity
        stage = 'input_binding'
        _verify_input_files(frozen)
        profile = _runtime_profile()
        record['runtime_profile'] = profile
        quantification = _prepare_engine(frozen, attempt, limits, execution_profile)
        trace_path = attempt / ('nextflow-trace.tsv' if salmon else 'trace.tsv')
        count_source = (
            SALMON_COUNT_SOURCE if salmon else 'STAR_GeneCounts_not_Salmon_estimates'
        )
        command = [runtime_identity['path'], '-log', str(attempt / 'nextflow.log'), 'run', WORKFLOW['name'],
                   '-r', WORKFLOW['commit'], '-profile', profile, '-params-file', str(attempt / 'params.json'),
                   '-c', str(attempt / 'local.config'), '-work-dir', str(attempt / 'work'),
                   '-with-trace', str(trace_path),
                   '-with-report', str(attempt / 'report.html'),
                   '-with-timeline', str(attempt / 'timeline.html')]
        command += ['--skip_alignment', '--pseudo_aligner', 'salmon'] if salmon else []
        record['count_source'] = count_source
        _write_json(attempt / 'invocation.json', {'argv': command, 'workflow': WORKFLOW,
            'nextflow': runtime_identity, 'cwd': str(attempt), 'timeout_sec': timeout_sec,
            'resources': limits, 'profile': profile, 'execution_profile': record['execution_profile'],
            'environment_policy': 'minimal_environment_fresh_NXF_HOME_pinned_NXF_VER',
            'count_source': count_source})
        stage = 'quantification'
        remaining = _remaining(deadline)
        observe('before_engine_start')
        _check_cancelled(stop)
        record['engine_launch_attempt_count'] = 1
        record['engine_execution'] = 'launch_intent'
        _write_json(attempt / 'result.json', record)
        def started_process(pid):
            import psutil
            record.update(engine_launch_count=1, engine_execution='started', engine_process_id=pid)
            _write_json(attempt / 'result.json', record)
            try:
                created = psutil.Process(pid).create_time()
            except psutil.NoSuchProcess:
                created = None
            record['engine_process_create_time'] = created
            _write_json(attempt / 'result.json', record)
            observe('engine_started', pid=pid, process_create_time=created)
            _check_cancelled(stop)
        execution = _execute_command(command, cwd=attempt, log_path=attempt / 'engine-output.log',
            timeout_sec=remaining, on_started=started_process,
            **({'cancellation_event': stop} if cancellation_event is not None or execution_observer is not None else {}))
        record['engine_process'] = execution
        observe('engine_finished', exit_code=execution['exit_code'])
        _check_cancelled(stop)
        if execution['exit_code'] != 0 or execution['timed_out']:
            raise RuntimeError('Nextflow quantification failed or exceeded its deadline')
        stage = 'engine_verification'
        runtime = _verify_runtime(attempt)
        record.update(engine_session_id=runtime['session_id'], engine_execution='verified',
                      engine_runtime=_identity(attempt / 'engine-runtime.json'))
        _verify_input_files(frozen)
        stage = 'count_matrix'
        salmon_artifacts, fit = None, None
        if salmon:
            salmon_artifacts = _salmon_attempt_artifacts(
                attempt, quantification, frozen, record,
            )
            count_matrix = salmon_artifacts['count_matrix']
        else:
            count_matrix = merge_star_gene_counts(
                quantification, _quantified_samples(frozen), attempt / 'counts.tsv',
            )
            record['count_family'] = {
                key: value for key, value in count_matrix.items()
                if key not in {'path', 'sha256', 'size_bytes'}
            }
        if not quantification_only:
            stage = 'count_model'
            record['count_model_supervisor'] = {}
            fit = _fastq_fit(
                attempt, salmon_artifacts, frozen, model_identity, deadline, limits,
                record['count_model_supervisor'], stop, observe,
                cancellation_event is not None or execution_observer is not None,
            )
        stage = 'artifact_verification'
        observe('before_publication')
        _check_cancelled(stop)
        _complete_fastq_attempt(
            attempt, record, count_matrix, fit, model_identity, salmon_artifacts,
        )
    except (Exception, KeyboardInterrupt) as error:
        from devharness_core.watchdog import WorkerCancelled
        before_launch = record['engine_launch_count'] == 0
        record.update(status='refused' if before_launch else 'failed', failure_stage=stage,
                      reason=(type(error).__name__ + ': ' + str(error))[:4000], software_acceptance=False,
                      artifacts={})
        if isinstance(error, WorkerCancelled):
            record.update(status='cancelled', outcome_category='cancelled')
        elif hasattr(error, 'category') and hasattr(error, 'code'):
            record.update(outcome_category=error.category, reason_code=error.code)
            if before_launch:
                record['status'] = error.status
        else:
            record['outcome_category'] = 'input_error' if stage in {'preflight', 'input_binding'} else 'infrastructure_failure'
        if record['engine_execution'] == 'started':
            record['engine_execution'] = 'failed'
        elif record['engine_execution'] == 'launch_intent':
            record['engine_execution'] = 'launch_failed'
        if stage == 'count_model':
            record['count_model_execution'] = 'failed'
            _retain_model_cleanup(attempt, record)
        if not before_launch:
            try:
                record['container_cleanup'] = _cleanup_containers(attempt)
            except Exception as cleanup_error:
                record['container_cleanup'] = {'status': 'failed', 'reason': str(cleanup_error)[:2000]}
    try:
        observe('terminal', status=record['status'], engine_launch_count=record['engine_launch_count'])
    except Exception as observer_error:
        record['observer_error'] = (type(observer_error).__name__ + ': ' + str(observer_error))[:2000]
        record.update(status='failed', software_acceptance=False, outcome_category='infrastructure_failure', artifacts={})
    return _finish(attempt, record)
