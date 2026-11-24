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
    fitted <- scran::computeSumFactors(sce, sizes = sizes, min.mean = 0.1,
                                       positive = FALSE)
    factors <- SingleCellExperiment::sizeFactors(fitted)
    stopifnot(length(factors) == ncol(counts), all(is.finite(factors)),
              all(factors > 0))
    writeLines(sprintf("%.17g", factors), args[[2L]], useBytes = TRUE)
}
