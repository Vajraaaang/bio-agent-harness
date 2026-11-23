"""Synthetic tests for interaction specificity and donor-level contrasts."""

from hashlib import sha256
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from devbench.core._hashing import canonical_sha256
from devbench.core._rna_solver_inputs import ConditionContrast
from devbench.core.scrna_ccc import CellCommunicationSpec, fit_cell_communication


def _fixture(
    tmp_path: Path,
    *,
    null: bool = False,
    negative: bool = False,
    degenerate: bool = False,
    differential: bool = False,
    confounded: bool = False,
):
    expression, metadata = [], []
    for donor in range(8):
        condition = "control" if donor < 4 else "treated"
        labels = (
            ("sender", "receiver") if not (degenerate and donor == 0) else ("sender",)
        )
        for label in labels:
            # Cell counts differ across donors. Each donor still contributes once.
            for cell in range(6 + 2 * donor):
                cell_id = f"d{donor}-{label}-{cell}"
                signal = (1.2 + 0.09 * (donor % 4)) * (
                    2 if condition == "treated" else 1
                )
                roles = (
                    signal if differential else 4.0,
                    signal if differential else 2.0,
                    3.0,
                )
                ligand = roles[0] if label == "sender" else 0.0
                subunit = roles[1] if label == "sender" else 0.0
                receptor = roles[2] if label == "receiver" else 0.0
                values = (1.0, 1.0, 1.0) if null else (ligand, subunit, receptor)
                expression.append((cell_id, *values))
                metadata.append(
                    (
                        cell_id,
                        f"d{donor}",
                        label,
                        condition if differential else "one",
                        condition,
                    )
                )
    expr = pd.DataFrame(expression, columns=["cell_id", "L1", "L2", "R"])
    if negative:
        expr.loc[0, "L1"] = -1.0
    meta = pd.DataFrame(
        metadata, columns=["cell_id", "donor_id", "cell_type", "condition", "batch"]
    )
    if not confounded:
        meta = meta.drop(columns=["batch"])
    ref = pd.DataFrame(
        [("pair", "L1|L2", "R"), ("missing", "unknown", "R")],
        columns=["interaction_id", "ligand", "receptor"],
    )
    paths = tuple(
        tmp_path / f"{name}.csv" for name in ("expression", "metadata", "reference")
    )
    for frame, path in zip((expr, meta, ref), paths, strict=True):
        frame.to_csv(path, index=False)
    expr_sha, meta_sha, ref_sha = (
        sha256(path.read_bytes()).hexdigest() for path in paths
    )
    spec = CellCommunicationSpec(
        expression_sha256=expr_sha,
        metadata_sha256=meta_sha,
        ligand_receptor_sha256=ref_sha,
        mode="differential_condition"
        if differential
        else "within_condition_specificity",
        normalization="library_size_log1p",
        n_perm=199,
        seed=17,
        min_cells_per_donor_type=5,
        contrast=ConditionContrast(
            numerator="treated",
            denominator="control",
            technical_factors=("batch",) if confounded else (),
        )
        if differential
        else None,
    )
    return paths, spec


def _pair(receipt, interaction="pair"):
    return next(
        row
        for row in receipt["interaction_statistics"]
        if (row["interaction_id"], row["sender"], row["receiver"])
        == (interaction, "sender", "receiver")
    )


@pytest.mark.parametrize(
    ("method", "magnitude"), [("min", 6.0), ("geometric_mean", 3 * np.sqrt(8))]
)
def test_planted_interaction_and_complex(tmp_path, method, magnitude):
    paths, spec = _fixture(tmp_path)
    spec = spec.model_copy(update={"complex_aggregation": method})
    result = fit_cell_communication(*paths, spec)
    assert result == fit_cell_communication(*paths, spec)
    assert result["receipt_sha256"] == canonical_sha256(
        {key: value for key, value in result.items() if key != "receipt_sha256"}
    )
    assert result["claim_scope"] == "correlational_coexpression"
    assert result["clinical_release_allowed"] is False
    pair = _pair(result)
    assert pair["interaction_magnitude"] == pytest.approx(magnitude)
    assert pair["donors"] == 8
    assert (
        pair["ligand_expressed_fraction"] == pair["receptor_expressed_fraction"] == 1.0
    )
    assert pair["p_value"] == 1 / 200
    assert pair["p_value"] <= pair["q_value"] < 0.05
    unmeasured = _pair(result, "missing")
    assert unmeasured["p_value"] is unmeasured["q_value"] is None


def test_null_interaction_is_nonspecific(tmp_path):
    paths, spec = _fixture(tmp_path, null=True)
    result = fit_cell_communication(*paths, spec)
    pair = _pair(result)
    assert pair["specificity"] == 0.25
    assert pair["p_value"] == pair["q_value"] == 1.0


def test_measured_but_undetected_complex_has_no_test(tmp_path):
    paths, spec = _fixture(tmp_path)
    expression, _, _ = paths
    frame = pd.read_csv(expression)
    frame["L2"] = 0
    frame.to_csv(expression, index=False)
    spec = spec.model_copy(
        update={"expression_sha256": sha256(expression.read_bytes()).hexdigest()}
    )
    pair = _pair(fit_cell_communication(*paths, spec))
    assert pair["interaction_magnitude"] == 0.0
    assert pair["specificity"] is pair["p_value"] is pair["q_value"] is None
    assert pair["missing_reason"] == "no_donor_has_both_complexes_detected"


def test_differential_condition_uses_donor_scores(tmp_path):
    paths, spec = _fixture(tmp_path, differential=True)
    pair = _pair(fit_cell_communication(*paths, spec))
    assert pair["donors"] == 8
    assert pair["effect"] == pytest.approx(3 * (1.2 + 0.09 * 1.5))
    assert 0 <= pair["p_value"] <= pair["q_value"] < 0.05


def test_confounded_differential_condition_refused(tmp_path):
    paths, spec = _fixture(tmp_path, differential=True, confounded=True)
    with pytest.raises(ValueError, match="rank_deficient_design"):
        fit_cell_communication(*paths, spec)


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        ("negative", "nonnegative_normalized_expression"),
        ("degenerate", "two_eligible_cell_types"),
        ("missing_reference", "ligand_receptor_reference_required"),
        ("digest_mismatch", "digest_mismatch"),
        ("missing_donor", "missing_or_invalid_labels"),
    ],
)
def test_refusals(tmp_path, scenario, message):
    paths, spec = _fixture(
        tmp_path, **{scenario: True} if scenario in {"negative", "degenerate"} else {}
    )
    expression, metadata, reference = paths
    if scenario == "missing_reference":
        reference = None
    if scenario == "digest_mismatch":
        expression.write_text(expression.read_text() + "\n")
    if scenario == "missing_donor":
        frame = pd.read_csv(metadata, dtype=str)
        frame.loc[0, "donor_id"] = ""
        frame.to_csv(metadata, index=False)
        spec = spec.model_copy(
            update={"metadata_sha256": sha256(metadata.read_bytes()).hexdigest()}
        )
    with pytest.raises(ValueError, match=message):
        fit_cell_communication(expression, metadata, reference, spec)
