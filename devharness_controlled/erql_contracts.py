"""Signed synthetic ERQL requests and source-bound count preparation."""
from dataclasses import asdict
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
from typing import Literal, get_args
from pydantic import model_validator

from devbench.core.publication_families import ERQL_FILTER_BY_PROFILE, FamilyAllocation, FamilySpec, Label
from devbench.core.publication_families import ERQLProfile as ERQLProfile

from .contracts import Digest, FrozenContract, NonemptyText, Revision, canonical_json_bytes
from .verification import _read, _json

ERQLImage = Literal["sha256:20a98c3379dccfc9afaae36d313b67ece17e3008de6eef41717d4bd596d8bfd0"]
ERQLPackageLock = Literal["16fea8283a9de6d1500ad6a3089b32e8d6a5501bf341420d1d1b9f0f535ec37c"]

CANONICAL, AUGMENTED, DESIGN_AWARE, GROUP_FILTERED = get_args(ERQLProfile)
# ERQL-1.2 remains executable for synthetic audit replay after its failed T09 diagnostic.
HISTORICAL_FAILED_PROFILES = frozenset({DESIGN_AWARE})
MULTIFACTOR_PROFILES = frozenset({DESIGN_AWARE, GROUP_FILTERED})
REQUEST_VERSIONS = {DESIGN_AWARE: "erql_request/2", GROUP_FILTERED: "erql_request/3"}
(IMAGE,) = get_args(ERQLImage)
(LOCK_SHA256,) = get_args(ERQLPackageLock)
LOCK = Path(__file__).resolve().parent / "erql-package-sources.lock.json"
DRIVER = Path(__file__).resolve().parent.parent / "devharness_server/r/erql_1_1.R"
DESIGN_AWARE_DRIVER = DRIVER.with_name("erql_1_2.R")
GROUP_FILTERED_DRIVER = DRIVER.with_name("erql_1_3.R")
REQUEST_LIMIT = 16 * 1024 * 1024
VERSIONS = {"R": "4.6.1", "BiocVersion": "3.23.1", "edgeR": "4.10.7", "limma": "3.68.5",
            "statmod": "1.5.2", "locfit": "1.5-9.12", "lattice": "0.23-1", "jsonlite": "2.0.0"}
LEGACY_REGIMES = ("S-I", "S-P")
DESIGN_AWARE_REGIMES = ("B-I", "B-P", "S-I", "S-P", "P-P")
PAIRED_REGIMES = frozenset({"B-P", "S-P", "P-P"})
INDEPENDENT_LABEL_ORIGINS = frozenset({"orthogonal_measurement", "independent_assignment"})


def driver_for_profile(profile):
    if profile == GROUP_FILTERED:
        return GROUP_FILTERED_DRIVER
    if profile == DESIGN_AWARE:
        return DESIGN_AWARE_DRIVER
    if profile in (CANONICAL, AUGMENTED):
        return DRIVER
    raise ValueError("Unknown ERQL profile")


def digest(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class ERQLPlan(FrozenContract):
    schema_version: Literal["controlled_erql_plan/1"] = "controlled_erql_plan/1"
    plan_id: NonemptyText
    study_id: NonemptyText
    revision: Revision = 1
    profile: ERQLProfile = CANONICAL
    data_classification: Literal["synthetic"]
    source_sha256: Digest
    inspection_sha256: Digest
    count_profiles_sha256: Digest
    design_sha256: Digest
    request_sha256: Digest
    driver_sha256: Digest
    package_lock_sha256: ERQLPackageLock = LOCK_SHA256
    runtime_image_id: ERQLImage = IMAGE
    platform: Literal["linux/amd64"] = "linux/amd64"
    max_memory_mb: Literal[2048] = 2048
    max_cpus: Literal[2] = 2
    timeout_sec: Literal[120] = 120


# The versioned subtype retains the /1 plan interface but signs a distinct discriminator.
class ERQLFamilyPlan(ERQLPlan):  # type: ignore[override]
    schema_version: Literal["controlled_erql_plan/2"] = "controlled_erql_plan/2"  # type: ignore[assignment]
    allocation: FamilyAllocation
    family_id: Label

    @model_validator(mode="after")
    def check_family(self):
        family = self.allocation.family(self.family_id)
        if self.profile != self.allocation.profile or family.request_sha256 != self.request_sha256:
            raise ValueError("Active family does not bind the signed ERQL request and profile")
        return self


def parse_erql_plan(value):
    raw = value.canonical_bytes() if isinstance(value, ERQLPlan) else canonical_json_bytes(value)
    if len(raw) > 8 * 1024**2:
        raise ValueError("ERQL plan exceeds the signed family inventory byte limit")
    body = _json(raw)
    if type(body) is not dict:
        raise ValueError("ERQL plan must be an object")
    if body.get("schema_version") == "controlled_erql_plan/2":
        return ERQLFamilyPlan.model_validate_json(raw)
    if body.get("schema_version") != "controlled_erql_plan/1":
        raise ValueError("Unknown ERQL plan schema")
    plan = ERQLPlan.model_validate_json(raw)
    if plan.profile == GROUP_FILTERED:
        raise ValueError("ERQL-1.3 requires a signed family allocation")
    return plan


def bind_erql_families(plan, allocation, family_id):
    """Commit the complete family allocation before signing or outcome access."""
    fields = plan.model_dump(exclude={"schema_version", "allocation", "family_id"})
    return ERQLFamilyPlan(**fields, allocation=allocation, family_id=family_id)


def validate_erql_request(request, plan):
    import numpy as np

    if plan.profile == GROUP_FILTERED and not isinstance(plan, ERQLFamilyPlan):
        raise ValueError("ERQL-1.3 requires a signed family allocation")
    multifactor = plan.profile in MULTIFACTOR_PROFILES
    fields = {"schema_version", "profile", "regime", "feature_ids", "profile_ids", "groups", "unit_ids",
              "denominator", "numerator", "counts", "design", "contrast"}
    if multifactor:
        fields.update({"condition", "population_label_basis"})
    if multifactor or isinstance(plan, ERQLFamilyPlan):
        fields.add("population")
    version = REQUEST_VERSIONS.get(plan.profile, "erql_request/1")
    if (type(request) is not dict or set(request) != fields or request["schema_version"] != version
            or request["profile"] != plan.profile or digest(request) != plan.request_sha256):
        raise ValueError("ERQL request does not match the signed profile and bytes")

    def labels(values, low, high, *, unique=False):
        return (type(values) is list and low <= len(values) <= high
                and all(type(x) is str and x.strip() and len(x) <= 200 and "\x00" not in x for x in values)
                and (not unique or len(values) == len(set(values))))

    genes, profiles = request["feature_ids"], request["profile_ids"]
    if not labels(genes, 1, 8192, unique=True) or not labels(profiles, 6, 256, unique=True):
        raise ValueError("ERQL complete panel exceeds the engineering envelope or has invalid axes")
    if isinstance(plan, ERQLFamilyPlan):
        family = plan.allocation.family(plan.family_id)
        if (tuple(genes), request["population"], request["numerator"], request["denominator"]) != (
                family.feature_ids, family.population, family.numerator, family.denominator):
            raise ValueError("ERQL request does not match the complete signed family")
    n = len(profiles)
    if n * len(genes) > 1_000_000:
        raise ValueError("ERQL panel exceeds the engineering cell bound")
    units, groups = request["unit_ids"], request["groups"]
    levels = [request["denominator"], request["numerator"]]
    if (not labels(units, n, n) or not labels(groups, n, n) or not labels(levels, 2, 2, unique=True)
            or set(groups) != set(levels)):
        raise ValueError("ERQL requires exactly two declared target groups")
    regime = request["regime"]
    if regime not in (DESIGN_AWARE_REGIMES if multifactor else LEGACY_REGIMES):
        raise ValueError("Unsupported ERQL regime in registered synthetic adapter")
    if multifactor:
        from rna_kernels.units import UNKNOWN_UNIT_MARKERS

        if any(unit.strip().lower() in UNKNOWN_UNIT_MARKERS for unit in units):
            raise ValueError(f"{plan.profile} requires declared biological units")
        if not labels([request["population"]], 1, 1):
            raise ValueError(f"{plan.profile} requires a bounded family population or analysis scope")
        condition, basis = request["condition"], request["population_label_basis"]
        if regime == "P-P":
            if (condition != request["population"] or not labels([condition], 1, 1)
                    or type(basis) is not dict or set(basis) != {"origin", "source_sha256"}
                    or type(basis["origin"]) is not str or basis["origin"] not in INDEPENDENT_LABEL_ORIGINS
                    or basis["source_sha256"] != plan.source_sha256):
                raise ValueError(f"{plan.profile} P-P requires one fixed condition and source-bound independent labels")
        elif condition is not None or basis is not None:
            raise ValueError(f"Only an {plan.profile} P-P request may bind a condition or population-label basis")
        if regime.startswith("B-") and request["population"] != "bulk":
            raise ValueError(f"{plan.profile} bulk requests require the bulk analysis scope")
    paired = regime in PAIRED_REGIMES
    if not paired:
        if len(set(units)) != n or any(groups.count(group) < 3 for group in levels):
            raise ValueError(f"ERQL {regime} requires distinct units and at least three per group")
    else:
        memberships = {}
        for unit, group in zip(units, groups):
            memberships.setdefault(unit, []).append(group)
        if len(memberships) < 3 or any(sorted(pair) != sorted(levels) for pair in memberships.values()):
            raise ValueError(f"ERQL {regime} requires at least three complete pairs")
    counts, design, contrast = request["counts"], request["design"], request["contrast"]
    if (type(counts) is not list or len(counts) != n or any(type(row) is not list or len(row) != len(genes)
            or any(type(x) is not int or not 0 <= x < 2**53 for x in row)
            or not 0 < sum(row) < 2**53 for row in counts)):
        raise ValueError("ERQL counts must be exact nonnegative integers with positive bounded libraries")
    if (type(contrast) is not list or not 2 <= len(contrast) < min(n, 129)
            or any(type(x) is not int or x not in (-1, 0, 1) for x in contrast)
            or contrast[:-1] != [0] * (len(contrast) - 1) or not contrast[-1]
            or type(design) is not list or len(design) != n
            or any(type(row) is not list or len(row) != len(contrast) or row[0] != 1
                   or any(type(x) is not int or x not in (0, 1) for x in row) for row in design)):
        raise ValueError("ERQL requires the exact typed additive design and target contrast")
    target = levels[1] if contrast[-1] == 1 else levels[0]
    matrix = np.asarray(design, dtype=float)
    if (any(row[-1] != int(group == target) for row, group in zip(design, groups))
            or np.linalg.matrix_rank(matrix) != len(contrast)):
        raise ValueError("ERQL design does not identify the signed target contrast")
    if paired:
        nuisance = matrix[:, :-1]
        indicators = np.array([[int(unit == level) for level in sorted(set(units))] for unit in units])
        if np.linalg.matrix_rank(np.column_stack((nuisance, indicators))) != nuisance.shape[1]:
            raise ValueError("ERQL paired design does not include the declared unit blocks")
    return request


@contextmanager
def _bound_anndata(source, expected_hash):
    import h5py  # type: ignore[import-untyped]
    from devbench.core.input_files import _hash_fd, _open_file, _signature, _unchanged
    from devbench.analysis.rna_scrna_input_contract import MAX_BYTES

    parent = source.parent.stat()
    parent_id = (parent.st_dev, parent.st_ino)
    with _open_file(source.parent, (source.name,), parent_id) as descriptor:
        signature = _signature(os.fstat(descriptor))
        if _hash_fd(descriptor, MAX_BYTES) != expected_hash:
            raise ValueError("ERQL source bytes differ from the registered inventory")
        os.lseek(descriptor, 0, os.SEEK_SET)
        with os.fdopen(os.dup(descriptor), "rb") as stream, h5py.File(stream, "r") as handle:
            yield handle
        if _signature(os.fstat(descriptor)) != signature:
            raise ValueError("ERQL source changed while aggregating counts")
    _unchanged(source.parent, (source.name,), parent_id, signature)


def prepare_erql(methods, inspection, *, components, observations, expected_profiles, design_spec,
                 condition_column, population_column, study_id, data_classification, profile=CANONICAL):
    """Bind T04 and T05 by streaming the exact inspected dense/CSR source.

    Observation assignments and component receipts are supplied synthetic
    declarations. The adapter checks their correspondence to the bound source.
    """
    import anndata as ad  # type: ignore[import-not-found]
    import h5py  # type: ignore[import-untyped]
    from devbench.analysis.rna_scrna_input_contract import verify_scrna_inspection
    from devbench.core.population_data import _annotation, _node
    from devbench.core.count_profiles import CountBlock, CountLimits, compile_count_profiles

    if data_classification != "synthetic":
        raise ValueError("ERQL T06 preparation requires an explicit synthetic scope")
    actual = verify_scrna_inspection(methods, inspection)
    selection = actual.get("count_source", {})
    if not selection.get("requested_count1_admissible"):
        raise ValueError("ERQL requires the admitted T04 original count source")
    if design_spec.regime not in ("S-I", "S-P", "P-P") or (
        design_spec.regime == "P-P" and profile not in MULTIFACTOR_PROFILES
    ):
        raise ValueError("Registered AnnData adapter admits synthetic within-population contrasts only")
    independence = actual["declarations"]["biological_independence"]
    if independence is None or not independence["independent"]:
        raise ValueError("ERQL requires a declared independent biological-unit axis")
    population_path = "obs/" + population_column
    population_record = next((row for row in actual["inherited_analysis"]["slots"]["obs"]["objects"]
                              if row["path"] == population_path and row["primary_pp_label_basis_candidate"]), None)
    if population_record is None:
        raise ValueError("ERQL synthetic populations require a declared independent label assignment")
    origin = None
    if design_spec.regime == "P-P":
        provenance = population_record["provenance"]
        if any(field in provenance for field in ("fit_artifact_sha256", "training_feature_ids_sha256",
                                                 "training_observation_ids_sha256", "training_scope")):
            raise ValueError(f"{profile} P-P requires labels independent of the tested RNA")
        origin = provenance["label_origin"]
    reference = actual["input_artifact_refs"]["anndata"]
    source = methods.artifact_path(reference["artifact_id"])
    if any(item.measurement != actual["declarations"]["measurement"] for item in components):
        raise ValueError("Count components contradict the inspected measurement")
    by_id = {item.observation_id: item for item in observations}
    if len(by_id) != len(observations):
        raise ValueError("ERQL requires a bijective observation assignment")
    slot = selection["requested_path"]
    with _bound_anndata(source, reference["sha256"]) as handle:
        element = handle
        for part in slot.split("/"):
            element = _node(element, part)
        var = _node(_node(handle, "raw"), "var") if slot == "raw/X" else _node(handle, "var")
        obs = _node(handle, "obs")
        matrix = element if isinstance(element, h5py.Dataset) else ad.io.sparse_dataset(element)
        if getattr(matrix, "format", None) == "csc":
            raise ValueError("ERQL registered input supports dense and CSR rows")
        n, p = matrix.shape
        ids, _ = _annotation(_node(obs, obs.attrs.get("_index", "_index")), n, 8 * 1024**2)
        genes, _ = _annotation(_node(var, var.attrs.get("_index", "_index")), p, 8 * 1024**2)
        ordered_ids = tuple(map(str, ids))
        feature_ids = tuple(map(str, genes))
        if len(set(ordered_ids)) != n or set(ordered_ids) != set(by_id):
            raise ValueError("ERQL observation assignments do not match the bound axis")
        units, _ = _annotation(_node(obs, independence["unit_column"]), n, 8 * 1024**2)
        conditions, _ = _annotation(_node(obs, condition_column), n, 8 * 1024**2)
        populations, _ = _annotation(_node(obs, population_column), n, 8 * 1024**2)
        ordered = tuple(by_id[key] for key in ordered_ids)
        if any((row.profile.unit_id, row.profile.condition, row.profile.population) != (unit, condition, population)
               for row, unit, condition, population in zip(ordered, units, conditions, populations)):
            raise ValueError("ERQL observation assignments contradict registered unit, condition or population metadata")
        if profile in MULTIFACTOR_PROFILES and design_spec.technical_factors:
            component_factors = {item.component_id: dict(item.technical_factors) for item in components}
            for factor in design_spec.technical_factors:
                labels, _ = _annotation(_node(obs, factor), n, 8 * 1024**2)
                if any(component_factors.get(row.component_id, {}).get(factor) != label
                       for row, label in zip(ordered, labels, strict=True)):
                    raise ValueError("ERQL technical factor contradicts registered observation metadata: " + factor)
        if p > 8192 or len(expected_profiles) > 256 or p * len(expected_profiles) > 1_000_000:
            raise ValueError("ERQL complete panel exceeds the engineering envelope")
        step = max(1, min(256, CountLimits().max_block_bytes // (p * 24)))
        blocks = (CountBlock(ordered[start:start+step], matrix[start:start+step]) for start in range(0, n, step))
        counts = compile_count_profiles(feature_ids, components, blocks, expected_profiles=expected_profiles)
    if methods.artifact_path(reference["artifact_id"]) != source:
        raise ValueError("Registered ERQL source changed during aggregation")
    return prepare_erql_count_profiles(
        counts, design_spec=design_spec, study_id=study_id, data_classification=data_classification,
        source_sha256=reference["sha256"], inspection_sha256=digest(actual), profile=profile,
        population_label_origin=origin,
    )


def prepare_erql_count_profiles(counts, *, design_spec, study_id, data_classification, source_sha256,
                                inspection_sha256, profile=CANONICAL, population_label_origin=None):
    """Bind a trusted adapter's compiled synthetic count inventory to a signed plan.

    The adapter owns original-count, independent-unit and label provenance.
    This entrypoint checks structural admission and retains those source joins.
    """
    from devbench.core.contrast_design import compile_contrast_design

    if data_classification != "synthetic":
        raise ValueError("ERQL count-profile preparation requires an explicit synthetic scope")
    driver = driver_for_profile(profile)
    multifactor = profile in MULTIFACTOR_PROFILES
    if design_spec.regime not in (DESIGN_AWARE_REGIMES if multifactor else LEGACY_REGIMES):
        raise ValueError("Unsupported ERQL regime for this synthetic profile")
    population_pairs = design_spec.regime == "P-P"
    if population_pairs:
        if (type(population_label_origin) is not str
                or population_label_origin not in INDEPENDENT_LABEL_ORIGINS):
            raise ValueError(f"{profile} P-P requires labels independent of the tested RNA")
    elif population_label_origin is not None:
        raise ValueError(f"Population-label basis is reserved for multifactor ERQL P-P ({profile})")
    design = compile_contrast_design(counts, design_spec)
    if design.status != "ready":
        raise ValueError("ERQL design held: " + ", ".join(design.refusal_codes))
    by_profile = {row.profile_id: row for row in counts.profiles}
    rows = [by_profile[key] for key in design.profile_ids]
    feature_ids = counts.feature_ids
    if len(rows) > 256 or len(feature_ids) > 8192 or len(rows) * len(feature_ids) > 1_000_000:
        raise ValueError("ERQL complete panel exceeds the engineering envelope")
    family_population = (design_spec.condition if population_pairs else
                         "bulk" if design_spec.regime.startswith("B-") else design_spec.population)
    request = {"schema_version": REQUEST_VERSIONS.get(profile, "erql_request/1"),
        "profile": profile, "regime": design_spec.regime, "population": family_population,
        "feature_ids": list(feature_ids),
        "profile_ids": list(design.profile_ids), "unit_ids": [row.key.unit_id for row in rows],
        "groups": [row.key.population if population_pairs else row.key.condition for row in rows],
        "denominator": design.denominator, "numerator": design.numerator,
        "counts": [list(row.counts) for row in rows], "design": [list(row) for row in design.matrix],
        "contrast": list(design.contrast)}
    if multifactor:
        request.update(condition=design_spec.condition, population_label_basis=(
            {"origin": population_label_origin, "source_sha256": source_sha256} if population_pairs else None))
    plan = ERQLPlan(plan_id="erql-" + digest(request), study_id=study_id, profile=profile,
        data_classification=data_classification, source_sha256=source_sha256, inspection_sha256=inspection_sha256,
        count_profiles_sha256=digest(asdict(counts)), design_sha256=design.design_sha256,
        request_sha256=digest(request), driver_sha256=hashlib.sha256(_read(driver, limit=1024**2)).hexdigest())
    allocation = FamilyAllocation(profile=profile, filtering_policy=ERQL_FILTER_BY_PROFILE[profile], families=(FamilySpec(
        family_id="primary", population=family_population, numerator=design.numerator,
        denominator=design.denominator, feature_ids=feature_ids, request_sha256=plan.request_sha256),))
    plan = bind_erql_families(plan, allocation, "primary")
    return plan, validate_erql_request(request, plan)


def read_erql_request(path, plan):
    return validate_erql_request(_json(_read(path, plan.request_sha256, REQUEST_LIMIT)), plan)
