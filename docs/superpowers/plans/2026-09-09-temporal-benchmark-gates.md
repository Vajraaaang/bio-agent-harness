# Temporal Benchmark Gates Implementation Plan

> For agentic workers: execute the bounded steps below, then use independent code/spec review. The coordinator owns implementation, integration and final evidence. Preserve earlier measured tasks and result archives.

**Goal:** Make the shipped benchmark exercise strict temporal monotonicity using numerically checked observations from staged synthetic data.

**Architecture:** Add five definitions using an observed-temporal builder that wraps the existing seeded synthetic generator. Compute coordinator-only references from the staged matrix with sample-ID alignment. Keep every old definition and generator unchanged. Extend grader field lookup to accept an explicit string-component list for keys containing dots; legacy dotted strings retain their meaning.

**Tech stack:** Existing NumPy/pandas/Patsy/Zarr fixtures, JSON Schema, deterministic all-of graders, supervised evaluation and existing audit records. No LLM judge, new biological estimator, clinical data or new clinical acceptance threshold.

## Design decisions

- Profiles: Increasing, Decreasing, Transient, Invariant, and Increasing under the existing inverted-label/shuffled-metadata variant. The last asks for the observed grouping as supplied, not recovery of true biological chronology.
- Five analytic questions reuse two seeded datasets; do not call them five independent cohorts or new biological mechanisms.
- Require the four actual stage means, computed design condition number and validation flag, signed-step score, strict-monotonic flag, direction and an explicit descriptive evidence scope.
- Use separate numeric gates on each mean and on the reported score, plus the dedicated temporal grader on the same means. Thus a correct direction cannot conceal invented values and correct values cannot conceal a contradictory interpretation.
- Use an absolute 1e-6 numerical tolerance for stage means and condition number, and exact score/direction/flag agreements. These are software comparison tolerances, not clinical acceptance limits.
- Keep the existing 61 definitions, 55-scenario matrix builder, curated twelve-task selection and historical measurements unchanged. The five new definitions become available through expanded-suite discovery and explicit task paths.
- New field paths may be nonempty JSON lists of nonempty string keys. An existing dotted string still splits on dots. Invalid path configuration is an error, not a missing-answer success.
- The builder returns private reference truth to the coordinator and stages no grader/expected-value files. Public task inputs contain only the target gene, columns, formula and requested descriptive operation.

## Implementation and evidence

- [x] Add meaningful failing tests for explicit field paths, real task coverage, metadata row alignment, private-reference separation, expected profile behavior and invalid/contradictory answers.
- [x] Implement structured field lookup in devbench_engine/runner.py without changing existing scalar/math/biological grading rules.
- [x] Implement benchmarks/temporal.py with definition generation and bounded staging/reference derivation; add its staging route in EvalRunner.
- [x] Generate five new JSON definitions and prove all existing 61 files remain byte-identical.
- [x] Run a plain-data solver in separate Python processes against staged inputs and grade the actual outputs. Give it no private truth or grader source; label it a deterministic software control, not live model performance.
- [x] Obtain independent review of task semantics, source-value binding, privacy and failure behavior; resolve verified findings.
- [x] Freeze source/tasks/protocol before measurement. Retain failed runs; never repair answers or tune tolerances after observing scores.
- [x] Run the appropriate full suite and installed-package checks, rebuild both notebooks, and verify new audit records against retained receipts.
- [x] Update the requirement map and source-grounded readiness audit, including the now-observed Colab result and still-unmet clinical/operational gates.
- [ ] Seal the new checkpoint, keeping definition coverage, deterministic controls, live agent performance and clinical qualification separate.

No Git HEAD or remote exists, so source copies and hashes identify this work. Baseline files are retained in results/temporal-track-2026-09-09.k8eqb_cb/. The existing Colab runtime-reset approval remains pending and is outside these code changes.
