# Experimental Gaussian selection inference

Executed local checks are summarized in [API verification](../reports/api-verification/README.md). The [offline evidence page](index.html) presents the separately preserved prototype study.

The research trajectory API offers an explicit `gaussian_selection_mc` mode. It repeats the complete AIC, BIC or GCV selection rule in simulated Gaussian null vectors, then ranks the observed selected-model probability against that reference. Default conditional inference and controlled fixed-plan contracts remain available. **Clinical release remains held.**

The [preceding prototype study](../reports/selection-calibration/README.md) found near-nominal null rejection under its equal-variance Gaussian assumptions and persistent excess rejection under unequal variances. That preserved study used a different simulation layout from the packaged mode. Its measurements are not a performance result for the new API or evidence of robustness to assumption violations.

## Use through the SDK

Run this as a script with an existing dataset in the assigned workspace. Specify independent biological units in metadata; repeated specimen, subject or donor identifiers block fitting.

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
        selection_criterion="bic",
        spline_dfs=[2, 3, 4, 5],
        inference_mode="gaussian_selection_mc",
        monte_carlo_draws=65535,
        monte_carlo_seed=0,
    )
    print(result["selection_adjustment"])
```

Choose the hypothesis family, formula, candidates, criterion, draw count and seed before inspecting the corresponding outcomes. The example is an interface demonstration, not a prespecified clinical analysis plan. Zarr, supported HDF5 and bounded gene-by-sample CSV stores are accepted. Counts must use a count model; declaring integer counts continuous does not validate a Gaussian error model.

The public MCP tool `fit_temporal_trajectory` accepts the same three new options. SDK calls use supervised Python execution and produce unauthenticated workspace traces; actual MCP calls produce protocol activity. Neither trace format alone authenticates a clinical operator.

## Results and mathematical scope

| Field | Meaning |
| --- | --- |
| `p_value` | Inclusive plus-one Monte Carlo rank probability for the complete fixed selection rule |
| `test_distribution` | `selection_monte_carlo` |
| `test_statistic`, `test_tail` | Selected conditional F probability, ranked in the lower tail |
| `conditional_p_value`, `conditional_f_statistic` | Conditional selected-model F diagnostics |
| `conditional_df_num`, `conditional_df_denom` | Degrees of freedom of that F calculation |
| `padj`, `q_value` | Benjamini–Hochberg adjustment across exactly the requested genes |
| `by_q_value` | Separately named Benjamini–Yekutieli adjustment across the same family |
| `endpoint_inference_scope` | Endpoint estimates and SEs remain conditional; no selection-adjusted interval |
| `selection_adjustment` | Method, generator, draw accounting, reference and matrix hashes, numerical policy and assumptions |

For B reference draws, `p = (1 + count(reference <= observed)) / (B + 1)`. Ties are included and probabilities cannot fall below `1/(B+1)`. The selected model minimizes `(criterion score, df)` with a fixed df tie rule. Both observed and simulated statistics use the same Householder residual calculation. A separate check compares the selected fitted model's conditional F calculation with that kernel; disagreement fails explicitly.

The argument depends on a fixed design with a common reduced nuisance space and independent homoskedastic Gaussian errors. Under those assumptions, nuisance shifts and common scale changes leave selection and F probabilities invariant. [Dufour's Monte Carlo test treatment](https://jeanmariedufour.github.io/Dufour_1995_MCT_W.pdf) and the [Phipson–Smyth corrected preprint](https://gksmyth.github.io/pubs/PermPValuesPreprint.pdf) explain the underlying pivotality and rank-test conditions. These arguments do not qualify generic plug-in bootstrap methods or floating-point implementations automatically.

One simulated reference is shared within a call. BH dependence conditions are not established merely by that implementation. BY has a broader dependence scope when marginal p-values satisfy the required conditions; neither adjustment repairs invalid marginal tests. See the [SciPy adjustment documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.false_discovery_control.html). No clinical FDR claim is made.

## Refusals and reproducibility

- Draw counts are nonboolean integers from 1 through 1,000,000; seeds are integers from 0 through `2**64-1`.
- This mode accepts continuous Gaussian data and AIC/BIC/GCV. Count data and `fixed` criterion are refused. Use the default conditional mode for prespecified fixed-design analytic F inference.
- Designs must be full rank, have positive residual degrees of freedom and condition number at most 1,000. The kernel bounds inputs to 10,000 observations and 128 design columns.
- Nonfinite or numerically unresolved residual information, incompatible reduced spaces and candidate/reference failures cause explicit refusal. Failed candidates are not dropped from simulated selection, even with `strict=False`.
- Constant expression retains an explicit `no_information` convention. An all-constant request does not claim a simulated reference or an informative temporal test.
- Reference generation uses PCG64 in draw-major order and bounded blocks. Interruption marks that reference failed and re-raises the interruption; an implicit retry cannot publish a partial or different pool.

Record the complete response and the source/environment identity. Matrix hashes encode little-endian float64 arrays and shapes; reference hashes encode probabilities in generation order. Metadata specifies the exact encodings and numerical-kernel version. Exact repeats are expected only within a verified numerical environment; a fixed seed alone does not establish cross-platform equality.

The residual-resolution diagnostic is conservative practical arithmetic checking, not a formally certified floating-point bound. Default Gaussian fitting also rejects unresolved residual variance, correcting previously returned warned F values for exact nuisance-only responses. Valid-input analytic formulas retain their existing meaning.

Unequal variance, correlated errors, repeated-measures inference, negative-binomial dispersion uncertainty, endpoint-interval coverage, clinical calibration and qualified deployment remain separate requirements. The [requirements map](requirements-verification.md) tracks those gaps.
