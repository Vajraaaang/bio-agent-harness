"""Deterministic evaluator-only matching; software controls are not clinical validation."""
from __future__ import annotations
import json
import copy
from pathlib import Path
import numpy as np
from scipy.optimize import linear_sum_assignment
from benchmarks.discovery_claims import validate_claims, valid_receipt, receipt_for_claim, receipt_for_statistic, statistic_signature


def _jaccard(a,b):
    a,b=set(a),set(b)
    return len(a&b)/len(a|b) if a|b else 1.


def _targets(truth):
    targets=[dict(row) for row in truth.get("signals",[])]
    for i,row in enumerate(targets):
        row.setdefault("target_id",f"target-{i}")
        if "log2fc_by_level" in row:
            row.setdefault("effect_direction_by_level",{k:"positive" if v>0 else "negative" if v<0 else "not_applicable" for k,v in row["log2fc_by_level"].items()})
    issues=truth.get("expected_design_issues",[])
    for issue in issues:
        issue={"condition_batch_confounded":"non_identifiable_design","pseudoreplication":"technical_replicates"}.get(issue,issue)
        if not any(t.get("issue_code")==issue for t in targets):
            targets.append(dict(target_id=f"issue-{issue}",type="design_issue",issue_code=issue))
    return sorted(targets,key=lambda t:t["target_id"])


def _family(truth):
    family=truth.get("family","")
    if family=="F": return "F-"+truth.get("provenance_view","unknown")
    return family


def _specific(row,target,receipt,thresholds):
    diagnostics=("design_issue","abstention")
    if target.get("type") in diagnostics:
        if row["type"] not in diagnostics or row["issue_code"]!=target.get("issue_code"): return None
        return 1.
    if row["type"]!=target.get("type"): return None
    if row["factor"]!=target.get("factor","condition") or row["contrast"]!=target.get("contrast","treated_vs_control"): return None
    if row["direction"]!=target.get("direction","positive"): return None
    gj=_jaccard(row["genes"],target.get("genes",[]))
    if gj<thresholds["gene_jaccard"]: return None
    if row["type"]=="sample_subgroup":
        sj=_jaccard(row["samples"],target.get("samples",[]))
        return gj+sj if sj>=thresholds["sample_jaccard"] else None
    if row["type"]=="interaction":
        if row["scope"]!="interaction" or row["effect_direction_by_level"]!=target.get("effect_direction_by_level",{}): return None
    adjustments=target.get("adjustment_factors",[])
    if adjustments and (row["scope"]!="adjusted_association" or not set(adjustments)<=set(receipt.get("adjustment_factors",[]))): return None
    if target.get("scope") and row["scope"]!=target["scope"]: return None
    return gj


def _coarse_targets(truth,targets):
    explicit=[dict(t) for t in truth.get("coarse_associations",[])]
    family=_family(truth)
    coarse=explicit
    if family=="B":
        for target in targets:
            for level,direction in target.get("effect_direction_by_level",{}).items():
                coarse.append(dict(type="gene_set_effect",genes=target.get("genes",[]),scope="stratum_association",factor="condition",contrast=f"{level}_treated_vs_control",direction=direction))
    if explicit: return coarse
    if family in ("A","B","C"):
        for target in targets:
            if target.get("genes"):
                coarse.append(dict(type="gene_set_effect",genes=target["genes"],scope="marginal_association",
                    factor="condition",contrast="treated_vs_control",direction="positive"))
    if family in ("C","D"):
        for target in truth.get("decoys",[]):
            for factor,contrast in [("condition","treated_vs_control"),("batch","batch_2_vs_batch_1")]:
                coarse.append(dict(type="gene_set_effect",genes=target.get("genes",[]),scope="marginal_association",
                    factor=factor,contrast=contrast,direction=target.get("direction","positive")))
    return coarse


def _coarse_match(row,targets,threshold):
    if row["type"]!="gene_set_effect" or (row["scope"]!="marginal_association" and not (row["scope"] in ("adjusted_association","subgroup_association") and (row["factor"]=="batch" or row["contrast"] in ("female_treated_vs_control","male_treated_vs_control")))): return None
    for t in targets:
        if (row["factor"]==t.get("factor") and row["contrast"]==t.get("contrast")
            and row["direction"]==t.get("direction","positive")
            and _jaccard(row["genes"],t.get("genes",[]))>=threshold): return t
    return None


def _semantic(row):
    ignored={"claim_id","evidence_refs","confidence","limitations"}
    return json.dumps({k:sorted(v) if isinstance(v,list) else v for k,v in row.items() if k not in ignored},sort_keys=True)


def _member_precision(row,target):
    genes=set(row["genes"]); true=set(target.get("genes",[]))
    return {"member_true_count":len(genes&true),"member_false_count":len(genes-true),
            "member_precision":len(genes&true)/len(genes) if genes else None}


def _matching(edges,claim_ids,target_ids):
    if not claim_ids or not target_ids: return []
    n,m=len(claim_ids),len(target_ids)
    bonus=10000*(min(n,m)+1)
    weights=np.zeros((n,m+n),dtype=float)
    weights[:,:m]=-bonus
    for (c,t),weight in edges.items(): weights[claim_ids.index(c),target_ids.index(t)]=bonus+round(weight*1000)
    rows,cols=linear_sum_assignment(weights,maximize=True)
    result=[]
    for i,j in zip(rows,cols):
        if j<m and (claim_ids[i],target_ids[j]) in edges:
            result.append(dict(claim_id=claim_ids[i],target_id=target_ids[j],weight=edges[(claim_ids[i],target_ids[j])]))
    return sorted(result,key=lambda r:(r["target_id"],r["claim_id"]))


def _statistic_truth(truth,targets,coarse,receipt,contrast):
    adjusted=receipt.get("scope")=="adjusted_association" or bool(receipt.get("adjustment_factors"))
    factor=receipt.get("factor")
    result=set()
    for t in targets:
        is_b_stratum=(_family(truth)=="B" and contrast in ("female_treated_vs_control","male_treated_vs_control"))
        if t.get("genes") and (t.get("contrast")==contrast or is_b_stratum):
            if factor==t.get("factor") or is_b_stratum or factor=="condition": result.update(t["genes"])
    if not adjusted or factor=="batch":
        for t in coarse:
            if t.get("contrast")==contrast and t.get("factor")==factor: result.update(t.get("genes",[]))
    return result,"adjusted_condition" if adjusted and factor=="condition" else f"{receipt.get('scope')}/{factor}/{contrast}"


def score_claims(payload,truth,*,gene_ids,sample_ids,evidence_lookup,config=None,expected_input_id=None):
    """Score a strict envelope against evaluator truth and caller-authenticated receipts.

    evidence_lookup must come from the trusted execution coordinator, never the
    actor output. The coordinator must verify referenced artifact content hashes.
    expected_input_id binds a staged input; absent it, truth.input_id is used.
    """
    validate_claims(payload,gene_ids=gene_ids,sample_ids=sample_ids)
    config=config or json.loads((Path(__file__).resolve().parent/"reference_data/discovery_evaluation_config.json").read_text())
    thresholds={k:v["value"] for k,v in config["parameters"].items()}
    expected=expected_input_id or truth.get("input_id")
    targets=_targets(truth);coarse=_coarse_targets(truth,targets);family=_family(truth)
    rows=sorted(payload["claims"],key=lambda r:(r["confidence"] not in config["primary_confidences"],r["claim_id"]))
    # Snapshot caller-owned evidence, validate/hash each receipt once, then index
    # immutable result signatures. No actor row re-hashes or scans a full table.
    snapshot=copy.deepcopy(evidence_lookup)
    verified={ref:receipt for ref,receipt in snapshot.items() if valid_receipt(receipt,expected_input_id=expected)}
    indices={ref:{(actual["gene_id"],actual["contrast_id"]):statistic_signature(actual) for actual in receipt["fit_result"]}
             for ref,receipt in verified.items() if receipt["scope"]!="design_diagnostic"}
    bound_sources=[]
    for row in payload["gene_statistics"]:
        key=(row["gene_id"],row["contrast_id"]); signature=statistic_signature(row)
        refs=[ref for ref in row["evidence_refs"] if ref in indices and indices[ref].get(key)==signature]
        bound_sources.append((row,refs))
    bound_stats=[(row,verified[refs[0]] if refs else None) for row,refs in bound_sources]
    root_verified=any(ref in verified and (payload["status"]!="null_result" or verified[ref]["scope"]!="design_diagnostic")
                      for ref in payload["execution_evidence_refs"])
    unauthenticated=sum(receipt is None for row,receipt in bound_stats)
    available_fit=any(row["status"]=="fitted" and receipt is not None for row,receipt in bound_stats)
    bound_results={}
    root_models={verified[ref]["model_sha256"]:verified[ref]["model_record"] for ref in payload["execution_evidence_refs"]
                 if ref in indices}
    root_fitted_rows={ (verified[ref]["model_sha256"],row["gene_id"],row["contrast_id"])
        for row,refs in bound_sources if row["status"]=="fitted" for ref in refs
        if verified[ref]["model_sha256"] in root_models }
    declared_expected=set();source_coverage=set();actor_coverage=set();declared_universe_valid=bool(root_models)
    for model_id,model in root_models.items():
        declared=model.get("hypothesis_family")
        if not isinstance(declared,dict):
            declared_universe_valid=False;continue
        genes=declared.get("gene_ids");contrasts=declared.get("contrast_ids")
        valid=(isinstance(genes,list) and isinstance(contrasts,list) and bool(genes) and bool(contrasts)
            and all(isinstance(g,str) for g in genes) and all(isinstance(c,str) for c in contrasts)
            and len(set(genes))==len(genes) and len(set(contrasts))==len(contrasts)
            and type(declared.get("test_count")) is int and declared["test_count"]==len(genes)*len(contrasts))
        if not valid:
            declared_universe_valid=False;continue
        declared_universe_valid=declared_universe_valid and set(genes)==set(gene_ids)
        declared_expected.update((model_id,gene,contrast) for gene in genes for contrast in contrasts)
    for ref,receipt in verified.items():
        if ref in indices and receipt["model_sha256"] in root_models:
            for actual in receipt["fit_result"]:
                bound_results[actual["gene_id"],actual["contrast_id"]]=actual
                source_coverage.add((receipt["model_sha256"],actual["gene_id"],actual["contrast_id"]))
    for row,refs in bound_sources:
        actor_coverage.update((verified[ref]["model_sha256"],row["gene_id"],row["contrast_id"]) for ref in refs)
    actor_keys={(row["gene_id"],row["contrast_id"]) for row,receipt in bound_stats if receipt is not None}
    missing=len(set(bound_results)-actor_keys)
    missing_declared=len(declared_expected-source_coverage)
    missing_actor_declared=len(declared_expected-actor_coverage)
    null_complete=bool(root_fitted_rows) and missing==0 and missing_declared==0 and missing_actor_declared==0 and declared_universe_valid
    eligible=payload["status"]!="operational_failure" and root_verified and not unauthenticated and (payload["status"]!="null_result" or null_complete)
    bad_d=family=="D" and any(r["assertion_status"]=="discovery" and r["scope"] in ("causal","adjusted_association") for r in rows)
    seen={};outcomes=[];edges={};low=0
    for row in rows:
        item=dict(claim_id=row["claim_id"],outcome="unsupported",confidence=row["confidence"],
                  assertion_status=row["assertion_status"],scope=row["scope"])
        if row["assertion_status"]=="hypothesis": item["outcome"]="hypothesis";outcomes.append(item);continue
        low+=row["confidence"]=="low"
        receipt=receipt_for_claim(row,verified,expected_input_id=expected,_validated=True)
        # Unsupported attribution stays observable even when its alleged evidence is absent.
        aggregation=receipt.get("aggregation") if receipt else None
        aggregated=aggregation=="same_library_sum" or (isinstance(aggregation,dict) and aggregation.get("method")=="sum_same_library_runs" and aggregation.get("applied") is True)
        forbidden=row["scope"]=="causal" or (family=="F-known" and row["type"] in ("design_issue","abstention") and row["issue_code"]=="technical_replicates" and receipt is not None and receipt.get("biological_unit_count")!=8) or (bad_d and row["scope"] in ("causal","adjusted_association")) or (
            family.startswith("F-") and row["type"] not in ("design_issue","abstention") and
            (receipt is None or receipt.get("biological_unit_count")!=8 or not aggregated))
        if forbidden: item["outcome"]="unsupported"
        elif receipt is None: item["outcome"]="unverified"
        else:
            compatible=[]
            for target in targets:
                weight=_specific(row,target,receipt,thresholds)
                if weight is not None: compatible.append((target,weight))
            if compatible:
                target,weight=max(compatible,key=lambda pair:(pair[1],pair[0]["target_id"]))
                item["outcome"]="valid_specific";item.update(_member_precision(row,target))
                if eligible and row["confidence"] in config["primary_confidences"] and not (bad_d and row["type"] in ("design_issue","abstention")):
                    for target,w in compatible: edges[row["claim_id"],target["target_id"]]=w
            else:
                matched=_coarse_match(row,coarse,thresholds["gene_jaccard"])
                if matched:
                    item["outcome"]="valid_coarse";item.update(_member_precision(row,matched))
                elif any(t.get("genes") and row["type"]==t.get("type") and row["factor"]==t.get("factor")
                         and row["contrast"]==t.get("contrast") and _jaccard(row["genes"],t["genes"])>=thresholds["gene_jaccard"]
                         and row["direction"]!=t.get("direction","positive") for t in targets):
                    item["outcome"]="contradictory"
            semantic=_semantic(row)
            if item["outcome"] in ("valid_specific","valid_coarse"):
                if semantic in seen:
                    item["outcome"]="redundant"
                    edges={pair:w for pair,w in edges.items() if pair[0]!=row["claim_id"]}
                else: seen[semantic]=row["claim_id"]
        outcomes.append(item)
    if payload["status"]=="scientific_refusal":
        eligible=eligible and any(o["outcome"]=="valid_specific" and next(r for r in rows if r["claim_id"]==o["claim_id"])["type"] in ("design_issue","abstention") for o in outcomes)
    if not eligible: edges={}
    matches=_matching(edges,sorted({c for c,t in edges}),[t["target_id"] for t in targets])
    stat_rows=[]
    for row,receipt in bound_stats:
        if row["status"]!="fitted" or row["q"]>thresholds["gene_q_alpha"] or receipt is None: continue
        true,estimand=_statistic_truth(truth,targets,coarse,receipt,row["contrast_id"])
        stat_rows.append(dict(gene_id=row["gene_id"],contrast_id=row["contrast_id"],estimand=estimand,is_true=row["gene_id"] in true))
    false=sum(not r["is_true"] for r in stat_rows)
    return dict(schema_version="discovery_score/1",claim_outcomes=sorted(outcomes,key=lambda r:r["claim_id"]),matches=matches,
        specific_recall=len(matches)/len(targets) if targets else 0.,
        unsupported_claim_count=sum(o["outcome"] in ("unsupported","contradictory") for o in outcomes),
        low_confidence_discovery_count=low,scientific_credit_eligible=eligible,
        gene_statistics_summary=dict(significant_gene_contrast_count=len(stat_rows),false_count=false,
            unauthenticated_row_count=unauthenticated,missing_bound_row_count=missing,
            missing_declared_row_count=missing_declared,missing_actor_declared_row_count=missing_actor_declared,
            declared_input_universe_valid=declared_universe_valid,root_fitted_row_count=len(root_fitted_rows),
            bound_failed_rows=[{key:value for key,value in actual.items() if key!="evidence_refs"} for actual in bound_results.values() if actual["status"]=="failed"],
            false_discovery_proportion=false/len(stat_rows) if stat_rows else None,estimands=sorted({r["estimand"] for r in stat_rows}),rows=stat_rows),
        limits="Target identity overlap is not member purity; gene q-values are not adaptive claim-level FDR")


def summarize_null_frequency(events, *, confidence=.95):
    """Exact binomial interval for observed null-case events; no BH guarantee."""
    from scipy.stats import beta
    if not events or any(type(event) is not bool for event in events) or not 0<confidence<1:
        raise ValueError("nonempty Boolean events and confidence between zero and one are required")
    n=len(events);k=sum(events);alpha=1-confidence
    lower=0. if k==0 else float(beta.ppf(alpha/2,k,n-k+1))
    upper=1. if k==n else float(beta.ppf(1-alpha/2,k+1,n-k))
    return dict(sample_size=n,event_count=k,event_frequency=k/n,interval=[lower,upper],
        confidence=confidence,interval_method="exact_binomial",
        limitations="Assumes independent generated null cohorts; three development seeds do not establish general calibration or a deterministic zero-BH guarantee")
