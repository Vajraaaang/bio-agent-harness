"""Small synthetic sparse expression inputs for retained evidence queries."""
import hashlib
from pathlib import Path

import pytest

from devbench.analysis.episode_evidence_queries import query_result


def test_sparse_csr_counts_bind_axes_without_dense_expansion(tmp_path, monkeypatch):
    import anndata as ad
    import pandas as pd
    from scipy.sparse import csr_matrix

    path = Path(tmp_path).resolve() / "counts.h5ad"
    matrix = csr_matrix([[0, 2], [3, 0]])
    ad.AnnData(matrix, obs=pd.DataFrame({"donor": ["d1", "d2"]}, index=["s1", "s2"]),
               var=pd.DataFrame(index=["a", "b"])).write_h5ad(path)
    def no_dense(*args, **kwargs):
        pytest.fail("CSR queries must not densify the matrix")
    monkeypatch.setattr(csr_matrix, "toarray", no_dense)
    bindings = {"counts_path": str(path), "counts_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "counts_transport": "h5ad_csr", "counts_matrix": "X",
                "gene_ids": ["a", "b"], "sample_ids": ["s1", "s2"], "unit_ids": ["d1", "d2"],
                "unit_id_column": "donor"}
    page = query_result({}, {"mode": "counts", "samples": ["s1"]}, bindings=bindings)
    assert page["status"] == "available"
    assert [row["counts"]["s1"] for row in page["rows"]] == [0, 2]
    with pytest.raises(ValueError, match="axis"):
        query_result({}, {"mode": "counts"}, bindings={**bindings, "unit_ids": ["d2", "d1"]})


def profiles(tmp_path):
    import json

    features = ["a", "b"]
    samples = ["p1", "p2", "p3"]
    units = ["d1", "d1", "d2"]
    data = {"format": "devbench.count-profiles/1", "measurement": "umi_counts", "feature_ids": features,
            "profiles": [
                {"profile_id": sample, "key": {"unit_id": unit}, "status": status, "counts": counts,
                 "library_size": sum(counts) if counts is not None else None}
                for sample, unit, status, counts in zip(samples, units,
                    ["observed", "observed_zero", "absent"], [[2, 1], [0, 0], None], strict=True)]}
    path = Path(tmp_path).resolve() / "profiles.json"
    path.write_text(json.dumps(data))
    return {"counts_transport": "count_profiles", "counts_path": str(path),
            "counts_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "gene_ids": features,
            "sample_ids": samples, "unit_ids": units, "measurement": "umi_counts"}


def test_persisted_pseudobulk_keeps_observed_zero_separate_from_absence(tmp_path):
    bindings = profiles(tmp_path)
    page = query_result({}, {"mode": "counts"}, bindings=bindings)
    assert page["biological_units"] == {"p1": "d1", "p2": "d1", "p3": "d2"}
    assert page["rows"][0]["counts"] == {"p1": 2, "p2": 0, "p3": None}
    donor = query_result({}, {"mode": "counts", "units": ["d1"]}, bindings=bindings)
    assert donor["rows"][0]["counts"] == {"p1": 2, "p2": 0}
    pairs = query_result({}, {"mode": "paired", "pairs": [{"reference": "p2", "comparison": "p3"}]},
                         bindings=bindings)
    assert pairs["rows"][0]["pairs"][0]["difference"] is None


def test_directional_membership_summary_uses_entire_versioned_set():
    from devbench.analysis.episode_evidence_queries import _bounded_digest
    from tests.test_evidence_query_receipts import family

    data = family(46)
    detail = data["families"][0]
    detail["genes"][0].update(status="filtered", effect=None, p=None, q_local=None, q_budget=None)
    detail["genes"][1].update(status="failed", effect=None, p=None, q_local=None, q_budget=None)
    detail["genes"].pop(2)
    result = query_result(data, {"mode": "family_summary", "limit": 1})["directional_summary"]
    assert result["counts"] == {"mapped": 46, "unmapped": 0, "filtered": 1, "tested": 43, "missing": 1, "failed": 1}
    assert result["effects"]["up"]["count"] == 42
    assert result["effects"]["down"]["median"] == -2

    library = {"record_format": "devbench.scientific-gene-set-library/1", "database_id": "synthetic",
               "version": "2026.1", "sets": [{"set_id": "set-A",
                   "feature_ids": ["g000", "g001", "g002", "g003", "g039", "unmapped"]}]}
    bindings = {"gene_set_library": library, "gene_set_library_sha256": _bounded_digest(library)}
    view = query_result(data, {"mode": "gene_set_summary", "gene_set_id": "set-A", "limit": 1},
                        bindings=bindings)
    summary = view["directional_summary"]
    assert summary["membership"]["version"] == "2026.1"
    assert summary["scope"] == "descriptive_membership_and_observed_effects"
    assert summary["counts"] == {"mapped": 5, "unmapped": 1, "filtered": 1, "tested": 2, "missing": 1, "failed": 1}
    assert summary["effects"]["up"]["count"] == summary["effects"]["down"]["count"] == 1


def test_corrupt_sparse_later_selection_and_file_bound(tmp_path, monkeypatch):
    import h5py
    import anndata as ad
    import pandas as pd
    from scipy.sparse import csr_matrix
    from devbench.analysis import episode_evidence_queries as engine

    path = Path(tmp_path).resolve() / "counts.h5ad"
    ad.AnnData(csr_matrix([[0, 2], [3, 0]]),
               obs=pd.DataFrame({"donor": ["d1", "d2"]}, index=["s1", "s2"]),
               var=pd.DataFrame(index=["a", "b"])).write_h5ad(path)
    with h5py.File(path, "r+") as file:
        file["X/data"][1] = -1
    bindings = {"counts_path": str(path), "counts_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "counts_transport": "h5ad_csr", "counts_matrix": "X", "gene_ids": ["a", "b"],
                "sample_ids": ["s1", "s2"], "unit_ids": ["d1", "d2"], "unit_id_column": "donor"}
    assert query_result({}, {"mode": "counts", "samples": ["s1"]}, bindings=bindings)["status"] == "available"
    with pytest.raises(ValueError, match="invalid raw counts"):
        query_result({}, {"mode": "counts", "samples": ["s2"]}, bindings=bindings)
    monkeypatch.setattr(engine, "MAX_INPUT_BYTES", 128)
    with pytest.raises(ValueError, match="budget"):
        query_result({}, {"mode": "counts", "samples": ["s1"]}, bindings=bindings)
