"""Offline transport/DNS fixtures for bounded, opt-in scientific source retrieval."""

import hashlib
from importlib import import_module
import socket
import threading
import time
from pathlib import Path

import pytest


def _api():
    return import_module("devbench.analysis.method_sources")


class _Response:
    def __init__(self, body=b"Method documentation", *, status=200, headers=None, before_read=None):
        self.body, self.status = body, status
        self.headers = {key.lower(): value for key, value in
                        ({"Content-Type": "text/plain"} if headers is None else headers).items()}
        self.before_read = before_read
        self.offset = 0
        self.read_calls = 0
        self.closed = False

    def getheader(self, name, default=None):
        return self.headers.get(name.lower(), default)

    def read(self, amount):
        self.read_calls += 1
        if self.before_read is not None:
            callback, self.before_read = self.before_read, None
            callback()
        chunk = self.body[self.offset:self.offset + amount]
        self.offset += len(chunk)
        return chunk

    def close(self):
        self.closed = True


def _addresses(*ips):
    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
             "", (ip, 443, 0, 0) if ":" in ip else (ip, 443)) for ip in ips]


def _offline(monkeypatch, responses, *, addresses=None):
    module = _api()
    pending = list(responses)
    calls = {"dns": [], "connections": []}

    def resolve(host, port, **kwargs):
        calls["dns"].append((host, port, kwargs))
        return addresses(host) if callable(addresses) else addresses or _addresses("93.184.216.34")

    class Connection:
        def __init__(self, host, family, address, deadline):
            self.host, self.family, self.address = host, family, address
            self.deadline = deadline
            self.sock = None
            self.requests = []
            self.closed = False
            self.aborted = False
            self.response = pending.pop(0)
            calls["connections"].append(self)

        def request(self, method, path, *, headers):
            self.requests.append((method, path, headers))

        def getresponse(self):
            return self.response

        def abort(self):
            self.aborted = True
            self.close()

        def close(self):
            self.closed = True

    monkeypatch.setattr(module.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(module, "_PinnedHTTPSConnection", Connection)
    return calls


def test_network_is_denied_before_dns_or_output_path_work(monkeypatch, tmp_path):
    module = _api()

    def forbidden(*args, **kwargs):
        pytest.fail("Default-denied retrieval attempted DNS or path work")

    monkeypatch.setattr(module.socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(module, "_open_parent", forbidden)
    output = tmp_path / "uncreated" / "source.txt"
    with pytest.raises(PermissionError):
        module.retrieve_source("https://example.org/method", output, allowed_hosts=["example.org"])
    assert not output.parent.exists()


def test_success_writes_once_and_records_unvalidated_evidence(monkeypatch, tmp_path):
    body = b"A primary method describes assumptions and limitations."
    response = _Response(body, headers={"Content-Type": "text/plain; charset=utf-8",
                                        "Content-Length": str(len(body)), "Set-Cookie": "untrusted=value"})
    calls = _offline(monkeypatch, [response])
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:9999")
    monkeypatch.setenv("METHOD_SOURCE_TOKEN", "must-not-be-sent")
    output = tmp_path / "source.txt"
    result = _api().retrieve_source("https://example.org/method?q=1", output,
                                    allowed_hosts=["example.org"], allow_network=True)
    assert output.read_bytes() == body
    assert result["path"] == str(output)
    assert result["requested_url"] == result["final_url"] == "https://example.org/method?q=1"
    assert result["content_sha256"] == hashlib.sha256(body).hexdigest()
    assert result["content_type"] == "text/plain"
    assert result["excerpt"] == body.decode()
    assert result["retrieved_at"].endswith("+00:00")
    assert result["evidence_scope"] == "retrieved_source_not_independently_validated"
    connection = calls["connections"][0]
    assert connection.address == ("93.184.216.34", 443)
    method, path, headers = connection.requests[0]
    assert (method, path) == ("GET", "/method?q=1")
    assert not {"cookie", "authorization", "proxy-authorization"} & {key.lower() for key in headers}
    assert connection.closed and response.closed


@pytest.mark.parametrize("url,hosts", [
    ("http://example.org/method", ["example.org"]),
    ("https://name:secret@example.org/method", ["example.org"]),
    ("https://example.org:8443/method", ["example.org"]),
    ("https://example.org/method#section", ["example.org"]),
    ("https://example.org/method#", ["example.org"]),
    ("https://example.org/\r\nInjected:header", ["example.org"]),
    ("https://example.org/method", []),
    ("https://other.org/method", ["example.org"]),
    ("https://example.org/method", ["*.org"]),
    ("https://example.org/method", ["https://example.org"]),
])
def test_invalid_or_unallowlisted_urls_are_denied_before_dns(monkeypatch, tmp_path, url, hosts):
    calls = _offline(monkeypatch, [])
    with pytest.raises((ValueError, PermissionError)):
        _api().retrieve_source(url, tmp_path / "source.txt", allowed_hosts=hosts, allow_network=True)
    assert calls["dns"] == []
    assert calls["connections"] == []


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "192.168.1.1",
                               "100.64.0.1", "0.0.0.0", "224.0.0.1", "240.0.0.1",
                               "::1", "fe80::1", "fc00::1", "ff02::1", "::ffff:127.0.0.1"])
def test_all_nonpublic_dns_addresses_are_rejected(monkeypatch, tmp_path, ip):
    calls = _offline(monkeypatch, [], addresses=_addresses(ip))
    with pytest.raises((ValueError, PermissionError)):
        _api().retrieve_source("https://example.org/source", tmp_path / "source.txt",
                               allowed_hosts=["example.org"], allow_network=True)
    assert calls["connections"] == []


def test_mixed_public_private_answers_are_rejected(monkeypatch, tmp_path):
    calls = _offline(monkeypatch, [], addresses=_addresses("93.184.216.34", "127.0.0.1"))
    with pytest.raises((ValueError, PermissionError)):
        _api().retrieve_source("https://example.org/source", tmp_path / "source.txt",
                               allowed_hosts=["example.org"], allow_network=True)
    assert calls["connections"] == []


def test_loopback_ip_literal_is_rejected_without_dns(monkeypatch, tmp_path):
    calls = _offline(monkeypatch, [])
    with pytest.raises((ValueError, PermissionError)):
        _api().retrieve_source("https://127.0.0.1/source", tmp_path / "source.txt",
                               allowed_hosts=["127.0.0.1"], allow_network=True)
    assert calls["dns"] == []
    assert calls["connections"] == []


def test_public_ipv6_address_is_pinned(monkeypatch, tmp_path):
    calls = _offline(monkeypatch, [_Response()], addresses=_addresses("2001:4860:4860::8888"))
    _api().retrieve_source("https://example.org/source", tmp_path / "source.txt",
                           allowed_hosts=["example.org"], allow_network=True)
    assert calls["connections"][0].address == ("2001:4860:4860::8888", 443, 0, 0)


def test_redirects_are_revalidated_and_do_not_forward_cookies(monkeypatch, tmp_path):
    responses = [_Response(status=302, headers={"Location": "/paper", "Set-Cookie": "session=secret"}),
                 _Response(b"Paper", headers={"Content-Type": "text/html"})]
    calls = _offline(monkeypatch, responses)
    result = _api().retrieve_source("https://example.org/start", tmp_path / "source.html",
                                    allowed_hosts=["example.org"], allow_network=True)
    assert result["final_url"] == "https://example.org/paper"
    assert len(calls["dns"]) == 2
    assert all("Cookie" not in conn.requests[0][2] for conn in calls["connections"])
    assert responses[0].read_calls == 0


@pytest.mark.parametrize("location", ["https://unapproved.org/paper", "http://example.org/paper",
                                      "https://user:secret@example.org/paper", "/paper#fragment",
                                      "/paper#", "https://example.org/paper\r\n"])
def test_unsafe_redirect_target_is_rejected(monkeypatch, tmp_path, location):
    calls = _offline(monkeypatch, [_Response(status=302, headers={"Location": location})])
    output = tmp_path / "source.txt"
    with pytest.raises((ValueError, PermissionError)):
        _api().retrieve_source("https://example.org/start", output,
                               allowed_hosts=["example.org"], allow_network=True)
    assert len(calls["connections"]) == 1
    assert not output.exists()


def test_redirect_rejects_allowlisted_host_resolving_to_private_ip(monkeypatch, tmp_path):
    calls = _offline(monkeypatch, [_Response(status=302, headers={"Location": "https://second.org/paper"})],
                     addresses=lambda host: _addresses("127.0.0.1" if host == "second.org" else "93.184.216.34"))
    with pytest.raises((ValueError, PermissionError)):
        _api().retrieve_source("https://example.org/start", tmp_path / "source.txt",
                               allowed_hosts=["example.org", "second.org"], allow_network=True)
    assert len(calls["connections"]) == 1


def test_redirect_budget_is_three(monkeypatch, tmp_path):
    calls = _offline(monkeypatch, [_Response(status=302, headers={"Location": f"/hop-{i}"}) for i in range(4)])
    with pytest.raises(ValueError, match="redirect"):
        _api().retrieve_source("https://example.org/start", tmp_path / "source.txt",
                               allowed_hosts=["example.org"], allow_network=True)
    assert len(calls["connections"]) == 4


@pytest.mark.parametrize("headers", [
    {"Content-Type": "application/pdf"},
    {"Content-Type": "application/octet-stream"},
    {},
    {"Content-Type": "text/plain", "Content-Encoding": "gzip"},
    {"Content-Type": "text/plain", "Content-Length": "-1"},
    {"Content-Type": "text/plain", "Content-Length": "3, 3"},
    {"Content-Type": "text/plain", "Content-Length": "100"},
])
def test_unsupported_or_oversized_responses_are_rejected_before_body(monkeypatch, tmp_path, headers):
    response = _Response(b"abc", headers=headers)
    _offline(monkeypatch, [response])
    output = tmp_path / "source.txt"
    with pytest.raises(ValueError):
        _api().retrieve_source("https://example.org/source", output, allowed_hosts=["example.org"],
                               allow_network=True, max_bytes=10)
    assert response.read_calls == 0
    assert not output.exists()


def test_streaming_limit_catches_missing_content_length(monkeypatch, tmp_path):
    response = _Response(b"x" * 100)
    _offline(monkeypatch, [response])
    output = tmp_path / "source.txt"
    with pytest.raises(ValueError, match="bound|limit|large"):
        _api().retrieve_source("https://example.org/source", output, allowed_hosts=["example.org"],
                               allow_network=True, max_bytes=10)
    assert response.offset == 11
    assert not output.exists()


def test_truncated_response_is_not_written(monkeypatch, tmp_path):
    _offline(monkeypatch, [_Response(b"short", headers={"Content-Type": "text/plain", "Content-Length": "10"})])
    output = tmp_path / "source.txt"
    with pytest.raises(ValueError, match="length|truncated"):
        _api().retrieve_source("https://example.org/source", output,
                               allowed_hosts=["example.org"], allow_network=True)
    assert not output.exists()


def test_exact_byte_limit_and_excerpt_bound(monkeypatch, tmp_path):
    body = ("x" * 40_000).encode()
    _offline(monkeypatch, [_Response(body, headers={"Content-Type": "application/json", "Content-Length": str(len(body))})])
    result = _api().retrieve_source("https://example.org/source", tmp_path / "source.json",
                                    allowed_hosts=["example.org"], allow_network=True, max_bytes=len(body))
    assert len(result["excerpt"]) == 32_000
    assert result["content_sha256"] == hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize("max_bytes", [0, -1, True, 10_000_001])
def test_invalid_byte_limits_fail_before_dns(monkeypatch, tmp_path, max_bytes):
    calls = _offline(monkeypatch, [])
    with pytest.raises(ValueError):
        _api().retrieve_source("https://example.org/source", tmp_path / "source.txt",
                               allowed_hosts=["example.org"], allow_network=True, max_bytes=max_bytes)
    assert calls["dns"] == []


def test_existing_output_is_never_overwritten(monkeypatch, tmp_path):
    calls = _offline(monkeypatch, [])
    output = tmp_path / "source.txt"
    output.write_text("Existing evidence")
    with pytest.raises(FileExistsError):
        _api().retrieve_source("https://example.org/source", output,
                               allowed_hosts=["example.org"], allow_network=True)
    assert output.read_text() == "Existing evidence"
    assert calls["dns"] == []


def test_output_race_preserves_concurrently_created_file(monkeypatch, tmp_path):
    output = tmp_path / "source.txt"
    _offline(monkeypatch, [_Response(before_read=lambda: output.write_text("Concurrent evidence"))])
    with pytest.raises(FileExistsError):
        _api().retrieve_source("https://example.org/source", output,
                               allowed_hosts=["example.org"], allow_network=True)
    assert output.read_text() == "Concurrent evidence"


def test_symlink_leaf_and_parent_are_rejected(monkeypatch, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    target = real / "existing.txt"
    target.write_text("Original")
    leaf = tmp_path / "leaf.txt"
    leaf.symlink_to(target)
    parent = tmp_path / "linked"
    parent.symlink_to(real, target_is_directory=True)
    calls = _offline(monkeypatch, [])
    for output in (leaf, parent / "new.txt"):
        with pytest.raises((ValueError, FileExistsError, OSError)):
            _api().retrieve_source("https://example.org/source", output,
                                   allowed_hosts=["example.org"], allow_network=True)
    assert target.read_text() == "Original"
    assert not (real / "new.txt").exists()
    assert calls["dns"] == []


def test_relative_and_missing_output_parents_are_rejected(monkeypatch, tmp_path):
    calls = _offline(monkeypatch, [])
    for output in (Path("relative.txt"), tmp_path / "absent" / "source.txt"):
        with pytest.raises((ValueError, OSError)):
            _api().retrieve_source("https://example.org/source", output,
                                   allowed_hosts=["example.org"], allow_network=True)
    assert calls["dns"] == []
    assert not (tmp_path / "absent").exists()


def test_parent_replaced_by_symlink_during_fetch_is_rejected(monkeypatch, tmp_path):
    parent, moved, other = tmp_path / "output", tmp_path / "moved", tmp_path / "other"
    parent.mkdir()
    other.mkdir()

    def replace_parent():
        parent.rename(moved)
        parent.symlink_to(other, target_is_directory=True)

    _offline(monkeypatch, [_Response(before_read=replace_parent)])
    with pytest.raises((ValueError, OSError)):
        _api().retrieve_source("https://example.org/source", parent / "source.txt",
                               allowed_hosts=["example.org"], allow_network=True)
    assert not (other / "source.txt").exists()
    assert not (moved / "source.txt").exists()


def test_actual_connection_uses_pinned_address_with_hostname_tls(monkeypatch):
    module = _api()
    events = {}

    class RawSocket:
        def settimeout(self, timeout):
            events["timeout"] = timeout

        def connect(self, address):
            events["address"] = address

        def close(self):
            events["raw_closed"] = True

    class TLSSocket(RawSocket):
        def do_handshake(self):
            events["handshake"] = True

    class TLSContext:
        def __init__(self, **kwargs):
            events["context_options"] = kwargs

        def wrap_socket(self, sock, *, server_hostname, do_handshake_on_connect):
            events["hostname"] = server_hostname
            events["automatic_handshake"] = do_handshake_on_connect
            return TLSSocket()

    def no_dns(*args, **kwargs):
        pytest.fail("Pinned connection attempted to re-resolve hostname")

    monkeypatch.setattr(module.socket, "getaddrinfo", no_dns)
    monkeypatch.setattr(module.socket, "socket", lambda *args: RawSocket())
    monkeypatch.setattr(module.ssl, "create_default_context", TLSContext)
    connection = module._PinnedHTTPSConnection("example.org", socket.AF_INET,
                                               ("93.184.216.34", 443), time.monotonic() + 10)
    connection.connect()
    connection.close()
    assert events["address"] == ("93.184.216.34", 443)
    assert events["hostname"] == "example.org"
    assert events["automatic_handshake"] is False
    assert events["handshake"] is True
    assert 0 < events["timeout"] <= 10


def test_pinned_tls_uses_bundled_certifi_ca_without_relaxing_verification(monkeypatch):
    import certifi
    import ssl

    module = _api()
    create_context = ssl.create_default_context
    options = []

    def verified_context(**kwargs):
        options.append(kwargs)
        return create_context(**kwargs)

    monkeypatch.setattr(module.ssl, "create_default_context", verified_context)
    connection = module._PinnedHTTPSConnection("example.org", socket.AF_INET,
                                               ("93.184.216.34", 443), time.monotonic() + 10)
    assert options == [{"cafile": certifi.where()}]
    assert connection._context.verify_mode == ssl.CERT_REQUIRED
    assert connection._context.check_hostname is True


def test_dns_timeout_does_not_leave_output(monkeypatch, tmp_path):
    module = _api()
    release = threading.Event()

    def stalled_dns(*args, **kwargs):
        release.wait()
        return _addresses("93.184.216.34")

    monkeypatch.setattr(module.socket, "getaddrinfo", stalled_dns)
    monkeypatch.setattr(module, "SOURCE_TIMEOUT_SECONDS", 0.03)
    started = time.monotonic()
    output = tmp_path / "source.txt"
    try:
        with pytest.raises(TimeoutError):
            module.retrieve_source("https://example.org/source", output,
                                   allowed_hosts=["example.org"], allow_network=True)
    finally:
        release.set()
    assert time.monotonic() - started < 1
    assert not output.exists()


def test_expired_fetch_deadline_cannot_write_a_late_response(monkeypatch, tmp_path):
    module = _api()
    _offline(monkeypatch, [_Response(before_read=lambda: time.sleep(0.04))])
    monkeypatch.setattr(module, "SOURCE_TIMEOUT_SECONDS", 0.01)
    output = tmp_path / "source.txt"
    with pytest.raises(TimeoutError):
        module.retrieve_source("https://example.org/source", output,
                               allowed_hosts=["example.org"], allow_network=True)
    assert not output.exists()
