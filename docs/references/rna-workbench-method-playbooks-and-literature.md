# Reference Playbooks, 20-Family Capability Map & Literature Rationale

**Document Role:** Tier-3 Reference Documentation (loaded on demand by agents or humans; **not** part of always-on agent prompt context).<br>
**Operating Contract:** See [local workflow contracts](../langgraph-workflow.md) for active runtime rules, exact tool boundaries, and hard invariants.<br>
**Implemented Codebase Status:** See [../thesis-goal-and-capability-assessment.md](../thesis-goal-and-capability-assessment.md).

> **CRITICAL FOR LLM AGENTS:** External methods cited below (e.g., `CellBender`, `SoupX`, `scDblFinder`, `DESeq2`, `dream`, `Milo`, `CellRank 2`, `BayesPrism`, `rMATS-turbo`, `SpatialData`, `pertpy`) are **scientific reference benchmarks**, NOT automatically installed Tier-A MCP tools. Before planning any execution, verify what is actually callable in the [local workflow contracts](../langgraph-workflow.md) and the live tool/runtime catalog.

## Table of Contents
1. [Multi-Agent Architecture References & Benchmark Failure Modes](#1-multi-agent-architecture-references--benchmark-failure-modes)
2. [Assay Types, Entry Points & Inferential Units](#2-assay-types-entry-points--inferential-units)
3. [Single-Cell & Single-Nucleus RNA-seq Reference Playbook (Priority 1)](#3-single-cell--single-nucleus-rna-seq-reference-playbook-priority-1)
4. [Bulk, Splicing, Spatial, Long-Read & Perturbation Reference Playbook (Priorities 2 & 3)](#4-bulk-splicing-spatial-long-read--perturbation-reference-playbook-priorities-2--3)
5. [Complete 20-Family RNA-seq Capability & Measurement Map](#5-complete-20-family-rna-seq-capability--measurement-map)
6. [Literature & Open-Source Reference Anchor List](#6-literature--open-source-reference-anchor-list)

---

## 1. Multi-Agent Architecture References & Benchmark Failure Modes

### 1.1 Reference Multi-Step Bioinformatics Agents
| External Reference System | Core Pattern | Relevance to This Workbench |
|---|---|---|
| **BioMaster** (`ai4nucleome/BioMaster`) | Role-isolated **Plan -> Execute -> Debug -> Check** + deterministic *BioSkills* + SQLite control plane | Mirrors our `WorkflowOperationLedger` + `plugin-jobs.sqlite3` control plane and curated Tier-A contracts. |
| **CellAgent** (`liu-shiqiang/CellAgent`) | Hierarchical **Planner -> Executor -> Evaluator** optimization loop | Mirrors separation of immutable plan freeze, isolated execution, and independent evaluation/sensitivity audit. |
| **Biomni** (`snap-stanford/Biomni`) | Retrieval-augmented planning over a structured **Know-How Library** | Mirrors progressive-disclosure `references/` playbooks + 20-family `rna_answerability` registry. |
| **Robin** (`Future-House/robin`, *Nature* 2026) & **Kosmos** (Edison Scientific) | Parallel **Data Analysis** (`Finch`) decoupled from grounded **Literature Synthesis** (`Crow`/`Falcon`), synced via a **Structured World Model** | Mirrors persistent multi-aim Research World State + strict post-evidence `LiteratureService` separation. |
| **SpatialAgent** (`Genentech/SpatialAgent`), **AutoBA** (`JoshuaChou2018/AutoBA`), **BioAgent / BioAgent Bench** | Checkpointed resumable DAGs, multimodal extensibility, resilience to noisy inputs | Mirrors SQLite session rehydration, bounded retries, and mandatory intake/ID audits. |

### 1.2 Documented Autonomous Agent Failure Modes (`BixBench`, `BioAgent Bench`, `scBench`, `PromptBio-Bench`)
1. **Silent Data Parsing & ID Misalignment:** Wrong columns, truncated matrices, or mismatched sample/cell IDs passing without errors -> mitigated by bounded schema/layer/ID audits before modeling.
2. **Statistical & Methodological Misapplication:** Cell-level Wilcoxon tests across donors, count models on log/batch-subtracted values -> mitigated by strict `COUNT-1` layer isolation and `Q-DESIGN` preflight.
3. **Long-Horizon DAG & Context Collapse:** Mixing stale pre-QC outputs with post-QC runs -> mitigated by durable SQLite lineage and automatic downstream invalidation.
4. **Error-Loop Traps:** Blind syntax retries until context exhaustion -> mitigated by bounded retry budgets (`<= 2`) and explicit error/refusal transitions.
5. **Multimodal Plot Hallucination (`BixBench` Vision-Ablation Finding):** Vision-only plot inspection degrades quantitative accuracy -> mitigated by the **Numeric-First Verification Rule** (every figure ships with a verified numeric sidecar table; quantitative claims must come from `EvidenceBroker`-released tables, not plot pixels).

---

## 2. Assay Types, Entry Points & Inferential Units

- **Single-Cell vs. Single-Nucleus (`scRNA-seq` vs. `snRNA-seq`):** Distinguish whole-cell cytoplasmic mRNA (`10x Chromium 3'/5'`, `Smart-seq2/3`, `Parse`) from single-nucleus pre-mRNA (high intronic fraction requiring intron-inclusive counting, lower expected `%MT`, distinct nuclear `MALAT1`/ribosomal profiles, ambient nuclear RNA risk).
- **Bulk, Spatial, Long-Read & Specialized RNA:** Purified/tissue/cohort bulk RNA-seq; spot/subcellular/imaging spatial transcriptomics (`Visium`, `Visium HD`, `Xenium`, `MERFISH`, `CosMx`); short-read, PacBio Iso-Seq/Kinnex, and ONT cDNA/direct-RNA long-read sequencing (LRGASP, *Nature Methods* 2024); small/miRNA, circRNA, metabolic labeling (`SLAM-seq`/`4sU`), `Ribo-seq`; host–pathogen / dual RNA-seq; paired multi-omics (CITE-seq, scATAC, Perturb-seq, TCR/BCR).
- **Entry-Point Provenance Audit:** Explicitly record what upstream signal was already transformed or discarded across local pre-staged FASTQs (zero archive downloads), BAMs/junctions, transcript quantifications (`quant.sf` + effective lengths), unfiltered raw droplet matrices, filtered `.h5ad`/`.zarr` matrices, or normalized/legacy matrices.
- **Inferential Units:** Never count multiple cells, nuclei, technical lanes, or repeated biopsy specimens from the same donor/animal as independent biological replicates (`Squair et al. 2021`, `Zimmerman et al. 2021`). In pooled cell-assigned Perturb-seq, model guide heterogeneity alongside channel/replicate/donor nesting. Never treat complete batch-condition confounding as repairable by adding covariates.

---

## 3. Single-Cell & Single-Nucleus RNA-seq Reference Playbook (Priority 1)

| Analytical Stage | Scientific Best-Practice Standard (Reference Benchmarks) | Mandatory Diagnostics & Pitfall Guards |
|---|---|---|
| **1. Ambient RNA & Droplet QC** | Inspect raw vs. filtered droplet distributions (`CellBender`, `SoupX` where unfiltered droplets exist). Use **per-sample / per-batch MAD adaptive thresholds** (`log1p(total_counts)`, `log1p(n_genes)`, `%MT`, `%ribo`, `MALAT1`). | Calibrate `%MT` and intron expectations for `snRNA-seq` and high-mitochondria tissues (cardiomyocytes, hepatocytes, kidney). Inspect excluded cells across biological groups to prove target biology is not erased. |
| **2. Doublet Detection** | Run **per-capture-lane / per-sample** doublet scoring (`scDblFinder`, `Scrublet`, or Tier-A `scrna_lane_doublets`) prior to merging. | Never simulate doublets across pooled multi-lane matrices with mixed cell-type composition. Check whether apparent doublets mark a real transitional state before removal. |
| **3. Normalization, Layers & HVGs** | Keep immutable layer separation: `layers["counts"]` (`COUNT-1`), ambient-corrected counts (if run), shifted-log (`Ahlmann-Eltze & Huber 2023`) / `scran` / Pearson residuals (`SCTransform`) for HVG/PCA; **batch-aware HVG selection** (`batch_key`). | Never feed log-normalized, scaled, integrated, or imputed values into NB/pseudobulk DE or compositional models. Check whether cell-cycle, stress, or sex signatures confound the target question before regressing them out. |
| **4. Integration & Atlas Mapping** | Compare unintegrated baseline vs. integrated embeddings (`Harmony`, `scVI`/`scANVI`, `FastMNN`, `BBKNN`, or Tier-A `scrna_integrate`) on **both `scIB` axes** (*Batch Correction* AND *Bio Conservation*, `Luecken et al. 2022`). | Explicitly check for over-integration (collapsing condition-specific states or rare populations). Preserve `"unknown"` / uncertainty scores in query-to-reference mapping (`scArches`/Symphony). |
| **5. Clustering, Annotation & Anti-Double-Dipping** | Assess cluster stability across resolutions (`Leiden`), combine canonical markers + reference mapping + uncertainty. Tag cluster-derived markers strictly as `exploratory_cluster_markers`. | Never report naive post-clustering cell-level p-values on the same counts as confirmatory Type I error tests (`Neufeld et al. / Gao et al. 2024`). Use **count splitting** (`scrna_nb_countsplit` where available) or held-out donors/cells for confirmatory testing. |
| **6. Condition Differential Expression** | Aggregate untouched raw counts (`COUNT-1`) to **pseudobulk per `(cell_type, biological_unit)`**, filtering low-cell/low-count pseudobulks transparently, then fit bulk NB/QL/limma-voom/mixed models (`edgeR` QL, `DESeq2`, `limma-voom`, `dream`; `Squair et al. 2021`). | If `n_donors` per group `< 3`, flag low confirmatory power instead of silently falling back to cell-level Wilcoxon tests. Check donor-level consistency to prevent single-donor outlier-driven hits. |
| **7. Differential Abundance & Composition** | Pair cluster compositional models (`scCODA` / Tier-A `scrna_sccoda`, `propeller`) with cluster-free KNN neighborhood DA (`Milo`/`MiloR`) at the biological-replicate level. | Respect simplex sum-to-one constraints (no naive t-tests/Chi-squared on raw cell fractions). Separate subpopulation compositional shifts from intrinsic within-cell-type DE. |
| **8. Trajectories & Fate Mapping** | Match trajectory topology (`Slingshot`, `Palantir`, `Monocle3`), NB-GAM lineage DE (`tradeSeq`), and multiview fate mapping (`CellRank 2`). | Treat pseudotime as an inferred ordering, not ground-truth time. Test sensitivity to roots, integration, sampling density, and donor effects; gate velocity strictly on spliced/unspliced counts. |
| **9. Pathways, Programs, GRNs & CCC** | Inter-gene-correlation-aware (`CAMERA`), rank-based (`fgsea`), footprint/TF ensembles (`decoupleR`), cNMF programs, multi-omic GRNs (`SCENIC+`), consensus CCC (`LIANA+`/`CellChat`). | **Mandatory ORA rule (`Wijesooriya et al. 2022`):** supply exact tested expressed-gene universe as background. Never infer physical signaling or causal TF binding from co-expression alone. |

---

## 4. Bulk, Splicing, Spatial, Long-Read & Perturbation Reference Playbook (Priorities 2 & 3)

| Domain | Scientific Best-Practice Standard (Reference Benchmarks) | Mandatory Diagnostics & Pitfall Guards |
|---|---|---|
| **Bulk Gene-Level DE** | Import transcript quantifications (`Salmon`/`Kallisto`) via `tximport` preserving gene-length offset matrices (`Soneson et al. 2015`); filter low counts (`filterByExpr`); fit `limma-voom`, `edgeR` QL, `DESeq2`, or `variancePartition`/`dream` LMM (`Hoffman & Roussos 2021`) with known batches **inside the design matrix**. | Never feed `ComBat`/`removeBatchEffect`-subtracted values into count-based DE models. Include `svaseq`/`RUVSeq` factors inside the GLM for hidden confounders. On large cohorts (`n >= 100`), add a Wilcoxon/rank sensitivity check against NB FDR inflation (`Li et al. 2022`). Inspect PCA/MDS, `Q-INFLUENCE` leverage, p-value histograms, and dispersion trends. |
| **Bulk Deconvolution** | Reference-matched Bayesian/weighted deconvolution (`BayesPrism`, `MuSiC`, `RCTD`), auditing outlier transcripts (`chrM`, `MALAT1`). | Verify reference-to-bulk tissue/platform compatibility; distinguish relative proportions from absolute cell counts and composition from within-cell-type DE. |
| **Alternative Splicing & DTU** | Event/junction (`rMATS-turbo`, `LeafCutter`, `MAJIQ` from BAMs/junctions); exon/DTU (`DEXSeq`, `DRIMSeq` + `stageR` from exon/transcript counts), incorporating batch strictly as design covariates. | Distinguish transcript-usage shifts (DTU) from total gene DE; verify junction depth and coverage uniformity; never pre-subtract batches before beta-binomial/Dirichlet-multinomial tests. |
| **Long-Read Transcript Discovery** | Follow **LRGASP** (`Pardo-Palacios et al. 2024`): reference-guided identification/quantification (`Bambu`, `IsoQuant`, `FLAIR`), matching platform accuracy vs. depth strengths. | Require multi-replicate support + orthogonal splice-junction/TSS/TES evidence + SQANTI3 artifact filtering before claiming novel isoforms. |
| **Spatial Transcriptomics** | Store images, segmentations, molecules, and counts in `SpatialData`; analyze with `Squidpy`/`OSTA` spatial autocorrelation, domains, niches, and `cell2location`/`RCTD` deconvolution. | Account for spatial autocorrelation (spots are not i.i.d.), tissue folds/boundaries, segmentation bleed-through, and targeted panel gene-universe bias. |
| **Perturbation / Functional Genomics** | Follow `pertpy`: `GuideAssignment` -> `Mixscape`/`Mixscale` efficacy scoring -> E-distance/Wasserstein shifts -> guide- and replicate-aware DE & program enrichment. | Validate non-targeting controls, MOI/multi-guide cells, off-target effects, and viability/selection bias. |

---

## 5. Complete 20-Family RNA-seq Capability & Measurement Map

*Cross-reference [../thesis-goal-and-capability-assessment.md](../thesis-goal-and-capability-assessment.md) for current implemented/validated/missing status per family.*

| # | Research Problem Family | Required Measurement Proof (Answerability Gate) | Analytical Scope & Validation |
|---|---|---|---|
| **1** | **Expression differences & differential cell states** | Raw counts (`COUNT-1`) or `tximport` counts+offsets (or explicit normalized continuous assay), biological unit IDs, identifiable contrast/design | Bulk (`limma-voom`/`edgeR`/`DESeq2`/`dream`) or donor pseudobulk DE; interaction/time/dose models; `svaseq`/`RUVSeq` covariates; `n >= 100` rank sensitivity; `0 <= p <= q <= 1`. |
| **2** | **Cell identities, heterogeneity & rare populations** | Per-cell expression matrix (`COUNT-1` preferred), sample/lane IDs | Assay-aware QC (sc vs. sn), ambient/doublet audit, batch-aware HVGs, stability clustering, reference mapping with uncertainty + `"unknown"`, anti-double-dipping provenance. |
| **3** | **Cell abundance, composition & deconvolution** | Single-cell annotations + biological units (DA), OR bulk counts + matched SC reference (deconvolution) | `scCODA`/`propeller`/`Milo` DA; `BayesPrism`/`RCTD`/`MuSiC` deconvolution; decouple composition from within-cell-type DE. |
| **4** | **Alternative splicing & transcript usage** | Pre-staged BAMs/junctions OR transcript/exon counts (refuse on gene-summed counts alone) | Junction/exon/intron-retention/DTU/APA with design-matrix batch covariates and `stageR` FDR; separate DTU from total gene DE. |
| **5** | **Transcript discovery & annotation** | Local pre-staged long-read or strand-specific short-read alignments/assemblies | `Bambu`/`IsoQuant`/`FLAIR`/`StringTie` reconstruction, non-model annotation, TSS/TES & SQANTI3 artifact checks, orthogonal replicate validation. |
| **6** | **Pathways, gene programs & networks** | Full tested gene ranking + exact background gene universe (or expression matrix for programs/GRNs) | `CAMERA`, `fgsea`, `decoupleR`, cNMF/module stability, background-controlled ORA, strict correlational vs. causal scope. |
| **7** | **Development, temporal responses & state transitions** | Observed time points OR continuous manifold + root evidence (spliced/unspliced mandatory for velocity) | Spline/GAM time-courses, `Slingshot`/`Palantir`, `CellRank 2`, `tradeSeq`, root/integration/donor sensitivity. |
| **8** | **Integration, atlas construction & comparative transcriptomics** | Multi-batch/multi-study matrices + batch/study metadata (+ explicit ortholog table cross-species) | Unintegrated vs. integrated benchmarked on both `scIB` axes; overcorrection/lost-population audit; query mapping; cross-study concordance. |
| **9** | **Spatial organization & tissue context** | Spatial coordinates + expression (+ images/segmentations for subcellular/spot assays) | `SpatialData`/`Squidpy`/`OSTA` spatially variable genes, niches, colocalization, deconvolution, spatial-dependence & segmentation checks. |
| **10** | **Cell–cell communication** | Cell-type expression + biological units (+ spatial coordinates or perturbation targets preferred) | Consensus `LIANA+`/`CellChat`, replicate-level differential CCC, downstream target footprinting, spatial colocalization grounding. |
| **11** | **Perturbation responses & functional genomics** | Perturbation/guide/dose assignments + non-targeting/vehicle controls + replicate/batch metadata | `pertpy` guide assignment, `Mixscape`/`Mixscale` efficacy, E-distance/Wasserstein, perturbation DE, off-target/selection checks. |
| **12** | **Genetic regulation & allelic expression** | Paired genotypes/VCFs + allele-specific read counts/BAMs + relatedness/ancestry covariates | ASE, eQTL/sQTL, GxE modeling; WASP allelic bias, relatedness adjustment, population-of-inference discipline. |
| **13** | **Transcript abnormalities & cancer-related questions** | Local pre-staged reads/BAMs (fusions/editing/variants) or chr-mapped counts + reference normals (broad CNV) | Fusions, editing, expressed variants, aberrant splicing, `inferCNV`/`CopyKat` with normal controls and orthogonal caveats. |
| **14** | **Small, noncoding, circular & repetitive RNAs** | Dedicated small-RNA / total-RNA / rRNA-depleted pre-staged reads or specialized biotype/back-splice tables | Specialized short-read/multi-mapping/TE/circRNA quantification (`nf-core/smrnaseq`), biotype normalization, artifact checks. |
| **15** | **RNA production, processing, turnover, modification & translation** | Spliced+unspliced (velocity), metabolic-labeling (`4sU`), ONT direct-RNA signals, or paired `Ribo-seq`+`RNA-seq` | Kinetic synthesis/degradation, assumption-audited velocity, modification signals, translational efficiency interaction models. |
| **16** | **Host–pathogen & community transcriptomics** | Pre-staged reads aligned competitively to host+microbe references or verified taxonomic+functional matrices | Dual RNA-seq separation, competitive multi-mapping disambiguation, taxon abundance vs. transcriptional regulation separation. |
| **17** | **Multimodal, repertoire & clonal analyses** | Paired multi-omic barcodes (CITE-seq ADT, scATAC, V(D)J TCR/BCR, lineage barcodes) | Joint WNN/MOFA+/MultiVI/SCENIC+, clonotype/lineage trees, paired vs. inferred match distinction, concordance/discordance audit. |
| **18** | **Prediction, signatures & phenotype associations** | Outcome labels + generalization unit IDs (donor/cohort/site) | Nested CV / outer-cohort holdout at the generalization unit (preprocessing/HVG/tuning strictly inside training folds); calibration & shortcut audits. |
| **19** | **Reproduction, replication & evidence synthesis** | Published protocols/accessions + primary/independent datasets | Protocol reproduction vs. sensitivity re-analysis, cross-study meta-analysis (`I^2`/random effects), discrepancy diagnosis. |
| **20** | **Experimental design & method development** | Pilot/reference data or simulation/power parameters | Power/depth planning, custom Tier-B `MethodContract` specification & fair head-to-head benchmarking with independent validation. |

---

## 6. Literature & Open-Source Reference Anchor List

- **General & Single-Cell Best Practices:** Heumos et al. (2023, *Nat Rev Genet*, `sc-best-practices.org`); Amezquita et al. (2020, *Nat Methods*, OSCA); Conesa et al. (2016, *Genome Biol*); Stark, Grzelak & Hadfield (2019, *Nat Rev Genet*); `nf-core/rnaseq` & `nf-core/scrnaseq`.
- **QC, Transformation, Integration, DE, Composition, Pathways, Trajectories, Splicing & Multi-Modal:** Fleming et al. (2023, *Nat Methods*, `CellBender`); Young & Behjati (2020, *GigaScience*, `SoupX`); Germain et al. (2021, *F1000Research*, `scDblFinder`); Ahlmann-Eltze & Huber (2023, *Nat Methods*); Luecken et al. (2022, *Nat Methods*, `scIB`); Squair et al. (2021, *Nat Commun*); Zimmerman et al. (2021, *Nat Commun*); Neufeld et al. / Gao et al. (2024, count splitting); Love et al. (2014, `DESeq2`); Law et al. (2014, `limma-voom`); Soneson et al. (2015, `tximport`); Hoffman & Roussos (2021, `dream`/`variancePartition`); Leek (2014, `svaseq`); Risso et al. (2014, `RUVSeq`); Li et al. (2022, *Genome Biol*, large-cohort Wilcoxon); Büttner et al. (2021, `scCODA`); Dann et al. (2022, `Milo`); Phipson et al. (2022, `propeller`); Chu et al. (2022, `BayesPrism`); Wu & Smyth (2012, `CAMERA`); Wijesooriya et al. (2022, *PLOS Comput Biol*, ORA background universe); Badia-i-Mompel et al. (2022, `decoupleR`); Bravo González-Blas et al. (2023, `SCENIC+`); Van den Berge et al. (2020, `tradeSeq`); Weiler et al. (2024, `CellRank 2`); Dimitrov et al. (2022/2024, `LIANA+`); Pardo-Palacios et al. (2024, *Nat Methods*, LRGASP); Marconato et al. (2024, `SpatialData`); Peidli et al. (2024, `pertpy`).
- **Autonomous Bioinformatics Agents & LLM Instruction Authoring:** `ai4nucleome/BioMaster`, `liu-shiqiang/CellAgent`, `snap-stanford/Biomni`, `Future-House/robin` (*Nature* 2026), Kosmos (Edison Scientific), `Genentech/SpatialAgent`, `JoshuaChou2018/AutoBA`, `deepmind11/scAgent`, BixBench, BioAgent Bench, scBench; OpenAI Codex `AGENTS.md` / `PLANS.md` / Skills progressive disclosure (`project_doc_max_bytes = 32768`); Anthropic `agentskills.io`; ETH Zurich & LogicStar.ai (*Evaluating AGENTS.md*, `arXiv:2602.11988`); IFScale (Distyl AI).
