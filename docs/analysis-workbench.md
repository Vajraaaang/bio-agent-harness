# Analysis workbench: implementation and verification record

Goal: `devbench-analyze --data <file> --prompt "QC, correct batch effects, cluster, identify cell types" --out <dir>` produces an executed editable notebook, figures, results, environment lock, plain-language summary, provenance receipts and reviewer report. Known-truth inputs additionally produce a separate scorecard. This is a research workbench, not a clinical qualification claim.

## Boundaries and design

- Implement under `devbench/analysis/`. Do not alter benchmark graders, plans, seeds, catalog, answer contracts or saved `results/`.
- Keep modeling and reviewer/scorecard inputs separate. Modeling receives observed data and public analysis configuration; only subsequent scoring reads private truth.
- Use AnnData's on-disk matrix interface for the selected H5AD matrix. Do not eagerly deserialize unused layers or embeddings. Materialize explicit subsets within a stated allocation budget; retain original observation and feature identities.
- Use count methods for documented counts and continuous methods for normalized expression. Refuse non-identifiable adjusted effects; never silently discard the confounding covariate.
- Notebook cells use bundle-relative paths. Preserve failed cells and failed reviews. Record commands, source, environment, data identities, seeds, stage wall time and sampled peak memory.
- The reviewer is a separately invoked verification pass that recomputes key quantities and checks claims and figure inputs. It must not merely restate producer summaries. Model-assisted interpretation review, if added, is separate from deterministic checks and requires provider authorization. No model reviewer is being launched during implementation.
- Plan resource needs before execution; require explicit consent for expensive or long work. No scored benchmark runs, subagents, private-data uploads or unapproved data acquisition.
- Acquire only the three authorized public reference collections. Validate annotations on experimentally labelled held-out data, report uncertainty and abstentions, and distinguish source labels from proven truth.
- Implement the smaller synthetic validation and biological capabilities before connector work. Do not claim product parity from a diagram or tests alone.

The reference product's public material supports a coordinating/reviewer split, artifact history and compute planning. Its internal implementation and scientific accuracy are not independently established here. [Anthropic announcement](https://www.anthropic.com/news/claude-science-ai-workbench), [product description](https://claude.com/product/claude-science).

## Phase 0: verified local state, 2026-09-15

Python 3.12.1, arm64. At preflight, installed: AnnData 0.12.19, Zarr 2.18.7, NumPy 2.5.3, pandas 2.3.3, SciPy 1.18.1, statsmodels 0.14.6, psutil 7.2.2. At preflight, missing: Scanpy, scvi-tools, harmonypy, igraph, leidenalg, umap-learn, celltypist, scrublet, nbclient, nbformat, ipykernel and papermill. The subsequent installation uses nbclient rather than papermill; see the progress evidence below. Version availability alone is not import compatibility.

No implementation of the named normalization/HVG/Leiden/UMAP/Harmony/scVI/ComBat operations was found by source search in `devharness_server/` or `devbench/study/`. Existing single-cell tools implement narrower benchmark methods.

Confirmed benchmark limits: single-cell `max_cells` option range 4–5,000; public feature cap 5,000; shared biology matrix cap 100,000,000 bytes; older public IO matrix cap 16,000,000 bytes. These limits remain intact; the workbench uses explicit memory planning rather than adopting them.

The specified pilot has two prepared cohorts with private truth, T01-dense and T06-aliased, each 120 genes × 104 observations. A filename inventory under `results/` found four `private/truth.json` files in total; it does not establish the objective's claimed 5,375 distinct cohorts. No calibration cohort was generated or consumed.

`e11_integrated.h5ad` exists at 2,860,140,073 bytes (about 2.66 GiB), with 22,702 observations × 26,841 features. Metadata-only HDF5 inspection found approximately 1,415,242,508 decoded bytes in each of `X` and `layers/counts`. Ordinary `read_h5ad(backed='r')` leaves the primary matrix backed but can load the other layers; selected-matrix loading is therefore necessary. Existing `obs` fields include `celltypes`, `conf_score` and `doublets`; their provenance is unverified, so “no labels exist” would be incorrect, and these labels are not experimental ground truth.

The named 10x H5 file exists at 16,891,753 bytes. The developmental H5AD files e09–e14 are present. Only file metadata has been inspected at this point; no biological conclusions are drawn from their presence. Free disk space at preflight was about 27.1 GiB.

## Requirement ledger

| Requirement | Evidence required | Current state |
|---|---|---|
| Analysis dependency stack | Locked environment, successful imports and targeted runtime checks | Installed/imported and exercised on local/Colab fixtures; native CellTypist training is incompatible with the current local scikit-learn version, while the separately tested inference path works on an artificial binary model; see runtime scope below |
| Large backed input | Actual large file inspection and subset, measured wall time/RSS, unchanged source | Passed for the specified 2.86 GB file and explicit 128 × 1,000 subset; not a full-file analysis |
| T01 known-truth recovery | Prespecified direction/effect/null metrics from observed-input analysis | Development cohort was contaminated by tuning; three fresh T01 cohorts passed unchanged gates, with sealed predictions before scoring; limited generator-specific evidence |
| T06 correct refusal | Rank/confounding evidence; no unsupported adjusted estimate | Three fresh T06 cohorts correctly refused the aliased contrast, plus earlier development checks |
| Executed notebook bundle | Re-executed notebook, figures/tables, environment, summary and receipts | Implemented; actual 80-cell Colab bundle and Mac replay passed, with 369 verified artifacts each; full e11 still pending |
| Separate reviewer pass | Deliberately corrupted calculations/claims/figures rejected; real clean run reviewed | Implemented and corruption-tested; actual Colab/Mac fixture passed 19 checks each; shared routines limit scientific independence |
| Single-cell QC | Count provenance, explicit gene-symbol handling, zero-match mitochondrial failure | Streaming QC and selected-feature normalization verified on fixtures; zero mitochondrial matches fail explicitly |
| Clustering/integration/doublets | Executed methods, preserved counts, batch/biological-unit handling and measured resources | Harmony/Leiden/UMAP executed on Colab fixture; small scVI training interrupted/resumed locally; full e11 batch/doublet evaluation pending |
| Cell-type annotation | CellTypist and marker evidence; uncertainty/abstention; scored held-out reference | Marker scoring and scorecard CLI exercised; CellTypist response contract fixed and actual package inference checked against direct coefficient calculations on a software fixture; pretrained biological-reference validation pending |
| Public references | Zheng FACS PBMC, labelled mouse atlas, Luecken pancreas; checksums/provenance | Pending |
| Reference rungs | SEQC mixture/ERCC; TRRUST scope; continuous bulk-stage summaries | Pending |
| Orthogonal development check | Stage/specimen/gene alignment and pseudobulk-versus-bulk comparison with limitations | Pending |
| User's 10x endpoint | Complete command after relevant reference validation | Bounded direct reader and complete synthetic 10x notebook tested; real 2,803-cell file inspected only; reference-supported real annotation pending |
| Connectors | Actual protocol/auth results, no assumption from configured URLs | Deferred until biology work |
| Local and Colab portability | Notebook rerun from bundle-relative paths in each claimed environment | Actual Colab-to-Mac fixture round trip passed; full Drive-hosted e11 round trip awaits interactive authorization |

## Method references

- [AnnData sparse dataset interface](https://anndata.readthedocs.io/en/stable/generated/anndata.io.sparse_dataset.html): on-disk CSR/CSC slices without loading an entire matrix.
- [Scanpy preprocessing and clustering](https://scanpy.readthedocs.io/en/stable/tutorials/basics/clustering.html): counts, QC, doublet checks, normalization, HVGs and graph analyses must preserve their respective data scales.

Recorded checks and machine-readable measurements will be written under `workbench_runs/`, separate from protected benchmark `results/`.

## First implementation checkpoint

The `analysis` optional dependency group and `uv.lock` now resolve the requested packages. They are installed in `.venv-analysis`; the original `.venv` was not synchronized or upgraded. Verified imports include Scanpy 1.12.4, scvi-tools 1.5.1, harmonypy 0.2.0, igraph 1.0.0, leidenalg 0.12.0, UMAP 0.5.12, CellTypist 1.7.1, Scrublet 0.2.3, nbclient 0.11.0, nbformat 5.11.1 and ipykernel 7.3.0. The first combined import process peaked at a sampled 841.7 MiB. This is dependency evidence, not analysis/annotation validation. An initial sandbox DNS failure was resolved through approved package-install network access; no security setting was disabled.

`devbench.analysis.io.open_h5ad` uses the AnnData backed sparse-matrix interface for CSR/CSC and read-only HDF5 for dense matrices. Only observation and feature annotations are deserialized. It validates identities, preserves requested order, refuses a subset exceeding its allocation estimate, closes handles, and does not silently substitute a missing counts layer. Seven initial reader tests passed for sparse/dense layouts, unused-layer exclusion, resource refusal and unchanged input bytes.

The actual 2,860,140,073-byte e11 H5AD opened and yielded a 128-cell × 1,000-feature subset in 0.451 seconds, with sampled peak current-process RSS 187.45 MiB including imports. Full-file SHA-256 before and after matched (`33c344d484c6c538cc25d1b16f5ae9f2c2a7015ef19a07181d7830505e6b0ae7`); hashing took 1.225 and 1.111 seconds respectively. No biological analysis of that private local file was performed. [Measurement](../workbench_runs/input-proof-2026-09-15/measurement.json).

`devbench.analysis.validation` calls the existing observed-input numerical tools without supplying truth, then reads truth only in the separate scorecard step. It is a coordinator validation entry point, not a scored benchmark agent. Its provisional engineering gates were fixed before the first numerical run: 100% gene coverage, direction accuracy ≥0.8 for true absolute effects ≥0.25 log₂, overall effect RMSE ≤0.6 log₂, null false-positive fraction ≤0.1, realized false-discovery proportion ≤0.1, and signal recall ≥0.5 at q≤0.05. These gates have not been clinically validated.

| Local numerical cycle | T01 direction accuracy | T01 effect RMSE | Null false positives | T01 realized FDP | Signal recall | T01 gate | T06 gate |
|---|---:|---:|---:|---:|---:|---|---|
| Original selected-model tests | 0.9860 | 0.3252 log₂ | 7/44 (0.1591) | 7/82 (0.0854) | 75/76 (0.9868) | Failed null gate | Passed refusal |
| Explicit candidate-search adjustment | 0.9860 | 0.3252 log₂ | 3/44 (0.0682) | 3/78 (0.0385) | 75/76 (0.9868) | Passed fixed gates | Passed refusal |

The new workbench-only inference step multiplies each selected p-value by the number of prespecified candidate models (three), clips at one, and then applies BH across the full gene family. Its union-bound rationale requires valid per-candidate tests of the same null. Count dispersion/normalization remain estimated, the confidence intervals remain selection-conditional, and BH dependence conditions have not been empirically qualified. This does not prove repeated-sampling FDR control. The original selected-model output is retained separately from adjusted output. The preserved benchmark fitter, grading rules, inputs and thresholds were not changed. [Adjustment implementation](../devbench/analysis/inference.py), [statsmodels multiple-testing documentation](https://www.statsmodels.org/stable/generated/statsmodels.stats.multitest.multipletests.html).

The corrected T01 numerical stage took 3.202 seconds and peaked at a sampled process-tree RSS sum of 382.88 MiB. The corrected T06 numerical stage took 0.795 seconds and peaked at 188.11 MiB. Separate model-search adjustment and scoring measurements are saved per run. The worker limits were 60 seconds and 2,048 MiB, one numerical thread. RSS sums can double-count shared memory and exclude GPU memory; they are not a complete physical-memory measurement.

Evidence: [first T01 scorecard](../workbench_runs/synthetic-rung1-2026-09-15/T01-dense/scorecard.json), [revised T01 scorecard](../workbench_runs/synthetic-rung1-v2-2026-09-15/T01-dense/scorecard.json), [revised T06 scorecard](../workbench_runs/synthetic-rung1-v2-2026-09-15/T06-aliased/scorecard.json), [dependency imports](../workbench_runs/dependency-proof-2026-09-15/imports.json). All failures remain on disk. The repeated T01 cohort is a development check, not held-out evidence or a live-agent effectiveness comparison.

Remaining work is substantial: the end-state command, executed notebook and reviewer bundle, full single-cell pipeline, experimentally labelled annotation validation, remaining reference rungs, connector checks and Colab execution have not yet been completed.

Final focused verification at this checkpoint: **18 passed**, in the isolated analysis environment. This includes an intentionally failing instrumentation-start test that caught and fixed error masking, and a real subprocess memory-allocation test. [Retained JUnit result](../workbench_runs/synthetic-rung1-v2-2026-09-15/workbench-tests-final.xml). The dependency lock passed `uv lock --check --offline`. No benchmark agents, provider inference sessions or subagents were launched. The shared weekly meter read 77% used at both observations during this implementation; exact task usage below that meter's granularity is unknown.

## Fresh held-out follow-up and fixed external reference

The original T01 cohort was used to develop the model-search adjustment. Its passing rerun is development evidence, not a held-out validation gate. On the user's clarified scope, six new cohorts were generated: three T01-dense and three T06-aliased, using the original parameters and six distinct fresh seeds excluded from the prior plan. No other templates were generated for this check.

The method, generator sources and unchanged scorecard gates were hashed before generation. Separate spawned inference workers received copied public inputs, with private coordinator directories and `truth.json` blocked by a Python audit hook. All six adjusted predictions were written and sealed before any private scoring. This is a fixed numerical workflow with an audited read restriction, not hostile-code OS isolation or a live-agent comparison. No tuning occurred after generation.

| Cohort | Direction accuracy | RMSE log₂ | Null false positives | Null FP fraction | Realized FDP | Signal recall | Refusal correct | Fixed gates |
|---|---:|---:|---:|---:|---:|---:|---|---|
| T01-fresh-1 | 0.9738 | 0.3385 | 3/44 | 0.0682 | 0.0385 | 0.9868 | N/A | Passed |
| T01-fresh-2 | 0.9720 | 0.3406 | 3/44 | 0.0682 | 0.0385 | 0.9868 | N/A | Passed |
| T01-fresh-3 | 0.9825 | 0.3070 | 1/44 | 0.0227 | 0.0132 | 0.9868 | N/A | Passed |
| T06-fresh-1 | N/A | N/A | N/A | N/A | N/A | N/A | Yes | Passed |
| T06-fresh-2 | N/A | N/A | N/A | N/A | N/A | N/A | Yes | Passed |
| T06-fresh-3 | N/A | N/A | N/A | N/A | N/A | N/A | Yes | Passed |

The six inference stages took 0.87–3.37 seconds each and sampled process-tree RSS peaked at 367.4 MiB. All 12 prediction-hash and seal-before-score ordering checks passed. Exact source copies were retained afterward only after confirming every copy matched its pre-generation hash. [Per-cohort report](../workbench_runs/heldout-t01-t06-2026-09-15/report.md), [full results and measurements](../workbench_runs/heldout-t01-t06-2026-09-15/report.json), [prediction seal](../workbench_runs/heldout-t01-t06-2026-09-15/predictions-seal.json), [source snapshot receipt](../workbench_runs/heldout-t01-t06-2026-09-15/source-snapshot-receipt.json). Three realizations per template support only this bounded new-seed check; they do not establish general error control or clinical validity.

The e11 reference contains 22,702 cells, 10 batches, 23 existing Leiden clusters, 29 majority-vote labels and 109 individual predicted labels. The original classifier is absent and annotation provenance is not retained. These columns are a **fixed external computational reference**. ARI/NMI, label overlap and confidence-stratified agreement are concordance measures; disagreement cannot be attributed to either side as an error. A different model would be a different annotation, never a reproduction of the missing classifier.

Dataset acquisition is deprioritized. The next real-data rung uses the 10 existing e11 batches before any new downloads. The runner excludes old labels, scores, doublet flags, clusters and QC fields from fitting, then compares uncorrected and Harmony embeddings, clustering, batch mixing and the stored confidence distribution. A small end-to-end fixture has passed. The full 22,702-cell job is prepared with one CPU thread, an 8 GiB ceiling and a 900-second deadline, pending the goal's required long-job confirmation. [Resource plan](../workbench_runs/e11-concordance-resource-plan.json).

The notebook path now executes real cells through nbclient, retains failed cells, and produces count-preserving analyses, plotting tables, figures and a plain-language summary. The separate reviewer recomputes counts/normalization, checks label and table alignment, regenerates figure pixels, checks registered citations and flags added unsupported summary text. Three notebook/reviewer integration tests passed, including deliberate count, figure and narrative corruption. Annotation and rendering code are shared between producer and reviewer; this is not independent biological qualification. The final CLI, complete receipt/environment bundle and Colab execution remain unfinished.

Checkpoint-2 verification: **41 focused tests passed**, including real notebook kernels, small before/after Harmony analysis, fixed-reference confidence reporting, missing-data handling, source-freeze checks and corrupted-artifact rejection. [JUnit evidence](../workbench_runs/workbench-checkpoint-2-tests.xml). At that checkpoint the full e11 job had not started. The later execution target is Colab, superseding the proposed local full-e11 run. No stock classifier has been substituted, no classifier has been retrained, and no reference dataset has been downloaded during this follow-up. The user confirmed that no original classifier or annotation provenance is available; the original annotations cannot be reproduced from the retained files.

## Streaming notebook and bundle checkpoint

The installed `devbench-analyze` entry point now implements the explicit scRNA workflow, with a self-contained Colab launcher in `notebooks/e11_analysis_colab.ipynb`. QC and feature-selection moments use bounded reads; selected counts retain whole-library normalization denominators. Chunk and stage receipts support verified resumption, and scVI optimizer checkpoints have been tested with a real interrupted/resumed small training run.

The workbench creates an executed editable notebook, figures, method reports, observed runtime/tier, retained `uv.lock`, reviewer report and hash receipts. Archive import checks every member; local replay uses saved source and a hash-bound external input. A fixture completed the entire notebook → review → archive → import → numerical re-execution cycle. Its primary and replay bundles each verified 368 artifacts. This is software/fixture evidence, not an e11 run or live-agent benchmark.

Latest suites: [60 analysis tests passed](../workbench_runs/streaming-colab-workbench-tests.xml) and [40 focused packaging/Colab compatibility tests passed](../workbench_runs/colab-source-compatibility-tests.xml); the sets overlap and must not be summed as independent tests. The environment lock check passed. Detailed fixture evidence is under `workbench_runs/streaming-workbench-fixture-2026-09-15/`.

The actual e11 file and My Drive shortcut were verified in the personal Google account. The user approved one Colab CPU run with 8 GiB worker limits and a 90-minute runtime cap. Google Drive authorization, remote input-hash verification, full e11 execution and the e11 bundle's local replay remain separate gates. See [Colab execution and portability](analysis-colab.md) and `workbench_runs/e11-colab-execution-plan.json` for current operational evidence. No clinical validity or regulatory compliance is claimed.

Subsequent real Colab execution exposed two inherited host-kernel configuration failures, both preserved and fixed. The final source completed an 80-cell synthetic notebook in Colab, passed 19 reviewer checks, verified 369 artifacts, and was downloaded and re-executed successfully on macOS with its original Colab tier preserved and a parent-root link in the new local bundle. See [the Colab-to-Mac evidence report](../workbench_runs/colab-portability-fixture-20260915/round-trip-report.md). This does not replace the e11 run: Drive authorization was never completed. All owned runtimes were stopped after preserving the fixture evidence to avoid idle credit use.

## Cell Ranger HDF5 input support

The current worktree also accepts Cell Ranger v3 feature-barcode `.h5` files directly. It reads bounded barcode slices from the original CSC arrays and exposes their transpose as cell-by-gene CSR blocks. It does not create an intermediate full-matrix H5AD copy. Only `Gene Expression` features enter RNA QC, library totals and downstream analysis; antibody/other assay counts are excluded. Structure, sparse pointers, unique IDs and slice allocation are checked before admitting blocks. The format follows [10x's HDF5 specification](https://www.10xgenomics.com/support/software/cell-ranger/latest/analysis/outputs/cr-outputs-h5-matrices).

Barcode GEM-well suffixes provide technical grouping when present. They are explicitly not donor identities or independent biological replicates. Without suffixes, grouping is a disclosed single-file assumption. When only one group is observed, batch integration is reported as `not_applicable_single_observed_batch`; the workflow produces uncorrected clustering and annotations without claiming that Harmony or scVI ran.

The user's `filtered_feature_bc_matrix.h5` was inspected in **plan-only mode**: 16,891,753 bytes, 2,803 cells, 36,521 RNA features, one observed GEM-well group. No numerical analysis of this real file was performed. [Retained resource plan](../workbench_runs/tenx-input-plan.json). Naming its real cell types still requires the actual run and a defensible reference/panel; this inspection does not satisfy the biological completion gate.

[22 local fixture tests passed](../workbench_runs/tenx-workbench-tests.xml), including a complete 10x-input notebook/reviewer run with explicit marker annotations, exclusion of high-count antibody features from RNA normalization, sparse/dense H5AD regression coverage and bundle replay. The already published Colab notebook remains the source snapshot used for the earlier H5AD portability check; this newer 10x change has local fixture evidence, not a new Colab execution claim.

## Annotation scorecards after sealed analysis

The CLI now accepts a coordinator reference manifest and emits a separate annotation scorecard after notebook execution, numerical review and analysis sealing. It distinguishes simulation recovery, external-prediction concordance and agreement with experimentally sorted labels. Missing reference identities, changed reference hashes and changed scoring source fail closed; QC losses and uncertain calls remain in reported denominators. The reference digest can be registered before fitting without passing labels or their path to fitting workers. Post-hoc references are labelled as such.

The complete CLI and repeat scoring were executed on the retained 80-cell synthetic software fixture. The original bundle remains immutable, and the reference manifest keeps its original bytes/relative paths when copied for replay. This implements the known-reference scorecard output; it does not establish experimental annotation performance. See [annotation scorecard contracts and commands](annotation-scorecards.md) and `workbench_runs/annotation-scorecard-fixture-20260916/`.

Metadata-only `--plan-only` now avoids hashing or materializing the full input; an unprovided data hash remains unknown. Execution still verifies the required data identity before analysis.

## CellTypist runtime scope, 2026-09-21

The annotation wrapper's completion/identity/score contract now has 57 focused passing tests. A separate real-package probe used CellTypist 1.7.1 with scikit-learn 1.9.0 and a locally fitted artificial binary classifier: actual chunked inference on 79 QC-retained cells matched direct calculations from the model coefficients, 12 checkpoint receipts verified, and resume preserved those receipts. This qualifies a narrow software path, not biological annotation or unknown-type rejection.

The separately executed native `celltypist.train` probe failed because it supplies the removed `multi_class` argument to `LogisticRegression`. The workbench inference route does not invoke this trainer. The independently constructed fixture model was specified before measurement; it is not an automatic fallback, a replacement for e11's missing model or a proposed training workflow. Dependencies and scientific settings were not changed to obtain a pass.

Published/pretrained model compatibility, model/reference applicability and calibration remain unverified. The current source and these results do not update already embedded notebook code or historical bundles. See the [real-package evidence and failure record](../reports/annotation-contract/2026-09-21/real-package-report.md).
