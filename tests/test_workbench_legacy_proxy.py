"""Archive proxy transport controls; no model or scientific execution."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading

import pytest

from devbench.analysis import workbench_legacy_proxy as legacy


@pytest.fixture
def archive():
    parent = Path(__file__).resolve().parents[1] / "reports" / ".legacy-proxy-tests"
    parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as directory:
        requests = []
        state = {"revision": 0, "project": {"id": "a" * 32, "name": "Retained project", "scope": "single_user_local"}}
        response = {"override": None}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def send(self, status, media, payload, filename=None, length=None, extra=()):
                self.send_response(status)
                self.send_header("Content-Type", media)
                self.send_header("Content-Length", str(len(payload) if length is None else length))
                if filename is not None:
                    self.send_header("Content-Disposition", filename)
                for name, value in extra:
                    self.send_header(name, value)
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def dispatch(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                requests.append({"method": self.command, "path": self.path,
                                 "headers": dict(self.headers), "body": body})
                if response["override"] is not None:
                    return self.send(**response["override"])
                if self.headers.get("X-Workbench-Token") != "private-child-token":
                    return self.send(403, "application/json", b'{"error":"session required"}')
                if self.path == "/api/workspace":
                    if self.command == "POST":
                        changes = json.loads(body)
                        state["revision"] += 1
                        if "name" in changes:
                            state["project"]["name"] = changes["name"]
                    return self.send(200, "application/json; charset=utf-8", json.dumps(state).encode())
                if self.path == "/api/config":
                    return self.send(200, "application/json", b'{"model_access":false,"execution_allowed":false}')
                if self.path == "/api/jobs/own/files":
                    return self.send(200, "application/octet-stream", b"retained artifact bytes",
                                     filename='attachment; filename="own-data.csv"')
                if self.path.startswith("/api/scientific/resolve?"):
                    return self.send(200, "application/json", b'{"reference":"exact"}')
                if self.path == "/api/inputs/inspect" and self.command == "POST":
                    return self.send(200, "application/json", b'{"header_only":true}')
                return self.send(404, "application/json", b'{"error":"unknown retained record"}')
            do_GET = dispatch
            do_POST = dispatch
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        proxy = legacy.LegacyWorkbenchProxy(Path(directory).resolve(), server.server_port, "private-child-token")
        try:
            yield proxy, requests, state, response
        finally:
            server.shutdown()
            server.server_close()
            worker.join(3)


def test_own_artifact_and_missing_foreign_artifact_are_relayed_without_changes(archive):
    proxy, requests, _, _ = archive
    status, media, filename, body = proxy.request("GET", "/api/jobs/own/files")
    assert (status, media, filename, body) == (200, "application/octet-stream", "own-data.csv", b"retained artifact bytes")
    assert proxy.request("GET", "/api/jobs/foreign/files")[0] == 404
    assert len(requests) == 2


@pytest.mark.parametrize("method,path,body", [
    ("GET", "http://example.com/api/config", None),
    ("GET", "//example.com/api/config", None),
    ("GET", "/api/../secret", None),
    ("GET", "/api/%2e%2e/secret", None),
    ("GET", "/api/config#fragment", None),
    ("GET", "/api/config\r\nHost:evil", None),
    ("GET", "/api/projects", None),
    ("POST", "/api/jobs/own/run", {"allow_data_execution": True}),
    ("POST", "/api/scientific", {"action": "run_trial"}),
    ("POST", "/api/investigations", {"question": "execute"}),
    ("POST", "/api/workspace?extra=1", {}),
    ("DELETE", "/api/workspace", None),
])
def test_unsafe_paths_and_mutations_are_rejected_before_any_network_call(archive, method, path, body):
    proxy, requests, _, _ = archive
    with pytest.raises((ValueError, PermissionError)):
        proxy.request(method, path, body)
    assert requests == []


def test_fixed_archive_host_origin_and_child_token_never_include_project_headers(archive):
    proxy, requests, _, _ = archive
    path = "/api/scientific/resolve?object_type=analysis&object_id=" + "a" * 32 + "&version_id=" + "b" * 64
    assert proxy.request("GET", path)[0] == 200
    headers = {key.lower(): value for key, value in requests[0]["headers"].items()}
    assert headers["host"] == "127.0.0.1:" + str(proxy._port)
    assert headers["origin"] == "http://" + headers["host"]
    assert headers["x-workbench-token"] == "private-child-token"
    assert "x-workbench-project" not in headers and "authorization" not in headers and "cookie" not in headers
    assert requests[0]["path"] == path


def test_redirects_are_never_followed(archive):
    proxy, requests, _, response = archive
    response["override"] = {"status": 302, "media": "application/json", "payload": b"{}",
                            "extra": [("Location", "http://127.0.0.1:" + str(proxy._port) + "/api/config")]}
    with pytest.raises(RuntimeError, match="redirects"):
        proxy.request("GET", "/api/redirect")
    assert len(requests) == 1


def test_response_length_and_filename_are_bounded_before_relay(archive, monkeypatch):
    proxy, _, _, response = archive
    monkeypatch.setattr(legacy, "MAX_RESPONSE_BYTES", 8)
    response["override"] = {"status": 200, "media": "application/octet-stream", "payload": b"", "length": 9}
    with pytest.raises(RuntimeError, match="bounded framing"):
        proxy.request("GET", "/api/oversized")
    response["override"] = {"status": 200, "media": "application/octet-stream", "payload": b"123",
                            "filename": 'attachment; filename="../escape.csv"'}
    with pytest.raises(RuntimeError, match="filename is unsafe"):
        proxy.request("GET", "/api/filename")


def test_real_metadata_protocol_and_only_two_allowed_posts(archive):
    proxy, requests, state, _ = archive
    assert proxy.is_legacy_proxy is True
    assert proxy.workspace_state()["project"]["id"] == "a" * 32
    assert proxy.configuration() == {"model_access": False, "execution_allowed": False}
    changed = proxy.workspace_state({"expected_revision": 0, "name": "Saved samples"})
    assert changed["project"]["name"] == state["project"]["name"] == "Saved samples"
    assert proxy.request("POST", "/api/inputs/inspect", {"source": "/approved/sample.h5ad"})[0] == 200
    assert [row["path"] for row in requests if row["method"] == "POST"] == ["/api/workspace", "/api/inputs/inspect"]
    assert proxy.close() is None


def test_notebook_html_is_relayed_only_as_a_safe_attachment(archive):
    proxy, _, _, response = archive
    body = b'<!doctype html><html><body>Retained notebook</body></html>'
    response["override"] = {"status": 200, "media": "text/html; charset=utf-8", "payload": body,
                            "filename": 'attachment; filename="notebook.html"'}
    assert proxy.request("GET", "/api/jobs/own/artifact?name=notebook.html") == (
        200, "text/html; charset=utf-8", "notebook.html", body)
    response["override"].pop("filename")
    with pytest.raises(RuntimeError, match="content type is unavailable"):
        proxy.request("GET", "/api/jobs/own/artifact?name=notebook.html")
    response["override"]["filename"] = 'inline; filename="notebook.html"'
    with pytest.raises(RuntimeError, match="filename is unsafe"):
        proxy.request("GET", "/api/jobs/own/artifact?name=notebook.html")
    response["override"]["filename"] = 'attachment; filename="../notebook.html"'
    with pytest.raises(RuntimeError, match="filename is unsafe"):
        proxy.request("GET", "/api/jobs/own/artifact?name=notebook.html")


@pytest.mark.parametrize("payload", [b'{"value":NaN}', b'{"value":1e999}', b'{"a":1,"a":2}', b'[]'])
def test_metadata_helpers_reject_nonfinite_duplicate_or_nonobject_json(archive, payload):
    proxy, _, _, response = archive
    response["override"] = {"status": 200, "media": "application/json", "payload": payload}
    with pytest.raises(ValueError):
        proxy.configuration()


def test_constructor_rejects_nonlocal_protocol_parameters(archive):
    proxy, _, _, _ = archive
    for port in (0, 65536, True, "8891"):
        with pytest.raises(ValueError):
            legacy.LegacyWorkbenchProxy(proxy.workspace, port, "private-child-token")
    with pytest.raises(ValueError):
        legacy.LegacyWorkbenchProxy(proxy.workspace, proxy._port, "token\r\nHost:evil")
