# Signed-in Codex generation route

Implement the user's 2026-10-19 request before any real study. This plan records implementation work; it grants no model or numerical execution.

## Accepted design

Use the official local Codex CLI with explicit ChatGPT authentication, normal client-managed credential custody, ignored user configuration/rules, and no API-key environment or billing fallback. Keep unsupported filesystem/tool boundaries blocked until exact-client qualification. A successful login status is authentication evidence, not execution qualification.

Retain MethodService, ToolDispatcher, the planner's three stages, the existing campaign SQLite ledger, purpose-specific approvals, provenance and LangSmith export. Each stage launches at most one fresh generation job. Account for jobs independently of the client's internal model requests, optional reported tokens and subscription credits. Unknown usage is never zero and never a preventive cap. An uncertain process/launch remains charged and blocks further dispatch. Numerical execution remains separately denied.

## Tasks

- [x] Inspect historical qualification evidence, official documentation and installed client authentication without reading credentials.
- [x] Add a closed versioned generation-job policy and atomic campaign accounting; preserve legacy token policies.
- [x] Add a bounded official CLI transport, explicit authentication checks and fail-closed boundary admission.
- [x] Integrate the selected route with planning, generation-only requests, approvals and source/runtime custody.
- [x] Extend trace projection with honest job versus usage accounting and existing privacy boundaries.
- [x] Freeze and build an ordinary installed artifact; test authorization, concurrency, failure/uncertainty, provenance and legacy compatibility. Final signedin-5: 2,299/2,299 focused tests and controlled synthetic verification passed.
- [x] Obtain independent specification review, then independent implementation-quality review; repair findings and reverify.
- [x] Prepare an exact synthetic probe and replacement budget. The user approved recommended defaults; a separate decision record binds them to the immutable HOLD packet.
- [ ] Qualify the native boundary and production acceptance mechanism, obtain a matching authenticated generation grant, then run the actual signed-in synthetic probe and verify LangSmith delivery/readback. This remains blocked; no real study is authorized here.

## Current evidence and boundaries

The installed official CLI is 0.147.0. Its official login-status command reported ChatGPT authentication; raw output was discarded. Current client evidence and the unsuccessful non-model boundary investigation are retained under `reports/rna-signedin-codex-route/2026-10-19-v1/`. No synthetic generation or real study has run for this route.

The older Platform API allowance (three API requests, 828,288 tokens, $12) does not authorize subscription generation. The proposed numerical limits (30 minutes, 4 GiB memory, 2 GiB storage) are not activated. Legacy failed attempts and sealed evidence remain historical records.

The synthetic probe defaults use three coordinator generation jobs, one at a time, within a 300-second campaign window. Each stage reserves 75 seconds, at most 32 KiB of UTF-8 STDIN containing the inline schema, and at most 128 KiB of combined retained CLI output. The remaining 75 seconds are coordinator headroom. Coordinator retries, resumes, children and numerical study runs are zero. The user approved the recommended defaults; no authenticated issuer grant has been created. Internal client/provider requests and retries, total provider context/serialization, provider tokens, subscription credits and spend have no preventive cap. Local process cleanup does not prove remote service cancellation; hard memory and scratch limits are unavailable.

Native generation remains explicitly blocked: no qualified packet-only native tool/filesystem boundary or production qualification resolver is registered. Synthetic process tests and installed auth-status checks cannot remove that blocker.

Official references: https://learn.chatgpt.com/docs/auth ; https://learn.chatgpt.com/docs/non-interactive-mode ; https://learn.chatgpt.com/docs/permissions ; https://learn.chatgpt.com/docs/app-server .
