"""Frozen generalized design and binary bridge for the governed mounted worker."""

import json
import math
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from devharness_core.storage import ChunkedExpressionStore

from .bulk_solver_policy import freeze_bulk_solver, require_dream_coefficient
from .streaming_rle import streaming_median_ratio_factors

_DRIVER = (
    Path(__file__).resolve().parent.parent / "devharness_server/r/mounted_general.R"
)
_MATRIX_BYTES = 512 * 1024**2
MAX_SUMMARY_BYTES = 1024**2
_OMNIBUS = {"nested", "lrt"}
_LATENT_SCORES = "latent_scores.bin"
_MODEL_STATISTICS = frozenset(
    {"F", "t", "LR", "df.prior", "df.residual.adj", "df.total", "s2.post"}
)
_COMPARATOR_COLUMNS = frozenset(
    {
        "sensitivity_effect",
        "sensitivity_p_value",
        "sensitivity_q_value",
        "sensitivity_code",
    }
)
MODEL_QUANTITY_COLUMNS = (
    _MODEL_STATISTICS
    | _COMPARATOR_COLUMNS
    | {f"sensitivity_{key}" for key in _MODEL_STATISTICS}
)


@dataclass(frozen=True)
class MountedCohort:
    design: np.ndarray
    condition_number: float
    unit_ids: list[str]
    labels: list[str]
    strata: list[str] | None
    covariates: np.ndarray | None
    reduced: np.ndarray | None
    contrast: np.ndarray | None

    @property
    def units_per_arm(self) -> tuple[int, ...]:
        # Sorting sizes makes the routing input invariant under renaming or
        # exchanging condition labels. Multiple profiles of a donor count once.
        pairs = set(zip(self.labels, self.unit_ids, strict=True))
        return tuple(sorted(Counter(label for label, _ in pairs).values()))


def _latent_options(latent):
    if (
        type(latent) is not dict
        or set(latent) - {"method", "sv_method", "n_factors", "controls"}
        or latent.get("method") not in ("svaseq", "ruvg")
    ):
        raise ValueError("Invalid frozen latent diagnostic")
    if latent["method"] == "svaseq":
        if set(latent) & {"n_factors", "controls"} or latent.get(
            "sv_method", "be"
        ) not in ("be", "leek"):
            raise ValueError("svaseq requires its frozen factor estimator")
        return
    controls = latent.get("controls")
    if (
        "sv_method" in latent
        or type(latent.get("n_factors")) is not int
        or not 1 <= latent["n_factors"] <= 32
        or type(controls) is not dict
    ):
        raise ValueError(
            "RUVg requires a bounded factor count and independent controls"
        )
    if (
        set(controls) != {"origin", "scope", "source", "gene_ids"}
        or controls["origin"] not in ("spike_ins", "predeclared_external")
        or controls["scope"] != "independent"
        or type(controls["source"]) is not str
        or not 0 < len(controls["source"]) <= 1024
        or type(controls["gene_ids"]) is not list
        or not 0 < len(controls["gene_ids"]) <= 10_000
        or not all(
            type(gene) is str and 0 < len(gene) <= 200 for gene in controls["gene_ids"]
        )
        or len(set(controls["gene_ids"])) != len(controls["gene_ids"])
    ):
        raise ValueError("RUVg requires a complete independent control declaration")


def validate_mounted_model(model, measurement):
    if (
        type(model) is not dict
        or set(model)
        - {
            "comparison",
            "units",
            "condition_weights",
            "reduced_terms",
            "lengths_sha256",
            "latent",
            "sensitivity_method",
            "heterogeneous_independent",
        }
        or model.get("comparison") not in ("coefficient", "contrast", "nested", "lrt")
        or model.get("units") not in ("independent", "paired_fixed", "mixed")
    ):
        raise ValueError("Invalid mounted model declaration")
    comparison = model["comparison"]
    if ("condition_weights" in model) != (comparison == "contrast"):
        raise ValueError(
            "An explicit contrast requires one frozen condition weight per level"
        )
    if "reduced_terms" in model and comparison not in _OMNIBUS:
        raise ValueError("A reduced model requires a nested comparison")
    if model["units"] == "mixed":
        require_dream_coefficient(measurement, comparison)
    lengths = model.get("lengths_sha256")
    if measurement == "tximport_estimated_counts":
        if type(lengths) is not str or not re.fullmatch(r"[a-f0-9]{64}", lengths):
            raise ValueError("tximport counts require digest-bound effective lengths")
    elif "lengths_sha256" in model:
        raise ValueError("Effective lengths require declared tximport estimated counts")
    if "latent" in model:
        if measurement != "raw_counts" or model["units"] != "independent":
            raise ValueError(
                "Latent diagnostics require an independent raw-count design"
            )
        _latent_options(model["latent"])
    _validate_declared_methods(model, measurement)


def _validate_declared_methods(model, measurement):
    if "heterogeneous_independent" in model and (
        type(model["heterogeneous_independent"]) is not bool
        or measurement != "raw_counts"
        or model["units"] != "independent"
        or model["comparison"] not in {"coefficient", "contrast"}
    ):
        raise ValueError("Heterogeneity requires an independent directed count design")
    if "sensitivity_method" in model and (
        model["sensitivity_method"] != "limma_voom"
        or measurement != "raw_counts"
        or model["units"] == "mixed"
        or model["comparison"] not in ("coefficient", "contrast")
        or "latent" in model
    ):
        raise ValueError("Unsupported frozen limma-voom sensitivity request")


def _condition_columns(labels, request, model):
    levels = set(labels)
    reference, target = request["denominator"], request["numerator"]
    if not {reference, target}.issubset(levels) or len(levels) > 32:
        raise ValueError("Mounted model condition levels differ from the frozen scope")
    if model["comparison"] == "coefficient" and levels != {reference, target}:
        raise ValueError("A frozen coefficient requires exactly two condition levels")
    coded = [target, *sorted(levels - {reference, target})]
    weights = model.get("condition_weights")
    if model["comparison"] == "contrast" and (
        type(weights) is not dict
        or set(weights) != levels
        or not all(
            type(weight) in (int, float)
            and abs(weight) <= 1e6
            and math.isfinite(weight)
            for weight in weights.values()
        )
        or abs(sum(weights.values())) > 1e-8
        or weights[target] <= 0
        or weights[reference] >= 0
    ):
        raise ValueError(
            "Contrast weights require all observed condition levels and a zero sum"
        )
    return coded, [weights[level] for level in coded] if weights is not None else [1.0]


def _check_units(labels, units, mode):
    memberships = {(unit, level) for unit, level in zip(units, labels, strict=True)}
    if any(
        len({unit for unit, level in memberships if level == group}) < 3
        for group in set(labels)
    ):
        raise ValueError(
            "Mounted model requires at least three biological units per condition"
        )
    if mode == "independent" and len(set(units)) != len(units):
        raise ValueError(
            "Independent mounted model requires one profile per biological unit"
        )
    if mode == "paired_fixed" and (
        len(set(labels)) != 2
        or len(memberships) != len(units)
        or len(memberships) != 2 * len(set(units))
    ):
        raise ValueError("Fixed pairing requires one complete pair per biological unit")
    if mode == "mixed" and sum(count > 1 for count in Counter(units).values()) < 2:
        raise ValueError("Mixed models require at least two repeated biological units")


def _scaled_covariates(covariates):
    if not np.isfinite(covariates).all():
        raise ValueError("Declared mounted covariates must be finite")
    # Rescale first to avoid overflow when taking moments in the input units.
    magnitude = np.max(np.abs(covariates), axis=0)
    scaled = covariates / np.where(magnitude > 0, magnitude, 1.0)
    centered = scaled - scaled.mean(axis=0)
    deviation = centered.std(axis=0)
    return centered / np.where(deviation > 0, deviation, 1.0)


def _design_terms(rows, request, model, labels, units, levels):
    terms = {"condition": [np.asarray(labels) == level for level in levels]}
    if model["units"] == "paired_fixed":
        terms["biological_unit"] = [
            np.asarray(units) == unit for unit in sorted(set(units))[1:]
        ]
    for name in request["technical_factors"]:
        values = np.asarray([row[name] for row in rows])
        terms[name] = [values == value for value in sorted(set(values))[1:]]
        if not terms[name]:
            raise ValueError(
                "Declared categorical covariates require two or more observed levels"
            )
    numeric = request["continuous_covariates"]
    covariates = (
        _scaled_covariates(
            np.asarray([[float(row[name]) for name in numeric] for row in rows])
        )
        if numeric
        else None
    )
    if covariates is not None:
        terms.update(
            {name: [column] for name, column in zip(numeric, covariates.T, strict=True)}
        )
    return terms, covariates


def _matrix(terms, n):
    columns = [np.ones(n), *(column for term in terms for column in term)]
    if len(columns) >= n or len(columns) > 256:
        raise ValueError("Mounted model has insufficient residual support")
    design = np.column_stack(columns).astype(float)
    condition = float(np.linalg.cond(design))
    if (
        np.linalg.matrix_rank(design) != design.shape[1]
        or not math.isfinite(condition)
        or condition > 1000
    ):
        raise ValueError("Mounted design rank, conditioning or residual support failed")
    return design, condition


def compile_mounted_model(rows, request):
    model = request["model"]
    labels = [row[request["condition_column"]] for row in rows]
    units = [row[request["unit_column"]] for row in rows]
    factors = request["technical_factors"]
    if {"condition", "biological_unit"}.intersection(
        (*factors, *request["continuous_covariates"])
    ):
        raise ValueError(
            "Generalized nuisance terms cannot use reserved model term names"
        )
    _check_units(labels, units, model["units"])
    levels, weights = _condition_columns(labels, request, model)
    terms, covariates = _design_terms(rows, request, model, labels, units, levels)
    design, condition = _matrix(terms.values(), len(rows))
    if model["units"] == "mixed" and design.shape[0] - design.shape[1] <= 1:
        raise ValueError(
            "Mounted dream requires at least two residual degrees of freedom"
        )
    strata = (
        [json.dumps([row[name] for name in factors]) for row in rows]
        if factors
        else None
    )
    reduced, contrast = None, None
    if model["comparison"] in _OMNIBUS:
        retained = model.get(
            "reduced_terms", [term for term in terms if term != "condition"]
        )
        if (
            type(retained) is not list
            or any(type(term) is not str for term in retained)
            or not set(retained) < set(terms)
            or len(set(retained)) != len(retained)
            or "condition" in retained
            or "biological_unit" in terms
            and "biological_unit" not in retained
        ):
            raise ValueError(
                "Reduced terms must drop condition and retain any fixed pairing"
            )
        reduced, _ = _matrix(
            (columns for term, columns in terms.items() if term in retained), len(rows)
        )
    else:
        contrast = np.zeros(design.shape[1])
        contrast[1 : 1 + len(weights)] = weights
    return MountedCohort(
        design, condition, units, labels, strata, covariates, reduced, contrast
    )


def _write_gene_matrix(path, source, *, positive=False, raw_counts=False):
    if math.prod(source.shape) * 8 > _MATRIX_BYTES:
        raise ValueError("Generalized solver matrix exceeds its scratch bound")
    position = 0
    with path.open("xb") as output:
        for _, block in source.iter_gene_chunks(256):
            if (
                block.dtype.kind not in "iuf"
                or not np.isfinite(block).all()
                or positive
                and (block <= 0).any()
                or raw_counts
                and (
                    (block < 0).any()
                    or (block > 2**53 - 1).any()
                    or block.dtype.kind == "f"
                    and (block != np.floor(block)).any()
                )
            ):
                raise ValueError("Mounted solver matrix has invalid values")
            np.asarray(block, dtype="<f8").tofile(output)
            position += len(block)
    if position != source.shape[0]:
        raise ValueError(
            "Generalized solver matrix did not cover the feature inventory"
        )


def _fit_config(choice, cohort, source, request):
    model = request["model"]
    labels = cohort.labels
    routing = {}
    if choice.regime_route:
        routing = {
            "regime_route": choice.regime_route,
            "normalization": choice.normalization,
            "filter_mode": choice.filter_mode,
            "abundance_trend": choice.abundance_trend,
            "sensitivity_solver": choice.sensitivity_solver,
            "units_per_arm": cohort.units_per_arm,
            "rank_sensitivity_planned": choice.rank_sensitivity_planned,
            "calibration_status": choice.calibration_status,
        }
    return {
        **routing,
        "solver": choice.solver,
        "test": choice.test,
        "shrinkage": choice.shrinkage,
        "units": model["units"],
        "comparison": model["comparison"],
        "labels": labels,
        "unit_ids": cohort.unit_ids,
        "gene_ids": source.gene_names,
        "numerator": request["numerator"],
        "denominator": request["denominator"],
        "measurement": request["measurement"],
        "latent": model.get("latent"),
        "genes": source.shape[0],
        "samples": source.shape[1],
        "columns": cohort.design.shape[1],
        "reduced_columns": 0 if cohort.reduced is None else cohort.reduced.shape[1],
        "contrast": None if cohort.contrast is None else cohort.contrast.tolist(),
    }


def _write_design_inputs(
    directory,
    source,
    cohort,
    measurement,
    lengths_path,
    *,
    require_rle=True,
):
    _write_gene_matrix(
        directory / "counts.bin", source, raw_counts=measurement == "raw_counts"
    )
    cohort.design.astype("<f8").tofile(directory / "design.bin")
    if cohort.reduced is not None:
        cohort.reduced.astype("<f8").tofile(directory / "reduced.bin")
    if measurement == "raw_counts":
        if not require_rle:
            return {"method": "TMM"}
        factors, normalization = streaming_median_ratio_factors(
            lambda: (block for _, block in source.iter_gene_chunks(256)),
            source.shape[1],
            scratch_dir=directory,
        )
        factors.astype("<f8").tofile(directory / "rle.bin")
        return {**normalization, "size_factors": factors.tolist()}
    if lengths_path is not None:
        with ChunkedExpressionStore(lengths_path) as lengths:
            if (
                lengths.gene_names != source.gene_names
                or lengths.sample_names != source.sample_names
                or lengths.shape != source.shape
            ):
                raise ValueError(
                    "Effective lengths differ from the exact expression axes"
                )
            _write_gene_matrix(directory / "lengths.bin", lengths, positive=True)
        return {"method": "tximport_effective_length_and_TMM_2D_offset"}
    return {"method": "as_measured"}


def _read_result(directory, n_genes, omnibus):
    binary = directory / "result.bin"
    if binary.stat().st_size != 4 * n_genes * 8:
        raise ValueError("Mounted model returned a different gene inventory")
    effects, pvalues, adjusted, codes = np.fromfile(binary, dtype="<f8").reshape(
        4, n_genes
    )
    estimated = codes == 0
    if (
        not np.isin(codes, [0, 1, 2, 3]).all()
        or not np.all(
            (0 <= pvalues[estimated])
            & (pvalues[estimated] <= adjusted[estimated])
            & (adjusted[estimated] <= 1)
        )
        or not np.isnan(pvalues[~estimated]).all()
        or not np.isnan(adjusted[~estimated]).all()
        or not np.isnan(effects if omnibus else effects[~estimated]).all()
        or not omnibus
        and not np.isfinite(effects[estimated]).all()
    ):
        raise ValueError(
            "Mounted model returned invalid effect or probability semantics"
        )
    with (directory / "method.json").open("rb") as file:
        raw = file.read(MAX_SUMMARY_BYTES + 1)
    if len(raw) > MAX_SUMMARY_BYTES:
        raise ValueError("Generalized solver diagnostics exceed their bound")
    evidence = json.loads(raw)
    return effects, pvalues, adjusted, codes.astype(np.int8), evidence


def freeze_mounted_solver(cohort, request, primary, runtime):
    """Freeze the method using only study design, before reading expression."""
    model = request["model"]
    return freeze_bulk_solver(
        measurement=request["measurement"],
        runtime=runtime,
        comparison=model["comparison"],
        units=model["units"],
        primary=primary,
        single_factor=not (
            request["technical_factors"] or request["continuous_covariates"]
        ),
        sensitivity_method=model.get("sensitivity_method"),
        units_per_arm=cohort.units_per_arm,
        heterogeneous_independent=model.get("heterogeneous_independent", False),
    )


def _read_model_quantities(directory, fields, n_genes, comparator):
    if (
        not isinstance(fields, list)
        or not fields
        or len(fields) > len(MODEL_QUANTITY_COLUMNS)
        or any(
            not isinstance(field, str) or field not in MODEL_QUANTITY_COLUMNS
            for field in fields
        )
        or len(set(fields)) != len(fields)
        or set(fields) & _COMPARATOR_COLUMNS
        != (_COMPARATOR_COLUMNS if comparator else set())
    ):
        raise ValueError("Invalid per-gene model statistic inventory")
    path = directory / "model-quantities.bin"
    expected = n_genes * len(fields) * 8
    if path.stat().st_size != expected or expected > _MATRIX_BYTES:
        raise ValueError("Invalid model quantity payload size")
    data = np.fromfile(path, dtype="<f8").reshape(len(fields), n_genes)
    columns = dict(zip(fields, data, strict=True))
    if comparator:
        codes = columns["sensitivity_code"]
        estimated = codes == 0
        p, q = columns["sensitivity_p_value"], columns["sensitivity_q_value"]
        effects = columns["sensitivity_effect"]
        if (
            not np.isin(codes, (0, 1, 3)).all()
            or not (
                (0 <= p[estimated])
                & (p[estimated] <= q[estimated])
                & (q[estimated] <= 1)
            ).all()
            or not np.isfinite(effects[estimated]).all()
            or any(not np.isnan(values[~estimated]).all() for values in (p, q, effects))
        ):
            raise ValueError("Invalid frozen sensitivity estimates")
    return columns


def _tmm_normalization(details, samples):
    libraries = np.asarray(details.get("lib.size"), dtype=float)
    factors = np.asarray(details.get("norm.factors"), dtype=float)
    if libraries.shape != (samples,) or factors.shape != libraries.shape:
        raise ValueError("Invalid TMM library axis")
    effective = libraries * factors
    if not np.all(np.isfinite(effective) & (effective > 0)):
        raise ValueError("Invalid TMM effective library size")
    relative = np.exp(np.log(effective) - np.log(effective).mean())
    return {"method": "TMM", "size_factors": relative.tolist()}


def _verify_count_route(evidence, choice, cohort, codes):
    details = evidence["solver_details"]
    if (
        evidence.get("regime_route") != choice.regime_route
        or evidence.get("calibration_status") != choice.calibration_status
        or (evidence.get("sensitivity_details") or {}).get("solver")
        != choice.sensitivity_solver
        or evidence.get("solver") != choice.solver
        or evidence.get("test") != choice.test
        or details.get("normalization") != choice.normalization
        or details.get("filter") != choice.filter_mode
    ):
        raise ValueError("Returned procedure differs from the frozen count route")
    if choice.solver == "edger_v4" and details.get("abundance_trend") is not (
        int(np.count_nonzero(codes != 1)) >= 500
    ):
        raise ValueError(
            "Returned QL trend differs from the pre-fit eligible count rule"
        )
    evidence.update(
        regime_route=choice.regime_route,
        units_per_arm=list(cohort.units_per_arm),
        calibration_status=choice.calibration_status,
        sensitivity_solver=choice.sensitivity_solver,
        rank_sensitivity_planned=choice.rank_sensitivity_planned,
    )
    return _tmm_normalization(details, len(cohort.labels))


def fit_mounted_model(source, cohort, request, primary, runtime, outputs, lengths_path):
    model = request["model"]
    measurement = request["measurement"]
    choice = freeze_mounted_solver(cohort, request, primary, runtime)
    if choice.solver == "pydeseq2" or runtime != "bioconductor":
        raise ValueError(
            "The generalized mounted model requires the pinned Bioconductor image"
        )
    with TemporaryDirectory(dir=outputs) as temporary:
        directory = Path(temporary)
        normalization = _write_design_inputs(
            directory,
            source,
            cohort,
            measurement,
            lengths_path,
            require_rle=choice.normalization != "TMM"
            or model.get("latent") is not None,
        )
        config = _fit_config(choice, cohort, source, request)
        with (directory / "model.json").open("x", encoding="utf-8") as stream:
            json.dump(config, stream, allow_nan=False)
        with (directory / "diagnostics.log").open("xb") as diagnostics:
            try:
                subprocess.run(
                    ["Rscript", "--vanilla", str(_DRIVER), str(directory)],
                    check=True,
                    stdin=subprocess.DEVNULL,
                    stdout=diagnostics,
                    stderr=subprocess.STDOUT,
                    timeout=240,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise ValueError(f"Mounted {choice.solver} execution failed") from error
        effects, pvalues, adjusted, codes, evidence = _read_result(
            directory,
            source.shape[0],
            model["comparison"] in _OMNIBUS,
        )
        # The binary files exist only inside this private solver scratch directory.
        evidence.pop("numeric_transport", None)
        if choice.regime_route:
            normalization = _verify_count_route(evidence, choice, cohort, codes)
        fields = evidence.get("model_columns")
        if fields or choice.regime_route:
            evidence["_model_quantities"] = _read_model_quantities(
                directory,
                fields,
                source.shape[0],
                choice.sensitivity_solver,
            )
        if latent := evidence.get("latent_diagnostics"):
            samples = source.shape[1]
            factors = latent["n_factors"]
            if type(factors) is not int or not 0 <= factors < samples:
                raise ValueError("Invalid latent factor count")
            if factors:
                expected_bytes = samples * factors * 8
                with (directory / _LATENT_SCORES).open("rb") as stream:
                    scores = stream.read(expected_bytes + 1)
                if (
                    len(scores) != expected_bytes
                    or not np.isfinite(np.frombuffer(scores, dtype="<f8")).all()
                ):
                    raise ValueError("Invalid latent score matrix")
                evidence["_latent_score_bytes"] = scores
    return (
        effects,
        pvalues,
        adjusted,
        codes,
        {**evidence, "normalization": normalization},
    )
