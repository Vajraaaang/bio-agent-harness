# Approval authority lifecycle implementation plan

> Execution: inline in the existing authorized clinical-platform work. Existing measured archives remain unchanged. The repository has no committed Git base; use separate frozen source copies for measurement instead of inventing a worktree base.

**Goal:** Bind new governed native plans to an explicit, administrator-signed study policy and prevent dispatch after that policy changes, expires or ceases to authorize the signing keys.

**Architecture:** Preserve legacy research plan semantics. Add governed plan versions for base and endpoint native analyses, an append-only signed policy history, and a shared launch guard that prevents cooperating policy writers from racing the final authorization check and process dispatch. The existing Ed25519 approvals sign the entire governed plan digest. This is authorization lifecycle software; it does not authenticate a human or enable clinical admission.

**Tech stack:** Existing Python/Pydantic/cryptography, private local SQLite records and flock, existing native executor and audit verifier. No identity-provider account or deployment is configured by inference.

## Decisions and scope

1. Continue configured-key files alone: retains no policy lifecycle and does not close the discovered dispatch gap.
2. Bind signed policies to plans and enforce a current-policy launch guard: selected because it closes the existing authorization gap while preserving numerical procedures.
3. Build a hosted OIDC/signing service immediately: requires the provider, enrollment and deployment policy requested from the owner. Primary-source requirements are documented, but those organizational choices remain unsupplied. Do not represent fixture authentication as production identity proof.

A policy change conservatively invalidates every queued plan bound to the previous tip. There is no silent grant migration or fallback to an older unexpired policy. Administrator trust is supplied independently; it is never taken from the policy's own claimed key. Administrative signing keys must not alias analysis-approval keys. Revocation after dispatch does not retroactively change a historical execution record. Local owner/host replacement remains outside this software control; independently retained checkpoints and qualified administration remain required.

## Tasks and verification

- [x] Define strict signed policy/administrator contracts and governed plan versions. Fail tests for wrong signer, role/study/key mismatch, key alias, expired/future policy, unknown fields and changed signed bindings before implementation.
- [x] Implement bounded append-only policy storage. Require a linear signed sequence, predecessor hashes, private canonical paths and exact schema; reject rollback through the API, ambiguous state and unsafe filesystem objects. Exercise parallel writers and externally supplied checkpoints.
- [x] Integrate policy snapshots and final launch authorization with native/operated dispatch. Tests must demonstrate refusal before native start when policy changes after container creation and serialization of revocation versus dispatch.
- [x] Verify governed histories with independently supplied administrator public keys. Reject missing trust or forged authority evidence even when the content hash chain is internally consistent. Retain legacy audit interpretation.
- [x] Add CLI admission/verification options, real synthetic governed execution/refusal checks, appropriate regression tests, package evidence and a source-grounded report. Preserve unsuccessful checks.

## Independent statistical work

Run4,000 fresh synthetic cohorts in an isolated frozen0.4.6 environment, using the same two settings, numerical method and prespecified warning rule as the200-cohort diagnostic. Use2,000 cohorts per setting and fresh seed2026091219. Extend only synthetic approval lifetime to24hours for the scheduled batch. Optimize integrity checking to verify all frozen files at stage boundaries and source plus each current cohort before/after its attempt. Do not change the fitting/grading method, retry failed cohorts, tune against results or conflate this generic diagnostic with intended-use clinical qualification.

Exactly one implementation step remains active at a time; the frozen numerical process is monitored by its actual execution handle. This plan addresses part of the unchanged full clinical-production goal.


## Verification outcome, 8 September 2026

This bounded authority implementation and review workstream is complete: 1,484 local tests, 279 independent focused checks, all six reproduced integration findings closed, six actual native analyses completed across source and wheel checks, two signed-revocation refusals, duplicate suppression and actual governed recovery. Eight native semantic records and one separate recovery hash receipt verified. The 39 controlled runtime/driver/lock files match the wheel, source archive and both rebuilt notebook bundles. Version 0.4.7 has not been executed in Colab.

The separate frozen precision study completed with 3,990/4,000 accepted results, ten preserved temporal failures and a prespecified undercoverage warning at n48/dispersion0.8 (1,868/2,000 =93.4%). Independent audits and two actual numerical replays passed. No statistical method or old result was changed. See results/approval-authority-2026-09-07 and results/native-precision-2026-09-07/postcheck.

The broader clinical-production objective remains open. Next active work is prospective statistical diagnosis and qualification planning with the still-unsupplied clinical context, provider and deployment requirements. New authority controls are not a qualified identity service or release authorization.
