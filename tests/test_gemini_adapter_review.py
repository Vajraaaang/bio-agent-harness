"""External review regressions using bounded local inputs and fake executors only."""
from copy import deepcopy
import csv
import json
from pathlib import Path

import pytest

from devbench.analysis.community_worker import convert_inputs, validate_inputs
from tests.test_community_workflow import (
    FixtureRunner, IMAGE, REQUEST, SESSION, _authorize, _prepared, _run, _sha,
    inventory, plan,
)


def _inputs():
    matrix = {"gene_ids": ["g1"], "sample_ids": ["s1", "s2", "s3", "s4"],
              "matrix": [[1, 2, 3, 4]]}
    metadata = {"samples": [
        {"sample_id": f"s{i}", "donor": f"d{i}",
         "condition": "control" if i < 3 else "treated"}
        for i in range(1, 5)]}
    return matrix, metadata


@pytest.mark.parametrize("alias", ["s1", "unrelated annotation"])
def test_reserved_sample_alias_rejected_before_conversion_or_admission(tmp_path, alias):
    matrix, metadata = _inputs()
    metadata["samples"][0]["sample"] = alias
    with pytest.raises(ValueError, match="reserved.*sample|sample.*reserved"):
        validate_inputs(matrix, metadata, plan())
    matrix_path, metadata_path = tmp_path / "matrix.json", tmp_path / "metadata.json"
    matrix_path.write_text(json.dumps(matrix)); metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="reserved.*sample|sample.*reserved"):
        convert_inputs(matrix_path, metadata_path, plan(), tmp_path)
    assert not (tmp_path / "samples.csv").exists()
    assert not (tmp_path / "counts.tsv").exists()


def test_reserved_gene_header_cannot_duplicate_counts_header():
    matrix, metadata = _inputs()
    matrix["sample_ids"][0] = metadata["samples"][0]["sample_id"] = "gene_id"
    with pytest.raises(ValueError, match="reserved.*gene_id|gene_id.*reserved"):
        validate_inputs(matrix, metadata, plan())


def test_unreserved_annotations_are_losslessly_converted_with_unique_headers(tmp_path):
    matrix, metadata = _inputs()
    for row in metadata["samples"]:
        row["sample_label"] = 'annotation, with "quotes"'
    paths = [tmp_path / "matrix.json", tmp_path / "metadata.json"]
    for path, value in zip(paths, (matrix, metadata)):
        path.write_text(json.dumps(value))
    convert_inputs(*paths, plan(), tmp_path)
    with (tmp_path / "samples.csv").open(newline="") as stream:
        reader = csv.DictReader(stream)
        assert len(reader.fieldnames) == len(set(reader.fieldnames))
        rows = list(reader)
    assert [row["sample"] for row in rows] == matrix["sample_ids"]
    assert [row["sample_label"] for row in rows] == [r["sample_label"] for r in metadata["samples"]]


class NoRuntimeObservation(FixtureRunner):
    def inspect_run(self, binding):
        raise AssertionError("No runtime identity exists to inspect")

    def cancel_run(self, binding):
        raise AssertionError("No runtime identity exists to signal")


def test_queued_exception_reconciles_and_latches_cancel_without_invented_runtime(tmp_path, monkeypatch):
    prepared = _prepared(tmp_path, runner=NoRuntimeObservation())
    context = _authorize(prepared)
    from devbench.analysis import workflow_entrypoints
    def fail_before_execution(*args, **kwargs):
        raise RuntimeError("local authority transition interrupted before executor entry")
    monkeypatch.setattr(workflow_entrypoints, "workflow_action", fail_before_execution)
    with pytest.raises(RuntimeError, match="before executor entry"):
        _run(prepared)
    _, adapter, methods, runner, step, *_ = prepared
    assert adapter.status(REQUEST)["binding"] == {}
    observed = adapter.reconcile(REQUEST, actor="operator")
    assert observed["state"] == "unknown"
    event = observed["events"][-1]
    assert event["state"] == "runtime_identity_unavailable"
    assert event["dispatch_settled"] is False
    cancelled = adapter.cancel(REQUEST, actor="operator", allow_data_execution=True, **context)
    assert cancelled["state"] == "cancellation_requested"
    assert cancelled["cancellation_observation"]["diagnostic_status"] == "runtime_identity_unavailable"
    assert adapter.reconcile(REQUEST, actor="operator")["state"] == "cancellation_requested"
    with pytest.raises(ValueError, match="reconciliation|redispatch"):
        adapter.run(step["step_id"], request_id=REQUEST, actor="operator", allow_data_execution=True, **context)
    assert runner.calls == runner.cancel_calls == 0
    assert methods.get("community_request", REQUEST)["binding"] == {}


@pytest.mark.parametrize("missing", ["container_name", "run_id", "image", "nextflow_session_id", "inventory_sha256", "output_dir", "max_output_bytes"])
def test_incomplete_owned_binding_retains_pending_observation_without_executor_call(tmp_path, missing):
    prepared = _prepared(tmp_path, runner=NoRuntimeObservation(mode="coordinator_loss"))
    with pytest.raises(KeyboardInterrupt):
        _run(prepared)
    _, adapter, methods, runner, *_ = prepared
    request = methods.get("community_request", REQUEST)
    request["binding"].pop(missing)
    with methods._db() as db:
        methods._put(db, "community_request", REQUEST, request, update=True)
    result = adapter.cancel(REQUEST, actor="operator", allow_data_execution=True, **prepared[7])
    assert result["state"] == "cancellation_requested"
    assert missing in result["cancellation_observation"]["missing_binding_fields"]
    assert adapter.reconcile(REQUEST, actor="operator")["state"] == "cancellation_requested"
    assert runner.calls == 1 and runner.cancel_calls == 0


def test_cancel_before_create_intent_remains_pending_and_blocks_later_dispatch(tmp_path):
    class DeferredIntent(NoRuntimeObservation):
        def set_observer(self, observer):
            super().set_observer(observer)
            pending = prepared[1].cancel(REQUEST, actor="operator", allow_data_execution=True, **prepared[7])
            assert pending["state"] == "cancellation_requested"
            assert not pending["binding"].get("container_name")

        def execute(self, contract, inputs, parameters, source, lock, output_dir):
            self.calls += 1
            run_id = Path(output_dir).parent.name
            allowed = self.observer({"event": "container_create_intent", "run_id": run_id,
                "container_name": "devharness-method-" + run_id, "image": IMAGE,
                "inventory_sha256": _sha(inventory()), "nextflow_session_id": SESSION})
            assert allowed is False
            return {"exit_code": 143, "worker_exit_code": 143, "engine_started": False,
                    "engine_exit_code": None, "cancelled": True, "stdout": "", "stderr": ""}
    prepared = _prepared(tmp_path, runner=DeferredIntent())
    run = _run(prepared)
    assert run["status"] == "failed" and run["outputs"] == {}
    result = prepared[1].status(REQUEST)
    assert result["state"] == "cancelled"
    assert result["engine_started"] is False and result["engine_exit_code"] is None
    assert prepared[3].calls == 1 and prepared[3].cancel_calls == 0
