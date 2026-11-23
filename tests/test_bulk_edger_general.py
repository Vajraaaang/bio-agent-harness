"""Opt-in synthetic edgeR checks on the installed pinned Bioconductor runtime."""

import os
import re
import subprocess
from pathlib import Path

import pytest

_IMAGE = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE", "")
pytestmark = pytest.mark.skipif(
    not _IMAGE, reason="Set the exact local RNA kernel image ID"
)
_R = r"""
source("/input/bulk_edger.R")
set.seed(344)
groups <- factor(rep(c("control", "a", "b"), each = 8))
batch <- factor(rep(c("x", "y"), 12))
n <- length(groups)
s <- paste0("s", seq_len(n))
g <- paste0("g", seq_len(300))
X <- model.matrix(~ batch + groups)
Z <- model.matrix(~ batch)
rownames(X) <- rownames(Z) <- s
counts <- matrix(rnbinom(length(g) * n, mu = 40, size = 12), nrow = length(g),
                 dimnames = list(g, s))
counts[1:25, groups == "b"] <- 3 * counts[1:25, groups == "b"]
counts[1, groups == "control"] <- 0
units <- setNames(s, s)
labels <- setNames(as.character(groups), s)
factors <- setNames(rep(1, n), s)
qlf <- bulk_edger_count(counts, X, units, labels, size_factors=factors, reduced=Z)
lrt <- bulk_edger_count(counts, X, units, labels, size_factors=factors,
                        reduced=Z, test="lrt")
for (x in list(qlf, lrt)) {
    valid <- x$rows$estimate_status == "estimated"
    stopifnot(x$method$numerator_df == 2L, identical(x$rows$gene_id, g),
              all(is.na(x$rows$effect)),
              all(x$rows$q_value[valid] >= x$rows$p_value[valid]),
              identical(x$rows$missing_reason[1], "zero_group_marginal"),
              median(x$rows$p_value[2:25]) < 0.01)
}
effective <- matrix(1000, nrow=nrow(counts), ncol=n, dimnames=dimnames(counts))
effective[26:70, groups == "b"] <- 1800
estimated <- counts + 0.5
estimated[1, groups == "control"] <- 0
txi <- list(counts=estimated, length=effective, countsFromAbundance="no")
contrast <- setNames(c(0,0,0,1), colnames(X))
tx <- bulk_edger_count(estimated, X, units, labels,
                       measurement="tximport_estimated_counts", tximport=txi,
                       contrast=contrast)
stopifnot(tx$method$normalization == "tximport_effective_length_and_TMM_2D_offset",
          tx$method$numerator_df == 1L, is.finite(tx$rows$effect[2]))
error <- tryCatch(bulk_edger_count(counts, X, units, labels, size_factors=factors,
    reduced=cbind(Z, nuisance=seq_len(n))), error=function(e) conditionMessage(e))
stopifnot(grepl("nested", error, fixed=TRUE))
"""


def test_general_edger_nested_and_tximport_offsets(workspace_tmp_path):
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", _IMAGE), "Use a local image content ID"
    driver = Path(__file__).resolve().parents[1] / "devharness_server/r/bulk_edger.R"
    check = workspace_tmp_path / "check.R"
    check.write_text(_R)
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=64m",
            "--mount",
            f"type=bind,src={driver},dst=/input/bulk_edger.R,readonly",
            "--mount",
            f"type=bind,src={check},dst=/check.R,readonly",
            "--entrypoint",
            "Rscript",
            _IMAGE,
            "--vanilla",
            "/check.R",
        ],
        check=True,
        timeout=90,
    )
