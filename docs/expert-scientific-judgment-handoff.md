# Expert scientific judgment — project handoff

Date: 2026-10-01. Status: **plan prepared; repairs, new evaluation and expert qualification are not implemented or run by this handoff**.

## End goal

Build a research-grade transcriptomics agent/workbench that assists with the scientific judgment of an experienced bioinformatician: diagnose data and design, choose suitable methods, compare actual evidence, recognize valid alternative interpretations, distinguish error from uncertainty, and explain reproducible conclusions. DevBench/DevHarness must measure this capability without falsely rejecting defensible biological alternatives. Human scientific oversight and separate execution/acceptance/promotion controls remain required. The product direction is a local browser over remote data, HPC compute and notebooks, with broader RNA workflows added in qualified stages. It is not currently clinically validated or proven to equal experienced humans.

The next milestone repairs diagnostic-versus-exclusion prerequisites and investigates the planning timeout, then evaluates feasible alternatives in a **new frozen synthetic cycle**. In parallel design work, establish alternative-aware scientific judgments and a qualified human/held-out validation protocol. Do not claim the next synthetic cycle alone solves biological interpretation.

## Read first

1. Root `AGENTS.md` and the [implementation plan](superpowers/plans/2026-10-01-expert-scientific-judgment.md).
2. [Completed live pilot](../reports/rna-agent-readiness/2026-10-01-method-selection-live.793kpnac/README.md), its [blinded review](../reports/rna-agent-readiness/2026-10-01-method-selection-live.793kpnac/independent-scientific-review.md), [final integrity check](../reports/rna-agent-readiness/2026-10-01-method-selection-live.793kpnac/final-integrity-verification.json) and [native audit](../reports/rna-agent-readiness/2026-10-01-method-selection-live.793kpnac/native-pilot-verification.json).
3. [Scientific planner](scientific-method-planner.md), [scientific review](scientific-review.md), [annotation-scorecard policy](annotation-scorecards.md) and [workspace architecture](research-workspace-organization.md).

Do not read private graders/goldens, other tasks or credentials. All source references below are public platform contracts/implementation; the future evaluator alone handles explicitly authorized hidden references after decision/review sealing.

## Verified current evidence

Report root: `/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-01-method-selection-live.793kpnac`.

| Case | Observed result | What it establishes |
| --- | --- | --- |
| Clean | Retained 72/72, 0 valid removed, null recommendation | Preservation by this executed candidate |
| Technical damage | Retained 72/72, 0/12 planted damaged removed | Failed frozen damage sensitivity; no diagnostic alternative tested |
| Valid heterogeneous signal | Overall native selection exceeded its 120-second deadline | Operational failure, cause undetermined; no output or scientific-abstention credit |
| Insufficient information | Retained 72/72, null recommendation | Provisional caution; numerical validity/distortion unavailable |

Three candidate runs, 16 selections and 32 provider sessions occurred. No comparative scientific winner was established. The native completed actions verified `gpt-6.1-sol`; the timed-out action has no qualified completion identity. CLI 0.159.2 was pinned after a compatible-runtime repair. Low reasoning effort and resource settings were test-author choices.

Recorded frozen verification: **1,586 passed, one existing T14 `effect_definition_mismatch`, zero errors/skips**; standard continuous/count synthetic qualification passed. Preserve optional harmonypy limitations and investigate only if relevant to the new work. These are retained results, not an assertion that a newly changed checkout passes.

These two new planning documents are intentional additions after the 625-file source freeze. The next baseline must record this expected documentation delta rather than asserting an unchanged entire checkout. Their creation changed no implementation or historical evidence.

| Identity | SHA-256 |
| --- | --- |
| Input/reference/rubric/budget/runtime root | `10eed29be43b340e51781f5c206c26eaad23fee1a8fdcbe4d6c8bd8cf7a71856` |
| Expanded source inventory, 625 files | `5a8965193bd9561e93d554d3a942ce9d1941c38d4bbde00e2c881f2bf499dd4a` |
| Native source bundle, 340 files | `4842d80c1fe565d69a4e3677c608937be78b602316337a58d5d6cf25663ff7eb` |

The completed pilot preserved 23,141 previous-pilot files, 14,895 historical files and 152 MAD/4 smoke files. These inventories overlap; do not add them. MAD/4 remains an assistant-selected execution fixture, not a user requirement or selected scientific best method. Preserve its corrected attribution and original evidence.

## Immediate defects and source owners

**Diagnostic scope:** `_applicability` in `devbench/analysis/method_planner.py` currently treats hard assumptions and all blocking dataset unknowns as candidate-wide gates. The current candidate contract has no action-specific scope. Add versioned, coordinator-checked action intent/scoped prerequisites, including mandatory named requirements owned by the coordinator. Generic numerical atypicality flags may use finite typed data without assay semantics, but cannot claim technical damage; assay/QC interpretation requires measurement meaning; exclusion needs its separate evidence and authorization. Unknown computable rank/variance triggers bounded prerequisite inspection rather than permanent blocking. No model-selected scope can erase universal numeric/type/integrity requirements or grant removal; enforce actual output effects, not the candidate's “diagnostic” label.

**Diagnostic output implementation gap:** `observation_selection_preview` in `devbench/analysis/method_validation.py` accepts only the exact `retained_ids` output, and the current contract/runner are retention oriented. Task B must add an explicit diagnostic interface/policy to `devharness_core/methods.py`, validation/extraction to `devbench/analysis/method_validation.py`, and extraction/evaluation wiring to `scripts/run_method_selection_pilot.py` and `devbench/analysis/method_selection_evaluation.py`. Preserve the old observation-selection API and comparison evidence floor. Enforce full input-ID coverage/retention, no silent subsets/removal/transformation, finite scores or explicit unavailable status, bounded evidence references and provenance. Independently measure public diagnostics/stability from immutable input plus actual flags; flags/self-report are predictions. Task F separately scores flagged-damage detection and exclusion recovery using hidden truth only after decisions/blinded review sealing. Focused tests include `tests/test_method_contracts.py`, `tests/test_method_comparison_provenance.py`, `tests/test_method_selection_evaluation.py`, `tests/test_method_selection_pilot_runner.py` and new `tests/test_method_diagnostic_outputs.py`.

**Timeout:** `devbench/analysis/episode_codex.py` wraps the overall native selection with a deadline, including preflight/staging, scientific action and capture; `devbench/analysis/method_planner_recovery.py` retains interrupted operations without restarting computation. Inspect `devbench/study/adapter.py`, `devbench/study/exec_preflight.py`, `devbench/study/cli_identity.py` and `devbench_engine/adapters.py` as applicable. The cause is undetermined. Trace phase events and add necessary phase telemetry before choosing a repair. Keep runtime work separate from scientific grading, preserve the original failure, and qualify cleanup/budget/identity controls. Automatically increasing the timeout is not a diagnosis.

**Alternative-aware judgments:** annotation scorecards currently require literal vocabularies. Add predeclared versioned hierarchy/granularity/label policies, with incompatible labels and unsupported fine resolution still penalized. In `devbench/study/reasoning_review.py`, `apply_reasoning_reviews` forces joint failure whenever `primary_task_success == 0`. Preserve that legacy endpoint; create a separately versioned judgment endpoint distinguishing objective failure from disputable reference mismatch. Its failure taxonomy and adjudication policy belong to the coordinator/evaluation contract. An evaluated model cannot call its own bad score disputed to bypass a hard error. Never let articulate reasoning reverse a proven integrity/statistical error.

**Expert validation:** the existing two-rater reasoning framework and recent model-agent review are useful interfaces, not qualified human validation. Retain independent expert judgments/disagreement, acceptable alternatives, blinded adjudication and held-out evaluation. Evaluated planners/model judges and developers are blind to final references; trusted expert reference builders/scorers/adjudicators access necessary relevant evidence in protected roles at the protocol's correct stage. If qualified humans are unavailable, continue software work and mark this validation requirement unmet.

## Next agent's execution sequence

1. Confirm implementation authorization and exact scope. The current request is **plan and handoff only**. A subsequent explicit “implement this plan” can authorize its defined bounded work; carry prior authorization where its scope applies, asking only for missing/expanded scope, data, permissions or budget. Declare the next live cycle separately and never resume the completed cycle. Do not invoke a model/container merely by reading this file.
2. Work in the assigned workspace with absolute filesystem paths. Preserve the dirty/unborn checkout; no reset, sweeping cleanup, blind git add/commit or report rewrite. Check for active writers/processes before freezing. A worktree is not required to create these documents and cannot replace preserving uncommitted work.
3. Snapshot current source/runtime, record differences from the retained pilot and establish an affected-test baseline. Replay old scores read-only:

```sh
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv/bin/python /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-10-01-method-selection-live.793kpnac/verify_scores.py 10eed29be43b340e51781f5c206c26eaad23fee1a8fdcbe4d6c8bd8cf7a71856
```

4. Implement plan B with failing scope tests first; investigate C independently. Establish D's judgment contract and synthetic false-accept/false-reject controls. Test current public contracts, provenance, permissions, legacy record replay and fail-closed behavior. Run the controlled synthetic suite from a frozen source.
5. After offline gates, freeze a new synthetic evaluation. Existing exposed four cases are regression/development material; use fresh holdouts and hidden truth. Separate flag detection from justified exclusion, keep user method/threshold requirements empty, and freeze model-observable comparison criteria before results. Availability follows scientific candidate generation.
6. Under applicable authorization, use verified `gpt-6.1-sol`, native selection and OCI execution. Default new-cycle caps: four cases, 24 selections, 48 provider sessions, eight OCI runs, one CPU/512 MiB/20 seconds per candidate, 120 seconds per overall native selection and 3,600 seconds total scientific live time. Allow at most two separately retained operational diagnostics at 120 seconds each outside the scientific-case budget. Count all attempts/retries; changed caps require a newly predeclared cycle and applicable authorization. Keep interventions separately identified. Evaluation/claim thresholds are test-author choices, not user scientific parameters. Do not force a winner or use hidden simulation labels for planner threshold selection.
7. Seal outputs before blinded review; freeze that review before opening hidden references. Compute independent scores and actual output plots, reconcile score-aware review, replay without computation, and verify historical preservation. Report which claims succeeded, failed or remain unidentified. Stop after reviewing this cycle; further repair starts another frozen cycle.
8. Expert annotation/adjudication and held-out biological judgment validation are the next governed qualification stage. Do not send external messages/recruit reviewers or load real data without applicable authorization. RNA expansion, remote HPC/notebooks, environment qualification and trusted registry promotion remain subsequent roadmap milestones.

## Non-negotiable acceptance tests

- Unknown technical cause/assay semantics does not automatically block generic numerical atypicality inspection; technical QC interpretation requires measurement meaning. Nonfinite inputs are diagnosed, and unknown computable prerequisites receive bounded inspection.
- Flagging outputs preserve immutable input IDs/order/measurements and cannot silently exclude; exclusion needs separate evidence, intent and approval.
- The additive diagnostic output interface enforces full identifier coverage/retention and bounded finite scores/status/evidence references/provenance; old retention outputs still replay unchanged. Public independent diagnostics and post-seal hidden flag/exclusion scoring remain distinct.
- Scientific candidacy remains registry-blind; genuine missing implementation and missing comparisons remain visible.
- Proven numerical/integrity failure stays a hard failure. Compatible biological alternatives are not rejected solely by literal label mismatch. Unsupported over-specific claims and generic coarse answers do not receive full detailed-task credit.
- Timeouts, unavailable outputs, missing review and unresolved reference disputes remain explicit unavailable/error states, not successful abstention.
- Judge references resolve to retained evidence; citation titles, confident prose and model consensus alone do not establish truth.
- Only the versioned coordinator/evaluation policy classifies a primary failure as disputed; a model cannot relabel its own failed score to bypass objective gates.
- Retained JSON is finite: no NaN/Inf; unavailable metrics use null plus a reason, not invented zero errors or estimates.
- Hidden evaluation labels are absent from evaluated model/developer contexts; expert development/held-out/confirmatory sets remain separate. Trusted expert reference/scoring/adjudication roles have the necessary protected-stage access. No score-based prompt or threshold revision inside a frozen cycle.
- Old plans/reports/receipts/roots remain unchanged; new source, inputs, rubric, runtime and report are separately frozen and replayable.
- No expert-level performance claim without qualified independent expert and held-out evidence; no clinical claim from synthetic or public-data results.

## Required report and stop boundary

The new report must answer the five questions and include source/runtime identities, old-versus-new preservation proof, scoped-prerequisite tests, separate timeout investigation, actual outputs and plots, per-case independent diagnostics/scores, blinded judgments and disagreement, negative-control results, uncertainty and a read-only replay command. Explain failure/abstention reasons separately: scientific insufficiency, implementation absence, comparison floor or operational failure.

Planning is complete when these documents are source/evidence checked. The next repair/evaluation milestone is complete only after its offline verification, separately authorized frozen live cycle and independent review are retained—even if the scientific result fails. Broader biological qualification and roadmap work remain explicitly unfinished.
