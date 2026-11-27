# Independent two-level scratch protocol. The original four/five arguments
# select the historical procedure. Optional explicit arguments are
# normalization filter_mode abundance_trend (TRUE, FALSE, or auto).
args <- commandArgs(trailingOnly = TRUE)
if (!length(args) %in% c(4L, 5L, 8L)) stop("Expected dimensions, solver and optional frozen count settings")
directory <- args[[1L]]
genes <- as.integer(args[[2L]])
samples <- as.integer(args[[3L]])
columns <- as.integer(args[[4L]])
solver <- if (length(args) >= 5L) args[[5L]] else "edger_v4"
normalization <- filter_mode <- NULL
abundance_trend <- TRUE
if (length(args) == 8L) {
    normalization <- args[[6L]]
    filter_mode <- args[[7L]]
    if (!args[[8L]] %in% c("TRUE", "FALSE", "auto")) stop("Invalid abundance trend")
    abundance_trend <- if (args[[8L]] == "auto") NULL else args[[8L]] == "TRUE"
}
read_doubles <- function(name, size) {
    path <- file.path(directory, name)
    if (file.info(path)$size != 8 * size) stop("Invalid binary matrix size")
    value <- readBin(path, double(), n = size, endian = "little")
    if (length(value) != size || any(!is.finite(value))) stop("Invalid binary matrix")
    value
}
counts <- matrix(read_doubles("counts.bin", genes * samples), nrow = genes, byrow = TRUE)
design <- matrix(read_doubles("design.bin", samples * columns), nrow = samples, byrow = TRUE)
sample_ids <- paste0("S", seq_len(samples))
dimnames(counts) <- list(paste0("G", seq_len(genes)), sample_ids)
nuisance <- if (columns > 2L) paste0("factor_", seq.int(3L, columns)) else character()
dimnames(design) <- list(sample_ids, c("baseline", "condition_effect", nuisance))
condition <- design[, 2L]
if (!all(condition %in% c(0, 1)) || min(sum(condition == 1), sum(condition == 0)) < 3L) {
    stop("Expected independent two-level contrast")
}
factors <- if (!identical(normalization, "TMM")) setNames(read_doubles("rle.bin", samples), sample_ids) else NULL
groups <- setNames(ifelse(condition == 1, "numerator", "denominator"), sample_ids)
units <- setNames(sample_ids, sample_ids)
driver <- sub("^--file=", "", grep("^--file=", commandArgs(), value = TRUE))
if (solver == "deseq2") {
    if (length(args) == 8L) stop("Explicit edgeR/voom options are unavailable for DESeq2")
    source(file.path(dirname(driver), "bulk_deseq2_sensitivity.R"))
    result <- bulk_deseq2_sensitivity(counts, design, units, groups, factors,
                                      coefficient = "condition_effect")
} else {
    source(file.path(dirname(driver), "bulk_edger.R"))
    contrast <- setNames(as.numeric(seq_len(columns) == 2L), colnames(design))
    if (solver == "edger_v4") {
        result <- bulk_edger_count(counts, design, units, groups, size_factors = factors,
                                   contrast = contrast, abundance_trend = abundance_trend,
                                   normalization = normalization, filter_mode = filter_mode)
    } else if (solver %in% c("limma_voom", "limma_voom_qw")) {
        source(file.path(dirname(driver), "bulk_voom_sensitivity.R"))
        result <- bulk_voom_sensitivity(counts, design, units, groups, factors, contrast, paired = FALSE,
                                        normalization = normalization, filter_mode = filter_mode,
                                        quality_weights = solver == "limma_voom_qw")
    } else stop("Unsupported frozen mounted solver")
}
rows <- result$rows
codes <- ifelse(rows$estimate_status == "estimated", 0, ifelse(rows$estimate_status == "filtered", 1, 3))
# Keep the three-vector payload (Python verifies and adjusts p-values) and
# publish additional model quantities under a separate, named inventory.
writeBin(as.double(c(rows$effect, rows$p_value, codes)), file.path(directory, "result.bin"), endian = "little")
quantities <- c(list(q_value = rows$q_value), result$quantities)
writeBin(as.double(unlist(quantities, use.names = FALSE)), file.path(directory, "model-quantities.bin"),
         endian = "little")
method <- result$method
method$regime_route <- if (length(args) == 8L) "explicit_count_settings" else "legacy_count_driver"
evidence <- list(solver = solver, solver_version = method$version, solver_details = method,
                 regime_route = method$regime_route, calibration_status = "uncalibrated_L1",
                 model_columns = as.list(names(quantities)))
evidence$numeric_transport <- list(
    encoding = "float64_little_endian_by_column", genes = genes,
    result.bin = list(columns = c("effect", "p_value", "estimate_code")),
    `model-quantities.bin` = list(columns = evidence$model_columns)
)
jsonlite::write_json(evidence, file.path(directory, "method.json"), auto_unbox = TRUE, digits = NA,
                     null = "null")
writeLines(method$version, file.path(directory, "edger-version.txt"))
