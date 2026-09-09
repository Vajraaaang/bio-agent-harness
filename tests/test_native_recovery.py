"""Recovery infrastructure doubles; no Docker, model, or clinical execution."""
import copy
import hashlib
import os
from pathlib import Path

import pytest

import devharness_controlled.native_recovery as module
from devharness_controlled.contracts import canonical_json_bytes
from devharness_controlled.native_contracts import NativeAnalysisPlan
from devharness_controlled.native_runtime import (
    NATIVE_IMAGE_ID, PROFILE_VERSION, approved_environment, container_invocation,
    container_profile, create_arguments,
)
from devharness_controlled.run_registry import LeaseBusyError, RunRegistry
from devharness_core.provenance import verify_execution
from devharness_controlled.qualification import synthetic_approvals

CID = "b" * 64
OTHER = "c" * 64
ACTOR = "synthetic-recovery-test"


def write(path, value):
    path.write_bytes(canonical_json_bytes(value))


def digest(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def setup(tmp_path, *, stage="started", marker=True, version=PROFILE_VERSION):
    registry = RunRegistry(tmp_path / "registry")
    workspace = tmp_path / "attempt"
    source = {"synthetic_source.py": "a" * 64}
    docker = {"endpoint": "unix:///synthetic/docker.sock", "daemon_id": "synthetic-daemon",
              "image": {"id": NATIVE_IMAGE_ID, "os": "linux", "architecture": "amd64"}}
    environment = {"docker": docker, "uid": 1000, "gid": 1000, "profile_version": version,
                   "coordinator": {"scope": "test-double-only"}}
    plan = NativeAnalysisPlan(**dict(plan_id="native-recovery-test", revision=1, intended_use_ref="software-tests-only", study_id="synthetic",
        data_classification="synthetic", engine="edger_qlf", normalization="rle", hypothesis_genes=("g1",),
        panel_genes=("g1", "g2"), sample_ids=("s1", "s2", "s3"), temporal_columns=(2,),
        time_unit="days", basis_definition_ref="synthetic-linear", platform="linux/amd64",
        max_memory_mb=256, max_cpus=1, timeout_sec=60,
        counts_sha256="a"*64, metadata_sha256="a"*64, full_design_sha256="a"*64,
        reduced_design_sha256="a"*64, driver_sha256="a"*64, package_lock_sha256="a"*64,
        runtime_image_id=NATIVE_IMAGE_ID,
        implementation_sha256=digest(source), environment_sha256=digest(environment)))
    _, signers = synthetic_approvals(plan)
    attempt = registry.register("synthetic-request", plan.sha256(), workspace, ACTOR,
                                submission_sha256="d" * 64)
    if stage != "registered":
        registry.append_event(attempt["attempt_id"], "started", {"scope": "synthetic-infrastructure-double"}, ACTOR)
    if marker:
        workspace.mkdir()
        write(workspace / "operation.json", module._binding(attempt, registry.registry_id))
        (workspace / "plan.json").write_bytes(plan.canonical_bytes())
    if stage in {"registered", "started"}:
        return registry, registry.get(attempt["attempt_id"]), workspace, plan, signers, None, None
    for directory in ("inputs", "native-output", "native-logs", "native-client"):
        (workspace / directory).mkdir()
    write(workspace / "environment.json", {
        "source": source, "environment": environment, "implementation_sha256": plan.implementation_sha256,
        "environment_sha256": plan.environment_sha256, "runtime_image_id": NATIVE_IMAGE_ID,
        "platform": "linux/amd64", "driver_sha256": plan.driver_sha256, "package_lock_sha256": plan.package_lock_sha256})
    profile = container_profile(plan, workspace / "inputs", workspace / "native-output", uid=1000, gid=1000)
    profile["schema_version"] = version
    name = "devharness-native-" + attempt["attempt_id"]
    command = ["/synthetic/docker", "--config", str(workspace / "native-client"), "--host", docker["endpoint"],
               *create_arguments(profile, plan.sha256(), name)]
    payloads = {
        "runtime_intent": {"image_id": NATIVE_IMAGE_ID, "profile": profile, "command": command},
        "container_observed": {"container_id": CID},
        "launch_authorized": {"container_id": CID, "receipts": []},
        "start_requested": {"container_id": CID},
    }
    from datetime import datetime, timezone
    for kind, payload in payloads.items():
        event = "attach_started" if kind == "start_requested" and version == "native_container_profile/1" else module._LIFECYCLE[kind]
        registry.append_event(attempt["attempt_id"], kind, {"schema_version": "native_lifecycle/1",
            "event": event, "recorded_at": datetime.now(timezone.utc).isoformat(),
            "plan_sha256": plan.sha256(), "container_name": name, "payload": payload}, ACTOR)
        if kind == stage:
            break
    entrypoint, command = container_invocation(profile)
    record = {"Id": CID, "Name": "/" + name, "Image": NATIVE_IMAGE_ID,
        "Config": {"User": "1000:1000", "Entrypoint": [entrypoint], "Cmd": command,
                   "Labels": {"io.devharness.plan-sha256": plan.sha256()}, "Env": approved_environment()},
        "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True, "Privileged": False,
            "Memory": plan.max_memory_mb * 1024**2, "MemorySwap": plan.max_memory_mb * 1024**2,
            "NanoCpus": plan.max_cpus * 10**9, "PidsLimit": 64, "PidMode": "", "IpcMode": "private",
            "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges=true"],
            "LogConfig": {"Type": "none", "Config": {}}, "Tmpfs": {"/tmp": profile["tmpfs"]}},
        "Mounts": [{"Type": "bind", "Source": str(workspace / "inputs"), "Destination": "/input", "RW": False},
                   {"Type": "bind", "Source": str(workspace / "native-output"), "Destination": "/output", "RW": True}],
        "State": {"Running": True, "ExitCode": 0, "OOMKilled": False, "Status": "running"}}
    return registry, registry.get(attempt["attempt_id"]), workspace, plan, signers, profile, record


class DockerDouble:
    def __init__(self, record, *, daemon="synthetic-daemon", unavailable=False):
        self.record, self.daemon, self.unavailable = copy.deepcopy(record), daemon, unavailable
        self.calls, self.endpoint, self.client = [], None, None
        self.before_call = None
        self.before_inspect = None
        self.kill_fails = False
        self.keep_running = False
        self.remove_fails = False

    def factory(self, *, endpoint):
        self.endpoint = endpoint
        self.calls.append(["connect", endpoint])
        return self

    def isolate_client(self, directory):
        self.client = directory
        directory.mkdir()

    def identity(self):
        if self.unavailable:
            raise ValueError("Synthetic daemon unavailable")
        return {"endpoint": self.endpoint, "daemon_id": self.daemon,
                "image": {"id": NATIVE_IMAGE_ID, "os": "linux", "architecture": "amd64"}}

    def _call(self, arguments):
        if self.before_call:
            self.before_call(arguments)
        self.calls.append(list(arguments))
        if arguments[:2] == ["container", "ls"]:
            if self.record is None:
                return ""
            filter_text = arguments[arguments.index("--filter") + 1]
            if filter_text.startswith("id="):
                return self.record["Id"] if self.record["Id"] == filter_text[3:] else ""
            return self.record["Id"]
        if arguments[:2] == ["container", "kill"]:
            assert arguments == ["container", "kill", "--signal", "KILL", CID]
            if not self.keep_running:
                self.record["State"]["Running"] = False
            if self.kill_fails:
                raise ValueError("Synthetic container already exited")
            return CID
        if arguments[:2] == ["container", "rm"]:
            assert arguments == ["container", "rm", CID]
            if self.remove_fails:
                raise ValueError("Synthetic removal failure")
            self.record = None
            return CID
        pytest.fail("Unapproved recovery Docker command: " + str(arguments))

    def inspect(self, cid):
        self.calls.append(["inspect", cid])
        if self.before_inspect:
            self.before_inspect(self)
        return copy.deepcopy(self.record)


def recover(tmp_path, fixture, monkeypatch, docker=None, **kwargs):
    registry, attempt, workspace, plan, signers, _, _ = fixture
    if docker is None:
        monkeypatch.setattr(module, "LocalDocker", lambda **kw: pytest.fail("Recovery must not contact Docker"))
    else:
        monkeypatch.setattr(module, "LocalDocker", docker.factory)
    return module.recover_native_attempt(registry.directory, attempt["attempt_id"], signers,
        actor_id=ACTOR, output_dir=tmp_path / "recovery", **kwargs)


def test_registered_attempt_is_interrupted_without_reading_unowned_workspace(tmp_path, monkeypatch):
    fixture = setup(tmp_path, stage="registered", marker=False)
    workspace = fixture[2]
    workspace.mkdir()
    (workspace / "unrelated.txt").write_text("must never be inventoried")
    checkpoint = fixture[0].export_checkpoint()
    report = recover(tmp_path, fixture, monkeypatch, expected_checkpoint=checkpoint)
    assert report["status"] == "reconciled" and report["registry_status_after"] == "interrupted"
    assert report["analysis_completed"] is False and report["clinical_release_allowed"] is False
    assert not (tmp_path / "recovery/original-snapshot").exists()
    assert (workspace / "unrelated.txt").read_text() == "must never be inventoried"
    assert verify_execution(tmp_path / "recovery/audit/manifest.json", report["receipt"]["root_hash"])["valid"]
    assert fixture[0].verify_integrity(checkpoint)["externally_anchored"]
    assert report["registry_terminal_event"]["payload"]["recovery_root_hash"] == report["receipt"]["root_hash"]


def test_started_owned_attempt_is_snapshotted_without_modifying_original(tmp_path, monkeypatch):
    fixture = setup(tmp_path)
    before = {p.name: p.read_bytes() for p in fixture[2].iterdir()}
    report = recover(tmp_path, fixture, monkeypatch)
    assert report["registry_status_after"] == "interrupted"
    assert report["original_inventory"]["file_count"] == 2
    assert before == {p.name: p.read_bytes() for p in fixture[2].iterdir()}
    assert not (fixture[2] / "receipt.json").exists()
    assert (tmp_path / "recovery/original-snapshot/plan.json").read_bytes() == before["plan.json"]


def test_busy_lease_refuses_before_output_or_docker(tmp_path, monkeypatch):
    fixture = setup(tmp_path)
    with fixture[0].lease(fixture[1]["attempt_id"]), pytest.raises(LeaseBusyError):
        recover(tmp_path, fixture, monkeypatch)
    assert not (tmp_path / "recovery").exists()
    assert fixture[0].get(fixture[1]["attempt_id"])["status"] == "started"


@pytest.mark.parametrize("location", ["workspace", "workspace_child", "registry", "registry_child", "ancestor"])
def test_output_must_be_fresh_and_disjoint(tmp_path, monkeypatch, location):
    fixture = setup(tmp_path)
    registry, attempt, workspace, _, signers, _, _ = fixture
    choices = {"workspace": workspace, "workspace_child": workspace / "recovery",
               "registry": registry.directory, "registry_child": registry.directory / "recovery", "ancestor": tmp_path}
    monkeypatch.setattr(module, "LocalDocker", lambda **kw: pytest.fail("No Docker"))
    with pytest.raises(ValueError, match="overlap"):
        module.recover_native_attempt(registry.directory, attempt["attempt_id"], signers,
                                      actor_id=ACTOR, output_dir=choices[location])


@pytest.mark.parametrize("kind", ["missing_marker", "wrong_marker", "wrong_plan", "symlink_marker"])
def test_unbound_original_is_never_inventoried_or_used_for_cleanup(tmp_path, monkeypatch, kind):
    fixture = setup(tmp_path, stage="runtime_intent")
    workspace = fixture[2]
    if kind == "missing_marker":
        (workspace / "operation.json").unlink()
    elif kind == "wrong_marker":
        value = module._binding(fixture[1], fixture[0].registry_id)
        value["attempt_id"] = "0" * 32
        write(workspace / "operation.json", value)
    elif kind == "wrong_plan":
        changed = NativeAnalysisPlan(**{**fixture[3].model_dump(), "revision": 2})
        (workspace / "plan.json").write_bytes(changed.canonical_bytes())
    else:
        (workspace / "operation.json").unlink()
        (workspace / "operation.json").symlink_to(tmp_path / "unrelated")
    report = recover(tmp_path, fixture, monkeypatch)
    assert report["status"] == "unresolved" and report["registry_status_after"] == "runtime_intent"
    assert not (tmp_path / "recovery/original-snapshot").exists()


def test_intent_without_cid_and_empty_discovery_remains_unknown(tmp_path, monkeypatch):
    fixture = setup(tmp_path, stage="runtime_intent")
    docker = DockerDouble(None)
    before = fixture[0].export_checkpoint()
    report = recover(tmp_path, fixture, monkeypatch, docker)
    assert report["status"] == "unresolved" and report["registry_status_after"] == "runtime_intent"
    assert report["cleanup"]["status"] == "unknown_create_may_be_in_flight"
    assert fixture[0].export_checkpoint() == before
    assert not (tmp_path / "recovery/original-snapshot").exists()
    assert not any(call[:2] in (["container", "kill"], ["container", "rm"]) for call in docker.calls)


def test_recorded_cid_confirmed_absent_can_be_interrupted(tmp_path, monkeypatch):
    fixture = setup(tmp_path, stage="container_observed")
    docker = DockerDouble(None)
    report = recover(tmp_path, fixture, monkeypatch, docker)
    assert report["status"] == "reconciled" and report["registry_status_after"] == "interrupted"
    assert report["cleanup"]["status"] == "recorded_container_absent"
    assert any("id=" + CID in c for c in docker.calls)


@pytest.mark.parametrize("stage", ["runtime_intent", "container_observed", "launch_authorized", "start_requested"])
@pytest.mark.parametrize("version", ["native_container_profile/1", "native_container_profile/2"])
def test_owned_container_is_stopped_removed_then_snapshotted(tmp_path, monkeypatch, stage, version):
    fixture = setup(tmp_path, stage=stage, version=version)
    docker = DockerDouble(fixture[-1])
    order = []
    original_snapshot = module._snapshot_original

    def snapshot(*args):
        assert docker.record is None, "Never snapshot running container outputs"
        order.append("snapshot")
        return original_snapshot(*args)

    def check_lease(arguments):
        with pytest.raises(LeaseBusyError):
            with fixture[0].lease(fixture[1]["attempt_id"]):
                pass
        order.append(arguments[1])

    docker.before_call = check_lease
    monkeypatch.setattr(module, "_snapshot_original", snapshot)
    report = recover(tmp_path, fixture, monkeypatch, docker)
    assert report["registry_status_after"] == "interrupted" and report["ownership_verified"]
    assert report["cleanup"]["status"] == "owned_container_removed"
    assert order.index("kill") < order.index("rm") < order.index("snapshot")
    assert not report["analysis_completed"]
    assert not (fixture[2] / "receipt.json").exists()


@pytest.mark.parametrize("mutation", ["image", "name", "label", "memory", "input_write", "command", "extra_mount", "proxy", "cid"])
def test_mismatched_container_is_not_signaled_or_removed(tmp_path, monkeypatch, mutation):
    fixture = setup(tmp_path, stage="container_observed")
    record = copy.deepcopy(fixture[-1])
    if mutation == "image": record["Image"] = "sha256:" + "0" * 64
    if mutation == "name": record["Name"] = "/unrelated"
    if mutation == "label": record["Config"]["Labels"] = {}
    if mutation == "memory": record["HostConfig"]["Memory"] += 1
    if mutation == "input_write": record["Mounts"][0]["RW"] = True
    if mutation == "command": record["Config"]["Cmd"] = ["other-command"]
    if mutation == "extra_mount": record["Mounts"].append({"Source": "/unrelated", "Destination": "/other", "RW": True})
    if mutation == "proxy": record["Config"]["Env"].append("HTTPS_PROXY=<synthetic-unapproved>")
    if mutation == "cid": record["Id"] = OTHER
    docker = DockerDouble(record)
    report = recover(tmp_path, fixture, monkeypatch, docker)
    assert report["status"] == "unresolved" and report["registry_status_after"] == "container_observed"
    assert not any(call[:2] in (["container", "kill"], ["container", "rm"]) for call in docker.calls)
    assert not (tmp_path / "recovery/original-snapshot").exists()
    assert "<synthetic-unapproved>" not in (tmp_path / "recovery/recovery.json").read_text()


@pytest.mark.parametrize("mode", ["unavailable", "replaced"])
def test_daemon_failure_or_replacement_keeps_registry_nonterminal(tmp_path, monkeypatch, mode):
    fixture = setup(tmp_path, stage="container_observed")
    docker = DockerDouble(fixture[-1], daemon="different" if mode == "replaced" else "synthetic-daemon",
                          unavailable=mode == "unavailable")
    report = recover(tmp_path, fixture, monkeypatch, docker)
    assert report["status"] == "unresolved" and report["registry_status_after"] == "container_observed"
    assert all(c[0] == "connect" for c in docker.calls)


def test_container_exit_race_is_reinspected_before_safe_removal(tmp_path, monkeypatch):
    fixture = setup(tmp_path, stage="container_observed")
    docker = DockerDouble(fixture[-1]); docker.kill_fails = True
    report = recover(tmp_path, fixture, monkeypatch, docker)
    assert report["registry_status_after"] == "interrupted"
    assert report["cleanup"]["actions"][0]["status"] == "command_failed"
    assert report["cleanup"]["status"] == "owned_container_removed"


def test_still_running_after_failed_kill_is_not_removed(tmp_path, monkeypatch):
    fixture = setup(tmp_path, stage="container_observed")
    docker = DockerDouble(fixture[-1]); docker.kill_fails = docker.keep_running = True
    report = recover(tmp_path, fixture, monkeypatch, docker)
    assert report["registry_status_after"] == "container_observed"
    assert not any(c[:2] == ["container", "rm"] for c in docker.calls)
    assert not (tmp_path / "recovery/original-snapshot").exists()


def test_identity_rechecked_after_stop_and_before_removal(tmp_path, monkeypatch):
    fixture = setup(tmp_path, stage="container_observed")
    docker = DockerDouble(fixture[-1])
    def change_after_stop(client):
        if not client.record["State"]["Running"]:
            client.record["Config"]["Labels"] = {}
    docker.before_inspect = change_after_stop
    report = recover(tmp_path, fixture, monkeypatch, docker)
    assert report["registry_status_after"] == "container_observed"
    assert not any(c[:2] == ["container", "rm"] for c in docker.calls)


@pytest.mark.parametrize("mode", ["symlink", "fifo", "hardlink", "unexpected_root", "file_limit", "byte_limit", "entry_limit"])
def test_unsafe_or_unbounded_original_inventory_never_gets_terminal_commit(tmp_path, monkeypatch, mode):
    fixture = setup(tmp_path)
    workspace = fixture[2]
    (workspace / "native-output").mkdir()
    path = workspace / "native-output/result.json"
    if mode == "symlink": path.symlink_to(tmp_path / "unrelated")
    elif mode == "fifo": os.mkfifo(path)
    elif mode == "hardlink": os.link(workspace / "plan.json", path)
    elif mode == "unexpected_root": (workspace / "unrelated.txt").write_text("do not inventory")
    elif mode == "file_limit": monkeypatch.setattr(module, "MAX_SNAPSHOT_FILES", 1)
    elif mode == "byte_limit": monkeypatch.setattr(module, "MAX_SNAPSHOT_BYTES", 1)
    else: monkeypatch.setattr(module, "MAX_SNAPSHOT_ENTRIES", 1)
    report = recover(tmp_path, fixture, monkeypatch)
    assert report["status"] == "unresolved" and report["registry_status_after"] == "started"
    assert report["registry_terminal_event"] is None
    assert verify_execution(tmp_path / "recovery/audit/manifest.json", report["receipt"]["root_hash"])["valid"]


def test_external_checkpoint_mismatch_prevents_observation_and_output(tmp_path, monkeypatch):
    fixture = setup(tmp_path)
    checkpoint = fixture[0].export_checkpoint()
    checkpoint["checkpoint_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="checkpoint"):
        recover(tmp_path, fixture, monkeypatch, expected_checkpoint=checkpoint)
    assert not (tmp_path / "recovery").exists()


def test_repeated_terminal_observation_does_not_rewrite_terminal_history(tmp_path, monkeypatch):
    fixture = setup(tmp_path)
    first = recover(tmp_path, fixture, monkeypatch)
    before = fixture[0].export_checkpoint()
    registry, attempt, _, _, signers, _, _ = fixture
    second = module.recover_native_attempt(registry.directory, attempt["attempt_id"], signers,
        actor_id="different-caller-label", output_dir=tmp_path / "recovery-again")
    assert second["status"] == "terminal_observation" and second["registry_terminal_event"] is None
    assert first["receipt"]["root_hash"] != second["receipt"]["root_hash"]
    assert registry.export_checkpoint() == before


def test_sealed_receipt_failure_does_not_become_successful_analysis(tmp_path, monkeypatch):
    fixture = setup(tmp_path)
    write(fixture[2] / "receipt.json", {"root_hash": "0" * 64})
    report = recover(tmp_path, fixture, monkeypatch)
    assert report["status"] == "unresolved" and report["registry_status_after"] == "started"
    assert not report["analysis_completed"]


@pytest.mark.parametrize("completed", [True, False])
def test_verified_original_can_recover_only_its_lost_terminal_commit(tmp_path, monkeypatch, completed):
    fixture = setup(tmp_path, stage="start_requested")
    write(fixture[2] / "receipt.json", {"root_hash": "f" * 64})
    # Semantic verification is independently tested elsewhere. This double tests
    # only recovery's registry transition using its explicit sealed-verdict API.
    monkeypatch.setattr(module, "_sealed_original", lambda *args: {
        "receipt": {"root_hash": "f" * 64},
        "verification": {"valid": True, "analysis_completed": completed}})
    docker = None if completed else DockerDouble(None)
    report = recover(tmp_path, fixture, monkeypatch, docker)
    assert report["registry_status_after"] == ("completed" if completed else "failed")
    assert report["analysis_completed"] is completed
    assert report["original_execution_root_hash"] == "f" * 64
    assert report["registry_terminal_event"]["payload"]["execution_root_hash"] == "f" * 64
    assert not report["clinical_release_allowed"]


def test_registry_commit_failure_preserves_recovery_receipt_and_allows_reconciliation(tmp_path, monkeypatch):
    fixture = setup(tmp_path)
    real_append = RunRegistry.append_event
    def failed_append(self, *args, **kwargs):
        raise OSError("Synthetic commit failure")
    monkeypatch.setattr(RunRegistry, "append_event", failed_append)
    with pytest.raises(OSError, match="commit failure"):
        recover(tmp_path, fixture, monkeypatch)
    assert (tmp_path / "recovery/receipt.json").is_file()
    assert fixture[0].get(fixture[1]["attempt_id"])["status"] == "started"
    monkeypatch.setattr(RunRegistry, "append_event", real_append)
    report = module.recover_native_attempt(fixture[0].directory, fixture[1]["attempt_id"], fixture[4],
        actor_id=ACTOR, output_dir=tmp_path / "recovery-again")
    assert report["registry_status_after"] == "interrupted"


def test_crash_after_operation_marker_before_plan_does_not_scan_workspace(tmp_path, monkeypatch):
    fixture = setup(tmp_path)
    marker_raw = (fixture[2] / "operation.json").read_bytes()
    (fixture[2] / "plan.json").unlink()
    (fixture[2] / "unrelated.txt").write_text("not inventoried")
    report = recover(tmp_path, fixture, monkeypatch)
    assert report["registry_status_after"] == "interrupted"
    assert not (tmp_path / "recovery/original-snapshot").exists()
    assert (tmp_path / "recovery/original-operation.json").read_bytes() == marker_raw
    assert report["original_operation_marker"]["sha256"] == hashlib.sha256(marker_raw).hexdigest()
    assert report["pre_runtime_observation"] == {"operation_binding_verified": True,
        "plan_verified": False, "inputs_verified": False, "inventory_performed": False}


@pytest.mark.parametrize("missing", ["receipt", "marker_and_receipt", "plan_and_receipt"])
def test_completed_registry_without_original_receipt_is_unresolved_without_docker(tmp_path, monkeypatch, missing):
    fixture = setup(tmp_path, stage="start_requested")
    registry, attempt, workspace, _, _, _, _ = fixture
    registry.append_event(attempt["attempt_id"], "completed", {"execution_root_hash": "f" * 64}, ACTOR)
    if missing == "marker_and_receipt": (workspace / "operation.json").unlink()
    if missing == "plan_and_receipt": (workspace / "plan.json").unlink()
    before = registry.export_checkpoint()
    report = recover(tmp_path, fixture, monkeypatch)
    assert report["status"] == "unresolved" and report["registry_status_after"] == "completed"
    assert report["analysis_completed"] is False and report["registry_terminal_event"] is None
    assert registry.export_checkpoint() == before
    assert not (tmp_path / "recovery/original-snapshot").exists()
    assert any("saved receipt" in e["message"] for e in report["errors"])


def test_marker_without_plan_but_with_receipt_is_not_treated_as_preruntime(tmp_path, monkeypatch):
    fixture = setup(tmp_path)
    (fixture[2] / "plan.json").unlink()
    write(fixture[2] / "receipt.json", {"root_hash": "f" * 64})
    report = recover(tmp_path, fixture, monkeypatch)
    assert report["status"] == "unresolved" and report["registry_status_after"] == "started"
    assert not (tmp_path / "recovery/original-snapshot").exists()


@pytest.mark.parametrize("version,wrong_event", [
    ("native_container_profile/1", "start_requested"),
    ("native_container_profile/2", "attach_started"),
])
def test_recorded_start_event_must_match_its_historical_profile(tmp_path, version, wrong_event):
    fixture = setup(tmp_path, stage="start_requested", version=version)
    attempt = copy.deepcopy(fixture[1])
    next(e for e in attempt["events"] if e["kind"] == "start_requested")["payload"]["event"] = wrong_event
    with pytest.raises(ValueError, match="checkpoint"):
        module._runtime_contract(attempt, fixture[2], fixture[3])


def test_cleanup_intents_are_durable_before_destructive_commands(tmp_path, monkeypatch):
    fixture = setup(tmp_path, stage="container_observed")
    docker = DockerDouble(fixture[-1])
    import json
    def durable_before_call(arguments):
        if arguments[:2] not in (["container", "kill"], ["container", "rm"]):
            return
        nodes = [json.loads(p.read_bytes()) for p in (tmp_path / "recovery/audit/nodes").iterdir()]
        last = max(nodes, key=lambda n: n["sequence"])
        assert last["kind"] == "recovery_cleanup_requested"
        assert last["payload"]["container_id"] == CID
        assert last["payload"]["action"] == ("kill" if arguments[1] == "kill" else "remove")
        assert not (tmp_path / "recovery/receipt.json").exists()
    docker.before_call = durable_before_call
    report = recover(tmp_path, fixture, monkeypatch, docker)
    assert report["registry_status_after"] == "interrupted"


@pytest.mark.parametrize("mode", ["environment_hash", "source_hash", "profile", "command", "endpoint"])
def test_mutated_runtime_binding_is_refused_before_docker(tmp_path, monkeypatch, mode):
    fixture = setup(tmp_path, stage="container_observed")
    import json
    path = fixture[2] / "environment.json"
    identity = json.loads(path.read_bytes())
    if mode == "environment_hash": identity["environment_sha256"] = "0" * 64
    elif mode == "source_hash": identity["source"]["extra.py"] = "0" * 64
    elif mode == "profile": identity["environment"]["uid"] = 0
    elif mode == "endpoint": identity["environment"]["docker"]["endpoint"] = "tcp://remote:2375"
    else:
        # Mutating a captured registry payload would require rehashing its chain;
        # use the pure boundary to test an internally consistent altered command.
        attempt = copy.deepcopy(fixture[1])
        next(e for e in attempt["events"] if e["kind"] == "runtime_intent")["payload"]["payload"]["command"][-1] = "/other.json"
        with pytest.raises(ValueError, match="invocation"):
            module._runtime_contract(attempt, fixture[2], fixture[3])
        return
    write(path, identity)
    report = recover(tmp_path, fixture, monkeypatch)
    assert report["status"] == "unresolved" and report["registry_status_after"] == "container_observed"


def test_failed_remove_is_preserved_and_does_not_commit_interruption(tmp_path, monkeypatch):
    fixture = setup(tmp_path, stage="container_observed")
    docker = DockerDouble(fixture[-1]); docker.remove_fails = True
    report = recover(tmp_path, fixture, monkeypatch, docker)
    assert report["registry_status_after"] == "container_observed"
    assert report["status"] == "unresolved"
    assert report["cleanup"]["actions"][-1] == {"action": "remove", "container_id": CID, "status": "requested"}
    assert docker.record["State"]["Running"] is False
    assert not (tmp_path / "recovery/original-snapshot").exists()


def test_already_exited_owned_container_is_removed_without_kill(tmp_path, monkeypatch):
    fixture = setup(tmp_path, stage="container_observed")
    docker = DockerDouble(fixture[-1]); docker.record["State"]["Running"] = False
    report = recover(tmp_path, fixture, monkeypatch, docker)
    assert report["registry_status_after"] == "interrupted"
    assert not any(c[:2] == ["container", "kill"] for c in docker.calls)


def test_snapshot_detects_mutation_during_streamed_read(tmp_path, monkeypatch):
    fixture = setup(tmp_path)
    (fixture[2] / "native-output").mkdir()
    original = fixture[2] / "native-output/result.json"
    original.write_bytes(b"x" * (1024 * 1024 + 1))
    real_fdopen = module.os.fdopen
    changed = False
    class MutatingReader:
        def __init__(self, handle): self.handle = handle
        def __getattr__(self, name): return getattr(self.handle, name)
        def __enter__(self): return self
        def __exit__(self, *args): self.handle.close()
        def read(self, count):
            nonlocal changed
            raw = self.handle.read(count)
            if count == 1024 * 1024 and len(raw) == count and not changed:
                changed = True
                with original.open("ab") as handle: handle.write(b"changed")
            return raw
    def fdopen(*args, **kwargs):
        handle = real_fdopen(*args, **kwargs)
        return MutatingReader(handle) if args[1] == "rb" else handle
    monkeypatch.setattr(module.os, "fdopen", fdopen)
    report = recover(tmp_path, fixture, monkeypatch)
    assert changed and report["status"] == "unresolved"
    assert report["registry_status_after"] == "started"
    assert any("grew" in e["message"] or "changed" in e["message"] for e in report["errors"])


def test_nested_inventory_entry_bound_cannot_be_evaded_by_pending_siblings(tmp_path, monkeypatch):
    fixture = setup(tmp_path)
    for directory in ("inputs", "native-output", "native-logs", "native-client", "audit"):
        target = fixture[2] / directory
        target.mkdir()
        for index in range(3): (target / str(index)).write_text("x")
    monkeypatch.setattr(module, "MAX_SNAPSHOT_ENTRIES", 8)
    report = recover(tmp_path, fixture, monkeypatch)
    assert report["status"] == "unresolved" and report["registry_status_after"] == "started"
    assert any("entry bound" in e["message"] for e in report["errors"])
