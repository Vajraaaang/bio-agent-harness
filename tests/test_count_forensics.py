"""COUNT-1 clarification gates preserve declared and observed bulk input facts."""

import numpy as np
import pytest

from devbench.analysis.rna_input_contract import validate_bulk_rna_input


def _inspect(matrix, representation="raw_counts", provenance=None):
    genes, samples = np.shape(matrix)
    sample_ids = [f"s{i}" for i in range(samples)]
    return validate_bulk_rna_input(
        {
            "format": "devbench.bulk-rna-input/1",
            "representation": representation,
            "axes": ["gene", "sample"],
            "provenance": provenance or {"source": "audit-declaration"},
            "biological_independence": None,
        },
        gene_ids=[f"g{i}" for i in range(genes)],
        sample_ids=sample_ids,
        metadata_rows=[{"sample_id": sample} for sample in sample_ids],
        matrix=matrix,
    )


@pytest.mark.parametrize("dtype", [np.float64, np.int32])
def test_raw_count_admission_holds_integer_valued_log2_probe(dtype):
    original = np.array([[1, 3, 7, 15], [15, 7, 3, 1]])
    logged = np.log2(1 + original).astype(dtype)
    result = _inspect(logged)
    assert result["count_values_valid"] is False
    assert result["count1_reason"] == "suspected_transformed_integer_counts"
    assert result["nonnegative_integer_values"] is True
    assert result["declared_representation"] == "raw_counts"
    assert result["provenance"] == {"source": "audit-declaration"}
    assert result["representation"] is None
    assert "representation" in result["unknowns"]
    assert _inspect(original)["count_values_valid"] is True


@pytest.mark.parametrize(
    "matrix",
    [
        [[1.0, 2.0], [3.0, 4.0]],  # Too few observations for a repeated plateau.
        [[1, 2, 3, 4], [1, 2, 3, 4]],  # Balanced levels, varied library depths.
        [[1, 3, 7, 15], [15, 7, 3, 1]],  # A genuine discrete upper tail.
        [[1, 1, 2, 4], [3, 1, 1, 1], [1, 3, 2, 1]],  # Skewed low counts.
        [[1024, 961, 1104], [27, 15, 39], [0, 1, 0], [815, 784, 907]],
    ],
)
def test_raw_count_admission_preserves_genuine_bulk_inputs(matrix):
    result = _inspect(matrix)
    assert result["representation"] == "raw_counts"
    assert result["count_values_valid"] is True
    assert "count1_reason" not in result


def test_normalized_and_undeclared_inputs_keep_their_representation():
    logged = np.log2(1 + np.array([[1, 3, 7, 15], [15, 7, 3, 1]]))
    for representation in ("normalized_expression", None):
        result = _inspect(logged, representation)
        assert result["representation"] == representation
        assert "count1_reason" not in result


def test_bulk_review_is_bound_to_values_slot_and_cited_source():
    counts = [[1, 2, 3, 4], [4, 3, 2, 1]]
    digest = _inspect(counts)["measurement_values_sha256"]
    review = {
        "measurement_values_sha256": digest,
        "slot": "feature_rows",
        "evidence": "Original molecule count export, supplementary table 2",
    }
    confirmed = _inspect(counts, provenance={"count1_confirmation": review})
    assert confirmed["count_values_valid"] is True
    assert confirmed["representation"] == "raw_counts"
    assert confirmed["count1_status"] == "caller_confirmed"
    assert confirmed["count1_reason"] == "suspected_transformed_integer_counts"
    assert confirmed["provenance"]["count1_confirmation"] == review
    for change in (
        {"measurement_values_sha256": "a" * 64},
        {"slot": "X"},
        {"evidence": " "},
        {"evidence": "s" * 4001},
    ):
        rejected = _inspect(counts, provenance={"count1_confirmation": review | change})
        assert rejected["count1_status"] == "clarification_required"
        assert rejected["count_values_valid"] is False
    conflict = {"count1_confirmation": review, "transformation": "log2"}
    assert _inspect(counts, provenance=conflict)["count_values_valid"] is False
    for invalid in (-1, 1.5):
        with pytest.raises(ValueError, match="nonnegative integer"):
            _inspect([[invalid]], provenance={"count1_confirmation": review})


def test_count_execution_cannot_admit_suspect_through_integer_storage(tmp_path):
    from devbench.analysis.rna_execution_inputs import prepare_execution_inputs
    from tests.test_rna_execution_inputs import inputs, ports

    matrix = {
        "gene_ids": ["g1", "g2"],
        "sample_ids": ["s1", "s2", "s3", "s4"],
        "matrix": [[1, 2, 3, 4], [4, 3, 2, 1]],
    }
    service, binding, _, _ = inputs(
        tmp_path, matrix=matrix, representation="raw_counts"
    )
    with pytest.raises(ValueError, match="Unknown/unsupported measurement"):
        prepare_execution_inputs(
            binding, ports(), artifact_service=service, output=tmp_path / "bridge"
        )


def test_source_bound_normalization_history_holds_a_wide_integer_matrix():
    values = [[25, 29], [104, 121]]
    digest = _inspect(values)["measurement_values_sha256"]
    confirmation = {
        "slot": "feature_rows",
        "measurement_values_sha256": digest,
        "evidence": "Caller cited a raw-count export.",
    }
    history = {
        "slot": "feature_rows",
        "measurement_values_sha256": digest,
        "representation": "normalized_expression",
        "count1_confirmation": confirmation,
    }
    result = _inspect(values, provenance=history)
    assert result["count_values_valid"] is False
    assert result["count1_reason"] == "conflicting_count_provenance"
    # A processing note about another representation or another matrix does
    # not establish that these bulk values underwent that transformation.
    assert _inspect(values, provenance=history | {"slot": "other"})[
        "count_values_valid"
    ]
    unrelated = history | {"measurement_values_sha256": "a" * 64}
    assert _inspect(values, provenance=unrelated)["count_values_valid"]


def test_sparse_forensics_accounts_for_implicit_zeros_and_block_boundaries():
    from devbench.core.count_forensics import CountDistribution

    data = np.array([1, 2, 3, 4, 4, 3, 2, 1])
    # Identical nonzero values, either fully occupied or scattered over 400 cells.
    for size, expected in ((len(data), True), (400 * 4, False)):
        distribution = CountDistribution(total_values=size, major_width=4)
        for block in (data[:3], data[3:7], data[7:]):
            distribution.observe(block)
        assert distribution.suspected_transform is expected


def test_well_supported_flat_cutoff_is_storage_independent():
    from devbench.core.count_forensics import CountDistribution

    # Sixteen or more repeats of each of four truncated levels. The one zero
    # is explicit in dense storage and implicit in otherwise identical CSR data.
    values = np.tile([1, 2, 3, 4], 20)
    values[-1] = 0
    for stored in (values, values[values != 0]):
        near_dense = CountDistribution(total_values=80, major_width=4)
        low_umi = CountDistribution(total_values=8_000, major_width=4)
        for block in np.array_split(stored, 7):
            near_dense.observe(block)
            low_umi.observe(block)
        assert near_dense.suspected_transform
        assert not low_umi.suspected_transform


def test_small_plateau_compares_every_library_in_either_storage_order():
    from devbench.core.count_forensics import CountDistribution

    cells = np.array([[1, 2, 3, 4]] * 8 + [[1, 1, 2, 2], [3, 3, 4, 4]])
    for storage, libraries_in_rows in ((cells, True), (cells.T, False)):
        distribution = CountDistribution(
            total_values=cells.size,
            major_width=storage.shape[1],
            libraries_in_rows=libraries_in_rows,
        )
        for block in np.array_split(storage.ravel(), 7):
            distribution.observe(block)
        assert not distribution.suspected_transform
