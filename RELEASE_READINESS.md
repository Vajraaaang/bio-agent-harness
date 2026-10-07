# Latest research development snapshot — 2026-10-07

This update publishes the latest local source for `0.5.0.dev0`, including the scientific workbench, method contracts/planner, discovery evaluation support, and RNA tracing/qualification tooling. It is a draft review snapshot. Existing verified 0.4.17 release evidence remains historical evidence for that release, not qualification of this update.

Fresh controlled synthetic software verification passed for both continuous expression and counts: repeated numerical outputs were identical, audit checks passed, and the configured R references agreed. These checks do not establish comparative scientific validity, hosted-service qualification, or clinical readiness.

The broader software test run was bounded and stopped near 30% after sandbox failures/setup errors; it is incomplete and no full-suite pass is claimed. The first setup failure was a notebook kernel socket blocked by the sandbox. Rerunning `tests/test_analysis_bundle.py` with local socket/process permissions passed all three tests in 43.55 seconds. The remaining observed failures were not resolved or fully classified. No unrelated bugfixes are included in this source synchronization.

`git diff --cached --check` reported four existing local whitespace findings in `devbench/analysis/assets/workbench-ui.html`, `devbench/analysis/plotting.py`, `tests/test_biology_preparation.py`, and `tests/test_planted_discovery.py`. Their source bytes are preserved. The copied-file manifest passed independent path-exclusion checks and SHA-256 verification for all 779 copied files.

`PUBLICATION_MANIFEST.json` records SHA-256 identities for the copied local files and the original GitHub parent commit. Virtual environments, downloaded data/cache directories, local run directories, captured source trees, and integration validation outputs are excluded. Existing curated release evidence is retained.

## Historical 0.4.17 release readiness

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
