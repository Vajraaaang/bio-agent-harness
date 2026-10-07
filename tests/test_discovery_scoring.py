"""Adversarial scorer controls using only hand-built truth and receipt fixtures."""
import importlib
import hashlib
import json
import pytest
from tests.test_discovery_claims import GENES, SAMPLES, claim, envelope


@pytest.fixture
def scorer():
    try:
        return importlib.import_module("benchmarks.discovery_scoring").score_claims
    except ModuleNotFoundError:
        def unavailable(*args, **kwargs):
            pytest.fail("discovery scorer has not been implemented")
        return unavailable


def receipt(**changes):
    row=dict(status="executed",input_id="input-fixture",model_id="model-fixture",
             contrast_id="treated_vs_control",result_id="result-fixture",scope="marginal_association",
             factor="condition", adjustment_factors=[],method="software-control",
             biological_unit_count=6,aggregation=None,issue_code=None,
             input_sha256="1"*64,model_sha256="2"*64,result_sha256="3"*64)
    row.update(changes)
    row.setdefault("model_record",{"kind":"statistical_model","family":"negative_binomial","method":"software-control", "design":{"columns":["Intercept","Condition"],"rank":2,"contrasts":{row["contrast_id"]:[0,1]}}})
    if "model_sha256" not in changes:
        row["model_sha256"]=hashlib.sha256(json.dumps(row["model_record"],sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    if row["scope"]=="design_diagnostic" and row["issue_code"]:
        observations={"sample_count":6,"declaration":{"status":None}}
        if row["issue_code"]=="non_identifiable_design":
            observations={"columns":["Intercept","Condition","Batch"],"matrix":[[1,0,0],[1,1,1]],"rank":2}
        if row["issue_code"]=="technical_replicates":
            observations={"biological_unit_count":row["biological_unit_count"],"run_count":24,
                          "same_library_mapping":{f"lib{i}":[f"unit{i}-run{j}" for j in range(3)] for i in range(8)}}
        row["diagnostic_result"]={"issue_code":row["issue_code"],"observations":observations}
        row["result_sha256"]=hashlib.sha256(json.dumps(row["diagnostic_result"],sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    if row["scope"]!="design_diagnostic":
        row.setdefault("fit_result",[dict(gene_id=gene,contrast_id=row["contrast_id"],status="fitted",p=.01,q=.02,effect=1.,effect_scale="log2",evidence_refs=[],reason=None) for gene in ["g1","g2"]])
        if "result_sha256" not in changes:
            row["result_sha256"]=hashlib.sha256(json.dumps(row["fit_result"],sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    return row


def truth(family="A", **changes):
    row=dict(family=family,input_id="input-fixture",signals=[dict(target_id="t1",
        type="sample_subgroup",genes=["g1","g2"],samples=["s1","s2"],
        direction="positive",factor="condition",contrast="treated_subgroup_vs_other")],decoys=[])
    row.update(changes)
    return row


def score(scorer,payload,private_truth=None,receipts=None):
    return scorer(payload,truth() if private_truth is None else private_truth,
                  gene_ids=GENES,sample_ids=SAMPLES,
                  evidence_lookup={"r1":receipt()} if receipts is None else receipts)


def outcomes(result):
    return {x["claim_id"]:x["outcome"] for x in result["claim_outcomes"]}


def test_a_subgroup_requires_both_gene_and_sample_overlap(scorer):
    gene_only=score(scorer,envelope())
    assert gene_only["specific_recall"] == 0
    assert outcomes(gene_only)["c1"] == "valid_coarse"
    specific=claim(type="sample_subgroup",scope="subgroup_association",contrast="treated_subgroup_vs_other",samples=["s1","s2"])
    result=score(scorer,envelope([specific]),receipts={"r1":receipt(scope="subgroup_association",contrast_id="treated_subgroup_vs_other")})
    assert result["specific_recall"] == 1
    assert outcomes(result)["c1"] == "valid_specific"


def test_wrong_samples_and_omnibus_gene_claim_do_not_recover_a(scorer):
    for changes in [dict(samples=["s3","s4"]),dict(genes=GENES)]:
        row=claim(type="sample_subgroup",scope="subgroup_association",contrast="treated_subgroup_vs_other",samples=["s1","s2"])
        row.update(changes)
        assert score(scorer,envelope([row]),receipts={"r1":receipt(scope="subgroup_association",contrast_id="treated_subgroup_vs_other")})["specific_recall"]==0


def test_evidence_strings_or_wrong_input_never_win_credit(scorer):
    row=claim(type="sample_subgroup",scope="subgroup_association",contrast="treated_subgroup_vs_other",samples=["s1","s2"])
    for lookup in [{},{"r1":"executed interaction"},{"r1":receipt(input_id="wrong")}]:
        result=score(scorer,envelope([row]),receipts=lookup)
        assert outcomes(result)["c1"]=="unverified"
        assert result["specific_recall"]==0


def test_b_requires_factor_contrast_both_directions_and_interaction_receipt(scorer):
    target=dict(target_id="t1",type="interaction",genes=["g1","g2"],samples=[],factor="sex",
        contrast="male_minus_female",direction="mixed",effect_direction_by_level={"female":"positive","male":"negative"})
    private=truth("B",signals=[target])
    row=claim(type="interaction",scope="interaction",direction="mixed",factor="sex",
        contrast="male_minus_female",effect_direction_by_level=target["effect_direction_by_level"])
    good={"r1":receipt(scope="interaction",factor="sex",contrast_id="male_minus_female")}
    assert score(scorer,envelope([row]),private,good)["specific_recall"]==1
    for change in [dict(factor="condition"),dict(contrast="treated_vs_control"),
                   dict(effect_direction_by_level={"female":"positive","male":"positive"})]:
        bad=dict(row);bad.update(change)
        assert score(scorer,envelope([bad]),private,good)["specific_recall"]==0
    assert score(scorer,envelope([row]),private,{"r1":receipt()})["specific_recall"]==0


def test_c_adjustment_cannot_be_inferred_from_claim_wording(scorer):
    target=dict(target_id="t1",type="gene_set_effect",genes=["g1","g2"],samples=[],factor="condition",
                contrast="treated_vs_control",direction="positive",adjustment_factors=["batch"])
    private=truth("C",signals=[target],decoys=[dict(type="batch_effect",genes=["g3","g4"])])
    row=claim(scope="adjusted_association")
    good={"r1":receipt(scope="adjusted_association",adjustment_factors=["batch"])}
    assert score(scorer,envelope([row]),private,good)["specific_recall"]==1
    assert score(scorer,envelope([row]),private)["specific_recall"]==0
    coarse=claim(genes=["g3","g4"])
    assert outcomes(score(scorer,envelope([coarse]),private))["c1"]=="valid_coarse"
    adjusted=claim(genes=["g3","g4"],scope="adjusted_association")
    assert outcomes(score(scorer,envelope([adjusted]),private,good))["c1"]=="unsupported"


def test_d_low_confidence_causal_discovery_blocks_abstention_credit(scorer):
    diagnostic=claim(type="abstention",scope="design_diagnostic",direction="not_applicable",
        genes=[],factor=None,contrast=None,issue_code="non_identifiable_design")
    causal=claim(claim_id="c2",scope="causal",confidence="low")
    private=truth("D",signals=[dict(target_id="t1",type="abstention",issue_code="non_identifiable_design")],
                  decoys=[dict(type="confounded_split",genes=["g1","g2"])])
    look={"r1":receipt(scope="design_diagnostic",issue_code="non_identifiable_design")}
    result=score(scorer,envelope([diagnostic,causal]),private,look)
    assert result["specific_recall"]==0
    assert outcomes(result)["c2"]=="unsupported"
    causal["assertion_status"]="hypothesis"
    result=score(scorer,envelope([diagnostic,causal]),private,look)
    assert result["specific_recall"]==1
    assert outcomes(result)["c2"]=="hypothesis"


def test_duplicates_redundant_and_contradictions_retained(scorer):
    specific=claim(type="sample_subgroup",scope="subgroup_association",contrast="treated_subgroup_vs_other",samples=["s1","s2"])
    duplicate=dict(specific,claim_id="c2")
    opposite=dict(specific,claim_id="c3",direction="negative")
    result=score(scorer,envelope([specific,duplicate,opposite]),receipts={"r1":receipt(scope="subgroup_association",contrast_id="treated_subgroup_vs_other")})
    assert result["specific_recall"]==1
    assert list(outcomes(result).values()).count("redundant")==1
    assert outcomes(result)["c3"]=="contradictory"


def test_matching_maximizes_cardinality_and_is_order_independent(scorer):
    targets=[dict(target_id="t1",type="gene_set_effect",genes=["g1","g2"],samples=[],
                  factor="condition",contrast="treated_vs_control",direction="positive"),
             dict(target_id="t2",type="gene_set_effect",genes=["g2","g3"],samples=[],
                  factor="condition",contrast="treated_vs_control",direction="positive")]
    rows=[claim(claim_id="broad",genes=["g1","g2","g3"]),claim(claim_id="narrow",genes=["g1"])]
    private=truth("control",signals=targets)
    first=score(scorer,envelope(rows),private)
    second=score(scorer,envelope(list(reversed(rows))),dict(private,signals=list(reversed(targets))))
    assert first["specific_recall"]==1
    assert first["matches"]==second["matches"]
    assert {(x["claim_id"],x["target_id"]) for x in first["matches"]}=={("narrow","t1"),("broad","t2")}


def test_null_gene_fdp_and_claim_error_are_separate(scorer):
    stat=dict(gene_id="g1",contrast_id="treated_vs_control",status="fitted",p=.01,q=.02,
              effect=1.,effect_scale="log2",evidence_refs=["r1"],reason=None)
    result=score(scorer,envelope(gene_statistics=[stat]),truth("E",signals=[]))
    assert outcomes(result)["c1"]=="unsupported"
    assert result["gene_statistics_summary"]["false_discovery_proportion"]==1
    assert result["unsupported_claim_count"]==1


def test_f_known_aggregation_and_unknown_missing_provenance(scorer):
    for family,issue in [("F-known","technical_replicates"),("F-unknown","missing_independence_provenance")]:
        private=truth(family,signals=[dict(target_id="t1",type="design_issue",issue_code=issue)],
                      biological_unit_count=8)
        row=claim(type="abstention",scope="design_diagnostic",direction="not_applicable",
                  genes=[],factor=None,contrast=None,issue_code=issue)
        result=score(scorer,envelope([row],status="scientific_refusal"),private,
                     {"r1":receipt(scope="design_diagnostic",biological_unit_count=8,issue_code=issue)})
        assert result["specific_recall"]==1


def test_operational_failure_does_not_earn_scientific_credit(scorer):
    result=score(scorer,envelope([],status="operational_failure",error={"code":"timeout","message":"run failed"}))
    assert result["specific_recall"]==0
    assert result["scientific_credit_eligible"] is False


def test_d_coarse_split_association_is_valid_without_causal_attribution(scorer):
    private=truth("D",signals=[dict(target_id="t1",type="abstention",issue_code="non_identifiable_design")],
                  decoys=[dict(type="confounded_split",genes=["g1","g2"])])
    result=score(scorer,envelope(),private)
    assert outcomes(result)["c1"]=="valid_coarse"
    assert result["specific_recall"]==0


def test_c_actual_batch_association_is_valid_coarse(scorer):
    private=truth("C",signals=[dict(target_id="t1",type="gene_set_effect",genes=["g1","g2"],
        direction="positive",factor="condition",contrast="treated_vs_control",adjustment_factors=["batch"])],
        decoys=[dict(type="batch_effect",genes=["g3","g4"])])
    row=claim(genes=["g3","g4"],factor="batch",contrast="batch_2_vs_batch_1")
    result=score(scorer,envelope([row]),private,{"r1":receipt(factor="batch",contrast_id="batch_2_vs_batch_1")})
    assert outcomes(result)["c1"]=="valid_coarse"
    assert result["unsupported_claim_count"]==0


def test_receipts_need_all_model_contrast_result_bindings(scorer):
    for changes in [dict(model_id=""),dict(result_id=""),dict(status="planned"),dict(contrast_id="wrong")]:
        result=score(scorer,envelope(),receipts={"r1":receipt(**changes)})
        assert outcomes(result)["c1"]=="unverified"


def test_known_f_run_level_inference_is_not_validated_biological_credit(scorer):
    private=truth("F-known",signals=[dict(target_id="t1",type="design_issue",issue_code="technical_replicates")],
                  biological_unit_count=8)
    row=claim()
    result=score(scorer,envelope([row]),private,{"r1":receipt(biological_unit_count=24)})
    assert result["specific_recall"]==0
    assert outcomes(result)["c1"]=="unsupported"


def test_empty_null_requires_hash_bound_executed_root_receipt(scorer):
    stats=[dict(gene_id=gene,contrast_id="treated_vs_control",status="fitted",p=.5,q=.5,effect=0.,effect_scale="log2",evidence_refs=["r1"],reason=None) for gene in GENES]
    model={"kind":"statistical_model","family":"negative_binomial","method":"software-control",
        "design":{"columns":["Intercept","Condition"],"rank":2},
        "hypothesis_family":{"gene_ids":GENES,"contrast_ids":["treated_vs_control"],"test_count":len(GENES)}}
    payload=envelope([],status="null_result",gene_statistics=stats)
    for lookup in [{},{"r1":receipt(input_sha256="not-a-hash",fit_result=stats,model_record=model)},{"r1":receipt(model_sha256="",fit_result=stats,model_record=model)}]:
        result=score(scorer,payload,truth("E",signals=[]),lookup)
        assert result["scientific_credit_eligible"] is False
    assert score(scorer,payload,truth("E",signals=[]),{"r1":receipt(fit_result=stats,model_record=model)})["scientific_credit_eligible"] is True


def test_unrelated_diagnostic_receipt_cannot_authenticate_refusal(scorer):
    private=truth("D",signals=[dict(target_id="t1",type="abstention",issue_code="non_identifiable_design")])
    row=claim(type="abstention",scope="design_diagnostic",direction="not_applicable",
              genes=[],factor=None,contrast=None,issue_code="non_identifiable_design")
    result=score(scorer,envelope([row],status="scientific_refusal"),private,
                 {"r1":receipt(scope="design_diagnostic",issue_code="fit_failure")})
    assert result["scientific_credit_eligible"] is False
    assert result["specific_recall"]==0


def test_low_asserted_discovery_retained_but_not_primary_recall(scorer):
    row=claim(type="sample_subgroup",scope="subgroup_association",contrast="treated_subgroup_vs_other",
              samples=["s1","s2"],confidence="low")
    result=score(scorer,envelope([row]),receipts={"r1":receipt(scope="subgroup_association",contrast_id="treated_subgroup_vs_other")})
    assert result["specific_recall"]==0
    assert outcomes(result)["c1"]=="valid_specific"
    assert result["low_confidence_discovery_count"]==1


def test_validity_is_independent_of_one_to_one_recovery(scorer):
    specific=claim(type="sample_subgroup",scope="subgroup_association",contrast="treated_subgroup_vs_other",samples=["s1","s2"])
    coarse=claim(claim_id="c2",evidence_refs=["r2"])
    result=score(scorer,envelope([specific,coarse]),receipts={"r1":receipt(scope="subgroup_association",contrast_id="treated_subgroup_vs_other"),"r2":receipt()})
    assert result["specific_recall"]==1
    assert outcomes(result)["c2"]=="valid_coarse"


def test_jaccard_identity_does_not_certify_member_precision(scorer):
    row=claim(genes=["g1","g2","g3","g4"])
    result=score(scorer,envelope([row]),truth("control",signals=[dict(target_id="t1",type="gene_set_effect",
        genes=["g1","g2"],factor="condition",contrast="treated_vs_control",direction="positive")]))
    assert result["specific_recall"]==1
    assert result["claim_outcomes"][0]["member_precision"]==.5


def test_c_marginal_gene_fdp_has_different_truth_than_adjusted_target(scorer):
    private=truth("C",signals=[dict(target_id="t1",type="gene_set_effect",genes=["g1","g2"],factor="condition",
        contrast="treated_vs_control",direction="positive",adjustment_factors=["batch"])],
        decoys=[dict(type="batch_effect",genes=["g3","g4"])])
    stat=dict(gene_id="g3",contrast_id="treated_vs_control",status="fitted",p=.01,q=.02,effect=1.,
              effect_scale="log2",evidence_refs=["r1"],reason=None)
    marginal=score(scorer,envelope(gene_statistics=[stat]),private,{"r1":receipt(fit_result=[stat])})
    assert marginal["gene_statistics_summary"]["false_discovery_proportion"]==0
    adjusted=score(scorer,envelope(gene_statistics=[stat]),private,
                   {"r1":receipt(scope="adjusted_association",adjustment_factors=["batch"],fit_result=[stat])})
    assert adjusted["gene_statistics_summary"]["false_discovery_proportion"]==1


def test_actual_batch_effect_remains_valid_after_joint_adjustment(scorer):
    private=truth("C",signals=[dict(target_id="t1",type="gene_set_effect",genes=["g1","g2"],factor="condition",
        contrast="treated_vs_control",direction="positive",adjustment_factors=["batch"])],
        decoys=[dict(type="batch_effect",genes=["g3","g4"])])
    row=claim(genes=["g3","g4"],factor="batch",contrast="batch_2_vs_batch_1",scope="adjusted_association")
    stat=dict(gene_id="g3",contrast_id="batch_2_vs_batch_1",status="fitted",p=.01,q=.02,effect=1.,
              effect_scale="log2",evidence_refs=["r1"],reason=None)
    result=score(scorer,envelope([row],gene_statistics=[stat]),private,
        {"r1":receipt(scope="adjusted_association",factor="batch",contrast_id="batch_2_vs_batch_1",adjustment_factors=["condition"],fit_result=[stat])})
    assert outcomes(result)["c1"]=="valid_coarse"
    assert result["gene_statistics_summary"]["false_discovery_proportion"]==0


def test_null_cannot_borrow_unrelated_failure_diagnostic_as_executed_fit(scorer):
    result=score(scorer,envelope([],status="null_result"),truth("E",signals=[]),
        {"r1":receipt(scope="design_diagnostic",issue_code="fit_failure")})
    assert result["scientific_credit_eligible"] is False


def test_known_f_diagnostic_with_wrong_unit_count_is_not_qualified(scorer):
    private=truth("F-known",signals=[dict(target_id="t1",type="design_issue",issue_code="technical_replicates")],biological_unit_count=8)
    row=claim(type="abstention",scope="design_diagnostic",direction="not_applicable",genes=[],factor=None,
              contrast=None,issue_code="technical_replicates")
    result=score(scorer,envelope([row],status="scientific_refusal"),private,
        {"r1":receipt(scope="design_diagnostic",issue_code="technical_replicates",biological_unit_count=24)})
    assert result["specific_recall"]==0
    assert result["scientific_credit_eligible"] is False


def test_repeated_null_frequency_reports_uncertainty_not_zero_error_guarantee():
    import benchmarks.discovery_scoring as module
    assert hasattr(module,"summarize_null_frequency"), "null-frequency uncertainty summary is missing"
    result=module.summarize_null_frequency([False,False,False])
    assert result["event_frequency"]==0
    assert result["interval"][1]>0
    assert result["sample_size"]==3
    assert "independent" in result["limitations"]


def test_low_confidence_duplicate_cannot_displace_primary_discovery(scorer):
    high=claim(claim_id="z-high",type="sample_subgroup",scope="subgroup_association",
        contrast="treated_subgroup_vs_other",samples=["s1","s2"])
    low=dict(high,claim_id="a-low",confidence="low")
    result=score(scorer,envelope([low,high]),receipts={"r1":receipt(scope="subgroup_association",contrast_id="treated_subgroup_vs_other")})
    assert result["specific_recall"]==1
    assert outcomes(result)["z-high"]=="valid_specific"


def test_forty_true_plus_forty_false_genes_retain_member_impurity(scorer):
    genes=[f"x{i}" for i in range(80)]
    private=truth("control",signals=[dict(target_id="t1",type="gene_set_effect",genes=genes[:40],
        factor="condition",contrast="treated_vs_control",direction="positive")])
    result=scorer(envelope([claim(genes=genes)]),private,gene_ids=genes,sample_ids=SAMPLES,evidence_lookup={"r1":receipt()})
    assert result["specific_recall"]==1
    row=result["claim_outcomes"][0]
    assert row["member_true_count"]==row["member_false_count"]==40
    assert row["member_precision"]==.5


def test_b_valid_single_stratum_effect_gets_no_crossover_recall(scorer):
    target=dict(target_id="t1",type="interaction",genes=["g1","g2"],factor="sex",
        contrast="male_minus_female",direction="mixed",effect_direction_by_level={"female":"positive","male":"negative"})
    row=claim(contrast="female_treated_vs_control",scope="adjusted_association")
    result=score(scorer,envelope([row]),truth("B",signals=[target]),
        {"r1":receipt(contrast_id="female_treated_vs_control",scope="adjusted_association",adjustment_factors=["sex"])})
    assert outcomes(result)["c1"]=="valid_coarse"
    assert result["specific_recall"]==0


def test_observational_gene_matching_cannot_authenticate_causal_claim(scorer):
    private=truth("control",signals=[dict(target_id="t1",type="gene_set_effect",genes=["g1","g2"],factor="condition",contrast="treated_vs_control",direction="positive")])
    result=score(scorer,envelope([claim(scope="causal")]),private,{"r1":receipt(scope="causal")})
    assert outcomes(result)["c1"]=="unsupported"
    assert result["specific_recall"]==0


def test_bound_diagnostic_cannot_fake_rank_deficiency(scorer):
    row=claim(type="abstention",scope="design_diagnostic",direction="not_applicable",genes=[],factor=None,
              contrast=None,issue_code="non_identifiable_design")
    private=truth("D",signals=[dict(target_id="t1",type="abstention",issue_code="non_identifiable_design")])
    fake=receipt(scope="design_diagnostic",issue_code="non_identifiable_design")
    fake["diagnostic_result"]["observations"]["matrix"]=[[1,0,0],[1,1,0],[1,0,1]]
    fake["diagnostic_result"]["observations"]["rank"]=3
    fake["result_sha256"]=hashlib.sha256(json.dumps(fake["diagnostic_result"],sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    result=score(scorer,envelope([row],status="scientific_refusal"),private,{"r1":fake})
    assert result["scientific_credit_eligible"] is False
    assert result["specific_recall"]==0


def test_diagnostic_digest_mismatch_cannot_authenticate_refusal(scorer):
    row=claim(type="abstention",scope="design_diagnostic",direction="not_applicable",genes=[],factor=None,
              contrast=None,issue_code="non_identifiable_design")
    private=truth("D",signals=[dict(target_id="t1",type="abstention",issue_code="non_identifiable_design")])
    fake=receipt(scope="design_diagnostic",issue_code="non_identifiable_design")
    fake["diagnostic_result"]["observations"]["rank"]=0
    result=score(scorer,envelope([row],status="scientific_refusal"),private,{"r1":fake})
    assert result["scientific_credit_eligible"] is False


def test_all_unavailable_gene_fits_are_not_successful_null_evidence(scorer):
    stat=dict(gene_id="g1",contrast_id="treated_vs_control",status="failed",p=None,q=None,effect=None,
              effect_scale="log2",evidence_refs=["r1"],reason="fit unavailable")
    result=score(scorer,envelope([],status="null_result",gene_statistics=[stat]),truth("E",signals=[]))
    assert result["scientific_credit_eligible"] is False


def test_actor_cannot_turn_authenticated_failed_fit_into_successful_null(scorer):
    failed=dict(gene_id="g1",contrast_id="treated_vs_control",status="failed",p=None,q=None,effect=None,
        effect_scale="log2",evidence_refs=[],reason="nonconvergence")
    forged=dict(failed,status="fitted",p=.5,q=.5,effect=0.,reason=None,evidence_refs=["r1"])
    record=receipt(fit_result=[failed])
    result=score(scorer,envelope([],status="null_result",gene_statistics=[forged]),truth("E",signals=[]),{"r1":record})
    assert result["scientific_credit_eligible"] is False
    assert result["gene_statistics_summary"]["unauthenticated_row_count"]==1


def test_actor_cannot_change_authenticated_gene_statistics(scorer):
    actual=dict(gene_id="g1",contrast_id="treated_vs_control",status="fitted",p=.5,q=.5,effect=0.,
        effect_scale="log2",evidence_refs=[],reason=None)
    forged=dict(actual,p=.01,q=.02,effect=5.,evidence_refs=["r1"])
    result=score(scorer,envelope(gene_statistics=[forged]),truth("E",signals=[]),{"r1":receipt(fit_result=[actual])})
    assert result["scientific_credit_eligible"] is False
    assert result["gene_statistics_summary"]["significant_gene_contrast_count"]==0


def test_model_and_fit_content_hashes_are_independently_recomputed(scorer):
    for key in ["model_record","fit_result"]:
        record=receipt()
        record[key]={"forged":"content"} if key=="model_record" else []
        result=score(scorer,envelope(),receipts={"r1":record})
        assert result["specific_recall"]==0
        assert outcomes(result)["c1"]=="unverified"


def test_null_requires_complete_bound_fit_coverage_including_failures(scorer):
    fitted=dict(gene_id="g1",contrast_id="treated_vs_control",status="fitted",p=.5,q=.5,effect=0.,
        effect_scale="log2",evidence_refs=[],reason=None)
    failed=dict(fitted,gene_id="g2",status="failed",p=None,q=None,effect=None,reason="failed estimate")
    result=score(scorer,envelope([],status="null_result",gene_statistics=[dict(fitted,evidence_refs=["r1"])]),
        truth("E",signals=[]),{"r1":receipt(fit_result=[fitted,failed])})
    assert result["scientific_credit_eligible"] is False
    assert result["gene_statistics_summary"]["missing_bound_row_count"]==1
    assert result["gene_statistics_summary"]["bound_failed_rows"][0]["gene_id"]=="g2"


def test_declared_full_panel_cannot_be_truncated_in_result_content(scorer):
    record=receipt()
    record["model_record"]["hypothesis_family"]={"gene_ids":["g1","g2","g3"],"contrast_ids":["treated_vs_control"],"test_count":3}
    record["model_sha256"]=hashlib.sha256(json.dumps(record["model_record"],sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    result=score(scorer,envelope(),receipts={"r1":record})
    assert outcomes(result)["c1"]=="unverified"


def _declared_null_model_receipts(*, include_batch=False):
    model={"kind":"statistical_model","family":"negative_binomial","method":"software-control",
           "design":{"columns":["Intercept","Condition","Batch"],"rank":3},
           "hypothesis_family":{"gene_ids":["g1","g2"],"contrast_ids":["treated_vs_control","batch_2_vs_batch_1"],"test_count":4}}
    result={};actor=[]
    for ref,contrast in [("r1","treated_vs_control"),("r2","batch_2_vs_batch_1")]:
        if ref=="r2" and not include_batch: continue
        rows=[dict(gene_id=gene,contrast_id=contrast,status="fitted",p=.5,q=.5,effect=0.,effect_scale="log2",evidence_refs=[],reason=None) for gene in ["g1","g2"]]
        result[ref]=receipt(contrast_id=contrast,model_record=model,fit_result=rows,factor="batch" if ref=="r2" else "condition")
        actor.extend([dict(row,evidence_refs=[ref]) for row in rows])
    return result,actor


def test_null_cannot_omit_entire_declared_contrast(scorer):
    lookup,rows=_declared_null_model_receipts()
    result=scorer(envelope([],status="null_result",gene_statistics=rows),truth("E",signals=[]),
                  gene_ids=["g1","g2"],sample_ids=SAMPLES,evidence_lookup=lookup)
    assert result["scientific_credit_eligible"] is False
    assert result["gene_statistics_summary"]["missing_declared_row_count"]==2


def test_null_complete_cartesian_coverage_is_eligible(scorer):
    lookup,rows=_declared_null_model_receipts(include_batch=True)
    result=scorer(envelope([],status="null_result",gene_statistics=rows,execution_evidence_refs=["r1","r2"]),truth("E",signals=[]),
                  gene_ids=["g1","g2"],sample_ids=SAMPLES,evidence_lookup=lookup)
    assert result["scientific_credit_eligible"] is True
    assert result["gene_statistics_summary"]["missing_declared_row_count"]==0


def test_null_declared_gene_universe_must_equal_input_universe(scorer):
    lookup,rows=_declared_null_model_receipts(include_batch=True)
    result=scorer(envelope([],status="null_result",gene_statistics=rows,execution_evidence_refs=["r1","r2"]),truth("E",signals=[]),
                  gene_ids=["g1","g2","g3"],sample_ids=SAMPLES,evidence_lookup=lookup)
    assert result["scientific_credit_eligible"] is False
    assert result["gene_statistics_summary"]["declared_input_universe_valid"] is False


def test_nonroot_fitted_model_cannot_rescue_all_failed_root_null(scorer):
    genes=["g1","g2"]
    def model(contrast):
        return {"kind":"statistical_model","family":"negative_binomial","method":"software-control",
                "design":{"columns":["Intercept","Condition"],"rank":2},
                "hypothesis_family":{"gene_ids":genes,"contrast_ids":[contrast],"test_count":2}}
    failed=[dict(gene_id=gene,contrast_id="treated_vs_control",status="failed",p=None,q=None,effect=None,effect_scale="log2",evidence_refs=[],reason="unavailable") for gene in genes]
    fitted=[dict(gene_id=gene,contrast_id="batch_2_vs_batch_1",status="fitted",p=.5,q=.5,effect=0.,effect_scale="log2",evidence_refs=[],reason=None) for gene in genes]
    lookup={"r1":receipt(model_record=model("treated_vs_control"),fit_result=failed),
            "r2":receipt(model_record=model("batch_2_vs_batch_1"),contrast_id="batch_2_vs_batch_1",factor="batch",fit_result=fitted)}
    rows=[dict(row,evidence_refs=["r1"]) for row in failed]+[dict(row,evidence_refs=["r2"]) for row in fitted]
    result=scorer(envelope([],status="null_result",gene_statistics=rows,execution_evidence_refs=["r1"]),truth("E",signals=[]),gene_ids=genes,sample_ids=SAMPLES,evidence_lookup=lookup)
    assert result["scientific_credit_eligible"] is False
    assert result["gene_statistics_summary"]["root_fitted_row_count"]==0


def test_each_receipt_is_fully_validated_once_per_score_call(scorer,monkeypatch):
    import benchmarks.discovery_claims as claims_module
    import benchmarks.discovery_scoring as scoring_module
    calls=[];real=claims_module.valid_receipt
    def count(record,**kwargs):
        calls.append(record)
        return real(record,**kwargs)
    monkeypatch.setattr(claims_module,"valid_receipt",count)
    monkeypatch.setattr(scoring_module,"valid_receipt",count)
    genes=[f"x{i}" for i in range(100)]
    rows=[dict(gene_id=gene,contrast_id="treated_vs_control",status="fitted",p=.5,q=.5,effect=0.,effect_scale="log2",evidence_refs=[],reason=None) for gene in genes]
    model={"kind":"statistical_model","family":"negative_binomial","method":"software-control",
           "design":{"columns":["Intercept","Condition"],"rank":2},
           "hypothesis_family":{"gene_ids":genes,"contrast_ids":["treated_vs_control"],"test_count":len(genes)}}
    record=receipt(model_record=model,fit_result=rows)
    actor=[dict(row,evidence_refs=["r1"]) for row in rows]
    result=scorer(envelope([],status="null_result",gene_statistics=actor),truth("E",signals=[]),gene_ids=genes,sample_ids=SAMPLES,evidence_lookup={"r1":record})
    assert result["scientific_credit_eligible"] is True
    assert len(calls)==1


def test_malformed_contrast_list_is_rejected_without_exception():
    from benchmarks.discovery_claims import valid_receipt
    record=receipt()
    record["model_record"]["hypothesis_family"]={"gene_ids":["g1","g2"],"contrast_ids":[{}],"test_count":2}
    record["model_sha256"]=hashlib.sha256(json.dumps(record["model_record"],sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    assert valid_receipt(record) is False


def malformed_technical_receipt():
    record=receipt(scope="design_diagnostic",issue_code="technical_replicates",biological_unit_count=8)
    record["diagnostic_result"]["observations"].update(run_count=16,
        same_library_mapping={f"lib{i}":[f"s{i}a",f"s{i}b"] for i in range(8)})
    record["diagnostic_result"]["observations"]["same_library_mapping"]["lib0"][1]={"sample":"s0b"}
    record["result_sha256"]=hashlib.sha256(json.dumps(record["diagnostic_result"],sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    return record


def test_digest_bound_non_scalar_sample_identifier_returns_false():
    from benchmarks.discovery_claims import valid_receipt
    assert valid_receipt(malformed_technical_receipt()) is False


def test_malformed_technical_mapping_cannot_crash_score_index(scorer):
    row=claim(type="abstention",scope="design_diagnostic",direction="not_applicable",genes=[],factor=None,
              contrast=None,issue_code="technical_replicates")
    result=score(scorer,envelope([row],status="scientific_refusal"),
        truth("F-known",signals=[dict(target_id="t1",type="design_issue",issue_code="technical_replicates")],biological_unit_count=8),
        {"r1":malformed_technical_receipt()})
    assert result["scientific_credit_eligible"] is False
