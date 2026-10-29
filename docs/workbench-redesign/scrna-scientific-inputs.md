# AnnData input bindings for scientific planning

The scientific planner now accepts a genuine AnnData RNA binding through the existing `create_plan.rna_input_contract` field. This is a typed header-audit adapter for the existing mandatory `SerialRNAWorkflow`, not a fabricated bulk input record or a second unreviewed workflow.

```json
{
  "format": "devbench.scrna-input-binding/1",
  "anndata_artifact_id": "exact registered anndata artifact identity",
  "declarations": {
    "assay": "scrna",
    "measurement": "umi_counts",
    "count_provenance": "Source quantification and preprocessing records",
    "counts_layer": "X",
    "capture_unit_key": "capture",
    "capture_units_confirmed": true,
    "annotation_provenance": "Annotation source or explicit unavailability",
    "biological_independence": null
  }
}
```

Supported declared assays are `scrna` and `snrna`; declared measurement types are `umi_counts`, `non_umi_counts` and `normalized_expression`. These are supplied declarations, not experimental facts inferred from an extension or integer-looking array. The storage layer must be explicitly declared as `X` or one of the actual recorded layer names. The capture-unit key must be an actual observation-column name, confirmed by source records. A batch-looking or donor-looking name is never automatically accepted as independent biological identity.

An optional biological-independence declaration must have exactly `independent` (boolean), `unit_column` (actual observation column) and `evidence` (nonempty source record). It is retained as a supplied declaration. This header-only adapter does not verify its truth, read unit identities, count donors, or mark biological units independent.

The adapter resolves the registered artifact through `MethodService.artifact_path`, verifies its content/manifest hash and `anndata` type, then performs supervised HDF5 header inspection in an importable resource-limited worker. It rechecks content identity afterward. Files are limited to the artifact service's existing 100 MB ceiling, headers to 128 names per collection and 64 KB of returned metadata, and inspection to the existing bounded memory/time controls. These are planning-adapter ceilings; the separate backed scRNA workflow retains its own interface and limits.

Observed results include dimensions, observation/feature column names, layers and embedding-key names. Byte hashing covers the file but does not decode expression values. The adapter does not read expression arrays, annotation values or cell/gene ID values, load a matrix into pandas, fit anything, change normalization or convert continuous expression into counts. It rejects symbolic/external HDF5 links and external/virtual dataset storage so a registered file cannot silently depend on unregistered external bytes.

The resulting `devbench.scrna-input/1` record separates observed headers, supplied declarations, inferences (none added), biological-unit limitations and named unknowns. The planner binds it as `dataset_summary.observed.scrna_input`. It does not inject `bulk_rna_input`. Original supplied claims about numeric validity, ID uniqueness or independence are retained as unverified metadata rather than elevated into coordinator observations.

Header integrity establishes only `input_integrity_valid`. Numeric finiteness/count validity, observation-ID uniqueness, assay semantics, biological independence, design identifiability and exclusion authority remain unassessed. The existing prerequisite-inspection hook reverifies this header binding without invoking an expression-reading observation adapter. A candidate that requires unknown scientific or numeric prerequisites remains blocked; a registered method is not made appropriate merely by being available.

`WorkflowActions._audit` verifies this exact registered AnnData record and then uses the unchanged route: audit, question, discovery, candidate generation and scientific review, capability resolution, implementation assessment, trial design/review, and the mandatory operator gate. Unsupported alternatives remain unavailable. This adapter adds no scientific computation, execution grant, method implementation, preferred parameter or claim of scientific validity. Any later data execution still requires exact trial admission, operator selection and fresh permissions.

Bulk bindings continue through their original inspection and audit branches. AnnData and bulk inspection records cannot be mixed or injected as client-authored observed metadata without a typed registered binding.

Tests in `tests/test_rna_scrna_input_contract.py` use actual retained H5AD files and the real graph. They cover header/declaration separation, duplicate IDs remaining unverified, unchanged bytes, invalid layers/columns/provenance, malformed declarations, artifact tampering, external links, declared normalized representation without conversion, unverified independence, numeric-claim rejection and unavailable-method preservation at the actual review interrupt. The duplicate-ID fixture intentionally produces the library's warning; no IDs are repaired.
