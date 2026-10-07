# Controlled hosted tracing qualification handoff

**2026-10-06 — planning only.** Implement the [detailed plan](superpowers/plans/2026-10-06-langsmith-hosted-qualification.md) in two independently gated milestones: **A, hosted synthetic tracing qualification software**, then **B, preventive aggregate token enforcement and qualified native worker isolation**. No hosted pilot, credential inspection, model campaign, account changes, dependency installation or product implementation was performed while preparing these documents.

## 1. What data do we have?

Observed retained execution evidence is in the [epoch 7 implementation report](../reports/rna-agent-readiness/2026-10-06-langsmith-tracing-implementation-v1/implementation-summary-epoch7.md): 421 focused tests passed; broad verification recorded 3,523 passes, the retained T14 failure and four Harmony errors. This is prior retained evidence inspected for planning, not a fresh rerun or a wholly green suite. Local fake-transport p95 receipt-to-ack was 25.448 ms against the plan's 2-second engineering target. Hosted latency and dashboard rendering have not been measured. No independent scientific plot was reviewed for this milestone.

Synthetic fixture units are software workflow attempts with lead and actual 0/1/2 conditional investigator dispatches, pre-fit or post-fit phases, local receipts, sanitized outbox operations and fitted-output references. These are execution units, not biological replicates. The existing independent-investigator contract supports bulk gene-by-sample JSON; a general single-cell/AnnData investigator contract is outside this increment. Source/runtime hashes establish provenance, not biological validity.

## 2. What question are we investigating?

The user's request is to build a controlled standalone hosted qualification harness, prove nesting and completion, recover safely after crashes/timeouts, isolate exporter credentials, preserve zero additional model calls/fits, and measure visibility against frozen criteria. The user also requests the subsequent hard aggregate-budget and worker-isolation milestone. Today's authorization is to produce the implementation plan and handoff; it does not authorize an external upload or native provider campaign.

## 3. Which approach is appropriate, and why?

Use existing local receipts and read-only evidence projection as authority, a separate credential-bearing exporter/remote verifier, and the durable outbox. A standalone driver gives bounded software controls without injecting SDK callbacks or external credentials into scientific workers. REST preserves the already implemented transport and exposes individual POST/PATCH outcomes. Vendor SDK batching is a possible future separately qualified alternative, not a reason to replace the tested exporter. Remote reads can establish observable content; eventual consistency prevents a missing query result from automatically proving non-delivery. Dashboard rendering needs separate UI evidence.

Milestone B reuses the atomic SQLite reservation ledger; its missing capability is preventive provider enforcement and actual OS/process isolation. Tokens reported after a call cannot establish a hard cap. If the native runtime cannot enforce all attributable input/output/reasoning/preflight/retry tokens within a trusted bound, keep native admission blocked and document the blocker rather than rebranding soft limits.

## 4. Current software and proposed changes

Existing owners/interfaces:

- `devbench/study/rna_langsmith_live.py`: `validate_live_session`, `RESTLiveTransport`, `ReceiptOperationBridge`, `LiveTraceExporter`.
- `devbench/study/langsmith_outbox.py`: `ExportOutbox.enqueue_operation`, `deliver`, `reconcile`; uncertainty blocks blind re-send. Existing reconciliation accepts a reviewed digest and caller-supplied state; the new verifier must bind remote evidence to an exact operation and target before using it.
- `devbench/analysis/rna_trace_receipts.py`, `rna_trace_projection.py` and `rna_trace_contract.py`: trusted local receipt, inert projection and closed sanitization.
- `devbench/analysis/rna_investigation_backend.py`: native admission explicitly unqualified; child directories alone are not security proof.
- `devbench/analysis/workflow_operations.py`: durable aggregate reservations, future parent capacity and uncertain usage retention.
- `devbench/analysis/episode_codex.py`: permission preflight and selection both require preventive token and boundary enforcement.

Create focused hosted contract/verifier/driver modules, synthetic fixture staging and tests named in the plan. Keep source evidence read-only; the driver writes only fresh qualification output and exporter-owned outbox. Freeze new ordinary wheel-installed source/runtime epochs. Preserve every old epoch, failed attempt, dependency lock and qualified environment. Do not borrow project source through editable installs, `PYTHONPATH`, hooks or source-path insertion.

## Gates and operator decisions

**A1 software gate:** fake transport and fault injection first; trace topology and completion, redaction, exact operation reconciliation, zero extra execution, and credential/process separation. A pass here is not hosted qualification.

**A2 external authorization gate:** operator supplies an existing dedicated project UUID/name, approved hosted endpoint/region and workspace UUID, bounded synthetic fixture/source/runtime/policy hashes, exact allowed mutations/reads, expiry and request/storage ceilings. One reviewed post-run payload and one live session have separate approval records. Upload remains default-off. No project creation, sharing, deletion or account change is implicitly authorized.

**A3 hosted gate:** read-side authentication, bounded content verification, ambiguity recovery and API visibility evidence. Capture dashboard screenshots/observations for actual nesting/completion separately. REST success alone is insufficient. Publish failures and missing UI evidence; do not infer a dashboard pass.

**B gate:** independently frozen capability proof for all-attributable preventive total-token enforcement and actual worker isolation; promotion remains false until both requirements and runtime-specific negative controls pass. Budget enforcement, service/dashboard qualification, scientific comparison and clinical approval are separate claims.

Proposed engineering defaults and full failure matrix are in the plan; freeze them before measurement. They are assistant-selected software acceptance criteria, not user-prespecified scientific parameters. Endpoint/workspace/project, operator authorization, account read permission and enforceable native token semantics remain required inputs. No credentials should be copied into documentation or inspected during planning.

## Evidence expected at completion

A new immutable report must contain source/wheel/runtime/test-helper identities, criteria digest, fixture inventories, source before/after manifests, outbox and remote reconciliation receipts, API visibility intervals/censored samples, dashboard evidence status, unchanged scientific outputs and model/fit invocation counts, and every operator intervention. Preserve unknown costs/usage as unknown. Observability success neither validates RNA-seq method choice nor enables clinical release.

Official references inspected on 2026-10-06: [REST tracing](https://docs.langchain.com/langsmith/trace-with-api), [querying traces](https://docs.langchain.com/langsmith/export-traces), [API authentication](https://docs.langchain.com/langsmith/create-account-api-key), [API reference](https://api.smith.langchain.com/redoc). API behavior is documented, not tested against the service by this planning task.
