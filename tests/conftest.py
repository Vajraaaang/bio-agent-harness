"""Shared runtime checks and protected test workspaces."""

import http.client
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    if config.getoption("collectonly"):
        return
    wal_tests = [item for item in items if item.get_closest_marker("wal") is not None]
    if not wal_tests:
        return

    from devbench.engine.scientific_authority import require_patched_wal
    from devbench.engine.workflow_operations import OperationConflict

    try:
        require_patched_wal()
    except OperationConflict as error:
        reason = f"{error}. Loaded SQLite {sqlite3.sqlite_version}."
        skip_flag = "DEVBENCH_SKIP_UNPATCHED_WAL_TESTS"
        if os.environ.get(skip_flag) != "1":
            raise pytest.UsageError(
                f"{reason}\nRun `make test` to use the verified runtime. "
                f"For a non-WAL run, set {skip_flag}=1 to skip @wal tests explicitly."
            ) from error
        skip = pytest.mark.skip(reason=f"{skip_flag}=1. {reason}")
        for item in wal_tests:
            item.add_marker(skip)


@contextmanager
def _workspace(config):
    from devbench.engine.permissions import require_protected_storage

    # An evidence-collection plugin may explicitly allocate and retain fixtures.
    # Ordinary pytest runs always clean up their own workspace.
    allocate = getattr(config, "_devbench_retained_workspace_factory", None)
    if allocate is not None:
        with allocate() as path:
            yield require_protected_storage(path)
        return

    parent = Path(__file__).resolve().parents[1] / ".pytest-workspaces"
    if parent.is_symlink():
        raise ValueError("Protected test workspace root cannot be a symlink")
    parent.mkdir(mode=0o700, exist_ok=True)
    parent = require_protected_storage(parent)
    with tempfile.TemporaryDirectory(prefix="", dir=parent) as directory:
        yield Path(directory)


@pytest.fixture
def workspace_tmp_path(pytestconfig):
    """An isolated, auto-cleaned path outside shared OS scratch."""
    with _workspace(pytestconfig) as path:
        yield path


@pytest.fixture(scope="module")
def module_workspace_tmp_path(pytestconfig):
    with _workspace(pytestconfig) as path:
        yield path


@pytest.fixture(autouse=True)
def _legacy_workbench_clients(request):
    """Enable authenticated clients for the existing authorized route suites."""
    route_suites = {
        "test_bulk_plot_workbench",
        "test_evidence_disclosure_reservations",
        "test_plugin_graph_studio",
        "test_plugin_mcp_server",
        "test_scientific_plot_http_closeout",
        "test_scientific_publication_policy",
        "test_scientific_workspace_http",
        "test_scientific_workspace_ui",
        "test_thesis_workbench_acceptance",
        "test_workbench_descriptive_ui",
        "test_workbench_input_import",
        "test_workbench_local_analysis",
        "test_workbench_local_analyst",
        "test_workbench_operator_runtime",
        "test_workbench_project_http",
        "test_workbench_pseudobulk",
        "test_workbench_question_intake",
        "test_workbench_shared_motor_umap_ui",
        "test_workbench_ui",
        "test_workbench_ui_organization",
    }
    if request.node.path.stem in route_suites:
        request.getfixturevalue("authenticated_workbench_client")


@pytest.fixture(autouse=True)
def _legacy_exploratory_operator(request, monkeypatch):
    """Supply the human approval in specific historical synthetic lifecycle tests."""
    scenarios = {
        "test_rna_v24_replay": {
            "test_unpublished_intermediate_oci_output_has_owner_resolved_replay",
            "test_failed_and_partial_oci_attempts_require_receipt_and_io_closure",
            "test_unfinished_attempts_preserve_unknown_vs_never_launched",
            "test_shared_output_bytes_preserve_each_original_attempt_use",
            "test_original_input_alias_is_refused_before_attachment_read_or_object_decode",
            "test_every_original_output_port_and_use_is_required",
            "test_original_access_metadata_is_validated_before_classification_and_decoding",
        },
        "test_two_agent_investigation_v24": {
            "test_completion_requires_current_reviews_and_no_pending_work",
            "test_controller_output_delivery_preserves_authoritative_source_ancestry",
            "test_mixed_scope_keeps_eligible_computation_and_withholds_other_output",
            "test_productive_controller_refines_graph_sensitivity_sweep_and_independent_branch",
            "test_requested_resolving_computation_runs_and_fresh_challenger_resolves_hold",
        },
        "test_investigation_metamorphic": {
            "test_donor_correction_invalidates_actual_oci_dependents",
            "test_claim_only_basis_hold_preserves_actual_oci_computation",
            "test_failed_and_omitted_actual_sweep_members_do_not_improve_completeness",
        },
        "test_rna_diagnostic_policy_v24": {
            "test_real_oci_action_binding_survives_reconstructed_staging_and_checks_acceptance",
            "test_conditional_oci_diagnostic_deadline_at_real_execution_boundaries",
        },
    }
    if request.node.originalname not in scenarios.get(request.node.path.stem, ()):
        return

    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.engine.methods import MethodService
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

    roots = tuple(
        request.getfixturevalue(name).resolve()
        for name in ("tmp_path", "workspace_tmp_path")
        if name in request.fixturenames
    )
    versions = set()
    register = MethodService.register_method
    admit = AdaptiveWorkflow._dispatch_code

    def register_synthetic(service, value, *, source, environment_lock=None, actor):
        method = register(
            service,
            value,
            source=source,
            environment_lock=environment_lock,
            actor=actor,
        )
        if any(
            service.root.is_relative_to(root)
            and Path(source).resolve().is_relative_to(root)
            for root in roots
        ):
            versions.add((service.root, method["version_id"]))
        return method

    def operator_review(flow, executor, envelope):
        node = envelope["step"]
        if type(executor) is ExploratoryOCI and not node["held_out"]:
            assert (executor.methods.root, node["oci_method_version_id"]) in versions
            executor.approve_step(
                flow,
                node["step_id"],
                actor_id="synthetic-test-operator",
                data_classification="synthetic",
            )
        return admit(flow, executor, envelope)

    monkeypatch.setattr(MethodService, "register_method", register_synthetic)
    monkeypatch.setattr(AdaptiveWorkflow, "_dispatch_code", operator_review)


@pytest.fixture
def authenticated_workbench_client(monkeypatch):
    """Supply a bearer for a test-owned listener when the caller omits one."""
    from devbench.platform import workbench_http

    listeners = {}
    explicit_auth = set()
    make_server = workbench_http.make_workbench_server
    putrequest = http.client.HTTPConnection.putrequest
    putheader = http.client.HTTPConnection.putheader
    endheaders = http.client.HTTPConnection.endheaders

    def bind(*args, **kwargs):
        server = make_server(*args, **kwargs)
        listeners[server.server_address] = server
        return server

    def begin(connection, *args, **kwargs):
        explicit_auth.discard(connection)
        return putrequest(connection, *args, **kwargs)

    def header(connection, name, *values):
        putheader(connection, name, *values)
        if name.lower() in ("authorization", b"authorization"):
            explicit_auth.add(connection)

    def finish(connection, *args, **kwargs):
        server = listeners.get((connection.host, connection.port))
        if (
            connection not in explicit_auth
            and server is not None
            and server.fileno() >= 0
        ):
            putheader(connection, "Authorization", f"Bearer {server.daemon_token}")
        explicit_auth.discard(connection)
        return endheaders(connection, *args, **kwargs)

    monkeypatch.setattr(workbench_http, "make_workbench_server", bind)
    monkeypatch.setattr(http.client.HTTPConnection, "putrequest", begin)
    monkeypatch.setattr(http.client.HTTPConnection, "putheader", header)
    monkeypatch.setattr(http.client.HTTPConnection, "endheaders", finish)


@pytest.fixture
def installed_hosted_wheel():
    """Require installed module origin when the product checks its wheel RECORD."""
    from devbench.analysis import rna_trace_contract

    module_path = Path(rna_trace_contract.__file__).resolve()
    if not module_path.is_relative_to(Path(sys.prefix).resolve()):
        pytest.skip(
            "Runtime identity verification requires a non-editable devharness wheel"
        )
