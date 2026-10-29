"""One inert local Python child exercises the frozen attachment close path.

No Docker, network, Codex, private state, auth stream or credential is involved.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
SOURCE=W/'scripts/run_gate1_personal_device_auth.py'
SOURCE_SHA='d2987a1f9f3ab53dffb3e311826e5733e205589f40caa8de08a734ba23a5550a'
ROOT=W/'reports/rna-native-gate1/2026-10-21-v2/auth-preparation/attachment-lifecycle-v1'


def main():
    ROOT.mkdir(mode=0o700,exist_ok=True)
    with os.fdopen(os.open(ROOT/'attempt.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as stream:
        stream.write('{"operation":"one_owned_inert_python_child"}\n');stream.flush();os.fsync(stream.fileno())
    if SOURCE.is_symlink()or hashlib.sha256(SOURCE.read_bytes()).hexdigest()!=SOURCE_SHA:raise PermissionError('frozen_source_changed')
    spec=importlib.util.spec_from_file_location('fixed_original_attachment',SOURCE);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.ROOT=ROOT
    environment={'PATH':'/usr/bin:/bin','HOME':str(ROOT),'LANG':'C'}
    command=[str(Path(sys.executable).absolute()),'-I','-B','-c','print(\'{"event":"owned_inert_exit"}\',flush=True)']
    result={'format':'devbench.owned-attachment-lifecycle/1','source_sha256':SOURCE_SHA,'docker_calls':0,
        'network_calls':0,'official_cli_invocations':0,'private_state_accessed':False,'observations':[]}
    attachment=m.Attachment(command,environment,1024);pid=attachment.process.pid
    original_exit=m._leader_exited;original_kill=m.os.killpg
    def observe_exit(target):
        if target!=pid:raise PermissionError('only_owned_child_observation')
        value=original_exit(target)
        if len(result['observations'])<16:result['observations'].append({'operation':'nonreaping_leader_check','exited':value})
        return value
    def observe_kill(target,sig):
        if target!=pid or sig!=signal.SIGKILL:raise PermissionError('only_owned_group_target')
        try:
            value=original_kill(target,sig);result['observations'].append({'operation':'kill_owned_group','outcome':'returned'});return value
        except OSError as error:
            result['observations'].append({'operation':'kill_owned_group','error_category':type(error).__name__,'errno':error.errno});raise
    m._leader_exited=observe_exit;m.os.killpg=observe_kill
    try:
        deadline=time.monotonic()+5;positive=False
        while not positive:
            if time.monotonic()>=deadline:raise TimeoutError('owned_child_output_deadline')
            for value in attachment.pump():
                if value!={'event':'owned_inert_exit'}:raise ValueError('fixed_owned_marker_only')
                positive=True
        result['owned_positive_marker_observed']=positive
        result['returncode_before_close']=attachment.process.returncode
        result['close_verdict']=attachment.close()
        result['returncode_after_close']=attachment.process.returncode
    except Exception as error:result['error_category']=type(error).__name__
    finally:
        m._leader_exited=original_exit;m.os.killpg=original_kill
        # Never signal a reaped PID. If close raised early, this still owns it.
        if attachment.process.returncode is None:
            try:original_kill(pid,signal.SIGKILL)
            except ProcessLookupError:pass
            attachment.process.wait(timeout=2)
        result['child_reaped']=attachment.process.returncode is not None
        for stream in [attachment.process.stdout,attachment.process.stderr]:stream.close()
        attachment.selector.close()
        with os.fdopen(os.open(ROOT/'result.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as stream:stream.write(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,sort_keys=True))
    return 0 if result.get('owned_positive_marker_observed')and result['child_reaped']else 1


if __name__=='__main__':raise SystemExit(main())
