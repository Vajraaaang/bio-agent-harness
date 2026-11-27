---
name: scrna-snrna-qc-integration
description: Assess droplet and nucleus quality, donor identity and integration choices before downstream single-cell inference.
---

# Capture, identity and integration QC

Identify capture, donor, chemistry, sequencing reference and assay. Preserve raw droplets, filtered counts, normalized expression and latent coordinates as distinct representations. Compare QC distributions by capture and donor, including differential retention by condition, before selecting exclusions.

- CellBender and SoupX need evidence about the ambient pool. Check for an unfiltered droplet matrix and matching features/barcodes. Preserve estimated ambient fractions, removed signal and the original counts. If empty droplets are missing, record the resulting uncertainty and assess sensitivity to ambient markers.
- For snRNA, document whether introns were counted, nuclear complexity and library size. Check genomic feature IDs and annotation before using mitochondrial fractions. If mito symbols are absent, report the metric as unavailable and record the features tested. Avoid thresholds transferred from whole cells without assay-specific evidence.
- Run scDblFinder or Scrublet by capture, matching loading rate and sample multiplexing. Retain scores, call thresholds and borderline cells for sensitivity analyses. With authorized, locally staged SNP evidence, compare souporcell or Vireo assignments to independently documented donor identities. Flag mixed, swapped or unresolved assignments before pseudobulk.
- Compare an unintegrated baseline and candidate integrations under the same feature and cell selection. Use scib-metrics to assess both batch mixing and biological conservation within comparable cell types. Inspect donor-level effects, rare type retention and stability. When batch and condition coincide, integrated proximity cannot establish an adjusted condition effect.

Discover available implementations with `list_kernels_and_methods`. Bind the selected cell set, parameters, diagnostic plots and alternatives to a receipt. Keep inference on an eligible measurement layer.
