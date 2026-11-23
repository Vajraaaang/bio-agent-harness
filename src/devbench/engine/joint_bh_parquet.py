"""Bounded reads and writes of receipt-backed cross-aim gene tables."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, contextmanager
from typing import BinaryIO

from devbench.analysis.episode_evidence_queries import _open_tier_a_parquet
from devbench.core.joint_bh import JOINT_BH_INPUT, JointBHRow
from devbench.platform.adaptive_workflow_state import ArtifactVersion

from .provenance import canonical_json

_METADATA = b"devbench.result"
_LOCAL_ADJUSTMENTS = {"q_local", "q_budget"}
OpenMember = Callable[[ArtifactVersion], AbstractContextManager[BinaryIO]]


@contextmanager
def _open_member(artifact: ArtifactVersion, opener: OpenMember):
    if artifact.representation != JOINT_BH_INPUT or artifact.permitted_uses:
        raise PermissionError("Joint BH requires a sealed member artifact")
    with _open_tier_a_parquet(artifact, opener) as checked:
        yield checked


def read_probabilities(
    artifact: ArtifactVersion, opener: OpenMember, *, family_sha256: str
) -> tuple[tuple[str, float | None], ...]:
    with _open_member(artifact, opener) as (reader, footer):
        if footer["family_sha256"] != family_sha256:
            raise ValueError("Joint BH member differs from the compiled family")
        rows = tuple(
            (row["gene_id"], row["p_value"])
            for batch in reader.iter_batches(
                batch_size=2048,
                columns=["gene_id", "p_value"],
                use_threads=False,
            )
            for row in batch.to_pylist()
        )
        if (
            sum(probability is not None for _, probability in rows)
            != footer["tested_count"]
        ):
            raise ValueError("Joint BH member changed the attempted test inventory")
        return rows


def encode_pooled_table(
    artifact: ArtifactVersion,
    opener: OpenMember,
    adjusted: Sequence[JointBHRow],
    *,
    publication_sha256: str,
) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    with _open_member(artifact, opener) as (reader, footer):
        original = reader.schema_arrow
        fields = [field for field in original if field.name not in _LOCAL_ADJUSTMENTS]
        schema = pa.schema(
            fields,
            metadata={
                _METADATA: canonical_json(
                    {
                        **footer,
                        "multiplicity_method": "cross_aim_bh",
                        "multiplicity_sha256": publication_sha256,
                    }
                )
            },
        )
        if len(adjusted) != footer["row_count"]:
            raise ValueError("Joint BH projection differs from the attempted row count")
        sink = pa.BufferOutputStream()
        with pq.ParquetWriter(sink, schema, compression="snappy") as writer:
            position = 0
            for batch in reader.iter_batches(
                batch_size=2048, columns=schema.names, use_threads=False
            ):
                size = batch.num_rows
                projected = [
                    row.q_value for row in adjusted[position : position + size]
                ]
                columns = [
                    pa.array(projected, type=field.type)
                    if field.name == "q_value"
                    else batch.column(batch.schema.get_field_index(field.name))
                    for field in schema
                ]
                writer.write_batch(pa.RecordBatch.from_arrays(columns, schema=schema))
                position += size
    return sink.getvalue().to_pybytes()
