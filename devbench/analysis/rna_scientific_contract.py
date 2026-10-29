"""Optional scientific planning companion, separate from staged execution inputs.

Declarations retain provenance, not authenticated human authorship or scientific
validation. This module reads registered artifact identities; it never fits data,
chooses a method, grants capabilities, or changes the bulk RNA /1 count contract.
"""
from typing import Annotated, Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from devbench.study.contracts import FrozenContract, Identifier, Sha256, content_sha256
from devharness_core.provenance import canonical_json
from .method_planner_contract import _copy_json, _reject_capabilities, validate_planning_metadata


Text = Annotated[str, Field(min_length=1, max_length=4000, pattern=r'\S')]
Texts = Annotated[tuple[Text, ...], Field(max_length=32)]
Ids = Annotated[tuple[Identifier, ...], Field(max_length=64)]
Origin = Literal['user', 'assistant', 'fixture', 'registry_default', 'literature_default', 'inferred', 'trial_selected']
MAX_CONTRACT_BYTES = 48_000


class InputReference(FrozenContract):
    artifact_id: Sha256
    sha256: Sha256
    artifact_type: Annotated[str, Field(min_length=1, max_length=80)]
    size_bytes: Annotated[int, Field(ge=0)]


class RequirementSource(FrozenContract):
    source_id: Identifier
    kind: Literal['user_request', 'supplied_metadata', 'observed_artifact', 'literature',
                  'assistant_suggestion', 'default', 'inference']
    text: Annotated[str, Field(min_length=1, max_length=12000, pattern=r'\S')]
    reference: Text


class Objective(FrozenContract):
    statement: Text
    estimand: Text | None
    origin: Origin
    source_id: Identifier
    source_excerpt: Text


class Requirement(FrozenContract):
    name: Identifier
    value: JsonValue
    origin: Origin
    source_id: Identifier
    source_excerpt: Text


class BiologicalUnit(FrozenContract):
    unit_id: Identifier
    kind: Literal['donor', 'subject', 'specimen', 'sample', 'library', 'capture', 'technical_replicate']
    parent_ids: Ids
    independence: Literal['independent', 'dependent', 'unknown']
    evidence_source_ids: Ids
    condition: Text | None
    time: Text | None


class VersionReference(FrozenContract):
    name: Text
    version: Text
    sha256: Sha256 | None


class EstimatedCountImport(FrozenContract):
    quantifier: VersionReference
    importer: VersionReference
    offset_strategy: Text
    source_artifact_ids: Annotated[tuple[Sha256, ...], Field(min_length=1, max_length=128)]


class Transformation(FrozenContract):
    name: Text
    version: Text
    source_ids: Ids


class Assay(FrozenContract):
    representation: Literal['direct_counts', 'estimated_counts', 'normalized_expression',
                            'corrected_expression', 'latent_representation', 'unknown']
    orientation: Literal['feature_by_sample', 'sample_by_feature', 'cell_by_feature', 'feature_by_cell', 'unknown']
    feature_id_type: Text
    observation_id_type: Text
    measurement_unit: Text
    count_provenance: Text | None
    estimated_count_import: EstimatedCountImport | None
    transformations: Annotated[tuple[Transformation, ...], Field(max_length=32)]
    feature_ids_sha256: Sha256 | None = None
    observation_ids_sha256: Sha256 | None = None
    metadata_sha256: Sha256 | None = None


class MethodAssumption(FrozenContract):
    statement: Text
    status: Literal['satisfied', 'violated', 'unknown', 'not_applicable']
    evidence_source_ids: Ids


class Contrast(FrozenContract):
    factor: Text
    numerator: Text
    denominator: Text

    @model_validator(mode='after')
    def distinct_levels(self):
        if self.numerator == self.denominator:
            raise ValueError('Contrast numerator and denominator must differ')
        return self


class Multiplicity(FrozenContract):
    method: Text
    family_id: Identifier
    hypothesis_ids_artifact_id: Sha256
    alpha: Annotated[float, Field(gt=0, lt=1, allow_inf_nan=False)]


class StatisticalPlan(FrozenContract):
    formula: Text | None
    contrast: Contrast | None
    normalization: Text | None
    test: Text | None
    filtering: Texts
    multiplicity: Multiplicity | None
    policy_source_ids: Ids
    offsets: Text | None = None


class Resources(FrozenContract):
    memory_mb: Annotated[int, Field(ge=1, le=1_048_576)]
    cpu_threads: Annotated[int, Field(ge=1, le=1024)]
    timeout_seconds: Annotated[int, Field(ge=1, le=604800)]


class ExecutionRequirements(FrozenContract):
    tool_refs: Annotated[tuple[VersionReference, ...], Field(max_length=32)]
    environment_refs: Annotated[tuple[VersionReference, ...], Field(max_length=32)]
    reference_refs: Annotated[tuple[VersionReference, ...], Field(max_length=32)]
    resource_requirements: Resources
    capability_requirements: Texts


class ReplayComparison(FrozenContract):
    output_name: Identifier
    identifiers: Texts
    absolute_tolerance: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    relative_tolerance: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    missingness: Literal['exact_mask']


class ReplayRequirements(FrozenContract):
    environment_refs: Annotated[tuple[VersionReference, ...], Field(max_length=32)]
    comparisons: Annotated[tuple[ReplayComparison, ...], Field(max_length=64)]
    seeds: Annotated[tuple[Annotated[int, Field(ge=0, le=2**63-1)], ...], Field(max_length=32)]


class Unknown(FrozenContract):
    field: Text
    reason: Text
    blocking: bool


class ScientificContract(FrozenContract):
    record_format: Literal['devbench.rna-scientific-contract/1']
    contract_id: Identifier
    input_artifact_refs: Annotated[tuple[InputReference, ...], Field(min_length=1, max_length=128)]
    sources: Annotated[tuple[RequirementSource, ...], Field(min_length=1, max_length=64)]
    objective: Objective
    requirements: Annotated[tuple[Requirement, ...], Field(max_length=64)]
    biological_units: Annotated[tuple[BiologicalUnit, ...], Field(max_length=128)]
    assay: Assay
    method_assumptions: Annotated[tuple[MethodAssumption, ...], Field(max_length=64)]
    statistical_plan: StatisticalPlan
    execution: ExecutionRequirements
    replay: ReplayRequirements
    unknowns: Annotated[tuple[Unknown, ...], Field(max_length=64)]
    human_authorship_authenticated: Literal[False]
    execution_authority_granted: Literal[False]
    scientific_validity_established: Literal[False]

    @field_validator('human_authorship_authenticated', 'execution_authority_granted',
                     'scientific_validity_established', mode='before')
    @classmethod
    def authority_denial(cls, value):
        # Literal[False] alone accepts numeric zero, including in strict mode.
        # Apply the same boundary to direct typed construction and JSON callers.
        if value is not False:
            raise ValueError('Scientific authority and authorship denials require boolean false')
        return value

    @model_validator(mode='after')
    def meaningful_references(self):
        sources = {source.source_id: source for source in self.sources}
        units = {unit.unit_id: unit for unit in self.biological_units}
        inputs = {ref.artifact_id for ref in self.input_artifact_refs}
        if (len(sources) != len(self.sources) or len(units) != len(self.biological_units)
                or len(inputs) != len(self.input_artifact_refs)
                or len({r.name for r in self.requirements}) != len(self.requirements)):
            raise ValueError('Scientific contract identifiers must be unique')
        for item in (self.objective, *self.requirements):
            source = sources.get(item.source_id)
            if source is None or item.source_excerpt not in source.text:
                raise ValueError('Requirement source excerpt must match an explicit retained source')
            if item.origin == 'user' and source.kind != 'user_request':
                raise ValueError('A user origin requires an explicit user-request source; authorship remains unauthenticated')
        if self.objective.origin == 'user' and self.objective.statement not in self.objective.source_excerpt:
            raise ValueError('User objective must match its explicit source excerpt')
        referenced = set(self.statistical_plan.policy_source_ids)
        for item in (*self.method_assumptions, *self.biological_units):
            referenced.update(item.evidence_source_ids)
        for item in self.assay.transformations:
            referenced.update(item.source_ids)
        if not referenced <= sources.keys():
            raise ValueError('Unknown scientific provenance source')
        for unit in self.biological_units:
            if len(set(unit.parent_ids)) != len(unit.parent_ids) or not set(unit.parent_ids) <= units.keys():
                raise ValueError('Biological unit parents must be distinct known identities')
            if unit.independence == 'independent' and (unit.kind not in {'donor', 'subject'}
                    or not unit.evidence_source_ids or unit.parent_ids):
                raise ValueError('Independent biological units require root donor/subject identity and explicit source evidence')
            if unit.kind in {'donor', 'subject'} and any(
                    units[parent].kind not in {'donor', 'subject'} for parent in unit.parent_ids):
                raise ValueError('Donor/subject hierarchy cannot have lower-level biological unit parents')
        completed = set()
        def visit(identity, ancestry):
            if identity in ancestry:
                raise ValueError('Biological unit hierarchy must be acyclic')
            if identity in completed:
                return
            for parent in units[identity].parent_ids:
                visit(parent, ancestry | {identity})
            completed.add(identity)
        for identity in units:
            visit(identity, set())
        unknown_fields = {item.field for item in self.unknowns}
        if not units or any(unit.independence == 'unknown' for unit in units.values()):
            if 'biological_units' not in unknown_fields:
                raise ValueError('Unknown biological independence needs an explicit biological_units unknown')
        for field in ('representation', 'orientation'):
            if getattr(self.assay, field) == 'unknown' and 'assay.' + field not in unknown_fields:
                raise ValueError('Unknown assay semantics need an explicit unknown record')
        if self.assay.representation == 'direct_counts' and self.assay.count_provenance is None:
            raise ValueError('Direct counts require count provenance')
        imported = self.assay.estimated_count_import
        if self.assay.representation == 'estimated_counts':
            if imported is None or self.assay.count_provenance is None:
                raise ValueError('Estimated counts require quantifier, import, offset and count provenance')
        elif imported is not None:
            raise ValueError('Estimated-count imports must match their representation')
        if imported is not None and not set(imported.source_artifact_ids) <= inputs:
            raise ValueError('Estimated-count import must bind declared input artifacts')
        multiplicity = self.statistical_plan.multiplicity
        if multiplicity is not None and multiplicity.hypothesis_ids_artifact_id not in inputs:
            raise ValueError('Multiplicity family must bind an exact registered input artifact')
        return self


def _typed(value):
    copied = _copy_json(value)
    if type(copied) is not dict:
        raise ValueError('Scientific contract must be an object')
    encoded = canonical_json(copied)
    if len(encoded) > MAX_CONTRACT_BYTES:
        raise ValueError('Scientific contract exceeds its 48 KB bound')
    normalized = ScientificContract.model_validate_json(encoded).model_dump(mode='json', exclude_unset=True)
    if len(canonical_json(normalized)) > MAX_CONTRACT_BYTES:
        raise ValueError('Normalized scientific contract exceeds its 48 KB bound')
    return normalized


def validate_scientific_contract(value, *, methods, artifact_ids, task, user_parameter_requirements=(),
                                 rna_input_inspection=None):
    """Check declared provenance and exact registered inputs; never authenticate a caller."""
    result = _typed(value)
    # Reject inputs that would poison later candidate/review projections before
    # a planner can persist the companion. Execution/replay remain separate.
    scientific_projection(result)
    refs = result['input_artifact_refs']
    if {ref['artifact_id'] for ref in refs} != set(artifact_ids):
        raise ValueError('Scientific contract must bind the exact declared input artifacts')
    for ref in refs:
        methods.artifact_path(ref['artifact_id'])
        actual = methods.get('artifact', ref['artifact_id'])
        if any(actual[key] != ref[key] for key in ref):
            raise ValueError('Scientific contract registered input identity changed')
    if result['objective']['statement'] != task['scientific_objective']:
        raise ValueError('Scientific contract objective must match the actual planning task')
    if rna_input_inspection is not None:
        _match_registered_rna(result, rna_input_inspection)
    metadata = validate_planning_metadata(user_parameter_requirements=list(user_parameter_requirements))
    required = {item['name']: item for item in metadata['user_parameter_requirements']}
    explicit = {item['name']: item for item in result['requirements'] if item['origin'] == 'user'}
    if explicit.keys() != required.keys():
        raise ValueError('Explicit user requirements must match the retained parameter requirement records')
    for name, item in explicit.items():
        if (canonical_json(item['value']) != canonical_json(required[name]['value'])
                or item['source_excerpt'] != required[name]['evidence']):
            raise ValueError('Explicit user requirement value and source evidence must match exactly')
    return result


def _match_registered_rna(contract, inspection):
    """Bind to the existing inspected /1 declarations; never reinterpret their assay."""
    expected_representation = {'raw_counts': 'direct_counts', 'normalized_expression': 'normalized_expression', None: 'unknown'}
    assay = contract['assay']
    if assay['representation'] != expected_representation[inspection['representation']]:
        raise ValueError('Scientific assay representation contradicts the registered RNA declaration')
    if assay['orientation'] != 'feature_by_sample':
        raise ValueError('Scientific assay orientation contradicts the registered RNA axes')
    for field, inspected in (('feature_ids_sha256', 'gene_ids_sha256'),
                             ('observation_ids_sha256', 'sample_ids_sha256'), ('metadata_sha256', 'metadata_sha256')):
        if assay.get(field) != inspection[inspected]:
            raise ValueError('Scientific assay identifier or metadata digest contradicts the registered RNA input')
    declaration = inspection['biological_independence']
    independent_units = declaration.get('independent_units') if declaration is not None else None
    if independent_units is not True:
        if any(unit['independence'] == 'independent' for unit in contract['biological_units']):
            raise ValueError('Registered biological independence cannot be upgraded by a companion declaration')
    if independent_units is not True and independent_units is not False:
        if not any(item['field'] == 'biological_units' for item in contract['unknowns']):
            raise ValueError('Unknown registered biological independence must remain explicit')


def scientific_projection(value):
    """Initial scientific planning never receives execution capability requirements."""
    result = _typed(value)
    result.pop('execution')
    result.pop('replay')
    _reject_capabilities(result)
    return result


def retained_scientific_contract(plan, methods):
    """Reverify optional retained companions without backfilling legacy records."""
    contract = scientific_contract_material(plan)
    if contract is None:
        return None
    result = validate_scientific_contract(contract, methods=methods,
        artifact_ids=plan['context']['dataset_summary'].get('artifact_ids', []), task=plan['context']['task'],
        user_parameter_requirements=plan.get('user_parameter_requirements', []),
        rna_input_inspection=plan['context']['dataset_summary'].get('observed', {}).get('bulk_rna_input'))
    observed = plan['context']['dataset_summary'].get('observed', {})
    if 'scrna_input' in observed:
        from .rna_scrna_input_contract import match_scrna_scientific_contract
        match_scrna_scientific_contract(result, observed['scrna_input'])
    return result


def scientific_contract_material(plan, *, scientific_only=False):
    """Verify frozen companion identity for pure stage projections; no backfill."""
    if 'scientific_contract' not in plan:
        return None
    contract = plan['scientific_contract']
    if content_sha256(contract) != plan.get('scientific_contract_sha256'):
        raise ValueError('Retained scientific contract digest changed')
    return scientific_projection(contract) if scientific_only else _typed(contract)
