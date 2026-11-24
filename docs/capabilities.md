# RNA-seq workflows

Start by identifying the measured features, biological units, and the
comparison. Use a compatible representation and retain the origin of design
declarations. Cells or libraries from a shared donor contribute observations
within that unit. Independent donors or specimens supply replication.

| Workflow | Inputs and prerequisites | Analysis and outputs |
| --- | --- | --- |
| Bulk gene-level differential expression | Original gene counts or supported quantified estimates, sample metadata, independent or repeated-unit declarations, and an estimable contrast | Exact-design count-model fit with coefficients, effect estimates, and multiple-testing results. Fit review takes place in the Workbench. |
| Bulk FASTQ QC and Salmon | Pre-staged reads, manifest, local references, and digest-pinned transcript-to-gene map | Workflow QC and transcript quantification with retained evidence. Gene-level modeling has its own design and approval. |
| Single-cell QC, doublet assessment, and integration | Original counts, capture IDs, and batch metadata. Droplet QC also requires verified raw-droplet provenance. | Capture- and batch-level diagnostics, retained-cell selection, and compatible integration routes. Integration evidence includes technical and biological checks where the necessary labels are available. |
| Donor-level pseudobulk | Accepted QC, original counts, cell labels, an approved aim, and declared donor structure | Aggregate by donor and population, keep low-cell and absent-profile diagnostics, and check the declared contrast before a separately reviewed fit. |
| Relative composition | Accepted QC, confirmed independent donors, condition labels, cell types, and an explicit reference type | scCODA reports relative effects, posterior inclusion, and sampling diagnostics for the specified model. |
| Neighborhood differential abundance | Declared embedding and donor and condition metadata with compatible support | Overlapping neighborhoods, donor-level counts and contrasts, and spatially weighted multiple-testing adjustment. |
| Bulk deconvolution | Normalized bulk expression and a compatible donor-derived reference with a shared feature axis | Reference-conditional relative fractions and fit diagnostics. Reference coverage constrains interpretation. |
| Pathway and gene-program testing | A verified upstream fit, identified tested universe where needed, and mapped curated collections | Gene-list overrepresentation, ranked enrichment, competitive pathway tests, or regulator activity summaries, depending on the selected runtime and inputs. |
| Spatial pattern measurement | Expression, physical coordinates with units, and donor and section metadata | Section-aware spatial expression and colocalization statistics. |
| Cell–cell coexpression | Cell types, donor identities, and a compatible ligand–receptor reference | Donor-stratified specificity and interaction scores. The result describes expression associations. |
| Perturbation response | Measured expression, a declared control group, and replicated assignments | Replicate-aware expression summaries and contrasts between existing groups. |
| Phenotype prediction | Expression, donor-linked outcomes, group IDs, and a digest-pinned independent partition | Nested group-aware evaluation with preprocessing inside folds and separately retained independent-partition metrics. |

Choose the analysis using the data and question first. For example, pseudobulk
tests within-population expression, while scCODA estimates relative shifts in
population abundance. A design may support one question and need more evidence
for the other. Choice of reference, filtering, and contrasts affects the
interpretation of a fitted result.

The server reports eligibility or the evidence required to continue. A method
can also need a mounted runtime or a separately reviewed fit. [MCP requests](mcp-tools.md) · [Worked single-cell walkthrough](../examples/single-cell-rnaseq/README.md)

DevHarness is scoped to computational RNA-seq research. Calibration, reference
comparisons, and review apply to the specific method and data. [Evaluation](evaluation.md)
