from pathlib import Path
import importlib.util
from types import SimpleNamespace
import pytest
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')

def load():
    p=W/'scripts/gate1_attachment_cleanup.py';s=importlib.util.spec_from_file_location('owned_cleanup_under_test',p)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def attachment():
    class Process:
        pid=12345;returncode=None
        stdout=SimpleNamespace(close=lambda:None);stderr=SimpleNamespace(close=lambda:None)
        def wait(self,timeout):self.returncode=0;return 0
    return SimpleNamespace(process=Process(),selector=SimpleNamespace(close=lambda:None),buffer=bytearray())


def test_exited_empty_group_is_not_killed_and_second_close_never_touches_pid(monkeypatch):
    m=load();a=attachment();monkeypatch.setattr(m.sys,'platform','darwin')
    monkeypatch.setattr(m,'_leader_exited',lambda _pid:True);monkeypatch.setattr(m,'_darwin_group_members',lambda _pid:[])
    def forbidden(*_args,**_kwargs):raise AssertionError('must never target released or empty group')
    monkeypatch.setattr(m.os,'killpg',forbidden)
    assert m.close_attachment(a)is True
    monkeypatch.setattr(m,'_leader_exited',forbidden);monkeypatch.setattr(m,'_darwin_group_members',forbidden)
    assert m.close_attachment(a)is True
    assert a._closure_receipt['group_signal_attempted']is False


def test_live_owned_descendant_is_killed_before_leader_reap(monkeypatch):
    m=load();a=attachment();events=[];groups=iter([[23456],[]])
    monkeypatch.setattr(m.sys,'platform','darwin');monkeypatch.setattr(m,'_leader_exited',lambda _pid:True)
    monkeypatch.setattr(m,'_darwin_group_members',lambda _pid:next(groups));monkeypatch.setattr(m,'_process_running',lambda *_args,**_kwargs:True)
    monkeypatch.setattr(m.os,'killpg',lambda *_args:events.append('kill'))
    original=a.process.wait
    def wait(timeout):events.append('reap');return original(timeout)
    a.process.wait=wait
    assert m.close_attachment(a)is True and events==['kill','reap']


def test_already_reaped_without_owned_receipt_is_uncertain_without_pid_access(monkeypatch):
    m=load();a=attachment();a.process.returncode=0
    def forbidden(*_args,**_kwargs):raise AssertionError('released pid must not be queried')
    monkeypatch.setattr(m,'_leader_exited',forbidden);monkeypatch.setattr(m.os,'killpg',forbidden)
    assert m.close_attachment(a)is False and a._closure_receipt['already_reaped_without_receipt']
    assert m.close_attachment(a)is False


def test_disappearing_group_member_after_kill_does_not_trigger_redundant_signal(monkeypatch):
    m=load();a=attachment();events=[];checks=iter([True,ProcessLookupError()])
    monkeypatch.setattr(m.sys,'platform','darwin');monkeypatch.setattr(m,'_leader_exited',lambda _pid:True)
    monkeypatch.setattr(m,'_darwin_group_members',lambda _pid:[23456])
    def running(*_args,**_kwargs):
        value=next(checks)
        if isinstance(value,Exception):raise value
        return value
    monkeypatch.setattr(m,'_process_running',running);monkeypatch.setattr(m.os,'killpg',lambda *_args:events.append('kill'))
    assert m.close_attachment(a)is True and events==['kill']
    assert m.close_attachment(a)is True and events==['kill']


def test_live_leader_cannot_be_excused_by_empty_member_snapshot(monkeypatch):
    m=load();a=attachment();events=[];clock=iter([0,1,3,4,4.1])
    monkeypatch.setattr(m.sys,'platform','darwin');monkeypatch.setattr(m.time,'monotonic',lambda:next(clock));monkeypatch.setattr(m.time,'sleep',lambda _x:None)
    monkeypatch.setattr(m,'_leader_exited',lambda _pid:False);monkeypatch.setattr(m,'_darwin_group_members',lambda _pid:[])
    monkeypatch.setattr(m.os,'killpg',lambda *_args:events.append('kill'))
    assert m.close_attachment(a)is True and events==['kill']


def test_new_descendant_after_exited_snapshot_triggers_group_containment(monkeypatch):
    m=load();a=attachment();events=[];groups=iter([[23456],[34567],[]])
    monkeypatch.setattr(m.sys,'platform','darwin');monkeypatch.setattr(m,'_leader_exited',lambda _pid:True)
    monkeypatch.setattr(m,'_darwin_group_members',lambda _pid:next(groups));monkeypatch.setattr(m,'_process_running',lambda pid,**_kwargs:pid==34567)
    monkeypatch.setattr(m.os,'killpg',lambda *_args:events.append('kill'))
    assert m.close_attachment(a)is True and events==['kill']
