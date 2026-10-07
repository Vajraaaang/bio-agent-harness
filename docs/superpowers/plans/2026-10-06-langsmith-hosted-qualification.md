# Controlled Hosted Tracing and Native Investigator Qualification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a default-off standalone synthetic hosted-tracing qualification harness, then independently implement preventive aggregate token enforcement and qualify native worker isolation.

**Architecture:** Local ledgers, verified captures and immutable artifacts retain scientific authority. A separate exporter/remote-verifier process consumes sanitized operations and writes only its own durable outbox/evidence. A later milestone binds preventive request limits and packet-only process boundaries to the existing reservation ledger; tracing cannot promote native capabilities.

**Tech Stack:** Python, existing closed JSON validators, SQLite outbox/operation ledger, isolated ordinary wheel-installed runtime, direct LangSmith REST, pytest fake transports and process fault injection; actual native runtime boundary chosen only after enforceability assessment.

---

## Evidence and scope

Read the [handoff](../../langsmith-hosted-qualification-handoff.md), [prior tracing plan](2026-10-06-langsmith-rna-tracing.md), and [epoch 7 report](../../../reports/rna-agent-readiness/2026-10-06-langsmith-tracing-implementation-v1/implementation-summary-epoch7.md) first. The report records 421 focused passes and 3,523 broad passes plus retained T14 failure/four Harmony errors. These are prior evidence, not tests run for this plan. The broad suite remains non-green. There is no hosted service, dashboard, native investigator or independent biological qualification in those totals.

**Data:** bounded synthetic execution fixtures, actual local ledger/dispatch/capture/numerical receipts and sanitized exports; no hidden truth, graders, private clinical inputs, real expression archives or new public-data analysis. **Question:** user's software qualification request above. **Method:** frozen fake-first fault testing, independent remote content verification and UI observation; method choice follows safety/invariance requirements rather than library availability. **Outputs:** actual retained results, including unknowns, failures, censored latency and operator interventions. **Conclusion:** qualify only exercised software/service/security claims, never scientific superiority or clinical validity. No scientific assessment plot is supplied by this plan.

Milestone A produces testable software independently of external pilot authorization. Milestone B is a separate product qualification; do not slip it into tracing tests or enable native admission as a tracing side effect. Independent RNA currently covers `bulk_gene_by_sample_json`; do not claim general scRNA worker support. Preserve defaults, answer schemas, p/q semantics, donor/rank/confounding safeguards and one declared fit per genuine numerical attempt.

## Existing interfaces inspected

| Existing source | Reuse / limitation |
|---|---|
| `devbench/study/rna_langsmith_live.py` | `validate_live_session`, `ReceiptOperationBridge.emit_new`, `RESTLiveTransport.__call__`, `LiveTraceExporter.open/drain_once/shutdown`; transport presently accepts POST/PATCH only and project name, so remote reads and immutable project UUID require additive versioned contracts. |
| `devbench/study/langsmith_outbox.py` | `ExportOutbox.enqueue_operation`, `deliver`, `reconcile`; persists before sending, parent acknowledgment gating, immutable operation revisions, one uncertain revision reconciliation. A supplied digest/state is not an independent remote verifier. |
| `devbench/analysis/rna_trace_receipts.py`, `rna_trace_projection.py`, `rna_trace_contract.py` | Existing authoritative receipt bridge and bounded read-only projection; do not give replay resume/dispatch authority. |
| `tests/test_rna_trace_invariance.py` | Existing actual 0/1/2 pre/post-fit fixtures and output/accounting equality; `_run` is a test helper, not a production driver API. Extract explicitly frozen software fixture support rather than importing live test source. |
| `devbench/analysis/rna_investigation_backend.py` | `AuditedInvestigationBackend.capabilities/require_admission`; native hard-cap and parallel boundary claims false. Child private roots do not establish packet-only isolation. |
| `devbench/analysis/workflow_operations.py` | `WorkflowOperationLedger.open_campaign/reserve_model_call/activate_model_call/settle_model_call/reconcile_model_call`; already provides atomic aggregate accounting, future-parent holds and uncertainty retention. |
| `devbench/analysis/episode_codex.py` | Permission preflight and action each invoke provider runtime; tool rejection occurs after observation. Include both in enforcement. |
| `devbench/study/permissions.py` | Named filesystem/network grants and empty-file sandbox probes exist; those probes are not actual participant qualification. |
| `scripts/run_rna_tracing_verification.py` | Source/runtime binding runner with exact historical selection; extend only through a new hosted runner/explicit selection, never edit historical copies. |

## File ownership and work sequence

Paths in tables are repository-relative descriptions; all code/command filesystem paths must resolve absolutely inside the assigned workspace. One writer owns each group; workers are not alone and must accommodate others' edits without reverting them.

| Owner | Create | Modify only when required |
|---|---|---|
| A contract/remote verifier | `devbench/study/rna_hosted_trace_contract.py`, `rna_langsmith_verify.py`; `tests/test_rna_hosted_trace_contract.py`, `test_rna_langsmith_verify.py` | `rna_langsmith_live.py`, `langsmith_outbox.py`, `rna_langsmith_export.py` for versioned target/reconciliation binding; keep old contracts compatible. |
| A standalone driver/fixtures | `devbench/study/rna_hosted_qualification.py`, `rna_hosted_fixtures.py`; `tests/support/rna_hosted_trace_fixtures.py`; `tests/test_rna_hosted_qualification.py` | No scientific workflow changes unless missing receipt fact requires a separately reviewed narrow fix. |
| A verification/docs | `scripts/run_rna_hosted_tracing_verification.py`; `tests/test_rna_hosted_trace_invariance.py`, `test_rna_hosted_exporter_isolation.py` | `docs/langsmith.md`, `docs/rna-independent-investigations.md` after implementation; source-bundle asset inclusion only if required. |
| B preventive enforcement | `devbench/analysis/rna_token_enforcement.py`; `tests/test_rna_token_enforcement.py` | `workflow_operations.py`, `rna_investigation_backend.py`, `episode_codex.py`, related existing budget tests. |
| B worker boundary | `devbench/analysis/rna_worker_boundary.py`; `tests/test_rna_worker_boundary.py` | Backend child dispatch, permission adapter and closed capability contract; don't change policy enum meaning. |
| B qualification/docs | `scripts/run_rna_native_qualification.py`; `tests/test_rna_native_qualification.py` | Independent-investigator documentation and source-bound runner manifest. |

Tasks A1/A2 may be explored concurrently; their target contract must freeze before either edits existing modules. A3 depends on those contracts. Native feasibility assessment may run read-only in parallel, but B implementation begins after A software gate and a distinct frozen B plan/criteria record. Independent reviewer owns no implementation files and examines failure gates before promotion.

## Frozen contracts and software criteria

Create closed format `devbench.rna-hosted-qualification/1` with: `format`, `qualification_id` (opaque UUID), `mode` (`fake` or `hosted`), `data_scope` (exactly `synthetic`), source/runtime/policy/criteria/fixture-manifest SHA-256 bindings, absolute fresh output path, approved endpoint, workspace UUID, existing project UUID and bounded name, immutable live-session digest, expiry, allowed operations, explicit authorization booleans, and request/span/byte/time limits. Reject unknown keys, relative/symlink/overlapping output paths, expired sessions and digest mismatch before credential access or transport. Identity and private root maps remain local only. Fake mode never requires or reads a credential.

Closed `devbench.rna-hosted-criteria/1` freezes all thresholds and sample selection before any pilot; proposed engineering defaults are **not user scientific requirements**:

| Criterion | Proposed default / treatment |
|---|---|
| Initial reviewed post-run payload | At most 64 spans, 256 KiB; exact canonical payload/hash approval. |
| Initial separate live trial | One retained synthetic session, at most 64 spans/256 KiB, at most 3 delivery attempts per operation, expiry at most 30 minutes; authorization cannot silently expand. |
| Subsequent measurement campaign | 30 explicitly listed workflow attempts (5 repeats × 0/1/2 children × pre/post-fit), at most 1,200 mutation requests and 2,400 bounded query requests overall, 15-minute total deadline; requires a separate approval manifest, not inferred from initial pilot. |
| Visibility | Durable sanitized operation persisted to first verified remote content: p95 upper bound ≤5 seconds; each sample deadline 30 seconds, poll interval 0.5 seconds; report HTTP-ack latency separately. Any censored/missing sample fails this engineering qualification rather than being removed. |
| Dashboard | Initial live trial needs observed parent-child nesting and terminal updates within a frozen 30-second UI observation window; screenshot times and polling/refresh cadence recorded. UI unavailable = dashboard unverified; no inferred p95 from a single screenshot. |
| Topology/updates | 100% expected actual spans and parent edges; 0/1/2 actual children, recorded zero-child decision, preflight/selection/shared numerical/synthesis barriers; same span ID for completion revisions, no invented dispatch or successful uncertain run. |
| Execution invariance | Exactly zero added provider/model, fit, retrieval or source writes from preview/export/verify/reconciliation; fake fixture's declared fit happens once, outputs/statistics/accounting match off/on control. |
| Privacy/credential boundary | Zero canary leaks, source writes, unauthorized network or forbidden reads; scientific workflow/fixture process has no exporter credential or inherited enabled tracing/client/parent context. |
| Recovery | Every deliberately ambiguous send remains uncertain until exact reviewed evidence; no blind POST/PATCH replay. Duplicate UUID existence alone never passes. |

Latency uses one trusted driver monotonic clock for persistence and observation; each remote visible transition lies between last nonmatching read and first matching read. Use the upper bound conservatively and publish intervals, timeouts, sample ordering and endpoint/workspace/project/software bindings. Worker UTC/display anchors cannot supply measured visibility. No assumed cost-free service; retain bounded actual request counts and any known billing basis, otherwise cost unknown. A later criteria revision starts a new campaign; preserve failed evidence.

### Remote identity and reconciliation receipt

The verifier returns closed `devbench.rna-remote-reconciliation/1`: qualification/session digests, endpoint/workspace/project UUIDs, exact `operation_id`, span UUID, immutable revision, expected payload digest, response class, normalized observed content digest, observer monotonic interval, verification result and reviewer approval digest. Only bounded allowlisted remote fields are retained; unknown response fields are excluded from evidence, headers/body free text and credentials are never logged. Record bounded bytes-read/status before sanitization without dumping the raw remote response.

Normalize and compare ID, project/session binding, parent/trace identity, run type/name, tags, start/terminal timestamps, empty inputs/outputs and the expected allowlisted metadata. Match expected field subset under a frozen normalizer that tolerates only documented service additions/canonical timestamp representation; mismatch is not acknowledgment. A start confirmation must not skip a pending finish; later visible terminal content may prove start acceptance but completion acknowledgment needs the exact expected terminal revision/content. Existing `operation_id`, revision and payload SHA are local envelope fields, not presently remote metadata. Add a versioned closed remote-proof allowlist carrying opaque operation UUID, revision and a semantic payload digest computed over canonical allowed payload fields **excluding the proof fields themselves**; bind the full wire-payload digest separately in the local outbox. Qualify service preservation/read-back of those fields with fake and hosted controls. This avoids a self-referential digest and does not grant arbitrary metadata access. If the service strips or cannot distinguish the proof, report `state_match_only`; exact operation/revision acknowledgment stays unqualified or requires explicit manual adjudication that preserves the limitation. Conflicting/equivalent observations stay uncertain; do not assert wire-payload equality to the whole provider response or exactly-once semantics.

Use a new versioned live target contract carrying both frozen project UUID and name; preserve `/1` name-only sessions for historical replay, but require UUID-bound `/2` for hosted qualification. Verify existing name→UUID mapping before sending. Prefer `session_id` transport identity, verify remote project UUID on every result and preserve existing sanitization checks. A mismatch/rename denies transport. If account project resolution needs more API access than approved, fail closed and request the specific future pilot scope.

**Eventual consistency:** a 404 or empty bounded query is visibility evidence, not sufficient proof of absence. Do not automatically call `reconcile(..., remote_state="absent_verified")`. If provider offers no authoritative delivery-status/idempotency guarantee, retain `uncertain` and stop. An operator may authorize a separately documented risk-bearing resend only after review; it must not be reported as a proven no-duplicate recovery. Successful content verification can support acknowledgment without resend. Bind the receipt to exactly one uncertain `(operation_id, revision)` in a **local SQLite** atomic compare-and-swap (no LangSmith server-side CAS or idempotency promise is assumed); reject stale, cross-target, tampered, reused or arbitrary caller receipts and active delivery leases.

## Milestone A — hosted qualification software

### Task A1: Freeze input, authorization and result contracts

- [ ] Add closed validators and absolute path/output-disjoint checks in `rna_hosted_trace_contract.py` before importing fixture execution or opening transports.
- [ ] Write failing contract tests for default-off upload, unknown keys, public/clinical scope, relative paths, symlink escape, mismatched source/runtime/policy/fixture hashes, expired authorization, missing project UUID, unauthorized method and over-limit campaign.
- [ ] Define result statuses independently: `software`, `http_acceptance`, `remote_content`, `api_visibility`, `dashboard`, `native_budget`, `native_isolation`, `scientific_review`; each has `passed/failed/blocked/unverified`, evidence refs and limitations. Hosted success cannot change native fields.
- [ ] Implement only enough validation to pass tests; retain canonical criteria/authorization digests before measurement. Frozen criteria are local evidence and grant no science execution.

Proposed contract API (new, not existing): `validate_qualification(value, *, source_identity, now)` and `validate_criteria(value)` return fresh validated JSON values; `write_result(output, *, status, evidence)` creates a fresh finite-JSON record, never overwrites an attempt.

```python
# New test contract; setup provides a valid synthetic fake manifest.
def test_upload_requires_hosted_authorization(valid_manifest, identities):
    manifest = dict(valid_manifest, mode="hosted", authorize_upload=False)
    with pytest.raises(PermissionError):
        validate_qualification(manifest, source_identity=identities, now=identities["now"])
```

### Task A2: Add bounded remote reads and proof-bound reconciliation

- [ ] Add `RemoteRunVerifier` in `rna_langsmith_verify.py` with explicit credential parameter available only in exporter process; no ambient SDK or env discovery. Restrict endpoint, redirects, proxies, methods, request/response bytes, allowed UUIDs, polling totals and deadlines.
- [ ] Pin documented read contract from [API reference](https://api.smith.langchain.com/redoc) into a reviewed local schema snapshot: `GET /runs/{run_id}` where supported and `POST /runs/query` as a **read-only query**, with exact run IDs/trace IDs and bounded fields/limit. Never issue broad account/project listings. Confirm deployment-compatible query schema before enabling hosted mode; unsupported fields/backend API variant is blocked, not guessed.
- [ ] Extend versioned session/transport target and outbox receipt binding described above; include migration tests for old read-only evidence and new immutable UUID/session binding. Retain POST `/runs` and PATCH `/runs/{id}` from current exporter; don't silently switch to batch SDK or v2 endpoints.
- [ ] Write fake service cases for accepted-but-not-yet-visible POST; visible exact content; wrong project/workspace, same UUID/wrong parent, partial start, stale completion, later revision, 401/403, 404/empty eventual consistency, 409/conflict, 429/Retry-After, 5xx, timeout, oversized response and redirect.
- [ ] Retain request attempt count/outcome locally; 401/403 stop, bounded 429 scheduling may retry only where operation definitely rejected under verified service semantics, transport loss/5xx remain uncertain. Read retries are capped separately; a read POST query is never confused with a write POST.
- [ ] Add exact uncertain-operation CAS reconciliation with verifier-produced reviewed receipt; test digest-only forgery, cross-target receipt, stale revision, active lease, multiple uncertain revisions and source drift. No generic `remote_state` escape hatch in the hosted driver.

```python
# Expected recovery behavior; fake_service models an accepted response lost in transit.
def test_lost_response_is_not_blindly_reposted(prepared_driver, fake_service):
    fake_service.accept_then_timeout = True
    prepared_driver.drain()
    assert prepared_driver.status()["uncertain"] == 1
    prepared_driver.drain()
    assert fake_service.mutation_count == 1
    receipt = prepared_driver.verify_remote()
    prepared_driver.reconcile_reviewed(receipt)
    assert prepared_driver.status()["uncertain"] == 0
    assert fake_service.mutation_count == 1
```

These are proposed fixture/driver methods with signatures defined below; they are not current APIs. `verify_remote()` never approves its own report; a reviewed approval digest records the integrity of an operator approval record and does not constitute an authenticated identity or electronic signature.

### Task A3: Build standalone driver and real retained fixture evidence

- [ ] Implement `rna_hosted_qualification.py` CLI with `--mode fake|hosted`, absolute `--manifest`, `--criteria`, `--output`; require `--allow-upload` plus valid upload authorization for hosted mutations. Default `fake`. Use a module-level `if __name__ == "__main__": main()` guard for supervised workers.
- [ ] Extract minimal bounded software fixture builders from existing off/on tests into `devbench/study/rna_hosted_fixtures.py`, installed in the same ordinary matching project wheel with reviewed bounded data-only assets; keep test wrappers in frozen `tests/support/rna_hosted_trace_fixtures.py`, source-bound by the verification runner. Production driver imports only its installed fixture module and package resources, never `tests` or checkout helpers. Bind builder and asset SHA-256 values in the approved fixture manifest. Driver accepts only reviewed synthetic fixture IDs and manifest hashes; it does not import arbitrary plugins/tests from live source or read external datasets. Add an installed-wheel smoke test under `-I -B` with checkout/test paths unavailable, proving fake-driver fixtures resolve solely to the matching wheel/resources.
- [ ] Capture actual local lead attempt, permission preflight/selection capture, conditional 0/1/2 child dispatches, barrier/attachment, one shared declared numerical attempt and terminal synthesis. Compare fixture off/on outputs/accounting. Recorded fake provider actions are labeled fake, never live-native qualification.
- [ ] Scientific/fixture processes receive a constructed minimal environment with tracing disabled and no credential; credential hydration occurs only inside a separately launched privileged exporter/verifier process through an approved private channel; the controller and fixture producer never read a real key, retrieve inherited key environment values or pass them through their own argument/state objects. Do not spawn fixtures with a parent environment that already contains a key and then merely delete it in the child.
- [ ] Driver records persistence monotonic boundaries and receives timestamped remote observations from exporter to measure intervals. Streaming receipt source and outbox export cannot alter retained artifacts. Replay mode only observes existing source stores read-only; never resumes a workflow.
- [ ] Post-run exact-payload preparation has independent approval/hash record. Live sessions approve a finite schema/fixture/operation scope before receipt production. Review all emitted payload fields against canaries before first remote send; never expand approval to a later fixture/runtime epoch.

### Task A4: Prove invariance, process boundary and failure recovery locally

- [ ] Spy provider/preflight/action, numerical dispatcher, retrieval and source-store writes. Expect zero calls from exporter/verifier/replay and exactly the declared fixture invocations. Compare gene statistics/hypothesis-family identity rather than elapsed timing or separately randomized IDs.
- [ ] Test exporter-only credential with an opaque dummy canary under real separate processes; adversarial fixture attempts to read exporter environment, forbidden home/sibling/coordinator files and outbound network. A process-environment separation pass does not establish native worker OS isolation (B owns that claim).
- [ ] Inject crashes before persist, after persist/before send, after server accept/before response, after response/before local acknowledgment; lease contention, stale/duplicate revisions, parent start unacknowledged, timeout/cancellation/late completion and restart. Source science must never be rerun by recovery.
- [ ] Test privacy canaries in objective/free-text/errors, prompts/responses, gene/sample/donor IDs, paths, grants, headers, matrices and deterministic identity hashes. Allowlist payload construction must reject leaks before outbox persistence.
- [ ] Test spans/bytes/read/mutation/time caps and session expiry during polling/delivery. Retain cap rejection and unfinished state; absence of a terminal receipt cannot become a successful finish.
- [ ] Freeze a new source epoch, ordinary matching wheel/runtime and exact test selection; run A tests plus existing tracing/RNA budget/replay regressions and then exact broad synthetic selection. Correct only new failures; retained T14/Harmony nonpasses stay visible.

### Task A5: Publish fake-qualified driver, stop before external pilot

- [ ] Write fresh report with source/runtime/origin/criteria/test-selection identities, all local failures/interventions, invariance/spies, current native admission still blocked, and explicit hosted/dashboard unverified status.
- [ ] Provide a concrete reviewed initial post-run payload and finite live-session manifest to the operator, identifying endpoint/workspace/project UUID, request caps, expiry, allowed operations/reads, redaction and expected storage/billing unknowns. Preparing these artifacts grants no upload.
- [ ] Request external execution only once concrete artifacts and A software proof exist. Operator selects existing dedicated project and credential access channel; no creation/sharing/account changes without explicit scope. Never print/inspect a credential for this planning task.

### Task A6: Separately authorized hosted pilot and dashboard evidence

- [ ] Verify read authentication and project UUID/name in approved scope; failure leaves hosted blocked. Run one exactly approved post-run payload and one separately approved live synthetic trial before any expanded latency campaign.
- [ ] Verify expected remote content/topology/completion by bounded REST reads; retain immutable operation/receipt bindings and incomplete/unknown states. Exercise accepted-but-lost response using a controlled exporter fault after server acceptance; do not intentionally cause remote duplicated posts.
- [ ] Independently inspect dashboard nesting, overlapping actual recorded investigator spans, preflight/numerical attribution and completion updates with approved UI access. Capture sanitized screenshots and observation times; distinguish display order/anchors from execution time.
- [ ] Freeze and authorize the 30-sample campaign before measurement. Report all intervals/HTTP ack/API visibility/UI observations and censored trials. Stop at caps, authentication failure, leak, content conflict or unidentified remote state; preserve evidence before fixes.
- [ ] Publish separate A software, remote-content, API-latency and dashboard decisions. No user-account cleanup/delete/sharing is implied; retain local evidence and document operator-controlled retention/cleanup as a separate action.

## Milestone B — preventive token budgets and qualified native workers

### Task B1: Establish enforceability before writing a promotion path

- [ ] Define attributable token semantics: parent/preflight/action, every child, retries, cancellation tails, failed calls, cached input and reasoning tokens without double counting. Compare reported native usage to provider accounting; document any hidden sessions/context expansion not observable to the wrapper.
- [ ] Inspect pinned provider/runtime primary interfaces (no credential or live call) for trusted input measurement/upper bound and preventive output/reasoning cap across all internal requests. Provider max-output alone is insufficient for input+output aggregate. Call/time budgets are not tokens.
- [ ] If the pinned native CLI cannot enforce an all-attributable bound, retain `enforced_total_token_cap=False`, `all_attributable_requests_accounted=False` and admission blocked. Produce a concrete gap report. A controlled request gateway/provider adapter is an alternative requiring its own pinned protocol, support qualification and authorization; do not silently replace the project's backend/model.
- [ ] Freeze B capability format, usage basis, budget/isolation criteria and actual boundary runtime before implementation/promotion. Hosted passing fields cannot satisfy B criteria.

### Task B2: Bind preventive dispatch to existing atomic reservations

- [ ] Add `rna_token_enforcement.py` trusted adapter contract: `prepare_bound(reservation, request_identity)`, `dispatch_bound(bound, request)` and `verify_usage(bound, receipt)`. Bound includes campaign/reservation/actor/request UUIDs, source/runtime/policy hash, trusted token upper bound and provider-enforced limits; reject unbound/incompatible provider calls.
- [ ] Reuse ledger atomic `reserve_model_call` and parent-reserve protections. Hold the full enforceable attributable upper bound before any preflight or action; concurrent A/B cannot each consume the same remaining campaign tokens. No second competing ledger.
- [ ] Bind request identity at actual dispatch and retain complete meter receipts; independently normalize input/output/reasoning/cached categories according to frozen provider semantics. Overrun or missing proof blocks campaign and preserves evidence, never truncates telemetry to the cap.
- [ ] Test cap exactly met, next request rejected before provider, simultaneous near-cap admissions, preflight consumes remainder, retry needs fresh reservation, cancellation tails, crash before/after dispatch, missing usage, stale replayed usage, overflow and mismatched identity. Unknown launch/usage retains reservation and blocks blind rerun/refund.
- [ ] Adapt parent generation/reconciliation and child selection only after all their provider sessions flow through the trusted adapter. Source hash inspection and posthoc totals alone cannot turn capability flags true.

### Task B3: Establish actual packet-only worker authority

- [ ] Add `rna_worker_boundary.py` to launch real participant in separate process with private immutable input packet, bounded scratch/output and approved runtime. Coordinator retains ledger/source/review authority; worker cannot write its own approval/capability proof.
- [ ] Use OS/container sandbox controls supported by the pinned runtime to deny sibling, coordinator, host home/credentials/private source and undeclared input reads; deny unauthorized writes, exec/shell/tools and outbound network. Permit only the explicitly mediated provider channel when needed. Unique path names and application aliases are not a security boundary.
- [ ] Construct environment before process creation; exclude exporter credentials, inherited SDK tracing and provider credentials except explicitly mediated provider authorization. Include symlink/hardlink/path traversal, process environment/descriptor inheritance and subprocess escape in negative controls.
- [ ] Couple resource/process/time caps and cancellation to actual launched process tree; retain usage from tails after cancellation and failed children. If enforcement absent on a platform, that platform remains unqualified.
- [ ] Run fake adversarial worker negative tests first, then separately authorized actual-native synthetic participant boundary tests. Dummy subprocess tests/empty-file permission probes alone do not qualify the actual participant.

### Task B4: Independent capability qualification and gated rollout

- [ ] New `run_rna_native_qualification.py` validates source/runtime/policy/boundary identities, packet scope, budget basis and operator authorization before actual provider execution. Default fake/off; no automatic login, network or native campaign.
- [ ] Execute actual participant negative controls under 0/1/2 concurrent children and parent activity: hostile sibling/coordinator/private file canaries, tool/shell/network attempts, environment leakage, writes, path escapes, quota races, cancellations, crashes and source/runtime drift. Separate inability to observe an attempted violation from a proven denial.
- [ ] Reviewer verifies retained bounds/meter receipts, preventive rejection before calls, complete request accounting and OS boundary evidence. Promote only versioned individually supported claims, scoped to exact backend/runtime/platform/permissions. Any identity drift invalidates promotion and fails closed.
- [ ] Run focused native budget/backend/recovery/parent integration and existing synthetic suite in a new B source/runtime epoch. Preserve broad known nonpasses and no scientific superiority claim.
- [ ] Deliver evidence/documentation and keep default serial mode intact. Native admission changes only when required hard-cap and operational-isolation predicates are both proven; a budget-only pass cannot enable investigators.

## Source-bound verification and command interface

This plan executes **none** of the following future commands. Build a fresh ordinary wheel-installed `.venv-rna-hosted-qualification` and freeze source `hosted-1` under a fresh report root `reports/rna-agent-readiness/2026-10-06-hosted-tracing-qualification-v1/`; do not alter `.venv`, `.venv-rna-subagents`, `.venv-rna-tracing`, old locks or any historical report. The new verification runner records current checkout owned-file hashes (including untracked files), wheel SHA-256/RECORD, dependencies/assets/module/test/helper origins, exact explicit selection and before/after source/runtime manifests. No editable installs, `PYTHONPATH`, path insertion, `.pth` hooks or borrowing old project source.

Dependency wheels are preferred. If offline wheels unavailable, any dependency-file copy must be a separately recorded deviation with complete RECORD/hash proof and independent approval; do not imply the prior epoch's copied dependencies were new wheel installs. Pytest helper imports are explicitly bound to new frozen epoch; ordinary product modules originate from matching installed wheel. Generate the same-epoch source ZIP and resource manifest before building the wheel, including the new modules and explicitly approved software assets. Bind that ZIP/resource identity in bundle checks. Source/assets must be sufficient for bundle identity checks without accessing historical mutable source.

New runner CLI: `--epoch-root`, `--selection`, `--output`, `--scope software`, `--allow-bounded-local-fixtures`, `--short-fixture-root`, `--short-workspace-root`. Follow existing tracing runner binding logic, declare the exact seven tracing modules plus the five new A test modules in the new runner's `NEW_TESTS`/focused-selection allowlist (never change the historical runner), and use the exact retained 112 module names for broad selection (names as provenance, copies from new epoch). No recursive repository test collection and no hidden graders/goldens/other benchmark tasks. Test-specific bounded software outputs stay disjoint from source/locks. Each retry uses a fresh attempt directory; source changes require a new epoch/runtime.

```bash
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-rna-hosted-qualification/bin/python -I -B   /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-06-hosted-tracing-qualification-v1/source/hosted-1/scripts/run_rna_hosted_tracing_verification.py   --epoch-root /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-06-hosted-tracing-qualification-v1/source/hosted-1   --selection /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-06-hosted-tracing-qualification-v1/selections/focused-1.json   --output /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-06-hosted-tracing-qualification-v1/verification/focused-1   --scope software --allow-bounded-local-fixtures   --short-fixture-root /Users/vajraang/Documents/ChatGPT/bio-agent-harness/hf1   --short-workspace-root /Users/vajraang/Documents/ChatGPT/bio-agent-harness/hw1
```

Expected: A tests and focused regressions pass, unchanged scientific outputs/source/runtime, zero unauthorized calls/reads/uploads, native gate still blocked. Broaden once to explicit `selections/broad-1.json` with fresh `verification/broad-1`, `hf2`, `hw2`; expected no unexpected nonpasses, actual T14/Harmony identities reported rather than assumed old counts. Permissions/fixtures unavailable means blocked, not pass. Do not install Harmony or change numerical expectations to hide old failure identities.

Standalone installed driver invocation after A software gate (manifest has fake mode, no credential):

```bash
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-rna-hosted-qualification/bin/python -I -B -m devbench.study.rna_hosted_qualification   --mode fake   --manifest /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-06-hosted-tracing-qualification-v1/manifests/fake-1.json   --criteria /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-06-hosted-tracing-qualification-v1/manifests/criteria-1.json   --output /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-06-hosted-tracing-qualification-v1/fake-driver-1
```

Hosted mode additionally requires future concrete hosted manifest, `--allow-upload` and exporter-only credential channel. No executable hosted command or credential setup is included before those reviewed artifacts exist. B receives separate report root/runtime/source and exact explicit selection after B1 feasibility; A source qualification cannot be inherited by changed B source.

## Exact proposed contract/API reference

All key sets below are closed: unknown/missing keys reject; optional values use explicit `null`, never silently omitted keys. Hashes are lowercase 64-character SHA-256 strings; IDs are canonical UUID strings. JSON is finite, canonical and bounded. These are new implementation contracts, not existing source APIs.

```python
QUALIFICATION_KEYS = frozenset({
    "format", "qualification_id", "mode", "data_scope", "source_digest",
    "runtime_digest", "policy_digest", "criteria_digest", "fixture_manifest_digest",
    "output_path", "endpoint", "workspace_id", "project_id", "project_name",
    "live_session_digest", "expires_at", "allowed_operations", "authorize_upload",
    "authorize_remote_read", "authorization_record_digest", "limits",
})
LIMIT_KEYS = frozenset({
    "max_spans", "max_bytes", "max_mutation_requests", "max_read_requests",
    "max_delivery_attempts", "total_deadline_seconds",
})
CRITERIA_KEYS = frozenset({
    "format", "sample_matrix", "api_p95_upper_seconds", "visibility_deadline_seconds",
    "poll_interval_seconds", "dashboard_window_seconds", "max_extra_model_calls",
    "max_extra_fits", "max_extra_retrievals", "max_source_writes", "max_privacy_leaks",
    "required_topology_fraction", "censored_samples_fail", "limits",
})
SAMPLE_KEYS = frozenset({"fixture_id", "stage", "allocation", "repeat"})
RECONCILIATION_KEYS = frozenset({
    "format", "qualification_id", "session_digest", "endpoint", "workspace_id",
    "project_id", "operation_id", "span_id", "revision", "expected_payload_digest",
    "response_class", "observed_content_digest", "observation_interval",
    "verification_result", "reviewer_approval_digest",
})
INTERVAL_KEYS = frozenset({"clock_id", "last_nonmatching_monotonic_ns",
                           "first_matching_monotonic_ns"})
REMOTE_PROOF_KEYS = frozenset({"operation_id", "revision", "semantic_payload_digest"})
```

`allowed_operations` is a nonduplicated subset of `create_run`, `update_run`, `read_run`, `query_runs`; fake transport never sends remotely. Hosted mutation requires upload authorization; reads require separate read authorization. Fake manifest target/authorization fields may be null and must not initiate secret access. A hosted UUID-bound session requires explicit existing-project name/UUID verification. `limits` uses the exact `LIMIT_KEYS` and bounded positive numbers defined in the criteria table. Matrix items use exact `SAMPLE_KEYS`, stage `pre_fit_methods|post_fit_explanation`, allocation `0|1|2`, repeat positive integer, and approved fixture identity. Criteria floats are finite and nonnegative; limits cannot exceed prior reviewed session caps. `verification_result` is `exact_revision_match|state_match_only|mismatch|not_visible|blocked`; only reviewed `exact_revision_match` may acknowledge an exact operation in the hosted path. A missing first match uses null and is censored; read auth failure is blocked rather than latency censored.

Store remote proof under new versioned `extra.metadata.remote_proof` only after expanding the closed allowlist in the new schema; it cannot carry prompts, paths, user IDs or arbitrary metadata. Semantic payload digest covers the frozen normalized fields excluding `remote_proof`; full wire payload digest includes proof and is retained locally. Exact acceptance vector: run ID, trace ID, parent ID, project UUID, run type/name, tags, immutable start time, expected terminal time/status, empty inputs/outputs, closed expected metadata subset, operation UUID, revision and semantic digest. `null` parent is valid only for the one retained workflow root. Do not allow service-supplied free fields to fill missing expected values.

Proposed public APIs (write type hints using ordinary Python types; no implicit env/client lookup):

```python
def validate_qualification(value: dict, *, source_identity: dict, now: datetime) -> dict: ...
def validate_criteria(value: dict) -> dict: ...
def write_result(output: Path, *, status: dict, evidence: dict) -> Path: ...

class RemoteRunVerifier:
    def __init__(self, *, manifest: dict, criteria: dict,
                 api_key: str, read_transport: Callable) -> None: ...
    def observe(self, *, operation: dict, expected_payload: dict,
                persisted_monotonic_ns: int) -> dict: ...

class QualificationDriver:
    def __init__(self, *, manifest: dict, criteria: dict,
                 exporter_channel: object, fixture_factory: Callable) -> None: ...
    def drain(self) -> dict: ...
    def status(self) -> dict: ...
    def verify_remote(self) -> dict: ...
    def reconcile_reviewed(self, receipt: dict) -> dict: ...

def main(argv: list[str] | None = None) -> int: ...
```

These signatures declare contracts; ellipsis is Python stub notation, not completed implementation. `RemoteRunVerifier` is constructed only inside privileged exporter/verifier process. `QualificationDriver` holds only a bounded IPC channel, fixture factory and secret-free manifest/criteria; it never receives a key or SDK client. `drain()` asks exporter to ingest/deliver only approved operations and returns `status()`. Status keys are exactly `prepared`, `acknowledged`, `uncertain`, `rejected`, `blocked`, `mutation_requests`, `read_requests`, each an integer count. `verify_remote()` returns one candidate reconciliation receipt for the sole pending operation; zero/multiple ambiguous candidates return blocked and require explicit operation selection before a subsequent bounded read. It does not approve the receipt. `reconcile_reviewed()` validates operator record hash, exact target/operation/revision/content and no-active-lease predicates, then invokes local outbox CAS. A supplied arbitrary digest cannot substitute for existence/contents of the reviewed approval record.

The existing recovery test's `prepared_driver` fixture supplies a fake-only recorded approval record when calling `reconcile_reviewed`; hosted driver requires the separately retained operator review record. Hash binding supplies integrity/provenance, not authentication, qualified scientific review or an electronic signature. `fake_service` defines `accept_then_timeout: bool` and cumulative `mutation_count: int`; it persists requested run content before raising a simulated timeout. `valid_manifest`/`identities` fixtures provide the exact closed fake manifest/source identity plus a `datetime` comparison clock; all assets are source-bound.

### Native feasibility deliverable and exact stop condition

Before B implementation, retain a fresh `native-feasibility-1.json` with closed keys:

```python
NATIVE_FEASIBILITY_KEYS = frozenset({
    "format", "source_digest", "runtime_digest", "policy_digest", "backend_id",
    "backend_version", "platform", "attributable_request_classes", "token_basis",
    "trusted_input_bound", "preventive_output_bound", "internal_request_coverage",
    "provider_identity_binding", "boundary_runtime", "boundary_controls",
    "evidence_refs", "enforcement_status", "isolation_status", "blocking_reasons",
})
```

Format is `devbench.rna-native-feasibility/1`. Request classes explicitly enumerate preflight, parent action/reconciliation, child action, retries/resumes and cancellation tails. `token_basis` records normalized accounting with double-count prevention and any unknown categories. The four bound/coverage/identity fields are `supported|unsupported|unknown` with supporting references in `evidence_refs`; no live invocation is implied. Status is `feasible_for_qualification|blocked|unverified`, never qualified before actual proof. `boundary_runtime` is null until an identified pinned OS/container/provider runtime is selected. `boundary_controls` lists each filesystem/env/network/exec/process-resource enforcement mechanism and supported platform, not generic sandbox wording.

**Stop condition:** any unsupported/unknown bound, hidden attributable request, unsupported preventive control or unselected actual boundary leaves B admission false. Deliver gap report and implementation alternatives, then stop promotion/live participant work. Do not promise the existing CLI supports missing caps. Once feasibility is positive, freeze exact B criteria in a new revision before B2; changing provider/runtime requires independently reviewed scope.

B source/runtime/report storage is distinct: new `.venv-rna-native-qualification`, source `native-1`, and `reports/rna-agent-readiness/2026-10-06-native-budget-isolation-qualification-v1/`. Retain feasibility, boundary policy, request-meter semantics, source ZIP/wheel/runtime/origins and new source manifest. `scripts/run_rna_native_qualification.py` gets `--mode fake|native` plus `--manifest`, `--criteria`, `--epoch-root`, `--selection`, `--output`; default fake, native requires separately recorded provider authorization. Its fake verification selection explicitly includes the three new B test modules and existing `test_rna_investigation_budget`, `test_rna_investigation_backend`, `test_rna_investigation_recovery`, `test_rna_investigation_evaluation_isolation`, `test_rna_investigation_parent_integration`, `test_langgraph_investigations`, `test_workflow_operations`, `test_episode_codex`. Broader regression is exact historical 112 plus tracing 7, hosted A5 and native B3 module names from matching new frozen source, deduplicated. No old source import or recursive test discovery.

Future fake-only B command, after feasibility, runner and matching wheel exist:

```bash
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-rna-native-qualification/bin/python -I -B /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-06-native-budget-isolation-qualification-v1/source/native-1/scripts/run_rna_native_qualification.py --mode fake --manifest /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-06-native-budget-isolation-qualification-v1/manifests/fake-1.json --criteria /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-06-native-budget-isolation-qualification-v1/manifests/criteria-1.json --epoch-root /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-06-native-budget-isolation-qualification-v1/source/native-1 --selection /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-06-native-budget-isolation-qualification-v1/selections/focused-1.json --output /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-06-native-budget-isolation-qualification-v1/verification/focused-1
```

Expected: exact selected fake budget/boundary tests pass and no provider calls; actual-native qualification fields remain unverified. A future native campaign is independently authorized and run in fresh output; fake tests cannot promote native flags. If feasibility is blocked, this fake command may exercise generic ledger/rejection controls but must not claim implemented native enforcement or actual participant isolation.

## Completion and independent review checklist

- [ ] Every user requirement maps to an A/B task and independently retained result field.
- [ ] Local software qualification precedes external authorization; uploads/defaults remain off.
- [ ] Parent acknowledgment, revision-bound completion and ambiguity recovery are proven without reexecution/blind resend.
- [ ] REST content/latency and dashboard rendering have separate evidence/status; missing UI/read permission cannot be mislabeled success.
- [ ] Token metering/preventive caps include all requests and cancellation tails; accounting already present is preserved.
- [ ] Actual participant/process authority is tested; capabilities remain blocked on any missing proof.
- [ ] Reviewer signs off on report scope, leaks/overruns/unknowns and preserved failed epochs before capability promotion.
- [ ] Scientific method comparison, general scRNA support, expert validation and clinical approval are explicitly outside qualification claims.

## Official API references and decisions

Inspected 2026-10-06; revalidate before service rollout. [REST tracing](https://docs.langchain.com/langsmith/trace-with-api) documents individual create/update and parent linkage; direct REST is selected to preserve the tested isolated transport, although the vendor recommends SDKs. [API reference](https://api.smith.langchain.com/redoc) governs endpoint-specific request/response schemas. [Trace queries](https://docs.langchain.com/langsmith/export-traces) provides the bounded query/read basis; explicitly verify project binding when selecting by IDs because ID lookup can ignore other filters. [Authentication](https://docs.langchain.com/langsmith/create-account-api-key) distinguishes user/service workspace authority; do not assume project-level credential isolation or roles unavailable on the account plan. The driver enforces project scope locally and reports the credential's broader permitted authority as a limitation without exposing its value.

No current provider capability or hosted consistency/idempotency guarantee is assumed from these docs. Remaining concrete operator inputs: existing project/workspace/region, approved read/write scope/retention and credential channel. Remaining engineering decision: whether pinned native runtime can enforce complete attributable preventive token bounds; if not, fail closed and preserve that blocker.
