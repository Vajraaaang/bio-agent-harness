"""Local verification, anchored input restoration and numerical tool replay.

Never executes captured agent text, shell commands, model adapters or network
requests. Replay is a comparison of captured numerical tool calls, not a claim
that a model-generated analysis or every future execution is deterministic.
"""

import argparse
import ast
import hashlib
import json
import math
import os
import re
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Optional, Union

from devharness_core.provenance import canonical_json, hash_artifact, verify_execution, _open_regular, _stream_digest

PathLike = Union[str, Path]
_CHUNK_BYTES = 1_048_576
_MAX_JSON_BYTES = 16_000_000
_ROOT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REPORT_NAME = ".devbench-replay.json"
_NUMERICAL_TOOLS = frozenset({
    "validate_design_matrix", "fit_temporal_trajectory", "calculate_temporal_monotonicity",
    "compact_temporal_state", "analyze_batch_effects", "infer_lineage", "audit_expression_quality",
    "infer_regulatory_network", "regulon_enrichment", "motif_enrichment",
})
_TIMING_KEYS = frozenset({"elapsed_sec", "elapsed_seconds", "duration_sec", "duration_seconds",
                          "runtime_sec", "runtime_seconds", "started_at_utc", "finished_at_utc"})


class AuditError(ValueError):
    """Evidence cannot safely support the requested audit operation."""


class StudyReplayOutOfScope(AuditError):
    """Study receipt verification is separate from ExecutionDAG replay."""


def _absolute(value: PathLike) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise AuditError("Filesystem paths must be absolute")
    if path.is_symlink():
        raise AuditError("Symlink paths are not permitted")
    return path.resolve()


def _require(condition, message):
    if not condition:
        raise AuditError(message)


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "Duplicate JSON object key")
        result[key] = value
    return result


def _read_json(path: Path):
    with _open_regular(path) as source:
        data = source.read(_MAX_JSON_BYTES + 1)
    _require(len(data) <= _MAX_JSON_BYTES, "Audit JSON exceeds the 16 MB bound")
    value = json.loads(data, object_pairs_hook=_unique_keys)
    canonical_json(value)
    return value


def _verified_bundle(manifest_path: PathLike, expected_root_hash: str) -> dict:
    _require(isinstance(expected_root_hash, str) and _ROOT_PATTERN.fullmatch(expected_root_hash),
             "A separately retained SHA-256 root is required")
    path = _absolute(manifest_path)
    declared = _read_json(path)
    if isinstance(declared, dict) and declared.get("record_format") in {
            "devbench.study.tool-receipts/1", "devbench.study.tool-receipts/2"}:
        raise StudyReplayOutOfScope(
            "Study tool receipts are an integrity-only contract. Use devbench-study verify-tools; "
            "numerical replay of study receipts is out of scope.")
    report = verify_execution(path, expected_root_hash)
    _require(report["valid"] and report["anchored"], "Evidence verification failed: " + "; ".join(report["errors"]))
    manifest = _read_json(path)
    unsigned = {key: value for key, value in manifest.items() if key != "root_hash"}
    _require(hashlib.sha256(canonical_json(unsigned)).hexdigest() == expected_root_hash,
             "Manifest changed after verification")
    nodes = []
    for node_id in manifest["node_ids"]:
        node = _read_json(path.parent / "nodes" / (node_id + ".json"))
        content = {key: value for key, value in node.items() if key != "node_id"}
        _require(hashlib.sha256(canonical_json(content)).hexdigest() == node_id,
                 "Node changed after verification")
        nodes.append(node)
    stages = [node["payload"] for node in nodes if node["kind"] == "inputs_staged"]
    _require(len(stages) == 1, "Exactly one captured inputs_staged record is required")
    stage = stages[0]
    references = stage.get("artifacts")
    _require(type(references) is dict and bool(references), "No staged artifact references are available")
    registered = {canonical_json(reference) for reference in manifest["artifacts"]}
    for name, reference in references.items():
        _require(_safe_member(name) and "/" not in name, "Unsafe staged artifact name")
        _require(type(reference) is dict and canonical_json(reference) in registered,
                 "Staged artifact reference was not registered in the manifest")
        _require(reference.get("name") == name, "Staged artifact name does not match its registered reference")
    return {"manifest_path": path, "manifest": manifest, "nodes": nodes, "stage": stage,
            "references": references, "root_hash": expected_root_hash}


def _safe_member(value) -> bool:
    if not isinstance(value, str) or not value or value == "." or "\\" in value or "\x00" in value:
        return False
    name = PurePosixPath(value)
    return (not name.is_absolute() and value == name.as_posix()
            and all(part not in (".", "..") for part in name.parts))


def _output_directory(value: PathLike, ledger_root: Path) -> Path:
    output = _absolute(value)
    _require(not output.is_relative_to(ledger_root) and not ledger_root.is_relative_to(output),
             "Restore destination must not overlap the execution ledger")
    if output.exists():
        _require(output.is_dir() and not any(output.iterdir()), "Restore destination must be a new or empty directory")
    return output


def _copy_blob(blob: Path, destination: Path, root: Path, digest: str, expected_size: int) -> None:
    _require(destination.is_relative_to(root), "Restore destination escapes output directory")
    _require(type(expected_size) is int and expected_size >= 0, "Invalid captured artifact size")
    for parent in (destination.parent, *destination.parent.parents):
        if not parent.is_relative_to(root):
            break
        _require(not parent.is_symlink(), "Symlink restore directory is not permitted")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(str(destination))
    # Publish only a fully verified file. Failure cleanup touches this private
    # directory, never a destination another writer may have created meanwhile.
    with TemporaryDirectory(prefix=".devbench-restore-", dir=destination.parent) as private_directory:
        temporary = Path(private_directory) / "payload"
        descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o400)
        with os.fdopen(descriptor, "wb") as target:
            observed, size = _stream_digest(blob, target, max_bytes=expected_size)
            target.flush()
            os.fsync(target.fileno())
        _require(observed == digest and size == expected_size,
                 "Artifact changed while being restored")
        os.link(temporary, destination, follow_symlinks=False)


def _restore(bundle: dict, output: Path) -> dict:
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    _require(not output.is_symlink() and not any(output.iterdir()), "Restore destination must remain empty")
    root, restored = bundle["manifest_path"].parent, {}
    for name, reference in bundle["references"].items():
        target = output / name
        if reference["kind"] == "file":
            _copy_blob(root / "objects" / reference["sha256"], target, output,
                       reference["sha256"], reference["size_bytes"])
        else:
            target.mkdir(mode=0o700)
            for entry in reference["entries"]:
                _require(_safe_member(entry["name"]), "Unsafe artifact member name")
                member = target.joinpath(*PurePosixPath(entry["name"]).parts)
                if entry["kind"] == "directory":
                    member.mkdir(mode=0o700)
                else:
                    _copy_blob(root / "objects" / entry["sha256"], member, output,
                               entry["sha256"], entry["size_bytes"])
        _require(hash_artifact(target)["sha256"] == reference["sha256"], "Restored artifact hash mismatch")
        restored[name] = str(target)
    return {"restored": True, "anchored": True, "root_hash": bundle["root_hash"],
            "output_dir": str(output), "artifacts": restored,
            "scope": "captured_staged_inputs", "compliance_certified": False}


def restore_execution(manifest_path: PathLike, expected_root_hash: str, output_dir: PathLike) -> dict:
    """Restore verified staged inputs into a new/empty absolute directory.

    The supplied root must come from an independently trusted source. Files are
    created exclusively; existing data and the original ledger are never replaced.
    An interrupted restore may leave incomplete files in its new output directory.
    """
    try:
        bundle = _verified_bundle(manifest_path, expected_root_hash)
        output = _output_directory(output_dir, bundle["manifest_path"].parent)
        return _restore(bundle, output)
    except AuditError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise AuditError(str(error)) from error


def _runtime_matches(bundle: dict) -> tuple:
    from devbench_engine.runner import _code_fingerprint, _environment
    starts = [node["payload"] for node in bundle["nodes"] if node["kind"] == "run_started"]
    _require(len(starts) == 1, "Exactly one run_started record is required for numerical replay")
    recorded = starts[0]
    current = {"code_hash": _code_fingerprint(), "environment": _environment()}
    mismatches = []
    if recorded.get("code_hash") != current["code_hash"]:
        mismatches.append("Current source code fingerprint differs from the recorded execution")
    if canonical_json(recorded.get("environment")) != canonical_json(current["environment"]):
        mismatches.append("Current Python, package, platform or numerical thread environment differs from the recorded execution")
    return current, mismatches


def _logical_members(references: dict) -> set:
    members = set(references)
    for name, reference in references.items():
        if reference["kind"] == "directory":
            members.update(name + "/" + entry["name"] for entry in reference["entries"])
    return members


def _remap_arguments(value, workspace, output: Path, members: set, mapping: dict, key=""):
    if key.endswith("_path") and isinstance(value, str):
        _require(isinstance(workspace, str) and Path(workspace).is_absolute(),
                 "Path-bearing tool calls require the original staged workspace binding")
        original, source_root = PurePosixPath(value), PurePosixPath(workspace)
        _require(original.is_absolute() and ".." not in original.parts and ".." not in source_root.parts,
                 "Captured tool path must be an unambiguous absolute path")
        _require(original.is_relative_to(source_root), "Uncaptured external tool path cannot be replayed")
        relative = original.relative_to(source_root).as_posix()
        _require(relative in members, "Tool path is not a captured staged input: " + relative)
        restored = str(output.joinpath(*PurePosixPath(relative).parts))
        mapping[value] = restored
        return restored
    if type(value) is dict:
        return {name: _remap_arguments(child, workspace, output, members, mapping, name)
                for name, child in value.items()}
    if type(value) is list:
        return [_remap_arguments(child, workspace, output, members, mapping, key) for child in value]
    return value


def _normalize_paths(value: str, roots: list) -> str:
    for root in sorted(set(roots), key=len, reverse=True):
        if root:
            value = re.sub(re.escape(root) + r"(?=/|['\"\s]|$)", "<staged-workspace>", value)
    return value


def _normalize_error_instance(value: str) -> str:
    """Preserve JSON Schema error content while canonicalizing its instance display.

    JSON Schema pretty-prints dictionaries in insertion order. The archive stores
    canonical JSON, so reconstructing the same arguments can change only that
    display order. Literal parsing is non-executable and every value is retained.
    Other exception text is compared unchanged apart from known workspace paths.
    """
    if not value.startswith("ValidationError:"):
        return value
    match = re.search(r"\nOn instance(?:\[[^\n]*\])?:\s*\n", value)
    if match is None:
        return value
    try:
        instance = ast.literal_eval(value[match.end():].strip())
        return value[:match.end()] + canonical_json(instance).decode("utf-8")
    except (ValueError, SyntaxError, TypeError, RecursionError):
        return value


def _compare(expected, actual, *, atol: float, rtol: float, roots: list) -> dict:
    mismatches, ignored = [], []
    counts = {"numeric_values_compared": 0, "mismatch_count": 0}
    def mismatch(path, reason):
        counts["mismatch_count"] += 1
        if len(mismatches) < 100:
            mismatches.append({"field": path, "reason": reason})
    def compare(left, right, path):
        if type(left) is dict and type(right) is dict:
            left_keys, right_keys = set(left) - _TIMING_KEYS, set(right) - _TIMING_KEYS
            for key in sorted((set(left) | set(right)) & _TIMING_KEYS):
                if len(ignored) < 100:
                    ignored.append(path + "." + key)
            for key in sorted(left_keys - right_keys):
                mismatch(path + "." + key, "Missing result field")
            for key in sorted(right_keys - left_keys):
                mismatch(path + "." + key, "Unexpected result field")
            for key in sorted(left_keys & right_keys):
                compare(left[key], right[key], path + "." + key)
        elif type(left) is list and type(right) is list:
            if len(left) != len(right):
                mismatch(path, "Result list length changed")
            for index, (first, second) in enumerate(zip(left, right)):
                compare(first, second, f"{path}[{index}]")
        elif type(left) in (int, float) and type(right) in (int, float):
            counts["numeric_values_compared"] += 1
            if type(left) is int and type(right) is int:
                close = left == right
            else:
                try:
                    close = (math.isfinite(left) and math.isfinite(right)
                             and abs(right - left) <= atol + rtol * abs(left))
                except (OverflowError, TypeError):
                    close = False
            if not close:
                mismatch(path, "Numerical result differs beyond tolerance or is nonfinite")
        elif type(left) is str and type(right) is str:
            if _normalize_paths(left, roots) != _normalize_paths(right, roots):
                mismatch(path, "String result changed")
        elif type(left) is not type(right) or left != right:
            mismatch(path, "Result type or value changed")
    compare(expected, actual, "$result")
    return {"passed": counts["mismatch_count"] == 0, **counts, "mismatches": mismatches,
            "ignored_timing_fields": ignored}


def replay_execution(manifest_path: PathLike, expected_root_hash: str, output_dir: PathLike,
                     *, atol: float = 1e-8, rtol: float = 1e-8) -> dict:
    """Restore anchored inputs and compare captured numerical ToolDispatcher calls.

    Requires identical recorded code and environment descriptors. Path-bearing
    calls additionally require the original workspace in inputs_staged so an
    external same-basename file cannot be substituted. Expected failures are
    replayed and compared, including their error strings. No model is invoked.
    """
    report = {"passed": False, "status": "unreplayable", "errors": [], "comparisons": [],
              "runtime_match": None, "replayed_tool_count": 0, "model_calls": 0,
              "runtime_match_scope": "recorded source and Python environment descriptors; not installed-file or native-library attestation",
              "scope": "captured_numerical_tools_only", "global_determinism_guaranteed": False,
              "compliance_certified": False, "path_mapping": {}, "atol": None, "rtol": None}
    try:
        _require(type(atol) in (int, float) and type(rtol) in (int, float)
                 and math.isfinite(atol) and math.isfinite(rtol) and atol >= 0 and rtol >= 0,
                 "Replay tolerances must be finite nonnegative numbers")
        report.update(atol=float(atol), rtol=float(rtol))
        bundle = _verified_bundle(manifest_path, expected_root_hash)
        report.update(root_hash=bundle["root_hash"], anchored=True)
        output = _output_directory(output_dir, bundle["manifest_path"].parent)
        _require(_REPORT_NAME not in bundle["references"], "Staged inputs conflict with the replay report name")
        _require(bundle["stage"].get("execution_path") != "code-action",
                 "Code-as-Action records have no trusted captured numerical tool history; script execution is not replayed")
        runtime, runtime_errors = _runtime_matches(bundle)
        report.update(runtime_match=not runtime_errors, current_runtime=runtime)
        _require(not runtime_errors, "; ".join(runtime_errors))
        nodes = [node for node in bundle["nodes"] if node["kind"] in ("tool_completed", "tool_failed")]
        _require(bool(nodes), "No captured numerical tool events are available for replay")
        workspace = bundle["stage"].get("workspace")
        members, prepared = _logical_members(bundle["references"]), []
        for node in nodes:
            event = node["payload"]
            name = event.get("tool_name")
            _require(name in _NUMERICAL_TOOLS, "Captured tool is not an allowlisted numerical tool: " + str(name))
            _require(type(event.get("arguments")) is dict and type(event.get("succeeded")) is bool,
                     "Malformed captured tool event")
            _require(event["succeeded"] == (node["kind"] == "tool_completed"), "Tool node and outcome disagree")
            _require("result" in event if event["succeeded"] else isinstance(event.get("error"), str),
                     "Captured tool outcome is incomplete")
            arguments = _remap_arguments(event["arguments"], workspace, output, members, report["path_mapping"])
            prepared.append((node, arguments))
        report["restore"] = _restore(bundle, output)
        from devharness_server.dispatcher import ToolDispatcher
        dispatcher = ToolDispatcher()
        roots = [str(output)] + ([workspace] if isinstance(workspace, str) else [])
        for node, arguments in prepared:
            event = node["payload"]
            actual, succeeded = None, True
            try:
                actual = json.loads(dispatcher.execute(event["tool_name"], arguments))
                canonical_json(actual)
            except Exception as error:
                succeeded = False
                actual = f"{type(error).__name__}: {error}"
            expected = event["result"] if event["succeeded"] else event["error"]
            expected_comparison = _normalize_error_instance(expected) if not event["succeeded"] else expected
            actual_comparison = _normalize_error_instance(actual) if not succeeded else actual
            compared = _compare(expected_comparison, actual_comparison, atol=atol, rtol=rtol, roots=roots)
            if succeeded != event["succeeded"]:
                compared["passed"] = False
                compared["mismatch_count"] += 1
                compared["mismatches"].append({"field": "$outcome", "reason": "Tool success/failure outcome changed"})
            report["comparisons"].append({"node_id": node["node_id"], "tool_name": event["tool_name"],
                "expected_success": event["succeeded"], "actual_success": succeeded,
                "expected_outcome_sha256": hashlib.sha256(canonical_json(expected)).hexdigest(),
                "observed_outcome_sha256": hashlib.sha256(canonical_json(actual)).hexdigest(), **compared})
            report["replayed_tool_count"] += 1
        for name, reference in bundle["references"].items():
            _require(hash_artifact(output / name)["sha256"] == reference["sha256"],
                     "Restored input changed during numerical replay: " + name)
        report["passed"] = all(item["passed"] for item in report["comparisons"])
        report["status"] = "matched" if report["passed"] else "mismatched"
        report["report_path"] = str(output / _REPORT_NAME)
        with (output / _REPORT_NAME).open("xb") as target:
            target.write(canonical_json(report))
            target.flush()
            os.fsync(target.fileno())
    except (OSError, ValueError, TypeError, KeyError, OverflowError, ImportError, RuntimeError) as error:
        report["passed"] = False
        report["status"] = "out_of_scope" if isinstance(error, StudyReplayOutOfScope) else "unreplayable"
        if isinstance(error, StudyReplayOutOfScope):
            report["reason_code"] = "study_receipts_integrity_only"
            report["recommended_verifier"] = "devbench-study verify-tools"
        report["errors"].append(str(error)[:2000])
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify, restore or numerically replay a local execution archive")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("verify", "restore", "replay"):
        command = commands.add_parser(name)
        command.add_argument("--manifest", required=True, help="Absolute path to the execution manifest")
        command.add_argument("--expected-root", required=name != "verify",
                             help="SHA-256 root retained independently from the archive")
        if name != "verify":
            command.add_argument("--output-dir", required=True, help="Absolute path to a new or empty directory")
        if name == "replay":
            command.add_argument("--atol", type=float, default=1e-8)
            command.add_argument("--rtol", type=float, default=1e-8)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            report = verify_execution(args.manifest, args.expected_root)
            passed = report["valid"]
        elif args.command == "restore":
            report = restore_execution(args.manifest, args.expected_root, args.output_dir)
            passed = report["restored"]
        else:
            report = replay_execution(args.manifest, args.expected_root, args.output_dir, atol=args.atol, rtol=args.rtol)
            passed = report["passed"]
    except (OSError, ValueError, TypeError, KeyError) as error:
        report, passed = {"passed": False, "errors": [str(error)[:2000]], "compliance_certified": False}, False
    print(canonical_json(report).decode("utf-8"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
