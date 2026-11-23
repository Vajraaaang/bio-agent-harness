"""Adaptive thesis packaging retains only verified and current evidence."""

import hashlib
import io
import json
import os
from contextlib import contextmanager

import pytest

from devbench.platform.adaptive_workflow import StepReceipt, request_digest
from rna_kernels.canonical import canonical_json
from tests.test_investigation_invalidation import artifact, make, step

pytestmark = pytest.mark.wal


class _DiagnosticExecutor:
    backend_id = "synthetic-thesis-diagnostic"

    def __init__(self, *, fail=False, value=None):
        self.fail = fail
        self.value = (
            {"count": 12, "conclusion": "Bound diagnostic result"}
            if value is None
            else value
        )
        self.contents = {}

    def _output(self, version_id, value, *, kind="table", representation="json"):
        version = artifact(
            version_id, kind=kind, representation=representation
        ).model_copy(
            update={"sha256": hashlib.sha256(value).hexdigest()},
        )
        self.contents[version.sha256] = value
        return version

    def run(self, envelope):
        step_id = envelope["step"]["step_id"]
        if self.fail:
            return StepReceipt(
                job_id=step_id,
                request_sha256=request_digest(envelope),
                status="failed",
                consumed=envelope["pins"],
                reason="synthetic_rank_deficit",
                outputs={},
            )
        version = self._output(step_id, canonical_json(self.value))
        return StepReceipt(
            job_id=step_id,
            request_sha256=request_digest(envelope),
            status="succeeded",
            consumed=envelope["pins"],
            outputs={"out": version},
        )

    def verify(self, envelope, receipt):
        return receipt.request_sha256 == request_digest(envelope) and all(
            hashlib.sha256(self.contents[value.sha256]).hexdigest() == value.sha256
            for value in receipt.outputs.values()
        )

    @contextmanager
    def open(self, artifact_version):
        with io.BytesIO(self.contents[artifact_version.sha256]) as handle:
            yield handle


class _FigureExecutor(_DiagnosticExecutor):
    def run(self, envelope):
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (16, 16), color=(10, 100, 120)).save(buffer, format="PNG")
        figure = self._output(
            "figure-v1",
            buffer.getvalue(),
            kind="figure",
            representation="test_png",
        )
        numeric = self._output("sidecar-v1", b"x,y\n1,2\n", representation="test_csv")
        content = {
            "plot": {
                "status": "rendered",
                "data_sha256": numeric.sha256,
                "image_sha256": figure.sha256,
                "plot_state": {
                    "render_verified": True,
                    "scientific_assessment": "assessed",
                },
            }
        }
        report = self._output("plot-report-v1", canonical_json(content))
        return StepReceipt(
            job_id="figure",
            request_sha256=request_digest(envelope),
            consumed=envelope["pins"],
            status="succeeded",
            outputs={"figure": figure, "numeric": numeric, "report": report},
        )


def _spec(root, flow, *, outcome="completed", failed=False):
    from devbench.analysis.thesis_synthesis import ThesisSynthesisSpec

    schema = root / "answer_schema.json"
    fields = {
        "count": {"type": "integer"},
        "refusal_reason": {"type": "string"},
    }
    schema.write_bytes(
        canonical_json(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": fields,
                "required": ["count"] if not failed else ["refusal_reason"],
            }
        )
    )
    spec = ThesisSynthesisSpec.model_validate(
        {
            "question": "How many observations met the frozen diagnostic?",
            "outcome": outcome,
            "expected_revision": flow.status()["revision"],
            "answer_schema_sha256": hashlib.sha256(schema.read_bytes()).hexdigest(),
            "fields": {
                "count": {"step_id": "qc", "port": "out", "path": ["count"]},
            },
            "follow_up": [
                {
                    "experiment": "Repeat the assay in an independent donor cohort.",
                    "uncertainty": "Holdout repeatability.",
                    "observable": "The donor-level diagnostic retention rate.",
                }
            ],
        }
    )
    return schema, spec


@pytest.mark.parametrize("target", ["summary.md", "final_discovery.json"])
def test_package_renders_current_results_and_detects_numeric_tampering(
    workspace_tmp_path,
    target,
):
    from devbench.analysis.episode_synthesis import verify_synthesis
    from devbench.analysis.thesis_synthesis import write_thesis_package

    root = workspace_tmp_path
    flow = make(root)
    worker = _DiagnosticExecutor()
    flow.insert(step("qc", purpose="checked_observation"))
    result = flow.dispatch("qc", worker, actor="researcher")
    schema, spec = _spec(root, flow)
    package = root / "thesis"
    receipt = write_thesis_package(
        package,
        flow,
        spec,
        answer_schema=schema,
        open_artifact=worker.open,
    )

    assert receipt["answer_status"] == "validated"
    assert json.loads((package / "final_discovery.json").read_text()) == {"count": 12}
    manifest = json.loads((package / "provenance_manifest.json").read_text())
    assert manifest["clinical_release_allowed"] is False
    assert manifest["fields"]["count"]["receipt_sha256"] == result["receipt_sha256"]
    summary = package / "summary.md"
    assert sum(line.startswith("## ") for line in summary.read_text().splitlines()) == 5
    assert "No verified plot" in summary.read_text()
    assert "Holdout repeatability" in summary.read_text()
    assert (package / "replay_bundle" / "replay.py").is_file()
    assert verify_synthesis(
        package,
        receipt["root_sha256"],
        allow_evidence_read=True,
    )["valid"]

    modified = package / target
    modified.write_text(modified.read_text().replace("12", "13"))
    with pytest.raises(ValueError, match=target):
        verify_synthesis(package, receipt["root_sha256"], allow_evidence_read=True)


@pytest.mark.parametrize("stage", ("ready", "reopened"))
def test_completed_package_requires_a_current_outcome_for_approved_aims(
    workspace_tmp_path, stage
):
    from devbench.analysis.thesis_synthesis import (
        verify_thesis_package,
        write_thesis_package,
    )
    from devbench.engine.workflow_operations import OperationBlocked
    from tests.test_thesis_aims import _aim, _approve, _lifecycle, _run_outcome

    root = workspace_tmp_path
    flow, aims = _lifecycle(root)
    aim = _aim("approved-aim")
    aims.propose(aim)
    _approve(aims, aim.aim_id)
    worker = _DiagnosticExecutor()
    flow.insert(step("qc", purpose="checked_observation"))
    flow.dispatch("qc", worker, actor="researcher")
    if stage == "reopened":
        question = "Does the trend persist across groups?"
        aims.revise(
            aim.model_copy(update={"question": question}),
            actor="researcher",
            rationale="Refined the scientific question before closure.",
        )
    schema, spec = _spec(root, flow)
    complete = root / "completed"
    with pytest.raises(OperationBlocked, match="thesis_approved_aim_not_closed"):
        write_thesis_package(
            complete, flow, spec, answer_schema=schema, open_artifact=worker.open,
        )
    assert not complete.exists()

    partial = root / "partial"
    receipt = write_thesis_package(
        partial,
        flow,
        spec.model_copy(update={"outcome": "partial"}),
        answer_schema=schema,
        open_artifact=worker.open,
    )
    assert verify_thesis_package(partial, receipt["root_sha256"])["valid"]

    if stage == "reopened":
        _approve(aims, aim.aim_id)
    _run_outcome(flow, aims, aim.aim_id, result="supported")
    aims.propose(_aim("unapproved-draft"))
    _, spec = _spec(root, flow)
    receipt = write_thesis_package(
        complete, flow, spec, answer_schema=schema, open_artifact=worker.open,
    )
    assert verify_thesis_package(complete, receipt["root_sha256"])["valid"]
    assert receipt["outcome"] == "completed"


@pytest.mark.parametrize(
    "purpose", ("admitted_research_inference", "qualified_inference")
)
def test_verifier_requires_tested_family_for_cited_negative_bulk_aim(
    workspace_tmp_path, purpose,
):
    from devbench.analysis.thesis_synthesis import (
        THESIS_RECEIPT,
        _render_methods,
        render_thesis_summary,
        verify_thesis_package,
        write_thesis_package,
    )
    from tests.test_thesis_aims import _aim, _approve, _lifecycle, _run_outcome

    root = workspace_tmp_path
    flow, aims = _lifecycle(root)
    aim = _aim("negative-aim")
    aims.propose(aim)
    _approve(aims, aim.aim_id)
    citation = _run_outcome(flow, aims, aim.aim_id)
    worker = _DiagnosticExecutor()
    flow.insert(step("qc", purpose="checked_observation"))
    flow.dispatch("qc", worker, actor="researcher")
    schema, spec = _spec(root, flow)
    package = root / "thesis"
    write_thesis_package(
        package, flow, spec, answer_schema=schema, open_artifact=worker.open,
    )

    # Simulate a writer that seals a bulk inference claim without its family.
    manifest = json.loads((package / "provenance_manifest.json").read_bytes())
    recorded, = manifest["aim_lifecycle"]["research_aims"]
    recorded.update(kind="confirmatory", multiplicity={"fdr": 0.05})
    method, = (
        row for row in manifest["methods"] if row["step_id"] == citation["step_id"]
    )
    method.update(operation_id="bulk_condition_contrast", purpose=purpose)
    answer = json.loads((package / "final_discovery.json").read_bytes())
    receipt = json.loads((package / THESIS_RECEIPT).read_bytes())
    for name, payload in {
        "provenance_manifest.json": canonical_json(manifest),
        "summary.md": render_thesis_summary(manifest, answer),
        "methods.md": _render_methods(manifest["methods"], manifest["runtime"]),
    }.items():
        (package / name).write_bytes(payload)
        receipt["files"][name] = hashlib.sha256(payload).hexdigest()
    wire = canonical_json(receipt)
    (package / THESIS_RECEIPT).write_bytes(wire)
    with pytest.raises(ValueError, match="Aim negative-aim lacks.*tested family"):
        verify_thesis_package(package, hashlib.sha256(wire).hexdigest())


def test_failed_attempt_withholds_unanswerable_schema(workspace_tmp_path):
    from devbench.analysis.thesis_synthesis import write_thesis_package

    root = workspace_tmp_path
    flow = make(root)
    worker = _DiagnosticExecutor(fail=True)
    flow.insert(step("qc", purpose="checked_observation"))
    flow.dispatch("qc", worker, actor="researcher")
    schema, spec = _spec(root, flow, outcome="refused", failed=True)
    package = root / "thesis"
    receipt = write_thesis_package(
        package,
        flow,
        spec,
        answer_schema=schema,
        open_artifact=worker.open,
    )
    assert receipt["answer_status"] == "withheld"
    assert not (package / "final_discovery.json").exists()
    assert "synthetic_rank_deficit" in (package / "summary.md").read_text()


def test_refusal_can_answer_an_explicit_schema_from_its_failed_receipt(
    workspace_tmp_path,
):
    from devbench.analysis.thesis_synthesis import (
        AnswerFieldRef,
        ThesisSynthesisSpec,
        write_thesis_package,
    )

    root = workspace_tmp_path
    flow = make(root)
    worker = _DiagnosticExecutor(fail=True)
    flow.insert(step("qc", purpose="checked_observation"))
    flow.dispatch("qc", worker, actor="researcher")
    schema, base = _spec(root, flow, outcome="refused", failed=True)
    spec = ThesisSynthesisSpec.model_validate(
        {
            **base.model_dump(mode="python"),
            "fields": {
                "refusal_reason": AnswerFieldRef(
                    step_id="qc", projection="failure_reason"
                ),
            },
        }
    )
    package = root / "thesis"
    receipt = write_thesis_package(
        package,
        flow,
        spec,
        answer_schema=schema,
        open_artifact=worker.open,
    )
    assert receipt["answer_status"] == "validated"
    assert json.loads((package / "final_discovery.json").read_text()) == {
        "refusal_reason": "synthetic_rank_deficit",
    }


def test_upstream_invalidation_blocks_a_current_package(workspace_tmp_path):
    from devbench.analysis.thesis_synthesis import write_thesis_package
    from devbench.engine.workflow_operations import OperationConflict

    root = workspace_tmp_path
    flow = make(root)
    worker = _DiagnosticExecutor()
    flow.insert(step("qc", purpose="checked_observation"))
    result = flow.dispatch("qc", worker, actor="researcher")
    schema, spec = _spec(root, flow)
    flow.hold("qc", "QC-review", evidence_sha256=result["receipt_sha256"])
    package = root / "thesis"
    with pytest.raises(OperationConflict, match="current ledger revision"):
        write_thesis_package(
            package,
            flow,
            spec,
            answer_schema=schema,
            open_artifact=worker.open,
        )
    assert not package.exists()


def test_figure_pair_requires_same_receipt_and_retains_assessment_scope(
    workspace_tmp_path,
):
    from devbench.analysis.thesis_synthesis import (
        ThesisSynthesisSpec,
        verify_thesis_package,
        write_thesis_package,
    )
    from devbench.platform.adaptive_workflow import AdaptiveStep

    root = workspace_tmp_path
    flow = make(root)
    diagnostic = _DiagnosticExecutor()
    worker = _FigureExecutor()
    flow.insert(step("qc", purpose="checked_observation"))
    document = step("plot", purpose="checked_observation").model_dump(mode="json")
    document["profile"]["operations"][0]["output_kinds"].append("figure")
    document["outputs"] = [
        {"name": "figure", "artifact_kind": "figure"},
        {"name": "numeric", "artifact_kind": "table"},
        {"name": "report", "artifact_kind": "table"},
    ]
    flow.insert(AdaptiveStep.model_validate(document))
    flow.dispatch("qc", diagnostic, actor="researcher")
    result = flow.dispatch("plot", worker, actor="researcher")
    schema, base = _spec(root, flow)
    spec = ThesisSynthesisSpec.model_validate(
        {
            **base.model_dump(mode="json"),
            "figures": [
                {
                    "step_id": "plot",
                    "figure_port": "figure",
                    "numeric_port": "numeric",
                    "report_port": "report",
                }
            ],
        }
    )
    package = root / "thesis"
    worker.contents.update(diagnostic.contents)
    seal = write_thesis_package(
        package,
        flow,
        spec,
        answer_schema=schema,
        open_artifact=worker.open,
    )
    manifest = json.loads((package / "provenance_manifest.json").read_bytes())
    (figure,) = manifest["figures"]
    assert figure["receipt_sha256"] == result["receipt_sha256"]
    assert figure["plot_state"]["scientific_assessment"] == "pending"
    assert figure["plot_state"]["image_accessible"]
    assert (package / figure["numeric_sidecar"]).read_bytes() == b"x,y\n1,2\n"
    (package / figure["numeric_sidecar"]).write_bytes(b"x,y\n1,3\n")
    with pytest.raises(ValueError, match="figure-01.csv"):
        verify_thesis_package(package, seal["root_sha256"])


@pytest.mark.parametrize(
    ("test_q", "exception", "reason"),
    [
        (0.01, ValueError, "0 <= p <= q <= 1"),
        (0.20, PermissionError, "Exploratory evidence cannot supply inference"),
    ],
)
def test_gene_statistics_cannot_leak_from_exploratory_receipts(
    workspace_tmp_path,
    test_q,
    exception,
    reason,
):
    from devbench.analysis.thesis_synthesis import (
        ThesisSynthesisSpec,
        write_thesis_package,
    )

    root = workspace_tmp_path
    flow = make(root)
    worker = _DiagnosticExecutor(
        value={
            "gene_statistics": [{"gene_id": "g1", "p_value": 0.1, "q_value": test_q}],
        }
    )
    flow.insert(step("qc", purpose="checked_observation"))
    flow.dispatch("qc", worker, actor="researcher")
    schema, base = _spec(root, flow)
    spec = ThesisSynthesisSpec.model_validate(
        {
            **base.model_dump(mode="json"),
            "fields": {
                "gene_statistics": {
                    "step_id": "qc",
                    "port": "out",
                    "path": ["gene_statistics"],
                },
            },
        }
    )
    with pytest.raises(exception, match=reason):
        write_thesis_package(
            root / "thesis",
            flow,
            spec,
            answer_schema=schema,
            open_artifact=worker.open,
        )


@pytest.mark.parametrize("status", ("not_estimable", "exploratory", "filtered"))
def test_nonestimated_gene_rows_require_null_probabilities(status):
    from devbench.analysis.thesis_synthesis_evidence import validate_gene_fields

    row = {"gene_id": "g1", "p_value": 0.1, "q_value": 0.2}
    validate_gene_fields({"gene_statistics": [row]}, inferential_scope=True)
    with pytest.raises(ValueError, match="require null probabilities"):
        validate_gene_fields(
            {"gene_statistics": [{**row, "estimate_status": status}]},
            inferential_scope=True,
        )
    null_row = {**row, "estimate_status": status, "p_value": None, "q_value": None}
    validate_gene_fields({"gene_statistics": [null_row]}, inferential_scope=True)


def test_same_image_mounted_replay_matches_exact_genes_parquet(
    workspace_tmp_path,
    monkeypatch,
):
    from devbench.analysis.thesis_replay import replay_thesis_package
    from devbench.analysis.thesis_synthesis import (
        ThesisSynthesisSpec,
        write_thesis_package,
    )
    from tests.test_tier_a_executor import _fixture, _submit

    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if not image:
        pytest.skip("Set the exact local RNA kernel image ID")
    root = workspace_tmp_path
    flow, executor, envelope = _fixture(root, input_format="h5", mounted_image_id=image)
    # This synthetic fixture supplies the review decision and exercises the real
    # retained receipt and acceptance gates around it.
    monkeypatch.setattr(flow, "_review_basis", lambda *_: envelope["design_review"])
    token, receipt = _submit(flow, executor, envelope)
    result = flow.complete(token, receipt, verify=executor.verify)
    assert result["status"] == "succeeded", result
    schema = root / "answer_schema.json"
    schema.write_bytes(
        canonical_json(
            {
                "type": "object",
                "properties": {"gene_statistics": {"type": "array", "minItems": 2}},
                "additionalProperties": False,
                "required": ["gene_statistics"],
            }
        )
    )
    spec = ThesisSynthesisSpec.model_validate(
        {
            "question": "Do these synthetic expression profiles differ?",
            "outcome": "completed",
            "expected_revision": flow.status()["revision"],
            "primary_branch_id": "bulk",
            "answer_schema_sha256": hashlib.sha256(schema.read_bytes()).hexdigest(),
            "fields": {
                "gene_statistics": {
                    "step_id": "bulk-fit",
                    "port": "result",
                    "projection": "tier_a_gene_statistics",
                },
            },
        }
    )
    package = root / "thesis"
    seal = write_thesis_package(
        package,
        flow,
        spec,
        answer_schema=schema,
        open_artifact=executor.open_output,
        tier_a_executor=executor,
    )
    answer = json.loads((package / "final_discovery.json").read_text())
    assert len(answer["gene_statistics"]) == 2
    assert all(
        0 <= row["p_value"] <= row["q_value"] <= 1 for row in answer["gene_statistics"]
    )
    replay = replay_thesis_package(
        package,
        seal["root_sha256"],
        output=root / "replayed",
        input_root=executor.training.root,
        allow_data_execution=True,
    )
    assert replay["exact_genes_parquet_match"]
    assert replay["steps"] == {"bulk-fit": receipt.outputs["result"].sha256}
