"""Strict discovery envelope validation and trusted-receipt structural checks.

The caller owns evidence_lookup and verifies artifact bytes before supplying it.
A syntactically valid receipt is not proof of execution if supplied by a claimant.
"""
from __future__ import annotations
import json
import hashlib
import numpy as np
import math
import re
from pathlib import Path
from collections.abc import Mapping
from jsonschema import Draft202012Validator

REFERENCE = Path(__file__).resolve().parent / "reference_data"
ISSUE_CODES = ("non_identifiable_design", "technical_replicates", "missing_independence_provenance",
               "invalid_design", "invalid_contrast", "insufficient_biological_units", "fit_failure")
CONTRAST_IDS = ("treated_vs_control", "treated_subgroup_vs_other", "female_treated_vs_control",
                "male_treated_vs_control", "male_minus_female", "batch_2_vs_batch_1")


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("all numeric values must be finite")
    if isinstance(value, Mapping):
        for child in value.values(): _finite(child)
    elif isinstance(value, list):
        for child in value: _finite(child)


def validate_claims(payload, *, gene_ids, sample_ids, metadata_columns=None, contrast_ids=None):
    """Return a valid envelope, otherwise raise ValueError; never repair estimates."""
    _finite(payload)
    schema = json.loads((REFERENCE / "discovery_claims.schema.json").read_text())
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda x: str(x.path))
    if errors: raise ValueError(f"invalid discovery claims: {errors[0].message}")
    genes, samples = set(gene_ids), set(sample_ids)
    factors = set(metadata_columns) if metadata_columns is not None else None
    contrasts = set(contrast_ids) if contrast_ids is not None else set(CONTRAST_IDS)
    ids = set()
    for row in payload["claims"]:
        if row["claim_id"] in ids: raise ValueError("duplicate claim_id")
        ids.add(row["claim_id"])
        if not set(row["genes"]) <= genes: raise ValueError("unknown gene ID")
        if not set(row["samples"]) <= samples: raise ValueError("unknown sample ID")
        if factors is not None and row["factor"] is not None and row["factor"] not in factors:
            raise ValueError("unknown public metadata factor")
        if row["contrast"] is not None and row["contrast"] not in contrasts:
            raise ValueError("unknown declared contrast")
    seen = set()
    for row in payload["gene_statistics"]:
        key = row["gene_id"], row["contrast_id"]
        if key in seen: raise ValueError("duplicate gene/contrast statistic")
        seen.add(key)
        if key[0] not in genes or key[1] not in contrasts: raise ValueError("unknown statistic ID")
        if row["status"] == "fitted" and row["q"] < row["p"]: raise ValueError("q must be at least p")
    status = payload["status"]
    discoveries = [r for r in payload["claims"] if r["assertion_status"] == "discovery"
                   and r["type"] not in ("design_issue", "abstention")]
    if status == "analysis_complete" and not payload["claims"]: raise ValueError("empty completed analysis")
    if status in ("null_result", "scientific_refusal") and discoveries:
        raise ValueError("null/refusal cannot contain inferential discoveries")
    if status == "scientific_refusal":
        if not any(r["type"] in ("design_issue", "abstention") for r in payload["claims"]):
            raise ValueError("refusal needs a diagnostic claim")
        if payload["gene_statistics"]: raise ValueError("refusal cannot invent inferential estimates")
    if status == "operational_failure" and (payload["claims"] or payload["gene_statistics"]):
        raise ValueError("operational failure cannot masquerade as scientific output")
    return payload


def valid_receipt(receipt, *, expected_input_id=None):
    """Reject malformed typed content without masking resource interrupts."""
    try:
        return _valid_receipt(receipt,expected_input_id=expected_input_id)
    except (TypeError,KeyError,ValueError,OverflowError):
        return False


def _identifier(value):
    return isinstance(value,str) and bool(value.strip())


def _identifier_mappings(aggregation):
    if not isinstance(aggregation,Mapping): return True
    for field in ("library_to_source_samples",):
        mapping=aggregation.get(field)
        if mapping is not None and (not isinstance(mapping,Mapping) or not all(
            _identifier(key) and isinstance(values,list) and values and all(_identifier(value) for value in values)
            for key,values in mapping.items())): return False
    mapping=aggregation.get("library_to_biological_unit")
    if mapping is not None and (not isinstance(mapping,Mapping) or not all(_identifier(key) and _identifier(value) for key,value in mapping.items())): return False
    mapping=aggregation.get("sample_to_library_run_unit")
    if mapping is not None and (not isinstance(mapping,Mapping) or not all(_identifier(key) and isinstance(value,Mapping)
        and all(_identifier(value.get(field)) for field in ("library","run","biological_unit")) for key,value in mapping.items())): return False
    return True


def _valid_receipt(receipt, *, expected_input_id=None):
    """Check content/provenance bindings supplied under coordinator custody."""
    if not isinstance(receipt, Mapping) or receipt.get("status") != "executed": return False
    for key in ("input_id", "model_id", "result_id"):
        if not isinstance(receipt.get(key), str) or not receipt[key].strip(): return False
    for key in ("input_sha256", "model_sha256", "result_sha256"):
        if not isinstance(receipt.get(key), str) or re.fullmatch(r"[0-9a-f]{64}", receipt[key]) is None:
            return False
    if expected_input_id is not None and receipt["input_id"] != expected_input_id: return False
    if not _identifier_mappings(receipt.get("aggregation")): return False
    if receipt.get("scope") not in ("marginal_association", "adjusted_association", "subgroup_association",
                                    "interaction", "design_diagnostic", "causal"): return False
    if not isinstance(receipt.get("model_record"),Mapping) or not receipt["model_record"] or content_hash(receipt["model_record"])!=receipt.get("model_sha256"): return False
    if receipt.get("scope")=="design_diagnostic" and not supported_diagnostic(receipt): return False
    if receipt.get("scope")!="design_diagnostic" and not supported_fit_result(receipt): return False
    if receipt.get("scope")!="design_diagnostic" and (not receipt.get("contrast_id") or not receipt.get("factor") or not receipt.get("method")): return False
    return True


def content_hash(value):
    try:
        return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    except (TypeError,ValueError): return None


STATISTIC_FIELDS=("gene_id","contrast_id","status","p","q","effect","effect_scale","reason")


def statistic_signature(row):
    return tuple(row.get(key) for key in STATISTIC_FIELDS)


def supported_fit_result(receipt):
    """Verify stable per-contrast result content, including unavailable estimates."""
    rows=receipt.get("fit_result")
    if not isinstance(rows,list) or not rows or content_hash(rows)!=receipt.get("result_sha256"): return False
    seen=set()
    for row in rows:
        if not isinstance(row,Mapping) or any(key not in row for key in STATISTIC_FIELDS): return False
        if row["contrast_id"]!=receipt.get("contrast_id") or not isinstance(row["gene_id"],str) or row["gene_id"] in seen: return False
        seen.add(row["gene_id"])
        if row["effect_scale"]!="log2": return False
        if row["status"]=="failed":
            if any(row[key] is not None for key in ("p","q","effect")) or not isinstance(row["reason"],str) or not row["reason"]: return False
        elif row["status"]=="fitted":
            if any(type(row[key]) not in (float,int) or not math.isfinite(row[key]) for key in ("p","q","effect")): return False
            if not 0<=row["p"]<=row["q"]<=1 or row["reason"] is not None: return False
        else: return False
    declared=receipt["model_record"].get("hypothesis_family")
    if declared is not None:
        if not isinstance(declared,Mapping): return False
        genes=declared.get("gene_ids");contrasts=declared.get("contrast_ids")
        if not isinstance(genes,list) or not isinstance(contrasts,list) or not genes or not contrasts: return False
        if not all(type(value) is str and value.strip() for value in genes+contrasts): return False
        if type(declared.get("test_count")) is not int or len(set(genes))!=len(genes) or len(set(contrasts))!=len(contrasts): return False
        if set(genes)!=seen or receipt.get("contrast_id") not in contrasts or declared.get("test_count")!=len(genes)*len(contrasts): return False
    return True


def receipt_for_statistic(row,evidence_lookup,*,expected_input_id=None):
    for ref in row["evidence_refs"]:
        receipt=evidence_lookup.get(ref)
        if not valid_receipt(receipt,expected_input_id=expected_input_id) or receipt.get("scope")=="design_diagnostic": continue
        if any(statistic_signature(row)==statistic_signature(actual) for actual in receipt["fit_result"]): return receipt
    return None


def supported_diagnostic(receipt):
    """Recompute executed diagnostic content identity and inspect its observations."""
    result=receipt.get("diagnostic_result")
    if not isinstance(result,Mapping) or result.get("issue_code")!=receipt.get("issue_code"): return False
    try:
        digest=hashlib.sha256(json.dumps(result,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    except (ValueError,TypeError): return False
    if digest!=receipt.get("result_sha256"): return False
    obs=result.get("observations")
    if not isinstance(obs,Mapping) or not obs: return False
    issue=result["issue_code"]
    if issue=="non_identifiable_design":
        try:
            columns=obs["columns"]; matrix=np.asarray(obs["matrix"],dtype=float)
            if not isinstance(columns,list) or not columns or matrix.ndim!=2 or matrix.shape[1]!=len(columns) or not np.isfinite(matrix).all(): return False
            rank=int(np.linalg.matrix_rank(matrix))
            return rank==obs.get("rank") and rank<len(columns)
        except (KeyError,TypeError,ValueError,np.linalg.LinAlgError): return False
    if issue=="technical_replicates":
        mapping=obs.get("same_library_mapping"); units=obs.get("biological_unit_count"); runs=obs.get("run_count")
        if not isinstance(mapping,Mapping) or not mapping or type(units) is not int or type(runs) is not int: return False
        if not all(_identifier(library) and isinstance(samples,list) and samples and all(_identifier(sample) for sample in samples) for library,samples in mapping.items()): return False
        sample_ids=[sample for samples in mapping.values() for sample in samples]
        return units==receipt.get("biological_unit_count")==len(mapping) and runs==len(sample_ids)>units and len(set(sample_ids))==runs
    if issue=="missing_independence_provenance":
        declaration=obs.get("declaration")
        unknown=declaration is None or (isinstance(declaration,Mapping) and declaration.get("status") is None)
        return unknown and type(obs.get("sample_count")) is int and obs["sample_count"]>0
    return issue in ISSUE_CODES and bool(obs)


def receipt_for_claim(row, evidence_lookup, *, expected_input_id=None, _validated=False):
    for ref in row["evidence_refs"]:
        receipt = evidence_lookup.get(ref)
        if receipt is None or (not _validated and not valid_receipt(receipt, expected_input_id=expected_input_id)): continue
        if row["type"] in ("design_issue", "abstention"):
            if receipt.get("scope") != "design_diagnostic" or receipt.get("issue_code") != row["issue_code"]:
                continue
        elif receipt.get("contrast_id") != row["contrast"] or receipt.get("factor") != row["factor"]:
            continue
        elif receipt.get("scope") != row["scope"]:
            continue
        return receipt
    return None
