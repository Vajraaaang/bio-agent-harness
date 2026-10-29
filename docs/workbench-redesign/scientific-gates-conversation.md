# Inline scientific review and execution gates

The durable investigation renderer now handles actual scientific planner gates as inline actions. Native workbench jobs continue to use the host's original `#review-form` and `#run-button`; scientific gate forms create neither of those IDs.

## Authoritative projection

A scientific gate appears only when there is no linked native `job_id`, the investigation has its actual `scientific_session_id`, and its gate is `review` or `execution_review`. The renderer consumes the backend's bounded `scientific_plan` and `gate_options` projection.

Submission requires all of the following:

- Gate/session fields match the investigation.
- The actual scientific workspace revision is an integer.
- `gate_options.can_submit === true`.
- An actual eligible frozen proposal (`review`) or actual declared trial (`execution_review`) is selected explicitly.
- Operator label, rationale and all backend-provided `required_confirmations` are completed.
- Execution additionally requires `startup_data_execution_allowed === true` and a separate explicit per-trial data-execution checkbox.

Missing or failed design-review evidence remains blocked through the backend's `can_submit` and `blocked_reasons`. The renderer does not manufacture readiness from a trial's existence. Frozen trial specs and resource limits are inspected through structured accordions and cannot be edited by the gate form.

## Callback contract

A review emits, only on explicit valid form submission:

```javascript
{
  type: 'scientific',
  request: {
    action: 'declare_trial', session_id,
    expected_revision: gateOptions.workspace_revision,
    actor: reviewer, spec: exactSelectedFrozenSpec
  },
  operatorReview: {reviewer, rationale, confirmations}
}
```

An execution emits:

```javascript
{
  type: 'scientific',
  request: {
    action: 'run_trial', session_id, experiment_id,
    expected_revision: gateOptions.workspace_revision,
    actor: reviewer, allow_data_execution: true, idempotency_key
  },
  operatorReview: {reviewer, rationale, confirmations}
}
```

The coordinator passes `request` and maps `operatorReview` to the investigation wrapper's `operator_review`. The wrapper retains that review metadata, consumes it before calling the original scientific API, and verifies source/session/frozen-spec bindings. The renderer performs no HTTP calls.

An execution UUID is generated only after an explicit valid submission. It remains stable for the same container, investigation, scientific workspace revision and declared trial, so a repeated click cannot mint a fresh execution identity. A browser without `crypto.randomUUID` shows an actionable blocker rather than substituting an unsafe identity.

## Additional behavior

After an actual synthesis record, three contextual chips draft questions about quality concerns, batch diagnostics and supported method scope. They carry the current native `job_id` (or `parent_job`) and exact input path. They do not assert findings or submit another plan. The coordinator retains the active investigation/selection context.

Scientific `auto_synthesis` is a separate explicit post-execution grant. Enabling planning-only `auto_advance` never enables it. Both require a configured scientific workflow and appropriate model/classification permissions, with the explicitly synthetic recorded-fixture exception remaining model-free. Native agent planning can use current `config.model_access` when the scientific workspace is disabled; this does not enable scientific auto-advance or synthesis.

Existing arbitrary scoped selects support actual `counts_layer` options and explicit selection-scope options from the backend without inferring a count layer or selected-subset execution.

## Styling and integration

The new form reuses native form/checkbox/disclosure styles with these optional hooks:

- `.wc-scientific-gate`, `.wc-scientific-gate-form`
- `.wc-trial-preview`
- `.wc-gate-blocked`, `.wc-gate-action-message`
- `.wc-scientific-submit`

Returned `inlineGate.kind` is `scientific_review` or `scientific_execution_review`. The host should hide its native legacy approval/run action host for these kinds because the renderer owns the scientific form. Existing native gate values are unchanged.

## Verification

The conversation module suite passes 28 tests and `node --check` passes. Added red-to-green checks cover draft-only synthesis follow-ups, source binding, distinct synthesis permission, native model capability fallback, exact frozen-spec review payloads, required confirmations, explicit execution grants, stable execution idempotency, unavailable-review blockers, and absence of duplicate legacy controls. Backend and real-browser verification remain the integrated host's responsibility.
