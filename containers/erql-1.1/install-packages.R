# P0 installation only. No network, study inputs, model calls or qualification.
root <- "/opt/devharness-erql"
lock <- jsonlite::read_json(file.path(root, "source-lock.json"), simplifyVector=FALSE)
stopifnot(lock$schema_version == "erql_package_sources/1", lock$profile == "ERQL-1.1",
          lock$R == as.character(getRversion()),
          lock$BiocVersion == as.character(packageVersion("BiocVersion")))
library_path <- file.path(root, "R-library")
dir.create(library_path, recursive=TRUE)
.libPaths(c(library_path, .libPaths()))
records <- setNames(lock$packages, vapply(lock$packages, function(p) p$package, character(1)))
stopifnot(length(records) == 6L, !anyDuplicated(names(records)), records$edgeR$version == "4.10.7")
for (p in records) {
    stopifnot(grepl("^[A-Za-z][A-Za-z0-9.]*$", p$package),
              grepl("^[0-9][A-Za-z0-9._+-]*$", p$version),
              p$file == paste0(p$package, "_", p$version, ".tar.gz"),
              grepl("^[a-f0-9]{64}$", p$sha256))
    archive <- file.path(root, "sources", p$file)
    actual <- strsplit(system2("sha256sum", shQuote(archive), stdout=TRUE), "[[:space:]]+")[[1]][1]
    stopifnot(identical(actual, p$sha256))
}
pending <- names(records)
done <- character()
while (length(pending)) {
    ready <- pending[vapply(pending, function(name) all(unlist(records[[name]]$dependencies) %in% done), logical(1))]
    if (!length(ready)) stop("Unresolved/cyclic ERQL source dependencies")
    for (name in ready) {
        p <- records[[name]]
        status <- system2(file.path(R.home("bin"), "R"), c("CMD", "INSTALL", "--no-multiarch",
                          paste0("--library=", shQuote(library_path)),
                          shQuote(file.path(root, "sources", p$file))))
        if (status != 0L) stop("Package installation failed: ", name)
        stopifnot(packageDescription(name, lib.loc=library_path, fields="Version") == p$version)
        done <- c(done, name)
    }
    pending <- setdiff(pending, ready)
}
for (name in unlist(lock$targets)) stopifnot(requireNamespace(name, quietly=TRUE))
# Runtime DESCRIPTION constraints, including base R packages, must be satisfied.
for (name in names(records)) {
    description <- packageDescription(name, lib.loc=library_path)
    for (field in c("Depends", "Imports", "LinkingTo")) {
        if (is.null(description[[field]])) next
        for (requirement in strsplit(description[[field]], ",")[[1]]) {
            pieces <- regmatches(trimws(requirement), regexec("^([A-Za-z][A-Za-z0-9.]*) *(?:\\(([><=]+) *([^ )]+)\\))?$", trimws(requirement), perl=TRUE))[[1]]
            if (!length(pieces)) stop("Unparsed dependency: ", requirement)
            installed <- if (pieces[2] == "R") as.character(getRversion()) else as.character(packageVersion(pieces[2]))
            if (length(pieces) >= 4L && nzchar(pieces[3])) {
                comparison <- compareVersion(installed, pieces[4])
                valid <- switch(pieces[3], ">="=comparison >= 0, "<="=comparison <= 0,
                                "=="=comparison == 0, ">"=comparison > 0, "<"=comparison < 0, FALSE)
                if (!valid) stop("Unsatisfied dependency: ", name, " requires ", requirement)
            }
        }
    }
}
installed <- installed.packages()
write.csv(installed[, c("Package", "Version", "License", "Built"), drop=FALSE],
          file.path(root, "installed-packages.csv"), row.names=FALSE)
writeLines(capture.output(sessionInfo()), file.path(root, "installation-session.txt"))
cat("Verified ERQL source installation and dependency metadata; qualification not run\n")
