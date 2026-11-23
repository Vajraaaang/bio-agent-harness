"""Precommitted ERQL family budgets and candidate result accounting."""

import hashlib
import math
import re
from fractions import Fraction
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from rna_kernels.canonical import canonical_json

ALPHA = Fraction(1, 20)
Label = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
Sha = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Finite = Annotated[float, Field(allow_inf_nan=False)]
ERQLProfile = Literal[
    "ERQL-1.1", "ERQL-1.1-estimateDisp-candidate/1", "ERQL-1.2", "ERQL-1.3"
]
ERQLFilteringPolicy = Literal[
    "erql-filter-by-expr/1",
    "erql-design-filter-by-expr/1",
    "erql-group-filter-by-expr/1",
]
ERQL_FILTER_BY_PROFILE: MappingProxyType[ERQLProfile, ERQLFilteringPolicy] = (
    MappingProxyType(
        {
            "ERQL-1.1": "erql-filter-by-expr/1",
            "ERQL-1.1-estimateDisp-candidate/1": "erql-filter-by-expr/1",
            "ERQL-1.2": "erql-design-filter-by-expr/1",
            "ERQL-1.3": "erql-group-filter-by-expr/1",
        }
    )
)


def _weight(value):
    if (
        type(value) is not str
        or len(value) > 48
        or not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]+)?|[0-9]+/[1-9][0-9]*)", value)
    ):
        raise ValueError(
            "Family weight requires an exact nonnegative decimal or fraction"
        )
    number = Fraction(value)
    if number > 1:
        raise ValueError("Family weight exceeds one")
    return number


class _Frozen(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", revalidate_instances="always"
    )


class FamilySpec(_Frozen):
    family_id: Label
    population: Label
    numerator: Label
    denominator: Label
    feature_ids: Annotated[tuple[Label, ...], Field(max_length=100_000)] = ()
    request_sha256: Sha | None = None
    primary: bool = True
    weight: str | None = None
    dependencies: tuple[Label, ...] = ()

    @field_validator("weight")
    @classmethod
    def normalize_weight(cls, value):
        return None if value is None else str(_weight(value))

    @model_validator(mode="after")
    def check_scope(self):
        if self.numerator == self.denominator or len(set(self.feature_ids)) != len(
            self.feature_ids
        ):
            raise ValueError(
                "Family needs a directed contrast and unique measured features"
            )
        if self.request_sha256 is not None and not self.feature_ids:
            raise ValueError(
                "Executable family needs its complete measured feature panel"
            )
        if len(self.dependencies) > 16 or len(set(self.dependencies)) != len(
            self.dependencies
        ):
            raise ValueError("Invalid family dependencies")
        return self


class FamilyAllocation(_Frozen):
    format: Literal["devbench.erql-family-allocation/1"] = (
        "devbench.erql-family-allocation/1"
    )
    profile: ERQLProfile = "ERQL-1.1"
    filtering_policy: ERQLFilteringPolicy = "erql-filter-by-expr/1"
    failure_policy: Literal["withhold-family/1"] = "withhold-family/1"
    families: Annotated[tuple[FamilySpec, ...], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def check_allocation(self):
        if self.filtering_policy != ERQL_FILTER_BY_PROFILE[self.profile]:
            raise ValueError("Family filtering policy must match its ERQL profile")
        if any(len(family.feature_ids) > 8192 for family in self.families):
            raise ValueError(
                "Historical ERQL allocation exceeds its 8192-feature bound"
            )
        keys = {
            (f.population, *sorted((f.numerator, f.denominator))) for f in self.families
        }
        ids = {f.family_id for f in self.families}
        requests = [
            f.request_sha256 for f in self.families if f.request_sha256 is not None
        ]
        if min(len(keys), len(ids)) != len(self.families) or len(set(requests)) != len(
            requests
        ):
            raise ValueError("Duplicate family, contrast, or execution request")
        weights = [f.weight for f in self.families]
        if any(weight is None for weight in weights):
            if not all(weight is None for weight in weights):
                raise ValueError("Supply every explicit weight or use equal allocation")
            count = sum(f.primary for f in self.families)
            default = str(Fraction(1, count)) if count else "0"
            object.__setattr__(
                self,
                "families",
                tuple(
                    f.model_copy(update={"weight": default if f.primary else "0"})
                    for f in self.families
                ),
            )
        if any(f.primary and not _weight(f.weight) for f in self.families):
            raise ValueError("Every primary family requires positive weight")
        if sum(_weight(f.weight) for f in self.families) > 1:
            raise ValueError("Family allocation exceeds one")
        return self

    def sha256(self):
        return hashlib.sha256(canonical_json(self.model_dump(mode="json"))).hexdigest()

    def family(self, family_id):
        for family in self.families:
            if family.family_id == family_id:
                return family
        raise ValueError("Family is outside the signed inventory")


def budgeted_bh(p_values, weight, *, pad_failed=False):
    """Local BH oracle. Padding here tests arithmetic and grants no admission."""
    weight = _weight(weight)
    if any(value is None for value in p_values) and not pad_failed:
        raise ValueError("Family contains a failed eligible test")
    if any(
        value is not None
        and (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not 0 <= value <= 1
        )
        for value in p_values
    ):
        raise ValueError("Invalid family probability")
    order = sorted(
        range(len(p_values)), key=lambda i: 1 if p_values[i] is None else p_values[i]
    )
    results = [
        dict(p=None, q_local=None, q_budget=None, passes_budget_threshold=False)
        for _ in order
    ]
    running = Fraction(1)
    for rank in range(len(order), 0, -1):
        index = order[rank - 1]
        p = p_values[index]
        running = min(running, Fraction(1 if p is None else str(p)) * len(order) / rank)
        if p is not None:
            budget = min(Fraction(1), running / weight) if weight else None
            results[index] = dict(
                p=p,
                q_local=float(running),
                q_budget=None if budget is None else float(budget),
                passes_budget_threshold=budget is not None and budget <= ALPHA,
            )
    return results


class FeatureEvidence(_Frozen):
    gene_id: Label
    status: Literal["tested", "failed", "filtered_all_zero", "filtered"]
    p: Probability | None = None
    effect: Finite | None = None
    reason: Label | None = None

    @model_validator(mode="after")
    def check_status(self):
        if self.status == "tested":
            if self.p is None or self.reason is not None:
                raise ValueError("Tested feature requires a valid probability")
        elif self.p is not None or self.effect is not None or self.reason is None:
            raise ValueError(
                "Failed and filtered features require a reason and null statistics"
            )
        return self


class FamilyEvidence(_Frozen):
    family_id: Label
    allocation_sha256: Sha
    request_sha256: Sha | None
    status: Literal["observed", "withheld", "unsupported"]
    reason: Label | None = None
    eligible_features: tuple[Label, ...] = ()
    rows: tuple[FeatureEvidence, ...] = ()


def _bound_family_evidence(allocation, evidence, identity):
    supplied = {}
    for item in evidence:
        item = FamilyEvidence.model_validate(item)
        spec = allocation.family(item.family_id)
        if (
            item.family_id in supplied
            or item.allocation_sha256 != identity
            or item.request_sha256 != spec.request_sha256
        ):
            raise ValueError("Duplicate or mismatched family evidence binding")
        if item.status == "observed":
            if (
                spec.request_sha256 is None
                or item.reason is not None
                or tuple(row.gene_id for row in item.rows) != spec.feature_ids
                or tuple(
                    row.gene_id
                    for row in item.rows
                    if row.status in {"tested", "failed"}
                )
                != item.eligible_features
            ):
                raise ValueError(
                    "Incomplete measured panel or inconsistent eligible family"
                )
        elif item.rows or item.eligible_features or item.reason is None:
            raise ValueError(
                "Unavailable family requires an explicit reason and no observed rows"
            )
        supplied[item.family_id] = item
    return supplied


def synthesize_families(allocation, evidence=(), *, shared_holds=None):
    """Account for every family. Withheld and failed families never recycle weight."""
    holds = {} if shared_holds is None else shared_holds
    known = {key for spec in allocation.families for key in spec.dependencies}
    if (
        type(holds) is not dict
        or set(holds) - known
        or any(
            not isinstance(reason, str) or not reason.strip() or len(reason) > 200
            for reason in holds.values()
        )
    ):
        raise ValueError("Invalid shared dependency hold")
    identity = allocation.sha256()
    supplied = _bound_family_evidence(allocation, evidence, identity)

    families = []
    for spec in allocation.families:
        item = supplied.get(spec.family_id)
        rows = item.rows if item is not None else ()
        eligible = tuple(row for row in rows if row.status in {"tested", "failed"})
        reason = item.reason if item is not None else "result_unavailable"
        status = item.status if item is not None else "withheld"
        if status == "observed":
            if any(row.status == "failed" for row in eligible):
                status, reason = "withheld", "failed_eligible_test"
            elif not eligible:
                status, reason = "empty", "filter_retained_no_features"
            else:
                status = "ready"
        for dependency in spec.dependencies:
            if dependency in holds:
                status, reason = "withheld", holds[dependency]
                break
        weights = _weight(spec.weight)
        adjusted = iter(
            budgeted_bh(tuple(row.p for row in eligible), spec.weight)
            if status == "ready"
            else ()
        )
        records = []
        for row in rows:
            values = (
                next(adjusted)
                if status == "ready" and row.status == "tested"
                else dict(
                    p=None, q_local=None, q_budget=None, passes_budget_threshold=False
                )
            )
            records.append(
                dict(
                    gene_id=row.gene_id,
                    status=row.status,
                    reason=row.reason or reason,
                    effect=row.effect if status == "ready" else None,
                    **values,
                )
            )
        families.append(
            {
                "family_id": spec.family_id,
                "population": spec.population,
                "numerator": spec.numerator,
                "denominator": spec.denominator,
                "weight": spec.weight,
                "status": status,
                "reason": reason,
                "unused_weight": "0" if status == "ready" else spec.weight,
                "local_alpha": float(ALPHA * weights),
                "feature_ids": list(spec.feature_ids),
                "eligible_features": list(item.eligible_features)
                if item is not None and item.status == "observed"
                else None,
                "counts": {
                    "measured": len(spec.feature_ids),
                    "eligible": len(eligible),
                    "attempted": len(eligible),
                    "failed": sum(row.status == "failed" for row in eligible),
                    "tested": sum(row.status == "tested" for row in eligible),
                    "filtered": len(rows) - len(eligible),
                    "reported": len(eligible) if status == "ready" else 0,
                }
                if item is not None and item.status == "observed"
                else None,
                "genes": records,
            }
        )
    return {
        "format": "devbench.erql-family-synthesis/1",
        "allocation_sha256": identity,
        "alpha": float(ALPHA),
        "scope": "synthetic_candidate",
        "publication_eligible": False,
        "families": families,
    }
