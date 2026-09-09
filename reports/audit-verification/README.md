# Task identity and evidence restoration

Version **0.4.15** fixes verified audit defects and records a completed synthetic Colab execution. **Clinical production readiness and regulatory compliance remain unestablished.**

## Corrected failures

| Reproduced behavior | Current behavior and direct check |
|---|---|
| Replacing a task file after constructing the legacy runner archived the replacement while executing and accepting the original; hash verification still passed. | Each run archives and uses one detached canonical normalized specification. Its recorded hash equals the archived snapshot hash. Source replacement/deletion and caller mutations are tested; private snapshots are excluded from participant inputs and restoration. |
| A trusted in-process callback could change the runner's validation mode and remove that run's clinical hold. | Condition, validation mode and nested control evidence are captured at run start. The hold remains in force when caller settings change during execution. This is configuration consistency, not protection against arbitrary malicious Python callbacks. |
| A FIFO could replace a manifest, node or blob after successful verification and block restoration. | The audit CLI uses the shared nonblocking regular-file reader. All three replacement cases now reject explicitly. Ordinary regular-file I/O latency is not bounded by this change. |
| A growing 16-byte artifact wrote 33 bytes before failing; cleanup could remove a competing destination. | Copying stops at the captured size and uses a private temporary payload. Verified content is published without overwriting another writer's destination. The same growth probe wrote at most 16 bytes; an eight-publisher probe produced one success and seven safe refusals. |
| The notebook recorded its uploaded bundle hash without checking every expected source member after execution. | Read-only postflight verification refuses changed/missing source without recreating it. The export retains verified source-member hashes, and source verification is required for the final success flag. |

Independent review caught one introduced packaging defect: the new bundled tests imported the controlled notebook builder, but the allowlist omitted that file. Local editable imports masked the omission. Standalone testing produced **2 failures and 7 passes**. The missing builder was added; isolated imports with `-I -S` and all ten source-verification tests then passed. Original failing probes and review records remain retained.

## Executed verification

- Final fresh Mac suite: **1,973 passed, 2 platform skips**, zero failures/errors. All 233 frozen files remained unchanged during that run.
- Noneditable installed wheel: **64 tests passed**, with 171 member hashes and package-only import origins checked first.
- Notebook delivery: **33 tests passed**. Both five-cell notebooks embed the same 205-member source bundle; their code cells compile. Source-only imports were checked with checkout/site-package fallback disabled.
- Independent implementation review: **36 focused tests and 12 additional probes passed**. Its source hashes stayed unchanged; the later notebook dependency finding was corrected and rechecked independently.

The earlier full runs (1,963 and 1,972 passes) are retained as earlier source checkpoints and are not added to the final test total. The second completed naturally; attempts to locate its child for interruption found no process and sent no signal. Changes were made only after its terminal completion and source comparison.

## Actual local numerical controls and replay

Four trusted programs read real staged synthetic matrices, compute sample-ID-aligned means/design diagnostics, and invoke the numerical dispatcher. **All four passed their unchanged task graders.** Both repeated answer/data pairs matched exactly. These are two questions over existing fixtures, not independent cohorts or live model participants.

| Task | Repeat | Grader | Time (s) | Sampled peak (MB) |
|---|---:|---|---:|---:|
| `trajectory_observed_increasing` | 0 | Pass, 1.0 | 0.076 | 283.00 |
| `adversarial_observed_label_reversal` | 0 | Pass, 1.0 | 0.049 | 284.88 |
| `trajectory_observed_increasing` | 1 | Pass, 1.0 | 0.047 | 285.88 |
| `adversarial_observed_label_reversal` | 1 | Pass, 1.0 | 0.048 | 286.05 |

All four new manifests verified against saved receipt roots. Each restored input set reproduced the original answer, and each captured numerical call replayed successfully through the documented audit CLI. There were four original captured dispatcher calls, four restored-data recalculations and four replayed calls. Ten historical 0.4.14 manifests were also reverified; their answers and original measurements were not changed or regraded.

Time in the table is the runner observation. The complete control/verify/restore/replay command took 11.10 seconds. Memory is sampled runner-plus-recursive-child RSS in decimal MB at 0.1-second intervals; only two samples were available for each short control. It is not an exact allocation peak or a memory-limit guarantee. These timings are not performance comparisons with subprocess controls or other versions.

## Completed Colab execution

The separate [0.4.15 Colab notebook](https://colab.research.google.com/drive/1iz5aVlhdNKxKH9gopxosLm-W6_gpqPO2) ran once. Setup completed in **100.71 seconds**. Its selected suite passed **1,489 tests with one macOS-specific skip** in 205.61 seconds, including all 22 new audit/source cases. Four fixed-plan Gaussian/count analyses completed in **24.17 seconds**, with two exact repeat pairs and R-reference agreement.

All four cloud histories passed semantic verification both in Colab and after download, including eight configured fixture-key signature checks. All **205 expected source members** matched after tests and analysis; 156 additional controlled-runtime source comparisons matched the uploaded bundle. The archive SHA-256 matched the notebook output and all **359 inventoried export files** verified locally.

- Source bundle: `6f3c310e98e0a5b38481d62b75586a9be9fc06d9036115b65065ae5e61bc390c`.
- Downloaded archive: `6a50b70e63c58232165c46acde123fcc075348a6f7f709ee3be3ca28fa800689` (948,146 bytes).
- Observed cloud environment: Python **3.13.15**, Linux **x86_64**, R **4.6.1**. The recorded numerical dependency versions and thread settings are in the [JSON summary](summary.json).

Colab peak memory and billed units are **unknown**; the numerical worker watchdog was 2,048 MiB. R count-regression comparison shares fitted dispersion/offsets, so it does not independently validate empirical-Bayes estimation. The notebook uses project dependency constraints; this is not a claim that every dependency or floating-point environment is identical across hosts. The separately archived 0.4.12 cloud record remains unchanged.

## Evidence and qualification boundary

Participant model identifier, tokens and cost are **not applicable/unmeasured (null)**. This increment made zero participant-model calls; that count excludes coordinator/reviewer conversations. Local dispatcher calls and protocol tests do not demonstrate model-selected MCP usage. The old eight-run Codex pilot remains its original failed performance record.

All 66 existing task definitions and 83 external dependency records remain unchanged. No statistical estimator, calibration result or clinical acceptance margin was adjusted. Count-interval undercoverage and unequal-variance Gaussian miscalibration remain adverse findings. Intended clinical use, assay validation, independently administered human identity and quality approval, qualified deployment and protected retention remain unresolved. No GitHub publication or clinical release occurred.

The capture/copy checks address specific software failure modes relevant to electronic-record controls. They do not establish complete validation, access administration, retention or audit-trail qualification under [21 CFR 11.10](https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11/subpart-B/section-11.10). LatchBio's documented [workflow version identity](https://wiki.latch.bio/workflows/sdk/console/versioning) and [organization access settings](https://wiki.latch.bio/admin/orgs/security) remain reference capabilities, not evidence of this repository's qualification.

Original sources, tests, command argv, failures, actual answers, audit objects and receipts: `results/audit-binding-2026-09-09.1asxvq8k`. Cloud source, upload receipt, downloaded archive and local verification: `results/colab-v0415-2026-09-09.epwwq95_`. These are local retained records; hashes and fixture signatures do not turn owner-writable storage into protected regulated retention. A failed multi-artifact restore may leave already verified members in its fresh output directory. Source postflight checks cover expected bundled members at the check time, not hostile-host isolation or every possible change-and-restore during execution.

See the [requirements map](../../docs/requirements-verification.md) for the full platform scope and remaining gates. The overall clinical-production goal remains active.
