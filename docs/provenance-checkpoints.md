# Checkpointing an unfinished execution ledger

`ExecutionDAG.checkpoint(destination, max_bytes=..., max_entries=...)` writes and seals a separate bounded copy of an in-progress ledger. It then suspends that writer. The completed copy includes its manifest and root receipt within the declared byte and entry limits. Transient atomic-publication directory entries are not retained payload.

`ExecutionDAG.resume(working_root, checkpoint_manifest, expected_root=...)` requires a separately retained checkpoint root. It verifies the checkpoint, the original identity and node prefix, the working inventories and referenced artifact bytes. It rejects finalized stores, changed nodes/artifacts and unexpected post-checkpoint nodes. The finished copy is never reopened for writing.

Before returning a new writer, resume exclusively creates a claim beside the working ledger. It records that claim and the checkpoint manifest in the continuing DAG. The ledger identity and earlier nodes remain unchanged; later nodes extend the same execution. Older checkpoint receipts remain verifiable after the working ledger finishes.

Claims serialize cooperating coordinators using one canonical working path. They are not a distributed lock, authorization signature or defense against a host owner. A failed or stale claim must not be automatically discarded just because its process is no longer running. Root hashes authenticate neither the operator nor the origin of a wholly replaced store; their integrity value depends on retaining the expected root separately.

This is a provenance primitive. It does not restore a scientific broker, authorize another model session, refresh resource budgets or complete the episode's user-facing clarification workflow. Those remain responsibilities of the episode coordinator and the explicitly authorized caller.

Verification evidence is recorded in `reports/clarification-handoff/2026-09-21/`. The separate coding-agent review found that the initial copy limit excluded sealing metadata; the completed-size limits were corrected and both rejection and exact-boundary acceptance were tested.
