"""Read-only numerical comparison of independently verified analysis bundles.

This compares declared results, not scientific methods, biological conclusions,
operator identities or clinical validity. It never executes retained code.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re

from .bundles import verify_bundle
from .checkpoints import digest
from devbench.study.evidence import _file_sha256, _read, decode_json


CONTRACT_FORMAT = 'devbench.analysis.numerical-equivalence-contract/1'
CONTRACT_PATH = 'numerical-equivalence-contract.json'
CONTRACT_KEY = 'numerical_equivalence_contract_sha256'
SCOPE = 'numerical_equivalence_not_scientific_validation'
_METADATA_BYTES = 2_000_000
_MAX_BYTES = 100_000_000
_MAX_ROWS = 1_000_000
_OPERATORS = {'<', '<=', '>', '>=', '==', '!='}


def _text(value, limit=200):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_numerical_equivalence_contract(contract):
    """Validate the complete bounded /1 schema; return an independent copy."""
    try:
        if (not isinstance(contract, dict) or set(contract) != {'format', 'declaration', 'results', 'limits'}
                or contract['format'] != CONTRACT_FORMAT):
            raise ValueError('unknown numerical equivalence contract schema')
        declaration = contract['declaration']
        if (not isinstance(declaration, dict) or set(declaration) != {'timing', 'artifact'}
                or declaration['timing'] not in {'historical_comparison', 'recorded_preparation_binding'}
                or declaration['artifact'] != (None if declaration['timing'] == 'historical_comparison' else CONTRACT_PATH)):
            raise ValueError('invalid numerical equivalence contract declaration')
        limits = contract['limits']
        if (not isinstance(limits, dict) or set(limits) != {'max_artifact_bytes', 'max_rows'}
                or type(limits['max_artifact_bytes']) is not int
                or not 1 <= limits['max_artifact_bytes'] <= _MAX_BYTES
                or type(limits['max_rows']) is not int or not 1 <= limits['max_rows'] <= _MAX_ROWS):
            raise ValueError('invalid numerical equivalence contract read limits')
        results = contract['results']
        if not isinstance(results, list) or not 1 <= len(results) <= 100:
            raise ValueError('invalid numerical equivalence result count')
        names, paths = set(), set()
        for result in results:
            if (not isinstance(result, dict) or set(result) != {
                    'name', 'path', 'kind', 'identifiers', 'alignment', 'numeric', 'categorical'}
                    or not _text(result['name']) or result['name'] in names):
                raise ValueError('invalid or duplicate numerical equivalence result')
            names.add(result['name'])
            path = result['path']
            if (not _text(path, 500) or '\\' in path or ':' in path or path in paths
                    or PurePosixPath(path).is_absolute()
                    or any(part in {'', '.', '..'} or part.startswith('.') for part in path.split('/'))):
                raise ValueError('unsafe or duplicate numerical equivalence result path')
            paths.add(path)
            if result['kind'] not in {'csv', 'json_records'} or result['alignment'] not in {'identifier', 'ordered'}:
                raise ValueError('unsupported numerical equivalence encoding or alignment')
            identifiers, categorical, numeric = result['identifiers'], result['categorical'], result['numeric']
            if (not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 20
                    or not isinstance(categorical, list) or len(categorical) > 100
                    or not isinstance(numeric, dict) or not 1 <= len(numeric) <= 100):
                raise ValueError('invalid numerical equivalence columns')
            columns = identifiers + categorical + list(numeric)
            if not all(_text(column) for column in columns) or len(set(columns)) != len(columns):
                raise ValueError('invalid or overlapping numerical equivalence columns')
            for rule in numeric.values():
                if (not isinstance(rule, dict) or set(rule) not in ({'atol', 'rtol'}, {'atol', 'rtol', 'decision'})
                        or not all(_number(rule[key]) and rule[key] >= 0 for key in ('atol', 'rtol'))):
                    raise ValueError('invalid numerical equivalence tolerance')
                if 'decision' in rule:
                    decision = rule['decision']
                    if (not isinstance(decision, dict) or set(decision) != {'operator', 'threshold'}
                            or decision['operator'] not in _OPERATORS or not _number(decision['threshold'])):
                        raise ValueError('invalid numerical equivalence decision boundary')
        encoded = json.dumps(contract, allow_nan=False)
        if len(encoded.encode()) > 1_000_000:
            raise ValueError('numerical equivalence contract exceeds byte budget')
        return json.loads(encoded)
    except (TypeError, OverflowError, KeyError) as error:
        raise ValueError('invalid numerical equivalence contract') from error


def _captured(root, name, limit, manifest):
    """Hash and parse the same bounded buffer, never a later pathname read."""
    artifacts = manifest.get('artifacts')
    expected = artifacts.get(name) if isinstance(artifacts, dict) else None
    if not isinstance(expected, str) or re.fullmatch(r'[0-9a-f]{64}', expected) is None:
        raise ValueError('result or metadata is not a sealed manifest artifact: ' + name)
    content = _read(root / name, limit)
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError('captured bundle artifact mismatch: ' + name)
    return content


def _json(root, name, manifest=None):
    content = (_read(root / name, _METADATA_BYTES) if manifest is None
               else _captured(root, name, _METADATA_BYTES, manifest))
    return decode_json(content)


def _manifest(root, expected_root):
    content = _read(root / 'receipts.json', _METADATA_BYTES)
    if hashlib.sha256(content).hexdigest() != expected_root:
        raise ValueError('captured bundle manifest root mismatch')
    manifest = decode_json(content)
    if not isinstance(manifest, dict) or manifest.get('format') != 'devbench.analysis.bundle/1':
        raise ValueError('invalid captured bundle manifest')
    return manifest


def verify_preparation_contract(root, parameters, identity, *, manifest=None):
    """Verify a preparation-bound declaration without changing old identities."""
    expected = parameters.get(CONTRACT_KEY)
    if expected is None:
        if CONTRACT_KEY in identity or (root / CONTRACT_PATH).exists():
            raise ValueError('unregistered numerical equivalence contract')
        return None
    declared = validate_numerical_equivalence_contract(_json(root, CONTRACT_PATH, manifest))
    if (declared['declaration']['timing'] != 'recorded_preparation_binding'
            or digest(declared) != expected or identity.get(CONTRACT_KEY) != expected):
        raise ValueError('numerical equivalence contract preparation binding mismatch')
    return declared


def _compatible_bundles(reference, replay, contract, reference_root, manifests):
    from .runtime import verify_runtime_compatibility
    parameters = [_json(root, 'parameters.json', manifest) for root, manifest in zip((reference, replay), manifests)]
    identities = [_json(root, 'checkpoint-identity.json', manifest) for root, manifest in zip((reference, replay), manifests)]
    runtimes = [_json(root, 'runtime.json', manifest) for root, manifest in zip((reference, replay), manifests)]
    for root, params, identity, runtime, manifest in zip((reference, replay), parameters, identities, runtimes, manifests):
        if not all(isinstance(record, dict) for record in (params, identity, runtime)):
            raise ValueError('invalid bundle parameter/checkpoint/runtime record shape')
        if (not isinstance(params.get('data_sha256'), str)
                or re.fullmatch(r'[0-9a-f]{64}', params['data_sha256']) is None):
            raise ValueError('missing or invalid bundle input identity')
        source_digest = _file_sha256(root / 'source.zip', _MAX_BYTES)
        if (identity.get('parameters') != params or identity.get('data_sha256') != params.get('data_sha256')
                or identity.get('source_sha256') != params.get('source_sha256')
                or manifest.get('data_sha256') != params.get('data_sha256')
                or manifest.get('source_sha256') != params.get('source_sha256')
                or source_digest != params.get('source_sha256')
                or source_digest != manifest['artifacts'].get('source.zip')):
            raise ValueError('bundle input/source/parameter identity mismatch')
        verify_runtime_compatibility(identity, runtime)
        if contract['declaration']['timing'] == 'recorded_preparation_binding':
            declared = verify_preparation_contract(root, params, identity, manifest=manifest)
            if declared is None or digest(declared) != digest(contract):
                raise ValueError('numerical equivalence contract preparation binding missing')
    if replay == reference or parameters[1].get('parent_root') != reference_root:
        raise ValueError('replay requires a distinct bundle bound to the reference root')
    # Parent linkage and runner location/tier are execution metadata. All other
    # declared parameters, including seeds, methods and contract identity, agree.
    scientific = [{key: value for key, value in params.items() if key not in {'parent_root', 'execution_tier'}}
                  for params in parameters]
    if scientific[0] != scientific[1]:
        raise ValueError('input/source/scientific parameters differ')
    verify_runtime_compatibility(identities[0], runtimes[1], recorded_runtime=runtimes[0])


def _numeric(value, *, csv_value):
    if type(value) is Fraction:
        # JSON number tokens were already admitted exactly by _record_json.
        return value
    if value is None or (csv_value and value == ''):
        return None
    if type(value) not in (str, int, float) or isinstance(value, str) and (
            not value or len(value) > 400 or re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', value) is None):
        raise ValueError('invalid numeric result cell')
    try:
        number = Decimal(str(value))
        # Results must be representable as finite ordinary numerical values.
        ordinary = float(number)
        if not number.is_finite() or not math.isfinite(ordinary) or number != 0 and ordinary == 0:
            raise ValueError('nonfinite numeric result cell')
        # Exact bounded rational arithmetic keeps both underflow and rounded
        # long-decimal values from crossing a declared tolerance boundary.
        return Fraction(number)
    except (InvalidOperation, OverflowError) as error:
        raise ValueError('invalid numeric result cell') from error


def _record_json(content):
    """Decode all JSON numbers exactly, including undeclared nested columns.

    The ordinary receipt decoder uses floats. Result tokens instead undergo the
    same bounded finite admission as CSV before becoming exact rationals. No
    JSON number can be rounded into a successful-looking zero or boundary value.
    """
    def unique(items):
        record = {}
        for key, value in items:
            if key in record:
                raise ValueError('duplicate JSON result key')
            record[key] = value
        return record

    def number(token):
        return _numeric(token, csv_value=False)

    def nonfinite(token):
        raise ValueError('nonfinite JSON result number')

    return json.loads(content, object_pairs_hook=unique, parse_int=number,
                      parse_float=number, parse_constant=nonfinite)


def _records(root, result, limits, manifest):
    content = _captured(root, result['path'], limits['max_artifact_bytes'], manifest)
    if result['kind'] == 'csv':
        reader = csv.reader(io.StringIO(content.decode('utf-8'), newline=''), strict=True)
        headers = next(reader, None)
        if (not headers or not all(_text(value) for value in headers) or len(set(headers)) != len(headers)):
            raise ValueError('missing or duplicate CSV headers')
        values = []
        for row in reader:
            if len(values) >= limits['max_rows'] or len(row) != len(headers):
                raise ValueError('CSV row budget or shape mismatch')
            values.append(dict(zip(headers, row)))
    else:
        values = _record_json(content)
        if not isinstance(values, list) or len(values) > limits['max_rows']:
            raise ValueError('JSON records row budget or shape mismatch')
    if not values:
        raise ValueError('result contains no comparable records')
    columns = result['identifiers'] + result['categorical'] + list(result['numeric'])
    records = {}
    for row in values:
        if not isinstance(row, dict) or not set(columns).issubset(row):
            raise ValueError('result record missing declared columns')
        identifier = tuple(row[name] for name in result['identifiers'])
        if not all(_text(value, 1000) for value in identifier) or identifier in records:
            raise ValueError('blank, non-string or duplicate result identifier')
        normalized = {name: _numeric(row[name], csv_value=result['kind'] == 'csv') for name in result['numeric']}
        for name in result['categorical']:
            value = row[name]
            if value is not None and (not isinstance(value, str) or len(value) > 1000):
                raise ValueError('invalid categorical result cell')
            normalized[name] = value
        records[identifier] = normalized
    return records, len(content)


def _decision(value, rule):
    threshold = Fraction(Decimal(str(rule['threshold'])))
    operator = rule['operator']
    return {'<': value < threshold, '<=': value <= threshold, '>': value > threshold,
            '>=': value >= threshold, '==': value == threshold, '!=': value != threshold}[operator]


def _compare_records(left, right, result):
    if set(left) != set(right):
        return {'name': result['name'], 'status': 'mismatch', 'reason': 'identifier_set_mismatch',
                'reference_rows': len(left), 'replay_rows': len(right)}
    if result['alignment'] == 'ordered' and list(left) != list(right):
        return {'name': result['name'], 'status': 'mismatch', 'reason': 'identifier_order_mismatch',
                'reference_rows': len(left), 'replay_rows': len(right)}
    mismatches = {}
    for identifier, reference in left.items():
        observed = right[identifier]
        for name, rule in result['numeric'].items():
            a, b = reference[name], observed[name]
            if a is None or b is None:
                same = a is None and b is None
            else:
                tolerance = Fraction(Decimal(str(rule['atol']))) + Fraction(Decimal(str(rule['rtol']))) * abs(a)
                same = abs(a - b) <= tolerance
                if 'decision' in rule:
                    same = same and _decision(a, rule['decision']) == _decision(b, rule['decision'])
            if not same:
                mismatches[name] = mismatches.get(name, 0) + 1
        for name in result['categorical']:
            if reference[name] != observed[name]:
                mismatches[name] = mismatches.get(name, 0) + 1
    return {'name': result['name'], 'status': 'mismatch' if mismatches else 'equivalent',
            'reference_rows': len(left), 'replay_rows': len(right), 'mismatched_cells': mismatches}


def compare_bundle_results(reference, replay, *, expected_reference_root, expected_replay_root, contract):
    """Verify custody, then compare named results; never write or execute either bundle.

    Invalid contracts and broken custody raise ValueError. Missing/failed results
    and incompatible provenance produce a finite not_comparable receipt.
    """
    contract = validate_numerical_equivalence_contract(contract)
    reference, replay = Path(reference), Path(replay)
    try:
        custody = [verify_bundle(reference, expected_reference_root), verify_bundle(replay, expected_replay_root)]
    except (AttributeError, TypeError, KeyError) as error:
        raise ValueError('invalid bundle custody metadata') from error
    receipt = {'format': 'devbench.analysis.numerical-equivalence-receipt/1', 'scope': SCOPE,
        'status': 'not_comparable', 'equivalent': None, 'prespecified': False,
        'contract_sha256': digest(contract), 'reference_root_sha256': expected_reference_root,
        'replay_root_sha256': expected_replay_root, 'custody': custody, 'results': [],
        'tolerance_rule': 'abs(replay-reference) <= atol + rtol*abs(reference); exact NA masks and categories',
        'runtime_scope': 'recorded Python major/minor and package versions; no platform attestation',
        'limits': 'No scientific concordance, biological validation, signature or clinical approval is established.'}
    try:
        manifests = [_manifest(root, expected) for root, expected in
                     ((reference, expected_reference_root), (replay, expected_replay_root))]
        if any(item.get('status') != 'completed_review_passed' for item in manifests):
            raise ValueError('reference or replay execution did not complete with passed review')
        _compatible_bundles(reference, replay, contract, expected_reference_root, manifests)
        receipt['prespecified'] = contract['declaration']['timing'] == 'recorded_preparation_binding'
        total_bytes = 0
        for result in contract['results']:
            parsed = []
            for root, manifest in zip((reference, replay), manifests):
                records, size = _records(root, result, contract['limits'], manifest)
                total_bytes += size
                if total_bytes > 2 * _MAX_BYTES:
                    raise ValueError('total numerical comparison byte budget exceeded')
                parsed.append(records)
            receipt['results'].append(_compare_records(*parsed, result))
        receipt['equivalent'] = all(item['status'] == 'equivalent' for item in receipt['results'])
        receipt['status'] = 'equivalent' if receipt['equivalent'] else 'mismatch'
    except (ValueError, TypeError, OSError, KeyError, UnicodeError, csv.Error, RecursionError) as error:
        receipt['status'], receipt['equivalent'] = 'not_comparable', None
        receipt['reason'] = str(error)[:400]
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--replay', type=Path, required=True)
    parser.add_argument('--expected-reference-root', required=True)
    parser.add_argument('--expected-replay-root', required=True)
    parser.add_argument('--contract', type=Path, required=True)
    args = parser.parse_args(argv)
    contract = decode_json(_read(args.contract, 1_000_000))
    result = compare_bundle_results(args.reference, args.replay,
        expected_reference_root=args.expected_reference_root, expected_replay_root=args.expected_replay_root,
        contract=contract)
    print(json.dumps(result, allow_nan=False, indent=2))
    return result


if __name__ == '__main__':
    main()
