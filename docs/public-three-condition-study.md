# Public reference track

The public track uses baseline, optional and enforced conditions with separate plans, answers, grading and reports. Local software fixtures exercise it; no public-cohort statistical analysis or live public-agent comparison has been run for this revision.

| Source | Input | Available reference | Limits |
| --- | --- | --- | --- |
| E-MTAB-6798 general brain subset | 96 genes × 35 processed RPKM columns | Observed-library means by collection stage | Animal identity unverified; no population inference or universal pseudotime truth |
| E-MTAB-6798 marker subset | 96 genes × the same 35 columns | The same descriptive contract | Exploratory selected markers; shares specimens with the general panel |
| GSE47774 SEQC NYG | Integer counts; lanes aggregated into technical libraries | Held-out nominal ERCC A/B ratios; secondary titration and repeatability | Shared RNA materials and technical replicates, not independent patients; not gene-wide truth |

The cached NYG file contains 22,427 features and 140 lane columns across 20 libraries including E/F ERCC-only material. Preparation selects A–D (16 libraries), a bounded endogenous panel and all 92 exact ERCC identifiers. E/F are excluded from endogenous comparison. A private retained nonce selects calibration and evaluation controls within the manufacturer's ratio groups, independently of performance. Every arm receives the same calibration subset and opaque feature IDs. Evaluation membership, original feature mapping and nominal evaluation ratios remain coordinator-private.

Counts are divided by submitted positive library factors; common rescaling cancels. The reference metric is median absolute held-out ERCC log2-ratio error under a frozen provisional cutoff. Eligibility uses retained raw control counts before answers; missing and failed attempts remain failures in eligible planned slots. No eligible controls means unavailable success. Within-mixture technical CV and endogenous A–C–D–B ordering are descriptive secondary diagnostics. RNA mass fractions are not automatically mRNA-content fractions.

## Commands

The [standalone probe guide](preflight-only.md) documents `public-run --preflight-only --authorize-exec-preflight`. This performs no scoring and requires no scored-participant authorization. It writes `preflight.json` rather than a scored run aggregate. Both probe-only and normal runs now enforce `--max-provider-sessions` across all model-bearing CLI sessions.

Use fresh absolute paths in the project, outside system temporary directories. This command writes configuration and an empty report only:

```sh
python -m devbench.study.cli public-plan \
  --dataset seqc_controls \
  --output /absolute/project/results/public-plan
```

Record an exact `--model` for a future executable plan. `--dataset` can be repeated. The default all-source configuration schedules nine scored attempts; the default caps are six scored attempts and six combined provider sessions, so it rejects that larger schedule before dispatch. It never silently truncates the study. One selected source produces a three-attempt pilot requiring up to six provider sessions. Standalone preflight selects the first source once per condition and requires three provider sessions.

Future stages are `public-prepare --plan … --cache-root … --output … --authorize-data-execution`, then `public-run --plan … --bundles … --workspace-root … --output … --authorize-live-participants --authorize-exec-preflight`. Use those flags only within an authorized run scope. Current authorization covers small local fixtures, not these live stages. Preparation retains partial failures in `bundles.json`; it never substitutes synthetic data for missing public inputs.

`public-report --plan … --records … --output … --authorize-result-analysis` verifies artifact bindings. Supply `--execution-receipt-sha256` from the separately retained execution receipt to check that root too; otherwise verification is labeled internal consistency. Unknown model identity, memory, tokens and cost remain unknown.

The executor uses the existing `StudyCodeActionAdapter`, actual `codex exec`, common ordinary Python dependencies and a protected tool broker. Enforced answers require a successful public-input audit before the matching analysis and agreement with the cited numerical payload. Receipts establish execution consistency; reference grading determines limited task success. Neither certifies narrative claims, biological correctness or clinical compliance.

The runtime must pass its permission preflight before dispatch. Colab credits and cached data do not prove that this boundary works. Live model identity emission, sandbox/socket interaction and actual agent performance remain unmeasured.

The broker remains inactive during preflight; only its capability-checked handshake is available. Analysis calls become available when the coordinator starts the broker clock after a passing probe, and the preflight handshake is then rejected. The handshake establishes capability possession and phase, not an authenticated caller identity. The shared outer deadline still includes setup and preflight for all conditions. Run manifests pin the observed CLI version and executable components, including the native payload behind the npm launcher.

The current launch also requires `--authorize-exec-preflight`. It permits one additional unscored provider session per attempt to exercise the same `codex exec` surface, using disposable canaries and a capability-checked transport handshake that executes no biological tool. Scored dispatch requires the recorded control to pass. A three-condition, one-source pilot therefore permits at most six provider sessions: three preflights and three scored sessions. Preflight costs and failures are recorded separately and aggregated in the run manifest; unavailable values remain unknown. The scored-attempt cap itself remains unchanged.

Enforced report acceptance is constrained to outputs the host tools actually recorded. Raw reference correctness remains the primary public task outcome and is reported separately. ERCC control recovery does not establish appropriate normalization for endogenous RNA; the [research assessment](../reports/benchmark-redesign/robustness-research.md) describes the supporting evidence and remaining assumptions.

## Source evidence

Source admission pins the [official GEO processed count table](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE47774) and [manufacturer ERCC reference](https://assets.thermofisher.com/TFS-Assets/LSG/manuals/cms_095046.txt). Technical-replicate interpretation follows the [SEQC study description](https://www.nature.com/articles/sdata201420). Hashes and format checks are retained in the gitignored public cache and `results/benchmark-redesign-2026-09-11/`. See the [implementation contract](public-track-design.md) for the separation from simulation truth.
