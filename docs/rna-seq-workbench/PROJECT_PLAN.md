# RNA-seq Workbench: Codex-Assisted Analysis Platform

**Project plan and requirements specification**

| Field | Value |
|---|---|
| Document | PROJECT_PLAN.md |
| Version | 1.0 (draft for review) |
| Date | 2026-10-26 |
| Status | Proposed. No component in this document has been implemented. |
| Implementing agent | Codex, working in this repository under `AGENTS.md` |
| Supersedes | `reports/workbench-ui-plan/2026-10-26/PLAN.md` (retained as history) |

---

## 1. Executive summary

The RNA-seq Workbench is a local-first application that lets researchers who have Codex access carry out RNA-seq analyses with an AI agent doing most of the operational work, while the researcher keeps control of scientific decisions. Codex inspects data, proposes analysis plans, runs approved methods in pinned containers, and prepares results. The workbench presents those results in interactive, linked visualizations and records how every result was produced.

The project has three deliverables:

1. **A Codex integration layer**: a Model Context Protocol (MCP) server and a set of RNA-seq analysis playbooks, so that Codex can operate the workbench through well-defined, permission-checked tools.
2. **An interactive analysis interface**: workflow-oriented views (Data, Design, QC, Model, Results, Figures, Report) built on a shared visualization layer with linked selection, live thresholding, gene and sample sets, and exportable views.
3. **An evidence layer** (largely existing): versioned studies, datasets, analyses, runs and reviews; pinned execution; pre-registered comparison criteria; reproducible reports.

Commercial notebook platforms, notably LatchBio's Latch Plots, were reviewed as reference systems (Appendix B). This project does not reproduce them. It differs in being agent-operated through Codex, running locally on the user's machine, and treating every governed result as auditable evidence.

## 2. Problem statement

RNA-seq analysis requires many decisions about data, design and method, each easy to get wrong silently: treating repeated measures as independent replicates, fitting a confounded design, or reporting a threshold-selected subset as a full result. Researchers with Codex access can delegate code-writing to an agent. They currently lack:

- a structured environment where the agent's actions are bounded, reviewable and reproducible;
- visual tools for checking the agent's work quickly, at the level of samples, genes and contrasts;
- a clear separation between quick exploration and results that can be reported.

The current workbench provides strong record-keeping and approval, but it has no data viewing, no interactive plots and no results explorer. It is organized around records rather than the analysis workflow.

## 3. Objectives and success measures

| ID | Objective | Success measure |
|---|---|---|
| O1 | Codex can run a complete bulk RNA-seq analysis through the workbench | From a registered count matrix and sample sheet, Codex produces an approved plan, executed run, results and report using only workbench tools; no manual file editing |
| O2 | Researchers can verify an analysis visually in minutes | Every required QC, diagnostic and results plot (Section 9) is available and linked; a reviewer can trace any highlighted gene from volcano plot to table to per-sample counts in three interactions or fewer |
| O3 | Exploration never contaminates evidence | 100% of exploratory outputs carry a non-evidence label; automated tests confirm no exploratory artifact can be attached to a governed result |
| O4 | Results are reproducible | A governed run can be re-executed from its record alone and produces byte-identical native outputs on the same platform |
| O5 | Scientific safeguards are enforced, not advisory | Design-rank, pairing and replicate checks block execution when violated; thresholds applied in the interface never change stored statistics |
| O6 | Single-cell analyses use the same platform | Single-cell QC, embedding, marker and pseudobulk views operate on the existing scRNA route (milestone M6) |

## 4. Scope

**In scope**
- Bulk RNA-seq from processed counts: data registration, design validation, QC, differential expression, diagnostics, results exploration, figures, reports.
- Reproduction of published processed-data analyses, starting with Love et al. 2015 (airway).
- Single-cell RNA-seq from H5AD: QC, embedding, clustering review, markers, pseudobulk differential expression (later milestone).
- Codex integration via MCP and analysis playbooks.
- An exploration workspace, separated from governed evidence.

**Out of scope**
- Read alignment and quantification from FASTQ/BAM (may be added later as containerized workflows).
- Cloud hosting, multi-tenant accounts, billing.
- Clinical use. The platform is for research use; clinical validation is not claimed.
- Reimplementing statistical methods in the browser.

## 5. Users

| Persona | Needs | Primary surfaces |
|---|---|---|
| Bench scientist with Codex access | Ask questions in plain language; check that results make sense; get figures for a paper | Codex chat, Results and Figures views, Report |
| Computational biologist | Control methods and parameters; inspect diagnostics; extend playbooks | Design, Model, Diagnostics views; plan editor; playbooks |
| Reviewer or principal investigator | Confirm what was done, by whom, with which data; approve or reject | Review queue, provenance panel, Report |

## 6. Product principles

1. **Agent-operated, human-decided.** Codex performs operations; the researcher approves scientific choices (design, method, thresholds, exclusions). Approval points are explicit and recorded.
2. **Two lanes.** The *Explore* lane is fast and ungoverned and is labelled as such. The *Governed* lane produces evidence through plan, review, run and assessment. Exploration may be promoted into a draft plan; it is never promoted into evidence.
3. **Visualize from data.** Every plot is rendered from a declared data contract with provenance. Static images are produced for publication, not used as the primary viewing format.
4. **Interaction never changes statistics.** Sliders, selections and filters reclassify or highlight stored results. They never recompute test statistics. Any view whose settings differ from the frozen plan shows that difference visibly.
5. **Local-first and offline-capable.** All computation and data stay on the user's machine. Front-end libraries are bundled; there are no runtime CDN calls.
6. **Workflow-first navigation.** Screens follow the analysis (Data, Design, QC, Model, Results, Figures, Report). Records and provenance are available in a side panel on every screen.

## 7. System architecture

```
 ┌──────────────┐      MCP (stdio / loopback)      ┌──────────────────────────────┐
 │    Codex     │ ───────────────────────────────▶ │  Workbench MCP server         │
 │ (CLI / IDE / │ ◀─────────────────────────────── │  (tool surface, permissions)  │
 │    app)      │                                   └──────────────┬───────────────┘
 └──────────────┘                                                  │ in-process API
                                                                   ▼
 ┌──────────────┐   HTTPS loopback + session token  ┌──────────────────────────────┐
 │  Browser UI  │ ◀───────────────────────────────▶ │  Workbench service            │
 │  (views,     │                                    │  - Scientific object store    │
 │  viz layer)  │                                    │  - Plan / review / authority  │
 └──────────────┘                                    │  - Query + plot-data service  │
                                                     │  - Explore runtime            │
                                                     └──────────────┬───────────────┘
                                                                    │ pinned OCI execution
                                                                    ▼
                                                     ┌──────────────────────────────┐
                                                     │  Method containers            │
                                                     │  (R/DESeq2, Python, scRNA)    │
                                                     │  network off, read-only input │
                                                     └──────────────────────────────┘
```

| Component | Responsibility | Status |
|---|---|---|
| Scientific object store | Studies, datasets, analyses, plans, runs, attempts, artifacts, reviews | Exists |
| Authority and review | Approval, signed one-attempt execution, operation ledger | Exists |
| Local OCI executor | Pinned image, enclosure checks, deadlines, cleanup | Exists |
| Workbench MCP server | Tool surface for Codex (Section 8) | New (extend `devharness_server`) |
| Analysis playbooks | Stage-by-stage instructions and self-checks for Codex | New |
| Query and plot-data service | Paginated tables, gene queries, plot contracts, image rendering | New |
| Visualization layer | Shared interactive components, linked selection, view state | New |
| Explore runtime | Ungoverned computation on registered data | New |
| Report builder | Versioned report from governed outputs | Partly exists (retained-output HTML index) |

## 8. Codex integration

### 8.1 Interaction model

Codex runs in the user's own environment (CLI, IDE or desktop app) under the user's Codex account. It connects to the Workbench MCP server configured for the local project. The researcher converses with Codex; Codex acts through workbench tools; the browser shows the resulting state live.

Two session modes:
- **Guided**: Codex stops for approval at every analysis stage and presents alternatives with expected runtime, memory and trade-offs.
- **Autonomous within plan**: after the researcher approves a plan, Codex executes its steps without further prompts, stopping only on failed checks, missing information or anything outside the approved plan.

### 8.2 MCP tool surface

Tools are grouped by permission level. Every call is recorded in the operation ledger with inputs, outputs and caller identity.

| Group | Tool | Purpose | Permission |
|---|---|---|---|
| Discover | `list_studies`, `get_study` | Browse registered studies | Read |
| | `inspect_dataset` | Shapes, sample sheet, factor levels, count integrity, library-size summary | Read |
| | `validate_design` | Model matrix rank, residual df, pairing, confounding, replicate structure | Read |
| | `get_gene`, `query_results` | Bounded result and per-gene queries | Read |
| Visualize | `get_plot` | Plot contract and summarized data | Read |
| | `render_plot_image` | PNG of a plot in a given view state, so Codex can visually check it | Read |
| | `save_view` | Save a plot view (filters, selection, encodings) to the study | Write (metadata) |
| Plan | `propose_plan` | Create a draft plan with stages, methods, parameters and rationale | Write (metadata) |
| | `update_plan_step` | Report step status and self-check results | Write (metadata) |
| | `ask_user` | Raise a question that appears in the review queue | Write (metadata) |
| | `request_review` | Submit a plan or result for human decision | Write (metadata) |
| Explore | `explore_compute` | Run a whitelisted exploratory computation (e.g. PCA on a sample subset) | Write (explore lane only) |
| Execute | `run_approved_step` | Execute one step of an approved plan | Execution: requires approved plan, signed grant, matching subject |
| | `get_job` | Status, logs, resource use | Read |
| Report | `build_report` | Assemble report from governed outputs | Write (metadata) |

Execution tools reuse the existing authority mechanism. Codex can never approve its own plan, sign a grant or change a frozen plan.

### 8.3 Analysis playbooks

Playbooks are versioned instruction packages shipped with the workbench and loaded by Codex for the relevant stage. Each contains:

- **Goal and method**: what the stage does and the default procedure.
- **Pre-checks**: conditions that must hold before the stage runs (e.g. integer counts for count models).
- **Self-checks**: numeric sanity bounds evaluated after the stage. A step is marked complete only when they pass.
- **Required views**: which plots Codex must render and inspect before reporting the stage complete.
- **Escalation rules**: when Codex must ask the researcher instead of deciding.

Initial playbooks: bulk data intake, bulk design validation, bulk QC, bulk differential expression (DESeq2-style negative binomial), results interpretation, published-analysis reproduction, single-cell QC, single-cell clustering and annotation review, pseudobulk differential expression.

### 8.4 Visibility of agent activity in the interface

- **Plan panel**: the current plan as a checklist with status, owner (Codex or researcher), self-check results and review state.
- **Activity timeline**: every tool call Codex made, with arguments and results, filterable by stage.
- **"Viewed by Codex" markers**: plots Codex rendered for its own checks are flagged, so the researcher can see what the agent looked at.
- **Review queue**: questions and approval requests from Codex, each bound to the exact subject being approved.

## 9. Interactive visualization specification

### 9.1 Shared interaction model (applies to every plot)

| ID | Capability | Behavior |
|---|---|---|
| V-1 | Hover details | Tooltip with identifiers and all values relevant to the mark (e.g. gene symbol, ID, baseMean, log2FC ± SE, p, padj, NA reason) |
| V-2 | Zoom, pan, reset | Mouse/trackpad zoom and pan; double-click resets; axis ranges retained in view state |
| V-3 | Selection | Click to select one mark; shift-click to add; box and lasso select for groups |
| V-4 | Linked selection | A shared selection bus: selecting genes or samples in any view highlights them in all open views and filters linked tables |
| V-5 | Gene sets and sample sets | Any selection can be saved as a named, versioned set; sets can be imported (paste list or file) and applied as highlights or filters |
| V-6 | Search | Gene search by symbol or ID with autocomplete; the result is highlighted and centered in every view |
| V-7 | Encodings | Color, shape and facet by any sample-metadata column (sample plots) or result attribute (gene plots); color-blind-safe palettes |
| V-8 | Labels | Pin labels to selected marks; automatic non-overlapping placement; labels persist in saved views |
| V-9 | Legend control | Click legend items to hide or isolate groups |
| V-10 | View state | Every view's settings (filters, encodings, zoom, selections, labels) serialize to a view spec that can be saved, shared by link and reopened exactly |
| V-11 | Export | PNG and SVG at publication resolution; underlying data as TSV; the export embeds a provenance footer (run, artifact hash, view spec hash) |
| V-12 | Add to report | Snapshot the current view into the report, with its view spec and provenance |
| V-13 | Ask Codex | Send the current view and selection to Codex with a question (e.g. "what do these genes have in common?"); the answer is attached to the view as a note |
| V-14 | Provenance footer | Source run, method, data artifact, sample count and filters, always visible |
| V-15 | Plan-difference banner | When interactive settings differ from the frozen plan (e.g. a different padj threshold), a banner states the difference and that stored statistics are unchanged |

### 9.2 Bulk RNA-seq views

**Data view**

| Component | Interactive functionality |
|---|---|
| Sample sheet | Sortable, filterable table; design columns marked; factor levels and reference level shown; edit proposals go to review, not direct edits |
| Count matrix preview | Paginated, server-side search by gene; never loads the full matrix into the browser |
| Library composition | Per-sample bars of total counts and detected genes; sort; color by any metadata column; click a sample to open its sample panel |

**Design view**

| Component | Interactive functionality |
|---|---|
| Design formula and model matrix | Shows the formula, model matrix, rank and residual degrees of freedom; toggling a term previews the effect on rank and df before any plan change |
| Pairing grid | Factor-by-factor grid (e.g. 4 cell lines × 2 treatments) with counts per cell; empty or unbalanced cells highlighted |
| Biological-unit panel | States the replicate unit (e.g. "4 paired cell lines, not 8 independent donors") and flags repeated measures |
| Confounding check | Cross-tabulation of covariates; fully confounded pairs block execution with an explanation |

**QC view**

| Plot | Interactive functionality |
|---|---|
| Library size | Bars per sample; color by group; adjustable reference line for a minimum-depth guideline (advisory, not a filter) |
| Count distributions | Per-sample box or violin of log counts from precomputed summaries; switch between raw and normalized scale |
| PCA | Choose any two of the first ten components; scree inset; color, shape and facet by metadata; toggle lines connecting paired samples; hover shows sample metadata; click opens the sample panel; lasso defines a sample set for the Explore lane |
| Sample distance heatmap | Clustered with dendrograms and metadata annotation tracks; hover shows the pair and distance; click a cell to open a sample-versus-sample scatter of log counts |
| Size factors | Per-sample values with library size overlay; hover details |
| Outlier flags | Samples flagged by QC checks are marked consistently across all QC plots |

**Model diagnostics view**

| Plot | Interactive functionality |
|---|---|
| Dispersion | Gene-wise, fitted and final dispersions against mean; hover gene; search highlights a gene; outlier dispersions highlighted |
| p-value histogram | Per contrast; uniform expectation overlay; hover bin counts; shows when the distribution indicates a problem (e.g. conservative or anti-conservative shape) as an advisory note |
| Independent filtering | Rejections versus filter quantile, with the chosen threshold marked |
| Cook's distance | Per sample box plots; genes exceeding the cutoff listed and selectable |

**Results view**

| Component | Interactive functionality |
|---|---|
| Contrast selector | Lists every hypothesis family in the run (e.g. treatment default, treatment alpha 0.05, treatment |log2FC| > 1, cell-line comparison); switching updates all linked views |
| Volcano plot | WebGL scatter for 60,000+ genes; live padj and |log2FC| sliders reclassify points and update the significant-gene count (statistics unchanged, V-15 banner shown when different from plan); NA genes shown in a separate strip with the reason; lasso to gene set; click pins a label; optional shrunken versus unshrunken log2FC toggle when both exist |
| MA plot | Same interactions as the volcano plot; mean-expression axis; linked to the same selection |
| Results table | Server-side paging, sorting and filtering on every column; columns for symbol, ID, baseMean, log2FC, lfcSE, stat, p, padj, NA reason; selecting rows highlights genes in all plots; export filtered table |
| Gene panel | For a selected gene: normalized counts per sample grouped by condition, with paired samples connected; raw, normalized and log scale toggle; the gene's result across all contrasts; external annotation links |
| Top-genes heatmap | Choose N and ranking statistic; row z-scores; cluster rows and columns on or off; annotation tracks; brush rows to create a gene set; click a row to open the gene panel |
| Contrast comparison | log2FC-versus-log2FC scatter between two contrasts; UpSet plot of significant-gene intersections; click an intersection to select its genes |

**Reproduction view (published-analysis reproduction)**

| Component | Interactive functionality |
|---|---|
| Side-by-side figure | Authored figure (PNG/PDF) next to the interactive re-rendering from the same plot-data table; synchronized zoom where applicable |
| Endpoint status | Per comparison endpoint: pass, fail or not assessable, with the reason and the frozen criterion |
| Chunk receipts | Executed, disabled and excluded chunks, linked to the figures and tables they produced |

### 9.3 Single-cell views (milestone M6)

| Plot | Interactive functionality |
|---|---|
| Embedding viewer | WebGL rendering with level-of-detail for up to 1,000,000 cells; color by metadata or gene expression; filter by any metadata; lasso to create a cell set; select embedding (UMAP, t-SNE, PCA) |
| QC distributions | Precomputed violins of genes, UMIs and mitochondrial fraction per sample; draggable threshold lines that create a *proposed* parameter change for review, with the number of cells that would be removed |
| Cluster composition | Stacked bars by sample or condition; click a segment to select those cells |
| Marker dot plot and heatmap | Choose clusters and genes; click a gene to color the embedding by it |
| Pseudobulk results | Uses the bulk Results view; warns when donor or sample structure is not used |

### 9.4 Performance requirements for visualization

| Requirement | Target |
|---|---|
| Volcano/MA initial render, 65,000 genes | under 1 second on a current laptop |
| Slider reclassification | under 100 ms |
| Linked-selection propagation across 5 open views | under 200 ms |
| Table page fetch (100 rows, sorted, filtered) | under 300 ms server-side |
| Embedding viewer, 1,000,000 cells | interactive pan and zoom at 30 fps or better via level-of-detail |
| Payload to browser per plot | bounded; large plots use server-side aggregation |

## 10. Functional requirements by module

| ID | Requirement |
|---|---|
| FR-D1 | Register datasets from count matrices (TSV, CSV), sample sheets, RData/RDS (via containerized reader) and H5AD, with hashes and declared provenance |
| FR-D2 | Validate count integrity (integer, non-negative, no missing values) and identifier uniqueness at registration |
| FR-D3 | Present data previews through bounded, paginated queries only |
| FR-S1 | Compute design rank, residual df, pairing and confounding for any proposed formula |
| FR-S2 | Block execution of designs that are not identifiable, with an explanation |
| FR-S3 | Record the biological replicate unit as a required plan field |
| FR-P1 | Plans list stages, methods, parameters, parameter origin (author, user, agent default) and rationale |
| FR-P2 | Frozen plans are immutable; any change creates a new version requiring review |
| FR-P3 | Comparison and acceptance criteria are frozen before results are produced |
| FR-E1 | Execute governed steps only through the pinned OCI executor with network disabled and read-only inputs |
| FR-E2 | One governed attempt per approved subject; failures are retained, not retried automatically |
| FR-E3 | Record resource use (wall time, peak memory, output size) per attempt |
| FR-R1 | Store complete result families, including standard errors and NA masks; never only significant subsets |
| FR-R2 | Enforce q ≥ p and value ranges on import |
| FR-X1 | Explore-lane outputs are stored separately and labelled non-evidence |
| FR-X2 | Promote an exploration to a draft plan, carrying its settings but none of its outputs |
| FR-V1 | All visualization requirements in Section 9 |
| FR-RP1 | Build reports from governed outputs and saved views, with provenance for each element; reports are versioned |
| FR-C1 | All Codex actions go through the MCP tool surface and are recorded |
| FR-C2 | Codex cannot approve, sign, or modify frozen plans |

## 11. Non-functional requirements

| Area | Requirement |
|---|---|
| Security | Loopback-only server; session token; Host/Origin checks; route allowlists; no credentials in logs; MCP server bound to the local user |
| Privacy | Data never leaves the machine through the workbench; no data sent to Codex beyond bounded summaries returned by tools, and never raw matrices |
| Reproducibility | Pinned images, locked environments, hashed inputs and outputs, retained run records |
| Offline operation | All assets bundled; no runtime network calls except those the user explicitly configures |
| Accessibility | Keyboard navigation for all controls; WCAG AA contrast; color-blind-safe palettes; text alternatives for plots (summary statistics) |
| Maintainability | Front end split into modules per view; visualization components shared; API versioned |
| Testability | Synthetic fixtures for all tests; no real expression matrices in the test suite |

## 12. Data and API additions

**Plot contract** (stored with each governed or exploratory plot):
`{plot_id, kind, lane (governed|explore), data_ref (artifact id + sha256), encodings, filters, aggregation, provenance (run, attempt, method version, plan version), created_by (codex|user|method)}`

**View spec**: `{plot_id, encodings, filters, selections, labels, axis_ranges, thresholds, created_at, author}`, hashed and versioned.

**Sets**: `{set_id, kind (gene|sample|cell), members, source (selection|import|codex), created_by, version}`.

**REST endpoints** (in addition to existing routes):

| Endpoint | Purpose |
|---|---|
| `GET /api/data/{artifact}/preview` | Paginated table slice, bounded |
| `GET /api/design/{dataset_version}?formula=` | Rank, df, pairing, confounding |
| `GET /api/results/{run}/{family}` | Paged, sorted, filtered results |
| `GET /api/genes/{run}/{gene}` | Per-gene counts and results across contrasts |
| `GET /api/plots/{plot_id}` | Contract plus aggregated data |
| `POST /api/plots/{plot_id}/render` | Server-side PNG for a view spec (used by `render_plot_image`) |
| `POST /api/views`, `GET /api/views/{id}` | Save and load view specs |
| `POST /api/sets`, `GET /api/sets/{id}` | Gene, sample and cell sets |
| `POST /api/explore/{operation}` | Whitelisted exploratory computations |
| `POST /api/reports`, `GET /api/reports/{id}` | Versioned reports |

## 13. Delivery plan

| Milestone | Scope | Acceptance criteria |
|---|---|---|
| **M0: Foundations** | Inventory of current views and APIs; plot contract, view spec and set schemas; bundled charting library (Plotly.js or equivalent, chosen in M0); front-end module structure | Schemas validated by tests; charting library loads with network disabled; inventory document reviewed |
| **M1: Data and Design** | Data view, Design view, FR-D1 to FR-D3, FR-S1 to FR-S3 | On the registered airway dataset: 64,102 × 8 preview pages correctly; pairing grid shows 4 × 2; rank 5 and residual df 3 displayed; confounded test fixture is blocked |
| **M2: Results explorer** | Results view with volcano, MA, table, gene panel, top-genes heatmap, contrast comparison; shared interaction model V-1 to V-11 | On a synthetic results fixture: linked selection works across all views; slider counts match server-side counts exactly; NA genes shown with reasons; performance targets in 9.4 met |
| **M3: QC and diagnostics** | QC and Model diagnostics views; bulk route emits plot contracts for each | Each plot renders from a governed run's contract; provenance footer present; outlier flags consistent across plots |
| **M4: Codex integration** | Workbench MCP server (Section 8.2), playbooks for bulk intake, design, QC and DE, plan panel, activity timeline, review queue, `render_plot_image` | In a recorded session on a synthetic dataset, Codex completes intake to report using only MCP tools; every call appears in the ledger; Codex is refused when attempting approval or signing |
| **M5: Reports and reproduction** | Report builder, Add to report, Reproduction view | Report regenerates identically from its record; airway reproduction view populated once a governed airway run exists |
| **M6: Single-cell** | Section 9.3 on the existing scRNA route; pseudobulk via the Results view | Embedding viewer meets the 1,000,000-cell target on a synthetic fixture; QC threshold proposals create reviewable plan changes |
| **M7: Explore lane** | Explore runtime, `explore_compute`, promote-to-plan | Exploratory outputs cannot be attached to governed results (negative tests); promote creates a draft plan only |

Milestones M1 to M3 can proceed in parallel after M0. M4 depends on M1 and M2. M7 can start after M2.

## 14. Quality assurance

- **Test-first development** for every requirement: failing test, implementation, passing test; failed attempts retained.
- **Unit and API tests** with synthetic fixtures, including bounds, pagination limits, permission refusals and malformed input.
- **Statistical integrity tests**: interactive thresholds never alter stored values; q ≥ p enforced; NA masks preserved through every query.
- **Browser tests** (Playwright) for each view: rendering, linked selection, export, keyboard navigation.
- **Performance tests** against Section 9.4 targets with generated datasets of the stated sizes.
- **Agent tests**: scripted Codex sessions against the MCP server on synthetic data, verifying tool sequencing, refusals and ledger records.
- **Independent review** of each milestone's source before release.

## 15. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Interactive thresholds mistaken for the analysis result | Misreported findings | Plan-difference banner (V-15); exports carry view spec and plan version |
| Codex over-reaches (approves, changes frozen plans) | Loss of control | Permission tiers enforced server-side; refusal tests in M4 |
| Large datasets overwhelm the browser | Unusable views | Server-side aggregation, WebGL, level-of-detail, payload limits |
| Front-end complexity grows unmanageably | Slow delivery | Module structure in M0; shared visualization components; view specs as the single state model |
| Old and new servers write to the same store | Record corruption or rejected records | Run the new service from the new installation only; no concurrent writers |
| Emulated execution (x86 image on ARM) is slow | Timeouts | Measure in a no-data preflight; size deadlines from measurements; offer native x86 execution later |
| Scope expands toward a general notebook product | Delay | Non-goals in Section 4; Explore lane limited to whitelisted operations in this plan |

## 16. Decisions required before M0

1. Charting library: Plotly.js (broad chart coverage, WebGL scatter) or an alternative (e.g. Vega-Lite plus a WebGL scatter component).
2. Explore runtime: server-side whitelisted operations only, or an R/Jupyter session inside the pinned image.
3. Deployment during transition: new service on a separate port alongside the existing server, or replacement once the airway run completes.
4. Gene annotation source for symbols and links: a bundled offline table or the annotation package inside the pinned image.
5. Milestone order: confirm bulk-first (M1 to M5) before single-cell (M6).

---

## Appendix A. Implementation context for Codex

- Read `AGENTS.md` before starting. Work only inside the repository.
- Base all source changes on `reports/rna-airway-start/2026-10-26-v1/local-docker-v1/source`. The top-level `devbench/` tree is older and must not be used as the base.
- Reuse existing components: the scientific object store (`scientific_objects.py`), method service and executor (`methods.py`, `method_executor.py`), workbench server (`workbench_ui.py`, `assets/workbench-ui.html`), the existing MCP server (`devharness_server/server.py`), the existing scRNA page (`reports/workbench-ui/2026-09-22`) and the bulk route (`reports/rna-workbench-milestone/2026-09-22`).
- The airway R wrapper already emits per-figure plot-data tables, state files, PNG and PDF, and a figure manifest. Use these as the first plot contracts.
- Do not modify the governed execution path, authority, signing, comparison policy or the in-progress airway reproduction records.
- Do not stop or alter running workbench processes. Develop against a separate port and the new installation.
- Bundle all front-end dependencies. Tests use synthetic fixtures only.
- Report per milestone: changes, tests run with results, screenshots of each new view, and open issues.

## Appendix B. Reference systems reviewed

LatchBio's open-source repositories were reviewed at fixed commits: `latch-plots-faas@2d9eb09`, `latch-skills@a1c4c6e`, `scbench@0bc3403`, `latch-eval-tools@0d79831`, `latch@e068e6b`.

| Reference capability | Treatment in this project |
|---|---|
| Reactive notebook with input widgets | Not adopted as the primary interface. Interactivity is provided by purpose-built analysis views with linked selection; a notebook-style Explore lane may follow later |
| Plotly/Matplotlib output widgets | Comparable rendering; added linked selection, saved view specs and provenance footers |
| AnnData viewer with color-by, filter and lasso | Comparable capability in the single-cell embedding viewer (M6), with lasso results flowing into reviewable sets rather than direct data edits |
| Agent tools to create/run cells, maintain a plan and view rendered plots | Adapted as an MCP tool surface for Codex with permission tiers, a plan panel and `render_plot_image` |
| Technology skills with numeric self-evaluation criteria | Adapted as analysis playbooks with pre-checks, self-checks, required views and escalation rules |
| Containerized workflow launch from the notebook | Replaced by governed execution of approved plan steps in pinned local containers |
| Server-side subsampling and precomputed summaries | Adopted as a general rule for all large plots |

Capabilities specific to this project and absent from the reference: versioned scientific records, pre-registered comparison criteria, independent review gates, signed one-attempt execution, separation of exploratory and governed results, and local-only operation. Bulk differential-expression views are specific to this project; the reference code reviewed is oriented to single-cell and spatial data.
