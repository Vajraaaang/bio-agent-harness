# Offline simulation in RNA Workbench

Open a fresh local workbench page without a session-token fragment and choose
**Open simulated project**. The current project/chat/figure layout displays an
**Airway Immune Landscape** fictional project initially. The Project selector
switches between six fictional scenarios. A persistent simulation label
identifies generated content throughout the view and in exports.

The initial project generates 287,463 cell-like records from seed `20261029`: 94,817,
101,206 and 91,440 in three deliberately unequal batches, six designed groups, and eight module scores. The
existing `ScientificFigure` renderer draws the selected population directly in
the workbench's `interactive-figure` canvas. No separate gallery or modal opens.

| Fictional project | Seed | Generated batch sizes | Total |
| --- | ---: | --- | ---: |
| Airway Immune Landscape | 20261029 | 94,817 / 101,206 / 91,440 | 287,463 |
| Tissue Repair Atlas | 104729 | 71,283 / 83,519 / 65,874 | 220,676 |
| Immune Activation Map | 130363 | 88,342 / 79,615 / 92,708 | 260,665 |
| Vascular Cell Landscape | 155921 | 56,843 / 64,127 / 59,306 | 180,276 |
| Stromal Remodeling Study | 196613 | 106,318 / 87,429 / 99,761 | 293,508 |
| Cell-Cycle Reference | 262147 | 43,719 / 52,864 / 49,173 | 145,756 |

Each scenario uses the same six-group generator with its own seed and batch
sizes. Names are fictional presentation contexts, not evidence of measured
organs, disease, repair, or cell-cycle biology. Switching projects destroys the
previous figure and releases its records before generating the next population;
only one project dataset is retained. Selection resets to that project's default
view and all batches, and the project selector is disabled during generation.

Five views show the generated cell landscape colored by group or batch, library
complexity, mitochondrial fraction, and immune-module score. Every selected
point is rendered, without downsampling; a batch filter displays the exact generated count for that batch.
Zoom, pan, selection, category filters, observation tables, and exports use the
same renderer as the workbench. Dense plots can place multiple points in one
screen pixel. Coordinates are generated latent positions, not fitted UMAP.

Sidebar conversations are scripted prompts. Their summaries are computed from
the generated records: group counts, batch means, read/gene correlation and
module-score means. They are neither model responses nor biological research
claims. Group names, separation and batch shifts are designed into the generator.
No significance testing, trial, approval or independent scientific review occurs.

The simulation uses distinct `devbench.simulated-figure/1` and
`devbench.simulated-figure-data/1` contracts. The ordinary retained-figure contract
remains separate and retains its 200,000-point limit; simulation is bounded to
300,000 points. SVG and PNG exports visibly identify simulated data. SVG embeds
the Canvas raster rather than emitting a vector circle for every cell. Other
exports retain synthetic provenance, including the seed and explicit batch sizes.
Filtered exports identify the selected batch in the panel title and identity.

Entry is allowed only on a fresh disconnected page. An active connection,
connection attempt, supplied token, or existing retained figure prevents entry.
The simulation never calls workbench APIs, imports project data, writes chat/run
records, or saves approvals. Original controls are hidden and their events are
blocked while simulation is active. **Exit simulation** restores the original
interface and draft. A fresh page is required after any attempted connection.

This feature verifies presentation and interaction with generated data. It does
not validate the scientific methods, biological identities, or clinical use of
the workbench. Rendering times depend on the browser and host.
