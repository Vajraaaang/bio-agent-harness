"""Exercise the DESeq2 sensitivity kernel against real locked packages."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from devharness_controlled.native_contracts import kernel_test_platform

_IMAGE = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE", "")
pytestmark = pytest.mark.skipif(
    not _IMAGE, reason="Set DEVBENCH_RNA_KERNEL_TEST_IMAGE to a locked local image ID"
)
_DRIVER = (
    Path(__file__)
    .resolve()
    .parents[1]
    .joinpath("src", "devharness_server", "r", "bulk_deseq2_sensitivity.R")
)

_TEST = """
source("/driver.R")
set.seed(101)
groups <- rep(c("A", "B", "C"), each = 4)
batch <- rep(c("early", "late"), 6)
ids <- paste0("sample", seq_along(groups))
genes <- paste0("gene", seq_len(120))
full <- model.matrix(~ batch + groups)
reduced <- model.matrix(~ batch)
rownames(full) <- rownames(reduced) <- ids
base <- runif(length(genes), 40, 300)
signal <- matrix(rep(base, length(ids)), nrow = length(genes))
signal[3:12, groups == "B"] <- signal[3:12, groups == "B"] * 5
x <- matrix(rnbinom(length(signal), mu = as.vector(signal), size = 18),
            nrow = length(genes), dimnames = list(genes, ids))
x[1, ] <- 0
x[2, groups == "A"] <- 0
x[3, groups == "C"] <- 0
factors <- setNames(rep(1, length(ids)), ids)

fit <- function(...) bulk_deseq2_sensitivity(x, full, ids, groups, factors, ...)
check <- function(result, test, shrinkage) {
    rows <- result$rows
    stopifnot(identical(rows$gene_id, genes), result$method$test == test,
              identical(result$method$shrinkage, shrinkage),
              rows$estimate_status[1] == "filtered",
              all(rows$estimate_status[2:3] == "estimated"),
              result$method$dispersion_fit_requested == "parametric",
              result$method$dispersion_fit %in% c("parametric", "local", "mean"),
              (length(result$method$dispersion_fit_fallbacks) > 0) ==
                  (result$method$dispersion_fit != "parametric"))
    passing <- rows$estimate_status == "estimated"
    family <- rows$estimate_status != "filtered"
    stopifnot(sum(passing) > 90, all(is.na(rows$p_value[!passing])),
              all(is.na(rows$q_value[!passing])))
    padded <- ifelse(passing[family], rows$p_value[family], 1)
    expected <- p.adjust(padded, "BH")
    stopifnot(isTRUE(all.equal(rows$q_value[passing], expected[passing[family]])),
              all(rows$p_value[passing] <= rows$q_value[passing]))
}

wald <- fit(coefficient = "groupsB")
check(wald, "Wald", "apeglm")
contrast <- setNames(c(0, 0, 1, 0), colnames(full))
arbitrary <- fit(contrast = contrast)
check(arbitrary, "Wald", "ashr")
for (result in list(wald, arbitrary)) {
    rows <- result$rows
    passing <- rows$estimate_status == "estimated"
    stopifnot(all(is.finite(rows$effect[passing])),
              all(is.finite(rows$effect_se[passing])),
              all(rows$effect_se[passing] >= 0),
              median(rows$effect[3:12], na.rm = TRUE) > 1)
}

lrt <- fit(reduced = reduced)
check(lrt, "LRT", NULL)
stopifnot(lrt$method$test_df == 2L, all(is.na(lrt$rows$effect)),
          all(is.na(lrt$rows$effect_se)))
fails <- function(expression, pattern) {
    failure <- tryCatch(force(expression), error = conditionMessage)
    stopifnot(is.character(failure), grepl(pattern, failure))
}
fails(fit(coefficient = "missing"), "coefficient")
fails(fit(contrast = contrast[c(1, 3, 2, 4)]), "contrast")
fails(fit(contrast = contrast, reduced = reduced), "exactly one")
not_nested <- reduced
not_nested[, 2] <- sin(seq_len(nrow(full)))
fails(fit(reduced = not_nested), "nested")
fails(bulk_deseq2_sensitivity(x, full[nrow(full):1, ], ids, groups, factors,
                             coefficient = "groupsB"), "sample axis")
nonindependent <- ids
nonindependent[1:3] <- ids[[1]]
fails(bulk_deseq2_sensitivity(x, full, nonindependent, groups, factors,
                             coefficient = "groupsB"),
      "one observation per biological unit")
"""


def test_locked_deseq2_wald_shrinkage_and_nested_lrt(workspace_tmp_path):
    platform = kernel_test_platform(_IMAGE)
    assert platform is not None, "Use an image ID from the native kernel runtime lock"
    docker = shutil.which("docker")
    assert docker is not None, "opted-in DESeq2 smoke requires local Docker"
    test_script = workspace_tmp_path / "synthetic.R"
    test_script.write_text(_TEST, encoding="utf-8")
    run = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "--pull=never",
            "--platform",
            platform,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=256m",
            "--mount",
            f"type=bind,src={_DRIVER},dst=/driver.R,readonly",
            "--mount",
            f"type=bind,src={test_script},dst=/test.R,readonly",
            "--entrypoint",
            "/usr/local/bin/Rscript",
            _IMAGE,
            "--vanilla",
            "/test.R",
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert run.returncode == 0, f"{run.stdout}\n{run.stderr}"
