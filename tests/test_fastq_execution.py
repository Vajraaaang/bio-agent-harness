"""Local FASTQ execution contracts: synthetic process fakes are software evidence only."""
from copy import deepcopy
import csv
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path

import pytest

PIN = 'e7ca46272c8f9d5ceee3f71759f4ba551d3217a4'


def api():
    name = 'devbench.analysis.fastq_execution'
    assert importlib.util.find_spec(name) is not None, 'FASTQ execution adapter is not implemented'
    return importlib.import_module(name)


def star(path, genes=('g1', 'g2', 'g3'), offset=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('N_unmapped\t0\t0\t0\nN_multimapping\t0\t0\t0\nN_noFeature\t0\t0\t0\nN_ambiguous\t0\t0\t0\n' +
                    ''.join(f'{gene}\t{index+offset}\t{index+10+offset}\t{index+20+offset}\n' for index, gene in enumerate(genes)))


def samples():
    return [{'sample': 'a', 'strandedness': 'unstranded'}, {'sample': 'b', 'strandedness': 'forward'},
            {'sample': 'c', 'strandedness': 'reverse'}]


def test_star_counts_select_declared_strand_without_trimming_family(tmp_path):
    m = api()
    for sample in samples():
        star(tmp_path / 'quant' / 'star_salmon' / 'log' / (sample['sample'] + '.ReadsPerGene.out.tab'))
    result = m.merge_star_gene_counts(tmp_path / 'quant', samples(), tmp_path / 'counts.tsv')
    with (tmp_path / 'counts.tsv').open() as stream:
        rows = list(csv.reader(stream, delimiter='\t'))
    assert rows == [['gene_id', 'a', 'b', 'c'], ['g1', '0', '10', '20'], ['g2', '1', '11', '21'], ['g3', '2', '12', '22']]
    assert result['gene_count'] == 3
    assert result['count_semantics'] == 'STAR_GeneCounts_direct_integer'
    assert result['excluded_summary_rows'] == ['N_unmapped', 'N_multimapping', 'N_noFeature', 'N_ambiguous']


def test_star_counts_preserve_more_than_5000_genes(tmp_path):
    m = api()
    genes = tuple('g' + str(i) for i in range(5001))
    for sample in samples():
        star(tmp_path / 'quant' / 'star_salmon' / 'log' / (sample['sample'] + '.ReadsPerGene.out.tab'), genes)
    result = m.merge_star_gene_counts(tmp_path / 'quant', samples(), tmp_path / 'counts.tsv')
    assert result['gene_count'] == 5001
    assert len((tmp_path / 'counts.tsv').read_text().splitlines()) == 5002


@pytest.mark.parametrize('mode', ['different_order', 'fraction', 'negative', 'duplicate_gene', 'missing_summary', 'unknown_sample', 'symlink'])
def test_star_counts_fail_closed_on_malformed_or_unbound_outputs(tmp_path, mode):
    m = api()
    folder = tmp_path / 'quant' / 'star_salmon' / 'log'
    for sample in samples():
        star(folder / (sample['sample'] + '.ReadsPerGene.out.tab'))
    path = folder / 'b.ReadsPerGene.out.tab'
    text = path.read_text()
    if mode == 'different_order':
        star(path, ('g2', 'g1', 'g3'))
    elif mode == 'fraction':
        path.write_text(text.replace('g1\t0\t10\t20', 'g1\t0\t10.5\t20'))
    elif mode == 'negative':
        path.write_text(text.replace('g1\t0\t10\t20', 'g1\t0\t-1\t20'))
    elif mode == 'duplicate_gene':
        path.write_text(text + 'g1\t1\t1\t1\n')
    elif mode == 'missing_summary':
        path.write_text(text.split('\n', 1)[1])
    elif mode == 'unknown_sample':
        star(folder / 'unknown.ReadsPerGene.out.tab')
    else:
        target = tmp_path / 'external.tab'
        path.rename(target)
        path.symlink_to(target)
    with pytest.raises(ValueError):
        m.merge_star_gene_counts(tmp_path / 'quant', samples(), tmp_path / 'counts.tsv')
    assert not (tmp_path / 'counts.tsv').exists()


def frozen(tmp_path):
    f = tmp_path / 'reads.fq'
    f.write_text('@r\nACGT\n+\nIIII\n')
    source = {'path': str(f), 'size_bytes': f.stat().st_size, 'sha256': hashlib.sha256(f.read_bytes()).hexdigest(), 'kind': 'fastq'}
    return {'format': 'devbench.fastq-preflight/1', 'samples': [{**s, 'fastq_1': str(f), 'fastq_2': ''} for s in samples()],
            'sample_ids': ['a', 'b', 'c'], 'input_files': [source], 'reference': {'fasta': str(f), 'gtf': str(f)},
            'design': {'formula': '~ batch + condition', 'contrast': {'factor': 'condition', 'numerator': 'treated', 'denominator': 'control'},
                       'metadata_rows': [], 'sample_ids': ['a', 'b', 'c']}, 'alpha': .05, 'plan_sha256': 'a' * 64}


def patch_preflight(monkeypatch, m, tmp_path):
    state = frozen(tmp_path)
    monkeypatch.setattr(m, 'preflight_fastq_plan', lambda plan: deepcopy(state))
    monkeypatch.setattr(m, 'check_count_model', lambda: {'name': 'PyDESeq2', 'version': '0.5.4'})
    monkeypatch.setattr(m, '_nextflow_identity', lambda executable, attempt: {'path': '/bin/nextflow', 'version': '25.10.4'})
    return state


def read_result(result):
    return json.loads(Path(result['result_manifest']).read_text())


def test_preflight_refusal_retains_reason_without_engine_or_de(tmp_path, monkeypatch):
    m = api()
    def reject(plan):
        raise ValueError('batch completely confounded with condition')
    monkeypatch.setattr(m, 'preflight_fastq_plan', reject)
    monkeypatch.setattr(m, '_execute_command', lambda *a, **k: pytest.fail('engine entered after refusal'))
    result = m.run_fastq_plan({}, tmp_path / 'runs')
    record = read_result(result)
    assert record['status'] == 'refused'
    assert 'confounded' in record['reason']
    assert record['engine_launch_count'] == 0
    assert record['engine_session_id'] is None
    assert record['scientific_correctness'] is None
    assert record['model_execution_authorized'] is False
    assert record['model_sessions'] == 0
    assert record['artifacts'] == {}


def test_missing_model_dependency_refuses_before_engine(tmp_path, monkeypatch):
    m = api()
    patch_preflight(monkeypatch, m, tmp_path)
    def reject():
        raise RuntimeError('Pinned PyDESeq2 dependency unavailable')
    monkeypatch.setattr(m, 'check_count_model', reject)
    monkeypatch.setattr(m, '_execute_command', lambda *a, **k: pytest.fail('engine entered with no count model'))
    record = read_result(m.run_fastq_plan({}, tmp_path / 'runs'))
    assert record['status'] == 'refused'
    assert record['failure_stage'] == 'count_model_admission'
    assert record['engine_launch_count'] == 0
    assert record['artifacts'] == {}


def test_preflight_only_never_invokes_runtime(tmp_path, monkeypatch):
    m = api()
    patch_preflight(monkeypatch, m, tmp_path)
    monkeypatch.setattr(m, '_nextflow_identity', lambda *a, **k: pytest.fail('preflight launches no executable'))
    record = read_result(m.run_fastq_plan({}, tmp_path / 'runs', preflight_only=True))
    assert record['status'] == 'preflight_passed'
    assert record['engine_launch_count'] == 0
    assert record['scientific_correctness'] is None


def fake_engine(monkeypatch, m, *, commit=PIN, exit_code=0, artifacts=True):
    calls = []
    monkeypatch.setattr(m, '_cleanup_containers', lambda attempt: {'status': 'synthetic_fixture_no_containers'})
    monkeypatch.setattr(m, '_run_count_model', lambda counts_path, design, output_dir, *, alpha, **kwargs: m.fit_count_matrix(counts_path, design, output_dir, alpha=alpha))
    def execute(command, *, cwd, log_path, timeout_sec, on_started=None):
        calls.append(command)
        if on_started is not None:
            on_started(12345)
        root = Path(cwd)
        params = json.loads((root / 'params.json').read_text())
        assert params['extra_star_align_args'] == '--quantMode TranscriptomeSAM GeneCounts'
        assert params['custom_config_base'] == ''
        assert params['igenomes_ignore'] is True
        assert params['skip_pseudo_alignment'] is True
        assert params['skip_bbsplit'] is True
        assert PIN in command
        assert '-resume' not in command
        runtime = {'workflow': 'nf-core/rnaseq', 'commit_id': commit, 'nextflow_version': '25.10.4',
                   'session_id': '11111111-2222-4333-8444-555555555555', 'success': exit_code == 0, 'exit_status': exit_code}
        (root / 'engine-runtime.json').write_text(json.dumps(runtime))
        if artifacts:
            for sample in samples():
                star(Path(params['outdir']) / 'star_salmon' / 'log' / (sample['sample'] + '.ReadsPerGene.out.tab'))
        Path(log_path).write_text('Controlled synthetic process fixture; no Nextflow actually launched.\n')
        return {'exit_code': exit_code, 'timed_out': False, 'log_truncated': False, 'duration_seconds': .01}
    monkeypatch.setattr(m, '_execute_command', execute)
    return calls


def fake_fit(counts_path, design, output_dir, *, alpha):
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)
    de = output_dir / 'differential-expression.tsv'
    de.write_text('gene_id\tlog2FoldChange\tpvalue\tpadj\ng1\t1\t0.1\t0.3\ng2\t0\t0.5\t0.75\ng3\tNA\tNA\tNA\n')
    provenance = output_dir / 'count-model.json'
    provenance.write_text(json.dumps({'formula': design['formula'], 'contrast': design['contrast'], 'fixture': True}))
    def identity(path):
        return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'size_bytes': path.stat().st_size}
    return {'status': 'completed', 'formula': design['formula'], 'contrast': design['contrast'],
            'differential_expression': identity(de), 'provenance': identity(provenance), 'family': {'gene_count': 3}}


def test_synthetic_orchestration_retains_pins_full_counts_and_separate_evidence(tmp_path, monkeypatch):
    m = api()
    patch_preflight(monkeypatch, m, tmp_path)
    calls = fake_engine(monkeypatch, m)
    monkeypatch.setattr(m, 'fit_count_matrix', fake_fit)
    result = m.run_fastq_plan({}, tmp_path / 'runs')
    record = read_result(result)
    assert len(calls) == 1
    assert record['status'] == 'completed'
    assert record['workflow']['commit'] == PIN
    assert record['formula'] == '~ batch + condition'
    assert record['engine_launch_count'] == 1
    assert record['model_sessions'] == 0
    assert record['scientific_correctness'] is None
    assert record['biological_agreement'] == 'not_run'
    assert set(record['artifacts']) >= {'count_matrix', 'differential_expression', 'count_model_provenance'}
    for value in record['artifacts'].values():
        assert Path(value['path']).is_absolute()
        assert hashlib.sha256(Path(value['path']).read_bytes()).hexdigest() == value['sha256']
    assert record['count_family']['gene_count'] == 3


@pytest.mark.parametrize('mode', ['wrong_pin', 'nonzero', 'missing_outputs', 'model_failure'])
def test_engine_exit_is_not_model_or_artifact_admission(tmp_path, monkeypatch, mode):
    m = api()
    patch_preflight(monkeypatch, m, tmp_path)
    fake_engine(monkeypatch, m, commit='0' * 40 if mode == 'wrong_pin' else PIN,
                exit_code=1 if mode == 'nonzero' else 0, artifacts=mode != 'missing_outputs')
    def fit(*args, **kwargs):
        if mode == 'model_failure':
            raise RuntimeError('count model did not estimate')
        pytest.fail('Invalid engine output reached count model')
    monkeypatch.setattr(m, 'fit_count_matrix', fit)
    record = read_result(m.run_fastq_plan({}, tmp_path / 'runs'))
    assert record['status'] == 'failed'
    assert record['artifacts'] == {}
    assert record['scientific_correctness'] is None
    assert not (Path(record['attempt_dir']) / 'artifacts' / 'differential-expression.tsv').exists()


def test_input_mutation_after_preflight_prevents_engine_launch(tmp_path, monkeypatch):
    m = api()
    state = patch_preflight(monkeypatch, m, tmp_path)
    def identity(*args):
        Path(state['input_files'][0]['path']).write_text('changed\n')
        return {'path': '/bin/nextflow', 'version': '25.10.4'}
    monkeypatch.setattr(m, '_nextflow_identity', identity)
    monkeypatch.setattr(m, '_execute_command', lambda *a, **k: pytest.fail('mutated input launched'))
    record = read_result(m.run_fastq_plan({}, tmp_path / 'runs'))
    assert record['status'] == 'refused'
    assert record['engine_launch_count'] == 0
    assert 'changed' in record['reason'].lower()


def test_command_capture_has_bounded_log(tmp_path, monkeypatch):
    import sys
    m = api()
    monkeypatch.setattr(m, 'MAX_LOG_BYTES', 1000)
    result = m._execute_command([sys.executable, '-c', 'print("x"*5000)'],
                                cwd=tmp_path, log_path=tmp_path / 'output.log', timeout_sec=5)
    assert result['exit_code'] == 0
    assert result['log_truncated'] is True
    assert (tmp_path / 'output.log').stat().st_size == 1000


def test_command_deadline_terminates_process_group(tmp_path):
    import sys
    m = api()
    result = m._execute_command([sys.executable, '-c', 'import time; time.sleep(30)'],
                                cwd=tmp_path, log_path=tmp_path / 'output.log', timeout_sec=.2)
    assert result['timed_out'] is True
    assert result['exit_code'] != 0
    assert result['duration_seconds'] < 3


def test_deadline_terminates_descendant_after_leader_exit(tmp_path):
    import os
    import signal
    import sys
    import time
    m = api()
    heartbeat = tmp_path / 'heartbeat.txt'
    child_code = ('import pathlib,time;p=pathlib.Path(' + repr(str(heartbeat)) + ')\n'
                  'while True:\n p.write_text(str(time.monotonic()));time.sleep(.03)')
    command = [sys.executable, '-c',
               'import subprocess,sys; p=subprocess.Popen([sys.executable,"-c",' + repr(child_code) + ']);print(p.pid,flush=True)']
    child = None
    try:
        result = m._execute_command(command, cwd=tmp_path, log_path=tmp_path / 'output.log', timeout_sec=.2)
        child = int((tmp_path / 'output.log').read_text().strip())
        assert result['timed_out'] is True
        assert result['duration_seconds'] < 3
        last_heartbeat = heartbeat.read_text()
        time.sleep(.2)
        assert heartbeat.read_text() == last_heartbeat
    finally:
        if child:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_stdout_close_does_not_shorten_configured_deadline(tmp_path):
    import sys
    m = api()
    result = m._execute_command([sys.executable, '-c', 'import os,time;os.close(1);os.close(2);time.sleep(2.2)'],
                                cwd=tmp_path, log_path=tmp_path / 'output.log', timeout_sec=5)
    assert result['exit_code'] == 0
    assert result['timed_out'] is False


def test_duplicate_samples_cannot_overwrite_count_column(tmp_path):
    m = api()
    folder = tmp_path / 'quant' / 'star_salmon' / 'log'
    star(folder / 'a.ReadsPerGene.out.tab')
    with pytest.raises(ValueError):
        m.merge_star_gene_counts(tmp_path / 'quant', [samples()[0], samples()[0]], tmp_path / 'counts.tsv')
    assert not (tmp_path / 'counts.tsv').exists()


def test_actual_cli_input_refusal_is_retained_without_runtime(tmp_path, capsys):
    from devbench.analysis.fastq_cli import main
    path = tmp_path / 'bad.json'
    path.write_text('{malformed}')
    assert main(['--plan', str(path), '--output', str(tmp_path / 'runs')]) != 0
    record = json.loads(capsys.readouterr().out)
    manifest = json.loads(Path(record['result_manifest']).read_text())
    assert manifest['failure_stage'] == 'plan_input'
    assert manifest['status'] == 'refused'
    assert manifest['engine_launch_count'] == 0


def test_runtime_profile_is_native_and_resources_are_explicit(monkeypatch):
    m = api()
    monkeypatch.setattr(m.platform, 'machine', lambda: 'aarch64')
    assert m._runtime_profile() == 'docker,arm64'
    monkeypatch.setattr(m.platform, 'machine', lambda: 'x86_64')
    assert m._runtime_profile() == 'docker'
    assert m._resources({'cpus': 4, 'memory_gb': 12}, 3600)['memory_gb'] == 12
    with pytest.raises(ValueError):
        m._resources({'cpus': True, 'memory_gb': 12}, 3600)


def test_container_cleanup_targets_only_attempt_label(tmp_path, monkeypatch):
    m = api()
    monkeypatch.setattr(m.shutil, 'which', lambda name: '/usr/bin/true')
    commands = []
    def execute(command, *, cwd, log_path, timeout_sec):
        commands.append(command)
        Path(log_path).write_text('a' * 64 + '\n' if command[1] == 'ps' else 'stopped\n')
        return {'exit_code': 0, 'timed_out': False, 'log_truncated': False}
    monkeypatch.setattr(m, '_execute_command', execute)
    result = m._cleanup_containers(tmp_path)
    assert result['status'] == 'complete'
    assert commands[0][-1] == 'label=devbench.fastq.attempt=' + tmp_path.name
    assert commands[1][-1] == 'a' * 64
    assert commands[1][1:4] == ['stop', '--time', '5']


def test_lane_memberships_merge_to_one_count_column():
    m = api()
    lanes = [{'sample': 'one', 'strandedness': 'forward', 'fastq_1': '/a'},
             {'sample': 'one', 'strandedness': 'forward', 'fastq_1': '/b'},
             {'sample': 'two', 'strandedness': 'reverse', 'fastq_1': '/c'}]
    frozen = {'samples': lanes, 'sample_ids': ['two', 'one']}
    assert [row['sample'] for row in m._quantified_samples(frozen)] == ['two', 'one']
    lanes[1]['strandedness'] = 'reverse'
    with pytest.raises(ValueError, match='conflicting'):
        m._quantified_samples(frozen)


def test_closed_minimal_counts_profile_records_omitted_qc(tmp_path):
    m = api()
    state = frozen(tmp_path)
    attempt = tmp_path / 'attempt'
    attempt.mkdir()
    m._prepare_engine(state, attempt, m._resources(None, 60), 'minimal-counts')
    params = json.loads((attempt / 'params.json').read_text())
    assert params['skip_qc'] is True
    assert params['skip_linting'] is False
    assert params['skip_alignment'] is False
    assert params['skip_quantification_merge'] is False
    assert params['gffread_transcript_fasta'] is True
    profile = m._execution_profile('minimal-counts')
    assert profile['name'] == 'minimal-counts'
    assert 'read trimming' in profile['omitted_checks']
    assert 'process.resourceLimits = [cpus: 2, memory: 6.GB, time: 60.sec]' in (attempt / 'local.config').read_text()
    assert 'devbench.fastq.attempt=' + attempt.name in (attempt / 'local.config').read_text()


def test_count_model_uses_existing_watchdog_with_remaining_deadline(tmp_path, monkeypatch):
    import time
    from devharness_core import watchdog
    m = api()
    calls = []
    def supervise(target, args=(), kwargs=None, **limits):
        calls.append((target, args, limits))
        return {'status': 'fixture'}
    monkeypatch.setattr(watchdog, 'run_sandboxed', supervise)
    deadline = time.monotonic() + 20
    result = m._run_count_model(tmp_path / 'counts.tsv', {'formula': '~ batch + condition'},
        tmp_path / 'model', alpha=.05, deadline=deadline,
        resources={'memory_gb': 3}, diagnostics={})
    assert result == {'status': 'fixture'}
    assert calls[0][0] is m._count_model_worker
    assert calls[0][2]['max_mem_mb'] == 3072
    assert 0 < calls[0][2]['timeout_sec'] <= 20


@pytest.mark.parametrize('reason', ['Worker timed out', 'Worker memory limit exceeded'])
def test_count_model_resource_failure_is_retained_without_de(tmp_path, monkeypatch, reason):
    from devharness_core.watchdog import HarnessResourceInterrupt
    m = api()
    patch_preflight(monkeypatch, m, tmp_path)
    fake_engine(monkeypatch, m)
    def reject(*args, **kwargs):
        raise HarnessResourceInterrupt(reason)
    monkeypatch.setattr(m, '_run_count_model', reject)
    record = read_result(m.run_fastq_plan({}, tmp_path / 'runs'))
    assert record['status'] == 'failed'
    assert record['failure_stage'] == 'count_model'
    assert record['count_model_execution'] == 'failed'
    assert record['artifacts'] == {}
    assert reason in record['reason']


def test_standard_profile_keeps_upstream_qc_defaults(tmp_path):
    m = api()
    state = frozen(tmp_path)
    attempt = tmp_path / 'attempt'
    attempt.mkdir()
    m._prepare_engine(state, attempt, m._resources(None, 60), 'standard')
    params = json.loads((attempt / 'params.json').read_text())
    for key in ['skip_qc', 'skip_trimming', 'skip_markduplicates', 'skip_multiqc']:
        assert params[key] is False
    assert params['skip_alignment'] is False
    with pytest.raises(ValueError):
        m._execution_profile('arbitrary-nextflow-flags')


def test_expired_before_engine_does_not_count_a_launch(tmp_path, monkeypatch):
    from devharness_core.watchdog import HarnessResourceInterrupt
    m = api()
    patch_preflight(monkeypatch, m, tmp_path)
    monkeypatch.setattr(m, '_execute_command', lambda *args, **kwargs: pytest.fail('Expired deadline launched engine'))
    def expired(deadline):
        raise HarnessResourceInterrupt('deadline expired before engine')
    monkeypatch.setattr(m, '_remaining', expired)
    record = read_result(m.run_fastq_plan({}, tmp_path / 'runs'))
    assert record['status'] == 'refused'
    assert record['engine_launch_count'] == 0
    assert record['engine_launch_attempt_count'] == 0
    assert record['engine_execution'] == 'not_run'
    assert record['engine_session_id'] is None


def test_popen_failure_records_intent_without_started_process(tmp_path, monkeypatch):
    m = api()
    patch_preflight(monkeypatch, m, tmp_path)
    def failed_spawn(*args, **kwargs):
        raise OSError('Executable disappeared between admission and spawn')
    monkeypatch.setattr(m.subprocess, 'Popen', failed_spawn)
    record = read_result(m.run_fastq_plan({}, tmp_path / 'runs'))
    assert record['status'] == 'refused'
    assert record['engine_launch_count'] == 0
    assert record['engine_launch_attempt_count'] == 1
    assert record['engine_execution'] == 'launch_failed'
    assert record['engine_session_id'] is None
    assert record['artifacts'] == {}


def test_sample_named_gene_id_preserves_positional_header(tmp_path):
    m = api()
    folder = tmp_path / 'quant' / 'star_salmon' / 'log'
    star(folder / 'gene_id.ReadsPerGene.out.tab')
    output = tmp_path / 'counts.tsv'
    result = m.merge_star_gene_counts(tmp_path / 'quant', [{'sample': 'gene_id', 'strandedness': 'unstranded'}], output)
    assert result['sample_count'] == 1
    assert output.read_text().splitlines()[0] == 'gene_id\tgene_id'
    assert output.read_text().splitlines()[1] == 'g1\t0'
