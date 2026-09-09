# Controlled evidence verification 0.4.3

This remediation closes the reproduced structural-only verification gap. It is one completed workstream within the active clinical-production goal; the platform is not clinically qualified.

`devharness-controlled verify` now requires the expected root receipt and explicitly supplied signer registry. It checks recorded event order/times/parents, approvals at both their recorded verification time and execution launch, archived registry consistency, plan and input hashes, preserved source/environment identity, model invocation, complete accepted-result structure and numerical invariants. Coefficients, effects, uncertainty, count normalization, convergence, diagnostics, model/reference distributions and BH adjustment are validated. It reads preserved CAS objects rather than loose mutable run files.

`verify-integrity` retains the generic hash-only primitive. A valid failed controlled attempt has `analysis_completed=false`; an empty or completion-only ledger is invalid for controlled verification. Historical approval verification does not renew permission for another run. Human identity, trusted time, external retention and host attestation remain separate controls.

Evidence in `results/controlled-verifier-2026-09-07/`:

- `final-tests.log` / XML: **709 passed**, two upstream Authlib warnings, 335.46 seconds.
- `reviewer-green.log`: 73 focused verification/execution tests, including 48 verifier tests.
- `prior-negative-controls-after.json`: both original bad histories are rejected by `verify` and accepted only as hash-consistent by `verify-integrity`.
- `reviewer-probes-after.json`: six validly hashed independent counterexamples now fail for their intended semantic reasons. The first malformed reviewer prototype still fails structural integrity.
- `historical-semantic-final.json`: eight retained genuine Mac/Colab records pass the stronger checks without alteration.
- `final-numerical/`: four new real numerical executions pass evidence checks, repeat exactly and retain the existing conditional R arithmetic comparisons. This does not resolve the earlier NB calibration concern.
- `dist/`: version 0.4.3 Python wheel/source distribution. The wheel was checked against current implementation files; regenerated notebook cells compile. This revision's notebook has not yet been executed in Colab.

The native statistical workstream is underway separately. The official Bioconductor image is pinned by digest, R4.6.1/Bioconductor3.23.1 were observed in a network-disabled inspection, and 49 public source packages (52,720,099 bytes) have verified hashes. The derived runtime built offline and both native candidates completed a functional smoke. It is not a calibrated or clinically qualified backend yet. The failed build, first smoke and corrections are retained; signed-plan integration and independent validation remain.

The full objective remains open: native inference validation, durable interruption recovery, independently administered identity and data governance, reproducible deployment/retention, approved assay/context and independent clinical/quality evidence still require work. See clinical-production-goal.md for the complete scope.
