# Install only the archives identified in the image's reviewed lock. The
# package staging context is an untrusted byte source and is mounted read-only.
lock <- jsonlite::fromJSON("/opt/exploratory/r-package-lock.json", simplifyVector = FALSE)
stopifnot(
  as.character(getRversion()) == lock$r_version,
  R.version$arch == lock$r_arch,
  as.character(packageVersion("BiocVersion")) == lock$bioc_version
)
artifacts <- c(lock$system_packages, lock$r_packages)
paths <- vapply(artifacts, function(item) file.path("/packages", item$file), "")
expected <- vapply(artifacts, "[[", "", "sha256")
stopifnot(all(file.exists(paths)), !anyDuplicated(paths))
sums <- system2("sha256sum", paths, stdout = TRUE)
stopifnot(is.null(attr(sums, "status")), identical(unname(sub(" .*", "", sums)), unname(expected)))

run <- function(command, args) {
  status <- system2(command, args)
  if (status != 0L) stop(command, " exited with status ", status)
}
if (length(lock$system_packages)) {
  debs <- vapply(lock$system_packages, function(item) file.path("/packages", item$file), "")
  run("dpkg", c("--unpack", debs))
  run("dpkg", c("--configure", "-a"))
}
packages <- vapply(lock$r_packages, function(item) file.path("/packages", item$file), "")
run(file.path(R.home("bin"), "R"), c("CMD", "INSTALL", "--no-multiarch",
                                    "--no-test-load", "--library=/opt/exploratory/R", packages))
for (item in lock$r_packages) {
  version <- unname(read.dcf(file.path("/opt/exploratory/R", item$package, "DESCRIPTION"), fields = "Version")[1L])
  stopifnot(identical(version, item$version))
}
