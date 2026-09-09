"""Launch a measured Codex-only pilot; no oracle or API-key fallback."""
import os

for _thread_key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_thread_key, "1")

import argparse
import json
from pathlib import Path
import tempfile
import uuid
import shutil
import subprocess

from devbench.adapters import CodeActionAdapter
from devbench.runner import CodeActionRunner
from devbench.tasks import curated_tasks
from devbench_engine.runner import EvalRunner
from devharness_core.provenance import hash_artifact
from scripts.make_leaderboard import markdown_report, write_reports


def check_codex_login():
    """Read-only preflight; missing authentication creates no fake task rows."""
    from devbench_engine.adapters import CodexCLIAdapter
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("Codex executable is unavailable; install the CLI and authenticate with your own ChatGPT login")
    result = subprocess.run([executable, "login", "status"], capture_output=True, text=True,
                            env=CodexCLIAdapter()._environment(), timeout=15)
    if result.returncode != 0 or "logged in using chatgpt" not in (result.stdout + result.stderr).lower():
        raise RuntimeError("Codex ChatGPT login is unavailable in this runtime. Run codex login interactively; credentials are never copied from another machine")
    return {"executable": executable, "login_status": "chatgpt_verified"}


def prepare_curated_data(output_dir):
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    prepared = []
    for track, path in curated_tasks():
        runner = EvalRunner(str(path))
        task_dir = output / runner.spec["task_id"]
        task_dir.mkdir(exist_ok=False)
        staged = runner._stage(task_dir)
        # Ground truth stays only in the coordinator's memory. The public data
        # directory contains inputs and schemas, no grader or truth files.
        prepared.append({"task_id": runner.spec["task_id"], "track": track,
                         "directory": str(task_dir), "source_kind": staged["metadata"]["source_kind"],
                         "hashes": {p.name: hash_artifact(p)["sha256"] for p in task_dir.iterdir()}})
    (output / "prepared.json").write_text(json.dumps(prepared, indent=2))
    return prepared


def run_benchmark(output, *, tasks=4, backend="code-action", timeout_sec=90, model=None, workspace_root=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "runs.json").exists() or (output / "runs.jsonl").exists():
        raise FileExistsError("Choose a fresh benchmark results directory")
    if backend not in {"code-action", "codex-mcp"}:
        raise ValueError("Live benchmark backend must be code-action or codex-mcp")
    selected = curated_tasks(tasks)
    preflight = check_codex_login()
    (output / "preflight.json").write_text(json.dumps(preflight, indent=2))
    if workspace_root is not None:
        workspace_root = Path(workspace_root).resolve()
        workspace_root.mkdir(parents=True, exist_ok=True)
    results = []
    for track, path in selected:
        task_id = path.stem
        print("Starting", backend, task_id, flush=True)
        if backend == "code-action":
            adapter = CodeActionAdapter(timeout_sec=timeout_sec, model=model)
            runner = CodeActionRunner(path, track=track)
            result = runner.run(adapter, output / "runs" / (task_id + "-" + uuid.uuid4().hex[:8]),
                                workspace_dir=workspace_root / task_id if workspace_root else None)
        else:
            from devbench.adapters.codex_mcp import CodexMCPAdapter
            adapter = CodexMCPAdapter(timeout_sec=timeout_sec, model=model, max_turns=1)
            result = EvalRunner(str(path), audit_dir=output / "audit").run(adapter)
            result.update(backend="codex-mcp", model=model or "codex-cli:default", condition="assisted")
            result["metadata"].update(track=track, backend="codex-mcp")
        results.append(result)
        with (output / "runs.jsonl").open("a") as handle:
            handle.write(json.dumps(result, allow_nan=False) + "\n")
        write_reports(results, output / "runs.json", output / "report.html", output / "report.md")
        print(task_id, "passed=" + str(result["passed"]), "failure=" + str(result["metadata"].get("failure_type")), flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["code-action", "codex-mcp"], default="code-action")
    parser.add_argument("--tasks", type=int, default=4)
    parser.add_argument("--timeout-sec", type=float, default=90)
    parser.add_argument("--model")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workspace-root", type=Path)
    parser.add_argument("--prepare-only", type=Path)
    args = parser.parse_args()
    if args.prepare_only:
        print(json.dumps(prepare_curated_data(args.prepare_only), indent=2))
        return
    output = args.output
    if output is None:
        root = Path.cwd() / "results"
        root.mkdir(exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix="code-action-live.", dir=root))
    results = run_benchmark(output, tasks=args.tasks, backend=args.backend, timeout_sec=args.timeout_sec,
                            model=args.model, workspace_root=args.workspace_root)
    print(markdown_report(results))


if __name__ == "__main__":
    main()
