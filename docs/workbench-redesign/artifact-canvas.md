# Published artifact canvas

`workbench-artifacts.js` displays the actual descriptors supplied by the existing
scientific output publication boundary. It supplements the specialized native
G02/G08 renderer; it does not synthesize figures or create scientific outputs.

## Integration

```javascript
const controller = window.WorkbenchArtifactCanvas.mount(container, {
  artifacts: record.artifacts,
  request: path => authenticatedRequest(path),
  context: { run_id: record.id },
  mode: 'figures', // 'code' or 'evidence' are also supported
  onStatus: (message, error) => updateCanvasStatus(message, error)
});
// Destroy on run, canvas or tab changes to invalidate pending previews.
controller.destroy();
```

The module accepts at most 200 artifact descriptors. Each must have a bounded
name, content type and artifact type, a nonnegative safe integer byte count,
64-character hexadecimal artifact and SHA-256 identities, and the exact local
`/api/scientific/artifacts/<artifact_id>` download route. The authenticated
backend remains the authority for publication and scientific version ownership.
The module receives no token, model authority or ability to mutate backend state.

## Explicit preview and download behavior

Mounting never fetches any artifact. Every read follows an explicit Preview or
Download action. Large matrices, AnnData/H5AD, HDF5, loom, MTX, NPY/NPZ and raw
read archives have no preview. SVG and HTML are never executed or embedded.
Unsupported formats retain an explicit unavailable state and download action.
Code mode enables actual notebook/source previews and keeps all other published
files available through their download controls.

Preview reads consume a response stream with a hard budget before combining
chunks. Declared oversized responses are refused before reading; an oversized
chunk cancels the reader. Exact byte length and WebCrypto SHA-256 must match the
published descriptor before any bytes are displayed or downloaded.

| Content | Browser behavior | Limit |
|---|---|---|
| PNG, JPEG, WebP, GIF | Verify descriptor, hash and raster magic; render a local Blob URL | 8 MiB |
| CSV / TSV | Parse quoted delimiters, escaped quotes and quoted newlines; semantic table | 256 KiB; 100 columns; first 200 actual rows |
| JSON array rows | Reject duplicate keys, nonfinite numbers and inexact integers; display actual rows and explicit missing values | 256 KiB; 100 columns; first 200 actual rows |
| Other JSON record | Read-only text of the verified saved record | 256 KiB; depth and item-count budgets |
| IPYNB | Read-only actual code-cell sources; no notebook execution or outputs-as-code | 256 KiB; 150,000 visible characters |
| Plain text / source | Read-only saved text | 256 KiB; 150,000 visible characters |
| Explicit download | Stream and verify the full published file, then prepare a Blob download | 64 MiB |

Above 64 MiB the module directs the operator to the existing export or CLI rather
than buffering large files in browser memory. These display budgets do not widen
backend read, execution, publication or scientific review permissions. A prepared
browser download is not proof that the native Save dialog completed.

Tables state the exact total row count and the number displayed. Their visible
rows are the first 200 rows, not a scientific sample or aggregate. Missing JSON
fields are distinguished from actual `null`, `false`, zero and objects. CSV/TSV
text values are not coerced to numeric estimates. Malformed field counts and
unterminated quotes cause explicit refusal rather than silent correction.

## DOM, lifecycle and provenance

All file names, data, code and metadata enter the DOM through `textContent` or
normal DOM properties. No arbitrary HTML, SVG or executable notebook content is
inserted. Each preview shows its actual file name, byte count, SHA-256, artifact
identity and source run. The strict local route is the only request target.

A generation guard prevents a late response from replacing a newer selection or
destroyed canvas. Raster Blob URLs are revoked on the next selection and on
destroy. Prepared download URLs are revoked after activation or on destroy.

The parent theme owns `.wa-artifact-canvas`, `.wa-artifact-list`,
`.wa-artifact-row`, `.wa-artifact-heading`, `.wa-artifact-actions`,
`.wa-artifact-status`, `.wa-artifact-viewer`, `.wa-artifact-metadata`,
`.wa-artifact-image`, `.wa-table-scroll`, `.wa-artifact-table`,
`.wa-artifact-code`, `.wa-artifact-record` and `.wa-preview-unavailable`.
The only rendering permission needed beyond the existing application is its
already-enabled `img-src blob:`; no external requests or CSP relaxation is used.

## Verification

The dedicated pytest module runs the actual JavaScript in Node. Its **20 tests**
cover quoted CSV/TSV and CRLF, precise/missing JSON values, malformed or oversized
data refusal, exact local descriptor routes, stream cancellation, actual notebook
source limits, zero automatic requests, exact displayed values, stale-response
protection, URL cleanup, SHA mismatch, SVG masquerading as a raster, disabled
matrix previews and the 64 MiB download guard. A regression also verifies that an
actual JSON object resembling an internal missing-value marker remains visible
as its actual object value.

Fresh JavaScript syntax and these 20 tests pass. Root integration, packaging,
authenticated live-browser artifact access and the combined workbench suite are
verified separately. These software checks do not establish scientific validity.
