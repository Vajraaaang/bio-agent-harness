---
name: composition-and-da
description: Test cell-type composition or neighborhood differential abundance across biological units and assess reference-based bulk mixture estimates.
---

# Composition and differential abundance

Build a donor-by-population count table with retained-cell totals, capture and group labels. Confirm comparable sampling, QC retention and annotation across groups. Freeze the annotation and denominator before testing. Relative abundance alone does not identify an absolute increase or loss.

For discrete populations, choose and justify a stable compositional reference. Fit multi-chain scCODA via a ready implementation such as pertpy, retaining seeds, warm-up and chain diagnostics. Inspect convergence, posterior inclusion and sensitivity to alternate reference populations. An unstable reference changes interpretation across the entire composition.

For a continuous cell-state manifold, use miloR on prespecified neighborhoods. Count cells per independent biological unit in each neighborhood, model the donor-level design and exposure, and account for overlapping neighborhoods when controlling FDR. Check neighborhood construction across batches, sensitivity to graph parameters and cell-state annotation.

Bulk deconvolution estimates mixture fractions conditional on matched tissue, platform, scale and donor-replicated reference signatures. Check missing populations, signature collinearity and held-out donor accuracy. Propagate uncertainty before using estimated fractions as covariates. Distinguish mixture shifts from within-cell-type expression changes.
