"""Synthetic trust/receipt controls; no live workflow or biological evidence."""
from copy import deepcopy
import hashlib
import json

import pytest

from devbench.analysis.methods import MethodService
from devbench.analysis.fastq_method_adapter import _verify_products, execute_fastq, fastq_contract
from devharness_core.methods import MethodContract


@pytest.fixture(autouse=True)
def synthetic_nextflow_pin(monkeypatch):
    """Only process-fake tests replace the official executable admission pin."""
    from devbench.analysis import fastq_execution
    monkeypatch.setattr(fastq_execution, "NEXTFLOW_DIST_SHA256",
        hashlib.sha256(b"process fixture; never executed\n").hexdigest(), raising=False)


def manifest_fixture(tmp_path):
    from tests.test_fastq_contract import plan_fixture
    from devbench.analysis.fastq_planner_binding import build_fastq_manifest
    root = tmp_path.resolve()
    plan = plan_fixture(root)
    executable = root / "nextflow-fixture"
    executable.write_text("process fixture; never executed\n")
    return build_fastq_manifest(plan, workspace=root, execution={
        "resources": {"cpus": 1, "memory_gb": 1}, "timeout_seconds": 60,
        "execution_profile": "minimal-counts", "nextflow_path": str(executable),
        "nextflow_sha256": hashlib.sha256(executable.read_bytes()).hexdigest()})


def synthetic_fastq_success(plan, output_root, **kwargs):
    """Process replacement only: fabricated numbers are NOT numerical evidence."""
    from devbench.analysis.fastq_contract import preflight_fastq_plan
    from devbench.analysis.fastq_execution import _identity
    frozen = preflight_fastq_plan(plan)
    design = frozen["design"]
    attempt = output_root / "synthetic-attempt"
    attempt.mkdir()
    counts = attempt / "counts.tsv"
    counts.write_text("gene_id\t" + "\t".join(design["sample_ids"]) + "\n" +
        "\n".join(gene + "\t" + "\t".join([str(n)] * len(design["sample_ids"]))
                  for gene, n in [("g1", 5), ("g2", 7)]) + "\n")
    de = attempt / "de.tsv"
    de.write_text("gene_id\tpvalue\tpadj\ng1\t0.2\t0.4\ng2\t0.5\t0.5\n")
    refs = {"count_matrix": _identity(counts), "differential_expression": _identity(de)}
    provenance = {"status": "completed", "design": design, **refs,
        "family": {"complete": True, "emitted_gene_count": 2, "result_row_count": 2,
                   "gene_ids_sha256": hashlib.sha256(b'["g1","g2"]').hexdigest()}}
    model = attempt / "model.json"
    model.write_text(json.dumps(provenance))
    refs["count_model_provenance"] = _identity(model)
    record = {"status": "completed", "software_acceptance": True, "count_model_execution": "verified",
              "formula": design["formula"], "contrast": design["contrast"], "artifacts": refs,
              "test_evidence": "synthetic_process_fixture_not_numerical_evidence"}
    receipt = attempt / "result.json"
    receipt.write_text(json.dumps(record))
    return {"status": "completed", "attempt_dir": str(attempt), "result_manifest": str(receipt)}


def prepared_fastq(tmp_path):
    from devbench.analysis.fastq_planner_binding import register_fastq_input
    manifest = manifest_fixture(tmp_path)
    methods = MethodService(tmp_path / "methods")
    inspection = register_fastq_input(methods, manifest, actor="fixture")
    method = methods.register_fastq(manifest, actor="fixture")
    analysis = methods.create_analysis("Synthetic FASTQ", actor="fixture")
    branch = methods.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="fixture")
    artifact = inspection["input_artifact_refs"]["manifest"]["artifact_id"]
    step = methods.add_step(branch["branch_id"], method["version_id"], {"manifest": artifact}, {}, actor="fixture")
    return methods, method, step, manifest


def test_explicit_authority_and_closed_executor(tmp_path):
    methods, _, step, _ = prepared_fastq(tmp_path)
    with pytest.raises(PermissionError, match="fresh"):
        methods.run_step(step["step_id"], actor="fixture")
    with pytest.raises(ValueError, match="injected executor"):
        methods.run_step(step["step_id"], actor="fixture", data_execution_authorized=True, executor=object())
    assert methods.list_records("run") == []


def test_registered_process_fixture_uses_real_method_dag_and_flat_outputs(tmp_path, monkeypatch):
    from devbench.analysis import fastq_execution
    methods, _, step, _ = prepared_fastq(tmp_path)
    monkeypatch.setattr(fastq_execution, "run_fastq_plan", synthetic_fastq_success)
    run = methods.run_step(step["step_id"], actor="fixture", data_execution_authorized=True)
    assert run["status"] == "succeeded", run.get("error")
    assert set(run["outputs"]) == {"count_matrix", "differential_expression", "count_model_provenance", "result"}
    assert methods.provenance(run["run_id"])["valid"]
    assert (methods.root / "runs" / run["run_id"] / "fastq-workflow").is_dir()


def test_live_contract_rejects_forged_policy(tmp_path):
    from devbench.analysis.fastq_method_adapter import fastq_source
    methods, method, _, _ = prepared_fastq(tmp_path)
    forged = deepcopy(method["contract"])
    forged["execution"]["cpu"] = 2
    with pytest.raises(ValueError, match="exact live"):
        methods.register_method(forged, source=fastq_source(), actor="fixture")


def test_contract_is_reusable_across_plan_inputs_but_not_runtime_policy(tmp_path):
    from devbench.analysis.fastq_planner_binding import build_fastq_manifest
    manifest = manifest_fixture(tmp_path)
    changed = deepcopy(manifest["source_plan"])
    changed["alpha"] = .01
    other = build_fastq_manifest(changed, workspace=tmp_path, execution=manifest["execution"])
    assert fastq_contract(manifest).version_id == fastq_contract(other).version_id
    other["execution"]["execution_profile"] = "standard"
    assert fastq_contract(manifest).version_id != fastq_contract(other).version_id


def test_new_kind_does_not_widen_custom_network_or_existing_limits(tmp_path):
    from tests.test_extensible_methods import custom
    value, _, _ = custom(tmp_path)
    value["execution"]["network"] = "allow"
    with pytest.raises(ValueError, match="network"):
        MethodContract.from_dict(value)
    manifest = manifest_fixture(tmp_path)
    manifest["execution"]["resources"]["cpus"] = 17
    with pytest.raises(ValueError, match="existing method execution limits"):
        fastq_contract(manifest)


def test_precancelled_method_never_creates_run(tmp_path):
    import threading
    methods, _, step, _ = prepared_fastq(tmp_path)
    cancellation = threading.Event()
    cancellation.set()
    with pytest.raises(PermissionError, match="Cancelled"):
        methods.run_step(step["step_id"], actor="fixture", data_execution_authorized=True,
                         cancellation_event=cancellation)
    assert methods.list_records("run") == []


def test_refusal_cannot_publish_success_or_output_references(tmp_path, monkeypatch):
    from devbench.analysis import fastq_execution
    methods, _, step, _ = prepared_fastq(tmp_path)
    def refusal(plan, output_root, **kwargs):
        receipt = output_root / "result.json"
        receipt.write_text(json.dumps({"status": "unsupported_analysis", "reason": "fixture unsupported request"}))
        return {"status": "unsupported_analysis", "result_manifest": str(receipt)}
    monkeypatch.setattr(fastq_execution, "run_fastq_plan", refusal)
    run = methods.run_step(step["step_id"], actor="fixture", data_execution_authorized=True)
    assert run["status"] == "failed" and not run["outputs"]
    assert run["execution"]["outcome"] == "unsupported_analysis"


def test_runtime_events_use_existing_run_record_and_cancel_blocks_publication(tmp_path, monkeypatch):
    import threading
    from devbench.analysis import fastq_execution
    methods, _, step, _ = prepared_fastq(tmp_path)
    cancellation = threading.Event()
    def process_fixture(plan, output_root, **kwargs):
        assert kwargs["execution_observer"]({"event": "attempt_created", "attempt_dir": str(output_root)})
        cancellation.set()
        assert kwargs["execution_observer"]({"event": "terminal", "status": "cancelled"}) is False
        return synthetic_fastq_success(plan, output_root, **kwargs)
    monkeypatch.setattr(fastq_execution, "run_fastq_plan", process_fixture)
    run = methods.run_step(step["step_id"], actor="fixture", data_execution_authorized=True,
                           cancellation_event=cancellation)
    assert run["status"] == "failed" and run["outputs"] == {}
    assert [event["event"] for event in run["runtime_events"]] == ["attempt_created", "terminal"]
    assert methods.provenance(run["run_id"])["valid"]


def test_same_step_cannot_redispatch_after_unsealed_interruption(tmp_path, monkeypatch):
    from devbench.analysis import fastq_execution
    methods, _, step, _ = prepared_fastq(tmp_path)
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt("synthetic coordinator death")
    monkeypatch.setattr(fastq_execution, "run_fastq_plan", interrupted)
    with pytest.raises(KeyboardInterrupt):
        methods.run_step(step["step_id"], actor="fixture", data_execution_authorized=True)
    with pytest.raises(PermissionError, match="no redispatch"):
        methods.run_step(step["step_id"], actor="fixture", data_execution_authorized=True)
    assert len(methods.list_records("run")) == 1


def test_abandoned_lease_reconciliation_uses_owned_runtime_without_redispatch(tmp_path, monkeypatch):
    from devbench.analysis import fastq_execution
    methods, _, step, _ = prepared_fastq(tmp_path)
    def interrupted(plan, output_root, **kwargs):
        kwargs["execution_observer"]({"event": "attempt_created", "attempt_dir": str(output_root / "fixture")})
        raise KeyboardInterrupt("synthetic coordinator death")
    monkeypatch.setattr(fastq_execution, "run_fastq_plan", interrupted)
    with pytest.raises(KeyboardInterrupt):
        methods.run_step(step["step_id"], actor="fixture", data_execution_authorized=True)
    run = methods.list_records("run")[0]
    calls = []
    def recovery(events, expected):
        calls.append((events, expected))
        return {"status": "uncertain", "execution_restarted": False, "reason": "synthetic missing PID identity"}
    monkeypatch.setattr(fastq_execution, "reconcile_fastq_runtime", recovery)
    assert methods.reconcile() == [run["run_id"]]
    assert len(calls) == 1 and calls[0][1] == methods.root / "runs" / run["run_id"] / "fastq-workflow"
    recovered = methods.get("run", run["run_id"])
    assert recovered["status"] == "interrupted" and recovered["cleanup_required"] is True
    assert recovered["runtime_reconciliation"]["execution_restarted"] is False
    assert recovered["outputs"] == {}
    with pytest.raises(PermissionError, match="no redispatch"):
        methods.run_step(step["step_id"], actor="fixture", data_execution_authorized=True)


def test_changed_live_owner_identity_refuses_before_engine(tmp_path, monkeypatch):
    from devbench.analysis import fastq_method_adapter, fastq_execution
    methods, _, step, _ = prepared_fastq(tmp_path)
    monkeypatch.setattr(fastq_method_adapter, "_runtime_identity", lambda: "0" * 64)
    monkeypatch.setattr(fastq_execution, "run_fastq_plan", lambda *a, **k: pytest.fail("must not launch"))
    run = methods.run_step(step["step_id"], actor="fixture", data_execution_authorized=True)
    assert run["status"] == "failed" and run["outputs"] == {}
    assert "live source" in run["error"]


@pytest.mark.parametrize("owner", ["rna_design_audit.py", "rna_execution_inputs.py"])
def test_runtime_identity_covers_rna_admission_owners(monkeypatch, owner):
    from pathlib import Path
    from devbench.analysis import fastq_method_adapter as adapter
    before = adapter._runtime_identity()
    original = adapter.hash_artifact
    def changed_owner(path, **kwargs):
        value = original(path, **kwargs)
        return {**value, "sha256": "0" * 64} if Path(path).name == owner else value
    monkeypatch.setattr(adapter, "hash_artifact", changed_owner)
    assert adapter._runtime_identity() != before


def test_failed_cleanup_blocks_redispatch_until_reentrant_reconciliation(tmp_path, monkeypatch):
    from devharness_core.provenance import verify_execution
    from pathlib import Path
    from devbench.analysis import fastq_execution
    methods, _, step, _ = prepared_fastq(tmp_path)
    calls = []
    def failed_engine(plan, output_root, **kwargs):
        calls.append(str(output_root))
        kwargs["execution_observer"]({"event": "attempt_created", "attempt_dir": str(output_root / "fixture")})
        receipt = output_root / "result.json"
        receipt.write_text(json.dumps({"status": "failed", "reason": "synthetic engine failure",
            "engine_launch_count": 1, "engine_launch_attempt_count": 1, "artifacts": {},
            "container_cleanup": {"status": "failed", "reason": "synthetic daemon unavailable"}}))
        return {"status": "failed", "result_manifest": str(receipt)}
    monkeypatch.setattr(fastq_execution, "run_fastq_plan", failed_engine)
    first = methods.run_step(step["step_id"], actor="fixture", data_execution_authorized=True)
    assert first["cleanup_required"] is True
    assert first["execution"]["container_cleanup"]["status"] == "failed"
    assert methods.provenance(first["run_id"])["valid"]
    original_evidence_bytes = Path(first["evidence"]["manifest_path"]).read_bytes()
    with pytest.raises(PermissionError, match="no redispatch"):
        methods.run_step(step["step_id"], actor="fixture", data_execution_authorized=True)
    attempts = []
    def recover(events, expected):
        attempts.append(expected)
        return {"status": "reconciled" if len(attempts) == 2 else "uncertain", "execution_restarted": False}
    monkeypatch.setattr(fastq_execution, "reconcile_fastq_runtime", recover)
    assert methods.reconcile() == [first["run_id"]]
    assert methods.get("run", first["run_id"])["cleanup_required"] is True
    assert methods.provenance(first["run_id"])["valid"]
    with pytest.raises(PermissionError, match="no redispatch"):
        methods.run_step(step["step_id"], actor="fixture", data_execution_authorized=True)
    assert methods.reconcile() == [first["run_id"]]
    repaired = methods.get("run", first["run_id"])
    assert repaired["cleanup_required"] is False
    assert repaired["status"] == first["status"] == "failed"
    assert repaired["started_at"] == first["started_at"] and repaired["finished_at"] == first["finished_at"]
    assert repaired["runtime_reconciled_at"]
    assert methods.provenance(first["run_id"])["valid"]
    assert Path(first["evidence"]["manifest_path"]).read_bytes() == original_evidence_bytes
    assert verify_execution(first["evidence"]["manifest_path"], first["evidence"]["root_hash"])["valid"]
    assert methods.reconcile() == []
    assert len(calls) == 1 and len(attempts) == 2
    monkeypatch.setattr(fastq_execution, "run_fastq_plan", synthetic_fastq_success)
    explicit_retry = methods.run_step(step["step_id"], actor="fixture", data_execution_authorized=True)
    assert explicit_retry["status"] == "succeeded"


def test_execution_rejects_mutated_reference_before_engine(tmp_path, monkeypatch):
    from pathlib import Path
    from devbench.analysis import fastq_execution
    methods, _, step, manifest = prepared_fastq(tmp_path)
    Path(manifest["source_plan"]["reference"]["fasta"]).write_text(">chr1\nACGTACGA\n")
    monkeypatch.setattr(fastq_execution, "run_fastq_plan", lambda *a, **k: pytest.fail("must not launch"))
    run = methods.run_step(step["step_id"], actor="fixture", data_execution_authorized=True)
    assert run["status"] == "failed" and not run["outputs"]
    assert "changed" in run["error"]


def test_method_service_exposes_closed_fastq_registration():
    assert callable(getattr(MethodService, "register_fastq", None))


def test_execution_requires_fresh_authority_before_any_reads(tmp_path):
    with pytest.raises(PermissionError, match="fresh"):
        execute_fastq(None, {}, {}, tmp_path)


def test_registered_backend_cannot_turn_arbitrary_host_code_into_trusted_runtime(tmp_path, monkeypatch):
    from devbench.analysis import fastq_execution
    manifest = manifest_fixture(tmp_path)
    monkeypatch.setattr(fastq_execution, "NEXTFLOW_DIST_SHA256",
        "60db869a7618783e3ee60183a2b0c6c50ffc67221eabec60800b904991786aaa")
    with pytest.raises(ValueError, match="official Nextflow distribution"):
        fastq_contract(manifest)


def products(tmp_path):
    from devharness_core.provenance import hash_artifact
    work = tmp_path / "workflow"
    work.mkdir()
    design = {"formula": "~ condition", "contrast": {"factor": "condition", "numerator": "B", "denominator": "A"},
              "sample_ids": ["a", "b"]}
    counts = work / "counts.tsv"
    counts.write_text("gene_id\ta\tb\ng1\t2\t4\ng2\t3\t5\n")
    de = work / "de.tsv"
    de.write_text("gene_id\tpvalue\tpadj\ng1\t0.2\t0.4\ng2\t0.5\t0.5\n")
    refs = {"count_matrix": {**hash_artifact(counts), "path": str(counts)},
            "differential_expression": {**hash_artifact(de), "path": str(de)}}
    family = {"complete": True, "emitted_gene_count": 2, "result_row_count": 2,
              "gene_ids_sha256": hashlib.sha256(b'["g1","g2"]').hexdigest()}
    provenance = {"status": "completed", "design": design, **refs, "family": family}
    path = work / "model.json"
    path.write_text(json.dumps(provenance))
    refs["count_model_provenance"] = {**hash_artifact(path), "path": str(path)}
    record = {"status": "completed", "software_acceptance": True, "count_model_execution": "verified",
              "formula": design["formula"], "contrast": design["contrast"], "artifacts": refs}
    return record, {"frozen_preflight": {"design": design}}, work


def test_full_family_receipt_verification(tmp_path):
    record, manifest, work = products(tmp_path)
    assert set(_verify_products(record, manifest, work)) == set(record["artifacts"])


@pytest.mark.parametrize("change", ["formula", "hash", "refusal", "family", "policy", "q_below_p"])
def test_receipt_rejects_changed_estimand_hash_refusal_or_family(tmp_path, change):
    from devharness_core.provenance import hash_artifact
    record, manifest, work = products(tmp_path)
    if change == "formula":
        record["formula"] = "~ batch + condition"
    elif change == "hash":
        (work / "de.tsv").write_text("gene_id\ng1\n")
    elif change == "refusal":
        record["status"] = "refused"
    elif change == "policy":
        manifest["frozen_preflight"]["analysis_policy"] = {"format": "devbench.fastq-analysis-policy/1"}
    elif change == "q_below_p":
        de = work / "de.tsv"
        de.write_text("gene_id\tpvalue\tpadj\ng1\t1e-30\t0\ng2\t0.5\t0.5\n")
        record["artifacts"]["differential_expression"] = {**hash_artifact(de), "path": str(de)}
        p = work / "model.json"
        model = json.loads(p.read_text())
        model["differential_expression"] = record["artifacts"]["differential_expression"]
        p.write_text(json.dumps(model))
        record["artifacts"]["count_model_provenance"] = {**hash_artifact(p), "path": str(p)}
    else:
        p = work / "model.json"
        model = json.loads(p.read_text())
        model["family"]["complete"] = False
        p.write_text(json.dumps(model))
        record["artifacts"]["count_model_provenance"] = {**hash_artifact(p), "path": str(p)}
    with pytest.raises(ValueError):
        _verify_products(record, manifest, work)
