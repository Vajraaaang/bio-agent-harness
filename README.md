> **Research release — clinical production is not qualified.** Read [release readiness and unresolved findings](RELEASE_READINESS.md) before use.

# DevHarness / DevBench

Transcriptomics research tools, controlled numerical workflows, and executable agent benchmarks.

**Status: research software. Clinical release is held.** The project implements statistical and record-integrity controls; it has not established clinical assay validity, qualified operations, or GxP/21 CFR Part 11 compliance. The [requirements and evidence map](docs/requirements-verification.md) states what is implemented, what was measured, and what remains missing.

[Methods and results](reports/selection-calibration/README.md) · [Controlled execution](docs/controlled-execution.md) · [Clinical qualification gaps](docs/clinical-production-goal.md) · [Colab notebook](notebooks/controlled_verification.ipynb)

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
