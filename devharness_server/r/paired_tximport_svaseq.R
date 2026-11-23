# A separate diagnostic for a frozen paired tximport coefficient. No primary fit runs here.
drivers <- "/opt/rna/runtime/devharness_server/r"
source(file.path(drivers, "bulk_edger.R"))
source(file.path(drivers, "bulk_latent_diagnostics.R"))

versions <- list(R = as.character(getRversion()),
                 edgeR = as.character(utils::packageVersion("edgeR")),
                 sva = as.character(utils::packageVersion("sva")))
if (!identical(versions, list(R = "4.6.1", edgeR = "4.10.7", sva = "3.60.0"))) {
    stop("Pinned paired tximport diagnostic package versions differ")
}

sha256 <- function(path) {
    output <- system2("/usr/bin/sha256sum", path, stdout = TRUE, stderr = TRUE)
    if (!is.null(attr(output, "status")) || length(output) != 1L ||
        !grepl("^[a-f0-9]{64} ", output)) stop("Cannot verify diagnostic input bytes")
    substr(output, 1L, 64L)
}

request_sha256 <- commandArgs(trailingOnly = TRUE)
config_file <- "/inputs/request.json"
if (length(request_sha256) != 1L || sha256(config_file) != request_sha256) {
    stop("Paired tximport diagnostic request hash mismatch")
}
cfg <- jsonlite::fromJSON(config_file)
if (!identical(cfg$format, "devbench.paired-tximport-svaseq-request/1") ||
    sha256("/inputs/diagnostic.R") != cfg$driver_sha256) {
    stop("Paired tximport diagnostic driver identity mismatch")
}

read_matrix <- function(name, nr, nc, rows, columns) {
    path <- file.path("/inputs", name)
    if (file.info(path)$size != 8 * nr * nc || sha256(path) != cfg$matrices[[name]]) {
        stop("Paired tximport diagnostic matrix hash or axes mismatch")
    }
    values <- readBin(path, numeric(), n = nr * nc, size = 8L, endian = "little")
    if (length(values) != nr * nc || any(!is.finite(values))) {
        stop("Paired tximport diagnostic matrix must contain finite complete values")
    }
    matrix(values, nr, nc, byrow = TRUE, dimnames = list(rows, columns))
}

diagnose <- function() {
    if (!.latent_ids_valid(cfg$genes) || !.latent_ids_valid(cfg$samples) ||
        !.latent_ids_valid(cfg$columns) || length(cfg$columns) < 2L ||
        length(cfg$genes) * length(cfg$samples) > 4000000) {
        stop("Invalid or oversized paired tximport diagnostic axes")
    }
    genes <- length(cfg$genes)
    samples <- length(cfg$samples)
    columns <- length(cfg$columns)
    counts <- read_matrix("counts.bin", genes, samples, cfg$genes, cfg$samples)
    lengths <- read_matrix("lengths.bin", genes, samples, cfg$genes, cfg$samples)
    full <- read_matrix("design.bin", samples, columns, cfg$samples, cfg$columns)
    reduced <- read_matrix("reduced.bin", samples, columns - 1L, cfg$samples, cfg$columns[-2L])
    units <- setNames(cfg$unit_ids, cfg$samples)
    groups <- setNames(cfg$groups, cfg$samples)
    if (any(counts < 0 | counts > 2^53 - 1) || any(lengths <= 0) ||
        !setequal(groups, c(cfg$numerator, cfg$denominator)) ||
        !identical(unname(full[, 2L]), as.double(groups == cfg$numerator)) ||
        !identical(reduced, full[, -2L, drop = FALSE])) {
        stop("Invalid estimated counts, lengths or preserved primary condition")
    }
    bulk_edger_replication(full, units, groups, paired = TRUE)
    design <- list(full = .latent_check_design(full, cfg$samples, "Full"),
                   reduced = .latent_check_design(reduced, cfg$samples, "Reduced"))
    evidence <- list(method = "svaseq", factor_estimator = "leek", seed = 0L,
                     input_scale = "tximport_TMM_offset_normalized_estimated_expression",
                     versions = versions, design = design,
                     observed_profiles = samples, declared_donors = length(unique(units)))
    unavailable <- function(code, detail = NULL, augmented = NULL, warnings = 0L) {
        c(evidence, list(status = "unavailable", reason = code, detail = detail,
                         warnings_count = warnings, n_factors = NULL, scores = NULL,
                         augmented_design = augmented))
    }
    max_factors <- design$full$residual_df - 1L
    expressed <- rowSums(counts) > 0
    if (max_factors < 1L || sum(expressed) < 10L || sum(expressed) <= columns) {
        return(unavailable("insufficient_residual_or_expressed_support"))
    }
    txi <- list(counts = counts, length = lengths, countsFromAbundance = "no")
    normalized <- bulk_edger_normalization(counts, "tximport_estimated_counts", NULL, txi)
    offset <- edgeR::getOffset(normalized$dge)
    centered <- sweep(offset, 1L, rowMeans(offset), "-")
    if (any(!is.finite(centered)) || any(abs(centered) > 350)) {
        return(unavailable("relative_offsets_exceed_numerical_bound"))
    }
    expression <- counts / exp(centered)
    if (any(!is.finite(expression)) || any(colSums(expression) <= 0)) {
        return(unavailable("nonfinite_or_empty_diagnostic_expression"))
    }
    evidence$expressed_features <- sum(expressed)
    evidence$max_abs_centered_offset <- max(abs(centered))
    warnings <- character()
    scores <- tryCatch(withCallingHandlers(
        .latent_sva(expression[expressed, , drop = FALSE], full, reduced, "leek",
                    0L, max_factors),
        warning = function(problem) {
            warnings <<- unique(c(warnings, conditionMessage(problem)))
            invokeRestart("muffleWarning")
        }), error = identity)
    if (inherits(scores, "error")) {
        return(unavailable("latent_estimator_unavailable",
                           substr(conditionMessage(scores), 1L, 300L), warnings = length(warnings)))
    }
    if (!is.matrix(scores) || nrow(scores) != samples || ncol(scores) > max_factors ||
        any(!is.finite(scores))) {
        return(unavailable("invalid_latent_scores", warnings = length(warnings)))
    }
    augmented <- list(full = .latent_design_summary(cbind(full, scores)),
                      reduced = .latent_design_summary(cbind(reduced, scores)))
    if (!augmented$full$passes || !augmented$reduced$passes) {
        return(unavailable("augmented_design_rank_or_conditioning_failed",
                           augmented = augmented, warnings = length(warnings)))
    }
    retained <- if (ncol(scores)) lapply(seq_len(samples), function(i) {
        list(sample_id = cfg$samples[[i]], values = as.list(unname(scores[i, ])))
    }) else list()
    c(evidence, list(status = "completed", reason = NULL, detail = NULL,
                     warnings_count = length(warnings), n_factors = ncol(scores),
                     scores = retained, augmented_design = augmented))
}

jsonlite::write_json(list(request_sha256 = request_sha256, diagnostics = diagnose()),
                     "/outputs/diagnostics.json", auto_unbox = TRUE, null = "null", digits = NA)
