import math

import pytest


def test_two_cohort_binary_design_and_pairing_assumptions():
    from devbench.study.study_precision import binary_design
    result = binary_design(2)
    assert result['difference_grid'] == [-1., -.5, 0., .5, 1.]
    assert result['minimum_two_sided_exact_p'] == .5
    assert result['iid_half_success_perfect_separation_either_direction'] == .125
    assert result['iid_half_success_perfect_separation_prespecified_direction'] == .0625
    assert result['empirical_power'] is None


def test_exact_matched_power_against_independent_binomial_enumeration():
    from scipy.stats import binom, binomtest
    from devbench.study.study_precision import matched_binary_power
    n, plus, minus = 15, .5, 0.
    reference = sum(binom.pmf(m,n,plus+minus) * sum(
        binom.pmf(k,m,plus/(plus+minus)) for k in range(m+1)
        if binomtest(k,m).pvalue <= .05) for m in range(1,n+1))
    assert matched_binary_power(n,plus,minus) == pytest.approx(reference)
    assert matched_binary_power(n,.75,.25) != pytest.approx(reference)


def test_three_repetitions_have_two_variance_degrees_of_freedom():
    from devbench.study.study_precision import repeatability
    value = repeatability([0.,1.,1.])
    assert value['variance_df'] == 2
    assert value['sample_variance'] == pytest.approx(1/3)
    assert repeatability([1.])['sample_variance'] is None
    assert repeatability([1.,1.,1.])['sample_variance'] == 0
    assert repeatability([1.,1.,1.])['population_repeatability_established'] is False


def rows():
    return [{'template_id':'T','case_id':'case','cohort_id':c,'independence_id':c,'value':v}
            for c,v in [('a',0.),('b',1.)]]


def test_mcse_counts_independent_cohorts_not_repetitions():
    from devbench.study.study_precision import stratified_mcse
    value = stratified_mcse(rows())
    assert value['monte_carlo_se'] == .5
    assert value['independent_cohort_count'] == 2
    assert stratified_mcse(rows()*3) == value
    assert stratified_mcse(rows()[:1])['monte_carlo_se'] is None


def test_mcse_preserves_equal_case_and_template_weights():
    from devbench.study.study_precision import stratified_mcse
    data=rows()+[dict(row,case_id='second',cohort_id=row['cohort_id']+'2',independence_id=row['cohort_id']+'2') for row in rows()]
    assert stratified_mcse(data)['monte_carlo_se'] == pytest.approx(math.sqrt(.125))
    data[2]['independence_id']='a'
    assert stratified_mcse(data)['monte_carlo_se'] is None
    changed=rows();changed.append(dict(changed[0],independence_id='changed'))
    assert stratified_mcse(changed)['monte_carlo_se'] is None


def test_report_exports_precision_but_not_arbitrary_nested_fields():
    import json
    from devbench.study.biology_dashboard import _public_analysis
    value=_public_analysis({'group_summaries':[{'task_success_precision':{'monte_carlo_se':.1,'private_truth':'DO_NOT_EXPORT'},
        'within_cohort_repeatability':[{'observed_repetitions':3,'sample_variance':.2,'cohort_id':'DO_NOT_EXPORT'}]}],
        'reasoning_evaluation':{'status':'human_review_pending'}, 'run_rows':[]})
    assert value['group_summaries'][0]['task_success_precision']['monte_carlo_se']==.1
    assert value['group_summaries'][0]['within_cohort_repeatability'][0]['observed_repetitions']==3
    assert 'DO_NOT_EXPORT' not in json.dumps(value)
