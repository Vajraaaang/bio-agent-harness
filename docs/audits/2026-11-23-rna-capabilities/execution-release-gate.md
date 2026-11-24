# RNA execution coverage and release gate

The [execution inventory](../../../src/devbench/platform/rna_execution_coverage.py) derives candidate rows from the registered question, assay and entry-point contracts. It expands alternative required measurements and curated operations separately. An unrestricted assay in the question registry contributes all assay categories to the candidate inventory. That describes the intake contract. Actual supported combinations come from exact runtime bindings and promises in the release manifest.

## Coverage obligations

The default inventory contains **20 families, 39 registered questions and 1,177 candidate rows**. The empty-evidence baseline contains 419 candidates with a named operation, 740 without a curated operation in their question contract, and 18 whose required measurement needs an explicit entry context. Running the release command supplies observed execution evidence. Custom registrations can supply the entry context where their authenticated fixture demonstrates the required evidence. A registered custom implementation earns separate coverage from any promised curated implementation.

| Family | Candidate rows | Execution obligations |
|---|---:|---|
| 1 Expression and cell state | 57 | Graph bindings and executed fixtures for gene expression and donor pseudobulk |
| 2 Cell identity | 14 | Registered cell identity route with declared label and representation scope |
| 3 Abundance and composition | 13 | Graph bindings and fixtures for scCODA, neighborhood abundance and deconvolution |
| 4 Splicing and transcript usage | 72 | Transcript and exon usage graph bindings. Registered splice-junction usage executor remains open |
| 5 Transcript discovery | 18 | Transcript structure assembly or reconstruction executor is unregistered |
| 6 Pathways and programs | 81 | Graph bindings and fixture coverage for the ranked, competitive, overrepresentation and regulon routes |
| 7 Development and time | 67 | Observed-time and donor-trajectory graph bindings and fixtures |
| 8 Integration and comparative RNA | 71 | Integration graph bindings. Cross-species comparison needs a registered ortholog-aware executor |
| 9 Spatial organization | 14 | Spatial pattern graph bindings and a registered spatial domain executor |
| 10 Cell communication | 6 | Graph bindings and fixture verification for reference-linked expression |
| 11 Perturbation | 63 | Graph bindings and fixture coverage for the permitted perturbation designs |
| 12 Allelic expression and QTL | 36 | Registered executors for allele-resolved tests and matched-genotype QTL |
| 13 Fusion and editing evidence | 27 | Registered executors that preserve evidence-specific inputs and reference scope |
| 14 Small and noncoding RNA | 38 | Specialized small-RNA, backsplice and repeat execution bindings |
| 15 RNA processing and translation | 150 | Registered velocity, turnover, modification and translation executors. Modification additionally needs an explicit entry context |
| 16 Organism-resolved inventory | 18 | Scoped descriptive inventory executor and fixture |
| 17 Modalities and clonal analysis | 261 | Registered paired modality, clonotype and lineage executors. Lineage concordance additionally needs an explicit entry context |
| 18 Phenotype prediction | 63 | Graph bindings with registered independent partition and verified prediction readback |
| 19 Reproduction and replication | 99 | Registered published-protocol and study replication workflow bindings |
| 20 Method development | 9 | Registered benchmark execution with pinned baseline and independent evaluation |

## What qualifies a route

The release coordinator supplies the trusted versioned registry through `ExecutorBinding` and any additional explicit obligations through `promised_routes`. Each binding pins the operation, accepted file formats, version, executor source digest and runtime digest. Registered custom methods also pin their method contract. The default call has no run observations and cannot pass.

The coordinator's evidence reader must check the launched installed plugin and fixture, the exact approved plan, the current accepted adaptive attempt and receipt, verified output bytes, and an independent request to Studio. It returns `VerifiedRun` with both graph and Studio `ReceiptReadback` records. The gate compares their session, plan, step, attempt, accepted record, runtime, executor, receipt and artifact identities. The reader should return the original specific blocker if a run has one. A runtime readiness model, a caller assertion, and an older standalone run each lack this evidence.

`make test-rna-release` runs the installed-plugin acceptance fixtures. The release gate requires a verified execution for every registered question and every versioned binding or explicit route promise, plus verified offline private execution and multi-family resumption. Multi-family resumption requires two completed, individually approved steps in the same investigation, with a dependency on the upstream receipt and a verified restart from that receipt. The motor-neuron investigation exercises an acceptance route. Its receipts are limited to the steps and scientific scope they actually verify.

Successful fixture execution establishes the exercised software path and evidence custody. Independent comparative validation and clinical release remain separate decisions.

## Workflow and approval scope

Native plugin steps retain the admitted input digests, runtime version, and exact plan digest. A follow-up can select a subset of originally admitted inputs and pin an earlier plan in the same investigation. Each dependency resolves to a current accepted graph output before execution. Registered OCI methods bind named current artifact versions to their typed input ports. An execution approval applies to one pinned plan. The graph records the actual attempt, receipt, and verified material outputs.

Offline Studio exposes the same registered private plan review and result projection. The installed motor fixture exercises an exact private marker-program plan followed by a UMAP plan dependent on its receipt. UMAP still uses the supplied cell-state labels. The registered private spatial fixture exercises an ordinary solver through offline review. MCP continues to withhold private result data.

Completed historical investigations retain their original evidence. Executing an older unstarted native plan requires replanning against a registered graph runtime identity.

## Observed installed-plugin run

The local acceptance command completed six fixtures. The gate matched nine completed routes in families **1, 3, 4, 6, 9, 10, 11, and 18**. The exercised routes were bulk differential expression, neighborhood differential abundance, bulk deconvolution, transcript usage, tested-background overrepresentation, spatial pattern, cell communication, perturbation response, and phenotype prediction. Every counted result matched its approved plan, current accepted graph attempt, and separate Studio readback.

The offline-private and multi-family resume checks both passed. The motor and registered custom-method acceptance fixtures also passed. The motor check used synthetic AnnData, and the custom OCI method consumed a checked native JSON result. These checks do not add scientific route coverage beyond the counted bindings.

The release check remains blocked on **30 of 39 question contracts**. In the candidate inventory, 410 rows need an executor binding or a qualifying run, 740 have no curated runtime, and 18 require a registered entry context. The result records engineering execution coverage for the exercised formats and runtimes.
