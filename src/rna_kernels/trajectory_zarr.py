"""Open the normalized trajectory matrix under its retained directory digest."""

from contextlib import contextmanager

from devbench.core.expression_io import open_expression
from devbench.core.h5ad_gene_blocks import ZarrGeneBlocks
from devharness_core.provenance import hash_artifact


@contextmanager
def bound_trajectory_zarr(path, spec):
    if hash_artifact(path, max_entries=100_000)["sha256"] != spec.expression_sha256:
        raise ValueError("Evidence input hash mismatch")
    with open_expression(path, layer=spec.layer) as source:
        yield ZarrGeneBlocks(source)
    if hash_artifact(path, max_entries=100_000)["sha256"] != spec.expression_sha256:
        raise ValueError("Evidence input changed during trajectory execution")
