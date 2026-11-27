#!/usr/bin/env Rscript
# ERQL-1.2 synthetic candidate. The controlled adapter signs the exact design.

options(warn = 1)

require_that <- function(test, message) {
  if (!isTRUE(test)) stop(message, call. = FALSE)
}

string_array <- function(value, size) {
  is.character(value) && length(value) == size && !anyNA(value) && all(nzchar(value))
}

json_array <- function(value) unname(as.list(value))

typed_number <- function(value) {
  if (is.finite(value)) return(list(state = "finite", value = unname(value)))
  if (is.na(value)) return(list(state = "missing", value = NULL))
  state <- if (value > 0) "positive_infinity" else "negative_infinity"
  list(state = state, value = NULL)
}

native_field <- function(value, size, label) {
  require_that(is.numeric(value) && length(value) %in% c(1L, size),
               paste("Invalid edgeR output field", label))
  rep_len(value, size)
}

locked_versions <- function() {
  # The candidate changes its driver and filter while using the frozen dependency image.
  lock <- jsonlite::fromJSON("/opt/devharness-erql/source-lock.json", simplifyVector = FALSE)
  actual <- list(R = as.character(getRversion()),
                 BiocVersion = packageDescription("BiocVersion", fields = "Version"))
  require_that(identical(lock$profile, "ERQL-1.1") &&
                 identical(actual$R, lock$R) && identical(actual$BiocVersion, lock$BiocVersion),
               "R or Bioconductor version does not match the ERQL lock")
  packages <- setNames(lapply(lock$packages, function(package) {
    version <- packageDescription(package$package, fields = "Version")
    require_that(identical(version, package$version),
                 paste("Package version does not match the ERQL lock:", package$package))
    version
  }), vapply(lock$packages, function(package) package$package, character(1)))
  c(actual, packages)
}

tmm_reference <- function(counts, library_sizes) {
  # The pinned normLibSizes.default removes all-zero rows before this rule.
  nonzero <- counts[rowSums(counts > 0) > 0L, , drop = FALSE]
  quantiles <- suppressWarnings(getFromNamespace(".calcFactorQuantile", "edgeR")(
    data = nonzero, lib.size = library_sizes, p = 0.75
  ))
  if (median(quantiles) < 1e-20) return(which.max(colSums(sqrt(nonzero))))
  which.min(abs(quantiles - mean(quantiles)))
}

validate_design <- function(request, design, contrast) {
  regime <- request$regime
  groups <- request$groups
  units <- request$unit_ids
  levels <- c(request$denominator, request$numerator)
  require_that(string_array(regime, 1L) && regime %in% c("B-I", "B-P", "S-I", "S-P", "P-P") &&
                 setequal(groups, levels) && string_array(request$population, 1L),
               "Unsupported ERQL-1.2 regime or scope")
  if (regime == "P-P") {
    basis <- request$population_label_basis
    require_that(string_array(request$condition, 1L) && identical(request$condition, request$population) &&
                   is.list(basis) && setequal(names(basis), c("origin", "source_sha256")) &&
                   string_array(basis$origin, 1L) &&
                   basis$origin %in% c("orthogonal_measurement", "independent_assignment") &&
                   string_array(basis$source_sha256, 1L) && grepl("^[a-f0-9]{64}$", basis$source_sha256),
                 "ERQL-1.2 P-P requires one fixed condition and source-bound independent labels")
  } else {
    require_that(is.null(request$condition) && is.null(request$population_label_basis) &&
                   (!startsWith(regime, "B-") || identical(request$population, "bulk")),
                 "Invalid ERQL-1.2 non-P-P analysis scope")
  }
  target <- if (tail(contrast, 1L) == 1L) levels[2L] else levels[1L]
  require_that(all(design[, ncol(design)] == (groups == target)), "Invalid ERQL-1.2 signed target column")
  if (regime %in% c("B-P", "S-P", "P-P")) {
    memberships <- table(units, groups)
    nuisance <- design[, -ncol(design), drop = FALSE]
    blocks <- model.matrix(~ factor(units) - 1L)
    require_that(nrow(memberships) >= 3L && all(memberships == 1L) &&
                   qr(cbind(nuisance, blocks))$rank == ncol(nuisance),
                 "ERQL-1.2 paired design requires at least three complete biological-unit blocks")
  } else {
    require_that(!anyDuplicated(units) && min(table(groups)) >= 3L,
                 "ERQL-1.2 independent design requires at least three distinct units per group")
  }
}

result_for <- function(request, versions, counts, keep, filtering,
                       statistics = NULL, normalization = NULL, fit = NULL) {
  support <- function(gene, label) {
    length(unique(request$unit_ids[request$groups == label & counts[gene, ] > 0]))
  }
  fitted_index <- cumsum(keep)
  genes <- lapply(seq_along(request$feature_ids), function(gene) {
    tested <- keep[gene]
    gene_statistics <- if (tested) {
      lapply(statistics, function(values) typed_number(values[fitted_index[gene]]))
    } else NULL
    list(gene_id = request$feature_ids[gene], status = if (tested) "tested" else "filtered",
         filter_reason = if (tested) NULL else "filter_by_expr",
         support = list(numerator = support(gene, request$numerator),
                        denominator = support(gene, request$denominator)),
         statistics = gene_statistics, interval = NULL,
         interval_reason = if (tested) "no_interval_in_profile" else "filtered_before_fit")
  })
  list(schema_version = "erql_result/1", profile = request$profile,
       feature_ids = json_array(request$feature_ids), profile_ids = json_array(request$profile_ids),
       groups = json_array(request$groups), package_versions = versions,
       filter = filtering, normalization = normalization, fit = fit, genes = genes)
}

compute <- function(request, versions) {
  require_that(identical(request$schema_version, "erql_request/2") && identical(request$profile, "ERQL-1.2"),
               "Unsupported ERQL request or profile")
  counts <- request$counts
  design <- request$design
  contrast <- request$contrast
  require_that(is.matrix(counts) && is.numeric(counts) && nrow(counts) %in% 6:256 &&
                 ncol(counts) %in% 1:8192 && length(counts) <= 1000000L &&
                 all(is.finite(counts)) && all(counts >= 0 & counts < 2^53) &&
                 all(counts == floor(counts)), "Invalid ERQL count matrix")
  require_that(is.matrix(design) && is.numeric(design) && nrow(design) == nrow(counts) &&
                 ncol(design) %in% 2:128 && ncol(design) < nrow(design) &&
                 all(design[, 1L] == 1) && all(design %in% 0:1) &&
                 qr(design)$rank == ncol(design), "Invalid ERQL design matrix")
  require_that(is.numeric(contrast) && is.null(dim(contrast)) &&
                 length(contrast) == ncol(design) && all(contrast[-length(contrast)] == 0) &&
                 tail(contrast, 1L) %in% c(-1, 1), "Invalid ERQL contrast")
  require_that(string_array(request$feature_ids, ncol(counts)) &&
                 !anyDuplicated(request$feature_ids) &&
                 string_array(request$profile_ids, nrow(counts)) &&
                 !anyDuplicated(request$profile_ids) &&
                 string_array(request$unit_ids, nrow(counts)) &&
                 string_array(request$groups, nrow(counts)) &&
                 string_array(request$numerator, 1L) && string_array(request$denominator, 1L) &&
                 request$numerator != request$denominator &&
                 all(c(request$numerator, request$denominator) %in% request$groups),
               "Invalid ERQL feature, profile, unit, or group identifiers")
  validate_design(request, design, contrast)

  counts <- t(counts)
  dimnames(counts) <- list(request$feature_ids, request$profile_ids)
  dimnames(design) <- list(request$profile_ids, paste0("coefficient_", seq_len(ncol(design))))
  group <- factor(request$groups, levels = unique(request$groups))
  library_sizes <- colSums(counts)
  require_that(all(is.finite(library_sizes) & library_sizes > 0 & library_sizes < 2^53),
               "Invalid ERQL pre-filter library sizes")
  y <- edgeR::DGEList(counts = counts, group = group)
  keep <- edgeR::filterByExpr(y, design = design, min.count = 10, min.total.count = 15,
                              large.n = 10, min.prop = 0.7)
  require_that(is.logical(keep) && length(keep) == nrow(counts) && !anyNA(keep),
               "ERQL filter returned an invalid mask")
  min_sample_size <- 1 / max(stats::hat(design))
  if (min_sample_size > 10) min_sample_size <- 10 + (min_sample_size - 10) * 0.7
  cpm_cutoff <- 10 / median(library_sizes) * 1e6
  filtering <- list(keep = json_array(keep), min_sample_size = min_sample_size,
                    cpm_cutoff = cpm_cutoff, pre_filter_library_sizes = json_array(library_sizes))
  if (!any(keep)) return(result_for(request, versions, counts, keep, filtering))

  y <- y[keep, , keep.lib.sizes = FALSE]
  retained_sizes <- y$samples$lib.size
  require_that(all(is.finite(retained_sizes) & retained_sizes > 0),
               "ERQL filter produced an empty or invalid retained library")
  reference <- tmm_reference(y$counts, retained_sizes)
  y <- edgeR::normLibSizes(y, method = "TMM", refColumn = NULL, logratioTrim = 0.3,
                          sumTrim = 0.05, doWeighting = TRUE, Acutoff = -1e10)
  factors <- y$samples$norm.factors
  require_that(all(is.finite(factors) & factors > 0), "Invalid ERQL TMM factors")

  fit <- edgeR::glmQLFit(y, design = design, legacy = FALSE, robust = TRUE,
                         abundance.trend = TRUE, dispersion = NULL, prior.count = 0.125,
                         winsor.tail.p = c(0.05, 0.1), top.proportion = NULL)
  test <- edgeR::glmQLFTest(fit, contrast = contrast)
  n_tested <- sum(keep)
  require_that(identical(rownames(test$table), request$feature_ids[keep]) &&
                 length(fit$top.proportion) == 1L && is.finite(fit$top.proportion) &&
                 is.numeric(fit$dispersion) && length(fit$dispersion) %in% c(1L, n_tested) &&
                 all(is.finite(fit$dispersion) & fit$dispersion >= 0) &&
                 is.null(fit$df.residual.zeros) && !any(fit$failed), "Invalid ERQL fitted result")
  statistics <- list(logFC = test$table$logFC, logCPM = test$table$logCPM,
                     F = test$table$F, p = test$table$PValue,
                     df_test = test$df.test, df_total = test$df.total, df_prior = fit$df.prior,
                     df_residual_adj = fit$df.residual.adj, s2_post = fit$s2.post)
  for (label in names(statistics)) {
    statistics[[label]] <- native_field(statistics[[label]], n_tested, label)
  }
  probability <- statistics$p
  require_that(all(is.finite(probability) & probability >= 0 & probability <= 1),
               "Invalid ERQL fitted probabilities")

  dispersion <- lapply(fit$dispersion, typed_number)
  normalization <- list(library_sizes = json_array(retained_sizes), factors = json_array(factors),
                        reference_profile_id = request$profile_ids[reference])
  fit_summary <- list(top_proportion = fit$top.proportion,
                      dispersion = list(state = unname(lapply(dispersion, `[[`, "state")),
                                        value = unname(lapply(dispersion, `[[`, "value"))))
  result_for(request, versions, counts, keep, filtering, statistics, normalization, fit_summary)
}

publish <- function(result, destination) {
  temporary <- tempfile(pattern = ".native-nb-", tmpdir = dirname(destination))
  on.exit(unlink(temporary), add = TRUE)
  jsonlite::write_json(result, temporary, auto_unbox = TRUE, digits = NA, na = "null", null = "null")
  require_that(isTRUE(Sys.chmod(temporary, mode = "0600")), "ERQL result permissions failed")
  require_that(isTRUE(suppressWarnings(file.link(temporary, destination))),
               "ERQL atomic result publication failed")
}

main <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  require_that(identical(args, c("--config", "/input/request.json", "--output", "/output/result.json")),
               "Expected fixed ERQL config and output arguments")
  versions <- locked_versions()
  request <- jsonlite::fromJSON(args[2L], simplifyVector = TRUE)
  publish(compute(request, versions), args[4L])
}

main()
