"""Operated authority CLI with real signatures and SQLite; no numerical doubles."""
import json
from datetime import datetime, timezone
import subprocess
import sys
from pathlib import Path

from tests.test_governed_native_execution import fixture
from devharness_controlled.executor import _save
from devharness_controlled.authority_contracts import AuthorityPolicy


def cli(*arguments):
    return subprocess.run([sys.executable,"-m","devharness_controlled.cli",*map(str,arguments)],
        stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=30)


def test_exported_history_requires_external_roots_and_preserves_checkpoint(tmp_path):
    case=fixture(tmp_path)
    roots=tmp_path/"administrators.json";snapshot=tmp_path/"snapshot.json";checkpoint=tmp_path/"checkpoint.json"
    _save(roots,[case.admin.model_dump(mode="json")])
    result=cli("authority-export","--registry",case.store.root,"--administrators",roots,
        "--output",snapshot,"--checkpoint-output",checkpoint)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)["policy_signatures_verified"]==1
    result=cli("authority-verify","--snapshot",snapshot,"--administrators",roots,"--checkpoint",checkpoint)
    assert result.returncode==0 and json.loads(result.stdout)["valid"]
    wrong=tmp_path/"wrong.json";_save(wrong,[])
    result=cli("authority-verify","--snapshot",snapshot,"--administrators",wrong)
    assert result.returncode==1 and json.loads(result.stdout)["status"]=="operation_failed"


def test_cli_initialization_has_no_implicit_grants(tmp_path):
    result=cli("authority-init","--registry",tmp_path.resolve()/"empty","--authority-id","new-authority")
    assert result.returncode==0,result.stdout+result.stderr
    data=json.loads(result.stdout)
    assert data["status"]=="empty_no_grants" and data["clinical_release_allowed"] is False
    assert data["metadata"]["authority_id"]=="new-authority"


def test_exports_cannot_overwrite_or_contaminate_live_store(tmp_path):
    case=fixture(tmp_path);roots=tmp_path/"administrators.json";_save(roots,[case.admin.model_dump(mode="json")])
    result=cli("authority-export","--registry",case.store.root,"--administrators",roots,
        "--output",case.store.root/"extra.json","--checkpoint-output",tmp_path/"checkpoint.json")
    assert result.returncode==1 and not (case.store.root/"extra.json").exists()
    snapshot=tmp_path/"snapshot.json";snapshot.write_text("preserve")
    result=cli("authority-export","--registry",case.store.root,"--administrators",roots,
        "--output",snapshot,"--checkpoint-output",tmp_path/"checkpoint.json")
    assert result.returncode==1 and snapshot.read_text()=="preserve"


def publish_arguments(tmp_path, case, output):
    roots=tmp_path/"administrators.json"; policy=tmp_path/"policy.json"
    _save(roots,[case.admin.model_dump(mode="json")])
    next_policy=AuthorityPolicy(**{**case.policy.model_dump(),"revision":2,
        "previous_sha256":case.checkpoint["tip_sha256"],"issued_at":datetime.now(timezone.utc),
        "change_reason":"Synthetic revocation","grants":()})
    _save(policy,case.sign(next_policy).model_dump(mode="json"))
    return ["authority-publish","--registry",case.store.root,"--administrators",roots,
        "--policy",policy,"--expected-tip",case.checkpoint["tip_sha256"],"--output",output]


def test_export_symlink_parent_cannot_contaminate_store(tmp_path):
    case=fixture(tmp_path); roots=tmp_path/"roots.json"
    _save(roots,[case.admin.model_dump(mode="json")])
    alias=tmp_path/"alias"; alias.symlink_to(case.store.root,target_is_directory=True)
    before=case.store.capture()
    result=cli("authority-export","--registry",case.store.root,"--administrators",roots,
        "--output",alias/"snapshot.json","--checkpoint-output",tmp_path/"checkpoint.json")
    assert result.returncode==1
    assert case.store.capture()==before
    assert not (tmp_path/"checkpoint.json").exists()


def test_publish_missing_export_parent_is_rejected_before_commit(tmp_path):
    case=fixture(tmp_path); before=case.store.capture()
    result=cli(*publish_arguments(tmp_path,case,tmp_path/"missing"/"checkpoint.json"))
    assert result.returncode==1
    assert case.store.capture()==before
    assert json.loads(result.stdout)["publication_may_have_committed"] is False


def test_publish_reports_committed_checkpoint_when_export_fails(tmp_path,monkeypatch,capsys):
    from devharness_controlled import cli as module
    case=fixture(tmp_path)
    arguments=publish_arguments(tmp_path,case,tmp_path/"checkpoint.json")
    def fail(*args,**kwargs): raise OSError("Synthetic post-commit export failure")
    monkeypatch.setattr(module,"_write_authority_export",fail,raising=False)
    monkeypatch.setattr(sys,"argv",["devharness-controlled",*map(str,arguments)])
    assert module.main()==1
    result=json.loads(capsys.readouterr().out)
    assert result["status"]=="published_export_failed"
    assert result["publication_may_have_committed"] is True
    assert result["published_checkpoint"]==case.store.checkpoint([case.admin])
