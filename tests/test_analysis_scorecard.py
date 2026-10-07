import json

import pandas as pd
import pytest


def test_external_predictions_are_concordance_with_abstentions_and_QC_loss():
    from devbench.analysis.scorecard import score_labels
    reference=pd.Series(['T','T','B','B'],index=['01','02','03','04'])
    predictions=pd.Series(['T','uncertain','T'],index=['01','02','03'])
    result=score_labels(reference,predictions,evidence_type='external_predictions')
    assert result['interpretation']=='concordance_with_external_predictions'
    assert result['reference_cells']==4 and result['retained_cells']==3
    assert result['QC_excluded_reference_cells']==1
    assert result['uncertain_retained_cells']==1
    assert result['label_agreement_retained_including_uncertain']==pytest.approx(1/3)
    assert result['label_agreement_on_called_cells']==.5
    assert result['correct_label_fraction_of_all_reference_cells']==.25
    assert 'accuracy' not in json.dumps(result).lower()


def test_no_calls_is_unknown_selective_agreement_not_perfect_score():
    from devbench.analysis.scorecard import score_labels
    result=score_labels(pd.Series(['T','B'],index=['a','b']),
                        pd.Series(['uncertain','uncertain'],index=['a','b']),evidence_type='simulation')
    assert result['call_coverage_of_retained_cells']==0
    assert result['label_agreement_on_called_cells'] is None
    assert result['correct_label_fraction_of_all_reference_cells']==0


def test_reference_alignment_and_unknown_labels_fail_closed():
    from devbench.analysis.scorecard import score_labels
    with pytest.raises(ValueError,match='identity'):
        score_labels(pd.Series(['T'],index=['a']),pd.Series(['T'],index=['outside']),evidence_type='simulation')
    with pytest.raises(ValueError,match='unknown_reference'):
        score_labels(pd.Series(['uncertain'],index=['a']),pd.Series(['T'],index=['a']),evidence_type='simulation')


from tests.test_analysis_workbench import portable_bundle


def test_sealed_bundle_scoring_is_separate_and_preserves_original_receipt(portable_bundle,tmp_path):
    from devbench.analysis.scorecard import score_bundle,verify_scorecard
    from devbench.analysis.checkpoints import sha256
    from devbench.analysis.bundles import verify_bundle
    source,bundle,result=portable_bundle
    import anndata as ad
    data=ad.read_h5ad(source)
    reference=tmp_path/'reference'
    reference.mkdir()
    pd.DataFrame({'cell_id':data.obs_names,'label':data.obs.celltypes.to_numpy()}).to_csv(reference/'labels.csv',index=False)
    manifest={'format':'devbench.analysis.annotation-reference/1','evidence_type':'simulation',
        'labels_file':'labels.csv','labels_sha256':sha256(reference/'labels.csv'),
        'dataset_id':'designed-80-cell-software-fixture','source_description':'Known labels used to construct the synthetic fixture.',
        'full_input_coverage_required':True}
    (reference/'reference.json').write_text(json.dumps(manifest))
    before=sha256(bundle/'receipts.json')
    output=tmp_path/'scorecard'
    scored=score_bundle(bundle,result['manifest_sha256'],reference/'reference.json',output)
    assert sha256(bundle/'receipts.json')==before
    verify_bundle(bundle,result['manifest_sha256'])
    assert scored['status']=='scored'
    assert scored['metrics']['interpretation']=='simulation_label_recovery'
    assert scored['metrics']['correct_label_fraction_of_all_reference_cells']>.9
    assert scored['population_inference_performed'] is False
    assert scored['analysis_execution_tier']=='local_supervised'
    receipt=json.loads((output/'root-receipt.json').read_text())
    assert verify_scorecard(output,receipt['manifest_sha256'])['verified']
    (output/'scorecard.json').write_text('{}')
    with pytest.raises(ValueError,match='artifact_mismatch'):
        verify_scorecard(output,receipt['manifest_sha256'])


def test_missing_reference_cells_cannot_silently_improve_score(portable_bundle,tmp_path):
    from devbench.analysis.scorecard import score_bundle
    from devbench.analysis.checkpoints import sha256
    _,bundle,result=portable_bundle
    labels=tmp_path/'labels.csv'
    labels.write_text('cell_id,label\nc0,T cell\n')
    manifest={'format':'devbench.analysis.annotation-reference/1','evidence_type':'external_predictions',
        'labels_file':'labels.csv','labels_sha256':sha256(labels),'dataset_id':'incomplete',
        'source_description':'A deliberately incomplete prediction reference.', 'full_input_coverage_required':True}
    path=tmp_path/'reference.json';path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='reference_input_identity'):
        score_bundle(bundle,result['manifest_sha256'],path,tmp_path/'scorecard')
    assert (tmp_path/'scorecard/failure.json').is_file()


def test_scorecard_reference_is_portable_and_experiment_requires_provenance(portable_bundle,tmp_path):
    from devbench.analysis.scorecard import score_bundle
    from devbench.analysis.checkpoints import sha256
    _,bundle,result=portable_bundle
    labels=tmp_path/'labels.csv'
    pd.DataFrame({'cell_id':[f'c{i}' for i in range(80)],'label':['T cell']*40+['B cell']*40}).to_csv(labels,index=False)
    reference={'format':'devbench.analysis.annotation-reference/1','evidence_type':'experimental_sort',
        'labels_file':'labels.csv','labels_sha256':sha256(labels),'dataset_id':'fixture',
        'source_description':'Test fixture, no experimental provenance.','full_input_coverage_required':True}
    path=tmp_path/'reference.json';path.write_text(json.dumps(reference))
    with pytest.raises(ValueError,match='sorting method'):
        score_bundle(bundle,result['manifest_sha256'],path,tmp_path/'rejected')
    reference['evidence_type']='simulation';path.write_text(json.dumps(reference))
    first=score_bundle(bundle,result['manifest_sha256'],path,tmp_path/'first')
    second=score_bundle(bundle,result['manifest_sha256'],tmp_path/'first'/first['replay_reference_manifest'],tmp_path/'second')
    assert first['metrics']==second['metrics']


def test_metadata_plan_does_not_hash_or_fit_the_dataset(tmp_path,monkeypatch,capsys):
    from tests.test_analysis_singlecell import counts_fixture
    from devbench.analysis.workbench import main
    import devbench.analysis.checkpoints as checkpoints
    from devbench.analysis.io import BackedExpression
    path=tmp_path/'data.h5ad';counts_fixture().write_h5ad(path)
    def forbidden(*args,**kwargs):
        raise AssertionError('planning must not hash the full input or materialize counts')
    monkeypatch.setattr(checkpoints,'sha256',forbidden)
    monkeypatch.setattr(BackedExpression,'subset',forbidden)
    main(['--data',str(path),'--out',str(tmp_path/'planned'),'--layer','X','--plan-only'])
    result=json.loads(capsys.readouterr().out)
    assert result['execution_performed'] is False
    assert result['expected_data_sha256'] is None
    assert not (tmp_path/'planned').exists()


def test_source_change_is_retained_as_failure_not_a_successful_score(portable_bundle,tmp_path,monkeypatch):
    import devbench.analysis.scorecard as module
    from devbench.analysis.checkpoints import sha256
    _,bundle,result=portable_bundle
    labels=tmp_path/'labels.csv'
    pd.DataFrame({'cell_id':[f'c{i}' for i in range(80)],'label':['T cell']*40+['B cell']*40}).to_csv(labels,index=False)
    path=tmp_path/'reference.json'
    path.write_text(json.dumps({'format':'devbench.analysis.annotation-reference/1','evidence_type':'simulation',
        'labels_file':'labels.csv','labels_sha256':sha256(labels),'dataset_id':'fixture',
        'source_description':'Synthetic fixture','full_input_coverage_required':True}))
    real=module._implementation_hashes
    calls=[]
    def changed():
        hashes=real();calls.append(1)
        if len(calls)>1:
            hashes['scorecard.py']='0'*64
        return hashes
    monkeypatch.setattr(module,'_implementation_hashes',changed)
    output=tmp_path/'scorecard'
    with pytest.raises(ValueError,match='implementation_changed'):
        module.score_bundle(bundle,result['manifest_sha256'],path,output)
    assert not (output/'scorecard.json').exists()
    assert json.loads((output/'receipts.json').read_text())['status']=='not_scored'
