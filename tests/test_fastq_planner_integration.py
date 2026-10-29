"""Existing durable graph over FASTQ references; offline software evidence only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile

import pytest

from devbench.analysis.fastq_planner_binding import (
    build_fastq_manifest, register_fastq_input, load_fastq_manifest, validate_fastq_manifest,
)
from devbench.analysis.method_planner import ScientificMethodPlanner
from devbench.analysis.method_planner_cli import main as planner_cli
from devbench.analysis.methods import MethodService
from devbench.analysis.workflow_entrypoints import audit_workflow_design, workflow_action
from tests.test_fastq_contract import plan_fixture, rows, write_csv
from tests.test_declared_bulk_analyst_workflow import DeclaredBulkSelector, ACTOR
from tests.support.workflow_science_fixtures import RecordingScientificRuntime
from devharness_core.provenance import canonical_json


@pytest.fixture(autouse=True)
def explicit_fake_nextflow_pin(monkeypatch):
    """Only software fixtures admit these fake bytes; production requires the official distribution."""
    from devbench.analysis import fastq_execution
    monkeypatch.setattr(fastq_execution, "NEXTFLOW_DIST_SHA256",
        hashlib.sha256(b"#!/bin/sh\nexit 99\n").hexdigest())


@pytest.fixture
def workspace():
    parent = Path(__file__).resolve().parents[1] / 'reports' / 'rna-agentic-integration-2026-10-28'
    parent.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix='fastq-workflow-', dir=parent))


def fixture_manifest(root, mutate=None):
    source = plan_fixture(root)
    if mutate:
        mutate(source)
    executable = root / 'nextflow'
    executable.write_text('#!/bin/sh\nexit 99\n')
    return build_fastq_manifest(source, workspace=root, execution={'resources': {'cpus': 1, 'memory_gb': 1},
        'timeout_seconds': 60, 'execution_profile': 'minimal-counts', 'nextflow_path': str(executable),
        'nextflow_sha256': hashlib.sha256(executable.read_bytes()).hexdigest()})


def declaration():
    return {'format': 'devbench.rna-design/1', 'objective': {'intent': 'infer'},
        'design': {'contrasts': [{'field': 'condition', 'reference': 'A', 'target': 'B'}],
            'covariates': ['batch'], 'chronological_field': None, 'chronological_order': []},
        'selected_sample_ids': [row['sample_id'] for row in rows()],
        'source': {'kind': 'fixture', 'reference': 'test_fastq_planner_integration',
            'statement': 'Hand-authored software fixture. Compare B minus A retaining batch.'}}


def create_plan(root, manifest):
    methods = MethodService(root / 'methods', reconcile=False)
    inspection = register_fastq_input(methods, manifest, actor=ACTOR)
    binding = {'format': 'devbench.fastq-input-binding/1',
        'manifest_artifact_id': inspection['input_artifact_refs']['manifest']['artifact_id']}
    planner = ScientificMethodPlanner(methods)
    plan = planner.create({'scientific_objective': 'Synthetic B minus A with batch', 'computational_task': 'FASTQ software proof'},
        {'observed': {}, 'inferred': {}, 'unknowns': [], 'artifact_ids': [binding['manifest_artifact_id']]},
        actor=ACTOR, data_classification='synthetic', evaluation_purpose='execution_verification',
        rna_input_contract=binding, rna_design=declaration())
    return methods, planner, plan


def test_fastq_manifest_registers_only_reference_artifact(workspace):
    manifest = fixture_manifest(workspace)
    methods, planner, plan = create_plan(workspace, manifest)
    inspection = plan['context']['dataset_summary']['observed']['fastq_input']
    assert load_fastq_manifest(methods, inspection) == manifest
    assert len(methods.list_records('artifact')) == 1
    assert 'source_plan' not in inspection and 'metadata_rows' not in json.dumps(inspection)
    assert inspection['retention'] == 'reference_bound_external_bytes'
    audit = audit_workflow_design(planner, plan['session_id'], actor=ACTOR)
    assert audit['status'] == 'ready'
    assert audit['audit']['rank'] == 3 and audit['audit']['unit_count'] == 8
    assert methods.list_records('run') == []


@pytest.mark.parametrize('member', ['s0.fastq', 'metadata.csv', 'reference.gtf', 'nextflow'])
def test_fastq_external_mutation_is_rejected(workspace, member):
    manifest = fixture_manifest(workspace)
    methods, planner, plan = create_plan(workspace, manifest)
    path = workspace / member
    path.write_bytes(path.read_bytes() + b'\n')
    result = audit_workflow_design(planner, plan['session_id'], actor=ACTOR)
    assert result['status'] == 'reconciliation_required'
    assert 'changed' in result['failure']['message']
    assert methods.list_records('run') == []
    assert planner.get(plan['session_id'])['selection_history'] == []


def test_confounded_fastq_retains_durable_refusal_before_selection(workspace):
    def confound(source):
        metadata = rows()
        for row in metadata:
            row['batch'] = row['condition']
        write_csv(Path(source['metadata']), list(metadata[0]), metadata)
    manifest = fixture_manifest(workspace, confound)
    methods, planner, plan = create_plan(workspace, manifest)
    result = audit_workflow_design(planner, plan['session_id'], actor=ACTOR)
    assert result['status'] == 'abstained'
    assert result['audit']['status'] == 'refused'
    assert result['audit']['engine_launch_count'] == 0
    with pytest.raises(ValueError):
        planner.generate_candidates(plan['session_id'], DeclaredBulkSelector(False), actor=ACTOR)
    assert planner.get(plan['session_id'])['selection_history'] == []
    assert methods.list_records('run') == []


@pytest.mark.parametrize('mutate,expected', [
    (lambda source: source.pop('formula'), 'needs_information'),
    (lambda source: source.update(analysis_scope='single_cell'), 'unsupported'),
])
def test_missing_and_unsupported_fastq_remain_distinct(workspace, mutate, expected):
    methods, planner, plan = create_plan(workspace, fixture_manifest(workspace, mutate))
    result = audit_workflow_design(planner, plan['session_id'], actor=ACTOR)
    assert result['audit']['status'] == expected
    assert methods.list_records('run') == []


def test_create_analyst_cli_registers_fastq_manifest(workspace, capsys):
    manifest = fixture_manifest(workspace)
    request = {'task': {'scientific_objective': 'Synthetic B minus A', 'computational_task': 'FASTQ software proof'},
        'dataset_summary': {'observed': {}, 'inferred': {}, 'unknowns': [], 'artifact_ids': []},
        'fastq_manifest': manifest, 'rna_design': declaration(), 'data_classification': 'synthetic',
        'evaluation_purpose': 'execution_verification'}
    path = workspace / 'request.json'
    path.write_bytes(canonical_json(request))
    assert planner_cli(['--workspace', str(workspace / 'methods'), '--actor', ACTOR,
        'create-analyst', '--request', str(path)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert 'fastq_input' in plan['context']['dataset_summary']['observed']
    assert plan['selection_history'] == []


def test_fastq_formula_substitution_stops_at_audit(workspace):
    manifest = fixture_manifest(workspace)
    methods, planner, plan = create_plan(workspace, manifest)
    changed = deepcopy(plan)
    changed['rna_design']['design']['covariates'] = []
    from devbench.analysis.workflow_state import digest
    changed['rna_design_sha256'] = digest(changed['rna_design'])
    from devbench.analysis.rna_design_audit import audit_rna_design
    assert audit_rna_design(changed, methods)['status'] == 'refused'
    assert methods.list_records('run') == []


@pytest.mark.parametrize("cancel_during_engine", [False, True])
def test_fastq_reaches_existing_durable_graph_and_trusted_adapter(workspace, monkeypatch, cancel_during_engine):
    from tests.test_fastq_method_adapter import synthetic_fastq_success
    from devbench.analysis import fastq_execution
    calls = []
    def engine(*args, **kwargs):
        calls.append((args, kwargs))
        if cancel_during_engine:
            from devbench.analysis.workflow_operations import WorkflowOperationLedger
            from devbench.analysis.workflow_entrypoints import LEDGER_NAME
            WorkflowOperationLedger(methods.root / LEDGER_NAME).cancel(session)
            assert kwargs["cancellation_event"].is_set()
        return synthetic_fastq_success(*args, **kwargs)
    monkeypatch.setattr(fastq_execution, 'run_fastq_plan', engine)
    manifest = fixture_manifest(workspace)
    methods, planner, plan = create_plan(workspace, manifest)
    session = plan['session_id']
    assert audit_workflow_design(planner, session, actor=ACTOR)['status'] == 'ready'
    selector = DeclaredBulkSelector(False)
    selector.name = 'Pinned FASTQ analysis'
    runtime = RecordingScientificRuntime()
    def act(node, **extra):
        return workflow_action(planner, session, node, actor=ACTOR, selector=selector,
            scientific_workflow=runtime, **extra)
    act('discovery')
    act('generate')
    act('candidate_review')
    selector.version = methods.register_fastq(manifest, actor=ACTOR)['version_id']
    act('resolve', version_ids=[selector.version])
    act('implementation')
    ports = {'format': 'devbench.fastq-execution-ports/1', 'method_version_id': selector.version,
        'parameters': {}, 'objective': declaration()['objective'], 'design': declaration()['design']}
    bridge = planner.prepare_execution_inputs(session, ports, output=workspace / 'execution-inputs', allow_data_execution=True)
    selector.inputs = {'manifest': plan['context']['dataset_summary']['artifact_ids'][0]}
    selector.parameters = {}
    drafts = act('design', execution_inputs_ref=bridge, execution_workspace=workspace)
    act('design_review')
    analysis = methods.create_analysis('FASTQ synthetic software proof', actor=ACTOR)
    branch = methods.create_branch(analysis['analysis_id'], analysis['revision_id'], actor=ACTOR)
    act('declare', branch_id=branch['branch_id'], spec=drafts[0])
    with pytest.raises(PermissionError, match='authorization|authority'):
        act('run', experiment_id='declared-bulk-proof')
    assert calls == []
    results = act('run', experiment_id='declared-bulk-proof', allow_data_execution=True)
    if cancel_during_engine:
        assert len(calls) == 1
        runs = methods.list_records('run')
        assert len(runs) == 1 and runs[0]['status'] == 'failed'
        assert 'Cancelled' in runs[0]['error']
        assert runs[0]['outputs'] == {}
        with pytest.raises((ValueError, PermissionError)):
            act('run', experiment_id='declared-bulk-proof', allow_data_execution=True)
        assert len(calls) == 1
        return
    assert len(results) == 1 and results[0]['status'] == 'success', results
    assert len(calls) == 1
    runs = methods.list_records('run')
    assert len(runs) == 1 and runs[0]['status'] == 'succeeded'
    assert set(runs[0]['outputs']) == {'result', 'count_matrix', 'differential_expression', 'count_model_provenance'}
    assert runs[0]['execution']['runtime'] == 'trusted_fastq'
    with pytest.raises(ValueError):
        act('run', experiment_id='declared-bulk-proof', allow_data_execution=True)
    assert len(calls) == 1
    for context in selector.contexts:
        assert 'metadata_rows' not in json.dumps(context)
        assert 'frozen_preflight' not in json.dumps(context)
    (workspace / 'proof-summary.json').write_bytes(canonical_json({'format': 'test.fastq-durable-workflow/1',
        'session_id': session, 'run_id': runs[0]['run_id'], 'engine_calls': len(calls),
        'evidence_scope': 'software_control_flow_only_fake_engine_and_fit', 'model_sessions': 0,
        'actual_comparator': 'not_run', 'scientific_correctness': None, 'biological_agreement': 'not_run'}))


@pytest.mark.parametrize('context', [None, {}, {'dataset_summary': None},
    {'dataset_summary': {}}, {'dataset_summary': {'observed': None}},
    {'dataset_summary': {'observed': []}}])
def test_execution_bridge_rejects_malformed_supplied_context(context):
    from devbench.analysis.rna_design_audit import require_rna_design_execution
    from devbench.analysis.workflow_state import digest
    design = declaration()
    plan = {'rna_design': design, 'rna_design_sha256': digest(design), 'context': context}
    with pytest.raises(ValueError, match='planning context'):
        require_rna_design_execution(plan, None)
