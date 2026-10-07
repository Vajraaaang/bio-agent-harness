"""Verify controlled-workflow semantics over preserved evidence, without fitting data.

External root and signer trust are mandatory. Authorization is checked at recorded
event times for historical verification, not renewed for another execution. Actor
identity, trusted time, event attestation, retention and clinical validity remain
deployment/qualification responsibilities.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re

from devharness_core.provenance import (
    MAX_JSON_BYTES, _open_regular, _signature, _unique_keys, canonical_json, verify_execution,
)
from .contracts import canonical_json_bytes, parse_plan_bytes
from .trust import TrustedSigner, verify_plan_approvals
from .result_schema import validate_accepted_result

STAGES = ("attempt_started", "plan_approvals_verified", "inputs_preserved",
          "numerical_execution_started", "numerical_execution_returned")
WORKER = "devharness_controlled.executor._controlled_worker"
METHOD = "temporal_glm/4_exact_terms_and_effect_scale"
LEGACY_METHOD = "temporal_glm/2_gaussian_F"


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _digest(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _time(value):
    _require(isinstance(value, str), "Missing recorded time")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None, "Recorded time lacks timezone")
    return parsed.astimezone(timezone.utc)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _read(path, digest=None, limit=MAX_JSON_BYTES):
    with _open_regular(path) as handle:
        before = _signature(os.fstat(handle.fileno()))
        raw = handle.read(limit+1)
        _require(_signature(os.fstat(handle.fileno())) == before, "Evidence changed while reading")
    _require(len(raw) <= limit, "Evidence exceeds read bound")
    if digest is not None:
        _require(hashlib.sha256(raw).hexdigest() == digest, "Evidence changed since integrity verification")
    return raw


def _json(raw):
    value = json.loads(raw, object_pairs_hook=_unique_keys)
    canonical_json(value)
    return value


class _Evidence:
    def __init__(self, root, references):
        self.root, self.references = root, references

    def reference(self, name, required=True):
        matches = [r for r in self.references if r["name"] == name]
        _require(len(matches) == 1 if required else len(matches) <= 1,
                 "Missing or ambiguous preserved artifact: " + name)
        return matches[0] if matches else None

    def bytes(self, name, limit=MAX_JSON_BYTES):
        ref = self.reference(name)
        _require(ref["kind"] == "file", "Expected file artifact: " + name)
        return _read(self.root / "objects" / ref["sha256"], ref["sha256"], limit)

    def json(self, name, limit=MAX_JSON_BYTES):
        return _json(self.bytes(name, limit))


def _formula(plan):
    return " + ".join(["1", "numeric_time"] + [f"C({name})" for name in plan.categorical_covariates]
                      + list(plan.continuous_covariates))


def _check_approvals(plan, evidence, event, start_time, signers):
    approvals = evidence.json("approvals.json", 1024*1024)
    archived_registry = evidence.json("trust-registry.json", 1024*1024)
    at = _time(event["timestamp_utc"])
    expected = verify_plan_approvals(plan, approvals, signers, now=at)
    archived = verify_plan_approvals(plan, approvals, archived_registry, now=at)
    _require(expected == archived, "Archived registry disagrees with configured trust")
    receipts = event["payload"].get("receipts")
    _require(type(receipts) is list and len(receipts) == 2, "Missing approval verification receipts")
    indexed = {r["key_id"]: r for r in expected}
    _require(len({r["key_id"] for r in receipts}) == 2, "Duplicate approval receipt")
    for receipt in receipts:
        wanted = indexed[receipt["key_id"]]
        _require({k:v for k,v in receipt.items() if k != "verified_at"}
                 == {k:v for k,v in wanted.items() if k != "verified_at"}, "Approval receipt does not match signature")
        _require(start_time <= _time(receipt["verified_at"]) <= at, "Approval verification time is outside event interval")
        # The earlier enclosing event check is not enough: the receipt must not
        # claim signature validation before issuance or after expiry.
        verify_plan_approvals(plan, approvals, signers, now=_time(receipt["verified_at"]))
    return approvals


def _check_environment(plan, evidence):
    identity = evidence.json("environment.json")
    _require(identity["implementation_sha256"] == plan.implementation_sha256
             and _digest(identity["source"]) == plan.implementation_sha256, "Implementation identity mismatch")
    _require(identity["environment_sha256"] == plan.environment_sha256
             and _digest(identity["environment"]) == plan.environment_sha256, "Environment identity mismatch")
    source = identity["source"]
    required = {"devharness_controlled/executor.py", "devharness_controlled/contracts.py", "devharness_controlled/trust.py",
                "devharness_core/storage.py", "devharness_core/provenance.py", "devharness_core/watchdog.py",
                "devharness_core/stages.py", "devharness_server/tools_stat.py", "devharness_server/normalization.py"}
    _require(type(source) is dict and required <= set(source) and len(source) <= 512, "Incomplete source identity")
    for name, digest in source.items():
        path = PurePosixPath(name)
        _require(not path.is_absolute() and name == path.as_posix() and ".." not in path.parts
                 and path.parts[0] in {"devharness_controlled", "devharness_core", "devharness_server"}
                 and path.suffix == ".py", "Unsafe source identity path")
        _require(any(r["kind"] == "file" and r["name"] == path.name and r["sha256"] == digest
                     for r in evidence.references), "Source is not preserved: " + name)
    return identity


def _check_inputs(plan, evidence, event):
    inputs = event["payload"]["inputs"]
    _require(type(inputs) is dict and len(inputs) == 2 and "metadata.csv" in inputs, "Invalid preserved input set")
    expression = next(name for name in inputs if name != "metadata.csv")
    _require(expression in {"expression.zarr", "expression.csv"}, "Unsupported controlled expression artifact")
    for name, expected in ((expression, plan.counts_sha256), ("metadata.csv", plan.metadata_sha256)):
        ref = evidence.reference(name)
        _require(inputs[name] == ref and ref["sha256"] == expected, "Preserved input is not bound to plan: " + name)
    _require(inputs["metadata.csv"]["kind"] == "file", "Metadata artifact must be a file")
    _require(inputs[expression]["kind"] == ("directory" if expression.endswith(".zarr") else "file"),
             "Expression artifact kind mismatch")


def _check_invocation(plan, event):
    expected = {"worker": WORKER, "formula": _formula(plan), "selection_criterion": "fixed",
                "hypothesis_genes": list(plan.hypothesis_genes), "timeout_sec": plan.timeout_sec,
                "max_memory_mb": plan.max_memory_mb, "generated_code": False, "model_service_calls": 0}
    _require(canonical_json(event["payload"]) == canonical_json(expected), "Numerical invocation differs from signed plan")
    _require(plan.data_classification in {"synthetic", "public_processed"}, "Unadmitted clinical execution")


def _check_result(plan, evidence, event, completed, identity):
    ref = evidence.reference("numerical-result.json")
    _require(event["payload"] == {"result_sha256": ref["sha256"]}, "Numerical result link mismatch")
    result = evidence.json("numerical-result.json")
    analysis = result["analysis"]
    parameters = analysis["provenance"]["parameters"]
    method = parameters.get("statistical_method_version")
    _require(method in {METHOD, LEGACY_METHOD}, "Unsupported retained numerical method version")
    expected = {"formula_str": _formula(plan), "time_col": plan.time_column, "gene_list": list(plan.hypothesis_genes),
                "statistical_method_version": method, "selection_criterion": "fixed", "spline_dfs": [plan.spline_df],
                "data_type": plan.data_type, "calibration_max_genes": len(plan.calibration_genes) or len(plan.hypothesis_genes),
                "strict": True, "glm_maxiter": 200, "glm_tol": 1e-8}
    if method == METHOD:
        expected["temporal_term_interpretation"] = "parsed_variable_identity/1"
    if plan.categorical_covariates:
        expected["categorical_columns"] = list(plan.categorical_covariates)
    _require(canonical_json(parameters) == canonical_json(expected), "Returned numerical parameters differ from plan")
    packages = ("numpy", "pandas", "scipy", "statsmodels", "patsy") + (("pydeseq2",) if plan.data_type == "counts" else ())
    _require(analysis["provenance"]["library_versions"] == {
        name: identity["environment"]["dependencies"][name] for name in packages},
        "Returned library versions differ from preserved environment")
    if not completed:
        return
    _require(analysis["status"] == "ok" and set(analysis["genes"]) == set(plan.hypothesis_genes), "Incomplete accepted hypothesis family")
    _require(analysis["family"] == ("negative_binomial" if plan.data_type == "counts" else "gaussian"), "Family differs from plan")
    _require(analysis["selection_criterion"] == "fixed" and analysis["spline_dfs"] == [plan.spline_df]
             and analysis["fdr_scope"] == "requested_genes", "Unapproved selection or FDR scope")
    _require(analysis["normalization_method"] == analysis["normalization"]["method"]
             == ("median_of_ratios" if plan.data_type == "counts" else "none"),
             "Unapproved normalization")
    validation = result["input_validation"]
    _require(validation["biological_unit_column"] == plan.biological_unit_column
             and validation["reference_sample_id"] == plan.reference_sample_id
             and validation["time_unit"] == plan.time_unit and validation["independence_verified"] is False,
             "Input validation differs from approved sampling specification")
    rows = list(csv.DictReader(io.StringIO(evidence.bytes("metadata.csv", 8*1024*1024).decode("utf-8"))))
    samples = validation["sample_ids"]
    _require(type(samples) is list and len(samples) == len(set(samples)) == len(rows) == validation["biological_unit_count"]
             and samples[0] == plan.reference_sample_id and set(samples) == {row["sample_id"] for row in rows},
             "Recorded samples differ from preserved metadata")
    units = [row[plan.biological_unit_column] for row in rows]
    _require(len(set(units)) == len(units) and all(unit.strip() for unit in units), "Invalid biological unit record")
    design = analysis["design"]
    _require(design == validation["design"] and design["is_full_rank"] is True
             and type(design["rank"]) is int and design["rank"] == design["num_parameters"]
             and _finite(design["condition_number"]) and 0 < design["condition_number"] <= 1000,
             "Invalid accepted design")
    formula = re.sub(r"\bnumeric_time\b", f"cr(numeric_time, df={plan.spline_df}, constraints='center')", _formula(plan))
    if method == METHOD:
        from devharness_core.design_terms import temporal_spline_formula
        formula = temporal_spline_formula(_formula(plan), plan.spline_df)
    _check_statistics(plan, analysis, len(samples), design, formula)
    validate_accepted_result(plan, result, rows, formula)


def _check_statistics(plan, analysis, sample_count, design, formula):
    from scipy.stats import chi2, f

    p_values, q_values = [], []
    _require(analysis["formula"] == formula, "Returned formula differs from locked design")
    for name in plan.hypothesis_genes:
        gene = analysis["genes"][name]
        _require(gene["status"] == "ok" and gene["selected_df"] == plan.spline_df
                 and analysis["selected_df"][name] == plan.spline_df and gene["design"] == design and gene["formula"] == formula,
                 "Failed, uninformative or changed accepted model")
        p, q = gene["p_value"], gene["padj"]
        _require(_finite(p) and _finite(q) and _finite(gene["q_value"]) and 0 <= p <= q <= 1
                 and gene["q_value"] == q, "Invalid accepted probability")
        stat = gene["test_statistic"]
        residual_df = sample_count-design["num_parameters"]
        _require(_finite(stat) and stat >= 0 and residual_df > 0 and gene["residual_df"] == residual_df
                 and gene["test_df_num"] == plan.spline_df, "Invalid test statistic/degrees of freedom")
        if plan.data_type == "continuous":
            _require(gene["test_distribution"] == "F" and gene["test_df_denom"] == residual_df, "Wrong Gaussian reference distribution")
            expected_p = float(f.sf(stat, plan.spline_df, residual_df))
        else:
            _require(gene["test_distribution"] == "chi_square" and gene["test_df_denom"] is None
                     and _finite(gene["dispersion_alpha"]) and gene["dispersion_alpha"] > 0, "Wrong NB reference/dispersion")
            expected_p = float(chi2.sf(stat, plan.spline_df))
        _require(math.isclose(p, expected_p, rel_tol=1e-8, abs_tol=1e-12), "p-value does not match recorded test")
        p_values.append(p); q_values.append(q)
    # Recompute BH without relying on a supplied q>=p invariant alone.
    ordered = sorted(range(len(p_values)), key=p_values.__getitem__)
    minimum = 1.0
    for rank in range(len(ordered), 0, -1):
        index = ordered[rank-1]
        minimum = min(minimum, p_values[index]*len(ordered)/rank)
        _require(math.isclose(q_values[index], max(p_values[index], minimum), rel_tol=1e-12, abs_tol=1e-15),
                 "BH adjustment differs from locked hypothesis family")
    if plan.data_type == "counts":
        _require(tuple(analysis["dispersion_calibration"]["gene_ids"]) == plan.calibration_genes, "Calibration panel differs from plan")


def verify_controlled_execution(manifest_path, expected_root_hash, trusted_signers, *, authority_administrators=None) -> dict:
    """Check a supported retained workflow against explicit caller-provisioned trust.

    A valid failed attempt is not a completed analysis. This does not certify that
    a hostile host really performed the logged work or that the assay is valid.
    """
    report = {"schema_version": "controlled_evidence_verification/1", "valid": False,
              "integrity_valid": False, "controlled_evidence_valid": False, "errors": [],
              "execution_status": None, "analysis_completed": False, "approval_signatures_verified": 0,
              "clinical_release_allowed": False, "compliance_certified": False,
              "authorization_scope": "configured keys at recorded event times; not new execution authorization",
              "identity_scope": "human identity, trusted clock, host execution attestation and retention not established"}
    try:
        _require(isinstance(expected_root_hash, str) and re.fullmatch(r"[0-9a-f]{64}", expected_root_hash), "An explicit expected root is required")
        path = Path(manifest_path)
        _require(path.is_absolute() and not path.is_symlink(), "Manifest path must be absolute and not a symlink")
        integrity = verify_execution(path, expected_root_hash)
        report["integrity_valid"] = integrity["valid"]
        report["root_hash"] = integrity["root_hash"]
        _require(integrity["valid"], "Integrity verification failed: " + "; ".join(integrity["errors"]))
        _require(isinstance(trusted_signers, (list, tuple, dict)) and trusted_signers, "Explicit configured signer trust is required")
        for signer in trusted_signers.values() if isinstance(trusted_signers, dict) else trusted_signers:
            TrustedSigner.model_validate(signer)
        manifest = _json(_read(path))
        _require(_digest({k:v for k,v in manifest.items() if k != "root_hash"}) == expected_root_hash, "Manifest changed during verification")
        nodes = []
        for node_id in manifest["node_ids"]:
            node = _json(_read(path.parent / "nodes" / (node_id+".json")))
            _require(_digest({k:v for k,v in node.items() if k != "node_id"}) == node_id, "Node changed during verification")
            nodes.append(node)
        _require(len(nodes) >= 2 and nodes[-1]["kind"] == "attempt_finished", "Incomplete controlled attempt history")
        evidence = _Evidence(path.parent, manifest["artifacts"])
        plan_bytes = evidence.bytes("plan.json", 1024*1024)
        from .native_contracts import NATIVE_PLAN_MODELS
        if _json(plan_bytes).get("schema_version") in NATIVE_PLAN_MODELS:
            # Explicit schema dispatch preserves Python-v1 interpretation and
            # gives native records their own runtime/result semantic checks.
            from .native_verification import verify_native_execution
            return verify_native_execution(manifest_path, expected_root_hash, trusted_signers,
                **({"authority_administrators":authority_administrators} if authority_administrators is not None else {}))
        plan = parse_plan_bytes(plan_bytes)
        outcome = evidence.json("outcome.json", 1024*1024)
        _require(outcome == nodes[-1]["payload"], "Preserved outcome differs from completion event")
        _require(outcome["schema_version"] == "controlled_execution/1" and outcome["clinical_release_allowed"] is False,
                 "Unsupported outcome or clinical-release claim")
        status = outcome["status"]
        _require(status in {"analysis_complete_pending_review", "failed"}, "Unsupported execution outcome")
        completed = status == "analysis_complete_pending_review"
        expected_stages = list(STAGES if completed else STAGES[:len(nodes)-1]) + ["attempt_finished"]
        _require([n["kind"] for n in nodes] == expected_stages, "Missing, duplicated or out-of-order controlled stage")
        previous_time = _time(manifest["created_at_utc"])
        for index, node in enumerate(nodes):
            _require(node["parents"] == ([nodes[index-1]["node_id"]] if index else []), "Broken controlled causal sequence")
            at = _time(node["timestamp_utc"])
            _require(at >= previous_time, "Controlled event times run backwards")
            previous_time = at
        _require(_time(manifest["finalized_at_utc"]) >= previous_time, "Manifest finalized before events")
        _require(manifest["analysis_fingerprint"] == outcome["plan_sha256"] == plan.sha256()
                 and nodes[0]["payload"] == {"plan_sha256": plan.sha256()}, "Plan identity is not consistently bound")
        _require(_finite(outcome["elapsed_seconds"]) and outcome["elapsed_seconds"] >= 0, "Invalid elapsed execution time")
        by_kind = {n["kind"]: n for n in nodes}
        approvals = None
        identity = None
        if "plan_approvals_verified" in by_kind:
            approvals = _check_approvals(plan, evidence, by_kind["plan_approvals_verified"], _time(nodes[0]["timestamp_utc"]), trusted_signers)
            report["approval_signatures_verified"] = 2
        if "inputs_preserved" in by_kind:
            identity = _check_environment(plan, evidence)
            _check_inputs(plan, evidence, by_kind["inputs_preserved"])
        if "numerical_execution_started" in by_kind:
            event = by_kind["numerical_execution_started"]
            verify_plan_approvals(plan, approvals, trusted_signers, now=_time(event["timestamp_utc"]))
            _check_invocation(plan, event)
        if "numerical_execution_returned" in by_kind:
            _check_result(plan, evidence, by_kind["numerical_execution_returned"], completed, identity)
        if not completed:
            _require(isinstance(outcome.get("error"), str) and outcome["error"]
                     and isinstance(outcome.get("error_type"), str) and outcome["error_type"], "Failed attempt lacks failure evidence")
        # Detect late changes to blobs not parsed above (e.g. expression chunks).
        final = verify_execution(path, expected_root_hash)
        _require(final["valid"], "Evidence changed during semantic verification")
        report.update(valid=True, controlled_evidence_valid=True, execution_status=status, analysis_completed=completed)
    except (OSError, ValueError, TypeError, KeyError, IndexError, StopIteration, AttributeError, OverflowError, RecursionError, csv.Error) as error:
        report["errors"].append(str(error) or type(error).__name__)
    return report
