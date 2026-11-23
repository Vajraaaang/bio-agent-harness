"""Prospective, independent simulation panels and exact mounted solver inputs.

The implementation freezes the simulation seeds and TPR floors. The count
generator samples negative binomial counts conditional on the log-scale mean.
``_sample_profiles`` includes a donor effect drawn from ``Normal(0, 0.30)`` with
standard deviation 0.30. Paired donor blocks absorb this effect. In independent
regimes it produces a marginal lognormal-compounded negative binomial.
For single-cell regimes it allocates each profile's sampled molecules to raw UMI
cell counts before the production count-profile compiler aggregates the cells.
Population membership and all design labels are assigned before expression.
"""

import csv
import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from devbench.core.contrast_design import (
    CompiledContrast,
    DesignSpec,
    Regime,
    compile_contrast_design,
)
from devbench.core.count_profiles import (
    CountBlock,
    CountComponent,
    CountObservation,
    CountProfiles,
    ProfileKey,
    compile_count_profiles,
)
from devharness_core.provenance import canonical_json, hash_artifact
from rna_kernels.bulk_solver_policy import freeze_bulk_solver
from rna_kernels.mounted_model import (
    _fit_config,
    _write_design_inputs,
    compile_mounted_model,
)

SEED = 20261117
N_SIM = 200
ALPHA = 0.05
NULL_FDR_LIMIT = 0.075
N_GENES = 192
N_PLANTED = 24
N_CELLS_PER_PROFILE = 24
CONFOUNDED_ARM_SIZE = 52
FEATURES = tuple(f"g{gene:03}" for gene in range(N_GENES))
_BULK_ARMS = ("reference", "case")
_POPULATION_ARMS = ("A", "B")


@dataclass(frozen=True)
class Scenario:
    regime: Regime
    key: str
    index: int
    paired: bool
    single_cell: bool
    tpr_minimum: float


# The paired floors allow for higher power after donor blocking. Even these
# strong planted signals must be recovered under sampling and normalization.
SIMULATIONS = (
    Scenario("B-I", "bi", 0, False, False, 0.60),
    Scenario("B-P", "bp", 1, True, False, 0.70),
    Scenario("S-I", "si", 2, False, True, 0.60),
    Scenario("S-P", "sp", 3, True, True, 0.70),
    Scenario("P-P", "pp", 4, True, True, 0.70),
)


@dataclass(frozen=True)
class _Assignments:
    keys: tuple[ProfileKey, ...]
    batches: tuple[str, ...]
    donor_indexes: np.ndarray
    treatment: np.ndarray


@dataclass(frozen=True)
class CountSource:
    counts: np.ndarray
    gene_names: tuple[str, ...] = FEATURES

    @property
    def shape(self) -> tuple[int, int]:
        return (self.counts.shape[0], self.counts.shape[1])

    def iter_gene_chunks(
        self, chunk_size: int
    ) -> Iterator[tuple[list[str], np.ndarray]]:
        for start in range(0, len(self.gene_names), chunk_size):
            section = slice(start, start + chunk_size)
            yield list(self.gene_names[section]), self.counts[section]


@dataclass(frozen=True)
class Cohort:
    case_id: str
    source: CountSource
    rows: list[dict[str, str]]
    request: dict[str, Any]
    design: CompiledContrast
    latent_signs: np.ndarray
    cell_profile_count: int

    @property
    def counts_sha256(self) -> str:
        counts_le = self.source.counts.astype("<i8", copy=False)
        return hashlib.sha256(counts_le.tobytes()).hexdigest()


def _assignments(spec: Scenario, n_arm: int, *, confounded: bool) -> _Assignments:
    keys, batches, donor_indexes, treatment = [], [], [], []
    for arm in (0, 1):
        for index in range(n_arm):
            donor_index = index if spec.paired else index + n_arm * arm
            donor = f"donor-{donor_index:03}"
            condition = "fixed" if spec.regime == "P-P" else _BULK_ARMS[arm]
            population = (
                _POPULATION_ARMS[arm]
                if spec.regime == "P-P"
                else ("A" if spec.single_cell else None)
            )
            key = ProfileKey(donor, f"{donor}-{condition}", condition, population)
            if confounded:
                batch = arm
            elif spec.regime == "P-P":
                batch = index % 2
            else:
                batch = (index + arm) % 2
            keys.append(key)
            batches.append(f"batch{batch}")
            donor_indexes.append(donor_index)
            treatment.append(arm)
    return _Assignments(
        tuple(keys),
        tuple(batches),
        np.asarray(donor_indexes),
        np.asarray(treatment),
    )


def _sample_profiles(
    spec: Scenario,
    assignments: _Assignments,
    rng: np.random.Generator,
    latent_signs: np.ndarray,
) -> np.ndarray:
    n_profiles = len(assignments.keys)
    n_donors = int(np.max(assignments.donor_indexes)) + 1
    typical_count = 80 if spec.single_cell else 45
    baseline = rng.normal(np.log(typical_count), 0.55, N_GENES)
    donor = rng.normal(0, 0.30, (n_donors, N_GENES))
    library = rng.normal(0, 0.20, n_profiles)
    technical = rng.normal(0, 0.20, N_GENES)
    batch1 = np.asarray([batch == "batch1" for batch in assignments.batches])
    log_mean = (
        baseline
        + donor[assignments.donor_indexes]
        + library[:, None]
        + batch1[:, None] * technical
        + assignments.treatment[:, None] * latent_signs * np.log(3)
    )
    dispersion = rng.uniform(0.04, 0.12, N_GENES)
    mean = np.exp(log_mean)
    return rng.negative_binomial(1 / dispersion, 1 / (1 + dispersion * mean))


def _profile_inventory(
    spec: Scenario,
    case_id: str,
    assignments: _Assignments,
    counts: np.ndarray,
    rng: np.random.Generator,
) -> CountProfiles:
    components: dict[str, CountComponent] = {}
    observations, rows = [], []
    n_cells = N_CELLS_PER_PROFILE if spec.single_cell else 1
    draw = np.full(n_cells, 1 / n_cells)
    for index, (key, batch, profile) in enumerate(
        zip(assignments.keys, assignments.batches, counts, strict=True)
    ):
        material = key.specimen_id
        if material not in components:
            receipt = hashlib.sha256(f"{case_id}/{material}".encode()).hexdigest()
            components[material] = CountComponent(
                material,
                material,
                material,
                "synthetic-v1",
                material,
                0,
                n_cells * (2 if spec.regime == "P-P" else 1),
                receipt,
                "umi_counts" if spec.single_cell else "non_umi_counts",
                (("batch", batch),),
            )
        allocation = rng.multinomial(profile, draw).T if spec.single_cell else [profile]
        for cell_index, values in enumerate(allocation):
            identity = f"observation-{index:03}-{cell_index:02}"
            observations.append(
                CountObservation(
                    identity,
                    key,
                    material,
                    identity if spec.single_cell else None,
                )
            )
            rows.append(values)
    return compile_count_profiles(
        FEATURES,
        tuple(components.values()),
        (CountBlock(tuple(observations), np.asarray(rows)),),
        expected_profiles=assignments.keys,
    )


def _role(spec: Scenario) -> DesignSpec:
    denominator, numerator = _POPULATION_ARMS if spec.regime == "P-P" else _BULK_ARMS
    return DesignSpec(
        spec.regime,
        numerator,
        denominator,
        population="A" if spec.regime in {"S-I", "S-P"} else None,
        condition="fixed" if spec.regime == "P-P" else None,
        technical_factors=("batch",),
        candidate_aliases=("batch",) if spec.regime == "P-P" else (),
    )


def _mounted_request(
    spec: Scenario,
    design: CompiledContrast,
) -> dict[str, Any]:
    # A constant within-donor P-P batch is represented by the fixed unit span.
    # The compiled nuisance-only proof must exist before removing the column.
    if spec.regime == "P-P":
        assert len(design.alias_proofs) == 1
        assert design.alias_proofs[0].removed_column.factor == "batch"
    return {
        "measurement": "raw_counts",
        "biological_unit": "donor",
        "unit_column": "donor",
        "condition_column": "target",
        "technical_factors": [] if design.alias_proofs else ["batch"],
        "continuous_covariates": [],
        "numerator": design.numerator,
        "denominator": design.denominator,
        "model": {
            "comparison": "coefficient",
            "units": "paired_fixed" if spec.paired else "independent",
        },
    }


def generate_cohort(
    spec: Scenario,
    *,
    planted: bool,
    index: int,
    confounded: bool = False,
) -> Cohort:
    assert 0 <= index < N_SIM
    if confounded:
        assert spec.regime == "B-I" and not planted
    arm = "confounded" if confounded else "planted" if planted else "null"
    case_id = f"{spec.key}-{arm}-{index:03}"
    rng = np.random.default_rng(
        np.random.SeedSequence(
            (SEED, spec.index, 2 if confounded else int(planted), index)
        )
    )
    signs = np.zeros(N_GENES, dtype=np.int8)
    if planted:
        signs[:N_PLANTED:2] = -1
        signs[1:N_PLANTED:2] = 1
    assignments = _assignments(
        spec,
        CONFOUNDED_ARM_SIZE if confounded else 10,
        confounded=confounded,
    )
    sampled = _sample_profiles(spec, assignments, rng, signs)
    inventory = _profile_inventory(spec, case_id, assignments, sampled, rng)
    design = compile_contrast_design(inventory, _role(spec))
    by_key = dict(zip(assignments.keys, sampled, strict=True))
    rows: list[dict[str, str]] = []
    matrix = []
    for profile in inventory.profiles:
        key = profile.key
        assert profile.status == "observed"
        assert profile.cell_count == (N_CELLS_PER_PROFILE if spec.single_cell else None)
        np.testing.assert_array_equal(profile.counts, by_key[key])
        target = key.population if spec.regime == "P-P" else key.condition
        assert target is not None
        rows.append(
            {
                "sample_id": profile.profile_id,
                "donor": key.unit_id,
                "target": target,
                "batch": dict(profile.technical_factors)["batch"],
            }
        )
        matrix.append(profile.counts)
    return Cohort(
        case_id,
        CountSource(np.asarray(matrix, dtype=np.int64).T),
        rows,
        _mounted_request(spec, design),
        design,
        signs,
        len(rows) if spec.single_cell else 0,
    )


def stage_solver_inputs(cohort: Cohort, directory: Path) -> None:
    """Use the deployed numeric transport and compare the two design compilers."""
    assert cohort.design.status == "ready"
    assert tuple(row["sample_id"] for row in cohort.rows) == cohort.design.profile_ids
    request = cohort.request
    compiled = compile_mounted_model(cohort.rows, request)
    # A change of reference coding or column ordering retains the same
    # fit and contrast only if both the design and effect projections agree.
    governed = np.asarray(cohort.design.matrix, dtype=float)
    np.testing.assert_allclose(
        governed @ np.linalg.pinv(governed),
        compiled.design @ np.linalg.pinv(compiled.design),
        atol=1e-12,
        rtol=0,
    )
    np.testing.assert_allclose(
        np.asarray(cohort.design.contrast) @ np.linalg.pinv(governed),
        compiled.contrast @ np.linalg.pinv(compiled.design),
        atol=1e-12,
        rtol=0,
    )
    choice = freeze_bulk_solver(
        measurement="raw_counts",
        runtime="bioconductor",
        comparison="coefficient",
        units=request["model"]["units"],
        primary=True,
        single_factor=not request["technical_factors"],
    )
    directory.mkdir()
    normalization = _write_design_inputs(
        directory,
        cohort.source,
        compiled,
        "raw_counts",
        None,
    )
    assert len(normalization["size_factors"]) == cohort.source.shape[1]
    cfg = _fit_config(choice, compiled, cohort.source, request)
    (directory / "model.json").write_text(
        json.dumps(cfg, allow_nan=False),
        encoding="utf-8",
    )


def stage_mounted_request(cohort: Cohort, directory: Path) -> str:
    """Build the selected parity and refusal cases for the normal entry point."""
    directory.mkdir()
    source = directory / "expression.h5"
    names = h5py.string_dtype("utf-8")
    with h5py.File(source, "x") as handle:
        handle.create_dataset("gene_names", data=FEATURES, dtype=names)
        handle.create_dataset(
            "sample_names",
            data=[row["sample_id"] for row in cohort.rows],
            dtype=names,
        )
        handle.create_dataset("expression", data=cohort.source.counts)
    metadata = directory / "metadata.csv"
    with metadata.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=("sample_id", "donor", "target", "batch")
        )
        writer.writeheader()
        writer.writerows(cohort.rows)
    family = directory / "family.json"
    family.write_bytes(
        canonical_json(
            {
                "family_id": cohort.case_id,
                "feature_ids": list(FEATURES),
                "primary": True,
                "numerator": cohort.design.numerator,
                "denominator": cohort.design.denominator,
            }
        )
    )
    request = directory / "request.json"
    request.write_bytes(
        canonical_json(
            {
                **cohort.request,
                "format": "devbench.mounted-bulk/1",
                "expression": source.name,
                "layer": None,
                "expression_sha256": hash_artifact(source)["sha256"],
                "metadata_sha256": hash_artifact(metadata)["sha256"],
                "family_sha256": hash_artifact(family)["sha256"],
            }
        )
    )
    return str(hash_artifact(request)["sha256"])
