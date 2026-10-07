# Extensible scientific methods implementation plan

**Goal:** Incorporate the supplied contract-first design into the existing harness, proving that a previously unknown method can produce a reviewable branch result without changing the approved analysis.

**Architecture:** Registered ToolDispatcher operations and digest-pinned custom OCI operations share an immutable MethodContract, Step and Run API. Reuse the existing canonical JSON, artifact hashing/copying and ExecutionDAG evidence. SQLite provides atomic revision checks; content-addressed files retain source, environment locks and datasets. The first implementation targets a single-user local workspace and bounded single-file artifacts.

**Stack:** Python, jsonschema Draft 2020-12, sqlite3, existing provenance utilities, Docker CLI and AnnData.

## Implementation

- [x] Add immutable contracts and adapt the actual registered ToolDispatcher methods. Keep implementation trust distinct from scientific acceptance. Validate finite parameters without remote schema resolution.
- [x] Add content-addressed artifacts, method versions, analysis revisions, branches, steps, runs, proposals and append-only audit events. Reject stale proposals transactionally. Preserve approved historical steps and method trust labels.
- [x] Add a hardened OCI executor that requires a locally installed image by digest, uses isolated client configuration, read-only input/source mounts, non-root identity, disabled network, resource limits and bounded outputs/logs. Never execute custom code through the trusted dispatcher.
- [x] Add bounded AnnData scientific comparisons for IDs, shape, sample retention, QC summaries, counts, metadata, layers and embeddings. Failed required invariants prevent proposal creation.
- [x] Expose the same operations through a Python service and CLI; provide a custom MAD fixture with explicit statistical semantics. Reproduction creates a new run from retained identities.
- [x] Verify unknown custom execution, registered adaptation, artifact tampering, malformed schemas, missing outputs, invariant failures, timeout/error handling, interrupted runs, stale/concurrent approval and replay. Run existing affected tests and the synthetic verification suite.

## Ownership

The delegated worker owns `devharness_core/methods.py`, `devharness_server/method_adapter.py` and `tests/test_method_contracts.py`. The parent owns the method service, executor, scientific comparator, CLI, integration tests and documentation. Existing files are preserved; this checkout has no committed HEAD and contains substantial untracked work, so no checkout/rebase or bulk commit is appropriate.

## Acceptance and scope

Use existing registered operations and an unknown custom QC implementation. CellBender and DoubletFinder are absent from this checkout; no fake adapters or claimed runs will be introduced. Their future wrappers can use the same contract.

Only an explicit approval operation can advance an analysis revision. A successful custom run remains exploratory and its implementation remains unreviewed. Approval records caller-supplied research identity, not an authenticated electronic signature.

Workspace recipes, promotion CI, signed images, shared-service isolation, remote HPC executors, workflow DSLs and a conversational UI are later extensions in the supplied roadmap. No private or real biological dataset execution is needed for this implementation.

## Additional user steering: scientific-method planner

The second supplied design extends this task with an open-world planner above the execution layer. Add separate scientific and capability-resolution passes, machine-checked observed preconditions, immutable trial declarations, comparable input-state and subset-validity checks, runner-derived metrics, structured abstention/recommendations, separate approval scopes and experimental recipes. Preserve unavailable candidates and the original scientific ordering. Add bounded primary-source retrieval with explicit network authorization. Reuse the existing protected Codex selector, method runs, artifact snapshots and proposal approvals. Verify with offline selector/HTTP fixtures and synthetic runs; no live provider or real biological execution is implied.

Implemented and verified, including separate source-draft and trial-design selector phases, inert source registration, source/trial/approval guards and conservative interruption recovery. Final focused release check: 451 passed. Real OCI planner smoke and public-source TLS retrieval passed. The verification report records the broader optional Harmony dependency failures and distinguishes recorded decisions from live provider evaluation.
