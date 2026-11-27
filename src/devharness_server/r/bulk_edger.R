# Sourceable generalized edgeR v4 bulk model. The coordinator supplies frozen
# named matrices and explicitly declared biological units and replicate groups.

bulk_edger_design <- function(design, samples, label) {
    if (!is.matrix(design) || !is.numeric(design) || any(!is.finite(design)) ||
        !identical(rownames(design), samples) || is.null(colnames(design)) ||
        anyDuplicated(colnames(design)) || ncol(design) < 1 || nrow(design) <= ncol(design) ||
        qr(design)$rank != ncol(design) || kappa(design, exact = TRUE) > 1000) {
        stop("Invalid, unaligned or non-estimable ", label, " design")
    }
    design
}

bulk_edger_hypothesis <- function(design, contrast, reduced) {
    if (is.null(contrast) == is.null(reduced)) stop("Supply one contrast or one nested reduced design")
    if (is.null(reduced)) {
        if (!is.numeric(contrast) || !identical(names(contrast), colnames(design)) ||
            any(!is.finite(contrast)) || !any(contrast != 0)) stop("Invalid directed contrast")
        return(unname(contrast))
    }
    reduced <- bulk_edger_design(reduced, rownames(design), "reduced")
    rank <- ncol(reduced)
    if (rank >= ncol(design) || qr(cbind(design, reduced))$rank != ncol(design)) {
        stop("Reduced design must be nested and strictly smaller")
    }
    coordinates <- qr.coef(qr(design), reduced)
    qr.Q(qr(coordinates), complete = TRUE)[, seq.int(rank + 1L, ncol(design)), drop = FALSE]
}

bulk_edger_replication <- function(design, biological_units, replicate_groups, paired) {
    samples <- rownames(design)
    for (axis in list(biological_units, replicate_groups)) {
        if (!identical(names(axis), samples) || !is.character(axis) ||
            anyNA(axis) || any(!nzchar(axis))) stop("Explicit aligned biological units and groups required")
    }
    members <- table(biological_units, replicate_groups)
    if (any(colSums(members > 0) < 3)) stop("At least three biological units are required in each group")
    if (!anyDuplicated(biological_units)) {
        if (paired) stop("A paired model requires repeated biological units")
        return(invisible(NULL))
    }
    if (!paired || ncol(members) != 2 || any(members != 1)) {
        stop("Repeated biological units require complete fixed pairs or a separately approved mixed model")
    }
    donor_basis <- model.matrix(~ factor(biological_units))
    if (qr(cbind(design, donor_basis))$rank != ncol(design)) {
        stop("Paired model must retain the complete biological-unit fixed-effect span")
    }
}

bulk_edger_normalization <- function(counts, measurement, size_factors, tximport,
                                     normalization = NULL) {
    libraries <- colSums(counts)
    if (any(libraries <= 0)) stop("Empty or invalid count library")
    if (!is.null(normalization) && !identical(normalization, "RLE") &&
        !identical(normalization, "TMM")) stop("Unsupported frozen count normalization")
    if (measurement == "tximport_estimated_counts") {
        if (identical(normalization, "RLE") || !is.null(size_factors) ||
            !is.list(tximport) || !"length" %in% names(tximport) ||
            !identical(tximport$countsFromAbundance, "no") ||
            !identical(tximport$counts, counts) ||
            !identical(dimnames(tximport$length), dimnames(counts)) ||
            !is.numeric(tximport$length) || any(!is.finite(tximport$length)) ||
            any(tximport$length <= 0)) {
            stop("tximport requires unscaled estimated counts and aligned positive effective lengths")
        }
        dge <- edgeR::DGEListFromTximport(tximport)
        dge <- edgeR::normLibSizes(dge, method = "TMM")
        offset <- edgeR::getOffset(dge)
        if (!identical(dim(offset), dim(counts)) || any(!is.finite(offset))) {
            stop("tximport failed to produce a finite two-dimensional offset")
        }
        relative <- sweep(offset, 1L, rowMeans(offset), "-")
        return(list(dge = dge, abundance = rowMeans(counts / exp(relative)),
                    method = "tximport_effective_length_and_TMM_2D_offset"))
    }
    if (measurement != "raw_counts" || !is.null(tximport) || any(counts != floor(counts))) {
        stop("Raw-count bulk requires nonnegative integer counts")
    }
    if (identical(normalization, "TMM")) {
        dge <- edgeR::normLibSizes(edgeR::DGEList(counts), method = "TMM")
        effective <- dge$samples$lib.size * dge$samples$norm.factors
        if (any(!is.finite(effective) | effective <= 0)) stop("Invalid effective TMM library sizes")
        relative <- exp(log(effective) - mean(log(effective)))
        return(list(dge = dge, abundance = drop(counts %*% (1 / relative)) / ncol(counts),
                    method = "TMM"))
    }
    if (!is.numeric(size_factors) || !identical(names(size_factors), colnames(counts)) ||
        any(!is.finite(size_factors) | size_factors <= 0)) {
        stop("Raw-count bulk requires integer counts and aligned positive full-family RLE factors")
    }
    factors <- exp(log(size_factors) - mean(log(size_factors)))
    list(dge = edgeR::DGEList(counts, lib.size = factors * exp(mean(log(libraries)))),
         abundance = drop(counts %*% (1 / factors)) / ncol(counts),
         method = if (is.null(normalization)) "full_family_RLE" else "RLE")
}

bulk_count_filter <- function(normal, design, filter_mode) {
    if (identical(filter_mode, "filterByExpr")) return(edgeR::filterByExpr(normal$dge, design = design))
    if (is.null(filter_mode) || identical(filter_mode, "normalized_mean_ge_1")) return(normal$abundance >= 1)
    stop("Unsupported frozen count filter")
}

bulk_count_details <- function(normal, filter_mode, eligible) {
    samples <- normal$dge$samples
    details <- list(normalization = normal$method,
                    filter = if (is.null(filter_mode)) "label_blind_overall_normalized_mean_at_least_one" else filter_mode,
                    n_eligible_genes = sum(eligible), lib.size = unname(samples$lib.size),
                    norm.factors = unname(samples$norm.factors))
    # Raw-count offsets have one value per library. Tximport uses the verified
    # gene-by-library offset matrix in DGEListFromTximport.
    if (is.null(normal$dge$offset)) details$offset <- log(samples$lib.size * samples$norm.factors)
    details
}

# Moderation quantities can be common across genes or gene-specific. Keep the
# original gene inventory, including unavailable rows and genuine infinite DF.
bulk_model_quantities <- function(gene_count, fitted, quantities) {
    lapply(quantities, function(value) {
        full <- rep(NA_real_, gene_count)
        if (length(fitted)) {
            if (!length(value) %in% c(1L, length(fitted))) stop("Unexpected fitted quantity axis")
            full[fitted] <- as.numeric(value)
        }
        full
    })
}

bulk_edger_count <- function(counts, design, biological_units, replicate_groups,
                             measurement = "raw_counts", size_factors = NULL, tximport = NULL,
                             contrast = NULL, reduced = NULL, test = "qlf", paired = FALSE,
                             abundance_trend = TRUE, normalization = NULL, filter_mode = NULL) {
    if (!(test %in% c("qlf", "lrt")) || length(test) != 1L ||
        !is.logical(paired) || length(paired) != 1L || is.na(paired)) stop("Unsupported frozen edgeR policy")
    version <- utils::packageVersion("edgeR")
    if (version < "4.10.0" || version >= "5.0.0") stop("Pinned edgeR v4 with tximport support required")
    if (!is.matrix(counts) || !is.numeric(counts) || any(!is.finite(counts)) ||
        any(counts < 0 | counts > 2^53 - 1) || is.null(rownames(counts)) ||
        is.null(colnames(counts)) || anyDuplicated(rownames(counts)) ||
        anyDuplicated(colnames(counts)) || nrow(counts) < 1) stop("Invalid count family")
    design <- bulk_edger_design(design, colnames(counts), "full")
    hypothesis <- bulk_edger_hypothesis(design, contrast, reduced)
    bulk_edger_replication(design, biological_units, replicate_groups, paired)
    normal <- bulk_edger_normalization(counts, measurement, size_factors, tximport, normalization)
    eligible <- bulk_count_filter(normal, design, filter_mode)
    if (is.null(abundance_trend)) abundance_trend <- sum(eligible) >= 500L
    if (!identical(abundance_trend, TRUE) && !identical(abundance_trend, FALSE)) {
        stop("abundance_trend must be a frozen boolean or the eligible-count rule")
    }
    quantities <- if (test == "qlf") {
        list(F = NULL, df.prior = NULL, df.residual.adj = NULL, df.total = NULL, s2.post = NULL)
    } else list(LR = NULL)
    rows <- data.frame(gene_id = rownames(counts), estimate_status = ifelse(eligible, "failed", "filtered"),
        missing_reason = ifelse(eligible, "invalid_or_failed_edger_test", "independent_low_count_filter"),
        effect = NA_real_, p_value = NA_real_, q_value = NA_real_, stringsAsFactors = FALSE)
    df_test <- if (is.matrix(hypothesis)) ncol(hypothesis) else 1L
    if (any(eligible)) {
        dataset <- normal$dge[eligible, , keep.lib.sizes = TRUE]
        if (test == "qlf") {
            fit <- edgeR::glmQLFit(dataset, design = design, legacy = FALSE, dispersion = NULL,
                                  robust = TRUE, abundance.trend = abundance_trend)
            tested <- edgeR::glmQLFTest(fit, contrast = hypothesis)
            observed <- tested$table
            quantities <- list(F = observed$F, df.prior = fit$df.prior, df.residual.adj = fit$df.residual.adj,
                               df.total = tested$df.total, s2.post = fit$s2.post)
        } else {
            dataset <- edgeR::estimateDisp(dataset, design = design, robust = TRUE)
            fit <- edgeR::glmFit(dataset, design = design)
            observed <- edgeR::glmLRT(fit, contrast = hypothesis)$table
            quantities <- list(LR = observed$LR)
        }
        if (!identical(rownames(observed), rownames(counts)[eligible])) stop("Unexpected fitted gene inventory")
        good <- is.finite(observed$PValue) & observed$PValue >= 0 & observed$PValue <= 1
        if (df_test == 1L) good <- good & is.finite(observed$logFC)
        if (!is.null(fit$failed)) good <- good & (fit$failed %in% FALSE)
        selected <- which(eligible)[good]
        rows$estimate_status[selected] <- "estimated"
        rows$missing_reason[selected] <- NA_character_
        rows$p_value[selected] <- observed$PValue[good]
        if (df_test == 1L) rows$effect[selected] <- observed$logFC[good]
        padded <- rows$p_value[eligible]
        padded[is.na(padded)] <- 1
        adjusted <- p.adjust(padded, method = "BH")
        rows$q_value[selected] <- pmax(rows$p_value[selected], adjusted[good])
    }
    list(rows = rows, quantities = bulk_model_quantities(nrow(counts), which(eligible), quantities),
         method = c(bulk_count_details(normal, filter_mode, eligible), list(
        engine = "edgeR", version = as.character(version), test = test, numerator_df = df_test,
        legacy = if (test == "qlf") FALSE else NULL, robust = TRUE,
        abundance_trend = if (test == "qlf") abundance_trend else NULL, adjustment = "BH",
        failure_policy = "pad_failed_eligible_internally_public_null",
        omnibus_effect = if (df_test > 1L) "unavailable" else "directed_log2_rate"
    )))
}
