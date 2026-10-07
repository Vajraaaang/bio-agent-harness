# Continuing after an information request

An episode can now stop for clarification, exit its CLI process and continue later from a verified handoff. This is implemented for the existing bounded bulk-count, bulk-continuous and scRNA-count episode routes. It does not extend assay support or complete the separate large-H5AD/e11 workflow.

When the driver selects `request_information`, the CLI exits with code **5** and returns the question plus a `handoff` receipt. Keep its `root_hash` separately. The original writer is suspended, scientific broker receipts are closed, and a separate audit checkpoint preserves the working prefix. No model waits in the background.

## Reply and continue

Write a bounded JSON reply:

```json
{
  "message": "Continue with the existing design and stated interpretation.",
  "preserves_frozen_inputs_and_settings": true
}
```

The reply is an operator annotation, not experimental verification. It cannot change inputs, assay, measurement scale, biological units, contrast, feature universe, scientific settings or grading policy. Corrections to those require a newly reviewed snapshot/episode. Do not put protected evaluation answers or held-out grading feedback into the reply. Human-assisted episodes are not evidence of autonomous agent performance without a prespecified matched study.

For an originally reviewed-action episode, continue with another bounded action file:

```sh
python -m devbench.analysis.episode_cli \
  --resume /absolute/episode/handoffs/0001/audit/manifest.json \
  --expected-root RETAINED_HANDOFF_ROOT \
  --response /absolute/response.json \
  --actions /absolute/continuation-actions.json \
  --output /absolute/episode \
  --allow-data-execution
```

For an originally Codex-driven episode, use the same requested model and explicitly authorize model execution again:

```sh
python -m devbench.analysis.episode_cli \
  --resume /absolute/episode/handoffs/0001/audit/manifest.json \
  --expected-root RETAINED_HANDOFF_ROOT \
  --response /absolute/response.json \
  --codex-model SAME_REQUESTED_MODEL \
  --agent-workspaces /absolute/short/actor-path \
  --output /absolute/episode \
  --allow-data-execution --allow-model-execution
```

The Codex command is an available, gated path, **not a live run performed during this implementation**. Its saved memory, reasoning and executable settings are restored; it cannot change into a manual run through this CLI. Actor workspace location remains an explicit scratch-location choice. Each subsequent live selection still requires the existing fresh permission/model preflight.

`--resume` excludes replacement `--spec` and `--replay`. The output directory is the existing episode, not a new empty directory. A reviewed action batch may end in `request_information`; the first request stops execution instead of silently running subsequent actions as a reply.

## What remains fixed

- Input snapshot, source identity, Python/package/platform compatibility and episode identity are checked before acquiring a single-use resume claim.
- The complete working-file inventory and prior broker receipt chain are verified. Previously successful diagnostics are restored from receipts, not recomputed merely to rebuild state.
- A fresh broker evidence generation contains the verified prefix. The old generation remains unchanged; failed events and call counts are retained. Prefix-copy bytes count toward the same cumulative evidence ceiling.
- Original active wall deadlines remain in force. Human waiting and restoration consume their remaining time. A clock that had not yet started stays unstarted; restoration does not create a new allowance. Expiry produces an explicit infrastructure failure before further model or numerical work. A reversed wall clock is rejected.
- Replies consume the existing revision allowance. Actions, selector attempts and source configuration do not restart. A second handoff preserves the earlier checkpoint too.
- Frozen evaluation registration remains attached to the same episode. Scores and private evaluator state do not enter the selector's observation/history.

Reset or abort of a pending episode uses verified termination so the checkpoint remains intact. There is no automatic clearing of a stale claim. Claims coordinate one canonical local working path; they are not distributed locks or authenticated approvals.

## Replay and evidence

Operator messages are recorded separately in `clarifications.json`, at their positions in the action journal. Numerical replay applies these recorded events without querying an operator or model, then re-executes the scientific actions. It does not reproduce external delays or make model inference deterministic.

The local verification report is `reports/clarification-integration/2026-09-21/README.md`. It distinguishes protocol fixtures, real tiny numerical calculations, separate CLI process continuation and protected synthetic-reference evaluation from live agents, biological qualification and clinical readiness.
