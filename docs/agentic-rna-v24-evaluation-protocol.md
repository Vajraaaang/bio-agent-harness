# Agentic RNA-seq v2.4 prospective evaluation protocol

Document version 1.0, 9 November 2026. Prospective protocol identity:
`devbench.rna-v24-evaluation-protocol/1`. This is the T02 document deliverable,
not an admitted campaign or a qualification result. Campaign admission, T02
acceptance and P0 exit remain **blocked** by the unassigned and unratified
fields below. No scientific workload, model campaign or live provider run is
authorized by this document.

## Authority, version and evidence state

The implementation plan, T02 and section 8 (`docs/superpowers/plans/2026-11-09-agentic-rna-seq-v2.4-implementation-spec.md`)
defines this work. Its normative v2.4 parent (`agentic-rna-seq-spec-v2.4.md`),
SHA-256 `cd821694a23fc25dd13766af72cdca18a07fb4c63bb0eb350cc672531ad3e313`,
supplies STAT-01–06, EVAL-01–05, EXEC-09 and X1–X9. Parent-specified procedure
settings and proposed targets below are attributed to that specification;
they are not user-prespecified study choices or observed performance.

The T01 conformance record (`docs/agentic-rna-v24-conformance.md`)
binds baseline commit `56396e44c4dc41f2781b5fde1680962c1ba18be7` and preserves
its failures and custody limits. The
task conformance ledger (`reports/agentic-rna-v24/conformance.json`)
records T02's pre-edit scope. Future campaigns must bind the actual candidate
source, protocol, evaluator, worker, model and data identities; the T01 commit
alone does not identify a later candidate.

The existing investigation evaluator (`src/devbench/analysis/rna_investigation_evaluation.py`)
retains `devbench.rna-investigation-protocol/1`, campaign `/1`, and its six
arms: `strong_single`, `conditional_0`, `conditional_1`, `conditional_2`,
`sequential_second`, `deterministic`. Its development/calibration/locked-test
fixtures and artificial token controls are not this protocol's evidence.
The evaluation CLI (`scripts/run_rna_investigation_evaluation.py`)
still blocks `--native`. The retained
scientific-trial contract (`src/devbench/analysis/rna_scientific_trial_contract.py`)
and benchmark contract (`src/devbench/analysis/rna_benchmark_contract.py`)
keep their `/1` identities and meanings. This document changes none of those
interfaces. T09/T23 must implement explicit new adapters and validation; no
executable v2.4 validator is delivered here.

| Evidence layer | Current status | Required evidence before a scoped pass |
| --- | --- | --- |
| Software and contracts | Existing baseline evidence only; v2.4 checks not run here | Frozen source and environment; relevant regression/synthetic suites; real entrypoint, fault, permission, lineage and replay receipts |
| Numerical procedure | Not run; campaign blocked | T03 installed worker identity and same-image direct-R smoke, then independently owned T09 agreement, calibration and utility results in every offered envelope |
| Scientific decisions and interpretations | Not run; campaign blocked | Blinded study portfolio, adjudicated errors/holds/usefulness, matched-arm comparisons and complete burden accounting |
| Actual adaptive-policy error control | Not run; unqualified | X8 evidence for the actual selection/reporting policy, model and study distribution; fixed-procedure simulation cannot supply it |
| Live backend | Not run here; each profile separately gated | Applicable grant and actual profile-specific recovery, transfer, deadline, cost and cleanup receipts |

Campaign admission requires T03's build, smoke and Literature identities to be
bound to their own receipts. The subsequent P0 build contract (`docs/erql-1.1-contract.md`)
records installation evidence and the remaining procedure/Literature gates.
An installed dependency, source file or image digest establishes neither
statistical calibration nor an admitted passage-retrieval service. Clinical
release remains outside this research protocol.

## 1. Data and evaluation units

No study dataset or qualification roster is assigned by this document. Future
manifests must distinguish observed measurements, supplied biological facts,
inferences and unknowns. Retain `cell -> capture/library -> specimen ->
biological unit`, technical-component provenance, groups, count provenance,
annotation history, missing combinations and quality concerns. Repeated
specimens, libraries, cells, genes and repeated agent runs are not independent
study draws. Integrity hashes do not establish valid biology or independence.

Authorized original integer gene read/UMI counts and exact eligible pseudobulk
sums are candidate inputs. Integer-valued corrected/imputed data do not become
original counts. Normalized-only inference, general longitudinal/random-effect
designs, abundance, FASTQ, trajectory/regulatory/enrichment inference and
clinical use retain their separate scopes.

### Five-regime scope to test

All five rows are mandatory for eventual R1. These are prospective candidate
regimes, not a published qualified envelope. Each campaign must bind exact
assay, representation, covariates, contrast/reference levels, label and coverage
rules, family allocation, and supported capacity before qualification.

| Regime | Biological unit and target | Candidate design and label/coverage boundary |
| --- | --- | --- |
| B-I | Independent units; condition-associated relative expression | One eligible bulk profile/unit; technical covariates plus condition; no donor effect that absorbs a donor-level condition |
| B-P | Biological-unit pairs across two matched conditions | Unit plus condition and admissible nonredundant technical terms; retain pairing and incomplete-pair inventory |
| S-I | Independent donors; condition contrast within each declared population | One eligible donor/sample/population profile; technical covariates plus condition; separately qualify label provenance and missing population coverage |
| S-P | Matched donor-condition profiles within each declared population | Donor plus condition and admissible technical terms; separately qualify labels, pairing and missingness |
| P-P | Matched donor-population profiles; relative-expression population contrast | Donor plus population and admissible technical terms; initial candidate labels defined without target RNA, with sampling/selection assumptions assessed; explicitly test composition/reference sensitivity and missing coverage |

The parent's candidate floor is at least three independent units per condition
or three complete biological-unit pairs, with positive estimator-appropriate
residual information. It is an admission proposal, not adequate power or a
qualified minimum. Test that boundary; two-unit cases remain negative or
exploratory controls. Numerical capacity limits, final replication floors and
supported covariate/missingness ranges are **unratified**.

For S-I/S-P, include an admissible orthogonal/independently established label
regime. A frozen external RNA classifier is a separate candidate requiring
task-specific qualification. For P-P, an external classifier applied to the
tested RNA still selects on that RNA; imported copies do not erase selection.
Unknown or outcome-selected labels remain exploratory unless a complete
selection-aware or independent-measurement procedure is separately qualified.
Orthogonal labels themselves do not prove biological identity or absence of
confounding.

No cells means a missing population profile, not an observed all-zero profile.
Record donor/population cell counts, depth, annotation uncertainty and
informative missingness. No universal cell-count cutoff is adopted. Complete-pair
restriction or conditional-on-observed-population inference requires an
explicit target and qualified missingness regime; it cannot silently remove
units or repair condition-dependent absence.

Bind `contrast_pair_subcohort` before outcome access for the initial two-level
procedure, retaining the original inventory. Joint multi-condition models,
interactions and other designs do not inherit that qualification. Technical
merges require common-origin/disjoint-contribution evidence and compatible
measurement/reference and technical-factor handling. Test complete confounding,
partial overlap, valid redundant-alias repair and misleading covariate cues.

## 2. Questions, partitions and independent ownership

The evaluation asks whether the complete declared procedure is numerically
faithful and calibrated within its envelope, and whether the integrated
controller/challenger produces valid useful answers with acceptable errors,
holds, burden and cost. X8 separately asks about an actual outcome-adaptive
inferential/reporting policy. Execution success is not a method-selection
result, biological validation or clinical efficacy.

| Role | Required responsibility and separation | Assignment/resources |
| --- | --- | --- |
| Statistical qualification owner | Numerical tolerances, sampling/power/stopping, strata, confidence allocation, calibration and independent decision | Unassigned; resources unratified |
| Domain qualification owner | Answerability, consequential-error taxonomy, label/target applicability, claim adjudication and blinded case validity | Unassigned; resources unratified |
| Evaluation custodian | Study lineage/partition roster, hidden references, schedule, sealing and post-seal scoring; denies participant access | Unassigned; resources unratified |
| Runtime owner | Source/environment identities, grants, execution/accounting/fault receipts; cannot self-certify scientific gates | Unassigned; resources unratified |
| Product/release owner | Utility/cost tradeoffs, supported scope and release decision with independent scientific sign-off | Unassigned; resources unratified |

Codex is the document recorder, not an assigned independent qualification
owner. Personnel, conflicts, access rights and resources must be recorded before
campaign admission; a model vote or user acceptance cannot substitute.

Partition at the **underlying study** level into development, disjoint pilot
and locked qualification. All derived views, perturbations, related variants,
repeated seeds and model runs of a study stay in one partition and retain one
cluster identity. Shared biological sources must be identified before assigning
partitions. Split IDs alone do not prove disjointness. Development may guide
implementation; disjoint pilot evidence may prospectively revise scope,
power, gates and budgets; locked qualification evaluates the frozen candidate.
Pilot or qualification outcomes exposed to development cannot be relabeled as
untouched qualification data.

The independent custodian retains hidden answers and disjoint qualification
variants outside participating coding/analysis-agent access. Public airway,
replicated IFN/LPS and known traps are development/reference material, not
hidden truth. SEQC mixtures/technical controls support specific quantitative
checks, not donor replication. Frozen schedules include clean controls and
F1–F7, five productive regimes, U2 constructive return/recomputation, U3 real
non-DE chains/verified figures, U4 protected biological-unit validation and U5
burden/stopping. Exact rosters, study counts, variant generation, sampling
distributions, seeds where supported, and power/stopping plans are unratified.

## 3. Methods, comparators and campaign controls

### Procedure and family envelope

ERQL-1.1 is the parent's initial candidate: edgeR 4.10.7 v4 QL, robust
moderation, TMM, primary-factor filtering and local BH with fixed family
allocation. T03 must resolve and verify the compatible R/package/numerical
library/image set and expanded defaults. Retain the parent STAT-02 procedure
exactly, including native adjusted residual information, actual denominator
capping and effective dynamic `top.proportion`; the old RLE temporal worker
is a separate procedure. Different estimators such as DESeq2/voom, TMMwsp or
alternative filters are development candidates, not automatic result-selected
fallbacks. A changed scientific procedure needs a new identity and evidence.

Before relevant outcomes, freeze disjoint `(population, contrast)` families
and nonnegative weights summing to at most one. Every requested primary
inferential family receives positive weight; the parent's default is `1/C`,
including requested families later empty, unsupported or withheld. One family
has weight one. Retain measured/filtered/eligible/attempted/failed/tested/reported
statuses. Bind the filter first, then its realized eligible hypothesis set.

At nominal request alpha 0.05, each complete local procedure is evaluated at
its actual `alpha*w_c`; `q_local` is local BH and
`q_budget=min(1,q_local/w_c)` supplies request-level flags. A zero-weight
inventory entry permits no inferential discoveries and has null `q_budget`.
The tested family-count/weight grid and smallest supported allocated level are
unratified; calibration at 0.05 alone does not qualify all smaller levels.

Qualify local eligible-gene failure handling versus shared fit/normalization/
design failure, fixed-weight partial family publication versus whole-request
withholding, and their utility cost. A local failure can retain an internal
conservative adjustment position only under the qualified missing-test policy;
public estimates remain missing. Shared failures hold every dependent family.
Whole-family publication/withholding preserves the fixed inventory; no budget
recycling, post-result subdivision, gene pruning, pooled-BH fallback or arbitrary
failed-gene percentage rule is allowed. Candidate failure-policy identities and
their exact scope require independent ratification before qualification.

### Matched comparisons

| Arm | Frozen meaning and required execution identity |
| --- | --- |
| A0 | Source-pinned current product on explicitly enumerated common scope; preserve its actual numerical/review capabilities and limitations |
| Direct-R | Exact locked ERQL-1.1 procedure on identical bound counts, design, contrast and families; same-procedure numerical oracle, not a controller or biological adjudicator |
| B | Deterministic adaptive policy with frozen condition/action rules, diagnostic policies and stopping; no undeclared model judgment or result-selected method menu |
| C | Scientific controller with the common numerical/exploratory tools and authority/publication constraints; no active challenger |
| D | Controller plus active challenger at design and prepublication touchpoints, fresh evidence-first assessment before controller narrative/plan, entitled queries and enforceable scoped holds |
| D-late | Source-pinned executable old late-review arrangement, evaluated as a separately named ablation; never an alias for D or the old fixture's `sequential_second` |

For D-late, the future executable adapter must schedule no pre-fit challenger
and invoke the retained late-review implementation after the analysis packet
is assembled and before its dependent interpretation is published. Freeze its
actual callable entrypoint, source/prompt hashes, packet order, available
evidence/query capabilities, review/hold powers and termination policy in the
arm manifest. Record which capabilities differ from D, so timing is not
confounded with undisclosed evidence or tools. Its source binding, runnable
adapter and smoke receipt are **unassigned/not run**; prose alone does not make
the arm executable. If the retained arrangement cannot run on common scope,
record that limitation and block any comparison requiring it. Do not fabricate
a reconstructed capability or silently replace it with another arm.

Use identical study inputs, method opportunities, evidence access, clarification
opportunities/answers and intervention rules on common scope. Pin candidate
models, prompts, tool contracts and runtime versions; record seed support and
uncontrolled randomness. Pair comparisons by underlying study and use a frozen
execution-order policy. Oracle metadata are permitted only in a separately
labeled engine-isolation experiment, never secretly in one user-experience arm.

Match **total** budgets, including controller, challenger, queries, numerical
jobs, retries, reviews, reconciliation and terminal reporting. Extra methods or
resources are capability changes. Count actual use as well as ceilings; an
unsupported hard-token cap must not be advertised as enforced. The parent
EXEC-09 development ceilings are engineering proposals, not a campaign grant.
Actual token/call/query/job/CPU/GPU/memory/storage/transfer/wall/spend limits,
review reserves and comparable arm allocations are **unratified**.

X1 separates review timing from evidence-first packet-order effects; X2 varies
challenger family/query scope without assuming a second provider or query cap.
X9 separates Literature access from retrieval quality with common pre-supplied
source-packet controls under matched models, numerical output and total budget.
Capture/access correctness and incremental usefulness have separate gates.

### Separate grants before execution

| Workload | Required authority and current boundary |
| --- | --- |
| Local software/synthetic checks | Existing P0 authorization applies only to its named bounded checks; record actual commands and receipts; no scientific qualification follows |
| Numerical/statistical simulation | Future explicit data/compute/wall/spend grant, frozen generating scenarios and candidate procedure; unassigned/unratified |
| Model-based scientific evaluation | Future exact model/data/capability/protocol grant, matched budgets, protected references and accounting admission; unassigned/unratified |
| Each live remote backend/workload profile | Future profile-specific grant and live lifecycle acceptance; no local or fake-driver pass transfers authorization |

Controller-created branches, retries and resumes share spend and exposure
history. Missing permission or unknown usage is not an unlimited grant or zero
cost. Reserve cleanup/reconciliation and truthful termination capacity. No
private clinical inputs are admitted to external model services.

## 4. Prospective endpoints, denominators and confidence

The following targets come from parent EVAL-03/04 and implementation section 8.
They remain prospective and require disjoint-pilot ratification, endpoint
operationalization, independent ownership and joint power planning. They are
not observed results, approved tolerances or promised deadlines.

| Gate | Endpoint and denominator | Prospective decision and unresolved fields |
| --- | --- | --- |
| Critical integrity | Accepted critical violations; report case, opportunity and underlying-study denominators with uncertainty | Zero accepted critical violations in frozen suite; finalize taxonomy/opportunity enumeration and uncertainty method |
| Numerical agreement | All expected bound quantities and statuses on same-procedure reference cases; include failed/missing values | Exact counts, IDs, designs, contrasts and family membership; quantity-specific tolerances unratified |
| Calibration | Study-level FDP for each complete local procedure at actual allocation and reporting scope; separate scenario strata | Nominal alpha 0.05; simultaneous local FDR upper bounds sum to at most 0.06 per stratum, or a prospectively justified alternative guarantee for the actual reporting rule; bounds/allocations/power unratified |
| Review detection | Consequential errors detected among eligible corrupted packets/errors; cluster touchpoints by study | Parent proposed point target at least 95%; exact severity rules, denominator and interval method unratified |
| Initial erroneous holds | Initially erroneous holds per assessed valid packet; retain later resolved holds | Parent proposed point target at most 10%; adjudication/interval rules unratified |
| Terminal false blocking | Answerable assigned studies incorrectly blocked at terminal state | Parent proposed point target at most 5%; separate resolved holds, harmful closures and burden; rules unratified |
| Useful completion | Valid useful completions among all assigned studies in predeclared answerable strata | Proposed lower 95% confidence bound at least 0.85; answerability/usefulness rubric, joint confidence allocation and signal/utility floors unratified |
| Routine/adaptive benefit | Paired study outcomes for D versus B | Routine noninferiority with prospectively justified initial five-percentage-point margin; positive paired benefit for adaptation-required useful-completion or scientist-effort endpoint; operationalization unratified |
| Challenger benefit | Paired consequential-error outcomes for D versus C at matched total budget | Positive benefit bound plus false-hold gates; bound/denominator/allocation unratified; inconclusive keeps challenger in preview |
| Runtime/reporting | Required C1–C11, D1–D9 and F7 outcomes through actual routes, all attempts/publication units/figures | One accepted result per binding/unit; current claim/figure links, partial-output and cleanup visibility; complete executed matrix required |
| Cost and burden | All assigned studies and separately valid useful answers | Cost per assigned study/useful answer, p50/p95 latency, failures, retries, reviewer calls and human assistance; deployment limits and intervention rules unratified |

The numerical tolerance schedule must name, for each quantity, scale,
absolute/relative or log-scale comparison, near-zero rule, boundary/nonfinite/
missing handling and applicable regimes. It must cover library sizes/offsets,
normalization factors, effects, native QL F statistics, numerator/denominator
df (including actual `df.total` capping), `df.prior`, `df.residual.adj`,
`s2.post`, raw p-values and independently recomputed local/budget q-values.
Boundary zeros, extreme probabilities, one/two detected pairs, sparse/unequal
depth and small retained panels need explicit coverage. Correlation or Jaccard
alone cannot pass numerical equivalence. All numerical tolerance values are
currently **unratified**, not implicit defaults or zero.

For independent study draw i, retain `FDP_i = V_i / max(R_i,1)` at the actual
qualified reporting scope. FDR is the mean study FDP, not pooled false/total
discoveries. No-result studies contribute zero FDP and remain in failure and
utility denominators. Discoveries already exposed by partial/failed runs stay
counted if subsequently withdrawn. Repeated runs/variants cluster by study.
Unscheduled retries cannot create extra independent draws or replace failures.

For the parent's candidate fixed-sample empirical Bernstein bound:

```text
sample_variance = sum((FDP_i - mean_FDP)^2) / (n - 1)
U = min(1, mean_FDP
           + sqrt(2 * sample_variance * log(2/delta) / n)
           + 7 * log(2/delta) / (3 * (n - 1)))
```

Require independent studies, `n >= 2`, Bessel-corrected variance and frozen
sampling/stopping. Confidence error `delta` is distinct from testing alpha.
For heterogeneous independent draws, the target is the prespecified average
of expected FDPs, not every setting or an unspecified deployment mixture.
Keep separate scenario-stratum claims and allocate simultaneous confidence
across all local families, strata and jointly asserted release bounds. The
global confidence-error budget and every allocation remain **unratified**;
the proposed 95% completion bound is not a free allocation for every gate.

Exact binomial bounds may serve a declared global-null any-discovery endpoint.
Reporting-conditioned claims require a separate endpoint, frozen minimum
reporting denominator and evaluation of the actual policy in designated
signal-bearing strata. Unconditional fixed-procedure evidence cannot certify
stochastic reviewer selection. Zero false discoveries in every valid null
study is not the integrity gate. Separate signal/utility floors prevent an
always-silent policy from passing.

Parent arithmetic controls at mean FDP 0.05 and zero variance are approximately
0.093253 for `(n=200, delta=.05)`, 0.120252 for `(200,.005)`, 0.064370 for
`(600,.05)` and 0.064225 for `(900,.05/6)`. They are future accounting checks,
not recommended sample sizes, performed simulations or qualification results.
No study count, optional-stopping rule or alternative bound is selected here.

## 5. Required admission validation and evidence packet

T09/T23 must fail closed before qualification compute. Document completeness
checks are distinct from this future executable protocol validator. At minimum
it must reject:

1. Missing/ambiguous scope, candidate identity, required regime, exact
   label/coverage/family-weight envelope or unqualified procedure substitution.
2. Unassigned independent owners; absent grants, budgets, reserves,
   intervention policy, sampling/power/stopping design or utility floors.
3. Missing or unspecified quantity-specific numerical tolerances; omitted
   boundary/failure semantics; defaulting null/unratified values to a pass.
4. Related study variants, biological sources or repeated runs crossing
   development/pilot/locked partitions; missing lineage or hidden-answer access
   by participants.
5. Unallocated simultaneous confidence claims, allocation overspend,
   pooled-gene/discovery substitution, repeated-seed inflation or an absent
   reporting-conditioned denominator.
6. Missing/non-executable required arms, unmatched evidence/tools/clarification/
   total budgets, undisclosed capability changes or absent invocation receipts.
7. Lost failed/partial/timeout/blocked/unknown-usage attempts, ignored exposed
   discoveries, missing assigned-study denominators or unrecorded interventions.
8. Post-result gate, tolerance, endpoint, stratum, weight, bound or stopping
   changes offered as a pass of that campaign. Preserve the original outcome;
   create a new prospective version and disjoint qualification where needed.

Before locked qualification, retain a signed-off prospective packet with named
independent roles and access separation; source/protocol/evaluator identities;
T03 installed worker and direct-R receipts; frozen study/partition/arm manifests;
the numerical tolerance schedule; exact endpoint, denominator, confidence,
power/stopping and utility definitions; grant/resource/clarification/intervention
rules; and executable admission tests. “Signed-off” requires an attributable
approval record; a local hash is not an authenticated electronic signature.

After execution, independently retain the full assignment/attempt ledger,
actual tool/model/worker invocations, exposure/publication histories, error and
hold adjudications, cost/unknown-usage records, fault/entrypoint evidence,
current numerical/figure links, immutable results and score-replay receipt.
Only then may independent owners assess a scoped qualification decision.

## 6. Current outcome and remaining uncertainty

This revision fixes the protocol identity, source attribution, mandatory regime
and comparison coverage, evidence boundaries and fail-closed requirements.
It contains no experiment results, verified scientific plots or qualification
decision. Named owners/resources, exact tested envelopes, D-late executable
binding, numerical tolerances, study roster/counts, power/stopping, confidence
allocations, signal/utility floors and actual grants remain unresolved.

Those omissions block campaign admission and T02 acceptance; they are not
waived by document delivery or by a T03 installation smoke. Independent
engineering work may proceed within its authorized scope. Any failing regime
stays unavailable; prospective redesign and disjoint requalification may narrow
its published envelope, but P-P or another required regime cannot be omitted
while declaring full R1 complete. A local research release cannot imply remote
qualification, whole-agent FDR control or clinical validity.
