# Governed scientific auto-advance

`ScientificWorkspace.action` now accepts a bounded campaign request:

```json
{
  "action": "advance_to_gate",
  "session_id": "an exact anchored RNA planner-session UUID",
  "expected_revision": 12,
  "actor": "local researcher",
  "idempotency_key": "unique-campaign-request",
  "phase": "planning",
  "allow_model_execution": false,
  "method_version_ids": ["exact registered method SHA-256 identities"]
}
```

`phase` defaults to `planning`; the only alternative is explicitly requested `synthesis`. The request cannot supply trial declarations, execution grants, executors, sources, scientific results or replacement runtime objects. Existing project admission, source/runtime identity and expected-revision checks still apply. Method identities are bounded and distinct; omitted identities use the actual registered inventory during capability resolution, after scientific candidate generation.

The response is the existing queued workspace-operation envelope. Poll `catalog.operations` for the campaign and `catalog.workflow_steps` for actual per-node ledger activity while it runs. Every node uses the existing `SerialRNAWorkflow`, `WorkflowActions`, artifact checks and `WorkflowOperationLedger`; there is no parallel replacement execution engine. A trusted host callback rechecks the current application source/runtime before the first node and each subsequent advance, so a mid-campaign source change stops before another dispatch. No unrecorded progress percentage or future stage is shown as completed.

Planning advances `audit → question → discovery → generate → candidate_review → resolve → implementation → design → design_review`, subject to each existing precondition and configured permission. It enters the actual LangGraph `review` interrupt once and returns `awaiting_operator_review`; it never sends the interrupt's resume value. Trial declaration remains an explicit exact-draft/branch selection through `declare_trial`. If a retained declaration is already present, auto-advance stops at `execution_review` and requires `run_trial` with fresh runtime authority. It never executes data.

Synthesis advances only `verify → recommend → conclusion_review` after the actual declared trial cursor has completed. It does not implicitly prepare planning stages, declare a trial, run a pending trial, authorize a reviewer or fabricate a recommendation. Calling planning advancement at a synthesis cursor returns an explicit requirement for a separate synthesis request. Existing independent conclusion review can still require attention or abstain.

Missing runtime, selector, model authority or substantive reviewer returns `awaiting_information` with `required_action` and a reason. Configured discovery/reviewer/implementation bounds remain enforced by the existing runtime. Checkpoints do not preserve execution permission. A failed or uncertain effect returns `recovery_required`; the ledger preserves uncertainty and no automatic redispatch or reconciliation occurs. Cancellation also stops the campaign.

The settled campaign value uses `devbench.scientific-advance/1` and includes exact session, requested phase, actual controller phase/status, next node, gate, required action, retained operation references and step digests/evidence. `scientific_qualification` remains `not_established`; successful graph dispatch is not qualified scientific review or method superiority.

Idempotency is scoped to the retained project campaign request. Repeating the same key and payload returns the same queued/completed/failed/recovery-required operation, even if the original expected revision is now old; it does not renew permissions. Reusing the key for a different session, phase, actor, method scope or model permission is rejected. A new request still requires the current workspace revision.

The read-only `workflow_steps` projection contains session/node/attempt, actual ledger status, controller status, evidence references, request/result hashes and any recorded error type. It initializes nothing and resumes nothing. It is distinct from `operations`, which records browser campaign liveness.

The companion `synthesis_ready(planner, session_id, scientific_workflow=..., selector=...)` helper observes only an existing cursor and ledger. It returns true only for a ready `verify`, `recommend` or `conclusion_review` cursor with nonempty declared trials all completed and independently revalidated predecessor evidence. It resumes nothing, executes nothing and does not initialize missing state. Active invocation leases return not-ready; corrupt/stale evidence is rejected. This permits a coordinator with a previously explicit auto-synthesis grant to queue synthesis once after real trial completion, instead of caching a premature blocked campaign.

RNA recognition includes both verified bulk bindings and the explicit registered AnnData header binding documented in `scrna-scientific-inputs.md`; no generic direct-planner shortcut is used for a missing RNA binding.

Verification is in `tests/test_scientific_auto_advance.py`, using the existing real local graph and explicitly labeled engineering-only scientific runtime. It covers actual unacknowledged review interrupts, no duplicate model/worker dispatch, declaration/execution gates, explicit post-trial synthesis, missing permissions/runtime/selector, interrupted-review uncertainty, cancellation, action idempotency, stale revisions and rejected extra data grants. Use a repository runtime with LangGraph installed, such as `.venv-workflow/bin/python`; the analysis-only runtime does not contain LangGraph. No live model, provider, network or clinical data is used.

Owner validation before integration freeze: 25 direct graph/AnnData checks passed, with the one current-service campaign/idempotency check intentionally deferred to the global source freeze. The broader method-planner/required-entrypoint compatibility run passed 50 selected checks before the final companion-consistency check was added. The dedicated JUnit record is `reports/workbench-refinement/2026-10-28/scientific-graph-direct.xml`; exact owned-file hashes and the deferred check are recorded in `scientific-owner-manifest.json`. Earlier current-service attempts preserved genuine source-change refusals while other implementation writers were active; no source guard was disabled to obtain a pass. Final integrated validation must run all 26 new checks on stable source and the existing scientific workspace/API regressions.
