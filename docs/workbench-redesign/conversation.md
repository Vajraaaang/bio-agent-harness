# Recorded scientific conversation

The integrated workbench has a bounded proposal selector and operator-controlled execution. It does not expose a general-purpose multi-turn chat API. The redesigned conversation renders the original research question, retained planner decision or operator-created plan, operator review, actual controller transitions, execution attempts and the next appropriate review surface. It never displays invented reasoning, progress percentages, pending scientific stages or generated biological findings.

## Source contract

`devbench/analysis/workbench_ui.py` is authoritative:

- `state.question`, `state.id`, `state.source`, `state.plan_author` and `state.model_sessions_attempted` identify the recorded request and proposal source. Historical author labels remain authoritative when the current service enables or disables model access.
- `state.planning_result.decision` supplies `kind` and the actual `rationale`, `message` or `reason`. A recorded offline fixture remains visibly distinct from an agent proposal.
- `state.proposal.external_input`, `state.proposal.preview` and `state.proposal.configuration` bind input, observations, declarations and fixed parameters. An input preview is not a scientific-validity claim.
- `state.review` supplies the recorded operator decision, rationale, reviewer and explicit confirmations.
- `state.state_history` supplies the exact `{from,to,at}` transitions created by `_update`. The renderer does not add prior, future or inferred steps. Unknown backend statuses remain readable and do not imply completion.
- `state.execution_history` supplies recorded attempt number, outcome and elapsed seconds, not live tool-level progress.
- `state.error.message` and `state.execution.reason` supply preserved failures. A cancellation request does not mean the worker stopped. Recovery does not imply restart.
- `completed_review_passed` and `completed_review_failed` remain distinct; the latter presents findings. Neither establishes independent scientific review, biological validity or clinical qualification.

## Module and integration

`devbench/analysis/assets/workbench-conversation.js` exposes:

```javascript
window.WorkbenchConversation.render(container, {
  state, config, loading: Boolean(selected && !state), revision: selectedRevisionRecord,
  onAction(action) { /* parent-owned navigation or question drafting only */ }
});
```

`buildModel(state, config)` is the pure presentation function used by the behavior checks. The module has no network, execution or persistence calls and inserts all source text with `textContent`.

The host must insert the trusted module within the existing nonce-based script policy and package the asset. No CSP relaxation, dynamic generated code, remote script, model request or new API route is necessary.

Supported callbacks are:

| Type | Fields | Host behavior |
| --- | --- | --- |
| `navigate` | `view` (`review`, `execute`, `results`) | Open an existing surface. Existing approval and run controls remain authoritative. |
| `draft` | `question`, `sourceRunId`, `input` | Put the question in the composer and show its explicit source context. Do not submit, approve, execute or silently change scientific settings. |

Follow-ups are drafts of **new questions/plans**, not messages appended to a fictional live model conversation. A source run ID and input reference accompany each draft. The host should retain or show that source context, populate only verified compatible input fields, and require the same data declarations and review as any new plan. The source question is quoted explicitly in suggested follow-ups. No current result is treated as canonical merely because it was recently opened.

## Styling contract

The module uses the existing `analyst-message`, `user-message`, `record-message`, `message-label`, `record-action` and `secondary` classes. Additive classes:

- `wc-welcome`, `wc-eyebrow`, `wc-support`, `wc-empty-suggestions`, `wc-mode` for first-use context.
- `wc-context`, `wc-input` for source context.
- `wc-message`, `wc-user`, `wc-planner`, `wc-plan`, `wc-operator`, `wc-message-label`, `wc-message-title`, `wc-message-body`, `wc-details` for conversation records and progressive disclosure.
- `wc-execution`, `wc-timeline`, `wc-step`, `is-current`, `wc-step-title`, `wc-step-time` for exact retained transitions. `data-status` preserves the backend vocabulary for styling.
- `wc-summary`, `wc-tone-neutral|attention|ready|complete|failure|running`, `wc-state-label`, `wc-primary-action` for the latest state and a navigation action.
- `wc-followups`, `wc-followup-note`, `wc-suggestions`, `wc-suggestion`, `wc-scope` for draft-only iteration.

All interactive elements are native buttons/details; source records use headings, paragraphs, ordered lists and timestamps. Rendered statuses announce through `role=status`. Color is not the sole state indicator: readable state labels and actual transition text remain visible.

## Verification

`tests/test_workbench_conversation_redesign.py` runs the module through Node and checks seven behaviors: truthful empty/source modes, exact lifecycle events, actual planner reasons, navigation-only approval/recovery actions, draft context without mutation/submission, unknown-status handling, safe DOM text and explicit click callbacks, and historical proposal authors independent of current access. Initial tests failed because the module was absent; the historical-author regression also failed before correction. All seven then passed, and `node --check` passed.

These checks establish local presentation behavior. They do not qualify a live agent, scientific analysis, accessibility compliance or actual browser layout. Browser verification and the existing guarded backend regression suite belong to the integrated host verification.
