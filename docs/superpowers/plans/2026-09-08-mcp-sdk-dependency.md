# MCP SDK Dependency Implementation Plan

**Goal:** Remove the unused standalone FastMCP/cache dependency tree while preserving strict, structured stdio tool behavior.

**Architecture:** Expose `ToolDispatcher` through public official SDK `Server` handlers. Keep the existing synchronous execution and return a `(content, structured)` pair so the SDK validates the output schema. Pin the supported SDK major version and retain the preceding environment/evidence.

**Tech stack:** Python 3.12 locally; `mcp>=1.29.1,<2`, existing Pytest, UV lock, ten existing dispatcher tools. Source edits are coordinated inline; an independent reviewer checks the design and final patch.

## 1. Preserve the baseline and reproduce protocol weaknesses

- [x] Seal and separately verify all terminal reference-coverage/security evidence against its retained digest. Do not edit either sealed result root.
- [x] Add `tests/test_mcp_protocol.py` with real stdio tests. Require all ten input schemas to reject additional properties, object output schemas, matching text/structured monotonicity results, explicit invalid-argument errors, workspace path refusals and a successful tool after refusals.
- [x] Run `./.venv/bin/python -m pytest tests/test_mcp_protocol.py -q` before changing the server. Preserve the failure log in `results/mcp-sdk-migration-2026-09-08/`.

## 2. Replace only the transport boundary and dependencies

- [x] In `devharness_server/server.py`, import `Server`, `stdio_server`, `Tool` and `TextContent` from the official SDK. Register an async list handler using the dispatcher's schemas with `additionalProperties=False` and `outputSchema={"type":"object","additionalProperties":True}`.
- [x] Register the call handler with `validate_input=False` so the dispatcher owns raw validation and failed-event capture. Execute `encoded = dispatcher.execute(name, arguments)`, then return `([TextContent(type="text", text=encoded)], json.loads(encoded))`. Preserve dispatcher exceptions for the SDK's explicit error result; do not return a successful empty object on failure.
- [x] Keep `create_server()` free of loop startup. In `main()`, run a local coroutine which opens `stdio_server()` and awaits `server.run(reader, writer, server.create_initialization_options())` using `asyncio.run`.
- [x] Change `pyproject.toml` to version 0.4.9, replace `fastmcp>=2,<3` with `mcp>=1.29.1,<2`. Preserve all statistical dependency constraints. Resolve `uv.lock` without unrelated upgrade flags.
- [x] Adapt the existing in-memory discovery test in `tests/test_tools.py` to the SDK's supported `create_connected_server_and_client_session` helper and `structuredContent` result. Keep real stdio and numerical tests.

## 3. Verify the isolated revision and package

- [x] Create a fresh environment under the new result directory using the new lock and `[dev]` extra. Never sync or prune the old `.venv`. Record interpreter/package identities and the package-set difference from the old lock; assert standalone `fastmcp` and `diskcache` are absent.
- [x] Run focused protocol/tool tests, then the full synthetic suite once on the final implementation. Retain red logs and failures; repair only demonstrated issues before retesting relevant scopes.
- [x] Build the wheel/source archive. Run the installed package's real stdio CLI from an unrelated workspace and check dependency consistency. Preserve the exact schema comparison; title-only annotation differences are not exact equality.
- [x] Update the controlled notebook version, include the focused MCP regression file in the allowlisted source bundle/test cell, and regenerate both notebooks. Compare embedded source hashes to the packaged/source files. Do not claim a new Colab execution from generation.
- [x] Obtain independent review of the final patch and test evidence. Fix confirmed defects and retain findings.
- [x] Query OSV with only public package names/versions from the new environment. Report remaining matches and scan limits. Retain the environment and exact output artifacts.

## 4. Publish an evidence checkpoint

- [x] Update current README, clinical goal and progress with measured results and the environment command. Preserve historical numerical/Colab statements and the preceding PDF.
- [x] Record commands, package/source digests, tests, local protocol outcomes and scan results under the new result root; seal terminal evidence. All clinical-release and compliance flags stay false.

This plan is an authorized maintenance step in the existing clinical-readiness work. There is no Git HEAD or user-supplied production deployment/identity/context of use; no commit, rollout, clinical release or model participant benchmark is implied.

## Completion record

Implemented and checked as recorded in results/mcp-sdk-migration-2026-09-08/report.md. Final evidence seal is the last publication step. The subsequent user-authorized Colab CLI work is separate: three created CPU sessions, zero verified notebook executions, all stopped, with original failures retained. Notebook generation and local tests do not establish a cloud run. The one-line CI Python 3.10-to-3.11 correction was independently reviewed; hosted CI was not run.
