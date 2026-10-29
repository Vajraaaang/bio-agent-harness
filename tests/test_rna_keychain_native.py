"""Dummy-only native-helper regressions; never look up an actual credential."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def _api():
    assert importlib.util.find_spec('devbench.study.rna_keychain_native_helper') is not None, 'Bounded native credential helper absent'
    assert importlib.util.find_spec('devbench.study.rna_keychain_native_runtime') is not None, 'Packaged native runtime pins absent'
    from unittest.mock import patch
    with patch('ctypes.CDLL',side_effect=AssertionError('Import-time native lookup')),patch('subprocess.Popen',side_effect=AssertionError('Import-time subprocess')):
        from devbench.study import rna_keychain_native_helper as helper,rna_keychain_native_runtime as runtime
        from devbench.study import rna_hosted_exporter as exporter
    assert hasattr(exporter,'_native_credential_command'), 'Fixed supervised native child path absent'
    assert hasattr(exporter,'CredentialUnavailable'), 'Closed native failure categories absent'
    return helper,runtime,exporter


def test_packaged_native_runtime_and_fixed_command_are_exact():
    helper,runtime,exporter=_api();pins=runtime.NATIVE_RUNTIME
    assert set(pins)=={'format','executable','python_sha256','helper_sha256','flags','environment','stdlib_module_files'}
    assert pins['format']=='devbench.rna-native-keychain-runtime/1'
    assert pins['executable']=='/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.8/bin/python3.8'
    assert pins['python_sha256']=='f18f901e6f40d82f93f90950cf7384c25a4ffeee4d21b20fb405a020f47d9b9d'
    assert pins['flags']==['-I','-S','-B'] and pins['environment']=={'PATH':'/usr/bin:/bin'}
    assert len(pins['stdlib_module_files'])==119
    assert pins['helper_sha256']==hashlib.sha256(Path(helper.__file__).read_bytes()).hexdigest()
    assert exporter._native_credential_command()==[pins['executable'],'-I','-S','-B',str(Path(helper.__file__).resolve())]
    assert exporter._native_credential_environment()=={'PATH':'/usr/bin:/bin'}
    exporter._validate_native_credential_runtime()


def test_native_pin_drift_blocks_before_any_subprocess(monkeypatch):
    _,runtime,exporter=_api()
    monkeypatch.setitem(runtime.NATIVE_RUNTIME,'python_sha256','0'*64)
    monkeypatch.setattr(subprocess,'Popen',lambda *args,**kwargs:pytest.fail('Unpinned native process launched'))
    with pytest.raises(exporter.CredentialUnavailable) as caught:exporter._validate_native_credential_runtime()
    assert caught.value.reason_code=='credential_runtime_rejected'
    assert str(caught.value)=='Exporter credential unavailable'


def test_closed_credential_failure_cannot_accept_secret_reason():
    _,_,exporter=_api()
    with pytest.raises(ValueError) as caught:exporter.CredentialUnavailable('DUMMY-NOT-A-REASON')
    assert 'DUMMY-' not in str(caught.value)


TRUSTED_NATIVE_TEST=r'''
import ctypes,io,json,os,runpy,socket,subprocess,sys
def denied(*args,**kwargs):raise AssertionError('Dummy native test cannot access real native/password/network APIs')
subprocess.Popen=denied;subprocess.run=denied;os.system=denied;os.popen=denied
socket.socket.connect=denied;socket.socket.connect_ex=denied;socket.create_connection=denied
ctypes.CDLL=denied
helper_path=sys.argv[1];scenario=sys.argv[2]
sys.argv=[helper_path]+(['--check'] if scenario=='unexpected_args' else [])
calls={'default':0,'find':0,'free':0,'release':0}
dummy=b'DUMMY-ONLY-NATIVE-HELPER'
if scenario=='control':dummy=b'DUMMY\nCONTROL'
if scenario=='blank':dummy=b''
if scenario=='oversize':dummy=b'x'*257
buffer=ctypes.create_string_buffer(dummy)
class Fn:
    def __init__(self,kind):self.kind=kind
    def __call__(self,*args):
        calls[self.kind]+=1
        if self.kind=='default':
            if scenario=='default_error':return -25291
            if scenario!='default_null':ctypes.cast(args[0],ctypes.POINTER(ctypes.c_void_p))[0]=1
            return 0
        if self.kind=='find':
            assert args[1]==len(b'bio-agent-harness.langsmith') and args[2]==b'bio-agent-harness.langsmith'
            assert args[3]==len(b'bio-harness') and args[4]==b'bio-harness'
            errors={'not_found':-25300,'auth_denied':-25293,'interaction_denied':-25308,'unknown_error':-1}
            if scenario in errors:return errors[scenario]
            ctypes.cast(args[5],ctypes.POINTER(ctypes.c_uint32))[0]=len(dummy)
            ctypes.cast(args[6],ctypes.POINTER(ctypes.c_void_p))[0]=ctypes.addressof(buffer)
            return 0
        return 0
class Framework:
    def __init__(self,security):
        if security:
            self.SecKeychainCopyDefault=Fn('default');self.SecKeychainFindGenericPassword=Fn('find')
            self.SecKeychainItemFreeContent=Fn('free')
        else:self.CFRelease=Fn('release')
def fake_cdll(path):
    assert path in ('/System/Library/Frameworks/Security.framework/Security','/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    return Framework('Security.framework' in path)
ctypes.CDLL=fake_cdll
stdout=sys.stdout;captured=io.BytesIO();sys.stdout=io.TextIOWrapper(captured,encoding='utf-8',write_through=True)
try:runpy.run_path(helper_path,run_name='__main__');code=0
except SystemExit as error:code=error.code
sys.stdout.flush();emitted=captured.getvalue();sys.stdout=stdout
print(json.dumps({'code':code,'dummy_match':emitted==dummy+b'\n','stdout_empty':emitted==b'',
                  'calls':calls,'real_native_or_network_calls':0}))
'''


@pytest.mark.parametrize('scenario,code,find,free,release',[
    ('success',0,1,1,1),('unexpected_args',20,0,0,0),('default_error',22,0,0,0),('default_null',22,0,0,0),
    ('not_found',23,1,0,1),('auth_denied',24,1,0,1),('interaction_denied',25,1,0,1),
    ('unknown_error',26,1,0,1),('control',27,1,1,1),('blank',27,1,1,1),('oversize',27,1,1,1)])
def test_native_helper_fake_framework_exact_item_cleanup_and_no_secret_output(tmp_path,scenario,code,find,free,release):
    helper,runtime,exporter=_api();exporter._validate_native_credential_runtime()
    result=subprocess.run([runtime.NATIVE_RUNTIME['executable'],'-I','-S','-B','-c',TRUSTED_NATIVE_TEST,
        str(Path(helper.__file__).resolve()),scenario],env={'PATH':'/usr/bin:/bin'},cwd=tmp_path,
        stdin=subprocess.DEVNULL,close_fds=True,capture_output=True,text=True,check=True,timeout=5)
    observed=json.loads(result.stdout)
    assert observed['code']==code and observed['calls']=={'default':0 if scenario=='unexpected_args' else 1,'find':find,'free':free,'release':release}
    assert observed['dummy_match'] is (code==0) and observed['stdout_empty'] is (code!=0)
    assert observed['real_native_or_network_calls']==0 and result.stderr==''
    assert 'DUMMY-' not in result.stdout+result.stderr
