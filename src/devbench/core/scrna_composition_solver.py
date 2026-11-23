"""Locked scCODA estimation for independent-unit cell compositions."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

from .scrna_composition import (
    CompositionEffect,
    CompositionInput,
    CompositionResult,
    CompositionSpec,
    SamplingDiagnostics,
    ScCODARuntime,
    _posterior_fdr,
)

if TYPE_CHECKING:
    from pandas import DataFrame


def run_pertpy(
    data: CompositionInput,
    spec: CompositionSpec,
    runtime: ScCODARuntime,
    base: CompositionResult,
) -> CompositionResult:
    """Fit only the exact installed package profile and retain explicit failures."""
    result = base.model_dump()
    result["runtime"] = runtime
    try:
        if any(version(name) != v for name, v in runtime.packages().items()):
            return CompositionResult(**(result | {"reason": "scCODA_version_mismatch"}))
        posterior, groups, features, diagnostics = _fit(data, spec)
        result["sampling"] = diagnostics
        result["zero_replacement"] = 0.5 if base.measured_zero_count else None
        if diagnostics.divergent_transitions:
            return _failed(result, "scCODA_divergent_sampling")
        if not diagnostics.mean_acceptance_probability:
            return _failed(result, "scCODA_sampler_stalled")
        effects = _effect_rows(posterior, groups, features, spec.reference_cell_type)
        observed_fdr = _posterior_fdr(effects)
        if observed_fdr is not None and observed_fdr > spec.target_est_fdr + 1e-12:
            return _failed(result, "scCODA_fdr_target_exceeded")
        return CompositionResult(
            **(
                result
                | {
                    "status": "succeeded",
                    "reason": None,
                    "effects": effects,
                    "est_fdr": observed_fdr,
                }
            )
        )
    except (PackageNotFoundError, ImportError, OSError):
        return CompositionResult(
            **(result | {"reason": "scCODA_dependency_unavailable"})
        )
    except (
        ArithmeticError,
        AttributeError,
        LookupError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as error:
        return _failed(result, f"scCODA_fit_failed_{type(error).__name__}")


def _failed(result: dict, reason: str) -> CompositionResult:
    return CompositionResult(**(result | {"status": "failed", "reason": reason}))


def _fit(
    data: CompositionInput, spec: CompositionSpec
) -> tuple[DataFrame, dict[str, str], dict[str, str], SamplingDiagnostics]:
    import numpy as np
    import pandas as pd
    from anndata import AnnData
    from pertpy.tools import Sccoda

    conditions = sorted(
        {sample.condition for sample in data.samples} - {spec.baseline_condition}
    )
    groups = {f"condition_{idx}": name for idx, name in enumerate(conditions)}
    features = {f"cell_type_{idx}": cell for idx, cell in enumerate(data.cell_types)}
    reference = next(
        key for key, cell in features.items() if cell == spec.reference_cell_type
    )
    metadata = pd.DataFrame(
        {
            key: [int(sample.condition == level) for sample in data.samples]
            for key, level in groups.items()
        },
        index=[sample.biological_unit_id for sample in data.samples],
    )
    dataset = AnnData(
        X=np.array([sample.counts for sample in data.samples], dtype=np.int64),
        obs=metadata,
        var=pd.DataFrame(index=list(features)),
    )
    model = Sccoda()
    prepared = model.prepare(
        dataset, formula=" + ".join(groups), reference_cell_type=reference
    )
    model.run_nuts(
        prepared,
        num_warmup=spec.num_warmup,
        num_samples=spec.num_samples,
        rng_key=spec.seed,
        target_accept_prob=spec.target_accept_probability,
    )
    diagnostics = _diagnostics(model.mcmc.get_extra_fields(), spec.num_samples)
    model.set_fdr(prepared, spec.target_est_fdr)
    return model.get_effect_df(prepared), groups, features, diagnostics


def _diagnostics(fields: dict, expected_draws: int) -> SamplingDiagnostics:
    import numpy as np

    diverging = np.asarray(fields["diverging"])
    acceptance = np.asarray(fields["accept_prob"], dtype=float)
    if (
        diverging.dtype.kind != "b"
        or diverging.shape != (expected_draws,)
        or acceptance.shape != diverging.shape
        or not np.isfinite(acceptance).all()
        or np.any((acceptance < 0) | (acceptance > 1))
    ):
        raise ValueError("scCODA returned invalid NUTS diagnostics")
    return SamplingDiagnostics(
        draws=expected_draws,
        divergent_transitions=int(diverging.sum()),
        mean_acceptance_probability=float(acceptance.mean()),
    )


def _effect_rows(
    table: DataFrame, groups: dict[str, str], features: dict[str, str], reference: str
) -> tuple[CompositionEffect, ...]:
    expected = {(group, feature) for group in groups for feature in features}
    if not table.index.is_unique or set(table.index) != expected:
        raise ValueError("scCODA result axes differ from the frozen contrast family")
    effects = []
    for group, condition in groups.items():
        for feature, cell_type in features.items():
            row = table.loc[(group, feature), :]
            coefficient = float(row["Final Parameter"])
            inclusion = float(row["Inclusion probability"])
            if cell_type == reference:
                if coefficient != 0 or inclusion != 0:
                    raise ValueError("scCODA altered the frozen reference coefficient")
                continue
            effects.append(
                CompositionEffect(
                    condition=condition,
                    cell_type=cell_type,
                    posterior_inclusion_probability=inclusion,
                    credible_effect=coefficient != 0,
                    relative_log_coefficient=coefficient or None,
                )
            )
    return tuple(effects)
