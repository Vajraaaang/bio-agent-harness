"""Proposal-only contracts for trial design and untrusted implementation drafts.

Validation never accepts a trial, registers a method, executes source, installs
packages or grants approval. The coordinator must resolve artifact and method
identities, establish applicability, preserve the immutable execution contract,
and explicitly review any generated implementation before broader use.
"""
from __future__ import annotations

from copy import deepcopy
import json

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry

from devharness_core.provenance import canonical_json
from .method_planner_contract import PLAN_SCHEMA, _copy_json, validate_metrics


TRIAL_DESIGN_INSTRUCTIONS = """Propose the smallest scientifically valid trials
for the supplied frozen candidates, objective, dataset and execution constraints.
Return only the supplied trial-design schema, with at most four experiments.
Start with the supplied data and scientific question; preserve alternatives,
assumptions, tradeoffs and missing information before specifying measurements.
Keep user requirements, assistant choices and fixture choices separately
attributed. Do not convert a proposal into an observed result or conclusion.
Use only candidate names, immutable input artifact identities and parameter
interfaces actually provided by the coordinator. Do not invent data inspections,
source evidence, software versions, applicability or completed runs.
Preserve method preconditions and the biological trial unit: complete raw-droplet
context, donors/replicates, batches, rare populations, spatial neighborhoods or
trajectory topology as applicable. Explain subset validity; preserve uncertainty
or return an empty experiment list when a valid discriminating pilot is unknown.
Predeclare diagnostics and runner-owned metric_sources before seeing outcomes.
Respect each candidate's explicit action_scope and the coordinator minimum
prerequisites. Diagnostic flagging preserves every immutable observation in its
original order, with exclusions false. Numerical atypicality needs finite typed
measurements; QC interpretation needs known measurement semantics. Neither
technical-damage labels nor causal assertions follow from numerical flags.
Computable rank/variance prerequisites need a retained bounded inspection rather
than an invented satisfied status. Descriptive flag rates, measurement shifts and
group summaries do not establish a comparative winner or perturbation stability.
Each inputs[candidate] maps exact MethodContract input port names to retained
artifact IDs or exact devbench.trial-input/1 objects from available_input_artifact_refs.
Copy the full registered reference exactly; never reconstruct, shorten or infer an
artifact ID from a filename, hash or label. The coordinator checks every field.
For legacy ID bindings use the complete supplied ID.
Artifact IDs: use the contract's port name, not a filename or an invented label.
For unchanged complete inputs, use subset_spec.kind exactly full_dataset.
Other subset kinds require a retained inspection_artifact_id binding both the
subset and original artifacts; a prose assertion is insufficient.
Use JSON-pointer selectors for validation/output_json metrics and only the
provided runtime fields. Each pointer must resolve to a finite scalar, not an
object/list (select a numeric element explicitly). Runtime and output_json
self-reports may describe execution, but cannot establish scientific superiority.
For method_selection propose a joint comparison of feasible alternatives with
coordinator-computed directed scientific diagnostics; otherwise preserve the
evidence gap. execution_verification trials are software checks, not method
selection. Never use executable expressions or select by final
hypothesis p-values, q-values or the number of significant genes. Preserve
calibration, biological signal and failure diagnostics as relevant to the task.
Distinguish algorithm, representation, parameters and seed. Keep inputs comparable
across candidates and remain within the supplied memory/time/run budgets.
Commands are documentary plans only. Do not execute, install, accept, bind,
approve, modify canonical analysis or promote. Return concise inspectable
decisions and evidence, never hidden chain-of-thought or fabricated results.
"""

IMPLEMENTATION_INSTRUCTIONS = """Draft source only for the explicitly justified
implementation gap in the supplied frozen scientific candidate. Prefer an
authoritative existing implementation; generate or adapt code only when the
coordinator has justified why a trustworthy usable implementation is insufficient.
Follow the coordinator-provided immutable MethodContract exactly: input/output
ports, parameter schema, entrypoint, fixed image and environment lock, permitted
dependencies and resource/network policy. You may not revise that contract.
For observation_diagnostic follow the frozen diagnostic_policy version exactly.
For devbench.diagnostic-policy/1 write devbench.observation-diagnostics/1,
binding input_sha256 to the immutable input, evidence_scope to its declared
policy, full ordered retained_ids, and one ordered record per observation with
observation_id, status flagged/not_flagged/unavailable, finite score or null,
reason and bounded evidence_refs to that observation's declared input metrics.
Admitted reasons are exact canonical strings: numerical_atypicality flagged
"Unusual under the supplied representation", not_flagged "Within reference";
qc_review flagged "Warrants QC review", not_flagged "No QC review flag under the
supplied reference". For unavailable under either interpretation use exactly
"Reference unavailable", "Measurement unavailable", "Score unavailable" or
"Insufficient evidence". Do not append an explanation or damage/causal assertion
to these scope-bound reasons. Additional candidate explanations remain untrusted
in the separately retained model/source draft, never this admitted reason field.
Unavailable scores are null with a reason; available scores cite actual input
measurements. No subset, reordered or transformed data are accepted. Flags
describe atypicality or warranted QC review; they do not prove technical damage
or authorize exclusion. Preserve actual failures, never fabricate successful
scores or stability. The coordinator alone declares diagnostic_policy semantics.
For devbench.diagnostic-policy/2 write devbench.observation-diagnostics/2 with
the same exact top-level keys and ordered coverage, but each record has exactly
observation_id, score, score_reason, flag, flag_reason, profiles and evidence_refs.
profiles maps EVERY frozen measurement to score, reason, reference_count and
reference_group (the exact group_by labels, or an empty object for pooled data).
absolute_mad is abs(value - median(reference)) / median(abs(reference - median));
this is unscaled MAD, with no Gaussian scaling constant. empirical_two_tail is
1 - 2*min(u,1-u), u=(number strictly below value + 0.5*number equal)/reference size.
References include the observation and exactly the frozen group_by population.
max aggregate is defined only if every declared measurement score is available.
Available numerical scores have null reasons. Undefined MAD scores use null and
exact reason "Zero MAD in reference"; genuinely nonfinite numerical calculations
use null and "Nonfinite score calculation".
For a strictly positive exact score that underflows to floating zero, use null
and "Score below floating representation" rather than fabricate an exact zero.
An actual zero deviation from the reference median remains available zero.
Undefined aggregate has null score and "Profile score unavailable" as score_reason.
Never suppress computable
profiles for unknown assay meaning, quality labels or flag cutoffs. A null frozen
threshold means null flag and "Flag threshold unresolved". A frozen threshold
with an undefined aggregate means null flag and "Profile score unavailable".
Otherwise flag is the Boolean aggregate >= threshold, with the scope's exact
flagged/not_flagged canonical reason. Cite each declared measurement on that
same observation exactly once. The coordinator independently recomputes all
profile values, reference populations, aggregate values and rule decisions;
merely reporting scores or claiming correct arithmetic cannot pass validation.
Version 2 profile/reference declarations must match the candidate frozen before
capability resolution, including unresolved thresholds. Unsupported families
remain implementation/verification unavailable, never silently replaced.
Scoped retain and diagnostic_flagging contracts have exactly one dataset output;
do not publish auxiliary filtered/transformed datasets through secondary ports.
The coordinator creates result plots separately. Public diagnostic_measurements
keeps scalar flag_rate/available_fraction and nests each metric summary in
by_measurement[metric]; metric names cannot replace aggregate fields. Declared
sample_key determines sample flag-rate grouping. Scoped AnnData retention must
preserve existing .X, counts/other layers, matrix slots, raw representation and
observation/variable annotations; reserialization itself is not a change.
Retain complete .uns metadata as well, including nested numerical measurements;
full-axis DataFrame slots retain their labels and values. The coordinator checks
semantic values within bounded nesting, not raw H5AD byte identity.
Read inputs through their supplied /inputs paths, read parameters from
/method/parameters.json, and write only the declared /outputs paths. Never
modify source inputs or approved artifacts. Use a module-level main guard where
the declared interface runs a Python program. Do not install dependencies or
assume software unavailable in the fixed image.
Base scientific behavior on actually provided primary evidence and record only
source references present in the supplied context. source_references must contain
only exact requested_url/final_url values from retrieved sources. If sources is
empty, return source_references: []; put the absence of retrieved evidence in
algorithm_summary or introduced_choices, never invent a citation or place
descriptive text in source_references. Separate paper-specified
behavior and assumptions from introduced implementation choices. Describe gaps
honestly; do not fabricate citations, benchmark results or completed tests.
Return exactly source_code, algorithm_summary, introduced_choices and
source_references. Source code is an untrusted draft, not an execution request.
Do not execute, install, register, bind, approve or promote anything. Generated
or materially adapted implementations remain custom, unreviewed and low
confidence; successful execution alone never establishes scientific validation.
Return concise observable design statements, never hidden chain-of-thought.
"""


_DRAFT = "https://json-schema.org/draft/2020-12/schema"
_TEXT = {"type": "string", "minLength": 1, "maxLength": 4096, "pattern": r"\S"}
_NAME = {**_TEXT, "maxLength": 200}


def _object(properties):
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(properties)}


def _mapping(value, maximum=16):
    return {"type": "object", "maxProperties": maximum, "propertyNames": _NAME,
            "additionalProperties": value}


_POINTER = {"type": "string", "maxLength": 300, "pattern": r"^/(?:[^~]|~[01])*$"}
_METRIC_SOURCE = {"oneOf": [
    _object({"kind": {"const": "runtime"}, "field": {"enum": ["elapsed_seconds", "output_bytes"]}}),
    _object({"kind": {"const": "validation"}, "field": _POINTER}),
    _object({"kind": {"const": "output_json"}, "output": _NAME, "field": _POINTER}),
]}
_EXPERIMENT = deepcopy(PLAN_SCHEMA["properties"]["experiments"]["items"])
_EXPERIMENT["additionalProperties"] = False
_EXPERIMENT["properties"]["candidates"]["maxItems"] = 16
_EXPERIMENT["properties"].update({
    "metric_sources": _mapping(_METRIC_SOURCE, 64),
    "inputs": _mapping(_mapping({"oneOf": [_TEXT, _object({
        "format": {"const": "devbench.trial-input/1"},
        "artifact_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "size_bytes": {"type": "integer", "minimum": 0, "maximum": 100_000_000},
        "artifact_type": {"enum": ["json", "file", "table", "anndata"]},
    })]})),
    "parameters": _mapping({"type": "object", "maxProperties": 128}),
    "resource_limits": _object({
        "memory_mb": {"type": "integer", "minimum": 1, "maximum": 65536},
        "timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 3600},
        "max_runs": {"type": "integer", "minimum": 1, "maximum": 16},
    }),
})
_EXPERIMENT["required"] += ["metric_sources", "inputs", "parameters"]
TRIAL_DESIGN_SCHEMA = {"$schema": _DRAFT, "title": "ScientificTrialProposals", **_object({
    "experiments": {"type": "array", "minItems": 0, "maxItems": 4, "items": _EXPERIMENT},
})}

_SOURCE_CODE = {"type": "string", "minLength": 1, "maxLength": 100_000, "pattern": r"^[^\u0000]+$"}
_TEXTS = {"type": "array", "maxItems": 32, "items": _TEXT}
IMPLEMENTATION_SCHEMA = {"$schema": _DRAFT, "title": "UntrustedImplementationDraft", **_object({
    "source_code": _SOURCE_CODE, "algorithm_summary": _TEXT,
    "introduced_choices": _TEXTS, "source_references": _TEXTS,
})}


def _validate(schema, value, label):
    try:
        Draft202012Validator(schema, registry=Registry()).validate(value)
    except ValidationError as error:
        # Never echo an entire code draft or input payload in validation errors.
        location = "/".join(str(part) for part in error.absolute_path) or "root"
        raise ValueError(f"Invalid {label} at {location}: {error.validator}") from error


def validate_trial_design(value) -> list[dict]:
    """Return detached experiment proposals, without admission or execution."""
    copied = _copy_json(value)
    _validate(TRIAL_DESIGN_SCHEMA, copied, "trial design")
    identities = set()
    for experiment in copied["experiments"]:
        if experiment["id"] in identities:
            raise ValueError("Duplicate trial proposal id")
        identities.add(experiment["id"])
        names = experiment["candidates"]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate trial candidate name")
        if set(experiment["inputs"]) != set(names) or set(experiment["parameters"]) != set(names):
            raise ValueError("Every trial candidate requires matching explicit inputs and parameters")
        if experiment["resource_limits"]["max_runs"] < len(names):
            raise ValueError("Trial run budget cannot cover its candidates")
        metrics = validate_metrics(experiment["metrics"])
        if set(experiment["metric_sources"]) != {metric["name"] for metric in metrics}:
            raise ValueError("Every predeclared metric requires exactly one runner-owned source")
        for metric in metrics:
            source = experiment["metric_sources"][metric["name"]]
            if source["kind"] != "runtime":
                for token in source["field"].split("/")[1:]:
                    decoded = token.replace("~1", "/").replace("~0", "~")
                    validate_metrics([{**metric, "name": decoded}])
    return copied["experiments"]


def validate_implementation(value) -> dict:
    """Validate bounded textual draft fields; never parse or execute the code."""
    if type(value) is not dict or set(value) != set(IMPLEMENTATION_SCHEMA["required"]):
        raise ValueError("Implementation drafts require exactly the four declared text fields")
    for field in ("source_code", "algorithm_summary"):
        item = value[field]
        maximum = 100_000 if field == "source_code" else 4096
        if type(item) is not str or not item.strip() or len(item) > maximum or "\x00" in item:
            raise ValueError(f"Invalid bounded implementation text: {field}")
    for field in ("introduced_choices", "source_references"):
        items = value[field]
        if type(items) is not list or len(items) > 32 or any(
                type(item) is not str or not item.strip() or len(item) > 4096 or "\x00" in item for item in items):
            raise ValueError(f"Invalid bounded implementation text list: {field}")
    try:
        # Source drafts have a distinct 100k-character allowance; ordinary
        # planner context strings remain subject to their smaller bound.
        copied = json.loads(canonical_json(value))
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError("Implementation draft is not strict finite UTF-8 JSON") from error
    _validate(IMPLEMENTATION_SCHEMA, copied, "implementation draft")
    return copied


def trial_terminal_outcome(experiments, results, *, refusal=None):
    """Classify observable trial evidence without calling an abstention a refusal.

    Refusal links must be resolved by the coordinator; their existence never
    establishes scientific correctness. Missing outputs are operational failures.
    A verified scientific null requires a separate schema-valid empty-claim
    result bound to an actual executed receipt; this generic helper cannot infer it.
    """
    if results and all(row.get("status") in {"success", "partial"} for row in results):
        return "executed" if all(row.get("artifacts") for row in results) else "operational_failure"
    if not experiments and not results and type(refusal) is dict:
        if (type(refusal.get("reason")) is str and refusal["reason"].strip()
                and type(refusal.get("evidence_artifact_ids")) is list and refusal["evidence_artifact_ids"]
                and all(type(identity) is str and identity for identity in refusal["evidence_artifact_ids"])):
            return "evidence_linked_refusal_pending_review"
    return "operational_failure"
