"""Exercise the L1 forensic hold and frozen-brain QC receipts via the public tools."""

import json
import time
from types import SimpleNamespace

import anndata as ad
import numpy as np
import pandas as pd
import pytest

from devbench.daemon.supervisor import Supervisor
from devbench.mcp.context import ControlPlane
from devbench.mcp.registry import pins
from devbench.mcp.server import dispatch
from devbench.platform.plugin_jobs import JobStore
from tests.test_donor_forensics import _cohort
from tests.test_mcp_13_tool_control_plane import _ok
from tests.test_research_daemon import _admission, _audit_plan

pytestmark = pytest.mark.wal


@pytest.fixture
def plane(workspace_tmp_path):
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir()
    jobs = JobStore(workspace_tmp_path / "workspace")
    supervisor = Supervisor(jobs.workspace, offline_only=True)
    ctx = ControlPlane(
        SimpleNamespace(jobs=jobs, input_root=inputs, mounted_image_id=None),
        supervisor,
        {"offline_only": True},
    )
    supervisor.start()
    try:
        yield ctx
    finally:
        supervisor.close()


@pytest.mark.parametrize("fmt", ["h5ad", "zarr"])
@pytest.mark.parametrize(
    ("probe", "reason"),
    [
        ("sex_swap", "sex_chromosome_discordance"),
        ("duplicate", "near_duplicate_donor_profiles"),
        ("integer_log", "suspected_transformed_integer_counts"),
    ],
)
def test_intake_holds_each_forensic_probe_before_e5(plane, fmt, probe, reason):
    path = plane.input_root / ("probe." + fmt)
    data = _cohort(probe)
    (data.write_zarr if fmt == "zarr" else data.write_h5ad)(path)
    request = _admission(path)
    entry = dict(request["input"])
    del entry["investigation_role"]
    result = _ok(plane, "inspect_inputs", {"inputs": [entry]})["inputs"][0]
    forensic = result["donor_forensics"]
    assert forensic["status"] == "conflict_halted" and reason in forensic["findings"]
    assert forensic["forensic_evidence_level"] == "expression_only_L1"
    reg = _ok(plane, "register_investigation_input", request)
    decision = _ok(
        plane,
        "check_answerability",
        {
            "session_id": reg["session_id"],
            "question_text": "Estimate donor-level effects",
            "axis_b_class": "B1",
            "assay": "snrna_seq",
            "entry_point": "open",
            "artifact_ids": [reg["artifact"]["artifact_id"]],
        },
    )
    assert decision["epistemic_state"] == "E_ABSTAIN"
    plan = dict(_audit_plan(reg), epistemic_purpose="confirmatory")
    blocked = dispatch(plane, "freeze_analysis_plan", plan)
    assert (
        blocked.isError
        and blocked.structuredContent["category"] == "scientific_refusal"
    )


def test_descriptor_bound_zarr_v3_donor_hold(plane):
    from scipy import sparse

    from tests.test_streaming_expression_formats import _v3, _v3_array

    path = plane.input_root / "cohort.zarr"
    cohort = _cohort("duplicate")
    _v3(path, sparse.csr_matrix(cohort.X), "csr")
    obs = path / "obs" / "zarr.json"
    meta = json.loads(obs.read_bytes())
    meta["attributes"]["column-order"] = ["donor"]
    obs.write_text(json.dumps(meta))
    _v3_array(path / "obs/donor", cohort.obs.donor_id, string=True)
    entry = _admission(path)["input"]
    del entry["investigation_role"]
    result = _ok(plane, "inspect_inputs", {"inputs": [entry]})["inputs"][0]
    assert result["donor_forensics"]["status"] == "conflict_halted"
    assert "near_duplicate_donor_profiles" in result["donor_forensics"]["findings"]


def _brain():
    rng = np.random.default_rng(150)
    n = 480
    donor = np.repeat(np.arange(6), n // 6)
    cluster = np.tile(np.repeat(["glia", "neuronal"], 40), 6)
    obs = pd.DataFrame(
        {
            "donor": ["d" + str(i) for i in donor],
            "condition": np.where(donor < 3, "control", "case"),
            "cluster": cluster,
            "capture": np.where(donor % 2, "b", "a"),
        },
        index=[f"bc{i}" for i in range(n)],
    )
    means = rng.uniform(0.2, 6, (n, 80))
    means[cluster == "glia", :20] += 15
    means[cluster == "neuronal", 20:40] += 19
    counts = rng.poisson(means)
    nascent = rng.binomial(counts, 0.65)
    return ad.AnnData(
        counts,
        obs=obs,
        var=pd.DataFrame(index=[f"G{i}" for i in range(80)]),
        layers={"unspliced": nascent, "spliced": counts - nascent},
    )


def _plan(reg, name, params=None):
    request = _audit_plan(reg)
    request["kernel"] = {"kernel_id": name, "version": "1", "parameters": params or {}}
    return request


def test_filtered_only_named_ambient_methods_refused_on_freeze(plane):
    path = plane.input_root / "brain.h5ad"
    _brain().write_h5ad(path)
    reg = _ok(plane, "register_investigation_input", _admission(path))
    for method in ("cellbender", "soupx"):
        packet = dispatch(
            plane,
            "freeze_analysis_plan",
            _plan(reg, "scrna_ambient_decontamination", {"requested_method": method}),
        )
        assert (
            packet.isError
            and packet.structuredContent["category"] == "missing_raw_droplet_matrix"
        )
    plan = _plan(reg, "scrna_ambient_decontamination")
    packet = dispatch(
        plane, "freeze_analysis_plan", dict(plan, epistemic_purpose="confirmatory")
    )
    assert (
        packet.isError and packet.structuredContent["category"] == "scientific_refusal"
    )


def _run(ctx, plan):
    frozen = _ok(ctx, "freeze_analysis_plan", plan)
    identity = {k: frozen[k] for k in ("session_id", "plan_id", "plan_sha256")}
    submitted = _ok(ctx, "submit_reviewed_run", identity)
    key = {"run_id": submitted["run_id"], "session_id": frozen["session_id"]}
    until = time.monotonic() + 240
    while time.monotonic() < until:
        status = _ok(ctx, "get_run_status", key)
        if status["status"] not in {"queued", "running"}:
            assert status["status"] == "completed", (
                status,
                ctx.jobs.managed_run(key["run_id"])["error_text"],
            )
            break
        time.sleep(0.2)
    else:
        pytest.fail("Synthetic M2 worker exceeded the execution deadline")
    receipt = _ok(ctx, "get_run_receipt", key)
    assert receipt["clinical_release_allowed"] is False
    assert receipt["claim_tier"] == "L1_execution_verified"
    return receipt


def _result(ctx, reg, receipt, port="result"):
    from pathlib import Path

    ref = receipt["outputs"][port]
    binding = ctx.artifact(reg["session_id"], ref["artifact_id"], ref["sha256"])
    return Path(binding["path"])


def test_frozen_brain_cohort_on_pinned_m2_runtime(plane):
    capabilities = _ok(
        plane, "inspect_runtime_capabilities", {"include_packages": False}
    )
    live = {item["image_id"] for item in capabilities["available_images"]}
    if not live.intersection(pins.M2_QC_IMAGES.values()):
        pytest.skip("Pinned M2 single-cell image unavailable on this host")
    data = _brain()
    brain = plane.input_root / "brain.h5ad"
    data.write_h5ad(brain)
    reg = _ok(plane, "register_investigation_input", _admission(brain))
    donor = _run(plane, _plan(reg, "donor_forensics"))
    a1 = json.loads(_result(plane, reg, donor).read_bytes())
    assert (
        a1["status"] == "warning" and "sex_chromosome_not_evaluable" in a1["findings"]
    )
    assert donor["outputs"]["plot_sidecar"]["kind"] == "plot_sidecar"
    qc = _run(plane, _plan(reg, "snrna_droplet_qc"))
    result = json.loads(_result(plane, reg, qc).read_bytes())
    assert "mitochondrial_annotation_unavailable" in result["warnings"]
    metrics = pd.read_parquet(_result(plane, reg, qc, "metrics"))
    np.testing.assert_allclose(
        metrics.nuclear_fraction, data.layers["unspliced"].sum(1) / data.X.sum(1)
    )
    assert metrics.pct_counts_mt.isna().all()
    assert qc["outputs"]["plot_sidecar"]["kind"] == "plot_sidecar"
    doublets = _plan(reg, "scrna_doublets", {"expected_rates": {"a": 0.05, "b": 0.08}})
    doublets["input_bindings"]["qc_metrics"] = {
        k: qc["outputs"]["metrics"][k] for k in ("artifact_id", "sha256")
    }
    called = _run(plane, doublets)
    detector = json.loads(_result(plane, reg, called).read_bytes())
    assert len(detector["batches"]) == 2
    assert all(
        b["method"] == "scdblfinder" and b["assessed"] > 0 for b in detector["batches"]
    )
    assert "predicted_doublet" in pd.read_parquet(
        _result(plane, reg, called, "metrics")
    )

    settings = {"modality": "snrna_seq", "tissue": "brain"}
    ambient = _plan(reg, "scrna_ambient_decontamination", settings)
    ambient["epistemic_purpose"] = "sensitivity_probe"
    filtered = _run(plane, ambient)
    raw_path = plane.input_root / "unfiltered.h5ad"
    empty = data[:64].copy()
    empty.obs_names = [f"empty{i}" for i in range(len(empty))]
    empty.obs["capture"] = np.resize(["a", "b"], len(empty))
    empty.X = np.random.default_rng(29).poisson(0.7, empty.shape)
    ad.concat([data, empty]).write_h5ad(raw_path)
    request = _admission(raw_path)
    request.update(
        session_id=reg["session_id"], representation_class="raw_reads_or_droplets"
    )
    request["provenance_declaration"]["raw_unfiltered_droplets"] = True
    raw = _ok(plane, "register_investigation_input", request)
    ambient["input_bindings"]["raw_droplets"] = {
        k: raw["artifact"][k] for k in ("artifact_id", "sha256")
    }
    background = _run(plane, ambient)
    for receipt, regime in (
        (filtered, "r1_filtered_only_decontx_heuristic"),
        (background, "r0_cpu_soupx_decontx"),
    ):
        evidence = json.loads(_result(plane, reg, receipt).read_bytes())
        assert evidence["sensitivity"]["purpose"] == "E3"
        assert evidence["sensitivity"]["status"] == "completed"
        assert evidence["sensitivity_required"] is True
        assert evidence["r1_primary"]["status"] == "completed"
        assert evidence["r1_sha256"] == reg["artifact"]["sha256"]
        effects = pd.read_parquet(_result(plane, reg, receipt, "metrics"))
        provenance = effects.attrs["ambient_provenance"]
        assert provenance["r1_sha256"] == reg["artifact"]["sha256"]
        sidecar = json.loads(_result(plane, reg, receipt, "plot_sidecar").read_bytes())
        assert sidecar["ambient_provenance"] == provenance
        assert len(effects) == data.n_vars
        assert (
            np.isfinite(
                effects[["raw_log2_cpm_difference", "adjusted_log2_cpm_difference"]]
            )
            .all()
            .all()
        )
        assert evidence["decontamination_regime"] == regime
        assert (
            receipt["outputs"]["adjusted"]["representation_class"]
            == "ambient_adjusted_counts"
        )
        assert reg["artifact"]["representation_class"] == "count_1_raw_counts"
        assert all(
            k in evidence
            for k in (
                "ambient_method",
                "input_matrix_tier",
                "hardware_backend",
                "cellbender_version",
                "cellbender_deferred_reason",
            )
        )


def test_genotype_header_keeps_clinical_evidence_off_the_wire(plane):
    vcf = plane.input_root / "genotypes.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tpatient_99\n"
    )
    entry = _admission(vcf, "public")
    entry["input"].update(file_kind="reference", investigation_role="genotype_panel")
    entry["representation_class"] = "reference_only"
    reg = _ok(plane, "register_investigation_input", entry)
    assert reg["private_tainted"] and reg["artifact"] is None
    assert "patient_99" not in json.dumps(reg)


def test_explicit_gpu_plan_selects_only_a_qualified_cuda_pin(monkeypatch):
    from dataclasses import replace

    from devbench.mcp import registry

    cpu, cuda = "sha256:" + "1" * 64, "sha256:" + "2" * 64
    name = "scrna_ambient_decontamination"
    kernel = replace(registry.catalog()[name], images=frozenset({cpu, cuda}))
    monkeypatch.setattr(registry, "catalog", lambda: {name: kernel})
    monkeypatch.setattr(registry, "_live_images", lambda *_: {cpu, cuda})
    monkeypatch.setattr(pins, "M2_CUDA_IMAGES", {"linux/amd64": cuda})
    ctx = SimpleNamespace(plugin=SimpleNamespace(mounted_image_id=None))
    assert registry.select_image(ctx, name, require_gpu=True) == cuda
    ctx.plugin.mounted_image_id = cpu
    assert registry.select_image(ctx, name, require_gpu=True) is None


def _write_unevaluable_cohort(path, case, logged, *, fractional_log1p=False):
    data = _cohort("integer_log" if logged else None)
    if fractional_log1p:
        data.X = np.log1p(data.X.astype(float))
        data.uns["log1p"] = {"base": None}
    if case == "donor_limit":
        rows = np.arange(257) % data.n_obs
        data = ad.AnnData(
            X=data.X[rows],
            var=data.var.copy(),
            obs=pd.DataFrame(
                {"donor_id": [f"d{i}" for i in range(len(rows))]},
                index=[f"c{i}" for i in range(len(rows))],
            ),
        )
    elif case == "duplicate_features":
        data.var_names = ["gene"] * data.n_vars
    elif case == "duplicate_cells":
        data.obs_names = ["cell"] * data.n_obs

    if path.suffix == ".h5ad":
        data.write_h5ad(path)
        if case == "structured_obs":
            import h5py

            records = np.rec.fromarrays(
                [data.obs_names.to_numpy(dtype="S8"), data.obs.donor_id.to_numpy("S8")],
                names="_index,donor_id",
            )
            with h5py.File(path, "r+") as handle:
                del handle["obs"]
                handle["obs"] = records
    else:
        data.write_zarr(path)
        if case == "axis_limit":
            import zarr

            # Valid fill-only column exceeds the decoded axis budget without I/O.
            var = zarr.open_group(str(path), mode="r+")["var"]
            labels = var.create_dataset(
                "annotation",
                shape=(data.n_vars,),
                dtype="U450000",
                chunks=(1,),
                fill_value="",
            )
            labels.attrs.update(
                {"encoding-type": "string-array", "encoding-version": "0.2.0"}
            )
            var.attrs["column-order"] = [*var.attrs["column-order"], "annotation"]


@pytest.mark.parametrize(
    ("fmt", "case", "logged"),
    [
        ("h5ad", "donor_limit", False),
        ("zarr", "donor_limit", False),
        ("h5ad", "duplicate_features", False),
        ("h5ad", "duplicate_cells", False),
        ("h5ad", "structured_obs", False),
        ("zarr", "axis_limit", False),
        ("h5ad", "donor_limit", True),
        ("h5ad", "duplicate_features", True),
        ("h5ad", "structured_obs", True),
        ("zarr", "axis_limit", True),
    ],
)
def test_public_intake_unevaluable_donor_identity(plane, fmt, case, logged):
    from devbench.core.donor_forensics import CAVEAT, SUSPECTED_TRANSFORM

    path = plane.input_root / f"cohort.{fmt}"
    _write_unevaluable_cohort(path, case, logged)
    request = _admission(path, "public")
    entry = dict(request["input"])
    del entry["investigation_role"]
    inspected = _ok(plane, "inspect_inputs", {"inputs": [entry]})["inputs"][0]
    reg = _ok(plane, "register_investigation_input", request)
    artifact = reg["artifact"]
    detail = plane.artifact(
        reg["session_id"], artifact["artifact_id"], artifact["sha256"]
    )["detail"]
    expected = {
        "status": "conflict_halted" if logged else "warning",
        "findings": ["donor_identity_not_evaluable"]
        + ([SUSPECTED_TRANSFORM] if logged else []),
        "forensic_evidence_level": "expression_only_L1",
        "caveat": CAVEAT,
    }
    assert inspected["count_1_forensics"]["suspected_transformed"] is logged
    assert inspected["donor_forensics"] == detail["donor_forensics"] == expected
    assert detail["count_1_verified"] is (not logged)
    if logged:
        blocked = dispatch(
            plane,
            "freeze_analysis_plan",
            dict(_audit_plan(reg), epistemic_purpose="confirmatory"),
        )
        assert (
            blocked.isError
            and blocked.structuredContent["category"] == "scientific_refusal"
        )


@pytest.mark.parametrize(
    ("fmt", "case", "finding"),
    [
        ("h5ad", None, "count_screen_not_evaluable"),
        ("zarr", "axis_limit", "donor_identity_not_evaluable"),
    ],
)
def test_fractional_log1p_does_not_hold_donor_inference(plane, fmt, case, finding):
    path = plane.input_root / f"normalized.{fmt}"
    _write_unevaluable_cohort(path, case, False, fractional_log1p=True)
    request = _admission(path, "public")
    request["representation_class"] = "size_factor_normalized"
    request["provenance_declaration"]["count_provenance"] = "fractional log1p"
    entry = dict(request["input"])
    del entry["investigation_role"]
    inspected = _ok(plane, "inspect_inputs", {"inputs": [entry]})["inputs"][0]
    reg = _ok(plane, "register_investigation_input", request)
    artifact = reg["artifact"]
    detail = plane.artifact(
        reg["session_id"], artifact["artifact_id"], artifact["sha256"]
    )["detail"]

    counts = inspected["count_1_forensics"]
    assert counts["suspected_transformed"] and counts["nonnegative"]
    assert not counts["integer_valued"]
    forensic = inspected["donor_forensics"]
    assert forensic == detail["donor_forensics"]
    assert forensic["status"] == "warning" and forensic["findings"] == [finding]
    assert not detail.get("donor_conflict_halted")
    assert not detail["count_1_verified"]

    decision = _ok(
        plane,
        "check_answerability",
        {
            "session_id": reg["session_id"],
            "question_text": "Compare normalized expression across donors",
            "axis_b_class": "B1",
            "assay": "bulk_short_read",
            "entry_point": "open",
            "artifact_ids": [artifact["artifact_id"]],
        },
    )
    assert decision["epistemic_state"] != "E_ABSTAIN"
