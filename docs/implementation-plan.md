# Devharness implementation plan

The supplied design is the implementation specification. Build directly in the empty repository.

- [x] Core: chunked CSV/Zarr/HDF5 stores; supervised spawned workers; compact temporal manifolds. Test lazy reads, compression, child termination, SVD and small-data limits.
- [x] Tools: spawned QR/SVD rank validation and spline Gaussian/negative-binomial GLMs; numeric stage ordering; direct dispatcher and identical MCP registrations. Test alignment, collinearity, continuous data and BH adjustment.
- [x] Evaluation: deterministic leaf/composite graders, schema validation, workspace staging and captured tool evidence. Test malformed outputs, gates/vetoes, score normalization and isolation.
- [x] Benchmarks: seeded synthetic datasets, confounded/reversed metadata, bounded processed-table ingestion with offline fallback. Test reproducibility, streaming conversion and fallback provenance.
- [x] Experiments: pluggable agent sweeps and Markdown metrics with honest missing-cost handling. Exercise full offline command line flow.
- [x] Integration: run full pytest, Python 3.10 validation, package build/install and MCP discovery/call checks; resolve defects before handoff.

Decisions: monotonicity uses the given signed formula with range [-1,1]; singular condition numbers are represented as null plus rank state at JSON boundaries; integer-valued nonnegative matrices use negative-binomial GLMs, other finite matrices use Gaussian GLMs; spline constraints center the basis so its constant direction does not duplicate the intercept; process isolation protects notebook kernels but is not a hostile-code security boundary. Fixture grading follows the requested Latch-style fields with documented local extensions for captured tool evidence.

The updated Codex CLI requirement is implemented through adapter classes, staged guidance, strict final_discovery.json schemas, CLI-authentication preflight and captured MCP events. Corrected the supplied CLI flags against the installed help. Final verification is recorded in validation.md.
