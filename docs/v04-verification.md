# v0.4 and Colab verification

Recorded 2026-09-07T18:28:38.074571+00:00 on macOS arm64, Python 3.12.1.

The notebook and shared runtime are implemented. **The requested live nonzero-completion and under-45-second targets were not achieved.** No result or answer was replaced by a mock. Actual Google Colab execution was not performed here; its own Codex login and runtime connectivity remain prerequisites.

## Software verification

- Full suite in the default restricted Mac sandbox: **494 passed, 2 upstream Authlib deprecation warnings, 281.87 seconds**. See `results/v04-development/final-full-tests.log`.
- Watchdog tests in that sandbox: **67 passed**. A real worker/child/grandchild check confirmed recursive RSS accounting and descendant cleanup.
- SDK plus code-action integration in that sandbox: **29 passed**. Tests execute real local Python scripts through fake CLI transport; they are software controls, not live model performance.
- Colab tests: **23 passed**; source extraction, imports from outside the checkout, all twelve synthetic preparations, thread setup, blockers, exports and subprocess stages were exercised offline.
- The notebook has five compilable code cells and embeds a deterministic allowlisted source ZIP. It excludes credentials, results, data caches and installed environments. Coordinator graders remain outside participant staging.
- The new HTML leaderboard uses flat artifact receipts and does not display legacy CAS/clinical-hold UI.

## Reproduced runtime fixes

The installed CLI rejects `--approve-for-me` together with `--sandbox`. The adapter now uses `--approve-for-me` alone, which selects workspace-write with automatic approval review. The real CLI parser was checked using a nonexistent working directory, preventing any model session. A regression test covers the mutually exclusive flags.

Inside the Mac sandbox, psutil's system-wide process enumeration was denied while owned-process RSS reads succeeded. The watchdog now falls back only on denied Darwin enumeration to Apple's **parent-scoped** `proc_listchildpids`, preserving bounded recursive accounting and termination. Errors, saturated buffers, ancestry races and process-count overflow still fail closed. Monitoring was not disabled and no blanket permission bypass was added. [Apple implementation](https://github.com/apple-oss-distributions/xnu/blob/main/libsyscall/wrappers/libproc/libproc.c)

## Actual model execution

| Snapshot | Launch attempts | Real sessions | Accepted finals | Completed grades | Partial cumulative session tokens | Peak runner RSS MB |
|---|---:|---:|---:|---:|---:|---:|
| Argument validation failure | 4 | 0 | 0 | 0 | N/A: no model session | 405.95 |
| Before scoped Mac monitoring fix | 4 | 4 | 0 | 0 | 781151 | 788.59 |
| After scoped Mac monitoring fix | 4 | 4 | 0 | 0 | 339380 | 964.95 |

The eight real sessions all recorded **gpt-5.6-sol** in their exact session-context records. Every real session used workspace-write with network access disabled for generated commands. There were zero MCP calls and zero MCP cancellations. This does not establish that all native commands are permission-free.

The first four live sessions attempted SDK rank checks that failed at the Mac process-list query. The final four were measured after the scoped monitoring fix and direct signature cheatsheet. Their final errors were still 90-second task deadline interruptions (approximately 88.4–88.6 seconds remained for Codex after staging). No final discovery artifact was accepted or preserved. One final-pilot participant trace records a successful rank check; other full SDK workflows were not established. Workspace traces remain unauthenticated evidence.

The final model-generated script also includes choices requiring correction, such as `data_type="count"` where the API documents `"counts"`, and coefficient assumptions incompatible with selected spline terms. These are preserved participant outputs, not repaired answers. The timeouts do not establish one universal underlying cause.

Partial session token counters include cached input; they are not complete billing totals. Complete usage and subscription charges remain unknown. No usage reset was redeemed. RSS uses sampled process-tree memory in decimal MB and cannot establish a whole-machine memory bound.

## Files, receipts and commands

- `results/code-action-live.kdpx0273/`: four preserved CLI argument failures, zero model sessions.
- `results/code-action-live.heuxgk3x/`: first four actual v0.4 sessions, scripts, traces, observations and frozen source.
- `results/code-action-live.izsuff65/`: final four actual sessions, flat report, scripts/traces and frozen source.
- Each directory has `SEALED.json`; every protected source/config/task file matched its pre-run hash during measurement. Original v0.2 evidence under `results/codex-live.gj3jlH` also remains unchanged.
- Three final export receipts and 24/20 saved script/guidance/trace artifact hashes were verified for the two live cohorts. The primary path has no execution DAG and makes no authenticated-provenance claim.
- `results/v04-development/acceptance-targets.json` explicitly records the unmet completion/time targets; `cohort-summary.json`, `live-inspection.json`, `final-live-inspection.json` and `sealed-integrity-verification.json` retain the supporting observations.

Executed local commands include `make install`, `make test`, focused SDK/watchdog/adapter/Colab pytest checks, `python scripts/build_colab_notebook.py`, the twelve-task `--prepare-only` operation, and three separate four-task launch attempts using `python scripts/run_live_benchmark.py --backend code-action --tasks 4 --timeout-sec 90 --output <fresh-directory>`. The first launch attempt failed at CLI parsing; the other two launched four real model sessions each. Authentication checks used `codex login status`; credentials were not read or copied.

## Using the Colab artifact

Upload `notebooks/colab_runner.ipynb`. It embeds the source, configures thread limits before scientific imports, installs the editable project, prepares twelve synthetic tasks, checks an optional bounded processed subset with explicit fallback, and launches scientific stages in fresh subprocesses. The Python companion is `scripts/colab_run.py` with `# %%` delimiters.

Run `!codex login --device-auth` and complete browser verification yourself when enabled for your account, then check `!codex login status`. No Mac login is transferred. The notebook's measured execution budget is 60 seconds per task, not 60 seconds for all twelve. It exports Markdown, HTML, JSON and SHA-256 receipts under `/content`; authentication or infrastructure failures produce explicit blocked records instead of synthetic model results. The observed Mac pilot does not demonstrate that a Colab pilot will meet its targets.

No clinical-validation, regulatory-compliance or causal harness-improvement claim is supported by these checks. Hosted GitHub CI was configured but not executed.
