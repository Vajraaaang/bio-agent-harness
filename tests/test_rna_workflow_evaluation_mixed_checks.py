"""Different frozen scientific checkers can assess one completed attempt."""
import pytest

from tests.test_rna_workflow_evaluation import (
    api, checked_outcomes, prepare, protocol, run,
)


def mixed_campaign(tmp_path):
    module = api()
    spec = protocol()
    spec["coordinator_checks"]["claim_support"] = module.driver_identity(module._example_outcomes)
    assert (spec["coordinator_checks"]["numeric_correctness"]["source_sha256"]
        != spec["coordinator_checks"]["claim_support"]["source_sha256"])
    _, root, prepared = prepare(tmp_path, spec=spec)
    receipt = run(module, prepared)
    records = checked_outcomes(receipt)
    for record in records:
        for name, endpoint in record["endpoints"].items():
            if endpoint["status"] == "assessed":
                endpoint["check_source_sha256"] = spec["coordinator_checks"][name]["source_sha256"]
        record["error_check_source_sha256"] = spec["coordinator_checks"][spec["primary_endpoint"]]["source_sha256"]
    return module, root, prepared, receipt, records, spec


@pytest.mark.parametrize("global_fallback", [True, False])
def test_completed_attempt_accepts_separately_bound_numeric_and_claim_checkers(tmp_path, global_fallback):
    module, root, prepared, receipt, records, _ = mixed_campaign(tmp_path)
    if not global_fallback:
        for record in records:
            record.pop("check_source_sha256")
    report = module.summarize_comparison(prepared, receipt, checked_outcomes=records)
    assert report["denominator"] == 18
    for arm in report["arms"].values():
        assert arm["endpoints"]["numeric_correctness"]["assessed"] == 6
        assert arm["endpoints"]["claim_support"]["assessed"] == 6
    assert report["arms"]["analyst_plus_reviewer"]["errors"]["introduced"] == 2
    replay = module.replay_comparison(root, report["report_seal_sha256"])
    assert replay["valid"] is True


def test_endpoint_checker_cannot_borrow_another_frozen_checkers_source(tmp_path):
    module, _, prepared, receipt, records, spec = mixed_campaign(tmp_path)
    records[0]["endpoints"]["claim_support"]["check_source_sha256"] = spec["coordinator_checks"]["numeric_correctness"]["source_sha256"]
    with pytest.raises(ValueError, match="endpoint.*checker|appropriate checker"):
        module.summarize_comparison(prepared, receipt, checked_outcomes=records)


def test_error_labels_require_their_separate_primary_checker_binding(tmp_path):
    module, _, prepared, receipt, records, spec = mixed_campaign(tmp_path)
    records[0]["error_check_source_sha256"] = spec["coordinator_checks"]["claim_support"]["source_sha256"]
    with pytest.raises(ValueError, match="Error labels|primary.*checker"):
        module.summarize_comparison(prepared, receipt, checked_outcomes=records)


def test_missing_endpoint_binding_cannot_fall_back_to_unrelated_global_checker(tmp_path):
    module, _, prepared, receipt, records, _ = mixed_campaign(tmp_path)
    records[0]["endpoints"]["claim_support"].pop("check_source_sha256")
    with pytest.raises(ValueError, match="endpoint.*checker|appropriate checker"):
        module.summarize_comparison(prepared, receipt, checked_outcomes=records)
