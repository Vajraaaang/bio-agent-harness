args <- commandArgs(trailingOnly = TRUE)
library_dir <- "/opt/rna/scrna-R-library"
if (!identical(as.character(utils::packageVersion("scran", lib.loc = library_dir)),
               "1.40.0")) {
    stop("Unexpected scran package version")
}
if (identical(args, "--probe")) {
    stopifnot(requireNamespace("scran", lib.loc = library_dir, quietly = TRUE))
    cat("1.40.0")
} else {
    stopifnot(length(args) == 2L)
    suppressPackageStartupMessages(library("scran", lib.loc = library_dir))
    counts <- methods::as(Matrix::readMM(args[[1L]]), "CsparseMatrix")
    sizes <- seq.int(21L, min(101L, ncol(counts) - 1L), by = 5L)
    sce <- SingleCellExperiment::SingleCellExperiment(assays = list(counts = counts))
    # A cohort smaller than the default minimum can form one pooling group.
    set.seed(0L)
    clusters <- scran::quickCluster(sce, min.size = min(100L, ncol(sce)))
    fitted <- scran::computeSumFactors(sce, clusters = clusters, positive = TRUE,
                                       sizes = sizes, min.mean = 0.1)
    factors <- SingleCellExperiment::sizeFactors(fitted)
    stopifnot(length(factors) == ncol(counts), all(is.finite(factors)),
              all(factors > 0))
    writeLines(sprintf("%.17g", factors), args[[2L]], useBytes = TRUE)
}
