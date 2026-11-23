"""Independent-unit admission and receipt custody for Bayesian composition."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from devbench.core.scrna_composition import (
    CompositionInput,
    CompositionSample,
    CompositionSpec,
    ScCODARuntime,
    read_composition_receipt,
    run_sccoda,
    write_composition_receipt,
)
from devharness_server.tools_scrna_composition import (
    COMPOSITION_OPERATION,
    CompositionRequest,
    run_mounted_composition,
)
from rna_kernels.canonical import canonical_json
from rna_kernels.scrna_upstream_contract import SUMMARY_FORMAT


def _input() -> CompositionInput:
    observed = (
        (55, 31, 14),
        (57, 28, 15),
        (51, 34, 15),
        (53, 32, 15),
        (46, 49, 5),
        (49, 43, 8),
        (44, 45, 11),
        (47, 46, 7),
    )
    return CompositionInput(
        source_sha256="1" * 64,
        cell_label_source_sha256="2" * 64,
        biological_unit_mapping_sha256="3" * 64,
        biological_unit="donor",
        cell_types=("reference", "state A", "state B"),
        samples=tuple(
            CompositionSample(
                biological_unit_id=f"donor-{index}",
                condition="vehicle" if index < 4 else "treated",
                cells_observed=sum(counts),
                counts=counts,
            )
            for index, counts in enumerate(observed)
        ),
    )


def _spec() -> CompositionSpec:
    return CompositionSpec(
        reference_cell_type="reference",
        baseline_condition="vehicle",
        target_est_fdr=0.05,
        seed=1989,
    )


def _runtime(image: str) -> ScCODARuntime:
    return ScCODARuntime(
        image_id=image,
        jax_version="0.12.0.1",
        jaxlib_version="0.12.0.1",
        arviz_version="1.3.0",
        anndata_version="0.12.19",
    )


def test_validated_composition_keeps_bayesian_scope_when_runtime_is_unavailable():
    result = run_sccoda(_input(), _spec())
    report = result.model_dump(mode="json")
    assert report["status"] == "unavailable"
    assert report["reason"] == "scCODA_runtime_unavailable"
    assert report["condition_biological_units"] == {"treated": 4, "vehicle": 4}
    assert report["reference_cell_type"] == "reference"
    assert report["target_est_fdr"] == 0.05
    assert report["est_fdr"] is None
    assert report["effects"] == []
    assert "relative" in report["closure_note"]
    assert "gene_statistics" not in report


def test_repeated_donor_is_refused_across_conditions():
    data = _input()
    duplicate = data.samples[-1].model_copy(
        update={"biological_unit_id": data.samples[0].biological_unit_id}
    )
    changed = data.model_copy(update={"samples": (*data.samples[:-1], duplicate)})
    result = run_sccoda(changed, _spec())
    assert result.status == "refused"
    assert result.reason == "repeated_biological_unit"
    assert not result.effects


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"counts": (50, 50)}, "incomplete_cell_type_counts"),
        ({"counts": (50, 25, 24)}, "cell_count_closure_mismatch"),
    ],
)
def test_missing_and_unaccounted_cells_are_refused(change, reason):
    data = _input()
    changed_row = data.samples[0].model_copy(update=change)
    changed = data.model_copy(update={"samples": (changed_row, *data.samples[1:])})
    result = run_sccoda(changed, _spec())
    assert result.status == "refused"
    assert result.reason == reason
    assert not result.effects


def test_measured_zero_is_counted_separately_from_missing_cells():
    data = _input()
    observed_zero = data.samples[0].model_copy(update={"counts": (55, 45, 0)})
    changed = data.model_copy(update={"samples": (observed_zero, *data.samples[1:])})
    result = run_sccoda(changed, _spec())
    assert result.status == "unavailable"
    assert result.measured_zero_count == 1
    assert result.zero_replacement is None


def test_low_replication_and_unmatched_reference_fail_before_runtime():
    data = _input()
    small = data.model_copy(update={"samples": (*data.samples[:2], *data.samples[4:])})
    assert run_sccoda(small, _spec()).reason == "insufficient_biological_units"
    unmatched = _spec().model_copy(update={"reference_cell_type": "UNKNOWN"})
    assert run_sccoda(data, unmatched).reason == "reference_cell_type_missing"


def test_exact_receipt_matches_input_specification_and_hash(workspace_tmp_path):
    data, spec = _input(), _spec()
    result = run_sccoda(data, spec)
    path = workspace_tmp_path / "composition_result.json"
    digest = write_composition_receipt(path, result)
    assert read_composition_receipt(path, digest, data=data, spec=spec) == result
    changed = data.model_copy(update={"source_sha256": "4" * 64})
    with pytest.raises(ValueError, match="composition input"):
        read_composition_receipt(path, digest, data=changed, spec=spec)
    with pytest.raises(ValueError, match="composition specification"):
        read_composition_receipt(
            path, digest, data=data, spec=spec.model_copy(update={"seed": 2})
        )
    raw = path.read_bytes().replace(b"relative", b"absolute")
    path.unlink()
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="receipt digest"):
        read_composition_receipt(path, digest, data=data, spec=spec)
    with pytest.raises(ValueError, match="closure_note"):
        read_composition_receipt(
            path, hashlib.sha256(raw).hexdigest(), data=data, spec=spec
        )


def test_h5ad_staging_uses_all_observed_labels_and_preserves_a_measured_zero(
    workspace_tmp_path,
):
    import anndata
    import numpy as np
    import pandas as pd

    from devbench.core.scrna_composition import (
        CompositionObservationSpec,
        composition_from_h5ad,
    )
    from devbench.core.scrna_qc_selection import AcceptedQCSelection, QCSelectionBinding

    obs = pd.DataFrame(
        {
            "donor": ["u2", "u1", "u1", "u2", "u3"],
            "condition": ["test", "control", "control", "test", "test"],
            "type": ["B", "A", "A", "A", "A"],
        },
        index=["c5", "c1", "c2", "c4", "c3"],
    )
    path = workspace_tmp_path / "annotated.h5ad"
    anndata.AnnData(np.zeros((len(obs), 1)), obs=obs).write_h5ad(path)
    source_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    declaration = CompositionObservationSpec(
        source_sha256=source_digest,
        biological_unit="donor",
        biological_unit_column="donor",
        condition_column="condition",
        cell_type_column="type",
    )

    data = composition_from_h5ad(path, declaration)
    assert data.cell_types == ("A", "B")
    assert [sample.counts for sample in data.samples] == [(2, 0), (1, 1), (1, 0)]
    assert [sample.cells_observed for sample in data.samples] == [2, 2, 1]
    assert data.source_sha256 == source_digest
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source_digest

    with pytest.raises(ValueError, match="hash mismatch"):
        composition_from_h5ad(
            path, declaration.model_copy(update={"source_sha256": "0" * 64})
        )

    binding = QCSelectionBinding(
        lineage_id="fixture", step_id="qc", generation=1, receipt_sha256="a" * 64,
        source_sha256=source_digest, metrics_sha256="b" * 64,
    )
    ids = tuple(obs.index)
    selected = AcceptedQCSelection(binding, ids, (True, True, False, True, True))
    filtered = composition_from_h5ad(path, declaration, qc_selection=selected)
    assert [sample.counts for sample in filtered.samples] == [(1, 0), (1, 1), (1, 0)]
    assert (
        filtered.biological_unit_mapping_sha256 != data.biological_unit_mapping_sha256
    )
    missing = AcceptedQCSelection(binding, ids, (True, True, True, True, False))
    with pytest.raises(
        ValueError, match="removed every cell from a composition profile",
    ):
        composition_from_h5ad(path, declaration, qc_selection=missing)


def _stage_request(
    inputs: Path,
    data: CompositionInput,
    spec: CompositionSpec,
    runtime: ScCODARuntime,
    *,
    canonical: bool = True,
) -> str:
    document = canonical_json(data.model_dump(mode="json"))
    if not canonical:
        document += b"\n"
    parameters = canonical_json(spec.model_dump(mode="json"))
    request = CompositionRequest(
        input_sha256=hashlib.sha256(document).hexdigest(),
        spec_sha256=hashlib.sha256(parameters).hexdigest(),
        runtime=runtime,
    )
    (inputs / "composition_input.json").write_bytes(document)
    (inputs / "composition_spec.json").write_bytes(parameters)
    body = canonical_json(request.model_dump(mode="json"))
    (inputs / "request.json").write_bytes(body)
    return hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize("changed", ["raw_bytes", "noncanonical"])
def test_mounted_composition_refuses_changed_input_before_writing(
    workspace_tmp_path,
    changed,
):
    inputs, outputs = workspace_tmp_path / "inputs", workspace_tmp_path / "outputs"
    inputs.mkdir()
    outputs.mkdir()
    digest = _stage_request(
        inputs,
        _input(),
        _spec(),
        _runtime("sha256:" + "b" * 64),
        canonical=changed != "noncanonical",
    )
    if changed == "raw_bytes":
        (inputs / "composition_spec.json").write_bytes(b"{}")
    with pytest.raises(
        ValueError, match="input digest mismatch|canonical byte encoding"
    ):
        run_mounted_composition(inputs, outputs, digest)
    assert not list(outputs.iterdir())


def test_mounted_refusal_keeps_bounded_reason_and_observed_support(workspace_tmp_path):
    inputs, outputs = workspace_tmp_path / "inputs", workspace_tmp_path / "outputs"
    inputs.mkdir()
    outputs.mkdir()
    data = _input()
    data = data.model_copy(update={"samples": (*data.samples[:2], *data.samples[4:])})
    spec, runtime = _spec(), _runtime("sha256:" + "b" * 64)
    digest = _stage_request(inputs, data, spec, runtime)
    result = run_mounted_composition(inputs, outputs, digest)
    summary = json.loads((outputs / "summary.json").read_text())
    assert summary["format"] == SUMMARY_FORMAT
    assert summary["operation"] == COMPOSITION_OPERATION
    assert summary["request_sha256"] == digest
    assert summary["status"] == result.status == "refused"
    assert summary["reason"] == "insufficient_biological_units"
    assert set(summary["output_sha256"]) == {"composition_result.json"}
    retained = read_composition_receipt(
        outputs / "composition_result.json",
        summary["output_sha256"]["composition_result.json"],
        data=data,
        spec=spec,
        runtime=runtime,
    )
    assert retained == result


def _planted_input() -> CompositionInput:
    """A frozen two-state Dirichlet-multinomial engineering fixture."""
    import numpy as np

    rng = np.random.default_rng(119)
    samples = []
    concentrations = {"vehicle": (18, 4), "treated": (18, 22)}
    for group, concentration in concentrations.items():
        for idx in range(12):
            fractions = rng.dirichlet(concentration)
            counts = tuple(map(int, rng.multinomial(120 + 6 * idx, fractions)))
            samples.append(
                CompositionSample(
                    biological_unit_id=f"{group}-{idx}",
                    condition=group,
                    counts=counts,
                    cells_observed=sum(counts),
                )
            )
    return _input().model_copy(
        update={"cell_types": ("reference", "state A"), "samples": tuple(samples)}
    )


def test_mounted_sccoda_recovers_planted_relative_compositional_effect(
    workspace_tmp_path,
):
    from devharness_controlled import native_contracts

    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if image is None or image not in native_contracts.sccoda_kernel_images():
        pytest.skip("Select the pinned scCODA image via DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    platform = native_contracts.kernel_test_platform(image)
    assert platform is not None
    inputs, outputs = workspace_tmp_path / "inputs", workspace_tmp_path / "outputs"
    inputs.mkdir()
    outputs.mkdir()
    data = _planted_input()
    spec = _spec().model_copy(
        update={
            "num_samples": 450,
            "num_warmup": 700,
            "target_accept_probability": 0.97,
        }
    )
    runtime = _runtime(image)
    request = _stage_request(inputs, data, spec, runtime)
    uid, gid = os.geteuid(), os.getegid()
    if uid == 0:
        uid = gid = 65532
        os.chown(outputs, uid, gid)
        inputs.chmod(0o755)
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            f"--platform={platform}",
            "--network=none",
            "--read-only",
            "--security-opt=no-new-privileges",
            "--cap-drop=all",
            "--pids-limit=64",
            "--memory=2048m",
            "--memory-swap=2048m",
            "--cpus=2",
            "--ipc=private",
            "--user",
            f"{uid}:{gid}",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,mode=1777,size=67108864",
            "--env",
            "JAX_PLATFORMS=cpu",
            "--mount",
            f"type=bind,src={inputs},dst=/inputs,readonly",
            "--mount",
            f"type=bind,src={outputs},dst=/outputs",
            "--entrypoint",
            "/usr/local/bin/python3.13",
            image,
            "-P",
            "-m",
            "devharness_server.tools_scrna_composition",
            request,
        ],
        capture_output=True,
        check=True,
        timeout=300,
    )
    path = outputs / "composition_result.json"
    summary = json.loads((outputs / "summary.json").read_text())
    assert summary["request_sha256"] == request
    result = read_composition_receipt(
        path,
        summary["output_sha256"][path.name],
        data=data,
        spec=spec,
        runtime=runtime,
    )
    assert result.status == "succeeded", (result.reason, result.sampling)
    assert summary["status"] == "completed", summary
    assert result.est_fdr is not None and result.est_fdr <= spec.target_est_fdr
    effects = {row.cell_type: row for row in result.effects}
    assert set(effects) == {"state A"}
    assert effects["state A"].credible_effect
    coefficient = effects["state A"].relative_log_coefficient
    assert coefficient is not None and coefficient > 0
