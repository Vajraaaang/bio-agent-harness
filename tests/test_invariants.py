import pytest


@pytest.mark.parametrize('answer', [
    {'gene_statistics': {'G': {'pvalue': .2, 'qvalue': .01}}},
    {'rank_deficient': True, 'causal_batch_adjusted_identifiable': True},
    {'contrast_valid': False, 'recommended_action': 'fit_validated_model'},
    {'embedding_valid': False, 'recommended_action': 'fit_validated_model'},
    {'gene_statistics': {'G': {'pvalue': float('nan'), 'qvalue': .1}}},
])
def test_positive_veto_for_invalid_statistical_claim(answer):
    from devbench_engine.invariants import statistical_failure_veto
    assert statistical_failure_veto(answer, [])["passed"]


def test_nonsignificance_claim_uses_observed_qvalues_only():
    from devbench_engine.invariants import statistical_failure_veto
    event = {'tool_name': 'fit_temporal_trajectory', 'succeeded': True,
             'result': {'genes': {'G': {'p_value': .2, 'padj': .4}}}}
    assert statistical_failure_veto({'claimed_significant_genes': ['G']}, [event])["passed"]
    assert not statistical_failure_veto({'master_regulators': ['G']}, [event])["passed"]
    assert not statistical_failure_veto({'claimed_significant_genes': ['Unknown']}, [event])["passed"]


def test_logged_failed_attempt_followed_by_valid_fit_is_not_a_veto():
    from devbench_engine.invariants import statistical_failure_veto
    events = [{'tool_name':'fit_temporal_trajectory','succeeded':False,'error':'singular'},
              {'tool_name':'fit_temporal_trajectory','succeeded':True,'result':{'genes':{'G':{'p_value':.01,'padj':.03}}}}]
    assert not statistical_failure_veto({'rank_deficient':True,'causal_batch_adjusted_identifiable':False,
                                        'gene_statistics':{'G':{'pvalue':.01,'qvalue':.03}}},events)["passed"]
