"""Opt-in execution of the complete scientific catalog through a governed graph."""

import json
import os

import pytest

from devbench.core.protected_partitions import digest
from devbench.engine.exploratory_images import exploratory_image
from devbench.engine.exploratory_oci import ExploratoryOCI
from devbench.engine.methods import MethodService
from devbench.engine.provenance import hash_artifact
from devbench.engine.workflow_operations import LEDGER_NAME
from devbench.platform.adaptive_workflow import Port
from tests.support.workflow_science_fixtures import custom
from tests.test_investigation_invalidation import make, step

pytestmark = [
    pytest.mark.wal,
    pytest.mark.skipif(
        os.environ.get("DEVBENCH_EXPLORE_SCIENCE") != "1",
        reason="requires the operator-staged full scientific image catalog",
    ),
]

PYTHON_PACKAGES = (
    "numpy",
    "scipy",
    "pandas",
    "pyarrow",
    "polars",
    "anndata",
    "scanpy",
    "mudata",
    "spatialdata",
    "tiledbsoma",
    "statsmodels",
    "sklearn",
    "matplotlib",
    "seaborn",
    "plotly",
    "umap",
    "igraph",
    "leidenalg",
    "harmonypy",
)
R_PACKAGES = (
    "SingleCellExperiment",
    "Seurat",
    "edgeR",
    "limma",
    "DESeq2",
    "scran",
    "scuttle",
    "miloR",
    "satuRn",
    "stageR",
    "variancePartition",
    "dreamlet",
    "ggplot2",
    "arrow",
    "zellkonverter",
)


def _source(language, packages):
    if language == "r":
        return (
            "packages <- c(" + ",".join(json.dumps(p) for p in packages) + ")\n"
            "for (package in packages) loadNamespace(package)\n"
            'writeLines(jsonlite::toJSON(packages), "/outputs/result.json")\n'
        )
    return f"""import importlib, json
from pathlib import Path
import numpy as np
import zarr
packages = {packages!r}
for module in packages:
    importlib.import_module(module)
folder = Path('/outputs/summary.zarr')
group = zarr.open_group(str(folder), mode='w', zarr_format=3)
group.create_array('X', data=np.array([[2,3]], dtype='int32'), chunks=(1,2))
group.create_group('obs')
group.create_group('var')
assert json.loads((folder/'zarr.json').read_text())['zarr_format'] == 3
Path('/outputs/result.json').write_text(json.dumps(packages))
"""


@pytest.mark.parametrize("language", ["python", "r"])
def test_full_scientific_packages_in_approved_offline_worker(tmp_path, language):
    root = tmp_path.resolve()
    flow = make(root, name=LEDGER_NAME)
    methods = MethodService(root)
    worker = ExploratoryOCI(methods, flow.ledger, [{}])
    packages = PYTHON_PACKAGES if language == "python" else R_PACKAGES
    pin = exploratory_image(
        "devharness-explore-" + ("py" if language == "python" else "r")
    )
    contract, source, lock = custom(root)
    source.write_text(_source(language, packages))
    contract.update(
        method_id="synthetic.imports." + language,
        inputs={},
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        outputs={
            "out": {
                "artifact_type": "json",
                "required": True,
                "path": "/outputs/result.json",
            }
        },
    )
    ports = (Port(name="out", artifact_kind="table"),)
    if language == "python":
        contract["outputs"]["matrix"] = {
            "artifact_type": "file",
            "path": "/outputs/summary.zarr",
            "required": True,
        }
        ports += (Port(name="matrix", artifact_kind="continuous_matrix"),)
    contract["execution"].update(
        cpu=2,
        pids=256,
        memory_mb=4096,
        scratch_mb=512,
        timeout_seconds=180,
        max_output_bytes=20_000_000,
    )
    contract["environment"]["image"] = pin.reference
    contract["implementation"].update(
        source_sha256=hash_artifact(source)["sha256"],
        entrypoint=(
            [pin.executable, "/method/main.py"]
            if language == "python"
            else [pin.executable, "--vanilla", "/method/main.R"]
        ),
    )
    installed = methods.register_method(
        contract, source=source, environment_lock=lock, actor="synthetic-operator"
    )
    node = step("imports")
    cap = node.profile.operations[0].model_copy(
        update={
            "max_memory_mb": 4096,
            "timeout_seconds": 180,
            "output_kinds": tuple(p.artifact_kind for p in ports),
        }
    )
    flow.insert(
        node.model_copy(
            update={
                "profile": node.profile.model_copy(update={"operations": (cap,)}),
                "outputs": ports,
                "parameters_sha256": digest({}),
                "oci_method_version_id": installed["version_id"],
                "environment_sha256": digest(
                    {**contract["environment"], "image_id": pin.image_id}
                ),
            }
        )
    )
    worker.approve_step(
        flow, "imports", actor_id="synthetic-operator", data_classification="synthetic"
    )
    result = flow.dispatch("imports", worker, actor="synthetic-operator")
    evidence = worker._load(worker.runs / result["attempt_id"] / "io.json")
    assert result["status"] == "succeeded", evidence["runtime"]
    assert evidence["runtime"]["oci_isolated"] is True
    assert evidence["runtime"]["image_id"] == pin.image_id
    artifact = result["outputs"]["out"]
    assert artifact["claim_tier"] == "L1_execution_verified"
    with worker.artifacts.open(artifact["sha256"]) as stream:
        assert json.load(stream) == list(packages)
    if language == "python":
        worker.zarr_vault.verify(result["outputs"]["matrix"]["sha256"])
    print(f"{language} {pin.image_id} loaded {len(packages)} packages")
