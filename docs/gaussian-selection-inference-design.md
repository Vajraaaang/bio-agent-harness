# Experimental Gaussian selection inference

This revision will expose the executed selection-aware Monte Carlo method through the research trajectory API. It will not change default inference, native count procedures, controlled fixed-plan contracts, benchmark definitions or historical measurements. Clinical release remains disabled. The source study demonstrates near-nominal rejection under its Gaussian assumptions and persistent failure under unequal variances.

A separate, reproduced admission defect will also be corrected: v0.4.11 returns warned F-test values for an exact nonconstant nuisance-only Gaussian response with no usable residual variance. Default formulas and valid-input results remain unchanged; such unresolved Gaussian fits will fail explicitly. Resolved small-scale data must remain admissible. The preserved baseline probe records both original outputs.

## Contract

Append an optional inference mode to fit_temporal_trajectory and the SDK wrapper. The default remains conditional inference with the existing serialized result meaning. The new mode is available only for continuous Gaussian data with a fixed candidate set, criterion and seed. Count data, nonfinite inputs, nonidentifiable designs, condition numbers above1,000 and incompatible null spaces must fail explicitly. A candidate failure cannot be silently removed from Monte Carlo selection even when ordinary partial-result mode is requested.

The mode records draw count, seed, generator, effective chunk size, design identity, reference-array identity, p-value resolution and assumptions. Simulation occurs in the existing supervised numerical worker with thread/resource bounds. References are shared across the requested gene family and are generated once per call.

## Statistical implementation

Use QR projection for both observed and simulated Gaussian selection statistics. The same score calculation and deterministic df tie rule must apply to both sides of the rank test; parity with the actual fitted model is a separate check. Do not mix a statsmodels observed statistic with a numerically different simulation algorithm and then call them exchangeable.

Every candidate must contain the same reduced nuisance space. Under the stated independent homoskedastic Gaussian model, location/scale invariance removes nuisance parameters. Repeat AIC/BIC/GCV selection on each simulated null vector and compute the inclusive plus-one lower-tail rank of the selected conditional F probability. Avoid sample-by-sample square projection matrices and bound simulation block allocation.

The new mode returns the calibrated temporal-test p-value with an explicit Monte Carlo distribution/method label. Preserve the selected model's conditional F statistic, probability and degrees of freedom separately. Selected coefficients and endpoint estimates remain descriptive/conditional; the correction does not produce selection-adjusted confidence intervals.

Retain the named BH calculation across the requested family. Also expose a separately named Benjamini–Yekutieli adjusted value for arbitrary-dependence use when the marginal-p assumptions hold. Neither computation is a clinical FDR qualification claim. Monte Carlo discreteness and shared-pool dependence must be disclosed; never fabricate probabilities below the attainable resolution.

## Verification before delivery

- Meaningful tests for constant/invalid inputs, unsupported counts, rank/null-space/conditioning guards, ties, finite ranks and repeatable seeds.
- Same-kernel observed/reference comparison, location/scale invariance, and sampled parity with the executed prototype/actual worker.
- Actual supervised API, SDK and MCP calls for the new mode; unchanged default/fixed/native behavior checked by the existing suite.
- A new frozen synthetic integration diagnostic with explicit unequal-variance stress; earlier source, seeds, results and failures remain preserved.
- Package/notebook consistency and independent method/code review before claiming the revision delivered.

This is a software/method component toward the full platform objective. Intended-use calibration, negative-binomial interval coverage, independently administered approval, qualified operations and clinical evidence remain separate requirements.
