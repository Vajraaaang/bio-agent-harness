"""Bounded bulk biological generators, dormant until explicitly authorized.

Import and admission validation use only configuration objects and the standard
library. This module never writes observations. Case support means executable
source is present, not that the generator, a fitting tool, or a scorer is qualified.

Counts have positive marginal means except the explicitly declared structural-zero
stress stratum, whose log effects are unavailable. Negative-binomial and Poisson-lognormal
frailties both have mean one and variance ``dispersion``. Extra donor and latent
module terms are mean-corrected separately. The continuous route models log2
normalized expression and never converts it to integer counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import math
from typing import Any

from .biology import (
    BiologyCatalog, BiologyRecipe, CapabilityRegistration, load_biology_catalog,
)


_CASES = {
    "T01": ("dense", "sparse", "unsampled-pulse"),
    "T02": ("dense", "aliased", "damped", "phase-shift"),
    "T03": ("baseline-only", "shared", "amplitude", "delay", "crossing"),
    "T04": ("bracketed", "unbracketed", "u-shaped", "threshold"),
    "T05": ("whole-null", "weak-low", "variance-only", "responder-mixture", "structural-zero"),
    "T06": ("balanced", "partial", "aliased", "invalid-contrast"),
    "T07": ("independent", "repeated-donors", "technical", "single-unit", "unknown-identity"),
    "T08": ("sex-balanced", "sex-imbalanced", "genotype-balanced", "genotype-rare", "genotype-aliased"),
    "T09": ("valid-controls", "no-controls", "invalid-controls", "dominant-sparse"),
    "T10": ("modules", "hidden-common-cause", "covariance-only"),
    "T11": ("complete-reference", "incomplete-reference", "no-reference", "rna-content-shift", "state-only", "composition-and-state"),
    "T12": ("clean", "outlier", "genuine-extreme", "quality-overlap", "quality-aliased", "mar", "mnar"),
    "T13": ("negative-binomial", "poisson-lognormal", "heavy-tail", "continuous", "continuous-heavy-tail"),
}

# These controls have explicit numerical meanings below. Design-only effect IDs
# must be configured through the unit/grid fields, not meaningless gene amplitudes.
_PROGRAM_IDS = {
    "T01": ("E01", "E02", "E03", "E04", "E08"), "T02": ("E05",),
    "T03": ("E07",), "T04": ("E06",), "T05": ("E08", "E09"),
    "T06": ("E22",), "T07": ("E12",), "T08": ("E13", "E14"),
    "T09": ("E24",), "T10": ("E15",), "T11": ("E10", "E11"),
    "T12": ("E26", "E30"), "T13": ("E29",),
}


@dataclass(frozen=True)
class BulkGenerationPlan:
    case_id: str
    observation_upper_bound: int
    expression_bytes: int
    dense_working_bytes_upper_bound: int
    measurement: str


@dataclass(frozen=True)
class BulkBiologyBundle:
    """Trusted in-memory result; private_truth must never enter participant input.

    ``expression`` is genes by observed libraries. Metadata consists of observed
    library records only. References in public_annotations are permitted assay
    evidence, distinct from the complete simulation truth in private_truth.
    """

    expression: Any
    gene_ids: tuple[str, ...]
    sample_ids: tuple[str, ...]
    metadata: tuple[dict[str, Any], ...]
    measurement: str
    public_context: dict[str, Any]
    public_annotations: dict[str, Any]
    private_truth: dict[str, Any]

    @property
    def observation_ids(self) -> tuple[str, ...]:
        return self.sample_ids


def _withheld_condition(recipe: BiologyRecipe) -> str:
    """Opaque requested label absent from observations; never advertise its absence."""
    index = 0
    while True:
        value = "c_" + sha256(f"{recipe.generation_seed}:condition:{index}".encode()).hexdigest()[:20]
        if value not in recipe.parameters.condition_labels:
            return value
        index += 1


def supported_biology_bulk_cases() -> tuple[str, ...]:
    """Return implemented cases without numerical imports or catalog I/O."""
    return tuple(f"{template}-{case}" for template, cases in _CASES.items() for case in cases)


def validate_biology_bulk_recipe(recipe: BiologyRecipe,
                                 catalog: BiologyCatalog | None = None) -> BulkGenerationPlan:
    """Pure admission checks; no observations, random numbers, imports, or I/O."""
    if not isinstance(recipe, BiologyRecipe):
        raise TypeError("expected BiologyRecipe")
    if recipe.case_id not in supported_biology_bulk_cases():
        raise ValueError(f"bulk case not implemented: {recipe.case_id}")
    if not recipe.case_id.startswith(recipe.template_id + "-"):
        raise ValueError("case and template must agree")
    p = recipe.parameters
    if p.num_genes > 5000:
        raise ValueError("bulk generation is bounded to 5000 genes")
    if p.num_genes < 30:
        raise ValueError("bulk mechanisms require at least 30 genes to allocate controls and effects")
    if p.cells_per_specimen:
        raise ValueError("bulk generation does not create single-cell observations")
    if recipe.generation_seed is None:
        raise ValueError("assign a private generation_seed before generation admission")
    if any(e.effect_id not in _PROGRAM_IDS[recipe.template_id] for e in p.effect_programs):
        raise ValueError("effect program is not parameterized by this bulk implementation")
    if len(p.condition_labels) not in (1, 2):
        raise ValueError("implemented bulk cases use one or two explicit condition levels")
    if recipe.template_id in {"T03", "T05", "T06", "T08", "T09", "T10", "T11"} and len(p.condition_labels) != 2:
        raise ValueError("this contrast requires exactly two condition levels")
    axis = p.dose_grid if recipe.template_id == "T04" else p.time_grid
    if recipe.template_id == "T04" and (not axis or p.time_grid):
        raise ValueError("dose cases require only a dose grid with a vehicle at zero")
    if recipe.template_id != "T04" and p.dose_grid:
        raise ValueError("a dose grid is only implemented for T04")
    if recipe.template_id in {"T01", "T02", "T03", "T07", "T12", "T13"} and len(axis) < 3:
        raise ValueError("temporal cases require at least three declared time points")
    if recipe.case_id == "T01-dense" and len(axis) < 10:
        raise ValueError("dense temporal event timing requires at least ten observations on the time grid")
    if recipe.case_id == "T01-sparse" and len(axis) != 6:
        raise ValueError("sparse temporal case requires exactly six time points")
    if recipe.case_id == "T02-aliased":
        if len(axis) != 6 or any(abs((b - a) - (axis[1] - axis[0])) > 1e-8 for a, b in zip(axis, axis[1:])):
            raise ValueError("aliased rhythm requires six equally spaced time points")
    elif recipe.template_id == "T02" and len(axis) < 12:
        raise ValueError("dense rhythm case requires at least twelve time points")
    if recipe.template_id == "T02" and recipe.case_id != "T02-aliased":
        if max(b - a for a, b in zip(axis, axis[1:])) > (axis[-1] - axis[0]) / 8:
            raise ValueError("dense rhythm grid must resolve the declared one-to-three-cycle frequency domain")
    if recipe.case_id == "T02-phase-shift" and len(p.condition_labels) != 2:
        raise ValueError("phase-shift counterpart requires two conditions")
    if recipe.template_id == "T04" and (len(axis) < 5 or axis[0] != 0):
        raise ValueError("dose cases require vehicle zero and at least four positive dose levels")
    if recipe.template_id == "T06" and p.num_batches < 2:
        raise ValueError("confounding cases require at least two batches")
    if recipe.case_id in {"T06-partial", "T06-aliased"} and p.num_batches != 2:
        raise ValueError("partial and aliased allocation are defined for exactly two batches")
    if p.num_batches > p.independent_units_per_group and recipe.case_id not in {"T06-aliased", "T07-single-unit"}:
        raise ValueError("declared units cannot cover every requested batch")
    if recipe.case_id == "T07-single-unit" and p.independent_units_per_group != 1:
        raise ValueError("single-unit case requires exactly one independent unit per condition")
    if recipe.case_id == "T07-technical" and p.technical_libraries_per_specimen < 2:
        raise ValueError("technical replicate case requires at least two technical libraries per specimen")
    repeated = recipe.case_id in {"T07-repeated-donors", "T07-single-unit"}
    if repeated and p.repeated_specimens_per_unit != len(axis):
        raise ValueError("repeated donors require one repeated specimen per declared time point")
    if not repeated and p.repeated_specimens_per_unit != 1:
        raise ValueError("repeated specimens are only implemented by repeated-donor and single-unit cases")
    if recipe.template_id == "T11" and not 3 <= p.cell_type_count <= 5:
        raise ValueError("bulk mixtures require three to five declared cell types")
    if recipe.template_id != "T11" and p.cell_type_count:
        raise ValueError("cell-type mixture dimensions only apply to T11")
    if recipe.case_id.startswith("T08-genotype-balanced") and p.independent_units_per_group < 6:
        raise ValueError("balanced genotype design requires at least six units per condition-position")
    if recipe.case_id == "T08-sex-imbalanced" and p.independent_units_per_group < 4:
        raise ValueError("sex imbalance with overlap requires at least four units per group")
    if recipe.case_id in {"T08-sex-balanced", "T08-genotype-balanced"}:
        levels = 2 if "sex" in recipe.case_id else 3
        if p.independent_units_per_group < levels * p.num_batches:
            raise ValueError("balanced covariates require units crossed with every batch and covariate level")
    if recipe.case_id in {"T08-sex-imbalanced", "T08-genotype-rare", "T08-genotype-aliased"} and p.num_batches != 1:
        raise ValueError("this covariate stress case uses one batch to isolate its declared mechanism")
    standard_deviation_controls = {"E12", "E15"}
    for program in p.effect_programs:
        expected = ("standard_deviations" if program.effect_id in standard_deviation_controls
                    else "log_variance_ratio" if program.effect_id == "E09"
                    else "fraction_change" if program.effect_id == "E10"
                    else "log2_fold_change")
        if program.amplitude_units != expected:
            raise ValueError(f"{program.effect_id} amplitude must use {expected}")
        if program.effect_id == "E12" and program.gene_fraction != 1:
            raise ValueError("donor random effects apply to all genes; E12 gene_fraction must be one")
        if recipe.case_id == "T05-whole-null" and program.amplitude != 0:
            raise ValueError("whole-null case cannot accept nonzero effect amplitudes")
        if recipe.case_id == "T05-structural-zero":
            if program.effect_id != "E08" or program.amplitude != 0 or not 0 < program.gene_fraction < 1:
                raise ValueError("structural-zero E08 uses a nonempty proper gene fraction and amplitude zero; log effects are undefined")
        if recipe.case_id in {"T05-variance-only", "T05-responder-mixture"} and program.effect_id == "E08" and program.amplitude != 0:
            raise ValueError("equal-mean variability cases cannot accept a mean effect")
        if recipe.case_id == "T05-responder-mixture" and program.effect_id == "E09" and program.amplitude < 0:
            raise ValueError("additional responder heterogeneity cannot reduce the matched baseline variance")
        if recipe.case_id == "T05-variance-only" and program.effect_id == "E09":
            smallest_mean = p.reference_mean * math.exp(-1.2)
            if math.exp(program.amplitude) * (p.dispersion + 1 / smallest_mean) <= 1 / smallest_mean:
                raise ValueError("requested variance ratio falls at or below the Poisson variance bound")
        if program.effect_id == "E10":
            if program.gene_fraction != 1:
                raise ValueError("composition shifts apply to the mixture, so E10 gene_fraction must be one")
            bound = 1. if recipe.case_id == "T11-rna-content-shift" else 1 / p.cell_type_count
            if recipe.case_id == "T11-rna-content-shift":
                if program.amplitude <= -1:
                    raise ValueError("RNA content must remain positive")
            elif abs(program.amplitude) >= bound:
                raise ValueError("composition amplitude must preserve positive cell fractions")
        if recipe.case_id in {"T11-state-only", "T11-composition-and-state"} and program.effect_id == "E11":
            if p.num_genes - int(p.num_genes * program.gene_fraction) < p.cell_type_count + 1:
                raise ValueError("state-change mixture requires enough unchanged reference markers to identify fractions")
    if recipe.template_id == "T01":
        programs = {program.effect_id: program for program in p.effect_programs}
        allocated = sum(programs[key].gene_fraction if key in programs else .16
                        for key in ("E01", "E02", "E03", "E04"))
        allocated += programs["E08"].gene_fraction if "E08" in programs else 0
        if allocated > 1. + 1e-12:
            raise ValueError("effective temporal program fractions including defaults exceed one")
    if catalog is not None:
        recipe.validate_against(catalog)
    n = p.observation_count_upper_bound
    expression_bytes = p.num_genes * n * 8
    # Bound simultaneous count means, draws, variances, truth and temporary arrays.
    # No dense gene-by-gene covariance matrix is constructed.
    working = expression_bytes * 16
    if working > p.max_matrix_bytes or working > 100_000_000:
        raise ValueError("dense working set exceeds the declared 100 MB maximum budget")
    measurement = "continuous" if recipe.case_id in {"T13-continuous", "T13-continuous-heavy-tail"} else "raw_counts"
    return BulkGenerationPlan(recipe.case_id, n, expression_bytes, working, measurement)


def biology_bulk_registrations(catalog: BiologyCatalog) -> tuple[CapabilityRegistration, ...]:
    """Register exact source-supported cases, without claiming tool compatibility."""
    registrations = []
    for template in catalog.templates:
        if template.template_id not in _CASES:
            continue
        for measurement in ("raw_counts", "continuous"):
            cases = tuple(c.case_id for c in template.cases
                          if c.case_id in supported_biology_bulk_cases() and c.measurement == measurement)
            if cases:
                registrations.append(CapabilityRegistration(
                    kind="generator", capability_id=template.generator_capability_id,
                    template_id=template.template_id, case_ids=cases,
                    target_ids=tuple(t.target_id for t in template.targets),
                    measurement=measurement, handler=generate_biology_bulk,
                ))
    return tuple(registrations)


def _opaque_ids(rng: Any, prefix: str, count: int) -> tuple[str, ...]:
    # Independent stream and fixed-width random labels; never encode effect class.
    values: set[str] = set()
    ordered = []
    while len(ordered) < count:
        value = prefix + rng.bytes(12).hex()
        if value not in values:
            values.add(value)
            ordered.append(value)
    return tuple(ordered)


def _layout(recipe: BiologyRecipe, rng: Any, np: Any) -> dict[str, Any]:
    p, case = recipe.parameters, recipe.case_id
    positions = p.dose_grid if recipe.template_id == "T04" else p.time_grid
    positions = positions or (0.,)
    rows, donor_index, specimen_index, reference_index = [], [], [], []
    repeated = case in {"T07-repeated-donors", "T07-single-unit"}
    donors: dict[tuple[int, ...], int] = {}
    specimen = 0
    for condition, label in enumerate(p.condition_labels):
        for position, value in enumerate(positions):
            for unit in range(p.independent_units_per_group):
                key = (condition, unit) if repeated else (condition, position, unit)
                donor = donors.setdefault(key, len(donors))
                if case == "T06-aliased":
                    batch = condition
                elif case == "T06-partial":
                    # At least one cross-batch unit remains in each condition-cell.
                    batch = 1 - condition if unit == 0 else condition
                else:
                    batch = unit % p.num_batches
                for library in range(p.technical_libraries_per_specimen):
                    row = {"condition": label, "batch": f"batch_{batch + 1}"}
                    row["dose" if recipe.template_id == "T04" else "time"] = float(value)
                    if recipe.template_id == "T08":
                        if "sex" in case:
                            covariate = (unit // p.num_batches) % 2 if case == "T08-sex-balanced" else int(unit == 0) if condition == 0 else int(unit != 0)
                            row["sex"] = "stratum_A" if covariate == 0 else "stratum_B"
                        elif case == "T08-genotype-aliased":
                            row["genotype_dosage"] = 2 * condition
                        elif case == "T08-genotype-rare":
                            row["genotype_dosage"] = int(unit == 0 and position == 0)
                        else:
                            row["genotype_dosage"] = (unit // p.num_batches) % 3
                    rows.append(row)
                    donor_index.append(donor)
                    specimen_index.append(specimen)
                    reference_index.append(condition * len(positions) + position)
                specimen += 1
    sample_ids = _opaque_ids(rng, "s_", len(rows))
    donor_ids = _opaque_ids(rng, "u_", len(donors))
    specimen_ids = _opaque_ids(rng, "p_", specimen)
    for index, row in enumerate(rows):
        row.update(sample_id=sample_ids[index], library_id=sample_ids[index],
                   specimen_id=specimen_ids[specimen_index[index]],
                   donor_id=None if case == "T07-unknown-identity" else donor_ids[donor_index[index]])
    return {"rows": rows, "donor": np.asarray(donor_index),
            "specimen": np.asarray(specimen_index), "reference": np.asarray(reference_index),
            "donor_ids": donor_ids, "sample_ids": sample_ids,
            "positions": positions, "n_donors": len(donors), "n_specimens": specimen}


def _setting(recipe: BiologyRecipe, effect_id: str, fraction: float, amplitude: float,
             *, units: str = "log2_fold_change") -> tuple[float, float, float, float]:
    program = next((p for p in recipe.parameters.effect_programs if p.effect_id == effect_id), None)
    if program is None:
        return fraction, amplitude, .5, .14
    if program.amplitude_units != units:
        raise ValueError(f"{effect_id} amplitude must use {units} in this case")
    return (program.gene_fraction, program.amplitude,
            .5 if program.onset_fraction is None else program.onset_fraction,
            .14 if program.width_fraction is None else program.width_fraction)


def _wave(x: Any, shape: str, onset: float, width: float, np: Any) -> Any:
    if shape == "linear":
        return x
    if shape == "accelerating":
        return x ** 2
    if shape == "decelerating":
        return np.sqrt(x)
    if shape == "saturating":
        return -np.expm1(-5 * x) / -np.expm1(-5.)
    if shape == "pulse":
        # Compact support gives a truly unsampled impulse exactly zero on grid.
        return np.maximum(1 - ((x - onset) / width) ** 2, 0.) ** 2
    if shape == "delayed":
        curve = 1 / (1 + np.exp(-(x - onset) / max(width, .01)))
        return curve - curve[0]
    if shape == "biphasic":
        return _wave(x, "pulse", .3, .18, np) - .8 * _wave(x, "pulse", .7, .18, np)
    if shape == "reversal":
        return 1 - np.abs(2 * x - 1)
    raise ValueError(f"unimplemented biological shape: {shape}")


def _biological_mean(recipe: BiologyRecipe, layout: dict[str, Any], rng: Any,
                     np: Any) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """Construct clean marginal population means on the declared reference grid."""
    p, case, template = recipe.parameters, recipe.case_id, recipe.template_id
    g, c, nt = p.num_genes, len(p.condition_labels), len(layout["positions"])
    axis = np.asarray(layout["positions"], dtype=float)
    x = (axis - axis[0]) / (axis[-1] - axis[0]) if nt > 1 else np.zeros(1)
    baseline = p.reference_mean * np.exp(rng.uniform(-1.2, 1.2, g))
    log_effect = np.zeros((g, c, nt))
    order = rng.permutation(g)
    shapes = ["flat"] * g
    details: dict[str, Any] = {"baseline": baseline, "normalized_axis": x,
                               "shapes": shapes, "gene_order": order, "design_parameters": {}}
    annotations: dict[str, Any] = {}

    def selected(fraction: float, start: int = 0) -> Any:
        return order[start:start + int(g * fraction)]

    def temporal(amplitude: float = 1.2, fraction: float = .4) -> None:
        idx = selected(fraction)
        for j, gene in enumerate(idx):
            shape = ("linear", "pulse", "delayed", "reversal")[j % 4]
            value = amplitude * (-1 if j % 2 else 1) * _wave(x, shape, .55, .17, np)
            log_effect[gene, :, :] = value
            shapes[gene] = shape

    if template == "T01":
        start = 0
        for effect, wave_names in (("E01", ("linear", "accelerating", "decelerating", "saturating")),
                                   ("E02", ("pulse",)), ("E03", ("delayed",)),
                                   ("E04", ("biphasic", "reversal"))):
            fraction, amplitude, onset, width = _setting(recipe, effect, .16, 1.4)
            idx = selected(fraction, start)
            start += len(idx)
            if case == "T01-unsampled-pulse" and effect == "E02":
                gap = int(np.argmax(np.diff(x)))
                onset, width = float((x[gap] + x[gap + 1]) / 2), float((x[gap + 1] - x[gap]) * .4)
            for j, gene in enumerate(idx):
                shape = wave_names[j % len(wave_names)]
                signed_amplitude = amplitude * (-1 if (j // len(wave_names)) % 2 else 1)
                log_effect[gene] = signed_amplitude * _wave(x, shape, onset, width, np)
                shapes[gene] = ("negative_" if signed_amplitude < 0 else "positive_") + shape
            details[effect] = {"gene_indices": idx, "onset_fraction": onset, "width_fraction": width,
                               "amplitude_log2": amplitude, "shapes": wave_names}
        if any(e.effect_id == "E08" for e in p.effect_programs):
            fraction, _, _, _ = _setting(recipe, "E08", .1, .1)
            baseline[selected(fraction, start)] *= .01
    elif template == "T02":
        fraction, amplitude, _, _ = _setting(recipe, "E05", .5, 1.3)
        idx = selected(fraction)
        phase = rng.uniform(-np.pi, np.pi, len(idx))
        damping = 1.5 if case == "T02-damped" else 0.
        for j, gene in enumerate(idx):
            for condition in range(c):
                shift = condition * np.pi / 3 if case == "T02-phase-shift" else 0.
                log_effect[gene, condition] = amplitude * np.exp(-damping * x) * np.sin(4 * np.pi * x + phase[j] + shift)
            shapes[gene] = "damped_rhythm" if damping else "rhythm"
        # Preserve the sine-plus-phase basis while representing a negative
        # amplitude by a nonnegative amplitude and a pi phase shift.
        canonical_phase = (phase + (np.pi if amplitude < 0 else 0) + np.pi) % (2 * np.pi) - np.pi
        details["rhythm"] = {"gene_indices": idx, "period": float((axis[-1] - axis[0]) / 2),
                             "amplitude_log2_nonnegative": abs(amplitude),
                             "amplitude_convention": "nonnegative",
                             "phase_convention": "sine_plus_phase",
                             "phase_radians": canonical_phase, "damping_per_window": damping,
                             "phase_radians_by_condition": {
                                 label: (canonical_phase + (condition * np.pi / 3 if case == "T02-phase-shift" else 0) + np.pi) % (2 * np.pi) - np.pi
                                 for condition, label in enumerate(p.condition_labels)},
                             "phase_reference_time": float(axis[0]),
                             "damping_per_time_unit": damping / float(axis[-1] - axis[0]),
                             "aliased_alternative_period": float((axis[-1] - axis[0]) / 3) if case == "T02-aliased" else None,
                             "aliased_alternative_phase": (np.pi - canonical_phase + np.pi) % (2 * np.pi) - np.pi if case == "T02-aliased" else None}
    elif template == "T03":
        fraction, amplitude, onset, width = _setting(recipe, "E07", .5, 1.3)
        for j, gene in enumerate(selected(fraction)):
            sign = (-1 if j % 2 else 1)
            shared = sign * amplitude * _wave(x, "saturating", onset, width, np)
            log_effect[gene, 0] = shared
            if case == "T03-baseline-only":
                log_effect[gene, 1] = shared + sign * .8
            elif case == "T03-shared":
                log_effect[gene, 1] = shared
            elif case == "T03-amplitude":
                log_effect[gene, 1] = 1.7 * shared
            elif case == "T03-delay":
                log_effect[gene, 1] = sign * amplitude * _wave(x, "delayed", .65, .1, np)
            else:  # T03-crossing: both the main and interaction targets are explicit.
                log_effect[gene, 0] = sign * amplitude * (x - .5)
                log_effect[gene, 1] = -log_effect[gene, 0]
            shapes[gene] = "condition_response"
    elif template == "T04":
        fraction, amplitude, _, _ = _setting(recipe, "E06", .5, 1.5)
        ec50 = float(axis[-1] * (4 if case == "T04-unbracketed" else .35))
        hill = 10 if case == "T04-threshold" else 2
        scaled = axis / ec50
        response = scaled ** hill / (1 + scaled ** hill)
        if case == "T04-u-shaped":
            response = 4 * (x - .5) ** 2 - 1
        for j, gene in enumerate(selected(fraction)):
            log_effect[gene] = amplitude * (-1 if j % 2 else 1) * response
            shapes[gene] = "u_shaped" if case == "T04-u-shaped" else "hill"
        details["dose"] = {"gene_indices": selected(fraction),
                           "interpretation": "half-maximal effect on log2 expression scale",
                           "unique_monotone_target": case != "T04-u-shaped"}
        if case == "T04-u-shaped":
            details["dose"]["half_response_root_set"] = (
                float(axis[-1] * (1 - np.sqrt(.5)) / 2),
                float(axis[-1] * (1 + np.sqrt(.5)) / 2),
            )
        else:
            details["dose"].update(effective_concentration=ec50, hill_coefficient=hill)
    elif template == "T05":
        if case == "T05-weak-low":
            fraction, amplitude, _, _ = _setting(recipe, "E08", .5, .12)
            baseline[order[:g // 3]] = .05
            baseline[order[g // 3:2 * g // 3]] = .5
            log_effect[selected(fraction), 1, :] = amplitude
        elif case == "T05-structural-zero":
            fraction, _, _, _ = _setting(recipe, "E08", .15, 0.)
            zero_genes = selected(fraction)
            if not len(zero_genes) or len(zero_genes) == g:
                raise ValueError("structural-zero recipe must allocate both zero-mean and positive-mean genes")
            baseline[zero_genes] = 0.
            zero_mask = np.zeros(g, dtype=bool)
            zero_mask[zero_genes] = True
            details["structural_zero_gene_mask"] = zero_mask
            details["structural_zero_interpretation"] = "simulation zero-mean mechanism; finite observed zero counts alone do not identify biological absence"
        # Whole null, dispersion-only and responder mixture have equal mean in
        # every condition and position. Variance enters the sampler, never mean.
        details["variance_gene_indices"] = selected(_setting(recipe, "E09", .5, 1.38629436112,
                                                              units="log_variance_ratio")[0])
    elif template == "T06":
        log_effect[selected(.4), 1] = 1.2
        details["design_parameters"]["requested_condition"] = _withheld_condition(recipe) if case == "T06-invalid-contrast" else p.condition_labels[1]
    elif template == "T07":
        temporal()
    elif template == "T08":
        effect = "E13" if "sex" in case else "E14"
        fraction, amplitude, _, _ = _setting(recipe, effect, .4, .6)
        slopes = np.zeros((g, c, nt))
        slopes[selected(fraction), 0, :] = amplitude
        slopes[selected(fraction), 1, :] = amplitude * (1 + .8 * (x if nt > 1 else np.ones(1)))
        details["covariate_slopes_log2"] = slopes
        details["covariate_reference"] = 0
        # Reference is conditional at dosage/stratum zero, not an average over
        # observed (potentially different) covariate proportions in each group.
    elif template == "T09":
        fraction, amplitude, _, _ = _setting(recipe, "E24", .8, 1.2)
        if case == "T09-dominant-sparse":
            idx = selected(.03)
            baseline[idx] *= 50
            log_effect[idx, 1] = 3.
        else:
            log_effect[selected(fraction), 1] = amplitude
        # Distinct controls have externally supplied quantities, not post-hoc
        # selected stable endogenous genes. Invalid controls change with biology.
        controls = order[-max(3, g // 20):]
        if case == "T09-valid-controls":
            log_effect[controls] = 0
        elif case == "T09-invalid-controls":
            log_effect[controls, 1] = amplitude
        details["control_indices"] = controls
        details["control_expected_abundance"] = baseline[controls].copy()
    elif template == "T10":
        if case != "T10-covariance-only":
            temporal(.9, .25)
        fraction, amplitude, _, _ = _setting(recipe, "E15", .7, .5, units="standard_deviations")
        loadings = np.zeros((g, 3))
        for j, gene in enumerate(selected(fraction)):
            loadings[gene, j % 3] = amplitude * (-1 if j % 2 else 1)
            if case == "T10-hidden-common-cause":
                loadings[gene, 0] += amplitude * .5
        details["module_loadings"] = loadings
    elif template == "T11":
        k = p.cell_type_count
        profiles = rng.lognormal(0, .6, (g, k))
        for j, gene in enumerate(order):
            profiles[gene, j % k] *= 8
        profiles /= profiles.sum(axis=0, keepdims=True)
        fractions = np.full((k, c, nt), 1 / k)
        _, composition_amplitude, _, _ = _setting(recipe, "E10", 1., .18, units="fraction_change")
        shift = composition_amplitude * (x if nt > 1 else np.ones(1))
        fractions[0, 1] += shift
        fractions[1, 1] -= shift
        rna_content = np.broadcast_to(np.linspace(.7, 1.7, k)[:, None, None], (k, c, nt)).copy()
        if case == "T11-rna-content-shift":
            # This counterpart changes RNA content alone; cell fractions stay fixed.
            fractions[:] = 1 / k
            _, rna_amplitude, _, _ = _setting(recipe, "E10", 1., 1.5, units="fraction_change")
            rna_content[0, 1] *= 1 + rna_amplitude * (x if nt > 1 else np.ones(1))
        intrinsic = np.zeros((g, k))
        changing_profiles = np.broadcast_to(profiles[:, :, None, None], (g, k, c, nt)).copy()
        if case in {"T11-state-only", "T11-composition-and-state"}:
            if case == "T11-state-only":
                fractions[:] = 1 / k
            fraction, amplitude, _, _ = _setting(recipe, "E11", .15, 1.)
            idx = selected(fraction)
            if len(idx) >= 2:
                signed = amplitude * np.where(np.arange(len(idx)) % 2, -1., 1.)
                changed = profiles[idx, 0] * np.exp2(signed)
                # Redistribute RNA within the changed subset. The complement
                # retains exactly unchanged relative expression as valid anchors.
                changed *= profiles[idx, 0].sum() / changed.sum()
                changing_profiles[idx, 0, 1, :] = changed[:, None]
                intrinsic[:, 0] = np.log2(changing_profiles[:, 0, 1, 0] / profiles[:, 0])
        abundance = np.einsum("gkct,kct->gct", changing_profiles, fractions * rna_content) * p.reference_mean * g
        rna_fraction = fractions * rna_content
        rna_fraction /= rna_fraction.sum(axis=0, keepdims=True)
        if case in {"T11-state-only", "T11-composition-and-state"}:
            stable = np.flatnonzero(np.all(np.abs(intrinsic) < 1e-12, axis=1))
            if len(stable) < k + 1 or np.linalg.matrix_rank(profiles[stable]) < k:
                raise ValueError("stable reference projection cannot identify all cell-type mixture weights")
        details["mixture"] = {"cell_fractions": fractions.reshape(k, -1),
                              "rna_fractions": rna_fraction.reshape(k, -1),
                              "rna_content": rna_content.reshape(k, -1), "profiles": profiles,
                              "within_cell_type_effect": intrinsic,
                              "state_change": case in {"T11-state-only", "T11-composition-and-state"}}
        return abundance.reshape(g, -1), details, annotations
    elif template == "T12":
        temporal()
    elif template == "T13":
        fraction, amplitude, _, _ = _setting(recipe, "E29", .4, 1.2)
        temporal(amplitude, fraction)
    else:
        raise ValueError(f"bulk template not implemented: {template}")
    mean = baseline[:, None, None] * np.exp2(log_effect)
    details["log2_effect"] = log_effect
    return mean.reshape(g, -1), details, annotations


def _observe(recipe: BiologyRecipe, plan: BulkGenerationPlan, layout: dict[str, Any],
             population: Any, details: dict[str, Any], streams: dict[str, Any], np: Any
             ) -> tuple[Any, Any, dict[str, Any]]:
    """Apply only the nuisance/measurement mechanism declared by the case."""
    p, case, template = recipe.parameters, recipe.case_id, recipe.template_id
    g, n = p.num_genes, len(layout["rows"])
    reference = layout["reference"]
    n_positions = len(layout["positions"])
    condition = reference // n_positions
    x = details["normalized_axis"][reference % n_positions]
    mean = population[:, reference].copy()
    dispersion = np.full((g, n), p.dispersion)
    extra: dict[str, Any] = {}
    # Reference targets marginalize these factors to one. Technical libraries
    # share donor/specimen effects and only receive independent sequencing noise.
    if template == "T07":
        _, sigma, _, _ = _setting(recipe, "E12", 1., .3, units="standard_deviations")
        sigma = abs(sigma)
        intercept = streams["donor"].normal(0, sigma, (g, layout["n_donors"]))
        slopes = streams["donor"].normal(0, sigma / 2, (g, layout["n_donors"]))
        donor_log = intercept[:, layout["donor"]] + slopes[:, layout["donor"]] * x
        donor_log -= .5 * (sigma ** 2 + (sigma / 2 * x) ** 2)
        mean *= np.exp(donor_log)
        specimen_sigma = .1
        specimen = streams["donor"].normal(0, specimen_sigma, (g, layout["n_specimens"]))
        mean *= np.exp(specimen[:, layout["specimen"]] - .5 * specimen_sigma ** 2)
        extra["unit_effects"] = {"intercepts": intercept, "slopes": slopes,
                                 "marginal_mean_correction": True}
    if template == "T06":
        fraction, amplitude, _, _ = _setting(recipe, "E22", .5, .7)
        gene_batch = streams["donor"].normal(0, abs(amplitude), (g, p.num_batches))
        gene_batch[details["gene_order"][int(g * fraction):]] = 0
        batch_index = np.asarray([int(row["batch"].split("_")[-1]) - 1 for row in layout["rows"]])
        mean *= np.exp2(gene_batch[:, batch_index])
        extra["batch_effect_log2"] = gene_batch
    if template == "T08":
        covariates = np.asarray([
            int(row["sex"] == "stratum_B") if "sex" in row else row["genotype_dosage"]
            for row in layout["rows"]
        ])
        slopes = details["covariate_slopes_log2"].reshape(g, -1)
        mean *= np.exp2(slopes[:, reference] * covariates)
    if template == "T10":
        loadings = details["module_loadings"]
        scales = np.where(condition == 1, 1.7, 1.) if case == "T10-covariance-only" else np.ones(n)
        # Factor draws live at specimen level; technical libraries remain linked.
        factors = streams["modules"].normal(size=(3, layout["n_specimens"]))[:, layout["specimen"]] * scales
        variance = (loadings ** 2).sum(axis=1)[:, None] * scales[None, :] ** 2
        mean *= np.exp(loadings @ factors - .5 * variance)
        extra["latent_factor_scales"] = scales
    if case == "T05-variance-only":
        _, log_ratio, _, _ = _setting(recipe, "E09", .5, 1.38629436112, units="log_variance_ratio")
        idx = details["variance_gene_indices"]
        reference_mu = population[idx, :n_positions].mean(axis=1)
        phi_b = np.exp(log_ratio) * p.dispersion + np.expm1(log_ratio) / reference_mu
        if np.any(phi_b <= 0) or not np.all(np.isfinite(phi_b)):
            raise ValueError("matched variance target is not attainable by a positive-dispersion count model")
        dispersion[np.ix_(idx, np.flatnonzero(condition == 1))] = phi_b[:, None]
        extra["target_log_variance_ratio"] = log_ratio
    if case in {"T05-responder-mixture", "T12-genuine-extreme"}:
        idx = details["variance_gene_indices"] if template == "T05" else details["gene_order"][:g // 3]
        affected = np.flatnonzero(condition == 1) if template == "T05" else np.arange(n)
        if template == "T05":
            _, log_ratio, _, _ = _setting(recipe, "E09", .5, 1.38629436112, units="log_variance_ratio")
            reference_mu = population[idx, :n_positions].mean(axis=1)
            multiplier_variance = np.expm1(log_ratio) * (1 / reference_mu + p.dispersion) / (1 + p.dispersion)
            probability = min(.25, .5 / (1 + float(multiplier_variance.max(initial=0.))))
            high = 1 + np.sqrt(multiplier_variance * (1 - probability) / probability)
            low = 1 - np.sqrt(multiplier_variance * probability / (1 - probability))
        else:
            probability, high = .1, 2.8
            low = (1 - probability * high) / (1 - probability)
            multiplier_variance = probability * (high - 1) ** 2 + (1 - probability) * (low - 1) ** 2
        responding = streams["donor"].random(layout["n_donors"]) < probability
        if template == "T05":
            multiplier = np.where(responding[layout["donor"]][None, :], high[:, None], low[:, None])
            mean[np.ix_(idx, affected)] *= multiplier[:, affected]
        else:
            multiplier = np.where(responding[layout["donor"]], high, low)
            mean[np.ix_(idx, affected)] *= multiplier[affected]
        extra["response_mixture"] = {"probability": probability, "high": high, "low": low,
                                     "gene_indices": idx, "responding_donors": responding,
                                     "mean_multiplier": probability * high + (1 - probability) * low,
                                     "variance_multiplier": multiplier_variance}
    quality = None
    if case in {"T12-quality-overlap", "T12-quality-aliased", "T12-mar", "T12-mnar"}:
        if case == "T12-quality-aliased":
            quality = .2 + .8 * x
        else:
            quality = streams["exposure"].uniform(.2, 1., layout["n_specimens"])[layout["specimen"]]
        for row, value in zip(layout["rows"], quality):
            row["quality_score"] = float(value)
        if "quality" in case:
            fraction, amplitude, _, _ = _setting(recipe, "E30", .35, 1.)
            susceptibility = np.zeros(g)
            susceptibility[details["gene_order"][:int(g * fraction)]] = amplitude
            mean *= np.exp2(-susceptibility[:, None] * (1 - quality))
            extra["quality_susceptibility_log2"] = susceptibility
    # Exposure is independent of biological total. Fixed-total sequencing below
    # preserves the impossibility of identifying global RNA changes from totals.
    exposure_sigma = .25 + .1 * p.nuisance_intensity
    exposure = np.exp(streams["exposure"].normal(-.5 * exposure_sigma ** 2, exposure_sigma, n))
    if plan.measurement == "continuous":
        continuous_mean = np.log2(mean)
        residual_sd = p.dispersion ** .5
        residual_shape = (g, layout["n_specimens"])
        if case == "T13-continuous-heavy-tail":
            # Student-t(5) scaled to unit variance, then to the declared variance.
            residual = streams["noise"].standard_t(5, size=residual_shape) * (3 / 5) ** .5
        else:
            residual = streams["noise"].normal(size=residual_shape)
        expression = continuous_mean + residual_sd * residual[:, layout["specimen"]]
        extra["continuous_noise_variance"] = p.dispersion
        extra["continuous_noise_scope"] = "biological specimen; technical libraries share the residual"
    elif template == "T09":
        totals = np.maximum(1, np.rint(p.reference_mean * g * exposure)).astype(np.int64)
        expression = np.empty((g, n), dtype=np.int64)
        for library in range(n):
            probabilities = mean[:, library] / mean[:, library].sum()
            expression[:, library] = streams["noise"].multinomial(int(totals[library]), probabilities)
        extra["fixed_sequencing_totals"] = totals
    else:
        mean *= exposure
        _, first_libraries = np.unique(layout["specimen"], return_index=True)
        specimen_phi = dispersion[:, first_libraries]
        if case == "T13-poisson-lognormal":
            sigma2 = np.log1p(specimen_phi)
            specimen_frailty = np.exp(streams["noise"].normal(-.5 * sigma2, np.sqrt(sigma2)))
        elif case == "T13-heavy-tail":
            # A two-component gamma mixture, mean-matched and variance-matched
            # to NB but with different higher moments. No unbounded t-log mean.
            component = streams["noise"].random(specimen_phi.shape) < .1
            local_phi = np.where(component, 5.5 * specimen_phi, .5 * specimen_phi)
            specimen_frailty = streams["noise"].gamma(1 / local_phi, local_phi)
        else:
            specimen_frailty = streams["noise"].gamma(1 / specimen_phi, specimen_phi)
        rate = mean * specimen_frailty[:, layout["specimen"]]
        if not np.all(np.isfinite(rate)) or np.any(rate < 0) or np.any(rate > 1e12):
            raise ValueError("count rate exceeds finite safe sampling bounds")
        expression = streams["noise"].poisson(rate)
        extra["count_frailty_scope"] = "biological specimen; technical libraries share frailty and have independent Poisson sampling"
    if case == "T12-outlier":
        fraction, amplitude, _, _ = _setting(recipe, "E26", .01, 4.)
        affected_count = max(1, int(g * fraction)) if fraction and amplitude else 0
        gene_index = streams["contamination"].choice(g, affected_count, replace=False)
        library_index = streams["contamination"].integers(0, n, affected_count)
        for gene, library in zip(gene_index, library_index):
            expression[gene, library] += int(np.ceil(mean[gene, library] * (np.exp2(abs(amplitude)) - 1)))
        extra["contamination_coordinates"] = tuple((int(gene), int(library)) for gene, library in zip(gene_index, library_index))
    observed = np.ones(n, dtype=bool)
    if case in {"T12-mar", "T12-mnar"}:
        # Fixed log-odds selection coefficient. EffectProgram has no log-odds
        # unit, so unsupported E27 gene-amplitude overrides are rejected above.
        amplitude = 1.
        severity = streams["missingness"].normal(size=layout["n_donors"])
        if case == "T12-mar":
            selection_score = 2 * (1 - quality)
        else:
            # Dependence on the latent realized response makes this MNAR for
            # expression even after conditioning on the observed quality score.
            response = np.log1p(expression[details["gene_order"][:max(1, g // 3)]]).mean(axis=0)
            selection_score = severity[layout["donor"]] + response - float(response.mean())
        specimen_score = (np.bincount(layout["specimen"], weights=selection_score)
                          / np.bincount(layout["specimen"]))
        selection_score = specimen_score[layout["specimen"]]
        missing_probability = 1 / (1 + np.exp(2.5 - abs(amplitude) * selection_score))
        # Whole-specimen dropout, including every technical library of a specimen.
        draws = streams["missingness"].random(layout["n_specimens"])[layout["specimen"]]
        observed = draws >= missing_probability
        extra["missingness"] = {"mechanism": "MAR" if case.endswith("mar") and not case.endswith("mnar") else "MNAR",
                                 "observation_mask": observed.copy(),
                                 "missing_probabilities": missing_probability,
                                 "complete_population_scope": "pre-selection population"}
    if not np.all(np.isfinite(expression)):
        raise ValueError("observation process produced a nonfinite expression value")
    extra["library_exposure"] = exposure
    extra["library_exposure_log_sd"] = exposure_sigma
    extra["conditional_dispersion"] = dispersion
    extra["observed_nonzero_gene_mask"] = np.any(expression[:, observed] != 0, axis=1)
    return expression[:, observed], observed, extra


def _public_annotations(recipe: BiologyRecipe, gene_ids: tuple[str, ...], details: dict[str, Any],
                        extra: dict[str, Any], np: Any) -> dict[str, Any]:
    case = recipe.case_id
    annotations: dict[str, Any] = {}
    if case in {"T09-valid-controls", "T09-invalid-controls"}:
        annotations["declared_external_controls"] = {
            "gene_ids": tuple(gene_ids[int(i)] for i in details["control_indices"]),
            "reference_abundance": details["control_expected_abundance"],
            "units": "declared molecules per standardized biological input",
            "protocol": "constant externally specified input per specimen; validity requires assessment",
            "input_verification": {
                "source": "independent preparation record supplied as assay evidence",
                "status": "verified_reference_input" if case == "T09-valid-controls" else "reference_input_not_verified",
                "quantity_scope": "verified amount per biological input" if case == "T09-valid-controls" else "nominal amount only; realized input and absolute scale unavailable",
            },
        }
    if case == "T12-quality-overlap":
        annotations["gene_susceptibility"] = {
            "gene_ids": gene_ids, "log2_attenuation_per_quality_deficit": extra["quality_susceptibility_log2"],
            "reference": "independently specified assay susceptibility annotation",
        }
    if recipe.template_id == "T11" and case != "T11-no-reference":
        mixture = details["mixture"]
        k = recipe.parameters.cell_type_count
        available = k - 1 if case == "T11-incomplete-reference" else k
        annotations["cell_type_reference"] = {
            "gene_ids": gene_ids, "cell_type_ids": tuple(f"population_{i + 1}" for i in range(available)),
            "relative_expression": mixture["profiles"][:, :available],
            "rna_content": mixture["rna_content"][:available],
            "expression_units": "within-cell-type relative expression",
            "rna_content_units": "relative RNA amount per cell; assay reference",
        }
        if case in {"T11-state-only", "T11-composition-and-state"}:
            stable = np.flatnonzero(np.all(np.abs(mixture["within_cell_type_effect"]) < 1e-12, axis=1))
            annotations["stable_reference_markers"] = tuple(gene_ids[int(i)] for i in stable)
    return annotations


def _check_observed_support(recipe: BiologyRecipe, catalog: BiologyCatalog,
                            rows: tuple[dict[str, Any], ...]) -> None:
    """Fail instead of silently returning a realization below declared support."""
    template = catalog.template(recipe.template_id)
    case = next(case for case in template.cases if case.case_id == recipe.case_id)
    if not rows:
        raise ValueError("no observed libraries remain")
    if len({r["sample_id"] for r in rows}) != len(rows):
        raise ValueError("observed sample labels must be unique")
    for scope in case.target_scopes:
        if scope.expected_scope != "supported":
            continue
        target = next(target for target in template.targets if target.target_id == scope.target_id)
        support = target.minimum_design_support
        for field in support.required_labels:
            if any(field not in row or row[field] is None for row in rows):
                raise ValueError(f"observed metadata lacks required label {field}")
        conditions = {r["condition"] for r in rows}
        if len(conditions) < support.minimum_conditions:
            raise ValueError("observed condition support is below the declared minimum")
        grid_field = "dose" if recipe.template_id == "T04" else "time"
        required_positions = support.minimum_dose_levels if grid_field == "dose" else support.minimum_timepoints
        if len({r[grid_field] for r in rows}) < required_positions:
            raise ValueError("observed grid support is below the declared minimum")
        groups: dict[tuple[Any, ...], set[str]] = {}
        for row in rows:
            key = (row["condition"], row.get(grid_field))
            groups.setdefault(key, set()).add(row["donor_id"])
        positions = recipe.parameters.dose_grid if grid_field == "dose" else recipe.parameters.time_grid
        expected_groups = ((condition, float(position))
                           for condition in recipe.parameters.condition_labels for position in (positions or (0.,)))
        if any(len(groups.get(group, set())) < support.minimum_independent_units_per_group for group in expected_groups):
            raise ValueError("observed independent units are below declared support; no automatic resampling")


def _sampled_events(curves: Any, shapes: list[str], axis: tuple[float, ...], np: Any
                    ) -> tuple[tuple[dict[str, Any], ...], ...]:
    """Events bracketed by observations; no exact continuous-time oracle score."""
    events = []
    for curve, shape in zip(curves, shapes):
        gene_events = []
        differences = np.diff(curve)
        active = np.flatnonzero(np.abs(differences) > 1e-10)
        for left, right in zip(active, active[1:]):
            if differences[left] * differences[right] < 0:
                gene_events.append({
                    "kind": "peak" if differences[left] > 0 else "trough",
                    "interval": (float(axis[int(left)]), float(axis[int(right) + 1])),
                    "reference": "sampled change in direction, including any intervening plateau",
                })
        if "delayed" in shape and abs(curve[-1] - curve[0]) > 1e-10:
            direction = 1 if curve[-1] > curve[0] else -1
            threshold = direction * (curve[-1] - curve[0]) * .5
            crossed = np.flatnonzero(direction * (curve - curve[0]) >= threshold)
            if len(crossed) and crossed[0] > 0:
                index = int(crossed[0])
                gene_events.append({"kind": "half_observed_response",
                                    "interval": (float(axis[index - 1]), float(axis[index])),
                                    "reference": "half of the observed endpoint excursion; not a mechanistic onset constant"})
        if "biphasic" in shape:
            nonzero = np.flatnonzero(np.abs(curve - curve[0]) > 1e-10)
            for left, right in zip(nonzero, nonzero[1:]):
                if (curve[left] - curve[0]) * (curve[right] - curve[0]) < 0:
                    gene_events.append({"kind": "baseline_crossing_reversal",
                                        "interval": (float(axis[int(left)]), float(axis[int(right)])),
                                        "reference": "positive-to-negative or negative-to-positive sampled excursion"})
        events.append(tuple(gene_events))
    return tuple(events)


def _target_truth(recipe: BiologyRecipe, catalog: BiologyCatalog, population: Any,
                  details: dict[str, Any], layout: dict[str, Any], extra: dict[str, Any],
                  observed: Any, measurement: str, np: Any) -> dict[str, Any]:
    p = recipe.parameters
    template = catalog.template(recipe.template_id)
    case = next(case for case in template.cases if case.case_id == recipe.case_id)
    g, c, nt = p.num_genes, len(p.condition_labels), len(layout["positions"])
    # Temporary finite placeholders are masked as unavailable before returning
    # any structural-zero log-effect target; they are never eligible zero effects.
    log_population = np.zeros_like(population)
    np.log2(population, out=log_population, where=population > 0)
    log_mean = log_population.reshape(g, c, nt)
    temporal = log_mean - log_mean[:, :, :1]
    contrast = log_mean[:, 1] - log_mean[:, 0] if c == 2 else temporal[:, 0]
    targets: dict[str, Any] = {}
    for target in template.targets:
        scope = next(s for s in case.target_scopes if s.target_id == target.target_id)
        truth = {"scope": scope.expected_scope, "expected_refusal": scope.expected_scope == "refusal",
                 "estimand": target.estimand, "units": target.measurement_units,
                 "contrast": target.contrast, "prediction_reference": target.prediction_reference,
                 "reference_population": target.reference_population,
                 "numerical_scoring": scope.numerical_scoring,
                 "required_limitation": scope.required_limitation,
                 "forbidden_claims": target.forbidden_claims}
        effect: Any = None
        name = target.target_id
        if name in {"sampled_mean", "clean_population_effect"}:
            effect = temporal.reshape(g, -1)
            truth["curve"] = np.log2(population) if measurement == "continuous" else population
            if name == "sampled_mean" and recipe.case_id in {"T12-quality-aliased", "T12-mnar"}:
                effect = None
                truth["latent_simulation_truth"] = {"clean_population_curve": truth.pop("curve")}
                truth["numerical_reference_available"] = False
                truth["restricted_reference"] = "Observed-support and assumption diagnostics only; no identified clean-population numerical target."
        elif name == "condition_main":
            effect = contrast[:, 0]
        elif name == "time_interaction":
            # This is difference-in-differences on the log2 scale, not the
            # omnibus time effect and not a baseline condition difference.
            effect = contrast - contrast[:, :1]
        elif name in {"mean_contrast", "adjusted_effect", "observed_difference", "tissue_effect"}:
            effect = contrast
            if name == "adjusted_effect" and recipe.case_id == "T06-invalid-contrast":
                effect = None
                truth["invalid_contrast"] = {"numerator": _withheld_condition(recipe), "denominator": p.condition_labels[0]}
            if name == "tissue_effect":
                relative = (population / population.sum(axis=0, keepdims=True)).reshape(g, c, nt)
                effect = np.log2(relative[:, 1] / relative[:, 0])
                truth["relative_tissue_mean"] = relative.reshape(g, -1)
            if name == "observed_difference" and "batch_effect_log2" in extra:
                observed_mean = population[:, layout["reference"]].copy()
                batches = np.asarray([int(r["batch"].split("_")[-1]) - 1 for r in layout["rows"]])
                observed_mean *= np.exp2(extra["batch_effect_log2"][:, batches])
                condition = layout["reference"] // nt
                effect = np.log2(observed_mean[:, condition == 1].mean(axis=1) / observed_mean[:, condition == 0].mean(axis=1))
        elif name == "variability":
            condition = layout["reference"] // nt
            phi = extra["conditional_dispersion"]
            reference_mu = population.reshape(g, c, nt)[:, 0].mean(axis=1)
            phi_a = phi[:, condition == 0].mean(axis=1)
            phi_b = phi[:, condition == 1].mean(axis=1)
            variance_a = reference_mu + phi_a * reference_mu ** 2
            variance_b = reference_mu + phi_b * reference_mu ** 2
            if "response_mixture" in extra:
                mix = extra["response_mixture"]
                idx = mix["gene_indices"]
                variance_b[idx] += (1 + phi_b[idx]) * reference_mu[idx] ** 2 * mix["variance_multiplier"]
            variance_ratio = np.ones(g)
            np.divide(variance_b, variance_a, out=variance_ratio, where=variance_a > 0)
            effect = np.log(variance_ratio)
            truth["mean_null"] = np.all(np.abs(contrast) < 1e-10, axis=1)
            truth["variance_reference"] = "matched to condition A marginal mean at unit exposure; separate from mean-induced variance change"
            truth["dispersion_log_ratio"] = np.log(phi_b / phi_a)
        elif name == "event_timing":
            informative = np.any(np.abs(temporal.reshape(g, -1)) > 1e-10, axis=1)
            extrema = np.argmax(np.abs(temporal[:, 0]), axis=1)
            truth["informative_genes"] = informative
            truth["shape"] = tuple(shape if keep else "flat" for shape, keep in zip(details["shapes"], informative))
            truth["sampled_extremum_index"] = tuple(int(index) if keep else None for index, keep in zip(extrema, informative))
            truth["event_intervals"] = _sampled_events(temporal[:, 0], details["shapes"], layout["positions"], np)
            truth["latent_simulation_truth"] = {"continuous_program_parameters": {key: details[key] for key in ("E01", "E02", "E03", "E04") if key in details}}
            truth["timing_reference"] = "sampled peak/trough and reversal intervals; delayed-response timing is half of the observed endpoint excursion, not exact mechanistic onset"
        elif name == "rhythm":
            rhythm = details["rhythm"]
            informative = np.any(np.abs(temporal.reshape(g, -1)) > 1e-10, axis=1)
            idx = rhythm["gene_indices"]
            keep = informative[idx]
            truth["nonnull"] = informative
            truth["informative_gene_indices"] = idx[keep]
            truth["phase_convention"] = rhythm["phase_convention"]
            truth["amplitude_convention"] = rhythm["amplitude_convention"]
            truth["phase_reference_time"] = rhythm["phase_reference_time"]
            truth["latent_simulation_truth"] = dict(rhythm)
            truth["numerical_reference_available"] = bool(np.any(keep))
            if np.any(keep):
                truth["period"] = rhythm["period"]
                truth["phase_radians_by_condition"] = {label: phases[keep] for label, phases in rhythm["phase_radians_by_condition"].items()}
                truth["amplitude_log2_nonnegative"] = rhythm["amplitude_log2_nonnegative"]
                truth["damping_per_time_unit"] = rhythm["damping_per_time_unit"]
        elif name == "dose_curve":
            effect = temporal.reshape(g, -1)
            truth["curve"] = population
        elif name == "effective_concentration":
            informative = np.any(np.abs(temporal.reshape(g, -1)) > 1e-10, axis=1)
            idx = details["dose"]["gene_indices"]
            truth["nonnull"] = informative
            truth["informative_gene_indices"] = idx[informative[idx]]
            truth["latent_simulation_truth"] = dict(details["dose"])
            truth["numerical_reference_available"] = bool(np.any(informative[idx]))
            if np.any(informative[idx]):
                truth.update({key: value for key, value in details["dose"].items() if key != "gene_indices"})
        elif name == "unit_accounting":
            truth["population_units"] = layout["n_donors"]
            truth["observed_libraries"] = int(observed.sum())
            truth["observed_specimens"] = int(len(np.unique(layout["specimen"][observed])))
            truth["units_per_condition"] = p.independent_units_per_group if recipe.case_id in {"T07-repeated-donors", "T07-single-unit"} else p.independent_units_per_group * nt
            truth["identity_available"] = recipe.case_id != "T07-unknown-identity"
        elif name == "covariate_effect":
            effect = details["covariate_slopes_log2"][:, 0]
        elif name == "covariate_interaction":
            slopes = details["covariate_slopes_log2"]
            effect = slopes[:, 1] - slopes[:, 0]
        elif name == "relative_expression":
            relative = population / population.sum(axis=0, keepdims=True)
            relative = relative.reshape(g, c, nt)
            effect = np.log2(relative[:, 1] / relative[:, 0])
            truth["relative_mean"] = relative.reshape(g, -1)
        elif name == "absolute_rna":
            effect = contrast
            keep = np.ones(g, dtype=bool)
            if recipe.case_id in {"T09-valid-controls", "T09-invalid-controls"}:
                keep[details["control_indices"]] = False
            totals = population.reshape(g, c, nt)[keep].sum(axis=0)
            truth["global_total_rna_log2_ratio"] = np.log2(totals[1] / totals[0])
            truth["absolute_abundance"] = population
            truth["effect_units"] = "log2 absolute abundance ratio; totals exclude supplied external controls"
        elif name == "residual_modules":
            truth["loadings"] = details["module_loadings"]
            truth["factor_variance_by_condition"] = (1., 1.7 ** 2) if recipe.case_id == "T10-covariance-only" else (1., 1.)
            truth["covariance_representation"] = "log-expression low-rank loadings; count covariance requires lognormal transform and observation variance"
            truth["loading_identification"] = "column signs and permutations are arbitrary; compare implied covariance or module membership"
        elif name == "direct_regulation":
            truth["direct_graph_available"] = False
        elif name == "cell_fractions":
            mixture = details["mixture"]
            truth["latent_simulation_truth"] = mixture
            if recipe.case_id == "T11-incomplete-reference":
                truth["numerical_reference_available"] = False
                truth["restricted_reference"] = "Reference-coverage diagnostics only; an omitted profile prevents identified full or partial cell fractions without an additional mixture model."
            elif scope.expected_scope != "refusal":
                truth["cell_fractions"] = mixture["cell_fractions"]
                truth["rna_fractions"] = mixture["rna_fractions"]
                truth["numerical_reference_available"] = True
                if scope.expected_scope == "limited_reference":
                    truth["eligible_reference_gene_indices"] = np.flatnonzero(np.all(np.abs(mixture["within_cell_type_effect"]) < 1e-12, axis=1))
                    truth["restricted_reference"] = "Fractions conditional on supplied stable-marker profiles and RNA-content references; no intrinsic-response recovery score."
                    truth["scale_constraint"] = "infer reference RNA mixture weights, divide by supplied per-cell RNA content, then impose cell fractions summing to one"
        elif name == "measurement_route":
            truth["analysis_route"] = "continuous_likelihood" if measurement == "continuous" else "count_likelihood"
            truth["measurement"] = measurement
        else:
            raise ValueError(f"bulk target truth not implemented: {name}")
        if effect is not None:
            # Private counterfactual effect may be known even when no observable
            # estimator can identify it. Refusal targets have no eligible effect.
            if scope.expected_scope == "refusal":
                truth["unidentifiable_simulation_effect"] = effect
            else:
                truth["effect"] = effect
                truth["nonnull"] = np.any(np.abs(effect.reshape(g, -1)) > 1e-10, axis=1)
                if recipe.case_id == "T05-structural-zero":
                    zero_mask = details["structural_zero_gene_mask"]
                    observed_information = extra["observed_nonzero_gene_mask"]
                    testable = ~zero_mask & observed_information
                    eligible_effect = effect.astype(object)
                    eligible_effect[~testable] = None
                    truth["effect"] = eligible_effect
                    nonnull = truth["nonnull"].astype(object)
                    nonnull[zero_mask] = None
                    truth["nonnull"] = nonnull
                    truth["hypothesis_testable"] = testable
                    truth["observed_information_available"] = observed_information
                    truth["unavailable_reason_by_gene"] = {
                        details["gene_ids"][index]: "all observed counts are zero; no estimable log effect and no evidence establishing biological absence"
                        for index in range(g) if not testable[index]
                    }
                    latent_effect = effect.astype(object)
                    latent_effect[zero_mask] = None
                    truth["latent_simulation_truth"] = {"effect_at_positive_mean": latent_effect,
                                                        "structural_zero_gene_mask": zero_mask}
                    if "mean_null" in truth:
                        mean_null = truth["mean_null"].astype(object)
                        mean_null[zero_mask] = None
                        truth["mean_null"] = mean_null
                    if "dispersion_log_ratio" in truth:
                        dispersion_contrast = truth["dispersion_log_ratio"].astype(object)
                        dispersion_contrast[zero_mask] = None
                        truth["dispersion_log_ratio"] = dispersion_contrast
        if scope.expected_scope == "refusal":
            contract_fields = {"scope", "expected_refusal", "estimand", "units", "contrast",
                               "prediction_reference", "reference_population", "numerical_scoring",
                               "required_limitation", "forbidden_claims", "invalid_contrast",
                               "direct_graph_available"}
            latent = truth.pop("latent_simulation_truth", {})
            latent = dict(latent)
            for key in tuple(truth):
                if key not in contract_fields:
                    latent[key] = truth.pop(key)
            truth["latent_simulation_truth"] = latent
            truth["numerical_reference_available"] = False
        targets[name] = truth
    return targets


def _require_finite_payload(value: Any, np: Any, seen: set[int] | None = None) -> None:
    """Refuse nonfinite truth/reference output, including extreme admitted inputs."""
    seen = set() if seen is None else seen
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("generated truth or reference contains a nonfinite value")
    elif isinstance(value, np.ndarray):
        if id(value) in seen:
            return
        seen.add(id(value))
        if value.dtype.hasobject:
            for item in value.flat:
                _require_finite_payload(item, np, seen)
        elif not np.all(np.isfinite(value)):
            raise ValueError("generated truth or reference contains a nonfinite array")
    elif isinstance(value, dict):
        for item in value.values():
            _require_finite_payload(item, np, seen)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _require_finite_payload(item, np, seen)
    elif np.isscalar(value):
        if not np.isfinite(value):
            raise ValueError("generated truth or reference contains a nonfinite scalar")
    else:
        raise TypeError("unsupported value in biological truth/reference payload")


def generate_biology_bulk(recipe: BiologyRecipe, *, allow_data_execution: bool = False) -> BulkBiologyBundle:
    """Generate in memory only after the caller explicitly passes literal True.

    Calling this function without authorization fails before inspecting recipe,
    importing a numerical library, obtaining randomness, or accessing any path.
    """
    if allow_data_execution is not True:
        raise PermissionError("bulk data execution is held; pass allow_data_execution=True only after explicit authorization")
    plan = validate_biology_bulk_recipe(recipe)
    catalog = load_biology_catalog()
    validate_biology_bulk_recipe(recipe, catalog)
    import numpy as np

    names = ("identity", "biology", "exposure", "donor", "modules", "noise", "contamination", "missingness")
    seeds = np.random.SeedSequence(recipe.generation_seed).spawn(len(names))
    streams = {name: np.random.default_rng(seed) for name, seed in zip(names, seeds)}
    layout = _layout(recipe, streams["identity"], np)
    gene_ids = _opaque_ids(streams["identity"], "g_", recipe.parameters.num_genes)
    population, details, _ = _biological_mean(recipe, layout, streams["biology"], np)
    details["gene_ids"] = gene_ids
    zero_mask = details.get("structural_zero_gene_mask", np.zeros(recipe.parameters.num_genes, dtype=bool))
    if (not np.all(np.isfinite(population)) or np.any(population < 0)
            or np.any(population[~zero_mask] <= 0) or np.any(population[zero_mask] != 0)):
        raise ValueError("biological count mean curves must remain positive and finite")
    expression, observed, extra = _observe(recipe, plan, layout, population, details, streams, np)
    rows = tuple(row for row, keep in zip(layout["rows"], observed) if keep)
    _check_observed_support(recipe, catalog, rows)
    if expression.nbytes > plan.expression_bytes or len(rows) > plan.observation_upper_bound:
        raise ValueError("observed output exceeds admitted dimensions")
    template = catalog.template(recipe.template_id)
    measurement = next(layer for layer in template.observation_layers if layer.measurement == plan.measurement)
    requested = details["design_parameters"].get("requested_condition")
    public_context = {
        "measurement": measurement.model_dump(mode="json"),
        "biological_units": template.unit_hierarchy.model_dump(mode="json"),
        "design": {"condition_levels": recipe.parameters.condition_labels,
                   "time_grid": recipe.parameters.time_grid, "time_units": recipe.parameters.time_units,
                   "dose_grid": recipe.parameters.dose_grid, "dose_units": recipe.parameters.dose_units,
                   "batch_labels": tuple(sorted({row["batch"] for row in rows})),
                   "independent_units_per_group_planned": recipe.parameters.independent_units_per_group,
                   "technical_libraries_per_specimen": recipe.parameters.technical_libraries_per_specimen,
                   "repeated_specimens_per_unit": recipe.parameters.repeated_specimens_per_unit},
        "contrasts": tuple({"target_id": target.target_id, "estimand": target.estimand,
                            "contrast": target.contrast, "units": target.measurement_units,
                            "reference_population": target.reference_population,
                            "prediction_reference": target.prediction_reference}
                           for target in template.targets),
        "references": tuple(source.model_dump(mode="json") for source in catalog.sources
                            if source.source_id in template.source_refs),
    }
    if requested is not None:
        public_context["requested_condition_contrast"] = {"numerator": requested,
                                                          "denominator": recipe.parameters.condition_labels[0]}
    if recipe.template_id == "T02":
        span = recipe.parameters.time_grid[-1] - recipe.parameters.time_grid[0]
        public_context["rhythm_analysis_domain"] = {
            "frequency_cycles_per_time_unit": (1 / span, 3 / span),
            "period_in_time_units": (span / 3, span),
            "phase_convention": "cosine_plus_phase",
            "amplitude_convention": "nonnegative",
            "phase_reference_time": recipe.parameters.time_grid[0],
            "scope": "prespecified candidate domain; frequencies outside this domain are not identified by the benchmark contract",
        }
    if recipe.template_id == "T01":
        public_context["timing_definitions"] = {
            "turning_events": "peak and trough intervals bracketed by observations, including intervening plateaus",
            "delayed_response": "time interval crossing half of the observed endpoint excursion",
            "reversal": "sampled direction changes or baseline crossings; report distinct observed events",
            "continuous_onset": "not an exact numerical target beyond observed sampling support",
        }
    if recipe.template_id == "T09":
        public_context["measurement"]["observation_model"] = "Fixed-total sequencing per library; counts describe relative abundances. Absolute scale requires independently verified input controls."
    if recipe.case_id == "T07-unknown-identity":
        public_context["biological_units"]["identity_status"] = "intentionally_unknown"
        public_context["biological_units"]["nesting"] = "Specimen and library links are supplied; biological donor identity was not documented."
    # Do not include case IDs, private seeds, planted gene labels, expected scopes,
    # or private mechanisms in the participant context.
    annotations = _public_annotations(recipe, gene_ids, details, extra, np)
    targets = _target_truth(recipe, catalog, population, details, layout, extra, observed, plan.measurement, np)
    private_truth = {
        "scope": "simulation_truth_only_not_biological_validation", "case_id": recipe.case_id,
        "generation_seed": recipe.generation_seed, "gene_ids": gene_ids,
        "complete_sample_ids": layout["sample_ids"], "observation_mask": observed,
        "population_mean": np.log2(population) if plan.measurement == "continuous" else population,
        "population_mean_units": "log2 normalized expression" if plan.measurement == "continuous" else "abundance at unit exposure",
        "reference_grid": tuple({"condition": condition, "dose" if recipe.template_id == "T04" else "time": float(position)}
                                for condition in recipe.parameters.condition_labels for position in layout["positions"]),
        "targets": targets, "mechanism": details, "observation_process": extra,
        "donor_indices": layout["donor"], "specimen_indices": layout["specimen"],
        "replay_streams": names,
    }
    if "mixture" in details:
        private_truth["mixture"] = details["mixture"]
    _require_finite_payload((private_truth, public_context, annotations), np)
    return BulkBiologyBundle(expression=expression, gene_ids=gene_ids,
                             sample_ids=tuple(row["sample_id"] for row in rows), metadata=rows,
                             measurement=plan.measurement, public_context=public_context,
                             public_annotations=annotations, private_truth=private_truth)
