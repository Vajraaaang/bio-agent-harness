# Connect an MCP client

Install with `uv sync --locked --extra dev --python-preference only-managed` from the checkout root.
Check the interpreter and patched SQLite runtime before connecting the server.
[Installation](installation.md)

In the setup commands, run this block from the repository root. The example
directory holds public synthetic inputs. Use separate, existing directories for
your own inputs and the writable workspace.

```sh
repo="$(pwd -P)"
input_root="$repo/examples/bulk-rnaseq/data"
workspace="$repo/results/mcp-quickstart"
mkdir -p "$workspace"
```

## Install the Codex plugin

The plugin adds eight modular research skills and carries the configured server
entry. Set up a local marketplace using the directories from the first block.

```sh
marketplace="$repo/results/mcp-local-marketplace"
mkdir -p "$marketplace"
"$repo/.venv/bin/python" "$repo/integrations/codex/devharness/scripts/configure.py" \
  --python "$repo/.venv/bin/python" \
  --input-root "$input_root" --workspace "$workspace" \
  --output "$marketplace/devharness" --write-marketplace "$marketplace"
codex plugin marketplace add "$marketplace" --json
codex plugin add devharness@devharness-local
```

Start a new session and select `@devharness` or invoke `$rna-research-core`.
Keep the configured copy local because it contains local paths. Choose either
the plugin or the direct server registration below for this workspace.
[Plugin configuration reference](../integrations/codex/devharness/README.md)

## Register the server directly in Codex

```sh
codex mcp add devharness -- "$repo/.venv/bin/devbench" mcp \
  --input-root "$input_root" --workspace "$workspace" --no-auto-browser
codex mcp list
```

## Claude Code

```sh
claude mcp add --transport stdio --scope local devharness -- \
  "$repo/.venv/bin/devbench" mcp \
  --input-root "$input_root" --workspace "$workspace" --no-auto-browser
claude mcp list
```

[Claude Code MCP documentation](https://code.claude.com/docs/en/mcp#option-3-add-a-local-stdio-server)

## Cursor and other STDIO clients

For Cursor, add the entry to the project’s `.cursor/mcp.json`. Use the same
command and argument array in other clients that accept an `mcpServers` map.
Replace each example path with an actual absolute path.

```json
{
  "mcpServers": {
    "devharness": {
      "type": "stdio",
      "command": "/absolute/bio-agent-harness/.venv/bin/devbench",
      "args": [
        "mcp",
        "--input-root", "/absolute/bio-agent-harness/examples/bulk-rnaseq/data",
        "--workspace", "/absolute/bio-agent-harness/results/mcp-quickstart",
        "--no-auto-browser"
      ]
    }
  }
}
```

The configured plugin binds the verified Python runtime automatically. [Cursor MCP documentation](https://cursor.com/docs/mcp) · [SQLite setup](installation.md#sqlite-for-local-execution)

## First investigation

Give the client the absolute paths of `counts.csv` and `design.csv` from the
configured input directory. Supply the objective, contrast, and the source for
the donor IDs. Ask it to inspect the data and check answerability before making
a plan. An eligible design can proceed to plan review in the same session.
The diagnostic audit in the example can run as soon as its exact plan is
frozen. [Bulk example](../examples/bulk-rnaseq/README.md) · [Tool reference](mcp-tools.md)
