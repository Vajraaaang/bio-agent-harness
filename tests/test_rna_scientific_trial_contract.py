"""Exact-trial contracts do not grant model/data/clinical authority."""
from copy import deepcopy
from datetime import timedelta
import os
from pathlib import Path

import pytest

from devbench.analysis.rna_scientific_trial_contract import (
    bounded_source, trial_binding, validate_approval, validate_trial_request,
)
from tests.test_rna_scientific_trial import fixture, generated, approve, NOW, source


@pytest.mark.parametrize("path,value", [
    (("engineering", "qualified"), True), (("allocation", "total_tokens"), 0),
    (("allocation", "seconds"), float("nan")), (("allocation", "cost_ceiling"), float("inf")),
    (("dataset", "binding", "declarations", "representation"), "single_cell"),
    (("question", "source", "path"), "question.txt"), (("privacy", "full_data_local"), False),
    (("approvals", "workflow_generation", "purpose"), "trial_execution"),
    (("roles", "operator"), "implementer"), (("roles", "evaluator"), "participant"),
])
def test_closed_finite_request_rejects_malformed_scope(tmp_path, path, value):
    request, *_ = fixture(tmp_path)
    node = request
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ValueError):
        validate_trial_request(request)


def test_generation_and_execution_authorizations_have_distinct_bindings(tmp_path):
    request, *_ = generated(tmp_path)
    assert trial_binding(request, "workflow_generation") != trial_binding(request, "trial_execution")
    validate_approval(request, "workflow_generation", now=NOW)
    validate_approval(request, "trial_execution", now=NOW)
    request["allocation"]["total_tokens"] += 1
    with pytest.raises(PermissionError, match="binding"):
        validate_approval(request, "trial_execution", now=NOW)


def test_execution_approval_does_not_authorize_generation(tmp_path):
    request, *_ = fixture(tmp_path)
    request["approvals"]["workflow_generation"] = None
    approve(request, "trial_execution")
    with pytest.raises(PermissionError, match="Missing separate"):
        validate_approval(request, "workflow_generation", now=NOW)


def test_expiry_is_closed_and_independently_rechecked(tmp_path):
    request, *_ = fixture(tmp_path)
    request["approvals"]["workflow_generation"]["expires_at"] = "2026-10-07T01:00:00"
    with pytest.raises(ValueError, match="timezone"):
        validate_trial_request(request)
    request["approvals"]["workflow_generation"]["expires_at"] = "2026-10-07T01:00:00Z"
    validate_approval(request, "workflow_generation", now=NOW)
    with pytest.raises(PermissionError, match="expired"):
        validate_approval(request, "workflow_generation", now=NOW + timedelta(hours=2))


def test_local_source_cannot_escape_via_symlink_or_fifo(tmp_path):
    ref = source(tmp_path / "ordinary", "Synthetic bounded evidence")
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "ordinary")
    with pytest.raises(ValueError, match="regular"):
        bounded_source({**ref, "path": str(link)}, tmp_path)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="regular"):
        bounded_source({**ref, "path": str(fifo)}, tmp_path)


def test_bounded_source_reads_secure_actual_bytes_not_path_read_bytes(tmp_path, monkeypatch):
    ref = source(tmp_path / "record", "Bounded bytes")
    def forbidden(*args, **kwargs):
        pytest.fail("Unbounded Path.read_bytes reached")
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    assert bounded_source(ref, tmp_path) == b"Bounded bytes"
    (tmp_path / "record").write_bytes(b"x" * 1_000_001)
    with pytest.raises(ValueError, match="bounded"):
        bounded_source(ref, tmp_path)


def test_source_hash_mismatch_cannot_be_approved(tmp_path):
    request, *_ = fixture(tmp_path)
    ref = request["question"]["source"]
    Path(ref["path"]).write_text("X" * ref["size_bytes"])
    with pytest.raises(ValueError, match="digest"):
        bounded_source(ref, tmp_path)
