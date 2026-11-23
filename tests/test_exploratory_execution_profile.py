
from devbench.engine.method_executor import container_command
from devbench.engine.methods import exploratory_method
from devbench.study.rna_hosted_fixtures import custom


def test_frozen_method_uses_daemon_lifetime_and_closed_mounts(tmp_path):
    contract, _, _ = custom(tmp_path)
    contract["environment"]["image"] = "python@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159"
    command = container_command(contract, tmp_path.resolve(), "test", exploratory=True)
    assert command[-3] == "--entrypoint=/bin/sleep"
    assert float(command[-1]) > contract["execution"]["timeout_seconds"]
    assert "--network=none" in command and "--read-only" in command
    assert all("readonly,bind-recursive=disabled" in command[i + 1]
               for i, part in enumerate(command) if part == "--mount")
    assert exploratory_method(contract).to_dict() == contract


import json
import os

import pytest

from devbench.core.protected_partitions import digest
from devbench.engine.method_executor import EXPLORATORY_IMAGE, EXPLORATORY_IMAGE_ID, LocalDockerExecutor
from devbench.engine.methods import MethodService
from devbench.platform.workflow_entrypoints import LEDGER_NAME
from devbench.engine.provenance import hash_artifact
from tests.test_investigation_invalidation import artifact, link, make, step, wal


docker = pytest.mark.skipif(os.environ.get("DEVBENCH_T15_DOCKER") != "1",
                            reason="explicit local Docker smoke opt-in")


def register(methods, directory, name, inputs, outputs, program, **limits):
    directory.mkdir()
    contract, source, lock = custom(directory)
    source.write_text(program)
    contract["method_id"] = "t15." + name
    contract["implementation"]["source_sha256"] = hash_artifact(source)["sha256"]
    contract["environment"]["image"] = EXPLORATORY_IMAGE
    contract["inputs"] = {port: {"artifact_type": "json", "required": True} for port in inputs}
    contract["outputs"] = {port: {"artifact_type": "json", "path": "/outputs/" + port + ".json",
                                  "required": True} for port in outputs}
    contract["execution"].update(limits)
    return methods.register_method(contract, source=source, environment_lock=lock, actor="tester")


def bound_step(record, node):
    return node.model_copy(update={"oci_method_version_id": record["version_id"],
        "environment_sha256": digest({**record["contract"]["environment"], "image_id": EXPLORATORY_IMAGE_ID}),
        "parameters_sha256": digest({"threshold": 1})})


@pytest.fixture
def exploratory_inspection(tmp_path):
    from types import SimpleNamespace

    workspace = tmp_path.resolve()
    uid, gid = (os.getuid(), os.getgid()) if os.getuid() else (65532, 65532)
    limits = {"cpu": 1, "memory_mb": 128, "pids": 32, "timeout_seconds": 5, "max_output_bytes": 1024}
    mounts = [{"Destination": "/" + name, "Source": str(workspace / name), "Type": "bind",
               "RW": False, "Propagation": "rprivate"} for name in ("inputs", "method")]
    host = {
        "NetworkMode": "none", "ReadonlyRootfs": True, "Privileged": False,
        "CapDrop": ["ALL"], "CapAdd": None, "SecurityOpt": ["no-new-privileges:true"],
        "NanoCpus": 10**9, "Memory": 128 * 1024**2, "MemorySwap": 128 * 1024**2,
        "PidsLimit": 32, "LogConfig": {"Type": "none"}, "IpcMode": "private", "PidMode": "",
        "Tmpfs": {path: f"rw,nosuid,nodev,noexec,size={size},uid={uid},gid={gid},mode=0700"
                  for path, size in (("/tmp", 32 * 1024**2), ("/outputs", 1024))},
        "Ulimits": [{"Name": name, "Soft": size, "Hard": size}
                    for name, size in (("nofile", 256), ("core", 0), ("fsize", 1024))],
        "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
        "Mounts": [{"Target": mount["Destination"], "Source": mount["Source"],
                    "Type": "bind", "ReadOnly": True, "BindOptions": {"NonRecursive": True}}
                   for mount in mounts],
    }
    value = {"Image": EXPLORATORY_IMAGE_ID, "HostConfig": host, "Mounts": mounts,
             "Config": {"Entrypoint": ["/bin/sleep"], "Cmd": ["13"], "User": f"{uid}:{gid}"}}
    runtime = SimpleNamespace(_call=lambda *_args, **_kwargs: value)

    def check():
        return LocalDockerExecutor._check_exploratory_runtime(
            runtime, {"container_id": "a" * 64}, limits, workspace)

    return value, check


@pytest.mark.parametrize("report_bind_options", [True, False])
def test_daemon_preserves_exploratory_controls(exploratory_inspection, report_bind_options):
    value, check = exploratory_inspection
    if not report_bind_options:
        for mount in value["HostConfig"]["Mounts"]:
            del mount["BindOptions"]
    assert len(check()) == 64


@pytest.mark.parametrize("old,new", [
    (",noexec", ""), (",nosuid", ""), (",nodev", ""), ("size=1024", "size=2048"),
    ("mode=0700", "mode=0777"), ("mode=0700", "mode=0700,exec"),
])
def test_daemon_rejects_weakened_tmpfs_options(exploratory_inspection, old, new):
    value, check = exploratory_inspection
    tmpfs = value["HostConfig"]["Tmpfs"]
    tmpfs["/outputs"] = tmpfs["/outputs"].replace(old, new)
    with pytest.raises(PermissionError, match="isolation controls"):
        check()


@pytest.mark.parametrize("name", ["nofile", "core", "fsize"])
def test_daemon_rejects_unfrozen_ulimits(exploratory_inspection, name):
    value, check = exploratory_inspection
    limit = next(item for item in value["HostConfig"]["Ulimits"] if item["Name"] == name)
    limit["Hard"] += 1
    with pytest.raises(PermissionError, match="isolation controls"):
        check()


@pytest.mark.parametrize("field,value", [("Name", "always"), ("MaximumRetryCount", 1)])
def test_daemon_rejects_unfrozen_restart_policy(exploratory_inspection, field, value):
    inspection, check = exploratory_inspection
    inspection["HostConfig"]["RestartPolicy"][field] = value
    with pytest.raises(PermissionError, match="isolation controls"):
        check()


@pytest.mark.parametrize("location,field,value", [
    ("effective", "Propagation", "rshared"),
    ("configured", "Propagation", "rslave"),
    ("configured", "NonRecursive", False),
])
def test_daemon_rejects_unfrozen_bind_mount_options(exploratory_inspection, location, field, value):
    inspection, check = exploratory_inspection
    options = (inspection["Mounts"][0] if location == "effective"
               else inspection["HostConfig"]["Mounts"][0]["BindOptions"])
    options[field] = value
    with pytest.raises(PermissionError, match="isolation controls"):
        check()


def test_daemon_rejects_configured_bind_options_without_nonrecursive(exploratory_inspection):
    inspection, check = exploratory_inspection
    inspection["HostConfig"]["Mounts"][0]["BindOptions"] = {"Propagation": "rprivate"}
    with pytest.raises(PermissionError, match="isolation controls"):
        check()


@docker
@wal
def test_real_oci_chain_and_closed_effects(tmp_path):
    from devbench.platform.method_planner import ScientificMethodPlanner
    from devbench.platform.workflow_entrypoints import advance_adaptive_workflow

    tmp_path = tmp_path.resolve()
    flow = make(tmp_path, name=LEDGER_NAME)
    methods = MethodService(tmp_path)
    planner = ScientificMethodPlanner(methods)
    planner.get = lambda session_id: {"session_id": session_id}
    worker = planner.exploratory_executor("study", flow.ledger, [{"threshold": 1}])
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("TEST-ONLY-PROTECTED-SENTINEL")
    root = artifact("input").model_copy(update={"sha256": worker.training.put(b'{"x":[1,2,3]}')})
    flow.register_artifact(root)
    first = register(methods, tmp_path / "method1", "transform", ("data",), ("out", "second"), f"""
import errno, json, os, socket
from pathlib import Path
for path in ({str(sentinel)!r}, '/proc/1/root/inputs/../../evaluation/sentinel',
             '/var/run/docker.sock', '/evaluation/sentinel', '/cache/sentinel'):
    assert not Path(path).exists()
assert set(os.listdir('/inputs')) == {{'data'}}
assert not any('TOKEN' in key or 'SECRET' in key for key in os.environ)
for path in ('/root-write', '/inputs/data', '/method/main.py'):
    try:
        Path(path).write_text('synthetic')
    except OSError as error:
        assert error.errno in (errno.EROFS, errno.EACCES)
    else:
        raise AssertionError(path)
try:
    socket.create_connection(('192.0.2.1', 443), timeout=.5)
except OSError:
    pass
else:
    raise AssertionError('network unexpectedly available')
x = json.loads(Path('/inputs/data').read_bytes())['x']
for port, y in [('out',[v/sum(x) for v in x]), ('second',[v**2 for v in x])]:
    Path('/outputs/' + port + '.json').write_text(json.dumps(y))
""")
    node = step("transform", link("data", "input", immutable=True))
    node = node.model_copy(update={"outputs": (node.outputs[0], node.outputs[0].model_copy(update={"name": "second"}))})
    flow.insert(bound_step(first, node))
    second = register(methods, tmp_path / "method2", "merge", ("a", "b"), ("out",), """
import json
from pathlib import Path
a,b = (json.loads(Path('/inputs/' + port).read_bytes()) for port in ('a','b'))
Path('/outputs/out.json').write_text(json.dumps({'n':len(a),'sum_squares':sum(b)}))
""")
    flow.insert(bound_step(second, step("merge", link("a", "transform"), link("b", "transform", output="second"))))
    result = advance_adaptive_workflow(planner, "study", ledger=flow.ledger, executor=worker, actor="tester",
                                       max_steps=2, allow_data_execution=True)
    assert result["steps_dispatched"] == 2, result
    for directory in sorted(worker.runs.iterdir()):
        io = json.loads((directory / "io.json").read_bytes())
        print(json.dumps({"runtime": io["runtime"], "frozen": io["frozen"]}, sort_keys=True))
    assert flow.status()["outcomes"] == {"transform": "succeeded", "merge": "succeeded"}
    output = flow.current_use("merge", "out", purpose="cache")
    with worker.training.open(output.sha256) as stream:
        assert json.load(stream) == {"n": 3, "sum_squares": 14}


@docker
@wal
def test_real_training_graph_subgroup_figure_and_protected_grouped_scoring(tmp_path):
    from devbench.adaptive.policy import GROUPED_EVALUATOR_SOURCE, authorize_holdout
    from devbench.engine.workflow_operations import OperationBlocked
    from devbench.platform.method_planner import ScientificMethodPlanner
    from devbench.platform.workflow_entrypoints import advance_adaptive_workflow
    from tests.test_protected_partition_isolation import sealed_fixture

    tmp_path = tmp_path.resolve()
    flow = make(tmp_path, name=LEDGER_NAME)
    methods = MethodService(tmp_path)
    planner = ScientificMethodPlanner(methods)
    planner.get = lambda session_id: {"session_id": session_id}
    worker = planner.exploratory_executor("study", flow.ledger, [{"threshold": 1}])
    training = {"features": ["g1", "g2"], "rows": [{"observation": name, "unit": unit, "label": label, "counts": counts}
        for name, unit, label, counts in [("s1", "a", "L", [9, 1]), ("s2", "a", "L", [8, 2]),
                                         ("s3", "c", "R", [1, 9]), ("s4", "c", "R", [2, 8])]]}
    held = {"features": ["g1", "g2"], "rows": [{"observation": "EVALUATION_SENTINEL", "unit": "b", "label": "R", "counts": [1, 8]},
                     {"observation": "held-repeat", "unit": "b", "label": "R", "counts": [2, 9]}]}
    partition, manifest = sealed_fixture(flow.ledger, worker.training.put(json.dumps(training).encode()),
                                         worker.evaluation.put(json.dumps(held).encode()))
    flow = planner.adaptive_workflow("study", flow.ledger, partition_check=partition)
    worker = planner.exploratory_executor("study", flow.ledger, [{"threshold": 1}], partition=partition)
    for version, key, uses in (("train", "training", ("exploratory", "protected_evaluation")),
                               ("held", "evaluation", ("protected_evaluation",))):
        item = artifact(version, kind="counts", representation="COUNT-1", measurement_history=("raw",),
                        label_origin="independent", permitted_uses=uses)
        flow.register_artifact(item.model_copy(update={"sha256": manifest[key]["sha256"],
            "unit_membership_sha256": manifest[key]["units_sha256"]}))
    flow.record_basis("exposure", "exposure_basis", digest("training-only diagnostic"))
    common = """
import json, math, statistics
from pathlib import Path
def read(name):
    return json.loads(Path('/inputs/' + name).read_bytes())
def write(name, value):
    Path('/outputs/' + name + '.json').write_text(json.dumps(value, allow_nan=False))
"""
    stages = [
        ("transform", "continuous_matrix", (link("counts", "train", kind="counts", immutable=True),), """
dataset = read('counts')
rows = dataset['rows']
assert all(row['unit'] != 'b' and row['observation'] != 'EVALUATION_SENTINEL' for row in rows)
for row in rows:
    row['x'] = math.log1p(row['counts'][0] / sum(row['counts']) * 10000)
write('out', dataset)
"""),
        ("embed", "embedding", (link("x", "transform", kind="continuous_matrix"),), """
dataset = read('x')
rows = dataset['rows']
mean = statistics.mean(row['x'] for row in rows)
for row in rows:
    row['z'] = row['x'] - mean
write('out', {'features':dataset['features'], 'mean':mean, 'rows':rows})
"""),
        ("fit", "model", (link("x", "embed", kind="embedding"),), """
data = read('x')
units = {}
for row in data['rows']:
    units.setdefault((row['unit'], row['label']), []).append(row['z'])
labels = {label for _, label in units}
write('out', {'features':data['features'], 'mean':data['mean'], 'centroids':{
    label:statistics.mean(statistics.mean(x) for (_, l),x in units.items() if l==label) for label in labels}})
"""),
        ("graph", "graph", (link("x", "embed", kind="embedding"),), """
rows = read('x')['rows']
neighbors = {row['observation']: min((abs(row['z']-other['z']),other['observation'])
    for other in rows if other != row)[1] for row in rows}
write('out', neighbors)
"""),
        ("subgroup", "membership_view", (link("graph", "graph", kind="graph"), link("x", "embed", kind="embedding")), """
graph, rows = read('graph'), read('x')['rows']
write('out', [row for row in rows if graph[row['observation']] in {'s1', 's2'}])
"""),
        ("figure", "figure", (link("subset", "subgroup", kind="membership_view"),), """
from xml.etree.ElementTree import Element, SubElement, tostring
rows = read('subset')
chart = Element('svg', xmlns='http://www.w3.org/2000/svg', width='160', height='100')
for index, row in enumerate(rows):
    SubElement(chart,'circle',{'data-observation':row['observation'], 'cx':str(25 + 40*index),
                               'cy':str(round(50 - 20*row['z'], 2)), 'r':'4'})
write('out', {'figure_id':'synthetic-subgroup', 'svg':tostring(chart, encoding='unicode')})
"""),
        ("evaluate", "scores", (link("held", "held", kind="counts", immutable=True),
                                  link("model", "fit", kind="model")), GROUPED_EVALUATOR_SOURCE),
    ]
    for name, kind, links, code in stages:
        method = register(methods, tmp_path / name, name, tuple(item.port.name for item in links), ("out",),
                          code if name == "evaluate" else common + code)
        representations = ["COUNT-1" if item.port.artifact_kind == "counts" else item.port.artifact_kind for item in links]
        extras = ({"held_out":True, "purpose":"protected_evaluation", "shared_units":False}
                  if name == "evaluate" else {})
        flow.insert(bound_step(method, step(name, *links, kind=kind, representations=representations,
                    basis=({"basis_id":"exposure"},) if name == "transform" else (), **extras)))
    assert advance_adaptive_workflow(planner, "study", ledger=flow.ledger, executor=worker,
        partition_check=partition, max_steps=6, actor="tester", allow_data_execution=True)["steps_dispatched"] == 6
    assert flow.status()["blocked"]["evaluate"] == "protected_partition_entitlement_mismatch"
    with flow.ledger._database() as db:
        db.execute("BEGIN")
        envelope = flow._envelope(flow._state(db), "evaluate", "tester", db=db, authorization_draft=True)
    alternative = register(methods, tmp_path / "unsafe-eval", "alternate-eval", ("held", "model"), ("out",),
                           GROUPED_EVALUATOR_SOURCE + "\n# altered evaluator\n")
    envelope["step"]["oci_method_version_id"] = alternative["version_id"]
    with pytest.raises(PermissionError, match="exact frozen grouped scoring"):
        worker.validate(envelope)
    authorize_holdout(flow, "evaluate")
    assert advance_adaptive_workflow(planner, "study", ledger=flow.ledger, executor=worker,
        partition_check=partition, actor="tester", allow_data_execution=True)["steps_dispatched"] == 1
    assert all(value == "succeeded" for value in flow.status()["outcomes"].values())
    eval_output = flow.history("evaluate")[0]["outputs"]["out"]
    for purpose in ("cache", "query", "export", "replay-current-use"):
        with pytest.raises(OperationBlocked, match="delivery_denied"):
            flow.current_use("evaluate", "out", purpose=purpose)
    with worker.evaluation.open(eval_output["sha256"]) as stream:
        assert json.load(stream) == {"units": 1, "accuracy": 1.0}
    with worker.training.open(flow.current_use("graph", "out", purpose="cache").sha256) as stream:
        assert json.load(stream) == {"s1":"s2", "s2":"s1", "s3":"s4", "s4":"s3"}
    with worker.training.open(flow.current_use("figure", "out", purpose="cache").sha256) as stream:
        from xml.etree.ElementTree import fromstring
        chart = fromstring(json.load(stream)["svg"])
        assert [mark.attrib["data-observation"] for mark in chart] == ["s1", "s2"]
    attempt = next(a["token"] for a in flow.authority.attempts("study")
                   if a["token"]["attempt_id"] == flow.history("figure")[0]["attempt_id"])
    envelope, _ = flow._load_dispatch(attempt)
    assert envelope["basis"]["exposure"]["sha256"] == digest("training-only diagnostic")
    assert set(envelope["inputs"]) == {"subset"}
    figure_io = json.loads((worker.runs / attempt["attempt_id"] / "io.json").read_bytes())
    assert figure_io["figures"]["out"]["source_artifact_id"] == digest(envelope["pins"])
    print(json.dumps({"grouped_evaluation": eval_output["sha256"], "figure_receipt": flow.history("figure")[0]["receipt_sha256"],
                      "evaluation_receipt": flow.history("evaluate")[0]["receipt_sha256"]}))


@docker
@wal
@pytest.mark.parametrize("fault", ["logs", "file", "symlink"])
def test_real_overflow_or_link_fails_without_accepted_outputs(tmp_path, fault):
    from devbench.adaptive.exploration import ExploratoryOCI

    tmp_path = tmp_path.resolve()
    flow = make(tmp_path, name=LEDGER_NAME)
    methods = MethodService(tmp_path)
    worker = ExploratoryOCI(methods, flow.ledger, [{"threshold": 1}])
    program = {
        "logs": "print('z'*1_100_000, flush=True)",
        "file": "open('/outputs/out.json','w').write('z'*2_000_000)",
        "symlink": "import os; os.symlink('/method/main.py', '/outputs/out.json')",
    }[fault]
    method = register(methods, tmp_path / "code", fault, (), ("out",), program, max_output_bytes=65_536)
    flow.insert(bound_step(method, step(fault)))
    outcome = flow.dispatch(fault, worker, actor="tester")
    assert outcome["status"] == "failed" and not outcome["outputs"]
    io = json.loads((worker.runs / outcome["attempt_id"] / "io.json").read_bytes())
    runtime = io["runtime"]
    if fault == "logs":
        assert "byte limit" in runtime["error"] and len(runtime["stdout"]) <= 500_000
    elif fault == "file":
        assert "File too large" in runtime["stderr"] or "space left" in runtime["stderr"]
    else:
        assert "regular output files" in runtime["diagnostic_collection_error"]
    print(json.dumps({"fault": fault, "io_sha256": flow.history(fault)[0]["receipt_sha256"],
                      "exit": runtime.get("exit_code"), "error": runtime.get("error")}))


def dispatch_child(root):
    from devbench.adaptive.exploration import ExploratoryOCI
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    ledger = WorkflowOperationLedger(root / LEDGER_NAME, scientific=True)
    flow = AdaptiveWorkflow(ledger, "study")
    flow.dispatch("live", ExploratoryOCI(MethodService(root), ledger, [{"threshold": 1}]), actor="tester")


@docker
@wal
@pytest.mark.parametrize("effect", ["recover_live", "independent_deadline", "cancel_live"])
def test_real_coordinator_death_or_cancel_while_worker_is_alive(tmp_path, effect):
    import multiprocessing
    import signal
    import time

    from devbench.adaptive.exploration import ExploratoryOCI
    from devbench.engine.workflow_operations import OperationBlocked
    from devharness_controlled.native_runtime import LocalDocker, NativeRuntimeError

    root = tmp_path.resolve()
    flow = make(root, name=LEDGER_NAME)
    methods = MethodService(root)
    worker = ExploratoryOCI(methods, flow.ledger, [{"threshold": 1}])
    method = register(methods, root / "code", "live", (), ("out",), """
import time
from pathlib import Path
Path('/outputs/out.json').write_text('{"synthetic":true}')
time.sleep(120)
""", timeout_seconds=5 if effect == "independent_deadline" else 25)
    flow.insert(bound_step(method, step("live")))
    child = multiprocessing.get_context("spawn").Process(target=dispatch_child, args=(root,))
    child.start()
    binding = None
    try:
        deadline = time.monotonic() + 12
        runtime = LocalDocker()
        while time.monotonic() < deadline:
            for path in worker.runs.glob("*/binding.json"):
                binding = json.loads(path.read_bytes())
            if binding and binding["event"] == "worker_exec_intent":
                try:
                    runtime._call(["exec", binding["container_id"], "/usr/bin/test", "-f", "/outputs/out.json"], timeout=2)
                    break
                except NativeRuntimeError:
                    pass
            time.sleep(.1)
        else:
            pytest.fail("Never observed the live worker's diagnostic file")
        token = flow.authority.attempts("study")[0]["token"]
        with pytest.raises(OperationBlocked, match="still_active"):
            worker.recover(flow, token)
        if effect == "cancel_live":
            observation = LocalDockerExecutor().cancel_run(binding)
            assert observation["state"] == "cancelled"
            assert observation["diagnostic_collection"]["files"]["out.json"]
            child.join(10)
            assert child.exitcode == 0
            assert flow.history("live")[0]["status"] == "failed"
        else:
            os.kill(child.pid, signal.SIGKILL)
            child.join(5)
            assert child.exitcode == -signal.SIGKILL
            assert LocalDockerExecutor().inspect_run(binding)["state"] == "running"
            if effect == "independent_deadline":
                deadline = time.monotonic() + 17
                while time.monotonic() < deadline:
                    if LocalDockerExecutor().inspect_run(binding)["state"] == "exited":
                        break
                    time.sleep(.2)
                else:
                    pytest.fail("Container failed to exit at its independent deadline")
            assert worker.recover(flow, token)["status"] == "failed"
            recovery = json.loads((worker.runs / token["attempt_id"] / "recovery.json").read_bytes())
            if effect == "recover_live":
                assert recovery["diagnostic_collection"]["files"]["out.json"]
            else:
                assert recovery["diagnostic_collection"] is None
                assert recovery["diagnostic_loss"] == "stopped_container_tmpfs_unavailable"
        assert LocalDockerExecutor().inspect_run(binding)["state"] == "missing"
        print(json.dumps({"effect":effect, "container_id":binding["container_id"],
                          "receipt_sha256":flow.history("live")[0]["receipt_sha256"], "cleanup":"confirmed"}))
    finally:
        if child.is_alive():
            child.kill()
            child.join(5)
        if binding and LocalDockerExecutor().inspect_run(binding)["state"] not in {"missing", "exited"}:
            LocalDockerExecutor().cancel_run(binding)


@wal
@pytest.mark.parametrize("interruption", ["before_directory", "before_lease", "before_frozen", "after_frozen"])
def test_recover_unlaunched_oci_reconciles_and_fences_late_coordinator(tmp_path, monkeypatch, interruption):
    from devbench.adaptive.exploration import ExploratoryOCI

    root = tmp_path.resolve()
    flow = make(root, name=LEDGER_NAME)
    methods = MethodService(root)
    worker = ExploratoryOCI(methods, flow.ledger, [{"threshold": 1}])
    method = register(methods, root / "code", "retry", (), ("out",), "pass")
    flow.insert(bound_step(method, step("retry")))
    original = ExploratoryOCI.run

    def crashed(self, envelope, *, token):
        directory = self._directory(token)
        if interruption != "before_directory":
            directory.mkdir()
        if interruption in {"before_frozen", "after_frozen"}:
            (directory / "lease").touch()
        if interruption == "after_frozen":
            self._write(directory / "frozen.json", self._invocation(envelope, token))
        raise RuntimeError("synthetic coordinator failure")

    monkeypatch.setattr(ExploratoryOCI, "run", crashed)
    with pytest.raises(RuntimeError, match="synthetic coordinator"):
        flow.dispatch("retry", worker, actor="tester")
    token = flow.authority.attempts("study")[0]["token"]
    envelope, _ = flow._load_dispatch(token)
    assert flow.status()["blocked"]["retry"] == "adaptive_attempt_requires_reconciliation"
    recovery = worker.recover(flow, token)
    assert recovery["execution_state"] == "noncompletion_confirmed"
    assert recovery["cleanup_state"] == "confirmed"
    assert "retry" in flow.ready()
    assert worker.recover(flow, token) == recovery
    with pytest.raises(PermissionError):
        original(worker, envelope, token=token)


@wal
def test_held_out_failure_embargoes_diagnostics_even_if_cleanup_is_uncertain(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from devbench.adaptive.exploration import ExploratoryOCI
    from devbench.engine.partition_authority import PartitionAuthority, embargoed

    flow = make(tmp_path.resolve(), name=LEDGER_NAME)
    methods = MethodService(tmp_path.resolve())
    partition = PartitionAuthority(flow.ledger, digest("diagnostic cohort"))
    worker = ExploratoryOCI(methods, flow.ledger, (), partition=partition)
    directory = worker.runs / ("a" * 32)
    directory.mkdir()
    token = {"attempt_id": directory.name}
    frozen = {"token": token}
    worker._write(directory / "frozen.json", frozen)
    identity = digest("synthetic diagnostic")
    binding = {"output_dir": str(directory / "outputs")}
    worker._write(directory / "binding.json", binding)
    monkeypatch.setattr(LocalDockerExecutor, "_dispatch_state", lambda _: None)

    def cancel(self, binding):
        with flow.ledger._database() as db:
            assert embargoed(db, identity)
        return {"state": "running"}

    monkeypatch.setattr(LocalDockerExecutor, "cancel_run", cancel)
    monkeypatch.setattr(methods, "artifact_path", lambda key: key)
    monkeypatch.setattr(LocalDockerExecutor, "execute", lambda *args, **kwargs: {
        "exit_code": 1, "diagnostic_collection": {"files": {"result.json": identity}}})
    receipt = worker._execute({"step": {"step_id": "score"}, "inputs": {}, "pins": []},
        token, directory, frozen, SimpleNamespace(held_out=True),
        {"contract": {}, "source_artifact": "source", "environment_artifact": "lock"}, {})
    assert receipt.status == "failed" and receipt.cleanup_state == "unknown"
    assert (directory / "receipt.json").exists()
    with flow.ledger._database() as db:
        assert embargoed(db, identity)
