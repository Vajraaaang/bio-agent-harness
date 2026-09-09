# DevHarness / DevBench 0.3

**A local transcriptomics evaluation platform for testing biological judgment, inspecting agent failures, and retaining verifiable execution evidence.**

![Python 3.10 and 3.12](docs/badges/python.svg) ![macOS arm64 verified locally](docs/badges/macos.svg) ![Linux CI configured](docs/badges/linux.svg) ![CI not yet run](docs/badges/ci.svg) ![MIT code license](docs/badges/license.svg)

Correct Python syntax does not establish a valid biological analysis. A model can invert a developmental contrast, treat repeated specimens as independent, fit an aliased design, or report unadjusted significance without raising an exception. DevBench turns these mistakes into explicit tasks and binary gates. It tests whether supervised tools help; it does not assume that they do.

**Research and software verification only. Clinical release is held.** Local artifact hashing is tamper-evidence, not a Part 11 certificate, electronic signature, or demonstration of biomarker utility. See the [control map](docs/part11-control-map.md) and [architecture](docs/architecture.md).

## Run on your Mac

Use a native Apple Silicon terminal and Python 3.12. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if needed (`brew install uv`), then run from this repository:

```sh
make install   # project-local .venv, locked dependencies and dev tools
make demo      # three offline adversarial oracle controls + an HTML report
make test      # full software verification suite
make help
```

Verified locally: **429 tests passed in 122.41 seconds; the three-control demo took 1.61 seconds.**

The demo requires no model login, network access or private data once dependencies are installed. It deliberately uses a **privileged simulation oracle**, so its passing results test harness plumbing, schemas and graders. They are not model performance. Installation depends on download speed; full-suite timing is recorded in [local verification](docs/mac-validation.md), with no 30-second installation or test guarantee.

`make install` uses `uv sync --locked --extra dev --python 3.12`, preserving the universal lockfile. If uv is absent, it uses the existing `.venv` or `python3.12 -m venv`, then pip; that fallback does not apply `uv.lock`. `make install PYTHON_VERSION=3.10` selects a patched 3.10 interpreter with uv. Python 3.11 is within the supported range but is not a CI matrix entry.

All make targets prepend the project's `.venv/bin` to PATH and default these settings before Python starts:

```sh
export VECLIB_MAXIMUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
```

Explicit environment overrides are preserved. For direct terminal/notebook launches, source [.env.example](.env.example) **before** importing numerical packages. The worker launcher also sets missing defaults before `spawn`; it cannot resize a BLAS pool already loaded by the parent. No global shell configuration is modified. `make check-platform` prints the interpreter architecture and rejects Rosetta on Apple Silicon.

Memory supervision uses `psutil` process-tree RSS on macOS and Linux, with no cgroup dependency. OS process-inspection permission is required; the watchdog fails closed when it cannot monitor a worker. RSS can double-count shared pages and miss spikes between polls. It is not a measurement of total unified-memory pressure or an instantaneous OS cap. Large-matrix RSS remains workload-dependent.

## Four execution planes

```text
 Synthetic / public processed inputs         CLI participant workspace
                  |                         CodexCLIAdapter / baseline
                  v                                     |
 +-------------------------------+                      |
 | 1. STORAGE                    |                      |
 | Bounded CSV / Zarr / HDF5      |                      v
 | IDs, chunks, provenance       |----> +-------------------------------+
 +-------------------------------+      | 3. FASTMCP SERVER             |
                  |                    | QC / rank / NB splines        |
                  v                    | lineage / networks / evidence |
 +-------------------------------+ <---+-------------------------------+
 | 2. RUNTIME GUARD               |                      |
 | spawn / thread defaults / RSS |---- compact results --+
 | timeouts / bounded transport  |                      |
 +-------------------------------+                      v
                                       +-------------------------------+
                                       | 4. DETERMINISTIC GRADER        |
                                       | Private references + gates    |
                                       | schema / outcome / invariants |
                                       +-------------------------------+
                                                    |
                     SHA-256 artifact store + parent-linked audit DAG
                                  JSON ledger / metrics / offline HTML
```

Private references and grader source stay in the coordinator, outside participant staging. Model transcripts, numerical tool events and final outcomes have different evidence scopes. A valid receipt proves integrity relative to that saved receipt; a completed grade evaluates the submitted answer.

## Benchmark matrix

| Analytical track | Synthetic tasks | Checks |
|---|---:|---|
| Trajectory and branching | 15 | Pseudotime rank, branch agreement, directed lineage topology |
| Batch and covariate disentanglement | 15 | Aliasing, identifiability, biological units, family and effect calibration |
| Regulatory networks | 10 | Directed/signed edges, precision-recall, reference evidence scope |
| Adversarial perturbations | 15 | Inverted chronology, invalid contrasts, collapsed embeddings, missing markers, dependence |
| **Expanded synthetic suite** | **55** | Distinct task definitions; invariant failures can veto partial scores |

Three additional public-data tasks use measured E-MTAB-6798 RPKM peaks, changes and collection chronology, requiring a separately cached, hash-verified snapshot. The original three tasks remain available through `--suite legacy`.

## Empirical leaderboard: measured pilot only

A frozen v0.2 pilot ran four synthetic tasks, one per track, in both conditions. It used the existing Codex ChatGPT login, CLI 0.147.0, four requested phases and a 300-second total deadline per run. The model below was observed in each saved session context; the adapter itself did not report its model identifier.

| Observed model | Condition | Live attempts | Accepted finals / completed grades | Recorded passes | Failure | MCP attempts / successes |
|---|---|---:|---:|---:|---|---:|
| gpt-5.6-sol via Codex CLI | Baseline | 4 | 0 / 0 | 0/4 | All timed out | 0 / 0 |
| gpt-5.6-sol via Codex CLI | Assisted | 4 | 0 / 0 | 0/4 | All timed out | 21 / 0 |
| Claude 3.7 / 3.5 | Unmeasured | — | — | Unknown | Not run | Unknown |
| GPT-4o | Unmeasured | — | — | Unknown | Not run | Unknown |

The per-track baseline/assisted outcomes were each **0 completed grades out of 1 attempt** for `trajectory_bifurcation`, `batch_balanced`, `grn_activating_fork` and `adversarial_alias_trap`. This is not a completed 55-task leaderboard. Scores of zero are timeout bookkeeping, not completed biological accuracy. No harness advantage was established.

All assisted MCP calls returned `user cancelled MCP tool call`; the cancellation origin is unresolved. Participants also encountered missing packages in selected Python environments. The new local setup has not been tested in another live pilot and does not establish that these blockers are repaired.

The sealed local archive is `results/codex-live.gj3jlH/`: [inspection](results/codex-live.gj3jlH/inspection.md), [commands](results/codex-live.gj3jlH/commands.json), [raw runs](results/codex-live.gj3jlH/runs.json) and [seal](results/codex-live.gj3jlH/SEALED-PILOT.json). Results and data are ignored by Git and are not shipped in the wheel. The archive includes the unchanged v0.2 source/data snapshot for reproduction; future measurements must identify the new source version.

All **8 audit manifests verified against saved receipts**. Four baseline histories had no numerical calls to replay. Replaying the four assisted histories executed 21 calls locally (18 successes, 3 failures); all four histories mismatched the recorded transport cancellations. No model was invoked by replay. This mismatch does not demonstrate numerical nondeterminism.

| Condition | Last saved partial cumulative tokens | Peak sampled runner RSS (decimal MB) |
|---|---:|---:|
| Baseline | 2,048,408 | 682.5 |
| Assisted | 3,137,138 | 965.1 |

Token counters are incomplete observations from distinct sessions; complete totals and subscription dollar charges are **unknown**. These peaks are specific to this pilot and do not predict full-matrix memory requirements.

## Standalone execution report

```sh
project_dir="$(pwd -P)"
"${project_dir}/.venv/bin/devbench-report" /absolute/results.json \
  --output /absolute/report.html
# Optional supplemental evidence from exact recorded CLI sessions:
"${project_dir}/.venv/bin/devbench-report" "${project_dir}/results/codex-live.gj3jlH/runs.json" \
  --session-observations "${project_dir}/results/codex-live.gj3jlH/session-observations.json" \
  --output "${project_dir}/results/mac-local/pilot-report.html"
```

The HTML contains inline SVG/CSS/JavaScript and works offline. It displays track coverage, model/condition results, gates, failures, root receipts, fingerprints and available memory telemetry. New runs retain bounded, sampled RSS observations; the historical pilot has only phase/runner peaks and is displayed accordingly. Missing fitted curves, confidence intervals, lineage artifacts or telemetry are labelled unavailable. The report never manufactures them or reads private goldens to fill gaps.

Report-time manifest/node checks are labelled separately from full content-addressed artifact verification. Use `devbench-audit verify` for that full check. Moving a JSON ledger without its audit directory makes local DAG inspection unavailable; it does not erase its saved receipt.

## Live execution and CI

`make sweep` explicitly launches **110 Codex runs** (55 synthetic tasks × both conditions), with up to four phases each and normal subscription usage. It is never called by `make demo`, tests or CI. For a smaller live pilot, use:

```sh
project_dir="$(pwd -P)"
run_dir=$(mktemp -d "${project_dir}/results/codex-live.XXXXXX")
"${project_dir}/.venv/bin/devbench" --backend codex-cli --suite expanded --pilot 4 \
  --condition both --turns 4 --repetitions 1 --timeout-sec 300 \
  --validation-mode research --output "${run_dir}/runs.json" --audit-dir "${run_dir}/audit"
"${project_dir}/.venv/bin/devbench-metrics" "${run_dir}/runs.json" --output "${run_dir}/report.md"
```

CLI adapters use existing subscription login and normal approval rules, scrub API-key environment variables, and retain failed runs. Baseline withholds domain MCP; assisted registers the domain server. Use `--model` to select a model explicitly. Missing model IDs, usage or costs remain unknown. `--resume` requires matching code/runtime/input/task fingerprints; `--retry-failed` preserves history when rerunning failures.

The [GitHub Actions workflow](.github/workflows/ci.yml) configures Python 3.10/3.12 on macOS arm64 and Ubuntu x86_64, locked installation, all tests, synthetic generation and the offline oracle demo. GitHub currently documents `macos-latest` as arm64; an explicit architecture check catches label changes. See [runner documentation](https://docs.github.com/en/actions/reference/runners/github-hosted-runners). **Hosted CI has not been run**; no Git remote is configured locally. There is no passing CI badge or guaranteed three-minute runtime until hosted evidence exists.

## Statistical engine

`fit_temporal_trajectory` retains its five positional arguments and adds `selection_criterion`, `spline_dfs`, `data_type`, `calibration_max_genes` and `strict`.

- Raw integer counts use median-of-ratios/RLE offsets and PyDESeq2 gene-wise, trend and empirical-Bayes MAP dispersions. Normalization and calibration use a disclosed panel bounded to 16 MiB and 1,000 genes by default; reference IDs, panel selection, versions and optimizer outcomes are returned. This is RLE, not an implementation of TMM.
- Continuous, normalized or signed expression uses Gaussian splines. Automatic routing respects the storage dtype; floating RPKM is never passed to PyDESeq2 or silently count-normalized.
- Feasible spline df 2–5 candidates are compared by AIC/BIC; GCV is available for continuous data. Use `selection_criterion="fixed"` with one df for a prespecified analysis. Wald p-values condition on selected complexity, estimated size factors and dispersions; selection uncertainty is not included. BH correction covers the requested genes.
- Strict mode rejects failed mean, dispersion, candidate or Wald fits. Non-strict failed estimates retain explicit failure status and null p/q values. Recoverable numerical retries are recorded. Missing biological-unit provenance remains a warning; repeated specimen/subject/donor IDs block inference requiring aggregation or a repeated-measures model.
- Formula evaluation has an AST allowlist. Approved metadata factors and `C`, `cr`, `bs`, `I`, `Q` expressions are supported; arbitrary Python calls, imports, attributes and subscripts are rejected.

The returned method evidence follows [PyDESeq2](https://pydeseq2.readthedocs.io/en/stable/api/docstrings/pydeseq2.dds.DeseqDataSet.html) and the [DESeq2 methodology](https://link.springer.com/article/10.1186/s13059-014-0550-8). Small or unrepresentative reference panels and unknown specimen independence are material analysis limitations, not hidden defaults for a qualified clinical procedure.

## Tools

Use `ToolDispatcher` directly in notebooks. Start `devharness` only when a CLI client manages MCP stdio. Heavy numerical work remains in monitored multiprocessing `spawn` workers.

| Tool | Output |
|---|---|
| `validate_design_matrix` | Rank, aliases and condition number, with configurable time column |
| `fit_temporal_trajectory` | Selected spline, coefficients, effects, p/q values and detailed method diagnostics |
| `analyze_batch_effects` | Rank-aware partial variance decomposition and identifiability limits |
| `compact_temporal_state` | SVD variance, stage centroids, drivers and signed monotonicity under a conservative byte/token bound |
| `calculate_temporal_monotonicity` | Chronologically sorted strict direction and signed step agreement |
| `infer_lineage` | Bounded PCA/kNN diffusion geometry, rooted pseudotime, branches and cluster-tree approximation |
| `infer_regulatory_network` | Regularized lagged associations, scored signed edges and design diagnostics |
| `regulon_enrichment` | Hypergeometric/BH enrichment against a supplied TF-target reference |
| `motif_enrichment` | Enrichment from explicit complete foreground/background motif-hit data |
| `audit_expression_quality` | Dropout, library sizes, biological units, invalid contrasts, variance retention and embedding neighborhood checks |

QC thresholds are returned with the result and are configurable exploratory settings. A diffusion/cluster-tree approximation is not a validated lineage truth. Lagged association and curated TF-target membership do not establish direct causal regulation. Motif-hit enrichment requires supplied scan evidence; a TF-target database is not a promoter motif scan.

MCP file inputs must be absolute and remain inside the assigned workspace, including after symlink resolution. Direct notebook callbacks are trusted Python. The outer watchdog is resource supervision, not a hostile-code security boundary or an instantaneous OS memory cap.

## Immutable records and replay

Each run archives the task, staged inputs and final artifacts into content-addressed objects. Versioned canonical JSON records form an append-only execution DAG with parent hashes, UTC timestamps, asserted actor, source/runtime fingerprint, parameters and seeds. Failures and schema rejections are recorded. Changed staged inputs fail execution. Agent-reported metadata cannot replace the runner's audit or release state.

`metadata.audit` contains the manifest path and root hash. Retain that root independently of the archive. The directory owner can rewrite local files; verification against a separate trusted root detects rewriting. Local file modes are not WORM retention.

```sh
# Replace these values with a receipt returned by an actual run.
project_dir="$(pwd -P)"
"${project_dir}/.venv/bin/devbench-audit" verify \
  --manifest /absolute/audit/run/manifest.json --expected-root RECEIPT_SHA256
"${project_dir}/.venv/bin/devbench-audit" replay \
  --manifest /absolute/audit/run/manifest.json --expected-root RECEIPT_SHA256 \
  --output-dir /absolute/new/replay-directory
```

Replay restores hash-verified inputs and reruns only allowlisted numerical tools. It refuses uncaptured external paths and code/environment mismatches, and compares both successful results and expected errors. It never invokes an LLM. The analysis fingerprint excludes execution timestamps; the audit root intentionally includes execution identity and time. Neither implies that future LLM responses or arbitrary BLAS runtimes are bit-identical.

`--validation-mode clinical` holds all results for qualified independent review. External model adapters are blocked in clinical mode and for clinical, private, restricted or unclassified inputs. Staging cannot downgrade a restrictive label; added files require explicit classifications. These are declared/provenance-based controls, not automated PHI detection. Production identity, signatures, retention, change control and deployment validation remain external requirements.

## Public data and reference rights

The cached bulk file is the actual 121,114,240-byte `Mouse.RPKM.txt` from [E-MTAB-6798](https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-6798). The marker snapshot contains 96 genes × 35 embryonic Brain samples and verified symbol/Ensembl annotations. Public task definitions bind an exact snapshot manifest hash and portable ID. Set `DEVHARNESS_DATA_DIR` to the absolute parent directory containing the verified snapshot when using a different installation.

Processed-text, dense/sparse H5AD and 10X ingestion APIs are provided. The E-MTAB-6967 processed archive was discovered but not downloaded; no actual single-cell evaluation is claimed. Synthetic fallbacks are explicit in the legacy ingest path; public snapshot tasks fail when their real inputs are missing or changed.

TRRUST is cached with a checksum and source provenance. Its downloadable edge data carry a non-commercial research restriction; they are not vendored or licensed here for commercial clinical use. See [reference provenance](benchmarks/reference_data/README.md). Clinical reference qualification includes rights review, version control, population/tissue appropriateness and the limits of unmeasured negatives.

## Bounded public subsets and offline fallback

Only processed text is accepted by this CLI; raw sequencing archives are excluded. Read and persisted-output budgets have a hard 200 MB decimal cap. `--subset` defaults to the first 200 genes and first 60 eligible embryonic samples in source order. This is deterministic truncation, not a representative cohort sample.

```sh
project_dir="$(pwd -P)"
"${project_dir}/.venv/bin/devharness-ingest" --subset --max-genes 200 --max-samples 60 \
  --max-download-mb 200 --output-dir "${project_dir}/results/public-subset"
# Reuse a local processed file without network access:
"${project_dir}/.venv/bin/devharness-ingest" --subset \
  --input-table /absolute/Mouse.RPKM.txt --output-dir "${project_dir}/results/local-subset"
# Deliberately exercise the seeded synthetic fallback:
"${project_dir}/.venv/bin/devharness-ingest" --offline --subset \
  --output-dir "${project_dir}/results/offline-subset"
```

Use a fresh output directory each time. A download/parse failure also produces a clearly labelled synthetic fallback with its reason; check `source_kind` before treating the result as public observations. The provenance records selected IDs, byte budgets, actual source bytes read, output bytes and whether the source hash covers a full file or only a consumed prefix. HTTP accounting excludes TLS/network buffering. An ingest fallback cannot satisfy hash-bound public benchmark tasks.

## License and evidence boundary

Original code and documentation are [MIT licensed](LICENSE). Dataset, annotation and reference rights remain separate; see [NOTICE](NOTICE.md). No clinical data, cloud deployment, external outreach or additional live model sweep was part of the Mac revision. Local software verification is documented separately from the historical live pilot.
