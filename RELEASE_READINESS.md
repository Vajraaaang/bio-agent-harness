# Release readiness and known issues

This is the verified **0.4.17 research software snapshot**. It is not qualified for clinical production, patient use, GxP compliance or regulatory biomarker validation.

## Verification retained

- Mac full suite: 2,014 passed, two platform skips.
- Noneditable installed wheel: 126 passed.
- Actual Colab: 1,542 passed, one platform skip; four controlled numerical analyses and verified exports.
- Final primary script controls: four expected passes and two deliberately failed scripts; all six receipts and restored answers checked.
- No new live-model performance claim. The earlier eight-run Codex pilot remains unsuccessful.

## Unresolved findings discovered after this snapshot

Development was stopped for this delivery before these findings were patched. The evidence is provided rather than presenting the snapshot as production ready.

1. **Controlled input capture:** the controlled Python and native executors bound the first snapshot copy but not subsequent CAS ingestion. A synthetic metadata file grew beyond the configured 8 MiB limit and was copied into the CAS. Python reached an instrumented worker-launch boundary; no real numerical execution occurred. Both paths returned failed outcomes; Python semantic verification failed, while the native failed-prefix record passed its applicable checks.
2. **Recovery inventory binding:** three synthetic pre-runtime cases changed private snapshot content, byte size or member count after inventory creation. Recovery committed `started` to `interrupted` despite inventory/reference disagreement. Generic DAG hash verification passed. No Docker, numerical or model execution occurred in those probes.
3. **Statistical qualification:** known native count-interval undercoverage and unequal-variance Gaussian miscalibration remain unresolved. Do not interpret algebraic gates or successful software tests as complete-pipeline calibration.
4. **Clinical and operational qualification:** intended assay/context, representative independent validation, administered human identities, independent quality approval, qualified deployment/recovery and protected retention remain absent or unqualified.

Raw retained reports are in `reports/`. Release assets contain the verified source archive, wheel, Colab export and curated execution evidence. Source code in this repository matches the tested source archive; this readiness note and publication metadata are additional documentation.

The Colab notebook uses its embedded, hashed source snapshot. Its scope is identified in the report; it does not become a validation of later edits merely because they share a version label.
