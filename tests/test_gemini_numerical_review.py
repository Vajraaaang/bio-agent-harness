"""Comparison attribution uses the exact prepared contract identity."""
from copy import deepcopy

from tests.test_rna_numerical_equivalence import _pair, _compare, contract


def test_equal_python_numbers_do_not_substitute_a_different_prepared_contract(tmp_path):
    declared = contract()
    declared["results"][0]["numeric"]["log2FC"]["atol"] = 0
    pair = _pair(tmp_path, declared)
    supplied = deepcopy(declared)
    supplied["results"][0]["numeric"]["log2FC"]["atol"] = 0.0
    assert supplied == declared
    result = _compare(pair, supplied)
    assert result["status"] == "not_comparable"
    assert result["prespecified"] is False


def test_exact_prepared_contract_still_compares(tmp_path):
    declared = contract()
    result = _compare(_pair(tmp_path, declared), declared)
    assert result["status"] == "equivalent" and result["prespecified"] is True
