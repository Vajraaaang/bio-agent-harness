# Temporal evidence validity implementation plan

**Goal:** Prevent missing transitions and malformed estimates from receiving successful temporal evidence scores.

**Architecture:** Keep the current signed-step score and exact expected-score comparison for valid data. Require at least two distinct finite parsed timepoints with finite numeric estimates in the public metric; reject malformed input deliberately. Convert only expected input-validation errors into failed grader results. A single-stage compactor remains useful for descriptive summaries but reports an undefined temporal score explicitly.

**Scope:** `devharness_server/tools_trajectory.py`, `devbench_engine/graders_math.py`, the single-stage metric in `devharness_core/compactor.py`, focused tests, version/package/notebook metadata and the current requirements map. Existing task definitions, grading thresholds, model fitting, historical answers and sealed results remain unchanged. No shipped task currently configures this dedicated temporal grader, so a corrected helper is not evidence of improved shipped benchmark scores.

**Baseline evidence:** `results/trajectory-invariant-2026-09-08/before-reproduction.json` records actual empty, singleton, numeric-string and boolean inputs passing the public metric and a configured single-gate composite. The minimum timepoint requirement follows the requested signed-step formula whose denominator is k−1. It does not require arbitrary extra samples or establish clinical trajectory validity.

1. Add red tests for empty/singleton data, malformed mappings, nonnumeric/boolean/nonfinite estimates and numeric overflow. Add valid unsorted, increasing, decreasing, tied and fractional-score cases.
2. Require the public metric to receive a mapping with at least two distinct parsed timepoints and finite real estimates. Advertise the minimum through its MCP JSON schema. Invalid data raises a descriptive `ValueError`; the grader catches expected input errors and returns `(False, 0.0, diagnostic)` rather than treating zero as a measured score.
3. Replace the compactor's singleton score of 1 with JSON null and `temporal_monotonicity_status: insufficient_timepoints`. Keep valid multi-stage summary bytes/results unchanged apart from package metadata.
4. Check direct/composite grading and real MCP refusal plus subsequent successful calls. Update the old singleton-success assertions. Run the full synthetic suite in the previously verified dependency environment after the focused checks pass.
5. Independently review the final change. Build version 0.4.10 and verify package/notebook source parity, preserving 0.4.9 artifacts and all original failed records. Do not rerun native coverage studies or infer a clinical calibration improvement from this change.
6. Publish an updated requirement-to-evidence map with explicit implemented, partial, absent and contradicted statuses. Clinical assay/context, deployment ownership and independent quality approval remain unanswered; clinical admission/release stays disabled.

The preceding turn was progress: a dependency revision, full clean tests, package proof and sealed failure evidence. This increment addresses a reproduced validity defect, not an acceptance threshold selected to make earlier results pass.

## Completed scope and evidence

The compactor change also preserves distinct numeric time identities when the short display format would merge them; adjacent positive/negative float probes verify the fallback. The full suite passed1,734tests with all152 frozen files unchanged. Notebook-only delivery additions then passed23 focused tests. Source/package/notebook byte comparisons passed. The initial packaged probe failed before discovery with incomplete nested diagnostics; a same-limit unchanged-wheel repeat passed six real calls. Both are retained; initialization reliability is not qualified. Clinical requirements and current benchmark-gate coverage are mapped explicitly. Final publication/verification is recorded by the result directory checkpoint.
