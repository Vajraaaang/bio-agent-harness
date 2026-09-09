"""Locked inputs for native edgeR/DESeq2 analyses, separate from Python plan v1.

The plan identifies data, two preserved design matrices, the trusted driver and
an immutable runtime image. It carries no executable formula, callback or path.
Column indices are R one-based indices into the numeric design (after removing
the sample-ID field); index 1 is the intercept and cannot be tested as time.

Version pins below are requirements for the executor to verify against the
runtime. A valid plan alone neither verifies that runtime nor authorizes a
clinical release. The signed implementation and package-lock digests bind the
version policy alongside the input specification.
"""
import os
from pathlib import Path
import stat
from typing import Annotated, Final, Literal

from pydantic import Field, StrictFloat, StrictInt, StringConstraints, field_validator, model_validator

from .contracts import Digest, FrozenContract, JSON_LIMIT_BYTES, NonemptyText, Revision, parse_json_bytes
from .normalization_reference import NormalizationReference, reference_genes_for_panel


EXPECTED_R_VERSION: Final[str] = "4.6.1"
EXPECTED_EDGER_VERSION: Final[str] = "4.10.5"
EXPECTED_DESEQ2_VERSION: Final[str] = "1.52.0"

RuntimeImageID = Annotated[str, StringConstraints(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]
TemporalColumn = Annotated[StrictInt, Field(ge=2)]


class NativeAnalysisPlan(FrozenContract):
    """Immutable native count-analysis specification with no inferred study defaults."""

    schema_version: Literal["controlled_native_analysis/1"] = "controlled_native_analysis/1"
    plan_id: NonemptyText
    revision: Revision
    intended_use_ref: NonemptyText
    study_id: NonemptyText
    data_classification: Literal["synthetic", "public_processed", "restricted_clinical"]
    engine: Literal["edger_qlf", "deseq2_lrt"]
    normalization: Literal["rle"]
    deseq2_fit_type: Literal["parametric", "mean"] | None = None
    hypothesis_genes: Annotated[tuple[NonemptyText, ...], Field(min_length=1, max_length=100)]
    panel_genes: Annotated[tuple[NonemptyText, ...], Field(min_length=1, max_length=5000)]
    sample_ids: Annotated[tuple[NonemptyText, ...], Field(min_length=3, max_length=512)]
    temporal_columns: Annotated[tuple[TemporalColumn, ...], Field(min_length=1, max_length=5)]
    time_unit: NonemptyText
    basis_definition_ref: NonemptyText
    counts_sha256: Digest
    metadata_sha256: Digest
    full_design_sha256: Digest
    reduced_design_sha256: Digest
    driver_sha256: Digest
    package_lock_sha256: Digest
    implementation_sha256: Digest
    environment_sha256: Digest
    runtime_image_id: RuntimeImageID
    platform: Literal["linux/amd64"]
    max_memory_mb: Annotated[StrictInt, Field(ge=128, le=65536)]
    timeout_sec: Annotated[StrictInt, Field(ge=1, le=86400)]
    max_cpus: Annotated[StrictInt, Field(ge=1, le=8)]

    @field_validator("hypothesis_genes", "panel_genes", "sample_ids")
    @classmethod
    def unique_nonblank_entries(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Collection entries must be unique")
        if any(not entry.strip() for entry in value):
            raise ValueError("Collection entries must not be blank")
        return value

    @field_validator("temporal_columns")
    @classmethod
    def increasing_temporal_columns(cls, value):
        if any(left >= right for left, right in zip(value, value[1:])):
            raise ValueError("Temporal column indices must be strictly increasing")
        return value

    @model_validator(mode="after")
    def coherent_plan(self):
        if not set(self.hypothesis_genes).issubset(self.panel_genes):
            raise ValueError("The locked panel must include every hypothesis gene")
        if self.engine == "deseq2_lrt" and self.deseq2_fit_type is None:
            raise ValueError("DESeq2 requires an explicit dispersion fit type")
        if self.engine == "edger_qlf" and self.deseq2_fit_type is not None:
            raise ValueError("DESeq2 dispersion fit type is not applicable to edgeR")
        return self


class NativeEffectPlan(NativeAnalysisPlan):
    """One prespecified fitted-mean contrast, separate from primary LRT inference."""

    schema_version: Literal["controlled_native_analysis/2"] = "controlled_native_analysis/2"
    engine: Literal["deseq2_lrt"]
    effect_name: NonemptyText
    effect_reference_sample_id: NonemptyText
    effect_comparison_sample_id: NonemptyText
    effect_confidence_level: Annotated[StrictFloat, Field(gt=0, lt=1, allow_inf_nan=False)]

    @model_validator(mode="after")
    def observed_distinct_effect_rows(self):
        if (self.effect_reference_sample_id == self.effect_comparison_sample_id
                or self.effect_reference_sample_id not in self.sample_ids
                or self.effect_comparison_sample_id not in self.sample_ids):
            raise ValueError("Effect references must be distinct signed sample rows")
        return self

    def primary_plan(self) -> NativeAnalysisPlan:
        """Shared numerical validation only; never a replacement signed plan."""
        values = {key: value for key, value in self.model_dump().items() if key in NativeAnalysisPlan.model_fields}
        values["schema_version"] = "controlled_native_analysis/1"
        return NativeAnalysisPlan(**values)


class GovernedNativePlan(NativeAnalysisPlan):
    """Native analysis bound to an explicit signed authority-history tip."""

    schema_version: Literal["controlled_native_analysis/3"] = "controlled_native_analysis/3"
    authority_id: NonemptyText
    authority_tip_sha256: Digest

    def primary_plan(self) -> NativeAnalysisPlan:
        """Shared numerical validation only; never a replacement signed plan."""
        values={key:value for key,value in self.model_dump().items() if key in NativeAnalysisPlan.model_fields}
        values["schema_version"]="controlled_native_analysis/1"
        return NativeAnalysisPlan(**values)


class GovernedNativeEffectPlan(NativeEffectPlan):
    """Prespecified effects with the same explicit authority binding."""

    schema_version: Literal["controlled_native_analysis/4"] = "controlled_native_analysis/4"
    authority_id: NonemptyText
    authority_tip_sha256: Digest


class ReferenceNativePlan(GovernedNativePlan):
    """Governed DESeq2 analysis using only prespecified positive references."""

    schema_version: Literal["controlled_native_analysis/5"] = "controlled_native_analysis/5"
    engine: Literal["deseq2_lrt"]
    normalization_reference: NormalizationReference

    @model_validator(mode="after")
    def coherent_reference(self):
        reference_genes_for_panel(self.normalization_reference, study_id=self.study_id, panel_genes=self.panel_genes)
        return self

    def primary_plan(self):
        """Revalidate without dropping reference semantics; not new authority."""
        return ReferenceNativePlan.model_validate(self)


class ReferenceNativeEffectPlan(GovernedNativeEffectPlan):
    """Governed reference-normalized endpoint effect; interval remains unqualified."""

    schema_version: Literal["controlled_native_analysis/6"] = "controlled_native_analysis/6"
    normalization_reference: NormalizationReference

    @model_validator(mode="after")
    def coherent_reference(self):
        reference_genes_for_panel(self.normalization_reference, study_id=self.study_id, panel_genes=self.panel_genes)
        return self

    def primary_plan(self) -> ReferenceNativePlan:
        values = {key: value for key, value in self.model_dump().items() if key in ReferenceNativePlan.model_fields}
        values["schema_version"] = "controlled_native_analysis/5"
        return ReferenceNativePlan(**values)


NATIVE_PLAN_MODELS = {
    "controlled_native_analysis/1": NativeAnalysisPlan,
    "controlled_native_analysis/2": NativeEffectPlan,
    "controlled_native_analysis/3": GovernedNativePlan,
    "controlled_native_analysis/4": GovernedNativeEffectPlan,
    "controlled_native_analysis/5": ReferenceNativePlan,
    "controlled_native_analysis/6": ReferenceNativeEffectPlan,
}


def is_reference_plan(plan):
    return isinstance(plan, (ReferenceNativePlan, ReferenceNativeEffectPlan))


def is_governed_plan(plan):
    return isinstance(plan, (GovernedNativePlan, GovernedNativeEffectPlan))


def parse_native_plan_bytes(raw: bytes) -> NativeAnalysisPlan:
    """Reject duplicate/nonfinite JSON before the strict array-to-tuple boundary."""
    value = parse_json_bytes(raw)
    model = NATIVE_PLAN_MODELS.get(value.get("schema_version"), NativeAnalysisPlan) if type(value) is dict else NativeAnalysisPlan
    return model.model_validate_json(raw)


def load_native_plan(path: str | Path) -> NativeAnalysisPlan:
    """Read one absolute regular file, without following its symlink, up to 1 MB."""
    source = Path(path)
    if not source.is_absolute():
        raise ValueError("Native plan path must be absolute")
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Native plan must be a regular file")
        if info.st_size > JSON_LIMIT_BYTES:
            raise ValueError("Native plan exceeds 1 MB size limit")
        return parse_native_plan_bytes(handle.read(JSON_LIMIT_BYTES + 1))
