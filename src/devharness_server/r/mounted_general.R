# Numeric-only transport. No manifest text is parsed as an R formula.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1L) stop("Expected one private mounted solver directory")
directory <- args[[1L]]
driver_arg <- grep("^--file=", commandArgs(FALSE), value = TRUE)
drivers <- dirname(normalizePath(sub("^--file=", "", driver_arg[[1L]])))
cfg <- jsonlite::fromJSON(file.path(directory, "model.json"))
read_matrix <- function(name, rows, columns) {
    path <- file.path(directory, name)
    if (file.info(path)$size != rows * columns * 8) stop("Invalid matrix inventory")
    values <- readBin(path, "double", n = rows * columns, endian = "little")
    if (length(values) != rows * columns || any(!is.finite(values))) stop("Invalid matrix payload")
    matrix(values, nrow = rows, byrow = TRUE)
}
samples <- paste0("sample_", seq_len(cfg$samples))
counts <- read_matrix("counts.bin", cfg$genes, cfg$samples)
dimnames(counts) <- list(cfg$gene_ids, samples)
design <- read_matrix("design.bin", cfg$samples, cfg$columns)
dimnames(design) <- list(samples, paste0("coef_", seq_len(cfg$columns)))
reduced <- if (cfg$reduced_columns > 0) {
    result <- read_matrix("reduced.bin", cfg$samples, cfg$reduced_columns)
    dimnames(result) <- list(samples, paste0("coef_", seq_len(cfg$reduced_columns)))
    result
} else NULL
directed <- if (!is.null(cfg$contrast)) setNames(cfg$contrast, colnames(design)) else NULL
factors <- if (cfg$measurement == "raw_counts") {
    setNames(drop(read_matrix("rle.bin", 1L, cfg$samples)), samples)
} else NULL
units <- setNames(cfg$unit_ids, samples)
groups <- setNames(cfg$labels, samples)
omnibus <- cfg$comparison %in% c("nested", "lrt")

fit_model <- function() {
    if (cfg$solver == "limma_voom") {
        if (cfg$measurement != "raw_counts" || cfg$test != "moderated_t" ||
            omnibus || !is.null(cfg$latent) ||
            !(cfg$units %in% c("independent", "paired_fixed"))) {
            stop("Unsupported frozen limma-voom contrast")
        }
        source(file.path(drivers, "bulk_edger.R"))
        source(file.path(drivers, "bulk_voom_sensitivity.R"))
        return(bulk_voom_sensitivity(counts, design, units, groups, factors, directed,
                                     paired = cfg$units == "paired_fixed"))
    }
    if (cfg$solver == "edger_v4") {
        source(file.path(drivers, "bulk_edger.R"))
        tximport <- NULL
        if (cfg$measurement == "tximport_estimated_counts") {
            lengths <- read_matrix("lengths.bin", cfg$genes, cfg$samples)
            dimnames(lengths) <- dimnames(counts)
            tximport <- list(counts = counts, length = lengths, countsFromAbundance = "no")
        }
        return(bulk_edger_count(counts, design, units, groups, measurement = cfg$measurement,
            size_factors = factors, tximport = tximport, contrast = directed,
            reduced = reduced, test = cfg$test, paired = cfg$units == "paired_fixed"))
    }
    if (cfg$solver == "deseq2") {
        source(file.path(drivers, "bulk_deseq2_sensitivity.R"))
        coefficient <- if (cfg$comparison == "coefficient") names(directed)[which(directed == 1)] else NULL
        contrast <- if (cfg$comparison == "contrast") directed else NULL
        return(bulk_deseq2_sensitivity(counts, design, units, groups, factors, coefficient,
                                       contrast, reduced))
    }
    if (cfg$solver == "dream") {
        source(file.path(drivers, "bulk_dream.R"))
        nuisance <- as.data.frame(design[, -(1:2), drop = FALSE])
        metadata <- data.frame(condition = cfg$labels, unit = cfg$unit_ids, nuisance, row.names = samples)
        return(bulk_dream(counts, metadata, "condition", "unit", cfg$numerator, cfg$denominator,
                          names(nuisance), cfg$measurement, factors))
    }
    stop("Unsupported frozen mounted solver")
}

latent_diagnostics <- function() {
    if (is.null(cfg$latent)) return(NULL)
    source(file.path(drivers, "bulk_latent_diagnostics.R"))
    latent_reduced <- if (is.null(reduced)) design[, !colnames(design) %in%
        paste0("coef_", seq.int(2L, length(unique(groups)))), drop=FALSE] else reduced
    diag <- bulk_latent_diagnostics(counts, design, latent_reduced, cfg$latent$method,
        sv_method = if (is.null(cfg$latent$sv_method)) "be" else cfg$latent$sv_method,
        controls = cfg$latent$controls, n_factors = cfg$latent$n_factors)
    diag$score_standard_deviations <- unname(as.list(apply(diag$scores, 2L, sd)))
    diag$warnings_count <- length(diag$warnings)
    diag$scores <- diag$warnings <- diag$controls <- NULL
    diag
}

diagnostics <- latent_diagnostics()
result <- fit_model()
rows <- result$rows
if (!identical(as.character(rows$gene_id), cfg$gene_ids)) stop("Generalized result changed the gene axis")
codes <- ifelse(rows$estimate_status == "estimated", 0L, ifelse(rows$estimate_status == "filtered", 1L,
    ifelse(rows$missing_reason %in% c("zero_group_marginal", "zero_condition_marginal",
                                     "zero_marginal_condition_count"), 2L, 3L)))
effects <- if (omnibus) rep(NA_real_, nrow(counts)) else rows$effect
writeBin(c(effects, rows$p_value, rows$q_value, codes), file.path(directory, "result.bin"), endian = "little")
method <- if (cfg$solver == "dream") result$diagnostics else result$method
if (inherits(method$group_observation_counts, "table")) {
    group_counts <- method$group_observation_counts
    method$group_observation_counts <- as.list(setNames(as.integer(group_counts), names(group_counts)))
}
version <- if (cfg$solver == "dream") method$package_version else method$version
df_test <- if (omnibus) ncol(design) - ncol(reduced) else 1L
evidence <- list(solver = cfg$solver, solver_version = version, test = cfg$test, test_df = df_test,
                 shrinkage = cfg$shrinkage, solver_details = method,
                 latent_diagnostics = diagnostics)
jsonlite::write_json(evidence, file.path(directory, "method.json"), auto_unbox = TRUE, null = "null",
                     digits = NA)
