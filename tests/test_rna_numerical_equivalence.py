"""P4 contract tests: synthetic numerical equivalence is not scientific validation."""
from copy import deepcopy
import csv
import hashlib
import importlib
import io
import json

import pytest

from devbench.analysis.bundles import import_bundle, pack_bundle, seal_bundle, verify_bundle
from devbench.analysis.checkpoints import digest, sha256


SCOPE = "numerical_equivalence_not_scientific_validation"
CONTRACT_PATH = "numerical-equivalence-contract.json"


def _api():
    try:
        module = importlib.import_module("devbench.analysis.numerical_equivalence")
    except ModuleNotFoundError as error:
        if error.name != "devbench.analysis.numerical_equivalence":
            raise
        pytest.fail("P4 compare_bundle_results API has not been implemented")
    return module.compare_bundle_results


def contract(*, kind="csv", alignment="identifier", historical=False):
    return {
        "format": "devbench.analysis.numerical-equivalence-contract/1",
        "declaration": {
            "timing": "historical_comparison" if historical else "recorded_preparation_binding",
            "artifact": None if historical else CONTRACT_PATH,
        },
        "results": [{
            "name": "gene_statistics", "path": "statistics.csv" if kind == "csv" else "statistics.json",
            "kind": kind, "identifiers": ["gene_id"], "alignment": alignment,
            "numeric": {"log2FC": {"atol": 1e-8, "rtol": 1e-6},
                        "padj": {"atol": 1e-8, "rtol": 1e-6}},
            "categorical": ["direction"],
        }],
        "limits": {"max_artifact_bytes": 100_000, "max_rows": 100},
    }


def rows():
    return [{"gene_id": "001", "log2FC": 1.0, "padj": 0.01, "direction": "up"},
            {"gene_id": "1", "log2FC": -2.0, "padj": None, "direction": "down"}]


def _json(path, value):
    path.write_text(json.dumps(value, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def _statistics(root, declared, values, *, pretty=False):
    spec = declared["results"][0]
    output = root / spec["path"]
    if spec["kind"] == "csv":
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=["gene_id", "log2FC", "padj", "direction"],
                                lineterminator="\r\n" if pretty else "\n")
        writer.writeheader()
        for row in values:
            writer.writerow({key: ("" if value is None else value) for key, value in row.items()})
        output.write_bytes(buffer.getvalue().encode())
    else:
        output.write_text(json.dumps(values, allow_nan=False, indent=2 if pretty else None), encoding="utf-8")


def _prepared(root, declared, *, parameters=None, runtime=None, source=b"synthetic-source-v1",
              recorded=True, data_sha256=None):
    root.mkdir()
    (root / "source.zip").write_bytes(source)
    params = {"execution_tier": "local_supervised", "data_sha256": data_sha256 or "a" * 64,
              "source_sha256": sha256(root / "source.zip"), "seed": 17,
              "scientific_question": "Synthetic numerical equivalence control"}
    params.update(parameters or {})
    if declared["declaration"]["timing"] == "recorded_preparation_binding":
        _json(root / CONTRACT_PATH, declared)
        if recorded:
            params["numerical_equivalence_contract_sha256"] = digest(declared)
    _json(root / "parameters.json", params)
    runtime_record = runtime or {"runtime": {"python": "3.12.9", "packages": {"numpy": "2.1.0"},
                                            "all_package_versions": {"numpy": "2.1.0"}}}
    identity = {"parameters": params, "data_sha256": params["data_sha256"],
                "source_sha256": params["source_sha256"],
                "python_minor": [int(value) for value in runtime_record["runtime"]["python"].split()[0].split(".")[:2]],
                "packages": runtime_record["runtime"]["packages"]}
    if recorded and "numerical_equivalence_contract_sha256" in params:
        identity["numerical_equivalence_contract_sha256"] = digest(declared)
    _json(root / "checkpoint-identity.json", identity)
    _json(root / "runtime.json", runtime_record)
    return root


def _bundle(root, declared, values=None, *, pretty=False, status="completed_review_passed", **kwargs):
    _prepared(root, declared, **kwargs)
    _statistics(root, declared, rows() if values is None else values, pretty=pretty)
    receipt = seal_bundle(root, status=status)
    verify_bundle(root, receipt["manifest_sha256"])
    return root, receipt["manifest_sha256"]


def _pair(tmp_path, declared, left=None, right=None, *, left_options=None, right_options=None):
    reference, reference_root = _bundle(tmp_path / "reference", declared, left, **(left_options or {}))
    replay, replay_root = _bundle(tmp_path / "replay", declared, right,
                                  parameters={"parent_root": reference_root}, **(right_options or {}))
    return reference, replay, reference_root, replay_root


def _compare(pair, declared):
    reference, replay, reference_root, replay_root = pair
    return _api()(reference, replay, expected_reference_root=reference_root,
                  expected_replay_root=replay_root, contract=declared)


@pytest.mark.parametrize("kind", ["csv", "json_records"])
def test_equivalence_is_separate_from_artifact_bytes_and_binds_real_roots(tmp_path, kind):
    declared = contract(kind=kind)
    pair = _pair(tmp_path, declared, right_options={"pretty": True})
    reference, replay, reference_root, replay_root = pair
    path = declared["results"][0]["path"]
    assert (reference / path).read_bytes() != (replay / path).read_bytes()
    result = _compare(pair, declared)
    assert result["status"] == "equivalent" and result["equivalent"] is True
    assert result["scope"] == SCOPE and result["prespecified"] is True
    assert result["contract_sha256"] == digest(declared)
    assert result["reference_root_sha256"] == reference_root
    assert result["replay_root_sha256"] == replay_root
    assert reference_root != replay_root
    json.dumps(result, allow_nan=False)


def test_identifier_alignment_preserves_001_distinct_from_1(tmp_path):
    declared = contract()
    result = _compare(_pair(tmp_path, declared, right=list(reversed(rows()))), declared)
    assert result["status"] == "equivalent"


def test_ordered_alignment_rejects_a_permutation(tmp_path):
    declared = contract(alignment="ordered")
    result = _compare(_pair(tmp_path, declared, right=list(reversed(rows()))), declared)
    assert result["status"] == "mismatch"


@pytest.mark.parametrize("change", ["duplicate", "missing", "extra", "blank", "coerced_identifier"])
def test_ambiguous_or_different_identifier_sets_are_not_equivalent(tmp_path, change):
    declared, changed = contract(), rows()
    if change == "duplicate":
        changed[1]["gene_id"] = changed[0]["gene_id"]
    elif change == "missing":
        changed.pop()
    elif change == "extra":
        changed.append({**changed[0], "gene_id": "extra"})
    elif change == "blank":
        changed[1]["gene_id"] = ""
    else:
        changed[0]["gene_id"] = "01"
    result = _compare(_pair(tmp_path, declared, right=changed), declared)
    assert result["status"] in {"mismatch", "not_comparable"}
    assert result["equivalent"] is not True


@pytest.mark.parametrize("statistic", ["log2FC", "padj"])
def test_missing_mask_is_checked_per_statistic_and_zero_is_not_missing(tmp_path, statistic):
    declared, original = contract(), rows()
    original[1][statistic] = None
    changed = deepcopy(original)
    changed[1][statistic] = 0.0
    result = _compare(_pair(tmp_path, declared, original, changed), declared)
    assert result["status"] == "mismatch" and result["equivalent"] is False


@pytest.mark.parametrize("delta,expected", [(0.0, "equivalent"), (1e-4, "equivalent"), (0.000100001, "mismatch")])
def test_explicit_absolute_tolerance_boundary(tmp_path, delta, expected):
    declared, original = contract(), rows()
    declared["results"][0]["numeric"]["log2FC"] = {"atol": 1e-4, "rtol": 0.0}
    original[0]["log2FC"] = 0.0
    changed = deepcopy(original)
    changed[0]["log2FC"] = delta
    assert _compare(_pair(tmp_path, declared, original, changed), declared)["status"] == expected


def test_relative_tolerance_is_not_an_absolute_rounding_rule(tmp_path):
    declared, original = contract(), rows()
    declared["results"][0]["numeric"]["log2FC"] = {"atol": 0.0, "rtol": 1e-6}
    original[0]["log2FC"] = 1e6
    changed = deepcopy(original)
    changed[0]["log2FC"] += 0.5
    assert _compare(_pair(tmp_path, declared, original, changed), declared)["status"] == "equivalent"


def test_approval_of_tolerance_does_not_hide_a_decision_threshold_crossing(tmp_path):
    declared, original = contract(), rows()
    declared["results"][0]["numeric"]["padj"] = {
        "atol": 1e-3, "rtol": 0.0, "decision": {"operator": "<", "threshold": 0.05}}
    original[0]["padj"] = 0.0499
    changed = deepcopy(original)
    changed[0]["padj"] = 0.0501
    assert _compare(_pair(tmp_path, declared, original, changed), declared)["status"] == "mismatch"


def test_declared_categories_are_exact_even_when_numbers_match(tmp_path):
    declared, changed = contract(), rows()
    changed[0]["direction"] = "down"
    assert _compare(_pair(tmp_path, declared, right=changed), declared)["status"] == "mismatch"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e309", "not-a-number"])
def test_invalid_numeric_cells_are_not_comparable(tmp_path, value):
    declared, changed = contract(), rows()
    changed[0]["log2FC"] = value
    result = _compare(_pair(tmp_path, declared, right=changed), declared)
    assert result["status"] == "not_comparable" and result["equivalent"] is None
    json.dumps(result, allow_nan=False)


def test_finite_extremes_cannot_pass_via_overflow(tmp_path):
    declared, original = contract(), rows()
    original[0]["log2FC"] = 1e308
    changed = deepcopy(original)
    changed[0]["log2FC"] = -1e308
    result = _compare(_pair(tmp_path, declared, original, changed), declared)
    assert result["status"] == "mismatch" and result["equivalent"] is False
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("change", ["unknown", "version", "negative_tolerance", "bool_tolerance", "infinite_tolerance",
                                   "unsafe_path", "duplicate_result", "empty_numeric", "unbounded_rows"])
def test_contract_is_strict_versioned_finite_and_bounded(tmp_path, change):
    declared = contract()
    pair = _pair(tmp_path, declared)
    invalid = deepcopy(declared)
    result = invalid["results"][0]
    if change == "unknown":
        invalid["trust_me"] = True
    elif change == "version":
        invalid["format"] = "devbench.analysis.numerical-equivalence-contract/999"
    elif change == "negative_tolerance":
        result["numeric"]["padj"]["atol"] = -1.0
    elif change == "bool_tolerance":
        result["numeric"]["padj"]["atol"] = True
    elif change == "infinite_tolerance":
        result["numeric"]["padj"]["rtol"] = float("inf")
    elif change == "unsafe_path":
        result["path"] = "../statistics.csv"
    elif change == "duplicate_result":
        invalid["results"].append(deepcopy(result))
    elif change == "empty_numeric":
        result["numeric"] = {}
    else:
        invalid["limits"]["max_rows"] = 0
    with pytest.raises(ValueError):
        _compare(pair, invalid)


@pytest.mark.parametrize("drift", ["input", "source", "parameters", "runtime"])
def test_semantic_identity_and_environment_drift_prevent_comparison(tmp_path, drift):
    declared = contract()
    left, left_root = _bundle(tmp_path / "reference", declared)
    options = {"parameters": {"parent_root": left_root}}
    if drift == "input":
        options["data_sha256"] = "b" * 64
    elif drift == "source":
        options["source"] = b"different-source"
    elif drift == "parameters":
        options["parameters"]["seed"] = 18
    else:
        options["runtime"] = {"runtime": {"python": "3.12.9", "packages": {"numpy": "2.2.0"},
                                           "all_package_versions": {"numpy": "2.2.0"}}}
    right, right_root = _bundle(tmp_path / "replay", declared, **options)
    result = _compare((left, right, left_root, right_root), declared)
    assert result["status"] == "not_comparable" and result["equivalent"] is None


def test_custody_verification_precedes_parsing_and_no_new_receipt_can_hide_tampering(tmp_path):
    declared = contract()
    pair = _pair(tmp_path, declared)
    (pair[1] / "statistics.csv").write_text("malformed changed output", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact|mismatch"):
        _compare(pair, declared)


@pytest.mark.parametrize("limit", ["max_rows", "max_artifact_bytes"])
def test_declared_read_budget_is_enforced(tmp_path, limit):
    declared = contract(historical=True)
    declared["limits"][limit] = 1
    result = _compare(_pair(tmp_path, declared), declared)
    assert result["status"] == "not_comparable"


@pytest.mark.parametrize("case", ["failed_status", "missing_artifact"])
def test_failed_or_incomplete_replay_never_produces_equivalence(tmp_path, case):
    declared = contract()
    left, left_root = _bundle(tmp_path / "reference", declared)
    right = _prepared(tmp_path / "replay", declared, parameters={"parent_root": left_root})
    if case == "failed_status":
        _statistics(right, declared, rows())
        _json(right / "failure.json", {"status": "failed", "reason": "synthetic missing dependency"})
    status = "failed" if case == "failed_status" else "completed_review_passed"
    right_root = seal_bundle(right, status=status)["manifest_sha256"]
    result = _compare((left, right, left_root, right_root), declared)
    assert result["status"] == "not_comparable" and result["equivalent"] is None
    if case == "failed_status":
        assert json.loads((right / "failure.json").read_text())["status"] == "failed"


def test_a_final_contract_file_does_not_establish_preparation_binding(tmp_path):
    declared = contract()
    pair = _pair(tmp_path, declared, right_options={"recorded": False})
    result = _compare(pair, declared)
    assert result["status"] == "not_comparable" and result["prespecified"] is False


def test_historical_comparison_is_explicitly_not_prespecified(tmp_path):
    declared = contract(historical=True)
    result = _compare(_pair(tmp_path, declared), declared)
    assert result["status"] == "equivalent" and result["prespecified"] is False


def _fingerprint(root):
    return {str(path.relative_to(root)): (path.stat().st_mode, path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest()) for path in sorted(root.rglob("*")) if path.is_file()}


def test_comparator_is_readonly_and_cannot_execute_or_call_a_model(tmp_path, monkeypatch):
    import socket
    import subprocess
    from devbench.analysis import workbench
    from devharness_server.dispatcher import ToolDispatcher
    declared = contract()
    pair = _pair(tmp_path, declared)
    compare = _api()
    before = _fingerprint(tmp_path)
    effects = []

    def forbidden(*args, **kwargs):
        effects.append((args, kwargs))
        raise AssertionError("Numerical comparator attempted execution")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(workbench, "execute_bundle", forbidden)
    monkeypatch.setattr(ToolDispatcher, "execute", forbidden)
    result = compare(pair[0], pair[1], expected_reference_root=pair[2], expected_replay_root=pair[3], contract=declared)
    assert result["status"] == "equivalent" and effects == []
    assert _fingerprint(tmp_path) == before


def test_clean_relocated_notebook_replay_uses_preserved_inputs_and_new_history(tmp_path):
    """Fresh tiny arithmetic only; no RNA job, private input or external model."""
    compare = _api()
    pytest.importorskip("nbclient")
    pytest.importorskip("ipykernel")
    from devbench.analysis.notebooks import execute_notebook, write_notebook
    from devbench.analysis.runtime import observe_runtime
    declared = contract()
    observed_runtime = observe_runtime("local_supervised", detect_gpu=False)
    data = tmp_path / "mutable-input.json"
    _json(data, {"values": [2, 3]})
    original = _prepared(tmp_path / "original", declared, data_sha256=sha256(data), runtime=observed_runtime)
    _json(original / "bindings.json", {"data": str(data)})
    code = "\n".join([
        "import csv, json", "from pathlib import Path", "root = Path.cwd().resolve()",
        "bindings = json.loads((root / 'bindings.json').read_text())",
        "data = json.loads(Path(bindings['data']).read_text())",
        "with (root / 'statistics.csv').open('w', newline='') as handle:",
        "    writer = csv.DictWriter(handle, fieldnames=['gene_id', 'log2FC', 'padj', 'direction'])",
        "    writer.writeheader()",
        "    writer.writerow({'gene_id': '001', 'log2FC': data['values'][0] * data['values'][1], 'padj': 0.01, 'direction': 'up'})",
    ])
    write_notebook(original / "notebook.template.ipynb", [code])
    execute_notebook(original / "notebook.template.ipynb", original / "notebook.ipynb", cell_timeout=30)
    original_root = seal_bundle(original, status="completed_review_passed")["manifest_sha256"]
    archive = tmp_path / "preserved.zip"
    pack_bundle(original, archive, original_root)
    relocated_data = tmp_path / "relocated-input.json"
    relocated_data.write_bytes(data.read_bytes())
    data.rename(tmp_path / "unavailable-original-input.json")
    imported = tmp_path / "imported"
    import_bundle(archive, imported, original_root, data_path=relocated_data)
    original.rename(tmp_path / "unavailable-original-workspace")
    replay = _prepared(tmp_path / "fresh-replay", declared, data_sha256=sha256(relocated_data),
                       parameters={"parent_root": original_root}, runtime=observed_runtime)
    _json(replay / "bindings.json", {"data": str(relocated_data)})
    (replay / "notebook.template.ipynb").write_bytes((imported / "notebook.template.ipynb").read_bytes())
    before = _fingerprint(imported)
    execute_notebook(replay / "notebook.template.ipynb", replay / "notebook.ipynb", cell_timeout=30)
    replay_root = seal_bundle(replay, status="completed_review_passed")["manifest_sha256"]
    result = compare(imported, replay, expected_reference_root=original_root,
                     expected_replay_root=replay_root, contract=declared)
    assert result["status"] == "equivalent" and result["prespecified"] is True
    assert original_root != replay_root and _fingerprint(imported) == before
    assert not original.exists() and not data.exists()
