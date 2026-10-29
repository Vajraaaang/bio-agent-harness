"""FASTQ direct-count transport into the registered paired bulk core.

No estimator, normalization, dispersion, contrast or BH implementation lives here.
"""
import csv
import hashlib
import importlib.metadata
import io
import json
import math
from pathlib import Path
import tempfile

from .fastq_analysis_policy import FastqAdmissionError, validate_analysis_policy


def check_paired_model():
    root=Path(__file__).resolve().parents[1]
    sources=['analysis/fastq_paired_model.py','study/biology_tools_bulk.py','study/biology_tool_io.py']
    files=[root/name for name in sources]
    files += [root.parent/'devharness_server'/name for name in ('tools_bulk.py','normalization.py','tools_stat.py','dispatcher.py')]
    files += [root.parent/'devharness_core'/'units.py']
    return {'package':'devbench_existing_bulk_core','version':'registered_bulk_condition_contrast/1',
            'source_sha256':{str(p.relative_to(root.parent)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
            'dependency_versions':{name:importlib.metadata.version(name) for name in ('numpy','scipy','statsmodels','pydeseq2')}}


def _task(design, genes):
    contrast=design['contrast'];unit=design['biological_unit']
    roles={'condition':contrast['factor'],'population_unit':unit,'batches':design['batch_columns'],
           'blocking':[unit],'time':None,'dose':None,'covariate':None}
    return {'measurement':'raw_counts','population_unit_label':unit,'observation_id_label':'sample_id',
            'gene_ids':genes,'unit_hierarchy':{'population_unit':design['core_biological_unit_type'],
              'population_unit_label':unit,'specimen_label':'specimen_id','library_label':'library_id','identity_status':'declared'},
            'analysis_columns':roles,'context':{'biological_unit':design['core_biological_unit_type'],
              'condition_labels':[contrast['denominator'],contrast['numerator']],
              'requested_condition_contrast':{k:contrast[k] for k in ('numerator','denominator')}},
            'questions':[{'target_id':'mean_contrast','estimand':'Condition mean contrast over observed biological units',
              'units':'log2 count-rate contrast','contrast':contrast['numerator']+' minus '+contrast['denominator'],
              'reference_population':'observed biological units','prediction_reference':'observed biological units'}]}


def _serialize_paired_provenance(provenance):
    """Retain one full result and bind actual captured events to that object."""
    def canonical(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

    result=provenance["core_result"]
    result_bytes=canonical(result)
    result_hash=hashlib.sha256(result_bytes).hexdigest()
    reference={"json_pointer":"#/core_result", "sha256":result_hash,
               "size_bytes":len(result_bytes), "encoding":"json_sorted_keys_compact_utf8"}
    events=[]
    for event in provenance["registered_tool_events"]:
        if (event.get("tool_name")!="bulk_condition_contrast" or event.get("succeeded") is not True
                or "result" not in event
                or hashlib.sha256(canonical(event["result"])).hexdigest()!=result_hash):
            raise ValueError("Captured registered tool event result differs from the retained core result")
        events.append({**{key:value for key,value in event.items() if key!="result"},
                       "result_reference":dict(reference)})
    if not events:
        raise ValueError("Missing captured registered tool event result")
    retained={key:value for key,value in provenance.items() if key not in {"registered_tool_events","diagnostics"}}
    if "diagnostics" in provenance:
        diagnostics=canonical(result["diagnostics"])
        if canonical(provenance["diagnostics"])!=diagnostics:
            raise ValueError("Duplicated diagnostics differ from the retained core result")
        retained["diagnostics_reference"]={"json_pointer":"#/core_result/diagnostics",
            "sha256":hashlib.sha256(diagnostics).hexdigest(),"size_bytes":len(diagnostics),
            "encoding":"json_sorted_keys_compact_utf8"}
    retained["registered_tool_events"]=events
    return canonical(retained)+b"\n"


def fit_paired_counts(counts_path, design, output_dir, *, alpha=.05, analysis_policy=None):
    from .fastq_count_model import _absolute_path,_count_bytes,_parse_count_matrix,_artifact
    from devharness_server.dispatcher import ToolDispatcher,capture_tool_events
    from scipy.stats import norm
    if type(alpha) not in {int,float} or not math.isfinite(alpha) or not 0<alpha<1:
        raise ValueError('Declare finite alpha strictly between zero and one')
    analysis_policy=validate_analysis_policy(analysis_policy,paired=True)
    identity=check_paired_model()
    output=_absolute_path(output_dir,exists=False)
    if output.exists() or not output.parent.is_dir():
        raise ValueError('Count-model output requires a fresh directory under an existing parent')
    path=_absolute_path(counts_path);body=_count_bytes(path)
    counts=_parse_count_matrix(body,design['sample_ids'])
    options={'condition_col':design['contrast']['factor'],'reference_condition':design['contrast']['denominator'],
             'comparison_condition':design['contrast']['numerator'],'batch_cols':design['batch_columns'],
             'block_cols':design['blocking'],'minimum_units':3,'condition_number_limit':1000.0}
    with tempfile.TemporaryDirectory(prefix='.paired-count-',dir=output.parent) as temporary:
        work=Path(temporary).resolve();count_csv=work/'counts.csv';metadata=work/'metadata.csv'
        counts.T.rename_axis('gene_id').to_csv(count_csv)
        with metadata.open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(design['metadata_rows'][0]))
            writer.writeheader();writer.writerows(design['metadata_rows'])
        with capture_tool_events() as events:
            result=json.loads(ToolDispatcher(allowed_root=work).execute('bulk_condition_contrast',{
                'counts_path':str(count_csv),'metadata_path':str(metadata),
                'public_task':_task(design,list(counts.columns)),'options':options}))
        fragments=result.get('target_results',[])
        if len(fragments)!=1 or not isinstance(fragments[0].get('payload'),dict):
            raise FastqAdmissionError('Registered bulk core did not estimate the declared contrast: '+str(result.get('diagnostics',{}).get('analysis_error',fragments)),category='unsupported_analysis',code='paired_core_refusal')
        core_rows=fragments[0]['payload']['gene_statistics']
        if [row['gene_id'] for row in core_rows]!=list(counts.columns):
            raise ValueError('Registered bulk core changed the complete gene family')
        diagnostics=result['diagnostics'];actual=diagnostics['designs'][0]
        if (actual['rank']!=design['rank'] or actual['residual_df']!=design['residual_df']
                or actual['block_columns']!=design['blocking']
                or actual['reference_condition']!=design['contrast']['denominator']
                or actual['comparison_condition']!=design['contrast']['numerator']):
            raise ValueError('Registered bulk execution differs from retained paired design')
        rows=[]
        for item in core_rows:
            estimated=item['estimate_status']=='estimated'
            effect=item['values'][0] if estimated else None
            interval=item['intervals'][0] if estimated else None
            se=(interval['upper']-interval['lower'])/(2*float(norm.ppf(.975))) if estimated else None
            calibration=diagnostics.get('calibration',{}).get('gene_estimates',{}).get(item['gene_id'],{})
            rows.append({'gene_id':item['gene_id'],'baseMean':None,'log2FoldChange':effect,'lfcSE':se,
                         'stat':effect/se if estimated and se and se>0 else None,
                         'pvalue':item['p_value'],'padj':item['q_value'],
                         'dispersion':calibration.get('dispersion_alpha'),
                         'status':'ok' if estimated else 'fit_failed','missing_reason':item.get('missing_reason',''),
                         'lfc_converged':estimated})
        tested=sum(row['pvalue'] is not None for row in rows)
        if not tested: raise ValueError('Registered paired core returned no valid tests')
        buffer=io.StringIO(newline='');writer=csv.DictWriter(buffer,fieldnames=list(rows[0]),delimiter='\t',lineterminator='\n')
        writer.writeheader();writer.writerows(rows);table_body=buffer.getvalue().encode()
        table=_artifact(output/'differential_expression.tsv',table_body);count_artifact=_artifact(path,body)
        family={'emitted_gene_count':len(rows),'result_row_count':len(rows),'tested_gene_count':tested,
                'failed_or_suppressed_gene_count':len(rows)-tested,'complete':True,
                'bh_family_count':len(rows),'bh_family_gene_ids':list(counts.columns),
                'gene_ids_sha256':hashlib.sha256(json.dumps(list(counts.columns),separators=(',',':')).encode()).hexdigest()}
        policy={'alpha':float(alpha),'adjustment':'benjamini_hochberg','independent_filtering':False,
                'failed_hypothesis_policy':'internal_p_one_retains_failed_hypotheses_public_p_q_null',
                'adjustment_implementation':'existing_registered_bulk_core_unchanged',
                'lfc_shrinkage':False,'count_replacement':False,'normalization':diagnostics['normalization']['method'],
                'test':'existing_bulk_two_sided_Wald','baseMean':'not_exported_by_existing_core',
                'lfcSE':'reconstructed_from_existing_core_95_percent_normal_interval'}
        if analysis_policy is not None: policy['analysis_policy']=analysis_policy
        provenance={'format':'devbench.fastq-count-model/1','status':'completed','model':identity,
                    'input_representation':'direct_integer_STAR_GeneCounts','design':design,
                    'count_matrix':count_artifact,'differential_expression':table,'family':family,
                    'normalization':diagnostics['normalization'],'diagnostics':diagnostics,
                    'core_result':result,'registered_tool_events':events,'numerical_policy':policy,
                    'registered_transport':{'counts_csv_sha256':hashlib.sha256(count_csv.read_bytes()).hexdigest(),
                                            'metadata_csv_sha256':hashlib.sha256(metadata.read_bytes()).hexdigest(),
                                            'retention':'transient_lossless_conversion_from_retained_counts_and_metadata'},
                    'evidence':{'execution_success':True,'scientific_correctness':None,'numerical_replay':'not_run',
                                'biological_agreement':'not_run','model_sessions':0,'clinical_release_authorized':False}}
        if identity!=check_paired_model(): raise ValueError('Paired core sources changed during execution')
        provenance_body=_serialize_paired_provenance(provenance)
        staged=work/'artifacts';staged.mkdir()
        (staged/'differential_expression.tsv').write_bytes(table_body)
        (staged/'provenance.json').write_bytes(provenance_body)
        if output.exists(): raise ValueError('Count-model output created concurrently')
        staged.rename(output)
    return {'format':provenance['format'],'status':'completed','formula':design['formula'],'contrast':design['contrast'],
            'model':identity,'count_matrix':count_artifact,'differential_expression':table,'family':family,
            'numerical_policy':policy,'provenance':_artifact(output/'provenance.json',provenance_body)}
