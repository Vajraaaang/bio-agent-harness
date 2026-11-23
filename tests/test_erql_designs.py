"""Synthetic prefit design tests. No ERQL worker is invoked."""
from dataclasses import replace
from fractions import Fraction
import hashlib

import numpy as np
import pytest

from devbench.core.contrast_design import DesignSpec, compile_contrast_design
from devbench.core.count_profiles import CountBlock, CountComponent, CountObservation, ProfileKey, compile_count_profiles


def profiles(regime, *, missing=None, extra=(), technical=None, exclusions=(), anatomical=False):
    paired = regime in {"B-P", "S-P", "P-P"}
    conditions = ("base", "treated")
    rows = []
    for index in range(3 if paired else 6):
        unit = f"unit-{index}"
        for condition in (("fixed",) if regime == "P-P" else conditions if paired else (conditions[index // 3],)):
            populations = ("A", "B") if regime == "P-P" else ("A",) if regime.startswith("S") else (None,)
            for population in populations:
                specimen = f"{unit}-{condition}-{population}" if anatomical else f"{unit}-{condition}"
                rows.append(ProfileKey(unit, specimen, condition, population))
    keys = tuple(rows) + tuple(extra)
    observations, components, counts = [], [], []
    for index, key in enumerate(keys):
        if key == missing:
            continue
        component_id = str(index)
        observations.append(CountObservation(component_id, key, component_id,
                                             cell_id=component_id if key.population is not None else None))
        components.append(CountComponent(
            component_id, key.specimen_id, component_id, "genes", f"read-ids-{component_id}", 0, 50,
            hashlib.sha256(component_id.encode()).hexdigest(), "umi_counts",
            () if technical is None else tuple(sorted(technical(key).items())),
        ))
        counts.append((index + 1,))
    return compile_count_profiles(("g",), tuple(components), (CountBlock(tuple(observations), np.array(counts)),),
                                  expected_profiles=keys, exclusions=exclusions)


def spec(regime):
    return DesignSpec(
        regime=regime,
        numerator="B" if regime == "P-P" else "treated",
        denominator="A" if regime == "P-P" else "base",
        population="A" if regime.startswith("S") else None,
        condition="fixed" if regime == "P-P" else None,
    )


@pytest.mark.parametrize("regime", ["B-I", "B-P", "S-I", "S-P", "P-P"])
def test_five_regimes_compile_with_exact_row_order_and_reversal(regime):
    counts = profiles(regime)
    forward = compile_contrast_design(counts, spec(regime))
    reverse = compile_contrast_design(counts, replace(spec(regime), numerator=spec(regime).denominator,
                                                     denominator=spec(regime).numerator))
    assert forward.status == reverse.status == "ready"
    assert forward.profile_ids == tuple(row.profile_id for row in counts.profiles)
    assert forward.matrix == reverse.matrix
    assert forward.contrast == tuple(-entry for entry in reverse.contrast)
    assert forward.denominator == spec(regime).denominator
    assert forward.estimable
    assert forward.residual_df == (2 if regime in {"B-P", "S-P", "P-P"} else 4)
    assert forward.pair_count == (3 if regime in {"B-P", "S-P", "P-P"} else None)
    matrix = np.array(forward.matrix)
    effect = np.linalg.lstsq(matrix, np.arange(len(forward.profile_ids)), rcond=None)[0]
    assert np.dot(effect, forward.contrast) == -np.dot(effect, reverse.contrast)
    assert np.dot(effect, forward.contrast) != 0


def test_incomplete_pair_is_held_and_remains_in_bound_cohort():
    missing = ProfileKey("unit-0", "unit-0-treated", "treated", "A")
    counts = profiles("S-P", missing=missing)
    result = compile_contrast_design(counts, spec("S-P"))
    assert result.status == "held"
    assert "incomplete_pair" in result.refusal_codes
    assert result.incomplete_units == ("unit-0",)
    assert result.pair_count == 2
    assert len(result.cohort_profile_ids) == len(result.original_profile_ids) == 6
    assert len(result.profile_ids) == 5


def test_confounded_batch_is_retained_and_refuses_even_if_alias_is_declared():
    counts = profiles("B-I", technical=lambda key: {"batch": key.condition})
    result = compile_contrast_design(counts, replace(spec("B-I"), technical_factors=("batch",),
                                                    candidate_aliases=("batch",)))
    assert result.status == "held"
    assert "contrast_not_estimable" in result.refusal_codes
    assert result.alias_proofs == ()
    assert any(column.factor == "batch" for column in result.columns)


def test_capture_alias_has_reconstructable_nuisance_only_proof():
    counts = profiles("B-P", technical=lambda key: {"capture": key.unit_id})
    result = compile_contrast_design(counts, replace(spec("B-P"), technical_factors=("capture",),
                                                    candidate_aliases=("capture",)))
    assert result.status == "ready"
    assert len(result.alias_proofs) == 2
    for proof in result.alias_proofs:
        recovered = tuple(sum(result.matrix[i][index] * Fraction(numerator, denominator)
                              for index, numerator, denominator in proof.coefficients)
                          for i in range(len(result.matrix)))
        assert recovered == proof.removed_values
        assert all(result.columns[index].role != "target" for index, _, _ in proof.coefficients)


def test_pair_subcohort_keeps_original_inventory_and_withholds_repeated_specimens():
    extra = ProfileKey("unit-0", "third-arm", "other", None)
    repeated = ProfileKey("unit-0", "repeated-specimen", "base", None)
    control = compile_contrast_design(profiles("B-P", extra=(extra,)), spec("B-P"))
    assert control.status == "ready"
    assert len(control.original_profile_ids) == 7
    assert len(control.cohort_profile_ids) == 6
    held = compile_contrast_design(profiles("B-P", extra=(extra, repeated)), spec("B-P"))
    assert held.status == "held"
    assert "repeated_specimens_or_duplicate_profiles" in held.refusal_codes


def test_population_pairs_admit_anatomically_separated_specimens_from_the_same_unit():
    counts = profiles("P-P", anatomical=True)
    result = compile_contrast_design(counts, spec("P-P"))
    assert result.status == "ready"
    assert result.pair_count == 3
    assert result.residual_df == 2
    assert result.profile_ids == tuple(row.profile_id for row in counts.profiles)


def test_population_pairs_still_reject_two_specimens_for_the_same_unit_and_population():
    duplicate = ProfileKey("unit-0", "another-A-specimen", "fixed", "A")
    counts = profiles("P-P", anatomical=True, extra=(duplicate,))
    result = compile_contrast_design(counts, spec("P-P"))
    assert result.status == "held"
    assert "repeated_specimens_or_duplicate_profiles" in result.refusal_codes


def test_exclusion_does_not_silently_change_independent_target_denominator():
    excluded = ProfileKey("unit-0", "unit-0-base", "base", None)
    result = compile_contrast_design(profiles("B-I", exclusions=((excluded, "quality hold"),)), spec("B-I"))
    assert result.status == "held"
    assert "incomplete_target_coverage" in result.refusal_codes
    assert "insufficient_independent_units_per_level" in result.refusal_codes
    assert result.denominator == "base"
    assert len(result.cohort_profile_ids) == 6
    assert len(result.profile_ids) == 5
