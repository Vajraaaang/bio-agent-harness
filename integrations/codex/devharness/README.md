# DevHarness RNA Research

Use the DevHarness 0.3.0 plugin to inspect local RNA inputs, test whether a study
design supports a question, freeze a plan, and follow reviewed execution through
its receipts. The 13 tools run through the workspace-scoped `devbench mcp` STDIO
shim. Studio links are passive by default. Open an investigation link explicitly
for interactive review. Private and clinical work requires local offline Studio.

## Install the configured plugin

From the source checkout, run `uv python install` and
`uv sync --locked --extra dev --python-preference only-managed`. The pinned
Python build uses patched stdlib SQLite (minimum 3.51.3). Configuration checks
the runtime before creating any plugin files. For system or Conda Python with
an unpatched SQLite, the helper can probe Homebrew's
`/opt/homebrew/opt/sqlite/lib` or `/usr/local/opt/sqlite/lib` on macOS. On Linux,
set `DEVBENCH_SQLITE_LIB` to the absolute directory of a patched library. If
macOS SIP strips the loader setting, use the uv-managed interpreter.

Run `scripts/configure.py` with these bindings.

- `--python` selects the installed Python 3.13+ virtual environment executable.
- `--input-root` selects an existing canonical read-only input directory.
- `--workspace` selects an existing canonical directory for the daemon, ledgers and runs. Keep it separate from inputs, source and the plugin.
- `--output` selects a fresh directory named `devharness`.
- `--expect-version` optionally checks the installed backend version.
- `--mounted-image-id` optionally binds a local mounted kernel image by full SHA-256 ID. Automatic discovery checks local images for native architecture.
- `--allow-external-input-files` opts into explicitly named external file bindings under the daemon's intake policy.
- `--write-marketplace` creates a local marketplace containing the configured copy.

The portable root manifest follows the published
[Agent Plugins 1.0.0 schema](https://agent-plugins.org/schemas/1.0.0/plugin.schema.json).
Portable clients discover `skills/` and `mcp.json` in fixed locations. The
portable command uses a package-relative launcher for the bound Python
interpreter. The compatibility manifest in `.codex-plugin/plugin.json`
references the same skills and a machine-local `.mcp.json`. Both launch
`-I -B -m devbench.platform.cli mcp` with the same daemon options.

Use `--no-legacy-codex-manifest` to omit the compatibility manifest. The default
mirror sunsets once the pinned minimum Codex CLI **and** Desktop App builds
resolve the portable root manifest across two consecutive releases. Record
CLI and Desktop results separately.

Register a configured marketplace with
`codex plugin marketplace add /absolute/path/to/marketplace --json`.
Install `devharness@devharness-local` through `codex plugin add` where supported
or use the plugin picker. A configured package contains machine-local paths.

## Skills and client mirrors

The package includes `rna-research-core`, `scrna-snrna-qc-integration`,
`pseudobulk-and-de`, `composition-and-da`, `trajectory-and-programs`,
`spatial-and-ccc`, `method-benchmarking-and-critic` and `private-data-studio`.
Use `$rna-research-core` for intake and evidence reporting. Each other skill
covers the corresponding assay, method or local-private workflow.

Pure Codex setup requires no client mirrors. From the repository root, run
the following to populate or verify an explicitly selected project.

```sh
.venv/bin/python integrations/codex/devharness/scripts/configure.py \
  --mirror-clients all --mirror-root /absolute/path/to/project
.venv/bin/python integrations/codex/devharness/scripts/configure.py \
  --check-mirrors --mirror-root /absolute/path/to/project
```

Select `claude`, `cursor`, a comma-separated list, or `all`. Claude reads copies
under `.claude/skills/`. Cursor uses native `.agents/skills/` discovery.
In another project, the helper materializes the canonical files there.
Add `--legacy-cursor-skills` to also populate `.cursor/skills/`.
The checksum manifest records SHA-256 for each source and mirrored file.
Verification reopens the files and reports drift, missing files or unsafe links.
