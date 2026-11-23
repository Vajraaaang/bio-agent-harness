"""Read bounded facts and figure pairs from accepted adaptive artifacts."""

from __future__ import annotations

import hashlib
import io
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager
from typing import Annotated, Any, BinaryIO, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from pydantic import Field, StrictInt, model_validator
from referencing import Registry

from devbench.adaptive.models import AdaptiveRecord
from devbench.core._hashing import decode_receipt_json
from devbench.core.contrast_design import TIER_A_BULK_OPERATION
from devbench.platform.adaptive_workflow_state import ArtifactVersion, Digest, Identity

from .episode_evidence_queries import _open_evidence_parquet
from .episode_synthesis import _verify_diagnostic_file_type
from .scientific_plot_verification import diagnostic_plot_state
from .thesis_exploratory_replay import is_exploratory_thesis_artifact

MAX_ANSWER_BYTES = 8_000_000
MAX_SCHEMA_BYTES = 1_000_000
MAX_ARTIFACT_BYTES = 16_000_000
PathKey = Annotated[str, Field(min_length=1, max_length=128)] | StrictInt
OpenArtifact = Callable[[ArtifactVersion], AbstractContextManager[BinaryIO]]


class AnswerFieldRef(AdaptiveRecord):
    """Resolve an answer property from accepted output or a failed receipt."""

    step_id: Identity
    port: Identity | None = None
    path: tuple[PathKey, ...] = Field(default=(), max_length=16)
    projection: Literal["json", "tier_a_gene_statistics", "failure_reason"] = "json"

    @model_validator(mode="after")
    def binding_shape(self) -> AnswerFieldRef:
        if (self.projection == "failure_reason") != (self.port is None):
            raise ValueError("Only a failed attempt can omit an output port")
        if self.path and self.projection != "json":
            raise ValueError("Only JSON projections can resolve a field path")
        if self.projection == "tier_a_gene_statistics" and self.port != "result":
            raise ValueError("A mounted family must name its complete result port")
        return self


class FigureRef(AdaptiveRecord):
    """One figure and numeric sidecar verified in the same accepted attempt."""

    step_id: Identity
    figure_port: Identity
    numeric_port: Identity
    report_port: Identity
    report_path: tuple[PathKey, ...] = Field(default=("plot",), max_length=16)
    figure_suffix: Literal[".png", ".pdf"] = ".png"
    numeric_suffix: Literal[".csv", ".json"] = ".csv"

    @model_validator(mode="after")
    def distinct_ports(self) -> FigureRef:
        if len({self.figure_port, self.numeric_port, self.report_port}) != 3:
            raise ValueError(
                "Figure receipts require distinct image, data and report ports"
            )
        return self


class VerifiedField(AdaptiveRecord):
    branch_id: Identity
    generation: Annotated[int, Field(gt=0, strict=True)]
    receipt_sha256: Digest
    attempt_id: Identity
    step_id: Identity
    artifact: ArtifactVersion | None
    claim_scope: str
    failure_reason: str | None = None


    def source_identity(self) -> dict[str, str | None]:
        return {
            "step_id": self.step_id,
            "receipt_sha256": self.receipt_sha256,
            "artifact_sha256": None if self.artifact is None else self.artifact.sha256,
        }


class NegativeFamilySummary(AdaptiveRecord):
    step_id: Identity
    receipt_sha256: Digest
    artifact_sha256: Digest
    tested_hypotheses: Annotated[int, Field(ge=0, strict=True)]
    minimum_q: Annotated[float, Field(ge=0, le=1, strict=True)] | None

    @model_validator(mode="after")
    def _consistent_test_count(self) -> NegativeFamilySummary:
        if (self.minimum_q is None) != (self.tested_hypotheses == 0):
            raise ValueError("The minimum q-value must reflect the tested hypotheses")
        return self


def _artifact_bytes(artifact: ArtifactVersion, opener: OpenArtifact) -> bytes:
    with opener(artifact) as handle:
        raw = handle.read(MAX_ARTIFACT_BYTES + 1)
    if (
        len(raw) > MAX_ARTIFACT_BYTES
        or hashlib.sha256(raw).hexdigest() != artifact.sha256
    ):
        raise ValueError("Thesis artifact identity or byte bound changed")
    return raw


def _json_output(artifact: ArtifactVersion, opener: OpenArtifact) -> Any:
    if artifact.kind != "table" or not (
        artifact.representation in {"json", "exploratory_json"}
        or artifact.representation.endswith("_json")
        or (
            artifact.representation == "table"
            and is_exploratory_thesis_artifact(artifact)
        )
    ):
        raise ValueError("Answer fields require a bounded JSON table")
    return decode_receipt_json(_artifact_bytes(artifact, opener))


def _path_value(document: Any, path: tuple[PathKey, ...]) -> Any:
    for key in path:
        if isinstance(key, str) and isinstance(document, dict) and key in document:
            document = document[key]
        elif (
            isinstance(key, int)
            and isinstance(document, list)
            and 0 <= key < len(document)
        ):
            document = document[key]
        else:
            raise ValueError("Answer field is absent from the exact accepted artifact")
    return document


def _probabilities(row: dict[str, Any]) -> bool:
    short = "p" in row or "q" in row
    if short and ("p_value" in row or "q_value" in row):
        raise ValueError("Gene statistics cannot mix probability aliases")
    p_value = row.get("p" if short else "p_value")
    q_value = row.get("q" if short else "q_value")
    if p_value is None and q_value is None:
        if row.get("estimate_status") == "estimated":
            raise ValueError("Estimated genes require finite probabilities")
        return False
    if row.get("estimate_status") not in (None, "estimated"):
        raise ValueError("Non-estimated genes require null probabilities")
    if (
        p_value is None
        or q_value is None
        or type(p_value) not in (int, float)
        or type(q_value) not in (int, float)
        or not 0 <= p_value <= q_value <= 1
    ):
        raise ValueError("Confirmatory statistics require 0 <= p <= q <= 1")
    return True


def _gene_rows(
    artifact: ArtifactVersion, opener: OpenArtifact
) -> Iterator[dict[str, Any]]:
    columns = [
        "gene_id",
        "hypothesis_id",
        "estimate_status",
        "effect",
        "p_value",
        "q_value",
        "missing_reason",
    ]
    with _open_evidence_parquet(artifact, opener) as (reader, footer):
        statuses: Counter[str] = Counter()
        hypotheses: set[str] = set()
        tested = 0
        for batch in reader.iter_batches(
            batch_size=2048, columns=columns, use_threads=False
        ):
            for row in batch.to_pylist():
                hypothesis, status = row["hypothesis_id"], row["estimate_status"]
                if not hypothesis or hypothesis in hypotheses or not row["gene_id"]:
                    raise ValueError("Thesis gene statistics require unique hypotheses")
                hypotheses.add(hypothesis)
                statuses[status] += 1
                tested += _probabilities(row)
                yield row
        if (
            tested != footer["tested_count"]
            or dict(statuses) != footer["status_counts"]
        ):
            raise ValueError("Thesis gene statistics differ from the retained family")


def extract_field(
    reference: AnswerFieldRef,
    binding: VerifiedField,
    opener: OpenArtifact,
) -> Any:
    if reference.projection == "failure_reason":
        return binding.failure_reason
    artifact = binding.artifact
    if artifact is None:
        raise ValueError("Answer field has no accepted output")
    if reference.projection == "tier_a_gene_statistics":
        if (
            binding.claim_scope != "admitted_research_inference"
            or "admitted_research_inference" not in artifact.permitted_uses
        ):
            raise PermissionError(
                "Thesis gene statistics require current research admission"
            )
        return list(_gene_rows(artifact, opener))
    document = _json_output(artifact, opener)
    if (
        is_exploratory_thesis_artifact(artifact)
        and "gene_statistics" in reference.path
    ):
        validate_gene_fields(document, inferential_scope=False)
    if (
        artifact.representation == "scrna_donor_trajectory_result_json"
        and isinstance(document, dict)
        and document.get("claim_scope") == "candidate_research_inference"
        and (not reference.path or reference.path[0] == "gene_statistics")
        and (
            binding.claim_scope != "admitted_research_inference"
            or "admitted_research_inference" not in artifact.permitted_uses
        )
    ):
        raise PermissionError(
            "Thesis trajectory statistics require current research admission"
        )
    return _path_value(document, reference.path)


def validate_gene_fields(answer: Any, *, inferential_scope: bool) -> None:
    """Validate gene-statistic families wherever the supplied answer nests them."""
    pending = [answer]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            stats = value.get("gene_statistics")
            if stats is not None:
                if not isinstance(stats, list) or any(
                    not isinstance(row, dict) for row in stats
                ):
                    raise ValueError("Thesis gene statistics must be a table")
                for row in stats:
                    if _probabilities(row) and not inferential_scope:
                        raise PermissionError(
                            "Exploratory evidence cannot supply inference"
                        )
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


def negative_confirmatory_aims(
    lifecycle: dict[str, Any],
) -> Iterator[dict[str, Any]]:
    for aim in lifecycle["research_aims"]:
        if aim["kind"] == "confirmatory" and aim["status"] == "no_significant_finding":
            yield aim


def _summarize_family(
    source: dict[str, str | None], rows: Iterable[dict[str, Any]]
) -> NegativeFamilySummary:
    tested = 0
    minimum = None
    for row in rows:
        q = row.get("q_value", row.get("q"))
        if q is not None:
            tested += 1
            minimum = q if minimum is None else min(minimum, q)
    return NegativeFamilySummary.model_validate(
        {**source, "tested_hypotheses": tested, "minimum_q": minimum}
    )


def read_negative_families(
    fields: dict[str, list[VerifiedField]], opener: OpenArtifact
) -> dict[str, list[dict[str, Any]]]:
    summaries = {}
    for aim_id, bindings in fields.items():
        summaries[aim_id] = [
            _summarize_family(
                binding.source_identity(), _gene_rows(binding.artifact, opener)
            ).model_dump(mode="json")
            for binding in bindings
            if binding.artifact is not None
        ]
    return summaries


def _negative_families(
    aim: dict[str, Any],
    manifest: dict[str, Any],
    projected: tuple[dict[str, str | None], NegativeFamilySummary] | None,
) -> Iterator[NegativeFamilySummary]:
    cited = aim["outcome"]["evidence"]
    for record in manifest.get("negative_aim_families", {}).get(aim["aim_id"], []):
        summary = NegativeFamilySummary.model_validate(record)
        source = summary.model_dump(
            include={"step_id", "receipt_sha256", "artifact_sha256"}
        )
        if source not in cited:
            raise ValueError("Negative aim family differs from its cited result")
        if projected and source == projected[0] and summary != projected[1]:
            raise ValueError("Negative aim family differs from projected statistics")
        yield summary
    if projected and projected[0] in cited:
        yield projected[1]


def validate_negative_aims(
    manifest: dict[str, Any], answer: dict[str, Any]
) -> None:
    """Check negative conclusions against their exact cited tested families."""
    negative_aims = tuple(negative_confirmatory_aims(manifest["aim_lifecycle"]))
    if not negative_aims:
        return
    rows = answer.get("gene_statistics")
    projected = None
    if rows is not None:
        binding = VerifiedField.model_validate(manifest["fields"]["gene_statistics"])
        source = binding.source_identity()
        if binding.artifact is not None:
            projected = (source, _summarize_family(source, rows))
    inference_steps = {
        method["step_id"]
        for method in manifest["methods"]
        if method["operation_id"] == TIER_A_BULK_OPERATION
        and method["purpose"] in {"admitted_research_inference", "qualified_inference"}
    }
    for aim in negative_aims:
        cited_inference_steps = {
            item["step_id"]
            for item in aim["outcome"]["evidence"]
            if item.get("artifact_sha256") is not None
            and item["step_id"] in inference_steps
        }
        families = tuple(_negative_families(aim, manifest, projected))
        if cited_inference_steps and not any(
            family.step_id in cited_inference_steps for family in families
        ):
            raise ValueError(f"Aim {aim['aim_id']} lacks a verified tested family")
        for tested in families:
            if tested.minimum_q is None:
                raise ValueError(f"Aim {aim['aim_id']} lacks tested hypotheses")
            if tested.minimum_q <= aim["multiplicity"]["fdr"]:
                raise ValueError(
                    f"Aim {aim['aim_id']} has a significant q-value in its cited result"
                )


def validate_answer(schema: Any, answer: dict[str, Any]) -> None:
    if not isinstance(schema, dict):
        raise ValueError("Thesis answer schema must be a JSON object")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, registry=Registry())
    for error in validator.iter_errors(answer):
        path = " ".join(str(key)[:128] for key in error.absolute_path) or "root"
        # Do not echo the rejected result or supplied data into the report.
        raise ValidationError(f"Answer failed {error.validator} at {path}") from None


def read_figure_pair(
    reference: FigureRef,
    *,
    figure: ArtifactVersion,
    numeric: ArtifactVersion,
    report: ArtifactVersion,
    opener: OpenArtifact,
) -> tuple[bytes, bytes, dict[str, Any]]:
    if figure.kind != "figure" or numeric.kind not in {"table", "embedding"}:
        raise ValueError("Thesis figures require their typed image and sidecar ports")
    state = _path_value(_json_output(report, opener), reference.report_path)
    if not isinstance(state, dict) or (
        state.get("status"),
        state.get("image_sha256"),
        state.get("data_sha256"),
    ) != ("rendered", figure.sha256, numeric.sha256):
        raise ValueError("Figure and numeric sidecar differ from the rendering report")
    declared = state.get("plot_state")
    if not isinstance(declared, dict) or declared.get("render_verified") is not True:
        raise ValueError(
            "A thesis figure requires receipt-backed rendering verification"
        )
    image = _artifact_bytes(figure, opener)
    data = _artifact_bytes(numeric, opener)
    _verify_diagnostic_file_type(reference.figure_suffix, image)
    _verify_diagnostic_file_type(reference.numeric_suffix, data)
    if reference.figure_suffix == ".png":
        from PIL import Image

        with Image.open(io.BytesIO(image)) as decoded:
            if decoded.format != "PNG" or decoded.width * decoded.height > 16_000_000:
                raise ValueError("Thesis PNG does not match its bounded image contract")
            decoded.verify()
    assessment = declared.get("scientific_assessment")
    return (
        image,
        data,
        diagnostic_plot_state(
            rendered=True,
            render_verified=True,
            image_accessible=True,
            scientific_assessment=(
                assessment if assessment in {"unavailable", "pending"} else "pending"
            ),
        ),
    )
