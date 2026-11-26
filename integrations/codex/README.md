# DevHarness Codex plugin

The [v0.2.0 plugin template](devharness/) connects Codex to the local `devbench plugin-mcp` server over STDIO. Its seven tools are `inspect_rna_inputs`, `check_answerability`, `plan_rna_workflow`, `execute_reviewed_plan`, `get_run_status`, `get_execution_receipt`, and `manage_research_aim`.

## Configure a local copy

Use a Python 3.13+ environment with the DevHarness backend and MCP installed. Scientific receipts use SQLite write-ahead logging (WAL), which requires SQLite 3.51.3 or later, or a supported patched backport. On macOS, the configuration helper retries the runtime probe with `DYLD_LIBRARY_PATH=/opt/homebrew/opt/sqlite/lib` when Homebrew SQLite is available. If that succeeds, it records the library path in the generated server configuration.

The template ships with an empty `mcpServers` map. From the repository root, run the following with canonical absolute paths for your installation. Create the input and workspace directories first, keep them separate, and choose a new output directory named `devharness` outside both.

```sh
.venv/bin/python integrations/codex/devharness/scripts/configure.py \
  --python /absolute/path/to/.venv/bin/python \
  --input-root /absolute/path/to/inputs \
  --workspace /absolute/path/to/workspace \
  --output /absolute/path/to/marketplace/devharness
```

The helper writes a configured copy whose `.mcp.json` launches `python -I -B -m devbench.platform.plugin_mcp`. To generate a local marketplace manifest as well, add `--write-marketplace /absolute/path/to/marketplace`. Installation steps and optional kernel settings are in the [plugin guide](devharness/README.md).

## Verify the plugin

Run the packaging and configuration tests from the repository root. The suite checks the inactive template and exercises the configured seven-tool server over a real MCP STDIO connection.

```sh
.venv/bin/python -m pytest -q integrations/codex/tests/test_plugin.py
```
