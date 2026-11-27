"""Physical sparse stores and fresh-process RSS checks of the mounted RNA workflow."""

from __future__ import annotations

import hashlib
import json
import os
import resource
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import h5py
import numpy as np
import pytest

from devharness_core.provenance import canonical_json

_ROWS = 17_000
_GENES = 36_000
_WIDTH = 4_000
_CHUNK_ROWS = 125


def _write_matrix(path, rows=_ROWS, genes=_GENES, width=_WIDTH):
    """Write every sparse entry. No padding, sparse file holes or dense conversion."""
    import anndata as ad
    import pandas as pd
    import zarr
    from scipy.sparse import csr_matrix

    cell = np.arange(rows)
    donors = cell % 8
    obs = pd.DataFrame(
        {
            "donor": [f"d{i}" for i in donors],
            "population": np.where(cell % 16 < 8, "P", "N"),
            "stage": np.where(donors < 4, "E1", "E2"),
        },
        index=[f"cell-{i}" for i in cell],
    )
    meta = ad.AnnData(
        X=csr_matrix((rows, genes), dtype="i4"),
        obs=obs,
        var=pd.DataFrame(index=[f"synthetic-gene-{i}" for i in range(genes)]),
    )
    if path.suffix == ".h5ad":
        meta.write_h5ad(path)
        root = h5py.File(path, "r+")
        opts = {"chunks": (min(_CHUNK_ROWS, rows) * width,)}
    else:
        meta.write_zarr(path)
        root = zarr.open_group(str(path), mode="a")
        opts = {"compressor": None, "chunks": (min(_CHUNK_ROWS, rows) * width,)}
    try:
        group = root["layers"].create_group("counts")
        group.attrs.update(
            {
                "encoding-type": "csr_matrix",
                "encoding-version": "0.1.0",
                "shape": [rows, genes],
            }
        )
        for name in ("indices", "data"):
            group.create_dataset(name, shape=(rows * width,), dtype="<i4", **opts)
        group.create_dataset("indptr", data=np.arange(rows + 1, dtype="<i8") * width)
        base = np.arange(width, dtype="<i4") * (genes // width)
        for start in range(0, rows, _CHUNK_ROWS):
            end = min(rows, start + _CHUNK_ROWS)
            r = np.arange(start, end, dtype="<i4")[:, None]
            group["indices"][start * width : end * width] = (
                base + r % (genes // width)
            ).ravel()
            group["data"][start * width : end * width] = np.broadcast_to(
                1 + r % 7, (end - start, width)
            ).ravel()
    finally:
        if isinstance(root, h5py.File):
            root.close()
        else:
            zarr.consolidate_metadata(str(path))


def _seal(path):
    for item in path.rglob("*") if path.is_dir() else ():
        item.chmod(0o555 if item.is_dir() else 0o444)
    path.chmod(0o555 if path.is_dir() else 0o444)


def _spec(sha):
    return {
        "format": "devbench.scrna-population-spec/1",
        "source_sha256": sha,
        "measurement": "umi_counts",
        "counts_layer": "counts",
        "populations": [
            {"label": n, "column": "population", "values": [n]} for n in ("P", "N")
        ],
        "biological_unit_column": "donor",
        "biological_unit_identity": "declared",
        "stage_column": "stage",
        "stage_levels": ["E1", "E2"],
        "technical_alias_columns": [],
        "question": "Aggregate synthetic counts by donor and population.",
        "requirement_source": {"origin": "synthetic_test_requirement"},
        "procedure": "embryo_fixed_effect_stage_population_nb_v1",
        "choice_origin": "assistant_proposed",
    }


def _peak():
    unit = 1 if sys.platform == "darwin" else 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit


def _workspace():
    from devbench.engine.permissions import require_protected_storage

    parent = Path(__file__).resolve().parents[1] / ".pytest-workspaces"
    parent.mkdir(mode=0o700, exist_ok=True)
    return TemporaryDirectory(dir=require_protected_storage(parent))


def _child(role, path, root):
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.test_out_of_core_vault",
            role,
            str(path),
            str(root),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=360,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _control(source, root):
    from devbench.core.expression_inventory import expression_inventory
    from devbench.core.rna_answerability import (
        AnswerabilityRequest,
        AttestedMeasurement,
        assess_answerability,
    )
    from devbench.engine.mounted_matrix_vault import MountedZarrVault
    from devbench.platform.plugin_mcp import Inspect, Plugin

    inventory = expression_inventory(source)
    assert inventory["shape"] == (_ROWS, _GENES) and "counts" in inventory["layers"]
    with _workspace() as workspace:
        plugin = Plugin(Path(workspace), source.parent, None)
        inspected = plugin.inspect(
            Inspect.model_validate(
                {
                    "inputs": [
                        {
                            "path": str(source),
                            "role": "expression",
                            "data_classification": "synthetic",
                        }
                    ]
                }
            )
        )["inputs"][0]
    assert inspected["observed"]["file_type"] == inventory["format"]
    assert len(inspected["inspection_receipt_sha256"]) == 64
    vault = MountedZarrVault(root / "vault")
    sha = vault.put(source)
    pin = vault.attestation(sha)
    assert pin["storage_mode"] == "zero_copy" and pin["size_bytes"] > 512 * 1024**2
    decision = assess_answerability(
        AnswerabilityRequest(
            question="population_qc", assay="custom", entry_point="annotated_object"
        ),
        measurements=(
            AttestedMeasurement(
                kind="gene_counts",
                artifact_sha256=sha,
                slot="layers/counts",
                receipt_sha256=hashlib.sha256(canonical_json(pin)).hexdigest(),
            ),
        ),
    )
    assert (
        decision.axis_b == "B_OPEN"
        and decision.claim_tier == "none"
        and decision.status == "tier2_exploratory_only"
    )
    stage = root / "worker"
    inputs, outputs = stage / "inputs", stage / "outputs"
    inputs.mkdir(mode=0o700, parents=True)
    outputs.mkdir(mode=0o700)
    target = inputs / ("expression" + source.suffix)
    vault.stage(sha, target)
    member = Path("layers/counts/data/0") if source.is_dir() else Path(".")
    assert (source / member).stat().st_ino == (target / member).stat().st_ino
    request = {
        "format": "devbench.mounted-population-qc/1",
        "source": target.name,
        "specification": _spec(sha),
    }
    raw = canonical_json(request)
    (inputs / "request.json").write_bytes(raw)
    vault.verify(sha)
    worker = _child("worker", stage, root)
    summary = json.loads((outputs / "summary.json").read_bytes())
    vault.verify(sha)
    assert summary["request_sha256"] == hashlib.sha256(raw).hexdigest()
    assert summary["matrix"]["shape"] == [_GENES, 16]
    assert all(summary["count_validation"].values())
    assert (
        not summary["clinical_release_allowed"] and not summary["independence_verified"]
    )
    expected_total = sum(1 + i % 7 for i in range(_ROWS)) * _WIDTH
    assert sum(summary["profile_library_sizes"]) == expected_total
    with h5py.File(outputs / "pseudobulk.h5", "r") as capsule:
        # Independently check the entire feature axis, including its row order.
        by_residue = [
            sum(1 + i % 7 for i in range(residue, _ROWS, 9)) for residue in range(9)
        ]
        np.testing.assert_array_equal(
            capsule["expression"][:].sum(axis=1), np.tile(by_residue, _WIDTH)
        )
    if os.environ.get("DEVBENCH_T15_DOCKER") == "1":
        # The subsequent run gets a new admission after these mounts close.
        shutil.rmtree(stage)
        second = _child("exploratory", source, root)
        assert second["count_sum"] == expected_total
        worker["tier2_worker_peak_rss_bytes"] = second["peak_rss_bytes"]
        worker["tier2_control_peak_rss_bytes"] = second["control_peak_rss_bytes"]
    return {
        "format": inventory["format"],
        "input_bytes": pin["size_bytes"],
        "genes": _GENES,
        "nonzeros": _ROWS * _WIDTH,
        "control_peak_rss_bytes": _peak(),
        **worker,
    }


def _worker(stage):
    from devbench.engine.mounted_population_qc import run_mounted_population_qc

    inputs, outputs = stage / "inputs", stage / "outputs"
    sha = hashlib.sha256((inputs / "request.json").read_bytes()).hexdigest()
    image = os.environ.get("DEVBENCH_STREAMING_TEST_IMAGE")
    if image:
        from devbench.core.execution_contracts import ResourceProfile
        from devbench.engine.mounted_population_qc import execute_population_qc

        proof = execute_population_qc(
            stage,
            request_sha=sha,
            image_id=image,
            attempt_id="1" * 32,
            resource_profile=ResourceProfile(
                cpus=2, memory_mb=4096, pids_limit=128, timeout_sec=300
            ),
            timeout=300,
            memory_mb=4096,
            data_classification="synthetic",
        )
        assert (
            proof["cleanup_confirmed"]
            and proof["oci_isolated"]
            and proof["claim_tier"] == "L1_execution_verified"
        ), proof
        peak = json.loads((outputs / "resource-usage.json").read_bytes())[
            "peak_rss_bytes"
        ]
        return {"worker_backend": "docker", "worker_peak_rss_bytes": peak}
    run_mounted_population_qc(inputs, outputs, sha)
    return {"worker_backend": "test_subprocess", "worker_peak_rss_bytes": _peak()}


def _exploratory(source):
    from devbench.core.protected_partitions import digest
    from devbench.engine.exploratory_images import exploratory_image
    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.engine.methods import MethodService
    from devbench.engine.workflow_operations import LEDGER_NAME
    from devharness_core.provenance import hash_artifact
    from tests.support.workflow_science_fixtures import custom
    from tests.test_investigation_invalidation import artifact, link, make, step

    with _workspace() as temporary:
        stage = Path(temporary)
        flow = make(stage, name=LEDGER_NAME)
        methods = MethodService(stage)
        params = {"threshold": 1}
        executor = ExploratoryOCI(methods, flow.ledger, [params])
        sha = hash_artifact(source)["sha256"]
        rep = executor.admit_input(source, expected_sha256=sha, artifact_type="file")
        flow.register_artifact(
            artifact("matrix", representation=rep).model_copy(update={"sha256": sha})
        )
        contract, script, lock = custom(stage)
        script.write_text(
            "import json, resource\n"
            "from pathlib import Path\n"
            "import h5py, zarr\n"
            "p = Path('/inputs/data')\n"
            "g = (zarr.open_group(str(p), mode='r') if p.is_dir()\n"
            "     else h5py.File(p, 'r'))\n"
            "a = g['layers/counts/data']\n"
            "n = sum(int(a[i:i+262144].sum(dtype='int64'))\n"
            "        for i in range(0, a.shape[0], 262144))\n"
            "peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024\n"
            "result = dict(count_sum=n, peak_rss_bytes=peak)\n"
            "Path('/outputs/out.json').write_text(json.dumps(result))\n"
        )
        pin = exploratory_image("devharness-explore-py")
        contract["environment"]["image"] = pin.reference
        contract["implementation"].update(
            entrypoint=[pin.executable, "/method/main.py"],
            source_sha256=hash_artifact(script)["sha256"],
        )
        contract.update(
            inputs={"data": {"artifact_type": "file", "required": True}},
            outputs={
                "out": {
                    "artifact_type": "json",
                    "path": "/outputs/out.json",
                    "required": True,
                }
            },
        )
        contract["execution"].update(memory_mb=1024, pids=128, timeout_seconds=120)
        registered = methods.register_method(
            contract, source=script, environment_lock=lock, actor="fixture-operator"
        )
        node = step(
            "stream", link("data", "matrix", immutable=True), representations=(rep,)
        )
        cap = node.profile.operations[0].model_copy(
            update={"max_memory_mb": 1024, "timeout_seconds": 120}
        )
        node = node.model_copy(
            update={
                "profile": node.profile.model_copy(update={"operations": (cap,)}),
                "oci_method_version_id": registered["version_id"],
                "parameters_sha256": digest(params),
                "environment_sha256": digest(
                    {**contract["environment"], "image_id": pin.image_id}
                ),
            }
        )
        flow.insert(node)
        executor.approve_step(
            flow, "stream", actor_id="fixture-operator", data_classification="synthetic"
        )
        outcome = flow.dispatch("stream", executor, actor="fixture-operator")
        assert outcome["status"] == "succeeded", outcome
        output = flow.current_use("stream", "out", purpose="cache")
        assert output.claim_tier == "L1_execution_verified" and output.exploratory_only
        with executor.training.open(output.sha256) as stream:
            return {**json.load(stream), "control_peak_rss_bytes": _peak()}


@pytest.mark.parametrize("suffix", [".h5ad", ".zarr"])
def test_real_sparse_population_scale(tmp_path, suffix):
    with TemporaryDirectory(dir=tmp_path.resolve()) as temporary:
        root = Path(temporary)
        source = root / ("cohort" + suffix)
        _write_matrix(source)
        _seal(source)
        stats = _child("control", source, root)
        print(json.dumps(stats, sort_keys=True), flush=True)
        assert stats["control_peak_rss_bytes"] <= 2 * 1024**3
        assert stats["worker_peak_rss_bytes"] <= 4 * 1024**3
        if "tier2_worker_peak_rss_bytes" in stats:
            assert stats["tier2_control_peak_rss_bytes"] <= 2 * 1024**3
            assert stats["tier2_worker_peak_rss_bytes"] <= 4 * 1024**3
    assert not root.exists()


def test_mutable_snapshot_and_changed_attestation(tmp_path, monkeypatch):
    from devbench.engine import mounted_matrix_vault as vault_module

    monkeypatch.setattr(vault_module, "ZERO_COPY_THRESHOLD", 0)
    vault = vault_module.MountedZarrVault(tmp_path.resolve() / "vault")
    original = tmp_path.resolve() / "counts.zarr"
    original.mkdir()
    data = original / "chunk"
    data.write_bytes(b"original")
    sha = vault.put(original)
    assert vault.attestation(sha)["storage_mode"] == "immutable_copy"
    data.write_bytes(b"read-only")
    _seal(original)
    pin = vault.put(original)
    stage = tmp_path.resolve() / "staged"
    vault.stage(pin, stage)
    assert (stage / "chunk").samefile(data)
    data.chmod(0o644)
    data.write_bytes(b"different")
    data.chmod(0o444)
    with pytest.raises(ValueError, match="attestation|digest"):
        vault.verify(pin)
    vault.verify(sha)


@pytest.mark.parametrize("suffix", [".h5mu", ".zarr"])
def test_multimodal_inventory_checks_global_observation_alignment(tmp_path, suffix):
    import zarr

    from devbench.core.expression_inventory import expression_inventory

    path = tmp_path.resolve() / ("multi" + suffix)
    root = (
        h5py.File(path, "w")
        if suffix == ".h5mu"
        else zarr.open_group(str(path), mode="w")
    )
    try:
        root.create_group("obs").create_dataset(
            "_index", data=np.array([b"a", b"b", b"c"])
        )
        mods, maps = root.create_group("mod"), root.create_group("obsmap")
        for name, labels in (("rna", [b"a", b"c"]), ("prot", [b"c", b"a"])):
            group = mods.create_group(name)
            group.create_group("obs").create_dataset("_index", data=np.array(labels))
            group.create_dataset("X", data=np.zeros((2, 3), dtype=np.float32))
            group.create_group("layers")
            maps.create_dataset(name, data=np.array([1, 0, 2]))
    finally:
        if isinstance(root, h5py.File):
            root.close()
    slots = expression_inventory(path)["modalities"]
    assert slots["rna"]["obs_aligned"] and not slots["prot"]["obs_aligned"]
    assert not any(slot["count_1_verified"] for slot in slots.values())


def test_spatial_and_soma_slot_inventory(tmp_path, monkeypatch):
    import zarr

    from devbench.core.expression_inventory import expression_inventory

    path = tmp_path.resolve() / "spatial.zarr"
    root = zarr.open_group(str(path), mode="w")
    img = root.create_group("images").create_group("section")
    axes = [{"name": "x", "unit": "micrometer"}]
    img.attrs["multiscales"] = [{"axes": axes}]
    result = expression_inventory(path)
    assert result["elements"]["images"] == ["section"]
    assert (
        result["coordinate_metadata"]["images/section"]["multiscales"][0]["axes"]
        == axes
    )
    soma = path.parent / "atlas.soma"
    soma.mkdir()
    (soma / "__tiledb_group.tdb").touch()
    monkeypatch.setitem(sys.modules, "tiledbsoma", None)
    assert expression_inventory(soma) == {
        "format": "tiledb_soma",
        "reader_available": False,
        "slots": None,
    }


if __name__ == "__main__":
    role, path, root = sys.argv[1:]
    if role == "control":
        outcome = _control(Path(path), Path(root))
    elif role == "exploratory":
        outcome = _exploratory(Path(path))
    else:
        outcome = _worker(Path(path))
    print(json.dumps(outcome))
