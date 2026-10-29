"""Transport-only checks: synthetic result structures are not fitted evidence."""
import copy
import hashlib
import json

import pytest


def payload(count=3):
    genes=[f'ENSG{i:011d}' for i in range(count)]
    rows=[{'gene_id':gene,'hypothesis_id':gene,'estimate_status':'estimated',
           'coordinate_ids':['contrast'],'values':[.25],
           'intervals':[{'lower':-.1,'upper':.6,'confidence_level':.95}],
           'p_value':.2,'q_value':.4} for gene in genes]
    result={'target_results':[{'payload':{'gene_statistics':rows}}],
            'diagnostics':{'gene_count':count,'gene_test_adjustment_family':{'attempted_hypothesis_ids':genes},
                           'calibration':{'gene_estimates':{gene:{'dispersion_alpha':.1} for gene in genes}}},
            'method':{'count_model':'negative_binomial_empirical_bayes_dispersion'}}
    events=[{'tool_name':'bulk_condition_contrast','arguments':{'public_task':{'gene_ids':genes}},
             'started_at_utc':'fixture-start','finished_at_utc':'fixture-end','elapsed_sec':.5,
             'succeeded':True,'result':copy.deepcopy(result)}]
    return {'format':'devbench.fastq-count-model/1','core_result':result,
            'diagnostics':result['diagnostics'],'registered_tool_events':events,
            'serialization_fixture_only':True}


def test_complete_core_result_is_retained_once_and_event_reference_verifies():
    from devbench.analysis.fastq_paired_model import _serialize_paired_provenance
    original=payload();expected=copy.deepcopy(original)
    body=_serialize_paired_provenance(original);retained=json.loads(body)
    assert original==expected
    assert retained['core_result']==original['core_result']
    assert 'diagnostics' not in retained
    for event,source in zip(retained['registered_tool_events'],original['registered_tool_events']):
        assert 'result' not in event
        assert {k:v for k,v in event.items() if k!='result_reference'}=={k:v for k,v in source.items() if k!='result'}
        reference=event['result_reference']
        canonical=json.dumps(retained['core_result'],sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        assert reference=={'json_pointer':'#/core_result','sha256':hashlib.sha256(canonical).hexdigest(),
                           'size_bytes':len(canonical),'encoding':'json_sorted_keys_compact_utf8'}
    assert retained['diagnostics_reference']['json_pointer']=='#/core_result/diagnostics'
    assert body.count(b'"gene_statistics"')==1
    assert b'\n  ' not in body


def test_mismatched_actual_tool_event_result_cannot_reference_another_result():
    from devbench.analysis.fastq_paired_model import _serialize_paired_provenance
    value=payload();value['registered_tool_events'][0]['result']['target_results'][0]['payload']['gene_statistics'][0]['p_value']=.9
    with pytest.raises(ValueError,match='event result'):
        _serialize_paired_provenance(value)


def test_complete_64102_row_serialization_does_not_drop_genes():
    from devbench.analysis.fastq_paired_model import _serialize_paired_provenance
    value=payload(64102);body=_serialize_paired_provenance(value);actual=json.loads(body)
    genes=actual['core_result']['diagnostics']['gene_test_adjustment_family']['attempted_hypothesis_ids']
    assert len(genes)==64102
    assert [r['gene_id'] for r in actual['core_result']['target_results'][0]['payload']['gene_statistics']]==genes
    assert len(body)<100_000_000
