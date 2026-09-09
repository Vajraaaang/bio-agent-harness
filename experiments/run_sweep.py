"""Checkpointed, audited comparisons of local CLI agents and offline controls."""
import argparse
import hashlib
import importlib
import json
import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from benchmarks import TASK_DIRECTORY
from devbench_engine import EvalRunner
from devbench_engine.runner import _code_fingerprint, _environment, requires_domain_tool_history
from devharness_core.provenance import hash_artifact


def select_pilot(paths: Iterable[Path], count: Optional[int]) -> List[Path]:
    paths = sorted(Path(path).resolve() for path in paths)
    if count is None:
        return paths
    if isinstance(count, bool) or count < 1:
        raise ValueError("Pilot count must be positive")
    tracks = defaultdict(list)
    for path in paths:
        tracks[json.loads(path.read_text()).get("metadata", {}).get("track", "legacy")].append(path)
    selected = []
    while len(selected) < min(count, len(paths)):
        for track in sorted(tracks):
            if tracks[track] and len(selected) < count:
                selected.append(tracks[track].pop(0))
    return selected


def _save_json(path: Path, results: list) -> None:
    temporary = path.with_name(path.name + ".pending")
    with temporary.open("w") as output:
        json.dump(results, output, indent=2, allow_nan=False)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def _backend(adapter) -> str:
    from devbench_engine.adapters import APIAdapter, ClaudeCLIAdapter, CodexCLIAdapter, InProcessMockAdapter
    if isinstance(adapter, ClaudeCLIAdapter):
        return "claude-cli"
    if isinstance(adapter, CodexCLIAdapter):
        return "codex-cli"
    if isinstance(adapter, APIAdapter):
        return "api"
    if isinstance(adapter, InProcessMockAdapter):
        return "mock"
    return "callback"


def _input_binding(path: Path) -> dict:
    spec = json.loads(path.read_text())
    bindings = {asset["destination"]: hash_artifact((path.parent / asset["source"]).resolve())["sha256"]
                for asset in spec.get("files", [])}
    if spec.get("dataset", {}).get("builder") == "public_subset":
        from benchmarks.datasets import _snapshot_directory
        bindings["public_snapshot"] = hash_artifact(_snapshot_directory(spec["dataset"]))["sha256"]
    return bindings


def run_sweep(agents: Dict[str, Callable], task_paths: Iterable[str], repetitions: int = 1,
              conditions: Sequence[str] = ("assisted",), audit_dir: Optional[Path] = None,
              output_path: Optional[Path] = None, resume: bool = False,
              validation_mode: str = "research", control_evidence: Optional[dict] = None,
              actor_id: str = "local-unverified", retry_failed: bool = False) -> List[dict]:
    if not agents or isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 1:
        raise ValueError("Supply agents and a positive integer repetition count")
    if not conditions or any(condition not in ("assisted", "baseline") for condition in conditions):
        raise ValueError("Unknown or empty condition sequence")
    if len(set(conditions)) != len(conditions):
        raise ValueError("Conditions must be unique")
    tasks = [Path(path).resolve() for path in task_paths]
    if not tasks:
        raise ValueError("Supply at least one task")
    if "baseline" in conditions:
        incompatible = [path.name for path in tasks if requires_domain_tool_history(json.loads(path.read_text())["grader"])]
        if incompatible:
            raise ValueError("Baseline comparison is incomparable for tasks requiring domain tool history: " +
                             ", ".join(incompatible) + ". Use the expanded outcome-graded suite for paired comparisons.")
    output = Path(output_path).expanduser().resolve() if output_path else None
    results = []
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        journal = (output.with_name(output.name + ".journal.jsonl") if output.suffix.lower() == ".jsonl"
                   else output.with_suffix(".jsonl"))
        if resume and journal.exists():
            for line in journal.read_text().splitlines():
                if line.strip():
                    results.append(json.loads(line))
        elif resume and output.exists():
            results = json.loads(output.read_text())
        else:
            journal.write_text("")
    elif resume:
        raise ValueError("resume requires output_path")
    historical = {result.get("run_key"): result for result in results}
    results = []
    code = _code_fingerprint()
    environment = _environment()
    for name, agent in agents.items():
        backend = _backend(agent)
        config = {key: getattr(agent, key, None) for key in ("model", "max_turns", "lifecycle", "timeout_sec")}
        # Lifecycle adapters expose their resolved phase list instead of the
        # original constructor arguments; include that in the resume identity.
        config["phases"] = list(getattr(agent, "phases", []))
        for path in tasks:
            task_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            try:
                inputs = _input_binding(path)
            except (OSError, ValueError) as error:
                # Do not reuse a previous completion when its inputs cannot be
                # inspected. Let the audited runner record the staging error.
                inputs = {"input_binding_error": f"{type(error).__name__}: {error}"}
            for repetition in range(repetitions):
                for condition in conditions:
                    key = hashlib.sha256(json.dumps({"task":task_hash, "agent":name, "backend":backend,
                        "condition":condition, "repetition":repetition, "code":code, "config":config,
                        "environment":environment, "inputs":inputs,
                        "validation_mode":validation_mode}, sort_keys=True).encode()).hexdigest()
                    if key in historical and not (retry_failed and not historical[key]["passed"]):
                        results.append(historical[key])
                        continue
                    runner = EvalRunner(str(path), audit_dir=audit_dir, actor_id=actor_id, condition=condition,
                                        validation_mode=validation_mode, control_evidence=control_evidence)
                    result = runner.run(agent)
                    result.update(model=name, backend=backend, condition=condition, repetition=repetition, run_key=key)
                    result["metadata"]["backend"] = backend
                    results.append(result)
                    historical[key] = result
                    if output:
                        with journal.open("a") as handle:
                            handle.write(json.dumps(result, allow_nan=False) + "\n")
                            handle.flush()
                            os.fsync(handle.fileno())
                        _save_json(output, results)
                    logging.getLogger(__name__).info("%s %s %s run=%s passed=%s score=%.3f", name,
                        result["task_id"], condition, repetition, result["passed"], result["score"])
    if output:
        _save_json(output, results)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["codex-cli", "claude-cli", "mock", "api"], action="append")
    parser.add_argument("--suite", choices=["expanded", "public", "legacy", "all"], default="expanded")
    parser.add_argument("--tasks-dir", type=Path)
    parser.add_argument("--task", action="append", type=Path)
    parser.add_argument("--pilot", type=int, help="Balanced number of tasks across tracks; omitted means the full selected suite")
    parser.add_argument("--condition", choices=["assisted", "baseline", "both"], default="assisted")
    parser.add_argument("--turns", type=int, choices=[1,4], default=4)
    parser.add_argument("--agent", action="append", help="LABEL=module:function for custom mock or optional API callbacks")
    parser.add_argument("--model", action="append", help="Model aliases for a single CLI backend; omitted uses that CLI's default")
    parser.add_argument("--timeout-sec", type=float, default=300)
    parser.add_argument("--api-key-env")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit-dir", type=Path)
    parser.add_argument("--actor-id", default="local-unverified", help="Audit actor label, not authenticated identity")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true", help="With resume, rerun failed completions instead of reusing them")
    parser.add_argument("--validation-mode", choices=["research","clinical"], default="research")
    parser.add_argument("--control-evidence", type=Path)
    args = parser.parse_args()
    from devbench_engine.adapters import APIAdapter, ClaudeCLIAdapter, CodexCLIAdapter, InProcessMockAdapter
    from experiments.matrix_oracle import MatrixOracleAdapter
    logging.basicConfig(level=logging.INFO)
    backends = args.backend or ["codex-cli"]
    if len(backends) != len(set(backends)):
        parser.error("Duplicate backend")
    if args.model and len(backends) != 1:
        parser.error("Use separate commands to select explicit model aliases for multiple CLI backends")
    agents = {}
    for backend in backends:
        if backend in ("codex-cli", "claude-cli"):
            if args.agent or args.api_key_env:
                parser.error("CLI backends use local subscription login; custom callbacks and API keys are separate")
            adapter_type = CodexCLIAdapter if backend == "codex-cli" else ClaudeCLIAdapter
            for model in args.model or [None]:
                agents[f"{backend}:{model or 'default'}"] = adapter_type(model=model, timeout_sec=args.timeout_sec,
                                                                        max_turns=args.turns)
        elif backend == "mock" and not args.agent:
            agents["synthetic-oracle"] = MatrixOracleAdapter()
        elif backend == "api" and (not args.agent or not args.api_key_env or not os.environ.get(args.api_key_env)):
            parser.error("api requires an explicit callback and a selected nonempty --api-key-env")
        else:
            for entry in args.agent or []:
                label, target = entry.split("=", 1)
                module, function = target.split(":", 1)
                callback = getattr(importlib.import_module(module), function)
                if label in agents or not callable(callback):
                    parser.error("Callbacks must be callable and labels unique")
                agents[label] = (APIAdapter(callback, api_key=os.environ[args.api_key_env]) if backend == "api"
                                 else InProcessMockAdapter(callback))
    if args.task:
        paths = args.task
    elif args.tasks_dir:
        paths = sorted(args.tasks_dir.expanduser().resolve().rglob("*.json"))
    else:
        paths = []
        if args.suite in ("legacy", "all"):
            paths.extend(sorted(TASK_DIRECTORY.glob("*.json")))
        if args.suite in ("expanded", "public", "all"):
            for path in sorted((TASK_DIRECTORY / "expanded").glob("*.json")):
                source = json.loads(path.read_text()).get("metadata", {}).get("source_kind")
                if args.suite == "all" or (args.suite == "public") == (source == "public_processed"):
                    paths.append(path)
    paths = select_pilot(paths, args.pilot)
    conditions = ("baseline", "assisted") if args.condition == "both" else (args.condition,)
    controls = json.loads(args.control_evidence.resolve().read_text()) if args.control_evidence else None
    results = run_sweep(agents, [str(path.resolve()) for path in paths], args.repetitions, conditions,
        args.audit_dir, args.output, args.resume, args.validation_mode, controls, args.actor_id, args.retry_failed)
    print(json.dumps({"runs":len(results), "passed":sum(result["passed"] for result in results),
                      "output":str(args.output.resolve()), "inference_backends":backends}))


if __name__ == "__main__":
    main()
