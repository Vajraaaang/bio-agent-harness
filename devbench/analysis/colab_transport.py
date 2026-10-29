"""Fixed, single-use official Colab transport. Never evaluates scientific code locally.

Receipts describe custody through the authenticated official client; they are not
Google-signed attestations. The Python process is trusted. There is no public
constructor from notebook JSON, nor a generic accepted-callback transport.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import selectors
import signal
import stat
import subprocess
import time
import zipfile

from devharness_core.provenance import canonical_json
from .colab_execution_contract import job_sha256, validate_job

PYTHON = Path('/opt/anaconda3/bin/python3.13')
CLI_PACKAGE = Path('/opt/anaconda3/lib/python3.13/site-packages/colab_cli')
PREPARATION = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-colab-integration/2026-10-22-v1/transport-preparation-v1')
PIN_MANIFEST = PREPARATION / 'official-client-pins.json'
PIN_MANIFEST_SHA256 = '3e32c4163ce4f8d84afe445445a20b5c533ab58ceef2cf41a8f8680d0faf17d8'
MAX_UPLOAD = 8_000_000
MAX_RESULT = 2_000_000
MAX_STREAM = 1_000_000
OVERALL_SECONDS = 300
COMMAND_SECONDS = 45
EXEC_SECONDS = 90
_MINT = object()

# These OS requests cap each official-client child where the host enforces
# them. In particular macOS address-space enforcement is not established by
# setrlimit succeeding. Qualification must not call this a proven hard RAM
# bound. No credential or raw exception is emitted by this wrapper.
_RESOURCE_WRAPPER = r'''
import os,resource,runpy,sys
try:
  for kind,limit in ((resource.RLIMIT_CORE,0),(resource.RLIMIT_FSIZE,2000000),
                     (resource.RLIMIT_CPU,120),(resource.RLIMIT_AS,1073741824)):
    resource.setrlimit(kind,(limit,limit))
except BaseException:
  raise SystemExit(77)
mode=sys.argv[1]
if mode=='cli':
  sys.argv=['colab',*sys.argv[2:]]
  runpy.run_module('colab_cli.cli',run_name='__main__')
elif mode=='observe':
  source=sys.argv[2];sys.argv=['opaque-observation',*sys.argv[3:]]
  exec(compile(source,'<fixed-owned-observation>','exec'),{'__name__':'__main__'})
else:
  raise SystemExit(78)
'''


def _absolute(path):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Absolute non-symlink custody path required')
    return path


def _hash(path, cap):
    path = _absolute(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > cap:
            raise ValueError('Bounded regular custody file required')
        value = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(fd, min(65536, cap + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > cap:
                raise ValueError('Custody file exceeds cap')
            value.update(chunk)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('Custody changed during hashing')
        return value.hexdigest()
    finally:
        os.close(fd)


def _private_file(path, body):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def _verify_pins():
    if _hash(PIN_MANIFEST, 100_000) != PIN_MANIFEST_SHA256:
        raise PermissionError('Official client pin manifest changed')
    value = json.loads(PIN_MANIFEST.read_bytes())
    if set(value) != {'format', 'version', 'files'} or value['format'] != 'devbench.colab-official-client-pins/1' or value['version'] != '0.6.0':
        raise PermissionError('Official client pin manifest invalid')
    for item in value['files']:
        if set(item) != {'path', 'sha256', 'size_bytes'} or _hash(item['path'], item['size_bytes']) != item['sha256']:
            raise PermissionError('Official client public source changed')


# Only this small helper touches the new owned CLI state. Raw endpoints/tokens,
# auth output, state and exceptions never cross its stdout boundary. It avoids
# sync_sessions/status: those may prune state or disguise auth failure as empty.
_OPAQUE_HELPER = r'''
import contextlib,json,logging,os,sys
from pathlib import Path
logging.disable(logging.CRITICAL)
operation,config,name,endpoint_file,cap=sys.argv[1:]
answer={'presence':'unknown'}
try:
  with open(os.devnull,'w') as sink,contextlib.redirect_stdout(sink),contextlib.redirect_stderr(sink):
    from colab_cli.common import state
    from colab_cli.auth import AuthProvider
    state.config_path=config;state.auth_provider=AuthProvider.OAUTH2
    target=Path(endpoint_file)
    if operation=='capture':
      entry=state.store.get(name)
      if entry is None: raise ValueError()
      endpoint=entry.endpoint
      fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
      with os.fdopen(fd,'w') as f: json.dump({'endpoint':endpoint},f)
    else:
      with open(target) as f: endpoint=json.load(f)['endpoint']
    if operation=='inventory':
      from colab_cli.contents import ContentsClient
      entry=state.store.get(name)
      if entry is None or entry.endpoint!=endpoint: raise ValueError()
      metadata=ContentsClient(entry)._request('GET','content/colab-result.zip',params={'content':'0'})
      size=metadata.get('size')
      if metadata.get('type')!='file' or type(size) is not int or not 0<size<=int(cap): raise ValueError()
      answer={'presence':'present','bounded_result_inventory':True}
    else:
      assignments=state.client.list_assignments()
      answer={'presence':'present' if any(a.endpoint==endpoint for a in assignments) else 'absent'}
except BaseException:
  pass
print(json.dumps(answer,separators=(',',':')))
'''


class ColabTransportObservation:
    """A fixed-channel observation minted by OfficialColabTransport only."""
    __slots__ = ('_body', '_signature', '_owner')

    def __init__(self, *, _token=None, body=None, owner=None):
        if _token is not _MINT or type(owner) is not OfficialColabTransport:
            raise PermissionError('Only the trusted transport may mint an observation')
        self._body = canonical_json(body)
        self._owner = owner
        self._signature = hmac.new(owner._witness_key, self._body, hashlib.sha256).digest()

    def verify(self, *, job, stage):
        job = validate_job(job)
        if stage not in {'dispatched', 'returned'} or type(self._owner) is not OfficialColabTransport:
            raise PermissionError('Invalid transport stage')
        expected = hmac.new(self._owner._witness_key, self._body, hashlib.sha256).digest()
        if not hmac.compare_digest(self._signature, expected):
            raise PermissionError('Transport witness changed')
        value = json.loads(self._body)
        if (value['stage'] != stage or value['job_id'] != job['job_id'] or
                value['job_sha256'] != job_sha256(job) or
                value['source_bundle_sha256'] != job['source_bundle_sha256']):
            raise PermissionError('Transport witness subject mismatch')
        witness = self._owner.private_root / ('witness-' + stage + '.json')
        fd = os.open(witness, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            meta = os.fstat(fd)
            if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or
                    stat.S_IMODE(meta.st_mode) != 0o600 or meta.st_nlink != 1 or
                    meta.st_size != len(self._body) or os.read(fd,len(self._body)+1) != self._body):
                raise PermissionError('Protected retained witness differs')
        finally:
            os.close(fd)
        return value


class OfficialColabTransport:
    """One fresh CPU assignment, one exec, one return, with explicit stop.

    Construction creates a new private HOME but performs no auth/network/job.
    The coordinator must arrange any Google authorization separately, preserving
    that HOME. There is no host credential discovery or browser cookie import.
    """
    def __init__(self, *, private_root, service):
        from .methods import MethodService
        if type(service) is not MethodService:
            raise TypeError('Exact MethodService required')
        self.service = service
        self.private_root = _absolute(private_root)
        self.private_root.mkdir(mode=0o700, parents=False, exist_ok=False)
        self.home = self.private_root / 'home'
        self.home.mkdir(mode=0o700)
        configdir = self.home / '.config'
        configdir.mkdir(mode=0o700)
        configdir = configdir / 'colab-cli'
        configdir.mkdir(mode=0o700)
        self.config = configdir / 'sessions.json'
        _private_file(configdir / 'settings.json', b'{"enable_update_check":false}')
        self._witness_key = secrets.token_bytes(32)
        _private_file(self.private_root / 'transport-witness.key', self._witness_key)
        self.session_name = 'devbench-' + secrets.token_hex(16)
        self.remote_session_id = secrets.token_hex(32)  # Public random identity, never an endpoint hash.
        self.dispatch_id = secrets.token_hex(16)
        self.state = 'new'
        self.events = []
        self.cleanup = {'stop_requested': False, 'presence': 'unknown'}
        self._deadline = None
        self._job = None

    def _run(self, arguments, *, seconds, capture=False, cleanup=False):
        _verify_pins()
        env = {'HOME': str(self.home), 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
               'TMPDIR': str(self.private_root), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'}
        now = time.monotonic()
        deadline = now + seconds
        if not cleanup:
            deadline = min(deadline, self._deadline)
        if deadline <= now:
            raise TimeoutError('Transport overall deadline spent')
        if arguments[:2] == ['-m', 'colab_cli.cli']:
            bounded_arguments = ['-c', _RESOURCE_WRAPPER, 'cli', *arguments[2:]]
        elif arguments[:2] == ['-c', _OPAQUE_HELPER]:
            bounded_arguments = ['-c', _RESOURCE_WRAPPER, 'observe', _OPAQUE_HELPER, *arguments[2:]]
        else:
            raise PermissionError('Only fixed official client and observation operations are supported')
        process = subprocess.Popen([str(PYTHON), '-I', '-B', *bounded_arguments], cwd=self.private_root,
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, umask=0o077)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, 'out')
        selector.register(process.stderr, selectors.EVENT_READ, 'err')
        count = 0
        public = bytearray()
        failure = None
        try:
            while selector.get_map():
                if time.monotonic() >= deadline:
                    failure = 'timeout'; break
                for key, _ in selector.select(min(.1, max(0, deadline - time.monotonic()))):
                    body = os.read(key.fileobj.fileno(), 8192)
                    if not body:
                        selector.unregister(key.fileobj); continue
                    count += len(body)
                    if count > MAX_STREAM:
                        failure = 'output_limit'; break
                    if capture and key.data == 'out':
                        if len(public) + len(body) > 512:
                            failure = 'projection_limit'; break
                        public.extend(body)
                if failure: break
            if failure:
                os.killpg(process.pid, signal.SIGKILL)
            returncode = process.wait(timeout=2)
        finally:
            selector.close()
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
            process.stdout.close(); process.stderr.close()
            self.events.append({'operation': arguments[6] if arguments[:2] == ['-m', 'colab_cli.cli'] else 'opaque_observation',
                                'exit_code': process.returncode, 'stream_bytes_discarded': count,
                                'failure': failure})
        if failure or returncode != 0:
            public.clear()
            raise RuntimeError('Official Colab command failed or exceeded bound')
        return bytes(public) if capture else None

    def _cli(self, command, *arguments, seconds=COMMAND_SECONDS, cleanup=False):
        accepted = command in {'new','stop'} and not arguments
        if command == 'upload':
            choices = {(str(self.bundle),'content/colab-source.zip'),
                       (str(self.private_root/'transport-context.json'),'content/transport-context.json')}
            accepted = arguments in choices
        elif command == 'exec':
            accepted = arguments == ('--timeout',str(EXEC_SECONDS),'-f',str(self.execution_file))
        elif command == 'download':
            accepted = arguments == ('content/colab-result.zip',str(self.private_root/'colab-result.zip'))
        if not accepted:
            raise PermissionError('Only the sealed fixed Colab lifecycle is supported')
        return self._run(['-m', 'colab_cli.cli', '--auth', 'oauth2', '--config', str(self.config),
                          command, '-s', self.session_name, *arguments], seconds=seconds, cleanup=cleanup)

    def _observe(self, operation, *, cleanup=False):
        body = self._run(['-c', _OPAQUE_HELPER, operation, str(self.config), self.session_name,
                          str(self.private_root / 'endpoint.private.json'), str(MAX_RESULT)],
                         seconds=COMMAND_SECONDS, capture=True, cleanup=cleanup)
        try:
            value = json.loads(body)
            keys = {'presence', 'bounded_result_inventory'} if operation == 'inventory' else {'presence'}
            if set(value) != keys or value['presence'] not in {'present', 'absent', 'unknown'}:
                raise ValueError()
            if operation == 'inventory' and value['bounded_result_inventory'] is not True:
                raise ValueError()
            return value['presence']
        except (ValueError, TypeError, KeyError):
            return 'unknown'

    def _mint(self, stage, *, archive_hash=None, root_hash=None):
        body = {'format': 'devbench.colab-transport-receipt/1', 'job_id': self._job['job_id'],
                'job_sha256': job_sha256(self._job), 'source_bundle_sha256': self._job['source_bundle_sha256'],
                'backend': 'google_colab', 'notebook_id': None, 'remote_session_id': self.remote_session_id,
                'dispatch_id': self.dispatch_id, 'stage': stage, 'result_archive_sha256': archive_hash,
                'expected_root_sha256': root_hash}
        _private_file(self.private_root / ('witness-' + stage + '.json'), canonical_json(body))
        return ColabTransportObservation(_token=_MINT, body=body, owner=self)

    def dispatch(self, job_id):
        if self.state != 'new':
            raise PermissionError('A transport is spent after its first dispatch attempt')
        self.state = 'spent'
        job, record = self.service._remote_job(job_id)
        if record['status'] != 'remote_pending' or not record.get('reservation') or not record.get('export'):
            raise PermissionError('A consumed exact-job approval and sealed export are required')
        self._job = validate_job(job)
        self._deadline = time.monotonic() + OVERALL_SECONDS
        self._bind_export(record['export'])
        self._check_claim(self.service.claim_remote_transport(job_id, dispatch_id=self.dispatch_id,
                                                              remote_session_id=self.remote_session_id))
        try:
            self._check_claim(self.service.revalidate_remote_dispatch(job_id))
            self._cli('new')
            if self._observe('capture') != 'present':
                raise RuntimeError('Authenticated remote assignment not confirmed')
            self._cli('upload', str(self.bundle), 'content/colab-source.zip')
            context = self.private_root / 'transport-context.json'
            _private_file(context, canonical_json({'remote_session_id': self.remote_session_id}))
            self._cli('upload', str(context), 'content/transport-context.json')
            self.state = 'dispatched'
            return self._mint('dispatched')
        except BaseException:
            self.stop()
            raise

    def _check_claim(self, claim):
        expected = {'format':'devbench.colab-transport-claim/1','job_id':self._job['job_id'],
                    'job_sha256':job_sha256(self._job),'dispatch_id':self.dispatch_id,
                    'remote_session_id':self.remote_session_id}
        if type(claim) is not dict or claim != expected:
            raise PermissionError('Current consumed transport claim differs')

    def _bind_export(self, export):
        keys = {'format','job_id','job_sha256','source_bundle_sha256','payload',
                'execution_file','notebook','scientific_execution_tier','local_fallback','automatic_replay',
                'remote_payload_name','remote_result_name'}
        if (type(export) is not dict or set(export) != keys or
                export['format'] != 'devbench.colab-method-export/1' or
                export['job_id'] != self._job['job_id'] or
                export['job_sha256'] != job_sha256(self._job) or
                export['source_bundle_sha256'] != self._job['source_bundle_sha256'] or
                export['remote_payload_name'] != 'colab-source.zip' or
                export['remote_result_name'] != 'colab-result.zip' or
                export['scientific_execution_tier'] != 'colab' or
                export['local_fallback'] != 'forbidden' or export['automatic_replay'] != 'forbidden'):
            raise ValueError('Sealed Colab export missing transport inputs')
        for name in ('payload','execution_file','notebook'):
            ref = export[name]
            if type(ref) is not dict or set(ref) != {'path','sha256','size_bytes'} or type(ref['size_bytes']) is not int:
                raise ValueError('Closed sealed export reference required')
            path = _absolute(ref['path'])
            cap = MAX_UPLOAD if name == 'payload' else 1_000_000
            if path.stat().st_size != ref['size_bytes'] or _hash(path, cap) != ref['sha256']:
                raise ValueError('Sealed export reference changed')
        self.bundle = _absolute(export['payload']['path'])
        script = _absolute(export['execution_file']['path'])
        self._bundle_hash = _hash(self.bundle, MAX_UPLOAD)
        self._execution_hash = _hash(script, 1_000_000)
        if script.suffix != '.py':
            raise ValueError('Fixed sealed remote submission script required')
        self.execution_file = self.private_root / 'execution.py'
        _private_file(self.execution_file, script.read_bytes())
        if _hash(self.execution_file, 1_000_000) != self._execution_hash:
            raise ValueError('Private execution copy differs')

    def collect(self):
        if self.state != 'dispatched':
            raise PermissionError('No outstanding single Colab dispatch')
        self.state = 'spent'
        archive = self.private_root / 'colab-result.zip'
        try:
            _, record = self.service._remote_job(self._job['job_id'])
            if record['status'] != 'remote_dispatched':
                raise PermissionError('MethodService must record dispatch before remote execution')
            if _hash(self.bundle, MAX_UPLOAD) != self._bundle_hash or _hash(self.execution_file, 1_000_000) != self._execution_hash:
                raise ValueError('Sealed Colab inputs changed')
            self._check_claim(self.service.revalidate_remote_dispatch(self._job['job_id']))
            self._cli('exec', '--timeout', str(EXEC_SECONDS), '-f', str(self.execution_file), seconds=EXEC_SECONDS + 10)
            if self._observe('inventory') != 'present':
                raise RuntimeError('Bounded remote result inventory not confirmed')
            self._cli('download', 'content/colab-result.zip', str(archive))
            archive_hash = _hash(archive, MAX_RESULT)
            with zipfile.ZipFile(archive) as bundle:
                members = [m for m in bundle.infolist() if m.filename == 'root-receipt.json']
                if len(members) != 1 or members[0].file_size > 100_000:
                    raise ValueError('Bounded result root required')
                root = bundle.read(members[0])
            root_hash = hashlib.sha256(canonical_json(json.loads(root))).hexdigest()
        finally:
            self.stop()
        if self.cleanup['presence'] != 'absent':
            raise RuntimeError('Remote cleanup remains uncertain; no accepted return')
        self.state = 'returned'
        return archive, self._mint('returned', archive_hash=archive_hash, root_hash=root_hash)

    def stop(self):
        self.cleanup['stop_requested'] = True
        try:
            self._cli('stop', cleanup=True)
        except BaseException:
            pass
        try:
            self.cleanup['presence'] = self._observe('verify', cleanup=True)
        except BaseException:
            self.cleanup['presence'] = 'unknown'
        return dict(self.cleanup)
