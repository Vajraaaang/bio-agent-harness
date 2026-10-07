# Receipt-bound episode reports

The initial bulk condition and donor-level scRNA state routes write `synthesis.json` and `summary.md` when an episode terminates. Both are included in its artifact inventory and execution DAG. A numerical result is reported from its saved, verified tool receipt. The agent may select observations to highlight; it cannot supply their effect sizes, intervals, p-values, q-values, units or comparison direction.

This is deterministic reporting with consistency verification. It is not an independent scientific reviewer, free-form biological interpretation, experimental validation or proof that the harness improves an agent.

## Selecting observations

Plain `{"kind":"finish"}` remains valid. It reports target scope, computation metadata, attempted/testing-family counts, limitations, diagnostics and execution evidence without selecting particular hypotheses.

An agent or reviewed action batch can optionally select up to 20 unique references:

```json
{
  "kind": "finish",
  "findings": [
    {"action": 4, "target_id": "mean_contrast", "hypothesis_id": "g0"}
  ]
}
```

`action` is the one-based episode action position, not the broker's tool-call sequence. Rejected actions, revisions and information requests also occupy positions. Use the `hypothesis_id` returned in the actual tool result; cellular IDs bind gene and type and must not be guessed from a label. The selected action must be the latest applicable computation for that target. Diagnostics, stale computations, unknown hypotheses, extra numerical fields, duplicate references and oversized selections are rejected through the bounded revision flow.

References use action positions so replay can resolve them to fresh receipt IDs. Replaying does not reuse old tool receipts or launch the agent again. Selecting an unavailable hypothesis reports its missing status and reason; it does not create an estimate or turn an incomplete analysis into a completed one.

## Evidence checks

The coordinator closes the active broker generation and reuses `verify_receipts` to check the retained root, input and source identities, event chain, recorded result hashes and capture completeness. A resumed generation already contains the verified prior prefix; generations are not concatenated.

Each successful result must also match a checkpoint and the immutable `episode.observation` record: action position, tool, observation bytes, checkpoint receipt hash, checkpoint stage and tool receipt ID. The checked observation-node IDs come from the coordinator's retained DAG prefix. A file moved into another action's directory is not enough to establish attribution. The terminal manifest binds that prefix, the report and its underlying artifacts.

The report uses the recorded producer's computed contrast metadata. Top-level `contrast` and `units` remain requested wording and are displayed separately. Legacy or other-estimand outputs without computed semantics are not promoted into a verified pairwise claim. No significance threshold or biological mechanism is invented by the report.

All attempted hypotheses remain in the family summary, including failed/unavailable rows. The two initial producers apply BH over that attempted family, retaining internal p=1 positions for unavailable tests without publishing fabricated p/q values. Highlight selection does not define a new testing family. Conditional uncertainty, biological units, donor counts where recorded, aggregation and tool limitations remain visible.

When evidence is incomplete or inconsistent, numerical statements are withheld and acceptance is denied. The bundle retains the report error and original termination context. Scientific refusal, scientific failure and infrastructure failure remain distinct when the evidence supports that distinction. A correctly recorded withheld report can pass report-consistency verification while its `evidence_verified` value remains false.

## Verifying and moving a report

Use the externally retained episode root, not a newly calculated replacement:

```python
from pathlib import Path
from devbench.analysis.episode_synthesis import verify_synthesis

verification = verify_synthesis(
    Path("/absolute/path/to/episode"),
    expected_root="RETAINED_EPISODE_ROOT",
    allow_evidence_read=True,
)
print(verification)
```

Verification first checks the outer episode inventory/DAG, requires the recorded source version, verifies the underlying receipts, reconstructs the JSON report and re-renders the Markdown. Both must match their saved forms. This reads evidence and performs no numerical analysis or model call. Result links are bundle-relative; a completed small-fixture bundle is tested after relocation and numerical replay. The latter still requires explicit data-execution authorization, its recorded source/runtime and a clean destination.

External evaluation remains separate. The episode's `scientific_correctness` is null, and held-out reference feedback does not enter this report or the agent's revision loop. Research retrieval, an independent scientific-reviewer role, large-H5AD/e11 execution, broader assay admission and a matched live-agent comparison remain separate product requirements. No clinical-readiness claim follows from hashes, report verification or the local tests.
