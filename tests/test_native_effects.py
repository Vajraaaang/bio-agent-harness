"""Contract/arithmetic negative controls, not native or clinical measurements."""
from copy import deepcopy
import math

import numpy as np
import pytest
from scipy.stats import norm

from devharness_controlled.native_contracts import NativeEffectPlan, parse_native_plan_bytes
from devharness_controlled.native_effects import METHOD, effect_design, validate_effect_result, verify_preserved_effect
from devharness_controlled.native_executor import native_request
from devharness_controlled.native_results import validate_native_result
from devharness_controlled.qualification import synthetic_approvals
from devharness_controlled.trust import verify_plan_approvals
from tests.test_native_results import analysis_plan, fitted_result


def plan(**changes):
    base = analysis_plan("deseq2_lrt").model_dump()
    return NativeEffectPlan(**{**base, "schema_version": "controlled_native_analysis/2", "effect_name": "later-vs-earlier",
        "effect_reference_sample_id": "S0", "effect_comparison_sample_id": "S4", "effect_confidence_level": .95, **changes})


def fixture():
    p = plan()
    x = np.array([[1,0,0,0],[1,0,0,1],[1,1,0,0],[1,1,0,1],[1,0,1,0],[1,0,1,1]], dtype=float)
    design = effect_design(p, x, ["Intercept","Time1","Time2","Batch"], [0,0,1,1,2,2], np.full((8,6),50))
    _, primary = fitted_result("deseq2_lrt")
    effects = {"schema_version":"native_deseq2_effects/1", "status":"ok", "error":None, "design":design,
               "method":{**METHOD,"beta_prior_variance_log2":[1e6]*4,"primary_state_unchanged":True},
               "genes":{},"conditions":[],"conditions_truncated":False}
    for i,name in enumerate(p.hypothesis_genes):
        beta = np.array([4., .2, .5+i*.1, .1])
        c = np.array(design["contrast"])
        mu = 1.3*np.exp(x@(math.log(2)*beta))
        w = mu/(1+.2*mu)
        b = x.T@(w[:,None]*x)
        inverse = np.linalg.inv(b+np.eye(4)/(math.log(2)**2*1e6))
        se = math.sqrt(float(c@inverse@b@inverse@c))/math.log(2)
        estimate = float(c@beta); z = float(norm.ppf(.975))
        effects["genes"][name] = {"status":"ok","failure_code":None,"native_log2_fold_change":estimate,
            "native_standard_error_log2":se,"native_zero_convention":False,"coefficients_log2":beta.tolist(),
            "log2_fold_change":estimate,"standard_error_log2":se,"ci_lower_log2":estimate-z*se,"ci_upper_log2":estimate+z*se}
    value = {"schema_version":"native_nb_effect_result/1","status":"ok","error":None,
             "clinical_release_allowed":False,"qualification_status":"candidate_unqualified","primary":primary,"effects":effects,
             "elapsed_seconds":1.,"memory_peak_mb":None,"memory_measurement_scope":"external_supervisor_required"}
    return p,value,x,design


def test_new_plan_preserves_signature_binding_and_primary_plan_meaning():
    p = plan(); parsed = parse_native_plan_bytes(p.canonical_bytes())
    assert isinstance(parsed,NativeEffectPlan) and parsed == p
    assert p.primary_plan().schema_version == "controlled_native_analysis/1"
    approvals,signers = synthetic_approvals(p)
    assert len(verify_plan_approvals(p,approvals,signers)) == 2
    for key,value in (("effect_comparison_sample_id","S2"),("effect_confidence_level",.99),("effect_name","another")):
        with pytest.raises(ValueError,match="does not bind"):
            verify_plan_approvals(plan(**{key:value}),approvals,signers)
    with pytest.raises(ValueError): verify_plan_approvals(p.primary_plan(),approvals,signers)
    assert native_request(p)["schema_version"] == "native_nb_request/2"


@pytest.mark.parametrize("changes", [{"engine":"edger_qlf","deseq2_fit_type":None},
    {"effect_confidence_level":0.},{"effect_confidence_level":1.},{"effect_confidence_level":True},
    {"effect_confidence_level":float('nan')},{"effect_reference_sample_id":"unknown"},
    {"effect_comparison_sample_id":"S0"}])
def test_invalid_effect_plans_are_rejected(changes):
    with pytest.raises(ValueError): plan(**changes)


def test_valid_effects_preserve_primary_and_reconstruct_covariance():
    p,value,x,design = fixture(); before=deepcopy(value)
    assert validate_native_result(p,value)["usable"]
    verify_preserved_effect(p,value,design,x)
    assert value == before
    assert any("joint temporal" in limit for limit in validate_effect_result(p,value)["qualification_limits"])


@pytest.mark.parametrize("mode", ["level","reference","native_name","column_order","primary_p","primary_q","refit",
    "prior","zero_override","interval","se","coefficient","extra_endpoint_p","clinical","primary_changed","lost_gene"])
def test_semantic_changes_are_not_accepted(mode):
    p,value,x,design=fixture(); e=value["effects"];g=e["genes"][p.hypothesis_genes[0]]
    if mode=="level": e["design"]["confidence_level"] = .99
    if mode=="reference": e["design"]["reference_sample_id"] = "S1"
    if mode=="native_name": e["design"]["native_coefficient_names"][1]="changed"
    if mode=="column_order": e["design"]["design_columns"].reverse()
    if mode=="primary_p": value["primary"]["genes"][p.hypothesis_genes[0]]["p_value"] = .2
    if mode=="primary_q": value["primary"]["genes"][p.hypothesis_genes[0]]["q_value"] = .99
    if mode=="refit": e["method"]["coefficient_refit"] = True
    if mode=="prior": e["method"]["beta_prior_variance_log2"] = [1e9]*4
    if mode=="zero_override": g["native_zero_convention"] = True
    if mode=="interval": g["ci_lower_log2"] -= .1
    if mode=="se": g["standard_error_log2"] = g["native_standard_error_log2"] = -1
    if mode=="coefficient": g["coefficients_log2"][2] += .3
    if mode=="extra_endpoint_p": g["p_value"] = .01
    if mode=="clinical": value["clinical_release_allowed"] = True
    if mode=="primary_changed": e["method"]["primary_state_unchanged"] = False
    if mode=="lost_gene": e["genes"].pop(p.hypothesis_genes[-1])
    with pytest.raises(ValueError): validate_native_result(p,value)


def test_internally_consistent_wrong_se_is_rejected_against_preserved_design():
    p,value,x,design=fixture();g=value["effects"]["genes"][p.hypothesis_genes[0]]
    se=g["standard_error_log2"]*1.2;g["standard_error_log2"]=g["native_standard_error_log2"]=se
    g["ci_lower_log2"]=g["log2_fold_change"]-float(norm.ppf(.975))*se
    g["ci_upper_log2"]=g["log2_fold_change"]+float(norm.ppf(.975))*se
    assert validate_effect_result(p,value)["usable"]
    with pytest.raises(ValueError,match="covariance"):verify_preserved_effect(p,value,design,x)


def test_nuisance_change_and_reverse_time_cannot_be_endpoint_contrasts():
    p,value,x,design=fixture()
    with pytest.raises(ValueError,match="nuisance"):
        effect_design(plan(effect_comparison_sample_id="S5"),x,design["design_columns"],[0,0,1,1,2,2],np.full((8,6),50))
    with pytest.raises(ValueError,match="later"):
        effect_design(plan(effect_reference_sample_id="S4",effect_comparison_sample_id="S0"),x,design["design_columns"],[0,0,1,1,2,2],np.full((8,6),50))


def test_effect_failure_keeps_primary_p_and_q_available():
    p,value,_,_=fixture();primary=deepcopy(value["primary"])
    value["status"]="partial_failure";value["error"]={"code":"effect_incomplete"}
    value["effects"].update(status="failed",error={"code":"native_effect_error","message":"retained failure"},genes=None)
    assert not validate_effect_result(p,value)["usable"]
    assert value["primary"]==primary
