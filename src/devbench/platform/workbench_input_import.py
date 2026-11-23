"""Stream selected files to and from the opted-in local import folder."""

from contextlib import contextmanager
import errno
import os
import secrets
import unicodedata
from urllib.parse import parse_qs

from devbench.analysis.workbench_inputs import _input_suffix
from devbench.core.input_files import _directory, _open_file


def transferable_filename(name):
    return (
        not name.startswith(".") and name == name.strip() and len(name.encode()) <= 255
        and "/" not in name and "\\" not in name
        and not any(unicodedata.category(char).startswith("C") for char in name)
        and _input_suffix(name) is not None
    )


def _filename(query):
    fields = parse_qs(query, strict_parsing=True, max_num_fields=1, errors="strict")
    names = fields.get("name", [])
    if len(names) != 1 or not transferable_filename(names[0]):
        raise ValueError("Select a supported RNA-seq input filename")
    return names[0]


def import_input(app, query, headers, stream):
    root = app.input_import_root
    if root is None:
        raise PermissionError("Local file import is disabled")
    name = _filename(query)
    lengths = headers.get_all("Content-Length") or []
    if (
        headers.get("Transfer-Encoding") or len(lengths) != 1
        or not lengths[0].isascii() or not lengths[0].isdecimal()
        or headers.get_all("Content-Type") != ["application/octet-stream"]
    ):
        raise ValueError("Use a bounded binary file request")
    size = int(lengths[0])
    temporary = ".input-import-" + secrets.token_hex(16)
    directory = _directory(root)
    try:
        identity = os.fstat(directory)
        if (identity.st_dev, identity.st_ino) != app.inputs._roots[root]:
            raise ValueError("The configured input root changed")
        try:
            os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise RuntimeError("A file with this name already exists")
        disk = os.fstatvfs(directory)
        if size > disk.f_bavail * disk.f_frsize:
            raise OSError(errno.ENOSPC, "Insufficient free space for this file")
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=directory,
        )
        try:
            with os.fdopen(descriptor, "wb") as target:
                remaining = size
                while remaining:
                    chunk = stream.read(min(1 << 20, remaining))
                    if not chunk:
                        raise ValueError("The selected file did not arrive in full")
                    target.write(chunk)
                    remaining -= len(chunk)
                target.flush()
                os.fsync(target.fileno())
            try:
                os.link(
                    temporary, name, src_dir_fd=directory, dst_dir_fd=directory,
                    follow_symlinks=False,
                )
            except FileExistsError as error:
                raise RuntimeError("A file with this name already exists") from error
        finally:
            os.unlink(temporary, dir_fd=directory)
    finally:
        os.close(directory)
    return {"status": "imported"}


@contextmanager
def open_input_download(app, query):
    root = app.input_import_root
    if root is None or app.evidence_delivery is not None:
        raise PermissionError("Local file download is disabled")
    name = _filename(query)
    try:
        with _open_file(root, (name,), app.inputs._roots[root]) as descriptor:
            yield name, descriptor, os.fstat(descriptor).st_size
    except FileNotFoundError as error:
        raise KeyError("The local input is unavailable") from error
