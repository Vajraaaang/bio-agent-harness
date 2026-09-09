# Runtime Version Identity Implementation Plan

**Goal:** Prevent numerical replay from reporting a matching runtime descriptor when a previously unrecorded Python dependency's version metadata changed.

**Evidence:** The preserved 0.4.15 capture lists eight packages and omits direct design/validation/storage/supervision dependencies. A synthetic Patsy metadata change left the old runtime matcher green. The corrected regression set failed 15 cases against the installed old package, while one unchanged-runtime control passed. No installed package or scientific input was modified by that fault injection.

**Design:** Keep the existing eight-key package subset for callers, and add a deterministically ordered census of metadata-visible installed Python distribution names/versions. Normalize names, collapse identical duplicates, reject conflicting versions or malformed/missing identity, and bound discovery. The complete descriptor is already compared before restoration/replay. Record an explicit scope: version metadata is not installed-file or native-library attestation. This is not a lockfile, complete loaded-module inventory, or qualification of the current verifier environment.

**Implementation ownership:** Coordinator owns runner/audit CLI changes, tests, integration and evidence; independent review operates read-only. Preserve all earlier archives, 66 definitions, scientific estimators and grading thresholds.

- [x] Save baseline source and reproduce unobserved metadata drift.
- [x] Add failing replay-admission, malformed/ambiguous identity, name-normalization and bounded-discovery tests.
- [x] Implement the census and explicit replay scope; retain original test-double failure and correction.
- [x] Independently review the frozen changes and validate current real-environment capture.
- [x] Freeze and execute real numerical tool capture/replay, with a separate metadata-drift refusal control.
- [x] Run the full suite and isolated installed-package checks; regenerate and validate both notebook bundles.
- [x] Update versioned evidence and requirement wording. Do not relabel prior Colab outputs as testing this new source.
- [ ] Seal and verify this checkpoint against separate receipts.

The full clinical-production goal remains active. Statistical calibration, assay/context, governed identity, quality authority, deployment and protected retention are still not qualified. GitHub login was reported by the user; no repository destination or publication action is inferred. No runtime deletion is authorized by reconnecting.
