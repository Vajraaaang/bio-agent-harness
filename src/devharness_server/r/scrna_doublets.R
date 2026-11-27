args <- commandArgs(trailingOnly=TRUE)
if (!requireNamespace("scDblFinder", quietly=TRUE)) quit(status=42L)
read_part <- function(name) rhdf5::h5read("counts.h5", paste0("counts/", name))
m <- methods::new("dgCMatrix", i=as.integer(read_part("i")), p=as.integer(read_part("p")),
                  x=as.numeric(read_part("x")), Dim=as.integer(read_part("dim")))
colnames(m) <- paste0("cell", seq_len(ncol(m)))
rownames(m) <- paste0("gene", seq_len(nrow(m)))
seed <- as.integer(args[[1L]])
set.seed(seed)
params <- list(sce=SingleCellExperiment::SingleCellExperiment(assays=list(counts=m)),
               BPPARAM=BiocParallel::SerialParam(RNGseed=seed))
if (args[[2L]] != "auto") params$dbr <- as.numeric(args[[2L]])
sce <- do.call(scDblFinder::scDblFinder, params)
stopifnot(identical(colnames(sce), colnames(m)))
write.csv(data.frame(position=seq_len(ncol(m)), score=sce$scDblFinder.score,
                     class=as.character(sce$scDblFinder.class)), "predictions.csv", row.names=FALSE)
