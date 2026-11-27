"""Bounded format readers. Numerical properties describe the inspected matrix only."""

from __future__ import annotations

import csv
import math
import re
from decimal import Decimal, InvalidOperation

import h5py
import numpy as np

from devbench.analysis.rna_scrna_input_contract import _closed_storage
from devbench.analysis.workbench_inputs import (
    _h5ad_axis_header,
    _h5ad_header,
    _h5ad_sparse_layout,
)
from devbench.core.count_forensics import CountDistribution
from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.plugin_workflows import (
    _h5_declares_count_transform,
    _h5_matrix_storage,
    _h5_value_blocks,
    _sparse_library_blocks,
)

from ._sources import MAX_HEADER_BYTES, QUOTA, check_time, clinical, text_input

FEATURE_KEYS = frozenset(
    {
        "gene",
        "genes",
        "gene_id",
        "gene_name",
        "transcript_id",
        "feature_id",
        "feature",
        "ensembl_id",
        "",
    }
)
UNTYPED = "COUNT-1 was not assessed for this representation."


def axes(n_obs=None, n_var=None, *, obs=(), var=(), layers=(), obsm=()):
    return {
        "observations": n_obs,
        "features": n_var,
        "obs_columns": list(obs)[:128],
        "var_columns": list(var)[:64],
        "layers": list(layers)[:128],
        "obsm_keys": list(obsm)[:128],
    }


def unassessed(message=UNTYPED):
    return {
        "integer_valued": False,
        "nonnegative": False,
        "suspected_transformed": False,
        "warnings": [message],
    }


class CountInspection:
    def __init__(self, distribution):
        self.distribution = distribution
        self.integer = True
        self.nonnegative = True
        self.seen = False

    def observe(self, values, indices=None):
        values = np.asarray(values)
        self.seen |= bool(values.size)
        if values.dtype.kind not in "ifu" or not np.isfinite(values).all():
            self.integer = self.nonnegative = False
            return
        self.nonnegative &= bool(np.all(values >= 0))
        self.integer &= bool(np.all((values > -(2**53)) & (values < 2**53)))
        if values.dtype.kind == "f":
            self.integer &= bool(np.all(values == np.floor(values)))
        if self.integer and self.nonnegative:
            self.distribution.observe(values, library_indices=indices)

    def report(self, *, declared_transform=False):
        suspected = bool(declared_transform or self.distribution.suspected_transform)
        warnings = []
        if suspected:
            warnings.append("suspected_transformed_integer_counts")
        if not self.seen:
            warnings.append("No observed count values.")
        if self.integer and self.nonnegative and not suspected:
            warnings.append("Raw count ancestry requires a source declaration.")
        return {
            "integer_valued": self.integer and self.seen,
            "nonnegative": self.nonnegative and self.seen,
            "suspected_transformed": suspected,
            "warnings": warnings,
        }


def bounded_lines(text, until):
    while line := text.readline(MAX_HEADER_BYTES + 1):
        check_time(until)
        if len(line) > MAX_HEADER_BYTES:
            raise OperationBlocked(QUOTA)
        yield line


def table_rows(stream, path, until):
    delimiter = "\t" if ".tsv" in path.suffixes else ","
    with text_input(stream, path.suffix == ".gz") as text:
        yield from csv.reader(
            bounded_lines(text, until), delimiter=delimiter, strict=True
        )


def _decimal_row(values, probe):
    try:
        numbers = [Decimal(value) for value in values]
        finite = all(n.is_finite() for n in numbers)
        if not finite:
            probe.seen = True
            probe.integer = probe.nonnegative = False
            return
        probe.nonnegative &= all(n >= 0 for n in numbers)
        probe.integer &= all(
            n == n.to_integral_value() and abs(n) < 2**53 for n in numbers
        )
        probe.seen = True
        if probe.integer and probe.nonnegative:
            probe.observe([int(n) for n in numbers])
    except InvalidOperation:
        probe.seen = True
        probe.integer = probe.nonnegative = False


def csv_profile(stream, path, kind, until):
    rows = table_rows(stream, path, until)
    header = next(rows, [])
    if not header or len(set(header)) != len(header):
        raise ValueError("Table header is missing or ambiguous")
    sensitive = any(map(clinical, header))
    expression = _gene_matrix(header) or (
        kind == "expression"
        and header[0].casefold() in FEATURE_KEYS
        and len(header) > 1
    )
    if sensitive:
        return axes(), unassessed(), True, expression
    probe = (
        CountInspection(
            CountDistribution(major_width=len(header) - 1, libraries_in_rows=False)
        )
        if expression
        else None
    )
    count = 0
    for row in rows:
        if len(row) != len(header):
            raise ValueError("Inconsistent table row width")
        if probe is not None:
            _decimal_row(row[1:], probe)
        if kind == "manifest" and any(clinical(value) for value in row):
            sensitive = True
        count += 1
    if probe is not None:
        return axes(len(header) - 1, count), probe.report(), sensitive, True
    return axes(count, len(header), obs=header), unassessed(), sensitive, False


def _h5_counts(node, shape, until):
    layout = _h5ad_sparse_layout(node)[0] if isinstance(node, h5py.Group) else None
    storage = _h5_matrix_storage(node, shape, layout)
    if storage is None:
        return unassessed("Unsupported or noncanonical count storage.")
    data, indices, pointers = storage
    major = layout != "csc"
    counts = CountInspection(
        CountDistribution(
            total_values=math.prod(shape),
            major_width=shape[1] if major else shape[0],
            libraries_in_rows=major,
        )
    )
    if indices is None:
        blocks = ((block, None) for block in _h5_value_blocks(data))
    else:
        blocks = _sparse_library_blocks(data, indices, pointers, major)
        # An all-zero sparse matrix has observed implicit zeros.
        counts.seen = math.prod(shape) > len(data)
    for values, libraries in blocks:
        check_time(until)
        counts.observe(values, libraries)
    return counts.report(
        declared_transform=(
            _h5_declares_count_transform(node) or "uns/log1p" in node.file
        )
    )


def hdf_profile(stream, fmt, inspect_layers, until):
    with h5py.File(stream, "r", rdcc_nbytes=1 << 20) as handle:
        _closed_storage(handle)
        names_private = any(map(clinical, handle))

        def check_name(name, node):
            nonlocal names_private
            if any(map(clinical, name.split("/"))):
                names_private = True
            if isinstance(node, h5py.Group):
                names_private |= any(map(clinical, node))
            if isinstance(node, h5py.Dataset) and node.dtype.names:
                names_private |= any(map(clinical, node.dtype.names))

        handle.visititems(check_name)
        if names_private:
            return axes(), unassessed(), True, True
        if fmt == "h5mu":
            n_obs, obs = _h5ad_axis_header(handle, "obs", 4096)
            n_var, var = _h5ad_axis_header(handle, "var", 4096)
            return (
                axes(n_obs, n_var, obs=obs, var=var),
                unassessed("Select an individual modality for COUNT-1 forensics."),
                False,
                True,
            )
        header = _h5ad_header(handle, 4096)
        shape = header["cells"], header["genes"]
        dimensions = axes(
            *shape,
            obs=header["obs_columns"],
            var=header["var_columns"],
            layers=header["layers"] if inspect_layers else (),
            obsm=header["obsm_keys"],
        )
        return dimensions, _h5_counts(handle["X"], shape, until), False, True


_STAT_FIELDS = frozenset(
    {
        "p",
        "q",
        "pval",
        "qval",
        "fdr",
        "fwer",
        "pvalue",
        "qvalue",
        "padj",
        "p_value",
        "q_value",
        "p_val",
        "p_val_adj",
        "adj_p_val",
        "p_adj",
        "log2foldchange",
        "logfc",
        "log2_fold_change",
        "avg_log2fc",
        "effect",
        "effect_size",
        "statistic",
        "stat",
        "estimate",
        "se",
        "lfcse",
        "stderr",
        "std_error",
        "lower_ci",
        "upper_ci",
        "basemean",
        "mean",
        "median",
        "count",
        "n",
        "score",
        "rank",
        "significant",
        "contrast",
        "method",
        "gene_name",
        "gene_symbol",
        "symbol",
        "biotype",
        "feature_name",
        "direction",
        "pathway",
        "set_id",
        "cluster",
        "population",
        "condition",
        "group",
        "term",
    }
)
_GENE_COLUMN = re.compile(r"(?:ENS[A-Z]*G\d+|[Gg]ene[_-]?[A-Za-z0-9]+|G\d+)")


def _gene_matrix(names):
    """Detect expression matrices independently of the declared artifact role."""
    if len(names) < 2:
        return False
    identity = names[0].casefold()
    remaining = names[1:]
    if identity in FEATURE_KEYS:
        # Adding a p-value to a matrix does not turn its sample columns into summaries.
        return any(
            n.casefold().replace("-", "_") not in _STAT_FIELDS for n in remaining
        )
    if identity in {
        "cell",
        "cell_id",
        "barcode",
        "sample",
        "sample_id",
        "spot",
        "spot_id",
        "_index",
    }:
        return any(_GENE_COLUMN.fullmatch(name) for name in remaining)
    return all(_GENE_COLUMN.fullmatch(name) for name in names)


def parquet_fragment(stream):
    import pyarrow.dataset as ds

    return ds.ParquetFileFormat().make_fragment(stream)


def parquet_profile(stream, kind, until):
    fragment = parquet_fragment(stream)
    schema = fragment.physical_schema
    names = schema.names
    if not names or len(set(names)) != len(names):
        raise ValueError("Parquet fields are ambiguous")
    sensitive = _arrow_sensitive(schema)
    matrix = _gene_matrix(names)
    count = fragment.metadata.num_rows
    if sensitive or not matrix or kind != "expression":
        return axes(count, len(names), obs=names), unassessed(), sensitive, matrix
    probe = CountInspection(
        CountDistribution(major_width=len(names) - 1, libraries_in_rows=False)
    )
    for batch in fragment.to_batches(
        columns=names[1:],
        batch_size=512,
        batch_readahead=0,
        fragment_readahead=0,
        use_threads=False,
    ):
        check_time(until)
        if any(a.null_count for a in batch.columns):
            probe.integer = probe.nonnegative = False
            continue
        probe.observe(
            np.column_stack([a.to_numpy(zero_copy_only=False) for a in batch.columns])
        )
    return axes(len(names) - 1, count), probe.report(), False, True


def _arrow_sensitive(fields):
    for field in fields:
        if clinical(field.name):
            return True
        if field.type.num_fields and _arrow_sensitive(
            [field.type.field(i) for i in range(field.type.num_fields)]
        ):
            return True
    return False


def json_profile(stream, until, kind):
    from ._json_tables import header

    fields, count, hidden = header(stream, until, manifest=kind == "manifest")
    return (
        axes(count, len(fields), obs=fields),
        unassessed(),
        hidden,
        _gene_matrix(fields),
    )


def mtx_profile(stream, path, until):
    with text_input(stream, path.suffix == ".gz") as text:
        lines = bounded_lines(text, until)
        banner = next(lines, "").strip().lower().split()
        if (
            len(banner) != 5
            or banner[:3] != ["%%matrixmarket", "matrix", "coordinate"]
            or banner[4] != "general"
        ):
            raise ValueError("Expected a general coordinate Matrix Market matrix")
        data = (line for line in lines if not line.startswith("%") and line.strip())
        genes, libraries, nnz = map(int, next(data).split())
        if min(genes, libraries) < 1 or nnz < 0:
            raise ValueError("Invalid Matrix Market dimensions")
        counts = CountInspection(
            CountDistribution(
                total_values=genes * libraries,
                major_width=libraries,
                libraries_in_rows=False,
            )
        )
        count = 0
        for line in data:
            gene, library, raw = line.split()
            i, j = int(gene), int(library)
            if not (1 <= i <= genes and 1 <= j <= libraries):
                raise ValueError("Invalid Matrix Market coordinate")
            number = Decimal(raw)
            counts.seen = True
            counts.nonnegative &= number.is_finite() and number >= 0
            counts.integer &= (
                number.is_finite()
                and number == number.to_integral_value()
                and abs(number) < 2**53
            )
            if counts.integer and counts.nonnegative:
                counts.observe([int(number)], j - 1)
            count += 1
        if count != nnz:
            raise ValueError("Matrix Market entry count changed")
        return axes(libraries, genes), counts.report(), False, True


def file_profile(stream, path, fmt, kind, inspect_layers, until):
    if fmt in {"csv", "tsv"}:
        return csv_profile(stream, path, kind, until)
    if fmt in {"h5ad", "h5mu"}:
        return hdf_profile(stream, fmt, inspect_layers, until)
    if fmt == "parquet":
        return parquet_profile(stream, kind, until)
    if fmt == "mtx":
        return mtx_profile(stream, path, until)
    if fmt == "json":
        return json_profile(stream, until, kind)
    raise ValueError("Unsupported input format")


_RAW_ORIGIN = re.compile(
    r"(?:original|raw|unprocessed|untransformed)\s+(?:(?:raw|synthetic|gene|umi|"
    r"read|integer|nonnegative|unfiltered|cell ranger|sequencing)\s+)*counts?"
    r"(?:\s+(?:fixture|matrix|in x))?\.?\Z",
    re.IGNORECASE,
)


def raw_declared(provenance, checks):
    return bool(
        checks["integer_valued"]
        and checks["nonnegative"]
        and not checks["suspected_transformed"]
        and _RAW_ORIGIN.fullmatch((provenance or "").strip().replace("_", " "))
    )
