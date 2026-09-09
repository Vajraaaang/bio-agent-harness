# Operated native execution and recovery

The clinical-production objective remains open. This work implements the already identified durable execution gap and gathers a separate post-repair statistical matrix; neither substitutes for study-specific qualification or independently administered identities.

## Design and ownership

An explicit private local SQLite registry binds an idempotent request ID to the complete submission fingerprint, plan hash and canonical workspace. Append-only API events record registered, started, runtime intent, observed container, launch authorization, start request and a terminal outcome. An advisory nonblocking lease gives one cooperating local process ownership of dispatch/recovery. External checkpoints detect replacement of previously retained history; the local filesystem owner remains a trust boundary.

The native runtime records creation intent before Docker create and start intent before Docker start. New profile2 uses the pinned image's GNU timeout9.4 inside the container in addition to the coordinator watchdog. Profile1 records retain their original semantics. A hard-killed coordinator therefore does not remove the only timer; this requires real container verification, not just command inspection.

Recovery never writes into the original attempt or reruns its analysis. It uses a matching operation marker, signed plan/environment, registered daemon and fully inspected immutable container ID before cleanup. It records cleanup requests before mutation in a separate recovery DAG. An unacknowledged create with no observed CID remains unresolved when discovery is empty. A sealed completed run can restore a lost registry commit only after verifying the original saved receipt, native evidence and registration binding. Missing receipt, unsafe ownership or unreachable infrastructure cannot become success.

The independent statistical stage uses a verified frozen0.4.4 wheel and six prespecified cells,20 cohorts each, two native engines (240 signed analyses). Current recovery edits cannot change that measured runtime. Four null cells cross n24/48 and alpha.05/.8; two mixed cells use alpha.2 and explicit fixed alternatives. The broader36-cell/2000-cohort-per-cell target remains outstanding. No clinical equivalence margin is invented.

- Registry worker: run_registry.py and registry tests.
- Recovery worker: native_recovery.py and recovery tests.
- Coordinator: runtime/profile hooks, operated submission/CLI, real interruption checks and final evidence integration.
- Matrix worker: separate frozen protocol, generator, actual execution, independent checks and reporting.

## Execution plan

- [x] Inspect current behavior and identify crash windows with independent review.
- [x] Implement registry, idempotent submission, leases and separate recovery records; retain caller-asserted identity scope.
- [x] Correct reviewed start-order, duplicate exit-status and checkpoint-size defects; test failure paths.
- [x] Real SIGKILL, active lease refusal, orphan timer and lost-commit checks passed; the observer-bug first run is preserved. The240-run stage completed with all audits checked.
- [x] Tests and package/Colab verification completed (1261 local;737 Colab plus one platform skip). CHECKPOINT-final.json seals2148 files and references three external evidence roots; the full clinical-production goal remains open.

Primary operational references: [SQLite atomic commit](https://www.sqlite.org/atomiccommit.html), [GNU timeout](https://www.gnu.org/software/coreutils/manual/html_node/timeout-invocation.html). SQLite depends on correct local locking/filesystem/flush behavior; these choices do not qualify a deployed storage system or establish Part11 compliance.
