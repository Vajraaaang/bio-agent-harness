"""Offline import and local format smoke for the governed Python image."""

from __future__ import annotations

import importlib
import json
import platform
import sys
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory

PACKAGES = {
    "anndata": "anndata",
    "harmonypy": "harmonypy",
    "igraph": "igraph",
    "leidenalg": "leidenalg",
    "matplotlib": "matplotlib",
    "mudata": "mudata",
    "numpy": "numpy",
    "pandas": "pandas",
    "plotly": "plotly",
    "polars": "polars",
    "pyarrow": "pyarrow",
    "scanpy": "scanpy",
    "scikit-learn": "sklearn",
    "scipy": "scipy",
    "seaborn": "seaborn",
    "spatialdata": "spatialdata",
    "statsmodels": "statsmodels",
    "tiledbsoma": "tiledbsoma",
    "umap-learn": "umap",
}


def verify_formats(root):
    import anndata
    import mudata
    import numpy as np
    import pandas as pd
    import polars as pl
    import pyarrow as pa
    import pyarrow.parquet as pq
    import tiledbsoma as soma
    import zarr
    from scipy.sparse import csr_matrix
    from spatialdata import SpatialData
    from spatialdata.models import PointsModel

    counts = np.array([[1, 0], [3, 2], [2, 1]], dtype=np.int32)
    matrix = anndata.AnnData(csr_matrix(counts))
    matrix.write_h5ad(root / "counts.h5ad")
    backed = anndata.read_h5ad(root / "counts.h5ad", backed="r")
    try:
        np.testing.assert_array_equal(backed.X[:].toarray(), counts)
    finally:
        backed.file.close()

    multimodal = mudata.MuData({"rna": matrix})
    assert multimodal.mod["rna"].n_obs == 3
    frame = pd.DataFrame({"x": [0.0, 1.0, 2.0], "y": [1.0, 3.0, 4.0]})
    points = PointsModel.parse(frame, coordinates={"x": "x", "y": "y"})
    spatial = SpatialData(points={"spots": points})
    assert len(spatial.points["spots"].compute()) == 3

    totals = counts.sum(axis=1).tolist()
    pq.write_table(pa.table({"count": totals}), root / "summary.parquet")
    assert pl.read_parquet(root / "summary.parquet")["count"].to_list() == totals
    for encoding in (2, 3):
        path = root / f"v{encoding}.zarr"
        group = zarr.open_group(store=str(path), mode="w", zarr_format=encoding)
        group.create_array("counts", data=counts, chunks=(1, 2))
        np.testing.assert_array_equal(
            zarr.open_group(str(path), mode="r")["counts"][:], counts
        )

    context = soma.SOMATileDBContext(
        tiledb_config={
            "sm.compute_concurrency_level": "1",
            "sm.io_concurrency_level": "1",
        }
    )
    uri = str(root / "cohort.soma")
    with soma.Collection.create(uri, context=context) as cohort:
        cohort.metadata["observations"] = 3
    with soma.open(uri, context=context) as cohort:
        assert cohort.metadata["observations"] == 3


def main():
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError("Scientific exploration requires Python 3.13")
    for module in PACKAGES.values():
        importlib.import_module(module)
    with TemporaryDirectory() as work:
        verify_formats(Path(work).resolve())
    print(
        json.dumps(
            {
                "architecture": platform.machine(),
                "python": platform.python_version(),
                "versions": {name: version(name) for name in PACKAGES},
                "formats": [
                    "H5AD-CSR",
                    "MuData",
                    "SpatialData",
                    "Parquet",
                    "Zarr-v2",
                    "Zarr-v3",
                    "TileDB-SOMA",
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
