"""Schema-only checks; no biological records are fitted, generated or graded."""
import importlib.util


def schema():
    assert importlib.util.find_spec("devbench.study.answers"), "Study answer schema is missing"
    from devbench.study.answers import StudyAnswer
    return StudyAnswer.model_json_schema()


def test_answer_does_not_require_fabricated_regulators():
    doc=schema()
    assert "master_regulators" not in doc["properties"]
    assert "gene_statistics" in doc["properties"]
    assert "diagnostics" in doc["properties"]
    assert doc["additionalProperties"] is False


def test_estimates_have_explicit_missingness_and_measurement_scope():
    estimate=schema()["$defs"]["GeneEstimate"]
    assert "status" in estimate["required"]
    assert "missing_reason" in estimate["properties"]
    assert "effect_units" in estimate["properties"]
    assert "interval_scope" in estimate["properties"]


def test_tool_use_and_free_text_are_not_self_verified():
    doc=schema()
    assert "verified" not in doc["properties"]
    assert "workflow_passed" not in doc["properties"]
    assert "unverified_narrative" in doc["properties"]
    assert "evidence_ids" in doc["properties"]
