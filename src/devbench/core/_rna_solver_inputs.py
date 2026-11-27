"""Bound file inputs and independent-donor designs for local RNA solvers."""

import csv
import io
import sys
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile
from typing import Annotated, Any, cast

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from pydantic import Field, model_validator

from ._hashing import canonical_sha256
from .design import require_design_condition
from .population_data import bound_query_file
from .publication_families import budgeted_bh
from .scientific_types import FrozenContract, content_sha256

# File admission is streamed; transport limits are independent of the explicitly
# bounded dense working sets of methods that cannot evaluate out of core.
MAX_INPUT_BYTES = sys.maxsize
MAX_MATRIX_ENTRIES = sys.maxsize // np.dtype(np.float64).itemsize
DENSE_WORK_BYTES = 100_000_000
DENSE_WORK_ENTRIES = DENSE_WORK_BYTES // np.dtype(np.float64).itemsize
_CHUNK_BYTES = 8 * 1024**2
MAX_EXACT_COUNT = 2**53
Label = Annotated[str, Field(min_length=1, max_length=200)]


@contextmanager
def _solver_rows(path, expected_sha256):
    try:
        with bound_query_file(path, expected_sha256, path.lstat().st_size) as binary:
            stream = io.TextIOWrapper(binary, encoding="utf-8", newline="")
            try:
                reader = csv.reader(stream, strict=True)
                header = next(reader, [])
                if (
                    not header
                    or len(set(header)) != len(header)
                    or any(
                        not label
                        or label.strip() != label
                        or len(label) > 200
                        or "\x00" in label
                        for label in header
                    )
                ):
                    raise ValueError("solver_csv_requires_unique_nonempty_headers")
                yield header, _checked_rows(reader, len(header))
            finally:
                stream.detach()
    except (csv.Error, UnicodeError) as error:
        raise ValueError("invalid_solver_csv") from error
    except ValueError as error:
        if str(error) == "Evidence input hash mismatch":
            raise ValueError("solver_input_digest_mismatch") from error
        raise


def _checked_rows(reader, width):
    for row in reader:
        if len(row) != width:
            raise ValueError("solver_csv_row_width_mismatch")
        if any("\x00" in token for token in row):
            raise ValueError("solver_csv_requires_utf8_text")
        yield row


def _backed_text_frame(header, rows):
    """Keep string columns backed by a local Arrow mapping after validation."""
    import pyarrow as pa

    schema = pa.schema([(name, pa.string()) for name in header])
    with TemporaryDirectory() as temporary:
        path = Path(temporary) / "table.arrow"
        with (
            pa.OSFile(str(path), "wb") as sink,
            pa.ipc.new_file(sink, schema) as writer,
        ):
            batch, size = [], 0
            for row in rows:
                size += sum(64 + len(token) * 4 for token in row)
                batch.append(row)
                if size >= _CHUNK_BYTES:
                    writer.write_batch(
                        pa.record_batch(
                            [
                                pa.array(col, type=pa.string())
                                for col in zip(*batch, strict=True)
                            ],
                            schema=schema,
                        )
                    )
                    batch, size = [], 0
            if batch:
                writer.write_batch(
                    pa.record_batch(
                        [
                            pa.array(col, type=pa.string())
                            for col in zip(*batch, strict=True)
                        ],
                        schema=schema,
                    )
                )
        with pa.memory_map(str(path), "r") as source:
            return (
                pa.ipc.open_file(source)
                .read_all()
                .to_pandas(types_mapper=pd.ArrowDtype)
            )


def read_solver_csv(path: Path, expected_sha256: str) -> pd.DataFrame:
    """Parse an attested UTF-8 file, preserving exact tokens and bounded buffers."""
    with _solver_rows(path, expected_sha256) as (header, rows):
        if path.lstat().st_size > _CHUNK_BYTES:
            frame = _backed_text_frame(header, rows)
        else:
            frame = pd.DataFrame(rows, columns=pd.Index(header))
    if frame.empty:
        raise ValueError("solver_csv_requires_data_rows")
    return frame


def _matrix_buffer(shape):
    if min(shape) < 1:
        raise ValueError("solver_numeric_matrix_empty_or_over_budget")
    if shape[0] * shape[1] * 8 <= _CHUNK_BYTES:
        return np.empty(shape, dtype=np.float64)
    with TemporaryFile() as backing:
        return np.memmap(backing, shape=shape, dtype=np.float64, mode="w+")


def numeric_matrix(frame: pd.DataFrame) -> NDArray[np.float64]:
    """Convert bounded row blocks to a disk-backed array when needed."""
    values = _matrix_buffer(frame.shape)
    rows = max(1, _CHUNK_BYTES // (frame.shape[1] * 8))
    for start in range(0, len(frame), rows):
        try:
            block = frame.iloc[start : start + rows].to_numpy(dtype=np.float64)
        except (TypeError, ValueError) as error:
            raise ValueError("solver_matrix_requires_numeric_values") from error
        if not np.isfinite(block).all():
            raise ValueError("solver_matrix_requires_finite_values")
        values[start : start + len(block)] = block
    return cast(NDArray[np.float64], values)


def _raw_count(token: str) -> int:
    error = "count1_requires_bounded_nonnegative_integer_counts"
    if not isinstance(token, str) or len(token) > 64 or not token.isascii():
        raise ValueError(error)
    if token.isdecimal():
        count = int(token)
        if count <= MAX_EXACT_COUNT:
            return count
        raise ValueError(error)
    try:
        value = Decimal(token)
    except InvalidOperation as exc:
        raise ValueError(error) from exc
    if not value.is_finite() or not 0 <= value <= MAX_EXACT_COUNT:
        raise ValueError(error)
    if value != value.to_integral_value():
        raise ValueError(error)
    return int(value)


def _exact_row(tokens):
    counts = tuple(map(_raw_count, tokens))
    if sum(counts) > MAX_EXACT_COUNT:
        raise ValueError("count1_requires_bounded_nonnegative_integer_counts")
    return counts


def raw_count_matrix(frame: pd.DataFrame) -> NDArray[np.float64]:
    """Validate exact integer tokens and observation totals before conversion."""
    values = _matrix_buffer(frame.shape)
    for i, row in enumerate(frame.itertuples(index=False, name=None)):
        values[i] = _exact_row(row)
    return values


def require_labels(frame: pd.DataFrame, columns: tuple[str, ...]) -> None:
    if not set(columns).issubset(frame.columns):
        raise ValueError("required_solver_columns_missing")
    for column in columns:
        if any(
            not isinstance(value, str)
            or not value
            or value.strip() != value
            or len(value) > 200
            for value in frame[column]
        ):
            raise ValueError("missing_or_invalid_labels")


def indexed_rows(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    require_labels(frame, (column,))
    if frame[column].duplicated().any():
        raise ValueError("solver_observation_or_feature_ids_must_be_unique")
    return frame.set_index(column)


class ConditionContrast(FrozenContract):
    numerator: Label
    denominator: Label
    technical_factors: tuple[Label, ...] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def distinct_roles(self) -> "ConditionContrast":
        factors = set(self.technical_factors)
        if self.numerator == self.denominator or any(
            arm.strip() != arm for arm in (self.numerator, self.denominator)
        ):
            raise ValueError("distinct_exact_condition_labels_required")
        if len(factors) != len(self.technical_factors) or factors.intersection(
            {"cell_id", "donor_id", "condition", "cell_type"}
        ):
            raise ValueError("distinct_technical_factors_required")
        return self


def _technical_factors(
    metadata: pd.DataFrame, contrast: ConditionContrast
) -> tuple[str, ...]:
    factors = contrast.technical_factors
    if "batch" not in metadata or "batch" in factors:
        return factors
    require_labels(metadata, ("batch",))
    return (*factors, "batch") if metadata["batch"].nunique() > 1 else factors


def donor_design(
    metadata: pd.DataFrame,
    contrast: ConditionContrast,
    *,
    min_units_per_group: int = 3,
) -> tuple[tuple[str, ...], NDArray[np.float64], dict[str, Any]]:
    """Construct an independent-donor design with declared categorical effects."""
    technical_factors = _technical_factors(metadata, contrast)
    columns = ("donor_id", "condition", *technical_factors)
    require_labels(metadata, columns)
    profiles = metadata.loc[:, list(columns)].drop_duplicates().sort_values("donor_id")
    if profiles["donor_id"].duplicated().any():
        raise ValueError("each_donor_requires_one_condition_and_technical_profile")
    conditions = profiles["condition"]
    levels = {contrast.denominator, contrast.numerator}
    if set(conditions) - levels:
        raise ValueError("unexpected_donor_condition")
    counts = {label: int((conditions == label).sum()) for label in sorted(levels)}
    if min(counts.values()) < min_units_per_group:
        if min_units_per_group == 3:
            raise ValueError("at_least_three_donors_per_condition_required")
        raise ValueError("insufficient_biological_units_per_condition")
    values = [
        np.ones(len(profiles)),
        (conditions == contrast.numerator).to_numpy(float),
    ]
    terms = ["intercept", "condition"]
    for factor in technical_factors:
        labels = sorted(set(profiles[factor]))
        if len(labels) < 2:
            raise ValueError("rank_deficient_design: technical factor has one level")
        if len(labels) > 16:
            raise ValueError("technical_factor_levels_over_budget")
        for level in labels[1:]:
            values.append((profiles[factor] == level).to_numpy(float))
            terms.append(f"{factor}={level}")
    matrix = np.column_stack(values)
    diagnostics = require_design_condition(matrix)
    if diagnostics["residual_df"] <= 0:
        raise ValueError("insufficient_donor_residual_degrees_of_freedom")
    diagnostics.update(model_terms=terms, donors_per_condition=counts)
    return tuple(profiles["donor_id"]), matrix, diagnostics


def adjust_probabilities(probabilities: list[float | None]) -> list[float | None]:
    """Count unestimable tests in the BH family and report their statistics as null."""
    return [
        result["q_local"] for result in budgeted_bh(probabilities, "1", pad_failed=True)
    ]


def solver_receipt(
    result_format: str,
    spec: FrozenContract,
    inputs: dict[str, str | None],
    details: dict[str, Any],
) -> dict[str, Any]:
    specification = spec.model_dump(mode="json")
    receipt = {
        **details,
        "format": result_format,
        "specification": specification,
        "specification_sha256": content_sha256(specification),
        "input_sha256s": {name: digest for name, digest in inputs.items() if digest},
        "clinical_release_allowed": False,
    }
    return {**receipt, "receipt_sha256": canonical_sha256(receipt)}


def read_count_and_metadata(
    counts_path: Path,
    metadata_path: Path,
    counts_sha256: str,
    metadata_sha256: str,
    observation_id: str,
) -> tuple[NDArray[np.float64], pd.DataFrame, list[str]]:
    """Read aligned observation-by-feature raw counts without normalization."""
    metadata = indexed_rows(
        read_solver_csv(metadata_path, metadata_sha256), observation_id
    )
    with _solver_rows(counts_path, counts_sha256) as (header, rows):
        if observation_id not in header:
            raise ValueError("required_solver_columns_missing")
        id_column = header.index(observation_id)
        genes = [name for name in header if name != observation_id]
        counts = _matrix_buffer((len(metadata), len(genes)))
        order, seen = [], set()
        for index, row in enumerate(rows):
            label = row.pop(id_column)
            if label not in metadata.index:
                raise ValueError("counts_and_metadata_observation_ids_must_match")
            if label in seen:
                raise ValueError("solver_observation_or_feature_ids_must_be_unique")
            counts[index] = _exact_row(row)
            seen.add(label)
            order.append(label)
    if len(order) != len(metadata):
        raise ValueError("counts_and_metadata_observation_ids_must_match")
    return counts, metadata.loc[order], genes
