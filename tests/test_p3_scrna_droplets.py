"""Synthetic raw-droplet admission and representation custody checks."""

import json
import os
import re
import shutil
import subprocess
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import h5py
import numpy as np
import pytest
from scipy import sparse

from devharness_core.provenance import hash_artifact


def _write_tenx(path, *, data=(2, 99, 30), second_capture=False):
    with h5py.File(path, "w") as handle:
        matrix = handle.create_group("matrix")
        matrix["shape"] = [3, 3]
        matrix["data"] = data
        matrix["indices"] = [0, 2, 1]
        matrix["indptr"] = [0, 2, 2, 3]
        barcodes = [b"AAAA-1", b"CCCC-1", b"GGGG-2" if second_capture else b"GGGG-1"]
        matrix["barcodes"] = np.asarray(barcodes, dtype="S")
        features = matrix.create_group("features")
        features["id"] = np.asarray([b"g1", b"g2", b"a1"], dtype="S")
        features["name"] = np.asarray([b"Gene1", b"Gene2", b"Antibody1"], dtype="S")
        kinds = [b"Gene Expression", b"Gene Expression", b"Antibody Capture"]
        features["feature_type"] = np.asarray(kinds, dtype="S")


def _provenance(source):
    from rna_kernels.scrna_droplets import RawDropletProvenance

    return RawDropletProvenance(
        source_sha256=hash_artifact(source)["sha256"],
        capture_id="capture-x",
        origin_receipt_sha256=sha256(b"upstream-attested-raw-droplets").hexdigest(),
        source_kind="raw_feature_bc_matrix",
        filter_state="unfiltered",
    )


def test_attested_unfiltered_matrix_retains_all_barcodes_and_rna_totals(
    workspace_tmp_path,
):
    from rna_kernels.scrna_droplets import verify_raw_droplet_input

    source = workspace_tmp_path / "staged-input.h5"
    _write_tenx(source)
    provenance = _provenance(source)
    admitted = verify_raw_droplet_input(
        source,
        provenance,
        expected_provenance_sha256=provenance.sha256(),
    )
    assert admitted.capture_id == "capture-x"
    assert admitted.barcodes == ("AAAA-1", "CCCC-1", "GGGG-1")
    assert admitted.rna_features == 2
    np.testing.assert_array_equal(admitted.rna_totals, [2, 0, 30])
    assert hash_artifact(source)["sha256"] == provenance.source_sha256


@pytest.mark.parametrize(
    "forgery", ["filtered", "changed_attestation", "changed_bytes"]
)
def test_raw_name_does_not_supply_provenance(workspace_tmp_path, forgery):
    from rna_kernels.scrna_droplets import verify_raw_droplet_input

    source = workspace_tmp_path / "raw_feature_bc_matrix.h5"
    _write_tenx(source)
    provenance = _provenance(source)
    expected = provenance.sha256()
    if forgery == "filtered":
        provenance = replace(
            provenance,
            source_kind="filtered_feature_bc_matrix",
            filter_state="filtered",
        )
        expected = provenance.sha256()
    elif forgery == "changed_attestation":
        provenance = replace(provenance, capture_id="unverified-capture")
    else:
        _write_tenx(source, data=(3, 99, 30))
    with pytest.raises(ValueError, match="unfiltered|provenance|identity"):
        verify_raw_droplet_input(
            source, provenance, expected_provenance_sha256=expected
        )


def test_droplet_admission_rejects_anndata_even_under_raw_filename(workspace_tmp_path):
    from rna_kernels.scrna_droplets import verify_raw_droplet_input

    source = workspace_tmp_path / "raw_feature_bc_matrix.h5"
    with h5py.File(source, "w") as handle:
        handle.create_group("obs")
        handle.create_group("var")
        handle.create_group("layers")["counts"] = [[1, 2], [3, 4]]
    provenance = _provenance(source)
    with pytest.raises(ValueError, match="Cell Ranger v3"):
        verify_raw_droplet_input(
            source,
            provenance,
            expected_provenance_sha256=provenance.sha256(),
        )


@pytest.mark.parametrize("data", [(2, 99, -1), (2.5, 99, 30), (2, 99, np.nan)])
def test_invalid_droplet_values_are_refused(workspace_tmp_path, data):
    from rna_kernels.scrna_droplets import verify_raw_droplet_input

    source = workspace_tmp_path / "raw_feature_bc_matrix.h5"
    _write_tenx(source, data=data)
    provenance = _provenance(source)
    with pytest.raises(ValueError, match="nonnegative integer"):
        verify_raw_droplet_input(
            source,
            provenance,
            expected_provenance_sha256=provenance.sha256(),
        )


def test_pooled_gem_wells_are_refused(workspace_tmp_path):
    from rna_kernels.scrna_droplets import verify_raw_droplet_input

    source = workspace_tmp_path / "raw_feature_bc_matrix.h5"
    _write_tenx(source, second_capture=True)
    provenance = _provenance(source)
    with pytest.raises(ValueError, match="capture"):
        verify_raw_droplet_input(
            source,
            provenance,
            expected_provenance_sha256=provenance.sha256(),
        )


def test_genome_axis_cannot_point_outside_input_hdf5(workspace_tmp_path):
    from rna_kernels.scrna_droplets import verify_raw_droplet_input

    source = workspace_tmp_path / "raw_feature_bc_matrix.h5"
    other = workspace_tmp_path / "annotations.h5"
    _write_tenx(source)
    with h5py.File(other, "w") as handle:
        handle["genome"] = np.asarray([b"hg", b"hg", b""], dtype="S")
    with h5py.File(source, "r+") as handle:
        handle["matrix/features/genome"] = h5py.ExternalLink(str(other), "/genome")
    provenance = _provenance(source)
    with pytest.raises(ValueError, match="internal storage"):
        verify_raw_droplet_input(
            source,
            provenance,
            expected_provenance_sha256=provenance.sha256(),
        )


def test_runtime_lack_returns_no_derived_measurements(workspace_tmp_path, monkeypatch):
    from rna_kernels import scrna_droplets as droplets

    source = workspace_tmp_path / "raw_feature_bc_matrix.h5"
    _write_tenx(source)
    provenance = _provenance(source)
    outputs = workspace_tmp_path / "outputs"
    outputs.mkdir()

    def unexpected_process(*args, **kwargs):
        pytest.fail("Runtime omission must prevent subprocess execution")

    monkeypatch.setattr(droplets.subprocess, "run", unexpected_process)
    result = droplets.run_droplet_action(
        source,
        outputs,
        provenance,
        expected_provenance_sha256=provenance.sha256(),
        action=droplets.EmptyDropsAction(lower=5),
    )
    assert result["status"] == "unavailable"
    assert result["reason"] == "pinned_droplet_runtime_required"
    assert result["artifacts"] == {}
    assert list(outputs.iterdir()) == []


@pytest.mark.parametrize("tampered", [False, True])
def test_emptydrops_calls_bind_exact_barcodes_and_raw_totals(
    workspace_tmp_path,
    monkeypatch,
    tampered,
):
    from rna_kernels import scrna_droplets as droplets

    source = workspace_tmp_path / "raw_feature_bc_matrix.h5"
    _write_tenx(source)
    provenance = _provenance(source)
    output = workspace_tmp_path / "outputs"
    output.mkdir()

    def produce_call_rows(_source, destination, action, _runtime):
        assert _source == source
        assert action.lower == 5
        total = 300 if tampered else 30
        probability = 1 / (action.niters + 1)
        destination.write_text(
            "barcode,total_umi,p_value,fdr,limited,called\n"
            "AAAA-1,2,,,,FALSE\n"
            "CCCC-1,0,,,,FALSE\n"
            f"GGGG-1,{total},{probability},{probability},TRUE,TRUE\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(droplets, "_run_solver", produce_call_rows)
    result = droplets.run_droplet_action(
        source,
        output,
        provenance,
        expected_provenance_sha256=provenance.sha256(),
        action=droplets.EmptyDropsAction(lower=5),
        runtime=droplets.DropletRuntime("emptydrops", "sha256:" + "1" * 64, "1.32.0"),
    )
    assert hash_artifact(source)["sha256"] == provenance.source_sha256
    if tampered:
        assert result["status"] == "failed"
        assert result["reason"] == "droplet_output_validation_failed"
        assert not any(output.iterdir())
    else:
        assert result["status"] == "completed"
        assert set(result["artifacts"]) == {"cell_calls"}
        assert result["metrics"] == {
            "barcodes_tested": 1,
            "barcodes_called": 1,
            "monte_carlo_limited_barcodes": 1,
        }
        assert (output / "cell_calls.csv").is_file()


def test_cellbender_corrected_output_is_bound_and_quarantined(
    workspace_tmp_path, monkeypatch
):
    from devbench.core.expression_io import open_expression
    from rna_kernels import scrna_droplets as droplets

    source = workspace_tmp_path / "raw_feature_bc_matrix.h5"
    _write_tenx(source)
    provenance = _provenance(source)
    output = workspace_tmp_path / "outputs"
    output.mkdir()

    def produce_corrected_artifact(_source, destination, _action, _runtime):
        assert _source == source
        _write_tenx(destination, data=(1, 99, 20))

    monkeypatch.setattr(droplets, "_run_solver", produce_corrected_artifact)
    result = droplets.run_droplet_action(
        source,
        output,
        provenance,
        expected_provenance_sha256=provenance.sha256(),
        action=droplets.CellBenderAction(expected_cells=1, total_droplets_included=2),
        runtime=droplets.DropletRuntime("cellbender", "sha256:" + "1" * 64, "0.4.0"),
    )
    assert result["status"] == "completed"
    artifact = result["artifacts"]["corrected_expression"]
    corrected = output / artifact["filename"]
    assert artifact["sha256"] == hash_artifact(corrected)["sha256"]
    assert artifact["source_sha256"] == provenance.source_sha256
    assert artifact["action_sha256"] == result["action_sha256"]
    assert artifact["representation"] == "cellbender_corrected_tenx_h5"
    assert artifact["measurement_history"] == ["cellbender_corrected"]
    assert artifact["count1_admissible"] is False
    assert "admitted_research_inference" not in artifact["permitted_uses"]
    assert hash_artifact(source)["sha256"] == provenance.source_sha256

    relabelled = _provenance(corrected)
    with pytest.raises(ValueError, match="quarantined"):
        droplets.verify_raw_droplet_input(
            corrected,
            relabelled,
            expected_provenance_sha256=relabelled.sha256(),
        )
    with pytest.raises(ValueError, match="quarantined|corrected"):
        with open_expression(corrected, layer="counts"):
            pytest.fail("Legacy expression intake accepted a corrected count matrix")


def test_cellbender_stages_lossless_signed_counts_without_changing_raw_file(
    workspace_tmp_path,
):
    from rna_kernels.scrna_droplets import _stage_cellbender_input

    raw = workspace_tmp_path / "raw_feature_bc_matrix.h5"
    _write_tenx(raw, data=np.asarray([2, 99, 30], dtype=np.uint32))
    digest = hash_artifact(raw)["sha256"]
    staged = _stage_cellbender_input(raw, workspace_tmp_path)
    assert staged != raw
    with h5py.File(raw, "r") as original, h5py.File(staged, "r") as prepared:
        assert prepared["matrix/data"].dtype == np.dtype(np.int32)
        for key in ("data", "barcodes", "indices", "indptr", "shape", "features/id"):
            np.testing.assert_array_equal(
                prepared[f"matrix/{key}"][:],
                original[f"matrix/{key}"][:],
            )
    assert hash_artifact(raw)["sha256"] == digest


def _synthetic_cell_call_matrix(path, *, background_n, cells_n, ambient_per_cell=0):
    rng = np.random.default_rng(737)
    ambient = np.arange(1, 81) ** -1.35
    ambient /= ambient.sum()
    cellular = np.roll(ambient, 39)
    background = [
        rng.multinomial(total, ambient) for total in rng.integers(8, 60, background_n)
    ]
    cells = [
        rng.multinomial(total, cellular) + rng.multinomial(ambient_per_cell, ambient)
        for total in rng.integers(780, 1440, cells_n)
    ]
    counts = sparse.csr_matrix(np.asarray(background + cells, dtype=np.uint32))
    with h5py.File(path, "w") as handle:
        matrix = handle.create_group("matrix")
        matrix["shape"] = np.asarray(counts.shape[::-1], dtype=np.uint64)
        matrix["data"] = counts.data
        matrix["indices"] = counts.indices.astype(np.uint32)
        matrix["indptr"] = counts.indptr.astype(np.uint32)
        barcodes = [f"BC{i:06d}-1" for i in range(counts.shape[0])]
        matrix["barcodes"] = np.asarray(barcodes, dtype="S")
        features = matrix.create_group("features")
        for name in ("id", "name"):
            features[name] = np.asarray([f"g{i:03d}" for i in range(80)], dtype="S")
        features["feature_type"] = np.asarray(["Gene Expression"] * 80, dtype="S")
        features["genome"] = np.asarray(["synthetic_reference"] * 80, dtype="S")
    return counts


_RUN_DROPLET_ADAPTER = """
import json
import sys
from pathlib import Path
from rna_kernels.scrna_droplets import (
    CellBenderAction, DropletRuntime, EmptyDropsAction, RawDropletProvenance,
    run_droplet_action,
)
evidence, evidence_sha256, image, method, package_version, params = sys.argv[1:]
action = {'emptydrops': EmptyDropsAction, 'cellbender': CellBenderAction}[method]
result = run_droplet_action(
    Path('/inputs/raw_feature_bc_matrix.h5'), Path('/outputs'),
    RawDropletProvenance(**json.loads(evidence)),
    expected_provenance_sha256=evidence_sha256,
    action=action(**json.loads(params)),
    runtime=DropletRuntime(method, image, package_version),
)
print(json.dumps(result))
assert result['status'] == 'completed', result['reason']
"""


def _pinned_image():
    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if not image:
        pytest.skip("Set DEVBENCH_RNA_KERNEL_TEST_IMAGE to the pinned P3.1 OCI image")
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", image)
    return image


def _run_oci_adapter(base, image, *, method, version, options):
    from importlib.util import find_spec

    docker = shutil.which("docker")
    assert docker, "Pinned droplet smoke requires local Docker"
    source, output, drivers = base / "inputs", base / "outputs", base / "drivers"
    for folder in (output, drivers):
        folder.mkdir()
    path = source / "raw_feature_bc_matrix.h5"
    provenance = _provenance(path)

    # Bind only the source adapter for the test, so the locked R environment
    # can be verified against the current code before the final image is built.
    for relative in (
        "rna_kernels/__init__.py",
        "rna_kernels/canonical.py",
        "rna_kernels/scrna_droplets.py",
        "devharness_server/r/scrna_emptydrops.R",
    ):
        target = drivers / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        package, member = relative.split("/", 1)
        module = find_spec(package)
        assert module is not None and module.origin is not None
        shutil.copyfile(Path(module.origin).parent / member, target)

    command = [
        docker,
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--pids-limit=64",
        "--memory=2g",
        "--security-opt=no-new-privileges",
        "--tmpfs=/tmp:rw,nosuid,nodev,size=64m",
        "--env",
        "PYTHONPATH=/drivers:/opt/rna/python:/opt/rna/runtime",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        f"--user={os.getuid()}:{os.getgid()}",
        "--mount",
        f"type=bind,src={drivers},dst=/drivers,readonly",
        "--mount",
        f"type=bind,src={source},dst=/inputs,readonly",
        "--mount",
        f"type=bind,src={output},dst=/outputs",
        "--entrypoint",
        "python3.13",
        image,
        "-c",
        _RUN_DROPLET_ADAPTER,
        json.dumps(vars(provenance)),
        provenance.sha256(),
        image,
        method,
        version,
        json.dumps(options),
    ]
    run = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=300
    )
    assert run.returncode == 0, f"{run.stdout[-2048:]}\n{run.stderr[-2048:]}"
    result = json.loads((output / "summary.json").read_bytes())
    assert result["runtime"]["package_version"] == version
    assert result["runtime"]["image_id"] == image
    assert hash_artifact(path)["sha256"] == provenance.source_sha256
    return result


def test_emptydrops_actual_pinned_runtime(workspace_tmp_path):
    image = _pinned_image()
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir()
    _synthetic_cell_call_matrix(
        inputs / "raw_feature_bc_matrix.h5",
        background_n=140,
        cells_n=28,
    )
    result = _run_oci_adapter(
        workspace_tmp_path,
        image,
        method="emptydrops",
        version="1.32.1",
        options={"lower": 100, "niters": 999, "fdr": 0.005, "seed": 737},
    )
    assert result["metrics"]["barcodes_tested"] == 28
    assert result["metrics"]["barcodes_called"] == 28


def test_cellbender_actual_pinned_runtime(workspace_tmp_path):
    image = _pinned_image()
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir()
    original = _synthetic_cell_call_matrix(
        inputs / "raw_feature_bc_matrix.h5",
        background_n=1_200,
        cells_n=160,
        ambient_per_cell=240,
    )
    result = _run_oci_adapter(
        workspace_tmp_path,
        image,
        method="cellbender",
        version="0.4.0",
        options={"expected_cells": 160, "total_droplets_included": 700, "fpr": 0.01},
    )
    artifact = result["artifacts"]["corrected_expression"]
    assert artifact["count1_admissible"] is False
    corrected = workspace_tmp_path / "outputs" / artifact["filename"]
    assert hash_artifact(corrected)["sha256"] == artifact["sha256"]
    with h5py.File(corrected, "r") as handle:
        matrix = handle["matrix"]
        denoised = sparse.csr_matrix(
            (matrix["data"][:], matrix["indices"][:], matrix["indptr"][:]),
            shape=original.shape,
        )
    # Seeded ambient contaminants are enriched in the first 8 features, and
    # the dominant biological signal lies in the shifted features 39 to 46.
    assert denoised[1200:, :8].sum() < original[1200:, :8].sum()
    assert denoised[1200:, 39:47].sum() >= 0.90 * original[1200:, 39:47].sum()
