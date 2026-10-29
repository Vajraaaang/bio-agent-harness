"""Synthetic admission and numerical components; no aligner or provider calls."""
import copy
import csv
import json
from pathlib import Path

import numpy as np
import pytest

from devbench.analysis.fastq_contract import validate_design, preflight_fastq_plan


def paired_rows():
    return [dict(sample_id=f's{i}', donor_id=f'd{i//2}', specimen_id=f'p{i}',
                 library_id=f'l{i}', condition=['A','B'][i%2]) for i in range(8)]


def paired_design(rows=None, **changes):
    kwargs = dict(formula='~ donor_id + condition', biological_unit='donor_id',
                  biological_unit_type='donor', blocking=['donor_id'], batch_columns=[],
                  contrast=dict(factor='condition',numerator='B',denominator='A'))
    kwargs.update(changes)
    return validate_design(rows or paired_rows(), **kwargs)


def test_paired_design_has_four_units_and_eight_memberships():
    design = paired_design()
    assert (design['unit_count'],design['rank'],design['residual_df']) == (4,5,3)
    assert design['analysis_observation_count'] == 8
    assert design['blocking'] == ['donor_id']


def test_technical_libraries_do_not_inflate_paired_degrees_of_freedom():
    rows = paired_rows()
    rows += [dict(row, sample_id=row['sample_id']+'tech',library_id=row['library_id']+'tech') for row in rows]
    design = paired_design(rows)
    assert design['residual_df'] == 3 and design['analysis_observation_count'] == 8
    assert len(design['sample_ids']) == 16


@pytest.mark.parametrize('defect', ['confounded','false_units','library_reuse','incomplete'])
def test_paired_design_refuses_invalid_identity_or_confounding(defect):
    rows = paired_rows(); options = {}
    if defect == 'confounded':
        for row in rows: row['batch'] = row['condition']
        options.update(batch_columns=['batch'],formula='~ donor_id + batch + condition')
    elif defect == 'false_units':
        for row in rows: row['hidden_unit'] = row['sample_id']
        options.update(biological_unit='hidden_unit',blocking=['hidden_unit'],formula='~ hidden_unit + condition')
    elif defect == 'library_reuse': rows[1]['library_id'] = rows[0]['library_id']
    else: rows[1]['condition'] = 'A'
    with pytest.raises(ValueError) as exc:
        paired_design(rows,**options)
    assert exc.value.category == 'invalid_design'


def test_missing_plan_information_is_typed_and_value_error_compatible():
    with pytest.raises(ValueError) as exc: preflight_fastq_plan({})
    assert exc.value.category == 'needs_information'


def test_optional_filter_uses_only_eligible_finite_tests_and_preserves_rows():
    from devbench.analysis.fastq_analysis_policy import apply_independent_filter
    rows = [dict(gene_id='a',baseMean=1.,pvalue=.001,padj=.003),
            dict(gene_id='b',baseMean=20.,pvalue=.02,padj=.03),
            dict(gene_id='c',baseMean=30.,pvalue=.8,padj=.8),
            dict(gene_id='zero',baseMean=0.,pvalue=None,padj=None)]
    family = apply_independent_filter(rows,dict(method='fixed_base_mean',threshold=10.))
    assert len(rows)==4 and rows[0]['pvalue']==.001 and rows[0]['padj']==.003 and rows[0]['padj_filtered'] is None
    assert rows[0]['filter_eligible'] is False
    assert rows[1]['padj']==.03 and rows[1]['padj_filtered']==pytest.approx(.04)
    assert family['bh_family_gene_ids']==['b','c']


def test_paired_optional_policy_refuses_before_execution():
    from devbench.analysis.fastq_analysis_policy import validate_analysis_policy
    with pytest.raises(ValueError) as exc:
        validate_analysis_policy(dict(format='devbench.fastq-analysis-policy/1',lfc_shrinkage=dict(method='apeglm')),paired=True)
    assert exc.value.category=='unsupported_analysis'


@pytest.mark.parametrize("unit_type", ["donor", "subject", "donor_derived_cell_line"])
def test_registered_paired_count_model_preserves_existing_core_and_bh(tmp_path, unit_type):
    from devbench.analysis.fastq_count_model import fit_count_matrix
    from statsmodels.stats.multitest import multipletests
    design=paired_design(biological_unit_type=unit_type); rng=np.random.default_rng(2481)
    values=rng.negative_binomial(25,25/(25+np.linspace(40,200,60)[:,None]*np.ones((1,8))))
    values[-1]=0
    path=tmp_path/'counts.tsv'
    with path.open('w') as h:
        writer=csv.writer(h,delimiter='\t');writer.writerow(['gene_id',*design['sample_ids']])
        writer.writerows([f'g{i}',*row] for i,row in enumerate(values))
    result=fit_count_matrix(path,design,tmp_path/'out')
    assert result['model']['package']=='devbench_existing_bulk_core'
    provenance=json.loads(Path(result['provenance']['path']).read_text())
    assert provenance['registered_tool_events'][0]['tool_name']=='bulk_condition_contrast'
    assert provenance['core_result']['target_results'][0]['payload']['biological_unit']==design['core_biological_unit_type']
    assert provenance['design']['biological_unit_type']==unit_type
    rows=list(csv.DictReader(Path(result['differential_expression']['path']).open(),delimiter='\t'))
    assert len(rows)==60 and rows[-1]['pvalue']=='' and rows[-1]['padj']==''
    expected=multipletests([float(r['pvalue']) if r['pvalue'] else 1. for r in rows],method='fdr_bh')[1]
    for row,q in zip(rows,expected):
        if row['pvalue']: assert float(row['padj'])==pytest.approx(q)
    assert provenance['core_result']['diagnostics']['designs'][0]['rank']==5
    # Independently reconstruct offsets and conditional NB coefficient/SE.
    # Dispersion is retained fitted evidence, not independently validated here.
    import statsmodels.api as sm
    positive=values[(values>0).all(axis=1)].astype(float)
    log_ratios=np.log(positive)-np.log(positive).mean(axis=1)[:,None]
    factors=np.exp(np.median(log_ratios,axis=0)-np.median(log_ratios,axis=0).mean())
    first=next(row for row in rows if row['pvalue'])
    index=int(first['gene_id'][1:]);matrix=np.asarray(design['design_matrix'])
    fitted=sm.GLM(values[index],matrix,family=sm.families.NegativeBinomial(alpha=float(first['dispersion'])),offset=np.log(factors)).fit(maxiter=100)
    column=design['design_columns'].index('condition[B]')
    assert float(first['log2FoldChange'])==pytest.approx(fitted.params[column]/np.log(2),rel=1e-6,abs=1e-8)
    assert float(first['lfcSE'])==pytest.approx(fitted.bse[column]/np.log(2),rel=1e-6)
    assert float(first['pvalue'])==pytest.approx(fitted.pvalues[column],rel=1e-6)


def test_real_optional_shrinkage_retains_original_wald_tests(tmp_path):
    from tests.test_fastq_count_model import _design,_write_counts
    from devbench.analysis.fastq_count_model import fit_count_matrix
    design=_design();rng=np.random.default_rng(293)
    counts={f'g{i}':rng.negative_binomial(30,30/(30+80.+i),size=12) for i in range(60)}
    counts['zero']=np.zeros(12,dtype=int)
    path=_write_counts(tmp_path/'counts.tsv',design,counts)
    default=fit_count_matrix(path,design,tmp_path/'default')
    optional=fit_count_matrix(path,design,tmp_path/'optional',analysis_policy={
        'format':'devbench.fastq-analysis-policy/1','lfc_shrinkage':{'method':'apeglm'}})
    def read(result): return list(csv.DictReader(Path(result['differential_expression']['path']).open(),delimiter='\t'))
    original,shrunk=read(default),read(optional)
    for a,b in zip(original,shrunk):
        for key in ['log2FoldChange','lfcSE','stat','pvalue','padj']: assert a[key]==b[key]
    assert any(r['log2FoldChange_shrunk'] and r['log2FoldChange_shrunk']!=r['log2FoldChange'] for r in shrunk)
    assert shrunk[-1]['log2FoldChange_shrunk']==''


def test_physical_library_cannot_span_two_specimens_in_one_coordinate():
    rows=paired_rows();rows.append(dict(rows[0],sample_id='extra',specimen_id='new_specimen'))
    with pytest.raises(ValueError,match='library.*specimen'):
        paired_design(rows)


def test_unknown_biological_identity_needs_information():
    rows=paired_rows();rows[0]['donor_id']='unknown'
    with pytest.raises(ValueError) as exc: paired_design(rows)
    assert exc.value.category=='needs_information'


def test_optional_filter_preserves_original_results_in_real_fit(tmp_path):
    from tests.test_fastq_count_model import _design,_write_counts
    from devbench.analysis.fastq_count_model import fit_count_matrix
    from statsmodels.stats.multitest import multipletests
    design=_design();rng=np.random.default_rng(23)
    counts={f'g{i}':rng.negative_binomial(25,25/(25+10+5*i),size=12) for i in range(45)}
    counts['zero']=np.zeros(12,dtype=int)
    path=_write_counts(tmp_path/'counts.tsv',design,counts)
    result=fit_count_matrix(path,design,tmp_path/'filtered',analysis_policy={
        'format':'devbench.fastq-analysis-policy/1','independent_filter':{'method':'fixed_base_mean','threshold':100.}})
    rows=list(csv.DictReader(Path(result['differential_expression']['path']).open(),delimiter='\t'))
    all_tested=[r for r in rows if r['pvalue']]
    eligible=[r for r in all_tested if r['filter_eligible']=='True']
    assert 0<len(eligible)<len(all_tested)
    for row,q in zip(all_tested,multipletests([float(r['pvalue']) for r in all_tested],method='fdr_bh')[1]):
        assert float(row['padj'])==pytest.approx(q)
    for row,q in zip(eligible,multipletests([float(r['pvalue']) for r in eligible],method='fdr_bh')[1]):
        assert float(row['padj_filtered'])==pytest.approx(q)
    assert all(r['padj_filtered']=='' for r in rows if r['filter_eligible']=='False')
    assert result['family']['filtered_bh_family_gene_ids']==[r['gene_id'] for r in eligible]


def test_pinned_shrinkage_matches_independent_posterior_optimization():
    """Conditional optimizer check, not an EB-prior or biological calibration claim."""
    from pydeseq2.utils import nbinomGLM
    from scipy.optimize import minimize
    from scipy.stats import nbinom
    x=np.column_stack([np.ones(12),np.tile([0.,1.],6)])
    counts=np.array([38,70,32,77,45,84,39,71,37,69,40,88])
    offset=np.log(np.linspace(.9,1.1,12));size=25.;prior=.7
    beta,_,converged=nbinomGLM(x,counts,size,offset,15.,prior,shrink_index=1)
    def posterior(parameters):
        mu=np.exp(offset+x@parameters)
        return -nbinom.logpmf(counts,size,size/(size+mu)).sum()+parameters[0]**2/(2*15.**2)+np.log1p((parameters[1]/prior)**2)
    independent=minimize(posterior,[3.,.1],method='BFGS',options={'gtol':1e-6})
    assert converged
    assert posterior(beta)==pytest.approx(independent.fun,abs=2e-5)
    np.testing.assert_allclose(beta,independent.x,atol=2e-3)


def test_paired_recognizable_cellline_alias_cannot_split_actual_unit():
    rows=paired_rows()
    for row in rows: row['cellline']='one_line'
    with pytest.raises(ValueError,match='repeated_biological_units:cellline'):
        paired_design(rows)


def test_valid_but_unsupported_interaction_is_not_invalid_science():
    rows=[dict(sample_id=f's{i}',donor_id=f'd{i}',condition=['A','B'][i%2],batch=['x','y'][i//4]) for i in range(8)]
    with pytest.raises(ValueError) as exc:
        validate_design(rows,formula='~ batch * condition',contrast={'factor':'condition','numerator':'B','denominator':'A'},biological_unit='donor_id',batch_columns=['batch'])
    assert exc.value.category=='unsupported_analysis'


@pytest.mark.parametrize('requested,core_type',[('subject','organism'),('donor','donor'),('donor_derived_cell_line','donor_derived_cell_line')])
def test_paired_unit_type_is_admitted_by_registered_core_before_quantification(requested,core_type):
    from devbench.analysis.fastq_paired_model import _task
    from devharness_server.tools_bulk import validate_bulk_contrast_parameters
    design=paired_design(biological_unit_type=requested)
    task=_task(design,['g1'])
    assert task['unit_hierarchy']['population_unit']==core_type
    assert task['context']['biological_unit']==core_type
    assert design['biological_unit_type']==requested
    assert design['core_biological_unit_type']==core_type
    options={'condition_col':'condition','reference_condition':'A','comparison_condition':'B',
             'batch_cols':[],'block_cols':['donor_id'],'minimum_units':3,'condition_number_limit':1000.}
    validate_bulk_contrast_parameters(task,options)


def test_specimen_type_needs_population_identity_before_quantification():
    with pytest.raises(ValueError) as exc: paired_design(biological_unit_type='specimen')
    assert exc.value.category=='needs_information'
    assert 'population' in str(exc.value)


@pytest.mark.parametrize('column',['donor_id','specimen_id','library_id'])
@pytest.mark.parametrize('missing',[None,'','  ','unknown','not_known',' Not Known '])
def test_all_missing_paired_identity_values_need_information(column,missing):
    rows=paired_rows();rows[0][column]=missing
    with pytest.raises(ValueError) as exc: paired_design(rows)
    assert exc.value.category=='needs_information'


@pytest.mark.parametrize('defect',['duplicate_library','cross_unit_specimen'])
def test_known_misnested_membership_remains_invalid_not_missing(defect):
    rows=paired_rows()
    if defect=='duplicate_library': rows[1]['library_id']=rows[0]['library_id']
    else: rows[2]['specimen_id']=rows[0]['specimen_id']
    with pytest.raises(ValueError) as exc: paired_design(rows)
    assert exc.value.category=='invalid_design'
