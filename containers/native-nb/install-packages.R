# Install a frozen source set with networking disabled by the container builder.
args <- commandArgs(trailingOnly=TRUE)
if (length(args)!=3) stop("Expected lock, source directory and library directory")
lock <- jsonlite::read_json(args[1], simplifyVector=FALSE)
if (lock$schema_version!="native_nb_package_sources/1" || lock$R!=as.character(getRversion()) ||
    lock$BiocVersion!=as.character(packageVersion("BiocVersion"))) stop("Runtime does not match source lock")
library_path <- args[3]; dir.create(library_path,recursive=TRUE)
.libPaths(c(library_path,.libPaths()))
records <- setNames(lock$packages,vapply(lock$packages,function(p) p$package,character(1)))
if (length(records)!=length(unique(names(records))) || length(records)>120) stop("Invalid package set")
for (p in records) {
    if (!grepl("^[A-Za-z][A-Za-z0-9.]*$",p$package) ||
        !grepl("^[0-9][A-Za-z0-9._+-]*$",p$version) ||
        p$file!=paste0(p$package,"_",p$version,".tar.gz")) stop("Unsafe source lock path")
    file <- file.path(args[2],p$file)
    actual <- strsplit(system2("sha256sum",shQuote(file),stdout=TRUE),"[[:space:]]+")[[1]][1]
    if (!identical(actual,p$sha256)) stop("Source SHA-256 mismatch: ",p$package)
}
pending <- names(records); done <- character()
while (length(pending)) {
    ready <- pending[vapply(pending,function(name) all(unlist(records[[name]]$dependencies) %in% done),logical(1))]
    if (!length(ready)) stop("Unresolved/cyclic source dependencies")
    for (name in ready) {
        p <- records[[name]]
        status <- system2(file.path(R.home("bin"),"R"),
                  c("CMD","INSTALL","--no-multiarch",paste0("--library=",shQuote(library_path)),
                    shQuote(file.path(args[2],p$file))))
        if (status!=0) stop("Package installation failed: ",name)
        # packageVersion canonicalizes hyphens to dots (1.4-8 -> 1.4.8).
        # Compare the raw installed DESCRIPTION value with the exact source lock.
        if (packageDescription(name,lib.loc=library_path,fields="Version")!=p$version) stop("Installed version mismatch: ",name)
        done <- c(done,name)
    }
    pending <- setdiff(pending,ready)
}
for (name in unlist(lock$targets)) {
    if (!requireNamespace(name,quietly=TRUE)) stop("Native engine load failed: ",name)
}
installed <- installed.packages()
write.csv(installed[,intersect(c("Package","Version","License","Built","NeedsCompilation"),colnames(installed)),drop=FALSE],
          file.path(dirname(library_path),"installed-packages.csv"),row.names=FALSE)
writeLines(capture.output(sessionInfo()),file.path(dirname(library_path),"runtime-session.txt"))
cat("Installed and loaded the frozen native inference dependency set\n")
