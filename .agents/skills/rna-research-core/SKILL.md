---
name: rna-research-core
description: Start or resume a governed RNA investigation, bind public inputs to an aim, and report the strongest claim its evidence supports.
---

# Governed RNA research

For public or synthetic data, use the live schemas and preserve the returned identifiers. For private or clinical material, follow the private-data-studio skill before supplying even a filename.

1. **E0, answerability.** Use `inspect_runtime_capabilities` for local availability and `inspect_inputs` for a bounded inventory. Distinguish observed matrix structure from declared units, group assignments and unknowns. Record the estimand, contrast and source of each requirement. Only a matching explicit user instruction establishes prespecification. Call `check_answerability` with the biological units and design. Use `E_ABSTAIN` when the question is unidentifiable, documenting what evidence could resolve it.
2. **E1, custody.** Use `register_investigation_input` to bind the immutable input, classification and representation. Retrieve revision and existing branches with `list_investigation_state`. Use `manage_research_aim` with the current revision to record aims, alternatives, approvals and later evidence.
3. **E2, exploration.** Compare plausible methods and assumptions before consulting `list_kernels_and_methods` for implementations, readiness and calibration. **E3** tests competing explanations. **E4** benchmarks candidate methods on frozen criteria and separate evaluation partitions. Keep exploratory choices and researcher instructions distinguishable.
4. **E5, confirmatory fit.** Bind the approved aim, input hashes, lane, estimand, exclusions, tests and sensitivity plan using `freeze_analysis_plan`. Keep the exact plan ID and SHA-256. After required review, call `submit_reviewed_run`. Follow `get_run_status` until terminal, or use `cancel_run` for an authorized cancellation. If the client lacks interactive review, use the returned Studio or CLI handoff. Verify outputs with `get_run_receipt`. Preserve failures and operator interventions.
5. **E6, synthesis.** Use `query_artifact_table` with the recorded artifact ID and SHA-256. Request only needed columns, filters and aggregates, at most 200 rows per call. Use `next_offset` only for additional relevant evidence. Bind conclusions to the tested family, receipt and any post-fit checks. Report missing estimates and plots explicitly. Never fill missing values or infer a biological result from successful execution.

L1 documents execution and custody. L2 requires quantitative comparison or calibration in the applicable regime. L3 adds independent scientific assessment and appropriate biological replication. L4 requires qualified clinical validation outside this workflow. Keep `clinical_release_allowed = false`.
