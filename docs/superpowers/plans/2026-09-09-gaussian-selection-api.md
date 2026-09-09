# Gaussian Selection Inference Implementation Plan

> For agentic workers: use subagent-driven-development for the isolated kernel task, followed by spec and code-quality review. The coordinator owns integration and final verification. Never edit the preserved diagnostic, benchmark definitions, grading thresholds or earlier result directories.

**Goal:** Deliver an opt-in, explicitly experimental Gaussian selection-aware temporal test through the supervised API and SDK.

**Architecture:** Put projection, selection and Monte Carlo reference generation in one numerical module. Keep integration narrow: only the new mode uses that kernel, while default/fixed/native behavior retains its current serialized meaning. Preserve the v0.4.11 source/results as the comparison baseline.

**Tech stack:** NumPy QR/PCG64, SciPy F tails, existing Patsy designs, statsmodels selected fits, existing worker supervision and MCP/SDK contracts.

## Baseline and responsibilities

There is no Git HEAD or remote. Before-source copies and hashes are retained in results/gaussian-selection-api-2026-09-09.yti04ore/. No commit or worktree identity is invented. Work in the authorized repository with explicit ownership.

Kernel implementer owns devharness_server/selection_mc.py and tests/test_selection_mc.py only. The coordinator owns tools_stat.py, stat_models.py, integration/protocol tests, documentation and packaging. Reviewers are read-only except their own review reports. Each worker must preserve other workers' changes.

## 1. Projection and simulation kernel

- [x] Add GaussianSelectionMC(designs, criterion, draws=65535, seed=0).
- [x] Accept the existing mapping from df to (formula, design DataFrame, diagnostics). Sort by df. Criteria are aic/bic/gcv. A single metadata-feasible candidate is permitted.
- [x] Validate draw counts as nonboolean integers in1..1,000,000 and seeds in0..2**64-1. Require finite full-rank designs, positive residual df, conditioning at most1,000, at most10,000 rows and128 columns. Validate actual matrices, not only caller-supplied diagnostic flags.
- [x] Identify temporal columns using Patsy's term slices. Check common reduced-space rank and mutual containment across candidates without constructing sample-by-sample square projectors. Support an empty reduced space.
- [x] Use one private batch kernel for observed and simulated values. Derive Gaussian RSS, maximized log likelihood including variance, AIC/BIC/GCV, temporal F statistics and F-tail probabilities. Select by (criterion score, df). Reject nonfinite or numerically unresolved residual variance; clip only roundoff-sized negative nested improvements.
- [x] evaluate(y) returns selected_df, candidate_scores keyed by df, conditional_p_value, conditional_f_statistic, conditional_df_num, conditional_df_denom and p_value. Candidate scores have the same aic/bic/gcv/log_likelihood/num_parameters meaning as the existing worker.
- [x] Use explicit Generator(PCG64(seed)), with default blocks of1,024 draws reduced when necessary to keep at most2,000,000 sample-by-draw-by-candidate elements in working arrays. Record the effective block size.
- [x] Build a null reference lazily on the first informative evaluation and reuse it within the object. Apply (1 + count(reference_probability <= observed_probability))/(draws +1), including ties. Never return a Monte Carlo p-value below the attainable floor.
- [x] metadata() returns schema/method, status, requested/completed draws, seed/generator, block size, observation/model dimensions, model-matrix hashes, little-endian float64 reference hash, resolution and explicit assumption/qualification limits. Before any informative evaluation, status is not_run and no completed reference is claimed.
- [x] Test draw/seed/type/rank/null-space/conditioning bounds, constant/unresolved inputs, positive finite ranks, ties and deterministic seeds. Check nuisance/scale invariance and comparison with independent least-squares/RSS calculations for each selected-model df. Keep test simulations bounded.
- [x] Run focused tests and obtain spec compliance followed by code-quality review before integration.

## 2. Supervised API and SDK integration

- [x] Append inference_mode, monte_carlo_draws and monte_carlo_seed to the public function without changing its original positional arguments. Append them after categorical_columns in the private worker to preserve controlled callers.
- [x] Default inference_mode is conditional. Preserve the old default parameters/results and avoid sending new default-only SDK arguments to an older server.
- [x] New mode name is gaussian_selection_mc. Reject counts explicitly before count calibration. Reject fixed criterion with this mode; fixed analytic F inference remains available through conditional mode. Reject unused nondefault Monte Carlo controls in conditional mode.
- [x] Create the kernel once from metadata-feasible designs. Retain biological-unit diagnostics and upstream repeated-unit refusals.
- [x] For informative genes, use the kernel's canonical projection scores to select the model and to populate the new mode's candidate scores. Compare the selected statsmodels conditional F result with the projection result using declared numerical tolerance. Any discrepancy or candidate failure fails explicitly, including when ordinary partial mode is requested.
- [x] New-mode primary p_value is the Monte Carlo rank probability. Set test_distribution to selection_monte_carlo, test_statistic to the ranked conditional probability, and primary test degrees of freedom to null. Preserve the conditional F probability/statistic/df separately. The legacy wald quadratic remains identified as conditional.
- [x] Keep selected coefficients and endpoint estimates unchanged in meaning. State explicitly that their SEs are conditional and no selection-adjusted interval has been produced.
- [x] Keep BH padj/q_value across the exact requested family; add a separately named by_q_value using the harmonic-factor Benjamini–Yekutieli adjustment. State their distinct dependence assumptions and retain clinical/FDR qualification as unestablished.
- [x] Constant genes keep their existing no_information status and neutral convention; they do not trigger a claimed informative test or a fabricated simulation record.
- [x] Give the new mode a distinct statistical method version and reference metadata. Default/fixed/native outputs keep their current method identifiers and numerical semantics.
- [x] Verify new mode through the private worker, supervised public API, SDK and actual local stdio MCP session. Test counts/configuration refusals and strict failure propagation. Compare default/fixed results against preserved v0.4.11 outputs.

## 3. Prospective integration evidence

- [x] Freeze final source, dependency records, protocol, seed schedule and numerical tolerances in a fresh result directory before measurement.
- [x] Exercise all four selectable spline df values with purpose-built software fixtures. Label these as targeted parity fixtures, not calibration samples.
- [x] Reproduce the declared projection/reference method with actual new-mode API calls. Retain unequal-variance failure evidence and avoid claiming clinical or negative-binomial calibration.
- [x] Check reference identities, p-value floor/ranks, BH/BY arithmetic, default behavior, signed controlled records and all available new audit receipts.
- [x] Run the appropriate full suite once after final changes; repeat only for new failures/changes. Build a fresh package environment and both notebooks, checking source identities and actual new-mode calls.

## 4. Presentable, publication-ready handoff

- [x] Finish the public snapshot verifier's independently reproduced gaps, then retest unchanged, tampered and malformed copies. No mutation of original evidence.
- [x] Rewrite the project front page around clear capabilities, setup and a real controlled demo; preserve detailed history separately.
- [x] Create a readable offline evidence page and portable results index with visible statistical and clinical limitations.
- [ ] Finish clean desktop/mobile screenshot proof. Documented commands, all local links, responsive DOM geometry and bounded export checks passed; only normal-viewport visual proof is clean because temporary viewport screenshots had compositor artifacts.
- [x] Prepare GitHub-ready artifacts and exact commands. Destination/visibility remain requested; no remote push or clinical qualification is inferred.

The latest user instruction prioritizes careful code updates and verification over rushing publication. The twelve-hour handoff target does not override these gates or the full clinical-production objective.

Completed local software and results checkpoint on 2026-09-09. Responsive DOM geometry and normal-viewport visual checks are distinguished in page-ui-verification.json; temporary viewport screenshots had compositor artifacts and were not used as clean visual proof. The results-only export was verified from the extracted ZIP. GitHub destination/visibility and clinical qualification remain open. No push, hosted CI or new Colab run is claimed.
