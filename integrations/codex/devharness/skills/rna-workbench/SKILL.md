---
name: rna-workbench
description: Inspect local RNA-seq data, check answerability, run reviewed DevHarness workflows, and assemble receipt-backed research packages from public or synthetic evidence.
---

# RNA-seq research through DevHarness

Use the seven DevHarness MCP tools and their live schemas. Load [MCP request examples](references/mcp-payload-contracts.md) for exact fields, worked examples, and handoff conditions. Use canonical absolute paths accepted by the plugin's configured input policy and keep originals read-only.

Start every analysis question with the DevHarness tools, including questions that may be unsupported. The first valid tool call starts or reuses offline Companion Studio and opens it. Follow the recorded investigation across MCP and the Studio handoff through the supported execution and results route. A standalone script or direct kernel run is not a plugin run or a receipt. If the plugin cannot complete a route, retain the answerability, approval, runtime, or execution blocker in the investigation and describe it to the researcher.

Check the runtime for the selected analysis. Use the installed MCP server and its configured Python interpreter for local runtime checks. Read the command from the installed plugin's `.mcp.json` when that check is needed. Use the selected workflow's readiness information and frozen plan to identify its required Python packages, pinned kernel, or local Studio route. Report missing prerequisites and keep execution approval separate from planning.

1. Call `inspect_rna_inputs`. Identify the assay, physical measurements, experimental units, groups, and missing provenance. Separate observed fields from supplied declarations and unverified inferences. A sample or capture label cannot establish biological independence.
2. State the scientific question and requirement sources. Call `check_answerability` with local inputs and, where needed, an explicit design. Clarify missing contrasts, labels, provenance, or independent units. Preserve refusals for non-estimable designs.
3. Compare appropriate methods and freeze evaluation criteria before seeing results. Call `plan_rna_workflow` with a supported operation. Save its `session_id`, `plan_id`, and `plan_sha256`. Keep the same session for follow-ups on the original input scope.
4. Complete Stage A before Stage H. As soon as the initial plan creates an eligible session, call `manage_research_aim` to list the aims and read the current revision. Propose the exploratory aim with its estimand and competing explanations. With the researcher's authorization, approve it at the current revision before executing the core fits. The approved aim gives execution receipts their evidence basis when the fits run.
5. Follow the selected workflow's execution route. Obtain native approval for each `execute_reviewed_plan` call, or complete the local review and execution step when the plan hands off to Companion Studio. Use `get_run_status` and `get_execution_receipt` for MCP-visible runs. Record failures, exclusions, and unestimated results.
6. Test competing biological and technical explanations. Use `manage_research_aim` to record outcomes and contradictions, revise aims, and build the replayable package. Require sensitivity or independent validation before making stronger claims. State uncertainty and missing evidence.

## What to run in MCP

- Run descriptive matrix audits and local pre-staged `qc-salmon`. Preserve fractional estimates and effective lengths for offset-aware modeling. Keep normalized expression separate from raw counts.
- Stage bulk DE and open Companion Studio when the run reports `awaiting_review`. The fit requires its separate reviews and a fresh local execution grant.
- Use admitted single-cell counts or a verified raw droplet source for droplet QC, batch QC, capture-level doublet predictions, preparation, and integration. Treat predicted doublets as flags. Check batch mixing and biological conservation when measured.
- With an existing QC selection and declared independent biological units, stage scCODA or bind donor pseudobulk for separate Studio review. Use a retained, verified DE fit to stage a gene-list pathway analysis from its exact tested background.
- Build the package from current evidence. Use the default schema for receipt-bound fields or supply the path and digest of a custom schema. A partial package can document uncertainty without a discovery artifact.

## When to open Companion Studio

- For a completed plugin run, use its Studio link to open the results-only Overview. Workflows shows the recorded inputs, settings, and available execution evidence for that run. Figures shows verified custom figures first for the selected investigation and prefers Plotly when compatible chart data are available.
- Code shows recorded execution requests, input labels, frozen settings, runtime, and available evidence hashes for plugin runs. Label replay scripts generated after a run as reconstructed. Show executed implementation source only when it matches the run's recorded digest.
- Use the existing Studio plot recipes for matching verified outputs. Supported receipt tables automatically get their interactive chart in Figures. Use the retained renderer or image when it preserves evidence the interactive adapter cannot show.

- Keep private and clinical data in loopback Studio with `--offline-only`. Keep files, values, plots, and outcomes off MCP. Clinical release is disabled.
- Open the supplied `studio_url` for interactive QC revision, review and acceptance, independently qualified labels, and workflows whose required artifacts or authority are absent from MCP. Keep exploratory Tier B work in the same Studio investigation when it offers a reviewed route. If no reviewed route is available, report that limit.
- Literature query execution requires an already registered, network-authorized grant and current post-evidence claim. Register those in the local coordinator.

Never download FASTQ or BAM archives or load expression matrices larger than 100 MB into pandas. Stop or narrow the claim when measurements, design, runtime, or independent evidence are inadequate.
