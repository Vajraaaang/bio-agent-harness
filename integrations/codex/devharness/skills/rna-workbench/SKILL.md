---
name: rna-workbench
description: Use DevHarness for supported RNA-seq research workflows, input and design checks, numerical tools, and evidence-backed reports. Use for bulk or single-cell analysis requests; clarify assay and input limits before running tools.
---

# RNA-seq research through DevHarness

Use the configured DevHarness runtime and the current analysis workspace. Codex
provides the conversation. The existing Python tools provide calculations. No
localhost web application, new model backend, or separate LLM call is required.

## Start with eligibility

Confirm the user's question, input locations, assay, and whether values are raw
counts or normalized expression. Confirm metadata roles where needed: condition,
donor/specimen, technical library/capture and batch. Do not infer biological
independence from unique cell/sample labels or infer count provenance from
integer-looking values. Ask for missing information or refuse unsupported
comparisons. Basic inspection does not establish inferential eligibility.

Use canonical absolute input/output paths inside the configured analysis scope.
Treat original data and metadata as read-only. Keep outputs in a fresh run
directory. Do not inspect or stage private goldens, graders, benchmark task
answers, host credentials or unrelated projects. Do not initiate downloads,
paid research, Colab allocations or live benchmarks merely because the plugin
is available. Respect permissions already granted for the specific request.

## Pick the existing supported route

- The registered MCP tools accept gene-by-sample CSV matrices or the existing
  custom Zarr/HDF5 expression format with aligned metadata CSV. These are not
  interchangeable with H5AD or Cell Ranger containers.
- The separate scRNA workbench accepts backed AnnData H5AD or supported Cell
  Ranger v3 feature-barcode HDF5. Use its existing CLI/notebook workflow when
  appropriate; do not push a large cell matrix through a small-table tool.
- The episode CLI supports bounded bulk raw-count/continuous contrasts and
  donor-aware scRNA count contrasts with separate proposal/review controls.
- Raw FASTQ/BAM processing, general snRNA qualification, spatial and isoform
  workflows are not supplied by this plugin. Do not claim universal RNA-seq
  support. Do not download raw sequencing archives as a fallback.

If a needed runtime/tool is absent, report that exact limitation. Do not replace
an unavailable numerical result with a guessed or fabricated answer.

## Use actual tool evidence

When available, use the registered tools by their advertised schemas:
`audit_expression_quality`, `validate_design_matrix`, `analyze_batch_effects`,
`compact_temporal_state`, `fit_temporal_trajectory`, `infer_lineage`,
`infer_regulatory_network`, `regulon_enrichment`, `motif_enrichment`, and
`calculate_temporal_monotonicity`.

Audit data and biological units and check the intended full design before
fitting. Retain repeated-donor identities. Do not drop a required confounder to
manufacture identifiability. Count and continuous models are different routes;
never round normalized expression into counts. Preserve errors and failed fits.
Do not change acceptance thresholds or private evaluation rules.

The installed Python runtime also provides the existing analysis CLIs. Consult
their public help/current interface before constructing an invocation. Use
the configured absolute Python executable and module names. Avoid starting a
nested Codex participant merely to drive an interactive plugin conversation.
Recorded-action CLI executions are not automatically live benchmark evidence.
Never start an MCP event loop inside a notebook.

## Review and present

State the plan, required assumptions and compute limits before analysis. Use
the existing proposal/review workflow where supported; the model cannot
approve its own plan. Changes after approval require a new recorded version.
Skill instructions guide behavior; they do not replace validators, permission
boundaries, resource supervisors or an independently qualified reviewer.

Use saved numerical outputs to generate or display figures. Show supported
images in the conversation and open existing reports/notebooks in an available
Codex panel. Do not invent plot values or imply a native custom dashboard exists.
Keep figure source tables, parameters and limitations accessible. A plot, hash
or software test does not prove a biological conclusion.

Separate statistical results, reference concordance, model suggestions and
unavailable evidence. Existing predicted cell labels support concordance, not
accuracy. Association and inferred ordering do not establish binding, causality
or ancestry. Report runtime/usage only when observed; otherwise mark unknown.

End with findings, limitations, completed/failed actions and artifact locations.
Interactive MCP access alone is not the enforced DevBench participant boundary.
This is research software, not a clinically validated or GxP-certified product.
