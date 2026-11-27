# The image build and the daemon-side acceptance run execute this without network.
lock <- jsonlite::fromJSON("/opt/exploratory/r-package-lock.json", simplifyVector = FALSE)
packages <- names(lock$required_packages)
stopifnot(length(packages) == 15L)
for (p in packages) {
  message("Loading ", p)
  loadNamespace(p)
}
versions <- setNames(lapply(packages, function(p) as.character(packageVersion(p))), packages)
stopifnot(identical(versions, lock$required_packages))

# Exercise the loaded Arrow C++ library and check a model fit through edgeR.
path <- tempfile(fileext = ".parquet")
observed <- data.frame(gene = c("synthetic_1", "synthetic_2"), count = c(3L, 7L))
arrow::write_parquet(observed, path)
stopifnot(identical(as.data.frame(arrow::read_parquet(path)), observed))
unlink(path)
counts <- matrix(c(15L, 9L, 4L, 18L, 10L, 8L, 29L, 9L, 7L, 32L, 7L, 13L),
                 nrow = 3L, dimnames = list(paste0("gene", 1:3), paste0("sample", 1:4)))
group <- factor(c("A", "A", "B", "B"))
y <- edgeR::DGEList(counts = counts, group = group)
y <- edgeR::calcNormFactors(y)
fit <- edgeR::glmFit(y, stats::model.matrix(~ group), dispersion = 0.1)
test <- edgeR::glmLRT(fit, coef = 2L)$table
stopifnot(nrow(test) == nrow(counts), all(is.finite(test$logFC)),
          all(test$PValue >= 0 & test$PValue <= 1))
cat(jsonlite::toJSON(list(r = as.character(getRversion()), arch = R.version$arch,
                          packages = versions, arrow_parquet = TRUE, edger_smoke = TRUE),
                     auto_unbox = TRUE), "\n")
