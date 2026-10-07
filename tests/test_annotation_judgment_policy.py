"""Predeclared synthetic ontology controls, not expert annotation validation."""
import pandas as pd
import pytest


def policy(requested=('CD4','CD8','B')):
    from devbench.analysis.scorecard import AnnotationPolicy
    return AnnotationPolicy(policy_id='synthetic-ontology',version='1',ontology_version='fixture-1',
        ontology_sha256='b'*64,labels=('immune','T','CD4','CD8','B'),
        parents=(('T','immune'),('B','immune'),('CD4','T'),('CD8','T')),
        synonyms=(('helper','CD4'),),requested_labels=requested,
        unresolved_labels=('uncertain','unknown'),mixed_labels=('mixed',),coarse_credit=.5)


def score(reference,predicted,frozen=None):
    from devbench.analysis.scorecard import score_labels_with_policy, annotation_policy_sha256
    frozen=frozen or policy()
    return score_labels_with_policy(pd.Series(reference,index=[f'c{i}' for i in range(len(reference))]),
        pd.Series(predicted,index=[f'c{i}' for i in range(len(predicted))]),evidence_type='simulation',
        policy=frozen,expected_policy_sha256=annotation_policy_sha256(frozen))


def test_frozen_synonyms_and_hierarchy_preserve_raw_exact_scores():
    result=score(['CD4','CD4','B','CD8'],['helper','T','CD4','uncertain'])
    assert result['legacy_exact']['correct_label_fraction_of_all_reference_cells']==0
    assert result['relations']=={'exact':0,'synonymous':1,'compatible_coarser':1,
        'compatible_finer':0,'contradictory':1,'unresolved':1,'mixed':0,'excluded':0}
    assert result['policy_credit_fraction']==.375
    assert result['full_credit_at_requested_granularity_fraction']==.25


def test_coarse_label_has_partial_fine_credit_and_full_prespecified_coarse_credit():
    assert score(['CD4'],['T'])['policy_credit_fraction']==.5
    assert score(['T'],['T'],policy(('T','B')))['policy_credit_fraction']==1
    finer=score(['T'],['CD4'],policy(('T','B')))
    assert finer['relations']['compatible_finer']==1
    assert finer['policy_credit_fraction']==0
    assert finer['unsupported_finer_predictions']==1


def test_unresolved_mixed_and_excluded_remain_denominators():
    result=score(['CD4','CD8','B'],['mixed','unknown'])
    assert result['policy_credit_fraction']==0
    assert result['relations']['excluded']==1
    assert result['relations']['mixed']==1
    assert result['relations']['unresolved']==1


def test_policy_cannot_change_after_freeze_or_fit_synonyms_from_predictions():
    from devbench.analysis.scorecard import score_labels_with_policy, annotation_policy_sha256
    p=policy();digest=annotation_policy_sha256(p)
    with pytest.raises(ValueError,match='policy'):
        score_labels_with_policy(pd.Series(['CD4'],index=['a']),pd.Series(['helper'],index=['a']),
            evidence_type='simulation',policy=p.model_copy(update={'synonyms':()}),expected_policy_sha256=digest)
    with pytest.raises(ValueError,match='unknown'):
        score(['CD4'],['unregistered-subtype'])
    with pytest.raises(Exception):p.coarse_credit=1.


def test_cycle_duplicate_alias_and_out_of_scope_reference_fail_closed():
    with pytest.raises(ValueError,match='cycle'):
        policy().model_copy(update={'parents':(('T','CD4'),('CD4','T'))}).__class__.model_validate_json(
            policy().model_copy(update={'parents':(('T','CD4'),('CD4','T'))}).model_dump_json())
    with pytest.raises(ValueError,match='granularity'):score(['T'],['T'])
