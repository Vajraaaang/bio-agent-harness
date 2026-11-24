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

bulk_edger_normalization <- function(counts, measurement, size_factors, tximport) {
    libraries <- colSums(counts)
    if (any(libraries <= 0)) stop("Empty or invalid count library")
    if (measurement == "tximport_estimated_counts") {
        if (!is.null(size_factors) || !is.list(tximport) || !"length" %in% names(tximport) ||
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
    if (measurement != "raw_counts" || !is.null(tximport) || any(counts != floor(counts)) ||
        !is.numeric(size_factors) || !identical(names(size_factors), colnames(counts)) ||
        any(!is.finite(size_factors) | size_factors <= 0)) {
        stop("Raw-count bulk requires integer counts and aligned positive full-family RLE factors")
    }
    factors <- exp(log(size_factors) - mean(log(size_factors)))
    list(dge = edgeR::DGEList(counts, lib.size = factors * exp(mean(log(libraries)))),
         abundance = drop(counts %*% (1 / factors)) / ncol(counts),
         method = "full_family_RLE")
}

bulk_edger_count <- function(counts, design, biological_units, replicate_groups,
                             measurement = "raw_counts", size_factors = NULL, tximport = NULL,
                             contrast = NULL, reduced = NULL, test = "qlf", paired = FALSE) {
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
    normalization <- bulk_edger_normalization(counts, measurement, size_factors, tximport)

    eligible <- normalization$abundance >= 1
    rows <- data.frame(gene_id = rownames(counts), estimate_status = ifelse(eligible, "failed", "filtered"),
        missing_reason = ifelse(eligible, "invalid_or_failed_edger_test", "independent_low_count_filter"),
        effect = NA_real_, p_value = NA_real_, q_value = NA_real_, stringsAsFactors = FALSE)
    df_test <- if (is.matrix(hypothesis)) ncol(hypothesis) else 1L
    if (any(eligible)) {
        dataset <- normalization$dge[eligible, , keep.lib.sizes = TRUE]
        if (test == "qlf") {
            fit <- edgeR::glmQLFit(dataset, design = design, legacy = FALSE, dispersion = NULL,
                                  robust = TRUE, abundance.trend = TRUE)
            observed <- edgeR::glmQLFTest(fit, contrast = hypothesis)$table
        } else {
            dataset <- edgeR::estimateDisp(dataset, design = design, robust = TRUE)
            fit <- edgeR::glmFit(dataset, design = design)
            observed <- edgeR::glmLRT(fit, contrast = hypothesis)$table
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
    list(rows = rows, method = list(
        engine = "edgeR", version = as.character(version), test = test, numerator_df = df_test,
        normalization = normalization$method, adjustment = "BH",
        failure_policy = "pad_failed_eligible_internally_public_null",
        filter = "label_blind_overall_normalized_mean_at_least_one",
        omnibus_effect = if (df_test > 1L) "unavailable" else "directed_log2_rate"
    ))
}
