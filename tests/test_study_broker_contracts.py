"""Pure configuration/request and default-denial checks, without data or tools."""
import json
from pathlib import Path

import pytest

from devbench.study.broker import BrokerPolicy, ToolBroker, parse_request
from devbench.study.admission import inspect_admission
from devbench.study.transport import BrokerTransport


def policy():
    return BrokerPolicy("enforced", "a" * 64, "raw_counts", ("gene-id",),
                        False, 60, "b" * 64)


def test_default_guards_precede_metadata_or_broker_operations():
    with pytest.raises(PermissionError, match="disabled"):
        inspect_admission(Path("/nonexistent"), unit_provenance_verified=True)
    with pytest.raises(PermissionError, match="disabled"):
        ToolBroker(policy(), {}, Path("/nonexistent"))
    with pytest.raises(PermissionError, match="cannot start"):
        BrokerTransport(None, Path("/nonexistent"))


def test_design_path_alias_matches_shared_participant_contract():
    broker = ToolBroker.__new__(ToolBroker)
    broker.aliases = {"/opt/participant/metadata.csv": Path("/opt/private/metadata.csv")}
    args = broker._backend_arguments("validate_design", {
        "metadata_path": "/opt/participant/metadata.csv", "formula_str": "1 + numeric_time + C(batch)"})
    assert args["metadata_csv_path"] == "/opt/private/metadata.csv"
    assert "metadata_path" not in args


@pytest.mark.parametrize("change", [{"time_col": "nonexistent"}, {"biological_unit_column": "sample_id"}])
def test_admission_rejects_alternative_columns_before_reading(change):
    broker = ToolBroker.__new__(ToolBroker)
    broker.policy = policy()
    with pytest.raises(ValueError, match="protected task contract"):
        broker._observed_admission({"metadata_path": "/nonexistent", **change})


def test_capability_required_and_removed_from_retained_request():
    capability = "c" * 43
    payload = json.dumps({"tool": "inspect_study_design", "arguments": {}, "capability": capability}).encode()
    assert parse_request(payload, expected_capability=capability) == {"tool": "inspect_study_design", "arguments": {}}
    with pytest.raises(PermissionError):
        parse_request(payload, expected_capability="d" * 43)


@pytest.mark.parametrize("path", ["/tmp/private-study", "/private/tmp/private-study"])
def test_protected_storage_cannot_be_under_ambient_scratch(path):
    from devbench.study.permissions import require_protected_storage
    with pytest.raises(ValueError, match="outside platform scratch"):
        require_protected_storage(Path(path))
