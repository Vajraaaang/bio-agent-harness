# M2-2 verification

The Linux ARM64 image executed both qualified integration routes, marker-panel annotation, scArches reference transfer, ClusterDE, targeted CADET and negative-binomial count splitting through the public MCP tools. The tests register synthetic inputs, freeze and submit plans, poll the daemon and read back the receipts. The close-mammal integration compares the in-paralog mapping with strict one-to-one mapping. Each produces separate scIB batch and biology scores. A returned R4 embedding and raw inputs identified as pooled across species are refused by count inference.

## Post-clustering calibration

Each complete-null run used 192 independent observations. A run counts as a false discovery if the method selects any feature. The nominal threshold was 5%. ClusterDE tested a 160-feature panel. CADET and the split diagnostic each tested one prespecified feature after clustering on two features. These discovery proportions describe different hypothesis families.

| Procedure | Runs with any discovery | Empirical complete-null FDR |
|---|---:|---:|
| Standalone ClusterDE, native Clipper BC selections | 0/128 | 0.00% |
| CADET, one target | 8/128 | 6.25% |
| Count splitting with true NB size 4 | 5/128 | 3.91% |
| Count splitting with misspecified NB size 16 | 82/128 | 64.06% |

The count-split comparison used a held-fold rank test to expose the effect of dispersion misspecification. The mounted dispersion diagnostic refused **128/128** misspecified runs. The Clipper power control selected **48/48** planted markers and **0/112** null features. Its cutoff was **2.9682127226927757**, with contrast scores from **10.662688661356508 to 19.959310965325308** among planted genes and **−2.9682127226927757 to 2.2424074842360966** among null genes. All 160 observed scores and selection flags, in feature order, are in [the planted-panel result](clusterde-planted.json). Indices 0 through 47 are the planted features. ClusterDE retains null p/q fields and returns native selection flags.

CADET conditions on the two-means clustering path. This adapter applies randomized quantile residuals using independently specified NB marginals and requires declared independent units and features with identity Gaussian covariance. Its single targeted test has q = p. The sparse initial panel (size 0.4, mean 1, 60.79% zeros) yielded 4/128 discoveries.

| Separate CADET panel | Runs with a discovery | Point estimate | One-sided exact 95% upper bound |
|---|---:|---:|---:|
| Size 4, mean 12 | 60/1,024 | 5.86% | 7.21% |
| Size 0.4, mean 1 | 55/1,024 | 5.37% | 6.68% |

Both upper bounds met the separate panels’ 7.5% tolerance. Both point estimates exceeded 5%. These results apply to the simulated regimes and known-marginal assumptions. ClusterDE uses a standalone moment-fitted NB copula with rank-based shrinkage and Clipper BC. Native scDesign3 and R CADET equivalence and cross-architecture parity remain unverified. The MCP receipts retain L1 and `clinical_release_allowed = false`.

## Runtime coverage

The live integration runs used scib-metrics 0.6.2 and PCA. scArches exercised scvi-tools query transfer. popV’s API and ontology-consensus handling received mocked adapter coverage. CellTypist, SingleR, Azimuth, scPoli, SAMap and SATURN did not run live. Neither a SATURN GPU nor staged ESM-2 weights were used. The cross-species tests exercised reference digest checks and SATURN barcode/species alignment on CPU. A separate offline smoke run in the built image exercised Harmony, scVI, scANVI and SysVI on 160 synthetic cells. Each returned a finite 160 × 4 embedding. This check exercised the adapters directly. The missing live executions leave the full M2 criterion for every P1 solver open.

## Verification commands and results

Runs used managed CPython 3.13.18 and SQLite 3.54.1 with no `DYLD_LIBRARY_PATH`. BLAS, OpenMP and Accelerate thread counts were set to one for the requested pytest run.

| Check | Observed output |
|---|---|
| Requested eleven-file pytest suite | `96 passed, 12 skipped, 1 warning in 104.00s (0:01:43)` |
| Marker calibration suite with printed observations | `16 passed, 1 warning in 14.45s` |
| Apptainer, thesis calibration, M2-1 E2E, existing marker inference and synthetic gallery regressions | `73 passed, 2 skipped, 8 warnings in 95.28s (0:01:35)` |
| Kernel registry rerun with the local routed ARM64 image selected | `8 passed, 1 warning in 13.28s` |
| Ruff over every changed Python file | `All checks passed!` |
| Ruff formatting over every changed Python file | `28 files already formatted` |
| Mypy over MCP, daemon and container backend | `Success: no issues found in 40 source files` |
| Git whitespace check | Exit 0, empty output |

Nine tests in the eleven-file suite skipped for missing host Scanpy. Three needed an explicitly selected legacy image and passed in the registry rerun. The two additional regression skips required Apptainer/SingularityCE and an explicitly selected thesis calibration image. Warnings included AnnData/Zarr deprecations. The historical thesis calibration simulation was not rerun.

## Changed files

- `containers/rna-kernel-2.0/Dockerfile.m2-wave2`
- `containers/rna-kernel-2.0/Dockerfile.m2-wave2.dockerignore`
- `docs/audits/2026-11-27-m2-2/clusterde-planted.json`
- `docs/audits/2026-11-27-m2-2/verification.md`
- `src/devbench/analysis/integration_metrics.py`
- `src/devbench/core/scrna_annotation.py`
- `src/devbench/core/scrna_marker_inference.py`
- `src/devbench/engine/mounted_worker_registry.py`
- `src/devbench/engine/nb_marker_confirmation.py`
- `src/devbench/mcp/auxiliary_intake.py`
- `src/devbench/mcp/execution.py`
- `src/devbench/mcp/intake.py`
- `src/devbench/mcp/planning.py`
- `src/devbench/mcp/registry.py`
- `src/devbench/mcp/registry_m2.py`
- `src/devbench/mcp/registry_m2_wave2.py`
- `src/devbench/mcp/species_scope.py`
- `src/devharness_controlled/native-kernel-runtime.lock.json`
- `src/devharness_controlled/native_contracts.py`
- `src/devharness_server/r/scrna_annotation.R`
- `src/rna_kernels/embedding_qc.py`
- `src/rna_kernels/m2_contract.py`
- `src/rna_kernels/mounted_m2.py`
- `src/rna_kernels/mounted_scrna.py`
- `src/rna_kernels/r/scrna_annotation.R`
- `src/rna_kernels/scrna_annotation.py`
- `src/rna_kernels/scrna_cadet.py`
- `src/rna_kernels/scrna_clusterde.py`
- `src/rna_kernels/scrna_cross_species.py`
- `src/rna_kernels/scrna_nb_countsplit.py`
- `src/rna_kernels/scrna_saturn.py`
- `tests/test_integration_scib_and_cross_species.py`
- `tests/test_m2_2_e2e.py`
- `tests/test_post_clustering_selective_inference.py`
- `tests/test_scrna_annotation_and_scfm_gate.py`
