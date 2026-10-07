"""Bounded synthetic tests for shared identities and receipt-backed metadata."""
import copy
import importlib
import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from devharness_core.provenance import hash_artifact
from tests.test_extensible_methods import FixtureExecutor, prepared


PROJECT_ID = "a" * 32


def science_class():
    return importlib.import_module("devbench.analysis.scientific_objects").ScientificObjectService


def setup_records(tmp_path):
    methods, legacy, branch, step, *_ = prepared(tmp_path)
    cls = science_class()
    migration = cls.migrate(methods.root, PROJECT_ID, actor="researcher")
    science = cls(methods.root, PROJECT_ID, readonly=False)
    study = science.create_study("Study", aim="Bounded synthetic comparison", actor="researcher",
                                 expected_revision=science.snapshot()["revision"])
    manifest = {"members": {"dataset": {"artifact_id": step["inputs"]["dataset"]}},
                "metadata": {"biological_unit": "synthetic specimen", "data_type": "normalized"}}
    dataset = science.create_dataset(study["study_id"], "Dataset", manifest, actor="researcher",
                                     expected_revision=study["revision"])
    method = methods.method(step["method_version_id"])
    definition = {"method_version_ids": [step["method_version_id"]],
                  "code_identity": {step["method_version_id"]: method["contract"]["implementation"]},
                  "parameters": step["parameters"], "validation_policy": {step["method_version_id"]: method["contract"]["validation"]},
                  "environment": {step["method_version_id"]: method["contract"]["environment"]}}
    analysis = science.create_analysis(study["study_id"], "Analysis", definition, actor="researcher",
                                       expected_revision=study["revision"], legacy_analysis_id=legacy["analysis_id"])
    return science, methods, study, dataset, analysis, step, manifest, definition, migration


def request(science, dataset, analysis, key="one"):
    return science.request_run(analysis["analysis_id"], analysis["current_version_id"],
                               [dataset["current_version_id"]], environment=science.get("analysis_version", analysis["current_version_id"])["definition"].get("environment") or {"runtime": "synthetic_fixture"},
                               trigger="explicit_trial", actor="researcher",
                               expected_revision=analysis["revision"], idempotency_key=key)


def completed(science, methods, dataset, analysis, step):
    run = request(science, dataset, analysis)
    native_id = uuid.uuid4().hex
    attempt = science.attach_attempt(run["run_id"], controller_kind="method", controller_id=native_id,
                                     correlation_id=run["correlation_id"], actor="researcher",
                                     expected_revision=run["revision"])
    native = methods.run_step(step["step_id"], actor="researcher", executor=FixtureExecutor(), run_id=native_id,
                              correlation=correlation(run, attempt))
    attempt = science.finish_attempt(attempt["attempt_id"], actor="researcher", expected_revision=attempt["revision"])
    result = science.publish_result(attempt["attempt_id"], interpretation="Synthetic threshold comparison",
                                     actor="researcher", expected_revision=attempt["revision"])
    return run, native, attempt, result


def correlation(run, attempt):
    return {"project_id": run["project_id"], "scientific_run_id": run["run_id"],
            "attempt_id": attempt["attempt_id"], "analysis_version_id": run["analysis_version_id"],
            "dataset_version_ids": run["dataset_version_ids"]}


def test_migration_preserves_legacy_rows_and_retains_validated_backup(tmp_path):
    methods, legacy, *_ = prepared(tmp_path)
    with sqlite3.connect(methods.database) as db:
        prior = db.execute("SELECT * FROM records ORDER BY kind,id").fetchall()
        audit = db.execute("SELECT * FROM audit ORDER BY sequence").fetchall()
    migration = science_class().migrate(methods.root, "project-fixture", actor="researcher")
    with sqlite3.connect(methods.database) as db, sqlite3.connect(migration["backup_path"]) as backup:
        assert db.execute("SELECT * FROM records ORDER BY kind,id").fetchall() == prior
        assert db.execute("SELECT * FROM audit ORDER BY sequence").fetchall()[:len(audit)] == audit
        assert db.execute("SELECT version FROM schema_version").fetchall() == [(2,)]
        assert backup.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert backup.execute("SELECT * FROM records ORDER BY kind,id").fetchall() == prior
        assert backup.execute("SELECT version FROM schema_version").fetchall() == [(1,)]
    science = science_class()(methods.root, "project-fixture")
    assert science.list("analysis_version") == []
    assert methods.analysis(legacy["analysis_id"])["revision_id"] == legacy["revision_id"]
    assert science_class().migrate(methods.root, "project-fixture", actor="researcher")["migrated"] is False


def test_readonly_requires_explicit_initialization_and_cannot_rebind(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(ValueError, match="initialized|existing"):
        science_class()(missing, "project")
    assert not missing.exists()
    science, methods, *_ = setup_records(tmp_path)
    before = methods.database.read_bytes()
    readonly = science_class()(methods.root, PROJECT_ID)
    readonly.list("study")
    readonly.snapshot()
    with pytest.raises(PermissionError, match="read.only"):
        readonly.create_study("Denied", aim="None", actor="researcher", expected_revision=0)
    with pytest.raises(ValueError, match="project"):
        science_class()(methods.root, "other-project")
    with pytest.raises(ValueError, match="project"):
        science_class().migrate(methods.root, "other-project", actor="researcher")
    assert methods.database.read_bytes() == before


def test_unsupported_schema_is_not_migrated_or_reset(tmp_path):
    methods, *_ = prepared(tmp_path)
    with sqlite3.connect(methods.database) as db:
        db.execute("UPDATE schema_version SET version=999")
    before = methods.database.read_bytes()
    with pytest.raises(ValueError, match="schema"):
        science_class().migrate(methods.root, "project", actor="researcher")
    assert before == methods.database.read_bytes()


def test_dataset_identity_versions_and_location_are_separate(tmp_path):
    science, _, study, dataset, _, _, manifest, *_ = setup_records(tmp_path)
    original = dataset["current_version_id"]
    renamed = science.rename_dataset(dataset["dataset_id"], "Renamed", actor="researcher", expected_revision=dataset["revision"])
    moved = science.set_dataset_locations(dataset["dataset_id"], {"dataset": str(tmp_path / "moved.json")},
                                          actor="researcher", expected_revision=renamed["revision"])
    assert moved["current_version_id"] == original
    assert moved["dataset_id"] == dataset["dataset_id"]
    same = science.add_dataset_version(dataset["dataset_id"], manifest, actor="researcher", expected_revision=moved["revision"])
    assert same["current_version_id"] == original
    changed = copy.deepcopy(manifest)
    changed["metadata"]["biological_unit"] = "independent synthetic subject"
    next_version = science.add_dataset_version(dataset["dataset_id"], changed, actor="researcher", expected_revision=same["revision"])
    assert next_version["current_version_id"] != original
    exact = science.resolve({"object_type": "dataset", "object_id": dataset["dataset_id"], "version_id": original})
    assert exact["version_id"] == original
    assert exact["manifest"]["metadata"]["biological_unit"] == "synthetic specimen"
    duplicate = science.create_dataset(study["study_id"], "Distinct logical dataset", manifest,
                                       actor="researcher", expected_revision=study["revision"])
    assert duplicate["dataset_id"] != dataset["dataset_id"]
    assert duplicate["current_version_id"] != original


def test_dataset_changed_bytes_make_version_and_tampering_fails(tmp_path):
    science, methods, _, dataset, _, _, manifest, *_ = setup_records(tmp_path)
    data = tmp_path / "new.json"
    data.write_text('{"cells": 12}')
    artifact = methods.import_artifact(data, artifact_type="json", actor="researcher")
    changed = copy.deepcopy(manifest)
    changed["members"]["dataset"]["artifact_id"] = artifact["artifact_id"]
    revised = science.add_dataset_version(dataset["dataset_id"], changed, actor="researcher", expected_revision=dataset["revision"])
    assert revised["current_version_id"] != dataset["current_version_id"]
    retained = methods.artifact_path(artifact["artifact_id"])
    retained.chmod(0o600)
    retained.write_text("changed bytes")
    with pytest.raises(ValueError, match="artifact|hash"):
        science.resolve(revised["ref"])


def test_unchanged_scientific_definition_reuses_version_and_stale_writes_fail(tmp_path):
    science, _, _, _, analysis, _, _, definition, _ = setup_records(tmp_path)
    original = analysis["current_version_id"]
    same = science.add_analysis_version(analysis["analysis_id"], definition, actor="researcher", expected_revision=analysis["revision"])
    assert same["current_version_id"] == original
    changed = copy.deepcopy(definition)
    changed["parameters"]["threshold"] = 3
    revised = science.add_analysis_version(analysis["analysis_id"], changed, actor="researcher", expected_revision=same["revision"])
    assert revised["current_version_id"] != original
    with pytest.raises(ValueError, match="stale"):
        science.add_analysis_version(analysis["analysis_id"], definition, actor="researcher", expected_revision=analysis["revision"])
    assert science.resolve(analysis["ref"])["definition"] == definition


def test_invocation_deduplicates_same_key_and_preserves_separate_reruns(tmp_path):
    science, _, _, dataset, analysis, *_ = setup_records(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(lambda _: request(science, dataset, analysis), range(2)))
    assert runs[0]["run_id"] == runs[1]["run_id"]
    assert len(science.list("run")) == 1
    repeat = request(science, dataset, analysis, "rerun")
    assert repeat["run_id"] != runs[0]["run_id"]
    assert repeat["analysis_version_id"] == runs[0]["analysis_version_id"]
    with pytest.raises(ValueError, match="idempotency"):
        science.request_run(analysis["analysis_id"], analysis["current_version_id"], [dataset["current_version_id"]],
                             environment={"runtime": "other"}, trigger="explicit_trial", actor="researcher",
                             expected_revision=analysis["revision"], idempotency_key="one")


def test_attempt_missing_evidence_stays_unresolved_and_cannot_redispatch(tmp_path):
    science, _, _, dataset, analysis, *_ = setup_records(tmp_path)
    run = request(science, dataset, analysis)
    attempt = science.attach_attempt(run["run_id"], controller_kind="method", controller_id="not-yet-observed",
                                     correlation_id=run["correlation_id"], actor="researcher", expected_revision=run["revision"])
    with pytest.raises(ValueError, match="Unknown|evidence"):
        science.finish_attempt(attempt["attempt_id"], actor="researcher", expected_revision=attempt["revision"])
    assert science.get("attempt", attempt["attempt_id"])["status"] == "reserved"
    current = science.get("run", run["run_id"])
    with pytest.raises(ValueError, match="one.shot|attempt"):
        science.attach_attempt(run["run_id"], controller_kind="method", controller_id="second-dispatch",
                                 correlation_id=run["correlation_id"], actor="researcher", expected_revision=current["revision"])


def test_result_review_is_explicit_append_only_and_selection_is_scoped(tmp_path):
    science, methods, study, dataset, analysis, step, *_ = setup_records(tmp_path)
    _, native, attempt, result = completed(science, methods, dataset, analysis, step)
    assert result["review_status"] == "candidate"
    result_version = science.get("result_version", result["current_version_id"])
    assert result_version["artifact_ids"] == list(native["outputs"].values())
    validation = science.get("validation", result_version["validation_id"])
    assert validation["method_version_id"] == step["method_version_id"]
    assert validation["evidence"] == native["evidence"]
    with pytest.raises(ValueError, match="accepted"):
        science.select_result(study["study_id"], "primary", result["current_version_id"], actor="researcher",
                               rationale="Select", expected_revision=0)
    accepted = science.review_result(result["result_id"], result["current_version_id"], "accepted", actor="reviewer",
                                      rationale="Reviewed fixture evidence", expected_revision=result["revision"])
    first = science.select_result(study["study_id"], "primary", result["current_version_id"], actor="reviewer",
                                   rationale="Primary synthetic result", expected_revision=0)
    alternate = science.select_result(study["study_id"], "secondary", result["current_version_id"], actor="reviewer",
                                       rationale="Secondary use", expected_revision=0)
    assert first["selection_id"] != alternate["selection_id"]
    rejected = science.review_result(result["result_id"], result["current_version_id"], "rejected", actor="reviewer",
                                      rationale="Subsequent interpretation review", expected_revision=accepted["revision"])
    assert rejected["review_status"] == "rejected"
    assert [item["decision"] for item in science.list("review")] == ["accepted", "rejected"]
    assert len(science.list("attempt")) == 1


def test_atomic_review_and_analysis_version_rollback_with_method_transaction(tmp_path):
    science, methods, _, dataset, analysis, step, _, definition, _ = setup_records(tmp_path)
    _, _, _, result = completed(science, methods, dataset, analysis, step)
    before = science.snapshot()
    with pytest.raises(RuntimeError, match="interrupted"):
        with methods._db() as db:
            changed = copy.deepcopy(definition)
            changed["parameters"]["threshold"] = 3
            science.add_analysis_version(analysis["analysis_id"], changed, actor="reviewer", expected_revision=analysis["revision"], db=db)
            science.review_result(result["result_id"], result["current_version_id"], "accepted", actor="reviewer",
                                   rationale="Checked", expected_revision=result["revision"], db=db)
            raise RuntimeError("interrupted before native approval committed")
    assert science.snapshot() == before


def test_exact_version_must_exist_and_belong_to_logical_object(tmp_path):
    science, _, _, dataset, analysis, *_ = setup_records(tmp_path)
    with pytest.raises(ValueError, match="Unknown|unavailable"):
        science.resolve({"object_type": "dataset", "object_id": dataset["dataset_id"], "version_id": "missing"})
    with pytest.raises(ValueError, match="Unknown|belong"):
        science.resolve({"object_type": "dataset", "object_id": analysis["analysis_id"], "version_id": dataset["current_version_id"]})
    with pytest.raises(ValueError, match="version"):
        science.resolve({"object_type": "dataset", "object_id": dataset["dataset_id"], "version_id": None})


def test_failed_validation_is_retained_and_cannot_be_accepted(tmp_path):
    science, methods, _, dataset, analysis, step, *_ = setup_records(tmp_path)
    class Failure(FixtureExecutor):
        def execute(self, contract, inputs, parameters, source, lock, output_dir):
            return {"exit_code": 1, "runtime": "synthetic_fixture"}
    run = request(science, dataset, analysis)
    native_id = uuid.uuid4().hex
    attempt = science.attach_attempt(run["run_id"], controller_kind="method", controller_id=native_id,
                                     correlation_id=run["correlation_id"], actor="researcher", expected_revision=run["revision"])
    native = methods.run_step(step["step_id"], actor="researcher", executor=Failure(), run_id=native_id,
                              correlation=correlation(run, attempt))
    attempt = science.finish_attempt(attempt["attempt_id"], actor="researcher", expected_revision=attempt["revision"])
    result = science.publish_result(attempt["attempt_id"], interpretation="Failed synthetic trial", actor="researcher",
                                     expected_revision=attempt["revision"])
    assert science.get("validation", science.get("result_version", result["current_version_id"])["validation_id"])["status"] == "failed"
    with pytest.raises(ValueError, match="validation"):
        science.review_result(result["result_id"], result["current_version_id"], "accepted", actor="reviewer",
                               rationale="Not acceptable", expected_revision=result["revision"])


def test_explicit_candidate_slots_have_one_controller_attempt_each(tmp_path):
    science, methods, _, dataset, analysis, step, _, definition, _ = setup_records(tmp_path)
    definition["steps"] = [
        {"step_key": "a", "method_version_id": step["method_version_id"], "parameters": {"threshold": 4}},
        {"step_key": "b", "method_version_id": step["method_version_id"], "parameters": {"threshold": 3}},
    ]
    definition["parameters"] = {step["step_key"]: step["parameters"] for step in definition["steps"]}
    analysis = science.add_analysis_version(analysis["analysis_id"], definition, actor="researcher", expected_revision=analysis["revision"])
    run = request(science, dataset, analysis)
    first = science.attach_attempt(run["run_id"], controller_kind="method", controller_id=uuid.uuid4().hex,
                                   correlation_id=run["correlation_id"], step_key="a", actor="researcher", expected_revision=1)
    current = science.get("run", run["run_id"])
    second = science.attach_attempt(run["run_id"], controller_kind="method", controller_id=uuid.uuid4().hex,
                                    correlation_id=run["correlation_id"], step_key="b", actor="researcher", expected_revision=current["revision"])
    assert first["attempt_id"] != second["attempt_id"]
    assert {item["step_key"] for item in science.list("attempt")} == {"a", "b"}
    current = science.get("run", run["run_id"])
    with pytest.raises(ValueError, match="one.shot"):
        science.attach_attempt(run["run_id"], controller_kind="method", controller_id=uuid.uuid4().hex,
                                 correlation_id=run["correlation_id"], step_key="b", actor="researcher", expected_revision=current["revision"])
    with pytest.raises(ValueError, match="step_key"):
        science.attach_attempt(run["run_id"], controller_kind="method", controller_id=uuid.uuid4().hex,
                                 correlation_id=run["correlation_id"], step_key="not-declared", actor="researcher", expected_revision=current["revision"])


def test_current_invocation_cannot_claim_unrelated_historical_receipt(tmp_path):
    science, methods, _, dataset, analysis, step, *_ = setup_records(tmp_path)
    native = methods.run_step(step["step_id"], actor="researcher", executor=FixtureExecutor())
    run = request(science, dataset, analysis)
    attempt = science.attach_attempt(run["run_id"], controller_kind="method", controller_id=native["run_id"],
                                     correlation_id=run["correlation_id"], actor="researcher", expected_revision=run["revision"])
    with pytest.raises(ValueError, match="correlation|ownership"):
        science.finish_attempt(attempt["attempt_id"], actor="researcher", expected_revision=attempt["revision"])


def test_draft_definition_can_be_saved_but_never_dispatched(tmp_path):
    science, _, study, dataset, _, *_ = setup_records(tmp_path)
    draft = science.create_analysis(study["study_id"], "Unplanned", {
        "method_version_ids": [], "code_identity": {}, "parameters": {}, "validation_policy": {"status": "unplanned"},
        "task": {"question": "Which method is appropriate?"}}, actor="researcher", expected_revision=study["revision"])
    with pytest.raises(ValueError, match="unplanned"):
        request(science, dataset, draft)
    assert science.list("run") == []


def test_touch_advances_project_revision_with_transaction_rollback(tmp_path):
    science, methods, *_ = setup_records(tmp_path)
    before = science.snapshot()["revision"]
    revision = science.touch(actor="researcher", action="plan_created", payload={"session_id": "fixture"}, expected_revision=before)
    assert revision == before + 1
    with pytest.raises(ValueError, match="stale"):
        science.touch(actor="researcher", action="plan_created", payload={}, expected_revision=before)
    with pytest.raises(RuntimeError):
        with methods._db() as db:
            science.touch(actor="researcher", action="plan_updated", payload={}, db=db)
            raise RuntimeError("interrupt")
    assert science.snapshot()["revision"] == revision


def test_explicit_import_metadata_is_idempotent_and_failed_evidence_unresolved(tmp_path):
    science, methods, study, dataset, analysis, step, *_ = setup_records(tmp_path)
    unresolved = science.record_import("method", "missing-run", {}, "unresolved", actor="researcher",
                                        expected_revision=science.snapshot()["revision"])
    duplicate = science.record_import("method", "missing-run", {}, "unresolved", actor="researcher", expected_revision=0)
    assert unresolved["import_id"] == duplicate["import_id"]
    assert unresolved["status"] == "unresolved"
    with pytest.raises(ValueError, match="mapping|Unknown|evidence"):
        science.record_import("method", "missing-run", {}, "imported", actor="researcher",
                               expected_revision=science.snapshot()["revision"])
    native = methods.run_step(step["step_id"], actor="researcher", executor=FixtureExecutor())
    mappings = {"study_id": study["study_id"], "analysis_id": analysis["analysis_id"],
                "analysis_version_id": analysis["current_version_id"], "dataset_version_ids": [dataset["current_version_id"]]}
    imported = science.record_import("method", native["run_id"], mappings, "imported", actor="researcher",
                                      expected_revision=science.snapshot()["revision"])
    assert imported["source_evidence"] == native["evidence"]
    assert imported["source_id"] == native["run_id"]
    assert len(science.list("import_event")) == 2


def test_explicit_backup_recovery_quarantines_corruption_without_rewriting_legacy_rows(tmp_path):
    science, methods, _, _, _, _, _, _, migration = setup_records(tmp_path)
    with sqlite3.connect(migration["backup_path"]) as db:
        legacy_rows = db.execute("SELECT * FROM records ORDER BY kind,id").fetchall()
    damaged = b"invalid SQLite primary kept as evidence"
    methods.database.write_bytes(damaged)
    with pytest.raises(ValueError, match="schema|database"):
        science_class()(methods.root, PROJECT_ID)
    recovery = science_class().restore_backup(methods.root, migration["backup_path"], PROJECT_ID, actor="operator")
    from pathlib import Path
    assert Path(recovery["quarantine_path"]).read_bytes() == damaged
    assert recovery["recovered"] is True
    reopened = science_class()(methods.root, PROJECT_ID)
    assert reopened.snapshot()["studies"] == []
    with sqlite3.connect(methods.database) as db:
        assert db.execute("SELECT * FROM records ORDER BY kind,id").fetchall() == legacy_rows


def test_recovery_refuses_valid_primary_unknown_schema_and_identity_mismatch(tmp_path):
    _, methods, _, _, _, _, _, _, migration = setup_records(tmp_path)
    backup = migration["backup_path"]
    original = methods.database.read_bytes()
    with pytest.raises(ValueError, match="valid|rollback"):
        science_class().restore_backup(methods.root, backup, PROJECT_ID, actor="operator")
    with pytest.raises(ValueError, match="project|identity"):
        science_class().restore_backup(methods.root, backup, "b" * 32, actor="operator")
    assert methods.database.read_bytes() == original
    with sqlite3.connect(methods.database) as db:
        db.execute("UPDATE schema_version SET version=999")
    unknown = methods.database.read_bytes()
    with pytest.raises(ValueError, match="schema"):
        science_class().restore_backup(methods.root, backup, PROJECT_ID, actor="operator")
    assert methods.database.read_bytes() == unknown


def test_recovery_rejects_changed_backup_and_failed_swap_keeps_primary(tmp_path, monkeypatch):
    _, methods, _, _, _, _, _, _, migration = setup_records(tmp_path)
    from pathlib import Path
    backup = Path(migration["backup_path"])
    original_backup = backup.read_bytes()
    backup.write_bytes(original_backup + b"tampered")
    methods.database.write_bytes(b"invalid primary")
    with pytest.raises(ValueError, match="backup|digest|hash"):
        science_class().restore_backup(methods.root, backup, PROJECT_ID, actor="operator")
    backup.write_bytes(original_backup)
    module = importlib.import_module("devbench.analysis.scientific_objects")
    def interrupt(source, target):
        raise OSError("interrupted atomic replacement")
    monkeypatch.setattr(module.os, "replace", interrupt)
    with pytest.raises(OSError, match="interrupted"):
        science_class().restore_backup(methods.root, backup, PROJECT_ID, actor="operator")
    assert methods.database.read_bytes() == b"invalid primary"
    assert backup.read_bytes() == original_backup


def test_typed_legacy_link_metadata_rechecks_receipt_on_idempotent_import(tmp_path):
    science, methods, study, dataset, analysis, step, *_ = setup_records(tmp_path)
    native = methods.run_step(step["step_id"], actor="researcher", executor=FixtureExecutor())
    mappings = {"study_id": study["study_id"], "dataset_ref": dataset["ref"], "analysis_ref": analysis["ref"]}
    linked = science.record_import("method_run", native["run_id"], mappings, "linked", actor="researcher",
                                   expected_revision=science.snapshot()["revision"], evidence={"receipt": native["evidence"]})
    assert linked["status"] == "linked"
    assert linked["evidence"]["receipt"] == native["evidence"]
    artifact = methods.artifact_path(next(iter(native["outputs"].values())))
    artifact.chmod(0o600)
    artifact.write_text("modified output")
    with pytest.raises(ValueError, match="artifact|hash"):
        science.record_import("method_run", native["run_id"], mappings, "linked", actor="researcher",
                               expected_revision=science.snapshot()["revision"], evidence={"receipt": native["evidence"]})


def test_cosmetic_analysis_edit_does_not_change_scientific_version(tmp_path):
    science, methods, _, _, analysis, step, _, definition, _ = setup_records(tmp_path)
    renamed = science.rename_analysis(analysis["analysis_id"], "Display title", actor="researcher", expected_revision=analysis["revision"])
    assert renamed["current_version_id"] == analysis["current_version_id"]
    contract = copy.deepcopy(methods.method(step["method_version_id"])["contract"])
    source = tmp_path / "source.py"
    source.write_text("# New scientific code identity retained without execution\n")
    contract["implementation"]["source_sha256"] = hash_artifact(source)["sha256"]
    registered = methods.register_method(contract, source=source, environment_lock=tmp_path / "environment.lock", actor="researcher")
    definition["method_version_ids"] = [registered["version_id"]]
    definition["code_identity"] = {registered["version_id"]: registered["contract"]["implementation"]}
    definition["validation_policy"] = {registered["version_id"]: registered["contract"]["validation"]}
    definition["environment"] = {registered["version_id"]: registered["contract"]["environment"]}
    revised = science.add_analysis_version(analysis["analysis_id"], definition, actor="researcher", expected_revision=renamed["revision"])
    assert revised["current_version_id"] != analysis["current_version_id"]


def test_failed_attempt_retains_validation_record_before_result_interpretation(tmp_path):
    science, methods, _, dataset, analysis, step, *_ = setup_records(tmp_path)
    run = request(science, dataset, analysis)
    native_id = uuid.uuid4().hex
    attempt = science.attach_attempt(run["run_id"], controller_kind="method", controller_id=native_id,
                                     correlation_id=run["correlation_id"], actor="researcher", expected_revision=run["revision"])
    class FailedExecutor(FixtureExecutor):
        def execute(self, contract, inputs, parameters, source, lock, output_dir):
            return {"exit_code": 1, "runtime": "synthetic_fixture", "error": "fixture failure"}
    methods.run_step(step["step_id"], actor="researcher", executor=FailedExecutor(), run_id=native_id,
                      correlation=correlation(run, attempt))
    observed = science.finish_attempt(attempt["attempt_id"], actor="researcher", expected_revision=attempt["revision"])
    assert science.get("validation", observed["validation_id"])["status"] == "failed"
    assert science.list("result") == []


def test_version_content_identity_detects_changed_payload_with_recomputed_row_hash(tmp_path):
    science, methods, _, dataset, *_ = setup_records(tmp_path)
    version = science.get("dataset_version", dataset["current_version_id"])
    version["manifest"]["metadata"]["biological_unit"] = "tampered identity"
    with methods._db() as db:
        methods._put(db, "scientific_dataset_version", version["version_id"], version, update=True)
    with pytest.raises(ValueError, match="version|identity|digest"):
        science.resolve(dataset["ref"])


def test_new_interpretations_require_exact_result_revision_and_keep_prior_version(tmp_path):
    science, methods, _, dataset, analysis, step, *_ = setup_records(tmp_path)
    _, _, attempt, result = completed(science, methods, dataset, analysis, step)
    with pytest.raises(ValueError, match="revision"):
        science.publish_result(attempt["attempt_id"], interpretation="Updated interpretation", actor="researcher",
                                expected_revision=attempt["revision"], result_id=result["result_id"])
    updated = science.publish_result(attempt["attempt_id"], interpretation="Updated interpretation", actor="researcher",
                                     expected_revision=attempt["revision"], result_id=result["result_id"],
                                     expected_result_revision=result["revision"])
    assert updated["current_version_id"] != result["current_version_id"]
    assert science.resolve(result["ref"])["interpretation"] == "Synthetic threshold comparison"
    with pytest.raises(ValueError, match="stale"):
        science.review_result(result["result_id"], result["current_version_id"], "accepted", actor="reviewer",
                               rationale="Old proposal", expected_revision=result["revision"])


def test_selection_replacement_preserves_other_scope_and_prior_review_history(tmp_path):
    science, methods, study, dataset, analysis, step, *_ = setup_records(tmp_path)
    _, _, attempt, first = completed(science, methods, dataset, analysis, step)
    second = science.publish_result(attempt["attempt_id"], interpretation="Second scientific meaning", actor="researcher",
                                     expected_revision=attempt["revision"])
    for item in (first, second):
        science.review_result(item["result_id"], item["current_version_id"], "accepted", actor="reviewer",
                               rationale="Checked evidence", expected_revision=item["revision"])
    primary = science.select_result(study["study_id"], "primary", first["current_version_id"], actor="reviewer",
                                     rationale="Initial primary", expected_revision=0)
    secondary = science.select_result(study["study_id"], "secondary", first["current_version_id"], actor="reviewer",
                                       rationale="Separate scientific role", expected_revision=0)
    replacement = science.select_result(study["study_id"], "primary", second["current_version_id"], actor="reviewer",
                                         rationale="Reviewed replacement", expected_revision=primary["revision"])
    assert replacement["selection_id"] == primary["selection_id"]
    assert science.get("selection", secondary["selection_id"])["result_version_id"] == first["current_version_id"]
    assert science.get("result", first["result_id"])["review_status"] == "accepted"
    assert science.list("selection_event")[-1]["superseded_result_version_id"] == first["current_version_id"]
    assert len(methods.list_records("run")) == 1


def test_interrupted_migration_rolls_back_primary_and_retains_backup(tmp_path, monkeypatch):
    methods, *_ = prepared(tmp_path)
    with sqlite3.connect(methods.database) as db:
        original_rows = db.execute("SELECT * FROM records ORDER BY kind,id").fetchall()
    def interrupted(*args, **kwargs):
        raise RuntimeError("interrupted migration")
    monkeypatch.setattr(type(methods), "_audit", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        science_class().migrate(methods.root, PROJECT_ID, actor="operator")
    with sqlite3.connect(methods.database) as db:
        assert db.execute("SELECT version FROM schema_version").fetchall() == [(1,)]
        assert db.execute("SELECT * FROM records ORDER BY kind,id").fetchall() == original_rows
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='scientific_metadata'").fetchone()
    assert len(list(methods.root.glob("methods.before-scientific-v2-*.sqlite3"))) == 1


@pytest.mark.parametrize("field", ["code_identity", "environment", "validation_policy"])
def test_execution_definition_cannot_claim_unregistered_scientific_metadata(tmp_path, field):
    science, _, _, _, analysis, _, _, definition, _ = setup_records(tmp_path)
    definition[field] = {definition["method_version_ids"][0]: {"invented": "unsupported"}}
    with pytest.raises(ValueError, match="retained|contract|identity"):
        science.add_analysis_version(analysis["analysis_id"], definition, actor="researcher", expected_revision=analysis["revision"])
    assert science.get("analysis", analysis["analysis_id"]) == analysis
