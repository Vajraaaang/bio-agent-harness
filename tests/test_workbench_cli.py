"""Workbench launcher and trusted per-job input settings, without model calls."""

import json
import platform
import sys

import pytest

from devbench.analysis import workbench_proposals
from devbench.engine import execution_snapshot
from devbench.platform import cli, workbench_ui
from devharness_controlled import native_runtime
from devharness_controlled.native_contracts import (
    kernel_test_platform,
    limma_voom_kernel_images,
)


def _arguments(root, *options):
    inputs = root / "inputs"
    inputs.mkdir(exist_ok=True)
    return [
        "--workspace",
        str(root / "workspace"),
        "--input-root",
        str(inputs),
        *options,
    ]


class _Server:
    server_address = ("127.0.0.1", 43123)

    def serve_forever(self):
        pass

    def server_close(self):
        pass


def _run_cli(monkeypatch, args):
    launches = []
    monkeypatch.setattr(sys, "argv", ["devbench", "workbench", *args])
    monkeypatch.setattr(
        execution_snapshot, "launch", lambda *a, **k: launches.append((a, k))
    )
    assert cli.main() == 0
    assert launches == [
        (("devbench.platform.cli", ["workbench", *args]), {"replace_process": True})
    ]


@pytest.mark.wal
def test_workbench_cli_sets_runtime_options_without_model_invocation(
    workspace_tmp_path, monkeypatch
):
    root = workspace_tmp_path
    image = min(limma_voom_kernel_images())
    args = _arguments(
        root,
        "--enable-scientific",
        "--allow-data-execution",
        "--codex-model",
        "fixture-model",
        "--agent-workspaces",
        str(root / "actors"),
        "--allow-model-execution",
        "--model-data-scope",
        "public",
        "--mounted-image-id",
        image,
        "--port",
        "0",
    )

    def bind(app, *, port):
        assert port == 0 and app.interactive_adaptive is True
        assert app.scientific is not None and app.mounted_image_id == image
        assert (
            app.allow_data_execution
            and app.allow_model_execution
            and app.model_data_scope == "public"
        )
        assert app.proposal_selector.requested_model == "fixture-model"
        assert app.proposal_selector.workspace_parent == root / "actors"
        config = app.configuration()
        assert not config["scientific"]["initialized"]
        assert config["investigations"]["adaptive_interactive"] is True
        assert config["investigations"]["auto_advance"] is True
        return _Server()

    monkeypatch.setattr(workbench_ui, "make_server", bind)
    monkeypatch.setattr(
        native_runtime,
        "LocalDocker",
        lambda: pytest.fail("Explicit pin queried Docker"),
    )
    _run_cli(monkeypatch, args)


@pytest.mark.wal
@pytest.mark.parametrize("present", [False, True])
def test_first_class_workbench_only_detects_registered_local_image(
    workspace_tmp_path, monkeypatch, present
):
    candidates = limma_voom_kernel_images()
    expected = min(candidates) if present else None
    queries = []

    class Docker:
        def _call(self, command, **_):
            assert command[:2] == ["image", "inspect"]
            assert command[2] in candidates
            queries.append(command[2])
            return command[2] if command[2] == expected else "sha256:" + "a" * 64

    def bind(app, *, port):
        assert app.mounted_image_id == expected
        assert not app.allow_data_execution and not app.allow_model_execution
        return _Server()

    monkeypatch.setattr(native_runtime, "LocalDocker", Docker)
    monkeypatch.setattr(workbench_ui, "make_server", bind)
    _run_cli(monkeypatch, _arguments(workspace_tmp_path, "--enable-scientific"))
    assert queries and (expected is None or queries[-1] == expected)


def test_local_image_detection_prefers_native_architecture(monkeypatch):
    from devharness_controlled.native_contracts import ROUTED_DE_KERNEL_IMAGES

    class Docker:
        def _call(self, command, **_):
            return command[2]

    monkeypatch.setattr(native_runtime, "LocalDocker", Docker)
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    image_id = workbench_ui._detect_mounted_image()
    assert kernel_test_platform(image_id) == "linux/arm64"
    assert image_id == ROUTED_DE_KERNEL_IMAGES["linux/arm64"]


def test_cli_rejects_unscoped_model_permission_before_starting(
    workspace_tmp_path, monkeypatch, capsys
):
    args = _arguments(workspace_tmp_path, "--allow-model-execution")
    monkeypatch.setattr(sys, "argv", ["devbench", "workbench", *args])
    monkeypatch.setattr(
        execution_snapshot,
        "launch",
        lambda *_a, **_k: pytest.fail("Unsafe startup launched"),
    )
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "--allow-model-execution" in capsys.readouterr().err
    assert not (workspace_tmp_path / "workspace").exists()


def test_cli_project_catalog_isolates_and_releases_project_storage(
    workspace_tmp_path, monkeypatch
):
    root = workspace_tmp_path
    projects = root / "projects"
    args = _arguments(
        root,
        "--projects-root",
        str(projects),
        "--codex-model",
        "fixture-model",
        "--agent-workspaces",
        str(root / "actors"),
        "--allow-model-execution",
        "--model-data-scope",
        "synthetic",
    )
    defaults = []

    def bind(app, *, port, project_hub):
        assert project_hub.default_project_id is not None
        assert not project_hub.read_only() and project_hub.resolve() is app
        records = project_hub.list_projects()["projects"]
        retained = project_hub.resolve(
            next(record["id"] for record in records if record["read_only"])
        )
        assert retained.workspace == root / "workspace"
        other = next((record for record in records if record["name"] == "Other"), None)
        if other is None:
            other = project_hub.create({"name": "Other"})
        first, second = app, project_hub.resolve(other["id"])
        assert first.workspace != second.workspace
        assert first.input_roots != second.input_roots
        assert (
            first.proposal_selector.workspace_parent
            != second.proposal_selector.workspace_parent
        )
        for project in (first, second):
            assert project.interactive_adaptive and project.allow_model_execution
            assert project.model_data_scope == "synthetic"
            assert project._executor is project_hub.executor
            assert all(
                path in project.proposal_selector.denied_roots
                for path in project_hub.denied_roots
            )
            proposal = project.configuration()["proposal_selection"]
            assert "denied_roots" not in proposal["configuration"]
            assert (
                "adaptive_interactive" not in project.configuration()["investigations"]
            )
            with pytest.raises(RuntimeError, match="already owned"):
                workbench_ui.WorkbenchService(project.workspace, project.input_roots)
        defaults.append(project_hub.default_project_id)
        return _Server()

    monkeypatch.setattr(workbench_ui, "make_server", bind)
    _run_cli(monkeypatch, args)
    _run_cli(monkeypatch, args)
    assert defaults[0] == defaults[1]


def test_job_input_overrides_are_validated_and_frozen_to_registered_job(
    workspace_tmp_path, monkeypatch
):
    root = workspace_tmp_path
    args = _arguments(root)
    source = root / "inputs" / "fixture.h5ad"
    source.touch()
    monkeypatch.setattr(
        workbench_ui.WorkbenchService, "_runtime_identity", staticmethod(lambda: {})
    )
    app = workbench_ui.WorkbenchService(args[1], [args[3]])

    def prepare(output, *, parameters, **_):
        output.mkdir()
        proposal = {"status": "awaiting_operator_review", "parameters": parameters}
        (output / "proposal.json").write_text(json.dumps(proposal))
        return {}

    monkeypatch.setattr(workbench_proposals, "prepare_workbench_proposal", prepare)
    monkeypatch.setattr(app, "_submit", lambda _job, work: work())
    request = {
        "source": str(source),
        "expected_sha256": "a" * 64,
        "question": "Inspect counts",
        "declarations": {
            "assay": "scrna",
            "measurement": "umi_counts",
            "count_provenance": "Fixture",
            "capture_unit_key": "donor",
            "capture_units_confirmed": True,
            "annotation_provenance": "Unavailable",
        },
    }
    try:
        options = {"batch_key": "donor", "capture_key": "donor", "counts_layer": None}
        altered = app.register(request, input_parameters=options)
        unchanged = app.register(request)
        assert altered["id"] != unchanged["id"]
        prepared = app._status(altered["id"])["proposal"]["parameters"]
        assert {key: prepared[key] for key in options} == options
        assert app.parameters["counts_layer"] == "counts"
        for invalid in ({"memory_mb": 10}, {"counts_layer": "nested/path"}):
            with pytest.raises(ValueError, match="input settings|counts_layer"):
                app.register(request, input_parameters=invalid)
        with pytest.raises(ValueError, match="Registration fields"):
            app.register({**request, "input_parameters": options})
        marker = json.loads((app.workspace / "service.json").read_text())
        assert marker["parameters"]["batch_key"] == "batch"
    finally:
        app.close()
