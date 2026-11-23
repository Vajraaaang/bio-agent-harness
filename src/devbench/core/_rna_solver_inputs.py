"""Bound file inputs and independent-donor designs for local RNA solvers."""

import csv
import hashlib
import io
from pathlib import Path
from typing import Annotated, Any, cast

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from pydantic import Field, model_validator

from ._files import read_bounded_file
from ._hashing import canonical_sha256
from .design import require_design_condition
from .publication_families import budgeted_bh
from .scientific_types import FrozenContract, content_sha256

MAX_INPUT_BYTES = 100_000_000
MAX_MATRIX_ENTRIES = MAX_INPUT_BYTES // np.dtype(np.float64).itemsize
Label = Annotated[str, Field(min_length=1, max_length=200)]


def read_solver_csv(path: Path, expected_sha256: str) -> pd.DataFrame:
    """Read a rectangular UTF-8 CSV by digest, preserving literal identifiers."""
    content = read_bounded_file(path, MAX_INPUT_BYTES)
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError("solver_input_digest_mismatch")
    if b"\0" in content:
        raise ValueError("solver_csv_requires_utf8_text")
    try:
        reader = csv.reader(
            io.StringIO(content.decode("utf-8"), newline=""), strict=True
        )
        header = next(reader, [])
        if (
            not header
            or len(set(header)) != len(header)
            or any(
                not label or label.strip() != label or len(label) > 200
                for label in header
            )
        ):
            raise ValueError("solver_csv_requires_unique_nonempty_headers")
        rows: list[list[str]] = []
        for row in reader:
            if len(row) != len(header):
                raise ValueError("solver_csv_row_width_mismatch")
            if (len(rows) + 1) * len(header) > MAX_MATRIX_ENTRIES:
                raise ValueError("solver_csv_table_over_budget")
            rows.append(row)
    except (csv.Error, UnicodeError) as error:
        raise ValueError("invalid_solver_csv") from error
    if not rows:
        raise ValueError("solver_csv_requires_data_rows")
    return pd.DataFrame(rows, columns=pd.Index(header))


def numeric_matrix(frame: pd.DataFrame) -> NDArray[np.float64]:
    """Check the dense float64 budget before converting values."""
    if not 0 < frame.size <= MAX_MATRIX_ENTRIES:
        raise ValueError("solver_numeric_matrix_empty_or_over_budget")
    try:
        values = frame.to_numpy(dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ValueError("solver_matrix_requires_numeric_values") from error
    if not np.isfinite(values).all():
        raise ValueError("solver_matrix_requires_finite_values")
    return cast(NDArray[np.float64], values)


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


def donor_design(
    metadata: pd.DataFrame,
    contrast: ConditionContrast,
    *,
    min_units_per_group: int = 3,
) -> tuple[tuple[str, ...], NDArray[np.float64], dict[str, Any]]:
    """Construct an independent-donor design with declared categorical effects."""
    columns = ("donor_id", "condition", *contrast.technical_factors)
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
    for factor in contrast.technical_factors:
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
    from rna_kernels.normalization import count_like

    frame = indexed_rows(read_solver_csv(counts_path, counts_sha256), observation_id)
    metadata = indexed_rows(
        read_solver_csv(metadata_path, metadata_sha256), observation_id
    )
    if set(frame.index) != set(metadata.index):
        raise ValueError("counts_and_metadata_observation_ids_must_match")
    counts = numeric_matrix(frame)
    if not count_like(counts) or np.any(counts.sum(axis=1) > 2**53):
        raise ValueError("count1_requires_bounded_nonnegative_integer_counts")
    return counts, metadata.loc[frame.index], list(frame.columns)
