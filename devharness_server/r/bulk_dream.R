# Repeated-donor bulk contrast. The caller freezes this solver and its scope
# before observing results. This source file does not grant execution authority.

.dream_labels <- function(values) {
    is.character(values) && length(values) > 0L && !anyNA(values) &&
        all(nzchar(values) & trimws(values) == values)
}

.dream_inputs <- function(expression, metadata, condition_col, donor_col, numerator,
                          denominator, technical_factors, measurement, size_factors) {
    if (!is.matrix(expression) || !is.numeric(expression) || !all(is.finite(expression)) ||
        !.dream_labels(rownames(expression)) || anyDuplicated(rownames(expression)) ||
        !.dream_labels(colnames(expression)) || anyDuplicated(colnames(expression))) {
        stop("dream needs a finite numeric matrix with exact unique gene and sample names")
    }
    if (length(measurement) != 1L || !measurement %in% c("raw_counts", "continuous")) {
        stop("dream needs a declared raw_counts or continuous measurement")
    }
    if (identical(measurement, "raw_counts") &&
        (any(expression < 0) || any(expression != floor(expression)) ||
         any(expression > 2^53 - 1))) {
        stop("dream raw counts must be nonnegative exact integers")
    }
    if (!is.null(size_factors) && (!identical(measurement, "raw_counts") ||
        !is.numeric(size_factors) || !is.null(dim(size_factors)) ||
        length(size_factors) != ncol(expression) || any(!is.finite(size_factors) | size_factors <= 0) ||
        (!is.null(names(size_factors)) && !identical(names(size_factors), colnames(expression))))) {
        stop("dream supplied size factors require a finite positive exact raw-count sample axis")
    }
    fields <- c(condition_col, donor_col, technical_factors)
    if (!is.data.frame(metadata) || !.dream_labels(rownames(metadata)) ||
        !setequal(rownames(metadata), colnames(expression)) || anyDuplicated(names(metadata)) ||
        !.dream_labels(fields) || anyDuplicated(fields) || !all(fields %in% names(metadata)) ||
        length(condition_col) != 1L || length(donor_col) != 1L ||
        length(numerator) != 1L || length(denominator) != 1L ||
        !.dream_labels(c(numerator, denominator)) || identical(numerator, denominator)) {
        stop("dream requires aligned sample metadata and distinct frozen contrast roles and levels")
    }
    metadata[colnames(expression), fields, drop=FALSE]
}

.dream_design <- function(metadata, condition_col, donor_col, numerator, denominator, technical_factors) {
    condition <- as.character(metadata[[condition_col]])
    donor <- as.character(metadata[[donor_col]])
    if (!.dream_labels(condition) || !setequal(condition, c(numerator, denominator)) ||
        !.dream_labels(donor)) {
        stop("dream needs complete donor labels and exactly the two declared condition levels")
    }
    treated <- condition == numerator
    repeats <- table(donor)
    donor_counts <- c(length(unique(donor[!treated])), length(unique(donor[treated])))
    if (sum(repeats > 1) < 2L || min(donor_counts) < 3L) {
        stop("dream needs at least two repeated donors and three distinct donors in each condition")
    }
    data <- data.frame(condition_target=as.numeric(treated), donor_group=factor(donor),
                       row.names=rownames(metadata))
    factors <- character()
    for (column in technical_factors) {
        values <- metadata[[column]]
        if (is.numeric(values)) {
            if (any(!is.finite(values)) || length(unique(values)) < 2L) {
                stop("dream numeric covariates need finite variation")
            }
            values <- as.numeric(scale(values))
        } else {
            values <- as.character(values)
            if (!.dream_labels(values) || length(unique(values)) < 2L) {
                stop("dream categorical covariates need complete labels with at least two levels")
            }
            values <- factor(values, levels=sort(unique(values), method="radix"))
        }
        name <- paste0("nuisance_", length(factors) + 1L)
        data[[name]] <- values
        factors <- c(factors, name)
    }
    # Generate the formula solely from fixed internal names. Metadata names and
    # factor labels are never interpreted as model formula text.
    terms <- c("condition_target", factors)
    fixed <- stats::model.matrix(stats::reformulate(terms), data=data)
    rank <- qr(fixed)$rank
    residual <- nrow(fixed) - rank
    conditioning <- kappa(fixed, exact=TRUE)
    if (rank != ncol(fixed) || residual <= 1L || !is.finite(conditioning) || conditioning > 1000) {
        stop("dream fixed design fails the rank, conditioning, or residual-information check")
    }
    list(data=data, treated=treated, formula=stats::reformulate(c(terms, "(1 | donor_group)")),
         diagnostics=list(donor_count=length(repeats), repeated_donor_count=sum(repeats > 1),
                          biological_units_by_condition=setNames(as.list(donor_counts), c(denominator, numerator)),
                          balanced_donor_profile_counts=length(unique(repeats)) == 1L,
                          fixed_rank=rank, fixed_columns=ncol(fixed), residual_information=residual,
                          condition_number=conditioning))
}

.dream_library_sizes <- function(expression, size_factors) {
    libraries <- colSums(expression)
    if (any(libraries <= 0 | libraries > 2^53 - 1 | !is.finite(libraries))) {
        stop("dream needs positive raw library sizes within exact count bounds")
    }
    if (is.null(size_factors)) {
        libraries <- libraries * edgeR::calcNormFactors(expression, method="TMM")
        method <- "TMM_effective_library_sizes"
    } else {
        center <- mean(log(libraries))
        libraries <- exp(log(size_factors) + center - mean(log(size_factors)))
        method <- "supplied_RLE_effective_library_sizes"
    }
    if (any(!is.finite(libraries) | libraries <= 0)) {
        stop("dream effective library sizes must remain finite and positive")
    }
    list(sizes=libraries, method=method)
}

.dream_eligible <- function(expression, treated, raw_counts) {
    if (raw_counts) {
        numerator <- rowSums(expression[, treated, drop=FALSE]) > 0
        denominator <- rowSums(expression[, !treated, drop=FALSE]) > 0
        return(ifelse(numerator & denominator, NA_character_,
                      ifelse(numerator | denominator, "zero_marginal_condition_count", "all_zero_gene")))
    }
    constant <- rowSums(expression == expression[, 1L]) == ncol(expression)
    ifelse(constant, "constant_continuous_gene", NA_character_)
}

bulk_dream <- function(expression, metadata, condition_col, donor_col, numerator,
                       denominator, technical_factors=character(), measurement="raw_counts",
                       size_factors=NULL) {
    metadata <- .dream_inputs(expression, metadata, condition_col, donor_col, numerator,
                              denominator, technical_factors, measurement, size_factors)
    design <- .dream_design(metadata, condition_col, donor_col, numerator, denominator, technical_factors)
    raw_counts <- identical(measurement, "raw_counts")
    libraries <- if (raw_counts) .dream_library_sizes(expression, size_factors) else NULL
    reasons <- .dream_eligible(expression, design$treated, raw_counts)
    eligible <- which(is.na(reasons))
    rows <- data.frame(gene_id=rownames(expression), effect=NA_real_, p_value=NA_real_,
                       q_value=NA_real_, estimate_status="not_estimable", missing_reason=reasons)
    rows$estimate_status[eligible] <- "failed"
    rows$missing_reason[eligible] <- "insufficient_genes_for_moderation"
    diagnostics <- c(design$diagnostics, list(
        solver="variancePartition::dream", package_version=as.character(utils::packageVersion("variancePartition")),
        formula_scope="fixed_condition_and_declared_covariates_plus_donor_random_intercept",
        contrast=list(numerator=numerator, denominator=denominator), voom_weights=raw_counts,
        normalization=if (raw_counts) libraries$method else "none_already_continuous",
        measurement=measurement, effect_scale=if (raw_counts) "log2_CPM" else "input_units",
        denominator_df="Satterthwaite", moderated=TRUE,
        multiplicity="BH_full_family_unestimated_as_one_missing_in_output"
    ))
    if (length(eligible) >= 2L) {
        counts <- expression[eligible, , drop=FALSE]
        workers <- BiocParallel::SerialParam()
        values <- if (raw_counts) {
            variancePartition::voomWithDreamWeights(
                counts, design$formula, design$data, lib.size=libraries$sizes,
                normalize.method="none", save.plot=FALSE, BPPARAM=workers
            )
        } else counts
        fit <- variancePartition::dream(values, design$formula, design$data,
                                       ddf="Satterthwaite", BPPARAM=workers, hideErrorsInBackend=TRUE)
        fit <- variancePartition::eBayes(fit)
        coefficient <- "condition_target"
        fit_genes <- rownames(fit$coefficients)
        if (!.dream_labels(fit_genes) || anyDuplicated(fit_genes) ||
            !all(fit_genes %in% rows$gene_id[eligible]) || !coefficient %in% colnames(fit$coefficients) ||
            !identical(fit_genes, rownames(fit$p.value))) {
            stop("dream returned an unexpected coefficient or fitted gene axis")
        }
        effects <- fit$coefficients[, coefficient]
        probabilities <- fit$p.value[, coefficient]
        valid <- is.finite(effects) & is.finite(probabilities) & probabilities >= 0 & probabilities <= 1
        rows$missing_reason[eligible] <- "dream_estimate_unavailable"
        fitted <- match(fit_genes[valid], rows$gene_id)
        rows$effect[fitted] <- effects[valid]
        rows$p_value[fitted] <- probabilities[valid]
        rows$estimate_status[fitted] <- "estimated"
        rows$missing_reason[fitted] <- NA_character_
        adjusted <- stats::p.adjust(ifelse(is.na(rows$p_value), 1, rows$p_value), method="BH")
        rows$q_value[fitted] <- pmax(rows$p_value[fitted], adjusted[fitted])
    }
    list(rows=rows, diagnostics=diagnostics)
}
