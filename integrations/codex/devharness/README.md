# DevHarness local Codex plugin

Codex is the conversational front end. This package adds an RNA-seq workflow
skill and can connect the **existing DevHarness MCP server over local STDIO**.
It does not start an HTTP server or require a localhost dashboard.

The plugin does not contain the scientific Python runtime, datasets, DevBench
graders or private references. Install an authorized DevHarness runtime
separately using that release's locked environment. Each user keeps their own
Codex login. No credentials are stored in this package.

## Configure a local copy

The source template contains the skill but no machine-specific MCP connection.
Run `scripts/configure.py` with these absolute paths:

- `--python`: Python from the installed DevHarness environment, retaining its
  virtual-environment executable path.
- `--workspace`: an existing dedicated folder for analysis inputs and outputs.
  Do not use the filesystem root, home directory, backend code or plugin folder.
- `--output`: a new directory named `devharness`, separate from those folders.
- `--expect-version`: optional exact installed backend version to require.

The helper creates a new plugin copy with an inline MCP configuration. The
command uses isolated Python imports, binds the server's working directory and
sets thread bounds. It probes installed package metadata, but does not run an
analysis, install dependencies, edit Codex settings or register a marketplace.
Backend input-path checks are not a complete security sandbox for Codex.

Register the configured copy through Codex's local plugin/marketplace workflow
when you are ready to install it. Then start a task in the dedicated analysis
workspace and ask DevHarness to inspect the input or propose a supported
analysis. Actual app installation and tool discovery must be checked there;
manifest validation is not proof of native app loading.

The package uses the supported Codex compatibility manifest. Skills are
discovered from the standard skill folder. An optional interactive MCP Apps
component can be considered later; arbitrary native Codex sidebar extensions
are not part of this package.

## RNA-seq scope

The MCP tools cover expression QC, design diagnostics, supported temporal
models and narrower lineage/network/enrichment operations. Matrix CSV/custom
Zarr/HDF5 inputs need aligned metadata. The separate existing workbench handles
supported H5AD/Cell Ranger inputs; the episode CLI handles its declared bounded
bulk and cellular contrasts. Raw reads, universal assay support and clinical
qualification are not provided by packaging these interfaces.

Plots remain real numerical artifacts: images, notebooks and HTML reports that
Codex can display or open. Deep Research is optional future integration, not an
enabled provider or automatic paid job.

For distribution, share the unconfigured package. A configured copy contains
machine-specific runtime and workspace locations. This is a local development
package, not a published universal-directory listing or a completed end-user
installer for the full scientific environment.
