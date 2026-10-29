"""Fixed TLS-only entry; no auth action or private credential epoch is admitted."""
import hashlib
import importlib.util
from pathlib import Path
SUPERVISOR=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/run_gate1_personal_device_auth.py')
SUPERVISOR_SHA256='d2987a1f9f3ab53dffb3e311826e5733e205589f40caa8de08a734ba23a5550a'
def load_supervisor():
    if SUPERVISOR.is_symlink()or hashlib.sha256(SUPERVISOR.read_bytes()).hexdigest()!=SUPERVISOR_SHA256:
        raise PermissionError('exact_auth_supervisor_changed')
    spec=importlib.util.spec_from_file_location('fixed_tls_only_supervisor',SUPERVISOR)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.MODE='tls-only'
    module.ROOT=module.TLS_ROOT
    module.NAMES={key:'devharness-gate1-auth-tls-v1-'+key for key in ['inside','outside','proxy','probe','client']}
    return module
if __name__=='__main__':raise SystemExit(load_supervisor().main())
