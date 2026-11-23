# DevHarness RNA Research

The Codex plugin connects to the governed DevHarness MCP server over local STDIO. Install the DevHarness runtime separately using the release's locked environment.

## Configure a local copy

Run `scripts/configure.py` with these absolute paths and options.

- `--python` selects the installed Python 3.13+ runtime. Keep the virtual environment executable path.
- `--input-root` selects an existing directory of read-only input data.
- `--workspace` selects an existing, dedicated directory for mutable receipts, ledgers and runs. Keep it separate from inputs, source code and the plugin.
- `--output` selects a new directory named `devharness`.
- `--expect-version` optionally requires an exact installed backend version.
- `--mounted-image-id` optionally selects a local mounted kernel image by full SHA-256 ID. By default the helper discovers a locally installed locked image when Docker is available.
- `--write-marketplace` optionally writes `.agents/plugins/marketplace.json` under a local marketplace directory containing the configured copy.

The helper checks the patched SQLite WAL runtime and, on macOS, retries with Homebrew's SQLite library when needed. It writes the STDIO command and thread limits to the configured copy's `.mcp.json`. It performs no installation or scientific execution.

For a local marketplace, run `codex plugin marketplace add /absolute/marketplace --json` and then `codex plugin add devharness@devharness-local`. Set `CODEX_HOME` to a dedicated test directory when validating an installation. Share the unconfigured package when distributing it, since a configured copy contains local paths.

## Research scope

Use the plugin to inspect supported RNA inputs, check whether a biological question is answerable, plan workflows for review, and follow execution and its receipts. Staged FASTQ requires local reads and references, and Salmon requires a pinned transcript-to-gene map. Companion Studio provides interactive QC, figures, and private-data analysis. Support depends on the installed runtime, input eligibility and recorded approval.
