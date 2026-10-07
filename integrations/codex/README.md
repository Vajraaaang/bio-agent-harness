# Local DevHarness plugin package

This integration was created separately from the active scientific code. It
uses Codex as the conversation interface and the existing Python backend for
calculation. There is no new HTTP server, hosted dashboard or model provider.

The distributable template is in `devharness/`. It contains one RNA workflow
skill, a compatibility manifest, a local configuration helper, documentation
and the existing project's MIT license. The helper makes a separate configured
copy using an explicit Python executable and a dedicated analysis workspace.
It does not alter Codex settings or install a marketplace.

## Observed checks

- Plugin manifest and RNA skill validation passed.
- Nine packaging/configuration tests passed, including missing runtime,
  version mismatch, broad workspace rejection and preservation of existing files.
- A real local MCP STDIO client initialized the configured backend and listed
  all ten tools. Actual expression QC and design-rank tools ran on synthetic
  count data. An input path outside the configured workspace was refused.
- Bulk-count and donor-aware single-cell count workflows completed through
  the same configured Python runtime and existing episode CLI. Both audit
  receipts verified. These CLI executions used explicit recorded actions;
  no live Codex participant selected them.

The bulk fixture has 12 genes and 8 artificial donor samples. The cellular
fixture has 14 genes, 32 cells and 8 declared artificial donors. All data were
generated for this local software check. The retained numerical-output check
requires actual estimated rows, finite estimates and `0 <= p <= q <= 1`.
These checks are not broad error-rate, biological or clinical qualification.

Observed runtime: Python 3.12.1, DevHarness 0.5.0.dev0, MCP 1.29.1. The actual
protocol and two RNA workflow checks took about 27.8 seconds. Peak RSS and cost
were not measured and remain unknown. No live model calls, new Colab jobs or
data uploads occurred. No existing scientific source, datasets, grading rules
or global Codex configuration were changed by this integration task.

## Evidence and limits

`validation/verification.json`, `validation/numerical-output-check.json` and
`validation/package-tests.xml` record the checks. Original MCP replies and
CLI stdout/stderr/commands are retained beside them, including the deliberate
out-of-scope refusal. The generated analysis episodes preserve their own
data/source identities and audit receipts.

The configured copy under `validation/configured/` points to synthetic test
data. **It is not the user's production installation.** The plugin has not
been registered in the active Codex app or tested through its actual plugin
picker. A dedicated user analysis folder is still needed before activation.
The original unconfigured template is the appropriate package to share;
configured copies contain machine-specific paths.

The full H5AD workbench and general scientific planning/reviewer features
retain their existing limits. This integration does not finish e11, establish
native custom dashboard support or prove that an agent produces better insight.
Further native installation testing must remain separate from these successful
local component and numerical checks.

## Publication scope

The validation receipt and absolute validation paths above describe local development evidence. The validation output tree and archived plugin distribution are excluded from this GitHub source snapshot.
