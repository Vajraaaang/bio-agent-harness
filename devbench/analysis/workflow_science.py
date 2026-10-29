"""Narrow scientific services for mandatory workflow nodes.

Configuration describes frozen identities, never authorization. Receipts retain
real adapter output and exact saved planner snapshots. An uncertain operation
cannot dispatch again; recovery reads only a completed immutable artifact.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path

from .checkpoints import atomic_json, digest
from .episode_agent import Selection, selector_configuration
from .method_executor import _path
from .workflow_discovery import (CrossrefDiscoveryProvider, GitHubRepositoryDiscoveryProvider,
    OfficialDocumentationDiscoveryProvider, discover, load_discovery)
from .workflow_implementation import perform_implementation, verify_implementation_validation
from .workflow_scientific_reviews import (
    STAGES, WorkflowScientificReviewAdapter, _verify_plan_sources, review,
    stage_material, verify_review,
)
from devbench.study.evidence import _read, decode_json
from devharness_core.provenance import canonical_json

_FORMAT = 'devbench.workflow-scientific-runtime/1'
_REVIEW_STAGES = {'candidate_review': 'candidate_suitability', 'design_review': 'comparison_design',
    'conclusion_review': 'results_conclusions'}
_NODES = {'discovery', 'implementation', *_REVIEW_STAGES}
_REF_KEYS = ('artifact_id', 'sha256', 'artifact_type', 'size_bytes')
_ENVELOPE_KEYS = {'record_ref', 'status', 'stage', 'material_sha256', 'before_planner_hash', 'after_planner_hash'}
_MAX_BYTES = 4_000_000


def _copy(value):
    return decode_json(canonical_json(value))


def _snapshot(planner, identity, session):
    value = planner.methods.get('method_plan_snapshot', identity)
    if digest(value) != identity or value.get('session_id') != session:
        raise ValueError('Scientific runtime saved planner snapshot binding differs')
    return value


def _load_record(planner, evidence):
    if type(evidence) is not dict or set(evidence) != _ENVELOPE_KEYS:
        raise ValueError('Use an exact scientific runtime envelope')
    ref = evidence['record_ref']
    if type(ref) is not dict or set(ref) != set(_REF_KEYS):
        raise ValueError('Scientific runtime requires an immutable artifact reference')
    path = planner.methods.artifact_path(ref['artifact_id'])
    descriptor = planner.methods.get('artifact', ref['artifact_id'])
    if {key: descriptor[key] for key in _REF_KEYS} != ref or ref['size_bytes'] > _MAX_BYTES:
        raise ValueError('Scientific runtime artifact identity changed')
    content = _read(path, _MAX_BYTES)
    if hashlib.sha256(content).hexdigest() != ref['sha256'] or len(content) != ref['size_bytes']:
        raise ValueError('Scientific runtime artifact bytes changed')
    record = decode_json(content)
    if record.get('format') != _FORMAT:
        raise ValueError('Scientific runtime evidence format differs')
    for key in _ENVELOPE_KEYS - {'record_ref'}:
        if evidence[key] != record.get(key):
            raise ValueError('Scientific runtime envelope binding differs')
    if digest(record['configuration']) != record['configuration_sha256']:
        raise ValueError('Scientific runtime frozen configuration changed')
    return record


def _implementation_material(plan):
    return {key: deepcopy(plan.get(key)) for key in
        ('context', 'sources', 'scientific_candidates', 'candidates', 'implementation_drafts')}


def _implementation_relevance(plan):
    material = _implementation_material(plan)
    for candidate in material.get('candidates') or []:
        for key in ('applicability', 'blockers', 'prerequisite_inspections'):
            candidate.pop(key, None)
        for assumption in candidate.get('assumptions', []):
            assumption.pop('status', None)
            assumption.pop('evidence', None)
    return material


def _verify_implementation(planner, report, before, after, requests):
    body = deepcopy(report)
    expected_digest = body.pop('decision_sha256')
    if digest(body) != expected_digest or report.get('format') != 'devbench.workflow-implementation/1':
        raise ValueError('Implementation report digest differs')
    if report['execution_granted'] is not False or report['promotion_granted'] is not False:
        raise ValueError('Implementation report cannot grant execution or promotion')
    drafts = report['drafts']
    by_name = {item['candidate']: item for item in requests}
    previous = {item['candidate']: item for item in before.get('implementation_drafts', [])}
    expected = deepcopy(before)
    new_drafts = []
    for draft in drafts:
        name, identity = draft['candidate'], draft['method_version_id']
        request = by_name.get(name)
        if request is None or request.get('authorized') is not True or not request.get('justification'):
            raise ValueError('Implementation draft lacks explicit justified request')
        method = planner.methods.method(identity)
        contract = method['contract']
        source = planner.methods.artifact_path(method['source_artifact'])
        lock = planner.methods.artifact_path(method['environment_artifact'])
        requested_lock = _path(request['environment_lock'])
        if (hashlib.sha256(_read(source, 1_000_000)).hexdigest() != contract['implementation']['source_sha256']
                or hashlib.sha256(_read(lock, 1_000_000)).hexdigest() != contract['environment']['lock_sha256']
                or hashlib.sha256(_read(requested_lock, 1_000_000)).hexdigest() != contract['environment']['lock_sha256']
                or method['source_artifact'] != draft['source_artifact']
                or digest(request['interface']) != draft['interface_sha256']
                or contract['implementation']['kind'] != 'oci'
                or contract['implementation_trust'] != 'unreviewed' or contract['lifecycle'] != 'custom'):
            raise ValueError('Implementation source, environment lock or unreviewed identity changed')
        if name in previous:
            if previous[name] != draft:
                raise ValueError('Retained implementation draft changed')
        else:
            new_drafts.append(draft)
            expected.setdefault('implementation_drafts', []).append(deepcopy(draft))
            expected['registry'][identity] = deepcopy(contract)
            candidate = next(item for item in expected['candidates'] if item['name'] == name)
            candidate.update(method_version_id=identity, availability='workspace_custom', implementation_confidence='low')
        if request.get('validation') is not None:
            proof = verify_implementation_validation(planner.methods, identity, request['validation'])
            if proof not in report['validations']:
                raise ValueError('Implementation validation proof is absent')
        elif not any(item['method_version_id'] == identity for item in report['validation_required']):
            raise ValueError('Unvalidated implementation cannot become ready')
    if new_drafts:
        history = after.get('selection_history', [])
        baseline = before.get('selection_history', [])
        if history[:len(baseline)] != baseline or len(history) != len(baseline) + len(new_drafts):
            raise ValueError('Implementation selection history delta differs')
        if any(item.get('phase') != 'method_implementation_draft' or item.get('status') != 'accepted'
                for item in history[len(baseline):]):
            raise ValueError('Implementation draft lacks actual accepted selection evidence')
        expected['selection_history'] = deepcopy(history)
        expected['selection_in_progress'] = None
    if after != expected:
        raise ValueError('Implementation planner snapshot delta exceeds declared drafts')
    # Relevance includes the exact resolved candidates and retained draft identities.
    current = planner.get(after['session_id'])
    if _implementation_relevance(current) != _implementation_relevance(after):
        raise ValueError('Implementation material is stale')
    expected_status = 'needs_validation' if report['validation_required'] else 'unavailable' if report['unavailable'] else 'ready'
    if report['status'] != expected_status:
        raise ValueError('Implementation outcome differs from retained evidence')
    return {'ready': 'completed', 'unavailable': 'descriptive', 'needs_validation': 'blocked'}[expected_status]


def verify_scientific_evidence(node, planner, session, evidence, *, plan_snapshot):
    """Read-only, authority-free validation of saved material and real services."""
    if node not in _NODES:
        raise ValueError('Unknown scientific workflow node')
    # A software-backend qualification is a separately typed protocol, never a
    # substitute biological discovery/reviewer/OCI-validation receipt.
    if type(evidence) is dict and type(evidence.get('record_ref')) is dict:
        reference = evidence['record_ref']
        if set(reference) == set(_REF_KEYS):
            path = planner.methods.artifact_path(reference['artifact_id'])
            body = _read(path, _MAX_BYTES)
            if (hashlib.sha256(body).hexdigest() != reference['sha256']
                    or len(body) != reference['size_bytes']):
                raise ValueError('Scientific or protocol evidence bytes changed')
            record = decode_json(body)
            if record.get('format') == 'devbench.nonbiological-protocol-check/1':
                from .workflow_protocol_qualification import verify_protocol_qualification_evidence
                return verify_protocol_qualification_evidence(node, planner, session, evidence,
                    plan_snapshot=plan_snapshot)
    record = _load_record(planner, evidence)
    if record['node'] != node or record['session_id'] != session:
        raise ValueError('Scientific runtime node or session differs')
    before = _snapshot(planner, record['before_planner_hash'], session)
    after = _snapshot(planner, record['after_planner_hash'], session)
    if after != plan_snapshot or digest(plan_snapshot) != record['after_planner_hash']:
        raise ValueError('Scientific runtime requires the exact saved AFTER planner snapshot')
    _verify_plan_sources(planner, before)
    _verify_plan_sources(planner, after)
    service = record['service_evidence']
    if node == 'discovery':
        discovery = load_discovery(planner, service, session_id=session)
        if any(discovery[key] != record[key] for key in ('before_planner_hash', 'after_planner_hash', 'status')):
            raise ValueError('Discovery runtime snapshot binding differs')
        current = planner.get(session)
        if (current['sources'] != after['sources'] or current['context'] != after['context']):
            raise ValueError('Discovery material is stale; explicit reassessment required')
        material = digest(discovery['context'])
        status, stage = discovery['status'], 'discovery'
    elif node in _REVIEW_STAGES:
        stage = _REVIEW_STAGES[node]
        if before != after:
            raise ValueError('Scientific review cannot mutate the planner snapshot')
        review_record = verify_review(planner, session, stage, service, plan_snapshot=after,
            policy=record['configuration']['review_policy'])
        if review_record['plan_sha256'] != digest(after):
            raise ValueError('Scientific review planner snapshot differs')
        material, status = digest(stage_material(after, stage)), review_record['status']
    else:
        stage = 'implementation'
        status = _verify_implementation(planner, service, before, after, record['implementation_requests'])
        material = digest(_implementation_material(after))
        if digest(record['implementation_requests']) != record['configuration']['implementation_requests_sha256']:
            raise ValueError('Implementation requests differ from frozen configuration')
    if (record['material_sha256'] != material or record['status'] != status or record['stage'] != node):
        raise ValueError('Scientific runtime material, stage or outcome differs')
    return record


class ScientificWorkflowRuntime:
    def __init__(self, *, discovery_provider, discovery_request, reviewer, review_policy,
                 implementation_requests=(), allow_network=False, allow_review_model_execution=False,
                 allow_implementation_model_execution=False):
        self.discovery_provider = discovery_provider
        self.discovery_request = _copy(discovery_request)
        self.reviewer = reviewer
        self.review_policy = _copy(review_policy)
        self.implementation_requests = _copy(list(implementation_requests))
        self.allow_network = allow_network is True
        self.allow_review_model_execution = allow_review_model_execution is True
        self.allow_implementation_model_execution = allow_implementation_model_execution is True
        self._configuration = self._describe()

    def _describe(self):
        reviewer = self.reviewer
        review_identity = None
        if reviewer is not None:
            if not isinstance(reviewer, WorkflowScientificReviewAdapter):
                raise TypeError('Use the real scientific-review runtime adapter')
            review_identity = {'reviewer_label': reviewer.reviewer_label,
                'selector': selector_configuration(reviewer.selector),
                'evaluation_policy_sha256': reviewer.expected_policy_sha256,
                'evaluation_policy': reviewer.evaluation_policy.model_dump(mode='json'),
                'limits': asdict(reviewer.limits),
                'claim_packets': deepcopy(getattr(reviewer, 'configured_claim_packets', None))}
        return _copy({'format': 'devbench.workflow-scientific-configuration/1',
            'discovery_provider': None if self.discovery_provider is None else self.discovery_provider.configuration(),
            'discovery_request': self.discovery_request, 'discovery_request_sha256': digest(self.discovery_request),
            'reviewer': review_identity, 'review_policy': self.review_policy,
            'review_policy_sha256': digest(self.review_policy),
            'implementation_requests_sha256': digest(self.implementation_requests)})

    def configuration(self):
        if self._describe() != self._configuration:
            raise ValueError('Scientific runtime configuration or frozen policy changed')
        return deepcopy(self._configuration)

    def _operation(self, node, planner, session, before_hash):
        if node not in _NODES:
            raise ValueError('Unknown scientific workflow node')
        identity = digest({'session_id': session, 'node': node, 'before_planner_hash': before_hash})
        return _path(planner.methods.root / 'workflow-science' / identity)

    def verify(self, node, planner, session, evidence, *, plan_snapshot=None):
        config = self.configuration()
        record = verify_scientific_evidence(node, planner, session, evidence,
            plan_snapshot=planner.get(session) if plan_snapshot is None else plan_snapshot)
        if record['configuration'] != config or record['configuration_sha256'] != digest(config):
            raise ValueError('Scientific runtime frozen configuration differs from evidence')
        return record

    def reconcile(self, node, planner, session, *, before_planner_hash):
        """Never search, review or draft during recovery."""
        config = self.configuration()
        root = self._operation(node, planner, session, before_planner_hash)
        index = _path(root / 'completed.json')
        if not index.is_file():
            marker = _path(root / 'started.json')
            if not marker.is_file():
                raise ValueError('Scientific operation uncertain or absent; explicit reconciliation required before redispatch')
            started = decode_json(_read(marker, 128_000))
            if (started.get('session_id') != session or started.get('node') != node
                    or started.get('before_planner_hash') != before_planner_hash
                    or started.get('configuration_sha256') != digest(config)):
                raise ValueError('Scientific reconciliation operation binding differs')
            path = _path(root / 'evidence.json')
            if path.is_file():
                artifact = planner.methods.import_artifact(path, artifact_type='json', actor=started['actor'])
                retained = decode_json(_read(path, _MAX_BYTES))
                evidence = {'record_ref': {key: artifact[key] for key in _REF_KEYS},
                    **{key: retained[key] for key in _ENVELOPE_KEYS - {'record_ref'}}}
                self.verify(node, planner, session, evidence,
                    plan_snapshot=_snapshot(planner, evidence['after_planner_hash'], session))
                atomic_json(index, evidence)
            elif node in _REVIEW_STAGES and self.reviewer is not None:
                before = _snapshot(planner, before_planner_hash, session)
                if planner.get(session) != before:
                    raise ValueError('Scientific review reconciliation snapshot changed')
                service = self.reviewer.reconcile(planner, session, stage=_REVIEW_STAGES[node],
                    policy=self.review_policy, actor=started['actor'])
                if service is None:
                    raise ValueError('Scientific review dispatch uncertain; explicit reconciliation required')
                return self._complete(node, planner, session, before, service,
                    actor=started['actor'], root=root)
            else:
                raise ValueError('Scientific operation uncertain; explicit reconciliation required before redispatch')
        evidence = decode_json(_read(index, 128_000))
        snapshot = _snapshot(planner, evidence['after_planner_hash'], session)
        self.verify(node, planner, session, evidence, plan_snapshot=snapshot)
        if evidence['before_planner_hash'] != before_planner_hash:
            raise ValueError('Scientific recovery baseline differs')
        return evidence

    def _complete(self, node, planner, session_id, before, service, *, actor, root):
        config = self.configuration()
        if node == 'discovery':
            discovery = load_discovery(planner, service, session_id=session_id)
            material, status = digest(discovery['context']), service['status']
        elif node in _REVIEW_STAGES:
            material, status = service['material_sha256'], service['status']
        else:
            material = digest(_implementation_material(planner.get(session_id)))
            status = {'ready': 'completed', 'unavailable': 'descriptive', 'needs_validation': 'blocked'}[service['status']]
        after = planner.get(session_id)
        _snapshot(planner, digest(after), session_id)
        record = {'format': _FORMAT, 'node': node, 'session_id': session_id, 'status': status, 'stage': node,
            'material_sha256': material, 'before_planner_hash': digest(before), 'after_planner_hash': digest(after),
            'configuration': config, 'configuration_sha256': digest(config), 'service_evidence': service,
            'implementation_requests': self.implementation_requests if node == 'implementation' else []}
        path = _path(root / 'evidence.json')
        with path.open('xb') as stream:
            stream.write(canonical_json(record))
            stream.flush()
            os.fsync(stream.fileno())
        artifact = planner.methods.import_artifact(path, artifact_type='json', actor=actor)
        evidence = {'record_ref': {key: artifact[key] for key in _REF_KEYS},
            **{key: record[key] for key in _ENVELOPE_KEYS - {'record_ref'}}}
        self.verify(node, planner, session_id, evidence, plan_snapshot=after)
        atomic_json(root / 'completed.json', evidence)
        return evidence

    def perform(self, node, planner, session_id, *, actor, selector):
        config = self.configuration()
        before = planner.get(session_id)
        before_hash = digest(before)
        _snapshot(planner, before_hash, session_id)
        root = self._operation(node, planner, session_id, before_hash)
        root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            root.mkdir(mode=0o700)
        except FileExistsError:
            return self.reconcile(node, planner, session_id, before_planner_hash=before_hash)
        with _path(root / 'started.json').open('xb') as stream:
            stream.write(canonical_json({'session_id': session_id, 'node': node,
                'before_planner_hash': before_hash, 'configuration_sha256': digest(config), 'actor': actor}))
            stream.flush()
            os.fsync(stream.fileno())
        if node == 'discovery':
            service = discover(planner, session_id, provider=self.discovery_provider,
                request=self.discovery_request, actor=actor, allow_network=self.allow_network)
            discovery = load_discovery(planner, service, session_id=session_id)
            material, status, stage = digest(discovery['context']), service['status'], 'discovery'
        elif node in _REVIEW_STAGES:
            stage = _REVIEW_STAGES[node]
            service = review(planner, session_id, stage=stage, reviewer=self.reviewer,
                policy=self.review_policy, actor=actor, allow_model_execution=self.allow_review_model_execution)
            if service.get('record_ref') is None:
                raise ValueError('Substantive scientific review adapter is required; no fallback evidence')
            material, status = service['material_sha256'], service['status']
        else:
            service = perform_implementation(planner, session_id, selector, actor=actor,
                requests=self.implementation_requests, allow_model_execution=self.allow_implementation_model_execution)
            material, stage = digest(_implementation_material(planner.get(session_id))), 'implementation'
            status = {'ready': 'completed', 'unavailable': 'descriptive', 'needs_validation': 'blocked'}[service['status']]
        return self._complete(node, planner, session_id, before, service, actor=actor, root=root)

    def generation_context(self, planner, session):
        """Return only the actual discovery service envelope; never arbitrary context."""
        root = _path(planner.methods.root / 'workflow-science')
        matches = []
        if root.exists():
            paths = list(root.glob('*/completed.json'))
            if len(paths) > 256:
                raise ValueError('Scientific recovery index exceeds its bounded scan')
            for path in paths:
                evidence = decode_json(_read(_path(path), 128_000))
                record = _load_record(planner, evidence)
                if record['node'] != 'discovery' or record['session_id'] != session:
                    continue
                snapshot = _snapshot(planner, evidence['after_planner_hash'], session)
                self.verify('discovery', planner, session, evidence, plan_snapshot=snapshot)
                if evidence['status'] not in {'completed', 'descriptive'}:
                    raise ValueError('Discovery is blocked; generation requires resolved evidence')
                matches.append(record['service_evidence'])
        if len(matches) != 1:
            raise ValueError('Generation requires exactly one verified discovery operation')
        return deepcopy(matches[0])


class _RecordedReviewer:
    """Explicit offline fixtures carry no live model-execution authority."""
    def __init__(self, actions):
        if type(actions) is not list or not 1 <= len(actions) <= 32 or any(type(item) is not dict for item in actions):
            raise ValueError('Recorded reviewer decisions must be a bounded JSON list of actions')
        self.actions = _copy(actions)
        self.position = 0
        self.identity = digest(actions)

    def configuration(self):
        return {'backend': 'recorded_fixture', 'decisions_sha256': digest(self.actions)}

    def select(self, context, output, **kwargs):
        if self.position >= len(self.actions):
            raise ValueError('Recorded scientific reviewer decisions exhausted')
        action = deepcopy(self.actions[self.position])
        self.position += 1
        return Selection(action, {'model_sessions_attempted': 0, 'tokens': None,
            'cost_usd': None, 'actual_model': 'offline_fixture'})


def load_scientific_workflow_config(path, *, methods_root, selector, allow_network=False,
        allow_review_model_execution=False, allow_implementation_model_execution=False):
    """Load data-only fixed adapters; JSON cannot grant network/model authority."""
    from .scientific_judgment import parse_claim_packet, EvaluationPolicy, verified_artifacts
    path, methods_root = _path(path), _path(methods_root)
    config = decode_json(_read(path, 1_000_000))
    required = {'provider', 'discovery_request', 'review_policy', 'reviewer'}
    if type(config) is not dict or not required <= set(config) or set(config) - required - {'implementation_requests'}:
        raise ValueError('Use the exact scientific workflow configuration schema')
    selection = config['provider']
    if selection == 'supplied_only':
        provider = None
    elif selection == 'crossref':
        provider = CrossrefDiscoveryProvider(methods_root / 'crossref-discovery')
    elif type(selection) is dict and selection.get('kind') == 'github_repositories':
        if not {'kind', 'owners'} <= set(selection) or set(selection) - {'kind', 'owners', 'max_results'}:
            raise ValueError('Use the exact GitHub repository provider configuration')
        provider = GitHubRepositoryDiscoveryProvider(methods_root / 'github-discovery',
            owners=selection['owners'], max_results=selection.get('max_results', 3))
    elif type(selection) is dict and selection.get('kind') == 'official_documentation':
        if (not {'kind', 'index_url', 'official_hosts'} <= set(selection)
                or set(selection) - {'kind', 'index_url', 'official_hosts', 'index_format', 'max_results'}):
            raise ValueError('Use the exact official documentation provider configuration')
        provider = OfficialDocumentationDiscoveryProvider(methods_root / 'documentation-discovery',
            index_url=selection['index_url'], official_hosts=selection['official_hosts'],
            index_format=selection.get('index_format', 'html'), max_results=selection.get('max_results', 3))
    else:
        raise ValueError('Only fixed supplied_only, Crossref, scoped GitHub and official documentation adapters are supported')
    if type(config['discovery_request']) is not dict:
        raise ValueError('Discovery request must be an explicit bounded object')
    if (provider is None) != (config['discovery_request'].get('mode') == 'supplied_only'):
        raise ValueError('Discovery provider and request mode differ')
    settings = config['reviewer']
    reviewer_keys = {'claim_packets', 'reviewer_label', 'evaluation_policy', 'expected_policy_sha256'}
    if type(settings) is not dict or not reviewer_keys <= set(settings) or set(settings) - reviewer_keys - {'decisions'}:
        raise ValueError('Use the exact reviewer configuration schema')
    packet_paths = settings['claim_packets']
    if type(packet_paths) is not dict or set(packet_paths) - set(STAGES):
        raise ValueError('Claim packets must use explicit supported scientific review stages')
    packet_ids = {stage: {'path': str(_path(packet_path))} for stage, packet_path in packet_paths.items()}
    input_references = {}
    def builder(planner, session, stage, material):
        if stage not in packet_ids:
            raise ValueError('Missing explicit claim packet for scientific review stage ' + stage)
        identity = packet_ids[stage]
        raw = _read(_path(identity['path']), 1_000_000)
        packet = parse_claim_packet(raw)
        if packet.input_root != digest(material):
            raise ValueError('Configured claim packet must bind actual current stage material')
        verified_artifacts(packet)
        input_references[stage] = {'path': identity['path'], 'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}
        return packet
    def input_reference(stage):
        if stage not in input_references:
            raise ValueError('Stage claim packet has not been verified')
        return deepcopy(input_references[stage])
    builder.input_reference = input_reference
    reviewer_selector = selector
    if 'decisions' in settings:
        reviewer_selector = _RecordedReviewer(decode_json(_read(_path(settings['decisions']), 1_000_000)))
    reviewer = WorkflowScientificReviewAdapter(output_root=methods_root / 'workflow-scientific-reviews',
        selector=reviewer_selector, claim_packet_builder=builder, reviewer_label=settings['reviewer_label'],
        evaluation_policy=EvaluationPolicy.model_validate_json(canonical_json(settings['evaluation_policy'])),
        expected_policy_sha256=settings['expected_policy_sha256'])
    reviewer.configured_claim_packets = packet_ids
    return ScientificWorkflowRuntime(discovery_provider=provider, discovery_request=config['discovery_request'],
        reviewer=reviewer, review_policy=config['review_policy'], implementation_requests=config.get('implementation_requests', ()),
        allow_network=allow_network, allow_review_model_execution=allow_review_model_execution,
        allow_implementation_model_execution=allow_implementation_model_execution)
