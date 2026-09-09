# Controlled evidence verifier implementation plan

> **For agentic workers:** Use subagent-driven development for independent work and test-driven local implementation for the verifier. No application files owned by this plan may be edited concurrently by another worker.

**Goal:** Prove that a retained controlled-analysis history is complete and consistently bound to an approved plan, rather than merely hash-consistent.

**Architecture:** Keep the existing generic DAG verifier as the integrity primitive. Add a read-only versioned semantic verifier that reads only preserved CAS evidence, requires an explicit expected root and operator-supplied signing registry, checks historical authorization at recorded execution time, and validates event, plan, data, environment and numerical-result links. Report completed, failed and invalid records separately; no clinical authorization is inferred.

**Tech Stack:** Python, existing strict JSON/Pydantic contracts, Ed25519 verifier and SHA-256 CAS/DAG.

## Task 1: Regressions before implementation

- [x] Add tests in `tests/test_controlled_verification.py` using a real supervised Gaussian fixture plus newly sealed adversarial histories.
- [x] Verify the current CLI falsely accepts empty/completion-only records, then require the new semantic result to reject them.
- [x] Cover missing stages, wrong plan/input/result/source links, altered numerical contracts, invalid signatures/registries, time reversal, source tampering and genuine failed runs. Retain fixture keys in test memory only; no test modifies historical artifacts.

## Task 2: Semantic verification module

- [x] Create `devharness_controlled/verification.py` with `verify_controlled_execution(manifest_path, expected_root_hash, trusted_signers)`.
- [x] Accept only the supported controlled schema, the fixed ordered event sequence or a failed prefix terminated by a failure outcome. Check consecutive causal parents and nondecreasing recorded times.
- [x] Resolve unique required logical artifacts from the manifest; hash-check bytes during bounded reads. Parse canonical plan, compare signed fingerprint, validate actual external keys at recorded approval/launch times, and compare archived registry bindings for used signers.
- [x] Bind preserved input digests, code/environment identity, parameters, hypothesis family, normalization and finite p/BH values to the signed plan. A valid failed record remains `analysis_completed=false`.
- [x] Require a complete preserved result for a claimed numerical return; distinguish algorithmic/evidence validity from clinical/identity/deployment qualification.

## Task 3: Integrate and exercise

- [x] Route `devharness-controlled verify` through semantic verification with required `--trust-registry`. Expose the old primitive as `verify-integrity` with an explicit limited scope.
- [x] Add semantic verification to genuine-run finalization, and update the verification notebook/qualification workflow to provide fixture trust explicitly without treating it as clinical identity.
- [x] Run focused adversarial tests, real Gaussian/count runs, and the synthetic suite; rerun the saved negative controls against the new CLI without mutating their evidence.
- [x] Document CLI compatibility, historical-time semantics and remaining trust/clinical limits. Preserve new logs and receipts separately from 0.4.2 audit evidence.

The user has authorized continuing implementation toward the audited goal. No additional design confirmation is needed for this concrete remediation. No fabricated deployment defaults, human approvals or assay limits are introduced. Statistical remediation proceeds as a separate research lane and is not hidden inside an audit-verifier change.

Implementation includes devharness_controlled/result_schema.py. Evidence: 709 full-suite passes, 48 verifier tests, direct old-CLI counterexamples and independently generated malformed-result/approval-time histories now rejected. Broad clinical-production qualification remains open under docs/clinical-production-goal.md.
