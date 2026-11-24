"""Read workspace data without copying an expression matrix into pandas."""
from pathlib import Path
from devharness_core.units import BIOLOGICAL_UNIT_COLUMNS
from typing import Union

import pandas as pd

from devharness_core.storage import ChunkedExpressionStore
from ._runtime import bounded_metadata_path, sdk_call, workspace_path

PathLike = Union[str, Path]


@sdk_call
def open_expression(path: PathLike) -> ChunkedExpressionStore:
    """Open a read-only Zarr/HDF5/CSV store. Use a context manager to close it.

    Read selected genes with ``get_gene_vector`` or iterate bounded blocks with
    ``iter_gene_chunks``. This call records opening the store; subsequent store
    reads use its existing API and do not generate separate SDK receipts.
    """
    return ChunkedExpressionStore(workspace_path(path))


@sdk_call
def read_metadata(path: PathLike) -> pd.DataFrame:
    """Read at most 20 MB and 100,000 metadata rows, preserving biological IDs."""
    source = bounded_metadata_path(path)
    frame = pd.read_csv(source, nrows=100001,
                        dtype={name: str for name in ("sample_id", *BIOLOGICAL_UNIT_COLUMNS)})
    if len(frame) > 100000:
        raise ValueError("Metadata exceeds the 100,000-row SDK limit")
    return frame
