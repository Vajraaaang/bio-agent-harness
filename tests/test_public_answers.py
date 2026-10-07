"""Schema-only public reference scope checks."""
import pytest
from devbench.study.public_answers import DevelopmentalObservation, SEQCNormalization, LibraryFactor


def test_technical_replicates_cannot_be_declared_patients():
    factors = tuple(LibraryFactor(library_id=str(index), factor=1.) for index in range(4))
    with pytest.raises(ValueError):
        SEQCNormalization(factors=factors, method="library totals", independent_patient_count=4)
    assert SEQCNormalization(factors=factors, method="library totals").independent_patient_count == 0


def test_public_developmental_identity_is_not_invented():
    with pytest.raises(ValueError):
        DevelopmentalObservation(stage_labels=("E10", "E11", "E12"), curves=(), independent_animal_count=35)


def test_normalization_cannot_supply_zero_or_nonfinite_factors():
    for invalid in (0., -1., float("inf"), float("nan")):
        with pytest.raises(ValueError):
            LibraryFactor(library_id="library", factor=invalid)
