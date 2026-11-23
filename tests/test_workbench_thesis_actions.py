"""Exercise the browser bridge against the actual Workbench thesis ledger."""

import json

import pytest

from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
from devbench.platform.thesis_aims import AimOutcome
from devbench.platform.thesis_workflow import ThesisWorkflow
from tests.test_thesis_aims import _aim, _run_evidence
from tests.test_workbench_pseudobulk import _install_controller, _open_workbench
from tests.test_workbench_thesis_review import _assessment

pytestmark = pytest.mark.wal


@pytest.fixture
def thesis_browser(workspace_tmp_path, monkeypatch, request):
    from devbench.engine.scientific_authority import ScientificAuthority

    activate = ScientificAuthority.activate_execution

    def activate_fixture(authority, lineage_id, *, max_jobs, **options):
        max_jobs = getattr(request, "param", max_jobs)
        return activate(authority, lineage_id, max_jobs=max_jobs, **options)

    with monkeypatch.context() as patch:
        patch.setattr(ScientificAuthority, "activate_execution", activate_fixture)
        app, ledger, lineage = _open_workbench(workspace_tmp_path)
    verified = {}
    lifecycle = ThesisWorkflow(
        ledger,
        lineage,
        authorize_operator=lambda actor: actor == "scientist",
        verify_blinded_review=lambda subject, assessment: verified.get(
            assessment.receipt_sha256
        )
        == (subject.sha256(), assessment),
    )
    _install_controller(app, ledger, lineage, lifecycle, None)
    try:
        yield app, lineage, lifecycle, verified
    finally:
        app.close()


def test_aim_actions_use_the_bound_principal_and_graph_revision(thesis_browser):
    from devbench.platform.workbench_thesis_actions import thesis_action, thesis_view

    app, lineage, lifecycle, _ = thesis_browser
    first = thesis_view(app, lineage)
    assert first["status"] == "available"
    assert first["clinical_release_allowed"] is False
    assert first["actions"]["propose_aim"]["enabled"] is True
    assert first["actions"]["synthesize"]["enabled"] is False
    assert first["aims"] == []

    definition = _aim("donor-outcomes").model_dump(mode="json")
    proposed = thesis_action(
        app,
        lineage,
        {
            "action": "propose_aim",
            "expected_revision": first["revision"],
            "definition": definition,
        },
    )
    open_aim = thesis_view(app, lineage)
    assert open_aim["revision"] == proposed["revision"]
    assert open_aim["aims"][0]["status"] == "operator_review"
    assert definition["question"] not in json.dumps(open_aim)

    approval = {
        "action": "approve_aim",
        "aim_id": "donor-outcomes",
        "rationale": "Review the retained synthetic aim scope.",
    }
    with pytest.raises(OperationConflict, match="revision changed"):
        thesis_action(
            app, lineage, {**approval, "expected_revision": first["revision"]}
        )
    with pytest.raises(ValueError, match="declared fields"):
        thesis_action(
            app,
            lineage,
            {**approval, "expected_revision": proposed["revision"], "actor": "other"},
        )
    result = thesis_action(
        app, lineage, {**approval, "expected_revision": proposed["revision"]}
    )
    assert thesis_view(app, lineage)["aims"][0]["status"] == "ready"
    assert lifecycle.aims.history()[-1]["actor"] == "scientist"

    revised = thesis_action(
        app,
        lineage,
        {
            "action": "revise_aim",
            "expected_revision": result["revision"],
            "definition": {**definition, "question": "A revised research question."},
            "rationale": "Narrow the declared question.",
        },
    )
    current = thesis_view(app, lineage)
    assert current["revision"] == revised["revision"]
    assert current["aims"][0]["version"] == 2
    assert current["aims"][0]["status"] == "operator_review"


def test_review_uses_the_independent_verifier_and_retains_evidence(thesis_browser):
    from devbench.platform.workbench_thesis_actions import thesis_action, thesis_view

    app, lineage, lifecycle, verified = thesis_browser
    aims = lifecycle.aims
    aims.propose(_aim("blinded", blinded_review_required=True))
    aims.approve("blinded", actor="scientist", rationale="Review the exact aim scope.")
    evidence = _run_evidence(lifecycle.flow, aims, "blinded")
    outcome = AimOutcome(
        status="supported",
        rationale="The synthetic check passed.",
        uncertainty="Independent cohorts are untested.",
        evidence=(evidence,),
    ).model_dump(mode="json")
    packet = app.workspace / "browser-masked-packet.txt"
    packet.write_text("Masked synthetic research evidence.")
    view = thesis_view(app, lineage)
    opened = thesis_action(
        app,
        lineage,
        {
            "action": "open_blinded_review",
            "expected_revision": view["revision"],
            "aim_id": "blinded",
            "version": 1,
            "outcome": outcome,
            "masked_packet": str(packet),
            "reviewers": ["reviewer-a", "reviewer-b"],
            "rationale": "Freeze the prepared research packet.",
        },
    )
    panel = opened["panel"]
    assert panel["status"] == "awaiting_independent_reviews"
    publish = {
        "action": "record_outcome",
        "aim_id": "blinded",
        "version": 1,
        "outcome": outcome,
    }
    with pytest.raises(OperationBlocked, match="awaiting_independent_reviews"):
        thesis_action(
            app, lineage, {**publish, "expected_revision": opened["revision"]}
        )

    assessment_path = app.workspace / "browser-assessment.json"
    review = {
        "action": "submit_blinded_review",
        "aim_id": "blinded",
        "version": 1,
        "assessment_file": str(assessment_path),
    }
    revision = opened["revision"]
    for reviewer in ("reviewer-a", "reviewer-b"):
        assessment = _assessment(reviewer, panel["subject_sha256"])
        assessment_path.write_text(assessment.model_dump_json())
        with pytest.raises(PermissionError, match="independently verified"):
            thesis_action(app, lineage, {**review, "expected_revision": revision})
        verified[assessment.receipt_sha256] = (panel["subject_sha256"], assessment)
        admitted = thesis_action(
            app, lineage, {**review, "expected_revision": revision}
        )
        revision = admitted["revision"]
    assert admitted["panel"]["status"] == "passed"
    thesis_action(app, lineage, {**publish, "expected_revision": revision})

    current = thesis_view(app, lineage)
    closed = current["aims"][0]
    assert closed["status"] == "supported"
    assert closed["outcome"]["evidence"] == [evidence]
    assert closed["blinded_review"]["submitted_reviews"] == 2
    public = json.dumps(current)
    assert outcome["rationale"] not in public
    assert outcome["uncertainty"] not in public
    assert "reviewer-a" not in public
    assert str(packet) not in public


@pytest.mark.parametrize("thesis_browser", [17], indirect=True)
def test_outcome_citation_survives_capped_aim_evidence(thesis_browser):
    from devbench.platform.workbench_thesis_actions import thesis_view

    app, lineage, lifecycle, _ = thesis_browser
    aims = lifecycle.aims
    aims.propose(_aim("many-artifacts"))
    aims.approve("many-artifacts", actor="scientist", rationale="Review the aim.")
    evidence = [
        _run_evidence(lifecycle.flow, aims, "many-artifacts", step_id=f"fit-{index:02}")
        for index in range(17)
    ]
    available = thesis_view(app, lineage)["aims"][0]["evidence"]
    assert len(available) == 16 and evidence[-1] not in available

    aims.record_outcome(
        "many-artifacts",
        version=1,
        status="supported",
        rationale="Use the final retained fit.",
        uncertainty="Independent replication remains open.",
        evidence=(evidence[-1],),
    )
    closed = thesis_view(app, lineage)["aims"][0]
    assert closed["outcome"] == {"status": "supported", "evidence": evidence[-1:]}
    assert len(closed["evidence"]) == 16 and evidence[-1] in closed["evidence"]

    lifecycle.flow.hold("fit-16", "review-required", evidence_sha256="e" * 64)
    held = thesis_view(app, lineage)["aims"][0]
    assert held["status"] == "invalidated"
    assert held["evidence"] == [] and "outcome" not in held


def test_package_inventory_prioritizes_current_packages_before_limiting(
    workspace_tmp_path,
):
    from os import utime
    from types import SimpleNamespace

    from devbench.platform.workbench_thesis_export import (
        _MAX_LISTED_PACKAGES,
        record_thesis_export,
        thesis_export_inventory,
    )

    app = SimpleNamespace(workspace=workspace_tmp_path)
    lineage = "a" * 32
    directory = app.workspace / "thesis-package-index" / lineage
    package = {"root_sha256": "b" * 64, "outcome": "partial"}
    for index in range(_MAX_LISTED_PACKAGES + 4):
        package_id = f"{index:032x}"
        revision, modified = {
            1: (11, 2),
            2: (11, 2),
            3: (11, 1),
            4: (9, 4),
            _MAX_LISTED_PACKAGES + 1: (10, 2),
            _MAX_LISTED_PACKAGES + 2: (10, 3),
        }.get(index, (8, 4))
        record_thesis_export(app, lineage, package_id, package, revision)
        utime(directory / f"{package_id}.json", (modified, modified))

    listing = thesis_export_inventory(app, lineage, 10, allow_export=True)
    leading = (_MAX_LISTED_PACKAGES + 2, _MAX_LISTED_PACKAGES + 1, 1, 2, 3, 4)
    assert [row["package_id"] for row in listing[:6]] == [
        f"{index:032x}" for index in leading
    ]
    assert len(listing) == _MAX_LISTED_PACKAGES
    assert [row["status"] for row in listing] == (
        ["current"] * 2 + ["stale"] * (_MAX_LISTED_PACKAGES - 2)
    )
    assert all(row["download"] for row in listing[:2])
    assert all(row["download"] is None for row in listing[2:])


def test_package_export_holds_until_terminal(workspace_tmp_path, monkeypatch):
    import hashlib
    import io
    import zipfile
    from pathlib import Path

    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
    from devbench.analysis.thesis_synthesis import verify_thesis_package
    from devbench.engine.tier_a_executor import TierAExecutor
    from devbench.platform.workbench_adaptive import WorkbenchAdaptiveBinding
    from devbench.platform.workbench_thesis_actions import thesis_action, thesis_view
    from devbench.platform.workbench_thesis_export import export_thesis_package
    from tests.test_investigation_invalidation import digest
    from tests.test_scientific_workspace_http import _fixture_adaptive_controller

    app, ledger, lineage = _open_workbench(workspace_tmp_path)
    try:
        lifecycle = ThesisWorkflow(
            ledger,
            lineage,
            authorize_operator=lambda actor: actor == "scientist",
        )
        broker = EvidenceBroker(ledger.scientific)
        policy = AccessPolicy(
            "thesis-readback",
            1,
            allow_outcomes=True,
            allow_statistics=True,
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
            content_sha256=digest("thesis graph"),
            roots=(digest("root"),),
            classification="structural",
        )
        controller, _ = _fixture_adaptive_controller(
            app.scientific.planner(),
            ledger,
            lineage,
            broker,
            thesis_lifecycle=lifecycle,
        )
        app.bind_adaptive_workflow(
            WorkbenchAdaptiveBinding(
                lineage,
                broker.delivery("viewer", "graph"),
                controller=controller,
                tier_a_executor=TierAExecutor(ledger),
            )
        )
        schema_data = '{"type":"object","additionalProperties":false}'
        schema = app.workspace / "synthetic-schema.json"
        schema.write_text(schema_data)
        request = {
            "action": "synthesize",
            "answer_schema": str(schema),
            "spec": {
                "question": "What can the retained synthetic evidence support?",
                "outcome": "partial",
                "answer_schema_sha256": digest(schema_data),
            },
        }
        before = thesis_view(app, lineage)
        assert before["actions"]["synthesize"]["enabled"] is False
        assert "terminal" in before["actions"]["synthesize"]["reason"]
        with pytest.raises(PermissionError, match="terminal"):
            thesis_action(
                app, lineage, {**request, "expected_revision": before["revision"]}
            )
        lifecycle.flow.stop_controller(
            {"status": "partial", "reason": "synthetic fixture"}
        )
        current = thesis_view(app, lineage)
        assert current["actions"]["synthesize"]["enabled"] is True
        with pytest.raises(OperationConflict, match="revision changed"):
            thesis_action(
                app, lineage, {**request, "expected_revision": before["revision"]}
            )
        package = thesis_action(
            app, lineage, {**request, "expected_revision": current["revision"]}
        )["package"]
        root = Path(package["directory"])
        assert root.is_relative_to(app.workspace)
        assert (
            package["answer_status"] == "withheld" and package["outcome"] == "partial"
        )
        verification = verify_thesis_package(root, package["root_sha256"])
        assert verification["evidence_verified"] is True
        manifest = json.loads((root / "provenance_manifest.json").read_text())
        assert manifest["clinical_release_allowed"] is False
        assert (root / "summary.md").exists()
        assert not (root / "final_discovery.json").exists()
        inventory = thesis_view(app, lineage)["packages"]
        assert len(inventory) == 1 and inventory[0]["status"] == "current"
        assert inventory[0]["package_id"] == package["package_id"]
        export = export_thesis_package(app, lineage, package["package_id"])
        with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
            receipt = archive.read("thesis-package-receipt.json")
            assert hashlib.sha256(receipt).hexdigest() == package["root_sha256"]
            for name, checksum in json.loads(receipt)["files"].items():
                assert hashlib.sha256(archive.read(name)).hexdigest() == checksum
        lifecycle.aims.propose(_aim("open-followup"))
        stale = thesis_view(app, lineage)["packages"][0]
        assert stale["status"] == "stale" and stale["download"] is None
        with pytest.raises(OperationBlocked, match="thesis_package_stale"):
            export_thesis_package(app, lineage, package["package_id"])

        from devbench.platform import workbench_thesis_actions

        write_package = workbench_thesis_actions.write_workbench_thesis_package

        def revise_after_capture(*args, **kwargs):
            result = write_package(*args, **kwargs)
            lifecycle.aims.propose(_aim("concurrent-revision"))
            return result

        monkeypatch.setattr(
            workbench_thesis_actions,
            "write_workbench_thesis_package",
            revise_after_capture,
        )
        latest = thesis_view(app, lineage)
        with pytest.raises(OperationConflict, match="revision changed"):
            thesis_action(
                app, lineage, {**request, "expected_revision": latest["revision"]}
            )
        assert {row["status"] for row in thesis_view(app, lineage)["packages"]} == {
            "stale"
        }
    finally:
        app.close()
