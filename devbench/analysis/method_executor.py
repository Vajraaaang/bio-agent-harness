"""Local single-user OCI execution; never a fallback to unsandboxed custom code.

Custom code has no writable host mount. Outputs and scratch live on bounded
tmpfs mounts and are copied out while the container is alive, then validated.
Shared-service hostile workloads require a stronger runtime boundary.
"""
from __future__ import annotations

import io
import fcntl
import re
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
import hashlib
import json
import uuid
import os
from pathlib import Path, PurePosixPath
import selectors
import shutil
import subprocess
import tarfile
import time

from devharness_core.provenance import canonical_json, copy_artifact, hash_artifact


def _path(value):
    path = Path(value)
    if not path.is_absolute() or path != path.resolve() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Canonical absolute paths without symlinks are required")
    if any(c in str(path) for c in (",", "\n", "\r")):
        raise ValueError("Path cannot be represented safely as a Docker mount")
    return path


def container_command(contract, workspace, name, image_volumes=(), *, labels=None):
    """Build argv only; no interpolation of user code into a shell command."""
    workspace = _path(workspace)
    limits = contract["execution"]
    uid, gid = os.getuid() or 65532, os.getgid() if os.getuid() else 65532
    command = ["create", "--name=" + name, "--pull=never", "--read-only", "--network=none",
               "--cap-drop=ALL", "--security-opt=no-new-privileges:true", "--no-healthcheck",
               "--user=" + str(uid) + ":" + str(gid), "--pids-limit=" + str(limits["pids"]),
               "--cpus=" + str(limits["cpu"]), "--memory=" + str(limits["memory_mb"]) + "m",
               "--memory-swap=" + str(limits["memory_mb"]) + "m", "--log-driver=none",
               "--ulimit=nofile=256:256", "--ulimit=core=0:0", "--workdir=/tmp",
               "--entrypoint=/bin/sh"]
    for directory in ("inputs", "method"):
        command += ["--mount", f"type=bind,src={workspace / directory},dst=/{directory},readonly"]
    mounts = {"/tmp": min(limits["memory_mb"] * 1024**2 // 4, 64 * 1024**2),
              "/outputs": limits["max_output_bytes"]}
    for target in image_volumes:
        path = PurePosixPath(target)
        if (not path.is_absolute() or str(path) != target or ".." in path.parts
                or target in {"/", "/inputs", "/method", "/proc", "/sys", "/dev"}
                or any(target.startswith(prefix + "/") for prefix in ("/inputs", "/method", "/proc", "/sys", "/dev"))):
            raise ValueError("Unsupported image volume")
        mounts.setdefault(target, 1024**2)
    for target, size in mounts.items():
        command.append(f"--tmpfs={target}:rw,nosuid,nodev,noexec,size={size},uid={uid},gid={gid},mode=0700")
    if labels:
        command += ["--label=" + key + "=" + value for key, value in sorted(labels.items())]
    if contract.get("method_id") == "community.nfcore.differentialabundance.2":
        from .community_workflow import _resources
        profile = _resources(contract["parameters"]["properties"]["plan"]["properties"]["resources"]["const"])
        if any(profile[k] != limits[k] for k in ("cpu", "memory_mb", "timeout_seconds", "pids", "max_output_bytes")):
            raise ValueError("Community resource profile differs from immutable method execution limits")
        # PID 1 exits independently of the host coordinator. Namespace teardown
        # stops all descendants, including an orphaned local Nextflow engine.
        command = [item for item in command if item != "--entrypoint=/bin/sh"]
        command += ["--entrypoint=python3",
            f"--tmpfs=/tmp/community:rw,nosuid,nodev,noexec,size={profile['scratch_bytes']},uid={uid},gid={gid},mode=0700",
            contract["environment"]["image"], "-c", "import time; time.sleep(" + str(limits["timeout_seconds"]) + ")"]
    else:
        # Historical generic-method lifetime remains host-supervised.
        command += [contract["environment"]["image"], "-c", "while :; do sleep 1; done"]
    return command


def _bounded(command, env, timeout, maximum):
    """Drain both pipes with one cumulative byte bound and a monotonic deadline."""
    started = time.monotonic()
    process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    buffers, total = {"stdout": bytearray(), "stderr": bytearray()}, 0
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                if time.monotonic() - started > timeout:
                    raise TimeoutError("Method exceeded its wall-time limit")
                for key, _ in selector.select(min(.1, timeout)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    available = max(0, maximum - total)
                    buffers[key.data].extend(chunk[:available])
                    total += len(chunk)
                    if total > maximum:
                        raise ValueError("Method output or logs exceeded the byte limit")
        process.wait(timeout=max(.01, timeout - (time.monotonic() - started)))
        return process.returncode, bytes(buffers["stdout"]), bytes(buffers["stderr"])
    except (TimeoutError, ValueError, subprocess.TimeoutExpired) as error:
        error.execution = {"exit_code": None, "runtime": "local_docker",
                           "stdout": bytes(buffers["stdout"][:500000]).decode("utf-8", "replace"),
                           "stderr": bytes(buffers["stderr"][:500000]).decode("utf-8", "replace"),
                           "error": str(error)}
        raise
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()


def _extract(data, output_dir, maximum):
    total, count, names = 0, 0, set()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive:
            count += 1
            if count > 64:
                raise ValueError("Too many output archive members")
            name = member.name.removeprefix("./")
            if member.isdir() and name in {"", "."}:
                continue
            path = PurePosixPath(name)
            if not member.isfile() or len(path.parts) != 1 or name in {"", ".", ".."} or name in names:
                raise ValueError("Only distinct regular output files are supported")
            names.add(name)
            total += member.size
            if total > maximum:
                raise ValueError("Method outputs exceeded the byte limit")
            with archive.extractfile(member) as source, (output_dir / name).open("xb") as target:
                shutil.copyfileobj(source, target, 1024**2)


class LocalDockerExecutor:
    """Use an installed digest-pinned image and one explicitly local daemon."""

    def __init__(self):
        self._observer_context=ContextVar('devbench_oci_lifecycle_observer',default=None)

    def set_observer(self, observer):
        if not callable(observer):
            raise TypeError("Use a trusted lifecycle observer")
        self._observer_context.set(observer)

    @staticmethod
    def _runtime(workspace):
        from devharness_controlled.native_runtime import LocalDocker
        runtime = LocalDocker()
        client = workspace / "docker-client"
        runtime.isolate_client(client)
        runtime.env = {"PATH": os.defpath, "HOME": str(client)}
        return runtime

    def inspect_image(self, image):
        # Read-only image metadata; no container, pull or installation.
        from devharness_controlled.native_runtime import LocalDocker
        runtime = LocalDocker()
        identity = runtime._call(["image", "inspect", image, "--format",
            '{"digests":{{json .RepoDigests}},"id":{{json .Id}},"os":{{json .Os}},"architecture":{{json .Architecture}},"labels":{{json .Config.Labels}}}'], json_result=True)
        labels = identity.get("labels") or {}
        try:
            inventory = json.loads(labels["devbench.community.inventory"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Installed image lacks an admitted community inventory") from error
        if "image" in inventory or image not in (identity["digests"] or []):
            raise ValueError("Image-free installed inventory and verified external image digest required")
        return {"repo_digests": identity["digests"], "image_id": identity["id"],
                "platform": identity["os"] + "/" + identity["architecture"], "inventory": {**inventory,"image":image}}

    @staticmethod
    def _inspect_owned(runtime, binding):
        identity=binding.get("container_id")
        selection="id="+identity if identity else "name=^/"+re.escape(binding["container_name"])+"$"
        listed=runtime._call(["container","ls","--all","--no-trunc","--filter",selection,"--format","{{.ID}}"])
        ids=listed.splitlines()
        if not ids:
            return {**binding,"state":"missing","observation":"successful_empty_daemon_listing"}
        if len(ids)!=1 or re.fullmatch('[0-9a-f]{64}',ids[0]) is None:
            raise ValueError("Ambiguous authoritative container identity observation")
        observed=runtime._call(["inspect",ids[0],"--format","{{json .}}"],json_result=True)
        labels=observed["Config"].get("Labels") or {}
        current={"container_id":observed["Id"],"container_name":observed["Name"].removeprefix("/"),
            "image":observed["Config"]["Image"],"run_id":labels.get("devbench.run_id"),
            "inventory_sha256":labels.get("devbench.inventory_sha256"),
            "nextflow_session_id":labels.get("devbench.nextflow_session_id"),"state":observed["State"]["Status"],
            "observation":"successful_daemon_listing_and_owned_inspection"}
        for key in ("container_name","image","run_id","inventory_sha256","nextflow_session_id"):
            if current[key]!=binding.get(key):
                raise PermissionError("Observed object is not the owned container/session identity binding")
        if current['container_id']!=ids[0] or identity is not None and current['container_id']!=identity:
            raise PermissionError("Observed immutable container identity changed")
        return {**binding,**current}

    def inspect_run(self, binding):
        from devharness_controlled.native_runtime import LocalDocker
        runtime=LocalDocker()
        if 'output_dir' not in binding:return self._inspect_owned(runtime,binding)
        try:
            with self._control_lock(binding,'community-runtime-control.lock',blocking=False):
                observed=self._inspect_owned(runtime,binding)
                phase=self._dispatch_state(binding)
                return {**observed,'dispatch_settled':self._cancellation_settled(binding,observed,phase),
                    'dispatch_phase':phase.get('phase') if phase else 'unknown'}
        except BlockingIOError:
            return {**self._inspect_owned(runtime,binding),'dispatch_settled':False,'dispatch_phase':'effect_in_flight'}

    @staticmethod
    @contextmanager
    def _control_lock(binding, name, *, blocking=True):
        output=_path(binding['output_dir'])
        with _path(output.parent/name).open('a+b') as lease:
            fcntl.flock(lease,fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            yield

    @staticmethod
    def _cancel_intent(binding):
        path=_path(Path(binding['output_dir']).parent/'community-cancel-intent')
        try:
            with path.open('xb') as stream:
                stream.write(b'cancel');stream.flush();os.fsync(stream.fileno())
        except FileExistsError:pass

    @staticmethod
    def _dispatch_state(binding):
        path=_path(Path(binding['output_dir']).parent/'community-dispatch-state.json')
        if not path.exists():return None
        hash_artifact(path,max_bytes=4096,max_entries=1)
        value=json.loads(path.read_bytes())
        if any(value.get(key)!=binding.get(key) for key in ('run_id','container_name','image','inventory_sha256','nextflow_session_id')):
            raise PermissionError('Dispatch phase belongs to a different immutable execution identity')
        return value

    @staticmethod
    def _cancellation_settled(binding, observed, phase):
        # One predicate for cancel and read-only reconcile: a successful empty
        # listing cannot settle a create/start RPC that may still complete.
        if observed['state'] not in {'missing','exited','dead'} or phase is None:return False
        if phase.get('phase') in {'creating','create_uncertain','starting','start_uncertain'}:return False
        return bool(phase.get('closed') or (Path(binding['output_dir']).parent/'community-cancel-intent').exists())

    @staticmethod
    def _dispatch_phase(binding,phase,**fields):
        # Caller holds the per-run effect lock. This is a lifecycle receipt,
        # never a permission grant or a replacement for the operation ledger.
        current=LocalDockerExecutor._dispatch_state(binding) or {
            key:binding[key] for key in ('run_id','container_name','image','inventory_sha256','nextflow_session_id')}
        if current.get('phase') in {'cancelled','removed'} and phase not in {'cancelled','removed'}:
            phase=current['phase']
        current.update(fields,phase=phase)
        path=_path(Path(binding['output_dir']).parent/'community-dispatch-state.json')
        pending=_path(path.with_suffix('.pending'))
        with pending.open('wb') as stream:
            stream.write(canonical_json(current));stream.flush();os.fsync(stream.fileno())
        os.replace(pending,path)
        return current

    @staticmethod
    def _collection_receipt(binding, maximum):
        output=_path(binding['output_dir']);path=output.parent/'community-diagnostic-collection.json'
        if not path.exists(): return None
        hash_artifact(_path(path),max_bytes=65536,max_entries=1)
        record=json.loads(path.read_bytes())
        if not isinstance(record.get('files'),dict) or len(record['files'])>64:raise ValueError('Invalid bounded diagnostic collection receipt')
        if any(record.get(key)!=binding.get(key) for key in ('container_id','nextflow_session_id','image')):
            raise ValueError('Prior diagnostic collection belongs to another immutable runtime')
        total=0
        for name,expected in record['files'].items():
            if len(PurePosixPath(name).parts)!=1 or name in {'.','..'}:raise ValueError('Invalid retained diagnostic path')
            actual=hash_artifact(_path(output/name),max_bytes=maximum,max_entries=1)
            total+=actual['size_bytes']
            if actual['sha256']!=expected or total>maximum:raise ValueError('Retained diagnostic bytes changed')
        return {**record,'reused_previously_collected_bytes':True}

    def _collect(self,runtime,binding,maximum):
        output=_path(binding['output_dir'])
        with self._control_lock(binding,'community-output-copy.lock'):
            retained=self._collection_receipt(binding,maximum)
            if retained is not None:return retained
            code,archive,error=_bounded(runtime.command(['exec',binding['container_id'],'/usr/bin/env','-i',
                'PATH=/usr/local/bin:/usr/bin:/bin','tar','-C','/outputs','-cf','-','.']),runtime.env,5,maximum+1024**2)
            if code:raise ValueError('Unable to collect live sandbox tmpfs: '+error.decode('utf-8','replace')[:1000])
            temporary=output.parent/('collection-'+uuid.uuid4().hex);temporary.mkdir()
            try:
                _extract(archive,temporary,maximum)
                files={}
                for path in temporary.iterdir():
                    expected=hash_artifact(path,max_bytes=maximum,max_entries=1)['sha256']
                    destination=output/path.name
                    if destination.exists():
                        if hash_artifact(_path(destination),max_bytes=maximum,max_entries=1)['sha256']!=expected:
                            raise ValueError('Concurrent output collection changed captured bytes')
                    else:os.link(path,destination)
                    files[path.name]=expected
                record={key:binding[key] for key in ('container_id','nextflow_session_id','image')}
                record.update(status='collected_while_supervisor_live',files=files,reused_previously_collected_bytes=False)
                receipt=output.parent/'community-diagnostic-collection.json'
                with receipt.open('xb') as stream:
                    stream.write(canonical_json(record));stream.flush();os.fsync(stream.fileno())
                return record
            finally:shutil.rmtree(temporary)

    def cancel_run(self, binding):
        from devharness_controlled.native_runtime import LocalDocker
        runtime=LocalDocker()
        observed=self._inspect_owned(runtime,binding)
        binding={**binding,**observed}
        # Intent can be recorded while an RPC owns the effect lock, but such a
        # request remains PENDING until that in-flight effect is settled.
        self._cancel_intent(binding)
        try:
            with self._control_lock(binding,'community-runtime-control.lock',blocking=False):
                return self._cancel_settled(runtime,binding)
        except BlockingIOError:
            return {**observed,'state':'cancellation_pending','diagnostic_status':'dispatch_effect_in_flight'}

    def _cancel_settled(self,runtime,binding):
        observed=self._inspect_owned(runtime,binding)
        binding={**binding,**observed}
        phase=self._dispatch_state(binding)
        if observed['state']=='missing':
            if not self._cancellation_settled(binding,observed,phase):
                return {**observed,'state':'cancellation_pending','diagnostic_status':'create_effect_not_settled; positive absence is not terminal proof'}
            self._dispatch_phase(binding,'cancelled',closed=True,in_flight=False,cleanup_complete=True)
            retained=self._collection_receipt(binding,binding['max_output_bytes']) if binding.get('container_id') else None
            return {**observed,'state':'cancelled','diagnostic_collection':retained,
                'diagnostic_loss':None if retained is not None else 'object_absent; no uncollected tmpfs bytes are claimed'}
        if observed['state']=='created':
            runtime._call(['rm','--force','--volumes',binding['container_id']])
            self._dispatch_phase(binding,'cancelled',closed=True,in_flight=False,cleanup_complete=True)
            return {**observed,'state':'cancelled','diagnostic_status':'engine_never_started'}
        if observed['state'] in {'exited','dead'}:
            retained=self._collection_receipt(binding,binding['max_output_bytes'])
            self._dispatch_phase(binding,'cancelled',closed=True,in_flight=False)
            return {**observed,'state':'cancelled','diagnostic_collection':retained,
                'diagnostic_loss':None if retained is not None else 'stopped_container_tmpfs_unavailable'}
        if phase is not None and not phase.get('worker_reserved',False):
            # No worker effect has been reserved. The durable latch plus this
            # same effect lock prevents any subsequent start/exec phase.
            runtime._call(['stop','--time=1',binding['container_id']],timeout=5)
            stopped=self._inspect_owned(runtime,binding)
            if stopped['state'] not in {'exited','dead','missing'}:raise ValueError('Pre-worker supervisor stop was not verified')
            self._dispatch_phase(binding,'cancelled',closed=True,in_flight=False)
            return {**stopped,'state':'cancelled','diagnostic_status':'worker_never_launched'}
        # Request an attached worker to stop its owned engine process group.
        # PID 1 stays live until worker diagnostics have been finalized/copied.
        runtime._call(['exec',binding['container_id'],'python3','-c',
            "from pathlib import Path;Path('/tmp/community/cancel.request').write_text('cancel')"],timeout=5)
        wait_code="""import json,time
from pathlib import Path
p=Path('/tmp/community/worker-state.json');deadline=time.monotonic()+5
while time.monotonic()<deadline:
    if p.is_file():
        value=json.loads(p.read_bytes())
        if value.get('state') in ('finished','cancelled'):
            print(json.dumps(value));break
    time.sleep(.05)
else:print(json.dumps({'state':'cancellation_pending'}))
"""
        finished=runtime._call(['exec',binding['container_id'],'python3','-c',wait_code],json_result=True,timeout=6)
        if finished.get('state') not in {'finished','cancelled'}:
            return {**observed,'state':'cancellation_pending','diagnostic_status':'worker_finalization_not_yet_observed'}
        collection=self._collect(runtime,binding,binding['max_output_bytes'])
        runtime._call(['stop','--time=1',binding['container_id']],timeout=5)
        stopped=self._inspect_owned(runtime,binding)
        if stopped['state'] not in {'exited','dead','missing'}:raise ValueError('Owned supervisor stop was not verified')
        self._dispatch_phase(binding,'cancelled',closed=True,in_flight=False)
        return {**stopped,'state':'cancelled','diagnostic_collection':collection}

    def execute(self, contract, inputs, parameters, source, lock, output_dir):
        # Registration and consumption are local to this caller's context; the
        # captured callback remains stable across threads and nested calls.
        observer=self._observer_context.get()
        self._observer_context.set(None)
        output_dir = _path(output_dir)
        workspace = output_dir.parent
        runtime = self._runtime(workspace)
        identity = runtime._call(["image", "inspect", contract["environment"]["image"], "--format",
            '{"id":{{json .Id}},"digests":{{json .RepoDigests}},"volumes":{{json (index .Config "Volumes")}},"os":{{json .Os}}}'], json_result=True)
        if (contract["environment"]["image"] not in (identity["digests"] or []) or identity["os"] != "linux"
                or len(identity["volumes"] or {}) > 16):
            raise ValueError("Installed image identity is unsupported or mismatched")
        (workspace / "inputs").mkdir(mode=0o755)
        (workspace / "method").mkdir(mode=0o755)
        for name, path in inputs.items():
            copy_artifact(path, workspace / "inputs" / name, max_bytes=100_000_000, max_entries=1)
        copy_artifact(source, workspace / "method" / "main.py", max_bytes=1_000_000, max_entries=1)
        copy_artifact(lock, workspace / "method" / "environment.lock", max_bytes=1_000_000, max_entries=1)
        (workspace / "method" / "parameters.json").write_bytes(canonical_json(parameters))
        for directory in (workspace / "inputs", workspace / "method"):
            directory.chmod(0o755)
            for path in directory.iterdir(): path.chmod(0o444)
        name = "devharness-method-" + workspace.name
        community = contract.get("method_id") == "community.nfcore.differentialabundance.2"
        labels = {}
        binding = {"run_id": workspace.name, "container_name": name, "image": contract["environment"]["image"]}
        if community:
            inventory = json.loads(Path(lock).read_bytes())
            binding.update(output_dir=str(output_dir),max_output_bytes=contract['execution']['max_output_bytes'],inventory_sha256=hashlib.sha256(canonical_json(inventory)).hexdigest(), nextflow_session_id=str(uuid.uuid4()))
            labels = {"devbench." + key: binding[key] for key in ("run_id", "inventory_sha256", "nextflow_session_id")}
        def emit(event):
            if community and callable(observer):
                if observer({"event":event,**binding}) is False:self._cancel_intent(binding)
        def cancelled():
            self._dispatch_phase(binding,'cancelled',closed=True,in_flight=False)
            return {"exit_code":143,"worker_exit_code":None,"worker_started":False,"runtime":"local_docker",
                "engine_started":False,"engine_exit_code":None,"engine_outcome":"not_started","cancelled":True,
                "container_name":name,"container_id":binding.get('container_id'),
                "stdout":"","stderr":"Dispatch cancelled before worker launch","diagnostic_status":"worker_never_launched"}
        def latched():
            return (workspace/'community-cancel-intent').exists()
        created = False
        result = None
        execution_error = None
        try:
            if community:
                with self._control_lock(binding,'community-runtime-control.lock'):
                    self._dispatch_phase(binding,'create_intent',in_flight=False,closed=False,worker_reserved=False)
            emit("container_create_intent")
            with (self._control_lock(binding,'community-runtime-control.lock') if community else nullcontext()):
                if community and latched():return cancelled()
                if community:self._dispatch_phase(binding,'creating',in_flight=True)
                try:
                    container_id=runtime._call(container_command(contract,workspace,name,identity['volumes'] or {},labels=labels))
                except BaseException:
                    if community:self._dispatch_phase(binding,'create_uncertain',in_flight=False,closed=True)
                    raise
                if community and re.fullmatch('[0-9a-f]{64}',str(container_id).strip()) is None:
                    self._dispatch_phase(binding,'create_uncertain',in_flight=False,closed=True)
                    raise ValueError('Create response did not identify an immutable owned container')
                created=True;binding['container_id']=str(container_id).strip()
                if community:self._dispatch_phase(binding,'created',in_flight=False)
            emit('container_created')
            emit('container_start_intent')
            with (self._control_lock(binding,'community-runtime-control.lock') if community else nullcontext()):
                if community and latched():return cancelled()
                if community:self._dispatch_phase(binding,'starting',in_flight=True)
                try:runtime._call(['start',binding['container_id']])
                except BaseException:
                    if community:self._dispatch_phase(binding,'start_uncertain',in_flight=False,closed=True)
                    raise
                if community:self._dispatch_phase(binding,'started',in_flight=False)
            emit('container_started')
            if community:
                with self._control_lock(binding,'community-runtime-control.lock'):
                    if latched():return cancelled()
                    self._dispatch_phase(binding,'exec_intent',in_flight=False,worker_reserved=True)
                emit('worker_exec_intent')
                with self._control_lock(binding,'community-runtime-control.lock'):
                    if latched():return cancelled()
                    # The worker RPC may still be in flight after releasing the
                    # lock. Cancellation cannot claim termination from absence
                    # of a worker-state file; it must wait for finalization.
                    self._dispatch_phase(binding,'executing',in_flight=True,worker_reserved=True)
            command = runtime.command(["exec", binding["container_id"], "/usr/bin/env", "-i", "PATH=/usr/local/bin:/usr/bin:/bin",
                "HOME=/tmp", "TMPDIR=/tmp", "PYTHONDONTWRITEBYTECODE=1", "OMP_NUM_THREADS=1",
                *(["DEVBENCH_NEXTFLOW_SESSION_ID=" + binding["nextflow_session_id"]] if community else []),
                *contract["implementation"]["entrypoint"]])
            try:
                code, stdout, stderr = _bounded(command, runtime.env, contract["execution"]["timeout_seconds"], 1_000_000)
                if community:
                    with self._control_lock(binding,'community-runtime-control.lock'):
                        self._dispatch_phase(binding,'worker_finished',in_flight=False,closed=True,worker_completed=True)
                result = {"exit_code": code, "runtime": "local_docker", "image_id": identity["id"],
                    "image": contract["environment"]["image"], "stdout": stdout.decode("utf-8", "replace"),
                    "stderr": stderr.decode("utf-8", "replace"), "container_name": name, "container_id": binding["container_id"]}
                if community:result.update(engine_started=None,engine_exit_code=None,engine_outcome='unknown')
            except (TimeoutError, ValueError, subprocess.TimeoutExpired) as error:
                if community:
                    with self._control_lock(binding,'community-runtime-control.lock'):
                        self._dispatch_phase(binding,'exec_uncertain',in_flight=False,closed=True)
                execution_error = error
            # Community diagnostics survive failed task exit, while the caller
            # still rejects all successful publication on any nonzero result.
            if community or (result is not None and result["exit_code"] == 0):
                maximum = contract["execution"]["max_output_bytes"]
                try:
                    if community:
                        collection=self._collect(runtime,binding,maximum)
                        if result is not None:result['diagnostic_collection']=collection
                    else:
                        code,archive,error=_bounded(runtime.command(['exec',binding['container_id'],'/usr/bin/env','-i',
                            'PATH=/usr/local/bin:/usr/bin:/bin','tar','-C','/outputs','-cf','-','.']),runtime.env,30,maximum+1024**2)
                        if code:raise ValueError('Unable to collect sandbox outputs: '+error.decode('utf-8','replace')[:1000])
                        _extract(archive,output_dir,maximum)
                    tasks=output_dir/'task-evidence.json'
                    if community and tasks.is_file():
                        task_record=json.loads(tasks.read_bytes())
                        state=task_record.get('engine_state',{})
                        if result is not None:
                            result.update(engine_started=state.get('engine_started'),engine_exit_code=state.get('engine_exit_code'),
                                engine_outcome=state.get('outcome','unknown'),cancelled=task_record.get('worker_exit_code')==143)
                        if (task_record.get('nextflow_session_id')==binding['nextflow_session_id']
                                and task_record.get('engine_session_observed') is True):emit('engine_session_started')
                except (TimeoutError, ValueError, subprocess.TimeoutExpired) as error:
                    if result is not None:
                        error.execution = {**result, "diagnostic_collection_error": str(error)[:2000],
                            "diagnostic_collection":{"status":"unavailable","reason":"No recovery of uncollected tmpfs bytes is claimed","error":str(error)[:1000]}}
                    elif execution_error is not None and hasattr(execution_error, "execution"):
                        execution_error.execution["diagnostic_collection_error"] = str(error)[:2000]
                        execution_error.execution['diagnostic_collection']={'status':'unavailable','reason':'Supervisor deadline, interruption, or collection failure may have lost tmpfs; bytes were not recovered'}
                    if execution_error is None: execution_error = error
            if execution_error is not None: raise execution_error
            return result
        finally:
            if created:
                # A successful immutable create result is ownership evidence;
                # never remove a name-only object after a lost create response.
                if community:
                    with self._control_lock(binding,'community-runtime-control.lock'):
                        state=self._dispatch_state(binding)
                        if not state.get('cleanup_complete',False):
                            runtime._call(["rm","--force","--volumes",binding["container_id"]])
                            self._dispatch_phase(binding,'removed',closed=True,in_flight=False,cleanup_complete=True)
                else:runtime._call(["rm","--force","--volumes",binding["container_id"]])
