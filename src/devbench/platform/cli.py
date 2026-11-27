"""DevBench entry point for benchmark, Workbench, RNA workflow, and replay commands."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4


def _runtime_failure(error):
    print(f"devbench: {type(error).__name__}: {error}", file=sys.stderr)
    return 1


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else None
    local_commands = {"daemon": run_daemon, "mcp": run_mcp_shim, "approve": approve}
    if command in local_commands:
        return local_commands[command](sys.argv[2:])
    if sys.argv[1:2] == ["plugin-mcp"]:
        from .plugin_mcp import main as plugin_main

        return plugin_main(sys.argv[2:])
    if sys.argv[1:2] == ["workbench"]:
        from .workbench_ui import main as workbench_main

        workbench_main(sys.argv[2:], pin_execution=True, interactive_adaptive=True)
        return 0
    if sys.argv[1:2] == ["workflow"]:
        return run_workflow(sys.argv[2:])
    if sys.argv[1:2] == ["replay-package"]:
        return replay_package(sys.argv[2:])
    if sys.argv[1:2] == ["replay-erql"]:
        return replay_erql(sys.argv[2:])
    if sys.argv[1:2] == ["run-erql"]:
        return run_erql(sys.argv[2:])
    if sys.argv[1:2] != ["run"]:
        from experiments.run_sweep import main as run_sweep

        return run_sweep()
    return run_population(sys.argv[2:], pin_execution=True)


_DEADLINE_PROFILES = ("qc", "standard", "long")


def _daemon_parser():
    from devbench.mcp import LITERATURE_PROVIDERS

    parser = argparse.ArgumentParser(
        prog="devbench daemon", description=run_daemon.__doc__
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    start = commands.add_parser("start", help="Start or attach to the research daemon")
    status = commands.add_parser("status", help="Inspect the current daemon")
    stop = commands.add_parser("stop", help="Stop the daemon once its jobs are idle")
    for command in (start, status, stop):
        command.add_argument("--workspace", required=True, type=Path)
    start.add_argument("--input-root", type=Path)
    start.add_argument("--mounted-image-id")
    start.add_argument(
        "--allow-external-input-files", action="store_true", default=argparse.SUPPRESS
    )
    start.add_argument("--http-port", type=int, default=0)
    start.add_argument("--idle-timeout-sec", type=float, default=1800)
    start.add_argument("--offline-only", action="store_true")
    start.add_argument("--enable-streamable-http", action="store_true")
    start.add_argument(
        "--auto-approve-tier2-public", action="store_true", default=argparse.SUPPRESS
    )
    start.add_argument(
        "--literature-provider",
        choices=LITERATURE_PROVIDERS,
        help="Allow public literature queries through this fixed host broker",
    )
    for name in ("cpu-slots", "gpu-slots", "memory-budget-mb", "max-concurrent-runs"):
        start.add_argument(f"--{name}", type=int, default=argparse.SUPPRESS)
    start.add_argument("--cancel-grace-sec", dest="grace_sec", type=float)
    for profile in _DEADLINE_PROFILES:
        start.add_argument(f"--{profile}-deadline-sec", type=int)
    return parser


def run_daemon(argv):
    """Start, inspect or stop the research daemon for one workspace."""
    from devbench.daemon import ipc

    if argv and argv[0].startswith("--") and "--auto-approve-tier2-public" in argv:
        argv = ["start", *argv]
    options = vars(_daemon_parser().parse_args(argv))
    operation = options.pop("operation")
    workspace = options.pop("workspace")
    try:
        if operation == "start":
            deadlines = {}
            for profile in _DEADLINE_PROFILES:
                seconds = options.pop(f"{profile}_deadline_sec")
                if seconds is not None:
                    deadlines[profile] = seconds
            if deadlines:
                options["deadlines"] = deadlines
            options = {
                key: value for key, value in options.items() if value is not None
            }
            result = ipc.start_daemon(workspace, **options)
        elif operation == "status":
            result = ipc.daemon_status(workspace)
            if result["status"] == "running":
                result = ipc.request(workspace, "status")
        else:
            result = ipc.stop_daemon(workspace)
    except (OSError, ValueError, RuntimeError) as error:
        return _runtime_failure(error)
    print(json.dumps(result, allow_nan=False))
    return 0


def run_mcp_shim(argv):
    """Connect an MCP STDIO client to the workspace's Unix socket."""
    from devbench.mcp.shim import main as shim

    return shim(argv)


def _local_review_action(args):
    action = "approve"
    extra = {}
    if args.issue_native_confirmation:
        action = "native_confirmation"
    elif args.show_gate1_subject:
        action = "gate1_subject"
    elif args.record_gate1:
        if not args.subject_sha256:
            raise ValueError("Supply the digest of the reviewed Gate 1 packet")
        action = "gate1_review"
        extra = {
            "subject_sha256": args.subject_sha256,
            "status": args.record_gate1,
        }
    return action, extra


def approve(argv):
    """Record the local operator's approval of an exact frozen plan."""
    from devbench.daemon import ipc
    from devbench.daemon.auth import local_actor

    parser = argparse.ArgumentParser(
        prog="devbench approve", description=approve.__doc__
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument(
        "--actor", help="Operator attribution (defaults to the OS user)"
    )
    parser.add_argument("--rationale", default="")
    parser.add_argument(
        "--offline-only",
        action="store_true",
        help="Review locally with all data egress disabled",
    )
    review = parser.add_mutually_exclusive_group()
    review.add_argument(
        "--submit",
        action="store_true",
        help="After local approval, enqueue this exact frozen plan",
    )
    review.add_argument(
        "--issue-native-confirmation",
        action="store_true",
        help="Authorize a short-lived plan-bound interactive client token",
    )
    review.add_argument(
        "--show-gate1-subject",
        action="store_true",
        help="Show the preregistered design packet for a local reviewer",
    )
    review.add_argument(
        "--record-gate1",
        choices=("passed", "flagged"),
        help="Record an independent outcome-blind review",
    )
    parser.add_argument("--subject-sha256", help="Exact reviewed Gate 1 packet digest")
    args = parser.parse_args(argv)
    try:
        actor = args.actor if args.actor is not None else local_actor()
        owner = ipc.daemon_status(args.workspace)
        if (
            args.offline_only
            and owner["status"] == "running"
            and not owner["offline_only"]
        ):
            raise ValueError("Private review requires an offline daemon")
        if owner["status"] != "running":
            if not (args.workspace / "plugin-jobs.sqlite3").is_file():
                raise ValueError("No frozen plan exists in this workspace")
            from devbench.engine.scientific_authority import require_patched_wal

            from .plugin_jobs import JobStore

            env = os.environ.copy()
            ipc._macos_sqlite_environment(env)
            if env.get("DYLD_LIBRARY_PATH") != os.environ.get("DYLD_LIBRARY_PATH"):
                # A macOS shell launcher can strip DYLD_* before Python starts.
                # Run just this approval with the same patched SQLite selector.
                source = str(Path(__file__).resolve().parents[2])
                entry = (
                    f"import sys; sys.path.insert(0, {source!r}); "
                    "from devbench.platform.cli import approve; "
                    "raise SystemExit(approve(sys.argv[1:]))"
                )
                return subprocess.run(
                    [sys.executable, "-I", "-B", "-c", entry, *argv], env=env
                ).returncode
            require_patched_wal()
            if (
                args.issue_native_confirmation
                or args.record_gate1
                or args.show_gate1_subject
            ):
                raise ValueError("Start the workspace daemon to review the plan")
            jobs = JobStore(args.workspace)
            canonical_plan = (
                jobs.control_plan(args.session_id, args.plan_id) is not None
            )
            result = jobs.approve(
                args.session_id,
                args.plan_id,
                args.plan_sha256,
                actor_id=actor,
                surface="cli",
                auth_method="local_os_user",
                rationale=args.rationale,
                require_public=canonical_plan and not args.offline_only,
                offline_approval=args.offline_only,
            )
        else:
            action, extra = _local_review_action(args)
            result = ipc.request(
                args.workspace,
                action,
                **extra,
                session_id=args.session_id,
                plan_id=args.plan_id,
                plan_sha256=args.plan_sha256,
                actor_id=actor,
                rationale=args.rationale,
            )
        if args.submit:
            from .plugin_jobs import JobStore

            run = JobStore(args.workspace).launch(
                args.session_id, args.plan_id, args.plan_sha256
            )
            result = {
                key: run[key] for key in ("session_id", "plan_id", "run_id", "status")
            }
    except (OSError, ValueError, RuntimeError) as error:
        return _runtime_failure(error)
    print(json.dumps(result, allow_nan=False))
    return 0


def run_workflow(argv):
    """Inspect or advance an existing local adaptive investigation."""
    parser = argparse.ArgumentParser(
        prog="devbench workflow", description=run_workflow.__doc__
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    status = commands.add_parser(
        "status", help="Read the retained adaptive lineage status"
    )
    advance = commands.add_parser("advance", help="Dispatch admitted local OCI steps")
    for command in (status, advance):
        command.add_argument(
            "--workspace", type=Path, required=True, help="Existing method workspace"
        )
        command.add_argument(
            "--session-id",
            required=True,
            help="Exact retained scientific plan session ID",
        )
    advance.add_argument("--actor", required=True)
    advance.add_argument(
        "--parameters",
        type=Path,
        required=True,
        help="JSON array of the exact frozen step parameter objects",
    )
    advance.add_argument(
        "--max-steps", type=int, default=1, help="Step budget from 1 to 64"
    )
    advance.add_argument("--allow-data-execution", action="store_true")
    args = parser.parse_args(argv)
    writing = args.operation == "advance"
    if writing and not args.allow_data_execution:
        advance.error("Adaptive dispatch requires --allow-data-execution")
    if writing and not 1 <= args.max_steps <= 64:
        advance.error("--max-steps must be between 1 and 64")

    from devbench.engine.method_executor import _path
    from devbench.engine.methods import MethodService
    from devbench.engine.workflow_operations import LEDGER_NAME, WorkflowOperationLedger
    from devbench.engine.workspace import read_bounded_file
    from devbench.study.evidence import decode_json

    from .method_planner import ScientificMethodPlanner
    from .workflow_entrypoints import advance_adaptive_workflow

    try:
        workspace = _path(args.workspace)
        methods = MethodService(workspace, readonly=True)
        planner = ScientificMethodPlanner(methods)
        ledger = WorkflowOperationLedger(workspace / LEDGER_NAME, readonly=True)
        adaptive = planner.adaptive_workflow(args.session_id, ledger)
        if writing and adaptive.controller_state()["binding"] is not None:
            raise PermissionError(
                "The bound controller requires its own runtime capabilities"
            )
        result = adaptive.status()
        if result["revision"] == 0:
            raise ValueError("The requested session has no retained adaptive lineage")
        if writing:
            parameters = decode_json(read_bounded_file(_path(args.parameters), 65_536))
            if (
                not isinstance(parameters, list)
                or len(parameters) > 64
                or any(not isinstance(document, dict) for document in parameters)
            ):
                raise ValueError("Provide at most 64 frozen parameter objects")
            methods = MethodService(workspace, initialize=False, reconcile=False)
            planner = ScientificMethodPlanner(methods)
            ledger = WorkflowOperationLedger(workspace / LEDGER_NAME)
            executor = planner.exploratory_executor(args.session_id, ledger, parameters)
            result = advance_adaptive_workflow(
                planner,
                args.session_id,
                actor=args.actor,
                executor=executor,
                ledger=ledger,
                max_steps=args.max_steps,
                allow_data_execution=True,
                manual_local_oci=True,
            )
    except (OSError, ValueError, RuntimeError) as error:
        return _runtime_failure(error)
    fields = ("mode", "revision", "ready", "blocked", "outcomes", "execution_block")
    if writing:
        fields += ("steps_dispatched",)
    print(json.dumps({key: result[key] for key in fields}, allow_nan=False))
    return 0


def replay_package(argv):
    """Closed portable verification route, with no launch or controller mode."""
    parser = argparse.ArgumentParser(prog="devbench replay-package")
    parser.add_argument("operation", choices=("verify", "presentation"))
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--expected-root", required=True)
    parser.add_argument(
        "--audience", choices=("public", "restricted_audit"), required=True
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--audit-ledger", type=Path)
    parser.add_argument("--audit-context")
    parser.add_argument("--audit-source")
    args = parser.parse_args(argv)
    if bool(args.output) != (args.operation == "presentation"):
        parser.error("Only presentation requires a fresh --output")
    audit_fields = (args.audit_ledger, args.audit_context, args.audit_source)
    if (
        args.audience == "public"
        and any(audit_fields)
        or args.audience == "restricted_audit"
        and not all(audit_fields)
    ):
        parser.error(
            "Restricted audit requires its ledger, context and exact source; "
            "public packages have no audit settings"
        )
    import zipfile

    from devbench.analysis import bundles

    try:
        if args.audience == "public":
            result = (
                bundles.verify_public_replay_package(args.package, args.expected_root)
                if args.operation == "verify"
                else bundles.replay_public_presentation(
                    args.package, args.expected_root, args.output
                )
            )
        else:
            from devbench.analysis.evidence_access import EvidenceBroker
            from devbench.analysis.scientific_publication import AuditReplayEntitlement
            from devbench.engine.workflow_operations import WorkflowOperationLedger

            ledger = WorkflowOperationLedger(
                args.audit_ledger, readonly=True, inert=True
            )
            broker = EvidenceBroker.open_readonly(ledger.scientific)
            with ledger._database() as db:
                identity = broker._source(db, args.audit_source)["content_sha256"]
            entitlement = AuditReplayEntitlement(
                broker.delivery(args.audit_context, args.audit_source), identity
            )
            manifest = bundles.replay_package_manifest(
                args.package, args.expected_root, entitlement=entitlement
            )
            if manifest.get("format") == bundles.INVESTIGATION_REPLAY_FORMAT:
                if args.operation != "verify":
                    raise PermissionError(
                        "Investigation presentation requires a separately admitted "
                        "public replay package"
                    )
                result = bundles.verify_investigation_replay(
                    args.package, args.expected_root, entitlement=entitlement
                )
            elif manifest.get("format") == bundles.REPLAY_FORMAT:
                result = (
                    bundles.verify_replay_package(
                        args.package, args.expected_root, entitlement=entitlement
                    )
                    if args.operation == "verify"
                    else bundles.replay_package_presentation(
                        args.package,
                        args.expected_root,
                        args.output,
                        entitlement=entitlement,
                    )
                )
            else:
                raise ValueError("Unsupported replay package format")
    except (
        OSError,
        ValueError,
        RuntimeError,
        TypeError,
        KeyError,
        zipfile.BadZipFile,
    ) as error:
        return _runtime_failure(error)
    print(json.dumps(result, allow_nan=False))
    return 0


def replay_erql(argv):
    parser = argparse.ArgumentParser(
        prog="devbench replay-erql",
        description="Separate retained evidence verification, presentation and "
        "authorized synthetic replay",
    )
    parser.add_argument(
        "operation",
        choices=(
            "describe",
            "export",
            "verify",
            "presentation",
            "numerical",
            "legacy-verify",
            "legacy-import",
        ),
    )
    for option in (
        "capsule",
        "execution",
        "package",
        "output",
        "audit-ledger",
        "input",
        "grant",
        "execution-ledger",
    ):
        parser.add_argument("--" + option, type=Path)
    for option in (
        "expected-root",
        "audit-context",
        "audit-source",
        "capture-source",
        "input-context",
        "input-source",
    ):
        parser.add_argument("--" + option)
    parser.add_argument("--scope", choices=("synthetic_numerical_replay",))
    parser.add_argument("--allow-data-execution", action="store_true")
    parser.add_argument(
        "--audience", choices=("restricted_audit", "public"), default="restricted_audit"
    )
    args = parser.parse_args(argv)
    return _replay_request(parser, args, argv)


def _validate_replay_request(parser, args):
    common = {"audit_ledger", "audit_context", "audit_source"}
    fields = {
        "describe": {"capsule", "execution", *common},
        "export": {"capsule", "execution", "output", "capture_source", *common},
        "verify": {"package", "expected_root", *common},
        "presentation": {"package", "expected_root", "output", *common},
        "numerical": {
            "package",
            "expected_root",
            "output",
            "input",
            "input_context",
            "input_source",
            "grant",
            "execution_ledger",
            "scope",
            "allow_data_execution",
            *common,
        },
        "legacy-verify": {"package", "expected_root"},
        "legacy-import": {"package", "expected_root", "output", *common},
    }
    required = fields[args.operation]
    provided = {
        key
        for key, value in vars(args).items()
        if value not in (None, False) and key not in {"operation", "audience"}
    }
    if provided != required:
        parser.error(
            "This operation requires exactly: "
            + ", ".join("--" + key.replace("_", "-") for key in sorted(required))
        )
    if args.audience != "restricted_audit":
        parser.error("Synthetic replay has no admitted public package")


def _replay_request(parser, args, argv):
    from devbench.analysis import bundles
    from devbench.analysis.evidence_access import EvidenceBroker
    from devbench.analysis.scientific_publication import AuditReplayEntitlement
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    from devbench.study.evidence import _read, decode_json

    from .workflow_entrypoints import SyntheticERQLCapsule, replay_fixed_erql

    _validate_replay_request(parser, args)
    try:
        if args.operation == "legacy-verify":
            result = bundles.verify_legacy_readonly(args.package, args.expected_root)
        else:
            writing = args.operation in {"export", "numerical", "legacy-import"}
            ledger = WorkflowOperationLedger(
                args.audit_ledger, readonly=not writing, inert=not writing
            )
            broker = (
                EvidenceBroker(ledger.scientific)
                if writing
                else EvidenceBroker.open_readonly(ledger.scientific)
            )

            def entitled(source):
                with ledger._database() as db:
                    identity = broker._source(db, source)["content_sha256"]
                result = AuditReplayEntitlement(
                    broker.delivery(args.audit_context, source), identity
                )
                result.require(identity)
                return result

            entitlement = entitled(args.audit_source)
            if args.operation == "describe":
                result = SyntheticERQLCapsule(args.capsule).describe_replay(
                    args.execution, entitlement=entitlement
                )
            elif args.operation == "export":
                capture = entitled(args.capture_source)
                capsule = SyntheticERQLCapsule(args.capsule)
                result = capsule.export_replay(
                    args.execution,
                    args.output,
                    entitlement=entitlement,
                    capture_entitlement=capture,
                )
            else:
                bundles._require_replay_scope(entitlement, args.expected_root)
                if args.operation == "verify":
                    result = bundles.verify_replay_package(
                        args.package, args.expected_root, entitlement=entitlement
                    )
                elif args.operation == "presentation":
                    result = bundles.replay_package_presentation(
                        args.package,
                        args.expected_root,
                        args.output,
                        entitlement=entitlement,
                    )
                elif args.operation == "legacy-import":
                    result = bundles.import_legacy_readonly(
                        args.package,
                        args.output,
                        args.expected_root,
                        entitlement=entitlement,
                    )
                else:
                    from devbench.engine.execution_snapshot import launch

                    # Validate the separate grant and input before any snapshot launch.
                    grant = decode_json(_read(args.grant, 16_384))
                    inputs = broker.delivery(args.input_context, args.input_source)
                    from .workflow_entrypoints import validate_erql_replay

                    validate_erql_replay(
                        args.package,
                        args.expected_root,
                        entitlement=entitlement,
                        input_path=args.input,
                        input_delivery=inputs,
                        grant=grant,
                        allow_data_execution=True,
                    )
                    launch(
                        "devbench.platform.cli",
                        ["replay-erql", *argv],
                        replace_process=True,
                    )
                    result = replay_fixed_erql(
                        args.package,
                        args.expected_root,
                        args.output,
                        entitlement=entitlement,
                        input_path=args.input,
                        input_delivery=inputs,
                        grant=grant,
                        execution_ledger=args.execution_ledger,
                        allow_data_execution=True,
                    )
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as error:
        return _runtime_failure(error)
    print(json.dumps(result, allow_nan=False))
    return 0 if result.get("numerical_match", True) else 1


def run_erql(argv):
    parser = argparse.ArgumentParser(
        prog="devbench run-erql",
        description="Run a signed synthetic ERQL verification capsule",
    )
    parser.add_argument("--capsule", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scope", choices=("synthetic-verification",), required=True)
    parser.add_argument("--allow-data-execution", action="store_true")
    args = parser.parse_args(argv)
    if not args.allow_data_execution:
        parser.error("Synthetic ERQL requires --allow-data-execution")
    from devbench.engine.execution_snapshot import launch

    from .workflow_entrypoints import SyntheticERQLCapsule

    try:
        launch("devbench.platform.cli", ["run-erql", *argv], replace_process=True)
        capsule = SyntheticERQLCapsule(args.capsule)
        outcome = capsule.run(
            args.output, allow_data_execution=args.allow_data_execution
        )
        result = capsule.status(args.output)
    except (OSError, ValueError, RuntimeError) as error:
        return _runtime_failure(error)
    print(json.dumps(result, allow_nan=False))
    return 0 if outcome["status"] == "analysis_complete_pending_review" else 1


def run_population(argv, *, pin_execution=False):
    parser = argparse.ArgumentParser(
        prog="devbench run",
        description="Run a frozen scRNA population study locally or open its "
        "Workbench session.",
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--study-request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-data-execution", action="store_true")
    parser.add_argument(
        "--model", help="Configure a Codex model for an interactive Workbench session"
    )
    parser.add_argument(
        "--agent-workspaces", type=Path, help="Protected actor root for Workbench"
    )
    parser.add_argument("--allow-model-execution", action="store_true")
    parser.add_argument(
        "--port",
        type=int,
        help="Workbench loopback port; defaults to an available port",
    )
    args = parser.parse_args(argv)
    if not args.allow_data_execution:
        parser.error("Population execution requires --allow-data-execution")
    if args.model and (not args.allow_model_execution or args.agent_workspaces is None):
        parser.error("--model requires --allow-model-execution and --agent-workspaces")
    if not args.model and (
        args.allow_model_execution
        or args.agent_workspaces is not None
        or args.port is not None
    ):
        parser.error("Workbench settings require --model")

    if pin_execution:
        from devbench.engine.execution_snapshot import launch

        try:
            launch("devbench.platform.cli", ["run", *argv], replace_process=True)
        except (OSError, ValueError, RuntimeError) as error:
            return _runtime_failure(error)

    from devbench.analysis.method_executor import _path

    from .workbench_singlecell import load_singlecell_trial_request

    try:
        request = load_singlecell_trial_request(args.study_request)
        dataset = _path(args.dataset)
        output = _path(args.output)
    except (OSError, ValueError, RuntimeError) as error:
        return _runtime_failure(error)

    if dataset != Path(request["source"]):
        parser.error("--dataset must match the frozen study request")
    if dataset.is_relative_to(output) or args.study_request.is_relative_to(output):
        parser.error("Output must be separate from the supplied inputs")
    try:
        return _run_population_request(args, request, dataset, output)
    except (OSError, ValueError, RuntimeError) as error:
        return _runtime_failure(error)


def _run_population_request(args, request, dataset, output):
    from devbench.analysis.method_executor import _path
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.engine.methods import MethodService

    from .population_verification import verify_population_execution
    from .workbench_singlecell import bind_singlecell_trial, population_input_binding

    if args.model:
        from .workbench_ui import main as workbench_main

        return workbench_main(
            [
                "--workspace",
                str(output),
                "--input-root",
                str(dataset.parent),
                "--singlecell-trial-request",
                str(args.study_request),
                "--enable-scientific",
                "--allow-data-execution",
                "--codex-model",
                args.model,
                "--agent-workspaces",
                str(_path(args.agent_workspaces)),
                "--model-data-scope",
                request["user_declarations"]["data_classification"],
                "--allow-model-execution",
                "--port",
                str(args.port if args.port is not None else 0),
                "--open-browser",
            ]
        )
    binding = bind_singlecell_trial(
        request,
        project_id=request.get("project_id") or uuid4().hex,
        input_roots=(dataset.parent,),
    )
    output.mkdir(mode=0o700)
    actor = "local-population-verification"
    methods = MethodService(output / "methods")
    artifact = methods.import_artifact(dataset, artifact_type="anndata", actor=actor)
    planner = ScientificMethodPlanner(methods)
    plan = planner.create(
        {
            "scientific_objective": request["question"],
            "computational_task": "experimental_scRNA_contrast",
        },
        {
            "observed": {},
            "inferred": {},
            "unknowns": [],
            "artifact_ids": [artifact["artifact_id"]],
        },
        actor=actor,
        data_classification=request["user_declarations"]["data_classification"],
        evaluation_purpose="execution_verification",
        rna_input_contract=population_input_binding(
            {"singlecell_trial": binding}, artifact["artifact_id"]
        ),
    )
    result = verify_population_execution(
        planner, plan["session_id"], actor=actor, allow_data_execution=True
    )
    from devbench.analysis.checkpoints import atomic_json

    atomic_json(output / "execution.json", result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "session_id": plan["session_id"],
                "execution_record": str(output / "execution.json"),
            },
            allow_nan=False,
        )
    )
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
