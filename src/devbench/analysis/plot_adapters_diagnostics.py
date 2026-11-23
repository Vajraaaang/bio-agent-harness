"""Source-linked scatter marginals and descriptive measured-variable correlations.

Coordinates/columns are saved values with an explicit already-applied transform.
Only histogram counts/densities and Pearson coefficients are calculated here;
there is no imputation, model fitting, significance test or replication claim.
"""
from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
import math

from .scientific_plot_contract import (
    finite_number, panel, point_marks, require_fields, require_unique, row,
)


CAPABILITIES = {
    "scatter_marginals": {
        "required": ["x_measurement", "y_measurement", "rows", "x_bin_edges", "y_bin_edges", "histogram_scale"],
        "optional": [],
        "description": "Actual saved joint scatter and independently complete X/Y marginal histograms, with explicit full-range bins, count/density convention and typed observation links.",
    },
    "measurement_correlation": {
        "required": ["mode", "observation_kind", "measurements", "observations", "missingness_policy"],
        "optional": ["saved_matrix"],
        "description": "Descriptive Pearson r among actual named saved measurement columns, retaining exact pair members/denominators and null constant or insufficient pairs; optional saved coefficients are checked against the observations.",
    },
}

_LIMITATIONS = [
    "Descriptive plotting calculations do not establish biological or clinical validity.",
    "Observation IDs and counts do not establish independent biological replication.",
]
_STATUSES = {"recorded", "failed", "missing", "excluded"}


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 2000 or "\x00" in value:
        raise ValueError(label + " must be a bounded nonempty string")
    return value


def _records(value, label, maximum=100_000):
    if not isinstance(value, list) or not 0 < len(value) <= maximum:
        raise ValueError(label + " must be a nonempty bounded list")
    return value


def _measurement(value):
    require_fields(value, ["id", "name", "unit", "declared_transform"])
    for key in value:
        _text(value[key], "measurement " + key)
    return value


def _identity(source):
    _text(source["id"], "observation ID")
    _text(source["key"], "source key")
    if not isinstance(source["group"], str) or len(source["group"]) > 2000:
        raise ValueError("group must be a bounded string")
    if source["status"] not in _STATUSES:
        raise ValueError("explicit recorded, failed, missing or excluded status required")
    if "reason" in source:
        _text(source["reason"], "source reason")
    if "details" in source and not isinstance(source["details"], dict):
        raise ValueError("source details must be an object")


def _source_row(source, kind, x=None, y=None, *, details=None, missing_reason="saved_coordinate_missing"):
    reason = None
    if source["status"] != "recorded":
        x = y = None
        reason = source.get("reason") or "saved_status_" + source["status"]
    elif x is None or y is None:
        reason = source.get("reason") or missing_reason
    return row(source["id"], x, y, entity_kind=kind, key=source["key"], group=source["group"],
               reason=reason, link={"kind": kind, "id": source["id"]},
               details={"source": deepcopy(source), "source_status": source["status"], **deepcopy(details or {})})


def _axis(measurement):
    return {"label": measurement["name"], "unit": measurement["unit"], "transform": measurement["declared_transform"]}


def _new_key(prefix, used):
    key = prefix
    while key in used:
        key += ":derived"
    used.add(key)
    return key


def _edges(value, coordinates):
    edges = [finite_number(v) for v in _records(value, "histogram edges", 1001)]
    if len(edges) < 2 or any(b <= a or not math.isfinite(b - a) for a, b in zip(edges, edges[1:])):
        raise ValueError("histogram edges must be finite and strictly increasing with finite widths")
    if any(v < edges[0] or v > edges[-1] for v in coordinates):
        raise ValueError("histogram bins must cover the full valid marginal range")
    return edges


def _marginal(capability, data, axis):
    measurement = data[axis + "_measurement"]
    sources = data["rows"]
    valid = [s for s in sources if s["status"] == "recorded" and s[axis] is not None]
    edges = _edges(data[axis + "_bin_edges"], [s[axis] for s in valid])
    bins = [[] for _ in edges[:-1]]
    for source in valid:
        bins[min(len(bins) - 1, bisect_right(edges, source[axis]) - 1)].append(source)
    n = len(valid)
    density = data["histogram_scale"] == "density"
    heights = [(len(members) / n / (right - left) if n else None) if density else float(len(members))
               for left, right, members in zip(edges, edges[1:], bins)]
    details = {"measurement": measurement, "histogram_scale": data["histogram_scale"], "bin_edges": edges,
               "marginal_n": n, "bin_convention": "left-inclusive, right-exclusive; final bin right-inclusive"}
    source_bins = {s["key"]: i for i, members in enumerate(bins) for s in members}
    rows = [_source_row(s, s["entity_kind"], s[axis], heights[source_bins[s["key"]]] if s["key"] in source_bins else None,
                        details={**details, "bin_index": source_bins.get(s["key"])}) for s in sources]
    used = {r["key"] for r in rows}
    marks = []
    for i, (left, right, members, height) in enumerate(zip(edges, edges[1:], bins, heights)):
        key = _new_key("marginal:" + axis + ":bin:" + str(i), used)
        member_keys = [s["key"] for s in members]
        rows.append(row(key, left + (right - left) / 2 if height is not None else None, height,
                        entity_kind="bin", key=key, group="All observations", reason="no_valid_marginal_values" if height is None else None,
                        details={**details, "count": len(members), "left_edge": left, "right_edge": right,
                                 "right_edge_inclusive": i == len(bins) - 1, "member_keys": member_keys,
                                 "member_links": [{"kind": s["entity_kind"], "id": s["id"]} for s in members]}))
        if height is not None:
            marks.append({"type": "rect", "keys": [key] + member_keys, "group": "All observations", "x": left,
                          "y": 0., "width": right - left, "height": height, "style": {"fill": "#0072B2", "opacity": .7}})
    yunit = "probability_density_per_" + measurement["unit"] if density else "observations_per_bin"
    return panel(capability, measurement["name"] + " marginal distribution", rows,
                 panel_id=capability + ":" + axis + "-marginal", marks=marks,
                 axes={"x": _axis(measurement), "y": {"label": "Probability density" if density else "Observation count", "unit": yunit, "transform": "identity"}},
                 annotations=[f"{n} valid {axis.upper()} observations contribute, independently of availability of the other coordinate.",
                              "Saved values already reflect the declared transform; no additional transform is applied.",
                              "Bins cover the full valid range: left-inclusive/right-exclusive, with the final right edge included.",
                              "Density = count / (marginal n × bin width); area integrates to 1 when n > 0." if density else "Height is observation count per bin; no density scaling."],
                 limitations=_LIMITATIONS)


def _scatter(capability, data):
    for axis in ("x", "y"):
        _measurement(data[axis + "_measurement"])
    if data["histogram_scale"] not in {"count", "density"}:
        raise ValueError("histogram_scale must explicitly be count or density")
    sources = _records(data["rows"], "scatter rows")
    typed_ids = set()
    for source in sources:
        require_fields(source, ["id", "key", "entity_kind", "x", "y", "group", "status"], ["reason", "details"])
        _identity(source)
        _text(source["entity_kind"], "observation kind")
        identity = (source["entity_kind"], source["id"])
        if identity in typed_ids:
            raise ValueError("scatter typed observation identities must be unique")
        typed_ids.add(identity)
        for axis in ("x", "y"):
            finite_number(source[axis], allow_none=True)
    require_unique([s["key"] for s in sources], "source keys")
    rows = [_source_row(s, s["entity_kind"], s["x"], s["y"],
                        details={"x_measurement": data["x_measurement"], "y_measurement": data["y_measurement"]}) for s in sources]
    joint = panel(capability, data["x_measurement"]["name"] + " versus " + data["y_measurement"]["name"], rows,
                  panel_id=capability + ":joint", axes={"x": _axis(data["x_measurement"]), "y": _axis(data["y_measurement"])},
                  marks=point_marks(rows), annotations=["Joint points require both saved coordinates; companions include every valid individual coordinate.",
                      "Saved values already reflect each declared transform; no additional transform is applied."], limitations=_LIMITATIONS)
    return [joint, _marginal(capability, data, "x"), _marginal(capability, data, "y")]


def _pearson(pairs):
    if len(pairs) < 2:
        return None, "insufficient_pairs"
    # Scaling before centering avoids overflow without changing Pearson r.
    xscale = max(abs(x) for x, _ in pairs) or 1.
    yscale = max(abs(y) for _, y in pairs) or 1.
    xs = [x / xscale for x, _ in pairs]
    ys = [y / yscale for _, y in pairs]
    xmean, ymean = math.fsum(xs) / len(xs), math.fsum(ys) / len(ys)
    dx, dy = [x - xmean for x in xs], [y - ymean for y in ys]
    xx, yy = math.fsum(x * x for x in dx), math.fsum(y * y for y in dy)
    if xx == 0 or yy == 0:
        return None, "constant_measurement"
    value = math.fsum(x * y for x, y in zip(dx, dy)) / math.sqrt(xx) / math.sqrt(yy)
    return max(-1., min(1., value)), None


def _saved_matrix(data, ids):
    saved = data.get("saved_matrix")
    if data["mode"] == "observations":
        if saved is not None:
            raise ValueError("saved_matrix requires validated_saved_matrix mode")
        return None
    if data["mode"] != "validated_saved_matrix":
        raise ValueError("correlation mode must be observations or validated_saved_matrix")
    require_fields(saved, ["variable_ids", "values", "pair_member_ids", "definition", "evidence_id"])
    if saved["variable_ids"] != ids or saved["definition"] != "pearson_r_across_observations":
        raise ValueError("saved correlation must describe the actual measured variables across observations")
    _text(saved["evidence_id"], "saved matrix evidence ID")
    for name in ("values", "pair_member_ids"):
        if not isinstance(saved[name], list) or len(saved[name]) != len(ids) or any(not isinstance(r, list) or len(r) != len(ids) for r in saved[name]):
            raise ValueError("saved correlation matrix dimensions do not match measurement definitions")
    return saved


def _correlation(capability, data):
    kind = _text(data["observation_kind"], "observation kind")
    measurements = [_measurement(m) for m in _records(data["measurements"], "measurements", 32)]
    ids = require_unique([m["id"] for m in measurements], "measurement IDs")
    if data["missingness_policy"] not in {"pairwise_complete", "complete_case"}:
        raise ValueError("declare pairwise_complete or complete_case missingness_policy; no imputation")
    sources = _records(data["observations"], "observations")
    if len(sources) * len(ids) ** 2 > 1_000_000:
        raise ValueError("explicit correlation pair-membership budget exceeded")
    for source in sources:
        require_fields(source, ["id", "key", "values", "group", "status"], ["reason", "details"])
        _identity(source)
        require_fields(source["values"], ids)
        for name in ids:
            finite_number(source["values"][name], allow_none=True)
    require_unique([s["key"] for s in sources], "source keys")
    require_unique([s["id"] for s in sources], "observation IDs")
    saved = _saved_matrix(data, ids)
    rows = [_source_row(s, kind, details={"measurements": measurements},
                        missing_reason="source_observation_retained_in_aggregate") for s in sources]
    used = {r["key"] for r in rows}
    eligible = [s for s in sources if s["status"] == "recorded" and
                (data["missingness_policy"] != "complete_case" or all(s["values"][name] is not None for name in ids))]
    marks = []
    for i, left in enumerate(measurements):
        for j, right in enumerate(measurements):
            members = [s for s in eligible if s["values"][left["id"]] is not None and s["values"][right["id"]] is not None]
            coefficient, reason = _pearson([(s["values"][left["id"]], s["values"][right["id"]]) for s in members])
            detail = {"left_measurement": left, "right_measurement": right, "pearson_r": coefficient,
                      "n_pairs": len(members), "pair_member_ids": [s["id"] for s in members],
                      "pair_member_keys": [s["key"] for s in members],
                      "pair_member_links": [{"kind": kind, "id": s["id"]} for s in members],
                      "missingness_policy": data["missingness_policy"], "observation_kind": kind,
                      "source_observations": len(sources), "excluded_observations": len(sources) - len(members),
                      "definition": "pearson_r_across_observations", "null_reason": reason}
            if saved is not None:
                value = finite_number(saved["values"][i][j], allow_none=True)
                if (value is None) != (coefficient is None) or (value is not None and (not -1 <= value <= 1 or not math.isclose(value, coefficient, rel_tol=1e-12, abs_tol=1e-12))):
                    raise ValueError("saved Pearson coefficient disagrees with actual valid measurement pairs")
                if saved["pair_member_ids"][i][j] != detail["pair_member_ids"]:
                    raise ValueError("saved pair membership disagrees with actual observations and policy")
                detail["saved_entry"] = {"pearson_r": saved["values"][i][j], "pair_member_ids": saved["pair_member_ids"][i][j]}
                detail["saved_matrix_evidence_id"] = saved["evidence_id"]
                detail["saved_matrix_validation"] = "recomputed_from_saved_observations; absolute/relative tolerance 1e-12"
            key = _new_key(f"measurement-pair:{i}:{j}", used)
            rows.append(row(key, float(j) if coefficient is not None else None, float(i) if coefficient is not None else None,
                            entity_kind="measurement_pair", key=key, details=detail, reason=reason))
            marks.append({"type": "rect", "keys": [key] + detail["pair_member_keys"], "group": "Pearson r", "x": j - .48,
                          "y": i - .48, "width": .96, "height": .96, "value": coefficient,
                          "style": {"fill": "#eeeeee"} if coefficient is None else {}})
            marks.append({"type": "text", "keys": [key], "group": "Pearson r", "x": float(j), "y": float(i),
                          "text": "NA" if coefficient is None else f"{coefficient:.2f}", "anchor": "middle"})
    names = [m["name"] for m in measurements]
    axes = {axis: {"label": "Measured variable", "unit": "measurement", "transform": "identity"} for axis in ("x", "y")}
    return [panel(capability, "Measured-variable Pearson correlation", rows, marks=marks, axes=axes,
                  x_categories=names, y_categories=names,
                  color_scale={"label": "Pearson r", "unit": "correlation", "palette": "RdBu_r", "domain": [-1., 1.], "center": 0},
                  annotations=[f"{len(sources)} saved {kind} observations; missingness policy: {data['missingness_policy']}.",
                               "Exact valid pair members and denominators are retained for every coefficient, including the diagonal.",
                               "Constant or fewer-than-two-pair results remain null (NA); columns already reflect their declared transforms.",
                               "Saved coefficients validated against actual observations." if saved else "Pearson coefficients calculated descriptively from the saved measurement columns."],
                  limitations=_LIMITATIONS + ["Descriptive association only: no p-values, causal claim or claim of independent biological replication."])]


def build_panels(capability, data):
    """Strict saved-input adapter; public synthetic examples are in fixture_packets()."""
    if capability not in CAPABILITIES:
        raise ValueError("unknown diagnostic plotting capability")
    spec = CAPABILITIES[capability]
    require_fields(data, spec["required"], spec["optional"])
    return {"scatter_marginals": _scatter, "measurement_correlation": _correlation}[capability](capability, deepcopy(data))
