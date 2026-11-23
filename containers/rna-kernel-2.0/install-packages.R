# Resolve nothing during the build. Fetch the frozen public archives, then
# install and check their exact source bytes in a network-disabled build step.
mode <- commandArgs(trailingOnly = TRUE)
if (length(mode) != 1L || !mode %in% c("fetch", "install")) stop("Specify fetch or install")
lock <- jsonlite::read_json("/tmp/r-sources.lock.json")
if (lock$format != "devbench.rna-kernel-r-sources/1" ||
    lock$R != as.character(getRversion()) ||
    lock$BiocVersion != as.character(packageVersion("BiocVersion"))) {
  stop("Bioconductor runtime does not match the package lock")
}
source_dir <- "/tmp/rna-sources"
root <- "/opt/rna/R-library"
records <- setNames(lock$packages, vapply(lock$packages, function(p) p$package, character(1)))
if (length(records) > 256L || anyDuplicated(names(records)) ||
    !all(unlist(lock$targets) %in% names(records))) stop("Invalid locked package inventory")

verify <- function(path, record) {
  actual <- system2("sha256sum", shQuote(path), stdout = TRUE)
  digest <- strsplit(actual, "[[:space:]]+")[[1]][1]
  if (!identical(record$sha256, digest) || !isTRUE(file.size(path) == record$size)) {
    stop("Source identity mismatch: ", record$package)
  }
}

dir.create(source_dir, showWarnings = FALSE)
options(timeout = 240)
for (record in records) {
  if (!grepl("^[A-Za-z][A-Za-z0-9.]*$", record$package) ||
      !grepl("^[0-9][A-Za-z0-9._+-]*$", record$version) ||
      !grepl("^[a-f0-9]{64}$", record$sha256) ||
      !all(unlist(record$dependencies) %in% names(records))) stop("Invalid source lock record")
  filename <- paste0(record$package, "_", record$version, ".tar.gz")
  path <- file.path(source_dir, filename)
  if (mode == "fetch") {
    url <- paste0(record$repository, "/", filename)
    urls <- url
    if (record$repository == "https://cloud.r-project.org/src/contrib") {
      urls <- c(url, paste0(record$repository, "/Archive/", record$package, "/", filename))
    }
    downloaded <- FALSE
    for (candidate in urls) {
      downloaded <- tryCatch(
        download.file(candidate, path, mode = "wb", method = "libcurl", quiet = TRUE) == 0L,
        error = function(error) FALSE
      )
      if (downloaded) break
    }
    if (!downloaded) stop("Locked archive unavailable: ", record$package)
  }
  verify(path, record)
}
if (mode == "fetch") quit(save = "no", status = 0)

dir.create(root, recursive = TRUE)
.libPaths(c(root, .libPaths()))
pending <- names(records)
done <- character()
while (length(pending)) {
  ready <- pending[vapply(pending, function(name) {
    all(unlist(records[[name]]$dependencies) %in% done)
  }, logical(1))]
  if (!length(ready)) stop("Unresolved dependency in the source lock")
  for (name in ready) {
    record <- records[[name]]
    path <- file.path(source_dir, paste0(name, "_", record$version, ".tar.gz"))
    status <- system2(file.path(R.home("bin"), "R"), c("CMD", "INSTALL", "--no-multiarch",
      paste0("--library=", shQuote(root)), shQuote(path)))
    if (status != 0L ||
        packageDescription(name, lib.loc = root, fields = "Version") != record$version) {
      stop("Failed to install the locked version of ", name)
    }
  }
  done <- c(done, ready)
  pending <- setdiff(pending, ready)
}
for (name in unlist(lock$targets)) {
  if (!requireNamespace(name, quietly = TRUE)) stop("Locked package failed to load: ", name)
}
