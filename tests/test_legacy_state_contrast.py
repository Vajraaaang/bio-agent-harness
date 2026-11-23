"""Check role aliases separately from the fitted donor contrast."""

import csv
import os

import pytest

from tests.test_computed_contrasts import analyze, inputs


@pytest.mark.skipif(
    os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1",
    reason="Requires the authorized small local numerical fixtures",
)
def test_state_effect_uses_legacy_arm_names_only_in_the_declared_direction(
    workspace_tmp_path,
):
    task, paths = inputs(workspace_tmp_path, cellular=True, continuous=False)
    rename = {"A": "control", "B": "treated"}
    with paths["metadata"].open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        row["condition"] = rename[row["condition"]]
    with paths["metadata"].open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    question = task["questions"][0]
    question["contrast"] = "Condition B minus A within annotated cell type"
    task["context"]["condition_labels"] = ["control", "treated"]
    forward = analyze(task, paths, cellular=True)["target_results"][0]["payload"]
    assert forward["effect_definition"] == question["contrast"]
    assert forward["computed_contrast"]["comparison_condition"] == "treated"
    assert forward["computed_contrast"]["reference_condition"] == "control"

    task["context"]["requested_condition_contrast"] = {
        "numerator": "control",
        "denominator": "treated",
    }
    reverse = analyze(task, paths, cellular=True)["target_results"][0]["payload"]
    assert "control minus treated" in reverse["effect_definition"]
    assert reverse["computed_contrast"]["comparison_condition"] == "control"
    assert reverse["computed_contrast"]["reference_condition"] == "treated"
