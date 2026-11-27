# M1-3 verification

Branch `m1/dual-lane-sandbox-and-out-of-core-vault` started at `e113454daa87f9271f6b4a51219ed590271af881`. The ratified specification was read from `9431af97aad63dd4661ff79f97a64db2722314eb`. The operator authorized public package staging and a single exception to the original kernel file exclusion for the R deadline in `src/rna_kernels/mounted_model.py`.

## Scope of evidence

These results establish L1 execution, custody, resource use and isolation. The fixtures contain synthetic counts and declared donor identities. Every exploratory artifact retains its L1 ceiling. `clinical_release_allowed` is false.

Docker Engine 29.9.2 ran locally on an ARM64 Mac. macOS Seatbelt also ran a real public synthetic worker and reported `oci_isolated=false`. Rootless Podman, cgroups v2 delegation, daemonless cleanup and Linux fallback behavior have fixture coverage on this host. The host had no Podman CLI. AMD64 image checks ran under Docker Desktop emulation. The images are staged locally.

## Scale and peak RSS

Each AnnData fixture has 17,000 observations, 36,000 synthetic features and 68,000,000 stored nonzeros in `layers/counts`. The writer emitted all values and indices. Inspection, answerability, source and staging attestation, and full-family population aggregation ran in separate processes. The test independently checks all 36,000 aggregated feature totals. A separately approved Tier 2 program reads the entire selected layer and reports the count sum.

| Physical store | Input bytes | Tier 1 control peak RSS | Tier 1 worker peak RSS | Tier 2 control peak RSS | Tier 2 worker peak RSS |
|---|---:|---:|---:|---:|---:|
| Sparse H5AD | 547,133,552 | 372,015,104 | 221,417,472 | 303,955,968 | 69,746,688 |
| Sparse Zarr v2 | 544,282,335 | 369,541,120 | 204,529,664 | 285,999,104 | 63,434,752 |

All RSS values are bytes from `resource.getrusage`, normalized for Darwin and Linux. The limits are 2,147,483,648 bytes for the control process and 4,294,967,296 bytes for the worker. This independent measurement used the streaming QC image `sha256:71270fd40eb5060ea908767f934dca52109a8f367767814d157a4ef9c92505f3` and the staged ARM64 scientific Python base `sha256:5288b9e8e4ba18600ca89ccfd2bbd6ae65ad5fe3c8582ec7bd27978465032f80`. The package-complete release images and the R deadline rebuild were checked separately below. Dedicated format tests also exercised CSC, CSR, compressed Zarr v3, reordered features, resumed reads and a physically larger-than-16-MB Parquet table.

## Release images

Each table entry is a locally inspected OCI image configuration SHA-256. The runtime verifies the full digest before admission.

| Image | linux/arm64 | linux/amd64 |
|---|---|---|
| `devharness-explore-py` | `7691a936b4be9151d22c73a2430be21581810ae6baa6e11adb7aba727332e7f7` | `4a66b4c2fffc074f7731e98206e3bfd5981d36b59aa136ec6fb2a154ec2ea270` |
| `devharness-explore-r` | `ad182b2b28b051d0bde455f0efe63106efef40ad75cab48e518b04128e6957de` | `0cfadd399855b8fd9a45a047519a64787330660ced53f6d3d9cb1a0fd5186154` |
| Routed kernel with profile-bound R deadline | `cf15dbd3a904d471df6c68990057f4ededa6420f9870dc107b61e193e4d2af91` | `6ecce881daff94423d7597eed0b273250e2afea61f32ff7f3b7d4806ffec56d2` |

Only public release packages were downloaded during the separately authorized staging step. The Python release used 54 SHA-256-pinned wheels per architecture. The R release used 78 checked archives per architecture. All installations and scientific smoke runs used `--network none`. Python 3.13.18 loaded 19 distributions and read or wrote H5AD, MuData, SpatialData, TileDB-SOMA, Parquet and both Zarr generations. R 4.6.1 loaded all 15 specified namespaces, round-tripped Arrow Parquet, fit the synthetic edgeR smoke model, and read a 3-gene, 2-cell H5AD with a count sum of 21 using the native R reader. Exact package versions, archive sources and hashes are in [the scientific image release files](../../containers/exploratory-science/).

## Verification commands and recorded outcomes

The shell commands below use the branch worktree as their working directory. Numerical and WAL tests ran with the following environment.

```sh
export DYLD_LIBRARY_PATH=/opt/homebrew/opt/sqlite/lib
export VECLIB_MAXIMUM_THREADS=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
```

The independent live sweep combined the eleven requested test modules with the format and Studio tests. Its original routed image had an internal 240-second R watchdog.

```sh
DEVBENCH_T15_DOCKER=1 \
DEVBENCH_RNA_KERNEL_TEST_IMAGE=sha256:f48dd0d073e1de9d569c3e13cb4d6e5c42858e7abb7302105f7703b020c1b42d \
DEVBENCH_STREAMING_TEST_IMAGE=sha256:71270fd40eb5060ea908767f934dca52109a8f367767814d157a4ef9c92505f3 \
uv run --no-sync pytest -s -q \
  tests/test_container_backend.py tests/test_dual_lane_sandbox.py \
  tests/test_out_of_core_vault.py tests/test_mounted_kernel_oci.py \
  tests/test_mounted_oci_recovery.py tests/test_exploratory_execution_profile.py \
  tests/test_plugin_registered_method.py tests/test_rna_answerability.py \
  tests/test_tier_a_executor.py tests/test_tier_a_donor_trajectory.py \
  tests/test_thesis_answerability_matrix.py tests/test_streaming_expression_formats.py \
  tests/test_plugin_graph_studio.py
```

```text
FAILED tests/test_mounted_kernel_oci.py::test_mounted_count_scale_in_pinned_container[bulk-60000]
FAILED tests/test_mounted_kernel_oci.py::test_mounted_count_scale_in_pinned_container[pseudobulk-60000]
2 failed, 255 passed, 3 warnings in 866.19s (0:14:26)
```

The original 2 GiB Docker fixture also failed both 60,000-gene cases. A previous run on the 4 GiB profile completed both, demonstrating timing variability. The captured synthetic pseudobulk traceback identified the 240-second `Rscript` subprocess deadline in the original routed image. Both checks passed on the separately identified historical edgeR image in 50.30 seconds. The authorized fix passes the reviewed timeout into the nested R process through the verified OCI command. Standalone callers retain 240 seconds, and the outer watchdog governs total job time.

```sh
uv run --no-sync pytest -q tests/test_container_backend.py::test_inner_r_watchdog_uses_verified_worker_deadline
docker build --network=none --pull=false --platform=linux/arm64 \
  --build-arg KERNEL_IMAGE=devbench-rna-kernel:2.0-thesis-routed-arm64 \
  --progress=plain -f containers/rna-kernel-2.0/Dockerfile.streaming \
  -t devharness-m1-3-streaming:local .
docker build --network=none --pull=false --platform=linux/amd64 \
  --build-arg KERNEL_IMAGE=devbench-rna-kernel:2.0-thesis-routed-amd64 \
  --progress=plain -f containers/rna-kernel-2.0/Dockerfile.streaming \
  -t devharness-m1-3-streaming:amd64 .
DEVBENCH_RNA_KERNEL_TEST_IMAGE=sha256:cf15dbd3a904d471df6c68990057f4ededa6420f9870dc107b61e193e4d2af91 \
  uv run --no-sync pytest -q tests/test_mounted_kernel_oci.py -k 60000
```

The deadline-contract test reported `1 passed in 0.11s`. Both builds returned the image IDs listed above. Native discovery selected the new ARM64 ID. Historical routed pins continue to resolve for replay.

The two previously failing cases ran on the rebuilt ARM64 image with the original 256 samples, 60,000 genes, statistical assertions and 4 GiB memory limit.

```text
..                                                                       [100%]
2 passed, 3 deselected in 453.86s (0:07:33)
```

Taken together, the integrated sweep and this targeted rerun give passing results for all 257 cases in that sweep. The three deselected tests had already passed.

## Governed scientific image dispatch

```sh
DEVBENCH_EXPLORE_SCIENCE=1 uv run --no-sync pytest -q -s tests/test_tier2_science_runtime.py
```

```text
python sha256:7691a936b4be9151d22c73a2430be21581810ae6baa6e11adb7aba727332e7f7 loaded 19 packages
.r sha256:ad182b2b28b051d0bde455f0efe63106efef40ad75cab48e518b04128e6957de loaded 15 packages
.
2 passed in 19.87s
```

Additional verification included both private authoring routes, late private-content taint, embargoed directory members, content replacement, exact script approval before reservation, verified orphan cleanup, sidecar binding, and publication of actual scientific files. The installed public Plugin and both private Studio routes passed in a 24-test live group. The final live sandbox, binary artifact, replay and evidence-policy group reported `79 passed in 88.09s`. Community runtime and extensible methods reported `180 passed in 59.38s`. The Studio UI, evidence access policy and MCP regression group reported `57 passed in 122.71s` with the optional analysis dependencies installed. The independent numerical software verifier completed its continuous and count fixtures with identical repeats, valid custody checks and completed R comparisons.

The private Studio output reader additionally checks the session, frozen plan digest, completed receipt, declared port and current embargo before producing a bounded local preview. Web content is displayed as text. Small PNGs can be viewed directly. The focused live CSV test checked the exact returned value and digest, wrong-plan and wrong-port refusal, and rejection on both an online Studio and an evidence-delivery surface.

```sh
DEVBENCH_T15_DOCKER=1 uv run --no-sync pytest -q \
  tests/test_plugin_graph_studio.py::test_offline_operator_inspects_verified_private_output
```

```text
1 passed in 6.31s
```

## Operational boundaries

- Registered Studio jobs check the worker's actual daemon reservation. The existing daemon allocates one CPU slot to a registered exploratory Plugin job. A request that exceeds the reservation fails before scientific execution. Tier 1 and directly approved Tier 2 graph runs accept their registry or plan resource profiles.
- The Tier 2 staging supervisor monitors aggregate disk usage during execution. Enforcement has a sampling interval. The runtime also enforces a single-file size limit, validates content after stopping the worker and checks actual `rw,nosuid,nodev,noexec` mount flags.
- The legacy public MCP receipt envelope exposes the exploratory scope and fully tagged nested artifacts. The digest-bound scientific step receipt and offline private wrapper carry the canonical top-level tags. Private data stays in the offline Studio path.
- Zarr supports regular local chunk grids and reviewed codecs. Unsupported storage dependencies fail during validation. The legacy MCP Zarr inspector reports a header digest. The local vault provides the full metadata-and-chunk digest used for admission and execution.

## Static checks

The final staged code passed the following checks. Ruff covered 64 Python files. The Git whitespace checks both exited zero with no output.

```sh
git diff --cached --name-only -z -- '*.py' | xargs -0 uv run --no-sync ruff check
git diff --cached --name-only -z -- '*.py' | xargs -0 uv run --no-sync ruff format --check
git diff --cached --check
git diff --check
git status --short
```

```text
All checks passed!
64 files already formatted
```

## Changed files

The following paths are relative to the repository root.

```text
containers/exploratory-science/Dockerfile
containers/exploratory-science/Dockerfile.r
containers/exploratory-science/Dockerfile.r.dockerignore
containers/exploratory-science/install-r-science.R
containers/exploratory-science/python-amd64.lock
containers/exploratory-science/python-arm64.lock
containers/exploratory-science/python-images.lock.json
containers/exploratory-science/r-image.py
containers/exploratory-science/r-science-amd64.lock.json
containers/exploratory-science/r-science-arm64.lock.json
containers/exploratory-science/verify-r-science.R
containers/exploratory-science/verify_python.py
containers/rna-kernel-2.0/Dockerfile.streaming
containers/rna-kernel-2.0/Dockerfile.streaming.dockerignore
src/devbench/analysis/assets/workbench-investigations.js
src/devbench/analysis/assets/workbench-studio.js
src/devbench/analysis/evidence_access.py
src/devbench/analysis/paired_tximport_svaseq.py
src/devbench/analysis/thesis_compact_replay.py
src/devbench/analysis/thesis_exploratory_replay.py
src/devbench/core/_rna_solver_inputs.py
src/devbench/core/bulk_fit_evidence.py
src/devbench/core/execution_contracts.py
src/devbench/core/expression_inventory.py
src/devbench/core/expression_io.py
src/devbench/core/h5ad_gene_blocks.py
src/devbench/core/hdf5_custody.py
src/devbench/core/phenotype_prediction.py
src/devbench/core/protected_partitions.py
src/devbench/core/rna_answerability.py
src/devbench/core/scrna_ccc.py
src/devbench/core/scrna_donor_trajectory.py
src/devbench/core/scrna_neighborhood_da.py
src/devbench/core/sparse_expression_blocks.py
src/devbench/core/zarr_expression_store.py
src/devbench/engine/container_backend.py
src/devbench/engine/exploratory_images.py
src/devbench/engine/exploratory_oci.py
src/devbench/engine/method_executor.py
src/devbench/engine/methods.py
src/devbench/engine/mounted_kernel_oci.py
src/devbench/engine/mounted_matrix_vault.py
src/devbench/engine/mounted_population_qc.py
src/devbench/engine/mounted_worker_registry.py
src/devbench/engine/tier2_approval.py
src/devbench/engine/tier2_artifacts.py
src/devbench/engine/tier2_input_custody.py
src/devbench/engine/tier2_staging.py
src/devbench/engine/tier_a_executor.py
src/devbench/engine/tier_a_scrna.py
src/devbench/platform/adaptive_workflow.py
src/devbench/platform/adaptive_workflow_state.py
src/devbench/platform/plugin_exploratory_studio.py
src/devbench/platform/plugin_graph_studio.py
src/devbench/platform/plugin_jobs.py
src/devbench/platform/plugin_registered_method.py
src/devbench/platform/scrna_population_analysis.py
src/devbench/platform/workbench_http.py
src/devharness_controlled/native-kernel-runtime.lock.json
src/devharness_controlled/native_contracts.py
src/devharness_core/methods.py
src/rna_kernels/mounted_model.py
tests/conftest.py
tests/test_container_backend.py
tests/test_dual_lane_sandbox.py
tests/test_exploratory_benchmark.py
tests/test_exploratory_execution_profile.py
tests/test_installed_plugin_custom_graph.py
tests/test_installed_plugin_private_graph.py
tests/test_mounted_kernel_oci.py
tests/test_mounted_oci_recovery.py
tests/test_oci_binary_artifacts.py
tests/test_out_of_core_vault.py
tests/test_plugin_graph_studio.py
tests/test_plugin_registered_method.py
tests/test_scrna_population_analysis.py
tests/test_streaming_expression_formats.py
tests/test_tier2_science_runtime.py
tests/test_tier_a_executor.py
validation/m1-3/verification.md
```
