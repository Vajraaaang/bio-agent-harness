# Three-condition Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Preserve the no-data-execution restriction and individual file ownership.

**Goal:** Implement the approved three-condition study, evidence enforcement, improved data contracts and generators, biological metrics, and a dashboard without executing benchmark data or participants.

**Architecture:** Add a versioned `devbench.study` package alongside historical runners. Separate pure configuration/record validation, explicit data preparation, coordinator tool execution, participant orchestration, scientific scoring, and result rendering. Preparation defaults to planned artifacts; every data/model execution requires an explicit command.

**Tech Stack:** Python, existing Pydantic/jsonschema/NumPy/SciPy dependencies, Codex CLI, ToolDispatcher, standard-library local IPC, and Matplotlib for optional offline statistical figures.

## Task 1: Study contracts and offline planning

Files: create `devbench/study/__init__.py`, `contracts.py`, `planning.py`, `cli.py`; add focused contract tests. Add CLI entry point only after the no-execution default is verified.

- [ ] Define explicit baseline/optional/enforced conditions; preserve legacy assisted semantics.
- [ ] Define versioned study, dataset, acceptance-policy, attempt, provenance and evidence-scope records. Missing empirical results remain unavailable, never zero-success substitutes.
- [ ] Make plan construction deterministic, balanced/interleaved, independent-cohort aware and model/budget matched. Primary comparison is enforced versus baseline; others are exploratory.
- [ ] Write contract tests before implementation. A representative required behavior is `assert plan.execution_status == "not_run"` and `assert set(plan.conditions) == {"baseline", "optional", "enforced"}`. Invalid duplicate conditions, zero/negative budgets and private input classifications must reject.
- [ ] Prepare plan JSON and an empty dashboard without importing data generators, launching subprocess participants, or opening expression matrices.

## Task 2: Data recipes and public contracts

Files: create `devbench/study/data.py`, `public_sources.json`, data contract tests; retain all historical generators and task definitions.

- [ ] Implement six documented scenarios and two distinct generating mechanisms with private random streams, opaque identifiers, declared biological units and estimands.
- [ ] Separate public input bundles from private truth and require explicit generation entry points. No generation on import, planning or reporting.
- [ ] Implement bounded ingestion/verification contracts for already selected public subsets, with no synthetic fallback. Record measured layer, source hash, independent-unit evidence and unavailable truth.
- [ ] Test recipe validation, scope separation, identity consistency, and size caps when fixture execution is authorized. Until then, write tests and perform static inspection only.

## Task 3: Trusted broker and study execution

Files: create `devbench/study/broker.py`, `client.py`, `execution.py`, `permissions.py`, broker/launch contract tests. Reuse existing numerical tools and CodexCLIAdapter; do not expose coordinator packages to participants.

- [ ] Execute tools in coordinator-owned state and bind real call outcomes to input/model/formula identities.
- [ ] Optional and enforced arms share the tool client. Optional prompts say tools may be used; enforced acceptance requires applicable recorded checks and claim-to-result bindings.
- [ ] Use exact default-deny filesystem profiles and per-run IPC allowlists; fail closed on unsupported permissions/preflight rather than weakening isolation.
- [ ] Count all attempts, retain errors, preserve original artifacts and separate scientific scoring from compliance.
- [ ] Test forged/absent receipts, changed inputs, mismatched design, unsupported claims, out-of-order fits, tool failure and missing isolation. Do not launch Codex participants during implementation.

## Task 4: Statistical scoring and paired analysis

Files: create `devbench/study/scoring.py`, `metrics.py` and metric tests; keep historical report semantics intact.

- [ ] Use fixed hypothesis universes and eligibility, signed discovery recovery, actual FDP/FDR, shape/error/coverage metrics and null/diagnostic cases.
- [ ] Preserve valid missing statistics with reasons. Do not treat failed or filtered values as fabricated p=1 or q=0.
- [ ] Aggregate expected single-run outcomes by independent cohort and prespecified strata; distinguish missing counterparts, invalid provenance and failed executions.
- [ ] Write regression tests for repeated-question pseudoreplication, unequal repetition counts, zero completions, nonfinite inputs, false refusals and orphaned output fields. Compute intervals only with adequate independent units and label exploratory scope.

## Task 5: Dashboard and export

Files: create `devbench/study/dashboard.py`, report assets/tests, and an empty prepared report under `reports/benchmark-redesign/`.

- [ ] Render condition/source/scenario filters and metric panels with standard plotting tools when measured records exist.
- [ ] Render planned, blocked, unavailable and not-run states without fabricated points. Every graph has units, denominators, interval method and exportable source rows.
- [ ] Include actual adoption versus compliance, all-attempt costs, statistical calibration and separate public-data metrics.
- [ ] Inspect the empty dashboard visually; data-bearing screenshot/metric fixtures require the pending test-scope clarification.

## Task 6: Scientific review, integration and verification

Files: `reports/benchmark-redesign/research-review.md`, `requirements-verification.json`, README/CLI documentation, and focused fixes to affected statistical interfaces if the review identifies supported defects.

- [ ] Check primary sources for raw-count requirements, replication, normalization, selection inference, multiple testing, simulator limits and paired benchmarking.
- [ ] Pin sources and document accepted assumptions, rejected claims, limitations and deferred empirical work. Never describe literature support as an empirical validation of this implementation.
- [ ] Review spec compliance before code quality and preserve review findings and remedies.
- [ ] Run only checks allowed by the user; never run dataset generation/download, scientific calibration or live benchmarks in this goal.
- [ ] Verify package inclusion/CLI help, source preservation, no-data defaults, empty dashboard and all authorized regression checks before marking the goal complete.

## Current evidence

- Source-only checkpoint: `results/benchmark-redesign-2026-09-11/source-before.json` (138 files; no datasets copied or executed).
- Local branch: `vajraang/benchmark-three-arm-study`; the original checkout had no commits, so an ordinary worktree could not be created without manufacturing a baseline commit.
- Awaiting clarification: whether tiny in-memory software-test fixtures may execute. No such fixtures or benchmark data have been run.
