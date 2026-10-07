# Extensible scientific methods

The harness now supports immutable custom method versions through the same Step, Run and Proposal service as its existing registered tools. A successful run creates an exploratory result. Accepting that result is a separate operation that advances a specific analysis revision.

This implements the backend MVP of the supplied extensibility design. Existing notebook, MCP, ToolDispatcher and workbench commands retain their interfaces. The new entry point is `devbench-analyze methods`; the equivalent Python API is `devbench.analysis.methods.MethodService`.

## What is implemented

| Object | Identity and behavior |
|---|---|
| MethodContract | Immutable finite JSON; SHA-256 version identity; parameter schema, input/output types, source and environment identities, resource and validation policies |
| Registered method | Adapter over a real ToolDispatcher tool; exact current source inventory and dependency/runtime fingerprint checked before execution |
| Custom method | Single-file Python, R or CLI implementation; unreviewed; executed in a locally installed OCI image pinned by repository digest |
| Artifact | Bounded single-file immutable snapshot; content-addressed bytes, file-format suffix, type and generating run preserved separately |
| Analysis revision | Immutable accepted steps, parent revision and review record |
| Branch | Exploratory steps tied to an existing revision |
| Run | Fresh attempt, retained source/inputs/environment, parameters, execution logs, validation, output identities and existing ExecutionDAG evidence |
| Proposal | Scientific preview plus all unapproved upstream steps and their previews; no automatic acceptance |
| Approval | Transactional comparison against the current revision; creates a new revision or rejects a stale proposal |

Repeated output bytes can share storage while having different artifact identities and generating runs. Reproduction reports whether output SHA-256 values match. It does not overwrite the first run or turn a mismatching repeat into a success claim about reproducibility.

Implementation trust and scientific acceptance remain separate. Approving an analysis never changes a custom method's `unreviewed` implementation label. A future promoted implementation must have a new version, leaving earlier runs unchanged.

## Authoring a method

Use `MethodContract.from_dict()` to validate the finite JSON contract before registration. Required top-level fields are `format`, `method_id`, `name`, `lifecycle`, `implementation_trust`, `inputs`, `parameters`, `outputs`, `implementation`, `environment`, `execution`, `validation` and `provenance`. The format is `devharness.method/1`.

- Parameter schemas use Draft 2020-12 and a closed root object. References must be local JSON pointers. Schema defaults are annotations and are not silently inserted into the recorded parameters.
- Inputs declare a name, artifact type and required flag. Artifact types are `file`, `json`, `table` and `anndata`.
- Outputs declare distinct absolute container paths directly under `/outputs`, their type and whether they are required. Undeclared files, directories, links, missing required files and nonfinite JSON are rejected.
- Custom implementations declare an argument-vector entrypoint and the SHA-256 of their source. The source is mounted as `/method/main.py`; the name is fixed even when an R or shell interpreter is used.
- Custom environments declare `image` as `repository@sha256:<digest>` and `lock_sha256` for a retained environment lock artifact. The service neither resolves dependencies nor builds or pulls images.
- Parameters are available at `/method/parameters.json`; input artifacts are mounted at `/inputs/<binding-name>`; the retained environment lock is `/method/environment.lock`.
- Execution requests are bounded to 16 CPUs, 65,536 MiB memory, 3,600 seconds, 1,024 processes and 100,000,000 output bytes. Network mode is `deny`.

The standard-library example in `examples/custom_methods/mad_filter.py` demonstrates a previously unknown method without a product-specific implementation. Its semantics are explicit: per-sample raw MAD, upper-tail filtering, strict greater-than comparison and removal when any selected metric exceeds its cutoff. Missing/nonfinite metrics are rejected. When MAD is zero, values above the median are removed. Those choices are examples to review, not an endorsed scientific default.

`scripts/verify_extensible_methods.py` creates a complete example contract and runs a synthetic fixture through the public service. Its output includes the exact `method.json`, source/environment hashes, run records and verification summary.

## Running and accepting

All filesystem arguments must be absolute. For example, with your own workspace and authored files:

```sh
devbench-analyze methods --workspace /analysis/method-workspace --actor researcher register \
  --contract /analysis/custom/method.json --source /analysis/custom/main.py \
  --environment-lock /analysis/custom/environment.lock

devbench-analyze methods --workspace /analysis/method-workspace --actor researcher import-artifact \
  --source /analysis/input/dataset.h5ad --artifact-type anndata

devbench-analyze methods --workspace /analysis/method-workspace --actor researcher create-analysis \
  --name "QC alternatives"
```

Use the returned identifiers with `branch --analysis --revision`, then `add-step --branch --method --inputs --parameters`. The last two options name absolute JSON files containing artifact bindings and parameter values. `run --step` executes the step. `preview --run` shows validation and observed changes; `provenance --run` verifies the retained execution DAG.

`propose --run --rationale` records a candidate. `approve --proposal --rationale` commits it; `--acknowledge-warnings` is required when any included preview contains warnings. `reject --proposal --rationale` leaves the accepted plan untouched. Execution never calls either review operation automatically.

When a candidate consumes another unapproved run on its branch, the proposal includes that complete dependency chain. Approval checks all associated previews and evidence and accepts the chain atomically. An artifact from another branch must already belong to the accepted base revision before it can be used directly. A stale proposal requires a new branch/review against the current revision; it is never silently rebased.

Other commands are `register-tool --tool`, `analysis --id`, `show --kind --id`, `lineage --artifact`, `reproduce --run`, `audit` and explicit `recover`. Read commands do not initialize or reconcile workers. `recover` reconciles abandoned coordinator leases as interrupted attempts and attempts to remove their named containers; unsuccessful cleanup is recorded explicitly. Programmatic read access uses `MethodService(path, readonly=True)`; legacy writer construction retains its default reconciliation unless `reconcile=False` is supplied.

The [shared scientific workspace](shared-scientific-workspace.md) binds this store to one stable local project through an explicit schema upgrade. Accepted method revisions remain history; new immutable scientific execution definitions use separate AnalysisVersion records. Browser acceptance and the existing method-head update share one transaction.

## Scientific validation

The `single_cell_qc` comparator requires named, required AnnData `dataset` inputs and outputs. It reports shape, removed/added cell IDs, per-sample retention, QC quantiles, metadata and variable changes, layer changes and embedding changes. It validates declared observation uniqueness, retained-ID subset, unchanged variable order and unchanged retained count values. Negative/nonfinite counts are rejected. Empty populations and imbalanced retention require review.

The comparison uses the declared `counts_layer`, optional `sample_key` and explicit `qc_metrics`. HDF5 structure and decoded array sizes are checked before materialization; the service runs scientific comparison in a worker with a 1,024 MiB and 60-second limit. The initial bounds are 100 MB per artifact, five million stored array elements and 64 MiB of estimated decoded array storage. HDF5 external links, virtual/external datasets and repeated object links are rejected.

The `json` and `file` comparators provide structural and content-identity evidence only, and say so in their warnings. They do not imply that an arbitrary scientific transformation preserves cell identities or count values.

## Execution and evidence boundaries

The local Docker executor uses a read-only root filesystem, read-only source/input mounts, a non-root UID, no network, dropped capabilities, no-new-privileges, bounded tmpfs outputs/scratch and CPU/memory/PID/time/log limits. Custom processes start with an explicit environment rather than host environment variables. Image-declared volumes are replaced by bounded tmpfs mounts; no Docker socket or writable host directory is mounted.

Outputs are collected through a bounded `tar` stream and manually checked for regular files, paths, duplicates and size before publication. This uses Docker's documented [tmpfs copy workaround](https://docs.docker.com/reference/cli/docker/container/cp/#corner-cases). The underlying isolation options are described in the [Docker container reference](https://docs.docker.com/reference/cli/docker/container/run/). Containers are removed after collection or failure. Failed artifacts cannot be proposed; errors and bounded available logs remain evidence.

Registered tools use the existing trusted dispatcher and its supervised workers. This compatibility path does not claim container isolation for trusted Python code. Python executor injection is a trusted coordinator/test seam; the CLI exposes only the real Docker backend for custom methods.

The SQLite audit chain and ExecutionDAG are tamper-evident against independently retained root hashes, not WORM storage. Actor labels and local research approvals are caller assertions, not authenticated electronic signatures. This single-user implementation does not establish clinical validation or qualify a shared hostile-code service.

## Remaining roadmap

CellBender and DoubletFinder are absent from this repository. Their motivating three-method scenario is not claimed as a completed run; future real adapters can use this interface. The current registered-method adapter is grounded in the tools that actually exist.

The first artifact implementation handles files, not Zarr directories, chunk stores or unrestricted large datasets. Existing direct tool APIs retain their broader supported formats. The subsequent [scientific-method planner](scientific-method-planner.md) adds scientific selection before capability resolution, controlled trials and experimental recipe saving. Environment builders, remote/HPC execution, a conversational method-authoring UI, promotion CI, authenticated role-based approval, signed releases, gVisor/microVM isolation and workflow/export adapters remain separate roadmap work.

Verification evidence is retained in `reports/extensible-methods/2026-09-27/README.md`.
