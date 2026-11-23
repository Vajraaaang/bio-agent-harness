"""Explicit public-track configuration, guarded execution and separate reports."""
from pathlib import Path

from .contracts import canonical_json_bytes


def register_commands(commands, absolute_path):
    from .public_study import DATASETS
    plan = commands.add_parser("public-plan", help="Write a frozen public configuration and empty report; no data reads")
    plan.add_argument("--study-id", default="public-three-arm-v1")
    plan.add_argument("--dataset", choices=tuple(DATASETS), action="append")
    plan.add_argument("--model")
    plan.add_argument("--repetitions", type=int, default=1)
    plan.add_argument("--output", type=absolute_path, required=True)
    prepare = commands.add_parser("public-prepare", help="Explicit public-data preparation; no agents")
    prepare.add_argument("--plan", type=absolute_path, required=True)
    prepare.add_argument("--cache-root", type=absolute_path, required=True)
    prepare.add_argument("--output", type=absolute_path, required=True)
    prepare.add_argument("--authorize-data-execution", action="store_true")
    run = commands.add_parser("public-run", help="Explicit future live Codex public comparison")
    for name in ("plan", "bundles", "output", "workspace-root"):
        run.add_argument("--" + name, type=absolute_path, required=True)
    run.add_argument("--codex-executable", default="codex")
    run.add_argument("--max-live-attempts", type=int, default=6)
    run.add_argument("--authorize-live-participants", action="store_true")
    run.add_argument("--authorize-exec-preflight", action="store_true",
                     help="Authorize unscored permission-probe sessions; --preflight-only prevents scored execution")
    run.add_argument("--preflight-only", action="store_true", help="Probe the first task once per condition; never score")
    run.add_argument("--preflight-condition", action="append", choices=("baseline", "optional", "enforced"), help="Retry only selected unscored conditions; repeat for multiple conditions")
    run.add_argument("--max-provider-sessions", type=int, default=6, help="Cap all probe and scored provider sessions (default: 6)")
    report = commands.add_parser("public-report", help="Separate public-task report with optional verified execution records")
    for name in ("plan", "output"):
        report.add_argument("--" + name, type=absolute_path, required=True)
    report.add_argument("--records", type=absolute_path)
    report.add_argument("--execution-receipt-sha256")
    report.add_argument("--authorize-result-analysis", action="store_true")


def run_command(args):
    if args.command == "public-run" and not args.authorize_exec_preflight:
        raise PermissionError("Live execution also requires the authorization flag --authorize-exec-preflight")
    if args.command == "public-prepare" and not args.authorize_data_execution:
        raise PermissionError("Public preparation requires --authorize-data-execution")
    if args.command == "public-run" and not args.authorize_live_participants and not args.preflight_only:
        raise PermissionError("Public live agents require --authorize-live-participants")
    if args.command == "public-report" and args.records and not args.authorize_result_analysis:
        raise PermissionError("Public result reads require --authorize-result-analysis")
    from .public_study import PublicStudyPlan, default_public_plan, DATASETS, prepare_public_task, PreparedPublicTask
    from .public_reporting import write_public_report, load_public_records
    from .permissions import require_protected_storage
    from .cli import _read_json
    def fresh_output(path):
        path = Path(path)
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("Public output path must not contain symlinks")
        require_protected_storage(path)
        path.mkdir(parents=True, exist_ok=False, mode=0o700)
        return path
    if args.command == "public-plan":
        plan = default_public_plan(args.study_id, datasets=tuple(args.dataset or DATASETS), requested_model=args.model,
                                   repetitions=args.repetitions)
        output = fresh_output(args.output)
        (output / "private").mkdir(mode=0o700)
        (output / "private/plan.json").write_bytes(canonical_json_bytes(plan.model_dump(mode="json")))
        report = write_public_report(plan, output / "report")
        return {"plan": str(output / "private/plan.json"), "plan_identity": plan.sha256(), "data_executed": False,
                "model_participants_launched": 0, **report}
    plan = PublicStudyPlan.model_validate_json(canonical_json_bytes(_read_json(args.plan)))
    if args.command == "public-prepare":
        output = fresh_output(args.output)
        prepared = {"record_format": "devbench.public.prepared-study/1", "plan_identity": plan.sha256(),
                    "datasets": {}, "failures": {}, "status": "preparing"}
        try:
            for dataset in plan.datasets:
                try:
                    bundle = prepare_public_task(plan, dataset, args.cache_root, output / dataset, allow_data_execution=True)
                    prepared["datasets"][dataset] = bundle.model_dump(mode="json")
                except Exception as error:
                    prepared["failures"][dataset] = f"{type(error).__name__}: {error}"[:2000]
            prepared["status"] = "prepared" if not prepared["failures"] else "incomplete"
        finally:
            (output / "bundles.json").write_bytes(canonical_json_bytes(prepared))
        return {"manifest": str(output / "bundles.json"), "status": prepared["status"], "model_participants_launched": 0}
    if args.command == "public-run":
        from .public_execution import execute_public_study
        prepared = _read_json(args.bundles)
        if (prepared.get("record_format") != "devbench.public.prepared-study/1" or prepared.get("plan_identity") != plan.sha256()
                or prepared.get("status") != "prepared"):
            raise ValueError("Public preparations are incomplete or do not match the plan")
        bundles = {key: PreparedPublicTask.model_validate_json(canonical_json_bytes(value)) for key, value in prepared["datasets"].items()}
        return execute_public_study(plan, bundles, args.output, workspace_root=args.workspace_root,
            codex_executable=args.codex_executable, max_live_attempts=args.max_live_attempts,
            allow_data_execution=args.authorize_live_participants, allow_exec_preflight=args.authorize_exec_preflight,
            preflight_only=args.preflight_only, max_provider_sessions=args.max_provider_sessions,
            preflight_conditions=args.preflight_condition)
    if args.command == "public-report":
        if args.records is None and args.execution_receipt_sha256 is not None:
            raise ValueError("An execution receipt requires recorded results")
        records, storage = (None, {}) if args.records is None else load_public_records(args.records, plan,
            execution_receipt_sha256=args.execution_receipt_sha256, allow_result_analysis=True)
        return write_public_report(plan, args.output, records, storage=storage, allow_result_analysis=bool(args.records))
    raise ValueError("Unknown public command")
