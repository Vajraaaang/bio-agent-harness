# ADR-15 development experiment. One independent 10+10 design for all arms.
# Global-null validation uses different generators/streams and the mounted solver.
args <- commandArgs(trailingOnly = TRUE)
out <- "/outputs"
workers <- as.integer(args[[1L]])
stopifnot(workers >= 1L, workers <= 12L)
packages <- c("edgeR", "limma", "DESeq2", "variancePartition", "statmod", "BiocParallel")
for (pkg in packages) invisible(loadNamespace(pkg))
versions <- lapply(setNames(packages, packages), function(p) as.character(packageVersion(p)))
versions$R <- as.character(getRversion())
versions$dreamlet <- if (requireNamespace("dreamlet", quietly = TRUE)) {
    stop("Diagnostic comparator must be specified for this different image")
} else "absent"
jsonlite::write_json(versions, file.path(out, "versions.json"), auto_unbox = TRUE)

meta <- data.frame(condition = rep(0:1, each = 10),
                   batch = c(0:9 %% 2L, (1:10) %% 2L), row.names = paste0("s", 1:20))
formula <- ~ condition + batch
design <- model.matrix(formula, meta)
arms <- expand.grid(robust = c(TRUE, FALSE), trend = c(TRUE, FALSE),
                    norm = c("TMM", "RLE"), filter = c("filterByExpr", "normalized_mean_ge_1"),
                    stringsAsFactors = FALSE)
arms$id <- with(arms, paste("edgeR", robust, trend, norm, filter, sep = "/"))
others <- c("voom", "voomWithQualityWeights", "DESeq2", "variancePartition::dream")
arm_ids <- c(arms$id, others)

simulate <- function(n, compounded, planted, index) {
    # Cross all parameters on the same data within a cohort; pair the exposure
    # and donor draws across both DGP cells. No historical seeds are reused.
    set.seed(41070000L + (n == 10000L) * 10000L + planted * 1000L + index)
    baseline <- rnorm(n, log(45), 0.55)
    donor <- matrix(rnorm(n * 20L, 0, 0.30), n)
    library <- rnorm(20, 0, 0.20)
    technical <- rnorm(n, 0, 0.20)
    signs <- numeric(n)
    if (planted) signs[seq_len(n %/% 8L)] <- rep_len(c(-1,1), n %/% 8L)
    mu <- exp(baseline + outer(rep(1, n), library) + compounded * donor +
              outer(technical, meta$batch) + outer(signs * log(3), meta$condition))
    phi <- runif(n, 0.04, 0.12)
    matrix(rnbinom(length(mu), mu = mu, size = 1 / phi), n,
           dimnames = list(paste0("g", seq_len(n)), rownames(meta)))
}

fit_cohort <- function(n, compounded, planted, index) {
    prefix <- sprintf("%s-%d-%s-%03d", if (compounded) "compounded" else "pure",
                      n, if (planted) "planted" else "null", index)
    path <- file.path(out, prefix)
    counts <- simulate(n, compounded, planted, index)
    lib <- colSums(counts)
    size <- DESeq2::estimateSizeFactorsForMatrix(counts)
    size <- size / exp(mean(log(size)))
    datasets <- list(TMM = edgeR::normLibSizes(edgeR::DGEList(counts), method = "TMM"),
                     RLE = edgeR::DGEList(counts, lib.size = size * exp(mean(log(lib)))))
    prepare <- function(norm, filter) {
        y <- datasets[[norm]]
        libs <- with(y$samples, lib.size * norm.factors)
        keep <- if (filter == "filterByExpr") edgeR::filterByExpr(y, design = design) else {
            rowMeans(sweep(counts, 2L, libs / exp(mean(log(libs))), "/")) >= 1
        }
        list(y = y[keep, , keep.lib.sizes = TRUE], keep = keep)
    }
    common <- prepare("TMM", "filterByExpr")
    fit <- function(i) {
        if (i <= nrow(arms)) {
            arm <- arms[i, ]
            data <- prepare(arm$norm, arm$filter)
            model <- edgeR::glmQLFit(data$y, design, robust = arm$robust, legacy = FALSE,
                                     abundance.trend = arm$trend, dispersion = NULL)
            test <- edgeR::glmQLFTest(model, coef = 2L)$table
            stopifnot(!any(model$failed %in% TRUE))
            return(list(keep = data$keep, effect = test$logFC, p = test$PValue))
        }
        id <- arm_ids[[i]]
        y <- common$y
        if (id == "DESeq2") {
            # Explicit Wald contrast and one pre-fit expression filter. Use
            # DESeq2's median ratio sizes, no post-fit independent filtering.
            dds <- suppressMessages(DESeq2::DESeqDataSetFromMatrix(y$counts, meta, formula))
            DESeq2::sizeFactors(dds) <- size
            dds <- DESeq2::DESeq(dds, quiet = TRUE, minReplicatesForReplace = Inf)
            test <- DESeq2::results(dds, name = "condition", independentFiltering = FALSE,
                                    cooksCutoff = FALSE)
            return(list(keep = common$keep, effect = test$log2FoldChange, p = test$pvalue))
        }
        if (id == "variancePartition::dream") {
            # All 20 donors are distinct: dream's documented fixed-effects path.
            bp <- BiocParallel::SerialParam()
            w <- variancePartition::voomWithDreamWeights(y, formula, meta, save.plot = FALSE,
                                                         BPPARAM = bp)
            model <- variancePartition::dream(w, formula, meta, BPPARAM = bp)
            model <- variancePartition::eBayes(model)
        } else {
            w <- if (id == "voom") limma::voom(y, design, plot = FALSE) else {
                limma::voomWithQualityWeights(y, design, plot = FALSE)
            }
            model <- limma::eBayes(limma::lmFit(w, design), robust = TRUE)
        }
        list(keep = common$keep, effect = model$coefficients[, "condition"],
             p = model$p.value[, "condition"])
    }
    stream <- file(paste0(path, ".bin"), "wb")
    on.exit(close(stream), add = TRUE)
    records <- lapply(seq_along(arm_ids), function(i) {
        warnings <- character()
        tryCatch({
            f <- withCallingHandlers(fit(i), warning = function(w) {
                warnings <<- unique(c(warnings, conditionMessage(w)))
                invokeRestart("muffleWarning")
            })
            stopifnot(length(f$p) > 0, length(f$p) == sum(f$keep),
                      length(f$effect) == sum(f$keep), all(is.finite(f$effect)),
                      all(is.finite(f$p)), all(f$p >= 0 & f$p <= 1))
            q <- pmax(f$p, p.adjust(f$p, "BH"))
            # Gene indexes preserve filtered positions. Independent Python scoring
            # consumes every fitted p/q pair and checks BH before aggregating.
            writeBin(as.double(c(which(f$keep), f$effect, f$p, q)), stream, endian = "little")
            list(arm = arm_ids[[i]], tested = length(q), warnings = as.list(warnings))
        }, error = function(e) list(arm = arm_ids[[i]], tested = 0L, error = conditionMessage(e),
                                    warnings = as.list(warnings)))
    })
    jsonlite::write_json(records, paste0(path, ".json"), auto_unbox = TRUE)
    prefix
}

cases <- expand.grid(compounded = c(FALSE, TRUE), genes = c(192L, 10000L),
                     replicate = seq_len(120L))
run_case <- function(i) {
    item <- cases[i, ]
    planted <- item$replicate > 100L
    fit_cohort(item$genes, item$compounded, planted,
               if (planted) item$replicate - 100L else item$replicate)
}
results <- parallel::mclapply(seq_len(nrow(cases)), run_case, mc.cores = workers,
                              mc.preschedule = FALSE, mc.set.seed = FALSE)
stopifnot(all(vapply(results, function(x) is.character(x) && length(x) == 1L &&
                     !inherits(x, "try-error"), logical(1))))
