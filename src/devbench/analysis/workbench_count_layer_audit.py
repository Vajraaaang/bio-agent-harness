"""Read-only technical diagnostics on a bounded AnnData counts layer."""

import numpy as np
from scipy.sparse import csr_matrix

from devbench.analysis.rna_scrna_input_contract import _closed_storage
from devbench.analysis.singlecell import validate_counts
from devbench.core.h5ad_gene_blocks import _bounded_node, _header
from devbench.core.population_data import _sparse_components

SOURCE = "layers/counts"


def count_layer_blocks(handle, observed, max_rows):
    """Yield checked, canonical CSR blocks with their original row offsets."""
    if "counts" not in observed["layers"]:
        raise ValueError("The AnnData file has no stored counts layer")
    _closed_storage(handle)
    matrix = _bounded_node(_bounded_node(handle, "layers"), "counts")
    shape, layout = _header(matrix)
    if (
        layout != "csr" or shape != (observed["cells"], observed["genes"])
        or shape[0] > max_rows or shape[1] > 100_000
    ):
        raise ValueError("Counts diagnostics require a bounded, header-matched CSR layer")
    for name in ("data", "indices", "indptr"):
        _bounded_node(matrix, name)
    data, indices, indptr = _sparse_components(matrix, shape, "csr", 16 * 1024**2)
    offsets = indptr[:]
    start = 0
    while start < shape[0]:
        left = int(offsets[start])
        stop = min(
            start + 256, shape[0],
            int(np.searchsorted(offsets, left + 1_000_000, side="right")) - 1,
        )
        if stop <= start:
            raise ValueError("A count row exceeds the diagnostic block budget")
        right = int(offsets[stop])
        values = data[left:right]
        validate_counts(values)
        block = csr_matrix(
            (values, indices[left:right], offsets[start:stop + 1] - left),
            shape=(stop - start, shape[1]),
        )
        block.sum_duplicates()
        block.eliminate_zeros()
        yield start, block, right - left
        start = stop


def audit_count_layer(handle, observed, max_rows):
    """Read every stored CSR value without materializing the expression matrix."""
    n_rows = observed["cells"]
    if not 1 <= n_rows <= max_rows:
        raise ValueError("Counts diagnostics exceed their observation bound")
    blocks = count_layer_blocks(handle, observed, max_rows)
    stored = 0
    depths = np.zeros(n_rows, dtype=np.float64)
    detected = np.zeros(n_rows, dtype=np.int64)
    for start, block, entries in blocks:
        stop = start + block.shape[0]
        depths[start:stop] = np.asarray(block.sum(axis=1, dtype=np.float64)).ravel()
        detected[start:stop] = np.diff(block.indptr)
        stored += entries
    if np.any(depths > 2**53 - 1):
        raise ValueError("A count total exceeds the exact integer reporting bound")

    rows = []
    for label, values in (("Total counts per observation", depths),
                          ("Detected features per observation", detected)):
        quartiles = np.quantile(values, [0.25, 0.5, 0.75])
        rows.append([label, *[float(value) for value in quartiles]])
    return {
        "table_title": "Stored counts-layer diagnostics",
        "columns": ["Metric", "25th percentile", "Median", "75th percentile"],
        "rows": rows,
        "source_ref": SOURCE,
        "denominator": n_rows,
        "stored_entries": stored,
        "nonzero_entries": int(detected.sum()),
        "zero_count_observations": int(np.count_nonzero(depths == 0)),
        "truncated": False,
    }
