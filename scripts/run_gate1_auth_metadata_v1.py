"""One released four-path lstat classification; no credentials or processes."""
import hashlib,json,os,stat,sys
from pathlib import Path
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
ROOT=W/'reports/rna-native-gate1/2026-10-21-v2/provider/auth-metadata/epoch4-v1'
SOURCE=W/'scripts/run_gate1_auth_metadata_v1.py'
B=W/'reports/rna-native-gate1/2026-10-21-v2'
PREREQUISITES=(B/'auth-preparation/login-attempt-v5/result.json',B/'reviews/personal-device-auth-postrun-v5.json',B/'provider/login-status/epoch4-v1/result.json',B/'reviews/epoch4-cached-login-status-postrun-v1.json')
PRIVATE=W/'.devbench-codex-auth'
PATHS=((PRIVATE,'missing_private_root',True),(PRIVATE/'epoch4','missing_epoch',True),
    (PRIVATE/'epoch4/codex-home','missing_codex_home',True),(PRIVATE/'epoch4/codex-home/auth.json','missing_auth_leaf',False))
FALSE={'live_generation_allowed':False,'biological_execution_allowed':False,'native_admission_qualified':False,
    'login_or_status_success_claimed':False,'cache_contents_read_hashed_or_copied':False,'private_directory_enumerated':False,
    'prior_results_modified':False,'Docker_CLI_network_or_ports_invoked':False}


def public_bytes(path):
    if path not in {SOURCE,ROOT/'release.json',*PREREQUISITES}:raise PermissionError('fixed_public_file_only')
    if path.is_symlink()or path.resolve()!=path or not path.is_file()or path.stat().st_size>65536:
        raise PermissionError('ordinary_bounded_public_file_required')
    return path.read_bytes()


def classify(inspect=os.lstat):
    for path,missing,directory in PATHS:
        try:info=inspect(path)
        except FileNotFoundError:return missing
        except OSError:return 'metadata_guard_rejected'
        regular=stat.S_ISDIR(info.st_mode)if directory else stat.S_ISREG(info.st_mode)
        if not regular or info.st_uid!=501 or stat.S_IMODE(info.st_mode)!=(0o700 if directory else 0o600):
            return 'metadata_guard_rejected'
    return 'metadata_guard_passed'


def write_exclusive(path,value):
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as f:
        f.write(json.dumps(value,sort_keys=True)+'\n');f.flush();os.fsync(f.fileno())


def main():
    if len(sys.argv)!=1:raise PermissionError('fixed_no_argument_metadata_operation')
    if ROOT.resolve()!=ROOT or not ROOT.is_dir():raise PermissionError('ordinary_public_attempt_root_required')
    release=json.loads(public_bytes(ROOT/'release.json'))
    if(release.get('accepted')is not True or release.get('scope')!='epoch4-known-auth-metadata-only'
        or release.get('diagnostic_purpose')!='classify-four-literal-owned-epoch4-paths'
        or any(release.get(k)is not False for k in ['live_generation_allowed','biological_execution_allowed','native_admission_qualified'])):
        raise PermissionError('distinct_independent_metadata_release_required')
    if set(release.get('source_sha256',{}))!={str(SOURCE)}or set(release.get('prerequisite_sha256',{}))!={str(p)for p in PREREQUISITES}:
        raise PermissionError('exact_public_metadata_pin_set_required')
    for group in ['source_sha256','prerequisite_sha256']:
        for path,expected in release[group].items():
            if hashlib.sha256(public_bytes(Path(path))).hexdigest()!=expected:raise PermissionError('metadata_release_pin_changed')
    failed=json.loads(public_bytes(PREREQUISITES[0]));review=json.loads(public_bytes(PREREQUISITES[1]));status=json.loads(public_bytes(PREREQUISITES[2]));status_review=json.loads(public_bytes(PREREQUISITES[3]))
    if(failed.get('private_root')!=str(PRIVATE/'epoch4')or failed.get('private_epoch_preserved_without_inspection')is not True
        or failed.get('owned_runtime_resources_removed')is not True or failed.get('source_custody_unchanged')is not True
        or failed.get('official_login_completed')is not False or review.get('accepted_official_device_auth_and_status')is not False
        or review.get('result_sha256')!=release['prerequisite_sha256'][str(PREREQUISITES[0])]
        or status.get('classification')!='known_auth_path_unavailable'or status.get('official_cli_invocations')!=0
        or status_review.get('diagnostic_evidence_reviewed')is not True
        or status_review.get('result_sha256')!=release['prerequisite_sha256'][str(PREREQUISITES[2])]):
        raise PermissionError('preserved_failed_epoch4_status_crosslink_required')
    if any((ROOT/name).exists()or(ROOT/name).is_symlink()for name in ['attempt.json','result.json']):raise FileExistsError('one_shot_metadata_attempt_required')
    write_exclusive(ROOT/'attempt.json',{'format':'devbench.auth-metadata-intent/1',**FALSE})
    value={'format':'devbench.auth-metadata-classification/1',**FALSE,'classification':classify(),
        'scope':'current_four_literal_path_metadata_only','prior_status_failure_cause_proven':False}
    write_exclusive(ROOT/'result.json',value);print(json.dumps(value,sort_keys=True),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
