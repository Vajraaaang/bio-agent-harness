# Install the reviewed M2 closure using only the staged, hash-pinned archives.
args <- commandArgs(trailingOnly=TRUE)
stopifnot(length(args)==3L)
lock <- jsonlite::read_json(args[[1]])
source_dir <- args[[2]]
root <- args[[3]]
stopifnot(lock$format=="devbench.m2-qc-r-sources/1",
          as.character(getRversion())==lock$R,
          as.character(packageVersion("BiocVersion"))==lock$BiocVersion)
parents <- unlist(lock$base_packages)
for (p in names(parents)) {
  if (packageDescription(p,fields="Version")!=parents[[p]]) stop("Inherited version mismatch: ", p)
}
dir.create(root, recursive=TRUE, showWarnings=FALSE)
.libPaths(c(root, .libPaths()))
Sys.setenv(R_LIBS_USER=paste(.libPaths(), collapse=.Platform$path.sep))
pkgs <- setNames(lock$packages, vapply(lock$packages, "[[", character(1), "package"))
pending <- names(pkgs)
done <- names(parents)
for (name in pending) {
  rec <- pkgs[[name]]
  archive <- file.path(source_dir,paste0(name,".tar.gz"))
  sha <- system2("sha256sum",c("--",shQuote(archive)),stdout=TRUE)
  stopifnot(length(sha)==1L,substr(sha,1,64)==rec$sha256,file.size(archive)==rec$size)
}
while (length(pending)) {
  ready <- pending[vapply(pending,function(p) all(unlist(pkgs[[p]]$dependencies) %in% done),logical(1))]
  if (!length(ready)) stop("Incomplete M2 dependency closure")
  for (name in ready) {
    rec <- pkgs[[name]]
    archive <- file.path(source_dir,paste0(name,".tar.gz"))
    status <- system2(file.path(R.home("bin"),"R"),c("CMD","INSTALL","--no-multiarch",
                      "--no-docs","--no-html",paste0("--library=",shQuote(root)),shQuote(archive)))
    if (status!=0L || packageDescription(name,lib.loc=root,fields="Version")!=rec$version) stop("Install failed: ",name)
  }
  done <- c(done,ready)
  pending <- setdiff(pending,ready)
}
for (p in unlist(lock$targets)) stopifnot(requireNamespace(p,quietly=TRUE))
