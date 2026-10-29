# RNA-seq Publication Grounding and Comparison Handoff

Date: October 9, 2026. **Documentation-only handoff; no code implementation or scientific run is performed by this task.**

Implementation plan: [RNA-seq publication grounding and question-adaptive comparison](superpowers/plans/2026-10-09-rna-publication-grounding-comparison-implementation.md).

## User scope to preserve

- Keep the current publication grounding. No Deep Research API integration.
- Research and produce the implementation plan/handoff first. Do not change code, tests, dependencies or runtime configuration in this phase.
- The user's pasted pipeline/metrics were ideas, not prespecified requirements.
- Confirmed design requirement: **shared evidence framework flexible across RNA-seq questions, with specific endpoints and comparison rules chosen per question before analysis**.
- No publication, dataset, scientific method, parameter, model, budget or trial is selected or authorized by these documents.

## Recommended implementation

Extend the current evidence, planner, executor and review services with versioned assessment plans and question-specific profiles. Keep methodological adequacy, publication agreement and interpretation separate; no universal weighted correctness score.

Use a two-step freeze: an immutable question-specific **assessment intent** binds profiles/rules/reference/exposure before workflow generation; the finalized **assessment plan** adds the generated workflow and derived-input hashes before data execution. Rules cannot change during finalization. Generation and execution permissions stay distinct. Actual output hashes belong to a separately sealed post-run bundle, never a backfilled pre-run plan.

The framework can describe different RNA-seq questions. Computational support is advertised only when the required input adapter, metrics, renderer and execution route exist and are qualified. Unknown/incomparable/missing evidence produces an explicit reason and abstention, not a fabricated score.

Publication grounding remains identity + frozen source bytes + exact excerpts. Add current primary-source acquisition, version/correction/rights evidence, structured table/supplement extraction and separate semantic-support review. A verified quote is not proof that it supports the claimed biological conclusion.

## Current capabilities versus proposed work

| Area | Current verified state | Next extension |
|---|---|---|
| Sources | Exact Crossref DOI metadata, local identifier matching, approved bounded text retrieval, source hashes/excerpts. | Current provider adapters, asset/collection manifests, structured reviewed extraction and article–dataset linkage. |
| Comparison | Curated scalar endpoints with frozen tolerances/direction/uncertainty rules. | Assessment `/2`, applicable profiles, coordinator-owned output adapters, table/set/rank/curve comparisons where justified. |
| Assays | Bounded registered bulk matrix JSON and metadata JSON. | Governed execution-input bridge; later assay-specific contracts. No general sc/sn/AnnData, junction or raw-read admission today. |
| Planning/execution | Existing scientific-first planner and governed execution. Trial scaffolding exists. | Purpose-separated bindings, real trusted one-use authority/campaign controls and actual admitted end-to-end route. |
| Figures | Typed artifacts; actual observation-point G02/G08 rendering. | Selected bulk/sample/gene/curve renderers and pixel evidence; no pixel-style scientific score. |
| Review | Separate bounded review services, with qualification/independence not established. | Trial/comparison evidence projection and independent review/report integration; model opinions stay advisory. |

Do not repeat the older October 7 document's statement that C4 files are wholly absent. They now exist, but real execution/integration remains unqualified.

## Critical workflow seams

1. **Keep `/1` stable.** Existing evidence/scalar contracts and historical seals must not be rewritten. New behavior uses an explicit `/2` path and compatibility tests.
2. **Resolve the format bridge.** Current bulk bindings are JSON artifact IDs; registered tools consume file/table inputs. Retain original dataset refs and separately derived execution refs with exact transform/value/sample provenance. Do not weaken direct ID checks to admit unrelated files.
3. **Select from the question.** Gene DE, time/trajectory, pathway, splicing, cell state/abundance, regulation and prediction require different estimands/evidence. Gene totals cannot recover per-cell or exon/junction facts.
4. **Keep source statistics.** Preserve full tested families, annotation/mapping coverage, missingness, adjustment/shrinkage and contrasts. Do not convert TPM/FPKM to counts, overwrite missing statistics, or recompute BH only on matched/significant genes.
5. **Reference agreement is scoped.** A published selected list is not biological ground truth. Partial highlighted lists cannot support exhaustive recall; extra genes are not established false discoveries.
6. **Treat exposure honestly.** Current projection hides outcome excerpts/paths, not all file/process/model knowledge. Actual grants/negative controls and prior/public exposure must be recorded; never claim blinding from projection alone.
7. **Preserve admission blocks.** Live workflow generation currently lacks campaign binding; native admission is disabled; trusted `TrialAuthority` has no production default. A serial/mediated alternative is not automatically qualified. No guessed execute command should be issued.
8. **Retain failures and review gaps.** T14/Harmony-dependent methods require an explicit dependency decision; do not silently install/fix/bypass them or turn software tests into scientific evidence.
9. **Constrain source parsers.** JATS/XML extraction must not resolve external DTD/entities, network resources or XInclude. Bound expansion/depth/nodes and test hostile local-file/network references. PDF/OCR tools must not execute scripts or fetch embedded resources outside the approved source grant.

## Proposed implementation ownership

Paths below are repository-relative; new files are proposed, not created code.

| Lane | Files / responsibilities |
|---|---|
| A — Evidence | Extend `devbench/analysis/rna_publication_evidence.py`; add proposed `rna_publication_sources.py` and `rna_reference_extraction.py`. Source identity, snapshots, extraction and provenance only. |
| B — Assessment contracts | Add proposed `devbench/analysis/rna_publication_profiles.py`; extend comparison `/2` dispatch in `rna_publication_comparison.py`. Applicability, projections and immutable metric policy. |
| C — Inputs/outputs/metrics | Add proposed `rna_execution_inputs.py`, `rna_comparison_outputs.py`, `rna_publication_metrics.py`; coordinate trial binding changes. Governed format lineage and deterministic results. |
| D — Figures/review | Extend `figure_artifacts.py`, `scientific_review.py`, `scientific_review_agent.py`; add proposed `rna_publication_report.py`. Actual figure evidence and separate review axes. |
| Coordinator — Trial integration | `rna_scientific_trial.py`, `rna_scientific_trial_contract.py`, existing planner/dispatcher and CLI seams. One writer owns shared comparison/trial files per epoch; parallel lanes must not revert one another. |

Use exact existing APIs described in plan section 2. Preserve `ScientificMethodPlanner`, `MethodService`, registered MCP/ToolDispatcher evidence capture, immutable artifacts and existing governed method execution; do not build a second orchestrator.

## Start here after implementation authorization

1. Recheck the current source/test baseline and the plan's primary source contracts. Capture a fresh epoch; do not assume installed13 qualifies new source.
2. Implement Task 1 closed assessment/applicability contracts and projections, with genuine failing cases first. Keep methods/thresholds unselected until a question/protocol justifies them.
3. Implement source/reference custody and the governed input/output bridge. Choose the first computational profile from the actual proposed question/data; preserve unsupported capabilities explicitly.
4. Add that profile's metrics/figures, review packets and report. Test invalid-method/high-agreement and valid-method/disagreement cases separately.
5. Qualify only the exact execution/backend route needed for the selected case, with authenticated purpose-separated one-use authority, preventive bounds and participant-boundary evidence.
6. Obtain a separate bounded publication–dataset–question trial approval, execute once and independently assess actual results. Documentation and synthetic tests are not that approval or execution.

There is no actual scientific-run command in the current CLI: it supports preparation/revalidation. A future command must come from the implemented, tested parser and exact accepted artifacts.

## Verification and first-case acceptance

- Tests run from a fresh ordinary installed wheel with frozen source/test/runtime identities and retained JUnit/results. Do not run product tests from the mutable source checkout or reuse a historical epoch.
- Extend the tracing verification selection/bindings explicitly for publication modules, rather than assuming its frozen historical inventory covers them.
- Verify `/1` compatibility, wrong source/assay/contrast/annotation/mapping, duplicate units, confounding, missing families, probability semantics, unknown profiles, malformed/censored references, reference leakage, review evidence omission, approval replay/expiry and partial failures.
- For any selected gene-set metric, report source/mapped/eligible/unknown counts and predeclared thresholds. Empty/incomplete sets do not receive invented perfect agreement.
- Produce actual rendered figures when the question requires them. Preserve missing pixels or reference plot data as not assessed.
- A first real case needs an accessible reference with assessable findings, a compatible processed dataset and known processing lineage, audited samples/units, an identifiable question, a justified frozen workflow, accepted comparison rules and an admitted execution route. No default paper, DE threshold or method is chosen here.
- Freeze/seal decisions and outputs before evaluator-held reference outcome release where the selected mode calls for that separation. Record public/prior exposure honestly and preserve all discrepancies and operator interventions.

## Online research findings that affect implementation

- PMC's old OA service retired in August 2026. Current cloud/article dataset routes and exact object metadata must replace legacy endpoint assumptions. Same version prefixes can still have changed bytes; hash the acquired assets. [PMC service notice](https://pmc.ncbi.nlm.nih.gov/tools/oa-service/), [cloud/version documentation](https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/).
- Crossref/NLM correction links and publication version relations are evidence with coverage limits; absence of a notice is not proof of its absence. [Crossref](https://www.crossref.org/documentation/retrieve-metadata/rest-api/), [NLM notices](https://www.nlm.nih.gov/bsd/policy/errata.html).
- Repository-generated counts may be a different processing lineage from the published/submitter results. Some live GEO and Europe PMC endpoint documentation could not be inspected; reverify exact routes before implementation. [NCBI counts announcement](https://ncbiinsights.ncbi.nlm.nih.gov/2023/04/19/human-rna-seq-geo/amp/).
- RNA-seq methods, tested families, biological replication, enrichment backgrounds and trajectory targets have distinct assumptions. The plan's source table provides primary references and their applicability limits; none supplies universal thresholds or method winners.

## Completion evidence for this documentation task

Completed: independent architecture/security review accepted the revised documents; existing-file references and the local plan link were checked; Markdown fences are balanced; both proposed Python examples parse as valid syntax; no unresolved draft placeholders remain. The before/after check matched all **569 enumerated product-source, test, script, integration and build-configuration files**, with no code additions/removals in that scope.

Primary-source access limitations are classified in the plan rather than represented as successful endpoint verification. No product tests, models, datasets or scientific analyses ran for this documentation task. The two code snippets are future acceptance examples, not executed implementations or results.

Document-validation and code-preservation records are retained under `reports/rna-publication-grounding-plan/2026-10-09-v1`. Implementation and scientific qualification remain pending separate authorized work.
