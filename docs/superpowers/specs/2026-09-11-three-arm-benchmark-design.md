# Three-condition inference benchmark design

## Approved scope and execution restriction

Implement the previously discussed baseline, optional DevHarness and enforced DevHarness comparison, improved synthetic/public data contracts, biological metrics and dashboard. The instruction to implement the recommendations is design approval. This revision must not generate or acquire benchmark datasets, run biological analyses, or launch model participants. Clarification about tiny unit-test fixtures is pending; until it is answered, verification is limited to static checks and configuration-only operations.

Existing v0.4.17 tasks, source snapshots, measured runs and reports retain their meaning. The new study is a separate `devbench.study` path and versioned record format. The existing `assisted` condition is historical guided assistance, not silently relabeled optional. This work is research software; it does not establish calibrated inference, biomarker validity or clinical qualification.

## Conditions and shared controls

- `baseline`: fresh Codex participant, ordinary pinned Python dependencies, common scientific question and output schema, no DevHarness endpoint.
- `optional`: identical participant resources plus documented callable DevHarness tools. Wording explicitly permits ordinary methods, hybrids and no tool use. Adoption has no direct correctness reward.
- `enforced`: the same available methods plus scenario-appropriate required checks before acceptance. Checks are enforced by coordinator-owned state, not participant declarations or writable SDK traces. Exploration remains flexible; acceptance is conditional on verified evidence.
- Scripted reference controls are a separate actor kind and never included in live-agent performance.

All conditions share immutable inputs, a declared hypothesis family, independent-unit metadata, input alignment checks, restricted execution, common biological scoring, model/version identity and failure retention. Scientific success, workflow compliance, artifact acceptance and completion are different fields. In particular, an agent that follows every step but makes a wrong discovery fails scientific scoring.

## Execution and trust boundary

Retain Codex subscription CLI execution through the existing adapter. A study adapter uses explicit named permissions with default-denied filesystem reads and grants only the staged participant workspace, necessary runtime libraries and one per-run tool endpoint. Coordinator source, private truth, other participants and receipts remain outside allowed paths. No escalation/permission bypass is enabled for measured participants. Missing support or unsuccessful boundary preflight blocks a run and is recorded, never silently relaxed.

A coordinator-owned tool broker executes an allowlisted set of ToolDispatcher operations on protected inputs, validates bounded requests, captures real results/errors and assigns receipt IDs. The participant receives a small client, not broker/grader source. Tool receipts bind input identity, arguments, implementation identity and outcome. Optional and enforced runs use the same broker transport and accounting. Enforced fits require successful matching input/design checks. An accepted enforced statistical claim must resolve to a broker result and agree with its values/scope. A blocked design permits a supported diagnostic conclusion, not an invented fitted effect.

This is an execution-evidence boundary, not a theorem that every scientific claim is true. Worker resource limits are not a filesystem security boundary. Never promote `.devharness_trace.jsonl` into authenticated telemetry. Free-form narrative remains unverified and separate from machine-checked claims.

## Data design

Add independently seeded negative-binomial and Poisson-lognormal generating families, randomized opaque gene IDs and six temporal/batch/replication scenarios. The initial recipe uses 2,000 genes, six times, three batches and four independent samples per balanced cell. Time-program membership is private and varies by cohort; include increasing, decreasing, transient, delayed and null programs, batch-only effects and library-size variation. Record known stable references only when genuinely part of the supplied experimental design. Technical replicates never become independent donors.

Generator outputs, when explicitly requested in a future execution, separate participant inputs from private truth. Latent truth defines the estimand on normalized relative expression, not absolute RNA production or causality. Parameters describe uncalibrated development recipes until independently evaluated; fitting a generator to empirical training data and held-out realism checks are required before realism claims.

A public-source registry includes E-MTAB-6798 continuous RPKM, SEQC GSE47774 count/control data, and later scDesign3/dyngen/Perturb-seq extensions. Registry metadata is not a downloaded dataset. Every supplied public subset must carry source identity/hash, measurement layer, selection, biological/technical units and explicit available/unknown truth scopes. Missing public data never silently falls back to synthetic. Current cached mouse subsets are descriptive references; unspecified specimen identity and absent causal/latent-time truth constrain permitted tasks.

## Scientific contracts

Require declared measurement type, estimand, hypotheses, normalization assumptions, model/selection rule, effect units, diagnostic status and missing-estimate reasons. Do not require four regulators or strict monotonicity for every task. Counts and continuous expression have different admissible methods. Repeated units, unestimable contrasts, nonfinite results and failed fits cannot become successful-looking estimates.

BH algebra and `q >= p` are probability-contract checks, not evidence of FDR calibration. Preserve legitimate unavailable estimates and distinguish independent filtering, failed tests and untested genes. Inferential scope after adaptive selection is explicit; unqualified conditional tests cannot be presented as confirmatory. Record all tested hypotheses, including omitted/filter-excluded members, so agents cannot improve recall/FDR by changing the denominator.

## Outcomes and analysis

Primary contrast: enforced minus baseline in raw scientific-success probability over attempted runs, equally weighted by prespecified scenario families and independent cohorts. Correct-and-accepted reports are a separate secondary outcome; condition-specific workflow acceptance does not redefine raw biological correctness. Optional-minus-baseline and enforced-minus-optional are exploratory secondary contrasts. Report uncertainty at cohort level, preserve all paired repetitions and avoid pseudoreplication from repeated tasks/genes/cells. Do not emit a confirmatory p-value for repeated runs as independent McNemar trials.

Metrics include signed recall, precision/FDP and cohort-average FDR, full-universe ranking/AUPRC, effect/curve errors, shape macro-F1, interval coverage with width/availability, justified/false refusals, unsupported claims, completion and all-attempt resource cost. Timeouts are failures for primary success and unavailable for uncomputed biological estimates. Tool adoption/compliance is a separate process panel based only on trusted capture. Public replication/concordance results remain distinct from synthetic truth metrics.

## Dashboard and offline deliverables

Provide an interactive standalone report with filters for condition, scenario, source type, model and study version. Include paired gains, outcomes, recovery/false discovery, effect/trajectory accuracy, calibration, diagnostics/refusals, resources and workflow evidence. No data means an honest planned/not-run state, never sample performance curves. Export record/plot-source tables and clearly mark unknown/unavailable values. Statistical graphics need a standard plotting library; numerical report generation must not execute data analysis or live agents implicitly.

## Completion evidence

Completion requires a requirement-to-file/check ledger, source-only before/after comparison, focused allowed tests/static checks, review of data contracts and statistical methods against primary literature, and browser/visual inspection of the empty dashboard. The evidence must explicitly list all unperformed empirical calibration, dataset preparation and live evaluation. Historical result files must not be edited. No upload or publication is part of this goal.
