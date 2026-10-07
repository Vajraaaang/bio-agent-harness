import json
import multiprocessing
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest


def _api():
    from devbench.analysis.workflow_operations import (
        OperationBlocked, OperationConflict, WorkflowOperationLedger,
    )
    return WorkflowOperationLedger, OperationBlocked, OperationConflict


def _crash(path):
    ledger = _api()[0](path)
    ledger.run('rna', 'fit', 0, {'dataset': 'counts-v1'}, lambda: os._exit(17))


def _process_run(path, ready, release):
    ledger = _api()[0](path)
    def callback():
        ready.set()
        release.wait(10)
        return {'artifact': 'fit-v1'}
    ledger.run('rna', 'fit', 0, {'dataset': 'counts-v1'}, callback)


def test_durable_completion_and_zero_callback_replay(tmp_path):
    Ledger, _, _ = _api()
    path = str(tmp_path / 'operations.sqlite')
    ledger = Ledger(path)
    calls = []
    assert ledger.run('rna', 'fit', 0, {'dataset': 'counts-v1'}, lambda: calls.append(1) or {'q': .2}) == {'q': .2}
    def forbidden():
        pytest.fail('checkpoint replay dispatched work')
    reopened = Ledger(path)
    assert reopened.run('rna', 'fit', 0, {'dataset': 'counts-v1'}, forbidden) == {'q': .2}
    records = reopened.replay('rna')
    assert records[0]['status'] == 'completed'
    assert calls == [1]


def test_same_key_different_inputs_conflict(tmp_path):
    Ledger, _, Conflict = _api()
    ledger = Ledger(str(tmp_path / 'operations.sqlite'))
    ledger.run('rna', 'fit', 0, {'dataset': 'v1'}, lambda: {})
    with pytest.raises(Conflict):
        ledger.run('rna', 'fit', 0, {'dataset': 'v2'}, lambda: pytest.fail())


def test_callback_exception_is_uncertain_and_not_automatically_retried(tmp_path):
    Ledger, Blocked, _ = _api()
    ledger = Ledger(str(tmp_path / 'operations.sqlite'))
    def fail():
        raise ValueError('secret exception detail')
    with pytest.raises(ValueError):
        ledger.run('rna', 'fit', 0, {}, fail)
    assert ledger.get('rna', 'fit', 0)['status'] == 'uncertain'
    assert 'secret' not in json.dumps(ledger.replay('rna'))
    with pytest.raises(Blocked) as error:
        ledger.run('rna', 'fit', 0, {}, lambda: pytest.fail())
    assert error.value.state == 'uncertain'
    assert ledger.reconcile('rna', 'fit', 0, {'verified_external_status': 'failed'}) == {'verified_external_status': 'failed'}


def test_known_scientific_failure_retained_as_completed_outcome(tmp_path):
    Ledger, _, _ = _api()
    ledger = Ledger(str(tmp_path / 'operations.sqlite'))
    outcome = {'scientific_status': 'failed', 'receipt_ref': 'run-1'}
    ledger.run('rna', 'fit', 0, {}, lambda: outcome)
    assert ledger.get('rna', 'fit', 0)['status'] == 'completed'
    assert ledger.run('rna', 'fit', 0, {}, lambda: pytest.fail()) == outcome


def test_failed_record_is_blocked_not_retried_or_reconciled(tmp_path):
    Ledger, Blocked, _ = _api()
    path = str(tmp_path / 'operations.sqlite')
    ledger = Ledger(path)
    ledger.run('rna', 'fit', 0, {}, lambda: {})
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE operations SET status='failed',result_json=NULL,result_digest=NULL")
    with pytest.raises(Blocked) as error:
        ledger.run('rna', 'fit', 0, {}, lambda: pytest.fail())
    assert error.value.state == 'failed'
    with pytest.raises(Blocked):
        ledger.reconcile('rna', 'fit', 0, {})


def test_crash_is_uncertain_and_requires_explicit_reconciliation(tmp_path):
    Ledger, Blocked, Conflict = _api()
    path = str(tmp_path / 'operations.sqlite')
    Ledger(path)
    process = multiprocessing.get_context('spawn').Process(target=_crash, args=(path,))
    process.start()
    process.join(15)
    assert process.exitcode == 17
    ledger = Ledger(path)
    with pytest.raises(Blocked) as error:
        ledger.run('rna', 'fit', 0, {'dataset': 'counts-v1'}, lambda: pytest.fail())
    assert error.value.state == 'uncertain'
    assert ledger.reconcile('rna', 'fit', 0, {'artifact': 'verified-existing-fit'}) == {'artifact': 'verified-existing-fit'}
    with pytest.raises(Conflict):
        ledger.reconcile('rna', 'fit', 0, {'artifact': 'different-fit'})


@pytest.mark.parametrize('cross_process', [False, True])
def test_concurrent_active_operation_is_not_dispatched_or_reconciled(tmp_path, cross_process):
    Ledger, Blocked, _ = _api()
    path = str(tmp_path / 'operations.sqlite')
    ledger = Ledger(path)
    context = multiprocessing.get_context('spawn')
    ready = context.Event() if cross_process else Event()
    release = context.Event() if cross_process else Event()
    if cross_process:
        worker = context.Process(target=_process_run, args=(path, ready, release))
        worker.start()
    else:
        pool = ThreadPoolExecutor(1)
        worker = pool.submit(_process_run, path, ready, release)
    try:
        assert ready.wait(15)
        with pytest.raises(Blocked) as error:
            ledger.run('rna', 'fit', 0, {'dataset': 'counts-v1'}, lambda: pytest.fail())
        assert error.value.state == 'active'
        with pytest.raises(Blocked):
            ledger.reconcile('rna', 'fit', 0, {})
    finally:
        release.set()
        if cross_process:
            worker.join(15)
            assert worker.exitcode == 0
        else:
            worker.result(15)
            pool.shutdown()


def test_cancellation_durable_and_retains_late_completion(tmp_path):
    Ledger, Blocked, _ = _api()
    path = str(tmp_path / 'operations.sqlite')
    ledger = Ledger(path)
    def callback():
        ledger.cancel('rna')
        return {'artifact': 'completed-despite-cancel-request'}
    ledger.run('rna', 'fit', 0, {}, callback)
    reopened = Ledger(path)
    assert reopened.is_cancelled('rna')
    assert reopened.run('rna', 'fit', 0, {}, lambda: pytest.fail())['artifact']
    with pytest.raises(Blocked) as error:
        reopened.run('rna', 'synthesis', 0, {}, lambda: pytest.fail())
    assert error.value.state == 'cancelled'


@pytest.mark.parametrize('operation_request', [{'x': float('nan')}, {'x': 'a' * 70000}, {1: 'invalid-key'}, {'x': Path('/tmp/file')}])
def test_invalid_request_cannot_dispatch(tmp_path, operation_request):
    Ledger, _, _ = _api()
    ledger = Ledger(str(tmp_path / 'operations.sqlite'))
    with pytest.raises(ValueError):
        ledger.run('rna', 'fit', 0, operation_request, lambda: pytest.fail())


def test_invalid_result_preserves_uncertainty(tmp_path):
    Ledger, Blocked, _ = _api()
    ledger = Ledger(str(tmp_path / 'operations.sqlite'))
    with pytest.raises(ValueError):
        ledger.run('rna', 'fit', 0, {}, lambda: {'x': float('inf')})
    assert ledger.get('rna', 'fit', 0)['status'] == 'uncertain'
    with pytest.raises(Blocked):
        ledger.run('rna', 'fit', 0, {}, lambda: pytest.fail())


def test_digest_tampering_rejected(tmp_path):
    Ledger, _, Conflict = _api()
    path = str(tmp_path / 'operations.sqlite')
    ledger = Ledger(path)
    ledger.run('rna', 'fit', 0, {}, lambda: {'q': .2})
    with sqlite3.connect(path) as connection:
        connection.execute('UPDATE operations SET result_json = ?', ('{"q":0}',))
    with pytest.raises(Conflict):
        ledger.get('rna', 'fit', 0)
    with pytest.raises(Conflict):
        ledger.run('rna', 'fit', 0, {}, lambda: pytest.fail())


def test_relative_ledger_path_rejected():
    Ledger, _, _ = _api()
    with pytest.raises(ValueError):
        Ledger('operations.sqlite')


@pytest.mark.parametrize('contents', [b'', b'broken sqlite'])
def test_existing_corrupt_ledger_never_reinitialized(tmp_path, contents):
    Ledger, _, Conflict = _api()
    path = tmp_path / 'operations.sqlite'
    path.write_bytes(contents)
    with pytest.raises(Conflict):
        Ledger(str(path))


def test_unrecognized_database_cannot_be_used(tmp_path):
    Ledger, _, Conflict = _api()
    path = tmp_path / 'operations.sqlite'
    with sqlite3.connect(str(path)) as connection:
        connection.execute('CREATE TABLE unrelated (value TEXT)')
    with pytest.raises(Conflict):
        Ledger(str(path))


def test_interruption_is_uncertain_not_failed(tmp_path):
    Ledger, Blocked, _ = _api()
    ledger = Ledger(str(tmp_path / 'operations.sqlite'))
    def interrupted():
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        ledger.run('rna', 'fit', 0, {}, interrupted)
    assert ledger.get('rna', 'fit', 0)['status'] == 'uncertain'
    with pytest.raises(Blocked):
        ledger.run('rna', 'fit', 0, {}, lambda: pytest.fail())


def test_replay_does_not_mutate_retained_database(tmp_path):
    Ledger, _, _ = _api()
    path = tmp_path / 'operations.sqlite'
    ledger = Ledger(str(path))
    ledger.run('rna', 'fit', 0, {}, lambda: {'artifact': 'fit-v1'})
    before = path.read_bytes()
    assert ledger.replay('rna')[0]['status'] == 'completed'
    assert path.read_bytes() == before


def test_missing_table_never_reinitialized(tmp_path):
    Ledger, _, Conflict = _api()
    path = tmp_path / 'operations.sqlite'
    Ledger(str(path))
    with sqlite3.connect(str(path)) as connection:
        connection.execute('DROP TABLE operations')
    with pytest.raises(Conflict):
        Ledger(str(path))


def test_parent_symlink_and_noncanonical_path_rejected(tmp_path):
    Ledger, _, _ = _api()
    target = tmp_path / 'actual'
    target.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        Ledger(str(alias / 'operations.sqlite'))
    with pytest.raises(ValueError):
        Ledger(str(target) + '/../actual/operations.sqlite')


def test_readonly_ledger_constructor_and_replay_never_mutate(tmp_path):
    Ledger, Blocked, _ = _api()
    path = tmp_path / 'operations.sqlite'
    ledger = Ledger(str(path))
    ledger.run('rna', 'fit', 0, {}, lambda: {'artifact': 'fit-v1'})
    before = {str(file): (file.read_bytes(), file.stat().st_mtime_ns) for file in tmp_path.rglob('*') if file.is_file()}
    readonly = Ledger(str(path), readonly=True)
    assert readonly.replay('rna')[0]['result'] == {'artifact': 'fit-v1'}
    assert readonly.get('rna', 'fit', 0)['status'] == 'completed'
    assert not readonly.is_cancelled('rna')
    for operation in (
        lambda: readonly.run('rna', 'fit', 0, {}, lambda: pytest.fail()),
        lambda: readonly.cancel('rna'),
        lambda: readonly.reconcile('rna', 'fit', 0, {}),
    ):
        with pytest.raises(Blocked) as error:
            operation()
        assert error.value.state == 'readonly'
    after = {str(file): (file.read_bytes(), file.stat().st_mtime_ns) for file in tmp_path.rglob('*') if file.is_file()}
    assert before == after


def test_readonly_missing_ledger_does_not_create_files(tmp_path):
    Ledger, _, Conflict = _api()
    with pytest.raises(Conflict):
        Ledger(str(tmp_path / 'missing' / 'operations.sqlite'), readonly=True)
    assert list(tmp_path.iterdir()) == []


def test_hardlink_alias_cannot_reconcile_live_callback(tmp_path):
    Ledger, _, Conflict = _api()
    path = tmp_path / 'operations.sqlite'
    alias = tmp_path / 'alias.sqlite'
    ledger = Ledger(str(path))
    def callback():
        os.link(path, alias)
        with pytest.raises(Conflict):
            Ledger(str(alias)).reconcile('rna', 'fit', 0, {'artifact': 'premature-fit'})
        alias.unlink()
        return {'artifact': 'real-fit'}
    assert ledger.run('rna', 'fit', 0, {}, callback) == {'artifact': 'real-fit'}
    assert ledger.replay('rna')[0]['result'] == {'artifact': 'real-fit'}


def test_hardlink_created_after_constructor_blocks_dispatch(tmp_path):
    Ledger, _, Conflict = _api()
    path = tmp_path / 'operations.sqlite'
    ledger = Ledger(str(path))
    os.link(path, tmp_path / 'alias.sqlite')
    with pytest.raises(Conflict):
        ledger.run('rna', 'fit', 0, {}, lambda: pytest.fail('hard-linked ledger dispatched'))


def test_terminal_mutation_during_callback_cannot_return_unretained_result(tmp_path):
    Ledger, _, Conflict = _api()
    path = tmp_path / 'operations.sqlite'
    ledger = Ledger(str(path))
    def callback():
        with sqlite3.connect(str(path)) as connection:
            connection.execute("UPDATE operations SET status='failed'")
        return {'artifact': 'real-fit'}
    with pytest.raises(Conflict):
        ledger.run('rna', 'fit', 0, {}, callback)
    assert ledger.get('rna', 'fit', 0)['status'] == 'failed'


def test_ledger_path_replacement_during_callback_rejected(tmp_path):
    Ledger, _, Conflict = _api()
    path = tmp_path / 'operations.sqlite'
    ledger = Ledger(str(path))
    replacement = tmp_path / 'replacement.sqlite'
    Ledger(str(replacement))
    def callback():
        os.replace(replacement, path)
        return {'artifact': 'unretained-fit'}
    with pytest.raises(Conflict):
        ledger.run('rna', 'fit', 0, {}, callback)


def test_legacy_v1_readonly_open_does_not_upgrade_budget_tables(tmp_path):
    Ledger, _, _ = _api()
    path = tmp_path / 'operations.sqlite'
    Ledger(path).run('legacy', 'fit', 0, {}, lambda: {'receipt': 'legacy'})
    before = path.read_bytes()
    readonly = Ledger(path, readonly=True)
    assert readonly.get_campaign('legacy') is None
    assert readonly.campaign_reservations('legacy') == []
    with sqlite3.connect(path) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 1
        assert {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {'workflows', 'operations'}
    assert path.read_bytes() == before


def test_partial_additive_budget_schema_never_reinitialized(tmp_path):
    Ledger, _, Conflict = _api()
    path = tmp_path / 'operations.sqlite'
    Ledger(path)
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE model_campaigns (campaign_id TEXT)')
    with pytest.raises(Conflict):
        Ledger(path)


def test_budget_tables_do_not_change_legacy_v1_columns(tmp_path):
    from tests.test_rna_investigation_budget import _open
    ledger = _open(tmp_path)
    with sqlite3.connect(ledger.path) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 1
        assert [row[1] for row in connection.execute('PRAGMA table_info(workflows)')] == ['workflow_id', 'cancelled']
        assert [row[1] for row in connection.execute('PRAGMA table_info(operations)')] == ['workflow_id', 'node', 'attempt', 'status', 'request_json', 'request_digest', 'result_json', 'result_digest', 'error_type']
    ledger.run('legacy', 'fit', 0, {}, lambda: {'receipt': 'legacy'})
    assert ledger.get('legacy', 'fit', 0)['result'] == {'receipt': 'legacy'}


def test_campaign_resource_hardlink_and_replacement_are_rejected(tmp_path):
    from tests.test_rna_investigation_budget import _open, _reserve
    ledger = _open(tmp_path)
    alias = tmp_path / 'alias.sqlite'
    os.link(ledger.path, alias)
    with pytest.raises(_api()[2]):
        _reserve(ledger)
    alias.unlink()
    replacement = tmp_path / 'replacement.sqlite'
    _api()[0](replacement)
    os.replace(replacement, ledger.path)
    with pytest.raises(_api()[2]):
        ledger.get_campaign('campaign')
