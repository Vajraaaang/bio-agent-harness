# Native controlled analysis integration plan

**Goal:** Connect the pinned native count procedures to signed, preserved and supervised execution without reinterpreting v1 Python plans, and execute independent qualification pilots.

**Architecture:** A separate native plan schema binds raw CSV/design bytes, ordered panel/sample/hypothesis identities, driver and package-source-lock hashes, immutable Docker image ID, resource bounds and host/runtime identity. A fixed local Unix-Docker profile creates a named non-root container with no network and read-only inputs/root filesystem, records inspected resource/exit/cleanup evidence, and retains all failed attempts. Native result/evidence verification has its own contract and never infers clinical qualification.

## Ownership and steps

- [x] NativeAnalysisPlan and strict tests: native_contracts.py / test_native_contracts.py (separate agent); old plans untouched.
- [x] Native result validator and adversarial tests: native_results.py / test_native_results.py;136 focused result tests passed.
- [x] Local container runtime identity/profile, create/start/inspect/cleanup supervision and tests: native_runtime.py. Independent reviewer counterexamples produced fixes and lifecycle regressions.
- [x] Signed native executor, driver/package-lock snapshotting, native evidence verification, CLI dispatch and integration tests. Both engine-specific RLE formulas are reconstructed from preserved counts;32 rehashed semantic negative controls reject.
- [x] Freeze and run 20-cohort fresh-seed native pilot; retain all40 attempts and independent integrity/statistical checks.
- [x] Diagnose DESeq2 optional native diagnostic serialization from the sealed pilot and repair only that encoding.21 regressions and an independent native comparison passed;778 original pilot files stayed unchanged.
- [x] Execute real signed native success/failure/repeat cases. Final-source seven-check integration passed; one additional execution from the built wheel matched the source-run statistics. All1103 tests passed, with two dependency warnings; eight historical Mac/Colab records still verify.
- [x] Sealed the verified evidence report and versioned artifact checkpoint: CHECKPOINT.json SHA-2560df3617fd4eb25bdf6bdd1b1749510fbd7d0acbd70b05ecf96136e24f544a542 covers2848 files. The three-page report was rendered and reviewed; wheel/sdist and both notebook bundles include the30 tested runtime/driver/lock files exactly. Current notebooks were structurally checked, not rerun in Colab. The old pilot's source preflight correctly rejects the repaired current driver; a separate saved-seal check matched all778 retained pilot files and all65 repair-evidence files.

The wider 2000-cohort-per-cell protocol and clinical/operational requirements remain in clinical-production-goal.md. Pilot success does not satisfy them. No inference threshold, estimator, cohort or previous answer is changed during a frozen measurement. Docker transport unit doubles are only infrastructure tests; reported native numerical results must come from real execution.
