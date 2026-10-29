"""Independent-review regressions on public local P5 fixtures only."""
from collections import Counter
import json
from pathlib import Path

import pytest

from tests.test_rna_workflow_evaluation import (
    api, cases, checked_outcomes, complete, failing_driver, identities,
    prepare, protocol, run,
)


@pytest.mark.parametrize("target", ["report.json", "report-seal.json", "manifest.json",
    "preparation-seal.json", "campaign-seal.json", "attempt.json", "call-0001.json", "checked-outcomes.json"])
def test_replay_hashes_and_parses_one_capture_per_artifact(tmp_path, monkeypatch, target):
    module, root, _, _, report = complete(tmp_path)
    original = module._bytes
    reads, swapped = Counter(), []
    def changing_read(path, maximum=module.MAX_RECORD):
        data = original(path, maximum)
        reads[str(path)] += 1
        if Path(path).name == target and not swapped:
            if target == "report.json":
                changed = json.loads(data)
                changed["denominator"] = 999999
                changed["scientific_improvement_claim"] = True
                Path(path).write_text(json.dumps(changed))
            else:
                Path(path).write_bytes(data + b" ")
            swapped.append(True)
        return data
    monkeypatch.setattr(module, "_bytes", changing_read)
    replay = module.replay_comparison(root, report["report_seal_sha256"])
    assert replay["valid"] is True
    assert replay["report"]["denominator"] == 18
    assert replay["report"]["scientific_improvement_claim"] is False
    assert swapped
    assert max(reads.values()) == 1


def test_checker_source_must_match_frozen_coordinator_check(tmp_path):
    module, _, prepared = prepare(tmp_path)
    receipt = run(module, prepared)
    outcomes = checked_outcomes(receipt)
    outcomes[0]["check_source_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="checker|check.*source|frozen.*check"):
        module.summarize_comparison(prepared, receipt, checked_outcomes=outcomes)


@pytest.mark.parametrize("endpoint", ["numeric_correctness", "claim_support", "replay_validity"])
def test_no_output_blocked_attempt_cannot_be_checked_as_numeric_success(tmp_path, endpoint):
    module, _, prepared = prepare(tmp_path, drivers=(failing_driver,) * 3)
    receipt = run(module, prepared, drivers=(failing_driver,) * 3)
    assert all(a["final_output"] is None for a in receipt["attempts"])
    outcomes = checked_outcomes(receipt)
    for outcome in outcomes:
        outcome["endpoints"][endpoint] = {"status": "assessed", "value": True, "evidence_sha256": "e" * 64}
    with pytest.raises(ValueError, match="output|completed|eligib"):
        module.summarize_comparison(prepared, receipt, checked_outcomes=outcomes)


def test_per_attempt_storage_forecast_rejects_unsealable_declared_maxima_before_execution(tmp_path):
    module = api()
    spec = protocol()
    spec["replicates"] = 1
    spec["identities"]["resources"].update(max_calls_per_attempt=32, max_driver_attempts=8,
        max_context_bytes=128000, max_output_bytes=64000, max_cost_usd=0)
    root = tmp_path / "unsealable-comparison"
    with pytest.raises(ValueError, match="storage|record|sealable"):
        module.prepare_comparison(root, protocol=spec, cases=cases(), driver_identities=identities(module))
    assert not root.exists()
