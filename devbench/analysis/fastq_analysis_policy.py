"""Versioned, opt-in policies frozen before fitting; defaults remain unchanged."""
from copy import deepcopy
import math

FORMAT = 'devbench.fastq-analysis-policy/1'


class FastqAdmissionError(ValueError):
    """Machine-readable admission outcome with legacy ValueError compatibility."""
    def __init__(self, reason, *, category='invalid_design', code=None):
        if category not in {'invalid_design','needs_information','unsupported_analysis','input_error'}:
            raise ValueError('Unknown FASTQ admission category')
        super().__init__(reason)
        self.category = category
        self.code = code or category
        self.status = {'invalid_design':'refused','unsupported_analysis':'unsupported'}.get(category,category)

    def as_dict(self):
        return {'category':self.category,'code':self.code,'reason':str(self),'status':self.status}


def validate_analysis_policy(value=None, *, paired=False):
    if value is None:
        return None
    if (type(value) is not dict or value.get('format') != FORMAT
            or set(value)-{'format','lfc_shrinkage','independent_filter'}):
        raise FastqAdmissionError('An explicit versioned analysis_policy is required',category='unsupported_analysis')
    policy=deepcopy(value)
    if 'lfc_shrinkage' in policy and policy['lfc_shrinkage'] != {'method':'apeglm'}:
        raise FastqAdmissionError('Only pinned PyDESeq2 apeGLM shrinkage is supported',category='unsupported_analysis')
    if 'independent_filter' in policy:
        filtering=policy['independent_filter']
        if (type(filtering) is not dict or set(filtering)!={'method','threshold'}
                or filtering['method']!='fixed_base_mean' or type(filtering['threshold']) not in {int,float}
                or not math.isfinite(filtering['threshold']) or filtering['threshold']<0):
            raise FastqAdmissionError('Independent filtering requires a frozen nonnegative base-mean threshold',category='unsupported_analysis')
    if paired and set(policy)-{'format'}:
        raise FastqAdmissionError('Paired existing-core fitting does not support optional filtering or apeGLM shrinkage; no policy is silently dropped',category='unsupported_analysis',code='paired_optional_policy_unsupported')
    return policy


def apply_independent_filter(rows, filtering):
    """Keep original tests but assign q only in the explicitly eligible BH family."""
    from scipy.stats import false_discovery_control
    family=[]
    for row in rows:
        eligible=row['baseMean'] is not None and row['baseMean']>=filtering['threshold']
        row['filter_eligible']=eligible
        row['filter_status']='eligible' if eligible else 'below_frozen_base_mean_threshold'
        row['padj_filtered']=None
        if eligible and row['pvalue'] is not None:
            family.append(row)
    if family:
        for row,q in zip(family,false_discovery_control([r['pvalue'] for r in family],method='bh'),strict=True):
            row['padj_filtered']=max(row['pvalue'],float(q))
    return {'method':'fixed_base_mean','threshold':filtering['threshold'],
            'threshold_source':'explicit_plan_policy_before_fit',
            'eligible_gene_count':sum(row['filter_eligible'] for row in rows),
            'bh_family_gene_ids':[row['gene_id'] for row in family],
            'bh_family_count':len(family),'excluded_row_policy':'retain_original_effect_pvalue_padj_null_padj_filtered'}


def add_shrunken_effects(rows, statistics, design):
    """Shrink only the exact numerator-minus-denominator coefficient, never tests."""
    import numpy as np
    import pandas as pd
    coefficient=f"{design['contrast']['factor']}[{design['contrast']['numerator']}]"
    if coefficient not in design['design_columns']:
        raise FastqAdmissionError('Shrinkage requires the exact retained contrast coefficient',category='unsupported_analysis')
    index=design['design_columns'].index(coefficient)
    expected=np.zeros(len(design['design_columns']));expected[index]=1
    if not np.array_equal(expected,design['contrast_vector']) or list(statistics.LFC.columns)!=design['design_columns']:
        raise FastqAdmissionError('Shrinkage coefficient does not match retained contrast',category='invalid_design')
    original=statistics.inference.lfc_shrink_nbinom_glm
    captured=[]
    prior_parameters={}
    def capture(**kwargs):
        prior_parameters.update(prior_scale=float(kwargs["prior_scale"]), prior_no_shrink_scale=float(kwargs["prior_no_shrink_scale"]))
        output=original(**kwargs)
        captured.append(output)
        return output
    statistics.inference.lfc_shrink_nbinom_glm=capture
    try:
        statistics.lfc_shrink(coeff=coefficient,adapt=True)
    finally:
        statistics.inference.lfc_shrink_nbinom_glm=original
    if len(captured)!=1:
        raise ValueError('Pinned shrinkage did not expose its numerical result')
    lfcs,hessians,convergence=captured[0]
    numerical={gene:(np.isfinite(lfc[index]) and np.isfinite(hessian[index,index]) and hessian[index,index]!=0)
               for gene,lfc,hessian in zip(statistics.dds.non_zero_genes,lfcs,hessians,strict=True)}
    for row in rows:
        gene=row['gene_id'];flag=statistics._LFC_shrink_converged.loc[gene]
        valid=(row['log2FoldChange'] is not None and not pd.isna(flag) and bool(flag)
               and numerical.get(gene,False))
        value=float(statistics.results_df.loc[gene,'log2FoldChange'])
        se=float(statistics.results_df.loc[gene,'lfcSE'])
        valid=valid and math.isfinite(value) and math.isfinite(se) and se>0
        row.update(log2FoldChange_shrunk=value if valid else None,lfcSE_shrunk=se if valid else None,
                   shrinkage_status='estimated' if valid else 'not_estimable_or_not_converged')
    return {'method':'pinned_PyDESeq2_apeglm','coefficient':coefficient,'adapt':True,
            'prior':'native_adaptive_heavy_tailed_Cauchy','original_wald_tests_retained':True,
            'prior_selection':'native_empirical_adaptation_declared_before_fit',
            'successful_gene_count':sum(r['shrinkage_status']=='estimated' for r in rows), **prior_parameters}
