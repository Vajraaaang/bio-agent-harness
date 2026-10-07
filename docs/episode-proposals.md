# Research proposal and review records

An already authored episode specification can now be prepared, reviewed and started through a recorded review path. This reuses `AnalysisEpisode` and the existing execution DAG. It does not interpret arbitrary questions, determine scientific appropriateness automatically, authenticate the reviewer or implement clinical approval.

The native/controlled Ed25519 approval verifier is unchanged. Its supported plan types and externally configured signer registry are not weakened to accommodate these research records.

## What is bound

`prepare_proposal(output, spec, allow_data_read=True)` validates the existing specification through `normalize_episode_configuration`. It records the question, assay, declared task/feature universe, input kind, fixed tool options, episode resource limits, execution tier, source fingerprint and SHA-256 identities of the three input bindings. Inputs are hashed without fitting or transformation. Original data paths are not copied during preparation; execution later creates the existing immutable episode snapshot and checks it against the reviewed identities.

The source and data must still match when an approval is recorded and when execution starts. Changes require a newly prepared proposal and review. Output roots must be disjoint from inputs and from prior proof roots; preparation cannot write into a supplied Zarr directory. A proposal's supported-workflow flag describes implementation availability, not method qualification or experimental provenance.

`record_review(...)` records `approve`, `reject` or `needs_information`, a bounded reviewer label/rationale and explicit confirmations. Approval requires confirmations for the question/contrast, input provenance, biological units/design and limitations. These are **caller assertions**, not software findings that those experimental facts are true. Unsupported episode routes cannot receive an execution approval through this interface.

The record binds the exact proposal root and contents. It explicitly grants neither data execution nor model execution. It creates no signing keys and supplies no authenticated identity, dual-review policy, revocation service or clinical release authority. The coordinator must select an appropriate current review; the record format is not a global “latest approval” registry.

## Preparing and recording a decision

Use the same scientific fields as the episode specification. In this API, the three `inputs` paths must be canonical and absolute. The `agent_limits` CLI extension is outside this scientific review record.

```python
from pathlib import Path
from devbench.analysis.episode_proposals import prepare_proposal, record_review

# spec is an explicitly authored configuration, not an inferred scientific plan.
proposal = prepare_proposal(
    Path("/absolute/path/to/new-proposal"), spec, allow_data_read=True
)

# Record only the decision actually made by the responsible operator.
review = record_review(
    Path("/absolute/path/to/new-review"),
    proposal["manifest_path"], expected_root=proposal["root_hash"],
    reviewer="OPERATOR_LABEL", decision="approve",
    rationale="REPLACE WITH THE ACTUAL REVIEW RATIONALE",
    confirmations={
        "question_and_contrast": True,
        "input_provenance": True,
        "biological_units_and_design": True,
        "limitations": True,
    },
    allow_review=True,
)
```

Retain both roots separately. Do not use an agent's self-approval as an operator decision. If the facts are missing, record `needs_information` or `reject`; the record does not invent an answer or update experimental metadata.

## Starting through the existing CLI

```sh
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv/bin/python -m devbench.analysis.episode_cli \
  --proposal /absolute/path/to/new-proposal/audit/manifest.json \
  --proposal-root RETAINED_PROPOSAL_ROOT \
  --review /absolute/path/to/new-review/audit/manifest.json \
  --review-root RETAINED_REVIEW_ROOT \
  --actions /absolute/path/to/reviewed-actions.json \
  --output /absolute/path/to/new-episode \
  --allow-data-execution
```

This path excludes replacement `--spec`, replay and handoff inputs. `--codex-model` may replace reviewed actions only with the existing separate model permission, actor workspace and preflight requirements. It uses the existing default agent limits; this increment does not authorize a live call or override a budget. Direct `--spec` remains the trusted coordinator entry point and reports `review_status: not_recorded`; its label does not imply a recorded approval.

`begin_reviewed_episode(...)` provides the corresponding coordinator API. It verifies both retained roots, approval, source and original input hashes before creating the episode. It then compares the actual copied snapshot/configuration with the proposal and embeds only the verified metadata proof. Unsealed sibling files and convenience receipt files are not imported as reviewed evidence. A copy or binding failure preserves a failed episode and executes no scientific action.

## Continuity and verification

`review-binding.json` and `plan-review/` preserve the review proof in the episode. The binding is registered in its DAG and checked with the frozen inputs before actions. Review records and scientific settings are outside the actor's action schema. A changed review binding cannot silently become an accepted run.

Clarification handoff retains the binding. Reset and replay require fresh data-execution permission, use disjoint workspaces and preserve the exact original review binding. They capture its expected hash before creating a clone; a disappearing or replaced binding fails before actions. Replay can use the episode snapshot after original source paths are removed. These controls do not renew model permission or clinical authority.

`verify_episode` checks artifact integrity and exposes the committed `review_binding_sha256`. `verify_bound_review(root, expected_binding_sha256, configuration, snapshot)` separately verifies the embedded approval association and its match to that episode. A failure bundle can remain integrity-verifiable even when its review proof was damaged; a valid outer hash is not a valid review decision.

Review records supplement the [receipt-bound numerical report](episode-synthesis.md). Scientific correctness still requires separate evaluation. Semantic question-to-plan authoring, operation-specific admission criteria and an independent scientific reviewer remain incomplete; see the [planning research prompt](../reports/product-research/2026-09-21/planning-admission-research-prompt.md).
