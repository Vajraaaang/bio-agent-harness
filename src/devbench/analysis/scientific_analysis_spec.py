"""Exact, content-addressed contracts for the existing native bulk procedure.

This boundary admits no new estimator. The supervised observed-bulk worker owns
the calculation; the episode owns decisions, prerequisite checks and receipts.
Source hashes identify the running checkout, not an immutable execution image.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re

from devharness_core.methods import validate_action_scope
from devharness_core.provenance import canonical_json

from .checkpoints import digest, sha256


SPEC_FORMAT = "devbench.scientific-analysis-spec/1"
DESIGN_FORMAT = "devbench.additive-design/1"
METHOD_VERSION = "observed-bulk-1"
_MEAN_TOOLS = frozenset({"mean_condition_analysis", "validate_design_matrix"})
_COMMON = frozenset({"audit_expression_quality", "validate_biological_units"})
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]{1,160}\Z")
_SPEC_FIELDS = {"format", "method", "action_scope", "inputs", "cohort", "question", "design",
    "contrast", "null_hypothesis", "normalization", "families", "parameters", "parameter_origins",
    "requirement_sources", "source", "environment", "model_comparison"}


def _copy(value):
    return json.loads(canonical_json(value))


def supports_specification(public_task, driver):
    return (driver == "bulk" and public_task.get("measurement") in {"raw_counts", "continuous"}
            and {item["target_id"] for item in public_task["questions"]} == {"mean_contrast"})


def resolve_episode_options(tool, options, public_task, driver):
    """Narrow the worker-wide vocabulary to options used by this procedure.

    Information criteria remain accepted for historical parameter transport, but
    score a single fixed model here. They cannot select among procedures/models.
    """
    options = dict(options)
    if not supports_specification(public_task, driver) or tool not in _MEAN_TOOLS:
        return options
    allowed = {"condition_col", "batch_cols", "block_cols", "reference_condition", "comparison_condition",
        "selection_criterion", "minimum_units", "condition_number_limit", "timeout_sec", "max_mem_mb"}
    unknown = set(options) - allowed
    if unknown:
        raise ValueError("inapplicable_scientific_options:" + ",".join(sorted(unknown)))
    from devbench.study.biology_input_limits import MINIMUM_BIOLOGICAL_UNITS
    from devbench.study.biology_tool_io import DESIGN_CONDITION_NUMBER_LIMIT
    context = public_task["context"]
    levels = context.get("design", {}).get("condition_levels", context.get("condition_labels", []))
    requested = context.get("requested_condition_contrast", {})
    reference = requested.get("denominator", levels[0] if levels else None)
    comparison = requested.get("numerator", levels[1] if len(levels) > 1 else None)
    if reference is None or comparison is None or reference == comparison:
        raise ValueError("scientific_contrast_requires_declared_distinct_levels")
    defaults = {"condition_col": public_task["analysis_columns"]["condition"],
        "batch_cols": public_task["analysis_columns"].get("batches", []),
        "block_cols": public_task["analysis_columns"].get("blocking", []),
        "reference_condition": reference, "comparison_condition": comparison,
        "selection_criterion": "aic", "minimum_units": MINIMUM_BIOLOGICAL_UNITS,
        "condition_number_limit": DESIGN_CONDITION_NUMBER_LIMIT, "timeout_sec": 180, "max_mem_mb": 2048}
    resolved = {**defaults, **options}
    if resolved["selection_criterion"] == "gcv" and public_task["measurement"] != "continuous":
        raise ValueError("gcv_not_available_for_negative_binomial_likelihood")
    return resolved


def validate_additive_design(value, declared_roles):
    """Validate typed terms only; no expression or formula string is accepted."""
    if (not isinstance(value, dict) or set(value) != {"format", "intercept", "terms", "reduced"}
            or value["format"] != DESIGN_FORMAT or value["intercept"] is not True
            or not isinstance(value["terms"], list) or not 1 <= len(value["terms"]) <= 22):
        raise ValueError("invalid_typed_additive_design")
    allowed = {"condition": {declared_roles.get("condition")},
        "batch": set(declared_roles.get("batches", [])), "block": set(declared_roles.get("blocking", [])),
        "donor": set(declared_roles.get("blocking", [])) & {declared_roles.get("population_unit")}}
    columns = []
    for term in value["terms"]:
        if (not isinstance(term, dict) or set(term) != {"role", "column"}
                or term["role"] not in allowed or not isinstance(term["column"], str)
                or not _IDENTIFIER.fullmatch(term["column"]) or term["column"] not in allowed[term["role"]]):
            raise ValueError("undeclared_or_unsupported_design_term")
        columns.append(term["column"])
    if len(set(columns)) != len(columns) or sum(term["role"] == "condition" for term in value["terms"]) != 1:
        raise ValueError("invalid_additive_design_roles")
    if value["reduced"] is not None:
        reduced = value["reduced"]
        if (not isinstance(reduced, dict) or set(reduced) != {"intercept", "terms"}
                or reduced["intercept"] is not True or not isinstance(reduced["terms"], list)
                or any(term not in value["terms"] for term in reduced["terms"])
                or len(reduced["terms"]) >= len(value["terms"])
                or len({canonical_json(term) for term in reduced["terms"]}) != len(reduced["terms"])):
            raise ValueError("reduced_design_is_not_strictly_nested")
    return _copy(value)


def validate_design_matrices(full, contrast, *, reduced=None):
    """Check finite numeric rank, estimability and strict LRT nesting.

    This numerical utility does not admit LRT execution: no such adapter is
    registered at this boundary. Current fits use the retained design audit.
    """
    import numpy as np
    matrix, vector = np.asarray(full, dtype=float), np.asarray(contrast, dtype=float)
    if matrix.ndim != 2 or not matrix.size or not np.isfinite(matrix).all():
        raise ValueError("invalid_design_matrix")
    rank = int(np.linalg.matrix_rank(matrix))
    if rank != matrix.shape[1]:
        raise ValueError("rank_deficient_design")
    if matrix.shape[0] - rank < 2:
        raise ValueError("insufficient_independent_residual_degrees_of_freedom")
    if (vector.shape != (matrix.shape[1],) or not np.isfinite(vector).all()
            or not np.any(vector) or not np.allclose(vector @ np.linalg.pinv(matrix) @ matrix, vector)):
        raise ValueError("contrast_not_estimable")
    nested = None
    if reduced is not None:
        null = np.asarray(reduced, dtype=float)
        if (null.ndim != 2 or null.shape[0] != matrix.shape[0] or not 0 < null.shape[1] < rank
                or not np.isfinite(null).all() or np.linalg.matrix_rank(null) != null.shape[1]
                or np.linalg.matrix_rank(np.column_stack([matrix, null])) != rank):
            raise ValueError("reduced_design_is_not_strictly_nested")
        nested = True
    return {"rank": rank, "column_count": matrix.shape[1], "residual_df": matrix.shape[0]-rank,
        "contrast_estimable": True, "nested": nested}


def execution_capability(tool, driver, public_task):
    return {"identifier": tool, "version": METHOD_VERSION if driver == "bulk" else None,
        "scientifically_plausible": "requires_data_and_question_assessment", "implemented": True,
        "admission": "requires_matching_prerequisites" if tool not in _COMMON | {"validate_design_matrix", "pseudobulk_membership_audit"}
            else "bounded_diagnostic_only",
        "numerically_checked": False, "scientifically_qualified": False,
        "specification_binding": "native_bulk_mean_contrast_v1" if supports_specification(public_task, driver) else "legacy_receipts",
        "scope": "implementation_availability_is_not_scientific_preference_or_qualification"}


def resolve_method_identity(tool, decision, public_task, driver):
    """Resolve a finite exact method identifier; prose cannot choose an engine."""
    version = METHOD_VERSION if driver == "bulk" else "observed-singlecell-1"
    family = "gaussian" if public_task["measurement"] == "continuous" else "negative_binomial"
    aliases = {tool}
    if supports_specification(public_task, driver) and tool in _MEAN_TOOLS:
        aliases.add(family)
        # A design check may refer to the exact fitted procedure it validates.
        if tool == "validate_design_matrix":
            aliases.add("mean_condition_analysis")
    if decision is not None:
        if decision["method"] not in aliases:
            raise ValueError("scientific_method_not_available_or_mismatched:" + decision["method"])
        if decision.get("method_version", version) != version:
            raise ValueError("scientific_method_version_mismatch")
    return {"identifier": tool, "version": version, "engine": "devbench.observed_bulk" if driver == "bulk" else "devbench.observed_singlecell",
        "statistical_family": family if tool in _MEAN_TOOLS else None,
        "qualification": "unvalidated_numerical_implementation"}


def _normalization(measurement):
    if measurement not in {"continuous", "raw_counts"}:
        raise ValueError("unsupported_scientific_measurement")
    return {"method": "unchanged_continuous_input" if measurement == "continuous" else "RLE_library_size_offsets",
        "dispersion": "gaussian_residual_scale" if measurement == "continuous" else "empirical_bayes_negative_binomial",
        "count_conversion": False}


@dataclass(frozen=True, init=False)
class ScientificAnalysisSpec:
    """Immutable canonical JSON, using the shared method/provenance convention."""
    _encoded: bytes
    specification_id: str

    @classmethod
    def from_dict(cls, value):
        value = _copy(value)
        if not isinstance(value, dict) or set(value) != _SPEC_FIELDS or value["format"] != SPEC_FORMAT:
            raise ValueError("invalid_scientific_analysis_specification")
        if (value["method"]["version"] != METHOD_VERSION
                or value["method"]["identifier"] not in _COMMON | _MEAN_TOOLS):
            raise ValueError("unsupported_scientific_specification_method")
        validate_action_scope(value["action_scope"])
        validate_additive_design(value["design"], value["cohort"]["declared_roles"])
        if value["design"]["reduced"] is not None:
            raise ValueError("lrt_execution_adapter_unavailable")
        task = {"measurement": value["question"]["measurement"], "questions": value["question"]["targets"],
            "analysis_columns": value["cohort"]["declared_roles"], "context": {"requested_condition_contrast": {
                "denominator": value["contrast"]["denominator"], "numerator": value["contrast"]["numerator"]}}}
        tool = value["method"]["identifier"]
        if (value["method"] != resolve_method_identity(tool, None, task, "bulk")
                or value["normalization"] != _normalization(task["measurement"])):
            raise ValueError("scientific_method_semantics_mismatch")
        from devbench.study.biology_tools_bulk import validate_bulk_options
        parameters = validate_bulk_options(value["parameters"])
        resolved = resolve_episode_options(tool, parameters, task, "bulk")
        if parameters != resolved or (tool in _COMMON and parameters):
            raise ValueError("scientific_parameters_not_exactly_resolved")
        if tool in _MEAN_TOOLS:
            protected = resolve_episode_options(tool, {}, task, "bulk")
            if any(parameters[key] != protected[key] for key in ("condition_col", "batch_cols", "block_cols",
                    "reference_condition", "comparison_condition")):
                raise ValueError("scientific_parameters_disagree_with_declarations")
        origins = value["parameter_origins"]
        if (not isinstance(origins, list) or {item["name"] for item in origins} != set(value["parameters"])
                or len(origins) != len(value["parameters"]) or any(
                    canonical_json(item["value"]) != canonical_json(value["parameters"][item["name"]])
                    or not item.get("source") for item in origins)):
            raise ValueError("scientific_parameter_origin_binding_mismatch")
        from .method_planner_contract import validate_parameter_origins
        validate_parameter_origins([{"parameter_candidates": origins}], value["requirement_sources"])
        instance = object.__new__(cls)
        object.__setattr__(instance, "_encoded", canonical_json(value))
        object.__setattr__(instance, "specification_id", digest(value))
        return instance

    def to_dict(self):
        return json.loads(self._encoded)


def bind_episode_spec(episode, tool, options, decision=None, proposed_parameters=None):
    task = episode.config["public_task"]
    driver = episode.menu[tool]["driver"]
    if not supports_specification(task, driver) or tool not in _COMMON | _MEAN_TOOLS:
        return None
    method = resolve_method_identity(tool, decision, task, driver)
    roles = task["analysis_columns"]
    parameters = _copy(options)
    context = task["context"]
    levels = context.get("design", {}).get("condition_levels", context.get("condition_labels", []))
    requested = context.get("requested_condition_contrast", {})
    contrast = {"column": roles["condition"], "denominator": requested.get("denominator", levels[0] if levels else None),
        "numerator": requested.get("numerator", levels[1] if len(levels) > 1 else None),
        "source": "public_task.context.requested_condition_contrast_or_declared_condition_levels"}
    terms = [{"role": "condition", "column": roles["condition"]},
        *({"role": "block", "column": name} for name in roles.get("blocking", [])),
        *({"role": "batch", "column": name} for name in roles.get("batches", []))]
    role_sources = {"condition_col": "analysis_columns.condition", "batch_cols": "analysis_columns.batches",
        "block_cols": "analysis_columns.blocking", "reference_condition": "context.requested_condition_contrast_or_declared_condition_levels",
        "comparison_condition": "context.requested_condition_contrast_or_declared_condition_levels"}
    chosen = (decision or {}).get("parameters", {})
    supplied_origins = (decision or {}).get("parameter_origins", [])
    if (not isinstance(supplied_origins, list) or len({item["name"] for item in supplied_origins}) != len(supplied_origins)
            or any(item["name"] not in chosen or canonical_json(item["value"]) != canonical_json(chosen[item["name"]])
                   for item in supplied_origins)):
        raise ValueError("scientific_parameter_origin_binding_mismatch")
    explicit = {item["name"]: item for item in supplied_origins}
    origins = []
    for name, value in sorted(parameters.items()):
        if name in role_sources:
            origin, source = "supplied_declaration", "public_task." + role_sources[name]
        elif name in chosen:
            origin, source = "assistant", "episode.action.decision.parameters." + name
        elif name in (proposed_parameters or {}):
            origin, source = "coordinator_action", "episode.action.options." + name
        elif name in episode.config["fixed_options"].get(tool, {}):
            origin, source = "coordinator_fixed", "episode.fixed_options." + tool + "." + name
        else:
            origin, source = "registry_default", "observed-bulk-1.default." + name
        record = {"name": name, "value": value, "origin": origin,
            "rationale": "Exact executable parameter; source attribution does not authenticate human authorship.", "source": source}
        if name in explicit:
            claimed = explicit[name]["origin"]
            if claimed == "registry_default":
                defaults = resolve_episode_options(tool, {}, task, driver)
                if name in role_sources or name not in defaults or canonical_json(defaults[name]) != canonical_json(value):
                    raise ValueError("scientific_parameter_origin_is_not_the_registry_default")
            elif claimed not in {"user", "assistant"}:
                # Planner discovery may describe such candidates. A native
                # episode has no retained literature/fixture/trial binding for
                # them, so its free text cannot establish that source.
                raise ValueError("scientific_parameter_origin_requires_retained_source")
            record.update(explicit[name])
            record["source"] = (("observed-bulk-1.default." if claimed == "registry_default" else
                "public_task.user_parameter_requirements." if claimed == "user" else
                "episode.action.decision.parameter_origins.") + name)
        origins.append(record)
    runtime = episode.runtime["runtime"]
    value = {"format": SPEC_FORMAT, "method": method,
        "action_scope": {"format": "devbench.method-action/1", "intent": "infer" if tool == "mean_condition_analysis" else "retain",
            "interpretation": "none", "exclusions": False},
        "inputs": {"snapshot_id": episode.snapshot_id, "public_task_sha256": digest(task), "artifacts": {role: {key: data[key] for key in ("kind", "sha256", "size_bytes")}
            for role, data in episode.snapshot.items()}},
        "cohort": {"scope": "all_bound_observations", "selection": None, "declared_roles": roles,
            "biological_units": task["unit_hierarchy"], "identity_scope": "supplied_declaration_requires_observed_nesting_validation"},
        "question": {"objective": episode.config["question"], "targets": task["questions"], "measurement": task["measurement"]},
        "design": {"format": DESIGN_FORMAT, "intercept": True, "terms": terms, "reduced": None},
        "contrast": contrast,
        "null_hypothesis": {"kind": "declared_condition_coefficient_equals_zero", "contrast": contrast,
            "test": "gaussian_coefficient_F" if task["measurement"] == "continuous" else "negative_binomial_wald",
            "evaluated_by_action": tool == "mean_condition_analysis"},
        "normalization": _normalization(task["measurement"]),
        "families": {"eligibility": "all_declared_features_attempted", "testing": task["gene_ids"],
            "adjustment": "Benjamini_Hochberg_over_all_attempted_hypotheses",
            "unavailable_policy": "internal_conservative_p_one_keeps_family_position_public_p_q_remain_missing"},
        "parameters": parameters, "parameter_origins": origins,
        "requirement_sources": task.get("user_parameter_requirements", []),
        "source": {"sha256": episode.source_id, "scope": "running_checkout_hash_not_immutable_execution_snapshot"},
        "environment": {"execution_tier": episode.config["execution_tier"], "python": runtime["python"],
            "executable": runtime["executable"], "packages": runtime.get("all_package_versions", runtime["packages"])},
        "model_comparison": {"performed": False, "candidate_model_count": 1,
            "criterion_scope": "single_fixed_model_score_only", "scientific_superiority_established": False}}
    return ScientificAnalysisSpec.from_dict(value)


def prerequisite_dependency(specification, tool):
    """Conservative versioned dependency key; no stale-design reuse rule."""
    value = specification.to_dict() if isinstance(specification, ScientificAnalysisSpec) else specification
    keys = ("inputs", "cohort", "question", "families", "source", "environment")
    if tool not in _COMMON:
        keys += ("design", "contrast", "normalization", "parameters")
    return digest({"format": "devbench.scientific-prerequisite/1", "validator": tool,
        "validator_version": METHOD_VERSION, **{key: value[key] for key in keys}})


def validate_prerequisite_binding(specification, receipt, tool):
    if receipt.get("dependency_sha256") != prerequisite_dependency(specification, tool):
        raise ValueError("stale_scientific_prerequisite:" + tool)


def validate_observed_design(specification, result):
    """Require the actual worker audit to support this fixed coefficient fit."""
    value = specification.to_dict()
    diagnostics = result.get("diagnostics", {})
    design, units = diagnostics.get("design", {}), diagnostics.get("biological_units", {})
    if not units.get("identity_and_nesting_valid") or units.get("inferential_support") is not True:
        raise ValueError("scientific_prerequisite_independence_unresolved")
    if (type(design.get("column_count")) is not int or design.get("rank") != design["column_count"]
            or design.get("standardized_rank") != design["column_count"]):
        raise ValueError("rank_deficient_design")
    if design.get("condition_number_validated") is not True:
        raise ValueError("design_condition_policy_rejected")
    if design.get("residual_df", 0) < 2:
        raise ValueError("insufficient_independent_residual_degrees_of_freedom")
    contrast = value["contrast"]
    if (design.get("condition_column") != contrast["column"]
            or design.get("reference_condition") != contrast["denominator"]
            or design.get("comparison_condition") != contrast["numerator"]
            or len(design.get("blocks", {}).get("condition", [])) != 1):
        raise ValueError("scientific_prerequisite_contrast_not_estimable_or_mismatched")
    if design.get("block_columns", []) != value["cohort"]["declared_roles"].get("blocking", []):
        raise ValueError("scientific_prerequisite_block_design_mismatch")


def verify_execution_binding(specification, event, response):
    value = specification.to_dict()
    method = response.get("result", {}).get("method", {})
    if (event.get("tool") != value["method"]["identifier"] or event.get("arguments") != {"options": value["parameters"]}
            or method.get("tool") != value["method"]["identifier"] or method.get("version") != value["method"]["version"]
            or event.get("receipt_id") != response.get("receipt_id")
            or digest({key: data for key, data in event.items() if key != "receipt_id"}) != event.get("receipt_id")):
        raise ValueError("scientific_execution_method_or_options_receipt_mismatch")
    if value["method"]["identifier"] == "mean_condition_analysis":
        diagnostics = response["result"].get("diagnostics", {})
        comparison = diagnostics.get("model_selection")
        if comparison and (comparison.get("model_selection_performed") is not False
                or comparison.get("criterion") != value["parameters"]["selection_criterion"]):
            raise ValueError("scientific_execution_fixed_procedure_mismatch")


def bound_prerequisites(episode, specification, checks):
    """Verify retained checkpoint bytes and dependency keys before a fit."""
    from .episode_synthesis import _observation_bindings
    observations = _observation_bindings(episode.root, list(episode.audit._node_ids))
    receipts = {}
    for check in checks:
        matches = []
        for index, action in enumerate(episode.actions, 1):
            if action.get("kind") != "tool" or action.get("tool") != check:
                continue
            stage = f"action-{index:04d}"
            folder = episode.root / "checkpoints" / stage
            if not (folder / "scientific-execution.json").exists() or episode.checkpoints.verify(stage) is None:
                continue
            binding = observations.get(index, {})
            if (binding.get("tool") != check or binding.get("result_sha256") != sha256(folder / "observation.json")
                    or binding.get("scientific_execution_sha256") != sha256(folder / "scientific-execution.json")
                    or binding.get("checkpoint_receipt", {}).get("sha256") != sha256(folder / "receipt.json")):
                raise ValueError("scientific_prerequisite_record_binding_mismatch")
            receipt = json.loads((folder / "scientific-execution.json").read_text())
            if receipt.get("decision_sha256") != digest(action) or receipt.get("specification_id") != binding.get("specification_id"):
                raise ValueError("scientific_prerequisite_action_binding_mismatch")
            try:
                validate_prerequisite_binding(specification, receipt, check)
            except ValueError:
                continue
            response = json.loads((folder / "observation.json").read_text())
            if response.get("status") != "succeeded" or response["result"].get("diagnostics", {}).get("analysis_error"):
                continue
            if check == "validate_design_matrix":
                validate_observed_design(specification, response["result"])
            matches.append({"action": index, "specification_id": receipt["specification_id"],
                "broker_receipt_id": receipt["broker_receipt_id"], "dependency_sha256": receipt["dependency_sha256"],
                "checkpoint_sha256": sha256(folder / "receipt.json")})
        if not matches:
            raise ValueError("stale_scientific_prerequisite:" + check)
        receipts[check] = matches[-1]
    return receipts


def verify_retained_specification(root, config, action, number, observation_binding, event, response, checkpoint):
    """Read the versioned exact contract without changing historical artifacts."""
    from types import SimpleNamespace
    from devbench.study.biology_broker import biology_tool_menu
    from devbench.study.evidence import _read, decode_json
    from .episodes import _proposed_parameters, resolve_scientific_options

    folder = root / "checkpoints" / f"action-{number:04d}"
    artifacts = {}
    for name in ("scientific-specification.json", "scientific-attempt.json", "scientific-execution.json"):
        content = _read(folder / name, 16_000_000)
        import hashlib
        if checkpoint.get("artifacts", {}).get(name) != hashlib.sha256(content).hexdigest():
            raise ValueError("scientific_execution_artifact_not_bound")
        artifacts[name] = decode_json(content)
    spec = ScientificAnalysisSpec.from_dict(artifacts["scientific-specification.json"])
    attempt, receipt = artifacts["scientific-attempt.json"], artifacts["scientific-execution.json"]
    context = SimpleNamespace(config=config, snapshot_id=config["snapshot_id"], source_id=config["source_sha256"],
        snapshot=decode_json(_read(root / "snapshot.json", 1_000_000)),
        runtime=decode_json(_read(root / "runtime.json", 16_000_000)), menu=biology_tool_menu(config["public_task"]))
    tool = action["tool"]
    options = resolve_scientific_options(tool, _proposed_parameters(action), config["fixed_options"].get(tool, {}),
        config["public_task"], context.menu[tool]["driver"])
    expected = bind_episode_spec(context, tool, options, action.get("decision"), _proposed_parameters(action))
    if (expected is None or expected.specification_id != spec.specification_id
            or observation_binding.get("specification_id") != spec.specification_id
            or observation_binding.get("scientific_execution_sha256") != sha256(folder / "scientific-execution.json")
            or checkpoint.get("metadata") != {"tool": tool, "receipt_id": event["receipt_id"], "specification_id": spec.specification_id}
            or any(attempt.get(key) != receipt.get(key) for key in ("attempt_id", "specification_id", "decision_sha256", "action"))
            or receipt.get("specification_id") != spec.specification_id or receipt.get("decision_sha256") != digest(action)
            or receipt.get("action") != number or receipt.get("broker_receipt_id") != event["receipt_id"]
            or receipt.get("result_sha256") != event["result_sha256"]
            or receipt.get("result_id") != digest({"attempt": attempt["attempt_id"], "receipt": event["receipt_id"]})):
        raise ValueError("scientific_execution_specification_decision_binding_mismatch")
    validate_prerequisite_binding(spec, receipt, tool)
    verify_execution_binding(spec, event, response)
    return spec
