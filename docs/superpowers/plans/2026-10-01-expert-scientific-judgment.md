# Evidence-grounded scientific judgment and pilot repair plan

> **For agentic workers:** Use the executing-plans workflow task by task. This is a proposed implementation plan, not evidence that the changes or evaluations have been completed. Preserve existing changes and independently review each gate.

**Project end goal:** Build a research-grade scientific agent and workbench that helps researchers analyze transcriptomic data with the judgment expected of an experienced bioinformatician: establish what the data support, choose and compare appropriate methods, diagnose failures, interpret evidence, recognize defensible alternatives, and communicate uncertainty. DevBench/DevHarness must also measure that capability reproducibly without treating disputable biological assumptions as exact truth. Every scientific claim must be inspectable and reproducible, with human oversight for scientific acceptance and consequential decisions. The intended product combines a local browser with remote data, HPC compute and notebooks; these remote capabilities and broader RNA workflows remain later milestones. Expert-level or clinical reliability is an evaluation claim to earn, not a current capability.

**Next milestone goal:** Repair action-specific prerequisites and investigate the planning timeout, then run a fresh method-unspecified synthetic comparison. Establish the judgment/evaluation contracts needed to distinguish demonstrable error from defensible alternatives and insufficient evidence.

**Architecture:** Extend the existing planner, MethodService, scientific-review packets, scorecards and execution DAG. Keep action eligibility, execution availability, scientific defensibility and human acceptance distinct. Numerical evaluators independently measure actual outputs; model judgments cite verified evidence and cannot change scores, authorize execution, or override hard integrity/statistical failures.

**Tech stack:** Existing Python/jsonschema/Pydantic contracts, SQLite scientific records, protected Codex CLI selector, OCI candidate executor, pytest, controlled synthetic qualification, receipt-bound plots. Continue using `gpt-6.1-sol` with a verified pinned runtime in any authorized new live cycle.

## Verified starting point

The [completed live pilot](../../../reports/rna-agent-readiness/2026-10-01-method-selection-live.793kpnac/README.md) contains four 72-observation synthetic cases. Three completed candidates retained every observation and produced accepted null recommendations. Valid heterogeneity timed out during the overall native selection deadline of 120 seconds, which includes preflight/staging, scientific action and capture; the cause remains undetermined. Clean data preserved all 72 valid observations; the damage candidate removed 0/12 planted damaged observations. Diagnostic flagging with exclusion deferred was unnecessarily blocked by prerequisites appropriate to exclusion. No comparative method selection or expert biological qualification was established.

The recorded frozen regression was **1,586 passed, one unchanged T14 `effect_definition_mismatch`, zero errors/skips**; controlled continuous/count qualification passed. These are retained results, not a new execution of the live checkout. Preserve the recorded dependency limitations separately; investigate T14/harmonypy only if they affect the next case or inference.

Retained pilot roots:

| Subject | Retained SHA-256 |
| --- | --- |
| Inputs, references, rubric, budget, runtime | `10eed29be43b340e51781f5c206c26eaad23fee1a8fdcbe4d6c8bd8cf7a71856` |
| Expanded source inventory, 625 files | `5a8965193bd9561e93d554d3a942ce9d1941c38d4bbde00e2c881f2bf499dd4a` |
| Native source bundle, 340 files | `4842d80c1fe565d69a4e3677c608937be78b602316337a58d5d6cf25663ff7eb` |

The runtime qualification used the bundled CLI 0.159.2. The pilot consumed 16 selections, 32 provider sessions and three candidate runs; the timeout is an operational failure, not a scientific abstention. Its blinded review was another model-agent assessment, not a qualified human expert panel.

## Scientific judgment contract

Always follow **data → question → appropriate methods and why → actual outcomes → conclusions and uncertainty**, within existing adapter phases and answer schemas. Record observations, declarations, inferences and unknowns separately. A prior suggestion, default or fixture never becomes a user requirement. Freeze candidates before implementation availability and comparison criteria before results.

Report independent dimensions rather than one reassuring pass/fail:

| Dimension | Required states and meaning |
| --- | --- |
| Execution | completed, failed, timed_out, not_run; absence of an output remains absence |
| Objective validity | verified, failed, not_assessed; independently checked identities, invariants and calculations |
| Scientific judgment | supported, defensible_alternative, demonstrable_error, insufficient_evidence, not_assessed |
| Operational feasibility | available, unavailable, resource_blocked; never evidence of scientific merit |
| Oversight | pending, accepted, qualified_acceptance, rejected; review grants no execution or promotion |

Judgments apply to individual claims/actions with explicit scope, cited artifacts, assumptions, competing explanations and missing evidence. A whole answer can contain a correct diagnostic and an unsupported causal claim. Preserve disagreement rather than forcing a single reference label. A plausible narrative cannot override changed input identifiers, corrupted output, fabricated statistics, invalid contrasts or nonidentifiable causal claims. Failure taxonomy and dispute/adjudication policy are owned and versioned by the coordinator/evaluation contract; an evaluated model cannot classify its own failed primary score as disputed to bypass objective errors. All retained JSON is finite: never serialize NaN/Inf; unavailable metrics are null with a reason, and nonfinite inputs require diagnosis rather than invented zero values.

Three approaches were considered: stronger prompting alone; one consensus model judge; and a layered system of deterministic checks, structured evidence-grounded judgment, alternative-aware references and independent expert validation. Use the layered approach. Prompting is useful instruction but provides no independent correctness test. Model agreement can share the same error; expert consensus itself remains scoped and uncertain.

## Work packages and ownership

Owners below describe responsibilities, not a requirement to launch agents. Do not share writers for the same files. All filenames in command blocks are absolute. Do not commit an unrelated dirty/unborn checkout as part of this work.

### A. Freeze and diagnose the current baseline

**Owner:** evaluation coordinator. **Depends on:** none.

**Read:** current AGENTS, the completed pilot README, `independent-scientific-review.md`, `native-pilot-verification.json`, `final-integrity-verification.json`, `runtime-3-source-manifest.json`, `combined-runtime-3.json`, and `synthetic-runtime-3.json` in the completed report. Do not inspect grader/private-golden files or hidden reference contents while planning candidate behavior.

- [ ] Inventory current source and dirty state; compare against the retained manifest without reverting differences. These two new planning documents are intentional additions after the 625-file freeze and must appear as an attributed documentation delta; no code or historical evidence was changed by their creation. Create a new report directory and immutable before-change inventory. Record prior MAD/4 evidence and completed pilot preservation roots.
- [ ] Replay the retained output scores without scientific execution:

```sh
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv/bin/python /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-01-method-selection-live.793kpnac/verify_scores.py 10eed29be43b340e51781f5c206c26eaad23fee1a8fdcbe4d6c8bd8cf7a71856
```

- [ ] Confirm four terminal records and three recomputed outputs, with zero new model/container runs. Establish a fresh affected-test baseline before editing. Retain known T14 and dependency failures with their scope; a new failure must be investigated.

**Gate:** old source/evidence inventories remain unchanged; every new difference is attributed. No broad-green claim from focused tests.

### B. Separate diagnostics from exclusion and inference

**Owner:** planner/contracts. **Depends on:** A.

**Modify:** `devbench/analysis/method_planner_contract.py`, `devbench/analysis/method_planner.py`, `devbench/analysis/method_authoring_contract.py`, `devbench/analysis/method_validation.py`, `devharness_core/methods.py`, `devbench/analysis/method_selection_evaluation.py`, and `scripts/run_method_selection_pilot.py`; API/CLI adapters only where the new context must pass through. **Test:** `tests/test_method_planner_contract.py`, `tests/test_method_planner_guards.py`, `tests/test_method_planner_scientific_safeguards.py`, `tests/test_method_contracts.py`, `tests/test_method_comparison_provenance.py`, `tests/test_method_selection_evaluation.py`, `tests/test_method_selection_pilot_runner.py`; create `tests/test_method_action_scope.py` and `tests/test_method_diagnostic_outputs.py`. **Document:** `docs/scientific-method-planner.md`.

- [ ] First add failing behavior tests distinguishing three stages: generic numerical atypicality flags may use finite typed measurements without assay semantics, but carry no technical-damage label; assay/QC interpretation requires known measurement meaning; exclusion authorization requires its further action-specific evidence. A flag-only candidate with unknown technical causation may produce reversible descriptive flags but cannot silently remove observations. An exclusion candidate with the same missing evidence remains blocked.
- [ ] Define a versioned optional action-intent field: `retain`, `diagnostic_flagging`, `exclude`, or `infer`. Scope hard prerequisites and blocking unknowns to the supported action. Always retain universal input/type/integrity prerequisites. The coordinator owns named minimum prerequisite policies per intent and assay; model assumptions may add caution but cannot remove or redefine mandatory requirements. New flagging records explicitly bind exclusions to `false`; assumptions cannot be downgraded by a model's assertion.
- [ ] Preserve old identities and exports. Legacy records without an action scope retain conservative behavior; do not guess an intent from names/prose or backfill sealed plans. Recompute scoped eligibility in the coordinator before trial and every new approval-dependent action.
- [ ] Implement an additive diagnostic interface and validation/extraction path. The existing `observation_selection_preview` in `devbench/analysis/method_validation.py` accepts exactly a `retained_ids` object; its observation-selection API, method contracts and historical receipts must remain unchanged. Add an explicit diagnostic contract/policy in `devharness_core/methods.py`, validate it in `devbench/analysis/method_validation.py`, and route declared diagnostic outputs through `scripts/run_method_selection_pilot.py` and `devbench/analysis/method_selection_evaluation.py`. A plain retention output cannot masquerade as diagnostic flag evidence.
- [ ] Define diagnostic outputs as a bounded per-observation flag/score table plus reason and evidence scope. Verify one record per immutable input identifier; no ID, count, variable, signal or order mutation. Retain errors; serialize unavailable values as null with a reason, never NaN/Inf or fabricated zeros. A numerical atypicality flag says only “unusual under this representation/reference”; an assay-supported diagnostic can say “warrants QC review,” not “proven damaged.” Exclusion requires a separate explicit plan/action, evidence and approval. Unknown but computable rank, variance or other prerequisites trigger a bounded coordinator prerequisite inspection rather than permanent exclusion from candidacy; retain the resulting observation or inspection failure.
- [ ] Require full immutable identifier coverage and full retention for diagnostic intent; verify input-linked finite scores or explicit unavailable statuses, valid status vocabulary, resolvable bounded evidence references and output provenance. Reject duplicate/missing/foreign IDs, silent subsets, altered observations, transformations or invented successful scores. Independently compute predeclared public diagnostics/stability from immutable inputs and actual flags; candidate flags and self-reports are predictions, not biological truth. Preserve the existing comparative evidence floor and provenance requirements.
- [ ] Report an evidence-supported diagnostic next step separately from formal comparative selection. Keep the current comparison floor for a claimed winner; null choice can accompany useful diagnostics. Separate scientific uncertainty, missing capabilities, missing comparison and operational failure in the response.
- [ ] Run red/green regressions for intent mismatch, malformed scope, global integrity failures, missing numerical prerequisites, unauthorized exclusion, old-record replay, user-attribution errors and downstream approval scope. No floor bypass to obtain a winner.

**Gate:** missing failure labels or biological causal validation cannot by themselves prohibit generic descriptive flags; missing finite measurements can. Unknown assay semantics limit interpretation rather than automatically prohibiting numerical inspection. Computable prerequisites receive bounded inspection. Inference cannot claim independent replication or causality from unavailable identities/confounded design. Flagging launches no exclusion.

### C. Investigate the timeout independently

**Owner:** runtime/operations. **Depends on:** A; may proceed alongside B with disjoint files.

**Inspect:** `devbench/analysis/episode_codex.py`, `devbench/analysis/method_planner_recovery.py`, `devbench/study/adapter.py`, `devbench/study/exec_preflight.py`, `devbench/study/cli_identity.py`, `devbench_engine/adapters.py`. **Test:** `tests/test_episode_codex.py`, `tests/test_method_planner_recovery.py`, `tests/test_study_adapter.py`, `tests/test_exec_preflight.py`, `tests/test_cli_identity.py`. Create a separate operational-diagnostics report.

- [ ] Trace the retained heterogeneity timeout through preflight, process startup, schema/context preparation, provider requests, stream capture, response validation and cleanup using available timestamped events. The 120-second deadline bounds the whole native selection, not proven model reasoning time. Identify missing telemetry explicitly; do not invent a root cause from the elapsed 120 seconds or automatically increase the deadline.
- [ ] Reproduce bounded startup, slow-stream, malformed-response, cancellation and deadline cases with controlled adapters. Verify process-tree cleanup, terminal failure retention, no automatic retry, unchanged approval scope and finite budget accounting.
- [ ] Add only the missing phase timing/error-classification needed to distinguish local setup, provider latency, interrupted streaming and invalid response. Keep raw failures and qualified model identity; do not log credentials or hidden task material.
- [ ] If a live operational probe is necessary, use at most two separately retained probes at no more than 120 seconds each, with exact model/CLI, public synthetic context and attempt identities declared before dispatch. Do not score these probes as scientific cases. A larger deadline requires evidence-driven diagnosis and a newly predeclared budget/cycle; it is a test-author configuration change, not proof of a repair.

**Gate:** supported root-cause conclusion or explicit unresolved classification; deterministic timeout/cleanup controls pass. Any budget change is versioned in the next cycle, with no retroactive success for the old timeout.

### D. Engineer alternative-aware validation and interpretation

**Owner:** scientific judgment/evaluation. **Depends on:** A; contract design can precede B/C, implementation follows a reviewed design.

**Extend:** `devbench/analysis/scientific_review.py`, `devbench/analysis/scientific_review_agent.py`, `devbench/study/reasoning_review.py`, `devbench/analysis/scorecard.py`, and their existing tests. Create `devbench/analysis/scientific_judgment.py` and `tests/test_scientific_judgment_contract.py` for a focused `devbench.scientific-judgment/1` contract; keep these companion records separate from sealed legacy outputs. Do not modify private graders/goldens.

- [ ] Create immutable claim-level packets containing objective, assay/data semantics, units/design, candidate intent/assumptions, actual diagnostics, exact artifact references, verified plots when available, and source evidence/applicability. Withhold final references, competing model identities and prior verdicts from evaluated model judges and initial blinded decision reviews. Trusted human reference builders/adjudicators access necessary evidence only in their recorded protected roles and protocol stages.
- [ ] Require evidence references for each judgment; reject unknown references, unsupported numerical claims and fake source retrieval. Retrieval summaries must retain source/version, actual accessible text, applicability and limitations. No source-entailment claim from a citation title alone. Use assay-specific policies and independently computed sensitivity/counterfactual checks to challenge a judgment; record whether a conclusion changes with plausible preprocessing, resolution, reference or prior choices. A source describes what it supports, not a blanket authority to validate a method.
- [ ] Version the review contract to support the states above, evidence quality, action consequences, competing defensible explanations and a concrete missing-evidence request. Model confidence labels remain uncalibrated until externally tested.
- [ ] Add prespecified annotation policies for hierarchy, granularity, synonyms and unresolved/mixed types. Version and freeze any ontology/mapping before answers; never fit a mapping from the scored confusion matrix. Distinguish exact concordance, compatible coarser/finer interpretations and contradictory labels. Coarse generic labels cannot gain full fine-resolution credit.
- [ ] Preserve existing `reviewed_insight_success` semantics as a legacy endpoint. Add an alternative-aware judgment endpoint with a coordinator/evaluation-contract-owned, versioned failure taxonomy and adjudication policy. A demonstrable hard error remains failed; a disputable primary reference mismatch remains reviewable only under that policy and cannot automatically erase supported reasoning. A model cannot declare its own bad score disputed to change the gate. An unresolved review stays unavailable, not a pass. Retain raw primary scores, both independent ratings and adjudication separately.
- [ ] Write negative controls: an attractive causal story from correlation; false replication from unique sample labels; forged p/q values; literal-label mismatch with compatible ontology; unsupported over-specific subtype; legitimate coarse labeling; two defensible clustering resolutions; fabricated citation; indeterminate assay semantics; all-abstain judge; all-accept judge; known operational failure.

**Gate:** controls detect objective errors without falsely rejecting the predeclared defensible alternatives. Synthetic control success validates the interface, not expert biological accuracy.

### E. Build expert reference and held-out judgment evaluation

**Owner:** scientific evaluation lead and qualified human reviewers. **Depends on:** D's recorded protocol; no external recruiting/contact is authorized by this plan.

- [ ] Prepare a development set of deidentified/public or synthetic packets across QC/exclusion, cell annotation, design/batch interpretation and biological claim scope. Each packet includes multiple plausible answers, a demonstrable error and an ambiguity case where the data permit one. Hold dataset/biological context apart between development and evaluation.
- [ ] Ask at least two experienced bioinformaticians to review independently before discussion, blinded to model identity and other ratings; retain qualification/independence declarations. A third qualified reviewer adjudicates unresolved disagreements. Preserve original votes and defensible alternative sets; consensus is not universal biological truth.
- [ ] Freeze rubric, accepted alternatives, evidence scope, failure severity, indeterminate policy and expert adjudication before model scoring. New plausible alternatives receive a separately logged challenge review; revisions apply prospectively or to an explicitly versioned rescore of every answer, never just one favored model.
- [ ] Reserve a fresh held-out set and an untouched confirmatory set. Evaluated planners/model judges and their developers remain blind to final references, answer-encoding rubric material and private evaluator files. Trusted expert reference builders, scorers and adjudicators may access the relevant data/references in protected recorded roles at the specified stage; this access must not leak into model inputs or development. If qualified human records are unavailable, mark expert validation blocked while completing synthetic/interface work; do not impersonate expert oversight with model agents.
- [ ] Evaluate demonstrable-error sensitivity, false acceptance of errors, false rejection of expert-supported alternatives, indeterminate recognition, unsupported-claim rate, and coverage versus error at each abstention threshold. Measure confidence calibration only where reference uncertainty and sample count support it. Report rater disagreement, severity, dataset/family strata and uncertainty intervals using dataset/biological units, not cells as independent replicates.
- [ ] Use development data to improve prompts, evidence retrieval, diagnostic checks and calibration; freeze all changes before held-out assessment. Any learned calibrator or fine-tuning follows this split and requires sufficient qualified data. Compare deterministic checks only, current reviewer and the engineered reviewer under the same frozen cases. Report shared-model errors and judge sensitivity to answer style/order/model identity.

**Gate:** a prespecified independently reviewed validation protocol and scored held-out evidence are required before an “experienced-bioinformatician-level” claim. Minimum panel participation, thresholds, sample-size/power justification and claim scope are frozen by the qualified evaluation lead before confirmatory testing. No universal numeric biological pass threshold is invented in this planning document.

### F. Run and review a fresh bounded synthetic cycle

**Owner:** evaluation coordinator plus independent reviewer. **Depends on:** B/C offline gates; D's synthetic judgment controls. E's human qualification is not required to run a clearly labeled software/scientific pilot, but is required for expert-level claims.

**Evaluation implementation:** extend `scripts/run_method_selection_pilot.py` and `devbench/analysis/method_selection_evaluation.py` to extract the new diagnostic output contract while preserving retention-only replay. Add flag-specific numerical controls in `tests/test_method_selection_evaluation.py`, runner controls in `tests/test_method_selection_pilot_runner.py`, and independent measurement/provenance controls in `tests/test_method_comparison_provenance.py`. Diagnostic schema and actual-effect enforcement are owned by B; F owns frozen evaluation integration and scoring.

- [ ] Create a new cycle directory; copy and adapt the existing report helpers outside sealed reports. Treat the four exposed cases as regression/development cases. Prepare fresh seeded holdouts of clean data, technical damage, valid heterogeneity and insufficient information; freeze fresh opaque identifiers and input/reference/rubric/budget/source/runtime inventories before any scientific call.
- [ ] Separate “flagging detection” from “exclusion recovery” scoring. Measure actual flagged damaged/valid observations separately from removal of valid/damaged observations, representation/signal distortion and justified uncertainty. Extract only validated, exact-input-bound outputs; keep public independent diagnostic/stability measurements separate from hidden-label scoring. Open hidden truth for this scoring only after decisions and blinded review are sealed. Synthetic membership is evaluator truth for those simulations, not proof that the observed QC pattern establishes biological damage. Apply scenario objectives without demanding exclusion when only diagnostic review is justified.
- [ ] Freeze planner-observable scientific comparison diagnostics before outcomes, independently of hidden evaluation labels. Include stability/perturbation, group/population preservation and action-specific validation where justified. Hidden labels must never select a method/threshold or appear in candidate construction, implementation prompts, context, logs or model-visible paths.
- [ ] Generate scientific candidates before revealing availability. Admit and execute feasible alternatives through existing MethodService/OCI only. Compare two or more only when scientifically justified; preserve inapplicable/unavailable candidates and abstain if comparison cannot be supported. Do not preinstall a favored method or force a candidate count/winner to satisfy the controller.
- [ ] Pin `gpt-6.1-sol`, CLI/helpers, source, packages, prompts, parameters, resource caps and applicable approvals in the new manifest. Default new-cycle caps: four cases, 24 selections, 48 provider sessions, eight OCI candidate runs, one CPU/512 MiB/20 seconds per candidate run, 120 seconds per overall native selection, and 3,600 seconds total scientific live time. Permit at most two separately retained operational diagnostics of 120 seconds each outside the scientific-case budget. Count all attempts/retries and stop at caps. Changed caps require a new predeclared cycle and applicable authorization; evaluation criteria/claim thresholds are test-author choices, not user scientific parameters. Never resume the completed cycle. Carry prior authorization when its scope applies; a subsequent explicit instruction to implement this plan can authorize its defined bounded work. Ask only for missing/expanded scope, data, permissions or budget.
- [ ] Seal raw attempts, outputs, failures, deadlines and operator interventions before independent blinded scientific review. Open hidden evaluation labels only after decisions/review freeze. Produce coordinator-derived output plots, numerical scores and a separate score-aware reconciliation; absence of a plot/output remains explicit. Recompute scores offline with zero new model/OCI calls.
- [ ] Run affected regressions plus the required controlled synthetic verification suite from the frozen source. Recheck before/after preservation inventories, runtime identity, source drift, privacy, output provenance and exact replay. Browser-check any changed result view using synthetic data only.

**Gate:** report whether comparative choice, useful diagnostics, preservation, damage recovery or justified abstention was demonstrated for each case. No scientific-answer credit for timeout, missing implementation or controller-only nonselection. A failed scientific pilot can complete the evaluation milestone when honestly sealed and reviewed; it cannot qualify the planner as reliable.

The existing focused suites can be run before new test files are created with this absolute-path command; run again with the new action-scope/judgment suites after their red/green steps. Save logs in the new report directory, not the old sealed report. Combined frozen QA and controlled synthetic qualification are additional gates, not replaced by this command.

```sh
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv/bin/python -m pytest /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_method_planner_contract.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_method_planner_guards.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_method_planner_scientific_safeguards.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_method_planner_recovery.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_episode_codex.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_scientific_review_agent.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_reasoning_review.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_analysis_scorecard.py -q
```

## Completion and boundaries

Deliver a new reproducible report containing the baseline, source/runtime identities, scoped-prerequisite regressions, separate timeout diagnosis, old-versus-new case outcomes, actual plots, frozen independent scores, blinded judgment, disagreement/uncertainty and replay instructions. Include a requirement-to-evidence matrix and remaining failures. Stop after review; any further correction starts a new frozen cycle.

This plan does not authorize source implementation or new live execution during its creation. Human oversight remains required. Expert reference collection/held-out biological qualification is a subsequent governed milestone, not a fabricated dependency already met. Broader RNA/large-data support, remote HPC/notebooks, trusted registry promotion and clinical release stay outside the immediate repair cycle. The end goal includes their staged research integration; clinical release requires separately qualified validation, approval and operational controls.

The [handoff](../../expert-scientific-judgment-handoff.md) gives the starting commands, exact evidence and next-agent instructions.

## Primary-source principles informing the design

- [Luecken and Theis, 2019](https://link.springer.com/article/10.15252/msb.20188746): consider QC measurements jointly and preserve legitimate biological heterogeneity; annotation and clustering choices affect interpretive detail. These are design cautions, not a prescribed filtering algorithm or threshold.
- [Luecken et al., scIB benchmark, 2022](https://www.nature.com/articles/s41592-021-01336-8): compare removal of nuisance variation with conservation of biological structure, including label-free evidence and rare populations. Use matched representations and multiple objectives; do not inherit this study's ranking weights for a new objective.
- [Cell Ontology](https://obophenotype.github.io/cell-ontology/) and its [relations guide](https://obophenotype.github.io/cell-ontology/relations_guide/): pin canonical identifiers and relation versions when defining annotation policies. Ancestor/descendant relations do not automatically imply synonymous labels or equal credit at the requested granularity.

The panel/adjudication protocol and judgment taxonomy above are engineering proposals for this project, not claims that these papers establish the correctness of model judges.
