# Check the locked source descriptions and every required inherited dependency.
args <- commandArgs(trailingOnly=TRUE)
stopifnot(length(args)==2L)
lock <- jsonlite::read_json(args[[1]])
base <- unlist(lock$base_packages)
records <- setNames(lock$packages, vapply(lock$packages, "[[", character(1), "package"))
stopifnot(!anyDuplicated(names(records)), !anyDuplicated(names(base)),
          !length(intersect(names(base),names(records))),
          all(unlist(lock$targets) %in% names(records)))
versions <- c(R=lock$R, base, vapply(records, "[[", character(1), "version"))
check_dep <- function(decl) {
  pattern <- "^([A-Za-z][A-Za-z0-9.]*)[[:space:]]*(?:\\([[:space:]]*(>=|<=|==|>|<)[[:space:]]*([0-9.-]+)[[:space:]]*\\))?$"
  hit <- regmatches(decl,regexec(pattern,decl,perl=TRUE))[[1]]
  if (length(hit)!=4L || !hit[[2]] %in% names(versions)) stop("Unbound requirement: ", decl)
  if (nzchar(hit[[3]])) {
    actual <- package_version(versions[[hit[[2]]]])
    required <- package_version(hit[[4]])
    if (!do.call(hit[[3]],list(actual,required))) stop("Version constraint: ", decl)
  }
  hit[[2]]
}
for (name in names(records)) {
  rec <- records[[name]]
  path <- file.path(args[[2]],paste0(name,".tar.gz"))
  digest <- system2("sha256sum", c("--", shQuote(path)), stdout=TRUE)
  stopifnot(length(digest)==1, substr(digest,1,64)==rec$sha256, file.size(path)==rec$size)
  # DropletQC is a commit-pinned source archive. Other sources use R's package layout.
  members <- system2("tar", c("-tzf",shQuote(path)),stdout=TRUE)
  description <- grep("^[A-Za-z0-9.-]+/DESCRIPTION$",members,value=TRUE)
  stopifnot(length(description)==1L)
  lines <- system2("tar",c("-xOzf",shQuote(path),shQuote(description)),stdout=TRUE)
  con <- textConnection(lines)
  desc <- read.dcf(con)[1,]
  close(con)
  stopifnot(desc[["Package"]]==name, desc[["Version"]]==rec$version)
  fields <- intersect(c("Depends","Imports","LinkingTo"),names(desc))
  terms <- trimws(unlist(strsplit(paste(desc[fields],collapse=","), ",",fixed=TRUE)))
  declared <- vapply(terms[nzchar(terms)],check_dep,character(1))
  stopifnot(setequal(setdiff(declared,"R"),unlist(rec$dependencies)))
}
reachable <- unlist(lock$targets)
repeat {
  next_names <- union(reachable,unlist(lapply(records[intersect(names(records),reachable)], "[[", "dependencies")))
  if (setequal(next_names,reachable)) break
  reachable <- next_names
}
stopifnot(setequal(reachable,c(names(base),names(records))))
cat("M2 source closure verified:",length(records),"archives,",length(base),"inherited packages.\n")
