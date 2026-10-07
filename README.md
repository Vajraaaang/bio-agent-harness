# DevHarness / DevBench

Transcriptomics research tools, controlled numerical workflows, and executable agent benchmarks.

**Status: research software. Clinical release is held.** The project implements statistical and record-integrity controls; it has not established clinical assay validity, qualified operations, or GxP/21 CFR Part 11 compliance. The [requirements and evidence map](docs/requirements-verification.md) states what is implemented, what was measured, and what remains missing.

[Methods and results](reports/selection-calibration/README.md) · [Controlled execution](docs/controlled-execution.md) · [Clinical qualification gaps](docs/clinical-production-goal.md) · [Colab notebook](notebooks/controlled_verification.ipynb)

The [extensible-methods backend](docs/extensible-methods.md) adds immutable method contracts for existing registered tools and custom OCI steps, branch runs, scientific previews, explicit revision-checked acceptance and replay. Access it through `devbench-analyze methods`. Custom execution remains exploratory until review; implementation trust is preserved separately.

The [scientific-method planner](docs/scientific-method-planner.md) generates candidates before revealing the registry, preserves unavailable options, checks observed preconditions, freezes trial diagnostics and separates recommendations from scoped approvals and experimental recipes. Access it through `devbench-analyze methods planner`; model, network and data execution each require explicit authorization.

## RNA-seq Analysis Workbench

The [research-use workbench milestone](reports/rna-workbench-completion/2026-09-22/README.md) connects questions, explicit input/design admission, proposals, operator review, controlled analysis, evidence packets and recorded scientific review. Its [support matrix](docs/analysis-episodes.md) covers bounded bulk count/continuous contrasts and donor-aware cellular counts, alongside the separate backed scRNA workflow. e11 remains an unfinished integration case; these small local checks do not establish live-agent benefit or scientific calibration.

Use the [local scRNA interface](docs/workbench-ui.md) for registration, reviewed execution, results and verified exports. Bulk and cellular contrast workflows use the [episode interfaces](docs/analysis-episodes.md). [Scientific-review packets](docs/scientific-review.md), the [current status ledger](docs/rna-workbench-status.md) and [offline evaluation preparation](docs/workbench-evaluation.md) state their evidence and authorization limits. No live study is authorized by preparing a configuration.

## Three-condition study under development

**Latest measured result:** the [revised six-slot Codex pilot](results/scientific-preparation-2026-09-14/proposed-pilot/revised-run-review/report.md) completed six analysis sessions and six permission preflights, with **zero accepted answers**. Optional participants made no trusted tool calls; enforced participants made 20 actual calls across repeated executions. This did not establish biological accuracy or harness benefit. The [subsequent seam remediation](reports/benchmark-redesign/seam-remediation-2026-09-15.md) adds execution versions, last-successful binding, common pre-submission validation and full offline fixture-agent tests. Study receipts have an explicit [integrity-only scope](docs/study-evidence-scope.md); numerical replay is not a supported study capability.

The [corrected launch runbook](reports/benchmark-redesign/six-slot-pilot-runbook.md) pins the verified CLI build, prepares evaluation cohorts only, and pauses after the first worker for a receipt-bound coordinator decision. The same process continues; restarting the command is not a resume operation. Its historical authorization/status text predates the measured pilot linked above; every new live cycle still needs a bounded execution scope.

The [review-design revision](reports/benchmark-redesign/qc-rubric-change-rationale.md) replaces the unexecuted proposal with two cohorts per case and two repetitions, retains independent reviewer ratings, and reports agreement, blinding and cutoff sensitivity. See the separate [custom instruction snapshot](reports/benchmark-redesign/custom-participant-instructions.md). The revised 24-slot/48-session proposal requires a new execution budget; no runs are authorized or queued.

The [instrument follow-up](reports/benchmark-redesign/instrument-followup.md) adds cohort-level Monte Carlo SE, repeated-run outcome variance, explicit small-pilot limits, and hash-bound human reasoning adjudication. Quantitative success alone is not presented as a reviewed biological insight. These are offline instrument changes; actual repeatability and case-specific detection floors remain unmeasured.

Standalone unscored permission checks are reachable through [`--preflight-only`](docs/preflight-only.md) on all three study run commands. The earlier [live validation](reports/benchmark-redesign/live-permission-validation.md) includes successful baseline, optional and enforced permission receipts, preserved genuine socket failures, and verified CLI-reported model identity. The subsequent six-slot pilot is separate and produced no accepted scientific answers.

Optional [LangSmith tracking](docs/langsmith.md) exports redacted traces after measurement. Prepare a local preview with `devbench-study langsmith-export`, review its payload, and explicitly authorize `langsmith-upload` with that payload's SHA-256. Existing run commands perform no uploads, and no LLM evaluator is enabled.

The [benchmark setup verification](reports/benchmark-redesign/READINESS.md), [biological configuration dashboard](reports/benchmark-redesign/biology-dashboard-ready/index.html), and separate [public reference dashboard](reports/benchmark-redesign/public-dashboard/index.html) describe configured coverage. Their empty performance views are not the latest measured pilot report. The biological catalog has 97 configured cases; public results remain separate.

The [scientific preparation assessment](reports/benchmark-redesign/scientific-preparation.md) documents corrected enrichment/background and motif-reference handling, explicit interval availability, complete-family BH bookkeeping, and inspectable model selection. It includes executed local checks and the frozen plan subsequently used for the measured six-slot pilot.

The [robustness research assessment](reports/benchmark-redesign/robustness-research.md) addresses the independent review's permission and scientific concerns. Current study launches require an explicitly authorized `codex exec` permission control and verified runtime model identity before scoring. Probe usage is recorded separately. Evidence applies to the recorded local CLI/runtime; new scored attempts still require their own checks.

The `0.5.0.dev0` development path adds a separate comparison of **baseline**, **optional DevHarness**, and **enforced DevHarness**. Baseline participants receive ordinary Python analysis libraries. Optional participants may use, combine, or ignore the supplied tools. Enforced reports require applicable coordinator-recorded checks and numerical claims that agree with the tool results they cite. Historical `assisted` runs retain their original guided-assistance meaning.

The project has executed **small local synthetic numerical/storage fixtures**, unscored permission probes, and the scored-slot synthetic pilot described above. It has not established a successful biological-agent comparison or statistically analyzed the real public cohorts in this study path. Processed public source formats and hashes were checked. The [research review](reports/benchmark-redesign/research-review.md) distinguishes methodological support from empirical calibration and biological validation.

Create configuration and an empty report only, using a fresh absolute output path outside system temporary directories:

```sh
python -m devbench.study.cli plan --stage smoke --output /absolute/project/results/proposed-study
```

The command writes a private plan and a separate public report. Keep the private plan, preparation receipts and grading references inaccessible to participants. Generation seeds and the model are deliberately unassigned in the default plan. `prepare-data`, `run`, and analysis of nonempty records require their explicit authorization flags; planning does not imply permission to execute them. The broader orchestration and numerical paths remain under review.

The [three-condition workflow guide](docs/three-condition-study.md) documents the evidence boundaries. A separate [plan-only Colab notebook](notebooks/three_condition_study.ipynb) prepares the same empty report from an existing repository checkout; it has not been executed in Colab for this revision.

## What the project does

| Workflow | Behavior | Evidence boundary |
| --- | --- | --- |
| Research SDK | Bounded Zarr/HDF5/CSV access; rank and biological-unit checks; Gaussian splines or RLE/empirical-Bayes NB count fits; AIC/BIC/GCV selection where supported | Observational research inference; warnings and failures are part of the result |
| Controlled Python execution | Prespecified matrices, contrasts and panels; two configured signing keys; source/environment/input identities; independently checked output contracts | Synthetic qualification checks use fixture keys, not authenticated clinical operators |
| Native count candidate | Pinned R/DESeq2/edgeR procedures, signed plans, normalization references, idempotent submission and recovery | Native interval undercoverage remains an unresolved finding |
| Agent evaluation | Isolated task staging, actual Codex script or MCP execution, deterministic grading, preserved failures | Software/agent performance, not biomarker validation |

The benchmark contains 66 definitions: 55 original expanded synthetic scenarios, five observed-temporal questions, three expanded public tasks and three legacy tasks. The five added questions reuse two seeded fixture variants; they are not five independent biological cohorts. They verify actual stage means, signed transitions and reported classifications, including flat and transient negative controls. A curated twelve-task track selects existing tasks across trajectory, batch, lineage and adversarial mechanisms. Earlier task definitions and thresholds remain unchanged. Private grading references and coordinator source must never be staged for participants.

## Run locally

Python 3.11+ is required; the default Mac setup uses Python 3.12. From the repository root:

```sh
make install           # uv-locked environment; pip fallback is explicitly unlocked
make test              # software tests; no live model calls
make controlled-demo   # real synthetic analyses, signed-plan checks and repeats
```

The controlled demo also compares results with R and MASS. Install R separately before using it as a complete check; missing references are reported and do not become passing validation. The R NB comparison shares fitted dispersions and offsets, so it does not independently validate empirical-Bayes estimation. Results are preserved in a fresh `results/controlled-demo.*` directory.

Make sets single-thread BLAS defaults before Python starts. Numerical workers require OS permission to inspect their processes; unavailable monitoring fails explicitly. The multiprocessing supervisor bounds resources and is not a security VM.

## Use the SDK

```python
import os
for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "1"

from pathlib import Path
import devharness as dh

if __name__ == "__main__":
    workspace = Path.cwd().resolve()
    result = dh.fit_trajectory(
        workspace / "expression.zarr",
        workspace / "metadata.csv",
        "1 + C(batch) + numeric_time",
        genes=["gene_a", "gene_b"],
        time_col="time",
        data_type="continuous",
        selection_criterion="fixed",
        spline_dfs=[3],
    )
    print(result["genes"])
```

Use the actual genes, time column and prespecified design for your experiment. Raw counts require a count model. Removing a confounded covariate does not recover an identifiable batch-adjusted effect. Repeated specimen, subject or donor identifiers block ordinary independent-observation inference. SDK paths stay within `DEVHARNESS_WORKSPACE_ROOT`, defaulting to the current directory; SDK traces are explicitly unauthenticated workspace records.

The 0.4.12 revision introduced opt-in [Gaussian selection Monte Carlo inference](docs/gaussian-selection-inference.md), with separate conditional F diagnostics, BH/BY adjustments, reference hashes and strict failure handling. It assumes independent homoskedastic Gaussian errors and does not provide selection-adjusted endpoint intervals. Default Gaussian fitting now refuses numerically unresolved residual variance. Final package and regression verification are recorded separately from the preceding prototype study.

The 0.4.13 candidate enforces cumulative byte and member limits while preserving controlled inputs, including changes during reading/copying. Directory destinations are reserved exclusively. These controls preserve existing artifact encodings; they do not impose a deadline on an individual filesystem operation or establish hostile-host isolation.

Version 0.4.14 adds the [observed-temporal task checks](reports/temporal-verification/README.md). These connect the existing strict monotonicity grader to five shipped tasks and independently gate the numerical values behind each classification. Their recorded deterministic software controls are separate from live model performance and clinical evidence.

Version 0.4.15 binds each legacy evaluation to a privately retained, normalized task snapshot and freezes its validation mode for the run. Restored evidence uses the shared regular-file reader, enforces captured byte sizes while copying, and publishes verified files without overwriting competing destinations. The [audit verification record](reports/audit-verification/README.md) separates the reproduced defects, software checks and version-specific cloud evidence. These controls do not establish hostile-host isolation, qualified retention or clinical release authority.

Version 0.4.16 adds a census of visible Python distribution name/version metadata to legacy capture and replay admission. This closes version-drift gaps for design, validation and storage dependencies omitted from the earlier eight-package subset. The [runtime identity record](reports/runtime-identity/README.md) states the checked scope: version metadata does not attest installed files, loaded modules or native-library builds.

## Inspect the measured results

The [portable Gaussian selection study](reports/selection-calibration/README.md) includes all 36 summary rows, 186 saved numerical arrays, the frozen prototype, protocol, figure and standalone verifier.

![Gaussian null rejection with unequal-variance failure visible](reports/selection-calibration/null-calibration.svg)

In that prototype study, corrected null rejection ranged from 5.045% to 5.225% under equal variance. Under unequal variance it remained 8.610%–9.065% at nominal 5%. Criteria share simulated vectors; intervals are pointwise and conditional on their reference pool. These measurements belong to the preserved prototype, not the subsequent packaged API.

```sh
PROJECT_ROOT="$(pwd -P)"
"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/reports/selection-calibration/verify.py"
```

This checks internal consistency. Add `--expected-manifest-sha256` with an independently retained hash of `SHA256SUMS` to anchor the check. Hashes detect changes relative to an anchor; they do not establish identity, event completeness or qualified retention.

Other retained evidence includes native interval undercoverage, an eight-run live Codex pilot with eight deadline failures and no accepted final answer, and successive software corrections. See the [evidence map](docs/requirements-verification.md), [live execution record](docs/v04-verification.md) and [historical overview](docs/history-v0411.md). Software tests, local protocol checks, live model performance and clinical evidence have separate denominators.

## Codex and Google Colab

`make pilot` launches four real Code-as-Action tasks through an existing Codex login and uses normal account quota. `make sweep` launches the curated twelve-task suite. `make demo` runs privileged offline oracle controls and is not live agent performance. The legacy `devbench --backend codex-cli` path remains available for historical protocol/replay compatibility.

Two self-contained notebooks are provided:

- [Controlled verification](notebooks/controlled_verification.ipynb): synthetic numerical/control checks without a model login; R reference availability is reported.
- [Agent benchmark runner](notebooks/colab_runner.ipynb): staged tasks and Code-as-Action execution. Colab needs its own Codex login; credentials are not copied from the Mac.

Rebuild locally with `make notebook` or the scripts in `scripts/`. A built notebook is not a completed cloud run. Three earlier Colab CLI connection attempts failed before cells executed. A subsequent browser-operated **0.4.12** controlled notebook completed 1,389 selected tests with one platform skip, four numerical analyses and verified archive retrieval; see the [cloud evidence summary](reports/colab-verification/README.md). That run predates the 0.4.13 input-preservation fix. Setup, per-task watchdogs and infrastructure delays preclude a guaranteed under-90-second run.

## Qualification and publication

TMM is not implemented by the RLE routine. The bounded PyDESeq2/statsmodels count path is not claimed identical to the full R DESeq2 pipeline. Lineage and regulon results do not establish direct causality or binding. An approved intended use, locked clinical analysis plan, representative assay evidence, independent clinical validation, administered human identity and qualified deployment remain required.

GitHub Actions defines macOS/Ubuntu tests on Python 3.11/3.12; hosted CI has not been established by local runs. Preserve raw results and review the intended repository visibility before publishing coordinator/grader material. No GitHub destination or remote publication is assumed.

Original code is [MIT licensed](LICENSE). Dataset and reference terms are described in [NOTICE.md](NOTICE.md). No clinical or regulatory certification is claimed.

Version 0.4.17 integrates the primary Code-as-Action runner with the existing execution DAG. Original staged inputs and SDK source files are privately captured before execution; observations, failures and outcomes are retained with a root receipt. The [primary audit record](reports/code-action-audit/README.md) distinguishes captured evidence from participant claims. Restoring inputs does not execute scripts, and numerical tool replay is unavailable for this path. Clinical admission and release remain held.
