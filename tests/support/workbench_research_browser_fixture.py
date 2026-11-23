"""Synthetic Workbench setup for the standalone Chromium smoke test."""

from contextlib import contextmanager

from devbench.analysis.checkpoints import atomic_json, digest, sha256
from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
from devbench.engine.tier_a_executor import TierAExecutor
from devbench.engine.workflow_operations import LEDGER_NAME, WorkflowOperationLedger
from devbench.platform.adaptive_workflow import ArtifactVersion
from devbench.platform.thesis_workflow import ThesisWorkflow
from devbench.platform.workbench_adaptive import WorkbenchAdaptiveBinding
from devbench.platform.workbench_ui import WorkbenchService
from devbench.platform.workflow_actions import WorkflowActions
from devharness_controlled.native_contracts import single_cell_kernel_images
from tests.test_investigation_publication_epochs import authorize
from tests.test_scientific_authority_migration import _binding
from tests.test_scientific_workspace import action, scientific_context, setup_workspace
from tests.test_scientific_workspace_http import _fixture_adaptive_controller, running
from tests.test_thesis_aims import _aim, _run_evidence
from tests.test_thesis_workbench_acceptance import _case_control_source
from tests.test_tier_a_scrna import _qc_step, _run_stage, _source
from tests.test_two_agent_investigation_v24 import decision
from tests.test_workbench_thesis_review import _assessment


def _seed_qc(app, flow, executor):
    source = _source(app.workspace)
    source_sha = executor.training.put_file(source)
    flow.register_artifact(
        ArtifactVersion(
            version_id="count-source",
            sha256=source_sha,
            kind="counts",
            representation="raw_counts_h5ad",
            biological_unit="donor",
            unit_membership_sha256=digest("Synthetic browser donor membership"),
            measurement_history=("raw_counts",),
        )
    )
    actions = WorkflowActions(
        app.scientific.planner(),
        flow.ledger,
        actor="fixture",
        selector=None,
        tier_a_executor=executor,
        allow_data_execution=True,
    )
    qc = _qc_step(executor)
    flow.insert(qc)
    checked = actions.perform_adaptive(flow, qc.step_id)
    assert checked["status"] == "succeeded", checked
    prepared = _run_stage(
        flow,
        executor,
        actions,
        "prepare-counts",
        "scrna_prepare",
        {"batch_key": "batch", "n_top_genes": 24, "normalization": "shifted_log"},
        {
            "expression": ("count-source", None),
            "qc_metrics": (qc.step_id, "qc_metrics"),
            "qc_features": (qc.step_id, "qc_features"),
        },
    )
    return source_sha, checked["receipt_sha256"], prepared["receipt_sha256"]


def _seed_thesis(app, ledger, lineage):
    packet = app.workspace / "masked-fixture-packet.txt"
    packet.write_text("Masked synthetic evidence for browser transport verification.\n")
    packet_sha = sha256(packet)
    reviewers = ("fixture-reviewer-a", "fixture-reviewer-b")

    def verify(subject, assessment):
        return (
            subject.masked_packet_sha256 == packet_sha
            and assessment.reviewer in reviewers
            and assessment == _assessment(assessment.reviewer, subject.sha256())
        )

    thesis = ThesisWorkflow(
        ledger,
        lineage,
        authorize_operator=lambda actor: actor == "scientist",
        verify_blinded_review=verify,
    )
    aim_id = "fixture-evidence"
    thesis.aims.propose(_aim(aim_id, blinded_review_required=True))
    thesis.aims.approve(
        aim_id, actor="scientist", rationale="Fixture operator checks scope."
    )
    evidence = _run_evidence(thesis.flow, thesis.aims, aim_id)
    schema = app.workspace / "fixture-answer-schema.json"
    atomic_json(
        schema,
        {
            "type": "object",
            "properties": {"cells_input": {"type": "integer", "minimum": 1}},
            "required": ["cells_input"],
            "additionalProperties": False,
        },
    )
    browser_inputs = {
        "aim_id": aim_id,
        "new_aim": _aim("browser-aim").model_dump(mode="json"),
        "outcome": {
            "status": "supported",
            "rationale": "The scripted synthetic evidence check passed.",
            "uncertainty": "Scientific extrapolation is outside this fixture.",
            "evidence": [evidence],
        },
        "packet": str(packet),
        "reviewers": list(reviewers),
        "assessments": [
            _assessment(reviewer, "0" * 64).model_dump(mode="json")
            for reviewer in reviewers
        ],
        "schema": str(schema),
        "schema_sha256": sha256(schema),
    }
    return thesis, browser_inputs


@contextmanager
def research_fixture(root, image):
    """Use a real pinned guest for QC and offline fixture roles for review."""
    if image not in single_cell_kernel_images():
        raise ValueError(
            "The fixture requires a locally available pinned single-cell image"
        )
    initial, methods, _, method_step, inputs = setup_workspace(root)
    method_selector = initial.scientific.selector
    initial.close()
    actors = root / "actors"
    actors.mkdir(mode=0o700)
    app = WorkbenchService(
        root / "research-browser",
        [inputs],
        enable_scientific=True,
        scientific_workspace=methods.root,
        allow_data_execution=True,
        codex_model="fixture-only-model",
        method_selector=method_selector,
        agent_workspaces=actors,
        allow_model_execution=True,
        model_data_scope="synthetic",
    )
    try:
        _, dataset, analysis = scientific_context(app, method_step)
        plan = action(
            app, "create_plan", dataset_ref=dataset["ref"], analysis_ref=analysis["ref"]
        )
        lineage = plan["session_id"]
        ledger = WorkflowOperationLedger(methods.root / LEDGER_NAME, scientific=True)
        authority = ledger.scientific
        authority.commit(lineage, expected_revision=0, record=_binding())
        authority.commit(
            lineage,
            expected_revision=1,
            record={
                "kind": "grant",
                "record_id": "grant-a",
                "binding_id": "binding-a",
                "grant_sha256": digest("Synthetic browser software verification grant"),
            },
        )
        authority.activate_execution(
            lineage,
            expected_revision=2,
            binding_id="binding-a",
            grant_id="grant-a",
            max_jobs=8,
            authorize=authorize,
        )
        executor = TierAExecutor(
            ledger, allow_data_execution=True, mounted_image_id=image
        )
        thesis, browser_inputs = _seed_thesis(app, ledger, lineage)
        source_sha, qc_receipt, prepared_receipt = _seed_qc(app, thesis.flow, executor)
        browser_inputs.update(
            session_id=lineage,
            source_sha256=source_sha,
            qc_receipt_sha256=qc_receipt,
            prepare_receipt_sha256=prepared_receipt,
        )
        broker = EvidenceBroker(ledger.scientific)
        policy = AccessPolicy(
            "research-browser", 1, allow_outcomes=True, allow_statistics=True
        )
        broker.register_policy(policy)
        broker.register_context(
            "viewer",
            principal_id="scientist",
            lineage_id=lineage,
            role="scientist",
            phase="readback",
            policy_sha256=policy.sha256(),
        )
        broker.register_source(
            "graph",
            content_sha256=digest("research browser graph"),
            roots=(digest("research browser graph root"),),
            classification="structural",
        )
        controller, selector = _fixture_adaptive_controller(
            app.scientific.planner(),
            ledger,
            lineage,
            broker,
            step_id="qc-lanes",
            thesis_lifecycle=thesis,
        )
        selector.script.extend(
            [decision("dispatch", step_id="prepare-counts"), decision()]
        )
        app.bind_adaptive_workflow(
            WorkbenchAdaptiveBinding(
                lineage,
                broker.delivery("viewer", "graph"),
                controller=controller,
                tier_a_executor=executor,
            )
        )
        state = app.workspace_state()
        anchors = app.scientific._plan(lineage)["scientific_anchors"]
        browser_inputs["analysis_ref"] = anchors["analysis_version"]
        request, _, _ = _case_control_source(inputs, file_type="h5ad")
        investigation = app.investigations.create(request)
        assert investigation["status"] == "completed", investigation.get("error")
        browser_inputs["matrix_investigation"] = {
            "id": investigation["id"],
            "question": request["question"],
        }
        state = app.workspace_state()
        catalog = app.scientific.catalog()
        app.workspace_state(
            {
                "expected_revision": state["revision"],
                "name": "Synthetic browser verification",
                "ui": {
                    "view": "scientific",
                    "study_id": catalog["studies"][0]["study_id"],
                    "dataset_ref": anchors["dataset_versions"][0],
                    "analysis_ref": anchors["analysis_version"],
                    "planner_session_id": lineage,
                },
            }
        )
        with running(app) as port:
            atomic_json(root / "browser-fixture.json", {**browser_inputs, "port": port})
            yield
    finally:
        app.close()
