"""Shared method identities and the trusted dispatcher compatibility boundary."""

import hashlib
import json
from pathlib import Path

import pytest

from devharness_core.provenance import canonical_json


def _payload():
    return {
        "format": "devharness.method/1",
        "method_id": "custom.robust-mad-qc",
        "name": "Robust MAD QC",
        "lifecycle": "custom",
        "implementation_trust": "unreviewed",
        "inputs": {"dataset": {"artifact_type": "anndata", "required": True}},
        "parameters": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object", "additionalProperties": False,
            "properties": {"threshold": {"type": "number", "exclusiveMinimum": 0, "default": 4}},
        },
        "outputs": {"dataset": {"artifact_type": "anndata", "path": "/outputs/filtered.h5ad", "required": True}},
        "implementation": {"kind": "oci", "source_sha256": "a" * 64, "entrypoint": ["python", "/method/main.py"]},
        "environment": {"image": "example/science@sha256:" + "b" * 64, "lock_sha256": "c" * 64},
        "execution": {"cpu": 1, "memory_mb": 256, "timeout_seconds": 30, "pids": 32,
                      "max_output_bytes": 1_000_000, "network": "deny"},
        "validation": {"comparator": "single_cell_qc", "invariants": ["variables_unchanged"],
                       "counts_layer": "X", "qc_metrics": []},
        "provenance": {"origin": "test"},
    }


def _contract(payload=None):
    from devharness_core.methods import MethodContract
    return MethodContract.from_dict(_payload() if payload is None else payload)


def test_contract_identity_and_nested_state_are_immutable():
    payload = _payload()
    expected = hashlib.sha256(canonical_json(payload)).hexdigest()
    contract = _contract(payload)
    payload["implementation"]["source_sha256"] = "d" * 64
    exposed = contract.to_dict()
    exposed["outputs"]["dataset"]["path"] = "/outputs/other.h5ad"
    assert contract.version_id == expected
    assert contract.to_dict() == _payload()
    with pytest.raises(AttributeError):
        contract.version_id = "forged"


def test_parameter_validation_copies_and_does_not_apply_defaults():
    contract = _contract()
    assert contract.validate_parameters({}) == {}
    params = {"threshold": 4}
    validated = contract.validate_parameters(params)
    validated["threshold"] = 5
    assert params == {"threshold": 4}


def _scientific_metadata():
    return {
        "tasks": ["quality_control"],
        "assumptions": ["Observations represent individual cells"],
        "preconditions": [
            {"fact": "raw_counts", "operator": "equals", "value": True,
             "rationale": "This method requires untransformed counts"},
            {"fact": "biological_replicates", "operator": "at_least", "value": 3,
             "rationale": "Replication is required for the intended comparison"},
        ],
        "output_semantics": "Filtered raw counts with retained observation identities",
        "implementation_origin": "generated",
        "references": [{"url": "https://example.org/method", "kind": "primary_paper", "version": "1"}],
    }


def test_optional_scientific_metadata_is_versioned_without_changing_old_contracts():
    payload = _payload()
    old = _contract(payload)
    assert "scientific" not in old.to_dict()
    assert old.version_id == hashlib.sha256(canonical_json(payload)).hexdigest()
    payload["scientific"] = _scientific_metadata()
    described = _contract(payload)
    assert described.version_id != old.version_id
    assert described.to_dict()["scientific"] == _scientific_metadata()
    payload["scientific"]["preconditions"][0]["value"] = False
    assert described.to_dict()["scientific"]["preconditions"][0]["value"] is True


def test_scientific_metadata_helpers_return_defensive_copies():
    from devharness_core.methods import scientific_metadata_schema, validate_scientific_metadata
    from jsonschema import Draft202012Validator

    metadata = _scientific_metadata()
    validated = validate_scientific_metadata(metadata)
    validated["preconditions"][0]["value"] = False
    assert metadata["preconditions"][0]["value"] is True
    schema = scientific_metadata_schema()
    Draft202012Validator.check_schema(schema)
    schema["properties"]["tasks"]["maxItems"] = 0
    assert scientific_metadata_schema()["properties"]["tasks"]["maxItems"] == 16
    assert validate_scientific_metadata(metadata) == metadata


@pytest.mark.parametrize("path,value", [
    (("unexpected",), True),
    (("tasks",), []),
    (("tasks",), ["qc", "qc"]),
    (("tasks",), ["task_" + str(i) for i in range(17)]),
    (("tasks",), ["x" * 4097]),
    (("assumptions",), ["assumption"] * 33),
    (("preconditions",), _scientific_metadata()["preconditions"] * 17),
    (("preconditions", 0, "fact"), "bad fact"),
    (("preconditions", 0, "fact"), "x" * 129),
    (("preconditions", 0, "operator"), "contains"),
    (("preconditions", 0, "value"), None),
    (("preconditions", 0, "value"), [1, 2]),
    (("preconditions", 0, "value"), float("nan")),
    (("preconditions", 0, "value"), float("inf")),
    (("preconditions", 0, "surprise"), True),
    (("preconditions", 1, "value"), True),
    (("preconditions", 1, "value"), "3"),
    (("output_semantics",), ""),
    (("implementation_origin",), "trusted"),
    (("references",), _scientific_metadata()["references"] * 33),
    (("references", 0, "kind"), "blog"),
    (("references", 0, "version"), ""),
    (("references", 0, "extra"), "unsupported"),
])
def test_invalid_scientific_metadata_is_rejected_by_helper_and_contract(path, value):
    from devharness_core.methods import validate_scientific_metadata

    metadata = _scientific_metadata()
    target = metadata
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        validate_scientific_metadata(metadata)
    payload = _payload()
    payload["scientific"] = metadata
    with pytest.raises(ValueError):
        _contract(payload)


def test_duplicate_scientific_precondition_fact_operator_is_rejected():
    from devharness_core.methods import validate_scientific_metadata

    metadata = _scientific_metadata()
    metadata["preconditions"].append(dict(metadata["preconditions"][0], value=False))
    with pytest.raises(ValueError, match="Duplicate"):
        validate_scientific_metadata(metadata)
    payload = _payload()
    payload["scientific"] = metadata
    with pytest.raises(ValueError, match="Duplicate"):
        _contract(payload)


@pytest.mark.parametrize("url", [
    "turn5search25", "doi:10.1/example", "ftp://example.org/method", "javascript:alert(1)",
    "https://", "https:///method", "https://?method", "https://#method", "https://[broken/method",
    "https://example.org:bad/method", "https://example.org/bad path", "https://example.org/\nmethod",
])
def test_scientific_reference_requires_an_http_url_with_host(url):
    from devharness_core.methods import validate_scientific_metadata

    metadata = _scientific_metadata()
    metadata["references"][0]["url"] = url
    with pytest.raises(ValueError):
        validate_scientific_metadata(metadata)


@pytest.mark.parametrize("value", ["droplet", 3, 2.5, False])
def test_scientific_equals_precondition_accepts_json_scalar_values(value):
    from devharness_core.methods import validate_scientific_metadata

    metadata = _scientific_metadata()
    metadata["preconditions"][0]["value"] = value
    metadata["references"][0]["url"] = "http://example.org/method"
    assert validate_scientific_metadata(metadata) == metadata


@pytest.mark.parametrize("params", [{"threshold": -1}, {"threshold": True}, {"unknown": 4}, {"threshold": float("nan")}])
def test_invalid_parameters_are_rejected(params):
    with pytest.raises(ValueError):
        _contract().validate_parameters(params)


@pytest.mark.parametrize("path,value", [
    (("surprise",), True),
    (("format",), "other/1"),
    (("method_id",), "../../escape"),
    (("implementation_trust",), "reviewed"),
    (("implementation", "source_sha256"), "missing"),
    (("implementation", "command"), "echo unsafe"),
    (("environment", "image"), "python:latest"),
    (("environment", "secrets"), {"token": "do-not-inherit"}),
    (("execution", "network"), "host"),
    (("execution", "cpu"), float("inf")),
    (("execution", "memory_mb"), True),
    (("execution", "memory_mb"), 256.0),
    (("execution", "mounts"), ["/etc"]),
    (("outputs", "dataset", "path"), "/outputs/../escape"),
    (("outputs", "dataset", "path"), "/tmp/escape"),
    (("outputs", "dataset", "path"), "/outputs/nested/result.h5ad"),
    (("outputs", "dataset", "path"), "/outputs//result.h5ad"),
    (("outputs", "dataset", "path"), "/outputs/result\u0000.h5ad"),
    (("validation", "invariants"), ["assert_everything_is_good"]),
    (("validation", "comparator"), "execute_python"),
    (("parameters", "additionalProperties"), True),
    (("parameters", "$ref"), "https://example.invalid/schema"),
    (("parameters", "properties", "threshold", "$ref"), "/etc/passwd"),
    (("parameters", "properties", "threshold", "$dynamicRef"), "https://example.invalid/dynamic"),
])
def test_unsafe_or_unknown_contract_fields_are_rejected(path, value):
    payload = _payload()
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        _contract(payload)


def test_contract_rejects_missing_fields_and_oversized_bindings():
    payload = _payload()
    del payload["provenance"]
    with pytest.raises(ValueError):
        _contract(payload)
    payload = _payload()
    payload["inputs"] = {f"dataset_{i}": {"artifact_type": "file", "required": True} for i in range(17)}
    with pytest.raises(ValueError):
        _contract(payload)


def test_local_json_schema_references_work_without_fetching():
    payload = _payload()
    payload["parameters"]["$defs"] = {"positive": {"type": "number", "exclusiveMinimum": 0}}
    payload["parameters"]["properties"]["threshold"] = {"$ref": "#/$defs/positive"}
    assert _contract(payload).validate_parameters({"threshold": 4}) == {"threshold": 4}


def test_contract_validation_does_not_read_source_or_environment():
    # The references need to be verified by the execution boundary, not while parsing JSON.
    assert _contract().to_dict()["implementation"]["source_sha256"] == "a" * 64


@pytest.mark.parametrize("field,maximum", [
    ("cpu", 16), ("memory_mb", 65536), ("timeout_seconds", 3600),
    ("pids", 1024), ("max_output_bytes", 100_000_000),
])
def test_local_execution_policy_caps_are_enforced(field, maximum):
    payload = _payload()
    payload["execution"][field] = maximum
    assert _contract(payload).to_dict()["execution"][field] == maximum
    payload["execution"][field] = maximum + 1
    with pytest.raises(ValueError):
        _contract(payload)


@pytest.mark.parametrize("section", ["inputs", "outputs"])
@pytest.mark.parametrize("invalid", ["wrong_type", "wrong_name", "optional"])
def test_single_cell_comparator_requires_named_anndata_dataset_bindings(section, invalid):
    payload = _payload()
    if invalid == "wrong_type":
        payload[section]["dataset"]["artifact_type"] = "json"
    elif invalid == "wrong_name":
        payload[section]["observations"] = payload[section].pop("dataset")
    else:
        payload[section]["dataset"]["required"] = False
    with pytest.raises(ValueError):
        _contract(payload)


def test_all_live_registered_tools_have_shared_contracts():
    from devharness_server.dispatcher import ToolDispatcher
    from devharness_server.method_adapter import dispatcher_contract, dispatcher_source
    for name in ToolDispatcher().tools:
        contract = dispatcher_contract(name)
        data = contract.to_dict()
        assert data["implementation"]["tool_name"] == name
        assert data["environment"]["runtime"] == "trusted_dispatcher"
        assert data["lifecycle"] == "registered"
        assert data["outputs"] == {"result": {"artifact_type": "json", "path": "/outputs/result.json", "required": True}}
        source = dispatcher_source(name)
        assert source.is_absolute() and source.is_file()
        assert data["implementation"]["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_dispatcher_contract_binds_paths_outside_parameter_schema():
    from devharness_server.method_adapter import dispatcher_contract
    data = dispatcher_contract("validate_design_matrix").to_dict()
    assert data["inputs"]["metadata_csv"]["parameter"] == "metadata_csv_path"
    assert "metadata_csv_path" not in data["parameters"]["properties"]
    assert "metadata_csv_path" not in data["parameters"]["required"]


def test_dispatcher_execution_writes_real_result_and_captures_event(tmp_path):
    from devharness_server.method_adapter import dispatcher_contract, execute_dispatcher
    contract = dispatcher_contract("calculate_temporal_monotonicity")
    result = execute_dispatcher(contract, {}, {"stage_values": {"e10": 1, "e11": 3}}, tmp_path)
    assert result["exit_code"] == 0 and result["runtime"] == "trusted_dispatcher"
    assert len(result["tool_events"]) == 1
    assert result["tool_events"][0]["succeeded"] is True
    written = json.loads((tmp_path / "result.json").read_text())
    assert written["direction"] == "increasing" and written["score"] == 1


def test_dispatcher_binds_declared_read_only_input(tmp_path):
    from devharness_server.method_adapter import dispatcher_contract, execute_dispatcher
    metadata = tmp_path / "metadata.csv"
    metadata.write_text("sample_id,embryonic_age\na,e10\nb,e11\nc,e12\n")
    output = tmp_path / "output"
    output.mkdir()
    original = metadata.read_bytes()
    result = execute_dispatcher(dispatcher_contract("validate_design_matrix"), {"metadata_csv": metadata},
                                {"formula_str": "1 + embryonic_age"}, output)
    assert result["tool_events"][0]["arguments"]["metadata_csv_path"] == str(metadata)
    assert metadata.read_bytes() == original
    assert (output / "result.json").is_file()


@pytest.mark.parametrize("change", ["tool", "source", "environment", "schema", "outputs"])
def test_dispatcher_rejects_forged_contracts_before_execution(tmp_path, change):
    from devharness_server.method_adapter import dispatcher_contract, execute_dispatcher
    data = dispatcher_contract("calculate_temporal_monotonicity").to_dict()
    if change == "tool":
        data["implementation"]["tool_name"] = "execute_arbitrary_code"
    elif change == "source":
        data["implementation"]["source_sha256"] = "d" * 64
    elif change == "environment":
        data["environment"]["identity_sha256"] = "d" * 64
    elif change == "schema":
        data["parameters"]["properties"]["stage_values"] = {"type": "object"}
    else:
        data["outputs"]["result"]["path"] = "/outputs/other.json"
    with pytest.raises(ValueError):
        execute_dispatcher(_contract(data), {}, {"stage_values": {"e10": 1, "e11": 2}}, tmp_path)
    assert not list(tmp_path.iterdir())


def test_dispatcher_rejects_bound_path_override(tmp_path):
    from devharness_server.method_adapter import dispatcher_contract, execute_dispatcher
    data = tmp_path / "metadata.csv"
    data.write_text("sample_id,embryonic_age\na,e10\nb,e11\n")
    with pytest.raises(ValueError):
        execute_dispatcher(dispatcher_contract("validate_design_matrix"), {"metadata_csv": data},
                           {"formula_str": "1", "metadata_csv_path": "/etc/passwd"}, tmp_path)
    assert not (tmp_path / "result.json").exists()


def test_dispatcher_rejects_relative_or_symlink_inputs(tmp_path):
    from devharness_server.method_adapter import dispatcher_contract, execute_dispatcher
    data = tmp_path / "metadata.csv"
    data.write_text("sample_id,embryonic_age\na,e10\nb,e11\n")
    link = tmp_path / "link.csv"
    link.symlink_to(data)
    for path in (Path("metadata.csv"), link):
        with pytest.raises(ValueError):
            execute_dispatcher(dispatcher_contract("validate_design_matrix"), {"metadata_csv": path},
                               {"formula_str": "1"}, tmp_path)


def test_dispatcher_does_not_overwrite_existing_output(tmp_path):
    from devharness_server.method_adapter import dispatcher_contract, execute_dispatcher
    output = tmp_path / "result.json"
    output.write_text("preserve")
    with pytest.raises(ValueError):
        execute_dispatcher(dispatcher_contract("calculate_temporal_monotonicity"), {},
                           {"stage_values": {"e10": 1, "e11": 2}}, tmp_path)
    assert output.read_text() == "preserve"


def test_dispatcher_unknown_tool_and_missing_input_are_rejected(tmp_path):
    from devharness_server.method_adapter import dispatcher_contract, execute_dispatcher
    with pytest.raises(ValueError):
        dispatcher_contract("execute_arbitrary_code")
    with pytest.raises(ValueError):
        execute_dispatcher(dispatcher_contract("validate_design_matrix"), {}, {"formula_str": "1"}, tmp_path)


def test_dispatcher_failure_keeps_the_actual_error_event_and_no_output(tmp_path):
    from devharness_server.method_adapter import dispatcher_contract, execute_dispatcher
    result = execute_dispatcher(dispatcher_contract("calculate_temporal_monotonicity"), {},
                                {"stage_values": {"e10": 1, "10": 2}}, tmp_path)
    assert result["exit_code"] == 1
    assert result["tool_events"][0]["succeeded"] is False
    assert "distinct numeric times" in result["tool_events"][0]["error"]
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize("filename", ["normalization.py", "selection_mc.py", "__init__.py", "native_nb.R"])
def test_dispatcher_identity_tracks_lazy_and_package_sources(tmp_path, monkeypatch, filename):
    import devharness_server.method_adapter as adapter
    contract = adapter.dispatcher_contract("calculate_temporal_monotonicity")
    original = adapter._stream_digest

    def changed_source(path, **kwargs):
        digest, size = original(path, **kwargs)
        return ("d" * 64, size) if Path(path).name == filename else (digest, size)

    monkeypatch.setattr(adapter, "_stream_digest", changed_source)
    updated = adapter.dispatcher_contract("calculate_temporal_monotonicity")
    assert updated.version_id != contract.version_id
    with pytest.raises(ValueError, match="identity"):
        adapter.execute_dispatcher(contract, {}, {"stage_values": {"e10": 1, "e11": 2}}, tmp_path)
    assert not list(tmp_path.iterdir())
