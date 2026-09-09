# Audit Task Binding and Restoration Implementation Plan

**Goal:** Bind legacy evaluation evidence to the effective task actually used and reject unsafe or oversized restored evidence before it can hang or exceed its declared byte budget.

**Evidence:** `results/audit-binding-2026-09-09.1asxvq8k/baseline-probe-results.json` reproduces three defects on unchanged 0.4.14 source. A task-file replacement between construction and execution archives the replacement while executing and accepting the original; integrity verification still passes. A 16-byte restore writes 33 bytes after the source grows, then rejects/removes the destination. The audit CLI's separate opener blocks on a FIFO after imports and requires the probe's two-second watchdog to kill it. These are synthetic fault probes, not live model evaluations or clinical evidence.

**Architecture:** Keep legacy public staging compatibility while freezing a detached canonical task dictionary for each run. Archive that exact normalized snapshot outside the participant workspace and use it throughout staging, policy, instruction, schema and grading. Retain explicit hash scope and source-path provenance. Restore/replay should use the shared nonblocking regular-file opener and enforce the already declared artifact size during streaming, including growth after preflight. Existing artifact encodings, task definitions, scientific procedures and grading thresholds remain unchanged.

**Ownership:** The coordinator owns runner/audit CLI changes and regression tests. Use bounded independent review of the frozen implementation after failing cases are reproduced. No historical archives, private clinical data, external model backend or cloud runtime deletion is part of this change.

- [x] Preserve current source and all 66 definitions before testing.
- [x] Execute and retain bounded reproductions of task misbinding, copy growth and FIFO blocking.
- [ ] Add meaningful failing tests for replacement/deletion of task files, effective-spec mutation during an adapter call, private snapshot separation, restore growth and post-verification FIFO replacement.
- [ ] Freeze and archive the normalized effective task per run. Preserve `_stage(work)` for existing callers; pass the detached spec explicitly from the legacy run path.
- [ ] Share the existing regular-file opener, preserving audit errors, and bound descriptor reads/writes by the captured expected size. Remove only this invocation's partial destination on error.
- [ ] Run focused tests, independent source review and actual numerical-tool capture/restore/replay with new saved roots. Reverify historical integrity without relabelling code-mismatched replay as successful.
- [ ] Run the full suite and installed-package checks, rebuild both notebooks and verify their exact source bundles.
- [ ] Execute the qualified software candidate in a separate Colab notebook after local verification, using only synthetic inputs and retaining its source identity and original failures.
- [ ] Publish local evidence documentation with clear software, model-performance and clinical boundaries, then seal the new checkpoint and verify it against separately saved anchors.

The user reconnected the existing Colab notebook. Coordinator UI inspection confirms a Python 3 backend connection and retained 0.4.12 outputs; it does not verify current VM files, variables or background processes. No cells were rerun or runtime state deleted. The next cloud measurement must be a separately identified candidate, preserving the historical notebook and sealed archive. Clinical assay/context, calibration, administered identity, independent quality and operational qualification remain unresolved; the full goal is still active.
