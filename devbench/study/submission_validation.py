"""Stage the public answer contract without exposing coordinator modules.

Only explicitly named public definitions are extracted, including their actual
Pydantic validators. The participant receives one script, identical across arms,
that reads an answer and reports contract errors without rewriting it or grading
scientific correctness. The caller must protect the returned paths and hashes.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
from pathlib import Path
import shlex
from typing import Literal


AnswerKind = Literal["biology", "study", "public"]
_MODELS = {"biology": "BiologyAnswer", "study": "StudyAnswer", "public": "PublicAnswer"}
_ANSWER_DEFINITIONS = {
    "biology": ("biology_answers.py", (
        "Finite", "Probability", "Text", "EstimateStatus", "EvidenceScope", "ClaimCode", "TargetId",
        "TARGET_PAYLOAD_KINDS", "_distinct", "Interval", "HypothesisEstimate", "SupportCheck",
        "ComputedConditionContrast", "EffectsPayload", "EventEstimate", "EventsPayload", "ConditionPhase", "RhythmEstimate",
        "RhythmPayload", "ConcentrationEstimate", "ConcentrationPayload", "FractionEstimate",
        "FractionsPayload", "GraphEdge", "CellCoordinate", "SharedProcessProjection", "ProjectedPayload",
        "GraphPayload", "AncestryPayload", "OrderingPayload", "AssociationEdge", "ModuleMembership",
        "AssociationPayload", "EnrichmentEstimate", "EnrichmentPayload", "DiagnosticValue", "CellDecision",
        "DiagnosticsPayload", "CellStatePayload", "RestraintPayload", "Payload", "TargetAnswer", "BiologyAnswer",
    )),
    "study": ("answers.py", (
        "Label", "Finite", "EffectUnits", "InferenceScope", "CurveReference", "AnalysisDescription",
        "GeneEstimate", "Diagnostic", "NetworkEdge", "LineageResult", "StudyAnswer",
    )),
    "public": ("public_answers.py", (
        "Number", "ObservedGeneCurve", "DevelopmentalObservation", "LibraryFactor", "SEQCNormalization",
        "PublicAnswer",
    )),
}


@dataclass(frozen=True)
class StagedSubmissionValidator:
    protected_paths: tuple[Path, ...]
    hashes: dict[str, str]
    argv: tuple[str, ...]

    @property
    def command(self) -> str:
        return shlex.join(self.argv)


def _definitions(filename: str, names: tuple[str, ...]) -> str:
    """Fail closed on missing definitions; never copy a module or its imports."""
    tree = ast.parse(Path(__file__).with_name(filename).read_text(encoding="utf-8"))
    selected, found = [], set()
    for node in tree.body:
        name = None
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            name = node.name
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        if name not in names:
            continue
        if name in found or any(isinstance(child, (ast.Import, ast.ImportFrom)) for child in ast.walk(node)):
            raise ValueError(f"Public contract definition is not self-contained: {filename}:{name}")
        selected.append(node)
        found.add(name)
    if found != set(names):
        raise ValueError(f"Missing public contract definitions in {filename}: {sorted(set(names) - found)}")
    return ast.unparse(ast.Module(body=selected, type_ignores=[]))


_IMPORTS = '''"""Public answer contract only; never modifies answers or adjudicates findings."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import stat
import sys
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
'''

_RUNTIME = '''
_MAX_ERRORS = 20


def _report(errors=(), *, truncated=False):
    return {"valid": not errors, "scope": "public_answer_contract", "errors": list(errors),
            "errors_truncated": truncated}


def _error(message, path="$"):
    return {"path": path if len(path) <= 240 else path[:237] + "...", "message": message[:500]}


def _field_path(location, document):
    path, current = "$", document
    for part in location:
        if isinstance(current, dict) and part not in current and current.get("kind") == part:
            continue  # Pydantic's discriminated-union tag is not a JSON field.
        if isinstance(part, int):
            path += f"[{part}]"
            current = current[part] if isinstance(current, list) and 0 <= part < len(current) else None
        else:
            part = str(part)
            path += "." + part if part.isidentifier() else "[" + json.dumps(part) + "]"
            current = current.get(part) if isinstance(current, dict) else None
    return path


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object keys are not allowed")
        result[key] = value
    return result


def _validate_submission(path):
    if not Path(path).is_absolute():
        return _report([_error("The answer path must be absolute")])
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            details = os.fstat(handle.fileno())
            if not stat.S_ISREG(details.st_mode):
                return _report([_error("The answer must be a regular file")])
            if details.st_size > _MAX_ANSWER_BYTES:
                return _report([_error(f"The answer exceeds {_MAX_ANSWER_BYTES} bytes")])
            content = handle.read(_MAX_ANSWER_BYTES + 1)
    except OSError:
        return _report([_error("Cannot read the answer as a regular file; symlinks are not allowed")])
    if len(content) > _MAX_ANSWER_BYTES:
        return _report([_error(f"The answer exceeds {_MAX_ANSWER_BYTES} bytes")])
    try:
        document = json.loads(content, object_pairs_hook=_unique_object)
    except json.JSONDecodeError as error:
        return _report([_error(f"Invalid JSON at line {error.lineno}, column {error.colno}")])
    except (UnicodeError, ValueError, RecursionError) as error:
        message = "Duplicate JSON object keys are not allowed" if str(error).startswith("Duplicate") else "Invalid JSON"
        return _report([_error(message)])
    if not isinstance(document, dict):
        return _report([_error("The answer must contain a JSON object")])

    pending, errors = [((), document)], []
    while pending and len(errors) <= _MAX_ERRORS:
        location, value = pending.pop()
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(_error("JSON numbers must be finite", _field_path(location, document)))
        elif isinstance(value, dict):
            pending.extend(((*location, key), item) for key, item in value.items())
        elif isinstance(value, list):
            pending.extend(((*location, index), item) for index, item in enumerate(value))
    if errors:
        return _report(errors[:_MAX_ERRORS], truncated=len(errors) > _MAX_ERRORS)
    try:
        # Match the transport's compact JSON byte bound as well as the file bound.
        if len(json.dumps(document, allow_nan=False, separators=(",", ":")).encode()) > _MAX_ANSWER_BYTES:
            return _report([_error(f"The answer exceeds {_MAX_ANSWER_BYTES} bytes")])
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False, allow_nan=False).encode("utf-8")
        _ANSWER_MODEL.model_validate_json(canonical)
    except ValidationError as error:
        issues = error.errors(include_url=False, include_context=False, include_input=False)
        return _report([_error(issue["msg"], _field_path(issue["loc"], document))
                        for issue in issues[:_MAX_ERRORS]], truncated=len(issues) > _MAX_ERRORS)
    except (ValueError, RecursionError):
        return _report([_error("Invalid JSON")])
    return _report()


def main():
    report = (_validate_submission(sys.argv[1]) if len(sys.argv) == 2 else
              _report([_error("Usage: validate_submission.py /absolute/path/to/final_discovery.json")]))
    print(json.dumps(report, allow_nan=False, separators=(",", ":")))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def stage_submission_validator(workspace: Path, answer_kind: AnswerKind, *,
                               max_answer_bytes: int = 1_000_000) -> StagedSubmissionValidator:
    """Stage a fresh read-only validator and its exact isolated Python command.

    The byte bound must match the answer artifact transport bound. This helper
    takes no study condition, input values, references, or adjudication results.
    Identity binding and scientific/workflow checks remain with the coordinator.
    """
    if answer_kind not in _MODELS:
        raise ValueError("Unknown public answer contract")
    if type(max_answer_bytes) is not int or not 1 <= max_answer_bytes <= 8_000_000:
        raise ValueError("Answer byte budget must be an integer between 1 and 8000000")
    workspace = Path(workspace)
    if not workspace.is_absolute():
        raise ValueError("The participant workspace must be absolute")
    workspace = workspace.resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("The participant workspace must be a directory")
    common = ("FrozenContract", "Sha256", "Probability" if answer_kind == "study" else "Identifier")
    parts = [_IMPORTS, _definitions("contracts.py", common)]
    if answer_kind == "biology":
        parts.append(_definitions("biology.py", ("SharedProcessProjectionContract",)))
    filename, names = _ANSWER_DEFINITIONS[answer_kind]
    parts.extend((_definitions(filename, names), f"_ANSWER_MODEL = {_MODELS[answer_kind]}\n"
                  f"_MAX_ANSWER_BYTES = {max_answer_bytes}", _RUNTIME))
    content = ("\n\n".join(parts) + "\n").encode("utf-8")
    destination = workspace / "validate_submission.py"
    with destination.open("xb") as handle:
        handle.write(content)
    destination.chmod(0o444)
    argv = (str(workspace / ".venv/bin/python"), "-I", "-B", str(destination),
            str(workspace / "final_discovery.json"))
    return StagedSubmissionValidator((destination,), {str(destination): hashlib.sha256(content).hexdigest()}, argv)
