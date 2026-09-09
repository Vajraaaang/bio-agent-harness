"""Untrusted evidence types must be rejected without waiting for a FIFO writer."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from devharness_core.provenance import ExecutionDAG


_CHILD = r"""
import json
import sys
from pathlib import Path
from devharness_core.provenance import _open_regular, verify_execution
Path(sys.argv[1]).write_text("imports complete")
if sys.argv[2] == "direct":
    try:
        with _open_regular(Path(sys.argv[3])):
            pass
    except ValueError as error:
        print(json.dumps({"rejected": True, "error": str(error)}))
    else:
        print(json.dumps({"rejected": False}))
else:
    print(json.dumps(verify_execution(sys.argv[3], sys.argv[4])))
"""


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="Requires POSIX FIFOs")
@pytest.mark.parametrize("position", [
    "direct", "manifest", "root_receipt", "ledger", "node", "artifact",
    "directory_descriptor",
])
def test_fifo_evidence_is_rejected_without_a_writer(tmp_path, position):
    source = tmp_path / "input.csv"
    source.write_text("gene,sample\nSox2,12\n")
    collection = tmp_path / "counts.zarr"
    collection.mkdir()
    (collection / "chunk").write_bytes(b"synthetic counts")
    dag = ExecutionDAG(tmp_path / "audit")
    artifact = dag.add_artifact(source)
    directory = dag.add_artifact(collection)
    node_id = dag.append("input", {"artifact": artifact, "directory": directory})
    receipt = dag.finalize()
    manifest = Path(receipt["manifest_path"])
    target = {
        "direct": source,
        "manifest": manifest,
        "root_receipt": dag.root_dir / "root.sha256",
        "ledger": dag.root_dir / "ledger.json",
        "node": dag.root_dir / "nodes" / (node_id + ".json"),
        "artifact": dag.root_dir / "objects" / artifact["sha256"],
        "directory_descriptor": dag.root_dir / "objects" / directory["sha256"],
    }[position]
    target.unlink()
    os.mkfifo(target, 0o600)
    ready = tmp_path / "ready"
    action = "direct" if position == "direct" else "verify"
    argument = target if action == "direct" else manifest
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", _CHILD, str(ready), action,
         str(argument), receipt["root_hash"]],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        # Start the I/O deadline only after imports, keeping slow startup distinct
        # from waiting indefinitely for a writer to an untrusted evidence FIFO.
        startup_deadline = time.monotonic() + 30
        while not ready.exists() and process.poll() is None and time.monotonic() < startup_deadline:
            time.sleep(0.01)
        assert ready.exists(), "Reader did not complete startup"
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            pytest.fail("Evidence reader blocked after imports on a FIFO with no writer")
        assert process.returncode == 0, stderr
        result = json.loads(stdout)
        if action == "direct":
            assert result["rejected"] is True
            assert "regular file" in result["error"]
        else:
            assert result["valid"] is False
            assert any("regular file" in error for error in result["errors"])
            assert result["compliance_certified"] is False
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
        # Do not leave special files in retained test artifacts.
        target.unlink(missing_ok=True)
