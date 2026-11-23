"""Explicit certifi trust for the additive hosted /3 route.

RECORD matching checks the installed bundle's integrity. Authenticity of the
runtime and its installation metadata remains a separate deployment assumption.
No ambient trust store, environment override, or unverified fallback is loaded.
"""

import base64
import hashlib
from importlib import metadata
import os
from pathlib import Path
import re
from ssl import CERT_REQUIRED, PROTOCOL_TLS_CLIENT, SSLContext
import stat
import sys

import certifi


_MAX_CA_BYTES = 1024 * 1024


def _canonical_path(value: str) -> Path:
    if not isinstance(value, str):
        raise ValueError("Certifi trust path must be an absolute canonical path")
    path = Path(value)
    if not path.is_absolute() or str(path) != value or path.resolve(strict=True) != path:
        raise ValueError("Certifi trust path must be canonical and contain no symlinks")
    return path


def _read_ca(path: Path, expected_size: int) -> bytes:
    # Walk from an anchored directory descriptor so a concurrent parent-path
    # replacement cannot turn a checked path into a followed symlink.
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(path.anchor, directory_flags)
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= _MAX_CA_BYTES:
                raise ValueError("Certifi CA file must be a bounded regular file")
            if info.st_size != expected_size:
                raise ValueError("Certifi CA file size does not match RECORD")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(_MAX_CA_BYTES + 1)
            if len(raw) != expected_size:
                raise ValueError("Certifi CA file changed during the bounded read")
            return raw
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def _verified_bundle() -> tuple[bytes, dict]:
    try:
        prefix = _canonical_path(sys.prefix)
        ca = _canonical_path(certifi.where())
        if ca == prefix or not ca.is_relative_to(prefix):
            raise ValueError("Certifi CA file must be inside the current runtime")
        distribution = metadata.distribution("certifi")
        entries = [entry for entry in distribution.files or ()
                   if str(entry) == "certifi/cacert.pem"]
        if len(entries) != 1:
            raise ValueError("Certifi CA file must have exactly one RECORD entry")
        entry = entries[0]
        recorded_path = _canonical_path(os.fspath(distribution.locate_file(entry)))
        if recorded_path != ca:
            raise ValueError("Certifi CA file does not match the installed RECORD path")
        recorded_hash = entry.hash
        if (recorded_hash is None or recorded_hash.mode != "sha256"
                or re.fullmatch(r"[A-Za-z0-9_-]{43}", recorded_hash.value) is None):
            raise ValueError("Certifi CA file requires a SHA-256 RECORD hash")
        if type(entry.size) is not int or not 0 < entry.size <= _MAX_CA_BYTES:
            raise ValueError("Certifi CA file requires a bounded RECORD size")
        version = distribution.version
        if (not isinstance(version, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,127}", version) is None):
            raise ValueError("Certifi version must be bounded public metadata")
        raw = _read_ca(ca, entry.size)
        digest = hashlib.sha256(raw).digest()
        encoded = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        if encoded != recorded_hash.value:
            raise ValueError("Certifi CA file does not match its SHA-256 RECORD hash")
        identity = {
            "cafile_sha256": digest.hex(),
            "certifi_version": version,
            "verify_mode": "CERT_REQUIRED",
            "check_hostname": True,
        }
        return raw, identity
    except (OSError, metadata.PackageNotFoundError):
        raise ValueError("Installed certifi trust could not be verified") from None


def verified_tls_context() -> SSLContext:
    """Return a hostname-verifying context using only verified installed roots."""
    raw, _ = _verified_bundle()
    context = SSLContext(PROTOCOL_TLS_CLIENT)
    context.verify_mode = CERT_REQUIRED
    context.check_hostname = True
    try:
        # Loading the verified bytes prevents a later file replacement from
        # changing the context's trust after the integrity check.
        context.load_verify_locations(cadata=raw.decode("ascii"))
    except (OSError, UnicodeError, ValueError):
        raise ValueError("Verified certifi CA bytes could not be loaded") from None
    return context


def tls_identity() -> dict:
    """Return the closed public trust identity, with no filesystem paths."""
    _, identity = _verified_bundle()
    return identity


# Additive, explicitly selected HTTP framing support. Existing TLS APIs above
# retain their original behavior; ordinary HTTP connections keep stdlib defaults.
import http.client as _http_client


class _StrictHeaderLines:
    """Apply one finite budget across status, interim and final header lines."""

    def __init__(self, stream):
        self.stream = stream
        self.lines = self.total = 0

    def readline(self, _size=-1):
        line = self.stream.readline(8193)
        self.lines += 1
        self.total += len(line)
        if (len(line) > 8192 or not line.endswith(b"\r\n")
                or self.lines > 128 or self.total > 65536):
            raise _http_client.HTTPException("Invalid or oversized HTTP response headers")
        return line

    def close(self):
        self.stream.close()


class StrictTrailerHTTPResponse(_http_client.HTTPResponse):
    """Opt-in bounded framing checks; body reads retain stdlib semantics.

    This proves completion only for chunked framing, not response semantics or
    close-delimited bodies. Bounds are 8 KiB per metadata line, 4096 chunk-size
    lines, 128 KiB total chunk metadata, and 64 trailer fields / 32 KiB trailers.
    Header budgets span all interim responses and the final response.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._strict_trailer_complete = False
        self._strict_zero_chunk_seen = False
        self._strict_metadata_bytes = self._strict_chunks = 0

    @property
    def strict_trailer_complete(self):
        return self._strict_trailer_complete

    def begin(self):
        if self.headers is not None:
            return
        original = self.fp
        guarded = _StrictHeaderLines(original)
        self.fp = guarded
        try:
            super().begin()
            if self.headers.defects:
                raise _http_client.HTTPException("Defective HTTP response headers")
            encodings = self.headers.get_all("Transfer-Encoding", [])
            lengths = self.headers.get_all("Content-Length", [])
            if encodings and (len(encodings) != 1 or encodings[0].lower() != "chunked" or lengths):
                raise _http_client.HTTPException("Unsupported or ambiguous HTTP response framing")
            if lengths and (len(lengths) != 1 or re.fullmatch(r"[0-9]+", lengths[0].strip(" \t")) is None):
                raise _http_client.HTTPException("Invalid or ambiguous HTTP response length")
        except _http_client.HTTPException:
            self._close_conn()
            raise _http_client.HTTPException("Invalid HTTP response headers") from None
        finally:
            if self.fp is guarded:
                self.fp = original

    def _strict_framing_failure(self):
        self._close_conn()
        raise _http_client.HTTPException("Invalid or oversized HTTP chunk framing") from None

    def _strict_account_metadata(self, size):
        self._strict_metadata_bytes += size
        if self._strict_metadata_bytes > 131072:
            self._strict_framing_failure()

    def _strict_metadata_line(self):
        line = self.fp.readline(8193)
        self._strict_account_metadata(len(line))
        if len(line) > 8192 or not line.endswith(b"\r\n"):
            self._strict_framing_failure()
        return line

    def _read_next_chunk_size(self):
        line = self._strict_metadata_line()
        self._strict_chunks += 1
        size = line[:-2].split(b";", 1)[0]
        if self._strict_chunks > 4096 or re.fullmatch(rb"[0-9A-Fa-f]+", size) is None:
            self._strict_framing_failure()
        return int(size, 16)

    def _get_chunk_left(self):
        chunk_left = self.chunk_left
        if chunk_left not in (None, 0):
            if type(chunk_left) is not int or chunk_left < 0:
                self._strict_framing_failure()
            return chunk_left
        if chunk_left == 0:
            self._strict_account_metadata(2)
            try:
                delimiter = self._safe_read(2)
            except _http_client.IncompleteRead:
                self._strict_framing_failure()
            if delimiter != b"\r\n":
                self._strict_framing_failure()
        chunk_left = self._read_next_chunk_size()
        if chunk_left == 0:
            self._strict_zero_chunk_seen = True
            self._read_and_discard_trailer()
            self._close_conn()
            chunk_left = None
        self.chunk_left = chunk_left
        return chunk_left

    def _read_and_discard_trailer(self):
        if not self._strict_zero_chunk_seen:
            self._strict_framing_failure()
        count = total = 0
        while True:
            line = self._strict_metadata_line()
            total += len(line)
            if total > 32768:
                self._strict_framing_failure()
            if line == b"\r\n":
                self._strict_trailer_complete = True
                return
            count += 1
            if count > 64:
                self._strict_framing_failure()


def strict_chunked_response_complete(response) -> bool:
    """Recognize actual completed strict chunked framing, without caller flags."""
    return (type(response) is StrictTrailerHTTPResponse
            and response.strict_trailer_complete is True
            and response.chunked is True and response.chunk_left is None
            and response.isclosed())

