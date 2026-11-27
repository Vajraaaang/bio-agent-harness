# A precomputed fraction may come from keyed BAM summaries or splicing layers.
if (!requireNamespace("DropletQC", quietly=TRUE)) quit(status=42L)
x <- read.csv("nuclear.csv", stringsAsFactors=FALSE)
set.seed(0)
# Unresolved mixture/density warnings cannot substantiate a droplet class.
options(warn=2)
classified <- DropletQC::identify_empty_drops(x[c("nuclear_fraction", "total_counts")])
classified$cell_type <- x$cluster
fit <- DropletQC::identify_damaged_cells(classified, verbose=FALSE)$df
stopifnot(identical(rownames(fit), rownames(x)))
write.csv(data.frame(position=seq_len(nrow(x)), class=fit$cell_status),
          "classes.csv", row.names=FALSE)
