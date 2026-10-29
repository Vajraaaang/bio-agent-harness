import importlib.util
from pathlib import Path
from copy import deepcopy
import pytest
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')

def load():
    p=W/'scripts/run_gate1_personal_device_auth_v2.py';s=importlib.util.spec_from_file_location('auth_v2_under_test',p)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def proof():
    return {'error_category':'RuntimeError','owned_runtime_resources_removed':True,'source_custody_unchanged':True,
        'tls_probe':{'event':'tls_positive','hostname_verified':True,'certificate_chain_verified':True,
            'http_application_bytes_sent':0,'tls_version':'TLSv1.3','actual_cli_tls_validation_is_separate':True}}


def test_scoped_tls_fact_does_not_rewrite_failed_overall_history():
    m=load();value=proof();before=deepcopy(value)
    assert m.verified_tls_fact(value)is True and value==before
    assert value['error_category']=='RuntimeError'and 'tls_only_probe_completed'not in value
    for key,bad in [('certificate_chain_verified',False),('http_application_bytes_sent',False),('tls_version','TLSv1')]:
        changed=deepcopy(value);changed['tls_probe'][key]=bad
        assert m.verified_tls_fact(changed)is False


def test_auth_v2_binds_correct_proof_scope_and_no_public_tls_retry():
    m=load();assert m.MODE=='device-login'and str(m.TLS_ROOT).endswith('/tls-attempt-v3')
    assert str(m.ROOT).endswith('/login-attempt-v2')
    assert m.container_arguments('client','inside-id')[-1]=='device-login'
    source=(W/'scripts/run_gate1_personal_device_auth_v2.py').read_text()
    assert 'reviews/auth-tls-postrun-v3.json'in source
    assert "proof.get('tls_only_probe_completed')is not True"not in source


def test_auth_v2_private_cache_digest_remains_forbidden():
    m=load()
    with pytest.raises(PermissionError):m.digest(m.PRIVATE/'codex-home/auth.json')


def test_auth_v2_attachment_dispatches_only_bound_cleanup_helper(monkeypatch):
    m=load();calls=[];attachment=object()
    monkeypatch.setattr(m,'_CLOSE_ATTACHMENT',lambda value:calls.append(value)or True)
    assert m.Attachment.close(attachment)is True and calls==[attachment]
