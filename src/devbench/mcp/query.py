"""Project verified local tables through a bounded, disk-backed relational query."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import date, datetime
from decimal import Decimal
from itertools import batched
from pathlib import Path
from typing import NamedTuple

import h5py

from devbench.analysis.rna_scrna_input_contract import _closed_storage
from devbench.analysis.workbench_inputs import _h5ad_axis_header, _h5ad_index_name
from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.plugin_h5ad_axes import _reader
from devbench.platform.plugin_mcp import _validate_h5_dataset

from . import schemas
from ._inspection import (
    _arrow_sensitive,
    _gene_matrix,
    parquet_fragment,
    table_rows,
)
from ._sources import PRIVATE, QUOTA, SCOPE, check_time, clinical, deadline
from .intake import PUBLIC, verify_artifact_file

_OPERATORS = {"eq": "=", "neq": "!=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}


def _json_scalar(value):
    if value is None or isinstance(value, (int, bool, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise ValueError("Only scalar table cells can be delivered")


class Table(NamedTuple):
    fields: list
    rows: object
    count: int | None


def _h5_values(node, field, length, selection):
    read, dtype = _reader(node, field, length, _validate_h5_dataset)
    encoding = h5py.check_string_dtype(dtype)
    values = read(selection).tolist()
    if encoding:
        if any(len(v) > 800 for v in values):
            raise OperationBlocked(QUOTA)
        return [v.decode(encoding.encoding) for v in values]
    return [_json_scalar(v) for v in values]


def _h5_categorical(group, size, selected):
    codes = _h5_values(group["codes"], None, size, selected)
    dataset = group["categories"]
    length = dataset.shape[0]
    ids = sorted({c for c in codes if c is not None and 0 <= c < length})
    if any(c not in ids and c != -1 for c in codes):
        raise ValueError("Invalid categorical annotation")
    values = _h5_values(dataset, None, length, ids) if ids else []
    labels = dict(zip(ids, values, strict=True))
    return [labels.get(c) for c in codes]


def _h5_column(axis, name, size, start, stop):
    legacy = isinstance(axis, h5py.Dataset)
    node = axis if legacy else axis[name]
    for offset in range(start, stop, 256):
        selected = slice(offset, min(offset + 256, stop))
        if not isinstance(node, h5py.Group):
            yield from _h5_values(node, name if legacy else None, size, selected)
        elif set(node) == {"categories", "codes"}:
            yield from _h5_categorical(node, size, selected)
        elif set(node) == {"values", "mask"}:
            values = _h5_values(node["values"], None, size, selected)
            mask = _h5_values(node["mask"], None, size, selected)
            yield from (
                None if empty else v for v, empty in zip(values, mask, strict=True)
            )
        else:
            raise ValueError("Unsupported HDF5 annotation encoding")


def _h5_reader(stream, slot):
    handle = h5py.File(stream, "r", rdcc_nbytes=1 << 20)
    try:
        _closed_storage(handle)
        count, names = _h5ad_axis_header(handle, slot, 4096)
        group = handle[slot]
        index = _h5ad_index_name(group)
        names = [index, *names]

        if any(map(clinical, names)):
            raise OperationBlocked(PRIVATE)

        def rows(columns, start=0, limit=None):
            stop = count if limit is None else min(count, start + limit)
            return zip(
                *(_h5_column(group, name, count, start, stop) for name in columns),
                strict=True,
            )

        yield Table(names, rows, count)
    finally:
        handle.close()


def _arrow_reader(stream):
    fragment = parquet_fragment(stream)
    schema = fragment.physical_schema
    if _arrow_sensitive(schema):
        raise OperationBlocked(PRIVATE)

    total = fragment.metadata.num_rows

    def rows(columns, start=0, limit=None):
        if start >= total:
            return
        groups = []
        offset = start
        stop = total if limit is None else start + limit
        preceding = 0
        for i in range(fragment.metadata.num_row_groups):
            n = fragment.metadata.row_group(i).num_rows
            if preceding >= stop:
                break
            if preceding + n > start:
                groups.append(i)
            else:
                offset -= n
            preceding += n
        selected = fragment.subset(row_group_ids=groups)
        batches = selected.to_batches(
            columns=columns,
            batch_size=512,
            batch_readahead=0,
            fragment_readahead=0,
            use_threads=False,
        )
        remaining = total - start if limit is None else limit
        for batch in batches:
            if batch.nbytes > 32 * 1024**2:
                raise OperationBlocked(QUOTA)
            amount = min(remaining, max(0, batch.num_rows - offset))
            subset = batch.slice(min(batch.num_rows, offset), amount)
            yield from zip(*(col.to_pylist() for col in subset.columns), strict=True)
            remaining -= amount
            if remaining == 0:
                return
            offset = max(0, offset - batch.num_rows)

    return Table(schema.names, rows, total)


def _project(names, sequence):
    def rows(columns):
        indexes = [names.index(name) for name in columns]
        for row in sequence:
            if not isinstance(row, (list, tuple)) or len(row) != len(names):
                raise ValueError("Invalid table row")
            yield tuple(row[i] for i in indexes)

    return Table(names, rows, None)


@contextmanager
def table_source(ctx, record, slot, until):
    _check_slot(record, slot)
    fmt = record["ref"]["format"]
    with verify_artifact_file(ctx, record, until=until) as stream:
        if fmt in {"h5ad", "h5mu"}:
            yield from _h5_reader(stream, slot)
            return
        table = _flat_table(stream, fmt, record, until)
        names = table.fields
        if not names or len(set(names)) != len(names):
            raise ValueError("Ambiguous table header")
        if any(map(clinical, names)):
            raise OperationBlocked(PRIVATE)
        if _gene_matrix(names):
            raise OperationBlocked(SCOPE)
        yield table


def _check_slot(record, slot):
    ref = record["ref"]
    fmt = ref["format"]
    detail = record.get("detail") or {}
    if slot in {"obs", "var"} and fmt not in {"h5ad", "h5mu"}:
        raise OperationBlocked(SCOPE)
    raw = (
        ref["kind"] == "expression_matrix"
        or detail.get("contains_expression_matrix")
        or ref["representation_class"]
        in {
            "raw_reads_or_droplets",
            "count_1_raw_counts",
            "ambient_adjusted_counts",
            "size_factor_normalized",
            "aggregated_unit_counts",
        }
    )
    if raw and slot not in {"obs", "var"}:
        raise OperationBlocked(SCOPE)
    if slot == "plot_sidecar" and ref["kind"] != "plot_sidecar":
        raise OperationBlocked(SCOPE)
    if fmt in {"h5ad", "h5mu"} and slot not in {"obs", "var"}:
        raise OperationBlocked(SCOPE)


def _flat_table(stream, fmt, record, until):
    if fmt == "parquet":
        return _arrow_reader(stream)
    if fmt == "json":
        from ._json_tables import reader

        return Table(*reader(stream, until))
    if fmt in {"csv", "tsv"}:
        records = table_rows(stream, Path(record["path"]), until)
        return _project(next(records, []), records)
    raise OperationBlocked(SCOPE)


def authorized_artifact(ctx, sid, aid, sha=None):
    ctx.require_public(sid)
    record = ctx.artifact(sid, aid, sha)
    ref = record["ref"]
    roots = [
        ref["sha256"],
        *filter(None, [record.get("detail", {}).get("source_sha256")]),
    ]
    ctx.check_embargo(roots)
    ctx.require_public(sid, digests=roots)
    if record["classification"] not in PUBLIC:
        raise OperationBlocked(PRIVATE)
    ctx.authority.daemon_read(lambda db: ctx.check_source(db, sid, ref["sha256"]))
    if record["status"] != "current":
        raise OperationBlocked(SCOPE)
    return record


def _filter_sql(index, filters):
    clauses = []
    params = []
    for predicate in filters:
        col = index[predicate["column"]]
        op, value = predicate["op"], predicate["value"]
        if op in {"eq", "neq"} and value is None:
            clauses.append(f"{col} IS " + ("NOT NULL" if op == "neq" else "NULL"))
        elif op == "in":
            if not isinstance(value, list) or len(value) > 256:
                raise ValueError("Membership filter needs a bounded array")
            clauses.append(f"{col} IN ({','.join('?' for _ in value)})")
            params.extend(_json_scalar(v) for v in value)
        elif op == "contains":
            if not isinstance(value, str):
                raise ValueError("Substring filter needs text")
            clauses.append(f"instr({col},?)>0")
            params.append(value)
        else:
            clauses.append(f"{col} {_OPERATORS[op]} ?")
            params.append(_json_scalar(value))
    return " AND ".join(clauses) or "1", params


def _requirements(names, args):
    grouping = args.get("aggregate")
    if grouping:
        groups = grouping.get("group_by", [])
        metrics = grouping["metrics"]
        outputs = [*groups, *(m["fn"] + "_" + m["column"] for m in metrics)]
        needed = [*groups, *(m["column"] for m in metrics)]
    else:
        outputs = args.get("columns") or names
        needed = [*outputs, *(o["column"] for o in args.get("order_by", []))]
    needed.extend(f["column"] for f in args.get("filters", []))
    if (
        len(outputs) != len(set(outputs))
        or len(outputs) > 32
        and not args.get("columns")
    ):
        raise OperationBlocked(QUOTA)
    if grouping and args.get("columns") and not set(args["columns"]) <= set(outputs):
        raise ValueError("Unknown aggregate output")
    if set(needed) - set(names):
        raise ValueError("Unknown source column")
    return list(dict.fromkeys(needed)), list(outputs)


def _grouped(index, aggregate, where):
    """Compute an exact disk-backed median using portable SQL window functions."""
    groups = [index[n] for n in aggregate["group_by"]]
    partition = "PARTITION BY " + ",".join(groups) if groups else ""
    terms = [*groups]
    windows = []
    for i, metric in enumerate(aggregate["metrics"]):
        col = index[metric["column"]]
        fn = metric["fn"]
        if fn == "median":
            windows.extend(
                [
                    f"row_number() OVER({partition} ORDER BY {col} IS NULL,{col}) r{i}",
                    f"count({col}) OVER({partition}) n{i}",
                ]
            )
            expr = f"avg(CASE WHEN r{i} IN ((n{i}+1)/2,(n{i}+2)/2) THEN {col} END)"
        else:
            sql_fn = "avg" if fn == "mean" else fn
            expr = f"{sql_fn}({col})"
        terms.append(expr)
    ranked = (
        "SELECT *" + "".join("," + w for w in windows) + f" FROM records WHERE {where}"
    )
    grouped_by = " GROUP BY " + ",".join(groups) if groups else ""
    select = ",".join(f"{expr} AS o{i}" for i, expr in enumerate(terms))
    return f"SELECT {select} FROM ({ranked}){grouped_by}"


def _validate_numeric_metrics(db, index, aggregate, where, params):
    numeric = {
        m["column"]
        for m in aggregate["metrics"]
        if m["fn"] in {"sum", "median", "mean"}
    }
    if not numeric:
        return
    bad = " OR ".join(
        f"typeof({index[n]}) NOT IN ('integer','real','null')" for n in numeric
    )
    if db.execute(
        f"SELECT 1 FROM records WHERE ({where}) AND ({bad}) LIMIT 1", params
    ).fetchone():
        raise ValueError("Numeric aggregation requires verified numeric cells")


def _execute_page(db, index, outputs, args):
    where, params = _filter_sql(index, args.get("filters", []))
    aggregate = args.get("aggregate")
    if aggregate:
        _validate_numeric_metrics(db, index, aggregate, where, params)
        source = _grouped(index, aggregate, where)
        aliases = {name: f"o{i}" for i, name in enumerate(outputs)}
        projected = args.get("columns") or outputs
    else:
        source = f"SELECT rowid AS seq,* FROM records WHERE {where}"
        aliases = index
        projected = outputs
    try:
        ordering = [
            aliases[o["column"]] + (" DESC" if o.get("descending") else " ASC")
            for o in args.get("order_by", [])
        ]
    except KeyError:
        raise ValueError("Unknown ordering column") from None
    ordering.extend(
        (aliases[n] for n in aggregate["group_by"]) if aggregate else ["seq"]
    )
    order = " ORDER BY " + ",".join(ordering) if ordering else ""
    count = db.execute(f"SELECT count(*) FROM ({source})", params).fetchone()[0]
    offset = args.get("offset", 0)
    if offset > count:
        return projected, count, ()
    columns = ",".join(aliases[n] for n in projected)
    cursor = db.execute(
        f"SELECT {columns} FROM ({source}){order} LIMIT ? OFFSET ?",
        (*params, args.get("limit", 50), offset),
    )
    return projected, count, cursor


def _spool(db, names, records, until):
    # Scratch lives in an automatically unlinked SQLite temporary file.
    db.execute("PRAGMA cache_size=-4096")
    db.execute("PRAGMA temp_store=FILE")
    db.set_progress_handler(lambda: int(time.monotonic() > until), 5000)
    index = {name: f"c{i}" for i, name in enumerate(names)}
    db.execute("CREATE TABLE records (" + ",".join(index.values()) + ")")
    placeholders = ",".join("?" for _ in names)
    for page in batched(records(names), 512):
        check_time(until)
        db.executemany(
            f"INSERT INTO records VALUES ({placeholders})",
            (tuple(map(_json_scalar, row)) for row in page),
        )
    return index


def _bounded_page(ref, columns, total, cursor, offset):
    page = {
        "artifact_id": ref["artifact_id"],
        "artifact_sha256": ref["sha256"],
        "columns": columns,
        "rows": [],
        "returned_rows": 0,
        "total_matching_rows": total,
        "next_offset": None,
        "truncated": False,
    }
    size = len(json.dumps(page, separators=(",", ":")).encode()) + 48
    for row in cursor:
        row = list(map(_json_scalar, row))
        size += (
            len(json.dumps(row, allow_nan=False, separators=(",", ":")).encode()) + 1
        )
        if size > schemas.PAYLOAD_BYTES:
            if not page["rows"]:
                raise OperationBlocked(QUOTA)
            break
        page["rows"].append(row)
    n = len(page["rows"])
    page.update(
        returned_rows=n,
        truncated=offset + n < total,
        next_offset=offset + n if offset + n < total else None,
    )
    return page


def query_artifact_table(ctx, args):
    until = deadline()
    sid = args["session_id"]
    record = authorized_artifact(ctx, sid, args["artifact_id"], args["artifact_sha256"])
    with table_source(ctx, record, args.get("table_slot", "default"), until) as table:
        result = _query_page(table, record["ref"], args, until)
    authorized_artifact(ctx, sid, args["artifact_id"], args["artifact_sha256"])
    return result


def _query_page(table, ref, args, until):
    wanted, outputs = _requirements(table.fields, args)
    if table.count is not None and not any(
        args.get(k) for k in ("order_by", "filters", "aggregate")
    ):
        offset = args.get("offset", 0)
        rows = (
            table.rows(outputs, offset, args.get("limit", 50))
            if offset < table.count
            else ()
        )
        return _bounded_page(ref, outputs, table.count, rows, offset)
    with closing(sqlite3.connect("")) as db:
        try:
            index = _spool(db, wanted, table.rows, until)
            columns, total, cursor = _execute_page(db, index, outputs, args)
            result = _bounded_page(ref, columns, total, cursor, args.get("offset", 0))
        except sqlite3.OperationalError as error:
            if getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT:
                raise OperationBlocked(QUOTA) from None
            raise
    return result
