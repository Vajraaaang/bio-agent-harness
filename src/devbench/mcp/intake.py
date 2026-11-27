"""Read-only input inspection and revision-fenced investigation admission."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from contextlib import contextmanager
from itertools import batched
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from devbench.core.count_forensics import CountDistribution
from devbench.engine.workflow_operations import OperationBlocked

from ._inspection import (
    CountInspection,
    axes,
    file_profile,
    mtx_profile,
    raw_declared,
    unassessed,
)
from ._sources import (
    BLOCK,
    PRIVATE,
    QUOTA,
    SCOPE,
    DirectoryInventory,
    canonical,
    check_time,
    clinical,
    deadline,
    pinned_file,
    scoped_path,
)

PUBLIC = {"public", "synthetic"}
_FILE_TYPES = {"csv", "tsv", "parquet", "h5ad", "h5mu", "json", "mtx"}
_KINDS = {
    "expression": "expression_matrix",
    "spatial": "expression_matrix",
    "metadata": "metadata",
    "manifest": "metadata",
    "reference": "reference",
}


def _known_private(root, entry):
    terms = (root / entry["path"]).parts
    return (
        entry.get("clinical_metadata", False)
        or entry.get("data_classification", "unclassified") not in PUBLIC
        or any(map(clinical, terms))
    )


def _file_format(path, hidden=False):
    fmt = path.with_suffix("").suffix if path.suffix == ".gz" else path.suffix
    fmt = fmt.casefold().removeprefix(".")
    if fmt not in _FILE_TYPES:
        if hidden:
            return "opaque"
        raise OperationBlocked("invalid_request")
    return fmt


def _store_format(tree, hidden=False):
    if tree.mtx_members:
        return "10x_mtx"
    if {"tables", "images", "points", "shapes", "labels"} & tree.top_names:
        return "spatialdata_zarr"
    if tree.metadata and {".zgroup", ".zattrs", "zarr.json"} & tree.top_names:
        return "zarr"
    if {"__tiledb_group.tdb", "__group", "__meta"} & tree.top_names:
        return "tiledbsoma"
    if hidden:
        return "opaque"
    raise ValueError("Unsupported directory store")


class _LocalArrayStore(Mapping):
    """Zarr opens only checked numeric arrays using descriptor-bound regular files."""

    def __init__(self, tree):
        self.tree = tree

    def __getitem__(self, key):
        if key in self.tree.metadata:
            return canonical(self.tree.metadata[key])
        if key not in self.tree.array_chunks:
            raise KeyError(key)
        path = scoped_path(self.tree.path, key)
        sha = self.tree.array_chunks[key]
        with pinned_file(path, self.tree.until, expected=sha) as (stream, _, size):
            if size > 32 * BLOCK:
                raise OperationBlocked(QUOTA)
            return stream.read()

    def __iter__(self):
        return iter(self.tree.array_chunks)

    def __len__(self):
        return len(self.tree.array_chunks)

    def __contains__(self, key):
        return key in self.tree.metadata or key in self.tree.array_chunks


def _safe_zarr(tree, path):
    import zarr

    meta = tree.metadata.get(f"{path}/.zarray")
    if meta is None:
        return None
    dtype = np.dtype(meta["dtype"])
    chunks = meta["chunks"]
    codecs = [meta.get("compressor"), *(meta.get("filters") or [])]
    if (
        dtype.kind not in "iuf"
        or dtype.hasobject
        or math.prod(chunks) * dtype.itemsize > 8 * BLOCK
        or any(
            c and c.get("id") not in {"blosc", "gzip", "zlib", "zstd", "lz4", "shuffle"}
            for c in codecs
        )
    ):
        return None
    store = zarr.storage.KVStore(_LocalArrayStore(tree))
    return zarr.open_array(store, path=path, mode="r")


def _zarr_axes(tree, inspect_layers):
    metadata = tree.metadata
    array_header = metadata.get("X/.zarray") or metadata.get("X/zarr.json") or {}
    attrs = metadata.get("X/.zattrs", {})
    shape = attrs.get("shape") or array_header.get("shape")
    if not shape or len(shape) != 2:
        shape = (None, None)

    def children(group):
        return sorted(
            {
                key.split("/")[1]
                for key in metadata
                if key.startswith(group + "/")
                and len(key.split("/")) > 2
                and not key.split("/")[1].startswith(".")
            }
        )

    def columns(name):
        record = metadata.get(name + "/.zattrs", {})
        return record.get(
            "column-order",
            [c for c in children(name) if c != record.get("_index", "_index")],
        )

    return axes(
        *shape,
        obs=columns("obs"),
        var=columns("var"),
        layers=children("layers") if inspect_layers else (),
        obsm=children("obsm"),
    )


def _zarr_forensics(tree, dimensions):
    n, m = dimensions["observations"], dimensions["features"]
    if not n or not m:
        return unassessed()
    matrix = _safe_zarr(tree, "X")
    if matrix is None:
        return _zarr_sparse_forensics(tree, n, m)
    counts = CountInspection(CountDistribution(total_values=n * m, major_width=m))
    stride = max(1, BLOCK // matrix.dtype.itemsize)
    height = max(1, stride // m)
    for start in range(0, n, height):
        for col in range(0, m, stride):
            check_time(tree.until)
            counts.observe(matrix[start : start + height, col : col + stride])
    return counts.report(declared_transform=_transformed_zarr(tree))


def _transformed_zarr(tree):
    from devbench.core.count_forensics import declares_count_transform

    attrs = tree.metadata.get("X/.zattrs", {})
    return any(
        k.startswith("uns/log1p/") for k in tree.metadata
    ) or declares_count_transform(
        attrs.get("representation"), attrs.get("transformation")
    )


def _zarr_sparse_forensics(tree, n, m):
    from devbench.platform.plugin_mcp import (
        _canonical_sparse_indices,
        _sparse_library_blocks,
    )

    encoding = tree.metadata.get("X/.zattrs", {}).get("encoding-type")
    if encoding not in {"csr_matrix", "csc_matrix"}:
        return unassessed()
    data, indices, pointers = (
        _safe_zarr(tree, "X/" + k) for k in ("data", "indices", "indptr")
    )
    if any(node is None for node in (data, indices, pointers)):
        return unassessed()
    rows = encoding == "csr_matrix"
    major, minor = (n, m) if rows else (m, n)
    if (
        data.ndim != 1
        or indices.shape != data.shape
        or pointers.shape != (major + 1,)
        or indices.dtype.kind not in "iu"
        or pointers.dtype.kind not in "iu"
        or pointers[0] != 0
        or pointers[-1] != data.size
    ):
        raise ValueError("Invalid sparse Zarr matrix")
    for pos in range(0, major, 65536):
        check_time(tree.until)
        p = pointers[pos : pos + 65537]
        if (p[1:] < p[:-1]).any() or p.min() < 0 or p.max() > data.size:
            raise ValueError("Invalid sparse Zarr pointers")
    if not _canonical_sparse_indices(indices, pointers):
        raise ValueError("Noncanonical sparse Zarr indices")
    probe = CountInspection(
        CountDistribution(total_values=n * m, major_width=minor, libraries_in_rows=rows)
    )
    probe.seen = n * m > data.size
    for start in range(0, data.size, 65536):
        check_time(tree.until)
        values = indices[start : start + 65536]
        if values.size and (values.min() < 0 or values.max() >= minor):
            raise ValueError("Invalid sparse Zarr coordinates")
    for block, libraries in _sparse_library_blocks(data, indices, pointers, rows):
        check_time(tree.until)
        probe.observe(block, libraries)
    return probe.report(declared_transform=_transformed_zarr(tree))


def _soma_profile(tree):
    # Open only locally inventoried frames, never a member URI supplied by a
    # collection. A store without a readable schema cannot be released as public.
    try:
        import tiledbsoma as soma
    except ImportError:
        raise OperationBlocked("runtime_prerequisite_missing") from None

    def frame(path):
        checked = scoped_path(tree.path, path)
        with soma.DataFrame.open(str(checked), mode="r") as data:
            return int(data.count), list(data.schema.names)

    n_obs, obs = frame("obs")
    measurements = scoped_path(tree.path, "ms")
    variables = []
    for member in measurements.iterdir():
        check_time(tree.until)
        if member.name.startswith("__") or not (member / "var").is_dir():
            continue
        variables.append(frame(member / "var"))
        if len(variables) > 128:
            raise OperationBlocked(QUOTA)
    if DirectoryInventory(tree.path, tree.until).sha256 != tree.sha256:
        raise OperationBlocked("plan_identity_mismatch")
    var = list(dict.fromkeys(name for _, columns in variables for name in columns))
    n_var = variables[0][0] if len(variables) == 1 else None
    return (
        axes(n_obs, n_var, obs=obs, var=var),
        unassessed(),
        any(map(clinical, [*obs, *var])),
        True,
    )


def _spatial_table(tree):
    tables = {
        key.split("/")[1]
        for key in tree.metadata
        if key.startswith("tables/")
        and len(key.split("/")) > 3
        and key.split("/")[2] == "X"
    }
    if len(tables) != 1:
        return None
    prefix = "tables/" + next(iter(tables)) + "/"
    return SimpleNamespace(
        path=tree.path / prefix,
        until=tree.until,
        array_chunks={
            k[len(prefix) :]: v
            for k, v in tree.array_chunks.items()
            if k.startswith(prefix)
        },
        metadata={
            k[len(prefix) :]: v
            for k, v in tree.metadata.items()
            if k.startswith(prefix)
        },
    )


def _directory_profile(tree, fmt, layers, hidden):
    if tree.clinical or hidden:
        return axes(), unassessed(), True, True
    if fmt == "10x_mtx":
        if len(tree.mtx_members) != 1:
            raise ValueError("Ambiguous 10x count matrix")
        name, sha = next(iter(tree.mtx_members.items()))
        path = tree.path / name
        with pinned_file(path, tree.until, expected=sha) as (stream, _, _):
            return mtx_profile(stream, path, tree.until)
    if fmt == "tiledbsoma":
        return _soma_profile(tree)
    if any(_sensitive_metadata(meta) for meta in tree.metadata.values()):
        return axes(), unassessed(), True, True
    if fmt == "spatialdata_zarr":
        tree = _spatial_table(tree)
        if tree is None:
            return (
                axes(),
                unassessed("Select one SpatialData expression table."),
                False,
                True,
            )
    dimensions = _zarr_axes(tree, layers)
    return dimensions, _zarr_forensics(tree, dimensions), False, True


def _sensitive_metadata(value):
    if isinstance(value, str):
        return clinical(value)
    if isinstance(value, list):
        return any(map(_sensitive_metadata, value))
    if isinstance(value, dict):
        return any(clinical(k) or _sensitive_metadata(v) for k, v in value.items())
    return False


def member_custody(ctx, digests, until, *, db=None):
    """Check every member digest in one WAL snapshot using bounded batches."""
    if db is None:
        return ctx.authority.daemon_read(
            lambda conn: member_custody(ctx, digests, until, db=conn)
        )
    protected = False
    for group in batched(digests, 256):
        check_time(until)
        ctx.check_embargo(group, db=db)
        if not protected:
            protected = ctx.protected(digests=group, db=db)
    return protected


def _input_path(ctx, value):
    path = Path(value)
    if not path.is_absolute() or path.is_relative_to(ctx.input_root):
        return scoped_path(ctx.input_root, path)
    if not ctx.config.get("allow_external_input_files") or path.is_relative_to(
        ctx.workspace
    ):
        raise OperationBlocked(SCOPE)
    # The operator opt-in admits only a specifically named, supported regular
    # file. It never grants directory discovery or access to the workspace.
    from devbench.analysis.workbench_inputs import InputDiscovery

    try:
        return InputDiscovery([ctx.input_root], allow_external_files=True).source_path(
            path
        )
    except (OSError, ValueError) as error:
        raise OperationBlocked(SCOPE) from error


def _inspect(ctx, entry, layers, until, *, admit):
    hidden = _known_private(ctx.input_root, entry)
    if hidden and not admit:
        raise OperationBlocked(PRIVATE)
    path = _input_path(ctx, entry["path"])
    members = ()
    if path.is_relative_to(ctx.input_root) and path.is_dir():
        tree = DirectoryInventory(path, until)
        sha = merkle = tree.sha256
        size = tree.size
        members = tuple(sorted(tree.member_sha256s))
        tainted = member_custody(ctx, members, until)
        hidden = hidden or tainted or ctx.protected(digests=(sha,))
        fmt = _store_format(tree, hidden or tree.clinical)
        details = _directory_profile(tree, fmt, layers, hidden)
    else:
        merkle = None
        with pinned_file(path, until) as (stream, sha, size):
            hidden = hidden or ctx.protected(digests=(sha,))
            fmt = _file_format(path, hidden)
            details = (
                (axes(), unassessed(), True, True)
                if hidden
                else file_profile(stream, path, fmt, entry["file_kind"], layers, until)
            )
    dimensions, counts, clinical_header, expression = details
    ctx.check_embargo((sha,))
    label = entry.get("data_classification", "unclassified")
    if (clinical_header or ctx.protected(digests=(sha,))) and label in PUBLIC:
        label = "private"
    if label not in PUBLIC and not admit:
        raise OperationBlocked(PRIVATE)
    return (
        {
            "canonical_path": str(path),
            "format": fmt,
            "size_bytes": size,
            "sha256": sha,
            "merkle_root_sha256": merkle,
            "axes": dimensions,
            "count_1_forensics": counts,
            "data_classification": label,
        },
        expression,
        members,
    )


def inspect_inputs(ctx, args):
    until = deadline()
    inspections = [
        _inspect(ctx, entry, args.get("inspect_layers", True), until, admit=False)
        for entry in args["inputs"]
    ]
    entries = [profile for profile, _, _ in inspections]
    for profile, _, members in inspections:
        if member_custody(ctx, (profile["sha256"], *members), until):
            raise OperationBlocked(PRIVATE)
    return {"inputs": entries}


def _manifest_ref(profile):
    # MTX has no ArtifactRef format token. Bind its native bytes via a JSON
    # manifest. Retain source_sha256 independently for taint and re-verification.
    manifest = {
        "format": "devbench.input-manifest/1",
        "source_format": profile["format"],
        "source_sha256": profile["sha256"],
        "source_size_bytes": profile["size_bytes"],
    }
    data = canonical(manifest)
    return hashlib.sha256(data).hexdigest(), len(data), manifest


def register_investigation_input(ctx, args):
    entry = args["input"]
    profile, expression, members = _inspect(ctx, entry, True, deadline(), admit=True)
    fmt, size, sha = profile["format"], profile["size_bytes"], profile["sha256"]
    classification = profile["data_classification"]
    checked = profile["count_1_forensics"]
    representation = args["representation_class"]
    detail = {
        "input": entry,
        "provenance_declaration": args["provenance_declaration"],
        "axes": profile["axes"],
        "count_1_forensics": checked,
        "source_format": fmt,
        "source_sha256": sha,
        "contains_expression_matrix": expression,
        "count_1_verified": (
            representation == "count_1_raw_counts"
            and raw_declared(
                args["provenance_declaration"].get("count_provenance"), checked
            )
        ),
    }
    if members:
        detail["member_sha256s"] = members
    if fmt in {"mtx", "10x_mtx", "opaque"}:
        sha, size, detail["manifest"] = _manifest_ref(profile)
        fmt = "json"
    kind = _KINDS[entry["file_kind"]]
    artifact = {
        "artifact_id": "a-" + sha,
        "sha256": sha,
        "size_bytes": size,
        "format": fmt,
        "kind": kind,
        "representation_class": representation,
        "claim_tier": "none",
    }
    if classification in PUBLIC:
        shape = profile["axes"]
        if shape["observations"] is not None and shape["features"] is not None:
            artifact["shape"] = [shape["observations"], shape["features"]]
        artifact["schema_preview"] = {
            "obs_columns": shape["obs_columns"],
            "var_columns": shape["var_columns"],
        }
    from .server import check_deadline

    check_deadline()
    return ctx.admit(
        args.get("session_id"),
        args.get("expected_revision"),
        artifact,
        Path(profile["canonical_path"]),
        classification,
        detail,
    )


def _artifact_path(ctx, record):
    path = Path(record["path"])
    if not record.get("producer_step_id"):
        return _input_path(ctx, path)
    if path.is_relative_to(ctx.input_root):
        return scoped_path(ctx.input_root, path)
    if record.get("producer_step_id") and path.is_relative_to(ctx.workspace):
        return scoped_path(ctx.workspace, path)
    raise OperationBlocked(SCOPE)


def _recheck(ctx, record, roots, until, allow_private):
    protected = member_custody(ctx, roots, until)
    if protected or record["classification"] not in PUBLIC:
        if not (allow_private and ctx.offline_only):
            raise OperationBlocked(PRIVATE)


@contextmanager
def verify_artifact_file(ctx, record, *, until=None, allow_private=False):
    until = deadline() if until is None else until
    path = _artifact_path(ctx, record)
    detail = record.get("detail") or {}
    expected = detail.get("source_sha256") or record["ref"]["sha256"]
    roots = (record["ref"]["sha256"], expected)
    _recheck(ctx, record, roots, until, allow_private)
    with pinned_file(path, until, expected=expected) as (stream, _, _):
        yield stream
    _recheck(ctx, record, roots, until, allow_private)


def verify_artifact_identity(ctx, record, *, until=None, allow_private=False):
    until = deadline() if until is None else until
    path = _artifact_path(ctx, record)
    if (
        path.is_relative_to(ctx.input_root) or path.is_relative_to(ctx.workspace)
    ) and path.is_dir():
        inventory = DirectoryInventory(path, until)
        expected = (
            record.get("detail", {}).get("source_sha256") or record["ref"]["sha256"]
        )
        if inventory.sha256 != expected:
            raise OperationBlocked("plan_identity_mismatch")
        _recheck(
            ctx,
            record,
            (inventory.sha256, *inventory.member_sha256s),
            until,
            allow_private,
        )
        return
    with verify_artifact_file(ctx, record, until=until, allow_private=allow_private):
        pass
