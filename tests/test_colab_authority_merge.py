"""Static merge preservation checks; no model or numerical execution."""
import ast
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "reports/rna-colab-integration/2026-10-22-v1/merge-audit/authorities.json"


def functions(source):
    tree = ast.parse(source)
    result = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            result.update({node.name + "." + member.name: member for member in node.body
                if isinstance(member, ast.FunctionDef)})
    return result


def fingerprint(node):
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()


def test_existing_authority_and_signing_functions_survive_delta_merge():
    audit = json.loads(AUDIT.read_text())
    assert audit["merge_kind"] == "user_approved_audited_colab_only_delta"
    assert audit["common_base_available"] is False
    for name, record in audit["files"].items():
        source = (ROOT / name).read_bytes()
        assert hashlib.sha256(source).hexdigest() == record["after_sha256"]
        current = functions(source.decode())
        for symbol, expected in record["preserved_function_ast_sha256"].items():
            assert fingerprint(current[symbol]) == expected, symbol


def test_appended_colab_functions_match_reviewed_staged_bytes():
    audit = json.loads(AUDIT.read_text())
    stage = ROOT / "reports/rna-colab-integration/2026-10-22-v1/candidate"
    for name, record in audit["files"].items():
        current, staged = functions((ROOT / name).read_text()), functions((stage / name).read_text())
        for symbol in record["appended_functions"]:
            assert fingerprint(current[symbol]) == fingerprint(staged[symbol])


def test_custody_union_preserves_existing_helpers_and_new_route():
    from devbench.analysis.rna_trial_authority import _CUSTODY_PATHS
    audit = json.loads(AUDIT.read_text())
    assert len(_CUSTODY_PATHS) == len(set(_CUSTODY_PATHS))
    assert set(audit["custody_paths_before"]) <= set(_CUSTODY_PATHS)
    assert set(audit["custody_paths_staged"]) <= set(_CUSTODY_PATHS)
    assert {"devbench/analysis/workflow_protocol_qualification.py",
        "devbench/analysis/colab_workflow.py"} <= set(_CUSTODY_PATHS)


def test_optional_colab_context_does_not_replace_existing_admission():
    from devbench.analysis.rna_trial_authority import AuthenticatedTrialAuthority
    import inspect
    signature = inspect.signature(AuthenticatedTrialAuthority)
    assert signature.parameters["colab_qualification_context"].default is None
    assert "runtime_verifier" in signature.parameters
    assert "execution_qualification" in signature.parameters
