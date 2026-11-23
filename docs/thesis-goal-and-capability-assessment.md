# Thesis research goal and capability assessment

## Goal and baseline

Build a single-cell and single-nucleus RNA-seq research agent that can formulate
connected aims, determine what each assay measures, adapt to conflicting evidence,
and deliver a replayable thesis package. Harden the existing bulk path alongside
the single-cell work. Keep an answerability record for all 20 families and a
governed extension path for unfamiliar research questions.

The matrices assess the Unified Hybrid implementation on
the current public implementation against the [answerability registry](../src/devbench/core/rna_answerability.py). Earlier development evaluations exceeded the frozen null-FDR ceiling of `0.075` and cross-architecture probability tolerance of `1e-10`. This assessment describes implementation scope. Independent public review and biological and clinical qualification remain open.

The [repository runtime rules](../AGENTS.md) apply at every entry point. Work
from authorized local inputs, download no BAM or FASTQ archives, and scan large
expression matrices in bounded chunks. The pandas limit is 100 MB. Preserve
`0 <= p <= q <= 1` for confirmatory gene statistics and record unavailable
estimates as null. Any authorized operator or researcher can approve a research
aim or an operator-review gate, with identity, timestamp, and rationale recorded
in `WorkflowOperationLedger`. A local actor label is an attribution record.
`clinical_release_allowed` stays false and private clinical inputs stay off external
model services.

**Implemented** means a usable code path matches the stated scope.
**Validated** requires an independent comparison for that scope with predefined
success criteria. **Partially Supported** means a narrower path or component
exists. **Missing** means the required scientific execution or verification path
could not be located in this revision. The scope column names what each rating
covers. For all 20 families, the [answerability registry](../src/devbench/core/rna_answerability.py)
and its [measurement probes](../tests/test_thesis_answerability_matrix.py)
check the required evidence. A gate or a Tier B extension contract alone does
not establish scientific execution.

## Research problem families

| # | Family | Status | Current evidence and boundary |
|---|---|---|---|
| 1 | Expression and differential cell states | Implemented | [Mounted bulk](../rna_kernels/mounted_bulk.py) covers gene-level coefficients, general contrasts, LRTs and estimated-count offsets. [General pseudobulk](../src/devbench/platform/scrna_general_pseudobulk.py) stages raw counts per donor and type for multi-factor contrasts and preserves low-cell warnings and absent profiles. The [paired tximport diagnostic](../src/devbench/analysis/paired_tximport_svaseq.py) keeps surrogate-variable evidence separate from the primary fit. Null FDR qualification remains open. |
| 2 | Cell identity, heterogeneity and rare populations | Partially Supported | [Annotation](../src/devbench/analysis/annotation.py) supports marker and reference-backed labeling. [Marker inference](../src/devbench/core/scrna_marker_inference.py) separates exploratory ranks from independently checked NB count-split evidence. Rare-population stability and external annotation accuracy still need study-specific evidence. |
| 3 | Abundance, composition and deconvolution | Implemented | [scCODA](../src/devbench/core/scrna_composition_solver.py) models relative composition at donor level. [Neighborhood DA](../src/devbench/core/scrna_neighborhood_da.py) tests relative abundance conditional on the supplied embedding and reports standard and spatial FDR. [Bulk deconvolution](../src/devbench/core/bulk_deconvolution.py) estimates relative proportions conditional on a matched donor reference. External comparisons and uncertainty calibration remain open. |
| 4 | Alternative splicing and transcript usage | Implemented | [Differential usage](../src/devbench/core/bulk_dtu.py) tests mapped transcripts or disjoint exon bins using replicated contrasts and stage-wise error control. `splice_junction_usage` remains gated. Independent software and data comparisons remain open. |
| 5 | Transcript discovery and annotation | Missing | The [local short-read route](../src/devbench/analysis/fastq_execution.py) generates expression. Transcript reconstruction and support for novel splice junctions and transcript ends require a separate long-read or transcript-structure runtime. |
| 6 | Pathways, gene programs and networks | Implemented | [Governed pathways](../src/devbench/engine/tier_a_pathways.py) cover CAMERA, ranked fgsea, tested-universe ORA and decoupleR activity with receipt-bound inputs. [Network tools](../devharness_server/tools_network.py) report association and motif evidence without claiming direct binding or causal regulation. |
| 7 | Development, temporal response and state transitions | Partially Supported | [Temporal fitting](../devharness_server/tools_stat.py) models observed time. [Donor-aware trajectories](../src/devbench/core/scrna_donor_trajectory.py) test declared pseudotime contrasts using donor-level evidence and guard confirmatory use. Topology sensitivity and independent progression evidence remain study-specific. |
| 8 | Integration, atlas and comparative transcriptomics | Partially Supported | [Embedding](../src/devbench/analysis/embedding.py) and [integration metrics](../src/devbench/analysis/integration_metrics.py) compare uncorrected and Harmony or scVI representations, check graph geometry and require qualified external labels for biological conservation. Cross-study and cross-species harmonization still require compatible references and further evidence. |
| 9 | Spatial organization and tissue context | Implemented | [Spatial pattern](../src/devbench/core/spatial_analysis.py) uses physical coordinates and graphs partitioned by donor and tissue section to test expression patterns and cell-type colocalization. `spatial_domains` remains gated. Independent spatial comparison is pending. |
| 10 | Cell–cell communication | Implemented | [Communication](../src/devbench/core/scrna_ccc.py) tests donor-stratified cell-type specificity and differential donor-level interaction scores with a measured ligand–receptor reference. The claim is correlational coexpression. Direct binding and causal signaling require distinct evidence. |
| 11 | Perturbation and functional genomics | Implemented | [Perturbation response](../src/devbench/core/perturbation_analysis.py) reports stratified energy tests, exploratory efficacy estimates, and replicate-aware raw-count pseudobulk contrasts. Assignment fidelity and causal claims require independent evidence on randomization and guide effects. |
| 12 | Genetic regulation and allelic expression | Missing | The [measurement gate](../src/devbench/core/rna_answerability.py) requires matched genotype and allele-resolved axes. Curated ASE, QTL and mapping-bias workflows are absent. |
| 13 | Transcript abnormalities and cancer-related questions | Missing | [Mounted expression](../rna_kernels/mounted_bulk.py) supports matched gene-level contrasts. Fusion, editing and expressed-variant questions require distinct evidence and specialized execution. |
| 14 | Small, noncoding, circular and repetitive RNAs | Partially Supported | [Bulk execution](../rna_kernels/mounted_bulk.py) compares compatible quantified features with declared provenance. Specialized quantification, back-splice detection and multi-mapping adjudication remain outside the curated path. |
| 15 | RNA production, kinetics, modification and translation | Missing | The [measurement gate](../src/devbench/core/rna_answerability.py) rejects counts-only velocity and checks distinct kinetic, modification and translation requirements. These measurements have no curated execution route here. |
| 16 | Host–pathogen and community transcriptomics | Partially Supported | The [descriptive intake contract](../src/devbench/core/rna_answerability.py) checks organism-resolved inventories. This path is limited to descriptive scope. |
| 17 | Multimodal, repertoire and clonal analysis | Missing | The [measurement gate](../src/devbench/core/rna_answerability.py) checks the linked axes and required modalities or clonotype assignments. The annotated RNA route does not supply curated linked-modality or clonal inference. |
| 18 | Prediction, signatures and phenotype | Implemented | [Phenotype prediction](../src/devbench/core/phenotype_prediction.py) fits binary classifiers with donor- or cohort-grouped nested cross-validation and training-fold preprocessing. The MCP route requires a digest-pinned independent partition. External calibration and transportability remain open. |
| 19 | Reproduction, replication and evidence synthesis | Partially Supported | [Thesis replay](../src/devbench/analysis/thesis_replay.py) reproduces sealed artifacts from authorized inputs, and [literature custody](../src/devbench/analysis/thesis_literature.py) binds assessed corpora. Independent public review and the cross-architecture numerical check remain open. |
| 20 | Experimental design and method development | Implemented | [Design compilation](../src/devbench/core/contrast_design.py) checks estimability. [Tier B benchmarks](../src/devbench/engine/exploratory_benchmark.py) freeze a comparator and negative control, isolate evaluation units and retain outcomes and cost. Their results remain exploratory. |

The seven [local Python MCP routes](../integrations/codex/devharness/skills/rna-workbench/references/mcp-payload-contracts.md#local-python-solvers)
were exercised with synthetic plans, approval, execution, and receipt readback.
The worker retains the full result artifact with a SHA-256 digest and returns
bounded summaries. The evidence covers the exercised execution and custody
contracts. Scientific validation requires a frozen independent comparison for
each claimed scope. `clinical_release_allowed = false`.

## Lifecycle stages

| Stage | Status | Current evidence and boundary |
|---|---|---|
| A · Questions and aims | Implemented | [Thesis aims](../src/devbench/platform/thesis_aims.py) record approval, versioned plans, competing explanations and dependent aims. [Literature receipts](../src/devbench/analysis/thesis_literature.py) bind novelty statements to an assessed corpus. |
| B · Evidence inventory | Implemented | [Intake](../src/devbench/analysis/workbench_episode_intake.py) and [AnnData inventory](../src/devbench/analysis/rna_scrna_input_contract.py) check registered observations and features. The [measurement gate](../src/devbench/core/rna_answerability.py) carries reference and access attestations and records missing evidence explicitly. |
| C · Design and answerability | Implemented | [Design compilation](../src/devbench/core/contrast_design.py) checks estimability, replication and donor aliases. The [family gates](../src/devbench/core/rna_answerability.py) distinguish unsupported claims, missing evidence and unavailable runtimes before dispatch. |
| D · Adaptive planning | Implemented | The [controller](../src/devbench/analysis/episode_agent.py) schedules admitted research using reviewed jobs and opaque aliases. The [compiler](../src/devbench/analysis/pipeline_compiler.py) builds governed branches and [the ledger](../src/devbench/engine/workflow_operations.py) records the evidence that permitted each launch. |
| E · Prepare at input entry point | Partially Supported | [Local short-read processing](../src/devbench/analysis/fastq_execution.py) now includes the pinned `qc-salmon` bridge, with synthetic output and launch-contract tests. [Bounded matrix intake](../src/devbench/analysis/workbench_episode_intake.py) and [raw-droplet cell calling](../rna_kernels/scrna_droplets.py) cover their declared routes. A live `qc-salmon` container run and specialized read-level, spatial and long-read preparation need compatible inputs and runtimes. |
| F · QC and artifact investigation | Implemented | [Mounted single-cell QC](../rna_kernels/mounted_scrna.py) retains per-capture MAD, per-lane doublet and group-retention evidence. The [QC handoff](../src/devbench/platform/scrna_qc_handoff.py) ties downstream donor fits to current masks. Revision withdraws dependent results and preserves history. |
| G · Data representations | Implemented | [Mounted preparation](../rna_kernels/mounted_scrna.py) keeps original counts separate from normalization, feature selection and integration artifacts. [Integration metrics](../src/devbench/analysis/integration_metrics.py) check corrected and uncorrected graphs and gate biological scores on verified labels. |
| H · Execute analyses | Implemented | [Tier A](../src/devbench/engine/tier_a_executor.py) mounts pinned bulk, single-cell and pathway kernels. [ExploratoryOCI](../src/devbench/engine/exploratory_oci.py) runs bounded custom methods. The execution tier and required evidence limit claim scope. |
| I · Competing explanations | Implemented | [Thesis aims](../src/devbench/platform/thesis_aims.py) require current comparison evidence for declared alternatives. Inconclusive assessments are retained and stale evidence can reopen dependent aims. |
| J · Validate and avoid circularity | Partially Supported | [EvidenceBroker](../src/devbench/analysis/evidence_access.py), [NB count splitting](../rna_kernels/scrna_nb_countsplit.py) and [blinded review](../src/devbench/core/thesis_blinded_review.py) enforce independence, provenance and evaluation boundaries. Scientific qualification remains open, including the frozen null-FDR gate and independent public review. |
| K · Develop and evaluate methods | Implemented | [Tier B benchmarks](../src/devbench/engine/exploratory_benchmark.py) evaluate a candidate against a frozen baseline and negative control in isolated partitions and retain runtime and failure evidence. Promotion requires separate scientific review. |
| L · Iterate across aims | Implemented | [Adaptive workflow](../src/devbench/platform/adaptive_workflow.py), [thesis aims](../src/devbench/platform/thesis_aims.py) and [thesis replay](../src/devbench/analysis/thesis_replay.py) preserve dependency versions, invalidations, prior decisions and executable synthesis across sessions. Exact same-architecture replay is exercised. Cross-architecture numerical parity remains open. |

## Entry-point coverage

| Entry point | Status | Required intake and output scope |
|---|---|---|
| Pre-staged short reads | Partially Supported | The [FASTQ path](../src/devbench/analysis/fastq_execution.py) supports declared local counting. `qc-salmon` prepares an offline analysis container, skips alignment, requires a digest-pinned transcript map and records bounded Nextflow trace progress. The launch and output contracts were exercised with fixtures. Require locally staged reads, library layout, pinned references and resource custody. Requests that need archive downloads are refused. |
| Pre-staged long or direct-RNA reads | Missing | Verify local assay signals and annotation prerequisites. Transcript structure needs a separately versioned long-read runtime. |
| BAM or CRAM | Missing | General alignment restart into downstream modality pipelines is unavailable. Require locally staged data, reference match and evidence of the requested signal. |
| Transcript quantification | Partially Supported | The [Salmon bridge](../src/devbench/analysis/fastq_execution.py) combines `quant.sf` with the pinned `tx2gene` map and retains `salmon_gene_counts.tsv`, `salmon_gene_lengths.tsv`, and `salmon_quant_provenance.json`. [Mounted estimated counts](../rna_kernels/mounted_model.py) consume estimated counts with matched effective lengths. The [paired diagnostic route](../src/devbench/analysis/paired_tximport_svaseq.py) preserves the primary 2D offsets. Synthetic fixtures test fractional estimates, weighted lengths, custody and the mounted handoff. |
| Raw gene count table or matrix | Validated | Validation covers synthetic transport and normalization. [Workbench bulk registration](../src/devbench/analysis/workbench_bulk_registration.py) stages plain or gzip CSV/TSV and compatible HDF5 with explicit expression and metadata roles, matching sample IDs, and declared design. [RLE tests](../tests/test_streaming_rle.py) compare streaming factors against the dense reference through 66,001 genes. Frozen inferential calibration remains open. |
| Raw droplet feature-barcode matrix | Implemented | [Workbench inspection](../src/devbench/analysis/workbench_inputs.py) reports bounded dimensions for modern and legacy CellRanger HDF5. The [10x reader](../src/devbench/core/tenx.py) and [droplet kernels](../rna_kernels/scrna_droplets.py) require unfiltered source provenance and an admitted execution format for EmptyDrops or CellBender. CellBender output is quarantined from primary raw-count inference. |
| Normalized expression | Implemented | Declared continuous bulk expression has a distinct [mounted fitting path](../rna_kernels/mounted_bulk.py). Preserve transformation history and restrict the applicable estimand. |
| Annotated single-cell or nuclear object | Implemented | [AnnData intake](../src/devbench/analysis/rna_scrna_input_contract.py) checks count layers and axes. Studio accepts 10x HDF5 and exploratory registration with an undeclared independence field. [Mounted single-cell execution](../rna_kernels/mounted_scrna.py) supports bounded verified QC and representation preparation. Confirmatory inference still requires the design, donor, capture and label provenance appropriate to the question. |
| Spatial or linked multimodal measurements | Partially Supported | The [measurement gate](../src/devbench/core/rna_answerability.py) verifies relevant coordinate, segmentation or paired modality evidence. Integrated scientific execution requires a suitable specialized backend. |
| Prior results and receipt-backed tables | Implemented | [Plot verification](../src/devbench/analysis/scientific_plot_verification.py), [synthesis](../src/devbench/analysis/thesis_synthesis.py) and [replay](../src/devbench/analysis/thesis_replay.py) support bounded inspection, verified exports and compatible replay. Replay preserves the original evidence scope and dependencies. |

## Multi-aim architecture

- Open Companion Studio with `devbench workbench`. A question can start with
  local description, a bounded audit, or an intake request for missing
  measurements and experimental design. Each investigation exposes the A–L
  lifecycle in Workflows, with the aim matrix in Project details. The scientific
  session owns adaptive lineage, Tier A runtime, and receipt delivery. Existing
  `thesis` view state redirects to Workflows, and
  `/api/scientific/thesis/…` and `thesis-package-receipt.json` remain compatible.
  Execution advances only as its input, review and runtime gates are met.
- Studio's `--offline-only` option disables model access and network discovery
  for the running process and leaves persisted service settings intact. The
  offline bulk differential-expression path binds the file roles, validates the
  mounted design, records design and publication reviews under separate
  operator identities, and requires diagnostic qualification before dispatch.
  These reviews record local governance decisions and contribute to the
  replayable evidence trail.
- A configured Codex plugin exposes seven typed tools through
  `devbench plugin-mcp`. The tools inspect bounded input schemas, check
  answerability, freeze and execute reviewed plans, poll runs, release
  receipt-backed numeric summaries, and manage research aims. Follow-up plans
  use the same admitted input scope and keep their own plan IDs and digests.
  Async state lives in the separate mode-`0600` WAL database
  `plugin-jobs.sqlite3`. The version-1 workflow ledger owns research evidence.
- MCP can execute matrix audits and pre-staged Salmon, stage bulk DE for Studio
  review, and run supported single-cell droplet QC, batch QC, doublet prediction,
  preparation, and integration. With confirmed units and current QC, MCP can
  stage scCODA or bind donor pseudobulk for separate Studio review. It can queue
  a gene-list pathway method from a completed verified fit or execute an already
  queued pathway node. Private or clinical work, interactive QC revision and
  qualified review continue in Studio. Literature queries require a preexisting
  grant and current claim. See the [operating contract](langgraph-workflow.md#enforced-route-and-node-contracts)
  and [payload examples](../integrations/codex/devharness/skills/rna-workbench/references/mcp-payload-contracts.md).
- The MCP plan, run, and receipt cards are read-only and use a declarative CSP
  with no external origins. A live loopback Studio can open the same session
  by deep link. Studio prepares a binding only when adaptive mode is enabled
  and the committed research context and source already exist. Publication
  render and export use a suitable existing delivery binding or the configured
  service fallback.
- The optional `local_interactive_codex` campaign uses one operator-authorized
  selection at a time on the exact declared public or synthetic data scope.
  The campaign carries no multi-agent qualification. Its selection evidence,
  curated execution receipts, and independent scientific review serve
  different purposes.
- Record each question, estimand, contrast, success condition, evidence source and
  competing explanation in the existing ledger. Pin confirmatory hypotheses,
  decision rules and multiplicity scope before inspecting the corresponding
  outcomes. Record plan changes with their source and rationale. Rebuild context
  from ledger events with zero raw matrix rows.
- Use artifact identities for aim dependencies and preserve the original history
  on revision. A revised input, representation or aim invalidates dependent
  results. Compare reproducible outputs under the old and new branches only
  when their cohorts and estimands support that comparison.
- Evaluate answerability before method selection. Record assay, biological and
  technical units, transformation and reference history, actual measurements,
  and collection versus inferred time. Distinguish missing fields that the user
  can supply, unavailable runtime capabilities, and a study design that cannot
  estimate the requested contrast.
- Use the existing ledger and `Q-*` gates for operator review. Preserve failed
  attempts, authorization, and invalidation across restart. Reconcile a launch
  before rerunning it, cap infrastructure retries at two, and cap scientific
  revisions at three.
- Bind novelty statements to current literature receipts with retrieval time and
  correction or retraction status. Bind each numerical claim and plot to the
  independently checked result and sidecar. Record the exact measurements or
  follow-up experiment needed to resolve open alternatives.

## Unified Hybrid verification

These records cover the five Unified Hybrid implementation commits. The
independent audit checked the Studio rehydration, publication readback, privacy,
input-root, workflow review, and installed-source boundaries. The four blinded
review packets matched their pre-Commit-0 SHA-256 digests.

The engineering fixtures exercise declared synthetic inputs. The STDIO test
starts the actual MCP server, freezes an audit plan, runs a local worker, checks
receipt custody, and confirms Companion Studio can read the same research
session. The isolated marketplace run registered and installed Codex plugin
`0.2.0` under a temporary `CODEX_HOME` using Codex CLI `0.163.0`. The
clinical release flag stays false. [STDIO regression](../tests/test_plugin_mcp_server.py),
[packaging regression](../integrations/codex/tests/test_plugin.py).

The integrated MCP/answerability suite passed 28 tests and the Commit 3
packaging/Salmon suite passed 27, each with zero failures, errors, and skips.
Their JUnit logs are `commit2-stdio-integrated.xml` and
`commit3-packaging.xml` under `.pytest-workspaces/unified-hybrid-verification/`.

Before the bulk replay capsule was added, the audited functional matrix ran
all 90 requested test files in five independent pytest processes. It
recorded 1,166 passes, nine skips, and no
failures or errors. Eight skips require pinned container images. The ninth
is the opt-in 60,000-gene paired fixture, which passed in a separate run.
The protected FakeCLI suite passed all 107 tests. A separate ordinary-wheel
test ran the actual STDIO audit, brokered receipt, and Studio readback.
The matrix retains exact shard commands and outcomes in
`release-matrix/matrix.json`. The subsequent committed-tree rerun records
its commands and outcomes under `committed-replay/` in the same verification
directory.

Local headless Chromium `149.0.7827.55` opened a plugin-created
session directly in all seven Studio views (Overview, Findings, Workflows, QC,
Figures, Code, and Evidence) and observed the graph status `available`. The
server sent `X-Frame-Options: DENY`, retained `service.json` byte-for-byte
under `--offline-only`, and removed its mode-`0600` liveness marker on
shutdown. The local run record is
`.pytest-workspaces/unified-hybrid-verification/release-studio/browser/offline-dom-verification.json`.

The `qc-salmon` regression uses synthetic quantification and trace files to
exercise reference-map validation, weighted effective lengths, trace progress,
artifact custody and the estimated-count model handoff. Container launch is
substituted in these tests. Docker was unavailable for the live Nextflow/Salmon,
mounted-model and pinned-image Chromium checks. Prior mounted and browser
evidence retains its original revision. [Salmon bridge regressions](../tests/test_fastq_qc_salmon_bridge.py).

The real STDIO bulk fixture reached design preflight and waited for local
review. It did not launch a fit. Studio requires current design and
publication reviews and fresh execution consent to proceed. A post-fit MCP
summary requires a current verified Tier A receipt and current disclosure
authority. Its replay digest identifies a capsule containing the frozen plan,
the verified fit summary, and the original Tier A receipt hash. The full
research package remains a separate export. Synthetic checks covered
concurrent sealing, repeat readback, and tamper detection. Mounted bulk fit
and live readback still require Docker.

| Static check | Commit 0 (`510d1a8`) | Audited implementation |
|---|---:|---:|
| Ruff default rule set (`E4,E7,E9,F`), tracked project paths | 5,697 (exit 1) | 5,697 (exit 1) |
| Ruff configured rules (`E,F,B,I,UP,C90`), tracked project paths | 55,015 (exit 1) | 54,967 (exit 1) |
| Ruff default rules, changed Python paths including packaging | 12 findings | 12 findings across 44 paths, zero new |
| Ruff configured rules, changed Python paths including packaging | 1,610 findings | 1,562 findings across 44 paths, zero new |
| Mypy configured product roots | 535 errors in 40 files, 494 checked (exit 1) | Same 535 errors in 40 files, 497 checked (exit 1) |
| `git diff --check 510d1a8 HEAD` | – | Exit 0 |
| `node --check` | – | 19 tracked JavaScript files, all exit 0 |

The configured Mypy run reported zero errors at changed Python paths and
identical error lines to Commit 0. Comparing changed files by path, rule, and
message also found zero new default Ruff, configured Ruff, or scoped Mypy
diagnostics. Ruff and Mypy ran under the locked Python runtime with the patched
SQLite library path. The project Ruff roots are
`src devharness_core devharness_controlled devharness_server rna_kernels
devbench_engine scripts tests`. The scoped Ruff comparison also includes
changed Python files under `integrations`.

## Tier A and Tier B extension strategy

Extend Tier A through typed input and output ports, immutable representation,
versioned packages and comparison-specific checks. Preserve the existing bulk
and ERQL runtime identities for historical replay. Start with bounded
single-cell and nuclear QC, lane-aware doublets, representation preparation,
donor-level contrasts and downstream pathway tests. Evaluate integration against
uncorrected baselines using technical and biological checks. Biological
conservation scores require independent labels.

Use `MethodContract` and `ExploratoryOCI` for permitted custom extensions. Pin the
source, parameters, container, measurement axes, seeds and resource bounds.
Treat a proposed improvement as a testable hypothesis. Predefine the comparator
and control, separate tuning from evaluation by biological unit, and measure
accuracy, failure, harm and cost. Persist all attempts and keep unreviewed
outputs `exploratory_only`.

For advanced modalities, the caller supplies authorized, locally staged inputs
and compatible reference artifacts. Transcript structure and specialized
short-read processing require their own pinned runtimes. Spatial inputs require
coordinate systems, resolution, tissue identity and segmentation where
applicable. Allele-aware analysis needs genotype linkage and bias checks.
Velocity needs paired splicing layers. Prediction needs outcome definitions
and partitions that match the unit of generalization. Reuse the same custody and
answerability contracts across runtimes, with explicit missing-input and
missing-runtime outcomes.

## Evaluation plan

Freeze public input accessions, reference versions, fixture generation seeds,
the method comparisons and assessment thresholds before any evaluation outcomes
are visible to the agent. The following targets are assessment proposals; the
independent reviewer records the version frozen for each study.

| Evaluation | Proposed measurable oracle |
|---|---|
| Engineering and recovery | Execute L1–L4, T1–T5, strict-schema, WAL and socket checks, crash recovery and same-architecture replay. Require every safety trap to reject or demote as specified and each retained step to reconcile to one dispatch. |
| Known absence and planted signals | Use 200 global-null cohorts per regime and a separate frozen planted panel. Measure mean false discovery proportion at BH 0.05 with a ceiling of 0.075. Report Monte Carlo uncertainty, successful fit and abstention rates, and TPR against a frozen positive floor for each estimable design. For non-estimable confounding, score correct refusal and zero unjustified discoveries. |
| Published reproduction | Use locally staged public processed counts and metadata from paired PBMC stimulation, the BICCN mouse motor cortex nucleus atlas, and paired bulk airway. Freeze gene and cell-type reference mappings and expected tables before running. Record eligible units, overlap of measured hypotheses, direction agreement, effect error and confidence interval agreement for targeted findings. Run a separately held replication before claiming generalization. [PBMC accession](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE96583), [BICCN atlas](https://www.nature.com/articles/s41586-021-03500-8), [airway count object](https://bioconductor.org/packages/release/data/experiment/html/airway.html). |
| Blinded scientific review | Use at least two independent reviewers per study. Mask implementation and study-arm labels and assess units, design, provenance, claim scope, alternative explanations, and adequacy of planned follow-up. Report adjudicated pass rate, unresolved disagreements, and each unsupported-claim veto. Require zero safety or provenance vetoes before calling an aim complete. |
| Unfamiliar questions and entry points | Test at least one supported and one missing-measurement variant per family, with unknown families and ambiguous metadata as separate cases. Record false acceptance, false refusal and exact missing-input identification. Require zero unsupported inferential dispatches in the adversarial set and report confidence intervals for false refusals and incorrect diagnostic reasons. |
| Connected aims and Tier B | Change aim 2 using a checked aim 1 result and preserve a negative finding and disconfirmed alternative. Change a source version and verify selective invalidation. Compare the custom method to a locked baseline and control across held-out units. Require isolated partitions, a failure and cost record, and exploratory claim scope. |

For a study entering the blinded evaluation, declare `blinded_review_required`
before aim approval. An authorized operator freezes a masked packet digest and
an independent roster against the exact proposed outcome and its current
receipts. The host verifies each attributed assessment against that subject.
All assigned reviewers must support the outcome before a finding or a verified
absence of findings can close the aim. A disagreement or a new evidence version
requires a new review. The local ledger retains full assessments, and the
thesis package reports receipt digests, disposition counts, and veto codes.

For a bound Workbench lineage, `open_workbench_blinded_review` binds the digest
of an operator-prepared masked packet in the local workspace.
`submit_workbench_blinded_review` admits a returned `BlindedAimAssessment` JSON
file only when the configured independent verifier accepts its exact subject
and receipt. Both actions recheck the current revision and return the panel
status with receipt digests. The operator prepares the masked contents and
the independent reviews remain separate from execution admission.

## Completion criteria

The mounted donor-pseudobulk plan can declare `sensitivity_method="limma_voom"`
for a fixed-effect coefficient or contrast from original counts. Register it as
a nonprimary comparison on the pinned voom runtime. The route uses RLE
effective library sizes, observation-level precision weights, robust empirical
Bayes moderation, and its own BH family. The receipt and replay identify those
choices. Synthesis keeps comparison statistics out of primary `gene_statistics`.
The current primary count method remains edgeR. The synthetic benchmark found a
lower null false discovery rate in independent designs and a bulk power loss
beyond the prespecified margin under moderate effects.

For a single-aim confirmatory contrast, an unweighted sensitivity comparison
can use the same approved ordered hypothesis family as the primary fit. A
confirmatory finding or a verified absence of findings requires a current
primary result, including before a blinded review panel opens. A sensitivity
receipt can accompany that result. Withdrawing the primary result invalidates
the current conclusion and preserves the historical receipts.

A sensitivity comparison can also reference the exact frozen member of a
cross-aim BH plan. Its step and parameter identities must remain distinct
from all primary members. It retains its own BH result, and joint release
still requires all frozen primary receipts.

Synthesis verifies a confirmatory negative outcome against its exact cited
primary Tier A gene family, including donor pseudobulk. It records the number
of tested hypotheses and the minimum adjusted p-value, and rejects an empty
tested family or a q-value at or below the aim's locked FDR. A schema that
omits the gene table can still retain the finding and its pooled BH replay.
The replay bundle also retains pooled releases cited by current supported
findings or competing-explanation assessments. Stale comparison receipts
remain in the audit history.

A trusted Workbench coordinator can bind a staged donor-pseudobulk capsule to
an approved thesis aim through `bind_workbench_pseudobulk`. It registers the
exact counted and metadata artifacts, retains the answerability decision, and
queues the plan with its primary or comparison role. The structural design
receipt records estimability. Dispatch still requires current independent
research review and diagnostic admission.

The coordinator's original-count reader stages up to 200,000 cells with a
64 MiB annotation budget and the shared 16 MiB expression blocks. A paired
declaration can identify `candidate_aliases` among its technical factors.
The coordinator removes a candidate from the mounted design only after an
exact one-to-one match to donor and reconstruction from the donor indicators.
It retains the original factor, profile order, and exact proof in the capsule.
A factor confounded with condition remains subject to the rank and
estimability checks.

The mounted L3 workflow now obtains the ordered cell mask from the current
QC receipt and applies it directly to the original count and observation
axes for pseudobulk and composition. Pseudobulk staging binds the mask hash,
and registration checks the mask against the owning ledger. An accepted QC
mask that removes a whole composition profile requires an explicit decision.
The ledger binds both count products to the original data and QC metrics.
Revising QC withdraws the derived products and their dependent fits from
current use. The original receipts and lineage remain in the audit trail.
The L3 reanalysis test accepts a revised QC mask, stages fresh count products,
and runs both replacement fits. Its sealed package binds the revised fit
receipts and carries only the current fits into executable replay.
The package can also regenerate its QC-selected count capsule and composition
input from the original H5AD, its replayed QC metrics and the declared scCODA
settings. A P-P recipe also binds the original external label map and its
qualification receipt by digest. Replay checks both before running QC and
revalidates the labels against the original observation axis during count
staging. The label files stay in protected local custody. Legacy P-P packages
keep their retained-input replay route.
The label dependencies also enter the dispatch record. A later protected
partition seal can detect prior use and withdraw current delivery.

When a reviewed research fit is ready, the controller can choose among all
ready jobs through opaque aliases in a scheduling brief. The brief includes
fit roles, design regimes and curated single-cell operation names. Study text,
evidence contents and scientific outcomes stay in the local coordinator. The
broker binds the exact brief to its exposure receipt. The coordinator resolves
the alias and rechecks the selected job's admission before dispatch. Turns
without ready reviewed research use the existing general planning context.

An **analytical step** is complete when it has a frozen method and applicable
design, reproducible input identities, a terminal receipt, and independently
checked outputs or a precise reason for abstention or failure. A fitted family
retains its denominator and finite valid probabilities or explicit nulls.
Figures bind numeric sidecars and their validation receipt. An absent plot or
check has an explicit status. A clean execution qualifies the step for replay.
Scientific claims require evidence appropriate to the estimand and design.

A **thesis investigation** is complete when every approved aim has an evidence
trail and adjudicated outcome, cross-aim dependencies and test families verify,
key competing explanations have been examined, and branch failures and negative
findings remain visible. Verify `summary.md` with the five scientific questions,
`final_discovery.json` against the applicable schema, `provenance_manifest.json`
with requirement sources and runtime identities, figures with numeric sidecars,
versioned methods, and executable `replay_bundle/`. Same-architecture replay
must reproduce the declared numeric artifacts. Distinguish a verified negative
finding, an unresolved aim and insufficient information, and state what
observations could resolve the remaining uncertainty.

Synthesis can publish a partial package while an approved aim is open.
Completing the investigation requires a current supported finding, a verified
negative finding, or a refusal for each approved aim. A revised aim keeps this
requirement while it awaits approval on the new version. New proposals enter
the approved scope when an operator approves them.

Before recording a supported or negative finding, the lifecycle verifies an
assessment for each competing explanation declared in the approved plan. The
comparison can conclude that the evidence is inconclusive. Retiring or
invalidating comparison evidence reopens the scientific question and blocks
dependent aims. Blinded panels require the same checks and exclude operators
who produced the comparison evidence from their reviewer roster.
