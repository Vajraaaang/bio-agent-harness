# Gaussian spline-selection diagnostic

The selection-aware Monte Carlo prototype produced null rejection rates of **5.045%–5.225%** in the tested equal-variance Gaussian settings, compared with **7.710%–10.235%** for the selected-model conditional F tests. Under unequal variances, corrected rates remained **8.610%–9.065%** at a nominal 5% threshold.

**This is an experimental method, not a production inference change or clinical qualification.** The unequal-variance result remains adverse evidence.

The stress case deliberately varies the error standard deviation from 0.5 to 4 across time. It is an assumption-violation diagnostic, not an estimate of failure frequency in typical clinical cohorts.

![Null rejection rates for conditional and selection-aware methods, with unequal-variance failures visible](null-calibration.svg)

The figure uses [results.csv](results.csv). Each interval is pointwise and covers evaluation uncertainty conditional on its reference pool; it does not include all calibration-pool randomness. The three criteria reuse simulated data and are not independent replication counts.

## Study and result files

The fixed protocol uses 24, 48 and 96 independent observations, six timepoints, two balanced batches, and candidate spline df 2–5. Each design has 65,535 null-reference vectors and four scenarios with 20,000 evaluation vectors each: equal-variance null, unequal-variance null, a linear signal and an oscillatory signal. This is 196,605 reference vectors and 240,000 evaluation vectors, reused across AIC, BIC and GCV.

- [Complete summary](summary.json): all 36 method/scenario/design rows, scope and measurement limits.
- [Protocol](protocol.json) and [prototype source](selection_mc.py): fixed before the main evaluation.
- [Numerical arrays and design matrices](data): stored reference and evaluation statistics.
- [Source mapping and original execution roots](provenance.json): repository-relative locators and hashes.
- [Snapshot verifier](verify.py): checksum, F-tail, rank-p-value, count, interval and CSV checks.

No clinical data or model participant was used. A first main launch stopped on a macOS process-tree permission error before saving numerical output. Its failed observer remains in the local archive. A separate driver-only observer then completed the study; aggregate peak memory is unknown. The successful command took 16.75 seconds, including startup and supervision. Maximum sampled driver RSS was approximately 251 MiB; this is not the full process-tree peak.

The main source freeze contains 95 files. Independent review recomputed all 36 rows. A fresh same-environment replay matched all 186 arrays across 15 NPZ files, and the semantic summary matched after excluding elapsed time. Three locally retained execution DAGs verified against their saved roots. These checks do not establish qualified external retention or authenticated human approval.

Worker parity is sampled: the main study compared 45 gene/criterion outputs with the actual supervised worker and observed selected df 2 and 3. Preflight also exercised df 5; those comparisons did not exercise selected df 4.

## Statistical meaning

Each candidate contains the same nuisance-design space. Under independent homoskedastic Gaussian errors, adding a nuisance mean and multiplying by a positive scale changes every candidate residual sum of squares by a common factor. AIC/BIC selection gains a common additive constant, GCV a common multiplier, and the F statistics remain unchanged. The complete selection procedure therefore has a nuisance-free null distribution for these fixed designs.

The prototype simulates that complete procedure, then uses an inclusive lower-tail rank:

**p = (1 + number of reference selected-model p-values ≤ the observed value) / (B + 1).**

This is marginal over selection, not conditional on a particular selected df. Monte Carlo rank-test arguments require the stated exchangeability/pivotality conditions; a generic bootstrap using fitted nuisance parameters would not inherit that finite-sample justification. The extra one prevents a zero Monte Carlo p-value, and ties are conservative.[1][2]

With B=65,535, the p-value floor is 1/65,536. At nominal 5%, the attainable rejection grid is 4.998779%. Across independent continuous-null reference pools, the conditional rejection probability has a central 95% range of approximately 4.833%–5.167%. That is separate from the reported conditional evaluation intervals and is not a simultaneous or clinical acceptance bound.

Unequal variances violate the pivotal model used here. Neither the p-value calculation nor passing arithmetic checks establishes robustness to that violation, correlated observations, multiple-testing/FDR control, negative-binomial dispersion uncertainty or endpoint-interval coverage. Production use would require an appropriate variance/correlation model and independent intended-use qualification.

## Verify this snapshot

After installing the repository's locked Python environment, run from the repository directory:

    PROJECT_ROOT="$(pwd -P)"
    "$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/reports/selection-calibration/verify.py"

For an anchored check, also pass the independently retained SHA-256 of SHA256SUMS with the verifier's --expected-manifest-sha256 option. Without that argument, the command checks internal consistency only. It does not contact a model service or load pickled arrays.

The complete original CAS/DAG archives are retained locally and are not all included in this portable snapshot. This export allows recomputation from stored statistics; it does not independently reconstruct every original execution event. Original failures and acceptance limits are preserved.

## Sources

1. Jean-Marie Dufour. [Monte Carlo tests with nuisance parameters: a general approach to finite-sample inference and nonstandard asymptotics](https://jeanmariedufour.github.io/Dufour_1995_MCT_W.pdf). Author preprint, 2005; published in Journal of Econometrics, 2006.
2. Belinda Phipson and Gordon K. Smyth. [Permutation P-values Should Never Be Zero](https://gksmyth.github.io/pubs/PermPValuesPreprint.pdf). Statistical Applications in Genetics and Molecular Biology, 2010; corrected preprint, 2011.

The numerical findings come from the frozen synthetic study and its recorded replay. They are not claims about LatchBio, clinical assay performance or regulated deployment.
