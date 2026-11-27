---
name: pseudobulk-and-de
description: Design and interpret donor-level bulk or pseudobulk differential expression and distinguish selective cluster markers from confirmatory effects.
---

# Differential expression by independent unit

Trace COUNT-1 through every ancestor of every summand back to raw, nonnegative integer counts. Audit a documented layer, transformations and aggregation membership. An integer-valued log2 matrix, rounded normalized abundance or an unknown parent fails COUNT-1. Use the documented continuous scale or abstain when lineage is unresolved.

Sum counts within the prespecified cell type and independent donor/sample-condition combination. Keep repeated samples paired or modeled within donor. Verify donor counts per group, residual degrees of freedom, full design rank and contrast estimability after exclusions. Record cell yield and exposure per profile. Fully confounded batch and condition require a narrower estimand or new data.

For a governed fit, apply ADR-15 routing using design and panel information before examining outcomes. For fewer than 500 eligible genes use the targeted edgeR v4 policy (TMM, filterByExpr, robust QL, abundance trend disabled). For small balanced or paired designs use edgeR v4 QL with limma-voom sensitivity. Use dreamlet for unbalanced repeated or longitudinal profiles, and limma-voom with quality weights for large or heterogeneous independent-donor cohorts with edgeR sensitivity. Check regime calibration. DESeq2 is a candidate in method comparisons with frozen criteria. Include library-size offsets and dispersion estimation for count models.

Clustering and testing markers on the same observations creates selection bias. Return exploratory rankings without inferential p/q values unless independent held-out data or a calibrated selective procedure such as ClusterDE supports the test. Freeze the tested universe and BH family. After fitting, use leave-one-donor-out (LODO) refits to inspect sign changes, rank stability and single-donor leverage. Report infeasible refits, failed estimates and sensitivity to donor removal, including for negative findings.
