"""Reviewed backed-workbench fixtures; no model participant or clinical approval."""
import json

import pytest
from tests.test_analysis_episodes import workspace


@pytest.fixture(name="tmp_path")
def protected_fixture_directory(workspace):
    # Review evidence deliberately cannot live in shared platform scratch.
    return workspace


def api():
    from devbench.analysis.workbench_proposals import (
        prepare_workbench_proposal, record_workbench_review, prepare_reviewed_workbench,
        execute_reviewed_workbench, verify_workbench_review,
    )
    return prepare_workbench_proposal, record_workbench_review, prepare_reviewed_workbench, execute_reviewed_workbench, verify_workbench_review


def fixture(root):
    from tests.test_analysis_singlecell import counts_fixture
    from devbench.analysis.workbench import default_parameters
    from devbench.analysis.checkpoints import sha256
    source = root / "cells.h5ad"
    counts_fixture().write_h5ad(source)
    parameters = default_parameters(data_sha256=sha256(source), counts_layer=None,
        min_genes=3, mad_limit=None, n_top_genes=30, n_pcs=8, seed=128,
        stage_timeout_seconds=90, memory_mb=1024, correction="none",
        markers={"T": ["Cd3d", "Cd3e", "Trac", "Il7r"], "B": ["Ms4a1", "Cd79a", "Cd79b", "Cd19"]})
    declarations = {"assay": "scrna", "measurement": "umi_counts",
        "count_provenance": "Artificial integer-count software fixture",
        "capture_unit_key": "batch", "capture_units_confirmed": True,
        "annotation_provenance": "Public toy markers; no experimental annotation validation"}
    return source, parameters, declarations


def prepared(root, *, confirmed=True):
    source, parameters, declarations = fixture(root)
    declarations["capture_units_confirmed"] = confirmed
    receipt = api()[0](root / "proposal", question="Inspect QC, clustering and marker-defined types",
        source=source, parameters=parameters, declarations=declarations,
        run_timeout_seconds=180, allow_data_read=True)
    return receipt, source


def reviewed(root, proposal, decision="approve"):
    return api()[1](root / "review", proposal["manifest_path"], expected_root=proposal["root_hash"],
        reviewer="scripted-fixture-operator", decision=decision, rationale="Control fixture; not independent scientific approval",
        confirmations={key: True for key in ("question_and_scope", "input_provenance", "capture_units", "settings_and_limits", "interpretation_limits")},
        allow_review=True)


def begin(root, proposal, review):
    return api()[2](root / "bundle", proposal["manifest_path"], review["manifest_path"],
        expected_proposal_root=proposal["root_hash"], expected_review_root=review["root_hash"], allow_data_execution=True)


@pytest.mark.parametrize("permission", [False, None, 0, 1, "true"])
def test_default_denial_precedes_inputs_and_paths(permission):
    prepare, review, begin, execute, _ = api()
    with pytest.raises(PermissionError):
        prepare(None, question=None, source=None, parameters=None, declarations=None, allow_data_read=permission)
    with pytest.raises(PermissionError):
        review(None, None, expected_root=None, reviewer=None, decision=None, rationale=None,
               confirmations=None, allow_review=permission)
    with pytest.raises(PermissionError):
        begin(None, None, None, expected_proposal_root=None, expected_review_root=None, allow_data_execution=permission)
    with pytest.raises(PermissionError):
        execute(None, None, allow_data_execution=permission)


def test_preparation_binds_backed_input_without_copying_expression(tmp_path):
    from devharness_core.provenance import verify_execution
    proposal, source = prepared(tmp_path)
    assert verify_execution(proposal["manifest_path"], proposal["root_hash"])["valid"]
    record = json.loads((tmp_path / "proposal/proposal.json").read_text())
    assert record["preview"]["cells"] == 80 and record["preview"]["genes"] == 60
    assert record["external_input"]["path"] == str(source)
    assert record["external_input"]["copied_into_proposal"] is False
    assert not list((tmp_path / "proposal").rglob("*.h5ad"))
    assert record["status"] == "awaiting_operator_review"


def test_unconfirmed_capture_identity_can_be_recorded_but_not_approved(tmp_path):
    proposal, _ = prepared(tmp_path, confirmed=False)
    with pytest.raises(ValueError, match="capture|unsupported|eligible"):
        reviewed(tmp_path, proposal)
    decision = reviewed(tmp_path, proposal, "needs_information")
    assert decision["root_hash"]


def test_rejected_review_never_prepares_execution(tmp_path):
    proposal, _ = prepared(tmp_path)
    review = reviewed(tmp_path, proposal, "reject")
    with pytest.raises(ValueError, match="approved"):
        begin(tmp_path, proposal, review)
    assert not (tmp_path / "bundle").exists()


def test_input_change_after_review_is_not_rebased(tmp_path):
    proposal, source = prepared(tmp_path)
    review = reviewed(tmp_path, proposal)
    with source.open("ab") as handle:
        handle.write(b"changed identity")
    with pytest.raises(ValueError, match="sha256|identity"):
        begin(tmp_path, proposal, review)
    assert not (tmp_path / "bundle").exists()


def test_prepared_workbench_verifies_embedded_review_and_retains_external_input(tmp_path):
    from devbench.analysis.workbench import _frozen_configuration
    proposal, source = prepared(tmp_path)
    review = reviewed(tmp_path, proposal)
    result = begin(tmp_path, proposal, review)
    params, _ = _frozen_configuration(tmp_path / "bundle")
    assert params["review_binding_sha256"]
    assert api()[4](tmp_path / "bundle", params)["proposal_root"] == proposal["root_hash"]
    assert result["analysis_executed"] is False
    assert json.loads((tmp_path / "bundle/bindings.json").read_text())["data"] == str(source)
    assert not list((tmp_path / "bundle").glob("*.h5ad"))


def test_removed_review_cannot_turn_a_reviewed_workbench_into_an_unreviewed_one(tmp_path):
    from devbench.analysis.workbench import _frozen_configuration
    proposal, _ = prepared(tmp_path)
    review = reviewed(tmp_path, proposal)
    begin(tmp_path, proposal, review)
    root = tmp_path / "bundle"
    params = json.loads((root / "parameters.json").read_text())
    identity = json.loads((root / "checkpoint-identity.json").read_text())
    params.pop("review_binding_sha256")
    identity["parameters"] = params
    (root / "parameters.json").write_text(json.dumps(params))
    (root / "checkpoint-identity.json").write_text(json.dumps(identity))
    with pytest.raises(ValueError, match="review"):
        _frozen_configuration(root)


def test_review_cannot_bind_a_different_proposal(tmp_path):
    proposal, _ = prepared(tmp_path)
    review = reviewed(tmp_path, proposal)
    another = tmp_path / "another"
    another.mkdir()
    other, _ = prepared(another)
    with pytest.raises(ValueError, match="proposal"):
        begin(another, other, review)


def test_real_supervised_execution_and_native_review_pass(tmp_path):
    from pathlib import Path
    import shutil
    import subprocess
    import sys
    from devbench.analysis.bundles import verify_bundle, import_bundle
    proposal, source = prepared(tmp_path)
    review = reviewed(tmp_path, proposal)
    begin(tmp_path, proposal, review)
    result = api()[3](tmp_path / "bundle", tmp_path / "execution", allow_data_execution=True)
    assert result["status"] == "completed_review_passed", result
    assert result["model_sessions_attempted"] == 0
    assert verify_bundle(tmp_path / "bundle", result["bundle_root"])["verified"]
    assert Path(result["archive"]).is_file()
    relocated_data = tmp_path / "relocated.h5ad"
    shutil.copyfile(source, relocated_data)  # only the 74 KB synthetic fixture
    imported = tmp_path / "imported"
    import_bundle(Path(result["archive"]), imported, result["bundle_root"], data_path=relocated_data)
    source.rename(tmp_path / "retained-original.h5ad")
    (tmp_path / "proposal").rename(tmp_path / "retained-proposal")
    (tmp_path / "review").rename(tmp_path / "retained-review")
    command = [sys.executable, "-m", "devbench.analysis.workbench", "--replay-bundle", str(imported),
        "--expected-root", result["bundle_root"], "--data", str(relocated_data), "--out", str(tmp_path / "replay")]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    (tmp_path / "replay.stdout").write_text(completed.stdout)
    (tmp_path / "replay.stderr").write_text(completed.stderr)
    assert completed.returncode == 0, completed.stderr[-3000:]
    replay_receipt = json.loads((tmp_path / "replay/root-receipt.json").read_text())
    assert verify_bundle(tmp_path / "replay", replay_receipt["manifest_sha256"])["verified"]
    assert json.loads((tmp_path / "replay.execution/result.json").read_text())["status"] == "completed_review_passed"
    assert json.loads((tmp_path / "replay/report.json").read_text()) == json.loads((tmp_path / "bundle/report.json").read_text())


def test_worker_interruption_is_retained_without_a_completed_claim(tmp_path, monkeypatch):
    from devharness_core import watchdog
    proposal, _ = prepared(tmp_path)
    review = reviewed(tmp_path, proposal)
    begin(tmp_path, proposal, review)
    def interrupted(*args, **kwargs):
        raise InterruptedError("fixture interruption")
    monkeypatch.setattr(watchdog, "run_sandboxed", interrupted)
    result = api()[3](tmp_path / "bundle", tmp_path / "execution", allow_data_execution=True)
    assert result["status"] == "infrastructure_failure"
    assert result["bundle_root"] is None
    assert (tmp_path / "execution/result.json").is_file()
    assert not (tmp_path / "bundle/receipts.json").exists()


def test_changed_active_coordinator_is_rejected_before_worker_launch(tmp_path, monkeypatch):
    from devbench.analysis import workbench_proposals as module
    from devharness_core import watchdog
    proposal, _ = prepared(tmp_path)
    review = reviewed(tmp_path, proposal)
    begin(tmp_path, proposal, review)
    monkeypatch.setattr(module, "_source_identity", lambda: "f" * 64)
    def forbidden(*args, **kwargs):
        pytest.fail("Unreviewed coordinator reached execution")
    monkeypatch.setattr(watchdog, "run_sandboxed", forbidden)
    with pytest.raises(ValueError, match="coordinator|source"):
        module.execute_reviewed_workbench(tmp_path / "bundle", tmp_path / "execution", allow_data_execution=True)


@pytest.mark.parametrize("entry", ["execute_bundle", "run_stage", "stage_cli"])
def test_reviewed_execution_cannot_bypass_its_whole_run_supervisor(tmp_path, monkeypatch, entry):
    from devbench.analysis import workbench, notebooks
    from devharness_core import watchdog
    proposal, _ = prepared(tmp_path)
    review = reviewed(tmp_path, proposal)
    begin(tmp_path, proposal, review)
    def forbidden(*args, **kwargs):
        pytest.fail("Reviewed workbench launched outside its whole-run supervisor")
    monkeypatch.setattr(notebooks, "execute_notebook", forbidden)
    monkeypatch.setattr(workbench.subprocess, "run", forbidden)
    monkeypatch.setattr(watchdog, "run_sandboxed", forbidden)
    with pytest.raises(PermissionError, match="supervis"):
        if entry == "execute_bundle":
            workbench.execute_bundle(tmp_path / "bundle")
        elif entry == "run_stage":
            workbench.run_stage(tmp_path / "bundle", "qc")
        else:
            workbench.main(["--_stage", "qc", "--bundle", str(tmp_path / "bundle")])


def test_existing_session_marker_is_preserved_and_failed_attempt_is_recorded(tmp_path):
    proposal, _ = prepared(tmp_path)
    review = reviewed(tmp_path, proposal)
    begin(tmp_path, proposal, review)
    marker = tmp_path / "bundle/.reviewed-execution.json"
    marker.write_text('{"session_id":"existing-attempt"}')
    result = api()[3](tmp_path / "bundle", tmp_path / "execution", allow_data_execution=True)
    assert result["status"] == "infrastructure_failure"
    assert result["error_type"] == "FileExistsError"
    assert json.loads(marker.read_text())["session_id"] == "existing-attempt"
    assert (tmp_path / "execution/result.json").is_file()


def test_interrupted_inspection_preserves_a_nonreviewable_failure_record(tmp_path, monkeypatch):
    from devharness_core import watchdog
    source, parameters, declarations = fixture(tmp_path)
    def interrupted(*args, **kwargs):
        raise InterruptedError("fixture inspection interrupted")
    monkeypatch.setattr(watchdog, "run_sandboxed", interrupted)
    with pytest.raises(InterruptedError):
        api()[0](tmp_path / "proposal", question="Inspect cells", source=source, parameters=parameters,
                 declarations=declarations, allow_data_read=True)
    assert json.loads((tmp_path / "proposal/failure.json").read_text())["status"] == "infrastructure_failure"
    assert not (tmp_path / "proposal/proposal.json").exists()


def test_untyped_native_failure_is_not_misreported_as_an_infrastructure_diagnosis(tmp_path, monkeypatch):
    from devharness_core import watchdog
    proposal, _ = prepared(tmp_path)
    review = reviewed(tmp_path, proposal)
    begin(tmp_path, proposal, review)
    def untyped(*args, **kwargs):
        raise watchdog.RemoteWorkerError("ValueError: native failure without an origin classification")
    monkeypatch.setattr(watchdog, "run_sandboxed", untyped)
    result = api()[3](tmp_path / "bundle", tmp_path / "execution", allow_data_execution=True)
    assert result["status"] == "execution_failure_unclassified"
    assert result["failure_origin"] == "unknown"
    assert result["bundle_root"] is None
