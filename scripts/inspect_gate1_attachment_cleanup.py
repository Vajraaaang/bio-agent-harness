"""Bounded local, inert process controls for the new closure adaptor."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness');sys.path.insert(0,str(W))
from scripts.gate1_attachment_cleanup import close_attachment
SOURCE=W/'scripts/run_gate1_personal_device_auth.py'
SOURCE_SHA='d2987a1f9f3ab53dffb3e311826e5733e205589f40caa8de08a734ba23a5550a'
ROOT=W/'reports/rna-native-gate1/2026-10-21-v2/auth-preparation/attachment-cleanup-v2'


def main():
    ROOT.mkdir(mode=0o700,exist_ok=True)
    with os.fdopen(os.open(ROOT/'attempt.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as stream:
        stream.write('{"operation":"two_owned_inert_child_controls"}\n');stream.flush();os.fsync(stream.fileno())
    if SOURCE.is_symlink()or hashlib.sha256(SOURCE.read_bytes()).hexdigest()!=SOURCE_SHA:raise PermissionError('frozen_source_changed')
    spec=importlib.util.spec_from_file_location('fixed_local_attachments',SOURCE);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.ROOT=ROOT
    cases=[]
    for descendant in [False,True]:
        code='print(\'{"event":"owned_inert_exit"}\',flush=True)'
        if descendant:code='import subprocess,sys;subprocess.Popen([sys.executable,"-I","-B","-c","import time;time.sleep(10)"],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);'+code
        command=[str(Path(sys.executable).absolute()),'-I','-B','-c',code]
        attachment=m.Attachment(command,{'PATH':'/usr/bin:/bin','HOME':str(ROOT),'LANG':'C'},1024)
        case={'kind':'live_descendant'if descendant else'exited_leader','positive_observed':False}
        try:
            deadline=time.monotonic()+5
            while not case['positive_observed']:
                if time.monotonic()>=deadline:raise TimeoutError('bounded_owned_control')
                for value in attachment.pump():
                    if value!={'event':'owned_inert_exit'}:raise ValueError('fixed_owned_marker_only')
                    case['positive_observed']=True
            case['first_close']=close_attachment(attachment);case['receipt']=dict(attachment._closure_receipt)
            case['second_close']=close_attachment(attachment)
            case['idempotent_receipt']=case['receipt']==attachment._closure_receipt
        except Exception as error:case['error_category']=type(error).__name__
        finally:
            if attachment.process.returncode is None:
                try:os.killpg(attachment.process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                attachment.process.wait(timeout=2)
            for stream in [attachment.process.stdout,attachment.process.stderr]:stream.close()
            attachment.selector.close()
            case['exit_code']=attachment.process.returncode
        cases.append(case)
    result={'format':'devbench.attachment-cleanup-controls/1','docker_calls':0,'network_calls':0,'official_cli_invocations':0,
        'private_state_accessed':False,'cases':cases,'passed':all(x.get('positive_observed')and x.get('first_close')and x.get('second_close')and x.get('idempotent_receipt')and x.get('exit_code')==0 for x in cases)}
    with os.fdopen(os.open(ROOT/'result.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as stream:stream.write(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,sort_keys=True));return 0 if result['passed']else 1


if __name__=='__main__':raise SystemExit(main())
