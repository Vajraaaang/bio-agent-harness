"""Exercise less common and invalid projections against the advertised schemas."""

import pytest
from jsonschema import Draft202012Validator

from devbench.platform.plugin_mcp_output import (
    PRIVATE_LAUNCH_HINT,
    output_schema,
    output_validator,
)

SHA = "c" * 64
SID = "a" * 32
HANDOFF = {"studio_url": None, "launch_hint": "devbench workbench --offline-only"}
INSPECTED = {
    "data_classification": "synthetic",
    "observed": {"file_type": "h5ad", "cells": 20, "genes": 12},
    "size_bytes": 8_192,
    "sha256": SHA,
    "header_sha256": None,
    "inspection_receipt_sha256": SHA,
}
RUN = {
    "session_id": SID,
    "run_id": "b" * 32,
    "status": "running",
    "compute_target": "local_worker",
    "progress": {"completed": 1, "total": 2},
    "tasks": [
        {
            "step": "qc",
            "target": "local_tier_a",
            "status": "succeeded",
            "resumed_from_checkpoint": True,
        }
    ],
    "warnings": [],
    "generation": 1,
    "stale": False,
    **HANDOFF,
}
ANSWER = {
    "status": "ready",
    "reason": "prerequisites_satisfied",
    "missing_measurements": [],
    "execution_route": "tier_a",
    "claim_scope": "checked_observation",
    "evidence_provenance": "server_attested_local_inputs",
    "design_evidence_status": "not_supplied",
    "registered_family_count": 20,
    "clinical_release_allowed": False,
    "attested_measurements": [{"kind": "gene_counts", "slot": "X"}],
    "measurement_basis": "verified_storage_and_declared_entry_point",
    "workflow_evidence_sha256": SHA,
    "count1_confirmation_sha256": SHA,
}
RECEIPT = {
    "session_id": SID,
    "run_id": "b" * 32,
    "operation": "single_cell",
    "probability_bounds_status": "not_evaluated",
    "clinical_release_allowed": False,
    "replay_bundle_sha256": SHA,
    "count1_confirmation_sha256": SHA,
    "source_bindings": {"input": {"artifact_id": "counts-v1", "sha256": SHA}},
    "tool_receipts": [
        {
            "step": "qc",
            "receipt_sha256": SHA,
            "outputs": {"filtered_counts": {"artifact_id": "filtered", "sha256": SHA}},
        }
    ],
    "summary_tables": [
        {"caption": "Cell summary", "columns": ["Retained"], "rows": [[True]]}
    ],
    "metrics": {"steps_completed": 1},
    **HANDOFF,
}


@pytest.mark.parametrize(
    ("tool", "result"),
    [
        ("inspect_rna_inputs", {"inputs": [INSPECTED]}),
        ("check_answerability", ANSWER),
        (
            "plan_rna_workflow",
            {
                "session_id": SID,
                "plan_id": SID,
                "plan_sha256": SHA,
                "status": "planned",
                "execution_route": "mcp",
                "answerability": ANSWER,
                "steps": [{"name": "matrix_audit", "state": "planned"}],
                "references": [],
                "inputs": [INSPECTED],
                "clinical_release_allowed": False,
                "runtime": {
                    "target": "local_worker",
                    "oci_digest": None,
                    "architecture": None,
                    "oci_runtime_verification": None,
                    "lockfile_sha256": SHA,
                    "network_policy": "offline",
                },
                **HANDOFF,
            },
        ),
        ("execute_reviewed_plan", RUN),
        ("get_run_status", RUN),
        ("get_execution_receipt", RECEIPT),
        ("manage_research_aim", {"session_id": SID, "revision": 0, "aims": []}),
    ],
)
def test_success_and_error_contracts_reject_undeclared_fields(tool, result):
    validator = output_validator(tool)
    assert validator is output_validator(tool)
    schema = output_schema(tool)
    Draft202012Validator.check_schema(schema)
    for definition in schema["$defs"].values():
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False, definition["title"]
    for payload in (result, {"error": "OperationBlocked", "reason": "invalid_request"}):
        validator.validate(payload)
        assert not validator.is_valid({**payload, "LEAKED_SENTINEL": "secret"})
    private_error = {
        "error": "OperationBlocked",
        "reason": "private_data_local_studio_only",
        "studio_url": None,
        "launch_hint": PRIVATE_LAUNCH_HINT,
    }
    validator.validate(private_error)
    assert not validator.is_valid(
        {**private_error, "launch_hint": "devbench --input-root /private/path"}
    )
    assert not validator.is_valid(
        {
            "error": "OperationBlocked",
            "reason": "invalid_request",
            "validation_errors": [
                {
                    "loc": "<field>",
                    "type": "extra_forbidden",
                    "input": "LEAKED_SENTINEL",
                }
            ],
        }
    )


def test_nested_measurements_tasks_cells_and_dynamic_ports_are_typed():
    assert not output_validator("check_answerability").is_valid(
        {
            **ANSWER,
            "attested_measurements": [{"kind": "gene_counts", "unexpected": 1}],
        }
    )
    assert not output_validator("get_run_status").is_valid(
        {
            **RUN,
            "tasks": [{**RUN["tasks"][0], "unexpected": 1}],
        }
    )
    receipt = output_validator("get_execution_receipt")
    assert not receipt.is_valid({**RECEIPT, "metrics": {"unknown_quantity": 1}})
    step = RECEIPT["tool_receipts"][0]
    assert not receipt.is_valid(
        {
            **RECEIPT,
            "tool_receipts": [
                {**step, "outputs": {"unexpected": {"artifact_id": "x", "sha256": SHA}}}
            ],
        }
    )
    assert not receipt.is_valid(
        {
            **RECEIPT,
            "summary_tables": [
                {"caption": "Test", "columns": ["a"], "rows": [[{"private": 1}]]}
            ],
        }
    )


def test_registered_receipt_ports_preserve_typed_artifact_versions():
    validator = output_validator("get_execution_receipt")
    version = {
        "version_id": "output-v1",
        "sha256": SHA,
        "kind": "table",
        "representation": "derived_summary",
        "biological_unit": "sample",
        "unit_membership_sha256": SHA,
    }
    receipt = {
        "session_id": SID,
        "run_id": "b" * 32,
        "operation": "registered_exploratory",
        "claim_scope": "exploratory_only",
        "metrics": {"outputs": 1},
        "probability_bounds_status": "not_evaluated",
        "clinical_release_allowed": False,
        "replay_bundle_sha256": SHA,
        "registered_method_outputs": {"qc": version},
        "graph_execution": {
            "step_id": "qc",
            "attempt_id": "q1",
            "accepted_record_id": "r1",
            "receipt_sha256": SHA,
            "result_sha256": SHA,
        },
        **HANDOFF,
    }
    validator.validate(receipt)
    for outputs in ({"not/a/port": version}, {"qc": {**version, "unexpected": 1}}):
        assert not validator.is_valid({**receipt, "registered_method_outputs": outputs})


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
        "query_receipt": {
            "format": "devbench.literature-query-receipt/1",
            "disclosure_reservation_id": SID,
            "snapshot_id": SHA,
            "query_sha256": SHA,
            "policy_sha256": SHA,
            "claim_binding_sha256": SHA,
            "content_identity_sha256": SHA,
        },
        "scope": (
            "untrusted_external_evidence_not_claim_support_or_numerical_validation"
        ),
        "failure": "TimeoutError",
    }
    validator.validate(result)
    assert not validator.is_valid({**result, "snapshot_id": None})
    assert not validator.is_valid({"status": "unavailable"})
    assert not validator.is_valid(
        {**result, "query_receipt": {**result["query_receipt"], "raw_query": "private"}}
    )
