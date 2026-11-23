"""Closed /2 assessment records and descriptor-only RNA question profiles.

An intent freezes rules before generation; a plan binds retained workflow and
input identities afterward, without observed outputs. Hashes and caller labels
are consistency evidence, not authenticated authority or scientific approval.
No profile is computationally admitted by this module. A context projection is
not OS isolation, and an evidence-read flag does not enforce participant access.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import stat

from jsonschema import Draft202012Validator, ValidationError

from devharness_core.provenance import canonical_json
from devbench.study.evidence import _read, decode_json
from .checkpoints import atomic_json, digest
from .episode_proposals import _absolute, _load, _seal, require_disjoint_output
from .method_planner_contract import _copy_json
from .rna_input_contract import MAX_MATRIX_BYTES
from .rna_scientific_trial_contract import NAME, TEXT, HASH, SOURCE, REFERENCE, SCHEMA as TRIAL_REQUEST_SCHEMA, obj, array, optional


ESTIMAND_FORMAT = "devbench.rna-estimand/2"
INTENT_FORMAT = "devbench.rna-assessment-intent/2"
PLAN_FORMAT = "devbench.rna-assessment-plan/2"
ANALYST_PROJECTION_FORMAT = "devbench.rna-analyst-projection/2"
EVALUATOR_PROJECTION_FORMAT = "devbench.rna-evaluator-projection/2"
REFERENCE_RESULT_SET_FORMAT = "devbench.rna-reference-result-set/2"
MAX_RECORD_BYTES = 1_000_000
MAX_AGGREGATE_BYTES = 20_000_000
MAX_ARTIFACT_BYTES = MAX_MATRIX_BYTES
# The existing 100 MB per-input bound also covers each derived input. Original
# matrix/metadata and derived matrix/metadata have a separate streaming budget.
MAX_AGGREGATE_ARTIFACT_BYTES = 4 * MAX_ARTIFACT_BYTES
MODES = ("exact_reproduction", "independent_method_selection", "external_validation")
METRIC_STATUSES = ("computed", "not_applicable", "not_assessable", "failed")

# IDs identify reviewed formulas, not default metrics or preferred methods.
FORMULA_REGISTRY = {key: "1" for key in (
    "gene_effect_absolute_difference", "gene_effect_direction_agreement", "gene_effect_rmse",
    "gene_table_coverage", "gene_set_precision", "gene_set_recall", "gene_set_f1", "gene_set_jaccard")}
FORMULA_REGISTRY["gene_effect_direction_match"] = "1"  # Typed descriptor; no admitted computation adapter.
PROFILE_REGISTRY = {
    "mean_expression": {"relationships": ["contrast", "association"], "entities": ["gene"],
        "capabilities": ["bulk_gene_expression", "independent_biological_units", "identifiable_design"]},
    "measured_time": {"relationships": ["temporal"], "entities": ["gene"],
        "capabilities": ["bulk_gene_expression", "measured_time", "independent_biological_units", "identifiable_design"]},
    "pathway": {"relationships": ["enrichment"], "entities": ["pathway"],
        "capabilities": ["ranked_gene_statistics", "tested_gene_universe", "versioned_gene_sets"]},
    "composed": {"relationships": ["composed"], "entities": ["gene", "pathway"],
        "capabilities": ["bulk_gene_expression", "measured_time", "independent_biological_units", "identifiable_design",
                         "ranked_gene_statistics", "tested_gene_universe", "versioned_gene_sets"]},
    "splicing": {"relationships": ["contrast", "temporal"], "entities": ["transcript", "exon", "junction"],
        "capabilities": ["transcript_exon_junction_measurements", "versioned_annotation", "independent_biological_units"]},
    "cell_abundance": {"relationships": ["composition"], "entities": ["cell"],
        "capabilities": ["per_cell_measurements", "cell_specimen_donor_mapping", "compositional_denominators"]},
    "lineage": {"relationships": ["ordering"], "entities": ["cell"],
        "capabilities": ["per_cell_measurements", "cell_specimen_donor_mapping", "independent_lineage_evidence"]},
    "regulatory_association": {"relationships": ["association"], "entities": ["network_edge"],
        "capabilities": ["association_evidence", "declared_network_evidence_class"]},
    "prediction": {"relationships": ["prediction"], "entities": ["signature"],
        "capabilities": ["group_aware_validation", "sealed_test_outcomes", "validation_population"]},
}
_OUTPUT_SCHEMAS = (
    "devbench.rna-gene-table/2", "devbench.rna-curve-table/2", "devbench.rna-pathway-table/2",
    "devbench.rna-selected-set/2", "devbench.rna-splicing-table/2", "devbench.rna-cell-table/2",
    "devbench.rna-lineage-graph/2", "devbench.rna-network-graph/2", "devbench.rna-prediction-table/2")
_PLOT_KINDS = ("effect_scatter", "gene_table_coverage", "temporal_curve", "pathway_effects", "selected_set_agreement",
               "usage_effects", "cell_abundance", "lineage_topology", "network_association", "prediction_calibration")
_POST_RESULT_FIELDS = {"observed_result", "observed_results", "metric_results", "analysis_output_bundle_ref",
                       "result_values", "measured_values", "observed_outputs", "post_result_thresholds", "chosen_after_results",
                       "output_sha256", "outputs_sha256", "observed_artifact_refs", "result_artifact_refs"}
_OUTCOME_FIELDS = _POST_RESULT_FIELDS | {"expected_effects", "expected_directions", "reference_genes", "selected_reference_genes",
    "reference_findings", "reference_bundle_ref", "evaluator_projection_ref", "source_paths", "outcome_excerpts"}

ARTIFACT_REFERENCE = obj({"artifact_id": HASH, "sha256": HASH,
    "size_bytes": {"type": "integer", "minimum": 1, "maximum": MAX_ARTIFACT_BYTES},
    "artifact_type": NAME, "suffix": {"type": "string", "maxLength": 24}, "generated_by": optional(NAME)})
IMMUTABLE_REFERENCE = {"oneOf": [SOURCE, REFERENCE, ARTIFACT_REFERENCE]}
_LOCAL_OR_MANIFEST = {"oneOf": [SOURCE, REFERENCE]}
_NUMBER = {"type": "number"}
_UNKNOWN = obj({"field": NAME, "reason": TEXT})
_RELATIONSHIPS = ["contrast", "temporal", "association", "enrichment", "composition", "prediction", "ordering", "composed", "unknown"]
_ENTITY_TYPES = ["gene", "transcript", "exon", "junction", "cell", "pathway", "network_edge", "signature", "unknown"]
_MEASUREMENT_SCALES = ["raw_counts", "normalized_expression", "continuous", "usage", "proportion", "ordering", "association", "prediction"]
_CLAIM_SCOPES = ["descriptive", "association", "treatment_effect", "temporal_change", "enrichment", "usage",
                 "cell_abundance", "lineage", "regulatory_association", "prediction", "causal", "composed", "unknown"]
_FRACTION_METRICS = {"gene_effect_direction_agreement", "gene_table_coverage", "gene_set_precision", "gene_set_recall", "gene_set_f1", "gene_set_jaccard"}
DATASET_DESCRIPTOR_SCHEMA = obj({"format": {"const": "devbench.rna-trial-dataset/2"},
                                **TRIAL_REQUEST_SCHEMA["properties"]["dataset"]["properties"]})
REQUIREMENT_RECORD_SCHEMA = obj({"format": {"const": "devbench.rna-requirement-record/2"}, "requirements": array(obj({
    "requirement_id": NAME, "origin": {"enum": ["user", "assistant", "literature", "fixture", "unknown"]},
    "name": NAME, "value": {}, "evidence": TEXT}), 64, 1)})
ESTIMAND_SCHEMA = obj({
    "format": {"const": ESTIMAND_FORMAT}, "estimand_id": NAME,
    "requirement_sources": array(obj({"origin": {"enum": ["user", "assistant", "literature", "fixture", "unknown"]},
        "record_ref": SOURCE, "requirement_ids": array(NAME, 32, 1)}), 32, 1),
    "assay": optional(TEXT), "measurement": obj({"quantity": optional(TEXT), "units": optional(TEXT),
        "scale": {"enum": [*_MEASUREMENT_SCALES, None]}}),
    "population": optional(TEXT), "biological_unit": obj({"independent_unit": optional(NAME), "dependent_unit": optional(NAME),
        "dependence": {"enum": ["independent", "repeated", "clustered", "unknown"]}}),
    "entity_type": {"enum": _ENTITY_TYPES}, "relationship": obj({"kind": {"enum": _RELATIONSHIPS}, "definition": optional(TEXT)}),
    "time_reference": optional(TEXT), "denominator": optional(TEXT),
    "adjustment": obj({"covariates": array(NAME), "assumptions": array(TEXT), "unknowns": array(TEXT)}),
    "claim_scope": {"enum": _CLAIM_SCOPES}, "unknowns": array(_UNKNOWN, 32),
})
ACCEPTANCE_POLICY_SCHEMA = obj({"kind": {"enum": ["report_only", "agreement_bound"]},
    "operator": {"enum": ["less_equal", "greater_equal", None]},
    "bound": optional({"type": "number", "minimum": 0}), "rationale": TEXT, "source_ref": optional(SOURCE)})
METRIC_SPEC_SCHEMA = obj({"metric_id": {"enum": list(FORMULA_REGISTRY)}, "formula_version": NAME, "units": TEXT,
    "parameter_record_ref": SOURCE, "comparability_policy_ref": SOURCE, "universe_rule_id": NAME,
    "uncertainty_policy": obj({"kind": {"enum": ["report_only", "require_interval", "require_independent_resampling"]}, "rationale": TEXT}),
    "acceptance_policy": ACCEPTANCE_POLICY_SCHEMA, "required": {"type": "boolean"}})
PROFILE_SPEC_SCHEMA = obj({"profile_id": {"enum": list(PROFILE_REGISTRY)}, "profile_version": {"const": "2"},
    "required_input_capabilities": array(NAME, 32, 1),
    "applicability_rules": array({"oneOf": [obj({"predicate_id": {"const": key}, "values": array({"enum": values}, 32, 1)})
        for key, values in (("relationship_in", _RELATIONSHIPS), ("entity_type_in", _ENTITY_TYPES),
                            ("measurement_scale_in", _MEASUREMENT_SCALES), ("claim_scope_in", _CLAIM_SCOPES))]}, 32, 1),
    "metric_specs": array(METRIC_SPEC_SCHEMA, 32),
    "required_output_schemas": array({"enum": _OUTPUT_SCHEMAS}, 16), "required_plot_kinds": array({"enum": _PLOT_KINDS}, 16),
    "permitted_claim_scopes": array({"enum": _CLAIM_SCOPES}, 32, 1), "missingness_policy": {"const": "report_and_abstain"}})
ASSESSMENT_INTENT_SCHEMA = obj({"format": {"const": INTENT_FORMAT}, "intent_id": NAME, "mode": {"enum": MODES},
    "question_ref": SOURCE, "dataset_ref": IMMUTABLE_REFERENCE, "reference_bundle_ref": _LOCAL_OR_MANIFEST,
    "exposure_ref": SOURCE, "estimand": ESTIMAND_SCHEMA, "profile_specs": array(PROFILE_SPEC_SCHEMA, 16, 1),
    "universe_policy_ref": SOURCE, "required_claim_ids": array(NAME, 128, 1), "analyst_projection_ref": SOURCE,
    "evaluator_projection_ref": SOURCE, "decision_policy": obj({"kind": {"enum": ["report_only", "all_required_metrics"]}, "rationale": TEXT}),
    "limitations": array(TEXT, 32, 1)})
ASSESSMENT_PLAN_SCHEMA = obj({"format": {"const": PLAN_FORMAT}, "plan_id": NAME, "intent_ref": _LOCAL_OR_MANIFEST,
    "workflow_ref": SOURCE, "execution_inputs_ref": _LOCAL_OR_MANIFEST, "resolved_capabilities_ref": SOURCE,
    "finalization_receipt_ref": SOURCE, "execution_prerequisites_ref": SOURCE})
ANALYST_PROJECTION_SCHEMA = obj({"format": {"const": ANALYST_PROJECTION_FORMAT}, "mode": {"enum": MODES},
    "question_ref": SOURCE, "dataset_ref": IMMUTABLE_REFERENCE, "estimand": ESTIMAND_SCHEMA,
    "permitted_method_excerpts": array(obj({"excerpt_id": NAME, "purpose": {"enum": ["methods", "data"]},
        "text": TEXT, "citation_id": NAME, "limitations": TEXT}), 32), "required_output_schemas": array({"enum": _OUTPUT_SCHEMAS}, 16),
    "omitted_evidence": array(TEXT, 32), "scope": {"const": "context_projection_not_os_isolation"}, "limitations": TEXT})
EVALUATOR_PROJECTION_SCHEMA = obj({"format": {"const": EVALUATOR_PROJECTION_FORMAT}, "reference_bundle_ref": _LOCAL_OR_MANIFEST,
    "exposure_ref": SOURCE, "required_claim_ids": array(NAME, 128, 1), "omitted_evidence": array(TEXT, 32),
    "scope": {"const": "context_projection_not_os_isolation"}, "limitations": TEXT})
TYPED_METRIC_VALUE = obj({"kind": {"enum": ["direction", "categorical"]}, "value": NAME})
METRIC_RESULT_SCHEMA = obj({"metric_id": {"enum": list(FORMULA_REGISTRY)}, "profile_id": {"enum": list(PROFILE_REGISTRY)},
    "estimand_id": NAME, "status": {"enum": METRIC_STATUSES}, "value": {"anyOf": [_NUMBER, TYPED_METRIC_VALUE, {"type": "null"}]},
    "units": TEXT, "denominators": {"type": "object", "minProperties": 1, "maxProperties": 16,
        "propertyNames": NAME, "additionalProperties": {"type": "integer", "minimum": 0}},
    "universe_ref": optional(IMMUTABLE_REFERENCE), "exclusions_ref": optional(IMMUTABLE_REFERENCE),
    "uncertainty_ref": optional(IMMUTABLE_REFERENCE), "reason_codes": array(NAME, 32), "evidence_refs": array(IMMUTABLE_REFERENCE, 32)})
REFERENCE_VALUE_SCHEMA = {"oneOf": [
    obj({"kind": {"const": "exact"}, "value": _NUMBER, "units": TEXT}),
    obj({"kind": {"enum": ["left_censored", "right_censored"]}, "bound": _NUMBER, "units": TEXT}),
    obj({"kind": {"const": "interval"}, "lower": _NUMBER, "upper": _NUMBER, "units": TEXT}),
    obj({"kind": {"enum": ["direction", "categorical"]}, "value": NAME, "units": TEXT}),
    obj({"kind": {"const": "unknown"}, "reason": TEXT, "units": optional(TEXT)}),
]}
REFERENCE_FINDING_SCHEMA = obj({"claim_id": NAME, "estimand_ref": _LOCAL_OR_MANIFEST, "entity_refs": array(IMMUTABLE_REFERENCE, 32, 1),
    "source_locator_refs": array(SOURCE, 32, 1), "extraction_record_ref": SOURCE, "value_or_censoring": REFERENCE_VALUE_SCHEMA,
    "uncertainty_ref": optional(SOURCE), "tested_family_ref": optional(SOURCE),
    "reporting_completeness": {"enum": ["complete", "highlights_only", "partial", "unknown"]},
    "context_review_ref": SOURCE, "limitations": array(TEXT, 32, 1)})
REFERENCE_RESULT_SET_SCHEMA = obj({"format": {"const": REFERENCE_RESULT_SET_FORMAT}, "reference_set_id": NAME,
    "publication_ref": REFERENCE, "source_collection_ref": optional(REFERENCE),
    "estimand_ref": _LOCAL_OR_MANIFEST, "findings": array(REFERENCE_FINDING_SCHEMA, 128), "universe_ref": optional(SOURCE),
    "exposure_ref": SOURCE, "limitations": array(TEXT, 32, 1)})


def _record(value, schema):
    value = _copy_json(value)  # Bound nesting, keys, containers and finite values before schema traversal.
    if len(canonical_json(value)) > MAX_RECORD_BYTES:
        raise ValueError("Assessment JSON exceeds its record byte bound")
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as error:
        raise ValueError("Invalid closed assessment fields: " + error.message[:240]) from error
    return value


def _ref_shape(value):
    return _record(value, IMMUTABLE_REFERENCE)


def _decode_record(body):
    try:
        return _copy_json(decode_json(body))
    except (RecursionError, UnicodeError, TypeError, ValueError) as error:
        raise ValueError("Invalid bounded assessment reference JSON") from error


class _References:
    """Per-validation bounded read cache; never an execution or authority store."""
    def __init__(self, workspace=None, service=None):
        self.root = _absolute(workspace if workspace is not None else Path.cwd())
        self.service = service
        self.cache = {}
        self.remaining = MAX_AGGREGATE_BYTES
        self.artifact_bytes = set()
        self.remaining_artifacts = MAX_AGGREGATE_ARTIFACT_BYTES

    def path(self, value):
        path = _absolute(value)
        if not path.is_relative_to(self.root):
            raise ValueError("Assessment reference lies outside the assigned workspace")
        return path

    def read(self, value, *, json_record=False):
        value = _ref_shape(value)
        key = digest(value)
        if key not in self.cache:
            if "manifest_path" in value:
                path = self.path(value["manifest_path"])
                manifest_body = _read(path, min(MAX_RECORD_BYTES, self.remaining))
                manifest = _decode_record(manifest_body)
                artifacts = manifest.get("artifacts") if type(manifest) is dict else None
                if type(artifacts) is not list or len(artifacts) != 1:
                    raise ValueError("Assessment requires a single bounded retained manifest record")
                filename = artifacts[0].get("name") if type(artifacts[0]) is dict else None
                if type(filename) is not str or Path(filename).name != filename or filename in {".", ".."}:
                    raise ValueError("Unsafe manifest record name")
                size = artifacts[0].get("size_bytes")
                if type(size) is not int or not 1 <= size <= MAX_RECORD_BYTES or len(manifest_body) + size > self.remaining:
                    raise ValueError("Manifest and record exceed the aggregate metadata byte bound")
                self.path(path.parent.parent / filename)
                root, record, _ = _load(path, value["root_hash"], filename)
                self.path(root / filename)
                body = canonical_json(_copy_json(record))
                cost = len(manifest_body) + size
            elif "path" in value:
                path = self.path(value["path"])
                body = _read(path, min(MAX_RECORD_BYTES, self.remaining))
                if len(body) != value["size_bytes"] or hashlib.sha256(body).hexdigest() != value["sha256"]:
                    raise ValueError("Assessment source byte count or digest mismatch")
            else:
                if self.service is None:
                    raise ValueError("An actual artifact service is required to verify an artifact reference")
                self.path(self.service.root)
                retained = self.service.get("artifact", value["artifact_id"])
                if retained != value:
                    raise ValueError("Artifact service identity or checksum mismatch")
                if value["size_bytes"] > MAX_ARTIFACT_BYTES:
                    raise ValueError("Artifact input exceeds the existing data byte bound")
                path = self.path(Path(self.service.root) / "objects" / value["sha256"])
                try:
                    info = path.stat(follow_symlinks=False)
                except OSError as error:
                    raise ValueError("Artifact input stat verification failed") from error
                if not stat.S_ISREG(info.st_mode) or info.st_size != value["size_bytes"] or info.st_size > MAX_ARTIFACT_BYTES:
                    raise ValueError("Artifact input must be a bounded regular file with matching byte count")
                blob = (value["sha256"], value["size_bytes"])
                pending_bytes = 0 if blob in self.artifact_bytes else info.st_size
                if pending_bytes > self.remaining_artifacts:
                    raise ValueError("Artifact inputs exceed their aggregate streaming data bound")
                verified_path = self.path(self.service.artifact_path(value["artifact_id"]))
                if verified_path != path:
                    raise ValueError("Verified artifact path differs from its retained object identity")
                if pending_bytes:
                    self.remaining_artifacts -= pending_bytes
                    self.artifact_bytes.add(blob)
                body = None  # Custody streams hashes through the service; never cache a matrix.
            if "manifest_path" not in value:
                cost = len(body) if body is not None else 0
            self.remaining -= cost
            if self.remaining < 0:
                raise ValueError("Assessment references exceed their aggregate byte bound")
            self.cache[key] = body
        body = self.cache[key]
        if json_record:
            if body is None:
                if "artifact_id" not in value or value["size_bytes"] > MAX_RECORD_BYTES:
                    raise ValueError("A bounded JSON record is required, not an expression matrix")
                path = self.path(self.service.artifact_path(value["artifact_id"]))
                body = _read(path, min(MAX_RECORD_BYTES, self.remaining))
                if len(body) != value["size_bytes"] or hashlib.sha256(body).hexdigest() != value["sha256"]:
                    raise ValueError("Artifact metadata changed while reading its exact identity")
                self.remaining -= len(body)
                self.cache[key] = body
            return _decode_record(body)
        return body


def _refs(value):
    if type(value) is dict:
        if set(value) in ({"path", "sha256", "size_bytes"}, {"manifest_path", "root_hash"},
                          {"artifact_id", "sha256", "size_bytes", "artifact_type", "suffix", "generated_by"}):
            yield value
        else:
            for child in value.values():
                yield from _refs(child)
    elif type(value) is list:
        for child in value:
            yield from _refs(child)


def _check_refs(value, context):
    for ref in _refs(value):
        context.read(ref)


def validate_immutable_reference(reference, *, workspace=None, service=None):
    result = _ref_shape(reference)
    _References(workspace, service).read(result)
    return result


def read_reference_json(reference, *, workspace=None, service=None):
    """Read one exact identity-bound metadata record; grants no retrieval authority."""
    return _References(workspace, service).read(reference, json_record=True)


def _estimand(value):
    value = _record(value, ESTIMAND_SCHEMA)
    unknowns = [item["field"] for item in value["unknowns"]]
    if len(unknowns) != len(set(unknowns)):
        raise ValueError("Duplicate estimand unknown fields")
    for field in ("assay", "population", "time_reference", "denominator", "measurement.quantity", "measurement.units",
                  "measurement.scale", "biological_unit.independent_unit", "biological_unit.dependent_unit", "relationship.definition"):
        actual = value
        for component in field.split("."):
            actual = actual[component]
        if actual is None and field not in unknowns:
            raise ValueError("An unknown estimand value requires its explicit field and reason: " + field)
    for field, actual in (("entity_type", value["entity_type"]), ("relationship.kind", value["relationship"]["kind"]),
                          ("claim_scope", value["claim_scope"]), ("biological_unit.dependence", value["biological_unit"]["dependence"])):
        if actual == "unknown" and field not in unknowns:
            raise ValueError("An unknown estimand declaration requires its explicit field and reason: " + field)
    return value


def validate_estimand(record, *, workspace=None, service=None):
    result = _estimand(record)
    context = _References(workspace, service)
    _check_refs(result, context)
    _check_requirement_sources(result, context)
    return result


def _check_requirement_sources(estimand, context):
    """Match explicit retained attribution; it remains caller-declared, not authenticated."""
    for declared in estimand["requirement_sources"]:
        record = _record(context.read(declared["record_ref"], json_record=True), REQUIREMENT_RECORD_SCHEMA)
        requirements = {item["requirement_id"]: item for item in record["requirements"]}
        if len(requirements) != len(record["requirements"]):
            raise ValueError("Duplicate retained requirement ID")
        for identity in declared["requirement_ids"]:
            if identity not in requirements or requirements[identity]["origin"] != declared["origin"]:
                raise ValueError("Requirement ID or origin attribution differs from the exact retained record")


def _metric(record):
    result = _record(record, METRIC_SPEC_SCHEMA)
    if result["formula_version"] != FORMULA_REGISTRY[result["metric_id"]]:
        raise ValueError("Unknown reviewed metric formula version")
    if result["metric_id"] in _FRACTION_METRICS and result["units"] != "fraction":
        raise ValueError("The reviewed metric formula requires explicit fraction units")
    if result["metric_id"] == "gene_effect_direction_match" and (result["units"] != "direction" or
            result["acceptance_policy"]["kind"] != "report_only"):
        raise ValueError("Typed direction descriptors require direction units and report-only decisions")
    policy = result["acceptance_policy"]
    if policy["kind"] == "report_only" and (policy["bound"] is not None or policy["source_ref"] is not None or policy["operator"] is not None):
        raise ValueError("A report-only policy has no acceptance bound")
    if policy["kind"] == "agreement_bound" and (policy["bound"] is None or policy["source_ref"] is None or policy["operator"] is None):
        raise ValueError("An agreement bound requires a justified value and retained source")
    if policy["kind"] == "agreement_bound":
        if result["metric_id"] in _FRACTION_METRICS:
            if policy["operator"] != "greater_equal" or policy["bound"] > 1:
                raise ValueError("A fraction agreement bound requires greater_equal and a bound in [0,1]")
        elif policy["operator"] != "less_equal":
            raise ValueError("A discrepancy agreement bound requires the less_equal operator")
    return result


def validate_metric_spec(record, *, workspace=None, service=None):
    result = _metric(record)
    _check_refs(result, _References(workspace, service))
    return result


def _profile(record):
    result = _record(record, PROFILE_SPEC_SCHEMA)
    registered = PROFILE_REGISTRY[result["profile_id"]]
    if not set(registered["capabilities"]).issubset(result["required_input_capabilities"]):
        raise ValueError("A profile cannot waive its required input capabilities")
    metrics = [_metric(item) for item in result["metric_specs"]]
    if len({item["metric_id"] for item in metrics}) != len(metrics):
        raise ValueError("Duplicate profile metric ID")
    predicates = {item["predicate_id"]: item["values"] for item in result["applicability_rules"]}
    if len(predicates) != len(result["applicability_rules"]):
        raise ValueError("Duplicate applicability predicate")
    for predicate, required in (("relationship_in", registered["relationships"]), ("entity_type_in", registered["entities"])):
        if predicate not in predicates or not set(predicates[predicate]).issubset(required):
            raise ValueError("Use registered applicability boundaries for the declared profile")
    return result


def validate_profile_spec(record, *, workspace=None, service=None):
    result = _profile(record)
    _check_refs(result, _References(workspace, service))
    return result


def select_applicable_profiles(estimand, capabilities):
    """Scientific descriptors with explicit limits; never execution admission or a winner."""
    estimate = _estimand(estimand)
    capabilities = _record(capabilities, array(NAME, 64))
    unknown = any(estimate["measurement"][key] is None for key in ("quantity", "units", "scale")) or (
        estimate["assay"] is None or estimate["population"] is None or estimate["biological_unit"]["independent_unit"] is None
        or estimate["biological_unit"]["dependence"] == "unknown" or estimate["claim_scope"] == "unknown"
        or estimate["entity_type"] == "unknown" or estimate["relationship"]["kind"] == "unknown" or estimate["relationship"]["definition"] is None)
    results = []
    for key, registered in PROFILE_REGISTRY.items():
        missing = sorted(set(registered["capabilities"]) - set(capabilities))
        matches = estimate["relationship"]["kind"] in registered["relationships"] and estimate["entity_type"] in registered["entities"]
        status, applicability, reasons = "unsupported_profile", "applicable", ["unqualified_adapters_metrics_renderers_execution_route"]
        if unknown:
            status, applicability, reasons = "not_assessable", "unknown", ["unknown_estimand"]
        elif not matches:
            status, applicability, reasons = "not_applicable", "not_applicable", ["different_estimand_family"]
        elif key in {"measured_time", "composed"} and estimate["time_reference"] is None:
            status, applicability, reasons = "not_assessable", "unknown", ["unknown_time_reference"]
        elif missing:
            status, applicability, reasons = "unsupported_input", "unsupported_input", ["missing_required_input_capabilities"]
        results.append({"profile_id": key, "profile_version": "2", "status": status, "applicability": applicability,
                        "reason_codes": reasons, "missing_capabilities": missing, "computationally_supported": False})
    return results


def _forbidden_fields(value, forbidden):
    if type(value) is dict:
        if set(value) & forbidden:
            raise ValueError("Projection or pre-run record contains prohibited result or evaluator fields")
        for child in value.values():
            _forbidden_fields(child, forbidden)
    elif type(value) is list:
        for child in value:
            _forbidden_fields(child, forbidden)


def _retained_paths(value):
    """Collect path labels only; this does not follow or grant access to assets."""
    if type(value) is dict:
        for key, child in value.items():
            if key in {"path", "manifest_path", "source_path"} and type(child) is str and child.startswith("/"):
                yield child
            else:
                yield from _retained_paths(child)
    elif type(value) is list:
        for child in value:
            yield from _retained_paths(child)


def _strings(value):
    if type(value) is str:
        yield value
    elif type(value) is dict:
        for key, child in value.items():
            yield key
            yield from _strings(child)
    elif type(value) is list:
        for child in value:
            yield from _strings(child)


def _analyst_record_strings(reference, context):
    """Inspect bounded granted metadata, never follow arbitrary refs found inside it."""
    if "artifact_id" in reference:
        value = context.read(reference, json_record=True)
    else:
        body = context.read(reference)
        try:
            text = body.decode("utf-8")
            json.loads(text)
        except json.JSONDecodeError:
            value = text
        except (UnicodeError, RecursionError) as error:
            raise ValueError("Analyst-readable reference is not bounded UTF-8 metadata") from error
        else:
            value = _decode_record(body)
    yield from _strings(value)


def load_analyst_projection(reference, *, workspace=None, service=None):
    context = _References(workspace, service)
    result = _record(context.read(reference, json_record=True), ANALYST_PROJECTION_SCHEMA)
    _estimand(result["estimand"])
    _check_requirement_sources(result["estimand"], context)
    _forbidden_fields(result, _OUTCOME_FIELDS)
    _check_refs(result, context)
    return result


def _intent(record, context):
    result = _record(record, ASSESSMENT_INTENT_SCHEMA)
    _estimand(result["estimand"])
    _check_requirement_sources(result["estimand"], context)
    for profile in result["profile_specs"]:
        _profile(profile)
        if result["estimand"]["claim_scope"] not in profile["permitted_claim_scopes"]:
            raise ValueError("The estimand claim scope is outside the frozen permitted profile scopes")
        for metric in profile["metric_specs"]:
            if metric["metric_id"] not in _FRACTION_METRICS | {"gene_effect_direction_match"} and (
                    metric["units"] != result["estimand"]["measurement"]["units"]):
                raise ValueError("Metric units differ from the frozen effect estimand")
    if len({profile["profile_id"] for profile in result["profile_specs"]}) != len(result["profile_specs"]):
        raise ValueError("Duplicate assessment profile ID")
    if result["decision_policy"]["kind"] == "all_required_metrics" and not any(
            metric["required"] for profile in result["profile_specs"] for metric in profile["metric_specs"]):
        raise ValueError("An all-required decision needs explicit required metrics")
    _check_refs(result, context)
    from .rna_publication_evidence import validate_exposure
    validate_exposure(context.read(result["exposure_ref"], json_record=True))
    analyst = _record(context.read(result["analyst_projection_ref"], json_record=True), ANALYST_PROJECTION_SCHEMA)
    evaluator = _record(context.read(result["evaluator_projection_ref"], json_record=True), EVALUATOR_PROJECTION_SCHEMA)
    _forbidden_fields(analyst, _OUTCOME_FIELDS)
    for field in ("mode", "question_ref", "dataset_ref", "estimand"):
        if analyst[field] != result[field]:
            raise ValueError("Analyst projection binding differs from the frozen intent")
    schemas = sorted({schema for profile in result["profile_specs"] for schema in profile["required_output_schemas"]})
    if sorted(analyst["required_output_schemas"]) != schemas:
        raise ValueError("Analyst projection omitted or changed required output schemas")
    for field in ("reference_bundle_ref", "exposure_ref", "required_claim_ids"):
        if evaluator[field] != result[field]:
            raise ValueError("Evaluator projection binding differs from the frozen intent")
    denied = {result["reference_bundle_ref"].get("path", result["reference_bundle_ref"].get("manifest_path")),
              result["evaluator_projection_ref"]["path"]}
    reference_metadata = context.read(result["reference_bundle_ref"], json_record=True)
    denied.update(_retained_paths(reference_metadata))
    if type(reference_metadata) is dict and reference_metadata.get("format") == REFERENCE_RESULT_SET_FORMAT:
        grounding, _ = _reference_grounding(reference_metadata, context)
        denied.update(grounding["retained_source_paths"])
    if any(path in text for path in denied for text in _strings(analyst)):
        raise ValueError("Analyst projection leaks evaluator reference paths")
    for reference in _refs(analyst):
        if any(path in text for text in _analyst_record_strings(reference, context) for path in denied):
            raise ValueError("Analyst projection granted source record leaks evaluator reference paths")
    _check_refs(analyst, context)
    _check_refs(evaluator, context)
    return result


def validate_assessment_intent(record, *, workspace=None, service=None):
    return _intent(record, _References(workspace, service))


def assessment_rules_digest(intent):
    """Rules are separate immutable intent content, never copied observed results."""
    intent = _record(intent, ASSESSMENT_INTENT_SCHEMA)
    return digest({key: intent[key] for key in ("mode", "question_ref", "dataset_ref", "reference_bundle_ref", "exposure_ref", "estimand",
        "profile_specs", "universe_policy_ref", "required_claim_ids", "analyst_projection_ref", "evaluator_projection_ref", "decision_policy", "limitations")})


def _seal_record(output, record, filename, kind, context):
    output = context.path(output)
    inputs = [ref.get("path", ref.get("manifest_path")) for ref in _refs(record) if "artifact_id" not in ref]
    require_disjoint_output(output, inputs)
    receipt = _seal(output, filename, record, kind, scope="internal_assessment_not_execution_authority")
    return {"manifest_path": str(output / "audit/manifest.json"), "root_hash": receipt["root_hash"]}


def prepare_assessment_intent(output, record, *, workspace=None, service=None):
    context = _References(workspace, service)
    record = _intent(record, context)
    return _seal_record(output, record, "assessment-intent.json", "analysis.assessment_intent", context)


def load_assessment_intent(reference, *, workspace=None, service=None):
    context = _References(workspace, service)
    return _intent(context.read(_record(reference, _LOCAL_OR_MANIFEST), json_record=True), context)


def _workflow(reference, intent_reference, intent, context):
    value = context.read(reference, json_record=True)
    _forbidden_fields(value, _POST_RESULT_FIELDS)
    schema = obj({"format": {"const": "devbench.rna-assessment-workflow/2"}, "assessment_intent_ref": _LOCAL_OR_MANIFEST,
        "assessment_rules_sha256": HASH, "governed_workflow": {"type": "object"}, "governed_workflow_sha256": HASH})
    value = _record(value, schema)
    if value["assessment_intent_ref"] != intent_reference or value["assessment_rules_sha256"] != assessment_rules_digest(intent):
        raise ValueError("Workflow intent binding or frozen assessment rules changed")
    if value["governed_workflow_sha256"] != digest(value["governed_workflow"]):
        raise ValueError("Governed workflow snapshot digest changed")
    return value


def _execution_inputs(reference, context):
    candidate = context.read(reference, json_record=True)
    _forbidden_fields(candidate, _POST_RESULT_FIELDS)
    if context.service is None:
        raise ValueError("An actual artifact service is required for execution-input integrity")
    from .rna_execution_inputs import load_execution_inputs
    record = load_execution_inputs(reference, artifact_service=context.service, workspace=context.root)
    _check_refs(record, context)
    return record


def _workflow_inputs(workflow, intent, inputs, context):
    from .rna_scientific_trial import validate_assessment_workflow_snapshot
    dataset = _record(context.read(intent["dataset_ref"], json_record=True), DATASET_DESCRIPTOR_SCHEMA)
    _check_refs(dataset, context)
    validate_assessment_workflow_snapshot(workflow["governed_workflow"], methods=context.service,
                                        execution_inputs=inputs, dataset=dataset, workspace=context.root)


def _resolved(intent, inputs):
    selection = {item["profile_id"]: item for item in select_applicable_profiles(intent["estimand"], inputs["capabilities"])}
    results = []
    for spec in intent["profile_specs"]:
        item = deepcopy(selection[spec["profile_id"]])
        # Additional frozen capabilities may restrict, never waive the registry boundary.
        missing = sorted(set(spec["required_input_capabilities"]) - set(inputs["capabilities"]))
        if missing and item["applicability"] == "applicable":
            item.update(status="unsupported_input", applicability="unsupported_input",
                        reason_codes=["missing_required_input_capabilities"], missing_capabilities=missing)
        observed = {"relationship_in": intent["estimand"]["relationship"]["kind"], "entity_type_in": intent["estimand"]["entity_type"],
                    "measurement_scale_in": intent["estimand"]["measurement"]["scale"], "claim_scope_in": intent["estimand"]["claim_scope"]}
        if any(observed[rule["predicate_id"]] not in rule["values"] for rule in spec["applicability_rules"]):
            item.update(status="not_applicable", applicability="not_applicable", reason_codes=["frozen_applicability_predicate_not_met"])
        results.append(item)
    return {"format": "devbench.rna-resolved-profiles/2", "capabilities": deepcopy(inputs["capabilities"]), "profiles": results}


def _prerequisites(inputs, resolved):
    # No caller JSON can manufacture qualification of adapters or a production route.
    return {"format": "devbench.rna-assessment-prerequisites/2", "prerequisites": deepcopy(inputs["prerequisites"]),
        "computational_admission": "blocked", "blockers": ["unqualified_adapters_metrics_renderers_execution_route"] + [
            item["profile_id"] + ":" + item["status"] for item in resolved["profiles"]],
        "scope": "descriptor_only_not_execution_authority"}


def _write_source(path, value):
    atomic_json(path, value)
    body = _read(path, MAX_RECORD_BYTES)
    path.chmod(0o444)
    return {"path": str(path), "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body)}


def finalize_assessment_plan(intent, workflow_ref, execution_inputs_ref, *, output, workspace=None, service=None):
    """Bind an already retained intent to pre-run workflow/inputs; preserve blockers."""
    context = _References(workspace, service)
    if type(intent) is not dict or set(intent) not in ({"path", "sha256", "size_bytes"}, {"manifest_path", "root_hash"}):
        raise ValueError("An already immutable intent reference is required before finalization")
    intent_ref = _record(intent, _LOCAL_OR_MANIFEST)
    frozen = _intent(context.read(intent_ref, json_record=True), context)
    workflow_ref = _record(workflow_ref, SOURCE)
    execution_inputs_ref = _record(execution_inputs_ref, _LOCAL_OR_MANIFEST)
    workflow = _workflow(workflow_ref, intent_ref, frozen, context)
    inputs = _execution_inputs(execution_inputs_ref, context)
    _workflow_inputs(workflow, frozen, inputs, context)
    from .rna_scientific_trial import assert_assessment_workflow_not_started
    assert_assessment_workflow_not_started(workflow["governed_workflow"], methods=context.service)
    resolved = _resolved(frozen, inputs)
    prerequisites = _prerequisites(inputs, resolved)
    output = context.path(output)
    paths = [ref.get("path", ref.get("manifest_path")) for ref in _refs([frozen, intent_ref, workflow_ref, execution_inputs_ref, inputs])
             if "artifact_id" not in ref]
    require_disjoint_output(output, paths)
    from devbench.study.permissions import require_protected_storage
    require_protected_storage(output)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    plan = {"format": PLAN_FORMAT, "plan_id": "plan-" + digest([intent_ref, workflow_ref, execution_inputs_ref])[:32],
            "intent_ref": intent_ref, "workflow_ref": workflow_ref, "execution_inputs_ref": execution_inputs_ref,
            "resolved_capabilities_ref": _write_source(output / "resolved-capabilities.json", resolved),
            "execution_prerequisites_ref": _write_source(output / "execution-prerequisites.json", prerequisites)}
    receipt = {"format": "devbench.rna-assessment-finalization/2", "intent_ref": intent_ref,
        "intent_sha256": digest(frozen), "assessment_rules_sha256": assessment_rules_digest(frozen),
        "workflow_ref": workflow_ref, "execution_inputs_ref": execution_inputs_ref,
        "resolved_capabilities_ref": plan["resolved_capabilities_ref"], "execution_prerequisites_ref": plan["execution_prerequisites_ref"],
        "scope": "hash_bound_pre_run_consistency_not_authenticated_timing_or_authority"}
    plan["finalization_receipt_ref"] = _write_source(output / "finalization-receipt.json", receipt)
    _write_source(output / "assessment-plan.json", plan)
    return _record(plan, ASSESSMENT_PLAN_SCHEMA)


def _plan(record, context):
    result = _record(record, ASSESSMENT_PLAN_SCHEMA)
    _check_refs(result, context)
    intent = _intent(context.read(result["intent_ref"], json_record=True), context)
    workflow = _workflow(result["workflow_ref"], result["intent_ref"], intent, context)
    inputs = _execution_inputs(result["execution_inputs_ref"], context)
    _workflow_inputs(workflow, intent, inputs, context)
    resolved = _resolved(intent, inputs)
    if context.read(result["resolved_capabilities_ref"], json_record=True) != resolved:
        raise ValueError("Resolved capabilities drifted from frozen rules and retained inputs")
    if context.read(result["execution_prerequisites_ref"], json_record=True) != _prerequisites(inputs, resolved):
        raise ValueError("Execution prerequisites changed or caller tried to self-qualify")
    receipt = context.read(result["finalization_receipt_ref"], json_record=True)
    expected = {"format": "devbench.rna-assessment-finalization/2", "intent_ref": result["intent_ref"],
        "intent_sha256": digest(intent), "assessment_rules_sha256": assessment_rules_digest(intent),
        **{key: result[key] for key in ("workflow_ref", "execution_inputs_ref", "resolved_capabilities_ref", "execution_prerequisites_ref")},
        "scope": "hash_bound_pre_run_consistency_not_authenticated_timing_or_authority"}
    if receipt != expected:
        raise ValueError("Finalization receipt intent binding or frozen rules changed")
    if result["plan_id"] != "plan-" + digest([result["intent_ref"], result["workflow_ref"], result["execution_inputs_ref"]])[:32]:
        raise ValueError("Assessment plan identity binding mismatch")
    return result


def validate_assessment_plan(record, *, workspace=None, service=None):
    return _plan(record, _References(workspace, service))


def prepare_assessment_plan(output, record, *, workspace=None, service=None):
    context = _References(workspace, service)
    result = _plan(record, context)
    return _seal_record(output, result, "assessment-plan.json", "analysis.assessment_plan", context)


def load_assessment_plan(reference, *, workspace=None, service=None):
    context = _References(workspace, service)
    return _plan(context.read(_record(reference, _LOCAL_OR_MANIFEST), json_record=True), context)


def validate_metric_result(record, *, workspace=None, service=None):
    result = _record(record, METRIC_RESULT_SCHEMA)
    if result["status"] == "computed":
        if result["value"] is None or not result["evidence_refs"] or result["universe_ref"] is None or result["exclusions_ref"] is None:
            raise ValueError("Computed metrics require actual values, universe/exclusion identities and evidence")
        if result["metric_id"] in _FRACTION_METRICS and (type(result["value"]) not in (int, float) or not 0 <= result["value"] <= 1):
            raise ValueError("Fraction formula results require numeric fractions in [0,1]")
        if result["metric_id"] == "gene_effect_direction_match" and (type(result["value"]) is not dict or
                result["value"]["kind"] != "direction" or result["value"]["value"] not in {"same", "opposite", "both_zero", "one_zero"}):
            raise ValueError("A typed direction descriptor requires an explicit registered direction category")
        if result["metric_id"] not in _FRACTION_METRICS | {"gene_effect_direction_match"} and type(result["value"]) not in (int, float):
            raise ValueError("Effect discrepancy formula results require finite numeric values")
        if result["metric_id"] not in _FRACTION_METRICS | {"gene_effect_direction_match"} and result["value"] < 0:
            raise ValueError("Absolute discrepancy/RMSE results must be nonnegative")
        counts = result["denominators"]
        denominator = ({"gene_set_precision": counts.get("actual"), "gene_set_recall": counts.get("reference"),
                        "gene_set_jaccard": counts.get("union"), "gene_table_coverage": counts.get("reference_rows"),
                        "gene_effect_direction_match": counts.get("matched")}).get(result["metric_id"], counts.get("assessed_rows"))
        if result["metric_id"] == "gene_set_f1":
            denominator = counts["actual"] + counts["reference"] if {"actual", "reference"} <= set(counts) else None
        if denominator is None or denominator <= 0:
            raise ValueError("A computed metric requires its declared positive formula denominator")
    elif result["value"] is not None or not result["reason_codes"]:
        raise ValueError("Noncomputed metrics require null values and explicit reasons")
    if result["metric_id"] in _FRACTION_METRICS and result["units"] != "fraction":
        raise ValueError("Fraction result units cannot be relabelled as effect units")
    if result["metric_id"] == "gene_effect_direction_match" and result["units"] != "direction":
        raise ValueError("Typed direction result requires direction units")
    _check_refs(result, _References(workspace, service))
    return result


def _reference_finding(record, context, estimand=None, for_comparison=False, *, publication_ref=None, source_collection_ref=None,
                       grounding=None, grounding_frame=None):
    result = _record(record, REFERENCE_FINDING_SCHEMA)
    _check_refs(result, context)
    estimate = _estimand(context.read(result["estimand_ref"], json_record=True))
    _check_requirement_sources(estimate, context)
    if estimand is not None and estimate != _estimand(estimand):
        raise ValueError("Reference finding estimand mismatch")
    value = result["value_or_censoring"]
    if value["kind"] == "interval" and value["lower"] > value["upper"]:
        raise ValueError("Reference interval bounds are reversed")
    if value["kind"] != "unknown" and value["units"] != estimate["measurement"]["units"]:
        raise ValueError("Reference finding units differ from its estimand")
    if for_comparison:
        if publication_ref is None:
            raise ValueError("Numerical reference eligibility requires a selected publication grounding binding")
        from .rna_reference_extraction import validate_context_review, validate_reference_number_record
        from .rna_publication_sources import (validate_reference_source_membership, validate_reference_grounding_binding,
                                              _prepare_reference_grounding_frame)
        if grounding_frame is None:
            grounding_frame = _prepare_reference_grounding_frame(publication_ref, source_collection_ref=source_collection_ref,
                workspace=context.root, allow_evidence_read=True)
        if grounding is None:
            grounding = validate_reference_grounding_binding(publication_ref, source_collection_ref=source_collection_ref,
                workspace=context.root, allow_evidence_read=True, _grounding_frame=grounding_frame)
        review = context.read(result["context_review_ref"], json_record=True)
        if review.get("proposal_author_id") != grounding["participant_id"] or review.get("reviewed_by") != grounding["curator_id"]:
            raise ValueError("Reference context curator or participant identity differs from its selected publication")
        extraction = context.read(result["extraction_record_ref"], json_record=True)
        verified = False
        for ref in result["source_locator_refs"]:
            locator = context.read(ref, json_record=True)
            if type(locator) is not dict or set(locator) != {"source_ref", "excerpt"}:
                raise ValueError("Comparison requires an exact source identity and retained excerpt locator")
            excerpt = locator["excerpt"]
            if type(excerpt) is not dict or excerpt.get("visibility") != "evaluator" or excerpt.get("purpose") != "outcome":
                raise ValueError("Reference values require evaluator-held outcome locators")
            membership = validate_reference_source_membership(publication_ref, locator["source_ref"], excerpt,
                source_collection_ref=source_collection_ref, workspace=context.root, allow_evidence_read=True,
                _grounding_frame=grounding_frame)
            if membership["locator_record_ref"] is not None and membership["locator_record_ref"] != ref:
                raise ValueError("Reference locator differs from its admitted source collection artifact")
            body = context.read(_record(locator["source_ref"], SOURCE))
            if review.get("excerpt_id") == excerpt.get("excerpt_id"):
                checked = validate_context_review(review, excerpt=excerpt, body=body, participant_id=review.get("proposal_author_id"))
                if checked["conclusion"] != "supported":
                    raise ValueError("Reference finding context is " + checked["conclusion"] + "; not assessable for numeric comparison")
                if value["kind"] in {"exact", "left_censored", "right_censored"}:
                    numerical = validate_reference_number_record(extraction, body=body, locator=excerpt["locator"])
                    if value["kind"] == "exact":
                        if numerical["number_type"] != "exact" or Decimal(str(value["value"])) != Decimal(numerical["canonical_decimal"]):
                            raise ValueError("Reference value differs from retained extraction evidence")
                    else:
                        operators = {"left_censored": {"<", "<="}, "right_censored": {">", ">="}}
                        if numerical["number_type"] != "censored" or numerical["operator"] not in operators[value["kind"]] or (
                                Decimal(str(value["bound"])) != Decimal(numerical["bound_decimal"])):
                            raise ValueError("Reference censoring differs from retained extraction evidence")
                else:
                    raise ValueError("This typed reference extraction is not yet assessable for comparison")
                verified = True
        if not verified:
            raise ValueError("Reference finding lacks matched contextual source review")
    return result


def validate_reference_finding(record, *, estimand=None, workspace=None, service=None, for_comparison=False,
                               publication_ref=None, source_collection_ref=None):
    return _reference_finding(record, _References(workspace, service), estimand, for_comparison,
                              publication_ref=publication_ref, source_collection_ref=source_collection_ref)


def _reference_grounding(record, context, expected_publication_ref=None):
    record = _record(record, REFERENCE_RESULT_SET_SCHEMA)
    if expected_publication_ref is not None and record["publication_ref"] != _record(expected_publication_ref, REFERENCE):
        raise ValueError("Reference results selected publication binding mismatch")
    context.read(record["publication_ref"])
    if record["source_collection_ref"] is not None:
        context.read(record["source_collection_ref"])
    from .rna_publication_sources import validate_reference_grounding_binding, _prepare_reference_grounding_frame
    frame = _prepare_reference_grounding_frame(record["publication_ref"], source_collection_ref=record["source_collection_ref"],
                                              workspace=context.root, allow_evidence_read=True)
    grounding = validate_reference_grounding_binding(record["publication_ref"], source_collection_ref=record["source_collection_ref"],
        workspace=context.root, allow_evidence_read=True, _grounding_frame=frame)
    from .rna_publication_evidence import validate_exposure
    exposure = validate_exposure(context.read(record["exposure_ref"], json_record=True))
    if exposure != grounding["exposure"]:
        raise ValueError("Reference result exposure conflicts with the selected retained publication context")
    return grounding, frame


def validate_reference_result_set(record, *, workspace=None, service=None, for_comparison=False, expected_publication_ref=None):
    context = _References(workspace, service)
    result = _record(record, REFERENCE_RESULT_SET_SCHEMA)
    grounding, frame = _reference_grounding(result, context, expected_publication_ref)
    _check_refs(result, context)
    from .rna_publication_evidence import validate_exposure
    validate_exposure(context.read(result["exposure_ref"], json_record=True))
    estimate = _estimand(context.read(result["estimand_ref"], json_record=True))
    _check_requirement_sources(estimate, context)
    ids = []
    for item in result["findings"]:
        _reference_finding(item, context, estimate, for_comparison, publication_ref=result["publication_ref"],
                           source_collection_ref=result["source_collection_ref"], grounding=grounding, grounding_frame=frame)
        ids.append(item["claim_id"])
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate reference claim ID")
    return result


def load_reference_result_set(reference, *, workspace=None, service=None, allow_evidence_read=False, expected_publication_ref=None):
    """Evaluator read seam; the flag is authority intent, not participant isolation."""
    if allow_evidence_read is not True:
        raise PermissionError("Reference outcome reading requires explicit evidence-read authority")
    record = read_reference_json(reference, workspace=workspace, service=service)
    return validate_reference_result_set(record, workspace=workspace, service=service, for_comparison=True,
                                         expected_publication_ref=expected_publication_ref)


def load_reference_grounding_binding(reference, *, workspace=None, service=None, expected_publication_ref=None, allow_evidence_read=False):
    """Return grounding refs without traversing the wrapper's finding references.

    Trusted coordinator integrity checks read admitted publication/collection
    source assets, which may overlap finding locator artifacts. Unadmitted
    finding extraction, family, entity and context records are not traversed.
    The output contains refs only; this confers no participant access and does
    not establish blinding or OS isolation.
    """
    if allow_evidence_read is not True:
        raise PermissionError("Reference grounding metadata requires explicit evidence-read authority")
    context = _References(workspace, service)
    record = _record(context.read(reference, json_record=True), REFERENCE_RESULT_SET_SCHEMA)
    _reference_grounding(record, context, expected_publication_ref)
    return {key: deepcopy(record[key]) for key in ("publication_ref", "source_collection_ref")}
