"""One reviewed owned network metadata probe; no container or public traffic."""
from __future__ import annotations
import hashlib,json,os,re,sys,time
from pathlib import Path
WORKSPACE=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
sys.path.insert(0,str(WORKSPACE))
from devbench.analysis.codex_isolated_boundary import DOCKER,SOCKET,closed_environment
from devbench.analysis.codex_signedin import _run_process
ROOT=WORKSPACE/'reports/rna-native-gate1/2026-10-21-v2/egress/network-profile-v2'
NAME='devharness-gate1-egress-profile-v2'

def digest(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size>100000000:raise PermissionError('ordinary_bounded_source_required')
    return hashlib.sha256(path.read_bytes()).hexdigest()

def verify_custody(release):
    for p in (ROOT,ROOT/'docker-client'):
        if p.is_symlink() or p.resolve()!=p or not p.is_dir():raise PermissionError('owned_directory_required')
    for p,h in release['source_sha256'].items():
        if digest(Path(p))!=h:raise PermissionError('reviewed_source_changed')
    if {p.name for p in(ROOT/'docker-client').iterdir()}!={'config.json'} or (ROOT/'docker-client/config.json').read_bytes()!=b'{"auths":{},"proxies":{}}\n':raise PermissionError('closed_config_required')

def main():
    target=ROOT/'result.json'
    if target.exists()or target.is_symlink():raise FileExistsError('one_shot_result_exists')
    release=json.loads((ROOT/'release.json').read_text())
    if release.get('accepted')is not True or release.get('scope')!='owned-network-metadata-only':raise PermissionError('exact_metadata_release_required')
    verify_custody(release)
    attempt={'release_sha256':digest(ROOT/'release.json'),'scope':'owned-network-metadata-only','live_generation_allowed':False,'biological_execution_allowed':False}
    with os.fdopen(os.open(ROOT/'attempt.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as f:f.write(json.dumps(attempt,sort_keys=True)+'\n')
    prefix=[DOCKER,'--config',str(ROOT/'docker-client'),'--host',SOCKET]
    result={'format':'devbench.owned-network-metadata/1','calls':[],'live_generation_allowed':False,'biological_execution_allowed':False,'identity_uncertain':False}
    identity=None
    def call(args,missing=False):
        verify_custody(release)
        raw=_run_process(prefix+args,cwd=ROOT,environment=closed_environment(ROOT/'docker-client'),prompt=b'',deadline=time.monotonic()+12,maximum=65536,authorize=lambda:None)
        result['calls'].append({'operation':args[:2],'exit_code':raw['exit_code'],'error':raw['error'],'process_reaped':raw['process_reaped'],'stdout_bytes':len(raw['stdout']),'stderr_bytes':len(raw['stderr'])})
        if missing:
            if raw['exit_code']!=1 or raw['error']is not None or not raw['process_reaped']or b'not found'not in raw['stderr']or identity.encode()not in raw['stderr']:raise RuntimeError('removal_not_verified')
        elif raw['exit_code']!=0 or raw['error']is not None or not raw['process_reaped']:raise RuntimeError('metadata_transport_failed')
        return raw['stdout']
    try:
        result['identity_uncertain']=True
        raw=call(['network','create','--driver','bridge','--internal','--ipv6=false','--opt','com.docker.network.bridge.inhibit_ipv4=true','--label','devbench.owned=gate1-egress-profile-v2',NAME])
        candidate=raw.decode().strip()
        if re.fullmatch('[0-9a-f]{64}',candidate)is None:raise RuntimeError('fresh_owned_id_required')
        identity=candidate;result['identity_uncertain']=False;result['owned_network_id']=identity
        records=json.loads(call(['network','inspect',identity]))
        if type(records)is not list or len(records)!=1 or records[0].get('Id')!=identity:raise ValueError('owned_record_required')
        record=records[0]
        # Retain bounded projected evidence BEFORE any profile comparison.
        actual={k:record.get(k)for k in ('Id','Name','Driver','Scope','Internal','EnableIPv4','EnableIPv6','Options')}
        actual['member_count']=len(record.get('Containers',{}));result['actual_network']=actual
        result['profile_matches_released_expectations']=(actual.get('Name')==NAME and actual.get('Driver')=='bridge'and actual.get('Internal')is True and actual.get('EnableIPv6')is False and actual.get('Options')=={'com.docker.network.bridge.inhibit_ipv4':'true'}and actual['member_count']==0)
    except Exception as error:result['error_category']=type(error).__name__
    finally:
        if identity:
            try:
                call(['network','rm',identity]);call(['network','inspect',identity],True);result['removal_verified']=True
            except Exception:result['removal_verified']=False
        try:verify_custody(release);result['source_custody_unchanged']=True
        except Exception:result['source_custody_unchanged']=False
        with os.fdopen(os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as f:f.write(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,sort_keys=True))
    return 0 if result.get('actual_network')is not None and result.get('removal_verified')is True and result.get('source_custody_unchanged')is True and not result['identity_uncertain']else 1

if __name__=='__main__':raise SystemExit(main())
