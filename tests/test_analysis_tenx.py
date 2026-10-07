import hashlib

import anndata as ad
import h5py
import numpy as np
import pandas as pd
import pytest
from scipy import sparse


def write_tenx(path, data=None, *, antibody=True):
    if data is None:
        from tests.test_analysis_singlecell import counts_fixture
        data = counts_fixture()
    matrix = sparse.csr_matrix(data.X)
    genes = list(data.var_names)
    symbols = data.var['gene_symbols'].astype(str).tolist()
    kinds = ['Gene Expression']*len(genes)
    if antibody:
        matrix = sparse.hstack([matrix, np.full((data.n_obs,1), 10000)], format='csr')
        genes.append('Antibody-id'); symbols.append('Cd3d'); kinds.append('Antibody Capture')
    with h5py.File(path,'w') as handle:
        group=handle.create_group('matrix')
        group.create_dataset('shape',data=np.array(matrix.shape[::-1],dtype=np.uint64))
        group.create_dataset('data',data=matrix.data.astype(np.int32))
        group.create_dataset('indices',data=matrix.indices.astype(np.uint32))
        group.create_dataset('indptr',data=matrix.indptr.astype(np.uint32))
        group.create_dataset('barcodes',data=np.asarray([f'BC{i:04d}-1' for i in range(data.n_obs)],dtype='S'))
        feature=group.create_group('features')
        for name,values in {'id':genes,'name':symbols,'feature_type':kinds,'genome':['mm10']*len(genes)}.items():
            feature.create_dataset(name,data=np.asarray(values,dtype='S'))
    return data


def test_tenx_backed_reader_excludes_non_RNA_and_preserves_requested_order(tmp_path):
    from devbench.analysis.io import open_expression
    path=tmp_path/'counts.h5'
    original=write_tenx(path)
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    with open_expression(path,layer='counts') as source:
        assert source.shape == original.shape
        assert source.input_info['excluded_non_RNA_features']==1
        assert set(source.obs.batch)=={'GEM_well_1'}
        part=source.subset([9,1,6],[12,0,3],max_bytes=1_000_000)
        np.testing.assert_array_equal(part.X.toarray(),original.X[[9,1,6]][:,[12,0,3]].toarray())
        assert part.obs_names.tolist()==['BC0009-1','BC0001-1','BC0006-1']
        assert part.var.gene_symbols.tolist()==original.var.gene_symbols.iloc[[12,0,3]].tolist()
        streamed=sparse.vstack([block.X for block in source.iter_rows(block_rows=13)])
        assert (streamed != original.X).nnz==0
        with pytest.raises(MemoryError):
            source.subset(max_bytes=1)
    assert source.closed
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before


def test_tenx_rejects_bad_structure_and_invented_layer(tmp_path):
    from devbench.analysis.io import open_expression
    path=tmp_path/'counts.h5'
    write_tenx(path)
    with pytest.raises(ValueError,match='layer'):
        with open_expression(path,layer='normalized'):
            pass
    with h5py.File(path,'r+') as handle:
        handle['matrix/indptr'][-1]=len(handle['matrix/data'])+1
    with pytest.raises(ValueError,match='pointer'):
        with open_expression(path,layer='counts'):
            pass


def test_tenx_gene_ids_and_barcodes_must_be_unique(tmp_path):
    from devbench.analysis.io import open_expression
    path=tmp_path/'counts.h5'
    write_tenx(path)
    with h5py.File(path,'r+') as handle:
        handle['matrix/barcodes'][1]=handle['matrix/barcodes'][0]
    with pytest.raises(ValueError,match='unique'):
        with open_expression(path):
            pass


def test_tenx_QC_uses_only_RNA_library_counts(tmp_path):
    from devbench.analysis.checkpoints import CheckpointStore,sha256
    from devbench.analysis.streaming import stream_qc,read_table
    path=tmp_path/'counts.h5';original=write_tenx(path)
    store=CheckpointStore(tmp_path/'checkpoints',{'data':sha256(path)})
    stream_qc(path,store,layer='counts',batch_key='batch',min_genes=3,mad_limit=None)
    metrics=read_table(store.path('qc')/'metrics.csv')
    np.testing.assert_array_equal(metrics.total_counts,np.asarray(original.X.sum(axis=1)).ravel())
    assert metrics.total_counts.max()<10000


def test_single_library_does_not_pretend_to_run_Harmony(tmp_path):
    from devbench.analysis.checkpoints import CheckpointStore
    from devbench.analysis.embedding import run_embedding_stages
    from tests.test_analysis_singlecell import counts_fixture
    import scanpy as sc
    import json
    data=counts_fixture();data.obs['batch']='one'
    data.layers['counts']=data.X.copy()
    sc.pp.normalize_total(data,target_sum=1e4);sc.pp.log1p(data)
    store=CheckpointStore(tmp_path/'checkpoints',{})
    store.run('selected',lambda folder:data.write_h5ad(folder/'selected.h5ad'))
    conditions=run_embedding_stages(store,batch_key='batch',seed=128,correction='harmony',n_pcs=8)
    assert conditions=={'uncorrected':'pca'}
    report=json.loads((store.path('integration_status')/'report.json').read_text())
    assert report['status']=='not_applicable_single_observed_batch'
    assert not store.path('harmony').exists()


def test_tenx_notebook_produces_reviewed_annotations_without_fabricated_batch_run(tmp_path):
    from devbench.analysis.checkpoints import sha256
    from devbench.analysis.workbench import default_parameters,prepare_bundle,execute_bundle
    import json
    source=tmp_path/'counts.h5'
    write_tenx(source)
    parameters=default_parameters(data_sha256=sha256(source),min_genes=3,mad_limit=None,
        n_top_genes=30,n_pcs=8,seed=128,
        markers={'T cell':['Cd3d','Cd3e','Trac','Il7r'], 'B cell':['Ms4a1','Cd79a','Cd79b','Cd19']})
    root=tmp_path/'bundle'
    prepare_bundle(source,root,parameters)
    outcome=execute_bundle(root)
    assert outcome['review_passed'],json.loads((root/'reviewer-report.json').read_text())
    report=json.loads((root/'report.json').read_text())
    assert report['input_info']['excluded_non_RNA_features']==1
    assert report['integration']['status']=='not_applicable_single_observed_batch'
    assert set(report['conditions'])=={'uncorrected'}
    assert report['annotation']['status']=='completed'
    from devbench.analysis.streaming import read_table
    labels=read_table(root/'checkpoints/annotation/predictions.csv')
    assert set(labels.cell_type)=={'T cell','B cell'}
    assert report['conditions']['uncorrected']['clustering']['ARI'] is None
