# Fixed-effect limma-voom for one frozen contrast.

fit_voom_directed <- function(dataset, design, contrast, quality_weights = FALSE) {
    frozen <- matrix(contrast, ncol = 1L,
                     dimnames = list(colnames(design), "frozen"))
    target <- limma::contrastAsCoef(design, frozen)
    if (length(target$coef) != 1L || ncol(target$design) != ncol(design) ||
        qr(cbind(design, target$design))$rank != ncol(design)) {
        stop("limma-voom reparameterization changed the frozen design span")
    }
    mapped <- drop(crossprod(contrast, qr.solve(design, target$design)))
    expected <- seq_len(ncol(design)) == target$coef
    if (max(abs(mapped - expected)) > 1e-10) {
        stop("limma-voom reparameterization changed the frozen contrast")
    }
    weigh <- if (quality_weights) limma::voomWithQualityWeights else limma::voom
    weighted <- weigh(dataset, design = design, normalize.method = "none", plot = FALSE)
    if (any(!is.finite(weighted$weights)) || any(weighted$weights <= 0)) {
        stop("limma-voom returned invalid observation weights")
    }
    fit <- limma::lmFit(weighted, target$design)
    list(model = limma::eBayes(fit, robust = TRUE, trend = FALSE),
         frozen_coef = target$coef,
         sample_weights = if (quality_weights) unname(weighted$targets$sample.weights) else NULL)
}

bulk_voom_sensitivity <- function(counts, design, units, groups, factors,
                                  contrast, paired, normalization = NULL, filter_mode = NULL,
                                  quality_weights = FALSE) {
    if (!identical(quality_weights, TRUE) && !identical(quality_weights, FALSE)) stop("Invalid quality-weight policy")
    if (!is.matrix(counts) || !is.numeric(counts) ||
        any(!is.finite(counts) | counts < 0 | counts > 2^53 - 1) ||
        is.null(rownames(counts)) || anyDuplicated(rownames(counts)) ||
        is.null(colnames(counts)) || anyDuplicated(colnames(counts))) {
        stop("limma-voom requires a named original count family")
    }
    design <- bulk_edger_design(design, colnames(counts), "full")
    directed <- bulk_edger_hypothesis(design, contrast, reduced = NULL)
    bulk_edger_replication(design, units, groups, paired)
    normal <- bulk_edger_normalization(counts, "raw_counts", factors, NULL, normalization)
    eligible <- bulk_count_filter(normal, design, filter_mode)
    quantities <- list(t = NULL, df.prior = NULL, df.total = NULL, s2.post = NULL)
    sample_weights <- NULL
    rows <- data.frame(
        gene_id = rownames(counts),
        estimate_status = ifelse(eligible, "failed", "filtered"),
        missing_reason = ifelse(eligible, "insufficient_voom_feature_support",
                                "independent_low_count_filter"),
        effect = NA_real_, p_value = NA_real_, q_value = NA_real_,
        stringsAsFactors = FALSE
    )
    if (sum(eligible) >= 2L) {
        fitted <- fit_voom_directed(normal$dge[eligible, , keep.lib.sizes = TRUE],
                                    design, directed, quality_weights)
        result <- fitted$model
        sample_weights <- fitted$sample_weights
        index <- fitted$frozen_coef
        if (!identical(rownames(result$p.value), rows$gene_id[eligible])) {
            stop("limma-voom changed the fitted gene family")
        }
        probabilities <- result$p.value[, index]
        effects <- result$coefficients[, index]
        quantities <- list(t = result$t[, index], df.prior = result$df.prior,
                           df.total = result$df.total, s2.post = result$s2.post)
        good <- is.finite(effects) & is.finite(probabilities) &
            probabilities >= 0 & probabilities <= 1
        selected <- which(eligible)[good]
        rows$missing_reason[eligible] <- "invalid_or_failed_voom_test"
        rows$estimate_status[selected] <- "estimated"
        rows$missing_reason[selected] <- NA_character_
        rows$effect[selected] <- effects[good]
        rows$p_value[selected] <- probabilities[good]
        family <- rows$p_value[eligible]
        family[is.na(family)] <- 1
        rows$q_value[selected] <- pmax(probabilities[good], p.adjust(family, method = "BH")[good])
    }
    fitted_genes <- if (sum(eligible) >= 2L) which(eligible) else integer()
    list(rows = rows, quantities = bulk_model_quantities(nrow(counts), fitted_genes, quantities),
         method = c(bulk_count_details(normal, filter_mode, eligible), list(
        engine = "limma", version = as.character(utils::packageVersion("limma")),
        weights = if (quality_weights) "voomWithQualityWeights" else "voom",
        sample_weights = sample_weights,
        robust = TRUE, adjustment = "BH",
        variance_model = if (quality_weights) "voom_quality_weights_robust_empirical_bayes" else
            "voom_observation_weights_robust_empirical_bayes"
    )))
}
