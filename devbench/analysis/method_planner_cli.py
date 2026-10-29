"""Explicit planning, trial and review operations; no automatic model or data run."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from .episode_agent import Selection
from .method_planner import ScientificMethodPlanner
from .method_validation import finite_json
from .methods import MethodService, _digest
from .workflow_entrypoints import workflow_action, workflow_full_execution, workflow_source, is_rna_plan, require_completed_parent


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
    parser.add_argument("--scientific-workflow-config", type=Path, help="Explicit registered discovery, reviewer and implementation policy")
    parser.add_argument("--allow-discovery-network", action="store_true")
    parser.add_argument("--allow-review-model-execution", action="store_true")
    parser.add_argument("--allow-implementation-model-execution", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    commands = {"create-analyst": ("request",), "audit-design": ("plan",), "advance": ("plan",),
        "prepare-inputs": ("plan", "ports", "output"),
        "discovery": ("plan",), "candidate-review": ("plan",), "implementation": ("plan",),
        "design-review": ("plan",), "conclusion-review": ("plan",), "create": ("request",), "source": ("plan", "url"), "science": ("plan",),
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
        if name in {"run-trial", "run-full", "prepare-inputs"}:
            command.add_argument("--allow-data-execution", action="store_true")
        if name == "advance":
            command.add_argument("--phase", choices=("planning", "synthesis"), default="planning")
            command.add_argument("--versions", type=Path)
        if name == "design-trials":
            command.add_argument("--execution-inputs-ref", type=Path)
            command.add_argument("--execution-workspace", type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare-inputs" and not args.allow_data_execution:
        raise PermissionError("Input preparation requires --allow-data-execution; no fit or model is launched")
    service = MethodService(args.workspace, readonly=args.command in {"show", "state"}, reconcile=False)
    planner = ScientificMethodPlanner(service)
    actor, action = args.actor, args.command
    if action in {"audit-design", "advance"} and planner.get(args.plan).get("rna_design") is None:
        raise ValueError("Local analyst operations require an explicit rna_design declaration; create a new declared plan")
    selected = (_selector(args, service) if action in {"science", "resolve", "design-trials", "recommend"}
                or action == "advance" and (args.model or args.decisions)
                or args.scientific_workflow_config and (args.model or args.decisions) else None)
    scientific_workflow = None
    if (args.allow_discovery_network or args.allow_review_model_execution or args.allow_implementation_model_execution) and not args.scientific_workflow_config:
        raise ValueError("Scientific execution grants require an explicit scientific workflow configuration")
    if args.scientific_workflow_config:
        from .workflow_science import load_scientific_workflow_config
        scientific_workflow = load_scientific_workflow_config(args.scientific_workflow_config,
            methods_root=service.root, selector=selected, allow_network=args.allow_discovery_network,
            allow_review_model_execution=args.allow_review_model_execution,
            allow_implementation_model_execution=args.allow_implementation_model_execution)
    if action in {"review", "save-recipe", "propose", "prepare-full", "review-full"} and is_rna_plan(planner.get(args.plan)):
        require_completed_parent(planner, args.plan, scientific_workflow=scientific_workflow)
    if action in {"create", "create-analyst"}:
        request = finite_json(args.request)
        if "fastq_manifest" in request:
            from .fastq_planner_binding import register_fastq_input
            if request.get("rna_input_contract") is not None or request["dataset_summary"].get("artifact_ids"):
                raise ValueError("FASTQ manifest registration requires an unbound request")
            inspection = register_fastq_input(service, request.pop("fastq_manifest"), actor=actor)
            identity = inspection["input_artifact_refs"]["manifest"]["artifact_id"]
            request["rna_input_contract"] = {"format": "devbench.fastq-input-binding/1", "manifest_artifact_id": identity}
            request["dataset_summary"]["artifact_ids"] = [identity]
        if action == "create-analyst" and (request.get("rna_design") is None or request.get("rna_input_contract") is None):
            raise ValueError("A local analyst requires explicit rna_design and registered rna_input_contract declarations")
        value = planner.create(request["task"], request["dataset_summary"], actor=actor,
            prior_state=request.get("prior_state"), data_classification=request.get("data_classification", "unclassified"),
            evaluation_purpose=request.get("evaluation_purpose", "method_selection"),
            user_parameter_requirements=request.get("user_parameter_requirements"),
            rna_input_contract=request.get("rna_input_contract"),
            rna_execution_inputs_ref=request.get("rna_execution_inputs_ref"),
            rna_execution_workspace=request.get("rna_execution_workspace"),
            scientific_contract=request.get("scientific_contract"),
            **({"rna_design": request["rna_design"]} if "rna_design" in request else {}))
    elif action == "audit-design":
        from .workflow_entrypoints import audit_workflow_design
        value = audit_workflow_design(planner, args.plan, actor=actor)
    elif action == "prepare-inputs":
        value = planner.prepare_execution_inputs(args.plan, finite_json(args.ports), output=args.output,
            allow_data_execution=args.allow_data_execution)
    elif action == "advance":
        from .workflow_entrypoints import advance_workflow_to_gate
        value = advance_workflow_to_gate(planner, args.plan, actor=actor, selector=selected,
            version_ids=finite_json(args.versions) if args.versions else (),
            allow_model_execution=args.allow_model_execution, scientific_workflow=scientific_workflow,
            phase=args.phase)
    elif action in {"discovery", "candidate-review", "implementation", "design-review", "conclusion-review"}:
        value = workflow_action(planner, args.plan, action.replace("-", "_"), actor=actor, selector=selected,
            scientific_workflow=scientific_workflow, allow_model_execution=args.allow_model_execution)
    elif action == "source":
        value = workflow_source(planner, args.plan, args.url, allowed_hosts=args.allow_host, actor=actor,
                                          allow_network=args.allow_network)
    elif action == "science":
        value = workflow_action(planner, args.plan, "generate", selector=selected, scientific_workflow=scientific_workflow, actor=actor,
                                              allow_model_execution=args.allow_model_execution)
    elif action == "resolve":
        value = workflow_action(planner, args.plan, "resolve", selector=selected, scientific_workflow=scientific_workflow, version_ids=finite_json(args.versions), actor=actor,
                                              allow_model_execution=args.allow_model_execution)
    elif action == "declare-trial":
        value = workflow_action(planner, args.plan, "declare", branch_id=args.branch, spec=finite_json(args.spec), actor=actor, scientific_workflow=scientific_workflow)
    elif action == "design-trials":
        value = workflow_action(planner, args.plan, "design", selector=selected, scientific_workflow=scientific_workflow, actor=actor,
            allow_model_execution=args.allow_model_execution,
            execution_inputs_ref=finite_json(args.execution_inputs_ref) if args.execution_inputs_ref else None,
            execution_workspace=args.execution_workspace)
    elif action == "draft-method":
        if is_rna_plan(planner.get(args.plan)):
            raise ValueError("RNA implementation must use the explicit implementation graph stage and its frozen trusted requests")
        value = planner.draft_implementation(args.plan, args.candidate, _selector(args, service),
            finite_json(args.interface), args.environment_lock, actor=actor, justification=args.justification,
            allow_model_execution=args.allow_model_execution)
    elif action == "run-trial":
        value = workflow_action(planner, args.plan, "run", experiment_id=args.experiment, actor=actor, allow_data_execution=args.allow_data_execution, scientific_workflow=scientific_workflow)
    elif action == "recommend":
        value = workflow_action(planner, args.plan, "recommend", selector=selected, scientific_workflow=scientific_workflow, actor=actor, allow_model_execution=args.allow_model_execution)
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
        value = workflow_full_execution(planner, args.plan, actor=actor, allow_data_execution=args.allow_data_execution, scientific_workflow=scientific_workflow)
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
