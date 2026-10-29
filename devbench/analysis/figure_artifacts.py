"""Bounded, typed figure evidence and rendering of frozen numeric payloads.

This module reads saved tables, never an expression matrix or a model. Other
Version 1 preserves the observation renderer; version 2 adds saved numerical
projections. Neither path chooses or executes an analysis workflow.
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


def validate_figure_artifact(record, payload, *, artifact_service=None):
    """Validate identities and scientific/display semantics without rendering."""
    if isinstance(record, dict) and record.get("format") == "devbench.figure/2":
        return _validate_publication_figure(record, payload, artifact_service=artifact_service)
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


def render_figure_artifact(record, payload, output, display_options=None, *, artifact_service=None):
    """Render frozen observation data or explicit bounded /2 projections."""
    if isinstance(record, dict) and record.get("format") == "devbench.figure/2":
        return _render_publication_figure(record, payload, output, display_options, artifact_service=artifact_service)
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


# The /2 path consumes bounded saved numeric projections. It neither loads an
# expression matrix nor chooses an analysis/profile or a scientific threshold.
_BULK_KINDS = {"sample_scatter": "sample", "gene_scatter": "gene",
               "effect_scatter": "gene", "measured_curve": "gene_time"}
_TRANSFORMS = {"identity", "natural_log1p", "negative_log10"}
_PLOT_SCOPE = "saved_numeric_geometry_not_pixel_truth_or_scientific_qualification"
_PLOT_ALIASES = {"temporal_curve": "measured_curve"}


def resolve_publication_plot_kind(requested_kind):
    """Resolve registered presentation aliases; do not advertise an input adapter."""
    if type(requested_kind) is not str:
        raise ValueError("unsupported_publication_plot_kind")
    kind = _PLOT_ALIASES.get(requested_kind, requested_kind)
    if kind not in _BULK_KINDS:
        raise ValueError("unsupported_publication_plot_kind")
    return kind


def _closed(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError("figure_requires_closed_declared_fields")


def _source_ref(value):
    import hashlib
    _closed(value, {"path", "sha256", "size_bytes"})
    if not _hash(value["sha256"]) or type(value["size_bytes"]) is not int or not 0 <= value["size_bytes"] <= MAX_PAYLOAD_BYTES:
        raise ValueError("figure_invalid_source_reference")
    body = _read(value["path"], MAX_PAYLOAD_BYTES)
    if len(body) != value["size_bytes"] or hashlib.sha256(body).hexdigest() != value["sha256"]:
        raise ValueError("figure_numeric_source_hash_or_size_mismatch")
    return body


def _manifest_ref(value):
    from .episode_proposals import _bind_manifest_snapshot
    from devharness_core.provenance import verify_execution
    _closed(value, {"manifest_path", "root_hash"} | ({"record_ref"} if "record_ref" in value else set()))
    if not _hash(value["root_hash"]):
        raise ValueError("figure_invalid_linked_receipt_root")
    path = _path(value["manifest_path"])
    manifest = _json(path)
    _bind_manifest_snapshot(manifest, value["root_hash"])
    if not verify_execution(path, value["root_hash"])["valid"]:
        raise ValueError("figure_linked_receipt_failed_verification")
    if "record_ref" in value:
        _source_ref(value["record_ref"])
        if not any(item.get("sha256") == value["record_ref"]["sha256"] for item in manifest["artifacts"]):
            raise ValueError("figure_linked_record_absent_from_receipt")
    return path, manifest


def _plot_value(value):
    _closed(value, {"status", "value", "censoring"})
    if value["status"] == "exact":
        if not _finite(value["value"]) or value["censoring"] is not None:
            raise ValueError("figure_exact_value_requires_finite_uncensored_number")
    elif value["status"] == "censored":
        _closed(value["censoring"], {"relation", "bound"})
        if (value["value"] is not None or value["censoring"]["relation"] not in {"lt", "le", "gt", "ge"}
                or not _finite(value["censoring"]["bound"])):
            raise ValueError("figure_censored_value_cannot_be_replaced_by_zero")
    elif value["status"] != "missing" or value["value"] is not None or value["censoring"] is not None:
        raise ValueError("figure_missing_value_cannot_carry_estimate")


def _transformed(value, transform):
    if transform == "identity":
        return value
    if transform == "natural_log1p" and value > -1:
        return math.log1p(value)
    if transform == "negative_log10" and value > 0:
        return -math.log10(value)
    raise ValueError("figure_transform_domain_invalid")


def validate_publication_plot_payload(payload):
    """Validate exact retained numeric membership, scales and uncertainty."""
    try:
        encoded = json.dumps(payload, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("figure_nonfinite_or_non_JSON_value") from error
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise ValueError("figure_payload_exceeds_render_budget")
    _closed(payload, {"format", "source_ref", "data"} | ({"projection_ref"} if "projection_ref" in payload else set()))
    if payload["format"] != "devbench.figure-data/2":
        raise ValueError("unknown_publication_figure_payload_format")
    body = _source_ref(payload["source_ref"])
    from devbench.study.evidence import decode_json
    source = decode_json(body)
    data = payload["data"]
    if data != source:
        raise ValueError("figure_numeric_projection_differs_from_retained_source")
    _closed(data, {"format", "plot_kind", "entity_kind", "axes", "membership", "rows",
                   "uncertainty_policy", "limitations"})
    if (data["format"] != "devbench.numeric-plot-source/2" or data["plot_kind"] not in _BULK_KINDS
            or data["entity_kind"] != _BULK_KINDS[data["plot_kind"]]):
        raise ValueError("unsupported_publication_figure_kind_or_entity")
    _closed(data["axes"], {"x", "y"})
    for axis in data["axes"].values():
        _closed(axis, {"field", "label", "unit", "transform", "direction", "range_policy"})
        if (not all(_text(axis[key]) for key in ("field", "label", "unit"))
                or axis["transform"] not in _TRANSFORMS or axis["direction"] != "increasing"
                or axis["range_policy"] != "saved_full_range_no_clipping"):
            raise ValueError("figure_axis_transform_direction_or_clipping_invalid")
    if data["uncertainty_policy"] not in {"report_absence", "require_interval"}:
        raise ValueError("figure_uncertainty_policy_required")
    if (type(data["limitations"]) is not list or len(data["limitations"]) > 64
            or not all(_text(item) for item in data["limitations"])):
        raise ValueError("figure_bounded_limitations_required")
    rows, membership = data["rows"], data["membership"]
    if type(rows) is not list or not 1 <= len(rows) <= 5000 or type(membership) is not list:
        raise ValueError("figure_bounded_rows_and_membership_required")
    observed, chronological = [], {}
    for row in rows:
        _closed(row, {"entity_id", "coordinate_id", "group_id", "x", "y", "interval"})
        if not _text(row["entity_id"]) or not _text(row["group_id"]):
            raise ValueError("figure_entity_and_group_identity_required")
        curve = data["entity_kind"] == "gene_time"
        if (curve and not _text(row["coordinate_id"])) or (not curve and row["coordinate_id"] is not None):
            raise ValueError("figure_coordinate_identity_mismatch")
        observed.append([row["entity_id"], row["coordinate_id"], row["group_id"]] if curve else row["entity_id"])
        for coordinate in ("x", "y"):
            _plot_value(row[coordinate])
            if row[coordinate]["status"] == "exact":
                _transformed(row[coordinate]["value"], data["axes"][coordinate]["transform"])
        interval = row["interval"]
        if interval is not None:
            _closed(interval, {"lower", "upper", "level", "kind"})
            if (not all(_finite(interval[key]) for key in ("lower", "upper", "level"))
                    or not interval["lower"] <= interval["upper"] or not 0 < interval["level"] < 1
                    or interval["kind"] not in {"confidence", "credible", "range"}
                    or row["y"]["status"] != "exact"
                    or not interval["lower"] <= row["y"]["value"] <= interval["upper"]):
                raise ValueError("figure_invalid_uncertainty_interval")
            for key in ("lower", "upper"):
                _transformed(interval[key], data["axes"]["y"]["transform"])
        elif data["uncertainty_policy"] == "require_interval" and row["y"]["status"] == "exact":
            raise ValueError("figure_required_uncertainty_absent")
        if curve and row["x"]["status"] == "exact":
            key = (row["entity_id"], row["group_id"])
            if key in chronological and row["x"]["value"] <= chronological[key]:
                raise ValueError("figure_curve_coordinates_not_chronological")
            chronological[key] = row["x"]["value"]
    if observed != membership or len({json.dumps(item) for item in observed}) != len(observed):
        raise ValueError("figure_numeric_membership_or_join_mismatch")
    if "projection_ref" in payload:
        from .episode_proposals import _load
        _manifest_ref(payload["projection_ref"])
        _, projection, _ = _load(payload["projection_ref"]["manifest_path"], payload["projection_ref"]["root_hash"], "figure-projection.json")
        _closed(projection, {"format", "reference_table_ref", "actual_table_ref", "numeric_source_ref", "uncertainty_policy", "limitations"})
        if (projection["format"] != "devbench.gene-effect-plot-projection/2"
                or projection["numeric_source_ref"] != payload["source_ref"]
                or _gene_effect_plot_data(projection["reference_table_ref"], projection["actual_table_ref"],
                    projection["uncertainty_policy"], projection["limitations"]) != data):
            raise ValueError("figure_gene_table_projection_or_join_changed")
    return deepcopy(payload)


def _gene_effect_plot_data(reference_ref, actual_ref, uncertainty_policy, limitations):
    from .rna_publication_metrics import validate_gene_table, validate_gene_comparability
    from devbench.study.evidence import decode_json
    reference = validate_gene_table(decode_json(_source_ref(reference_ref)))
    actual = validate_gene_table(decode_json(_source_ref(actual_ref)))
    if validate_gene_comparability(reference, actual, comparison_kind="effects")["status"] != "comparable":
        raise ValueError("figure_gene_tables_incomparable_alignment_mapping_or_family")
    left, right = ({row["feature_id"]: row for row in table["rows"]} for table in (reference, actual))
    membership = [row["feature_id"] for row in reference["rows"]]
    membership.extend(row["feature_id"] for row in actual["rows"] if row["feature_id"] not in left)
    rows = []
    for gene in membership:
        values = []
        for table in (left, right):
            row = table.get(gene)
            number = row["effect"] if row is not None and row["status"] == "ok" else None
            values.append({"status": "missing" if number is None else "exact", "value": number, "censoring": None})
        rows.append({"entity_id": gene, "coordinate_id": None, "group_id": "matched_effects",
            "x": values[0], "y": values[1], "interval": right.get(gene, {}).get("uncertainty") if values[1]["status"] == "exact" else None})
    return {"format": "devbench.numeric-plot-source/2", "plot_kind": "effect_scatter", "entity_kind": "gene",
        "axes": {axis: {"field": field, "label": label, "unit": actual["alignment"]["effect_scale"],
            "transform": "identity", "direction": "increasing", "range_policy": "saved_full_range_no_clipping"}
            for axis, field, label in (("x", "reference_effect", "Reference effect"), ("y", "actual_effect", "Actual effect"))},
        "membership": membership, "rows": rows, "uncertainty_policy": uncertainty_policy, "limitations": list(limitations)}


def prepare_gene_effect_plot_payload(output, *, reference_table_ref, actual_table_ref, uncertainty_policy, limitations):
    """Project reviewed gene tables with explicit identity joins and no filtering."""
    from .episode_proposals import _seal, require_disjoint_output
    from devbench.study.permissions import require_protected_storage
    data = _gene_effect_plot_data(reference_table_ref, actual_table_ref, uncertainty_policy, limitations)
    output = require_disjoint_output(_path(output), [_path(reference_table_ref["path"]), _path(actual_table_ref["path"])])
    require_protected_storage(output)
    output.mkdir(parents=True, exist_ok=False)
    atomic_json(output / "numeric-source.json", data)
    source = {"path": str(output / "numeric-source.json"), "sha256": sha256(output / "numeric-source.json"),
              "size_bytes": (output / "numeric-source.json").stat().st_size}
    receipt = _seal(output / "projection", "figure-projection.json", {"format": "devbench.gene-effect-plot-projection/2",
        "reference_table_ref": deepcopy(reference_table_ref), "actual_table_ref": deepcopy(actual_table_ref),
        "numeric_source_ref": source, "uncertainty_policy": uncertainty_policy, "limitations": list(limitations)},
        "analysis.gene_effect_plot_projection", scope=_PLOT_SCOPE)
    payload = {"format": "devbench.figure-data/2", "source_ref": source, "data": data,
        "projection_ref": {"manifest_path": receipt["manifest_path"], "root_hash": receipt["root_hash"]}}
    return validate_publication_plot_payload(payload)


def _validate_publication_binding(binding):
    _closed(binding, {"input_sha256", "parameters_sha256", "analysis_source_sha256",
                      "runtime_sha256", "output_bundle_ref"})
    if (not all(_hash(binding[key]) for key in set(binding) - {"output_bundle_ref", "runtime_sha256"})
            or (binding["runtime_sha256"] is not None and not _hash(binding["runtime_sha256"]))):
        raise ValueError("figure_analysis_source_runtime_parameters_binding_required")
    if "manifest_path" in binding["output_bundle_ref"]:
        return _manifest_ref(binding["output_bundle_ref"])[1]
    _source_ref(binding["output_bundle_ref"])
    return {"artifacts": []}


def derive_publication_figure_binding(payload, *, output_bundle_ref, artifact_service):
    """Bind actual inputs/parameters/source; an unknown runtime stays null."""
    from .rna_comparison_outputs import load_comparison_outputs
    from .episode_proposals import _load
    validate_publication_plot_payload(payload)
    if "projection_ref" not in payload:
        raise ValueError("figure_governed_gene_table_projection_required")
    bundle = load_comparison_outputs(output_bundle_ref, artifact_service=artifact_service)
    _, projection, _ = _load(payload["projection_ref"]["manifest_path"], payload["projection_ref"]["root_hash"], "figure-projection.json")
    matches = [table for table in bundle["gene_tables"] if table["table_ref"] == projection["actual_table_ref"]]
    if len(matches) != 1:
        raise ValueError("figure_actual_table_not_bound_to_governed_outputs")
    retained = next(run for run in bundle["runs"] if run["run_id"] == matches[0]["run_id"])
    run = artifact_service.get("run", retained["run_id"])
    method = artifact_service.method(retained["method_version_id"])
    source = artifact_service.get("artifact", method["source_artifact"])
    artifact_service.artifact_path(source["artifact_id"])
    execution = run.get("execution", {})
    environment = method["contract"]["environment"]
    runtime = (environment["identity_sha256"] if environment.get("runtime") == "trusted_dispatcher"
        and execution.get("runtime") == "trusted_dispatcher" else None)
    return {"input_sha256": digest(retained["input_refs"]), "parameters_sha256": digest(run["parameters"]),
        "analysis_source_sha256": source["sha256"], "runtime_sha256": runtime,
        "output_bundle_ref": deepcopy(output_bundle_ref)}


def _governed_plot_binding(payload, binding, artifact_service):
    expected = derive_publication_figure_binding(payload, output_bundle_ref=binding["output_bundle_ref"], artifact_service=artifact_service)
    if binding != expected:
        raise ValueError("figure_provenance_binding_differs_from_actual_retained_run")
    return expected["runtime_sha256"] is not None


def _validate_publication_figure(record, payload, *, artifact_service=None):
    _closed(record, {"format", "figure_id", "plot_kind", "analysis_binding", "renderer_identity",
        "render_parameters_sha256", "artifacts", "pixels_status", "numeric_source_status", "output_binding_status", "governed_output_status", "provenance_status",
        "comparison_binding_status", "scope", "scientific_validity_established"})
    validate_publication_plot_payload(payload)
    if (record["format"] != "devbench.figure/2" or not isinstance(record["figure_id"], str)
            or not _ID.fullmatch(record["figure_id"]) or record["plot_kind"] != payload["data"]["plot_kind"]
            or record["pixels_status"] != "not_assessed" or record["numeric_source_status"] != "retained_verified"
            or record["comparison_binding_status"] != "not_comparison_bound"
            or record["scope"] != _PLOT_SCOPE or record["scientific_validity_established"] is not False):
        raise ValueError("figure_invalid_publication_record_or_authority")
    linked = _validate_publication_binding(record["analysis_binding"])
    bound = any(item.get("kind") == "file" and item.get("sha256") == payload["source_ref"]["sha256"]
                and item.get("size_bytes") == payload["source_ref"]["size_bytes"] for item in linked["artifacts"])
    if record["output_binding_status"] != ("linked_saved_numeric_source" if bound else "render_only_unbound"):
        raise ValueError("figure_output_numeric_source_binding_mismatch")
    if record["governed_output_status"] not in {"revalidated", "custody_verified_runtime_unknown", "not_reverified"}:
        raise ValueError("figure_invalid_governed_output_verification_status")
    if record["provenance_status"] not in {"retained_run_bindings_verified", "retained_run_bindings_runtime_unknown", "unverified_declarations"}:
        raise ValueError("figure_invalid_provenance_assurance")
    if artifact_service is not None:
        runtime_known = _governed_plot_binding(payload, record["analysis_binding"], artifact_service)
        if (record["governed_output_status"] != ("revalidated" if runtime_known else "custody_verified_runtime_unknown")
                or record["provenance_status"] != ("retained_run_bindings_verified" if runtime_known else "retained_run_bindings_runtime_unknown")):
            raise ValueError("figure_record_does_not_claim_the_verified_governed_binding")
    _closed(record["renderer_identity"], {"source_sha256", "environment_sha256"})
    if not all(_hash(value) for value in record["renderer_identity"].values()) or not _hash(record["render_parameters_sha256"]):
        raise ValueError("figure_invalid_renderer_or_parameter_identity")
    _closed(record["artifacts"], {"png", "pdf", "plot_data"})
    for key, name in (("png", "figure.png"), ("pdf", "figure.pdf"), ("plot_data", "plot-data.json")):
        item = record["artifacts"][key]
        _closed(item, {"path", "sha256", "size_bytes"})
        if item["path"] != name or not _hash(item["sha256"]) or type(item["size_bytes"]) is not int or item["size_bytes"] < 0:
            raise ValueError("figure_invalid_retained_artifact_reference")
    return record


def _render_publication_figure(record, payload, output, options=None, *, artifact_service=None):
    _validate_publication_figure(record, payload, artifact_service=artifact_service)
    if options not in (None, {}):
        raise ValueError("publication_display_options_not_implemented")
    output = _path(output)
    if output.suffix not in {".png", ".pdf"} or not output.parent.is_dir():
        raise ValueError("figure_output_requires_PNG_or_PDF_in_existing_directory")
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    data = payload["data"]
    with plt.rc_context({"font.family": "DejaVu Sans", "font.size": 10, "text.usetex": False,
                         "text.parse_math": False, "figure.dpi": 110, "savefig.dpi": 110}):
        fig, ax = plt.subplots(figsize=(7, 5), constrained_layout=True)
        try:
            groups = sorted({(row["group_id"], row["entity_id"] if data["plot_kind"] == "measured_curve" else "")
                             for row in data["rows"]})
            for group, entity in groups:
                rows = [row for row in data["rows"] if row["group_id"] == group
                        and (not entity or row["entity_id"] == entity)
                        and all(row[key]["status"] == "exact" for key in ("x", "y"))]
                x = [_transformed(row["x"]["value"], data["axes"]["x"]["transform"]) for row in rows]
                y = [_transformed(row["y"]["value"], data["axes"]["y"]["transform"]) for row in rows]
                label = group + (": " + entity if entity else "")
                if data["plot_kind"] == "measured_curve":
                    ax.plot(x, y, marker="o", label=label)
                else:
                    ax.scatter(x, y, s=24, label=label)
                for xx, yy, row in zip(x, y, rows):
                    if row["interval"] is not None:
                        bounds = [_transformed(row["interval"][key], data["axes"]["y"]["transform"])
                                  for key in ("lower", "upper")]
                        low, high = sorted(bounds)
                        ax.errorbar(xx, yy, yerr=[[yy-low], [high-yy]], fmt="none", capsize=3, color="0.4")
            labels = {}
            for name, axis in data["axes"].items():
                unit_present = re.search(r"(?<!\w)" + re.escape(axis["unit"]) + r"(?!\w)", axis["label"], flags=re.IGNORECASE) is not None
                labels[name] = axis["label"] if unit_present else axis["label"] + " (" + axis["unit"] + ")"
                annotation = {"natural_log1p": "[ln(1+x)]", "negative_log10": "[-log10(x)]"}.get(axis["transform"])
                if annotation:
                    labels[name] += " " + annotation
            ax.set(xlabel=labels["x"], ylabel=labels["y"],
                   title=record["figure_id"])
            ax.margins(.08)
            if ax.has_data(): ax.legend(loc="best", frameon=False)
            metadata = ({"Software": "DevBench frozen numeric figure renderer"} if output.suffix == ".png"
                        else {"Creator": "DevBench frozen numeric figure renderer", "CreationDate": None, "ModDate": None})
            fig.savefig(output, metadata=metadata)
        finally:
            plt.close(fig)
    return {}


def export_publication_figure(output, payload, *, figure_id, analysis_binding, artifact_service=None):
    """Retain PNG, PDF and source-bound plot data under the existing DAG."""
    from .episode_proposals import require_disjoint_output
    from devbench.study.permissions import require_protected_storage
    from devharness_core.provenance import ExecutionDAG
    payload = validate_publication_plot_payload(payload)
    linked = _validate_publication_binding(analysis_binding)
    if not isinstance(figure_id, str) or not _ID.fullmatch(figure_id):
        raise ValueError("invalid_figure_id")
    original = analysis_binding["output_bundle_ref"]
    proof_path = _path(original["manifest_path"] if "manifest_path" in original else original["path"])
    proof_location = proof_path.parent.parent if "manifest_path" in original else proof_path
    output = require_disjoint_output(_path(output), [_path(payload["source_ref"]["path"]), proof_location])
    runtime_known = _governed_plot_binding(payload, analysis_binding, artifact_service) if artifact_service is not None else False
    identity = renderer_identity()  # Missing optional renderer fails before writing.
    require_protected_storage(output)
    record = {"format": "devbench.figure/2", "figure_id": figure_id, "plot_kind": payload["data"]["plot_kind"],
        "analysis_binding": deepcopy(analysis_binding), "renderer_identity": identity,
        "render_parameters_sha256": digest({"dpi": 110, "font": "DejaVu Sans", "width": 770, "height": 550}),
        "artifacts": {key: {"path": name, "sha256": "0" * 64, "size_bytes": 0}
            for key, name in (("png", "figure.png"), ("pdf", "figure.pdf"), ("plot_data", "plot-data.json"))},
        "pixels_status": "not_assessed", "numeric_source_status": "retained_verified",
        "comparison_binding_status": "not_comparison_bound",
        "output_binding_status": "linked_saved_numeric_source" if any(
            item.get("kind") == "file" and item.get("sha256") == payload["source_ref"]["sha256"]
            and item.get("size_bytes") == payload["source_ref"]["size_bytes"] for item in linked["artifacts"])
            else "render_only_unbound", "governed_output_status": "revalidated" if runtime_known else "custody_verified_runtime_unknown" if artifact_service is not None else "not_reverified",
        "provenance_status": "retained_run_bindings_verified" if runtime_known else "retained_run_bindings_runtime_unknown" if artifact_service is not None else "unverified_declarations", "scope": _PLOT_SCOPE,
        "scientific_validity_established": False}
    _validate_publication_figure(record, payload, artifact_service=artifact_service)
    output.mkdir(parents=True, exist_ok=False)
    atomic_json(output / "plot-data.json", payload)
    for suffix in ("png", "pdf"):
        render_figure_artifact(record, payload, output / ("figure." + suffix), artifact_service=artifact_service)
    for artifact in record["artifacts"].values():
        path = output / artifact["path"]
        artifact.update(sha256=sha256(path), size_bytes=path.stat().st_size)
    atomic_json(output / "figure.json", record)
    audit = ExecutionDAG(output / "audit", actor_id="numeric-figure-coordinator")
    for name in ("figure.json", "plot-data.json", "figure.png", "figure.pdf"):
        audit.add_artifact(output / name, max_bytes=MAX_PAYLOAD_BYTES)
    audit.append("analysis.numeric_figure", {"figure_id": figure_id, "pixels_status": "not_assessed", "scope": _PLOT_SCOPE})
    receipt = audit.finalize(analysis_fingerprint=digest(record))
    atomic_json(output / "root-receipt.json", receipt)
    return {"manifest_path": receipt["manifest_path"], "root_hash": receipt["root_hash"]}


def load_publication_figure(manifest, expected_root, *, expected_renderer_identity=None, artifact_service=None):
    path, declared = _manifest_ref({"manifest_path": str(manifest), "root_hash": expected_root})
    root = path.parent.parent
    refs = {item["name"]: item for item in declared["artifacts"]}
    if set(refs) != {"figure.json", "plot-data.json", "figure.png", "figure.pdf"}:
        raise ValueError("figure_unexpected_retained_artifacts")
    for name, item in refs.items():
        data = _read(root / name, MAX_PAYLOAD_BYTES)
        import hashlib
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("figure_retained_artifact_hash_mismatch")
    record, payload = _json(root / "figure.json"), _json(root / "plot-data.json", MAX_PAYLOAD_BYTES)
    _validate_publication_figure(record, payload, artifact_service=artifact_service)
    if digest(record) != declared["analysis_fingerprint"]:
        raise ValueError("figure_fingerprint_mismatch")
    expected_renderer_identity = expected_renderer_identity or renderer_identity()
    if record["renderer_identity"] != expected_renderer_identity:
        raise ValueError("figure_renderer_identity_mismatch")
    for item in record["artifacts"].values():
        if (sha256(root / item["path"]) != item["sha256"]
                or (root / item["path"]).stat().st_size != item["size_bytes"]):
            raise ValueError("figure_recorded_artifact_hash_or_size_mismatch")
    for key, signature in (("png", b"\x89PNG\r\n\x1a\n"), ("pdf", b"%PDF-")):
        ref = record["artifacts"][key]
        data = _read(root / ref["path"], MAX_PAYLOAD_BYTES)
        if not data.startswith(signature) or len(data) != ref["size_bytes"] or sha256(root / ref["path"]) != ref["sha256"]:
            raise ValueError("figure_image_signature_size_or_hash_mismatch")
    return record, payload


def compare_publication_figure_data(actual, reference, *, absolute_tolerance, relative_tolerance):
    """Compare saved numeric coordinates; never assign a pixel truth score."""
    if not all(_finite(value) and value >= 0 for value in (absolute_tolerance, relative_tolerance)):
        raise ValueError("figure_explicit_finite_tolerances_required")
    a = validate_publication_plot_payload(actual)["data"]
    result = {"status": "not_assessed", "reason_codes": [], "matched_n": 0, "assessed_n": 0,
              "max_absolute_difference": None, "pixel_truth_score": None, "scope": _PLOT_SCOPE}
    if reference is None:
        result["reason_codes"] = ["reference_numeric_source_absent"]
        return result
    b = validate_publication_plot_payload(reference)["data"]
    mismatches = [key for key in ("plot_kind", "entity_kind", "membership") if a[key] != b[key]]
    for axis in ("x", "y"):
        if any(a["axes"][axis][key] != b["axes"][axis][key]
               for key in ("field", "unit", "transform", "direction", "range_policy")):
            mismatches.append(axis + "_axis_semantics")
    if [row["group_id"] for row in a["rows"]] != [row["group_id"] for row in b["rows"]]:
        mismatches.append("group_membership")
    if mismatches:
        result.update(status="incomparable", reason_codes=["incompatible:" + key for key in mismatches])
        return result
    result["matched_n"] = len(a["rows"])
    if any(row[key]["status"] != "exact" for data in (a, b) for row in data["rows"] for key in ("x", "y")):
        result["reason_codes"] = ["censored_or_missing_coordinates"]
        return result
    if any(row["interval"] is None for data in (a, b) for row in data["rows"]):
        result["reason_codes"] = ["uncertainty_absent"]
        return result
    if any(any(left["interval"][key] != right["interval"][key] for key in ("kind", "level"))
           for left, right in zip(a["rows"], b["rows"])):
        result.update(status="incomparable", reason_codes=["incompatible:uncertainty_semantics"])
        return result
    differences, agreement = [], True
    for left, right in zip(a["rows"], b["rows"]):
        values = [(left[key]["value"], right[key]["value"]) for key in ("x", "y")]
        values.extend((left["interval"][key], right["interval"][key]) for key in ("lower", "upper"))
        for observed, expected in values:
            delta = abs(observed-expected)
            bound = absolute_tolerance + relative_tolerance*abs(expected)
            if not _finite(delta) or not _finite(bound):
                result.update(status="not_assessed", reason_codes=["nonfinite_geometry_discrepancy_or_bound"])
                return result
            differences.append(delta)
            agreement &= delta <= bound
    result.update(status="agreement" if agreement else "disagreement", assessed_n=len(a["rows"]),
                  max_absolute_difference=max(differences, default=None))
    return result


def record_publication_figure_review(output, manifest, expected_root, *, reviewer_label, observations, allow_review=False):
    """Record a pixel inspection declaration separately from rendered evidence."""
    if allow_review is not True:
        raise PermissionError("Pixel inspection recording requires review authorization")
    from .episode_proposals import _seal, require_disjoint_output
    record, _ = load_publication_figure(manifest, expected_root)
    if not _text(reviewer_label) or type(observations) is not list or not observations or len(observations) > 64 or not all(_text(item) for item in observations):
        raise ValueError("figure_bounded_pixel_observations_required")
    output = require_disjoint_output(_path(output), [_path(manifest).parent.parent])
    return _seal(output, "pixel-review.json", {"format": "devbench.figure-pixel-review/2",
        "figure_ref": {"manifest_path": str(manifest), "root_hash": expected_root}, "figure_id": record["figure_id"],
        "image_sha256": record["artifacts"]["png"]["sha256"], "pixels_status": "inspected",
        "reviewer_label": reviewer_label, "observations": list(observations), "qualified_review": "not_established",
        "scientific_validity_established": False}, "analysis.figure_pixels_inspected", scope=_PLOT_SCOPE)
