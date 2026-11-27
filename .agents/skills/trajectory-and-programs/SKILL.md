---
name: trajectory-and-programs
description: Assess inferred cell order, expression programs and enrichment against a verified gene universe while bounding regulatory interpretations.
---

# Trajectories, programs and pathways

Treat pseudotime as an inferred ordering and collection time as an observed reference. Freeze lineage/root choices without target-gene outcomes. Examine direction, branching, coverage and alternative roots within donors before pooling. tradeSeq count smoothers or donor-level splines need library-depth handling, enough independent donors, and evidence distinguishing developmental shifts from composition. Report uncertainty where order or lineage membership is ambiguous.

For cNMF or other gene programs, vary rank and initialization, compare stability across donors and identify batch, depth or composition factors. Test a selected program on independent evidence before elevating the claim.

Bind the source fit, gene IDs, estimable rows, tested-universe checksum, gene-set version, species and mapping policy. ORA draws its foreground from that tested background using a frozen threshold. For `fgsea` or `decoupleR`, rank the full eligible universe without a foreground q cutoff. The score is `math.copysign(-math.log10(max(p, 1e-300)), effect) if effect else 0.0`. Use the retained fitted effect and finite p-value. For example, a negative effect with p = 0.01 gives −2, a zero effect gives 0 and a positive effect with p = 0 gives 300. Exclude failed estimates and record exclusions. decoupleR additionally needs signed, aligned weights. CAMERA needs the matched expression, design and contrast to account for inter-gene correlation.

Distinguish inferred GRN associations, curated TF-target membership and motif-hit enrichment. Each supports a different claim. Causal regulation or direct binding needs independent perturbation or binding evidence.
