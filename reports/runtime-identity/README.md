# Runtime version identity and replay admission

Version **0.4.16** closes a reproduced dependency-version detection gap. **Clinical production readiness remains unestablished.** This report separates software tests, trusted numerical controls and an actual Colab execution from live model performance and clinical qualification.

## Reproduced defect and correction

The previous benchmark runtime descriptor recorded eight selected packages. A synthetic change to Patsy version metadata left its runtime matcher green. This fault injection changed metadata returned to the probe; it did not upgrade an installed package or change scientific inputs.

The new descriptor keeps those eight compatibility fields and adds a sorted census of distribution name/version metadata visible on Python's search path. The measured Mac environment recorded **78 normalized names**. Names are normalized, identical duplicates collapse, conflicting or malformed identities refuse capture, and discovery refuses a 4,097th yielded entry. The entry cap does not bound metadata lookup latency. Missing/older descriptors do not silently match the richer schema. Historical archive integrity verification remains a separate operation.

Eight formerly omitted design, validation, storage and supervision dependency changes are exercised by replay-admission regressions. The actual captured-history probe additionally changed only Patsy metadata from 1.0.3 to 1.0.3.post1: replay refused the environment before restoring data or executing any replayed tool.

**Scope:** these are version metadata descriptors, not installed-file, already-loaded-module or native-library attestation. Same-version byte changes, native BLAS/OpenSSL builds and a change-and-restore during execution are not detected by this census. Python describes distributions and import packages as different concepts in its [metadata documentation](https://docs.python.org/3.12/library/importlib.metadata.html).

The separately signed controlled-execution path retains its existing 15 selected dependency versions; this increment changes the legacy benchmark capture/replay owner. A controlled historical verifier checks the stored signed identity without requiring the present verifier environment to equal the original execution environment.

## Executed local verification

- Fresh Mac suite: **1,989 passed, 2 platform skips**, zero errors/failures, 459.58 seconds. All 234 frozen source/test/task/protocol files remained unchanged.
- Noneditable installed wheel: **80 passed**, 36.09 seconds. Isolated import origins and wheel member bytes were checked before testing.
- Corrected focused suite: **52 passed**. Independent review: **16 tests and 11 additional probe groups passed**; the three-file review snapshot remained unchanged.
- Old installed 0.4.15 package: **15 failures and one unchanged-runtime pass**, retained as regression evidence.
- Both regenerated notebooks contain the same **206-member** source bundle and five compiled code cells each.

The initial red run (12 failures, one pass) and initial focused run (one failure, 48 passes) remain preserved. The metadata test double lacked `entry_points`, which Pydantic plugin discovery also needs; the fixture correction and original file are retained. Earlier runs are not pooled into final test totals.

## Actual data/tool controls

Four trusted programs read staged synthetic matrices and execute the real numerical dispatcher. All four passed the unchanged task graders. Both repeated task/answer/data pairs matched exactly. These are two questions over existing fixtures, not independent clinical cohorts or live model participants.

| Task | Repeat | Grader | Time (s) | Sampled peak (MB) |
|---|---:|---|---:|---:|
| `trajectory_observed_increasing` | 0 | Pass, 1.0 | 0.122 | 273.86 |
| `adversarial_observed_label_reversal` | 0 | Pass, 1.0 | 0.079 | 275.50 |
| `trajectory_observed_increasing` | 1 | Pass, 1.0 | 0.077 | 277.45 |
| `adversarial_observed_label_reversal` | 1 | Pass, 1.0 | 0.077 | 278.04 |

Each of the four manifests verified against its saved root. Each restored dataset reproduced the answer, and all four captured tool calls replayed successfully through the audit CLI. There were four original captured calls, four restored-data recalculations and four replayed calls. The full control/verify/restore/replay command took 11.16 seconds.

Memory is sampled runner-plus-recursive-child RSS in decimal MB, nominal interval 0.1 seconds, with two successful samples per control. It is not an exact allocation peak or an enforced limit. Control timing is not a model-speed measurement.

## Actual Colab execution

The separately uploaded [0.4.16 notebook](https://colab.research.google.com/drive/1YDZQ5wdXdeTxiP9q_LcbUXGzoDecOx2l) ran once. The selected suite passed **1,505 tests with 1 platform skip** in 211.05 seconds. All 16 runtime-identity regressions and 22 audit/source regressions passed. Four Gaussian/count analyses completed, two repeated numerical pairs matched exactly, and the available R references agreed.

All four histories passed semantic verification in Colab and again locally after download, including eight configured fixture-key signature checks. All **206 expected source members** matched after execution. All **359 export inventory entries** and the downloaded ZIP matched the displayed archive receipt. The observed Python/platform/R and numerical dependency versions are retained in the JSON summary.

- Source bundle SHA-256: `608b683d195a9664b69729d35604cfab7e72c8117439a1e1c0488fe64a65cc38`.
- Downloaded archive SHA-256: `6c6374e906df5995ad36e457d9457d12d6c56e3babc81d9ea15e6f9f84616d10`.

Colab peak memory and billed units are **unknown**; 2,048 MiB is a numerical-worker watchdog limit, not a measured peak. The R count comparison shares fitted dispersion/offsets and therefore does not independently qualify empirical-Bayes estimation. Colab dependency resolution uses project constraints, not a claim of identical dependencies or floating-point behavior across hosts. The native Docker path did not run in Colab. No runtime was deleted/reset by this task.

## Remaining qualification work

All 66 task definitions, 83 external lock records, statistical estimators, grading thresholds and historical answers remain unchanged. No participant-model calls were made in this increment; model identifier, participant tokens and cost are null/unmeasured. Coordinator/reviewer usage is separate. Dispatcher and local protocol activity do not demonstrate model-selected MCP usage. The original eight-run Codex pilot remains its failed performance record.

Observed count-interval undercoverage and unequal-variance Gaussian miscalibration remain adverse findings. Intended clinical use, representative assay validation, administered human identity, independent quality approval, qualified deployment/recovery and protected retention remain unresolved. A metadata census or locally retained hash does not establish [21 CFR 11.10 controls](https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11/subpart-B/section-11.10). LatchBio's [workflow versioning](https://wiki.latch.bio/workflows/sdk/console/versioning) and [organization security](https://wiki.latch.bio/admin/orgs/security) are platform references, not this project's qualification evidence.

Original argv/logs, failed probes, source snapshots, actual answers, receipts, and verification scripts are retained in `results/runtime-identity-2026-09-09.oqm1j9qq`. The separate upload/download and cloud checks are in `results/colab-v0416-2026-09-09.1oomsf4p`. These owner-writable local stores are not qualified regulated retention. No GitHub publication, hosted CI or clinical release is claimed. See the [requirements map](../../docs/requirements-verification.md).
