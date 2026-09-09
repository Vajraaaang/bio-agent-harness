"""Frozen synthetic endpoint-interval coverage checks through the real native CLI."""
from __future__ import annotations
import os
for _key in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","VECLIB_MAXIMUM_THREADS","NUMEXPR_NUM_THREADS"):
    os.environ[_key] = "1"
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time

import numpy as np
from patsy import dmatrix
from scipy.stats import beta as beta_distribution

from devharness_controlled.executor import _save
from devharness_controlled.native_contracts import NativeEffectPlan
from devharness_controlled.native_runtime import native_identity, DRIVER, PACKAGE_LOCK
from devharness_controlled.qualification import synthetic_approvals
from devharness_core.provenance import hash_artifact

REPO = Path(__file__).resolve().parent.parent
PROTOCOL = REPO/"validation/native_effect_coverage_v1.json"


def load(path):
    return json.loads(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_execution_status(case, exit_code):
    """Keep candidate output separate from CLI/semantic acceptance and failures."""
    objects={};read_errors=[]
    for name in ('execution/outcome.json','execution/controlled-verification.json','stdout.log'):
        path=case/name
        objects[name]={}
        if not path.is_file():continue
        try:
            with path.open('rb') as handle:raw=handle.read(1024*1024+1)
            if len(raw)>1024*1024:raise ValueError('status evidence exceeds 1 MiB')
            value=json.loads(raw)
            if not isinstance(value,dict):raise ValueError('status evidence must be an object')
            objects[name]=value
        except (OSError,ValueError) as error:
            read_errors.append(name+': '+str(error))
    outcome=objects['execution/outcome.json'];verification=objects['execution/controlled-verification.json'];cli=objects['stdout.log']
    usable=(exit_code==0 and outcome.get('status')=='analysis_complete_pending_review'
            and cli.get('status')=='analysis_complete_pending_review' and verification.get('valid') is True
            and verification.get('analysis_completed') is True and not verification.get('errors') and not read_errors)
    errors=verification.get('errors',[])
    error=None if usable else (cli.get('error') or outcome.get('error') or '; '.join(map(str,errors))
        or '; '.join(read_errors) or ('CLI exit '+str(exit_code)+'; no verified completed analysis'))
    return {'status':outcome.get('status') if usable else 'failed','candidate_status':outcome.get('status','incomplete'),
        'cli_status':cli.get('status'),'usable':usable,'error':error,'verification_errors':errors,'evidence_read_errors':read_errors}


def binomial_interval(successes, count, alpha=.05):
    if not count: return [None,None]
    return [0. if successes == 0 else float(beta_distribution.ppf(alpha/2,successes,count-successes+1)),
            1. if successes == count else float(beta_distribution.ppf(1-alpha/2,successes+1,count-successes))]


def curves(full, contrast):
    """Exactly model-space curves, including joint signal with zero endpoint effect."""
    t = np.repeat(np.arange(6,dtype=float),len(full)//6)
    basis = full[:,1:4]
    linear = np.linalg.lstsq(basis,(t-t.mean())/5*math.log(4),rcond=None)[0]
    neutral = np.array([1.,0.,0.])-contrast*np.dot(contrast,[1.,0.,0.])/np.dot(contrast,contrast)
    neutral *= math.log(4)/np.ptp(basis@neutral)
    nonlinear = contrast*math.log(4)/np.dot(contrast,contrast)
    return (linear,-linear,neutral,nonlinear)


def prepare(root):
    protocol=load(PROTOCOL); identity=native_identity()
    root.mkdir(parents=True,exist_ok=False,mode=0o700); frozen=root/"frozen";frozen.mkdir()
    for source,name in ((PROTOCOL,"protocol.json"),(Path(__file__).resolve(),"runner.py"),(DRIVER,"native_nb.R"),(PACKAGE_LOCK,"package-lock.json")):
        shutil.copyfile(source,frozen/name)
    _save(frozen/"identity.json",identity)
    seeds=np.random.SeedSequence(protocol["seed"]).spawn(len(protocol["cells"])*protocol["cohorts_per_cell"])
    schedule=[];position=0
    for cell in protocol["cells"]:
        n=cell["samples"]; m=protocol["panel_genes"]
        samples=[f"S{i:03d}" for i in range(n)];genes=[f"g{i:03d}" for i in range(m)]
        times=np.repeat(np.arange(6,dtype=float),n//6);batch=np.tile([0.,1.],n//2)
        full=np.column_stack((np.ones(n),np.asarray(dmatrix("cr(t, df=3, constraints='center') - 1",{"t":times})),batch))
        reference,comparison=0,5*(n//6);contrast=full[comparison,1:4]-full[reference,1:4]
        patterns=curves(full,contrast)
        for cohort in range(protocol["cohorts_per_cell"]):
            path=frozen/cell["id"]/f"cohort-{cohort:03d}"; inputs=path/"input";inputs.mkdir(parents=True)
            stream_seed=seeds[position];position+=1
            generators=[np.random.Generator(np.random.PCG64(s)) for s in stream_seed.spawn(5)]
            means_rng,library_rng,batch_rng,selected_rng,count_rng=generators
            base=np.exp(means_rng.uniform(math.log(2),math.log(600),m)); log_libraries=library_rng.normal(0,.7,n);log_libraries-=log_libraries.mean()
            coefficients=np.zeros((m,5));coefficients[:,0]=np.log(base);coefficients[:,4]=batch_rng.normal(0,.35,m)
            shape=["null"]*16
            for offset,(label,pattern) in enumerate(zip(("positive_linear","negative_linear","endpoint_neutral","nonlinear_positive"),patterns)):
                coefficients[16+4*offset:20+4*offset,1:4]=pattern;shape.extend([label]*4)
            mu=np.exp(coefficients@full.T+log_libraries)
            counts=count_rng.negative_binomial(1/cell["alpha"],1/(1+cell["alpha"]*mu))
            truth=coefficients[:32]@(full[comparison]-full[reference])/math.log(2)
            selected=int(selected_rng.integers(32))
            tables={"counts.csv":[["gene_id",*samples],*[[g,*row.tolist()] for g,row in zip(genes,counts)]],
                    "metadata.csv":[["sample_id","biological_unit_id","numeric_time"],*[[s,f"{cell['id']}-{cohort}-unit{i}",float(times[i])] for i,s in enumerate(samples)]],
                    "full.csv":[["sample_id","Intercept","Time1","Time2","Time3","Batch"],*[[s,*row.tolist()] for s,row in zip(samples,full)]],
                    "reduced.csv":[["sample_id","Intercept","Batch"],*[[s,*row.tolist()] for s,row in zip(samples,full[:,[0,4]])]]}
            for name,rows in tables.items():
                with (inputs/name).open("x",newline="") as handle:csv.writer(handle).writerows(rows)
            plan=NativeEffectPlan(plan_id=f"effect-{cell['id']}-{cohort:03d}",revision=1,intended_use_ref="synthetic-coverage-diagnostic-only",
                study_id="synthetic-effects-2026091017",data_classification="synthetic",engine="deseq2_lrt",normalization="rle",deseq2_fit_type="mean",
                hypothesis_genes=tuple(genes[:32]),panel_genes=tuple(genes),sample_ids=tuple(samples),temporal_columns=(2,3,4),time_unit="simulated-days",
                basis_definition_ref="fixed centered natural-cubic3-df basis; exact generating curves in saved full design",
                effect_name="time5-vs-time0",effect_reference_sample_id=samples[reference],effect_comparison_sample_id=samples[comparison],effect_confidence_level=.95,
                counts_sha256=hash_artifact(inputs/"counts.csv")["sha256"],metadata_sha256=hash_artifact(inputs/"metadata.csv")["sha256"],
                full_design_sha256=hash_artifact(inputs/"full.csv")["sha256"],reduced_design_sha256=hash_artifact(inputs/"reduced.csv")["sha256"],
                max_memory_mb=1024,max_cpus=1,timeout_sec=60,
                **{k:identity[k] for k in ("implementation_sha256","environment_sha256","driver_sha256","package_lock_sha256","runtime_image_id","platform")})
            approvals,signers=synthetic_approvals(plan)
            for name,value in (("plan.json",plan.model_dump(mode="json")),("approvals.json",[a.model_dump(mode="json") for a in approvals]),("trust.json",[s.model_dump(mode="json") for s in signers]),
                               ("truth.json",{"seed":stream_seed.state,"log_library_factors":log_libraries.tolist(),"coefficients_natural_log":coefficients.tolist(),"true_log2_contrast":dict(zip(genes[:32],truth.tolist())),"shape":dict(zip(genes[:32],shape)),"primary_selected_gene":genes[selected]})):
                _save(path/name,value)
            schedule.append({"cell":cell["id"],"cohort":cohort,"path":str(path)})
    _save(frozen/"schedule.json",schedule)
    freeze={"files":{str(p.relative_to(root)):digest(p) for p in sorted(frozen.rglob('*')) if p.is_file()},
            "source":{str(REPO/name):value for name,value in identity["source"].items()},"driver":{str(DRIVER):identity["driver_sha256"]},
            "runner":{str(Path(__file__).resolve()):digest(Path(__file__).resolve())},"protocol":{str(PROTOCOL):digest(PROTOCOL)},"before_native_fitting":True}
    for path in frozen.rglob('*'):path.chmod(0o555 if path.is_dir() else 0o444)
    frozen.chmod(0o555);_save(root/"FREEZE.json",freeze)
    print(json.dumps({"prepared":len(schedule),"freeze_sha256":digest(root/"FREEZE.json")}),flush=True)


def verify(root):
    f=load(root/"FREEZE.json")
    expected={**f['source'],**f['driver'],**f['runner'],**f['protocol'],**{str(root/k):v for k,v in f['files'].items()}}
    changed=[path for path,sha in expected.items() if not Path(path).is_file() or digest(Path(path))!=sha]
    if changed:raise ValueError("Frozen coverage inputs/source changed: "+str(changed[:5]))


def run(root):
    verify(root);protocol=load(root/'frozen/protocol.json');schedule=load(root/'frozen/schedule.json')
    outputs=root/'attempts';outputs.mkdir(exist_ok=False)
    def attempt(item):
        verify(root)
        source=Path(item['path']);case=outputs/item['cell']/f"cohort-{item['cohort']:03d}";case.mkdir(parents=True,exist_ok=False)
        command=[sys.executable,'-m','devharness_controlled.cli','execute-native','--plan',str(source/'plan.json'),'--approvals',str(source/'approvals.json'),
                 '--trust-registry',str(source/'trust.json'),'--counts',str(source/'input/counts.csv'),'--metadata',str(source/'input/metadata.csv'),
                 '--full-design',str(source/'input/full.csv'),'--reduced-design',str(source/'input/reduced.csv'),'--output',str(case/'execution')]
        _save(case/'command.json',command);_save(case/'started.json',{'wall_time':time.time(),'cell':item['cell'],'cohort':item['cohort']})
        start=time.monotonic()
        with (case/'stdout.log').open('xb') as out,(case/'stderr.log').open('xb') as err:
            done=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=out,stderr=err,check=False)
        result={'cell':item['cell'],'cohort':item['cohort'],'exit_code':done.returncode,'seconds':time.monotonic()-start,'usable':False,'primary_covered':None,'metrics':None}
        result.update(collect_execution_status(case,done.returncode))
        if result['usable']:
            native=load(case/'execution/native-output/result.json');truth=load(source/'truth.json')
            covered={g:bool(native['effects']['genes'][g]['ci_lower_log2']<=v<=native['effects']['genes'][g]['ci_upper_log2']) for g,v in truth['true_log2_contrast'].items()}
            errors={g:native['effects']['genes'][g]['log2_fold_change']-v for g,v in truth['true_log2_contrast'].items()}
            result['primary_covered']=covered[truth['primary_selected_gene']]
            result['metrics']={'coverage_fraction':float(np.mean(list(covered.values()))),'mean_bias':float(np.mean(list(errors.values()))),
                'mean_squared_error':float(np.mean(np.square(list(errors.values())))),
                'shapes':{shape:float(np.mean([covered[g] for g in covered if truth['shape'][g]==shape])) for shape in set(truth['shape'].values())}}
        _save(case/'result.json',result);verify(root)
        print(json.dumps({k:result[k] for k in ('cell','cohort','usable','status')}),flush=True)
        return result
    with ThreadPoolExecutor(max_workers=protocol['concurrency']) as pool:
        futures=[pool.submit(attempt,item) for item in schedule]
        records=[future.result() for future in as_completed(futures)]
    summary={'scope':protocol['scope'],'clinical_release_allowed':False,'planned':len(schedule),'records':sorted(records,key=lambda r:(r['cell'],r['cohort'])),'cells':{}}
    for cell in protocol['cells']:
        rows=[r for r in records if r['cell']==cell['id']];usable=[r for r in rows if r['usable']];success=sum(r['primary_covered'] is True for r in usable);n=len(usable)
        upper=1. if success==n else float(beta_distribution.ppf(1-.05/len(protocol['cells']),success+1,n-success)) if n else None
        summary['cells'][cell['id']]={'planned':protocol['cohorts_per_cell'],'attempted':len(rows),'usable':n,'missing':len(rows)-n,
            'random_selected_gene_covered':success,'random_selected_gene_coverage':success/n if n else None,'coverage_ci95':binomial_interval(success,n),
            'coverage_missingness_bounds':[success/len(rows),(success+len(rows)-n)/len(rows)],
            'simultaneous_diagnostic_upper':upper,'undercoverage_warning':bool(upper<.95) if n==len(rows) and upper is not None else None,
            'all_gene_mean_coverage':float(np.mean([r['metrics']['coverage_fraction'] for r in usable])) if n else None,
            'mean_bias':float(np.mean([r['metrics']['mean_bias'] for r in usable])) if n else None,
            'rmse':math.sqrt(float(np.mean([r['metrics']['mean_squared_error'] for r in usable]))) if n else None,
            'shape_coverage':{shape:float(np.mean([r['metrics']['shapes'][shape] for r in usable])) for shape in ('null','positive_linear','negative_linear','endpoint_neutral','nonlinear_positive')} if n else {}}
    verify(root);_save(root/'summary.json',summary)
    print(json.dumps(summary['cells']),flush=True)
    _save(root/'SEAL.json',{'files':{str(p.relative_to(root)):digest(p) for p in sorted(root.rglob('*')) if p.is_file()},'clinical_release_allowed':False})


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('prepare','run','verify'));parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not args.output.is_absolute() or args.output.resolve()!=args.output:parser.error('Absolute canonical output required')
    {'prepare':prepare,'run':run,'verify':verify}[args.action](args.output)


if __name__=='__main__':main()
