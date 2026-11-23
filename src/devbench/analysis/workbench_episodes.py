"""A startup-bound Workbench entry to the existing live analysis episode engine."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import csv
import hashlib
import io
import json
from pathlib import Path
import uuid

from .checkpoints import sha256, digest
from devbench.study.evidence import _read, decode_json


def public_episode_download(bridge, identity, kind):
    """Render a fresh publication projection from the retained synthesis."""
    from .episode_synthesis import public_episode_synthesis
    from .scientific_publication import workspace_guard

    renderer = getattr(bridge, "publication_render", None)
    rendered = renderer(identity, kind) if renderer is not None else None
    if rendered is not None:
        return rendered.release(), ("text/markdown; charset=utf-8" if kind == "summary" else "application/json"), "publication-" + kind
    if kind not in {"summary", "synthesis"}:
        raise PermissionError("restricted_audit_entitlement_required")
    path, _, _ = bridge._retained_download(identity, "synthesis")
    source = decode_json(_read(path, 5_000_000))
    view = public_episode_synthesis(source, guard=workspace_guard(bridge.app.workspace))
    return _episode_public_bytes(view, kind)


def episode_publication_render(bridge, identity, kind):
    """Bind reports to the exact accepted result, independent of saved prose."""
    from .scientific_publication import publication_descriptor_for_source
    if kind not in {"summary", "synthesis", "result"}:
        raise PermissionError("restricted_audit_entitlement_required")
    record = bridge.app._job(identity)
    source = record.get("downloads", {}).get("result")
    if source is None:
        return None
    descriptor = publication_descriptor_for_source(bridge, source["sha256"])
    if descriptor is None:
        return None
    bridge._retained_download(identity, "result")  # Validate owning episode and exact retained bytes.
    registry = getattr(getattr(bridge.app, "scientific", None), "publication_registry", None)
    if registry is None:
        raise PermissionError("publication_subject_required")
    subject_id = record.get("publication_subject_id", descriptor.subject_id)
    with registry.ledger._database() as db:
        db.execute("BEGIN")
        subject, _ = registry._subject(db, subject_id)
        if subject.source_sha256 != source["sha256"]:
            raise PermissionError("publication_source_withheld")
    return registry.render(subject_id, delivery=bridge.app.evidence_delivery,
                           format="summary" if kind == "summary" else "json")


def _episode_public_bytes(view, kind):
    if kind == "synthesis":
        return (json.dumps(view, allow_nan=False, sort_keys=True).encode(),
                "application/json; charset=utf-8", "publication-synthesis.json")
    summary = ("# RNA analysis publication\n\n"
               f"Family entries recorded: {len(view.get('targets', []))}.\n\n"
               "Interpretation requires publication review. See the typed synthesis for coverage.\n")
    return summary.encode(), "text/markdown; charset=utf-8", "publication-summary.md"


class WorkbenchEpisodeBridge:
    """The service queue owns execution; HTTP callers cannot supply code or settings."""
    def __init__(self, app, spec_path):
        from .episodes import EpisodeLimits, normalize_episode_configuration
        from .episode_agent import AgentLimits
        self.app = app
        self.path = Path(spec_path)
        if not self.path.is_absolute() or self.path.resolve() != self.path:
            raise ValueError('Reviewed specification requires a canonical absolute path')
        raw = _read(self.path, 5_000_000)
        spec = decode_json(raw)
        allowed = {'question', 'assay', 'public_task', 'inputs', 'limits', 'fixed_options',
                   'input_kind', 'execution_tier', 'agent_limits'}
        if not isinstance(spec, dict) or set(spec) - allowed or spec.get('assay') != 'bulk':
            raise ValueError('Bind a reviewed bulk analysis episode specification')
        self.limits = EpisodeLimits(**spec.get('limits', {}))
        self.agent_limits = AgentLimits(**spec.get('agent_limits', {}))
        if (max(self.limits.memory_mb, self.agent_limits.memory_mb) > app.parameters['memory_mb']
                or self.limits.stage_timeout_seconds > app.parameters['stage_timeout_seconds']
                or self.agent_limits.session_timeout_seconds > app.parameters['stage_timeout_seconds']
                or max(self.limits.timeout_seconds, self.agent_limits.timeout_seconds) > app.run_timeout_seconds):
            raise ValueError('Reviewed resource ceilings exceed the Workbench startup resource policy')
        bindings = spec.get('inputs')
        if not isinstance(bindings, dict) or set(bindings) != {'expression', 'metadata', 'annotations'}:
            raise ValueError('Bind complete expression, metadata and annotations inputs')
        self.inputs = {}
        for role, supplied in bindings.items():
            path = Path(supplied)
            if not path.is_absolute() or path.is_symlink() or path.resolve() != path:
                raise ValueError('Reviewed inputs require canonical absolute regular paths')
            if not any(path.is_relative_to(root) for root in app.input_roots):
                raise ValueError('Reviewed input is outside this project input roots')
            self.inputs[role] = path
        self.config = normalize_episode_configuration(**{key:value for key,value in spec.items()
            if key not in {'inputs', 'agent_limits', 'limits'}}, limits=self.limits)
        targets = self.config['public_task']['questions']
        if len(targets) != 1 or targets[0]['target_id'] != 'mean_contrast':
            raise ValueError('The Workbench local analyst bridge supports one reviewed mean_contrast target')
        self.binding = {'spec_sha256': hashlib.sha256(raw).hexdigest(),
                        'input_sha256': self._input_identities()}
        with self.inputs['expression'].open(newline='') as handle:
            header_line = handle.readline(1_000_001)
        if len(header_line) > 1_000_000:
            raise ValueError('Expression header exceeds its bounded contract')
        header = next(csv.reader([header_line]))
        metadata = list(csv.DictReader(io.StringIO(_read(self.inputs['metadata'], 5_000_000).decode('utf-8'))))
        if len(metadata) > 100_000:
            raise ValueError('Metadata row budget exceeded')
        unit = self.config['public_task']['population_unit_label']
        self.facts = {'genes':len(self.config['public_task']['gene_ids']), 'libraries':len(header)-1,
                      'biological_units':len({row[unit] for row in metadata}), 'biological_unit_column':unit}
        self.identity = digest({'binding':self.binding, 'configuration':self.config,
                                'agent_limits':vars(self.agent_limits), 'selector':app.selector_config})

    def configuration(self):
        from devbench.platform.workbench_ui import _require_job_publication
        try:
            _require_job_publication(self.app, {"reviewed_binding": self.binding})
        except PermissionError:
            return {"enabled": True, "execution_available": False,
                    "publication_status": "withheld", "publication_reason": "publication_source_withheld"}
        return {'enabled':True, 'spec_sha256':self.binding['spec_sha256'],
                'question':self.config['question'], **self.facts,
                'execution_available':bool(self.app.allow_data_execution and self.app.allow_model_execution
                    and self.app.model_data_scope == 'public'
                    and (self.app.selector_config or {}).get('backend') == 'codex-cli'),
                'scope':'Reviewed fixed-procedure bulk RNA trial; not independent scientific validation'}

    def _input_identities(self):
        hashes, remaining = {}, self.limits.max_input_bytes
        for role, path in self.inputs.items():
            if not path.is_absolute() or path.resolve() != path or path.is_symlink():
                raise ValueError('Reviewed inputs must remain canonical non-symlink bounded regular files')
            bound = min(remaining, 5_000_000 if role in {'metadata', 'annotations'} else self.limits.max_input_bytes)
            content = _read(path, bound)
            remaining -= len(content)
            hashes[role] = hashlib.sha256(content).hexdigest()
        return hashes

    def _check(self):
        from devbench.platform.workbench_ui import _require_job_publication
        _require_job_publication(self.app, {"reviewed_binding": self.binding})
        if not self.path.is_absolute() or self.path.resolve() != self.path or self.path.is_symlink():
            raise ValueError('Reviewed specification must remain canonical and non-symlink')
        raw = _read(self.path, 5_000_000)
        if hashlib.sha256(raw).hexdigest() != self.binding['spec_sha256'] or self._input_identities() != self.binding['input_sha256']:
            raise ValueError('Reviewed specification or input changed; preserve this binding and review a fresh specification')

    def _job(self):
        return next((row for row in self.app._jobs.values()
            if row.get('operation_kind') == 'local_analyst' and row.get('request_identity') == self.identity), None)

    def run(self, request):
        fields = {'spec_sha256', 'allow_data_execution', 'allow_model_execution'}
        if (type(request) is not dict or set(request) != fields
                or request.get('spec_sha256') != self.binding['spec_sha256']
                or request.get('allow_data_execution') is not True or request.get('allow_model_execution') is not True
                or not self.configuration()['execution_available']):
            raise PermissionError('This reviewed public-data trial requires current service and explicit request authority')
        with self.app._lock:
            self._check()
            self.app._require_known_workers()
            prior = self._job()
            if prior is not None:
                return self.status(prior['id'])
            identity = uuid.uuid4().hex
            folder = self.app._path('jobs', identity)
            folder.mkdir(mode=0o700)
            now = datetime.now(timezone.utc).isoformat()
            self.app._jobs[identity] = {'id':identity, 'operation_kind':'local_analyst',
                'request_identity':self.identity, 'question':self.config['question'],
                'source':str(self.inputs['expression']), 'status':'queued', 'created_at':now,
                'updated_at':now, 'worker_liveness':'stopped', 'completed_analysis':False,
                'reviewed_binding':self.binding, 'request_authority':request,
                'runtime_identity':self.app.runtime_identity, 'state_history':[]}
            self.app._update(identity)
            self.app._submit(identity, lambda:self._execute(identity))
            return self.status(identity)

    def _selector(self, episode):
        from .episode_codex import CodexActionSelector
        selected = self.app.proposal_selector
        return CodexActionSelector(requested_model=selected.requested_model,
            workspace_parent=selected.workspace_parent, private_root=self.app.workspace,
            executable=selected.executable, reasoning_effort=selected.reasoning_effort,
            memory_mb=self.agent_limits.memory_mb,
            denied_roots=tuple(dict.fromkeys((*selected.denied_roots, *self.app.input_roots))),
            max_context_bytes=selected.max_context_bytes, protected_model_gate=selected.protected_model_gate,
            trace_sidecar=selected.trace_sidecar)

    def _execute(self, identity):
        from .episodes import AnalysisEpisode
        self._check()
        self.app._update(identity, status='running')
        episode = None
        try:
            episode = AnalysisEpisode.create(self.app._path('jobs', identity, 'episode'),
                **{key:value for key,value in self.config.items() if key != 'limits'},
                limits=self.limits, inputs=self.inputs, allow_data_execution=True)
            self._execute_episode(identity, episode)
        except BaseException as error:
            if episode is not None:
                if episode.outcome is None:
                    episode.abort('workbench_operation_failed:' + type(error).__name__)
                if episode.receipt is not None:
                    self.app._update(identity, episode_receipt=episode.receipt)
            raise

    def _execute_episode(self, identity, episode):
        from .episodes import verify_episode
        from .episode_agent import drive_episode
        response = drive_episode(episode, self._selector(episode), limits=self.agent_limits,
            allow_data_execution=True, allow_model_execution=True)
        if episode.receipt is None:
            self.app._update(identity, status='awaiting_information', information_request=response.get('message'),
                handoff=episode.handoff, completed_analysis=False)
            return
        verified = verify_episode(episode.root, episode.receipt['root_hash'])
        if not verified['valid']:
            raise ValueError('Completed episode evidence failed verification')
        summary = {**self.facts, 'family_genes':self.facts['genes'], 'outcome':episode.outcome,
            'reason':response.get('reason'), 'scientific_correctness':None,
            'biological_agreement':'not_evaluated', 'row_status_counts':{},
            'failed_public_p_q_null':None, 'agent_execution':episode.observation()['agent_execution']}
        downloads = {}
        for name, path in {'summary':episode.root/'summary.md', 'synthesis':episode.root/'synthesis.json'}.items():
            downloads[name] = {'path':str(path), 'sha256':sha256(path)}
        if episode.last_result is not None:
            for target in episode.last_result.get('target_results', []):
                rows = (target.get('payload') or {}).get('gene_statistics', [])
                if rows:
                    summary['row_status_counts'] = dict(Counter(row['estimate_status'] for row in rows))
                    summary['failed_public_p_q_null'] = all(row.get('p_value') is None and row.get('q_value') is None
                        for row in rows if row['estimate_status'] != 'estimated')
            fits = [event for event in episode.broker.events
                    if event['tool'] == 'mean_condition_analysis' and event['status'] == 'succeeded']
            if fits:
                path = episode.broker.output_dir/(fits[-1]['receipt_id']+'.json')
                downloads['result'] = {'path':str(path), 'sha256':sha256(path)}
        self.app._update(identity, status=episode.outcome, episode_receipt=episode.receipt,
            episode_verification=verified, summary=summary, downloads=downloads,
            completed_analysis=episode.outcome == 'completed')

    def status(self, identity=None):
        from devbench.platform.workbench_ui import _publication_status
        if identity is None:
            record = self._job()
            if record is None:
                return {'configuration':self.configuration(), 'job':None}
            identity = record['id']
        record = self.app._status(identity)
        if record.get('operation_kind') != 'local_analyst' or record.get('request_identity') != self.identity:
            raise ValueError('This job does not belong to the reviewed local analyst binding')
        projected = _publication_status(self.app, record)
        if projected.get("publication_status") == "withheld":
            return projected
        actions = []
        for path in sorted(self.app._path('jobs',identity,'episode','agent').glob('selection-*/selection.json')):
            item = decode_json(_read(path, 4_000_000))
            actions.append(item.get('action'))
        record['agent_actions'] = actions
        downloads = record.pop('downloads', {})
        record['download_names'] = [kind for kind in ('summary', 'synthesis') if 'synthesis' in downloads]
        record['plots_available'] = 'result' in downloads
        return _publication_status(self.app, record)

    def _retained_download(self, identity, kind):
        from devbench.platform.workbench_ui import _require_job_publication
        record = self.app._job(identity)
        if record.get('operation_kind') != 'local_analyst' or record.get('request_identity') != self.identity:
            raise KeyError("publication_artifact_unavailable")
        descriptor = record.get('downloads', {}).get(kind)
        if descriptor is None:
            raise KeyError('This retained output is unavailable')
        _require_job_publication(self.app, record, descriptor["sha256"])
        path = Path(descriptor['path'])
        if (path.resolve() != path or path.is_symlink()
                or not path.is_relative_to(self.app._path('jobs',identity,'episode'))):
            raise ValueError('Output escaped the owned episode')
        _read(path, 64_000_000)
        if sha256(path) != descriptor['sha256']:
            raise ValueError('Retained output changed')
        return path, 'text/markdown; charset=utf-8' if kind == 'summary' else 'application/json', path.name

    def download(self, identity, kind):
        """Trusted local evidence read. HTTP routes call public_download."""
        return self._retained_download(identity, kind)

    def public_download(self, identity, kind):
        return public_episode_download(self, identity, kind)

    def publication_render(self, identity, kind):
        return episode_publication_render(self, identity, kind)

    def publication_descriptor(self, identity):
        from .scientific_publication import publication_descriptor_for_source
        record = self.app._job(identity)
        source = record.get("downloads", {}).get("result")
        return publication_descriptor_for_source(self, source["sha256"]) if source else None
