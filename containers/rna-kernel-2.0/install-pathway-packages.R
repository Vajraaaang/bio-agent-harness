# Verify exact public package inputs before installing into a dedicated library.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1L || !args[[1L]] %in% c("fetch", "install")) {
    stop("Specify fetch or install")
}
mode <- args[[1L]]
base <- "/opt/rna/R-library"
root <- "/opt/rna/pathway-R-library"
source_dir <- "/tmp/rna-pathway-sources"
lock_path <- "/tmp/r-pathway-sources.lock.json"
base_lock <- "/opt/rna/r-sources.lock.json"
.libPaths(c(base, .Library.site, .Library))
lock <- jsonlite::read_json(lock_path)
cran <- "https://cloud.r-project.org/src/contrib"
bioc <- "https://bioconductor.org/packages/3.23/bioc/src/contrib"
mirror <- "https://bioconductor.posit.co/packages/3.23/bioc/src/contrib"

checksum <- function(path) {
    value <- system2("sha256sum", c("--", shQuote(path)), stdout = TRUE)
    if (length(value) != 1L || !is.null(attr(value, "status"))) stop("Checksum unavailable")
    sub("[[:space:]].*$", "", value)
}
if (lock$format != "devbench.rna-kernel-pathway-r-sources/1" ||
    lock$R != as.character(getRversion()) ||
    lock$BiocVersion != as.character(packageVersion("BiocVersion")) ||
    lock$base_lock_sha256 != checksum(base_lock)) {
    stop("Inherited bulk R runtime differs from the pathway lock")
}

records <- setNames(lock$packages, vapply(lock$packages, `[[`, character(1), "package"))
base_packages <- unlist(lock$base_packages)
targets <- unlist(lock$targets)
dependencies <- function(item) unlist(item$dependencies, use.names = FALSE)
inherited <- setdiff(unlist(lapply(records, dependencies)), names(records))
if (!length(records) || !length(targets) || anyDuplicated(names(records)) ||
    !all(grepl("^[A-Za-z][A-Za-z0-9.]*$", names(records))) ||
    length(intersect(names(records), names(base_packages))) ||
    !setequal(inherited, names(base_packages)) || !all(targets %in% names(records))) {
    stop("Pathway package inventory is invalid")
}
for (name in names(base_packages)) {
    version <- packageDescription(name, fields = "Version")
    if (!identical(version, base_packages[[name]])) stop("Inherited version mismatch: ", name)
}

fetch <- function(item, path) {
    archive <- basename(path)
    candidates <- c(paste(item$repository, archive, sep = "/"),
        if (item$repository == cran) paste(cran, "Archive", item$package, archive, sep = "/")
        else paste(mirror, archive, sep = "/"))
    for (url in candidates) {
        result <- tryCatch(suppressWarnings(download.file(
            url, path, mode = "wb", method = "libcurl", quiet = TRUE
        )), error = function(error) 1L)
        if (identical(result, 0L)) return(invisible(NULL))
    }
    stop("Locked pathway archive unavailable: ", item$package)
}

verify <- function(item, path) {
    if (!isTRUE(file.size(path) == item$size) || checksum(path) != item$sha256) {
        stop("Pathway source identity mismatch: ", item$package)
    }
    member <- paste0(item$package, "/DESCRIPTION")
    lines <- system2("tar", c("-xOzf", shQuote(path), shQuote(member)), stdout = TRUE)
    if (!length(lines) || !is.null(attr(lines, "status"))) stop("Missing package DESCRIPTION")
    connection <- textConnection(lines)
    on.exit(close(connection))
    description <- read.dcf(connection)[1L, ]
    fields <- intersect(c("Depends", "Imports", "LinkingTo"), names(description))
    raw <- strsplit(paste(description[fields], collapse = ","), ",", fixed = TRUE)[[1L]]
    declared <- setdiff(trimws(sub("\\(.*$", "", raw)), c("", "R"))
    if (description[["Package"]] != item$package ||
        description[["Version"]] != item$version || !setequal(declared, dependencies(item))) {
        stop("Pathway package DESCRIPTION differs from the lock: ", item$package)
    }
}

dir.create(source_dir, showWarnings = FALSE)
options(timeout = 240)
archive_path <- function(item) {
    file.path(source_dir, paste0(item$package, "_", item$version, ".tar.gz"))
}
for (item in records) {
    if (!grepl("^[0-9][0-9.-]*$", item$version) ||
        !grepl("^[a-f0-9]{64}$", item$sha256) || !item$repository %in% c(cran, bioc) ||
        !is.numeric(item$size) || length(item$size) != 1L || item$size <= 0) {
        stop("Invalid pathway source record: ", item$package)
    }
    path <- archive_path(item)
    if (mode == "fetch") fetch(item, path)
    verify(item, path)
}
if (mode == "fetch") quit(save = "no", status = 0L)

dir.create(root, recursive = TRUE, showWarnings = FALSE)
.libPaths(c(root, .libPaths()))
Sys.setenv(R_LIBS_USER = paste(c(root, base), collapse = .Platform$path.sep))
installed <- names(base_packages)
pending <- names(records)
while (length(pending)) {
    ready <- pending[vapply(pending, function(name) {
        all(dependencies(records[[name]]) %in% installed)
    }, logical(1L))]
    if (!length(ready)) stop("Unresolved pathway package dependencies")
    for (name in ready) {
        item <- records[[name]]
        status <- system2(file.path(R.home("bin"), "R"), c(
            "CMD", "INSTALL", "--no-multiarch", paste0("--library=", root),
            shQuote(archive_path(item))
        ))
        if (status != 0L ||
            packageDescription(name, lib.loc = root, fields = "Version") != item$version) {
            stop("Locked pathway package installation failed: ", name)
        }
    }
    pending <- setdiff(pending, ready)
    installed <- c(installed, ready)
}
for (name in targets) {
    if (!requireNamespace(name, quietly = TRUE)) stop("Locked package failed to load: ", name)
}
