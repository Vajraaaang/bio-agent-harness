# The coordinator supplies closed method data and fixed numeric array files.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1L) stop("Expected one staged pathway job directory")
root <- args[[1L]]
request <- jsonlite::read_json(file.path(root, "solver.json"))
packages <- c(camera = "limma", fgsea = "fgsea", decoupleR = "decoupleR")
versions <- c(camera = "3.68.5", fgsea = "1.38.0", decoupleR = "2.17.0")
method <- request$method
if (!method %in% names(packages)) stop("Unknown registered pathway method")
package <- packages[[method]]
required_version <- versions[[method]]

finish <- function(status, rows = list()) {
    body <- list(status = status, version = required_version, rows = rows)
    jsonlite::write_json(body, file.path(root, "result.json"),
        auto_unbox = TRUE, null = "null", na = "null", digits = NA)
}
if (as.character(getRversion()) != "4.6.1" ||
    !requireNamespace(package, quietly = TRUE) ||
    packageDescription(package, fields = "Version") != required_version) {
    finish("unavailable")
    quit(save = "no", status = 0L)
}

genes <- unlist(request$genes, use.names = FALSE)
sets <- lapply(request$sets, function(item) unlist(item$members, use.names = FALSE))
names(sets) <- vapply(request$sets, function(item) item$set_id, character(1L))
read_matrix <- function(name, rows, columns) {
    path <- file.path(root, name)
    if (file.info(path)$size != rows * columns * 8) stop("Invalid pathway matrix")
    values <- readBin(path, "double", n = rows * columns, endian = "little")
    if (length(values) != rows * columns || any(!is.finite(values))) {
        stop("Invalid pathway numeric values")
    }
    matrix(values, nrow = rows, byrow = TRUE)
}

run_camera <- function() {
    contrast <- unlist(request$contrast, use.names = FALSE)
    expression <- read_matrix("expression.bin", length(genes), request$samples)
    design <- read_matrix("design.bin", request$samples, length(contrast))
    if (qr(design)$rank != ncol(design) || nrow(design) - ncol(design) < 2L) {
        stop("CAMERA requires a full-rank design and residual degrees of freedom")
    }
    rownames(expression) <- genes
    index <- lapply(sets, match, table = genes)
    fit <- limma::camera(expression, index, design, contrast = contrast,
        inter.gene.cor = NA_real_, allow.neg.cor = FALSE, sort = FALSE)
    lapply(seq_len(nrow(fit)), function(i) {
        row <- fit[i, ]
        list(
            set_id = rownames(fit)[[i]], tested_members = row$NGenes,
            p_value = row$PValue, direction = tolower(row$Direction),
            correlation = row$Correlation,
            vif = max(1, 1 + (row$NGenes - 1) * row$Correlation)
        )
    })
}

run_fgsea <- function() {
    scores <- setNames(unlist(request$scores, use.names = FALSE), genes)
    scores <- scores[order(-scores, names(scores), method = "radix")]
    set.seed(request$seed)
    fit <- fgsea::fgseaMultilevel(pathways = sets, stats = scores,
        minSize = request$min_set_size, maxSize = request$max_set_size,
        eps = 1e-10, BPPARAM = BiocParallel::SerialParam())
    lapply(seq_len(nrow(fit)), function(i) {
        list(set_id = fit$pathway[[i]], tested_members = fit$size[[i]],
             p_value = fit$pval[[i]], score = fit$NES[[i]])
    })
}

run_decoupler <- function() {
    scores <- matrix(unlist(request$scores, use.names = FALSE), ncol = 1L,
        dimnames = list(genes, "frozen_contrast"))
    network <- do.call(rbind, lapply(request$sets, function(item) {
        data.frame(source = item$set_id, target = unlist(item$members),
                   mor = unlist(item$weights))
    }))
    fit <- decoupleR::run_ulm(scores, network, .source = "source",
        .target = "target", .mor = "mor", minsize = request$min_set_size)
    lapply(seq_len(nrow(fit)), function(i) {
        list(set_id = fit$source[[i]], tested_members = length(sets[[fit$source[[i]]]]),
             score = fit$score[[i]])
    })
}

rows <- switch(method, camera = run_camera(), fgsea = run_fgsea(),
               decoupleR = run_decoupler())
finish("completed", rows)
