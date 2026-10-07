"""Frozen synthetic software controls; recorded models are not native qualification.

Extracted from the previously exercised 0/1/2 pre/post-fit controls. Input arrays
are constructed locally and source-bound. Post-fit uses the actual trusted
ToolDispatcher numerical path once; no tests, datasets or plugins are imported.
All choices are fixture-authored execution controls, never scientific selection.
"""
from copy import deepcopy
import csv
import hashlib
import importlib
import inspect
import json
import math
from pathlib import Path

from devharness_core.provenance import canonical_json, hash_artifact
from devharness_server.dispatcher import ToolDispatcher, capture_tool_events
from devbench.analysis.episode_agent import Selection
from devbench.analysis.method_planner import ScientificMethodPlanner
from devbench.analysis.methods import MethodService, _digest
from devbench.analysis.rna_investigation_backend import AuditedInvestigationBackend, InvestigationExecutionAuthority
from devbench.analysis.rna_investigation_projection import verify_packet_projection
from devbench.analysis.rna_investigations import retain_investigation_json
from devbench.analysis.workflow_actions import WorkflowActions, create_rna_workflow
from devbench.analysis.workflow_operations import WorkflowOperationLedger
from devbench.analysis.workflow_state import digest
from devbench.analysis.rna_trace_receipts import LocalTraceObserver


def custom(tmp_path, *, invariants=(), comparator='json'):
    source = tmp_path / 'source.py'
    source.write_text('# A previously unknown user implementation\n')
    lock = tmp_path / 'environment.lock'
    lock.write_text('fixture-runtime=1\n')
    return ({'format': 'devharness.method/1', 'method_id': 'custom.unknown', 'name': 'Unknown QC', 'lifecycle': 'custom', 'implementation_trust': 'unreviewed', 'inputs': {'dataset': {'artifact_type': 'json', 'required': True}}, 'parameters': {'type': 'object', 'additionalProperties': False, 'properties': {'threshold': {'type': 'number', 'exclusiveMinimum': 0}}, 'required': ['threshold']}, 'outputs': {'dataset': {'artifact_type': 'json', 'path': '/outputs/result.json', 'required': True}}, 'implementation': {'kind': 'oci', 'source_sha256': hash_artifact(source)['sha256'], 'entrypoint': ['python', '/method/main.py']}, 'environment': {'image': 'fixture.invalid/python@sha256:' + 'a' * 64, 'lock_sha256': hash_artifact(lock)['sha256']}, 'execution': {'cpu': 1, 'memory_mb': 256, 'timeout_seconds': 5, 'pids': 32, 'max_output_bytes': 1000000, 'network': 'deny'}, 'validation': {'comparator': comparator, 'invariants': list(invariants), 'counts_layer': 'X', 'qc_metrics': []}, 'provenance': {'origin': 'test_fixture'}}, source, lock)

def declaration(representation='raw_counts'):
    return {'format': 'devbench.bulk-rna-input/1', 'representation': representation, 'axes': ['gene', 'sample'], 'provenance': None, 'biological_independence': None}

def candidate(name='Unknown QC'):
    return {'name': name, 'method_family': 'quality_control', 'source': {'type': 'official_package', 'reference': 'https://example.invalid/synthetic-method'}, 'assumptions': [{'statement': 'Independent samples are available', 'status': 'satisfied', 'evidence': 'Synthetic fixture', 'hard_precondition': True, 'field': 'independent_samples', 'operator': 'equals', 'expected': True}], 'expected_inputs': ['dataset'], 'dataset_fit': 'plausible', 'objective_fit': 'strong', 'known_failure_modes': ['Selection bias'], 'parameter_candidates': [{'name': 'threshold', 'value': 4, 'origin': 'fixture', 'rationale': 'Fixture-authored synthetic test value'}]}

class Selector:

    def __init__(self, *answers):
        self.answers = list(answers)
        self.contexts = []

    def configuration(self):
        return {'backend': 'recorded_fixture'}

    def select(self, context, output, **kwargs):
        self.contexts.append(deepcopy(context))
        return Selection(self.answers.pop(0), {'backend': 'recorded_fixture', 'model_execution': False})

def recommendation(choice='Unknown QC', implementation='low'):
    return {'choice': choice, 'confidence': 'low' if choice else 'no_recommendation', 'scientific_confidence': 'low' if choice else 'no_recommendation', 'implementation_confidence': implementation if choice else 'unknown', 'confidence_basis': 'One synthetic trial; unavailable alternatives remain untested', 'rationale': 'Review this provisional option', 'tradeoffs': ['Other candidates remain unavailable'], 'alternatives': ['Unavailable preferred method'], 'params': {'threshold': 4} if choice else {}, 'tests_run': ['qc-pilot'] if choice else [], 'approval_actions': [{'action': 'approve_recommendation', 'required': True, 'status': 'pending'}, {'action': 'approve_analysis_plan_change', 'required': True, 'status': 'pending'}]}

def _policy(kind='hard_total_tokens', **updates):
    value = {'format': 'devbench.model-budget-policy/1', 'kind': kind, 'max_total_tokens': 120 if kind == 'hard_total_tokens' else None, 'soft_token_target': 120 if kind == 'bounded_observed_tokens' else None, 'max_calls': 12, 'max_total_seconds': 120, 'max_context_bytes': 65536, 'memory_mb': 512, 'max_children': 2, 'reconciliation': {'tokens': 20, 'calls': 1, 'seconds': 10, 'context_bytes': 4096}}
    return {**value, **updates}

def _capability(**updates):
    return {'backend': 'enforced-offline-fixture', 'backend_digest': 'b' * 64, 'runtime_digest': 'c' * 64, 'source_digest': 'd' * 64, 'capability_status': 'offline_qualified', 'enforced_total_token_cap': True, 'usage_capture': 'verified_complete', 'context_bytes_enforced': True, 'outer_timeout_enforced': True, 'memory_enforced': True, 'request_count_enforced': True, 'all_attributable_requests_accounted': True, 'parallel_boundary_qualified': True, 'child_command_or_numerical_tools_allowed': False, **updates}

def contract():
    name = 'devbench.analysis.rna_investigation_contract'
    assert importlib.util.find_spec(name) is not None, 'Independent investigation contract is missing'
    return importlib.import_module(name)

def ref(identity='audit', value=None, kind='json'):
    content = canonical_json({} if value is None else value)
    return {'artifact_id': identity, 'sha256': hashlib.sha256(content).hexdigest(), 'artifact_type': kind, 'size_bytes': len(content)}

def fixture(stage='pre_fit_methods', *, action='infer', changes=None):
    audit = {'input_integrity_valid': True, 'gene_ids_unique': True, 'sample_ids_unique': True, 'alignment_valid': True, 'measurements_finite': True, 'count_values_valid': True, 'chronology_valid': True, 'design_rank_valid': True, 'contrast_valid': True, 'age_batch_confounded': False, 'sample_count': 4, 'representation': 'raw_counts', 'biological_units': ['donor-a', 'donor-b', 'donor-c', 'donor-d'], 'biological_independence': True, 'biological_unit_identity_known': True}
    audit.update(changes or {})
    audit_ref = ref('audit', audit)
    declared = {'representation', 'biological_units', 'biological_independence', 'biological_unit_identity_known'}
    facts = {key: [] for key in ('observed', 'declared', 'inferred', 'unknown')}
    for field, value in audit.items():
        category = 'declared' if field in declared else 'observed'
        facts[category].append({'field': field, 'value': value, 'source': {'kind': 'caller_declaration' if category == 'declared' else 'coordinator_artifact', 'reference': 'audit#/' + field}, 'evidence_refs': [audit_ref], 'limitation': 'Declarations are supplied, not independent validation.' if category == 'declared' else None})
    plan = {'session_id': '1' * 32, 'context': {'task': {'scientific_objective': 'Investigate temporal expression', 'computational_task': 'Assess valid temporal approaches'}, 'dataset_summary': {'observed': {}, 'inferred': {}, 'unknowns': [], 'artifact_ids': ['audit']}}, 'data_classification': 'synthetic', 'limits': {'max_context_bytes': 128000}, 'user_parameter_requirements': [], 'scientific_candidates': [{'parent_preferred': 'Do not expose'}], 'selection_history': [{'private_parent_history': 'Do not expose'}], 'registry': {'available_methods': ['Do not expose']}, 'approvals': {'grant': 'Do not expose'}}
    evidence = {'investigation_id': '2' * 32, 'question_record': {'objective': plan['context']['task']['scientific_objective'], 'estimand': 'Change across collection time among independent biological units', 'action': action, 'source': {'kind': 'caller_declaration', 'reference': 'task-scientific-objective'}, 'evidence_refs': [audit_ref]}, 'facts': facts, 'diagnostic_refs': [audit_ref], 'evidence_refs': [audit_ref], 'allowed_read_selectors': [], 'verified_artifacts': [{'ref': audit_ref, 'value': audit}]}
    policy = {'policy_digest': '3' * 64, 'source_digest': '4' * 64, 'runtime_digest': '5' * 64, 'max_context_bytes': 128000}
    return (plan, evidence, policy)

def investigator_candidate():
    return {'name': 'temporal_model', 'method_family': 'negative-binomial temporal splines', 'source': {'type': 'primary_paper', 'reference': 'Primary method documentation'}, 'assumptions': [{'statement': 'Independent units are required', 'status': 'satisfied', 'evidence': 'Supplied unit declaration', 'hard_precondition': False, 'field': 'biological_independence', 'operator': 'equals', 'expected': True}], 'expected_inputs': ['Raw counts and collection times'], 'dataset_fit': 'plausible', 'objective_fit': 'strong', 'known_failure_modes': ['Unidentifiable time and batch effects'], 'parameter_candidates': [{'name': 'df', 'value': 3, 'origin': 'assistant', 'rationale': 'A proposal to compare before results'}]}

def result(p, *, transported=True):
    assessment = {'status': 'supported', 'summary': 'Verified audit and supplied declarations support the bounded question.', 'evidence_refs': p['evidence_refs']}
    proposals = {'candidates': [investigator_candidate()], 'comparison_diagnostics': ['Compare feasible AIC and residual diagnostics before outcomes'], 'tradeoffs': ['Selection remains conditional']} if p['stage'] == 'pre_fit_methods' else {'explanations': ['Observed associations permit competing biological explanations'], 'discriminating_evidence': ['Independent composition measurements']}
    return {'format': 'devbench.rna-investigation-result/1', 'investigation_id': p['investigation_id'], 'parent_session_id': p['parent_session_id'], 'packet_sha256': contract().investigation_packet_digest(p), 'stage': p['stage'], 'status': 'completed', 'data_assessment': deepcopy(assessment), 'requirements_assessment': deepcopy(assessment), 'proposals': proposals, 'assumptions': ['No independent scientific validation follows from integrity'], 'missing_information': [], 'evidence_claims': [], 'read_requests': [], 'uncertainty': ['This proposal does not establish the best method'], 'capture_ref': ref('capture', kind='rna_investigation_capture') if transported else None, 'usage_ref': ref('usage', kind='rna_investigation_usage') if transported else None, 'error': None}

class CapturedFixtureSelector(Selector):
    """Test-only protected capture simulation, zero provider calls."""

    def select(self, context, output, **kwargs):
        self.forwarded_limits = deepcopy(kwargs)
        (output / 'context.json').write_bytes(canonical_json(context))
        native_usage = {'exact_accounting_available': False} if getattr(self, 'unknown_usage', False) else {'input_tokens': 8, 'output_tokens': 4, 'cached_input_tokens': 3, 'reasoning_tokens': 2}
        (output / 'usage.json').write_bytes(canonical_json(native_usage))
        if self.answers and isinstance(self.answers[0], BaseException):
            error = self.answers.pop(0)
            (output / 'failure.json').write_bytes(canonical_json({'error': type(error).__name__}))
            raise error
        response = super().select(context, output, **kwargs)
        (output / 'response.json').write_bytes(canonical_json(response.action))
        return response

class CapturedFixtureBackend(AuditedInvestigationBackend):
    """Explicit offline fixture verifier; never qualifies a native backend."""

    def __init__(self, methods, selector):
        self.methods, self.selector = (methods, selector)
        self.capability = _capability()
        self.unknown_usage = False

    def capabilities(self):
        return deepcopy(self.capability)

    def bind_ledger(self, ledger):
        self.ledger = ledger

    def parent_selection_binding(self, selector, context, *, parent_session_id, session_id, invocation_id):
        if selector is not self.selector:
            raise ValueError('A raw selector cannot bypass the admitted protected backend')
        selector.unknown_usage = self.unknown_usage
        return {'parent_session_id': parent_session_id, 'session_id': session_id, 'invocation_id': invocation_id, 'backend_digest': self.capability['backend_digest'], 'runtime_digest': self.capability['runtime_digest'], 'context_digest': _digest(context), 'prompt_digest': _digest('offline fixture instructions'), 'schema_digest': _digest(context['decision_kind'])}

    def parent_call_limits(self, context, campaign, planner_limits):
        allocation = deepcopy(campaign['identity']['parent_call_limits']['default'])
        allocation['seconds'] = min(allocation['seconds'], planner_limits['session_timeout_seconds'])
        return allocation

    def verify_selection_capture(self, output, *, bindings, reservation):

        def retain(path):
            record = self.methods.import_artifact(path, artifact_type='json', actor='offline-fixture-capture')
            return {key: record[key] for key in ('artifact_id', 'sha256', 'artifact_type', 'size_bytes')}
        context = retain(output / 'context.json')
        if context['sha256'] != bindings['context_digest']:
            raise ValueError('Fixture context capture binding differs')
        response = retain(output / 'response.json') if (output / 'response.json').exists() else None
        usage = retain(output / 'usage.json')
        request = reservation.request
        manifest = {'format': 'offline-fixture.model-capture/1', 'scope': 'fixture_only', 'reservation_ref': reservation.ref, 'request_digest': reservation.request_digest, 'bindings': {key: request[key] for key in ('session_id', 'invocation_id', 'backend_digest', 'runtime_digest', 'prompt_digest', 'schema_digest', 'context_digest')}, 'context_ref': context, 'response_ref': response, 'usage_ref': usage, 'response_digest': response['sha256'] if response else None, 'status': 'completed' if response else 'failed', 'unknown_usage': self.unknown_usage}
        path = output / 'capture.json'
        path.write_bytes(canonical_json(manifest))
        retained = self.methods.import_artifact(path, artifact_type='rna_investigation_capture', actor='offline-fixture-capture')
        capture = {key: retained[key] for key in ('artifact_id', 'sha256', 'artifact_type', 'size_bytes')}
        return (capture, {**self.verify_capture(capture, reservation), 'response_digest': manifest['response_digest']})

    def verify_capture(self, capture_ref, reservation=None):
        path = self.methods.artifact_path(capture_ref['artifact_id'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != capture_ref['sha256']:
            raise ValueError('Capture manifest hash changed')
        manifest = json.loads(path.read_bytes())
        if reservation is None:
            record = self.ledger.get_model_reservation(manifest['reservation_ref'])
            reservation = self.ledger._reservation(record, self.ledger.get_campaign(record['campaign_id']))
        if manifest['scope'] != 'fixture_only' or manifest['request_digest'] != reservation.request_digest:
            raise ValueError('Fixture capture request differs')
        for name in ('context_ref', 'response_ref', 'usage_ref'):
            ref = manifest[name]
            if ref is not None:
                actual = self.methods.artifact_path(ref['artifact_id'])
                if hashlib.sha256(actual.read_bytes()).hexdigest() != ref['sha256']:
                    raise ValueError('Fixture raw capture changed')
        raw_usage = json.loads(self.methods.artifact_path(manifest['usage_ref']['artifact_id']).read_bytes())
        from devbench.study.model_telemetry import normalize_usage
        usage = normalize_usage(raw_usage)
        return {'format': 'devbench.model-call-capture/1', 'reservation_ref': reservation.ref, 'request_digest': reservation.request_digest, **manifest['bindings'], 'status': manifest['status'], 'capture_ref': capture_ref, 'usage': usage, 'actual_calls': 2, 'native_session_ids': ['offline-preflight-' + reservation.ref[-32:], 'offline-action-' + reservation.ref[-32:]], 'elapsed_seconds': 1, 'launch_confirmed': True}

    def verify_child_result(self, packet_ref, result_ref):
        from devbench.analysis.rna_investigation_contract import validate_investigation_result, investigation_context
        packet = self.read_packet(packet_ref)
        result = validate_investigation_result(self.read_result(result_ref), packet=packet)
        receipt = self.verify_capture(result['capture_ref'])
        capture = self._document(result['capture_ref'])
        raw = json.loads(self.methods.artifact_path(capture['response_ref']['artifact_id']).read_bytes())
        if {**raw, 'capture_ref': result['capture_ref'], 'usage_ref': result['usage_ref']} != result or receipt['context_digest'] != _digest(investigation_context(packet)) or receipt['schema_digest'] != packet['return_schema_digest'] or (packet['bindings']['source_digest'] != self.capability['source_digest']) or (packet['bindings']['runtime_digest'] != self.capability['runtime_digest']):
            raise ValueError('Fixture child actual capture, schema or runtime binding differs')
        usage = self._document(result['usage_ref'], artifact_type='rna_investigation_usage', max_bytes=24000)
        charge = self.ledger.get_model_reservation(capture['reservation_ref'])
        if usage != {'format': 'devbench.rna-investigation-usage/1', 'capture_ref': result['capture_ref'], 'receipt': receipt}:
            raise ValueError('Fixture child usage differs from actual capture')
        if charge['status'] != 'completed' or charge['capture'] != receipt:
            raise ValueError('Fixture child was not charged through the retained ledger')
        return result

    def verify_incomplete_child(self, packet_ref, dispatch_ref):
        from devbench.analysis.rna_investigation_contract import investigation_context
        packet = self.read_packet(packet_ref)
        receipt = self.verify_capture(dispatch_ref)
        if receipt['status'] != 'failed' or receipt['context_digest'] != _digest(investigation_context(packet)):
            raise ValueError('Fixture missing child has no failed exact capture')
        return {'status': 'incomplete'}

class WorkflowFixtureBackend(CapturedFixtureBackend):
    """Real immutable fixture captures, private calls, and shared ledger receipts."""

    def __init__(self, methods, selector):
        super().__init__(methods, selector)
        self.child_contexts = []
        self.fail_children = set()

    def verify_capture(self, capture_ref, reservation=None):
        if reservation is None:
            manifest = json.loads(self.methods.artifact_path(capture_ref['artifact_id']).read_bytes())
            record = self.ledger.get_model_reservation(manifest['reservation_ref'])
            reservation = self.ledger._reservation(record, self.ledger.get_campaign(record['campaign_id']))
        return super().verify_capture(capture_ref, reservation)

    def prepare_requests(self, packet_refs, *, budget_policy, reservation_refs):
        from devbench.analysis.rna_investigation_contract import investigation_context, validate_investigation_request
        packets = [self.read_packet(reference) for reference in packet_refs]
        roots = [self.methods.root / 'offline-fixture-children' / packet['investigation_id'] for packet in packets]
        for root in roots:
            root.mkdir(mode=448, parents=True)
        prepared = []
        for packet, reference, reservation_ref in zip(packets, packet_refs, reservation_refs):
            request = {'format': 'devbench.rna-investigation-request/1', 'investigation_id': packet['investigation_id'], 'parent_session_id': packet['parent_session_id'], 'stage': packet['stage'], 'packet_ref': reference, 'backend_digest': self.capability['backend_digest'], 'runtime_digest': self.capability['runtime_digest'], 'budget_policy': budget_policy, 'reservation_ref': reservation_ref}
            validate_investigation_request(request, packet=packet)
            context = investigation_context(packet)
            identity = digest({'request': request, 'reservation': reservation_ref})[:32]
            binding = {'format': 'devbench.model-call-request/1', 'parent_session_id': packet['parent_session_id'], 'session_id': packet['investigation_id'], 'invocation_id': identity, 'backend_digest': self.capability['backend_digest'], 'runtime_digest': self.capability['runtime_digest'], 'context_digest': digest(context), 'prompt_digest': digest('offline child instructions'), 'schema_digest': packet['return_schema_digest'], 'request': request, 'investigation_request_digest': digest(request), 'packet_ref': reference}
            descriptor = {'format': 'offline-fixture.invocation/1', 'request': request, 'binding': binding, 'child_roots': [str(root) for root in roots]}
            invocation_ref = self._retain(descriptor, 'json')
            with self.methods._db() as db:
                self.methods._put(db, 'fixture_invocation', digest(binding), {'invocation_ref': invocation_ref})
            prepared.append({'request': request, 'binding': binding, 'invocation_ref': invocation_ref})
        return prepared

    def read_dispatch(self, reference):
        from devbench.analysis.rna_investigations import read_investigation_json
        value = read_investigation_json(self.methods, reference)
        assert value['format'] == 'offline-fixture.invocation/1'
        self.read_packet(value['request']['packet_ref'])
        return value

    def investigate(self, request, *, execution_authority, reservation):
        from devbench.analysis.rna_investigation_contract import investigation_context, validate_investigation_result
        execution_authority.require(request)
        assert self.ledger.get_model_reservation(reservation)['status'] == 'active'
        assert reservation.request['request'] == request
        packet = self.read_packet(request['packet_ref'])
        context = investigation_context(packet)
        self.child_contexts.append(deepcopy(context))
        action = result(packet, transported=False)
        output = self.methods.root / 'offline-fixture-children' / packet['investigation_id'] / 'capture'
        output.mkdir()
        selector = CapturedFixtureSelector(action)
        selector.select(context, output, allow_model_execution=False)
        capture_ref, _ = self.verify_selection_capture(output, bindings=reservation.request, reservation=reservation)
        receipt = self.verify_capture(capture_ref, reservation)
        usage_ref = self._retain({'format': 'devbench.rna-investigation-usage/1', 'capture_ref': capture_ref, 'receipt': receipt}, 'rna_investigation_usage')
        captured = json.loads((output / 'response.json').read_bytes())
        result_ref, child_error = (None, None)
        try:
            validate_investigation_result(captured, packet=packet, transport_pending=True)
            transported = {**captured, 'capture_ref': capture_ref, 'usage_ref': usage_ref}
            validate_investigation_result(transported, packet=packet)
            result_ref = self._retain(transported, 'rna_investigation_result')
        except ValueError as error:
            child_error = {'type': 'InvalidFixtureResponse', 'message': str(error)[:500]}
        invocation_ref = self.methods.get('fixture_invocation', digest(reservation.request))['invocation_ref']
        outcome = {'status': 'completed' if result_ref else 'incomplete', 'invocation_ref': invocation_ref, 'result_ref': result_ref, 'capture_ref': capture_ref, 'usage_ref': usage_ref, 'error': child_error}
        if packet['investigation_id'] in self.fail_children:
            outcome.update(status='incomplete', result_ref=None, error={'type': 'MalformedFixture', 'message': 'Fixture failure retained'})
        with self.methods._db() as db:
            self.methods._put(db, 'fixture_completion', digest(invocation_ref), outcome)
        return outcome

    def reconcile_capture(self, invocation_ref):
        self.read_dispatch(invocation_ref)
        try:
            outcome = self.methods.get('fixture_completion', digest(invocation_ref))
        except (KeyError, ValueError):
            return {'status': 'uncertain', 'invocation_ref': invocation_ref, 'result_ref': None, 'capture_ref': None, 'usage_ref': None, 'error': None}
        if outcome['capture_ref'] is not None:
            self.verify_capture(outcome['capture_ref'])
        return deepcopy(outcome)

    def verify_child_result(self, packet_ref, result_ref):
        from devbench.analysis.rna_investigation_contract import validate_investigation_result
        packet, result = (self.read_packet(packet_ref), self.read_result(result_ref))
        validate_investigation_result(result, packet=packet)
        self.verify_capture(result['capture_ref'])
        manifest = json.loads(self.methods.artifact_path(result['capture_ref']['artifact_id']).read_bytes())
        response = json.loads(self.methods.artifact_path(manifest['response_ref']['artifact_id']).read_bytes())
        context = json.loads(self.methods.artifact_path(manifest['context_ref']['artifact_id']).read_bytes())
        if response != {**result, 'capture_ref': None, 'usage_ref': None} or context['packet'] != packet:
            raise ValueError('Fixture child response or neutral packet changed')
        return result

    def verify_incomplete_child(self, packet_ref, dispatch_ref):
        assert self.read_dispatch(dispatch_ref)['request']['packet_ref'] == packet_ref
        outcome = self.reconcile_capture(dispatch_ref)
        return {'status': outcome['status']}

def independent_setup(tmp_path, allocation=1, *, stage='pre_fit_methods'):
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    from devbench.analysis.rna_investigation_backend import InvestigationExecutionAuthority
    from devbench.analysis.rna_investigations import retain_investigation_json
    methods = MethodService(tmp_path / "methods")
    samples = ['s' + str(index) for index in range(8)]
    donors = ['virtual-donor-' + str(index) for index in range(8)]
    matrix_path, metadata_path = (tmp_path / 'counts.json', tmp_path / 'metadata.json')
    matrix_path.write_bytes(canonical_json({'gene_ids': ['g1', 'g2'], 'sample_ids': samples, 'matrix': [[10, 12, 13, 13, 20, 21, 24, 27], [30, 27, 24, 23, 20, 16, 10, 8]]}))
    metadata_path.write_bytes(canonical_json({'samples': [{'sample_id': sample, 'donor': donor, 'collection_time': index // 2, 'batch': 'A' if index % 2 == 0 else 'B'} for index, (sample, donor) in enumerate(zip(samples, donors))]}))
    refs = [methods.import_artifact(path, artifact_type='json', actor='offline-fixture') for path in (matrix_path, metadata_path)]
    declared = declaration()
    declared.update(provenance={'origin': 'synthetic software fixture; not biological validation'}, biological_independence={'origin': 'explicit virtual donor construction', 'independent': True}, metadata_roles={'biological_unit': 'donor', 'time': 'collection_time', 'batch': 'batch'})
    planner = ScientificMethodPlanner(methods)
    plan = planner.create({'scientific_objective': 'Compare temporal raw-count trajectories and valid nuisance strategies', 'computational_task': 'bulk RNA temporal method investigation'}, {'observed': {}, 'inferred': {}, 'unknowns': [], 'artifact_ids': [row['artifact_id'] for row in refs]}, actor='offline-fixture', data_classification='synthetic', rna_input_contract={'format': 'devbench.bulk-rna-binding/1', 'matrix_artifact_id': refs[0]['artifact_id'], 'metadata_artifact_id': refs[1]['artifact_id'], 'declarations': declared})
    proposals = [candidate('Fixture NB linear'), candidate('Fixture NB spline'), candidate('Fixture NB shared nuisance'), candidate('Fixture NB stratified nuisance')]
    params = (('df', 1), ('df', 3), ('nuisance_strategy', 'shared'), ('nuisance_strategy', 'stratified'))
    for row, (name, value) in zip(proposals, params):
        row['method_family'] = 'negative-binomial temporal trajectory'
        row['assumptions'] = [{'statement': 'The explicitly declared measurement is raw counts', 'status': 'satisfied', 'evidence': 'Registered synthetic raw-count input', 'hard_precondition': True, 'field': 'bulk_rna_input.representation', 'operator': 'equals', 'expected': 'raw_counts'}]
        row['parameter_candidates'] = [{'name': name, 'value': value, 'origin': 'fixture', 'rationale': 'A fixture-authored scientific comparison alternative, not a user requirement'}]
    answers = [{'candidates': proposals}]
    if allocation and stage == 'pre_fit_methods':
        answers.append({'candidates': proposals})
    answers.extend([{'bindings': [{'candidate': row['name'], 'method_version_id': None, 'discovery': None} for row in proposals]}, recommendation(None)])
    selector = CapturedFixtureSelector(*answers)
    backend = WorkflowFixtureBackend(planner.methods, selector)
    _, evidence, _ = fixture(action='infer')
    audit = evidence['verified_artifacts'][0]['value']
    audit.update(sample_count=8, biological_units=donors, biological_independence=True)
    audit_ref = retain_investigation_json(planner.methods, audit, actor='offline-fixture')
    for category, records in evidence['facts'].items():
        for row in records:
            row['value'] = audit[row['field']]
            row['source']['reference'] = audit_ref['artifact_id'] + '#/' + row['field']
            row['evidence_refs'] = [audit_ref]
    evidence.update(diagnostic_refs=[audit_ref], evidence_refs=[audit_ref], verified_artifacts=[{'ref': audit_ref, 'value': audit}])
    evidence.pop('investigation_id')
    evidence['question_record']['objective'] = plan['context']['task']['scientific_objective']
    evidence['question_record']['evidence_refs'] = [audit_ref]
    rules = [{'id': 'fixture-temporal-complexity', 'axis': 'temporal_shape_or_complexity', 'evidence_refs': [audit_ref], 'discriminating_criterion': 'Compare frozen predictive loss and residual diagnostics'}]
    if allocation == 2:
        rules.append({**deepcopy(rules[0]), 'id': 'fixture-nuisance-model', 'axis': 'valid_nuisance_model', 'discriminating_criterion': 'Compare frozen nuisance residual and design diagnostics'})
    limits = {'tokens': 40, 'calls': 2, 'seconds': 20, 'context_bytes': 128000, 'memory_mb': 512}
    policy = {'format': 'devbench.rna-investigation-policy/1', 'stage': stage, 'allocation': allocation, 'max_context_bytes': 128000, 'ambiguity_rules': rules, 'budget_policy': _policy(max_total_tokens=1000, max_calls=30, max_total_seconds=500, max_context_bytes=128000, reconciliation={'tokens': 160, 'calls': 8, 'seconds': 80, 'context_bytes': 128000}), 'parent_call_limits': {'default': limits}, 'child_call_limits': limits}
    ledger = WorkflowOperationLedger(planner.methods.root / 'independent-operations.sqlite')
    actions = WorkflowActions(planner, ledger, actor='offline-fixture-coordinator', selector=selector, allow_model_execution=True, investigation_policy=policy, investigation_evidence=evidence, investigation_authority=InvestigationExecutionAuthority(check=lambda request: True, data_classification='synthetic', budget_policy='hard_total_tokens'))
    return (planner, plan, backend, ledger, actions)

GENES = ['zeta', 'alpha', 'middle']

FORMULA = '1 + numeric_time + C(batch)'

PARAMETERS = {'formula_str': FORMULA, 'time_col': 'collection_time', 'gene_list': GENES, 'selection_criterion': 'fixed', 'spline_dfs': [2], 'data_type': 'continuous'}

def _ref(methods, identity):
    artifact = methods.get('artifact', identity)
    return {key: artifact[key] for key in ('artifact_id', 'sha256', 'artifact_type', 'size_bytes')}

def _write_csv(matrix, metadata, root):
    """Bounded lossless transport projection, without fitting or count conversion."""
    root.mkdir(parents=True, exist_ok=False)
    expression, samples = (root / 'expression.csv', root / 'metadata.csv')
    with expression.open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['gene', *matrix['sample_ids']])
        writer.writerows(([gene, *values] for gene, values in zip(matrix['gene_ids'], matrix['matrix'])))
    with samples.open('w', newline='') as stream:
        fields = ['sample_id', 'collection_time', 'donor_id', 'batch']
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(metadata['samples'])
    with expression.open(newline='') as stream:
        rows = list(csv.reader(stream))
    assert rows[0][1:] == matrix['sample_ids']
    assert [row[0] for row in rows[1:]] == matrix['gene_ids']
    assert [[float(value) for value in row[1:]] for row in rows[1:]] == matrix['matrix']
    return (expression, samples)

def _with_verified_bh_metadata(raw, gene_list):
    """Name an independently checked original correction; never repair statistics."""
    from devharness_server.tools_stat import bh_adjust
    assert raw['fdr_scope'] == 'requested_genes'
    assert gene_list and len(gene_list) == len(set(gene_list))
    assert set(raw['genes']) == set(gene_list)
    rows = [raw['genes'][gene] for gene in gene_list]
    adjusted = bh_adjust([row['p_value'] if row['p_value'] is not None else 1.0 for row in rows])
    for row, expected_q in zip(rows, adjusted):
        if row['p_value'] is None:
            assert row['q_value'] is None and row['padj'] is None
        else:
            assert 0 <= row['p_value'] <= row['q_value'] <= 1
            assert row['padj'] == row['q_value']
            assert math.isclose(row['q_value'], expected_q, rel_tol=1e-12, abs_tol=0.0)
    metadata = {'q_value': 'benjamini_hochberg', 'padj': 'benjamini_hochberg'}
    assert raw.get('fdr_methods', metadata) == metadata
    return {**deepcopy(raw), 'fdr_methods': metadata}

class ActualDispatcherExecutor:
    """Test-only executor with one real supervised numerical call per workflow."""

    def __init__(self, *, qc_only=False):
        self.qc_only = qc_only
        self.calls = []
        self.executions = 0
        self.input_digests = []
        self.raw_output_bytes = []

    def execute(self, contract, inputs, parameters, source, lock, output_dir):
        self.executions += 1
        assert self.executions == 1, 'Resume or replay redispatched numerical execution'
        matrix = json.loads(inputs['counts'].read_bytes())
        metadata = json.loads(inputs['metadata'].read_bytes())
        self.input_digests.append({name: hash_artifact(path)['sha256'] for name, path in inputs.items()})
        expression, samples = _write_csv(matrix, metadata, output_dir.parent / 'synthetic-format-view')
        dispatcher = ToolDispatcher(allowed_root=output_dir.parent)
        with capture_tool_events() as events:
            if self.qc_only:
                tool, arguments = ('audit_expression_quality', {'counts_path': str(expression), 'metadata_path': str(samples), 'time_col': 'collection_time', 'max_cells': 12, 'top_genes': 3, 'n_neighbors': 2})
            else:
                tool, arguments = ('fit_temporal_trajectory', {**parameters, 'counts_path': str(expression), 'metadata_path': str(samples)})
            self.calls.append(tool)
            raw_bytes = dispatcher.execute(tool, arguments).encode('utf-8')
            self.raw_output_bytes.append(raw_bytes)
            actual = json.loads(raw_bytes)
            if not self.qc_only:
                actual = _with_verified_bh_metadata(actual, parameters['gene_list'])
            (output_dir / 'result.json').write_bytes(canonical_json(actual))
        assert events and events[-1]['tool_name'] == tool
        return {'exit_code': 0, 'runtime': 'trusted_synthetic_json_adapter', 'tool_events': events}

class PostFitSelector(CapturedFixtureSelector):
    """Responses are recorded fixtures; all statistics enter via the real receipt."""

    def __init__(self, method_id, input_ids, allocation):
        super().__init__()
        self.method_id, self.input_ids, self.allocation = (method_id, input_ids, allocation)

    def select(self, context, output, **kwargs):
        phase = context['decision_kind']
        name = 'Fixture Gaussian association'
        if phase == 'method_scientific_planning':
            proposal = candidate(name)
            proposal.update(method_family='Gaussian temporal association', assumptions=[{'statement': 'The fixture explicitly declares continuous normalized measurements', 'status': 'satisfied', 'evidence': 'Immutable synthetic input declaration', 'hard_precondition': True, 'field': 'bulk_rna_input.representation', 'operator': 'equals', 'expected': 'normalized_expression'}], parameter_candidates=[{'name': key, 'value': value, 'origin': 'fixture', 'rationale': 'Test-author execution control, not a user-prespecified scientific choice'} for key, value in PARAMETERS.items()])
            answer = {'candidates': [proposal]}
        elif phase == 'method_capability_resolution':
            answer = {'bindings': [{'candidate': name, 'method_version_id': self.method_id, 'discovery': None}]}
        elif phase == 'method_trial_design':
            answer = {'experiments': [{'id': 'bounded-shared-fit', 'candidates': [name], 'purpose': 'A synthetic execution control; it cannot select the best scientific method', 'subset_spec': {'kind': 'full_dataset', 'representation': 'Unchanged normalized input'}, 'subset_validity': {'preserves_preconditions': True, 'rationale': 'All explicitly constructed independent virtual units and values retained'}, 'commands': [], 'metrics': [{'name': 'runtime_seconds', 'category': 'runtime', 'direction': 'lower_better', 'interpretation': 'Execution duration only'}], 'metric_sources': {'runtime_seconds': {'kind': 'runtime', 'field': 'elapsed_seconds'}}, 'resource_limits': {'memory_mb': 4096, 'timeout_seconds': 300, 'max_runs': 1}, 'inputs': {name: self.input_ids}, 'parameters': {name: PARAMETERS}}]}
        elif phase == 'method_preliminary_explanation':
            view = context['verified_output_view']
            stats = next((row for row in view['facts']['observed'] if row['field'] == 'gene_statistics'))
            coverage = next((row for row in view['facts']['observed'] if row['field'] == 'fitted_family_coverage'))
            assert stats['value']['tested_hypotheses'] == len(GENES)
            refs = stats['evidence_refs']
            answer = {'hypotheses': [{'id': 'composition', 'statement': 'Changing composition could explain the association', 'evidence_refs': refs, 'limitations': ['Cell composition was not measured']}, {'id': 'intrinsic', 'statement': 'An intrinsic response could explain the association; PARENT-PRIVATE-CANARY', 'evidence_refs': refs, 'limitations': ['Association does not establish causality']}, {'id': 'technical', 'statement': 'Technical effects could contribute to the association', 'evidence_refs': coverage['evidence_refs'], 'limitations': ['Independent technical measurements are absent']}], 'ambiguity_items': [{'id': 'composition-or-intrinsic', 'axis': 'composition_vs_intrinsic', 'hypothesis_ids': ['composition', 'intrinsic'], 'evidence_refs': refs, 'missing_information_status': 'sufficient', 'discriminating_criterion': 'Independent composition measurements in a new cohort'}], 'uncertainty': ['These remain exploratory explanations'], 'absent_assessments': ['Validated plot pixels', 'Qualified independent scientific review']}
            if self.allocation == 2:
                answer['ambiguity_items'].append({'id': 'technical-or-intrinsic', 'axis': 'technical_vs_biological', 'hypothesis_ids': ['technical', 'intrinsic'], 'evidence_refs': coverage['evidence_refs'], 'missing_information_status': 'sufficient', 'discriminating_criterion': 'Independent technical covariates and a replication cohort'})
        elif phase == 'method_recommendation':
            answer = recommendation(None)
        else:
            raise AssertionError('Unexpected unallocated model phase: ' + phase)
        self.answers.append(answer)
        return super().select(context, output, **kwargs)

class ObservingBackend(WorkflowFixtureBackend):

    def __init__(self, methods, selector, *, cancel_after_child=False):
        super().__init__(methods, selector)
        self.before_children = []
        self.cancel_after_child = cancel_after_child

    def investigate(self, request, **kwargs):
        parent = self.methods.get('method_plan', request['parent_session_id'])
        nomination = [row for row in parent['selection_history'] if row['phase'] == 'method_preliminary_explanation']
        assert len(nomination) == 1 and nomination[0]['status'] == 'accepted'
        charge = self.ledger.get_model_reservation(nomination[0]['model_reservation_ref'])
        assert charge['status'] == 'completed' and charge['capture']['usage']['normalized_total_tokens'] > 0
        self.before_children.append(deepcopy(charge))
        outcome = super().investigate(request, **kwargs)
        if self.cancel_after_child:
            self.ledger.cancel_campaign(request['parent_session_id'])
        return outcome

def _case(root, *, allocation=2, qc_only=False, cancel_after_child=False):
    root = Path(root).resolve()
    methods = MethodService(root / 'methods')
    samples = ['s' + str(index) for index in range(12)]
    matrix = {'gene_ids': GENES, 'sample_ids': samples, 'matrix': [[float(10 + gene * 2 + index * 0.35 + (index + gene) % 3 * 0.4) for index in range(12)] for gene in range(len(GENES))]}
    metadata = {'samples': [{'sample_id': sample, 'collection_time': 8 + index // 3, 'donor_id': 'virtual-independent-donor-' + str(index), 'batch': 'A' if index % 2 else 'B'} for index, sample in enumerate(samples)]}
    paths = {'counts': root / 'matrix.json', 'metadata': root / 'metadata.json'}
    for name, content in (('counts', matrix), ('metadata', metadata)):
        paths[name].write_bytes(canonical_json(content))
    inputs = {name: methods.import_artifact(path, artifact_type='json', actor='fixture-author')['artifact_id'] for name, path in paths.items()}
    declared = declaration('normalized_expression')
    declared.update(provenance={'origin': 'Constructed synthetic software control, not biological validation'}, biological_independence={'independent': True, 'origin': 'Explicit construction of separate virtual units'}, metadata_roles={'biological_unit': 'donor_id', 'time': 'collection_time', 'batch': 'batch'})
    planner = ScientificMethodPlanner(methods)
    plan = planner.create({'scientific_objective': 'Describe receipt-bound temporal associations and competing explanations', 'computational_task': 'Bounded synthetic post-fit explanation control'}, {'observed': {}, 'inferred': {}, 'unknowns': [], 'artifact_ids': list(inputs.values())}, actor='fixture-author', data_classification='synthetic', evaluation_purpose='execution_verification', rna_input_contract={'format': 'devbench.bulk-rna-binding/1', 'matrix_artifact_id': inputs['counts'], 'metadata_artifact_id': inputs['metadata'], 'declarations': declared})
    contract, source, lock = custom(root)
    contract.update(method_id='custom.synthetic-json-temporal-control', name='Fixture Gaussian association', inputs={port: {'artifact_type': 'json', 'required': True} for port in inputs}, parameters={'type': 'object', 'additionalProperties': False, 'properties': {key: {'const': value} for key, value in PARAMETERS.items()}, 'required': list(PARAMETERS)}, outputs={'statistics': {'artifact_type': 'json', 'path': '/outputs/result.json', 'required': True}})
    contract['execution'].update(memory_mb=4096, timeout_seconds=300)
    source.write_text('# Test-only trusted adapter, never provider or clinical qualification.\nimport csv\nimport json\nimport math\nfrom copy import deepcopy\nfrom devharness_core.provenance import canonical_json, hash_artifact\nfrom devharness_server.dispatcher import ToolDispatcher, capture_tool_events\n\n' + inspect.getsource(_write_csv) + '\n' + inspect.getsource(_with_verified_bh_metadata) + '\n' + inspect.getsource(ActualDispatcherExecutor) + "\nif __name__ == '__main__':\n    raise SystemExit('Trusted test executor only')\n")
    contract['implementation']['source_sha256'] = hash_artifact(source)['sha256']
    method = methods.register_method(contract, source=source, environment_lock=lock, actor='fixture-author')
    analysis = methods.create_analysis('Synthetic exploratory post-fit control', actor='fixture-author')
    branch = methods.create_branch(analysis['analysis_id'], analysis['revision_id'], actor='fixture-author')
    _, metadata_csv = _write_csv(matrix, metadata, root / 'pre-fit-audit-view')
    with capture_tool_events() as audit_events:
        design = json.loads(ToolDispatcher(allowed_root=root).execute('validate_design_matrix', {'metadata_csv_path': str(metadata_csv), 'formula_str': FORMULA, 'time_col': 'collection_time'}))
    assert design['is_full_rank'] is True and design['condition_number'] is not None
    audit = {'input_integrity_valid': True, 'gene_ids_unique': True, 'sample_ids_unique': True, 'alignment_valid': True, 'measurements_finite': True, 'chronology_valid': True, 'design_rank_valid': design['is_full_rank'], 'contrast_valid': True, 'age_batch_confounded': False, 'sample_count': len(samples), 'representation': 'normalized_expression', 'biological_units': [row['donor_id'] for row in metadata['samples']], 'biological_independence': True, 'biological_unit_identity_known': True, 'time_labels': [row['collection_time'] for row in metadata['samples']], 'batch_labels': [row['batch'] for row in metadata['samples']], 'design_diagnostics': design, 'metadata_roles': declared['metadata_roles'], 'source_input_refs': [_ref(methods, identity) for identity in inputs.values()], 'actual_design_tool_events': audit_events}
    assert len(set(audit['biological_units'])) == len(samples)
    assert all(({row['batch'] for row in metadata['samples'] if row['collection_time'] == age} == {'A', 'B'} for age in set(audit['time_labels'])))
    audit_ref = retain_investigation_json(methods, audit, actor='fixture-author')
    declared_fields = {'representation', 'biological_units', 'biological_independence', 'biological_unit_identity_known', 'time_labels', 'batch_labels', 'metadata_roles'}
    facts = {key: [] for key in ('observed', 'declared', 'inferred', 'unknown')}
    for field, value in audit.items():
        if field in {'source_input_refs', 'actual_design_tool_events'}:
            continue
        category = 'declared' if field in declared_fields else 'observed'
        facts[category].append({'field': field, 'value': value, 'source': {'kind': 'caller_declaration' if category == 'declared' else 'coordinator_artifact', 'reference': audit_ref['artifact_id'] + '#/' + field}, 'evidence_refs': [audit_ref], 'limitation': 'Synthetic declaration is not independent biological validation'})
    evidence = {'question_record': {'objective': plan['context']['task']['scientific_objective'], 'estimand': 'Association across declared collection ages among constructed independent units', 'action': 'infer', 'source': {'kind': 'caller_declaration', 'reference': 'synthetic-control-objective'}, 'evidence_refs': [audit_ref]}, 'facts': facts, 'evidence_refs': [audit_ref], 'diagnostic_refs': [audit_ref], 'allowed_read_selectors': [], 'verified_artifacts': [{'ref': audit_ref, 'value': audit}]}
    call_limits = {'tokens': 40, 'calls': 2, 'seconds': 20, 'context_bytes': 128000, 'memory_mb': 512}
    policy = {'format': 'devbench.rna-investigation-policy/1', 'stage': 'post_fit_explanation', 'allocation': allocation, 'max_context_bytes': 128000, 'statistics_projection': {'rule': 'deterministic_head', 'max_rows': 2, 'requirement_name': None}, 'budget_policy': _policy(max_total_tokens=1000, max_calls=30, max_total_seconds=500, max_context_bytes=128000, reconciliation={'tokens': 160, 'calls': 8, 'seconds': 80, 'context_bytes': 128000}), 'parent_call_limits': {'default': call_limits}, 'child_call_limits': call_limits}
    selector = PostFitSelector(method['version_id'], inputs, allocation)
    backend = ObservingBackend(methods, selector, cancel_after_child=cancel_after_child)
    ledger = WorkflowOperationLedger(root / 'operations.sqlite')
    executor = ActualDispatcherExecutor(qc_only=qc_only)
    actions = WorkflowActions(planner, ledger, actor='fixture-coordinator', selector=selector, version_ids=[method['version_id']], declarations=[{'draft_id': 'bounded-shared-fit', 'branch_id': branch['branch_id']}], allow_model_execution=True, allow_data_execution=True, executor=executor, investigation_policy=policy, investigation_evidence=evidence, investigation_authority=InvestigationExecutionAuthority(check=lambda request: True, data_classification='synthetic', budget_policy='hard_total_tokens'))
    return {'root': root, 'methods': methods, 'planner': planner, 'plan': plan, 'backend': backend, 'ledger': ledger, 'actions': actions, 'selector': selector, 'executor': executor, 'analysis': analysis, 'inputs': inputs, 'input_bytes': {name: path.read_bytes() for name, path in paths.items()}}

STAGES = ('pre_fit_methods', 'post_fit_explanation')


def fixture_manifest() -> dict:
    """Bind both builder bytes and bounded embedded input specifications."""
    builder = Path(__file__).resolve()
    return {'format': 'devbench.rna-hosted-fixtures/1', 'data_scope': 'synthetic',
        'builder_sha256': hashlib.sha256(builder.read_bytes()).hexdigest(),
        'fixtures': [{'fixture_id': stage + '-' + str(allocation), 'stage': stage,
                      'allocation': allocation, 'asset_sha256': hashlib.sha256(canonical_json({
                          'stage': stage, 'allocation': allocation, 'version': 1,
                          'genes': GENES if stage == 'post_fit_explanation' else ['g1', 'g2'],
                          'samples': 12 if stage == 'post_fit_explanation' else 8})).hexdigest()}
                     for stage in STAGES for allocation in (0, 1, 2)]}


def run_fixture(*, root: Path, stage: str, allocation: int, trace: bool = True, software_identity: dict | None = None) -> dict:
    """Execute only one explicitly selected synthetic control in a fresh root."""
    root = Path(root)
    if not root.is_absolute() or root.is_symlink() or root.resolve() != root:
        raise ValueError('Absolute nonsymlink fixture root required')
    if stage not in STAGES or type(allocation) is not int or allocation not in (0, 1, 2):
        raise ValueError('Unapproved synthetic fixture')
    root.mkdir(parents=True, exist_ok=False)
    if stage == 'post_fit_explanation':
        case = _case(root, allocation=allocation)
    else:
        planner, plan, backend, ledger, actions = independent_setup(root, allocation)
        case = dict(root=root, planner=planner, plan=plan, backend=backend, ledger=ledger,
                    actions=actions, methods=planner.methods, selector=actions.selector)
    if software_identity is not None:
        for key in ('source_digest','runtime_digest'):case['backend'].capability[key]=software_identity[key]
    capability = case['backend'].capabilities()
    from devbench.analysis.workflow_operations import _policy as validate_policy, _json
    observer = LocalTraceObserver(case['methods'].root / 'observer.sqlite',
        source_digest=capability['source_digest'], runtime_digest=capability['runtime_digest'],
        policy_digest=hashlib.sha256(_json(validate_policy(case['actions'].investigation_policy['budget_policy'])).encode()).hexdigest()) if trace else None
    checkpoint = root / 'checkpoint.sqlite'
    with create_rna_workflow(mode='independent_investigations', actions=case['actions'],
            checkpoint_path=checkpoint, investigation_backend=case['backend'], trace_observer=observer) as workflow:
        identity = case['plan']['session_id']
        terminal = workflow.start(case['actions'].create_state(identity))
        while terminal['status'] == 'review_required':
            terminal = workflow.resume(identity)
    case.update(identity=identity, terminal=terminal, checkpoint=checkpoint, observer=observer)
    return case


def scientific_accounting(case: dict) -> dict:
    """Compare observed science and accounting, excluding independently minted IDs/time."""
    campaign = case['ledger'].get_campaign(case['identity'])
    saved = case['planner'].get(case['identity'])
    result = {'status': case['terminal']['status'], 'allocated_children': case['terminal']['allocated_children'],
        'phases': [context['decision_kind'] for context in case['selector'].contexts],
        'scientific_candidates': saved['scientific_candidates'], 'verified_calls': campaign['verified_calls'],
        'verified_spent_tokens': campaign['verified_spent_tokens'], 'unresolved_count': campaign['unresolved_count'],
        'actual_child_count': len(case['backend'].child_contexts), 'fit_calls': 0}
    if 'executor' in case:
        raw = json.loads(case['executor'].raw_output_bytes[0])
        result.update(input_digests=case['executor'].input_digests, fit_calls=case['executor'].executions,
                      gene_statistics=raw['genes'], family=raw['fdr_scope'])
    return result
