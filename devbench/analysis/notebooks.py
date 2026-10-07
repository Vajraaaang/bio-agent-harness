"""Execute editable notebooks in the caller's pinned Python environment."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile


def write_notebook(path, cells):
    import nbformat

    notebook = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(source) for source in cells])
    notebook.metadata["kernelspec"] = {"display_name": "Python 3", "language": "python", "name": "python3"}
    notebook.metadata["language_info"] = {"name": "python", "version": sys.version.split()[0]}
    with Path(path).open("x") as handle:
        nbformat.write(notebook, handle)


def _save_notebook(notebook, path):
    import nbformat

    descriptor, name = tempfile.mkstemp(prefix=".notebook-", suffix=".ipynb", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as handle:
            nbformat.write(notebook, handle)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def execute_notebook(template, output, *, cell_timeout=120):
    """Run trusted notebook code, retaining partial output on failure.

    This is execution and capture, not a security sandbox for arbitrary notebooks.
    The application coordinator applies its outer process-tree resource limits.
    """
    import nbformat
    from nbclient import NotebookClient
    from jupyter_client import AsyncKernelManager
    from jupyter_client.kernelspec import KernelSpec

    template, output = Path(template), Path(output)
    if not template.is_absolute() or not output.is_absolute() or template == output:
        raise ValueError("Use distinct absolute template and executed-notebook paths")
    if type(cell_timeout) is not int or cell_timeout < 1:
        raise ValueError("cell_timeout must be a positive integer")
    notebook = nbformat.read(template, as_version=4)

    class PinnedKernelManager(AsyncKernelManager):
        @property
        def kernel_spec(self):
            return KernelSpec(argv=[sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}",
                                   "--IPKernelApp.kernel_class=ipykernel.ipkernel.IPythonKernel",
                                   "--IPKernelApp.extensions=[]", "--IPKernelApp.extra_extensions=[]",
                                   "--IPKernelApp.exec_lines=[]", "--IPKernelApp.exec_files=[]"],
                display_name="DevBench pinned Python", language="python",
                env={"IPYTHONDIR": str(Path(runtime)/"ipython"), "JUPYTER_CONFIG_DIR": runtime,
                     "PYTHONPATH": ""})

    with tempfile.TemporaryDirectory(prefix=".kernel-", dir=output.parent) as runtime:
        manager = PinnedKernelManager(connection_file=str(Path(runtime) / "connection.json"))
        client = NotebookClient(notebook, km=manager, timeout=cell_timeout, allow_errors=False,
            record_timing=True, resources={"metadata": {"path": str(output.parent)}},
            on_cell_executed=lambda **kwargs: _save_notebook(notebook, output),
            on_cell_error=lambda **kwargs: _save_notebook(notebook, output))
        with (output.parent/"kernel-startup.log").open("ab") as kernel_log:
            try:
                client.execute(cleanup_kc=True, stdout=kernel_log, stderr=kernel_log)
            finally:
                _save_notebook(notebook, output)
    code = [cell for cell in notebook.cells if cell.cell_type == "code"]
    return {"status": "completed", "executed_code_cells": sum(cell.execution_count is not None for cell in code),
            "code_cells": len(code), "interpreter": sys.executable,
            "capture_scope": "nbclient_cell_outputs_and_timings"}


def colab_bootstrap_cells(project, *, runtime_root=Path("/content")):
    """Prepare source, locked environment and Drive access; does not run analysis.

    The source ZIP reuses the repository's allowlist and bounded extractor.
    Numerical stages must use project_python in a fresh process, not the Colab
    notebook's preinstalled scientific packages. Drive authorization is user-driven.
    """
    import inspect
    from scripts.build_colab_notebook import (
        build_source_bundle, allowed_bundle_path, _read_source_file, extract_source_bundle,
    )
    if not Path(project).is_absolute() or not Path(runtime_root).is_absolute():
        raise ValueError("Source and runtime roots must be absolute")
    bundle = build_source_bundle(project)
    if "uv.lock" not in bundle["files"]:
        raise ValueError("A Colab workbench requires its exact uv.lock in the source bundle")
    setup = "\n".join([
        "import os",
        "for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS','NUMBA_NUM_THREADS'):",
        "    os.environ[name] = '1'",
        "import base64, hashlib, io, json, stat, sys, zipfile",
        "from pathlib import Path, PurePosixPath",
        f"CONTENT_ROOT = Path({str(runtime_root)!r})",
        inspect.getsource(allowed_bundle_path), inspect.getsource(_read_source_file), inspect.getsource(extract_source_bundle),
        f"SOURCE_BUNDLE_SHA256 = {bundle['sha256']!r}",
        f"SOURCE_BUNDLE_BASE64 = {bundle['base64']!r}",
        "PROJECT = CONTENT_ROOT / ('devbench-analysis-' + SOURCE_BUNDLE_SHA256[:12])",
        "extract_source_bundle(SOURCE_BUNDLE_BASE64, PROJECT, SOURCE_BUNDLE_SHA256)",
        "print({'source_sha256': SOURCE_BUNDLE_SHA256, 'project': str(PROJECT)})",
    ])
    install = "\n".join([
        "import subprocess",
        "subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet', 'uv'], check=True)",
        "subprocess.run([sys.executable, '-m', 'uv', 'sync', '--locked', '--python', '3.12', '--extra', 'analysis', '--directory', str(PROJECT)], check=True)",
        "project_python = PROJECT / '.venv' / 'bin' / 'python'",
        "assert project_python.is_file()",
    ])
    mount = "\n".join([
        "from google.colab import drive",
        "DRIVE_ROOT = CONTENT_ROOT / 'drive'",
        "drive.mount(str(DRIVE_ROOT))  # Complete Google's authorization prompt yourself.",
        "print('Use the original Drive-hosted H5AD path; do not copy it into /content.')",
    ])
    return [setup, install, mount], {key:value for key,value in bundle.items() if key != "base64"}


def write_colab_analysis_notebook(path, project, *, drive_data_path, expected_data_sha256,
                                  drive_output_path, correction="harmony"):
    """Self-contained Colab launcher plus a separately executed editable notebook.

    Google Drive OAuth still requires the user's interaction. The generated
    notebook makes that boundary explicit; it never uploads the original data.
    """
    import re
    from .workbench import PROMPT

    path = Path(path)
    for value in (drive_data_path, drive_output_path):
        if not Path(value).is_relative_to(Path("/content/drive/MyDrive")):
            raise ValueError("Use an original mounted My Drive data/output path")
    if not re.fullmatch("[0-9a-f]{64}", expected_data_sha256) or correction not in {"harmony", "scvi"}:
        raise ValueError("A retained data SHA-256 and supported correction are required")
    cells, source = colab_bootstrap_cells(Path(project))
    # Put authorization first so the user need not execute a large source cell
    # or install dependencies just to expose the Drive consent prompt.
    mount_first="\n".join([
        "# @title 1. Connect Google Drive (complete Google's authorization prompt)",
        "import os",
        "for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS','NUMBA_NUM_THREADS'):",
        "    os.environ[name] = '1'",
        "from google.colab import drive",
        "drive.mount('/content/drive')",
    ])
    cells=[mount_first,"# @title 2. Restore the frozen source snapshot\n"+cells[0],
           "# @title 3. Install the uv-locked Python environment\n"+cells[1]]
    cells += ["\n".join([
        "# @title 4. Inspect the data path and frozen resource plan",
        f"DATA = Path({str(drive_data_path)!r})",
        f"OUTPUT = Path({str(drive_output_path)!r})",
        f"EXPECTED_DATA_SHA256 = {expected_data_sha256!r}",
        "if not DATA.is_file():",
        "    raise FileNotFoundError('Mount the correct Google account and add the shared file shortcut to My Drive: ' + str(DATA))",
        "command = [str(project_python), '-m', 'devbench.analysis.workbench',",
        f"    '--data', str(DATA), '--out', str(OUTPUT), '--prompt', {PROMPT!r},",
        f"    '--execution-tier', 'colab', '--expected-data-sha256', EXPECTED_DATA_SHA256, '--correction', {correction!r}]",
        "# Planning reads only shape and annotations; inspect the limits before execution.",
        "subprocess.run(command + ['--plan-only'], cwd=PROJECT, check=True)",
    ])]
    cells += ["\n".join([
        "# @title 5. Execute the full dataset, review it and export embedded HTML",
        "# Run only after approving the displayed resource plan. Each numerical stage",
        "# is supervised and writes verified progress directly to OUTPUT on Drive.",
        "# Re-run this cell after reconnecting to resume the same unfinished bundle.",
        "# A sealed bundle is preserved; use a fresh output path for a new analysis.",
        "subprocess.run(command, cwd=PROJECT, check=True)",
        "from IPython.display import display, Markdown, Image, HTML",
        "display(Markdown((OUTPUT / 'summary.md').read_text()))",
        "display(HTML((OUTPUT / 'figures' / 'interactive.html').read_text()))",
        "print((OUTPUT / 'reviewer-report.json').read_text())",
        "print('Retain this root receipt separately:', (OUTPUT / 'root-receipt.json').read_text())",
        "for figure in sorted((OUTPUT / 'figures').glob('*.png')):",
        "    display(Image(filename=str(figure)))",
        "print('Bundle archive on Drive:', OUTPUT.with_name(OUTPUT.name + '.bundle.zip'))",
        "print('Self-contained notebook HTML:', OUTPUT / 'notebook.html')",
    ])]
    write_notebook(path, cells)
    return {"notebook": str(path), "source_bundle": source, "data_sha256": expected_data_sha256,
            "execution_tier": "colab", "runtime_executed": False}
