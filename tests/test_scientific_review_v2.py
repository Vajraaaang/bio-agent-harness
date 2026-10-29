"""Offline /2 review integration, not model performance or scientific qualification."""
import hashlib
import json

import pytest

from tests.test_scientific_result_contract import api, packet, packet_record, judgment, judgment_record, write_artifact


def test_v2_prepare_reload_context_schema_record_and_reload(tmp_path):
    from devbench.analysis.scientific_review import (prepare_claim_packet, load_claim_packet,
        record_claim_judgment, load_claim_judgment)
    from devbench.analysis.scientific_review_agent import claim_review_context, claim_review_schema
    from jsonschema import Draft202012Validator
    p = packet(tmp_path)
    sealed = prepare_claim_packet(tmp_path/'packet',p,allow_evidence_read=True)
    manifest = tmp_path/'packet/audit/manifest.json'
    loaded = load_claim_packet(manifest,sealed['root_hash'],allow_evidence_read=True)
    assert loaded == p and loaded.record_format.endswith('/2')
    context = claim_review_context(loaded)
    assert str(tmp_path) not in json.dumps(context)
    assert context['presentation_rules'][0]['rule_id'] == 'effect'
    schema = claim_review_schema(context,reviewer_id='first')
    Draft202012Validator.check_schema(schema)
    j = judgment(p,({'rule_id':'effect','value':1.23},))
    Draft202012Validator(schema).validate(j.model_dump(mode='json'))
    assert not {'objective_checks','qc_policies','presentation_rules'} & schema['properties'].keys()
    recorded = record_claim_judgment(tmp_path/'judgment',manifest,expected_root=sealed['root_hash'],
        judgment=j,allow_evidence_read=True,allow_review=True)
    restored = load_claim_judgment(tmp_path/'judgment/audit/manifest.json',recorded['root_hash'],
        packet_manifest=manifest,packet_root=sealed['root_hash'],allow_evidence_read=True)
    assert restored == j


def test_reload_reverifies_result_bytes_and_typed_bindings(tmp_path):
    from devbench.analysis.scientific_review import prepare_claim_packet, load_claim_packet
    p = packet(tmp_path)
    sealed = prepare_claim_packet(tmp_path/'packet',p,allow_evidence_read=True)
    (tmp_path/'result.json').write_text('{}')
    with pytest.raises(ValueError,match='changed'):
        load_claim_packet(tmp_path/'packet/audit/manifest.json',sealed['root_hash'],allow_evidence_read=True)


def test_prepare_rejects_a_stale_plot_even_when_both_artifacts_have_valid_hashes(tmp_path):
    from devbench.analysis.scientific_review import prepare_claim_packet
    data = packet_record(tmp_path)
    png = tmp_path/'plot.png'
    png.write_bytes(b'fixture plot bytes, not a rendered scientific figure')
    plot = {'evidence_id':'plot','path':str(png),'sha256':hashlib.sha256(png.read_bytes()).hexdigest(),
            'kind':'plot','plot_verified':False}
    data['evidence'].append(plot)
    data['plot_bindings'] = [{'plot_evidence_id':'plot','result_evidence_id':'result',
        'result_sha256':'f'*64,'cohort_sha256':data['result_bindings'][0]['cohort_sha256'],
        'contrast_id':'treated-versus-control','feature_ids':['g1']}]
    with pytest.raises(ValueError):
        prepare_claim_packet(tmp_path/'packet',api().parse_claim_packet(data),allow_evidence_read=True)


def test_blinded_v2_context_rejects_protected_fields_in_result_artifacts(tmp_path):
    from devbench.analysis.scientific_review_agent import claim_review_context
    data = packet_record(tmp_path)
    result = json.loads((tmp_path/'result.json').read_text())
    result['prior_verdict'] = 'supported'
    data['evidence'][0] = write_artifact(tmp_path,'result.json',result,'result')
    with pytest.raises(ValueError): claim_review_context(api().parse_claim_packet(data))


def test_v2_reviewer_schema_binds_known_rules_and_rejects_self_selected_tolerance(tmp_path):
    from devbench.analysis.scientific_review_agent import claim_review_context,claim_review_schema
    from jsonschema import Draft202012Validator, ValidationError
    p = packet(tmp_path)
    schema = claim_review_schema(claim_review_context(p),reviewer_id='first')
    record = judgment_record(p,({'rule_id':'effect','value':1.23,'absolute_tolerance':1.},))
    with pytest.raises(ValidationError): Draft202012Validator(schema).validate(record)
    record = judgment_record(p,({'rule_id':'unknown','value':1.23},))
    with pytest.raises(ValidationError): Draft202012Validator(schema).validate(record)


@pytest.mark.parametrize('version',['devbench.scientific-claim-packet/3','unsupported'])
def test_unknown_packet_versions_fail_closed(tmp_path,version):
    data = packet_record(tmp_path)
    data['record_format'] = version
    with pytest.raises(ValueError): api().parse_claim_packet(data)


def test_mixed_packet_and_judgment_versions_are_rejected(tmp_path):
    from devbench.analysis.scientific_judgment import ClaimJudgment
    p = packet(tmp_path)
    data = judgment_record(p)
    data.pop('presentation_assertions')
    data['record_format'] = 'devbench.scientific-judgment/1'
    legacy = ClaimJudgment.model_validate_json(json.dumps(data))
    with pytest.raises(ValueError): api().validate_judgment(p,legacy)


def test_real_recorded_reviewer_dispatch_parses_v2_companion(tmp_path):
    from devbench.analysis.scientific_review import prepare_claim_packet
    from devbench.analysis.scientific_review_agent import request_review
    from devbench.analysis.episode_agent import Selection
    from tests.test_scientific_review import packet as advisory_packet, assessment
    outer, _ = advisory_packet(tmp_path)
    p = packet(tmp_path)
    companion = prepare_claim_packet(tmp_path/'companion',p,allow_evidence_read=True)
    class RecordedSelector:
        def configuration(self): return {'backend':'recorded_v2_synthetic_control'}
        def select(self,context,output,**kwargs):
            assert context['claim_review']['record_format'].endswith('/2')
            answer = {'kind':'scientific_review',**assessment(),
                      'claim_judgment':judgment(p,({'rule_id':'effect','value':1.23},)).model_dump(mode='json')}
            return Selection(answer,{'backend':'recorded_v2_synthetic_control','model_sessions_attempted':0,
                'tokens':None,'cost_usd':None,'actual_model':None})
    result = request_review(tmp_path/'review',outer['manifest_path'],expected_root=outer['root_hash'],
        reviewer_label='fixture-reviewer',selector=RecordedSelector(),allow_evidence_read=True,allow_review=True,
        claim_packet_manifest=tmp_path/'companion/audit/manifest.json',claim_packet_root=companion['root_hash'],
        claim_reviewer_id='first')
    assert result['status'] == 'review_recorded'
    assert result['model_sessions_attempted'] == 0 and result['claim_review_receipt']
    assert result['qualification_status'] == 'not_established'


@pytest.mark.parametrize('protected',[{'artifact_path':'/private/source'}, {'answers':{'expected':'supported'}},
    {'nested':{'prior_verdict':'supported'}}, {'capabilities':['unavailable_tool']},
    {'data_execution_granted':True}, {'approval_token':'fixture-not-a-credential'}])
def test_v2_never_projects_generic_diagnostic_paths_answers_or_capabilities(tmp_path,protected):
    data = packet_record(tmp_path)
    data['evidence'].append(write_artifact(tmp_path,'extra.json',protected,'extra'))
    with pytest.raises(ValueError): api().judgment_context(api().parse_claim_packet(data))


def test_v2_full_validation_precedes_bounded_reviewer_projection(tmp_path):
    from tests.test_scientific_result_contract import result_record
    result = result_record()
    result['family']['complete'] = False
    for i in range(2,200):
        result['family']['eligible_ids'].append('g'+str(i+1))
        result['rows'].append({**result['rows'][0],'feature_id':'g'+str(i+1)})
    p = api().parse_claim_packet(packet_record(tmp_path,result))
    context = api().judgment_context(p)
    diagnostic = context['diagnostics'][0]
    assert diagnostic['projection']['retained_rows'] == 200
    assert diagnostic['projection']['omitted_rows'] > 0
    assert len(diagnostic['content']['rows']) <= 32
    # A corrupted row beyond that projection must still fail full validation.
    result['rows'][-1]['missing_reasons'] = {'p_value':'fit_failed'}
    with pytest.raises(ValueError): api().judgment_context(api().parse_claim_packet(packet_record(tmp_path,result)))


def plot_record(root):
    data = packet_record(root)
    png = root/'plot.png'
    png.write_bytes(b'fixture-only pixel placeholder')
    data['evidence'].append({'evidence_id':'plot','path':str(png),
        'sha256':hashlib.sha256(png.read_bytes()).hexdigest(),'kind':'plot','plot_verified':False})
    spec = {'record_format':'devbench.scientific-plot-specification/1',
        'result_sha256':data['evidence'][0]['sha256'],
        'cohort_sha256':data['result_bindings'][0]['cohort_sha256'],
        'contrast_id':'treated-versus-control','feature_ids':['g1','g2'],
        'plot_type':'synthetic-scatter','x_metric':'effect','y_metric':'p_value','transformations':[]}
    spec_evidence = write_artifact(root,'specification.json',spec,'spec')
    data['evidence'].append(spec_evidence)
    binding = {key:spec[key] for key in ('result_sha256','cohort_sha256','contrast_id','feature_ids')}
    binding.update(plot_evidence_id='plot',result_evidence_id='result',specification_evidence_id='spec',
                   specification_sha256=spec_evidence['sha256'])
    data['plot_bindings'] = [binding]
    return data


def test_v2_current_plot_source_population_contrast_and_spec_bindings(tmp_path):
    data = plot_record(tmp_path)
    context = api().judgment_context(api().parse_claim_packet(data))
    assert context['plots'][0]['verified'] is False
    assert context['plot_bindings'][0]['specification_evidence_id'] == 'spec'


@pytest.mark.parametrize('field,value',[('cohort_sha256','e'*64),('contrast_id','reverse'),
    ('feature_ids',['not-retained']),('specification_sha256','e'*64)])
def test_v2_plot_binding_rejects_stale_identity_even_with_valid_image_hash(tmp_path,field,value):
    data = plot_record(tmp_path)
    data['plot_bindings'][0][field] = value
    with pytest.raises(ValueError): api().verified_artifacts(api().parse_claim_packet(data))


def test_v2_plot_image_alone_cannot_claim_verification(tmp_path):
    data = plot_record(tmp_path)
    data['plot_bindings'] = []
    data['evidence'][1]['plot_verified'] = True
    with pytest.raises(ValueError): api().verified_artifacts(api().parse_claim_packet(data))


def large_complete_result():
    from tests.test_scientific_result_contract import result_record
    result = result_record()
    result['family']['eligible_ids'] = ['g'+str(i) for i in range(3000)]
    result['rows'] = [{**result['rows'][0],'feature_id':key,'p_value':.5,'adjusted_p_value':.5}
                      for key in result['family']['eligible_ids']]
    return result


def test_large_complete_result_validates_all_bh_rows_before_small_preview(tmp_path):
    data = packet_record(tmp_path,large_complete_result())
    assert (tmp_path/'result.json').stat().st_size > 128_000
    context = api().judgment_context(api().parse_claim_packet(data))
    assert context['result_checks'][0]['multiplicity']['audited_count'] == 3000
    assert context['result_checks'][0]['multiplicity']['family_size'] == 3000
    diagnostic = context['diagnostics'][0]
    assert len(diagnostic['content']['rows']) <= 50
    assert len(diagnostic['content']['family']['eligible_ids']) <= 50
    assert diagnostic['projection']['omitted_rows'] == 3000-len(diagnostic['content']['rows'])
    assert len(json.dumps(context).encode()) < 128_000


def test_large_complete_family_corruption_beyond_preview_is_rejected(tmp_path):
    result = large_complete_result()
    result['rows'][-1]['adjusted_p_value'] = .6
    with pytest.raises(ValueError,match='BH'):
        api().judgment_context(api().parse_claim_packet(packet_record(tmp_path,result)))


def test_v2_result_and_packet_byte_limits_are_independent_and_enforced(tmp_path,monkeypatch):
    from devbench.analysis import scientific_result_contract as contracts
    data = packet_record(tmp_path)
    size = (tmp_path/'result.json').stat().st_size
    monkeypatch.setattr(contracts,'MAX_RESULT_BYTES',size)
    monkeypatch.setattr(contracts,'MAX_PACKET_EVIDENCE_BYTES',size)
    api().verified_artifacts(api().parse_claim_packet(data))
    extra = write_artifact(tmp_path,'extra.json',{'observed':1},'extra')
    data['evidence'].append(extra)
    with pytest.raises(ValueError): api().verified_artifacts(api().parse_claim_packet(data))
    monkeypatch.setattr(contracts,'MAX_PACKET_EVIDENCE_BYTES',size+1000)
    api().verified_artifacts(api().parse_claim_packet(data))
    monkeypatch.setattr(contracts,'MAX_RESULT_BYTES',size-1)
    with pytest.raises(ValueError): api().verified_artifacts(api().parse_claim_packet(data))


def test_unbound_generic_diagnostics_do_not_inherit_large_result_budget(tmp_path):
    data = packet_record(tmp_path)
    extra = write_artifact(tmp_path,'extra.json',{'observations':'x'*128_001},'extra')
    data['evidence'].append(extra)
    with pytest.raises(ValueError): api().verified_artifacts(api().parse_claim_packet(data))


def test_v1_diagnostic_artifact_budget_stays_128kb(tmp_path):
    from tests.test_scientific_judgment_contract import packet as legacy_packet
    from devbench.analysis.scientific_judgment import verified_artifacts
    legacy = legacy_packet(tmp_path)
    path = tmp_path/'large-v1.json'
    raw = json.dumps({'content':'x'*128_001}).encode()
    path.write_bytes(raw)
    evidence = legacy.evidence[0].model_copy(update={'path':str(path),'sha256':hashlib.sha256(raw).hexdigest()})
    with pytest.raises(ValueError): verified_artifacts(legacy.model_copy(update={'evidence':(evidence,)}))


def test_hash_and_typed_validation_use_same_captured_bytes(tmp_path,monkeypatch):
    from devbench.analysis import scientific_judgment
    p = packet(tmp_path)
    original = scientific_judgment._read
    calls=[]
    def capture_then_mutate(path,limit):
        raw=original(path,limit)
        calls.append(str(path))
        path.write_text('{}')
        return raw
    monkeypatch.setattr(scientific_judgment,'_read',capture_then_mutate)
    assert scientific_judgment.verified_artifacts(p)['result']['rows'][0]['effect']==1.23456
    assert len(calls)==1
    with pytest.raises(ValueError,match='changed'): scientific_judgment.verified_artifacts(p)


def test_plot_binding_does_not_automatically_certify_pixel_content(tmp_path):
    data=plot_record(tmp_path)
    data['evidence'][1]['plot_verified']=True
    context=api().judgment_context(api().parse_claim_packet(data))
    assert context['plots'][0]['declared_plot_verified'] is True
    assert context['plots'][0]['pixel_content_verified'] is False
    assert context['plots'][0]['verification_scope']=='byte_and_declared_source_specification_binding'


def test_review_implementation_fingerprint_covers_result_contract():
    from pathlib import Path
    from devbench.analysis.scientific_review_agent import _review_sources
    from devbench.analysis import scientific_result_contract
    raw=Path(scientific_result_contract.__file__).read_bytes()
    assert _review_sources()['devbench/analysis/scientific_result_contract.py']==hashlib.sha256(raw).hexdigest()


def test_real_workflow_configuration_dispatch_and_reconcile_accept_v2_packet(tmp_path):
    from tests.test_workflow_science_runtime import runtime_fixture
    from devbench.analysis.workflow_science import load_scientific_workflow_config
    from devbench.analysis.workflow_scientific_reviews import stage_material
    from devbench.analysis.episode_agent import Selection
    planner,runtime,base_selector,_=runtime_fixture(tmp_path)
    class V2Selector:
        def configuration(self):
            return {"backend": "recorded_fixture"}

        def select(self,*args,**kwargs):
            selected=base_selector.select(*args,**kwargs)
            selected.action['claim_judgment'].update(record_format='devbench.scientific-judgment/2',presentation_assertions=[])
            return Selection(selected.action,selected.evidence)
    selector=V2Selector()
    material=stage_material(planner.get('synthetic-session'),'candidate_suitability')
    claim=runtime.reviewer.claim_packet_builder(planner,'synthetic-session','candidate_suitability',material).model_dump(mode='json')
    claim.update(record_format='devbench.scientific-claim-packet/2',result_bindings=[],presentation_rules=[],qc_policies=[],plot_bindings=[])
    claim_path=tmp_path/'configured-claim-v2.json'
    claim_path.write_text(json.dumps(claim))
    config_path=tmp_path/'config-v2.json'
    config_path.write_text(json.dumps({'provider':'supplied_only','discovery_request':runtime.discovery_request,
        'review_policy':{},'reviewer':{'claim_packets':{'candidate_suitability':str(claim_path)},
            'reviewer_label':'first','evaluation_policy':runtime.reviewer.evaluation_policy.model_dump(mode='json'),
            'expected_policy_sha256':runtime.reviewer.expected_policy_sha256}}))
    configured=load_scientific_workflow_config(config_path,methods_root=planner.methods.root,selector=selector)
    evidence=configured.perform('candidate_review',planner,'synthetic-session',actor='fixture',selector=selector)
    assert evidence['status']=='accepted' and base_selector.calls==1
    assert configured.reconcile('candidate_review',planner,'synthetic-session',before_planner_hash=evidence['before_planner_hash'])==evidence
    assert base_selector.calls==1
    claim_path.write_text(claim_path.read_text()+'\n')
    with pytest.raises(ValueError,match='changed|input'):
        configured.verify('candidate_review',planner,'synthetic-session',evidence)


@pytest.mark.parametrize('protected',[{'allow_model_execution':True},{'allow_data_execution':True},
    {'source_uri':'file:///private/synthetic-input'},{'uri':'FILE:///private/synthetic-input'}])
def test_canonical_authority_flags_and_local_file_uris_never_enter_v2_context(tmp_path,protected):
    data=packet_record(tmp_path)
    data['evidence'].append(write_artifact(tmp_path,'extra.json',protected,'extra'))
    with pytest.raises(ValueError): api().judgment_context(api().parse_claim_packet(data))


def complete_gene_set_packet(root,kind,count,*,all_companions_large=False):
    from tests.test_scientific_result_contract import gene_set_packet_record
    data=gene_set_packet_record(root,kind)
    by_id={item['evidence_id']:item for item in data['evidence']}
    def retain(evidence_id,record):
        prior=by_id[evidence_id]
        updated=write_artifact(root,prior['path'].split('/')[-1],record,evidence_id)
        data['evidence'][next(i for i,item in enumerate(data['evidence']) if item['evidence_id']==evidence_id)]=updated
        by_id[evidence_id]=updated
        return updated
    source=json.loads((root/'result.json').read_text())
    features=['g'+str(i).zfill(5) for i in range(count)]
    source['family']['eligible_ids']=features
    source['rows']=[{**source['rows'][0],'feature_id':feature,'effect':float(count-i),
        'p_value':.5,'adjusted_p_value':.5} for i,feature in enumerate(features)]
    source_ref=retain('result',source)
    result=json.loads((root/'gene-sets.json').read_text())
    result['gene_set']['source_result_sha256']=source_ref['sha256']
    library=json.loads((root/'library.json').read_text())
    library['sets'][0]['feature_ids']=features if all_companions_large else features[:2]
    result['gene_set']['database']['sha256']=retain('library',library)['sha256']
    if kind=='ranked_enrichment':
        ranking=json.loads((root/'ranking.json').read_text())
        ranking.update(source_result_sha256=source_ref['sha256'],feature_ids=features,
            values=[row['effect'] for row in source['rows']])
        result['gene_set']['ranking']['sha256']=retain('ranking',ranking)['sha256']
    else:
        mapping=json.loads((root/'mapping.json').read_text())
        mapping['records']=[{'source_id':feature,'target_id':feature,'status':'mapped'} for feature in features]
        mapping_ref=retain('mapping',mapping)
        universe=json.loads((root/'universe.json').read_text())
        universe.update(source_result_sha256=source_ref['sha256'],eligible_ids=features,mapped_ids=features)
        universe['mapping']['sha256']=mapping_ref['sha256']
        result['gene_set']['universe']['sha256']=retain('universe',universe)['sha256']
        selection=json.loads((root/'selection.json').read_text())
        selection.update(source_result_sha256=source_ref['sha256'],feature_ids=features if all_companions_large else features[:1])
        result['gene_set']['selection']['sha256']=retain('selection',selection)['sha256']
    retain('gene-sets',result)
    return data


@pytest.mark.parametrize('kind,count,large_companions',[('ranked_enrichment',20_000,['ranking']),
    ('overrepresentation',3000,['mapping']),
    ('overrepresentation',20_000,['library','mapping','universe','selection'])])
def test_complete_gene_set_companions_receive_typed_bounded_admission_and_small_preview(tmp_path,kind,count,large_companions):
    data=complete_gene_set_packet(tmp_path,kind,count,all_companions_large=len(large_companions)>1)
    for evidence_id in large_companions:
        item=next(e for e in data['evidence'] if e['evidence_id']==evidence_id)
        assert len(open(item['path'],'rb').read())>128_000
    # Deliberately place companions before their referring result in evidence order.
    data['evidence']=data['evidence'][1:]+data['evidence'][:1]
    context=api().judgment_context(api().parse_claim_packet(data))
    assert len(json.dumps(context).encode())<128_000
    check=context['gene_set_checks'][0]
    assert check['ranked_count' if kind=='ranked_enrichment' else 'eligible_count']==count
    for diagnostic in context['diagnostics']:
        content=diagnostic['content']
        for key in ('rows','records','values','feature_ids','eligible_ids','mapped_ids'):
            if isinstance(content,dict) and isinstance(content.get(key),list):
                assert len(content[key])<=32


@pytest.mark.parametrize('companion',['ranking','mapping','universe','selection','library'])
def test_complete_gene_set_companion_corruption_beyond_preview_is_rejected(tmp_path,companion):
    kind='ranked_enrichment' if companion=='ranking' else 'overrepresentation'
    data=complete_gene_set_packet(tmp_path,kind,20_000,all_companions_large=True)
    record=json.loads((tmp_path/(companion+'.json')).read_text())
    result=json.loads((tmp_path/'gene-sets.json').read_text())
    if companion=='ranking': record['values'][-1]+=.5
    elif companion=='mapping': record['records'][-1]['target_id']='foreign-feature'
    elif companion=='universe': record['mapped_ids'][-1]='foreign-feature'
    elif companion=='selection': record['feature_ids'][-1]='foreign-feature'
    else:
        record['sets'][0]['feature_ids'][-1]='foreign-feature'
        result['method']['effect_scale']='overlap_count'
        result['rows'][0]['effect']=20_000
        data['result_bindings'][1]['effect_scale']='overlap_count'
    updated=write_artifact(tmp_path,companion+'.json',record,companion)
    data['evidence'][next(i for i,e in enumerate(data['evidence']) if e['evidence_id']==companion)]=updated
    if companion=='mapping':
        universe=json.loads((tmp_path/'universe.json').read_text())
        universe['mapping']['sha256']=updated['sha256']
        updated=write_artifact(tmp_path,'universe.json',universe,'universe')
        data['evidence'][next(i for i,e in enumerate(data['evidence']) if e['evidence_id']=='universe')]=updated
    field='universe' if companion=='mapping' else 'database' if companion=='library' else companion
    result['gene_set'][field]['sha256']=updated['sha256']
    data['evidence'][-1]=write_artifact(tmp_path,'gene-sets.json',result,'gene-sets')
    with pytest.raises(ValueError): api().verified_artifacts(api().parse_claim_packet(data))


def test_typed_companion_read_budgets_and_cumulative_accounting(tmp_path,monkeypatch):
    from pathlib import Path
    from devbench.analysis import scientific_judgment,scientific_result_contract
    data=complete_gene_set_packet(tmp_path,'ranked_enrichment',20_000)
    sizes={e['evidence_id']:Path(e['path']).stat().st_size for e in data['evidence']}
    original=scientific_judgment._read
    observed=[]
    def capture(path,limit):
        observed.append((path.name,limit))
        return original(path,limit)
    monkeypatch.setattr(scientific_judgment,'_read',capture)
    api().verified_artifacts(api().parse_claim_packet(data))
    assert dict(observed)['ranking.json']==16_000_000
    assert len(observed)==len(data['evidence'])
    monkeypatch.setattr(scientific_result_contract,'MAX_PACKET_EVIDENCE_BYTES',sum(sizes.values()))
    api().verified_artifacts(api().parse_claim_packet(data))
    monkeypatch.setattr(scientific_result_contract,'MAX_PACKET_EVIDENCE_BYTES',sum(sizes.values())-1)
    with pytest.raises(ValueError): api().verified_artifacts(api().parse_claim_packet(data))


def test_unreferenced_typed_looking_companion_keeps_generic_read_limit(tmp_path):
    from tests.test_scientific_result_contract import result_record
    data=packet_record(tmp_path)
    ranking={'record_format':'devbench.scientific-ranking/1','source_result_sha256':data['evidence'][0]['sha256'],
        'contrast_id':result_record()['contrast']['contrast_id'],'statistic':'effect','direction':'descending',
        'feature_ids':['g'+str(i) for i in range(20_000)],'values':list(range(20_000,0,-1))}
    data['evidence'].append(write_artifact(tmp_path,'unreferenced-ranking.json',ranking,'ranking'))
    with pytest.raises(ValueError): api().verified_artifacts(api().parse_claim_packet(data))
