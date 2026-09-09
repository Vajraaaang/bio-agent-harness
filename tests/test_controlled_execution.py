"""Adversarial boundaries plus real supervised numerical execution on synthetic data."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from devharness_controlled.contracts import AnalysisPlan
from devharness_controlled.executor import execute_plan, _result_gates
from devharness_controlled.qualification import synthetic_case, synthetic_approvals, gaussian_projection_reference
from devharness_core.provenance import hash_artifact, verify_execution


def revised(plan, **changes):
    return AnalysisPlan(**{**plan.model_dump(), **changes})


def run(tmp_path, plan, expression, metadata, *, approvals=None, signers=None):
    if approvals is None:
        approvals, signers = synthetic_approvals(plan)
    outcome = execute_plan(plan, approvals, signers, counts_path=expression, metadata_path=metadata,
                           output_dir=tmp_path / "run")
    assert outcome["clinical_release_allowed"] is False
    assert outcome["audit_verification"]["valid"] is True
    saved = json.loads((tmp_path / "run" / "outcome.json").read_text())
    assert saved["status"] == outcome["status"]
    return outcome


def test_real_fixed_gaussian_and_preserved_inputs_match_independent_projection(tmp_path):
    plan, expression, metadata = synthetic_case(tmp_path / "fixture")
    outcome = run(tmp_path, plan, expression, metadata)
    assert outcome["status"] == "analysis_complete_pending_review", outcome
    numerical = json.loads((tmp_path / "run" / "numerical-result.json").read_text())
    expected = gaussian_projection_reference(expression, metadata)
    for gene, reference in expected.items():
        assert numerical["analysis"]["genes"][gene]["p_value"] == pytest.approx(reference["p_value"], abs=1e-12)
    assert numerical["input_validation"]["biological_unit_count"] == 24
    assert hash_artifact(tmp_path / "run" / "inputs" / "expression.zarr")["sha256"] == plan.counts_sha256
    assert (tmp_path / "run").stat().st_mode & 0o777 == 0o700
    manifest = json.loads((tmp_path / "run" / "audit" / "manifest.json").read_text())
    kinds = [json.loads((tmp_path / "run" / "audit" / "nodes" / (node + ".json")).read_text())["kind"]
             for node in manifest["node_ids"]]
    assert kinds == ["attempt_started", "plan_approvals_verified", "inputs_preserved",
                     "numerical_execution_started", "numerical_execution_returned", "attempt_finished"]
    # Corrupt a stored object; verification against a receipt retained by the test must fail.
    blob = next((tmp_path / "run" / "audit" / "objects").iterdir())
    blob.chmod(0o600)
    blob.write_bytes(b"tampered")
    assert not verify_execution(tmp_path / "run" / "audit" / "manifest.json", outcome["receipt"]["root_hash"])["valid"]


@pytest.mark.parametrize("change,expected", [
    ({"implementation_sha256": "0" * 64}, "implementation_sha256_mismatch"),
    ({"environment_sha256": "0" * 64}, "environment_sha256_mismatch"),
    ({"counts_sha256": "0" * 64}, "input_hash_mismatch"),
    ({"data_classification": "restricted_clinical"}, "clinical_admission_unconfigured"),
    ({"reference_sample_id": "S01"}, "reference_sample_order_mismatch"),
    ({"max_memory_mb": 1}, "memory"),
])
def test_signed_but_invalid_attempts_are_rejected_and_sealed(tmp_path, change, expected):
    plan, expression, metadata = synthetic_case(tmp_path / "fixture", **change)
    outcome = run(tmp_path, plan, expression, metadata)
    assert outcome["status"] == "failed"
    assert expected in outcome["error"].lower(), outcome


@pytest.mark.parametrize("mode,expected", [
    ("repeated", "repeated_biological_units"), ("blank", "missing_metadata"),
    ("unknown", "missing_biological_unit_identity"), ("not_available", "missing_biological_unit_identity"),
    ("nonnumeric", "nonnumeric_metadata"),
    ("duplicate_header", "duplicate_or_missing_metadata_columns"),
])
def test_invalid_sampling_metadata_rejected_before_modeling(tmp_path, mode, expected):
    plan, expression, metadata = synthetic_case(tmp_path / "fixture")
    frame = pd.read_csv(metadata)
    if mode == "duplicate_header":
        metadata.write_text(metadata.read_text().replace("subject_id", "sample_id", 1))
    else:
        if mode == "repeated":
            frame.loc[1, "subject_id"] = frame.loc[0, "subject_id"]
        elif mode == "blank":
            frame.loc[0, "subject_id"] = " "
        elif mode in {"unknown", "not_available"}:
            frame.loc[0, "subject_id"] = mode
        else:
            frame["time"] = frame["time"].astype(str)
            frame.loc[0, "time"] = "E10"
        frame.to_csv(metadata, index=False)
    plan = revised(plan, metadata_sha256=hash_artifact(metadata)["sha256"])
    outcome = run(tmp_path, plan, expression, metadata)
    assert outcome["status"] == "failed" and expected in outcome["error"]
    assert not (tmp_path / "run" / "numerical-result.json").exists()


def test_missing_second_approval_never_starts_worker(tmp_path, monkeypatch):
    plan, expression, metadata = synthetic_case(tmp_path / "fixture")
    approvals, signers = synthetic_approvals(plan)
    def forbidden(*args, **kwargs):
        pytest.fail("Worker should not execute without both approvals")
    monkeypatch.setattr("devharness_controlled.executor.run_sandboxed", forbidden)
    outcome = run(tmp_path, plan, expression, metadata, approvals=approvals[:1], signers=signers)
    assert outcome["status"] == "failed"


def test_external_hdf5_storage_is_not_admitted(tmp_path):
    import h5py
    plan, _, metadata = synthetic_case(tmp_path / "fixture")
    external = tmp_path / "external.h5"
    source = tmp_path / "linked.h5"
    with h5py.File(external, "w") as handle:
        handle["expression"] = np.ones((3, 24))
    with h5py.File(source, "w") as handle:
        handle["expression"] = h5py.ExternalLink(str(external), "/expression")
    plan = revised(plan, counts_sha256=hash_artifact(source)["sha256"])
    outcome = run(tmp_path, plan, source, metadata)
    assert outcome["status"] == "failed" and "unsupported_expression_format" in outcome["error"]


def test_locked_count_panel_cannot_change_with_requested_subset(tmp_path):
    plan, expression, metadata = synthetic_case(tmp_path / "fixture", "counts")
    plan = revised(plan, calibration_genes=plan.calibration_genes[:-1])
    outcome = run(tmp_path, plan, expression, metadata)
    assert outcome["status"] == "failed" and "locked_calibration_panel_mismatch" in outcome["error"]


def test_collinearity_rejected_without_dropping_covariate(tmp_path):
    plan, expression, metadata = synthetic_case(tmp_path / "fixture")
    frame = pd.read_csv(metadata)
    frame["batch"] = frame.time.astype(str)
    frame.to_csv(metadata, index=False)
    plan = revised(plan, metadata_sha256=hash_artifact(metadata)["sha256"], categorical_covariates=("batch",))
    outcome = run(tmp_path, plan, expression, metadata)
    assert outcome["status"] == "failed"
    assert not (tmp_path / "run" / "numerical-result.json").exists()


def test_no_information_is_retained_but_never_accepted(tmp_path):
    from devharness_core.storage import ChunkedExpressionStore
    plan, expression, metadata = synthetic_case(tmp_path / "fixture")
    source = tmp_path / "constant.csv"
    with ChunkedExpressionStore(expression) as store:
        pd.DataFrame(np.ones(store.shape), index=store.gene_names, columns=store.sample_names).to_csv(source)
    plan = revised(plan, counts_sha256=hash_artifact(source)["sha256"])
    outcome = run(tmp_path, plan, source, metadata)
    assert outcome["status"] == "failed" and "failed_or_uninformative_hypothesis" in outcome["error"]
    assert (tmp_path / "run" / "numerical-result.json").exists()


def test_run_directory_cannot_be_reused(tmp_path):
    plan, expression, metadata = synthetic_case(tmp_path / "fixture", counts_sha256="0" * 64)
    run(tmp_path, plan, expression, metadata)
    with pytest.raises(FileExistsError):
        run(tmp_path, plan, expression, metadata)


def test_numeric_looking_categorical_labels_are_preserved_in_actual_fit(tmp_path):
    plan, expression, metadata = synthetic_case(tmp_path / "fixture")
    frame = pd.read_csv(metadata)
    frame["batch"] = ["01", "1"] * 12
    frame.to_csv(metadata, index=False)
    plan = revised(plan, metadata_sha256=hash_artifact(metadata)["sha256"], categorical_covariates=("batch",))
    outcome = run(tmp_path, plan, expression, metadata)
    assert outcome["status"] == "analysis_complete_pending_review", outcome
    result = json.loads((tmp_path / "run" / "numerical-result.json").read_text())["analysis"]
    assert result["design"]["num_parameters"] == 5
    assert "C(batch)[T.1]" in result["genes"]["g000"]["coefficients"]


def test_malformed_signature_attempt_is_preserved(tmp_path):
    plan, expression, metadata = synthetic_case(tmp_path / "fixture")
    approvals, signers = synthetic_approvals(plan)
    raw = [approval.model_dump(mode="json") for approval in approvals]
    raw[0]["signature"] = "not_base64!"
    outcome = run(tmp_path, plan, expression, metadata, approvals=raw, signers=signers)
    assert outcome["status"] == "failed"
    assert json.loads((tmp_path / "run" / "approvals.json").read_text()) == raw


def test_pickle_zarr_codec_is_rejected_without_decoding(tmp_path):
    plan, expression, metadata = synthetic_case(tmp_path / "fixture")
    descriptor = expression / "expression" / ".zarray"
    spec = json.loads(descriptor.read_text())
    spec["dtype"] = "|O"
    spec["filters"] = [{"id": "pickle", "protocol": 4}]
    descriptor.write_text(json.dumps(spec))
    plan = revised(plan, counts_sha256=hash_artifact(expression)["sha256"])
    outcome = run(tmp_path, plan, expression, metadata)
    assert outcome["status"] == "failed" and "unapproved_zarr_encoding" in outcome["error"]


def test_zarr_chunk_separator_cannot_escape_snapshot(tmp_path):
    plan, expression, metadata = synthetic_case(tmp_path / "fixture")
    descriptor = expression / "expression" / ".zarray"
    spec = json.loads(descriptor.read_text())
    spec["dimension_separator"] = "/../../../../"
    descriptor.write_text(json.dumps(spec))
    plan = revised(plan, counts_sha256=hash_artifact(expression)["sha256"])
    outcome = run(tmp_path, plan, expression, metadata)
    assert outcome["status"] == "failed" and "unapproved_zarr_encoding" in outcome["error"]


def test_missing_independent_reference_cannot_report_verification_success(tmp_path, monkeypatch):
    from devharness_controlled import qualification
    case = {"outcome": {"status": "analysis_complete_pending_review"}, "repeat_numerical_sha256_equal": True,
            "projection_max_p_error": 0, "r_reference": {"status": "unavailable"}}
    monkeypatch.setattr(qualification, "run_verification", lambda output: {"cases": {"continuous": case, "counts": case}})
    monkeypatch.setattr("sys.argv", ["qualification", "--output", str(tmp_path / "out")])
    assert qualification.main() == 1


def test_real_count_worker_does_not_inherit_notebook_only_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("MPLBACKEND", "module://not_installed_in_isolated_environment")
    plan, expression, metadata = synthetic_case(tmp_path / "fixture", "counts")
    outcome = run(tmp_path, plan, expression, metadata)
    assert outcome["status"] == "analysis_complete_pending_review", outcome
    result = json.loads((tmp_path / "run" / "numerical-result.json").read_text())["analysis"]
    assert result["family"] == "negative_binomial" and result["dispersion_calibration"]
