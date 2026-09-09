# Observed-temporal task verification

Version **0.4.14** connects the existing strict temporal grader to five shipped tasks. Each checks actual sample-ID-aligned raw-count means, the numerical design condition number, the signed-step score and the reported classification. Transient and flat profiles are negative controls for strict monotonicity; a label-reversal case checks supplied observations with shuffled metadata.

The five questions reuse **two seeded fixture variants**. All 61 earlier task definitions, the original generator, the 55-scenario builder, curated twelve-task selection and 83 external dependency records remain unchanged. The new total is **66 definitions**, not 66 independent cohorts.

## Executed software controls

**10/10 data-reading subprocess runs passed (100%), with five exact answer/data repeat pairs.** The solver uses NumPy, pandas, Patsy and Zarr, receives staged data and the answer schema, and never receives private references or grader source. Its code and inputs were frozen before measurement. These are trusted local numerical controls, not model participants or a security-sandbox test. No answers were repaired and no thresholds were tuned after execution.

| Task ID | Analytical track | Condition | Pass/fail | Time (s), two runs | Peak memory (MB), max | Grader reasoning |
|---|---|---|---|---|---|---|
| `adversarial_observed_label_reversal` | adversarial | Baseline software control | 2/2 pass | 0.599, 0.421 | 365.51 | All observed-value and classification gates pass |
| `adversarial_observed_plateau` | adversarial | Baseline software control | 2/2 pass | 0.478, 0.426 | 364.84 | All observed-value and classification gates pass |
| `adversarial_observed_transient` | adversarial | Baseline software control | 2/2 pass | 0.483, 0.473 | 366.64 | All observed-value and classification gates pass |
| `trajectory_observed_decreasing` | trajectory | Baseline software control | 2/2 pass | 0.466, 0.477 | 368.79 | All observed-value and classification gates pass |
| `trajectory_observed_increasing` | trajectory | Baseline software control | 2/2 pass | 0.427, 0.481 | 367.67 | All observed-value and classification gates pass |

Time is the runner's observed elapsed time; setup, audit verification and the outer driver are additional. Memory is sampled runner-plus-recursive-child RSS in decimal MB at 0.1-second intervals; it is not an exact allocator maximum or a memory-limit guarantee. The [JSON summary](summary.json) retains each observation and its measurement scope. All ten solver processes exited successfully before the 60-second watchdog.

The full fresh Mac suite passed **1,951 tests with 2 platform skips**, with zero failures/errors. A separate installed-wheel check passed **26 tests**. The independent review passed 46 focused tests and rejected all **55 schema-valid corruptions**, 15 nonfinite answers and 14 invalid field-path configurations. The initial failing development tests and interrupted reviewer attempt are retained in the source archive.

## Audit, replay and failures

All **10 audit manifests** verified against separately saved root receipts. The documented replay CLI was invoked for each and correctly returned **unavailable: no captured numerical tool events**. No MCP tools were called. The five repeated program executions are separate numerical-repeat evidence and are not relabelled as successful tool-history replay.

No data-reading run failed. The adversarial verification covered altered stage values, a value shift that preserves direction, wrong score/direction/flag, wrong condition number and a false validation flag. Those deliberately corrupt fixtures failed as required. Offline setup initially selected Python 3.14 and could not find its SciPy wheel; that attempt is preserved. Setup then completed with the existing Mac Python 3.12.1 dependency set. No network or model fallback was used.

The initial installed-package verifier mistakenly expected six cached public-dataset metadata files inside the wheel. Its first correction preparation also stopped on an incorrect expected count, followed by a failed invocation of the not-yet-created helper. All attempts are retained. The corrected inventory verified 85 declared package members and package-only import origins before the 26 installed tests ran. These were verifier/setup failures before testing, not erased passing runs or changes to benchmark answers.

## Qualification boundary

Actual participant model identifier, token use and service cost are **not applicable/unmeasured (null)**: these controls made zero participant-model calls. This count excludes the coordinator conversation and supplies no new live-agent score. The original eight-run Codex pilot and its failures remain unchanged. The independently archived 0.4.12 Colab execution predates this source and does not prove these additions on Linux.

Raw-count stage summaries are descriptive checks, not normalized differential expression, biological chronology or biomarker validation. Previously observed count-interval undercoverage and unequal-variance Gaussian miscalibration remain unresolved. Clinical context of use, assay evidence, administered identity, independent quality approval and qualified retention/operations remain open. **Clinical admission and release remain held; regulatory compliance is not established.**

The [requirements map](../../docs/requirements-verification.md) distinguishes implementation from qualification. Full source snapshots, command records, original answers, audit objects and saved receipts are retained in `results/temporal-track-2026-09-09.k8eqb_cb`. The public summary contains derived software observations; it excludes private reference answers, credentials and clinical data. No GitHub push or hosted CI execution is claimed.
