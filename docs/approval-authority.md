# Governed approval authority candidate

Version 0.4.7 binds native analyses to an explicitly supplied, administrator-signed study policy. It supports synthetic/public verification only. It does not establish an authenticated human identity, a qualified electronic-signature ceremony, trusted clock, protected retention, or clinical release. Restricted clinical admission and all clinical-release flags remain disabled.

## Contracts and trust

`controlled_native_analysis/3` is the governed native base plan; `/4` adds the prespecified DESeq2 endpoint contrast. Both include `authority_id` and the exact current `authority_tip_sha256` in the plan signed by the author and reviewer. Their numerical requests remain the existing base/effect requests. Historical `/1` and `/2` plans keep their original interpretation and cannot silently acquire governance by adding CLI flags.

An `approval_authority_policy/1` envelope contains study-scoped grants, validity intervals, a revision and predecessor record SHA-256. Administrator trust is independently supplied, public-key only, and bound to both the authority identifier and the newly generated registry UUID. Analysis and administrator public keys cannot alias. Analysis authorization requires exactly one author and one reviewer with distinct subjects and keys. These subjects are configured labels, not verified people.

The Ed25519 admission path rejects weak, noncanonical and mixed-order public points using PyNaCl/libsodium before cryptography verifies signatures. JSON contracts reject duplicate/nonfinite/unknown fields and oversized input. Neither a copied public registry nor a valid content hash establishes an external trust root.

## Policy history and dispatch

The private SQLite store has exact schema checks, bounded history, append-only API/triggers, inode checks and cooperating-process locks. Publications require the expected current tip; an empty grant list revokes all study authority. A checkpoint can prove that a retained prefix is present. It does not prove that an independently unknown newer policy never existed. Owner-level replacement or rollback of the entire local store is outside the protection established here.

Admission preserves the policy history, public administrator configuration and externally supplied checkpoint in the execution DAG. Immediately before dispatch, the executor checks the current store again and holds its shared policy lock until the daemon's actual container start is observed. A concurrent signed policy publication cannot pass the exclusive writer lock during that interval. Publication before lock acquisition makes a stale plan fail; it does not fall back to the old snapshot.

Approval and grant validity are checked after durable start callbacks immediately before dispatch, and again at the recorded daemon start. The independent verifier recomputes those checks, including affirmative approval claims in failed transports. Delayed starts with expired authority cannot become verified completed analyses. Existing strict cross-domain chronology checks remain: no clock-skew tolerance was added. There is no general claim of preventing an already dispatched process from briefly running before a late daemon observation fails; the failure is retained and owned-container cleanup is attempted.

Recovery inventories the three authority files under the existing bounded no-follow rules. It can adopt a verified original terminal result without a second numerical execution. It cannot create missing original evidence, infer successful analysis from an incomplete record or authorize a retry. External administrator roots remain necessary to verify governed originals.

## CLI operations

`devharness-controlled authority-init --registry ABSOLUTE_NEW_DIR --authority-id ID` creates an empty store with no implicit grants. Construct and sign a policy using the separately managed administrator key and the returned registry UUID; this CLI never takes private keys.

`authority-publish --registry DIR --policy SIGNED_POLICY_JSON --administrators PUBLIC_ROOTS_JSON --expected-tip SHA256 --output NEW_CHECKPOINT_JSON` appends a signed successor. Omit `--expected-tip` only for genesis. All export targets must be distinct, canonical paths outside the live store, with existing parents and no existing output files. Directory descriptors are pinned and writes are exclusive/no-follow.

If a publication was committed but its checkpoint export failed, the CLI exits nonzero with `status: published_export_failed` and `published_checkpoint`. `publication_may_have_committed` is true once publication was attempted, including uncertain database/durability failures. Inspect/export the current store before deciding whether a new publication is necessary; do not blindly retry or rewrite history.

`authority-export --registry DIR --administrators PUBLIC_ROOTS_JSON --output NEW_SNAPSHOT_JSON --checkpoint-output NEW_CHECKPOINT_JSON` verifies then exports the history and root. Retain a checkpoint independently. The two exported files are separate writes; a later export failure can leave an earlier output, which must be retained/inspected rather than overwritten.

`authority-verify --snapshot SNAPSHOT_JSON --administrators EXTERNAL_PUBLIC_ROOTS_JSON --checkpoint RETAINED_CHECKPOINT_JSON` verifies history against explicit roots and an optional external checkpoint. This read-only operation does not grant a new execution authorization.

`execute-native` and `submit-native` take `--authority-store`, `--authority-administrators` and `--authority-checkpoint` for governed plans. `verify` and `recover-native` take `--authority-administrators` to verify their retained history. Idempotent duplicate submissions return the existing attempt and do not execute a new analysis.

## Evidence and limits

The independent review and failure reproductions are retained in `results/approval-authority-2026-09-07/integration-review.md`. Focused tests cover signature forgery, registry aliasing, extra SQLite indexes/views, stale policy, expiry, export contamination, publication uncertainty and governed recovery. Fault injection in those tests is infrastructure evidence, not a substitute for the actual native DESeq2/edgeR checks under the same results directory.

The frozen precision study in `results/native-precision-2026-09-07/` is a separate 0.4.6 measurement. Its higher-dispersion interval setting triggered an undercoverage warning. Neither new authority controls nor a valid audit root repairs that statistical finding. The original study and failures remain unchanged.

Provider enrollment, identity proofing, authentication/step-up, key custody/revocation operations, controlled user administration, human-readable signature manifestations, protected retention/restore, deployment qualification and assay/context-of-use validation remain required work. See [identity design](identity-signature-design.md) and [clinical production goal](clinical-production-goal.md).
