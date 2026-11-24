# Install DevHarness

Use a native Python 3.13 or newer on macOS or Linux. The locked setup below
selects Python 3.13. Install `uv` and run these commands from the repository root.

```sh
uv sync --locked --python 3.13
repo="$(pwd -P)"
"$repo/.venv/bin/python" -c 'import platform, sqlite3; print(platform.machine(), sqlite3.sqlite_version)'
```

On Apple Silicon, use an arm64 terminal and Python. The lockfile also offers
`--extra dev` for tests and linters, `--extra analysis` for the optional
single-cell packages, and `--extra reporting` for local figure exports.

## SQLite for local execution

Scientific sessions use SQLite WAL with the WAL-reset fix. Use SQLite 3.51.3
or newer, or a backport recognized by the runtime check. On macOS with a patched
Homebrew SQLite, expose its library to the Python process and check it from the
checkout root.

```sh
export DYLD_LIBRARY_PATH="$(brew --prefix sqlite)/lib"
make check-sqlite-wal VERIFY_PYTHON="$repo/.venv/bin/python"
```

On Linux, select a Python build linked to a fixed SQLite and run the same
`make` check without the macOS variable. If a client launches the server with
its own environment on macOS, pass the library path to that server entry
explicitly. For Codex, add `--env "DYLD_LIBRARY_PATH=$DYLD_LIBRARY_PATH"` before
the MCP server name in `codex mcp add`. For Claude Code, pass
`--env "DYLD_LIBRARY_PATH=$DYLD_LIBRARY_PATH"` before `--transport stdio`.
For Cursor, add `env.DYLD_LIBRARY_PATH` to the server configuration with the
absolute library path. [Client setup](quickstart.md)

## Numerical runtimes

The Python runtime supports input inspection, design checks, matrix audits,
and the local Python solvers. Some workflows require a digest-pinned kernel
image or staged analysis runtime. Use a configured local container engine and
an image accepted by the matching operation, such as the bulk, single-cell, or
scCODA kernel. Pass the full image ID with `--mounted-image-id` where the entry
point supports it. Follow the runtime diagnostic if a plan reports the
required route as unavailable. [Kernel build definitions](../containers/rna-kernel-2.0) · [Workflow prerequisites](capabilities.md)

## Local Workbench

Use the same input root and workspace as MCP when opening a shared
investigation. Start the loopback application after the SQLite check.

```sh
mkdir -p "$repo/results/mcp-quickstart"
"$repo/.venv/bin/devbench" workbench \
  --input-root "$repo/examples/bulk-rnaseq/data" \
  --workspace "$repo/results/mcp-quickstart" \
  --enable-scientific --offline-only --port 8765
```

Open the URL printed by the command. Use the local Workbench for review and
data approved for local-only handling. For a fit in an authorized workspace,
start the Workbench with `--allow-data-execution` and complete the route's
review. [Interactive synthetic Studio demo](https://vajraaaang.github.io/bio-agent-harness/)
