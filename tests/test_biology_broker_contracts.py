"""Public configuration and default-denial checks; no data or tool calls."""
import pytest

from devbench.study.biology_broker import (BiologyBroker, biology_tool_menu,
                                          validate_biology_request, verify_biology_workflow)


def public_task():
    return {"measurement": "raw_counts", "population_unit_label": "donor_id",
            "context": {"biological_units": {"population_unit": "donor", "population_unit_label": "donor_id",
                "specimen_label": "specimen_id", "library_label": "library_id"},
                "design": {"condition_levels": ["control", "treated"], "time_grid": [0.0, 1.0, 2.0]}},
            "analysis_columns": {"condition": "condition", "time": "numeric_time", "dose": None,
                "covariate": None, "population_unit": "donor_id", "batches": ["batch"]},
            "analysis_reference_time": 0.0,
            "questions": [{"target_id": "mean_contrast", "estimand": "Condition contrast", "units": "log2 change",
                "contrast": "treated minus control", "reference_population": "declared independent donors",
                "prediction_reference": "observed design"}]}


def test_biology_broker_and_binding_are_disabled_before_inputs():
    with pytest.raises(PermissionError, match="disabled"):
        BiologyBroker(None, None, None, public_task=None)
    with pytest.raises(PermissionError, match="disabled"):
        verify_biology_workflow(None, None, None)


def test_menu_requires_only_public_questions_and_contains_actual_checks():
    names = biology_tool_menu(public_task())
    assert {"audit_expression_quality", "validate_biological_units", "validate_design_matrix",
            "mean_condition_analysis"} <= names.keys()
    assert "periodic_trajectory_analysis" not in names


@pytest.mark.parametrize("options", [{"condition_col": "batch"}, {"batch_cols": []},
    {"reference_condition": "treated"}, {"comparison_condition": "control"},
    {"time_col": "unrelated"}, {"reference_time": 2.0}, {"minimum_units": 1}])
def test_enforced_options_cannot_change_the_assigned_question(options):
    with pytest.raises(ValueError):
        validate_biology_request("mean_condition_analysis", {"options": options}, public_task(), enforced=True)


def test_optional_exploration_has_no_forced_adoption_or_fixed_contrast_gate():
    assert validate_biology_request("mean_condition_analysis", {"options": {"reference_condition": "treated"}},
                                    public_task(), enforced=False) == {"reference_condition": "treated"}


def test_participant_cannot_supply_input_paths_or_private_context():
    with pytest.raises(ValueError, match="coordinator-bound"):
        validate_biology_request("mean_condition_analysis", {"counts_path": "/outside"}, public_task(), enforced=False)
@pytest.mark.parametrize("status", ["analysis_failed", "support_unavailable", "failed"])
def test_transport_success_does_not_certify_an_incomplete_prerequisite(status):
    from devbench.study.biology_broker import _audit_completed
    event = {"status": "succeeded", "receipt_id": "protocol-only-placeholder"}
    results = {event["receipt_id"]: {"diagnostics": {"status": status}}}
    assert not _audit_completed(event, results)
