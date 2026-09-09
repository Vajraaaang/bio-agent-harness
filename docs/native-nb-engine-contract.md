# Native NB candidate engine contract

Status: **candidate for qualification; clinical release is not authorized**. This
entrypoint implements specified native procedures. It does not establish error-rate
calibration, biomarker validity, authenticated human approval, protected retention,
or qualified production operation. It admits only declared synthetic/public data.

The trusted entrypoint is `devharness_server/r/native_nb.R`. There are no submitted
formulas, code strings, callbacks, RDS objects, Python-fitted dispersions/offsets,
package installation, network calls, interactive prompts, or model-service calls.
The coordinator must invoke the script inside the pinned, resource-limited runtime
with read-only input mounts, a fresh writable output directory and no network.

## Invocation and responsibility boundary

```sh
Rscript --vanilla /opt/devharness/native_nb.R \
  --config /workspace/input/request.json \
  --output /workspace/output/result.json
```

All paths must be absolute and canonical. Input symlink resolution must not change
the supplied paths. The output parent must exist; the output cannot already exist.
The script writes only its output and a same-directory temporary JSON file. Normal
success/failure publication uses `file.link(temporary, destination)` followed by
removal of the temporary name. Hard-link creation atomically refuses an existing
destination, including one created by another writer after the initial check.
There is no rename/copy fallback: a filesystem that cannot support this operation
causes an explicit publication failure. Existing regular files and dangling
symlinks are rejected; a missing path is correctly distinguished from `NA` returned
by `Sys.readlink`. Crash recovery, filesystem durability, hostile same-user races
against parent directories, artifact hashes, root receipts, plan signatures and operating-system
isolation remain coordinator responsibilities. Abrupt process termination cannot be
represented by an output the terminated process never wrote: the supervisor must
preserve its own explicit failed/interrupted attempt record.

Exit code 0 means every requested hypothesis has a usable native result. Exit code
1 means rejection, native failure, a partial hypothesis failure, or publication
failure. Publication never replaces an existing destination. With valid CLI
arguments and a usable fresh output path, input/model errors are saved as JSON. If
the output path/CLI is invalid, structured failure is written to stderr. A missing
`jsonlite` dependency has a fixed minimal JSON failure path; it never triggers an
installation. The parent must inspect status and diagnostics, not exit code alone.

## Request: `native_nb_request/1`

```json
{
  "schema_version": "native_nb_request/1",
  "engine": "edger_qlf",
  "counts_csv": "/workspace/input/counts.csv",
  "metadata_csv": "/workspace/input/metadata.csv",
  "full_design_csv": "/workspace/input/full_design.csv",
  "reduced_design_csv": "/workspace/input/reduced_design.csv",
  "hypothesis_genes": ["g001", "g002", "g003"],
  "temporal_columns": [2, 3, 4],
  "normalization": "rle",
  "data_classification": "synthetic"
}
```

For `engine: "deseq2_lrt"`, add exactly one field:
`"deseq2_fit_type": "parametric"` or `"mean"`. It is mandatory for DESeq2 and
forbidden for edgeR. No other fields are accepted. JSON duplicates and nonfinite
numeric values are rejected. Arrays remain arrays even for one hypothesis/index.
The data classification can only be `synthetic` or `public_processed`; a declaration
is not an independent privacy classification. Upstream admission remains required.

The signed coordinator plan must bind this complete request, raw inputs, ordered
hypothesis family, design matrices and basis-construction provenance, runtime image
digest, driver digest and dependency lock. This driver validates the supplied
numeric matrices; it does not prove that a supplied column is the intended spline
basis, that time units are correct, or that unique IDs denote independent organisms.
No adaptive design selection is performed.

### CSV inputs

| Input | Exact contract |
|---|---|
| Counts | First header `gene_id`; subsequent headers are unique sample IDs. One unique gene per row; raw finite nonnegative integer values. No rounding or count conversion. |
| Metadata | Exactly `sample_id,biological_unit_id,numeric_time` in that order. Unit IDs must be unique, nonblank and not missing-value placeholders. Numeric times must be finite, with at least `number of temporal columns + 1` distinct values. |
| Full/reduced designs | First header `sample_id`; subsequent headers are unique identifiers matching `[A-Za-z][A-Za-z0-9_]*`. All matrix entries must be finite numbers. First design column is named `Intercept` and is exactly 1 for every sample. |

Sample order must be **identical** in counts headers, metadata rows and both design
row-ID columns; automatic reordering is forbidden. Hypotheses must occur in the
input panel and retain request order in the output. Identifiers must have no
leading/trailing whitespace, control characters or duplicates and must be at most
256 bytes. Required numeric fields cannot be blank or padded with whitespace.

`temporal_columns` uses **one-based design indices excluding the sample-ID column**.
Indices must be increasing, unique, contain 1–5 terms, and never include the
intercept. The reduced matrix must equal the full matrix with precisely these
columns removed, including names and order. Both must be full rank with positive
residual degrees of freedom and spectral condition number at most 1000. SVD rank
uses `machine epsilon × max(matrix dimensions) × largest singular value`; the
rank difference must equal the number of temporal columns. No covariate is dropped
to rescue collinearity.

### Resource and representation bounds

- Request: at most 1 MiB. Counts CSV: at most 64 MiB. Each auxiliary CSV: at most
  8 MiB. Inputs are read with row limits before native fitting.
- At most 5000 panel genes, 100 requested hypotheses, 512 samples and 32 columns per
  design. Counts must have at least three sample columns.
- The numeric count matrix is at most 16 MiB (`genes × samples × 8`). CSV parsing,
  native fits and intermediate objects require additional memory; **16 MiB is not
  a process-memory guarantee**. The external supervisor enforces actual memory and
  wall-clock limits.
- edgeR accepts integers through `2^53 - 1`; DESeq2 is limited to R's signed integer
  maximum, 2147483647. The DESeq2 conversion occurs only after range/integrality
  checks. Larger values fail; they are never rounded or overflowed intentionally.
- Zero-total libraries and panels without any gene positive in every sample fail.
  There is no positive-count/iterative normalization fallback. All-zero genes stay
  in the declared family; an all-zero requested hypothesis is explicitly failed.
- BLAS/OpenMP-related thread environment variables are set to 1 at script startup,
  before native package loading. The caller must set them before starting R too;
  this script cannot retroactively change a library initialized by an R startup
  mechanism. Use `--vanilla` and the qualified runtime.

## Locked native procedures

The driver refuses versions other than **R 4.6.1**, **edgeR 4.10.5** for `edger_qlf`,
or **DESeq2 1.52.0** for `deseq2_lrt`. It emits actual selected-package, R/platform,
limma, BiocParallel and jsonlite versions. Version strings alone are not an image
or dependency lock; the coordinator retains the resolved immutable runtime identity.

### edgeR QL F

1. Native `DGEList` and `normLibSizes(method="RLE")` use the complete input panel.
2. `glmQLFit` uses the supplied full design, `dispersion=NULL`, `legacy=FALSE`,
   `robust=TRUE`, `abundance.trend=TRUE`, `winsor.tail.p=c(0.05,0.1)`,
   `keep.unit.mat=FALSE`, and `prior.count=0`. Native default top-proportion selection
   for the common NB dispersion is part of this locked method and is recorded.
3. Native `glmQLFTest(coef=temporal_columns)` supplies the joint QL F statistic,
   numerator/denominator df and p-value. Its v4 working dispersion is estimated
   within edgeR; no per-gene PyDESeq2 alphas are supplied.
4. An **additional diagnostic-only native reduced `glmFit`** uses the exact reduced
   matrix, full-fit offsets/weights, `dispersion / average.ql.dispersion`, and
   `prior.count=0`. This duplicates the hidden reduced-fit specification inside
   `glmQLFTest` to expose available flags; its results do not replace the native test.

Native algorithms include the documented v4 QL bias adjustments and optimizer
paths. They are preserved, not rewritten. Known full/reduced failures, nonfinite
fit values and invalid tests fail the affected hypothesis. Crucially, edgeR's
one-way fast path does **not expose** per-gene convergence flags, and not every
intermediate dispersion fit exposes convergence telemetry. The driver returns
`converged: null` with an explicit scope string in those cases. A finite result and
exit code 0 do not turn an unknown convergence diagnostic into a verified one.

The output records native effective library sizes, normalization factors and a
geometric-mean-one representation of effective sizes for comparison. The centering
is only a reported equivalent scale; it is not fed back to refit the model. Native
RLE implementations can differ in finite-sample details; the method identity must
remain engine-specific.

### DESeq2 LRT

The native `DESeqDataSetFromMatrix` is constructed from checked raw integer counts
and the supplied full numeric design. Native `estimateSizeFactors(type="ratio")`
computes the offsets internally. The exported native functions
`estimateDispersionsGeneEst`, `estimateDispersionsFit`, and
`estimateDispersionsMAP` implement the standard dispersion pipeline, using the full
design, Cox–Reid correction, dispersion maximum iterations 100 and minimum mean
0.5 where applicable. They are split only so the actual trend type can be checked
**before MAP estimation and testing**.

If a requested parametric trend triggers DESeq2's automatic local trend, the actual
and requested types are recorded and the run fails with
`unapproved_native_fit_type_change`. The alternate fit is not accepted. Mean trend
must be explicitly requested; the driver never silently changes to it.

`nbinomLRT(full=..., reduced=...)` uses `type="DESeq2"`, `betaTol=1e-8`,
`maxit=100`, `useOptim=TRUE`, `useQR=TRUE`, `minmu=0.5`. Native optimizer recovery
is part of the pinned procedure. `results` is called with
`independentFiltering=FALSE` and `cooksCutoff=FALSE`; no count replacement is
performed. This is an explicit **diagnostic-only influence policy**, not evidence
that outliers are safe. Cook's diagnostics are retained where exposed. A separate
qualified influence/exclusion policy is required before regulated inference.

Native full/reduced coefficient convergence flags must be true for a requested
hypothesis to succeed. Native gene-wise/MAP dispersion iterations and estimates are
reported, but the current native API does not expose final dispersion-optimizer
convergence flags; that uncertainty is explicit. A missing/invalid native test is
failed, never replaced with p=1 or a successful estimate.

DESeq2's native `recordMaxCooks` returns a logical `NA` for every gene when no
full-design row has at least three replicate samples. In that case
`max_cooks_distance` is reported as `missing_native_value` with a null value; it
is not zero, a successful influence check or evidence that influential observations
are absent. Available test statistics and convergence flags retain their own
independent validation gates. The platform must retain the unavailable-influence
limitation when assessing the run.

## Result: `native_nb_result/1`

Every saved output identifies `engine`, `actual_versions`, `elapsed_seconds`,
`qualification_status: "candidate_unqualified"`, `clinical_release_allowed: false`,
captured conditions and available evidence. `memory_peak_mb` is null with scope
`external_supervisor_required`. There is no claimed memory observation.

`status` is `ok`, `partial_failure`, or `failed`. A global failure has an `error`
object with stable driver code (or `native_or_input_error`), stage and bounded
message. Evidence collected before failure remains available. A partial failure
retains all requested gene records and returns nonzero exit code.

For fitted requests, `genes` is keyed by the exact ordered hypothesis IDs. Each
record includes status/failure code, native statistic and distribution, df,
`native_p_value`, usable `p_value`, `q_value`, convergence scopes and native
dispersion/QL diagnostics. A failed gene can retain a finite **diagnostic** native
p-value; its usable p/q values are null. It does not participate as a discovery.
BH is recomputed with native R `p.adjust(method="BH", n=original family size)` on
the requested family, preserving failed positions in that denominator. No filter
reduces the hypothesis family after looking at the results.

Diagnostic numbers use `{ "state": "finite", "value": number }`. A legitimate
positive-infinite **edgeR QL prior df** is instead represented as
`{ "state": "positive_infinity", "value": null }`; the test still uses the native
finite capped denominator df. This tag is not allowed for arbitrary values.
Native scalar numeric or logical NA diagnostics use `missing_native_value`;
absent diagnostics use `not_exposed`. A native all-missing vector may have logical
storage type; accepting its scalar NA does not admit TRUE/FALSE as numeric
diagnostics. Character NA, unsupported NaN/infinite diagnostics and nonscalar
diagnostics cause a visible failed run.
Test probabilities/statistics are finite or explicitly unavailable/failed; no JSON
NaN/Infinity literals are emitted.

Warnings and messages are stored as bounded stage-labelled records, not discarded
or converted into success. At most 256 records of 2000 characters are retained.
Overflow sets `conditions_truncated` and fails the run; it cannot silently conceal
an unlimited stream of warnings. Native package messages can describe internal
numerical recovery; the coordinator must preserve and assess them.

### Effect and uncertainty interpretation

This protocol tests **all supplied temporal coefficients jointly against zero**,
conditional on the supplied nuisance design. It does not estimate a causal effect,
identify a biomarker, choose a clinical cutpoint, or infer a specific start/end
contrast. It supplies no endpoint prediction rows, so endpoint effects, fold
changes, standard errors and confidence intervals are intentionally omitted.
The potentially misleading single fold-change column printed by native LRT tables
is not promoted into a joint-test effect estimate. A future effect protocol needs
an explicitly signed contrast/prediction design and independently verified scale,
nuisance reference, covariance and interval method.

## Required qualification evidence still outstanding

Static parse success is not native execution evidence. The coordinator must run
and retain, at minimum:

- Actual image/version checks and successful edgeR/DESeq2 runs from raw synthetic
  counts, independently compared with separately written native reference commands.
- Multi-column temporal tests, nuisance covariates, exact sample order, family-order
  preservation and a one-hypothesis request (including JSON array behavior).
- Wrong native versions, integer overflow/fractional counts, duplicate IDs/JSON
  keys/headers, wrong sample order, nonnested/rank-deficient/ill-conditioned designs,
  unapproved fields/formulas, zero libraries and missing RLE references.
- Explicit DESeq2 trend-substitution refusal and a separately requested mean-trend
  case; no post hoc method switch to make a failing run pass.
- All-zero hypotheses, native NA/nonconvergence, supported infinite prior-df
  serialization, unknown convergence diagnostics, malformed output/publication
  failure and watchdog interruption with a retained supervisor outcome.
- A frozen fresh-seed calibration/power matrix and external independent review.
  Current historical diagnostic seeds must not be used to tune the procedure.

Primary method references: the [edgeR manual](https://bioconductor.org/packages/release/bioc/manuals/edgeR/man/edgeR.pdf),
[edgeR v4 paper](https://academic.oup.com/nar/article/53/2/gkaf018/7973897), and
[DESeq2 vignette](https://bioconductor.org/packages/release/bioc/vignettes/DESeq2/inst/doc/DESeq2.html).
Implementation details above were checked against the locally retained pinned
edgeR 4.10.5 and DESeq2 1.52.0 source archives; the coordinator records their SHA-256
lock and actual runtime execution evidence.

### Reproducible publication helper checks

The following checks load only definitions, stop before the driver CLI, and do not
load or run either statistical engine. Set `driver` to the absolute installed
script path. They exercise fresh/existing/dangling paths and publication without
replacement; the temporary directory is created and removed by the check.

```r
driver <- "/absolute/path/to/devharness_server/r/native_nb.R"
for (expression in parse(file = driver)) {
  if (is.call(expression) && identical(expression[[1]], as.name("<-")) &&
      identical(expression[[2]], as.name("arguments"))) break
  eval(expression, envir = .GlobalEnv)
}
check_publication <- function() {
  folder <- tempfile("native-output-check-", tmpdir = normalizePath(tempdir()))
  dir.create(folder)
  on.exit(unlink(folder, recursive = TRUE), add = TRUE)
  fresh <- file.path(folder, "fresh.json")
  existing <- file.path(folder, "existing.json")
  dangling <- file.path(folder, "dangling.json")
  writeLines("retained", existing)
  stopifnot(file.symlink(file.path(folder, "missing-target"), dangling))
  rejected <- function(expression) inherits(tryCatch(force(expression), error = identity), "native_nb_error")
  stopifnot(identical(output_path(fresh), fresh))
  stopifnot(rejected(output_path(existing)))
  stopifnot(rejected(output_path(dangling)))
  publish_no_clobber('{"first":true}', fresh)
  stopifnot(identical(readLines(fresh), '{"first":true}'))
  stopifnot(rejected(publish_no_clobber('{"second":true}', fresh)))
  stopifnot(identical(readLines(fresh), '{"first":true}'))
  stopifnot(rejected(publish_no_clobber("replacement", dangling)))
  stopifnot(identical(readLines(existing), "retained"))
  stopifnot(!file.exists(file.path(folder, "missing-target")))
  stopifnot(length(list.files(folder, pattern = "^\\.native-nb-", all.files = TRUE)) == 0L)
}
check_publication()
```

These checks do not qualify crash durability, protected retention, or the target
container filesystem. Repeat relevant checks in the deployment runtime, including
concurrent writers and an unsupported-hard-link filesystem failure.
