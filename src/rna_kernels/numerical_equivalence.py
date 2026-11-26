"""ADR-16 equivalence for a single mounted gene family and its fitting runtimes."""

import hashlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from scipy.stats import false_discovery_control

from .mounted_bulk import MAX_FEATURES, MAX_PARQUET_BYTES

SAME_ARCH = "exact_match_same_arch"
CROSS_ARCH = "cross_arch_calibrated_match"
_RTOL, _ATOL, _NEAR_ZERO = 1e-5, 1e-7, 1e-4
_PROB_ATOL, _LOG_ATOL, _FLOOR, _BH_ATOL = 1e-5, 5e-4, 1e-300, 1e-14
_REQUIRED = {"gene_id", "estimate_status", "missing_reason", "p_value", "q_value"}
_PROBABILITIES = {"p_value", "q_value"}
_EFFECTS = {"effect", "logfc", "log2fc", "log2_fc", "log2_fold_change"}
_CONTINUOUS = _EFFECTS | {
    "lib_size",
    "lib_sizes",
    "library_size",
    "library_sizes",
    "offset",
    "offsets",
    "norm_factor",
    "norm_factors",
    "normalization_factor",
    "normalization_factors",
    "size_factors",
    "f",
    "ql_f",
    "qlf",
    "ql_f_statistic",
    "df_prior",
    "df_residual_adj",
    "df_total",
    "s2_post",
    "values",
    "lower",
    "upper",
}
_NUMERIC = _CONTINUOUS | _PROBABILITIES
_SUMMARY_FIELDS = {
    "format",
    "request_sha256",
    "family_sha256",
    "target_id",
    "family_id",
    "row_count",
    "tested_count",
    "status_counts",
    "filtered_genes",
    "failed_genes",
    "design_rank",
    "residual_df",
    "comparison",
    "measurement",
    "biological_unit",
    "solver",
    "solver_version",
    "test",
    "normalization",
    "solver_details",
}


@dataclass(frozen=True)
class ReplayRuntime:
    """Execution metadata from the fitting run, independent of the verifier host."""

    arch: str
    image_digest: str | None
    package_versions: Mapping[str, str]
    blas_threads: int | None
    blas_backend: str | None = None
    blas_library: str | None = None

    def __post_init__(self):
        if not isinstance(self.arch, str) or not self.arch.strip():
            raise ValueError("Missing runtime CPU architecture")
        if not isinstance(self.package_versions, Mapping) or not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in self.package_versions.items()
        ):
            raise ValueError("Runtime package versions must map names to versions")
        if self.blas_threads is not None and type(self.blas_threads) is not int:
            raise ValueError("Invalid BLAS thread count")
        if not all(
            v is None or isinstance(v, str)
            for v in (
                self.image_digest,
                self.blas_backend,
                self.blas_library,
            )
        ):
            raise ValueError("Invalid runtime image or BLAS metadata")

    @property
    def cpu(self) -> str:
        arch = self.arch.lower().removeprefix("linux/")
        return {"aarch64": "arm64", "x86_64": "amd64"}.get(arch, arch)


class _Receipt:
    def __init__(self, mode, source, target, alpha, oracle) -> None:
        self.data: dict[str, Any] = {
            "policy": "ADR-16",
            "mode": mode,
            "passed": True,
            "source_arch": source.cpu,
            "target_arch": target.cpu,
            "same_arch": source.cpu == target.cpu,
            "runtimes": {
                side: {
                    "image_digest": runtime.image_digest,
                    "package_versions": dict(runtime.package_versions),
                    "blas_threads": runtime.blas_threads,
                }
                for side, runtime in (("source", source), ("target", target))
            },
            "alpha": alpha,
            "checks": {},
            "boundary_band_genes": [],
            "violations": [],
            "debug_oracle": oracle,
        }

    def check(self, name, ok, field, gene=None, *, delta=None, limit=None):
        entry = self.data["checks"].setdefault(name, {"compared": 0, "failed": 0})
        entry["compared"] += 1
        if delta is not None and math.isfinite(delta):
            entry["max_delta"] = max(entry.get("max_delta", 0.0), float(delta))
        if not ok:
            self.data["passed"] = False
            entry["failed"] += 1
            # Keep exact counts and bounded examples, even for a misaligned family.
            if entry["failed"] <= 16:
                item = {"check": name, "field": field}
                if isinstance(gene, str):
                    item["gene_id"] = gene
                if delta is not None and math.isfinite(delta):
                    item["delta"] = float(delta)
                if limit is not None:
                    item["limit"] = limit
                self.data["violations"].append(item)


def _single_thread(runtime):
    return type(runtime.blas_threads) is int and runtime.blas_threads == 1


def _oracle_backend(runtime):
    """Check the declared debug backend against the library loaded by R."""
    backend = (runtime.blas_backend or "").lower()
    library = (runtime.blas_library or "").lower()
    if backend == "flexiblas":
        return "flexiblas" in library
    if backend != "netlib" or any(
        vendor in library for vendor in ("openblas", "mkl", "blis", "accelerate")
    ):
        return False
    return any(
        marker in library for marker in ("netlib", "/blas/libblas.", "/librblas.")
    )


def _runtime_checks(report, mode, source, target, oracle):
    cpus = {source.cpu, target.cpu}
    if mode == SAME_ARCH:
        valid_cpu = len(cpus) == 1 and source.cpu not in {"", "unknown"}
        report.check("runtime", valid_cpu, "same CPU architecture")
        digest = source.image_digest or ""
        report.check(
            "runtime",
            re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            and digest == target.image_digest,
            "identical pinned image digest",
        )
        report.check(
            "runtime",
            _single_thread(source) and _single_thread(target),
            "single-threaded BLAS",
        )
    elif mode == CROSS_ARCH:
        report.check("runtime", cpus == {"arm64", "amd64"}, "arm64/amd64 pair")
        pins = source.package_versions
        pinned = bool(pins) and all(
            isinstance(k, str)
            and k
            and isinstance(v, str)
            and re.fullmatch(r"[0-9][A-Za-z0-9.+_~-]*", v)
            for k, v in pins.items()
        )
        report.check(
            "runtime",
            pinned and pins == target.package_versions,
            "identical pinned package versions",
        )
    else:
        report.check("runtime", False, "unsupported verification mode")
    if oracle:
        info = {}
        for side, runtime in (("source", source), ("target", target)):
            info[side] = {
                "backend": runtime.blas_backend,
                "threads": runtime.blas_threads,
            }
            if runtime.blas_library:
                info[side]["library"] = runtime.blas_library
            report.check(
                "debug_oracle",
                _single_thread(runtime) and _oracle_backend(runtime),
                side,
            )
        report.data["debug_oracle_metadata"] = info


def _key(name):
    return name.lower().replace(".", "_")


def _kind(value):
    if value is None:
        return "null"
    if type(value) not in (int, float):
        return "invalid"
    if math.isfinite(value):
        return "finite"
    if math.isnan(value):
        return "nan"
    return "+inf" if value > 0 else "-inf"


def _numeric(report, key, a, b, label, gene):
    ka, kb = _kind(a), _kind(b)
    report.check("structure", ka == kb and ka != "invalid", label + " mask/type", gene)
    if ka != "finite" or kb != "finite":
        return
    delta = abs(a - b)
    if key in _PROBABILITIES:
        report.check(
            key + ".linear",
            delta <= _PROB_ATOL,
            label,
            gene,
            delta=delta,
            limit=_PROB_ATOL,
        )
        tail = abs(math.log10(max(a, _FLOOR)) - math.log10(max(b, _FLOOR)))
        report.check(
            key + ".log10", tail <= _LOG_ATOL, label, gene, delta=tail, limit=_LOG_ATOL
        )
    else:
        scale = max(abs(a), abs(b))
        limit = _ATOL if scale < _NEAR_ZERO else _ATOL + _RTOL * scale
        report.check(
            "continuous", delta <= limit, label, gene, delta=delta, limit=limit
        )


def _compare_value(report, name, a, b, label, gene=None):
    # Vector offsets, sample factors and mounted coordinates retain their ordering.
    if isinstance(a, list) or isinstance(b, list):
        if not (isinstance(a, list) and isinstance(b, list) and len(a) == len(b)):
            report.check("structure", False, label + " shape", gene)
            return
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _compare_value(report, name, x, y, f"{label}[{i}]", gene)
    elif isinstance(a, dict) or isinstance(b, dict):
        if not (isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys()):
            report.check("structure", False, label + " fields", gene)
            return
        for k in a:
            quantity = (
                "norm_factors" if (name, k) == ("normalization", "factors") else k
            )
            _compare_value(report, quantity, a[k], b[k], f"{label}.{k}", gene)
    elif _key(name) in _NUMERIC:
        _numeric(report, _key(name), a, b, label, gene)
    else:
        match = type(a) is type(b) and (a == b or _kind(a) == _kind(b) == "nan")
        report.check("structure", match, label, gene)


def _read_table(payload):
    if len(payload) > MAX_PARQUET_BYTES:
        raise ValueError("gene table exceeds mounted byte limit")
    parquet = pq.ParquetFile(pa.BufferReader(payload))
    if not 0 < parquet.metadata.num_rows <= MAX_FEATURES:
        raise ValueError("gene table has invalid row count")
    schema = parquet.schema_arrow
    if len(set(schema.names)) != len(schema) or not _REQUIRED <= set(schema.names):
        raise ValueError("gene table has missing or duplicate columns")
    return parquet.read(use_threads=False)


def _check_family(report, columns, side):
    """Recompute BH after filtering, with one slot for each failed fit."""
    ids = columns["gene_id"]
    if not all(isinstance(g, str) and g for g in ids) or len(set(ids)) != len(ids):
        report.check("structure", False, side + " gene identifiers")
        return
    family = []
    estimates = []
    for i, (g, status, p, q) in enumerate(
        zip(
            ids,
            columns["estimate_status"],
            columns["p_value"],
            columns["q_value"],
            strict=True,
        )
    ):
        reason = columns["missing_reason"][i]
        if status == "estimated":
            report.check(
                "structure", reason is None, side + " estimated missing_reason", g
            )
            valid = _kind(p) == _kind(q) == "finite" and 0 <= p <= q <= 1
            report.check("probability_order", valid, side + " 0 <= p <= q <= 1", g)
            estimates.append((len(family), i))
            family.append(p)
        else:
            valid = (
                status in ("failed", "filtered") and isinstance(reason, str) and reason
            )
            report.check("structure", valid, side + " unestimated status/reason", g)
            missing = (p, q, *(v[i] for k, v in columns.items() if _key(k) in _EFFECTS))
            report.check(
                "structure",
                all(_kind(v) in {"null", "nan"} for v in missing),
                side + " unestimated statistics",
                g,
            )
            if status == "failed":
                family.append(1.0)
    if not estimates:
        return
    if not all(_kind(p) == "finite" and 0 <= p <= 1 for p in family):
        report.check("bh_" + side, False, "invalid BH input")
        return
    adjusted = false_discovery_control(family, method="bh")
    for position, i in estimates:
        q = columns["q_value"][i]
        delta = (
            abs(q - max(family[position], adjusted[position]))
            if _kind(q) == "finite"
            else math.inf
        )
        report.check(
            "bh_" + side,
            delta <= _BH_ATOL,
            "q_value",
            ids[i],
            delta=delta,
            limit=_BH_ATOL,
        )


def _discoveries(report, left, right, alpha, omnibus):
    # Compute the declared decimal endpoints before rounding each to Parquet float64.
    center, width = Decimal(str(alpha)), 2 * Decimal(str(_PROB_ATOL))
    lower, upper = float(center - width), float(center + width)
    report.data["boundary_band"] = [lower, upper]
    boundary = {
        g
        for table in (left, right)
        for g, status, q in zip(
            table["gene_id"], table["estimate_status"], table["q_value"], strict=True
        )
        if isinstance(g, str)
        and status == "estimated"
        and _kind(q) == "finite"
        and lower <= q <= upper
    }
    report.data["boundary_band_genes"] = sorted(boundary)
    if left["gene_id"] != right["gene_id"]:
        return
    effects = [name for name in left.keys() & right.keys() if _key(name) in _EFFECTS]
    for i, gene in enumerate(left["gene_id"]):
        q1, q2 = left["q_value"][i], right["q_value"][i]
        if not isinstance(gene, str) or _kind(q1) != "finite" or _kind(q2) != "finite":
            continue
        d1, d2 = q1 <= alpha, q2 <= alpha
        if gene not in boundary:
            report.check("discovery_set", d1 == d2, "q_value", gene)
        if d1 or d2:
            _direction(report, left, right, i, gene, effects, omnibus)


def _direction(report, left, right, i, gene, effects, omnibus):
    if not effects:
        report.check("discovery_direction", False, "missing effect column", gene)
    signed = [(name, left[name][i], right[name][i]) for name in effects]
    if "values" in left and "values" in right:
        x, y = left["values"][i], right["values"][i]
        if isinstance(x, list) and isinstance(y, list) and len(x) == len(y):
            signed.extend(
                (f"values[{j}]", a, b)
                for j, (a, b) in enumerate(zip(x, y, strict=True))
            )
    for name, a, b in signed:
        if omnibus and a is None and b is None:
            # The mounted nested/LRT output has no signed coefficient by design.
            report.check("unsigned_omnibus", True, name, gene)
            continue
        valid = _kind(a) == _kind(b) == "finite" and (a > 0) - (a < 0) == (b > 0) - (
            b < 0
        )
        report.check("discovery_direction", valid, name, gene)


def _cross_arch(report, source, target, summaries, alpha):
    tables = [_read_table(payload) for payload in (source, target)]
    left, right = [table.to_pydict() for table in tables]
    for side, summary, columns in zip(
        ("source", "target"), summaries, (left, right), strict=True
    ):
        rank = summary.get("design_rank")
        report.check("structure", type(rank) is int and rank > 0, side + " design_rank")
        _check_family(report, columns, side)
    a, b = summaries
    keys = {
        k for k in a.keys() | b.keys() if k in _SUMMARY_FIELDS or _key(k) in _CONTINUOUS
    }
    for name in sorted(keys):
        if name in a and name in b:
            _compare_value(report, name, a[name], b[name], "summary." + name)
        else:
            report.check("structure", False, "summary." + name)
    omnibus = a.get("comparison") == b.get("comparison") and a.get("comparison") in (
        "nested",
        "lrt",
    )
    _discoveries(report, left, right, alpha, omnibus)
    same_schema = tables[0].schema.equals(tables[1].schema, check_metadata=False)
    report.check("structure", same_schema, "Parquet schema")
    # This canonical mounted footer binds the family, request, axes and claim scope.
    footers = [
        (table.schema.metadata or {}).get(b"devbench.result") for table in tables
    ]
    report.check("structure", footers[0] == footers[1], "devbench.result footer")
    aligned = left["gene_id"] == right["gene_id"]
    report.check("structure", aligned, "gene_id order")
    if not (aligned and same_schema):
        return
    for name, values in left.items():
        for gene, x, y in zip(left["gene_id"], values, right[name], strict=True):
            _compare_value(report, name, x, y, name, gene)


def verify_numerical_equivalence(
    source: bytes,
    target: bytes,
    *,
    mode: str,
    source_runtime: ReplayRuntime,
    target_runtime: ReplayRuntime,
    source_summary: Mapping[str, Any] | None = None,
    target_summary: Mapping[str, Any] | None = None,
    alpha: float = 0.05,
    blas_debug_oracle: bool = False,
) -> dict[str, Any]:
    """Compare original bytes. Calibrated mode reads mounted Parquet and design ranks.

    The optional debug oracle records the fitting runtimes' single-threaded
    Netlib/FlexiBLAS declarations. It never selects a backend or changes a bound.
    """
    if _kind(alpha) != "finite" or not 0 <= alpha <= 1:
        raise ValueError("alpha must be finite and between zero and one")
    report = _Receipt(mode, source_runtime, target_runtime, alpha, blas_debug_oracle)
    _runtime_checks(report, mode, source_runtime, target_runtime, blas_debug_oracle)
    summaries = (source_summary or {}, target_summary or {})
    for side, payload, summary in zip(
        ("source", "target"), (source, target), summaries, strict=True
    ):
        digest = hashlib.sha256(payload).hexdigest()
        report.data[side + "_sha256"] = digest
        if "genes_sha256" in summary:
            report.check(
                "artifact_binding",
                digest == summary["genes_sha256"],
                side + " genes_sha256",
            )
    for field in ("request_sha256", "family_sha256"):
        if any(field in summary for summary in summaries):
            value = summaries[0].get(field)
            report.check(
                "artifact_binding",
                bool(value) and value == summaries[1].get(field),
                field,
            )
            report.data[field] = value if isinstance(value, str) else None
    if mode == SAME_ARCH:
        report.check(
            "sha256",
            report.data["source_sha256"] == report.data["target_sha256"],
            "original artifact bytes",
        )
    elif mode == CROSS_ARCH:
        try:
            _cross_arch(report, source, target, summaries, alpha)
        except (ValueError, TypeError, OverflowError, OSError) as exc:
            report.check(
                "structure", False, "invalid mounted family: " + str(exc)[:160]
            )
    return report.data
