"""Run reviewed episode actions or an explicitly authorized Codex selector."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys


def _read(path):
    from .episodes import _json_copy
    from devbench.study.evidence import _read as read_bytes, decode_json
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
        raise ValueError("Use a bounded regular absolute JSON configuration file")
    return _json_copy(decode_json(read_bytes(path, 1_000_000)))


def _actions(path):
    actions = _read(path)
    if (not isinstance(actions, list) or not 1 <= len(actions) <= 100 or not isinstance(actions[-1], dict)
            or actions[-1].get("kind") not in {"finish", "refuse", "request_information"}):
        raise ValueError("Batch actions must be bounded and end with finish, refuse or request_information")
    return actions


def _reviewed_actions(episode, actions):
    response = None
    for action in actions:
        if episode.outcome is not None:
            break
        response = episode.step(action)
        if response["state"] == "awaiting_information":
            from .episode_handoff import save_handoff
            try:
                receipt = save_handoff(episode)
                return {**response, "handoff": receipt}
            except (OSError, ValueError, RuntimeError) as error:
                return episode.abort(("handoff_capture_failed:" + str(error))[:2000])
    return response


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, help="Coordinator-reviewed question, data bindings and settings")
    parser.add_argument("--plan-request", type=Path, help="Question and dataset declarations for an unreviewed proposal")
    parser.add_argument("--plan-answer", type=Path, help="Explicit recorded proposal decision; no model is launched")
    parser.add_argument("--model-data-scope", choices=("synthetic", "public"), help="Declared disclosure scope for live proposal selection")
    parser.add_argument("--proposal", type=Path, help="Immutable research proposal audit manifest")
    parser.add_argument("--proposal-root", help="Externally retained proposal root")
    parser.add_argument("--review", type=Path, help="Recorded operator review audit manifest")
    parser.add_argument("--review-root", help="Externally retained review root")
    parser.add_argument("--actions", type=Path, help="Bounded JSON array of reviewed actions ending in finish/refuse")
    parser.add_argument("--replay", type=Path, help="Previously sealed episode")
    parser.add_argument("--resume", type=Path, help="Retained information-handoff manifest")
    parser.add_argument("--response", type=Path, help="Operator clarification JSON for --resume")
    parser.add_argument("--expected-root", help="Externally retained receipt for replay")
    parser.add_argument("--output", type=Path, required=True, help="Fresh output directory, or existing episode directory with --resume")
    parser.add_argument("--allow-data-execution", action="store_true")
    parser.add_argument("--codex-model", help="Exact model identifier for the optional Codex action selector")
    parser.add_argument("--agent-workspaces", type=Path, help="Short absolute directory for isolated selector workspaces")
    parser.add_argument("--allow-model-execution", action="store_true")
    parser.add_argument("--evidence-request", type=Path, help="Research packet or recorded scientific-review request")
    parser.add_argument("--allow-evidence-read", action="store_true")
    parser.add_argument("--allow-review-record", action="store_true")
    parser.add_argument("--agent-review-request", type=Path, help="One bounded scientific reviewer selection")
    parser.add_argument("--review-answer", type=Path, help="Recorded reviewer response; no model is launched")
    args = parser.parse_args(argv)
    if args.agent_review_request:
        if any((args.spec, args.plan_request, args.plan_answer, args.proposal, args.proposal_root, args.review,
                args.review_root, args.actions, args.replay, args.resume, args.response, args.expected_root,
                args.allow_data_execution, args.evidence_request)):
            parser.error("Scientific reviewer selection excludes analysis execution and other modes")
        if bool(args.review_answer) == bool(args.codex_model):
            parser.error("Choose exactly one of --review-answer and --codex-model")
        if args.review_answer and any((args.allow_model_execution, args.model_data_scope, args.agent_workspaces)):
            parser.error("Recorded scientific reviews exclude live model settings")
        from .resources import configure_threads
        configure_threads()
        from .scientific_review_agent import reviewer_cli
        try:
            with redirect_stdout(sys.stderr): result = reviewer_cli(args)
            print(json.dumps(result, allow_nan=False))
            return 0 if result["status"] == "review_recorded" else 3 if result["status"] == "invalid_review" else 4
        except (ValueError, PermissionError, OSError) as error:
            parser.error(str(error))
    if args.review_answer:
        parser.error("--review-answer requires --agent-review-request")
    if args.evidence_request:
        if any((args.spec, args.plan_request, args.plan_answer, args.model_data_scope, args.proposal,
                args.proposal_root, args.review, args.review_root, args.actions, args.replay, args.resume,
                args.response, args.expected_root, args.allow_data_execution, args.codex_model,
                args.agent_workspaces, args.allow_model_execution)):
            parser.error("Evidence requests exclude analysis execution, proposal selection and model permissions")
        from .resources import configure_threads
        configure_threads()
        from .scientific_review import evidence_cli
        try:
            with redirect_stdout(sys.stderr):
                result = evidence_cli(args)
            print(json.dumps(result, allow_nan=False))
            return 0
        except (ValueError, PermissionError, OSError) as error:
            parser.error(str(error))
    if args.allow_evidence_read or args.allow_review_record:
        parser.error("Evidence permissions require --evidence-request")
    if not args.allow_data_execution:
        parser.error("Execution requires explicit --allow-data-execution coordinator authorization")
    if args.plan_request:
        if any((args.spec, args.proposal, args.proposal_root, args.review, args.review_root,
                args.actions, args.replay, args.resume, args.response, args.expected_root)):
            parser.error("Proposal drafting excludes existing specification, review and execution modes")
        if bool(args.plan_answer) == bool(args.codex_model):
            parser.error("Choose exactly one of --plan-answer and --codex-model for drafting")
        if args.codex_model and not args.model_data_scope:
            parser.error("Live proposal selection requires --model-data-scope synthetic or public")
    elif args.plan_answer or args.model_data_scope:
        parser.error("Planning answer and disclosure scope require --plan-request")
    if args.codex_model and (args.actions or args.replay):
        parser.error("Choose exactly one of reviewed actions, replay or Codex selection")
    if args.codex_model and not args.allow_model_execution:
        parser.error("Codex selection requires --allow-model-execution in addition to data authorization")
    if args.codex_model and not args.agent_workspaces:
        parser.error("Codex selection requires --agent-workspaces")
    if not args.codex_model and (args.agent_workspaces or args.allow_model_execution):
        parser.error("Model execution settings require --codex-model")
    if args.proposal:
        if args.spec or args.replay or args.resume or args.expected_root or args.response:
            parser.error("Reviewed proposal execution excludes --spec, --replay, --resume and handoff/replay fields")
        if not args.proposal_root or not args.review or not args.review_root or not (args.actions or args.codex_model):
            parser.error("A proposal requires both retained roots, its review and actions or a Codex model")
    elif args.proposal_root or args.review or args.review_root:
        parser.error("Proposal/review receipts require --proposal")
    if args.resume:
        if args.spec or args.replay:
            parser.error("Handoff resume excludes --spec and --replay")
        if not args.expected_root or not args.response or not (args.actions or args.codex_model):
            parser.error("Resume requires a retained root, response and continuation actions or Codex model")
    elif args.response:
        parser.error("--response requires --resume")
    from .resources import configure_threads
    configure_threads()
    from .episodes import AnalysisEpisode, EpisodeLimits, replay_episode
    try:
        if args.plan_request:
            from .episode_planning import planning_cli
            with redirect_stdout(sys.stderr):
                result = planning_cli(args)
            print(json.dumps(result, allow_nan=False))
            return (0 if result["status"] == "proposal_ready_for_review" else
                    5 if result["status"] == "awaiting_information" else
                    4 if result["status"] == "infrastructure_failure" else 3)
        driver_response = None
        with redirect_stdout(sys.stderr):
            if args.resume:
                from .episode_handoff import resume_handoff
                from .episode_agent import drive_episode
                actions = _actions(args.actions) if args.actions else None
                selector = None
                episode = resume_handoff(args.output, args.resume, expected_root=args.expected_root,
                    response=_read(args.response), expected_mode="agent" if args.codex_model else "manual",
                    selector_request={"backend": "codex-cli", "requested_model": args.codex_model} if args.codex_model else None,
                    allow_data_execution=True)
                if episode.outcome is None:
                    try:
                        if args.codex_model:
                            from .episode_codex import CodexActionSelector
                            frozen = episode.resume_driver["configuration"]
                            selector = CodexActionSelector(requested_model=frozen["requested_model"],
                                workspace_parent=args.agent_workspaces, private_root=episode.root,
                                executable=frozen["executable"], reasoning_effort=frozen["reasoning_effort"], memory_mb=frozen["memory_mb"])
                        driver_response = (drive_episode(episode, selector, allow_data_execution=True, allow_model_execution=True)
                            if selector else _reviewed_actions(episode, actions))
                    except (OSError, ValueError, RuntimeError) as error:
                        if episode.outcome is None:
                            episode.abort(("handoff_continuation_failed:" + str(error))[:2000])
                        raise
            elif args.replay:
                if args.spec or args.actions or not args.expected_root:
                    raise ValueError("Replay requires --expected-root and excludes --spec/--actions")
                episode = replay_episode(args.replay, args.expected_root, args.output, allow_data_execution=True)
            elif args.proposal:
                from .episode_proposals import begin_reviewed_episode
                from .episode_agent import AgentLimits, drive_episode
                actions = _actions(args.actions) if args.actions else None
                episode = begin_reviewed_episode(args.output, args.proposal, args.review,
                    expected_proposal_root=args.proposal_root, expected_review_root=args.review_root, allow_data_execution=True)
                try:
                    if args.codex_model:
                        from .episode_codex import CodexActionSelector
                        agent_limits = AgentLimits()
                        selector = CodexActionSelector(requested_model=args.codex_model,
                            workspace_parent=args.agent_workspaces, private_root=episode.root, memory_mb=agent_limits.memory_mb)
                        driver_response = drive_episode(episode, selector, limits=agent_limits,
                            allow_data_execution=True, allow_model_execution=True)
                    else:
                        driver_response = _reviewed_actions(episode, actions)
                except BaseException as error:
                    if episode.outcome is None:
                        episode.abort(("reviewed_execution_setup_failed:" + type(error).__name__ + ": " + str(error))[:2000])
                    if not isinstance(error, Exception):
                        raise
            else:
                if not args.spec or not (args.actions or args.codex_model) or args.expected_root:
                    raise ValueError("New episodes require --spec and either --actions or --codex-model")
                spec = _read(args.spec)
                actions = _actions(args.actions) if args.actions else None
                allowed = {"question", "assay", "public_task", "inputs", "limits", "fixed_options", "input_kind", "execution_tier", "agent_limits"}
                if not isinstance(spec, dict) or set(spec)-allowed:
                    raise ValueError("Unknown episode specification fields")
                spec = dict(spec)
                from .episode_agent import AgentLimits
                agent_values = spec.pop("agent_limits", {})
                if agent_values and not args.codex_model:
                    raise ValueError("agent_limits apply only to Codex selection")
                agent_limits = AgentLimits(**agent_values)
                paths = spec.pop("inputs")
                if not isinstance(paths, dict) or any(not isinstance(value, str) for value in paths.values()):
                    raise ValueError("Input bindings must map roles to paths")
                bindings = {name: (args.spec.parent / value).resolve() if not Path(value).is_absolute() else Path(value)
                            for name, value in paths.items()}
                limits = EpisodeLimits(**spec.pop("limits", {}))
                selector = None
                if args.codex_model:
                    from .episode_codex import CodexActionSelector
                    selector = CodexActionSelector(requested_model=args.codex_model,
                        workspace_parent=args.agent_workspaces, private_root=args.output,
                        memory_mb=agent_limits.memory_mb)
                episode = AnalysisEpisode.create(args.output, inputs=bindings, limits=limits,
                    allow_data_execution=True, **spec)
                if args.codex_model:
                    from .episode_agent import drive_episode
                    driver_response = drive_episode(episode, selector, limits=agent_limits,
                        allow_data_execution=True, allow_model_execution=True)
                else:
                    driver_response = _reviewed_actions(episode, actions)
        observation = episode.observation()
        print(json.dumps({"episode_root": str(episode.root), "receipt": episode.receipt,
                          "observation": observation,
                          "handoff": episode.handoff,
                          "information_request": driver_response.get("message") if driver_response else None}, allow_nan=False))
        return (0 if observation["harness_accepted"] else 5 if observation["state"] == "awaiting_information"
                else 4 if observation["outcome"] == "infrastructure_failure" else 3)
    except (KeyError, TypeError, ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
