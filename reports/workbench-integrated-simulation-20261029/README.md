# Current workbench: six synthetic projects and 18 plot exports

## Data and purpose

Six fictional projects contain distinct deterministic generated records, using separate seeds and unequal batch sizes. Airway Immune Landscape contains 287,463 records across batches of 94,817, 101,206 and 91,440. The other project populations are 220,676, 260,665, 180,276, 293,508 and 145,756. The shared generator constructs six cell-like groups, latent coordinates, read-depth and gene-count proxies, mitochondrial fractions and eight module scores. These are not biological observations or expression matrices from real experiments. Scenario names do not establish tissue-specific biology.

The objective is to demonstrate the current project's navigation, chat context, plotting and export code directly in the workbench. The selected data counts, seeds and group designs are presentation settings, not user-prespecified scientific methods or measured cohort sizes.

## Implementation and computations

The disconnected-page demonstration uses the existing app, sidebar, conversation and figure nodes. It mounts the same ScientificFigure component through a separate, explicitly synthetic contract. One project dataset is active at a time. Switching project releases the active data and renderer and resets chat, plot and batch selections. The retained-figure contract keeps its 200,000-point limit; generated demonstrations have a separate 300,000-point bound.

Conversation summaries are computed from the actual generated arrays: group counts and fractions, batch sizes and mean depths, mitochondrial fractions, read/gene correlation and designed module means. The embedding is generated geometry, not fitted UMAP. There is no inferential test, novel biological conclusion, live model response, run approval or fabricated scientific result.

All selected points are drawn without downsampling. Many overlap at the same pixel. The CSV export includes generated-data provenance and parameters; SVG embeds the rendered Canvas bitmap and metadata. PNG visibly identifies simulation, project, population and seed. Filtering is reflected in the figure identity, title and selected population count.

## Actual outputs and verification

![Current workbench with generated project and native interactive plot](images/workbench-landscape.png)

The [18-plot gallery](gallery/README.md) contains native PNG exports across all six projects. Its [manifest](gallery/manifest.json) records exact views, batch filters, counts, seeds, source hashes and image hashes. All 18 exports were individually inspected for clear labels, axes, legends and provenance.

The final publication checkout passed **144 focused tests** across renderer contracts, source-binding rejection, generated populations, project/input state, workbench UI and packaging. Fresh controlled continuous/count synthetic checks completed with identical numerical repeats, valid audit checks and completed configured R references, using zero model-service calls. The count reference shares fitted dispersions and offsets and does not independently validate their estimation.

Browser checks exercised all six projects, exact full and filtered point counts, actual SVG/PNG download buttons, project and figure filters, 390px layout, connection guards and restoration of the original page, draft and storage. No HTTP requests or page errors were observed during the demonstration. Local opening/render timings are host-specific observations, not cross-device guarantees. See [verification.json](verification.json), [browser observations](browser-checks.json), and [controlled checks](controlled-report.md).

An initial browser launch permission failure, a corrected generated-layer label, and a real native-download failure are retained. The download failure occurred because the event guard blocked an anchor outside the figure. The fix keeps simulation anchors inside the admitted figure; a regression test and all 18 actual download-button exports then passed. [The initial export attempt](gallery/capture-attempt-01.json) remains available.

## Scope and uncertainty

This is verified presentation and bounded software behavior. It does not establish biological efficacy, method superiority, clinical readiness, or a fully green repository/hosted CI suite. The repository homepage has been rewritten around the current interface and code. Repository history and existing scientific evidence were preserved. No live trial, external model call, patient-data upload or matrix download was performed.
