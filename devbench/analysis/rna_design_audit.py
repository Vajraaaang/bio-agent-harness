"""Method-free declared design gate over verified registered bulk RNA bytes.

This is a necessary pre-selection check, not method selection or scientific
validation. Cohort subsets are audited but cannot authorize full-input execution.
"""
from copy import deepcopy
import hashlib

from devharness_core.provenance import canonical_json
from devharness_core.units import UNKNOWN_UNIT_MARKERS
from .workflow_state import digest

FORMAT = 'devbench.rna-design/1'


def _text(value):
    return type(value) is str and bool(value.strip()) and len(value) <= 4000


def validate_rna_design(value):
    if (type(value) is not dict or set(value) != {'format', 'objective', 'design', 'selected_sample_ids', 'source'}
            or value['format'] != FORMAT or len(canonical_json(value)) > 48_000):
        raise ValueError('Use the closed method-free RNA design declaration')
    if value['objective'] not in ({'intent': 'infer'}, {'intent': 'descriptive'}):
        raise ValueError('Declare the actual inferential or descriptive objective')
    design = value['design']
    required = {'contrasts', 'covariates', 'chronological_field', 'chronological_order'}
    if type(design) is not dict or not required <= set(design) or set(design) - required - {'blocking'}:
        raise ValueError('Declare contrast, covariate and chronological design fields explicitly')
    blocking = design.get('blocking', [])
    if (type(blocking) is not list or len(blocking) > 1
            or any(not _text(item) for item in blocking)):
        raise ValueError('Declare at most one explicit biological-unit blocking column')
    for key, maximum in (('covariates', 16), ('chronological_order', 128)):
        items = design[key]
        if (type(items) is not list or len(items) > maximum or any(not _text(item) for item in items)
                or len(set(items)) != len(items)):
            raise ValueError('Design labels must be bounded and unique')
    if design['chronological_field'] is not None and not _text(design['chronological_field']):
        raise ValueError('Use an explicit chronological field or null')
    if type(design['contrasts']) is not list or len(design['contrasts']) > 16:
        raise ValueError('Use bounded explicit contrasts')
    for contrast in design['contrasts']:
        if (type(contrast) is not dict or set(contrast) != {'field', 'reference', 'target'}
                or any(not _text(item) for item in contrast.values())):
            raise ValueError('Use exact contrast fields and levels')
    ids = value['selected_sample_ids']
    if (type(ids) is not list or not 1 <= len(ids) <= 128 or any(not _text(item) for item in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError('Declare 1 to 128 unique selected sample IDs')
    source = value['source']
    if (type(source) is not dict or set(source) != {'kind', 'reference', 'statement'}
            or source['kind'] not in {'user_request', 'supplied_metadata', 'assistant_suggestion', 'fixture'}
            or not _text(source['reference']) or not _text(source['statement'])):
        raise ValueError('Retain the actual source of the design declaration')
    return deepcopy(value)


def retained_rna_design(plan):
    value = plan.get('rna_design')
    if value is None:
        if 'rna_design_sha256' in plan:
            raise ValueError('RNA design declaration is missing')
        return None
    value = validate_rna_design(value)
    if digest(value) != plan.get('rna_design_sha256'):
        raise ValueError('Frozen RNA design declaration changed')
    return value


def audit_rna_design(plan, methods):
    """Audit the selected cohort using the same bytes whose identities we verify."""
    from devbench.study.evidence import _read
    from .rna_execution_inputs import _decode_json, audit_execution_design
    from .rna_input_contract import validate_bulk_rna_input, MAX_MATRIX_BYTES
    declaration = retained_rna_design(plan)
    if declaration is None:
        return None
    if 'fastq_input' in plan['context']['dataset_summary']['observed']:
        from .fastq_planner_binding import audit_fastq_design
        return audit_fastq_design(plan, methods)
    inspection = plan['context']['dataset_summary']['observed'].get('bulk_rna_input')
    if inspection is None:
        raise ValueError('The explicit RNA design gate currently requires registered bulk input')
    documents = {}
    references = inspection['input_artifact_refs']
    for name, maximum in (('matrix', MAX_MATRIX_BYTES), ('metadata', 5_000_000)):
        ref = references[name]
        path = methods.artifact_path(ref['artifact_id'])
        actual = methods.get('artifact', ref['artifact_id'])
        if {key: actual[key] for key in ref} != ref:
            raise ValueError('RNA design input reference changed')
        raw = _read(path, maximum)
        if hashlib.sha256(raw).hexdigest() != ref['sha256'] or len(raw) != ref['size_bytes']:
            raise ValueError('RNA design consumed bytes differ from verified input')
        documents[name] = _decode_json(raw)
    matrix, metadata = documents['matrix'], documents['metadata']
    if (type(matrix) is not dict or set(matrix) != {'gene_ids', 'sample_ids', 'matrix'}
            or type(metadata) is not dict or set(metadata) != {'samples'}):
        raise ValueError('RNA design requires exact bulk matrix and metadata documents')
    source = {key: inspection[key] for key in ('format', 'representation', 'axes', 'provenance', 'biological_independence')}
    if 'metadata_roles' in inspection:
        source['metadata_roles'] = inspection['metadata_roles']
    actual = validate_bulk_rna_input(source, gene_ids=matrix['gene_ids'], sample_ids=matrix['sample_ids'],
        metadata_rows=metadata['samples'], matrix=matrix['matrix'])
    for key in ('shape', 'gene_ids_sha256', 'sample_ids_sha256', 'metadata_sha256',
                'aligned_metadata_sha256', 'measurement_values_sha256'):
        if actual[key] != inspection[key]:
            raise ValueError('RNA design source inspection changed')
    ids = declaration['selected_sample_ids']
    by_id = {row['sample_id']: row for row in metadata['samples']}
    absent = sorted(set(ids) - set(matrix['sample_ids']))
    rows = [] if absent else [deepcopy(by_id[identity]) for identity in ids]
    audit = {'rank': None, 'columns': None, 'sample_count': len(rows), 'unit_count': None,
        'blockers': ['selected_samples_absent'] if absent else [],
        'assurance': 'necessary_recomputed_checks_not_independent_scientific_validation'}
    if rows:
        selected = {**actual, 'sample_metadata': rows, 'sample_metadata_complete': True,
                    'shape': [actual['shape'][0], len(rows)]}
        audit = audit_execution_design(declaration, selected)
    blockers = sorted(set(audit['blockers']))
    unknown_prefixes = ('independent_biological_units_unknown', 'biological_independence_declaration_missing',
        'measurement_representation_unknown', 'inferential_contrast_or_chronological_estimand_missing',
        'chronological_labels_require_complete_explicit_order', 'chronological_order_without_field',
        'design_covariate_missing:', 'selected_samples_absent')
    known = [item for item in blockers if not item.startswith(unknown_prefixes)]
    # The inherited generic independence warning combines unknowns and repeats;
    # only explicit repeated identities independently establish a refusal.
    if 'independent_biological_units_unknown_or_repeated' in known:
        known.remove('independent_biological_units_unknown_or_repeated')
    information = [item for item in blockers if item not in known]
    if ids != matrix['sample_ids']:
        information.append('selected_cohort_requires_registered_execution_inputs')
    companion = plan.get('scientific_contract')
    if companion is not None:
        match_scientific_design(declaration, companion)
        information.extend('scientific_contract_unknown:' + item['field'] for item in companion['unknowns'] if item['blocking'])
    status = 'refused' if known else 'needs_information' if information else 'passed'
    return {'format': 'devbench.rna-design-audit/1', **audit, 'blockers': blockers,
        'information_required': sorted(set(information)), 'status': status,
        'declaration_sha256': digest(declaration), 'source': deepcopy(declaration['source']),
        'input_artifact_refs': deepcopy(references), 'source_inspection_sha256': digest(actual),
        'selected_sample_ids': deepcopy(ids), 'selected_sample_ids_sha256': digest(ids),
        'selected_metadata_sha256': digest(rows), 'scientific_validity_established': False,
        'execution_authority_granted': False,
        'model_execution_authority': 'not_granted_by_design_audit'}


def require_rna_design_audit(plan, methods):
    """Prevent direct planner APIs bypassing the opt-in declared-design route."""
    if retained_rna_design(plan) is None:
        return
    from .workflow_operations import WorkflowOperationLedger
    from .workflow_entrypoints import LEDGER_NAME
    from .workflow_state import planning_binding
    path = methods.root / LEDGER_NAME
    if not path.is_file() or path.is_symlink():
        raise ValueError('A completed RNA design audit is required before method selection or execution')
    ledger = WorkflowOperationLedger(path, readonly=True)
    record = ledger.get(plan['session_id'], 'audit', 0)
    if (record is None or record['status'] != 'completed'
            or record['request'].get('binding_digest') != planning_binding(plan)
            or record['result'].get('update', {}).get('status') != 'ready'):
        raise ValueError('A passed RNA design audit is required before method selection or execution')
    actual = audit_rna_design(plan, methods)
    if actual['status'] != 'passed' or actual != record['result'].get('evidence'):
        raise ValueError('Retained RNA design audit is stale or does not authorize this cohort')
    return actual


def match_scientific_design(declaration, companion):
    if companion is None:
        return
    contrast = companion['statistical_plan']['contrast']
    if contrast is not None:
        expected = {'field': contrast['factor'], 'reference': contrast['denominator'], 'target': contrast['numerator']}
        if expected not in declaration['design']['contrasts']:
            raise ValueError('Scientific companion contrast differs from the explicit RNA design')


def require_rna_design_execution(plan, methods):
    """An audit licenses no fit: require an exact existing reader/input bridge."""
    declaration = retained_rna_design(plan)
    if declaration is None or declaration['objective']['intent'] != 'infer':
        return None
    # Legacy direct bridge checks omit context; a supplied context must still
    # be structurally valid and cannot silently fall back to the count route.
    observed = {}
    if 'context' in plan:
        context = plan['context']
        if (type(context) is not dict or type(context.get('dataset_summary')) is not dict
                or type(context['dataset_summary'].get('observed')) is not dict):
            raise ValueError('Declared RNA planning context requires dataset_summary.observed')
        observed = context['dataset_summary']['observed']
    if 'fastq_input' in observed:
        from .fastq_planner_binding import require_fastq_execution
        return require_fastq_execution(plan, methods)
    binding = plan.get('rna_execution_inputs')
    if binding is None:
        raise ValueError('Inferential RNA design requires an exact execution input bridge; audit alone cannot authorize a fit')
    from .rna_execution_inputs import load_execution_inputs, _read_source
    record = load_execution_inputs(binding['record_ref'], artifact_service=methods,
        dataset_binding=binding['dataset_binding'], workspace=binding.get('workspace'))
    receipt = _read_source(record['transformation_receipt_ref'], workspace=binding.get('workspace', methods.root.parent))
    ports = receipt['method_ports']
    if (digest(ports['objective']) != digest(declaration['objective'])
            or digest(ports['design']) != digest(declaration['design'])
            or receipt['sample_ids_sha256'] != digest(declaration['selected_sample_ids'])):
        raise ValueError('Execution bridge design or cohort differs from the audited RNA design')
    if receipt['reader_contract'] is None:
        raise ValueError('Inferential RNA design requires an exact method/parameter reader binding, not unbound identity ports')
    if receipt['reader_contract']['tool_name'] != 'bulk_condition_contrast':
        raise ValueError('inferential_design_to_method_binding_not_implemented: no reviewed semantic mapping for this method')
    _require_bulk_semantics(plan, methods, receipt)
    return record


def _require_bulk_semantics(plan, methods, receipt):
    """Bind the existing structured bulk contrast, never interpret a free formula."""
    from devharness_server.tools_bulk import validate_bulk_contrast_parameters
    from .rna_execution_inputs import _read_json
    declaration = retained_rna_design(plan)
    design = declaration['design']
    if (len(design['contrasts']) != 1 or design['chronological_field'] is not None
            or design['chronological_order']):
        raise ValueError('Bulk semantic binding supports one independent two-group contrast without temporal terms')
    inspection = plan.get('context', {}).get('dataset_summary', {}).get('observed', {}).get('bulk_rna_input')
    if inspection is None:
        raise ValueError('Bulk semantic binding requires the original registered input inspection')
    keys = ('format', 'representation', 'axes', 'provenance', 'biological_independence')
    original = {key: inspection[key] for key in keys}
    if 'metadata_roles' in inspection:
        original['metadata_roles'] = inspection['metadata_roles']
    if (digest(original) != digest(receipt['input_binding']['declarations'])
            or any(digest(inspection['input_artifact_refs'][key]) != digest({field: receipt['original_refs'][key][field]
                for field in inspection['input_artifact_refs'][key]}) for key in ('matrix', 'metadata'))):
        raise ValueError('Bulk execution input declarations differ from the original audited source')
    reader = receipt['reader_contract']
    public, options = validate_bulk_contrast_parameters(reader['parameters']['public_task'], reader['parameters']['options'])
    contrast = design['contrasts'][0]
    roles = inspection.get('metadata_roles', {})
    if (roles.get('condition') != contrast['field'] or public['analysis_columns']['condition'] != contrast['field']
            or options['condition_col'] != contrast['field']
            or options['reference_condition'] != contrast['reference']
            or options['comparison_condition'] != contrast['target']
            or public['context']['requested_condition_contrast'] != {
                'denominator': contrast['reference'], 'numerator': contrast['target']}):
        raise ValueError('Bulk contrast direction or condition role differs from the audited objective')
    if public['questions'][0]['contrast'] != contrast['target'] + ' minus ' + contrast['reference']:
        raise ValueError('Bulk target contrast text must state the exact declared numerator minus denominator')
    batch = roles.get('batch')
    batches = [batch] if batch is not None else []
    blocking = design.get('blocking', [])
    if (public['analysis_columns'].get('blocking', []) != blocking
            or options.get('block_cols', []) != blocking
            or blocking and blocking != [roles.get('biological_unit')]):
        raise ValueError('Bulk fixed block must preserve the audited biological-unit declaration')
    if (set(design['covariates']) - set(batches)
            or public['analysis_columns']['batches'] != batches
            or options.get('batch_cols', []) != batches):
        raise ValueError('Bulk binding must retain every declared batch and refuses unsupported covariates')
    hierarchy = (inspection.get('biological_independence') or {}).get('unit_hierarchy')
    if hierarchy is None or digest(hierarchy) != digest(public['unit_hierarchy']):
        raise ValueError('Bulk biological-unit hierarchy must match the explicit original source declaration')
    if (hierarchy['population_unit_label'] != roles.get('biological_unit')
            or public['analysis_columns']['population_unit'] != roles.get('biological_unit')):
        raise ValueError('Bulk population-unit role differs from the audited source')
    expected_measurement = {'raw_counts': 'raw_counts', 'normalized_expression': 'continuous'}.get(inspection['representation'])
    if public['measurement'] != expected_measurement:
        raise ValueError('Bulk measurement interpretation differs from the admitted assay')
    if (digest(public['gene_ids']) != inspection['gene_ids_sha256']
            or receipt['gene_ids_sha256'] != inspection['gene_ids_sha256']
            or receipt['sample_ids_sha256'] != inspection['sample_ids_sha256']):
        raise ValueError('Bulk execution must preserve the entire admitted gene and sample family')
    ref = receipt['original_refs']['metadata']
    metadata = _read_json(methods.artifact_path(ref['artifact_id']), 5_000_000, expected=ref)
    rows = metadata['samples']
    fields = {field for row in rows for field in row}
    if batch is None and 'batch' in fields:
        raise ValueError('An observed batch column must be declared; no implicit nuisance terms')
    for column in (contrast['field'], *batches, *blocking):
        if any(type(row.get(column)) is not str or not row[column].strip() for row in rows):
            raise ValueError('Bulk design columns require complete string values')
    if {row[contrast['field']] for row in rows} != {contrast['reference'], contrast['target']}:
        raise ValueError('Bulk execution requires exactly the declared two-group cohort')
    from .rna_execution_inputs import audit_execution_design
    execution_audit = audit_execution_design(declaration, {**inspection, 'sample_metadata': rows})
    if execution_audit['blockers']:
        raise ValueError('Bulk biological design is invalid: ' + ', '.join(execution_audit['blockers']))
    for name in ('population_unit_label', 'specimen_label', 'library_label'):
        values = [row.get(hierarchy[name]) for row in rows]
        # Paired biological units may repeat; libraries remain memberships, not
        # independent donors. The shared design audit checks pairing and identity.
        require_unique = not blocking or name == 'library_label'
        if (any(type(value) is not str or value.strip().lower() in UNKNOWN_UNIT_MARKERS for value in values)
                or require_unique and len({value.strip() for value in values}) != len(values)):
            raise ValueError('Bulk binding requires complete biological-unit and unique library identities')


def rna_design_execution_parameters(plan, methods):
    """Return exact existing method parameters and ports after semantic verification."""
    record = require_rna_design_execution(plan, methods)
    if record is None:
        raise ValueError('An inferential RNA design is required for execution parameters')
    if record['format'] == 'devbench.fastq-execution-inputs/1':
        return {'method_version_id': record['method_version_id'], 'parameters': {},
            'inputs': {name: ref['artifact_id'] for name, ref in record['execution_refs'].items()}}
    from .rna_execution_inputs import _read_source
    workspace = plan['rna_execution_inputs'].get('workspace', methods.root.parent)
    receipt = _read_source(record['transformation_receipt_ref'], workspace=workspace)
    reader = receipt['reader_contract']
    return {'method_version_id': reader['method_version_id'], 'parameters': deepcopy(reader['parameters']),
        'inputs': {name: reference['artifact_id'] for name, reference in record['execution_refs'].items()}}
