"""Trusted local OCI adapter for adaptive exploration and protected evaluation."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import time
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from devbench.adaptive.models import FigureRecord
from devbench.engine.exploratory_images import exploratory_image
from devbench.engine.provenance import canonical_json, hash_artifact

from .tier2_artifacts import (
    LANE_TAGS,
    VAULT_BYTES,
    preflight_source,
    validate_ports,
)
from .tier2_input_custody import (
    artifact_digests,
    check_stage,
    guard_digests,
    stage_identity,
)

if TYPE_CHECKING:
    from devbench.platform.adaptive_workflow import (
        AdaptiveStep,
        AdaptiveWorkflow,
        StepReceipt,
    )


Document = dict[str, Any]

# Existing registered methods bind the exact bytes of this grouped evaluator.
GROUPED_EVALUATOR_SOURCE = """\
import json
import math
import statistics
from pathlib import Path

def read(port):
    return json.loads(Path('/inputs/' + port).read_bytes())

def require(condition, message):
    if not condition:
        raise ValueError(message)

data, model = read('held'), read('model')
centroids = model['centroids']
mean = model['mean']
features = data['features']
require(features and features == model['features'], 'Evaluation features must match the frozen model')
require(type(mean) in (int, float) and math.isfinite(mean), 'Frozen model mean must be finite')
require(type(centroids) is dict and len(centroids) >= 2, 'Frozen model requires at least two centroids')
require(all(type(v) in (int, float) and math.isfinite(v) for v in centroids.values()),
        'Frozen model centroids must be finite')
units = {}
for row in data['rows']:
    label = row['label']
    require(label in centroids, 'Evaluation label is outside the frozen model')
    recorded, scores = units.setdefault(row['unit'], (label, []))
    require(recorded == label, 'Evaluation labels disagree within a biological unit')
    counts = row['counts']
    require(len(counts) == len(features) and all(type(v) is int and v >= 0 for v in counts),
            'Evaluation counts must match the frozen features and be nonnegative integers')
    total = sum(counts)
    require(total > 0, 'Evaluation library size must be positive')
    scores.append(math.log1p(counts[0] / total * 10000) - mean)
require(units, 'Evaluation requires at least one biological unit')
correct = sum(min(centroids, key=lambda key: (abs(statistics.mean(scores) - centroids[key]), key))
              == label for label, scores in units.values())
Path('/outputs/out.json').write_text(json.dumps({'units':len(units), 'accuracy':correct/len(units)},
                                              allow_nan=False))
"""  # noqa: E501


class ExploratoryOCI:
    """Trusted I/O adapter for custom methods admitted by the adaptive ledger."""

    backend_id = "local_exploratory_oci_1"

    def __init__(
        self, methods, ledger, parameters: Iterable[Document], *, partition=None
    ) -> None:
        from devbench.core.protected_partitions import PartitionVault
        from devbench.engine.workflow_operations import LEDGER_NAME

        if ledger.path != methods.root / LEDGER_NAME:
            raise ValueError("Exploratory OCI requires the planner's operation ledger")
        self.methods, self.ledger, self.partition = methods, ledger, partition
        self.parameters = {
            hashlib.sha256(canonical_json(value)).hexdigest(): canonical_json(value)
            for value in parameters
        }
        self.root = methods.root / "adaptive-oci"
        self.root.mkdir(mode=0o700, exist_ok=True)
        from .mounted_matrix_vault import MountedZarrVault

        # Historical callers read small inline JSON via training; governed
        # tables and matrix ports stream from the same content-addressed objects.
        self.training = PartitionVault(self.root / "training")
        self.artifacts = PartitionVault(self.training.root, max_bytes=VAULT_BYTES)
        self.zarr_vault = MountedZarrVault(self.root / "training-zarr")
        self.private = False
        self._member_cache = {}
        self.plugin_plan = None
        self.session_id = None
        self.binary_training = PartitionVault(self.training.root, max_bytes=100_000_000)
        self.evaluation = PartitionVault(self.root / "evaluation")
        self.runs = self.root / "runs"
        self.runs.mkdir(mode=0o700, exist_ok=True)

    def approve_step(
        self,
        flow,
        step_id,
        *,
        actor_id,
        data_classification,
        surface="cli",
        auth_method="local_os_user",
    ):
        """Operator API for explicit, per-plan public or synthetic script approval."""
        from .tier2_approval import approve

        return approve(
            self,
            flow,
            step_id,
            actor_id=actor_id,
            data_classification=data_classification,
            surface=surface,
            auth_method=auth_method,
        )

    @staticmethod
    def _identity(value: Any) -> str:
        return hashlib.sha256(canonical_json(value)).hexdigest()

    def validate(self, envelope: Document) -> tuple[AdaptiveStep, Document, Document]:
        from devbench.engine.methods import exploratory_method
        from devbench.platform.adaptive_workflow import AdaptiveStep
        from devharness_core.methods import binary_oci_transport

        step = AdaptiveStep.model_validate(envelope["step"])
        method = self.methods.method(step.oci_method_version_id)
        contract = method["contract"]
        checked = exploratory_method(contract)
        if checked.version_id != step.oci_method_version_id:
            raise PermissionError("Exploratory method version changed")
        encoded = self.parameters.get(step.parameters_sha256)
        if encoded is None:
            raise ValueError("No exact frozen parameter document for this step")
        parameters = checked.validate_parameters(json.loads(encoded))
        capability = next(
            item
            for item in step.profile.operations
            if item.operation_id == step.operation_id
        )
        limits = contract["execution"]
        binary = binary_oci_transport(contract)
        self._validate_binary_ports(step, contract, envelope["inputs"], binary)
        if step.held_out and step.operation_id == "exploratory_grouped_benchmark":
            from .exploratory_benchmark import verify_benchmark_evaluator

            verify_benchmark_evaluator(self, envelope, contract, parameters)
        elif step.held_out:
            if (
                contract["implementation"]["source_sha256"]
                != hashlib.sha256(GROUPED_EVALUATOR_SOURCE.encode()).hexdigest()
                or {name: item["kind"] for name, item in envelope["inputs"].items()}
                != {"held": "counts", "model": "model"}
                or [
                    (port.name, port.artifact_kind, port.required)
                    for port in step.outputs
                ]
                != [("out", "scores", True)]
                or contract["outputs"]["out"]["path"] != "/outputs/out.json"
                or contract["outputs"]["out"]["artifact_type"] != "json"
            ):
                raise PermissionError(
                    "Protected evaluation requires the exact frozen grouped "
                    "scoring procedure"
                )
        if (
            capability.effects
            or step.primary
            or step.purpose not in {"exploratory", "protected_evaluation"}
            or "qualified_procedure_result" in step.profile.permitted_claims
            or step.held_out != (step.purpose == "protected_evaluation")
            or step.held_out
            and self.partition is None
            or limits["memory_mb"] > capability.max_memory_mb
            or limits["timeout_seconds"] > capability.timeout_seconds
            or envelope["environment_sha256"]
            != self._identity(
                {
                    **contract["environment"],
                    "image_id": exploratory_image(
                        contract["environment"]["image"]
                    ).image_id,
                }
            )
        ):
            raise PermissionError(
                "Unsupported exploratory purpose, effects, limits or environment"
            )
        if (
            set(envelope["inputs"]) - contract["inputs"].keys()
            or any(
                spec["required"] and name not in envelope["inputs"]
                for name, spec in contract["inputs"].items()
            )
            or any(
                spec["artifact_type"] not in {"json", "file", "table", "anndata"}
                for spec in contract["inputs"].values()
            )
            or {item.name: item.required for item in step.outputs}
            != {name: item["required"] for name, item in contract["outputs"].items()}
            or any(item.artifact_kind == "counts" for item in step.outputs)
            or any(
                item["artifact_type"] not in {"json", "file", "table", "anndata"}
                for item in contract["outputs"].values()
            )
        ):
            raise ValueError("Exploratory I/O ports differ from the frozen typed step")
        with self.ledger._database() as db:
            self._guard_input_scope(db, envelope)
        source = self.methods.artifact_path(method["source_artifact"])
        pin = exploratory_image(contract["environment"]["image"])
        preflight_source(
            source,
            pin.language,
            (a["representation"] for a in envelope["inputs"].values()),
        )
        return step, method, parameters

    def validate_dispatch(self, envelope):
        from .tier2_approval import require_approval

        validated = self.validate(envelope)
        if validated is not None:
            require_approval(self, envelope, validated[1]["contract"])
        return validated

    def _guard_input_scope(self, db, envelope):
        from devbench.analysis.evidence_access import EvidenceBroker

        from .partition_authority import embargoed

        step = envelope["step"]
        method = self.methods.method(step["oci_method_version_id"])["contract"]
        code = (
            method["implementation"]["source_sha256"],
            method["environment"]["lock_sha256"],
        )
        if any(embargoed(db, checksum) for checksum in code):
            raise PermissionError(
                "Protected bytes cannot be used as executable code or a lock"
            )
        inputs = tuple(row["sha256"] for row in envelope["inputs"].values())
        if not step.get("held_out"):
            for row in envelope["inputs"].values():
                self.guard_artifact(db, row)
        if not self.private and EvidenceBroker.has_private_content(
            db, (*code, *inputs)
        ):
            raise PermissionError("private_data_local_studio_only")

    def _matrix_object(self, row):
        return row["representation"] in {"exploratory_zarr", "exploratory_h5ad"} and (
            self.zarr_vault.contains(row["sha256"])
        )

    def guard_artifact(self, db, row, *, record=None):
        identity = row["sha256"]
        if not self._matrix_object(row):
            guard_digests(db, (identity,), private=self.private)
            return
        if identity not in self._member_cache or record is not None:
            if len(self._member_cache) >= 16:
                self._member_cache.clear()
            self._member_cache[identity] = self.zarr_vault.member_digests(
                identity, record
            )
        guard_digests(db, self._member_cache[identity], private=self.private)

    def admit_input(self, path, *, expected_sha256, artifact_type):
        """Bind operator-declared local bytes to training custody before a plan."""
        from .method_executor import _path
        from .tier2_staging import ENTRY_LIMIT

        path = _path(path)
        if "evaluation" in path.parts:
            raise PermissionError("An evaluation vault cannot be an exploratory input")
        record = hash_artifact(path, max_bytes=VAULT_BYTES, max_entries=ENTRY_LIMIT)
        with self.ledger._database() as db:
            guard_digests(db, artifact_digests(record), private=self.private)
        if record["sha256"] != expected_sha256:
            raise ValueError("Exploratory input differs from the declared bytes")
        if self.private:
            from devbench.analysis.evidence_access import EvidenceBroker

            broker = EvidenceBroker.open_existing(self.ledger.scientific)
            broker.taint_private_content(
                artifact_digests(record, marking_private=True),
                lineage_id=self.session_id,
            )
        if path.suffix in {".zarr", ".h5ad"}:
            if path.suffix == ".h5ad":
                from devbench.core.hdf5_custody import validate_h5ad_custody

                validate_h5ad_custody(path, expected_sha256=expected_sha256)
            stored = self.zarr_vault.put(path, expected_sha256=expected_sha256)
            rep = "exploratory_" + path.suffix[1:]

        elif path.is_file() and artifact_type in {"file", "anndata", "table", "json"}:
            stored = self.artifacts.put_file(path, expected_sha256=expected_sha256)
            rep = "exploratory_" + ("h5ad" if path.suffix == ".h5ad" else artifact_type)
        else:
            raise ValueError("Unsupported exploratory input")
        if stored != expected_sha256:
            raise ValueError("Exploratory input changed during vault admission")
        return rep

    @staticmethod
    def _validate_binary_ports(step, contract, inputs, binary):
        if binary and (
            step.held_out
            or any(
                port.artifact_kind != "table"
                for port in step.outputs
                if port.name != "manifest"
            )
        ):
            raise PermissionError(
                "Exploratory binary transport is limited to training table artifacts"
            )
        for name, value in inputs.items():
            if value["representation"] == "exploratory_parquet" and (
                contract["inputs"].get(name, {}).get("artifact_type") != "file"
            ):
                raise ValueError(
                    "Exploratory Parquet input requires a frozen binary file port"
                )

    def _directory(self, token: Document) -> Path:
        from devbench.engine.method_executor import _path

        if re.fullmatch(r"[a-f0-9]{32}", token["attempt_id"]) is None:
            raise ValueError("Invalid OCI attempt ID")
        return _path(self.runs / token["attempt_id"])

    def replay_references(self, workflow):
        """Resolve owned training outputs/receipts without opening any artifact."""
        from devbench.engine.workflow_operations import (
            LEDGER_NAME,
            WorkflowOperationLedger,
        )
        from devbench.platform.adaptive_workflow import AdaptiveWorkflow, request_digest

        if (
            type(workflow) is not AdaptiveWorkflow
            or workflow.ledger.path != self.ledger.path
            or self.methods.root / LEDGER_NAME != self.ledger.path
            or self.training.root != self.methods.root / "adaptive-oci/training"
            or self.runs != self.methods.root / "adaptive-oci/runs"
        ):
            raise PermissionError("Replay runtime owner differs from the workflow")
        reader = WorkflowOperationLedger(self.ledger.path, readonly=True, inert=True)
        flow = AdaptiveWorkflow(reader, workflow.lineage_id)
        references = []
        with flow._read_connection() as db:
            state = flow._state(db)
            for attempt_id, outcome in sorted(state.outcomes.items()):
                dispatch = state.dispatches[attempt_id]
                token, envelope = dispatch["token"], dispatch["envelope"]
                if token["backend_id"] != self.backend_id:
                    continue
                if any(
                    item.get("data_classification") == "private"
                    for item in outcome.get("outputs", {}).values()
                ):
                    raise PermissionError(
                        "Private replay materials remain in offline Studio"
                    )
                if (
                    envelope["step"]["held_out"]
                    or envelope["step"]["purpose"] == "protected_evaluation"
                ):
                    raise PermissionError(
                        "Replay runtime excludes the evaluation partition"
                    )
                attempt = reader.scientific._checked_attempt(db, token)
                if attempt["external_job_id"] != attempt_id:
                    raise PermissionError(
                        "Replay receipt is not an owned runtime attempt"
                    )
                basis = {
                    "owner_root": str(self.methods.root),
                    "attempt_id": attempt_id,
                    "token_sha256": request_digest(token),
                    "partition": "training",
                }
                references.append(
                    {
                        **basis,
                        "role": "original_oci_receipt",
                        "artifact_version_id": None,
                        "port": None,
                        "path": str(self.runs / attempt_id / "receipt.json"),
                        "sha256": outcome["receipt_sha256"],
                    }
                )
                for role, outputs in (
                    ("accepted_output", outcome["outputs"]),
                    ("unaccepted_output", outcome.get("staged_outputs", {})),
                ):
                    for port, artifact in outputs.items():
                        if artifact["kind"] == "counts":
                            # Original counts require their own data scope.
                            continue
                        references.append(
                            {
                                **basis,
                                "role": role,
                                "artifact_version_id": artifact["version_id"],
                                "port": port,
                                "path": (
                                    self.zarr_vault.attestation(artifact["sha256"])[
                                        "canonical_path"
                                    ]
                                    if artifact["representation"] == "exploratory_zarr"
                                    else str(self.training.root / artifact["sha256"])
                                ),
                                "sha256": artifact["sha256"],
                            }
                        )
        return references

    @staticmethod
    def _write(path: Path, value: Document) -> None:
        pending = path.with_suffix(".pending")
        with pending.open("wb") as stream:
            stream.write(canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)

    def _finish(
        self, directory, envelope, token, *, runtime, outputs, failure, cleanup, elapsed
    ):
        from devbench.platform.adaptive_workflow import StepReceipt, request_digest

        figures = {
            name: FigureRecord(
                figure_id=artifact["version_id"],
                step_id=envelope["step"]["step_id"],
                state="produced_unverified",
                source_artifact_id=self._identity(envelope["pins"]),
                diagnostic_purpose=envelope["step"]["operation_id"],
                reason="Output identity verified; visual inspection absent",
            ).model_dump(mode="json")
            for name, artifact in outputs.items()
            if artifact["kind"] == "figure"
        }
        tags = {} if envelope["step"].get("held_out") else dict(LANE_TAGS)
        if self.private:
            tags["data_classification"] = "private"
        evidence = {
            **tags,
            "frozen": self._load(directory / "frozen.json"),
            "runtime": runtime,
            "inputs": envelope["pins"],
            "outputs": outputs,
            "failure": failure,
            "figures": figures,
        }
        self._write(directory / "io.json", evidence)
        receipt = StepReceipt(
            **tags,
            job_id=token["attempt_id"],
            request_sha256=request_digest(envelope),
            consumed=envelope["pins"],
            status="failed" if failure else "succeeded",
            outputs=outputs,
            reason=failure,
            cleanup_state=cleanup,
            io_sha256=self._identity(evidence),
            wall_millis=elapsed,
        )
        self._write(directory / "receipt.json", receipt.model_dump(mode="json"))
        if cleanup == "confirmed":
            for name in ("verified-inputs", "inputs", "method", "outputs"):
                stage = directory / name
                if stage.is_dir():
                    # Staged input files may be immutable vault links. Only the
                    # private directory nodes need write permission for unlink.
                    for parent, _, _ in os.walk(stage):
                        Path(parent).chmod(0o700)
                    shutil.rmtree(stage)
        return receipt

    @staticmethod
    def _load(path: Path) -> Document:
        hash_artifact(path, max_bytes=3_000_000, max_entries=1)
        return json.loads(path.read_bytes())

    def _invocation(self, envelope: Document, token: Document) -> Document:
        from devbench.engine.scientific_authority import _lookup
        from devbench.platform.adaptive_workflow import AdaptiveWorkflow, request_digest

        flow = AdaptiveWorkflow(
            self.ledger, envelope["lineage_id"], partition_check=self.partition
        )
        retained, _ = flow._load_dispatch(token)
        attempt = flow.authority.attempt(token)
        if (
            retained != envelope
            or request_digest(envelope) != token["request_sha256"]
            or token["backend_id"] != self.backend_id
            or attempt["execution_state"] != "submission_uncertain"
        ):
            raise PermissionError(
                "Exploratory work requires an exact T11 submission intent"
            )
        with self.ledger._database() as db:
            db.execute("BEGIN")
            flow.authority._active(
                db,
                token["lineage_id"],
                flow.authority._load_state(db, token["lineage_id"]),
                token,
            )
            flow._guard(db, envelope, token=token)
            self._guard_input_scope(db, envelope)
            grant = _lookup(db, token["lineage_id"], attempt["grant_id"])
        from .tier2_approval import require_approval

        contract = self.methods.method(envelope["step"]["oci_method_version_id"])[
            "contract"
        ]
        require_approval(self, envelope, contract)
        return {
            "token": token,
            "grant_id": attempt["grant_id"],
            "grant_sha256": grant["sha256"],
            "method_version_id": envelope["step"]["oci_method_version_id"],
            "image_id": exploratory_image(
                self.methods.method(envelope["step"]["oci_method_version_id"])[
                    "contract"
                ]["environment"]["image"]
            ).image_id,
            "parameters_sha256": envelope["step"]["parameters_sha256"],
            "parameters": json.loads(
                self.parameters[envelope["step"]["parameters_sha256"]]
            ),
            "input_sha256": {pin["port"]: pin["sha256"] for pin in envelope["pins"]},
        }

    def run(self, envelope: Document, *, token: Document) -> StepReceipt:
        step, method, parameters = self.validate(envelope)
        from devbench.engine.method_executor import _path

        directory = self._directory(token)
        directory.mkdir(mode=0o700, exist_ok=True)
        with _path(directory / "lease").open("a+b") as lease:
            fcntl.flock(lease, fcntl.LOCK_EX)
            # Recovery can win the lease before this coordinator reaches it.
            frozen = self._invocation(envelope, token)
            if (directory / "frozen.json").exists():
                raise PermissionError(
                    "Exploratory attempt already has a frozen invocation"
                )
            self._write(directory / "frozen.json", frozen)
            return self._execute(
                envelope, token, directory, frozen, step, method, parameters
            )

    def _execute(self, envelope, token, directory, frozen, step, method, parameters):
        from devbench.engine.method_executor import LocalDockerExecutor

        output_dir = directory / "outputs"
        output_dir.mkdir(mode=0o700)
        start = time.monotonic()
        executor = LocalDockerExecutor()
        matrix_pins = {}

        def observe(event):
            from devbench.engine.workflow_operations import OperationBlocked

            self._write(directory / "binding.json", event)
            try:
                if event["event"] in {"container_create_intent", "worker_exec_intent"}:
                    check_stage(
                        self.zarr_vault,
                        directory,
                        matrix_pins,
                        capture_mount=event["event"] == "container_create_intent",
                    )
                return self._invocation(envelope, token) == frozen
            except OperationBlocked:
                return False

        executor.set_observer(observe)
        runtime = {}
        try:
            inputs = self._stage_oci_inputs(
                envelope, step, directory, matrix_pins=matrix_pins
            )
            contract = method["contract"]
            lint = (
                {}
                if step.held_out
                else preflight_source(
                    self.methods.artifact_path(method["source_artifact"]),
                    exploratory_image(contract["environment"]["image"]).language,
                    (a["representation"] for a in envelope["inputs"].values()),
                )
            )
            runtime = executor.execute(
                contract,
                inputs,
                parameters,
                self.methods.artifact_path(method["source_artifact"]),
                self.methods.artifact_path(method["environment_artifact"]),
                output_dir,
                frozen=frozen,
                **(
                    {"matrix_mounts": (self.zarr_vault, matrix_pins)}
                    if matrix_pins
                    else {}
                ),
            )
            runtime["preflight"] = lint
            check_stage(self.zarr_vault, directory, matrix_pins)
            runtime["matrix_attestations"] = matrix_pins
            if matrix_pins:
                with self.ledger._database() as db:
                    self._guard_input_scope(db, envelope)
            files, hashes, binary_references = self._check_collected_outputs(
                runtime,
                contract,
                inputs,
                step,
                output_dir,
            )
            outputs = self._retain_oci_outputs(
                step,
                envelope,
                token,
                files,
                hashes,
                binary_references,
            )
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            runtime = {
                **runtime,
                **getattr(error, "execution", {}),
                "error_type": type(error).__name__,
                "error": str(error)[:2000],
            }
            cleanup = self._settle_failure(directory, runtime, held_out=step.held_out)
            return self._finish(
                directory,
                envelope,
                token,
                runtime=runtime,
                outputs={},
                failure="exploratory_execution_failed",
                cleanup=cleanup,
                elapsed=int((time.monotonic() - start) * 1000),
            )
        return self._finish(
            directory,
            envelope,
            token,
            runtime=runtime,
            outputs=outputs,
            failure=None,
            cleanup="confirmed",
            elapsed=int((time.monotonic() - start) * 1000),
        )

    def _stage_oci_inputs(
        self, envelope, step, directory, *, matrix_pins=None, source_records=None
    ):
        from devbench.core.binary_artifacts import check_flat_parquet
        from devbench.engine.partition_authority import embargoed

        inputs = {}
        with self.ledger._database() as db:
            db.execute("BEGIN")
            held = {
                name
                for name, artifact in envelope["inputs"].items()
                if embargoed(db, artifact["sha256"])
            }
        if held and not step.held_out:
            raise PermissionError("Protected input denied outside the frozen evaluator")
        staging = directory / "verified-inputs"
        staging.mkdir(mode=0o700)
        for name, artifact in envelope["inputs"].items():
            if self._matrix_object(artifact):
                if name in held or step.held_out:
                    raise PermissionError(
                        "A training matrix cannot enter this evaluator"
                    )
                target = staging / name
                expected = (
                    (self.plugin_plan or {})
                    .get("registered_method", {})
                    .get("matrix_attestations", {})
                    .get(name)
                )
                record = self.zarr_vault.stage(
                    artifact["sha256"],
                    target,
                    record=(source_records or {}).get(name, expected),
                )
                with self.ledger._database() as db:
                    self.guard_artifact(db, artifact, record=record)
                if matrix_pins is not None:
                    matrix_pins[name] = {
                        "source": record,
                        "stage_stat": stage_identity(target),
                    }
                inputs[name] = target
                continue
            binary_input = artifact["representation"] == "exploratory_parquet"
            vault = (
                self.evaluation
                if name in held
                else self.binary_training
                if binary_input
                else self.artifacts
            )
            with vault.open(artifact["sha256"]) as stream:
                target = staging / name
                with target.open("xb") as staged:
                    shutil.copyfileobj(stream, staged, 1024**2)
            if binary_input:
                check_flat_parquet(target, artifact["sha256"], vault.max_bytes)
            inputs[name] = target
        return inputs

    @staticmethod
    def _check_collected_outputs(runtime, contract, inputs, step, output_dir):
        from devbench.analysis.method_validation import validate_outputs

        if runtime["exit_code"] != 0:
            raise ValueError("Exploratory worker returned a nonzero exit code")
        declared = {
            Path(spec["path"]).name: name for name, spec in contract["outputs"].items()
        }
        files = {declared[path.name]: path for path in output_dir.iterdir()}
        from devharness_core.methods import binary_oci_transport

        hashes = validate_ports(contract, step, files)
        if hashes != runtime["diagnostic_collection"]["files"]:
            raise ValueError("Exploratory outputs differ from trusted collection")
        validation = (
            validate_outputs(contract, inputs, files)
            if binary_oci_transport(contract)
            else {}
        )
        binary_references = validation.get("binary_artifacts", {})
        if binary_references:
            runtime["binary_artifacts"] = binary_references
        return files, hashes, binary_references

    def _authorize_output_custody(self, envelope, files, hashes, *, held_out):
        from devbench.analysis.evidence_access import EvidenceBroker

        from .tier2_staging import ENTRY_LIMIT

        if held_out:
            return
        identities = []
        private_digests = []
        for path in files.values():
            inventory = hash_artifact(
                path, max_bytes=VAULT_BYTES, max_entries=ENTRY_LIMIT
            )
            if inventory["sha256"] != hashes[path.name]:
                raise ValueError("Exploratory output changed before custody")
            identities.extend(artifact_digests(inventory))
            if self.private:
                private_digests.extend(
                    artifact_digests(inventory, marking_private=True)
                )
        with self.ledger._database() as db:
            guard_digests(db, identities, private=self.private)
        if self.private:
            broker = EvidenceBroker.open_existing(self.ledger.scientific)
            broker.taint_private_content(
                private_digests, lineage_id=envelope["lineage_id"]
            )

    def _retain_oci_outputs(
        self,
        step,
        envelope,
        token,
        files,
        hashes,
        binary_references,
    ):
        from devbench.platform.adaptive_workflow import ArtifactVersion

        outputs = {}
        vault = self.evaluation if step.held_out else self.artifacts
        parents = tuple(envelope["inputs"].values())
        membership = {
            (item["biological_unit"], item["unit_membership_sha256"])
            for item in parents
        }
        unit, members = (
            next(iter(membership))
            if len(membership) == 1
            else (
                "mixed",
                self._identity([list(item) for item in sorted(membership)]),
            )
        )
        # No arbitrary output can establish narrower ancestry. Subgroups and
        # figures retain the full mounted cohort and its selection exposure.
        learned = (
            "partition_only"
            if self.partition is not None
            and not step.held_out
            and len(membership) == 1
            and all(
                item["learned_scope"] in {"none", "partition_only"} for item in parents
            )
            else "unknown"
        )
        labels = {item["label_origin"] for item in parents} - {"none"}
        if step.held_out:
            self.partition.protect_outputs(hashes.values())
        self._authorize_output_custody(envelope, files, hashes, held_out=step.held_out)
        for port in step.outputs:
            if port.name in files:
                path = files[port.name]
                binary_reference = binary_references.get(port.name)
                output_vault = (
                    self.binary_training if binary_reference is not None else vault
                )
                identity = (
                    self.zarr_vault.put(
                        path, immutable_copy=True, expected_sha256=hashes[path.name]
                    )
                    if path.is_dir()
                    else output_vault.put_file(path, expected_sha256=hashes[path.name])
                )
                if identity != hashes[path.name] or (
                    binary_reference is not None
                    and identity != binary_reference["sha256"]
                ):
                    raise ValueError(
                        "Exploratory artifact changed before vault publication"
                    )
                tags = {} if step.held_out else dict(LANE_TAGS)
                if not step.held_out and port.artifact_kind == "figure":
                    contract = self.methods.method(step.oci_method_version_id)[
                        "contract"
                    ]
                    sidecar = contract["outputs"][port.name].get(
                        "plot_sidecar", "plot_sidecar"
                    )
                    tags["plot_sidecar_sha256"] = hashes[files[sidecar].name]
                if self.private:
                    tags["data_classification"] = "private"
                outputs[port.name] = ArtifactVersion(
                    **tags,
                    representation_class="derived_summary"
                    if not step.held_out
                    else None,
                    parent_sha256=tuple(sorted({item["sha256"] for item in parents}))
                    if not step.held_out
                    else None,
                    version_id=token["attempt_id"] + "-" + str(len(outputs)),
                    sha256=identity,
                    kind=port.artifact_kind,
                    representation=(
                        "exploratory_parquet"
                        if binary_reference is not None
                        else (
                            "exploratory_zarr"
                            if path.is_dir()
                            else "exploratory_h5ad"
                            if path.suffix == ".h5ad"
                            else port.artifact_kind
                        )
                    ),
                    biological_unit=unit,
                    unit_membership_sha256=members,
                    graph_sha256=identity if port.artifact_kind == "graph" else None,
                    learned_scope=learned,
                    selection_status="exposed",
                    label_origin=next(iter(labels))
                    if len(labels) == 1
                    else "unknown"
                    if labels
                    else "none",
                    permitted_uses=("protected_evaluation",)
                    if step.held_out
                    else (
                        "exploratory",
                        "checked_observation",
                        "protected_evaluation",
                    ),
                ).model_dump(mode="json")
        return outputs

    def _settle_failure(
        self, directory: Path, runtime: Document, *, held_out: bool
    ) -> Literal["confirmed", "unknown"]:
        # Protect known bytes before cleanup and any newly collected bytes as
        # part of cleanup, without rereading the same diagnostics twice.
        protected = self._protect_diagnostics(directory, runtime) if held_out else None
        try:
            return self._cleanup(directory, protected=protected)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            runtime["cleanup_error"] = type(error).__name__
            return "unknown"

    @staticmethod
    def _diagnostic_hashes(runtime: Document) -> set[str]:
        collection = runtime.get("diagnostic_collection")
        files = collection.get("files") if isinstance(collection, dict) else None
        return (
            {
                value
                for value in files.values()
                if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
            }
            if isinstance(files, dict)
            else set()
        )

    def _protect_diagnostics(self, directory: Path, runtime: Document) -> set[str]:
        from devbench.engine.method_executor import LocalDockerExecutor

        identities = self._diagnostic_hashes(runtime)
        for name in ("recovery.json", "binding.json"):
            try:
                path = directory / name
                if not path.exists():
                    continue
                evidence = self._load(path)
                if name == "binding.json" and evidence.get("container_id"):
                    collection = LocalDockerExecutor._collection_receipt(
                        evidence, evidence["max_output_bytes"]
                    )
                    evidence = {"diagnostic_collection": collection}
                identities.update(self._diagnostic_hashes(evidence))
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                runtime.setdefault("diagnostic_errors", {})[name] = type(error).__name__
        self.partition.protect_outputs(identities)
        return identities

    def _cleanup(
        self, directory: Path, *, protected: set[str] | None = None
    ) -> Literal["confirmed"]:
        from devbench.engine.method_executor import LocalDockerExecutor

        binding_path = directory / "binding.json"
        if not binding_path.exists():
            # The adapter writes an owned identity before the first create RPC.
            return "confirmed"
        binding = self._load(binding_path)
        state = LocalDockerExecutor._dispatch_state(binding)
        if state is not None and state.get("cleanup_complete"):
            return "confirmed"
        recovered = LocalDockerExecutor().cancel_run(binding)
        if protected is not None:
            additional = self._diagnostic_hashes(recovered) - protected
            if additional:
                self.partition.protect_outputs(additional)
        self._write(directory / "recovery.json", recovered)
        if recovered["state"] != "cancelled":
            raise RuntimeError("Exploratory cleanup remains uncertain")
        return "confirmed"

    def recover(self, flow: AdaptiveWorkflow, token: Document) -> Document:
        """Reconcile retained bytes or diagnostic failure without redispatch."""
        from devbench.engine.method_executor import _path
        from devbench.engine.workflow_operations import OperationBlocked

        if (
            flow.ledger.path != self.ledger.path
            or token.get("backend_id") != self.backend_id
        ):
            raise PermissionError(
                "Exploratory recovery requires the bound workflow and backend"
            )
        envelope, _ = flow._load_dispatch(token)
        directory = self._directory(token)
        directory.mkdir(mode=0o700, exist_ok=True)
        lease_path = _path(directory / "lease")
        if not lease_path.exists() and (directory / "binding.json").exists():
            raise OperationBlocked("exploratory_bound_runtime_lease_missing")
        with lease_path.open("a+b") as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise OperationBlocked(
                    "exploratory_coordinator_still_active"
                ) from error
            return self._recover_locked(flow, envelope, token, directory)

    def _recover_locked(self, flow, envelope, token, directory):
        from devbench.engine.method_executor import _path
        from devbench.engine.workflow_operations import OperationBlocked
        from devbench.platform.adaptive_workflow import StepReceipt

        frozen_path = _path(directory / "frozen.json")
        binding_path = _path(directory / "binding.json")
        frozen = self._load(frozen_path) if frozen_path.exists() else None
        if frozen is not None and frozen["token"] != token:
            raise PermissionError(
                "Exploratory recovery requires the exact retained token"
            )
        receipt_path = _path(directory / "receipt.json")
        if not receipt_path.exists() and not binding_path.exists():
            # A create RPC is reachable only after the synchronous observer has
            # durably written binding.json under this same coordinator lease.
            dispatch_path = _path(directory / "exploratory-dispatch-state.json")
            if (
                dispatch_path.exists()
                and self._load(dispatch_path).get("phase") != "create_intent"
                or (directory / "recovery.json").exists()
                or (directory / "exploratory-diagnostic-collection.json").exists()
                or (directory / "outputs").exists()
                and any((directory / "outputs").iterdir())
            ):
                raise OperationBlocked(
                    "exploratory_missing_binding_with_runtime_evidence"
                )
            attempt = flow.authority.attempt(token)
            if attempt.get("no_container_binding") is True:
                return attempt
            proof = self._identity(
                {
                    "token": token,
                    "observation": "no_durable_container_binding",
                    "frozen_sha256": self._identity(frozen)
                    if frozen is not None
                    else None,
                }
            )
            return flow.authority.reconcile_attempt(
                token,
                job_id=None,
                execution_state="noncompletion_confirmed",
                cleanup_state="confirmed",
                usage={},
                receipt_sha256=proof,
                _no_container_binding=True,
                verify_observation=lambda observation: observation[
                    "no_container_binding"
                ]
                and not binding_path.exists(),
            )
        if frozen is None:
            raise OperationBlocked(
                "exploratory_bound_runtime_without_frozen_invocation"
            )
        runtime = {}
        held_out = envelope["step"]["held_out"]
        if receipt_path.exists():
            receipt = StepReceipt.model_validate(self._load(receipt_path))
            outcome = flow.complete(token, receipt, verify=self.verify)
            attempt = flow.authority.attempt(token)
            if receipt.status == "failed" and attempt["cleanup_state"] != "confirmed":
                if (
                    self._settle_failure(directory, runtime, held_out=held_out)
                    == "confirmed"
                ):
                    path = directory / "recovery.json"
                    proof = self._identity(
                        {
                            "token": token,
                            "cleanup": "confirmed",
                            "runtime": self._load(path) if path.exists() else {},
                        }
                    )
                    flow.authority.reconcile_attempt(
                        token,
                        job_id=receipt.job_id,
                        execution_state="failed",
                        cleanup_state="confirmed",
                        usage=attempt["usage"],
                        receipt_sha256=proof,
                        verify_observation=lambda _: True,
                    )
            return outcome

        cleanup = self._settle_failure(directory, runtime, held_out=held_out)
        path = directory / "recovery.json"
        if path.exists():
            runtime.update(self._load(path))
        receipt = self._finish(
            directory,
            envelope,
            token,
            runtime=runtime,
            outputs={},
            failure="exploratory_coordinator_interrupted",
            cleanup=cleanup,
            elapsed=0,
        )
        return flow.complete(token, receipt, verify=self.verify)

    def binary_descriptor(self, artifact):
        """Read the structural reference retained for an accepted exploratory table."""
        from devbench.analysis.evidence_access import binary_evidence_reference
        from devbench.core.binary_artifacts import bound_parquet

        version = artifact.version_id
        if artifact.representation != "exploratory_parquet" or not re.fullmatch(
            r"[a-f0-9]{32}-[0-9]+", version
        ):
            raise ValueError("An owned exploratory Parquet table is required")
        directory = self._directory({"attempt_id": version.split("-")[0]})
        io = self._load(directory / "io.json")
        receipt = self._load(directory / "receipt.json")
        if (
            receipt["io_sha256"] != self._identity(io)
            or receipt["outputs"] != io["outputs"]
        ):
            raise ValueError("Exploratory Parquet receipt changed")
        ports = [
            name
            for name, value in receipt["outputs"].items()
            if value == artifact.model_dump(mode="json")
        ]
        if len(ports) != 1:
            raise ValueError("Exploratory table does not match its retained receipt")
        reference = binary_evidence_reference(
            io["runtime"]["binary_artifacts"][ports[0]],
            expected_sha256=artifact.sha256,
        )
        with bound_parquet(
            self.binary_training.root / artifact.sha256,
            artifact.sha256,
            self.binary_training.max_bytes,
            expected_size=reference["size_bytes"],
        ) as reader:
            if reader.metadata.num_rows != reference["row_count"]:
                raise ValueError(
                    "Exploratory Parquet row count differs from its retained manifest"
                )
        return reference

    def inspect_figure(
        self,
        workflow: AdaptiveWorkflow,
        *,
        step_id: str,
        figure_port: str,
        numeric_port: str | None = None,
    ) -> Document:
        """Project bounded diagnostic state from a current figure receipt."""
        from .exploratory_plot_evidence import inspect_figure

        return inspect_figure(
            self,
            workflow,
            step_id=step_id,
            figure_port=figure_port,
            numeric_port=numeric_port,
        )

    def _retained_receipt(
        self,
        state,
        step_id: str,
    ) -> tuple[Document, StepReceipt] | None:
        from devbench.engine.workflow_operations import OperationConflict
        from devbench.platform.adaptive_workflow import StepReceipt, request_digest

        outcome = state.outcome(step_id)
        if outcome is None:
            return None
        dispatch = state.dispatches[outcome["attempt_id"]]
        token, envelope = dispatch["token"], dispatch["envelope"]
        path = self._directory(token) / "receipt.json"
        if not path.is_file():
            return None
        receipt = StepReceipt.model_validate(self._load(path))
        receipt_digest = request_digest(receipt.model_dump(mode="json"))
        if (
            token["backend_id"] != self.backend_id
            or receipt_digest != outcome["receipt_sha256"]
            or not self.verify(envelope, receipt)
        ):
            raise OperationConflict("Retained Tier B receipt evidence changed")
        return envelope, receipt

    def _verify_matrix_scope(self, envelope, receipt, io):
        if not envelope["step"]["held_out"]:
            with self.ledger._database() as db:
                for port, source in envelope["inputs"].items():
                    if self._matrix_object(source):
                        pin = io["runtime"].get("matrix_attestations", {}).get(port)
                        if pin is None and receipt.status == "succeeded":
                            return False
                        if pin:
                            self.guard_artifact(db, source, record=pin["source"])
                for item in receipt.outputs.values():
                    self.guard_artifact(db, item.model_dump(mode="json"))
        return True

    def verify(self, envelope: Document, receipt: StepReceipt) -> bool:
        from devbench.engine.method_executor import _path
        from devbench.platform.adaptive_workflow import request_digest

        if re.fullmatch(r"[a-f0-9]{32}", receipt.job_id) is None:
            return False
        directory = _path(self.runs / receipt.job_id)
        saved = self._load(directory / "receipt.json")
        io = self._load(directory / "io.json")
        if (
            saved != receipt.model_dump(mode="json")
            or self._identity(io) != receipt.io_sha256
            or io["frozen"]["token"]["attempt_id"] != receipt.job_id
            or io["outputs"] != saved["outputs"]
            or request_digest(envelope) != receipt.request_sha256
            or io["inputs"] != envelope["pins"]
            or io["frozen"]["token"]["request_sha256"] != receipt.request_sha256
        ):
            return False
        if not self._verify_matrix_scope(envelope, receipt, io):
            return False
        vault = self.evaluation if envelope["step"]["held_out"] else self.artifacts
        for artifact in receipt.outputs.values():
            if artifact.representation == "exploratory_parquet":
                if envelope["step"]["held_out"]:
                    return False
                self.binary_descriptor(artifact)
            elif artifact.representation == "exploratory_zarr":
                self.zarr_vault.verify(artifact.sha256)
            else:
                with vault.open(artifact.sha256):
                    pass
        return True
