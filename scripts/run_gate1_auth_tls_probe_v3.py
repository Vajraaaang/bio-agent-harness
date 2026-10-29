"""Fixed TLS-only entry; no auth action or private credential epoch is admitted."""
import hashlib
import importlib.util
from pathlib import Path
SUPERVISOR=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/run_gate1_personal_device_auth.py')
SUPERVISOR_SHA256='d2987a1f9f3ab53dffb3e311826e5733e205589f40caa8de08a734ba23a5550a'
def normalize_proxy_unset_dns(record,role):
    """Only measured null/[] proxy DNS defaults normalize to one unset value."""
    if role!='proxy':return record
    host=record.get('HostConfig',{})
    if 'Dns'not in host:raise PermissionError('explicit_proxy_dns_representation_required')
    value=host['Dns']
    if value is not None and not(type(value)is list and len(value)==0):
        raise PermissionError('unset_proxy_dns_only')
    normalized=dict(record);normalized['HostConfig']=dict(host);normalized['HostConfig']['Dns']=[]
    return normalized

def load_supervisor():
    if SUPERVISOR.is_symlink()or hashlib.sha256(SUPERVISOR.read_bytes()).hexdigest()!=SUPERVISOR_SHA256:
        raise PermissionError('exact_auth_supervisor_changed')
    spec=importlib.util.spec_from_file_location('fixed_tls_only_v3_supervisor',SUPERVISOR)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.MODE='tls-only'
    module.ROOT=module.WORKSPACE/'reports/rna-native-gate1/2026-10-21-v2/auth-preparation/tls-attempt-v3'
    module.NAMES={key:'devharness-gate1-auth-tls-v3-'+key for key in ['inside','outside','proxy','probe','client']}
    original_verify=module.verify_container
    def verify_with_measured_proxy_dns(record,role,identity,networks,initial):
        return original_verify(normalize_proxy_unset_dns(record,role),role,identity,networks,initial)
    module.verify_container=verify_with_measured_proxy_dns
    return module
if __name__=='__main__':raise SystemExit(load_supervisor().main())
