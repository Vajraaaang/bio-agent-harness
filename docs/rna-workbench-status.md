# RNA-seq workbench: current status

Updated **2026-09-24**. Requirements and scientific guardrails are in the
[product requirements](rna-workbench-requirements.md). The immediate product
priorities are bulk-count contrasts and the backed scRNA workflow; the broader
RNA-seq scope remains in the backlog.

> **Current authorizations — 2026-09-24**
> M1 authorized local implementation, small synthetic fixtures, local browser
> verification and documentation. It did not authorize live model/preflight
> sessions, acquisition/uploads, cloud/e11 runs, training, scientific/resource-policy
> changes, commits or publication. Additional provider work requires a separate
> approved scope; the September 23 stopped cycle remains closed.

## Current state

M2a figure evidence/presentation and the single-screen workspace are locally
verified in `reports/rna-agent-readiness/2026-09-27-m2a.l9eya67z/README.md`.
Recorded results include 597 passes/1 known T14 failure in the broad offline run,
10 native checks, 31 final UI/controller checks, actual browser flows and portable
presentation replay. Suites overlap and are not summed. The Python core was
unchanged after the broad run; final UI verification is separately identified.
Its evidence record is authoritative for fresh results; M1 counts below remain historical.
The new catalog registers 22 requirements but enables presentation only for
verified QC-scatter and UMAP artifacts. No M2b graph or live-provider support is
implied. T14 remains unresolved and the broad suite must not be called green
while that failure persists.

| Research journey | Scoped delivery and remaining evidence |
|---|---|
| Donor-aware comparison | M2c; actual unit/design/result contracts and affected T14 gate remain required. |
| Conflicting annotation markers | M2b; new expression/aggregate plots and annotation evidence remain required. |
| Integration preserving biology | M2b comparison views; quantitative method ranking needs separately validated mixing and protected-biology criteria. |
| Figure-only revision | M2a locally verified: exact data/population/parameter/parent invariance, observed worker cleanup and zero numerical/model dispatch; actual QC and UMAP browser revisions. |
| Exploratory selected population | M2b; exact membership/state and exploratory provenance remain required. |
| Interruption and reproducible figures | M1 bounded numerical recovery retained; M2a adds presentation cancellation, immutable attempts and portable rendering replay. Arbitrary host-loss recovery remains open. |
| Traceable literature interpretation | Later claim/source enforcement; stable figure receipts alone do not establish entailment or scientific-review quality. |

- **Callable:** four bounded workflows, operator-review records, controlled
  execution, numerical reports, research/reviewer packets and native exports.
  The scRNA page now uses the existing proposal selector as well as the operator
  path, with explicit fixture/live-origin labels and approval before execution.
- **Recorded verification:** the [M1 integration](../reports/rna-agent-readiness/2026-09-24-m1.i_ibb4sy/README.md) adds a browser-observed scRNA proposal/approval/run flow, real numerical cancellation/resume, bulk CLI control and verified export/replay. Proposal choices are recorded fixtures; no live model was invoked. Earlier four-route handoffs remain in the [evidence table](#evidence).
- **Open:** live scRNA agent decision evaluation, complete narrative claim enforcement,
  live decision/reviewer evaluation, full e11 execution and the T14 cellular
  `effect_definition_mismatch`. The [September 23 study](../results/codex-feasibility-retry-20260923.eun55i2s/REPORT.md)
  stopped on missing CLI model metadata after one unscored preflight; zero scored
  analyses ran. Its 607-pass/1-failure regression is a separate later record.

## Capability records

There are **two execution paths**: the bounded `AnalysisEpisode` path for WF-1–3,
and the backed native workbench for WF-4. The **four fixture routes** below are
variants across those two paths. “Four replay paths” refers to numerical replay
of these same four workflow variants, not four additional engines or LLM replay.

These records describe synthetic fixtures; organism-specific and real library-
protocol performance is not established by them. Implementation = callable bounded route. Verification = retained small-fixture
results at the linked revision, not a fresh full-suite result. Shared metadata:
exact method/package versions and options are retained in the linked run's
configuration/environment; execution enforces the selected route's frozen
resource/access profile and immutable input/source identities. Input eligibility
is checked per proposal. Scientific scope follows the [claims rules](rna-workbench-requirements.md#rules-for-claims).

| ID / workflow | Inputs, units and prerequisites | Method, output and code owner | Implementation / verification / remaining scope |
|---|---|---|---|
| WF-1 — `bulk_counts` | Bounded CSV/Zarr gene counts; metadata, condition, donor/specimen/library identity; independent units and estimable contrast. | `mean_condition_analysis`: NB mean contrast, gene statistics, diagnostics and receipt-bound synthesis. Owner: [episodes](../devbench/analysis/episodes.py) with existing bulk tools. | **Implemented bounded route; offline fixture.** [Saved handoff](../reports/completed-scientific-review/2026-09-22/workspaces/handoff-ogc14rr7/handoff-evidence.json). No general longitudinal or complete-estimator calibration claim. |
| WF-2 — `bulk_continuous` | Bounded declared-scale continuous CSV/Zarr expression and sample metadata; valid independent-unit contrast. | `mean_condition_analysis`: Gaussian contrast on the declared scale; same episode/synthesis path. No count conversion. | **Implemented bounded route; offline fixture.** [Saved handoff](../reports/completed-scientific-review/2026-09-22/workspaces/handoff-z7skait9/handoff-evidence.json). Broader repeated-measure designs need separate support. |
| WF-3 — `cellular_counts` | Bounded cell-count matrix, cell type, condition, donor/specimen/library membership; auditable aggregation and design. | `pseudobulk_membership_audit` then `cell_type_state_analysis`; donor-aware state contrast and synthesis. Owner: episode path plus existing cellular tools. | **Implemented restricted route; offline fixture.** [Saved handoff](../reports/completed-scientific-review/2026-09-22/workspaces/handoff-56tq93ym/handoff-evidence.json). T14 study regression remains open; this fixture does not establish all cellular contrasts. |
| WF-4 — `backed_scrna` | External H5AD/10x with declared UMI counts, expected hash, confirmed capture units, count/annotation provenance and supported scRNA declaration. Reads are backed/bounded. | Native QC, correction/embedding, clustering, annotation evidence, deterministic review and bundle. Owners: [workbench proposals](../devbench/analysis/workbench_proposals.py), [workbench](../devbench/analysis/workbench.py), [operator UI](workbench-ui.md). | **Implemented bounded pipeline with agent/operator proposal UI; 80-cell offline numerical and browser evidence.** [Saved handoff](../reports/completed-scientific-review/2026-09-22/workspaces/handoff-c3fyzkos/handoff-evidence.json). UI uses supplied markers; the separate [classifier route](analysis-workbench.md#celltypist-runtime-scope-2026-09-21) has artificial-model software evidence. Full e11 and published-model annotation performance remain unverified. |

All four handoffs bind a proposal, operator decision, numerical result and joined
scientific-review packet; their model decisions are recorded fixtures. See the
[named replay evidence](../reports/completed-scientific-review/2026-09-22/README.md).
Raw reads, general snRNA, normalized-only cellular, spatial and isoform workflows
have no verified end-to-end route in this milestone; see the
[input support matrix](analysis-episodes.md#support-matrix).

## Contract status

**Partial** means components exist but the complete product contract is unmet.
**Planned** means the full contract has no demonstrated implementation. These
statuses apply to the general product; individual fixture evidence is retained.

| Contract | Status | Existing responsibility / evidence | Remaining requirement |
|---|---|---|---|
| [AC-1 Discovery/admission](rna-workbench-requirements.md#ac-1) | Partial | Analyst, scientific admission; [proposal selector](../devbench/analysis/episode_planning.py) | M1 connects WF-4 to the existing selector; broader workflow inventory and live selection evidence remain open. |
| [AC-2 Extension](rna-workbench-requirements.md#ac-2) | Partial | Architecture/support matrix; existing registered routes | Governed registration/evaluation of new custom workflows. |
| [AC-3 Differential expression](rna-workbench-requirements.md#ac-3) | Partial | Scientific validators; WF-1–3 | Design-specific statistical and biological validation. |
| [AC-4 Annotation](rna-workbench-requirements.md#ac-4) | Partial | Scientific tools/dashboard; WF-4 | Reference applicability, uncertainty performance and experimental-label evaluation. |
| [AC-5 Instructions](rna-workbench-requirements.md#ac-5) | Partial | Analyst/research; bound questions/options and supplied claims | Unified instruction precedence/version/application evidence across roles. |
| [AC-6 Ownership](rna-workbench-requirements.md#ac-6) | Partial | Execution environment; episode controller and UI workspace lock | M1 verifies local queue/cancel/finalization ownership; wider platform recovery remains open. |
| [AC-7 Access](rna-workbench-requirements.md#ac-7) | Partial | Execution environment/evaluator; scoped runtime and input boundaries | M1 checks actual resume bindings and default-off model access; live provider containment remains untested here. |
| [AC-8 Cached stops](rna-workbench-requirements.md#ac-8) | Partial | Execution environment; [checkpoint store](../devbench/analysis/checkpoints.py) | M1 keys local requests by inputs/config/source/runtime/selector, preserves terminal outcomes and gates cached readers; cross-platform cache behavior remains open. |
| [AC-9 Failures](rna-workbench-requirements.md#ac-9) | Partial | Execution environment/validators; bounded failures and retained outcomes | M1 retains prerequisite/native failures and cancellation cleanup evidence; broader dependency graphs remain unverified. |
| [AC-10 Invocation](rna-workbench-requirements.md#ac-10) | Partial | Analyst, research, scientific reviewer; bounded selectors/packets | Complete role invocation policy and live evidence. |
| [AC-11 Resume](rna-workbench-requirements.md#ac-11) | Partial | Execution environment; episode resume/replay and native checkpoints | M1 resumes controlled stopped numerical work with remaining budget; unknown liveness blocks launches. Arbitrary host-loss recovery remains open. |
| [AC-12 Evaluation](rna-workbench-requirements.md#ac-12) | Partial | Evaluators/completion audit; [prepared evaluation](workbench-evaluation.md) | Native scRNA three-condition dispatch and full-product decision evaluation. |
| [AC-13 Export](rna-workbench-requirements.md#ac-13) | Partial | Dashboard/reporting; native archive and numerical replay fixtures | Authenticated API export and relocated numerical replay verified in M1; browser download-save completion remains unobserved. |
| [AC-14 Human approval](rna-workbench-requirements.md#ac-14) | Partial | Operational flow; [episode review](../devbench/analysis/episode_proposals.py), workbench review | M1 presents the authored proposal for bound operator approval; operator labels remain caller-asserted. |
| [AC-15 Claim traceability](rna-workbench-requirements.md#ac-15) | Partial | Reporting/research/reviewer; [numeric synthesis](episode-synthesis.md) and evidence packets | Enforced evidence bindings for every substantive scientific narrative claim. |

The older role names map directly: **Analyst → AC-1/5/10; Research → AC-5/10/15;
Scientific reviewer → AC-10/15; Execution environment → AC-6–9/11/14;
Scientific validators → AC-3/4/9; Evaluators → AC-12; Dashboard/reporting → AC-13/15.**
Architecture/support maps to AC-2; scientific admission to AC-1; operational
flow to AC-14; dataset preservation to AC-7/11; and the completion audit to AC-12.
The [retained milestone audit](../reports/rna-workbench-completion/2026-09-22/README.md)
keeps its historical ledger. Research retrieval/source applicability and reviewer
expertise, independence and performance remain distinct outstanding questions.

## Next milestone

**M1 local implementation is delivered.** [Evidence and limitations](../reports/rna-agent-readiness/2026-09-24-m1.i_ibb4sy/README.md)
cover WF-4 question → recorded selector → operator approval → real numerical
execution → results/export, WF-1 CLI control, cancellation/recovery, and numerical
replay with original paths unavailable. The proposed September 25 report location
was replaced by the actual fresh September 24 run directory.

Owner for the next bounded increment: Codex implementation coordinator; execution
approver: project owner. Next deliverable: an offline diagnosis and tested remedy
for the existing CLI/model-metadata blocker, with an exact separately approvable
live-selector check and session budget. Live decision quality and the broader
RNA-seq backlog remain separate work. T14 needs its own scientific-contract
investigation; M1 did not change its definitions or thresholds.

## Evidence

These are **historical results at their saved revisions**, inspected during the
documentation repair. Suites overlap and are not summed. Each listed JUnit file
records zero failures, errors and skips. These entries describe the earlier milestone; M1 results are linked separately below.

| Evidence | Saved result and direct source |
|---|---|
| Admission | [97 tests](../reports/rna-workbench-milestone/2026-09-22/admission-final/junit.xml) |
| Scientific packets | [160 tests](../reports/scientific-review-packets/2026-09-22/final/junit.xml) |
| Numerical components | [271 tests](../reports/numerical-components/2026-09-22/regression.xml) |
| Connected-flow audit, before final packet join | [338 tests](../reports/rna-workbench-completion/2026-09-22/integration.xml) |
| Joined handoff follow-up | [49 tests](../reports/completed-scientific-review/2026-09-22/handoff-1.xml) |
| Scientific-reviewer adapter | [123 tests](../reports/scientific-review-agent/2026-09-22/final/junit.xml) |
| Retained receipt checks | [31 valid receipt records](../reports/completed-scientific-review/2026-09-22/receipt-verification.json); saved verification, not 31 tests or a new verification run |
| Source ZIP/HTML packaging | [16 tests](../reports/workbench-ui/2026-09-22/packaging/packaging.xml); wheel build [attempted and failed](../reports/workbench-ui/2026-09-22/packaging/result.json), so wheel verification remains open |

M1's broad opt-in run recorded **530 passed, 3 failed, 0 skipped**. Two failures
were incomplete substituted-backend fixtures; both pass after adding the actual
preparation's required input binding. The remaining T14 failure is unchanged.
This is a combined evidence statement, not a claim that the original full run
was green. See the [run and targeted retest](../reports/rna-agent-readiness/2026-09-24-m1.i_ibb4sy/README.md).

## Glossary

- **DevHarness:** scientific tools, validation and controlled execution components. **DevBench:** task/evaluation infrastructure and protected reference assessment.
- **scRNA / snRNA:** single-cell / single-nucleus RNA sequencing. **Backed:** reads an on-disk expression matrix without eagerly loading the whole matrix.
- **e11:** the existing `e11_integrated.h5ad` integration dataset (22,702 cells, ten recorded batches). Its original annotation classifier was not retained; batch names alone do not prove independent biological units.
- **T14:** a synthetic cellular benchmark template; its `state-only` case has a recorded effect-definition contract mismatch.
- **UMI:** unique molecular identifier used to count captured molecules. **NB:** negative-binomial count model. **BH:** Benjamini–Hochberg multiple-testing adjustment. Passing arithmetic checks does not establish complete-pipeline error control.
- **Approval:** operator decision bound to a proposal; **scientific review:** evidence/interpretation assessment; **clinical validation:** intended-use evidence and operational controls. These terms replace the ambiguous blanket word “qualified.”

## Change log

- 2026-09-24: moved current status first; split future requirements; populated WF-1–4;
  mapped AC-1–15 to status/roles; corrected evidence attribution and wheel wording;
  proposed M1 with owner/date/finish line. Historical reports were preserved.

- 2026-09-24 M1: connected the existing proposal selector to the local interface; added local ownership/cache/cancel/resume evidence and explicit export/browser limits. Live model evaluation remains pending.
