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
routed <- !is.null(cfg$regime_route)
use_tmm <- identical(cfg$normalization, "TMM")
factors <- if (cfg$measurement == "raw_counts" && (!use_tmm || !is.null(cfg$latent))) {
    setNames(drop(read_matrix("rle.bin", 1L, cfg$samples)), samples)
} else NULL
units <- setNames(cfg$unit_ids, samples)
groups <- setNames(cfg$labels, samples)
omnibus <- cfg$comparison %in% c("nested", "lrt")

fit_model <- function(solver, test, companion = FALSE) {
    fit_factors <- if (use_tmm) NULL else factors
    if (solver %in% c("limma_voom", "limma_voom_qw")) {
        if (cfg$measurement != "raw_counts" || test != "moderated_t" ||
            omnibus || (solver == "limma_voom" && !companion && !is.null(cfg$latent)) ||
            !(cfg$units %in% c("independent", "paired_fixed"))) {
            stop("Unsupported frozen limma-voom contrast")
        }
        source(file.path(drivers, "bulk_edger.R"))
        source(file.path(drivers, "bulk_voom_sensitivity.R"))
        return(bulk_voom_sensitivity(counts, design, units, groups, fit_factors, directed,
                                     paired = cfg$units == "paired_fixed",
                                     normalization = cfg$normalization, filter_mode = cfg$filter_mode,
                                     quality_weights = solver == "limma_voom_qw"))
    }
    if (solver == "edger_v4") {
        source(file.path(drivers, "bulk_edger.R"))
        tximport <- NULL
        if (cfg$measurement == "tximport_estimated_counts") {
            lengths <- read_matrix("lengths.bin", cfg$genes, cfg$samples)
            dimnames(lengths) <- dimnames(counts)
            tximport <- list(counts = counts, length = lengths, countsFromAbundance = "no")
        }
        trend <- if (routed || "abundance_trend" %in% names(cfg)) cfg$abundance_trend else TRUE
        return(bulk_edger_count(counts, design, units, groups, measurement = cfg$measurement,
            size_factors = fit_factors, tximport = tximport, contrast = directed,
            reduced = reduced, test = test, paired = cfg$units == "paired_fixed",
            abundance_trend = trend, normalization = cfg$normalization, filter_mode = cfg$filter_mode))
    }
    if (solver == "deseq2") {
        source(file.path(drivers, "bulk_deseq2_sensitivity.R"))
        coefficient <- if (cfg$comparison == "coefficient") names(directed)[which(directed == 1)] else NULL
        contrast <- if (cfg$comparison == "contrast") directed else NULL
        return(bulk_deseq2_sensitivity(counts, design, units, groups, factors, coefficient,
                                       contrast, reduced))
    }
    if (solver == "dream") {
        fixed <- cfg$units == "independent" && test == "moderated_t"
        mixed <- cfg$units == "mixed" && test == "satterthwaite"
        if (!(fixed && cfg$comparison %in% c("coefficient", "contrast") ||
              mixed && cfg$comparison == "coefficient")) stop("Unsupported dream hypothesis")
        if (!is.numeric(directed) || length(directed) != ncol(design) || ncol(design) < 2L ||
            any(!is.finite(directed)) || any(directed[-2L] != 0) || directed[[2L]] <= 0 ||
            (cfg$comparison == "coefficient" && directed[[2L]] != 1) ||
            !setequal(groups, c(cfg$denominator, cfg$numerator)) || any(design[, 1L] != 1) ||
            any(design[, 2L] != as.numeric(groups == cfg$numerator))) {
            stop("dream requires a positive multiple of the aligned two-arm condition coefficient")
        }
        source(file.path(drivers, "bulk_dream.R"))
        nuisance <- as.data.frame(design[, -(1:2), drop = FALSE])
        metadata <- data.frame(condition = cfg$labels, unit = cfg$unit_ids, nuisance, row.names = samples)
        result <- bulk_dream(counts, metadata, "condition", "unit", cfg$numerator, cfg$denominator,
                             names(nuisance), cfg$measurement, fit_factors, random_intercept = mixed,
                             normalization = cfg$normalization, filter_mode = cfg$filter_mode)
        if (cfg$comparison == "contrast") {
            # A positive scale multiplies the estimate and its standard error
            # equally. The moderated t test of zero is invariant.
            result$rows$effect <- result$rows$effect * directed[[2L]]
            result$diagnostics$contrast$scale <- directed[[2L]]
            result$diagnostics$contrast$design_columns <- colnames(design)
            result$diagnostics$contrast$weights <- unname(directed)
        }
        return(result)
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
        controls = cfg$latent$controls, n_factors = cfg$latent$n_factors, size_factors = factors)
    diag$score_standard_deviations <- unname(as.list(apply(diag$scores, 2L, sd)))
    diag$warnings_count <- length(diag$warnings)
    if (diag$n_factors > 0L) {
        writeBin(as.double(diag$scores), file.path(directory, "latent_scores.bin"), endian = "little")
    }
    diag$scores <- diag$warnings <- diag$controls <- NULL
    diag
}

result_codes <- function(rows) {
    if (!identical(as.character(rows$gene_id), cfg$gene_ids)) stop("Generalized result changed the gene axis")
    filtered <- rows$estimate_status == "filtered" | rows$missing_reason %in% "all_zero_gene"
    ifelse(rows$estimate_status == "estimated", 0L, ifelse(filtered, 1L, 3L))
}

diagnostics <- latent_diagnostics()
result <- fit_model(cfg$solver, cfg$test)
rows <- result$rows
codes <- result_codes(rows)
effects <- if (omnibus) rep(NA_real_, nrow(counts)) else rows$effect
writeBin(c(effects, rows$p_value, rows$q_value, codes), file.path(directory, "result.bin"), endian = "little")
method <- if (cfg$solver == "dream") result$diagnostics else result$method
method$regime_route <- cfg$regime_route
quantities <- result$quantities
sensitivity_details <- NULL
if (!is.null(cfg$sensitivity_solver)) {
    other <- cfg$sensitivity_solver
    if (!routed || !((cfg$solver == "edger_v4" && other == "limma_voom") ||
                     (cfg$solver == "limma_voom_qw" && other == "edger_v4"))) {
        stop("Unsupported frozen sensitivity pair")
    }
    other_test <- if (other == "edger_v4") "qlf" else "moderated_t"
    sensitivity <- fit_model(other, other_test, companion = TRUE)
    other_rows <- sensitivity$rows
    other_quantities <- c(list(effect = other_rows$effect, p_value = other_rows$p_value,
                               q_value = other_rows$q_value, code = result_codes(other_rows)),
                          sensitivity$quantities)
    quantities <- c(quantities, setNames(other_quantities, paste0("sensitivity_", names(other_quantities))))
    sensitivity_details <- sensitivity$method
    sensitivity_details$solver <- other
    sensitivity_details$test <- other_test
    sensitivity_details$regime_route <- cfg$regime_route
}
if (length(quantities)) {
    if (any(lengths(quantities) != nrow(counts)) || anyDuplicated(names(quantities))) {
        stop("Invalid continuous model quantity inventory")
    }
    writeBin(as.double(unlist(quantities, use.names = FALSE)),
             file.path(directory, "model-quantities.bin"), endian = "little")
}
if (inherits(method$group_observation_counts, "table")) {
    group_counts <- method$group_observation_counts
    method$group_observation_counts <- as.list(setNames(as.integer(group_counts), names(group_counts)))
}
version <- if (cfg$solver == "dream") method$package_version else method$version
df_test <- if (omnibus) ncol(design) - ncol(reduced) else 1L
evidence <- list(solver = cfg$solver, solver_version = version, test = cfg$test, test_df = df_test,
                 shrinkage = cfg$shrinkage, solver_details = method,
                 latent_diagnostics = diagnostics)
if (routed) {
    evidence$regime_route <- cfg$regime_route
    evidence$calibration_status <- cfg$calibration_status
}
if (!is.null(sensitivity_details)) evidence$sensitivity_details <- sensitivity_details
# Keep method.json bounded for genome-scale fits. Each file stores one
# little-endian float64 vector per named column, on the unchanged gene axis.
evidence$numeric_transport <- list(
    encoding = "float64_little_endian_by_column", genes = nrow(counts),
    result.bin = list(columns = c("effect", "p_value", "q_value", "estimate_code"))
)
if (length(quantities)) {
    evidence$model_columns <- as.list(names(quantities))
    evidence$numeric_transport[["model-quantities.bin"]] <- list(columns = evidence$model_columns)
}
jsonlite::write_json(evidence, file.path(directory, "method.json"), auto_unbox = TRUE, null = "null",
                     digits = NA)
