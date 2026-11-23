---
name: rna-workbench
description: Inspect local RNA-seq data, check answerability, run reviewed DevHarness workflows, and assemble receipt-backed research packages from public or synthetic evidence.
---

# RNA-seq research through DevHarness

Use the seven DevHarness MCP tools and their live schemas. Load [MCP request examples](references/mcp-payload-contracts.md) for exact fields and handoff conditions. Use canonical absolute paths inside the configured input root and keep originals read-only.

1. Call `inspect_rna_inputs`. Identify the assay, physical measurements, experimental units, groups, and missing provenance. Separate observed fields from supplied declarations and unverified inferences. A sample or capture label cannot establish biological independence.
2. State the scientific question and requirement sources. Call `check_answerability` with local inputs and, where needed, an explicit design. Clarify missing contrasts, labels, provenance, or independent units. Preserve refusals for non-estimable designs.
3. Compare appropriate methods and freeze evaluation criteria before seeing results. Call `plan_rna_workflow` with a supported operation. Save its `session_id`, `plan_id`, and `plan_sha256`. Keep the same session for follow-ups on the original input scope.
4. Complete Stage A before Stage H. As soon as the initial plan creates an eligible session, call `manage_research_aim` to list the aims and read the current revision. Propose the exploratory aim with its estimand and competing explanations. With the researcher's authorization, approve it at the current revision before executing the core fits. The approved aim gives execution receipts their evidence basis when the fits run.
5. Obtain native approval for each `execute_reviewed_plan` call. Poll `get_run_status`. Use `get_execution_receipt` for bounded numeric evidence, probability checks, and the replay digest. Record failures, exclusions, and unestimated results.
6. Test competing biological and technical explanations. Use `manage_research_aim` to record outcomes and contradictions, revise aims, and build the replayable package. Require sensitivity or independent validation before making stronger claims. State uncertainty and missing evidence.

## What to run in MCP

- Run descriptive matrix audits and local pre-staged `qc-salmon`. Preserve fractional estimates and effective lengths for offset-aware modeling. Keep normalized expression separate from raw counts.
- Stage bulk DE and open Companion Studio when the run reports `awaiting_review`. The fit requires its separate reviews and a fresh local execution grant.
- Use admitted single-cell counts or a verified raw droplet source for droplet QC, batch QC, capture-level doublet predictions, preparation, and integration. Treat predicted doublets as flags. Check batch mixing and biological conservation when measured.
- With an existing QC selection and declared independent biological units, stage scCODA or bind donor pseudobulk for separate Studio review. Use a retained, verified DE fit to stage a gene-list pathway analysis from its exact tested background.
- Build the package from current evidence. Use the default schema for receipt-bound fields or supply the path and digest of a custom schema. A partial package can document uncertainty without a discovery artifact.

## When to open Companion Studio

- Keep private and clinical data in loopback Studio with `--offline-only`. Keep files, values, plots, and outcomes off MCP. Clinical release is disabled.
- Open the supplied `studio_url` for interactive QC revision, review and acceptance, independently qualified labels, and workflows whose required artifacts or authority are absent from MCP. Use Studio or the Python coordinator for exploratory Tier B work with explicit controls and review.
- Literature query execution requires an already registered, network-authorized grant and current post-evidence claim. Register those in the local coordinator.

Never download FASTQ or BAM archives or load expression matrices larger than 100 MB into pandas. Stop or narrow the claim when measurements, design, runtime, or independent evidence are inadequate.
