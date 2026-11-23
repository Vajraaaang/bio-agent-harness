"""Execute editable notebooks in the caller's pinned Python environment."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


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
    from jupyter_client import AsyncKernelManager
    from jupyter_client.kernelspec import KernelSpec
    from nbclient import NotebookClient

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
