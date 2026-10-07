# Codex episode connection implementation plan

**Goal:** Connect a bounded Codex action selector to the existing analysis episode, preserving trusted scientific execution, protected evaluation and failed attempts.

**Architecture:** Codex selects one structured action in a fresh CLI session. The coordinator calls `AnalysisEpisode.step`; no broker endpoint, original matrix, reference answer or evaluator is exposed to the selector. Reuse the existing CLI process supervisor, named permission boundary, standalone probe and exact-surface model preflight. This is the enforced product route, not evidence from a three-condition benchmark.

**Scope and authorization:** The user has authorized this incremental connection and small local fixtures. Implementation and fixture verification can proceed; this work does not authorize a live provider session. No scientific method, threshold, dataset or existing result changes. Keep the existing worktree because its implementation is untracked and a Git worktree would omit it. No commit or push.

## Interface decisions grounded in the existing code map

- Fresh sessions carry explicit public observations and action/diagnostic history. A byte limit rejects oversized context before dispatch; no silent truncation of results.
- Bound selection count and total wall time separately from existing numerical worker caps. Each selection has at most one permission-preflight session plus one action session. Preserve preflight and selection usage separately; missing usage stays unknown.
- A request for information returns control immediately. It does not invent an answer or restart a fresh budget. Metadata changes require a new immutable episode; persisted interactive continuation is not claimed by this increment.
- Syntactically invalid model answers end as scientific/protocol failure. Provider, permission, timeout, capture and identity failures end as infrastructure failure. Rejected supported-action choices retain the existing bounded revision path.
- Save model output outside actor-writable paths. Preserve malformed output, bounded CLI records and launch metadata. Record session evidence before an action can terminate and seal the episode.
- Terminal sealing must include selector artifacts and accounting. Later replay re-executes actions without a model and explicitly does not reproduce model choices. Interrupted selector runs retain a sealed failure where storage permits.

## Steps

1. Add failing fixtures in `tests/test_episode_agent.py` for real small bulk/scRNA tool flows driven by scripted selections, refusal, missing-information stop, budget exhaustion, invalid output and interruption. Add adapter fixtures for authorization, private output preservation and the existing launch configuration. Clearly label every substituted provider event as fixture evidence.
2. Add `devbench/analysis/episode_agent.py` and `episode_codex.py`. Reuse `StudyCodeActionAdapter`, `CodexCLIAdapter`, staging, permission probes and model-identity capture. Add small trusted coordinator methods and optional tighter deadline to `episodes.py`; do not add agent-selectable policy controls.
3. Add reusable output hooks to `CodexCLIAdapter` while retaining defaults for existing adapters. Test protected preflight final-output placement before changing it. Preserve all rejected/failed attempts.
4. Run the focused new fixtures first, then the existing episode, CLI, adapter, permission and preflight suites once against the final source. Record commands, counts, duration, source hashes and limitations under `reports/episode-agent-verification/2026-09-21/`.
5. Update the episode documentation and research handoff to distinguish implemented/offline-tested behavior from the still-unverified live permission and model-selection route. Keep e11 and scientific qualification gaps explicit.

No new biological-method decision is required for this connection. The unresolved R1–R8 qualification questions remain in the research dossier.
