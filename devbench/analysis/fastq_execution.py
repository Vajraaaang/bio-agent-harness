"""Local pinned Nextflow quantification and whole-family count-model execution.

STAR GeneCounts, emitted by nf-core/rnaseq, is the direct integer-count handoff.
Salmon estimates are deliberately not silently rounded or fitted without length
correction. This adapter launches no provider/model selector and grants no
scientific or clinical approval. Every attempt has a separate retained outcome.
"""
from __future__ import annotations

import csv
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
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
MAX_ARTIFACT_BYTES = 100_000_000
MAX_LOG_BYTES = 10_000_000
MAX_GENES = 100_000
SUMMARY_ROWS = ('N_unmapped', 'N_multimapping', 'N_noFeature', 'N_ambiguous')
STRAND_COLUMN = {'unstranded': 1, 'forward': 2, 'reverse': 3}


def preflight_fastq_plan(plan):
    from .fastq_contract import preflight_fastq_plan as validate
    return validate(plan)


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
    if name not in {'standard', 'minimal-counts'}:
        raise ValueError('Select the closed standard or minimal-counts execution profile')
    return json.loads(json.dumps(STANDARD_PROFILE if name == 'standard' else MINIMAL_PROFILE))


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
    from devharness_core.watchdog import run_sandboxed, worker_limits, cancel_workers_on
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
    return {'status': 'reconciled' if stopped is True and cleanup.get('status') == 'complete' else 'uncertain',
            'process_stopped': stopped, 'reason': reason, 'attempt_dir': str(attempt),
            'container_cleanup': cleanup, 'execution_restarted': False}


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
    _write_json(attempt / 'params.json', params)
    runtime = _groovy_string(attempt / 'engine-runtime.json')
    config = """// Local execution policy; fixed workflow revision and direct STAR count handoff.
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
        'CONTAINER_LABEL', _groovy_string('--pids-limit=256 --label devbench.fastq.attempt=' + attempt.name))
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


def _publish(attempt, count_matrix, fit, model_identity):
    model_directory = attempt / 'count-model'
    de = _verified_model_artifact(fit.get('differential_expression'), model_directory)
    provenance = _verified_model_artifact(fit.get('provenance'), model_directory)
    staging, final = attempt / 'artifacts.partial', attempt / 'artifacts'
    staging.mkdir(exist_ok=False)
    paths = {'count_matrix': (count_matrix, 'counts.tsv'), 'differential_expression': (de, 'differential-expression.tsv'),
             'count_model_provenance': (provenance, 'count-model.json')}
    for source, name in paths.values():
        shutil.copyfile(source['path'], staging / name, follow_symlinks=False)
        if _identity(staging / name, maximum=MAX_ARTIFACT_BYTES)['sha256'] != source['sha256']:
            raise ValueError('Artifact changed while publishing final results')
    staging.rename(final)
    return {key: {**_identity(final / name, maximum=MAX_ARTIFACT_BYTES), 'workflow': dict(WORKFLOW),
                  'count_model': model_identity} for key, (_, name) in paths.items()}


def run_fastq_plan(plan, output_root, *, nextflow_executable=None, timeout_sec=3600, preflight_only=False,
                   resources=None, execution_profile='standard', cancellation_event=None, execution_observer=None,
                   expected_nextflow_sha256=None):
    """Run one immutable local attempt; refusals retain no workflow session.

    ``completed`` means the specified software ran and verified its output
    identities. It does not mean independent scientific correctness, a successful
    scientific trial, a model-provider authorization or a competitor benchmark.
    """
    started = time.monotonic()
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
        frozen = preflight_fastq_plan(plan)
        _check_cancelled(stop)
        _write_json(attempt / 'preflight.json', frozen)
        record.update(formula=frozen['design']['formula'], contrast=frozen['design']['contrast'],
                      preflight=_identity(attempt / 'preflight.json'), input_files=frozen['input_files'])
        stage = 'count_model_admission'
        model_identity = _check_model_for_design(frozen['design'])
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
        command = [runtime_identity['path'], '-log', str(attempt / 'nextflow.log'), 'run', WORKFLOW['name'],
                   '-r', WORKFLOW['commit'], '-profile', profile, '-params-file', str(attempt / 'params.json'),
                   '-c', str(attempt / 'local.config'), '-work-dir', str(attempt / 'work'),
                   '-with-trace', str(attempt / 'trace.tsv'), '-with-report', str(attempt / 'report.html'),
                   '-with-timeline', str(attempt / 'timeline.html')]
        _write_json(attempt / 'invocation.json', {'argv': command, 'workflow': WORKFLOW,
            'nextflow': runtime_identity, 'cwd': str(attempt), 'timeout_sec': timeout_sec,
            'resources': limits, 'profile': profile, 'execution_profile': record['execution_profile'],
            'environment_policy': 'minimal_environment_fresh_NXF_HOME_pinned_NXF_VER',
            'count_source': 'STAR_GeneCounts_not_Salmon_estimates'})
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
        count_matrix = merge_star_gene_counts(quantification, _quantified_samples(frozen), attempt / 'counts.tsv')
        record['count_family'] = {key: value for key, value in count_matrix.items() if key not in {'path', 'sha256', 'size_bytes'}}
        stage = 'count_model'
        # A new dependency check closes the interval between preflight and fit.
        if _check_model_for_design(frozen['design']) != model_identity:
            raise ValueError('Pinned count-model identity changed after quantification')
        record['count_model_supervisor'] = {}
        fit = _run_count_model(attempt / 'counts.tsv', frozen['design'], attempt / 'count-model',
            alpha=frozen['alpha'], deadline=deadline, resources=limits,
            diagnostics=record['count_model_supervisor'],
            **({'analysis_policy': frozen['analysis_policy']} if frozen.get('analysis_policy') is not None else {}),
            **({'cancellation_event': stop} if cancellation_event is not None or execution_observer is not None else {}))
        if fit.get('status') != 'completed' or fit.get('formula') != frozen['design']['formula'] or fit.get('contrast') != frozen['design']['contrast']:
            raise ValueError('Count model did not preserve the frozen formula and contrast')
        stage = 'artifact_verification'
        observe('before_publication')
        _check_cancelled(stop)
        record['artifacts'] = _publish(attempt, count_matrix, fit, model_identity)
        record.update(status='completed', count_model_execution='verified',
                      execution_scope='specified_workflow_and_count_model_software_execution',
                      software_acceptance=True, count_model_result=fit, outcome_category='accepted')
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
