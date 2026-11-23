"""Bounded descriptive evidence, routing, and durable native lifecycle."""

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from devbench.analysis.workbench_inputs import InputDiscovery
from devbench.analysis.workbench_investigations import (
    InvestigationService,
    classify_question,
)


@pytest.fixture
def supervised(monkeypatch):
    from devharness_core import watchdog

    calls = []

    def run(target, args=(), **kwargs):
        calls.append(target.__name__)
        try:
            return target(*args)
        finally:
            kwargs["diagnostics"]["cleanup"] = {"verified_stopped": True}

    monkeypatch.setattr(watchdog, "run_sandboxed", run)
    return calls


def fixture_h5ad(path, *, rows=4, annotations=True):
    import h5py
    import numpy as np

    with h5py.File(path, "w") as handle:
        handle.create_dataset("X", shape=(rows, 3), dtype="f4")
        for name, count in [("obs", rows), ("var", 3)]:
            group = handle.create_group(name)
            group.attrs["_index"] = "_index"
            group.create_dataset("_index", shape=(count,), dtype="S12")
        if annotations:
            for name, codes, labels in [
                ("cell_type", [0, 0, 1, -1], ["T", "B"]),
                ("annotation", [0, 1, 1, -1], ["A", "B"]),
                ("batch", [0, 0, 1, -1], ["one", "two"]),
            ]:
                group = handle["obs"].create_group(name)
                group.attrs["encoding-type"] = "categorical"
                group.create_dataset("codes", data=np.array(codes, dtype="i4"))
                group.create_dataset(
                    "categories", data=np.array(labels, dtype=h5py.string_dtype())
                )
    return path


class LocalApp:
    def __init__(self, root):
        self.workspace = root / "state"
        self.workspace.mkdir()
        self.input_roots = (root / "data",)
        self.input_roots[0].mkdir()
        self.parameters = {"batch_key": "batch", "n_pcs": 8}
        self.scientific = None
        self.allow_data_execution = self.allow_model_execution = False
        self.model_data_scope = "local_only"
        self.selector_config = None
        self._executor = ThreadPoolExecutor(max_workers=1)
        self.calls = []

    def _path(self, *parts):
        return self.workspace.joinpath(*parts)

    def workspace_state(self):
        return {"project": {"id": "a" * 32}}

    def register(self, *args, **kwargs):
        self.calls.append("register")
        raise AssertionError("Descriptive intake must not start scientific execution")


@pytest.fixture
def service(tmp_path, supervised):
    app = LocalApp(tmp_path)
    source = fixture_h5ad(app.input_roots[0] / "fixture.h5ad")
    inputs = InputDiscovery(app.input_roots)
    yield app, source, inputs, InvestigationService(app, inputs)
    app._executor.shutdown(wait=True)


def answer(record):
    return next(
        t["content"] for t in reversed(record["turns"]) if t["kind"] == "dataset_answer"
    )


@pytest.mark.parametrize(
    "question",
    [
        "How many distinct genes are expressed?",
        "How many genes are expressed per sample within batch A?",
        "How many expressed genes per sample and run UMAP?",
        "Show PCA by batch and identify differentially expressed genes.",
        "Check for batch effects and test whether treatment changed expression.",
        "Show me a PCA plot while finding differentially expressed genes.",
    ],
)
def test_profile_audit_does_not_substitute_for_other_matrix_scopes(question):
    from devbench.analysis.workbench_request_contract import (
        supports_matrix_profile_audit,
    )

    assert supports_matrix_profile_audit(question) is False


@pytest.mark.parametrize(
    "question",
    [
        "Please check for batch effects in this dataset.",
        "Could you show PCA colored by batch?",
        "Can you check whether there is a batch effect?",
        "Are there any signs of batch effects in my dataset?",
        "Show a batch-colored PCA plot.",
        "Report nonzero gene counts per sample.",
        "Run PCA",
        "Show me a PCA plot",
        "Run a PCA to check for batch effects.",
        "Do you see any batch effects? "
        "Show the number of non-zero genes in each sample.",
    ],
)
def test_full_descriptive_clauses_are_recognized_as_matrix_requests(question):
    from devbench.analysis.workbench_request_contract import (
        classify_request,
        supports_matrix_profile_audit,
    )

    request = classify_request(question)
    assert supports_matrix_profile_audit(question)
    assert request["matrix_computation_required"] is True
    assert request["remaining_action"] == "exploratory_computation"


@pytest.mark.wal
@pytest.mark.parametrize(
    "input_format, measurement, question",
    [
        (
            "h5ad",
            "non_umi_counts",
            "How many genes are expressed per sample and is there a batch effect?",
        ),
        (
            "csv",
            "non_umi_counts",
            "How many genes are expressed per sample and is there a batch effect?",
        ),
        ("h5ad", "raw_counts", "Please check for batch effects in this dataset."),
        ("csv", "integer_counts", "Could you show PCA colored by batch?"),
        ("h5ad", "COUNT-1", "Run a PCA to check for batch effects."),
        ("csv", None, "Could you show PCA colored by batch?"),
        ("h5ad", "normalized_expression", "Run PCA"),
        ("h5ad", "raw_counts", "Show PCA by batch and run differential expression."),
    ],
)
def test_governed_descriptive_matrix_audit_runs_and_records_exposure(
    workspace_tmp_path,
    input_format,
    measurement,
    question,
    monkeypatch,
):
    import csv

    import numpy as np
    import pandas as pd

    from devbench.analysis.evidence_access import EvidenceBroker
    from devbench.platform.workbench_ui import WorkbenchService
    from devharness_server.dispatcher import ToolDispatcher

    root = workspace_tmp_path / "inputs"
    root.mkdir()
    samples = [f"s{index}" for index in range(8)]
    genes = ["g0", "g1", "g2", "g3"]
    values = np.array(
        [[3 + index % 2, 9 + index, index % 3, 2 + index % 3] for index in range(8)]
    )
    observation = pd.DataFrame(
        {
            "condition": ["A"] * 4 + ["B"] * 4,
            "batch": ["first", "second"] * 4,
        },
        index=samples,
    )
    declaration = {"measurement": measurement} if measurement is not None else {}
    if input_format == "h5ad":
        from anndata import AnnData

        source = root / "expression.h5ad"
        AnnData(values, obs=observation, var=pd.DataFrame(index=genes)).write_h5ad(
            source
        )
        request = {"source": str(source), "declarations": declaration}
    else:
        source = root / "expression.csv"
        metadata = root / "metadata.csv"
        with source.open("w", newline="") as handle:
            csv.writer(handle).writerows(
                [
                    ["gene_id", *samples],
                    *[[gene, *row] for gene, row in zip(genes, values.T, strict=True)],
                ]
            )
        observation.to_csv(metadata, index_label="sample_id")
        request = {
            "sources": [str(source), str(metadata)],
            "source_declarations": [
                {"role": "expression", **declaration},
                {"role": "metadata"},
            ],
        }
    app = WorkbenchService(
        workspace_tmp_path / "service",
        [root],
        enable_scientific=True,
        allow_data_execution=True,
    )
    worker_sources = []
    execute = ToolDispatcher.execute
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    def observe(dispatcher, name, kwargs):
        staged = Path(kwargs["counts_path"])
        assert staged.is_relative_to(app.workspace) and staged != source
        assert hashlib.sha256(staged.read_bytes()).hexdigest() == original_hash
        worker_sources.append(staged)
        return execute(dispatcher, name, kwargs)

    monkeypatch.setattr(ToolDispatcher, "execute", observe)
    try:
        record = app.investigations.create({**request, "question": question})
        if "differential expression" in question:
            assert record["request"]["remaining_action"] == "inferential_analysis"
            assert record.get("required_action") != "bounded_matrix_computation"
            assert "descriptive_matrix_audit" not in record
            assert not worker_sources and not record.get("scientific_session_id")
            return
        if question == "Run PCA":
            assert record["required_action"] == "matrix_intake"
            field = record["clarification_fields"][0]
            assert field["field"] == "batch_col"
            assert {item["value"] for item in field["options"]} == {"batch"}
            assert not worker_sources
            record = app.investigations.action(
                record["id"],
                {
                    "action": "clarify",
                    "expected_revision": record["revision"],
                    "scientific_context": {"batch_col": "batch"},
                },
            )
        if measurement is None:
            assert record["required_action"] == "matrix_intake"
            assert record["clarification_fields"][0]["field"] == "measurement"
            assert record["clarification_fields"][0]["source_index"] == 0
            clarification = record["turns"][-1]["content"]
            assert (
                "measurement" in clarification["fact_provenance"]["Unknown/Conflicting"]
            )
            assert clarification["warnings"] and not worker_sources
            assert "descriptive_matrix_audit" not in record
            measurement = "non_umi_counts"
            record = app.investigations.action(
                record["id"],
                {
                    "action": "clarify",
                    "expected_revision": record["revision"],
                    "source_declarations": [{"measurement": measurement}, {}],
                },
            )
        assert record["status"] == "completed", record.get("error")
        audit = record["descriptive_matrix_audit"]
        assert audit["measurement"] == measurement
        assert audit["nonzero_per_sample"] == [
            {"sample_id": sample, "nonzero_genes": int(np.count_nonzero(row))}
            for sample, row in zip(samples, values, strict=True)
        ]
        assert audit["batch"]["adjusted_for"] == "condition"
        transform = (
            "none_continuous_input"
            if measurement == "normalized_expression"
            else "log1p_RLE_normalized_counts"
        )
        assert audit["batch"]["transformation"] == transform
        assert any(
            row["batch_partial_r2"] is not None for row in audit["batch"]["genes"]
        )
        assert audit["batch"]["pca"]["sample_ids"] == samples
        result = answer(record)
        assert result["binding"]["population"]["scope"] == "full_source_matrix"
        assert result["binding"]["result_ref"]
        tables = {table["caption"]: table for table in result["tables"]}
        assert tables["Finite nonzero genes per observed profile"]["rows"][0] == [
            "s0",
            3,
        ]
        assert tables["Per-gene batch diagnostics"]["rows"][0][0] == "g0"
        assert tables["PCA scores by batch"]["rows"][0] == [
            "s0",
            "first",
            *audit["batch"]["pca"]["coordinates"][0],
        ]
        assert (
            result["plot"]["variance_ratio"] == audit["batch"]["pca"]["variance_ratio"]
        )
        assert not record.get("synthesis")
        ledger = app.scientific.open_workflow_ledger()
        broker = EvidenceBroker.open_existing(ledger.scientific)
        exposed = broker.reservation(audit["disclosure_reservation_id"])
        assert exposed["state"] == "delivery_attempted"
        assert exposed["source_id"] == audit["evidence_source_id"]
        assert len(audit["exposure_basis_sha256"]) == 64
        assert app.investigations.get(record["id"])["descriptive_matrix_audit"] == audit
        assert len(worker_sources) == 2 and worker_sources[0] == worker_sources[1]
        assert not worker_sources[0].exists()
    finally:
        app.close()


def wait_record(service, record, statuses):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = service.get(record["id"])
        if current["status"] in statuses:
            return current
        time.sleep(0.01)
    raise AssertionError(f"No terminal state: {current['status']}")


def test_how_many_batches_is_descriptive_not_batch_correction():
    assert (
        classify_question("How many batches are stored?")["kind"]
        == "dataset_description"
    )


def test_explicit_estimation_contrast_is_inferential(service):
    _, source, _, investigations = service
    record = investigations.create(
        {"source": str(source), "question": "Estimate B minus A"}
    )
    assert record["request"]["category"] == "inferential_analysis"
    assert (
        record["required_action"] != "clarify_intent"
        if "required_action" in record
        else True
    )


@pytest.mark.parametrize(
    "question,category,remaining",
    [
        (
            "How many genes are expressed?",
            "exploratory_computation",
            "exploratory_computation",
        ),
        ("Find marker genes", "inferential_analysis", "inferential_analysis"),
        ("Show stored marker results", "descriptive_retrieval", None),
        (
            "How many cells and compare expression between treated and control?",
            "ambiguous_or_mixed",
            "inferential_analysis",
        ),
        ("Hello, can you help?", "ambiguous_or_mixed", "clarify_intent"),
    ],
)
def test_four_request_classes_do_not_grant_computation(
    service, question, category, remaining
):
    app, source, _, investigations = service
    record = investigations.create({"source": str(source), "question": question})
    assert record["request"]["category"] == category
    assert record["request"]["remaining_action"] == remaining
    assert not app.calls
    if remaining:
        assert record["status"] == "awaiting_information"


def test_exact_annotation_counts_missing_denominator_and_ambiguous_alternatives(
    service,
):
    app, source, _, investigations = service
    record = investigations.create(
        {"source": str(source), "question": "Give details about cell types"}
    )
    content = answer(record)
    tables = {t["column_name"]: t for t in content["tables"] if t.get("column_name")}
    assert set(tables) == {"cell_type", "annotation"}
    assert tables["cell_type"]["rows"] == [["T", 2, 50.0], ["B", 1, 25.0]]
    assert all(
        t["denominator"] == 4 and t["missing_count"] == 1 for t in tables.values()
    )
    assert any("alternative" in text.lower() for text in content["limitations"])
    assert "lineage" not in content["message"].lower()
    assert record["declarations"] == {} and record["scientific_context"] == {}
    assert (
        content["binding"]["source_sha256"]
        == hashlib.sha256(source.read_bytes()).hexdigest()
    )
    assert content["binding"]["project_id"] == "a" * 32
    assert content["binding"]["question_id"] == record["question_id"]
    assert record["status"] == "completed" and not app.calls


def test_dimensions_are_stored_features_not_unique_or_expressed_genes(service):
    _, source, _, investigations = service
    record = investigations.create(
        {"source": str(source), "question": "How many genes?"}
    )
    content = answer(record)
    assert "3 features" in content["message"] and "4 observations" in content["message"]
    assert "expressed" in " ".join(content["limitations"]).lower()
    assert not any("counts (X)" in str(t) for t in record["turns"])


def test_mixed_request_keeps_remaining_analysis_state_after_descriptive_answer(service):
    _, source, _, investigations = service
    record = investigations.create(
        {
            "source": str(source),
            "question": "How many cells and compare expression "
            "between treated and control?",
        }
    )
    assert record["status"] == "awaiting_information"
    assert answer(record)["completion"] == "partial"
    assert record["clarification_fields"]
    assert not any(t["kind"] == "conclusion" for t in record["turns"])
    assert record["description_operation"]["status"] == "completed"


def test_small_table_rows_are_not_cells_or_expression_orientation(service):
    _, source, _, investigations = service
    source = source.with_suffix(".csv")
    source.write_text("sample,batch\ns1,a\ns2,b\n")
    record = investigations.create(
        {"source": str(source), "question": "Describe the dataset"}
    )
    content = answer(record)
    facts = {f["name"]: f["value"] for f in content["facts"]}
    assert facts["metadata_rows"] == 2
    assert facts["observations"] is None and facts["features"] is None
    assert "2 cells" not in content["message"]


def test_source_set_digest_is_not_labeled_as_first_file_sha(service):
    _, source, _, investigations = service
    other = fixture_h5ad(source.parent / "other.h5ad", rows=3, annotations=False)
    record = investigations.create(
        {"sources": [str(source), str(other)], "question": "What columns are stored?"}
    )
    content = answer(record)
    assert content["binding"]["input_kind"] == "source_set"
    assert content["binding"]["source_sha256"] is None
    assert content["binding"]["input_set_sha256"] == record["input_set_sha256"]
    assert all(
        table["source"] in {str(source), str(other)} for table in content["tables"]
    )
    assert content["filename"] == "Attached source set"
    assert content["observed"]["cells"] is None


def test_annotation_bounds_do_not_fabricate_empty_population(service):
    _, source, inputs, _ = service
    profile = inputs.inspect(source)
    result = inputs.describe(
        source,
        expected_sha256=profile["sha256"],
        topics=["annotations"],
        limits={"max_rows": 2},
    )
    assert result["completion"] == "unavailable"
    assert result["tables"] == []
    assert any(
        "row" in text.lower() and "bound" in text.lower()
        for text in result["limitations"]
    )


def test_changed_source_rejected_before_description(service):
    _, source, inputs, _ = service
    profile = inputs.inspect(source)
    with source.open("ab") as handle:
        handle.write(b"changed")
    with pytest.raises((ValueError, RuntimeError), match="changed|identity|checksum"):
        inputs.describe(
            source, expected_sha256=profile["sha256"], topics=["dimensions"]
        )


def test_worker_error_requires_verified_cleanup(service, monkeypatch):
    _, source, inputs, _ = service
    profile = inputs.inspect(source)
    from devharness_core import watchdog

    def broken(*args, **kwargs):
        kwargs["diagnostics"]["cleanup"] = {"verified_stopped": False}
        raise ValueError("malformed metadata")

    monkeypatch.setattr(watchdog, "run_sandboxed", broken)
    with pytest.raises(RuntimeError, match="cleanup"):
        inputs.describe(
            source, expected_sha256=profile["sha256"], topics=["annotations"]
        )


def test_async_description_exposes_actual_pending_without_holding_investigation_lock(
    service, monkeypatch
):
    _, source, inputs, investigations = service
    entered, release = threading.Event(), threading.Event()
    inspect = inputs.inspect

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return inspect(*args, **kwargs)

    monkeypatch.setattr(inputs, "inspect", slow)
    record = investigations.create(
        {"source": str(source), "question": "How many genes?"}, defer_description=True
    )
    assert record["status"] == "queued"
    assert entered.wait(1)
    try:
        pending = investigations.get(record["id"])
        assert pending["status"] == "inspecting"
        assert pending["description_operation"]["status"] == "running"
        assert pending["operation_pending"] is True
    finally:
        release.set()
    complete = wait_record(investigations, record, {"completed", "operation_failed"})
    assert complete["status"] == "completed"
    turns = complete["turns"]
    assert [t["sequence"] for t in turns] == list(range(1, len(turns) + 1))
    assert len({t["id"] for t in turns}) == len(turns)


def test_followup_refuses_changed_source_declarations_and_does_not_renew_grants(
    service,
):
    _, source, _, investigations = service
    record = investigations.create(
        {
            "source": str(source),
            "question": "How many genes?",
            "scientific_context": {
                "data_classification": "synthetic",
                "allow_model_execution": True,
            },
        }
    )
    with source.open("ab") as handle:
        handle.write(b"changed")
    child = investigations.action(
        record["id"],
        {
            "action": "follow_up",
            "expected_revision": record["revision"],
            "question": "How many cells?",
        },
    )
    assert child["status"] == "operation_failed"
    assert child["declarations"] == {}
    assert "allow_model_execution" not in child["scientific_context"]
    assert not any(t["kind"] == "dataset_answer" for t in child["turns"])


@pytest.mark.parametrize(
    "question",
    [
        "What cell types are stored?",
        "How many cell types?",
        "What batches are present?",
    ],
)
def test_stored_annotation_questions_are_retrieval(service, question):
    _, source, _, investigations = service
    record = investigations.create({"source": str(source), "question": question})
    assert record["request"]["category"] == "descriptive_retrieval"
    assert record["status"] == "completed"
    assert any(t.get("column_name") for t in answer(record)["tables"])


def test_partial_annotation_limit_does_not_claim_complete(service):
    _, source, inputs, _ = service
    profile = inputs.inspect(source)
    content = inputs.describe(
        source,
        expected_sha256=profile["sha256"],
        topics=["annotations"],
        limits={"max_annotation_columns": 1},
    )
    assert content["completion"] == "partial"
    assert len(content["tables"]) == 1
    assert any("uninspected" in item for item in content["limitations"])


def test_nonfinite_json_cannot_be_counted_as_valid_metadata(service):
    _, source, inputs, _ = service
    source = source.with_suffix(".json")
    source.write_text('[{"value":1e999}]')
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises((ValueError, RuntimeError), match="finite"):
        inputs.describe(source, expected_sha256=expected, topics=["dimensions"])


def test_async_worker_failure_remains_error_without_answer(service, monkeypatch):
    _, source, inputs, investigations = service

    def failed(*args, **kwargs):
        raise RuntimeError("synthetic parser failure")

    monkeypatch.setattr(inputs, "describe", failed)
    record = investigations.create(
        {"source": str(source), "question": "How many genes?"}, defer_description=True
    )
    current = wait_record(investigations, record, {"operation_failed"})
    assert current["description_operation"]["status"] == "failed"
    assert current["operation_pending"] is False
    assert "synthetic parser failure" in current["error"]["message"]
    assert not any(
        t["kind"] in {"dataset_answer", "conclusion"} for t in current["turns"]
    )


def test_async_cancellation_keeps_request_and_no_answer(service, monkeypatch):
    _, source, inputs, investigations = service
    entered, release = threading.Event(), threading.Event()

    def slow(*args, **kwargs):
        from devharness_core.watchdog import _raise_if_cancelled

        entered.set()
        assert release.wait(3)
        _raise_if_cancelled()

    monkeypatch.setattr(inputs, "describe", slow)
    record = investigations.create(
        {"source": str(source), "question": "How many genes?"}, defer_description=True
    )
    assert entered.wait(1)
    current = investigations.get(record["id"])
    stopped = investigations.action(
        record["id"], {"action": "cancel", "expected_revision": current["revision"]}
    )
    assert stopped["status"] == "cancelling"
    release.set()
    current = wait_record(investigations, record, {"cancelled", "operation_failed"})
    assert current["status"] == "cancelled"
    assert current["description_operation"]["worker_cleanup_verified"] is True
    states = [
        t["content"]["state"] for t in current["turns"] if t["kind"] == "lifecycle"
    ]
    assert states == ["queued", "running", "cancel_requested", "cancelled"]
    assert not any(t["kind"] == "dataset_answer" for t in current["turns"])


def test_project_identity_cannot_be_swapped(service):
    _, source, _, investigations = service
    with pytest.raises(ValueError, match="project"):
        investigations.create(
            {"source": str(source), "question": "How many genes?"}, project_id="b" * 32
        )


def test_unchanged_followup_keeps_facts_with_new_question_and_no_grants(service):
    _, source, _, investigations = service
    record = investigations.create(
        {
            "source": str(source),
            "question": "How many genes?",
            "declarations": {"assay": "scrna", "measurement": "umi_counts"},
            "scientific_context": {
                "data_classification": "synthetic",
                "allow_model_execution": True,
            },
        }
    )
    child = investigations.action(
        record["id"],
        {
            "action": "follow_up",
            "expected_revision": record["revision"],
            "question": "How many cells?",
        },
    )
    assert child["declarations"] == record["declarations"]
    assert child["question_id"] != record["question_id"]
    assert child["project_id"] == record["project_id"]
    assert child["profile"]["sha256"] == record["profile"]["sha256"]
    assert "allow_model_execution" not in child["scientific_context"]
    assert answer(child)["binding"]["population"]["scope"] == "full_source_metadata"
    assert child["thread_turns"][: len(record["turns"])] == record["turns"]


def test_restart_never_requeues_interrupted_description(service):
    _, source, _, investigations = service
    record = investigations.create(
        {"source": str(source), "question": "How many genes?"}
    )
    retained = investigations._load(record["id"])
    retained.update(status="inspecting", operation_pending=True)
    retained["description_operation"]["status"] = "running"
    investigations._save(retained)
    current = investigations.get(record["id"])
    assert (
        current["status"] == "recovery_required"
        and current["operation_pending"] is False
    )
    assert current["description_operation"]["status"] == "recovery_required"


def test_changed_during_annotation_read_fails_without_answer(service, monkeypatch):
    from devbench.analysis import workbench_descriptions as descriptions

    _, source, inputs, investigations = service
    real = descriptions._h5_annotations

    def changed(*args, **kwargs):
        result = real(*args, **kwargs)
        with source.open("ab") as handle:
            handle.write(b"changed during read")
        return result

    monkeypatch.setattr(descriptions, "_h5_annotations", changed)
    record = investigations.create(
        {"source": str(source), "question": "What cell types are stored?"}
    )
    assert record["status"] == "operation_failed"
    assert "changed" in record["error"]["message"]
    assert not any(t["kind"] == "dataset_answer" for t in record["turns"])


@pytest.mark.wal
def test_matrix_batch_audit_requires_metadata_with_a_batch_column(workspace_tmp_path):
    import numpy as np
    import pandas as pd
    from anndata import AnnData

    from devbench.platform.workbench_ui import WorkbenchService

    root = workspace_tmp_path / "inputs"
    root.mkdir()
    source = root / "expression.h5ad"
    AnnData(
        np.ones((4, 2)),
        obs=pd.DataFrame({"condition": ["A", "A", "B", "B"]}, index=list("abcd")),
    ).write_h5ad(source)
    app = WorkbenchService(
        workspace_tmp_path / "service",
        [root],
        enable_scientific=True,
        allow_data_execution=True,
    )
    try:
        record = app.investigations.create(
            {
                "source": str(source),
                "question": "Show PCA by batch",
                "declarations": {"measurement": "normalized_expression"},
            }
        )
        assert record["status"] == "operation_failed"
        assert "aligned metadata with a batch column" in str(
            record["turns"][-1]["content"]
        )
        assert "descriptive_matrix_audit" not in record
    finally:
        app.close()


@pytest.mark.parametrize("limits", [{"max_categories": 1}, {"max_bytes": 1}])
def test_category_and_byte_bounds_publish_no_fake_counts(service, limits):
    _, source, inputs, _ = service
    profile = inputs.inspect(source)
    result = inputs.describe(
        source, expected_sha256=profile["sha256"], topics=["annotations"], limits=limits
    )
    assert result["completion"] == "unavailable" and result["tables"] == []


def test_output_bound_prevents_oversized_answer(service):
    _, source, inputs, _ = service
    profile = inputs.inspect(source)
    with pytest.raises(ValueError, match="output byte"):
        inputs.describe(
            source,
            expected_sha256=profile["sha256"],
            topics=["annotations"],
            limits={"max_output_bytes": 100},
        )


def test_unknown_intent_does_not_fit_even_with_complete_scientific_declarations(
    service,
):
    app, source, _, investigations = service
    record = investigations.create(
        {
            "source": str(source),
            "question": "Do something useful",
            "declarations": {
                "assay": "scrna",
                "measurement": "umi_counts",
                "count_provenance": "Synthetic fixture",
                "capture_unit_key": "batch",
                "capture_units_confirmed": True,
                "annotation_provenance": "Synthetic labels",
            },
        }
    )
    assert record["status"] == "awaiting_information" and not app.calls
    assert record["required_action"] == "clarify_intent"
    assert not any(t["kind"] == "dataset_answer" for t in record["turns"])


@pytest.mark.parametrize(
    "question",
    [
        "Show stored clusters and compute new clusters",
        "Describe subpopulations and run clustering",
        "Show stored markers and perform QC",
        "How many genes and normalize expression?",
        "How many cells and do PCA?",
    ],
)
def test_mixed_exploratory_action_is_not_lost_behind_retrieval(service, question):
    _, source, _, investigations = service
    record = investigations.create({"source": str(source), "question": question})
    assert record["request"]["category"] == "ambiguous_or_mixed"
    assert record["request"]["remaining_action"] == "exploratory_computation"
    assert record["status"] == "awaiting_information"
    assert answer(record)["completion"] in {"partial", "unavailable"}


def test_unknown_remaining_clause_is_not_completed_by_metadata(service):
    _, source, _, investigations = service
    record = investigations.create(
        {
            "source": str(source),
            "question": "How many cells and do something interesting?",
        }
    )
    assert record["request"]["remaining_action"] == "clarify_intent"
    assert record["status"] == "awaiting_information"
    assert answer(record)["completion"] == "partial"


@pytest.mark.parametrize(
    "question",
    [
        "How many genes and cells are stored?",
        "How many genes and cells are stored in this dataset?",
        "How many rows and columns are in this dataset?",
        "What columns and layers are present?",
        "How many features and observations are available?",
        "How many genes and cells?",
    ],
)
def test_compound_stored_metadata_continuations_complete_without_clarification(
    service, question
):
    app, source, _, investigations = service
    record = investigations.create({"source": str(source), "question": question})
    assert record["request"]["category"] == "descriptive_retrieval"
    assert record["request"]["remaining_action"] is None
    assert record["status"] == "completed"
    assert record["clarification_fields"] == []
    assert answer(record)["completion"] == "completed"
    assert (
        answer(record)["binding"]["source_sha256"]
        == hashlib.sha256(source.read_bytes()).hexdigest()
    )
    assert not app.calls


@pytest.mark.parametrize(
    "question,remaining",
    [
        ("How many genes and cells are selected?", "clarify_intent"),
        ("How many genes and cells are in batch A?", "clarify_intent"),
        ("How many genes and normalize expression?", "exploratory_computation"),
        ("How many genes and cells are biologically independent?", "clarify_intent"),
        ("How many genes and find markers?", "inferential_analysis"),
        ("How many genes and do something interesting?", "clarify_intent"),
    ],
)
def test_compound_extra_actions_and_population_claims_remain_pending(
    service, question, remaining
):
    app, source, _, investigations = service
    record = investigations.create({"source": str(source), "question": question})
    assert record["request"]["remaining_action"] == remaining
    assert record["status"] == "awaiting_information"
    assert answer(record)["completion"] == "partial"
    assert not app.calls


def test_cells_per_batch_reads_batch_frequencies(service):
    _, source, _, investigations = service
    record = investigations.create(
        {"source": str(source), "question": "How many cells per batch?"}
    )
    assert "batches" in record["request"]["descriptive_topics"]
    assert any(
        table.get("column_name") == "batch" for table in answer(record)["tables"]
    )


@pytest.mark.parametrize(
    "question",
    [
        "How many genes are unique?",
        "How many cells are in batch A?",
        "How many cells are selected?",
    ],
)
def test_unanswered_scope_qualifier_retains_partial_answer(service, question):
    _, source, _, investigations = service
    record = investigations.create({"source": str(source), "question": question})
    assert record["status"] == "awaiting_information"
    assert record["request"]["remaining_action"] == "clarify_intent"
    assert answer(record)["completion"] == "partial"


def test_revised_question_has_new_identity_and_preserves_old_turn_binding(service):
    _, source, _, investigations = service
    record = investigations.create({"source": str(source), "question": "Please help"})
    old_binding = record["turns"][0]["binding"]
    changed = investigations.action(
        record["id"],
        {
            "action": "clarify",
            "expected_revision": record["revision"],
            "question": "How many genes?",
        },
    )
    assert changed["question_id"] != record["question_id"]
    assert changed["turns"][0]["binding"] == old_binding
    assert answer(changed)["binding"]["question_id"] == changed["question_id"]
    assert changed["description_operation"]["status"] == "completed"


def test_mixed_qc_keeps_native_queue_pending_after_description(service, monkeypatch):
    app, source, _, investigations = service
    assert (
        classify_question("How many genes and run QC?")["kind"] == "qc_and_integration"
    )

    def register(request, *, investigation_id):
        return {"id": "d" * 32, "status": "queued"}

    monkeypatch.setattr(app, "register", register)
    monkeypatch.setattr(
        app,
        "status",
        lambda identity: {"id": identity, "source": str(source), "status": "queued"},
        raising=False,
    )
    record = investigations.create(
        {
            "source": str(source),
            "question": "How many genes and run QC?",
            "declarations": {
                "assay": "scrna",
                "measurement": "umi_counts",
                "count_provenance": "Synthetic fixture",
                "capture_unit_key": "batch",
                "capture_units_confirmed": True,
                "annotation_provenance": "Synthetic labels",
            },
        }
    )
    assert record["status"] == "queued" and record["job_id"] == "d" * 32
    assert record["operation_pending"] is True
    assert record["description_operation"]["status"] == "completed"
    assert answer(record)["completion"] == "partial"


def test_clarified_description_is_async_cancelable_and_has_fresh_operation(
    service, monkeypatch
):
    _, source, inputs, investigations = service
    initial = investigations.create(
        {"source": str(source), "question": "Help with this data"}
    )
    entered, release = threading.Event(), threading.Event()
    real = inputs.describe

    def slow(*args, **kwargs):
        from devharness_core.watchdog import _raise_if_cancelled

        entered.set()
        assert release.wait(3)
        _raise_if_cancelled()
        return real(*args, **kwargs)

    monkeypatch.setattr(inputs, "describe", slow)
    record = investigations.action(
        initial["id"],
        {
            "action": "clarify",
            "expected_revision": initial["revision"],
            "question": "How many genes?",
        },
        defer_description=True,
    )
    assert record["status"] == "queued"
    assert entered.wait(1)
    current = investigations.get(record["id"])
    investigations.action(
        record["id"], {"action": "cancel", "expected_revision": current["revision"]}
    )
    release.set()
    stopped = wait_record(investigations, record, {"cancelled", "operation_failed"})
    assert stopped["status"] == "cancelled"
    assert stopped["description_operation"]["status"] == "cancelled"


def test_retrying_failed_description_gets_new_completed_attempt(service, monkeypatch):
    _, source, inputs, investigations = service
    real = inputs.describe
    monkeypatch.setattr(
        inputs,
        "describe",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("synthetic read failure")
        ),
    )
    record = investigations.create(
        {"source": str(source), "question": "How many genes?"}
    )
    assert record["description_operation"]["status"] == "failed"
    monkeypatch.setattr(inputs, "describe", real)
    retry = investigations.action(
        record["id"], {"action": "clarify", "expected_revision": record["revision"]}
    )
    assert retry["status"] == "completed"
    assert retry["description_operation"]["status"] == "completed"
    assert (
        retry["description_operation"]["operation_id"]
        != record["description_operation"]["operation_id"]
    )


def test_partial_source_set_retry_retains_known_identity_without_short_hash_vector(
    service,
):
    _, source, _, investigations = service
    other = source.parent / "initially-invalid.h5ad"
    other.write_bytes(b"not hdf5")
    failed = investigations.create(
        {"sources": [str(source), str(other)], "question": "What columns are stored?"}
    )
    assert (
        failed["status"] == "operation_failed" and len(failed["source_profiles"]) == 1
    )
    fixture_h5ad(other, rows=3, annotations=False)
    queued = investigations.action(
        failed["id"],
        {"action": "clarify", "expected_revision": failed["revision"]},
        defer_description=True,
    )
    current = wait_record(investigations, queued, {"completed", "operation_failed"})
    assert current["status"] == "completed", current.get("error")
    assert (
        current["source_profiles"][0]["sha256"]
        == failed["source_profiles"][0]["sha256"]
    )


def _slow_description_worker(root, *args):
    """Importable test worker to prove native cancellation and process cleanup."""
    (Path(root).parent / "worker-started").write_text("started")
    time.sleep(20)
    raise RuntimeError("Slow test worker should have been cancelled")


def test_real_supervisor_success_and_parser_error_cleanup(tmp_path, monkeypatch):
    import h5py

    from devharness_core import watchdog

    root = tmp_path / "inputs"
    root.mkdir()
    source = fixture_h5ad(root / "fixture.h5ad")
    observed = []
    run = watchdog.run_sandboxed

    def capture(*args, **kwargs):
        try:
            return run(*args, **kwargs)
        finally:
            observed.append(dict(kwargs["diagnostics"]))

    monkeypatch.setattr(watchdog, "run_sandboxed", capture)
    inputs = InputDiscovery([root])
    profile = inputs.inspect(source)
    result = inputs.describe(
        source, expected_sha256=profile["sha256"], topics=["annotations"]
    )
    assert result["completion"] == "completed"
    with h5py.File(source, "r+") as handle:
        handle["obs/cell_type/codes"][0] = 99
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match="categorical code"):
        inputs.describe(source, expected_sha256=expected, topics=["annotations"])
    assert len(observed) == 3 and all(
        item["cleanup"]["verified_stopped"] for item in observed
    )
    assert observed[-1]["supervisor_status"] == "failed"


def test_real_supervisor_cancellation_stops_worker(tmp_path, monkeypatch):
    from devbench.analysis import workbench_descriptions as descriptions
    from devharness_core import watchdog

    app = LocalApp(tmp_path)
    source = fixture_h5ad(app.input_roots[0] / "fixture.h5ad")
    observations = []
    run = watchdog.run_sandboxed

    def capture(*args, **kwargs):
        try:
            return run(*args, **kwargs)
        finally:
            observations.append(dict(kwargs["diagnostics"]))

    monkeypatch.setattr(watchdog, "run_sandboxed", capture)
    monkeypatch.setattr(descriptions, "_describe_worker", _slow_description_worker)
    investigations = InvestigationService(app, InputDiscovery(app.input_roots))
    try:
        record = investigations.create(
            {"source": str(source), "question": "How many genes?"},
            defer_description=True,
        )
        deadline = time.monotonic() + 10
        while (
            not (tmp_path / "worker-started").exists() and time.monotonic() < deadline
        ):
            current = investigations.get(record["id"])
            assert current["status"] != "operation_failed", current.get("error")
            time.sleep(0.02)
        assert (tmp_path / "worker-started").exists()
        current = investigations.get(record["id"])
        investigations.action(
            record["id"], {"action": "cancel", "expected_revision": current["revision"]}
        )
        current = wait_record(investigations, record, {"cancelled", "operation_failed"})
        assert current["status"] == "cancelled", current.get("error")
        assert current["description_operation"]["worker_cleanup_verified"] is True
        assert observations[-1]["supervisor_status"] == "cancelled"
        assert all(item["cleanup"]["verified_stopped"] for item in observations)
    finally:
        app._executor.shutdown(wait=True)
