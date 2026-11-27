"""Exercise schema discovery and the live mounted CAMERA input/custody adapter."""

import gzip
import hashlib
import json
import os
from types import SimpleNamespace

import numpy as np
import pytest

from devbench.engine.workflow_operations import OperationBlocked
from devbench.mcp import registry, runtime
from devbench.mcp.registry_execution import _camera, _table_rows, execute_mounted_kernel
from devbench.mcp.schemas import validator


def test_registry_projects_observed_availability_and_filters(monkeypatch):
    image = sorted(registry.pins.pathway_kernel_images())[0]
    ctx = SimpleNamespace(plugin=SimpleNamespace(mounted_image_id=image))
    monkeypatch.setattr(registry, "workspace_kernels", lambda *_: {})
    monkeypatch.setattr(runtime, "available_images", lambda _, **kwargs: [])
    args = {"lane": "tier1_curated", "axis_b_class": "B5", "assay": "bulk"}
    unavailable = registry.list_kernels_and_methods(ctx, args)
    assert validator("list_kernels_and_methods", output=True).is_valid(unavailable)
    assert unavailable["tier2_base_images"] == []
    assert {k["kernel_id"] for k in unavailable["kernels"]} == {
        "rna_pathway_camera",
        "network_regulon_enrichment",
        "network_motif_enrichment",
    }
    assert all(
        not k["runtime_ready"] and k["parameter_schema"] is None
        for k in unavailable["kernels"]
    )
    monkeypatch.setattr(
        runtime, "available_images", lambda _, **kwargs: [{"image_id": image}]
    )
    result = registry.list_kernels_and_methods(
        ctx,
        {
            **args,
            "kernel_ids": ["rna_pathway_camera"],
            "include_parameter_schema": True,
        },
    )
    (camera,) = result["kernels"]
    assert (
        camera["runtime_ready"]
        and camera["parameter_schema"]["additionalProperties"] is False
    )
    assert camera["calibration_status"] == "uncalibrated_L1"
    assert (
        registry.resolve_kernel(ctx, camera["kernel_id"], camera["version"]) == camera
    )


def _bind(path, representation):
    return {
        "ref": {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "representation_class": representation,
            "format": path.suffix[1:],
        },
        "path": path,
    }


@pytest.mark.wal
@pytest.mark.parametrize(("fmt", "delimiter"), [("csv", ","), ("tsv", "\t")])
def test_table_rows_reads_registered_gzip_with_decompressed_limit(
    workspace_tmp_path, fmt, delimiter
):
    from devbench.mcp.context import ControlPlane
    from devbench.platform.plugin_jobs import JobStore
    from tests.test_mcp_intake_query import register

    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir()
    ctx = ControlPlane(
        SimpleNamespace(input_root=inputs, jobs=JobStore(workspace_tmp_path / "work"))
    )
    path = inputs / f"metadata.{fmt}.gz"
    rows = [["group", "label"], *([["A", "α" * 20]] * 50)]
    raw = "".join(delimiter.join(row) + "\r\n" for row in rows).encode("utf-8-sig")
    path.write_bytes(gzip.compress(raw))
    admitted = register(ctx, path)
    ref = admitted["artifact"]
    assert ref["format"] == fmt
    binding = ctx.artifact(admitted["session_id"], ref["artifact_id"], ref["sha256"])
    assert list(_table_rows(binding, len(raw))) == rows
    assert path.stat().st_size < len(raw) - 1
    with pytest.raises(OperationBlocked, match="resource_quota_exceeded"):
        list(_table_rows(binding, len(raw) - 1))


def _inputs(root):
    rng = np.random.default_rng(219)
    samples = [f"s{n}" for n in range(12)]
    genes = [f"g{n}" for n in range(36)]
    raw = rng.normal(5, 1, (len(genes), len(samples)))
    raw[:10, 6:] += 2
    table = root / "expression.csv"
    lines = [",".join(["gene", *samples])]
    lines.extend(
        ",".join([gene, *map(str, data)]) for gene, data in zip(genes, raw, strict=True)
    )
    table.write_text("\n".join(lines) + "\n")
    metadata = root / "metadata.csv"
    metadata.write_text(
        "sample_id,unit,condition\n"
        + "".join(
            f"{name},donor-{i},{'a' if i < 6 else 'b'}\n"
            for i, name in enumerate(samples)
        )
    )
    ref = root / "collection.json"
    ref.write_text(json.dumps({"signal": genes[:10], "rest": genes[10:]}))
    return (
        {
            "expression": _bind(table, "size_factor_normalized"),
            "metadata": _bind(metadata, "metadata_only"),
            "reference": _bind(ref, "reference_only"),
        },
        {
            "condition_column": "condition",
            "biological_unit_column": "unit",
            "numerator": "b",
            "denominator": "a",
        },
    )


def test_camera_stager_refuses_changed_or_repeated_units(tmp_path):
    bindings, params = _inputs(tmp_path)
    metadata = bindings["metadata"]["path"]
    metadata.write_text(metadata.read_text().replace("donor-1,", "donor-0,"))
    stage = tmp_path / "changed"
    stage.mkdir()
    with pytest.raises(OperationBlocked, match="plan_identity_mismatch"):
        _camera(bindings, stage, params)
    stage = tmp_path / "repeated"
    stage.mkdir()
    bindings["metadata"] = _bind(metadata, "metadata_only")
    with pytest.raises(OperationBlocked, match="scientific_refusal"):
        _camera(bindings, stage, params)


def test_registry_camera_executes_only_pinned_mounted_worker(workspace_tmp_path):
    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if image not in registry.pins.pathway_kernel_images():
        pytest.skip("Select a pinned pathway image with DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    root = workspace_tmp_path
    bindings, params = _inputs(root)
    plan = {
        "kernel": {"kernel_id": "rna_pathway_camera", "parameters": params},
        "runtime_pin": {"oci_image_digest": image},
        "session_id": "e" * 32,
    }
    stages = []
    jobs = SimpleNamespace(
        workspace=root, stage=lambda rid, **kwargs: stages.append(kwargs)
    )
    output = root / "outputs"
    output.mkdir()
    result = execute_mounted_kernel(jobs, "a" * 32, plan, bindings, output)
    assert result["metrics"] == {"tested_genes": 36, "tested_sets": 2}
    report = json.loads(result["files"]["result"]["path"].read_bytes())
    assert report["method"] == "camera" and report["clinical_release_allowed"] is False
    assert all(0 <= row["p_value"] <= row["q_value"] <= 1 for row in report["results"])
    assert stages[-1]["mounted_container"]["image_id"] == image


def test_bulk_routes_freeze_scale_validate_family_and_bind_outputs(workspace_tmp_path):
    """Exercise the mounted routed NB fit, exact BH family and result hashes."""
    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if image not in registry.pins.routed_de_kernel_images():
        pytest.skip("Select a routed digest with DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    root = workspace_tmp_path
    gen = np.random.default_rng(413)
    expression = root / "counts.csv"
    metadata = root / "metadata.csv"
    samples = tuple(f"s{i}" for i in range(12))
    counts = gen.negative_binomial(20, 0.35, (150, 12))
    counts[:10, 6:] *= 5
    expression.write_text(
        "gene,"
        + ",".join(samples)
        + "\n"
        + "".join(
            "g" + str(n) + "," + ",".join(map(str, values)) + "\n"
            for n, values in enumerate(counts)
        )
    )
    metadata.write_text(
        "sample_id,unit,condition\n"
        + "".join(
            f"{s},unit{i},{'a' if i < 6 else 'b'}\n" for i, s in enumerate(samples)
        )
    )
    bindings = {
        "expression": _bind(expression, "count_1_raw_counts"),
        "metadata": _bind(metadata, "metadata_only"),
    }
    bindings["expression"]["detail"] = {"count_1_verified": False}
    plan = {
        "kernel": {
            "kernel_id": "bulk_de",
            "parameters": {
                "condition_column": "condition",
                "biological_unit": "donor",
                "biological_unit_column": "unit",
                "numerator": "b",
                "denominator": "a",
            },
        },
        "plan_id": "cp-" + "a" * 32,
        "runtime_pin": {"oci_image_digest": image},
        "session_id": "e" * 32,
    }
    jobs = SimpleNamespace(workspace=root, stage=lambda *_args, **_kwargs: None)
    dest = root / "outputs"
    dest.mkdir()
    with pytest.raises(OperationBlocked, match="scientific_refusal"):
        execute_mounted_kernel(jobs, "f" * 32, plan, bindings, dest)
    bindings["expression"]["detail"]["count_1_verified"] = True
    result = execute_mounted_kernel(jobs, "b" * 32, plan, bindings, dest)
    assert result["metrics"]["genes"] == 150
    assert result["metrics"]["tested"] > 0 and result["metrics"]["residual_df"] == 10
    assert result["files"]["statistics"]["format"] == "parquet"


@pytest.mark.wal
def test_workspace_promotion_binds_independent_evidence_and_signed_scope(
    workspace_tmp_path, monkeypatch
):
    from devbench.daemon.supervisor import Supervisor
    from devbench.mcp import approval, planning, registry_promotions
    from devbench.mcp.context import ControlPlane
    from devbench.platform.plugin_jobs import JobStore, digest

    workspace = workspace_tmp_path / "workspace"
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir()
    path = inputs / "counts.csv"
    data = b"gene,a,b\nx,1,2\n"
    path.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    ref = {
        "artifact_id": "counts-" + sha,
        "sha256": sha,
        "kind": "expression_matrix",
        "format": "csv",
        "representation_class": "count_1_raw_counts",
        "claim_tier": "none",
        "size_bytes": len(data),
    }
    jobs = JobStore(workspace)
    supervisor = Supervisor(workspace, offline_only=True)
    ctx = ControlPlane(
        SimpleNamespace(jobs=jobs, input_root=inputs, mounted_image_id=None),
        supervisor,
        {"offline_only": True},
    )
    image = registry.EXPLORATORY_IMAGE_ID
    live = any(
        row["image_id"] == image
        for row in runtime.available_images(ctx, backend="docker")
    )
    monkeypatch.setattr(
        runtime, "available_images", lambda _, **kwargs: [{"image_id": image}]
    )
    try:
        sid = ctx.admit(None, None, ref, path, "synthetic", {"count_1_verified": True})[
            "session_id"
        ]
        base = {
            "session_id": sid,
            "branch_id": "primary",
            "epistemic_purpose": "method_benchmark",
            "input_bindings": {
                "expression": {"artifact_id": ref["artifact_id"], "sha256": sha}
            },
            "plan_kind": "tier2_exploratory_code",
            "objective": "Synthetic governance fixture",
            "exploratory_code": {
                "base_image_id": image,
                "language": "python",
                "source_code": "print(1)",
                "expected_outputs": [],
            },
        }
        frozen = planning.freeze_analysis_plan(ctx, base)
        ident = {key: frozen[key] for key in ("session_id", "plan_id", "plan_sha256")}
        version = ctx.jobs.plan(sid, ident["plan_id"])["script_sha256"]
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"seed": {"type": "integer"}},
            "required": ["seed"],
        }
        descriptor = {
            "kernel_id": "fixture_kernel",
            "scope": "workspace",
            "method_version_id": version,
            "input_ports": {"expression": "count_1_raw_counts"},
            "parameter_schema": schema,
            "capability_axes": ["B_OPEN"],
            "calibrated_regimes": ["synthetic-fixture"],
            "evidence": {},
        }
        proof = {
            "pinned_environment": {"image_id": image, "lock_sha256": "d" * 64},
            "typed_io": {
                "input_ports": descriptor["input_ports"],
                "output_ports": [],
                "parameter_schema": schema,
            },
            "null_and_planted_calibration": {
                "calibrated_regimes": ["synthetic-fixture"]
            },
            "adversarial_boundary_tests": {},
            "independent_review": {
                "reviewer_id": "lab-panel",
                "critic_origin": "human_expert_panel",
                "deterministic_verification_passed": True,
            },
        }
        with ctx.authority.daemon_transaction() as db:
            for event, observations in proof.items():
                state = {
                    "method_version_id": version,
                    "status": "passed",
                    **observations,
                }
                record = {
                    "kind": "execution_event",
                    "event": event,
                    "state_sha256": digest(state),
                    "state": state,
                    "record_id": "fixture-" + event,
                }
                ctx.authority._append_execution_record(db, sid, record)
                descriptor["evidence"][event] = {
                    "record_id": record["record_id"],
                    "sha256": db.execute(
                        "SELECT sha256 FROM scientific_records "
                        "WHERE lineage_id=? AND record_id=?",
                        (sid, record["record_id"]),
                    ).fetchone()[0],
                }
        review = registry_promotions.prepare_workspace_promotion(ctx, ident, descriptor)
        with pytest.raises(OperationBlocked, match="explicit_plan_approval_required"):
            registry_promotions.seal_workspace_promotion(ctx, review)
        assert jobs.approve(
            sid,
            review["plan_id"],
            review["plan_sha256"],
            actor_id="operator",
            surface="cli",
            auth_method="local_os_user",
            rationale="Reviewed promotion scope",
        )
        with pytest.raises(OperationBlocked, match="outcome_blind_critic_required"):
            registry_promotions.seal_workspace_promotion(ctx, review)
        subject = approval.gate1_subject(jobs.plan(sid, review["plan_id"]))
        approval.record_gate1(
            jobs,
            review,
            reviewer="second-critic",
            subject_sha256=digest(subject),
            status="passed",
            rationale="Reviewed frozen promotion plan",
            offline=True,
        )
        assert registry_promotions.seal_workspace_promotion(ctx, review) == (
            "fixture_kernel",
            version,
        )
        promoted = registry.resolve_kernel(
            ctx, "fixture_kernel", version, session_id=sid
        )
        assert (
            promoted["calibration_status"] == "uncalibrated_L1"
            and not promoted["calibrated_regimes"]
        )
        second_data = b"gene,c,d\ny,3,4\n"
        second_path = inputs / "other.csv"
        second_path.write_bytes(second_data)
        second_sha = hashlib.sha256(second_data).hexdigest()
        second_ref = {
            **ref,
            "artifact_id": "counts-" + second_sha,
            "sha256": second_sha,
            "size_bytes": len(second_data),
        }
        second = ctx.admit(
            None, None, second_ref, second_path, "synthetic", {"count_1_verified": True}
        )["session_id"]
        assert second != sid
        listed = registry.list_kernels_and_methods(ctx, {"session_id": second})[
            "kernels"
        ]
        assert any(
            row["kernel_id"] == "fixture_kernel"
            and row["promotion_scope"] == "workspace"
            for row in listed
        )
        assert not any(row["kernel_id"] == "script-" + version for row in listed)
        assert (
            registry.resolve_kernel(ctx, "fixture_kernel", version, session_id=second)
            == promoted
        )
        run_request = {
            **base,
            "session_id": second,
            "plan_kind": "tier1_kernel",
            "input_bindings": {
                "expression": {
                    "artifact_id": second_ref["artifact_id"],
                    "sha256": second_sha,
                }
            },
            "kernel": {
                "kernel_id": "fixture_kernel",
                "version": version,
                "parameters": {"seed": 42},
            },
        }
        del run_request["exploratory_code"]
        with pytest.raises(OperationBlocked, match="scientific_refusal"):
            planning.freeze_analysis_plan(
                ctx, {**run_request, "epistemic_purpose": "confirmatory"}
            )
        run_plan = planning.freeze_analysis_plan(ctx, run_request)
        assert run_plan["runtime_pin"]["oci_image_digest"] == image
        if live:
            import time

            supervisor.start()
            launch = planning.submit_reviewed_run(
                ctx, {k: run_plan[k] for k in ("session_id", "plan_id", "plan_sha256")}
            )
            run_key = {"session_id": second, "run_id": launch["run_id"]}
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                state = planning.get_run_status(ctx, run_key)
                if state["status"] not in {"queued", "running"}:
                    assert state["status"] == "completed", (
                        state["warnings"],
                        jobs.managed_run(launch["run_id"])["error_text"],
                    )
                    break
                time.sleep(0.1)
            else:
                pytest.fail("Promoted workspace script did not finish")
        assert promoted["promotion_scope"] == "workspace" and promoted["runtime_ready"]
        assert (
            registry.select_image(
                ctx, "fixture_kernel", session_id=sid, version=version
            )
            == image
        )
    finally:
        supervisor.close()


@pytest.mark.wal
def test_governed_bulk_to_enrichment_requires_verified_predecessor(workspace_tmp_path):
    import time

    from devbench.daemon.supervisor import Supervisor
    from devbench.mcp import intake, planning, receipts
    from devbench.mcp.context import ControlPlane
    from devbench.platform.plugin_jobs import JobStore

    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if image not in registry.pins.routed_de_kernel_images():
        pytest.skip("Select a routed digest with DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    root = workspace_tmp_path
    workspace, inputs = root / "workspace", root / "inputs"
    inputs.mkdir()
    rows = np.random.default_rng(627).negative_binomial(20, 0.35, (150, 12))
    rows[:20, 6:] *= 5
    (inputs / "counts.csv").write_text(
        "gene,"
        + ",".join(f"s{i}" for i in range(12))
        + "\n"
        + "".join(
            f"g{i}," + ",".join(map(str, row)) + "\n" for i, row in enumerate(rows)
        )
    )
    (inputs / "metadata.csv").write_text(
        "sample_id,unit,condition\n"
        + "".join(f"s{i},u{i},{'case' if i >= 6 else 'control'}\n" for i in range(12))
    )
    (inputs / "reference.tsv").write_text(
        "tf\ttarget\n"
        + "".join(f"{'first' if i < 20 else 'second'}\tg{i}\n" for i in range(150))
    )
    jobs = JobStore(workspace)
    supervisor = Supervisor(workspace, offline_only=True)
    ctx = ControlPlane(
        SimpleNamespace(jobs=jobs, input_root=inputs, mounted_image_id=image),
        supervisor,
        {"offline_only": True},
    )
    sid, revision = None, None

    def register(name, kind, representation, role):
        nonlocal sid, revision
        registered = intake.register_investigation_input(
            ctx,
            {
                "session_id": sid,
                "expected_revision": revision,
                "input": {
                    "path": str(inputs / name),
                    "file_kind": kind,
                    "investigation_role": role,
                    "data_classification": "synthetic",
                },
                "representation_class": representation,
                "provenance_declaration": {
                    "origin": "user_declaration",
                    "source": "Synthetic software fixture",
                    "biological_unit": "donor",
                    "count_provenance": "original raw counts"
                    if kind == "expression"
                    else None,
                },
            },
        )
        sid, revision = registered["session_id"], registered["revision"]
        return {k: registered["artifact"][k] for k in ("artifact_id", "sha256")}

    def run(name, params, bindings, depends_on=()):
        projection = planning.freeze_analysis_plan(
            ctx,
            {
                "session_id": sid,
                "branch_id": "primary",
                "objective": "Characterize a predefined synthetic contrast",
                "epistemic_purpose": "exploratory",
                "plan_kind": "tier1_kernel",
                "kernel": {"kernel_id": name, "version": "1", "parameters": params},
                "input_bindings": bindings,
                "depends_on": list(depends_on),
            },
        )
        ident = {k: projection[k] for k in ("plan_id", "plan_sha256")}
        run = planning.submit_reviewed_run(ctx, {"session_id": sid, **ident})
        key = {"session_id": sid, "run_id": run["run_id"]}
        deadline = time.monotonic() + 70
        while time.monotonic() < deadline:
            latest = planning.get_run_status(ctx, key)
            if latest["status"] not in {"running", "queued"}:
                assert latest["status"] == "completed", latest
                return receipts.get_run_receipt(ctx, key), ident
            time.sleep(0.1)
        pytest.fail("The verified synthetic worker did not finish")

    supervisor.start()
    try:
        counts = register(
            "counts.csv", "expression", "count_1_raw_counts", "primary_cohort"
        )
        meta = register("metadata.csv", "metadata", "metadata_only", "primary_cohort")
        reference = register(
            "reference.tsv", "reference", "reference_only", "gene_set_collection"
        )
        de, ident = run(
            "bulk_de",
            {
                "biological_unit": "donor",
                "biological_unit_column": "unit",
                "condition_column": "condition",
                "numerator": "case",
                "denominator": "control",
            },
            {"expression": counts, "metadata": meta},
        )
        artifact = de["outputs"]["statistics"]
        result, _ = run(
            "network_regulon_enrichment",
            {"tested_step_id": de["run_id"], "foreground_q_max": 0.05},
            {
                "statistics": {k: artifact[k] for k in ("artifact_id", "sha256")},
                "reference": reference,
            },
            [ident],
        )
        assert result["metrics"]["tested_genes"] == de["metrics"]["tested"]
        assert result["metrics"]["tested_sets"] == 2
        assert (
            result["claim_tier"] == "L1_execution_verified"
            and result["clinical_release_allowed"] is False
        )
    finally:
        supervisor.close()
