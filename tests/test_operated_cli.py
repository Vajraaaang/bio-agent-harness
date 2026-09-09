"""Actual local CLI parsing/registry checks, without Docker or numerical execution."""
import json
import os
import subprocess
import sys

import pytest

from devharness_controlled.run_registry import RunRegistry


def cli(*arguments):
    process = subprocess.run([sys.executable, "-m", "devharness_controlled.cli", *map(str, arguments)],
                             capture_output=True, text=True, check=False, timeout=15)
    return process, json.loads(process.stdout)


def test_status_checkpoint_and_prefix_verification_cli(tmp_path):
    registry = RunRegistry(tmp_path/"registry")
    attempt = registry.register("cli-request", "a"*64, str(tmp_path/"workspace"), "synthetic-operator", submission_sha256="b"*64)
    process, status = cli("registry-status", "--registry", registry.directory, "--attempt-id", attempt["attempt_id"])
    assert process.returncode == 0 and status["status"] == "registered"
    process, checkpoint = cli("registry-checkpoint", "--registry", registry.directory, "--output", tmp_path/"anchor.json")
    assert process.returncode == 0 and json.loads((tmp_path/"anchor.json").read_bytes()) == checkpoint
    registry.append_event(attempt["attempt_id"], "interrupted", {"reason": "synthetic test"}, "synthetic-operator")
    process, verified = cli("registry-verify", "--registry", registry.directory, "--checkpoint", tmp_path/"anchor.json")
    assert process.returncode == 0 and verified["externally_anchored"] is True and verified["later_event_count"] == 1


def test_read_commands_do_not_create_missing_registry(tmp_path):
    missing = tmp_path/"absent"
    process, result = cli("registry-verify", "--registry", missing)
    assert process.returncode != 0 and result["status"] == "operation_failed"
    assert not missing.exists() and result["execution_may_have_started"] is False


def test_checkpoint_reader_accepts_registry_sized_anchors_but_not_oversized_files(tmp_path):
    from devharness_controlled.cli import _json_file
    value = {"entries": [{"attempt_id": f"{i:032x}", "head": "a"*64} for i in range(10000)]}
    path = tmp_path/"large-checkpoint.json"
    path.write_text(json.dumps(value))
    assert 1024*1024 < path.stat().st_size < 4*1024*1024
    assert _json_file(path, checkpoint=True) == value
    with pytest.raises(ValueError):
        _json_file(path)
    path.write_bytes(b" "*(4*1024*1024+1))
    with pytest.raises(ValueError):
        _json_file(path, checkpoint=True)


def test_json_reader_does_not_block_on_named_pipe(tmp_path):
    from devharness_controlled.cli import _json_file
    path = tmp_path/"fifo"; os.mkfifo(path)
    with pytest.raises(ValueError):
        _json_file(path, checkpoint=True)


def test_duplicate_failed_submission_does_not_exit_successfully(tmp_path):
    from devharness_controlled.qualification import synthetic_approvals
    from tests.test_native_execution import plan
    p = plan(); approvals, signers = synthetic_approvals(p)
    for filename, value in (("plan.json", p.model_dump(mode="json")),
                            ("approvals.json", [approvals[0].model_dump(mode="json")]),
                            ("trust.json", [s.model_dump(mode="json") for s in signers])):
        (tmp_path/filename).write_text(json.dumps(value))
    arguments = ["submit-native", "--registry", tmp_path/"registry", "--request-id", "failed-duplicate",
                 "--actor-id", "synthetic-operator", "--plan", tmp_path/"plan.json", "--approvals", tmp_path/"approvals.json",
                 "--trust-registry", tmp_path/"trust.json", "--counts", tmp_path/"counts.csv", "--metadata", tmp_path/"metadata.csv",
                 "--full-design", tmp_path/"full.csv", "--reduced-design", tmp_path/"reduced.csv", "--output", tmp_path/"run"]
    first, initial = cli(*arguments)
    duplicate, existing = cli(*arguments)
    assert first.returncode == duplicate.returncode == 1
    assert initial["status"] == "failed" and existing["status"] == "existing_request"
    assert existing["attempt"]["status"] == "failed" and not existing["started_new_execution"]
