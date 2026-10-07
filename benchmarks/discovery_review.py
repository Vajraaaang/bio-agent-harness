"""Blinded evidence packets and same-input repeatability, without model calls."""
from __future__ import annotations
import itertools
import json
import re
import statistics
from benchmarks.discovery_claims import valid_receipt

AI_QUALIFICATION="AI review is not qualified independent scientific or clinical validation"
EXCLUDED={"arm","agent_arm","actor_arm","arm_name","model_id","backend","family","case_family","task_family","seed","generation_seed","truth","prior_votes","votes",
          "provider","provider_id","provider_name","agent_provider","agent_model","actor_model","agent_model_id","agent_id","agent_name","llm","llm_model","llm_provider","model_provider","model_backend","provider_model"}
EXCLUDED_NORMALIZED={re.sub(r"[^a-z0-9]","",key.lower()) for key in EXCLUDED}
RECEIPT_FIELDS={"status","input_id","input_sha256","model_sha256","result_id","result_sha256",
    "contrast_id","scope","factor","adjustment_factors","method","biological_unit_count","aggregation",
    "issue_code","fit_result","diagnostic_result","model_record","model"}
MODEL_FIELDS={"kind","family","method","formula","design","parameters","normalization","dispersion",
    "convergence","aggregation","hypothesis_family","provisional","runtime","source_sha256","roles","independence"}
DESIGN_FIELDS={"columns","matrix","row_ids","rank","residual_df","role_levels","contrasts","condition_number"}
RUNTIME_FIELDS={"python","pydeseq2_version","numpy_version","pandas_version","scipy_version","pydeseq2_source_sha256"}
PARAMETER_FIELDS={"n_cpus","normalization","dispersion_fit_type","refit_cooks","cooks_filter","independent_filter",
    "low_memory","alpha","multiple_testing","parameter_source_kind","parameter_source_ref","df","span",
    "degree","degrees_of_freedom","regularization","link","offset","penalty","n_knots","fit_type"}
PUBLIC_FIELDS={"input_id","input_sha256","metadata_sha256","gene_ids","sample_ids","metadata_columns",
               "metadata","input_declaration","counts_summary","data_quality","limitations"}


def _typed_statistical_model(value):
    return isinstance(value,dict) and value.get("kind")=="statistical_model" and (
        isinstance(value.get("design"),dict) or isinstance(value.get("formula"),str))


def _strip(value, *, statistical=False):
    if isinstance(value,dict):
        result={}
        for key,child in value.items():
            normalized=re.sub(r"[^a-z0-9]","",key.lower())
            if normalized=="model" and not _typed_statistical_model(child): continue
            identity_alias=normalized.startswith(("agent","actor","provider","llm","modelprovider"))
            if (normalized in EXCLUDED_NORMALIZED or identity_alias) and not (statistical and normalized=="family" and child in ("negative_binomial","gaussian","poisson","binomial","nb")): continue
            child_statistical=statistical or key in ("model_record","statistical_model") or (key=="model" and isinstance(child,dict))
            result[key]=_strip(child,statistical=child_statistical)
        return result
    if isinstance(value,list): return [_strip(v,statistical=statistical) for v in value]
    return value


def _project_model(model):
    result={key:value for key,value in model.items() if key in MODEL_FIELDS}
    for key,allowed in (("design",DESIGN_FIELDS),("runtime",RUNTIME_FIELDS),("parameters",PARAMETER_FIELDS)):
        if isinstance(result.get(key),dict): result[key]={name:value for name,value in result[key].items() if name in allowed}
    return _strip(result,statistical=True)


def _project_receipt(receipt):
    result={key:value for key,value in receipt.items() if key in RECEIPT_FIELDS}
    result["model_record"]=_project_model(receipt["model_record"])
    if _typed_statistical_model(result.get("model")): result["model"]=_project_model(result["model"])
    else: result.pop("model",None)
    return _strip(result)


def build_review_packet(public_input,payload,*,evidence_lookup):
    """Use only public inputs and caller-authenticated executed receipt evidence."""
    public={k:_strip(v) for k,v in public_input.items() if k in PUBLIC_FIELDS}
    refs=set(payload.get("execution_evidence_refs",[]))
    for row in payload.get("claims",[])+payload.get("gene_statistics",[]): refs.update(row.get("evidence_refs",[]))
    evidence=[];unverified=[];alternatives=set()
    for ref in sorted(refs):
        receipt=evidence_lookup.get(ref)
        if not valid_receipt(receipt,expected_input_id=public_input.get("input_id")):
            unverified.append(ref);continue
        alternatives.add(receipt["model_sha256"])
        evidence.append({"evidence_ref":ref,**_project_receipt(receipt)})
    return dict(schema_version="discovery_review_packet/1",public_input=public,output=_strip(payload),
                executed_evidence=evidence,unverified_evidence_refs=unverified,
                executed_alternative_count=len(alternatives),qualification=AI_QUALIFICATION)


def _canonical(value):
    if isinstance(value,dict): return {k:_canonical(v) for k,v in sorted(value.items())}
    if isinstance(value,list):
        children=[_canonical(v) for v in value]
        return sorted(children,key=lambda v:json.dumps(v,sort_keys=True,allow_nan=False))
    return value


def same_input_repeatability(payloads):
    """Compare full canonical envelopes, retaining stable errors without excusing them."""
    successful=[p for p in payloads if p.get("status") in ("analysis_complete","null_result")
                and any(c.get("assertion_status")=="discovery" and c.get("type") not in ("design_issue","abstention") for c in p.get("claims",[]))]
    if len(payloads)<2 or len(successful)<2:
        return dict(applicable=False,median=None,pair_scores=[],reason="no repeated positive discovery output")
    sets=[]
    for p in payloads:
        tokens={json.dumps({"claim":_canonical(c)},sort_keys=True,allow_nan=False) for c in p.get("claims",[])}
        # Complete errors/statistics/terminal facts remain in the comparison.
        remainder={k:v for k,v in p.items() if k!="claims"}
        tokens.add(json.dumps({"terminal_and_statistics":_canonical(remainder)},sort_keys=True,allow_nan=False))
        sets.append(tokens)
    scores=[len(a&b)/len(a|b) for a,b in itertools.combinations(sets,2)]
    return dict(applicable=True,median=statistics.median(scores),pair_scores=scores,
                reason="same-input repeatability; stable errors do not establish validity")


def require_campaign_approval(config):
    """Gate before held-out generation; no campaign is implemented by this module."""
    if config.get("campaign_approval_status")!="approved" or not config.get("campaign_budget"):
        raise ValueError("campaign approval and nonzero explicit budget are required before generation")
    return True
