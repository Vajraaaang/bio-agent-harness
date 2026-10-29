# Required RNA node contracts

User approved implementation and verification on 2026-10-22.

Goal: supported RNA workflow entrypoints use the existing LangGraph route and
each node checks entry, permitted execution, retained output, and continuation.
The existing planner, resource admission, model accounting, and method service
retain authority; graph completion is not independent scientific validation.

1. Add regression tests for skipped nodes, forged references, non-ready dispatch,
   invalid initial cursors, durable reviews, invalid outputs, and safe replay.
   Run them before implementation to establish the failures.
2. Enforce exact serial predecessor/operation prefixes and completed ledger
   evidence in WorkflowActions. Validate updates and permitted planner changes
   before recording success. Record review gates after explicit resume.
3. Apply outer-cursor contracts to independent investigation nodes, preserving
   nested serial prerequisites and existing fork/barrier/campaign evidence.
4. Make pinned LangGraph dependencies mandatory and require the controller by
   default. Keep the old installation extra as a compatibility alias.
5. Route RNA CLI, workspace, and scientific-trial planning entrypoints through
   the same graph. Manual stage actions stop at checkpoints and cannot authorize
   additional model calls; generation stops before data execution.
6. Verify node regressions, existing graph/recovery tests, entrypoint integration,
   and the synthetic verification suite; independently review changed boundaries.

Implementation ownership: primary agent owns serial actions/state/controller;
parallel workers own dependency/docs, independent controller, and entrypoint
integration. No supplied inputs, sealed evidence, or historical runs are edited.
