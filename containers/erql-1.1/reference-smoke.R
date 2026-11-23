# Deterministic public synthetic installation/reference smoke, NOT qualification.
suppressPackageStartupMessages(library(edgeR))
args <- commandArgs(trailingOnly=TRUE)
stopifnot(length(args) == 1L)
out <- args[1]
dir.create(out, recursive=TRUE, showWarnings=FALSE)
lock_path <- "/opt/devharness-erql/source-lock.json"
lock <- jsonlite::read_json(lock_path, simplifyVector=FALSE)
stopifnot(lock$profile == "ERQL-1.1", as.character(getRversion()) == lock$R,
          as.character(packageVersion("BiocVersion")) == lock$BiocVersion)
versions <- setNames(lapply(lock$packages, function(p) {
    version <- packageDescription(p$package, fields="Version")
    stopifnot(identical(version, p$version))
    version
}), vapply(lock$packages, function(p) p$package, character(1)))
sha <- function(path) strsplit(system2("sha256sum", shQuote(path), stdout=TRUE), "[[:space:]]+")[[1]][1]
typed <- function(value) list(
    value=ifelse(is.finite(value), value, NA_real_),
    state=ifelse(is.na(value), "missing", ifelse(is.infinite(value),
        ifelse(value > 0, "positive_infinity", "negative_infinity"), "finite")))

# Eight independent synthetic profiles; seed/dimensions are engineering fixtures.
set.seed(104729L)
group <- factor(rep(c("reference", "target"), each=4L), levels=c("reference", "target"))
design <- model.matrix(~group)
rownames(design) <- paste0("synthetic_unit_", seq_len(8L))
contrast <- c(0, 1)
mu <- matrix(rep(exp(seq(log(10), log(500), length.out=600)), 8L), nrow=600L)
mu[1:40, 5:8] <- mu[1:40, 5:8] * 2
counts <- matrix(rnbinom(length(mu), mu=as.vector(mu), size=10), nrow=600L)
rownames(counts) <- paste0("synthetic_gene_", seq_len(nrow(counts)))
colnames(counts) <- rownames(design)
stopifnot(qr(design)$rank == ncol(design), all(counts == floor(counts)), all(colSums(counts) > 0))
y <- DGEList(counts=counts, group=group)
keep <- filterByExpr(y, group=group, min.count=10, min.total.count=15, large.n=10, min.prop=0.7)
y <- y[keep, , keep.lib.sizes=FALSE]
y <- normLibSizes(y, method="TMM", refColumn=NULL, logratioTrim=0.3,
                 sumTrim=0.05, doWeighting=TRUE, Acutoff=-1e10)
fit_one <- function(object) glmQLFit(object, design=design, legacy=FALSE, robust=TRUE,
    abundance.trend=TRUE, dispersion=NULL, prior.count=0.125,
    winsor.tail.p=c(0.05, 0.1), top.proportion=NULL)

# Keep the normative parent fit and diagnostic-chained candidate separately.
fit <- fit_one(y)
test <- glmQLFTest(fit, contrast=contrast)
diagnostic_y <- estimateDisp(y, design=design, robust=TRUE)
augmented_fit <- fit_one(diagnostic_y)
augmented_test <- glmQLFTest(augmented_fit, contrast=contrast)
expected_top <- limma::chooseLowessSpan(nrow(y) * sqrt(nrow(design)-ncol(design)),
                                      small.n=20, min.span=0.02)
stopifnot(identical(fit$top.proportion, expected_top),
          all(c("df.prior", "df.residual.adj", "s2.post", "top.proportion") %in% names(fit)),
          "df.total" %in% names(test), is.null(fit$df.residual.zeros))
expected_df <- pmin(fit$df.prior + fit$df.residual.adj, sum(fit$df.residual))
stopifnot(isTRUE(all.equal(test$df.total, expected_df, tolerance=0)),
          isTRUE(all.equal(test$table$PValue, pf(test$table$F, df1=test$df.test,
                   df2=test$df.total, lower.tail=FALSE), tolerance=0)),
          all(is.finite(test$table$PValue)), all(test$table$PValue >= 0 & test$table$PValue <= 1))
# Deliberate artificial df.prior probe exercises native capping; not a new fit result.
cap_probe <- fit
cap_probe$df.prior <- rep(Inf, nrow(y))
cap_result <- glmQLFTest(cap_probe, contrast=contrast)
stopifnot(all(cap_result$df.total == sum(fit$df.residual)))

quantities <- list(logFC=list(test$table$logFC, augmented_test$table$logFC),
    F=list(test$table$F, augmented_test$table$F), p=list(test$table$PValue, augmented_test$table$PValue),
    df_total=list(test$df.total, augmented_test$df.total),
    AveLogCPM=list(fit$AveLogCPM, augmented_fit$AveLogCPM),
    dispersion=list(fit$dispersion, augmented_fit$dispersion))
comparisons <- lapply(quantities, function(pair) list(
    exactly_equal=isTRUE(all.equal(pair[[1]], pair[[2]], tolerance=0)),
    max_absolute_delta=if (all(is.finite(unlist(pair)))) max(abs(pair[[1]]-pair[[2]])) else NULL))
changed <- !all(vapply(comparisons, function(x) x$exactly_equal, logical(1)))
table <- test$table
table$gene_id <- rownames(table)
table$df_test <- test$df.test
table$df_total <- test$df.total
table$q_local_fixture_only <- p.adjust(table$PValue, method="BH")
write.csv(table, file.path(out, "parent-reference.csv"), row.names=FALSE)
write.csv(augmented_test$table, file.path(out, "diagnostic-chained-reference.csv"), row.names=TRUE)
write.table(counts, file.path(out, "synthetic-counts.tsv"), sep="\t", quote=FALSE, col.names=NA)
write.table(design, file.path(out, "design.tsv"), sep="\t", quote=FALSE, col.names=NA)
writeLines(capture.output(sessionInfo()), file.path(out, "runtime-session.txt"))
file.copy("/opt/devharness-erql/installed-packages.csv", file.path(out, "installed-packages.csv"))
libraries <- unique(c(sessionInfo()$BLAS, sessionInfo()$LAPACK, file.path(R.home("lib"), "libR.so")))
library_records <- lapply(libraries[file.exists(libraries)], function(p) list(path=p, sha256=sha(p)))
receipt <- list(format="devbench.erql-direct-r-smoke/1", status="passed",
    scope="synthetic_installation_and_native_field_mapping_only", profile="ERQL-1.1",
    lock_sha256=sha(lock_path), R=as.character(getRversion()),
    BiocVersion=as.character(packageVersion("BiocVersion")), package_versions=versions,
    platform=R.version$platform, numerical_libraries=library_records,
    seed=104729L, source_genes=nrow(counts), retained_genes=nrow(y), profiles=ncol(y),
    design_rank=qr(design)$rank, nominal_residual_df=nrow(design)-ncol(design),
    parent_top_proportion=fit$top.proportion, diagnostic_top_proportion=augmented_fit$top.proportion,
    parent_nb_dispersion=typed(fit$dispersion), diagnostic_nb_common=typed(diagnostic_y$common.dispersion),
    diagnostic_nb_trended=typed(diagnostic_y$trended.dispersion),
    diagnostic_nb_tagwise=typed(diagnostic_y$tagwise.dispersion),
    df_prior=typed(fit$df.prior), df_residual_adj=typed(fit$df.residual.adj),
    s2_post=typed(fit$s2.post), df_test=typed(test$df.test), df_total=typed(test$df.total),
    numerator_df_source="glmQLFTest result df.test",
    denominator_df_source="glmQLFTest result df.total; pmin(df.prior + df.residual.adj, sum(nominal df.residual))",
    effective_poisson_bound=FALSE, artificial_infinite_prior_capping_probe="passed",
    diagnostic_chained_comparison=comparisons, diagnostic_chained_changes_observed=changed,
    diagnostic_chained_identity=if (changed) "ERQL-1.1-estimateDisp-chained-candidate/1" else "diagnostic_augmentation_equivalent_on_this_fixture_only",
    calibration="not_run", scientific_qualification="not_run", production_dispatch="not_implemented_T06")
jsonlite::write_json(receipt, file.path(out, "receipt.json"), pretty=TRUE, auto_unbox=TRUE,
                     digits=NA, na="null", null="null")
cat("ERQL direct-R smoke passed; retained", nrow(y), "synthetic genes; diagnostic-chained change:", changed, "\n")
