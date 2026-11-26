"""Bounded AnnData headers and ordered axis digests for modern and legacy inputs."""

import hashlib
import tempfile

import h5py
import numpy as np
import pytest

from devbench.analysis.workbench_inputs import _h5ad_header, _h5ad_sparse_layout
from devbench.platform import plugin_h5ad_axes as axes
from rna_kernels.canonical import canonical_json


def test_legacy_compound_headers_are_metadata_only(workspace_tmp_path, monkeypatch):
    path = workspace_tmp_path / "legacy.h5ad"
    with h5py.File(path, "w") as handle:
        x = handle.create_group("X")
        x.attrs["h5sparse_shape"] = (24_673, 15_706)
        x.attrs["h5sparse_format"] = "csc"
        handle.create_dataset(
            "obs", (24_673,), dtype=[("barcode", "S16"), ("replicate", "i1")]
        )
        handle.create_dataset(
            "var", (15_706,), dtype=[("index", "S16"), ("name", "S32")]
        )
        handle.create_dataset("obsm", (24_673,), dtype=[("X_pca", "f8", (30,))])

        def forbidden(*args, **kwargs):
            pytest.fail("Header inspection read an expression or annotation value")

        monkeypatch.setattr(h5py.Dataset, "__getitem__", forbidden)
        header = _h5ad_header(handle, 128)
        assert header["cells"] == 24_673 and header["genes"] == 15_706
        assert header["obs_columns"] == ["replicate"]
        assert header["var_columns"] == ["name"]
        assert header["obsm_keys"] == ["X_pca"]
        assert header["annotation_values_read"] is False
        with pytest.raises(ValueError, match="bounds"):
            _h5ad_header(handle, 0)


@pytest.mark.parametrize(
    ("attribute", "value"),
    [("h5sparse_shape", [20, 10]), ("h5sparse_format", np.bytes_("csr"))],
)
def test_conflicting_sparse_header_encodings_fail_closed(
    workspace_tmp_path, attribute, value
):
    with h5py.File(workspace_tmp_path / "conflicting.h5ad", "w") as handle:
        x = handle.create_group("X")
        x.attrs["shape"] = (10, 20)
        x.attrs["h5sparse_shape"] = (10, 20)
        x.attrs["encoding-type"] = "csc_matrix"
        x.attrs["h5sparse_format"] = np.bytes_("csc")
        assert _h5ad_sparse_layout(x) == ("csc", (10, 20))
        x.attrs[attribute] = value
        with pytest.raises(ValueError, match="conflict"):
            _h5ad_sparse_layout(x)


def test_large_index_preserves_digest_and_rejects_cross_block_duplicates(
    workspace_tmp_path, monkeypatch
):
    labels = [f"cell-{i:06d}" for i in range(200_001)]
    monkeypatch.setattr(tempfile, "tempdir", str(workspace_tmp_path))
    with h5py.File(workspace_tmp_path / "large.h5ad", "w") as handle:
        obs = handle.create_group("obs")
        index = obs.create_dataset("_index", data=np.asarray(labels, dtype="S11"))
        original = h5py.Dataset.__getitem__

        def bounded(self, key):
            assert isinstance(key, slice) and 0 < key.stop - key.start <= 256
            return original(self, key)

        monkeypatch.setattr(h5py.Dataset, "__getitem__", bounded)
        assert axes.h5ad_index_digest(obs, len(labels), lambda _: None) == (
            hashlib.sha256(canonical_json(labels)).hexdigest()
        )
        index[-1] = labels[0]
        with pytest.raises(ValueError, match="not unique"):
            axes.h5ad_index_digest(obs, len(labels), lambda _: None)
        assert list(workspace_tmp_path.iterdir()) == [workspace_tmp_path / "large.h5ad"]


def test_categorical_index_decodes_before_hashing_and_checks_invalid_codes(
    workspace_tmp_path, monkeypatch
):
    labels = ['é"\\', "β", "tab\there", "gene"]
    monkeypatch.setattr(axes, "_MEMORY_LABELS", 2)
    with h5py.File(workspace_tmp_path / "categorical.h5ad", "w") as handle:
        index = handle.create_group("var").create_group("_index")
        index.attrs["encoding-type"] = "categorical"
        index.create_dataset("categories", data=labels, dtype=h5py.string_dtype())
        codes = index.create_dataset("codes", data=[3, 0, 2, 1])
        assert axes.h5ad_index_digest(handle["var"], 4, lambda _: None) == (
            hashlib.sha256(
                canonical_json([labels[i] for i in (3, 0, 2, 1)])
            ).hexdigest()
        )
        codes[-1] = -1
        with pytest.raises(ValueError, match="invalid labels"):
            axes.h5ad_index_digest(handle["var"], 4, lambda _: None)


def test_anndata_categorical_index_inspects_headers_then_hashes(
    workspace_tmp_path, monkeypatch
):
    import anndata as ad
    import pandas as pd

    labels = ["cell-b", "cell-c", "cell-a"]
    path = workspace_tmp_path / "categorical-index.h5ad"
    obs = pd.DataFrame(index=pd.CategoricalIndex(labels, name="barcode"))
    ad.AnnData(np.ones((3, 2)), obs=obs).write_h5ad(path)
    with h5py.File(path, "r") as handle:
        assert isinstance(handle["obs/barcode"], h5py.Group)
        with monkeypatch.context() as header_only:

            def forbid(*args, **kwargs):
                pytest.fail(
                    "Categorical header inspection must not read dataset values"
                )

            header_only.setattr(h5py.Dataset, "__getitem__", forbid)
            header = _h5ad_header(handle, 128)
        assert (header["cells"], header["genes"]) == (3, 2)
        assert header["obs_columns"] == []
        assert axes.h5ad_index_digest(handle["obs"], 3, lambda _: None) == (
            hashlib.sha256(canonical_json(labels)).hexdigest()
        )


@pytest.mark.parametrize(
    ("key", "data", "message"),
    [
        ("codes", [0.1, 1.0], "integer"),
        ("codes", [[0, 1]], "integer"),
        ("codes", [0, 1, 0], "disagree"),
        ("categories", [[b"a", b"b"]], "text"),
        ("categories", [1, 2], "text"),
        ("categories", h5py.SoftLink("/unbound"), "links"),
        ("codes", h5py.ExternalLink("outside.h5", "unsafe"), "links"),
    ],
)
def test_categorical_index_rejects_invalid_storage_headers(
    workspace_tmp_path, key, data, message
):
    with h5py.File(workspace_tmp_path / "invalid-categorical.h5ad", "w") as handle:
        handle.create_dataset("X", shape=(2, 1), dtype="i4")
        handle.create_group("var").create_dataset("_index", data=[b"gene"])
        idx = handle.create_group("obs").create_group("_index")
        idx.attrs["encoding-type"] = "categorical"
        idx["codes"] = data if key == "codes" else [0, 1]
        idx["categories"] = data if key == "categories" else [b"a", b"b"]
        with pytest.raises(ValueError, match=message):
            _h5ad_header(handle, 128)


def test_legacy_record_indexes_and_annotations_decode_in_row_order(workspace_tmp_path):
    with h5py.File(workspace_tmp_path / "record.h5ad", "w") as handle:
        obs = handle.create_dataset(
            "obs",
            data=np.array(
                [(b"c2", 1, 0), (b"c1", 0, 1)],
                dtype=[("index", "S2"), ("replicate", "i1"), ("label", "i1")],
            ),
        )
        handle.create_group("uns").create_dataset(
            "replicate_categories", data=[b"a", b"b"]
        )
        assert axes.h5ad_index_digest(obs, 2, lambda _: None) == (
            hashlib.sha256(canonical_json(["c2", "c1"])).hexdigest()
        )
        for field, expected in (("replicate", ("b", "a")), ("label", ("0", "1"))):
            assert list(
                axes.h5ad_label_blocks(obs, field, 2, lambda _: None, annotation=True)
            ) == [expected]


def test_variable_width_labels_enforce_decoded_length(workspace_tmp_path):
    allowed = "🧬" * 200
    with h5py.File(workspace_tmp_path / "unicode.h5ad", "w") as handle:
        obs = handle.create_group("obs")
        index = obs.create_dataset("_index", data=[allowed], dtype=h5py.string_dtype())
        assert axes.h5ad_index_digest(obs, 1, lambda _: None) == (
            hashlib.sha256(canonical_json([allowed])).hexdigest()
        )
        index[0] = allowed * 100
        with pytest.raises(ValueError, match="supported size"):
            axes.h5ad_index_digest(obs, 1, lambda _: None)
