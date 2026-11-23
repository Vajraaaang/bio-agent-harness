"""Independent-unit cell composition contracts for scCODA."""

import os
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from math import fsum, isclose
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from rna_kernels.canonical import canonical_json

from ._files import hash_bounded_file, read_bounded_file
from ._hashing import canonical_sha256
from .scientific_types import FrozenContract
from .scrna_qc_selection import AcceptedQCSelection

MAX_COMPOSITION_BYTES = 4_000_000
MAX_COMPOSITION_PROFILES = 4096
MAX_COMPOSITION_CELL_TYPES = 128
_MAX_POSTERIOR_SAMPLE_CELLS = 8_000_000
_CLOSURE = (
    "Effects describe relative composition among captured and labeled cells "
    "against the selected reference. An increase in one category changes "
    "the other fractions under closure. Absolute abundance needs separate "
    "measurements."
)
Label = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ImageID = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Count = Annotated[int, Field(ge=0, le=2**31 - 1)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]
NontrivialProbability = Annotated[float, Field(gt=0.0, lt=1.0)]
ResultStatus = Literal["succeeded", "refused", "unavailable", "failed"]


class CompositionSample(FrozenContract):
    biological_unit_id: Label
    condition: Label
    cells_observed: Annotated[int, Field(gt=0, le=2**31 - 1)]
    counts: Annotated[
        tuple[Count, ...], Field(min_length=1, max_length=MAX_COMPOSITION_CELL_TYPES)
    ]


class CompositionInput(FrozenContract):
    """Counts of labeled cells, aligned to one common measured cell-type axis."""

    format: Literal["devbench.scrna.composition-input/1"] = (
        "devbench.scrna.composition-input/1"
    )
    source_sha256: Digest
    cell_label_source_sha256: Digest
    biological_unit_mapping_sha256: Digest
    biological_unit: Label
    unit_identity_status: Literal["declared_independent"] = "declared_independent"
    cell_types: Annotated[
        tuple[Label, ...], Field(min_length=2, max_length=MAX_COMPOSITION_CELL_TYPES)
    ]
    samples: Annotated[
        tuple[CompositionSample, ...],
        Field(min_length=1, max_length=MAX_COMPOSITION_PROFILES),
    ]


class CompositionObservationSpec(FrozenContract):
    source_sha256: Digest
    biological_unit: Label
    biological_unit_column: Label
    condition_column: Label
    cell_type_column: Label

    @model_validator(mode="after")
    def check_observation_roles(self) -> "CompositionObservationSpec":
        if (
            len(
                {
                    self.biological_unit_column,
                    self.condition_column,
                    self.cell_type_column,
                }
            )
            != 3
        ):
            raise ValueError("Composition observation roles require distinct columns")
        return self


def composition_from_h5ad(
    path: Path,
    declaration: CompositionObservationSpec,
    *,
    qc_selection: AcceptedQCSelection | None = None,
) -> CompositionInput:
    """Count the complete observed cell-label axis using bounded metadata reads."""
    from .scrna_composition_data import read_observed_composition

    return read_observed_composition(path, declaration, qc_selection=qc_selection)


class CompositionSpec(FrozenContract):
    reference_cell_type: Label
    baseline_condition: Label
    target_est_fdr: NontrivialProbability
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)]
    num_warmup: Annotated[int, Field(ge=100, le=5_000)] = 1_000
    num_samples: Annotated[int, Field(ge=200, le=10_000)] = 1_000
    target_accept_probability: Annotated[float, Field(ge=0.8, le=0.99)] = 0.9


class ScCODARuntime(FrozenContract):
    """Package profile bound to an execution image by the coordinator."""

    image_id: ImageID
    pertpy_version: Literal["1.5.0"] = "1.5.0"
    numpyro_version: Literal["0.21.0"] = "0.21.0"
    jax_version: Label
    jaxlib_version: Label
    arviz_version: Label
    anndata_version: Label

    def packages(self) -> dict[str, str]:
        return {
            "pertpy": self.pertpy_version,
            "numpyro": self.numpyro_version,
            "jax": self.jax_version,
            "jaxlib": self.jaxlib_version,
            "arviz": self.arviz_version,
            "anndata": self.anndata_version,
        }


class CompositionEffect(FrozenContract):
    condition: Label
    cell_type: Label
    posterior_inclusion_probability: Probability
    credible_effect: bool
    relative_log_coefficient: float | None

    @model_validator(mode="after")
    def check_reported_effect(self) -> "CompositionEffect":
        if self.credible_effect != (self.relative_log_coefficient not in (None, 0.0)):
            raise ValueError("Only selected effects carry a nonzero coefficient")
        if self.relative_log_coefficient == 0:
            raise ValueError("A suppressed coefficient must be null")
        return self


class SamplingDiagnostics(FrozenContract):
    algorithm: Literal["NUTS"] = "NUTS"
    chains: Literal[1] = 1
    draws: Annotated[int, Field(ge=1)]
    divergent_transitions: Count
    mean_acceptance_probability: Probability
    between_chain_convergence: Literal["unavailable_single_chain"] = (
        "unavailable_single_chain"
    )


def _posterior_fdr(effects: tuple[CompositionEffect, ...]) -> float | None:
    selected = [
        1.0 - row.posterior_inclusion_probability
        for row in effects
        if row.credible_effect
    ]
    return fsum(selected) / len(selected) if selected else None


class CompositionResult(FrozenContract):
    """Bayesian effect family with separate custody and estimation diagnostics."""

    format: Literal["devbench.scrna.sccoda/1"] = "devbench.scrna.sccoda/1"
    method: Literal["scCODA"] = "scCODA"
    inference_scope: Literal["bayesian_relative_cell_type_composition"] = (
        "bayesian_relative_cell_type_composition"
    )
    design_scope: Literal["independent_unit_condition_only"] = (
        "independent_unit_condition_only"
    )
    status: ResultStatus
    reason: str | None
    input_sha256: Digest
    spec_sha256: Digest
    reference_cell_type: Label
    baseline_condition: Label
    condition_biological_units: dict[Label, Count]
    target_est_fdr: NontrivialProbability
    est_fdr: Probability | None = Field(
        default=None,
        description="Posterior expected false discovery proportion among selected "
        "effects across tested conditions and nonreference cell types.",
    )
    effects: tuple[CompositionEffect, ...] = ()
    measured_zero_count: Count | None = None
    zero_replacement: Annotated[float, Field(ge=0.5, le=0.5)] | None = None
    sampling: SamplingDiagnostics | None = None
    runtime: ScCODARuntime | None = None
    closure_note: str = _CLOSURE

    @field_validator("closure_note")
    @classmethod
    def check_closure_note(cls, text: str) -> str:
        if text != _CLOSURE:
            raise ValueError("Expected the registered compositional closure note")
        return text

    @model_validator(mode="after")
    def check_estimation_status(self) -> "CompositionResult":
        if self.status == "succeeded":
            self._assert_complete_fit()
        elif self.effects or self.est_fdr is not None or not self.reason:
            raise ValueError("A blocked result requires a reason and no effects")
        return self

    def _assert_complete_fit(self) -> None:
        if (
            self.reason
            or not self.effects
            or self.runtime is None
            or self.sampling is None
        ):
            raise ValueError("A completed fit requires posterior and runtime evidence")
        if self.sampling.divergent_transitions:
            raise ValueError("Divergent posterior sampling cannot publish effects")
        actual = _posterior_fdr(self.effects)
        if actual is None:
            if self.est_fdr is not None:
                raise ValueError("An empty credible family has no selected-effect FDR")
            return
        if self.est_fdr is None or not isclose(
            self.est_fdr, actual, rel_tol=0.0, abs_tol=1e-12
        ):
            raise ValueError("Estimated FDR must match the posterior inclusions")
        if actual > self.target_est_fdr + 1e-12:
            raise ValueError("Estimated selection FDR exceeds the declared cutoff")


@dataclass(frozen=True)
class _CompositionSupport:
    units_by_condition: dict[str, int]
    reason: str | None = None
    measured_zeros: int | None = None


def _audit_composition(
    data: CompositionInput, spec: CompositionSpec
) -> _CompositionSupport:
    donors: dict[str, set[str]] = {}
    for sample in data.samples:
        donors.setdefault(sample.condition, set()).add(sample.biological_unit_id)
    units = {condition: len(ids) for condition, ids in sorted(donors.items())}
    reason = _unit_admission(data, spec, units)
    if reason is None:
        reason = _count_admission(data, spec)
    if reason is not None:
        return _CompositionSupport(units, reason)
    zeros = sum(count == 0 for sample in data.samples for count in sample.counts)
    return _CompositionSupport(units, measured_zeros=zeros)


def _unit_admission(
    data: CompositionInput, spec: CompositionSpec, units: dict[str, int]
) -> str | None:
    if len({sample.biological_unit_id for sample in data.samples}) != len(data.samples):
        return "repeated_biological_unit"
    if len(units) < 2 or len(units) > 32 or spec.baseline_condition not in units:
        return "unsupported_condition_design"
    if min(units.values()) < 3:
        return "insufficient_biological_units"
    if len(data.samples) * len(data.cell_types) * spec.num_samples > (
        _MAX_POSTERIOR_SAMPLE_CELLS
    ):
        return "posterior_size_bound_exceeded"
    return None


def _count_admission(data: CompositionInput, spec: CompositionSpec) -> str | None:
    if len(set(data.cell_types)) != len(data.cell_types):
        return "duplicate_cell_types"
    if spec.reference_cell_type not in data.cell_types:
        return "reference_cell_type_missing"
    if any(len(sample.counts) != len(data.cell_types) for sample in data.samples):
        return "incomplete_cell_type_counts"
    if any(sum(sample.counts) != sample.cells_observed for sample in data.samples):
        return "cell_count_closure_mismatch"
    if any(
        not any(column)
        for column in zip(*(sample.counts for sample in data.samples), strict=True)
    ):
        return "cell_type_unobserved_in_cohort"
    reference = data.cell_types.index(spec.reference_cell_type)
    observed_reference = {
        sample.condition for sample in data.samples if sample.counts[reference] > 0
    }
    if observed_reference != {sample.condition for sample in data.samples}:
        return "reference_cell_type_absent_in_condition"
    return None


def run_sccoda(
    data: CompositionInput,
    spec: CompositionSpec,
    *,
    runtime: ScCODARuntime | None = None,
) -> CompositionResult:
    """Validate a frozen count panel before invoking the pinned sample-level fit."""
    support = _audit_composition(data, spec)
    common = CompositionResult(
        status="refused" if support.reason else "unavailable",
        reason=support.reason or "scCODA_runtime_unavailable",
        input_sha256=canonical_sha256(data.model_dump(mode="json")),
        spec_sha256=canonical_sha256(spec.model_dump(mode="json")),
        reference_cell_type=spec.reference_cell_type,
        baseline_condition=spec.baseline_condition,
        condition_biological_units=support.units_by_condition,
        target_est_fdr=spec.target_est_fdr,
        measured_zero_count=support.measured_zeros,
    )
    if support.reason or runtime is None:
        return common
    from .scrna_composition_solver import run_pertpy

    result = run_pertpy(data, spec, runtime, common)
    _verify_result_axes(result, data, spec, runtime)
    return result


def _verify_result_axes(
    result: CompositionResult,
    data: CompositionInput,
    spec: CompositionSpec,
    runtime: ScCODARuntime | None,
) -> None:
    if result.runtime is not None and result.runtime != runtime:
        raise ValueError("Composition receipt does not bind the trusted runtime")
    if result.status != "succeeded":
        return
    conditions = {sample.condition for sample in data.samples} - {
        spec.baseline_condition
    }
    cell_types = set(data.cell_types) - {spec.reference_cell_type}
    expected = {(condition, cell) for condition in conditions for cell in cell_types}
    actual = [(row.condition, row.cell_type) for row in result.effects]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError("Composition receipt does not cover the full Bayesian family")
    if result.sampling is None or result.sampling.draws != spec.num_samples:
        raise ValueError("Composition receipt does not match the frozen draw count")
    if result.zero_replacement != (0.5 if result.measured_zero_count else None):
        raise ValueError("Composition receipt disagrees with measured-zero treatment")


def write_composition_receipt(path: Path, result: CompositionResult) -> str:
    if not path.is_absolute() or path != path.resolve():
        raise ValueError("Composition receipt requires an absolute nonsymlink path")
    payload = canonical_json(result.model_dump(mode="json"))
    if len(payload) > MAX_COMPOSITION_BYTES:
        raise ValueError("Composition receipt exceeds its byte bound")
    digest = sha256(payload).hexdigest()
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with tempfile.NamedTemporaryFile(prefix=".pending-", dir=path.parent) as staged:
            staged.write(payload)
            staged.flush()
            os.fchmod(staged.fileno(), 0o444)
            os.fsync(staged.fileno())
            try:
                os.link(
                    Path(staged.name).name,
                    path.name,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
            except FileExistsError as error:
                if hash_bounded_file(path, MAX_COMPOSITION_BYTES) != digest:
                    raise ValueError(
                        "Corrupt existing evidence: hash mismatch"
                    ) from error
        os.fsync(directory)
    finally:
        os.close(directory)
    return digest


def read_composition_receipt(
    path: Path,
    digest: str,
    *,
    data: CompositionInput,
    spec: CompositionSpec,
    runtime: ScCODARuntime | None = None,
) -> CompositionResult:
    payload = read_bounded_file(path, MAX_COMPOSITION_BYTES)
    if sha256(payload).hexdigest() != digest:
        raise ValueError("Composition receipt digest mismatch")
    result = CompositionResult.model_validate_json(payload)
    if canonical_json(result.model_dump(mode="json")) != payload:
        raise ValueError("Composition receipt must use its canonical byte encoding")
    if result.input_sha256 != canonical_sha256(data.model_dump(mode="json")):
        raise ValueError("Receipt does not bind the supplied composition input")
    if result.spec_sha256 != canonical_sha256(spec.model_dump(mode="json")):
        raise ValueError("Receipt does not bind the supplied composition specification")
    _verify_audit(result, data, spec)
    _verify_result_axes(result, data, spec, runtime)
    return result


def _verify_audit(
    result: CompositionResult, data: CompositionInput, spec: CompositionSpec
) -> None:
    support = _audit_composition(data, spec)
    actual = (
        result.reference_cell_type,
        result.baseline_condition,
        result.target_est_fdr,
        result.condition_biological_units,
        result.measured_zero_count,
    )
    expected = (
        spec.reference_cell_type,
        spec.baseline_condition,
        spec.target_est_fdr,
        support.units_by_condition,
        support.measured_zeros,
    )
    if actual != expected:
        raise ValueError("Composition receipt disagrees with the design audit")
    if result.status == "refused" and support.reason is None:
        raise ValueError("Composition receipt refuses an admitted design")
    if support.reason is not None and (
        result.status != "refused" or result.reason != support.reason
    ):
        raise ValueError("Composition receipt bypasses input refusal")
