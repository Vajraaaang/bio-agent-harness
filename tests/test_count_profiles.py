"""Synthetic prefit count-profile tests."""
from dataclasses import asdict, replace
import hashlib
import json

import numpy as np
import pytest
from scipy import sparse

from devbench.core.count_profiles import (
    CountBlock,
    CountComponent,
    CountObservation,
    ProfileKey,
    compile_count_profiles,
)


def component(name="lane-a", start=0, stop=20, *, batch="a"):
    return CountComponent(
        component_id=name,
        material_id="material-1",
        library_id="library-1",
        reference="feature-reference",
        partition_axis="flowcell-clusters",
        partition_start=start,
        partition_stop=stop,
        contribution_sha256={"lane-a": "1", "lane-b": "2"}[name] * 64,
        measurement="umi_counts",
        technical_factors=(("batch", batch),),
    )


def test_exact_dense_csr_order_invariance_and_coverage():
    keep = ProfileKey("donor-1", "specimen-1", "treated", "A")
    zero = replace(keep, population="B")
    absent = replace(keep, population="C")
    excluded = replace(keep, population="D")
    observations = (
        CountObservation("cell-1-a", keep, "lane-a", cell_id="cell-1"),
        CountObservation("cell-1-b", keep, "lane-b", cell_id="cell-1"),
        CountObservation("cell-3", zero, "lane-a", cell_id="cell-3"),
        CountObservation("cell-4", excluded, "lane-b", cell_id="cell-4"),
    )
    # Two lane contributions are separately identified and do not overlap.
    components = (component(), component("lane-b", 20, 40))
    counts = np.array([[2**53 - 3, 0], [1, 1], [0, 0], [3, 7]], dtype=np.int64)
    kwargs = {"expected_profiles": (absent, excluded, keep, zero),
              "exclusions": ((excluded, "retained quality exclusion"),)}
    dense = compile_count_profiles(("g1", "g2"), components, (CountBlock(observations, counts),), **kwargs)
    compressed = compile_count_profiles(
        ("g1", "g2"), components[::-1],
        (CountBlock(observations[2:][::-1], sparse.csr_matrix(counts[2:][::-1])),
         CountBlock(observations[:2][::-1], sparse.csr_matrix(counts[:2][::-1]))),
        **kwargs,
    )
    assert compressed == dense
    assert dense.measurement == "umi_counts"
    records = {record.key: record for record in dense.profiles}
    assert records[keep].counts == (2**53 - 2, 1)
    assert records[keep].library_size == 2**53 - 1
    assert records[keep].observation_count == 2
    assert records[keep].cell_count == 1
    assert records[keep].component_ids == ("lane-a", "lane-b")
    assert records[zero].status == "observed_zero"
    assert records[zero].counts == (0, 0)
    assert records[absent].status == "absent"
    assert records[absent].counts is records[absent].library_size is None
    assert records[excluded].status == "excluded"
    assert records[excluded].counts is None
    assert records[excluded].observation_count == 1
    assert records[excluded].exclusion_reason == "retained quality exclusion"


@pytest.mark.parametrize(("excluded_library_size", "accepted"), [
    (10, True), (None, True), (-1, False), (2**53, False), (True, False), (10.0, False),
])
def test_persisted_excluded_profile_retains_audit_size_without_admitting_counts(
        tmp_path, excluded_library_size, accepted):
    from devbench.core.population_data import query_population_counts

    key = ProfileKey("donor-1", "specimen-1", "treated", None)
    observation = CountObservation("observation-1", key, "lane-a")
    compiled = compile_count_profiles(("g1", "g2"), (component(),),
        (CountBlock((observation,), np.array([[3, 7]], dtype=np.int64)),),
        expected_profiles=(key,), exclusions=((key, "quality hold"),))
    profile, = compiled.profiles
    assert profile.counts is None and profile.library_size == 10
    saved = asdict(compiled)
    saved["profiles"][0]["library_size"] = excluded_library_size
    path = tmp_path.resolve() / "profiles.json"
    path.write_text(json.dumps(saved))
    artifact = path.read_bytes()
    bindings = {"counts_transport": "count_profiles", "counts_path": str(path),
                "counts_sha256": hashlib.sha256(artifact).hexdigest(), "gene_ids": ["g1", "g2"],
                "sample_ids": [profile.profile_id], "unit_ids": [key.unit_id]}

    if accepted:
        counts, totals, _, _ = query_population_counts(bindings, {"g1"}, {profile.profile_id}, 65_536)
        assert counts == {"g1": {profile.profile_id: None}}
        assert totals == {profile.profile_id: None}
        assert path.read_bytes() == artifact
    else:
        with pytest.raises(ValueError, match="status and count data disagree"):
            query_population_counts(bindings, {"g1"}, {profile.profile_id}, 65_536)


@pytest.mark.parametrize(("change", "message"), [
    ({"partition_start": 19}, "overlapping technical contributions"),
    ({"contribution_sha256": "1" * 64}, "duplicate contribution"),
    ({"technical_factors": (("batch", "different"),)}, "incompatible technical contributions"),
    ({"material_id": "different"}, "incompatible technical contributions"),
    ({"library_id": "different"}, "incompatible technical contributions"),
    ({"reference": "different"}, "incompatible technical contributions"),
    ({"partition_axis": "different"}, "incompatible technical contributions"),
    ({"measurement": "non_umi_counts"}, "incompatible technical contributions"),
])
def test_rejects_unproved_technical_merge(change, message):
    key = ProfileKey("donor-1", "specimen-1", "treated", None)
    observations = (CountObservation("a", key, "lane-a"), CountObservation("b", key, "lane-b"))
    with pytest.raises(ValueError, match=message):
        compile_count_profiles(
            ("g",), (component(), replace(component("lane-b", 20, 40), **change)),
            (CountBlock(observations, np.ones((2, 1), dtype=np.int64)),), expected_profiles=(key,),
        )


def test_rejects_overlapping_source_partitions_across_profile_and_library_ids():
    first = ProfileKey("donor-1", "specimen-1", "treated", None)
    second = ProfileKey("donor-2", "specimen-2", "treated", None)
    other = replace(component("lane-b"), material_id="material-2", library_id="library-2")
    observations = (CountObservation("a", first, "lane-a"), CountObservation("b", second, "lane-b"))
    with pytest.raises(ValueError, match="overlapping technical contributions"):
        compile_count_profiles(("g",), (component(), other),
            (CountBlock(observations, np.ones((2, 1), dtype=np.int64)),),
            expected_profiles=(first, second))


@pytest.mark.parametrize(("values", "message"), [
    (np.array([[1.1], [1.0]]), "finite nonnegative integers"),
    (np.array([[2**53 - 1], [1]], dtype=np.int64), "aggregate exceeds"),
    (np.array([[1], [2**63]], dtype=np.uint64), "finite nonnegative integers"),
    (sparse.csr_matrix(([1, 1], [0, 0], [0, 2, 2]), shape=(2, 1)), "canonical CSR"),
    (sparse.csc_matrix(np.ones((2, 1))), "dense array or CSR"),
])
def test_rejects_nonexact_or_unsupported_blocks(values, message):
    key = ProfileKey("donor-1", "specimen-1", "treated", None)
    observations = (CountObservation("a", key, "lane-a"), CountObservation("b", key, "lane-a"))
    with pytest.raises(ValueError, match=message):
        compile_count_profiles(("g",), (component(),), (CountBlock(observations, values),), expected_profiles=(key,))


def test_rejects_duplicate_observations_and_undeclared_coverage():
    key = ProfileKey("donor-1", "specimen-1", "treated", None)
    observation = CountObservation("a", key, "lane-a")
    block = CountBlock((observation,), np.array([[1]]))
    with pytest.raises(ValueError, match="duplicate observation"):
        compile_count_profiles(("g",), (component(),), (block, block), expected_profiles=(key,))
    with pytest.raises(ValueError, match="outside the original coverage"):
        compile_count_profiles(("g",), (component(),), (block,), expected_profiles=(replace(key, condition="control"),))


def test_cell_identity_is_required_and_cannot_change_population_across_lanes():
    keep = ProfileKey("donor-1", "specimen-1", "treated", "A")
    other = replace(keep, population="B")
    components = (component(), component("lane-b", 20, 40))
    observations = (
        CountObservation("a", keep, "lane-a", cell_id="cell"),
        CountObservation("b", other, "lane-b", cell_id="cell"),
    )
    kwargs = {"expected_profiles": (keep, other)}
    counts = np.ones((2, 1), dtype=np.int64)
    with pytest.raises(ValueError, match="cell identity crosses profiles"):
        compile_count_profiles(("g",), components, (CountBlock(observations, counts),), **kwargs)
    observations = (replace(observations[0], cell_id=None), observations[1])
    with pytest.raises(ValueError, match="explicit cell IDs"):
        compile_count_profiles(("g",), components, (CountBlock(observations, counts),), **kwargs)


def test_rejects_unknown_biological_identity():
    key = ProfileKey("unknown", "specimen", "treated", None)
    with pytest.raises(ValueError, match="declared biological unit"):
        compile_count_profiles(("g",), (), (), expected_profiles=(key,))


def test_read_and_umi_counts_cannot_share_one_profile_panel():
    treated = ProfileKey("donor-1", "specimen-1", "treated", None)
    control = ProfileKey("donor-2", "specimen-2", "control", None)
    other = replace(component("lane-b", 20, 40), material_id="material-2",
                    library_id="library-2", measurement="non_umi_counts")
    observations = (CountObservation("a", treated, "lane-a"), CountObservation("b", control, "lane-b"))
    block = CountBlock(observations, np.ones((2, 1), dtype=np.int64))
    with pytest.raises(ValueError, match="one measurement representation"):
        compile_count_profiles(("g",), (component(), other), (block,), expected_profiles=(treated, control))
