"""Large family artifacts are allowed only by the exact registered contract."""
from copy import deepcopy
import json

import pytest

from devbench.analysis.method_validation import finite_json, validate_outputs
from devharness_server.method_adapter import dispatcher_contract


@pytest.fixture
def large_result(tmp_path):
    path = tmp_path.resolve() / 'result.json'
    path.write_text(json.dumps({'bounded_artifact': 'x' * 16_000_000}))
    return path


def contrast_outputs(result):
    summary = result.with_name("summary.json")
    summary.write_text(json.dumps({"format": "devbench.scrna-population-summary/1"}))
    return {"result": result, "summary": summary}


def test_registered_singlecell_full_family_can_exceed_legacy_json_limit(large_result):
    contract = dispatcher_contract('scrna_population_contrast').to_dict()
    result = validate_outputs(contract, {'anndata': large_result}, contrast_outputs(large_result))
    assert result['passed'] is True
    assert result['comparator'] == 'json'


def test_generic_json_reader_keeps_legacy_bound(large_result):
    with pytest.raises(ValueError):
        finite_json(large_result)


def test_forged_large_contract_cannot_widen_validation(large_result):
    contract = deepcopy(dispatcher_contract('scrna_population_contrast').to_dict())
    contract['implementation']['source_sha256'] = '0' * 64
    with pytest.raises(ValueError, match="Registered single-cell result contract identity changed"):
        validate_outputs(contract, {'anndata': large_result}, contrast_outputs(large_result))


def test_nonfinite_large_result_remains_rejected(tmp_path):
    path = tmp_path.resolve() / 'result.json'
    path.write_text('{"value":NaN}')
    contract = dispatcher_contract('scrna_population_contrast').to_dict()
    with pytest.raises(ValueError, match="Nonfinite JSON"):
        validate_outputs(contract, {'anndata': path}, contrast_outputs(path))


def test_registered_result_cannot_exceed_its_own_byte_ceiling(tmp_path):
    path = tmp_path.resolve() / 'result.json'
    with path.open('wb') as stream:
        stream.truncate(96_000_001)
    contract = dispatcher_contract('scrna_population_contrast').to_dict()
    with pytest.raises(ValueError):
        validate_outputs(contract, {'anndata': path}, contrast_outputs(path))


def test_registered_contrast_requires_summary_port(large_result):
    contract = dispatcher_contract('scrna_population_contrast').to_dict()
    with pytest.raises(ValueError, match="artifact bindings do not match"):
        validate_outputs(contract, {'anndata': large_result}, {'result': large_result})
