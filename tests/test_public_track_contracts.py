"""Public source metadata/denial contracts; no numerical analysis or acquisition."""
import pytest
from devbench.study.public_tracks import (inspect_developmental_subset, inspect_seqc_cache,
                                         seqc_sample_metadata, ERCC_ID, MIXTURES)


def test_public_readers_require_authorization_before_paths():
    for reader in (inspect_developmental_subset, inspect_seqc_cache):
        with pytest.raises(PermissionError):
            reader(object())


def test_seqc_lanes_share_technical_library_and_never_create_patients():
    a = seqc_sample_metadata("SEQC_ILM_NYG_A_1_L02_ATCACG_AC132FACXX")
    b = seqc_sample_metadata("SEQC_ILM_NYG_A_1_L03_ATCACG_AC132FACXX")
    assert a["library_id"] == b["library_id"]
    assert a["independent_patient_id"] is None
    assert a["replicate_kind"] == "technical"
    assert a["lane"] != b["lane"]


def test_human_ercc_genes_are_not_synthetic_controls():
    assert ERCC_ID.fullmatch("ERCC-00130")
    assert not ERCC_ID.fullmatch("ERCC1")
    assert MIXTURES["C"] == {"A": .75, "B": .25}
    assert MIXTURES["D"] == {"A": .25, "B": .75}
    assert "E" not in MIXTURES and "F" not in MIXTURES
