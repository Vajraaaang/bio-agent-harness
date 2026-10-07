"""Offline comparison preparation; no participant, data or protected truth access."""
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[1]


def api():
    name = "scripts.prepare_workbench_evaluation"
    assert importlib.util.find_spec(name), "The frozen workbench comparison package is missing"
    from scripts.prepare_workbench_evaluation import prepare, verify
    return prepare, verify


@pytest.fixture
def destination():
    parent = ROOT / "tmp"
    parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="evaluation-readiness-", dir=parent) as directory:
        yield Path(directory) / "package"


def test_preparation_never_launches_processes_or_generates_data(destination, monkeypatch):
    import subprocess
    def forbidden(*args, **kwargs):
        pytest.fail("Configuration preparation must not execute processes or data")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    from devbench.study import biology_bulk, biology_singlecell, public_study
    monkeypatch.setattr(public_study, "prepare_public_task", forbidden)
    # Patch all public generation entrypoints without invoking them.
    for module in (biology_bulk, biology_singlecell):
        for name in vars(module):
            if name.startswith("generate_") and callable(getattr(module, name)):
                monkeypatch.setattr(module, name, forbidden)
    prepare, verify = api()
    receipt = prepare(destination)
    record = verify(destination / "audit/manifest.json", receipt["root_hash"])
    assert record["status"] == "offline_readiness_not_launchable"
    assert record["execution"] == {"data_executed": False, "provider_sessions": 0,
                                   "scientific_performance": None, "usage": None}
    assert not (destination / "data").exists()


def test_frozen_plans_use_existing_contracts_and_matched_three_arm_slots(destination):
    from devbench.study.biology_planning import BiologyStudyPlan, schedule_biology_attempts
    from devbench.study.public_study import PublicStudyPlan, schedule_public_attempts
    prepare, verify = api()
    receipt = prepare(destination)
    record = verify(destination / "audit/manifest.json", receipt["root_hash"])
    for name, cls, schedule in (("synthetic", BiologyStudyPlan, schedule_biology_attempts),
                                ("public", PublicStudyPlan, schedule_public_attempts)):
        plan = cls.model_validate_json(json.dumps(record["plans"][name]))
        assert plan.requested_model is None
        slots = [x.model_dump(mode="json") if hasattr(x, "model_dump") else x for x in schedule(plan)]
        assert slots == record["schedules"][name]
        grouped = {}
        for slot in slots:
            key = (slot.get("task_identity", slot.get("task_id")), slot["repetition"])
            grouped.setdefault(key, []).append(slot["condition"])
        assert all(sorted(arms) == ["baseline", "enforced", "optional"] for arms in grouped.values())
    assert all(row["generation_seed"] is None for row in record["plans"]["synthetic"]["recipes"])
    assert record["protocol"]["reference_scopes"] == ["synthetic_truth", "public_reference_concordance", "experimental_technical_controls"]


def test_protocol_preserves_scope_correctness_acceptance_and_missing_values(destination):
    prepare, verify = api()
    receipt = prepare(destination)
    record = verify(destination / "audit/manifest.json", receipt["root_hash"])
    protocol = record["protocol"]
    assert protocol["comparisons"]["optional-baseline"] == "availability"
    assert protocol["comparisons"]["enforced-optional"] == "enforcement_with_other_factors_matched"
    assert protocol["comparisons"]["enforced-baseline"] == "total_system_intervention"
    assert protocol["reviewer_intervention"] == "none; outcome review held constant across arms"
    assert {"raw_scientific_correctness", "harness_acceptance", "invalid_acceptance",
            "unnecessary_rejection", "appropriate_refusal", "appropriate_clarification"} <= set(protocol["outcomes"])
    assert "unknown_is_not_zero" in protocol["missingness_rules"]
    assert record["route_readiness"]["backed_scrna"]["live_three_arm_dispatch"] is False
    assert "authorization" in record["launch_prerequisites"]
    assert "scientific_precision_design" in record["launch_prerequisites"]
    assert "e11" not in record["plans"]["public"]["datasets"]


def test_tampered_package_fails_against_original_receipt(destination):
    prepare, verify = api()
    receipt = prepare(destination)
    path = destination / "comparison.json"
    payload = json.loads(path.read_text())
    payload["execution"]["scientific_performance"] = 1
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        verify(destination / "audit/manifest.json", receipt["root_hash"])


def test_existing_package_cannot_be_overwritten(destination):
    prepare, _ = api()
    prepare(destination)
    before = (destination / "comparison.json").read_bytes()
    with pytest.raises(FileExistsError):
        prepare(destination)
    assert (destination / "comparison.json").read_bytes() == before
