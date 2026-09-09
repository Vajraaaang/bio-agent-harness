"""Semantic negative controls with valid hashes; real numerical success/failure fixtures."""
import copy
from datetime import datetime, timedelta
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from devharness_core.provenance import ExecutionDAG, canonical_json, verify_execution
from devharness_controlled.executor import execute_plan
from devharness_controlled.qualification import synthetic_case, synthetic_approvals


def verifier():
    assert importlib.util.find_spec("devharness_controlled.verification"), "Controlled semantic verifier is missing"
    return importlib.import_module("devharness_controlled.verification").verify_controlled_execution


@pytest.fixture(scope="module")
def completed(tmp_path_factory):
    root = tmp_path_factory.mktemp("semantic-verification")
    plan, counts, metadata = synthetic_case(root / "input")
    approvals, signers = synthetic_approvals(plan)
    result = execute_plan(plan, approvals, signers, counts_path=counts, metadata_path=metadata, output_dir=root / "run")
    assert result["status"] == "analysis_complete_pending_review", result
    return root / "run", result["receipt"], signers


def altered_history(completed, destination, mutate):
    """Make an internally consistent adversarial history without modifying the real run."""
    source, _, _ = completed
    shutil.copytree(source / "audit", destination)
    manifest = json.loads((destination / "manifest.json").read_text())
    nodes = [json.loads((destination / "nodes" / (n + ".json")).read_text()) for n in manifest["node_ids"]]

    def artifact(name, modify):
        reference = next(x for x in manifest["artifacts"] if x["name"] == name)
        value = json.loads((destination / "objects" / reference["sha256"]).read_text())
        modify(value)
        encoded = canonical_json(value)
        digest = hashlib.sha256(encoded).hexdigest()
        (destination / "objects" / digest).write_bytes(encoded)
        reference.update(sha256=digest, size_bytes=len(encoded))
        if name == "numerical-result.json":
            next(n for n in nodes if n["kind"] == "numerical_execution_returned")["payload"]["result_sha256"] = digest
        return value

    mutate(nodes, manifest, artifact)
    shutil.rmtree(destination / "nodes")
    (destination / "nodes").mkdir()
    ids = []
    for i, node in enumerate(nodes):
        node.pop("node_id", None)
        node.update(sequence=i+1, previous=ids[-1] if ids else None, parents=ids[-1:] if ids else [])
        digest = hashlib.sha256(canonical_json(node)).hexdigest()
        node["node_id"] = digest
        (destination / "nodes" / (digest + ".json")).write_bytes(canonical_json(node))
        ids.append(digest)
    manifest.pop("root_hash")
    manifest.update(node_ids=ids, node_count=len(ids))
    root_hash = hashlib.sha256(canonical_json(manifest)).hexdigest()
    manifest["root_hash"] = root_hash
    for name, data in (("manifest.json", canonical_json(manifest)), ("root.sha256", (root_hash+"\n").encode())):
        path = destination / name
        path.chmod(0o600)
        path.write_bytes(data)
    assert verify_execution(destination / "manifest.json", root_hash)["valid"]
    return destination / "manifest.json", root_hash


def test_cli_default_cannot_accept_an_empty_history(tmp_path):
    ledger = ExecutionDAG(tmp_path / "audit")
    receipt = ledger.finalize()
    path = tmp_path / "receipt.json"; path.write_text(json.dumps(receipt))
    result = subprocess.run([sys.executable, "-m", "devharness_controlled.cli", "verify", "--manifest",
                             receipt["manifest_path"], "--receipt", str(path)], capture_output=True, text=True)
    assert result.returncode != 0, "Empty DAG incorrectly accepted as controlled verification"


def test_real_complete_history_is_bound_and_preserves_scope(completed):
    folder, receipt, signers = completed
    result = verifier()(folder / "audit/manifest.json", receipt["root_hash"], signers)
    assert result["valid"] and result["integrity_valid"] and result["controlled_evidence_valid"], result
    assert result["analysis_completed"] and result["execution_status"] == "analysis_complete_pending_review"
    assert result["approval_signatures_verified"] == 2
    assert result["clinical_release_allowed"] is False and result["compliance_certified"] is False


@pytest.mark.parametrize("mode", ["empty", "completion_only", "missing_approval", "wrong_plan", "wrong_input",
                                  "wrong_result_link", "wrong_worker", "generated_code", "adaptive_selection",
                                  "backward_time", "duplicate_plan", "invalid_signature", "replaced_registry",
                                  "wrong_environment", "missing_source", "wrong_hypotheses", "wrong_df",
                                  "invalid_probability", "wrong_bh", "wrong_normalization", "altered_outcome",
                                  "wrong_library_version", "wrong_returned_formula", "false_test_statistic"])
def test_correct_hashes_do_not_mask_semantic_corruption(completed, tmp_path, mode):
    def modify(nodes, manifest, artifact):
        by_kind = {n["kind"]: n for n in nodes}
        if mode == "empty":
            nodes.clear()
        elif mode == "completion_only":
            nodes[:] = [by_kind["attempt_finished"]]
        elif mode == "missing_approval":
            nodes.remove(by_kind["plan_approvals_verified"])
        elif mode == "wrong_plan":
            by_kind["attempt_started"]["payload"]["plan_sha256"] = "0"*64
        elif mode == "wrong_input":
            by_kind["inputs_preserved"]["payload"]["inputs"]["metadata.csv"]["sha256"] = "0"*64
        elif mode == "wrong_result_link":
            by_kind["numerical_execution_returned"]["payload"]["result_sha256"] = "0"*64
        elif mode == "wrong_worker":
            by_kind["numerical_execution_started"]["payload"]["worker"] = "unreviewed.worker"
        elif mode in {"generated_code", "adaptive_selection"}:
            by_kind["numerical_execution_started"]["payload"].update(
                {"generated_code": True} if mode == "generated_code" else {"selection_criterion": "aic"})
        elif mode == "backward_time":
            by_kind["numerical_execution_returned"]["timestamp_utc"] = "2000-01-01T00:00:00Z"
        elif mode == "duplicate_plan":
            manifest["artifacts"].append(copy.deepcopy(next(a for a in manifest["artifacts"] if a["name"] == "plan.json")))
        elif mode == "invalid_signature":
            artifact("approvals.json", lambda values: values[0].update(signature="A"*86+"=="))
        elif mode == "replaced_registry":
            artifact("trust-registry.json", lambda values: values[0].update(subject_id="someone-else"))
        elif mode == "wrong_environment":
            artifact("environment.json", lambda value: value["environment"].update(python="0.0.0"))
        elif mode == "missing_source":
            manifest["artifacts"] = [a for a in manifest["artifacts"] if a["name"] != "tools_stat.py"]
        elif mode == "altered_outcome":
            artifact("outcome.json", lambda value: value.update(status="failed"))
        else:
            def change_result(value):
                analysis = value["analysis"]
                first = next(iter(analysis["genes"].values()))
                if mode == "wrong_hypotheses": analysis["genes"].pop(next(iter(analysis["genes"])))
                elif mode == "wrong_df": first["selected_df"] = 2
                elif mode == "invalid_probability": first["p_value"] = -0.1
                elif mode == "wrong_bh": first["padj"] = first["q_value"] = 1.0
                elif mode == "wrong_normalization": analysis["normalization_method"] = "median_of_ratios"
                elif mode == "wrong_library_version": analysis["provenance"]["library_versions"]["numpy"] = "0.0.0"
                elif mode == "wrong_returned_formula": first["formula"] = "1"
                elif mode == "false_test_statistic": first["test_statistic"] = 0.0
            artifact("numerical-result.json", change_result)
    manifest, root_hash = altered_history(completed, tmp_path / "changed", modify)
    result = verifier()(manifest, root_hash, completed[2])
    assert result["integrity_valid"] is True
    assert result["valid"] is False and result["controlled_evidence_valid"] is False, mode
    assert result["errors"], mode


def test_operator_trust_and_expected_root_are_mandatory(completed):
    folder, receipt, signers = completed
    assert not verifier()(folder / "audit/manifest.json", receipt["root_hash"], None)["valid"]
    assert not verifier()(folder / "audit/manifest.json", None, signers)["valid"]
    assert not verifier()(folder / "audit/manifest.json", "0"*64, signers)["integrity_valid"]


def test_loose_run_files_are_not_used_as_evidence(completed, tmp_path):
    folder, receipt, signers = completed
    copied = tmp_path / "copy"; shutil.copytree(folder, copied)
    (copied / "plan.json").write_text('{"not":"the preserved plan"}')
    (copied / "outcome.json").write_text('{"status":"fake"}')
    assert verifier()(copied / "audit/manifest.json", receipt["root_hash"], signers)["valid"]


def test_real_rejected_attempt_does_not_become_success(tmp_path):
    plan, counts, metadata = synthetic_case(tmp_path / "input", counts_sha256="0"*64)
    approvals, signers = synthetic_approvals(plan)
    run = execute_plan(plan, approvals, signers, counts_path=counts, metadata_path=metadata, output_dir=tmp_path / "run")
    result = verifier()(tmp_path / "run/audit/manifest.json", run["receipt"]["root_hash"], signers)
    assert result["valid"] and result["execution_status"] == "failed", result
    assert result["analysis_completed"] is False


def test_runtime_emits_separate_controlled_verification(completed):
    folder, receipt, signers = completed
    # The coordinator stores this post-seal report separately from its own root.
    report = folder / "controlled-verification.json"
    assert report.is_file(), "Coordinator still exposes hash verification alone"
    assert json.loads(report.read_text())["controlled_evidence_valid"] is True


def test_real_nb_evidence_is_verified_and_fixed_to_count_panel(tmp_path):
    plan, counts, metadata = synthetic_case(tmp_path / "input", "counts")
    approvals, signers = synthetic_approvals(plan)
    result = execute_plan(plan, approvals, signers, counts_path=counts, metadata_path=metadata, output_dir=tmp_path / "run")
    assert result["status"] == "analysis_complete_pending_review", result
    report = verifier()(tmp_path / "run/audit/manifest.json", result["receipt"]["root_hash"], signers)
    assert report["valid"] and report["analysis_completed"], report


def test_cli_genuine_evidence_requires_explicit_trust(completed):
    folder, receipt, _ = completed
    process = subprocess.run([sys.executable, "-m", "devharness_controlled.cli", "verify", "--manifest",
                              receipt["manifest_path"], "--receipt", str(folder / "receipt.json"),
                              "--trust-registry", str(folder / "trust-registry.json")], capture_output=True, text=True)
    assert process.returncode == 0, process.stderr + process.stdout
    assert json.loads(process.stdout)["controlled_evidence_valid"] is True


@pytest.fixture(scope="module")
def completed_count(tmp_path_factory):
    root = tmp_path_factory.mktemp("semantic-count-schema")
    plan, counts, metadata = synthetic_case(root / "input", "counts")
    approvals, signers = synthetic_approvals(plan)
    result = execute_plan(plan, approvals, signers, counts_path=counts, metadata_path=metadata, output_dir=root / "run")
    assert result["status"] == "analysis_complete_pending_review", result
    return root / "run", result["receipt"], signers


@pytest.mark.parametrize("mode", ["negative_factor", "missing_sample_factor", "unscaled_factors", "nonconverged",
                                  "gene_error", "top_error", "negative_se", "coefficient_string", "coefficient_bool",
                                  "missing_coefficient", "wrong_effect", "wrong_scale", "wrong_time", "unknown_gene_field",
                                  "inconsistent_dispersion"])
def test_invalid_accepted_result_fields_rejected(completed_count, tmp_path, mode):
    def modify(nodes, manifest, artifact):
        def change(result):
            a = result["analysis"]; g = a["genes"]["g000"]
            if mode == "negative_factor": a["size_factors"] = {n:-v for n,v in a["size_factors"].items()}
            elif mode == "missing_sample_factor": a["size_factors"].pop(next(iter(a["size_factors"])))
            elif mode == "unscaled_factors": a["size_factors"] = {n:2*v for n,v in a["size_factors"].items()}
            elif mode == "nonconverged": g["dispersion_converged"] = False
            elif mode == "gene_error": g["diagnostics"].append({"code":"glm_nonconvergence","severity":"error"})
            elif mode == "top_error": a["diagnostics"].append({"code":"fit_failed","severity":"error"})
            elif mode == "negative_se": g["endpoint_contrast_se"] = -1
            elif mode == "coefficient_string": g["coefficients"]["Intercept"] = "not a number"
            elif mode == "coefficient_bool": g["coefficients"]["Intercept"] = True
            elif mode == "missing_coefficient": g["coefficients"].pop("Intercept")
            elif mode == "wrong_effect": g["effect_difference"] += 10
            elif mode == "wrong_scale": g["effect_scale"] = "expression_units"
            elif mode == "wrong_time": g["time_end"] += 1
            elif mode == "unknown_gene_field": g["clinical_validated"] = True
            elif mode == "inconsistent_dispersion": a["dispersion_calibration"]["gene_estimates"]["g000"]["dispersion_alpha"] *= 2
        artifact("numerical-result.json", change)
    manifest, root_hash = altered_history(completed_count, tmp_path / "altered", modify)
    result = verifier()(manifest, root_hash, completed_count[2])
    assert result["integrity_valid"] and not result["valid"], mode


def test_approval_receipt_cannot_precede_signed_issuance(completed, tmp_path):
    def modify(nodes, manifest, artifact):
        event = next(n for n in nodes if n["kind"] == "plan_approvals_verified")
        issued = min(datetime.fromisoformat(r["issued_at"].replace("Z", "+00:00")) for r in event["payload"]["receipts"])
        before = (issued-timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        manifest["created_at_utc"] = before
        nodes[0]["timestamp_utc"] = before
        # Keep ledger identity consistent with the changed created-at timestamp.
        for receipt in event["payload"]["receipts"]: receipt["verified_at"] = before
    # This test needs the identity file resealed to the same earlier creation time.
    original = altered_history
    def change_with_identity(nodes, manifest, artifact):
        modify(nodes, manifest, artifact)
        identity = tmp_path / "altered/ledger.json"
        value = json.loads(identity.read_text()); value["created_at_utc"] = manifest["created_at_utc"]
        identity.chmod(0o600); identity.write_bytes(canonical_json(value))
    manifest, root_hash = original(completed, tmp_path / "altered", change_with_identity)
    result = verifier()(manifest, root_hash, completed[2])
    assert result["integrity_valid"] and not result["valid"], result
