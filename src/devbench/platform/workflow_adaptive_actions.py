"""Bounded context, review state and selection for the adaptive controller."""

import hashlib
import json
from copy import deepcopy
from functools import cache

from devbench.analysis.controller_research_dispatch import (
    BRIEF_CONTEXT_KEY,
    BRIEF_EVIDENCE_KEY,
    eligible_ready_research,
)
from devbench.analysis.evidence_access import AccessDenied, _fields
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict

from .workflow_state import digest


class _ControllerDisclosure:
    """Evidence and ancestry accumulated for a single controller context."""

    def __init__(self, db, controller, lineage_id, decision_sources):
        self.db = db
        self.broker = controller.broker
        self.evidence = {}
        self.sources = set(decision_sources)
        if controller.planning_source_id is not None:
            self.sources.add(controller.planning_source_id)
        self.dependencies = set()
        self.payload_identities = set()
        self.recipient, self.policy = self.broker._context_policy(
            db, controller.context_id
        )
        if (
            self.recipient["lineage_id"] != lineage_id
            or self.recipient["role"] != "controller"
        ):
            raise AccessDenied("Evidence recipient is outside the bound lineage")

    def require_sources(self, identities, fields):
        for _, source in self.broker._source_ancestry(
            self.db,
            identities,
            limit_error=AccessDenied(
                "Controller disclosure ancestry exceeds its bound"
            ),
        ):
            self.broker._check(self.recipient, self.policy, source, fields)

    def ancestry(self):
        payload_peers = {
            identity: sorted(
                row["source_id"] for row in self.broker._source_peers(self.db, identity)
            )
            for identity in sorted(self.payload_identities)
        }
        # One byte-equivalent representative binds all peers and their parents.
        self.sources.update(peers[0] for peers in payload_peers.values() if peers)
        source_bindings = {
            identity: digest(source)
            for identity, source in self.broker._source_ancestry(
                self.db,
                self.sources,
                limit_error=OperationBlocked(
                    "controller_disclosure_ancestry_exceeds_bound"
                ),
            )
        }
        return {
            "parents": sorted(self.sources),
            "sources": source_bindings,
            "dependencies": sorted(self.dependencies),
            "payload_peers": payload_peers,
        }


def _adaptive_output_parent_scopes(
    workflow, broker, context_id, role, parents, dependencies
):
    with workflow.ledger._database() as db:
        db.execute("BEGIN")
        context, policy = broker._context_policy(db, context_id)
        if context["lineage_id"] != workflow.lineage_id or context["role"] != role:
            raise AccessDenied("Evidence recipient is outside the bound lineage")
        broker._query_dependencies(db, tuple(dependencies))
        scopes = [broker._source(db, identity) for identity in sorted(parents)]
        for source in scopes:
            broker._check(context, policy, source, set())
    return scopes


def register_adaptive_output_source(
    workflow, step_id, port, decision_sources, *, broker, context_id, role
):
    """Register derived scope from real accepted inputs and existing peers."""
    from .adaptive_workflow import request_digest

    if broker.authority.ledger.path != workflow.ledger.path:
        raise AccessDenied("Evidence source belongs to another workflow ledger")
    provenance = workflow.disclosure_provenance(step_id, port)

    def peers(sha256):
        with workflow.ledger._database() as db:
            db.execute("BEGIN")
            return [
                row["source_id"]
                for row in broker._source_peers(db, sha256)
                if not row["source_id"].startswith(
                    ("adaptive-output-", "adaptive-input-")
                )
            ]

    def leaf(sha256):
        existing = peers(sha256)
        if existing:
            return existing
        identity = "adaptive-input-" + sha256
        broker.register_source(
            identity,
            content_sha256=sha256,
            roots=(sha256,),
            classification="unknown",
        )
        return [identity]

    @cache
    def visit(version):
        record = provenance["artifacts"][version]
        artifact = record["artifact"]
        if (
            version != provenance["version_id"]
            and not record["parents"]
            and not record["basis"]
            and artifact["learned_scope"] != "full_cohort"
        ):
            # Reuse the reviewed source instead of adding an unknown peer.
            return tuple(leaf(artifact["sha256"]))
        parents = set(peers(artifact["sha256"]))
        for parent in record["parents"]:
            parents.update(visit(parent))
        for identity in record["basis"]:
            parents.update(leaf(identity))
        if not record["parents"]:
            parents.update(leaf(artifact["sha256"]))
        parents.update(decision_sources)
        scopes = _adaptive_output_parent_scopes(
            workflow, broker, context_id, role, parents, provenance["dependencies"]
        )
        roots = tuple(
            sorted({root for source in scopes for root in source["roots"]})
        )
        identity = (
            "adaptive-output-"
            + request_digest([workflow.lineage_id, version, sorted(parents)])[:48]
        )
        broker.register_source(
            identity,
            content_sha256=artifact["sha256"],
            roots=roots,
            parents=tuple(sorted(parents)),
            classification="unknown",
            tags=("full_cohort_learned_state",)
            if artifact["learned_scope"] == "full_cohort"
            else (),
        )
        return (identity,)

    (published_source,) = visit(provenance["version_id"])
    return published_source, provenance["dependencies"]


class _AdaptiveControllerActions:
    def _controller_diagnostic_descriptors(
        self, workflow, evidence, visible_holds=(), *, connection=None
    ):
        if not self._diagnostic_actions:
            return []
        if self.review_lifecycle.lineage_id != workflow.lineage_id:
            raise PermissionError(
                "Diagnostic actions belong to another authoritative workflow"
            )
        registered = self.review_lifecycle.state(connection=connection)["evidence"]
        visible = {
            hold["hold_id"]
            for hold in visible_holds
            if hold.get("content_status") != "withheld_by_access_policy"
        }
        descriptors = []
        for _, record in sorted(self._diagnostic_actions.items()):
            descriptor = record["descriptor"]
            identity = descriptor["input_evidence_id"]
            if (
                identity in evidence
                and identity in registered
                and evidence[identity].get("sha256") == registered[identity]["sha256"]
                and (descriptor["hold_id"] is None or descriptor["hold_id"] in visible)
            ):
                descriptors.append(deepcopy(descriptor))
        return descriptors

    def _authoritative_review_state(self, workflow, *, connection=None):
        """Optional runtime wiring cannot hide this lineage's durable holds."""
        from devbench.analysis.scientific_review_holds import ReviewHoldLifecycle

        candidates = (
            [self.review_lifecycle] if self.review_lifecycle is not None else []
        )
        if self.controller is not None:
            candidates.extend(
                active.lifecycle
                for active in self.controller._review_capabilities.values()
            )
        for lifecycle in candidates:
            if (
                lifecycle.ledger.path != workflow.ledger.path
                or lifecycle.lineage_id != workflow.lineage_id
            ):
                raise PermissionError(
                    "Review lifecycle belongs to another authoritative workflow"
                )
        if self.review_lifecycle is None and candidates:
            self.review_lifecycle = candidates[-1]
        reader = self.review_lifecycle or ReviewHoldLifecycle(
            workflow.authority, workflow.lineage_id
        )
        return reader.state(connection=connection)

    def _controller_source_ancestry(self, workflow, step_id, port, decision_sources):
        return register_adaptive_output_source(
            workflow, step_id, port, decision_sources,
            broker=self.controller.broker, context_id=self.controller.context_id,
            role="controller",
        )

    def adaptive_controller_context(self, workflow):
        """Prepare immutable source identities, then render one authority snapshot."""
        if (
            workflow.ledger.path != self.ledger.path
            or self.controller.broker.authority.ledger.path != self.ledger.path
        ):
            raise PermissionError(
                "Controller context requires the owning workflow ledger"
            )

        def prepare(db):
            current = workflow._state(db)
            state = current.controller
            revision = workflow.authority._revision(db, workflow.lineage_id)
            decision_sources = {
                item["evidence"].get("controller_disclosure_source_id")
                for item in state["decisions"]
            }
            if None in decision_sources or "" in decision_sources:
                raise OperationBlocked("current_controller_disclosure_binding_required")
            outputs = [
                {
                    "step_id": step_id,
                    "port": port,
                    "generation": current.generations[step_id],
                }
                for step_id in reversed(current.nodes)
                for port in sorted((current.outcome(step_id) or {}).get("outputs", {}))
            ]
            return (
                revision,
                tuple(sorted(decision_sources)),
                self._controller_page(
                    state,
                    "query_handles",
                    outputs,
                    page_size=8,
                ),
            )

        revision, decision_sources, output_page = workflow._read_projection(prepare)
        handle_page, output_rows = output_page
        # Registering derived sources is a broker write. The final read checks
        # the same scientific revision and reauthorizes their current scope.
        prepared = {}
        for output in output_rows:
            step_id, port = output["step_id"], output["port"]
            try:
                workflow.current_use(
                    step_id,
                    port,
                    purpose="query",
                    generation=output["generation"],
                )
                prepared[step_id, port] = self._controller_source_ancestry(
                    workflow,
                    step_id,
                    port,
                    decision_sources,
                )
            except AccessDenied:
                prepared[step_id, port] = None
            except OperationBlocked as error:
                if not (
                    error.state.startswith("thesis_joint_bh_")
                    or error.state in {
                        "adaptive_use_invalidated",
                        "adaptive_use_held_or_retired",
                        "adaptive_current_use_unavailable",
                        "adaptive_protected_partition_delivery_denied",
                    }
                ):
                    raise

        def read(db):
            if workflow.authority._revision(db, workflow.lineage_id) != revision:
                raise OperationConflict(
                    "Controller evidence changed while preparing context"
                )
            return self._render_adaptive_controller_context(
                workflow,
                db,
                decision_sources,
                handle_page,
                output_rows,
                prepared,
            )

        return workflow._read_projection(read)

    def _render_adaptive_controller_context(
        self,
        workflow,
        db,
        decision_sources,
        handle_page,
        output_rows,
        prepared,
    ):
        from devbench.analysis.episode_agent import (
            adaptive_authoritative_scientific_context,
        )

        controller = self.controller
        tier_a = self.tier_a_executor
        current = workflow._state(db)
        state = current.controller
        review_state = self._authoritative_review_state(workflow, connection=db)
        status = workflow._status(db, current)
        snapshot = workflow.projection_snapshot(connection=db)
        snapshot["steps"] = [
            {key: value for key, value in row.items() if key != "role"}
            for row in snapshot["steps"]
        ]
        history = snapshot["attempt_history"]
        if len(history) > 8:
            snapshot["attempt_history"] = history[-8:]
            snapshot["attempt_history_total"] = len(history)
            snapshot["attempt_history_sha256"] = digest(history)

        disclosure = _ControllerDisclosure(
            db, controller, workflow.lineage_id, decision_sources
        )
        query_handles = self._controller_output_context(
            workflow,
            output_rows,
            prepared,
            disclosure,
        )
        holds, entitled, withheld = self._controller_review_holds(
            workflow,
            review_state,
            disclosure,
        )
        feasible = self._controller_resolving_checks(workflow, db, state, holds)
        self._controller_review_evidence(review_state, entitled, withheld, disclosure)
        query_results = self._controller_retained_queries(state, disclosure)
        pipeline_inputs = (
            self._controller_pipeline_inputs(workflow, status["revision"], disclosure)
            if tier_a is not None
            else []
        )
        ancestry = disclosure.ancestry()
        catalog = self._controller_action_catalog()
        from .thesis_benchmark_actions import benchmark_descriptors
        from .thesis_joint_bh_release import joint_publication_choices

        benchmark_choices = benchmark_descriptors(
            getattr(controller, "benchmark_plans", {})
        )
        joint_choices = (
            joint_publication_choices(workflow, db, current)
            if tier_a is not None and self.allow_data_execution
            else []
        )
        context_pages, sections = self._controller_context_pages(
            workflow,
            state,
            disclosure.evidence,
            holds,
            handle_page,
            query_handles,
            query_results,
            connection=db,
        )
        return {
            "decision_kind": "adaptive_controller",
            "revision": status["revision"],
            **deepcopy(self.planner.get(workflow.lineage_id)["context"]),
            "graph": snapshot,
            "ready": list(status["ready"]),
            "blocked": status["blocked"],
            **(
                {BRIEF_CONTEXT_KEY: True}
                if eligible_ready_research(current, status["ready"]) else {}
            ),
            "scientific_state": adaptive_authoritative_scientific_context(
                workflow,
                connection=db,
                state=current,
                revision=status["revision"],
                review_state=review_state,
                evidence=disclosure.evidence,
                broker=controller.broker,
                context_id=controller.context_id,
                thesis_lifecycle=getattr(controller, "thesis_lifecycle", None),
                byte_budget=min(8192, controller.invoker.limits.max_context_bytes // 8),
            ),
            "evidence": disclosure.evidence,
            "open_holds": holds,
            "available_resolving_checks": feasible,
            "disclosure_ancestry": ancestry,
            "action_catalog": catalog,
            **({"benchmark_choices": benchmark_choices} if benchmark_choices else {}),
            **({"joint_publication_choices": joint_choices} if joint_choices else {}),
            "catalog_origin": (
                "coordinator_admitted_software_not_user_parameter_requirements"
            ),
            "literature_handles": [
                handle.descriptor().model_dump(mode="json")
                for handle in controller.literature_handles
            ],
            **({"pipeline_inputs": pipeline_inputs} if tier_a is not None else {}),
            **sections,
            "context_pages": context_pages,
            "decisions_remaining": controller.max_decisions - len(state["decisions"]),
            "termination_required": len(state["decisions"])
            >= controller.max_decisions - 1,
            "qualification_status": "not_established",
        }

    def _controller_output_context(self, workflow, rows, prepared, disclosure):
        from .adaptive_workflow import request_digest

        query_handles = []
        preview_budget = min(
            8192, self.controller.invoker.limits.max_context_bytes // 8
        )
        for output in rows:
            step_id, port = output["step_id"], output["port"]
            descriptor = {**output, "content_status": "unavailable"}
            query_handles.append(descriptor)
            if (step_id, port) not in prepared:
                continue
            try:
                value = workflow._current_use(
                    disclosure.db,
                    step_id,
                    port,
                    "compute",
                    output["generation"],
                )
                source = prepared[step_id, port]
                if source is None:
                    raise AccessDenied("Adaptive output ancestry was withheld")
                source_id, dependencies = source
                disclosure.broker._query_dependencies(
                    disclosure.db, tuple(dependencies)
                )
                disclosure.require_sources((source_id,), set())
            except OperationBlocked:
                continue
            except AccessDenied:
                self._withhold_controller_output(descriptor, disclosure)
                continue
            handle_id = (
                "output-" + request_digest([step_id, port, value.version_id])[:32]
            )
            item = {
                "step_id": step_id,
                "port": port,
                "artifact": value.model_dump(mode="json"),
                "content_status": "not_available_to_controller",
            }
            try:
                preview_budget -= self._add_controller_output_preview(
                    value,
                    source_id,
                    handle_id,
                    item,
                    preview_budget,
                    disclosure,
                )
            except AccessDenied:
                self._withhold_controller_output(descriptor, disclosure)
                continue
            if "query_handle_id" in item:
                descriptor.update(
                    handle_id=handle_id,
                    source_id=source_id,
                    source_sha256=value.sha256,
                    content_status="available",
                    modes=["family_summary", "filtered"],
                )
            else:
                descriptor["content_status"] = item["content_status"]
            disclosure.sources.add(source_id)
            disclosure.dependencies.update(dependencies)
            disclosure.evidence[handle_id] = item
        return query_handles

    @staticmethod
    def _withhold_controller_output(descriptor, disclosure):
        from .adaptive_workflow import request_digest

        # The plan can disclose a step's structure without granting its output.
        step_id, port = descriptor["step_id"], descriptor["port"]
        withheld = "withheld-" + request_digest([step_id, port])[:32]
        disclosure.evidence[withheld] = {
            "step_id": step_id,
            "port": port,
            "content_status": "withheld_by_access_policy",
        }
        descriptor["content_status"] = "withheld_by_access_policy"

    def _add_controller_output_preview(
        self,
        value,
        source_id,
        handle_id,
        item,
        budget,
        disclosure,
    ):
        from devbench.engine.tier_a_scrna import is_single_cell_output

        if is_single_cell_output(value):
            return self._preview_single_cell_output(
                value, source_id, item, budget, disclosure,
            )
        if value.representation == "exploratory_parquet":
            from devbench.engine.exploratory_oci import ExploratoryOCI

            if type(self.executor) is not ExploratoryOCI:
                return 0
            reference = self.executor.binary_descriptor(value)
            disclosure.require_sources((source_id,), _fields(reference))
            disclosure.dependencies.add(value.sha256)
            size = len(json.dumps(reference, ensure_ascii=False).encode())
            if budget >= size:
                item.update(value=reference, content_status="available")
                return size
            return 0
        if value.representation == "parquet":
            if self.tier_a_executor is None:
                return 0
            from devbench.analysis.episode_evidence_queries import (
                parquet_evidence_summary,
            )

            summary = parquet_evidence_summary(
                value, open_artifact=self.tier_a_executor.open_output
            )
            disclosure.require_sources((source_id,), _fields(summary))
            item.update(
                query_handle_id=handle_id,
                content_status="available_through_bounded_query",
            )
            size = len(json.dumps(summary, ensure_ascii=False).encode())
            if budget >= size:
                item.update(value=summary, content_status="available")
                return size
        elif hasattr(self.executor, "training"):
            return self._add_controller_json_preview(
                value, handle_id, item, budget, disclosure
            )
        return 0

    def _preview_single_cell_output(self, value, source_id, item, budget, disclosure):
        if self.tier_a_executor is None:
            return 0
        item["content_status"] = "receipt_verified_artifact"
        compact_result = (
            value.representation.endswith("_report_json")
            or value.representation == "scrna_composition_result_json"
        )
        if not compact_result or budget < 1:
            return 0
        with self.tier_a_executor.open_output(value) as stream:
            raw = stream.read(budget + 1)
        if len(raw) > budget:
            return 0
        decoded = json.loads(raw)
        disclosure.require_sources((source_id,), _fields(decoded))
        disclosure.dependencies.add(value.sha256)
        item.update(value=decoded, content_status="available")
        return len(raw)

    def _add_controller_json_preview(self, value, handle_id, item, budget, disclosure):
        with self.executor.training.open(value.sha256) as stream:
            content = stream.read(4097)
        if len(content) > 4096:
            item.update(
                content_status="available_through_bounded_query",
                query_handle_id=handle_id,
            )
            return 0
        if hashlib.sha256(content).hexdigest() != value.sha256:
            raise ValueError(
                "Controller output bytes differ from their accepted artifact"
            )
        try:
            parsed = json.loads(content)
        except (ValueError, UnicodeDecodeError):
            item["content_status"] = "non_json_output"
            return 0

        # Also authorize any canonical JSON peers without discarding file custody.
        canonical = digest(parsed)
        broker, db = disclosure.broker, disclosure.db
        broker._query_dependencies(db, (canonical,))
        peers = {peer["source_id"] for peer in broker._source_peers(db, canonical)}
        disclosure.require_sources(peers, _fields(parsed))
        if (
            isinstance(parsed, dict)
            and {"families", "targets", "target_results"} & parsed.keys()
        ):
            item.update(
                query_handle_id=handle_id,
                content_status="available_through_bounded_query",
            )
        elif self._controller_scalar_summary(parsed) and budget >= len(content):
            item.update(value=parsed, content_status="available")
            if canonical != value.sha256:
                disclosure.payload_identities.add(canonical)
                disclosure.dependencies.add(canonical)
            return len(content)
        return 0

    def _controller_review_holds(self, workflow, review_state, disclosure):
        from devbench.analysis.scientific_review_holds import ReviewHoldLifecycle

        holds, entitled, withheld = [], set(), set()
        reader = ReviewHoldLifecycle(workflow.authority, workflow.lineage_id)
        for hold in review_state["holds"].values():
            if hold["status"] != "open":
                continue
            try:
                origins = reader.hold_origins(
                    disclosure.broker,
                    hold["hold_id"],
                    connection=disclosure.db,
                )
                sources = set().union(*origins["groups"])
                disclosure.require_sources(sources, _fields(hold))
            except AccessDenied:
                cycle = review_state["cycles"][hold["cycle_id"]]
                withheld.update(reader._available_evidence(cycle))
                holds.append(
                    {
                        key: deepcopy(hold[key])
                        for key in ("hold_id", "cycle_id", "status", "scope")
                    }
                    | {"content_status": "withheld_by_access_policy"}
                )
                continue
            holds.append(deepcopy(hold))
            entitled.update(origins["evidence_ids"])
            disclosure.sources.update(sources)
            disclosure.dependencies.update(origins["dependencies"])
        return holds, entitled, withheld

    def _controller_resolving_checks(self, workflow, db, state, holds):
        feasible = []
        for hold in holds:
            request = hold.get("objection", {}).get("check_request")
            if request is None or any(
                check["status"] in {"scheduled", "completed"}
                for check in hold["checks"]
            ):
                continue
            for key, action in self.controller.action_catalog.items():
                if action.operation_id != request["registered_check_id"]:
                    continue
                if self.review_lifecycle is None:
                    continue
                workflow._controller_admission(db, state)
                try:
                    self.review_lifecycle.check_candidate(
                        hold["hold_id"],
                        workflow,
                        action,
                        actor=self.actor,
                        connection=db,
                    )
                except OperationConflict as error:
                    if str(error) not in {
                        "Candidate step differs from the current registered action",
                        "Resolving action identity cannot change "
                        "its step specification",
                    }:
                        raise
                    continue
                except (ValueError, PermissionError, OperationBlocked):
                    continue
                feasible.append({"hold_id": hold["hold_id"], "action_id": key})
        return feasible

    def _controller_review_evidence(self, review_state, entitled, withheld, disclosure):
        broker, db = disclosure.broker, disclosure.db
        for identity, item in review_state["evidence"].items():
            sources = {
                row["source_id"] for row in broker._source_peers(db, item["sha256"])
            }
            if identity not in entitled and (not sources or identity in withheld):
                continue
            try:
                broker._query_dependencies(db, (item["sha256"],))
                disclosure.require_sources(sources, set())
            except AccessDenied:
                continue
            disclosure.evidence.setdefault(
                identity,
                {
                    "sha256": item["sha256"],
                    "relevant_entity_ids": item["relevant_entity_ids"],
                },
            )
            disclosure.sources.update(sources)
            disclosure.dependencies.add(item["sha256"])

    def _controller_retained_queries(self, state, disclosure):
        query_results = []
        broker, db = disclosure.broker, disclosure.db
        for item in state["decisions"]:
            result, kind = item["result"], item["decision"]["kind"]
            if kind == "review" and result.get("cycle_id"):
                disclosure.sources.add("review-return-" + result["cycle_id"])
            if kind == "query_literature" and result.get("query_receipt"):
                reservation = broker._get(
                    db,
                    "reservation",
                    result["query_receipt"]["disclosure_reservation_id"],
                )
                disclosure.sources.add(reservation["source_id"])
            if kind == "query_evidence" and result.get("page"):
                query_results.append(
                    self._project_retained_controller_query(item, disclosure)
                )
        return query_results

    def _project_retained_controller_query(self, item, disclosure):
        broker, db = disclosure.broker, disclosure.db
        page = item["result"]["page"]
        receipt = page["query_receipt"]
        reservation = broker._get(
            db, "reservation", receipt["disclosure_reservation_id"]
        )
        if (
            not reservation.get("query_page")
            or reservation["state"] not in {"delivery_attempted", "receipt_confirmed"}
            or reservation["content_sha256"] != digest(page)
        ):
            raise OperationConflict(
                "Retained controller evidence query receipt changed"
            )
        dependencies = reservation["query_page"]["dependencies"]
        peers = {
            row["source_id"]
            for row in broker._source_peers(db, receipt["source_sha256"])
        }
        handle_id = item["decision"]["handle_id"]
        try:
            broker._query_dependencies(db, tuple(dependencies))
            disclosure.require_sources(
                {reservation["source_id"], *peers}, _fields(page)
            )
        except AccessDenied:
            return {
                "handle_id": handle_id,
                "content_status": "withheld_by_access_policy",
            }
        disclosure.sources.add(reservation["source_id"])
        disclosure.dependencies.update(dependencies)
        disclosure.payload_identities.add(receipt["source_sha256"])
        return {
            "handle_id": handle_id,
            "page": self._controller_query_projection(
                page, reservation["content_sha256"]
            ),
        }

    def _controller_pipeline_inputs(self, workflow, revision, disclosure):
        pipeline_inputs = []
        for candidate in self.controller.pipeline_input_candidates(
            workflow,
            expected_revision=revision,
            connection=disclosure.db,
        ):
            artifact = candidate["artifact"]
            identity = artifact["version_id"]
            if identity in disclosure.evidence:
                raise OperationConflict(
                    "Pipeline input identity conflicts with current evidence"
                )
            disclosure.evidence[identity] = {
                "artifact": artifact,
                "content_status": "admitted_pipeline_input",
            }
            pipeline_inputs.append(artifact)
            disclosure.sources.update(candidate["source_ids"])
            disclosure.dependencies.update(candidate["dependencies"])
        return pipeline_inputs

    def _controller_action_catalog(self):
        catalog = {
            key: value.model_dump(mode="json", exclude={"role"})
            for key, value in self.controller.action_catalog.items()
        }
        for item in catalog.values():
            encoded = getattr(self.executor, "parameters", {}).get(
                item["parameters_sha256"]
            )
            if encoded is not None:
                item["parameters"] = json.loads(encoded)
        return catalog

    def _controller_context_pages(
        self,
        workflow,
        state,
        evidence,
        holds,
        handle_page,
        query_handles,
        query_results,
        *,
        connection,
    ):
        context_pages = {"query_handles": handle_page}
        sections = {"query_handles": query_handles}
        decision_fields = {
            "kind",
            "action_id",
            "step_id",
            "hold_id",
            "branch_id",
            "touchpoint",
            "handle_id",
            "disposition",
        }
        result_fields = {
            "status",
            "reason",
            "kind",
            "step_id",
            "snapshot_id",
            "blocked_entity_ids",
            "resumption_condition",
        }
        recent = [
            {
                "sequence": item["sequence"],
                "decision_sha256": digest(item["decision"]),
                "decision": {
                    key: value
                    for key, value in item["decision"].items()
                    if key in decision_fields
                },
                "evidence_count": len(item["decision"]["evidence_ids"]),
                "result_sha256": digest(item["result"]),
                "result": {
                    key: value
                    for key, value in item["result"].items()
                    if key in result_fields
                },
            }
            for item in reversed(state["decisions"])
            if item["decision"]["kind"] != "page_context"
        ]
        literature = self._controller_literature_rows(state)
        diagnostics = self._controller_diagnostic_descriptors(
            workflow,
            evidence,
            holds,
            connection=connection,
        )
        for section, rows, limit in (
            ("recent_decisions", recent, 4),
            ("literature_evidence", literature, 2),
            ("query_results", list(reversed(query_results)), 2),
            ("diagnostic_actions", diagnostics, 8),
        ):
            context_pages[section], sections[section] = self._controller_page(
                state,
                section,
                rows,
                page_size=limit,
            )
        return context_pages, sections

    @staticmethod
    def _controller_scalar_summary(value):
        """A small legacy scalar aggregate can accompany an artifact reference."""
        import math

        return (
            isinstance(value, dict)
            and bool(value)
            and all(
                (name in {"mean", "mean_squared"} or name.endswith("_mean"))
                and (
                    type(metric) is int
                    or type(metric) is float
                    and math.isfinite(metric)
                )
                for name, metric in value.items()
            )
        )

    @staticmethod
    def _controller_query_projection(page, page_sha256):
        receipt = page["query_receipt"]
        return {
            **{
                key: page[key]
                for key in (
                    "status",
                    "mode",
                    "scope",
                    "family_rows",
                    "matched_rows",
                    "offset",
                    "family_summary",
                    "directional_summary",
                    "aggregate",
                    "aggregate_scope",
                )
                if key in page
            },
            "source_sha256": receipt["source_sha256"],
            "page_sha256": page_sha256,
            "individual_gene_rows_in_prompt": 0,
            "omitted_gene_rows": receipt["total_returned"],
            "query_receipt": {
                key: receipt[key]
                for key in (
                    "disclosure_reservation_id",
                    "query_sha256",
                    "next_cursor",
                    "total_matched",
                    "total_returned",
                    "complete",
                )
            },
        }

    @staticmethod
    def _controller_literature_rows(state):
        rows = []
        for item in reversed(state["decisions"]):
            result = item["result"]
            if item["decision"]["kind"] != "query_literature" or not result.get(
                "snapshot_id"
            ):
                continue
            if len(json.dumps(result, ensure_ascii=False).encode()) <= 8192:
                rows.append(result)
                continue
            base = {
                key: result[key]
                for key in (
                    "snapshot_id",
                    "status",
                    "affected_claim_ids",
                    "query_receipt",
                    "scope",
                )
                if key in result
            }
            base["result_sha256"] = digest(result)
            passages = (result.get("extraction") or {}).get("passages", [])
            if passages:
                rows.extend(
                    {
                        **base,
                        "passage_offset": offset,
                        "passage_total": len(passages),
                        "extraction": {"passages": [passage]},
                    }
                    for offset, passage in enumerate(passages)
                )
            else:
                rows.append(
                    {
                        **base,
                        "content_status": "retained_result_preview_exceeds_byte_bound",
                    }
                )
        return rows

    @staticmethod
    def _controller_page(state, section, rows, *, page_size):
        cumulative = digest({"section": section, "rows": rows})
        previous = next(
            (
                item["result"]
                for item in reversed(state["decisions"])
                if (
                    item["decision"]["kind"] == "page_context"
                    and item["result"].get("section") == section
                    and item["result"].get("status") == "paged"
                )
            ),
            None,
        )
        offset = (
            previous["offset"]
            if previous is not None and previous["digest"] == cumulative
            else 0
        )
        page = rows[offset : offset + page_size]
        next_offset = offset + len(page)
        return {
            "total": len(rows),
            "offset": offset,
            "digest": cumulative,
            "next_cursor": f"{next_offset}:{cumulative}"
            if next_offset < len(rows)
            else None,
            "previous_cursor": f"{max(0, offset - page_size)}:{cumulative}"
            if offset
            else None,
        }, page

    def _query_controller_evidence(self, workflow, handle, query, *, reservation_id):
        from devbench.analysis.episode_evidence_queries import (
            query_parquet_result,
            query_result,
        )
        from devbench.analysis.evidence_access import EvidenceDelivery
        from devbench.analysis.method_planner_contract import evidence_query_arguments
        from devbench.study.evidence import decode_json

        broker, context_id = self.controller.broker, self.controller.context_id
        source_id = self._controller_query_source(workflow, handle, query.get("cursor"))
        delivery = EvidenceDelivery(broker, context_id, source_id)

        artifact, require_use = workflow._authorized_current(
            handle["step_id"],
            handle["port"],
            delivery,
            "compute",
        )
        if artifact.sha256 != handle["source_sha256"]:
            raise OperationBlocked("controller_query_source_changed")
        canonical = None

        def guard(db):
            require_use(db)
            value = workflow._current_use(
                db, handle["step_id"], handle["port"], "compute", handle["generation"]
            )
            if value.sha256 != handle["source_sha256"]:
                raise OperationBlocked("controller_query_source_changed")
            context, policy = broker._context_policy(db, context_id)
            broker._check(
                context, policy, broker._source(db, handle["source_id"]), set()
            )
            if canonical is not None:
                for peer in broker._source_peers(db, canonical):
                    broker._check(
                        context, policy, broker._source(db, peer["source_id"]), set()
                    )

        query = evidence_query_arguments(query)
        if artifact.representation == "parquet":
            if self.tier_a_executor is None:
                raise OperationBlocked("tier_a_readback_executor_required")
            return query_parquet_result(
                artifact,
                query,
                open_artifact=self.tier_a_executor.open_output,
                evidence_delivery=delivery,
                _use_guard=guard,
                _reservation_id=reservation_id,
            )

        def load(value):
            with self.executor.training.open(value.sha256) as stream:
                return stream.read(16_000_001)

        document = decode_json(workflow._load_current_bytes(artifact, load))
        if (
            not isinstance(document, dict)
            or not {"families", "targets", "target_results"} & document.keys()
        ):
            raise OperationBlocked("controller_query_result_contract_unavailable")
        canonical = digest(document)
        return query_result(
            document,
            query,
            evidence_delivery=delivery,
            _use_guard=guard,
            _reservation_id=reservation_id,
        )

    def _controller_query_source(self, workflow, handle, cursor):
        source_id = handle["source_id"]
        if not cursor:
            return source_id
        previous = next(
            (
                item
                for item in workflow.controller_state()["decisions"]
                if item["decision"]["kind"] == "query_evidence"
                and item["decision"]["handle_id"] == handle["handle_id"]
                and item["result"]
                .get("page", {})
                .get("query_receipt", {})
                .get("next_cursor")
                == cursor
            ),
            None,
        )
        if previous is None:
            raise OperationBlocked("current_controller_evidence_cursor_required")
        reservation = self.controller.broker.reservation(cursor)
        if reservation["scope"]["content_sha256"] != handle["source_sha256"]:
            raise OperationBlocked("controller_query_source_changed")
        return reservation["source_id"]

    def select_adaptive(self, workflow, *, expected_revision=None):
        if (
            expected_revision is not None
            and workflow.status()["revision"] != expected_revision
        ):
            raise OperationConflict(
                "Adaptive workflow revision changed before selection"
            )
        state = workflow.controller_state()
        if state["outcome"] is not None:
            return None
        if len(state["decisions"]) >= self.controller.max_decisions:
            self.stop_adaptive(workflow, "controller_decision_budget_exhausted")
            return None
        context = (
            state["pending"]["context"]
            if state["pending"] is not None
            else self.adaptive_controller_context(workflow)
        )
        return self.controller.select(
            workflow, context, expected_revision=expected_revision
        )

    def stop_adaptive(self, workflow, reason, *, decision=None):
        return workflow.stop_controller(
            lambda db: self._adaptive_outcome(
                workflow, reason, decision=decision, connection=db
            )
        )

    def _adaptive_outcome(self, workflow, reason, *, decision=None, connection=None):
        state = workflow.controller_state(connection=connection)
        status = workflow.status(connection=connection)
        completed = sorted(
            key for key, value in status["outcomes"].items() if value == "succeeded"
        )
        unresolved = [
            f"{key}: {value}" for key, value in sorted(status["blocked"].items())
        ]
        unresolved.extend((decision or {}).get("unresolved", ()))
        holds = [
            {
                "hold_id": hold["hold_id"],
                "scope": hold["scope"],
                "requested_resolution": hold["objection"]["requested_resolution"],
                "resumption_condition": hold["objection"]["predicate"][
                    "resumption_condition"
                ],
            }
            for hold in self._authoritative_review_state(
                workflow, connection=connection
            )["holds"].values()
            if hold["status"] == "open"
        ]
        unresolved.extend(
            item["hold_id"] + ": " + item["requested_resolution"] for item in holds
        )
        snapshot = workflow.projection_snapshot(connection=connection)
        snapshot.pop("controller_outcome", None)
        conditions = {
            "cancelled": [
                "Start a new explicitly authorized campaign as a separate run, "
                "linked to the retained cancelled campaign, evidence and spend; "
                "this is not same-investigation resume."
            ],
            "controller_decision_budget_exhausted": [
                "The controller decision ceiling is exhausted; any separately "
                "authorized continuation must retain this history and campaign spend."
            ],
            "unresolved_usage": [
                "Reconcile the exact retained invocation capture and unknown usage "
                "before any further model call."
            ],
            "controller_selection_requires_reconciliation": [
                "Reconcile the retained controller invocation; "
                "do not redispatch an uncertain active call."
            ],
        }.get(reason)
        outcome = {
            "format": "devbench.two-agent-outcome/1",
            "status": (decision or {}).get(
                "disposition", "partial" if completed else "unresolved"
            ),
            "reason": reason,
            "rationale": (decision or {}).get(
                "rationale",
                "The current admitted investigation cannot continue "
                "within its retained authority.",
            ),
            "completed_steps": completed,
            "remaining_ready_steps": list(status["ready"]),
            "unresolved": list(dict.fromkeys(unresolved)),
            "open_holds": holds,
            "resumption_conditions": conditions
            or list(
                (decision or {}).get("resumption_conditions")
                or [
                    "Resolve "
                    + reason
                    + " under the same retained campaign and current scientific "
                    "binding; budgets do not reset."
                ]
            ),
            "decisions": len(state["decisions"]),
            "qualification_status": "not_established",
            "graph_sha256": digest(snapshot),
            "real_agent_usefulness": "requires_independent_T23_evaluation",
            "publication_granted": False,
        }
        if connection is not None:
            outcome = workflow._controller_stop_outcome(connection, state, outcome)
        return outcome

    def require_primary_review(self, entity_id):
        """Primary admission never follows from a planning pause or model approval."""
        if self.review_lifecycle is None or not callable(self.review_binding):
            raise OperationBlocked("current_design_review_required")
        return self.review_lifecycle.require_primary_fit(
            entity_id, self.review_binding(entity_id)
        )

    def adaptive_review_admission(self, connection, step):
        """Read review state under the scheduler's dispatch/acceptance transaction."""
        from devbench.core.procedure_profile import ADMITTED_RESEARCH

        if self.review_lifecycle is None or not callable(self.review_binding):
            raise OperationBlocked("completed_current_design_review_required")
        if step.purpose == ADMITTED_RESEARCH:
            return self._current_research_admission(connection, step)
        binding = self.review_binding(step.step_id)
        self.review_lifecycle.require_primary_fit(
            step.step_id, binding, connection=connection
        )
        state = self.review_lifecycle._state(connection)
        from devbench.analysis.scientific_review_holds import ReviewBinding

        binding = ReviewBinding.model_validate(binding)
        cycle = self.review_lifecycle._latest_cycle(
            state, binding, "design", step.step_id
        )
        return {
            "binding_sha256": binding.sha256(),
            "cycle_id": cycle["cycle_id"],
            "assessment_sha256": cycle["assessment_sha256"],
        }

    @staticmethod
    def _check_research_review_context(
        db,
        broker,
        cycle,
        lineage,
        controller_principal,
    ):
        initial, _ = broker._context_policy(db, cycle["context_id"])
        principals = broker._context_principals(db, initial)
        phase = f"{cycle['touchpoint']}_initial"
        if (
            initial["lineage_id"] != lineage
            or initial["role"] != "challenger"
            or initial["phase"] != phase
            or controller_principal in principals
        ):
            raise AccessDenied("Research review requires an independent context")
        for identity in cycle["exposure_receipt_ids"]:
            receipt = broker._get(db, "reservation", identity)
            owner = broker._get(db, "context", receipt["context_id"])
            if (
                owner["principal_id"] not in principals
                or receipt["classification"] not in {"structural", "diagnostic"}
                or {"controller_narrative", "validation_plan"}
                & set(receipt["scope"]["tags"])
            ):
                raise AccessDenied("Evidence exceeded the blinded research scope")

    @staticmethod
    def _local_research_roots(db, delivery, context, policy):
        if delivery is None:
            return None
        if context["principal_id"] != "workbench-operator-analyst":
            raise AccessDenied("Use the registered local research analyst")
        broker = delivery.broker
        source = broker._source(db, delivery.source_id)
        broker._check(context, policy, source, set())
        if (
            source["classification"] != "structural"
            or policy.allow_outcomes or policy.allow_statistics
        ):
            raise AccessDenied("Local research disclosure must be structural")
        return set(source["roots"])

    def _research_exposure(self, db, reviews, inputs):
        from devbench.analysis.evidence_access import EvidenceBroker

        controller = self.controller
        delivery = getattr(self, "research_delivery", None)
        if delivery is None:
            broker = getattr(controller, "broker", None)
            context_id = getattr(controller, "context_id", None)
        else:
            broker, context_id = delivery.broker, delivery.context_id
        lineage = self.review_lifecycle.lineage_id
        if (
            type(broker) is not EvidenceBroker
            or broker.authority.ledger.path != self.ledger.path
        ):
            raise OperationBlocked("current_research_disclosure_authority_required")
        try:
            context, policy = broker._context_policy(db, context_id)
            role = (context["role"], context["phase"])
            if context["lineage_id"] != lineage or role != ("controller", "analysis"):
                raise AccessDenied("Research controller context has wrong scope")
            allowed_roots = self._local_research_roots(db, delivery, context, policy)
            for touchpoint in ("design", "publication"):
                self._check_research_review_context(
                    db,
                    broker,
                    reviews[touchpoint],
                    lineage,
                    context["principal_id"],
                )
            evidence = {}
            for name, artifact in sorted(inputs.items()):
                peers = broker._source_peers(db, artifact.sha256)
                if not peers:
                    raise AccessDenied("Research input has no registered source")
                source = broker._source(db, min(row["source_id"] for row in peers))
                broker._check(context, policy, source, set())
                if allowed_roots is not None and set(source["roots"]) - allowed_roots:
                    raise AccessDenied("Research input exceeds its declared roots")
                prior = broker._prior(db, context, source)
                classifications = {source["classification"]}
                classifications.update(row["classification"] for row in prior)
                observed = any(
                    row["state"] in {"delivery_attempted", "receipt_confirmed"}
                    for row in prior
                )
                if classifications - {"structural", "diagnostic"} or not observed:
                    raise AccessDenied("Research requires outcome-free disclosure")
                evidence[name] = {
                    "source": source,
                    "prior": sorted(prior, key=lambda row: row["reservation_id"]),
                }
        except AccessDenied as error:
            raise OperationBlocked("current_research_exposure_required") from error
        return digest(
            {
                "context": context,
                "policy_sha256": policy.sha256(),
                "evidence": evidence,
            }
        )

    def _research_scope(self, db, workflow, step):
        if (
            self.review_lifecycle is None
            or self.review_lifecycle.ledger is not self.ledger
            or self.review_lifecycle.lineage_id != workflow.lineage_id
            or not callable(self.review_binding)
        ):
            raise OperationBlocked("current_research_review_authority_required")
        state = workflow._state(db)
        if state.nodes.get(step.step_id) != step:
            raise OperationBlocked("research_step_changed_during_admission")
        if not step.inputs or any(link.source_port is not None for link in step.inputs):
            raise OperationBlocked("research_requires_exact_immutable_input_versions")
        inputs = {
            link.port.name: workflow._input(state, step, link) for link in step.inputs
        }
        if any(
            artifact is None
            or artifact.selection_status != "none"
            or artifact.learned_scope in {"full_cohort", "unknown"}
            for artifact in inputs.values()
        ):
            raise OperationBlocked("research_input_selection_or_scope_unavailable")
        producers = [state.artifacts[link.source_id][1] for link in step.inputs]
        if any(
            producer is not None
            and state.nodes[producer[0]].oci_method_version_id is not None
            for producer in producers
        ):
            raise OperationBlocked("research_input_requires_owned_non_oci_provenance")
        reviews = self.review_lifecycle.require_admitted_research_fit(
            step.step_id,
            self.review_binding(step.step_id),
            connection=db,
        )
        publication = reviews["publication"]
        return {
            "lineage_id": workflow.lineage_id,
            "step_id": step.step_id,
            "generation": state.generations[step.step_id],
            "step_sha256": digest(step.model_dump(mode="json")),
            "inputs_sha256": digest(
                {
                    name: artifact.model_dump(mode="json")
                    for name, artifact in inputs.items()
                }
            ),
            "review_binding_sha256": reviews["binding_sha256"],
            "design_cycle_id": reviews["design"]["cycle_id"],
            "design_assessment_sha256": reviews["design"]["assessment_sha256"],
            "publication_cycle_id": publication["cycle_id"],
            "publication_assessment_sha256": publication["assessment_sha256"],
            "exposure_sha256": self._research_exposure(db, reviews, inputs),
            "required_checks": reviews["required_checks"],
            "scoped_evidence_sha256": reviews["scoped_evidence_sha256"],
        }

    def _refresh_after_research_brief(self, workflow, pending):
        evidence = pending["evidence"]
        identity = evidence.get(BRIEF_EVIDENCE_KEY)
        if identity is None:
            return
        broker = self.controller.broker
        with workflow._read_connection() as db:
            receipt = broker._get(db, "reservation", evidence.get(
                "controller_disclosure_reservation_id", "",
            ))
            source = broker._source(db, receipt["source_id"])
        if (
            receipt["context_id"] != self.controller.context_id
            or receipt["source_id"] != evidence.get("controller_disclosure_source_id")
            or receipt["content_sha256"] != identity
            or receipt["state"] not in {"delivery_attempted", "receipt_confirmed"}
            or receipt["classification"] not in {"structural", "diagnostic"}
            or source["classification"] != "structural"
        ):
            raise OperationBlocked("current_controller_research_brief_required")
        # The brief adds a structural receipt to the same research exposure scope.
        # Revalidate that new scope before comparing the current scheduling frontier.
        from .workflow_entrypoints import _prepare_research_admissions

        _prepare_research_admissions(workflow, self)

    def prepare_research_admission(self, workflow, step_id):
        """Obtain current research qualification outside the ledger write fence."""
        from devbench.analysis.rna_diagnostic_policy import (
            DiagnosticQualificationAuthority,
        )
        from devbench.core.procedure_profile import ADMITTED_RESEARCH

        authority = self.diagnostic_qualification
        if (
            workflow.ledger is not self.ledger
            or type(authority) is not DiagnosticQualificationAuthority
        ):
            raise OperationBlocked("current_research_diagnostic_authority_required")

        def snapshot(db):
            step = workflow._state(db).nodes[step_id]
            if step.purpose != ADMITTED_RESEARCH:
                raise OperationBlocked("research_step_purpose_required")
            return self._research_scope(db, workflow, step)

        if not hasattr(self, "_research_preflights"):
            self._research_preflights = {}
        key = (workflow.lineage_id, step_id)
        self._research_preflights.pop(key, None)
        scope = self.ledger._read(snapshot)
        admission = authority.verify_research(scope)
        if admission is None:
            raise OperationBlocked("current_research_diagnostic_acceptance_required")
        if (
            authority is not self.diagnostic_qualification
            or self.ledger._read(snapshot) != scope
        ):
            raise OperationConflict("Research scope changed during verification")
        self._research_preflights[key] = (authority, scope, admission)

    def _current_research_admission(self, connection, step):
        from .adaptive_workflow import AdaptiveWorkflow

        lineage = self.review_lifecycle.lineage_id
        key = (lineage, step.step_id)
        retained = getattr(self, "_research_preflights", {}).get(key)
        if retained is None:
            raise OperationBlocked("current_research_diagnostic_acceptance_required")
        authority, scope, admission = retained
        workflow = AdaptiveWorkflow(self.ledger, lineage)
        if (
            authority is not self.diagnostic_qualification
            or self._research_scope(connection, workflow, step) != scope
            or not authority.research_admission_current(scope, admission)
        ):
            raise OperationBlocked("current_research_admission_scope_changed")
        return {
            "binding_sha256": scope["review_binding_sha256"],
            "cycle_id": scope["design_cycle_id"],
            "assessment_sha256": scope["design_assessment_sha256"],
            "research_admission": deepcopy(admission),
        }
