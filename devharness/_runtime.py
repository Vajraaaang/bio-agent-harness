"""Workspace resolution and explicitly unauthenticated SDK call receipts."""
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
import fcntl
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import stat
import time

from devharness_server.dispatcher import ToolDispatcher, capture_tool_events

TRACE_NAME = ".devharness_trace.jsonl"
TRACE_LIMIT_BYTES = 2 * 1024 * 1024
TRACE_ENTRY_BYTES = 16 * 1024
METADATA_LIMIT_BYTES = 20 * 1024 * 1024


def workspace_root() -> Path:
    """Resolve the assigned workspace at call time, defaulting to the cwd."""
    root = Path(os.environ.get("DEVHARNESS_WORKSPACE_ROOT", Path.cwd())).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("DEVHARNESS_WORKSPACE_ROOT must be an existing directory")
    return root


def workspace_path(path, *, root=None, reject_symlinks=False) -> Path:
    """Resolve relative paths against the workspace, never the process's other cwd."""
    root = workspace_root() if root is None else root
    supplied = Path(path).expanduser()
    supplied = supplied if supplied.is_absolute() else root / supplied
    if reject_symlinks:
        for component in (supplied, *supplied.parents):
            if component.is_symlink():
                raise PermissionError("Symlinks are not allowed for SDK output files")
            if component == root:
                break
    resolved = supplied.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise PermissionError("Path is outside the assigned workspace or is the workspace directory")
    return resolved


def bounded_metadata_path(path) -> Path:
    source = workspace_path(path)
    if not source.is_file() or source.stat().st_size > METADATA_LIMIT_BYTES:
        raise ValueError("Metadata must be a regular file of at most 20 MB")
    return source


def _summary(value, depth=0):
    """Keep receipts compact; never serialize a resident DataFrame or matrix."""
    if isinstance(value, float) and not math.isfinite(value):
        return {"nonfinite_input": str(value)}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (str, Path)):
        text = str(value)
        return text if len(text) <= 512 else text[:512] + " [truncated]"
    if depth >= 3:
        return {"type": type(value).__name__, "summary_truncated": True}
    if isinstance(value, dict):
        items = list(value.items())[:20]
        result = {str(key): _summary(item, depth + 1) for key, item in items}
        if len(value) > len(items):
            result["summary_truncated"] = True
        return result
    if isinstance(value, (list, tuple)):
        result = [_summary(item, depth + 1) for item in value[:10]]
        return result + ([{"summary_truncated": True, "total_items": len(value)}] if len(value) > 10 else [])
    return {"type": type(value).__name__, **({"shape": list(value.shape)} if hasattr(value, "shape") else {})}


def _event_summary(event):
    result = {key: _summary(value) for key, value in event.items() if key != "result"}
    if "result" in event:
        encoded = json.dumps(event["result"], separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        result["result_sha256"] = hashlib.sha256(encoded).hexdigest()
        result["result_summary"] = _summary(event["result"])
    return result


@contextmanager
def _trace_file(root):
    path = workspace_path(TRACE_NAME, root=root, reject_symlinks=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise PermissionError("SDK trace must be a regular file without hard links")
        # Serialize SDK calls sharing a workspace, reserving space before work.
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if os.fstat(descriptor).st_size + TRACE_ENTRY_BYTES > TRACE_LIMIT_BYTES:
            raise RuntimeError("SDK trace size limit reached; use a fresh workspace")
        yield descriptor
    finally:
        os.close(descriptor)


def sdk_call(function):
    """Append one bounded receipt, including captured dispatcher failures."""
    signature = inspect.signature(function)

    @wraps(function)
    def wrapped(*args, **kwargs):
        root = workspace_root()
        with _trace_file(root) as descriptor, capture_tool_events() as events:
            started = time.monotonic()
            record = {"schema_version": 1, "source": "participant_workspace_trace",
                      "authenticated": False, "sdk_function": function.__name__,
                      "started_at_utc": datetime.now(timezone.utc).isoformat()}
            try:
                bound = signature.bind(*args, **kwargs)
                record["arguments_summary"] = _summary(dict(bound.arguments))
                result = function(*args, **kwargs)
                record.update(succeeded=True, result_summary=_summary(result))
                return result
            except BaseException as error:
                record.update(succeeded=False, error=f"{type(error).__name__}: {error}"[:1500])
                raise
            finally:
                record["elapsed_sec"] = time.monotonic() - started
                record["tool_events"] = [_event_summary(event) for event in events]
                encoded = json.dumps(record, ensure_ascii=False, allow_nan=False, default=str).encode()
                if len(encoded) >= TRACE_ENTRY_BYTES:
                    record.pop("arguments_summary", None)
                    record.pop("result_summary", None)
                    for event in record["tool_events"]:
                        event.pop("result_summary", None)
                    record["summary_truncated"] = True
                    encoded = json.dumps(record, ensure_ascii=False, allow_nan=False, default=str).encode()
                if len(encoded) >= TRACE_ENTRY_BYTES:
                    record["tool_events"] = [{"tool_name": event["tool_name"], "succeeded": event["succeeded"]}
                                             for event in record["tool_events"]]
                    encoded = json.dumps(record, ensure_ascii=False, allow_nan=False, default=str).encode()
                payload = encoded + b"\n"
                while payload:
                    written = os.write(descriptor, payload)
                    if written <= 0:
                        raise OSError("Unable to append SDK trace")
                    payload = payload[written:]
                os.fsync(descriptor)

    return wrapped


def execute(tool_name, **arguments):
    """Delegate the actual computation to the existing constrained dispatcher."""
    root = workspace_root()
    for name, value in arguments.items():
        if name.endswith("_path") and value is not None:
            resolved = bounded_metadata_path(value) if "metadata" in name else workspace_path(value, root=root)
            arguments[name] = str(resolved)
    return json.loads(ToolDispatcher(allowed_root=root).execute(tool_name, arguments))
