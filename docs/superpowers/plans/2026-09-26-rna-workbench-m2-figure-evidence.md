# RNA Workbench M2: Scientific Graph Library, Linked Exploration and Figure Evidence

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox syntax for tracking. No delegation or implementation is initiated by this document.

**Goal:** Let a scientist inspect supported RNA-seq graphs and their underlying observations/calculations, save exploratory selections, revise presentation, and export reproducible evidence. Cover the complete requested graph inventory through explicit delivery stages and scientific prerequisites.

**Architecture:** Extend existing DevHarness/DevBench tools, episode/native execution paths, renderers, controller and verified bundles. Separate calculation artifacts, exploration state and presentation revisions; preserve exact population/unit/layer identity between linked views. The existing local controller owns bounded calculation/rendering work and its failure/cancellation outcomes.

**Tech stack:** Existing Python, Matplotlib, Plotly, bounded H5AD/Zarr/table access, JSON receipts, local HTTP service, HTML/JavaScript and pytest. Evaluate an existing linked-view component before introducing a new frontend dependency. No new orchestration framework or implicit change to scientific estimators.

---

## Decision and evidence

**September 27 organization addendum, revised after the [organization audit](../../../reports/rna-agent-readiness/2026-09-27-organization-audit.iro5gaqt/research-organization-audit.txt):** apply the [organization contract](../../research-workspace-organization.md) to future graph/selection work. The local label and metadata-recovery corrections have [separate evidence](../../../reports/rna-agent-readiness/2026-09-27-organization-audit.iro5gaqt/README.md) and do not start M2b. Distinguish WorkspaceViewState, scientific object/version identity, Run versus ExecutionAttempt, Result versus Artifact, and MethodVersion/ValidationRecord. Exact branch bases, current permission checks, typed proposals, scoped canonical roles, migrations/tombstones and separate execution admission remain required future contracts. Scientific acceptance never grants computation. The source inspection below records the original planning baseline before M2a implementation.

This revision incorporates the supplied **Evidence-Based Requirements Assessment for an Agent-Driven RNA-seq Research Workbench**, including all **22 graph families**, the interaction taxonomy and seven acceptance journeys. The attachment SHA-256 is `d6417c812f96c797bcbaa646539d4eba4a2c92a6797cca8b78e724e263af0386`. Its graph-matrix rows are mapped to stable G01–G22 identifiers below in source order.

The earlier plan covered only existing QC/embedding figures and styling. This revision adds core expression graphs and linked inspection, a subsequent sample-aware result increment, and explicit extension stages. Immutable presentation revisions remain the first checkpoint. A graph requirement or installed renderer does not establish a supported scientific workflow. This advances portions of AC-1/3/4/8/9/11/13/14/15; completion remains contract-specific.

The active WF-4 renderer is `devbench/analysis/reporting.py` (`render_concordance_figures`, `export_report`), with interactive output in `html_report.py` (`write_interactive_figures`). `plotting.py` belongs to a separate older pipeline and is outside this change. Current interactive JSON preserves coordinates but its traces omit stable cell IDs. `streaming_review.py` regenerates both static and interactive figures. The operational UI previews PNGs; it has no figure-version controls. `bundles.py` rejects rewriting a sealed bundle.

Further source inspection grounds the new graph work:

- `embedding.py` persists PCA `latent.npy` without explained-variance ratios. Proper PCA axes require a new receipt-bound variance artifact from fresh runs. `embedding.csv` carries UMAP coordinates and cell IDs; no t-SNE producer was found in this path.
- `streaming.py` writes `selected.h5ad` with HVG-selected genes, full-library-total normalization to 10,000 followed by `log1p`, and selected raw counts in `layers["counts"]`. Arbitrary marker genes cannot be assumed present. Use bounded `open_expression(...).subset(...)` reads from approved input for additional features, with explicit normalization provenance.
- `annotation.py` produces flag-only doublet scores/predictions and per-batch reports, which may be partly unavailable. Its library/batch assumption does not establish donor identity. No ambient-RNA producer was established.
- `episode_synthesis.py` binds `mean_condition_analysis` and `cell_type_state_analysis` results to receipts; this does not establish that every diagnostic/DE plotting field exists.
- The existing interactive output also includes stored-versus-recomputed QC and stored classifier-confidence histograms. Preserve those views and their explicit limits: confidence calibration is unknown and external-label concordance is not accuracy.

Fresh read-only baseline checks during planning found that all 12 files in the M1 completion audit still match their retained hashes. The saved broad JUnit contains 533 cases and 3 failures; the saved targeted correction run contains 2 passing cases. These are saved-record/integrity checks, not new test execution. T14 remains unresolved. The checkout's top-level files remain untracked, so Git diff is not the implementation baseline. `.venv-analysis/bin/python` exists.

Source requirements: the new assessment, `handoff.md`, `docs/rna-workbench-requirements.md`, `docs/rna-workbench-status.md`, and the earlier objective. Internal `turn...` citation markers are not independently resolvable sources here. Verify primary documentation and pinned compatibility before adopting new methods/components. The assessment describes four screenshots; the handoff records only three images as actually inspected. This planning task performed no new image or vendor-feature validation.

## Scope

| Stage | Deliverable | Completion boundary |
|---|---|---|
| **M2a: evidence and presentation** | Typed figure evidence, exact backing data, version inspection, immutable style edits and portable render replay for current QC/embedding figures. | Unchanged scientific hashes; zero numerical/model launches for styling. First independent implementation checkpoint. |
| **M2b: core graphs and linked exploration** | QC distributions/scatter, available PCA/embedding views, feature maps, marker dots, violin/box/ridge, heatmaps, descriptive sample composition and doublet overlays; linked inspection and named exploratory selections. | Every delivered family reconciles with saved tables/IDs; missing data or producers remain explicitly unavailable. |
| **M2c: sample-aware result views** | Sample correlation/PCA, model diagnostics, MA/volcano with replicate companions, and compatible DA/enrichment result adapters. | Valid biological units/designs and actual numerical outputs. T14 must be resolved before enabling its affected cellular effect route; missing diagnostics cannot be invented. |
| **E1: temporal/velocity extensions** | G18 trajectory/trend views, then G19 velocity and gene-fit diagnostics. | Separate input/method/validation contracts. Existing tools alone do not establish an end-to-end workbench route. |
| **E2: specialized assay views** | G20 splice/isoform/junction, G21 spatial, G22 perturbation/cross-species. | Separate sequence/coordinate/mapping/experimental prerequisites and evaluated workflows, one bounded extension at a time. |

M2a is followed by M2b; M2c is a separately testable increment. M2 is not fully delivered when styling alone works. A disabled adapter is not a completed graph. E1/E2 retain concrete requirements without advertising support from ordinary expression matrices.

Presentation options include dimensions, point size, opacity, fonts, legend placement, category-color mapping and positions/repulsion of **unchanged label IDs/text**. This explicitly supports the label-overlap journey. Scientific captions, axis meanings, category names, grouping, layers, filters, values, statistics and clustering cannot change through a style endpoint. Clipping/scales/display sampling require the disclosure rules below. Free-text scientific captions need separate versioned evidence review.

Existing legacy bundles remain readable/downloadable; revision is unavailable when required semantics are absent. Preserve existing QC/confidence/concordance views. Never edit historical bundles or synthesize missing evidence.

## Complete graph catalog

All families require exported backing values, provenance, unit/layer/population labels, and numerical plus visual checks. Source availability is separate from renderer implementation and scientific validation. Subtypes are separate adapters when their calculations differ.

| ID / family | Data and scientific semantics | Interaction and acceptance | Source/status and delivery |
|---|---|---|---|
| **G01 — Library-size / detected-gene / mitochondrial-QC distributions** | Per-cell count-derived metrics; bins/counts/density and transforms; sample/batch/condition strata. | Bin/point membership links exact IDs; recompute metrics and bin totals; selected/pre/post-filter N reconcile. No universal QC cutoff or hidden outlier removal. | Existing QC data, new distributions/membership adapters. **M2b**. |
| **G02 — QC scatterplots** | Exact QC coordinates and units, declared color diagnostic; distinguish zero/missing and log transforms. | Point-to-embedding links; coordinate and point-count equality with table; selections return stable IDs. | Existing static/interactive QC; evidence retrofit **M2a**, links **M2b**. |
| **G03 — Doublet / ambient-RNA diagnostics** | Actual method/version scores, calibration strata and thresholds; flag-only evidence, not definitive doublet identity. | Score distributions/embedding overlays; partial-unavailable status; before/after counts and score reconciliation. | Existing doublet prediction/report artifacts, new view adapters **M2b**. Ambient producer not established; unavailable until separately supported. |
| **G04 — Sample correlation / sample PCA** | Independent-sample or audited pseudobulk matrix with normalization and sample/condition/batch/pairing. | Sample links, exported correlation/scores/loadings/variance; symmetry where applicable and full sample-ID checks. | New adapter over verified episode/sample artifacts. **M2c**; cells cannot substitute for sample replication. |
| **G05 — Mean–variance / dispersion diagnostics** | Real model means, observed/fitted dispersion/variance/precision and fitted trend; filtered genes retained as status. | Link genes to fits; reconcile plotted values, trend and transform. No derived dispersion invented from effect tables. | Producer-field inventory required; expose only actual validated diagnostics. **M2c**. |
| **G06 — Residual / design diagnostics** | Actual residuals/fitted values, design/covariates, supported leverage/influence measures; residual type explicit. | Link sample/gene points; verify rank, estimability and residual values. Full rank is not proof against confounding. | Existing design validators reusable; residual output availability remains a gate. **M2c**. |
| **G07 — PCA** | Declared transformed feature matrix, scores/loadings, feature identity and explained variance; cell/sample unit explicit. | Choose components/facets, stable-ID links; axes show actual variance percentages. | Stored latent scores exist but variance artifact is missing; add to fresh producer output. Cell PCA **M2b**, sample PCA **M2c/G04**. |
| **G08 — UMAP / t-SNE** | Exact coordinates plus representation/neighbors/configuration/random state; no physical/global-distance implication. | Metadata/gene colors, unintegrated/integrated comparison, sampled-display disclosures; coordinate/configuration checks. | Existing UMAP path: **M2a** evidence, **M2b** linked views. t-SNE producer not found; remains unavailable until explicitly implemented/verified. |
| **G09 — Feature expression plot** | Values from explicit `.X`, `.raw` or named layer; gene IDs, normalization, transform and missing policy. | Hover value/layer, clipping bounds and shared/independent scales; distinguish missing/zero; check against bounded source slices. | New bounded feature adapter; HVG-only `selected.h5ad` cannot satisfy arbitrary marker requests. **M2b**. |
| **G10 — Marker dot plot** | Group×gene numerator/denominator, expression threshold, fraction, mean basis and scaling; disclose pooling across donors. | Size/color legends identify exact statistics; dot hover highlights numerator/denominator memberships; recalculate rows independently. | New aggregate payload and renderer over approved features/annotations. **M2b**. |
| **G11 — Violin / box / ridge expression plots** | Exportable expression values/summaries, zeros/missing, layer, cell N and biological-unit N; KDE bandwidth/box convention. | Group/cell links and donor overlays; verify quartiles/density inputs/counts. Many cells do not create independent replicates. | New adapters for all three forms. **M2b** descriptive; **M2c** inferential companions where supported. |
| **G12 — Heatmap** | Gene×observation/group matrix, aggregation, absolute/log/row-z scale, order/clustering/linkage method/version. | Inspect exact values and group/feature IDs; export matrix/order; no absolute-expression inference from row-scaled colors. | New bounded matrix/aggregate renderer. **M2b**; program statistics require verified sources. |
| **G13 — MA plot** | Verified average abundance, signed effect/effect definition, shrinkage status and p/q/tested-family fields. | Gene links and stated thresholds; all coordinates and threshold counts reconcile; failed/filtered genes remain visible as status. | New receipt-bound result adapter with availability checks. **M2c**. |
| **G14 — Volcano plot** | Actual signed effect and explicitly named `-log10(p)` or `-log10(q)`; unit/design/contrast preserved. | Flag clipping/zero/underflow without fabricating finite p-values; link gene to replicate companion; reconcile transforms. | New adapter, gated by valid results and T14 for its affected route. **M2c**. |
| **G15 — Replicate-level expression / effect plot** | Independent donor/specimen/sample values and pairing; model estimate/CI only if actually supported/produced. | Points identify units; pair lines reflect real pairing; do not omit influential donors; N matches design/membership. | New audited bulk/pseudobulk joins and renderer. **M2c**. |
| **G16 — Cell proportion / differential-abundance plots** | Counts/denominators per independent sample; optional validated type/neighborhood effect/FDR. Sampled composition is not absolute tissue abundance. | Sample points, condition/denominator labels; proportions reconcile; distinguish descriptive composition from inferential DA. | Descriptive sample composition **M2b** with declared identity. Inferential DA **M2c**, only after compatible replicated-method contract. |
| **G17 — Enrichment / gene-set plots** | Verified ranked result/sample scores, gene/species IDs, database/version, background/tested universe, score/FDR/set size and leading edge where defined. | Set→gene→result links; verify membership/statistics; association language, not causal activation. | Existing enrichment tools are candidates, not guaranteed pathway producers. Compatible result adapters **M2c**; missing producers require separate method work. |
| **G18 — Pseudotime / trajectory plots** | Fitted graph/order/branches and trends/uncertainty; separate collection age from dimensionless inferred pseudotime. | Root/parameter sensitivity, smoothing provenance and model replay; no lineage/elapsed-time claim from ordering. | Existing temporal tools need dedicated workbench contract/adapter. **E1**. |
| **G19 — Velocity fields** | Spliced/unspliced measurements plus fitted kinetics and gene-level diagnostics. | Vectors/streamlines and sensitivity; reject missing layers; arrows are model-dependent estimates. | New prerequisite-gated workflow. **E1**, separate from trajectory support. |
| **G20 — Splice / isoform / junction views** | Junctions, supported transcript estimates or authorized alignment-derived evidence; genomic coordinates/strand/build and sample replicates. | Junction/event-table reconciliation, isoform proportions and support inspection. No sashimi evidence from gene counts. | Dedicated sequence inputs/methods. **E2**; no BAM/FASTQ download authorized by this plan. |
| **G21 — Spatial maps** | Coordinates/transforms, expression and image/segmentation/physical scale where applicable; spot/cell/region unit. | Tissue/feature links; ID and registration checks, transform round-trip and comparable scales. | Dedicated coordinate-aware adapter; assess SpatialData/Vitessce reuse. **E2**. |
| **G22 — Perturbation / cross-species comparison views** | Separate contracts: guide/control/replicate identity; or species/gene IDs and versioned one-to-many orthology. | Audit control/guide or orthology joins and sample effects; label predictions and mapping multiplicity. | Separate experiment-specific adapters/evaluations. **E2**; no forced one-to-one mapping or predicted-as-observed effects. |

Each catalog entry must separately track `implementation_state`, `verification_level`, and `current_input_eligibility`. Unavailable graphs show required inputs and a typed reason. Rendering code alone cannot promote an assay or estimator to supported status.

## Interaction and invalidation rules

| Interaction | Operation and required behavior |
|---|---|
| Pan/zoom/hover; stable palette; dimensions/fonts/legend; move/repel unchanged labels | Presentation only; retain saved render state; no numerical stage or renewed scientific approval. |
| Continuous color clipping, shared/independent panel scales, display downsampling | Presentation only when full backing values/statistics/population remain unchanged. Show clipping bounds/counts, independent scaling and rendered N / analysis N; record sampled IDs/seed. |
| Change smoothing, binning, aggregation, transformed values or gene calculation | New derived-data artifact under existing eligibility/resource/approval rules. These are not style edits. |
| Lasso/filter/exclude cells or samples | Named exploratory population with exact IDs, origin/filter/context. Branch or invalidate dependent calculations. A lasso on sampled points selects only those IDs unless an explicit full-data query is executed. |
| Change layer, annotations, cluster membership, design or contrast | New scientific state; mark dependent results/prose stale and invalidate consequential approval bindings. Preserve prior versions. |
| Switch to already verified feature/graph artifact | Reuse that artifact's exact state. If absent, request calculation; never silently substitute a gene/layer. |
| Use selected cells for DE | Keep data-selected/exploratory status; require biological units, estimable design and a reviewed supported route. No automatic confirmatory claim. |

Side-by-side integration views disclose input/feature/scale differences. They do not rank methods by visual mixing. A ranking requires protected-biology and batch-removal metrics, prespecified criteria, confounding checks and retained failed runs.

## Proposed contracts

The names below are proposed APIs, not existing capabilities.

`figure_catalog.py` owns family eligibility and producer/renderer status. `figure_artifacts.py` owns strict specifications and `validate_figure_artifact`/`validate_display_options`. `figure_data.py` produces bounded calculation payloads; `figure_renderers.py` renders frozen payloads. `exploration_state.py` owns versioned linked state/selection provenance. `figure_results.py` adapts existing verified scientific outputs. `figure_revisions.py` owns immutable presentation execution/replay. Rendering never launches numerical analysis.

Each figure record contains the following common fields plus its typed payload:

```text
format = devbench.figure/1
figure_id, figure_kind, original_run_id
analysis_binding = input_sha256, parameters_sha256, analysis_source_sha256,
                   generating_stage_receipt_sha256
semantics = representation_or_layer, statistical_unit, population_sha256,
            filter_sha256, grouping, aggregation, axis_meanings, limitations
analysis_n, rendered_n, contributing_observation_n, independent_unit_n_if_known
plot_data = relative_path, sha256, key_columns, payload_kind, membership_references
display_options, renderer_source_sha256, renderer_environment_sha256
image = relative_path, sha256
```

QC metrics state their count source/transformation; embedding coordinates are a latent view, not an expression layer. Encode inapplicable aggregation/statistical fields explicitly, and preserve external-label provenance/concordance limits. Payload kinds cover observation points, sample points, gene results, group×gene aggregates, ordered matrices, model graphs, genomic events and spatial geometries. IDs match the unit: cells, biological samples, genes or composite group/gene keys. Every aggregation retains contributor membership; a global cell-ID schema is inappropriate.

Default to all points on small fixtures. If display sampling is enabled, retain full scientific populations/statistics, sampled IDs/seed and visible rendered N / analysis N. Missing is distinct from measured zero; failed estimates stay missing/failed. Finite coordinates/statistics or explicit exclusion/clipping status are required. Population identity is order-independent; serialized table hashes remain byte-specific. Never replace absent or failed estimates with successful-looking values.

`devbench.exploration/1` binds dataset/snapshot, observation/feature universes, layer, exact selected-ID membership/hash, filter AST, grouping/aggregation, transformations, annotation version, scales/clipping, palette, display sampling and linked-view configuration. A named selection additionally retains originating graph/version, parent population, selection action and condition-informed context, with `exploratory=true` propagated into downstream results/claims. Screen coordinates alone cannot reconstruct a population.

G10 backing rows retain group/gene IDs, total/observed/missing/expressing counts, denominator policy/count, fraction, mean, all-cell-versus-expressing-cell mean basis, threshold, layer and scaling. Empty denominators are unavailable, not zero. G11/G12 retain KDE/binning/scaling/order/clustering calculations. G13/G14 retain the exact effect definition/base, contrast, shrinkage status and tested p/q family; do not relabel arbitrary coefficients as log fold changes.

Bound matrix slices, plot tables and response sizes. Preserve sparse/backed access and do not load expression matrices over 100 MB into pandas. Tiny fixtures establish no e11/100k/1M scale claim.

A completed base bundle cannot contain its own final root hash. The service attaches the verified parent root when it serves a figure or prepares a revision. A revision record contains `format=devbench.figure-revision/1`, `parent_bundle_root`, `parent_figure_id`, `parent_figure_data_sha256`, immutable semantic/analysis bindings, normalized display options, author label, request identity, renderer source/runtime identities and terminal outcome. Author labels remain caller-asserted.

Presentation bundles use the existing `devbench.analysis.bundle/1` receipt envelope with an explicit `artifact_kind=presentation`; missing kind retains current native-analysis behavior. Add explicit validation of this kind to bundle import/verification and numerical replay dispatch. A presentation bundle contains its exact plotting payload, semantic record, renderer source/environment, output, logs and parent receipt/root. It needs no raw matrix binding and must not be accepted by numerical replay. Preserve the original analysis identity separately from the renderer identity. Parent verification provenance is retained; this is not a new scientific approval or a substitute for the full parent analysis archive.

## File ownership

| Unit | Files | Responsibility |
|---|---|---|
| Catalog/contracts | Create `devbench/analysis/figure_catalog.py`, `figure_artifacts.py`; create `tests/test_figure_catalog.py`, `test_figure_artifacts.py` | Family prerequisites/status, typed scientific payloads and presentation validation. |
| Calculation/rendering | Create `devbench/analysis/figure_data.py`, `figure_renderers.py`; create `tests/test_figure_data.py`, `test_figure_renderers.py`; extend `embedding.py` and its existing checks for PCA variance output | Bounded explicit-layer reads, aggregate backing tables, receipt-bound PCA metadata, pure rendering. |
| Exploration | Create `devbench/analysis/exploration_state.py`, `tests/test_exploration_state.py` | Serializable common state, exact membership, provenance and invalidation. |
| Scientific result adapters | Create `devbench/analysis/figure_results.py`, `tests/test_figure_results.py`; integrate verified `episode_synthesis.py` outputs | Sample/gene/model result joins and eligibility gates; no alternate fitting in renderers. |
| Existing report integration | Modify `devbench/analysis/reporting.py`, `html_report.py`, `streaming_review.py`; modify `tests/test_analysis_workbench.py`, `test_analysis_html.py` | Capture real generating data and stage bindings; retain current outputs; add independent invariants. |
| Revision/portability | Create `devbench/analysis/figure_revisions.py`, `tests/test_figure_revisions.py`; modify `bundles.py`, `workbench.py`, `tests/test_analysis_bundle.py` | Immutable child artifacts, receipt kind validation, presentation replay and numerical replay rejection. |
| Controller/API/UI | Modify `workbench_ui.py`, `assets/workbench-ui.html`; create `tests/test_workbench_ui_figures.py`; modify `tests/test_workbench_ui_agent_asset.py` | Existing executor ownership, strict API, graph selection, linked state, version inspection and style controls. |
| Documentation/evidence | Modify `docs/workbench-ui.md`, `docs/rna-workbench-status.md`, `docs/rna-workbench-requirements.md`; create a fresh M2 report directory | Describe actual support and retain verification without overwriting M1. |

All shortened module names in this table are within `devbench/analysis/`; shortened test names are within `tests/`. Use absolute filesystem paths in code/commands, and portable logical names inside archive records. Do not edit the historical planning files.

## Implementation sequence

### 1. Freeze the baseline and graph catalog (M2a)

- [ ] Create a new report directory and retain hashes/snapshots of the files above before changing them. Record existing untracked state without staging or committing anything.
- [ ] Register G01–G22 with prerequisites, units/layers, producer/renderer status, allowed operations and typed unavailable reasons. Record the seven journeys against their actual submilestone coverage below; preserve M1's bounded recovery evidence.
- [ ] Inventory exact saved QC, embedding/PCA, annotation, aggregate and episode-result fields. Record absent producers or fields; do not expand a scientific method inside a renderer. Add catalog tests refusing velocity/junction/spatial requests from ordinary gene counts and sample inference without biological units.
- [ ] Record T14 as an unresolved scientific-contract issue. Diagnose it separately using permitted public contracts and execution outputs; do not inspect graders/private goldens, alter thresholds or call it unrelated. It blocks a claim of a fully green broad suite, but need not prevent an explicitly scoped figure-only milestone.
- [ ] Freeze exact style allowlist, bundle kind and parent/renderer identity fields before writing integration code.

### 2. Implement typed evidence and strict presentation inputs (M2a)

- [ ] Add contract tests first for the observation/sample/gene/aggregate payload union, exact joins, missing/zero distinction, finite-or-explicitly-excluded values, missing semantic fields, corrupted payloads and identity-preserving row order.
- [ ] Implement the proposed pure functions and bounded JSON/table loading. Use existing hashing and atomic-write helpers. A payload larger than the declared render budget fails explicitly; do not load an expression matrix to reconstruct it.
- [ ] Test valid display edits and reject unknown keys. Bounds: dimensions 320–2400 pixels each; point size 1–24; opacity 0.1–1; font size 8–28; legend placement from a fixed enum; category colors only for existing categories using validated hex colors. No executable expressions, HTML or arbitrary renderer code.
- [ ] Add independent invariants: point size or label-position-only changes preserve `plot_data.sha256`, `population_sha256`, `filter_sha256`, label text and `analysis_binding`. Group/layer/filter or density-bandwidth changes through the style endpoint return a required calculation/analysis revision. Test explicit scale/clipping/sampling disclosure.

### 3. Attach evidence to current WF-4 figures (M2a)

- [ ] Refactor rendering inputs into explicit saved plotting tables without changing scientific computations or existing output names. Add a figure manifest under `figures/` and hashes of the actual generating checkpoint receipts.
- [ ] Include stable cell IDs in Plotly `customdata`; join reference labels by IDs. Use one category-to-color map per semantic grouping across comparable panels. Preserve the existing offline embedded Plotly behavior.
- [ ] Render both initial and revised figures through the same bounded renderer over saved data. Existing report generation captures the renderer code and environment identities.
- [ ] Extend deterministic review to validate semantic/data bindings. Supplement shared-renderer regeneration with independent coordinate/ID/category assertions so producer and reviewer cannot pass solely by sharing the same mistake.
- [ ] Keep legacy output/read behavior intact; older bundles without the new manifest show `presentation_revision_unavailable` with a reason.

### 4. Add immutable presentation revisions and portable replay (M2a)

- [ ] Implement `prepare_figure_revision(parent_bundle, expected_parent_root, figure_id, display_options, output)` and `execute_figure_revision(output)` in `figure_revisions.py`. The output must be a new absolute directory outside the sealed parent.
- [ ] Verify the parent and exact selected figure before copying the bounded plot payload and evidence. Compute request identity from parent root, figure identity, normalized options and renderer identity. Identical requests reuse the existing outcome; failures/cancellations do not restart automatically.
- [ ] Extend existing bundle sealing/import/verification for the explicit presentation kind, with a closed artifact inventory and no raw external-input dependency. Numerical execution rejects that kind before opening input data.
- [ ] Add module CLI `python -m devbench.analysis.figure_revisions replay --bundle <absolute-directory> --expected-root <sha256> --out <new-absolute-directory>`. It verifies inputs and rerenders only the saved plotting payload, retaining a new receipt and parent lineage.
- [ ] Sentinel tests replace every numerical stage launch with a function that raises. Presentation revision and replay must succeed with those sentinels and with the original matrix unavailable. Force renderer errors and verify failed status, retained logs and no successful artifact publication.

### 5. Connect revision controls through the existing service (M2a)

- [ ] Add strict routes under the existing job endpoint: `GET /figures`, `POST /figure-revisions`, `GET /figure-revisions/<id>`, and verified revision download. POST accepts exactly `figure_id`, `expected_parent_root`, `display_options`, `author_label`.
- [ ] Use the existing workspace lock, admission/liveness checks, sequential executor and cancellation supervision. Add operation kind `presentation`; do not introduce another job queue or controller. Keep parent numerical job status/review untouched.
- [ ] The API refuses nonterminal parents, unverified/mismatched roots, unsupported legacy figures, stale renderer/source bindings, path escape and unknown fields. A parent with failed scientific review retains that status prominently; styling cannot turn it into a scientific pass.
- [ ] Add a compact evidence panel and style form beside recorded figures. Show original/revision identity, scientific metadata, analysis/rendered counts and parent review status. Provide keyboard-operable controls, text values and version selection; do not rely on color alone.
- [ ] Show the immutable source figure next to its revision and provide a separate presentation download. Explain that numerical results remain those of the original analysis. Test duplicate requests, concurrent requests, cancel/failure paths and stale parent identity.

### 6. Produce core graph data and renderers (M2b)

- [ ] Implement bounded `figure_data.py` adapters over explicit approved layers and saved QC/embedding artifacts. Calculation identity includes source, full population, selected features, layer, normalization, grouping and method parameters. `figure_renderers.py` consumes frozen payloads only.
- [ ] Deliver G01/G02, available G07/G08, G09, G10, all three G11 forms, and G12 in that order; then descriptive G16 with declared sample identity. Add G03 overlays from existing flag-only doublet artifacts, retaining partial availability and library/batch assumptions; ambient scores remain unavailable without a producer.
- [ ] Extend fresh PCA production in `embedding.py` to retain explained-variance ratios and required score/feature/loading identity. Compare retained values with the actual fit. Never guess percentages for old bundles or recompute a different PCA merely to label axes.
- [ ] Read requested markers using bounded `open_expression(...).subset(...)` against authorized inputs. Do not assume arbitrary genes exist in HVG-only `selected.h5ad`; preserve the full-library normalization denominator instead of renormalizing a selected gene subset.
- [ ] Materialize/export G10 numerator/denominator/mean tables and contributor membership before drawing. Independently test zeros, missing values, empty groups, unequal donors, expression threshold and all-versus-expressing-cell means. Spot-check displayed dot area/color and legends against the stored encoding policy.
- [ ] Export distribution/matrix values and exact KDE/binning/scaling/order/linkage settings. Add hand-calculable fixtures and visual checks for all delivered families; reject wrong-layer/feature mappings and retain explicit missing/filtered status.

### 7. Link graphs and retain exploratory selections (M2b)

- [ ] Compare existing Plotly interactions with a small Vitessce-style component prototype on the same offline fixture/state contract before choosing a new dependency. Check format support, offline embedding, state serialization, accessibility, pinned compatibility/license and complexity. Record a bounded decision; no framework replacement is presumed.
- [ ] Implement versioned exploration state and exact named-selection membership. Test permuted IDs, linked gene/group selection, stable palette mapping, wrong-population joins, stale annotations/layers and condition-informed selection provenance.
- [ ] Add authenticated strict handlers under existing job ownership for graph inventory/payload, `figure_calculation` and selections. Resolve approved artifact IDs server-side; do not accept arbitrary paths, executable filters or renderer code. Reuse the existing queue, lock, admission, cancellation and failure controls.
- [ ] Link embeddings, feature maps, marker/distribution, QC and sample composition through exact IDs and shared active population. Dot hover identifies numerator and denominator members; sample-level views explicitly aggregate cells into biological units.
- [ ] Add family selection with unavailable reasons, evidence panel, linked hover/filter/brush, named subset list, compare/restore controls and keyboard/table alternatives. Show layer/scales, independent-unit N, full/rendered N, exploratory context and parent review status. Do not rely only on color.
- [ ] Branch or mark dependencies stale after population/layer/annotation changes. Restore creates a new current pointer/branch, preserving history. Propagate exploratory status into downstream artifacts and claims; a UI action cannot turn it into confirmatory evidence.
- [ ] Test duplicate/stale concurrent requests, sampling/lasso membership, malformed IDs/filters, cancellation/failure and export/import of exact selection state. Retain source input read-only and never automatically run DE from a selection gesture.

### 8. Add sample-aware result adapters (M2c)

- [ ] Diagnose T14 using permitted public contracts and execution outputs before enabling the affected cellular effect adapter. Preserve definitions and thresholds; do not inspect graders/private goldens or tune using held-out feedback.
- [ ] Deliver G04/G15 from audited bulk/pseudobulk matrices, membership, design and pairing. Unknown independent-unit identity must be visible; never substitute batch for donor.
- [ ] Deliver G13/G14 from receipt-bound tables preserving actual effect definitions and p/q status, with linked G15 companions. Require true abundance for MA, valid p/q for volcano and supported CIs for effect intervals; otherwise show explicit unavailability.
- [ ] Add G05/G06 only from genuine diagnostic artifacts. If a necessary field is missing, specify the smallest producer-output extension and independent reference check before exposure; never fit a second model in a plotting adapter.
- [ ] Add inferential G16/G17 only after replicated-DA or gene-set method/database/background contracts pass. Keep descriptive composition/program scores distinct from DA/enrichment inference. Missing compatible producers require separate bounded method work.
- [ ] Test design N versus biological units, audited cell membership, complete confounding, invalid contrasts, omitted donors, effect/fit/table/plot consistency and p/q family handling with q ≥ p for fitted genes. Failed estimates remain failed, not zero. Record exactly which families passed and which remain unavailable.

### 9. Verify each checkpoint and update evidence (M2a, M2b, M2c)

- [ ] Run focused tests, then existing affected regressions and the synthetic verification suite with explicit local-fixture opt-in and numerical thread bounds. Freeze all source writers before source-capturing runs. Record test collection, skips, failures and environment; no provider-backed tests.
- [ ] Use fresh small offline fixtures, including unequal cells per donor, missing/zero values, conflicting markers, swapped layers, permuted IDs and absent prerequisites. Browser-observe each delivered family, linked/selected state and style/version controls against exported numeric values. A display-only edit must produce zero numerical launches and zero model sessions; derived-data jobs retain separate execution evidence.
- [ ] Export/import into a fresh directory with original input/planning paths unavailable. Verify receipts and rerender the presentation; reproduce exact selection IDs/state. Require exact semantic/table equality and identical pixels in the same pinned renderer environment; scientific replay uses prespecified tolerances. Cross-environment equivalence remains unclaimed without separate evidence.
- [ ] Inject calculation failure, duplicate submission, stale input and cancellation at relevant boundaries. Prove no orphan workers, stale success, erased failed attempts or unreviewed scientific reruns; keep existing M1 limits on recovery explicit.
- [ ] Observe an actual browser file save only through permitted browser tooling. If unavailable, report verified API transfer separately and keep browser save open; do not bypass the earlier native-app denial.
- [ ] Write a fresh M2 evidence index and report, update status/requirements with only observed coverage, and retain M1/T14/packaging/live-provider limitations. No commit, push, deployment, cloud run or paid call is part of this plan.

## Acceptance matrix

| Case | Required result |
|---|---|
| Change legend placement/point size | New immutable revision/image; identical data, filter, population, analysis and parent hashes; zero numerical/model launches. |
| Layer/population/gene/grouping/caption change through style endpoint | Reject and identify required scientific/calculation/review operation; no silent rerun. Named population changes through the separate selection API remain permitted and versioned. |
| Reordered input table | Observation/sample/gene/aggregate joins remain ID-based; malformed duplicate/missing keys fail. |
| Dot plot and expression distributions | Exact aggregate/value reconciliation, correct numerator/denominator and mean policy, disclosed layer/scaling/bandwidth/zero handling. |
| Display sampling or color clipping | Full scientific population/statistics unchanged; sampled IDs/seed and full/rendered N or bounds/clipped values disclosed. |
| Named condition-informed selection | Exact membership and source view survive export; dependent results/claims retain exploratory provenance. |
| Sample-level effect without units or estimable design | Refuse inference or show a clearly bounded descriptive view; no cell-as-donor substitution. |
| Parent or plot-data tampering | Fail verification before rendering; retained failure evidence. |
| Same revision request twice | One owned execution and same retained revision/outcome. |
| Renderer failure/cancellation | Failed/cancelled revision; original complete parent remains unchanged; no success download. |
| Parent scientific review failed | Revision preserves failed-review context and never adds scientific approval. |
| Original input/parent filesystem path unavailable | Portable presentation verifies and rerenders from included plot data; parent numerical reproducibility is not newly claimed. |
| Legacy bundle | Existing results/downloads still work; revision unavailable without required semantics. |
| Changed renderer/runtime | New attributable rendering identity or explicit incompatibility; never reuse a stale cache entry. |
| Broad regression | Exact new results retained; unresolved T14 remains a visible failure unless independently fixed within its permitted contract. |

## Seven research journeys and coverage

| Journey | Planned coverage and evidence |
|---|---|
| Donor-aware treatment/control comparison | **M2c**, gated on a supported route and T14 resolution where applicable: G04/G13/G14/G15 share design, membership and results. Unit removal causes refusal; numerical reference checks and matching sample N are required. |
| Explain labels and contradictory markers | **M2b** G08–G12 preserve annotation/source versions and expose positive/contradicting markers, unknown/ambiguous labels and donor strata. Annotation assistance does not establish reference-model accuracy. |
| Compare integration without erasing biology | **M2b** compares existing artifacts with IDs/scales/input differences visible. Actual ranking/effect preservation is a **separate scientific increment** with both mixing and protected-biology metrics, prespecified thresholds, confounding checks and included failed runs. |
| Improve overlapping labels without changing analysis | **M2a** changes positions/repulsion for unchanged label identities/text; backing data, population/filter/statistics and parent hashes stay fixed; zero numerical/model launches. Smoothing/exclusion is reclassified. |
| Inspect a selected population | **M2b** linked embedding/expression/QC/composition and exact named membership. Source artifact and condition-informed context survive export; derived results remain exploratory. |
| Resume and reproduce figures | **M2a/b** extend existing bounded recovery with calculation/render cancellation, duplicate/stopped-cache tests and relocated figure/selection replay. Arbitrary host-loss recovery is not implied. |
| Literature-backed interpretation | **Later claim/literature increment**: source identity and entailment checks, result reconciliation and stale prose after analysis changes. M2 supplies stable graph/result references, not complete narrative enforcement. |

Negative acceptance includes wrong dot denominators, missing-versus-zero errors, misleading independent scales, swapped layers, omitted donors, count-only velocity, gene-count junctions, absent spatial coordinates and reviewer text attempting to override a deterministic failure.

## Runnable offline verification commands

These commands are for implementation time, after new test modules exist. They have not been run as part of planning. Use the repository's retained M1 synthetic command selection for the broad verification gate; do not replace it with a provider campaign or claim skipped numerical fixtures as passes.

```bash
M2_EVIDENCE_DIR="$(mktemp -d /Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-agent-readiness/2026-09-26-m2.XXXXXX)"
env PYTHONPATH=/Users/vajraang/Documents/ChatGPT/bio-agent-harness DEVBENCH_RUN_LOCAL_FIXTURES=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 /Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-analysis/bin/python -m pytest /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_figure_artifacts.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_figure_revisions.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_workbench_ui_figures.py --junitxml="$M2_EVIDENCE_DIR/focused.xml" -q
env PYTHONPATH=/Users/vajraang/Documents/ChatGPT/bio-agent-harness DEVBENCH_RUN_LOCAL_FIXTURES=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 /Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-analysis/bin/python -m pytest /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_figure_catalog.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_exploration_state.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_figure_data.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_figure_renderers.py --junitxml="$M2_EVIDENCE_DIR/core-graphs.xml" -q
env PYTHONPATH=/Users/vajraang/Documents/ChatGPT/bio-agent-harness DEVBENCH_RUN_LOCAL_FIXTURES=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 /Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-analysis/bin/python -m pytest /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_figure_results.py --junitxml="$M2_EVIDENCE_DIR/result-graphs.xml" -q
env PYTHONPATH=/Users/vajraang/Documents/ChatGPT/bio-agent-harness DEVBENCH_RUN_LOCAL_FIXTURES=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 /Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-analysis/bin/python -m pytest /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_analysis_workbench.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_analysis_html.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_workbench_ui.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_workbench_ui_agent_asset.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_workbench_ui_planning.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_workbench_ui_recovery.py /Users/vajraang/Documents/ChatGPT/bio-agent-harness/tests/test_worker_cancellation.py --junitxml="$M2_EVIDENCE_DIR/regression.xml" -q
```

Run only the implemented stage's tests at its checkpoint. Expected: focused contract tests pass without provider execution; native fixture tests execute rather than skip; existing UI, approval, cancellation, recovery, export and numerical replay retain their behavior. Capture stdout/stderr and actual exit status through the execution tool. The broad synthetic gate reuses the `regression-2` selection in `reports/rna-agent-readiness/2026-09-24-m1.i_ibb4sy/commands.json`, adding delivered modules and writing to the fresh evidence directory. Never reuse historical output paths or replay provider commands. Unresolved T14 means the broad run is not green even when scoped graph checks pass.

## Alternatives and subsequent phases

**CLI/model-metadata repair first** would unblock a separately authorized live-selector check, but would not deliver the newer figure/workspace requirement and cannot establish scientific quality by itself. Keep it as a small independent operational track.

**Donor-aware inference before graph foundations** would front-load a larger scientific change because of WF-3's unresolved effect-definition contract and the missing WF-4 inferential interface. M2c therefore follows explicit population/unit/result contracts and carries its own numerical gates. This ordering must not be used to declare missing core inferential graphs finished.

**Immediate linked-view framework replacement** adds integration/bundle/runtime risk. Task 7 first compares existing Plotly capabilities with a reusable component using the same small fixture and state contract; retain the simpler supported implementation unless the comparison demonstrates a concrete gap.

Annotation evidence/linked exploration are now M2b, rather than vague future work. Further scientific phases cover protected-biology integration evaluation, extension workflows E1/E2, structured claim/literature enforcement and separately approved live analyst/reviewer evaluation.

Other supplied requirements—data dictionary, versioned project instructions, persistent Python/R exploration, materialized promoted execution, comments anchored to artifact versions, shared-kernel ownership, manuscript export and full claim/source enforcement—must remain explicit ledger items with their own acceptance evidence. M2 adds the relevant unit/state/artifact primitives; it does not implement the entire workspace. Future Python/R interoperability should use typed/versioned artifacts. A resource watchdog is not arbitrary-code security containment.

Preserve matrix-based bulk and scRNA priorities. General snRNA, raw reads, long-read, perturbation/multimodal, spatial/velocity/splicing/cross-species and full e11 require separate contracts and authorization; a renderer does not enable them. Fusion, RNA editing, ASE and clinical/diagnostic use remain unsupported without dedicated validation. Candidate methods in the assessment are not permission to replace tested estimators or change thresholds.

No fresh paid/provider campaign or preflight, cloud allocation, e11 run, data acquisition/upload, training, method/resource-policy relaxation, commit or publication is authorized by this document. Small local offline implementation fixtures remain the intended scope; Colab is the separately authorized future cloud target. CLI/model metadata and wheel packaging remain independent operational/release issues.

## Planning validation

This document is a proposed implementation plan. Validate all 22 catalog rows against the attachment, seven journeys, stage dependencies, existing/new file distinctions and command syntax. Source owners and retained baseline were inspected; no feature implementation, test run, scientific analysis, provider call or browser validation was performed. Each submilestone requires fresh evidence; unavailable families and broader failures remain visible. Documentation coverage is not software completion.
