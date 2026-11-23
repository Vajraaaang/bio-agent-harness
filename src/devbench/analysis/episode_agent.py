"""Bounded, trusted orchestration of existing episode actions.

Selectors receive public observations only. They never receive the episode object
or its broker. This module is not an OS sandbox or a protected evaluator.
"""

import json
import math
import time
from copy import deepcopy
from dataclasses import asdict, dataclass, replace

from devbench.engine.workflow_operations import LOCAL_INTERACTIVE_CODEX

from .checkpoints import atomic_json
from .episodes import _json_copy, _public_only
from .model_authority import read_model_authority, reservation_binding

# The local Codex CLI's short startup burst is measured over a bounded window.
# The configured growth rate and per-sample absolute RSS guard still apply.
_LOCAL_CODEX_GROWTH_WINDOW_SECONDS = 0.5


@dataclass(frozen=True)
class AgentLimits:
    max_selections: int = 8
    timeout_seconds: float = 600
    session_timeout_seconds: float = 90
    memory_mb: int = 2048
    max_context_bytes: int = 128_000
    max_total_context_bytes: int = 3_584_000

    def __post_init__(self):
        for name, low, high in (
            ("max_selections", 1, 32),
            ("memory_mb", 128, 4096),
            ("max_context_bytes", 1024, 1_000_000),
            ("max_total_context_bytes", 1024, 32_000_000),
        ):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} exceeds selector policy")
        for name, maximum in (
            ("timeout_seconds", 3600),
            ("session_timeout_seconds", 300),
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 < value <= maximum
            ):
                raise ValueError(f"{name} exceeds selector policy")


@dataclass(frozen=True)
class Selection:
    action: dict
    evidence: dict
    decision_record: dict | None = None


@dataclass(frozen=True)
class ReviewAgentLimits(AgentLimits):
    """Query calls and conservative token reservations are separate from turns."""

    max_queries: int = 32
    max_tokens_per_invocation: int = 8192

    def __post_init__(self):
        super().__post_init__()
        for name, maximum in (
            ("max_queries", 1024),
            ("max_tokens_per_invocation", 131072),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 0 < value <= maximum:
                raise ValueError(name + " exceeds reviewer grant")


class InvalidSelection(ValueError):
    """The provider completed but did not supply an admissible action envelope."""


def selector_configuration(selector):
    method = getattr(selector, "configuration", None)
    value = (
        method()
        if callable(method)
        else {
            "backend": "trusted_custom_selector",
            "type_name": type(selector).__module__ + "." + type(selector).__qualname__,
        }
    )
    return _json_copy(value)


class CampaignSelectionInvoker:
    """Trusted role transport over the existing cumulative campaign authority.

    This owns no additional spend budget. ReviewBudget and controller limits are
    nested sublimits. An entered invocation is reconciled, never relaunched.
    """

    def __init__(
        self, planner, session_id, *, limits=None, allow_model_execution=False
    ):
        self.planner, self.session_id = planner, session_id
        self.limits = limits or AgentLimits(max_selections=24)
        if not isinstance(self.limits, AgentLimits):
            raise TypeError("Campaign transport requires bounded AgentLimits")
        self.allow_model_execution = allow_model_execution is True

    def _runtime(self):
        from devbench.engine.workflow_operations import OperationBlocked

        state = self.planner.get(self.session_id)
        retained = state.get("model_campaign")
        current = self.planner._model_campaigns.get(self.session_id)
        if retained is not None and current is not None:
            campaign = current[0].get_campaign(retained["campaign_id"])
            if campaign["status"] != "open":
                raise OperationBlocked(campaign.get("reason") or campaign["status"])
        ledger, backend, campaign = self.planner._campaign_runtime(state)
        if campaign["policy"]["kind"] not in {
            "hard_total_tokens",
            "bounded_observed_tokens",
            LOCAL_INTERACTIVE_CODEX,
        }:
            raise PermissionError("Adaptive roles require aggregate model admission")
        if campaign["policy"]["kind"] == LOCAL_INTERACTIVE_CODEX:
            if not (
                self.allow_model_execution
                and campaign["identity"]["data_scope"]
                == backend.data_scope
                == state["data_classification"]
                and campaign["identity"]["operator"] == backend.actor
            ):
                raise PermissionError(
                    "Local interactive Codex requires exact current authorization"
                )
        return state, ledger, backend, campaign

    def default_selector(self):
        """Fresh actor sessions retain the exact common coordinator-private root."""
        from .episode_codex import CodexActionSelector

        _, _, backend, campaign = self._runtime()
        original = backend.selector
        if not isinstance(original, CodexActionSelector):
            raise TypeError("Default adaptive roles require the Codex CLI runtime")
        if campaign["policy"]["kind"] == LOCAL_INTERACTIVE_CODEX:
            return original
        selected = CodexActionSelector(
            requested_model=original.requested_model,
            workspace_parent=original.workspace_parent,
            private_root=original.private_root,
            executable=original.executable,
            reasoning_effort=original.reasoning_effort,
            memory_mb=original.memory_mb,
            denied_roots=original.denied_roots,
            max_context_bytes=original.max_context_bytes,
            protected_model_gate=original.protected_model_gate,
            trace_sidecar=original.trace_sidecar,
        )
        selected.workspaces = list(original.workspaces)
        return selected

    @staticmethod
    def _require_local_role(backend, selector, context, role, purpose):
        termination = context.get("termination_required")
        expected = "adaptive_termination" if termination else "adaptive_controller"
        if (
            role != "controller"
            or selector is not backend.selector
            or context.get("decision_kind") != "adaptive_controller"
            or context.get("analyst_profile") is not None
            or type(termination) is not bool
            or purpose != expected
        ):
            raise PermissionError(
                "Local interactive Codex admits only its single-operator controller"
            )

    def invoke(
        self,
        selector,
        context,
        output,
        *,
        role,
        purpose,
        node,
        attempt,
        timeout_seconds=None,
        token_limit=None,
        memory_mb=None,
        max_context_bytes=None,
    ):
        from pathlib import Path

        from devbench.engine.method_executor import _path
        from devbench.engine.workflow_operations import _identity

        from .episode_codex import _METHOD_PLANNER_CONTRACTS, CodexActionSelector
        from .model_authority import is_offline_selector

        if role not in {"controller", "challenger"}:
            raise ValueError("Unknown adaptive model role")
        if context.get("decision_kind") not in {
            *_METHOD_PLANNER_CONTRACTS,
            "scientific_review",
        }:
            raise ValueError(
                "Campaign selection requires a registered decision contract"
            )
        _public_only(context)
        state, ledger, backend, campaign = self._runtime()
        if campaign["policy"]["kind"] == LOCAL_INTERACTIVE_CODEX:
            self._require_local_role(backend, selector, context, role, purpose)
        configuration = selector_configuration(selector)
        offline = is_offline_selector(configuration)
        if not offline and (
            not isinstance(selector, CodexActionSelector)
            or not self.allow_model_execution
            or state["data_classification"] not in {"synthetic", "public"}
        ):
            raise PermissionError(
                "Adaptive roles require authorized public/synthetic Codex CLI selection"
            )
        root = _path(Path(output))
        if not root.is_relative_to(self.planner.methods.root):
            raise PermissionError(
                "Campaign output must stay in the common private workspace"
            )
        campaign_id, node, attempt = _identity(campaign["campaign_id"], node, attempt)
        lease = ("campaign-role", campaign_id, node, attempt)
        with ledger._operation_lock(lease):
            return self._invoke_locked(
                selector,
                context,
                root,
                configuration=configuration,
                offline=offline,
                role=role,
                purpose=purpose,
                lease=lease,
                locked_ledger_path=ledger.path,
                timeout_seconds=timeout_seconds,
                token_limit=token_limit,
                memory_mb=memory_mb,
                max_context_bytes=max_context_bytes,
            )

    def _invoke_locked(
        self,
        selector,
        context,
        root,
        *,
        configuration,
        offline,
        role,
        purpose,
        lease,
        locked_ledger_path,
        timeout_seconds,
        token_limit,
        memory_mb,
        max_context_bytes,
    ):
        import uuid

        from devbench.engine.workflow_operations import OperationBlocked

        from .checkpoints import digest

        state, ledger, backend, campaign = self._runtime()
        _, campaign_id, _, attempt = lease
        if ledger.path != locked_ledger_path or campaign["campaign_id"] != campaign_id:
            raise OperationBlocked("model_campaign_changed")
        local = campaign["policy"]["kind"] == LOCAL_INTERACTIVE_CODEX
        if local:
            self._require_local_role(backend, selector, context, role, purpose)
        if not offline and state["data_classification"] not in {"synthetic", "public"}:
            raise PermissionError(
                "Adaptive roles require authorized public/synthetic Codex CLI selection"
            )
        records = ledger.campaign_reservations(campaign_id)
        context_digest, selector_digest = digest(context), digest(configuration)
        identity = {
            "role": role,
            "backend": LOCAL_INTERACTIVE_CODEX
            if local
            else configuration.get("backend"),
            "requested_model": configuration.get(
                "requested_model",
                "offline_fixture" if offline else None,
            ),
            "runtime_build_sha256": campaign["capability"]["runtime_digest"],
            "profile_sha256": selector_digest,
        }
        live_scope = "single_operator_interactive" if local else "unqualified_live"
        scope = "protocol_only" if offline else live_scope
        role_binding = {
            "context_digest": context_digest,
            "selector_digest": selector_digest,
            "role": role,
            "purpose": purpose,
        }
        node, directory, intent, row = self._find_role_invocation(
            root,
            ledger,
            records,
            lease,
            role_binding,
            identity,
            scope,
        )
        if row is not None and row["status"] != "reserved":
            reservation = ledger._reservation(row, campaign)
            try:
                return self._settle(
                    directory,
                    intent,
                    reservation,
                    ledger,
                    backend,
                    campaign,
                    retained=row,
                )
            except OperationBlocked:
                raise
            except Exception as error:
                raise OperationBlocked("model_call_requires_reconciliation") from error

        identifier = (
            uuid.uuid4().hex if row is None else row["request"]["invocation_id"]
        )
        bindings = backend.parent_selection_binding(
            selector,
            context,
            parent_session_id=self.session_id,
            session_id=identifier,
            invocation_id=identifier,
        )
        if bindings["context_digest"] != context_digest or any(
            bindings[key] != campaign["capability"][key]
            for key in ("backend_digest", "runtime_digest")
        ):
            raise ValueError("Protected role context or backend binding differs")
        allocation, context_size = self._role_allocation(
            context,
            backend,
            campaign,
            timeout_seconds,
            token_limit,
            memory_mb,
            max_context_bytes,
        )
        prior_context = sum(
            row["request"].get("context_bytes", row["limits"]["context_bytes"])
            for row in records
            if row["charge_basis"] != "verified_unlaunched"
            and (row["node"], row["attempt"]) != (node, attempt)
        )
        if prior_context + context_size > self.limits.max_total_context_bytes:
            raise OperationBlocked("cumulative_context_transport_limit")
        request = {
            "format": "devbench.model-call-request/1",
            **bindings,
            "parent_session_id": self.session_id,
            "session_id": identifier,
            "invocation_id": identifier,
            "plan_hash": digest(state),
            "purpose": purpose,
            "role": role,
            "context_bytes": context_size,
            "selector_digest": selector_digest,
            "role_lease_sha256": digest(list(lease)),
            "role_output_sha256": digest(str(directory)),
        }
        reservation = self._reserve_role(
            directory,
            row,
            ledger,
            campaign,
            node,
            attempt,
            request,
            allocation,
            purpose,
        )
        expected_intent = {
            "format": "devbench.campaign-role-invocation/1",
            "campaign_id": campaign_id,
            "reservation_ref": reservation.ref,
            "bindings": request,
            "context_sha256": context_digest,
            "selector_sha256": selector_digest,
            "role": role,
            "purpose": purpose,
            "node": node,
            "attempt": attempt,
            "identity": identity,
            "protocol_scope": scope,
        }
        if intent is not None and intent != expected_intent:
            raise ValueError("Retained invocation differs from its exact preparation")
        return self._execute_reserved_role(
            selector,
            context,
            directory,
            expected_intent,
            reservation,
            ledger,
            backend,
            campaign,
            allocation,
            configuration,
            offline,
        )

    def _find_role_invocation(
        self,
        root,
        ledger,
        records,
        lease,
        binding,
        identity,
        scope,
    ):
        from devbench.engine.method_executor import _path
        from devbench.engine.workflow_operations import OperationBlocked

        from .checkpoints import digest

        _, campaign_id, node, attempt = lease
        prior = {(row["node"], row["attempt"]): row for row in records}
        lease_sha256 = digest(list(lease))
        for retry in range(3):
            candidate = (
                node
                if retry == 0
                else f"unlaunched-retry-{digest([node, attempt, retry])[:40]}"
            )
            directory = _path(
                root if retry == 0 else root / f"unlaunched-retry-{retry}"
            )
            intent, row = self._read_retained_role(
                directory,
                ledger,
                prior.get((candidate, attempt)),
                lease_sha256,
                campaign_id,
                candidate,
                attempt,
                binding,
                identity,
                scope,
            )
            if row is None or row["status"] == "reserved":
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                return candidate, directory, intent, row
            if (
                row["status"] == "cancelled"
                and row["charge_basis"] == "verified_unlaunched"
                and row["capture"] is None
            ):
                if any(
                    row["request"].get(key) != value for key, value in binding.items()
                ):
                    raise ValueError(
                        "Cancelled invocation differs from this exact role and context"
                    )
                continue
            if intent is None:
                raise OperationBlocked("model_call_requires_reconciliation")
            return candidate, directory, intent, row
        raise OperationBlocked("unchanged_infrastructure_retry_budget_exhausted")

    @staticmethod
    def _read_retained_role(
        directory,
        ledger,
        row,
        lease_sha256,
        campaign_id,
        node,
        attempt,
        binding,
        identity,
        scope,
    ):
        from devbench.study.evidence import _read, decode_json

        from .checkpoints import digest

        intent = None
        intent_path = directory / "campaign-intent.json"
        if intent_path.exists():
            intent = decode_json(_read(intent_path, 128_000))
            expected = {
                "context_sha256": binding["context_digest"],
                "selector_sha256": binding["selector_digest"],
                "role": binding["role"],
                "purpose": binding["purpose"],
                "node": node,
                "attempt": attempt,
                "campaign_id": campaign_id,
                "identity": identity,
                "protocol_scope": scope,
            }
            if any(intent[key] != value for key, value in expected.items()):
                raise ValueError(
                    "Retained invocation differs from this exact role and context"
                )
            if row is None:
                row = ledger.get_model_reservation(intent["reservation_ref"])
            if (
                (row["campaign_id"], row["node"], row["attempt"])
                != (campaign_id, node, attempt)
                or row["ref"] != intent["reservation_ref"]
                or row["request"] != intent["bindings"]
            ):
                raise ValueError(
                    "Retained invocation differs from its exact reservation"
                )
        output_sha256 = digest(str(directory))
        if row is not None and (
            row["request"].get("role_lease_sha256") not in (None, lease_sha256)
            or row["request"].get("role_output_sha256") not in (None, output_sha256)
        ):
            raise ValueError("Retained invocation differs from its exact role lease")
        return intent, row

    def _role_allocation(
        self,
        context,
        backend,
        campaign,
        timeout_seconds,
        token_limit,
        memory_mb,
        max_context_bytes,
    ):
        from devbench.engine.workflow_operations import _limits
        from devharness_core.provenance import canonical_json

        transport = asdict(self.limits)
        for key, value in (
            ("session_timeout_seconds", timeout_seconds),
            ("memory_mb", memory_mb),
            ("max_context_bytes", max_context_bytes),
        ):
            if value is None:
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(
                    "Current transport sublimits must be positive and finite"
                )
            transport[key] = min(transport[key], value)
        allocation = _limits(backend.parent_call_limits(context, campaign, transport))
        if token_limit is not None:
            if type(token_limit) is not int or token_limit < 1:
                raise ValueError("Review token sublimit must be a positive integer")
            allocation["tokens"] = min(allocation["tokens"], token_limit)
        context_size = len(canonical_json(context))
        if (
            context_size > allocation["context_bytes"]
            or allocation["context_bytes"] > transport["max_context_bytes"]
            or allocation["memory_mb"] > transport["memory_mb"]
            or allocation["seconds"] > transport["session_timeout_seconds"]
        ):
            raise ValueError("Campaign allocation exceeds its transport limits")
        return allocation, context_size

    @staticmethod
    def _reserve_role(
        directory,
        row,
        ledger,
        campaign,
        node,
        attempt,
        request,
        allocation,
        purpose,
    ):
        from devbench.engine.workflow_operations import OperationBlocked

        if row is None:
            return ledger.reserve_model_call(
                campaign["campaign_id"],
                node,
                attempt,
                request,
                limits=allocation,
                purpose=purpose,
                child_id=None,
            )
        # Every selector enters through activation while this lease is
        # held. A matching reservation still in its original state proves
        # that only coordinator setup could have been interrupted.
        if (
            row["charge_basis"] != "reserved_bound"
            or row["capture"] is not None
            or row["request"] != request
            or row["limits"] != allocation
            or row["child_id"] is not None
            or row["purpose"] != purpose
            or row["error_type"] is not None
        ):
            raise ValueError(
                "Pre-launch recovery requires the exact retained role "
                "request and limits"
            )
        setup = {
            "campaign-intent.json",
            "selection-reservation.json",
            "phase-timing.json",
        }
        if any(
            child.is_symlink()
            or not child.is_file()
            or (child.name not in setup and not child.name.startswith(".pending-"))
            for child in directory.iterdir()
        ):
            raise OperationBlocked("model_call_requires_reconciliation")
        return ledger._reservation(row, campaign)

    def _recover_role_dispatch_error(
        self,
        directory,
        intent,
        reservation,
        ledger,
        backend,
        campaign,
        dispatch_entered,
        error,
    ):
        from devbench.engine.workflow_operations import OperationBlocked
        from devharness_core.watchdog import HarnessResourceInterrupt

        current = ledger.get_model_reservation(reservation)["status"]
        if not dispatch_entered and current == "reserved":
            ledger.cancel_unlaunched_model_call(reservation)
        elif dispatch_entered and current == "active":
            try:
                self._settle(directory, intent, reservation, ledger, backend, campaign)
            except BaseException:
                if ledger.get_model_reservation(reservation)["status"] == "active":
                    ledger.mark_model_call_uncertain(
                        reservation,
                        error_type=type(error).__name__,
                    )
        if (
            campaign["policy"]["kind"] == LOCAL_INTERACTIVE_CODEX
            and isinstance(error, HarnessResourceInterrupt)
        ):
            status = ledger.get_model_reservation(reservation)["status"]
            state = (
                "model_call_requires_reconciliation"
                if status == "uncertain"
                else "model_worker_resource_limit"
            )
            raise OperationBlocked(state) from error

    def _execute_reserved_role(
        self,
        selector,
        context,
        directory,
        intent,
        reservation,
        ledger,
        backend,
        campaign,
        allocation,
        configuration,
        offline,
    ):
        from devbench.engine.workflow_operations import OperationBlocked

        from .episode_codex import CodexActionSelector

        authority_attempt = {
            "invocation_id": intent["bindings"]["invocation_id"],
            "context_sha256": intent["context_sha256"],
            "role": intent["role"],
        }
        dispatch_entered = False
        local_interactive = campaign["policy"]["kind"] == LOCAL_INTERACTIVE_CODEX

        def before_start():
            nonlocal dispatch_entered
            if ledger.model_call_remaining(reservation) <= 0:
                raise OperationBlocked("campaign_deadline")
            ledger.activate_model_call(reservation)
            dispatch_entered = True

        try:
            allowed = self._read_reserved_role_authority(
                directory,
                intent,
                configuration,
                authority_attempt,
            )
            timeout = min(
                allocation["seconds"], ledger.model_call_remaining(reservation)
            )
            if timeout <= 0:
                raise OperationBlocked("campaign_deadline")
            # Each actor denies all earlier actor workspaces while retaining the
            # exact backbone configuration and the common private ledger root.
            if isinstance(selector, CodexActionSelector):
                selector.workspaces = list(
                    dict.fromkeys(
                        (*selector.workspaces, *backend.selector.workspaces),
                    )
                )
            payload = deepcopy(context)
            gate = backend.model_dispatch_gate(reservation)
            if offline:
                before_start()
            launch_options = {} if offline else {"_before_start": before_start}
            if local_interactive:
                launch_options.update(
                    _growth_window_seconds=_LOCAL_CODEX_GROWTH_WINDOW_SECONDS,
                )
            answer = selector.select(
                payload,
                directory,
                timeout_seconds=timeout,
                memory_mb=allocation["memory_mb"],
                max_context_bytes=allocation["context_bytes"],
                protected_model_gate=gate,
                allow_model_execution=allowed,
                **launch_options,
            )
            if not isinstance(answer, Selection):
                raise InvalidSelection("Campaign selectors must return a Selection")
            atomic_json(
                directory / "campaign-selection.json",
                {
                    "action": answer.action,
                    "evidence": answer.evidence,
                },
            )
            return self._settle(
                directory, intent, reservation, ledger, backend, campaign
            )
        except BaseException as error:
            self._recover_role_dispatch_error(
                directory,
                intent,
                reservation,
                ledger,
                backend,
                campaign,
                dispatch_entered,
                error,
            )
            raise
        finally:
            if isinstance(selector, CodexActionSelector):
                backend.selector.workspaces = list(
                    dict.fromkeys(
                        (*backend.selector.workspaces, *selector.workspaces),
                    )
                )

    def _read_reserved_role_authority(
        self,
        directory,
        intent,
        configuration,
        authority_attempt,
    ):
        intent_path = directory / "campaign-intent.json"
        if not intent_path.is_file():
            atomic_json(intent_path, intent)
        authority_path = directory / "selection-reservation.json"
        if not authority_path.exists():
            atomic_json(
                authority_path,
                {
                    "model_execution_authorized": self.allow_model_execution,
                    **reservation_binding(configuration, authority_attempt),
                },
            )
        return read_model_authority(
            authority_path,
            configuration=configuration,
            attempt=authority_attempt,
            maximum_authority=self.allow_model_execution,
        )

    def _settle(
        self,
        directory,
        intent,
        reservation,
        ledger,
        backend,
        campaign,
        *,
        retained=None,
    ):
        from devbench.engine.workflow_operations import OperationBlocked
        from devbench.study.evidence import _read, decode_json

        from .checkpoints import digest

        if (
            retained
            and retained["status"] in {"completed", "failed", "cancelled"}
            and retained["capture"]
        ):
            capture_ref = retained["capture"]["capture_ref"]
        else:
            capture_ref, _ = backend.verify_selection_capture(
                directory, bindings=intent["bindings"], reservation=reservation
            )
        settle = (
            ledger.reconcile_model_call
            if retained is not None
            else ledger.settle_model_call
        )
        receipt = settle(
            reservation, capture=capture_ref, verify_capture=backend.verify_capture
        )["capture"]
        current = ledger.get_campaign(campaign["campaign_id"])
        if current["status"] != "open":
            raise OperationBlocked(current.get("reason") or current["status"])
        if current["qualification"] == "violated":
            raise OperationBlocked("budget_policy_violation")
        if (
            current["unresolved_count"]
            or receipt["usage"]["normalized_total_tokens"] is None
        ):
            raise OperationBlocked("unresolved_usage")
        if time.time() >= current["deadline_epoch"]:
            raise OperationBlocked("campaign_deadline")
        if receipt["status"] != "completed":
            raise ValueError("Role selection differs from its actual protected capture")
        answer = backend._capture_selection(capture_ref)
        selection_path = directory / "campaign-selection.json"
        if selection_path.exists() and digest(
            decode_json(_read(selection_path, 1_000_000))["action"]
        ) != digest(answer.action):
            raise ValueError("Role selection differs from its actual protected capture")
        # The capture retains the full CLI and runtime evidence. Controller
        # journals need only the verified references and bounded accounting.
        identity = {
            key: answer.evidence.get(key)
            for key in ("backend", "actual_model", "model_identity_verified")
        }
        if campaign["policy"]["kind"] == LOCAL_INTERACTIVE_CODEX:
            identity["backend"] = LOCAL_INTERACTIVE_CODEX
        evidence = {
            **identity,
            "model_sessions_attempted": receipt["actual_calls"],
            "campaign_id": campaign["campaign_id"],
            "model_reservation_ref": reservation.ref,
            "model_capture_ref": capture_ref,
            "campaign_usage": receipt["usage"],
            "tokens": receipt["usage"]["normalized_total_tokens"],
            "role_identity": intent["identity"],
            "protocol_scope": intent["protocol_scope"],
        }
        atomic_json(
            directory / "campaign-result.json",
            {"action": answer.action, "evidence": evidence},
        )
        return Selection(answer.action, evidence)


class AdaptiveController:
    """Runtime capabilities for a constructive, revision-bound two-role loop.

    The catalog contains unexecuted admitted actions, not outcomes. Models pick
    among those actions; only WorkflowActions can apply them to the live DAG.
    """

    def __init__(
        self,
        model_invoker,
        *,
        broker,
        context_id,
        action_catalog,
        selector=None,
        challenger=None,
        review_builder=None,
        literature_handles=(),
        max_decisions=24,
        review_limits=None,
        benchmark_catalog=(),
        thesis_lifecycle=None,
        planning_source_id=None,
    ):
        from devbench.engine.tier_a_scrna import is_single_cell_operation
        from devbench.platform.adaptive_workflow import AdaptiveStep
        from devbench.platform.thesis_benchmark_actions import benchmark_plans
        from devbench.platform.thesis_workflow import ThesisWorkflow

        from .evidence_access import EvidenceBroker
        from .literature_adapter import EntitledControllerLiteratureQuery

        if (
            type(model_invoker) is not CampaignSelectionInvoker
            or type(broker) is not EvidenceBroker
        ):
            raise TypeError(
                "Adaptive controller requires trusted campaign and disclosure "
                "capabilities"
            )
        if type(max_decisions) is not int or not 2 <= max_decisions <= 24:
            raise ValueError("Controller engineering ceiling is 2 to 24 decisions")
        if type(action_catalog) is not dict or len(action_catalog) > 64:
            raise ValueError("Controller requires a bounded admitted action catalog")
        self.action_catalog = {
            key: AdaptiveStep.model_validate(value)
            for key, value in action_catalog.items()
        }
        self.benchmark_plans = benchmark_plans(benchmark_catalog)
        if thesis_lifecycle is not None and (
            type(thesis_lifecycle) is not ThesisWorkflow
            or thesis_lifecycle.ledger.path != broker.authority.ledger.path
            or thesis_lifecycle.lineage_id != model_invoker.session_id
        ):
            raise PermissionError("Use the controller's exact thesis workflow lineage")
        self.thesis_lifecycle = thesis_lifecycle
        if any(
            step.primary
            or step.held_out
            or "qualified_procedure_result" in step.profile.permitted_claims
            or (
                step.purpose != "exploratory"
                and not (
                    step.purpose == "checked_observation"
                    and is_single_cell_operation(step.operation_id)
                )
            )
            for step in self.action_catalog.values()
        ):
            raise PermissionError(
                "Controller catalog cannot grant qualified or held-out execution"
            )
        if review_builder is not None and not callable(review_builder):
            raise TypeError("Review builder is a coordinator-owned capability")
        if any(
            type(handle) is not EntitledControllerLiteratureQuery
            for handle in literature_handles
        ):
            raise TypeError("Literature queries require current entitled capabilities")
        self.invoker, self.broker, self.context_id = model_invoker, broker, context_id
        _, _, backend, campaign = model_invoker._runtime()
        self.local_interactive = campaign["policy"]["kind"] == LOCAL_INTERACTIVE_CODEX
        if planning_source_id is not None and not self.local_interactive:
            raise PermissionError("Workbench planning scope requires local selection")
        self.planning_source_id = planning_source_id
        if self.local_interactive and (
            challenger is not None
            or review_builder is not None
            or (selector is not None and selector is not backend.selector)
        ):
            raise PermissionError(
                "Single-operator interactive Codex requires its bound selector"
            )
        self.selector = (
            selector if selector is not None else model_invoker.default_selector()
        )
        self.challenger = (
            None
            if self.local_interactive
            else challenger
            if challenger is not None
            else model_invoker.default_selector()
        )
        self.review_builder, self.literature_handles = (
            review_builder,
            tuple(literature_handles),
        )
        self._review_capabilities = {}
        self._review_scope = None
        self.max_decisions = max_decisions
        from .scientific_review_holds import ReviewBudget

        default_budget = ReviewBudget()
        self.review_limits = review_limits or ReviewAgentLimits(
            max_selections=default_budget.invocations,
            max_queries=default_budget.queries,
        )

    def _pipeline_inputs(self, workflow, *, expected_revision=None, connection=None):
        from devbench.engine.workflow_operations import OperationConflict

        broker = self.broker
        if (
            workflow.ledger.path != broker.authority.ledger.path
            or workflow.lineage_id != self.invoker.session_id
        ):
            raise PermissionError("Pipeline compilation requires the bound lineage")

        def read(db):
            revision = workflow.authority._revision(db, workflow.lineage_id)
            if expected_revision is not None and revision != expected_revision:
                raise OperationConflict("Controller evidence changed before compile")
            context, policy = broker._context_policy(db, self.context_id)
            if (
                context["role"] != "controller"
                or context["phase"] != "analysis"
                or context["lineage_id"] != workflow.lineage_id
            ):
                raise PermissionError("Compilation requires the controller context")
            return self._eligible_pipeline_inputs(
                db,
                workflow._state(db),
                context,
                policy,
            )

        return workflow._read_projection(read, connection=connection)

    def _eligible_pipeline_inputs(self, db, state, context, policy):
        from devbench.core.contrast_design import (
            TIER_A_CONTINUOUS_REPRESENTATIONS,
            TIER_A_CONTROLS_REPRESENTATION,
            TIER_A_COUNT_REPRESENTATIONS,
            TIER_A_LENGTHS_REPRESENTATION,
            TIER_A_MOUNTED_CONTINUOUS_REPRESENTATIONS,
            TIER_A_MOUNTED_COUNT_REPRESENTATIONS,
        )
        from devbench.platform.adaptive_workflow import ArtifactVersion

        from .evidence_access import AccessDenied

        broker = self.broker
        representations = {
            "counts": (
                *TIER_A_COUNT_REPRESENTATIONS,
                *TIER_A_MOUNTED_COUNT_REPRESENTATIONS,
            ),
            "continuous_matrix": (
                *TIER_A_CONTINUOUS_REPRESENTATIONS,
                *TIER_A_MOUNTED_CONTINUOUS_REPRESENTATIONS,
            ),
            "table": (
                "metadata_csv",
                "family_spec_json",
                TIER_A_CONTROLS_REPRESENTATION,
                TIER_A_LENGTHS_REPRESENTATION,
            ),
        }
        candidates = {}
        for version_id, (raw, producer) in sorted(state.artifacts.items()):
            if producer is not None or version_id in state.stale_versions:
                continue
            artifact = ArtifactVersion.model_validate(raw)
            allowed_representations = representations.get(artifact.kind, ())
            if (
                artifact.representation not in allowed_representations
                or "admitted_research_inference" not in artifact.permitted_uses
                or artifact.selection_status != "none"
                or artifact.learned_scope != "none"
            ):
                continue
            dependencies = sorted(
                {
                    artifact.sha256,
                    *(
                        state.basis[key]["sha256"]
                        for key in state.sources.get(version_id, ())
                    ),
                }
            )
            peers = broker._source_peers(db, artifact.sha256)
            if not peers:
                continue
            source_id = min(peer["source_id"] for peer in peers)
            try:
                broker._query_dependencies(db, tuple(dependencies))
                for _, source in broker._source_ancestry(
                    db,
                    (source_id,),
                    limit_error=AccessDenied("Pipeline ancestry exceeds its bound"),
                ):
                    broker._check(context, policy, source, set())
            except AccessDenied:
                continue
            candidates[version_id] = (artifact, source_id, dependencies)
        return candidates

    def pipeline_input_candidates(
        self,
        workflow,
        *,
        expected_revision=None,
        connection=None,
    ):
        """Return original input identities and their disclosure dependencies."""
        inputs = self._pipeline_inputs(
            workflow,
            expected_revision=expected_revision,
            connection=connection,
        )
        fields = {
            "version_id",
            "sha256",
            "kind",
            "representation",
            "biological_unit",
            "unit_membership_sha256",
        }
        return [
            {
                "artifact": artifact.model_dump(mode="json", include=fields),
                "source_ids": [source_id],
                "dependencies": dependencies,
            }
            for artifact, source_id, dependencies in inputs.values()
        ]

    def compile_pipeline_step(
        self,
        workflow,
        decision,
        *,
        executor,
        expected_revision=None,
    ):
        """Compile registered bulk inputs into an unexecuted step."""
        from devbench.core._hashing import canonical_sha256
        from devbench.core.contrast_design import (
            TIER_A_BULK_OPERATION,
            TIER_A_BULK_OUTPUT,
        )
        from devbench.engine.workflow_operations import OperationConflict
        from devbench.platform.adaptive_workflow import AdaptiveStep

        from .method_planner_contract import validate_adaptive_controller_decision
        from .pipeline_compiler import compile_bulk_parameters

        action = validate_adaptive_controller_decision(decision)
        if action["kind"] != "propose_pipeline_step":
            raise ValueError("A pipeline proposal is required")
        if executor.ledger.path != workflow.ledger.path:
            raise PermissionError("Pipeline parameters must use the same ledger")
        candidates = self._pipeline_inputs(
            workflow,
            expected_revision=expected_revision,
        )
        spec, artifacts = compile_bulk_parameters(action, candidates, executor)
        parameters_sha256 = canonical_sha256(spec.model_dump(mode="json"))
        fit_inputs = tuple(
            (role, artifact) for role, artifact in artifacts.items() if role != "family"
        )
        step = AdaptiveStep.model_validate(
            {
                "step_id": action["step_id"],
                "branch_id": action["branch_id"],
                "operation_id": TIER_A_BULK_OPERATION,
                "environment_sha256": executor.environment_sha256,
                "parameters_sha256": parameters_sha256,
                "regime": spec.design.regime,
                "purpose": "admitted_research_inference",
                "primary": spec.family.primary,
                "comparison": not spec.family.primary,
                "profile": {
                    "profile_id": "tier-a-registered-bulk",
                    "version": 1,
                    "operations": [
                        {
                            "operation_id": TIER_A_BULK_OPERATION,
                            "output_kinds": ["table"],
                            "max_memory_mb": 2048,
                            "timeout_seconds": 300,
                        }
                    ],
                    "input_representations": [
                        *dict.fromkeys(
                            artifact.representation for _, artifact in fit_inputs
                        ),
                    ],
                    "biological_units": [spec.biological_unit],
                    "design_scope": [spec.design.regime],
                    "permitted_claims": ["admitted_research_inference"],
                },
                "inputs": [
                    {
                        "port": {"name": role, "artifact_kind": artifact.kind},
                        "source_id": artifact.version_id,
                        "source_port": None,
                    }
                    for role, artifact in fit_inputs
                ],
                "outputs": [{"name": TIER_A_BULK_OUTPUT, "artifact_kind": "table"}],
            }
        )
        if executor.register_pipeline(spec) != parameters_sha256:
            raise OperationConflict("Registered pipeline differs from compiled bytes")
        return step

    def bind(self, workflow):
        from devbench.engine.workflow_operations import OperationBlocked

        from .checkpoints import digest

        state, ledger, backend, campaign = self.invoker._runtime()
        if (
            workflow.ledger.path != ledger.path
            or self.broker.authority.ledger.path != ledger.path
            or workflow.lineage_id != self.invoker.session_id
        ):
            raise PermissionError(
                "Both roles, disclosure and computation require one "
                "authoritative lineage"
            )
        with ledger._database() as db:
            db.execute("BEGIN")
            context, _ = self.broker._context_policy(db, self.context_id)
            if (
                context["role"] != "controller"
                or context["lineage_id"] != workflow.lineage_id
            ):
                raise PermissionError(
                    "Controller disclosure context belongs to another role or lineage"
                )
        protected = campaign["identity"].get("protected_parent_allocations", {})
        required = {"adaptive_termination"}
        if not self.local_interactive:
            required.add("scientific_review")
        if not required <= protected.keys():
            raise OperationBlocked("current_review_and_termination_reserve_required")
        binding = {
            "campaign_id": campaign["campaign_id"],
            "campaign_identity_sha256": campaign["identity_digest"],
            "catalog_sha256": digest(
                {
                    key: value.model_dump(mode="json")
                    for key, value in self.action_catalog.items()
                }
            ),
            "max_decisions": self.max_decisions,
            "max_review_cycles": 0 if self.local_interactive else 8,
            "max_sweep_members": 24,
            "max_unchanged_infrastructure_retries": 2,
            "controller_sha256": digest(selector_configuration(self.selector)),
            "challenger_sha256": None
            if self.local_interactive
            else digest(selector_configuration(self.challenger)),
            "context_id": self.context_id,
            "review_limits": asdict(self.review_limits),
            "transport_limits": asdict(self.invoker.limits),
            "literature_sha256": digest(
                [
                    handle.descriptor().model_dump(mode="json")
                    for handle in self.literature_handles
                ]
            ),
            "qualification_status": "not_established",
            "scope": LOCAL_INTERACTIVE_CODEX
            if self.local_interactive
            else "engineering_protocol",
        }
        scope = (workflow.ledger.path, workflow.lineage_id)
        if self._review_scope is not None and self._review_scope != scope:
            raise PermissionError(
                "An active controller cannot switch scientific ledgers"
            )
        current = workflow.bind_controller(binding)
        if self._review_scope is None:
            self._restore_review_capabilities(workflow, current)
            self._review_scope = scope
        return current

    def _restore_review_capabilities(self, workflow, controller_state):
        from devbench.engine.workflow_operations import OperationBlocked

        from .workflow_scientific_reviews import WorkflowScientificReviewAdapter

        if self.review_builder is None:
            return
        latest = {
            row["decision"]["touchpoint"]: row
            for row in controller_state["decisions"]
            if row["decision"]["kind"] == "review"
        }
        material = WorkflowScientificReviewAdapter.adaptive_material_sha256(
            self.invoker.planner, workflow
        )
        for touchpoint, row in latest.items():
            expected = row["result"]
            basis = expected.get("controller_review_basis", {})
            if (
                expected.get("status") != "completed"
                or basis.get("material_sha256") != material
                or "capability_sha256" not in basis
            ):
                continue
            try:
                active = self._build_review(workflow, touchpoint, row["sequence"])
                retained, active = self._restore_review(
                    workflow, touchpoint, row["sequence"], material, active
                )
                if retained != expected:
                    continue
                WorkflowScientificReviewAdapter.require_adaptive_review_currentness(
                    self.invoker.planner, workflow, retained, active
                )
            except (OperationBlocked, OSError, PermissionError, ValueError):
                # A revoked, stale or unavailable binding grants no current capability.
                continue
            self._review_capabilities[row["sequence"]] = active

    def select(self, workflow, context, *, expected_revision=None):
        with workflow.ledger._operation_lock(
            (workflow.lineage_id, "adaptive-controller-select", 0)
        ):
            return self._select(workflow, context, expected_revision=expected_revision)

    def _select(self, workflow, context, *, expected_revision=None):
        from devbench.engine.workflow_operations import (
            OperationBlocked,
            OperationConflict,
        )

        from .checkpoints import digest
        from .rna_local_interactive import local_controller_evidence

        if (
            expected_revision is not None
            and workflow.status()["revision"] != expected_revision
        ):
            raise OperationConflict(
                "Adaptive workflow revision changed before selection"
            )

        state = workflow.controller_state()
        pending = state["pending"]
        if pending is None:
            pending = workflow.begin_controller_decision(
                context, expected_revision=context["revision"]
            )
        if pending["decision"] is not None:
            return pending
        context = pending["context"]
        sequence = pending["sequence"]
        feedback = pending.get("rejected_selections", [])
        max_invalid = min(2, self.invoker.limits.max_selections)
        while True:
            identities = {note["response_sha256"] for note in feedback}
            if len(identities) != len(feedback):
                raise OperationBlocked("controller_repeated_invalid_selection")
            if len(feedback) >= max_invalid:
                raise OperationBlocked("controller_invalid_selection_budget_exhausted")
            selected_context = (
                context
                if not feedback
                else {**context, "selection_feedback": feedback[-1]}
            )
            raw = None
            try:
                choice, disclosure = self._select_context(
                    workflow, selected_context, sequence, len(feedback)
                )
                raw = choice.action
                visible = (
                    {"evidence": local_controller_evidence(context)}
                    if self.local_interactive
                    else context
                )
                decision = self._validate_controller_selection(raw, visible)
            except InvalidSelection as error:
                _, _, _, campaign = self.invoker._runtime()
                if campaign["unresolved_count"]:
                    raise OperationBlocked("unresolved_usage") from error
                reason = (
                    str(error)
                    if raw is not None
                    else "invalid_structured_controller_response"
                )
                semantic = raw.get("action", raw) if isinstance(raw, dict) else raw
                if isinstance(semantic, dict):
                    semantic = {
                        key: value
                        for key, value in semantic.items()
                        if key != "rationale"
                    }
                feedback = workflow.reject_controller_selection(
                    sequence,
                    len(feedback),
                    {"reason": reason, "response_sha256": digest(semantic)},
                )
                continue
            return workflow.select_controller_decision(
                sequence, decision, {**choice.evidence, **disclosure}
            )

    @staticmethod
    def _validate_controller_selection(raw, context):
        from .method_planner_contract import validate_adaptive_controller_decision

        try:
            decision = validate_adaptive_controller_decision(raw)
        except ValueError as error:
            raise InvalidSelection("invalid_controller_decision_schema") from error
        if set(decision["evidence_ids"]) - context["evidence"].keys():
            raise InvalidSelection("controller_cited_unentitled_evidence")
        return decision

    def _select_context(self, workflow, context, sequence, feedback_index):
        from devbench.engine.workflow_operations import OperationBlocked

        from .checkpoints import digest
        from .controller_research_dispatch import (
            BRIEF_EVIDENCE_KEY,
            build_dispatch_brief,
        )
        from .rna_local_interactive import local_controller_payload

        identity = [workflow.lineage_id, sequence]
        if feedback_index:
            identity.append(feedback_index)
        source_id = "controller-input-" + digest(identity)[:32]
        ancestry = context.get("disclosure_ancestry")
        ancestry_types = {
            "parents": list,
            "sources": dict,
            "dependencies": list,
            "payload_peers": dict,
        }
        if (
            type(ancestry) is not dict
            or ancestry.keys() != ancestry_types.keys()
            or any(
                type(ancestry[key]) is not kind for key, kind in ancestry_types.items()
            )
        ):
            raise OperationBlocked("current_controller_disclosure_binding_required")
        scientific_state = context.get("scientific_state", {})
        if scientific_state.get("format") != "devbench.adaptive-scientific-context/1":
            raise OperationBlocked("current_compact_controller_context_required")
        with workflow.ledger._database() as db:
            db.execute("BEGIN")
            brief = build_dispatch_brief(workflow, db, context)
            sources = [self.broker._source(db, key) for key in ancestry["sources"]]
        disclosure_context = (
            local_controller_payload(context) if self.local_interactive else context
        )
        payload = disclosure_context if brief is None else brief.payload(source_id)
        self.broker.register_source(
            source_id,
            content_sha256=digest(payload),
            **self._controller_selection_source(workflow, ancestry, sources, brief),
        )

        def current_disclosure(db):
            if brief is not None:
                brief.require_current(workflow, db)
            self._require_current_controller_disclosure(
                db,
                workflow,
                context,
                source_id,
                ancestry,
                scientific_state,
                disclosure_context,
            )

        output = (
            self.invoker.planner.methods.root
            / "adaptive-controller"
            / workflow.lineage_id
            / str(sequence)
        )
        node = "adaptive-controller"
        if feedback_index:
            output = output / f"feedback-{feedback_index}"
            node = f"{node}-feedback-{feedback_index}"
        delivery = self.broker.reserve(
            self.context_id,
            source_id,
            payload,
            _guard=current_disclosure,
            _controller_context=True,
        )
        if delivery["state"] == "reserved":
            self.broker.mark_attempted(
                delivery["reservation_id"],
                _guard=current_disclosure,
            )
        choice = self.invoker.invoke(
            self.selector,
            payload,
            output,
            role="controller",
            purpose=(
                "adaptive_termination"
                if context["termination_required"]
                else "adaptive_controller"
            ),
            node=node,
            attempt=sequence,
        )
        evidence = {
            "controller_disclosure_source_id": source_id,
            "controller_disclosure_reservation_id": delivery["reservation_id"],
        }
        if brief is not None:
            try:
                choice = replace(choice, action=brief.resolve_action(choice.action))
            except ValueError as error:
                raise InvalidSelection("invalid_reviewed_research_dispatch") from error
            evidence[BRIEF_EVIDENCE_KEY] = digest(payload)
        return choice, evidence

    def _controller_selection_source(self, workflow, ancestry, sources, brief):
        from devbench.engine.workflow_operations import OperationBlocked

        from .checkpoints import digest

        if brief is not None:
            return {"roots": brief.roots, "classification": "structural"}
        plan = self.invoker.planner.get(workflow.lineage_id)
        planning_sha256 = digest(plan["context"])
        roots = {root for source in sources for root in source["roots"]}
        if self.planning_source_id is None:
            roots.add(planning_sha256)
        elif not any(
            source["source_id"] == self.planning_source_id
            and source["content_sha256"] == planning_sha256
            for source in sources
        ):
            raise OperationBlocked("current_controller_disclosure_binding_required")
        tags = {tag for source in sources for tag in source["tags"]}
        scope = {
            name: ()
            if any(not source[name] for source in sources)
            else tuple(sorted({value for source in sources for value in source[name]}))
            for name in ("units", "populations")
        }
        return {
            "roots": tuple(sorted(roots)),
            "tags": tuple(sorted(tags)),
            **scope,
            "parents": tuple(ancestry["parents"]),
            "classification": "structural" if self.local_interactive else "unknown",
        }

    def _require_current_controller_disclosure(
        self,
        db,
        workflow,
        context,
        source_id,
        ancestry,
        scientific_state,
        disclosure_context,
    ):
        from devbench.engine.workflow_operations import OperationBlocked

        from .checkpoints import digest
        from .evidence_access import _fields
        from .scientific_review_holds import ReviewHoldLifecycle

        self.broker._query_dependencies(db, tuple(ancestry["dependencies"]))
        recipient, policy = self.broker._context_policy(db, self.context_id)
        for exposure in scientific_state["exposure_history"]["records"]:
            if "reservation_id" in exposure:
                reservation = self.broker._get(
                    db,
                    "reservation",
                    exposure["reservation_id"],
                )
                _require_controller_exposure(
                    db,
                    self.broker,
                    recipient,
                    policy,
                    reservation,
                )
        review = ReviewHoldLifecycle(workflow.authority, workflow.lineage_id)
        for hold in context["open_holds"]:
            if hold.get("content_status") == "withheld_by_access_policy":
                continue
            origins = review.hold_origins(self.broker, hold["hold_id"], connection=db)
            if not set().union(*origins["groups"]) <= ancestry[
                "sources"
            ].keys() or not origins["dependencies"] <= set(ancestry["dependencies"]):
                raise OperationBlocked("controller_hold_ancestry_changed")
        for identity, expected in ancestry.get("payload_peers", {}).items():
            current = sorted(
                row["source_id"] for row in self.broker._source_peers(db, identity)
            )
            if current != expected:
                raise OperationBlocked("controller_payload_ancestry_changed")
        # Authorize the current closure, including late peers and the delivery
        # source, both when reserving and when attempting disclosure.
        fields = _fields(disclosure_context)
        for identity, source in self.broker._source_ancestry(
            db,
            [source_id, *ancestry["sources"]],
            limit_error=OperationBlocked(
                "controller_disclosure_ancestry_exceeds_bound"
            ),
        ):
            if (
                identity in ancestry["sources"]
                and digest(source) != ancestry["sources"][identity]
            ):
                raise OperationBlocked("controller_source_ancestry_changed")
            self.broker._check(recipient, policy, source, fields)
        self._require_current_controller_artifacts(db, workflow, context)

    def _require_current_controller_artifacts(self, db, workflow, context):
        from devbench.engine.workflow_operations import OperationBlocked

        pipeline_inputs = context.get("pipeline_inputs", ())
        if pipeline_inputs:
            current_inputs = self.pipeline_input_candidates(workflow, connection=db)
            if pipeline_inputs != [row["artifact"] for row in current_inputs]:
                raise OperationBlocked("adaptive_pipeline_inputs_changed")
        for item in context["evidence"].values():
            if "artifact" not in item:
                continue
            if item.get("content_status") == "admitted_pipeline_input":
                if item["artifact"] not in pipeline_inputs:
                    raise OperationBlocked("adaptive_pipeline_inputs_changed")
                continue
            artifact = workflow._current_use(
                db,
                item["step_id"],
                item["port"],
                "compute",
                None,
            )
            if artifact.model_dump(mode="json") != item["artifact"]:
                raise OperationBlocked("controller_evidence_changed_before_disclosure")

    def resume(self, workflow, *, expected_revision=None):
        "Continue the retained grant only after a changed frontier or reconciliation."
        from devbench.engine.workflow_operations import (
            OperationBlocked,
            OperationConflict,
        )

        from .checkpoints import digest

        if (
            expected_revision is not None
            and workflow.status()["revision"] != expected_revision
        ):
            raise OperationConflict(
                "Adaptive workflow revision changed before resumption"
            )
        _, _, _, campaign = self.invoker._runtime()
        state = workflow.controller_state()
        if state["outcome"] is None:
            return state
        if campaign["unresolved_count"] or campaign["qualification"] == "violated":
            raise OperationBlocked("current_campaign_reconciliation_required")
        if time.time() >= campaign["deadline_epoch"]:
            raise OperationBlocked("campaign_deadline")
        if len(state["decisions"]) >= self.max_decisions:
            raise OperationBlocked("controller_decision_budget_exhausted")
        snapshot = workflow.projection_snapshot()
        snapshot.pop("controller_outcome", None)
        if state["pending"] is not None:
            self.select(
                workflow,
                state["pending"]["context"],
                expected_revision=expected_revision,
            )
            if expected_revision is not None:
                expected_revision = workflow.status()["revision"]
        elif digest(snapshot) == state["outcome"]["graph_sha256"]:
            from .scientific_review_holds import ReviewHoldLifecycle

            current_holds = {
                identity
                for identity, hold in ReviewHoldLifecycle(
                    workflow.authority, workflow.lineage_id
                )
                .state()["holds"]
                .items()
                if hold["status"] == "open"
            }
            if current_holds == {
                hold["hold_id"] for hold in state["outcome"].get("open_holds", ())
            }:
                raise OperationBlocked("resumption_requires_changed_evidence")
        return workflow.resume_controller(
            expected_outcome_sha256=digest(state["outcome"]),
            expected_revision=expected_revision,
        )

    def _build_review(self, workflow, touchpoint, sequence):
        from devbench.engine.workflow_operations import OperationBlocked

        from .workflow_scientific_reviews import ActiveWorkflowReview

        if self.local_interactive:
            raise OperationBlocked("single_operator_challenger_unavailable")
        if self.review_builder is None:
            raise OperationBlocked("fresh_active_review_builder_required")
        active = self.review_builder(workflow, touchpoint, sequence)
        if (
            type(active) is not ActiveWorkflowReview
            or active.packet.touchpoint != touchpoint
            or active.lifecycle.lineage_id != workflow.lineage_id
            or active.lifecycle.ledger.path != workflow.ledger.path
            or active.broker is not self.broker
            or active.controller_context_id != self.context_id
        ):
            raise ValueError(
                "Active review capabilities differ from this exact controller lineage"
            )
        return active

    @staticmethod
    def _review_capability_sha256(active):
        from .checkpoints import digest

        return digest(
            {
                "context_id": active.context_id,
                "reveal_context_id": active.reveal_context_id,
                "controller_context_id": active.controller_context_id,
                "query_handles": [
                    handle.descriptor().model_dump(mode="json")
                    for handle in active.query_handles
                ],
                "literature_handles": [
                    handle.descriptor().model_dump(mode="json")
                    for handle in active.literature_handles
                ],
            }
        )

    def _review_root(self, workflow, sequence):
        return (
            self.invoker.planner.methods.root
            / "adaptive-reviews"
            / workflow.lineage_id
            / str(sequence)
        )

    def _restore_review(self, workflow, touchpoint, sequence, material, active):
        """Use a current coordinator grant and the exact already delivered packet."""
        import hashlib
        from dataclasses import replace

        from devbench.engine.workflow_operations import OperationBlocked
        from devbench.study.evidence import _read, decode_json
        from devharness_core.provenance import verify_execution

        from .checkpoints import digest
        from .episode_proposals import _bind_manifest_snapshot, _load
        from .evidence_access import _fields
        from .scientific_review import ActiveReviewPacket
        from .workflow_scientific_reviews import _verify_active_current

        root = self._review_root(workflow, sequence)
        basis = decode_json(_read(root.with_name(f"{sequence}-material.json"), 64_000))
        if (
            basis.get("material_sha256") != material
            or basis.get("touchpoint") != touchpoint
            or basis.get("sequence") != sequence
            or basis.get("capability_sha256") != self._review_capability_sha256(active)
        ):
            raise OperationBlocked("adaptive_review_material_changed")
        packet_receipt = decode_json(
            _read(root / "packet" / "root-receipt.json", 64_000)
        )
        _, raw_packet, _ = _load(
            root / "packet" / "audit" / "manifest.json",
            packet_receipt["root_hash"],
            "packet.json",
        )
        if digest(raw_packet) != basis.get("packet_sha256"):
            raise OperationBlocked("adaptive_review_packet_changed")
        packet = ActiveReviewPacket.model_validate_json(json.dumps(raw_packet))
        # Access history legitimately grows during the original review. Keep
        # the exposure actually frozen in that packet, with current live grants.
        rebuilt = active.packet.model_dump(mode="json")
        rebuilt["prior_exposure"] = raw_packet["prior_exposure"]
        rebuilt["binding"]["exposure_sha256"] = raw_packet["binding"]["exposure_sha256"]
        if rebuilt != raw_packet:
            raise OperationBlocked("adaptive_review_packet_changed")

        result_raw = _read(root / "result.json", 1_000_000)
        result = decode_json(result_raw)
        manifest_path = root / "audit" / "manifest.json"
        manifest = decode_json(_read(manifest_path, 1_000_000))
        receipt = {
            "schema_version": manifest["schema_version"],
            "manifest_path": str(manifest_path),
            "root_hash": manifest["root_hash"],
            "node_count": manifest["node_count"],
            "artifact_count": len(manifest["artifacts"]),
            "analysis_fingerprint": manifest["analysis_fingerprint"],
        }
        if (
            result.get("cycle_id")
            != "adaptive-review-" + digest([workflow.lineage_id, sequence])[:32]
            or result.get("packet_sha256") != basis["packet_sha256"]
            or receipt["analysis_fingerprint"] != basis["packet_sha256"]
            or not any(
                item.get("name") == "result.json"
                and item.get("sha256") == hashlib.sha256(result_raw).hexdigest()
                for item in manifest["artifacts"]
            )
        ):
            raise OperationBlocked("adaptive_review_capture_changed")
        result["receipt"] = receipt
        with workflow.ledger._database() as db:
            db.execute("BEGIN")
            context, policy = self.broker._context_policy(db, self.context_id)
            source = self.broker._source(db, "review-return-" + result["cycle_id"])
            if source["content_sha256"] != digest(result):
                raise OperationBlocked("adaptive_review_delivery_changed")
            self.broker._check(context, policy, source, _fields(result))
        _bind_manifest_snapshot(manifest, receipt["root_hash"])
        if not verify_execution(manifest_path, receipt["root_hash"])["valid"]:
            raise OperationBlocked("adaptive_review_capture_changed")
        active = replace(active, packet=packet)
        _verify_active_current({"active_review": result, "status": "blocked"}, active)
        return {**result, "controller_review_basis": basis}, active

    @staticmethod
    def _archive_unstarted_review(root, basis_path, sequence):
        from pathlib import Path
        from tempfile import mkdtemp

        # Before open_cycle there can be tentative setup files, but
        # the coordinator has not admitted reviewer execution.
        if root.exists() or basis_path.exists():
            archive = Path(mkdtemp(prefix=f"{sequence}-unstarted-", dir=root.parent))
            if root.exists():
                root.rename(archive / "review")
            if basis_path.exists():
                basis_path.rename(archive / basis_path.name)

    def review(self, workflow, touchpoint, sequence):
        from dataclasses import replace

        from devbench.engine.workflow_operations import OperationBlocked

        from .checkpoints import digest
        from .scientific_review_agent import request_active_review
        from .workflow_scientific_reviews import WorkflowScientificReviewAdapter

        prior = workflow.controller_state()["decisions"]
        if sum(item["decision"]["kind"] == "review" for item in prior) >= 8:
            raise OperationBlocked("challenger_cycle_budget_exhausted")
        active = self._build_review(workflow, touchpoint, sequence)
        material = WorkflowScientificReviewAdapter.adaptive_material_sha256(
            self.invoker.planner, workflow
        )
        root = self._review_root(workflow, sequence)
        cycle_id = "adaptive-review-" + digest([workflow.lineage_id, sequence])[:32]
        basis = {
            "material_sha256": material,
            "packet_sha256": digest(active.packet.model_dump(mode="json")),
            "capability_sha256": self._review_capability_sha256(active),
            "touchpoint": touchpoint,
            "sequence": sequence,
        }
        root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        basis_path = root.with_name(f"{sequence}-material.json")
        with workflow.ledger._operation_lock(
            (workflow.lineage_id, "adaptive-active-review", sequence)
        ):
            review_state = active.lifecycle.state()
            if (root / "result.json").is_file():
                result, active = self._restore_review(
                    workflow, touchpoint, sequence, material, active
                )
                self._review_capabilities[sequence] = active
                return result
            if cycle_id in review_state["cycles"]:
                raise OperationBlocked("active_review_requires_reconciliation")

            self._archive_unstarted_review(root, basis_path, sequence)
            usage, budget = review_state["accounting"], active.lifecycle.budget
            remaining_invocations = min(
                budget.invocations - usage["invocations"], budget.turns - usage["turns"]
            )
            remaining_queries = budget.queries - usage["queries"]
            if remaining_invocations <= 0 or remaining_queries <= 0:
                raise OperationBlocked(
                    "challenger_invocation_or_query_budget_exhausted"
                )
            review_limits = replace(
                self.review_limits,
                max_selections=min(
                    self.review_limits.max_selections, remaining_invocations
                ),
                max_queries=min(self.review_limits.max_queries, remaining_queries),
            )
            atomic_json(basis_path, basis)
            try:
                result = request_active_review(
                    root,
                    active.packet,
                    lifecycle=active.lifecycle,
                    cycle_id=cycle_id,
                    reviewer_id="challenger",
                    selector=self.challenger,
                    broker=self.broker,
                    context_id=active.context_id,
                    reveal_context_id=active.reveal_context_id,
                    controller_context_id=self.context_id,
                    query_handles=active.query_handles,
                    literature_handles=active.literature_handles,
                    limits=review_limits,
                    allow_model_execution=self.invoker.allow_model_execution,
                    model_data_scope=self.invoker.planner.get(workflow.lineage_id)[
                        "data_classification"
                    ],
                    model_invoker=self.invoker,
                )
            except BaseException:
                if cycle_id not in active.lifecycle.state()["cycles"]:
                    self._archive_unstarted_review(root, basis_path, sequence)
                raise
        if material != WorkflowScientificReviewAdapter.adaptive_material_sha256(
            self.invoker.planner, workflow
        ):
            raise OperationBlocked("adaptive_review_material_changed")
        self._review_capabilities[sequence] = active
        return {**result, "controller_review_basis": basis}

    def require_current_completion_reviews(
        self, workflow, *, review_binding=None, connection=None
    ):
        from devbench.engine.workflow_operations import OperationBlocked

        from .workflow_scientific_reviews import WorkflowScientificReviewAdapter

        latest = {
            item["decision"]["touchpoint"]: item
            for item in workflow.controller_state(connection=connection)["decisions"]
            if item["decision"]["kind"] == "review"
        }
        if set(latest) != {"design", "publication"}:
            raise OperationBlocked("current_reviews_and_resolved_scope_required")
        for item in latest.values():
            if item["result"].get("status") != "completed":
                raise OperationBlocked("current_completed_reviews_required")
            active = self._review_capabilities.get(item["sequence"])
            WorkflowScientificReviewAdapter.require_adaptive_review_currentness(
                self.invoker.planner,
                workflow,
                item["result"],
                active,
                review_binding=review_binding,
                connection=connection,
            )


def _scrna_effect_projection(target, diagnostics, gene_ids, annotation_labels):
    """Bounded aggregates from a caller-verified cell-state result, never rows."""
    import re
    from collections import Counter

    from devbench.study.biology_answers import public_hypothesis_id

    payload = target.get("payload") or {}
    rows = payload.get("gene_statistics", [])
    labels = sorted(annotation_labels)
    expected = (
        [(gene, label) for label in labels for gene in gene_ids]
        if len(labels) <= 50
        else []
    )
    tested = [
        row.get("hypothesis_id") for row in rows if row.get("p_value") is not None
    ]
    valid = (
        target.get("target_id") == "cell_state_effect"
        and payload.get("kind") == "effects"
        and bool(labels)
        and len(labels) == len(set(labels))
        and len(labels) <= 50
        and [(row.get("gene_id"), row.get("component_id")) for row in rows] == expected
        and [row.get("hypothesis_id") for row in rows]
        == [public_hypothesis_id(gene, label) for gene, label in expected]
        and payload.get("multiplicity_scope") == tested
    )
    reasons = Counter()
    for row in rows:
        if row.get("estimate_status") != "estimated":
            reason = row.get("missing_reason", "reason_unavailable")
            reasons[
                reason
                if isinstance(reason, str)
                and re.fullmatch(r"[A-Za-z0-9_]{1,160}", reason)
                else "other_or_unavailable"
            ] += 1
    details = diagnostics.get("cell_state_effect", {}).get("fits_by_type", {})
    summaries = {}
    for label in labels[:50]:
        info = details.get(label, {})
        design = info.get("design", {})
        calibration = info.get("calibration", {})
        dispersions = calibration.get("gene_estimates", {})
        converged = Counter(
            "converged"
            if item.get("dispersion_converged") is True
            else "not_converged"
            if item.get("dispersion_converged") is False
            else "unknown"
            for item in dispersions.values()
        )
        summaries[label] = {
            "status": "available" if info else "unavailable",
            "design": {
                key: design[key]
                for key in (
                    "rank",
                    "column_count",
                    "residual_df",
                    "condition_number",
                    "condition_number_validated",
                    "independent_donors_per_condition",
                )
                if key in design
            },
            "dispersion_status_counts": dict(converged),
            "nonfinite_condition_contrast_count": len(
                info.get("nonfinite_condition_contrast_genes", [])
            ),
            "model_warning_gene_count": len(info.get("model_warnings", {})),
            "calibration_diagnostic_count": len(
                info.get("calibration_diagnostics", [])
            ),
        }
    return {
        "full_attempted_BH_denominator": len(rows) if valid else None,
        "family_structure_verified": valid,
        "adjustment": "Benjamini_Hochberg_over_all_attempted_hypotheses"
        if valid
        else None,
        "unavailable_reason_counts": dict(reasons.most_common(20)),
        "unavailable_reason_categories_omitted": max(len(reasons) - 20, 0),
        "fit_diagnostics_by_type": summaries,
    }


def _verified_checkpoint_result(episode, action, stage, bounds):
    from devbench.study.evidence import _read, decode_json

    from .checkpoints import digest, sha256
    from .episode_synthesis import _observation_bindings

    if episode.checkpoints.verify(stage) is None:
        raise ValueError("projection_checkpoint_unavailable")
    path = episode.root / "checkpoints" / stage / "observation.json"
    response = decode_json(_read(path, bounds["max_checkpoint_bytes"]))
    binding = _observation_bindings(
        episode.root,
        list(episode.audit._node_ids),
    ).get(action, {})
    receipt = episode.root / "checkpoints" / stage / "receipt.json"
    if binding.get("result_sha256") != sha256(path) or binding.get(
        "checkpoint_receipt", {}
    ).get("sha256") != sha256(receipt):
        raise ValueError("projection_observation_binding_mismatch")
    matches = [
        event
        for event in episode.broker.events
        if event["receipt_id"] == response.get("receipt_id")
    ]
    if len(matches) != 1 or response.get("status") != "succeeded":
        raise ValueError("projection_successful_receipt_required")
    event = matches[0]
    event_body = {key: value for key, value in event.items() if key != "receipt_id"}
    if digest(event_body) != event["receipt_id"]:
        raise ValueError("projection_event_hash_mismatch")
    result_path = episode.broker.output_dir / (event["receipt_id"] + ".json")
    if sha256(result_path) != event.get("result_sha256"):
        raise ValueError("projection_result_hash_mismatch")
    result = decode_json(_read(result_path, bounds["max_result_bytes"]))
    if episode.broker._public_result(result) != response.get("result"):
        raise ValueError("projection_full_result_mismatch")
    return response, result, event, path, receipt


def _checkpoint_projection(episode, action):
    """Rebuild a whole-result aggregate from independently bound full evidence."""
    from collections import Counter

    from devbench.study.evidence import _read, decode_json

    from .checkpoints import sha256
    from .episodes import episode_evidence_limits

    bounds = episode_evidence_limits(episode.config)
    stage = f"action-{action:04d}"
    response, result, event, path, receipt = _verified_checkpoint_result(
        episode,
        action,
        stage,
        bounds,
    )
    targets, omitted = [], 0
    diagnostics = result.get("diagnostics", {})
    family = diagnostics.get("gene_test_adjustment_family", {})
    annotation_labels = []
    if episode.config["assay"] == "scrna":
        annotations = decode_json(
            _read(episode.root / "inputs" / "annotations.json", 1_000_000)
        )
        annotation_labels = [
            item["label"]
            for item in annotations.get("cell_annotation_reference", {}).get(
                "markers", []
            )
        ]
    for target in result.get("target_results", []):
        payload = target.get("payload") or {}
        rows = payload.get("gene_statistics", [])
        omitted += len(rows)
        row_ids = [row.get("hypothesis_id") for row in rows]
        denominator = (
            family.get("attempted_count")
            if (
                episode.config["assay"] == "bulk"
                and payload.get("kind") == "effects"
                and family.get("adjustment")
                == "Benjamini_Hochberg_over_all_attempted_hypotheses"
                and type(family.get("attempted_count")) is int
                and family["attempted_count"] == len(rows)
                and family.get("attempted_hypothesis_ids")
                == row_ids
                == episode.config["public_task"]["gene_ids"]
            )
            else None
        )
        cell_summary = (
            _scrna_effect_projection(
                target,
                diagnostics,
                episode.config["public_task"]["gene_ids"],
                annotation_labels,
            )
            if episode.config["assay"] == "scrna"
            else {}
        )
        targets.append(
            {
                "target_id": target["target_id"],
                "claim_type": target["claim_type"],
                "estimate_status": target["estimate_status"],
                "evidence_scope": target["evidence_scope"],
                "limitation_codes": target.get("limitation_codes", []),
                "attempted_gene_rows": len(rows),
                "full_attempted_BH_denominator": denominator,
                "status_counts": dict(Counter(row["estimate_status"] for row in rows)),
                "tested_gene_rows": sum(row.get("p_value") is not None for row in rows),
                "biological_unit": payload.get("biological_unit"),
                "analysis_layer": payload.get("analysis_layer"),
                "computed_contrast": payload.get("computed_contrast"),
                "selection_conditional_inference": payload.get(
                    "selection_conditional_inference"
                ),
                **cell_summary,
            }
        )
    summary = {
        "format": "devbench.episode-result-projection/1",
        "action": action,
        "source": {
            "receipt_id": event["receipt_id"],
            "result_sha256": event["result_sha256"],
            "checkpoint_sha256": sha256(path),
            "checkpoint_receipt_sha256": sha256(receipt),
        },
        "scope": (
            "whole_result_aggregates_not_individual_gene_evidence_"
            "or_scientific_validation"
        ),
        "full_result_retained": True,
        "individual_gene_rows_in_prompt": 0,
        "omitted_gene_rows": omitted,
        "declared_family_gene_count": len(episode.config["public_task"]["gene_ids"]),
        "targets": targets,
        "diagnostics": {
            key: diagnostics[key]
            for key in (
                "status",
                "analysis_error",
                "design",
                "designs",
                "biological_units",
            )
            if key in diagnostics
        },
    }
    projected = {
        key: response.get(key)
        for key in ("receipt_id", "status", "error", "input_sha256")
    }
    projected["result_summary"] = summary
    scientific_receipt = (
        episode.root / "checkpoints" / stage / "scientific-execution.json"
    )
    if scientific_receipt.is_file():
        projected["scientific_execution"] = decode_json(
            _read(scientific_receipt, 128_000)
        )
    if len(json.dumps(projected, allow_nan=False).encode()) > 128_000:
        raise ValueError("projection_summary_exceeds_prompt_bound")
    return _json_copy(projected)


def analyst_policy(episode):
    return (episode.agent_policy or {}).get("analyst_profile") == "analyst_v1"


def authoritative_scientific_context(
    episode,
    history,
    *,
    context_bytes_used,
    thesis_lifecycle=None,
):
    """Compact scientific state alongside guarded exact evidence references.

    Failed/rejected attempts and supplied unknowns remain in this state even
    when large numerical results are projected. No concern is auto-resolved by
    a successful calculation, and saved checkpoints never renew a budget.
    """
    from devbench.study.evidence import _read, decode_json

    from .checkpoints import sha256

    task = episode.config["public_task"]
    concerns, failures, specifications = [], [], []
    assessments = []
    for reference in getattr(episode, "decision_records", []):
        selection = reference.get("selection")
        if type(selection) is not int or not 1 <= selection <= 32:
            raise ValueError("scientific_context_decision_binding_invalid")
        path = episode.root / "agent" / "decisions" / f"selection-{selection:04d}.json"
        decision = decode_json(_read(path, 128_000))
        if (
            sha256(path) != reference.get("pre_action_sha256")
            or decision.get("selection") != selection
        ):
            raise ValueError("scientific_context_decision_binding_mismatch")
        assessments.append(
            {
                "selection": selection,
                "pre_action_sha256": reference["pre_action_sha256"],
                "record": decision["record"],
                "status": "retained_concern_not_resolved_by_tool_success",
            }
        )
    for index, item in enumerate(history, 1):
        action, response = item.get("action", {}), item.get("observation", {})
        if response.get("status") in {"rejected", "failed"} or response.get(
            "outcome"
        ) in {"scientific_failure", "infrastructure_failure"}:
            failures.append(
                {
                    "history_entry": index,
                    "action": action,
                    "status": response.get("status"),
                    "reason": response.get("diagnostic")
                    or response.get("reason")
                    or response.get("error"),
                }
            )
        diagnostics = response.get("result", {}).get(
            "diagnostics", response.get("result_summary", {}).get("diagnostics", {})
        )
        if diagnostics.get("analysis_error") or diagnostics.get("status") in {
            "analysis_failed",
            "support_unavailable",
            "failed",
        }:
            failures.append(
                {
                    "history_entry": index,
                    "action": action,
                    "status": "scientific_diagnostic_failure",
                    "reason": diagnostics.get("analysis_error")
                    or diagnostics.get("status"),
                }
            )
        binding = response.get("scientific_execution")
        if binding:
            specifications.append(
                {
                    key: binding[key]
                    for key in (
                        "action",
                        "specification_id",
                        "result_id",
                        "broker_receipt_id",
                    )
                }
            )
        if action.get("kind") == "request_information":
            concerns.append(
                {
                    "history_entry": index,
                    "message": action.get("message"),
                    "status": "retained_clarification_request_see_explicit_response",
                }
            )
    thesis = _thesis_context_projection(thesis_lifecycle, byte_budget=16_384)
    return {
        "format": "devbench.scientific-context/1",
        "objective": episode.config["question"],
        **thesis,
        "snapshot_id": episode.snapshot_id,
        "requirement_sources": task.get("user_parameter_requirements", []),
        "biological_declarations": {
            "unit_hierarchy": task["unit_hierarchy"],
            "analysis_columns": task["analysis_columns"],
            "measurement": task["measurement"],
            "targets": task["questions"],
        },
        "unresolved_assumptions": {
            "supplied": {
                key: task[key]
                for key in ("unknowns", "unresolved_assumptions")
                if key in task
            },
            "context": {
                key: task["context"][key]
                for key in ("unknowns", "unresolved_assumptions")
                if key in task["context"]
            },
            "limits": [
                "supplied_biological_identity_not_independently_verified",
                "model_assumptions_and_scientific_qualification_not_established_by_execution",
                "running_checkout_hash_is_not_immutable_execution_isolation",
            ],
        },
        "failed_or_rejected_attempts": failures,
        "selected_specifications": specifications,
        "unresolved_assessments": assessments,
        "clarifications": {"requests": concerns, "responses": episode.clarifications},
        "decision_records": getattr(episode, "decision_records", []),
        "comparison_policy": (
            "single_fixed_native_procedure_no_independent_alternative_comparison"
        ),
        "cumulative_limits": {
            "actions_used": len(episode.actions),
            "max_actions": episode.limits.max_steps,
            "revisions_used": episode.revisions,
            "max_revisions": episode.limits.max_revisions,
            "selections_used": len(episode.agent_selections),
            "context_bytes_used": context_bytes_used,
            "selector_policy": episode.agent_policy,
        },
        "scope": (
            "retained_observations_declarations_and_limits_not_scientific_validation"
        ),
    }


def _context_inventory(rows, *, byte_budget):
    """Bound recent descriptors while committing to the full ordered inventory."""
    from .checkpoints import digest

    shown = []
    for row in reversed(rows):
        size = len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode()) + 1
        if size > byte_budget:
            break
        shown.append(row)
        byte_budget -= size
    return {
        "total": len(rows),
        "omitted": len(rows) - len(shown),
        "sha256": digest(rows),
        "records": list(reversed(shown)),
    }


def _thesis_context_projection(lifecycle, *, byte_budget, connection=None):
    """Expose structural references while source narratives remain broker mediated."""
    if lifecycle is None:
        return {}
    from devbench.platform.thesis_aims import ThesisAimLifecycle
    from devbench.platform.thesis_workflow import ThesisWorkflow

    from .checkpoints import digest

    if type(lifecycle) not in (ThesisAimLifecycle, ThesisWorkflow):
        raise TypeError("Thesis context requires its trusted coordinator lifecycle")
    context = lifecycle.scientific_context(connection=connection)
    aims = []
    for row in context["research_aims"]:
        aims.append(
            {
                "aim_id": row["aim_id"],
                "version": row["version"],
                "kind": row["kind"],
                "depends_on": row["depends_on"],
                "invalidated_by": row["invalidated_by"],
                "status": "outcome_retained" if row["outcome"] else row["status"],
                "record_sha256": digest(row),
                "competing_explanation_ids": [
                    item["explanation_id"] for item in row["competing_explanations"]
                ],
            }
        )
    return {
        "thesis_state": {
            "raw_matrix_rows": 0,
            "clinical_release_allowed": False,
            "research_aims": _context_inventory(aims, byte_budget=byte_budget // 2),
            "answerability": _context_inventory(
                context.get("answerability", []),
                byte_budget=byte_budget // 2,
            ),
        }
    }


def _adaptive_context_events(db, lineage_id):
    from devbench.engine.scientific_authority import _row
    from devbench.engine.workflow_operations import OperationConflict

    rows = db.execute(
        """SELECT r.lineage_id, r.revision, r.record_id, r.kind,
        r.binding_id, r.publication_unit,
        CASE WHEN length(CAST(r.payload AS BLOB)) <= 65536 THEN r.payload END
            AS payload,
        r.sha256, o.sha256 AS outbox_digest FROM scientific_records r
        LEFT JOIN scientific_outbox o ON r.lineage_id=o.lineage_id
            AND r.revision=o.revision
        WHERE r.lineage_id=? AND (r.kind='disclosure_event'
            OR (r.kind='adaptive_event' AND json_extract(r.payload, '$.event')='hold'))
        ORDER BY r.revision""",
        (lineage_id,),
    )
    for row in rows:
        if row["payload"] is None or row["sha256"] != row["outbox_digest"]:
            raise OperationConflict("Scientific context event custody changed")
        yield _row(row)


def _require_controller_exposure(db, broker, recipient, policy, reservation):
    from .evidence_access import AccessDenied

    if reservation["context_id"] != recipient["context_id"]:
        raise AccessDenied("Exposure belongs to another recipient")
    broker._query_dependencies(db, (reservation["content_sha256"],))
    query = reservation.get("query_page", {})
    broker._query_dependencies(db, tuple(query.get("dependencies", ())))
    broker._check(recipient, policy, reservation["scope"], set())
    for _, source in broker._source_ancestry(
        db,
        (reservation["source_id"],),
        limit_error=AccessDenied("Exposure ancestry exceeds its bound"),
    ):
        broker._check(recipient, policy, source, set())


def _adaptive_attempt_failures(workflow, connection, state):
    from devbench.engine.workflow_operations import OperationConflict

    from .checkpoints import digest

    failures = []
    for identity, dispatch in state.dispatches.items():
        outcome = state.outcomes.get(identity)
        if outcome is not None and outcome["status"] == "succeeded":
            continue
        envelope = dispatch["envelope"]
        attempt = workflow.authority._load_state(
            connection,
            workflow.lineage_id,
            identity,
        )
        if attempt is None:
            raise OperationConflict("Adaptive dispatch lost its authoritative attempt")
        execution_state = attempt["execution_state"]
        failures.append(
            {
                "attempt_id": identity,
                "step_id": envelope["step"]["step_id"],
                "branch_id": envelope["step"]["branch_id"],
                "generation": envelope["generation"],
                "status": (outcome or {}).get("status", execution_state),
                "reason": (
                    (outcome or {}).get("reason")
                    or attempt.get("rejection_reason")
                    or execution_state
                ),
                "receipt_sha256": (outcome or {}).get("receipt_sha256"),
                "observation_sha256": attempt.get("observation_sha256"),
                "request_sha256": dispatch["token"]["request_sha256"],
            }
        )
    decisions = list(state.controller["decisions"])
    if state.controller["pending"] is not None:
        decisions.append(state.controller["pending"])
    for row in decisions:
        failures.extend(
            {"sequence": row["sequence"], "status": "rejected_selection", **rejection}
            for rejection in row.get("rejected_selections", ())
        )
        result = row.get("result", {})
        if (
            result.get("status") in {"failed", "rejected", "audit_only"}
            and result.get("attempt_id") not in state.dispatches
        ):
            failures.append(
                {
                    "sequence": row["sequence"],
                    "kind": row["decision"]["kind"],
                    "status": result["status"],
                    "reason": result.get("reason"),
                    "result_sha256": digest(result),
                }
            )
    return failures


def adaptive_authoritative_scientific_context(
    workflow,
    *,
    connection,
    state,
    revision,
    review_state,
    evidence,
    broker,
    context_id,
    byte_budget,
    thesis_lifecycle=None,
):
    """Project attempts, holds, exposure and artifact references at one WAL snapshot."""
    from .checkpoints import digest
    from .evidence_access import AccessDenied

    failures = _adaptive_attempt_failures(workflow, connection, state)
    qualification_holds, active_qualification = [], {}
    disclosures, disclosure_events = {}, []
    for row in _adaptive_context_events(connection, workflow.lineage_id):
        event = row["record"]
        material = event["state"]
        pointer = {"revision": row["revision"], "record_sha256": row["sha256"]}
        if event["kind"] == "disclosure_event":
            disclosure_events.append(pointer)
            disclosures[material["reservation_id"]] = (material, pointer)
            continue
        step, reason = material["step_id"], material["reason"]
        previous = active_qualification.pop(step, None)
        if previous is not None and previous != reason:
            qualification_holds.append(
                {
                    **pointer,
                    "step_id": step,
                    "reason": previous,
                    "status": "released" if reason is None else "superseded",
                    "evidence_sha256": material["evidence_sha256"],
                }
            )
        if reason is not None and reason.startswith("Q-"):
            active_qualification[step] = reason
            qualification_holds.append(
                {
                    **pointer,
                    "step_id": step,
                    "reason": reason,
                    "status": "open",
                    "evidence_sha256": material["evidence_sha256"],
                }
            )

    recipient, policy = broker._context_policy(connection, context_id)
    exposure = []
    disclosures_in_order = sorted(
        disclosures.values(),
        key=lambda row: row[0]["watermark"],
    )
    for material, pointer in disclosures_in_order:
        item = {
            **pointer,
            "status": material["state"],
            "possible": material["possible"],
            "confirmed": material["confirmed"],
        }
        try:
            _require_controller_exposure(
                connection,
                broker,
                recipient,
                policy,
                material,
            )
        except AccessDenied:
            item["content_status"] = "withheld_by_access_policy"
        else:
            item.update(
                {
                    key: material[key]
                    for key in (
                        "reservation_id",
                        "source_id",
                        "content_sha256",
                        "receipt_sha256",
                    )
                }
            )
        exposure.append(item)
    holds = [
        {
            "hold_id": row["hold_id"],
            "cycle_id": row["cycle_id"],
            "status": row["status"],
            "hold_sha256": digest(row),
        }
        for row in review_state["holds"].values()
    ]
    artifacts = [
        {
            **{key: value[key] for key in ("step_id", "port") if key in value},
            "version_id": value["artifact"]["version_id"],
            "sha256": value["artifact"]["sha256"],
        }
        for value in evidence.values()
        if "artifact" in value
    ]
    if thesis_lifecycle is not None and (
        thesis_lifecycle.ledger.path != workflow.ledger.path
        or thesis_lifecycle.lineage_id != workflow.lineage_id
    ):
        raise PermissionError("Thesis context requires the current adaptive lineage")
    section_budget = byte_budget // (6 if thesis_lifecycle is not None else 5)
    thesis = _thesis_context_projection(
        thesis_lifecycle,
        byte_budget=section_budget,
        connection=connection,
    )
    return {
        "format": "devbench.adaptive-scientific-context/1",
        "lineage_id": workflow.lineage_id,
        "snapshot_revision": revision,
        "individual_gene_rows_in_prompt": 0,
        **thesis,
        "failed_or_rejected_attempts": _context_inventory(
            failures,
            byte_budget=section_budget,
        ),
        "qualification_holds": _context_inventory(
            qualification_holds,
            byte_budget=section_budget,
        ),
        "review_holds": _context_inventory(holds, byte_budget=section_budget),
        "exposure_history": {
            **_context_inventory(exposure, byte_budget=section_budget),
            "event_count": len(disclosure_events),
            "events_sha256": digest(disclosure_events),
        },
        "artifact_refs": _context_inventory(artifacts, byte_budget=section_budget),
    }


def _analyst_projection(episode, action):
    from devbench.study.evidence import _read, decode_json

    from .episode_evidence_queries import summarize_result
    from .episodes import episode_evidence_limits

    # Independently check the retained numerical result before summarizing it.
    verified = _checkpoint_projection(episode, action)
    path = episode.root / "checkpoints" / f"action-{action:04d}" / "observation.json"
    result = decode_json(
        _read(path, episode_evidence_limits(episode.config)["max_checkpoint_bytes"])
    )["result"]
    return {
        "status": "succeeded",
        "source_action": action,
        "result_summary": {
            "format": "devbench.analyst-result-summary/1",
            "action": action,
            "source": verified["result_summary"]["source"],
            "summary": summarize_result(result),
        },
    }


def _decision_record(episode, choice, context, number):
    from .checkpoints import digest, sha256

    record = choice.decision_record
    fields = {
        "question",
        "concern",
        "evidence_refs",
        "competing_explanations",
        "expected_discriminating_observation",
    }
    if not isinstance(record, dict) or set(record) != fields:
        raise InvalidSelection("Analyst requires a bounded pre-action decision record")
    for field in ("question", "concern", "expected_discriminating_observation"):
        if (
            not isinstance(record[field], str)
            or not record[field].strip()
            or len(record[field]) > 2000
        ):
            raise InvalidSelection("Invalid decision text")
    if (
        not isinstance(record["competing_explanations"], list)
        or not 1 <= len(record["competing_explanations"]) <= 5
        or any(
            not isinstance(x, str) or not x.strip() or len(x) > 1000
            for x in record["competing_explanations"]
        )
    ):
        raise InvalidSelection("Invalid competing explanations")
    visible = set()
    for index, item in enumerate(context["history"], 1):
        if "action" in item:
            observed = item.get("observation", {})
            action_number = (
                observed.get("source_action")
                if item["action"].get("kind") == "tool"
                else None
            )
            if action_number is None:
                action_number = observed.get("query_receipt", {}).get("action", index)
            visible.add(
                (
                    "query:"
                    if item["action"].get("kind") == "query_evidence"
                    else "action:"
                )
                + str(action_number)
            )
    refs = record["evidence_refs"]
    if (
        not isinstance(refs, list)
        or len(refs) > 32
        or any(not isinstance(x, str) or x not in visible for x in refs)
    ):
        raise InvalidSelection("Decision references unavailable evidence")
    _public_only(record)
    bound = {
        "format": "devbench.analyst-decision/1",
        "selection": number,
        "instance_id": episode.instance_id,
        "snapshot_id": episode.snapshot_id,
        "context_sha256": digest(context),
        "action": choice.action,
        "admissible_action_kinds": context["action_kinds"],
        "origin": "agent_selected",
        "record": record,
        "scope": "prospective_local_rationale_not_independent_preregistration",
    }
    folder = episode.root / "agent" / "decisions"
    folder.mkdir(exist_ok=True)
    path = folder / f"selection-{number:04d}.json"
    atomic_json(path, bound)
    episode._record(
        "episode.analyst_decision",
        {
            "selection": number,
            "artifact": episode.audit.add_artifact(path),
            "context_sha256": digest(context),
        },
    )
    episode.pending_decision = {"selection": number, "pre_action_sha256": sha256(path)}


def record_decision_outcome(episode, response):
    from .checkpoints import digest, sha256

    pending = getattr(episode, "pending_decision", None)
    if pending is None:
        return
    path = (
        episode.root
        / "agent"
        / "decisions"
        / f"outcome-{pending['selection']:04d}.json"
    )
    value = {
        "format": "devbench.analyst-decision-outcome/1",
        **pending,
        "action": len(episode.actions),
        "status": response.get("status"),
        "outcome": response.get("outcome"),
        "state": response.get("state"),
        "reason": response.get("reason") or response.get("diagnostic"),
        "observation_sha256": digest(response),
        "query_receipt": response.get("query_receipt"),
    }
    atomic_json(path, value)
    episode._record(
        "episode.analyst_decision_outcome",
        {**pending, "artifact": episode.audit.add_artifact(path)},
    )
    episode.decision_records = [
        *getattr(episode, "decision_records", []),
        {**pending, "outcome_sha256": sha256(path)},
    ]
    episode.pending_decision = None


def project_observation(episode, response):
    """Keep small responses intact; retain and bind full large results separately."""
    if analyst_policy(episode) and "result" in response:
        projected = _analyst_projection(episode, len(episode.actions))
    elif len(json.dumps(response, allow_nan=False).encode()) <= 128_000:
        return _json_copy(response)
    else:
        projected = _checkpoint_projection(episode, len(episode.actions))
    from .checkpoints import sha256

    folder = episode.root / "agent" / "observations"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"action-{len(episode.actions):04d}.json"
    atomic_json(path, projected)
    episode._record(
        "episode.context_projection",
        {
            "action": len(episode.actions),
            "projection_sha256": sha256(path),
            "source": projected["result_summary"]["source"],
        },
    )
    return projected


def verify_projected_history(episode, history):
    """Recompute projections from full receipts again before model dispatch."""
    from devbench.study.evidence import _read, decode_json

    from .checkpoints import sha256

    for item in history:
        observed = item.get("observation", {})
        summary = observed.get("result_summary", {})
        if "result_summary" in observed:
            if summary.get("format") not in {
                "devbench.episode-result-projection/1",
                "devbench.analyst-result-summary/1",
            }:
                raise ValueError("projection_format_invalid")
            action = summary.get("action")
            if type(action) is not int or not 1 <= action <= len(episode.actions):
                raise ValueError("projection_action_invalid")
            rebuilt = (
                _analyst_projection(episode, action)
                if summary["format"] == "devbench.analyst-result-summary/1"
                else _checkpoint_projection(episode, action)
            )
            if observed != rebuilt:
                raise ValueError("projection_history_mismatch")
            path = episode.root / "agent" / "observations" / f"action-{action:04d}.json"
            if decode_json(_read(path, 128_000)) != observed:
                raise ValueError("projection_artifact_mismatch")
            bindings = []
            for node_id in episode.audit._node_ids:
                node = decode_json(
                    _read(
                        episode.root / "audit" / "nodes" / (node_id + ".json"),
                        4_000_000,
                    )
                )
                if (
                    node.get("kind") == "episode.context_projection"
                    and node["payload"].get("action") == action
                ):
                    bindings.append(node["payload"])
            if (
                len(bindings) != 1
                or bindings[0].get("projection_sha256") != sha256(path)
                or bindings[0].get("source") != summary["source"]
            ):
                raise ValueError("projection_recorded_binding_mismatch")


def drive_episode(
    episode,
    selector,
    *,
    limits=None,
    allow_data_execution=False,
    allow_model_execution=False,
    analyst_profile=None,
    evidence_delivery=None,
):
    """Scope a live selector lease to one invocation, including cleanup on handoff."""
    if allow_data_execution is not True:
        raise PermissionError(
            "Episode action driving requires data-execution authorization"
        )
    if evidence_delivery is not None:
        from .evidence_access import EvidenceDelivery

        if type(evidence_delivery) is not EvidenceDelivery:
            raise ValueError("Evidence delivery must be coordinator-owned")
    from contextlib import nullcontext

    from devbench.engine.episode_codex import CodexActionSelector

    scope = (
        selector.episode_lease()
        if isinstance(selector, CodexActionSelector)
        else nullcontext()
    )
    with scope:
        return _drive_episode(
            episode,
            selector,
            limits=limits,
            allow_data_execution=allow_data_execution,
            allow_model_execution=allow_model_execution,
            analyst_profile=analyst_profile,
            evidence_delivery=evidence_delivery,
        )


def _prepare_episode_driver(
    episode,
    selector,
    limits,
    analyst_profile,
    allow_data_execution,
):
    if allow_data_execution is not True:
        raise PermissionError(
            "Episode action driving requires data-execution authorization"
        )
    resumed = episode.resume_driver if episode is not None else None
    if analyst_profile not in {None, "analyst_v1"}:
        raise ValueError("Unknown analyst profile")
    saved_policy = episode.agent_policy or {}
    if resumed is not None:
        saved_profile = saved_policy.get("analyst_profile")
        if analyst_profile is not None and analyst_profile != saved_profile:
            raise ValueError("The resumed analyst profile is frozen")
        analyst_profile = saved_profile
    if not limits:
        if resumed is not None:
            limits = AgentLimits(
                **{
                    key: value
                    for key, value in saved_policy.items()
                    if key != "analyst_profile"
                }
            )
        elif analyst_profile:
            limits = AgentLimits(max_selections=14, max_context_bytes=256_000)
        else:
            limits = AgentLimits()
    policy = {
        **asdict(limits),
        **({"analyst_profile": analyst_profile} if analyst_profile else {}),
    }
    if analyst_profile and (episode.limits.max_steps < 14 or limits.max_selections < 2):
        raise ValueError(
            "Analyst profile requires at least 14 episode steps "
            "and a reserved finish selection"
        )
    if not isinstance(limits, AgentLimits):
        raise TypeError("Use AgentLimits for selector resource settings")
    configuration = selector_configuration(selector)
    root = episode.root / "agent"
    history = _open_episode_driver(
        episode,
        root,
        resumed,
        policy,
        configuration,
        limits,
    )
    return limits, analyst_profile, root, configuration, history


def _open_episode_driver(episode, root, resumed, policy, configuration, limits):
    if resumed is None:
        if episode.state != "ready" or episode.actions or root.exists():
            raise ValueError(
                "The action driver requires a fresh episode; "
                "budgets cannot be restarted"
            )
        episode.register_agent(policy)
        root.mkdir(mode=0o700)
        atomic_json(root / "policy.json", policy)
        atomic_json(root / "selector.json", configuration)
        episode.controller_deadline = time.monotonic() + limits.timeout_seconds
        history = []
    else:
        if policy != episode.agent_policy or configuration != resumed["configuration"]:
            raise ValueError(
                "The resumed selector configuration and budgets are frozen"
            )
        if episode.state != "ready" or episode.outcome is not None:
            raise ValueError("The restored episode is not ready for continuation")
        history = _json_copy(resumed["history"])
        episode.resume_driver = None
    return history


def _select_episode_action(
    episode,
    selector,
    root,
    configuration,
    limits,
    analyst_profile,
    history,
    number,
    deadline,
    context_bytes_used,
    allow_model_execution,
    evidence_delivery,
):
    context = {
        "observation": episode.observation(),
        "history": _json_copy(history),
        "action_kinds": ["tool", "finish", "refuse", "revise", "request_information"],
        "selection_budget": {
            "decisions_remaining": limits.max_selections - number + 1,
            "seconds_remaining": max(0, deadline - time.monotonic()),
            "per_selection_seconds": limits.session_timeout_seconds,
            "memory_mb": limits.memory_mb,
        },
    }
    if analyst_profile:
        context["analyst_profile"] = analyst_profile
        context["action_kinds"].append("query_evidence")
        context["final_selection_reserved"] = number == limits.max_selections
        context["evidence_scope"] = (
            "retained_diagnostics_and_queries_not_independent_scientific_validation"
        )
    try:
        context["scientific_state"] = authoritative_scientific_context(
            episode,
            history,
            context_bytes_used=context_bytes_used,
        )
        verify_projected_history(episode, history)
        _public_only(context)
        encoded = json.dumps(
            context,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        from .episode_codex import selection_instruction

        prompt_bytes = len(selection_instruction(context).encode("utf-8"))
        if context_bytes_used + prompt_bytes > limits.max_total_context_bytes:
            return (
                None,
                episode.abort(
                    "selector_cumulative_context_budget_exhausted_no_dispatch",
                ),
                context_bytes_used,
            )
        if prompt_bytes > limits.max_context_bytes:
            return (
                None,
                episode.abort(
                    "selector_context_budget_exhausted_no_dispatch",
                ),
                context_bytes_used,
            )
    except (OSError, TypeError, ValueError):
        return (
            None,
            episode.abort(
                "selector_context_not_public_or_finite",
            ),
            context_bytes_used,
        )
    choice, result = _capture_episode_selection(
        episode,
        selector,
        root,
        configuration,
        limits,
        analyst_profile,
        number,
        deadline,
        context,
        encoded,
        prompt_bytes,
        context_bytes_used,
        allow_model_execution,
        evidence_delivery,
    )
    return choice, result, context_bytes_used + prompt_bytes


def _capture_episode_selection(
    episode,
    selector,
    root,
    configuration,
    limits,
    analyst_profile,
    number,
    deadline,
    context,
    encoded,
    prompt_bytes,
    context_bytes_used,
    allow_model_execution,
    evidence_delivery,
):
    output = root / f"selection-{number:04d}"
    selector_entered = False
    try:
        output.mkdir(mode=0o700)
        atomic_json(output / "context.json", context)
        atomic_json(
            output / "context-budget.json",
            {
                "full_prompt_bytes": prompt_bytes,
                "serialized_context_bytes": len(encoded.encode("utf-8")),
                "cumulative_bytes": context_bytes_used + prompt_bytes,
                "profile": analyst_profile,
            },
        )
        # Reservation persists before any selector or permission-preflight call.
        attempt = {
            "episode_config": episode.config_id,
            "selection": number,
            "context": context,
            "attempt_path": str(output),
        }
        atomic_json(
            output / "reservation.json",
            {
                "selection": number,
                "max_provider_sessions": 1,
                "model_execution_authorized": allow_model_execution is True,
                "scope": "one_scientific_decision_session_local_permission_probe",
                **reservation_binding(configuration, attempt),
            },
        )
        model_authority = read_model_authority(
            output / "reservation.json",
            configuration=configuration,
            attempt=attempt,
            maximum_authority=allow_model_execution,
        )
        selection_timeout = min(
            limits.session_timeout_seconds,
            deadline - time.monotonic(),
        )
        if selection_timeout <= 0:
            raise TimeoutError("selector_deadline_exhausted_before_dispatch")

        def select(disclosed):
            nonlocal selector_entered
            selector_entered = True
            return selector.select(
                disclosed,
                output,
                timeout_seconds=selection_timeout,
                memory_mb=limits.memory_mb,
                allow_model_execution=model_authority,
            )

        choice = (
            evidence_delivery.transmit(_json_copy(context), select)
            if evidence_delivery is not None
            else select(_json_copy(context))
        )
        _record_episode_selection(
            episode,
            choice,
            output,
            analyst_profile,
            evidence_delivery,
        )
        if analyst_profile:
            _decision_record(episode, choice, context, number)
            final_actions = {"finish", "refuse", "request_information"}
            if (
                number == limits.max_selections
                and choice.action.get("kind") not in final_actions
            ):
                return None, episode.abort(
                    "selector_finalization_reserve_reached_no_dispatch",
                )
        if time.monotonic() >= deadline:
            return None, episode.abort("selector_deadline_exhausted")
    except BaseException as error:
        result = _handle_episode_selection_error(
            episode,
            root,
            output,
            number,
            selector_entered,
            error,
        )
        if not isinstance(error, Exception):
            raise
        return None, result
    return choice, None


def _record_episode_selection(
    episode,
    choice,
    output,
    analyst_profile,
    evidence_delivery,
):
    if not isinstance(choice, Selection):
        raise InvalidSelection("Selector must return a Selection")
    atomic_json(
        output / "selection.json",
        {
            "action": choice.action,
            "evidence": choice.evidence,
            **({"decision_record": choice.decision_record} if analyst_profile else {}),
        },
    )
    episode.record_agent_selection(choice.evidence, output / "selection.json")
    if not isinstance(choice.action, dict):
        raise InvalidSelection("Selected action is not a JSON object")
    _public_only(choice.action)
    if evidence_delivery is not None and choice.action.get("kind") == "query_evidence":
        evidence_delivery.check_query(choice.action.get("query", {}))


def _handle_episode_selection_error(
    episode,
    root,
    output,
    number,
    selector_entered,
    error,
):
    failure_path = (
        output / "failure.json"
        if output.is_dir() and not output.is_symlink()
        else root / f"selection-{number:04d}-failure.json"
    )
    atomic_json(
        failure_path,
        {
            "error_type": type(error).__name__,
            "message": str(error)[:2000],
            "selector_entered": selector_entered,
        },
    )
    if len(episode.agent_selections) < number:
        evidence_path = output / "evidence.json"
        evidence = {
            "backend": "unknown_selector",
            "model_sessions_attempted": None if selector_entered else 0,
            "actual_model": None,
            "tokens": None,
            "cost_usd": None,
        }
        if evidence_path.exists():
            try:
                from devbench_engine.adapters import _read_answer

                evidence = _read_answer(output, "evidence.json", max_bytes=4_000_000)
            except (OSError, TypeError, ValueError):
                evidence["evidence_capture_error"] = (
                    "invalid_or_unreadable_session_record"
                )
        try:
            episode.record_agent_selection(evidence, failure_path)
        except (TypeError, ValueError):
            episode.record_agent_selection(
                {
                    "backend": "unknown_selector",
                    "model_sessions_attempted": None,
                    "actual_model": None,
                    "tokens": None,
                    "cost_usd": None,
                    "evidence_capture_error": "invalid_session_accounting",
                },
                failure_path,
            )
    invalid = isinstance(error, InvalidSelection)
    return episode.abort(
        "invalid_selector_response"
        if invalid
        else "selector_failed:" + type(error).__name__,
        scientific=invalid,
    )


def _capture_episode_handoff(episode, root, response, history, configuration, number):
    try:
        atomic_json(root / "awaiting-information.json", response)
        from .episode_handoff import save_handoff

        receipt = save_handoff(
            episode,
            driver={"history": history, "configuration": configuration},
        )
        return {**response, "handoff": receipt}
    except BaseException as error:
        if episode.handoff is None:
            atomic_json(
                root / f"handoff-failure-{number:04d}.json",
                {
                    "error_type": type(error).__name__,
                    "message": str(error)[:2000],
                },
            )
        reason = f"handoff_capture_failed:{type(error).__name__}: {str(error)}"
        result = episode.abort(reason[:2000])
        if not isinstance(error, Exception):
            raise
        return result


def _drive_episode(
    episode,
    selector,
    *,
    limits=None,
    allow_data_execution=False,
    allow_model_execution=False,
    analyst_profile=None,
    evidence_delivery=None,
):
    """Run one fresh episode; return immediately on a request for information.

    ``selector`` is trusted coordinator code, never supplied by a participant.
    A live selector receives authority read from its persisted reservation.
    A returned information request requires the operator, not an automatic retry.
    This entry point deliberately cannot reopen a used selection budget.
    """
    limits, analyst_profile, root, configuration, history = _prepare_episode_driver(
        episode,
        selector,
        limits,
        analyst_profile,
        allow_data_execution,
    )
    context_bytes_used = sum(
        json.loads(path.read_text())["full_prompt_bytes"]
        for path in root.glob("selection-*/context-budget.json")
    )
    deadline = episode.controller_deadline
    for number in range(len(episode.agent_selections) + 1, limits.max_selections + 1):
        if len(episode.actions) >= episode.limits.max_steps:
            return episode.abort("action_budget_exhausted", scientific=True)
        if time.monotonic() >= deadline:
            return episode.abort("selector_deadline_exhausted")
        try:
            episode._check_inputs()
        except (OSError, ValueError) as error:
            return episode.abort("episode_inputs_changed:" + type(error).__name__)
        choice, result, context_bytes_used = _select_episode_action(
            episode,
            selector,
            root,
            configuration,
            limits,
            analyst_profile,
            history,
            number,
            deadline,
            context_bytes_used,
            allow_model_execution,
            evidence_delivery,
        )
        if choice is None:
            return result
        if (
            evidence_delivery is not None
            and choice.action.get("kind") == "query_evidence"
        ):
            response = episode.step(
                choice.action,
                deadline=deadline,
                evidence_delivery=evidence_delivery,
            )
        else:
            response = episode.step(choice.action, deadline=deadline)
        if episode.outcome is not None:
            return response  # The episode is sealed: no further artifact writes.
        record_decision_outcome(episode, response)
        try:
            history.append(
                {
                    "action": _json_copy(choice.action),
                    "observation": project_observation(episode, response),
                }
            )
        except (OSError, TypeError, ValueError) as error:
            return episode.abort("context_projection_failed:" + type(error).__name__)
        if response["state"] == "awaiting_information":
            return _capture_episode_handoff(
                episode,
                root,
                response,
                history,
                configuration,
                number,
            )
    return episode.abort("selector_decision_budget_exhausted")
