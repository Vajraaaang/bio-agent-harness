# Prespecified native endpoint effects

The new `controlled_native_analysis/2` plan adds one explicit DESeq2 contrast to the native joint temporal test. It is a candidate method for qualification, not a clinically qualified analysis. Existing `controlled_native_analysis/1` plans and `native_nb_result/1` records retain their original interpretation.

The plan must specify `effect_name`, `effect_reference_sample_id`, `effect_comparison_sample_id` and `effect_confidence_level`. The two IDs must identify observed rows in the signed full design, comparison time must be later, and every non-temporal covariate must match exactly. The contrast is the comparison design row minus the reference row. It describes a difference between fitted population means at those settings, not a paired change in an individual patient. No contrast is chosen from the results, and no extrapolated design row is accepted.

Only `deseq2_lrt` supports this interval protocol. No supported native edgeR QL contrast-SE/CI API was identified in the pinned4.10.5 source. Its QL statistic is a scaled likelihood-ratio statistic; dividing logFC by its square root is not a justified native standard error.

## Native procedure and result meaning

The primary LRT and full-family BH calculation are retained without modification. The driver then calls the exported `DESeq2::results` API with the fixed numeric contrast, `test="Wald"`, `lfcThreshold=0`, `minmu=.5`, `independentFiltering=FALSE` and `cooksCutoff=FALSE`. In DESeq2 1.52.0 this uses saved LRT coefficients, normalization and dispersions. Numeric contrast evaluation invokes the native covariance code with `maxit=0`; no coefficient or dispersion refit is performed. The driver checks that the saved numerical state remains unchanged.

The native API returns a log2 fold-change and standard error. The driver constructs the nominal pointwise normal-Wald interval using those values and the signed confidence level. These bounds are locally constructed; they are not returned by a native confidence-interval function. They use plug-in normalization and dispersion estimates and do not establish simultaneous trajectory coverage, prediction bounds, selection-adjusted inference, causal effects or clinical validity.

The `native_nb_effect_result/1` envelope contains:

- `primary`: the complete original `native_nb_result/1` joint-test record, with its p/q values and diagnostics.
- `effects`: the separately identified contrast, exact row/name mapping, native coefficient vector, estimate, SE, interval, convergence-related failure status and method evidence.
- An aggregate status. An incomplete effect record cannot become a completed analysis, but its primary test information is preserved.

**Primary p/q values test the joint temporal null. They do not test the displayed endpoint contrast.** The secondary Wald p-values are not exposed as if they were the primary tests. The endpoint-neutral/nonconstant validation generator explicitly exercises this distinction.

## Numerical and diagnostic safeguards

`betaPrior=FALSE` means no learned LFC-shrinkage prior. The pinned native implementation still uses a fixed log2 coefficient variance of1e6 for numerical regularization. The legacy primary metadata label `coefficient_prior="none"` refers to the former API choice, not literal zero regularization. The new effect method evidence records the fixed variance explicitly. `lfcShrink` is not used; its posterior SD would have a different inferential meaning from this SE.

The native numeric-contrast API has a special zero-count convention that can overwrite an LFC with0 for selected all-zero groups. The driver records that condition and refuses to publish it as a usable biological zero or confidence interval. Failed/unknown primary fits, nonfinite estimates, nonpositive SEs, malformed row mappings and nonfinite interval bounds also prevent accepted effects. Warnings and truncation are retained.

The controlled verifier recomputes the contrast from preserved matrices and checks nuisance/time constraints, native coefficient order, estimate/interval arithmetic and native covariance. With `B=X'WX`, it reconstructs the pinned ridge sandwich `inverse(B+Lambda) B inverse(B+Lambda)`, including the natural-log/log2 conversion and `minmu=.5`. This is an arithmetic-consistency check using fitted values; it is not an independent refit or a coverage guarantee.

## Executed verification and remaining uncertainty

### Current adverse evidence

The later 0.4.8 reference-feature comparison completed 1,024 attempts over 512 new cohorts, with 986 numerical completions and 38 explicit zero-reference refusals. Mean signed error moved toward zero but paired MSE and width increased. Low-dispersion selected coverage was identical at 243/256; high-dispersion paired coverage differed by +0.917 percentage points over 218 complete pairs, with an approximate 95% Monte Carlo interval -1.643 to +3.478. Accepted-only reference coverage and all-cohort whole-panel coverage have different denominators. All 1,024 receipts, 31,552 complete effect records and four fresh verification-only replays were checked. The reference implementation therefore does not establish a coverage or clinical-performance improvement. See `results/reference-coverage-2026-09-08/` for the frozen data, independent recomputation and selection-timing caveat.

The later frozen 4,000-cohort precision study retained 3,990 accepted outputs and ten temporal-verification failures. In the n48/dispersion 0.8 setting, the selected-effect interval covered 1,868/2,000 effects (93.4%; exact 95% CI 92.22-94.45%), triggering the prespecified undercoverage warning. Original source, thresholds, failures and outputs remain preserved in `results/native-precision-2026-09-07/`.

A separate prospective mechanism diagnostic completed 512 fresh cohorts with four paired arms each. Generating normalization factors largely removed average effect bias; generating dispersion alone did not. This did not establish interval calibration: the n24 arm using both generating nuisance parameters covered 233/256 selected effects (91.02%; pointwise 95% CI 86.82-94.22%). Its paired coverage difference from the estimated/estimated arm was uncertain, so the diagnostic does not establish that it is a worse method. Generating values are synthetic reference controls unavailable to clinical analyses, not a deployable correction.

All 2,048 arm records passed numerical checks, and two verification-only fresh replays matched exactly apart from elapsed times. Those software checks do not repair undercoverage. No estimator, clinical acceptance threshold or new reference set was selected from these results. The approved estimand/reference scale and whole-procedure uncertainty still require independent qualification. See `results/interval-diagnosis-2026-09-08/main-01/analysis-summary.json`, the independent result review, and the qualification requirements in the same evidence directory.

### Earlier implementation checks

The first real signed comparison used one preserved synthetic cohort. Primary gene results and evidence were identical with/without effect extraction; repeated effect records matched; a nuisance-mismatched endpoint pair was rejected. An independent public `DESeq()` pipeline matched32 contrast estimates and SEs exactly, with interval differences below1.1e-15. Those checks establish implementation parity, not coverage.

A separately frozen200-cohort coverage diagnostic completed under `results/native-effects-2026-09-07/coverage-01`, without retries or changes during measurement. It uses independently generated exact-model-space curves and one prespecified uniformly sampled hypothesis per cohort for a binomial coverage assessment.

| Synthetic setting | Accepted / attempted | Sampled effect covered | Exact95% coverage interval | Prespecified warning |
|---|---:|---:|---:|---|
| 24 samples, dispersion0.2 | 100/100 | 92/100 (92%) | 84.84–96.48% | No warning; not acceptance |
| 48 samples, dispersion0.8 | 99/100 | 94/99 (94.95%) | 88.61–98.34% | Unassessable because one output is missing |

All-gene mean coverage among accepted cohorts was93.75% and92.42%; genes within a cohort are not independent Monte Carlo replicates. The second setting's missingness bounds for the sampled-effect coverage proportion are94–95%. These descriptive results and wide intervals do not qualify nominal coverage. The diagnostic used80 genes,32 hypotheses, balanced two-batch fixed designs and strong log4 generating curves; it does not represent an approved assay or all clinical cohort structures. No clinical acceptance margin was invented.

Independent postchecks verified200 audit receipts,400 configured-key fixture signatures and the effect covariance/semantics of199 accepted outputs. One cohort remains failed with `Native output/log limit exceeded`; its saved84,458-byte numerical result is not admitted into coverage. A deterministic test reproduced a temporary-file unlink race capable of that false rejection. The original generic error did not identify which file-check branch fired, so that attribution remains an inference. Cleanup also recorded an observed inspect/kill exit race; the stopped container was subsequently verified and removed in a separate record.

The original aggregate seal has one mismatch: the runner appended its summary to redirected `run.log` after sealing. The original sealed log prefix matches. Both the original seal and full log are preserved; `SEAL-final.json` separately seals18,211 files against the saved root `5a0e78bc86302d94181687f420ece912775e26fa39531ff45211280b9056d53b`. This locally retained root is not qualified external retention.

Version0.4.6 corrects the live temporary-file observer, rechecks the exact owned container after a failed kill, and prints the final summary before sealing. Tests retain refusal of symlinks, nonregular/oversized output, disappeared final output, excess names and still-running or changed container identities. Revisions do not replace the frozen failed attempt. Remaining work includes larger intended-use coverage/calibration studies, stress cases and independent statistical qualification.

Primary sources: [DESeq2 results manual](https://bioconductor.org/packages/release/bioc/manuals/DESeq2/man/DESeq2.pdf#page=49), [LRT result-column interpretation](https://bioconductor.org/packages/release/bioc/vignettes/DESeq2/inst/doc/DESeq2.html#i-ran-a-likelihood-ratio-test-but-results-only-gives-me-one-comparison), and pinned DESeq2 1.52.0 `R/results.R`, `R/core.R`, `R/fitNbinomGLMs.R` and `src/DESeq2.cpp` retained with the native package sources. The source audit and actual execution have different evidence scopes.
