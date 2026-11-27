# Install DevHarness

Install uv (CI uses 0.13.10) and run these commands from the repository root
to provision its pinned, native Python 3.13.18 on macOS or Linux.

```sh
uv sync --locked --extra dev --python-preference only-managed
repo="$(pwd -P)"
"$repo/.venv/bin/python" -c 'import platform, sqlite3; print(platform.machine(), sqlite3.sqlite_version)'
```

After pulling an update that relocates source files, rerun the sync command
with the extras you use to refresh an existing editable install.

On Apple Silicon, use an arm64 terminal and Python. The lockfile also offers
`--extra dev` for tests and linters, `--extra analysis` for the optional
single-cell packages, and `--extra reporting` for local figure exports.

## SQLite for local execution

The pinned interpreter supplies stdlib SQLite 3.51.3 or later for scientific WAL. Verify it from the checkout root.

```sh
make check-sqlite-wal VERIFY_PYTHON="$repo/.venv/bin/python"
```

For a separately installed system or Conda Python, the plugin configurator can check a patched Homebrew library on macOS or a Linux library selected with `DEVBENCH_SQLITE_LIB`. If macOS SIP strips the loader setting, run `uv python install` and repeat the locked sync. [Plugin binding and fallback options](../integrations/codex/devharness/README.md)

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
