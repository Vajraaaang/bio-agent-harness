# Verification evidence — DevHarness 0.2

> Historical v0.2 verification record. A later authorized live Codex pilot is documented in ../results/codex-live.gj3jlH/inspection.md and summarized in the current README. Mac v0.3 verification is recorded in [mac-validation.md](mac-validation.md). Statements below describe the earlier verification date, not current authorization or execution status.

Recorded 2026-09-07T06:19:01.730855+00:00 on macOS arm64. This is software and numerical verification, not a clinical validation package or compliance certification.

| Check | Result |
|---|---|
| Full Python 3.12.1 suite | 370 passed, 2 upstream Authlib deprecation warnings; 127.43 seconds |
| Full Python 3.10.1 suite | 370 passed, 2 upstream Authlib deprecation warnings; 115.23 seconds |
| Synthetic matrix | 55 distinct mechanisms across four tracks; both conditions exercised |
| Privileged oracle controls | 110/110 passed; all 110 audit manifests verified against saved receipts |
| Public numerical workflow | 3/3 measurement-derived tasks passed on real RPKM; all 3 audit manifests verified |
| Public numerical replay | QC, design validation and spline fitting replayed; 3/3 structured outcomes matched |
| Replay tolerance/runtime | Absolute and relative tolerance 1e-8; code, guidance and numerical environment matched |
| Replay model calls | Zero; only recorded numerical tools were executed |
| Dependency resolution | 149-package universal lockfile generated and checked |
| Model adapters | Codex/Claude single-turn and native four-stage resume tested with fake executables |
| Live CLI model evaluation | Not executed; destination-specific authorization pending |

The oracle has privileged simulation answers. Its perfect scores validate staging, schema/grade logic, condition plumbing, persistence and hashing; they do not evaluate model reasoning. The public numerical callback computes from actual staged measurements and performs QC/rank/fit calls without reading goldens.

## Data and artifacts

- The actual E-MTAB-6798 Mouse.RPKM.txt download is 121,114,240 bytes with SHA256 `0d70a65b0d3b4f8b0cd8103ec30f2544852ea4af6896fc67fcee4c63e2574f50`.
- The public marker snapshot contains 96 genes by 35 embryonic Brain columns. The source manifest retains measured-data hashes, selected IDs and verified Ensembl annotations.
- TRRUST mouse data were cached with source/rights information, not vendored or treated as a commercial clinical license.
- E-MTAB-6967 was inspected at the study/catalog level. Its 1.67 GB archive was not downloaded. H5AD/10X readers were tested on generated fixtures; no actual single-cell model evaluation is claimed.
- JSON results, root receipts, the matched replay report and source-derived PNG/SVG plots are under results/expansion. The plot was visually inspected.

## Reviewed failure cases

Regression tests cover unsafe formula evaluation, optimizer failures, repeated biological units, q/p contradictions, malformed schemas, pipeline input changes, artifact tampering/deletion, rewritten history against a trusted root, replay path/runtime mismatch, incomplete tool evidence, and missing telemetry. Additional review found and fixed clinical classification downgrading, unclassified overlays, stale checkpoint reuse after asset/runtime changes, incomparable assisted-only gates in baseline comparisons, and journal filename collisions.

MCP tools are confined to the assigned workspace. Clinical/private/restricted or unclassified data cannot enter built-in external model backends, and clinical mode holds every result. Actor strings and provided control evidence are assertions; they are not electronic signatures or regulatory authorization.

## Remaining qualification and external-work limits

Production identity/access controls, electronic signatures, qualified retention and backup/restore, reference licensing, intended-use analytical/clinical validation, change control, training and independent quality approval remain deployment responsibilities. Local hashing alone does not guarantee Part 11 compliance, WORM storage or universal determinism. The old Python 3.10.1 spawn cleanup issue is documented; use a patched interpreter.

Automatic approval review rejected the proposed terminal Claude source review because exporting repository content to Anthropic required destination-specific approval. Terminal Claude was started and its Team subscription login verified, but no review prompt or live benchmark turn was sent. See external-evaluation-scope.md for the concrete proposed next step.
