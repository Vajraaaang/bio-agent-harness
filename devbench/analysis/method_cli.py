"""CLI over MethodService; custom code executes only through the OCI backend."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .method_validation import finite_json
from .methods import MethodService


def main(argv=None):
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "planner":
        from .method_planner_cli import main as planner_main
        return planner_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--actor", default="local-unverified")
    commands = parser.add_subparsers(dest="command", required=True)
    definitions = {
        "import-artifact": ("source", "artifact-type"),
        "register": ("contract", "source", "environment-lock"),
        "register-tool": ("tool",),
        "register-fastq": ("manifest",),
        "create-analysis": ("name",),
        "branch": ("analysis", "revision"),
        "add-step": ("branch", "method", "inputs", "parameters"),
        "run": ("step",), "show": ("kind", "id"), "analysis": ("id",),
        "preview": ("run",), "provenance": ("run",), "reproduce": ("run",),
        "propose": ("run", "rationale"), "approve": ("proposal", "rationale"),
        "reject": ("proposal", "rationale"), "lineage": ("artifact",), "audit": (), "recover": (),
    }
    for name, options in definitions.items():
        command = commands.add_parser(name)
        for option in options:
            command.add_argument("--" + option, required=True)
        if name == "approve":
            command.add_argument("--acknowledge-warnings", action="store_true")
    args = parser.parse_args(argv)
    readonly = args.command in {"show", "analysis", "preview", "provenance", "lineage", "audit"}
    service = MethodService(args.workspace, readonly=readonly, reconcile=False,
                            initialize=False if args.command == "recover" else None)
    actor, action = args.actor, args.command
    if action == "import-artifact":
        value = service.import_artifact(args.source, artifact_type=args.artifact_type, actor=actor)
    elif action == "register":
        value = service.register_method(finite_json(args.contract), source=args.source,
                                        environment_lock=args.environment_lock, actor=actor)
    elif action == "register-tool":
        value = service.register_dispatcher(args.tool, actor=actor)
    elif action == "register-fastq":
        value = service.register_fastq(finite_json(args.manifest), actor=actor)
    elif action == "create-analysis":
        value = service.create_analysis(args.name, actor=actor)
    elif action == "branch":
        value = service.create_branch(args.analysis, args.revision, actor=actor)
    elif action == "add-step":
        value = service.add_step(args.branch, args.method, finite_json(args.inputs), finite_json(args.parameters), actor=actor)
    elif action == "run":
        value = service.run_step(args.step, actor=actor)
    elif action == "show":
        value = service.get(args.kind, args.id)
    elif action == "analysis":
        value = service.analysis(args.id)
    elif action == "preview":
        value = service.get("run", args.run)["validation"]
    elif action == "provenance":
        value = service.provenance(args.run)
    elif action == "reproduce":
        value = service.reproduce(args.run, actor=actor)
    elif action == "propose":
        value = service.propose(args.run, actor=actor, rationale=args.rationale)
    elif action == "approve":
        value = service.approve(args.proposal, actor=actor, rationale=args.rationale,
                                acknowledge_warnings=args.acknowledge_warnings)
    elif action == "reject":
        value = service.reject(args.proposal, actor=actor, rationale=args.rationale)
    elif action == "lineage":
        value = service.lineage(args.artifact)
    elif action == "recover":
        value = {"recovered_run_ids": service.reconcile()}
    else:
        value = service.audit()
    print(json.dumps(value, sort_keys=True, allow_nan=False))
    return 1 if isinstance(value, dict) and (value.get("status") in {"failed", "interrupted"} or value.get("valid") is False) else 0


if __name__ == "__main__":
    raise SystemExit(main())
