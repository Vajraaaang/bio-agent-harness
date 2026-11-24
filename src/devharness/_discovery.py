"""Finite, schema-checked participant answer output; never private grading."""
import json
import os
from pathlib import Path
import stat
import uuid

from jsonschema import validate

from ._runtime import sdk_call, workspace_path, workspace_root
from .data import PathLike

ANSWER_LIMIT_BYTES = 2 * 1024 * 1024


def _local_references_only(value):
    if isinstance(value, dict):
        for name, item in value.items():
            if name in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                    not isinstance(item, str) or not item.startswith("#")):
                raise ValueError("Only local fragment schema references are allowed; external references are disabled")
            _local_references_only(item)
    elif isinstance(value, list):
        for item in value:
            _local_references_only(item)


def _validate_answer(answer, root):
    schema_path = workspace_path("answer_schema.json", root=root, reject_symlinks=True)
    if schema_path.exists():
        if not schema_path.is_file() or schema_path.stat().st_size > 1024 * 1024:
            raise ValueError("Local answer_schema.json must be a regular file of at most 1 MB")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        _local_references_only(schema)
        validate(answer, schema)


@sdk_call
def save_discovery(path: PathLike = "final_discovery.json", **answer) -> Path:
    """Atomically write finite JSON inside the workspace and return its Path.

    Validate against local ``answer_schema.json`` when present. This performs
    schema validation only; it does not import graders or claim scientific
    correctness. Nonfinite values are rejected, never converted to null or zero.
    Existing regular JSON answers may be replaced. Supplied schemas, task inputs,
    guidance, symlink destinations, and non-JSON paths cannot be overwritten.
    """
    root = workspace_root()
    output = workspace_path(path, root=root, reject_symlinks=True)
    if output.suffix.lower() != ".json" or output.name.casefold() in {
        "answer_schema.json", "task.json", "task_input.json", "task_inputs.json",
        "metadata.json", "guidance.json", "input.json", "inputs.json",
    }:
        raise PermissionError("save_discovery requires an answer JSON path, not a schema or supplied input")
    encoded = (json.dumps(answer, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > ANSWER_LIMIT_BYTES:
        raise ValueError("Discovery JSON exceeds the 2 MB SDK limit")
    # Validate exactly the JSON representation which will be written.
    _validate_answer(json.loads(encoded), root)
    # Open each parent relative to the workspace using no-follow directory FDs,
    # so a symlink substitution cannot redirect the atomic write outside it.
    parent_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary_name = ".discovery-" + uuid.uuid4().hex + ".tmp"
    created = False
    try:
        for component in output.parent.relative_to(root).parts:
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
        try:
            existing = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and (not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1):
            raise PermissionError("Answer must be a regular file without symlinks or hard links")
        if existing is not None and not existing.st_mode & 0o222:
            raise PermissionError("Read-only supplied inputs cannot be replaced by save_discovery")
        fd = os.open(temporary_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent_fd)
        created = True
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        created = False
        os.fsync(parent_fd)
    finally:
        if created:
            os.unlink(temporary_name, dir_fd=parent_fd)
        os.close(parent_fd)
    return output
