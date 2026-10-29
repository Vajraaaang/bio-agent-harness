"""Synthetic statistical contract review; no estimator or scientific qualification.

The frozen presentation decision is strictly value < threshold.  Result-row
status describes the published test outcome: an outlier-suppressed test has no
p/q, while a successful refit retains its actual estimates as an available row.
"""
import copy
import json

import pytest

from devbench.analysis.scientific_result_contract import (
    ResultRow, ScientificResult, multiplicity_check,
)
from tests.test_scientific_result_contract import (
    api, judgment, packet_record, result_record,
)


def typed_result(record):
    return ScientificResult.model_validate_json(json.dumps(record, allow_nan=False))


@pytest.mark.parametrize('metric', ['p_value', 'adjusted_p_value'])
@pytest.mark.parametrize('actual,claimed', [(.050000000001, .05), (.05, .050000000001)])
def test_f10_strict_boundary_equality_and_above_share_the_nonrejecting_side(tmp_path, metric, actual, claimed):
    result = result_record()
    result['rows'][0].update(p_value=actual / 2 if metric == 'adjusted_p_value' else actual,
        adjusted_p_value=actual if metric == 'adjusted_p_value' else actual * 2)
    data = packet_record(tmp_path, result)
    data['presentation_rules'][1].update(pointer='/rows/0/' + metric)
    packet = api().parse_claim_packet(data)
    assert api().validate_judgment(packet,
        judgment(packet, ({'rule_id': 'probability', 'value': claimed},)))['scientific_judgment'] == 'supported'


@pytest.mark.parametrize('raw_p,captured_q', [(.0250000000005, .05), (.025, .050000000001)])
def test_f10_bh_equality_and_above_do_not_flip_a_strict_decision(tmp_path, raw_p, captured_q):
    result = result_record()
    result['rows'][0].update(p_value=raw_p, adjusted_p_value=captured_q)
    data = packet_record(tmp_path, result)
    data['presentation_rules'][1].update(pointer='/rows/0/adjusted_p_value')
    packet = api().parse_claim_packet(data)
    assert api().verified_artifacts(packet)['result']['rows'][0]['adjusted_p_value'] == captured_q


@pytest.mark.parametrize('actual,claimed', [(.049999999999, .05), (.05, .049999999999)])
def test_f10_genuine_strict_presentation_decision_crossing_still_rejected(tmp_path, actual, claimed):
    result = result_record()
    result['rows'][0].update(p_value=actual, adjusted_p_value=actual * 2)
    packet = api().parse_claim_packet(packet_record(tmp_path, result))
    with pytest.raises(ValueError, match='Unsupported presentation assertion'):
        api().validate_judgment(packet, judgment(packet, ({'rule_id': 'probability', 'value': claimed},)))


@pytest.mark.parametrize('raw_p,captured_q', [(.025, .049999999999), (.0249999999995, .05)])
def test_f10_genuine_strict_recomputed_bh_decision_crossing_still_rejected(tmp_path, raw_p, captured_q):
    result = result_record()
    result['rows'][0].update(p_value=raw_p, adjusted_p_value=captured_q)
    data = packet_record(tmp_path, result)
    data['presentation_rules'][1].update(pointer='/rows/0/adjusted_p_value')
    packet = api().parse_claim_packet(data)
    with pytest.raises(ValueError, match='decision boundary'):
        api().verified_artifacts(packet)


@pytest.mark.parametrize('present', [('p_value',), ('adjusted_p_value',), ('p_value', 'adjusted_p_value')])
def test_f11_outlier_suppressed_test_cannot_substitute_numeric_probabilities(present):
    row = copy.deepcopy(result_record()['rows'][0])
    row.update(status='outlier', p_value=None, adjusted_p_value=None,
        missing_reasons={'p_value': 'outlier_test_suppressed', 'adjusted_p_value': 'outlier_test_suppressed'})
    for metric in present:
        row[metric] = 1.
        row['missing_reasons'].pop(metric)
    with pytest.raises(ValueError, match='Outlier-suppressed'):
        ResultRow.model_validate(row)


def test_f11_suppressed_outlier_preserves_effect_se_and_denominator_without_fabricated_p():
    result = result_record()
    result['family']['eligible_ids'].append('g3')
    result['rows'][0]['adjusted_p_value'] = 3e-50
    result['rows'][1]['adjusted_p_value'] = .6
    result['rows'].append({'feature_id': 'g3', 'status': 'outlier', 'effect': 1.5,
        'standard_error': .75, 'p_value': None, 'adjusted_p_value': None,
        'missing_reasons': {'p_value': 'cooks_distance_test_suppressed',
            'adjusted_p_value': 'cooks_distance_test_suppressed'}})
    typed = typed_result(result)
    assert multiplicity_check(typed)['status'] == 'verified'
    assert typed.rows[2].effect == 1.5 and typed.rows[2].standard_error == .75
    assert typed.rows[2].p_value is None and typed.rows[2].adjusted_p_value is None


@pytest.mark.parametrize('provenance', [
    'DESeq2 replacement-count refit after Cook outlier detection; upstream diagnostic retained',
    'DESeq2 cooksCutoff=FALSE; original-count outlier diagnostic retained',
])
@pytest.mark.parametrize('status', ['ok', 'partially_available'])
def test_f11_successful_refit_or_unfiltered_test_retains_finite_statistics_and_source(tmp_path, provenance, status):
    result = result_record()
    result['method']['test_effect_source'] = provenance
    if status == 'partially_available':
        result['rows'][0].update(status=status, standard_error=None,
            missing_reasons={'standard_error': 'not_reported_by_method'})
    original = copy.deepcopy(result)
    packet = api().parse_claim_packet(packet_record(tmp_path, result))
    assert api().verified_artifacts(packet)['result'] == original
    assert typed_result(result).rows[0].status == status


def test_f11_suppressed_outlier_may_also_lack_effect_and_standard_error():
    row = {'feature_id': 'g3', 'status': 'outlier', 'effect': None,
        'standard_error': None, 'p_value': None, 'adjusted_p_value': None,
        'missing_reasons': {metric: 'upstream_outlier_suppressed' for metric in
            ('effect', 'standard_error', 'p_value', 'adjusted_p_value')}}
    assert ResultRow.model_validate(row).effect is None
    row['missing_reasons'].pop('p_value')
    with pytest.raises(ValueError, match='Every missing statistic'):
        ResultRow.model_validate(row)


@pytest.mark.parametrize('probability', [1e-300, 5e-324])
def test_review_preserves_finite_tiny_probabilities_and_rejects_zero_substitution(tmp_path, probability):
    result = result_record()
    result['rows'][0].update(p_value=probability, adjusted_p_value=probability * 2)
    packet = api().parse_claim_packet(packet_record(tmp_path, result))
    captured = api().verified_artifacts(packet)['result']
    assert captured['rows'][0]['p_value'] == probability
    with pytest.raises(ValueError):
        api().validate_judgment(packet, judgment(packet, ({'rule_id': 'probability', 'value': 0.},)))


def test_review_unknown_upstream_na_is_partial_without_fabricated_outlier_attribution():
    result = result_record()
    result['family']['failed_hypothesis_policy'] = 'exclude_failed_hypotheses'
    result['rows'][0].update(status='partially_available', p_value=None,
        adjusted_p_value=None, missing_reasons={'p_value': 'upstream_NA_cause_unresolved',
            'adjusted_p_value': 'upstream_NA_cause_unresolved'})
    typed = typed_result(result)
    assert multiplicity_check(typed)['status'] == 'verified'
    assert typed.rows[0].effect == result['rows'][0]['effect']
    assert typed.rows[0].missing_reasons['p_value'] == 'upstream_NA_cause_unresolved'


def test_review_incomplete_bh_family_remains_unassessed_and_q_below_p_rejected():
    result = result_record()
    result['family']['complete'] = False
    assert multiplicity_check(typed_result(result))['reason'] == 'incomplete_tested_family'
    result['rows'][0]['adjusted_p_value'] = 1e-51
    with pytest.raises(ValueError, match='q >= p'):
        typed_result(result)
