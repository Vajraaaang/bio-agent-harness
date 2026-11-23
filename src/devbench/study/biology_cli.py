"""Broad biology study commands, separate from the classic temporal study."""
from pathlib import Path

from .contracts import canonical_json_bytes


def register_commands(commands, absolute_path):
    release = commands.add_parser("biology-release-checkpoint", help="Release or stop an existing waiting coordinator; never restarts a process")
    release.add_argument("--output", type=absolute_path, required=True)
    release.add_argument("--checkpoint-sha256", required=True)
    release.add_argument("--decision", choices=("continue","stop"), required=True)
    review = commands.add_parser("biology-review-template", help="Create an unscored, condition-blinded human review packet")
    review.add_argument("--answer", type=absolute_path, required=True)
    review.add_argument("--attempt-id", required=True)
    review.add_argument("--output", type=absolute_path, required=True)
    review.add_argument("--authorize-result-analysis", action="store_true")
    public = commands.add_parser("biology-public-check", help="Inspect cached public metadata/count formats; no fitting or agents")
    public.add_argument("--cache-root", type=absolute_path, required=True)
    public.add_argument("--output", type=absolute_path, required=True)
    public.add_argument("--authorize-public-data-read", action="store_true")
    plan = commands.add_parser("biology-plan", help="Configure biological coverage and an empty dashboard; no data")
    plan.add_argument("--coverage", choices=("representative", "full"), default="full")
    plan.add_argument("--study-id", default="biology-three-arm-v1")
    plan.add_argument("--cohorts-per-case", type=int, default=1)
    plan.add_argument("--repetitions", type=int, default=1)
    plan.add_argument("--genes", type=int, default=2000)
    plan.add_argument("--case", action="append", help="Select exact catalog cases; repeat to create a small paired pilot")
    seeds = plan.add_mutually_exclusive_group()
    seeds.add_argument("--seed-base", type=int, help="Optional explicit future cohort seeds; no random data drawn")
    seeds.add_argument("--fresh-seeds", action="store_true", help="Freeze fresh private seeds without generating data")
    plan.add_argument("--stress-variants", action="store_true", help="Add declared signal, dispersion and nuisance variants")
    plan.add_argument("--model", help="Exact future requested model; no model is launched")
    plan.add_argument("--output", type=absolute_path, required=True)
    schema = commands.add_parser("biology-schema", help="Print a strict biological schema without reading data")
    schema.add_argument("kind", choices=("plan", "answer", "attempt"))
    validate = commands.add_parser("biology-validate", help="Validate a frozen biological configuration only")
    validate.add_argument("--plan", type=absolute_path, required=True)
    prepare = commands.add_parser("biology-prepare", help="Explicit future supervised generation; no agents")
    prepare.add_argument("--plan", type=absolute_path, required=True)
    prepare.add_argument("--output", type=absolute_path, required=True)
    prepare.add_argument("--authorize-data-execution", action="store_true")
    prepare.add_argument("--evaluation-only", action="store_true", help="Generate only evaluation cohorts; preserve reserved calibration/training cohorts")
    run = commands.add_parser("biology-run", help="Run the frozen biological study through live Codex")
    for field in ("plan", "bundles", "output", "workspace-root"):
        run.add_argument("--" + field, type=absolute_path, required=True)
    run.add_argument("--codex-executable", default="codex")
    run.add_argument("--expected-codex-sha256", help="Reject a different executable build before reference preparation or model dispatch")
    run.add_argument("--pause-after-attempts", type=int, help="Pause the same coordinator after this many workers; release using its checkpoint receipt")
    run.add_argument("--checkpoint-timeout-sec", type=int, default=300)
    run.add_argument("--max-live-attempts", type=int, default=6,
                     help="Hard study launch cap; larger frozen schedules fail before dispatch (default: 6)")
    run.add_argument("--authorize-data-execution", action="store_true")
    run.add_argument("--authorize-exec-preflight", action="store_true",
                     help="Authorize unscored permission-probe sessions; --preflight-only prevents scored execution")
    run.add_argument("--preflight-only", action="store_true", help="Probe the first task once per condition; never score")
    run.add_argument("--preflight-condition", action="append", choices=("baseline", "optional", "enforced"), help="Retry only selected unscored conditions; repeat for multiple conditions")
    run.add_argument("--max-provider-sessions", type=int, default=6, help="Cap all probe and scored provider sessions (default: 6)")
    run.add_argument("--stop-on-infrastructure-failure", action="store_true",
                     help="Stop further scored dispatch after any infrastructure/capture failure; scientific scores do not control stopping")
    run.add_argument("--participant-runtime-profile", choices=("host", "bulk"), default="host",
                     help="bulk stages installed scientific dependencies only, identically for all scored study conditions")
    report = commands.add_parser("biology-report", help="Render an empty plan or explicitly analyze measured records")
    report.add_argument("--plan", type=absolute_path, required=True)
    report.add_argument("--records", type=absolute_path)
    report.add_argument("--output", type=absolute_path, required=True)
    report.add_argument("--task-id", help="Load retained predicted details for one opaque task identity")
    report.add_argument("--execution-receipt-sha256", help="Separately retained SHA-256 of execution.json")
    report.add_argument("--authorize-result-analysis", action="store_true")
    report.add_argument("--reasoning-reviews", type=absolute_path, help="Version-2 batch with two prespecified reviewers and raw ratings; requires retained execution receipt")


def _dashboard(plan, analysis, output, *, allow_result_reporting=False):
    from .biology_dashboard import build_biology_dashboard
    from .biology_registry import biology_runtime_registry, biology_method_metadata
    return build_biology_dashboard(plan, analysis, output, registry=biology_runtime_registry(),
        method_metadata=biology_method_metadata(), allow_result_reporting=allow_result_reporting)


def run_command(args):
    if args.command == "biology-release-checkpoint":
        from .checkpoint import release_checkpoint
        return release_checkpoint(args.output,args.checkpoint_sha256,args.decision)
    if args.command == "biology-review-template":
        if not args.authorize_result_analysis:
            raise PermissionError("Review packets require --authorize-result-analysis")
        from .evidence import _read, decode_json
        from .reasoning_review import reasoning_review_template
        from .permissions import require_protected_storage
        packet = reasoning_review_template(decode_json(_read(Path(args.answer), 8_000_000)), args.attempt_id)
        destination = require_protected_storage(Path(args.output))
        destination.parent.mkdir(parents=True,exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(canonical_json_bytes(packet))
        return {"packet":str(destination),"status":"requires_human_adjudication","model_calls":0}
    # Authorization comes before even the first plan/data/result file read.
    if args.command == "biology-run" and not args.authorize_exec_preflight:
        raise PermissionError("Live execution also requires the authorization flag --authorize-exec-preflight")
    if args.command == "biology-public-check":
        if not args.authorize_public_data_read:
            raise PermissionError("Public inspection requires the --authorize-public-data-read authorization flag")
        from .public_tracks import inspect_public_tracks
        manifest = inspect_public_tracks(args.cache_root, allow_public_data_read=True)
        if any(path.is_symlink() for path in (args.output, *args.output.parents)):
            raise ValueError("Public manifest output must not contain symlinks")
        args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with args.output.open("xb") as output:
            output.write(canonical_json_bytes(manifest))
        return {"manifest": str(args.output), "datasets": len(manifest["datasets"]),
                "statistical_analysis_performed": False, "live_agents_launched": 0}
    if (args.command in {"biology-prepare", "biology-run"} and not args.authorize_data_execution
            and not (args.command == "biology-run" and args.preflight_only)):
        raise PermissionError("This command requires the explicit --authorize-data-execution authorization flag")
    if args.command == "biology-report" and args.records and not args.authorize_result_analysis:
        raise PermissionError("Recorded result analysis requires the --authorize-result-analysis authorization flag")
    from .biology_planning import (BiologyStudyPlan, default_biology_study, validate_biology_plan,
                                   schedule_biology_attempts, assign_biology_seeds, prepare_biology_plan,
                                   assign_fresh_biology_seeds, add_biology_stress_variants)
    from .cli import _read_json
    if args.command == "biology-schema":
        from .biology_answers import BiologyAnswer
        from .biology_records import BiologicalAttemptRecord
        return {"plan": BiologyStudyPlan, "answer": BiologyAnswer,
                "attempt": BiologicalAttemptRecord}[args.kind].model_json_schema()
    if args.command == "biology-plan":
        from .permissions import require_protected_storage
        plan = default_biology_study(args.study_id, coverage=args.coverage,
            cohorts_per_case=args.cohorts_per_case, repetitions=args.repetitions,
            num_genes=args.genes, requested_model=args.model)
        if args.case:
            wanted = set(args.case)
            available = {recipe.case_id for recipe in plan.recipes}
            if wanted - available:
                raise ValueError("Requested cases are absent from the selected coverage: " + ", ".join(sorted(wanted-available)))
            payload = plan.model_dump(mode="json")
            payload["recipes"] = [recipe for recipe in payload["recipes"] if recipe["case_id"] in wanted]
            plan = validate_biology_plan(BiologyStudyPlan.model_validate_json(canonical_json_bytes(payload)))
        if args.stress_variants:
            plan = add_biology_stress_variants(plan)
        if args.fresh_seeds:
            plan = assign_fresh_biology_seeds(plan)
        elif args.seed_base is not None:
            plan = assign_biology_seeds(plan, args.seed_base)
        output = Path(args.output)
        if any(path.is_symlink() for path in (output, *output.parents)):
            raise ValueError("Study configuration output must not contain symlinks")
        output = require_protected_storage(output)
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        receipt = prepare_biology_plan(plan, output / "private")
        report = _dashboard(plan, None, output / "report")
        return {"data_executed": False, "model_participants_launched": 0,
                "plan": str(output / "private" / "plan.json"), "preparation_receipt": receipt,
                "report": report, "clinical_qualification": False}
    plan = validate_biology_plan(BiologyStudyPlan.model_validate_json(canonical_json_bytes(_read_json(args.plan))))
    if args.command == "biology-validate":
        return {"valid_configuration": True, "study_id": plan.study_id, "plan_identity": plan.sha256(),
                "configured_cases": len({recipe.case_id for recipe in plan.recipes}),
                "configured_cohorts": len(plan.recipes), "planned_attempts": len(schedule_biology_attempts(plan)),
                "all_generation_seeds_assigned": all(recipe.generation_seed is not None for recipe in plan.recipes),
                "data_executed": False, "calibration_status": plan.calibration_status}
    if args.command == "biology-prepare":
        from .biology_preparation import prepare_biology_study_data
        return prepare_biology_study_data(plan, args.output, allow_data_execution=True, evaluation_only=args.evaluation_only)
    if args.command == "biology-run":
        from .biology_datasets import PreparedBiologyBundle
        from .biology_execution import execute_biology_study
        manifest = _read_json(args.bundles, 32_000_000)
        if (manifest.get("record_format") != "devbench.biology.prepared-study/1"
                or manifest.get("plan_identity") != plan.sha256()):
            raise ValueError("Prepared biological bundles do not match the frozen plan")
        bundles = {key: PreparedBiologyBundle.model_validate_json(canonical_json_bytes(value))
                   for key, value in manifest["cohorts"].items()}
        return execute_biology_study(plan, bundles, args.output, workspace_root=args.workspace_root,
                                    codex_executable=args.codex_executable, max_live_attempts=args.max_live_attempts,
                                    allow_data_execution=args.authorize_data_execution, allow_exec_preflight=args.authorize_exec_preflight,
                                    preflight_only=args.preflight_only, max_provider_sessions=args.max_provider_sessions,
                                    preflight_conditions=args.preflight_condition, pause_after_attempts=args.pause_after_attempts,
                                    checkpoint_timeout_sec=args.checkpoint_timeout_sec,expected_codex_sha256=args.expected_codex_sha256,
                                    stop_on_infrastructure_failure=args.stop_on_infrastructure_failure,
                                    participant_runtime_profile=args.participant_runtime_profile)
    if args.command == "biology-report":
        if args.reasoning_reviews is not None and (args.records is None or not args.authorize_result_analysis or args.execution_receipt_sha256 is None):
            raise PermissionError("Reasoning reviews require records, result-analysis authorization and the retained execution receipt")
        from .biology_metrics import analyze_biology_attempts
        from .biology_reporting import load_biology_report_records
        if args.records is None and (args.task_id is not None or args.execution_receipt_sha256 is not None):
            raise ValueError("Detail and receipt options require a recorded result file")
        records, storage = ([], {}) if args.records is None else load_biology_report_records(args.records, plan,
            task_id=args.task_id, execution_receipt_sha256=args.execution_receipt_sha256,
            allow_result_analysis=True)
        summary = analyze_biology_attempts(records, plan, allow_result_analysis=bool(args.records))
        summary.update(storage)
        rating_audit = None
        if args.reasoning_reviews is not None:
            from .reasoning_review import apply_reasoning_reviews, write_reasoning_review_audit
            from .evidence import _read, decode_json
            summary = apply_reasoning_reviews(summary, records,
                decode_json(_read(Path(args.reasoning_reviews), 4_000_000)),
                retained_root_verified=storage.get("retained_root_verified") is True)
            rating_audit = write_reasoning_review_audit(summary, Path(args.output).with_name(Path(args.output).name + "-review-audit"))
        report = _dashboard(plan, summary, args.output, allow_result_reporting=bool(args.records))
        if rating_audit is not None:report["private_reasoning_review_audit"] = rating_audit
        return report
    raise ValueError("Unknown biological study command")
