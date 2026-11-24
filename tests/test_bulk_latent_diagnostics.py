"""Synthetic checks for the pinned R latent diagnostic entry point."""

import os
import re
import subprocess
from pathlib import Path

import pytest

_IMAGE = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE", "")
pytestmark = pytest.mark.skipif(
    not _IMAGE, reason="Set the exact local RNA kernel image ID"
)
_DRIVER = (
    Path(__file__).resolve().parents[1]
    / "devharness_server/r/bulk_latent_diagnostics.R"
)

_R_FIXTURE = """
source("/diagnostic.R")
set.seed(392)
samples <- sprintf("s%02d", seq_len(32))
genes <- sprintf("g%03d", seq_len(160))
condition <- rep(0:1, each = 16)
batch <- rep(rep(c(-1, 1), each = 8), 2)
full <- cbind(intercept = 1, condition = condition)
rownames(full) <- samples
reduced <- full[, "intercept", drop = FALSE]
mu <- exp(4 + outer(c(rep(0, 125), rep(1, 35)), condition) +
          outer(c(rep(1.1, 35), rep(0, 125)), batch))
counts <- matrix(rpois(length(mu), mu), nrow = length(genes),
                 dimnames = list(genes, samples))
fails <- function(expression, pattern) {
    message <- tryCatch({ force(expression); "" }, error = conditionMessage)
    stopifnot(grepl(pattern, message))
}
"""


def _r(workspace, script):
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", _IMAGE)
    command = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--pids-limit=64",
        "--memory=2g",
        "--cpus=2",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=64m",
        f"--user={os.getuid()}:{os.getgid()}",
        "--mount",
        f"type=bind,src={_DRIVER},dst=/diagnostic.R,readonly",
        "--mount",
        f"type=bind,src={workspace},dst=/scratch,readonly",
        "--entrypoint",
        "Rscript",
        _IMAGE,
        "--vanilla",
        "/scratch/test.R",
    ]
    (workspace / "test.R").write_text(_R_FIXTURE + script)
    result = subprocess.run(
        command, check=False, timeout=90, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr[-2000:]


@pytest.mark.parametrize("num_sv", ["be", "leek"])
def test_svaseq_uses_frozen_estimator_and_reports_conditioning(
    workspace_tmp_path, num_sv
):
    _r(
        workspace_tmp_path,
        """
original <- counts
seed_before <- .Random.seed
out <- bulk_latent_diagnostics(counts, full, reduced, "svaseq", sv_method = "%s")
again <- bulk_latent_diagnostics(counts, full, reduced, "svaseq", sv_method = "%s")
stopifnot(identical(counts, original), identical(seed_before, .Random.seed),
          identical(out$scores, again$scores), identical(rownames(out$scores), samples),
          out$n_factors >= 1, abs(cor(out$scores[, 1], batch)) > 0.8,
          out$conditioning$num_sv_method == "%s", out$diagnostic_only,
          is.null(out$normalizedCounts), out$design$full$rank == 2,
          out$design$reduced$rank == 1, out$augmented_design$full$rank >= 3)
fails(bulk_latent_diagnostics(counts, full, reduced, "svaseq", sv_method = "b"),
      "exactly")
fails(bulk_latent_diagnostics(counts, full, reduced[nrow(reduced):1, , drop = FALSE],
                              "svaseq"),
      "align")
fails(bulk_latent_diagnostics(counts, full, full[, "condition", drop = FALSE],
                              "svaseq"),
      "intercept")
fails(bulk_latent_diagnostics(counts, full, cbind(reduced, latent = batch), "svaseq"),
      "nested")
"""
        % ((num_sv,) * 3),
    )


def test_ruvg_requires_independent_control_provenance(workspace_tmp_path):
    _r(
        workspace_tmp_path,
        """
controls <- list(origin = "predeclared_external", scope = "independent",
                 source = "synthetic external reference panel fixture",
                 gene_ids = genes[1:20])
out <- bulk_latent_diagnostics(counts, full, reduced, "ruvg",
                               controls = controls, n_factors = 1L)
stopifnot(out$diagnostic_only, is.null(out$normalizedCounts), out$n_factors == 1,
          identical(rownames(out$scores), samples),
          abs(cor(out$scores[, 1], batch)) > 0.8,
          identical(out$controls, controls), out$augmented_design$full$passes)
controls$origin <- "same_cohort_de"
fails(bulk_latent_diagnostics(counts, full, reduced, "ruvg",
                              controls = controls, n_factors = 1L),
      "independent")
controls$origin <- "spike_ins"
controls$scope <- "same_cohort_de"
fails(bulk_latent_diagnostics(counts, full, reduced, "ruvg",
                              controls = controls, n_factors = 1L),
      "independent")
controls$scope <- "independent"
counts[] <- rep(seq_len(nrow(counts)) * 10L + 400L, ncol(counts))
counts[genes[1:20], ] <- outer(seq_len(20), 10 + condition)
out <- bulk_latent_diagnostics(counts, full, reduced, "ruvg",
                               controls = controls, n_factors = 1L)
stopifnot(!out$augmented_design$full$passes, out$augmented_design$full$rank == 2)
fails(bulk_latent_diagnostics(counts, full, reduced, "ruvg",
                              controls = controls, n_factors = 2L),
      "control rank")
""",
    )


def test_depth_only_counts_do_not_create_latent_factors(workspace_tmp_path):
    _r(
        workspace_tmp_path,
        """
depth <- rep(c(1, 4, 3, 8, 2, 12, 5, 15), 4)
depth_only <- outer(50 + seq_along(genes), depth)
dimnames(depth_only) <- list(genes, samples)
for (estimator in c("be", "leek")) {
    out <- bulk_latent_diagnostics(depth_only, full, reduced, "svaseq",
                                   sv_method = estimator)
    stopifnot(out$n_factors == 0L, identical(rownames(out$scores), samples),
              ncol(out$scores) == 0L, out$augmented_design$full$rank == 2L)
}
controls <- list(origin = "spike_ins", scope = "independent",
                 source = "synthetic depth null", gene_ids = genes[1:20])
fails(bulk_latent_diagnostics(depth_only, full, reduced, "ruvg",
                              controls = controls, n_factors = 1L), "control rank")
""",
    )
