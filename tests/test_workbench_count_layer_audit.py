"""Technical diagnostics use the stored counts layer and count features once."""

import h5py
import numpy as np
import pytest

from devbench.analysis.workbench_count_layer_audit import audit_count_layer


def test_count_layer_reads_duplicate_coordinates_and_explicit_zeros(tmp_path):
    path = tmp_path.resolve() / "counts.h5ad"
    with h5py.File(path, "w") as handle:
        layer = handle.create_group("layers").create_group("counts")
        layer.attrs["encoding-type"] = "csr_matrix"
        layer.attrs["shape"] = [3, 4]
        layer.create_dataset("indices", data=[1, 2, 1, 3])
        layer.create_dataset("indptr", data=[0, 3, 4, 4])
        layer.create_dataset("data", data=np.array([2.0, 0.0, 3.0, 1.0]))
        observed = {"layers": ["counts"], "cells": 3, "genes": 4}
        audit = audit_count_layer(handle, observed, 100)
        assert audit["rows"] == [
            ["Total counts per observation", 0.5, 1.0, 3.0],
            ["Detected features per observation", 0.5, 1.0, 1.0],
        ]
        assert audit["stored_entries"] == 4 and audit["nonzero_entries"] == 2
        assert audit["zero_count_observations"] == 1
        layer["data"][0] = 0.5
        with pytest.raises(ValueError, match="raw_counts_required"):
            audit_count_layer(handle, observed, 100)
