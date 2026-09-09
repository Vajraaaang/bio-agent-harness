# Mac v0.3 verification

Recorded 2026-09-07T08:27:43.246931+00:00 on native Darwin arm64, Python 3.12.1. This record covers local software checks. It does not demonstrate model improvement, clinical validation, or regulatory compliance.

| Check | Observed outcome |
|---|---|
| `make install` | Locked uv installation succeeded; native arm64 interpreter |
| Final `make test` | **429 passed**, 2 upstream Authlib deprecation warnings; **122.41 seconds** in pytest |
| Spawn/thread verification | Real spawned NumPy BLAS pools bounded to one thread; explicit overrides preserved |
| Final `make demo` | **3/3 offline oracle controls passed; 1.61 seconds** wall time |
| Demo audit CLI | All 3 manifests valid and anchored to saved receipts |
| Real local subset | **200 genes × 60 samples**; 1,002,556 bytes read; zero downloaded; 294,915 data bytes persisted (sidecar excluded) |
| Real-data integrity | Selected IDs, first-row numeric values and consumed-prefix SHA-256 checked against cached source |
| Offline fallback | Explicit synthetic 200 × 12 subset; zero source bytes read/downloaded; reason recorded |
| Synthetic CLI | CSV, Zarr and HDF5 formats plus metadata generated |
| Browser check | Desktop 1280 × 720; filters, search/reset, expanded DAG/gates, RSS and unavailable-artifact labels checked |
| Browser report boundaries | Oracle controls separated from non-control grades; exact-session supplemental model labels; no external assets or console warnings/errors observed |
| Lockfile | 149-package lock check passed |
| Packaging | Wheel and source archive built; report entry point, 58 expanded definitions, guidance and code license included; cached data/results excluded |
| CI configuration | Four jobs: macOS arm64 / Ubuntu x86_64 × Python 3.10 / 3.12; YAML parsed and checks inspected |
| Hosted CI | **Not run**; no Git remote configured |
| Live model runs during Mac revision | **Zero**; `make sweep` inspected using `make -n` only |

An initial full suite passed 421 tests before the browser-discovered oracle-label fix. The final 429-test run includes that correction. Installation and full-suite timing have no 30-second guarantee, and hosted CI has no measured three-minute bound. RSS is sampled process-tree resident memory, with shared-page double-counting and between-poll gaps; whole-machine memory pressure is not measured.

## Files and commands

Primary evidence is under `results/mac-local/`: `commands.json`, `install.log`, `final-tests.log`, `final-demo.log`, `ingest-and-seal-validation.json`, `final-demo-audit-verification.json`, `browser-verification.json`, `ci-config-validation.json`, `package-validation.json`, `runtime.json`, `pilot-report.html`, and `demo-report.html`.

The final demo raw results are `results/demo.JzzVdV/runs.json`. Its oracle has privileged simulation answers. Passing controls establish staging/schema/grader/audit behavior, not biological reasoning. Both HTML reports are self-contained; their local manifest/node checks are narrower than full artifact verification through `devbench-audit verify`.

Package outputs are under `results/mac-local/dist/`. Dependencies are locked with uv; pip fallback is explicitly unlocked. Mac thread defaults apply before spawn and, through Makefile/.env.example, before CLI startup. Existing parent BLAS pools are not retroactively reconfigured. Local Python 3.10 v0.3 and hosted Linux execution were not rerun; the CI matrix configures those checks without claiming a result.

## Sealed live pilot: separate evidence

The earlier `results/codex-live.gj3jlH/` remains sealed. All nine saved seal hashes, including the frozen v0.2 source/data archive, were rechecked after the Mac edits and matched. Measurement preserved all 186 protected files while it ran.

The original preflight checked Codex CLI 0.147.0, installed exec/resume flags and ChatGPT login without exposing credentials. Its original test invocation passed 370 tests in 122.53 seconds. Eight separate live sessions recorded **gpt-5.6-sol** in their exact session contexts: four baseline and four assisted. All eight timed out at the configured 300-second total deadline, with 24 phase attempts, 16 validated intermediate responses, **zero accepted final answers and zero completed final grades**. Recorded zero scores are timeout bookkeeping.

Assisted sessions attempted 21 MCP calls; every call returned `user cancelled MCP tool call`. The cancellation origin remains unresolved. Missing packages in participant-selected interpreters and CLI model-cache decoding warnings were also observed. The new Mac setup has not been tested in another live pilot; it does not establish that these failures are fixed.

All eight pilot manifests verified against saved receipts before sealing. Baseline histories had no eligible numerical calls. Assisted numerical replay executed 21 calls (18 local successes, 3 local failures); all four histories mismatched their original transport-cancellation outcomes. Replay invoked no LLM and does not demonstrate numerical nondeterminism.

| Condition | Live attempts / accepted finals | Recorded score per run | Partial cumulative session tokens | Peak runner RSS, decimal MB |
|---|---:|---:|---:|---:|
| Baseline | 4 / 0 | 0.0, ungraded | 2,048,408 | 682.5 |
| Assisted | 4 / 0 | 0.0, ungraded | 3,137,138 | 965.1 |

Complete token totals, subscription charges and unrecorded memory trajectories are **unknown**. See the sealed pilot's `inspection.md`, `commands.json`, `session-observations.json`, `audit-verification.json` and replay records for exact commands, per-run values and failures. The pilot used only authorized synthetic inputs through the existing OpenAI Codex login. No mock answer replaced a live result.
