"""Bounded file/JSON plumbing checks, with no expression data or model runs."""
import json
import pytest
from devbench.adapters import code_action
from devbench_engine.adapters import _read_answer


def test_answer_reader_supports_an_explicit_bounded_contract(tmp_path):
    (tmp_path / "final_discovery.json").write_text(json.dumps({"note": "x" * 40}))
    assert _read_answer(tmp_path, max_bytes=100)["note"] == "x" * 40
    with pytest.raises(ValueError, match="exceeds"):
        _read_answer(tmp_path, max_bytes=20)


def test_hashing_applies_the_requested_bound(tmp_path):
    path = tmp_path / "artifact.json"
    path.write_bytes(b"123456")
    assert code_action._file_hash(path, max_bytes=5) is None
    assert code_action._file_hash(path, max_bytes=6) is not None


def test_study_artifact_budget_is_explicit_and_bounded():
    adapter = code_action.CodeActionAdapter(artifact_max_bytes=8_000_000)
    assert adapter.artifact_max_bytes == 8_000_000
    with pytest.raises(ValueError, match="artifact"):
        code_action.CodeActionAdapter(artifact_max_bytes=8_000_001)


def test_larger_explicit_contract_reaches_nested_json_validators(tmp_path):
    # Text-only serialization plumbing; this is not a biological answer fixture.
    (tmp_path / "final_discovery.json").write_text(json.dumps({"note": "x" * 1_000_001}))
    assert len(_read_answer(tmp_path, max_bytes=8_000_000)["note"]) == 1_000_001
    with pytest.raises(ValueError, match="1 MB"):
        _read_answer(tmp_path)
