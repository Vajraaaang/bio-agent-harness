---
name: spatial-and-ccc
description: Evaluate tissue spatial patterns and candidate cell–cell communication using section-aware coordinates, donor replication and curated interactions.
---

# Spatially variable genes and communication

Identify the SpatialData coordinate system, axis order, transforms, physical units, scale and tissue mask for each section. Align the raw spot-by-gene counts keyed by `spot_id` to unique `spot_id,donor_id,section_id,x,y` metadata. The `cell_type` column is optional for spot-level spatially variable gene (SVG) tests. Supply validated labels for cell-type colocalization. A count row with an empty library needs a recorded exclusion. Build graphs within donor, section and tissue mask.

For example, a section with micrometre coordinates in a registered tissue frame and no type labels supports within-section SVG assessment. Record the frame, units, graph parameters, seed, null procedure and source hashes. Limit its result to that section. Check observed spatial structure against library depth, tissue edges and coverage. Where supported, compare a depth-conditioned negative-binomial (NB) model and SPARK-X with appropriate covariates or residual inputs. SPARK-X is a nonparametric spatial test. Assess replication across independent donors before generalizing.

For candidate ligand–receptor (LR) associations, pin LIANA or CellChat resource versions, species, subunit composition, sender/receiver identities, expression thresholds and null model. Evaluate proximity and donor consistency where coordinates permit. Coexpression and curated LR compatibility are evidence for a hypothesis. Physical signaling requires additional spatial or functional evidence.
