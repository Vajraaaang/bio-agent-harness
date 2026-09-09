# v0.4 runtime plan

Implement the user's code-as-action design through compatible SDK/runner modules. The existing numerical implementations, historical tasks, grades and sealed live pilot stay intact. No blanket approval or sandbox bypass flags are introduced. The official CLI supports workspace-write execution without a TUI, but approval behavior and observed MCP cancellation causality remain separate questions.

- [x] SDK wrappers: bounded reads, rank diagnostics, NB/Gaussian trajectory fitting, compaction, lineage, atomic finite-schema discovery saving and explicitly participant-written traces.
- [x] Staged clean Python environment: third-party numerical dependencies and runtime SDK only; exclude project editable hooks, coordinator packages, tests, task definitions and goldens.
- [x] Code-action adapter: one live session, 90-second deadline, no domain MCP, explicit interpreter/solve.py instruction, real CLI command evidence and artifact preservation.
- [x] Lean runner: local data classification, unchanged input hashes, frozen answer schema and existing deterministic graders; scripts and evidence stored outside participant workspace after execution. No CAS archive on the primary path.
- [x] Versioned curated twelve-task catalog, live pilot and leaderboard scripts, Makefile/README update. Preserve optional ordinary MCP operation without approval suppression.
- [x] Verify SDK, isolation, fake-CLI contracts and actual local script execution; run complete test suite before freezing measurement.
- [x] Launch four separate real Codex participants with synthetic tasks only, using existing ChatGPT login. Preserve all failures. Report observed execution, finals, grades, MCP attempts, times and usage; evaluate under-45s/nonzero-completion targets without forcing them.

Architecture exceptions are deliberate: retain tested legacy modules behind the new public namespace instead of a risky wholesale file move; do not silently drop a fully confounded covariate; workspace trace contents are not authenticated host tool evidence. Native command events plus matching captured script/answer hashes establish the bounded execution observation, not hostile-code proof.

Verification performed; live performance targets remain unmet. See v04-verification.md and acceptance-targets.json.
