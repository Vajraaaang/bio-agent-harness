# Latent scores are diagnostic artifacts. They do not change the primary model.
.latent_ids_valid <- function(ids) {
    is.character(ids) && !anyNA(ids) && all(nzchar(trimws(ids))) && !anyDuplicated(ids)
}

.latent_design_summary <- function(design) {
    rank <- qr(design)$rank
    full_rank <- rank == ncol(design)
    condition <- if (full_rank) kappa(design, exact = TRUE) else Inf
    list(rank = rank, columns = ncol(design), residual_df = nrow(design) - rank,
         condition_number = if (is.finite(condition)) condition else NULL,
         passes = full_rank && nrow(design) > rank && condition <= 1000)
}

.latent_check_design <- function(design, samples, label) {
    if (!is.matrix(design) || !is.numeric(design) || !all(is.finite(design)) ||
        ncol(design) < 1L || !identical(rownames(design), samples) ||
        !.latent_ids_valid(colnames(design))) {
        stop(label, " design must contain finite numeric columns and align to the count sample IDs")
    }
    summary <- .latent_design_summary(design)
    if (!summary$passes) stop(label, " design rank, residual degrees of freedom, or conditioning failed")
    if (max(abs(qr.resid(qr(design), rep(1, length(samples))))) > 1e-8) {
        stop(label, " design must include an intercept")
    }
    summary
}

.latent_controls <- function(controls, counts, size_factors, k) {
    fields <- c("origin", "scope", "source", "gene_ids")
    if (!is.list(controls) || !setequal(names(controls), fields) || anyDuplicated(names(controls)) ||
        !is.character(controls$origin) || length(controls$origin) != 1L ||
        !controls$origin %in% c("spike_ins", "predeclared_external") ||
        !identical(controls$scope, "independent") ||
        !is.character(controls$source) || length(controls$source) != 1L ||
        !.latent_ids_valid(controls$source) || !.latent_ids_valid(controls$gene_ids) ||
        any(!controls$gene_ids %in% rownames(counts))) {
        stop("RUVg requires independent spike-in or predeclared external control provenance")
    }
    chosen <- log1p(sweep(counts[controls$gene_ids, , drop = FALSE], 2L, size_factors, "/"))
    centered <- scale(t(chosen), scale = FALSE)
    # Use RUVg's singular-value tolerance so roundoff cannot supply a control factor.
    if (length(controls$gene_ids) < k || sum(svd(centered, nu = 0L, nv = 0L)$d > 1e-8) < k) {
        stop("RUVg control rank cannot support the declared factor count")
    }
    chosen
}

.latent_with_seed <- function(seed, run) {
    old_kind <- RNGkind()
    old_seed <- get0(".Random.seed", envir = .GlobalEnv, inherits = FALSE)
    on.exit({
        do.call(RNGkind, as.list(old_kind))
        if (is.null(old_seed)) {
            if (exists(".Random.seed", envir = .GlobalEnv, inherits = FALSE)) {
                rm(".Random.seed", envir = .GlobalEnv)
            }
        } else {
            assign(".Random.seed", old_seed, envir = .GlobalEnv)
        }
    })
    set.seed(seed, kind = "Mersenne-Twister", normal.kind = "Inversion", sample.kind = "Rejection")
    run()
}

.latent_sva <- function(normalized, full, reduced, method, seed, max_factors) {
    if (nrow(normalized) < 10L || nrow(normalized) <= ncol(full)) {
        stop("svaseq requires enough expressed rows for the frozen factor estimator")
    }
    ranges <- matrixStats::rowRanges(normalized, useNames = FALSE)
    tolerance <- 64 * .Machine$double.eps * pmax(1, ranges[, 2L])
    no_factors <- matrix(numeric(), nrow = ncol(normalized), ncol = 0L)
    if (!any(ranges[, 2L] - ranges[, 1L] > tolerance)) return(no_factors)
    .latent_with_seed(seed, function() {
        k <- sva::num.sv(log1p(normalized), full, method = method, B = 20L)
        if (length(k) != 1L || !is.finite(k) || k < 0L || k != floor(k) || k > max_factors) {
            stop("svaseq estimated factor count cannot preserve full-design residual support")
        }
        if (k == 0L) return(no_factors)
        fit <- NULL
        # svaseq writes progress even when no warnings occur.
        invisible(capture.output(fit <- sva::svaseq(normalized, full, reduced, n.sv = k,
                                                   method = "irw", B = 5L, constant = 1)))
        if (!is.matrix(fit$sv) || ncol(fit$sv) != k) stop("svaseq returned an unexpected factor count")
        fit$sv
    })
}

bulk_latent_diagnostics <- function(counts, full_design, reduced_design, method,
                                    sv_method = "be", controls = NULL, n_factors = NULL, seed = 0L,
                                    size_factors = NULL) {
    if (!is.matrix(counts) || !is.numeric(counts) || any(!is.finite(counts)) ||
        any(counts < 0 | counts != floor(counts) | counts > 2^53 - 1) ||
        !.latent_ids_valid(rownames(counts)) || !.latent_ids_valid(colnames(counts)) ||
        !nrow(counts) || !ncol(counts)) {
        stop("Latent batch diagnostics require a declared complete raw-count matrix with nonempty libraries")
    }
    libraries <- colSums(counts)
    if (any(!is.finite(libraries) | libraries <= 0)) stop("Latent diagnostics require nonempty libraries")
    if (!identical(method, "svaseq") && !identical(method, "ruvg")) {
        stop("Latent diagnostic method must be exactly 'svaseq' or 'ruvg'")
    }
    if (!identical(sv_method, "be") && !identical(sv_method, "leek")) {
        stop("svaseq factor estimator must be exactly 'be' or 'leek'")
    }
    if (!is.numeric(seed) || length(seed) != 1L || !is.finite(seed) ||
        seed < 0 || seed > .Machine$integer.max || seed != floor(seed)) {
        stop("Require a bounded integer RNG seed")
    }
    samples <- colnames(counts)
    design <- list(full = .latent_check_design(full_design, samples, "Full"),
                   reduced = .latent_check_design(reduced_design, samples, "Reduced"))
    delta <- qr.resid(qr(full_design), reduced_design)
    if (design$reduced$rank >= design$full$rank ||
        max(abs(delta) / pmax(1, abs(reduced_design))) > 1e-8) {
        stop("Reduced design must be strictly nested in the full design")
    }
    max_factors <- design$full$residual_df - 1L
    warnings <- character()
    conditioning <- list(full_and_reduced_designs_frozen = TRUE,
                         effect_inference_conditional_on_latent_estimation = TRUE)
    scores <- withCallingHandlers({
        if (is.null(size_factors)) {
            size_factors <- libraries * edgeR::normLibSizes(counts, method = "TMM")
            conditioning$normalization <- "TMM_effective_library_sizes"
        } else {
            conditioning$normalization <- "supplied_RLE_size_factors"
        }
        if (!is.numeric(size_factors) || !is.null(dim(size_factors)) ||
            length(size_factors) != length(samples) || any(!is.finite(size_factors) | size_factors <= 0) ||
            (!is.null(names(size_factors)) && !identical(names(size_factors), samples))) {
            stop("Latent diagnostics require aligned positive size factors")
        }
        relative_factors <- exp(log(size_factors) - mean(log(size_factors)))
        if (any(!is.finite(relative_factors) | relative_factors <= 0)) {
            stop("Latent diagnostics require finite positive relative size factors")
        }
        if (method == "svaseq") {
            if (!is.null(controls) || !is.null(n_factors)) stop("svaseq uses its frozen factor estimator")
            conditioning$num_sv_method <- sv_method
            conditioning$seed <- as.integer(seed)
            conditioning$permutations <- if (sv_method == "be") 20L else NULL
            expressed <- rowSums(counts) > 0
            conditioning$expressed_feature_count <- sum(expressed)
            normalized <- sweep(counts[expressed, , drop = FALSE], 2L, relative_factors, "/")
            .latent_sva(normalized, full_design, reduced_design, sv_method, seed, max_factors)
        } else {
            if (!is.numeric(n_factors) || length(n_factors) != 1L || !is.finite(n_factors) ||
                n_factors < 1L || n_factors != floor(n_factors) || n_factors > max_factors) {
                stop("RUVg requires a prespecified factor count preserving full-design residual support")
            }
            reference <- .latent_controls(controls, counts, relative_factors, n_factors)
            fit <- RUVSeq::RUVg(reference, seq_len(nrow(reference)), n_factors, isLog = TRUE, round = FALSE)
            if (!is.matrix(fit$W) || ncol(fit$W) != n_factors) {
                stop("RUVg returned fewer factors than the declared count")
            }
            fit$W
        }
    }, warning = function(problem) {
        warnings <<- unique(c(warnings, conditionMessage(problem)))
        invokeRestart("muffleWarning")
    })
    if (!is.matrix(scores) || nrow(scores) != length(samples) || !all(is.finite(scores))) {
        stop("Latent diagnostic returned invalid scores")
    }
    dimnames(scores) <- list(samples, paste0("latent_", seq_len(ncol(scores)))[seq_len(ncol(scores))])
    package <- if (method == "svaseq") "sva" else "RUVSeq"
    list(method = method, package_version = as.character(utils::packageVersion(package)),
         n_factors = ncol(scores), scores = scores, design = design,
         augmented_design = list(full = .latent_design_summary(cbind(full_design, scores)),
                                 reduced = .latent_design_summary(cbind(reduced_design, scores))),
         conditioning = conditioning, controls = controls, warnings = warnings,
         diagnostic_only = TRUE)
}
