"""Synthetic retained-byte controls; no actual airway fits or adapter acceptance."""
from pathlib import Path
import importlib.util
import hashlib
import io
import tarfile

import pytest


def api():
    assert importlib.util.find_spec('devbench.analysis.rna_airway_native_outputs') is not None, 'native-output validator is missing'
    from devbench.analysis import rna_airway_native_outputs
    return rna_airway_native_outputs


HEADER = 'feature_id\tbaseMean\tlog2FoldChange\tlfcSE\tstat\tpvalue\tpadj\n'


def table(tmp_path, rows):
    path = tmp_path / 'native.tsv'
    path.write_text(HEADER + ''.join('\t'.join(row) + '\n' for row in rows))
    return path


def test_native_na_retains_finite_lfc_p_and_standard_error_without_ci_or_bh(tmp_path):
    path = table(tmp_path, [['g1','12','-1.5','0.25','-6','0.0001','NA'], ['g2','0','NA','NA','NA','NA','NA']])
    result = api().validate_native_tsv(path, expected_gene_ids=['g1','g2'])
    assert result['rows'] == 2
    assert result['missing']['padj'] == 2 and result['missing']['pvalue'] == 1
    assert result['missing']['log2FoldChange'] == 1 and result['missing']['lfcSE'] == 1
    assert result['uncertainty'] == 'native_standard_error_not_confidence_interval'
    assert result['BH_recomputed'] is False
    assert result['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('attack', ['duplicate','reordered','missing','extra','q_below_p','q_without_p','nonfinite','negative_SE','p_outside','empty','extra_column'])
def test_native_full_family_and_probability_failures_are_preserved_as_rejection(tmp_path, attack):
    rows = [['g1','12','-1.5','0.25','-6','0.01','0.1'], ['g2','2','1','0.3','3.3','0.1','0.2']]
    expected = ['g1','g2']
    if attack == 'duplicate': rows[1][0] = 'g1'
    elif attack == 'reordered': rows.reverse()
    elif attack == 'missing': rows.pop()
    elif attack == 'extra': rows.append(['g3','1','0','1','0','1','1'])
    elif attack == 'q_below_p': rows[0][-1] = '0.0001'
    elif attack == 'q_without_p': rows[0][-2] = 'NA'
    elif attack == 'nonfinite': rows[0][2] = 'Inf'
    elif attack == 'negative_SE': rows[0][3] = '-1'
    elif attack == 'p_outside': rows[0][-2] = '1.1'
    elif attack == 'empty': rows[0][2] = ''
    else: rows[0].append('unexpected')
    with pytest.raises(ValueError): api().validate_native_tsv(table(tmp_path, rows), expected_gene_ids=expected)


def archive(tmp_path, members, *, special=None):
    path = tmp_path / 'support.tar.gz'
    with tarfile.open(path, 'w:gz', format=tarfile.USTAR_FORMAT) as handle:
        for name, body in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(body)
            handle.addfile(member, io.BytesIO(body))
        if special:
            handle.addfile(special)
    return path


def test_support_archive_verifies_full_opaque_inventory_without_loading_rds(tmp_path):
    members = {'state.rds': b'opaque not executed', 'ids.txt': b'g1\ng2\n'}
    path = archive(tmp_path, members)
    expected = [{'name': name, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()} for name, body in members.items()]
    actual = api().read_native_support_archive(path, expected_archive_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), expected_members=expected)
    assert actual == members


@pytest.mark.parametrize('attack', ['escape','directory','symlink','missing','extra','hash','duplicate'])
def test_support_archive_never_follows_paths_or_infers_missing_members(tmp_path, attack):
    members = {'state.rds': b'opaque'}
    special = None
    if attack == 'escape': members = {'../outside': b'bad'}
    elif attack == 'directory':
        special = tarfile.TarInfo('directory'); special.type = tarfile.DIRTYPE
    elif attack == 'symlink':
        special = tarfile.TarInfo('link'); special.type = tarfile.SYMTYPE; special.linkname = '/tmp/elsewhere'
    elif attack == 'duplicate':
        special = tarfile.TarInfo('state.rds'); special.size = 0
    path = archive(tmp_path, members, special=special)
    expected = [{'name':'state.rds','bytes':6,'sha256':hashlib.sha256(b'opaque').hexdigest()}]
    if attack == 'missing': expected.append({'name':'missing','bytes':1,'sha256':'a'*64})
    if attack == 'extra': expected = []
    if attack == 'hash': expected[0]['sha256'] = '0'*64
    with pytest.raises(ValueError): api().read_native_support_archive(path, expected_archive_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), expected_members=expected)


def native_case(tmp_path, monkeypatch):
    import json
    from devharness_core.provenance import canonical_json
    from devbench.analysis.checkpoints import digest
    from devbench.analysis import rna_comparison_outputs as comparison
    from devbench.analysis.rna_execution_inputs import _source
    from tests.test_extensible_methods import service, custom
    module = api()
    methods = service(tmp_path)
    run_id = 'e' * 32
    genes = ['g1','g2']
    samples = ['s1','s2']
    def register(name, body, kind='file'):
        path = tmp_path / name
        path.write_bytes(body)
        return methods._import_artifact(path, artifact_type=kind, actor='synthetic-fixture', generated_by=run_id)
    matrix = register('matrix.json', canonical_json({'gene_ids': genes,'sample_ids':samples,'matrix':[[1,2],[3,4]]}), 'json')
    contract, code, lock = custom(tmp_path)
    contract['method_id'] = 'original-airway-2015-full-processed-v1'
    method = methods.register_method(contract, source=code, environment_lock=lock, actor='fixture')
    chunks = [{'chunk_id':'chunk-'+str(i),'code_file':str(i)+'.R','code_sha256':str(i)*64,'author_eval':True} for i in range(1,5)]
    manifest = {'format':'devbench.airway-original-workflow/1','source':{'sha256':'a'*64},'chunks':chunks,
        'hypotheses':[{'hypothesis_id':identity,'family_id':'airway2015-'+identity,'source_chunk_id':chunks[i]['chunk_id'],'native_table_file':identity+'.native.tsv'} for i,identity in enumerate(module.HYPOTHESES.values())]}
    body = (HEADER+'g1\t12\t-1.5\t0.25\t-6\t0.01\tNA\ng2\t2\t1\t0.3\t3.3\t0.1\t0.2\n').encode()
    support = {'original-row-ranges.rds':b'opaque ranges','sample-ids.txt':b's1\ns2\n','input-gene-ids.txt':b'g1\ng2\n','retained-gene-ids.txt':b'g1\ng2\n','initial-rng.json':b'{"state":null}','dex-default.annotated.tsv':b'feature_id\tsymbol\ng1\tGENE1\n'}
    receipts = [{'chunk_id':c['chunk_id'],'code_sha256':c['code_sha256'],'author_eval':True,'status':'completed','error':None,'stdout':[]} for c in chunks]
    support['execution-receipts.json'] = canonical_json({'format':'devbench.airway-execution-receipts/1','chunks':receipts,'original_disabled_chunks_were_not_executed':True,'limitations':'Synthetic only'})
    support['chunk-receipts.jsonl'] = b''.join(canonical_json(r)+b'\n' for r in receipts)
    outputs = {}
    declared = []
    for i,(port,identity) in enumerate(module.HYPOTHESES.items()):
        ref = register(identity+'.native.tsv',body,'table'); outputs[port] = ref['artifact_id']
        support[identity+'.native.rds'] = b'opaque result'
        support[identity+'.family-state.rds'] = b'opaque NA state'
        support[identity+'.family.json'] = canonical_json({'format':'devbench.airway-native-family/1','hypothesis_id':identity,'source_chunk_id':chunks[i]['chunk_id'],'source_chunk_sha256':chunks[i]['code_sha256'],'family_id':'airway2015-'+identity,'native_table_sha256':ref['sha256'],'p_missing':0,'q_missing':1,'native_rows':2,'recomputed_BH':False,'invented_confidence_intervals':False,'parameter_attribution':'authors_procedure_not_user_selected','limitations':'Fixture'})
        declared.append({'hypothesis_id':identity,'family_id':'airway2015-'+identity,'table_path':identity+'.native.tsv','table_sha256':ref['sha256']})
    manifest['native_support_members'] = sorted(support)
    manifest_path = tmp_path/'authored-workflow.json'; manifest_path.write_bytes(canonical_json(manifest)); manifest_ref = _source(manifest_path)
    expectation_path = tmp_path/'expectation.json'; expectation_path.write_bytes(canonical_json({'workflow_sha256':manifest_ref['sha256'],'workflow_manifest_ref':manifest_ref,'expected_plots':{},'required_image_formats':['png','pdf']})); expectation_ref = _source(expectation_path)
    archive_path = archive(tmp_path,support)
    archive_ref = methods._import_artifact(archive_path,artifact_type='file',actor='fixture',generated_by=run_id); outputs['native_support'] = archive_ref['artifact_id']
    for port in ('actual_session','airway_figures'):
        outputs[port] = register(port+'.bin',b'opaque fixture')['artifact_id']
    outputs['reader_audit'] = register('reader-audit.json', b'{"fixture":"opaque unqualified"}', 'json')['artifact_id']
    result = {'format':'devbench.airway-full-native-result/1','workflow_id':contract['method_id'],'workflow_sha256':manifest_ref['sha256'],'retained_figures':{},'native_support':{'archive_port':'native_support','sha256':archive_ref['sha256'],'members':[{'name':name,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()} for name,b in support.items()]},'native_hypotheses':declared,'canonical_gene_table_status':'unqualified','original_eval_false_extensions':'retained_disabled_not_claimed_executed','limitations':'Synthetic fake trust seams only'}
    outputs['result_manifest'] = register('result.json',canonical_json(result),'json')['artifact_id']
    run = {'run_id':run_id,'status':'succeeded','method_version_id':method['version_id'],'parameters':{'operation':'full_analysis','source_sha256':'a'*64,'workflow_sha256':manifest_ref['sha256'],'row_ranges_sha256':hashlib.sha256(support['original-row-ranges.rds']).hexdigest(),'retained_figure_expectation_ref':expectation_ref},'outputs':outputs,'evidence':{'root_hash':'f'*64},'execution':{'exit_code':0,'runtime':'local_docker','image':contract['environment']['image'],'image_id':'sha256:'+'a'*64}}
    bridge = {'format':'devbench.rna-original-airway-execution-inputs/3','original_refs':{'matrix':matrix}}
    monkeypatch.setattr(comparison,'_plan',lambda *args: ({},{},{},bridge))
    monkeypatch.setattr(comparison,'_runs',lambda *args: [run])
    monkeypatch.setattr(comparison,'_run_start_plan_binding',lambda *args:{'status':'governed_plan_identity_before_dispatch'})
    return module,methods,run,manifest_ref,bridge


def test_native_full_bundle_recomputes_all_four_families_without_claiming_rds_semantics(tmp_path,monkeypatch):
    module,methods,run,manifest_ref,bridge = native_case(tmp_path,monkeypatch)
    result = module.validate_airway_native_result(run['run_id'],{},manifest_ref,artifact_service=methods,workspace=tmp_path)
    assert len(result['hypotheses']) == 4
    assert all(table['validation']['missing']['padj'] == 1 for table in result['hypotheses'])
    assert result['workflow_manifest_ref'] == manifest_ref
    assert any('Opaque' in item for item in result['limitations'])


@pytest.mark.parametrize('attack',['failed','wrong_format','postrun_plan','fake_runtime','wrong_image','wrong_operation','foreign_locator','missing_output'])
def test_native_gov_run_uncertainty_rejects_before_numeric_authority(tmp_path,monkeypatch,attack):
    from devbench.analysis import rna_comparison_outputs as comparison
    module,methods,run,manifest_ref,bridge = native_case(tmp_path,monkeypatch)
    if attack == 'failed': run['status'] = 'failed'
    elif attack == 'wrong_format': bridge['format'] = 'devbench.rna-execution-inputs/2'
    elif attack == 'postrun_plan': monkeypatch.setattr(comparison,'_run_start_plan_binding',lambda *args:{'status':'retrospective_unbound_assessment_plan'})
    elif attack == 'fake_runtime': run['execution']['runtime'] = 'synthetic_fixture'
    elif attack == 'wrong_image': run['execution']['image'] = 'other:tag'
    elif attack == 'wrong_operation': run['parameters']['operation'] = 'inspect_original_input'
    elif attack == 'foreign_locator': manifest_ref = {**manifest_ref,'path':str(tmp_path/'other.json')}
    else: run['outputs'].pop('native_cell')
    with pytest.raises((ValueError,PermissionError)):
        module.validate_airway_native_result(run['run_id'],{},manifest_ref,artifact_service=methods,workspace=tmp_path)


@pytest.mark.parametrize('attack',['wrong_mask_count','wrong_chunk','user_attribution','BH_recomputed','missing_RDS','extra_runtime_file','chunk_failed','wrong_ranges'])
def test_resealed_native_support_cannot_change_hypothesis_state_or_execution_history(tmp_path,monkeypatch,attack):
    import json
    from devharness_core.provenance import canonical_json
    module,methods,run,manifest_ref,bridge = native_case(tmp_path,monkeypatch)
    result_ref = methods.get('artifact',run['outputs']['result_manifest'])
    result = json.loads(methods.artifact_path(result_ref['artifact_id']).read_bytes())
    archive_ref = methods.get('artifact',run['outputs']['native_support'])
    support = module.read_native_support_archive(methods.artifact_path(archive_ref['artifact_id']),expected_archive_sha256=archive_ref['sha256'],expected_members=result['native_support']['members'])
    family = json.loads(support['dex-default.family.json'])
    if attack == 'wrong_mask_count': family['q_missing'] = 0
    elif attack == 'wrong_chunk': family['source_chunk_sha256'] = '0'*64
    elif attack == 'user_attribution': family['parameter_attribution'] = 'user_prespecified'
    elif attack == 'BH_recomputed': family['recomputed_BH'] = True
    elif attack == 'missing_RDS': support.pop('dex-default.family-state.rds')
    elif attack == 'extra_runtime_file': support['unknown.tmp'] = b'arbitrary'
    elif attack == 'chunk_failed':
        receipts = json.loads(support['execution-receipts.json']); receipts['chunks'][0]['status'] = 'failed'
        support['execution-receipts.json'] = canonical_json(receipts)
    else: support['original-row-ranges.rds'] = b'different unqualified ranges'
    support['dex-default.family.json'] = canonical_json(family)
    changed_archive = archive(tmp_path,support)
    actual = methods._import_artifact(changed_archive,artifact_type='file',actor='fixture',generated_by=run['run_id'])
    run['outputs']['native_support'] = actual['artifact_id']
    result['native_support'] = {'archive_port':'native_support','sha256':actual['sha256'],'members':[{'name':name,'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()} for name,body in support.items()]}
    result_path = tmp_path/'modified-result.json'; result_path.write_bytes(canonical_json(result))
    run['outputs']['result_manifest'] = methods._import_artifact(result_path,artifact_type='json',actor='fixture',generated_by=run['run_id'])['artifact_id']
    with pytest.raises(ValueError): module.validate_airway_native_result(run['run_id'],{},manifest_ref,artifact_service=methods,workspace=tmp_path)
