"""Parent attachments preserve independent proposals and immutable evidence."""
from copy import deepcopy
import importlib

import pytest

from devbench.analysis.workflow_state import digest, planning_binding
from tests.test_rna_investigation_policy import ref


def module():
    value = importlib.import_module("devbench.analysis.rna_investigations")
    assert hasattr(value, "reduce_investigation_refs"), "The deterministic reference reducer is missing"
    return value


def test_id_keyed_reducer_is_order_independent_and_idempotent():
    value = module()
    left = {"child-b": {"result_ref": ref("b"), "status": "failed"}}
    right = {"child-a": {"result_ref": ref("a"), "status": "completed"}}
    assert value.reduce_investigation_refs(left, right) == value.reduce_investigation_refs(right, left)
    assert value.reduce_investigation_refs(left, left) == left
    with pytest.raises(ValueError, match="[Cc]onflict"):
        value.reduce_investigation_refs(left, {"child-b": {"result_ref": ref("other"), "status": "failed"}})


def test_reducer_rejects_more_than_two_and_full_results():
    value = module()
    with pytest.raises(ValueError, match="two|bounded"):
        value.reduce_investigation_refs({}, {str(i): {"result_ref": ref(str(i)), "status": "completed"}
                                             for i in range(3)})
    with pytest.raises(ValueError, match="reference|exact"):
        value.reduce_investigation_refs({}, {"child": {"matrix": [[1]], "reasoning": "private"}})


def test_bundle_verifier_requires_actual_child_verification():
    value = importlib.import_module("devbench.analysis.rna_investigations")
    assert hasattr(value, "verify_investigation_bundle"), "The immutable bundle verifier is missing"
    with pytest.raises(ValueError, match="verifier|backend"):
        value.verify_investigation_bundle(None, ref("bundle"), plan={}, verify_child=None)


def test_immutable_json_resolves_exact_bytes_and_rejects_changed_manifest(tmp_path):
    value = importlib.import_module("devbench.analysis.rna_investigations")
    assert hasattr(value, "retain_investigation_json"), "The bounded immutable retention helper is missing"
    from tests.test_extensible_methods import prepared
    methods, *_ = prepared(tmp_path)
    record = {"format": "fixture", "stage": "pre_fit_methods", "disagreement": ["a", "b"]}
    artifact = value.retain_investigation_json(methods, record, actor="coordinator")
    assert value.read_investigation_json(methods, artifact) == record
    changed = deepcopy(artifact)
    changed["sha256"] = digest("forged")
    with pytest.raises(ValueError, match="reference|manifest"):
        value.read_investigation_json(methods, changed)


def test_bounded_projection_rejects_overflow_instead_of_truncation(tmp_path):
    value = importlib.import_module("devbench.analysis.rna_investigations")
    assert hasattr(value, "retain_investigation_json"), "The bounded immutable retention helper is missing"
    from tests.test_extensible_methods import prepared
    methods, *_ = prepared(tmp_path)
    with pytest.raises(ValueError, match="byte|budget"):
        value.retain_investigation_json(methods, {"reasoning": "x" * 64_001}, actor="coordinator", max_bytes=24_000)
