"""Exercise less common and invalid projections against the advertised schemas."""

from jsonschema import Draft202012Validator

from devbench.platform.plugin_mcp_output import output_schema


def test_private_plan_schema_limits_the_handoff_to_safe_fields():
    validator = Draft202012Validator(output_schema("plan_rna_workflow"))
    handoff = {
        "status": "local_studio_only",
        "session_id": "a" * 32,
        "plan_id": "b" * 32,
        "plan_sha256": "c" * 64,
        "execution_route": None,
        "execution_blocker": "private_plan_execution_unavailable",
        "studio_url": None,
        "launch_hint": "devbench workbench --offline-only",
    }
    validator.validate(handoff)
    assert not validator.is_valid({**handoff, "inputs": []})
    assert not validator.is_valid({**handoff, "execution_route": "mcp"})


def test_literature_snapshot_schema_validates_unavailable_outcome():
    validator = Draft202012Validator(output_schema("manage_research_aim"))
    result = {
        "status": "unavailable",
        "snapshot_id": "c" * 64,
        "query_receipt": {"format": "devbench.literature-query-receipt/1"},
        "scope": (
            "untrusted_external_evidence_not_claim_support_or_numerical_validation"
        ),
        "failure": "TimeoutError",
    }
    validator.validate(result)
    assert not validator.is_valid({**result, "snapshot_id": None})
    assert not validator.is_valid({"status": "unavailable"})
