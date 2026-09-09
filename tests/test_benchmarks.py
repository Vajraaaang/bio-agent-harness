import json
from pathlib import Path

import numpy as np
import pandas as pd


def test_synthetic_reproducibility_and_three_formats(tmp_path):
    from benchmarks.generate_synthetic import generate_synthetic
    from devharness_core.storage import ChunkedExpressionStore
    first = generate_synthetic(tmp_path / "one", seed=42)
    second = generate_synthetic(tmp_path / "two", seed=42)
    expected = pd.read_csv(first["csv"], index_col=0)
    pd.testing.assert_frame_equal(expected, pd.read_csv(second["csv"], index_col=0))
    assert expected.shape == (1000, 12)
    for format in ("csv", "zarr", "h5"):
        with ChunkedExpressionStore(first[format]) as store:
            np.testing.assert_allclose(store.get_gene_vector("Sox2"), expected.loc["Sox2"].to_numpy())
    metadata = pd.read_csv(first["metadata"])
    assert metadata["embryonic_age"].nunique() == 4
    assert metadata["batch"].nunique() == 2
def test_ingestion_timeout_fallback_records_provenance(tmp_path, monkeypatch):
    from benchmarks import ingest_real_data as module
    def fail(*args, **kwargs):
        raise TimeoutError("simulated timeout")
    monkeypatch.setattr(module, "download_processed", fail)
    result = module.ingest_real_data(tmp_path)
    assert result["source"] == "synthetic_fallback"
    assert Path(result["zarr"]).exists()
    provenance = json.loads((tmp_path / "provenance.json").read_text())
    assert "timeout" in provenance["fallback_reason"]
def test_processed_table_conversion_and_metadata_alignment(tmp_path):
    from benchmarks.ingest_real_data import convert_processed
    from devharness_core.storage import ChunkedExpressionStore
    table = tmp_path / "Mouse.RPKM.txt"
    metadata = tmp_path / "metadata.csv"
    pd.DataFrame({"s2": [1.5, 2.5], "s1": [4.1, 7.2]}, index=["g1", "g2"]).to_csv(table, sep="\t")
    pd.DataFrame({"sample_id": ["s1", "s2"], "embryonic_age": ["E8.5", "E10.5"],
                  "organ": ["brain", "brain"], "batch": ["a", "b"]}).to_csv(metadata, index=False)
    result = convert_processed(table, metadata, tmp_path / "out")
    with ChunkedExpressionStore(result["zarr"]) as store:
        assert list(store.sample_names) == ["s2", "s1"]
        np.testing.assert_allclose(store.get_gene_vector("g1"), [1.5, 4.1])
    assert pd.read_csv(result["metadata"])["sample_id"].tolist() == ["s2", "s1"]
def test_no_raw_archives(tmp_path):
    import pytest
    from benchmarks.ingest_real_data import download_processed
    with pytest.raises(ValueError, match="processed"):
        download_processed("https://example.org/raw.fastq.gz", tmp_path / "raw.fastq.gz")
