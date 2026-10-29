"""Prepare a fresh synthetic three-stage Codex generation HOLD packet.

This command only creates local fixture/evidence records. It has no launch,
approval, signing, retry, resume, numerical execution or provider API mode.
Run it with the ordinary installed wheel's Python -I -B after installation.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import sys

from devharness_core.provenance import canonical_json


STAGES = ("method_scientific_planning", "method_capability_resolution", "method_trial_design")
POLICY = {"format": "devbench.model-job-budget-policy/1", "kind": "generation_jobs",
    "max_jobs": 3, "max_concurrent_jobs": 1, "max_total_seconds": 300,
    "max_context_bytes": 32768, "max_output_bytes": 131072, "max_children": 0,
    "reconciliation": {"jobs": 3, "seconds": 225, "context_bytes": 32768}}
_BOUNDARY_FIELDS = {"packet_only_command_filesystem_verified", "command_network_denial_verified",
    "ambient_user_config_ignored_for_sandbox_verified", "ambient_rules_ignored_for_sandbox_verified",
    "native_tool_deny_verified"}
_PUBLIC_CLIENT_ROOT = Path("/opt/homebrew/lib/node_modules/@openai/codex")


def _path(value):
    path = Path(value)
    if not path.is_absolute() or path.resolve() != path or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Use an absolute ordinary path without symlinks")
    return path


def _sha(path, maximum=1_000_000):
    path = _path(path)
    if not path.is_file():
        raise ValueError("Evidence must be an ordinary file")
    hashed, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(min(65536, maximum + 1 - size)):
            size += len(chunk)
            if size > maximum:
                raise ValueError("Evidence exceeds its byte bound")
            hashed.update(chunk)
    return hashed.hexdigest(), size


def source_ref(path, workspace):
    path, workspace = _path(path), _path(workspace)
    if not path.is_relative_to(workspace):
        raise ValueError("Evidence must remain inside the assigned workspace")
    hashed, size = _sha(path)
    return {"path": str(path), "sha256": hashed, "size_bytes": size}


def _read(path, workspace):
    source_ref(path, workspace)
    from devbench.study.evidence import decode_json
    return decode_json(_path(path).read_bytes())


def _write(path, value):
    body = value.encode() if isinstance(value, str) else canonical_json(value)
    with path.open("xb") as stream:
        stream.write(body)
    path.chmod(0o444)
    return path


def _installed_runtime(path, workspace):
    """Bind actual ordinary installed custody; a historical JSON label is insufficient."""
    from devbench.analysis.rna_trial_authority import (
        trial_enforcement_source_digest, trial_enforcement_runtime_digest,
        verify_installed_distribution_custody,
    )
    binding = _read(path, workspace)
    fields = {"format", "source_manifest_sha256", "runtime_prefix", "distribution", "wheel_path",
        "wheel_sha256", "record_sha256", "runtime_manifest_path", "runtime_manifest_sha256"}
    if (type(binding) is not dict or set(binding) != fields
            or binding["format"] != "devbench.rna-tracing-runtime/1" or binding["distribution"] != "devharness"):
        raise ValueError("Invalid closed installed runtime binding")
    if not sys.flags.isolated or not sys.flags.dont_write_bytecode:
        raise PermissionError("Preparation requires the installed Python -I -B")
    if _path(binding["runtime_prefix"]) != Path(sys.prefix).resolve():
        raise PermissionError("Actual installed runtime prefix differs")
    expected = ((binding["wheel_path"], binding["wheel_sha256"], 200_000_000),
        (binding["runtime_manifest_path"], binding["runtime_manifest_sha256"], 16_000_000),
        (str(_path(path).parent / "source-manifest.json"), binding["source_manifest_sha256"], 8_000_000))
    for filename, digest, bound in expected:
        candidate = _path(filename)
        if not candidate.is_relative_to(_path(workspace)) or _sha(candidate, bound)[0] != digest:
            raise PermissionError("Installed runtime evidence changed or escaped workspace")
    custody = verify_installed_distribution_custody(importlib.metadata.distribution("devharness"),
        runtime_root=Path(sys.prefix).resolve())
    if custody["record_sha256"] != binding["record_sha256"]:
        raise PermissionError("Actual installed RECORD differs from its runtime binding")
    return {"binding_ref": source_ref(path, workspace), "verified_installed_record_sha256": custody["record_sha256"],
        "source_digest": trial_enforcement_source_digest(), "runtime_digest": trial_enforcement_runtime_digest()}


def _client_evidence(client_path, auth_path, boundary_path, workspace):
    client, auth, boundary = (_read(path, workspace) for path in (client_path, auth_path, boundary_path))
    if (type(auth) is not dict or set(auth) != {"auth_mode", "exit_code", "raw_output_retained", "raw_output_printed"}
            or type(auth.get("exit_code")) is not int
            or any(type(auth.get(key)) is not bool for key in ("raw_output_retained", "raw_output_printed"))
            or auth != {"auth_mode": "ChatGPT", "exit_code": 0, "raw_output_retained": False, "raw_output_printed": False}):
        raise PermissionError("Closed sanitized official-client ChatGPT observation is required")
    for path_key, hash_key in (("resolved_binary_path", "binary_sha256"), ("native_binary_path", "native_binary_sha256")):
        candidate = _path(client[path_key])
        if (candidate.name not in {"codex", "codex.js"}
                or not (candidate.is_relative_to(_path(workspace)) or candidate.is_relative_to(_PUBLIC_CLIENT_ROOT))):
            raise PermissionError("Client evidence may identify only public Codex binaries or owned synthetic fixtures")
        if _sha(candidate, 250_000_000)[0] != client[hash_key]:
            raise PermissionError("Public installed client identity changed")
    if Path(client["invoked_path"]).resolve() != _path(client["resolved_binary_path"]):
        raise PermissionError("Public installed client launcher changed")
    if any(client[key].get("name") != "@openai/codex" for key in ("npm_launcher_package", "npm_native_package")):
        raise PermissionError("An observed official Codex client identity is required")
    capabilities = boundary.get("capabilities")
    if (type(capabilities) is not dict or set(capabilities) != _BOUNDARY_FIELDS
            or any(type(value) is not bool for value in capabilities.values())):
        raise ValueError("Closed independent boundary capability evidence is required")
    return client, auth, capabilities


def _fixture(root, methods):
    """Invented tiny records through existing services; no fits or generated answers."""
    from devbench.analysis.checkpoints import digest
    from devbench.analysis.rna_input_contract import inspect_registered_bulk_rna_input
    from devbench.analysis.rna_publication_evidence import prepare_publication_evidence
    from devbench.analysis.rna_publication_profiles import prepare_assessment_intent
    workspace = root.parent
    def keep(name, value):
        return source_ref(_write(root / name, value), workspace)
    samples = ["synthetic-s1", "synthetic-s2", "synthetic-s3", "synthetic-s4"]
    matrix = keep("matrix.json", {"gene_ids": ["synthetic-g1", "synthetic-g2"], "sample_ids": samples,
        "matrix": [[1.0, 2.25, 3.5, 4.0], [5.0, 6.0, 7.0, 8.0]]})
    metadata = keep("metadata.json", {"samples": [{"sample_id": sample,
        "group": "invented-A" if index < 2 else "invented-B", "unit": "invented-unit-" + str(index),
        "batch": "invented-batch-" + str(index % 2)} for index, sample in enumerate(samples)]})
    refs = {name: methods.import_artifact(ref["path"], artifact_type="json", actor="synthetic-preparation")
        for name, ref in (("matrix", matrix), ("metadata", metadata))}
    binding = {"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": refs["matrix"]["artifact_id"],
        "metadata_artifact_id": refs["metadata"]["artifact_id"], "declarations": {
            "format": "devbench.bulk-rna-input/1", "representation": "normalized_expression", "axes": ["gene", "sample"],
            "provenance": {"source": "Invented engineering fixture; no study or biological observations"},
            "biological_independence": {"independent_units": True, "scope": "invented fixture labels only"},
            "metadata_roles": {"condition": "group", "biological_unit": "unit", "batch": "batch"}}}
    inspection = inspect_registered_bulk_rna_input(methods, binding)
    question = ("Describe this wholly invented normalized-expression fixture and propose an unexecuted diagnostic workflow. "
        "This is a synthetic engineering check. Do not infer biology, recommend a best scientific method, execute methods, "
        "or use any real-study material.")
    qsource = keep("question.txt", question)
    access = keep("access.json", {"classification": "synthetic", "source": "assistant-proposed engineering fixture",
        "scope": "Local preparation only; generation and numerical execution have no approval"})
    dataset = {"binding": binding, "classification": "synthetic", "access_record": access, "inspection_sha256": digest(inspection)}
    exposure = {"prior_outcome_access": "unknown", "public_outcomes_accessible": "yes", "workflow_author_outcome_access": "unknown",
        "limitations": "All records are invented; no held-out scientific evidence or outcome assessment"}
    identity = {"doi": "10.1234/invented-signedin-probe", "pmid": None, "pmcid": None,
        "title": "Invented Signed-In Engineering Fixture", "version": "1"}
    method_quote = "Quality diagnostics inspect finite measurements without deleting observations or inferring biological effects."
    paper = "\n".join([identity["doi"], identity["title"], "1", method_quote])
    paper_ref = keep("invented-method-guidance.txt", paper)
    def locator(text):
        start = paper.encode().index(text.encode())
        return {"start_byte": start, "end_byte": start + len(text.encode()), "text": text, "location": "Invented engineering guidance"}
    publication = prepare_publication_evidence(root / "publication", {"identity": identity,
        "approved_urls": ["https://example.invalid/invented-signedin-probe"]},
        [{"source_id": "invented-guidance", "path": paper_ref["path"], "sha256": paper_ref["sha256"],
            "source_url": "https://example.invalid/invented-signedin-probe", "identity": identity, "access": "extracted_text",
            "identity_locators": {key: locator(value) for key, value in identity.items() if value is not None},
            "identity_reviewed_by": "synthetic-curator", "limitations": "Invented source; not a real publication or scientific evidence"}],
        [{"excerpt_id": "invented-method-scope", "source_id": "invented-guidance", "locator": locator(method_quote),
            "visibility": "participant", "purpose": "methods"}], exposure, curator_id="synthetic-curator",
        participant_id="official-codex-cli", workspace=workspace, allow_evidence_read=True)
    requirement = keep("fixture-origin.json", {"format": "devbench.rna-requirement-record/2", "requirements": [
        {"requirement_id": "synthetic-question", "origin": "fixture", "name": "scientific_objective", "value": question,
            "evidence": "Assistant-proposed engineering fixture; no user-prespecified scientific parameters"}]})
    estimand = {"format": "devbench.rna-estimand/2", "estimand_id": "invented-description",
        "requirement_sources": [{"origin": "fixture", "record_ref": requirement, "requirement_ids": ["synthetic-question"]}],
        "assay": "invented bulk expression fixture", "measurement": {"quantity": "invented normalized expression",
            "units": "arbitrary fixture units", "scale": "normalized_expression"}, "population": "invented fixture labels",
        "biological_unit": {"independent_unit": "invented unit", "dependent_unit": "invented specimen", "dependence": "independent"},
        "entity_type": "gene", "relationship": {"kind": "association", "definition": "Describe invented measurements only"},
        "time_reference": None, "denominator": "two invented genes", "adjustment": {"covariates": [],
            "assumptions": ["Synthetic labels only"], "unknowns": []}, "claim_scope": "association",
        "unknowns": [{"field": "time_reference", "reason": "No temporal question"}]}
    data_ref = keep("dataset.json", {"format": "devbench.rna-trial-dataset/2", **dataset})
    exposure_ref, universe_ref = keep("exposure.json", exposure), keep("universe.json", {"rule_id": "jointly_estimable"})
    reference_ref = keep("reference-grounding.json", {"format": "devbench.rna-reference-result-set/2",
        "reference_set_id": "invented-generation-grounding-only", "publication_ref": publication, "source_collection_ref": None,
        "estimand_ref": keep("estimand.json", estimand), "findings": [], "universe_ref": universe_ref,
        "exposure_ref": exposure_ref, "limitations": ["No reference results or numerical qualification"]})
    analyst = keep("analyst.json", {"format": "devbench.rna-analyst-projection/2", "mode": "independent_method_selection",
        "question_ref": qsource, "dataset_ref": data_ref, "estimand": estimand, "permitted_method_excerpts": [],
        "required_output_schemas": ["devbench.rna-gene-table/2"], "omitted_evidence": ["evaluator references"],
        "scope": "context_projection_not_os_isolation", "limitations": "Engineering fixture only"})
    evaluator = keep("evaluator.json", {"format": "devbench.rna-evaluator-projection/2", "reference_bundle_ref": reference_ref,
        "exposure_ref": exposure_ref, "required_claim_ids": ["synthetic-contract-only"], "omitted_evidence": [],
        "scope": "context_projection_not_os_isolation", "limitations": "No scientific evaluation or approval"})
    metric = {"metric_id": "gene_effect_absolute_difference", "formula_version": "1", "units": "arbitrary fixture units",
        "parameter_record_ref": keep("metric-parameters.json", {"parameters": {}}),
        "comparability_policy_ref": keep("comparability.json", {"permitted_differences": []}), "universe_rule_id": "jointly_estimable",
        "uncertainty_policy": {"kind": "report_only", "rationale": "No numerical assessment is authorized"},
        "acceptance_policy": {"kind": "report_only", "operator": None, "bound": None,
            "rationale": "Descriptor only; no success threshold", "source_ref": None}, "required": True}
    intent = {"format": "devbench.rna-assessment-intent/2", "intent_id": "invented-generation-only",
        "mode": "independent_method_selection", "question_ref": qsource, "dataset_ref": data_ref,
        "reference_bundle_ref": reference_ref, "exposure_ref": exposure_ref, "estimand": estimand,
        "profile_specs": [{"profile_id": "mean_expression", "profile_version": "2",
            "required_input_capabilities": ["bulk_gene_expression", "independent_biological_units", "identifiable_design"],
            "applicability_rules": [{"predicate_id": "relationship_in", "values": ["association"]},
                {"predicate_id": "entity_type_in", "values": ["gene"]}], "metric_specs": [metric],
            "required_output_schemas": ["devbench.rna-gene-table/2"], "required_plot_kinds": ["effect_scatter"],
            "permitted_claim_scopes": ["association"], "missingness_policy": "report_and_abstain"}],
        "universe_policy_ref": universe_ref, "required_claim_ids": ["synthetic-contract-only"], "analyst_projection_ref": analyst,
        "evaluator_projection_ref": evaluator, "decision_policy": {"kind": "report_only", "rationale": "No scientific winner or promotion"},
        "limitations": ["Synthetic engineering proposal; no real data, fitted outputs, plots, or scientific validation"]}
    frozen = prepare_assessment_intent(root / "intent", intent, workspace=workspace, service=methods)
    # This non-runnable descriptor lets generation declare a governed proposal.
    # It cannot supply numerical execution qualification or a real method result.
    method_source = keep("synthetic-method.py", "raise RuntimeError('Synthetic planning descriptor; execution unavailable')\n")
    lock = keep("synthetic-environment.lock", "synthetic-planning-descriptor=1\n")
    method = methods.register_method({"format": "devharness.method/1", "method_id": "synthetic.planning_descriptor",
        "name": "Synthetic planning descriptor", "lifecycle": "custom", "implementation_trust": "unreviewed",
        "inputs": {name: {"artifact_type": "json", "required": True} for name in ("matrix", "metadata")},
        "parameters": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
        "outputs": {"description": {"artifact_type": "json", "path": "/outputs/description.json", "required": True}},
        "implementation": {"kind": "oci", "source_sha256": method_source["sha256"], "entrypoint": ["python", "/method/main.py"]},
        "environment": {"image": "fixture.invalid/non-runnable@sha256:" + "0" * 64, "lock_sha256": lock["sha256"]},
        "execution": {"cpu": 1, "memory_mb": 128, "timeout_seconds": 1, "pids": 1, "max_output_bytes": 1024, "network": "deny"},
        "validation": {"comparator": "json", "invariants": [], "counts_layer": "X", "qc_metrics": []},
        "provenance": {"origin": "synthetic_generation_preparation_only"}},
        source=method_source["path"], environment_lock=lock["path"], actor="synthetic-preparation")
    analysis = methods.create_analysis("Invented generation preparation", actor="synthetic-preparation")
    branch = methods.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="synthetic-preparation")
    return {"question": question, "question_source": qsource, "dataset": dataset, "inspection": inspection,
        "access": access, "publication": publication, "assessment_intent": frozen,
        "method": method, "branch_id": branch["branch_id"]}


def _engineering_identity(backend, runtime):
    value = backend.runtime_identity()
    if any(value[key] != runtime[key] for key in ("source_digest", "runtime_digest")):
        raise PermissionError("Actual backend differs from the independently bound installed runtime")
    return value


def _stage_allocations(backend):
    return backend.generation_allocations({"session_timeout_seconds": 300, "max_context_bytes": 32768})


def prepare_probe(output, *, workspace, model, runtime_binding, client_evidence, auth_evidence, boundary_evidence):
    """Create a reviewable exact generation subject; no authority is constructed."""
    if type(model) is not str or re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", model) is None:
        raise ValueError("An explicit bounded model identifier is required; no default is inferred")
    workspace, output = _path(workspace), _path(output)
    evidence_paths = [runtime_binding, client_evidence, auth_evidence, boundary_evidence]
    if not output.is_relative_to(workspace) or output.exists() or any(_path(item).is_relative_to(output) for item in evidence_paths):
        raise ValueError("Preparation output must be fresh, inside workspace and disjoint from evidence")
    client, auth, boundary = _client_evidence(client_evidence, auth_evidence, boundary_evidence, workspace)
    runtime = _installed_runtime(runtime_binding, workspace)
    from devbench.analysis.codex_signedin import SignedInCodexActionSelector, SignedInCodexInvestigationBackend, request_material
    from devbench.analysis.methods import MethodService
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    from devbench.analysis.rna_scientific_trial_contract import trial_binding, validate_trial_request
    from devbench.analysis.rna_scientific_trial import prepare_trial
    from devbench.analysis.rna_publication_evidence import participant_publication_context
    output.mkdir(mode=0o700)
    fixture_root = output / "synthetic"
    fixture_root.mkdir(mode=0o700)
    methods = MethodService(output / "service")
    fixture = _fixture(fixture_root, methods)
    selector = SignedInCodexActionSelector(requested_model=model, private_root=methods.root,
        executable=client["invoked_path"], max_context_bytes=32768, max_output_bytes=131072)
    ledger = WorkflowOperationLedger(methods.root / "generation-ledger.sqlite")
    backend = SignedInCodexInvestigationBackend(methods, selector, policy=deepcopy(POLICY), ledger=ledger)
    actual_identity = _engineering_identity(backend, runtime)
    if actual_identity["authentication"]["method"] != "chatgpt_signin":
        raise PermissionError("Current official-client ChatGPT authentication unavailable; no concrete packet or launch")
    engineering = deepcopy(actual_identity)
    engineering.update(qualification_refs=[source_ref(boundary_evidence, workspace)], hosted_trace_required=True,
        baseline_decisions=[{"identity": name, "status": "not_dependent", "evidence": fixture["access"]}
            for name in ("T14-effect-definition-mismatch", "Harmony-missing-dependency")])
    request = validate_trial_request({"format": "devbench.rna-scientific-trial/3", "trial_id": "synthetic-signedin-generation-probe",
        "question": {"text": fixture["question"], "source": fixture["question_source"], "source_excerpt": fixture["question"]},
        "task": {"scientific_objective": fixture["question"], "computational_task": "quality_control"}, "requirements": [],
        "dataset": fixture["dataset"], "objective": {"mode": "independent_method_selection", "estimand": "Describe invented measurements only",
            "claim_scope": "Synthetic engineering control flow only", "intent": "descriptive"},
        "design": {"contrasts": [], "covariates": [], "chronological_field": None, "chronological_order": []},
        "publication": fixture["publication"], "comparison": None, "workflow": None, "engineering": engineering,
        "privacy": {"full_data_local": True, "participant_fields": ["question", "bounded_bulk_inspection", "invented_method_guidance"], "trace_fields": []},
        "allocation": {"kind": "generation_jobs", "generation": {"jobs": 3, "seconds": 300, "context_bytes": 32768, "output_bytes": 131072}, "numeric": None},
        "roles": {"implementation_owner": "codex-preparation", "operator": "vajraang", "security_owner": "signedin-security-review",
            "reviewer": "signedin-quality-review", "evaluator": "synthetic-curator", "participant": "official-codex-cli"},
        "approvals": {"workflow_generation": None, "trial_execution": None}, "assessment_intent": fixture["assessment_intent"],
        "assessment_plan": None, "scientific_context": None})
    request_ref = source_ref(_write(output / "trial-request.HOLD.json", request), workspace)
    prepared = prepare_trial(output / "trial-preparation", request, methods=methods, workspace=workspace, purpose="workflow_generation")
    allocations = _stage_allocations(backend)
    if set(allocations) != set(STAGES) or any(row != {"jobs": 1, "seconds": 75, "context_bytes": 32768, "output_bytes": 131072} for row in allocations.values()):
        raise PermissionError("Actual backend stage allocations differ from this exact proposed budget")
    publication_context = participant_publication_context(fixture["publication"]["manifest_path"], fixture["publication"]["root_hash"], allow_evidence_read=True)
    previews = []
    for stage in STAGES:
        context = {"decision_kind": stage, "task": request["task"], "dataset_summary": fixture["inspection"],
            "publication_evidence": publication_context, "scope": "synthetic_preparation_preview_only"}
        if stage == "method_capability_resolution":
            context.update(candidates=[], registry={fixture["method"]["version_id"]: fixture["method"]["contract"]})
        material = request_material(context)
        prompt = source_ref(_write(output / (stage + ".prompt.preview.txt"), material["prompt"]), workspace)
        if prompt["size_bytes"] > 32768:
            raise ValueError("Synthetic prompt/schema preview exceeds the proposed job input bound")
        previews.append({"stage": stage, "preview_only": True, "prompt_ref": prompt,
            "schema_ref": source_ref(_write(output / (stage + ".schema.preview.json"), material["schema"]), workspace),
            "dispatch_binding": "Actual grounded stage prompt/schema/prior accepted outputs must be reserved before each launch"})
    packet = {"format": "devbench.codex-signedin-probe-preparation/1", "status": "HOLD", "purpose": "workflow_generation",
        "allow_model_execution": False, "approval_required": True, "trial_request_ref": request_ref,
        "generation_binding_sha256": trial_binding(request, "workflow_generation"), "runtime": runtime,
        "client_evidence_ref": source_ref(client_evidence, workspace), "auth_observation_ref": source_ref(auth_evidence, workspace),
        "boundary_evidence_ref": source_ref(boundary_evidence, workspace), "boundary_capabilities": boundary,
        "actual_backend_identity_at_preparation": actual_identity, "selector_configuration": selector.configuration(),
        "coordinator_inputs": {"method_workspace": str(methods.root), "branch_id": fixture["branch_id"],
            "version_ids": [fixture["method"]["version_id"]], "actor": request["roles"]["participant"]},
        "synthetic_method_scope": "Unreviewed non-runnable planning descriptor; source deliberately refuses execution",
        "readiness": prepared["readiness"], "trial_preparation_manifest": prepared["manifest_path"],
        "trial_preparation_root": prepared["root_hash"], "stage_material_previews": previews,
        "budget": {"policy": deepcopy(POLICY), "stage_allocations": allocations, "seconds_reserved_for_stages": 225,
            "coordinator_wall_headroom_seconds": 75, "coordinator_job_retries": 0, "resumes": 0, "child_jobs_or_tools": 0,
            "scope": {"concurrency": "This coordinator's protected ledger only; unrelated clients are not controlled",
                "context_bytes": "Our serialized prompt/schema bytes; provider hidden/system context is not capped",
                "output_bytes": "Combined retained CLI stdout/stderr/events; provider output tokens and scratch writes are not capped",
                "memory_scratch_and_total_disk": "UNSUPPORTED; reject if a hard cap on these resources is required"},
            "observability": {"internal_provider_requests": "UNKNOWN_NOT_CAPPED", "provider_internal_retries": "UNVERIFIED_NOT_CAPPED",
                "provider_tokens": "POSTHOC_OPTIONAL_NOT_CAPPED", "subscription_credits": "NOT_CAPPED", "spend": "NOT_CAPPED",
                "deadline_scope": "Local deadline initiates owned process-group stop/reap with bounded cleanup; remote cancellation unconfirmed"}},
        "data_facts": {"observed": "Two invented genes by four invented samples; eight finite arbitrary normalized values",
            "declarations": "Every measurement, group, unit, batch and source is assistant-authored synthetic fixture content",
            "unknowns": "No biological population, real-study identity, scientific effect, or method superiority is established"},
        "blockers": sorted(set([*prepared["readiness"]["blockers"], "native_boundary_unqualified", "fresh_authentication_required_at_launch",
            "model_native_availability_unverified", "exact_user_approval_and_trusted_workflow_generation_grant_required",
            "independent_source_runtime_and_boundary_reviews_required"])),
        "effects": {"model_jobs_launched": 0, "numeric_runs": 0, "authority_grants_created": 0},
        "qualification_requirements": {
            "workflow": "Complete the existing three-stage synthetic generation tree and retain the sealed unexecuted workflow",
            "capture": "Retain every actual dispatch, completion, failure, cancellation, gap and intervention in that same job tree",
            "hosted_delivery": "Deliver the actual privacy-reviewed three-job tree to LangSmith; local receipts alone are insufficient",
            "independent_readback": "Independently read back and match that same actual tree without rerunning analysis or generation",
            "accounting": "Report coordinator jobs separately; preserve unknown internal requests/retries, optional tokens and uncapped credits/spend",
            "approval_scope": "Local budget approval grants no hosted-delivery, readback, workflow-success or scientific qualification proof"},
        "qualification_scope": "Synthetic three-stage generation preparation only; no transport/full-workflow success or real-study qualification",
        "launch_requirement": "No process may launch without exact reviewed packet approval and a matching current trusted workflow_generation grant"}
    path = _write(output / "probe-packet.HOLD.json", packet)
    return {"packet_path": str(path), "packet_sha256": _sha(path)[0], "status": "HOLD", "generation_binding_sha256": packet["generation_binding_sha256"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("workspace", "output", "model", "runtime-binding", "client-evidence", "auth-evidence", "boundary-evidence"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    result = prepare_probe(args.output, workspace=args.workspace, model=args.model, runtime_binding=args.runtime_binding,
        client_evidence=args.client_evidence, auth_evidence=args.auth_evidence, boundary_evidence=args.boundary_evidence)
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
