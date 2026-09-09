# Historical project overview through 0.4.11

This page preserves the previous front page as historical context. Use the [current overview](../README.md) and [requirements map](requirements-verification.md) for current scope. Historical test totals and environment availability are dated observations.

# DevBench / DevHarness 0.4.11

**Clinical-readiness decision: research prototype, not qualified for clinical production.** A source-grounded audit reproduced and corrected a small-sample Gaussian inference problem and three execution-evidence defects. Adaptive spline selection remains miscalibrated in the audited null simulation. Assay/clinical validation, authenticated records, and deployed operational qualification remain missing. See [clinical readiness and evidence](../docs/clinical-readiness.md).

Version 0.4.2 adds a [controlled fixed-plan executor](../docs/controlled-execution.md): immutable signed specifications, two distinct configured approval keys, locked input/code/environment hashes, explicit biological units and normalization panels, preserved input execution and coordinator-owned DAG evidence. This path has no LLM or generated-code execution and refuses restricted clinical inputs. The [synthetic Colab verification notebook](../notebooks/controlled_verification.ipynb) tests these controls without a Codex login. Clinical release remains blocked.

Version 0.4.3 adds controlled-workflow verification: `devharness-controlled verify` now requires explicit signer trust and checks the approval/event sequence, plan/input/environment/result links, accepted result structure, convergence, normalization, uncertainty and probability contracts. Use `verify-integrity` for the original hash-only check. The [active clinical-production goal](../docs/clinical-production-goal.md) tracks the remaining statistical, identity, operational and clinical qualification work; this release does not establish clinical readiness.

Version 0.4.4 introduces a separate [native count-analysis candidate](../docs/native-controlled-execution.md): signed fixed matrices and panels, pinned edgeR/DESeq2 procedures in a local container, preserved runtime evidence and independent checks of count validity, engine-specific RLE normalization, native test tails and full-family BH. It remains unqualified for clinical use. The original frozen pilot retained 20 usable edgeR cohorts and 20 DESeq2 serialization failures; the documented diagnostic repair does not turn those historical failures into successful runs.

Version 0.4.5 adds [operated native submission and recovery](../docs/operated-native-execution.md): an idempotent local registry, exclusive dispatch/recovery leases, a container-local deadline and separate recovery evidence. Real synthetic tests cover coordinator SIGKILL, duplicate requests, orphan timeout and a lost terminal commit. A separately frozen 240-run diagnostic matrix completed without missing results; its small-cell uncertainty does not establish clinical calibration. Clinical release remains disabled.

Version 0.4.7 adds [signed study authority policies](../docs/approval-authority.md), registry-bound policy history, revocation checks through observed container start, and governed recovery. Independent review exposed and drove fixes for weak Ed25519 keys, extra SQLite schema objects, unsafe export paths, ambiguous publication failures, expiry checks and false authority claims in failed histories. Python 3.11+ is required by the SQLite limit APIs. These are software controls; human identity, qualified deployment and clinical approval remain unestablished.

Version 0.4.8 adds [prespecified normalization references](../docs/reference-normalization.md) to new signed, governed DESeq2 plans. The reference identities, annotation reference and selection rationale are bound to the plan; missing or nonpositive references cause explicit refusal. The verifier reconstructs the factors from the declared references and preserved counts. This enables a reference-set workflow but does not establish biological stability or repair the interval-coverage finding below.

Version 0.4.11 fixes an evidence-reader hang: on Mac/Linux, a named pipe now reaches the existing file-descriptor type check without waiting for a writer. Seven adversarial probes reproduce the original block and the corrected rejection. The correction does not bound ordinary filesystem I/O or establish clinical readiness. The [verification record](../results/evidence-reader-2026-09-08.eb4h4yej/report.md) retains the original failures and the scope of subsequent checks.

Version 0.4.10 corrects [temporal evidence validity](../docs/requirements-verification.md): empty/singleton trajectories and malformed estimates cannot receive a passing temporal grade. Single-stage compact summaries report an undefined score explicitly, and distinct numeric times no longer collapse through display formatting. The full local suite passed 1,734 tests. No shipped task currently configures the dedicated temporal grader, so this change does not establish improved benchmark scores or clinical calibration. Historical task definitions, thresholds, fitting procedures and measured results remain unchanged.

**Adverse statistical evidence:** a separate frozen 4,000-cohort study of the [DESeq2 endpoint intervals](../docs/native-effects.md) retained 3,990 accepted analyses and ten timing-verification failures. The higher-dispersion setting covered 1,868/2,000 selected effects (93.4%; exact 95% CI 92.22-94.45%) and triggered its prespecified undercoverage warning. The other setting covered 1,884/1,990 (94.67%) but had ten missing outcomes. This candidate cannot be called calibrated or clinically validated. Original inputs, inference rules and failed results remain preserved; the status collector now exposes CLI/verifier failures separately from candidate numerical output. See the [current qualification gaps](../docs/clinical-production-goal.md).

A fresh 512-cohort mechanism diagnostic then separated normalization from dispersion estimation. Generating normalization factors largely removed average bias, but nominal interval coverage remained unestablished even with generating nuisance parameters. All 2,048 arm records passed arithmetic checks and two fresh replays matched; these checks do not establish clinical calibration. The reference parameters are synthetic controls, not a deployable correction. The [normalization and interval findings](../docs/native-effects.md#current-adverse-evidence) remain release blockers.

The actual 0.4.8 reference-feature comparison subsequently completed 1,024 paired arm attempts over 512 new cohorts: 986 numerical completions and 38 explicit zero-reference refusals. Mean signed error moved toward zero while observed paired MSE and interval width increased. All 1,024 receipts and 89,365 input hashes passed independent checks; four later-selected verification-only replays matched exactly except elapsed time. The [current evidence report](../output/pdf/clinical-reference-normalization-review.pdf) preserves the conditional denominators, uncertain coverage comparison and remaining qualification requirements.

A [public dependency advisory assessment](../results/supply-chain-audit-2026-09-08/report.md) matched four CVE families in the preceding FastMCP/DiskCache environment. Version 0.4.9 now uses the official MCP SDK directly and removes that unused dependency tree. Raw tool arguments are validated before coercion, and successes retain matching JSON text and structured content. A fresh locked environment passed 1,670 local tests; its OSV query matched no advisories across 77 installed public Python packages. These checks do not establish vulnerability freedom or qualify native/OS dependencies. Long synchronous tools retain their existing protocol-responsiveness limitation. The previous measured inputs, failures, runtime sources and packages are preserved; see [migration evidence](../results/mcp-sdk-migration-2026-09-08/report.md).

**Test biological judgment through executed Python, with explicit failures and deterministic grading.**

DevHarness is an importable transcriptomics SDK. DevBench stages a clean Python environment, asks a Codex participant to write and run `solve.py`, and grades the resulting `final_discovery.json`. Syntax alone is insufficient: task gates cover chronology, confounding, biological units, lineage topology and valid p/q reporting.

The primary path uses ordinary Codex workspace script execution with automatic approval review and a retained workspace-write sandbox. It registers no domain MCP server. Permission, authentication, quota and runtime failures remain possible and are recorded. No approval/sandbox bypass flag is used.

## Local Mac quickstart

```sh
make install     # uv-locked native Python 3.12 environment and editable SDK
make test        # unit/integration checks; no live model inference
make demo        # three privileged offline oracle controls, not model performance
make pilot       # FOUR live Codex tasks, one per track; normal subscription usage
make sweep       # twelve live curated tasks, 90-second budget per task
make notebook    # rebuild the self-contained Colab notebook; no upload/inference
```

`make install` prefers uv; the pip fallback is explicitly unlocked. Make targets set one-thread BLAS defaults before Python starts and preserve explicit overrides. Native arm64 is checked on Apple Silicon. Direct scripts can source `.env.example` before importing numerical libraries. Resident memory is sampled using psutil on macOS/Linux; unavailable monitoring fails closed in supervised workers.

A Codex ChatGPT login must already exist. `codex login status` checks it without exposing credentials. The installed CLI used during development is 0.147.0. The adapter's `--approve-for-me` option routes eligible approvals through automatic review while keeping workspace restrictions; it does not promise every operation will be approved. See [official noninteractive execution](https://developers.openai.com/codex/non-interactive-mode) and [configuration reference](https://developers.openai.com/codex/config-reference).

## Python SDK

```python
import devharness as dh

if __name__ == "__main__":
    audit = dh.validate_design("metadata.csv", "1 + numeric_time + C(batch)")
    if not audit["is_full_rank"]:
        raise ValueError("Design is aliased; diagnose identifiability before fitting")

    result = dh.fit_trajectory(
        "counts.zarr", "metadata.csv", "1 + cr(numeric_time, df=3) + C(batch)",
        genes=["Sox2", "Pax6"], selection_criterion="bic",
    )
    print(result["genes"])
```

The dataset determines the appropriate formula and genes. Removing a fully confounded batch term does not recover an identifiable batch-adjusted effect. `numeric_time` is derived from the named time column, defaulting to `embryonic_age`.

| SDK entry | Purpose |
|---|---|
| `open_expression`, `read_metadata` | Bounded Zarr/HDF5/CSV access and metadata |
| `validate_design` | SVD/QR rank, aliases and condition number |
| `fit_trajectory` | RLE/empirical-Bayes NB counts or Gaussian splines; AIC/BIC selection and BH q-values |
| `compact_manifold` | Bounded temporal summary |
| `audit_quality` | Units, chronology, contrast and embedding diagnostics |
| `infer_lineage` | Rooted pseudotime and branching approximation |
| `save_discovery` | Finite, locally schema-validated, atomic JSON artifact |

Numerical results preserve the original implementation's warnings and inference limits. Endpoint predictions are not synthesized into per-stage means. SDK paths resolve within `DEVHARNESS_WORKSPACE_ROOT`, defaulting to the current directory. Use a main guard for spawned numerical workers. SDK receipts in `.devharness_trace.jsonl` are bounded **participant-written traces**, not authenticated host telemetry.

## Execution architecture

```text
Coordinator: frozen task definitions + private references
    |
    +-- stage data/schema + TASK_README + SDK cheatsheet
    +-- clean .venv: third-party dependencies + runtime SDK only
    |     (no editable project hook, coordinator modules or grader source)
    |
    +-- codex exec --approve-for-me (selects workspace-write + automatic review)
    |       write solve.py -> execute explicit Python -> final_discovery.json
    |
    +-- capture actual CLI command completion and script/answer hashes
    +-- check frozen inputs + original schema + deterministic task/invariant gates
    +-- retain scripts, failures, answers, JSON ledger and SHA-256 file receipts
```

Script existence alone is insufficient. The adapter requires a successful direct invocation of the staged interpreter and matching script/answer hashes captured at command completion. Later artifact changes fail this check. This is bounded execution evidence, not proof against malicious host processes. Third-party dependency symlinks avoid repeated installations; the participant environment excludes coordinator imports but is not an OS filesystem read-isolation boundary.

The primary `devbench.runner.CodeActionRunner` writes a compact flat record. The legacy `devbench_engine` and ordinary MCP adapter remain available for existing interfaces and historical audit replay. Old CAS/DAG archives are not rewritten or used as substitutes for live results.

## Twelve curated tasks

The catalog references existing definitions without changing their data generators, private expected values or grading thresholds. Three distinct mechanisms are selected per track, and a four-task pilot selects the first task from every track.

| Track | Selected mechanisms |
|---|---|
| Trajectory | Linear trajectory, continuous noise, stage gap |
| Batch | Balanced batch, partial confounding, complete aliasing |
| Lineage | Bifurcation, early branching, trifurcation |
| Adversarial | Inverted chronology, invalid contrast, zero inflation |

The original 55 synthetic tasks and three hash-bound public tasks remain accessible through the legacy `devbench` command. The curated twelve are a selection, not twelve newly validated biological cohorts. Their exact catalog is [catalog.json](../devbench/tasks/catalog.json).

```sh
python scripts/run_live_benchmark.py --backend code-action --tasks 4 --timeout-sec 90
python scripts/run_live_benchmark.py --prepare-only /absolute/new/benchmark_data
```

Results are saved incrementally under a fresh results directory. Every failure is retained. Staging consumes part of the task budget; bounded process cleanup and reporting can extend observed wall time. Runtime targets and nonzero pass rates must be measured, not asserted in advance.

## Google Colab

Upload [notebooks/colab_runner.ipynb](../notebooks/colab_runner.ipynb) to Colab and run its five stages. The notebook embeds a deterministic source bundle, so no GitHub remote is required. It configures thread limits, installs the editable project, stages twelve synthetic tasks under `/content/benchmark_data`, and uses the same code-action runner with a 60-second task budget and workspaces under `/content/workspace`.

Fresh Colab requires its **own Codex login**. Run `codex login --device-auth` and complete the browser verification yourself when device authentication is available for your account. No Mac credentials or API keys are copied. An unavailable login or blocked command produces an explicit blocker, not a mock model result. Twelve 60-second budgets, package installation and staging have no under-90-second guarantee. Local notebook/source verification is not a claim that a Colab runtime was executed.

The cell output contains Task ID, track, condition, outcome, runtime, RSS and grader reasoning. Exports are `/content/devbench_results.json`, `/content/devbench_results.html` and a separate SHA-256 receipt file. The companion [scripts/colab_run.py](../scripts/colab_run.py) has `# %%` cell delimiters and can also run as a CLI script.

An optional E-MTAB-6798 processed subset uses a ten-second download budget and 200 MB cap, with an explicitly labelled synthetic fallback. That optional data is kept separate from the fixed curated synthetic tasks; it cannot silently replace a hash-bound public benchmark snapshot.

## Evidence and limits

The sealed earlier pilot at `results/codex-live.gj3jlH` measured **v0.2**: eight live gpt-5.6-sol sessions, eight deadline failures, zero accepted final answers, and 21 cancelled assisted MCP calls. Cancellation origin was not established. Token observations were partial cumulative counters, not complete billing totals. v0.3 subsequently passed 429 local software tests; those tests did not rerun the pilot. See [Mac verification](../docs/mac-validation.md) and [historical v0.3 documentation](../docs/legacy-v03.md).

New v0.4 verification and live outcomes are recorded separately in [v0.4 verification](../docs/v04-verification.md). A nonzero pass count, if observed, is not clinical validation or proof of a causal benefit over the older configuration. Code, task selection, prompts, environment and deadlines differ.

GitHub Actions configures macOS arm64 and Ubuntu x86_64 with Python 3.10/3.12. Hosted CI has not run locally; no Git remote is configured. [MIT](../LICENSE) applies to original code; datasets and references retain their own terms, including TRRUST restrictions described in [NOTICE](../NOTICE.md). Intended use is research/software evaluation. No regulatory-compliance claim is made.
