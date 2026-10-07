# Persisted clarification handoff

**Goal:** Let a question-driven RNA-seq episode survive CLI exit without losing prior evidence, resetting its budgets or changing its input snapshot.

**Code-grounded constraints:** `ExecutionDAG` currently finalizes only once and rejects additional node files when verifying a receipt. `ToolBroker` also closes once: its exclusive `root.json` cannot be overwritten. The episode instance identity binds a frozen evaluation registration. Serializing a Python object or reopening either closed store is therefore unsuitable.

**Architecture:** Preserve an independently verifiable copy of an in-progress DAG, suspend its original writer, and reopen only its verified working prefix through a single-use claim. The same ledger identity continues. Restore scientific broker state only from verified receipts into a fresh broker evidence generation; use its normal constructor for runtime objects. Prior numerical outputs may be reused only after receipt/input/source checks. Do not serialize executable objects or expose evaluator data.

**Authorization and budgets:** This work permits local implementation and small fixtures only. Data/model execution remains separately authorized on each launch. Preserve the existing wall-clock deadline: waiting does not silently restart or extend it. Persist wall timestamps plus remaining allowances, never reuse an absolute `time.monotonic()` value across processes. Resource, scientific and grading settings stay frozen. Changes to experimental metadata require a new reviewed input snapshot, not a clarification note.

## Bounded implementation steps

1. Add `ExecutionDAG.checkpoint` and `ExecutionDAG.resume` using the existing bounded artifact copier, canonical JSON and verifier. Checkpoints remain valid after further work. Resume verifies a matching working prefix, reserves one claim before returning a writer and retains claim evidence. Validate tampering, concurrent/double resume, copy limits and finalization guards.
2. Add explicit episode handoff state and broker restoration, using `verify_receipts` and the existing broker constructor. Preserve failures, elapsed allowances, actions, diagnostics, usage and evaluation registration. Reconstruct locks/dispatchers locally. Reject unsupported scoped receipts rather than invent execution-window state.
3. Add CLI continuation with a retained root and operator response, without allowing replacement specs or relaxed limits on that path. Save a new pending checkpoint on subsequent questions. Verify a successful continuation, unsupported metadata-change request, expired budget, interruption and tampered state using tiny fixtures.

The current checkpoint increment does not by itself complete the user-facing continuation flow. It is a prerequisite, not a replacement for that flow. Clinical readiness, live containment and scientific qualification remain outside its evidence scope.
