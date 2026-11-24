# Callable only by the fixed mounted worker, after raw-count and design validation.
args <- commandArgs(trailingOnly = TRUE)
if (!length(args) %in% c(4L, 5L)) stop("Expected scratch path, matrix dimensions and solver")
directory <- args[[1]]
genes <- as.integer(args[[2]])
samples <- as.integer(args[[3]])
columns <- as.integer(args[[4]])
solver <- if (length(args) == 5) args[[5]] else "edger_v4"
read_doubles <- function(name, size) {
    value <- readBin(file.path(directory, name), double(), n = size, endian = "little")
    if (length(value) != size || any(!is.finite(value))) stop("Invalid binary matrix")
    value
}
counts <- matrix(read_doubles("counts.bin", genes * samples), nrow = genes, byrow = TRUE)
design <- matrix(read_doubles("design.bin", samples * columns), nrow = samples, byrow = TRUE)
factors <- read_doubles("rle.bin", samples)
if (any(counts < 0 | counts != floor(counts)) || any(factors <= 0) ||
    qr(design)$rank != columns || samples <= columns) stop("Invalid counts, offsets or design")
condition <- design[, 2] == 1
if (!all(design[, 2] %in% c(0, 1)) || min(sum(condition), sum(!condition)) < 3) {
    stop("Expected independent two-level contrast")
}
if (solver == "deseq2") {
    driver <- sub("^--file=", "", grep("^--file=", commandArgs(), value = TRUE))
    source(file.path(dirname(driver), "bulk_deseq2_sensitivity.R"))
    sample_ids <- paste0("S", seq_len(samples))
    dimnames(counts) <- list(paste0("G", seq_len(genes)), sample_ids)
    nuisance <- if (columns > 2L) paste0("factor_", seq.int(3L, columns)) else character()
    dimnames(design) <- list(sample_ids, c("baseline", "condition_effect", nuisance))
    groups <- ifelse(condition, "numerator", "denominator")
    fit <- bulk_deseq2_sensitivity(counts, design, sample_ids, groups, factors,
                                   coefficient = "condition_effect")
    rows <- fit$rows
    code <- ifelse(rows$estimate_status == "estimated", 0,
                   ifelse(rows$estimate_status == "filtered", 1, 3))
    writeBin(as.double(c(rows$effect, rows$p_value, code)),
             file.path(directory, "result.bin"), endian = "little")
    writeLines(fit$method$version, file.path(directory, "edger-version.txt"))
    quit(save = "no")
}
if (solver != "edger_v4") stop("Unsupported frozen mounted solver")
version <- utils::packageVersion("edgeR")
if (version < "4.0.0" || version >= "5.0.0") stop("edgeR v4 required")
library_sizes <- colSums(counts)
if (any(library_sizes <= 0)) stop("Count cohort contains an empty library")
normalized_mean <- drop(counts %*% (1 / factors)) / samples
eligible <- normalized_mean >= 1
code <- ifelse(eligible, 3, 1)
effect <- probability <- rep(NA_real_, genes)
if (any(eligible)) {
    # A common scale restores library-sized offsets while preserving the exact
    # RLE ratios. It changes the fitted intercept, never the directed log fold change.
    y <- edgeR::DGEList(counts[eligible, , drop = FALSE],
                        lib.size = factors * exp(mean(log(library_sizes))))
    fit <- edgeR::glmQLFit(y, design = design, legacy = FALSE, robust = TRUE,
                           abundance.trend = TRUE, dispersion = NULL)
    tested <- edgeR::glmQLFTest(fit, coef = 2)$table
    if (nrow(tested) != sum(eligible)) stop("Incomplete edgeR hypothesis inventory")
    valid <- is.finite(tested$logFC) & is.finite(tested$PValue) &
        tested$PValue >= 0 & tested$PValue <= 1
    if (!is.null(fit$failed)) valid <- valid & (fit$failed %in% FALSE)
    code[eligible] <- ifelse(valid, 0, 3)
    effect[eligible] <- ifelse(valid, tested$logFC, NA_real_)
    probability[eligible] <- ifelse(valid, tested$PValue, NA_real_)
}
writeBin(as.double(c(effect, probability, code)), file.path(directory, "result.bin"), endian = "little")
writeLines(as.character(version), file.path(directory, "edger-version.txt"))
