"""Offline checks only; injected calls are never benchmark measurements."""
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import zipfile

import pytest


PROJECT = Path(__file__).resolve().parents[1]


def load_script(name):
    path = PROJECT / "scripts" / f"{name}.py"
    assert path.exists(), f"Colab source is missing: {path.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_import_sets_all_thread_limits_without_loading_numerical_packages():
    script = PROJECT / "scripts" / "colab_run.py"
    assert script.exists(), "Import-safe executable Colab source is required"
    code = f"""
import os, runpy, sys
for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[name] = '8'
runpy.run_path({str(script)!r}, run_name='colab_import_test')
assert all(os.environ[name] == '1' for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'))
assert not any(name in sys.modules for name in ('numpy','pandas','scipy','devharness'))
"""
    subprocess.run([sys.executable, "-c", code], check=True, timeout=15)


def test_setup_is_explicit_and_uses_project_constraints(tmp_path):
    runner = load_script("colab_run")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='example'\n")
    calls = []

    def command(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    runner.setup_environment(tmp_path, command=command, which=lambda name: "/bin/npm" if name == "npm" else None)
    assert calls == []
    runner.setup_environment(tmp_path, install=True, install_codex=True, command=command,
                             which=lambda name: "/bin/npm" if name == "npm" else None)
    assert calls[0][0] == [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-e", f"{tmp_path}[dev]"]
    assert calls[1][0] == ["/bin/npm", "install", "--global", "--quiet", "--no-audit", "--no-fund", "@openai/codex@0.147.0"]
    assert all("login" not in argv and "--upgrade" not in argv for argv, _ in calls)


def test_requested_cli_setup_reports_missing_npm_without_login(tmp_path):
    runner = load_script("colab_run")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='example'\n")
    with pytest.raises(RuntimeError, match="Codex executable unavailable and npm unavailable"):
        runner.setup_environment(tmp_path, install_codex=True, which=lambda name: None,
                                 command=lambda *args, **kwargs: pytest.fail("Unexpected installation or login"))


def test_offline_data_and_execution_contract_are_separate(tmp_path):
    runner = load_script("colab_run")
    prepared = []
    summary = runner.prepare_data(tmp_path / "data", prepare=lambda path: prepared.append(path) or {"task_count": 12})
    assert summary["task_count"] == 12
    assert prepared == [tmp_path / "data"]
    calls = []
    result = runner.execute_benchmark(tmp_path / "results", workspace_root=tmp_path / "workspace",
                                      run=lambda output, **kwargs: calls.append((output, kwargs)) or [])
    assert result == []
    assert calls == [(tmp_path / "results", {"tasks": 12, "backend": "code-action", "timeout_sec": 60,
                                           "model": None, "workspace_root": tmp_path / "workspace"})]


def test_missing_cli_blocker_is_not_converted_to_oracle_results(tmp_path):
    runner = load_script("colab_run")

    def blocked(*args, **kwargs):
        raise RuntimeError("Codex executable unavailable; interactive ChatGPT login required")

    with pytest.raises(RuntimeError, match="Codex executable unavailable; interactive ChatGPT login required"):
        runner.execute_benchmark(tmp_path / "results", workspace_root=tmp_path / "workspace", run=blocked)


def test_optional_ingest_is_disabled_by_default_and_separate(tmp_path):
    runner = load_script("colab_run")
    calls = []

    def ingest(**kwargs):
        calls.append(kwargs)
        return {"source_kind": "synthetic", "fallback_reason": "offline"}

    assert runner.optional_processed_ingest(tmp_path, ingest=ingest)["status"] == "skipped"
    assert not calls
    result = runner.optional_processed_ingest(tmp_path, enabled=True, offline=True, ingest=ingest)
    assert result["source_kind"] == "synthetic"
    assert result["used_for_curated_tasks"] is False
    assert calls[0]["output_dir"] == tmp_path / "optional_processed"
    assert calls[0]["timeout_sec"] == 10
    assert calls[0]["max_download_mb"] == 200
    assert calls[0]["subset"] is True
    assert calls[0]["offline"] is True


def test_export_hashes_actual_files(tmp_path, capsys):
    runner = load_script("colab_run")
    results = [{"task_id": "offline_fixture", "passed": False}]

    def report(items, *, output_json, output_html, output_md):
        output_json.write_text(json.dumps(items))
        output_html.write_text("<p>Offline fixture only</p>")
        output_md.write_text("Offline fixture only")

    exported = runner.export_results(results, tmp_path, write=report, markdown=lambda items: "Offline fixture only")
    receipt = json.loads(exported["receipt"].read_text())
    for entry in receipt["artifacts"]:
        path = tmp_path / entry["name"]
        assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert "Offline fixture only" in capsys.readouterr().out


def test_blocked_export_contains_no_successful_looking_rows(tmp_path):
    runner = load_script("colab_run")
    paths = runner.export_results(None, tmp_path, blocker="Codex executable unavailable")
    result = json.loads(paths["json"].read_text())
    assert result == {"status": "blocked", "blocker": "Codex executable unavailable",
                      "results": [], "live_measurements": False}
    assert "Codex executable unavailable" in paths["html"].read_text()
    assert paths["receipt"].exists()


def test_prepare_only_cli_does_not_invoke_benchmark(tmp_path, monkeypatch):
    runner = load_script("colab_run")
    calls = []
    monkeypatch.setattr(runner, "setup_environment", lambda *args, **kwargs: calls.append(("setup", kwargs)))
    monkeypatch.setattr(runner, "prepare_data", lambda path: calls.append(("prepare", path)))
    monkeypatch.setattr(runner, "optional_processed_ingest", lambda *args, **kwargs: calls.append(("optional", kwargs)))
    monkeypatch.setattr(runner, "execute_benchmark", lambda *args, **kwargs: pytest.fail("Offline preparation attempted model execution"))
    assert runner.main(["--prepare-only", "--data-dir", str(tmp_path / "data")]) == 0
    assert calls[0][1] == {"install": False, "install_codex": False}
    assert calls[1] == ("prepare", tmp_path / "data")
    assert not any(call[0] == "optional" for call in calls), "Prepare-only must not access processed data"


def test_notebook_stages_spawn_absolute_script_with_fresh_interpreter(tmp_path):
    runner = load_script("colab_run")
    calls = []

    def command(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    runner.run_stage("execute", tmp_path, command=command)
    argv, options = calls[0]
    assert argv[:2] == [sys.executable, str(PROJECT / "scripts/colab_run.py")]
    assert argv[argv.index("--stage") + 1] == "execute"
    assert argv[argv.index("--project") + 1] == str(PROJECT)
    assert argv[argv.index("--workspace-root") + 1] == str(tmp_path / "workspace")
    assert options["cwd"] == tmp_path
    assert "--setup" not in argv and "--install-codex" not in argv


def test_existing_run_rejection_preserves_exported_evidence(tmp_path, monkeypatch):
    runner = load_script("colab_run")
    prior = tmp_path / "devbench_results.json"
    prior.write_text('[{"task_id": "prior_run"}]')

    def existing(*args, **kwargs):
        raise FileExistsError("Choose a fresh benchmark results directory")

    monkeypatch.setattr(runner, "execute_benchmark", existing)
    assert runner.main(["--stage", "execute", "--output-root", str(tmp_path)]) == 3
    assert prior.read_text() == '[{"task_id": "prior_run"}]'


def fake_project(root):
    for name, text in {"README.md": "Example", "LICENSE": "MIT", "pyproject.toml": "[project]\nname='example'\n",
                       "devharness/__init__.py": "", "devharness/data.py": "VALUE = 1", "devbench/tasks/catalog.json": "{}",
                       "scripts/colab_run.py": (PROJECT / "scripts/colab_run.py").read_text(),
                       "scripts/run_live_benchmark.py": "", "scripts/make_leaderboard.py": "",
                       ".git/config": "secret", ".env": "secret", "results/run.json": "secret",
                       "devharness/__pycache__/private.py": "secret", "devharness/credentials.py": "secret"}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_bundle_is_deterministic_allowlisted_and_extracts_safely(tmp_path):
    builder = load_script("build_colab_notebook")
    source = tmp_path / "source"
    fake_project(source)
    first = builder.build_source_bundle(source)
    assert first == builder.build_source_bundle(source)
    assert first["bytes"] <= 8_000_000
    assert "devbench/tasks/catalog.json" in first["files"]
    assert "devharness/data.py" in first["files"]
    assert not any("secret" in path or "credentials" in path or "__pycache__" in path or path.startswith((".git", "results")) for path in first["files"])
    destination = builder.extract_source_bundle(first["base64"], tmp_path / "extracted", first["sha256"])
    assert (destination / "scripts/colab_run.py").is_file()
    assert builder.extract_source_bundle(first["base64"], destination, first["sha256"]) == destination


@pytest.mark.parametrize("name", ["../escape.py", "/escape.py", "devharness/../../escape.py", "devharness\\escape.py", ".env"])
def test_extractor_rejects_bad_members(tmp_path, name):
    builder = load_script("build_colab_notebook")
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr(name, "malicious")
    payload = content.getvalue()
    with pytest.raises(ValueError):
        builder.extract_source_bundle(base64.b64encode(payload).decode(), tmp_path / "extract", hashlib.sha256(payload).hexdigest())
    assert not (tmp_path / "escape.py").exists()


@pytest.mark.parametrize("kind", ["symlink", "oversized", "duplicate"])
def test_extractor_rejects_nonregular_or_excessive_members(tmp_path, kind):
    builder = load_script("build_colab_notebook")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        entry = zipfile.ZipInfo("devharness/example.py")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16 if kind == "symlink" else (stat.S_IFREG | 0o644) << 16
        archive.writestr(entry, "x" * (2_000_001 if kind == "oversized" else 1))
        if kind == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr(entry, "y")
    payload = buffer.getvalue()
    with pytest.raises(ValueError):
        builder.extract_source_bundle(base64.b64encode(payload).decode(), tmp_path / "extract", hashlib.sha256(payload).hexdigest())
    assert not (tmp_path / "extract").exists()


def test_embedded_setup_runs_locally_with_install_flags_disabled(tmp_path):
    builder = load_script("build_colab_notebook")
    source = tmp_path / "source"
    fake_project(source)
    notebook = builder.build_notebook(source)
    setup_cell = next("".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code")
    local = setup_cell.replace("Path('/content')", f"Path({str(tmp_path / 'content')!r})")
    local = local.replace("INSTALL_DEPENDENCIES = True", "INSTALL_DEPENDENCIES = False")
    local = local.replace("INSTALL_CODEX_CLI = True", "INSTALL_CODEX_CLI = False")
    subprocess.run([sys.executable, "-c", local], check=True, timeout=15, capture_output=True, text=True, cwd=tmp_path)
    assert (tmp_path / "content/devbench_repo/scripts/colab_run.py").read_text() == (PROJECT / "scripts/colab_run.py").read_text()


def test_bundled_offline_preparation_uses_fresh_process_and_twelve_synthetic_tasks(tmp_path):
    builder = load_script("build_colab_notebook")
    notebook = builder.build_notebook(PROJECT)
    setup_cell = next("".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code")
    local = setup_cell.replace("Path('/content')", f"Path({str(tmp_path / 'content')!r})")
    local = local.replace("INSTALL_DEPENDENCIES = True", "INSTALL_DEPENDENCIES = False")
    local = local.replace("INSTALL_CODEX_CLI = True", "INSTALL_CODEX_CLI = False")
    local += """
assert not any(name in sys.modules for name in ('numpy', 'pandas', 'scipy', 'devharness'))
probe = "import sys; from pathlib import Path; sys.path.insert(0, " + repr(str(PROJECT)) + "); import devharness; import devharness.data; import scripts.run_live_benchmark as coordinator; assert all(Path(module.__file__).resolve().is_relative_to(Path(" + repr(str(PROJECT)) + ")) for module in (devharness, devharness.data, coordinator))"
colab.subprocess.run([sys.executable, '-c', probe], cwd=CONTENT_ROOT, check=True, timeout=20)
colab.run_stage('prepare', CONTENT_ROOT)
prepared = json.loads((CONTENT_ROOT / 'benchmark_data' / 'prepared.json').read_text())
assert len(prepared) == 12
assert all(item['source_kind'] == 'synthetic' for item in prepared)
assert not any(name in sys.modules for name in ('numpy', 'pandas', 'scipy', 'devharness'))
"""
    # stdin keeps a self-contained bundle out of OS command-line length limits.
    result = subprocess.run([sys.executable, "-"], input=local, text=True,
                            capture_output=True, timeout=45, cwd=tmp_path)
    assert result.returncode == 0, result.stderr[-3000:]


def test_notebook_has_five_compilable_cells_and_uses_source_functions(tmp_path):
    builder = load_script("build_colab_notebook")
    fake_project(tmp_path)
    notebook = builder.build_notebook(tmp_path)
    assert json.loads(json.dumps(notebook))["nbformat"] == 4
    cells = ["".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code"]
    assert len(cells) == 5
    for code in cells:
        compile(code, "colab_cell", "exec")
    assert "colab.setup_environment" in cells[0]
    assert "colab.run_stage('prepare'" in cells[1]
    assert "colab.run_stage('ingest'" in cells[2]
    assert "colab.run_stage('execute'" in cells[3]
    assert "colab.run_stage('report'" in cells[4]
    assert "OMP_NUM_THREADS" in cells[0]
    assert "VECLIB_MAXIMUM_THREADS" in cells[0]
    assert "/content" in cells[0]
    markdown = "\n".join("".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "markdown")
    assert "interactive ChatGPT login" in markdown
    assert "credentials" in markdown
