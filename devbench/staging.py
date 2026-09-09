"""A participant Python environment containing runtime code, never graders."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import venv

from devharness_core.watchdog import configure_worker_environment

RUNTIME_PACKAGES = ("devharness", "devharness_core", "devharness_server")
COORDINATOR_NAMES = ("devbench", "devharness", "benchmarks", "experiments", "scripts", "tests",
                     "__editable__", "sitecustomize", "usercustomize", "_virtualenv")


def stage_python(workspace: Path, *, assisted: bool = True) -> dict:
    """Link third-party dependencies individually; copy only runtime source.

    This avoids exposing this project's editable-install hook or installed grader
    packages through sys.path. It is not a hostile-code filesystem read sandbox.
    Codex's own workspace policy remains responsible for process permissions.
    """
    workspace = Path(workspace).resolve()
    environment = workspace / ".venv"
    if environment.exists() or environment.is_symlink():
        raise FileExistsError("Participant .venv must be new")
    configure_worker_environment()
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    source_site = Path(sysconfig.get_path("purelib")).resolve()
    target_site = environment / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    target_site.mkdir(parents=True, exist_ok=True)
    linked = []
    for source in sorted(source_site.iterdir()):
        name = source.name
        if (name.startswith(COORDINATOR_NAMES) or name.startswith(".")
                or name.endswith(".pth") or name == "__pycache__"):
            continue
        (target_site / name).symlink_to(source, target_is_directory=source.is_dir())
        linked.append(name)
    copied = {}
    if assisted:
        root = Path(__file__).resolve().parent.parent
        for package in RUNTIME_PACKAGES:
            source = root / package
            if not source.is_dir():
                raise RuntimeError("Runtime package is missing: " + package)
            destination = target_site / package
            destination.mkdir()
            for file in sorted(source.rglob("*.py")):
                if "__pycache__" in file.parts or file.is_symlink():
                    continue
                relative = file.relative_to(source)
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, target)
                target.chmod(0o444)
                copied[str(target.relative_to(workspace))] = hashlib.sha256(target.read_bytes()).hexdigest()
    python = environment / "bin" / "python"
    probe = (
        "import importlib.util,json,platform; import numpy,pandas,scipy,zarr,statsmodels; "
        "forbidden=['devbench','devbench_engine','benchmarks','experiments','scripts']; "
        "assert all(importlib.util.find_spec(name) is None for name in forbidden), 'Coordinator module exposed'; "
        + ("import devharness; " if assisted else "assert importlib.util.find_spec('devharness') is None; ")
        + "print(json.dumps({'python':platform.python_version(),'machine':platform.machine(),'coordinator_modules_absent':True}))"
    )
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONNOUSERSITE"] = "1"
    check = subprocess.run([str(python), "-I", "-c", probe], cwd=workspace,
                           env=env, capture_output=True, text=True, timeout=20)
    if check.returncode:
        raise RuntimeError("Participant interpreter probe failed: " + check.stderr[-2000:])
    return {"python": str(python), "probe": json.loads(check.stdout),
            "linked_dependency_entries": linked, "runtime_source_hashes": copied,
            "dependency_mode": "individual_third_party_symlinks_without_editable_hooks",
            "pythonpath_isolation": "project coordinator modules excluded; not an OS read sandbox"}


def write_task_guidance(workspace: Path, instruction: str, python: str, *, assisted: bool, timeout_sec: float = 90) -> None:
    """Provide concrete entry points and task inputs, never solutions or goldens."""
    workspace = Path(workspace).resolve()
    sdk = ("Use `import devharness as dh` for the provided numerical SDK.\n" if assisted else
           "Use the installed NumPy, pandas, SciPy, statsmodels and Zarr packages. Domain SDK is unavailable.\n")
    readme = f"""# Transcriptomics task

{instruction}

## Execution contract

{sdk}
Your working directory is {workspace}.
The tested Python interpreter is {python}. Use this exact interpreter.
Read task_inputs.json, metadata.csv, answer_schema.json and sdk_cheatsheet.py.
Write a complete solve.py script, then execute this exact command:

{python} {workspace / 'solve.py'}

Use an `if __name__ == '__main__':` guard around calls that start workers.
Save final_discovery.json from the executed script, matching answer_schema.json.
The final chat response can be short; it is not used as the answer artifact.
Do not install packages, search the host for other environments, inspect grader
code or private reference answers, call external model services, or alter inputs.
Work only with the supplied synthetic/public data in this workspace. Do not use MCP.
Do not request permission bypasses. Record permission or runtime failures plainly.
The task budget is {timeout_sec:g} seconds; do not spend it on planning documents.

## Analysis requirements

Check biological units, chronology, data type, contrasts and design rank before
fitting. A fully confounded age/batch effect remains unidentifiable when one term
is removed. Diagnose or refuse it; do not claim the confounding was repaired.
Keep q >= p for valid probabilities. Keep numerical failures explicit. Use
task_inputs.json for the observable task context and exact requested fields;
never invent missing evidence. Timing targets do not override correctness.
"""
    (workspace / "TASK_README.md").write_text(readme)
    neutral = "# Task workspace\nRead TASK_README.md. Write and execute solve.py using the specified Python. Preserve inputs and answer schema.\n"
    (workspace / "AGENTS.md").write_text(neutral)
    (workspace / "CLAUDE.md").unlink(missing_ok=True)
    if assisted:
        import inspect
        import devharness as dh
        signatures = "\n".join("# " + name + str(inspect.signature(getattr(dh, name))) for name in
                               ("validate_design", "fit_trajectory", "compact_manifold", "audit_quality", "infer_lineage", "save_discovery"))
        cheatsheet = '''"""SDK entry points. Examples show calls, not task solutions.
Always put numerical calls under a main guard in solve.py.
"""
import devharness as dh
''' + signatures + '''
# Reading this file supplies the signatures; no separate inspection command is needed.
# Public return fields (not data or expected benchmark answers):
# validate_design: is_full_rank, condition_number, aliased_columns, rank.
# fit_trajectory: family, formula, design, genes, size_factors, warnings, diagnostics.
# Each fitted genes[gene]: status, p_value, padj/q_value, coefficients,
# predicted_start, predicted_end, effect_difference, selected_df, candidate_scores.
# Endpoint predictions are not stage means; calculate any requested stage summary from data.
# infer_lineage: pseudotime{sample: value}, branch_assignments{sample: branch},
# lineage_graph{nodes:[{id,label,...}],edges:[{source,target}],root}, provenance.
# The task answer schema may require a different graph representation; convert explicitly.

# Bounded access:
# with dh.open_expression("counts.zarr") as store:
#     print(store.gene_names[:10], store.sample_names[:5])
#     values = store.get_gene_vector("one_observed_gene_id")
# Rank check: dh.validate_design("metadata.csv", "1 + numeric_time + C(batch)")
# Numeric time is derived from time_col; time_col defaults to embryonic_age.
# Fit: dh.fit_trajectory("counts.zarr", "metadata.csv", "1 + cr(numeric_time, df=3)", genes=[...])
# For normalized/continuous values use data_type="continuous"; do not cast them to counts.
# Save all task-specific fields: dh.save_discovery(**answer)
# Tool results and exact parameters are recorded in .devharness_trace.jsonl.
# That workspace trace is participant-written evidence, not authenticated host telemetry.
'''
    else:
        cheatsheet = '''"""Standard Python data access; no domain SDK in baseline."""
import pandas as pd
import zarr
# metadata = pd.read_csv("metadata.csv")
# store = zarr.open_group("counts.zarr", mode="r")
# print(list(store.array_keys()))
# Inspect array shapes and take small slices before computing.
'''
    (workspace / "sdk_cheatsheet.py").write_text(cheatsheet)
