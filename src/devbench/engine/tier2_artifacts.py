"""Structural checks for governed exploratory source and declared output ports."""

from __future__ import annotations

import ast
import csv
import os
import re
import shutil
import subprocess
from pathlib import Path

from devharness_core.provenance import hash_artifact

from .tier2_staging import ENTRY_LIMIT

SOURCE_BYTES = 512 * 1024
VAULT_BYTES = 1 << 40
LANE_TAGS = {
    "execution_lane": "tier2_exploratory",
    "claim_tier": "L1_execution_verified",
    "exploratory_only": True,
    "figure_verification": "produced_unverified",
    "clinical_release_allowed": False,
}
_FORBIDDEN = re.compile(
    r"/proc/1(?:/|\b)|/(?:var/)?run/docker\.sock|/etc/passwd|(?:~|/root)/\.codex\b"
)
_COUNT_MODELS = re.compile(
    r"DESeq\s*\(|DGEList\s*\(|NegativeBinomial\s*\(|glmQLFit\s*\("
)
_FIGURES = frozenset({"png", "svg", "plotly_json", "figure"})


def _parse_python(source):
    try:
        tree = ast.parse(source)
    except (SyntaxError, RecursionError) as error:
        raise ValueError("Exploratory Python syntax check failed") from error
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _FORBIDDEN.search(node.value)
        ):
            raise PermissionError("Exploratory source references a forbidden host path")


def preflight_source(path, language, representations=()):
    """Syntax checks never evaluate the script. Paths and parser stderr stay local."""
    from devbench.core.population_data import bound_query_file

    digest = hash_artifact(path, max_bytes=SOURCE_BYTES, max_entries=1)["sha256"]
    with bound_query_file(path, digest, SOURCE_BYTES) as stream:
        source = stream.read(SOURCE_BYTES + 1).decode("utf-8", errors="strict")
    warnings = []
    if _FORBIDDEN.search(source):
        raise PermissionError("Exploratory source references a forbidden host path")
    if language == "python":
        _parse_python(source)
        syntax = "python_ast"
    elif language == "r":
        binary = shutil.which("Rscript")
        syntax = "r_parse_unavailable"
        if binary:
            # Only read stdin as text passed to parse(). No user-defined profile.
            check = subprocess.run(
                [binary, "--vanilla", "-e", "invisible(parse(file=file('stdin')))"],
                input=source.encode(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
                env={"PATH": os.defpath, "LC_ALL": "C"},
            )
            if check.returncode:
                raise ValueError("Exploratory R syntax check failed")
            syntax = "r_parse"
        else:
            warnings.append("r_syntax_check_unavailable")
    else:
        raise ValueError("Unsupported exploratory script language")
    if _COUNT_MODELS.search(source) and not (
        set(representations)
        & {"COUNT-1", "count_1_raw_counts", "aggregated_unit_counts"}
    ):
        warnings.append("suspected_non_count_1_fit")
    if re.search(r"hold.?out|test_ids|evaluation_ids", source, re.I) and re.search(
        r"train|fit\(", source, re.I
    ):
        warnings.append("suspected_holdout_id_mixing")
    return {"script_sha256": digest, "syntax_check": syntax, "warnings": warnings}


def port_format(spec):
    if "format" in spec:
        return spec["format"]
    path = Path(spec["path"])
    if path.name.endswith(".plotly.json"):
        return "plotly_json"
    return path.suffix.lower().lstrip(".") or spec["artifact_type"]


def port_kind(spec):
    fmt = port_format(spec)
    if fmt in _FIGURES:
        return "figure"
    return "continuous_matrix" if fmt in {"h5ad", "zarr"} else "table"


def _finite_json(path, maximum):
    from devbench.analysis.method_validation import _bounded_finite_json

    return _bounded_finite_json(path, maximum)


def _verify_h5ad(path, expected_sha256):
    from devbench.core.hdf5_custody import validate_h5ad_custody

    validate_h5ad_custody(path, expected_sha256=expected_sha256)


def _table_columns(path, fmt, spec):
    if fmt == "parquet":
        import pyarrow.parquet as pq

        with pq.ParquetFile(
            path,
            thrift_string_size_limit=262144,
            thrift_container_size_limit=65536,
            arrow_extensions_enabled=False,
        ) as reader:
            names = reader.schema_arrow.names
            if any(
                reader.metadata.row_group(i).total_byte_size < 0
                for i in range(reader.num_row_groups)
            ):
                raise ValueError("Invalid Parquet row group")
    else:
        with path.open(encoding="utf-8", newline="") as stream:
            rows = csv.reader(stream, delimiter="\t" if fmt == "tsv" else ",")
            names = next(rows, [])
            if any(len(row) != len(names) for row in rows):
                raise ValueError("Tabular output row differs from its header")
    if (
        not names
        or len(names) > 4096
        or len(set(names)) != len(names)
        or spec.get("columns", names) != names
    ):
        raise ValueError("Output columns differ from the declared table schema")
    return names


def _check_sidecar(data):
    if not isinstance(data, dict):
        raise ValueError("A figure requires a structured plot_sidecar")
    stats = data.get("plotted_statistics", data.get("statistics"))
    axes = data.get("axis_labels", data.get("axes"))
    if (
        not isinstance(stats, (dict, list))
        or not stats
        or not isinstance(axes, (dict, list))
        or not axes
        or any(
            type(data.get(k)) is not int or data[k] < 0
            for k in ("sample_count", "donor_count")
        )
        or data["donor_count"] > data["sample_count"]
        or not isinstance(data.get("transformations"), list)
    ):
        raise ValueError(
            "Incomplete plot_sidecar statistics, counts, axes or transformations"
        )


def _check_figure(path, fmt, json_max):
    if fmt == "png":
        from PIL import Image

        with Image.open(path) as image:
            if image.format != "PNG" or min(image.size) < 1:
                raise ValueError("Invalid PNG figure")
            image.verify()
    elif fmt == "svg":
        from xml.etree.ElementTree import ParseError, iterparse

        try:
            with path.open("rb") as stream:
                if b"<!DOCTYPE" in stream.read(65536).upper():
                    raise ValueError("SVG document type declarations are unsupported")
                stream.seek(0)
                items = iterparse(stream, events=("start", "end"))
                if next(items)[1].tag.split("}")[-1] != "svg":
                    raise ValueError("Invalid SVG root")
                for event, element in items:
                    if event == "end":
                        element.clear()
        except (ParseError, StopIteration) as error:
            raise ValueError("Invalid SVG figure") from error
    else:
        data = _finite_json(path, json_max)
        if not isinstance(data, dict) or (
            fmt == "plotly_json"
            and (
                not isinstance(data.get("data"), list)
                or not isinstance(data.get("layout", {}), dict)
            )
        ):
            raise ValueError("Invalid JSON figure")


def validate_ports(contract, step, files):
    specs = contract["outputs"]
    maximum = contract["execution"]["max_output_bytes"]
    # The coordinator decodes JSON. The worker memory setting grants no
    # additional controller memory; large derivatives use the streaming formats.
    json_max = min(maximum, 32 * 1024**2)
    required = {name for name, spec in specs.items() if spec["required"]}
    if not files or not required <= files.keys() <= specs.keys():
        raise ValueError("Output inventory differs from the frozen manifest")
    sizes = 0
    hashes = {}
    for port in step.outputs:
        if port.name not in files:
            continue
        path = files[port.name]
        spec = specs[port.name]
        info = hash_artifact(
            path,
            max_bytes=min(maximum - sizes, spec.get("max_bytes", maximum)),
            max_entries=ENTRY_LIMIT,
        )
        sizes += info["size_bytes"]
        hashes[path.name] = info["sha256"]
        _validate_member(path, spec, json_max, info["sha256"])
        if port.artifact_kind == "figure" or port_kind(spec) == "figure":
            sidecar = spec.get("plot_sidecar", "plot_sidecar")
            if sidecar not in files or sidecar == port.name:
                raise ValueError(
                    "Each exploratory figure requires its declared plot_sidecar"
                )
            if port_format(specs[sidecar]) == "parquet":
                cols = _table_columns(files[sidecar], "parquet", specs[sidecar])
                if not {
                    "plotted_statistics",
                    "sample_count",
                    "donor_count",
                    "axis_labels",
                    "transformations",
                } <= set(cols):
                    raise ValueError("Parquet plot_sidecar schema is incomplete")
            else:
                _check_sidecar(_finite_json(files[sidecar], json_max))
    return hashes


def _validate_member(path, spec, json_max, expected_sha256):
    fmt = port_format(spec)
    if fmt == "zarr":
        meta = path / ("zarr.json" if (path / "zarr.json").is_file() else ".zgroup")
        if not path.is_dir() or _finite_json(meta, 65536).get("zarr_format") not in (
            2,
            3,
        ):
            raise ValueError("Invalid Zarr group metadata")
    elif not path.is_file():
        raise ValueError("Only a declared Zarr derivative may be a directory")
    elif fmt in _FIGURES:
        _check_figure(path, fmt, json_max)
    elif fmt in {"parquet", "csv", "tsv"}:
        _table_columns(path, fmt, spec)
    elif fmt == "h5ad":
        _verify_h5ad(path, expected_sha256)
    elif fmt in {"json", "plot_sidecar"} or spec["artifact_type"] == "json":
        _finite_json(path, json_max)
    else:
        raise ValueError("Unsupported exploratory output format")
