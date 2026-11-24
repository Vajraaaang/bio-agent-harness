"""Verified implementation and generated replay views for completed motor UMAP runs."""

from __future__ import annotations

import importlib.util
import shlex
from hashlib import sha256
from pathlib import Path
from pprint import pformat

from devbench.core._files import read_bounded_file
from devbench.engine.workflow_operations import OperationBlocked

IMPLEMENTATION = "devbench.analysis.shared_motor_umap"
SOURCE_LIMIT = 256 * 1024
_STAGES = ("e09", "e10", "e11")
_SOURCE_NAME = "shared_motor_umap.py"
_REPLAY_NAME = "replay_shared_motor_umap.py"

_REPLAY = """\
import argparse
import os
from pathlib import Path
from tempfile import TemporaryDirectory


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for item in RECORD["inputs"]:
        parser.add_argument("--" + item["stage"], required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    for variable in (
        "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS", "NUMBA_NUM_THREADS",
    ):
        os.environ[variable] = "1"
    os.environ["NUMBA_THREADING_LAYER"] = "workqueue"

    from devbench.core._files import hash_bounded_file
    from devbench.platform.plugin_motor_umap import _require_runtime

    _require_runtime(RECORD["runtime"])
    sources = []
    for item in RECORD["inputs"]:
        path = getattr(args, item["stage"]).resolve(strict=True)
        if (path.stat().st_size != item["size_bytes"]
                or hash_bounded_file(path, item["size_bytes"]) != item["sha256"]):
            parser.exit(1, item["stage"] + ": input SHA-256 or size mismatch\\n")
        sources.append(path)

    with TemporaryDirectory(prefix="motor-umap-replay-") as temporary:
        os.environ["MPLCONFIGDIR"] = str(Path(temporary) / "matplotlib")
        from devbench.analysis.shared_motor_umap import run_shared_motor_umap

        run_shared_motor_umap(
            tuple(sources), args.output.resolve(), **RECORD["parameters"],
        )
"""


def implementation_path() -> Path:
    module = importlib.util.find_spec(IMPLEMENTATION)
    if module is None or module.origin is None:
        raise OperationBlocked("shared_motor_umap_runtime_unavailable")
    return Path(module.origin).resolve(strict=True)


def _runtime(plan: dict, receipt: dict) -> dict:
    return receipt.get("runtime") or plan["runtime"]


def _inputs(plan: dict) -> list[dict]:
    return [
        {
            "stage": stage,
            "name": Path(profile["source"]).name,
            "sha256": profile["sha256"],
            "size_bytes": profile["size_bytes"],
        }
        for stage, profile in zip(_STAGES, plan["profiles"], strict=True)
    ]


def _replay(plan: dict, receipt: dict) -> bytes:
    record = {
        "runtime": _runtime(plan, receipt),
        "parameters": plan["design"]["parameters"],
        "inputs": [
            {key: item[key] for key in ("stage", "sha256", "size_bytes")}
            for item in _inputs(plan)
        ],
    }
    head = '"""Generated replay from the completed DevHarness motor UMAP plan."""\n\n'
    tail = '\n\nif __name__ == "__main__":\n    main()\n'
    return (
        head + _REPLAY + "\n\nRECORD = " + pformat(record, width=78) + tail
    ).encode()


def read_code(plan: dict, receipt: dict, kind: str) -> tuple[bytes, str]:
    if kind == "replay":
        return _replay(plan, receipt), _REPLAY_NAME
    if kind != "source":
        raise KeyError("Unknown shared motor UMAP code artifact")
    try:
        content = read_bounded_file(implementation_path(), SOURCE_LIMIT)
    except (OSError, ValueError, OperationBlocked) as error:
        raise OperationBlocked("shared_motor_umap_code_unavailable") from error
    if sha256(content).hexdigest() != _runtime(plan, receipt)["implementation_sha256"]:
        raise OperationBlocked("shared_motor_umap_code_unavailable")
    return content, _SOURCE_NAME


def code_view(plan: dict, receipt: dict, base: str) -> dict:
    runtime = _runtime(plan, receipt)
    inputs = _inputs(plan)
    args = [f'--{item["stage"]} "$PWD"/{shlex.quote(item["name"])}' for item in inputs]
    view = {
        "entry_point": IMPLEMENTATION + ".run_shared_motor_umap",
        "runtime": {
            name: runtime[name]
            for name in ("python", "packages", "implementation_sha256")
        },
        "inputs": [
            {key: item[key] for key in ("stage", "name", "sha256")} for item in inputs
        ],
        "command": " ".join(
            [
                f'python -I -B "$PWD"/{_REPLAY_NAME}',
                *args,
                '--output "$PWD/replay-motor-umap"',
            ]
        ),
    }
    for kind in ("source", "replay"):
        try:
            content, _ = read_code(plan, receipt, kind)
        except OperationBlocked:
            continue
        identity = sha256(content).hexdigest()
        view[kind] = {"url": f"{base}/code/{kind}/{identity}", "sha256": identity}
    return view
