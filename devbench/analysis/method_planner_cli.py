"""Explicit planning, trial and review operations; no automatic model or data run."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from .episode_agent import Selection
from .method_planner import ScientificMethodPlanner
from .method_validation import finite_json
from .methods import MethodService, _digest


class RecordedMethodSelector:
    """Offline decision replay, never evidence of a live model evaluation."""
    def __init__(self, responses):
        if isinstance(responses, dict):
            responses = [responses]
        if not isinstance(responses, list) or not 1 <= len(responses) <= 8 or any(not isinstance(x, dict) for x in responses):
            raise ValueError("Use one decision object or up to eight recorded decision objects")
        self.responses = deepcopy(responses)

    def configuration(self):
        return {"backend": "recorded_fixture", "model_execution": False}

    def select(self, context, output, **kwargs):
        if not self.responses:
            raise ValueError("Recorded decisions exhausted")
        response = self.responses.pop(0)
        return Selection(response, {"backend": "recorded_fixture", "model_execution": False,
                                    "response_sha256": _digest(response)})


def _selector(args, service):
    if args.decisions:
        if args.model:
            raise ValueError("Choose either recorded decisions or a live model")
        return RecordedMethodSelector(finite_json(args.decisions))
    if not args.model or not args.actor_workspaces:
        raise ValueError("Live selection requires an explicit model and isolated actor-workspaces path")
    from .episode_codex import CodexActionSelector
    return CodexActionSelector(requested_model=args.model, workspace_parent=args.actor_workspaces,
                               private_root=service.root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--actor", default="local-unverified")
    parser.add_argument("--model")
    parser.add_argument("--actor-workspaces", type=Path)
    parser.add_argument("--decisions", type=Path, help="Offline decision fixture, not a live model result")
    parser.add_argument("--allow-model-execution", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    commands = {"create": ("request",), "source": ("plan", "url"), "science": ("plan",),
        "resolve": ("plan", "versions"), "declare-trial": ("plan", "branch", "spec"),
        "run-trial": ("plan", "experiment"), "recommend": ("plan",),
        "review": ("plan", "rationale"), "save-recipe": ("plan", "name", "rationale"),
        "propose": ("plan", "rationale"), "prepare-full": ("plan", "branch", "inputs"),
        "review-full": ("plan", "rationale"), "run-full": ("plan",), "show": ("plan",), "state": ("plan",),
        "recover": ("plan",), "design-trials": ("plan",),
        "draft-method": ("plan", "candidate", "interface", "environment-lock", "justification")}
    for name, fields in commands.items():
        command = sub.add_parser(name)
        for field in fields:
            command.add_argument("--" + field, required=True)
        if name == "source":
            command.add_argument("--allow-host", action="append", required=True)
            command.add_argument("--allow-network", action="store_true")
        if name in {"run-trial", "run-full"}:
            command.add_argument("--allow-data-execution", action="store_true")
    args = parser.parse_args(argv)
    service = MethodService(args.workspace, readonly=args.command in {"show", "state"}, reconcile=False)
    planner = ScientificMethodPlanner(service)
    actor, action = args.actor, args.command
    if action == "create":
        request = finite_json(args.request)
        value = planner.create(request["task"], request["dataset_summary"], actor=actor,
            prior_state=request.get("prior_state"), data_classification=request.get("data_classification", "unclassified"),
            evaluation_purpose=request.get("evaluation_purpose", "method_selection"),
            user_parameter_requirements=request.get("user_parameter_requirements"))
    elif action == "source":
        value = planner.retrieve_evidence(args.plan, args.url, allowed_hosts=args.allow_host, actor=actor,
                                          allow_network=args.allow_network)
    elif action == "science":
        value = planner.generate_candidates(args.plan, _selector(args, service), actor=actor,
                                              allow_model_execution=args.allow_model_execution)
    elif action == "resolve":
        value = planner.resolve_capabilities(args.plan, _selector(args, service), finite_json(args.versions), actor=actor,
                                              allow_model_execution=args.allow_model_execution)
    elif action == "declare-trial":
        value = planner.declare_trial(args.plan, args.branch, finite_json(args.spec), actor=actor)
    elif action == "design-trials":
        value = planner.design_trials(args.plan, _selector(args, service), actor=actor,
                                      allow_model_execution=args.allow_model_execution)
    elif action == "draft-method":
        value = planner.draft_implementation(args.plan, args.candidate, _selector(args, service),
            finite_json(args.interface), args.environment_lock, actor=actor, justification=args.justification,
            allow_model_execution=args.allow_model_execution)
    elif action == "run-trial":
        value = planner.run_trial(args.plan, args.experiment, actor=actor, allow_data_execution=args.allow_data_execution)
    elif action == "recommend":
        value = planner.recommend(args.plan, _selector(args, service), actor=actor, allow_model_execution=args.allow_model_execution)
    elif action == "review":
        value = planner.approve_recommendation(args.plan, actor=actor, rationale=args.rationale)
    elif action == "save-recipe":
        value = planner.save_recipe(args.plan, args.name, actor=actor, rationale=args.rationale)
    elif action == "propose":
        value = planner.propose_selected(args.plan, actor=actor, rationale=args.rationale)
    elif action == "prepare-full":
        value = planner.prepare_full_execution(args.plan, args.branch, finite_json(args.inputs), actor=actor)
    elif action == "review-full":
        value = planner.approve_full_execution(args.plan, actor=actor, rationale=args.rationale)
    elif action == "run-full":
        value = planner.run_full(args.plan, actor=actor, allow_data_execution=args.allow_data_execution)
    elif action == "state":
        value = planner.get(args.plan)
    elif action == "recover":
        from .method_planner_recovery import recover_plan
        value = recover_plan(planner, args.plan)
    else:
        value = planner.export(args.plan)
    print(json.dumps(value, sort_keys=True, allow_nan=False))
    rows = value if isinstance(value, list) else [value]
    return 1 if any(isinstance(row, dict) and row.get("status") in {"failed", "invalid_trial", "resource_limit", "partial"}
                    for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
