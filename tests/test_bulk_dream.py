"""Synthetic tests for the repeated-donor bulk dream solver."""

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
_DRIVER = Path(__file__).resolve().parents[1] / "src/devharness_server/r/bulk_dream.R"
_FIXTURE = r"""
source("/driver.R")
set.seed(413)
donor <- rep(paste0("d", seq_len(12)), c(3, rep(2, 11)))
n <- length(donor)
condition <- c("control", "case", "case", rep(c("control", "case"), 11))
rownames_input <- paste0("g", 1:64)
sample_ids <- paste0("s", seq_len(n))
metadata <- data.frame(
    subject = donor, group = condition, run = rep(c("A", "B", "A"), length.out=n),
    age = rnorm(n), row.names = sample_ids
)
signal <- outer(rnorm(64, 0, 0.4), rep(1, n))
signal <- signal + matrix(rep(rnorm(64*12, 0, 0.4), each=1), 64, 12)[,
    match(donor, unique(donor))]
signal[1:8, condition == "case"] <- signal[1:8, condition == "case"] + 0.8
expression <- 1.3 + signal + matrix(rnorm(64*n, 0, 0.16), 64, n)
dimnames(expression) <- list(rownames_input, sample_ids)
if (commandArgs(trailingOnly=TRUE)[1L] == "raw_counts") {
    means <- exp(expression) * 12
    expression[] <- rpois(length(means), means)
    expression[62, ] <- c(1, rep(0, n - 1L))
    expression[64, ] <- 0
    expression[63, condition == "case"] <- 0
} else {
    expression[64, ] <- 0.125
    expression[63, ] <- expression[63, ] - 10
}
measurement <- commandArgs(trailingOnly=TRUE)[1L]
fit <- bulk_dream(expression, metadata[rev(sample_ids), ], "group", "subject",
                  "case", "control",
                  technical_factors = c("run", "age"), measurement = measurement)
rows <- fit$rows
estimated <- rows$estimate_status == "estimated"
eligible <- rows$estimate_status %in% c("estimated", "failed")
stopifnot(
    identical(rows$gene_id, rownames_input),
    sum(estimated) >= 58L,
    all(rows$p_value[estimated] >= 0 &
        rows$q_value[estimated] >= rows$p_value[estimated]
        & rows$q_value[estimated] <= 1),
    all(is.na(rows$p_value[!estimated])), all(is.na(rows$q_value[!estimated])),
    all(is.na(rows$effect[!estimated])),
    identical(rows$estimate_status[64L], "not_estimable"),
    mean(rows$effect[1:8]) > 0.35,
    fit$diagnostics$donor_count == 12L,
    fit$diagnostics$repeated_donor_count == 12L,
    !fit$diagnostics$balanced_donor_profile_counts,
    all(rows$q_value[estimated] ==
        p.adjust(ifelse(estimated[eligible], rows$p_value[eligible], 1),
                 method="BH")[estimated[eligible]])
)
if (measurement == "raw_counts") {
    stopifnot(identical(rows$estimate_status[62L], "filtered"),
              identical(rows$missing_reason[64L], "all_zero_gene"),
              estimated[63L], rows$effect[63L] < 0,
              identical(fit$diagnostics$normalization, "TMM_effective_library_sizes"),
              isTRUE(fit$diagnostics$voom_weights))
    zeros <- matrix(0, nrow=100, ncol=n,
                    dimnames=list(paste0("zero", 1:100), sample_ids))
    expanded <- bulk_dream(rbind(expression, zeros), metadata, "group", "subject",
                          "case", "control", technical_factors=c("run", "age"))$rows
    stopifnot(isTRUE(all.equal(expanded$p_value[1:64], rows$p_value)),
              isTRUE(all.equal(expanded$q_value[1:64], rows$q_value)),
              all(is.na(expanded$q_value[65:164])))
    factors <- seq(0.7, 1.3, length.out=n)
    weighted <- bulk_dream(expression, metadata, "group", "subject", "case", "control",
                          technical_factors=c("run", "age"), measurement=measurement,
                          size_factors=factors)
    stopifnot(identical(weighted$diagnostics$normalization,
                       "supplied_RLE_effective_library_sizes"))
} else {
    stopifnot(identical(fit$diagnostics$normalization, "none_already_continuous"),
              !fit$diagnostics$voom_weights, estimated[63L])
}
metadata$confounded <- metadata$group
blocked <- tryCatch(
    bulk_dream(expression, metadata, "group", "subject", "case", "control",
               technical_factors = "confounded", measurement = measurement),
    error = function(e) conditionMessage(e)
)
stopifnot(is.character(blocked), grepl("rank", blocked))
metadata$subject <- rownames(metadata)
blocked <- tryCatch(
    bulk_dream(expression, metadata, "group", "subject", "case", "control",
               measurement = measurement),
    error = function(e) conditionMessage(e)
)
stopifnot(is.character(blocked), grepl("repeated", blocked))
if (measurement == "raw_counts") {
    expression[1, 1] <- 0.25
    blocked <- tryCatch(
        bulk_dream(expression, metadata, "group", "subject", "case", "control",
                   measurement = measurement),
        error = function(e) conditionMessage(e)
    )
    stopifnot(is.character(blocked), grepl("integer", blocked))
}
cat("dream smoke passed\n")
"""


@pytest.mark.parametrize("measurement", ["continuous", "raw_counts"])
def test_bulk_dream_locked_image(workspace_tmp_path, measurement):
    platform = kernel_test_platform(_IMAGE)
    assert platform is not None, "Use an image ID from the native kernel runtime lock"
    docker = shutil.which("docker")
    assert docker is not None, "opted-in dream smoke requires local Docker"
    script = workspace_tmp_path / "dream_smoke.R"
    script.write_text(_FIXTURE, encoding="utf-8")
    script.chmod(0o444)
    result = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "--pull=never",
            "--platform",
            platform,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65532:65532",
            "--pids-limit",
            "128",
            "--memory",
            "2g",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
            "--mount",
            f"type=bind,src={_DRIVER},dst=/driver.R,readonly",
            "--mount",
            f"type=bind,src={script},dst=/fixture.R,readonly",
            "--entrypoint",
            "Rscript",
            _IMAGE,
            "--vanilla",
            "/fixture.R",
            measurement,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-4_000:]
    assert result.stdout.rstrip().endswith("dream smoke passed")
