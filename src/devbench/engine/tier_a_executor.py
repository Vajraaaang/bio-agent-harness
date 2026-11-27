"""Local registered Tier A computation with verified input and output custody."""

import csv
import hashlib
import io
import json
import os
import platform
import re
import shutil
import tempfile
import time
from collections import Counter
from contextlib import contextmanager, suppress
from importlib.machinery import PathFinder
from importlib.metadata import version
from importlib.util import find_spec
from pathlib import Path

from devbench.core._files import read_bounded_file
from devbench.core.contrast_design import (
    TIER_A_BULK_OPERATION,
    TIER_A_BULK_OUTPUT,
    TIER_A_CONTINUOUS_REPRESENTATIONS,
    TIER_A_CONTROLS_REPRESENTATION,
    TIER_A_COUNT_REPRESENTATIONS,
    TIER_A_LENGTHS_REPRESENTATION,
    TIER_A_MOUNTED_CONTINUOUS_REPRESENTATIONS,
    TIER_A_MOUNTED_COUNT_REPRESENTATIONS,
    TIER_A_MOUNTED_REPRESENTATIONS,
    PipelineSpec,
)
from devbench.core.joint_bh import JOINT_BH_INPUT, JOINT_BH_OUTPUT_PREFIX
from devbench.core.procedure_profile import ADMITTED_RESEARCH
from devbench.core.protected_partitions import PartitionVault
from devbench.core.publication_families import FamilySpec
from devbench.engine.partition_authority import embargoed
from devbench.engine.provenance import canonical_json
from devbench.platform.adaptive_workflow_state import (
    AdaptiveStep,
    ArtifactVersion,
    StepReceipt,
)
from devbench.study.biology_answers import EffectsPayload
from devharness_core.watchdog import run_sandboxed, worker_limits
from devharness_server.dispatcher import ToolDispatcher, capture_tool_events

from .method_executor import _path
from .mounted_matrix_vault import MountedZarrVault
from .scientific_authority import _lookup
from .tier_a_pathways import (
    NB_MARKER_ORA,
    register_confirmed_nb_marker_ora,
    register_pathway_step,
    require_confirmed_marker_fit,
)
from .tier_a_scrna import (
    _open_retained_single_cell_output,
    collect_single_cell_outputs,
    execute_mounted_scrna,
    is_single_cell_operation,
    is_single_cell_output,
    load_single_cell_spec,
    open_single_cell_input,
    open_single_cell_output,
    register_single_cell_step,
    stage_single_cell_request,
    validate_single_cell_step,
)
from .workflow_operations import OperationBlocked, OperationConflict, _digest, _json

_OPERATION = TIER_A_BULK_OPERATION
_REQUEST_FORMAT = "devbench.tier-a-io/1"
_RESULT_FORMAT = "devbench.tier-a-result/1"
_RESEARCH = ADMITTED_RESEARCH
_PARQUET_LIMIT = 16_000_000
_RECORD_LIMIT = 32 * 1024
_RECEIPT_UNVERIFIED = "adaptive_tier_a_receipt_unverified"
_CUSTODY_UNVERIFIED = "adaptive_tier_a_custody_unverified"
_CUSTODY_FORMAT = "devbench.tier-a-worker-custody/1"
TIER_A_INTERRUPTED = "tier_a_coordinator_interrupted"
_CLEANUP_UNVERIFIED = "adaptive_tier_a_interrupted_cleanup_unverified"

# Files reached by the registered CSV bulk operation and its custody and
# validation path. A new helper on this path must be added to this inventory.
_SOURCE_MODULES = (
    "devbench.adaptive.models",
    "devbench.analysis.mounted_design_evidence",
    "devbench.analysis.episode_evidence_queries",
    "devbench.core._files",
    "devbench.core._hashing",
    "devbench.core.bulk_fit_evidence",
    "devbench.core.contrast_design",
    "devbench.core.count_profiles",
    "devbench.core.design",
    "devbench.core.estimators",
    "devbench.core.family_fitting",
    "devbench.core.batched_nb_fit",
    "devbench.core.input_files",
    "devbench.core.joint_bh",
    "devbench.core.observed_data",
    "devbench.core.population_contract",
    "devbench.core.population_data",
    "devbench.core.procedure_profile",
    "devbench.core.protected_partitions",
    "devbench.core.publication_families",
    "devbench.core.rna_pathway_analysis",
    "devbench.core.scrna_donor_trajectory",
    "devbench.core.scrna_marker_inference",
    "devbench.core.trajectory_evidence",
    "devbench.engine.method_executor",
    "devbench.engine.nb_marker_confirmation",
    "devbench.engine.joint_bh_parquet",
    "devbench.engine.mounted_kernel_oci",
    "devbench.engine.mounted_matrix_vault",
    "devbench.engine.mounted_result_validation",
    "devbench.engine.partition_authority",
    "devbench.engine.provenance",
    "devbench.engine.runtime_contracts",
    "devbench.engine.scientific_authority",
    __name__,
    "devbench.engine.tier_a_pathways",
    "devbench.engine.tier_a_scrna",
    "devbench.engine.workflow_operations",
    "devbench.platform.adaptive_workflow",
    "devbench.platform.adaptive_workflow_state",
    "devbench.platform.thesis_aims",
    "devbench.platform.thesis_joint_bh",
    "devbench.platform.thesis_joint_bh_release",
    "devbench.platform.thesis_workflow",
    "devbench.study.biology",
    "devbench.study.biology_answers",
    "devbench.study.biology_input_limits",
    "devbench.study.biology_tool_io",
    "devbench.study.biology_tools_bulk",
    "devbench.study.contracts",
    "devharness_controlled.native_runtime",
    "devharness_server.tools_rna_pathways",
    "devharness_core.provenance",
    "devharness_core.storage",
    "devharness_core.units",
    "devharness_core.watchdog",
    "devharness_server._runtime_scope",
    "devharness_server.dispatcher",
    "devharness_server.tools_bulk",
    "devharness_server.tools_stat",
    "rna_kernels.canonical",
    "rna_kernels.bulk_solver_policy",
    "rna_kernels.bulk_pydeseq2_wald",
    "rna_kernels.mounted_bulk",
    "rna_kernels.mounted_count",
    "rna_kernels.mounted_model",
    "rna_kernels.mounted_rank",
    "rna_kernels.nb_irls",
    "rna_kernels.paired_dispersion",
    "rna_kernels.rank_sensitivity",
    "rna_kernels.scrna_nb_clustering",
    "rna_kernels.scrna_nb_countsplit",
    "rna_kernels.scrna_nb_marker_fit",
    "rna_kernels.scrna_nb_pseudobulk",
    "rna_kernels.scrna_upstream_contract",
    "rna_kernels.normalization",
    "rna_kernels.streaming_rle",
    "rna_kernels.units",
)

# Bind installed versions. These are runtime pins, not wheel attestations.
_RUNTIME_PACKAGES = (
    "anndata",
    "formulaic",
    "h5py",
    "jsonschema",
    "numcodecs",
    "numpy",
    "pandas",
    "patsy",
    "psutil",
    "pyarrow",
    "pydantic",
    "pydantic-core",
    "pydeseq2",
    "scikit-learn",
    "scipy",
    "statsmodels",
    "zarr",
)


class _TierAEmbargo(PermissionError):
    """Access denied by current ledger custody."""


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _source_digest(module_name):
    module = find_spec(module_name)
    if module is not None and module.name != module_name:
        # Compatibility modules can replace their sys.modules entry with the
        # implementation. Bind the shim bytes as well as that implementation.
        parent = find_spec(module_name.rpartition(".")[0])
        if parent is None or parent.submodule_search_locations is None:
            raise ValueError(f"Tier A source package is unavailable for {module_name}")
        module = PathFinder.find_spec(module_name, parent.submodule_search_locations)
    if module is None or module.origin is None:
        raise ValueError(f"Tier A source is unavailable for {module_name}")
    return _sha(read_bounded_file(Path(module.origin).resolve(), 2_000_000))


def _runtime_identity(backend_id, mounted_image_id=None, allow_python_wald=False):
    sources = {name: _source_digest(name) for name in _SOURCE_MODULES}
    code = _sha(canonical_json(sources))
    environment = _sha(
        canonical_json(
            {
                "backend_id": backend_id,
                "mounted_image_id": mounted_image_id,
                "allow_python_wald": allow_python_wald,
                "code_sha256": code,
                "python": platform.python_version(),
                "platform": [platform.system(), platform.machine()],
                "packages": {name: version(name) for name in _RUNTIME_PACKAGES},
            }
        )
    )
    return code, environment


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_record(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent) as handle:
        handle.write(canonical_json(value))
        handle.flush()
        os.fchmod(handle.fileno(), 0o400)
        os.fsync(handle.fileno())
        # Linking publishes complete bytes and refuses to replace a prior record.
        os.link(handle.name, path)
    _sync_directory(path.parent)


def _read_record(path, maximum):
    raw = read_bounded_file(path, maximum)
    value = json.loads(raw)
    if type(value) is not dict or canonical_json(value) != raw:
        raise ValueError("Tier A receipt bytes are not a canonical object")
    return value


def _worker_record(token, phase, **evidence):
    return {"format": _CUSTODY_FORMAT, "token": token, "phase": phase, **evidence}


def _clear_stage(directory):
    with suppress(FileNotFoundError):
        shutil.rmtree(_path(directory / ".work"))


def _source_suffix(source, port):
    """Recognize the legacy operation's only format on the verified stream."""
    text = io.TextIOWrapper(source, encoding="utf-8", newline="")

    def has_columns(delimiter):
        text.seek(0)
        fields = next(csv.reader(text, delimiter=delimiter, strict=True), [])
        if len(fields) < 2:
            return False
        if port == "expression":
            return fields[0] in {"gene_id", "gene", "gene_name", ""}
        return "sample_id" in fields

    try:
        is_csv = has_columns(",")
        try:
            is_tsv = has_columns("\t")
        except csv.Error:
            is_tsv = False
    except (UnicodeError, csv.Error) as error:
        raise ValueError(f"Tier A requires uncompressed CSV for {port}") from error
    finally:
        text.detach().seek(0)
    if not is_csv or is_tsv:
        raise ValueError(f"Tier A requires uncompressed CSV for {port}")
    return ".csv"


def _execute_registered_bulk(stage, request, identity):
    if _runtime_identity(TierAExecutor.backend_id, *identity[2:]) != identity[:2]:
        raise PermissionError("Tier A code or runtime identity changed")
    dispatcher = ToolDispatcher(allowed_root=stage)
    with capture_tool_events() as events:
        try:
            result = json.loads(dispatcher.execute(_OPERATION, request))
            tool = {"tool": _OPERATION, "result_sha256": _sha(canonical_json(result))}
        except (ArithmeticError, ValueError) as error:
            result = None
            tool = {"tool": _OPERATION, "error_type": type(error).__name__}
    tool["event_sha256"] = _sha(canonical_json(events[-1]))
    return result, tool


def _execute_mounted_bulk(stage, request, identity):
    from rna_kernels.mounted_bulk import MAX_SUMMARY_BYTES, run_mounted_bulk

    if _runtime_identity(TierAExecutor.backend_id, *identity[2:]) != identity[:2]:
        raise PermissionError("Tier A code or runtime identity changed")
    request_sha = request["request_sha256"]
    if identity[2] is not None:
        from .mounted_kernel_oci import run_mounted_container

        transport = run_mounted_container(
            stage,
            request_sha=request_sha,
            image_id=identity[2],
            attempt_id=request["attempt_id"],
            timeout=request["timeout"],
            memory_mb=request["memory_mb"],
        )
        tool = {"tool": "mounted_bulk", "request_sha256": request_sha, "oci": transport}
        if transport["error_type"] is not None:
            return None, tool
        try:
            summary = _read_record(stage / "outputs/summary.json", MAX_SUMMARY_BYTES)
        except (OSError, ValueError) as error:
            return None, {**tool, "error_type": type(error).__name__}
        return summary, {**tool, "result_sha256": _sha(canonical_json(summary))}
    try:
        summary = run_mounted_bulk(
            stage / "inputs",
            stage / "outputs",
            request_sha,
            runtime="python_only",
        )
    except (ArithmeticError, ValueError, OSError) as error:
        return None, {
            "tool": "mounted_bulk",
            "error_type": type(error).__name__,
            "request_sha256": request_sha,
            "reason": str(error)[:500],
        }
    return summary, {
        "tool": "mounted_bulk",
        "request_sha256": request_sha,
        "result_sha256": _sha(canonical_json(summary)),
    }


def _result_table(spec, result, request_sha):
    import pyarrow as pa
    import pyarrow.parquet as pq

    targets = result["target_results"]
    if len(targets) != 1 or targets[0]["target_id"] != "mean_contrast":
        raise ValueError("Registered tool did not retain the exact mean contrast")
    target = targets[0]
    if target["estimate_status"] != "estimated":
        codes = target.get("limitation_codes", [])
        return None, (codes[0] if codes else "contrast_unavailable")
    payload = EffectsPayload.model_validate_json(canonical_json(target["payload"]))
    if (
        payload.biological_unit != spec.biological_unit
        or payload.analysis_layer != spec.measurement
        or payload.computed_contrast is None
        or payload.reference_condition != spec.contrast.denominator
        or payload.computed_contrast.comparison_condition != spec.contrast.numerator
        or payload.condition_number_validated is not True
        or payload.design_rank != payload.design_column_count
    ):
        raise ValueError(
            "Registered result differs from the declared scope or valid design"
        )
    by_gene = {row.gene_id: row for row in payload.gene_statistics}
    if len(by_gene) != len(payload.gene_statistics) or set(by_gene) != set(
        spec.family.feature_ids
    ):
        raise ValueError(
            "Registered result must retain the complete measured feature family"
        )
    rows = []
    for gene in spec.family.feature_ids:
        row = by_gene[gene]
        if row.p_value is not None and (
            row.q_value is None or row.q_value < row.p_value
        ):
            raise ValueError("Registered result violates the retained BH family")
        value = row.model_dump(mode="json")
        value["effect"] = row.values[0] if len(row.values) == 1 else None
        rows.append(value)
    metadata = {
        "format": _RESULT_FORMAT,
        "target_id": "mean_contrast",
        "family_id": spec.family.family_id,
        "row_count": len(rows),
        "tested_count": len(payload.multiplicity_scope),
        "status_counts": dict(Counter(row["estimate_status"] for row in rows)),
        "request_sha256": request_sha,
        "family_sha256": spec.family_sha256,
        "measurement": payload.analysis_layer,
        "biological_unit": payload.biological_unit,
        "claim_scope": _RESEARCH,
    }
    schema = pa.schema(
        [
            pa.field("gene_id", pa.string(), nullable=False),
            pa.field("hypothesis_id", pa.string(), nullable=False),
            pa.field("estimate_status", pa.string(), nullable=False),
            pa.field("coordinate_ids", pa.list_(pa.string()), nullable=False),
            pa.field("values", pa.list_(pa.float64()), nullable=False),
            pa.field(
                "intervals",
                pa.list_(
                    pa.struct(
                        [
                            ("lower", pa.float64()),
                            ("upper", pa.float64()),
                            ("confidence_level", pa.float64()),
                        ]
                    )
                ),
                nullable=False,
            ),
            pa.field("effect", pa.float64()),
            pa.field("p_value", pa.float64()),
            pa.field("q_value", pa.float64()),
            pa.field("missing_reason", pa.string()),
        ],
        metadata={b"devbench.result": canonical_json(metadata)},
    )
    sink = pa.BufferOutputStream()
    pq.write_table(
        pa.Table.from_pylist(rows, schema=schema), sink, compression="snappy"
    )
    buffer = sink.getvalue()
    if buffer.size > _PARQUET_LIMIT:
        raise ValueError("Tier A Parquet output exceeds its bounded local vault")
    return buffer.to_pybytes(), None


class TierAExecutor:
    """Exact registered operation backend for the host-side adaptive workflow."""

    backend_id = "local_registered_tier_a_1"
    worker_custody_format = _CUSTODY_FORMAT
    register_single_cell_step = register_single_cell_step
    register_pathway_step = register_pathway_step
    register_confirmed_nb_marker_ora = register_confirmed_nb_marker_ora

    def __init__(
        self,
        ledger,
        *,
        allow_data_execution=False,
        mounted_image_id=None,
        allow_python_wald=False,
        marker_review_verifier=None,
    ):
        from rna_kernels.mounted_bulk import MAX_MATRIX_BYTES, MAX_PARQUET_BYTES

        self.ledger = ledger
        if mounted_image_id is not None and not re.fullmatch(
            r"sha256:[a-f0-9]{64}", mounted_image_id
        ):
            raise ValueError("Mounted Tier A requires an exact local image ID")
        self.mounted_image_id = mounted_image_id
        self.allow_python_wald = allow_python_wald is True
        self.allow_data_execution = allow_data_execution is True
        if marker_review_verifier is not None and not callable(marker_review_verifier):
            raise TypeError("Tier A marker review requires a host verifier")
        self.marker_review_verifier = marker_review_verifier
        self.root = _path(ledger.path.parent / "adaptive-tier-a")
        self.runs = self.root / "runs"
        training = self.root / "training"
        parameters = self.root / "parameters"
        outputs = self.root / "outputs"
        if ledger.readonly and not all(
            path.is_dir() for path in (self.runs, training, parameters, outputs)
        ):
            raise PermissionError("Read-only Tier A access requires retained storage")
        self.training = PartitionVault(training, max_bytes=MAX_MATRIX_BYTES)
        self.mounted_zarr = MountedZarrVault(self.root / "mounted-zarr")
        self._parameters = PartitionVault(parameters)
        self._outputs = PartitionVault(outputs, max_bytes=MAX_PARQUET_BYTES)
        self.runs.mkdir(mode=0o700, exist_ok=True)
        if self.runs.stat().st_mode & 0o077:
            raise PermissionError("Tier A receipts require private storage")
        self.code_sha256, self.environment_sha256 = _runtime_identity(
            self.backend_id,
            self.mounted_image_id,
            self.allow_python_wald,
        )

    def _require_current_identity(self):
        retained = self.code_sha256, self.environment_sha256
        if (
            _runtime_identity(
                self.backend_id, self.mounted_image_id, self.allow_python_wald
            )
            != retained
        ):
            raise PermissionError("Tier A code or runtime identity changed")

    def register_pipeline(self, spec):
        """Store a checked typed declaration before a step references its hash."""
        self.ledger._writable()
        pipeline = PipelineSpec.model_validate(spec)
        if pipeline.format == "devbench.tier-a-bulk-pipeline/1":
            pipeline.tool_request()
        return self._parameters.put(canonical_json(pipeline.model_dump(mode="json")))

    def mounted_design_preflight(self, parameters_sha256):
        """Retain a structural check over the pinned declared metadata axis."""
        from devbench.analysis.mounted_design_evidence import mounted_design_evidence

        self._require_current_identity()
        self._require_unprotected((parameters_sha256,))
        with self._parameters.open(parameters_sha256) as stream:
            spec = PipelineSpec.model_validate_json(stream.read())
        if spec.mounted_model is None:
            raise ValueError(
                "Generalized preflight requires a mounted design specification"
            )
        self._require_unprotected((spec.metadata_sha256,))
        with self.training.open(spec.metadata_sha256) as stream:
            metadata = stream.read(5_000_001)
        if len(metadata) > 5_000_000:
            raise ValueError("Mounted design metadata exceeds its input bound")
        document = mounted_design_evidence(
            spec, metadata, parameters_sha256=parameters_sha256
        )
        digest = self._parameters.put(canonical_json(document))
        return {"sha256": digest, **document}

    def _require_unprotected(self, identities):
        if self.ledger._read(
            lambda db: any(embargoed(db, identity) for identity in identities)
        ):
            raise _TierAEmbargo(
                "Tier A cannot open protected input, family or plan bytes"
            )

    def _pipeline(self, envelope):
        plan_sha = envelope["step"]["parameters_sha256"]
        self._require_unprotected(
            (plan_sha, *(pin["sha256"] for pin in envelope["pins"]))
        )
        with self._parameters.open(plan_sha) as stream:
            spec = PipelineSpec.model_validate_json(stream.read())
        self._require_unprotected((spec.family_sha256,))
        with self.training.open(spec.family_sha256) as stream:
            if FamilySpec.model_validate_json(stream.read()) != spec.family:
                raise ValueError("Tier A family differs from its pinned manifest")
        return spec

    def validate(self, envelope):
        step = AdaptiveStep.model_validate(envelope["step"])
        if is_single_cell_operation(step.operation_id):
            spec, inputs = validate_single_cell_step(self, step, envelope)
            return step, spec, inputs
        review = envelope.get("design_review")
        if (
            envelope.get("mode") != "adaptive/1"
            or envelope.get("environment_sha256") != self.environment_sha256
            or step.environment_sha256 != self.environment_sha256
            or step.operation_id != _OPERATION
            or step.oci_method_version_id is not None
            or step.held_out
            or step.purpose != _RESEARCH
            or _RESEARCH not in step.profile.permitted_claims
            or "qualified_procedure_result" in step.profile.permitted_claims
            or not isinstance(review, dict)
            or not review.get("research_admission")
            or "manual_local_oci" in envelope
        ):
            raise PermissionError(
                "Tier A requires its admitted registered research contrast"
            )
        self._require_current_identity()
        capability = next(
            item for item in step.profile.operations if item.operation_id == _OPERATION
        )
        if (
            capability.effects
            or capability.max_memory_mb > 2048
            or capability.timeout_seconds > 300
        ):
            raise PermissionError(
                "The Tier A step exceeds the registered local resource bounds"
            )
        spec = self._pipeline(envelope)
        auxiliary = spec.mounted_model.artifact_digests() if spec.mounted_model else {}
        if [
            (port.name, port.artifact_kind, port.required) for port in step.outputs
        ] != [
            (TIER_A_BULK_OUTPUT, "table", True),
        ] or set(envelope["inputs"]) != {"expression", "metadata", *auxiliary}:
            raise ValueError(
                "Tier A contrast requires exact expression, metadata and result ports"
            )
        inputs = {
            port: ArtifactVersion.model_validate(row)
            for port, row in envelope["inputs"].items()
        }
        expression_kind = (
            "continuous_matrix" if spec.measurement == "continuous" else "counts"
        )
        representations = (
            (*TIER_A_COUNT_REPRESENTATIONS, *TIER_A_MOUNTED_COUNT_REPRESENTATIONS)
            if spec.measurement != "continuous"
            else (
                *TIER_A_CONTINUOUS_REPRESENTATIONS,
                *TIER_A_MOUNTED_CONTINUOUS_REPRESENTATIONS,
            )
        )
        if (
            step.regime != spec.design.regime
            or step.primary != spec.family.primary
            or not step.primary
            and not step.comparison
            or inputs["expression"].sha256 != spec.expression_sha256
            or inputs["expression"].kind != expression_kind
            or inputs["expression"].representation not in representations
            or inputs["metadata"].sha256 != spec.metadata_sha256
            or inputs["metadata"].kind != "table"
            or inputs["metadata"].representation != "metadata_csv"
            or any(
                value.biological_unit != spec.biological_unit
                for value in inputs.values()
            )
            or len({value.unit_membership_sha256 for value in inputs.values()}) != 1
        ):
            raise ValueError("Tier A invocation differs from the typed pipeline inputs")
        for role, representation in (
            ("lengths", TIER_A_LENGTHS_REPRESENTATION),
            ("controls", TIER_A_CONTROLS_REPRESENTATION),
        ):
            if role in auxiliary and (
                inputs[role].sha256,
                inputs[role].kind,
                inputs[role].representation,
            ) != (auxiliary[role], "table", representation):
                raise ValueError(
                    "Tier A invocation differs from the pinned auxiliary input"
                )
        if inputs["expression"].representation in TIER_A_MOUNTED_REPRESENTATIONS:
            from .mounted_kernel_oci import require_mounted_pipeline_runtime

            require_mounted_pipeline_runtime(
                spec,
                self.mounted_image_id,
                representation=inputs["expression"].representation,
                allow_python_wald=self.allow_python_wald,
            )
        elif spec.format == "devbench.tier-a-bulk-pipeline/2":
            raise ValueError(
                "Mounted pipeline parameters require a mounted expression artifact"
            )
        return step, spec, inputs

    def _attempt(self, envelope, *, job_id=None, execute=False, family_sha256=None):
        from devbench.platform.adaptive_workflow import AdaptiveWorkflow, request_digest

        flow = AdaptiveWorkflow(self.ledger, envelope["lineage_id"])
        request_sha = request_digest(envelope)

        def read(db):
            state = flow._state(db)
            matches = [
                flow.authority._checked_attempt(db, entry["token"])
                for attempt_id, entry in state.dispatches.items()
                if (job_id is None or attempt_id == job_id)
                and entry["token"]["backend_id"] == self.backend_id
                and entry["token"]["request_sha256"] == request_sha
                and entry["token"]["environment_sha256"]
                == envelope["environment_sha256"]
                and entry["envelope"] == envelope
            ]
            if execute:
                matches = [
                    attempt
                    for attempt in matches
                    if attempt["execution_state"] == "submission_uncertain"
                    and attempt["external_job_id"] is None
                ]
            if len(matches) != 1:
                raise PermissionError(
                    "Tier A requires an exact retained submission intent"
                )
            (attempt,) = matches
            if not attempt.get("adaptive"):
                raise PermissionError("Tier A requires an adaptive submission intent")
            if execute:
                self._require_current_submission(
                    flow, db, state, attempt, envelope, family_sha256
                )
            dispatch = state.dispatches[attempt["token"]["attempt_id"]]
            return {
                **attempt,
                "worker_custody_format": dispatch.get("worker_custody_format"),
            }

        return self.ledger._read(read)

    @staticmethod
    def _require_current_submission(flow, db, state, attempt, envelope, family_sha256):
        token = attempt["token"]
        execution = flow.authority._load_state(db, token["lineage_id"])
        flow.authority._active(db, token["lineage_id"], execution, token)
        if (
            token["binding_id"] != execution["binding_id"]
            or attempt["grant_id"] != execution["grant_id"]
        ):
            raise PermissionError("Tier A submission binding changed")
        step_id = envelope["step"]["step_id"]
        step = state.nodes.get(step_id)
        if (
            step is None
            or state.generations.get(step_id) != envelope["generation"]
            or step_id in state.holds
            or step.branch_id in state.retired
        ):
            raise PermissionError("Tier A submission no longer has a current step")
        for link in step.inputs:
            actual = flow._input(state, step, link)
            if (
                actual is None
                or actual.model_dump(mode="json") != envelope["inputs"][link.port.name]
                or embargoed(db, actual.sha256)
            ):
                raise PermissionError("Tier A submission input custody changed")
        if (family_sha256 is not None and embargoed(db, family_sha256)) or embargoed(
            db, step.parameters_sha256
        ):
            raise PermissionError("Tier A cannot execute a protected family or plan")

    def run(self, envelope):
        """Invoke one registered operation for a current scientific submission."""
        if not self.allow_data_execution:
            raise PermissionError("Tier A requires current data execution authority")
        self.ledger._writable()
        step, spec, inputs = self.validate(envelope)
        single_cell = is_single_cell_operation(step.operation_id)
        family_sha256 = None if single_cell else spec.family_sha256
        token = self._attempt(
            envelope,
            execute=True,
            family_sha256=family_sha256,
        )["token"]
        with self._attempt_lock(token):
            self._attempt(
                envelope,
                job_id=token["attempt_id"],
                execute=True,
                family_sha256=family_sha256,
            )
            prepared, launched, _ = self._read_custody(token)
            if prepared is None or launched is not None:
                raise OperationBlocked("adaptive_tier_a_submission_not_launchable")
            if single_cell:
                return self._run_owned_single_cell(token, envelope, step, spec, inputs)
            return self._run_owned(token, envelope, step, spec, inputs)

    def _attempt_lock(self, token):
        return self.ledger._operation_lock(
            (
                token["lineage_id"],
                "tier-a-attempt",
                token["attempt_id"],
            )
        )

    @staticmethod
    def _custody_event(db, token, phase):
        record_id = f"tier-a-{phase}-{token['attempt_id']}"
        row = _lookup(db, token["lineage_id"], record_id)
        if row is None:
            return None
        record = row["record"]
        state = record.get("state")
        expected = _worker_record(token, phase)
        outbox = db.execute(
            "SELECT sha256 FROM scientific_outbox WHERE lineage_id=? AND revision=?",
            (token["lineage_id"], row["revision"]),
        ).fetchone()
        if (
            record["kind"] != "execution_event"
            or record["event"] != "tier_a_custody"
            or outbox is None
            or outbox[0] != row["sha256"]
            or not isinstance(state, dict)
            or any(state.get(key) != value for key, value in expected.items())
        ):
            raise OperationBlocked(_CUSTODY_UNVERIFIED)
        return row["revision"], state

    def _record_custody(self, db, token, phase, **evidence):
        authority = self.ledger.scientific
        attempt = authority._checked_attempt(db, token)
        required_state = "reserved" if phase == "prepared" else "submission_uncertain"
        if (
            attempt["execution_state"] != required_state
            or attempt["external_job_id"] is not None
            or token["backend_id"] != self.backend_id
            or self._custody_event(db, token, phase) is not None
        ):
            raise OperationBlocked("adaptive_tier_a_submission_not_launchable")
        state = _worker_record(token, phase, **evidence)
        authority._append_execution_record(
            db,
            token["lineage_id"],
            {
                "kind": "execution_event",
                "event": "tier_a_custody",
                "record_id": f"tier-a-{phase}-{token['attempt_id']}",
                "state": state,
                "state_sha256": _digest(_json(state)),
            },
        )
        return state

    def _read_custody(self, token):
        phases = ("prepared", "launch_authorized", "worker_stopped")

        def read(db):
            events = tuple(self._custody_event(db, token, phase) for phase in phases)
            revisions = [event[0] for event in events if event is not None]
            if revisions != sorted(set(revisions)):
                raise OperationBlocked(_CUSTODY_UNVERIFIED)
            return tuple(event[1] if event is not None else None for event in events)

        return self.ledger._read(read)

    def _stage_inputs(self, inputs, stage):
        paths = {}
        for name, item in inputs.items():
            with self.training.open(item.sha256) as source:
                suffix = _source_suffix(source, name)
                path = stage / f"{name}{suffix}"
                with path.open("xb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
            path.chmod(0o400)
            paths[name] = str(path)
        return paths

    def _stage_mounted_inputs(self, inputs, stage, spec):
        from rna_kernels.mounted_bulk import REQUEST_FORMAT

        mount = stage / "inputs"
        mount.mkdir(mode=0o700)
        (stage / "outputs").mkdir(mode=0o700)
        suffix = TIER_A_MOUNTED_REPRESENTATIONS[inputs["expression"].representation]
        expression = "expression" + suffix
        staged = [
            (expression, spec.expression_sha256),
            ("metadata.csv", spec.metadata_sha256),
            ("family.json", spec.family_sha256),
        ]
        model = spec.mounted_model
        if model is not None and model.lengths_sha256:
            staged.append(("lengths.h5", model.lengths_sha256))
        for name, digest in staged:
            path = mount / name
            if path.suffix == ".zarr":
                self.mounted_zarr.stage(digest, path)
                continue
            with self.training.open(digest) as source, path.open("xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            path.chmod(0o400)
        document = {
            "format": REQUEST_FORMAT,
            "expression": expression,
            "layer": spec.expression_layer,
            "expression_sha256": spec.expression_sha256,
            "family_sha256": spec.family_sha256,
            "metadata_sha256": spec.metadata_sha256,
            "measurement": spec.measurement,
            "biological_unit": spec.biological_unit,
            "unit_column": spec.population_unit_column,
            "condition_column": spec.condition_column,
            "technical_factors": list(spec.design.technical_factors),
            "continuous_covariates": list(spec.continuous_covariates),
            "numerator": spec.contrast.numerator,
            "denominator": spec.contrast.denominator,
        }
        if model is not None:
            controls = None
            if model.controls_sha256:
                with self.training.open(model.controls_sha256) as stream:
                    raw = stream.read(256 * 1024 + 1)
                if len(raw) > 256 * 1024:
                    raise ValueError(
                        "The independent control manifest exceeds its request bound"
                    )
                controls = json.loads(raw)
                if not isinstance(controls, dict) or canonical_json(controls) != raw:
                    raise ValueError(
                        "Independent controls require a canonical JSON object"
                    )
            document["model"] = model.request(controls)
        _write_record(mount / "request.json", document)
        return {"request_sha256": _sha(canonical_json(document))}

    def _supervised_bulk(
        self,
        token,
        stage,
        request,
        capability,
        started,
        *,
        envelope,
        family_sha256,
        worker=_execute_registered_bulk,
    ):
        from devbench.platform.adaptive_workflow import AdaptiveWorkflow

        launch = None
        flow = AdaptiveWorkflow(self.ledger, token["lineage_id"])
        authority = flow.authority
        identity = (
            self.code_sha256,
            self.environment_sha256,
            self.mounted_image_id,
            self.allow_python_wald,
        )
        uses_oci = (
            worker in (_execute_mounted_bulk, execute_mounted_scrna)
            and self.mounted_image_id is not None
        )
        diagnostics = {}
        result = None

        def retain_launch():
            nonlocal launch
            self._require_current_identity()
            with authority._execution_tx(token["lineage_id"]) as db:
                state = flow._state(db)
                retained = state.dispatches.get(token["attempt_id"])
                if (
                    retained is None
                    or retained["token"] != token
                    or retained["envelope"] != envelope
                ):
                    raise OperationBlocked("adaptive_tier_a_submission_not_launchable")
                attempt = authority._checked_attempt(db, token)
                self._require_current_submission(
                    flow,
                    db,
                    state,
                    attempt,
                    envelope,
                    family_sha256,
                )
                transport = {}
                if uses_oci:
                    transport["mounted_oci"] = {
                        "request_sha256": request["request_sha256"],
                        "image_id": self.mounted_image_id,
                        "stage_identity": self._mounted_stage_identity(stage),
                    }
                launch = self._record_custody(
                    db, token, "launch_authorized", **transport
                )

        try:
            with worker_limits(
                deadline=time.monotonic() + capability.timeout_seconds,
                max_mem_mb=capability.max_memory_mb,
                scratch_dir=stage,
            ):
                result = run_sandboxed(
                    worker,
                    (stage, request, identity),
                    diagnostics=diagnostics,
                    _before_start=retain_launch,
                )
        finally:
            cleanup = diagnostics.get("cleanup", {})
            stopped = launch is not None and cleanup.get("verified_stopped") is True
            if uses_oci:
                stopped = (
                    stopped
                    and result is not None
                    and (result[1].get("oci", {}).get("cleanup_confirmed") is True)
                )
            if stopped:
                with authority._execution_tx(token["lineage_id"]) as db:
                    self._record_custody(
                        db,
                        token,
                        "worker_stopped",
                        launch_sha256=_sha(canonical_json(launch)),
                        watchdog_cleanup_sha256=_sha(canonical_json(cleanup)),
                        wall_millis=(time.monotonic_ns() - started) // 1_000_000,
                    )
        if not stopped:
            raise OperationBlocked("adaptive_tier_a_worker_cleanup_unverified")
        return result

    def _run_owned(self, token, envelope, step, spec, inputs):
        directory = self._directory(token["attempt_id"])
        directory.mkdir(mode=0o700)
        _sync_directory(directory.parent)
        started = time.monotonic_ns()
        capability = next(
            operation
            for operation in step.profile.operations
            if operation.operation_id == _OPERATION
        )
        stage = _path(directory / ".work")
        stage.mkdir(mode=0o700)
        try:
            mounted = (
                inputs["expression"].representation in TIER_A_MOUNTED_REPRESENTATIONS
            )
            if mounted:
                request = self._stage_mounted_inputs(inputs, stage, spec)
                request.update(
                    attempt_id=token["attempt_id"],
                    timeout=max(1, capability.timeout_seconds - 30),
                    memory_mb=capability.max_memory_mb,
                )
            else:
                paths = self._stage_inputs(inputs, stage)
                task, options = spec.tool_request()
                request = {
                    "counts_path": paths["expression"],
                    "metadata_path": paths["metadata"],
                    "public_task": task,
                    "options": options,
                }
            result, tool_record = self._supervised_bulk(
                token,
                stage,
                request,
                capability,
                started,
                envelope=envelope,
                family_sha256=spec.family_sha256,
                worker=_execute_mounted_bulk if mounted else _execute_registered_bulk,
            )
            if mounted:
                outputs, failure, evidence = self._mounted_artifacts(
                    token,
                    spec,
                    inputs,
                    stage / "outputs",
                    result,
                    request["request_sha256"],
                )
                tool_record.update(evidence)
            else:
                outputs, failure = self._artifacts(token, spec, inputs, result)
        finally:
            # An orphan may still be using the staged files after a failed launch.
            _, launch, cleanup = self._read_custody(token)
            if launch is None or cleanup is not None:
                _clear_stage(directory)
        return self._finish_receipt(
            token,
            envelope,
            directory,
            spec.family_sha256,
            outputs,
            failure,
            tool_record,
            started,
        )

    def _run_owned_single_cell(self, token, envelope, step, spec, inputs):
        directory = self._directory(token["attempt_id"])
        directory.mkdir(mode=0o700)
        _sync_directory(directory.parent)
        started = time.monotonic_ns()
        capability = step.profile.operations[0]
        stage = _path(directory / ".work")
        stage.mkdir(mode=0o700)
        try:
            request = {
                "request_sha256": stage_single_cell_request(
                    self, stage, spec, inputs, envelope=envelope
                ),
                "operation": spec.operation,
                "attempt_id": token["attempt_id"],
                "timeout": max(1, capability.timeout_seconds - 30),
                "memory_mb": capability.max_memory_mb,
            }
            result, tool = self._supervised_bulk(
                token,
                stage,
                request,
                capability,
                started,
                envelope=envelope,
                family_sha256=None,
                worker=execute_mounted_scrna,
            )
            outputs, failure, evidence = collect_single_cell_outputs(
                self,
                spec,
                stage / "outputs",
                result,
                request_sha=request["request_sha256"],
                attempt=token["attempt_id"],
                membership=next(iter(inputs.values())),
            )
            tool.update(evidence)
        finally:
            _, launch, cleanup = self._read_custody(token)
            if launch is None or cleanup is not None:
                _clear_stage(directory)
        return self._finish_receipt(
            token,
            envelope,
            directory,
            None,
            outputs,
            failure,
            tool,
            started,
        )

    @staticmethod
    def _finish_receipt(
        token,
        envelope,
        directory,
        family_sha256,
        outputs,
        failure,
        tool_record,
        started,
    ):
        if "joint_bh" in envelope.get("thesis_scope", {}):
            outputs = {
                name: artifact.model_copy(
                    update={"representation": JOINT_BH_INPUT, "permitted_uses": ()}
                )
                for name, artifact in outputs.items()
            }
        status = "succeeded" if failure is None else "failed"
        manifest = {
            "format": _REQUEST_FORMAT,
            "token": token,
            "parameters_sha256": envelope["step"]["parameters_sha256"],
            "family_sha256": family_sha256,
            "inputs": envelope["pins"],
            "status": status,
            "reason": failure,
            "outputs": {
                name: value.model_dump(mode="json") for name, value in outputs.items()
            },
            "tool": tool_record,
        }
        receipt = StepReceipt(
            job_id=token["attempt_id"],
            request_sha256=token["request_sha256"],
            consumed=envelope["pins"],
            status=status,
            reason=failure,
            outputs=outputs,
            wall_millis=(time.monotonic_ns() - started) // 1_000_000,
            cleanup_state="confirmed",
            io_sha256=_sha(canonical_json(manifest)),
        )
        _write_record(directory / "io.json", manifest)
        _write_record(directory / "receipt.json", receipt.model_dump(mode="json"))
        return receipt

    def _artifacts(self, token, spec, inputs, result):
        if result is None:
            return {}, "registered_contrast_failed"
        artifact_bytes, failure = _result_table(spec, result, token["request_sha256"])
        if artifact_bytes is None:
            return {}, failure
        return self._output(
            token, spec, inputs, self._outputs.put(artifact_bytes)
        ), failure

    def _mounted_artifacts(self, token, spec, inputs, directory, result, request_sha):
        from rna_kernels.mounted_bulk import MAX_SUMMARY_BYTES, SUMMARY_FORMAT

        from .mounted_result_validation import (
            mounted_method_evidence,
            verify_mounted_primary,
            verify_mounted_solver,
        )

        if result is None:
            return {}, "registered_mounted_contrast_failed", {}
        if (
            _read_record(directory / "summary.json", MAX_SUMMARY_BYTES) != result
            or result["format"] != SUMMARY_FORMAT
            or result["family_id"] != spec.family.family_id
            or result["row_count"] != len(spec.family.feature_ids)
            or result["request_sha256"] != request_sha
            or result["family_sha256"] != spec.family_sha256
            or result["measurement"] != spec.measurement
            or result["biological_unit"] != spec.biological_unit
        ):
            raise ValueError(
                "Mounted Tier A result differs from the frozen specification"
            )
        verify_mounted_solver(
            result,
            spec,
            inputs=directory.parent / "inputs",
            runtime="bioconductor" if self.mounted_image_id else "python_only",
        )
        digest = self._outputs.put_file(directory / "genes.parquet")
        if digest != result["genes_sha256"]:
            raise ValueError("Mounted Tier A Parquet hash mismatch")
        artifacts = self._output(token, spec, inputs, digest)
        verify_mounted_primary(
            artifacts[TIER_A_BULK_OUTPUT],
            open_artifact=lambda artifact: self._outputs.open(artifact.sha256),
            summary=result,
            spec=spec,
        )
        evidence = self._mounted_rank_evidence(directory, result)
        latent = result.get("latent_diagnostics")
        if latent is not None:
            if (
                not spec.mounted_model
                or not spec.mounted_model.latent_method
                or not isinstance(latent, dict)
                or latent.get("diagnostic_only") is not True
                or latent.get("method") != spec.mounted_model.latent_method
                or len(canonical_json(latent)) > 8_192
            ):
                raise ValueError(
                    "Mounted latent diagnostic differs from the frozen diagnostic"
                )
            evidence["latent_diagnostics"] = latent
        elif spec.mounted_model and spec.mounted_model.latent_method:
            raise ValueError("Mounted kernel omitted the requested latent diagnostic")
        evidence["method"] = mounted_method_evidence(result)
        if evidence.get("rank_sensitivity", {}).get("hold") == "Q-DESIGN":
            return {}, "Q-DESIGN", {**evidence, "held_primary_artifact_sha256": digest}
        if not result["tested_count"]:
            return {}, "registered_mounted_contrast_unavailable", evidence
        return artifacts, None, evidence

    def _mounted_rank_evidence(self, directory, result):
        rank = result.get("rank_sensitivity")
        if rank is None:
            return {}
        if rank.get("status") == "completed":
            digest = self._outputs.put_file(directory / "sensitivity.parquet")
            if digest != rank.get("artifact_sha256"):
                raise ValueError("Mounted rank sensitivity Parquet hash mismatch")
        return {"rank_sensitivity": rank}

    @staticmethod
    def _output(token, spec, inputs, digest):
        return {
            TIER_A_BULK_OUTPUT: ArtifactVersion(
                version_id="tier-a-" + token["attempt_id"],
                sha256=digest,
                kind="table",
                representation="parquet",
                biological_unit=spec.biological_unit,
                unit_membership_sha256=inputs["expression"].unit_membership_sha256,
                permitted_uses=("checked_observation", _RESEARCH),
            )
        }

    def _directory(self, job_id):
        if re.fullmatch(r"[0-9a-f]{32}", job_id) is None:
            raise ValueError("Tier A receipts require an exact attempt ID")
        return _path(self.runs / job_id)

    def _missing_receipt_proof(self, token):
        prepared, launch, cleanup = self._read_custody(token)
        if prepared is not None and launch is None and cleanup is None:
            # Only the WAL protocol binding proves every launch would have a
            # WAL fence. A missing or replaced run directory proves nothing.
            return _worker_record(
                token,
                "worker_not_launched",
                prepared_sha256=_sha(canonical_json(prepared)),
            )
        if cleanup is not None:
            if prepared is None or launch is None:
                raise OperationBlocked(_CUSTODY_UNVERIFIED)
            return self._verified_cleanup(token, launch, cleanup)
        return _worker_record(
            token,
            "worker_cleanup_unverified",
            launch_sha256=_sha(canonical_json(launch)) if launch is not None else None,
        )

    @staticmethod
    def _verified_cleanup(token, launch, cleanup):
        elapsed = cleanup.get("wall_millis")
        if "mounted_oci_cleanup" in cleanup:
            proof = cleanup["mounted_oci_cleanup"]
            binding = launch.get("mounted_oci")
            if (
                type(elapsed) is not int
                or elapsed < 0
                or not isinstance(binding, dict)
                or not isinstance(proof, dict)
                or any(
                    proof.get(key) != binding.get(key)
                    for key in ("request_sha256", "image_id")
                )
                or proof.get("name") != "devbench-rna-tier-a-" + token["attempt_id"]
                or proof.get("cleanup_confirmed") is not True
                or cleanup
                != _worker_record(
                    token,
                    "worker_stopped",
                    launch_sha256=_sha(canonical_json(launch)),
                    mounted_oci_cleanup=proof,
                    wall_millis=elapsed,
                )
            ):
                raise OperationBlocked(_CUSTODY_UNVERIFIED)
            return cleanup
        watchdog = cleanup.get("watchdog_cleanup_sha256")
        if (
            type(elapsed) is not int
            or elapsed < 0
            or type(watchdog) is not str
            or not re.fullmatch(r"[a-f0-9]{64}", watchdog)
            or cleanup
            != _worker_record(
                token,
                "worker_stopped",
                launch_sha256=_sha(canonical_json(launch)),
                watchdog_cleanup_sha256=watchdog,
                wall_millis=elapsed,
            )
        ):
            raise OperationBlocked(_CUSTODY_UNVERIFIED)
        return cleanup

    def _recover_oci_cleanup(self, flow, token, directory, attempt):
        from .mounted_kernel_oci import NativeRuntimeError, recover_mounted_container

        prepared, launch, cleanup = self._read_custody(token)
        binding = launch.get("mounted_oci") if launch else None
        if (
            prepared is None
            or cleanup is not None
            or not isinstance(binding, dict)
            or set(binding) != {"request_sha256", "image_id", "stage_identity"}
            or attempt["execution_state"] != "submission_uncertain"
            or attempt["external_job_id"] is not None
        ):
            raise OperationBlocked(_CUSTODY_UNVERIFIED)
        try:
            stage = _path(directory / ".work")
            if self._mounted_stage_identity(stage) != binding["stage_identity"]:
                raise ValueError("The retained mounted OCI stage changed")
            proof = recover_mounted_container(
                stage,
                request_sha=binding["request_sha256"],
                image_id=binding["image_id"],
                attempt_id=token["attempt_id"],
            )
        except (NativeRuntimeError, OSError, ValueError) as error:
            # Keep the original attempt pending so a later reconciliation can
            # recover a delayed create reply or a temporarily unavailable daemon.
            raise OperationBlocked(_CUSTODY_UNVERIFIED) from error
        with flow.authority._execution_tx(token["lineage_id"]) as db:
            current = self._custody_event(db, token, "launch_authorized")
            if current is None or current[1] != launch:
                raise OperationBlocked(_CUSTODY_UNVERIFIED)
            return self._record_custody(
                db,
                token,
                "worker_stopped",
                launch_sha256=_sha(canonical_json(launch)),
                mounted_oci_cleanup=proof,
                wall_millis=attempt["usage"].get("wall_millis", 0),
            )

    @staticmethod
    def _mounted_stage_identity(stage):
        return [
            [details.st_dev, details.st_ino]
            for details in (
                stage.stat(follow_symlinks=False),
                stage.parent.stat(follow_symlinks=False),
            )
        ]

    def recover(self, flow, token):
        """Attach retained evidence or settle a provably interrupted local run."""
        if (
            flow.ledger.path != self.ledger.path
            or token.get("backend_id") != self.backend_id
        ):
            raise PermissionError(
                "Tier A recovery requires the bound workflow and backend",
            )
        envelope, _ = flow._load_dispatch(token)
        with self._attempt_lock(token):
            receipt = self.load_receipt(envelope, attempt_id=token["attempt_id"])
            if receipt is not None:
                return flow.complete(token, receipt, verify=self.verify)
            attempt = flow.authority.attempt(token)
            try:
                directory = self._directory(token["attempt_id"])
                proof = self._missing_receipt_proof(token)
                if proof["phase"] == "worker_cleanup_unverified":
                    _, launch, _ = self._read_custody(token)
                    if launch is not None and "mounted_oci" in launch:
                        proof = self._recover_oci_cleanup(
                            flow, token, directory, attempt
                        )
                if proof["phase"] == "worker_not_launched":
                    return self._confirm_not_launched(flow, attempt, directory, proof)
                if proof["phase"] == "worker_stopped":
                    _clear_stage(directory)
                return self._settle_interrupted(flow, envelope, attempt, proof)
            except (OSError, ValueError) as error:
                raise OperationBlocked(_CUSTODY_UNVERIFIED) from error

    @staticmethod
    def _confirm_not_launched(flow, attempt, directory, proof):
        if (
            attempt["execution_state"] != "submission_uncertain"
            or attempt["external_job_id"] is not None
            or (directory / "io.json").exists(follow_symlinks=False)
        ):
            raise OperationBlocked(_CUSTODY_UNVERIFIED)
        _clear_stage(directory)
        token = attempt["token"]
        return flow.authority.reconcile_attempt(
            token,
            job_id=None,
            execution_state="noncompletion_confirmed",
            cleanup_state="confirmed",
            usage=attempt["usage"],
            receipt_sha256=_sha(canonical_json(proof)),
            verify_observation=lambda _: flow.authority.attempt(token) == attempt,
        )

    @staticmethod
    def _settle_interrupted(flow, envelope, attempt, proof):
        token = attempt["token"]
        proof_sha = _sha(canonical_json(proof))
        execution_state = attempt["execution_state"]
        stopped = proof["phase"] == "worker_stopped"
        already_failed = (
            execution_state == "failed" and attempt["observation_sha256"] == proof_sha
        )
        if execution_state != "submission_uncertain" and not already_failed:
            raise OperationBlocked(_CUSTODY_UNVERIFIED)
        usage = dict(attempt["usage"])
        if stopped:
            usage["wall_millis"] = max(
                usage.get("wall_millis", 0),
                proof["wall_millis"],
            )
        job_id = token["attempt_id"]
        if execution_state == "submission_uncertain":
            flow.authority.reconcile_attempt(
                token,
                job_id=job_id,
                execution_state="failed",
                cleanup_state="confirmed" if stopped else "unknown",
                usage=usage,
                receipt_sha256=proof_sha,
                verify_observation=lambda _: flow.authority.attempt(token) == attempt,
            )
        outcome = {
            "attempt_id": job_id,
            "status": "failed",
            "reason": TIER_A_INTERRUPTED if stopped else _CLEANUP_UNVERIFIED,
            "receipt_sha256": proof_sha,
            "outputs": {},
            "staged_outputs": {},
            "consumed": envelope["pins"],
            "accepted_record_id": None,
        }
        flow._record_failed(token, outcome)
        return outcome

    @contextmanager
    def open_output(self, artifact):
        """Open a retained Tier A artifact through its verified digest."""
        if artifact.version_id.startswith(JOINT_BH_OUTPUT_PREFIX):
            from devbench.platform.thesis_joint_bh_release import open_pooled_output

            with open_pooled_output(self, artifact) as source:
                yield source
            return
        if is_single_cell_output(artifact):
            with open_single_cell_output(self, artifact) as source:
                yield source
            return
        if (
            artifact.kind != "table"
            or artifact.representation != "parquet"
            or _RESEARCH not in artifact.permitted_uses
        ):
            raise ValueError("Tier A output requires a typed Parquet artifact")
        with self._open_bulk_output(artifact) as source:
            yield source

    @contextmanager
    def _open_bulk_output(self, artifact):
        if not artifact.version_id.startswith("tier-a-"):
            raise PermissionError("Expected a retained Tier A artifact")
        directory = self._directory(artifact.version_id.removeprefix("tier-a-"))
        saved = _read_record(directory / "receipt.json", 32 * 1024)
        if saved["outputs"].get(TIER_A_BULK_OUTPUT) != artifact.model_dump(mode="json"):
            raise PermissionError("Expected a retained Tier A artifact")
        self._require_unprotected((artifact.sha256,))
        with self._outputs.open(artifact.sha256) as source:
            yield source

    def load_output(self, artifact):
        with self.open_output(artifact) as source:
            return source.read()

    def verify(self, envelope, receipt):
        """Verify the original receipt and artifact bytes against their dispatch."""
        try:
            receipt = StepReceipt.model_validate(receipt)
            attempt = self._attempt(envelope, job_id=receipt.job_id)
            return self._receipt_matches(envelope, receipt, attempt)
        except (KeyError, OSError, TypeError, ValueError, OperationConflict):
            return False

    def _receipt_custody_matches(self, attempt, receipt):
        token = attempt["token"]
        protocol = attempt["worker_custody_format"]
        try:
            phases = self._read_custody(token)
            # Historical dispatches did not declare a custody protocol.
            if protocol is None and phases == (None, None, None):
                return True
            prepared, launch, stopped = phases
            if (
                protocol not in {None, self.worker_custody_format}
                or prepared is None
                or launch is None
                or stopped is None
            ):
                return False
            self._verified_cleanup(token, launch, stopped)
        except (OperationBlocked, OperationConflict):
            return False
        return (
            receipt.cleanup_state == "confirmed"
            and receipt.wall_millis >= stopped["wall_millis"]
        )

    def _receipt_matches(self, envelope, receipt, attempt):
        token = attempt["token"]
        if receipt.job_id != token["attempt_id"]:
            return False
        if attempt["execution_state"] in {
            "reserved",
            "noncompletion_confirmed",
        } or attempt["external_job_id"] not in (None, receipt.job_id):
            return False
        if not self._receipt_custody_matches(attempt, receipt):
            return False
        directory = self._directory(receipt.job_id)
        saved = _read_record(directory / "receipt.json", _RECORD_LIMIT)
        manifest = _read_record(directory / "io.json", _RECORD_LIMIT)
        if (
            saved != receipt.model_dump(mode="json")
            or _sha(canonical_json(manifest)) != receipt.io_sha256
            or manifest["format"] != _REQUEST_FORMAT
            or manifest["token"] != token
            or receipt.request_sha256 != token["request_sha256"]
            or manifest["parameters_sha256"] != envelope["step"]["parameters_sha256"]
            or manifest["inputs"] != saved["consumed"]
            or saved["consumed"] != envelope["pins"]
            or any(
                manifest[key] != saved[key] for key in ("status", "reason", "outputs")
            )
        ):
            return False
        if is_single_cell_operation(envelope["step"]["operation_id"]):
            return self._check_retained_single_cell(envelope, receipt, manifest)
        return self._check_retained_pipeline(envelope, receipt, manifest)

    def _check_retained_pipeline(self, envelope, receipt, manifest):
        spec = self._pipeline(envelope)
        if manifest["family_sha256"] != spec.family_sha256:
            return False
        for pin in receipt.consumed:
            representation = envelope["inputs"][pin.port]["representation"]
            if TIER_A_MOUNTED_REPRESENTATIONS.get(representation) == ".zarr":
                self.mounted_zarr.verify(pin.sha256)
                continue
            with self.training.open(pin.sha256):
                pass
        for artifact in receipt.outputs.values():
            if "joint_bh" in envelope.get("thesis_scope", {}):
                if artifact.representation != JOINT_BH_INPUT or artifact.permitted_uses:
                    return False
                opener = self._open_bulk_output
            else:
                opener = self.open_output
            with opener(artifact):
                pass
        tool = manifest["tool"]
        sources = (
            tool.get("held_primary_artifact_sha256"),
            tool.get("rank_sensitivity", {}).get("artifact_sha256"),
        )
        for identity in filter(None, sources):
            self._require_unprotected((identity,))
            with self._outputs.open(identity):
                pass
        return True

    def _check_retained_single_cell(self, envelope, receipt, manifest):
        spec = load_single_cell_spec(self, envelope)
        if manifest["family_sha256"] is not None:
            return False
        if spec.operation == NB_MARKER_ORA:
            artifact = ArtifactVersion.model_validate(envelope["inputs"]["marker_fit"])
            require_confirmed_marker_fit(self, spec, {"marker_fit": artifact})
        for pin in receipt.consumed:
            artifact = ArtifactVersion.model_validate(envelope["inputs"][pin.port])
            with open_single_cell_input(
                self,
                pin.port,
                artifact,
                operation=spec.operation,
            ):
                pass
        for artifact in receipt.outputs.values():
            with _open_retained_single_cell_output(self, artifact):
                pass
        for digest in manifest["tool"].get("retained_evidence", {}).values():
            self._require_unprotected((digest,))
            with self._outputs.open(digest):
                pass
        return True

    def load_receipt(self, envelope, *, attempt_id=None):
        """Locate a verified previously produced receipt without running a worker."""
        attempt = self._attempt(envelope, job_id=attempt_id)
        try:
            retained = self._directory(attempt["token"]["attempt_id"]) / "receipt.json"
            receipt = StepReceipt.model_validate(_read_record(retained, 32 * 1024))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            raise OperationBlocked(_RECEIPT_UNVERIFIED) from error
        try:
            if not self._receipt_matches(envelope, receipt, attempt):
                raise OperationBlocked(_RECEIPT_UNVERIFIED)
        except _TierAEmbargo as error:
            raise OperationConflict(
                "Tier A retained receipt failed verification"
            ) from error
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise OperationBlocked(_RECEIPT_UNVERIFIED) from error
        return receipt
