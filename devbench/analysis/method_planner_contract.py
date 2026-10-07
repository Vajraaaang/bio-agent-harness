"""Bounded contracts for scientific discovery, resolution and recommendations.

This implements the core ScientificMethodPlan shape from the supplied design,
with coordinator-owned experiment/result extensions. It does not search sources,
run methods, authenticate approvals or establish scientific applicability. Those
checks belong to the planner control plane; a structurally valid plan is not an
execution authorization. Capability exclusion here is a recursive field check,
not a claim to detect capability information concealed in arbitrary prose.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry

from devharness_core.provenance import canonical_json
from devharness_core.methods import action_scope_schema, diagnostic_profile_schema


SCIENTIFIC_PLANNER_INSTRUCTIONS = """You are the scientific method planner.
Work from the supplied data and its uncertainty to the scientific question,
then alternatives, assumptions, tradeoffs and missing information. Observed
outcomes precede conclusions; a proposed trial has no observed outcome yet.
For bulk_rna_input, inspect the separate gene/sample axes, declared representation,
metadata catalog, supplied role cross-tabs, provenance and independence unknowns.
Unique sample labels never establish biological replication. Missing declarations
remain unknown; they do not automatically require scientific refusal or block
bounded descriptive computations. Validate design rank and contrasts before
inferential fitting. Raw counts use RLE library-size offsets and empirical-Bayes
negative-binomial dispersions; normalized/continuous expression uses Gaussian
splines without count conversion. Report fitted p-values and BH q-values, with
q at least p. A matrix is not an observation/QC table. Preserve the input's scope,
unknowns and any fitting-specific failure; do not invent assay or causal validity.
Generate a small serious candidate set without a preferred method or threshold
unless an explicit retained requirement establishes one. You have no registry or executable-tool inventory in
this phase. Never limit candidates to familiar or available implementations.
Use primary method papers, official documentation and author repositories; state
only evidence actually available and never claim a search was performed without
retrieval evidence. Separate observed facts, inferred facts and blocking unknowns.
Record machine-checkable hard preconditions and preserve unknown or violated
states. Each assumption.field is a dot path relative to dataset_summary.observed:
for example use metrics_finite for an observed metrics_finite key, without adding
dataset or observed prefixes. The coordinator recomputes status from that exact
path, operator and expected value; your stated status cannot establish evidence.
Use contains for one member of a list, and equals for a whole value. Method
parameters, algorithm choices and required future outputs are not observations
about the input dataset; describe them in parameter_candidates, expected_inputs
or known_failure_modes instead of asserting them as satisfied data preconditions.
Keep genuinely necessary but unobserved data preconditions explicitly unknown.
For new candidates declare optional action_scope with format
devbench.method-action/1 and intent retain, diagnostic_flagging, exclude or infer.
For a numerical profile, freeze optional diagnostic_profile before implementation
availability: family, ordered measurements, reference group_by metadata fields,
aggregate and flag_threshold (null when categorical decisions are unresolved).
Choose the scientifically appropriate family/reference, not an implemented one.
Use canonical mathematical serialization when the chosen calculation matches it:
absolute_mad means abs(x - median(reference)) / median(abs(reference -
median(reference))), with unscaled median absolute deviation, no 1.4826 factor,
and unavailable scores at zero scale. empirical_two_tail means
1 - 2*min(midrank, 1-midrank), where midrank=(number < x + number <= x)/(2*n)
within the declared reference group, half weighting ties and including x itself.
max means the maximum of declared measurement scores, with null aggregate if any
score is unavailable. These identifiers describe mathematics, not preference,
package equivalence or implementation availability. Other scientifically suitable
families and aggregates remain expressible; never translate prose into a different
calculation or substitute an available family. Freeze ordered measurements and
reference group_by explicitly; an empty group_by uses the pooled input reference.
The supplied observation_fact_catalog names the exact paths for bounded input
facts. metrics_finite covers numeric typed finite measurements; do not invent
measurements_numeric_typed or other aliases. prerequisite_inspection retains
coordinator evidence or failure for this selection context, without changing the
historical caller summary. Input parse/hash/type/identity checks do not establish
scientific validity, assay semantics, units, design rank, covariance assumptions
or nonzero profile scale. Preserve these as separate unknowns when necessary.
Describe each calculation and reference in your assumptions and parameter
rationales; an unknown cutoff does not make computable numerical profiles absent.
Profile and threshold choices remain assistant-attributed unless an exact user
parameter requirement supports user attribution. Unsupported implementations
stay unavailable; capability resolution cannot substitute another profile.
Generic numerical_atypicality flags require finite typed measurements and input
integrity, without assay semantics or technical-damage validation. They describe
unusual measurements only. qc_review interpretation additionally requires known
measurement semantics. Retain and diagnostic_flagging explicitly bind exclusions
to false; an exclusion needs its own evidence and authorization, and inference
needs independently supported units and an identifiable design. Follow the
coordinator's named minimum prerequisite policy; assumptions may add caution,
never waive a mandatory prerequisite. Computable unknowns may request bounded
coordinator inspection; your asserted status or an action label is not evidence.
Assess the biological experimental unit, raw versus transformed inputs,
replication, confounding and downstream representation requirements.
Keep algorithm, representation, parameters and seed distinct. Parameter origins
must reflect their actual source: user, assistant, fixture, registry_default,
literature_default, inferred or trial_selected. Use user only for an exact name
and value in user_parameter_requirements with its retained evidence quote or
reference. These are caller-declared requirements, not authenticated proof of
human authorship. Task prose, prior mentions, fixture values and registry defaults
do not establish user requirements. Preserve assistant and fixture choices as
such; do not invent constraints from an earlier example. In execution_verification
mode assess a procedure's execution, not its scientific superiority.
Rank scientifically without availability or
implementation maturity as a signal. Do not force a winner or invent candidates
when no scientifically plausible method is known; an empty candidate set is valid.
Return only the supplied scientific candidate schema, with concise evidence and
decision statements, never hidden chain-of-thought. Stop after this phase; do not
execute commands, change canonical analysis, request full execution or promote.
"""

CAPABILITY_PLANNER_INSTRUCTIONS = """Resolve the frozen scientific candidates
against the supplied execution registry. Return bindings in the original order;
do not rewrite, remove, rerank or substitute scientific candidates. Availability
is an execution fact, not scientific preference. Bind only an exact supplied
method version whose implementation corresponds to the scientific candidate.
For missing implementations, preserve a null binding and report the gap. When
evidence exists, identify an authoritative HTTPS source, version/commit, license,
environment and concise correspondence evidence. Preserve missing pins as null;
never invent a version, claim inspection or claim installation without evidence.
Return only the supplied resolution schema. No shell execution, installation,
registry mutation, approval, canonical-analysis change or promotion is authorized
by this phase. Record inspectable facts rather than hidden chain-of-thought.
"""

RECOMMENDATION_INSTRUCTIONS = """Recommend only from the frozen candidates,
applicability assessments and recorded trial evidence. Organize the conclusion
around the supplied data, scientific question, alternatives/assumptions/tradeoffs,
observed outcomes and remaining uncertainty. Distinguish retained user
requirements from assistant or fixture choices. Execution evidence does not by
itself establish a scientific selection. In execution_verification mode choice
must be null: report what executed and what remains scientifically untested.
For method_selection, use recommendation_evidence_assessment. A non-null choice
requires comparable predeclared successful trials for the eligible implemented
alternatives, with shared scientifically directed numeric diagnostics computed
by coordinator validation. Runtime, memory, retention totals, structural pass
flags and a method's own output report cannot supply that evidence alone. The
assessment is a necessary comparative-pilot floor, not independent scientific
validation or proof of overall superiority. Preserve unavailable, failed or
uncompared alternatives as limitations. Useful reversible diagnostic flags may
support a next inspection step alongside a null choice. Describe those actual
flags and uncertainty separately from comparison: flag counts, descriptive
input shifts and group flag rates cannot meet the winner evidence floor, and
stability is unavailable without matched perturbation outputs. Never infer
technical damage, causal biology or exclusion authorization from flagging.
Version 2 numerical profile evidence distinguishes score coverage and arithmetic
fidelity from deferred categorical decisions. Useful computed profiles may
accompany a null scientific recommendation; availability and correct arithmetic
alone still cannot meet the comparative winner evidence floor.
Compare predeclared
diagnostics that preserve the biological trial unit: donors/replicates, complete
raw-droplet context, batches, rare populations or topology as applicable. Invalid
or failed trials are failures, never successful estimates. Do not select methods
by final hypothesis p-values, q-values or the number of significant genes.
Keep scientific confidence separate from implementation confidence. Generated or
materially adapted code has low implementation confidence until independently
validated; smoke-test success alone is not validation. Distinguish method family,
representation, parameters and seed, and acknowledge remaining tradeoffs.
Do not force a winner. If evidence is insufficient or conflicting, set choice to
null and confidence/scientific_confidence to no_recommendation, with the blocking
reason and evidence needed next. Return the supplied recommendation schema only,
using concise observations and rationale rather than hidden chain-of-thought.
Trial completion is not human approval. Approval actions may be pending or
not_requested only; never assert approved/rejected or infer consent from silence.
Recommendation, full execution, analysis-plan change, saving an experimental
recipe and trusted-registry promotion require their separate approval scopes.
"""


REASSESSMENT_INSTRUCTIONS = SCIENTIFIC_PLANNER_INSTRUCTIONS + """
Reassess the scientific candidates using the bounded verified investigation
evidence. Preserve unresolved disagreement, unavailable assessments and absent
children. Proposals do not establish data facts or satisfy hard prerequisites.
Explain alternatives before any method catalog; do not vote or average child
confidence. This step may revise an unbound pre-fit candidate set only. It cannot
change an existing trial, hypothesis family, fitted statistics or approval.
"""

PRELIMINARY_EXPLANATION_INSTRUCTIONS = """Develop a preliminary explanation of
the actual verified results from the supplied data, question and assumptions.
Distinguish observations, supplied declarations, inferences and unknowns. Return
competing evidence-linked hypotheses and a meaningful observational criterion
that could distinguish them; agreement is not independent scientific validation.
An ambiguity nomination grants no additional calls. Do not invent a competing
explanation when none is supported. Critical missing facts remain unknown rather
than eligible ambiguity. New biological hypotheses are exploratory; do not add
genes, contrasts, covariates or tuning attempts to a confirmatory family, change
the numerical results, refit, select a winner or assert approval. Repeated units,
rank failure and confounding retain their diagnostic/inferential limits. Do not
infer causal regulation or promoter occupancy from network/motif associations.
Report absent plots, pixels and expert assessment explicitly. Artifact references
do not mean pixels were supplied. Return only the bounded preliminary explanation
schema and observable findings, never private reasoning or a final discovery.
"""


_DRAFT = "https://json-schema.org/draft/2020-12/schema"
_TEXT = {"type": "string", "minLength": 1, "maxLength": 4096, "pattern": r"\S"}
_NAME = {**_TEXT, "maxLength": 200}
_NULL_TEXT = {"anyOf": [_TEXT, {"type": "null"}]}
_CONFIDENCE = {"enum": ["high", "medium", "low", "no_recommendation"]}
_IMPLEMENTATION_CONFIDENCE = {"enum": ["high", "medium", "low", "unknown"]}
_SOURCE_TYPES = ["primary_paper", "official_package", "author_repository", "generated"]
_DATA = {"type": "object", "maxProperties": 128}
_MAX_JSON_BYTES = 2_000_000


def _object(properties, required=None, *, extensions=False):
    return {"type": "object", "properties": properties, "additionalProperties": extensions,
            "maxProperties": 128,
            "required": list(properties) if required is None else required}


def _array(items, maximum=32, minimum=0):
    return {"type": "array", "items": items, "maxItems": maximum, "minItems": minimum}


_STRINGS = _array(_TEXT)
_TASK = _object({
    "scientific_objective": _TEXT, "computational_task": _TEXT,
    "downstream_use": _NULL_TEXT, "constraints": _STRINGS,
}, ["scientific_objective", "computational_task"])
_DATASET = _object({
    "observed": _DATA, "inferred": _DATA,
    "unknowns": _array(_object({"field": _TEXT, "impact": _TEXT,
                                "blocking": {"type": "boolean"}}, ["field", "impact"])),
    "artifact_ids": _STRINGS,
}, ["observed", "inferred", "unknowns"])
_SOURCE = _object({
    "type": {"enum": _SOURCE_TYPES}, "reference": _TEXT,
    "version": _NULL_TEXT, "commit": _NULL_TEXT,
}, ["type", "reference"])
_ASSUMPTION = _object({
    "statement": _TEXT,
    "status": {"enum": ["satisfied", "violated", "unknown", "not_applicable"]},
    "evidence": _NULL_TEXT, "hard_precondition": {"type": "boolean"},
    "field": {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$"},
    "operator": {"enum": ["equals", "at_least", "contains", "present"]}, "expected": {},
})
_PARAMETER = _object({
    "name": _NAME, "value": {},
    "origin": {"enum": ["user", "assistant", "fixture", "registry_default", "literature_default", "inferred", "trial_selected"]},
    "rationale": _TEXT,
})
_CANDIDATE = _object({
    "name": _NAME, "method_family": _TEXT, "source": _SOURCE,
    "assumptions": _array(_ASSUMPTION), "expected_inputs": _STRINGS,
    "dataset_fit": {"enum": ["strong", "plausible", "weak", "inapplicable", "unknown"]},
    "objective_fit": {"enum": ["strong", "plausible", "weak", "inapplicable"]},
    "known_failure_modes": _STRINGS, "parameter_candidates": _array(_PARAMETER, 64),
})
_CANDIDATE["properties"]["action_scope"] = action_scope_schema()
_CANDIDATE["properties"]["diagnostic_profile"] = diagnostic_profile_schema()
SCIENTIFIC_SCHEMA = {"$schema": _DRAFT, "title": "ScientificCandidates",
                     **_object({"candidates": _array(_CANDIDATE)})}
REASSESSMENT_SCHEMA = SCIENTIFIC_SCHEMA

_EVIDENCE_REF = _object({
    "artifact_id": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
    "sha256": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
    "artifact_type": _NAME,
    "size_bytes": {"type": "integer", "minimum": 0, "maximum": 100_000_000},
})
PRELIMINARY_EXPLANATION_SCHEMA = {"$schema": _DRAFT, "title": "PreliminaryExplanation", **_object({
    "hypotheses": _array(_object({"id": _NAME, "statement": _TEXT,
        "evidence_refs": _array(_EVIDENCE_REF, 16, 1), "limitations": _array(_TEXT, 16)}), 4),
    "ambiguity_items": _array(_object({"id": _NAME,
        "axis": {"enum": ["composition_vs_intrinsic", "technical_vs_biological", "alternative_biological_context"]},
        "hypothesis_ids": {**_array(_NAME, 2, 2), "uniqueItems": True},
        "evidence_refs": _array(_EVIDENCE_REF, 16, 1),
        "missing_information_status": {"enum": ["sufficient", "unknown", "critical_unknown"]},
        "discriminating_criterion": _TEXT}), 2),
    "uncertainty": _array(_TEXT, 16), "absent_assessments": _array(_TEXT, 16),
})}

_DISCOVERY = _object({
    "source_type": {"enum": _SOURCE_TYPES},
    "reference": {**_TEXT, "pattern": r"^https://[^\s]+$"},
    "version": _NULL_TEXT, "commit": _NULL_TEXT, "license": _TEXT,
    "correspondence_evidence": _TEXT, "required_environment": _DATA,
})
_NULL_DISCOVERY = {"anyOf": [_DISCOVERY, {"type": "null"}]}
RESOLUTION_SCHEMA = {"$schema": _DRAFT, "title": "CandidateBindings", **_object({
    "bindings": _array(_object({"candidate": _NAME, "method_version_id": _NULL_TEXT,
                               "discovery": _NULL_DISCOVERY})),
})}

_APPROVAL = _object({
    "action": {"enum": ["approve_recommendation", "approve_full_execution", "approve_analysis_plan_change",
                        "save_experimental_recipe", "promote_to_registry"]},
    "required": {"type": "boolean"},
    "status": {"enum": ["not_requested", "pending", "approved", "rejected"]},
    "summary": _TEXT,
}, ["action", "required", "status"])
RECOMMENDATION_SCHEMA = {"$schema": _DRAFT, "title": "ScientificRecommendation", **_object({
    "choice": _NULL_TEXT, "confidence": _CONFIDENCE, "scientific_confidence": _CONFIDENCE,
    "implementation_confidence": _IMPLEMENTATION_CONFIDENCE,
    "confidence_basis": _TEXT, "rationale": _TEXT, "tradeoffs": _STRINGS, "alternatives": _STRINGS,
    "params": _DATA, "required_environment": _DATA, "tests_run": _STRINGS,
    "diffs": _array(_DATA), "approval_actions": _array(_APPROVAL, 5),
}, ["choice", "confidence", "scientific_confidence", "implementation_confidence",
    "rationale", "params", "approval_actions"])}

_METRIC = _object({
    "name": _NAME,
    "category": {"enum": ["accuracy_proxy", "signal_preservation", "artifact_removal", "stability",
                          "calibration", "runtime", "memory", "failure_mode", "other"]},
    "direction": {"enum": ["higher_better", "lower_better", "target_range", "descriptive"]},
    "interpretation": _TEXT,
})
_METRICS = _array(_METRIC, 64, 1)
_PLAN_CANDIDATE = _object({**_CANDIDATE["properties"],
    "availability": {"enum": ["registered", "workspace_custom", "externally_available",
                              "implementation_required", "unknown"]},
    "method_version_id": _NULL_TEXT, "implementation_confidence": _IMPLEMENTATION_CONFIDENCE,
    "applicability": {"enum": ["eligible", "inapplicable", "blocked_unknown"]},
    "blockers": _STRINGS, "discovery": _NULL_DISCOVERY,
})
_PLAN_CANDIDATE["required"].remove("action_scope")
_PLAN_CANDIDATE["required"].remove("diagnostic_profile")
_PLAN_CANDIDATE["properties"]["prerequisite_inspections"] = _array(_TEXT, 16)
_EXPERIMENT = _object({
    "id": _NAME, "candidates": _array(_NAME, minimum=1), "purpose": _TEXT,
    "subset_spec": _DATA,
    "subset_validity": _object({"preserves_preconditions": {"type": "boolean"}, "rationale": _TEXT}),
    "commands": _array(_TEXT, 64), "metrics": _METRICS, "resource_limits": _DATA,
}, ["id", "candidates", "subset_spec", "subset_validity", "commands", "metrics", "resource_limits"],
    extensions=True)
_RESULT = _object({
    "experiment_id": _NAME, "candidate": _NAME,
    "status": {"enum": ["success", "failed", "partial", "invalid_trial", "resource_limit"]},
    "metric_values": _DATA, "warnings": _STRINGS, "failures": _STRINGS,
    "artifacts": _array(_object({"artifact_id": _TEXT, "role": _TEXT, "immutable": {"type": "boolean"}},
                                ["artifact_id", "role"]), 64),
    "provenance": _DATA,
}, ["experiment_id", "candidate", "status", "metric_values", "artifacts", "provenance"], extensions=True)
PLAN_SCHEMA = {"$schema": _DRAFT, "title": "ScientificMethodPlan", **_object({
    "task": _TASK, "dataset_summary": _DATASET, "candidates": _array(_PLAN_CANDIDATE),
    "experiments": _array(_EXPERIMENT, 64), "results": _array(_RESULT, 128),
    "recommendation": RECOMMENDATION_SCHEMA,
})}
_CONTEXT_SCHEMA = _object({"task": _TASK, "dataset_summary": _DATASET,
                           "prior_state": {"anyOf": [_DATA, {"type": "null"}]}})
PLANNING_METADATA_SCHEMA = _object({
    "evaluation_purpose": {"enum": ["method_selection", "execution_verification"]},
    "user_parameter_requirements": _array(_object({"name": _NAME, "value": {}, "evidence": _TEXT}), 64),
})


def _copy_json(value):
    """Preflight container sizes/depth before strict finite serialization."""
    nodes, byte_count = 0, 0

    def visit(item, depth=0):
        nonlocal nodes, byte_count
        nodes += 1
        if depth > 24 or nodes > 50_000:
            raise ValueError("Planner JSON exceeds the nesting or value bound")
        if type(item) is dict:
            if len(item) > 128:
                raise ValueError("Planner JSON object exceeds its property bound")
            byte_count += 2 + max(0, len(item) - 1)
            for key, child in item.items():
                if type(key) is not str or not key or len(key) > 200:
                    raise ValueError("Planner JSON requires bounded string keys")
                byte_count += len(json.dumps(key, ensure_ascii=False).encode("utf-8")) + 1
                visit(child, depth + 1)
        elif type(item) is list:
            if len(item) > 256:
                raise ValueError("Planner JSON array exceeds its item bound")
            byte_count += 2 + max(0, len(item) - 1)
            for child in item:
                visit(child, depth + 1)
        elif item is None or type(item) in (str, int, float, bool):
            if type(item) is str and (len(item) > 16_384 or "\x00" in item):
                raise ValueError("Planner JSON string exceeds its bound or contains NUL")
            byte_count += len(json.dumps(item, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        else:
            raise ValueError(f"Unsupported JSON value: {type(item).__name__}")
        if byte_count > _MAX_JSON_BYTES:
            raise ValueError("Planner JSON exceeds the 2 MB bound")

    try:
        visit(value)
        encoded = canonical_json(value)
        if len(encoded) > _MAX_JSON_BYTES:
            raise ValueError("Planner JSON exceeds the 2 MB bound")
        return json.loads(encoded)
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ValueError(f"Invalid planner JSON: {error}") from error


def _validate(schema, value, label):
    copied = _copy_json(value)
    try:
        # No schema lookup may trigger remote retrieval.
        Draft202012Validator(schema, registry=Registry()).validate(copied)
    except ValidationError as error:
        raise ValueError(f"Invalid {label}: {error.message}") from error
    return copied


def _normal_name(value):
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _reject_capabilities(value):
    if isinstance(value, dict):
        for key, child in value.items():
            name = _normal_name(key)
            if (any(part in name for part in ("registry", "capability", "capabilities", "availability"))
                    or name in {"allowedtools", "tools", "inventory", "methodinventory",
                                "executablemethods", "availablemethods", "registeredmethods"}):
                raise ValueError(f"Scientific context cannot contain capability or registry inventory field: {key}")
            _reject_capabilities(child)
    elif isinstance(value, list):
        for child in value:
            _reject_capabilities(child)


def scientific_context(task: dict, dataset_summary: dict, prior_state=None) -> dict:
    """Build the first-pass context without capability/availability fields."""
    copied = _copy_json({"task": task, "dataset_summary": dataset_summary, "prior_state": prior_state})
    _reject_capabilities(copied)
    return _validate(_CONTEXT_SCHEMA, copied, "scientific context")


def validate_planning_metadata(evaluation_purpose="method_selection", user_parameter_requirements=None):
    """Caller-declared attribution metadata, kept outside the six-field export."""
    value = _validate(PLANNING_METADATA_SCHEMA, {
        "evaluation_purpose": evaluation_purpose,
        "user_parameter_requirements": [] if user_parameter_requirements is None else user_parameter_requirements,
    }, "planning metadata")
    _unique(value["user_parameter_requirements"], "name", "user parameter requirement")
    return value


def validate_parameter_origins(candidates, user_parameter_requirements):
    """Check new user claims without rewriting model responses or legacy plans."""
    requirements = validate_planning_metadata(user_parameter_requirements=user_parameter_requirements)["user_parameter_requirements"]
    by_name = {item["name"]: item for item in requirements}
    for candidate in candidates:
        for parameter in candidate["parameter_candidates"]:
            if parameter["origin"] == "user":
                requirement = by_name.get(parameter["name"])
                if requirement is None or canonical_json(requirement["value"]) != canonical_json(parameter["value"]):
                    raise ValueError("A user parameter origin requires an exact retained requirement with source evidence")


def _unique(values, field, label):
    seen = set()
    for value in values:
        identity = value[field]
        if identity in seen:
            raise ValueError(f"Duplicate {label}: {identity}")
        seen.add(identity)


def validate_scientific_candidates(value) -> list[dict]:
    """Validate the discovery envelope and return a detached, ordered shortlist."""
    copied = _validate(SCIENTIFIC_SCHEMA, value, "scientific candidates")
    _reject_capabilities(copied)
    _unique(copied["candidates"], "name", "candidate name")
    return copied["candidates"]


def validate_preliminary_explanation(value) -> dict:
    """A bounded nomination, never an approval or numerical result change."""
    copied = _validate(PRELIMINARY_EXPLANATION_SCHEMA, value, "preliminary explanation")
    if len(canonical_json(copied)) > 24_000:
        raise ValueError("Preliminary explanation exceeds its 24000 byte bound")
    _unique(copied["hypotheses"], "id", "hypothesis")
    _unique(copied["ambiguity_items"], "id", "ambiguity")
    hypotheses = {item["id"] for item in copied["hypotheses"]}
    if any(not set(item["hypothesis_ids"]) <= hypotheses for item in copied["ambiguity_items"]):
        raise ValueError("Ambiguity requires two retained competing hypotheses")
    return copied


def _check_discovery(discovery):
    if discovery is None:
        return
    try:
        url = urlsplit(discovery["reference"])
        valid = url.scheme == "https" and bool(url.hostname) and url.username is None and url.password is None
        url.port
    except ValueError as error:
        raise ValueError("Discovery requires a valid HTTPS source URL") from error
    if not valid:
        raise ValueError("Discovery requires a valid HTTPS source URL without credentials")


def validate_resolution(value) -> dict:
    """Validate bindings without resolving or changing scientific candidates."""
    copied = _validate(RESOLUTION_SCHEMA, value, "candidate resolution")
    _unique(copied["bindings"], "candidate", "candidate binding")
    for binding in copied["bindings"]:
        _check_discovery(binding["discovery"])
    return copied


def _check_recommendation(value, *, model_output):
    abstaining = value["choice"] is None
    if any((value[field] == "no_recommendation") != abstaining
           for field in ("confidence", "scientific_confidence")):
        raise ValueError("Recommendation choice and no_recommendation confidences must agree")
    _unique(value["approval_actions"], "action", "approval action")
    if model_output and any(action["status"] in {"approved", "rejected"} for action in value["approval_actions"]):
        raise ValueError("Model recommendations cannot assert human approval or rejection")


def validate_recommendation(value) -> dict:
    """Validate model output; only the control plane can record approval status."""
    copied = _validate(RECOMMENDATION_SCHEMA, value, "recommendation")
    _check_recommendation(copied, model_output=True)
    return copied


def validate_metrics(metrics) -> list[dict]:
    """Reject explicit outcome-directed metric names, not calibration metrics.

    This exact-name guard does not establish diagnostic independence; the
    controller must still freeze diagnostics before trials and inspect their use.
    """
    copied = _validate(_METRICS, metrics, "selection metrics")
    prohibited = {"pvalue", "pvalues", "qvalue", "qvalues", "significantgenes",
                  "numberofsignificantgenes", "numsignificantgenes", "significantgenecount",
                  "countofsignificantgenes", "minpvalue", "minqvalue"}
    for metric in copied:
        if _normal_name(metric["name"]) in prohibited:
            raise ValueError("Final hypothesis outcomes cannot be method-selection metrics")
    _unique(copied, "name", "selection metric name")
    return copied


def validate_plan(value) -> dict:
    """Validate a harness-assembled plan; recorded approvals require controller verification."""
    copied = _validate(PLAN_SCHEMA, value, "scientific method plan")
    _unique(copied["candidates"], "name", "candidate name")
    _unique(copied["experiments"], "id", "experiment id")
    for candidate in copied["candidates"]:
        _check_discovery(candidate["discovery"])
    for experiment in copied["experiments"]:
        validate_metrics(experiment["metrics"])
    _check_recommendation(copied["recommendation"], model_output=False)
    return copied
