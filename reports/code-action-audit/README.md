# Primary execution audit and failure preservation

Version **0.4.17** integrates the primary Code-as-Action runner with the existing execution DAG. **Clinical production remains unqualified.** The evidence below demonstrates specific software behavior using synthetic inputs; no live model benchmark or clinical assay validation was performed.

## Implemented behavior

The primary runner previously retained flat output files and hashes. It now privately captures original staged task inputs and SDK source files before invoking a participant, records the captured references in the DAG, and binds retained observations and the computed outcome to a saved root receipt. The per-run task specification and condition are detached before execution. Flat exports remain available under the new `devbench.code-action/2` record format; historical version-1 records were not rewritten.

Private task/grader snapshots stay outside the participant workspace and outside the restoreable task-input references. SDK source is recorded separately and is not restored as an executable environment. Input restoration copies verified bytes and never executes `solve.py`. Participant-written SDK traces and adapter-reported CLI events remain observations; they are never promoted to trusted numerical tool nodes. The numerical audit CLI explicitly refuses replay for this primary path, with zero replayed tools.

Private input/source copying has a cumulative **100 MB payload and 50,000-member limit**. The shared DAG ingestion API now accepts optional cumulative byte/member bounds; this caller supplies the captured sizes at the second-copy boundary. Existing default callers retain their prior identities and behavior. Payload limits exclude separately bounded JSON descriptors. These limits do not bound initial input-hash traversal or ordinary filesystem latency, and they do not establish hostile-process isolation.

Ledger initialization failure prevents participant execution. Capture failures prevent execution and retain a failed history when ledger storage remains usable. A finalization failure cannot return a passing accepted run or a successful receipt. Original execution error text/category and subsequent persistence errors remain distinguishable. Failures before complete staging need not have restoreable inputs; an unfinalized ledger is not a successful audit record.

## Independent findings and verified corrections

Independent review reproduced defects despite the first focused tests passing:

1. A failure writing CLI observations replaced the original adapter error. The original cause now survives with a separate persistence-error record.
2. A private snapshot grew from 8 to 72 bytes under a 16-byte capture limit; uncapped ingestion created a 72-byte CAS blob before rejecting the mismatch. The corrected bounded ingestion refuses it before creating that blob.
3. A later double-persistence failure preserved text but relabelled the saved original category. The final correction preserves the adapter/environment category through both failures.

Adjacent output-boundary probes also reproduced oversized retained-file ingestion and a changed outcome snapshot. Retained outputs now use their recorded size limits; the captured outcome digest must equal the canonical computed result. The independent rechecks retained their original failed probes and confirmed the corrections. Their review is scoped automated evidence, not independently administered clinical quality approval.

## Executed verification

- Final fresh Mac suite: **2,014 passed, 2 platform skips**, zero failures/errors, 467.63 seconds in JUnit.
- Final noneditable installed wheel: **126 passed**, 25.57 seconds in JUnit. Application module origins and all installed wheel members except the installer-rewritten RECORD were checked first. Only copied test fixtures were added to the test import path.
- Final focused error/boundary file: **10 passed**, including both chained-failure cases. The preceding broader focused suite passed 124 tests. All independent final snapshot hashes matched.
- Both notebooks contain the same **210-member** source bundle and five compiled code cells each. The final measurement inventory contains 240 source/test/task/protocol members.

Earlier records are retained and not pooled: the original primary-audit red suite had 11 failures; initial integration had 57 passes and two old-interface expectation failures; corrected focus had 63 passes. Six review regressions failed before correction. One subsequent assertion expected a later mismatch message but the tighter size check rejected earlier (85 passed, one failed); the assertion was updated to recognize either explicit rejection. Both output-boundary probes failed before their corrections. The preceding full source passed 2,012 tests with two skips, but independent review still found the chained-category issue. Its red probe had one failure and one unchanged environment-case pass; the final ten-case file passes.

The first controlled-notebook build command omitted its required output argument and exited before building. The explicit-output invocation succeeded. The original build log is retained. An initial test expectation used the wrong restore-result key; it was corrected to the documented `restored` field before green verification. No scientific answer or grading threshold was edited to obtain a pass.

## Actual script controls

Two existing observed-temporal tasks ran under both baseline and assisted staging. Two additional scripts were prespecified to exit with status 7 after writing their answer. All six cases met their expected outcomes: **four accepted research grades and two retained script failures**. This is not a model pass-rate comparison.

| Task | Condition | Control | Observed result | Run phase (s) | Audit finalize (s) | Sampled peak (MB) |
|---|---|---|---|---:|---:|---:|
| `trajectory_observed_increasing` | baseline | Expected success | Pass, 1.0 | 1.001 | 0.011 | 318.80 |
| `adversarial_observed_label_reversal` | baseline | Expected success | Pass, 1.0 | 0.983 | 0.012 | 332.56 |
| `trajectory_observed_increasing` | assisted | Expected success | Pass, 1.0 | 2.030 | 0.013 | 427.03 |
| `adversarial_observed_label_reversal` | assisted | Expected success | Pass, 1.0 | 2.015 | 0.014 | 429.56 |
| `trajectory_observed_increasing` | baseline | Expected script failure | Fail retained | 0.982 | 0.011 | 336.89 |
| `trajectory_observed_increasing` | assisted | Expected script failure | Fail retained | 2.014 | 0.014 | 434.65 |

All six final-source receipts verified against saved roots, and restored public data reproduced all six original answers. The two failed scripts remain unaccepted. Six numerical replay requests correctly reported this path ineligible and executed zero tools/scripts. The separate restored-data recalculations were explicit trusted control commands, not audit-CLI replay and not model inference.

Two earlier six-case cycles remain under their original source snapshots. Across the three cycles, 18 actual scripts and 18 explicit restored-data recalculations ran; each cycle keeps its own six receipts and outcomes. Repeated fixtures and conditions are not independent cohorts. Timing differs with process/cache/system conditions and is not evidence of a performance improvement.

Run-phase timing includes staging, execution and flat preservation, but excludes DAG finalization and `run.json` publication. DAG finalization has its own elapsed field. Sampled RSS covers the runner and recursive children before DAG finalization, in decimal MB at nominal 0.1-second intervals; it is not an exact allocation peak or an enforced limit. Full driver elapsed time and raw samples remain in the original command/run records.

## Actual Colab execution

The separate [0.4.17 notebook](https://colab.research.google.com/drive/1Swo-3rkG1g2yb7s2L39560_UjOhGuSs5) ran once. Its selected suite passed **1,542 tests with 1 platform skip**, including all **37 primary audit/integrity regression cases**. Four fixed-plan Gaussian/count analyses completed, with two exact repeated numerical pairs and agreement with the available R references. These four analyses use the separate controlled numerical workflow, not the primary script-control runner.

All four cloud histories passed semantic verification in Colab and after download, including eight configured fixture-key signature checks. All **210 expected source members** matched after execution, and all **359 exported inventory entries** matched the downloaded archive. The ZIP matched the browser-displayed receipt:

`b2c18ee5f37b14a442a8c0b6930254ec76f5dcfd5964ad79f5c8b9d127addbb0`

The uploaded source bundle is `bcfb5d1e5c7f11aa47ded9098129dbccaae4c8c01908bf702b9ebf651095497d`. Observed environment/version records and numerical comparisons are retained in the JSON summary. Colab peak memory and billed units are **unknown**; 2,048 MiB is a numerical-worker watchdog limit, not a measured peak. R count-regression comparison shares fitted dispersion/offsets and therefore does not independently validate empirical-Bayes estimation. No native Docker or live model benchmark ran in Colab.

## Platform and qualification boundary

LatchBio documents workflow version strings tied to directory content and optional Git state, and separately administered organization login-provider/domain controls.[^1][^2] The new primary ledger improves this project's execution-record coverage. It does not supply a governed Git release, deployed identity provider, qualified operator or protected retention service.

FDA describes intended-use/risk-based validation alongside access, record and signature controls; biomarker qualification requires a defined context of use and reliable measurement.[^3][^4] Locally retained hashes and fixture keys do not replace those organizational and scientific requirements. Current direct automated eCFR access returned a CAPTCHA page; it was not bypassed or used as new regulatory evidence.

All 66 task definitions, all 83 external dependency lock records, numerical estimators, grading thresholds and historical benchmark answers remain unchanged. The original failed eight-run Codex pilot is unchanged. Participant model identifiers, tokens and costs for this increment are null/unmeasured; there were zero participant-model service calls. Coordinator/reviewer usage is separate.

Count-interval undercoverage, unequal-variance Gaussian miscalibration, approved assay/context, representative clinical validation, administered human identity and independent quality approval, qualified deployment/recovery and protected retention remain unresolved. The overall clinical-production goal is still active. No GitHub publication, hosted CI, runtime deletion or clinical release is claimed.

Original code, commands, failure probes, measurements and receipts: `results/code-action-audit-2026-09-09.mcawtee8`. Separate cloud upload/download and verification: `results/colab-v0417-2026-09-09.2h3zey28`. These owner-writable local stores are not qualified regulated retention. See the [requirements map](../../docs/requirements-verification.md).

## Sources

[^1]: LatchBio. [Versioning](https://wiki.latch.bio/workflows/sdk/console/versioning). Workflow version and Git identity documentation.
[^2]: LatchBio. [Security Settings](https://wiki.latch.bio/admin/orgs/security). Organization authentication/provider/domain controls.
[^3]: FDA. [Electronic Systems, Electronic Records, and Electronic Signatures in Clinical Investigations: Questions and Answers](https://www.fda.gov/media/166215/download). October 2024, Revision 1.
[^4]: FDA. [Qualifying a Biomarker through the Biomarker Qualification Program](https://www.fda.gov/drugs/biomarker-qualification-program/qualifying-biomarker-through-biomarker-qualification-program). Context-of-use and measurement qualification.
