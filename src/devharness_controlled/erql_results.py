"""Validate complete, versioned ERQL worker output before accepting its receipt."""
from collections import Counter
import math

from .erql_contracts import DESIGN_AWARE, GROUP_FILTERED, MULTIFACTOR_PROFILES, VERSIONS

STATISTICS = ("logFC", "logCPM", "F", "p", "df_test", "df_total",
              "df_prior", "df_residual_adj", "s2_post")


def _require(condition, reason):
    if not condition:
        raise ValueError("ERQL result: " + reason)


def _fields(value, names):
    _require(type(value) is dict and set(value) == set(names.split()), "unexpected fields")


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _typed(value):
    _fields(value, "state value")
    state, number = value["state"], value["value"]
    _require((_finite(number) if state == "finite" else number is None
              and state in ("missing", "positive_infinity", "negative_infinity")),
             "invalid typed native value")
    return number


def _expression_filter_mask(rows, sizes, minimum, cutoff):
    """Recompute edgeR's CPM support and total-count rules on the bound panel."""
    import numpy as np

    counts = np.asarray(rows, dtype=np.int64)
    cpm = (counts * 1e6) / np.asarray(sizes)[:, None]
    support = np.count_nonzero(cpm >= cutoff, axis=0)
    return ((support >= minimum - 1e-14) & (counts.sum(axis=0) >= 15)).tolist()


def _validate_erql_fit(result, request, rows, keep):
    norm = result["normalization"]
    _fields(norm, "library_sizes factors reference_profile_id")
    retained = [sum(v for v, include in zip(row, keep) if include) for row in rows]
    _require(norm["library_sizes"] == retained and all(value > 0 for value in retained)
             and norm["reference_profile_id"] in request["profile_ids"], "invalid retained libraries")
    factors = norm["factors"]
    _require(type(factors) is list and len(factors) == len(rows)
             and all(_finite(value) and value > 0 for value in factors), "invalid TMM factors")
    fit = result["fit"]
    if request["profile"] == GROUP_FILTERED:
        _fields(fit, "top_proportion dispersion design_rank residual_df")
        rank = len(request["contrast"])
        _require(type(fit["design_rank"]) is int and fit["design_rank"] == rank
                 and type(fit["residual_df"]) is int and fit["residual_df"] == len(rows) - rank > 0,
                 "fitted rank or residual degrees of freedom contradict the signed design")
    else:
        _fields(fit, "top_proportion dispersion")
    _require(_finite(fit["top_proportion"]) and 0 < fit["top_proportion"] <= 1, "invalid top proportion")
    dispersion = fit["dispersion"]
    _fields(dispersion, "state value")
    states, values = dispersion["state"], dispersion["value"]
    _require(type(states) is list and type(values) is list and len(states) == len(values)
             and len(values) in (1, sum(keep))
             and all(state == "finite" and _finite(value) and value >= 0 for state, value in zip(states, values)),
             "invalid fitted dispersion")


def validate_erql_result(result, request):
    _fields(result, "schema_version profile feature_ids profile_ids groups package_versions filter normalization fit genes")
    group_filtered = request["profile"] == GROUP_FILTERED
    schema = "erql_result/3" if group_filtered else "erql_result/1"
    _require(result["schema_version"] == schema and result["package_versions"] == VERSIONS,
             "wrong schema or runtime versions")
    for name in ("profile", "feature_ids", "profile_ids", "groups"):
        _require(result[name] == request[name], "mismatched " + name)
    features = request["feature_ids"]
    rows = request["counts"]
    filtering = result["filter"]
    _fields(filtering, "keep min_sample_size cpm_cutoff pre_filter_library_sizes")
    keep = filtering["keep"]
    design_aware = request["profile"] == DESIGN_AWARE
    _require(type(keep) is list and len(keep) == len(features) and all(type(v) is bool for v in keep)
             and (request["profile"] in MULTIFACTOR_PROFILES or any(keep)), "invalid or empty filter")
    n_tested = sum(keep)
    from statistics import median
    sizes = [sum(row) for row in rows]
    if design_aware:
        import numpy as np

        orthogonal, _ = np.linalg.qr(np.asarray(request["design"], dtype=float))
        minimum = 1 / float(np.max(np.sum(orthogonal**2, axis=1)))
    else:
        minimum = min(Counter(request["groups"]).values())
    minimum = 10 + (minimum - 10) * 0.7 if minimum > 10 else minimum
    cutoff = 10 / median(sizes) * 1e6
    observed_minimum = filtering["min_sample_size"]
    minimum_matches = (_finite(observed_minimum) and math.isclose(observed_minimum, minimum, rel_tol=1e-12)
                       if design_aware or group_filtered else observed_minimum == minimum)
    _require(filtering["pre_filter_library_sizes"] == sizes and minimum_matches
             and _finite(filtering["cpm_cutoff"])
             and math.isclose(filtering["cpm_cutoff"], cutoff, rel_tol=1e-12),
             "invalid filtering diagnostics")
    if group_filtered:
        _require(keep == _expression_filter_mask(rows, sizes, minimum, cutoff),
                 "mask contradicts the signed target-group filter")
    if n_tested:
        _validate_erql_fit(result, request, rows, keep)
    else:
        _require(result["normalization"] is None and result["fit"] is None,
                 "empty filter cannot carry normalization or fit")
        if design_aware:
            _require(not any(_expression_filter_mask(rows, sizes, observed_minimum, cutoff)),
                     "empty filter omits eligible features")
    genes = result["genes"]
    _require(type(genes) is list and len(genes) == len(features), "incomplete gene family")
    for index, (gene, name, tested) in enumerate(zip(genes, features, keep)):
        _fields(gene, "gene_id status filter_reason support statistics interval interval_reason")
        _require(gene["gene_id"] == name and gene["status"] == ("tested" if tested else "filtered")
                 and gene["interval"] is None and gene["interval_reason"] == (
                     "no_interval_in_profile" if tested else "filtered_before_fit"),
                 "invalid row identity, status, or interval")
        support = {key: len({unit for row, group, unit in zip(rows, request["groups"], request["unit_ids"])
                            if group == request[key] and row[index] > 0})
                   for key in ("numerator", "denominator")}
        _require(gene["support"] == support, "invalid observed unit support")
        if not tested:
            _require(gene["statistics"] is None and gene["filter_reason"] == "filter_by_expr",
                     "filtered rows cannot carry test statistics")
            continue
        stats = gene["statistics"]
        _fields(stats, " ".join(STATISTICS))
        values = {name: _typed(stats[name]) for name in STATISTICS}
        _require(gene["filter_reason"] is None and values["p"] is not None and 0 <= values["p"] <= 1
                 and values["df_test"] == 1 and values["df_total"] is not None and values["df_total"] > 0,
                 "invalid tested row or native test degrees of freedom")
        if group_filtered:
            adjusted = values["df_residual_adj"]
            _require(_finite(adjusted) and adjusted >= 0, "invalid adjusted residual degrees of freedom")
            prior = math.inf if stats["df_prior"]["state"] == "positive_infinity" else values["df_prior"]
            _require(prior is not None and prior >= 0, "invalid prior degrees of freedom")
            expected = min(prior + adjusted, n_tested * result["fit"]["residual_df"])
            _require(math.isclose(values["df_total"], expected, rel_tol=1e-12, abs_tol=1e-12),
                     "total degrees of freedom contradict adjusted residual and prior degrees of freedom")
    return {"tested": n_tested, "filtered": len(keep) - n_tested}
