# Per-capture sparse matrix fitting. Cell ordinals are shared between R0 and R1.
args <- commandArgs(trailingOnly=TRUE)
method <- args[[1L]]
pkg <- if (method=="soupx") "SoupX" else "decontX"
if (!requireNamespace(pkg, quietly=TRUE)) quit(status=42L)
read_matrix <- function(group) {
  elem <- function(key) rhdf5::h5read("counts.h5", paste(group, key, sep="/"))
  m <- methods::new("dgCMatrix", x=as.numeric(elem("x")), i=as.integer(elem("i")),
                    p=as.integer(elem("p")), Dim=as.integer(elem("dim")))
  colnames(m) <- paste0("d",as.integer(elem("barcodes")))
  rownames(m) <- paste0("g",seq_len(nrow(m)))
  m
}
set.seed(as.integer(args[[2L]]))
filtered <- read_matrix("filtered")
has_raw <- args[[3L]]=="R0"
if (has_raw) raw <- read_matrix("raw")
clusters <- as.integer(rhdf5::h5read("counts.h5","clusters"))
if (method=="soupx") {
  stopifnot(has_raw)
  channel <- SoupX::SoupChannel(raw, filtered)
  channel <- SoupX::setClusters(channel,setNames(clusters,colnames(filtered)))
  channel <- tryCatch(withCallingHandlers(SoupX::autoEstCont(channel, doPlot=FALSE),
                            warning=function(w) stop(conditionMessage(w))),
                      error=function(e) quit(status=43L))
  adjusted <- SoupX::adjustCounts(channel,roundToInt=FALSE)
} else {
  stopifnot(method=="decontx")
  params <- list(x=filtered, seed=as.integer(args[[2L]]))
  if (length(unique(clusters))>=2L) params$z <- clusters
  if (has_raw) params$background <- raw
  adjusted <- do.call(decontX::decontX,params)$decontXcounts
}
stopifnot(identical(colnames(adjusted),colnames(filtered)),
          identical(rownames(adjusted),rownames(filtered)))
m <- as(adjusted,"dgCMatrix")
rhdf5::h5createFile("adjusted.h5")
for (s in c("x","i","p","Dim")) rhdf5::h5write(methods::slot(m,s),"adjusted.h5",s)
