"""Pure schema checks: advertised requirements must exist in the actual API."""
from devbench.study import biology_answers as answers
from devbench.study.biology import load_biology_catalog


def schema_fields(value):
    if isinstance(value, dict):
        return set(value.get("properties", {})) | set().union(*(schema_fields(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(schema_fields(item) for item in value))
    return set()


def test_advertised_fields_exist_in_each_targets_typed_answer():
    payloads = {"effects": answers.EffectsPayload, "events": answers.EventsPayload,
        "rhythm": answers.RhythmPayload, "concentration": answers.ConcentrationPayload,
        "fractions": answers.FractionsPayload, "graph": answers.GraphPayload,
        "ancestry": answers.AncestryPayload, "ordering": answers.OrderingPayload,
        "association": answers.AssociationPayload, "enrichment": answers.EnrichmentPayload,
        "diagnostics": answers.DiagnosticsPayload, "cell_state": answers.CellStatePayload,
        "restraint": answers.RestraintPayload}
    common = set(answers.TargetAnswer.model_fields)
    for template in load_biology_catalog().templates:
        for target in template.targets:
            fields = common | schema_fields(payloads[answers.TARGET_PAYLOAD_KINDS[target.target_id]].model_json_schema())
            assert set(target.required_answer_fields) <= fields, (target.target_id, set(target.required_answer_fields)-fields)
            assert set(target.statistical_reporting.required_gene_fields) <= set(answers.HypothesisEstimate.model_fields)
            assert "hypothesis_testable" not in target.required_answer_fields
