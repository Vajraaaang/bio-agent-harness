#!/usr/bin/env Rscript
# Curated adapter for the mounted, preflighted Cell Ranger v3 input.
# All requested parameters and the exact package pin come from the worker.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 7L) stop("Expected raw HDF5, output CSV, lower, niters, FDR, seed, version")
if (as.character(utils::packageVersion("DropletUtils")) != args[[7L]]) {
    stop("Installed DropletUtils differs from the pinned version")
}
lower <- as.integer(args[[3L]])
niters <- as.integer(args[[4L]])
fdr <- as.numeric(args[[5L]])
seed <- as.integer(args[[6L]])
stopifnot(lower >= 1L, niters >= 1L, fdr > 0, fdr < 1, seed >= 0L)

sce <- DropletUtils::read10xCounts(args[[1L]], col.names = TRUE, type = "HDF5", version = "3")
rna <- SummarizedExperiment::rowData(sce)$Type == "Gene Expression"
umi <- SummarizedExperiment::assay(sce, "counts")[rna, , drop = FALSE]
set.seed(seed)
# Disable automatic knee retention. Calls use only the declared FDR rule.
result <- DropletUtils::emptyDrops(
    umi, lower = lower, niters = niters, retain = Inf, round = FALSE,
    test.ambient = FALSE, BPPARAM = BiocParallel::SerialParam(RNGseed = seed)
)
cells <- !is.na(result$FDR) & result$FDR <= fdr
table <- data.frame(
    barcode = colnames(umi), total_umi = as.integer(result$Total),
    p_value = result$PValue, fdr = result$FDR, limited = result$Limited,
    called = cells
)
options(scipen = 100)
utils::write.csv(table, args[[2L]], row.names = FALSE, na = "")
