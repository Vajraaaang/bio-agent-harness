"""Opt-in, bounded public-source retrieval without installation or source trust.

Returned text is untrusted source material. Retrieval proves the bytes obtained,
not authorship, scientific validity or correspondence to an implementation.
Output paths must be absolute; directory-descriptor traversal is anchored at the
filesystem root so no symlink component or working-directory lookup is followed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import os
from pathlib import Path
import queue
import re
import socket
import ssl
import threading
import time
from urllib.parse import urljoin, urlsplit

import certifi


SOURCE_TIMEOUT_SECONDS = 10.0
_MAX_SOURCE_BYTES = 10_000_000
_REDIRECTS = {301, 302, 303, 307, 308}
_CONTENT_TYPES = {"text/plain", "text/html", "application/json"}


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Source retrieval deadline exceeded")
    return remaining


def _host(value):
    if type(value) is not str or not value or len(value) > 253 or "%" in value:
        raise ValueError("Allowed hosts must be explicit DNS names or IP addresses")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    try:
        name = value.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise ValueError("Invalid source hostname") from error
    if not name or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                       for label in name.split(".")):
        raise ValueError("Allowed hosts must be explicit DNS names without wildcards, schemes or ports")
    return name


def _check_url_text(url):
    if (type(url) is not str or not url or len(url) > 8192 or "#" in url
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)):
        raise ValueError("Source URLs must be bounded HTTPS URLs without fragments or control characters")


def _check_url(url, allowed_hosts):
    _check_url_text(url)
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "https" and parsed.hostname is not None
                 and parsed.username is None and parsed.password is None and parsed.port in (None, 443))
        host = _host(parsed.hostname) if valid else None
    except ValueError as error:
        raise ValueError("Invalid HTTPS source URL") from error
    if not valid:
        raise ValueError("Source URLs require HTTPS, no credentials and port 443")
    if host not in allowed_hosts:
        raise PermissionError("Source host is not explicitly allowlisted")
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    # http.client transmits request targets as ASCII; callers may percent-encode
    # Unicode paths without changing the host allowlist or connection identity.
    try:
        target.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("Source URL paths must use percent encoding for non-ASCII characters") from error
    return host, target


def _public_address(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise PermissionError("Source DNS did not return a valid public IP address") from error
    if (not address.is_global or address.is_private or address.is_loopback or address.is_link_local
            or address.is_reserved or address.is_multicast or address.is_unspecified):
        raise PermissionError("Source addresses must be public, globally routable unicast addresses")
    if isinstance(address, ipaddress.IPv6Address):
        embedded = [address.ipv4_mapped, address.sixtofour]
        if address.teredo:
            embedded.extend(address.teredo)
        for inner in embedded:
            if inner is not None:
                _public_address(str(inner))
    return address


def _resolve(host, deadline):
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        address = _public_address(str(literal))
        return ((socket.AF_INET, (str(address), 443)) if address.version == 4
                else (socket.AF_INET6, (str(address), 443, 0, 0)))
    results = queue.Queue(maxsize=1)

    def resolve():
        try:
            answers = socket.getaddrinfo(host, 443, family=socket.AF_UNSPEC,
                                         type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
            results.put((answers, None))
        except Exception as error:
            results.put((None, error))

    # libc DNS calls have no per-call timeout. A daemon worker bounds the caller's
    # wait; a late DNS result cannot create a connection or modify any artifact.
    threading.Thread(target=resolve, daemon=True).start()
    try:
        answers, error = results.get(timeout=_remaining(deadline))
    except queue.Empty as error:
        raise TimeoutError("Source DNS deadline exceeded") from error
    _remaining(deadline)
    if error is not None:
        raise OSError("Source DNS lookup failed") from error
    if not answers or len(answers) > 32:
        raise PermissionError("Source DNS must return a bounded nonempty address set")
    checked = []
    for family, kind, protocol, _, sockaddr in answers:
        if family not in (socket.AF_INET, socket.AF_INET6) or kind != socket.SOCK_STREAM:
            raise PermissionError("Source DNS returned an unsupported address family")
        if protocol not in (0, socket.IPPROTO_TCP) or sockaddr[1] != 443:
            raise PermissionError("Source DNS returned an unsupported transport")
        if family == socket.AF_INET6 and ("%" in sockaddr[0] or sockaddr[3] != 0):
            raise PermissionError("Scoped source addresses are not permitted")
        address = _public_address(sockaddr[0])
        if address.version != (4 if family == socket.AF_INET else 6):
            raise PermissionError("Source DNS address and family disagree")
        checked.append((family, (str(address), 443) if address.version == 4 else (str(address), 443, 0, 0)))
    # Reject the entire answer set on an unsafe answer, then pin one checked IP.
    return checked[0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, family, address, deadline):
        super().__init__(host, 443, timeout=_remaining(deadline),
                         context=ssl.create_default_context(cafile=certifi.where()))
        self._family, self._address, self._deadline = family, address, deadline
        self._connecting_socket = None
        self._aborted = False

    def connect(self):
        raw = socket.socket(self._family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        self._connecting_socket = raw
        try:
            raw.settimeout(_remaining(self._deadline))
            raw.connect(self._address)
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host, do_handshake_on_connect=False)
            self._connecting_socket = None
            if self._aborted:
                raise TimeoutError("Source retrieval deadline exceeded")
            self.sock.settimeout(_remaining(self._deadline))
            self.sock.do_handshake()
            _remaining(self._deadline)
        except BaseException:
            raw.close()
            self.close()
            raise
        finally:
            self._connecting_socket = None

    def abort(self):
        self._aborted = True
        for transport in (self.sock, self._connecting_socket):
            if transport is not None:
                try:
                    transport.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                transport.close()


def _fetch_hop(host, target, deadline, max_bytes):
    family, address = _resolve(host, deadline)
    connection = _PinnedHTTPSConnection(host, family, address, deadline)
    timer = threading.Timer(_remaining(deadline), connection.abort)
    timer.daemon = True
    timer.start()
    response = None
    try:
        connection.request("GET", target, headers={
            "Accept": "text/plain, text/html, application/json", "Accept-Encoding": "identity",
            "User-Agent": "DevHarness-Source-Retrieval/1", "Connection": "close",
        })
        response = connection.getresponse()
        _remaining(deadline)
        if response.status in _REDIRECTS:
            location = response.getheader("Location")
            if not location:
                raise ValueError("Source redirect has no target")
            return location, None, None
        if response.status != 200:
            raise ValueError(f"Source retrieval returned HTTP {response.status}")
        content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type not in _CONTENT_TYPES:
            raise ValueError("Source response has an unsupported content type")
        if response.getheader("Content-Encoding", "identity").strip().lower() != "identity":
            raise ValueError("Compressed source responses are not permitted")
        length = response.getheader("Content-Length")
        if length is not None:
            if not re.fullmatch(r"[0-9]{1,10}", length) or int(length) > max_bytes:
                raise ValueError("Source Content-Length is invalid or exceeds the byte limit")
            length = int(length)
        transfer = response.getheader("Transfer-Encoding")
        if transfer is not None and (transfer.strip().lower() != "chunked" or length is not None):
            raise ValueError("Ambiguous source response framing is not permitted")
        chunks, total = [], 0
        while True:
            _remaining(deadline)
            if connection.sock is not None:
                connection.sock.settimeout(_remaining(deadline))
            chunk = response.read(min(65_536, max_bytes - total + 1))
            _remaining(deadline)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("Source body exceeds the byte limit")
            chunks.append(chunk)
        if length is not None and total != length:
            raise ValueError("Source body length is truncated or inconsistent")
        return None, b"".join(chunks), content_type
    except (OSError, http.client.HTTPException):
        # Deadline-driven shutdown can surface as EOF, reset or SSL failure.
        _remaining(deadline)
        raise
    finally:
        timer.cancel()
        if response is not None:
            response.close()
        connection.close()


def _open_parent(output):
    path = Path(output)
    if not path.is_absolute() or ".." in path.parts or not path.name:
        raise ValueError("Source output must be an absolute file path without parent traversal")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("This platform lacks required symlink-safe output primitives")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open("/", flags)
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    except BaseException:
        os.close(descriptor)
        raise
    return path, descriptor


def _absent(parent, name):
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise FileExistsError("Source output already exists; overwriting is forbidden")


def _write_once(parent, name, body):
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    identity = os.fstat(descriptor)
    try:
        view, offset = memoryview(body), 0
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                raise OSError("Source output write did not make progress")
            offset += written
        os.fsync(descriptor)
    except BaseException:
        try:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                os.unlink(name, dir_fd=parent)
        except OSError:
            pass
        raise
    finally:
        os.close(descriptor)


def retrieve_source(url, output: Path, *, allowed_hosts: list[str], allow_network=False, max_bytes=1_000_000) -> dict:
    """Retrieve explicitly allowed public text into one new absolute artifact.

    No trust or execution approval follows from retrieval. Existing parent
    directories are required; packages, redirects outside the allowlist,
    compressed/binary content, credentials and cookies are never requested.
    """
    if allow_network is not True:
        raise PermissionError("Source retrieval requires explicit network permission")
    if type(max_bytes) is not int or not 1 <= max_bytes <= _MAX_SOURCE_BYTES:
        raise ValueError("Source byte limit must be an integer between 1 and 10000000")
    if type(allowed_hosts) is not list or not 1 <= len(allowed_hosts) <= 32:
        raise ValueError("Source retrieval requires an explicit bounded host allowlist")
    hosts = {_host(host) for host in allowed_hosts}
    host, target = _check_url(url, hosts)
    path, parent = _open_parent(output)
    try:
        _absent(parent, path.name)
        deadline = time.monotonic() + SOURCE_TIMEOUT_SECONDS
        current = url
        for redirects in range(4):
            location, body, content_type = _fetch_hop(host, target, deadline, max_bytes)
            if location is None:
                break
            if redirects == 3:
                raise ValueError("Source redirect limit exceeded")
            _check_url_text(location)
            current = urljoin(current, location)
            host, target = _check_url(current, hosts)
        _remaining(deadline)
        # Recheck the absolute directory path after network I/O; a renamed or
        # symlink-substituted parent must not redirect the resulting artifact.
        _, fresh_parent = _open_parent(path)
        try:
            before, after = os.fstat(parent), os.fstat(fresh_parent)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise ValueError("Source output directory changed during retrieval")
            _write_once(fresh_parent, path.name, body)
        finally:
            os.close(fresh_parent)
        return {"requested_url": url, "final_url": current,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "content_sha256": hashlib.sha256(body).hexdigest(), "path": str(path),
                "content_type": content_type, "excerpt": body[:128_000].decode("utf-8", errors="replace")[:32_000],
                "evidence_scope": "retrieved_source_not_independently_validated"}
    finally:
        os.close(parent)
