"""Coordinator-owned primary-run evidence; participant traces remain observations."""
from pathlib import Path, PurePosixPath
import hashlib

from devharness_core.provenance import ExecutionDAG, canonical_json, copy_artifact

INPUT_BYTES = 100_000_000
INPUT_MEMBERS = 50_000


class CodeActionEvidence:
    """One fresh ledger and bounded private snapshots for a primary execution."""
    def __init__(self, output):
        self.output = Path(output)
        self.dag = ExecutionDAG(self.output / 'audit')
        self.parents = []

    def record(self, kind, payload):
        self.parents = [self.dag.append(kind, payload, parents=self.parents)]

    def begin(self, metadata):
        reference = self.dag.add_artifact(self.output / 'task_spec.snapshot.json',
            max_bytes=metadata['task_spec_size_bytes'], max_entries=1)
        if reference['sha256'] != metadata['task_spec_hash']:
            raise ValueError('Private task snapshot changed before audit capture')
        self.record('run_started', {'execution_path':'code-action',
            'condition':metadata['condition'], 'record_format':metadata['record_format'],
            'code_hash':metadata['code_hash'], 'environment':metadata['environment'],
            'task_spec_hash':metadata['task_spec_hash'], 'clinical_release_allowed':False,
            'artifacts':{'task_spec.snapshot.json':reference}, 'artifact_scope':'coordinator_only'})

    def capture_inputs(self, workspace, protected, runtime_hashes, dataset_hash):
        remaining_bytes, remaining_members = INPUT_BYTES, INPUT_MEMBERS
        references, runtime_references = {}, {}
        for group, expected, retained in (('input-snapshot',protected,references),
                                          ('runtime-source',runtime_hashes,runtime_references)):
            for name, digest in sorted(expected.items()):
                member = PurePosixPath(name)
                if (not name or member.is_absolute() or name != member.as_posix()
                        or '\\' in name or any(p in ('.','..') for p in member.parts)):
                    raise ValueError('Unsafe capture member')
                source = workspace / name
                if source.is_symlink() or not source.resolve().is_relative_to(workspace):
                    raise ValueError('Capture source escapes the task workspace')
                if remaining_members < 1:
                    raise ValueError('Input capture member limit exceeded')
                destination = self.output / group / name
                destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                copied = copy_artifact(source, destination, max_bytes=remaining_bytes,
                    max_entries=remaining_members-1 if source.is_dir() else remaining_members)
                remaining_bytes -= copied['size_bytes']
                remaining_members -= 1 + len(copied.get('entries', []))
                if copied['sha256'] != digest:
                    raise ValueError('Input changed before private capture: ' + name)
                reference = self.dag.add_artifact(destination, max_bytes=copied["size_bytes"],
                    max_entries=len(copied["entries"]) if copied["kind"] == "directory" else 1)
                if reference != copied:
                    raise ValueError('Private snapshot changed before audit capture: ' + name)
                retained[name] = reference
        self.record('inputs_staged', {'execution_path':'code-action',
            'workspace':str(workspace), 'artifacts':references, 'runtime_sources':runtime_references,
            'dataset_hash':dataset_hash, 'capture_payload_bytes':INPUT_BYTES-remaining_bytes,
            'capture_members':INPUT_MEMBERS-remaining_members,
            'capture_limits':{'payload_bytes':INPUT_BYTES,'members':INPUT_MEMBERS},
            'scope':'preserved pre-execution inputs and SDK sources; not hostile-process isolation',
            'tool_capture_complete':False})

    def finish(self, result, artifacts):
        captured = {}
        for name, record in artifacts.items():
            if not record.get('preserved'):
                continue
            reference = self.dag.add_artifact(Path(record['path']), max_bytes=record['bytes'], max_entries=1)
            if reference['sha256'] != record['sha256'] or reference['size_bytes'] != record['bytes']:
                raise ValueError('Retained output changed before audit capture: ' + name)
            captured[name] = reference
        observations = self.output / 'cli-observations.json'
        if observations.is_file():
            captured['cli-observations.json'] = self.dag.add_artifact(observations, max_bytes=16_000_000, max_entries=1)
        self.record('execution_observed', {'artifacts':captured,
            'input_integrity':result['metadata'].get('input_integrity'),
            'scope':'adapter-reported CLI observations and participant-written artifacts; not authenticated tool events',
            'tool_capture_complete':False})
        outcome = self.output / 'outcome.snapshot.json'
        payload = canonical_json(result)
        with outcome.open('xb') as stream:
            stream.write(payload)
        reference = self.dag.add_artifact(outcome, max_bytes=len(payload), max_entries=1)
        if reference['sha256'] != hashlib.sha256(payload).hexdigest():
            raise ValueError('Captured outcome differs from the computed result')
        self.record('run_finished', {'status':'success' if result['passed'] else 'failed',
            'failure_type':result['metadata'].get('failure_type'),
            'error':result['metadata'].get('error'), 'grader_result':result['grader_result'],
            'clinical_release_allowed':False, 'artifacts':{'outcome.snapshot.json':reference}})
        return self.dag.finalize()
