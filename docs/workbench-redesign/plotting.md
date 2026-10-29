# Scientific figure system

## Data and scientific scope

The current backed scRNA producer exports exact retained observation geometry in
`devbench.figure/1` and `devbench.figure-data/1`. QC is family G02 and saved UMAP
embeddings are G08. Each panel declares axes, transforms, categorical colors or a
continuous range, exclusions, labels and exact `observation_id` membership. The
workbench service verifies the native bundle before serving these files. A hash
identifies retained bytes; it does not establish biological validity.

The new module consumes these existing projections, never expression matrices.
Cells remain descriptive observations, not independent biological replicates.
UMAP proximity does not establish expression similarity, biological identity or
the validity of integration. An apparent improvement in batch mixing does not
establish preserved biology. Missing estimates stay excluded with their recorded
reason. No annotation confidence is invented.

## Plot requirements and priorities

| Plot | Scientific purpose and required data | Stage | Interaction | Renderer / export | Current backend support | Priority |
|---|---|---|---|---|---|---|
| Retained-cell QC scatter | Saved log1p counts, detected genes and mitochondrial percentage | QC evidence | Exact hover, pan, zoom, box/lasso and table | Canvas2D; SVG, PNG, saved CSV, display JSON | G02 exact saved geometry | Implemented |
| UMAP by batch or rerun cluster | Saved UMAP coordinates, grouping and stable observation IDs | Embedding evidence | Panel switch, linked IDs, category hide/recolor, pan/zoom, selection handoff | Canvas2D; SVG, PNG, CSV, JSON | G08 saved condition panels | Implemented |
| Original saved figure and presentation revisions | Verified PNG plus immutable figure metadata | Review | Compare original, retained presentation history | Existing trusted image / evidence archive | Existing service | Retained |
| QC distributions and before/after filtering | Per-cell measurements for both retained and excluded populations; recorded thresholds | QC | Distributions, sample grouping | Future scientific adapter | Current interactive G02 projection is retained cells, not all pre-filter cells | Next adapter; do not infer from G02 |
| PCA and before/after integration comparison | Saved numeric sample/cell PCA, matched identities and integration metrics | Diagnostics | Linked scales and identities | Canvas2D or Plotly after adapter validation | Some numerical pipeline artifacts; no verified current figure adapter exposed here | Deferred |
| Marker dot plot / heatmap / violin | Saved normalized values or validated group-gene aggregates and normalization context | Annotation | Genes, populations, exact aggregates | Future validated heatmap/statistical renderer | Not a G02/G08 artifact | Deferred |
| Composition | Cell/group membership plus sample/donor denominators | Interpretation | Donor-aware denominators, group comparison | Future aggregate renderer | No current figure adapter in this UI | Deferred |
| Volcano, MA and DE table | Validated effect estimates, p/q values, contrast, biological units and model | Differential expression | Linked genes, display labels, explicit cutoffs | Future scientific scatter/table adapter | Backed scRNA UI does not perform DE; separate bounded bulk route exists | Deferred; never label generic scatter as DE |
| Bulk library QC / sample correlation / fit diagnostics | Saved bounded bulk outputs and validated figure contracts | Bulk diagnostics | Exact sample/gene context | Existing bulk/report route, future UI adapter | Separate episode/scientific workflows; not this scRNA renderer | Deferred |
| t-SNE, doublets, expression overlays | Actual coordinates, scores or gene values with method provenance | Exploration | Only after validated source exists | Future adapter | Not supplied by current figure producer | Unavailable |

## Rendering decision

Keep the existing vanilla DOM/CSS frontend and add one small, original Canvas2D
scatter module rather than a React migration or competing chart runtimes. This
works offline inside the strict nonce CSP, uses no CDN or external fonts, and
retains exact local geometry. The research document compares Plotly.js (MIT),
ECharts (Apache-2.0), deck.gl (MIT), D3 (ISC), and React-oriented libraries. Plotly
is a sensible future candidate when additional validated statistical plot classes
justify its asset and integration cost. Canvas is deliberately limited to G02/G08;
unsupported contracts retain the original verified image.

Native controls provide labels, keyboard operations, focus and a paged semantic
table. Canvas geometry alone is not an accessible data representation. The table
is the keyboard equivalent for exact row selection; no full screen-reader or WCAG
qualification is claimed by unit tests.

## Contract and integration

Load `scientific-figure.js` as trusted application source under the existing nonce.
Use the existing authenticated artifact route with `record.plot_data.path`, after
loading the verified figure catalog:

```javascript
const controller = window.ScientificFigure.mount(container, {
  record, payload,
  context: { label: 'Recorded analysis', job_id, parent_root },
  onSelection: ({ observation_ids, figure_id, panel_id, context }) => {
    // Populate an explicit question draft. Do not submit or execute here.
  }
});
```

Destroy the previous controller when the selected job, figure or saved revision
changes. Reject a stale asynchronous payload if selection changed while loading.
Fetch and display failures must leave original-image access available. The frontend
checks the version, family, axis meanings, finite values, category membership,
exact per-panel identity sets, and saved analysis/rendered counts. Backend receipt
verification remains authoritative; this renderer does not replace it.

`onSelection` fires only after the explicit **Ask about selection** action. The
payload contains exact IDs rather than row indices or fabricated group labels.
Filtering and panel changes retain the same selection set; the interface reports
both selected and currently visible counts. No selection automatically starts a
method, edits input data, changes a scientific threshold or bypasses approval.

## Visual customization and exports

Point radius, opacity, category color/visibility, pan/zoom and panel choice are
local display settings. The saved axis meanings and continuous range remain
unchanged. Reset restores the saved full-range view and original category colors.
The continuous display palette is a sequential blue interpolation over the exact
saved range; hover preserves the original numeric value.

Exploration exports are clearly separate from backend-retained presentation
revisions. SVG and 2× PNG capture the current viewport; their caption states
viewport and analysis counts. SVG embeds display configuration in metadata.
CSV includes the complete saved panel including excluded rows and their reasons;
formula-like scalar fields are neutralized for spreadsheet opening. JSON contains
exact selected IDs, the figure/context reference, viewport and display settings.
The existing evidence archive and versioned presentation workflow remain the
reproducibility/export authority.

## Performance model and limits

No display sampling is used. Up to 200,000 population identities and 400,000 total
panel rows are accepted. Larger contracts show the original image and an explicit
interactive budget message. Existing backend payload and resource limits still
apply; frontend limits do not widen them. Only one panel/canvas is active, there
are no WebGL contexts, device pixel ratio is capped at two, rendering is batched
by color, hover uses a screen-space grid, and drawing is scheduled through one
animation frame. The observation table creates at most 40 rows per page.

Observed local Node 24.19.0 timings on 28 October 2026 (one deterministic process
run; not a statistically repeated benchmark):

| Synthetic cells | Serialized payload | Contract preparation | Exact polygon selection | Selected / expected |
|---|---:|---:|---:|---:|
| 50,000 | 7,012,763 bytes | 16.38 ms | 3.71 ms | 25,000 / 25,000 |
| 100,000 | 14,057,263 bytes | 31.93 ms | 3.70 ms | 50,000 / 50,000 |

Pure logic benchmarks use deterministic synthetic observations and validate exact
selection cardinality at 50,000 and 100,000 cells. They measure contract checking
and polygon selection, not scientific performance or browser frame rate. Browser
rendering must be measured separately at the actual target viewport using
`controller.getPerformance()` after a render; it returns `last_render_ms`,
`viewport_points`, `population_n` and `display_sampling`. The prior static PNG
view has no equivalent exact-identity selection operation, so a fabricated
before/after speedup is inappropriate. Use the same retained fixture for initial
image-load versus interactive-render measurements and report workload, browser,
viewport and hardware with the observed timings.

`scripts/benchmark_workbench_figures.py --serve` generates a separate synthetic
benchmark page on loopback port 8879. It serves deterministic 50,000- and
100,000-point payloads plus PNGs of the exact same geometry, and loads the actual
application renderer. Explicit controls measure payload fetch/parse, static PNG
decode, interactive mount/first draw, redraw, exact-ID polygon selection and CSV
serialization. The page can save observed browser metrics as
`reports/workbench-redesign/2026-10-28/performance/browser-observations.json`.
PNG and interactive timings describe different capabilities and do not imply a
speedup. The synthetic page is separate from retained scientific results.

## Validation

`tests/test_scientific_figure_redesign.py` runs the actual JavaScript module in
Node to verify exact IDs, reordered panel joins, excluded observations, invalid
coordinates/categories/counts, box/polygon membership, escaped SVG text,
spreadsheet-safe CSV and an unsampled 50,000-observation selection. Browser tests
must additionally cover pan/zoom gestures, table keyboard selection, category
filtering, theme redraw, exports, fullscreen support and selected-ID draft
handoff. Synthetic checks establish software behavior only.

Fresh module checks: `node --check` succeeded and the dedicated pytest module
passed 16 tests. Integration verification uses the actual existing backend
exporter for QC, uncorrected UMAP and Harmony UMAP, preserving literal identifiers
`001`, `NA` and `cell3`, reordered panels and missing QC color values. A reviewed
CSV regression verifies that negative numeric coordinates remain numeric while
formula-like string identifiers are neutralized for spreadsheet opening.
