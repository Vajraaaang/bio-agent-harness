"""Signed reference semantics and factor reconstruction; no clinical qualification."""
from copy import deepcopy
import csv

import numpy as np
import pytest

from devharness_controlled.native_contracts import (
    NativeEffectPlan, GovernedNativePlan, GovernedNativeEffectPlan,
    ReferenceNativePlan, ReferenceNativeEffectPlan, is_reference_plan,
    is_governed_plan, parse_native_plan_bytes,
)
from devharness_controlled.normalization_reference import NormalizationReference
from devharness_controlled.native_executor import check_input_tables, native_request
from devharness_controlled.qualification import synthetic_approvals
from devharness_controlled.trust import verify_plan_approvals
from tests.test_native_execution import plan as original_plan, tables


def reference(**updates):
    values=dict(schema_version='normalization_reference/1',reference_id='fixture-reference',study_id='test-fixture',
        gene_ids=('g2',),annotation_reference='synthetic-identifiers',selection_basis='Prespecified software-test controls; not clinical stability',
        eligibility_policy='all_positive',qualification_status='not_established')
    return NormalizationReference(**{**values,**updates})


def referenced_plan(effect=False,**updates):
    values={**original_plan(engine='deseq2_lrt',deseq2_fit_type='mean').model_dump(),
        'schema_version':'controlled_native_analysis/6' if effect else 'controlled_native_analysis/5',
        'authority_id':'fixture-authority','authority_tip_sha256':'b'*64,'normalization_reference':reference()}
    if effect:values.update(effect_name='later',effect_reference_sample_id='S0',effect_comparison_sample_id='S4',effect_confidence_level=.95)
    return (ReferenceNativeEffectPlan if effect else ReferenceNativePlan)(**{**values,**updates})


@pytest.mark.parametrize('effect',[False,True])
def test_references_are_signed_and_retained_in_numeric_primary_plan(effect):
    p=referenced_plan(effect);decoded=parse_native_plan_bytes(p.canonical_bytes())
    assert decoded==p and is_reference_plan(decoded) and is_governed_plan(decoded)
    assert p.primary_plan().normalization_reference==p.normalization_reference
    approvals,signers=synthetic_approvals(p);assert len(verify_plan_approvals(p,approvals,signers))==2
    changed=referenced_plan(effect,normalization_reference=reference(annotation_reference='another-annotation'))
    with pytest.raises(ValueError,match='does not bind'):verify_plan_approvals(changed,approvals,signers)
    if effect:
        with pytest.raises(ValueError):verify_plan_approvals(p.primary_plan(),approvals,signers)
    request=native_request(p)
    assert request['schema_version']==('native_nb_request/4' if effect else 'native_nb_request/3')
    assert request['normalization_reference']=={'gene_ids':['g2'],'specification_sha256':reference().sha256(),'eligibility_policy':'all_positive'}
    assert 'selection_basis' not in str(request)


@pytest.mark.parametrize('updates',[{'engine':'edger_qlf','deseq2_fit_type':None},
    {'normalization_reference':reference(study_id='another-study')},
    {'normalization_reference':reference(gene_ids=('absent',))}])
def test_reference_study_and_panel_are_explicit(updates):
    with pytest.raises(ValueError):referenced_plan(**updates)


def write_counts(path,rows):
    with path.open('w',newline='') as f:csv.writer(f).writerows(rows)


def test_nonreference_changes_do_not_change_reference_factors(tmp_path):
    _,rows=tables(tmp_path/'data');p=referenced_plan()
    read=lambda name:(tmp_path/'data'/name).read_bytes()
    first=check_input_tables(p,read)['normalization_from_counts']
    rows['counts.csv'][1][1:]=[1000,1,3,9000,2,700]
    write_counts(tmp_path/'data/counts.csv',rows['counts.csv'])
    second=check_input_tables(p,read)['normalization_from_counts']
    expected=np.asarray(rows['counts.csv'][2][1:],dtype=float)
    expected=np.exp(np.log(expected)-np.log(expected).mean())
    assert first==second
    assert first['scope']=='prespecified_reference_genes'
    assert first['reference_gene_ids']==['g2']
    assert first['reference_specification_sha256']==p.normalization_reference.sha256()
    assert np.allclose(list(first['size_factors'].values()),expected,rtol=0,atol=1e-12)


def test_a_zero_reference_is_not_dropped_when_other_panel_genes_are_positive(tmp_path):
    _,rows=tables(tmp_path/'data');rows['counts.csv'][2][1]=0;write_counts(tmp_path/'data/counts.csv',rows['counts.csv'])
    with pytest.raises(ValueError,match='reference'):
        check_input_tables(referenced_plan(),lambda name:(tmp_path/'data'/name).read_bytes())
    # Historical whole-panel RLE remains usable; this is intentionally a
    # different signed reference policy, not a blanket refusal of all data.
    assert check_input_tables(original_plan(engine='deseq2_lrt',deseq2_fit_type='mean'),lambda name:(tmp_path/'data'/name).read_bytes())


def test_legacy_plan_canonical_bytes_keep_their_previous_binding():
    p=original_plan(engine='deseq2_lrt',deseq2_fit_type='mean')
    e=NativeEffectPlan(**{**p.model_dump(),'schema_version':'controlled_native_analysis/2',
        'effect_name':'later','effect_reference_sample_id':'S0','effect_comparison_sample_id':'S4','effect_confidence_level':.95})
    g=GovernedNativePlan(**{**p.model_dump(),'schema_version':'controlled_native_analysis/3',
        'authority_id':'fixture-authority','authority_tip_sha256':'b'*64})
    h=GovernedNativeEffectPlan(**{**e.model_dump(),'schema_version':'controlled_native_analysis/4',
        'authority_id':'fixture-authority','authority_tip_sha256':'b'*64})
    expected=('d132d36bb4f60c20406a5d361a9a65402822872596c6c2ac71216db262ac461a',
        'de6963e9595a66de7090acc1738b8a8237f0162ff358765009f26f93f7ca4192',
        'b54c8ca5fc6bb39f68c65d748b3f1bde482628b0fc3a28b4134bf901e866797b',
        'a819eb8f0a051a7d5ddc94b67fac47c7da9032d103fa3ace4a9a261f6d8e61e3')
    for item,sha in zip((p,e,g,h),expected):
        assert item.sha256()==sha
        assert parse_native_plan_bytes(item.canonical_bytes())==item
        assert 'normalization_reference' not in native_request(item)


@pytest.mark.parametrize('mode',['full_panel','wrong_digest','wrong_set','unknown_field'])
def test_reference_result_cannot_claim_another_normalization(mode):
    from devharness_controlled.native_results import _normalization
    p=referenced_plan()
    record={'method':'DESeq2_ratio','scope':'prespecified_reference_genes','reference_gene_ids':['g2'],
        'reference_specification_sha256':p.normalization_reference.sha256(),'size_factors':{s:1.0 for s in p.sample_ids}}
    _normalization(p,record)
    if mode=='full_panel':record['scope']='entire_input_panel'
    if mode=='wrong_digest':record['reference_specification_sha256']='f'*64
    if mode=='wrong_set':record['reference_gene_ids']=['g1']
    if mode=='unknown_field':record['automatic_fallback']=True
    with pytest.raises(ValueError):_normalization(p,record)


def test_reference_effect_primary_validation_retains_reference_semantics():
    from tests.test_native_effects import fixture as effect_fixture
    from devharness_controlled.native_results import validate_native_result
    old,result,_,_=effect_fixture()
    ref=reference(study_id=old.study_id,gene_ids=(old.panel_genes[-1],))
    p=ReferenceNativeEffectPlan(**{**old.model_dump(),'schema_version':'controlled_native_analysis/6',
        'authority_id':'fixture-authority','authority_tip_sha256':'b'*64,'normalization_reference':ref})
    norm=result['primary']['evidence']['normalization']
    norm.update(scope='prespecified_reference_genes',reference_gene_ids=[old.panel_genes[-1]],reference_specification_sha256=ref.sha256())
    assert validate_native_result(p,result)['usable']
    norm['scope']='entire_input_panel'
    with pytest.raises(ValueError):validate_native_result(p,result)
