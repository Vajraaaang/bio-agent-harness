# Three-condition inference study

This development workflow measures whether DevHarness changes an agent's scientific output. It is separate from the historical `baseline`/`assisted` runner. Small local numerical/storage fixtures have run; live participants and full benchmark cohorts have not. See the [setup verification](../reports/benchmark-redesign/READINESS.md) for the current biological and public paths.

| Condition | Available analysis environment | Report acceptance |
| --- | --- | --- |
| Baseline | Ordinary installed Python libraries, a scientific question, supplied inputs and an answer schema | Common execution, input-integrity and output contracts |
| Optional | The same environment plus a documented coordinator tool client | Common contracts; no requirement or correctness reward for tool adoption |
| Enforced | The same ordinary environment and tool client | Common contracts plus applicable required checks and numerical claims bound to verified tool results |

All agents are blind to private reference answers, generator membership and the grading implementation. Blindness does not remove their pretrained knowledge, ordinary Python tools or common scientific instructions. Optional participants may ignore the tools, use only diagnostics, combine methods, or correct an analysis after a tool error. These behaviors must be observed in retained execution evidence; availability alone is not adoption.

## Prepare a plan without running data

Run the following from the checked-out development environment, replacing the output with a fresh absolute path outside system temporary directories:

```sh
python -m devbench.study.cli plan --stage smoke --output /absolute/project/results/proposed-study
```

`smoke`, `diagnostic` and `exploratory` plan 6, 36 and 216 model attempts respectively. These are scheduling counts, not power calculations or completed runs. The default plan intentionally has no generation seeds or requested model. `--model` can record a future exact model request without launching it. Keep the private plan, including its blinding nonce, under coordinator control. Never serve its directory to participants or publish it as a dashboard export.

The output separates `private/plan.json` and the schedule from `report/index.html`. The report opens offline, has no external scripts or assets, and shows unavailable scientific performance until genuine records are supplied. Table filters are explicitly labeled: they filter visible rows while figures retain their labeled full scope.

Inspect the CLI without executing any analysis:

```sh
python -m devbench.study.cli --help
python -m devbench.study.cli schema answer
python -m devbench.study.cli validate-plan --plan /absolute/project/results/proposed-study/private/plan.json
```

## Separate future execution steps

For an unscored permission check, use the [standalone `--preflight-only` mode](preflight-only.md). It probes one source task once per condition, writes a separate probe manifest, and cannot fall through to analysis or scoring. `--max-provider-sessions` now caps the combined probe/scored session count; the scored-attempt cap alone is not a usage cap.

Preparation and execution are distinct operations. The `prepare-data` command and normal scored `run` mode reject before reading inputs unless their explicit `--authorize-data-execution` flag is supplied. Standalone `run --preflight-only` instead requires `--authorize-exec-preflight` and never grants scored authorization. An execution-ready frozen plan must have assigned generation seeds and a pinned model. Dataset preparation retains input hashes and a separate hash of private reference truth. Public observations require their own source, measurement and provenance contract; unavailable public input never silently becomes synthetic data.

The coordinator uses the existing Codex CLI Code-as-Action execution path. It stages ordinary dependencies for every condition, supplies only the small client to optional/enforced participants, and keeps the broker and grading source outside participant access. The participant writes and executes `solve.py`, then produces `final_discovery.json`. A script, model statement or participant-written log alone cannot certify tool use.

The named permission profile must pass a standalone protected-file probe before a participant is dispatched. On the inspected macOS CLI, the minimal runtime profile grants ambient access to platform scratch directories. Therefore protected input bundles, private plans, coordinator receipts, participant workspaces and numerical-worker spools must be stored outside those directories. This is a protected-benchmark-asset boundary; it is not a claim of complete isolation from every host file. A failed probe blocks execution without enabling permission bypasses.

The executing coordinator must preserve failed and unlaunched attempts separately, including exact infrastructure errors. Declared resource limits, requested model identity, observed model identity and measurement coverage are distinct fields. Unknown cost or memory is not zero. Package versions and implementation hashes must be stable during measurement.

Broker analysis clocks now start once after successful permission preflight. Only the authenticated no-op handshake is available before activation. This removes preflight/analysis clock coupling; it does not extend the shared outer attempt deadline, which limits baseline, optional and enforced conditions alike. Optional and enforced tools share the same minimum-unit option floor of three. That is a common benchmark policy, not a claim of adequate biological replication. The [timing and policy assessment](../reports/benchmark-redesign/timing-and-policy-review.md) explains these distinctions and the CLI component pinning.

The standalone sandbox probe is not sufficient to authorize scoring. Each attempt additionally requires `run_exec_preflight` to pass using the scored adapter's actual `codex exec` command construction and environment. It uses dummy private/sibling files, protected-write and workspace-write controls, and restricted network/socket controls. Exactly one trusted script invocation must be observed in complete CLI records; model assertions and additional actions cannot satisfy the check. A failed or unavailable check blocks scored dispatch.

Future run commands require `--authorize-exec-preflight` as well as their existing execution flag; direct Python entrypoints require `allow_exec_preflight=True`. This explicitly authorizes up to one additional unscored provider session per attempt. The preflight has a 45-second ceiling within the existing outer attempt deadline, so setup failures and consumed preflight time remain visible. Its dispatch, usage and evidence files are separate from scored-participant accounting. No preflight model call is authorized merely by planning or by the local-fixture opt-in.

## Required temporal evidence

The current enforced temporal model uses `1 + numeric_time + C(batch)` as the input formula with an explicit candidate spline-df list and selection rule. The fitter expands the time term to centered natural splines. An explicitly supplied `cr` term must also use `constraints='center'`, avoiding an intercept dependency. This acceptance path requires the declared time column and a full categorical batch term; ordinary exploratory code remains available to every agent.

Required quality checks use fixed diagnostic thresholds and sampling settings. The matching design and quality checks must precede the fit. `fit_family` accounts for the complete declared gene family, uses a common bounded count-calibration panel, and recomputes BH adjustment across that family. Failed or untested estimates remain unavailable in the submitted answer rather than acquiring invented p-values.

`inspect_study_design` reads observable metadata and reports the suitability of the current independent-observation model. It never consults private generator truth. A refusal must cite the matching observation and state its blocking diagnostic codes. Biological-unit labels alone do not verify independence, and technical libraries do not increase biological replicate counts. Other appropriately validated models may support some repeated-unit designs; this tool does not supply them.

For reported gene estimates, the acceptance checker compares the method, input paths, family, normalization, time column, selection scope, p/q values, effect units, curves and actual reference batch with coordinator-retained results. The present tools return endpoint standard errors rather than validated confidence intervals; intervals cannot be invented and represented as tool-certified output. Supplemental lineage, regulatory and perturbation claims need their own qualified acceptance contracts. They are not accepted as evidence from this temporal fit alone.

Free-form interpretation and shape labels are not certified by a tool receipt. The independent scientific grader evaluates reference correctness separately. A report can pass workflow checks and still be scientifically wrong; a scientifically correct artifact can also fail enforced report acceptance. Both outcomes remain visible.

## Interpretation and remaining validation

The primary contrast is enforced minus baseline in raw scientific correctness over attempted runs, with optional comparisons reported separately. Correct-and-accepted reports are an additional outcome, so rejection costs and workflow enforcement do not silently redefine biological correctness. All assigned optional attempts belong in the comparison, including nonusers. Tool-user-only comparisons are descriptive because adoption is selected by the agent rather than independently assigned.

Repeated attempts on one dataset are not independent biological cohorts. Pairing requires matching inputs and runtime/model evidence; uncertainty calculations preserve cohort grouping and report insufficient evidence instead of fabricating narrow intervals. Synthetic recovery and false discovery metrics require known reference truth. Public concordance, mixture controls and observational labels have narrower scopes and must not be pooled as complete causal truth.

The current development recipes and research thresholds are uncalibrated. Static review, configuration tests, hashes and an empty dashboard do not demonstrate improved biological inference, calibrated error rates, realistic simulations, clinical qualification or regulatory compliance. See the [research review](../reports/benchmark-redesign/research-review.md) and [progress evidence](../reports/benchmark-redesign/progress.md) for the checked and deferred scope.
