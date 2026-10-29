# Synthetic atlas

Choose **Explore synthetic atlas** on the connection screen or **Synthetic atlas**
in the workbench sidebar. Opening the gallery generates 300,000 synthetic cells
in the browser: exactly 100,000 in each of three technical batches, with six
illustrative cell-like groups and eight generated module scores. The gallery
does not read project inputs or create analysis, trial, chat, or review records.

This is a presentation demonstration. The positions, group names, expression
patterns, read counts, detected genes, and mitochondrial fractions are generated
values, not observations. Coordinates are constructed latent positions, not a
computed UMAP. Cell-like names do not establish biological identities. No live
trial or model service is involved.

The seeded generator (`20261029`, generator version 1) intentionally varies group
mixtures, technical depth, and mitochondrial fractions between batches. These
choices illustrate visual differences; they do not estimate an experimental
effect. The panels show:

- **Cell landscape:** all 300,000 generated cells, or all 100,000 in a selected
  batch, rendered on Canvas without downsampling. Color can show cell-like group
  or technical batch; the legend follows the selected color mode.
- **Module signatures:** group summaries over every selected cell. Dot area is
  not proportional to prevalence: the radius is `2 + 11 × fraction(score > 1)`.
  Opacity is `0.15 + 0.85 × mean(score) / 5`; generated scores are bounded to 0–5.
- **Library complexity:** every selected cell, colored by batch, with axes
  extending to the maximum selected read and gene counts.
- **Batch distributions:** generated mitochondrial fractions in 0.5-point bins,
  with each batch normalized to its own peak. These are histogram outlines,
  not fitted probability densities.
- **Cell composition:** counts and within-batch group proportions computed over
  all selected cells.

Every scatter point is submitted to Canvas. Multiple points can occupy the same
screen pixel, so a dense area does not expose each cell individually. Rendering
uses a fixed 1,200-pixel-wide bitmap; zooming does not add spatial detail. The
aggregate plots intentionally summarize the selected population.

**Download figure SVG** exports the landscape as a Canvas bitmap embedded in an
SVG container, retaining the visible synthetic label. Embedded metadata records
the seed, synthetic provenance, batch/color selection, point count, and raster
rendering. It is not a vector-per-cell export or a scientific evidence bundle.

Generation is lazy, beginning only when the gallery opens. It uses browser
memory and can take longer on older devices. Closing the dialog restores focus
to its opener; Escape also closes it. The gallery is available without a service
credential, because it has no access to service data.

Verification covers deterministic generation, exact cell counts and finite
values, packaging, and browser behavior. Browser checks instrument the actual
scatter drawing calls, verify batch filtering, exported provenance, desktop and
mobile layout, and keyboard closure/focus. These are software presentation
checks, not biological validation or evidence that a scientific method works.
