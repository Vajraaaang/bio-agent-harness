"""Standalone stdlib-only worker for the admitted offline nf-core image.

The controller retains this complete source by hash. No package installation,
network fetch, alternate estimator, or durable resume is attempted here.
"""
from __future__ import annotations
import csv
import importlib
import platform
import base64
import io
import signal
import time
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

COMMIT='30ed7741fc392127156c2fb10cfa3d69d216b54b'
REQUIRED_TOOLS=frozenset({'java','R','DESeq2','shinyngs','csvtk','python','pandas','numpy'})


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def validate_inputs(matrix, metadata, plan):
    if not isinstance(matrix,dict) or set(matrix)!={'gene_ids','sample_ids','matrix'} or not isinstance(metadata,dict) or set(metadata)!={'samples'}:
        raise ValueError('Canonical bulk matrix/metadata JSON required')
    genes,samples,values=matrix['gene_ids'],matrix['sample_ids'],matrix['matrix']
    for ids in (genes,samples):
        if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)) or any(not isinstance(x,str) or not x or len(x)>200 or any(c in x for c in '\t\r\n') for x in ids):
            raise ValueError('Distinct bounded feature/sample identifiers required')
    if 'gene_id' in samples:
        raise ValueError('Sample identifier gene_id is reserved for the counts table header')
    if len(genes)>100000 or len(samples)>4096 or len(values)!=len(genes) or len(genes)*len(samples)>2000000:
        raise ValueError('Matrix dimension bound exceeded')
    if any(not isinstance(row,list) or len(row)!=len(samples) or any(type(v) is not int or not 0<=v<=2147483647 for v in row) for row in values):
        raise ValueError('Nonnegative integer direct counts required without conversion')
    rows=metadata['samples']; d=plan['design']
    if not isinstance(rows,list) or len(rows)!=len(samples): raise ValueError('Cohort/sample count mismatch')
    by_id={}
    units=set(); groups={d['reference']:0,d['target']:0}
    for row in rows:
        if not isinstance(row,dict) or row.get('sample_id') in by_id or row.get('sample_id') not in samples:
            raise ValueError('Duplicate or mismatched metadata sample IDs')
        if 'sample' in row:
            raise ValueError('Metadata annotation sample is reserved for the canonical sample_id CSV header')
        unit=row.get(d['biological_unit']); group=row.get(d['condition'])
        if not isinstance(unit,str) or not unit or unit in units or group not in groups:
            raise ValueError('Independent biological units and explicit contrast groups required')
        if any(not isinstance(k,str) or not isinstance(v,(str,int,float)) or isinstance(v,bool) or isinstance(v,float) and not math.isfinite(v) for k,v in row.items()):
            raise ValueError('Bounded scalar sample annotations required')
        units.add(unit);groups[group]+=1;by_id[row['sample_id']]=row
    if min(groups.values())<2: raise ValueError('At least two independently declared units per group required')
    return [by_id[s] for s in samples]


def convert_inputs(matrix_path, metadata_path, plan, directory):
    """Lossless ID alignment from immutable canonical JSON; no host CSV inputs."""
    for path in (matrix_path,metadata_path):
        if Path(path).stat().st_size>100_000_000: raise ValueError('Input byte bound exceeded')
    matrix=json.loads(Path(matrix_path).read_bytes()); metadata=json.loads(Path(metadata_path).read_bytes())
    rows=validate_inputs(matrix,metadata,plan)
    directory=Path(directory)
    with (directory/'counts.tsv').open('w',newline='') as stream:
        writer=csv.writer(stream,delimiter='\t',lineterminator='\n');writer.writerow(['gene_id',*matrix['sample_ids']])
        writer.writerows([g,*v] for g,v in zip(matrix['gene_ids'],matrix['matrix']))
    columns=['sample',*sorted(set().union(*(set(r)-{'sample_id'} for r in rows)))]
    with (directory/'samples.csv').open('w',newline='') as stream:
        writer=csv.writer(stream,lineterminator='\n');writer.writerow(columns)
        writer.writerows([r['sample_id'],*[r.get(k,'') for k in columns[1:]]] for r in rows)
    d=plan['design']
    with (directory/'contrasts.csv').open('w',newline='') as stream:
        writer=csv.writer(stream,lineterminator='\n');writer.writerow(['id','variable','reference','target'])
        writer.writerow(['comparison',d['condition'],d['reference'],d['target']])
    return matrix,metadata


def write_json(path,value):
    Path(path).write_bytes(canonical(value))


def resolve_transform(plan):
    """Resolve the exploratory-only upstream control without mutating the plan."""
    supplied=plan['statistics'].get('exploratory_transformation')
    if supplied is None:
        method='vst'
        source={'kind':'inherited_upstream_default',
            'reference':'https://github.com/nf-core/differentialabundance/blob/'+COMMIT+'/nextflow.config#L112'}
    else:
        if not isinstance(supplied,dict) or set(supplied)!={'method','source'} or supplied['method'] not in {'vst','rlog'}:
            raise ValueError('Explicit supported exploratory transformation required')
        method=supplied['method'];source=supplied['source']
        if (not isinstance(source,dict) or set(source)!={'kind','reference'}
                or source['kind'] not in {'engineering_fixture','analyst_suggestion'}
                or not isinstance(source['reference'],str) or not 1<=len(source['reference'])<=4096):
            raise ValueError('Transformation provenance must identify the engineering or analyst source; user-prespecified attribution requires a matching requirement record')
    return {'parameter':'deseq2_vs_method','value':method,'scope':'exploratory_variance_stabilization','source':source}


def verify_components(inventory, assets):
    """Bind named package/workflow identities to verified installed file hashes."""
    if not REQUIRED_TOOLS <= set(inventory.get('tools',{})):
        raise ValueError('Required workflow tool/Python dependency is absent from installed inventory')
    def component(path, kind, expected):
        if not isinstance(path,str) or not path.startswith('/') or '..' in Path(path).parts:
            raise ValueError('Exact installed component path required')
        if kind=='file':
            actual=assets.get(path)
        elif kind=='tree':
            prefix=path.rstrip('/')+'/'
            files={name[len(prefix):]:value for name,value in assets.items() if name.startswith(prefix)}
            if not files: raise ValueError('Installed component tree is absent from asset manifest')
            actual=digest(files)
        else:
            raise ValueError('Explicit component hash basis required')
        if actual!=expected: raise ValueError('Installed component identity hash mismatch: '+path)
    workflow=inventory['workflow']
    component(workflow.get('tree_root'),'tree',workflow['tree_sha256'])
    nextflow=inventory['nextflow']
    component(nextflow.get('sha256_path'),'file',nextflow['sha256'])
    for item in [*inventory['plugins'],*inventory['tools'].values()]:
        component(item.get('path'),item.get('sha256_kind'),item['sha256'])


def verify_python_dependencies(inventory):
    """Check mandatory postprocessor imports in this actual image interpreter.

    Package presence/version/location is checked before Nextflow or count data
    processing. This performs no statistical analysis or package installation.
    """
    tools=inventory.get('tools',{})
    if not {'python','pandas','numpy'} <= set(tools):
        raise ValueError('Required Python postprocessing dependency metadata is missing')
    python=tools['python']
    if (platform.python_version()!=python['version']
            or Path(sys.executable).resolve()!=Path(python['path']).resolve()):
        raise ValueError('Actual Python interpreter identity differs from admitted metadata')
    for name in ('numpy','pandas'):
        declared=tools[name]
        try:module=importlib.import_module(name)
        except ImportError as error:
            raise ValueError('Required Python dependency cannot be imported: '+name) from error
        if (module.__version__!=declared['version'] or declared.get('sha256_kind')!='tree'
                or Path(module.__file__).resolve().parent!=Path(declared['path']).resolve()):
            raise ValueError('Actual Python package identity differs from admitted metadata: '+name)


def write_state(path,value):
    path=Path(path)
    pending=path.with_suffix('.pending')
    with pending.open('wb') as stream:
        stream.write(canonical(value));stream.flush();os.fsync(stream.fileno())
    os.replace(pending,path)


def build_config(resources):
    config="""includeConfig '/opt/nf-core/differentialabundance/nextflow.config'
process.executor = 'local'
process.container = null
process.cpus = __CPUS__
process.memory = '__MEMORY__ MB'
process.time = '__TASK_TIME__ sec'
process.errorStrategy = 'terminate'
process.maxRetries = 0
executor.queueSize = 1
docker.enabled = false
podman.enabled = false
singularity.enabled = false
apptainer.enabled = false
charliecloud.enabled = false
sarus.enabled = false
shifter.enabled = false
conda.enabled = false
wave.enabled = false
tower.enabled = false
fusion.enabled = false
telemetry.enabled = false
plugins { id 'nf-schema@2.5.1' }
trace { enabled = true; raw = true; file = '/tmp/community/trace.tsv'; fields = 'task_id,hash,name,status,exit,attempt,realtime,%cpu,peak_rss,read_bytes,write_bytes,cpus,memory,time,error_action' }
process {
    resourceLimits = [cpus:__CPUS__, memory:__MEMORY__.MB, time:__TASK_TIME__.sec]
    withName: '.*' {
        executor = 'local'
        cpus = __CPUS__
        memory = '__MEMORY__ MB'
        time = '__TASK_TIME__ sec'
        errorStrategy = 'terminate'
        maxRetries = 0
    }
}
"""
    return config.replace('__CPUS__',str(resources['cpu'])).replace('__MEMORY__',str(resources['memory_mb']//2)).replace('__TASK_TIME__',str(resources['timeout_seconds']-8))


def build_parameters(plan,root):
    s=plan['statistics'];transform=resolve_transform(plan)
    return {'input':str(root/'samples.csv'),'matrix':str(root/'counts.tsv'),'contrasts':str(root/'contrasts.csv'),
        'outdir':str(root/'published'),'study_type':'rnaseq','study_abundance_type':'counts','paramset_name':'rnaseq','features_id_col':'gene_id','differential_method':'deseq2',
        'deseq2_test':s['test'],'deseq2_fit_type':s['fit_type'],'deseq2_sf_type':s['normalization'],
        'deseq2_p_adjust_method':s['adjustment'],'deseq2_alpha':s['alpha'],
        'deseq2_independent_filtering':False,'deseq2_shrink_lfc':False,'deseq2_vs_method':transform['value'],
        'filtering_min_abundance':0,'filtering_min_samples':1,'functional_method':'none',
        'shinyngs_build_app':False,'shinyngs_deploy_to_shinyapps_io':False,'skip_reports':True,
        'custom_config_base':'/opt/nf-core/configs','seed':s['seed']}


def capture_file(path,maximum):
    path=Path(path)
    if not path.exists():
        return {'status':'absent','absent_reason':'not_created','original_size_bytes':None,'captured_size_bytes':0,'truncated':False}
    size=path.stat().st_size
    with path.open('rb') as stream: data=stream.read(maximum)
    return {'status':'captured','original_size_bytes':size,'captured_size_bytes':len(data),'truncated':size>len(data),
        'content_base64':base64.b64encode(data).decode('ascii'),'captured_sha256':hashlib.sha256(data).hexdigest(),
        'complete_sha256':hashlib.sha256(data).hexdigest() if size==len(data) else None}


def capture_execution_evidence(root,maximum=100000):
    root=Path(root)
    invocation=root/'invocation.json'
    return {'format':'devbench.community-execution-evidence/1',
        'invocation':json.loads(invocation.read_bytes()) if invocation.is_file() else {'status':'not_started'},
        'supplied_parameters':capture_file(root/'params.json',maximum),
        'supplied_config':capture_file(root/'local.config',maximum),
        'actual_resolved_config':{'status':'not_obtained','reason':'No engine config-resolution command was executed'},
        'trace':capture_file(root/'trace.tsv',maximum),
        'logs':{key:capture_file(root/name,maximum) for key,name in
            [('stdout','stdout.log'),('stderr','stderr.log'),('nextflow','nextflow.log')]}}


def parse_trace_text(text):
    attempts=[]
    for row in csv.DictReader(io.StringIO(text),delimiter='\t'):
        if len(attempts)>=128: raise ValueError('Task attempt bound exceeded')
        metrics={key:{'value':row[key],'unit':unit,'denominator':'task_attempt'} for key,unit in
            [('realtime','milliseconds'),('peak_rss','bytes'),('read_bytes','bytes'),('write_bytes','bytes')]
            if row.get(key) not in (None,'','-')}
        resources={key:{'value':row[key],'unit':unit} for key,unit in
            [('cpus','cpu'),('memory','bytes'),('time','milliseconds')] if row.get(key) not in (None,'','-')}
        attempts.append({'task_id':row.get('task_id'),'hash':row.get('hash'),'task':row['name'],
            'attempt':int(row.get('attempt','1')),'status':row['status'],
            'exit':int(row['exit']) if row.get('exit','').lstrip('-').isdigit() else None,
            'metrics':metrics,'resources':resources,'error_action':None if row.get('error_action') in (None,'','-') else row['error_action'],
            'absent_metrics':[key for key in ('realtime','peak_rss','read_bytes','write_bytes') if key not in metrics],
            'absent_resource_fields':[key for key in ('cpus','memory','time') if key not in resources]})
    return attempts


def run_engine(command,env,root,timeout):
    """Keep process-start and actual return code separate from worker outcomes."""
    root=Path(root)
    state={'engine_started':None,'engine_exit_code':None,'outcome':'start_intent'}
    if (root/'cancel.request').exists():
        state.update(engine_started=False,outcome='cancelled_before_start')
        write_state(root/'engine-state.json',state)
        return 143
    write_state(root/'engine-state.json',state)
    with (root/'stdout.log').open('wb') as stdout,(root/'stderr.log').open('wb') as stderr:
        try:
            process=subprocess.Popen(command,cwd=root,env=env,stdout=stdout,stderr=stderr,start_new_session=True)
        except Exception:
            state.update(engine_started=False,outcome='not_started');write_state(root/'engine-state.json',state)
            raise
        state.update(engine_started=True,engine_pid=process.pid,outcome='running')
        write_state(root/'engine-state.json',state)
        deadline=time.monotonic()+timeout
        reason=None
        while process.poll() is None:
            if (root/'cancel.request').exists(): reason='cancelled'
            elif time.monotonic()>=deadline: reason='timeout'
            if reason:
                try: os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError: pass
                try: process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try: os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError: pass
                    process.wait()
                break
            time.sleep(.025)
        code=process.wait()
        state.update(engine_exit_code=code,outcome=reason or ('completed' if code==0 else 'failed'))
        write_state(root/'engine-state.json',state)
        return 124 if reason=='timeout' else 143 if reason=='cancelled' else code


def _execute_workflow():
    # All state is bounded tmpfs. The outer container has its own independent
    # timeout; this child deadline cannot survive by leaving a detached engine.
    root=Path('/tmp/community');root.mkdir(mode=0o700,exist_ok=True)
    output=Path('/outputs')
    plan=json.loads(Path('/method/parameters.json').read_bytes())['plan']
    inventory=json.loads(Path('/method/environment.lock').read_bytes())
    installed=json.loads(Path('/opt/devbench/admission.json').read_bytes())
    if installed!={k:v for k,v in inventory.items() if k!='image'}: raise ValueError('Image admission inventory mismatch')
    # A build-time verified complete asset manifest is itself covered by the
    # immutable image digest. Recheck every admitted asset before engine start.
    assets=json.loads(Path('/opt/devbench/asset-manifest.json').read_bytes())
    if not isinstance(assets,dict) or not assets or len(assets)>50000: raise ValueError('Admitted installed asset manifest required')
    for name,expected in assets.items():
        if (root/'cancel.request').exists():raise InterruptedError('Cancellation requested during preflight; engine not started')
        path=Path(name)
        if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size>500_000_000:
            raise ValueError('Unsupported installed asset')
        h=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1048576),b''):
                if (root/'cancel.request').exists():raise InterruptedError('Cancellation requested during asset verification; engine not started')
                h.update(block)
        if h.hexdigest()!=expected: raise ValueError('Installed asset drift: '+name)
    verify_components(inventory,assets)
    verify_python_dependencies(inventory)
    matrix,_=convert_inputs('/inputs/matrix','/inputs/metadata',plan,root)
    s=plan['statistics']; session=os.environ['DEVBENCH_NEXTFLOW_SESSION_ID']
    transform=resolve_transform(plan)
    params=build_parameters(plan,root)
    write_json(root/'params.json',params)
    # -C replaces user/system Nextflow configs. Include only the pinned upstream
    # pipeline config, followed by these mandatory local overrides.
    resources=plan['resources']
    config=build_config(resources)
    (root/'local.config').write_text(config)
    env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/tmp','TMPDIR':'/tmp','NXF_HOME':str(root/'nxf-home'),'NXF_PLUGINS_DIR':'/opt/nextflow/plugins',
         'NXF_OFFLINE':'true','NXF_DISABLE_CHECK_LATEST':'true','NXF_ANSI_LOG':'false',
         'NXF_TEMP':str(root),'NXF_WORK':str(root/'work'),'NXF_UUID':session,
         'JAVA_TOOL_OPTIONS':'-Xmx512m -Djava.io.tmpdir=/tmp -Djava.net.preferIPv4Stack=true'}
    command=['/usr/local/bin/nextflow','-C',str(root/'local.config'),'-log',str(root/'nextflow.log'),
             'run','/opt/nf-core/differentialabundance/main.nf','-offline','-params-file',str(root/'params.json'),
             '-work-dir',str(root/'work')]
    # The supervisor's own deadline terminates the complete container after loss
    # of this worker/controller. Never use -resume with ephemeral cache.
    write_json(root/'invocation.json',{'argv':command,'environment':env})
    code=run_engine(command,env,root,resources['timeout_seconds']-6)
    trace=root/'trace.tsv'
    attempts=[]
    if trace.exists():
        if trace.stat().st_size>100000: raise ValueError('Trace exceeds diagnostic bound')
        attempts=parse_trace_text(trace.read_text())
    # Keep raw engine failure text, bounded and explicitly non-successful.
    diagnostics='\n'.join(p.open('rb').read(16000).decode('utf-8','replace') for p in (root/'stderr.log',root/'nextflow.log') if p.exists())
    session_observed=False
    if (root/'nextflow.log').is_file():
        with (root/'nextflow.log').open(errors='replace') as stream:
            session_observed=any('Session UUID: '+session in line for line in stream)
    tasks={'format':'devbench.community-task-evidence/1','nextflow_session_id':session,
        'engine_session_observed':session_observed,'producer':{'name':'Nextflow','version':inventory['nextflow']['version']},
        'attempts':attempts,'diagnostics':diagnostics[:32000],
        'engine_state':json.loads((root/'engine-state.json').read_bytes()),
        'execution_evidence':capture_execution_evidence(root,maximum=min(100000,resources['max_output_bytes']//12)),
        'metric_definitions':{'realtime':{'unit':'milliseconds','denominator':'task_attempt'},
            'peak_rss':{'unit':'bytes','denominator':'task_attempt'},'read_bytes':{'unit':'bytes','denominator':'task_attempt'},
            'write_bytes':{'unit':'bytes','denominator':'task_attempt'}},
        'absent_evidence':[] if trace.exists() else ['nextflow_trace']}
    for attempt in attempts:
        attempt['absent_metrics']=[key for key in tasks['metric_definitions'] if key not in attempt.get('metrics',{})]
    write_json(output/'task-evidence.json',tasks)
    if code: return code
    if not session_observed: raise ValueError('Actual Nextflow session UUID was not observed')
    candidates=list((root/'published').rglob('*.deseq2.results.tsv'))
    if len(candidates)!=1 or candidates[0].stat().st_size>plan['resources']['max_output_bytes']//2:
        raise ValueError('Exactly one bounded pinned DESeq2 result table required')
    # Upstream writes its feature column using params.features_id_col. Preserve
    # all raw numeric columns and NA text, rather than replacing missing values.
    shutil.copyfile(candidates[0],output/'gene-statistics.tsv')
    rows=[]
    with (output/'gene-statistics.tsv').open() as stream:
        for row in csv.DictReader(stream,delimiter='\t'):
            rows.append({'gene_id':row['gene_id'],**{k:None if row[k]=='NA' else float(row[k]) for k in ('log2FoldChange','pvalue','padj')}})
    d=plan['design']
    result={'format':'devbench.community-workflow-result/1','engine_status':'completed','engine_exit_code':0,
        'nextflow_session_id':session,'workflow_commit':COMMIT,'contrast':{'variable':d['condition'],'reference':d['reference'],'target':d['target']},
        'method':'DESeq2','test':'Wald','adjustment':'BH','shrinkage':'none','gene_statistics':rows,
        'scientific_qualification':'not_established','clinical_release_allowed':False}
    write_json(output/'result.json',result)
    artifacts={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()}
    write_json(output/'execution-manifest.json',{'format':'devbench.community-execution-manifest/1',
        'inventory_sha256':digest(inventory),'plan_sha256':digest(plan),'nextflow_session_id':session,
        'workflow_commit':COMMIT,'artifacts':artifacts,'resolved_configuration':{'deseq2_vs_method':transform},'cache_reuse':any(a['status']=='CACHED' for a in attempts),
        'execution_scope':'bounded local executor inside offline single-image OCI boundary; scientific qualification not established'})
    return 0


def main():
    root=Path('/tmp/community');root.mkdir(mode=0o700,exist_ok=True)
    output=Path('/outputs')
    write_state(root/'worker-state.json',{'state':'preflight','worker_exit_code':None})
    code=2;error=None
    try:
        code=_execute_workflow()
    except Exception as caught:
        error={'type':type(caught).__name__,'message':str(caught)[:2000]}
        print(error['type']+': '+error['message'],file=sys.stderr)
    finally:
        if (root/'cancel.request').exists(): code=143
        path=output/'task-evidence.json'
        tasks=json.loads(path.read_bytes()) if path.is_file() else {
            'format':'devbench.community-task-evidence/1','nextflow_session_id':os.environ.get('DEVBENCH_NEXTFLOW_SESSION_ID'),
            'attempts':[],'execution_evidence':capture_execution_evidence(root)}
        state=root/'engine-state.json'
        tasks['engine_state']=json.loads(state.read_bytes()) if state.is_file() else {'engine_started':False,'engine_exit_code':None,'outcome':'not_started'}
        tasks['worker_exit_code']=code;tasks['worker_error']=error
        write_json(path,tasks)
        manifest=output/'execution-manifest.json'
        if manifest.is_file():
            record=json.loads(manifest.read_bytes())
            record['artifacts']['task-evidence.json']=hashlib.sha256(path.read_bytes()).hexdigest()
            write_json(manifest,record)
        write_state(root/'worker-state.json',{'state':'cancelled' if code==143 else 'finished','worker_exit_code':code})
    return code


if __name__=='__main__':
    sys.exit(main())
