"""Prepare or verify exact /1 or /2 local trial packets; live admission is unavailable.

Version 2 preparation requires a retained assessment intent and execution plans
retain profile, renderer, participant-boundary and route qualification blockers.
The supported commands remain --prepare and --validate; neither runs a model,
scientific method, comparison or hosted trace.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from devbench.analysis.methods import MethodService
from devbench.analysis.method_planner import ScientificMethodPlanner
from devbench.analysis.rna_scientific_trial import load_trial, prepare_trial
from devbench.study.evidence import _read, decode_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--validate", action="store_true")
    parser.add_argument("--workspace", required=True, help="Absolute assigned evidence workspace")
    parser.add_argument("--method-workspace", required=True, help="Existing absolute governed method workspace")
    parser.add_argument("--request", help="Closed bounded local JSON request for preparation")
    parser.add_argument("--output", help="Fresh absolute evidence directory inside the assigned workspace")
    parser.add_argument("--manifest", help="Retained preparation manifest for validation")
    parser.add_argument("--expected-root", help="Separately retained manifest root")
    parser.add_argument("--purpose", choices=("workflow_generation", "trial_execution"), default="trial_execution")
    args = parser.parse_args(argv)
    workspace = Path(args.workspace)
    method_workspace = Path(args.method_workspace)
    if not workspace.is_absolute() or not method_workspace.is_absolute() or not method_workspace.resolve().is_relative_to(workspace.resolve()):
        parser.error("Workspaces must be absolute and the method workspace must be inside the assigned workspace")
    methods = MethodService(method_workspace, readonly=True)
    planner = ScientificMethodPlanner(methods)
    if args.prepare:
        if not args.request or not args.output:
            parser.error("--prepare requires --request and --output")
        path = Path(args.request)
        if not path.is_absolute() or not path.resolve().is_relative_to(workspace.resolve()):
            parser.error("Request must be absolute and inside the assigned workspace")
        request = decode_json(_read(path, 1_000_000))
        result = prepare_trial(args.output, request, methods=methods, workspace=workspace, planner=planner, purpose=args.purpose)
        print(json.dumps({"manifest_path": result["manifest_path"], "root_hash": result["root_hash"],
                          "readiness": result["readiness"], "invocation": result["invocation"]}, allow_nan=False))
    else:
        if not args.manifest or not args.expected_root:
            parser.error("--validate requires --manifest and --expected-root")
        record = load_trial(args.manifest, args.expected_root, methods=methods, workspace=workspace, planner=planner)
        print(json.dumps({"readiness": record["readiness"], "model_calls": 0, "data_runs": 0}, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
