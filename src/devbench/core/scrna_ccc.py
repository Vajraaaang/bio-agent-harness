"""Donor-stratified ligand–receptor coexpression on declared normalized RNA."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from pydantic import Field, model_validator
from scipy.stats import t

from ._hashing import canonical_sha256
from ._rna_solver_inputs import (
    MAX_MATRIX_ENTRIES,
    ConditionContrast,
    Label,
    adjust_probabilities,
    donor_design,
    indexed_rows,
    numeric_matrix,
    read_solver_csv,
    require_labels,
)
from .design import require_design_condition
from .scientific_types import FrozenContract, Probability, Sha256, content_sha256

_Floats = NDArray[np.float64]
_Ints = NDArray[np.intp]
_Complex = tuple[str, ...]


class CellCommunicationSpec(FrozenContract):
    """Freeze cell metadata, expression, reference and analysis parameters."""

    format: Literal["devbench.scrna-ccc/1"] = "devbench.scrna-ccc/1"
    expression_sha256: Sha256
    metadata_sha256: Sha256
    ligand_receptor_sha256: Sha256
    normalization: Literal["library_size_normalized", "library_size_log1p"]
    normalization_receipt_sha256: Sha256 | None = None
    mode: Literal["within_condition_specificity", "differential_condition"]
    condition: Label | None = None
    contrast: ConditionContrast | None = None
    min_cells_per_donor_type: int = Field(ge=1)
    complex_aggregation: Literal["min", "geometric_mean"] = "min"
    n_perm: int = Field(default=999, ge=1, le=20 * (MAX_MATRIX_ENTRIES // 32))
    seed: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def check_mode(self) -> "CellCommunicationSpec":
        if self.mode == "differential_condition":
            if self.contrast is None or self.condition is not None:
                raise ValueError("differential_condition_requires_exact_contrast")
        elif self.contrast is not None:
            raise ValueError("within_condition_specificity_accepts_no_contrast")
        return self


class CommunicationStatistic(FrozenContract):
    interaction_id: str
    sender: str
    receiver: str
    donors: int
    ligand_mean_expression: float | None = None
    receptor_mean_expression: float | None = None
    ligand_expressed_fraction: Probability | None = None
    receptor_expressed_fraction: Probability | None = None
    interaction_magnitude: float | None = None
    specificity: Probability | None = None
    effect: float | None = None
    p_value: Probability | None = None
    q_value: Probability | None = None
    missing_reason: str | None = None

    @model_validator(mode="after")
    def check_probability_order(self) -> "CommunicationStatistic":
        if (self.p_value is None) != (self.q_value is None) or (
            self.p_value is not None
            and self.q_value is not None
            and self.p_value > self.q_value
        ):
            raise ValueError("invalid_communication_probability_order")
        return self


@dataclass(frozen=True)
class _Donor:
    name: str
    expression: _Floats
    labels: _Ints
    sizes: _Ints


def _read_reference(
    path: Path | None, digest: str
) -> dict[str, tuple[_Complex, _Complex]]:
    if path is None:
        raise ValueError("ligand_receptor_reference_required")
    try:
        table = indexed_rows(read_solver_csv(path, digest), "interaction_id")
    except FileNotFoundError as error:
        raise ValueError("ligand_receptor_reference_required") from error
    if set(table.columns) != {"ligand", "receptor"}:
        raise ValueError("ligand_receptor_reference_requires_ligand_and_receptor")
    require_labels(table, ("ligand", "receptor"))
    reference = {}
    for name, ligand, receptor in table.itertuples():
        complexes = (
            tuple(sorted(ligand.split("|"))),
            tuple(sorted(receptor.split("|"))),
        )
        if any(
            len(set(side)) != len(side)
            or any(not gene or gene.strip() != gene for gene in side)
            for side in complexes
        ):
            raise ValueError("invalid_ligand_receptor_complex_subunits")
        reference[str(name)] = complexes
    if len(set(reference.values())) != len(reference):
        raise ValueError("duplicate_ligand_receptor_reference_pairs")
    return reference


def _select_condition(
    metadata: pd.DataFrame, spec: CellCommunicationSpec
) -> pd.DataFrame:
    if spec.mode == "differential_condition":
        return metadata
    if "condition" not in metadata:
        if spec.condition is not None:
            raise ValueError("declared_condition_requires_cell_condition_labels")
        return metadata
    require_labels(metadata, ("condition",))
    conditions = set(metadata["condition"])
    if spec.condition is None:
        if len(conditions) != 1:
            raise ValueError(
                "within_condition_specificity_requires_one_selected_condition"
            )
        return metadata
    if spec.condition not in conditions:
        raise ValueError("declared_condition_absent")
    return metadata.loc[metadata["condition"] == spec.condition]


def _group_means(values: _Floats, labels: _Ints, sizes: _Ints) -> _Floats:
    sums = np.zeros((len(sizes), values.shape[1]))
    np.add.at(sums, labels, values)
    return sums / np.maximum(sizes, 1)[:, None]


def _complex_means(
    means: _Floats,
    columns: tuple[_Ints, ...],
    method: str,
) -> _Floats:
    output = np.empty((means.shape[0], len(columns)))
    for index, genes in enumerate(columns):
        subunits = means[:, genes]
        if method == "min":
            output[:, index] = subunits.min(axis=1)
        else:
            present = (subunits > 0).all(axis=1)
            output[:, index] = 0
            output[present, index] = np.exp(np.log(subunits[present]).mean(axis=1))
    return output


def _specificity(means: _Floats, ligand: _Ints, receptor: _Ints) -> _Floats:
    totals = means.sum(axis=0)
    shares = np.divide(means, totals, out=np.zeros_like(means), where=totals > 0)
    return shares.T[ligand, :, None] * shares.T[receptor, None, :]


def _complex_fractions(donor: _Donor, columns: tuple[_Ints, ...]) -> _Floats:
    fractions = np.zeros((len(donor.sizes), len(columns)))
    for index, genes in enumerate(columns):
        detected = (donor.expression[:, genes] > 0).all(axis=1)
        np.add.at(fractions[:, index], donor.labels, detected)
    return fractions / np.maximum(donor.sizes, 1)[:, None]


def _permutation_probabilities(
    donors: list[_Donor],
    columns: tuple[_Ints, ...],
    ligand: _Ints,
    receptor: _Ints,
    observed: _Floats,
    spec: CellCommunicationSpec,
    n_perm: int,
) -> _Floats:
    rng = np.random.default_rng(spec.seed)
    exceedances = np.zeros(observed.shape, dtype=np.intp)
    # Cell summation order can cause roundoff on otherwise tied permutations.
    threshold = observed * (1 - 1e-12)
    for _ in range(n_perm):
        permuted = np.zeros_like(observed)
        for donor in donors:
            labels = rng.permutation(donor.labels)
            means = _complex_means(
                _group_means(donor.expression, labels, donor.sizes),
                columns,
                spec.complex_aggregation,
            )
            permuted += _specificity(means, ligand, receptor)
        exceedances += permuted >= threshold
    return (1 + exceedances) / (1 + n_perm)


def _condition_effect(
    values: _Floats, matrix: _Floats
) -> tuple[float | None, float | None]:
    projection = np.linalg.pinv(matrix)
    coefficients = projection @ values
    residuals = values - matrix @ coefficients
    df = len(values) - matrix.shape[1]
    scale = np.max(np.abs(values))
    if np.linalg.norm(residuals) <= np.finfo(float).eps * scale * len(values):
        return None, None
    standard_error = (
        np.linalg.norm(residuals) * np.linalg.norm(projection[1]) / np.sqrt(df)
    )
    if not np.isfinite(standard_error) or standard_error <= 0:
        return None, None
    effect = float(coefficients[1])
    p = float(2 * t.sf(abs(effect / float(standard_error)), df))
    return (effect, p) if np.isfinite(effect) and 0 <= p <= 1 else (None, None)


def _pair_designs(
    support: NDArray[np.bool_],
    full_design: _Floats,
) -> dict[tuple[int, int], tuple[_Ints, _Floats] | str]:
    pairs: dict[tuple[int, int], tuple[_Ints, _Floats] | str] = {}
    for sender, receiver in np.ndindex((support.shape[1], support.shape[1])):
        indexes = np.flatnonzero(support[:, sender] & support[:, receiver])
        matrix = full_design[indexes]
        numerator = np.count_nonzero(matrix[:, 1])
        if min(numerator, len(indexes) - numerator) < 3:
            pairs[sender, receiver] = "at_least_three_donors_per_condition_required"
            continue
        try:
            diagnostics = require_design_condition(matrix)
            if diagnostics["residual_df"] <= 0:
                raise ValueError("insufficient_donor_residual_degrees_of_freedom")
        except ValueError as error:
            pairs[sender, receiver] = str(error).split(":", 1)[0]
        else:
            pairs[sender, receiver] = (indexes, matrix)
    return pairs


def _differential_statistics(
    scores: _Floats,
    design: tuple[_Ints, _Floats] | str,
) -> dict[str, float | str | None]:
    if isinstance(design, str):
        return {"missing_reason": design}
    indexes, matrix = design
    effect, p_value = _condition_effect(scores[indexes], matrix)
    return {
        "effect": effect,
        "p_value": p_value,
        "missing_reason": "donor_residual_variance_unestimable"
        if p_value is None
        else None,
    }


def _interaction_statistics(
    donors: list[_Donor],
    cell_types: list[str],
    genes: pd.Index,
    reference: dict[str, tuple[_Complex, _Complex]],
    full_design: _Floats | None,
    spec: CellCommunicationSpec,
) -> tuple[list[dict[str, Any]], int, int]:
    n_types = len(cell_types)
    measured = {
        name: pair
        for name, pair in reference.items()
        if all(gene in genes for side in pair for gene in side)
    }
    pair_shape = (len(measured), n_types, n_types)
    if len(measured) * n_types**2 * len(donors) > MAX_MATRIX_ENTRIES:
        raise ValueError("communication_interaction_family_over_budget")
    complexes = {
        side: index
        for index, side in enumerate(
            dict.fromkeys(side for pair in measured.values() for side in pair)
        )
    }
    columns = tuple(genes.get_indexer(side) for side in complexes)
    ligand = np.array([complexes[side] for side, _ in measured.values()], dtype=np.intp)
    receptor = np.array(
        [complexes[side] for _, side in measured.values()], dtype=np.intp
    )
    support = np.array([donor.sizes > 0 for donor in donors])
    counts = support.astype(int).T @ support
    means = [
        _complex_means(
            _group_means(d.expression, d.labels, d.sizes),
            columns,
            spec.complex_aggregation,
        )
        for d in donors
    ]
    fractions = [_complex_fractions(donor, columns) for donor in donors]
    subunits_detected = np.asarray(
        [
            [detection[genes].all() for genes in columns]
            for detection in (donor.expression.any(axis=0) for donor in donors)
        ],
        dtype=bool,
    )
    magnitude = np.zeros((len(donors), *pair_shape))
    specificity = np.zeros(pair_shape)
    for donor_index, values in enumerate(means):
        magnitude[donor_index] = values.T[ligand, :, None] * values.T[receptor, None, :]
        specificity += _specificity(values, ligand, receptor)

    family_size = int(np.prod(pair_shape))
    # Reach the first BH threshold at 0.05 within the automatic work
    # budget. Larger families can explicitly request a finer resolution.
    n_perm = (
        max(spec.n_perm, min(10_000, 20 * family_size))
        if measured and spec.contrast is None
        else 0
    )
    probabilities = (
        _permutation_probabilities(
            donors, columns, ligand, receptor, specificity, spec, n_perm
        )
        if n_perm
        else None
    )
    designs = _pair_designs(support, full_design) if full_design is not None else {}
    measured_index = {name: idx for idx, name in enumerate(measured)}
    rows: list[dict[str, Any]] = []
    for name in reference:
        idx = measured_index.get(name)
        for sender, receiver in np.ndindex((n_types, n_types)):
            row: dict[str, Any] = {
                "interaction_id": name,
                "sender": cell_types[sender],
                "receiver": cell_types[receiver],
                "donors": int(counts[sender, receiver]),
            }
            if idx is None:
                row["missing_reason"] = "reference_subunit_unmeasured"
            elif not row["donors"]:
                row["missing_reason"] = "no_donor_has_both_eligible_types"
            else:
                contributors = np.flatnonzero(support[:, sender] & support[:, receiver])
                scores = magnitude[:, idx, sender, receiver]
                row.update(
                    ligand_mean_expression=float(
                        np.mean([means[i][sender, ligand[idx]] for i in contributors])
                    ),
                    receptor_mean_expression=float(
                        np.mean(
                            [means[i][receiver, receptor[idx]] for i in contributors]
                        )
                    ),
                    ligand_expressed_fraction=float(
                        np.mean(
                            [fractions[i][sender, ligand[idx]] for i in contributors]
                        )
                    ),
                    receptor_expressed_fraction=float(
                        np.mean(
                            [
                                fractions[i][receiver, receptor[idx]]
                                for i in contributors
                            ]
                        )
                    ),
                    interaction_magnitude=float(scores[contributors].mean()),
                )
                detected = (
                    subunits_detected[contributors, ligand[idx]]
                    & subunits_detected[contributors, receptor[idx]]
                )
                if not detected.any():
                    row["missing_reason"] = "no_donor_has_both_complexes_detected"
                else:
                    row["specificity"] = float(
                        specificity[idx, sender, receiver] / len(contributors)
                    )
                    if probabilities is not None:
                        row["p_value"] = float(probabilities[idx, sender, receiver])
                    else:
                        row.update(
                            _differential_statistics(scores, designs[sender, receiver])
                        )
            rows.append(row)
    family = [row for row in rows if row["interaction_id"] in measured]
    for row, q in zip(
        family,
        adjust_probabilities([row.get("p_value") for row in family]),
        strict=True,
    ):
        row["q_value"] = q
    return (
        [CommunicationStatistic(**row).model_dump(mode="json") for row in rows],
        family_size,
        n_perm,
    )


def fit_cell_communication(
    expression_path: Path,
    metadata_path: Path,
    ligand_receptor_path: Path | None,
    spec: CellCommunicationSpec,
) -> dict[str, Any]:
    """Test coexpression on digest-pinned CSVs with one row per cell.

    Expression columns are cell_id and unique gene identifiers. Metadata has
    cell_id, donor_id and cell_type plus condition/covariates when required.
    The reference has interaction_id, ligand and receptor columns, with pipe-
    separated subunit gene identifiers. Missing subunits stay in the BH family
    as unestimable tests. The expressed fraction counts cells with all subunits
    detected. The input scale is declared, with a numeric nonnegativity check.
    """
    spec = CellCommunicationSpec.model_validate(spec.model_dump())
    reference = _read_reference(ligand_receptor_path, spec.ligand_receptor_sha256)
    frame = indexed_rows(
        read_solver_csv(expression_path, spec.expression_sha256), "cell_id"
    )
    metadata = indexed_rows(
        read_solver_csv(metadata_path, spec.metadata_sha256), "cell_id"
    )
    if set(frame.index) != set(metadata.index):
        raise ValueError("expression_and_metadata_cell_ids_must_match")
    require_labels(metadata, ("donor_id", "cell_type"))
    metadata = _select_condition(metadata.loc[frame.index], spec)
    expression = numeric_matrix(frame)
    if (expression < 0).any():
        raise ValueError("nonnegative_normalized_expression_required")
    # Keep subsequent sums of expression products and their squared residuals finite.
    if expression.max() > 1e60:
        raise ValueError("normalized_expression_values_outside_numerical_range")
    full_design, diagnostics = None, None
    if spec.contrast:
        _, full_design, diagnostics = donor_design(metadata, spec.contrast)
    cell_types = sorted(set(metadata["cell_type"]))
    if (
        len(reference) * len(cell_types) ** 2 > MAX_MATRIX_ENTRIES // 32
        or metadata["donor_id"].nunique() * len(cell_types) > MAX_MATRIX_ENTRIES
    ):
        raise ValueError("communication_interaction_family_over_budget")
    relevant = {gene for pair in reference.values() for side in pair for gene in side}
    feature_mask = frame.columns.isin(relevant)
    genes = frame.columns[feature_mask]
    donors = []
    for name, cells in metadata.groupby("donor_id", sort=True):
        labels = np.searchsorted(cell_types, cells["cell_type"])
        sizes = np.bincount(labels, minlength=len(cell_types))
        eligible = sizes >= spec.min_cells_per_donor_type
        if spec.contrast is None and eligible.sum() < 2:
            raise ValueError("donors_require_two_eligible_cell_types_for_specificity")
        keep = eligible[labels]
        cell_indexes = frame.index.get_indexer(cells.index[keep])
        values = expression[np.ix_(cell_indexes, feature_mask)]
        donors.append(
            _Donor(str(name), values, labels[keep], np.where(eligible, sizes, 0))
        )

    rows, family_size, n_perm = _interaction_statistics(
        donors, cell_types, genes, reference, full_design, spec
    )
    specification = spec.model_dump(mode="json")
    receipt = {
        "format": "devbench.scrna-ccc-result/1",
        "specification": specification,
        "specification_sha256": content_sha256(specification),
        "input_sha256s": {
            "expression": spec.expression_sha256,
            "metadata": spec.metadata_sha256,
            "ligand_receptor": spec.ligand_receptor_sha256,
        },
        "status": "completed"
        if any(row["p_value"] is not None for row in rows)
        else "no_estimable_interactions",
        "claim_scope": "correlational_coexpression",
        "clinical_release_allowed": False,
        "n_cells": len(metadata),
        "n_donors": len(donors),
        "conditions_observed": sorted(set(metadata["condition"]))
        if "condition" in metadata
        else [],
        "donor_type_cell_counts_used": {
            donor.name: dict(zip(cell_types, donor.sizes.tolist(), strict=True))
            for donor in donors
        },
        "design_diagnostics": diagnostics,
        "bh_family_size": family_size,
        "n_permutations_used": n_perm,
        "interaction_statistics": rows,
        "score_definition": (
            "Magnitude is the equal-donor mean of the product of ligand and receptor "
            "complex expression. Specificity divides each complex expression by its "
            "sum across eligible types in that donor before taking the product. "
            "An undetected complex contributes zero. A pair is untestable if no "
            "contributing donor detects every subunit across its eligible cells."
        ),
        "test_definition": (
            "Within-donor cell-type label permutations among eligible cells test "
            "within-dataset cell-type specificity. The test assumes exchangeability "
            "within donors. Unmeasured reference pairs are excluded from BH. "
            "The automatic resolution increase uses at most 10,000 draws. Larger "
            "permutation counts can be requested for larger measured families."
            if spec.contrast is None
            else "Two-sided donor-level linear-model t test on interaction magnitude, "
            "assuming independent donor errors with common variance. Unmeasured "
            "reference pairs are excluded from BH."
        ),
    }
    return {**receipt, "receipt_sha256": canonical_sha256(receipt)}
