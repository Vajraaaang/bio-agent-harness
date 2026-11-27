"""Synthetic exploratory binary transport with a closed JSON manifest."""

import hashlib
import json

import pytest

from devbench.analysis.method_validation import validate_outputs

_FORMAT = "devbench.oci-binary-artifacts/1"


def _fixture(root):
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = root / "table.parquet"
    pq.write_table(pa.table({"gene_id": ["g1", "g2"], "effect": [0.1, -0.5]}), table)
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "format": _FORMAT,
                "artifacts": {
                    "result": {
                        "format": "parquet",
                        "sha256": hashlib.sha256(table.read_bytes()).hexdigest(),
                        "size_bytes": table.stat().st_size,
                        "row_count": 2,
                    }
                },
            }
        )
    )
    contract = {
        "binary_transport": _FORMAT,
        "implementation": {"kind": "oci"},
        "outputs": {
            "manifest": {
                "artifact_type": "json",
                "path": "/outputs/manifest.json",
                "required": True,
            },
            "result": {
                "artifact_type": "file",
                "path": "/outputs/table.parquet",
                "required": True,
            },
        },
        "execution": {"max_output_bytes": 100_000_000},
        "validation": {"comparator": "file", "invariants": []},
    }
    return contract, {"manifest": manifest, "result": table}


def test_oci_binary_manifest_binds_streamed_table(tmp_path):
    contract, outputs = _fixture(tmp_path)
    validation = validate_outputs(contract, {}, outputs)
    assert validation["binary_artifacts"]["result"]["row_count"] == 2
    data = json.loads(outputs["manifest"].read_bytes())
    data["artifacts"]["result"]["row_count"] = 3
    outputs["manifest"].write_text(json.dumps(data))
    with pytest.raises(ValueError, match="row count"):
        validate_outputs(contract, {}, outputs)


def test_output_archive_spools_without_retaining_stdout(tmp_path):
    import os
    import sys

    from devbench.engine.method_executor import _bounded

    command = [
        sys.executable,
        "-c",
        "import sys; sys.stdout.buffer.write(b'x' * 100_000)",
    ]
    with (tmp_path / "spool").open("w+b") as stream:
        code, output, errors = _bounded(
            command, os.environ, 10, 200_000, stdout_sink=stream
        )
        assert code == 0 and output == errors == b""
        assert stream.tell() == 100_000
        with pytest.raises(ValueError, match="exceeded the byte limit"):
            _bounded(command, os.environ, 10, 500, stdout_sink=stream)


@pytest.mark.wal
def test_governed_oci_retains_large_binary_table(workspace_tmp_path):
    import os

    if os.environ.get("DEVBENCH_T15_DOCKER") != "1":
        pytest.skip("explicit local Docker smoke opt-in")
    import pyarrow as pa
    import pyarrow.parquet as pq

    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.engine.method_executor import EXPLORATORY_IMAGE
    from devbench.engine.methods import MethodService
    from devbench.engine.provenance import hash_artifact
    from devbench.platform.workflow_entrypoints import LEDGER_NAME
    from tests.support.workflow_science_fixtures import custom
    from tests.test_exploratory_execution_profile import bound_step
    from tests.test_investigation_invalidation import artifact, link, make, step

    root = workspace_tmp_path.resolve()
    table = root / "origin.parquet"
    pq.write_table(
        pa.table({"value": pa.repeat(pa.scalar("x" * 1024), 20_000)}),
        table,
        compression="NONE",
        use_dictionary=False,
    )
    assert table.stat().st_size > 16_000_000
    flow = make(root, name=LEDGER_NAME)
    methods = MethodService(root)
    executor = ExploratoryOCI(methods, flow.ledger, [{"threshold": 1}])
    digest = executor.binary_training.put_file(table)
    flow.register_artifact(
        artifact("input", representation="exploratory_parquet").model_copy(
            update={"sha256": digest}
        )
    )
    directory = root / "code"
    directory.mkdir()
    contract, source, lock = custom(directory)
    source.write_text("""
import hashlib, json, shutil
from pathlib import Path
source, target = Path('/inputs/data'), Path('/outputs/out.parquet')
shutil.copyfile(source, target)
with target.open('rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
Path('/outputs/manifest.json').write_text(json.dumps({
    'format': 'devbench.oci-binary-artifacts/1',
    'artifacts': {'out': {'format': 'parquet', 'sha256': digest,
                         'size_bytes': target.stat().st_size, 'row_count': 20000}}
}))
""")
    contract["method_id"] = "synthetic.binary"
    contract["environment"]["image"] = EXPLORATORY_IMAGE
    contract["implementation"]["source_sha256"] = hash_artifact(source)["sha256"]
    contract["binary_transport"] = _FORMAT
    contract["inputs"] = {"data": {"artifact_type": "file", "required": True}}
    contract["outputs"] = {
        "manifest": {
            "artifact_type": "json",
            "path": "/outputs/manifest.json",
            "required": True,
        },
        "out": {
            "artifact_type": "file",
            "path": "/outputs/out.parquet",
            "required": True,
        },
    }
    contract["validation"]["comparator"] = "file"
    contract["execution"].update(max_output_bytes=25_000_000)
    method = methods.register_method(
        contract, source=source, environment_lock=lock, actor="tester"
    )
    node = step(
        "binary",
        link("data", "input", immutable=True),
        representations=["exploratory_parquet"],
    )
    node = node.model_copy(
        update={
            "outputs": (
                *node.outputs,
                node.outputs[0].model_copy(update={"name": "manifest"}),
            )
        }
    )
    flow.insert(bound_step(method, node))
    executor.approve_step(
        flow, "binary", actor_id="test-operator", data_classification="synthetic"
    )
    outcome = flow.dispatch("binary", executor, actor="tester")
    assert outcome["status"] == "succeeded", outcome
    retained = flow.current_use("binary", "out", purpose="cache")
    assert retained.sha256 == digest
    assert executor.binary_descriptor(retained) == {
        "format": "parquet",
        "sha256": digest,
        "size_bytes": table.stat().st_size,
        "row_count": 20_000,
    }
    with pytest.raises(ValueError, match="byte budget"):
        with executor.training.open(digest):
            pytest.fail("Historical JSON storage admitted oversized binary bytes")
    attempt_dir = executor.runs / retained.version_id.split("-")[0]
    io = executor._load(attempt_dir / "io.json")
    receipt = executor._load(attempt_dir / "receipt.json")
    io["runtime"]["binary_artifacts"]["out"]["row_count"] -= 1
    receipt["io_sha256"] = executor._identity(io)
    executor._write(attempt_dir / "io.json", io)
    executor._write(attempt_dir / "receipt.json", receipt)
    with pytest.raises(ValueError, match="row count"):
        executor.binary_descriptor(retained)
