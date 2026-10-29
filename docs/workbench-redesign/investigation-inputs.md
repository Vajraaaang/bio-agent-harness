# Read-only discovery and dataset profiles

`devbench.analysis.workbench_inputs.InputDiscovery` supplies the optional input
discovery boundary for the investigation composer. It receives only the service's
already configured input roots. Request payloads cannot add roots, directories,
parser code, model access or analysis settings.

## Public API

```python
discovery = InputDiscovery(input_roots, memory_mb=512, timeout_seconds=20)
listing = discovery.list()
canonical_source = discovery.source_path(source)
profile = discovery.inspect(source)
```

All filesystem paths must be absolute and canonical. The module accepts `.h5ad`,
`.json`, `.csv` and `.tsv` regular files. It does not upload, copy, edit, normalize,
fit or execute the supplied data. Hashing reads original bytes sequentially;
header inspection never materializes expression values into pandas or AnnData.

`source_path(source)` is the investigation registration boundary for all four
supported extensions. It returns a `Path` only after descriptor-based regular
file admission, configured-root containment, file-size bounds and identity
revalidation. It neither hashes nor parses content. It does not widen the legacy
H5AD-only execution reader; JSON and table scientific workflows still require
their own explicit supported method and content inspection before dispatch.

`list()` returns `devbench.workbench-inputs/1` with:

- `inputs`: canonical `source`/`path`, `filename`, `size_bytes`, `sha256` and
  `hash_status`. A validated cached profile appears as `profile` when available.
- `hash_status: ready` when a stat-matching cached or freshly computed digest is
  available. `pending_budget` with `sha256: null` when the listing's cumulative
  hashing budget or file-size ceiling prevents hashing. This is not a fake hash.
- `truncated` when file/entry limits stop the traversal, plus explicit `limits`.

`inspect(source)` returns `devbench.dataset-profile/1` with the exact file
identity, filename and byte count, then separates the scientific evidence:

| Field | Meaning |
| --- | --- |
| `observed` | Header observations only. H5AD includes actual matrix/header dimensions, `obs_columns`, `var_columns`, `layers` and `obsm_keys`. `expression_values_read` is false. |
| `declarations` | Empty object. No assay, raw-count, capture identity or annotation declaration is invented from the extension, a field name or a successful read. |
| `inferred.candidate_columns` | Name-based suggestions under `batch`, `capture_unit`, and `donor`. The exact observed column names are retained. These suggestions never confirm capture or biological identity. |
| `unknowns` | `{field, reason, blocking}` records for assay, measurement, count provenance, capture-unit identity, annotation provenance, design and contrasts. |
| `inspection` | Trusted supervised header worker, verified cleanup, no analysis execution and zero model sessions. |

CSV/TSV profiles report bounded column names and whether names are unique. They
do not count rows as cells or independent biological samples. Small JSON files
report top-level keys and a bounded supplied `format` label without promoting
these declarations into an assay or matrix contract. JSON over the header byte
budget is still hashed when allowed but explicitly returns
`header_status: not_inspected_byte_budget` rather than parsing a partial document.

## Resource bounds

Defaults are depth 2, 64 discovered files, 512 inspected directory entries,
32 MiB cumulative hashing per listing, 4 GiB per explicitly inspected file,
256 KiB table/JSON headers, 128 named columns/collections, a 20-second worker
deadline and 512 MiB worker memory. Constructor validation caps these limits at
depth 4, 256 files, 4,096 entries, 64 MiB list hashing, 16 GiB per input,
1 MiB headers, 256 columns, 60 seconds and 1,024 MiB. The service should pass
resource limits no wider than its own frozen profile.

Full-file hashing is streaming and bounded; it is not expression analysis.
HDF5 header inspection reads dimensions and bounded attributes/name collections,
including sparse `X` shape metadata. It does not read `X`, sparse values or index
arrays, layer values, embeddings, observation values, feature values or `uns`.
Dataset/header dimensional consistency is checked, but data types, counts,
normalization, donor nesting, contrasts and numerical quality remain unverified.

The existing `run_sandboxed` supervisor runs a trusted importable module-level
worker. It supplies time, memory, cancellation and cleanup observation. As the
supervisor's own contract states, this is resource isolation, not an OS security
boundary for arbitrary untrusted Python. No user-provided code is executed.

## Containment, races and caching

Every path component is opened descriptor-relatively with `O_NOFOLLOW`; files
also use `O_NONBLOCK` and are checked as regular files before reading. Traversal
uses `stat(..., follow_symlinks=False)` and never enters symbolic directories.
Pipes, devices and unsupported extensions are omitted from discovery and denied
by inspection. HDF5 external/symbolic links in inspected objects are rejected
without following them.

Configured roots are bound to their device/inode identity. Replacing a root does
not quietly expand the service's input authority; it requires a reviewed restart.
File caches are bounded and keyed by device, inode, mode, size, nanosecond mtime
and nanosecond ctime. Each access reopens the authorized path and revalidates its
signature. Hashing checks actual byte count and pre/post signatures. The worker
checks the expected identity before and after hashing/header reads; the parent
revalidates the path before publishing or caching the profile. Replacement or
mutation during these windows fails closed. Unverified worker cleanup prevents
profile publication and caching.

These checks report the identity observed during the bounded read. A later
analysis must still revalidate its expected digest and declarations through the
existing proposal and execution admission contracts. Discovery is not a grant,
an authenticated experimental declaration, a scientific review or a promise that
a registered method supports the file.

## Verification

The targeted suite in `tests/test_workbench_inputs.py` verifies regular-file
discovery, canonical paths, escape/symlink/FIFO refusal, root replacement,
depth/file/hash/header limits, cached-digest invalidation, mutation/replacement
races, absent cleanup, HDF5 external-link rejection, sparse header handling,
and supervised workers. A real HDF5 worker inspects a 50,000 × 20,000 logical
matrix with an unallocated expression dataset: the file remains below 100 KiB
and only headers are read. Other checks replace every HDF5 dataset-value read
with a failing sentinel. This establishes software controls, not RNA biological
validity or scientific qualification.
