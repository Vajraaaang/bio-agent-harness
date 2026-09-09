# Code-as-Action Audit Implementation Plan

**Goal:** Give the primary CodeActionRunner durable captured inputs, runtime sources, execution observations, failures and outcomes in the existing execution DAG.

**Architecture:** Reuse the existing CAS/DAG rather than create a second ledger. Preserve compatible flat exports and add a saved audit receipt to the returned record. Bind the per-run condition and specification before staging. Capture original inputs and SDK files before any participant invocation under cumulative byte/member limits; record preserved copies and their hashes, without claiming that before/after checks prove hostile-process isolation. Keep the private task/grader snapshot outside the participant workspace and outside restoreable input references.

**Tech stack:** Existing Python runner, ExecutionDAG, bounded copy_artifact, deterministic JSON and audit CLI. No added dependencies or statistical changes.

The user already requested immutable execution-DAG provenance across this platform; this integration implements that scope. There is no Git HEAD/worktree base. Original 0.4.16 measured inputs and packages remain preserved. No model participant is needed for implementation verification; any local adapters or fake-CLI tests are software fixtures and must be labelled as such.

1. Reproduce the missing primary audit/restoration evidence with failing tests.
2. Add a focused `devbench/evidence.py` helper for bounded private input/source capture and final outcome/observation records. The cumulative original-input/source limit is 100 MB and 50,000 members; changed-output preservation retains its existing 8 MB bound. Do not turn artifact limits into numerical acceptance thresholds.
3. Integrate runner startup, immutable condition/specification, capture-before-execute, failure-safe finalization and explicit clinical holds. Persist audit failure separately from the original execution error. A failing or unavailable ledger cannot yield a passing run.
4. Retain participant claims under observation artifacts, never trusted numerical tool nodes. The audit CLI may restore complete captured task inputs; it must explicitly refuse numerical replay for this path because supervised tool history is unavailable. Do not execute solve.py during restore/replay.
5. Test success, adapter failure, source/input mutation, private golden exclusion, ledger/copy/finalization failure, condition mutation, restore/tamper behavior and untrusted trace injection. Retain old flat records as historical v1; new records use v2.
6. Independently review the changed code, run appropriate focused and full suites, verify an isolated installed wheel, and execute actual data-reading script controls in a new measurement directory.
7. Update the requirements and package/notebook delivery records using actual results. Preserve earlier Colab versions. Seal the new evidence against separate roots; retain clinical qualification gaps.

**Acceptance boundary:** Tests and captured scripts cannot establish live model improvement, complete operating-system isolation, protected retention, authentic human identity, a calibrated clinical assay or regulatory compliance. The separate controlled/native paths retain their existing owners and qualification holds.
