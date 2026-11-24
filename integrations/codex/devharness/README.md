# DevHarness RNA Research

The Codex plugin connects to the governed DevHarness MCP server over local STDIO. Install the DevHarness runtime separately using the release's locked environment.

## Configure a local copy

Run `scripts/configure.py` with these absolute paths and options.

- `--python` selects the installed Python 3.13+ runtime. Keep the virtual environment executable path.
- `--input-root` selects an existing directory of read-only input data.
- `--allow-external-input-files` accepts explicitly named, canonical files from other directories. Folder discovery and files referenced by a FASTQ manifest still use the configured input root. Private and unclassified inputs still go to offline Companion Studio.
- `--workspace` selects an existing, dedicated directory for mutable receipts, ledgers and runs. Keep it separate from inputs, source code and the plugin.
- `--output` selects a new directory named `devharness`.
- `--expect-version` optionally requires an exact installed backend version.
- `--mounted-image-id` optionally selects a local mounted kernel image by full SHA-256 ID. By default the helper discovers a locally installed locked image when Docker is available.
- `--write-marketplace` optionally writes `.agents/plugins/marketplace.json` under a local marketplace directory containing the configured copy.

The helper checks the patched SQLite WAL runtime and, on macOS, retries with Homebrew's SQLite library when needed. It writes the STDIO command and thread limits to the configured copy's `.mcp.json`. It performs no installation or scientific execution.

For a local marketplace, run `codex plugin marketplace add /absolute/marketplace --json` and then `codex plugin add devharness@devharness-local`. Set `CODEX_HOME` to a dedicated test directory when validating an installation. Share the unconfigured package when distributing it, since a configured copy contains local paths.

## Research scope

Use the plugin to inspect supported RNA inputs, check whether a biological question is answerable, plan workflows for review, and follow execution and its receipts. Staged FASTQ requires local reads and references, and Salmon requires a pinned transcript-to-gene map. Companion Studio provides interactive QC, figures, and private-data analysis. Support depends on the installed runtime, input eligibility and recorded approval.

The first valid tool call starts or reuses offline Companion Studio for the configured workspace and opens it in the default browser. If that call returns an investigation link, Studio opens that view. Add `--no-auto-studio` to the server arguments for a headless client that manages Studio separately.

Follow the plugin route from inspection and answerability through the reviewed plan, run status, and receipt or verified Studio results. Starting Studio leaves the existing execution reviews in place. When the registry or local runtime cannot execute a question, report the missing requirements and the available Studio route in the same investigation.

Reuse `session_id` for a follow-up over admitted inputs. Native graph plans can declare `depends_on` with the exact predecessor `plan_id` and `plan_sha256`. Studio or MCP prepares each approved step against the current accepted predecessor receipt. Dependency edges enforce order and reuse completed work. A registered exploratory method can consume declared, current graph artifact version IDs in its closed input ports.

Private motor-program comparisons and the registered local solvers use the standard offline Studio review. Review the exact plan under Workflows, then use Findings for verified summaries. The shared motor UMAP also retains its figures and artifact downloads. The release audit tracks actual installed-plugin execution separately from scientific answerability.
