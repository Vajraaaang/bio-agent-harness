"""Optional protected local lifecycle observations; never execution authority.

No environment flags, provider client, ambient context or scientific payload is
accepted. Separate immutable sidecars cross the trusted supervisor boundary.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid
from threading import Lock

_PROCESS_ALIASES = {}
_PROCESS_ALIAS_LOCK = Lock()


def _process_alias():
    pid = os.getpid()
    with _PROCESS_ALIAS_LOCK:
        return _PROCESS_ALIASES.setdefault(pid, str(uuid.uuid4())), pid

from .rna_trace_contract import (canonical_bytes, trace_digest, validate_trace_context,
                                 validate_trace_event, PHASES)


def _path(value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Observer requires absolute non-traversing paths')
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('Observer paths cannot contain symlinks')
    return path


def _empty_metrics():
    return dict(input_tokens=None, output_tokens=None, total_tokens=None,
                elapsed_seconds=None, peak_rss_mb=None, usage_basis='unavailable',
                provider_cost=dict(value=None, currency=None, basis='unavailable', source_alias=None))


class LocalTraceObserver:
    """Trusted-caller-only opt-in store, disjoint from scientific evidence.

    Constructor errors are explicit configuration errors. Subsequent observation
    errors never change execution and are counted locally when storage permits.
    """
    def __init__(self, path, *, source_digest, runtime_digest, policy_digest,
                 max_events=2000, max_bytes=32_000_000):
        self.path = _path(path)
        if type(max_events) is not int or not 2 <= max_events <= 2000:
            raise ValueError('Invalid observer event cap')
        if type(max_bytes) is not int or not 65536 <= max_bytes <= 32_000_000:
            raise ValueError('Invalid observer byte cap')
        self.max_events, self.max_bytes = max_events, max_bytes
        self.identities = dict(source_digest=source_digest, runtime_digest=runtime_digest,
                               policy_digest=policy_digest)
        validate_trace_context(dict(format='devbench.rna-trace-context/1', trace_id=str(uuid.uuid4()),
            span_id=str(uuid.uuid4()), parent_span_id=None, workflow_alias=str(uuid.uuid4()),
            attempt=1, role='coordinator', phase='workflow', invocation_binding_digest='0'*64,
            **self.identities))
        self.process_alias, self.process_id = _process_alias()
        self.failure_count = 0
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.exists():
            raise ValueError('Create a fresh observer store; use open_existing for recovery')
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with self._db() as db:
            db.execute('CREATE TABLE settings(value TEXT NOT NULL)')
            db.execute('INSERT INTO settings VALUES (?)', (json.dumps(dict(identities=self.identities,
                max_events=max_events, max_bytes=max_bytes)),))
            db.execute('CREATE TABLE contexts(key TEXT PRIMARY KEY, context TEXT NOT NULL)')
            db.execute('CREATE TABLE events(span TEXT NOT NULL, sequence INTEGER NOT NULL, workflow TEXT NOT NULL, event TEXT NOT NULL, PRIMARY KEY(span,sequence))')
        self.sidecar_dir = self.path.with_name(self.path.name + '.sidecars')
        self.sidecar_dir.mkdir(mode=0o700)

    @classmethod
    def open_existing(cls, path):
        obj = cls.__new__(cls)
        obj.path = _path(path)
        if not obj.path.is_file():
            raise ValueError('Observer store missing')
        with obj._db(readonly=True) as db:
            settings = json.loads(db.execute('SELECT value FROM settings').fetchone()[0])
        if set(settings) != {'identities','max_events','max_bytes'}:
            raise ValueError('Invalid observer settings')
        obj.identities = settings['identities']
        validate_trace_context(dict(format='devbench.rna-trace-context/1',trace_id=str(uuid.uuid4()),
            span_id=str(uuid.uuid4()),parent_span_id=None,workflow_alias=str(uuid.uuid4()),attempt=1,
            role='coordinator',phase='workflow',invocation_binding_digest='0'*64,**obj.identities))
        if (type(settings['max_events']) is not int or not 2<=settings['max_events']<=2000
                or type(settings['max_bytes']) is not int or not 65536<=settings['max_bytes']<=32_000_000):
            raise ValueError('Invalid retained observer bounds')
        obj.max_events, obj.max_bytes = settings['max_events'], settings['max_bytes']
        obj.sidecar_dir = obj.path.with_name(obj.path.name + '.sidecars')
        _path(obj.sidecar_dir)
        obj.process_alias, obj.process_id = _process_alias()
        obj.failure_count = 0
        return obj

    def _db(self, readonly=False):
        _path(self.path)
        return sqlite3.connect(self.path.as_uri() + '?mode=ro' if readonly else self.path,
                               uri=readonly, timeout=5)

    def _storage_size(self):
        paths = [self.path, self.path.with_name(self.path.name+'-journal'),
                 self.path.with_name(self.path.name+'-wal'), self.path.with_name(self.path.name+'-shm')]
        if getattr(self, 'sidecar_dir', None) is not None and self.sidecar_dir.exists():
            paths.extend(self.sidecar_dir.iterdir())
        return sum(_path(path).stat().st_size for path in paths if path.exists())

    def _admit_storage(self, db, *, extra_bytes=0):
        contexts=db.execute('SELECT count(*) FROM contexts').fetchone()[0]
        if (contexts >= self.max_events
                or self._storage_size()+contexts*4096+extra_bytes+32768 >= self.max_bytes
                or db.execute("SELECT 1 FROM events WHERE json_extract(event,'$.reason_code')='byte_cap' LIMIT 1").fetchone()):
            raise ValueError('Observer storage cap exhausted')

    def _context(self, db, workflow, role, phase, attempt, binding_digest):
        key = trace_digest([workflow, role, phase, attempt, binding_digest])
        found = db.execute('SELECT context FROM contexts WHERE key=?',(key,)).fetchone()
        if found:
            context = validate_trace_context(json.loads(found[0]))
            if any(context[name] != value for name,value in self.identities.items()):
                raise ValueError('Observer identity changed')
            return context
        self._admit_storage(db, extra_bytes=4096)
        if role == 'coordinator' and phase == 'workflow':
            parent, trace, alias = None, str(uuid.uuid4()), str(uuid.uuid4())
        else:
            root = self._context(db, workflow, 'coordinator', 'workflow', 1, trace_digest(workflow))
            parent, trace, alias = root['span_id'],root['trace_id'],root['workflow_alias']
        context = validate_trace_context(dict(format='devbench.rna-trace-context/1',
            trace_id=trace, span_id=str(uuid.uuid4()), parent_span_id=parent, workflow_alias=alias,
            attempt=attempt,role=role,phase=phase,invocation_binding_digest=binding_digest,**self.identities))
        db.execute('INSERT INTO contexts VALUES (?,?)',(key,canonical_bytes(context).decode()))
        return context

    def _emit(self, context, workflow_digest, kind, status, *, phase=None,
              dispatch='unknown', capture='unavailable', reason='none'):
        import time
        parent = context['parent_span_id']
        if parent is not None:
            with self._db(readonly=True) as db:
                row = db.execute("SELECT context FROM contexts WHERE json_extract(context,'$.span_id')=?",(parent,)).fetchone()
                exists = db.execute('SELECT 1 FROM events WHERE span=? LIMIT 1',(parent,)).fetchone()
            if row and not exists:
                self._emit(json.loads(row[0]),workflow_digest,'reservation','active',dispatch='prepared')
        if self.process_id != os.getpid():
            self.process_alias, self.process_id = _process_alias()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if context['phase']=='workflow' and kind=='reservation' and db.execute('SELECT 1 FROM events WHERE span=? LIMIT 1',(context['span_id'],)).fetchone():
                return
            registered = db.execute('SELECT context FROM contexts WHERE json_extract(context,\'$.span_id\')=?', (context['span_id'],)).fetchone()
            if not registered or json.loads(registered[0]) != context:
                raise ValueError('Observer topology context changed')
            count = db.execute('SELECT count(*) FROM events WHERE workflow=?',(workflow_digest,)).fetchone()[0]
            if count >= self.max_events:
                return
            size = db.execute('SELECT coalesce(sum(length(event)),0) FROM events').fetchone()[0]
            if count == self.max_events-1 or size >= self.max_bytes-4096 or self._storage_size()+db.execute('SELECT count(*) FROM contexts').fetchone()[0]*4096+32768>=self.max_bytes:
                kind,status,reason = 'gap','incomplete','event_cap' if count == self.max_events-1 else 'byte_cap'
            sequence = db.execute('SELECT coalesce(max(sequence)+1,0) FROM events WHERE span=?',(context['span_id'],)).fetchone()[0]
            event = validate_trace_event(dict(format='devbench.rna-trace-event/1',
                event_id=str(uuid.uuid4()),trace_id=context['trace_id'],span_id=context['span_id'],
                parent_span_id=context['parent_span_id'],sequence=sequence,attempt=context['attempt'],
                role=context['role'],phase=phase or context['phase'],kind=kind,source_status=status,
                dispatch_status=dispatch,capture_status=capture,
                clock=dict(utc=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
                    monotonic_ns=time.monotonic_ns(),process_alias=self.process_alias,timing_basis='observed'),
                bindings=dict(workflow=workflow_digest,invocation=context['invocation_binding_digest'],
                    source=context['source_digest'],runtime=context['runtime_digest'],policy=context['policy_digest']),
                metrics=_empty_metrics(),reason_code=reason,origin='optional_observer_receipt'))
            db.execute('INSERT INTO events VALUES (?,?,?,?)',(context['span_id'],sequence,workflow_digest,canonical_bytes(event).decode()))

    def _gap(self, workflow_id):
        try:
            with self._db(readonly=True) as db:
                key=trace_digest([workflow_id,'coordinator','workflow',1,trace_digest(workflow_id)])
                row=db.execute('SELECT context FROM contexts WHERE key=?',(key,)).fetchone()
            if row:
                self._emit(json.loads(row[0]),trace_digest(workflow_id),'gap','incomplete',reason='observer_failure')
        except Exception:
            pass

    def observe_operation(self, workflow_id, node, attempt, request_digest, kind, status):
        """Coordinator ledger transition; contains no request/result bytes."""
        try:
            phase = node if node in PHASES else 'invocation'
            role = 'numerical' if node in {'run','execute','run_full'} else 'review' if node in {'verify','review'} else 'coordinator'
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                context = self._context(db, workflow_id,role,phase,attempt+1,request_digest)
            self._emit(context,trace_digest(workflow_id),kind,status,
                dispatch='not_dispatched' if kind=='reservation' else 'unknown')
        except Exception:
            self.failure_count += 1
            self._gap(workflow_id)

    def prepare_invocation(self, workflow_id, binding, reservation, *, role='lead'):
        """Allocate immutable sidecar from exact admitted invocation before launch."""
        try:
            if any(binding.get(name) != self.identities[name] for name in ('source_digest','runtime_digest')) or reservation.policy_digest != self.identities['policy_digest']:
                raise ValueError('Observer invocation identity mismatch')
            digest = reservation.request_digest
            if trace_digest(reservation.request) != digest:
                raise ValueError('Observer reservation binding mismatch')
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                phase = 'investigate' if role=='investigator' else binding.get('purpose', 'method_scientific_planning')
                if phase == 'scientific_review':
                    phase = 'review'
                if phase not in PHASES:
                    raise ValueError('Observer invocation purpose not approved')
                context = self._context(db, workflow_id,role,phase,reservation.attempt+1,digest)
            path = self.sidecar_dir / (context['span_id'] + '.json')
            value = dict(context=context,workflow_digest=trace_digest(workflow_id),observer_path=str(self.path))
            data = canonical_bytes(value)
            if len(data)>4096:
                raise ValueError('Observer sidecar byte cap')
            if path.exists():
                if path.read_bytes()!=data:
                    raise ValueError('Observer sidecar changed')
            else:
                with self._db() as db:
                    self._admit_storage(db, extra_bytes=len(data))
                fd=os.open(_path(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as handle:
                    handle.write(data)
            self._emit(context,value['workflow_digest'],'reservation','reserved',dispatch='prepared')
            return dict(path=str(path),sha256=hashlib.sha256(data).hexdigest(),binding_digest=digest,
                        **self.identities)
        except Exception:
            self.failure_count += 1
            self._gap(workflow_id)
            return None

    def complete_invocation(self, reference, *, reservation, status, capture_verified=False):
        if reference is None:
            return
        try:
            receipt = InvocationTraceReceipt(reference,binding_digest=reservation.request_digest,
                policy_digest=reservation.policy_digest, source_digest=self.identities['source_digest'],
                runtime_digest=self.identities['runtime_digest'],private_root=self.path.parent)
            if capture_verified:
                receipt.emit('capture',status,capture='verified',dispatch='attempted')
            receipt.emit('completion' if status=='completed' else 'failure',status,
                         capture='verified' if capture_verified else 'unavailable')
        except Exception:
            self.failure_count += 1

    def begin_interval(self, workflow_id, action):
        try:
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                root=self._context(db,workflow_id,'coordinator','workflow',1,trace_digest(workflow_id))
                attempt=db.execute("SELECT coalesce(max(json_extract(context,'$.attempt')),0)+1 FROM contexts WHERE json_extract(context,'$.trace_id')=? AND json_extract(context,'$.phase')='invocation'",(root['trace_id'],)).fetchone()[0]
                context=self._context(db,workflow_id,'coordinator','invocation',attempt,trace_digest([workflow_id,action,attempt]))
            self._emit(context,trace_digest(workflow_id),'reservation','active',dispatch='prepared')
            return root,context
        except Exception:
            self.failure_count += 1
            self._gap(workflow_id)
            return None

    def finish_interval(self, workflow_id, interval, status):
        if interval is None:
            return
        try:
            root,context=interval
            status='paused' if status=='review_required' else status
            if status not in {'completed','abstained','failed','cancelled','paused','ready','reconciliation_required'}:
                status='uncertain'
            kind='pause' if status=='paused' else 'cancel' if status=='cancelled' else 'failure' if status in {'failed','uncertain','reconciliation_required'} else 'completion'
            self._emit(context,trace_digest(workflow_id),kind,status)
            self._emit(root,trace_digest(workflow_id),kind if status in {'completed','abstained','failed','cancelled','paused'} else 'reconcile',status)
        except Exception:
            self.failure_count += 1
            self._gap(workflow_id)

    def read_contexts(self, workflow_id):
        with self._db(readonly=True) as db:
            key=trace_digest([workflow_id,'coordinator','workflow',1,trace_digest(workflow_id)])
            row=db.execute('SELECT context FROM contexts WHERE key=?',(key,)).fetchone()
            if row is None:
                return []
            trace=validate_trace_context(json.loads(row[0]))['trace_id']
            return [validate_trace_context(json.loads(item[0])) for item in db.execute(
                "SELECT context FROM contexts WHERE json_extract(context,'$.trace_id')=?",(trace,))]

    def read_events(self, workflow_id):
        with self._db(readonly=True) as db:
            return [validate_trace_event(json.loads(row[0])) for row in db.execute(
                'SELECT event FROM events WHERE workflow=? ORDER BY rowid',(trace_digest(workflow_id),))]


class InvocationTraceReceipt:
    """Validated explicit per-worker context. No inherited SDK state is read."""
    def __init__(self, reference, *, binding_digest, policy_digest, source_digest, runtime_digest, private_root):
        if type(reference) is not dict or set(reference)!={'path','sha256','binding_digest','policy_digest','source_digest','runtime_digest'}:
            raise ValueError('Invalid observer sidecar reference')
        path=_path(reference['path'])
        root=_path(private_root)
        if not path.is_relative_to(root) or path.stat().st_size>4096 or path.stat().st_mode & 0o077:
            raise ValueError('Observer sidecar must be bounded private storage')
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=reference['sha256']:
            raise ValueError('Observer sidecar integrity mismatch')
        value=json.loads(data)
        if set(value)!={'context','workflow_digest','observer_path'}:
            raise ValueError('Invalid observer sidecar')
        self.context=validate_trace_context(value['context'])
        for name,expected in dict(invocation_binding_digest=binding_digest,policy_digest=policy_digest,
                                   source_digest=source_digest,runtime_digest=runtime_digest).items():
            if self.context[name]!=expected or reference['binding_digest' if name=='invocation_binding_digest' else name]!=expected:
                raise ValueError('Observer invocation binding mismatch')
        observer_path=_path(value['observer_path'])
        if not observer_path.is_relative_to(root) or path.parent != observer_path.with_name(observer_path.name+'.sidecars'):
            raise ValueError('Observer sidecar topology storage mismatch')
        self.observer=LocalTraceObserver.open_existing(observer_path)
        self.workflow_digest=value['workflow_digest']
        # Existence and exact content of parent/context checked at every emit.
        with self.observer._db(readonly=True) as db:
            parent=self.context['parent_span_id']
            rows=[json.loads(row[0]) for row in db.execute('SELECT context FROM contexts')]
        if self.context not in rows or not any(row['span_id']==parent and row['trace_id']==self.context['trace_id'] and row['workflow_alias']==self.context['workflow_alias'] for row in rows):
            raise ValueError('Observer topology parent absent')

    def emit(self,kind,status,*,phase=None,dispatch='unknown',capture='unavailable',reason='none'):
        try:
            context=self.context
            if phase is not None and phase != context['phase']:
                key=trace_digest([context['span_id'],phase])
                with self.observer._db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    row=db.execute('SELECT context FROM contexts WHERE key=?',(key,)).fetchone()
                    if row:
                        context=validate_trace_context(json.loads(row[0]))
                    else:
                        self.observer._admit_storage(db,extra_bytes=4096)
                        context=validate_trace_context({**context,'span_id':str(uuid.uuid4()),
                            'parent_span_id':context['span_id'],'phase':phase})
                        db.execute('INSERT INTO contexts VALUES (?,?)',(key,canonical_bytes(context).decode()))
            self.observer._emit(context,self.workflow_digest,kind,status,phase=None,
                                dispatch=dispatch,capture=capture,reason=reason)
        except Exception:
            self.observer.failure_count += 1


class ObservedWorkflow:
    """Scope optional hooks to active controller intervals and restore on exit.

    The shared ledger lock prevents concurrent controllers using the same
    runtime objects from accidentally borrowing each other's observer. Worker
    correlation remains an explicit sidecar argument, never ambient context.
    """
    def __init__(self, workflow, observer, actions, backend=None):
        from threading import RLock
        if not isinstance(observer, LocalTraceObserver) or actions.ledger.readonly:
            raise TypeError('Use a trusted local observer with writable stores')
        if backend is not None and not observer.path.is_relative_to(backend.methods.root):
            raise ValueError('Audited observer store requires coordinator-private storage')
        if observer.path == actions.ledger.path:
            raise ValueError('Observer must be disjoint from scientific ledger')
        self.workflow,self.observer,self.actions,self.backend=workflow,observer,actions,backend
        if not hasattr(actions.ledger,'_observer_scope_lock'):
            actions.ledger._observer_scope_lock=RLock()

    def __enter__(self):
        self.workflow.__enter__()
        return self

    def __exit__(self,exc_type,exc_value,traceback):
        return self.workflow.__exit__(exc_type,exc_value,traceback)

    def __getattr__(self,name):
        return getattr(self.workflow,name)

    def _invoke(self,name,*args,**kwargs):
        targets=[self.actions.ledger]
        if self.backend is not None:
            targets.append(self.backend)
        with self.actions.ledger._observer_scope_lock:
            previous=[getattr(target,'trace_observer',None) for target in targets]
            try:
                for target in targets:
                    target.trace_observer=self.observer
                supplied = args[0] if args else kwargs.get('state',kwargs.get('workflow_id'))
                workflow_id = (supplied.get('workflow_id') if type(supplied) is dict
                    else supplied if type(supplied) is str else None)
                interval=self.observer.begin_interval(workflow_id,name) if workflow_id is not None else None
                try:
                    result=getattr(self.workflow,name)(*args,**kwargs)
                except BaseException:
                    if workflow_id is not None:
                        self.observer.finish_interval(workflow_id,interval,'uncertain')
                    raise
                if workflow_id is not None:
                    self.observer.finish_interval(workflow_id,interval,result.get('status','ready') if type(result) is dict else 'ready')
                return result
            finally:
                for target,old in zip(targets,previous):
                    target.trace_observer=old

    def start(self,*args,**kwargs):
        return self._invoke('start',*args,**kwargs)

    def resume(self,*args,**kwargs):
        return self._invoke('resume',*args,**kwargs)

    def reconcile(self,*args,**kwargs):
        return self._invoke('reconcile',*args,**kwargs)
