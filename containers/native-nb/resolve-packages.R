# Resolve/download public package sources only. No samples, credentials or fits.
options(timeout=300)
root <- commandArgs(trailingOnly=TRUE)[1]
if (is.na(root) || !dir.exists(root)) stop("Existing output directory required")
repos <- c(Bioc="https://bioconductor.org/packages/3.23/bioc",
           CRAN="https://cloud.r-project.org")
targets <- c("edgeR", "DESeq2", "statmod")
available <- available.packages(repos=repos, type="source")
if (!all(targets %in% rownames(available))) stop("A requested native engine is unavailable")
dependencies <- tools::package_dependencies(targets, db=available,
                which=c("Depends", "Imports", "LinkingTo"), recursive=TRUE)
selected <- unique(c(targets, unlist(dependencies)))
installed <- installed.packages()
base <- rownames(installed)[!is.na(installed[,"Priority"]) & installed[,"Priority"]=="base"]
selected <- sort(setdiff(selected, c(base,"R")))
if (length(selected)>120 || !all(selected %in% rownames(available))) stop("Unbounded/unresolved dependency set")
versions <- available[selected,"Version"]
if (!all(grepl("^[A-Za-z][A-Za-z0-9.]*$",selected)) ||
    !all(grepl("^[0-9][A-Za-z0-9._+-]*$",versions))) stop("Unsafe package/version metadata")
saveRDS(available, file.path(root,"repository-metadata.rds"))
sources <- file.path(root,"sources"); dir.create(sources)
downloaded <- download.packages(selected, destdir=sources, repos=repos, type="source", available=available)
if (nrow(downloaded)!=length(selected) || !setequal(downloaded[,1],selected)) stop("Incomplete source download")
if (sum(file.info(downloaded[,2])$size)>500*1024^2) stop("Package sources exceed 500 MiB")
direct <- tools::package_dependencies(selected, db=available,
                which=c("Depends", "Imports", "LinkingTo"), recursive=FALSE)
records <- lapply(selected,function(name) {
    file <- downloaded[downloaded[,1]==name,2]
    hash <- strsplit(system2("sha256sum", shQuote(file),stdout=TRUE), "[[:space:]]+")[[1]][1]
    if (!grepl("^[0-9a-f]{64}$",hash)) stop("Cannot hash package source")
    list(package=name, version=unname(available[name,"Version"]), file=basename(file), sha256=hash,
         repository=unname(available[name,"Repository"]),
         dependencies=intersect(direct[[name]], selected))
})
lock <- list(schema_version="native_nb_package_sources/1", R=as.character(getRversion()),
             BiocVersion=as.character(packageVersion("BiocVersion")), repositories=repos,
             targets=targets, packages=records,
             scope="Resolved source bytes for build; not a clinical qualification or package-signature assertion")
jsonlite::write_json(lock,file.path(root,"package-sources.lock.json"),pretty=TRUE,auto_unbox=TRUE)
writeLines(capture.output(sessionInfo()),file.path(root,"resolver-session.txt"))
cat("Resolved",length(records),"source packages\n")
for (p in targets) cat(p,available[p,"Version"],"\n")
