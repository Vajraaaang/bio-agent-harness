"""Idempotent owned-process closure, using existing scoped group inspection."""
from __future__ import annotations
import os
import signal
import subprocess
import sys
import time
from devbench.analysis.codex_signedin import _leader_exited,_darwin_group_members,_process_running


def owned_member_running(pid,pgid):
    try:return _process_running(pid,pgid=pgid)
    except ProcessLookupError:return False


def close_attachment(attachment):
    if hasattr(attachment,'_closure_receipt'):
        return attachment._closure_receipt['closed_verified']
    observation={'format':'devbench.owned-attachment-close/1','leader_observed_exited':False,
        'live_owned_group_observed':None,'group_signal_attempted':False,'group_signal_errno':None,
        'group_signal_error_category':None,'uncertain':False,'already_reaped_without_receipt':False}
    process=attachment.process;uncertain=False
    if process.returncode is not None:
        # Ownership of this PID has already ended. Never inspect or signal it.
        uncertain=True;observation['already_reaped_without_receipt']=True
    else:
        try:
            end=time.monotonic()+2
            while time.monotonic()<end:
                observation['leader_observed_exited']=_leader_exited(process.pid)
                if observation['leader_observed_exited']:break
                time.sleep(.01)
            needs_signal=True
            if sys.platform=='darwin':
                members=_darwin_group_members(process.pid)
                states=[owned_member_running(pid,process.pid)for pid in members]
                needs_signal=not observation['leader_observed_exited']or any(states)
                if not needs_signal:
                    again=_darwin_group_members(process.pid)
                    new_members=set(again)-set(members)
                    needs_signal=bool(new_members)or any(owned_member_running(pid,process.pid)for pid in again)
                observation['live_owned_group_observed']=needs_signal
            if needs_signal:
                observation['group_signal_attempted']=True
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                except OSError as error:
                    observation['group_signal_errno']=error.errno
                    observation['group_signal_error_category']=type(error).__name__;uncertain=True
                if sys.platform=='darwin'and not uncertain:
                    end=time.monotonic()+.5
                    while time.monotonic()<end:
                        members=_darwin_group_members(process.pid)
                        if not any(owned_member_running(pid,process.pid)for pid in members):break
                        time.sleep(.01)
                    else:uncertain=True
        except Exception as error:
            uncertain=True;observation['inspection_error_category']=type(error).__name__
            # The unreaped leader still leases this group; contain it safely.
            if not observation['group_signal_attempted']:
                observation['group_signal_attempted']=True
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                except OSError as failure:
                    observation['group_signal_errno']=failure.errno
                    observation['group_signal_error_category']=type(failure).__name__
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:uncertain=True
    try:attachment.selector.close()
    except Exception:uncertain=True
    for stream in [process.stdout,process.stderr]:
        try:stream.close()
        except Exception:uncertain=True
    attachment.buffer.clear()
    observation.update(uncertain=uncertain,leader_reaped=process.returncode is not None,
        exit_code=process.returncode,closed_verified=not uncertain and process.returncode is not None)
    attachment._closure_receipt=observation
    return observation['closed_verified']
