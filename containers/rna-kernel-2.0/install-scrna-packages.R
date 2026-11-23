# Fetch the locked scRNA package archives before installing them offline into
# a separate library. The inherited bulk library provides shared dependencies.
mode <- commandArgs(trailingOnly = TRUE)
if (length(mode) != 1L || !mode %in% c("fetch", "install")) {
  stop("Specify fetch or install")
}

base_library <- "/opt/rna/R-library"
root <- "/opt/rna/scrna-R-library"
source_dir <- "/tmp/rna-scrna-sources"
if (!dir.exists(base_library)) stop("Missing locked bulk R library")
.libPaths(c(base_library, .Library.site, .Library))
lock <- jsonlite::read_json("/tmp/r-scrna-sources.lock.json")

sha256 <- function(path) {
  actual <- system2("sha256sum", c("--", shQuote(path)), stdout = TRUE)
  if (length(actual) != 1L || !is.null(attr(actual, "status"))) {
    stop("Cannot checksum ", path)
  }
  sub("[[:space:]].*$", "", actual)
}

base_lock <- "/tmp/r-sources.lock.json"
if (!file.exists(base_lock)) base_lock <- "/opt/rna/r-sources.lock.json"
if (lock$format != "devbench.rna-kernel-scrna-r-sources/1" ||
    lock$R != as.character(getRversion()) ||
    lock$BiocVersion != as.character(packageVersion("BiocVersion")) ||
    lock$base_lock_sha256 != sha256(base_lock)) {
  stop("Bulk R runtime does not match the scRNA package lock")
}

records <- setNames(lock$packages, vapply(lock$packages, `[[`, character(1), "package"))
base_packages <- unlist(lock$base_packages)
targets <- unlist(lock$targets)
package_pattern <- "^[A-Za-z][A-Za-z0-9.]*$"
if (!length(records) || !length(targets) || anyDuplicated(names(records)) ||
    anyDuplicated(names(base_packages)) ||
    length(intersect(names(records), names(base_packages))) ||
    !all(targets %in% names(records)) ||
    !all(grepl(package_pattern, c(names(records), names(base_packages))))) {
  stop("Invalid locked scRNA package inventory")
}
for (name in names(base_packages)) {
  if (!identical(packageDescription(name, fields = "Version"), base_packages[[name]])) {
    stop("Inherited dependency version does not match the scRNA lock: ", name)
  }
}

dependencies <- function(record) vapply(record$dependencies, identity, character(1))
used_base <- setdiff(unlist(lapply(records, dependencies)), names(records))
if (!setequal(used_base, names(base_packages))) stop("Invalid inherited dependency inventory")

cran <- "https://cloud.r-project.org/src/contrib"
bioc <- "https://bioconductor.org/packages/3.23/bioc/src/contrib"
archive_path <- function(record) {
  file.path(source_dir, paste0(record$package, "_", record$version, ".tar.gz"))
}

fetch <- function(path, record) {
  filename <- basename(path)
  url <- paste(record$repository, filename, sep = "/")
  if (record$repository == cran) {
    urls <- c(url, paste(cran, "Archive", record$package, filename, sep = "/"))
  } else {
    mirror <- "https://bioconductor.posit.co/packages/3.23/bioc/src/contrib"
    urls <- c(url, paste(mirror, filename, sep = "/"))
  }
  for (candidate in urls) {
    status <- tryCatch(
      suppressWarnings(download.file(candidate, path, mode = "wb", method = "libcurl", quiet = TRUE)),
      error = function(error) 1L
    )
    if (status == 0L) return(invisible(NULL))
  }
  stop("Locked scRNA source archive unavailable: ", record$package)
}

verify <- function(path, record) {
  if (!isTRUE(file.size(path) == record$size) || sha256(path) != record$sha256) {
    stop("scRNA source identity mismatch: ", record$package)
  }
  description_lines <- system2(
    "tar",
    c("-xOzf", shQuote(path), shQuote(paste0(record$package, "/DESCRIPTION"))),
    stdout = TRUE
  )
  if (!length(description_lines) || !is.null(attr(description_lines, "status"))) {
    stop("Cannot read locked DESCRIPTION: ", record$package)
  }
  handle <- textConnection(description_lines)
  on.exit(close(handle))
  description <- read.dcf(handle)[1L, ]
  fields <- intersect(c("Depends", "Imports", "LinkingTo"), names(description))
  declarations <- strsplit(paste(description[fields], collapse = ","), ",", fixed = TRUE)[[1L]]
  declared <- setdiff(trimws(sub("\\(.*$", "", declarations)), c("", "R"))
  if (description[["Package"]] != record$package ||
      description[["Version"]] != record$version ||
      !setequal(declared, dependencies(record))) {
    stop("scRNA source DESCRIPTION does not match the lock: ", record$package)
  }
}

dir.create(source_dir, showWarnings = FALSE)
options(timeout = 240)
for (record in records) {
  if (!grepl("^[0-9][0-9.-]*$", record$version) ||
      !grepl("^[a-f0-9]{64}$", record$sha256) ||
      !record$repository %in% c(cran, bioc) ||
      !is.numeric(record$size) || length(record$size) != 1L || record$size <= 0) {
    stop("Invalid locked scRNA source record: ", record$package)
  }
  path <- archive_path(record)
  if (mode == "fetch") fetch(path, record)
  verify(path, record)
}
if (mode == "fetch") quit(save = "no", status = 0L)

dir.create(root, recursive = TRUE, showWarnings = FALSE)
.libPaths(c(root, .libPaths()))
Sys.setenv(R_LIBS_USER = paste(c(root, base_library), collapse = .Platform$path.sep))
pending <- names(records)
done <- names(base_packages)
while (length(pending)) {
  ready <- pending[vapply(pending, function(name) {
    all(dependencies(records[[name]]) %in% done)
  }, logical(1))]
  if (!length(ready)) stop("Unresolved dependency in the scRNA source lock")
  for (name in ready) {
    record <- records[[name]]
    status <- system2(file.path(R.home("bin"), "R"), c(
      "CMD", "INSTALL", "--no-multiarch", paste0("--library=", shQuote(root)),
      shQuote(archive_path(record))
    ))
    if (status != 0L ||
        packageDescription(name, lib.loc = root, fields = "Version") != record$version) {
      stop("Failed to install the locked version of ", name)
    }
  }
  pending <- setdiff(pending, ready)
  done <- c(done, ready)
}
for (name in targets) {
  if (!requireNamespace(name, quietly = TRUE)) stop("Locked scRNA package failed to load: ", name)
}
