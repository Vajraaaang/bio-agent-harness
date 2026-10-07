"""Authenticated local scientific routes keep the existing browser boundary."""
from contextlib import contextmanager
import http.client
import json
import threading

from devbench.analysis.workbench_ui import make_server
from tests.test_scientific_workspace import tmp_path, setup_workspace


@contextmanager
def running(app):
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def call(port, path, *, token=None, body=None, origin=None):
    headers = {}
    if token is not None:
        headers["X-Workbench-Token"] = token
    if origin is not None:
        headers["Origin"] = origin
    encoded = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        encoded = json.dumps(body)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("POST" if body is not None else "GET", path, body=encoded, headers=headers)
        response = connection.getresponse()
        return response.status, response.read(), response.getheader("Content-Type")
    finally:
        connection.close()


def test_scientific_routes_preserve_auth_origin_revision_and_source_read_boundaries(tmp_path):
    app, methods, _, step, _ = setup_workspace(tmp_path)
    try:
        with running(app) as port:
            assert call(port, "/api/scientific")[0] == 403
            before = methods.database.read_bytes()
            assert call(port, "/api/scientific", token=app.token)[0] == 200
            assert methods.database.read_bytes() == before
            request = {"action": "initialize", "expected_revision": 0}
            assert call(port, "/api/scientific", token=app.token, body=request)[0] == 403
            assert call(port, "/api/scientific", token=app.token, body=request, origin="https://foreign.invalid")[0] == 403
            origin = "http://127.0.0.1:" + str(port)
            assert call(port, "/api/scientific", token=app.token, body=request, origin=origin)[0] == 200
            revision = app.scientific.catalog()["revision"]
            create = {"action": "create_study", "name": "HTTP study", "aim": "Verify local authentication", "expected_revision": revision}
            assert call(port, "/api/scientific", token=app.token, body=create, origin=origin)[0] == 200
            assert call(port, "/api/scientific", token=app.token, body=create, origin=origin)[0] == 409
            before = methods.database.read_bytes()
            status, data, media = call(port, "/api/scientific/methods/" + step["method_version_id"], token=app.token)
            preview = json.loads(data)
            assert status == 200 and media.startswith("application/json")
            assert preview["execution_started"] is False
            assert preview["source_text"].startswith("# A previously unknown")
            assert methods.database.read_bytes() == before
            assert methods.list_records("run") == []
    finally:
        app.close()


def test_exact_version_link_requires_auth_and_never_follows_latest(tmp_path):
    app, _, _, _, _ = setup_workspace(tmp_path)
    try:
        with running(app) as port:
            reference = "object_type=dataset&object_id=" + "f" * 32 + "&version_id=" + "a" * 64
            assert call(port, "/?" + reference)[0] == 200  # Shell only, no scientific content.
            assert call(port, "/api/scientific/resolve?" + reference)[0] == 403
            status, data, _ = call(port, "/api/scientific/resolve?" + reference, token=app.token)
            assert status == 404 and "unavailable" in json.loads(data)["message"]
            assert call(port, "/api/jobs", token=app.token)[0] == 200
    finally:
        app.close()
