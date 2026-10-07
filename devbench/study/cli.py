"""Explicit study commands. Planning never launches data or model execution."""
import argparse
import json
from pathlib import Path
import sys

from .contracts import StudyPlan, canonical_json_bytes


def absolute_path(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise argparse.ArgumentTypeError("Use an absolute path without parent traversal")
    return path


def build_parser():
    parser = argparse.ArgumentParser(prog="devbench-study", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Write only a private study plan and empty public dashboard")
    plan.add_argument("--stage", choices=("smoke", "diagnostic", "exploratory"), default="exploratory")
    plan.add_argument("--study-id", default="inference-three-arm-v1")
    plan.add_argument("--model", help="Exact future requested model identifier; no model is launched")
    plan.add_argument("--output", type=absolute_path, required=True)
    schema = commands.add_parser("schema", help="Print a schema without reading data")
    schema.add_argument("kind", choices=("plan", "answer", "attempt"))
    validate = commands.add_parser("validate-plan", help="Validate configuration and print safe counts")
    validate.add_argument("--plan", type=absolute_path, required=True)
    prepare = commands.add_parser("prepare-data", help="Explicit future dataset preparation, never a model run")
    prepare.add_argument("--plan", type=absolute_path, required=True)
    prepare.add_argument("--output", type=absolute_path, required=True)
    prepare.add_argument("--authorize-data-execution", action="store_true")
    public = commands.add_parser("import-public", help="Verify and copy a preprepared local public subset; no downloads")
    for name in ("plan", "input-bundle", "output", "provenance"):
        public.add_argument("--" + name, type=absolute_path, required=True)
    public.add_argument("--cohort-id", required=True)
    public.add_argument("--authorize-data-execution", action="store_true")
    run = commands.add_parser("run", help="Execute a frozen study on supplied bundles using live Codex")
    for name in ("plan", "bundles", "output", "workspace-parent"):
        run.add_argument("--" + name, type=absolute_path, required=True)
    run.add_argument("--codex-executable", default="codex")
    run.add_argument("--authorize-data-execution", action="store_true")
    run.add_argument("--authorize-exec-preflight", action="store_true",
                     help="Authorize unscored permission-probe sessions; --preflight-only prevents scored execution")
    run.add_argument("--preflight-only", action="store_true", help="Probe the first task once per condition; never score")
    run.add_argument("--preflight-condition", action="append", choices=("baseline", "optional", "enforced"), help="Retry only selected unscored conditions; repeat for multiple conditions")
    run.add_argument("--max-provider-sessions", type=int, default=6, help="Cap all probe and scored provider sessions (default: 6)")
    report = commands.add_parser("report", help="Render an empty plan or explicitly analyze recorded results")
    report.add_argument("--plan", type=absolute_path, required=True)
    report.add_argument("--records", type=absolute_path)
    report.add_argument("--output", type=absolute_path, required=True)
    report.add_argument("--authorize-result-analysis", action="store_true")
    report.add_argument("--gene-task-id", help="Load bounded retained gene details for one opaque task identity")
    report.add_argument("--execution-receipt-sha256", help="Independently retained SHA-256 of execution.json")
    verify = commands.add_parser("verify-tools", help="Verify receipts against a separately retained root")
    for name in ("audit-dir", "receipt-file"):
        verify.add_argument("--" + name, type=absolute_path, required=True)
    verify.add_argument("--input-sha256", required=True)
    verify.add_argument("--implementation-sha256", required=True)
    verify.add_argument("--authorize-evidence-read", action="store_true")
    preview = commands.add_parser("langsmith-export", help="Prepare a redacted post-run trace preview; never upload")
    for name in ("run-dir", "output"):
        preview.add_argument("--" + name, type=absolute_path, required=True)
    preview.add_argument("--data-scope", choices=("synthetic", "public_processed"), required=True)
    preview.add_argument("--project", default="devbench")
    preview.add_argument("--workspace-root", type=absolute_path, help="Optional allowed root for redacted final-answer reads")
    preview.add_argument("--expected-manifest-sha256")
    preview.add_argument("--max-spans", type=int, default=2000)
    preview.add_argument("--authorize-evidence-read", action="store_true")
    upload = commands.add_parser("langsmith-upload", help="Upload an explicitly approved trace payload after measurement")
    upload.add_argument("--payload", type=absolute_path, required=True)
    upload.add_argument("--expected-sha256", required=True)
    upload.add_argument("--output", type=absolute_path, required=True)
    upload.add_argument("--endpoint", default="https://api.smith.langchain.com")
    upload.add_argument("--timeout-sec", type=int, default=15)
    upload.add_argument("--max-spans", type=int, default=2000)
    upload.add_argument("--authorize-upload", action="store_true")
    rna_preview = commands.add_parser("rna-langsmith-preview", help="Prepare a local RNA trace preview; never upload")
    for name in ("snapshot", "output"):
        rna_preview.add_argument("--" + name, type=absolute_path, required=True)
    rna_preview.add_argument("--expected-snapshot-sha256", required=True)
    rna_preview.add_argument("--data-scope", choices=("synthetic", "public_processed"), required=True)
    rna_preview.add_argument("--project", required=True)
    rna_preview.add_argument("--export-namespace", help="Reuse the locally retained UUID for an identical derived view")
    rna_preview.add_argument("--max-spans", type=int, default=2000)
    rna_preview.add_argument("--authorize-evidence-read", action="store_true")
    rna_outbox = commands.add_parser("rna-langsmith-outbox", help="Create a local immutable export-only journal")
    for name in ("payload", "output"):
        rna_outbox.add_argument("--" + name, type=absolute_path, required=True)
    rna_outbox.add_argument("--expected-sha256", required=True)
    rna_outbox.add_argument("--endpoint", default="https://api.smith.langchain.com")
    rna_outbox.add_argument("--project", required=True)
    rna_outbox.add_argument("--max-attempts", type=int, default=3)
    rna_deliver = commands.add_parser("rna-langsmith-deliver", help="Deliver an exactly reviewed RNA outbox")
    rna_deliver.add_argument("--outbox", type=absolute_path, required=True)
    rna_deliver.add_argument("--expected-sha256", required=True)
    rna_deliver.add_argument("--authorize-upload", action="store_true")
    rna_deliver.add_argument("--timeout-sec", type=int, default=15)
    rna_deliver.add_argument("--deadline-sec", type=int, default=60)
    rna_status = commands.add_parser("rna-langsmith-status", help="Read an existing export journal without creating storage")
    rna_status.add_argument("--outbox", type=absolute_path, required=True)
    from .biology_cli import register_commands
    register_commands(commands, absolute_path)
    from .public_cli import register_commands as register_public_commands
    register_public_commands(commands, absolute_path)
    return parser


def _read_json(path, limit=8_000_000):
    from .evidence import _read, decode_json
    return decode_json(_read(path, limit))


def _load_plan(path):
    return StudyPlan.model_validate_json(canonical_json_bytes(_read_json(path)))


def _run_command(args):
    if args.command == "rna-langsmith-preview":
        from .rna_langsmith_export import prepare_rna_export
        return prepare_rna_export(snapshot_path=args.snapshot, output_dir=args.output,
            data_scope=args.data_scope, project=args.project, expected_snapshot_sha256=args.expected_snapshot_sha256,
            allow_evidence_read=args.authorize_evidence_read, max_spans=args.max_spans,
            export_namespace=args.export_namespace)
    if args.command == "rna-langsmith-outbox":
        from .langsmith_outbox import ExportOutbox
        return ExportOutbox.create(args.output, payload_path=args.payload, expected_sha256=args.expected_sha256,
            endpoint=args.endpoint, project=args.project, max_attempts=args.max_attempts).status()
    if args.command == "rna-langsmith-status":
        from .langsmith_outbox import ExportOutbox
        return ExportOutbox.open_readonly(args.outbox).status()
    if args.command == "rna-langsmith-deliver":
        from .rna_langsmith_export import deliver_rna_outbox
        return deliver_rna_outbox(outbox_path=args.outbox, expected_sha256=args.expected_sha256,
            allow_upload=args.authorize_upload, timeout_seconds=args.timeout_sec, deadline_seconds=args.deadline_sec)
    if args.command == "langsmith-export":
        from .langsmith_export import prepare_export
        return prepare_export(args.run_dir, args.output, data_scope=args.data_scope, project=args.project,
            workspace_root=args.workspace_root, max_spans=args.max_spans,
            expected_manifest_sha256=args.expected_manifest_sha256, allow_evidence_read=args.authorize_evidence_read)
    if args.command == "langsmith-upload":
        from .langsmith_export import upload_export
        return upload_export(args.payload, args.expected_sha256, args.output, endpoint=args.endpoint,
            timeout_sec=args.timeout_sec, max_spans=args.max_spans, allow_upload=args.authorize_upload)
    if args.command.startswith("public-"):
        from .public_cli import run_command
        return run_command(args)
    if args.command.startswith("biology-"):
        from .biology_cli import run_command
        return run_command(args)
    if args.command == "run" and not args.authorize_exec_preflight:
        raise PermissionError("Live execution also requires the authorization flag --authorize-exec-preflight")
    if (args.command in {"prepare-data", "import-public", "run"} and not args.authorize_data_execution
            and not (args.command == "run" and args.preflight_only)):
        raise PermissionError("This command requires the explicit --authorize-data-execution authorization flag")
    if args.command == "report" and args.records and not args.authorize_result_analysis:
        raise PermissionError("Recorded result analysis requires the --authorize-result-analysis authorization flag")
    if args.command == "verify-tools" and not args.authorize_evidence_read:
        raise PermissionError("Evidence reading requires the --authorize-evidence-read authorization flag")
    if args.command == "schema":
        from .answers import StudyAnswer
        from .records import AttemptRecord
        return {"plan": StudyPlan, "answer": StudyAnswer, "attempt": AttemptRecord}[args.kind].model_json_schema()
    if args.command == "plan":
        from .planning import default_study_plan, prepare_plan
        from .dashboard import render_dashboard
        from .permissions import require_protected_storage
        destination = require_protected_storage(args.output)
        if any(path.is_symlink() for path in (args.output, *args.output.parents)):
            raise ValueError("Plan output cannot contain symlink components")
        plan = default_study_plan(args.study_id, stage=args.stage)
        if args.model:
            plan = StudyPlan.model_validate_json(canonical_json_bytes(
                {**plan.model_dump(mode="json"), "requested_model": args.model}))
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        prepared = prepare_plan(plan, destination / "private")
        report = render_dashboard(plan, None, destination / "report")
        return {"data_executed": False, "model_participants_launched": 0,
                "plan": str(destination / "private" / "plan.json"),
                "report": report["paths"]["html"], "preparation_receipt": prepared}
    if args.command == "verify-tools":
        from .evidence import verify_receipts
        verified = verify_receipts(args.audit_dir, expected_receipt=_read_json(args.receipt_file, 100_000),
            expected_input_sha256=args.input_sha256, expected_implementation_sha256=args.implementation_sha256,
            allow_evidence_read=True)
        return {"verified": True, "tool_calls": len(verified.events), "root_receipt": verified.root_receipt,
                "scope": "consistency_against_retained_root", "clinical_qualification": False}
    plan = _load_plan(args.plan)
    if args.command == "validate-plan":
        from .planning import schedule_attempts
        return {"valid_configuration": True, "plan_identity": plan.sha256(), "study_id": plan.study_id,
                "planned_attempts": len(schedule_attempts(plan)), "data_executed": False}
    if args.command == "prepare-data":
        from .datasets import prepare_study_data
        return prepare_study_data(plan, args.output, allow_data_execution=True)
    if args.command == "import-public":
        from .datasets import import_public_subset
        bundle = import_public_subset(plan, args.cohort_id, args.input_bundle, args.output,
            _read_json(args.provenance), allow_data_execution=True)
        return {"record_format": "devbench.study.public-import-result/1", "plan_identity": plan.sha256(),
                "cohorts": {args.cohort_id: bundle}, "biological_units_verified": False,
                "source_authenticity_verified": False}
    if args.command == "run":
        from .execution import PreparedCohortBundle, execute_study
        manifest = _read_json(args.bundles)
        if manifest.get("plan_identity") != plan.sha256():
            raise ValueError("Prepared bundles do not match the frozen study plan")
        bundles = {key: PreparedCohortBundle.from_mapping(value) for key, value in manifest["cohorts"].items()}
        return execute_study(plan, bundles, args.output, workspace_parent=args.workspace_parent,
                             codex_executable=args.codex_executable, allow_data_execution=args.authorize_data_execution,
                             allow_exec_preflight=args.authorize_exec_preflight, preflight_only=args.preflight_only,
                             max_provider_sessions=args.max_provider_sessions, preflight_conditions=args.preflight_condition)
    if args.command == "report":
        from .metrics import analyze_attempts
        from .dashboard import render_dashboard
        from .reporting import load_report_records
        if args.records is None and (args.gene_task_id is not None or args.execution_receipt_sha256 is not None):
            raise ValueError("Detail and receipt options require an attempt-record file")
        records, storage = ([], {}) if args.records is None else load_report_records(args.records, plan,
            gene_task_id=args.gene_task_id, execution_receipt_sha256=args.execution_receipt_sha256,
            allow_result_analysis=True)
        summary = analyze_attempts(records, plan)
        summary.update(storage)
        return render_dashboard(plan, summary, args.output)
    raise ValueError("Unknown study command")


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        result = _run_command(args)
        print(json.dumps(result, allow_nan=False, sort_keys=True))
        if args.command == "langsmith-upload" and result.get("status") != "uploaded":
            return 1
        if args.command == "rna-langsmith-deliver" and result.get("state") != "acknowledged":
            return 1
    except (ValueError, TypeError, OSError, RuntimeError, KeyError) as error:
        print(f"devbench-study: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
