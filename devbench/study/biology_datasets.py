"""Protected biological dataset preparation; dormant until literal authorization.

Public receipts establish retained byte identity, not biological qualification,
authenticated approval, or operating-system isolation from other host processes.
The caller owns the coordinator snapshot and must exclude its private directory
from participant permissions. Preparation is synchronous and can be wrapped by
the existing coordinator process supervisor by a main-guarded caller.
"""

from __future__ import annotations

import csv
import hashlib
import io
from itertools import chain
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from .biology import BiologyCatalog, BiologyRecipe, CapabilityRegistration, RuntimeSupportRegistry, load_biology_catalog
from .biology_input_limits import (
    PUBLIC_DENIED_KEYS, PUBLIC_MAX_AGGREGATE_BYTES, PUBLIC_MAX_ANNOTATION_BYTES,
    PUBLIC_MAX_CHUNK_BYTES, PUBLIC_MAX_DECODED_LABEL_BYTES, PUBLIC_MAX_FEATURES,
    PUBLIC_MAX_JSON_DEPTH, PUBLIC_MAX_JSON_KEY_CHARS, PUBLIC_MAX_JSON_NODES,
    PUBLIC_MAX_JSON_STRING_CHARS, PUBLIC_MAX_LABEL_CHARS, PUBLIC_MAX_MATRIX_BYTES,
    PUBLIC_MAX_METADATA_BYTES, PUBLIC_MAX_METADATA_COLUMNS, PUBLIC_MAX_METADATA_FIELD_CHARS,
    PUBLIC_MAX_METADATA_HEADER_CHARS, PUBLIC_MAX_METADATA_ROWS, PUBLIC_MAX_TASK_BYTES,
    PUBLIC_MAX_ZARR_FILES, PUBLIC_MAX_ZARR_HEADER_BYTES, PUBLIC_MAX_ZARR_METADATA_BYTES,
    validate_expression_source_dtype, validate_public_structure,
)
from .contracts import FrozenContract, Identifier, Sha256, content_sha256
from .permissions import require_protected_storage
from .zarr_bounds import is_prepared_zarr_member, validate_prepared_zarr


MAX_INPUT_BYTES = PUBLIC_MAX_AGGREGATE_BYTES
MAX_MATRIX_BYTES = PUBLIC_MAX_MATRIX_BYTES
MAX_METADATA_BYTES = PUBLIC_MAX_METADATA_BYTES
MAX_TRUTH_BYTES = 50_000_000
MAX_MANIFEST_BYTES = 10_000_000
MAX_SOURCE_BYTES = 2_000_000
MAX_FILES = PUBLIC_MAX_ZARR_FILES + 3
MAX_PRIVATE_JSON_NODES = 5_000_000
MAX_PRIVATE_JSON_DEPTH = 80
_PUBLIC_FILES = {"inputs/metadata.csv", "inputs/task_inputs.json", "inputs/public_annotations.json"}
_BULK_CONTEXT = {
    "measurement", "biological_units", "design", "contrasts", "references",
    "requested_condition_contrast", "rhythm_analysis_domain", "timing_definitions",
}
_CELL_CONTEXT = {
    "assay", "measurement", "measurement_units", "matrix_orientation", "expression_layer",
    "transformation_history", "feature_universe", "biological_unit", "nested_observation_roles",
    "condition_assignment_unit", "independent_units_per_condition", "metadata_aliases",
    "capture_batch_definition", "condition_labels", "collection_age_units",
    "repeated_specimens_per_donor", "annotation_ids", "requested_targets",
    "absolute_cell_abundance_measured", "spliced_unspliced_layers_available",
    "mitochondrial_feature_annotation_available", "population_reference", "protocol", "limitations",
    "transcription_factor_annotation", "regulatory_evidence_limits", "primary_process",
    "cycle_phase_invariances", "history_evidence_scope", "target_contrasts", "target_populations",
    "target_references", "captured_abundance_reference_scope", "multiplet_identity_scope",
    "artifact_detection_calibration", "enrichment_query_gene_ids", "enrichment_background_gene_ids",
    "enrichment_query_selection", "shared_process_projection", "projection_interpretation",
}
_ANNOTATIONS = {
    "declared_external_controls", "gene_susceptibility", "cell_type_reference", "stable_reference_markers",
    "history_markers", "capture_calibration", "process_markers", "regulon_annotation",
    "motif_hit_annotation", "guide_assignments", "efficacy_measurement", "ambient_reference",
    "cell_annotation_reference",
}
_ENRICHMENT_TEXT = {"input", "transformation", "ranking", "variance_denominator", "query_count_rule",
                    "tie_break", "empty_library_policy", "background", "inference_scope"}
_ENRICHMENT_COUNTS = {"query_count", "nonempty_library_count", "positive_variance_gene_count", "working_bytes_upper_bound"}
_ENRICHMENT_FLAGS = {"selection_independent_of_tested_expression", "independent_assay",
                     "calibrated_p_values_or_fdr", "causal_evidence"}
_PRIVATE_KEYS = PUBLIC_DENIED_KEYS
_BASE_SOURCES = (
    "devbench/study/biology.py", "devbench/study/contracts.py", "devbench/study/biology_datasets.py",
    "devbench/study/biology_input_limits.py",
    "devbench/study/permissions.py", "devbench/study/zarr_bounds.py", "devharness_core/storage.py",
)
_GENERATOR_SOURCES = {
    "devbench.study.biology_bulk": "devbench/study/biology_bulk.py",
    "devbench.study.biology_singlecell": "devbench/study/biology_singlecell.py",
}


class PreparedBiologyBundle(FrozenContract):
    """Coordinator receipt; safe to retain, never a participant input object."""

    record_format: Literal["devbench.prepared-biology/1"] = "devbench.prepared-biology/1"
    root: Annotated[str, Field(min_length=1, max_length=4096)]
    input_sha256: Sha256
    input_manifest_sha256: Sha256
    truth_sha256: Sha256
    preparation_receipt_sha256: Sha256
    recipe_id: Identifier
    cohort_id: Identifier
    plan_identity: Sha256
    recipe_sha256: Sha256
    catalog_sha256: Sha256
    generator_sha256: Sha256
    generator_source_sha256: Sha256
    source_hashes: dict[str, Sha256]
    measurement: Literal["raw_counts", "umi_counts", "non_umi_counts", "continuous"]
    population_unit_label: Identifier
    unit_identity_status: Literal["declared_synthetic", "intentionally_unknown"]
    num_genes: Annotated[int, Field(strict=True, ge=1, le=PUBLIC_MAX_FEATURES)]
    num_observations: Annotated[int, Field(strict=True, ge=1, le=PUBLIC_MAX_METADATA_ROWS)]

    @model_validator(mode="after")
    def exact_receipt_configuration(self) -> PreparedBiologyBundle:
        path = PurePosixPath(self.root)
        if not path.is_absolute() or self.root.startswith("//") or ".." in path.parts or str(path) != self.root:
            raise ValueError("prepared root must be a canonical absolute path")
        selected = set(self.source_hashes) & set(_GENERATOR_SOURCES.values())
        if len(selected) != 1 or set(self.source_hashes) not in (
                set(_BASE_SOURCES) | selected,
                set(_BASE_SOURCES) | selected | {"devbench/study/biology_catalog.json"}):
            raise ValueError("receipt source hashes must use approved relative source filenames")
        if self.generator_source_sha256 != self.source_hashes[next(iter(selected))]:
            raise ValueError("primary generator source differs from its source receipt")
        if self.generator_sha256 != content_sha256(self.source_hashes):
            raise ValueError("generator identity differs from the retained source receipts")
        return self

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def biology_generator_registry(catalog: BiologyCatalog) -> RuntimeSupportRegistry:
    """Exact callable generator support only; no generation or numerical imports."""
    from .biology_bulk import biology_bulk_registrations
    from .biology_singlecell import generate_biology_singlecell, supported_biology_singlecell_cases

    registrations = list(biology_bulk_registrations(catalog))
    implemented = set(supported_biology_singlecell_cases())
    for template in catalog.templates:
        for measurement in ("umi_counts", "non_umi_counts"):
            case_ids = tuple(case.case_id for case in template.cases
                             if case.case_id in implemented and case.measurement == measurement)
            if case_ids:
                registrations.append(CapabilityRegistration(
                    kind="generator", capability_id=template.generator_capability_id,
                    template_id=template.template_id, case_ids=case_ids,
                    target_ids=tuple(target.target_id for target in template.targets),
                    measurement=measurement, handler=generate_biology_singlecell,
                ))
    return RuntimeSupportRegistry(tuple(registrations))


def _generator(recipe: BiologyRecipe, catalog: BiologyCatalog):
    case = recipe.validate_against(catalog)
    template = catalog.template(recipe.template_id)
    matches = [item for item in biology_generator_registry(catalog).registrations
               if item.template_id == template.template_id and recipe.case_id in item.case_ids
               and item.measurement == case.measurement
               and set(item.target_ids) == {target.target_id for target in template.targets}]
    if len(matches) != 1:
        raise ValueError("recipe requires exactly one registered, complete generator capability")
    item = matches[0]
    if item.handler.__module__ not in _GENERATOR_SOURCES:
        raise ValueError("generator must originate in the approved biological source modules")
    return item


def validate_biology_public_transport(recipe: BiologyRecipe, catalog: BiologyCatalog) -> dict[str, Any]:
    """Pure necessary public-size checks; actual output must also pass all caps.

    The registered cell generator emits the declared number of observations and
    seven opaque identifier fields (including the sample alias) per CSV record.
    Each uses a nonempty prefix, separator and 24 hexadecimal characters. This
    is a serialization lower bound, not generated metadata or a power claim.
    """
    if not isinstance(recipe, BiologyRecipe):
        raise TypeError("public transport admission requires BiologyRecipe")
    case = recipe.validate_against(catalog)
    p = recipe.parameters
    observations = p.observation_count_upper_bound
    if p.num_genes > PUBLIC_MAX_FEATURES or observations > PUBLIC_MAX_METADATA_ROWS:
        raise ValueError("planned public dimensions exceed the shared feature/metadata row bound")
    if p.num_genes * observations * 8 > PUBLIC_MAX_MATRIX_BYTES:
        raise ValueError("planned expression exceeds the shared public matrix byte bound")
    metadata_minimum = 0
    annotation_minimum_nodes = 1
    if p.cells_per_specimen:
        # Seven IDs plus condition, delimiters and newline; chronology and the
        # header can only increase this bound. No label records are instantiated.
        row_minimum = 7 * 26 + min(len(label.encode("utf-8")) for label in p.condition_labels) + 8
        metadata_minimum = observations * row_minimum
        if metadata_minimum > PUBLIC_MAX_METADATA_BYTES:
            raise ValueError("planned cell metadata cannot fit the shared public byte bound")
        declared = set(case.public_annotation_ids)
        if "guide_assignments" in declared:
            annotation_minimum_nodes += 5 + 4 * observations
        if "efficacy_measurement" in declared:
            annotation_minimum_nodes += 3 + 4 * observations
        if "history_markers" in declared:
            annotation_minimum_nodes += 7 + 3 * observations
        if annotation_minimum_nodes > PUBLIC_MAX_JSON_NODES:
            raise ValueError("planned public annotation records exceed the shared JSON node bound")
    return {"observation_upper_bound": observations, "metadata_bytes_lower_bound": metadata_minimum,
            "annotation_nodes_lower_bound": annotation_minimum_nodes,
            "unit_identity_status": case.biological_identity_status,
            "actual_byte_and_structure_validation_required": True}


def is_biology_public_member(name: str) -> bool:
    """Pure allowlist for public manifest paths."""
    if not isinstance(name, str) or PurePosixPath(name).as_posix() != name:
        return False
    if name in _PUBLIC_FILES:
        return True
    prefix = "inputs/counts.zarr/"
    return name.startswith(prefix) and is_prepared_zarr_member(name[len(prefix):])


def _path(value: Any, *, protected: bool = False) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("paths must be absolute without traversal")
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("coordinator paths must not contain symlinks")
    path = path.resolve()
    return require_protected_storage(path) if protected else path


def _hash_file(path: Path, limit: int) -> tuple[str, int]:
    path = _path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError("receipt file exceeds its regular-file byte bound")
    digest, count = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(min(1_048_576, limit - count + 1)):
            count += len(chunk)
            if count > limit:
                raise ValueError("receipt file exceeds its read bound")
            digest.update(chunk)
    if count != info.st_size:
        raise ValueError("receipt file changed during bounded hashing")
    return digest.hexdigest(), count


def _decode_json(raw: bytes) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("repeated JSON key")
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("JSON values must be finite")

    def finite_float(token):
        value = float(token)
        if not math.isfinite(value):
            raise ValueError("JSON values must be finite")
        return value

    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid, parse_float=finite_float)
    if not isinstance(result, dict):
        raise ValueError("coordinator JSON must contain an object")
    return result


def _read_json(path: Path, limit: int, *, public: bool = False) -> dict[str, Any]:
    _hash_file(path, limit)
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("JSON read exceeds its byte bound")
    result = _decode_json(raw)
    if public:
        validate_public_structure(result)
    return result


def _json_parts(value: Any, np: Any = None, *, depth: int = 0, budget: list[int] | None = None,
                public: bool = False):
    """Stream arrays without full .tolist() copies; retain None and reject NaN."""
    nodes = PUBLIC_MAX_JSON_NODES if public else MAX_PRIVATE_JSON_NODES
    max_depth = PUBLIC_MAX_JSON_DEPTH if public else MAX_PRIVATE_JSON_DEPTH
    budget = [nodes] if budget is None else budget
    if public and budget[0] > PUBLIC_MAX_JSON_NODES:
        raise ValueError("public JSON structural bound cannot be enlarged")
    if np is not None and isinstance(value, np.ndarray) and value.ndim == 0:
        value = value.item()
    budget[0] -= 1
    if budget[0] < 0 or depth > max_depth:
        raise ValueError("structured JSON exceeds its node/depth bound")
    if np is not None and isinstance(value, np.generic):
        value = value.item()
    if value is None or isinstance(value, (str, bool, int, float)):
        if public and isinstance(value, str) and len(value) > PUBLIC_MAX_JSON_STRING_CHARS:
            raise ValueError("public JSON string exceeds its shared bound")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("JSON values must be finite; missing values must be explicit None")
        yield json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    elif isinstance(value, dict):
        if any(not isinstance(key, str) or len(key) > (PUBLIC_MAX_JSON_KEY_CHARS if public else 3000) for key in value):
            raise ValueError("structured JSON keys must be bounded strings")
        if public and set(value) & PUBLIC_DENIED_KEYS:
            raise ValueError("public JSON cannot contain private or coordinator fields")
        yield "{"
        for index, key in enumerate(sorted(value)):
            if index:
                yield ","
            yield json.dumps(key, ensure_ascii=False)
            yield ":"
            yield from _json_parts(value[key], np, depth=depth + 1, budget=budget, public=public)
        yield "}"
    elif isinstance(value, (tuple, list)) or (np is not None and isinstance(value, np.ndarray)):
        yield "["
        for index, item in enumerate(value):
            if index:
                yield ","
            yield from _json_parts(item, np, depth=depth + 1, budget=budget, public=public)
        yield "]"
    else:
        raise TypeError("only structured JSON and numeric arrays can be serialized")


def _write_json(path: Path, value: Any, limit: int, np: Any = None, *, public: bool = False) -> None:
    # Failed writes remain bounded partial evidence. No receipt is returned until
    # all files, frames, mappings and identities have been verified and sealed.
    path = _path(path)
    if public:
        limit = min(limit, max(PUBLIC_MAX_TASK_BYTES, PUBLIC_MAX_ANNOTATION_BYTES))
    total = 0
    with path.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        for part in _json_parts(value, np, public=public):
            encoded = part.encode("utf-8")
            total += len(encoded)
            if total > limit:
                raise ValueError("JSON output exceeds its byte bound; partial output retained")
            stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    if public:
        _read_json(path, limit, public=True)


def _public_value(value: Any, recipe: BiologyRecipe, *, depth: int = 0) -> None:
    if depth > PUBLIC_MAX_JSON_DEPTH:
        raise ValueError("public context exceeds its structural bound")
    if isinstance(value, dict):
        if set(value) & _PRIVATE_KEYS:
            raise ValueError("private recipe/truth fields cannot enter public inputs")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("public keys must be strings")
            _public_value(item, recipe, depth=depth + 1)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _public_value(item, recipe, depth=depth + 1)
    elif isinstance(value, str):
        if recipe.case_id in value or re.search(r"\bT(?:0[1-9]|1[0-9]|20)\b", value):
            raise ValueError("case/template labels cannot enter public inputs")


def _questions(catalog: BiologyCatalog, recipe: BiologyRecipe,
               public_context: dict[str, Any] | None = None) -> tuple[dict[str, Any], ...]:
    """Bind public questions to declared, publicly supplied reference definitions."""
    targets = catalog.template(recipe.template_id).targets
    target_ids = {target.target_id for target in targets}
    context = {} if public_context is None else public_context
    if not isinstance(context, dict):
        raise ValueError("public question context must be an object")
    _public_value(context, recipe)
    populations = context.get("target_populations", {})
    references = context.get("target_references", {})
    reference_fields = {"estimand", "contrast", "units", "reference_population", "prediction_reference"}
    for mapping in (populations, references):
        if not isinstance(mapping, dict) or set(mapping) - target_ids:
            raise ValueError("public reference override names an unknown question target")
    if any(not isinstance(value, str) or not 1 <= len(value) <= 3000 for value in populations.values()):
        raise ValueError("public target populations must be bounded reference text")
    for reference in references.values():
        if (not isinstance(reference, dict) or not reference or set(reference) - reference_fields
                or any(not isinstance(value, str) or not 1 <= len(value) <= 3000 for value in reference.values())):
            raise ValueError("public target references must contain only bounded canonical reference fields")
    questions = []
    for target in targets:
        question = {"target_id": target.target_id, "estimand": target.estimand,
                    "contrast": target.contrast, "units": target.measurement_units,
                    "reference_population": populations.get(target.target_id, target.reference_population),
                    "prediction_reference": target.prediction_reference,
                    "required_answer_fields": target.required_answer_fields,
                    "identification_assumptions": target.identification_assumptions,
                    "forbidden_claims": target.forbidden_claims}
        # The explicit canonical reference is more precise than a general
        # population description. No scope/truth fields are accepted here.
        question.update(references.get(target.target_id, {}))
        if target.shared_process_projection is not None:
            question["projection_contract"] = target.shared_process_projection.model_dump(mode="json")
        questions.append(question)
    return tuple(questions)


def _annotation_contract(annotations: dict[str, Any], recipe: BiologyRecipe, catalog: BiologyCatalog) -> None:
    case = next(case for case in catalog.template(recipe.template_id).cases if case.case_id == recipe.case_id)
    expected = set(case.public_annotation_ids)
    if "rna_content_reference" in expected:
        # The bulk generator bundles this declared assay reference with its
        # type-expression reference; it is not a separate top-level artifact.
        expected.remove("rna_content_reference")
        reference = annotations.get("cell_type_reference")
        if not isinstance(reference, dict) or "rna_content" not in reference:
            raise ValueError("declared RNA-content reference is missing")
    if set(annotations) != expected:
        raise ValueError("public annotations differ from the exact case evidence contract")


def _feature_ids(value: Any, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or not (0 if allow_empty else 1) <= len(value) <= 5000:
        raise ValueError("public feature identifiers must be a bounded sequence")
    if any(not isinstance(item, str) or not 1 <= len(item) <= 200 for item in value) or len(set(value)) != len(value):
        raise ValueError("public feature identifiers must be distinct bounded strings")
    return tuple(value)


def _cell_extension_contract(context: dict[str, Any], annotations: dict[str, Any],
                             recipe: BiologyRecipe, catalog: BiologyCatalog) -> None:
    """Pure validation of declared feature views and public selection metadata."""
    _public_value(context, recipe)
    projection = context.get("shared_process_projection")
    declared = [target.shared_process_projection.model_dump(mode="json")
                for target in catalog.template(recipe.template_id).targets
                if target.shared_process_projection is not None]
    if declared:
        expected = declared[0]
        if any(item != expected for item in declared):
            raise ValueError("one public feature view cannot satisfy different declared projection contracts")
        if not isinstance(projection, dict) or set(projection) != set(expected) | {"excluded_feature_ids", "retained_feature_universe"}:
            raise ValueError("public projection must match the exact typed projection contract")
        if any(type(projection[key]) is not type(value) or projection[key] != value for key, value in expected.items()):
            raise ValueError("public projection changes a typed projection constraint")
        universe = _feature_ids(context.get("feature_universe"))
        if len(universe) != recipe.parameters.num_genes:
            raise ValueError("projection universe differs from the declared measured gene count")
        excluded = _feature_ids(projection["excluded_feature_ids"])
        retained = _feature_ids(projection["retained_feature_universe"])
        excluded_set = set(excluded)
        if excluded_set & set(retained) or excluded_set | set(retained) != set(universe):
            raise ValueError("public projection must partition the complete measured feature universe")
        if retained != tuple(gene for gene in universe if gene not in excluded_set):
            raise ValueError("retained projection features must preserve the measured gene order")
        marker_reference = annotations.get(expected["marker_reference_id"])
        if not isinstance(marker_reference, dict) or not isinstance(marker_reference.get("markers"), (tuple, list)):
            raise ValueError("projection requires its declared public marker reference")
        markers = marker_reference["markers"]
        if not 1 <= len(markers) <= 5000:
            raise ValueError("public marker reference exceeds its bounded record count")
        marker_union, marker_mentions = set(), 0
        for marker in markers:
            if not isinstance(marker, dict) or set(marker) != {"label", "gene_ids"}:
                raise ValueError("projection marker records require label and gene_ids only")
            if not isinstance(marker["label"], str) or not 1 <= len(marker["label"]) <= 3000:
                raise ValueError("public marker labels must be bounded text")
            marker_ids = _feature_ids(marker["gene_ids"])
            marker_mentions += len(marker_ids)
            if marker_mentions > 25_000:
                raise ValueError("public marker reference exceeds its feature-mention bound")
            marker_union.update(marker_ids)
        if marker_union != excluded_set:
            raise ValueError("projection exclusions must equal the declared marker union")
    elif projection is not None:
        raise ValueError("public projection has no matching question contract")
    query_keys = {"enrichment_query_gene_ids", "enrichment_background_gene_ids", "enrichment_query_selection"}
    if set(context) & query_keys:
        if not query_keys <= set(context):
            raise ValueError("public enrichment selection requires query, background and selection metadata")
        universe = _feature_ids(context.get("feature_universe"))
        if len(universe) != recipe.parameters.num_genes:
            raise ValueError("enrichment universe differs from the declared measured gene count")
        query = _feature_ids(context["enrichment_query_gene_ids"])
        background = _feature_ids(context["enrichment_background_gene_ids"])
        if background != universe or not set(query) <= set(background):
            raise ValueError("enrichment background must be the complete measured feature universe")
        selection = context["enrichment_query_selection"]
        if not isinstance(selection, dict) or set(selection) != _ENRICHMENT_TEXT | _ENRICHMENT_COUNTS | _ENRICHMENT_FLAGS:
            raise ValueError("public enrichment selection has an unexpected schema")
        if any(not isinstance(selection[key], str) or not 1 <= len(selection[key]) <= 3000 for key in _ENRICHMENT_TEXT):
            raise ValueError("enrichment selection text must remain bounded")
        if any(type(selection[key]) is not bool for key in _ENRICHMENT_FLAGS):
            raise ValueError("enrichment selection evidence flags must be explicit booleans")
        if any(type(selection[key]) is not int or not 0 <= selection[key] <= 100_000_000 for key in _ENRICHMENT_COUNTS):
            raise ValueError("enrichment selection counts exceed their bounded integer schema")
        if (selection["query_count"] != len(query) or selection["nonempty_library_count"] < 2
                or selection["nonempty_library_count"] > recipe.parameters.observation_count_upper_bound
                or selection["positive_variance_gene_count"] > len(universe)
                or selection["working_bytes_upper_bound"] > recipe.parameters.max_matrix_bytes):
            raise ValueError("enrichment selection metadata disagrees with its declared feature/reference bounds")


def _context_and_annotations(generated: Any, recipe: BiologyRecipe, catalog: BiologyCatalog):
    allowed = _CELL_CONTEXT if recipe.parameters.cells_per_specimen else _BULK_CONTEXT
    if not isinstance(generated.public_context, dict) or set(generated.public_context) - allowed:
        raise ValueError("generator emitted an unexpected public context key")
    if not isinstance(generated.public_annotations, dict) or set(generated.public_annotations) - _ANNOTATIONS:
        raise ValueError("generator emitted an unexpected public annotation object")
    _annotation_contract(generated.public_annotations, recipe, catalog)
    if recipe.parameters.cells_per_specimen:
        if tuple(generated.public_context.get("feature_universe", ())) != tuple(generated.gene_ids):
            raise ValueError("public feature universe differs from the measured expression gene axis")
        _cell_extension_contract(generated.public_context, generated.public_annotations, recipe, catalog)
    _public_value(generated.public_context, recipe)
    _public_value(generated.public_annotations, recipe)
    questions = _questions(catalog, recipe, generated.public_context)
    _public_value(questions, recipe)
    return {"record_format": "devbench.biology-task-inputs/1", "context": generated.public_context,
            "measurement": generated.measurement, "population_unit_label": catalog.template(recipe.template_id).unit_hierarchy.population_unit_label,
            "observation_id_label": "sample_id", "expression_path": "counts.zarr",
            "metadata_path": "metadata.csv", "public_annotations_path": "public_annotations.json",
            "questions": questions}, generated.public_annotations


def _source_hashes(handler: Any) -> tuple[dict[str, str], str, str]:
    repository = Path(__file__).resolve().parents[2]
    generator_source = _GENERATOR_SOURCES[handler.__module__]
    hashes = {name: _hash_file(repository / name, MAX_SOURCE_BYTES)[0]
              for name in (*_BASE_SOURCES, generator_source)}
    # Plan/cohort identity uses the same validated semantic catalog. Preserve
    # its exact serialized source independently, just like generator sources.
    hashes["devbench/study/biology_catalog.json"] = _hash_file(
        repository / "devbench/study/biology_catalog.json", MAX_SOURCE_BYTES)[0]
    catalog_hash = content_sha256(load_biology_catalog().model_dump(mode="json"))
    return hashes, hashes[generator_source], catalog_hash


def _identity(plan_identity: str, cohort_id: str) -> None:
    if not isinstance(plan_identity, str) or re.fullmatch(r"[0-9a-f]{64}", plan_identity) is None:
        raise ValueError("plan_identity must be a lowercase SHA-256 digest")
    if not isinstance(cohort_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", cohort_id) is None:
        raise ValueError("cohort_id must be a bounded identifier")


def _metadata(generated: Any, recipe: BiologyRecipe, catalog: BiologyCatalog) -> tuple[dict[str, Any], ...]:
    ids = tuple(generated.sample_ids)
    genes = tuple(generated.gene_ids)
    for labels in (ids, genes):
        if not labels or len(set(labels)) != len(labels) or any(not isinstance(value, str) or not 1 <= len(value) <= PUBLIC_MAX_LABEL_CHARS for value in labels):
            raise ValueError("observation and gene labels must be nonempty, unique and bounded")
    if len(generated.metadata) != len(ids) or len(genes) != recipe.parameters.num_genes:
        raise ValueError("generator identifiers do not match its declared observations")
    if len(ids) > recipe.parameters.observation_count_upper_bound:
        raise ValueError("generator exceeded declared observation bounds")
    template = catalog.template(recipe.template_id)
    case = next(case for case in template.cases if case.case_id == recipe.case_id)
    hierarchy = template.unit_hierarchy
    required = (hierarchy.population_unit_label, hierarchy.specimen_label, hierarchy.library_label)
    rows = []
    for identifier, original in zip(ids, generated.metadata):
        if not isinstance(original, dict) or set(original) & _PRIVATE_KEYS:
            raise ValueError("metadata must be public scalar records")
        row = dict(original)
        if hierarchy.cell_label is not None:
            if row.get(hierarchy.cell_label) != identifier:
                raise ValueError("cell identifiers must match expression columns")
        if "sample_id" in row and row["sample_id"] != identifier:
            raise ValueError("sample alias disagrees with the expression column")
        row["sample_id"] = identifier
        for label in required:
            if label not in row:
                raise ValueError("required biological hierarchy label is absent")
            if row[label] is None and not (label == hierarchy.population_unit_label and case.biological_identity_status == "intentionally_unknown"):
                raise ValueError("required biological hierarchy identity is missing")
        for key, value in row.items():
            if not isinstance(key, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,159}", key) is None:
                raise ValueError("metadata field names must be bounded identifiers")
            if value is not None and type(value) not in (str, int, float, bool):
                raise ValueError("CSV metadata fields must be scalar")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("CSV metadata must be finite")
            if isinstance(value, str) and len(value) > PUBLIC_MAX_METADATA_FIELD_CHARS:
                raise ValueError("CSV metadata field exceeds its bound")
        _public_value(row, recipe)
        rows.append(row)
    return tuple(rows)


def _write_metadata(path: Path, rows: tuple[dict[str, Any], ...], limit: int = MAX_METADATA_BYTES) -> None:
    headers = ("sample_id", *sorted(set().union(*(row.keys() for row in rows)) - {"sample_id"}))
    if (len(rows) > PUBLIC_MAX_METADATA_ROWS or len(headers) > PUBLIC_MAX_METADATA_COLUMNS
            or any(not header or len(header) > PUBLIC_MAX_METADATA_HEADER_CHARS for header in headers)):
        raise ValueError("metadata rows/headers exceed the shared public bounds")
    limit = min(limit, PUBLIC_MAX_METADATA_BYTES)
    total = 0
    with path.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        for values in chain((headers,), (tuple(row.get(key) for key in headers) for row in rows)):
            if any(value is not None and len(str(value)) > PUBLIC_MAX_METADATA_FIELD_CHARS for value in values):
                raise ValueError("metadata field exceeds the shared public bound")
            buffer = io.StringIO(newline="")
            csv.writer(buffer, lineterminator="\n").writerow(values)
            encoded = buffer.getvalue().encode("utf-8")
            total += len(encoded)
            if total > limit:
                raise ValueError("metadata CSV exceeds its byte bound; partial output retained")
            stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def _remaining_public_bytes(root: Path) -> int:
    total = 0
    for index, path in enumerate((root / "inputs").rglob("*")):
        if index > MAX_FILES + 10 or path.is_symlink():
            raise ValueError("public tree exceeds its bounded member contract")
        if path.is_file():
            total += path.stat().st_size
            if total > MAX_INPUT_BYTES:
                raise ValueError("public inputs exceed their aggregate byte bound")
    return MAX_INPUT_BYTES - total


def _public_receipts(root: Path) -> dict[str, dict[str, Any]]:
    inputs = root / "inputs"
    allowed_directories = {"inputs/counts.zarr", "inputs/counts.zarr/expression", "inputs/counts.zarr/gene_names", "inputs/counts.zarr/sample_names"}
    receipts, total = {}, 0
    for index, path in enumerate(inputs.rglob("*")):
        if index > MAX_FILES + 10:
            raise ValueError("public input traversal exceeds its member-count bound")
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError("public input trees cannot contain symlinks")
        if path.is_dir():
            if relative not in allowed_directories:
                raise ValueError("public inputs contain an undeclared directory")
            continue
        if not is_biology_public_member(relative) or path.stat().st_nlink != 1:
            raise ValueError("public inputs contain an undeclared or linked member")
        if relative == "inputs/metadata.csv":
            limit = PUBLIC_MAX_METADATA_BYTES
        elif relative == "inputs/task_inputs.json":
            limit = PUBLIC_MAX_TASK_BYTES
        elif relative == "inputs/public_annotations.json":
            limit = PUBLIC_MAX_ANNOTATION_BYTES
        elif path.name in {".zarray", ".zgroup", ".zattrs"}:
            limit = PUBLIC_MAX_ZARR_HEADER_BYTES
        else:
            limit = PUBLIC_MAX_CHUNK_BYTES + 65_536
        digest, size = _hash_file(path, limit)
        total += size
        if total > MAX_INPUT_BYTES or len(receipts) >= MAX_FILES:
            raise ValueError("public inputs exceed their aggregate byte/file bound")
        receipts[relative] = {"sha256": digest, "size_bytes": size}
    if not _PUBLIC_FILES <= set(receipts):
        raise ValueError("required public inputs are missing")
    return receipts


def _sealed(root: Path) -> None:
    for index, path in enumerate(chain((root,), root.rglob("*"))):
        if index > MAX_FILES + 20:
            raise ValueError("prepared snapshot exceeds its member-count bound")
        info = path.lstat()
        if path.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o277:
            raise ValueError("prepared snapshots must be sealed, owner-only coordinator files and directories")


def _seal(root: Path) -> None:
    for path in root.rglob("*"):
        path.chmod(0o500 if path.is_dir() else 0o400)
    root.chmod(0o500)


def prepare_biology_dataset(recipe: BiologyRecipe, output_dir: Path, *, plan_identity: str,
                            cohort_id: str, allow_data_execution: bool = False) -> PreparedBiologyBundle:
    if allow_data_execution is not True:
        raise PermissionError("biological dataset preparation requires allow_data_execution=True")
    if not isinstance(recipe, BiologyRecipe):
        raise TypeError("preparation requires BiologyRecipe")
    _identity(plan_identity, cohort_id)
    catalog = load_biology_catalog()
    registration = _generator(recipe, catalog)
    validate_biology_public_transport(recipe, catalog)
    root = _path(output_dir, protected=True)
    if root.exists():
        raise FileExistsError("prepared destination must be a fresh directory")
    if not root.parent.is_dir() or root.parent.stat().st_uid != os.getuid() or root.parent.stat().st_mode & 0o022:
        raise ValueError("prepared parent must be an existing coordinator-owned directory without shared writes")
    source_hashes, generator_source_hash, catalog_hash = _source_hashes(registration.handler)
    root.mkdir(mode=0o700)
    (root / "inputs").mkdir(mode=0o700)
    (root / "private").mkdir(mode=0o700)
    generated = registration.handler(recipe, allow_data_execution=True)
    rows = _metadata(generated, recipe, catalog)
    task_inputs, annotations = _context_and_annotations(generated, recipe, catalog)
    if generated.measurement != registration.measurement:
        raise ValueError("actual generator measurement differs from its registered capability")
    import numpy as np
    import pandas as pd
    from devharness_core.storage import ChunkedExpressionStore

    shape = (len(generated.gene_ids), len(generated.sample_ids))
    expression = generated.expression
    if (not isinstance(expression, np.ndarray) or expression.shape != shape
            or expression.nbytes > MAX_MATRIX_BYTES or expression.dtype.kind not in "iuf"
            or not np.all(np.isfinite(expression))):
        raise ValueError("generated expression violates finite matrix bounds")
    if generated.measurement != "continuous" and (expression.dtype.kind not in "iu" or np.any(expression < 0)):
        raise ValueError("count measurements require nonnegative integer storage")
    if generated.measurement == "continuous" and expression.dtype.kind != "f":
        raise ValueError("continuous measurements require floating-point storage without count conversion")
    if sum(max(map(len, labels)) * 4 * len(labels) for labels in (generated.gene_ids, generated.sample_ids)) > PUBLIC_MAX_DECODED_LABEL_BYTES:
        raise ValueError("decoded label arrays exceed strict Zarr metadata bounds")
    frame = pd.DataFrame(expression, index=generated.gene_ids, columns=generated.sample_ids, copy=False)
    with ChunkedExpressionStore.from_dataframe_to_zarr(frame, root / "inputs/counts.zarr",
                                                       chunk_shape=(min(250, shape[0]), min(50, shape[1]))):
        pass
    validate_prepared_zarr(root / "inputs/counts.zarr", expected_shape=shape,
                          measurement_type="continuous" if generated.measurement == "continuous" else "raw_counts",
                          max_chunk_bytes=PUBLIC_MAX_CHUNK_BYTES,
                          max_metadata_bytes=PUBLIC_MAX_ZARR_METADATA_BYTES, max_matrix_bytes=PUBLIC_MAX_MATRIX_BYTES)
    _write_metadata(root / "inputs/metadata.csv", rows, min(MAX_METADATA_BYTES, _remaining_public_bytes(root)))
    _write_json(root / "inputs/task_inputs.json", task_inputs, min(PUBLIC_MAX_TASK_BYTES, _remaining_public_bytes(root)), np, public=True)
    _write_json(root / "inputs/public_annotations.json", annotations, min(PUBLIC_MAX_ANNOTATION_BYTES, _remaining_public_bytes(root)), np, public=True)
    _write_json(root / "private/truth.json", generated.private_truth, MAX_TRUTH_BYTES, np)
    files = _public_receipts(root)
    manifest = {"record_format": "devbench.biology-input-manifest/1", "scope": "public_inputs_only",
                "measurement": generated.measurement, "input_sha256": content_sha256(files), "files": files}
    _write_json(root / "input_manifest.json", manifest, MAX_MANIFEST_BYTES)
    template = catalog.template(recipe.template_id)
    case = next(case for case in template.cases if case.case_id == recipe.case_id)
    receipt = {"record_format": "devbench.prepared-biology/1", "root": str(root),
               "input_sha256": manifest["input_sha256"],
               "input_manifest_sha256": _hash_file(root / "input_manifest.json", MAX_MANIFEST_BYTES)[0],
               "truth_sha256": _hash_file(root / "private/truth.json", MAX_TRUTH_BYTES)[0],
               "recipe_id": recipe.recipe_id, "cohort_id": cohort_id, "plan_identity": plan_identity,
               "recipe_sha256": content_sha256(recipe.model_dump(mode="json")),
               "catalog_sha256": catalog_hash, "generator_sha256": content_sha256(source_hashes),
               "generator_source_sha256": generator_source_hash, "source_hashes": source_hashes,
               "measurement": generated.measurement,
               "population_unit_label": template.unit_hierarchy.population_unit_label,
               "unit_identity_status": case.biological_identity_status,
               "num_genes": shape[0], "num_observations": shape[1]}
    _write_json(root / "private/preparation_receipt.json", receipt, MAX_MANIFEST_BYTES)
    bundle = PreparedBiologyBundle(**receipt, preparation_receipt_sha256=_hash_file(root / "private/preparation_receipt.json", MAX_MANIFEST_BYTES)[0])
    _seal(root)
    return verify_prepared_biology_bundle(bundle, recipe, plan_identity, allow_data_execution=True)


def verify_prepared_biology_bundle(bundle: PreparedBiologyBundle | dict[str, Any], recipe: BiologyRecipe,
                                   plan_identity: str, *, allow_data_execution: bool = False) -> PreparedBiologyBundle:
    if allow_data_execution is not True:
        raise PermissionError("biological dataset verification requires allow_data_execution=True")
    bundle = PreparedBiologyBundle.model_validate(bundle.model_dump(mode="json") if isinstance(bundle, PreparedBiologyBundle) else bundle)
    _identity(plan_identity, bundle.cohort_id)
    if not isinstance(recipe, BiologyRecipe):
        raise TypeError("verification requires BiologyRecipe")
    if (plan_identity != bundle.plan_identity or recipe.recipe_id != bundle.recipe_id
            or content_sha256(recipe.model_dump(mode="json")) != bundle.recipe_sha256):
        raise ValueError("prepared cohort differs from its retained plan/recipe identity")
    if (bundle.num_genes != recipe.parameters.num_genes
            or bundle.num_observations > recipe.parameters.observation_count_upper_bound):
        raise ValueError("prepared dimensions differ from the declared recipe bounds")
    root = _path(bundle.root, protected=True)
    _sealed(root)
    if {path.name for path in root.iterdir()} != {"inputs", "private", "input_manifest.json"}:
        raise ValueError("prepared root has undeclared members")
    if {path.name for path in (root / "private").iterdir()} != {"truth.json", "preparation_receipt.json"}:
        raise ValueError("private directory has undeclared members")
    for name, expected, limit in (("input_manifest.json", bundle.input_manifest_sha256, MAX_MANIFEST_BYTES),
                                   ("private/truth.json", bundle.truth_sha256, MAX_TRUTH_BYTES),
                                   ("private/preparation_receipt.json", bundle.preparation_receipt_sha256, MAX_MANIFEST_BYTES)):
        if _hash_file(root / name, limit)[0] != expected:
            raise ValueError("prepared receipt or truth bytes changed")
    # Verification hashes truth bytes only. It never deserializes or returns truth.
    receipt = _read_json(root / "private/preparation_receipt.json", MAX_MANIFEST_BYTES)
    if receipt != bundle.model_dump(mode="json", exclude={"preparation_receipt_sha256"}):
        raise ValueError("retained preparation receipt differs from its external identity")
    catalog = load_biology_catalog()
    registration = _generator(recipe, catalog)
    validate_biology_public_transport(recipe, catalog)
    case = next(case for case in catalog.template(recipe.template_id).cases if case.case_id == recipe.case_id)
    if bundle.unit_identity_status != case.biological_identity_status:
        raise ValueError("unit identity status differs from the declared evidence")
    sources, generator_source_hash, catalog_hash = _source_hashes(registration.handler)
    if (sources != bundle.source_hashes or generator_source_hash != bundle.generator_source_sha256
            or catalog_hash != bundle.catalog_sha256 or registration.measurement != bundle.measurement):
        raise ValueError("generator/catalog source or measurement differs from the frozen preparation")
    manifest = _read_json(root / "input_manifest.json", MAX_MANIFEST_BYTES)
    if (set(manifest) != {"record_format", "scope", "measurement", "input_sha256", "files"}
            or manifest["record_format"] != "devbench.biology-input-manifest/1" or manifest["scope"] != "public_inputs_only"
            or manifest["measurement"] != bundle.measurement):
        raise ValueError("public manifest scope/schema/measurement is invalid")
    files = _public_receipts(root)
    if (manifest["files"] != files or manifest["input_sha256"] != content_sha256(files)
            or bundle.input_sha256 != content_sha256(files)):
        raise ValueError("public inputs differ from the retained manifest identity")
    task = _read_json(root / "inputs/task_inputs.json", PUBLIC_MAX_TASK_BYTES, public=True)
    annotations = _read_json(root / "inputs/public_annotations.json", PUBLIC_MAX_ANNOTATION_BYTES, public=True)
    allowed = _CELL_CONTEXT if recipe.parameters.cells_per_specimen else _BULK_CONTEXT
    if (set(task) != {"record_format", "context", "measurement", "population_unit_label", "observation_id_label",
                     "expression_path", "metadata_path", "public_annotations_path", "questions"}
            or task["record_format"] != "devbench.biology-task-inputs/1"
            or not isinstance(task["context"], dict) or set(task["context"]) - allowed
            or set(annotations) - _ANNOTATIONS or task["measurement"] != bundle.measurement
            or task["population_unit_label"] != bundle.population_unit_label
            or task["observation_id_label"] != "sample_id" or task["expression_path"] != "counts.zarr"
            or task["metadata_path"] != "metadata.csv" or task["public_annotations_path"] != "public_annotations.json"):
        raise ValueError("public task inputs have invalid paths/context/measurement")
    _public_value(task, recipe)
    _public_value(annotations, recipe)
    _annotation_contract(annotations, recipe, catalog)
    if recipe.parameters.cells_per_specimen:
        _cell_extension_contract(task["context"], annotations, recipe, catalog)
    if content_sha256(task["questions"]) != content_sha256(_questions(catalog, recipe, task["context"])):
        raise ValueError("public question contract differs from the declared recipe target contract")
    validate_prepared_zarr(root / "inputs/counts.zarr", expected_shape=(bundle.num_genes, bundle.num_observations),
                          measurement_type="continuous" if bundle.measurement == "continuous" else "raw_counts",
                          max_chunk_bytes=PUBLIC_MAX_CHUNK_BYTES,
                          max_metadata_bytes=PUBLIC_MAX_ZARR_METADATA_BYTES, max_matrix_bytes=PUBLIC_MAX_MATRIX_BYTES)
    expression_header = _read_json(root / "inputs/counts.zarr/expression/.zarray", PUBLIC_MAX_ZARR_HEADER_BYTES)
    validate_expression_source_dtype(expression_header.get("dtype"), bundle.measurement)
    from devharness_core.storage import ChunkedExpressionStore
    import numpy as np

    with (root / "inputs/metadata.csv").open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if (not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames))
                or len(reader.fieldnames) > PUBLIC_MAX_METADATA_COLUMNS
                or any(not field or len(field) > PUBLIC_MAX_METADATA_HEADER_CHARS for field in reader.fieldnames)):
            raise ValueError("metadata headers must be unique and respect shared public bounds")
        rows = []
        for row in reader:
            if (len(rows) >= PUBLIC_MAX_METADATA_ROWS or None in row
                    or any(value is None or len(value) > PUBLIC_MAX_METADATA_FIELD_CHARS for value in row.values())):
                raise ValueError("metadata records exceed the shared public row/field bounds")
            rows.append(row)
    if len(rows) != bundle.num_observations or any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("metadata records are malformed or have wrong observation count")
    _public_value(rows, recipe)
    hierarchy = catalog.template(recipe.template_id).unit_hierarchy
    if bundle.population_unit_label != hierarchy.population_unit_label:
        raise ValueError("population unit label differs from the declared biological hierarchy")
    nesting = {"library_to_specimen": {}, "specimen_to_donor": {}, "specimen_to_condition": {}}
    with ChunkedExpressionStore(root / "inputs/counts.zarr") as store:
        if recipe.parameters.cells_per_specimen and tuple(task["context"].get("feature_universe", ())) != tuple(store.gene_names):
            raise ValueError("public feature universe differs from stored expression gene labels")
        if tuple(row.get("sample_id") for row in rows) != tuple(store.sample_names):
            raise ValueError("metadata sample identifiers do not match expression columns")
        if hierarchy.cell_label and any(row.get(hierarchy.cell_label) != row["sample_id"] for row in rows):
            raise ValueError("cell/sample aliases changed during transport")
        for row in rows:
            for name in (hierarchy.population_unit_label, hierarchy.specimen_label, hierarchy.library_label):
                if name not in row or (not row[name] and not (name == hierarchy.population_unit_label and bundle.unit_identity_status == "intentionally_unknown")):
                    raise ValueError("metadata biological hierarchy is incomplete")
            if row.get("condition") not in recipe.parameters.condition_labels:
                raise ValueError("metadata condition differs from the declared public design")
            mappings = (("library_to_specimen", row[hierarchy.library_label], row[hierarchy.specimen_label]),
                        ("specimen_to_donor", row[hierarchy.specimen_label], row[hierarchy.population_unit_label]),
                        ("specimen_to_condition", row[hierarchy.specimen_label], row["condition"]))
            for kind, key, value in mappings:
                if key in nesting[kind] and nesting[kind][key] != value:
                    raise ValueError("biological unit nesting is inconsistent across observations")
                nesting[kind][key] = value
        chunk_rows = max(1, min(64, PUBLIC_MAX_CHUNK_BYTES // (bundle.num_observations * 8)))
        for _, chunk in store.iter_gene_chunks(chunk_rows):
            if not np.all(np.isfinite(chunk)) or (bundle.measurement != "continuous" and np.any(chunk < 0)):
                raise ValueError("stored observations violate the finite measurement contract")
            if bundle.measurement == "continuous" and chunk.dtype.kind != "f":
                raise ValueError("continuous observations were converted to integer storage")
    return bundle
