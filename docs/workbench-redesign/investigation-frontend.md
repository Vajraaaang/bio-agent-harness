# Unified investigation frontend coordinator

`devbench/analysis/assets/workbench-investigations.js` coordinates the authenticated local investigation API with the existing conversation renderer, dataset controls, exact-selection chip, retained thread history and stable legacy approval/run forms. It adds no network destination, credentials, model client, generated frontend code or scientific computation.

## Host integration

The host composes the shipped module inside its existing nonce-protected script. Call `WorkbenchInvestigations.mount(options)` after authentication/configuration. The required `request(path, body?)` returns parsed JSON; the host supplies its existing token/Origin/Host boundary. The module never reads or stores session credentials.

Required element handles are `conversationContainer`, `datasetSelect` and `questionInput`. Optional handles are `historyContainer`, `inputsContainer`, `modeSelect`, `selectionHost` and `submitButton`. The module never looks up or duplicates legacy DOM IDs. Pass the current service `config` so unavailable agent proposal mode remains disabled; actual execution authority remains on the backend.

Callbacks:

| Callback | Meaning |
| --- | --- |
| `onHistory(summaries)` | Receives actual retained list/poll summary changes for host activity counts; includes `job_id` for native/scientific deduplication. Unchanged polls do not repeat callbacks. |
| `onJob(jobId, investigation)` | Open the existing verified job/artifact binding. Called only when a truthy job binding changes. A failed callback is eligible for a later refresh; it is not treated as successfully loaded. |
| `onGate({kind, jobId, investigationId, revision, investigation, jobState})` | Synchronize stable host-owned `#review-form` and `#run-button`. No job/current thread means hide or clear stale artifact/control bindings. |
| `onCanvas(view, action)` | Focus an existing artifact/code/evidence surface. |
| `onNewThread()` | Clear host draft-source metadata and stale artifact context. The coordinator clears its current record, selection and question draft. |
| `onStatus(message, error)` | Display a concise actual loading/error result. No fake progress is generated. |
| `onBusy(boolean)` | Optional host control for an explicit pending request. The textarea remains editable. |

The returned controller exposes `ready`, `current()`, `refresh()`, `render(force?)`, `open(id)`, `submit()`, `newThread()`, `setSelection(payload)`, `handleAction(action)`, `review(fields)`, `run({allow_data_execution:true})`, `inspect(source)`, `reloadInputs()`, `reloadHistory()` and `destroy()`.

Optional `requirementsProvider()` and `scientificContextProvider()` return explicit host-managed requirements/context; no defaults are relabeled as user requirements. Optional `transition(update)` injects the host’s reduced-motion-aware View Transition wrapper for changed-thread DOM adoption. Unchanged background polls do not start transitions. Optional timer injection supports isolated tests. Normal operation uses native timers and a 1.6-second refresh only while an actual investigation/job status is active.

## Requests and question lifecycle

Initialization loads `/api/inputs` and `/api/investigations` independently in parallel. Dataset selection explicitly invokes `/api/inputs/inspect` with its exact allowed path. Profiles show actual dimensions, column/layer/embedding keys and unresolved experimental information. The coordinator never interprets column names as confirmed assay, capture-unit identity or independent biological replication.

An explicit composer submit posts `{question, source, proposal_mode}` plus any explicit context to `/api/investigations`. It does not require the user to compute a checksum. When an existing thread uses the same dataset, submit sends `action:"follow_up"` with the current expected revision and adopts the returned child record. A different selected dataset creates a new investigation carrying explicit parent references. Clearing the dataset selector does not silently reuse the active source.

Successful submission clears only the exact textarea value that was submitted. Edits typed while the request is pending survive. Dataset, mode and submit controls remain disabled during an explicit mutation, while the textarea remains editable. A newly attached selection added during an in-flight request survives that response. Opening another retained thread preserves an unsent draft’s dataset/origin instead of silently rebinding it to the newly viewed dataset. A failed request retains the draft and selection. Requests exceeding the existing 65,536-byte JSON limit are refused without truncating identities. A late response cannot replace a newly opened/cleared thread.

The renderer receives `WorkbenchConversation.renderInvestigation(container, {investigation, config, jobState, onAction})`. It is called only when the investigation identity/revision/status, authoritative job status, thread turns or synthesis changes. Unchanged polls preserve focused controls and expanded evidence. Each adopted actual poll updates the matching retained history summary (including its status, revision and job binding) before rendering and publishing activity callbacks. The module uses actual backend history and readable status pills; unknown statuses remain readable rather than being fabricated into completion.

## Inline action and scientific authority

Clarification emits a single `clarify` request with the actual entered scalar declarations. The renderer’s optional `scientificContext` is mapped to the backend’s `scientific_context` field, keeping explicit scientific permissions separate from biological declarations. Review passes the existing reviewer, decision, rationale and confirmation object to the backend. Recording approval never calls run. Running requires the separate explicit method argument `allow_data_execution:true`, and the backend still enforces startup permission, reviewed plan identity and frozen limits. Scientific actions forward an explicit existing scientific request. If the inline gate supplies `operatorReview`, it is preserved separately as the outer `operator_review` receipt; the scientific request retains its own workspace revision, action and permission flags. The outer investigation revision comes from the currently retained investigation, never a callback-supplied revision. This module does not invent version references, campaign permissions or route outputs.

Action errors trigger a read-only refresh of the retained revision so a stale approval is not automatically replayed. No mutation is retried automatically. Transient polling failures do not manufacture a terminal state; actual active records remain eligible for the next bounded poll.

## Exact selection context

`setSelection` accepts the existing figure callback `{figure_id, panel_id, observation_ids, context:{job_id,parent_root}, display_only:true}` or its normalized equivalent. It attaches a removable **Selected cells (N)** chip, focuses the question and leaves its text unchanged. It does not paste thousands of IDs into the composer.

The request representation is `{figure_id,panel_id,observation_ids,source_job,parent_root,display_only:true}`. It retains at most 1,000 unique nonempty IDs (each <=200 characters), an exact run/root binding, and <=50,000 bytes of selection JSON. Larger selections require an exact-ID export or a smaller explicit selection; the module never samples or truncates them. The backend independently verifies membership and artifact/source bindings. This context is not a selected-population computation adapter.

## Generated class contract

- Thread history: `.investigation-thread`, `.investigation-thread-title`, `.investigation-thread-meta`, `.dataset-pill`, `.wc-status-pill.wc-pill-good|warning|active|bad|neutral`.
- Dataset drawer: `.dataset-discovery-summary`, `.dataset-list`, `.dataset-row[aria-current]`, `.dataset-row-name`, `.dataset-row-meta`, `.dataset-profile`, `.wc-kv-grid`, `.dataset-unknowns`.
- Selection: `.selection-chip`, `.selection-remove`. The host can style its injected selection container independently.

Dynamic text uses `textContent`. Dynamic row listeners are owned by their detached nodes instead of being retained in a growing global list. The only long-lived injected-element listener is removed by `destroy()`, which also clears polling and invalidates pending responses.

## Verification

`tests/test_workbench_investigation_asset.py`: 18 isolated Node/DOM behavior checks passed. They cover actual request routes and payloads, explicit-only mutation, draft preservation, new follow-up plans, exact removable selection/context, oversized attachment refusal, confirmation/run separation, unchanged-poll rendering, safe source text, timer/listener cleanup, cleared datasets, late-response rejection read-only revision recovery, live-vs-recorded selector permission, source-bound drafts across thread switches, in-flight selection preservation and injected thread transitions. `node --check` passed.

These checks establish coordinator behavior, not browser layout, live-model benefit, biological validity or backend scientific qualification. Root owns composed-page/package integration, the full workbench suite and real browser verification.
