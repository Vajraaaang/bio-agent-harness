# Colab execution and bundle portability

Execution target: **Google Colab**. No HPC, Modal or SSH is part of this workbench path. This supersedes the earlier proposed local full-e11 execution. A real **80-cell synthetic fixture** has now completed notebook execution and review in Colab, archive import on macOS, and local numerical re-execution from saved source. Both bundles passed 19 reviewer checks and verified 369 artifacts. This is portability evidence; the full e11 Colab run and its local replay remain pending. See [the measured round-trip report](../workbench_runs/colab-portability-fixture-20260915/round-trip-report.md).

## Data and memory rationale

The e11 input contains 22,702 cells × 26,841 features. Colab is the requested first execution platform; revisit compute placement when combining all six timepoints and external atlases. Do not assume a GPU or a particular RAM allocation.

The proposed 0.24 GB sparse-memory estimate does not match the full stored counts matrix. Metadata inspection measured 117,929,308 stored entries: 943,434,464 data bytes, 471,717,232 index bytes and 90,812 pointer bytes, totalling **1,415,242,508 bytes** before annotations and intermediate arrays. Feature selection can produce a smaller working matrix, but that size must be measured separately. The full H5AD file is 2,860,140,073 bytes.

Local full-file SHA-256 for the inspected e11 input:

`33c344d484c6c538cc25d1b16f5ae9f2c2a7015ef19a07181d7830505e6b0ae7`

Put the H5AD files into Google Drive once, preferably using Drive for desktop or an existing file-sync workflow. In Colab, mount Drive at `/content/drive` and pass the original mounted file path to the backed reader. **Do not copy a multi-GB H5AD into `/content`.** The actual e11 file was found in the personal Google account, and a shortcut was added to My Drive without copying the file or changing sharing. The expected mounted path is `/content/drive/MyDrive/e11_integrated.h5ad`; it still needs runtime verification and comparison with the local SHA-256. The connected Drive plugin uses a different account, which explains its earlier empty searches.

The user approved one CPU run with a 90-minute runtime cap. The browser attachment URL unexpectedly allocated a separate CPU runtime; the unused CLI runtime was stopped, and only the browser runtime was used for execution. An automatic stop guard enforced the originally approved deadline. After fixture evidence was downloaded, that runtime was also stopped to avoid idle charges; `colab sessions` confirmed no active server sessions. The CLI's Drive-mount flow is interactive. Authorization was not completed, so no e11 matrix was analyzed. The expired authorization prompt was dismissed after shutdown.

The [prepared final notebook](https://colab.research.google.com/drive/1H4ml96ZmQvxqbqd6wuxCc0Ozttezqo6d) contains the source version that passed the real Colab fixture and macOS replay. The two earlier uploaded versions are labelled superseded. The full e11 run remains paused until the user can complete the interactive Drive mount.

The global CLI (`google-colab-cli==0.6.0`) could not execute with `jupyter-kernel-client==1.0.2`, whose public API removed `KernelClient`. Global packages were left unchanged. An isolated operator environment using `jupyter-kernel-client==0.15.0` executed through the installed CLI library's normal authenticated runtime API, with no credential inspection or CLI state editing.

## Reused setup pattern

`devbench.analysis.notebooks.colab_bootstrap_cells` now reuses the existing bounded source ZIP builder, allowlist and extractor. The ZIP includes `uv.lock`, excludes local datasets/results/credentials, and installs a fresh project interpreter with `uv sync --locked --extra analysis`. Thread limits are set before scientific imports; numerical stages must run through that project interpreter rather than Colab's preinstalled packages.

`write_colab_analysis_notebook` extends that bootstrap into the complete launcher. It pins Python 3.12, prints a resource plan, and runs `devbench-analyze` under the fresh project interpreter. The workbench emits a separate executed, editable notebook in the Drive bundle. Google Drive authorization remains a user interaction. No remote result is claimed from the bootstrap or local fixture tests.

Actual Linux testing found two startup failures: the host's configured `google.colab._kernel.Kernel` was unavailable in the isolated environment, followed by its inherited `google.colab` extension. Both failed attempts are preserved. The pinned kernel now explicitly selects ordinary IPython, isolates profile paths, and excludes inherited extensions/startup commands. Startup logs are retained. A one-cell probe, four local notebook tests, the complete Colab fixture and its local replay passed with the final change.

The supported prompt is the explicit preset `QC, correct batch effects, cluster, identify cell types`. This is a deterministic workflow, not an arbitrary natural-language planning agent; unsupported prompts are rejected. No external model service is called. Without an explicitly supplied marker panel or checksum-bound classifier, annotation is unavailable and cells remain `uncertain`.

## Small local subset command

The subset helper selects cells reproducibly, preserves all features and observation identities, uses the selected backed matrix, and writes a source/subset SHA-256 receipt. It does not copy the original full file. Its output stores selected counts in `X`; use `--layer X` when selecting from a previously generated subset.

```sh
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-analysis/bin/python \
  -m devbench.analysis.subset \
  --data /Users/vajraang/Downloads/e11_integrated.h5ad \
  --out /Users/vajraang/Documents/ChatGPT/bio-agent-harness/workbench_runs/subsets/e11-1000.h5ad \
  --cells 1000 --seed 20260915 --layer counts
```

This is a preparation command for quick iterations, not a substitute for the requested full-cell concordance run. Small fixture execution is tested; the command above has not yet been run on e11.

## Cross-boundary provenance contract

Every completed analysis bundle must retain:

- `execution_tier`: `colab` or `local_supervised`, without upgrading the original tier when importing a bundle locally.
- Runtime observations made inside the executing process: CPU/GPU model and availability, RAM, Python, package versions and observation time. GPU availability and the device actually used are separate facts.
- `data_sha256`, computed by streaming the original data file in Colab and compared with the retained local hash. A mismatch is a failure.
- Source ZIP and parameter identities, stage status/measurements, output hashes, reviewer report and any parent-bundle receipt.
- Explicit absence of inherited local permission/preflight guarantees. Environment labels and hashes are not platform attestation or clinical qualification.

Runtime observation and expected-hash comparison are wired into the bundle and stage workers. The input is rehashed by the reviewer. Workbench receipts use the same finite canonical JSON/hash contract as the coordinator broker but remain a distinct receipt type: they are not invented MCP events.

## Checkpoints, output and return

The new `devbench.analysis.workbench` path uses bounded row blocks and retains the input on Drive. QC and batch-specific feature-selection moments stream before constructing a smaller selected-feature matrix. Full-library normalization denominators are preserved after feature selection. Tests compare QC, normalized expression and the HVG table/selection with public Scanpy calls on sparse and dense fixtures. The older `run_concordance` implementation remains a separate in-memory helper; the Colab launcher does not call it.

Completed row chunks and stages persist SHA-256 receipts bound to data/source/parameter identities. Reconnection verifies completed outputs and resumes unfinished stages. Corruption or identity changes are errors. Scrublet processes one observed batch at a time under an allocation guard; batch is assumed to identify a library. It only flags doublets, and small batches remain unavailable.

Harmony modifies the embedding only. scVI selects CUDA when available and CPU otherwise, saving optimizer/epoch state every epoch and approximately every 120 seconds during training. A real small training run was deliberately interrupted and resumed in tests. Bitwise equivalence after reconnecting or changing hardware is not claimed. Default worker limits are 8 GiB RSS and 600 seconds per notebook stage, or 3,600 seconds for scVI. These are ceilings, not runtime estimates or OS security guarantees.

The bundle writer emits the executed notebook, figures, `environment.lock`, `summary.md`, measurements, `receipts.json`, `root-receipt.json` and a separate deterministic reviewer report. The reviewer recomputes whole-transcriptome QC, selected raw counts and normalization from the original input, checks identities and supported summary claims, recomputes concordance metrics, verifies checkpoints and regenerates figures. Shared scoring/renderer functions limit its independence; it is not a qualified biological or clinical reviewer.

Existing e11 labels are fixed external predictions, excluded from fitting metadata. ARI/NMI and annotation agreement are concordance, not accuracy. Reference confidence distributions accompany comparisons. Neither side is established as ground truth; the missing original classifier is never represented as reproduced. Original data remain an external hash-bound reference. A local binding file resolves that input, and notebook artifacts/source use bundle-relative paths.

The installed CLI supports this transfer syntax once a complete bundle exists:

```sh
/opt/anaconda3/bin/colab download -s SESSION_NAME \
  /content/drive/MyDrive/DevBench/bundle.zip \
  /Users/vajraang/Documents/ChatGPT/bio-agent-harness/workbench_runs/colab-bundle.zip
```

`SESSION_NAME` and the remote bundle path must come from the actual run. This download has not been executed. Import and replay are implemented and fixture-tested:

```sh
devbench-analyze --import-bundle /absolute/e11.bundle.zip \
  --expected-root RETAINED_MANIFEST_SHA256 --out /absolute/workbench_runs/e11-colab-import \
  --data /Users/vajraang/Downloads/e11_integrated.h5ad

devbench-analyze --replay-bundle /absolute/workbench_runs/e11-colab-import \
  --expected-root RETAINED_MANIFEST_SHA256 --out /absolute/workbench_runs/e11-local-replay \
  --data /Users/vajraang/Downloads/e11_integrated.h5ad --execution-tier local_supervised
```

Use the hash from the separately retained root receipt. Import checks every archived artifact and rejects unsafe paths without executing code; it retains the original execution tier. Replay uses saved source and adds a parent-root link. Install the saved source's `uv.lock` into a Python 3.12 environment first. A verified archive is not evidence of numerical replay. The original bundle stays sealed; replay creates a new bundle.

## Completion gate

The goal remains open until the end-state command executes in Colab against the Drive-hosted e11 file, produces the full tier-labelled bundle, and that bundle re-executes locally with its input hash verified. Neither bootstrap tests, cached files nor a prepared resource plan satisfy this gate.
