"""Independent review regressions for identity, recovery and receipt consistency."""
import copy
import sqlite3
import uuid

import pytest

from tests.test_extensible_methods import FixtureExecutor
from tests.test_scientific_objects import setup_records, completed, request, correlation, science_class, PROJECT_ID


def test_missing_project_binding_can_recover_only_from_its_verified_backup(tmp_path):
    science, methods, *rest = setup_records(tmp_path)
    migration = rest[-1]
    with sqlite3.connect(methods.database) as db:
        db.execute("DELETE FROM scientific_metadata WHERE singleton=1")
    with pytest.raises(ValueError):
        science_class()(methods.root, PROJECT_ID)
    result = science_class().restore_backup(methods.root, migration["backup_path"], PROJECT_ID, actor="recovery-operator")
    assert result["recovered"] is True and result["revision"] == 0
    assert science_class()(methods.root, PROJECT_ID).snapshot()["project_id"] == PROJECT_ID


def test_invocation_environment_must_match_exact_method_definition(tmp_path):
    science, _, _, dataset, analysis, *_ = setup_records(tmp_path)
    with pytest.raises(ValueError, match="environment"):
        science.request_run(analysis["analysis_id"], analysis["current_version_id"], [dataset["current_version_id"]],
            environment={"invented_runtime": "different"}, trigger="explicit_trial", actor="researcher",
            expected_revision=analysis["revision"], idempotency_key="mismatched-environment")
    assert science.list("run") == []


@pytest.mark.parametrize("review_original", [False, True])
def test_repeated_original_publication_does_not_retarget_to_newer_interpretation(tmp_path, review_original):
    science, methods, _, dataset, analysis, step, *_ = setup_records(tmp_path)
    _, _, attempt, first = completed(science, methods, dataset, analysis, step)
    first_reviewed = science.review_result(first["result_id"], first["current_version_id"], "accepted", actor="reviewer",
        rationale="Reviewed the original interpretation", expected_revision=first["revision"]) if review_original else first
    newer = science.publish_result(attempt["attempt_id"], interpretation="A different interpretation", actor="researcher",
        expected_revision=attempt["revision"], result_id=first["result_id"], expected_result_revision=first_reviewed["revision"])
    science.review_result(newer["result_id"], newer["current_version_id"], "rejected", actor="another-reviewer",
        rationale="Reject the newer interpretation", expected_revision=newer["revision"])
    again = science.publish_result(attempt["attempt_id"], interpretation="Synthetic threshold comparison", actor="researcher",
        expected_revision=attempt["revision"])
    assert again["ref"] == first["ref"]
    assert again["current_ref"] == newer["ref"]
    assert again["review_status"] == ("accepted" if review_original else "candidate")
    assert again.get("review_id") == first_reviewed.get("review_id")
    assert science.get("result", first["result_id"])["ref"] == newer["ref"]
    assert "review_id" not in newer


def test_import_honors_explicit_step_inputs_within_multi_member_dataset(tmp_path):
    science, methods, study, dataset, analysis, step, manifest, definition, _ = setup_records(tmp_path)
    annotation = tmp_path / "annotation.json"
    annotation.write_text('{"annotation":"synthetic"}')
    artifact = methods.import_artifact(annotation, artifact_type="json", actor="researcher")
    manifest = copy.deepcopy(manifest)
    manifest["members"]["annotation"] = {"artifact_id": artifact["artifact_id"]}
    dataset = science.add_dataset_version(dataset["dataset_id"], manifest, actor="researcher", expected_revision=dataset["revision"])
    definition = copy.deepcopy(definition)
    definition.update(parameters={"qc": step["parameters"]}, steps=[{"step_key": "qc", "method_version_id": step["method_version_id"],
                      "parameters": step["parameters"], "inputs": step["inputs"]}])
    analysis = science.add_analysis_version(analysis["analysis_id"], definition, actor="researcher", expected_revision=analysis["revision"])
    native = methods.run_step(step["step_id"], actor="researcher", executor=FixtureExecutor())
    imported = science.record_import("method", native["run_id"], {"study_id": study["study_id"],
        "analysis_id": analysis["analysis_id"], "analysis_version_id": analysis["current_version_id"],
        "dataset_version_ids": [dataset["current_version_id"]]}, "imported", actor="researcher",
        expected_revision=science.snapshot()["revision"])
    assert imported["status"] == "imported"


def test_failed_native_receipt_cannot_be_accepted_using_inconsistent_validation_metadata(tmp_path):
    science, methods, _, dataset, analysis, step, *_ = setup_records(tmp_path)
    run = request(science, dataset, analysis)
    attempt = science.attach_attempt(run["run_id"], controller_kind="method", controller_id=uuid.uuid4().hex,
        correlation_id=run["correlation_id"], actor="researcher", expected_revision=run["revision"])
    class FailedExecutor:
        def execute(self, *args):
            return {"exit_code": 1, "error": "bounded fixture failure"}
    native = methods.run_step(step["step_id"], actor="researcher", executor=FailedExecutor(),
        run_id=attempt["controller_id"], correlation=correlation(run, attempt))
    assert native["status"] == "failed"
    attempt = science.finish_attempt(attempt["attempt_id"], actor="researcher", expected_revision=attempt["revision"])
    result = science.publish_result(attempt["attempt_id"], interpretation="Retain failed evidence", actor="researcher", expected_revision=attempt["revision"])
    version = science.resolve(result["ref"])
    with science._db(write=True) as db:
        validation = science._get(db, "validation", version["validation_id"])
        validation["status"] = "passed"
        science._put(db, "validation", validation["validation_id"], validation, update=True)
    with pytest.raises(ValueError, match="failed|Failed|successful|validation"):
        science.review_result(result["result_id"], result["current_version_id"], "accepted", actor="reviewer",
            rationale="Must reject inconsistent evidence", expected_revision=result["revision"])
    assert methods.provenance(native["run_id"])["valid"]
