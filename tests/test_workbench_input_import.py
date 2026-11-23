"""A user-selected file becomes a discoverable local input."""

import json
import os
import threading
from http.client import HTTPConnection
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from devbench.platform.workbench_ui import WorkbenchService, make_server


def test_loopback_import_publishes_one_new_file_without_replacing_inputs(workspace_tmp_path):
    root = workspace_tmp_path / "data"
    root.mkdir()
    existing = root / "existing.csv"
    existing.write_bytes(b"gene,A\nx,3\n")
    app = WorkbenchService(
        workspace_tmp_path / "state", [root], input_import_root=root,
    )
    app.inputs.limits["max_file_bytes"] = 4
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    counts = b"gene,B\nx,7\n"

    def send(name, source=origin):
        return urlopen(Request(
            origin + "/api/inputs/import?" + urlencode({"name": name}),
            data=counts,
            headers={"Origin": source, "Content-Type": "application/octet-stream"},
        ), timeout=5)

    try:
        with send("My counts.csv") as response:
            assert response.status == 201
        with urlopen(origin + "/api/inputs", timeout=5) as response:
            rows = {row["filename"]: row for row in json.load(response)["inputs"]}
        assert set(rows) == {"existing.csv", "My counts.csv"}
        row = rows["My counts.csv"]
        assert row["analysis_size_supported"] is False
        assert row["sha256"] is None
        with urlopen(origin + row["download_url"], timeout=5) as response:
            assert response.headers["Content-Length"] == str(len(counts))
            assert response.headers["Content-Disposition"].startswith("attachment; ")
            assert response.read() == counts
        assert (root / "My counts.csv").read_bytes() == counts
        assert (root / "My counts.csv").stat().st_nlink == 1

        for name, source, code in [
            ("existing.csv", origin, 409),
            ("../outside.csv", origin, 400),
            ("untrusted.csv", "http://other.invalid", 403),
        ]:
            with pytest.raises(HTTPError) as failure:
                send(name, source)
            assert failure.value.code == code
            failure.value.close()
        assert existing.read_bytes() == b"gene,A\nx,3\n"
        assert {path.name for path in root.iterdir()} == {"existing.csv", "My counts.csv"}

        large = root / "large-counts.h5ad"
        size = 4 * 1024**3 + 1
        with large.open("wb") as target:
            target.write(b"counts")
            target.truncate(size)
        with urlopen(origin + "/api/inputs/download?name=large-counts.h5ad", timeout=5) as response:
            assert response.headers["Content-Length"] == str(size)
            assert response.read(6) == b"counts"

        disk = os.statvfs(root)
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        try:
            connection.request("POST", "/api/inputs/import?name=no-space.h5ad", headers={
                "Content-Length": str(disk.f_bavail * disk.f_frsize + 1024**3),
                "Content-Type": "application/octet-stream",
                "Origin": origin,
            })
            with connection.getresponse() as response:
                assert response.status == 507
        finally:
            connection.close()
        assert not (root / "no-space.h5ad").exists()
        assert not list(root.glob(".input-import-*"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        app.close()
