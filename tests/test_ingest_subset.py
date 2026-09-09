"""Offline coverage for bounded processed-table ingestion; no model services."""
import hashlib
import io
import json
from http.client import HTTPException
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from benchmarks import ingest_real_data as ingest
from devharness_core.storage import ChunkedExpressionStore


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    monkeypatch.setattr(ingest, "urlopen", lambda *a, **k: pytest.fail("real network is forbidden in this suite"))


class Response(io.BytesIO):
    def __init__(self, data, *, declared=None, url=ingest.PROCESSED_URL):
        super().__init__(data)
        self.url = url
        self.headers = {"Content-Length": str(declared if declared is not None else len(data))}
        self.received = 0
        self.read_sizes = []

    def readline(self, size=-1):
        assert size > 0, "HTTP lines must have an explicit finite bound"
        value = super().readline(size)
        self.received += len(value)
        self.read_sizes.append(size)
        return value


def quoted_table():
    return (b'"brain.P1.1" "brain.E8.5.01" "liver.E10.5.02" "brain.E12.5.03"\n'
            b'"0001" 90 1.5 2.5 3.5\n'
            b'"0002" 80 4.5 5.5 6.5\n'
            b'"unread" broken response tail\n')


def test_http_subset_stops_early_and_records_source_prefix(tmp_path, monkeypatch):
    data = quoted_table()
    response = Response(data, declared=900_000_000)
    monkeypatch.setattr(ingest, "urlopen", lambda *a, **k: response)
    result = ingest.ingest_real_data(tmp_path, subset=True, max_genes=2, max_samples=2)
    consumed = b"".join(data.splitlines(keepends=True)[:3])
    assert response.closed
    assert response.received == len(consumed) < len(data)
    assert result["source_kind"] == "public_processed"
    assert result["shape"] == [2, 2]
    assert result["selected_genes"] == ["0001", "0002"]
    assert result["selected_samples"] == ["brain.E8.5.01", "liver.E10.5.02"]
    assert result["source_sha256"] == hashlib.sha256(consumed).hexdigest()
    assert result["source_sha256_scope"] == "partial_stream"
    assert result["bytes_received"] == len(consumed)
    assert result["bytes_persisted"] > 0
    assert "not representative" in result["sampling_method"]
    with ChunkedExpressionStore(result["zarr"]) as store:
        assert store.gene_names == ["0001", "0002"]
        np.testing.assert_allclose(store.get_gene_vector("0002"), [4.5, 5.5])
    metadata = pd.read_csv(result["metadata"])
    assert metadata.sample_id.tolist() == result["selected_samples"]
    assert metadata.numeric_time.tolist() == [8.5, 10.5]
    assert not list(tmp_path.glob("*.part"))
    assert not list(tmp_path.glob(".ingest-*"))
    assert not (tmp_path / "Mouse.RPKM.txt").exists()


@pytest.mark.parametrize("separator", [",", "\t"])
def test_local_subset_preserves_ids_and_aligns_metadata(tmp_path, monkeypatch, separator):
    table = tmp_path / "source.csv"
    table.write_text(separator.join(["gene", "002", "001", "003"]) + "\n" +
                     separator.join(["0007", "2", "1", "3"]) + "\n" +
                     separator.join(["0008", "5", "4", "6"]) + "\n")
    metadata = tmp_path / "samples.csv"
    metadata.write_text("sample_id,embryonic_age,organ,batch\n001,E10.5,brain,a\n003,P1,brain,a\n002,E8.5,brain,b\n")
    monkeypatch.setattr(ingest, "urlopen", lambda *a, **k: pytest.fail("local input must not fetch"))
    original = table.read_bytes()
    result = ingest.ingest_real_data(tmp_path / "out", subset=True, input_table=table,
                                     metadata_csv=metadata, max_genes=1, max_samples=2)
    assert result["source_kind"] == "public_processed"
    assert result["bytes_received"] == 0
    assert result["bytes_read"] < len(original)
    assert result["selected_samples"] == ["002", "001"]
    assert result["selected_genes"] == ["0007"]
    assert table.read_bytes() == original
    assert pd.read_csv(result["metadata"], dtype={"sample_id": str}).sample_id.tolist() == ["002", "001"]


@pytest.mark.parametrize("options", [
    {"max_download_mb": 201}, {"max_download_mb": 0}, {"max_download_mb": float("nan")},
    {"max_download_mb": float("inf")}, {"timeout_sec": float("nan")},
    {"timeout_sec": 0}, {"max_genes": 0}, {"max_samples": 0}, {"max_genes": 1.5},
])
def test_invalid_limits_reject_before_network_or_fallback(tmp_path, monkeypatch, options):
    monkeypatch.setattr(ingest, "urlopen", lambda *a, **k: pytest.fail("invalid limits must not fetch"))
    with pytest.raises(ValueError):
        ingest.ingest_real_data(tmp_path / "out", subset=True, **options)
    assert not (tmp_path / "out").exists()


def test_offline_fallback_is_explicit_and_honors_subset_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "urlopen", lambda *a, **k: pytest.fail("offline must not fetch"))
    result = ingest.ingest_real_data(tmp_path, subset=True, offline=True, max_genes=3, max_samples=4)
    assert result["source"] == "synthetic_fallback"
    assert result["source_kind"] == "synthetic"
    assert result["shape"] == [3, 4]
    assert result["bytes_received"] == 0
    assert "offline" in result["fallback_reason"].lower()
    assert result["requested_limits"]["max_genes"] == 3
    assert result["source_sha256"] is None
    assert json.loads((tmp_path / "provenance.json").read_text())["shape"] == [3, 4]


def test_network_timeout_removes_partial_subset_and_keeps_reason(tmp_path, monkeypatch):
    class TimedResponse(Response):
        def readline(self, size=-1):
            if self.received:
                raise TimeoutError("simulated body stall")
            return super().readline(size)
    response = TimedResponse(quoted_table())
    monkeypatch.setattr(ingest, "urlopen", lambda *a, **k: response)
    result = ingest.ingest_real_data(tmp_path, subset=True, max_genes=2, max_samples=2)
    assert result["source_kind"] == "synthetic"
    assert "simulated body stall" in result["fallback_reason"]
    assert result["bytes_received"] == response.received > 0
    assert result["shape"] == [2, 2]
    assert response.closed
    assert not (tmp_path / "processed_subset.tsv").exists()
    assert not list(tmp_path.glob(".ingest-*"))


def test_http_byte_cap_is_never_exceeded(tmp_path, monkeypatch):
    response = Response(b'x' * 10_000)
    monkeypatch.setattr(ingest, "urlopen", lambda *a, **k: response)
    # Too small even for fallback artifacts: error is explicit and leaves no output data.
    with pytest.raises(ValueError, match="budget"):
        ingest.ingest_real_data(tmp_path, subset=True, max_download_mb=.0001)
    assert response.received <= 100
    assert not list(tmp_path.glob(".ingest-*"))
    assert not (tmp_path / "processed_counts.zarr").exists()


@pytest.mark.parametrize("header", ['gene,one,one\n', 'gene,,two\n', 'gene," ",two\n'])
def test_bad_sample_ids_are_rejected_without_pandas_mangling(tmp_path, header):
    table = tmp_path / "bad.csv"
    table.write_text(header + "g1,1,2\n")
    with pytest.raises(ValueError, match="sample"):
        ingest.convert_processed(table, None, tmp_path / "out")
    assert not (tmp_path / "out" / "processed_counts.zarr").exists()


def test_failed_full_conversion_does_not_leave_partial_store(tmp_path):
    table = tmp_path / "bad.csv"
    table.write_text("gene,brain.E8.5.1\ng1,1\ng2,nan\n")
    with pytest.raises(ValueError, match="finite"):
        ingest.convert_processed(table, None, tmp_path / "out")
    assert not (tmp_path / "out" / "processed_counts.zarr").exists()


def test_existing_store_and_provenance_survive_failed_ingest(tmp_path):
    store = tmp_path / "processed_counts.zarr"
    store.mkdir()
    sentinel = store / "keep"
    sentinel.write_text("good store")
    provenance = tmp_path / "provenance.json"
    provenance.write_text('{"original": true}')
    with pytest.raises(FileExistsError):
        ingest.ingest_real_data(tmp_path, subset=True, offline=True)
    assert sentinel.read_text() == "good store"
    assert provenance.read_text() == '{"original": true}'


@pytest.mark.parametrize("url", ["https://example.org/x.fastq.gz", "https://example.org/x.bam.txt",
                                "https://example.org/x.fq.csv", "http://example.org/x.tsv"])
def test_subset_rejects_raw_and_insecure_urls_before_network(tmp_path, monkeypatch, url):
    monkeypatch.setattr(ingest, "urlopen", lambda *a, **k: pytest.fail("must not fetch raw"))
    with pytest.raises(ValueError, match="processed"):
        ingest.ingest_real_data(tmp_path, subset=True, url=url)


def test_truncated_http_body_falls_back_instead_of_claiming_a_complete_source(tmp_path, monkeypatch):
    data = b'"brain.E8.5.1"\n"g1" 1\n'
    response = Response(data, declared=len(data) + 100)
    monkeypatch.setattr(ingest, "urlopen", lambda *a, **k: response)
    result = ingest.ingest_real_data(tmp_path, subset=True, max_genes=2)
    assert result["source_kind"] == "synthetic"
    assert "Incomplete" in result["fallback_reason"]
    assert result["bytes_received"] == len(data)


def test_full_download_failure_retains_received_bytes(tmp_path, monkeypatch):
    class TruncatedResponse(Response):
        def read(self, size=-1):
            return super().read(size)
    data = b'"brain.E8.5.1"\n"g1" 1\n'
    response = TruncatedResponse(data, declared=len(data) + 100)
    monkeypatch.setattr(ingest, "urlopen", lambda *a, **k: response)
    result = ingest.ingest_real_data(tmp_path)
    assert result["source_kind"] == "synthetic"
    assert result["bytes_received"] == len(data)
    assert result["attempted_source_sha256"] == hashlib.sha256(data).hexdigest()
    assert not (tmp_path / "Mouse.RPKM.txt").exists()


@pytest.mark.parametrize("limit", [200.00000001, 1e308])
def test_fractional_and_overflowing_budget_over_cap_rejected(tmp_path, limit):
    with pytest.raises(ValueError):
        ingest.ingest_real_data(tmp_path / "out", subset=True, max_download_mb=limit)
    assert not (tmp_path / "out").exists()


def test_oversized_header_is_bounded_and_falls_back(tmp_path, monkeypatch):
    response = Response(quoted_table())
    monkeypatch.setattr(ingest, "MAX_HEADER_BYTES", 20)
    monkeypatch.setattr(ingest, "urlopen", lambda *a, **k: response)
    result = ingest.ingest_real_data(tmp_path, subset=True, max_genes=2)
    assert result["source_kind"] == "synthetic"
    assert "line budget" in result["fallback_reason"]
    assert result["bytes_received"] == 21
    assert not (tmp_path / "processed_subset.tsv").exists()


def test_empty_gene_is_rejected_and_not_published(tmp_path):
    table = tmp_path / "empty_gene.tsv"
    table.write_text('gene_id\tbrain.E8.5.1\n""\t1\n')
    with pytest.raises(ValueError, match="gene"):
        ingest.convert_processed(table, None, tmp_path / "out")
    assert not (tmp_path / "out" / "processed_counts.zarr").exists()


def test_http_protocol_error_uses_explicit_fallback(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise HTTPException("malformed server response")
    monkeypatch.setattr(ingest, "urlopen", fail)
    result = ingest.ingest_real_data(tmp_path, subset=True)
    assert result["source_kind"] == "synthetic"
    assert "malformed server response" in result["fallback_reason"]


def test_raw_redirect_is_closed_without_reading_body(tmp_path, monkeypatch):
    response = Response(quoted_table(), url="https://example.org/source.bam.txt")
    monkeypatch.setattr(ingest, "urlopen", lambda *a, **k: response)
    result = ingest.ingest_real_data(tmp_path, subset=True)
    assert result["source_kind"] == "synthetic"
    assert response.closed and response.received == 0
