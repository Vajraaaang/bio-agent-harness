"""The study evidence format has an explicit integrity-only scope."""
import json

import pytest

from devbench_engine.audit_cli import replay_execution


@pytest.mark.parametrize('version', [1, 2])
def test_study_receipts_are_explicitly_out_of_replay_scope(tmp_path, version):
    root = tmp_path / 'root.json'
    root.write_text(json.dumps({'record_format': f'devbench.study.tool-receipts/{version}'}))
    output = tmp_path / 'replay'
    report = replay_execution(root, 'a' * 64, output)
    assert report['status'] == 'out_of_scope'
    assert report['reason_code'] == 'study_receipts_integrity_only'
    assert report['recommended_verifier'] == 'devbench-study verify-tools'
    assert report['passed'] is False and report['replayed_tool_count'] == 0
    assert report['model_calls'] == 0
    assert not output.exists()
