"""Versioned experimental admission using declared units and observed preflight.

Only the current supplied-population contrast can use this policy. Retained
records never authorize execution, authenticate biological identity, or change
the generic inference policy. Each preflight is a synchronous MethodService
child of the occupied graph executor, with a persisted identity before dispatch.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path

from devbench.analysis.checkpoints import digest
from devbench.core.population_contract import (
    content_sha256,
    specification_sha256,
    validate_population_spec,
)
from devbench.engine.methods import _digest
from devharness_core.provenance import hash_artifact

BINDING_FORMAT = "devbench.scrna-population-input-binding/1"
POLICY = "devbench.scrna-declaration-conditioned-admission/1"
RECORD_KIND = "scrna_population_preflight"
STAGES = ("audit", "design", "pseudobulk")
CONTRAST = "scrna_population_contrast"
PARQUET_CONTRAST = "scrna_population_contrast_parquet"
CAPSULE = "scrna_population_pseudobulk_hdf5"
_BINARY_REQUEST = "devbench.scrna-population-preflight-request/2"
_LEGACY_REQUEST = "devbench.scrna-population-preflight-request/1"
ACTION_SCOPE = {
    "format": "devbench.method-action/1",
    "intent": "infer",
    "interpretation": "none",
    "exclusions": False,
}
REQUIREMENTS = (
    "input_integrity_valid",
    "observation_ids_unique",
    "metrics_finite",
    "design_identifiable",
    "scrna_population_preflight.declared_unit_mapping_consistent",
    "scrna_population_preflight.declaration_conditioned_measurement_supported",
)
DECLARATION_CONDITIONED_UNKNOWNS = frozenset(
    {
        "assay",
        "measurement",
        "count_provenance",
        "capture_unit_identity",
        "annotation_provenance",
    }
)
LIMITATION = (
    "Experimental research inference conditional on supplied biological-unit identity, "
    "independence, "
    "annotations and raw-count provenance; observed nesting and numeric checks do not "
    "authenticate those declarations."
)


def _validate_trial_identity(trial):
    if type(trial) is not dict or set(trial) != {
        "request_sha256",
        "project_id",
        "question_sha256",
        "source_sha256",
    }:
        raise ValueError(
            "Population trial requires the exact frozen request, project, question and "
            "source binding"
        )
    for key, item in trial.items():
        length = 32 if key == "project_id" else 64
        if (
            type(item) is not str
            or re.fullmatch("[0-9a-f]{" + str(length) + "}", item) is None
        ):
            raise ValueError("Population trial binding has an invalid identity: " + key)


def validate_population_binding(
    methods, value, *, task, data_classification, anchors=None
):
    from devbench.analysis.rna_scrna_input_contract import validate_scrna_declarations

    fields = {
        "format",
        "anndata_artifact_id",
        "declarations",
        "specification",
        "trial_binding",
    }
    if (
        type(value) is not dict
        or set(value) != fields
        or value["format"] != BINDING_FORMAT
    ):
        raise ValueError("Use the exact versioned supplied-population input binding")
    result = deepcopy(value)
    result["declarations"] = declarations = validate_scrna_declarations(
        value["declarations"]
    )
    result["specification"] = specification = validate_population_spec(
        value["specification"]
    )
    trial = result["trial_binding"]
    _validate_trial_identity(trial)
    independence = declarations["biological_independence"]
    if (
        declarations["assay"] != "scrna"
        or declarations["measurement"] != "umi_counts"
        or declarations["counts_layer"] != specification["counts_layer"]
        or independence is None
        or independence["independent"] is not True
        or independence["unit_column"] != specification["biological_unit_column"]
    ):
        raise ValueError(
            "Conditional admission requires explicit matching UMI-count and "
            "independent-unit declarations"
        )
    if declarations["capture_unit_key"] not in {
        specification["biological_unit_column"],
        *specification["technical_alias_columns"],
    }:
        raise ValueError(
            "Declared capture-unit identity must be among the observed "
            "biological-unit/alias mappings"
        )
    if data_classification not in {"public", "synthetic"}:
        raise ValueError(
            "Experimental supplied-population admission is limited to public or "
            "synthetic research data"
        )
    if task.get("scientific_objective") != specification["question"] or trial[
        "question_sha256"
    ] != digest(specification["question"]):
        raise ValueError(
            "Population question differs from the frozen scientific objective"
        )
    if anchors is not None and anchors["project_id"] != trial["project_id"]:
        raise ValueError("Population trial project differs from the scientific anchor")
    methods.artifact_path(result["anndata_artifact_id"])
    artifact = methods.get("artifact", result["anndata_artifact_id"])
    if (
        artifact["artifact_type"] != "anndata"
        or artifact["sha256"] != specification["source_sha256"]
        or trial["source_sha256"] != artifact["sha256"]
    ):
        raise ValueError(
            "Population source differs from the exact registered AnnData artifact"
        )
    return result


def retained_population_binding(plan, methods):
    value = plan.get("scrna_population_input")
    if value is None:
        return None
    actual = validate_population_binding(
        methods,
        value,
        task=plan["context"]["task"],
        data_classification=plan["data_classification"],
        anchors=plan.get("scientific_anchors"),
    )
    if actual != value or _digest(actual) != plan.get("scrna_population_input_sha256"):
        raise ValueError("Retained supplied-population binding changed")
    inspection = plan["context"]["dataset_summary"]["observed"].get("scrna_input", {})
    if (
        inspection.get("declarations") != actual["declarations"]
        or inspection.get("input_artifact_refs", {})
        .get("anndata", {})
        .get("artifact_id")
        != actual["anndata_artifact_id"]
        or set(plan["context"]["dataset_summary"].get("artifact_ids", []))
        != {actual["anndata_artifact_id"]}
    ):
        raise ValueError(
            "Population declaration or dataset differs from its original header binding"
        )
    return actual


def _request(plan, methods):
    from devharness_server.method_adapter import dispatcher_contract

    binding = retained_population_binding(plan, methods)
    if binding is None:
        raise ValueError(
            "Conditional preflight requires an explicit supplied-population binding"
        )
    owners = (
        "scrna_population_admission.py",
        "method_planner.py",
        "workflow_actions.py",
        "workflow_entrypoints.py",
        "rna_scrna_input_contract.py",
        "method_planner_contract.py",
        "methods.py",
        "method_executor.py",
        "method_validation.py",
    )
    root = Path(__file__).resolve().parents[1]
    sources = [f"analysis/{name}" for name in owners]
    sources.extend(
        (
            "engine/population_execution.py",
            "engine/method_provenance.py",
            "engine/method_executor.py",
            "engine/methods.py",
            "engine/workflow_operations.py",
            "platform/workflow_entrypoints.py",
            "platform/method_planner.py",
            "platform/workflow_actions.py",
            "platform/workflow_adaptive_actions.py",
            "platform/workflow_diagnostic_actions.py",
            "platform/workflow_serial_evidence.py",
            "platform/workflow_state.py",
            "platform/langgraph_workflow.py",
            "platform/scrna_population_admission.py",
        )
    )
    validator = {
        name: hash_artifact(root / name, max_bytes=1_000_000)["sha256"]
        for name in sources
    }
    return {
        "format": _BINARY_REQUEST,
        "policy": POLICY,
        "session_id": plan["session_id"],
        "binding_sha256": _digest(binding),
        "input_artifact_id": binding["anndata_artifact_id"],
        "source_sha256": binding["specification"]["source_sha256"],
        "specification_sha256": specification_sha256(binding["specification"]),
        "method_versions": {
            tool: dispatcher_contract(tool).version_id
            for tool in (
                "scrna_population_audit",
                "scrna_population_design",
                CAPSULE,
                CONTRAST,
                PARQUET_CONTRAST,
            )
        },
        "validator_sha256": _digest(validator),
    }


def _read_record(methods, session_id):
    with methods._db() as db:
        found = db.execute(
            "SELECT 1 FROM records WHERE kind=? AND id=?", (RECORD_KIND, session_id)
        ).fetchone()
        return methods._get(db, RECORD_KIND, session_id) if found else None


def _save_record(methods, record, *, actor, prior=None):
    with methods._db() as db:
        if (
            prior is not None
            and methods._get(db, RECORD_KIND, record["session_id"]) != prior
        ):
            raise ValueError("Population preflight changed during its occupied attempt")
        methods._put(
            db, RECORD_KIND, record["session_id"], record, update=prior is not None
        )
        methods._audit(
            db,
            actor,
            "scrna_population_preflight_" + record["status"],
            {
                "session_id": record["session_id"],
                "attempt_id": record["attempt_id"],
                "record_sha256": _digest(record),
            },
        )


def _result_json(path):
    """Respect the registered population output limit without selecting features."""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("An absolute preflight output path is required")
    hash_artifact(path, max_bytes=96_000_000, max_entries=1)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate preflight JSON key")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError("Preflight output requires finite JSON: " + value)

    def number(value):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else nonfinite(value)

    return json.loads(
        path.read_bytes(),
        object_pairs_hook=unique,
        parse_constant=nonfinite,
        parse_float=number,
    )


def _stage_tool(request, stage):
    return (
        CAPSULE
        if stage == "pseudobulk" and request["format"] == _BINARY_REQUEST
        else "scrna_population_" + stage
    )


def _receipt(methods, request, stage, attempt):
    run = methods.get("run", attempt["run_id"])
    tool = _stage_tool(request, stage)
    if (
        run["method_version_id"] != request["method_versions"][tool]
        or methods.method(run["method_version_id"])["contract"]["implementation"].get(
            "tool_name"
        )
        != tool
        or run["step_id"] != attempt["step_id"]
        or run["inputs"] != {"anndata": request["input_artifact_id"]}
        or specification_sha256(run["parameters"]["specification"])
        != request["specification_sha256"]
        or run["backend"] != "trusted_coordinator"
        or not methods.provenance(run["run_id"])["valid"]
    ):
        raise ValueError(
            "Population preflight run lacks exact current execution provenance"
        )
    receipt = {
        "stage": stage,
        "run_id": run["run_id"],
        "step_id": run["step_id"],
        "method_version_id": run["method_version_id"],
        "run_status": run["status"],
        "evidence_root": run["evidence"]["root_hash"],
        "output_ref": None,
        "failure": str(run.get("error", ""))[:2000]
        if run["status"] != "succeeded"
        else None,
    }
    if run["status"] != "succeeded":
        return receipt, None
    identity = run["outputs"].get("result")
    output = methods.get("artifact", identity)
    if output["generated_by"] != run["run_id"]:
        raise ValueError(
            "Population preflight output is not generated by its exact run"
        )
    receipt["output_ref"] = {
        key: output[key] for key in ("artifact_id", "sha256", "size_bytes")
    }
    body = _result_json(methods.artifact_path(identity))
    binary = tool == CAPSULE
    if (
        body.get("format")
        != "devbench.scrna-population-" + stage + ("/2" if binary else "/1")
        or body.get("source_sha256") != request["source_sha256"]
        or body.get("specification_sha256") != request["specification_sha256"]
        or not binary
        and specification_sha256(body.get("specification"))
        != request["specification_sha256"]
        or body.get("supervision", {}).get("cleanup", {}).get("verified_stopped")
        is not True
    ):
        raise ValueError(
            "Population preflight output identity or supervised cleanup differs"
        )
    if binary:
        capsule = methods.get("artifact", run["outputs"]["capsule"])
        matrix = body["pseudobulk"]["matrix"]
        if (
            capsule["generated_by"] != run["run_id"]
            or capsule["artifact_type"] != "file"
            or capsule["suffix"] != ".h5"
            or any(capsule[key] != matrix[key] for key in ("sha256", "size_bytes"))
        ):
            raise ValueError(
                "Population preflight lost its exact binary capsule receipt"
            )
        receipt["capsule_ref"] = {
            key: capsule[key] for key in ("artifact_id", "sha256", "size_bytes")
        }
    return receipt, body


def _design_failures(audit, design):
    failures = []
    if (
        any(
            audit.get(key) is not True
            for key in (
                "observation_ids_unique",
                "feature_ids_unique",
                "populations_disjoint",
            )
        )
        or audit.get("independence_verified") is not False
        or audit.get("biological_unit_identity_status") != "declared"
        or audit.get("feature_universe") != "all_stored_features_in_original_order"
    ):
        failures.append(
            "Observed identities/populations must be valid while unit authenticity "
            "remains unverified"
        )
    diagnostics = design.get("diagnostics", {})
    if (
        design.get("identifiable") is not True
        or design.get("refusal_codes")
        or diagnostics.get("condition_number_validated") is not True
        or type(diagnostics.get("residual_df")) is not int
        or diagnostics["residual_df"] < 2
        or diagnostics.get("rank") != len(design.get("columns", []))
        or design.get("dropped_protected_terms") != []
    ):
        failures.append(
            "Population design lacks full rank, condition validation or residual "
            "degrees of freedom"
        )
    if not design.get("contrasts") or any(
        row.get("supported") is not True
        or row.get("estimable") is not True
        or row.get("paired_unit_count", 0) < 2
        for row in design["contrasts"]
    ):
        failures.append(
            "Every protected stage-specific population contrast must be supported "
            "and estimable"
        )
    if any(
        row.get("one_to_one_biological_unit_alias") is not True
        for row in design.get("technical_alias_diagnostics", {}).values()
    ):
        failures.append(
            "Technical aliases are not one-to-one declared biological-unit aliases"
        )
    return failures


def _pseudobulk_failures(audit, design, pseudobulk, *, binary):
    failures = []
    validation = pseudobulk.get("count_validation", {})
    if any(
        validation.get(key) is not True
        for key in (
            "all_source_entries_validated",
            "sparse_storage_structure_validated",
            "finite",
            "integer_valued",
            "nonnegative",
        )
    ):
        failures.append(
            "Actual finite nonnegative integer raw-count validation is required"
        )
    if binary:
        if (
            any(
                pseudobulk.get(key) != audit.get(key)
                for key in (
                    "feature_ids_sha256",
                    "membership_sha256",
                    "observation_ids_sha256",
                )
            )
            or pseudobulk.get("profile_ids")
            != [row["profile_id"] for row in design["profiles"]]
            or (pseudobulk.get("matrix") or {}).get("shape")
            != [audit["feature_count"], len(design["profiles"])]
            or pseudobulk.get("missing_profile_count")
            != len(design["missing_combinations"])
        ):
            failures.append(
                "The binary capsule differs from the audited feature and profile scope"
            )
    elif content_sha256(pseudobulk.get("feature_ids")) != audit.get(
        "feature_ids_sha256"
    ) or len(pseudobulk.get("feature_ids", [])) != audit.get("feature_count"):
        failures.append("Pseudobulk does not retain the exact full feature universe")
    return failures


def _assess(request, receipts, bodies, *, error=None):
    failures = [] if error is None else [error]
    for stage, body in bodies.items():
        if body.get("status") != "completed":
            reasons = body.get("design", {}).get("refusal_codes", [])
            failures.append(
                "Registered "
                + stage
                + " refusal: "
                + "; ".join(reasons or [body.get("status", "unknown")])
            )
    if len(receipts) != 3 or set(bodies) != set(STAGES):
        failures.append("All three actual preflight output receipts are required")
    else:
        audit = bodies["audit"]["audit"]
        design = bodies["design"]["design"]
        pseudobulk = bodies["pseudobulk"].get("pseudobulk") or {}
        if any(body.get("status") != "completed" for body in bodies.values()):
            failures.append("A preflight stage refused the supplied population design")
        binary = _stage_tool(request, "pseudobulk") == CAPSULE
        if audit != bodies["design"]["audit"] or (
            not binary and audit != bodies["pseudobulk"]["audit"]
        ):
            failures.append(
                "Population, feature, cell or unit membership changed between "
                "preflight stages"
            )
        if not binary and design != bodies["pseudobulk"]["design"]:
            failures.append("Preflight design changed before raw-count aggregation")
        failures.extend(_design_failures(audit, design))
        failures.extend(_pseudobulk_failures(audit, design, pseudobulk, binary=binary))
    proof = {
        "format": "devbench.scrna-population-preflight-evidence/1",
        "policy": POLICY,
        "status": "input_error" if error else "refused" if failures else "passed",
        "request_sha256": _digest(request),
        "receipts": receipts,
        "failures": failures,
        "information_required": [],
        "independence_verified": False,
        "scope": LIMITATION,
    }
    if not failures:
        proof["observed_binding"] = {
            "membership_sha256": audit["membership_sha256"],
            "observation_ids_sha256": audit["observation_ids_sha256"],
            "feature_ids_sha256": audit["feature_ids_sha256"],
            "design_sha256": design["design_sha256"],
            "contrasts_sha256": content_sha256(design["contrasts"]),
            "biological_units_sha256": content_sha256(design["biological_unit_levels"]),
            "pseudobulk_sha256": pseudobulk["pseudobulk_sha256"],
        }
    return proof


_retained_synthesis_audit = ContextVar("retained_synthesis_audit", default=None)


@contextmanager
def retained_synthesis_audit(session, reference):
    token = _retained_synthesis_audit.set((session, reference))
    try:
        yield
    finally:
        _retained_synthesis_audit.reset(token)


def _retained_request(request, record, reference, record_ref):
    if reference != record_ref:
        raise ValueError(
            "Historical preflight is not bound to the retained audit receipt"
        )
    original = record["request"]
    if original["format"] not in {_BINARY_REQUEST, _LEGACY_REQUEST}:
        raise ValueError("Unsupported retained preflight request format")
    # Immutable MethodService receipts bind the original registered versions.
    # Their retained identities never authorize historical execution.
    return {
        **request,
        **{
            key: original[key]
            for key in (
                "validator_sha256",
                "format",
                "method_versions",
            )
        },
    }


def verify_population_preflight(plan, methods, *, historical_ref=None):
    request = _request(plan, methods)
    scoped = _retained_synthesis_audit.get()
    if (
        historical_ref is None
        and scoped is not None
        and scoped[0] == plan["session_id"]
    ):
        historical_ref = scoped[1]
    record = _read_record(methods, plan["session_id"])
    if record is None:
        raise ValueError("Supplied-population preflight has not executed")
    record_ref = {
        "kind": RECORD_KIND,
        "id": plan["session_id"],
        "sha256": _digest(record),
    }
    if historical_ref is not None:
        request = _retained_request(request, record, historical_ref, record_ref)
    if record["request"] != request:
        raise ValueError(
            "Population preflight binding, method or validator source changed; "
            "original receipts remain immutable"
        )
    if record["status"] != "completed":
        raise ValueError(
            "Population preflight attempt is occupied or uncertain; reconciliation is "
            "required, no redispatch"
        )
    receipts, bodies = [], {}
    for attempt in record["attempts"]:
        receipt, body = _receipt(methods, request, attempt["stage"], attempt)
        receipts.append(receipt)
        if body is not None:
            bodies[attempt["stage"]] = body
    actual = _assess(request, receipts, bodies, error=record.get("error"))
    if actual != record["evidence"]:
        raise ValueError("Retained population preflight evidence changed")
    return {**actual, "record_ref": record_ref}


def run_population_preflight(
    plan, methods, *, actor, allow_data_execution=False, cancellation_event=None
):
    """Run mandatory non-inferential checks once, in the current graph executor."""
    if allow_data_execution is not True:
        raise PermissionError(
            "Population preflight requires fresh explicit data-execution authority"
        )
    request = _request(plan, methods)
    if _read_record(methods, plan["session_id"]) is not None:
        return verify_population_preflight(plan, methods)
    record = {
        "format": "devbench.scrna-population-preflight/1",
        "session_id": plan["session_id"],
        "attempt_id": uuid.uuid4().hex,
        "request": request,
        "status": "running",
        "attempts": [],
        "evidence": None,
    }
    _save_record(methods, record, actor=actor)
    analysis = methods.create_analysis(
        "Supplied-population non-inferential preflight", actor=actor
    )
    branch = methods.create_branch(
        analysis["analysis_id"], analysis["revision_id"], actor=actor
    )
    binding = retained_population_binding(plan, methods)
    receipts, bodies, error = [], {}, None
    for stage in STAGES:
        if cancellation_event is not None and cancellation_event.is_set():
            error = "Population preflight cancelled before the next child dispatch"
            break
        tool = _stage_tool(request, stage)
        method = methods.register_dispatcher(tool, actor=actor)
        if method["version_id"] != request["method_versions"][tool]:
            raise ValueError("Population validator source changed before dispatch")
        step = methods.add_step(
            branch["branch_id"],
            method["version_id"],
            {"anndata": binding["anndata_artifact_id"]},
            {"specification": binding["specification"]},
            actor=actor,
        )
        prior = deepcopy(record)
        attempt = {
            "stage": stage,
            "step_id": step["step_id"],
            "run_id": uuid.uuid4().hex,
        }
        record["attempts"].append(attempt)
        _save_record(methods, record, actor=actor, prior=prior)
        # No nested queue and no injected worker: MethodService owns dispatch,
        # source/environment binding, supervised tool events and evidence roots.
        from devbench.engine.population_execution import run_population_step

        run_population_step(
            methods,
            step["step_id"],
            stage=stage,
            actor=actor,
            run_id=attempt["run_id"],
            allow_data_execution=allow_data_execution,
            cancellation_event=cancellation_event,
            binary_pseudobulk=tool == CAPSULE,
        )
        receipt, body = _receipt(methods, request, stage, attempt)
        receipts.append(receipt)
        if body is None:
            error = (
                "Registered " + tool + " failed; its failed run evidence is retained"
            )
            break
        bodies[stage] = body
        if body["status"] != "completed":
            # There is no reason to read counts for a design that already fails.
            break
    prior = deepcopy(record)
    record.update(
        status="completed",
        error=error,
        evidence=_assess(request, receipts, bodies, error=error),
    )
    _save_record(methods, record, actor=actor, prior=prior)
    return verify_population_preflight(plan, methods)


def conditional_population_applicability(plan, methods, contract, artifact_ids):
    """Return a verified narrow overlay; never mutate the retained header facts."""
    if not plan or plan.get("scrna_population_input") is None:
        return None
    from devharness_server.method_adapter import dispatcher_contract

    tool = contract.get("implementation", {}).get("tool_name")
    if (
        tool not in {CONTRAST, PARQUET_CONTRAST}
        or contract != dispatcher_contract(tool).to_dict()
    ):
        return None
    binding = retained_population_binding(plan, methods)
    if set(artifact_ids) != {binding["anndata_artifact_id"]}:
        raise ValueError("Population inference must retain the exact original input")
    evidence = verify_population_preflight(plan, methods)
    if evidence["status"] != "passed":
        raise ValueError(
            "Population preflight failed: " + "; ".join(evidence["failures"])
        )
    summary = {
        "policy": POLICY,
        "binding_sha256": _digest(binding),
        "preflight_record_ref": evidence["record_ref"],
        "input_sha256": binding["specification"]["source_sha256"],
        "specification_sha256": specification_sha256(binding["specification"]),
        "independence_verified": False,
        "scope": LIMITATION,
        "unverified_assumptions": sorted(DECLARATION_CONDITIONED_UNKNOWNS),
    }
    observations = {
        "input_integrity_valid": True,
        "observation_ids_unique": True,
        "metrics_finite": True,
        "design_identifiable": True,
        "scrna_population_preflight": {
            "declared_unit_mapping_consistent": True,
            "declaration_conditioned_measurement_supported": True,
        },
    }
    return summary, observations


def population_planning_context(plan, methods):
    """Expose scoped declarations/evidence, without replacing historical facts."""
    binding = retained_population_binding(plan, methods)
    context = {
        "policy": POLICY,
        "scope": LIMITATION,
        "independence_verified": False,
        "action_scope": deepcopy(ACTION_SCOPE),
        "minimum_requirements": list(REQUIREMENTS),
        "declaration_conditioned_unknowns": sorted(DECLARATION_CONDITIONED_UNKNOWNS),
        "binding_sha256": _digest(binding),
        "supplied_declarations": deepcopy(binding["declarations"]),
        "frozen_procedure": {
            "candidate_name": "scrna population contrast",
            "procedure_id": binding["specification"]["procedure"],
            "multiplicity": "One Benjamini-Hochberg family across all stored features "
            "and all declared stages.",
            "untestable_hypotheses": "Keep all rows in the family; unavailable p and "
            "q values stay null.",
        },
        "frozen_execution_binding": {
            "inputs": {"anndata": binding["anndata_artifact_id"]},
            "parameters": {"specification": deepcopy(binding["specification"])},
            "subset_kind": "full_dataset",
        },
        "interpretation": "The fixed assistant-proposed experimental procedure is "
        "evaluated for execution, not scientific superiority.",
    }
    observations = {}
    try:
        evidence = verify_population_preflight(plan, methods)
        context["preflight"] = evidence
        if evidence["status"] == "passed":
            observations = {
                "input_integrity_valid": True,
                "observation_ids_unique": True,
                "metrics_finite": True,
                "design_identifiable": True,
                "scrna_population_preflight": {
                    "declared_unit_mapping_consistent": True,
                    "declaration_conditioned_measurement_supported": True,
                },
            }
    except ValueError as error:
        context["preflight"] = {"status": "unavailable", "reason": str(error)}
    return context, observations


def require_population_execution(
    plan, methods, *, inputs, method_version_id, parameters, subset_spec
):
    if plan.get("scrna_population_input") is None:
        return
    binding = retained_population_binding(plan, methods)
    if (
        inputs != {"anndata": binding["anndata_artifact_id"]}
        or parameters != {"specification": binding["specification"]}
        or subset_spec.get("kind") != "full_dataset"
    ):
        raise ValueError(
            "Population execution must preserve the exact frozen input, cohort, units, "
            "contrasts and options"
        )
    contract = methods.method(method_version_id)["contract"]
    if (
        conditional_population_applicability(
            plan, methods, contract, list(inputs.values())
        )
        is None
    ):
        raise ValueError(
            "Conditional population execution is limited to the current registered "
            "contrast"
        )
