import subprocess

import pytest


def test_snapshot_retains_packaged_analysis_resources(tmp_path):
    from devbench.engine.execution_snapshot import materialize

    project = tmp_path / "project"
    relative = (
        "devbench/analysis/assets/agentic-rna-v24-evaluation-protocol.md",
        "devbench/analysis/assets/2026-11-04-workbench-compute-contracts.schema.json",
        "devbench/analysis/assets/official-client-pins.json",
        "devbench/analysis/assets/owned-host-sentinel.txt",
    )
    for name in relative:
        source = project / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(name.encode())
    destination = tmp_path / "snapshots" / "code_snapshot"
    destination.parent.mkdir()
    materialize(project, destination)
    for name in relative:
        assert (destination / name).read_bytes() == name.encode()


def test_snapshot_executes_retained_lazy_imports_in_spawned_worker(tmp_path):
    from devbench.engine.execution_snapshot import materialize, snapshot_command, verify

    project = tmp_path / "project"
    package = project / "devbench"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    source = package / "example.py"
    source.write_text("value = 'retained'\n")
    (package / "driver.py").write_text(
        "import multiprocessing as mp\n"
        "def worker(queue):\n"
        "    from devbench.example import value\n"
        "    queue.put(value)\n"
        "def main():\n"
        "    ctx = mp.get_context('spawn')\n"
        "    queue = ctx.Queue()\n"
        "    process = ctx.Process(target=worker, args=(queue,))\n"
        "    process.start()\n"
        "    print(queue.get(timeout=10))\n"
        "    process.join(timeout=10)\n"
        "    return process.exitcode\n"
    )
    (package / "credentials.json").write_text("excluded")
    destination = tmp_path / ".runs" / "id" / "code_snapshot"
    destination.parent.mkdir(parents=True)
    identity = materialize(project, destination)
    assert not (destination / "devbench" / "credentials.json").exists()
    source.write_text("value = 'live edit'\n")
    command = snapshot_command(destination, identity, "devbench.driver", ())
    result = subprocess.run(command, cwd=project, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "retained"
    retained = destination / "devbench" / "example.py"
    retained.chmod(0o600)
    retained.write_text("value = 'tamper'\n")
    with pytest.raises(ValueError, match="snapshot"):
        verify(destination, identity)
    result = subprocess.run(command, cwd=project, text=True, capture_output=True, timeout=20)
    assert result.returncode != 0 and not result.stdout
    assert "changed before import" in result.stderr
