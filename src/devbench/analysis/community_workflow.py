"""Governed, offline single-image nf-core count-matrix execution.

This adapter supplies a capability, not method selection or scientific approval.
The mandatory RNA graph and MethodService own dispatch and result publication.
"""
from __future__ import annotations

import csv
import base64
import hashlib
import json
import math
from pathlib import Path
import re
import time
from copy import deepcopy

from devharness_core.provenance import canonical_json, hash_artifact
from .method_executor import LocalDockerExecutor, _path
from .method_validation import finite_json

COMMIT = '30ed7741fc392127156c2fb10cfa3d69d216b54b'
FILES = {'result.json', 'gene-statistics.tsv', 'task-evidence.json', 'execution-manifest.json'}
PROFILE = {'cpu': 1, 'memory_mb': 2048, 'timeout_seconds': 30, 'pids': 128,
           'scratch_bytes': 16_000_000, 'max_output_bytes': 1_000_000, 'max_retries': 0}


def _sha(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _inventory(value):
    value = deepcopy(value)
    if set(value) != {'format','image','platform','workflow','nextflow','plugins','tools','execution','evidence_scope'}:
        raise ValueError('Exact admitted inventory required')
    if (value['format'] != 'devbench.community-workflow-inventory/1'
            or re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', value['image']) is None
            or value['platform'] not in {'linux/amd64','linux/arm64'}
            or value['workflow'].get('name') != 'nf-core/differentialabundance'
            or value['workflow'].get('version') != '2.0.0' or value['workflow'].get('commit') != COMMIT
            or value['workflow'].get('path') != '/opt/nf-core/differentialabundance/main.nf'
            or value['nextflow'].get('version') != '25.10.4'
            or value['nextflow'].get('path') != '/usr/local/bin/nextflow'
            or value['execution'] != {'executor':'local','container_engine':'none','network':'deny',
                'conda':False,'wave':False,'daemon_socket':False}
            or len(value['plugins']) != 1 or value['plugins'][0].get('id') != 'nf-schema'
            or value['plugins'][0].get('version') != '2.5.1'):
        raise ValueError('Unsupported workflow, image, engine or execution policy')
    from .community_worker import REQUIRED_TOOLS
    required_tools = REQUIRED_TOOLS
    if not required_tools <= set(value['tools']) or len(value['tools']) > 64 or any(
            not isinstance(tool.get('version'),str) or re.fullmatch(r'[0-9]+(?:[.][0-9]+){1,3}(?:[-+][A-Za-z0-9.]+)?',tool['version']) is None
            for tool in value['tools'].values()):
        raise ValueError('Exact admitted tool versions required; moving aliases are unsupported')
    for name,kind in (('python','file'),('pandas','tree'),('numpy','tree')):
        tool=value['tools'][name]
        if tool.get('sha256_kind')!=kind or not isinstance(tool.get('path'),str) or not Path(tool['path']).is_absolute():
            raise ValueError('Required Python dependency needs exact admitted path and component hash basis: '+name)
    hashes = [value['workflow'].get('tree_sha256'), value['nextflow'].get('sha256'),
              value['plugins'][0].get('sha256'), *[v.get('sha256') for v in value['tools'].values()]]
    if any(not isinstance(h,str) or re.fullmatch('[0-9a-f]{64}',h) is None for h in hashes):
        raise ValueError('Installed asset hashes required')
    canonical_json(value)
    return value


def _resources(value):
    value=deepcopy(value)
    bounds={'cpu':(1,16),'memory_mb':(2048,65536),'timeout_seconds':(10,3600),
            'pids':(32,1024),'scratch_bytes':(1_000_000,512_000_000),'max_output_bytes':(1,100_000_000),'max_retries':(0,0)}
    if set(value)!=set(bounds) or any(type(value[k]) is not int or not low<=value[k]<=high for k,(low,high) in bounds.items()):
        raise ValueError('Explicit bounded community resources required; retries disabled')
    if value['scratch_bytes']>value['memory_mb']*1024**2//4:
        raise ValueError('Scratch cannot exceed one quarter of container memory')
    return value


def _plan(value):
    value = deepcopy(value)
    canonical_json(value)
    if set(value) != {'format','objective','assay','design','statistics','resources'} or value['format'] != 'devbench.community-workflow-plan/1':
        raise ValueError('Exact resolved community plan required')
    a,d,s,r = (value[k] for k in ('assay','design','statistics','resources'))
    if a != {'kind':'raw_counts','orientation':'genes_by_samples','gene_id_column':'gene_id',
             'sample_id_column':'sample','provenance':'direct_counts'}:
        raise ValueError('Only direct raw gene counts with explicit axes are admitted')
    if set(d) != {'condition','reference','target','blocking','biological_unit','formula'}:
        raise ValueError('Exact reviewed design required')
    if (any(not isinstance(d[k],str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}',d[k])
            for k in ('condition','reference','target','biological_unit'))
            or d['reference'] == d['target'] or d['blocking'] != [] or d['formula'] != '~ '+d['condition']):
        raise ValueError('Only an explicit independent two-group design is admitted')
    fixed = {'normalization':'ratio','test':'Wald','adjustment':'BH','independent_filtering':False,
             'shrinkage':'none','fit_type':'parametric','filtering_min_abundance':0,'filtering_min_samples':1}
    if set(s)-{'exploratory_transformation'} != {*fixed,'alpha','seed'} or any(s[k] != v or type(s[k]) is not type(v) for k,v in fixed.items()):
        raise ValueError('Unsupported or unspecified statistical method')
    if type(s['alpha']) not in (float,int) or not 0 < s['alpha'] < 1 or type(s['seed']) is not int or not 0 <= s['seed'] <= 2147483647:
        raise ValueError('Explicit bounded alpha and seed required')
    _resources(r)
    from .community_worker import resolve_transform
    resolve_transform(value)
    if not isinstance(value['objective'].get('question'),str) or not value['objective']['question'].strip() or not value['objective'].get('source'):
        raise ValueError('Objective and requirement source required')
    return value


def _inputs(methods, inputs, plan):
    if set(inputs) != {'matrix','metadata'}:
        raise ValueError('Canonical matrix and metadata artifact IDs required')
    matrix, metadata = [finite_json(methods.artifact_path(inputs[k])) for k in ('matrix','metadata')]
    from .community_worker import validate_inputs
    validate_inputs(matrix, metadata, plan)
    return matrix, metadata


def _runtime_evidence(tasks, plan, inventory):
    evidence=tasks.get('execution_evidence')
    if evidence is None:
        if inventory['nextflow'].get('sha256_path'):
            raise ValueError('Actual image execution requires retained raw runtime evidence')
        return  # Historical explicit engineering fixtures have no real engine.
    if evidence.get('format')!='devbench.community-execution-evidence/1':
        raise ValueError('Unsupported runtime evidence format')
    def captured(record, *, complete=False):
        if record.get('status')=='absent':
            if not record.get('absent_reason'):raise ValueError('Absent runtime evidence needs a reason')
            return None
        if record.get('status')!='captured':raise ValueError('Unknown raw runtime evidence capture status')
        try:data=base64.b64decode(record['content_base64'],validate=True)
        except (KeyError,ValueError,TypeError) as error:raise ValueError('Invalid raw runtime evidence bytes') from error
        size=record.get('original_size_bytes');count=record.get('captured_size_bytes')
        if (type(size) is not int or type(count) is not int or not 0<=count<=size
                or count!=len(data) or record.get('captured_sha256')!=hashlib.sha256(data).hexdigest()
                or record.get('truncated') is not (size>count)
                or record.get('complete_sha256')!=(hashlib.sha256(data).hexdigest() if size==count else None)):
            raise ValueError('Raw runtime evidence hash/size correspondence failed')
        if complete and record['truncated']:raise ValueError('Complete runtime evidence was not retained')
        return data
    from .community_worker import build_config,build_parameters,parse_trace_text
    root=Path('/tmp/community')
    params=captured(evidence['supplied_parameters'],complete=True)
    config=captured(evidence['supplied_config'],complete=True)
    if params is None or canonical_json(json.loads(params))!=canonical_json(build_parameters(plan,root)):
        raise ValueError('Supplied runtime parameters differ from frozen plan')
    if config!=build_config(plan['resources']).encode():raise ValueError('Supplied runtime configuration differs from bounded profile')
    invocation=evidence['invocation']
    expected=['/usr/local/bin/nextflow','-C',str(root/'local.config'),'-log',str(root/'nextflow.log'),
        'run','/opt/nf-core/differentialabundance/main.nf','-offline','-params-file',str(root/'params.json'),'-work-dir',str(root/'work')]
    if (invocation.get('argv')!=expected or invocation.get('environment',{}).get('NXF_OFFLINE')!='true'
            or invocation.get('environment',{}).get('NXF_UUID')!=tasks['nextflow_session_id']):
        raise ValueError('Actual launcher invocation differs from frozen runtime')
    resolved=evidence.get('actual_resolved_config',{})
    if resolved.get('status')!='not_obtained' or not resolved.get('reason'):
        raise ValueError('Actual resolved configuration must not be inferred from intended configuration')
    trace=captured(evidence['trace'],complete=True)
    if trace is None or canonical_json(parse_trace_text(trace.decode()))!=canonical_json(tasks['attempts']):
        raise ValueError('Normalized task trace differs from retained raw execution evidence')
    for name in ('stdout','stderr','nextflow'):
        data=captured(evidence['logs'][name])
        if data is None:raise ValueError('Successful execution lacks required raw runtime log evidence')
        if name=='nextflow' and ('Session UUID: '+tasks['nextflow_session_id']).encode() not in data:
            raise ValueError('Actual session observation is absent from retained raw runtime log')
    if (tasks.get('producer')!={'name':'Nextflow','version':inventory['nextflow']['version']}
            or tasks.get('engine_state',{}).get('engine_started') is not True
            or tasks['engine_state'].get('engine_exit_code')!=0 or tasks.get('worker_exit_code')!=0
            or tasks.get('engine_session_observed') is not True):
        raise ValueError('Runtime engine/worker outcomes do not support successful execution')
    limits={'cpus':plan['resources']['cpu'],'memory':plan['resources']['memory_mb']//2*1024**2,
            'time':(plan['resources']['timeout_seconds']-8)*1000}
    for attempt in tasks['attempts']:
        if not attempt.get('task_id') or not attempt.get('hash'):raise ValueError('Raw trace task identity is absent')
        for key,record in attempt.get('resources',{}).items():
            try:value=int(record['value'])
            except (ValueError,TypeError) as error:raise ValueError('Uninterpretable raw runtime resource evidence') from error
            if key not in limits or not 0<value<=limits[key]:raise ValueError('Actual task configuration exceeds frozen resource cap')
        if attempt.get('error_action') not in (None,'terminate'):
            raise ValueError('Observed task error action contradicts frozen no-retry policy')


def _ingest_outputs(folder, *, plan, inventory):
    """Check bounded raw tables and exact identities before publishing any result."""
    plan, inventory = _plan(plan), _inventory(inventory)
    folder = _path(folder)
    paths = list(folder.iterdir())
    if len(paths) > 64 or any(p.is_symlink() or not p.is_file() for p in paths):
        raise ValueError('Only flat regular output files are admitted')
    if sum(p.stat().st_size for p in paths) > plan['resources']['max_output_bytes']:
        raise ValueError('Output size exceeds byte budget limit')
    if {p.name for p in paths} != FILES:
        raise ValueError('Output set differs from exact declared inventory')
    result, manifest, tasks = [finite_json(folder / n) for n in ('result.json','execution-manifest.json','task-evidence.json')]
    if (manifest.get('format') != 'devbench.community-execution-manifest/1'
            or result.get('format') != 'devbench.community-workflow-result/1'
            or tasks.get('format') != 'devbench.community-task-evidence/1'
            or manifest.get('inventory_sha256') != _sha(inventory) or manifest.get('plan_sha256') != _sha(plan)
            or manifest.get('workflow_commit') != COMMIT or result.get('workflow_commit') != COMMIT
            or result.get('engine_status') != 'completed' or result.get('engine_exit_code') != 0):
        raise ValueError('Stale, failed or misbound workflow output')
    if 'exploratory_transformation' in plan['statistics'] or 'resolved_configuration' in manifest:
        from .community_worker import resolve_transform
        if manifest.get('resolved_configuration') != {'deseq2_vs_method':resolve_transform(plan)}:
            raise ValueError('Exploratory transformation resolution differs from frozen plan configuration')
    session = manifest.get('nextflow_session_id')
    if not isinstance(session,str) or re.fullmatch('[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}',session) is None or any(x.get('nextflow_session_id') != session for x in (result,tasks)):
        raise ValueError('Engine session binding mismatch')
    if manifest.get('artifacts') != {n:hash_artifact(folder/n)['sha256'] for n in FILES-{'execution-manifest.json'}}:
        raise ValueError('Raw output hashes differ from manifest')
    expected = {'variable':plan['design']['condition'],'reference':plan['design']['reference'],'target':plan['design']['target']}
    if result.get('contrast') != expected or any(result.get(k) != v for k,v in {'method':'DESeq2','test':'Wald','adjustment':'BH','shrinkage':'none',
            'scientific_qualification':'not_established','clinical_release_allowed':False}.items()):
        raise ValueError('Result contrast or method identity mismatch')
    _runtime_evidence(tasks,plan,inventory)
    attempts = tasks.get('attempts')
    if not isinstance(attempts,list) or not attempts or len(attempts)>128:
        raise ValueError('Bounded task attempts required')
    for attempt in attempts:
        if (not isinstance(attempt,dict) or type(attempt.get('attempt')) is not int or attempt['attempt']<1
                or attempt['attempt'] != 1 or attempt.get('status') not in {'COMPLETED','CACHED'} or attempt.get('exit') != 0):
            raise ValueError('Failed or unsupported engine task attempt')
    if type(manifest.get('cache_reuse')) is not bool or manifest['cache_reuse'] != any(a['status']=='CACHED' for a in attempts):
        raise ValueError('Cache reuse must match task evidence')
    from .scientific_result_contract import ResultRow, ResultContrast, ResultMethod, ResultFamily
    ResultContrast(contrast_id='condition',reference=expected['reference'],comparison=expected['target'],operation='comparison_minus_reference')
    ResultMethod(method_id='DESeq2',version=inventory['tools']['DESeq2']['version'],normalization='ratio',test='Wald',effect_scale='log2_fold_change',effect_kind='unshrunken')
    rows=[]; typed=[]; seen=set()
    with (folder/'gene-statistics.tsv').open(newline='') as stream:
        reader=csv.DictReader(stream,delimiter='\t')
        if reader.fieldnames != ['gene_id','baseMean','log2FoldChange','lfcSE','stat','pvalue','padj']:
            raise ValueError('Pinned DESeq2 table schema mismatch')
        for row in reader:
            gene=row.pop('gene_id')
            if not gene or gene in seen or len(seen)>=100000 or None in row:
                raise ValueError('Invalid or duplicate gene identity')
            seen.add(gene)
            values={}
            for key,text in row.items():
                try: value=None if text=='NA' else float(text)
                except (ValueError,TypeError) as e: raise ValueError('Unsupported numeric table value') from e
                if value is not None and (not math.isfinite(value) or key in {'baseMean','lfcSE'} and value<0):
                    raise ValueError('Nonfinite or negative statistic')
                values[key]=value
            metrics=dict(effect=values['log2FoldChange'],p_value=values['pvalue'],adjusted_p_value=values['padj'],standard_error=values['lfcSE'])
            if metrics['adjusted_p_value'] is not None and (metrics['p_value'] is None or metrics['adjusted_p_value']<metrics['p_value']):
                raise ValueError('BH requires q >= p')
            status=('not_estimable' if all(v is None for v in metrics.values()) else
                'ok' if metrics['effect'] is not None and metrics['p_value'] is not None else 'partially_available')
            # The raw table does not distinguish all-zero, outlier and fit failures.
            # Missingness is preserved; its cause remains explicitly unresolved.
            typed.append(ResultRow(feature_id=gene,status=status,**metrics,missing_reasons={k:'upstream_NA_cause_unresolved' for k,v in metrics.items() if v is None}))
            rows.append({'gene_id':gene,**{k:values[k] for k in ('log2FoldChange','pvalue','padj')}})
    if not rows or result.get('gene_statistics') != rows:
        raise ValueError('Result statistics differ from raw table or NA mask')
    family=ResultFamily(family_id='all_retained_features',eligible_ids=tuple(row.feature_id for row in typed),complete=True,adjustment='benjamini_hochberg',failed_hypothesis_policy='exclude_failed_hypotheses')
    from types import SimpleNamespace
    from .scientific_result_contract import multiplicity_check
    multiplicity_check(SimpleNamespace(family=family,rows=typed))
    return deepcopy(result),typed,family


def ingest_outputs(folder, *, plan, inventory):
    return _ingest_outputs(folder,plan=plan,inventory=inventory)[0]


class CommunityWorkflowService:
    def __init__(self, methods, *, inventory, executor=None):
        self.methods=methods
        self.inventory=_inventory(inventory)
        self.executor=executor or LocalDockerExecutor()
        self._active_requests=set()

    def register_method(self, *, actor, resources=None):
        resources=_resources(PROFILE if resources is None else resources)
        source=Path(__file__).resolve().with_name('community_worker.py')
        directory=self.methods.root/'community-admission'; directory.mkdir(exist_ok=True)
        lock=directory/(_sha(self.inventory)+'.json')
        if not lock.exists():
            with lock.open('xb') as stream: stream.write(canonical_json(self.inventory))
            lock.chmod(0o444)
        execution={k:v for k,v in resources.items() if k not in {'scratch_bytes','max_retries'}}
        execution['network']='deny'
        contract={'format':'devharness.method/1','method_id':'community.nfcore.differentialabundance.2',
            'name':'nf-core differentialabundance 2.0.0 count comparison','lifecycle':'custom','implementation_trust':'unreviewed',
            'inputs':{k:{'artifact_type':'json','required':True} for k in ('matrix','metadata')},
            'parameters':{'type':'object','additionalProperties':False,'properties':{'plan':{'type':'object','properties':{'resources':{'const':resources}},'required':['resources']}},'required':['plan']},
            'outputs':{k:{'artifact_type':t,'path':'/outputs/'+n,'required':True} for k,t,n in (
                ('result','json','result.json'),('statistics','table','gene-statistics.tsv'),('tasks','json','task-evidence.json'),('manifest','json','execution-manifest.json'))},
            'implementation':{'kind':'oci','source_sha256':hash_artifact(source)['sha256'],'entrypoint':['python3','/method/main.py']},
            'environment':{'image':self.inventory['image'],'lock_sha256':hash_artifact(lock)['sha256']},'execution':execution,
            'validation':{'comparator':'json','invariants':[],'counts_layer':'X','qc_metrics':[]},
            'provenance':{'origin':'pinned upstream count-matrix adapter; execution/scientific qualification pending'}}
        contract['outputs']['typed_result']={'artifact_type':'json','path':'/outputs/typed-result.json','required':False}
        return self.methods.register_method(contract,source=source,environment_lock=lock,actor=actor)

    def _admitted_method(self, method_version_id):
        inventory=_inventory(self.inventory)
        method=self.methods.method(method_version_id)
        contract=method['contract']
        source=Path(__file__).resolve().with_name('community_worker.py')
        if (contract['method_id']!='community.nfcore.differentialabundance.2'
                or contract['implementation']['kind']!='oci'
                or contract['implementation']['entrypoint']!=['python3','/method/main.py']
                or contract['implementation']['source_sha256']!=hash_artifact(source)['sha256']
                or contract['environment']['image']!=inventory['image']):
            raise ValueError('Method source/worker differs from admitted community adapter')
        if finite_json(self.methods.artifact_path(method['environment_artifact']))!=inventory:
            raise ValueError('Retained method environment lock differs from current admitted inventory')
        return method

    def prepare_step(self, branch_id, *, method_version_id, inputs, plan, actor):
        plan=_plan(plan); _inputs(self.methods,inputs,plan)
        self._admitted_method(method_version_id)
        return self.methods.add_step(branch_id,method_version_id,inputs,{'plan':plan},actor=actor)

    def _save(self, request, *, new=False):
        # Every observer and controller update merges against the durable row
        # under the same SQLite writer transaction used by publication.
        with self.methods._db() as db:
            if not new:
                latest=self.methods._get(db,'community_request',request['request_id'])
                binding=deepcopy(latest['binding'])
                for key,value in request['binding'].items():
                    if key in {'container_id','container_name','nextflow_session_id','image','inventory_sha256','run_id'} and binding.get(key) is not None and value!=binding[key]:
                        raise ValueError('Immutable owned runtime binding changed')
                    binding[key]=value
                events=deepcopy(latest['events'])
                for event in request['events']:
                    if event not in events: events.append(deepcopy(event))
                state=request['state']
                if latest['state'] in {'cancellation_requested','cancelled','completed'}:
                    state=latest['state']
                    if latest['state']=='cancellation_requested' and request['state']=='cancelled': state='cancelled'
                diagnostics=deepcopy(latest['failure_artifacts'])
                for artifact in request['failure_artifacts']:
                    if artifact not in diagnostics:diagnostics.append(deepcopy(artifact))
                known={key:latest[key] for key in ('engine_started','engine_exit_code','worker_exit_code')
                    if latest.get(key) is not None and request.get(key) is None}
                previous_observation=latest.get('cancellation_observation')
                incoming_observation=request.get('cancellation_observation')
                if previous_observation is not None:
                    if incoming_observation is None or (previous_observation.get('state')=='cancelled' and incoming_observation.get('state')!='cancelled'):
                        known['cancellation_observation']=deepcopy(previous_observation)
                    else:
                        merged={**previous_observation,**incoming_observation}
                        if previous_observation.get('diagnostic_collection') is not None and incoming_observation.get('diagnostic_collection') is None:
                            merged['diagnostic_collection']=deepcopy(previous_observation['diagnostic_collection'])
                        known['cancellation_observation']=merged
                for field in ('diagnostic_loss','diagnostic_status','diagnostic_collection'):
                    if latest.get(field) is not None and request.get(field) is None:known[field]=deepcopy(latest[field])
                previous_collection=latest.get('diagnostic_collection')
                incoming_collection=request.get('diagnostic_collection')
                if (isinstance(previous_collection,dict) and previous_collection.get('status')=='collected_while_supervisor_live'
                        and (not isinstance(incoming_collection,dict) or incoming_collection.get('status')!='collected_while_supervisor_live')):
                    known['diagnostic_collection']=deepcopy(previous_collection)
                latest.update(request)
                latest.update(known)
                latest.update(binding=binding,events=events,state=state,failure_artifacts=diagnostics)
                request.clear();request.update(latest)
            self.methods._put(db,'community_request',request['request_id'],request,update=not new)

    def status(self, request_id):
        value=self.methods.get('community_request',request_id)
        if value['state'] in {'queued','running','cancellation_requested'} and request_id not in self._active_requests:
            value['state']='reconciliation_required'
        return value

    def _authority(self, step_id, *, planner=None, session_id=None, experiment_id=None, scientific_workflow=None, allow_data_execution=False):
        from .method_planner import ScientificMethodPlanner
        from .workflow_entrypoints import is_rna_plan
        if (allow_data_execution is not True or not isinstance(planner,ScientificMethodPlanner) or planner.methods is not self.methods
                or scientific_workflow is None or not all(callable(getattr(scientific_workflow,k,None)) for k in ('configuration','verify','perform'))):
            raise PermissionError('Live planner and trusted scientific runtime with current request permission required')
        state=planner.get(session_id)
        matches=[e for e in state['experiments'] if e['id']==experiment_id and list(e['step_ids'].values())==[step_id]]
        if not is_rna_plan(state) or len(matches)!=1:
            raise ValueError('Execution must bind the exact frozen RNA trial step')
        from .workflow_operations import WorkflowOperationLedger,OperationBlocked
        # Only immutable retained evidence is observed here. A short bounded
        # read retry tolerates a concurrent SQLite commit; no operation is
        # reserved, executed, resumed, or authorized from a cached answer.
        for attempt in range(6):
            try:
                ledger=WorkflowOperationLedger(self.methods.root/'rna-workflow-operations.sqlite',readonly=True)
                declaration=ledger.get(session_id,'declare',0)
                break
            except OperationBlocked as error:
                if error.state!='ledger_busy' or attempt==5:raise
                time.sleep(.01)
        if declaration is None or declaration['status']!='completed' or declaration['request'].get('science_configuration_digest') != _sha(scientific_workflow.configuration()):
            raise PermissionError('Exact retained declaration and trusted runtime required')
        return matches[0]

    def run(self, step_id, *, request_id, actor, **context):
        if not isinstance(request_id,str) or re.fullmatch('[0-9a-f]{32}',request_id) is None:
            raise ValueError('Bounded request identity required')
        self._authority(step_id,**context)
        try: previous=self.methods.get('community_request',request_id)
        except ValueError: previous=None
        identity={'step_id':step_id,'session_id':context['session_id'],'experiment_id':context['experiment_id'],'inventory_sha256':_sha(self.inventory)}
        if previous:
            if any(previous[k]!=v for k,v in identity.items()): raise ValueError('Request binding conflicts with different step or session')
            if previous['state'] not in {'completed','failed','cancelled'}: raise ValueError('Uncertain attempt requires reconciliation; no implicit redispatch')
            return self.methods.get('run',previous['run_id'])
        step=self.methods.get('step',step_id)
        self._admitted_method(step['method_version_id'])
        plan=_plan(step['parameters']['plan']); _inputs(self.methods,step['inputs'],plan)
        observed=self.executor.inspect_image(self.inventory['image'])
        if (observed.get('inventory')!=self.inventory or observed.get('platform')!=self.inventory['platform']
                or self.inventory['image'] not in observed.get('repo_digests',[])):
            raise ValueError('Installed image inventory differs from admission')
        request={'request_id':request_id,**identity,'state':'queued','run_id':None,'binding':{},'events':[],'failure_artifacts':[]}
        self._save(request,new=True)
        service=self
        class GuardedExecutor:
            def execute(self, contract, inputs, parameters, source, lock, output_dir):
                request['run_id']=Path(output_dir).parent.name
                request['state']='running'
                request['binding'].update(output_dir=str(output_dir),max_output_bytes=plan['resources']['max_output_bytes'])
                service._save(request)
                def observer(event):
                    required={'run_id':request['run_id'],'container_name':'devharness-method-'+request['run_id'],
                        'image':service.inventory['image'],'inventory_sha256':_sha(service.inventory)}
                    if any(event.get(k)!=v for k,v in required.items()): raise ValueError('Runtime identity binding mismatch')
                    for k in ('container_id','nextflow_session_id'):
                        if request['binding'].get(k) is not None and event.get(k)!=request['binding'][k]:
                            raise ValueError('Immutable runtime session identity changed')
                    request['binding']={**request['binding'],**{k:v for k,v in event.items() if k not in {'event','state'}}}
                    request['events'].append(deepcopy(event)); service._save(request)
                    return request['state'] not in {'cancellation_requested','cancelled','unknown','reconciliation_required'}
                service.executor.set_observer(observer)
                result=service.executor.execute(contract,inputs,parameters,source,lock,output_dir)
                current=service.methods.get('community_request',request_id)
                request['state']=current['state']
                request['worker_exit_code']=result.get('worker_exit_code',result.get('exit_code'))
                started=any(e['event']=='engine_session_started' for e in request['events'])
                request['engine_started']=result.get('engine_started',started)
                request['engine_exit_code']=result.get('engine_exit_code',result.get('exit_code') if started else None)
                if current['state']=='cancellation_requested' and result.get('cancelled') is True:request['state']='cancelled'
                service._save(request)
                try:
                    if result.get('exit_code')==0:
                        verified,rows,family=_ingest_outputs(output_dir,plan=plan,inventory=service.inventory)
                        matrix,metadata=_inputs(service.methods,step['inputs'],plan)
                        if [row['gene_id'] for row in verified['gene_statistics']] != matrix['gene_ids']:
                            raise ValueError('Result feature cohort differs from immutable unfiltered input')
                        if verified['nextflow_session_id'] != request['binding'].get('nextflow_session_id'):
                            raise ValueError('Result session differs from observed owned engine identity')
                        from .scientific_result_contract import ScientificResult
                        by_sample={row['sample_id']:row for row in metadata['samples']}
                        units=[by_sample[sample][plan['design']['biological_unit']] for sample in matrix['sample_ids']]
                        typed=ScientificResult(record_format='devbench.scientific-result-diagnostic/1',
                            result_id='p2-'+hash_artifact(output_dir/'execution-manifest.json')['sha256'],input_root=_sha(step['inputs']),
                            cohort={'cohort_id':'cohort-'+_sha(step['inputs']),'sample_ids':tuple(matrix['sample_ids']),'biological_unit_ids':tuple(units)},
                            biological_unit_count=len(set(units)),
                            contrast={'contrast_id':'condition','reference':plan['design']['reference'],'comparison':plan['design']['target'],'operation':'comparison_minus_reference'},
                            method={'method_id':'DESeq2','version':service.inventory['tools']['DESeq2']['version'],'normalization':'ratio',
                                'test':'Wald','effect_scale':'log2_fold_change','effect_kind':'unshrunken'},family=family,rows=tuple(rows))
                        (output_dir/'typed-result.json').write_bytes(canonical_json(typed.model_dump(mode='json')))
                        if sum(p.stat().st_size for p in output_dir.iterdir())>plan['resources']['max_output_bytes']:
                            raise ValueError('Typed result plus raw evidence exceeds cumulative output byte budget')
                        if request['state']=='cancellation_requested' or request['state']=='cancelled':
                            raise ValueError('Cancelled execution cannot publish outputs')
                except Exception as error:
                    error.execution=deepcopy(result)
                    raise
                return result
            def publication_context(self, record):
                from contextlib import contextmanager
                @contextmanager
                def guarded():
                    with service.methods._db() as db:
                        latest=service.methods._get(db,'community_request',request_id)
                        if latest['state'] in {'cancellation_requested','cancelled','unknown','reconciliation_required'}:
                            raise ValueError('Cancellation or uncertain execution blocks successful publication')
                        if latest['run_id']!=record['run_id']:
                            raise ValueError('Publication run identity differs from durable request')
                        yield db
                        latest=service.methods._get(db,'community_request',request_id)
                        latest['state']='completed'
                        service.methods._put(db,'community_request',request_id,latest,update=True)
                return guarded()
        from .workflow_entrypoints import workflow_action
        self._active_requests.add(request_id)
        try:
            workflow_action(context['planner'],context['session_id'],'run',actor=actor,experiment_id=context['experiment_id'],
                allow_data_execution=True,executor=GuardedExecutor(),scientific_workflow=context['scientific_workflow'])
        except BaseException:
            request['state']='unknown'; self._save(request); raise
        finally:
            self._active_requests.discard(request_id)
        if request['run_id'] is None:
            request['state']='failed'; self._save(request)
            raise ValueError('Mandatory workflow refused dispatch')
        run=self.methods.get('run',request['run_id'])
        execution=run.get('execution',{})
        if 'worker_exit_code' not in request:request['worker_exit_code']=execution.get('exit_code')
        if 'engine_exit_code' not in request:request['engine_exit_code']=execution.get('engine_exit_code')
        if 'engine_started' not in request:request['engine_started']=execution.get('engine_started')
        if 'diagnostic_collection' in execution:request['diagnostic_collection']=deepcopy(execution['diagnostic_collection'])
        request['state']='completed' if run['status']=='succeeded' else ('cancelled' if request['state']=='cancelled' else 'failed')
        if (run['status']!='succeeded' and request.get('worker_exit_code') is None and request['state']!='cancelled') or (request['state']!='cancelled' and not request['binding'].get('container_id') and any(e['event']=='container_create_intent' for e in request['events'])):
            request['state']='unknown'
        if run['status']!='succeeded':
            output=self.methods.root/'runs'/run['run_id']/'outputs'
            total=0
            for path in sorted(output.iterdir()):
                if path.name not in FILES or path.is_symlink() or not path.is_file(): continue
                total+=path.stat().st_size
                if total>plan['resources']['max_output_bytes']: break
                ref=self.methods.import_artifact(path,artifact_type='file',actor=actor)
                request['failure_artifacts'].append({k:ref[k] for k in ('artifact_id','sha256')})
        self._save(request)
        return run

    def _observe(self, request):
        # A queued failure or cancellation before create intent has no owned
        # lifecycle identity yet. Do not query/signal a guessed object or treat
        # absence of a binding as evidence that an in-flight create has settled.
        binding=request['binding']
        if not isinstance(binding,dict):
            raise ValueError('Owned runtime binding must be a mapping')
        required=('container_name','image','nextflow_session_id','inventory_sha256','run_id','output_dir','max_output_bytes')
        missing=[key for key in required if binding.get(key) is None or binding.get(key)=='']
        if missing:
            return {'state':'runtime_identity_unavailable','dispatch_settled':False,
                'dispatch_phase':'runtime_identity_unavailable','diagnostic_status':'runtime_identity_unavailable',
                'missing_binding_fields':missing}
        observed=self.executor.inspect_run(request['binding'])
        for key in ('container_name','image','nextflow_session_id','inventory_sha256','run_id'):
            if observed is None or not request['binding'].get(key) or observed.get(key)!=request['binding'][key]:
                raise PermissionError('Observed runtime identity is not the owned container/session binding')
        original=request['binding'].get('container_id')
        if original is not None and observed.get('container_id')!=original:
            raise PermissionError('Observed owned immutable container identity changed')
        if original is None and observed.get('state')!='missing':
            recovered=observed.get('container_id')
            if not isinstance(recovered,str) or re.fullmatch('[0-9a-f]{64}',recovered) is None:
                raise ValueError('Successful owned observation did not recover an immutable container identity')
            request['binding']['container_id']=recovered
            request['events'].append({'event':'container_identity_recovered','container_id':recovered})
            self._save(request)
        return observed

    def cancel(self, request_id, *, actor, **context):
        request=self.methods.get('community_request',request_id)
        self._authority(request['step_id'],**context)
        if any(context[k]!=request[k] for k in ('session_id','experiment_id')): raise PermissionError('Cancellation session binding changed')
        with self.methods._db() as db:
            latest=self.methods._get(db,'community_request',request_id)
            if latest['state'] in {'completed','failed','cancelled'}:
                return {**latest,'cancellation_outcome':'already_'+latest['state']}
            latest['state']='cancellation_requested'
            self.methods._put(db,'community_request',request_id,latest,update=True)
            request=latest
        initial=self._observe(request)
        request['state']='cancellation_requested'; self._save(request)
        observed=({**initial,'state':'cancellation_pending'} if initial.get('state')=='runtime_identity_unavailable'
            else self.executor.cancel_run(request['binding']))
        retained={key:deepcopy(observed[key]) for key in ('state','diagnostic_loss','diagnostic_collection','diagnostic_status','missing_binding_fields') if key in observed}
        if len(canonical_json(retained))>65536:raise ValueError('Cancellation observation exceeds bounded retained evidence limit')
        request['cancellation_observation']=retained
        for field in ('diagnostic_loss','diagnostic_collection','diagnostic_status'):
            if field in retained:request[field]=deepcopy(retained[field])
        request['state']='cancelled' if observed.get('state')=='cancelled' else 'cancellation_requested'
        self._save(request)
        return deepcopy(request)

    def reconcile(self, request_id, *, actor):
        request=self.methods.get('community_request',request_id)
        if request['state'] in {'completed','failed','cancelled'}: return request
        observed=self._observe(request)
        settled=observed.get('dispatch_settled')
        if settled is None:
            # Legacy trusted executor fixtures can prove they already passed
            # engine launch; real OCI observations always supply the predicate.
            settled=any(event['event']=='engine_session_started' for event in request['events'])
        if request['state']=='cancellation_requested' and observed.get('state') in {'missing','exited','dead'} and settled is True:
            request['state']='cancelled'
            request['diagnostic_loss']='No additional tmpfs recovery is claimed after observed termination; only retained failure_artifacts are available'
        elif request['state']!='cancellation_requested':
            request['state']='interrupted' if observed.get('state')=='missing' else 'unknown'
        observation={'event':'reconciled_observation','state':observed.get('state'),'cache_present':observed.get('cache_present'),
            'dispatch_settled':settled,'dispatch_phase':observed.get('dispatch_phase')}
        if observed.get('state')=='runtime_identity_unavailable':
            observation.update(diagnostic_status=observed['diagnostic_status'],missing_binding_fields=observed['missing_binding_fields'])
        if len(canonical_json(observation))>65536:raise ValueError('Reconciliation observation exceeds bounded evidence limit')
        request['events'].append(observation)
        self._save(request)
        return deepcopy(request)
