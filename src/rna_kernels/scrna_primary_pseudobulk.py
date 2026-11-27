"""Raw R1 donor pseudobulk with the existing, locked edgeR v4 QL driver."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from devbench.analysis.singlecell import validate_counts

from .canonical import canonical_json
from .mounted_model import _read_result
from .scrna_qc_audit import SolverUnavailable, run_r
from .scrna_upstream_contract import MAX_MATRIX_BYTES


def pseudobulks(source, adjusted, donors, groups):
    """Sum each original row once into an aligned donor-by-condition profile."""
    keys = pd.MultiIndex.from_arrays([donors, groups])
    codes, units = pd.factorize(keys, sort=True)
    if len(units) * source.shape[1] * 64 > MAX_MATRIX_BYTES:
        raise ValueError("sensitivity_pseudobulk_allocation_bound")
    raw = np.zeros((len(units), source.shape[1]), dtype=np.float64)
    corrected = np.zeros_like(raw)
    start = 0
    for block in source.iter_rows():
        validate_counts(block.X)
        end = start + block.n_obs
        positions = codes[start:end]
        for code in np.unique(positions):
            take = np.where(positions == code)[0]
            raw[code] += np.asarray(block.X[take].sum(axis=0, dtype=np.float64)).ravel()
            corrected[code] += np.asarray(
                adjusted[take + start].sum(axis=0, dtype=np.float64)
            ).ravel()
        start = end
    # Every positive integer addition below 2**53 is exact in float64.
    if np.any(raw >= 2**53) or np.any(raw.sum(axis=1) >= 2**53):
        raise ValueError("pseudobulk_raw_library_exceeds_exact_integer_range")
    return list(units), raw, corrected


def primary_design(units, denominator, numerator):
    donors, labels = map(np.asarray, zip(*units, strict=True))
    if min(len(set(donors[labels == group])) for group in (denominator, numerator)) < 3:
        return None, None, "fewer_than_three_donors_per_condition"
    identity, n = np.unique(donors, return_counts=True)
    repeated = n.max() > 1
    if repeated and not np.all(n == 2):
        return None, None, "incomplete_donor_pairs"
    ncols = len(identity) + 1 if repeated else 2
    if ncols >= len(units) or ncols * len(units) * 32 > MAX_MATRIX_BYTES:
        return None, None, "non_estimable_donor_design"
    cols = [np.ones(len(labels)), (labels == numerator)]
    if repeated:
        cols.extend(donors == donor for donor in identity[1:])
    design = np.column_stack(cols).astype("<f8")
    if (
        np.linalg.matrix_rank(design) != design.shape[1]
        or np.linalg.cond(design) > 1000
    ):
        return None, None, "non_estimable_donor_design"
    return design, "paired_fixed" if repeated else "independent", None


def _verify_bh(p, q, codes):
    # The reviewed driver retains failed eligible tests at p=1 in its BH family.
    from scipy.stats import false_discovery_control

    eligible = codes != 1
    estimated = codes[eligible] == 0
    if estimated.any():
        padded = np.where(estimated, p[eligible], 1.0)
        expected = false_discovery_control(padded, method="bh")
        if not np.allclose(
            q[eligible][estimated], expected[estimated], rtol=1e-10, atol=1e-15
        ):
            raise ValueError("pseudobulk_BH_family_mismatch")


def raw_primary(
    raw, units, genes, denominator, numerator, scratch, admitted, capture_confounded
):
    identity = dict(
        representation_class="count_1_raw_counts",
        evidence_level="L1",
        scientific_scope="whole_cohort_declared_donor_condition_pseudobulks",
        donor_identity_basis="declared; expression cannot prove independence",
        numerator=numerator,
        denominator=denominator,
        calibrated=False,
    )
    if not admitted:
        return {}, dict(
            **identity,
            status="not_evaluable",
            reason="primary_count_or_forensic_gate_held",
        )
    design, model, reason = primary_design(units, denominator, numerator)
    if reason or capture_confounded:
        return {}, dict(
            **identity,
            status="not_evaluable",
            reason=reason or "capture_confounded_with_condition",
        )
    profile = raw.T.astype("<f8", order="C")
    config = dict(
        genes=len(genes),
        samples=len(units),
        columns=design.shape[1],
        gene_ids=list(genes),
        unit_ids=[u[0] for u in units],
        labels=[u[1] for u in units],
        contrast=[0, 1] + [0] * (design.shape[1] - 2),
        reduced_columns=0,
        comparison="coefficient",
        measurement="raw_counts",
        units=model,
        solver="edger_v4",
        test="qlf",
        normalization="TMM",
        filter_mode="filterByExpr",
        abundance_trend=None,
        calibration_status="uncalibrated_L1",
        regime_route="m2_raw_pseudobulk",
        numerator=numerator,
        denominator=denominator,
    )
    cfg = canonical_json(config)
    identity.update(
        solver="edger_v4",
        test="qlf",
        normalization="TMM",
        adjustment="BH",
        design=model,
        observations=len(units),
        biological_units=len(set(config["unit_ids"])),
        configuration_sha256=hashlib.sha256(cfg).hexdigest(),
        design_sha256=hashlib.sha256(design.tobytes()).hexdigest(),
        counts_sha256=hashlib.sha256(profile.tobytes()).hexdigest(),
    )
    with tempfile.TemporaryDirectory(dir=scratch) as name:
        root = Path(name)
        profile.tofile(root / "counts.bin")
        design.tofile(root / "design.bin")
        (root / "model.json").write_bytes(cfg)
        try:
            run_r("mounted_general.R", root, root)
        except SolverUnavailable as error:
            return {}, dict(**identity, status="failed", reason=str(error))
        effect, p, q, codes, evidence = _read_result(root, len(genes), False)
    details = evidence["solver_details"]
    if (
        evidence["solver"] != "edger_v4"
        or evidence["test"] != "qlf"
        or not evidence["solver_version"].startswith("4.")
        or details["normalization"] != "TMM"
        or details["adjustment"] != "BH"
        or details["filter"] != "filterByExpr"
    ):
        raise ValueError("pseudobulk_frozen_method_mismatch")
    _verify_bh(p, q, codes)
    status = np.where(
        codes == 0, "estimated", np.where(codes == 1, "filtered", "failed")
    )
    estimates = dict(
        raw_log2_fold_change=effect,
        raw_p_value=p,
        raw_q_value=q,
        raw_estimate_status=status,
    )
    return estimates, dict(
        **identity,
        status="completed" if np.any(codes == 0) else "no_estimable_genes",
        estimated_genes=int(sum(codes == 0)),
        failed_genes=int(sum(codes == 3)),
        filtered_genes=int(sum(codes == 1)),
        solver_version=evidence["solver_version"],
    )
