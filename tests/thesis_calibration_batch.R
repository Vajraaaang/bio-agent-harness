# Invoke the exact baked mounted driver with independent scratch and model
# environments. Sharing the pinned library namespace avoids 1,000 R startups.
driver <- "/opt/rna/runtime/devharness_server/r/mounted_general.R"
case_names <- readLines("/inputs/cases.txt", n = 1001L)
if (!length(case_names) || length(case_names) > 1000L || anyDuplicated(case_names) ||
    any(!grepl("^(bi|bp|si|sp|pp)-(null|planted)-[0-9]{3}$", case_names))) {
    stop("Invalid calibration batch inventory")
}
invisible(loadNamespace("edgeR"))
inputs <- c("model.json", "counts.bin", "design.bin", "rle.bin")
outputs <- c("result.bin", "method.json")

fit_one <- function(case, index) {
    warnings <- character()
    scratch <- tempfile("rna-calibration-")
    dir.create(scratch, mode = "0700")
    on.exit(unlink(scratch, recursive = TRUE), add = TRUE)
    source <- file.path("/inputs", case, inputs)
    sizes <- file.info(source)$size
    if (anyNA(sizes) || any(sizes < 1) || sum(sizes) > 131072L) {
        stop("Unbounded or missing cohort inputs")
    }
    if (!all(file.copy(source, scratch))) stop("Failed to stage cohort")

    env <- new.env(parent = globalenv())
    env$commandArgs <- function(trailingOnly = FALSE) {
        if (trailingOnly) scratch else paste0("--file=", driver)
    }
    env$source <- function(file) sys.source(file, envir = env)
    # Individual edgeR fits do not draw random values. Reset the seed anyway
    # so a dependency cannot inherit another cohort's RNG state.
    set.seed(20261117L + index)
    withCallingHandlers(
        sys.source(driver, envir = env),
        warning = function(warning) {
            warnings <<- unique(c(warnings, substr(conditionMessage(warning), 1L, 200L)))
            invokeRestart("muffleWarning")
        }
    )
    if (!dir.create(file.path("/outputs", case), mode = "0700") ||
        !all(file.copy(file.path(scratch, outputs), file.path("/outputs", case)))) {
        stop("Failed to collect cohort")
    }
    list(case_id = case, status = "completed", warnings = as.list(warnings))
}

receipts <- lapply(seq_along(case_names), function(index) {
    start <- proc.time()[["elapsed"]]
    case <- case_names[[index]]
    observed <- tryCatch(
        fit_one(case, index),
        error = function(error) {
            list(case_id = case, status = "failed",
                 reason = substr(conditionMessage(error), 1L, 400L))
        }
    )
    observed$elapsed_seconds <- proc.time()[["elapsed"]] - start
    observed
})
jsonlite::write_json(receipts, "/outputs/cohorts.json", auto_unbox = TRUE, digits = NA)
