import hashlib

import pytest

nbformat = pytest.importorskip("nbformat")
pytest.importorskip("nbclient")


def test_real_kernel_executes_and_retains_template(tmp_path):
    from devbench.analysis.notebooks import execute_notebook, write_notebook
    template = tmp_path / "notebook.template.ipynb"
    output = tmp_path / "notebook.ipynb"
    write_notebook(template, ["value = 6 * 7", "print(value)"])
    before = hashlib.sha256(template.read_bytes()).hexdigest()
    result = execute_notebook(template, output, cell_timeout=30)
    book = nbformat.read(output, as_version=4)
    assert result["executed_code_cells"] == 2
    assert book.cells[-1].outputs[0].text == "42\n"
    assert hashlib.sha256(template.read_bytes()).hexdigest() == before
    assert all(cell.execution_count is not None for cell in book.cells if cell.cell_type == "code")


def test_failed_cell_and_prior_output_are_preserved(tmp_path):
    from devbench.analysis.notebooks import execute_notebook, write_notebook
    template = tmp_path / "notebook.template.ipynb"
    output = tmp_path / "notebook.ipynb"
    write_notebook(template, ["print('completed first cell')", "raise ValueError('deliberate_failure')"])
    with pytest.raises(Exception, match="deliberate_failure"):
        execute_notebook(template, output, cell_timeout=30)
    book = nbformat.read(output, as_version=4)
    assert book.cells[0].outputs[0].text == "completed first cell\n"
    assert book.cells[1].outputs[-1].output_type == "error"
    assert book.cells[1].outputs[-1].ename == "ValueError"


def test_pinned_kernel_does_not_inherit_host_kernel_class(tmp_path, monkeypatch):
    from devbench.analysis.notebooks import execute_notebook, write_notebook
    profile = tmp_path/"host-ipython/profile_default"
    profile.mkdir(parents=True)
    (profile/"ipython_kernel_config.py").write_text("c.IPKernelApp.kernel_class = 'not_installed.host_kernel.Kernel'\n")
    monkeypatch.setenv("IPYTHONDIR", str(profile.parent))
    template, output = tmp_path/"template.ipynb", tmp_path/"notebook.ipynb"
    write_notebook(template, ["from IPython import get_ipython; print(type(get_ipython().kernel).__module__)"])
    execute_notebook(template, output, cell_timeout=30)
    book = nbformat.read(output, as_version=4)
    assert "ipykernel.ipkernel" in book.cells[0].outputs[0].text
    assert (tmp_path/"kernel-startup.log").is_file()
