import numpy as np
import pandas as pd
import pytest


def test_streamed_all_cell_ASW_matches_sklearn_and_scib_definition():
    from sklearn.metrics import silhouette_samples
    from devbench.analysis.integration_metrics import full_silhouette_metrics
    rng=np.random.default_rng(782)
    labels=np.repeat(['A','B','C'],24)
    batches=np.tile(['x','y','z'],24)
    x=rng.normal(size=(72,5))+np.repeat([0,3,6],24)[:,None]
    observed=full_silhouette_metrics(x,batches,labels,working_memory_mb=.01)
    expected_type=float(silhouette_samples(x,labels).mean())
    expected_batch=float(silhouette_samples(x,batches).mean())
    by_type=[]
    for label in np.unique(labels):
        mask=labels==label
        by_type.append(float((1-np.abs(silhouette_samples(x[mask],batches[mask]))).mean()))
    assert observed['cell_type_ASW']==pytest.approx(expected_type,abs=1e-12)
    assert observed['batch_silhouette']==pytest.approx(expected_batch,abs=1e-12)
    assert observed['batch_ASW']==pytest.approx(np.mean(by_type),abs=1e-12)
    assert observed['cells_evaluated']==72 and observed['subsampling'] is False


def test_ineligible_reference_types_and_single_batches_are_explicit():
    from devbench.analysis.integration_metrics import full_silhouette_metrics
    x=np.arange(18,dtype=float).reshape(6,3)
    result=full_silhouette_metrics(x,['one']*6,['A']*3+['B']*3)
    assert result['batch_ASW'] is None
    assert result['eligible_reference_types']==0
    assert result['cell_type_ASW'] is not None
    result=full_silhouette_metrics(x,['a','b']*3,None)
    assert result['cell_type_ASW'] is None and result['batch_ASW'] is None


def test_missing_reference_labels_are_excluded_without_inventing_a_type():
    from sklearn.metrics import silhouette_samples
    from devbench.analysis.integration_metrics import full_silhouette_metrics
    rng=np.random.default_rng(73);x=rng.normal(size=(8,3))
    labels=np.array(['A','A','A','B','B','B',None,None],dtype=object)
    result=full_silhouette_metrics(x,['one','two']*4,labels)
    assert result['reference_label_cells']==6
    assert result['cell_type_ASW']==pytest.approx(silhouette_samples(x[:6],labels[:6]).mean(),abs=1e-12)


def test_QC_comparison_preserves_disagreement_and_missingness():
    from devbench.analysis.qc_concordance import compare_qc
    new=pd.DataFrame({'total_counts':[10,20,30],'pct_counts_mt':[1.,2.,3.]},index=['a','b','c'])
    old=pd.DataFrame({'total_counts':[10,25,30],'pct_counts_mt':[1.,np.nan,3.]},index=new.index)
    report=compare_qc(old,new)
    assert report['fields']['total_counts']['mismatched_cells']==1
    assert report['fields']['total_counts']['maximum_absolute_difference']==5
    assert report['fields']['pct_counts_mt']['paired_finite_cells']==2
    assert report['fields']['pct_counts_mt']['missing_or_nonfinite_reference_cells']==1
    assert report['fields']['pct_counts_in_top_20_genes']['status']=='unavailable_column'
    assert report['scope']=='concordance_with_stored_QC_not_a_truth_test'


def test_collapsed_geometry_does_not_hide_behind_a_perfect_mixing_score():
    from devbench.analysis.integration_metrics import full_silhouette_metrics
    result=full_silhouette_metrics(np.zeros((12,3)),['a','b']*6,['T']*6+['B']*6)
    assert result['batch_ASW']==1
    assert result['cell_type_ASW']==0
    assert result['constant_embedding'] is True


def test_top20_is_recomputed_and_stored_QC_disagreement_remains_visible(tmp_path):
    import scanpy as sc
    import json
    from tests.test_analysis_singlecell import counts_fixture
    from devbench.analysis.checkpoints import CheckpointStore,sha256
    from devbench.analysis.streaming import stream_qc,read_table
    data=counts_fixture()
    data.var['mt']=data.var.gene_symbols.str.upper().str.startswith('MT-')
    sc.pp.calculate_qc_metrics(data,qc_vars=['mt'],percent_top=[20],inplace=True)
    expected=data.obs.pct_counts_in_top_20_genes.copy()
    data.obs.loc[data.obs_names[0],'total_counts']+=10
    data.obs.loc[data.obs_names[1],'pct_counts_in_top_20_genes']+=.5
    source=tmp_path/'source.h5ad';data.write_h5ad(source)
    store=CheckpointStore(tmp_path/'checkpoints',{'data_sha256':sha256(source)})
    stream_qc(source,store,layer=None,batch_key='batch',min_genes=3,mad_limit=None)
    actual=read_table(store.path('qc')/'metrics.csv')
    np.testing.assert_allclose(actual.pct_counts_in_top_20_genes,expected)
    comparison=json.loads((store.path('qc')/'concordance.json').read_text())
    assert comparison['fields']['total_counts']['mismatched_cells']==1
    assert comparison['fields']['pct_counts_in_top_20_genes']['mismatched_cells']==1
