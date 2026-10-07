"""Immutable, finite-JSON contracts shared by custom and registered methods.

Parsing a contract never opens a source, environment, or data artifact. Executors
must verify those identities at their boundary. JSON Schema defaults are
annotations: parameter validation does not insert them or mutate caller data.
"""

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, SchemaError, ValidationError
from referencing import Registry

from devharness_core.provenance import _digest, canonical_json

METHOD_FORMAT = "devharness.method/1"
_DRAFT = "https://json-schema.org/draft/2020-12/schema"
_NAME = {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"}
_TEXT = {"type": "string", "minLength": 1, "maxLength": 4096, "pattern": r"^[^\x00]+$"}
_HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_ARTIFACT = {"enum": ["anndata", "json", "table", "file"]}
INVARIANTS = (
    "observation_ids_unique", "retained_observation_ids_subset_of_input",
    "variables_unchanged", "retained_count_matrix_unchanged",
)
OBSERVATION_SELECTION_INVARIANTS = (
    "observation_ids_unique", "retained_observation_ids_unique",
    "retained_observation_ids_subset_of_input", "retained_observation_ids_in_input_order",
    "immutable_observation_rows_selected",
)
_ALL_INVARIANTS = tuple(dict.fromkeys((*INVARIANTS, *OBSERVATION_SELECTION_INVARIANTS)))
ACTION_SCOPE_FORMAT = "devbench.method-action/1"
DIAGNOSTIC_OUTPUT_FORMAT = "devbench.observation-diagnostics/1"
DIAGNOSTIC_PROFILE_OUTPUT_FORMAT = "devbench.observation-diagnostics/2"
DIAGNOSTIC_INVARIANTS = (*OBSERVATION_SELECTION_INVARIANTS, "full_observation_coverage", "full_retention")
DIAGNOSTIC_REASONS = {
    "numerical_atypicality": {"flagged": ("Unusual under the supplied representation",),
                             "not_flagged": ("Within reference",)},
    "qc_review": {"flagged": ("Warrants QC review",),
                  "not_flagged": ("No QC review flag under the supplied reference",)},
}
_UNAVAILABLE_DIAGNOSTIC_REASONS = ("Reference unavailable", "Measurement unavailable", "Score unavailable", "Insufficient evidence")


def diagnostic_reasons(interpretation, status):
    """Coordinator-admitted language; candidate explanations cannot widen scope."""
    if interpretation not in DIAGNOSTIC_REASONS or status not in {"flagged", "not_flagged", "unavailable"}:
        raise ValueError("Unknown diagnostic interpretation or status")
    return _UNAVAILABLE_DIAGNOSTIC_REASONS if status == "unavailable" else DIAGNOSTIC_REASONS[interpretation][status]


_ALL_INVARIANTS = tuple(dict.fromkeys((*_ALL_INVARIANTS, *DIAGNOSTIC_INVARIANTS)))


def _object(properties, required=None):
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": list(properties) if required is None else required}


_ACTION_SCOPE = _object({
    "format": {"const": ACTION_SCOPE_FORMAT},
    "intent": {"enum": ["retain", "diagnostic_flagging", "exclude", "infer"]},
    "interpretation": {"enum": ["none", "numerical_atypicality", "qc_review"]},
    "exclusions": {"type": "boolean"},
})
_ACTION_SCOPE["allOf"] = [{
    "if": {"properties": {"intent": {"enum": ["retain", "diagnostic_flagging"]}}},
    "then": {"properties": {"exclusions": {"const": False}}},
}, {"if": {"properties": {"intent": {"const": "diagnostic_flagging"}}},
    "then": {"properties": {"interpretation": {"enum": ["numerical_atypicality", "qc_review"]}}}},
    {"if": {"properties": {"intent": {"const": "exclude"}}},
     "then": {"properties": {"exclusions": {"const": True}}}}]


def action_scope_schema():
    return json.loads(canonical_json(_ACTION_SCOPE))


def validate_action_scope(value):
    copied = json.loads(canonical_json(value))
    _validate(_ACTION_SCOPE, copied, "method action scope")
    return copied


_DIAGNOSTIC_POLICY = _object({
    "format": {"const": "devbench.diagnostic-policy/1"},
    "interpretation": {"enum": ["numerical_atypicality", "qc_review"]},
    "measurement_semantics": {"type": "object", "maxProperties": 128,
                              "additionalProperties": _TEXT},
})


def diagnostic_profile_schema(*, implemented=False):
    """Scientific descriptions stay open to families unavailable to the executor."""
    return _object({
        "family": {**({"enum": ["absolute_mad", "empirical_two_tail"]} if implemented else dict(_TEXT)),
                   "description": "Mathematical identifiers, not a preferred-method or availability list: "
                   "absolute_mad is abs(x - median(reference)) / median(abs(reference - median(reference))), "
                   "using unscaled median absolute deviation (no 1.4826 factor); zero scale leaves the score unavailable. "
                   "empirical_two_tail is 1 - 2*min(midrank, 1-midrank), where midrank = "
                   "(number of reference values < x + number <= x)/(2*n), with ties half weighted and x included "
                   "in its declared reference group. Other scientific families remain expressible before binding."},
        "measurements": {"type": "array", "minItems": 1, "maxItems": 128, "uniqueItems": True, "items": _TEXT},
        "group_by": {"type": "array", "maxItems": 2, "uniqueItems": True, "items": _TEXT},
        "aggregate": {**({"const": "max"} if implemented else dict(_TEXT)),
                      "description": "max is the maximum across the declared measurement scores; "
                      "the aggregate is null when any measurement score is unavailable. "
                      "Other scientific aggregations remain expressible before binding."},
        "flag_threshold": {"type": ["number", "null"], **({"minimum": 0} if implemented else {})},
    })


_DIAGNOSTIC_POLICY_V2 = _object({
    **_DIAGNOSTIC_POLICY["properties"], "format": {"const": "devbench.diagnostic-policy/2"},
    "profile": diagnostic_profile_schema(implemented=True),
})
_DIAGNOSTIC_POLICIES = {"oneOf": [_DIAGNOSTIC_POLICY, _DIAGNOSTIC_POLICY_V2]}


def validate_diagnostic_policy(value):
    copied = json.loads(canonical_json(value))
    _validate(_DIAGNOSTIC_POLICIES, copied, "diagnostic policy")
    return copied


_PRECONDITION = _object({
    "fact": {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$"},
    "operator": {"enum": ["equals", "at_least"]},
    "value": {"type": ["string", "number", "boolean"], "maxLength": 4096},
    "rationale": _TEXT,
})
_PRECONDITION["allOf"] = [{
    "if": {"properties": {"operator": {"const": "at_least"}}},
    "then": {"properties": {"value": {"type": "number"}}},
}]
_SCIENTIFIC_SCHEMA = _object({
    "tasks": {"type": "array", "minItems": 1, "maxItems": 16, "uniqueItems": True, "items": _TEXT},
    "assumptions": {"type": "array", "maxItems": 32, "items": _TEXT},
    "preconditions": {"type": "array", "maxItems": 32, "items": _PRECONDITION},
    "output_semantics": _TEXT,
    "implementation_origin": {"enum": ["official", "generated", "adapted"]},
    "references": {"type": "array", "maxItems": 32, "items": _object({
        "url": {"type": "string", "maxLength": 4096, "pattern": r"^https?://[^\s]+$"},
        "kind": {"enum": ["primary_paper", "official_docs", "official_repository"]},
        "version": _TEXT,
    })},
})


def scientific_metadata_schema():
    """Return a detached schema for optional scientific method descriptions."""
    return json.loads(canonical_json(_SCIENTIFIC_SCHEMA))


_INPUT = _object({"artifact_type": _ARTIFACT, "required": {"type": "boolean"}, "parameter": _NAME},
                 ["artifact_type", "required"])
_OUTPUT = _object({"artifact_type": _ARTIFACT, "path": _TEXT, "required": {"type": "boolean"}})
_SCHEMA = _object({
    "format": {"const": METHOD_FORMAT},
    "method_id": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
    "name": {**_TEXT, "maxLength": 200},
    "lifecycle": {"enum": ["custom", "registered"]},
    "implementation_trust": {"enum": ["unreviewed", "reviewed"]},
    "inputs": {"type": "object", "maxProperties": 16, "propertyNames": _NAME, "additionalProperties": _INPUT},
    "parameters": {"type": "object"},
    "outputs": {"type": "object", "minProperties": 1, "maxProperties": 16,
                "propertyNames": _NAME, "additionalProperties": _OUTPUT},
    "implementation": {"oneOf": [
        _object({"kind": {"const": "oci"}, "source_sha256": _HASH,
                 "entrypoint": {"type": "array", "minItems": 1, "maxItems": 64, "items": _TEXT}}),
        _object({"kind": {"const": "dispatcher"}, "source_sha256": _HASH, "tool_name": _NAME}),
    ]},
    "environment": {"type": "object"},
    "execution": _object({
        "cpu": {"type": "number", "exclusiveMinimum": 0, "maximum": 16},
        "memory_mb": {"type": "integer", "minimum": 1, "maximum": 65536},
        "timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 3600},
        "pids": {"type": "integer", "minimum": 1, "maximum": 1024},
        "max_output_bytes": {"type": "integer", "minimum": 1, "maximum": 100_000_000},
        "network": {"const": "deny"},
    }),
    "validation": _object({
        "comparator": {"enum": ["single_cell_qc", "observation_selection", "observation_diagnostic", "json", "file"]},
        "invariants": {"type": "array", "uniqueItems": True, "maxItems": len(_ALL_INVARIANTS),
                       "items": {"enum": list(_ALL_INVARIANTS)}},
        "counts_layer": _TEXT,
        "sample_key": _TEXT,
        "qc_metrics": {"type": "array", "maxItems": 128, "uniqueItems": True, "items": _TEXT},
        "diagnostic_policy": _DIAGNOSTIC_POLICIES,
    }, ["comparator", "invariants", "counts_layer", "qc_metrics"]),
    "provenance": _object({
        "origin": _TEXT,
        "source_conversation_id": _TEXT,
        "source_message_id": _TEXT,
        "source_plan_revision": _TEXT,
        "citations": {"type": "array", "maxItems": 100, "items": _TEXT},
    }, ["origin"]),
})
# Optional metadata must not insert defaults or alter existing version identities.
_SCHEMA["properties"]["scientific"] = _SCIENTIFIC_SCHEMA
_SCHEMA["properties"]["action_scope"] = _ACTION_SCOPE
_ENVIRONMENTS = {
    "oci": _object({
        "image": {"type": "string", "maxLength": 512,
                  "pattern": r"^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$"},
        "lock_sha256": _HASH,
    }),
    "dispatcher": _object({"runtime": {"const": "trusted_dispatcher"}, "identity_sha256": _HASH}),
}


def _validate(schema, value, label):
    try:
        # An explicit empty registry prevents jsonschema's implicit remote lookup.
        Draft202012Validator(schema, registry=Registry()).validate(value)
    except (ValidationError, SchemaError) as error:
        raise ValueError(f"Invalid {label}: {error.message}") from error
    except RecursionError as error:
        raise ValueError(f"Invalid {label}: recursive schema or data") from error


def validate_scientific_metadata(value):
    """Validate finite scientific metadata without granting implementation trust."""
    try:
        copied = json.loads(canonical_json(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid scientific metadata JSON: {error}") from error
    _validate(_SCIENTIFIC_SCHEMA, copied, "scientific metadata")
    seen = set()
    for precondition in copied["preconditions"]:
        identity = (precondition["fact"], precondition["operator"])
        if identity in seen:
            raise ValueError("Duplicate scientific precondition fact/operator")
        seen.add(identity)
    for reference in copied["references"]:
        try:
            url = urlsplit(reference["url"])
            valid = url.scheme in {"http", "https"} and bool(url.hostname)
            url.port  # Validate a declared port, if any.
        except ValueError as error:
            raise ValueError("Scientific references require an HTTP(S) URL with a host") from error
        if not valid:
            raise ValueError("Scientific references require an HTTP(S) URL with a host")
    return copied


def _check_parameter_schema(schema):
    if (schema.get("type") != "object" or schema.get("additionalProperties") is not False
            or "patternProperties" in schema):
        raise ValueError("Parameters must declare a closed object schema")
    if schema.get("$schema", _DRAFT) != _DRAFT:
        raise ValueError("Parameters must use JSON Schema Draft 2020-12")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise ValueError(f"Invalid parameter schema: {error.message}") from error

    def visit(node):
        if isinstance(node, dict):
            if "$id" in node:
                raise ValueError("Parameter schemas may not change reference resolution with $id")
            for keyword in ("$ref", "$dynamicRef"):
                if keyword in node:
                    reference = node[keyword]
                    if not isinstance(reference, str) or not reference.startswith("#/"):
                        raise ValueError("Only local JSON pointer schema references are permitted")
                    target = schema
                    try:
                        for key in reference[2:].split("/"):
                            target = target[key.replace("~1", "/").replace("~0", "~")]
                    except (KeyError, TypeError, IndexError) as error:
                        raise ValueError("Unresolved local parameter schema reference") from error
                    if not isinstance(target, (dict, bool)):
                        raise ValueError("Parameter schema reference must identify a schema")
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)


def _validate_contract(value):
    _validate(_SCHEMA, value, "method contract")
    if "scientific" in value:
        validate_scientific_metadata(value["scientific"])
    kind = value["implementation"]["kind"]
    _validate(_ENVIRONMENTS[kind], value["environment"], "method environment")
    for key in ("memory_mb", "pids", "max_output_bytes"):
        if type(value["execution"][key]) is not int:
            raise ValueError(f"Execution {key} must be a JSON integer")
    if value["lifecycle"] == "custom" and value["implementation_trust"] != "unreviewed":
        raise ValueError("Custom methods must remain unreviewed")
    if kind == "dispatcher" and (value["lifecycle"] != "registered" or value["implementation_trust"] != "reviewed"):
        raise ValueError("Dispatcher contracts are reserved for registered methods")
    paths = []
    for output in value["outputs"].values():
        path = PurePosixPath(output["path"])
        if (not re.fullmatch(r"/outputs/[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", output["path"])
                or str(path) != output["path"]):
            raise ValueError("Output paths must be canonical /outputs/<filename> paths")
        paths.append(output["path"])
    if len(set(paths)) != len(paths):
        raise ValueError("Output paths must be distinct")
    _check_parameter_schema(value["parameters"])
    bound = [spec["parameter"] for spec in value["inputs"].values() if "parameter" in spec]
    if len(set(bound)) != len(bound):
        raise ValueError("Input parameter bindings must be distinct")
    if set(bound) & set(value["parameters"].get("properties", {})):
        raise ValueError("Bound input paths may not also be method parameters")
    if kind == "oci" and bound:
        raise ValueError("Only dispatcher inputs declare parameter bindings")
    if kind == "dispatcher" and len(bound) != len(value["inputs"]):
        raise ValueError("Every dispatcher input requires a parameter binding")
    policy = value["validation"]
    scope = value.get("action_scope")
    if scope and scope["intent"] in {"retain", "diagnostic_flagging"} and set(value["outputs"]) != {"dataset"}:
        raise ValueError("Non-exclusion retention/diagnostic actions require exactly the validated dataset output port")
    if scope and (scope["intent"] in {"retain", "exclude", "diagnostic_flagging"}
                  or scope["exclusions"] or "dataset" in value["outputs"]):
        if policy["comparator"] not in {"observation_selection", "single_cell_qc", "observation_diagnostic"}:
            raise ValueError("Action scope requires an actual observation-effect validator")
        if scope["intent"] == "diagnostic_flagging" and policy["comparator"] != "observation_diagnostic":
            raise ValueError("Diagnostic action scope requires diagnostic output validation")
    if "diagnostic_policy" in policy and policy["comparator"] != "observation_diagnostic":
        raise ValueError("Diagnostic policy requires the diagnostic comparator")
    if policy["comparator"] in {"observation_selection", "observation_diagnostic"}:
        diagnostic = policy["comparator"] == "observation_diagnostic"
        allowed = DIAGNOSTIC_INVARIANTS if diagnostic else OBSERVATION_SELECTION_INVARIANTS
        if any(name not in allowed for name in policy["invariants"]):
            raise ValueError("Observation selection requires observation-row invariants")
        if (not policy["qc_metrics"] or set(policy["qc_metrics"]) & {"cell_id", "sample", policy.get("sample_key")}
                or policy.get("sample_key") == "cell_id"):
            raise ValueError("Observation selection requires numeric QC measurement columns")
        for section in ("inputs", "outputs"):
            dataset = value[section].get("dataset", {})
            if dataset.get("artifact_type") != "json" or dataset.get("required") is not True:
                raise ValueError("Observation selection requires named, required JSON dataset inputs and outputs")
        if diagnostic:
            scope = value.get("action_scope", {})
            declared = policy.get("diagnostic_policy", {})
            if (scope.get("intent") != "diagnostic_flagging" or scope.get("exclusions") is not False
                    or declared.get("interpretation") != scope.get("interpretation")):
                raise ValueError("Diagnostic output requires matching explicit non-exclusion action and policy")
            if declared["interpretation"] == "qc_review" and (not set(policy["qc_metrics"]) <= declared["measurement_semantics"].keys()
                    or any(not declared["measurement_semantics"].get(name, "").strip() for name in policy["qc_metrics"])):
                raise ValueError("QC review requires declared measurement semantics")
            if declared["format"] == "devbench.diagnostic-policy/2":
                profile = declared["profile"]
                if profile["measurements"] != policy["qc_metrics"]:
                    raise ValueError("Diagnostic profile measurements must match the frozen validation measurements")
                if not set(profile["group_by"]) <= {"sample", "group", policy.get("sample_key")}:
                    raise ValueError("Diagnostic profile reference groups require declared metadata roles")
        elif value.get("action_scope", {}).get("intent") == "diagnostic_flagging":
            raise ValueError("Diagnostic flagging cannot use a retention-only output")
    elif policy["invariants"] and policy["comparator"] != "single_cell_qc":
        raise ValueError("Single-cell invariants require the single_cell_qc comparator")
    if policy["comparator"] == "single_cell_qc":
        if any(name not in INVARIANTS for name in policy["invariants"]):
            raise ValueError("Single-cell comparison requires single-cell invariants")
        for section in ("inputs", "outputs"):
            dataset = value[section].get("dataset", {})
            if dataset.get("artifact_type") != "anndata" or dataset.get("required") is not True:
                raise ValueError("Single-cell comparison requires named, required anndata dataset inputs and outputs")


@dataclass(frozen=True, init=False)
class MethodContract:
    """An immutable canonical JSON value; trust is checked by its executor."""

    _encoded: bytes
    _version_id: str

    @classmethod
    def from_dict(cls, value):
        try:
            encoded = canonical_json(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid method JSON: {error}") from error
        copied = json.loads(encoded)
        _validate_contract(copied)
        instance = object.__new__(cls)
        object.__setattr__(instance, "_encoded", encoded)
        object.__setattr__(instance, "_version_id", _digest(copied))
        return instance

    @property
    def version_id(self):
        return self._version_id

    def to_dict(self):
        return json.loads(self._encoded)

    def validate_parameters(self, parameters):
        """Return a finite JSON copy; schema defaults are never inserted."""
        try:
            copied = json.loads(canonical_json(parameters))
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid parameter JSON: {error}") from error
        _validate(self.to_dict()["parameters"], copied, "method parameters")
        return copied
