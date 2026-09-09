# Native count inference and independent validation

This is the next statistical implementation/validation workstream under the active clinical-production goal. The current hybrid PyDESeq2/statsmodels Wald pipeline remains research-only; its observed 16-cohort failure is retained for diagnosis and cannot be reused as a tuning/validation split.

## Candidate and comparator

Implement a complete native edgeR v4 quasi-likelihood pipeline from raw counts, using the signed complete calibration panel, explicit RLE normalization, the fixed full design, `glmQLFit(dispersion=NULL, legacy=FALSE, robust=TRUE)` and `glmQLFTest` over the complete temporal coefficient block. Apply BH only to the signed hypothesis family. Do not inject per-gene PyDESeq2 MAP dispersions into edgeR QL; native QL estimates a global NB dispersion and empirical Bayes quasi-dispersions.

Run complete native DESeq2 LRT as an independently specified comparator, retaining nuisance covariates in the reduced model. Lock normalization, design/basis, filtering and influence policy. This is a candidate comparison; neither package name nor the QL/F reference establishes qualification for this platform.

Primary basis: [edgeR v4 methods](https://academic.oup.com/nar/article/53/2/gkaf018/7973897), [edgeR reference manual](https://bioconductor.org/packages/release/bioc/manuals/edgeR/man/edgeR.pdf), and [DESeq2 LRT documentation](https://bioconductor.org/packages/release/bioc/vignettes/DESeq2/inst/doc/DESeq2.html#likelihood-ratio-test). The old known-parameter R diagnostic also exceeded nominal rejection, so dispersion shrinkage alone has not been isolated as the cause. A generic `use_f=True` change, p-value scaling or tuned threshold would not be this procedure.

## Runtime identity already observed

The official public Bioconductor Docker image was resolved, downloaded and inspected without networking or project mounts during inspection:

- Image: `bioconductor/bioconductor_docker@sha256:979991813693d4256f685a565cd59560d7bca7c4d4d41f66ed5fc38b31338594`.
- Configuration/image ID: `sha256:e62ba95218d7f8602c14695ef232ef2042bba1337ee1e7d2a459880a4c1b6423`.
- Linux amd64; R 4.6.1; BiocVersion 3.23.1; BiocManager 1.30.27; jsonlite 2.0.0.
- edgeR, DESeq2, limma and statmod were not installed in this base image.
- Image size reported by Docker: 5,148,104,881 bytes. This is an amd64 image on the arm64 Mac, so local timing will include emulation. Do not generalize it to a native Linux deployment.

Next, construct a derived image with resolved source packages and all dependency versions/checksums retained. Inspect compatibility and native `sessionInfo()`, pin the resulting immutable image, and measure an initial timing/reference pilot. Never install a mismatched current package into the host R 4.3.2 stack or silently fall back to the hybrid Python method. The production runtime must have bounded resources, network disabled during analysis, read-only inputs and separately retained outputs.

## Protocol to freeze before fresh measurements

The validation runner must freeze a machine-readable protocol, RNG algorithm and independent seed streams before running either candidate. Record all generated inputs, planned hypotheses, versions, failures, fallback choices and resource outcomes. The historical 16 cohorts are diagnostic examples only.

The core design matrix has 36 cells: sample sizes 24/48/96, panels of 80/800 genes, true NB alpha 0.05/0.2/0.8 and log-library-factor SD 0/0.7. Use six balanced chronological times, two balanced batches, independent biological units, a fixed three-df temporal basis and 32 prespecified hypotheses. Cover low/high abundance strata. Run both complete-null and mixed-null/alternative cases with fixed balanced positive/negative linear, transient and delayed effects. Add separately specified stress generators for inadequate residual df, uneven replication, near/perfect confounding, sparse and zero libraries, dispersion heterogeneity, influential counts, correlated genes, normalization-assumption violations, repeated donors, missing identities and batch misspecification.

Begin with a timing/transport pilot to size the run. The main precision target is 2,000 independent cohorts per stochastic cell; a proportion of 5% has roughly 0.49 percentage-point Monte Carlo standard error at that sample count. Treat cohorts, not genes, as simulation replicates. Specify simultaneous assessment across primary scenarios before execution. Record type-I error, cohort-level FDR, power, effect bias/coverage, non-estimability/failures and resource cost. A failure or timeout remains in the attempted-cohort denominator.

Do not invent a clinical equivalence margin or power requirement. A software simulation can report estimates/uncertainty and flag evidence against nominal calibration; absence of a detected problem is not automatically validation. The acceptable intended-use envelope and clinical acceptance criteria require an approved assay/context and independent statistical/quality review. Assay analytical performance and independent clinical validity remain separate requirements.

Integration ownership: a new versioned controlled plan schema signs the native engine/image/version, full/reduced design, normalization/filter/influence policies and hypothesis family. A bounded native adapter and packaged R entrypoint execute only those allowlisted operations. Preserve old signed-plan meaning and engine-specific result/evidence validation; do not reinterpret existing v1 archives as native QL results.
