"""Native investigation ownership of reviewed, bounded bulk analysis episodes.

HTTP requests provide declarations and grants, never executable specifications.
The existing single service worker performs planning and episode driving directly.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

from .checkpoints import atomic_json, digest, sha256
from devbench.study.evidence import _read, decode_json


def _selection_accounting(evidence):
    """Project observed usage; full provider capture stays in selection.json."""
    if not isinstance(evidence, dict):
        raise ValueError('Retained selection evidence must be an object')
    fields = ('backend', 'actual_model', 'model_identity_verified', 'model_sessions_attempted',
              'tokens', 'cost_usd', 'duration_sec', 'session_id', 'inference_dispatch_attempted',
              'event_capture_complete', 'usage_complete')
    usage_fields = ('input_tokens', 'output_tokens', 'cached_input_tokens', 'reasoning_tokens',
                    'reasoning_output_tokens', 'total_tokens', 'normalized_total_tokens')

    def project(value):
        selected = {name: value[name] for name in fields if name in value}
        if isinstance(value.get('usage'), dict):
            selected['usage'] = {name: value['usage'][name] for name in usage_fields if name in value['usage']}
        return selected

    result = project(evidence)
    for name in ('preflight', 'selection'):
        value = evidence.get(name)
        if isinstance(value, dict):
            result[name] = project(value)
            if isinstance(value.get('billing'), dict):
                result[name]['billing'] = project(value['billing'])
    if len(json.dumps(result, allow_nan=False).encode()) > 8 * 1024:
        raise ValueError('Retained selection accounting exceeds its live size bound')
    return result


def _query_audit_prefix(root):
    from .episode_synthesis import _observation_bindings
    paths = list((root/'audit'/'nodes').glob('*.json'))
    if not paths or len(paths) > 2000:
        raise ValueError('query_audit_prefix_missing_or_unbounded')
    nodes = sorted((decode_json(_read(path, 4_000_000)) for path in paths), key=lambda node: node['sequence'])
    bindings = _observation_bindings(root, [node['node_id'] for node in nodes])
    return nodes, bindings


def _verified_query_rejection(root, number, action):
    """Expose an actual rejected action, without creating a numerical receipt."""
    if not (root/'audit'/'nodes').is_dir():
        return None
    try:
        nodes, _ = _query_audit_prefix(root)
        actions = [(index, node) for index, node in enumerate(nodes)
                   if node['kind'] == 'episode.action' and node['payload'].get('sequence') == number]
        if not actions:
            return None  # A selected action may not have been dispatched yet.
        if len(actions) != 1 or actions[0][1]['payload'].get('action') != action:
            raise ValueError('query_rejection_action_binding_changed')
        following = nodes[actions[0][0] + 1:]
        boundary = next((index for index, node in enumerate(following) if node['kind'] == 'episode.action'), len(following))
        rejected = [node for node in following[:boundary] if node['kind'] == 'episode.action_rejected']
        if not rejected:
            return None
        if len(rejected) != 1 or any(node['kind'] == 'episode.evidence_query'
                and node['payload'].get('sequence') == number for node in nodes):
            raise ValueError('query_rejection_outcome_duplicated')
        node = rejected[0]
        payload = node['payload']
        diagnostic = payload.get('diagnostic')
        if (payload.get('status') != 'rejected' or payload.get('state') != 'needs_revision'
                or payload.get('steps') != number or not isinstance(diagnostic, str)
                or not diagnostic.startswith('evidence_query_rejected:') or len(diagnostic) > 400):
            raise ValueError('query_rejection_diagnostic_binding_invalid')
        if diagnostic not in {'evidence_query_rejected:evidence_query_sample_axis_mismatch'}:
            diagnostic = 'evidence_query_rejected'
        path = root/'audit'/'nodes'/(node['node_id'] + '.json')
        return {'format': 'devbench.query-rejection-view/1', 'status': 'rejected', 'state': 'needs_revision',
            'message': diagnostic, 'reason': diagnostic, 'action': number, 'source_action': action['source_action'],
            'query_sha256': digest(action['query']), 'action_node_id': actions[0][1]['node_id'],
            'audit_ref': {'path': path.relative_to(root).as_posix(), 'node_id': node['node_id'], 'sha256': sha256(path)}}
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ValueError('query_rejection_audit_integrity_failed') from error


def _verified_query_observation(root, number, action, *, terminal, require_current=None):
    """Read a committed query through its immutable audit/source bindings.

    A worker publishes checkpoint files before the audit node. Withhold that
    short-lived uncommitted observation; never trust its adjacent checksum alone.
    No checkpoint constructor is called because status must remain read-only.
    """
    from .episode_evidence_queries import query_result
    from .checkpoints import CheckpointStore
    from .episodes import episode_evidence_limits
    try:
        nodes, bindings = _query_audit_prefix(root)
        matches = [node['payload'] for node in nodes if node['kind'] == 'episode.evidence_query'
                   and node['payload'].get('sequence') == number]
        if not matches and not terminal:
            return None
        if len(matches) != 1:
            raise ValueError('query_audit_binding_missing_or_duplicated')
        bound = matches[0]
        query = root/'checkpoints'/f'query-{number:04d}'/'query-observation.json'
        retained = decode_json(_read(query, 12 * 1024))
        receipt = retained['query_receipt']
        config = decode_json(_read(root/'parameters.json', 5_000_000))
        snapshot = decode_json(_read(root/'snapshot.json', 5_000_000))
        runtime = decode_json(_read(root/'runtime.json', 5_000_000))
        source = action['source_action']
        if type(source) is not int or not 1 <= source < number:
            raise ValueError('query_source_action_invalid')
        original = root/'checkpoints'/f'action-{source:04d}'/'observation.json'
        store = CheckpointStore.__new__(CheckpointStore)
        store.root = root/'checkpoints'
        store.identity = digest({'config': digest(config), 'snapshot': config['snapshot_id'],
            'source': config['source_sha256'], 'python': runtime['runtime']['python'],
            'packages': runtime['runtime']['all_package_versions']})
        if store.verify(f'query-{number:04d}', (f'action-{source:04d}',)) is None:
            raise ValueError('query_checkpoint_missing')
        source_binding = bindings.get(source, {})
        if require_current is not None:
            require_current(source_binding.get('result_sha256'),
                            *(snapshot[role]['sha256'] for role in ('expression', 'metadata', 'annotations')))
        if (digest(snapshot) != config['snapshot_id'] or receipt.get('action') != number
                or receipt.get('source_action') != source or receipt.get('snapshot_id') != config['snapshot_id']
                or receipt.get('query_sha256') != digest(action['query'])
                or bound.get('query_receipt') != receipt or bound.get('response_sha256') != sha256(query)
                or bound.get('checkpoint_receipt', {}).get('sha256') != sha256(query.parent/'receipt.json')
                or source_binding.get('result_sha256') != sha256(original)
                or source_binding.get('checkpoint_receipt', {}).get('sha256') != sha256(original.parent/'receipt.json')
                or receipt.get('source', {}).get('checkpoint_sha256') != sha256(original)
                or receipt.get('source', {}).get('checkpoint_receipt_sha256') != sha256(original.parent/'receipt.json')):
            raise ValueError('query_audit_or_source_binding_changed')
        source_response = decode_json(_read(original, episode_evidence_limits(config)['max_checkpoint_bytes']))
        inputs = {'measurement': config['public_task']['measurement'],
            'public_task': config['public_task'], 'public_task_sha256': digest(config['public_task']),
            'gene_ids': config['public_task']['gene_ids'],
            'sample_id_column': config['public_task'].get('observation_id_label', 'sample_id')}
        if config['public_task'].get('sample_ids'):
            inputs['sample_ids'] = config['public_task']['sample_ids']
        for role, prefix in (('expression', 'counts'), ('metadata', 'metadata'), ('annotations', 'annotation')):
            inputs[prefix+'_path'] = str(root/snapshot[role]['path'])
            inputs[prefix+'_sha256'] = snapshot[role]['sha256']
        rebuilt = query_result(source_response['result'], action['query'], bindings=inputs)
        if retained['query_result'] != rebuilt or receipt.get('response_sha256') != digest(rebuilt):
            raise ValueError('query_response_changed')
        return retained
    except PermissionError:
        raise
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ValueError('query_audit_integrity_failed') from error


def _publication_query(observed, action):
    from .scientific_publication import is_statistic_field

    request = action.get("query") or {}
    sort = request.get("sort_by", "")
    fields = [sort, *(request.get("filters") or {})]
    receipt = observed["query_receipt"]
    projected = {"query_receipt_ref": {"action": receipt["action"], "sha256": digest(receipt)}}
    if request.get("mode") == "aggregate" or any(is_statistic_field(key) for key in fields):
        return {**projected, "query_outcome": {"status": "withheld",
            "reason": "publication_statistics_withheld", "message": "publication_statistics_withheld"}}
    result = observed["query_result"]
    coverage = {key: result[key] for key in ("family_rows", "matched_rows", "total_matches", "returned_rows")
                if type(result.get(key)) is int and result[key] >= 0}
    pairing = result.get("pairing_declarations") or {}
    if type(pairing.get("distinct_declared_units")) is int:
        coverage["pairing_declarations"] = {"distinct_declared_units": pairing["distinct_declared_units"]}
    return {**projected, "query_result": coverage}


class InvestigationEpisodeController:
    def __init__(self, app):
        self.app = app

    def _limits(self):
        from .episodes import EpisodeLimits
        from .episode_agent import AgentLimits
        seconds = min(600, self.app.run_timeout_seconds)
        memory = min(2048, self.app.parameters['memory_mb'])
        stage = min(180, self.app.parameters['stage_timeout_seconds'], seconds)
        return (EpisodeLimits(max_steps=32, timeout_seconds=seconds,
                    stage_timeout_seconds=stage, memory_mb=memory),
                AgentLimits(max_selections=14, timeout_seconds=seconds,
                    session_timeout_seconds=min(90, stage), memory_mb=memory,
                    max_context_bytes=256_000))

    def _authority(self, context, *, execution=False, request=None):
        selected = self.app.selector_config or {}
        if self.app.proposal_selector is None:
            raise PermissionError('Configure an existing proposal selector before starting this analyst')
        if selected.get('backend') == 'codex-cli':
            classification = context.get('data_classification', 'unclassified')
            if (classification not in {'public', 'synthetic'}
                    or classification != self.app.model_data_scope
                    or context.get('allow_model_execution') is not True
                    or self.app.allow_model_execution is not True):
                raise PermissionError('Model planning requires matching public/synthetic dataset, current investigation grant and server authority')
            if execution and (request or {}).get('allow_model_execution') is not True:
                raise PermissionError('Episode driving requires an explicit current model-execution grant')
        if execution and (self.app.allow_data_execution is not True
                          or (request or {}).get('allow_data_execution') is not True):
            raise PermissionError('Episode driving requires current server and request data-execution authority')

    def _selector(self):
        selected = self.app.proposal_selector
        if (self.app.selector_config or {}).get('backend') != 'codex-cli':
            return selected
        from .episode_codex import CodexActionSelector
        _, limits = self._limits()
        return CodexActionSelector(requested_model=selected.requested_model,
            workspace_parent=selected.workspace_parent, private_root=self.app.workspace,
            executable=selected.executable, reasoning_effort=selected.reasoning_effort,
            memory_mb=limits.memory_mb,
            denied_roots=tuple(dict.fromkeys((*selected.denied_roots, *self.app.input_roots))),
            max_context_bytes=limits.max_context_bytes, protected_model_gate=selected.protected_model_gate,
            trace_sidecar=selected.trace_sidecar)

    def prepare(self, record):
        from .workbench_episode_intake import compile_bulk_intake
        # Compilation is local intake only; no selector runs before clarification.
        compiled = compile_bulk_intake(record, output_dir=self.app._path('episode-intakes', record['id']))
        record['episode_intake'] = {key: value for key, value in compiled.items() if key != 'dataset'}
        if compiled['status'] != 'ready':
            record.update(status='awaiting_information' if compiled['status'] == 'clarification' else 'unsupported',
                clarification_fields=compiled.get('clarification_fields', []), required_action='bulk_episode_intake')
            return compiled
        try:
            self._authority(record['scientific_context'])
        except PermissionError as error:
            from .workbench_investigations import _grant_fields
            record.update(status='awaiting_information', clarification_fields=_grant_fields(),
                          required_action='authorize_model_planning')
            record['episode_intake']['authority_issue'] = str(error)
            return compiled
        with self.app._lock:
            self.app._require_known_workers()
            if len(self.app._jobs) >= 100:
                raise ValueError('Job limit reached; preserve history and use a fresh workspace')
            identity = uuid.uuid4().hex
            self.app._path('jobs', identity).mkdir(mode=0o700)
            now = datetime.now(timezone.utc).isoformat()
            binding = {**compiled['binding'], 'job_id': identity,
                'investigation_id': record['id'], 'thread_id': record['thread_id']}
            atomic_json(self.app._path('jobs', identity, 'intake.json'), compiled)
            self.app._jobs[identity] = {'id': identity, 'operation_kind': 'agentic_episode',
                'analyst_profile': 'analyst_v1', 'question': record['question'], 'source': record['source'],
                'investigation_id': record['id'], 'thread_id': record['thread_id'], 'intake_binding': binding,
                'intake_sha256': sha256(self.app._path('jobs', identity, 'intake.json')),
                'scientific_context': record['scientific_context'], 'status': 'queued',
                'created_at': now, 'updated_at': now, 'worker_liveness': 'stopped',
                'completed_analysis': False, 'runtime_identity': self.app.runtime_identity, 'state_history': []}
            self.app._update(identity)
            self.app._submit(identity, lambda: self._plan(identity))
        record.update(job_id=identity, status='queued', required_action=None, clarification_fields=[])
        return compiled

    def _job(self, identity):
        row = self.app._job(identity)
        if row.get('operation_kind') != 'agentic_episode' or row.get('analyst_profile') != 'analyst_v1':
            raise ValueError('This job is not an analyst investigation episode')
        return row

    def _plan(self, identity):
        from .episode_planning import propose_analysis
        from .episode_proposals import _load, _input_identity, proposal_view
        row = self._job(identity)
        self._authority(row['scientific_context'])
        path = self.app._path('jobs', identity, 'intake.json')
        if sha256(path) != row['intake_sha256']:
            raise ValueError('Retained intake changed before planning')
        compiled = decode_json(_read(path, 5_000_000))
        limits, agent_limits = self._limits()
        identities = _input_identity(compiled['dataset']['inputs'], limits.max_input_bytes)
        original = {item['source']: item for item in compiled['binding']['sources']}
        original[compiled['binding']['annotation']['source']] = compiled['binding']['annotation']
        for role, source in compiled['dataset']['inputs'].items():
            expected = original[source]
            if (identities[role]['sha256'] != expected['sha256']
                    or 'size_bytes' in expected and identities[role]['size_bytes'] != expected['size_bytes']):
                raise ValueError('Selected source snapshot changed before planning')
        self.app._update(identity, status='planning')
        result = propose_analysis(self.app._path('jobs', identity, 'planning'),
            question=row['question'], dataset=compiled['dataset'], selector=self._selector(),
            episode_limits=limits, agent_limits=agent_limits, allow_data_read=True,
            allow_model_execution=self.app.allow_model_execution,
            model_data_scope=row['scientific_context'].get('data_classification', 'local_only'))
        fields = {'planning': result, 'model_sessions_attempted': result.get('model_sessions_attempted')}
        if result.get('proposal_receipt'):
            receipt = result['proposal_receipt']
            _, proposal, _ = _load(receipt['manifest_path'], receipt['root_hash'], 'proposal.json')
            if proposal['input_identity'] != identities:
                raise ValueError('Proposal inputs differ from the selected intake snapshots')
            fields.update(status='awaiting_operator_review', proposal=proposal_view(proposal, receipt), proposal_receipt=receipt)
        else:
            fields.update(status=result['status'], information_request=(result.get('decision') or {}).get('message'))
        self.app._update(identity, **fields)

    def review(self, identity, request):
        from .episode_proposals import record_review
        with self.app._lock:
            row = self._job(identity)
            if row.get('operation_pending') or row['status'] != 'awaiting_operator_review':
                raise RuntimeError('Review requires a stopped reviewable proposal')
            receipt = row['proposal_receipt']
            reviewed = record_review(self.app._path('jobs', identity, 'review'), receipt['manifest_path'],
                expected_root=receipt['root_hash'], allow_review=True, **request)
            self.app._update(identity, review_receipt=reviewed, review=request,
                status={'approve': 'approved', 'reject': 'rejected', 'needs_information': 'awaiting_information'}[request['decision']])
        return self.status(identity)

    def run(self, identity, request, *, response=None):
        with self.app._lock:
            row = self._job(identity)
            self._authority(row['scientific_context'], execution=True, request=request)
            self.app._require_known_workers()
            if row.get('operation_pending'):
                raise RuntimeError('This investigation already has an active driver')
            if response is None:
                fresh_resume = (row['status'] == 'cancelled' and row.get('review_receipt')
                                and not self.app._path('jobs', identity, 'episode').exists())
                if row['status'] != 'approved' and not fresh_resume:
                    raise RuntimeError('A reviewed approved proposal is required')
            elif row['status'] not in {'awaiting_information', 'cancelled'} or not row.get('handoff'):
                raise RuntimeError('Clarification requires a stopped episode handoff')
            if response is not None:
                from .episode_handoff import validate_clarification
                validate_clarification(response)
            self.app._update(identity, status='queued', request_authority=request)
            self.app._submit(identity, lambda: self._execute(identity, response=response))
        return self.status(identity)

    def _execute(self, identity, *, response=None):
        from .episode_proposals import begin_reviewed_episode
        from .episode_handoff import resume_handoff
        from .episode_agent import drive_episode
        from .episodes import verify_episode
        row = self._job(identity)
        self._authority(row['scientific_context'], execution=True, request=row['request_authority'])
        self.app._update(identity, status='running')
        episode = None
        try:
            root = self.app._path('jobs', identity, 'episode')
            if response is None:
                proposal, review = row['proposal_receipt'], row['review_receipt']
                episode = begin_reviewed_episode(root, proposal['manifest_path'], review['manifest_path'],
                    expected_proposal_root=proposal['root_hash'], expected_review_root=review['root_hash'],
                    allow_data_execution=True)
            else:
                handoff = row['handoff']
                episode = resume_handoff(root, handoff['manifest_path'], expected_root=handoff['root_hash'],
                    response=response, allow_data_execution=True)
            _, limits = self._limits()
            result = drive_episode(episode, self._selector(), limits=limits, analyst_profile='analyst_v1',
                allow_data_execution=True, allow_model_execution=self.app.allow_model_execution)
            if episode.receipt is None:
                self.app._update(identity, status='awaiting_information', handoff=episode.handoff,
                    information_request=result.get('message'), completed_analysis=False)
                return
            verified = verify_episode(episode.root, episode.receipt['root_hash'])
            if not verified['valid']:
                raise ValueError('Completed episode evidence failed verification')
            downloads = {}
            synthesis = None
            for name, path in {'summary': root/'summary.md', 'synthesis': root/'synthesis.json'}.items():
                if path.is_file():
                    downloads[name] = {'path': str(path), 'sha256': sha256(path), 'size_bytes': path.stat().st_size}
                    if name == 'synthesis':
                        synthesis = decode_json(_read(path, 5_000_000))
            if episode.last_result is not None:
                fits = [event for event in episode.broker.events
                    if event['tool'] == 'mean_condition_analysis' and event['status'] == 'succeeded']
                if fits:
                    path = episode.broker.output_dir/(fits[-1]['receipt_id']+'.json')
                    downloads['result'] = {'path': str(path), 'sha256': sha256(path), 'size_bytes': path.stat().st_size}
            self.app._update(identity, status=episode.outcome, episode_receipt=episode.receipt,
                episode_verification=verified, downloads=downloads, handoff=None,
                synthesis=synthesis,
                summary={'outcome': episode.outcome, 'reason': result.get('reason'),
                    'scientific_correctness': None, 'biological_agreement': 'not_evaluated',
                    'agent_execution': episode.observation()['agent_execution']},
                completed_analysis=episode.outcome == 'completed')
        except BaseException as error:
            if episode is not None and episode.receipt is None and episode.handoff is None:
                episode.abort('workbench_operation_failed:' + type(error).__name__)
            if episode is not None and episode.receipt is not None:
                self.app._update(identity, episode_receipt=episode.receipt)
            raise

    def status(self, identity):
        from devbench.platform.workbench_ui import _publication_status, _require_job_publication
        from .episode_synthesis import public_episode_synthesis
        from .scientific_publication import workspace_guard

        retained_job = self._job(identity)
        if (withheld := _publication_status(self.app, retained_job)).get("publication_status") == "withheld":
            return withheld
        read_status = getattr(self.app, "_status", self.app.status)
        row = read_status(identity)
        row.pop('synthesis', None)
        downloads = row.pop('downloads', {})
        if 'synthesis' in downloads:
            try:
                path, _, _ = self._retained_download(identity, 'synthesis')
                row['synthesis'] = public_episode_synthesis(decode_json(_read(path, 5_000_000)),
                    guard=workspace_guard(self.app.workspace), publication=self.publication_render(identity, "synthesis"))
            except (KeyError, OSError, ValueError, PermissionError):
                row['synthesis_status'] = 'publication_synthesis_withheld'
        if row.get('proposal'):
            from .episode_proposals import proposal_view, verify_proposal_view
            if row['proposal'].get('metadata_projection') == 'complete_family_reference/1':
                verify_proposal_view(row['proposal'], row['proposal_receipt'])
            else:
                row['proposal'] = proposal_view(row['proposal'], row['proposal_receipt'])
        actions = []
        root = self.app._path('jobs', identity, 'episode', 'agent')
        for path in sorted(root.glob('selection-*/selection.json')):
            raw = _read(path, 4_000_000)
            retained = decode_json(raw)
            item = {name: retained[name] for name in ('action', 'decision_record') if name in retained}
            item['evidence'] = _selection_accounting(retained.get('evidence', {}))
            item['selection_ref'] = {'path': path.relative_to(root.parent).as_posix(),
                'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}
            # Query responses are small, independently hash-checked checkpoint outputs.
            number = int(path.parent.name.split('-')[1])
            query = self.app._path('jobs', identity, 'episode', 'checkpoints',
                f'query-{number:04d}', 'query-observation.json')
            receipt_path = query.parent/'receipt.json'
            if query.is_file() and receipt_path.is_file():
                try:
                    observed = _verified_query_observation(self.app._path('jobs', identity, 'episode'),
                        number, item.get('action'), terminal=bool(row.get('episode_receipt')),
                        require_current=lambda *hashes: _require_job_publication(self.app, retained_job, *hashes))
                    if observed is not None:
                        item.update(_publication_query(observed, item.get('action') or {}))
                except PermissionError:
                    item['query_outcome'] = {'status': 'withheld', 'reason': 'publication_source_withheld',
                                             'message': 'publication_source_withheld'}
            elif (item.get('action') or {}).get('kind') == 'query_evidence':
                rejected = _verified_query_rejection(root.parent, number, item['action'])
                if rejected is not None:
                    item['query_outcome'] = rejected
            actions.append(item)
        row['agent_actions'] = [item.get('action') for item in actions]
        row['agent_selections'] = actions
        row['decision_records'] = [item['decision_record'] for item in actions if item.get('decision_record')]
        row['required_confirmations'] = ['question_and_contrast', 'input_provenance',
                                         'biological_units_and_design', 'limitations']
        row['download_names'] = [kind for kind in ('summary', 'synthesis') if 'synthesis' in row]
        from .workbench_episodes import _episode_public_bytes
        row['output_descriptors'] = {}
        for kind in row['download_names']:
            content, _, _ = _episode_public_bytes(row['synthesis'], kind)
            row['output_descriptors'][kind] = {'sha256': hashlib.sha256(content).hexdigest(), 'size_bytes': len(content)}
        row['plots_available'] = 'result' in downloads
        row['resumable'] = bool(not row.get('operation_pending') and (
            row.get('handoff') and row['status'] in {'awaiting_information', 'cancelled'}
            or row['status'] == 'cancelled' and row.get('review_receipt')
                and not self.app._path('jobs', identity, 'episode').exists()))
        return _publication_status(self.app, row)

    def _retained_download(self, identity, name):
        from devbench.platform.workbench_ui import _require_job_publication
        row = self._job(identity)
        descriptor = row.get('downloads', {}).get(name)
        if descriptor is None:
            raise KeyError('This episode output is unavailable')
        _require_job_publication(self.app, row, descriptor["sha256"])
        path = Path(descriptor['path'])
        if (path.resolve() != path or path.is_symlink()
                or not path.is_relative_to(self.app._path('jobs', identity, 'episode'))):
            raise ValueError('Output escaped the owned episode')
        _read(path, 64_000_000)
        if sha256(path) != descriptor['sha256']:
            raise ValueError('Retained episode output changed')
        return path, 'text/markdown; charset=utf-8' if name == 'summary' else 'application/json', path.name

    def download(self, identity, name):
        """Trusted local evidence read. HTTP routes call public_download."""
        return self._retained_download(identity, name)

    def public_download(self, identity, name):
        from .workbench_episodes import public_episode_download
        return public_episode_download(self, identity, name)

    def publication_render(self, identity, kind):
        from .workbench_episodes import episode_publication_render
        return episode_publication_render(self, identity, kind)

    def publication_descriptor(self, identity):
        from .scientific_publication import publication_descriptor_for_source
        source = self._job(identity).get("downloads", {}).get("result")
        return publication_descriptor_for_source(self, source["sha256"]) if source else None
