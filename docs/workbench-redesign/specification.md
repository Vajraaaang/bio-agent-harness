# Workbench design and implementation specification

## Product scope and data

This workbench connects a scientist's question to an existing absolute-path H5AD, bounded proposal selection or operator-authored plan, review, controlled execution and verified artifacts. Local input registration is supported; file upload and arbitrary conversational scientific execution are not. The current registered route supports scRNA UMI-count QC, integration, clustering and available annotation. The separate scientific workspace retains Study/DatasetVersion/AnalysisVersion and guarded method workflows. Bulk contrasts remain available through their existing CLI.

The brief requests a substantial integrated redesign. The user's later instruction selects **only grey, black and white for the workbench UI**, superseding the initial green accent exploration. Scientific figure colors remain bound to saved categories/continuous scales; monochrome chrome never changes a saved result or its legend.

## Visual directions and selection

1. Scientific precision: compact native controls, a clear reading order, quiet separators and a conversation beside the figure. Chosen, with the user's monochrome palette.
2. Notebook studio: document-like central notebook and collapsible cells. Better for editable kernels, which this service does not expose.
3. Atlas explorer: full-window embeddings and dense metadata controls. Useful as a figure fullscreen state, but would subordinate the question/review interaction as the default.

## Application shell

Desktop uses compact project navigation, a primary research conversation, and an adjacent artifact/context canvas. The conversation is visually second, preceding the canvas; existing DOM identifiers and guarded forms remain stable for compatibility. At <=860 CSS pixels, the conversation precedes the canvas in a stacked second column. At <=540 pixels, navigation becomes a horizontal row, then conversation, then canvas. Scientific panels retain bounded scrolling and readable controls.

The question remains visible while reviewing data, plan, execution and results. Navigation provides Project, Datasets, Investigations, Runs, Results and Files & exports. A retained run opens its actual question/status. Navigation/search/drafts use the existing expected-revision workspace persistence. Appearance is a view-local light/dark/system choice; it is not stored as a scientific setting.

## Tokens and components

The native stylesheet defines semantic surfaces, ink, muted text, lines, action, warning/error and plot-axis tokens. Light uses #f5f5f5/#ffffff surfaces and #171717 ink; dark uses #151515/#1f1f1f surfaces and #f4f4f4 ink. All UI tokens and literals are grayscale, including native checkbox/range accents. Status meaning also appears in text. Figure colors alone follow scientific records.

Typography uses locally available Avenir Next with Segoe UI/sans-serif fallbacks; there are no external font requests. Body text is 13px with a 1.6 line-height; primary headings 20–28px; control labels 10–12px. A compact 8px spacing base, 6–8px radii, single-pixel boundaries and restrained control transitions replace inconsistent legacy presentation. Existing form, details, button, select, table, status and alert semantics remain native.

## Question and execution experience

The conversation module renders the exact original question, retained proposal source, planner outcome where present, observed input preview, recorded operator review, exact state transitions and preserved failures. It distinguishes operator plans, offline recorded fixture selectors and actual Codex plans. No synthetic reasoning stream, future execution steps, progress percentages or biological findings are generated.

Suggested initial questions fill the draft only. Follow-ups carry an explicit source run and hydrate its retained input path, SHA-256 and declarations; unresolved source context clears stale fields and requires review. Selected-cell handoffs include exact saved IDs and panel/figure/run context, with a bounded draft length. These are new plan drafts, not an unsupported multi-turn or selected-population inference API. They never approve or execute an analysis.

The next action opens an existing review/execution/figure surface. The operator approval form, explicit confirmations, separate run action, startup permissions, frozen resource profile, receipt verification and recovery/cancellation boundaries remain authoritative.

## Artifact and scientific figure experience

The original verified image and immutable presentation-revision controls remain available. The new viewer lazily fetches only a figure's verified `plot_data.path` through the existing authenticated artifact route. It mounts one active Canvas2D renderer and destroys it on navigation, preserving an original-image fallback and remounting when results reopen. Request generations reject stale responses.

QC and retained UMAP use the saved data contract and exact identities. Controls provide pan, zoom, box/lasso selection, category filtering/recoloring, point radius/opacity, reset/fullscreen, a search/paged exact-observation table, and SVG/2×PNG/CSV/selected-ID exports. Controls are explicitly display-only. View counts distinguish viewport, visible, plotted and total analysis populations; no display sampling occurs. Unsupported artifacts fall back to their recorded image and state the limitation.

Exploration exports remain separate from the existing supervised retained presentation versions. No visual cutoff, smoother or embedding transformation silently changes results. The plot requirements/support matrix, data validity rules, workload limits and benchmark protocol are in plotting.md.

## State and interaction coverage

| State | Experience |
| --- | --- |
| No input / first use | Honest supported scope, draft suggestions, dataset-context action |
| Uninspected dataset | Required provenance/assay/count/capture declarations and frozen profile |
| Preparing / running | Actual latest state plus expandable retained transitions; explicit cancel/recovery eligibility |
| Clarification / refusal / unsupported request | Recorded reason and plan inspection; no substitute analysis |
| Approval required | Full retained proposal, operator identity label/rationale and explicit confirmations |
| Completed / review findings | Exact output checks separated from independent scientific review |
| Failed / partial / interrupted | Preserved reason, evidence and guarded eligible actions |
| Missing/empty/unsupported figure | Explicit message and original-image/download fallback |
| Historical output | Source run/receipt identity; no implication of canonical or independent validation |

Keyboard support includes a skip link, visible focus rings, native controls/disclosures, existing tab arrow navigation, Ctrl/Cmd+Enter to request plan preparation through existing validation, Escape back to the conversation, canvas zoom/pan keys, and table-based selection. Reduced-motion disables transitions. Full WCAG or assistive-technology certification is not claimed.

## Implementation and verification order

1. Preserve baseline sources and capture original overview/setup.
2. Audit actual APIs, figure contracts and lifecycle; research primary public sources.
3. Add the token stylesheet and layout while preserving all IDs and backend forms.
4. Add isolated recorded-conversation and bounded scientific-figure modules with tests.
5. Compose shipped JS/CSS into the existing nonce-protected page. Add only these exact assets to wheel/source allowlists; retain the same CSP, token and Origin/Host rules.
6. Verify source-context binding, navigation/remount, unknown payloads and exports; fix review findings.
7. Run relevant synthetic control/figure/package regressions and a fresh real numerical software fixture. Inspect actual rendered states at desktop/laptop/smaller widths and both themes.
8. Measure clearly labeled 50k/100k software workloads. Report limits, evidence and remaining backend capabilities honestly.

No scientific fitting, filtering, inference, backend admission rule, task dataset, private golden, frozen qualification or remote dispatch is changed by this redesign.
