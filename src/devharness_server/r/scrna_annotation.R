# Fixed offline reference-transfer entry point. Only the named staging directory
# and its two counts matrices, reference labels, and fixed Azimuth files are read.
args <- commandArgs(trailingOnly = TRUE)
method <- args[[1]]
folder <- args[[2]]
label <- args[[3]]
read_axis <- function(name) {
    utils::read.csv(file.path(folder, name), colClasses = "character",
                    check.names = FALSE)[[1]]
}
read_counts <- function(stem) {
    x <- methods::as(Matrix::readMM(file.path(folder, paste0(stem, ".mtx"))), "CsparseMatrix")
    dimnames(x) <- list(read_axis(paste0(stem, "_genes.csv")), read_axis(paste0(stem, "_cells.csv")))
    x
}
query <- read_counts("query")
classes <- read_axis("labels.csv")
if (method == "singler") {
    ref <- read_counts("reference")
    train <- SingleCellExperiment::SingleCellExperiment(assays = list(counts = ref))
    train <- scuttle::logNormCounts(train)
    # SingleR compares expression ranks per query cell. Reference markers are
    # learned on the normalized log-expression scale.
    pred <- SingleR::SingleR(test = query, ref = train, labels = classes,
                             assay.type.ref = "logcounts")
    stopifnot(identical(rownames(pred), colnames(query)))
    labels <- as.character(pred$pruned.labels)
    labels[is.na(labels)] <- "uncertain"
} else if (method == "azimuth") {
    # Use the Azimuth reference and its Seurat SCT transfer directly. The
    # convenience RunAzimuth wrapper can request a remote gene homology table.
    ref <- Azimuth::LoadReference(folder)$map
    ref_cells <- read_axis("reference_cells.csv")
    stopifnot(identical(colnames(ref), ref_cells),
              identical(as.character(ref[[label, drop = TRUE]]), classes))
    features <- rownames(SeuratObject::Loadings(ref[["refDR"]]))
    stopifnot(all(features %in% rownames(query)))
    q <- SeuratObject::CreateSeuratObject(query)
    dims <- seq_len(ncol(SeuratObject::Embeddings(ref[["refDR"]])))
    anchors <- Seurat::FindTransferAnchors(
        reference = ref, query = q, k.filter = NA,
        reference.neighbors = "refdr.annoy.neighbors",
        reference.assay = "refAssay", query.assay = "RNA",
        reference.reduction = "refDR", normalization.method = "SCT",
        features = features, dims = dims, verbose = FALSE
    )
    pred <- Seurat::TransferData(anchorset = anchors, refdata = ref[[label, drop = TRUE]],
                                 dims = dims, verbose = FALSE)
    stopifnot(identical(rownames(pred), colnames(query)))
    labels <- as.character(pred$predicted.id)
} else {
    stop("unknown reference transfer method")
}
utils::write.csv(data.frame(cell = colnames(query), label = labels),
                 file.path(folder, "predictions.csv"), row.names = FALSE)
