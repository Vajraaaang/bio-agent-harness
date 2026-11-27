"""Bounded donor identity screens. All decisions here are L1 forensic observations."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from itertools import combinations
from math import prod
from typing import Annotated, Final, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ._hashing import canonical_sha256
from .count_forensics import (
    SUSPECTED_TRANSFORM,
    CountDistribution,
    declares_count_transform,
)

CAVEAT: Final = "Genotype-free expression checks cannot prove donor independence."
_Y_GENES = {"UTY", "RPS4Y1", "DDX3Y", "KDM5D"}
_MAX_DONORS = 256
_MAX_PROFILE_BYTES = 64 << 20
_MAX_BLOCK_BYTES = 16 << 20
Label = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[^/\x00]+$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
GeneticMethod = Literal[
    "expression_only", "souporcell", "vireo", "demuxlet", "verifybamid2", "qtltools_mbv"
]
Status = Literal["passed", "warning", "conflict_halted", "runtime_unavailable"]


class DonorForensicsParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    donor_key: Label | None = None
    sex_key: Label | None = None
    species: Label | None = None
    expected_donor: Label | None = None
    genetic_method: GeneticMethod = "expression_only"


class _Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class DonorConcordance(_Evidence):
    donor: str
    observations: int
    declared_sex: str | None
    marker_pattern: Literal["XIST_dominant", "Y_dominant", "not_evaluable"]
    sex_concordance: Literal["concordant", "discordant", "not_evaluable"]
    xist_counts: float
    y_counts: float


class DuplicatePair(_Evidence):
    donors: tuple[str, str]
    profile_correlation: float
    total_variation: float


class GeneticDonor(_Evidence):
    donor: str
    concordant: int = 0
    discordant: int = 0
    ambiguous: int = 0
    contamination_fraction: float | None = None
    het_concordance: float | None = None
    hom_concordance: float | None = None


class InterDonorDoublet(_Evidence):
    barcode: str
    parents: tuple[str, str]


class GeneticEvidence(_Evidence):
    method: GeneticMethod
    status: Status
    findings: list[str]
    per_donor: list[GeneticDonor] = Field(default_factory=list)
    inter_donor_doublet_assignments: list[InterDonorDoublet] = Field(
        default_factory=list
    )
    output_sha256: Digest | None = None
    executable_sha256: Digest | None = None


class DonorForensicsReceipt(_Evidence):
    status: Status
    forensic_evidence_level: Literal["expression_only_L1", "genetic_forensics_L1"]
    caveat: Literal[
        "Genotype-free expression checks cannot prove donor independence."
    ] = CAVEAT
    findings: list[str]
    per_donor: list[DonorConcordance]
    duplicate_pairs: list[DuplicatePair]
    genetic: GeneticEvidence | None = None
    input_sha256: dict[str, Digest]
    parameters_sha256: Digest
    provenance_sha256: Digest
    clinical_release_allowed: Literal[False] = False

    @model_validator(mode="after")
    def _bound_provenance(self):
        if self.provenance_sha256 != canonical_sha256(
            self.model_dump(mode="json", exclude={"provenance_sha256"})
        ):
            raise ValueError("Donor forensic receipt provenance mismatch")
        return self


def _no_check():
    pass


def _choose(columns, explicit, candidates):
    if explicit is not None:
        if explicit not in columns:
            raise ValueError("Declared forensic observation column is missing")
        return explicit
    return next((key for key in candidates if key in columns), None)


def donor_column(source, params):
    return _choose(
        source.obs,
        params.donor_key,
        ("donor_id", "donor", "animal_id", "subject_id", "individual"),
    )


def _sex(value):
    name = str(value).strip().casefold()
    if name in {"xx", "female", "f"}:
        return "female"
    if name in {"xy", "male", "m"}:
        return "male"
    return None


def _compare_sex(donor, observations, declarations, profile, xist, y, evaluable):
    x, y_total = float(profile[xist].sum()), float(profile[y].sum())
    declared = next(iter(declarations)) if len(declarations) == 1 else None
    pattern = "not_evaluable"
    if evaluable:
        if x >= 5 and x >= 5 * max(1, y_total):
            pattern = "XIST_dominant"
        if y_total >= 5 and y_total >= 5 * max(1, x):
            pattern = "Y_dominant"
    concordance = "not_evaluable"
    if declared and pattern != "not_evaluable":
        concordance = (
            "concordant"
            if ((pattern == "XIST_dominant") == (declared == "female"))
            else "discordant"
        )
    return DonorConcordance(
        donor=donor,
        observations=observations,
        declared_sex=declared,
        marker_pattern=pattern,
        sex_concordance=concordance,
        xist_counts=x,
        y_counts=y_total,
    )


def _duplicates(profiles, check_time):
    pairs = []
    assessed = False
    for (a, x), (b, y) in combinations(profiles.items(), 2):
        check_time()
        # Correlation alone is often high for unrelated donors from one tissue.
        # Check the complete depth-normalized shape as well, including zeros.
        if np.count_nonzero(x + y) < 3 or not x.sum() or not y.sum():
            continue
        x, y = x / x.sum(), y / y.sum()
        if np.ptp(x) == 0 or np.ptp(y) == 0:
            continue
        assessed = True
        tv = float(np.abs(x - y).sum() / 2)
        if tv > 0.005:
            continue
        correlation = float(np.clip(np.corrcoef(x, y)[0, 1], -1, 1))
        if correlation >= 0.9999:
            pairs.append(
                DuplicatePair(
                    donors=(a, b), profile_correlation=correlation, total_variation=tv
                )
            )
    return pairs, assessed


def _groups(source, params):
    import pandas as pd

    donor_key = donor_column(source, params)
    sex_key = _choose(
        source.obs, params.sex_key, ("sex", "declared_sex", "donor_sex", "sex_at_birth")
    )
    _, m = source.shape
    donors = (
        source.obs[donor_key].astype("string").str.strip()
        if donor_key
        else pd.Series(pd.NA, index=source.obs.index, dtype="string")
    )
    donors = donors.replace("", pd.NA)
    counts = Counter(donors.dropna())
    if len(counts) > _MAX_DONORS or len(counts) * m * 8 > _MAX_PROFILE_BYTES:
        raise ValueError("Donor pseudobulk profiles exceed the bounded forensic budget")
    profiles = {k: np.zeros(m, dtype=np.float64) for k in sorted(counts)}
    declarations: dict[str, set[str]] = {k: set() for k in profiles}
    if sex_key:
        for donor, label in zip(donors, source.obs[sex_key], strict=True):
            if pd.notna(donor) and (sex := _sex(label)):
                declarations[donor].add(sex)
    return donors, counts, profiles, declarations


def _count_values(values):
    return (
        values.dtype.kind in "iuf"
        and np.isfinite(values).all()
        and np.all((values >= 0) & (values < 2**53))
        and (values.dtype.kind != "f" or np.equal(values, np.floor(values)).all())
    )


def _aggregate(source, donors, profiles, count_forensics, check_time):
    from scipy import sparse

    n, m = source.shape
    distribution = (
        None
        if count_forensics is not None
        else CountDistribution(total_values=n * m, major_width=m)
    )
    invalid = count_forensics is not None and not (
        count_forensics["integer_valued"] and count_forensics["nonnegative"]
    )
    if not invalid and (profiles or distribution is not None):
        source.validate_sparse_storage(block_bytes=_MAX_BLOCK_BYTES)
        offset = 0
        for block in source.iter_rows(block_rows=256, max_bytes=_MAX_BLOCK_BYTES):
            check_time()
            matrix = block.X
            values = matrix.data if sparse.issparse(matrix) else np.asarray(matrix)
            if not _count_values(values):
                return "not_raw_count_measurements"
            if sparse.issparse(matrix) and not matrix.has_canonical_format:
                raise ValueError("Noncanonical sparse expression coordinates")
            if distribution is not None:
                if sparse.issparse(matrix):
                    coo = matrix.tocoo(copy=False)
                    distribution.observe(
                        coo.data, library_indices=offset + coo.row.astype(np.int64)
                    )
                else:
                    distribution.observe(matrix)
            group = (
                donors.iloc[offset : offset + block.n_obs]
                .fillna("")
                .to_numpy(dtype=str)
            )
            for donor in np.unique(group):
                if donor:
                    profiles[donor] += np.asarray(
                        matrix[group == donor].sum(axis=0, dtype=np.float64)
                    ).ravel()
            offset += block.n_obs
    if invalid:
        return "not_raw_count_measurements"
    transformed = (
        distribution.suspected_transform
        if distribution is not None
        else count_forensics["suspected_transformed"]
    )
    return SUSPECTED_TRANSFORM if transformed else None


def _sex_rows(source, params, profiles, counts, declarations):
    import pandas as pd

    key = _choose(
        source.var, None, ("gene_symbols", "gene_symbol", "feature_name", "gene_name")
    )
    values = source.var.index if key is None else source.var[key]
    symbols = pd.Index(values).fillna("").astype(str).str.upper()
    xist = np.asarray(symbols == "XIST")
    y = np.asarray(symbols.isin(_Y_GENES))
    species = {params.species.strip().casefold()} if params.species else set()
    for key in ("organism", "species", "organism_ontology_term_id"):
        if key in source.obs:
            species.update(
                str(v).strip().casefold() for v in source.obs[key].dropna().unique()
            )
    supported = species <= {
        "",
        "human",
        "homo sapiens",
        "9606",
        "mouse",
        "mus musculus",
        "10090",
        "ncbitaxon:10090",
        "ncbitaxon:9606",
    }
    return [
        _compare_sex(
            k,
            counts[k],
            declarations[k],
            v,
            xist,
            y,
            supported and xist.any() and y.any(),
        )
        for k, v in profiles.items()
    ]


def _screen(source, params, count_forensics, check_time):
    donors, counts, profiles, declarations = _groups(source, params)
    findings = []
    if donors.isna().any():
        findings.append("donor_identity_not_evaluable")
    count_issue = _aggregate(source, donors, profiles, count_forensics, check_time)
    if count_issue:
        findings.append(count_issue)
    if count_issue == "not_raw_count_measurements":
        return findings, [], []
    rows = _sex_rows(source, params, profiles, counts, declarations)
    if any(len(v) > 1 for v in declarations.values()):
        findings.append("conflicting_declared_sex")
    if any(row.sex_concordance == "discordant" for row in rows):
        findings.append("sex_chromosome_discordance")
    if not rows or any(row.sex_concordance == "not_evaluable" for row in rows):
        findings.append("sex_chromosome_not_evaluable")
    pairs, assessed = _duplicates(profiles, check_time)
    if pairs:
        findings.append("near_duplicate_donor_profiles")
    if not assessed:
        findings.append("duplicate_donor_not_evaluable")
    return findings, rows, pairs


_HALT = {
    SUSPECTED_TRANSFORM,
    "not_raw_count_measurements",
    "sex_chromosome_discordance",
    "conflicting_declared_sex",
    "near_duplicate_donor_profiles",
}


def _logged(source):
    import h5py

    from .population_data import _node

    root = source._handle
    if "uns" not in root:
        return False
    uns = _node(root, "uns") if isinstance(root, h5py.Group) else root["uns"]
    return hasattr(uns, "keys") and "log1p" in uns


def screen_expression(source, params, *, count_forensics=None, check_time=_no_check):
    findings, rows, pairs = _screen(source, params, count_forensics, check_time)
    # An integer matrix with declared transformation history is still ineligible.
    node = source._element
    if (
        declares_count_transform(
            node.attrs.get("representation"), node.attrs.get("transformation")
        )
        or _logged(source)
    ) and SUSPECTED_TRANSFORM not in findings:
        findings.append(SUSPECTED_TRANSFORM)
    status = (
        "conflict_halted"
        if set(findings) & _HALT
        else "warning"
        if findings
        else "passed"
    )
    return status, findings, rows, pairs


def receipt(status, findings, rows, pairs, hashes, params, genetic=None):
    level = "expression_only_L1"
    if genetic is not None:
        findings = [*findings, *genetic.findings]
        if genetic.output_sha256 is not None:
            level = "genetic_forensics_L1"
        for severity in ("conflict_halted", "runtime_unavailable", "warning"):
            if severity in (status, genetic.status):
                status = severity
                break
    fields = dict(
        status=status,
        findings=findings,
        per_donor=rows,
        duplicate_pairs=pairs,
        genetic=genetic,
        input_sha256=hashes,
        forensic_evidence_level=level,
        parameters_sha256=canonical_sha256(params.model_dump(mode="json")),
    )
    provisional = DonorForensicsReceipt.model_construct(**fields)
    digest = canonical_sha256(
        provisional.model_dump(mode="json", exclude={"provenance_sha256"})
    )
    return DonorForensicsReceipt(**fields, provenance_sha256=digest)


def _intake(group, source_sha256, counts, check_time):
    # Minimal H5AD headers admitted for structural inventory can lack annotated
    # dataframe axes. Such inputs cannot resolve identity, even with integer X.
    if counts is not None and not (counts["integer_valued"] and counts["nonnegative"]):
        return dict(
            status="warning",
            findings=["count_screen_not_evaluable"],
            forensic_evidence_level="expression_only_L1",
            caveat=CAVEAT,
        )
    obs = group.get("obs")
    keys = ("donor_id", "donor", "animal_id", "subject_id", "individual")
    if obs is None or not any(k in obs for k in keys):
        flags = ["donor_identity_not_evaluable"]
        status = "warning"
        if counts is not None and counts["suspected_transformed"]:
            flags.append(SUSPECTED_TRANSFORM)
            status = "conflict_halted"
    else:
        from .expression_io import BackedExpression

        source = BackedExpression(group, None)
        status, flags, _, _ = screen_expression(
            source,
            DonorForensicsParameters(),
            count_forensics=counts,
            check_time=check_time,
        )
    return dict(
        status=status,
        findings=flags,
        forensic_evidence_level="expression_only_L1",
        caveat=CAVEAT,
    )


def intake_h5ad_forensics(
    stream,
    *,
    source_sha256: str,
    count_forensics=None,
    check_time: Callable = _no_check,
):
    """Summarize an already vetted, descriptor-bound public H5AD without IDs."""
    import h5py

    with h5py.File(stream, "r", rdcc_nbytes=1 << 20) as group:
        return _intake(group, source_sha256, count_forensics, check_time)


def check_zarr_axes(group):
    """Bound materialization of annotated axes before constructing BackedExpression."""
    bytes_used = 0
    nodes = [group["obs"], group["var"]]
    while nodes:
        node = nodes.pop()
        if hasattr(node, "dtype"):
            bytes_used += prod(node.shape) * max(
                node.dtype.itemsize, 256 if node.dtype.hasobject else 8
            )
            if bytes_used > 64 << 20:
                raise ValueError("Zarr axes exceed the bounded forensic budget")
        else:
            nodes.extend(node[key] for key in node)


def intake_zarr_forensics(
    group, *, source_sha256: str, count_forensics=None, check_time: Callable = _no_check
):
    """Accept a checksum-bound group with vetted codecs and bounded local chunks."""
    if "obs" in group and "var" in group:
        check_zarr_axes(group)
    return _intake(group, source_sha256, count_forensics, check_time)


# These names are mounted by the daemon. Callers cannot supply shell arguments
# or paths to executables. Vireo consumes per-cell AD/DP plus donor genotypes.
GENETIC_PORTS = {
    "expression_only": frozenset(),
    "souporcell": frozenset(
        {"bam", "bam_index", "reference_fasta", "reference_fai", "barcodes"}
    ),
    "vireo": frozenset({"cell_vcf", "donor_vcf"}),
    "demuxlet": frozenset(
        {"bam", "bam_index", "donor_vcf", "donor_vcf_index", "barcodes"}
    ),
    "verifybamid2": frozenset(
        {
            "bam",
            "bam_index",
            "reference_fasta",
            "reference_fai",
            "svd_ud",
            "svd_mu",
            "svd_bed",
        }
    ),
    "qtltools_mbv": frozenset({"bam", "bam_index", "donor_vcf", "donor_vcf_index"}),
}
