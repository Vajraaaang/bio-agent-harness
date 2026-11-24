"""Stage bounded original-count single-cell profiles for the mounted bulk kernel."""

from __future__ import annotations

import csv
import os
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from itertools import product
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, NamedTuple

import h5py
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from devbench.analysis.integration_metrics import (
    MAX_LABEL_MAP_BYTES,
    MAX_LABEL_QUALIFICATION_BYTES,
    ExternalLabelEvidence,
    verify_external_labels,
)
from devbench.core.contrast_design import (
    ContrastSpec,
    DesignSpec,
    MountedModelSpec,
    PipelineSpec,
    prove_donor_aliases,
)
from devbench.core.population_contract import LIMITS, content_sha256
from devbench.core.population_data import _node, _source
from devbench.core.publication_families import FamilySpec
from devbench.core.scrna_qc_selection import AcceptedQCSelection, QCSelectionBinding
from devbench.platform.adaptive_workflow_state import ArtifactVersion
from devbench.platform.scrna_population_analysis import (
    _BINARY_MAX_PROFILES,
    _BINARY_PANEL_BYTES,
    _BINARY_SUMMARY_BYTES,
    _write_binary_counts,
    bounded_result,
)
from devbench.study.evidence import _read
from devharness_core.provenance import canonical_json, copy_artifact, hash_artifact
from rna_kernels.bulk_solver_policy import require_dream_coefficient
from rna_kernels.mounted_model import compile_mounted_model
from rna_kernels.units import UNKNOWN_UNIT_MARKERS

if TYPE_CHECKING:
    from devbench.engine.tier_a_executor import TierAExecutor
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

_Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")]
_Level = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,160}$")]
_Label = Annotated[str, StringConstraints(min_length=1, max_length=200)]
_Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_MIN_CELLS = 20
_MAX_METADATA = 5_000_000
_MAX_FAMILY = 16_000_000
# The coordinator can stage larger cohorts than the participant population
# contract. Expression is still read with the shared bounded row blocks.
_SOURCE_LIMITS = {**LIMITS, "max_cells": 200_000, "max_metadata_bytes": 64 * 1024**2}
_MOUNTED_INPUT_LIMITS = {
    "expression.h5": _BINARY_PANEL_BYTES,
    "metadata.csv": _MAX_METADATA,
    "family.json": _MAX_FAMILY,
}
QC_LABEL_MAP = "population-labels.json"
QC_LABEL_QUALIFICATION = "population-label-qualification.json"
QC_LABEL_FILE_LIMITS = {
    QC_LABEL_MAP: MAX_LABEL_MAP_BYTES,
    QC_LABEL_QUALIFICATION: MAX_LABEL_QUALIFICATION_BYTES,
}
_IDENTITY_COLUMNS = (
    "sample_id",
    "donor",
    "specimen",
    "condition",
    "source_condition",
    "population",
    "profile_membership_id",
    "cell_count",
)
_FORMAT = "devbench.scrna-general-pseudobulk/1"


class GeneralPseudobulkSpec(BaseModel):
    """Frozen original-count, donor, cohort, and mounted model declarations."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )

    source_sha256: _Sha256
    assay: Literal["scrna", "snrna"]
    measurement: Literal["umi_counts", "non_umi_counts"]
    count_provenance: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    family_id: _Label
    regime: Literal["S-I", "S-P", "P-P"]
    unit_column: _Name
    specimen_column: _Name
    condition_column: _Name
    population_column: _Name
    groups: Annotated[tuple[_Level, ...], Field(min_length=2, max_length=32)]
    population: _Label | None = None
    condition: _Label | None = None
    contrast: ContrastSpec
    model: MountedModelSpec
    technical_factors: Annotated[tuple[_Name, ...], Field(max_length=16)] = ()
    continuous_covariates: Annotated[tuple[_Name, ...], Field(max_length=16)] = ()
    candidate_aliases: Annotated[tuple[_Name, ...], Field(max_length=16)] = ()

    @model_validator(mode="after")
    def freeze_roles(self) -> GeneralPseudobulkSpec:
        roles = (
            self.unit_column,
            self.specimen_column,
            self.condition_column,
            self.population_column,
            *self.technical_factors,
            *self.continuous_covariates,
        )
        nuisance = self.technical_factors + self.continuous_covariates
        if len(roles) != len(set(roles)) or set(nuisance).intersection(
            {*_IDENTITY_COLUMNS, "biological_unit"}
        ):
            raise ValueError(
                "Pseudobulk metadata roles and mounted factors must be distinct"
            )
        aliases = set(self.candidate_aliases)
        if aliases and (
            self.model.units != "paired_fixed"
            or len(aliases) != len(self.candidate_aliases)
            or not aliases <= set(self.technical_factors)
        ):
            raise ValueError(
                "Donor alias candidates require fixed pairing and technical factors"
            )
        if any(
            not _label(value)
            for value in (self.family_id, self.condition, self.population)
            if value is not None
        ):
            raise ValueError("Pseudobulk labels require exact nonempty text")
        if not self.count_provenance.strip() or "\x00" in self.count_provenance:
            raise ValueError("Declare the provenance of the original counts layer")
        _validate_hypothesis(self)
        return self


def _validate_hypothesis(spec: GeneralPseudobulkSpec) -> None:
    groups = set(spec.groups)
    if (
        len(groups) != len(spec.groups)
        or not {
            spec.contrast.numerator,
            spec.contrast.denominator,
        }
        <= groups
    ):
        raise ValueError("Bind distinct groups and both contrast anchors")
    if spec.regime == "P-P":
        if spec.condition is None or spec.population is not None:
            raise ValueError("Population pairs require one source condition")
    elif spec.population is None or spec.condition is not None:
        raise ValueError(
            "Within-cell-type designs require one population across groups"
        )
    expected_units = (
        {"independent"} if spec.regime == "S-I" else {"paired_fixed", "mixed"}
    )
    if (
        spec.model.units not in expected_units
        or spec.model.artifact_digests()
        or spec.model.latent_method
    ):
        raise ValueError(
            "Bind the declared donor design to an original-count mounted model"
        )
    if spec.model.units == "mixed":
        require_dream_coefficient("raw_counts", spec.model.comparison)
        if len(groups) != 2 or spec.model.sensitivity_method is not None:
            raise ValueError(
                "Mixed pseudobulk requires a primary two-group coefficient"
            )


@dataclass(frozen=True)
class StagedPseudobulk:
    """Immutable capsule reference and its optional structurally estimable plan."""

    directory: Path
    pipeline: PipelineSpec | None
    summary: dict[str, Any]
    provenance_sha256: str


class _ProfileKey(NamedTuple):
    donor: str
    specimen: str
    condition: str
    population: str


def _label(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and "\x00" not in value
        and len(value) <= 200
    )


def _reader_spec(spec: GeneralPseudobulkSpec) -> dict[str, Any]:
    return {
        "source_sha256": spec.source_sha256,
        "counts_layer": "counts",
        "biological_unit_column": spec.unit_column,
        "stage_column": spec.condition_column,
        "populations": [{"column": spec.population_column}],
        "technical_alias_columns": [spec.specimen_column, *spec.technical_factors],
        "limits": _SOURCE_LIMITS,
    }


def _profile_keys(source: Any, spec: GeneralPseudobulkSpec) -> list[_ProfileKey]:
    roles = (
        spec.unit_column,
        spec.specimen_column,
        spec.condition_column,
        spec.population_column,
    )
    rows = [
        _ProfileKey(*row)
        for row in source.obs.loc[:, list(roles)].itertuples(
            index=False,
            name=None,
        )
    ]
    if any(not all(map(_label, row)) for row in rows):
        raise ValueError(
            "Donor, specimen, condition and population require exact labels"
        )
    if any(
        row.donor.casefold() in UNKNOWN_UNIT_MARKERS
        or row.specimen.casefold() in UNKNOWN_UNIT_MARKERS
        for row in rows
    ):
        raise ValueError(
            "Pseudobulk requires known declared donor and specimen identities"
        )
    if {row.donor for row in rows} == set(source.obs.index):
        raise ValueError("Cell identifiers cannot serve as independent donor units")
    donor_specimens = {(row.donor, row.specimen) for row in rows}
    if len(donor_specimens) != len({row.specimen for row in rows}):
        raise ValueError("Specimen identities must nest within a single donor")
    return rows


def _expected_profiles(
    rows: list[_ProfileKey],
    spec: GeneralPseudobulkSpec,
) -> list[_ProfileKey]:
    conditions = {spec.condition} if spec.regime == "P-P" else set(spec.groups)
    populations = spec.groups if spec.regime == "P-P" else (spec.population,)
    specimens = sorted(
        {
            (row.donor, row.specimen, row.condition)
            for row in rows
            if row.condition in conditions
        }
    )
    if not specimens or len(specimens) * len(populations) > _BINARY_MAX_PROFILES:
        raise ValueError(
            "Declared pseudobulk scope has no specimens or exceeds profile bounds"
        )
    return [
        _ProfileKey(donor, specimen, condition, str(population))
        for (donor, specimen, condition), population in product(specimens, populations)
    ]


def _numeric_columns(source: Any, names: tuple[str, ...]) -> dict[str, np.ndarray]:
    if not names:
        return {}
    obs = _node(source._handle, "obs")
    columns = {}
    for name in names:
        node = _node(obs, name)
        if (
            not isinstance(node, h5py.Dataset)
            or node.shape != (source.shape[0],)
            or node.dtype.kind not in "iuf"
            or node.dtype.itemsize > 8
        ):
            raise ValueError(
                f"Covariate {name} requires an aligned numeric observation field"
            )
        values = node[:]
        if (
            not np.isfinite(values).all()
            or np.any(values > 2**53)
            or np.any(values < -(2**53))
        ):
            raise ValueError(f"Covariate {name} exceeds finite numeric bounds")
        columns[name] = values
    return columns


def _profile_rows(
    keys: list[_ProfileKey],
    indices: np.ndarray,
    source: Any,
    spec: GeneralPseudobulkSpec,
) -> tuple[list[dict[str, Any]], list[str]]:
    numeric = _numeric_columns(source, spec.continuous_covariates)
    columns = {name: source.obs[name].to_numpy() for name in spec.technical_factors}
    columns.update(numeric)
    selected = np.flatnonzero(indices >= 0)
    ordered = selected[np.argsort(indices[selected], kind="stable")]
    cell_counts = np.bincount(indices[selected], minlength=len(keys))
    offsets = np.concatenate(([0], np.cumsum(cell_counts)))
    rows, unresolved = [], set()
    for index, key in enumerate(keys):
        sample = f"profile_{index}"
        members = ordered[offsets[index] : offsets[index + 1]]
        row = {
            "sample_id": sample,
            "donor": key.donor,
            "specimen": key.specimen,
            "condition": key.population if spec.regime == "P-P" else key.condition,
            "source_condition": key.condition,
            "population": key.population,
            "profile_membership_id": sample,
            "cell_count": len(members),
        }
        for name, column in columns.items():
            values = column[members]
            value = values[0]
            if name in numeric or all(map(_label, values)):
                if np.all(values == value):
                    row[name] = float(value) if name in numeric else value
                    continue
            unresolved.add(name)
        rows.append(row)
    return rows, [
        f"Nonconstant or missing profile-level factor {name}"
        for name in sorted(unresolved)
    ]


def _check_units(
    expected: list[_ProfileKey],
    observed: list[_ProfileKey],
    spec: GeneralPseudobulkSpec,
) -> list[str]:
    if spec.model.units == "independent":
        counts = Counter(key.donor for key in expected)
        if any(count > 1 for count in counts.values()):
            return ["Repeated specimens or conditions violate independent donors"]
        return []
    if spec.model.units == "mixed":
        # A donor intercept also covers specimen clustering when the two
        # identities coincide. Repeated profiles within multiple specimens
        # from one donor require a second grouping term.
        profiles = Counter(key.specimen for key in observed)
        donor_specimens = {(row.donor, row.specimen) for row in observed}
        specimens = Counter(donor for donor, _ in donor_specimens)
        if any(
            profiles[specimen] > 1 and specimens[donor] > 1
            for donor, specimen in donor_specimens
        ):
            return ["Nested repeat profiles require a specimen effect within donor"]
        return []
    if len(spec.groups) != 2:
        return ["Mounted fixed pairing requires exactly two explicit target groups"]
    by_unit: dict[str, list[str]] = defaultdict(list)
    for key in expected:
        target = key.population if spec.regime == "P-P" else key.condition
        by_unit[key.donor].append(target)
    if any(sorted(targets) != sorted(spec.groups) for targets in by_unit.values()):
        return ["Paired donors require a single specimen profile per target group"]
    return []


def _make_plan(
    spec: GeneralPseudobulkSpec,
    family: FamilySpec,
    hashes: dict[str, str],
) -> PipelineSpec:
    return PipelineSpec(
        format="devbench.tier-a-bulk-pipeline/2",
        design=DesignSpec(
            "B-I" if spec.regime == "S-I" else "B-P",
            spec.contrast.numerator,
            spec.contrast.denominator,
            technical_factors=spec.technical_factors,
            cohort_mode="joint_multi_condition_model"
            if len(spec.groups) > 2
            else "contrast_pair_subcohort",
        ),
        contrast=spec.contrast,
        family=family,
        measurement="raw_counts",
        biological_unit="donor",
        condition_column="condition",
        population_unit_column="donor",
        specimen_column="specimen",
        library_column="profile_membership_id",
        mounted_model=spec.model,
        continuous_covariates=spec.continuous_covariates,
        expression_sha256=hashes["expression.h5"],
        metadata_sha256=hashes["metadata.csv"],
        family_sha256=hashes["family.json"],
    )


def _compile_design(spec: GeneralPseudobulkSpec, rows: list[dict[str, Any]]) -> dict:
    if {row["condition"] for row in rows} != set(spec.groups):
        raise ValueError(
            "Declared target groups require observed profiles in every group"
        )
    design = compile_mounted_model(
        rows,
        {
            "condition_column": "condition",
            "unit_column": "donor",
            "technical_factors": spec.technical_factors,
            "continuous_covariates": spec.continuous_covariates,
            "numerator": spec.contrast.numerator,
            "denominator": spec.contrast.denominator,
            "model": spec.model.request(),
        },
    )
    samples, columns = design.design.shape
    return {
        "rank": columns,
        "residual_df": samples - columns,
        "condition_number": design.condition_number,
        "declared_donors": len(set(design.unit_ids)),
    }


def _resolve_donor_aliases(spec, rows, summary):
    if not spec.candidate_aliases:
        return spec
    columns, proofs = prove_donor_aliases(
        tuple(row["donor"] for row in rows),
        {name: tuple(row[name] for row in rows) for name in spec.candidate_aliases},
    )
    if not proofs:
        return spec
    removed = {proof.removed_column.factor for proof in proofs}
    summary.update(
        alias_proofs=[{
            "removed_column": asdict(proof.removed_column),
            "removed_values": list(proof.removed_values),
            "coefficients": [list(coefficient) for coefficient in proof.coefficients],
        } for proof in proofs],
        alias_proof_columns=[asdict(column) for column in columns],
        alias_proof_profile_ids=[row["sample_id"] for row in rows],
    )
    model = spec.model
    if model.reduced_terms is not None:
        reduced = tuple(term for term in model.reduced_terms if term not in removed)
        model = model.model_copy(update={"reduced_terms": reduced})
    return spec.model_copy(update={
        "technical_factors": tuple(name for name in spec.technical_factors
                                   if name not in removed),
        "candidate_aliases": (),
        "model": model,
    })


def _write_matrix(directory, source, features, rows, missing, indices, binding):
    with h5py.File(directory / "expression.h5", "x") as handle:
        _, totals = _write_binary_counts(handle, source, LIMITS, rows, indices)
        handle.attrs.update(binding)
        string = h5py.string_dtype("utf-8")
        handle.create_dataset("gene_names", data=features, dtype=string)
        handle.create_dataset(
            "sample_names", data=[row["sample_id"] for row in rows], dtype=string
        )
        observed = handle.create_group("profiles")
        absent = handle.create_group("missing_profiles")
        for name in ("donor", "specimen", "source_condition", "population"):
            observed.create_dataset(
                name, data=[row[name] for row in rows], dtype=string
            )
        for name in _ProfileKey._fields:
            absent.create_dataset(
                name, data=[getattr(key, name) for key in missing], dtype=string
            )
        observed.create_dataset(
            "cell_count", data=[row["cell_count"] for row in rows], dtype="<i8"
        )
    return totals


def _write_metadata(directory, spec, rows):
    with (directory / "metadata.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=(
                *_IDENTITY_COLUMNS,
                *spec.technical_factors,
                *spec.continuous_covariates,
            ),
        )
        writer.writeheader()
        writer.writerows(rows)


def _qualified_population_labels(source, spec, label_evidence):
    if spec.regime != "P-P":
        if label_evidence is not None:
            raise ValueError(
                "External population pair evidence belongs to a P-P declaration"
            )
        return None
    labels, proof = verify_external_labels(
        label_evidence,
        cell_ids=list(source.obs.index),
        selected_sha256=spec.source_sha256,
    )
    if not np.array_equal(labels, source.obs[spec.population_column].to_numpy()):
        raise ValueError(
            "Qualified population labels differ from the supplied observation axis"
        )
    return proof


def _stage_panel(directory, source, spec, label_evidence, qc_selection):
    source_rows = _profile_keys(source, spec)
    expected = _expected_profiles(source_rows, spec)
    cells, features = tuple(source.obs.index), tuple(source.var.index)
    kept = (
        None if qc_selection is None
        else np.asarray(qc_selection.align(spec.source_sha256, cells), dtype=bool)
    )
    measured = (
        set(source_rows) if kept is None
        else {row for row, passed in zip(source_rows, kept, strict=True) if passed}
    )
    keys = [key for key in expected if key in measured]
    missing = [key for key in expected if key not in measured]
    if not keys:
        raise ValueError("Declared pseudobulk scope has no observed profiles")
    label_proof = _qualified_population_labels(source, spec, label_evidence)
    lookup = {key: index for index, key in enumerate(keys)}
    indices = np.fromiter((lookup.get(key, -1) for key in source_rows), dtype=np.int64)
    if kept is not None:
        indices[~kept] = -1
    rows, refusals = _profile_rows(keys, indices, source, spec)
    refusals.extend(_check_units(expected, keys, spec))
    if missing and (spec.model.units == "paired_fixed" or spec.regime == "P-P"):
        refusals.append("Absent target profiles leave an incomplete donor pair")
    if not all(map(_label, features)):
        raise ValueError(
            "Pseudobulk feature axis must retain exact bounded identifiers"
        )
    membership_sha256 = content_sha256([cells, keys, indices.tolist()])
    binding = {
        "source_sha256": spec.source_sha256,
        "membership_sha256": membership_sha256,
    }
    if label_proof:
        binding["label_qualification_sha256"] = label_proof["qualification_sha256"]
    if qc_selection is not None:
        binding["qc_receipt_sha256"] = qc_selection.binding.receipt_sha256
        binding["qc_metrics_sha256"] = qc_selection.binding.metrics_sha256
        binding["qc_mask_sha256"] = qc_selection.mask_sha256()
    totals = _write_matrix(directory, source, features, rows, missing, indices, binding)
    _write_metadata(directory, spec, rows)
    small = [row["sample_id"] for row in rows if row["cell_count"] < _MIN_CELLS]
    summary = {
        "source_sha256": spec.source_sha256,
        "counts_layer": "layers/counts",
        "measurement_provenance_status": "supplied",
        "independence_verified": False,
        "label_qualification": label_proof,
        "feature_ids_sha256": content_sha256(features),
        "observation_ids_sha256": content_sha256(cells),
        "membership_sha256": membership_sha256,
        "profile_count": len(keys),
        "missing_profile_count": len(missing),
        "missing_combinations": "absent_profiles_never_zero_filled",
        "low_cell_profile_count": len(small),
        "low_cell_profile_ids": small,
        "low_cell_threshold": _MIN_CELLS,
        "profile_library_sizes": totals,
        "observed_cells": int(np.count_nonzero(indices >= 0)),
        "source_cells": len(cells),
        "all_declared_count_entries_validated": True,
    }
    if not all(totals):
        refusals.append("Observed profile with zero total raw counts")
    if qc_selection is not None:
        summary["qc_selection"] = qc_selection.binding.model_dump(mode="json")
        summary["qc_mask_sha256"] = binding["qc_mask_sha256"]
        summary["qc_retained_cells"] = sum(qc_selection.passed)
    return features, rows, summary, refusals


def _publish_plan(directory, spec, features, rows, refusals, summary):
    family = FamilySpec(
        family_id=spec.family_id,
        population="bulk",
        feature_ids=features,
        numerator=spec.contrast.numerator,
        denominator=spec.contrast.denominator,
        primary=spec.model.sensitivity_method is None,
    )
    (directory / "family.json").write_bytes(
        canonical_json(family.model_dump(mode="json"))
    )
    hashes = {
        name: hash_artifact(directory / name, max_bytes=limit, max_entries=1)["sha256"]
        for name, limit in _MOUNTED_INPUT_LIMITS.items()
    }
    plan, design = None, None
    if not refusals:
        try:
            resolved = _resolve_donor_aliases(spec, rows, summary)
            design = _compile_design(resolved, rows)
            plan = _make_plan(resolved, family, hashes)
        except ValueError as error:
            refusals.append(str(error)[:1000])
    if plan is not None:
        path = directory / "pipeline.json"
        path.write_bytes(canonical_json(plan.model_dump(mode="json")))
        hashes[path.name] = hash_artifact(path, max_bytes=_MAX_FAMILY)["sha256"]
    return plan, design, hashes


def _label_file_digests(proof: dict[str, str]) -> dict[str, str]:
    return {
        QC_LABEL_MAP: proof["labels_sha256"],
        QC_LABEL_QUALIFICATION: proof["qualification_sha256"],
    }


def retained_qc_label_files(summary: dict[str, Any]) -> dict[str, str]:
    """Verify the optional two-file inventory against its label qualification."""
    hashes = summary["artifact_sha256"]
    retained = {name: hashes[name] for name in QC_LABEL_FILE_LIMITS if name in hashes}
    if not retained:
        return {}
    proof = summary.get("label_qualification")
    if (
        not proof or "qc_selection" not in summary
        or retained != _label_file_digests(proof)
    ):
        raise ValueError("QC label files differ from the qualified label inventory")
    return retained


def _retain_label_files(
    directory: Path, evidence: ExternalLabelEvidence, proof: dict[str, str],
) -> dict[str, str]:
    expected = _label_file_digests(proof)
    paths = {
        QC_LABEL_MAP: evidence.labels_path,
        QC_LABEL_QUALIFICATION: evidence.qualification_path,
    }
    for name, path in paths.items():
        copied = copy_artifact(
            path, directory / name, max_bytes=QC_LABEL_FILE_LIMITS[name], max_entries=1,
        )
        if copied["kind"] != "file" or copied["sha256"] != expected[name]:
            raise ValueError("Qualified label evidence changed while staging")
    return expected


def stage_general_pseudobulk(
    source: Path,
    specification: GeneralPseudobulkSpec,
    destination: Path,
    *,
    label_evidence: ExternalLabelEvidence | None = None,
    qc_selection: AcceptedQCSelection | None = None,
) -> StagedPseudobulk:
    """Atomically prepare observed donor profiles for a trusted coordinator."""
    spec = GeneralPseudobulkSpec.model_validate(specification)
    output = Path(destination)
    if (
        not output.is_absolute()
        or output != output.parent.resolve() / output.name
        or not output.parent.is_dir()
        or output.name in {"", ".", ".."}
    ):
        raise ValueError(
            "Use an absolute capsule path in a canonical staging directory"
        )
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    with tempfile.TemporaryDirectory(
        dir=output.parent, prefix=".pseudobulk-"
    ) as scratch:
        staged = Path(scratch) / "capsule"
        staged.mkdir(mode=0o700)
        with _source(source, _reader_spec(spec)) as selected:
            features, rows, summary, refusals = _stage_panel(
                staged,
                selected,
                spec,
                label_evidence,
                qc_selection,
            )
        plan, design, hashes = _publish_plan(
            staged, spec, features, rows, refusals, summary,
        )
        if qc_selection is not None and label_evidence is not None:
            hashes.update(_retain_label_files(
                staged, label_evidence, summary["label_qualification"],
            ))
        declaration = spec.model_dump(
            mode="json",
            exclude=None if spec.candidate_aliases else {"candidate_aliases"},
        )
        summary.update(
            format=_FORMAT,
            status="ready" if plan else "held",
            design=design,
            specification=declaration,
            specification_sha256=content_sha256(declaration),
            refusals=refusals,
            artifact_sha256=hashes,
            scientific_validity_established=False,
        )
        manifest = staged / "provenance.json"
        manifest.write_bytes(
            canonical_json(bounded_result(summary, _BINARY_SUMMARY_BYTES))
        )
        identity = hash_artifact(manifest, max_bytes=_BINARY_SUMMARY_BYTES)["sha256"]
        for path in staged.iterdir():
            path.chmod(0o400)
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
        staged.rename(output)
    return StagedPseudobulk(output, plan, summary, identity)


def register_general_pseudobulk(
    executor: TierAExecutor,
    staged: StagedPseudobulk,
    *,
    workflow: AdaptiveWorkflow | None = None,
) -> tuple[str, dict[str, ArtifactVersion]]:
    """Bind a ready capsule to the existing Tier A inputs before research admission."""
    plan = staged.pipeline
    if plan is None:
        raise ValueError("Pseudobulk design is held and has no mounted plan")
    root, summary = staged.directory, staged.summary
    provenance = root / "provenance.json"
    pipeline = root / "pipeline.json"
    if (
        _read(provenance, _BINARY_SUMMARY_BYTES) != canonical_json(summary)
        or content_sha256(summary) != staged.provenance_sha256
        or _read(pipeline, _MAX_FAMILY) != canonical_json(plan.model_dump(mode="json"))
    ):
        raise ValueError(
            "Staged pseudobulk provenance or plan changed before registration"
        )
    hashes = summary["artifact_sha256"]
    if any(
        hashes[name] != expected
        for name, expected in (
            ("expression.h5", plan.expression_sha256),
            ("metadata.csv", plan.metadata_sha256),
            ("family.json", plan.family_sha256),
        )
    ):
        raise ValueError(
            "Staged pseudobulk provenance differs from mounted plan inputs"
        )
    _require_pseudobulk_qc(executor, workflow, summary)
    label_files = retained_qc_label_files(summary)
    label_source = (
        (summary["label_qualification"]["source_sha256"],) if label_files else ()
    )
    executor._require_unprotected(
        (
            summary["source_sha256"], staged.provenance_sha256,
            *hashes.values(), *label_source,
        )
    )
    records = {
        "expression.h5": plan.expression_sha256,
        "metadata.csv": plan.metadata_sha256,
        "family.json": plan.family_sha256,
        "provenance.json": staged.provenance_sha256,
        **label_files,
    }
    limits = {
        **_MOUNTED_INPUT_LIMITS,
        **{name: QC_LABEL_FILE_LIMITS[name] for name in label_files},
    }
    for name, limit in limits.items():
        actual = hash_artifact(root / name, max_bytes=limit, max_entries=1)["sha256"]
        if actual != records[name]:
            raise ValueError(f"Staged pseudobulk artifact changed: {name}")
    for name, expected in records.items():
        if executor.training.put_file(root / name) != expected:
            raise ValueError(f"Staged pseudobulk artifact changed: {name}")
    parameters = executor.register_pipeline(plan)
    if parameters != hashes["pipeline.json"]:
        raise ValueError(
            "Staged pseudobulk plan digest differs from the registered plan"
        )
    proof = summary["label_qualification"]
    common: dict[str, Any] = {
        "biological_unit": "donor",
        "unit_membership_sha256": summary["membership_sha256"],
        "label_origin": "independent" if proof else "unknown",
        "label_qualification_sha256": proof["qualification_sha256"] if proof else None,
        "permitted_uses": ("checked_observation", "admitted_research_inference"),
    }
    return parameters, {
        "expression": ArtifactVersion(
            version_id=f"pseudobulk-{staged.provenance_sha256}-counts",
            sha256=plan.expression_sha256,
            kind="counts",
            representation="raw_counts_h5",
            measurement_history=(summary["specification"]["measurement"], "pseudobulk"),
            **common,
        ),
        "metadata": ArtifactVersion(
            version_id=f"pseudobulk-{staged.provenance_sha256}-metadata",
            sha256=plan.metadata_sha256,
            kind="table",
            representation="metadata_csv",
            **common,
        ),
    }


def _require_pseudobulk_qc(executor, workflow, summary) -> None:
    from devbench.engine.workflow_operations import OperationBlocked

    from .scrna_qc_handoff import accepted_qc_selection

    selection = summary.get("qc_selection")
    if selection is None:
        return
    if workflow is None or executor.ledger.path != workflow.ledger.path:
        raise PermissionError("QC-selected pseudobulk requires its owning workflow")
    binding = QCSelectionBinding.model_validate(selection)
    if binding.source_sha256 != summary["source_sha256"]:
        raise ValueError("Pseudobulk QC selection differs from its count source")
    current = accepted_qc_selection(
        workflow, executor, binding.step_id, source_sha256=binding.source_sha256,
    )
    if current.binding != binding or current.mask_sha256() != summary["qc_mask_sha256"]:
        raise OperationBlocked("scrna_qc_handoff_receipt_changed")
