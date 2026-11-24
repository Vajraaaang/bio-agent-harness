# Fixed DESeq2 comparison of independently declared bulk models. The caller
# supplies a frozen design and RLE offsets, with technical libraries combined.

.bulk_deseq2_labels <- function(values, length_expected) {
    is.character(values) && length(values) == length_expected &&
        !anyNA(values) && all(nzchar(trimws(values)))
}

.bulk_deseq2_design <- function(design, samples, name) {
    if (!is.matrix(design) || !is.numeric(design) || any(!is.finite(design)) ||
        !identical(rownames(design), samples)) {
        stop(name, " design must be a finite matrix aligned to the sample axis")
    }
    if (!.bulk_deseq2_labels(colnames(design), ncol(design)) || anyDuplicated(colnames(design)) ||
        ncol(design) < 1L || nrow(design) - ncol(design) < 2L ||
        qr(design)$rank != ncol(design)) {
        stop(name, " design requires named coefficients, full rank and residual degrees of freedom")
    }
}

.bulk_deseq2_replicates <- function(design, units, groups, n) {
    if (!.bulk_deseq2_labels(units, n) || !.bulk_deseq2_labels(groups, n) ||
        anyDuplicated(data.frame(unit = units, group = groups))) {
        stop("Declare one observation per biological unit and replication group")
    }
    group_counts <- table(groups)
    if (length(group_counts) < 2L || any(group_counts < 3L)) {
        stop("At least three distinct biological units per declared replication group are required")
    }
    if (anyDuplicated(units)) {
        # Repeated units require a fixed unit term in the model's column space.
        if (length(unique(units)) > ncol(design) ||
            qr(cbind(design, model.matrix(~ 0 + factor(units))))$rank != ncol(design)) {
            stop("Repeated biological units require an explicit fixed unit term")
        }
    }
    group_counts
}

.bulk_deseq2_fit <- function(counts, design, size_factors, coefficient, contrast, reduced) {
    design_names <- colnames(design)
    fitted_names <- make.names(ifelse(design_names == "(Intercept)", "Intercept", design_names))
    if (anyDuplicated(fitted_names)) stop("Full-design coefficient names collide after DESeq2 encoding")
    dds <- DESeq2::DESeqDataSetFromMatrix(
        countData = counts, colData = data.frame(row.names = colnames(counts)),
        design = design
    )
    DESeq2::sizeFactors(dds) <- size_factors
    dds <- DESeq2::estimateDispersions(dds, fitType = "mean", quiet = TRUE)
    if (!identical(attr(DESeq2::dispersionFunction(dds), "fitType"), "mean")) {
        stop("Unexpected DESeq2 dispersion trend")
    }
    if (!is.null(reduced)) {
        dds <- DESeq2::nbinomLRT(dds, full = design, reduced = reduced, quiet = TRUE)
        raw <- DESeq2::results(dds, test = "LRT", independentFiltering = FALSE)
        convergence <- S4Vectors::mcols(dds)
        return(list(
            probability = raw$pvalue,
            valid_effect = rep(TRUE, nrow(counts)),
            converged = (convergence$fullBetaConv %in% TRUE) & (convergence$reducedBetaConv %in% TRUE),
            effect = rep(NA_real_, nrow(counts)), effect_se = rep(NA_real_, nrow(counts))
        ))
    }
    dds <- DESeq2::nbinomWaldTest(dds, modelMatrix = design, betaPrior = FALSE, quiet = TRUE)
    if (!identical(DESeq2::resultsNames(dds), fitted_names)) {
        stop("DESeq2 returned an unexpected coefficient axis")
    }
    if (!is.null(coefficient)) {
        coefficient <- fitted_names[match(coefficient, design_names)]
        raw <- DESeq2::results(dds, name = coefficient, independentFiltering = FALSE)
        shrunk <- DESeq2::lfcShrink(dds, coef = coefficient, res = raw, type = "apeglm", quiet = TRUE)
    } else {
        raw <- DESeq2::results(dds, contrast = unname(contrast), independentFiltering = FALSE)
        shrunk <- DESeq2::lfcShrink(dds, res = raw, type = "ashr", quiet = TRUE)
    }
    list(
        probability = raw$pvalue,
        valid_effect = is.finite(shrunk$log2FoldChange) & is.finite(shrunk$lfcSE) & shrunk$lfcSE >= 0,
        converged = S4Vectors::mcols(dds)$betaConv %in% TRUE,
        effect = shrunk$log2FoldChange, effect_se = shrunk$lfcSE
    )
}

bulk_deseq2_sensitivity <- function(counts, design, biological_units, replicate_groups, size_factors,
                                    coefficient = NULL, contrast = NULL, reduced = NULL) {
    if (sum(!vapply(list(coefficient, contrast, reduced), is.null, logical(1))) != 1L) {
        stop("Select exactly one named coefficient, numeric contrast or nested reduced design")
    }
    if (!is.matrix(counts) || !is.numeric(counts) || any(dim(counts) < 1L) ||
        any(!is.finite(counts) | counts < 0 | counts > .Machine$integer.max | counts != floor(counts))) {
        stop("DESeq2 requires a finite nonnegative integer count matrix")
    }
    samples <- colnames(counts)
    genes <- rownames(counts)
    if (!.bulk_deseq2_labels(samples, ncol(counts)) || anyDuplicated(samples) ||
        !.bulk_deseq2_labels(genes, nrow(counts)) || anyDuplicated(genes)) {
        stop("Counts require unique gene and sample axis labels")
    }
    .bulk_deseq2_design(design, samples, "Full")
    replication <- .bulk_deseq2_replicates(design, biological_units, replicate_groups, ncol(counts))
    if (!is.numeric(size_factors) || !is.null(dim(size_factors)) ||
        length(size_factors) != length(samples) || any(!is.finite(size_factors) | size_factors <= 0) ||
        (!is.null(names(size_factors)) && !identical(names(size_factors), samples)) ||
        any(colSums(counts) == 0)) {
        stop("Supply aligned positive RLE factors for nonempty count libraries")
    }
    if (!is.null(coefficient) &&
        (!.bulk_deseq2_labels(coefficient, 1L) || !coefficient %in% colnames(design))) {
        stop("Select a single named Wald coefficient in the full design")
    }
    if (!is.null(contrast) &&
        (!is.numeric(contrast) || !is.null(dim(contrast)) || !identical(names(contrast), colnames(design)) ||
         any(!is.finite(contrast)) || !any(contrast != 0))) {
        stop("Supply a nonzero numeric contrast with the exact full-design coefficient axis")
    }
    if (!is.null(reduced)) {
        .bulk_deseq2_design(reduced, samples, "Reduced")
        if (ncol(reduced) >= ncol(design) || qr(cbind(design, reduced))$rank != ncol(design)) {
            stop("Require a strictly nested reduced design for the LRT")
        }
    }
    shrinkage <- if (!is.null(coefficient)) "apeglm" else if (!is.null(contrast)) "ashr" else NULL
    selected_method <- if (is.null(reduced)) "Wald" else "LRT"
    fitted_df <- if (is.null(reduced)) 1L else ncol(design) - ncol(reduced)
    method <- list(
        solver = "DESeq2", version = as.character(utils::packageVersion("DESeq2")),
        test = selected_method, test_df = fitted_df, shrinkage = shrinkage,
        shrinkage_version = if (is.null(shrinkage)) NULL else as.character(utils::packageVersion(shrinkage)),
        coefficient = coefficient, contrast = contrast, dispersion_fit = "mean",
        normalization = "supplied_rle_size_factors", low_count_filter = "mean_RLE_normalized_counts_at_least_one",
        multiplicity = "unweighted_BH_failed_eligible_tests_padded_with_one",
        effect_scale = if (is.null(reduced)) "log2_fold_change" else NULL,
        group_observation_counts = replication
    )
    normalized_mean <- drop(counts %*% (1 / size_factors)) / ncol(counts)
    if (any(!is.finite(normalized_mean))) stop("Invalid RLE-normalized mean counts")
    eligible <- normalized_mean >= 1
    separated <- Reduce(`|`, lapply(split(seq_along(samples), replicate_groups), function(columns) {
        rowSums(counts[, columns, drop = FALSE]) == 0
    }))
    fit_rows <- which(eligible & !separated)
    rows <- data.frame(
        gene_id = genes, estimate_status = ifelse(eligible, "failed", "filtered"),
        missing_reason = ifelse(!eligible, "independent_low_count_filter",
                                ifelse(separated, "zero_condition_marginal", "deseq2_fit_unavailable")),
        effect = NA_real_, effect_se = NA_real_, p_value = NA_real_, q_value = NA_real_
    )
    if (length(fit_rows)) {
        fit <- .bulk_deseq2_fit(counts[fit_rows, , drop = FALSE], design, size_factors, coefficient, contrast, reduced)
        if (!all(lengths(fit) == length(fit_rows))) stop("DESeq2 returned an incomplete hypothesis inventory")
        probability <- fit$probability
        valid <- fit$converged & fit$valid_effect &
            is.finite(probability) & probability >= 0 & probability <= 1
        passing <- fit_rows[valid]
        rows$estimate_status[passing] <- "estimated"
        rows$missing_reason[passing] <- NA_character_
        rows$effect[passing] <- fit$effect[valid]
        rows$effect_se[passing] <- fit$effect_se[valid]
        rows$p_value[passing] <- probability[valid]
        padded <- rep(1, nrow(counts))
        padded[passing] <- rows$p_value[passing]
        adjusted <- rep(NA_real_, nrow(counts))
        adjusted[eligible] <- p.adjust(padded[eligible], method = "BH")
        rows$q_value[passing] <- adjusted[passing]
    }
    list(rows = rows, method = method)
}
