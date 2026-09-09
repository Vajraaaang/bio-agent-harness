#!/usr/bin/env Rscript
# Trusted native candidate engine. Inputs are data, never R expressions or formulas.
# See docs/native-nb-engine-contract.md. This driver cannot authorize clinical use.

Sys.setenv(OMP_NUM_THREADS = "1", OPENBLAS_NUM_THREADS = "1", MKL_NUM_THREADS = "1",
           VECLIB_MAXIMUM_THREADS = "1", NUMEXPR_NUM_THREADS = "1")
options(warn = 1)

LIMIT <- list(config = 1024^2, counts_file = 64 * 1024^2, auxiliary_file = 8 * 1024^2,
              dense_counts = 16 * 1024^2, genes = 5000L, samples = 512L,
              hypotheses = 100L, columns = 32L, conditions = 256L)
EXPECTED <- c(R = "4.6.1", edgeR = "4.10.5", DESeq2 = "1.52.0")
state <- new.env(parent = emptyenv())
state$stage <- "startup"
state$conditions <- list()
state$conditions_truncated <- FALSE
state$engine <- NULL
state$versions <- list(R = as.character(getRversion()), platform = R.version$platform)
state$evidence <- list()
state$effect_requested <- FALSE
state$effect_dds <- NULL
state$effect_input <- NULL
state$config <- NULL

fail <- function(code, message = code) {
  stop(structure(list(message = message, call = NULL, code = code),
                 class = c("native_nb_error", "error", "condition")))
}
require_that <- function(test, code, message = code) {
  if (!isTRUE(test)) fail(code, message)
}
record_condition <- function(kind, condition) {
  if (length(state$conditions) < LIMIT$conditions) {
    state$conditions[[length(state$conditions) + 1L]] <- list(
      stage = state$stage, kind = kind, message = substr(conditionMessage(condition), 1L, 2000L))
  } else state$conditions_truncated <- TRUE
}
capture <- function(stage, expression) {
  state$stage <- stage
  withCallingHandlers(force(expression),
    warning = function(w) { record_condition("warning", w); invokeRestart("muffleWarning") },
    message = function(m) { record_condition("message", m); invokeRestart("muffleMessage") })
}
scalar_text <- function(x) is.character(x) && length(x) == 1L && !is.na(x) && nzchar(x)
finite_number <- function(x) is.numeric(x) && length(x) == 1L && is.finite(x)
number_or_null <- function(x) if (finite_number(x)) unname(x) else NULL
array <- function(x) unname(as.list(x))
named_numbers <- function(x, labels) setNames(lapply(x, number_or_null), labels)
native_number <- function(x, positive_infinity = FALSE) {
  if (finite_number(x)) return(list(state = "finite", value = unname(x)))
  if (positive_infinity && is.numeric(x) && length(x) == 1L && is.infinite(x) && x > 0)
    return(list(state = "positive_infinity", value = NULL))
  if (is.null(x)) return(list(state = "not_exposed", value = NULL))
  # Native all-missing diagnostics can be logical NA (e.g. DESeq2 maxCooks
  # without a design cell containing >=3 replicates). This is not NaN/Inf or a
  # successful estimate. Nonmissing logical values still fail this predicate.
  require_that((is.numeric(x) || is.logical(x)) && length(x) == 1L && is.na(x) && !is.nan(x),
               "unsupported_nonfinite_native_diagnostic")
  list(state = "missing_native_value", value = NULL)
}
at_gene <- function(x, i, n) {
  if (is.null(x)) return(NULL)
  require_that(length(x) %in% c(1L, n), "unexpected_native_diagnostic_shape")
  unname(x[if (length(x) == 1L) 1L else i])
}
flag_or_null <- function(x) if (is.logical(x) && length(x) == 1L && !is.na(x)) unname(x) else NULL

regular_input <- function(path, limit) {
  require_that(scalar_text(path) && startsWith(path, "/"), "absolute_input_path_required")
  info <- file.info(path)
  require_that(nrow(info) == 1L && !is.na(info$isdir) && !info$isdir &&
               is.finite(info$size) && info$size <= limit, "input_file_missing_or_oversized")
  require_that(identical(normalizePath(path, mustWork = TRUE), path), "noncanonical_input_path")
  path
}
output_path <- function(path) {
  require_that(scalar_text(path) && startsWith(path, "/") &&
               grepl("^[A-Za-z0-9][A-Za-z0-9_.-]*\\.json$", basename(path)), "invalid_output_path")
  require_that(dir.exists(dirname(path)) &&
               identical(normalizePath(dirname(path), mustWork = TRUE), dirname(path)), "noncanonical_output_parent")
  link <- Sys.readlink(path)
  require_that(!file.exists(path) && !(!is.na(link) && nzchar(link)), "output_already_exists")
  path
}
publish_no_clobber <- function(text, destination) {
  temporary <- tempfile(pattern = ".native-nb-", tmpdir = dirname(destination))
  on.exit(if (file.exists(temporary)) unlink(temporary), add = TRUE)
  writeLines(text, temporary, useBytes = TRUE)
  require_that(isTRUE(Sys.chmod(temporary, mode = "0600")), "result_permissions_failed")
  link <- Sys.readlink(destination)
  require_that(!file.exists(destination) && !(!is.na(link) && nzchar(link)), "output_already_exists")
  # Unlike rename(), link creation atomically refuses an existing destination.
  # There is no replacement/copy fallback if the filesystem disallows hard links.
  require_that(isTRUE(suppressWarnings(file.link(temporary, destination))),
               "no_clobber_publication_failed",
               "Atomic hard-link publication failed; the destination may exist or hard links may be unavailable")
  invisible(destination)
}
check_json_tree <- function(x) {
  if (is.list(x)) {
    if (!is.null(names(x))) require_that(!anyDuplicated(names(x)), "duplicate_json_key")
    invisible(lapply(x, check_json_tree))
  } else if (is.numeric(x)) require_that(all(is.finite(x)), "nonfinite_json_number")
}
read_request <- function(path) {
  path <- regular_input(path, LIMIT$config)
  text <- readChar(path, file.info(path)$size, useBytes = TRUE)
  require_that(jsonlite::validate(text), "invalid_json_request")
  x <- jsonlite::fromJSON(text, simplifyVector = FALSE)
  check_json_tree(x)
  require_that(is.list(x) && !is.null(names(x)), "request_must_be_object")
  state$effect_requested <- identical(x$schema_version, "native_nb_request/2") || identical(x$schema_version, "native_nb_request/4")
  reference_requested <- identical(x$schema_version, "native_nb_request/3") || identical(x$schema_version, "native_nb_request/4")
  common <- c("schema_version", "engine", "counts_csv", "metadata_csv", "full_design_csv",
              "reduced_design_csv", "hypothesis_genes", "temporal_columns", "normalization", "data_classification")
  require_that(scalar_text(x$engine) && x$engine %in% c("edger_qlf", "deseq2_lrt"), "unsupported_engine")
  allowed <- c(common, if (x$engine == "deseq2_lrt") "deseq2_fit_type", if (state$effect_requested) "effect",
               if (reference_requested) "normalization_reference")
  require_that(setequal(names(x), allowed), "missing_or_unknown_request_field")
  require_that(x$schema_version %in% c("native_nb_request/1", "native_nb_request/2", "native_nb_request/3", "native_nb_request/4"), "unsupported_request_schema")
  if (reference_requested) {
    require_that(identical(x$engine, "deseq2_lrt"), "reference_normalization_engine_not_supported")
    reference <- x$normalization_reference
    require_that(is.list(reference) && setequal(names(reference), c("gene_ids", "specification_sha256", "eligibility_policy")),
                 "invalid_normalization_reference")
    require_that(identical(reference$eligibility_policy, "all_positive") && scalar_text(reference$specification_sha256) &&
                 grepl("^[0-9a-f]{64}$", reference$specification_sha256), "invalid_reference_binding_or_policy")
    require_that(is.list(reference$gene_ids) && is.null(names(reference$gene_ids)) &&
                 length(reference$gene_ids) >= 1L && length(reference$gene_ids) <= LIMIT$genes &&
                 all(vapply(reference$gene_ids, scalar_text, logical(1))), "invalid_reference_gene_ids")
    x$normalization_reference$gene_ids <- unlist(reference$gene_ids, use.names = FALSE)
    check_ids(x$normalization_reference$gene_ids, "normalization_reference")
  }
  if (state$effect_requested) {
    require_that(identical(x$engine, "deseq2_lrt"), "effect_engine_not_supported")
    effect <- x$effect
    require_that(is.list(effect) && setequal(names(effect), c("name", "reference_sample_id", "comparison_sample_id", "confidence_level", "time_unit")),
                 "invalid_effect_specification")
    require_that(all(vapply(effect[c("name", "reference_sample_id", "comparison_sample_id", "time_unit")], scalar_text, logical(1))) &&
                 finite_number(effect$confidence_level) && effect$confidence_level > 0 && effect$confidence_level < 1,
                 "invalid_effect_specification")
    check_ids(c(effect$reference_sample_id, effect$comparison_sample_id), "effect_reference")
  }
  require_that(identical(x$normalization, "rle"), "unapproved_normalization")
  require_that(scalar_text(x$data_classification) &&
               x$data_classification %in% c("synthetic", "public_processed"), "clinical_admission_unconfigured")
  if (x$engine == "deseq2_lrt") require_that(scalar_text(x$deseq2_fit_type) &&
    x$deseq2_fit_type %in% c("parametric", "mean"), "explicit_deseq2_fit_type_required")
  require_that(is.list(x$hypothesis_genes) && is.null(names(x$hypothesis_genes)) &&
               length(x$hypothesis_genes) >= 1L && length(x$hypothesis_genes) <= LIMIT$hypotheses &&
               all(vapply(x$hypothesis_genes, scalar_text, logical(1))), "invalid_hypothesis_family")
  x$hypothesis_genes <- unlist(x$hypothesis_genes, use.names = FALSE)
  check_ids(x$hypothesis_genes, "hypothesis")
  require_that(is.list(x$temporal_columns) && is.null(names(x$temporal_columns)) &&
               length(x$temporal_columns) >= 1L && length(x$temporal_columns) <= 5L &&
               all(vapply(x$temporal_columns, function(z) finite_number(z) && z == floor(z), logical(1))),
               "invalid_temporal_column_indices")
  x$temporal_columns <- unlist(x$temporal_columns, use.names = FALSE)
  require_that(!anyDuplicated(x$temporal_columns) && all(diff(x$temporal_columns) > 0),
               "temporal_columns_must_be_unique_and_increasing")
  x
}
check_ids <- function(ids, label) {
  require_that(is.character(ids) && length(ids) > 0L && !anyNA(ids) && !anyDuplicated(ids) &&
               all(nzchar(ids)) && all(nchar(ids, type = "bytes") <= 256L) &&
               all(trimws(ids) == ids) && !any(grepl("[[:cntrl:]]", ids)), paste0("invalid_", label, "_ids"))
}
read_table <- function(path, byte_limit, row_limit) {
  path <- regular_input(path, byte_limit)
  table <- read.table(path, header = TRUE, sep = ",", quote = "\"", row.names = NULL,
                      check.names = FALSE, colClasses = "character", stringsAsFactors = FALSE,
                      na.strings = character(), comment.char = "", fill = FALSE,
                      blank.lines.skip = FALSE, strip.white = FALSE, nrows = row_limit + 1L)
  require_that(nrow(table) > 0L && nrow(table) <= row_limit && !anyDuplicated(names(table)),
               "empty_oversized_or_duplicate_header_table")
  table
}
numeric_matrix <- function(table, id_column) {
  require_that(identical(names(table)[1L], id_column) && ncol(table) >= 2L, "invalid_matrix_header")
  check_ids(table[[1L]], id_column)
  labels <- names(table)[-1L]
  check_ids(labels, "matrix_column")
  values <- as.matrix(table[-1L])
  require_that(all(nzchar(values)) && all(trimws(values) == values), "missing_or_noncanonical_numeric_value")
  numeric <- suppressWarnings(as.numeric(values))
  require_that(all(is.finite(numeric)), "nonfinite_or_nonnumeric_matrix")
  matrix(numeric, nrow = nrow(table), dimnames = list(table[[1L]], labels))
}
design_diagnostics <- function(x) {
  require_that(ncol(x) >= 1L && ncol(x) <= LIMIT$columns && nrow(x) > ncol(x), "invalid_design_dimensions")
  require_that(identical(colnames(x)[1L], "Intercept") && all(x[, 1L] == 1) &&
               all(grepl("^[A-Za-z][A-Za-z0-9_]*$", colnames(x))), "invalid_design_columns_or_intercept")
  singular <- svd(x, nu = 0L, nv = 0L)$d
  tolerance <- .Machine$double.eps * max(dim(x)) * singular[1L]
  rank <- sum(singular > tolerance)
  condition <- singular[1L] / tail(singular, 1L)
  require_that(rank == ncol(x), "rank_deficient_design")
  require_that(is.finite(condition) && condition <= 1000, "design_condition_number_exceeds_1000")
  list(rank = rank, columns = array(colnames(x)), condition_number = condition, residual_df = nrow(x) - rank)
}
read_inputs <- function(config) {
  counts_table <- read_table(config$counts_csv, LIMIT$counts_file, LIMIT$genes)
  require_that(ncol(counts_table) >= 4L && ncol(counts_table) <= LIMIT$samples + 1L &&
               nrow(counts_table) * (ncol(counts_table) - 1L) * 8 <= LIMIT$dense_counts, "count_matrix_exceeds_bounds")
  counts <- numeric_matrix(counts_table, "gene_id")
  max_count <- if (config$engine == "deseq2_lrt") .Machine$integer.max else 2^53 - 1
  require_that(all(counts >= 0 & counts == floor(counts) & counts <= max_count), "invalid_or_unrepresentable_raw_counts")
  require_that(all(colSums(counts) > 0), "zero_library_size")
  reference <- rowSums(counts > 0) == ncol(counts)
  if (!is.null(config$normalization_reference)) {
    declared <- config$normalization_reference$gene_ids
    require_that(all(declared %in% rownames(counts)), "declared_reference_gene_missing")
    selected <- rownames(counts) %in% declared
    require_that(all(reference[selected]), "nonpositive_declared_reference_no_fallback")
    reference <- selected
  }
  require_that(any(reference), "no_positive_rle_reference_no_fallback")
  require_that(all(config$hypothesis_genes %in% rownames(counts)), "hypothesis_gene_missing")
  metadata <- read_table(config$metadata_csv, LIMIT$auxiliary_file, LIMIT$samples)
  require_that(identical(names(metadata), c("sample_id", "biological_unit_id", "numeric_time")), "invalid_metadata_columns")
  check_ids(metadata$sample_id, "sample")
  check_ids(metadata$biological_unit_id, "biological_unit")
  require_that(!any(tolower(metadata$biological_unit_id) %in%
    c("na", "n/a", "nan", "none", "null", "unknown", "unverified", "unavailable", "not_available")), "missing_biological_unit_identity")
  times <- suppressWarnings(as.numeric(metadata$numeric_time))
  require_that(all(is.finite(times)) && all(trimws(metadata$numeric_time) == metadata$numeric_time) &&
               length(unique(times)) >= length(config$temporal_columns) + 1L, "invalid_numeric_time")
  full <- numeric_matrix(read_table(config$full_design_csv, LIMIT$auxiliary_file, LIMIT$samples), "sample_id")
  reduced <- numeric_matrix(read_table(config$reduced_design_csv, LIMIT$auxiliary_file, LIMIT$samples), "sample_id")
  samples <- colnames(counts)
  require_that(identical(samples, metadata$sample_id) && identical(samples, rownames(full)) &&
               identical(samples, rownames(reduced)), "sample_order_mismatch")
  full_diagnostic <- design_diagnostics(full)
  reduced_diagnostic <- design_diagnostics(reduced)
  temporal <- config$temporal_columns
  require_that(all(temporal >= 2L & temporal <= ncol(full)), "temporal_column_out_of_range_or_intercept")
  require_that(identical(reduced, full[, -temporal, drop = FALSE]), "reduced_design_not_exact_non_temporal_subset")
  require_that(full_diagnostic$rank - reduced_diagnostic$rank == length(temporal), "invalid_nested_design_rank_difference")
  value <- list(counts = counts, metadata = metadata, full = full, reduced = reduced,
                reference = reference, full_diagnostic = full_diagnostic, reduced_diagnostic = reduced_diagnostic)
  if (state$effect_requested) value$effect_design <- effect_design(config, value)
  value
}

effect_design <- function(config, input) {
  effect <- config$effect
  a <- match(effect$reference_sample_id, colnames(input$counts)); b <- match(effect$comparison_sample_id, colnames(input$counts))
  require_that(!is.na(a) && !is.na(b) && a != b, "effect_reference_row_missing")
  times <- as.numeric(input$metadata$numeric_time)
  contrast <- unname(input$full[b, ] - input$full[a, ])
  require_that(times[b] > times[a] && all(is.finite(contrast)) && any(contrast != 0) &&
               all(contrast[-config$temporal_columns] == 0), "effect_rows_not_temporal_or_nuisance_matched")
  zero <- rep(FALSE, nrow(input$counts))
  if (!(all(contrast >= 0) || all(contrast <= 0))) {
    selected <- drop(input$full %*% as.numeric(contrast != 0)) != 0
    zero <- rowSums(input$counts[, selected, drop = FALSE]) == 0
  }
  list(name = effect$name, reference_sample_id = effect$reference_sample_id, comparison_sample_id = effect$comparison_sample_id,
       reference_time = times[a], comparison_time = times[b], time_unit = effect$time_unit, confidence_level = effect$confidence_level,
       design_columns = array(colnames(input$full)), native_coefficient_names = array(make.names(colnames(input$full))),
       reference_row = array(input$full[a, ]), comparison_row = array(input$full[b, ]), contrast = array(contrast),
       native_zero_convention = setNames(lapply(match(config$hypothesis_genes, rownames(input$counts)), function(i) unname(zero[i])), config$hypothesis_genes),
       interpretation = "Contrast of fitted population means at observed design rows; not an individual longitudinal change")
}
version_check <- function(engine) {
  selected <- if (engine == "edger_qlf") "edgeR" else "DESeq2"
  require_that(requireNamespace(selected, quietly = TRUE), "native_package_unavailable")
  for (name in c(selected, "limma", "BiocParallel", "jsonlite")) {
    state$versions[[name]] <- if (requireNamespace(name, quietly = TRUE)) as.character(packageVersion(name)) else NULL
  }
  require_that(identical(as.character(getRversion()), unname(EXPECTED["R"])) &&
               identical(state$versions[[selected]], unname(EXPECTED[selected])), "native_version_mismatch")
}
fit_flag <- function(fit, i, n) {
  failed <- at_gene(fit$failed, i, n)
  if (is.logical(failed) && !is.na(failed)) return(list(converged = !failed, scope = "native_failed_flag"))
  list(converged = NULL, scope = if (identical(fit$method, "oneway"))
    "not_exposed_by_native_oneway" else "not_exposed_by_native_API")
}
valid_native_test <- function(p, statistic, df1, df2 = NULL) {
  finite_number(p) && p >= 0 && p <= 1 && finite_number(statistic) && statistic >= 0 &&
    finite_number(df1) && df1 > 0 && (is.null(df2) || finite_number(df2) && df2 > 0)
}

fit_edger <- function(config, input) {
  y <- capture("edgeR_normalization", edgeR::normLibSizes(edgeR::DGEList(counts = input$counts), method = "RLE"))
  effective <- y$samples$lib.size * y$samples$norm.factors
  require_that(all(is.finite(effective) & effective > 0), "invalid_native_normalization")
  state$evidence$normalization <- list(method = "edgeR_RLE", scope = "entire_input_panel",
    reference_gene_ids = array(rownames(input$counts)[input$reference]),
    effective_library_sizes = named_numbers(effective, colnames(input$counts)),
    normalization_factors = named_numbers(y$samples$norm.factors, colnames(input$counts)),
    centered_size_factors = named_numbers(exp(log(effective) - mean(log(effective))), colnames(input$counts)))
  fit <- capture("edgeR_glmQLFit", edgeR::glmQLFit(y, design = input$full, dispersion = NULL,
    legacy = FALSE, robust = TRUE, abundance.trend = TRUE, winsor.tail.p = c(0.05, 0.1),
    keep.unit.mat = FALSE, prior.count = 0))
  require_that(all(is.finite(fit$dispersion) & fit$dispersion >= 0) &&
               finite_number(fit$average.ql.dispersion) && fit$average.ql.dispersion > 0, "invalid_native_ql_dispersion")
  # glmQLFTest's internal reduced fit is not returned. Repeat exactly its native
  # specification for visible diagnostics; never replace the native test result.
  reduced <- capture("edgeR_reduced_diagnostic_fit", edgeR::glmFit(fit$counts, design = input$reduced,
    offset = fit$offset, weights = fit$weights, dispersion = fit$dispersion / fit$average.ql.dispersion, prior.count = 0))
  test <- capture("edgeR_glmQLFTest", edgeR::glmQLFTest(fit, coef = config$temporal_columns))
  require_that(identical(rownames(test$table), rownames(input$counts)) &&
               identical(rownames(fit$coefficients), rownames(input$counts)) &&
               identical(rownames(reduced$coefficients), rownames(input$counts)), "native_gene_order_changed")
  state$evidence$native_settings <- list(legacy = FALSE, robust = TRUE, abundance_trend = TRUE,
    winsor_tail_p = list(0.05, 0.1), prior_count = 0, top_proportion = number_or_null(fit$top.proportion),
    low_count_filter = "none", reduced_diagnostic_fit = "additional_native_glmFit_at_identical_working_dispersion")
  n <- nrow(input$counts)
  genes <- setNames(lapply(config$hypothesis_genes, function(name) {
    i <- match(name, rownames(input$counts))
    full_flag <- fit_flag(fit, i, n); reduced_flag <- fit_flag(reduced, i, n)
    p <- unname(test$table$PValue[i]); statistic <- unname(test$table$F[i])
    df1 <- at_gene(test$df.test, i, n); df2 <- at_gene(test$df.total, i, n)
    finite_fit <- all(is.finite(fit$coefficients[i, ])) && all(is.finite(reduced$coefficients[i, ])) &&
      all(is.finite(fit$fitted.values[i, ])) && all(is.finite(reduced$fitted.values[i, ]))
    ok <- valid_native_test(p, statistic, df1, df2) && finite_fit &&
      !identical(full_flag$converged, FALSE) && !identical(reduced_flag$converged, FALSE)
    reason <- if (sum(input$counts[i, ]) == 0) "all_zero_hypothesis" else if (!ok) "native_fit_or_test_failed" else NULL
    list(status = if (is.null(reason)) "ok" else "failed", failure_code = reason,
      p_value = if (is.null(reason)) p else NULL, q_value = NULL,
      native_p_value = number_or_null(p), test_statistic = number_or_null(statistic),
      test_distribution = "quasi_F", test_df_num = number_or_null(df1), test_df_denom = number_or_null(df2),
      full_fit_convergence = full_flag, reduced_fit_convergence = reduced_flag,
      diagnostics = list(nb_dispersion = native_number(at_gene(fit$dispersion, i, n)),
        working_nb_dispersion = native_number(at_gene(fit$dispersion, i, n) / fit$average.ql.dispersion),
        ql_posterior = native_number(at_gene(fit$s2.post, i, n)), ql_prior = native_number(at_gene(fit$s2.prior, i, n)),
        ql_prior_df = native_number(at_gene(fit$df.prior, i, n), positive_infinity = TRUE),
        adjusted_residual_df = native_number(at_gene(fit$df.residual.adj, i, n)),
        adjusted_deviance = native_number(at_gene(fit$deviance.adj, i, n)),
        average_ql_dispersion = native_number(fit$average.ql.dispersion),
        full_iterations = native_number(at_gene(fit$iter, i, n)), reduced_iterations = native_number(at_gene(reduced$iter, i, n))))
  }), config$hypothesis_genes)
  list(genes = genes, convergence_scope = "Native flags retained where exposed; oneway and some dispersion optimizers do not expose complete convergence telemetry")
}

fit_deseq2 <- function(config, input) {
  integer_counts <- input$counts
  storage.mode(integer_counts) <- "integer"  # Range/integrality checked before conversion.
  dds <- capture("DESeq2_dataset", DESeq2::DESeqDataSetFromMatrix(countData = integer_counts,
    colData = S4Vectors::DataFrame(row.names = colnames(integer_counts)), design = input$full))
  dds <- capture("DESeq2_normalization", if (is.null(config$normalization_reference)) {
    DESeq2::estimateSizeFactors(dds, type = "ratio")
  } else {
    DESeq2::estimateSizeFactors(dds, type = "ratio", controlGenes = which(input$reference))
  })
  factors <- DESeq2::sizeFactors(dds)
  require_that(all(is.finite(factors) & factors > 0), "invalid_native_normalization")
  state$evidence$normalization <- list(method = "DESeq2_ratio", scope = "entire_input_panel",
    reference_gene_ids = array(rownames(input$counts)[input$reference]),
    size_factors = named_numbers(factors, colnames(input$counts)))
  if (!is.null(config$normalization_reference)) {
    state$evidence$normalization$scope <- "prespecified_reference_genes"
    state$evidence$normalization$reference_specification_sha256 <- config$normalization_reference$specification_sha256
  }
  # These are the exported native steps used by estimateDispersions. Split only
  # to reject an automatic trend substitution before MAP estimation and testing.
  dds <- capture("DESeq2_gene_dispersions", DESeq2::estimateDispersionsGeneEst(dds, modelMatrix = input$full,
    maxit = 100, useCR = TRUE, minmu = 0.5, quiet = TRUE))
  dds <- capture("DESeq2_dispersion_trend", DESeq2::estimateDispersionsFit(dds, fitType = config$deseq2_fit_type, quiet = TRUE))
  actual_fit_type <- attr(DESeq2::dispersionFunction(dds), "fitType")
  state$evidence$dispersion_fit_type <- list(requested = config$deseq2_fit_type, actual = actual_fit_type)
  require_that(identical(actual_fit_type, config$deseq2_fit_type), "unapproved_native_fit_type_change")
  dds <- capture("DESeq2_MAP_dispersions", DESeq2::estimateDispersionsMAP(dds, modelMatrix = input$full,
    maxit = 100, useCR = TRUE, quiet = TRUE))
  dds <- capture("DESeq2_nbinomLRT", DESeq2::nbinomLRT(dds, full = input$full, reduced = input$reduced,
    betaTol = 1e-8, maxit = 100, useOptim = TRUE, useQR = TRUE, minmu = 0.5, type = "DESeq2", quiet = TRUE))
  test <- capture("DESeq2_results", DESeq2::results(dds, independentFiltering = FALSE, cooksCutoff = FALSE))
  require_that(identical(rownames(test), rownames(input$counts)) &&
               identical(rownames(dds), rownames(input$counts)), "native_gene_order_changed")
  information <- S4Vectors::mcols(dds)
  state$evidence$native_settings <- list(dispersion_fit_type = actual_fit_type, dispersion_maxit = 100,
    beta_maxit = 100, beta_tolerance = 1e-8, use_optim = TRUE, use_QR = TRUE, min_mu = 0.5,
    independent_filtering = FALSE, count_outlier_policy = "diagnostic_only_no_replacement_no_filtering",
    coefficient_prior = "none", low_count_filter = "none")
  n <- nrow(input$counts)
  genes <- setNames(lapply(config$hypothesis_genes, function(name) {
    i <- match(name, rownames(input$counts))
    full_flag <- flag_or_null(information$fullBetaConv[i]); reduced_flag <- flag_or_null(information$reducedBetaConv[i])
    p <- unname(test$pvalue[i]); statistic <- unname(test$stat[i]); alpha <- unname(DESeq2::dispersions(dds)[i])
    ok <- valid_native_test(p, statistic, length(config$temporal_columns)) &&
      isTRUE(full_flag) && isTRUE(reduced_flag) && finite_number(alpha) && alpha > 0
    reason <- if (sum(input$counts[i, ]) == 0) "all_zero_hypothesis" else if (!ok) "native_fit_or_test_failed" else NULL
    list(status = if (is.null(reason)) "ok" else "failed", failure_code = reason,
      p_value = if (is.null(reason)) p else NULL, q_value = NULL,
      native_p_value = number_or_null(p), test_statistic = number_or_null(statistic),
      test_distribution = "chi_square", test_df_num = length(config$temporal_columns), test_df_denom = NULL,
      full_fit_convergence = list(converged = full_flag, scope = "native_fullBetaConv"),
      reduced_fit_convergence = list(converged = reduced_flag, scope = "native_reducedBetaConv"),
      diagnostics = list(nb_dispersion = native_number(alpha), gene_wise_dispersion = native_number(information$dispGeneEst[i]),
        fitted_dispersion = native_number(information$dispFit[i]), map_dispersion = native_number(information$dispMAP[i]),
        dispersion_outlier = flag_or_null(information$dispOutlier[i]),
        gene_dispersion_iterations = native_number(information$dispGeneIter[i]),
        map_dispersion_iterations = native_number(information$dispIter[i]),
        full_iterations = native_number(information$betaIter[i]), max_cooks_distance = native_number(information$maxCooks[i]),
        dispersion_convergence = list(converged = NULL, scope = "native_final_optimizer_flags_not_exposed")))
  }), config$hypothesis_genes)
  if (state$effect_requested) {
    state$effect_dds <- dds
    state$effect_input <- input
  }
  list(genes = genes, convergence_scope = "Native full/reduced coefficient flags retained; final dispersion-optimizer flags are not exposed")
}

extract_effect <- function(config, input, dds) {
  design <- input$effect_design
  names <- DESeq2::resultsNames(dds)
  require_that(identical(names, unlist(design$native_coefficient_names, use.names = FALSE)), "native_effect_coefficient_names_changed")
  contrast <- unlist(design$contrast, use.names = FALSE)
  # Serialize only fitted numerical state to detect accidental in-place changes;
  # no serialized object is accepted as input or written to disk.
  numerical_state <- function() list(coefficients = stats::coef(dds), dispersions = DESeq2::dispersions(dds),
    size_factors = DESeq2::sizeFactors(dds), information = S4Vectors::mcols(dds),
    matrix = attr(dds, "modelMatrix"), prior = attr(dds, "betaPriorVar"))
  before <- serialize(numerical_state(), NULL)
  effect <- capture("DESeq2_effects", DESeq2::results(dds, contrast = contrast, test = "Wald", lfcThreshold = 0,
                    minmu = 0.5, independentFiltering = FALSE, cooksCutoff = FALSE))
  unchanged <- identical(before, serialize(numerical_state(), NULL))
  require_that(unchanged && identical(rownames(effect), rownames(input$counts)), "native_primary_state_changed_during_effect")
  coefficients <- stats::coef(dds)
  prior <- unname(attr(dds, "betaPriorVar"))
  require_that(isFALSE(attr(dds, "betaPrior")) && length(prior) == ncol(input$full) && all(prior == 1e6),
               "unexpected_native_effect_regularization")
  z <- stats::qnorm((1 - config$effect$confidence_level) / 2, lower.tail = FALSE)
  information <- S4Vectors::mcols(dds)
  genes <- setNames(lapply(config$hypothesis_genes, function(name) {
    i <- match(name, rownames(input$counts))
    estimate <- unname(effect$log2FoldChange[i]); se <- unname(effect$lfcSE[i]); beta <- unname(coefficients[i, ])
    zero <- design$native_zero_convention[[name]]
    ok <- isTRUE(information$fullBetaConv[i]) && all(is.finite(beta)) && finite_number(estimate) && finite_number(se) && se > 0 &&
      is.finite(estimate-z*se) && is.finite(estimate+z*se)
    reason <- if (isTRUE(zero)) "native_zero_count_convention" else if (!ok) "native_effect_not_estimable" else NULL
    list(status = if (is.null(reason)) "ok" else "failed", failure_code = reason,
         native_log2_fold_change = number_or_null(estimate), native_standard_error_log2 = number_or_null(se),
         native_zero_convention = zero, coefficients_log2 = unname(lapply(beta, number_or_null)),
         log2_fold_change = if (is.null(reason)) estimate else NULL,
         standard_error_log2 = if (is.null(reason)) se else NULL,
         ci_lower_log2 = if (is.null(reason)) estimate-z*se else NULL,
         ci_upper_log2 = if (is.null(reason)) estimate+z*se else NULL)
  }), config$hypothesis_genes)
  list(schema_version = "native_deseq2_effects/1", status = if (all(vapply(genes, function(g) identical(g$status, "ok"), logical(1)))) "ok" else "partial_failure",
       error = NULL, design = design, genes = genes,
       method = list(api = "DESeq2::results(test=Wald, numeric contrast) on saved LRT fit", coefficient_refit = FALSE,
         dispersion_refit = FALSE, lfc_shrink = FALSE, coefficient_prior_learned = FALSE, min_mu = 0.5,
         interval_distribution = "normal",
         interval_scope = "Pointwise normal-Wald interval for a prespecified fitted normalized-mean log2 ratio; plug-in normalization and dispersion",
         p_values = "Not exposed for this secondary contrast; primary p/q retain the joint LRT meaning",
         beta_prior_variance_log2 = array(prior), primary_state_unchanged = unchanged))
}

main <- function(config_path) {
  config <- capture("request_validation", read_request(config_path))
  state$config <- config
  state$engine <- config$engine
  capture("native_version_check", version_check(config$engine))
  input <- capture("input_validation", read_inputs(config))
  state$evidence$input_validation <- list(panel_gene_ids = array(rownames(input$counts)),
    sample_ids = array(colnames(input$counts)), biological_unit_count = nrow(input$metadata),
    independence_verified = FALSE, independence_scope = "Unique declared IDs do not establish biological independence",
    full_design = input$full_diagnostic, reduced_design = input$reduced_diagnostic,
    temporal_columns = array(config$temporal_columns), temporal_names = array(colnames(input$full)[config$temporal_columns]),
    design_nesting = "exact_non_temporal_column_subset", design_selection = "supplied_fixed_matrices")
  native <- if (config$engine == "edger_qlf") fit_edger(config, input) else fit_deseq2(config, input)
  p_values <- vapply(native$genes, function(g) if (is.null(g$p_value)) NA_real_ else g$p_value, numeric(1))
  adjusted <- p.adjust(p_values, method = "BH", n = length(config$hypothesis_genes))
  for (i in seq_along(native$genes)) native$genes[[i]]$q_value <- number_or_null(adjusted[i])
  require_that(!state$conditions_truncated, "native_condition_capture_limit_exceeded")
  list(status = if (all(vapply(native$genes, function(g) identical(g$status, "ok"), logical(1)))) "ok" else "partial_failure",
       error = NULL, hypothesis_genes = array(config$hypothesis_genes), genes = native$genes,
       fdr_scope = "prespecified_hypothesis_genes_including_failed_positions", convergence_scope = native$convergence_scope,
       effects = list(status = "not_supported_by_this_protocol", scope = "Joint temporal-null test only; no endpoint prediction rows or effect contrast supplied"))
}

arguments <- commandArgs(trailingOnly = TRUE)
destination <- NULL
exit_status <- 1L
started <- proc.time()[["elapsed"]]
has_jsonlite <- requireNamespace("jsonlite", quietly = TRUE)
if (!has_jsonlite) {
  # Only this fixed string is used before JSON support is available.
  text <- '{"schema_version":"native_nb_result/1","status":"failed","clinical_release_allowed":false,"error":{"code":"jsonlite_unavailable"}}'
  if (length(arguments) == 4L && identical(arguments[c(1L, 3L)], c("--config", "--output"))) {
    destination <- tryCatch(output_path(arguments[4L]), error = function(e) NULL)
    if (!is.null(destination)) tryCatch(publish_no_clobber(text, destination),
      error = function(e) cat("native_nb no-clobber failure publication failed\n", file = stderr()))
  }
  cat(text, "\n", file = stderr())
  quit(save = "no", status = 1L)
}
result <- tryCatch({
  require_that(length(arguments) == 4L && identical(arguments[c(1L, 3L)], c("--config", "--output")), "invalid_cli_arguments")
  destination <- output_path(arguments[4L])
  main(arguments[2L])
}, error = function(error) list(status = "failed", error = list(
  code = if (inherits(error, "native_nb_error")) error$code else "native_or_input_error",
  stage = state$stage, message = substr(conditionMessage(error), 1L, 6000L))))
result <- c(list(schema_version = "native_nb_result/1", engine = state$engine,
                 clinical_release_allowed = FALSE, qualification_status = "candidate_unqualified",
                 actual_versions = state$versions, elapsed_seconds = proc.time()[["elapsed"]] - started),
            result, list(evidence = state$evidence, conditions = state$conditions,
                         conditions_truncated = state$conditions_truncated,
                         memory_peak_mb = NULL, memory_measurement_scope = "external_supervisor_required"))
if (state$effect_requested) {
  primary <- result
  prior_conditions <- length(state$conditions)
  failed_effect <- function(status, code, message) list(schema_version = "native_deseq2_effects/1", status = status,
      error = list(code = code, message = substr(message, 1L, 6000L)),
      design = if (!is.null(state$effect_input)) state$effect_input$effect_design else NULL,
      method = NULL, genes = NULL)
  effects <- if (!identical(primary$status, "ok")) failed_effect("not_run", "primary_incomplete", "Primary joint-test family is incomplete") else
    tryCatch(extract_effect(state$config, state$effect_input, state$effect_dds),
      error = function(e) failed_effect("failed", if (inherits(e, "native_nb_error")) e$code else "native_effect_error", conditionMessage(e)))
  effects$conditions <- if (length(state$conditions) > prior_conditions) state$conditions[seq.int(prior_conditions+1L, length(state$conditions))] else list()
  effects$conditions_truncated <- state$conditions_truncated && !isTRUE(primary$conditions_truncated)
  if (effects$conditions_truncated) {
    effects$status <- "failed"
    effects$error <- list(code = "native_condition_capture_limit_exceeded", message = "Native effect conditions exceeded the shared capture bound")
    effects$genes <- NULL
  }
  complete <- identical(primary$status, "ok") && identical(effects$status, "ok")
  result <- list(schema_version = "native_nb_effect_result/1",
    status = if (complete) "ok" else if (identical(primary$status, "failed")) "failed" else "partial_failure",
    error = if (complete) NULL else list(code = if (!identical(primary$status, "ok")) "primary_incomplete" else "effect_incomplete"),
    clinical_release_allowed = FALSE, qualification_status = "candidate_unqualified", primary = primary, effects = effects,
    elapsed_seconds = proc.time()[["elapsed"]] - started, memory_peak_mb = NULL, memory_measurement_scope = "external_supervisor_required")
}
check_json_tree(result)  # No NA/NaN/Inf is silently serialized as a successful estimate.
encoded <- jsonlite::toJSON(result, auto_unbox = TRUE, null = "null", na = "null", digits = NA, pretty = TRUE)
if (!is.null(destination)) {
  tryCatch({
    publish_no_clobber(encoded, destination)
    exit_status <- if (identical(result$status, "ok")) 0L else 1L
  }, error = function(error) cat(jsonlite::toJSON(list(schema_version = "native_nb_result/1",
      status = "failed", clinical_release_allowed = FALSE, error = list(
        code = if (inherits(error, "native_nb_error")) error$code else "result_publication_failed",
        stage = "output_publication", message = substr(conditionMessage(error), 1L, 6000L))),
      auto_unbox = TRUE), "\n", file = stderr()))
} else cat(encoded, "\n", file = stderr())
quit(save = "no", status = exit_status)
