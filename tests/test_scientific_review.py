"""Offline research/reviewer records over real public-fixture evidence."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_analysis_episodes import workspace, run_contrast
from tests.test_episode_proposals import prepared, reviewed


def api():
    from devbench.analysis.scientific_review import prepare_packet, record_scientific_review, verify_packet, verify_scientific_review
    return prepare_packet, record_scientific_review, verify_packet, verify_scientific_review


def research():
    return {"researcher_label": "fixture-curator", "sources": [{"source_id": "S1",
        "title": "DESeq2 author documentation", "url": "https://bioconductor.org/packages/release/bioc/vignettes/DESeq2/inst/doc/DESeq2.html",
        "version": "1.52.0", "access": "documentation", "accessed_on": "2026-09-22",
        "limitations": "Method documentation does not qualify the custom implementation."}],
        "claims": [{"claim_id": "C1", "text": "The experimental unit must be identified before population inference.",
            "basis": "inference", "source_ids": ["S1"], "subject_sections": ["configuration"],
            "applicability": "Declared biological-unit contrast; actual identities need owner evidence.",
            "limitations": "This is a reviewable assertion, not an independently adjudicated conclusion."}]}


def assessment():
    return {"disposition": "needs_information", "rationale": "Owner evidence is not independently authenticated.",
        "findings": [{"claim_id": "C1", "section": "configuration", "status": "needs_information",
                      "reason": "Confirm the experimental unit and target population."}],
        "questions": ["What records establish these biological units?"],
        "limitations": ["Scripted reviewer fixture; scientific review performance is unqualified."]}


def packet(root, *, backed=False):
    prepare, _, _, _ = api()
    if backed:
        from tests.test_workbench_proposals import prepared as backed_prepared
        proposal, _ = backed_prepared(root)
    else:
        proposal, _ = prepared(root)
    receipt = prepare(root / "packet", subject_kind="proposal", subject_path=proposal["manifest_path"],
        expected_root=proposal["root_hash"], research=research(), allow_evidence_read=True)
    return receipt, proposal


@pytest.mark.parametrize("permission", [False, None, 0, 1, "true"])
def test_permissions_deny_before_input_reads(permission):
    prepare, record, verify, _ = api()
    with pytest.raises(PermissionError):
        prepare(None, subject_kind=None, subject_path=None, expected_root=None, research=None, allow_evidence_read=permission)
    with pytest.raises(PermissionError):
        record(None, None, expected_root=None, reviewer_label=None, assessment=None,
            qualification_statement=None, independence_statement=None, allow_review=permission)
    with pytest.raises(PermissionError):
        verify(None, None, allow_evidence_read=permission)


@pytest.mark.parametrize("backed", [False, True])
def test_both_proposal_routes_produce_bounded_research_packets_without_execution(workspace, backed):
    _, _, verify, _ = api()
    receipt, proposal = packet(workspace, backed=backed)
    value = json.loads((workspace / "packet/packet.json").read_text())
    assert value["subject"]["root_hash"] == proposal["root_hash"]
    assert value["research"]["claims"][0]["basis"] == "inference"
    assert value["source_entailment_verified"] is False
    assert value["clinical_release_allowed"] is False
    assert value["data_execution_granted"] is False and value["model_execution_granted"] is False
    assert value["sections"]["configuration"]["question"]
    assert value["sections"]["numerical_results"]["status"] == "not_executed"
    assert verify(receipt["manifest_path"], receipt["root_hash"], allow_evidence_read=True)["valid"]


def test_authored_admission_context_is_verified_and_carried_into_the_packet(workspace):
    from tests.test_episode_planning import dataset, Selector
    from devbench.analysis.episode_planning import propose_analysis
    result = propose_analysis(workspace / "planning", question="Inspect B minus A", dataset=dataset(workspace),
        selector=Selector(), allow_data_read=True)
    proposal = result["proposal_receipt"]
    receipt = api()[0](workspace / "packet", subject_kind="proposal", subject_path=proposal["manifest_path"],
        expected_root=proposal["root_hash"], research=research(), allow_evidence_read=True)
    value = json.loads((workspace / "packet/packet.json").read_text())
    assert value["sections"]["admission"]["format"] == "devbench.proposal-admission/1"
    assert value["sections"]["design"]["identifiability"] == "not_evaluated"
    assert value["subject"]["authoring_root"] == result["authoring_receipt"]["root_hash"]
    assert receipt["root_hash"]


@pytest.mark.parametrize("change", ["unknown_source", "duplicate_source", "unknown_section", "unsafe_url", "authority"])
def test_research_records_reject_broken_links_and_authority_claims(workspace, change):
    proposal, _ = prepared(workspace)
    value = research()
    if change == "unknown_source": value["claims"][0]["source_ids"] = ["missing"]
    elif change == "duplicate_source": value["sources"].append(copy.deepcopy(value["sources"][0]))
    elif change == "unknown_section": value["claims"][0]["subject_sections"] = ["private_golden"]
    elif change == "unsafe_url": value["sources"][0]["url"] = "javascript:alert(1)"
    else: value["scientific_correctness"] = True
    with pytest.raises(ValueError):
        api()[0](workspace / "packet", subject_kind="proposal", subject_path=proposal["manifest_path"],
            expected_root=proposal["root_hash"], research=value, allow_evidence_read=True)
    assert not (workspace / "packet").exists()


def test_scientific_review_binds_exact_packet_and_keeps_qualification_unestablished(workspace):
    _, record, _, verify = api()
    receipt, _ = packet(workspace)
    review = record(workspace / "scientific-review", receipt["manifest_path"], expected_root=receipt["root_hash"],
        reviewer_label="fixture-reviewer", assessment=assessment(), qualification_statement="Not qualified by this fixture",
        independence_statement="Scripted observation only", allow_review=True)
    value = json.loads((workspace / "scientific-review/scientific-review.json").read_text())
    assert value["packet_root"] == receipt["root_hash"]
    assert value["qualification_status"] == "not_established"
    assert value["independence_verified"] is False and value["clinical_release_allowed"] is False
    result = verify(review["manifest_path"], review["root_hash"], packet_manifest=receipt["manifest_path"],
        packet_root=receipt["root_hash"], allow_evidence_read=True)
    assert result["valid"] and result["scientific_validity_established"] is False


def test_packet_and_review_relocate_without_following_original_analysis_paths(workspace):
    import shutil
    _, record, verify_packet, verify_review = api()
    receipt, _ = packet(workspace)
    review = record(workspace / "scientific-review", receipt["manifest_path"], expected_root=receipt["root_hash"],
        reviewer_label="fixture-reviewer", assessment=assessment(), qualification_statement="Unqualified",
        independence_statement="Not established", allow_review=True)
    moved = workspace / "moved"; moved.mkdir()
    for name in ["packet", "scientific-review"]: shutil.move(str(workspace / name), moved / name)
    (workspace / "proposal").rename(workspace / "original-proposal-unavailable")
    (workspace / "source").rename(workspace / "original-data-unavailable")
    packet_path = moved / "packet/audit/manifest.json"
    assert verify_packet(packet_path, receipt["root_hash"], allow_evidence_read=True)["origin_rechecked"] is False
    assert verify_review(moved / "scientific-review/audit/manifest.json", review["root_hash"],
        packet_manifest=packet_path, packet_root=receipt["root_hash"], allow_evidence_read=True)["valid"]


def test_changed_packet_and_wrong_packet_root_are_rejected(workspace):
    receipt, _ = packet(workspace)
    path = workspace / "packet/packet.json"; path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError):
        api()[2](receipt["manifest_path"], receipt["root_hash"], allow_evidence_read=True)


@pytest.mark.parametrize("cellular,continuous", [(False, True), (False, False), (True, False)])
def test_episode_packet_uses_verified_numerical_report_and_keeps_original_seal(workspace, cellular, continuous):
    from devbench.analysis.episode_proposals import begin_reviewed_episode
    from devbench.analysis.episodes import verify_episode
    proposal, _ = prepared(workspace, cellular=cellular, continuous=continuous)
    review = reviewed(workspace, proposal)
    episode = begin_reviewed_episode(workspace / "episode", proposal["manifest_path"], review["manifest_path"],
        expected_proposal_root=proposal["root_hash"], expected_review_root=review["root_hash"], allow_data_execution=True)
    observed = run_contrast(episode, cellular)
    target = observed["result"]["target_results"][0]
    estimated = next(row for row in target["payload"]["gene_statistics"] if row["estimate_status"] == "estimated")
    findings = [{"action": len(episode.actions), "target_id": target["target_id"], "hypothesis_id": estimated["hypothesis_id"]}]
    assert episode.step({"kind": "finish", "findings": findings})["outcome"] == "completed"
    receipt = api()[0](workspace / "packet", subject_kind="episode", subject_path=episode.root,
        expected_root=episode.receipt["root_hash"], research=research(), allow_evidence_read=True)
    value = json.loads((workspace / "packet/packet.json").read_text())
    assert value["sections"]["numerical_results"]["evidence"]["status"] == "verified"
    assert value["sections"]["numerical_results"]["scientific_correctness"] is None
    assert value["sections"]["numerical_results"]["findings"][0]["estimate"] == estimated
    assert value["sections"]["provenance"]["expression"]["sha256"]
    assert value["subject"]["proposal_root"] == proposal["root_hash"]
    assert verify_episode(episode.root, episode.receipt["root_hash"])["valid"]
    assert api()[2](receipt["manifest_path"], receipt["root_hash"], allow_evidence_read=True)["valid"]


def test_existing_cli_can_prepare_packet_without_model_or_data_execution(workspace):
    proposal, _ = prepared(workspace)
    request = {"operation": "prepare_packet", "subject_kind": "proposal", "subject_path": proposal["manifest_path"],
        "expected_root": proposal["root_hash"], "research": research()}
    path = workspace / "request.json"; path.write_text(json.dumps(request))
    args = [sys.executable, "-m", "devbench.analysis.episode_cli", "--evidence-request", str(path),
            "--allow-evidence-read", "--output", str(workspace / "packet")]
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["root_hash"]


def test_review_output_cannot_be_written_inside_its_original_sealed_subject(workspace):
    receipt, _ = packet(workspace)
    with pytest.raises(ValueError, match="disjoint"):
        api()[1](workspace / "proposal/extra-review", receipt["manifest_path"], expected_root=receipt["root_hash"],
            reviewer_label="fixture-reviewer", assessment=assessment(), qualification_statement="Unqualified",
            independence_statement="Not established", allow_review=True)
    assert not (workspace / "proposal/extra-review").exists()


def test_wrong_packet_cannot_reuse_a_review(workspace):
    first, _ = packet(workspace)
    review = api()[1](workspace / "scientific-review", first["manifest_path"], expected_root=first["root_hash"],
        reviewer_label="fixture-reviewer", assessment=assessment(), qualification_statement="Unqualified",
        independence_statement="Not established", allow_review=True)
    other = workspace / "other"; other.mkdir()
    second, _ = packet(other)
    with pytest.raises(ValueError, match="does not bind"):
        api()[3](review["manifest_path"], review["root_hash"], packet_manifest=second["manifest_path"],
            packet_root=second["root_hash"], allow_evidence_read=True)


def test_captured_subject_bytes_must_match_verified_hashes(workspace, monkeypatch):
    from devbench.analysis import scientific_review as module
    path = workspace / "record.json"; path.write_text('{"valid":true}')
    expected = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    original = module._read
    monkeypatch.setattr(module, "_read", lambda target, limit: b'{"valid":false}' if target == path else original(target, limit))
    with pytest.raises(ValueError, match="changed after verification"):
        module._bound_json(workspace, "record.json", expected)


def test_backed_result_packet_preserves_runtime_review_and_original_bundle(workspace):
    from tests.test_workbench_proposals import prepared as prepare_backed, reviewed as review_backed, begin
    from devbench.analysis.workbench_proposals import execute_reviewed_workbench
    from devbench.analysis.bundles import verify_bundle
    proposal, _ = prepare_backed(workspace)
    review = review_backed(workspace, proposal)
    begin(workspace, proposal, review)
    executed = execute_reviewed_workbench(workspace / "bundle", workspace / "execution", allow_data_execution=True)
    assert executed["status"] == "completed_review_passed", executed
    receipt = api()[0](workspace / "packet", subject_kind="workbench", subject_path=workspace / "bundle",
        expected_root=executed["bundle_root"], research=research(), allow_evidence_read=True)
    value = json.loads((workspace / "packet/packet.json").read_text())
    assert value["sections"]["runtime"]
    assert value["sections"]["diagnostics"].get("status") != "not_recorded"
    assert value["subject"]["proposal_root"] == proposal["root_hash"]
    assert value["sections"]["provenance"]["external_input_rechecked"] is False
    assert verify_bundle(workspace / "bundle", executed["bundle_root"])["verified"]
    assert api()[2](receipt["manifest_path"], receipt["root_hash"], allow_evidence_read=True)["valid"]


@pytest.mark.parametrize("field", ["access", "basis"])
def test_malformed_research_enum_values_are_controlled_validation_errors(workspace, field):
    proposal, _ = prepared(workspace)
    value = research()
    value["sources" if field == "access" else "claims"][0][field] = []
    with pytest.raises(ValueError):
        api()[0](workspace / "packet", subject_kind="proposal", subject_path=proposal["manifest_path"],
            expected_root=proposal["root_hash"], research=value, allow_evidence_read=True)
    assert not (workspace / "packet").exists()


@pytest.mark.parametrize("field", ["disposition", "claim_id", "section", "status"])
def test_malformed_scientific_review_values_are_controlled_validation_errors(workspace, field):
    receipt, _ = packet(workspace)
    value = assessment()
    (value if field == "disposition" else value["findings"][0])[field] = []
    with pytest.raises(ValueError):
        api()[1](workspace / "scientific-review", receipt["manifest_path"], expected_root=receipt["root_hash"],
            reviewer_label="fixture-reviewer", assessment=value, qualification_statement="Unqualified",
            independence_statement="Not established", allow_review=True)
    assert not (workspace / "scientific-review").exists()


@pytest.mark.parametrize("permission", ["--allow-data-execution", "--allow-model-execution"])
def test_evidence_cli_rejects_execution_permissions_before_reading_request(workspace, permission):
    command = [sys.executable, "-m", "devbench.analysis.episode_cli", "--evidence-request", str(workspace / "absent.json"),
        "--allow-evidence-read", permission, "--output", str(workspace / "output")]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2 and "Evidence requests exclude" in result.stderr
    assert not (workspace / "output").exists()
