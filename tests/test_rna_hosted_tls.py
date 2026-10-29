"""Offline trust-store checks for the additive hosted /3 route."""

import base64
import csv
import hashlib
import importlib.metadata
import importlib.util
from pathlib import Path
import ssl
import sys
from types import SimpleNamespace

import certifi
import pytest


def _api():
    assert importlib.util.find_spec("devbench.study.rna_hosted_tls") is not None, (
        "Explicit verified TLS helper for hosted /3 is absent"
    )
    from devbench.study import rna_hosted_tls

    return rna_hosted_tls


@pytest.fixture
def tls():
    return _api()


def test_reviewed_tls_transport_is_available():
    assert callable(_api().verified_tls_context)
    assert callable(_api().tls_identity)


@pytest.fixture
def installed_bundle(tmp_path, monkeypatch, tls):
    """A real PEM bundle and real importlib RECORD parser, in a private prefix."""
    raw = Path(certifi.where()).read_bytes()
    prefix = tmp_path.resolve() / "runtime"
    package = prefix / "site-packages" / "certifi"
    package.mkdir(parents=True)
    ca = package / "cacert.pem"
    ca.write_bytes(raw)
    info = package.parent / "certifi-2026.7.22.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Name: certifi\nVersion: 2026.7.22\n")
    record = info / "RECORD"

    def set_record(*, digest=None, size=None, name="certifi/cacert.pem", duplicate=False):
        digest = digest if digest is not None else "sha256=" + base64.urlsafe_b64encode(
            hashlib.sha256(raw).digest()
        ).decode("ascii").rstrip("=")
        size = len(raw) if size is None else size
        with record.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow((name, digest, size))
            if duplicate:
                writer.writerow((name, digest, size))

    set_record()
    distribution = importlib.metadata.Distribution.at(info)
    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: distribution)
    monkeypatch.setattr(certifi, "where", lambda: str(ca))
    monkeypatch.setattr(sys, "prefix", str(prefix))
    return SimpleNamespace(
        ca=ca, prefix=prefix, raw=raw, record=record, set_record=set_record,
    )


def fingerprints(context):
    return {hashlib.sha256(certificate).hexdigest()
            for certificate in context.get_ca_certs(binary_form=True)}


def test_context_uses_only_installed_certifi_and_keeps_verification(tls, tmp_path, monkeypatch):
    expected = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    assert expected.cert_store_stats()["x509_ca"] == 0
    expected.load_verify_locations(cafile=certifi.where())
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "absent.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "absent-directory"))
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(tmp_path / "other.pem"))

    def forbidden_default(*args, **kwargs):
        pytest.fail("The explicit hosted TLS context consulted ambient default trust")

    monkeypatch.setattr(ssl, "create_default_context", forbidden_default)
    context = tls.verified_tls_context()
    assert context.protocol == ssl.PROTOCOL_TLS_CLIENT
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert fingerprints(context) == fingerprints(expected)
    assert fingerprints(context)


def test_identity_is_closed_and_contains_no_paths(tls):
    identity = tls.tls_identity()
    assert identity == {
        "cafile_sha256": hashlib.sha256(Path(certifi.where()).read_bytes()).hexdigest(),
        "certifi_version": importlib.metadata.version("certifi"),
        "verify_mode": "CERT_REQUIRED",
        "check_hostname": True,
    }


def test_record_verified_bundle_loads_actual_trust(tls, installed_bundle):
    expected = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    expected.load_verify_locations(cadata=installed_bundle.raw.decode("ascii"))
    assert fingerprints(tls.verified_tls_context()) == fingerprints(expected)


def test_tampered_ca_is_rejected_before_context_creation(tls, installed_bundle, monkeypatch):
    installed_bundle.ca.write_bytes(installed_bundle.raw.replace(b"CERTIFICATE", b"CERTIFICATX", 1))

    def forbidden_context(*args, **kwargs):
        pytest.fail("TLS context was constructed before CA integrity validation")

    monkeypatch.setattr(tls, "SSLContext", forbidden_context)
    with pytest.raises(ValueError):
        tls.verified_tls_context()
    with pytest.raises(ValueError):
        tls.tls_identity()


@pytest.mark.parametrize("digest", ["", "sha512=abc", "sha256=not-base64!", "sha256=YQ"])
def test_missing_unknown_or_malformed_record_hash_is_rejected(tls, installed_bundle, digest):
    installed_bundle.set_record(digest=digest)
    with pytest.raises(ValueError):
        tls.verified_tls_context()


@pytest.mark.parametrize("size", ["", 0, 1, 1048577])
def test_missing_or_incorrect_record_size_is_rejected(tls, installed_bundle, size):
    installed_bundle.set_record(size=size)
    with pytest.raises(ValueError):
        tls.verified_tls_context()


def test_duplicate_ca_record_is_rejected(tls, installed_bundle):
    installed_bundle.set_record(duplicate=True)
    with pytest.raises(ValueError):
        tls.verified_tls_context()


def test_unrecorded_ca_is_rejected(tls, installed_bundle):
    installed_bundle.set_record(name="certifi/other.pem")
    with pytest.raises(ValueError):
        tls.verified_tls_context()


def test_symlink_ca_is_rejected(tls, installed_bundle):
    original = installed_bundle.ca.with_suffix(".original")
    installed_bundle.ca.rename(original)
    installed_bundle.ca.symlink_to(original)
    with pytest.raises(ValueError):
        tls.verified_tls_context()


def test_symlink_parent_is_rejected(tls, installed_bundle):
    package = installed_bundle.ca.parent
    original = package.with_name("real-certifi")
    package.rename(original)
    package.symlink_to(original, target_is_directory=True)
    with pytest.raises(ValueError):
        tls.verified_tls_context()


def test_relative_ca_path_is_rejected(tls, installed_bundle, monkeypatch):
    monkeypatch.setattr(certifi, "where", lambda: "certifi/cacert.pem")
    with pytest.raises(ValueError):
        tls.verified_tls_context()


def test_noncanonical_ca_path_is_rejected(tls, installed_bundle, monkeypatch):
    monkeypatch.setattr(certifi, "where", lambda: str(installed_bundle.ca.parent) + "/../certifi/cacert.pem")
    with pytest.raises(ValueError):
        tls.verified_tls_context()


def test_ca_outside_current_runtime_is_rejected(tls, installed_bundle, monkeypatch):
    other = installed_bundle.prefix.parent / "other-runtime"
    other.mkdir()
    monkeypatch.setattr(sys, "prefix", str(other))
    with pytest.raises(ValueError):
        tls.verified_tls_context()


def test_nonregular_ca_is_rejected(tls, installed_bundle):
    installed_bundle.ca.unlink()
    installed_bundle.ca.mkdir()
    with pytest.raises(ValueError):
        tls.verified_tls_context()


def test_oversize_ca_is_rejected_before_context_creation(tls, installed_bundle, monkeypatch):
    with installed_bundle.ca.open("wb") as stream:
        stream.truncate(1048577)

    def forbidden_context(*args, **kwargs):
        pytest.fail("TLS context was constructed for an oversized trust store")

    monkeypatch.setattr(tls, "SSLContext", forbidden_context)
    with pytest.raises(ValueError):
        tls.verified_tls_context()


def test_context_loads_verified_bytes_despite_later_path_replacement(tls, installed_bundle, monkeypatch):
    expected = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    expected.load_verify_locations(cadata=installed_bundle.raw.decode("ascii"))
    real_context = ssl.SSLContext

    def replace_path_after_validation(protocol):
        installed_bundle.ca.write_bytes(b"not the verified trust store")
        return real_context(protocol)

    monkeypatch.setattr(tls, "SSLContext", replace_path_after_validation)
    context = tls.verified_tls_context()
    assert fingerprints(context) == fingerprints(expected)


# Additive opt-in HTTP framing tests. The TLS test prefix above is preserved.
def _framing_api():
    api = _api()
    assert isinstance(getattr(api, "StrictTrailerHTTPResponse", None), type), (
        "Opt-in strict trailer-aware HTTP response class is absent"
    )
    assert callable(getattr(api, "strict_chunked_response_complete", None)), (
        "Strict chunked framing completion predicate is absent"
    )
    return api


class _FramingWire:
    def __init__(self, raw):
        import io

        class BoundedBytes(io.BytesIO):
            def read(self, size=-1):
                assert size >= 0, "HTTP framing triggered an unbounded body read"
                return super().read(size)

            def read1(self, size=-1):
                assert size >= 0, "HTTP framing triggered an unbounded body read1"
                return super().read1(size)

            def readline(self, size=-1):
                assert size >= 0, "HTTP framing triggered an unbounded line read"
                return super().readline(size)

        self.stream = BoundedBytes(raw)

    def makefile(self, mode):
        assert mode == "rb"
        return self.stream


def _framing_response(api, body, *, headers=b"Transfer-Encoding: chunked\r\n"):
    wire = _FramingWire(b"HTTP/1.1 200 OK\r\n" + headers + b"\r\n" + body)
    response = api.StrictTrailerHTTPResponse(wire)
    response.begin()
    return response


def _bounded_framing_body(response):
    pieces = []
    while True:
        piece = response.read(3)
        if not piece:
            return b"".join(pieces)
        assert len(piece) <= 3
        pieces.append(piece)


def test_strict_chunked_response_api_is_available():
    import http.client

    api = _framing_api()
    assert issubclass(api.StrictTrailerHTTPResponse, http.client.HTTPResponse)


@pytest.mark.parametrize("body,expected", [
    (b"0\r\n\r\n", b""),
    (b"2\r\n{}\r\n0\r\n\r\n", b"{}"),
    (b'2;name="a;b"\r\n{}\r\n0;done=yes\r\nX-Check: yes\r\nX-Other: ok\r\n\r\n', b"{}"),
])
def test_strict_chunked_accepts_complete_bounded_wire(body, expected):
    api = _framing_api()
    response = _framing_response(api, body)
    assert response.strict_trailer_complete is False
    assert api.strict_chunked_response_complete(response) is False
    with pytest.raises(AttributeError):
        response.strict_trailer_complete = True
    assert _bounded_framing_body(response) == expected
    assert response.strict_trailer_complete is True
    assert api.strict_chunked_response_complete(response) is True


@pytest.mark.parametrize("body", [
    b"2\r\n{}\r\n",  # payload ends without the mandatory zero chunk
    b"2\r\n{}\r\n0\r\n",  # EOF is not the terminal blank trailer line
    b"2\r\n{}\r\n0\r\nX-Check: yes\r\n",  # trailer exists but no final blank
    b"5\r\nabc",  # partial data must not become a completed body
    b"2\r\n{}\r\n0",  # partial last-chunk metadata
])
def test_strict_chunked_rejects_truncated_wire(body):
    import http.client

    api = _framing_api()
    response = _framing_response(api, body)
    with pytest.raises(http.client.HTTPException):
        _bounded_framing_body(response)
    assert response.strict_trailer_complete is False
    assert api.strict_chunked_response_complete(response) is False


@pytest.mark.parametrize("body", [
    b"+2\r\n{}\r\n0\r\n\r\n",
    b"-2\r\n{}\r\n0\r\n\r\n",
    b"-0\r\n\r\n",
    b"0x2\r\n{}\r\n0\r\n\r\n",
    b" 2\r\n{}\r\n0\r\n\r\n",
    b"2 \r\n{}\r\n0\r\n\r\n",
    b"g\r\n{}\r\n0\r\n\r\n",
    b"2\r\n{}XX0\r\n\r\n",  # stdlib ordinarily discards these two bytes
    b"2\n{}\r\n0\r\n\r\n",
    b"2\r\n{}\r\n0\r\n\n",  # an LF-only line cannot prove CRLF framing
])
def test_strict_chunked_rejects_malformed_sizes_and_delimiters(body):
    import http.client

    api = _framing_api()
    response = _framing_response(api, body)
    with pytest.raises(http.client.HTTPException):
        _bounded_framing_body(response)
    assert response.strict_trailer_complete is False
    assert api.strict_chunked_response_complete(response) is False


@pytest.mark.parametrize("body", [
    b"2;" + b"x" * 100000 + b"\r\n{}\r\n0\r\n\r\n",
    b"1\r\na\r\n" * 5000 + b"0\r\n\r\n",
    (b"1;pad=" + b"x" * 1000 + b"\r\na\r\n") * 200 + b"0\r\n\r\n",
    b"0\r\nX-Private: " + b"x" * 100000 + b"\r\n\r\n",
    b"0\r\n" + b"X-Check: yes\r\n" * 100 + b"\r\n",
    b"0\r\n" + (b"X-Private: " + b"x" * 1000 + b"\r\n") * 60 + b"\r\n",
])
def test_strict_chunked_bounds_metadata_and_trailers(body):
    import http.client

    api = _framing_api()
    response = _framing_response(api, body)
    with pytest.raises(http.client.HTTPException) as captured:
        _bounded_framing_body(response)
    assert "X-Private" not in str(captured.value)
    assert "xxxxx" not in str(captured.value)
    assert response.strict_trailer_complete is False
    assert api.strict_chunked_response_complete(response) is False


@pytest.mark.parametrize("headers", [
    b"X-Private: " + b"x" * 100000 + b"\r\nTransfer-Encoding: chunked\r\n",
    b"X-Check: yes\r\n" * 150 + b"Transfer-Encoding: chunked\r\n",
    (b"X-Private: " + b"x" * 1000 + b"\r\n") * 80 + b"Transfer-Encoding: chunked\r\n",
])
def test_strict_response_bounds_header_lines_count_and_total(headers):
    import http.client

    api = _framing_api()
    with pytest.raises(http.client.HTTPException) as captured:
        _framing_response(api, b"0\r\n\r\n", headers=headers)
    assert "X-Private" not in str(captured.value)
    assert "xxxxx" not in str(captured.value)


def test_strict_chunked_predicate_does_not_accept_eof_or_caller_flags():
    import http.client

    api = _framing_api()
    response = _framing_response(api, b"2\r\n{}\r\n0\r\n\r\n")
    assert response.read(2) == b"{}"
    assert response.strict_trailer_complete is False
    assert api.strict_chunked_response_complete(response) is False
    response.close()
    assert response.isclosed()
    assert api.strict_chunked_response_complete(response) is False
    for headers in (b"Content-Length: 2\r\n", b""):
        other = _framing_response(api, b"{}", headers=headers)
        assert _bounded_framing_body(other) == b"{}"
        assert other.isclosed()
        assert api.strict_chunked_response_complete(other) is False
    ordinary = http.client.HTTPResponse(_FramingWire(
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n"
    ))
    ordinary.begin()
    assert ordinary.read(1) == b""
    ordinary.strict_trailer_complete = True
    assert api.strict_chunked_response_complete(ordinary) is False
    assert api.strict_chunked_response_complete(SimpleNamespace(
        strict_trailer_complete=True, chunked=True, chunk_left=None, isclosed=lambda: True,
    )) is False


def test_strict_response_is_per_connection_without_stdlib_monkeypatch():
    import http.client

    original = http.client.HTTPConnection.response_class
    api = _framing_api()
    connection = http.client.HTTPConnection("unused.invalid")
    connection.response_class = api.StrictTrailerHTTPResponse
    assert connection.response_class is api.StrictTrailerHTTPResponse
    assert http.client.HTTPConnection.response_class is original
    assert original is http.client.HTTPResponse


# Transfer-Encoding / Content-Length ambiguity must fail before body evidence.
@pytest.mark.parametrize("headers", [
    b"Transfer-Encoding: gzip\r\n",
    b"Transfer-Encoding: gzip, chunked\r\n",
    b"Transfer-Encoding: chunked, gzip\r\n",
    b"Transfer-Encoding: identity\r\n",
    b"Transfer-Encoding: x-private-sentinel\r\n",
    b"Transfer-Encoding: gzip\r\nContent-Length: 3\r\n",
    b"Transfer-Encoding: gzip, chunked\r\nContent-Length: 3\r\n",
    b"Transfer-Encoding: chunked\r\nContent-Length: 3\r\n",
    b"Content-Length: 3\r\nContent-Length: 6\r\n",
    b"Content-Length: 3\r\nContent-Length: 3\r\n",
    b"Content-Length: +3\r\n",
    b"Content-Length: -3\r\n",
])
def test_strict_response_rejects_unsupported_or_ambiguous_transfer_encoding(headers):
    import http.client

    api = _framing_api()
    with pytest.raises(http.client.HTTPException) as captured:
        _framing_response(api, b"3\r\nabc\r\n0\r\n\r\n", headers=headers)
    assert "x-private-sentinel" not in str(captured.value)


@pytest.mark.parametrize("headers,body,chunked", [
    (b"Content-Length: 3\r\n", b"abc", False),
    (b"Transfer-Encoding: chunked\r\n", b"3\r\nabc\r\n0\r\n\r\n", True),
    (b"Transfer-Encoding: ChUnKeD\r\n", b"3\r\nabc\r\n0\r\n\r\n", True),
])
def test_strict_response_keeps_unambiguous_length_and_chunked_controls(headers, body, chunked):
    api = _framing_api()
    response = _framing_response(api, body, headers=headers)
    assert _bounded_framing_body(response) == b"abc"
    assert response.isclosed()
    assert response.chunked is chunked
    assert api.strict_chunked_response_complete(response) is chunked
    if not chunked:
        assert response.length == 0


# A defective header block can hide later framing fields from HTTPMessage.
@pytest.mark.parametrize("hidden", [
    b"Transfer-Encoding: gzip\r\n",
    b"Content-Length: 6\r\n",
])
def test_strict_response_rejects_defective_headers_hiding_framing_fields(hidden):
    import http.client

    api = _framing_api()
    headers = b"Content-Length: 2\r\nBad\r\n" + hidden
    with pytest.raises(http.client.HTTPException) as captured:
        _framing_response(api, b"{}JUNK", headers=headers)
    assert "Bad" not in str(captured.value)
    assert "gzip" not in str(captured.value)


def test_strict_response_accepts_valid_headers_without_parser_defects():
    api = _framing_api()
    response = _framing_response(
        api, b"{}", headers=b"Content-Length: 2\r\nX-Check: yes\r\n",
    )
    assert response.headers.defects == []
    assert _bounded_framing_body(response) == b"{}"
    assert response.length == 0
    assert response.isclosed()
    assert api.strict_chunked_response_complete(response) is False

