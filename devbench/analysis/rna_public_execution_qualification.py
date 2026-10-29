"""Exact public-airway execution qualification; never an execution approval.

The two inspectors are trusted coordinator dependencies, not request fields.
Acceptance must come from independently authenticated retained proof, including
actual OCI/R input-reader and renderer verification. This module never builds an
image, fits data, calls a model, signs a grant or treats report flags as trust.
Historical synthetic qualification remains a different exact type and scope.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re

from devharness_core.provenance import canonical_json
from devbench.study.evidence import _read, decode_json
from .checkpoints import digest
from .episode_proposals import _absolute
from .rna_scientific_trial_contract import (
    FORMAT_V3, _instant, bounded_source, trial_binding, validate_approval, validate_trial_request,
)

FORMAT = "devbench.rna-public-execution-qualification/1"
BINDING_FORMAT = "devbench.rna-public-execution-qualification-binding/1"
ACCEPTANCE_FORMAT = "devbench.rna-public-execution-qualification-acceptance/1"
SCOPE = "public_original_airway_execution_only"
REQUIRED_PROOFS = ("original_data_and_paired_design", "historical_method_source_and_environment",
    "actual_oci_r_runtime_and_controls", "lossless_actual_r_input_roundtrip", "canonical_output_adapter",
    "frozen_publication_metrics", "comparison_renderers", "independent_scientific_review")
_IDENTITY = {"route", "source_digest", "runtime_digest", "backend_digest", "model", "platform",
             "client", "authentication", "budget_semantics", "qualified_boundary"}
_ACCEPTANCE = {"format", "issuer_id", "reviewer_id", "accepted_root_sha256", "origin", "scope", "purpose",
               "expires_at", "binding", "proofs"}
_PLOTS = {"effect_scatter", "gene_table_coverage"}
_WORKFLOW_ID = "original-airway-2015-full-processed-v1"
_ROUNDTRIP_KEYS = {"observation_sha256", "matrix_artifact_sha256", "metadata_artifact_sha256", "original_rdata_sha256",
    "row_ranges_sha256", "gene_ids_sha256", "sample_ids_sha256", "measurement_values_sha256", "metadata_sha256", "aligned_metadata_sha256"}
_PARAMETERS = {"workflow_id", "workflow_sha256", "source_sha256", "required_package_versions", "adapter_package_versions",
    "sample_universe_sha256", "gene_universe_sha256", "retained_figure_expectation_ref", "operation", "row_ranges_sha256"}
_BLOCKERS = ["differential_expression:unsupported_profile", "unqualified_adapters_metrics_renderers_execution_route"]
_NATIVE_OUTPUTS = {port: {"artifact_type": kind, "path": "/outputs/" + name, "required": True}
    for port, kind, name in (
        ("airway_figures", "file", "figure-bundle.tar.gz"), ("native_support", "file", "native-support.tar.gz"),
        ("native_default", "table", "dex-default.native.tsv"), ("native_alpha05", "table", "dex-alpha05.native.tsv"),
        ("native_lfc1", "table", "dex-lfc1.native.tsv"), ("native_cell", "table", "cell-N061011-vs-N61311.native.tsv"),
        ("result_manifest", "json", "result-manifest.json"), ("reader_audit", "json", "reader-audit.json"),
        ("actual_session", "file", "actual-session.txt"))}



def _hash(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _adapter_digest():
    files = ("rna_public_execution_qualification.py", "rna_execution_inputs.py", "rna_comparison_outputs.py",
             "rna_publication_comparison.py", "rna_publication_metrics.py", "rna_publication_profiles.py",
             "method_executor.py", "figure_artifacts.py", "rna_retained_figures.py", "rna_airway_native_outputs.py")
    root = Path(__file__).resolve().parent
    return digest({name: hashlib.sha256(_read(root / name, 1_000_000)).hexdigest() for name in files})


def audit_original_airway_pairs(inspection, design):
    """Audit observed original cell-line pairs; establish no donor independence."""
    import numpy as np
    expected_design = {"contrasts": [{"field": "dex", "reference": "untrt", "target": "trt"}],
        "covariates": ["cell"], "chronological_field": None, "chronological_order": []}
    expected_cells = {"N052611", "N061011", "N080611", "N61311"}
    roles = inspection.get("metadata_roles", {})
    rows = inspection.get("sample_metadata", [])
    if (design != expected_design or inspection.get("shape") != [64102, 8]
            or inspection.get("representation") != "raw_counts" or inspection.get("numeric_storage") != "integer"
            or inspection.get("count_values_valid") is not True or inspection.get("sample_metadata_complete") is not True
            or roles.get("condition") != "dex" or roles.get("biological_unit") != "cell" or "batch" in roles
            or type(rows) is not list or len(rows) != 8
            or any(type(row.get("sample_id")) is not str or not row["sample_id"] for row in rows)
            or len({row["sample_id"] for row in rows}) != 8
            or {row.get("cell") for row in rows} != expected_cells
            or any(sum(row.get("cell") == cell and row.get("dex") == treatment for row in rows) != 1
                   for cell in expected_cells for treatment in ("untrt", "trt"))):
        raise PermissionError("Only exact original airway raw counts and four complete within-cell treatment pairs are supported")
    columns = [np.ones(8)]
    columns += [np.array([float(row["cell"] == cell) for row in rows]) for cell in sorted(expected_cells)[1:]]
    columns.append(np.array([float(row["dex"] == "trt") for row in rows]))
    rank = int(np.linalg.matrix_rank(np.column_stack(columns)))
    if rank != 5:
        raise PermissionError("Original paired airway design must retain full rank five")
    return {"format": "devbench.original-airway-paired-design/1", "design": deepcopy(design),
        "libraries": 8, "observed_cell_line_blocks": 4, "cell_line_labels": sorted(expected_cells),
        "rank": rank, "columns": 5, "residual_degrees_of_freedom": 3,
        "donor_independence": "not_established_by_labels",
        "modeled_block_independence": "assumption_requires_independent_review",
        "sample_pairing_sha256": digest([{key: row[key] for key in ("sample_id", "cell", "dex")} for row in rows]),
        "blockers": [], "limitations": ["Eight libraries are four paired cell-line blocks, not eight independent biological replicates.",
            "No explicit sequencing batch was supplied; unknown effects cannot be ruled out.",
            "Full design rank establishes identifiability of the modeled contrast, not causal or donor validity."]}


def _validate_figure_expectation(expectation, authored_manifest, parameters, inspection):
    """Require the full frozen authored figure objective before acceptance."""
    from .rna_retained_figures import validate_authored_figure_expectations
    if (type(expectation) is not dict
            or set(expectation) != {"workflow_sha256", "workflow_manifest_ref", "expected_plots", "required_image_formats"}
            or expectation["workflow_sha256"] != parameters["workflow_sha256"]
            or expectation["workflow_manifest_ref"]["sha256"] != parameters["workflow_sha256"]
            or type(authored_manifest) is not dict
            or authored_manifest.get("source", {}).get("sha256") != parameters["source_sha256"]):
        raise ValueError("Pre-run authored figure manifest locator, source or workflow differs")
    return validate_authored_figure_expectations(expectation, authored_manifest,
        sample_universe_sha256=inspection["sample_ids_sha256"], gene_universe_sha256=inspection["gene_ids_sha256"])


class TrustedPublicExecutionQualification:
    """A detached exact-subject public /3 overlay, with no default trust root.

    Only adapter/comparator qualification blockers may be satisfied. Missing
    signed purpose approval, native account/boundary acceptance, biological
    independence evidence, malformed design or allocation cannot be waived.
    """
    def __init__(self, *, workspace, accepted_proof_verifier, runtime_verifier, clock=None):
        if (not callable(accepted_proof_verifier) or not callable(runtime_verifier)
                or clock is not None and not callable(clock)):
            raise TypeError("Trusted independent acceptance and actual OCI runtime inspectors are required")
        self.workspace = _absolute(workspace)
        self.accepted_proof_verifier = accepted_proof_verifier
        self.runtime_verifier = runtime_verifier
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self):
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise PermissionError("Trusted current qualification time is unavailable")
        return now

    def _runtime(self, request, methods, planner, context):
        from .method_planner import ScientificMethodPlanner
        from .methods import MethodService
        from .method_executor import LocalDockerExecutor
        from .codex_signedin import SignedInCodexInvestigationBackend
        from . import rna_trial_authority as authority
        required = {"planner", "executor", "actor", "scientific_context", "backend"}
        if (type(methods) is not MethodService or type(planner) is not ScientificMethodPlanner
                or planner.methods is not methods or not _absolute(methods.root).is_relative_to(self.workspace)
                or type(context) is not dict or not required <= set(context) or set(context) - (required | {"selector"})
                or context["planner"] is not planner or context["actor"] != request["roles"]["participant"]
                or request["scientific_context"] is None or context["scientific_context"] != request["scientific_context"]
                or type(context["backend"]) is not SignedInCodexInvestigationBackend
                or type(context["executor"]) is not LocalDockerExecutor):
            raise PermissionError("Exact actual signed-in coordinator and OCI executor objects are required")
        backend = context["backend"]
        if (backend.methods is not methods or "selector" in context and context["selector"] is not backend.selector):
            raise PermissionError("Selected signed-in backend or selector differs")
        capability = backend.require_admission("generation_jobs")
        boundary = request["engineering"]["qualified_boundary"]
        if (capability.get("scope") != "native" or capability.get("native_boundary_qualified") is not True
                or backend.qualification is None or backend.qualification.reference != boundary):
            raise PermissionError("Verified actual native authentication/boundary is required")
        identity = {key: request["engineering"][key] for key in _IDENTITY}
        if (backend.runtime_identity() != identity
                or identity["source_digest"] != authority.trial_enforcement_source_digest()
                or identity["runtime_digest"] != authority.trial_enforcement_runtime_digest()):
            raise PermissionError("Current signed-in source/runtime identity differs")
        observed = self.runtime_verifier(dict(context))
        keys = {"identity", "executor_sha256", "image", "r_session_sha256", "input_reader_roundtrip", "container_controls_sha256"}
        if (type(observed) is not dict or set(observed) != keys or observed["identity"] != identity
                or not all(_hash(observed[key]) for key in keys - {"identity", "image", "input_reader_roundtrip"})
                or type(observed["input_reader_roundtrip"]) is not dict or set(observed["input_reader_roundtrip"]) != _ROUNDTRIP_KEYS
                or not all(_hash(value) for value in observed["input_reader_roundtrip"].values())
                or type(observed["image"]) is not str
                or re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", observed["image"]) is None):
            raise PermissionError("Actual inspected OCI/R runtime, lossless reader and controls evidence is unavailable")
        return deepcopy(observed)

    def observe_binding(self, request, *, methods, planner, purpose, runtime_context):
        """Rehash a complete frozen subject without dispatching or accepting it."""
        from .rna_publication_profiles import (
            assessment_rules_digest, load_assessment_intent, load_assessment_plan, read_reference_json,
        )
        from .rna_publication_comparison import _frozen_gene_universe, _gene_comparison_parameters
        from .rna_scientific_trial import inspect_trial_data, workflow_snapshot
        request = validate_trial_request(request)
        if (purpose != "trial_execution" or request["format"] != FORMAT_V3
                or request["dataset"]["classification"] != "public"
                or request["objective"]["mode"] != "exact_reproduction"):
            raise PermissionError("Public qualification supports only signed-in exact-reproduction version 3 execution")
        if (request["assessment_plan"] is None or request["workflow"] is None
                or request["engineering"]["qualified_boundary"] is None or request["allocation"]["numeric"] is None):
            raise PermissionError("Frozen plan, native boundary and separate numeric allocation are required")
        validate_approval(request, purpose, now=self._now())
        boundary = request["engineering"]["qualified_boundary"]
        refs = request["engineering"]["qualification_refs"]
        if len(refs) != 2 or boundary not in refs:
            raise PermissionError("Exactly one native boundary and one complete independent scientific proof are required")
        for ref in refs:
            bounded_source(ref, self.workspace)
        observed = self._runtime(request, methods, planner, runtime_context)
        plan = load_assessment_plan(request["assessment_plan"], workspace=self.workspace, service=methods)
        intent = load_assessment_intent(request["assessment_intent"], workspace=self.workspace, service=methods)
        snapshot = workflow_snapshot(planner, request["workflow"], workspace=self.workspace)
        wrapper = read_reference_json(plan["workflow_ref"], workspace=self.workspace, service=methods)
        if (plan["intent_ref"] != request["assessment_intent"] or plan["workflow_ref"] != request["workflow"]["workflow_ref"]
                or request.get("execution_inputs_ref") != plan["execution_inputs_ref"]
                or wrapper["governed_workflow"] != snapshot or digest(snapshot) != request["workflow"]["sha256"]
                or wrapper["assessment_intent_ref"] != request["assessment_intent"]
                or wrapper["assessment_rules_sha256"] != assessment_rules_digest(intent)
                or snapshot["task"] != request["task"] or snapshot["requirements"] != request["requirements"]
                or intent["question_ref"] != request["question"]["source"] or intent["mode"] != request["objective"]["mode"]
                or read_reference_json(intent["dataset_ref"], workspace=self.workspace, service=methods)
                    != {"format": "devbench.rna-trial-dataset/2", **request["dataset"]}):
            raise PermissionError("Exact plan, workflow, question, intent or original dataset binding differs")
        inspection = inspect_trial_data(methods, request)
        if (digest(inspection) != request["dataset"]["inspection_sha256"]
                or inspection["representation"] != "raw_counts" or inspection["numeric_storage"] != "integer"
                or not inspection["count_values_valid"]):
            raise PermissionError("Original raw integer count identity or semantics differ")
        audit = audit_original_airway_pairs(inspection, request["design"])
        if audit["blockers"]:
            raise PermissionError("Unresolved scientific design blockers: " + ", ".join(audit["blockers"]))
        inputs = load_original_airway_execution_inputs(plan["execution_inputs_ref"], artifact_service=methods,
            dataset_binding=request["dataset"]["binding"], workspace=self.workspace)
        transform = read_reference_json(inputs["transformation_receipt_ref"], workspace=self.workspace, service=methods)
        roundtrip = observed["input_reader_roundtrip"]
        expected_roundtrip = {"matrix_artifact_sha256": inputs["original_refs"]["matrix"]["sha256"],
            "metadata_artifact_sha256": inputs["original_refs"]["metadata"]["sha256"],
            "original_rdata_sha256": inputs["original_rdata_ref"]["sha256"],
            **{key: inspection[key] for key in ("gene_ids_sha256", "sample_ids_sha256", "measurement_values_sha256", "metadata_sha256", "aligned_metadata_sha256")}}
        if any(roundtrip[key] != value for key, value in expected_roundtrip.items()):
            raise PermissionError("Actual R roundtrip differs from full original matrix, metadata, identifiers or opaque RData")
        reader = transform["reader_contract"]
        if (inputs["format"] != INPUT_FORMAT or reader["kind"] != "oci_original_airway_rdata_and_json_identity"
                or reader["image"] != observed["image"] or transform["paired_design_audit"] != audit):
            raise PermissionError("Original opaque RData, paired JSON audit and actual historical R reader must match")
        if len(intent["profile_specs"]) != 1:
            raise PermissionError("Only the exact differential-expression comparison profile is supported")
        profile = intent["profile_specs"][0]
        plots = profile["required_plot_kinds"]
        if (profile["profile_id"] != "differential_expression" or profile["profile_version"] != "2"
                or profile["required_output_schemas"] != ["devbench.airway-full-native-result/1"]
                or set(plots) - _PLOTS or len(plots) != len(set(plots))):
            raise PermissionError("Public qualification requires exact native differential-expression outputs and only frozen supported comparison plots")
        resolved = read_reference_json(plan["resolved_capabilities_ref"], workspace=self.workspace, service=methods)["profiles"]
        if (len(resolved) != 1 or resolved[0]["status"] != "unsupported_profile"
                or resolved[0]["applicability"] != "applicable" or resolved[0]["missing_capabilities"]
                or resolved[0]["reason_codes"] != ["unqualified_adapters_metrics_renderers_execution_route"]):
            raise PermissionError("Qualification cannot waive input capability, unknown estimand or applicability blockers")
        metric_subjects = []
        for metric in profile["metric_specs"]:
            parameters = read_reference_json(metric["parameter_record_ref"], workspace=self.workspace, service=methods)
            comparability = read_reference_json(metric["comparability_policy_ref"], workspace=self.workspace, service=methods)
            reasons, universe = _frozen_gene_universe(intent, metric, None, None, None,
                workspace=self.workspace, artifact_service=methods)
            supported = (metric["metric_id"] in {"gene_effect_absolute_difference", "gene_effect_direction_agreement", "gene_effect_rmse", "gene_table_coverage"}
                and metric["formula_version"] == "1" and metric["uncertainty_policy"]["kind"] == "report_only"
                and _gene_comparison_parameters(parameters) is not None and parameters["selection_rule"] is None
                and parameters["reference_claim_ids"] == intent["required_claim_ids"]
                and comparability == {"permitted_differences": []} and not reasons and universe is not None)
            if metric["required"] and not supported:
                raise PermissionError("Required frozen comparison metric exceeds qualified scope; absent publication data cannot be invented")
            metric_subjects.append({"metric": metric, "parameters": parameters, "comparability": comparability,
                "universe": universe, "adapter_scope": "supported_frozen_formula" if supported else "not_assessable_optional_adapter"})
        actual_steps = []
        for experiment in snapshot["experiments"]:
            for candidate in experiment["candidates"]:
                step = methods.get("step", experiment["step_ids"][candidate])
                method = methods.method(step["method_version_id"])
                contract = method["contract"]
                if (contract["method_id"] != _WORKFLOW_ID or contract["implementation"]["kind"] != "oci"
                        or contract["implementation"]["entrypoint"] != ["/usr/local/bin/Rscript", "--vanilla", "/method/main.py", "/method/parameters.json"]
                        or contract["environment"]["image"] != observed["image"]
                        or contract.get("scientific", {}).get("implementation_origin") not in {"official", "adapted"}
                        or not method["environment_artifact"]):
                    raise PermissionError("Exact historically sourced OCI method and locked inspected image are required")
                if contract["outputs"] != _NATIVE_OUTPUTS:
                    raise PermissionError("Exact nine-output full native airway method contract is required")
                parameters = step["parameters"]
                if (set(parameters) != _PARAMETERS or parameters["workflow_id"] != _WORKFLOW_ID or parameters["operation"] != "full_analysis"
                        or not all(_hash(parameters[key]) for key in ("workflow_sha256", "source_sha256", "row_ranges_sha256", "sample_universe_sha256", "gene_universe_sha256"))
                        or parameters["source_sha256"] != "834e806bfb6020606378a08b75b749a967502fec0550de12724da9f6aa076d4c"
                        or parameters["row_ranges_sha256"] != roundtrip["row_ranges_sha256"]
                        or parameters["sample_universe_sha256"] != inspection["sample_ids_sha256"]
                        or parameters["gene_universe_sha256"] != inspection["gene_ids_sha256"]
                        or any(type(parameters[key]) is not dict or not parameters[key] for key in ("required_package_versions", "adapter_package_versions"))):
                    raise PermissionError("Frozen original workflow, source, historical packages or full input universes differ")
                figure_expectation = read_reference_json(parameters["retained_figure_expectation_ref"], workspace=self.workspace, service=methods)
                if (type(figure_expectation) is not dict or set(figure_expectation) != {"workflow_sha256", "workflow_manifest_ref", "expected_plots", "required_image_formats"}
                        or figure_expectation["workflow_sha256"] != parameters["workflow_sha256"]
                        or set(figure_expectation["required_image_formats"]) != {"png", "pdf"}
                        or type(figure_expectation["expected_plots"]) is not dict or not 1 <= len(figure_expectation["expected_plots"]) <= 64):
                    raise PermissionError("Frozen authored native figure expectations and original workflow differ")
                authored_manifest = read_reference_json(figure_expectation["workflow_manifest_ref"], workspace=self.workspace, service=methods)
                _validate_figure_expectation(figure_expectation, authored_manifest, parameters, inspection)
                validate_original_airway_method_binding(inputs, step["method_version_id"], parameters, artifact_service=methods)
                if step["inputs"] != {name: ref["artifact_id"] for name, ref in inputs["execution_refs"].items()}:
                    raise PermissionError("Actual OCI method inputs differ from original RData and full JSON audit views")
                identities = [*step["inputs"].values(), method["source_artifact"], method["environment_artifact"]]
                artifacts = {}
                for identity in identities:
                    methods.artifact_path(identity)
                    artifacts[identity] = methods.get("artifact", identity)
                actual_steps.append({"step": step, "method": method, "artifacts": artifacts})
        if not actual_steps:
            raise PermissionError("A finalized actual OCI workflow is required")
        return {"format": BINDING_FORMAT, "proof_scope": SCOPE, "purpose": purpose,
            "trial_binding_sha256": trial_binding(request, purpose), **observed,
            "assessment_plan_sha256": digest(plan), "assessment_intent_sha256": digest(intent),
            "workflow_sha256": digest(snapshot), "execution_inputs_sha256": digest(inputs),
            "method_execution_sha256": digest(actual_steps), "metrics_sha256": digest(metric_subjects),
            "profile_sha256": digest(profile), "adapter_sha256": _adapter_digest(),
            "design_audit_sha256": digest(audit), "dataset_inspection_sha256": digest(inspection),
            "required_plot_kinds": sorted(plots)}

    def verify(self, request, *, methods, planner, purpose, runtime_context):
        now = self._now()
        kwargs = {"methods": methods, "planner": planner, "purpose": purpose, "runtime_context": runtime_context}
        binding = self.observe_binding(request, **kwargs)
        request = validate_trial_request(request)
        boundary = request["engineering"]["qualified_boundary"]
        refs = [ref for ref in request["engineering"]["qualification_refs"] if ref != boundary]
        if len(refs) != 1:
            raise PermissionError("One separate complete independent public scientific proof is required")
        raw = self.accepted_proof_verifier(refs[0], workspace=self.workspace, now=now)
        try:
            encoded = canonical_json(raw)
            if len(encoded) > 1_000_000:
                raise ValueError("Acceptance exceeds its byte bound")
            accepted = decode_json(encoded)
            valid = (type(accepted) is dict and set(accepted) == _ACCEPTANCE
                and accepted["format"] == ACCEPTANCE_FORMAT
                and type(accepted["issuer_id"]) is str and 0 < len(accepted["issuer_id"].strip()) <= 200
                and accepted["reviewer_id"] == request["roles"]["reviewer"]
                and _hash(accepted["accepted_root_sha256"]) and accepted["origin"] == "public_dataset_qualification"
                and accepted["scope"] == SCOPE and accepted["purpose"] == purpose
                and type(accepted["expires_at"]) is str and len(accepted["expires_at"]) <= 200
                and digest(accepted["binding"]) == digest(binding)
                and type(accepted["proofs"]) is dict and set(accepted["proofs"]) == (set(REQUIRED_PROOFS) if binding["required_plot_kinds"] else set(REQUIRED_PROOFS) - {"comparison_renderers"})
                and all(_hash(value) for value in accepted["proofs"].values()))
            expiry = _instant(accepted["expires_at"]) if valid else None
        except (ValueError, TypeError, KeyError) as error:
            raise PermissionError("Independent public execution acceptance is malformed") from error
        if not valid or expiry <= now:
            raise PermissionError("Independent public execution acceptance scope, exact subject or expiry differs")
        if self.observe_binding(request, **kwargs) != binding or expiry <= self._now():
            raise PermissionError("Current public qualification subject changed or acceptance expired")
        return {"format": FORMAT, "binding_sha256": digest(binding), "satisfied_blockers": list(_BLOCKERS),
            "qualification_sha256": digest(accepted), "rendering": {"required": bool(binding["required_plot_kinds"]), "qualified": bool(binding["required_plot_kinds"]),
                "plot_kinds": deepcopy(binding["required_plot_kinds"])}}


# A distinct input format leaves all historical /2 parsers and unit semantics
# intact. JSON is an audit view; the opaque original retains rowRanges/annotation.
INPUT_FORMAT = "devbench.rna-original-airway-execution-inputs/3"
_ORIGINAL_RDATA_SHA256 = "c97976e3c8b3ef61cd62e9032057e4ad37514a71490168c322f87171dfca69a2"
_INPUT_FIELDS = {"format", "original_refs", "execution_refs", "transformation_receipt_ref", "declarations_ref",
    "method_ports_sha256", "capabilities", "prerequisites", "original_rdata_ref", "assumption_record_ref"}
_INPUT_CAPABILITIES = ["bulk_gene_expression", "known_measurement_representation", "exact_sample_alignment",
    "paired_biological_units", "identifiable_design", "modeled_independent_blocks_assumption", "original_genomic_ranges_retained"]


def _public_input_facts(dataset_binding, original_rdata_artifact_id, method_version_id, parameters,
                        assumption_record_ref, design, *, artifact_service, workspace):
    from .rna_execution_inputs import _artifact, _read_json, _read_source
    from .rna_input_contract import MAX_MATRIX_BYTES, inspect_registered_bulk_rna_input
    from devharness_core.methods import MethodContract
    workspace = _absolute(workspace)
    if not _absolute(artifact_service.root).is_relative_to(workspace):
        raise ValueError("Artifact service lies outside the assigned workspace")
    originals, documents = {}, {}
    for port, maximum in (("matrix", MAX_MATRIX_BYTES), ("metadata", 5_000_000)):
        originals[port], path = _artifact(artifact_service, dataset_binding[port + "_artifact_id"])
        if originals[port]["artifact_type"] != "json":
            raise PermissionError("Original airway QC bindings require unchanged JSON audit views")
        documents[port] = _read_json(path, maximum, expected=originals[port])
    inspection = inspect_registered_bulk_rna_input(artifact_service, dataset_binding)
    paired = audit_original_airway_pairs(inspection, design)
    rdata, _ = _artifact(artifact_service, original_rdata_artifact_id)
    if rdata["artifact_type"] != "file" or rdata["sha256"] != _ORIGINAL_RDATA_SHA256:
        raise PermissionError("The exact original RData artifact including genomic ranges is required")
    assumptions = _read_source(assumption_record_ref, workspace=workspace)
    if (type(assumptions) is not dict or set(assumptions) != {"format", "study", "block_independence", "donor_independence", "source_ref"}
            or assumptions["format"] != "devbench.original-airway-assumptions/1" or assumptions["study"] != "airway_2015"
            or assumptions["block_independence"] != "modeled_author_assumption" or assumptions["donor_independence"] != "unverified"):
        raise PermissionError("Retain attributed author block-independence assumption and unverified donor identity")
    bounded_source(assumptions["source_ref"], workspace)
    method = artifact_service.method(method_version_id)
    contract = method["contract"]
    selected = MethodContract.from_dict(contract).validate_parameters(parameters)
    expected_ports = {"matrix": "json", "metadata": "json", "airway_rdata": "file"}
    if (contract["implementation"]["kind"] != "oci" or set(contract["inputs"]) != set(expected_ports)
            or any(contract["inputs"][port]["artifact_type"] != kind or contract["inputs"][port]["required"] is not True
                   for port, kind in expected_ports.items()) or method["environment_artifact"] is None):
        raise PermissionError("The exact locked OCI method must consume JSON matrix/metadata and opaque airway_rdata")
    for identity in (method["source_artifact"], method["environment_artifact"]):
        artifact_service.artifact_path(identity)
    reader = {"kind": "oci_original_airway_rdata_and_json_identity", "method_version_id": method_version_id,
        "contract_sha256": digest(contract), "parameters": selected, "image": contract["environment"]["image"],
        "reader_source_sha256": contract["implementation"]["source_sha256"],
        "actual_r_roundtrip": "unqualified_requires_independent_actual_oci_evidence"}
    return originals, rdata, inspection, paired, reader


def _public_prerequisites(declarations_ref, assumption_record_ref):
    return [{"prerequisite_id": "input_integrity_and_alignment", "status": "satisfied", "evidence_refs": [declarations_ref],
                "reason": "Bounded unchanged original JSON audit view and exact original RData identity; not proof of scientific validity"},
        {"prerequisite_id": "paired_design", "status": "satisfied", "evidence_refs": [declarations_ref],
                "reason": "Four complete observed treatment pairs; model rank five and residual degrees of freedom three"},
        {"prerequisite_id": "independent_block_assumption_review", "status": "unknown", "evidence_refs": [assumption_record_ref],
                "reason": "Author modeling assumption requires independent scientific review; labels do not establish donor independence"},
        {"prerequisite_id": "actual_oci_r_lossless_roundtrip", "status": "unknown", "evidence_refs": [],
                "reason": "Original assay/count/sample/metadata/rowRanges equivalence must be verified inside exact historical R runtime"},
        {"prerequisite_id": "qualified_execution_route", "status": "unknown", "evidence_refs": [],
                "reason": "Input custody grants no native, model, numerical or scientific execution authority"}]


def _public_ports(method_version_id, parameters, design):
    return {"format": "devbench.original-airway-method-ports/3", "ports": {
        "matrix": {"source": "matrix", "artifact_type": "json", "encoding": "json", "required": True, "parameter": None},
        "metadata": {"source": "metadata", "artifact_type": "json", "encoding": "json", "required": True, "parameter": None},
        "airway_rdata": {"source": "airway_rdata", "artifact_type": "file", "encoding": "rdata", "required": True, "parameter": None}},
        "accepted_representations": ["raw_counts"], "objective": {"intent": "infer"}, "design": deepcopy(design),
        "method_version_id": method_version_id, "parameters": deepcopy(parameters)}


def _public_transforms(execution):
    return [{"port": port, "source": port, "encoding": "rdata" if port == "airway_rdata" else "json",
        "source_artifact_id": ref["artifact_id"], "execution_artifact_id": ref["artifact_id"],
        "operation": "opaque_original_identity" if port == "airway_rdata" else "identity",
        "scientific_filtering": False, "measurement_conversion": False} for port, ref in execution.items()]


def prepare_original_airway_execution_inputs(dataset_binding, original_rdata_artifact_id, method_version_id,
        parameters, assumption_record_ref, design, *, artifact_service, output):
    """Retain unchanged inputs and pair audit; never load RData or run R."""
    from .rna_execution_inputs import _source
    from .episode_proposals import require_disjoint_output, _seal
    workspace = _absolute(artifact_service.root).parent
    facts = _public_input_facts(dataset_binding, original_rdata_artifact_id, method_version_id, parameters,
        assumption_record_ref, design, artifact_service=artifact_service, workspace=workspace)
    originals, rdata, inspection, paired, reader = facts
    execution = {**deepcopy(originals), "airway_rdata": deepcopy(rdata)}
    output = require_disjoint_output(output, [artifact_service.artifact_path(ref["artifact_id"]) for ref in execution.values()])
    if not output.is_relative_to(workspace) or output.exists():
        raise ValueError("Original-airway preparation requires fresh assigned-workspace protected storage")
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    declarations_path = output / "declarations.json"
    declarations_path.write_bytes(canonical_json(dataset_binding["declarations"]))
    declarations_path.chmod(0o444)
    declarations_ref = _source(declarations_path)
    ports = _public_ports(method_version_id, parameters, design)
    receipt = {"format": "devbench.original-airway-input-transformation/3", "input_binding": deepcopy(dataset_binding),
        "method_ports": ports, "original_refs": deepcopy(originals), "execution_refs": execution,
        "original_rdata_ref": rdata, "assumption_record_ref": deepcopy(assumption_record_ref), "reader_contract": reader,
        "paired_design_audit": paired, "inspection_sha256": digest(inspection),
        "transformations": _public_transforms(execution)}
    receipt_path = output / "transformation-receipt.json"
    receipt_path.write_bytes(canonical_json(receipt))
    receipt_path.chmod(0o444)
    record = {"format": INPUT_FORMAT, "original_refs": originals, "execution_refs": execution,
        "transformation_receipt_ref": _source(receipt_path), "declarations_ref": declarations_ref,
        "method_ports_sha256": digest(ports), "capabilities": list(_INPUT_CAPABILITIES),
        "prerequisites": _public_prerequisites(declarations_ref, assumption_record_ref), "original_rdata_ref": rdata,
        "assumption_record_ref": deepcopy(assumption_record_ref)}
    sealed = _seal(output / "binding", "execution-inputs.json", record, "original_airway_execution_inputs_prepared")
    return {"record_ref": _source(output / "binding" / "execution-inputs.json"), **sealed}


def load_original_airway_execution_inputs(reference, *, artifact_service, dataset_binding=None, method_ports=None, workspace=None):
    """Revalidate actual retained inputs, method bytes, assumptions and pairing."""
    from .rna_execution_inputs import _read_source
    from .episode_proposals import _load
    workspace = _absolute(workspace) if workspace is not None else _absolute(artifact_service.root).parent
    if type(reference) is not dict:
        raise ValueError("An immutable original-airway input reference is required")
    if "manifest_path" in reference:
        if not _absolute(reference["manifest_path"]).is_relative_to(workspace):
            raise ValueError("Original-airway manifest lies outside assigned workspace")
        _, record, _ = _load(reference["manifest_path"], reference["root_hash"], "execution-inputs.json")
    else:
        record = _read_source(reference, workspace=workspace)
    if type(record) is not dict or set(record) != _INPUT_FIELDS or record["format"] != INPUT_FORMAT:
        raise ValueError("Use the closed original-airway /3 input contract")
    receipt = _read_source(record["transformation_receipt_ref"], workspace=workspace)
    receipt_fields = {"format", "input_binding", "method_ports", "original_refs", "execution_refs", "original_rdata_ref",
        "assumption_record_ref", "reader_contract", "paired_design_audit", "inspection_sha256", "transformations"}
    if type(receipt) is not dict or set(receipt) != receipt_fields or type(receipt.get("method_ports")) is not dict:
        raise ValueError("Use the closed original-airway transformation receipt")
    ports = receipt["method_ports"]
    if ports != _public_ports(ports.get("method_version_id"), ports.get("parameters"), ports.get("design")):
        raise PermissionError("Original-airway identity ports and inferential objective changed")
    if (receipt.get("format") != "devbench.original-airway-input-transformation/3"
            or dataset_binding is not None and receipt["input_binding"] != dataset_binding
            or method_ports is not None and ports != method_ports):
        raise PermissionError("Original-airway input, design or selected method binding differs")
    facts = _public_input_facts(receipt["input_binding"], record["original_rdata_ref"]["artifact_id"], ports["method_version_id"],
        ports["parameters"], record["assumption_record_ref"], ports["design"], artifact_service=artifact_service, workspace=workspace)
    originals, rdata, inspection, paired, reader = facts
    execution = {**deepcopy(originals), "airway_rdata": deepcopy(rdata)}
    if (record["original_refs"] != originals or receipt["original_refs"] != originals or record["original_rdata_ref"] != rdata
            or receipt["original_rdata_ref"] != rdata or record["execution_refs"] != execution or receipt["execution_refs"] != execution
            or receipt["reader_contract"] != reader or receipt["paired_design_audit"] != paired or receipt["inspection_sha256"] != digest(inspection)
            or receipt["assumption_record_ref"] != record["assumption_record_ref"]
            or _read_source(record["declarations_ref"], workspace=workspace) != receipt["input_binding"]["declarations"]
            or receipt["transformations"] != _public_transforms(execution)
            or record["method_ports_sha256"] != digest(ports) or record["capabilities"] != _INPUT_CAPABILITIES
            or record["prerequisites"] != _public_prerequisites(record["declarations_ref"], record["assumption_record_ref"])):
        raise PermissionError("Original-airway actual source, opaque input, paired design or assumption custody changed")
    return deepcopy(record)


def validate_original_airway_method_binding(execution_inputs, method_version_id, parameters, *, artifact_service):
    from .rna_execution_inputs import _read_source
    receipt = _read_source(execution_inputs["transformation_receipt_ref"], workspace=_absolute(artifact_service.root).parent)
    reader = receipt["reader_contract"]
    from devharness_core.methods import MethodContract
    method = artifact_service.method(method_version_id)
    selected = MethodContract.from_dict(method["contract"]).validate_parameters(parameters)
    if (reader["method_version_id"] != method_version_id or reader["contract_sha256"] != digest(method["contract"])
            or reader["parameters"] != selected):
        raise PermissionError("Actual original-airway consumer method, reader or parameters differ")
    return {"method_version_id": method_version_id, "parameters_sha256": digest(selected),
        "reader_binding": "original_airway_rdata_json_identity_requires_actual_r_qualification"}
