"""Bounded, typed figure evidence and rendering of frozen scatter payloads.

This module reads saved tables, never an expression matrix or a model. Other
payload kinds are validation contracts only; they do not enable new workflows.
"""
from __future__ import annotations

from copy import deepcopy
from importlib import metadata
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import stat
import sys

from .checkpoints import atomic_json, digest, sha256


MAX_PAYLOAD_BYTES = 16 * 1024**2
MAX_TABLE_BYTES = 32 * 1024**2
_HASH = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[A-Za-z0-9_-]{1,100}")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
_KEYS = {"observation_points": ["observation_id"], "sample_points": ["sample_id"],
         "gene_results": ["gene_id"], "group_gene_aggregates": ["group_id", "gene_id"]}
_UNITS = {"observation_points": "cell", "sample_points": "biological_sample",
          "gene_results": "gene", "group_gene_aggregates": "aggregate"}
_LEGENDS = {"best", "upper right", "upper left", "lower right", "lower left", "none"}
_STYLE = {"width", "height", "point_size", "opacity", "font_size", "legend_position",
          "category_colors", "label_positions"}
_SCIENTIFIC = {"layer", "population", "filter", "grouping", "gene", "genes", "caption",
               "title", "labels", "bandwidth", "smoothing", "bins", "aggregation", "transform",
               "contrast", "annotations", "parameters"}


def _path(path):
    path = Path(path)
    if not path.is_absolute() or path != path.resolve() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("figure_path_must_be_absolute_canonical_without_symlinks")
    return path


def _read(path, maximum):
    path = _path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("figure_requires_regular_file")
        if info.st_size > maximum:
            raise ValueError("figure_payload_exceeds_render_budget")
        value = handle.read(maximum + 1)
    if len(value) > maximum:
        raise ValueError("figure_payload_exceeds_render_budget")
    return value


def _json(path, maximum=2 * 1024**2):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("figure_duplicate_json_key")
            result[key] = value
        return result
    def nonfinite(value):
        raise ValueError("figure_nonfinite_json")
    return json.loads(_read(path, maximum), object_pairs_hook=unique, parse_constant=nonfinite)


def _hash(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _text(value):
    return isinstance(value, str) and bool(value) and len(value) <= 2000


def _identity(row, keys):
    values = [row.get(key) for key in keys]
    if not all(_text(value) for value in values):
        raise ValueError("figure_missing_typed_identity")
    return values[0] if len(values) == 1 else values


def validate_figure_artifact(record, payload):
    """Validate identities and scientific/display semantics without rendering."""
    try:
        json.dumps((record, payload), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("figure_nonfinite_or_non_JSON_value") from error
    if not isinstance(record, dict) or record.get("format") != "devbench.figure/1":
        raise ValueError("unknown_figure_format")
    if not isinstance(record.get("figure_id"), str) or not _ID.fullmatch(record["figure_id"]):
        raise ValueError("invalid_figure_id")
    if not isinstance(record.get("family"), str) or not re.fullmatch(r"G(?:0[1-9]|1[0-9]|2[0-2])", record["family"]):
        raise ValueError("invalid_figure_family")
    semantics = record.get("semantics", {})
    needed = {"unit", "representation_or_layer", "population_sha256", "filter_sha256", "grouping",
              "aggregation", "axis_meanings", "limitations", "scale_policy", "display_sampling"}
    if not isinstance(semantics, dict) or not needed <= semantics.keys():
        raise ValueError("figure_semantics_required")
    if (not _text(semantics["representation_or_layer"]) or not _hash(semantics["population_sha256"])
            or not _hash(semantics["filter_sha256"]) or not isinstance(semantics["limitations"], list)
            or not all(_text(value) for value in semantics["limitations"])):
        raise ValueError("invalid_figure_semantics")
    if semantics["display_sampling"] != {"enabled": False, "sampled_ids": None, "seed": None}:
        raise ValueError("display_sampling_not_implemented_M2a")
    if semantics["scale_policy"] != "saved_full_range_no_clipping":
        raise ValueError("display_scale_clipping_not_implemented_M2a")
    binding = record.get("analysis_binding", {})
    if (not isinstance(binding, dict)
            or not all(_hash(binding.get(key)) for key in ("input_sha256", "parameters_sha256", "analysis_source_sha256"))
            or not isinstance(binding.get("generating_stage_receipts"), dict)
            or not binding["generating_stage_receipts"]
            or not all(isinstance(key, str) and _ID.fullmatch(key) and _hash(value)
                       for key, value in binding["generating_stage_receipts"].items())):
        raise ValueError("figure_analysis_binding_required")
    identity = record.get("renderer_identity", {})
    if not isinstance(identity, dict) or not all(_hash(identity.get(key)) for key in ("source_sha256", "environment_sha256")):
        raise ValueError("figure_renderer_identity_required")
    if not isinstance(payload, dict) or payload.get("format") != "devbench.figure-data/1":
        raise ValueError("unknown_figure_payload_format")
    kind = payload.get("payload_kind")
    if kind not in _KEYS or payload.get("key_columns") != _KEYS[kind] or semantics["unit"] != _UNITS[kind]:
        raise ValueError("figure_payload_kind_unit_or_keys_mismatch")
    for key, suffix in (("plot_data", "-data.json"), ("image", ".png")):
        item = record.get(key, {})
        if (not isinstance(item, dict) or item.get("path") != f"figures/{record['figure_id']}{suffix}"
                or not _hash(item.get("sha256"))):
            raise ValueError("figure_artifact_path_or_hash_invalid")
    if (record["plot_data"].get("payload_kind") != kind
            or record["plot_data"].get("key_columns") != payload["key_columns"]):
        raise ValueError("figure_payload_descriptor_mismatch")
    population = payload.get("population_ids")
    if not isinstance(population, list) or len({json.dumps(value) for value in population}) != len(population):
        raise ValueError("figure_population_ids_required_or_duplicate")
    try:
        population_hash = digest(sorted(population))
    except (TypeError, ValueError) as error:
        raise ValueError("figure_population_ids_invalid") from error
    if population_hash != semantics["population_sha256"] or record.get("analysis_n") != len(population):
        raise ValueError("figure_population_binding_mismatch")
    panels = payload.get("panels")
    if not isinstance(panels, list) or not 1 <= len(panels) <= 8:
        raise ValueError("figure_panels_required")
    panel_ids, label_ids, rendered = set(), set(), set()
    for panel in panels:
        if not isinstance(panel, dict) or not _text(panel.get("panel_id")) or panel["panel_id"] in panel_ids:
            raise ValueError("figure_panel_identity_invalid")
        panel_ids.add(panel["panel_id"])
        if not _text(panel.get("title")) or not isinstance(panel.get("rows"), list):
            raise ValueError("figure_panel_content_required")
        for axis in ("x_axis", "y_axis"):
            if not isinstance(panel.get(axis), dict) or not all(_text(panel[axis].get(key)) for key in ("field", "label", "unit", "transform")):
                raise ValueError("figure_axis_semantics_required")
        color = panel.get("color", {})
        if not isinstance(color, dict) or not all(_text(color.get(key)) for key in ("field", "label", "kind")):
            raise ValueError("figure_color_semantics_required")
        if color["kind"] == "categorical":
            if (not _text(color.get("grouping")) or not isinstance(color.get("colors"), dict)
                    or not all(_text(key) and isinstance(value, str) and _COLOR.fullmatch(value)
                               for key, value in color["colors"].items())):
                raise ValueError("figure_category_color_map_required")
        elif color["kind"] == "continuous":
            scale = color.get("scale", {})
            if (not isinstance(scale, dict) or scale.get("kind") != "linear" or scale.get("clipped_n") != 0
                    or not all(_finite(scale.get(key)) for key in ("min", "max")) or scale["min"] > scale["max"]):
                raise ValueError("figure_continuous_scale_required")
        else:
            raise ValueError("unsupported_figure_color_kind")
        rows = []
        for row in panel["rows"]:
            if not isinstance(row, dict):
                raise ValueError("figure_row_invalid")
            item_id = _identity(row, payload["key_columns"])
            rows.append(item_id)
            if not isinstance(row.get("metadata"), dict):
                raise ValueError("figure_row_metadata_required")
            if kind == "group_gene_aggregates":
                contributors = row.get("contributor_ids")
                if (not isinstance(contributors, list) or not all(_text(value) for value in contributors)
                        or len(contributors) != len(set(contributors))):
                    raise ValueError("aggregate_contributor_membership_required")
            for coordinate in ("x", "y"):
                if row.get(coordinate) is not None and not _finite(row[coordinate]):
                    raise ValueError("figure_nonfinite_coordinate")
            if color["kind"] == "continuous":
                if row.get("color") is not None and not _finite(row["color"]):
                    raise ValueError("figure_nonfinite_color")
            elif row.get("color") is not None and row["color"] not in color["colors"]:
                raise ValueError("figure_unknown_category")
            if row.get("status") == "rendered":
                if any(row.get(key) is None for key in ("x", "y", "color")) or row.get("exclusion_reason") is not None:
                    raise ValueError("figure_rendered_value_missing")
                rendered.add(json.dumps(item_id))
            elif row.get("status") != "excluded" or not _text(row.get("exclusion_reason")):
                raise ValueError("figure_exclusion_status_required")
        if sorted(rows) != sorted(population):
            raise ValueError("figure_panel_population_identity_mismatch")
        if color["kind"] == "continuous":
            finite_colors = [row["color"] for row in panel["rows"] if row["color"] is not None]
            if finite_colors and (color["scale"]["min"] != min(finite_colors) or color["scale"]["max"] != max(finite_colors)):
                raise ValueError("figure_continuous_scale_does_not_cover_exact_saved_range")
        labels = panel.get("labels")
        if not isinstance(labels, list):
            raise ValueError("figure_immutable_labels_required")
        for label in labels:
            if (not isinstance(label, dict) or not _text(label.get("label_id")) or label["label_id"] in label_ids
                    or label.get("kind") not in {"title", "x_axis", "y_axis"} or not _text(label.get("text"))):
                raise ValueError("figure_label_identity_invalid")
            text = panel["title"] if label["kind"] == "title" else panel[label["kind"]]["label"]
            if label["text"] != text:
                raise ValueError("figure_label_text_mismatch")
            label_ids.add(label["label_id"])
    if record.get("rendered_n") != len(rendered):
        raise ValueError("figure_rendered_count_mismatch")
    if semantics["axis_meanings"] != [{"x": panel["x_axis"], "y": panel["y_axis"]} for panel in panels]:
        raise ValueError("figure_axis_semantics_binding_mismatch")
    return record


def validate_display_options(options, record, payload):
    """Return complete bounded display options; reject scientific changes."""
    if not isinstance(options, dict):
        raise ValueError("display_options_must_be_an_object")
    extra = set(options) - _STYLE
    if extra & _SCIENTIFIC:
        raise ValueError("scientific_or_calculation_revision_required:" + ",".join(sorted(extra & _SCIENTIFIC)))
    if extra & {"sampling", "clipping", "scales", "label_repulsion"}:
        raise ValueError("presentation_option_not_implemented_M2a")
    if extra:
        raise ValueError("unknown_display_option:" + ",".join(sorted(extra)))
    result = {"width": 770 if len(payload["panels"]) == 1 else 1320, "height": 550,
              "point_size": 4, "opacity": .65 if record["family"] == "G02" else .7,
              "font_size": 9, "legend_position": "best", "category_colors": {}, "label_positions": {}}
    base = record.get("display_options", {})
    if not isinstance(base, dict) or set(base) - _STYLE:
        raise ValueError("invalid_recorded_display_options")
    result.update(deepcopy(base))
    result.update(deepcopy(options))
    for key, low, high, integer in (("width", 320, 2400, True), ("height", 320, 2400, True),
                                   ("point_size", 1, 24, False), ("opacity", .1, 1, False),
                                   ("font_size", 8, 28, False)):
        value = result[key]
        if not _finite(value) or (integer and type(value) is not int) or not low <= value <= high:
            raise ValueError("display_option_out_of_bounds:" + key)
    if not isinstance(result["legend_position"], str) or result["legend_position"] not in _LEGENDS:
        raise ValueError("display_option_legend_position_invalid")
    categories = {}
    for panel in payload["panels"]:
        color = panel["color"]
        if color["kind"] == "categorical":
            categories.setdefault(color["grouping"], {}).update(color["colors"])
    replacements = result["category_colors"]
    if not isinstance(replacements, dict):
        raise ValueError("category_colors_must_be_an_object")
    for grouping, mapping in replacements.items():
        if grouping not in categories or not isinstance(mapping, dict) or set(mapping) - categories[grouping].keys():
            raise ValueError("unknown_figure_category")
        if not all(isinstance(value, str) and _COLOR.fullmatch(value) for value in mapping.values()):
            raise ValueError("invalid_category_color")
        categories[grouping].update({key: value.lower() for key, value in mapping.items()})
    result["category_colors"] = categories
    labels = {label["label_id"] for panel in payload["panels"] for label in panel["labels"]}
    positions = result["label_positions"]
    if not isinstance(positions, dict) or set(positions) - labels:
        raise ValueError("unknown_figure_label")
    for position in positions.values():
        if (not isinstance(position, dict) or set(position) != {"x", "y"}
                or not all(_finite(value) and -1 <= value <= 2 for value in position.values())):
            raise ValueError("invalid_label_position")
    return result


def renderer_identity():
    import matplotlib
    from matplotlib import ft2font
    from matplotlib.backends import _backend_agg
    packages = {}
    for name in ("matplotlib", "numpy", "pillow"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    environment = {"python": list(sys.version_info[:3]), "platform": platform.platform(),
                   "packages": packages, "backend": "Agg", "font": "DejaVu Sans", "dpi": 110,
                   "renderer_runtime_sha256": {"agg": sha256(Path(_backend_agg.__file__).resolve()),
                       "freetype": sha256(Path(ft2font.__file__).resolve()),
                       "font": sha256(Path(matplotlib.get_data_path()).resolve() / "fonts/ttf/DejaVuSans.ttf")}}
    return {"source_sha256": sha256(Path(__file__).resolve()), "environment_sha256": digest(environment)}


def render_figure_artifact(record, payload, output, display_options=None):
    """Render only frozen G02/G08 data; return the normalized display options."""
    validate_figure_artifact(record, payload)
    if payload["payload_kind"] != "observation_points" or record["family"] not in {"G02", "G08"}:
        raise ValueError("figure_renderer_unavailable_for_payload_kind")
    options = validate_display_options(display_options or {}, record, payload)
    output = _path(output)
    if output.suffix != ".png" or not output.parent.is_dir():
        raise ValueError("figure_output_requires_absolute_PNG_in_existing_directory")
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    with plt.rc_context({"font.family": "DejaVu Sans", "font.size": options["font_size"],
                         "text.usetex": False, "text.parse_math": False,
                         "figure.dpi": 110, "savefig.dpi": 110}):
        fig, axes = plt.subplots(1, len(payload["panels"]), squeeze=False,
                                 figsize=(options["width"] / 110, options["height"] / 110),
                                 constrained_layout=True)
        try:
            for ax, panel in zip(axes[0], payload["panels"]):
                rows = [row for row in panel["rows"] if row["status"] == "rendered"]
                color = panel["color"]
                common = {"s": options["point_size"], "alpha": options["opacity"], "linewidths": 0}
                if color["kind"] == "continuous":
                    points = ax.scatter([row["x"] for row in rows], [row["y"] for row in rows],
                                        c=[row["color"] for row in rows], cmap="viridis",
                                        vmin=color["scale"]["min"], vmax=color["scale"]["max"], **common)
                    fig.colorbar(points, ax=ax, label=color["label"])
                else:
                    for category, value in options["category_colors"][color["grouping"]].items():
                        group = [row for row in rows if row["color"] == category]
                        if group:
                            ax.scatter([row["x"] for row in group], [row["y"] for row in group],
                                       color=value, label=category, **common)
                    if options["legend_position"] != "none" and rows:
                        ax.legend(fontsize=max(6, options["font_size"] - 3), markerscale=2,
                                  loc=options["legend_position"], frameon=False)
                ax.set(xlabel=panel["x_axis"]["label"], ylabel=panel["y_axis"]["label"], title=panel["title"])
                for label in panel["labels"]:
                    position = options["label_positions"].get(label["label_id"])
                    if position:
                        if label["kind"] == "title":
                            ax.title.set_position((position["x"], position["y"]))
                        else:
                            axis = ax.xaxis if label["kind"] == "x_axis" else ax.yaxis
                            axis.set_label_coords(position["x"], position["y"])
            fig.savefig(output, metadata={"Software": "DevBench frozen figure renderer"})
        finally:
            plt.close(fig)
    return options


def load_figure(bundle, figure_id):
    root = _path(bundle)
    if not isinstance(figure_id, str) or not _ID.fullmatch(figure_id):
        raise ValueError("invalid_figure_id")
    if not (root / "figures/manifest.json").exists():
        raise ValueError("presentation_revision_unavailable:figure_manifest_missing")
    manifest = _json(root / "figures/manifest.json")
    if not isinstance(manifest, dict) or manifest.get("format") != "devbench.figures/1" or not isinstance(manifest.get("figures"), list):
        raise ValueError("presentation_revision_unavailable:invalid_figure_manifest")
    records = manifest["figures"]
    ids = [row.get("figure_id") for row in records if isinstance(row, dict)]
    if len(ids) != len(records) or any(not isinstance(value, str) for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("invalid_figure_manifest_identities")
    matches = [row for row in records if row["figure_id"] == figure_id]
    if not matches:
        raise ValueError("presentation_revision_unavailable:figure_not_found")
    record = matches[0]
    path = root / f"figures/{figure_id}-data.json"
    payload = _json(path, MAX_PAYLOAD_BYTES)
    validate_figure_artifact(record, payload)
    if sha256(path) != record["plot_data"]["sha256"]:
        raise ValueError("figure_plot_data_hash_mismatch")
    image = root / record["image"]["path"]
    if not _read(image, MAX_PAYLOAD_BYTES).startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("figure_image_must_be_PNG")
    if sha256(image) != record["image"]["sha256"]:
        raise ValueError("figure_image_hash_mismatch")
    validate_display_options({}, record, payload)
    return record, payload


def _table(store, stage, filename):
    import pandas as pd
    folder = _path(store.path(stage))
    receipt = _json(folder / "receipt.json")
    data = _read(folder / filename, MAX_TABLE_BYTES)
    import hashlib
    if (receipt.get("stage") != stage or receipt.get("identity_sha256") != store.identity
            or receipt.get("artifacts", {}).get(filename) != hashlib.sha256(data).hexdigest()):
        raise ValueError("figure_generating_table_receipt_mismatch")
    frame = pd.read_csv(io.BytesIO(data), dtype={0: str}, float_precision="round_trip",
                        keep_default_na=False, na_values=[""])
    index = frame.pop(frame.columns[0])
    frame.index = pd.Index(index.astype(str).to_numpy(), name=None)
    if frame.index.has_duplicates or any(not _text(value) for value in frame.index):
        raise ValueError("figure_duplicate_or_missing_table_identity")
    return frame


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _panel(panel_id, table, *, x, y, title, xlabel, ylabel, unit, transform, color, metadata_fields=()):
    import pandas as pd
    rows = []
    for observation_id, source in table.iterrows():
        categorical = color["kind"] == "categorical"
        raw = source[color["field"]]
        value = (None if pd.isna(raw) else str(raw)) if categorical else _number(raw)
        metadata_values = {}
        for field in metadata_fields:
            if field not in source:
                continue
            item = source[field]
            item = item.item() if hasattr(item, "item") else item
            metadata_values[field] = (None if pd.isna(item) or (type(item) is float and not math.isfinite(item))
                                      else item if type(item) in (str, int, float, bool) else str(item))
        row = {"observation_id": str(observation_id), "x": _number(source[x]), "y": _number(source[y]),
               "color": value, "metadata": metadata_values}
        missing = [key for key in ("x", "y", "color") if row[key] is None]
        row.update(status="excluded" if missing else "rendered",
                   exclusion_reason="missing_or_nonfinite:" + ",".join(missing) if missing else None)
        rows.append(row)
    return {"panel_id": panel_id, "title": title, "rows": rows, "color": color,
            "x_axis": {"field": x, "label": xlabel, "unit": unit, "transform": transform},
            "y_axis": {"field": y, "label": ylabel, "unit": unit, "transform": transform},
            "labels": [{"label_id": panel_id + ":" + kind, "kind": kind, "text": text}
                       for kind, text in (("title", title), ("x_axis", xlabel), ("y_axis", ylabel))]}


def export_figures(store, conditions, output, *, batch_key):
    """Capture exact saved QC/UMAP evidence and render the existing filenames."""
    output = _path(output)
    output.mkdir(exist_ok=True)
    root = _path(store.root.parent)
    parameters = _json(root / "parameters.json")
    identity = _json(root / "checkpoint-identity.json")
    if (digest(identity) != store.identity or identity.get("parameters") != parameters or identity.get("data_sha256") != parameters.get("data_sha256")
            or identity.get("source_sha256") != parameters.get("source_sha256")):
        raise ValueError("figure_analysis_identity_mismatch")
    metrics = _table(store, "qc", "metrics.csv")
    if "qc_pass" not in metrics or not all(type(value) is bool for value in metrics["qc_pass"].tolist()):
        raise ValueError("figure_QC_population_requires_boolean_selection")
    kept = metrics.loc[metrics["qc_pass"]]
    population = list(kept.index)
    tables = {name: _table(store, name + "_graph", "embedding.csv") for name in conditions}
    for table in tables.values():
        if sorted(table.index) != sorted(population):
            raise ValueError("figure_embedding_population_mismatch")
    from matplotlib import colormaps
    from matplotlib.colors import to_hex
    palette = colormaps["tab20"]
    category_maps = {}
    for grouping, column in (("observed_batch", batch_key), ("rerun_leiden", "leiden")):
        levels = sorted({str(value) for table in tables.values() for value in table[column].dropna()})
        category_maps[grouping] = {value: to_hex(palette(index % 20)) for index, value in enumerate(levels)}
    mito = [value for item in kept.pct_counts_mt for value in [_number(item)] if value is not None]
    figures = {"qc": ("G02", [_panel("qc", kept, x="log1p_total_counts", y="log1p_n_genes_by_counts",
        title="QC: retained cells", xlabel="log(1 + total counts)", ylabel="log(1 + detected genes)",
        unit="log1p count", transform="natural_log1p", color={"kind": "continuous", "field": "pct_counts_mt",
        "label": "Mitochondrial counts (%)", "unit": "percent", "scale": {"kind": "linear",
        "min": min(mito, default=0), "max": max(mito, default=0), "clipped_n": 0}},
        metadata_fields=(batch_key, "qc_pass", "total_counts", "n_genes_by_counts", "pct_counts_mt"))])}
    for condition, table in tables.items():
        panels = []
        for grouping, column, label in (("observed_batch", batch_key, "Observed batch"),
                                        ("rerun_leiden", "leiden", "Rerun Leiden cluster")):
            panels.append(_panel(condition + "_" + grouping, table, x="UMAP_1", y="UMAP_2",
                title=condition + ": " + label, xlabel="UMAP 1", ylabel="UMAP 2", unit="arbitrary units",
                transform="none", color={"kind": "categorical", "field": column, "label": label,
                    "grouping": grouping, "colors": category_maps[grouping]}, metadata_fields=(batch_key, "leiden")))
        figures[condition] = ("G08", panels)
    records = []
    renderer = renderer_identity()
    qc_hash = sha256(store.path("qc") / "receipt.json")
    for figure_id, (family, panels) in figures.items():
        ids = list(kept.index) if figure_id == "qc" else list(tables[figure_id].index)
        payload = {"format": "devbench.figure-data/1", "payload_kind": "observation_points",
                   "key_columns": ["observation_id"], "population_ids": ids, "panels": panels}
        if len(json.dumps(payload, allow_nan=False).encode()) > MAX_PAYLOAD_BYTES:
            raise ValueError("figure_payload_exceeds_render_budget")
        receipts = {"qc": qc_hash}
        if figure_id != "qc":
            receipts[figure_id + "_graph"] = sha256(store.path(figure_id + "_graph") / "receipt.json")
        record = {"format": "devbench.figure/1", "figure_id": figure_id, "family": family,
            "figure_kind": "qc_scatter" if family == "G02" else "umap_scatter", "original_run_id": root.name,
            "analysis_binding": {"input_sha256": parameters["data_sha256"],
                "parameters_sha256": sha256(root / "parameters.json"), "analysis_source_sha256": identity["source_sha256"],
                "generating_stage_receipts": receipts},
            "semantics": {"unit": "cell", "representation_or_layer":
                "count_derived_QC:" + str(parameters.get("counts_layer") or "X") if family == "G02"
                else "UMAP_from:" + str(conditions[figure_id]),
                "population_sha256": digest(sorted(ids)), "filter_sha256": digest({"qc_receipt_sha256": qc_hash,
                    "policy": "qc_pass", "retained_ids": sorted(population)}),
                "grouping": [panel["color"].get("grouping") for panel in panels], "aggregation": None,
                "axis_meanings": [{"x": panel["x_axis"], "y": panel["y_axis"]} for panel in panels],
                "limitations": ["Descriptive visualization; cells are not independent biological replicates.",
                    "UMAP coordinates are a latent view, not expression or physical distances.",
                    "Each panel uses its full saved values and independent automatic axis limits; no display sampling or clipping.",
                    "Rerun cluster numbers do not establish matched cell types across conditions.",
                    "Stored predictions support concordance, not accuracy; confidence calibration is unknown."],
                "scale_policy": "saved_full_range_no_clipping", "display_sampling": {
                    "enabled": False, "sampled_ids": None, "seed": None}},
            "analysis_n": len(ids), "rendered_n": len({row["observation_id"] for panel in panels
                for row in panel["rows"] if row["status"] == "rendered"}),
            "contributing_observation_n": len(ids), "independent_unit_n_if_known": None,
            "plot_data": {"path": f"figures/{figure_id}-data.json", "sha256": "0" * 64,
                "payload_kind": "observation_points", "key_columns": ["observation_id"],
                "membership_references": "population_ids"},
            "image": {"path": f"figures/{figure_id}.png", "sha256": "0" * 64},
            "renderer_identity": renderer, "display_options": {}}
        atomic_json(output / (figure_id + "-data.json"), payload)
        if (output / (figure_id + "-data.json")).stat().st_size > MAX_PAYLOAD_BYTES:
            raise ValueError("figure_payload_exceeds_render_budget")
        record["plot_data"]["sha256"] = sha256(output / (figure_id + "-data.json"))
        record["display_options"] = render_figure_artifact(record, payload, output / (figure_id + ".png"))
        record["image"]["sha256"] = sha256(output / (figure_id + ".png"))
        records.append(record)
    manifest = {"format": "devbench.figures/1", "figures": records}
    atomic_json(output / "manifest.json", manifest)
    return manifest
