"""Local observer software fixtures never launch providers or qualify science."""
import copy
import multiprocessing
from types import SimpleNamespace
import pytest

from devbench.analysis.rna_trace_contract import trace_digest
from devbench.analysis.rna_trace_receipts import LocalTraceObserver, InvocationTraceReceipt
from devbench.analysis.workflow_operations import WorkflowOperationLedger

IDENTITIES=dict(source_digest='1'*64,runtime_digest='2'*64,policy_digest='3'*64)


def _observer(tmp_path,**kwargs):
    return LocalTraceObserver(tmp_path/'observer.sqlite',**IDENTITIES,**kwargs)


def _reservation(number=0):
    binding=dict(source_digest=IDENTITIES['source_digest'],runtime_digest=IDENTITIES['runtime_digest'],
                 invocation_id=str(number))
    reservation=SimpleNamespace(request=binding,request_digest=trace_digest(binding),attempt=number,
                                policy_digest=IDENTITIES['policy_digest'])
    return binding,reservation


def _receipt(reference,root):
    return InvocationTraceReceipt(reference,binding_digest=reference['binding_digest'],
        private_root=root,**IDENTITIES)


def _child_emit(reference,root):
    _receipt(reference,root).emit('dispatch','active',phase='selection',dispatch='attempted')


def test_default_off_and_exact_ledger_invariance(tmp_path):
    results=[]
    for enabled in [False,True]:
        directory=tmp_path/str(enabled)
        directory.mkdir()
        ledger=WorkflowOperationLedger(directory/'ledger.sqlite')
        observer=_observer(directory) if enabled else None
        ledger.trace_observer=observer
        calls=[]
        assert ledger.run('workflow','run',0,{'x':1},lambda: calls.append(1) or {'value':4})=={'value':4}
        assert ledger.run('workflow','run',0,{'x':1},lambda: pytest.fail('replayed operation dispatched'))=={'value':4}
        results.append(ledger.replay('workflow'))
        assert calls==[1]
        if enabled:
            assert [event['kind'] for event in observer.read_events('workflow')]==['reservation','reservation','completion']
        else:
            assert not (directory/'observer.sqlite').exists()
    assert results[0]==results[1]


def test_observer_failure_never_retries_or_changes_science(tmp_path):
    ledger=WorkflowOperationLedger(tmp_path/'ledger.sqlite')
    class Broken:
        def observe_operation(self,*args):
            raise OSError('fixture storage failure')
    ledger.trace_observer=Broken()
    calls=[]
    assert ledger.run('w','run',0,{},lambda: calls.append(1) or 42)==42
    assert calls==[1]


def test_concurrent_spawned_children_use_explicit_distinct_contexts(tmp_path):
    observer=_observer(tmp_path)
    references=[]
    for number in [0,1]:
        binding,reservation=_reservation(number)
        references.append(observer.prepare_invocation('workflow',binding,reservation,role='investigator'))
    context=multiprocessing.get_context('spawn')
    children=[context.Process(target=_child_emit,args=(reference,tmp_path)) for reference in references]
    for child in children: child.start()
    for child in children:
        child.join(20)
        assert child.exitcode==0
    events=observer.read_events('workflow')
    dispatched=[event for event in events if event['kind']=='dispatch']
    assert len(dispatched)==2
    assert len({event['span_id'] for event in dispatched})==2
    assert len({event['parent_span_id'] for event in dispatched})==2
    contexts={context['span_id']:context for context in observer.read_contexts('workflow')}
    assert len({contexts[event['parent_span_id']]['parent_span_id'] for event in dispatched})==1
    assert len({event['clock']['process_alias'] for event in dispatched})==2
    assert {event['bindings']['invocation'] for event in dispatched}=={ref['binding_digest'] for ref in references}


def test_resume_reuses_span_exact_attempt_and_keeps_new_attempt_separate(tmp_path):
    observer=_observer(tmp_path)
    binding,reservation=_reservation()
    reference=observer.prepare_invocation('w',binding,reservation)
    recovered=LocalTraceObserver.open_existing(observer.path)
    assert recovered.prepare_invocation('w',binding,reservation)==reference
    next_binding,next_reservation=_reservation(1)
    second=recovered.prepare_invocation('w',next_binding,next_reservation)
    assert reference['path']!=second['path']


@pytest.mark.parametrize('field',['binding_digest','policy_digest','source_digest','runtime_digest'])
def test_worker_rejects_mismatched_exact_binding(tmp_path,field):
    observer=_observer(tmp_path)
    binding,reservation=_reservation()
    reference=observer.prepare_invocation('w',binding,reservation)
    changed=copy.deepcopy(reference)
    changed[field]='9'*64
    with pytest.raises(ValueError,match='binding'):
        _receipt(changed,tmp_path)
    assert len(observer.read_events('w'))==2


def test_sidecar_extra_fields_tampering_and_symlink_reject(tmp_path):
    observer=_observer(tmp_path)
    binding,reservation=_reservation()
    reference=observer.prepare_invocation('w',binding,reservation)
    with pytest.raises(ValueError):
        _receipt({**reference,'baggage':'forbidden'},tmp_path)
    from pathlib import Path
    path=Path(reference['path'])
    path.write_bytes(b'{}')
    with pytest.raises(ValueError,match='integrity'):
        _receipt(reference,tmp_path)
    link=tmp_path/'linked.sqlite'
    link.symlink_to(observer.path)
    with pytest.raises(ValueError,match='symlink'):
        LocalTraceObserver.open_existing(link)


def test_caps_stop_context_sidecar_growth_and_leave_gap(tmp_path):
    observer=_observer(tmp_path,max_events=4)
    for number in range(20):
        observer.observe_operation('w','run',number,trace_digest(number),'reservation','active')
    events=observer.read_events('w')
    assert len(events)<=4
    assert observer.failure_count>0
    with observer._db(readonly=True) as db:
        assert db.execute('SELECT count(*) FROM contexts').fetchone()[0]<=4


def test_readonly_ledger_never_emits_observer_writes(tmp_path):
    observer=_observer(tmp_path)
    ledger=WorkflowOperationLedger(tmp_path/'ledger.sqlite')
    ledger.run('w','run',0,{},lambda:1)
    readonly=WorkflowOperationLedger(ledger.path,readonly=True)
    readonly.trace_observer=observer
    before=observer.path.read_bytes()
    assert readonly.replay('w')
    assert observer.path.read_bytes()==before


def test_failed_work_preserves_failure_without_success_or_retry(tmp_path):
    observer=_observer(tmp_path)
    ledger=WorkflowOperationLedger(tmp_path/'ledger.sqlite')
    ledger.trace_observer=observer
    def fail(): raise TimeoutError()
    with pytest.raises(TimeoutError): ledger.run('w','run',0,{},fail)
    assert [event['source_status'] for event in observer.read_events('w')]==['active','active','uncertain']


def test_phase_call_spans_are_nested_with_immutable_phases(tmp_path):
    observer=_observer(tmp_path)
    binding,reservation=_reservation()
    reference=observer.prepare_invocation('w',binding,reservation)
    receipt=_receipt(reference,tmp_path)
    for phase in ['permission_preflight','selection']:
        receipt.emit('dispatch','active',phase=phase,dispatch='attempted')
        receipt.emit('completion','returned_unvalidated',phase=phase,dispatch='attempted')
    events=observer.read_events('w')
    spans={}
    for event in events:
        spans.setdefault(event['span_id'],set()).add(event['phase'])
    assert all(len(phases)==1 for phases in spans.values())
    calls=[event for event in events if event['kind']=='dispatch']
    assert len({event['span_id'] for event in calls})==2
    assert {event['parent_span_id'] for event in calls}=={receipt.context['span_id']}


def test_observer_scope_restores_on_exit_and_failure(tmp_path):
    from devbench.analysis.rna_trace_receipts import ObservedWorkflow
    observer=_observer(tmp_path)
    ledger=WorkflowOperationLedger(tmp_path/'ledger.sqlite')
    actions=SimpleNamespace(ledger=ledger)
    backend=SimpleNamespace(trace_observer=None,methods=SimpleNamespace(root=tmp_path))
    class Workflow:
        def start(self,fail=False):
            assert ledger.trace_observer is observer
            assert backend.trace_observer is observer
            if fail: raise RuntimeError('fixture failure')
            return 7
    wrapped=ObservedWorkflow(Workflow(),observer,actions,backend)
    assert wrapped.start()==7
    assert ledger.trace_observer is None and backend.trace_observer is None
    with pytest.raises(RuntimeError): wrapped.start(fail=True)
    assert ledger.trace_observer is None and backend.trace_observer is None


def test_exporter_credentials_are_excluded_from_participant_environment(monkeypatch):
    from devbench.analysis.episode_codex import EpisodeCodexAdapter, CodexCLIAdapter
    canary={'LANGSMITH_API_KEY':'fixture-secret','LANGCHAIN_API_KEY':'fixture-secret',
            'OTEL_EXPORTER_OTLP_HEADERS':'fixture-secret','PATH':'fixture-path',
            'LANGSMITH_TRACING':'false','SAFE_SETTING':'fixture-safe'}
    monkeypatch.setattr(CodexCLIAdapter,'_environment',lambda self:dict(canary))
    adapter=EpisodeCodexAdapter.__new__(EpisodeCodexAdapter)
    from pathlib import Path
    adapter.workspace=Path('/private/tmp/fixture-workspace')
    adapter._model_capture=None
    adapter.python=Path('/private/tmp/fixture-python')
    adapter.boundary=SimpleNamespace(execution_environment=lambda environment: environment)
    environment=adapter._environment()
    assert not any(name.startswith(('LANGSMITH_','LANGCHAIN_','OTEL_')) for name in environment)
    assert environment['SAFE_SETTING']=='fixture-safe'
    assert 'fixture-path' in environment['PATH']
    assert canary['LANGSMITH_API_KEY']=='fixture-secret'


def test_active_intervals_resume_cancel_and_late_capture_preserve_same_root(tmp_path):
    observer=_observer(tmp_path)
    first=observer.begin_interval('w','start')
    observer.finish_interval('w',first,'review_required')
    recovered=LocalTraceObserver.open_existing(observer.path)
    second=recovered.begin_interval('w','resume')
    recovered.finish_interval('w',second,'cancelled')
    assert first[0]==second[0]
    assert first[1]['span_id']!=second[1]['span_id']
    binding,reservation=_reservation()
    reference=recovered.prepare_invocation('w',binding,reservation)
    recovered.complete_invocation(reference,reservation=reservation,status='completed',capture_verified=True)
    events=recovered.read_events('w')
    assert any(event['kind']=='pause' for event in events)
    assert any(event['kind']=='cancel' for event in events)
    assert events[-1]['source_status']=='completed'
    assert len({event['trace_id'] for event in events})==1
    assert all(event['clock']['monotonic_ns'] is not None for event in events)


@pytest.mark.parametrize('purpose',['method_capability_resolution','method_trial_design',
    'method_recommendation','method_candidates_reassessment'])
def test_lead_invocation_preserves_closed_actual_purpose(tmp_path,purpose):
    observer=_observer(tmp_path)
    binding,reservation=_reservation()
    binding['purpose']=purpose
    reservation.request_digest=trace_digest(binding)
    reference=observer.prepare_invocation('w',binding,reservation)
    assert _receipt(reference,tmp_path).context['phase']==purpose


def test_fake_native_adapter_observes_durable_dispatch_before_capture(tmp_path,monkeypatch):
    from devbench.analysis.episode_codex import EpisodeCodexAdapter, StudyCodeActionAdapter
    observer=_observer(tmp_path)
    binding,reservation=_reservation()
    reference=observer.prepare_invocation('w',binding,reservation,role='investigator')
    receipt=_receipt(reference,tmp_path)
    checkpoints=[]
    def fake_process(self,*args,**kwargs):
        self.coordinator_dispatch_callback({'stage':'model_dispatch'})
        return 0,'',''
    monkeypatch.setattr(StudyCodeActionAdapter,'_run_process',fake_process)
    adapter=EpisodeCodexAdapter.__new__(EpisodeCodexAdapter)
    adapter.trace_receipt=receipt
    adapter.protected_model_gate=None
    adapter.metadata={}
    callback=lambda event:checkpoints.append(event)
    adapter.coordinator_dispatch_callback=callback
    for phase in ['exec_preflight','action']:
        adapter._phase=phase
        assert adapter._run_process(['fixture-cli','exec'],tmp_path,{},'fixture',1)==(0,'','')
        assert adapter.coordinator_dispatch_callback is callback
        # Fixture capture verification is explicit; a zero process exit alone
        # remains returned_unvalidated and cannot close the scientific attempt.
        trace_phase='permission_preflight' if phase=='exec_preflight' else 'selection'
        receipt.emit('capture','completed',phase=trace_phase,dispatch='attempted',capture='verified')
    observer.complete_invocation(reference,reservation=reservation,status='completed',capture_verified=True)
    assert len(checkpoints)==2
    events=observer.read_events('w')
    dispatched=[event for event in events if event['kind']=='dispatch']
    assert {event['phase'] for event in dispatched}=={'permission_preflight','selection'}
    assert all(event['parent_span_id']==receipt.context['span_id'] for event in dispatched)
    for phase in ['permission_preflight','selection']:
        phase_events=[event for event in events if event['phase']==phase]
        assert [event['source_status'] for event in phase_events]==['active','returned_unvalidated','completed']
        assert phase_events[-1]['capture_status']=='verified'
    contexts=observer.read_contexts('w')
    assert all(context['source_digest']==IDENTITIES['source_digest'] for context in contexts)
    assert LocalTraceObserver.open_existing(observer.path).read_contexts('w')==contexts


def test_observed_workflow_context_manager_closes_on_failure(tmp_path):
    from devbench.analysis.rna_trace_receipts import ObservedWorkflow
    ledger=WorkflowOperationLedger(tmp_path/'ledger.sqlite')
    closed=[]
    class Workflow:
        def __enter__(self): return self
        def __exit__(self,*args): closed.append(args[0])
    observed=ObservedWorkflow(Workflow(),_observer(tmp_path),SimpleNamespace(ledger=ledger))
    with pytest.raises(RuntimeError):
        with observed as current:
            assert current is observed
            raise RuntimeError('fixture')
    assert closed==[RuntimeError]
    assert ledger.trace_observer is None


def test_observer_reopen_keeps_same_process_clock_identity(tmp_path):
    observer=_observer(tmp_path)
    recovered=LocalTraceObserver.open_existing(observer.path)
    assert observer.process_alias==recovered.process_alias
    assert observer.process_id==recovered.process_id


def test_active_observer_readonly_connection_uses_live_sqlite_locking(tmp_path):
    observer=_observer(tmp_path)
    before_files={path.name for path in tmp_path.iterdir()}
    before=observer.path.read_bytes()
    reader=observer._db(readonly=True)
    assert reader.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
    assert reader.execute('SELECT count(*) FROM events').fetchone()[0]==0
    assert observer.path.read_bytes()==before
    assert {path.name for path in tmp_path.iterdir()}==before_files
    observer.observe_operation('w','run',0,trace_digest('fixture'),'reservation','active')
    assert reader.execute('SELECT count(*) FROM events').fetchone()[0]==2
    with pytest.raises(Exception): reader.execute('CREATE TABLE forbidden(value TEXT)')
    reader.close()
    assert {path.name for path in tmp_path.iterdir()}==before_files


def _writer_after_signal(reference,root,ready,release):
    receipt=_receipt(reference,root)
    receipt.emit('dispatch','active',phase='selection',dispatch='attempted')
    ready.set()
    if not release.wait(10):
        raise RuntimeError('fixture parent release timed out')
    receipt.emit('capture','completed',phase='selection',dispatch='attempted',capture='verified')


def test_readonly_reader_observes_active_spawned_writer_without_immutable_cache(tmp_path):
    observer=_observer(tmp_path)
    binding,reservation=_reservation()
    reference=observer.prepare_invocation('w',binding,reservation)
    context=multiprocessing.get_context('spawn')
    ready,release=context.Event(),context.Event()
    child=context.Process(target=_writer_after_signal,args=(reference,tmp_path,ready,release))
    reader=observer._db(readonly=True)
    before=reader.execute('SELECT count(*) FROM events').fetchone()[0]
    files={path.name for path in tmp_path.iterdir()}
    child.start()
    try:
        assert ready.wait(20)
        assert reader.execute('SELECT count(*) FROM events').fetchone()[0]==before+1
        release.set()
        child.join(20)
        assert child.exitcode==0
        assert reader.execute('SELECT count(*) FROM events').fetchone()[0]==before+2
        assert {path.name for path in tmp_path.iterdir()}==files
    finally:
        release.set()
        child.join(20)
        reader.close()


def test_keyword_controller_invocations_retain_root_lifecycle(tmp_path):
    from devbench.analysis.rna_trace_receipts import ObservedWorkflow
    ledger=WorkflowOperationLedger(tmp_path/'ledger.sqlite')
    observer=_observer(tmp_path)
    class Workflow:
        def start(self,*,state): return {**state,'status':'review_required'}
        def resume(self,*,workflow_id): return {'workflow_id':workflow_id,'status':'completed'}
    observed=ObservedWorkflow(Workflow(),observer,SimpleNamespace(ledger=ledger))
    assert observed.start(state={'workflow_id':'w'})['status']=='review_required'
    assert observed.resume(workflow_id='w')['status']=='completed'
    root=[event for event in observer.read_events('w') if event['phase']=='workflow']
    assert [event['source_status'] for event in root]==['active','paused','completed']
    assert len({event['span_id'] for event in root})==1
    assert ledger.trace_observer is None
