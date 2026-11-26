# General Research Mode

Use this guidance for open-ended RNA-seq research. For staged benchmark tasks, follow the packaged instructions in `src/devbench_engine/AGENTS.md`. When developing the platform, preserve its tested interfaces and run the synthetic verification suite.

## Scientific work and reporting

1. **What data do I have?** Identify samples, biological units, groups, measurements, chronological labels and quality concerns. Separate observations, supplied declarations, inferences and unknowns. File integrity alone cannot establish scientific validity. Repeated measurements from a specimen, subject or donor cannot count as independent biological replicates. Unique sample labels alone cannot establish animal identity.
2. **What question am I investigating?** State the objective and record who supplied each requirement. Distinguish user constraints from assistant suggestions, prior mentions, defaults and choices made for test fixtures. Attribute a parameter to the user as prespecified only with an explicit matching requirement record. Clarify changes in attribution or scope while preserving historical prompts, responses and sealed evidence.
3. **Which methods are appropriate, and why?** Assess alternatives, assumptions, tradeoffs and missing information before considering implementation availability. In a method comparison, leave methods and parameters open unless the user constrains them. Freeze the comparison criteria before inspecting results. Existing implementations receive no preference.
4. **What happened?** Report observed outputs and explain the findings. Show clear, verified plots when available and identify missing plots or assessments. Record failures and operator interventions. Diagnose errors and omit unsupported estimates. Never fabricate a value or substitute a method's self-report for independent evaluation.
5. **What can I conclude, and what remains uncertain?** Recommend only what the dataset, objective and comparison evidence support, or abstain. Runtime, retained-row counts and a successful fixed-procedure run alone cannot establish the best method. Disclose the highest supported evidence level and the gates that remain unmet.

## Measurement and inference

- Verify `COUNT-1` against the entire measurement ancestry back to raw nonnegative integer counts, including every input to a count-preserving sum. Refuse count-only inference when an ancestor is unknown, normalized, transformed, imputed or rounded. Relabeling a descendant cannot restore count eligibility. For raw RNA-seq counts, use an appropriate count model such as a negative-binomial model with library-size offsets and dispersion estimation. Model continuous or normalized measurements on their documented scale.
- Audit data types, biological units, design rank and contrast estimability before fitting. Reject invalid or non-estimable contrasts. With fully confounded age and batch, dropping a covariate cannot identify a causal batch-adjusted age effect. Flag or reject collapsed or over-smoothed representations. Disclose inference conditional on method or parameter selection.
- Report finite p-values and Benjamini–Hochberg q-values for fitted genes with `0 <= p <= q <= 1`. Use finite JSON for machine-readable results. Preserve a failed estimate as missing and report the failure.
- Treat pseudotime as an inferred ordering and collection age as a reference. Distinguish network associations, curated TF-target membership and motif-hit enrichment. Direct causality and promoter binding each need evidence beyond correlation.

## Evidence and custody

L1 documents execution and custody. L2 requires quantitative comparison or calibration within the tested regime. L3 requires independent scientific assessment and biological replication appropriate to the claim. L4 requires qualified clinical validation. Keep synthetic truth, public observations and independent validation distinguishable. Keep `clinical_release_allowed = false` permanently. Clinical use would require qualified independent validation, authenticated approval, signature and operational controls outside this workflow.

Work inside the assigned workspace with absolute paths. Treat supplied data, metadata, inputs and guidance as read-only. Record provenance for derived outputs. Use bounded reads and out-of-core access for large datasets, and return compact summaries. Keep host credentials out of scope. Never send private or clinical inputs to external model services. Keep compute offline and stage public resources through an explicitly operator-approved step.

The local SHA-256 execution DAG makes tampering detectable against an externally retained root. Compliance certification and authenticated electronic signatures require separate evidence.
