"""Closed hosted exporter child, with default-off admission and child-only secrets.

The exact existing Keychain item is selected by an opaque constant, never key
bytes. Legacy /1 remains blocked. The separately reviewed /2 bootstrap requires
independent installed identities and trusted operator handles before resolving
that item; its local integrity records do not authenticate human consent.

Clean environments and closed descriptors provide process separation; this is
not a sandbox against another process running under the same OS identity.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import sys
import time
import unicodedata

from devbench.study.rna_hosted_qualification import (
    FakeExporterChannel, IPC_BYTES, _absolute, _canonical, _command, minimal_environment)
from devbench.study.rna_hosted_preflight import (
    validate_hosted_action, validate_operator_approval)

_CHILD_PROCESS_PID = None
_SECRET_BYTES = 4096
_SECRET_TIMEOUT_SECONDS = 5
_NATIVE_PYTHON = '/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.8/bin/python3.8'
_CREDENTIAL_FAILURE_CODES = frozenset({'credential_runtime_rejected','credential_native_unavailable',
    'credential_default_unavailable','credential_item_not_found','credential_authorization_denied',
    'credential_interaction_denied','credential_lookup_rejected','credential_output_rejected',
    'credential_lookup_timeout','credential_helper_failed'})
_NATIVE_FAILURE_CODES = {20:'credential_runtime_rejected',21:'credential_native_unavailable',
    22:'credential_default_unavailable',23:'credential_item_not_found',24:'credential_authorization_denied',
    25:'credential_interaction_denied',26:'credential_lookup_rejected',27:'credential_output_rejected'}
_HANDLE = {'format': 'devbench.rna-exporter-credential-handle/1',
           'handle': 'langsmith-bio-harness'}
_STATUS_KEYS = {'format', 'status', 'blockers', 'credential_resolutions',
                'authorize_upload', 'authorize_remote_read'}
_BOUNDARY_KEYS = {'pid', 'credential_resolutions', 'tracing_enabled', 'inherited_context'}
_BLOCKERS = {'unsupported_schema', 'independent_source_runtime_gate_unverified',
             'authenticated_operator_approval_unverified', 'separate_action_approval_absent'}


def keychain_credential_handle() -> dict:
    """Select only the existing reviewed item without consulting the host."""
    return dict(_HANDLE)


def _validate_handle(value) -> None:
    if not isinstance(value, dict) or value != _HANDLE:
        raise ValueError('Closed credential handle required')


def _validate_descriptor(value) -> dict:
    try:
        if isinstance(value,dict) and value.get('format') == 'devbench.rna-hosted-context-readback-session/1':
            from .rna_hosted_context_readback import validate_context_readback_descriptor
            return validate_context_readback_descriptor(value)
        if isinstance(value,dict) and value.get('format') == 'devbench.rna-hosted-exporter-session/5':
            from .rna_hosted_workflow import validate_generation_context_descriptor
            return validate_generation_context_descriptor(value)
        if isinstance(value,dict) and value.get('format') == 'devbench.rna-hosted-exporter-session/4':
            from .rna_hosted_workflow import validate_context_descriptor
            return validate_context_descriptor(value)
        if isinstance(value,dict) and value.get('format') == 'devbench.rna-hosted-exporter-session/3':
            from .rna_hosted_workflow import validate_descriptor
            return validate_descriptor(value)
        if isinstance(value,dict) and value.get('format') == 'devbench.rna-hosted-exporter-session/2':
            from .rna_hosted_connection import validate_descriptor
            return validate_descriptor(value)
        if not isinstance(value, dict) or set(value) != {'format', 'proposal', 'approval_record'}:
            raise ValueError()
        if value['format'] != 'devbench.rna-hosted-exporter-session/1':
            raise ValueError()
        if len(_canonical(value)) > 65536:
            raise ValueError()
        proposal = value['proposal']
        now = datetime.now(timezone.utc)
        validate_hosted_action(proposal, expected_binding=proposal['binding'],
                               expected_target=proposal['target'], now=now)
        if value['approval_record'] is not None:
            validate_operator_approval(value['approval_record'], proposal=proposal,
                expected_binding=proposal['binding'], expected_target=proposal['target'], now=now)
        return json.loads(_canonical(value))
    except Exception:
        # Do not expose arbitrary rejected input, JSON exceptions or chained data.
        raise ValueError('Closed bounded exporter descriptor required') from None


def _initialize(descriptor, credential_handle) -> dict:
    """Validate integrity only; neither local hashes nor flags admit a key read."""
    _validate_handle(credential_handle)
    descriptor = _validate_descriptor(descriptor)
    from devbench.study.rna_langsmith_verify import hosted_schema_qualified
    blockers = ['independent_source_runtime_gate_unverified',
                'authenticated_operator_approval_unverified']
    if not hosted_schema_qualified():
        blockers.insert(0, 'unsupported_schema')
    if descriptor['approval_record'] is None:
        blockers.append('separate_action_approval_absent')
    # There is deliberately no resolver/transport call here. An authenticated
    # exact-action admission contract and independent schema review are pending.
    return dict(format='devbench.rna-hosted-exporter-status/1', status='blocked',
                blockers=blockers, credential_resolutions=0,
                authorize_upload=False, authorize_remote_read=False)


class CredentialUnavailable(ValueError):
    """Closed reason only; never native stderr, provider data, or credential text."""
    def __init__(self, reason_code):
        if type(reason_code) is not str or reason_code not in _CREDENTIAL_FAILURE_CODES:
            raise ValueError('Closed credential failure reason required') from None
        self.reason_code=reason_code
        super().__init__('Exporter credential unavailable')


def _native_credential_command() -> list[str]:
    return [_NATIVE_PYTHON,'-I','-S','-B',str(Path(__file__).resolve().with_name('rna_keychain_native_helper.py'))]


def _native_credential_environment() -> dict[str,str]:
    return {'PATH':'/usr/bin:/bin'}


def _validate_native_credential_runtime() -> None:
    """Verify packaged helper/native/119 public stdlib pins before any key read."""
    try:
        from .rna_keychain_native_runtime import NATIVE_RUNTIME
        pins=NATIVE_RUNTIME
        if (not isinstance(pins,dict) or set(pins)!={'format','executable','python_sha256','helper_sha256',
                'flags','environment','stdlib_module_files'} or pins['format']!='devbench.rna-native-keychain-runtime/1'
                or pins['executable']!=_NATIVE_PYTHON or pins['flags']!=['-I','-S','-B']
                or pins['environment']!=_native_credential_environment()):
            raise ValueError()
        total=0
        def verify(path,expected):
            nonlocal total
            path=_absolute(path)
            if type(expected) is not str or re.fullmatch('[a-f0-9]{64}',expected) is None:raise ValueError()
            descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            h=hashlib.sha256()
            with os.fdopen(descriptor,'rb') as stream:
                before=os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size>16*1024*1024:raise ValueError()
                count=0
                while True:
                    chunk=stream.read(65536)
                    if not chunk:break
                    count+=len(chunk);total+=len(chunk)
                    if count>16*1024*1024 or total>128*1024*1024:raise ValueError()
                    h.update(chunk)
                after=os.fstat(stream.fileno())
                if count!=before.st_size or any(getattr(before,k)!=getattr(after,k)
                        for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')):raise ValueError()
            if h.hexdigest()!=expected:raise ValueError()
        command=_native_credential_command()
        verify(Path(command[0]),pins['python_sha256'])
        verify(Path(command[-1]),pins['helper_sha256'])
        modules=pins['stdlib_module_files']
        if not isinstance(modules,dict) or len(modules)!=119:raise ValueError()
        native_root=Path(_NATIVE_PYTHON).parent.parent
        for record in modules.values():
            if not isinstance(record,dict) or set(record)!={'path','sha256'}:raise ValueError()
            path=_absolute(record['path'])
            if not path.is_relative_to(native_root):raise ValueError()
            verify(path,record['sha256'])
    except Exception:
        raise CredentialUnavailable('credential_runtime_rejected') from None


def _resolve_keychain_credential() -> str:
    """Private child-only, bounded static Keychain read; never called at startup.

    No service/account/path, environment, raw key or callback is accepted. The
    caller must retain the returned value only inside the trusted child process.
    """
    if _CHILD_PROCESS_PID != os.getpid():
        raise PermissionError('Exporter child credential scope required')
    process = None
    try:
        _validate_native_credential_runtime()
        process = subprocess.Popen(
            _native_credential_command(),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=_native_credential_environment(), close_fds=True, bufsize=0)
        os.set_blocking(process.stdout.fileno(), False)
        deadline = time.monotonic() + _SECRET_TIMEOUT_SECONDS
        raw = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise CredentialUnavailable('credential_lookup_timeout')
                try:
                    chunk = os.read(process.stdout.fileno(), min(1024, _SECRET_BYTES + 1 - len(raw)))
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > _SECRET_BYTES:
                    raise CredentialUnavailable('credential_output_rejected')
        code=process.wait(timeout=max(.001, deadline - time.monotonic()))
        if code != 0:
            raise CredentialUnavailable(_NATIVE_FAILURE_CODES.get(code,'credential_helper_failed'))
        try:credential = raw.decode('utf-8')
        except UnicodeError:raise CredentialUnavailable('credential_output_rejected') from None
        if credential.endswith('\n'):
            credential = credential[:-1]
        if not credential or credential.isspace() or any(unicodedata.category(c).startswith('C') for c in credential):
            raise CredentialUnavailable('credential_output_rejected')
        return credential
    except CredentialUnavailable as error:
        raise error from None
    except (TimeoutError,subprocess.TimeoutExpired):
        raise CredentialUnavailable('credential_lookup_timeout') from None
    except Exception:
        raise CredentialUnavailable('credential_helper_failed') from None
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=1)
            except Exception:
                pass
            try:
                process.stdout.close()
            except Exception:
                pass


def _child_command() -> list[str]:
    return _command() + ['--hosted-exporter-child']


def _parse_json(raw: bytes):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate IPC field')
            value[key] = item
        return value
    def nonfinite(_):
        raise ValueError('Nonfinite IPC value')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)


def _decode_response(raw: bytes, *, command: str, connection_v2: bool = False,
                     workflow_v3: bool = False, context_v4: bool = False, context_v5: bool = False,
                     context_readback: bool = False) -> dict:
    try:
        if not isinstance(raw, bytes) or len(raw) > IPC_BYTES or not raw.endswith(b'\n'):
            raise ValueError()
        value = _parse_json(raw)
        _canonical(value)
        if context_readback and command != 'boundary':
            from .rna_hosted_context_readback import validate_context_readback_status, validate_context_readback_verification
            if isinstance(value,dict) and value.get('format') == 'devbench.rna-hosted-context-readback-status/1':
                return validate_context_readback_status(value)
            if command == 'verify':
                return validate_context_readback_verification(value)
            raise ValueError()
        if context_v5 and command != 'boundary':
            from .rna_hosted_workflow import validate_generation_context_status, validate_generation_context_verification
            if isinstance(value, dict) and value.get('format') == 'devbench.rna-hosted-exporter-status/5':
                return validate_generation_context_status(value)
            if command == 'verify':
                return validate_generation_context_verification(value)
            raise ValueError()
        if context_v4 and command != 'boundary':
            from .rna_hosted_workflow import validate_context_status, validate_context_verification
            if isinstance(value, dict) and value.get('format') == 'devbench.rna-hosted-exporter-status/4':
                return validate_context_status(value)
            if command == 'verify':
                return validate_context_verification(value)
            raise ValueError()
        if workflow_v3 and command != 'boundary':
            from .rna_hosted_workflow import validate_status, validate_verification
            if isinstance(value,dict) and value.get('format') == 'devbench.rna-hosted-exporter-status/3':
                return validate_status(value)
            if command == 'verify':
                return validate_verification(value)
            raise ValueError()
        if connection_v2 and isinstance(value,dict) and value.get('format') == 'devbench.rna-hosted-exporter-status/2':
            from .rna_hosted_connection import validate_status
            return validate_status(value)
        if connection_v2 and isinstance(value,dict) and value.get('format') == 'devbench.rna-remote-reconciliation/1' and command=='verify':
            from .rna_hosted_trace_contract import validate_reconciliation
            return validate_reconciliation(value)
        keys = _BOUNDARY_KEYS if command == 'boundary' else _STATUS_KEYS
        if not isinstance(value, dict) or set(value) != keys:
            raise ValueError()
        if type(value['credential_resolutions']) is not int or value['credential_resolutions'] not in ((0,1) if command=='boundary' and (connection_v2 or workflow_v3 or context_v4 or context_v5 or context_readback) else (0,)):
            raise ValueError()
        if command == 'boundary':
            if type(value['pid']) is not int or value['pid'] <= 0:
                raise ValueError()
            if value['tracing_enabled'] is not False or value['inherited_context'] is not False:
                raise ValueError()
        else:
            if value['format'] != 'devbench.rna-hosted-exporter-status/1' or value['status'] != 'blocked':
                raise ValueError()
            if value['authorize_upload'] is not False or value['authorize_remote_read'] is not False:
                raise ValueError()
            if not isinstance(value['blockers'], list) or not value['blockers']:
                raise ValueError()
            if any(not isinstance(item, str) or item not in _BLOCKERS for item in value['blockers']):
                raise ValueError()
        return value
    except Exception:
        raise ValueError('Exporter response rejected') from None


class HostedExporterChannel(FakeExporterChannel):
    """Launch clean child; closed commands carry no raw key or provider response."""
    def __init__(self, *, root: Path, descriptor: dict, credential_handle: dict,
                 restart: bool = False) -> None:
        _validate_handle(credential_handle)
        descriptor = _validate_descriptor(descriptor)
        self.root = _absolute(root)
        self._connection_v2 = descriptor['format'] == 'devbench.rna-hosted-exporter-session/2'
        self._workflow_v3 = descriptor['format'] == 'devbench.rna-hosted-exporter-session/3'
        self._context_v4 = descriptor['format'] == 'devbench.rna-hosted-exporter-session/4'
        self._context_v5 = descriptor['format'] == 'devbench.rna-hosted-exporter-session/5'
        self._context_readback = descriptor['format'] == 'devbench.rna-hosted-context-readback-session/1'
        self._bounded_connection = self._connection_v2 or self._workflow_v3 or self._context_v4 or self._context_v5 or self._context_readback
        self._workflow_packet = descriptor['packet'] if self._workflow_v3 or self._context_v4 or self._context_v5 or self._context_readback else None
        if self._bounded_connection and self.root != _absolute(descriptor['packet']['evidence_path']):
            raise ValueError('Approved connection evidence root required')
        self._connection_started = time.monotonic()
        self._connection_expires = descriptor['packet']['expires_at'] if self._bounded_connection else None
        self._connection_seconds = descriptor['packet']['limits']['total_deadline_seconds'] if self._bounded_connection else None
        if restart:
            if not self.root.is_dir():
                raise ValueError('Retained exporter root required')
        elif not self._bounded_connection:
            self.root.mkdir(parents=True, exist_ok=False, mode=0o700)
        elif self.root.exists() or not self.root.parent.is_dir():
            raise ValueError('Fresh connection root and existing parent required')
        try:
            self.process = subprocess.Popen(_child_command(), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
                env=minimal_environment(), cwd=self.root.parent if self._bounded_connection else self.root, close_fds=True)
        except Exception:
            if not restart:
                try:
                    self.root.rmdir()
                except OSError:
                    pass
            raise ValueError('Exporter process unavailable') from None
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)
        self._closed = False
        try:
            self._send({'command': 'initialize', 'descriptor': descriptor,
                        'credential_handle': keychain_credential_handle()})
        except Exception:
            self.close()
            raise

    def _send(self, value: dict) -> dict:
        try:
            if self._bounded_connection:
                expires=datetime.fromisoformat(self._connection_expires.replace('Z','+00:00'))
                remaining=min(self._connection_seconds-(time.monotonic()-self._connection_started),
                    (expires-datetime.now(timezone.utc)).total_seconds())
                self._ipc_deadline_seconds=max(.001,remaining) if value['command'] not in ('status','boundary') else 3
            raw = self._exchange_bytes(value)
        except TimeoutError:
            raise TimeoutError('Exporter IPC deadline') from None
        except Exception:
            self.close()
            raise ValueError('Exporter IPC rejected') from None
        try:
            return _decode_response(raw, command=value['command'],connection_v2=self._connection_v2,
                                    workflow_v3=self._workflow_v3,context_v4=getattr(self, '_context_v4', False), context_v5=getattr(self, '_context_v5', False),
                                    context_readback=getattr(self, '_context_readback', False))
        except ValueError:
            self.close()
            raise ValueError('Exporter response rejected') from None

    def request(self, value: dict) -> dict:
        if getattr(self, '_context_readback', False):
            if type(value) is not dict or set(value) != {'command'} or value['command'] not in {'boundary','status','verify'}:
                raise ValueError('Closed GET-only readback IPC request required')
            return self._send(value)
        if getattr(self, '_context_v4', False) or getattr(self, '_context_v5', False):
            fields = {'boundary': {'command'}, 'status': {'command'}, 'verify': {'command'},
                      'emit': {'command', 'operation'}}
            if not isinstance(value, dict) or value.get('command') not in fields or set(value) != fields[value['command']]:
                raise ValueError('Closed hosted context IPC request required')
            if value['command'] == 'emit':
                from .rna_hosted_workflow import validate_context_wire_operation, validate_generation_context_wire_operation
                validator = validate_generation_context_wire_operation if getattr(self, '_context_v5', False) else validate_context_wire_operation
                value = {'command': 'emit', 'operation': validator(value['operation'], packet=self._workflow_packet)}
            return self._send(value)
        if getattr(self, '_workflow_v3', False):
            fields = {'boundary': {'command'}, 'status': {'command'}, 'verify': {'command'},
                      'emit': {'command', 'operation'}}
            if not isinstance(value,dict) or value.get('command') not in fields or set(value) != fields[value['command']]:
                raise ValueError('Closed hosted workflow IPC request required')
            if value['command'] == 'emit':
                from .rna_hosted_workflow import validate_wire_operation
                value = {'command': 'emit', 'operation': validate_wire_operation(value['operation'], packet=self._workflow_packet)}
            return self._send(value)
        if self._connection_v2:
            fields={name:{'command'} for name in ('boundary','status','prepare','drain','verify')}
            fields['reconcile']={'command','receipt','approval_handle'}
            if not isinstance(value,dict) or value.get('command') not in fields or set(value)!=fields[value['command']]:
                raise ValueError('Closed hosted IPC request required')
            if value['command']=='reconcile':
                try:
                    from .rna_hosted_trace_contract import validate_reconciliation, _closed
                    from .rna_hosted_connection_contract import read_approval_handle
                    from .langsmith_outbox import _digest, _uuid
                    validate_reconciliation(value['receipt'])
                    handle=value['approval_handle']
                    record=read_approval_handle(handle)
                    _closed(record,{'format','receipt_digest','approved','reviewer_alias'},'reviewed reconciliation')
                    if record['format']!='devbench.rna-remote-review/1' or record['approved'] is not True:
                        raise ValueError()
                    _digest(record['receipt_digest']);_uuid(record['reviewer_alias'])
                    if value['receipt']['reviewer_approval_digest']!=handle['sha256']:
                        raise ValueError()
                except Exception:
                    raise ValueError('Closed hosted IPC request required') from None
            return self._send(value)
        if not isinstance(value, dict) or set(value) != {'command'} or value['command'] not in ('boundary', 'status'):
            raise ValueError('Closed hosted IPC request required')
        return self._send(value)


def _encoded_response(value: dict, *, command: str, connection_v2: bool = False,
                      workflow_v3: bool = False, context_v4: bool = False, context_v5: bool = False,
                      context_readback: bool = False) -> bytes:
    """Validate before emission; parent validation cannot undo an IPC leak."""
    try:
        raw = _canonical(value) + b'\n'
        _decode_response(raw, command=command,connection_v2=connection_v2,workflow_v3=workflow_v3,context_v4=context_v4,context_v5=context_v5,context_readback=context_readback)
        return raw
    except Exception:
        return b'{"error":"Exporter operation rejected"}\n'


def _exporter_child() -> int:
    global _CHILD_PROCESS_PID
    _CHILD_PROCESS_PID = os.getpid()
    state = None
    connection = None
    workflow_v3 = False
    context_v4 = False
    context_v5 = False
    context_readback = False
    while True:
        try:
            raw = sys.stdin.buffer.readline(IPC_BYTES + 1)
        except BaseException:
            if connection is not None: connection.close()
            raise
        if not raw:
            if connection is not None: connection.close()
            return 0
        command = None
        try:
            if len(raw) > IPC_BYTES or not raw.endswith(b'\n'):
                raise ValueError()
            value = _parse_json(raw)
            if not isinstance(value, dict):
                raise ValueError()
            command = value.get('command')
            if command == 'initialize' and state is None and set(value) == {'command', 'descriptor', 'credential_handle'}:
                _validate_handle(value['credential_handle'])
                if isinstance(value['descriptor'], dict) and value['descriptor'].get('format') == 'devbench.rna-hosted-context-readback-session/1':
                    from .rna_hosted_context_readback import HostedContextReadback
                    connection = HostedContextReadback(value['descriptor'])
                    context_readback = True
                    state = connection.status()
                elif isinstance(value['descriptor'], dict) and value['descriptor'].get('format') == 'devbench.rna-hosted-exporter-session/5':
                    from .rna_hosted_workflow import HostedGenerationContextObservation
                    connection = HostedGenerationContextObservation(value['descriptor'])
                    context_v5 = True
                    state = connection.status()
                elif isinstance(value['descriptor'], dict) and value['descriptor'].get('format') == 'devbench.rna-hosted-exporter-session/4':
                    from .rna_hosted_workflow import HostedContextObservation
                    connection = HostedContextObservation(value['descriptor'])
                    context_v4 = True
                    state = connection.status()
                elif isinstance(value['descriptor'],dict) and value['descriptor'].get('format') == 'devbench.rna-hosted-exporter-session/3':
                    from .rna_hosted_workflow import HostedWorkflow
                    connection = HostedWorkflow(value['descriptor'])
                    workflow_v3 = True
                    state = connection.status()
                elif isinstance(value['descriptor'],dict) and value['descriptor'].get('format') == 'devbench.rna-hosted-exporter-session/2':
                    from .rna_hosted_connection import HostedConnection
                    connection = HostedConnection(value['descriptor'])
                    state = connection.status()
                else:
                    state = _initialize(value['descriptor'], value['credential_handle'])
                result = state
            elif command == 'status' and state is not None and set(value) == {'command'}:
                result = connection.status() if connection is not None else state
            elif command == 'boundary' and state is not None and set(value) == {'command'}:
                result = dict(pid=os.getpid(), credential_resolutions=connection.credential_resolutions if connection is not None else 0,
                    tracing_enabled=os.environ.get('LANGSMITH_TRACING') == 'true',
                    inherited_context=any(k in os.environ for k in ('LANGSMITH_PARENT', 'LANGCHAIN_SESSION')))
            elif connection is not None and context_readback and connection.admission['status']=='admitted':
                if command != 'verify' or set(value) != {'command'}:
                    raise ValueError()
                result = connection.status() if connection.status()['status'] == 'blocked' else connection.verify()
            elif connection is not None and (workflow_v3 or context_v4 or context_v5) and connection.admission['status']=='admitted':
                fields={'emit': {'command','operation'}, 'verify': {'command'}}
                if command not in fields or set(value) != fields[command]:
                    raise ValueError()
                if connection.status()['status'] == 'blocked':
                    result = connection.status()
                elif command == 'emit':
                    result = connection.emit(value['operation'])
                else:
                    result = connection.verify()
            elif connection is not None and connection.admission['status']=='admitted':
                fields={'prepare':{'command'},'drain':{'command'},'verify':{'command'},
                    'reconcile':{'command','receipt','approval_handle'}}
                if command not in fields or set(value)!=fields[command]: raise ValueError()
                if connection.status()['status']=='blocked':
                    result=connection.status()
                elif command in ('prepare','drain','verify') and set(value)=={'command'}:
                    result=getattr(connection,command)()
                elif command=='reconcile' and set(value)=={'command','receipt','approval_handle'}:
                    result=connection.reconcile(value['receipt'],value['approval_handle'])
                else: raise ValueError()
            else:
                raise ValueError()
        except Exception:
            result = {'error': 'Exporter operation rejected'}
        sys.stdout.buffer.write(_encoded_response(result, command=command,
            connection_v2=connection is not None and not workflow_v3 and not context_v4 and not context_v5 and not context_readback,
            workflow_v3=workflow_v3,context_v4=context_v4,context_v5=context_v5,context_readback=context_readback))
        sys.stdout.buffer.flush()


def main(argv=None) -> int:
    # No secret/selector/session CLI arguments exist. Input is the bounded pipe.
    argparse.ArgumentParser(description='Default-off hosted exporter child').parse_args(argv)
    return _exporter_child()


if __name__ == '__main__':
    raise SystemExit(main())
