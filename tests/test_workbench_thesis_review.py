"""Exercise local file intake for a Workbench-owned blinded review panel."""

import hashlib
import json

import pytest

from devbench.core.thesis_blinded_review import BlindedAimAssessment
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
from devbench.platform.thesis_aims import AimOutcome
from devbench.platform.thesis_workflow import ThesisWorkflow
from devbench.platform.workbench_adaptive import _graph_revision
from tests.test_investigation_invalidation import digest
from tests.test_thesis_aims import _aim, _approve, _run_evidence
from tests.test_workbench_pseudobulk import _install_controller, _open_workbench

pytestmark = pytest.mark.wal


def _fixture_review(app, ledger, lineage):
    verified = {}

    def verify(subject, assessment):
        return verified.get(assessment.receipt_sha256) == (subject.sha256(), assessment)

    thesis = ThesisWorkflow(
        ledger,
        lineage,
        authorize_operator=lambda actor: actor == "researcher",
        verify_blinded_review=verify,
    )
    aims = thesis.aims
    aims.propose(_aim("independent-review", blinded_review_required=True))
    _approve(aims, "independent-review")
    _install_controller(app, ledger, lineage, thesis, None)
    evidence = _run_evidence(thesis.flow, aims, "independent-review")
    outcome = AimOutcome.model_validate(
        {
            "status": "supported",
            "rationale": "The fixture supports a scoped observation.",
            "uncertainty": "Independent cohorts would test generalization.",
            "evidence": [evidence],
        }
    )
    return thesis, outcome, verified


def _assessment(reviewer, subject):
    return BlindedAimAssessment(
        reviewer=reviewer,
        subject_sha256=subject,
        receipt_sha256=digest(reviewer),
        ratings=dict.fromkeys(
            (
                "units",
                "design",
                "provenance",
                "claim_scope",
                "alternatives",
                "follow_up",
            ),
            "supports",
        ),
        rationale="The masked evidence supports the scoped observation.",
    )


def test_workbench_binds_the_packet_and_verified_assessment_files(workspace_tmp_path):
    from devbench.platform.workbench_thesis import (
        open_workbench_blinded_review,
        submit_workbench_blinded_review,
    )

    app, ledger, lineage = _open_workbench(workspace_tmp_path)
    try:
        thesis, outcome, verified = _fixture_review(app, ledger, lineage)
        packet = app.workspace / "blind-packet.txt"
        packet.write_bytes(b"Masked synthetic evidence for the assigned reviewers.\n")
        revision = _graph_revision(thesis.flow)
        opening = {
            "aim_id": "independent-review",
            "version": 1,
            "outcome": outcome,
            "masked_packet": packet,
            "reviewers": ("reviewer-a", "reviewer-b"),
            "actor": "researcher",
            "rationale": "Bind the prepared synthetic packet.",
        }
        with pytest.raises(OperationConflict, match="revision changed"):
            open_workbench_blinded_review(
                app, lineage, **opening, expected_revision=revision - 1
            )
        alias = app.workspace / "packet-link.txt"
        alias.symlink_to(packet)
        with pytest.raises(PermissionError, match="admitted workspace"):
            open_workbench_blinded_review(
                app,
                lineage,
                **{**opening, "masked_packet": alias},
                expected_revision=revision,
            )
        assert _graph_revision(thesis.flow) == revision
        opened = open_workbench_blinded_review(
            app,
            lineage,
            **opening,
            expected_revision=revision,
        )
        panel = opened["panel"]
        assert (
            panel["masked_packet_sha256"]
            == hashlib.sha256(packet.read_bytes()).hexdigest()
        )
        assert panel["status"] == "awaiting_independent_reviews"
        assert panel["submitted_reviews"] == 0
        subject = panel["subject_sha256"]
        with pytest.raises(OperationBlocked, match="awaiting_independent_reviews"):
            thesis.aims.record_outcome(
                "independent-review", version=1, **outcome.model_dump(mode="json")
            )

        first = _assessment("reviewer-a", subject)
        letter = app.workspace / "assessment.json"
        args = {
            "aim_id": "independent-review",
            "version": 1,
            "assessment_file": letter,
            "expected_revision": opened["revision"],
        }
        letter.write_text(first.model_dump_json(indent=2))
        with pytest.raises(PermissionError, match="independently verified"):
            submit_workbench_blinded_review(app, lineage, **args)
        assert _graph_revision(thesis.flow) == opened["revision"]

        verified[first.receipt_sha256] = (subject, first)
        outside = workspace_tmp_path / "outside-assessment.json"
        outside.write_text(first.model_dump_json())
        with pytest.raises(PermissionError, match="admitted workspace"):
            submit_workbench_blinded_review(
                app, lineage, **{**args, "assessment_file": outside},
            )
        body = first.model_dump_json()
        letter.write_text(body[:-1] + ',"reviewer":"reviewer-a"}')
        with pytest.raises(ValueError, match="Duplicate receipt JSON keys"):
            submit_workbench_blinded_review(app, lineage, **args)
        changed = first.model_copy(update={"rationale": "Altered after attestation."})
        letter.write_text(changed.model_dump_json())
        with pytest.raises(PermissionError, match="independently verified"):
            submit_workbench_blinded_review(app, lineage, **args)
        assert _graph_revision(thesis.flow) == opened["revision"]

        letter.write_text(first.model_dump_json(indent=2))
        admitted = submit_workbench_blinded_review(app, lineage, **args)
        assert admitted["panel"]["submitted_reviews"] == 1
        assert admitted["assessment_file_sha256"] == (
            hashlib.sha256(letter.read_bytes()).hexdigest()
        )
        second = _assessment("reviewer-b", subject)
        verified[second.receipt_sha256] = (subject, second)
        letter.write_text(second.model_dump_json(indent=2))
        completed = submit_workbench_blinded_review(
            app,
            lineage,
            **{**args, "expected_revision": admitted["revision"]},
        )
        assert completed["panel"]["status"] == "passed"
        assert "reviewer-a" not in json.dumps(completed)
        assert first.rationale not in json.dumps(completed)
        thesis.aims.record_outcome(
            "independent-review", version=1, **outcome.model_dump(mode="json")
        )
        assert (
            thesis.aims.scientific_context()["research_aims"][0]["status"]
            == "supported"
        )
    finally:
        app.close()
