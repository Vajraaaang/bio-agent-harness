import json
import shlex
import sys
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform import plugin_motor_code as code_backend
from devbench.platform import plugin_motor_umap as motor
from devbench.platform.plugin_jobs import digest


@pytest.fixture
def completed(tmp_path, monkeypatch):
    source = tmp_path / "kernel.py"
    source.write_bytes(b"# exact synthetic implementation\n")
    parameters = {"stage_column": "day", "label_column": "class", "counts_layer": "umi"}
    paths = tuple(tmp_path / f"{name} 'test'.h5ad" for name in ("e09", "e10", "e11"))
    for index, path in enumerate(paths):
        path.write_bytes(bytes([index]))
    profiles = [
        {
            "source": str(path),
            "sha256": sha256(path.read_bytes()).hexdigest(),
            "size_bytes": 1,
            "observed": {"cells": 10},
        }
        for path in paths
    ]
    sid, rid = "a" * 32, "b" * 32
    runtime = {
        "python": "3.13.5",
        "packages": {"anndata": "0.12.0"},
        "implementation_sha256": sha256(source.read_bytes()).hexdigest(),
        "compute_target": "local_worker",
        "network_policy": "offline",
    }
    plan = {
        "session_id": sid,
        "plan_id": "c" * 32,
        "runtime": runtime,
        "profiles": profiles,
        "design": {"parameters": parameters},
    }
    plan_sha = digest(plan)
    receipt = {
        "format": "devbench.shared-motor-umap/1",
        "operation": "shared_motor_umap",
        "run_id": rid,
        "session_id": sid,
        "plan_id": plan["plan_id"],
        "plan_sha256": plan_sha,
        "runtime": runtime,
        "cells": 30,
        "features": 3,
        "stages": [],
        "artifacts": {},
    }
    row = {
        "run_id": rid,
        "plan_sha256": plan_sha,
        "status": "completed",
        "stage_json": "{}",
        "error_text": None,
        "receipt_json": json.dumps({**receipt, "receipt_sha256": digest(receipt)}),
    }
    monkeypatch.setattr(
        motor,
        "_studio_record",
        lambda app, identity: (plan, plan_sha, row) if identity == sid else None,
    )
    monkeypatch.setattr(code_backend, "implementation_path", lambda: source)
    app = SimpleNamespace(
        offline_only=True, allow_data_execution=False, _path=tmp_path.joinpath
    )
    return SimpleNamespace(
        app=app,
        sid=sid,
        source=source,
        paths=paths,
        runtime=runtime,
        parameters=parameters,
    )


def test_studio_publishes_only_the_exact_kernel_source(completed):
    code = motor.studio_view(completed.app, completed.sid)["code"]
    assert (
        code["entry_point"]
        == "devbench.analysis.shared_motor_umap.run_shared_motor_umap"
    )
    assert str(completed.source.parent) not in json.dumps(code)
    assert [item["stage"] for item in code["inputs"]] == ["e09", "e10", "e11"]
    checksum = completed.runtime["implementation_sha256"]
    assert code["source"]["url"].endswith(f"/code/source/{checksum}")
    assert motor.studio_code_artifact(
        completed.app, completed.sid, "source", checksum
    ) == (
        completed.source.read_bytes(),
        "text/plain",
        "shared_motor_umap.py",
    )
    completed.source.write_bytes(b"# revised implementation\n")
    assert "source" not in motor.studio_view(completed.app, completed.sid)["code"]
    with pytest.raises(OperationBlocked, match="shared_motor_umap_code_unavailable"):
        motor.studio_code_artifact(completed.app, completed.sid, "source", checksum)


def test_generated_replay_checks_input_order_and_runtime(
    completed, monkeypatch, capsys
):
    code = motor.studio_view(completed.app, completed.sid)["code"]
    checksum = code["replay"]["sha256"]
    content, mime, filename = motor.studio_code_artifact(
        completed.app,
        completed.sid,
        "replay",
        checksum,
    )
    assert mime == "text/plain" and filename == "replay_shared_motor_umap.py"
    assert sha256(content).hexdigest() == checksum
    assert b"Generated" in content
    assert str(completed.source.parent).encode() not in content
    command_args = shlex.split(code["command"])
    for stage, path in zip(("e09", "e10", "e11"), completed.paths, strict=True):
        assert command_args[command_args.index("--" + stage) + 1] == "$PWD/" + path.name
    with pytest.raises(KeyError):
        motor.studio_code_artifact(completed.app, "f" * 32, "replay", checksum)
    with pytest.raises(KeyError):
        motor.studio_code_artifact(completed.app, completed.sid, "replay", "0" * 64)
    with pytest.raises(PermissionError):
        motor.studio_code_artifact(
            SimpleNamespace(offline_only=False), completed.sid, "replay", checksum
        )

    runtimes, invocations = [], []
    monkeypatch.setattr(motor, "_require_runtime", runtimes.append)
    stub = SimpleNamespace(
        run_shared_motor_umap=lambda *args, **kw: invocations.append((args, kw))
    )
    monkeypatch.setitem(sys.modules, "devbench.analysis.shared_motor_umap", stub)
    module = {"__name__": "_motor_replay_test"}
    exec(compile(content, filename, "exec"), module)
    for name in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMBA_NUM_THREADS",
        "NUMBA_THREADING_LAYER",
        "MPLCONFIGDIR",
    ):
        monkeypatch.setenv(name, "")
    output = completed.source.parent / "replay"
    arguments = [
        "--e09",
        str(completed.paths[0]),
        "--e10",
        str(completed.paths[1]),
        "--e11",
        str(completed.paths[2]),
        "--output",
        str(output),
    ]
    module["main"](arguments)
    assert runtimes == [completed.runtime]
    assert invocations == [
        ((tuple(Path(p) for p in completed.paths), output), completed.parameters)
    ]

    completed.paths[1].write_bytes(b"x")
    with pytest.raises(SystemExit) as error:
        module["main"](arguments)
    assert error.value.code == 1
    assert "e10: input SHA-256" in capsys.readouterr().err
    assert len(invocations) == 1
