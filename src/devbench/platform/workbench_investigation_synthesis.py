"""Bound synthesis continuation for historical serial investigations."""
import re

from devbench.analysis.checkpoints import digest


class WorkbenchInvestigationSynthesis:
    @staticmethod
    def _synthesis_operations(record, catalog):
        return sorted((row for row in catalog["operations"]
            if (row.get("request") or {}).get("investigation_synthesis", {}).get("investigation_id") == record["id"]),
            key=lambda row: row["created_at"])

    def _synthesis_runtime(self, record):
        classification = record["scientific_context"].get("data_classification", "unclassified")
        scientific = self.app.scientific
        offline = (scientific.configuration().get("planning_mode") == "recorded_fixture"
            and classification == "synthetic"
            and not getattr(scientific.scientific_workflow, "allow_review_model_execution", False))
        live = (classification in {"synthetic", "public"} and self.app.allow_model_execution is True
                and getattr(self.app, "model_data_scope", None) == classification)
        return classification, offline, live

    @staticmethod
    def _synthesis_key(record, evidence):
        from .workflow_entrypoints import synthesis_evidence_key
        if evidence["session_id"] != record.get("scientific_session_id"):
            raise ValueError("Retained synthesis belongs to another session.")
        return synthesis_evidence_key(record["id"], evidence["session_id"], evidence["frozen_revision"],
            evidence["trials"], evidence["results"])

    def _synthesis_descriptor(self, record, catalog=None):
        from .workflow_entrypoints import synthesis_descriptor
        scientific = getattr(self.app, "scientific", None)
        session = record.get("scientific_session_id")
        if (scientific is None or not session or record.get("analyst_profile") == "analyst_v1"
                or record.get("workflow_mode") == "adaptive/1"):
            return None
        plan = record.get("scientific_plan") or {}
        if not any(row.get("status") == "completed" for row in plan.get("experiments", [])):
            return None
        catalog = catalog if catalog is not None else self._scientific_catalog()
        operations = self._synthesis_operations(record, catalog)
        relevant = [row for row in catalog["operations"] if row["operation_id"] in record["scientific_operations"]
                    or row in operations or (row.get("request") or {}).get("session_id") == session]
        steps = [row for row in catalog.get("workflow_steps", []) if row["session_id"] == session
                 and row["node"] in {"verify", "recommend", "conclusion_review"}]
        classification, offline, live = self._synthesis_runtime(record)
        base = {"eligible": False, "expected_revision": record["revision"], "data_classification": classification,
            "steps": steps, "model_scope": "external" if live else "recorded_fixture" if offline else "local_verification"}
        if record.get("singlecell_trial"):
            try:
                full_plan = scientific._plan(session)
                trial_rows = [{key: result[key] for key in ("experiment_id", "candidate", "status", "metric_values", "warnings", "failures", "provenance") if key in result}
                              for result in full_plan.get("results", []) if "provenance" in result]
                if trial_rows:
                    population_evidence = self._population_conclusion(record, trial_rows)
                    if population_evidence:
                        base["population_evidence"] = population_evidence
            except (ValueError, KeyError, OSError):
                base["population_evidence_unavailable"] = True
        if record.get("scientific_cancel_requested"):
            return {**base, "status": "cancelled", "reason": "The workflow was cancelled. Inspect the retained evidence."}
        active = next((row for row in relevant if row["status"] in {"queued", "running"}), None)
        if active is not None:
            binding = (active.get("request") or {}).get("investigation_synthesis")
            sequence = ("verify", "recommend", "conclusion_review")
            next_node = binding.get("next_node") if isinstance(binding, dict) else None
            if next_node not in sequence:
                return {**base, "status": "blocked", "reason": "A scientific operation is active."}
            done = {row["node"] for row in steps if row["status"] == "completed"}
            node = next((item for item in sequence[sequence.index(next_node):] if item not in done),
                "conclusion_review")
            return {**base, "status": "active", "next_node": node}
        if any(row["status"] == "recovery_required" for row in relevant):
            return {**base, "status": "recovery_required", "reason": "An operation has uncertain effects and requires reconciliation."}
        review = next((row for row in reversed(steps) if row["node"] == "conclusion_review" and row["status"] == "completed"), None)
        if review and review.get("evidence"):
            try:
                from devbench.engine.workflow_operations import WorkflowOperationLedger
                from devbench.study.evidence import _read, decode_json
                from .workflow_entrypoints import LEDGER_NAME

                ledger = WorkflowOperationLedger(scientific.root / LEDGER_NAME, readonly=True)
                retained = ledger.get(session, "conclusion_review", review["attempt"])
                if (retained is None or retained["status"] != "completed"
                        or retained["result_digest"] != review["result_sha256"]):
                    raise ValueError("Conclusion review does not match the retained operation")
                methods = scientific.methods()
                reference = retained["result"]["evidence"]["record_ref"]
                runtime = decode_json(_read(methods.artifact_path(reference["artifact_id"]), 1_000_000))
                service = runtime["service_evidence"]["record_ref"]
                assessment = decode_json(_read(methods.artifact_path(service["artifact_id"]), 1_000_000))["record"]
                judgment = assessment.get("claim_judgment") or {}
                usage = assessment.get("request_result") or {}
                base["review"] = {"status": assessment["status"],
                    **{key: judgment[key] for key in ("rationale", "scientific_status", "missing_evidence") if key in judgment},
                    **({"reason": usage["reason"]} if usage.get("reason") else {}),
                    "model_usage": {key: usage[key] for key in ("model_sessions_attempted", "tokens", "cost_usd", "actual_model") if key in usage}}
            except (ValueError, KeyError, OSError) as error:
                return {**base, "status": "blocked", "reason": "Conclusion review receipt unavailable. " + str(error)[:1500]}
        if review and review.get("controller_status") == "review_required" and review.get("attempt") in {0, 1}:
            return {**base, "status": "review_required", "reason": record.get("scientific_blocker_reason"),
                "required_action": "inspect_scientific_review"}
        if record.get("synthesis"):
            status = record["synthesis"].get("supported_findings", {}).get("workflow_status")
            if status in {"completed", "abstained"}:
                return {**base, "status": status}
        if operations:
            last = operations[-1]
            value = last.get("value") or {}
            if last["status"] == "failed":
                return {**base, "status": "failed", "reason": last.get("error", {}).get("message"),
                    "required_action": "Inspect the failed operation and reconcile any uncertain effects."}
            if (value.get("required_action") and value["required_action"] != "authorize_model_execution"
                    and not (value.get("phase") == "recommend" and value.get("required_action") == "inspect_and_reconcile")
                    and not (value.get("phase") == "conclusion_review" and value.get("required_action") == "inspect_scientific_review"
                        and (base.get("review") or {}).get("reason") == "Public analysis cannot receive private or coordinator fields: seed")):
                return {**base, "status": value.get("controller_status") or value.get("status"),
                    "next_node": value.get("next_node"), "reason": value.get("reason"),
                    "required_action": value["required_action"]}
        try:
            evidence = synthesis_descriptor(scientific.planner(), session,
                selector=scientific.selector, scientific_workflow=scientific.scientific_workflow)
        except (ValueError, OSError, RuntimeError, KeyError) as error:
            reason = str(error)[:2000]
            status = ("cancelled" if reason == "The retained workflow is cancelled." else
                "recovery_required" if "requires reconciliation" in reason else "blocked")
            return {**base, "status": status, "reason": reason}
        node = evidence["next_node"]
        if evidence["data_classification"] != classification:
            return {**base, "status": "blocked", "reason": "The retained plan and investigation data classifications differ."}
        base.update(next_node=node, evidence_key=self._synthesis_key(record, evidence),
            trials=[row["experiment"]["id"] for row in evidence["trials"]],
            evidence_artifacts=[artifact["artifact_id"] for row in evidence["results"] for artifact in row["artifacts"]])
        runtime = scientific.scientific_workflow
        if runtime is None:
            return {**base, "status": "blocked", "reason": "The scientific workflow capability is unavailable."}
        configuration = runtime.configuration()
        if node != "verify" and (not (offline or live) or (node == "recommend" and scientific.selector is None)
                or (configuration.get("format") == "devbench.workflow-scientific-configuration/1"
                    and configuration.get("reviewer") is None)):
            return {**base, "status": "blocked", "reason": "The required synthesis capability is unavailable for this data scope."}
        return {**base, "status": "pending", "eligible": True}

    def _enqueue_synthesis(self, record, body, *, automatic=False):
        from devbench.engine.methods import _label
        from .workbench_investigations import _copy

        scientific = getattr(self.app, "scientific", None)
        fields = {"action", "expected_revision", "evidence_key", "idempotency_key", "allow_model_execution", "reviewer_label"}
        if (set(body) != fields or type(body["expected_revision"]) is not int or body["expected_revision"] < 0
                or type(body["allow_model_execution"]) is not bool
                or not isinstance(body["evidence_key"], str) or not re.fullmatch("[0-9a-f]{64}", body["evidence_key"])):
            raise ValueError("Use the exact synthesis request fields, evidence digest, and explicit boolean consent.")
        key, reviewer = _label(body["idempotency_key"]), body["reviewer_label"]
        if not isinstance(reviewer, str) or not reviewer or reviewer != reviewer.strip() or len(reviewer) > 120:
            raise ValueError("Use a trimmed reviewer label of at most 120 characters.")
        if scientific is None:
            raise PermissionError("Scientific synthesis is unavailable.")
        with self.app._lock:
            self.app._require_current_runtime()
            catalog = self._scientific_catalog()
            for operation in self._synthesis_operations(record, catalog):
                retained = scientific.methods().get("workspace_operation", operation["operation_id"])
                saved = retained["request"]["investigation_synthesis"]["authorization"]
                if _label(saved["idempotency_key"]) == key:
                    if saved != body:
                        raise ValueError("Conflicting synthesis idempotency key payload.")
                    return operation
            if body["expected_revision"] != record["revision"]:
                raise RuntimeError("Investigation changed. Refresh before continuing synthesis.")
            descriptor = self._synthesis_descriptor(record, catalog)
            if not descriptor or not descriptor["eligible"] or descriptor["evidence_key"] != body["evidence_key"]:
                raise RuntimeError((descriptor or {}).get("reason") or "The retained synthesis evidence changed.")
            classification, offline, live = self._synthesis_runtime(record)
            consent = body["allow_model_execution"]
            if (consent and not (offline or live)) or (not consent and not (automatic and offline)
                    and descriptor["next_node"] != "verify"):
                raise PermissionError("The requested synthesis consent does not match the current capability and data scope.")
            offline_synthesis = offline and (consent or automatic)
            dispatch_models = consent and live and not offline_synthesis
            session = record["scientific_session_id"]
            binding = {"investigation_id": record["id"], "next_node": descriptor["next_node"],
                "classification": classification, "authorization": _copy(body), "automatic": automatic}

            started = False

            def validate(evidence):
                nonlocal started
                self.app._require_current_runtime()
                current = self._load(record["id"])
                current_classification, current_offline, current_live = self._synthesis_runtime(current)
                if (current.get("scientific_cancel_requested") or current_classification != classification
                        or evidence["data_classification"] != classification or (dispatch_models and not current_live)
                        or offline != current_offline or self._synthesis_key(current, evidence) != body["evidence_key"]
                        or (not started and evidence["next_node"] != binding["next_node"])):
                    raise PermissionError("Queued synthesis evidence or runtime authority changed.")
                started = True

            return scientific._advance_to_gate({"action": "advance_to_gate", "expected_revision": catalog["revision"],
                "session_id": session, "phase": "synthesis", "idempotency_key": digest([record["id"], key]),
                "allow_model_execution": dispatch_models}, reviewer, synthesis_binding=binding, validate_synthesis=validate,
                verification_only=not (offline_synthesis or dispatch_models))

    def scientific_trial_completed(self, session, operation_id):
        # The trial operation is committed before this callback. Reads never dispatch.
        with self._lock:
            for identity, grant in list(self._campaign_grants.items()):
                record = self._load(identity)
                context = record["scientific_context"]
                if (record.get("scientific_session_id") != session or operation_id not in record["scientific_operations"]
                        or grant != {key: context.get(key) for key in grant} or not context.get("auto_synthesis")):
                    continue
                try:
                    self._sync_scientific(record)
                    descriptor = self._synthesis_descriptor(record)
                    if not descriptor or not descriptor["eligible"]:
                        self._save(record)
                        continue
                    _, offline, live = self._synthesis_runtime(record)
                    if record.get("singlecell_trial"):
                        live = live and self._current_grants.get(identity, {}).get("allow_model_execution") is True
                    if not (offline or (live and context.get("allow_model_execution") is True)):
                        self._save(record)
                        continue
                    self._enqueue_synthesis(record, {"action": "continue_synthesis", "expected_revision": record["revision"],
                        "evidence_key": descriptor["evidence_key"], "idempotency_key": "automatic:" + operation_id,
                        "allow_model_execution": bool(live and not offline), "reviewer_label": "investigation-controller"},
                        automatic=True)
                    self._sync_scientific(record)
                except (ValueError, PermissionError, RuntimeError, OSError, KeyError) as error:
                    self._failure(record, error)
                self._save(record)
