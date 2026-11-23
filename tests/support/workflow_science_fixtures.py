"""Synthetic local workflow helpers for scientific dispatch and recovery tests."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import uuid

from devharness_core.provenance import canonical_json, hash_artifact
from devbench.analysis.workflow_state import digest


class RecordingScientificRuntime:
    """Record immutable control-flow material without endorsing its science."""
    nodes = {"discovery", "candidate_review", "implementation", "design_review", "conclusion_review"}
    maximum_bytes = 2_000_000

    def configuration(self):
        return {"format": "test.offline-engineering-science-runtime/1", "provider": None,
            "model": None, "scientific_claims": "not_established", "qualification": "not_established",
            "independence": "not_established", "purpose": "offline engineering dispatch and recovery only"}

    def _material(self, node, planner, session, plan):
        if node not in self.nodes or plan["session_id"] != session:
            raise ValueError("Fixture node/session binding mismatch")
        sources = []
        for source in plan.get("sources", []):
            ref = planner.methods.get("artifact", source["artifact_id"])
            path = planner.methods.artifact_path(source["artifact_id"])
            if path.stat().st_size > self.maximum_bytes:
                raise ValueError("Fixture source exceeds bounded evidence budget")
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != source["content_sha256"]:
                raise ValueError("Fixture source bytes changed")
            sources.append({key: ref[key] for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")})
        value = {"stage": node, "session_id": session, "planner": deepcopy(plan), "source_refs": sources,
                 "runtime": self.configuration()}
        if len(canonical_json(value)) > self.maximum_bytes:
            raise ValueError("Fixture material exceeds bounded evidence budget")
        return value

    def perform(self, node, planner, session, *, actor, selector):
        plan = planner.get(session)
        material = self._material(node, planner, session, plan)
        status = "descriptive" if node == "discovery" else "accepted"
        record = {"format": "test.offline-engineering-node-evidence/1", "stage": node, "status": status,
            "material": material, "material_sha256": digest(material), "before_planner_hash": digest(plan),
            "after_planner_hash": digest(plan), "engineering_check": "exact immutable fixture material binding only",
            "scientific_claims": "not_established", "qualification": "not_established", "independence": "not_established",
            "search": {"mode": "supplied_only", "search_calls": 0, "provider_calls": 0, "model_calls": 0,
                       "reason": "Explicit offline engineering fixture; no scientific search or endorsement"}}
        directory = Path(planner.methods.root).resolve() / "offline-engineering-fixture-evidence"
        directory.mkdir(exist_ok=True)
        target = directory / (uuid.uuid4().hex + ".json")
        with target.open("xb") as stream:
            stream.write(canonical_json(record))
        artifact = planner.methods.import_artifact(target, artifact_type="json", actor=actor)
        envelope = {"record_ref": {key: artifact[key] for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")},
            **{key: record[key] for key in ("status", "stage", "material_sha256", "before_planner_hash", "after_planner_hash")}}
        with planner.methods._db() as db:
            planner.methods._put(db, "test_offline_science_fixture", artifact["artifact_id"], envelope)
        return envelope

    def verify(self, node, planner, session, evidence, *, plan_snapshot=None):
        ref = evidence["record_ref"]
        actual = planner.methods.get("artifact", ref["artifact_id"])
        if {key: actual[key] for key in ref} != ref or ref["size_bytes"] > self.maximum_bytes:
            raise ValueError("Fixture artifact binding changed")
        path = planner.methods.artifact_path(ref["artifact_id"])
        raw = path.read_bytes()
        if len(raw) != ref["size_bytes"] or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise ValueError("Fixture artifact bytes changed")
        record = json.loads(raw)
        plan = planner.get(session) if plan_snapshot is None else plan_snapshot
        expected = self._material(node, planner, session, plan)
        if (record["material"] != expected or record["material_sha256"] != digest(expected)
                or any(record[key] != evidence[key] for key in ("status", "stage", "material_sha256", "before_planner_hash", "after_planner_hash"))
                or record["before_planner_hash"] != digest(plan) or record["after_planner_hash"] != digest(plan)):
            raise ValueError("Fixture scientific-node material differs from exact retained binding")
        return deepcopy(evidence)

    def reconcile(self, node, planner, session, *, before_planner_hash):
        matches = [item for item in planner.methods.list_records("test_offline_science_fixture")
                   if item["stage"] == node and item["before_planner_hash"] == before_planner_hash]
        if len(matches) != 1:
            raise ValueError("Fixture reconciliation requires one prior immutable envelope")
        return self.verify(node, planner, session, matches[0])


def custom(tmp_path, *, invariants=(), comparator='json'):
    source = tmp_path / 'source.py'
    source.write_text('# A previously unknown user implementation\n')
    lock = tmp_path / 'environment.lock'
    lock.write_text('fixture-runtime=1\n')
    return ({'format': 'devharness.method/1', 'method_id': 'custom.unknown', 'name': 'Unknown QC', 'lifecycle': 'custom', 'implementation_trust': 'unreviewed', 'inputs': {'dataset': {'artifact_type': 'json', 'required': True}}, 'parameters': {'type': 'object', 'additionalProperties': False, 'properties': {'threshold': {'type': 'number', 'exclusiveMinimum': 0}}, 'required': ['threshold']}, 'outputs': {'dataset': {'artifact_type': 'json', 'path': '/outputs/result.json', 'required': True}}, 'implementation': {'kind': 'oci', 'source_sha256': hash_artifact(source)['sha256'], 'entrypoint': ['python', '/method/main.py']}, 'environment': {'image': 'fixture.invalid/python@sha256:' + 'a' * 64, 'lock_sha256': hash_artifact(lock)['sha256']}, 'execution': {'cpu': 1, 'memory_mb': 256, 'timeout_seconds': 5, 'pids': 32, 'max_output_bytes': 1000000, 'network': 'deny'}, 'validation': {'comparator': comparator, 'invariants': list(invariants), 'counts_layer': 'X', 'qc_metrics': []}, 'provenance': {'origin': 'test_fixture'}}, source, lock)


def resume_synthetic_fixture(workflow, identity, terminal):
    """Bound review resumes for synthetic tracing controls."""
    max_resumes = 5
    for _ in range(max_resumes):
        if terminal['status'] != 'review_required':
            break
        terminal = workflow.resume(identity)
    if terminal['status'] == 'review_required':
        failure = terminal.get('failure') or 'no failure reason recorded'
        raise RuntimeError(f"Synthetic fixture remains review_required after {max_resumes} resumes: {failure}")
    return terminal
