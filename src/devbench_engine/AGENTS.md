# DevBench runtime guidance

These instructions apply to staged transcriptomics benchmark tasks. When developing the platform, preserve its tested interfaces and run the synthetic verification suite.

Work only inside the assigned workspace and resolve filesystem paths absolutely. Treat supplied data, metadata, task inputs, guidance and answer_schema.json as read-only. Do not inspect grader files, private goldens, other tasks or host credentials. Do not download BAM/FASTQ archives or load expression matrices larger than 100 MB into pandas. Use bounded reads and compact summaries; do not print full matrices or terminal dumps.

For assisted tasks use the registered devharness MCP tools, or ToolDispatcher in a trusted in-process workflow. Available tools include audit_expression_quality, validate_design_matrix, analyze_batch_effects, compact_temporal_state, fit_temporal_trajectory, infer_lineage, infer_regulatory_network, regulon_enrichment, motif_enrichment and calculate_temporal_monotonicity. Call the MCP tools in CLI runs so the harness can capture actual execution evidence. Standalone Python scripts calling supervised workers need a module-level `if __name__ == "__main__":` guard. Do not start an MCP event loop inside a notebook.

Follow exploration, diagnostics, modeling and synthesis when the adapter requests phases. Report observable findings, tool errors and corrections, not private reasoning. Intermediate phases return their own schema and must not emit the final artifact early. A tool failure is evidence to diagnose, not permission to fabricate a result.

Audit data types, chronological labels, biological units, contrasts and design rank before fitting. Raw counts use RLE library-size offsets and empirical-Bayes negative-binomial dispersions; continuous/normalized expression uses Gaussian splines without count conversion. Compare feasible AIC/BIC/GCV candidates when requested, or use a prespecified fixed df. Disclose selection-conditional inference. Report p-values and BH q-values for fitted genes; q must be at least p. Do not replace failed estimates with successful-looking values.

Repeated specimens, subjects or donors cannot be counted as independent biological replicates. Unique sample labels do not establish independent animal identity. Completely confounded age and batch do not yield an identifiable causal batch-adjusted effect merely by dropping a covariate. Invalid contrasts and collapsed/over-smoothed representations require explicit diagnostic warnings or rejection.

Pseudotime is an inferred ordering; collection age is a reference, not its universal truth. Network associations, curated TF-target membership and motif-hit enrichment have different evidence scopes. Do not infer direct causality or promoter binding from correlation alone. Keep simulation goldens, public observations and clinical validation distinct.

During synthesis write final_discovery.json and return finite JSON matching answer_schema.json. Common fields are stage_kinetics, four distinct master_regulators, condition_number_validated and biological_rationale. Include all task-specific fields, including diagnostic/refusal fields and gene_statistics when a fit was performed. Omit unsupported estimates rather than fabricating zeros or significance. Model output is schema-validated; unsupported claims may trigger binary vetoes.

Clinical release is held pending qualified independent validation, authenticated approval, signature and operational controls. The local SHA-256 execution DAG is tamper-evident against an externally retained root, not a compliance certificate or an authenticated electronic signature. No private clinical inputs are authorized for external model services in this workflow.
