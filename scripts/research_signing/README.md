# Existing research signer bridge preparation

The new library is `devbench.analysis.rna_grant_signing`. There is no live signing
CLI. `stage_authority_bridge.py` writes only a reviewable copy and patch; it does
not alter the canonical authority or touch Keychain.

A trusted coordinator must separately inject the exact approved canonical
unsigned-grant SHA-256 and deliberately accepted existing public SPKI fingerprint.
Neither may be read from participant/model output. Existing HOLD bootstrap and
proposal artifacts remain historical public material, not enrollment or grants.
The existing proposal's synthetic scope is not widened; the new exact v3 subject
path explicitly restricts the public/synthetic purpose being deliberately signed.
The local agent-provisioned key has no institutional or human-signature claim.

The staged authority preflight runs the existing actual native backend/runtime,
source, allocation, roles and expiry checks before any key access, plus fresh
scientific custody/readiness and durable consumed-state checks. A protected
BEGIN IMMEDIATE transaction remains held through the signature and post-checks.
No row is inserted, grant consumed or dispatch admitted by signing. Existing
consume/reserve-dispatch controls remain the only downstream one-use authority.

Scientific readiness uses an honestly unsigned copy with only the selected
approval absent. Its trial binding must stay identical. Only the exact single
missing-approval marker is tolerated after the actual execution qualification
verifies the original pending execution subject. Other design/data/publication/
assessment/workflow blockers fail closed. Generation and numerical grants remain
separate; generation's allocation excludes numerical authority.

The private adapter is fixed to the existing exact generic-password class,
service, account and current default Keychain search list. It is read-only,
noninteractive and fails closed on missing/locked/denied/malformed results. It
never creates, updates, deletes, unlocks, changes ACLs or exports a private file.
Only a 32-byte in-memory seed enters Ed25519 and its derived public key must match
both explicitly accepted and currently configured roots before signing. Native
failures are reduced to a fixed public error. The mutable seed buffer is cleared
on success and failure; Python, cryptography and CFData copies have no guaranteed
erasure. This is not Secure Enclave/non-exportable key protection.

Before any live invocation, the parent must review and integrate the staged
patch, verify the changed ordinary installed artifact and release a bounded
one-attempt dedicated coordinator process with discarded native pipes and no
secret logs/core dumps. A generic CLI taking a payload and self-selected digest
would not satisfy the trusted coordinator boundary. No such CLI is supplied.
There is no automatic issuer enrollment, root default, fallback key, actual grant,
model request, study execution or retry in this preparation.

Tests use synthetic keys and fake native adapters only. They verify protocol,
rejection, current-subject checks, lock/rollback, no consumption, closed fields,
privacy and legacy compatibility; they do not establish native Keychain success,
accepted public scientific qualification or valid live approvals.
