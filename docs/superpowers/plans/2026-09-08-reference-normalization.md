# Prespecified reference normalization implementation plan

> Execute in the existing authorized clinical-platform work using subagent-driven-development for the independent contract module and independent review. The checkout has no committed HEAD; retain source/evidence snapshots rather than inventing a Git base or rewriting measured archives.

**Goal:** Permit an explicitly declared DESeq2 normalization reference set within new signed, governed native plans and verify that only those references determine the saved size factors.

**Architecture:** Add a strict immutable NormalizationReference nested in new ReferenceNativePlan/version5 and ReferenceNativeEffectPlan/version6. Older plan versions1-4 and their canonical bytes retain their interpretation. New versions remain synthetic/public candidates with clinical release disabled. The native request passes only the reference IDs, a canonical specification digest and the fixed eligibility policy; all metadata remains in the signed/preserved plan.

**Tech stack:** Existing Python/Pydantic contracts, Ed25519 plan/authority verification, native DESeq2 1.52.0, existing execution DAG and semantic verifier. No new statistical dependency, model backend or production identity integration.

## Design decision

Keeping whole-panel RLE alone would leave the diagnosed reference-scale issue unaddressed. Accepting arbitrary external factors adds calibration and measurement-provenance obligations without a supplied assay context. The selected bounded change uses the supported DESeq2 controlGenes API with prespecified reference genes. It enables an assay to supply its independently justified reference set; it does not establish that any selected genes are biologically stable or that the pointwise intervals are calibrated.

All declared references must occur in the signed panel and have strictly positive raw counts in every sample. Failure is explicit and occurs before Docker dispatch; there is no automatic dropping of controls or fallback to the full panel. This is one deliberately declared eligibility policy, not a universal recommendation for all assays. A different policy would need an explicit versioned method and validation. No clinical reference genes or acceptance margins will be invented.

## Strict reference contract

Create devharness_controlled/normalization_reference.py. NormalizationReference extends FrozenContract and has eight required fields:

- schema_version: literal normalization_reference/1
- reference_id: nonblank NonemptyText
- study_id: nonblank NonemptyText
- gene_ids: immutable tuple of1-5000unique identifiers; each is trimmed, nonblank, at most256UTF-8bytes and contains no ASCII control characters or DEL
- annotation_reference: nonblank NonemptyText
- selection_basis: nonblank NonemptyText
- eligibility_policy: literal all_positive
- qualification_status: literal not_established

No defaults or unknown fields. parse_normalization_reference_bytes must enforce existing duplicate/nonfinite/UTF-8/1MiB JSON rules before strict JSON-to-tuple validation. reference_genes_for_panel(reference, *, study_id, panel_genes) must revalidate the specification, require exact study binding and nonempty subset membership, and return the references in signed panel order. The original declared order remains part of the canonical digest.

Example complete specification:

```json
{"schema_version":"normalization_reference/1","reference_id":"synthetic-reference-v1","study_id":"synthetic-study","gene_ids":["refA","refB"],"annotation_reference":"synthetic-identifiers-v1","selection_basis":"Prespecified synthetic reference genes; not a clinical stability assertion","eligibility_policy":"all_positive","qualification_status":"not_established"}
```

## Tasks

- [x] Contract worker owns only normalization_reference.py and tests/test_normalization_reference.py. Write and run meaningful failing tests for missing/extra/duplicate/nonfinite fields, mutable Python arrays, invalid identifiers, false qualification claims, canonical binding, study mismatch and absent panel members; then implement and verify. Other agents may edit other files; do not revert their work. No commits or old-result edits.
- [x] Coordinator adds ReferenceNativePlan/version5 and ReferenceNativeEffectPlan/version6 in native_contracts.py, plus exact parser registration and is_reference_plan. Both inherit governed authority behavior and require DESeq2. Their nested reference must match the plan study and panel. Effect-to-primary numerical projection must preserve the reference contract; projections are never signed-plan substitutes. Test approval digests and legacy canonical stability.
- [x] Coordinator integrates new request versions3(base)/4(effect) in native_executor.py and native_nb.R. The request adds normalization_reference={gene_ids,specification_sha256,eligibility_policy}; all other fitting settings stay unchanged. Before fitting, both implementations reject missing/nonpositive declared references. Python factors use exp(median(log(reference_counts)-row_mean_log)); R uses estimateSizeFactors(type=ratio,controlGenes=...). Preserve the native requested/actual reference scope and digest.
- [x] Coordinator updates native_results.py/native_verification.py to require declared reference membership and the new scope/digest, and to recompute factors from preserved counts and references. Numerical projection must not discard reference semantics. Add meaningful negative controls for a valid-looking full-panel normalization record, wrong digest/set and reference mutations even with internally consistent arithmetic.
- [x] Exercise actual signed governed base/effect execution, native controlGenes parity, metadata/signature tampering, nonreference perturbation and pre-dispatch zero/missing-reference refusals. Confirm operated duplicate/recovery compatibility. Preserve all failed checks and distinguish fixture authority from human approval.
- [x] Independent specification and quality reviews, focused and full regression suite, documentation/package/notebook source verification, and a concise evidence-backed report. Do not infer clinical coverage improvement from this implementation or change the original statistical procedure during measurement.
- [ ] Retrieve the existing Colab evidence ZIP and independently verify its inventory and receipts. Actual execution was observed, but browser downloads timed out and the connected Drive metadata request returned HTTP 404. The four visible audit passes are not a substitute for external verification.

## Verification boundaries

The existing normal-Wald interval construction and dispersion estimator do not change. The prior4000-cohort undercoverage and512-cohort mechanism results remain adverse/unqualified evidence. New positive checks prove reference binding and native implementation parity, not clinical reference stability, interval calibration or regulatory compliance. Production identity, clinical context/approval, deployment and protected retention remain open.
