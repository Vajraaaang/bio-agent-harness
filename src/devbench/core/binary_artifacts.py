"""Bounded readers for versioned local binary artifact transport."""

from contextlib import contextmanager

from devbench.core.population_data import bound_query_file

PARQUET_MAX_BYTES = 256 * 1024**2
PARQUET_MAX_ROWS = 100_000


@contextmanager
def bound_parquet(path, sha256, maximum=PARQUET_MAX_BYTES, *, expected_size=None):
    """Validate the footer and decode from the same digest-bound descriptor."""
    import os

    import pyarrow as pa
    import pyarrow.parquet as pq

    with bound_query_file(path, sha256, min(maximum, PARQUET_MAX_BYTES)) as stream:
        if (
            expected_size is not None
            and os.fstat(stream.fileno()).st_size != expected_size
        ):
            raise ValueError("Parquet evidence does not match its declared byte bound")
        try:
            reader = pq.ParquetFile(
                stream,
                thrift_string_size_limit=65_536,
                thrift_container_size_limit=16_384,
                arrow_extensions_enabled=False,
            )
            try:
                metadata = reader.metadata
                if (
                    not 0 < metadata.num_rows <= PARQUET_MAX_ROWS
                    or not 0 < metadata.num_row_groups <= 4096
                    or not 0
                    < sum(
                        metadata.row_group(i).total_byte_size
                        for i in range(metadata.num_row_groups)
                    )
                    <= PARQUET_MAX_BYTES
                ):
                    raise ValueError(
                        "Parquet footer exceeds its row or decoded byte bound"
                    )
                yield reader
            finally:
                reader.close()
        except pa.ArrowException as error:
            raise ValueError("Invalid bounded Parquet artifact") from error


def parquet_batches(reader, *, max_decoded_bytes=PARQUET_MAX_BYTES):
    size = rows = 0
    for batch in reader.iter_batches(batch_size=1024, use_threads=False):
        rows += batch.num_rows
        size += batch.nbytes
        if rows > PARQUET_MAX_ROWS or size > min(PARQUET_MAX_BYTES, max_decoded_bytes):
            raise ValueError("Parquet contents exceed the decoded byte or row bound")
        yield batch
    if rows != reader.metadata.num_rows:
        raise ValueError("Parquet footer row count differs from decoded evidence")


def check_flat_parquet(
    path, sha256, maximum=PARQUET_MAX_BYTES, *, expected_size=None, row_count=None
):
    """Check the structure and bounds of an exploratory table."""
    import re

    import pyarrow as pa
    import pyarrow.compute as pc

    with bound_parquet(path, sha256, maximum, expected_size=expected_size) as reader:
        schema = reader.schema_arrow
        if (
            not 0 < len(schema) <= 128
            or len(set(schema.names)) != len(schema)
            or any(
                not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", field.name)
                or not (
                    pa.types.is_string(field.type)
                    or pa.types.is_integer(field.type)
                    or pa.types.is_floating(field.type)
                    or pa.types.is_boolean(field.type)
                )
                for field in schema
            )
        ):
            raise ValueError(
                "Exploratory Parquet requires distinct, named scalar columns"
            )
        observed = reader.metadata.num_rows
        if row_count is not None and observed != row_count:
            raise ValueError("Exploratory Parquet row count differs from its manifest")
        for batch in parquet_batches(reader):
            for field, column in zip(schema, batch.columns, strict=True):
                if (
                    pa.types.is_floating(field.type)
                    and pc.any(pc.invert(pc.is_finite(column))).as_py()
                ):
                    raise ValueError("Exploratory Parquet numbers must be finite")
    return observed
