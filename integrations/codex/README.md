# DevHarness agent plugin

DevHarness 0.3.0 connects a local client to the workspace daemon through the
`devbench mcp` STDIO shim. The configured plugin contains eight research skills
and exposes 13 control-plane tools.

| Task | Tools |
| --- | --- |
| Discover runtime, inspect data and register inputs | `inspect_runtime_capabilities`, `inspect_inputs`, `register_investigation_input` |
| Assess the study design and available methods | `check_answerability`, `list_kernels_and_methods` |
| Manage aims, revisit investigations and inspect bounded tables | `manage_research_aim`, `list_investigation_state`, `query_artifact_table` |
| Freeze a plan and submit a reviewed run | `freeze_analysis_plan`, `submit_reviewed_run` |
| Follow a run, cancel it or read the receipt | `get_run_status`, `cancel_run`, `get_run_receipt` |

## Configure a local copy

From the repository root, synchronize the locked environment. The pinned
uv-managed Python supplies patched stdlib SQLite for scientific WAL. CI uses
uv 0.13.10, which can install the pinned Python 3.13.18 build on a fresh host.

```sh
uv python install
uv sync --locked --extra dev --python-preference only-managed
.venv/bin/python integrations/codex/devharness/scripts/configure.py \
  --python /absolute/path/to/.venv/bin/python \
  --input-root /absolute/path/to/read-only-inputs \
  --workspace /absolute/path/to/investigation-workspace \
  --output /absolute/path/to/marketplace/devharness \
  --write-marketplace /absolute/path/to/marketplace
```

Choose existing, disjoint input and workspace directories and a fresh plugin
destination. The source template has empty MCP server maps. Configuration binds
the local interpreter and packages one copy of each canonical skill from
`.agents/skills/`. The daemon starts or reattaches on client connection.

Run the packaging and real STDIO checks with
`.venv/bin/python -m pytest -q integrations/codex/tests/test_plugin.py`.
See the [installation and client mirroring guide](devharness/README.md) for
the skill names, marketplace installation and optional bindings.
