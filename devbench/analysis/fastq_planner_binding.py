"""Reference-bound FASTQ inputs for the existing durable scientific planner.

Only this bounded manifest is snapshotted. External read/reference bytes remain
mutable filesystem objects and are revalidated at every admission boundary.
"""
from copy import deepcopy
from pathlib import Path
import tempfile

from devharness_core.provenance import canonical_json
from .workflow_state import digest

FORMAT = 'devbench.fastq-input-binding/1'
INSPECTION = 'devbench.fastq-input/1'
RETENTION = 'reference_bound_external_bytes'
MAX_MANIFEST_BYTES = 5_000_000


def _workspace(value):
    from .fastq_execution import _absolute
    path = _absolute(value, exists=True)
    if not path.is_dir():
        raise ValueError('FASTQ workspace must be a canonical directory')
    return path


def _members(plan, workspace):
    from .fastq_contract import _local_file, _read_csv, _file_identity
    paths = [(plan.get('samplesheet'), 'samplesheet'), (plan.get('metadata'), 'metadata')]
    reference = plan.get('reference', {})
    if isinstance(reference, dict):
        paths.extend((reference.get(role), role) for role in ('fasta', 'gtf'))
    if plan.get('samplesheet'):
        sheet = _local_file(plan['samplesheet'], 'samplesheet')
        rows, _ = _read_csv(sheet, 'samplesheet')
        paths.extend((row.get(mate), 'fastq') for row in rows for mate in ('fastq_1', 'fastq_2') if row.get(mate))
    members = []
    for value, role in paths:
        if value is None:
            continue
        path = _local_file(value, role)
        if not path.is_relative_to(workspace):
            raise ValueError('FASTQ member lies outside the assigned workspace')
        members.append(_file_identity(path, role))
    return members


def _admit(plan, workspace):
    from .fastq_contract import preflight_fastq_plan
    members, frozen = [], None
    try:
        members = _members(plan, workspace)
        frozen = preflight_fastq_plan(plan)
        if members != frozen['input_files']:
            raise ValueError('FASTQ inputs changed between reference binding and preflight')
        outcome = {'status': 'passed', 'reason': None}
    except (ValueError, OSError) as exc:
        outcome = {'status': getattr(exc, 'status', 'input_error'), 'reason': str(exc)[:2000]}
    return members, frozen, outcome


def build_fastq_manifest(source_plan, *, workspace, execution):
    """Freeze local identities, retaining scientifically invalid requests for audit."""
    workspace = _workspace(workspace)
    if type(source_plan) is not dict:
        raise ValueError('FASTQ source_plan must be an object')
    members, frozen, outcome = _admit(source_plan, workspace)
    manifest = {'format': FORMAT, 'source_plan': deepcopy(source_plan), 'workspace': str(workspace),
        'frozen_preflight': frozen, 'input_files': members, 'preflight_outcome': outcome,
        'retention': RETENTION, 'execution': deepcopy(execution)}
    return validate_fastq_manifest(manifest)


def validate_fastq_manifest(manifest, *, revalidate=True):
    from .fastq_execution import _absolute, _identity, _resources
    fields = {'format', 'source_plan', 'workspace', 'frozen_preflight', 'input_files',
              'preflight_outcome', 'retention', 'execution'}
    if (type(manifest) is not dict or set(manifest) != fields or manifest['format'] != FORMAT
            or manifest['retention'] != RETENTION or len(canonical_json(manifest)) > MAX_MANIFEST_BYTES):
        raise ValueError('Use the exact bounded FASTQ input manifest')
    workspace = _workspace(manifest['workspace'])
    execution = manifest['execution']
    if type(execution) is not dict or set(execution) != {
            'resources', 'timeout_seconds', 'execution_profile', 'nextflow_path', 'nextflow_sha256'}:
        raise ValueError('Use exact frozen FASTQ execution limits and executable identity')
    _resources(execution['resources'], execution['timeout_seconds'])
    if type(execution['timeout_seconds']) is not int or not 1 <= execution['timeout_seconds'] <= 3600:
        raise ValueError('FASTQ timeout must be an explicit bounded integer')
    if execution['execution_profile'] not in {'standard', 'minimal-counts'}:
        raise ValueError('Unsupported FASTQ execution profile')
    executable = _absolute(execution['nextflow_path'], exists=True)
    if _identity(executable, maximum=100_000_000)['sha256'] != execution['nextflow_sha256']:
        raise ValueError('Frozen Nextflow executable identity changed')
    if type(manifest['source_plan']) is not dict:
        raise ValueError('FASTQ source_plan must be an object')
    if revalidate is not True:
        raise ValueError('FASTQ external bytes must always be revalidated')
    if revalidate:
        members, frozen, outcome = _admit(manifest['source_plan'], workspace)
        if (members != manifest['input_files'] or frozen != manifest['frozen_preflight']
                or outcome != manifest['preflight_outcome']):
            raise ValueError('FASTQ source bytes, design, or preflight outcome changed')
    return deepcopy(manifest)


def _ref(methods, identity):
    artifact = methods.get('artifact', identity)
    return {key: artifact[key] for key in ('artifact_id', 'sha256', 'size_bytes', 'artifact_type')}


def register_fastq_input(methods, manifest, *, actor):
    manifest = validate_fastq_manifest(manifest)
    if not methods.root.is_relative_to(Path(manifest['workspace'])):
        raise ValueError('FASTQ method service lies outside the assigned workspace')
    with tempfile.TemporaryDirectory(prefix='fastq-binding-', dir=methods.root) as directory:
        path = Path(directory) / 'fastq-input.json'
        path.write_bytes(canonical_json(manifest))
        artifact = methods.import_artifact(path, artifact_type='json', actor=actor)
    return inspect_registered_fastq_input(methods, {'format': FORMAT, 'manifest_artifact_id': artifact['artifact_id']})


def _summary(manifest, reference):
    frozen = manifest['frozen_preflight']
    declared = {key: deepcopy(manifest['source_plan'][key]) for key in (
        'formula', 'contrast', 'biological_unit', 'biological_unit_type', 'blocking', 'batch_columns',
        'analysis_scope', 'objective', 'alpha', 'analysis_policy') if key in manifest['source_plan']}
    return {'format': INSPECTION, 'retention': RETENTION, 'supplied_declarations': declared,
        'declaration_assurance': 'supplied_plan_not_verified_biological_identity_or_user_requirement', 'manifest_sha256': reference['sha256'],
        'input_artifact_refs': {'manifest': reference}, 'preflight_outcome': manifest['preflight_outcome'],
        'sample_count': len(frozen['sample_ids']) if frozen else None,
        'external_member_count': len(manifest['input_files']),
        'external_bytes': sum(item['size_bytes'] for item in manifest['input_files']),
        'source_plan_sha256': digest(manifest['source_plan']),
        'scientific_validity_established': False, 'count_matrix_available': False}


def inspect_registered_fastq_input(methods, binding):
    from .rna_execution_inputs import _read_json
    if type(binding) is not dict or set(binding) != {'format', 'manifest_artifact_id'} or binding['format'] != FORMAT:
        raise ValueError('Use an exact registered FASTQ manifest reference')
    reference = _ref(methods, binding['manifest_artifact_id'])
    if reference['artifact_type'] != 'json':
        raise ValueError('FASTQ manifest must be a registered JSON artifact')
    manifest = validate_fastq_manifest(_read_json(methods.artifact_path(reference['artifact_id']), MAX_MANIFEST_BYTES, expected=reference))
    if not methods.root.is_relative_to(Path(manifest['workspace'])):
        raise ValueError('FASTQ method service lies outside the assigned workspace')
    return _summary(manifest, reference)


def load_fastq_manifest(methods, inspection):
    from .rna_execution_inputs import _read_json
    reference = inspection['input_artifact_refs']['manifest']
    actual = inspect_registered_fastq_input(methods, {'format': FORMAT, 'manifest_artifact_id': reference['artifact_id']})
    if actual != inspection:
        raise ValueError('FASTQ registered inspection changed')
    return _read_json(methods.artifact_path(reference['artifact_id']), MAX_MANIFEST_BYTES, expected=reference)


def audit_fastq_design(plan, methods):
    from .rna_design_audit import retained_rna_design
    declaration = retained_rna_design(plan)
    inspection = plan['context']['dataset_summary']['observed']['fastq_input']
    manifest = load_fastq_manifest(methods, inspection)
    outcome, source, frozen = manifest['preflight_outcome'], manifest['source_plan'], manifest['frozen_preflight']
    status = outcome['status']
    blockers, information = [], []
    if status == 'passed':
        design = declaration['design']
        contrast = source['contrast']
        expected_contrast = {'field': contrast['factor'], 'reference': contrast['denominator'], 'target': contrast['numerator']}
        terms = frozen['design']['terms']
        blocking = source.get('blocking', [])
        if (declaration['objective'] != {'intent': 'infer'} or design['contrasts'] != [expected_contrast]
                or design['chronological_field'] is not None or design['chronological_order']
                or design.get('blocking', []) != blocking
                or design['covariates'] != [term for term in terms if term != contrast['factor'] and term not in blocking]
                or declaration['selected_sample_ids'] != frozen['sample_ids']):
            blockers.append('fastq_formula_contrast_or_cohort_differs_from_declared_design')
            status = 'refused'
    elif status == 'needs_information':
        information.append(outcome['reason'])
    else:
        blockers.append(outcome['reason'])
    model = frozen['design'] if frozen else {}
    return {'format': 'devbench.rna-design-audit/1', 'input_kind': 'fastq', 'status': status,
        'blockers': blockers, 'information_required': information, 'reason': outcome['reason'],
        'rank': model.get('rank'), 'columns': len(model.get('design_columns', [])) if frozen else None,
        'sample_count': len(frozen['sample_ids']) if frozen else None, 'unit_count': model.get('unit_count'),
        'residual_degrees_of_freedom': model.get('residual_df'),
        'declaration_sha256': digest(declaration), 'source': deepcopy(declaration['source']),
        'input_artifact_refs': deepcopy(inspection['input_artifact_refs']),
        'source_inspection_sha256': digest(inspection), 'selected_sample_ids': declaration['selected_sample_ids'],
        'selected_sample_ids_sha256': digest(declaration['selected_sample_ids']),
        'retention': RETENTION, 'engine_launch_count': 0, 'scientific_validity_established': False,
        'execution_authority_granted': False, 'model_execution_authority': 'not_granted_by_design_audit'}


def prepare_fastq_execution(plan, methods, ports, output):
    from .rna_execution_inputs import _source
    manifest = load_fastq_manifest(methods, plan['context']['dataset_summary']['observed']['fastq_input'])
    if (set(ports) != {'format', 'method_version_id', 'parameters', 'objective', 'design'}
            or ports['format'] != 'devbench.fastq-execution-ports/1' or ports['parameters'] != {}):
        raise ValueError('Use exact FASTQ execution ports; scientific parameters are frozen in the input manifest')
    from .fastq_method_adapter import fastq_contract
    method = methods.method(ports['method_version_id'])
    expected = fastq_contract(manifest)
    expected = expected.to_dict() if hasattr(expected, 'to_dict') else expected
    if method['contract'] != expected:
        raise ValueError('Selected method differs from the exact FASTQ manifest')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    reference = plan['context']['dataset_summary']['observed']['fastq_input']['input_artifact_refs']['manifest']
    record = {'format': 'devbench.fastq-execution-inputs/1', 'method_version_id': ports['method_version_id'],
        'parameters': {}, 'original_refs': {'manifest': reference}, 'execution_refs': {'manifest': reference},
        'declaration_sha256': plan['rna_design_sha256'], 'manifest_sha256': reference['sha256']}
    path = output / 'execution-inputs.json'
    path.write_bytes(canonical_json(record))
    return _source(path)


def require_fastq_execution(plan, methods, *, inputs=None, method_version_id=None, parameters=None, subset_spec=None):
    from .rna_execution_inputs import _read_source
    inspection = plan['context']['dataset_summary']['observed']['fastq_input']
    manifest = load_fastq_manifest(methods, inspection)
    if audit_fastq_design(plan, methods)['status'] != 'passed':
        raise ValueError('FASTQ design did not pass admission')
    binding = plan.get('rna_execution_inputs')
    if binding is None:
        raise ValueError('FASTQ execution requires the exact selected-method input bridge')
    if (binding.get('workspace', manifest['workspace']) != manifest['workspace']
            or binding.get('dataset_binding') != {'format': FORMAT, 'manifest_artifact_id':
                inspection['input_artifact_refs']['manifest']['artifact_id']}):
        raise ValueError('FASTQ execution bridge changed its assigned workspace or original dataset')
    record = _read_source(binding['record_ref'], workspace=manifest['workspace'])
    ref = inspection['input_artifact_refs']['manifest']
    expected = {'format': 'devbench.fastq-execution-inputs/1', 'method_version_id': record.get('method_version_id'),
        'parameters': {}, 'original_refs': {'manifest': ref}, 'execution_refs': {'manifest': ref},
        'declaration_sha256': plan['rna_design_sha256'], 'manifest_sha256': ref['sha256']}
    if record != expected:
        raise ValueError('FASTQ execution binding changed')
    from .fastq_method_adapter import fastq_contract
    contract = fastq_contract(manifest)
    contract = contract.to_dict() if hasattr(contract, 'to_dict') else contract
    if methods.method(record['method_version_id'])['contract'] != contract:
        raise ValueError('FASTQ selected method or frozen formula changed')
    if inputs is not None and (inputs != {'manifest': ref['artifact_id']} or method_version_id != record['method_version_id']
            or parameters != {} or subset_spec.get('kind') != 'full_dataset'):
        raise ValueError('FASTQ execution requires exact method, manifest, parameters, and complete cohort')
    return record
