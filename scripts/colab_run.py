#!/usr/bin/env python3
"""Five-stage Colab runner. Importing this module never installs or runs models.

Local offline preparation:
    python scripts/colab_run.py --prepare-only --data-dir /absolute/new/data

Fresh Colab setup is explicit: --setup --install-codex. Authenticate separately
with your own interactive ChatGPT login before executing the benchmark.
"""
# %% 1. Configure numerical threads, then explicitly set up dependencies.
import os

# Set, rather than setdefault: a Colab runtime may already configure more threads.
for _thread_variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                         "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_thread_variable] = "1"

import argparse
import hashlib
import html
import json
from pathlib import Path
import shutil
import subprocess
import sys

CODEX_VERSION = "0.147.0"
COLAB_ROOT = Path("/content")
LOGIN_INSTRUCTIONS = (
    "Fresh Colab requires your own ChatGPT login. Run `codex login --device-auth` "
    "and complete browser verification yourself if enabled for your account, then `codex login status`. "
    "If this runtime cannot complete the login flow, stop and use a supported interactive runtime. "
    "Never copy Mac credentials or API keys into this notebook. Authentication, account limits, "
    "and permission prompts can block execution; setup does not authenticate you."
)


def setup_environment(project, *, install=False, install_codex=False, command=None, which=None):
    """Install only on explicit request, using the project's bounded dependencies."""
    project = Path(project).expanduser().resolve()
    if not (project / "pyproject.toml").is_file():
        raise FileNotFoundError(f"Project pyproject.toml unavailable: {project}")
    command = command or subprocess.run
    which = which or shutil.which
    if install:
        command([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                 "-e", f"{project}[dev]"], check=True, cwd=project, timeout=600)
    codex = which("codex")
    if install_codex and not codex:
        npm = which("npm")
        if not npm:
            raise RuntimeError("Codex executable unavailable and npm unavailable; automatic CLI provisioning cannot proceed")
        command([npm, "install", "--global", "--quiet", "--no-audit", "--no-fund",
                 f"@openai/codex@{CODEX_VERSION}"], check=True, cwd=project, timeout=180)
        codex = which("codex")
    print(LOGIN_INSTRUCTIONS)
    return {"project": str(project), "dependency_install_requested": install,
            "codex_install_requested": install_codex, "codex_available": bool(codex),
            "login_checked": False}


# %% 2. Prepare the fixed twelve synthetic coordinator tasks.
def prepare_data(data_dir=COLAB_ROOT / "benchmark_data", *, prepare=None):
    """Stage fixed synthetic inputs; this is preparation, not a model evaluation."""
    if prepare is None:
        from scripts.run_live_benchmark import prepare_curated_data
        prepare = prepare_curated_data
    summary = prepare(Path(data_dir).expanduser().resolve())
    compact = ({"task_count": len(summary),
                "tasks": [{key: task.get(key) for key in ("task_id", "track", "source_kind")} for task in summary]}
               if isinstance(summary, list) else summary)
    print(json.dumps(compact, indent=2, allow_nan=False))
    return summary


# %% 3. Optionally inspect a bounded processed subset, separate from fixed inputs.
def optional_processed_ingest(data_dir=COLAB_ROOT / "benchmark_data", *, enabled=False,
                              offline=False, url=None, ingest=None):
    """Optional <=200 MB, ten-second processed ingest; fallback stays synthetic.

    This output is never passed to run_benchmark or substituted for curated tasks.
    The bounded ingester retains its own source and fallback provenance.
    """
    if not enabled:
        print("Optional processed ingestion skipped.")
        return {"status": "skipped", "used_for_curated_tasks": False}
    if ingest is None:
        from benchmarks.ingest_real_data import ingest_real_data
        ingest = ingest_real_data
    options = {"output_dir": Path(data_dir).expanduser().resolve() / "optional_processed",
               "timeout_sec": 10, "max_download_mb": 200, "subset": True,
               "max_genes": 200, "max_samples": 60, "offline": offline}
    if url is not None:
        options["url"] = url
    result = dict(ingest(**options))
    result["used_for_curated_tasks"] = False
    print(json.dumps({key: result.get(key) for key in ("source_kind", "shape", "fallback_reason", "used_for_curated_tasks")},
                     indent=2, allow_nan=False))
    return result


# %% 4. Execute CodeActionAdapter via the shared benchmark coordinator.
def execute_benchmark(results_dir=COLAB_ROOT / "devbench_runs", *,
                      workspace_root=COLAB_ROOT / "workspace", model=None, run=None):
    """Run twelve curated tasks with a 60-second task deadline.

    Missing CLI/login, account, permission and runtime blockers stay blockers.
    No fallback adapter is selected. Whole-run wall time includes setup, staging
    and cleanup and is not promised to fit a 60- or 90-second target.
    """
    if run is None:
        from scripts.run_live_benchmark import run_benchmark
        run = run_benchmark
    return run(Path(results_dir).expanduser().resolve(), tasks=12,
               backend="code-action", timeout_sec=60, model=model,
               workspace_root=Path(workspace_root).expanduser().resolve())


# %% 5. Print authentic results and export JSON, HTML, Markdown and hash receipts.
def export_results(results, output_root=COLAB_ROOT, *, blocker=None, write=None, markdown=None):
    """Export measured results, or an explicitly blocked document with no rows."""
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    files = {"json": root / "devbench_results.json", "html": root / "devbench_results.html",
             "markdown": root / "devbench_results.md"}
    if results is None:
        if not blocker:
            raise ValueError("Absent measurements require an exact blocker")
        payload = {"status": "blocked", "blocker": str(blocker), "results": [], "live_measurements": False}
        files["json"].write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
        rendered = f"Benchmark blocked: {blocker}\n\nNo benchmark measurements were produced.\n"
        files["markdown"].write_text(rendered)
        files["html"].write_text("<!doctype html><html><meta charset='utf-8'><title>DevBench blocked</title>"
                                 "<body><h1>DevBench blocked</h1><pre>" + html.escape(rendered) + "</pre></body></html>")
    else:
        if write is None or markdown is None:
            from scripts.make_leaderboard import markdown_report, write_reports
            write = write or write_reports
            markdown = markdown or markdown_report
        write(results, output_json=files["json"], output_html=files["html"], output_md=files["markdown"])
        rendered = markdown(results)
    print(rendered)
    receipt = {"format": "devbench.colab-export/1", "scope": "exported bytes; not a signature or compliance certificate",
               "artifacts": [{"name": path.name, "bytes": path.stat().st_size,
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in files.values()]}
    files["receipt"] = root / "devbench_exports.sha256.json"
    files["receipt"].write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n")
    print("Exports: " + ", ".join(str(path) for path in files.values()))
    return files


def run_stage(stage, content_root=COLAB_ROOT, *, model=None, optional_processed=False,
              offline=False, blocked=False, command=None):
    """Keep the notebook kernel stdlib-only; use a fresh interpreter per stage.

    Process status is not a model measurement. The child uses absolute source
    paths, so an editable install never requires a notebook kernel restart.
    """
    if stage not in {"prepare", "ingest", "execute", "report"}:
        raise ValueError("Unknown Colab stage")
    root = Path(content_root).expanduser().resolve()
    project = Path(__file__).resolve().parents[1]
    argv = [sys.executable, str(project / "scripts" / "colab_run.py"), "--stage", stage,
            "--project", str(project), "--data-dir", str(root / "benchmark_data"),
            "--results-dir", str(root / "devbench_runs"), "--workspace-root", str(root / "workspace"),
            "--output-root", str(root)]
    if model is not None:
        argv.extend(["--model", model])
    if optional_processed:
        argv.append("--optional-processed")
    else:
        argv.append("--skip-processed")
    if offline:
        argv.append("--offline")
    if blocked:
        argv.append("--blocked")
    result = (command or subprocess.run)(argv, cwd=root, check=False)
    if result.returncode != 0 and not (stage == "execute" and result.returncode == 2):
        raise subprocess.CalledProcessError(result.returncode, argv)
    return {"stage": stage, "returncode": result.returncode}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--setup", action="store_true", help="Explicitly install editable project dependencies")
    parser.add_argument("--install-codex", action="store_true", help="Explicitly provision the pinned npm CLI if absent; never logs in")
    parser.add_argument("--prepare-only", action="store_true", help="Generate synthetic inputs without executing models")
    parser.add_argument("--stage", choices=["all", "prepare", "ingest", "execute", "report"], default="all")
    parser.add_argument("--blocked", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--data-dir", type=Path, default=COLAB_ROOT / "benchmark_data")
    parser.add_argument("--results-dir", type=Path, default=COLAB_ROOT / "devbench_runs")
    parser.add_argument("--workspace-root", type=Path, default=COLAB_ROOT / "workspace")
    parser.add_argument("--output-root", type=Path, default=COLAB_ROOT)
    parser.add_argument("--model")
    parser.add_argument("--optional-processed", action="store_true", default=True,
                        help="Check a bounded processed subset with explicit synthetic fallback (default)")
    parser.add_argument("--skip-processed", action="store_false", dest="optional_processed")
    parser.add_argument("--offline", action="store_true", help="Force synthetic fallback for the optional ingest")
    parser.add_argument("--processed-url")
    args = parser.parse_args(argv)
    project = args.project.expanduser().resolve()
    sys.path.insert(0, str(project))
    if args.stage == "all" or args.setup or args.install_codex:
        setup_environment(project, install=args.setup, install_codex=args.install_codex)
    if args.stage in {"all", "prepare"}:
        prepare_data(args.data_dir)
    if args.prepare_only:
        print("Preparation complete. No model calls or benchmark performance measurements were made.")
        return 0
    if args.stage in {"all", "ingest"}:
        optional_processed_ingest(args.data_dir, enabled=args.optional_processed, offline=args.offline, url=args.processed_url)
    if args.stage in {"prepare", "ingest"}:
        return 0
    if args.stage == "report":
        source = (args.output_root / "devbench_results.json" if args.blocked else args.results_dir / "runs.json").resolve()
        if source.stat().st_size > 8_000_000:
            raise ValueError("Results exceed the 8 MB report input limit")
        payload = json.loads(source.read_text())
        if args.blocked:
            if not isinstance(payload, dict) or payload.get("status") != "blocked":
                raise ValueError("Expected the exact blocked execution record")
            print("Benchmark blocked: " + payload["blocker"] + "\nNo benchmark measurements were produced.")
        else:
            if not isinstance(payload, list):
                raise ValueError("Benchmark results must be a list of recorded outcomes")
            export_results(payload, args.output_root)
        return 0
    try:
        results = execute_benchmark(args.results_dir, workspace_root=args.workspace_root, model=args.model)
    except FileExistsError as error:
        print(f"Benchmark blocked: FileExistsError: {error}", file=sys.stderr)
        return 3  # Preserve any earlier exports when the coordinator rejects reuse.
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        export_results(None, args.output_root, blocker=f"{type(error).__name__}: {error}")
        return 2
    if args.stage == "all":
        export_results(results, args.output_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
