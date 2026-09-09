"""Prespecified DESeq2 contrast geometry and pointwise interval verification.

Intervals are constructed from native log2 estimates/SEs. They are conditional
normal-Wald intervals, not native CI output, simultaneous bands, prediction
intervals, selection-adjusted inference or clinical qualification.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.stats import norm

from .native_contracts import NativeEffectPlan

SCHEMA = "native_nb_effect_result/1"
EFFECT_SCHEMA = "native_deseq2_effects/1"
SCOPE = "Pointwise normal-Wald interval for a prespecified fitted normalized-mean log2 ratio; plug-in normalization and dispersion"
RESERVED_R_NAMES = {"if", "else", "repeat", "while", "function", "for", "in", "next", "break", "TRUE", "FALSE",
                    "NULL", "Inf", "NaN", "NA", "NA_integer_", "NA_real_", "NA_complex_", "NA_character_"}
METHOD = {"api": "DESeq2::results(test=Wald, numeric contrast) on saved LRT fit",
          "coefficient_refit": False, "dispersion_refit": False, "lfc_shrink": False,
          "coefficient_prior_learned": False, "min_mu": 0.5,
          "interval_distribution": "normal", "interval_scope": SCOPE,
          "p_values": "Not exposed for this secondary contrast; primary p/q retain the joint LRT meaning"}


def _require(value, reason):
    if not value:
        raise ValueError(reason)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _close(left, right):
    return _finite(left) and _finite(right) and math.isclose(left, right, rel_tol=1e-8, abs_tol=1e-11)


def effect_design(plan, full, columns, times, counts):
    """Derive the exact contrast from two observed, nuisance-matched signed rows."""
    _require(isinstance(plan, NativeEffectPlan), "Effect plan required")
    a, b = plan.sample_ids.index(plan.effect_reference_sample_id), plan.sample_ids.index(plan.effect_comparison_sample_id)
    contrast = np.asarray(full[b]-full[a], dtype=float)
    keep = [i for i in range(len(columns)) if i+1 not in plan.temporal_columns]
    _require(times[b] > times[a], "Effect comparison must be later than its reference")
    _require(np.isfinite(contrast).all() and np.any(contrast != 0) and np.all(contrast[keep] == 0),
             "Effect design rows must share every nuisance value and have a nonzero temporal contrast")
    if np.all(contrast >= 0) or np.all(contrast <= 0):
        zero_flags = np.zeros(len(plan.panel_genes), dtype=bool)
    else:
        selected = np.asarray(full) @ (contrast != 0).astype(float) != 0
        zero_flags = np.sum(np.asarray(counts)[:, selected], axis=1) == 0
    return {"name": plan.effect_name, "reference_sample_id": plan.effect_reference_sample_id,
            "comparison_sample_id": plan.effect_comparison_sample_id, "reference_time": times[a], "comparison_time": times[b],
            "time_unit": plan.time_unit, "confidence_level": plan.effect_confidence_level,
            "design_columns": list(columns), "native_coefficient_names": [name+"." if name in RESERVED_R_NAMES else name for name in columns],
            "reference_row": np.asarray(full[a]).tolist(), "comparison_row": np.asarray(full[b]).tolist(),
            "contrast": contrast.tolist(),
            "native_zero_convention": {name: bool(zero_flags[plan.panel_genes.index(name)]) for name in plan.hypothesis_genes},
            "interpretation": "Contrast of fitted population means at observed design rows; not an individual longitudinal change"}


def primary_result(plan, result):
    return result["primary"] if isinstance(plan, NativeEffectPlan) else result


def validate_effect_result(plan, result):
    """Check the separated primary/effect envelope without changing either part."""
    from .native_results import _validate_native_result

    _require(isinstance(plan, NativeEffectPlan) and type(result) is dict, "Effect plan/result required")
    _require(set(result) == {"schema_version", "status", "error", "clinical_release_allowed", "qualification_status",
                            "primary", "effects", "elapsed_seconds", "memory_peak_mb", "memory_measurement_scope"},
             "Unknown or missing effect envelope fields")
    _require(result["schema_version"] == SCHEMA and result["clinical_release_allowed"] is False
             and result["qualification_status"] == "candidate_unqualified", "Unsupported effect qualification claim")
    _require(result["status"] in {"ok", "partial_failure", "failed"}, "Unsupported effect envelope status")
    _require(_finite(result["elapsed_seconds"]) and result["elapsed_seconds"] >= 0 and result["memory_peak_mb"] is None
             and result["memory_measurement_scope"] == "external_supervisor_required", "Invalid effect resource evidence")
    primary = result["primary"]
    verdict = _validate_native_result(plan.primary_plan(), primary)
    if result["status"] == "ok":
        _require(result["error"] is None, "Successful effect envelope contains an error")
    else:
        _require(result["error"] == {"code": "effect_incomplete" if verdict["usable"] else "primary_incomplete"},
                 "Effect failure does not identify its incomplete component")
        _require(result["status"] == ("failed" if primary["status"] == "failed" else "partial_failure"),
                 "Effect failure status disagrees with primary record")
    effects = result["effects"]
    _require(type(effects) is dict and set(effects) == {"schema_version", "status", "error", "design", "method", "genes", "conditions", "conditions_truncated"}
             and effects["schema_version"] == EFFECT_SCHEMA, "Unsupported native effect record")
    _require(type(effects["conditions"]) is list and len(effects["conditions"]) <= 256
             and type(effects["conditions_truncated"]) is bool, "Malformed effect conditions")
    for condition in effects["conditions"]:
        _require(type(condition) is dict and set(condition) == {"stage", "kind", "message"}
                 and condition["stage"] == "DESeq2_effects" and condition["kind"] in {"warning", "message"}
                 and type(condition["message"]) is str and len(condition["message"]) <= 2000, "Malformed effect condition")
    _require(not effects["conditions_truncated"] or effects["status"] == "failed", "Truncated effect conditions claimed usable")
    if effects["status"] in {"failed", "not_run"}:
        _require(type(effects["error"]) is dict and set(effects["error"]) == {"code", "message"}
                 and type(effects["error"]["code"]) is str and effects["error"]["code"]
                 and type(effects["error"]["message"]) is str and len(effects["error"]["message"]) <= 6000
                 and effects["genes"] is None, "Malformed failed effect record")
        _require(effects["status"] != "not_run" or not verdict["usable"], "Effect omitted despite usable primary result")
        _require(result["status"] != "ok", "Failed effects claimed complete")
        return {"usable": False, "qualification_limits": verdict["qualification_limits"] + ["Effect extraction failed or did not run."]}
    _require(effects["status"] in {"ok", "partial_failure"} and effects["error"] is None, "Invalid effect status")
    _require(verdict["usable"], "Effects computed from an incomplete primary family")
    design = effects["design"]
    _require(type(design) is dict and design["name"] == plan.effect_name
             and design["reference_sample_id"] == plan.effect_reference_sample_id
             and design["comparison_sample_id"] == plan.effect_comparison_sample_id
             and design["confidence_level"] == plan.effect_confidence_level and design["time_unit"] == plan.time_unit,
             "Effect specification differs from signed plan")
    columns = primary["evidence"]["input_validation"]["full_design"]["columns"]
    _require(design["design_columns"] == columns and len(design["contrast"]) == len(columns)
             and all(_finite(value) for value in design["contrast"]) and any(value != 0 for value in design["contrast"]),
             "Invalid effect coefficient order/contrast")
    native_names = [name+"." if name in RESERVED_R_NAMES else name for name in columns]
    _require(design["native_coefficient_names"] == native_names, "Native coefficient name mapping changed")
    method = effects["method"]
    _require(type(method) is dict and set(method) == set(METHOD) | {"beta_prior_variance_log2", "primary_state_unchanged"}
             and all(method[key] == value and (method[key] is value if type(value) is bool else True) for key,value in METHOD.items()),
             "Unapproved effect extraction or interval method")
    _require(method["primary_state_unchanged"] is True and method["beta_prior_variance_log2"] == [1e6]*len(columns),
             "Changed primary state or unsupported numerical regularization")
    _require(type(effects["genes"]) is dict and set(effects["genes"]) == set(plan.hypothesis_genes), "Changed effect family")
    z = float(norm.isf((1-plan.effect_confidence_level)/2))
    usable = True
    fields = {"status", "failure_code", "native_log2_fold_change", "native_standard_error_log2", "native_zero_convention",
              "coefficients_log2", "log2_fold_change", "standard_error_log2", "ci_lower_log2", "ci_upper_log2"}
    for name in plan.hypothesis_genes:
        gene = effects["genes"][name]
        _require(type(gene) is dict and set(gene) == fields and type(gene["native_zero_convention"]) is bool, "Malformed effect estimate")
        _require(type(gene["coefficients_log2"]) is list and len(gene["coefficients_log2"]) == len(columns)
                 and all(x is None or _finite(x) for x in gene["coefficients_log2"]), "Invalid native effect coefficients")
        if gene["status"] != "ok":
            usable = False
            _require(gene["status"] == "failed" and gene["failure_code"] in {"native_zero_count_convention", "native_effect_not_estimable"}
                     and all(gene[k] is None for k in ("log2_fold_change", "standard_error_log2", "ci_lower_log2", "ci_upper_log2")),
                     "Invalid failed effect record")
            _require((gene["failure_code"] == "native_zero_count_convention") == gene["native_zero_convention"],
                     "Effect failure code contradicts native zero convention")
            continue
        lfc, se = gene["log2_fold_change"], gene["standard_error_log2"]
        _require(gene["failure_code"] is None and gene["native_zero_convention"] is False
                 and _finite(lfc) and _finite(se) and se > 0 and lfc == gene["native_log2_fold_change"]
                 and se == gene["native_standard_error_log2"] and all(_finite(x) for x in gene["coefficients_log2"]), "Invalid accepted effect estimate/SE")
        estimate = sum(c*b for c,b in zip(design["contrast"], gene["coefficients_log2"]))
        _require(_close(lfc, estimate) and _close(gene["ci_lower_log2"], lfc-z*se)
                 and _close(gene["ci_upper_log2"], lfc+z*se), "Effect/interval is inconsistent with the recorded contrast")
    _require(effects["status"] == ("ok" if usable else "partial_failure"), "Effect family status contradicts estimates")
    complete = verdict["usable"] and usable
    _require(result["status"] == ("ok" if complete else "partial_failure")
             and (result["error"] is None if complete else type(result["error"]) is dict), "Envelope status contradicts native results")
    limits = [item for item in verdict["qualification_limits"] if item != "This joint temporal-null protocol establishes no clinical biomarker, endpoint effect or interval."]
    limits += ["Endpoint intervals are nominal pointwise normal-Wald constructions using native estimates/SEs and plug-in normalization/dispersions; coverage remains unqualified.",
               "Primary p/q values test the joint temporal null, not the displayed endpoint contrast.",
               "Native betaPrior=FALSE retains fixed log2 coefficient variance 1e6 numerical regularization; it is not zero regularization.",
               "No simultaneous, selection-adjusted, predictive, causal or clinical interval interpretation is established."]
    if effects["conditions"]:
        limits.append("Native effect warnings/messages are retained and require review.")
    return {"usable": complete, "qualification_limits": limits}


def verify_preserved_effect(plan, result, expected_design, full):
    """Reconstruct native contrast variance from preserved design and fitted evidence.

    DESeq2 1.52.0 getContrast calls fitBeta(maxit=0); its pinned C++ covariance
    is inverse(B+Lambda) B inverse(B+Lambda), B=X'WX, with minmu=.5.
    This checks arithmetic consistency, not independent refitting/coverage.
    """
    observed = result["effects"]["design"]
    _require(type(observed) is dict and set(observed) == set(expected_design), "Effect design fields differ from preserved inputs")
    for key, value in expected_design.items():
        if key in {"reference_row", "comparison_row", "contrast"}:
            _require(type(observed[key]) is list and len(observed[key]) == len(value)
                     and all(_close(a,b) for a,b in zip(observed[key],value)), "Effect rows/contrast differ from preserved inputs")
        else:
            _require(observed[key] == value, "Effect design differs from preserved inputs: " + key)
    primary = result["primary"]
    factors = np.array([primary["evidence"]["normalization"]["size_factors"][s] for s in plan.sample_ids])
    x, c = np.asarray(full, dtype=float), np.asarray(expected_design["contrast"], dtype=float)
    ridge = np.diag(np.full(x.shape[1], 1/(math.log(2)**2*1e6)))
    for name in plan.hypothesis_genes:
        gene = result["effects"]["genes"][name]
        _require(gene["native_zero_convention"] is expected_design["native_zero_convention"][name],
                 "Native zero-count convention differs from preserved counts/design")
        if gene["status"] != "ok":
            continue
        beta = np.asarray(gene["coefficients_log2"])*math.log(2)
        alpha = primary["genes"][name]["diagnostics"]["nb_dispersion"]["value"]
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                mu = np.maximum(factors*np.exp(x@beta), .5)
                weights = mu/(1+alpha*mu)
                information = x.T@(weights[:, None]*x)
                inverse = np.linalg.solve(information+ridge, np.eye(x.shape[1]))
                covariance = inverse@information@inverse
                se = math.sqrt(float(c@covariance@c))/math.log(2)
        except (ArithmeticError, np.linalg.LinAlgError, ValueError) as error:
            raise ValueError("Native effect covariance cannot be reconstructed") from error
        _require(_close(gene["standard_error_log2"], se), "Native contrast SE differs from reconstructed covariance")
