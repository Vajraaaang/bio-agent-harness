# M0-5 DE calibration

## Frozen experiment

The unit of null-FDR analysis is one independently sampled cohort. A single BH discovery at q ≤ 0.05 counts as a global-null rejection. The historical panel at `ad1d7472` used RLE, an overall normalized mean of at least one, and v4 QL with robust moderation and an abundance trend already enabled. It produced 39 rejections in 400 independent cohorts and 38 in 600 paired cohorts. The historical generator gives donors gene-specific Gaussian effects on the log scale in **both** designs. A fixed donor block absorbs the shared effect in pairs. The pooled contrast alone cannot separate heterogeneity, design and panel scale.

For the diagnostic we fixed 20 distinct donors (10 per arm), a balanced observed batch covariate, gene baselines centered at 45, historical library/batch variation, and NB dispersion uniform on [0.04, 0.12]. We varied the gene-specific donor SD (0 or 0.30) and feature count (192 or 10,000). Here “pure NB” means NB conditional on the design, gene parameters and library exposure. Each development cell has 100 null and 20 independently seeded planted cohorts. All solvers see the same matrix within a cohort. The planted fraction is one eighth at both scales, alternating threefold decrease/increase. Recall uses every planted gene, including filtered ones. Direction error uses discovered planted genes as its denominator.

The 16 edgeR arms cross robust moderation, abundance trend, full-family TMM versus median-ratio RLE, and design-based filterByExpr versus mean normalized count ≥1. Full-family library offsets are retained on subsetting. Both voom arms use robust eBayes. The DESeq2 Wald comparison uses median ratios, the common pre-fit expression filter and no post-fit independent filtering or Cook's cutoff. Its default parametric dispersion fit can switch to local regression using the package's own documented behavior. With one profile per donor the variancePartition comparison uses voomWithDreamWeights and dream's fixed-effect path. Random-intercept performance requires repeated donors.

Diagnostic intervals are nominal two-sided exact 95% Clopper–Pearson. The prespecified heterogeneity check uses the genome-scale compounded arm with robust=TRUE, trend=TRUE, TMM and filterByExpr. Its lower confidence limit must exceed nominal 0.05 to extend the quality-weighted primary to otherwise small independent designs on the basis of this experiment. The frozen 0.075 calibration ceiling remains separate. Other factorial contrasts are descriptive. Development seeds start at 41070000 using offsets for scale, planted status and replicate.

The held-out validation uses a disjoint NumPy SeedSequence root 2026112605 and 1,200 null cohorts in each of the five historical regimes. The one-sided bound uses delta = 0.025/5 = 0.005. Regime status requires the historical 0.075 gate and original power floor, the new UCB gate, complete refusals and the ADR-16 same- and cross-architecture sentinels. Any missing or failing gate keeps the regime at L1. These are synthetic solver assessments.

## Diagnostic results

Across 9,600 fits (480 matrices), all 80 diagnostic cells completed. Python independently checked 48,865,476 fitted p/q pairs against BH. No discovered planted gene had a reversed direction. The 16 edgeR variants ranged from 5–8 null rejections on the pure targeted panel and 13–14 on the compounded targeted panel. At genome scale the ranges were 5–6 and 7, respectively, out of 100. Disabling the abundance trend changed the designated targeted compounded result from 14/100 to 13/100. The data support a contribution from donor heterogeneity on small panels. They do not uniquely identify trend overfitting as the cause. Matched matrices mean the 16 arms cannot be pooled as independent replications.

For the designated genome-scale compounded comparison, 7/100 gives a 95% interval of [0.0286052889, 0.1389197285]. This does not meet the prespecified condition for extending the large-cohort quality-weighted route to all small independent studies. DESeq2 had the highest observed rejection rate in all four cells under this particular Wald protocol. All methods retained high recall for the threefold planted signals. Smaller effects were outside this experiment.

Each table entry gives null rejections out of 100 (two-sided exact 95% CI in percent) and recovered planted genes out of 480 or 25,000. The direction-error numerator was zero for every entry. The JSON retains full-precision intervals and all numerators and denominators.

| Arm (robust / trend / norm / filter for edgeR) | Pure, 192 | Compounded, 192 | Pure, 10,000 | Compounded, 10,000 |
|---|---:|---:|---:|---:|
| T/T/TMM/filterByExpr | 5 (1.64–11.28), 477/480 | 14 (7.87–22.37), 475/480 | 5 (1.64–11.28), 24,840/25,000 | 7 (2.86–13.89), 24,700/25,000 |
| F/T/TMM/filterByExpr | 5 (1.64–11.28), 477/480 | 14 (7.87–22.37), 475/480 | 6 (2.23–12.60), 24,840/25,000 | 7 (2.86–13.89), 24,700/25,000 |
| T/F/TMM/filterByExpr | 5 (1.64–11.28), 477/480 | 13 (7.11–21.20), 475/480 | 6 (2.23–12.60), 24,840/25,000 | 7 (2.86–13.89), 24,695/25,000 |
| F/F/TMM/filterByExpr | 5 (1.64–11.28), 477/480 | 13 (7.11–21.20), 475/480 | 6 (2.23–12.60), 24,840/25,000 | 7 (2.86–13.89), 24,695/25,000 |
| T/T/RLE/filterByExpr | 8 (3.52–15.16), 477/480 | 13 (7.11–21.20), 475/480 | 5 (1.64–11.28), 24,844/25,000 | 7 (2.86–13.89), 24,699/25,000 |
| F/T/RLE/filterByExpr | 8 (3.52–15.16), 477/480 | 13 (7.11–21.20), 475/480 | 5 (1.64–11.28), 24,844/25,000 | 7 (2.86–13.89), 24,699/25,000 |
| T/F/RLE/filterByExpr | 7 (2.86–13.89), 477/480 | 13 (7.11–21.20), 475/480 | 5 (1.64–11.28), 24,844/25,000 | 7 (2.86–13.89), 24,697/25,000 |
| F/F/RLE/filterByExpr | 7 (2.86–13.89), 477/480 | 13 (7.11–21.20), 475/480 | 5 (1.64–11.28), 24,844/25,000 | 7 (2.86–13.89), 24,697/25,000 |
| T/T/TMM/mean≥1 | 5 (1.64–11.28), 480/480 | 14 (7.87–22.37), 479/480 | 5 (1.64–11.28), 24,993/25,000 | 7 (2.86–13.89), 24,861/25,000 |
| F/T/TMM/mean≥1 | 5 (1.64–11.28), 480/480 | 14 (7.87–22.37), 479/480 | 6 (2.23–12.60), 24,993/25,000 | 7 (2.86–13.89), 24,861/25,000 |
| T/F/TMM/mean≥1 | 5 (1.64–11.28), 480/480 | 13 (7.11–21.20), 479/480 | 6 (2.23–12.60), 24,994/25,000 | 7 (2.86–13.89), 24,858/25,000 |
| F/F/TMM/mean≥1 | 5 (1.64–11.28), 480/480 | 13 (7.11–21.20), 479/480 | 6 (2.23–12.60), 24,994/25,000 | 7 (2.86–13.89), 24,858/25,000 |
| T/T/RLE/mean≥1 | 8 (3.52–15.16), 480/480 | 13 (7.11–21.20), 479/480 | 5 (1.64–11.28), 24,993/25,000 | 7 (2.86–13.89), 24,862/25,000 |
| F/T/RLE/mean≥1 | 8 (3.52–15.16), 480/480 | 13 (7.11–21.20), 479/480 | 5 (1.64–11.28), 24,993/25,000 | 7 (2.86–13.89), 24,863/25,000 |
| T/F/RLE/mean≥1 | 7 (2.86–13.89), 480/480 | 13 (7.11–21.20), 479/480 | 5 (1.64–11.28), 24,994/25,000 | 7 (2.86–13.89), 24,861/25,000 |
| F/F/RLE/mean≥1 | 7 (2.86–13.89), 480/480 | 13 (7.11–21.20), 479/480 | 5 (1.64–11.28), 24,994/25,000 | 7 (2.86–13.89), 24,861/25,000 |
| voom | 4 (1.10–9.93), 477/480 | 9 (4.20–16.40), 471/480 | 5 (1.64–11.28), 24,835/25,000 | 5 (1.64–11.28), 24,649/25,000 |
| voomWithQualityWeights | 5 (1.64–11.28), 477/480 | 9 (4.20–16.40), 473/480 | 5 (1.64–11.28), 24,835/25,000 | 5 (1.64–11.28), 24,647/25,000 |
| DESeq2 | 14 (7.87–22.37), 477/480 | 26 (17.74–35.73), 476/480 | 28 (19.48–37.87), 24,840/25,000 | 39 (29.40–49.27), 24,679/25,000 |
| variancePartition::dream | 4 (1.10–9.93), 477/480 | 9 (4.20–16.40), 471/480 | 4 (1.10–9.93), 24,836/25,000 | 5 (1.64–11.28), 24,651/25,000 |

The baked R 4.6.1 images both contained edgeR 4.10.7, limma 3.68.5, DESeq2 1.52.0, variancePartition 1.42.0 and statmod 1.5.2. Both lacked dreamlet. Building and pinning dreamlet remains an M2 prerequisite. The variancePartition arm used its real engine throughout.

## Routed policy and scope

At the mounted entry point the adapter counts distinct biological units per arm and reads the declared model before inspecting expression. Small balanced and complete paired two-arm directed designs use edgeR QL with ordinary limma-voom sensitivity. An explicitly heterogeneous independent design or a balanced independent design with at least 15 donors/arm uses quality-weighted voom with robust eBayes and edgeR sensitivity. Unequal independent groups in a directed two-arm comparison use the fixed-effect dream path, including explicitly scaled contrasts. General multilevel contrasts retain their existing procedure. Repeated donors in a mixed model use its random-intercept path. Incomplete declared fixed pairs are refused. The pre-fit count filter sets the edgeR abundance trend off below 500 *eligible* genes, including when edgeR runs as a comparator. The two fitted BH families retain independent statuses and results. Large independent coefficient contrasts carry rank/permutation metadata from 20/arm. At 100 total units the existing bounded rank implementation can execute when its design checks pass. Explicit legacy sensitivity, tximport, omnibus and Python-only hypotheses retain their original contracts.

The sealed `validation-evidence.json` retains the original `bulk_solver_policy.py` digest from before the fix that gives quality-weighted voom with edgeR sensitivity precedence solely for unequal two-arm independent designs declared `heterogeneous_independent=True`, a combination outside the validation strata and parity probes.

## Historical regression and held-out qualification

The 2,200 historical matrices regenerated from the unchanged 20261117 generator. Both design compilers refused 200/200 confounded cohorts (104 donors each). All 2,000 estimable cohorts completed, and the normal mounted sentinel outputs matched the R batch result vectors exactly. The fresh panel added 6,000 matrices, all distinct from the historical matrices and one another. Each row below uses BH q ≤ 0.05. U is the exact one-sided 99.5% Clopper–Pearson upper bound, evaluated against 0.075 at full precision.

| Stratum | Historical null rejections | Historical recovered / planted | Fresh null rejections | Empirical FDR | U | Status in tested stratum |
|---|---:|---:|---:|---:|---:|---|
| B-I | 17/200 | 4,754/4,800 | 85/1,200 | 0.07083333 | 0.0920846726 | `uncalibrated_L1` |
| B-P | 10/200 | 4,800/4,800 | 48/1,200 | 0.04000000 | 0.0569159336 | `calibrated_L2` |
| S-I | 22/200 | 4,783/4,800 | 87/1,200 | 0.07250000 | 0.0939487421 | `uncalibrated_L1` |
| S-P | 17/200 | 4,800/4,800 | 78/1,200 | 0.06500000 | 0.0855360862 | `uncalibrated_L1` |
| P-P | 9/200 | 4,800/4,800 | 63/1,200 | 0.05250000 | 0.0713560678 | `calibrated_L2` |

All five historical planted panels cleared their original recall floors, with zero wrong directions. Only B-P and P-P cleared both null gates. The 0.075 ceiling quantifies uncertainty in *study-level* false discovery risk. Three fresh point estimates below 0.075 still have upper bounds above it. The two L2 statuses apply to these synthetic 192-gene, ten-unit-per-arm strata. New study receipts default to L1. Testing dispatch on a large independent or mixed design does not establish its error rate. No clinical release is authorized.

## ADR-16 replay

Two independent arm64 runs produced identical gene Parquet SHA-256 for each of the five fitted sentinels. An independent amd64 run passed the unchanged continuous, probability, within-family BH, structure, discovery and sign checks for each sentinel. The sixth input was refused on all three runs. No discovery fell in the boundary band of either fitted family. Per-fit receipts retain hashes, runtime facts, check counts and maximum differences.

Three additional checkout probes also passed both replay modes. They exercised declared heterogeneous independent donors (quality-weighted voom primary), incomplete pairs (random-intercept dream), and an unequal independent 9+10 design with an explicitly doubled contrast (fixed-effect dream). These are numerical replay checks. None of the three probes qualifies its branch for L2 error-rate control.

The separate baked-image baseline reproduced the seven original S-I failures. Each gene appeared twice in the old verifier, once as the effect and once as the displayed coordinate. All seven passed the same continuous bound after routing.

| S-I gene | Absolute baked logFC difference | Absolute routed logFC difference |
|---|---:|---:|
| g025 | 1.03921323e-06 | 1.89431804e-14 |
| g030 | 3.11703957e-07 | 4.68375339e-15 |
| g104 | 9.42055034e-07 | 1.64868119e-14 |
| g138 | 3.61427996e-07 | 7.63104857e-15 |
| g163 | 1.02176469e-06 | 2.01366701e-14 |
| g170 | 3.53743394e-07 | 7.64319164e-15 |
| g177 | 4.88109605e-07 | 8.21565038e-15 |

## Reproduction and image custody

Run from this checkout with the locked Python 3.13 development environment and the local image IDs in the kernel lock. Each command requires a new work directory. The diagnostic writes raw fitted families there for independent scoring. The qualification command retains the staged matrices, fitted families and replay outputs.

```sh
ROOT="$(git rev-parse --show-toplevel)"
export DYLD_LIBRARY_PATH=/opt/homebrew/opt/sqlite/lib
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
"$ROOT/.venv/bin/python" -m scripts.run_m0_edger_ablation --work "$ROOT/.scientific-m0/reproduce-ablation"
"$ROOT/.venv/bin/python" -m scripts.run_m0_edger_validation --work "$ROOT/.scientific-m0/reproduce-validation"
```

The remediated fits ran checked-out Python and R from the read-only source bind in the pinned R 4.6.1/Bioconductor runtime. The R batch sources the mounted driver and its siblings explicitly. The ordinary Python entry point resolves the same sources. Only the historical parity control ran baked scripts. Production OCI images still contain their original files under `/opt/rna/runtime/`. M0-closeout/M1 must bake the updated sources into both architectures, record new immutable image pins, and repeat parity on those baked images.

The machine-readable records are [all 80 ablation cells](ablation-evidence.json), [historical and held-out gates](validation-evidence.json), and [baked and checkout parity receipts](parity-receipts.json).

## Files in this change

| Change | Directory | Files |
|---|---|---|
| Added | `docs/audits/2026-11-26-m0-edger-ablation` | `README.md`, `ablation-evidence.json`, `parity-receipts.json`, `validation-evidence.json` |
| Added | `scripts` | `m0_edger_ablation.R`, `run_m0_edger_ablation.py`, `run_m0_edger_validation.py` |
| Updated | `src/devbench/analysis` | `episode_evidence_queries.py` |
| Updated | `src/devbench/core` | `contrast_design.py` |
| Updated | `src/devbench/engine` | `mounted_result_validation.py`, `tier_a_executor.py` |
| Updated | `src/devharness_server/r` | `bulk_dream.R`, `bulk_edger.R`, `bulk_voom_sensitivity.R`, `mounted_edger.R`, `mounted_general.R` |
| Updated | `src/rna_kernels` | `bulk_solver_policy.py`, `mounted_bulk.py`, `mounted_model.py`, `mounted_rank.py`, `numerical_equivalence.py` |
| Added | `tests` | `test_bulk_solver_routing.py` |
| Updated | `tests` | `test_cross_arch_equivalence.py`, `test_mounted_bulk.py`, `test_mounted_result_validation.py`, `test_thesis_calibration.py`, `thesis_calibration_batch.R`, `thesis_calibration_parity.py` |

## Software verification

With `DEVBENCH_RNA_KERNEL_TEST_IMAGE` set to the locked arm64 voom digest, the focused suite produced **155 passed, 1 failed**. The failure was the preserved assertion requiring all five historical regimes to clear their original calibration gates. An earlier latent-fit subprocess exited 1 during concurrent tests; the isolated retry and the full focused rerun both passed that test. Its private R log was unavailable after cleanup. After adding the independent receipt-status check, the fast routing and ADR-16 files passed 100/100. Ruff lint and format checks passed on all 17 changed Python files.

The statistical verifier independently rescored all 9,600 diagnostic fits and the primary and comparator outputs from 8,000 estimable historical/fresh fits. Maximum BH disagreement was 2.22e−16 in both checks. Independent binomial-CDF inversion agreed with every held-out bound. The retained validation JSON binds the exact procedure source hashes.

```sh
export DEVBENCH_RNA_KERNEL_TEST_IMAGE=sha256:9d845943fc1754d9338330f9f5275848b95a65932ca7a41c3a89c369e63d0b98
"$ROOT/.venv/bin/python" -m pytest -q tests/test_bulk_solver_routing.py tests/test_cross_arch_equivalence.py tests/test_p3_general_pseudobulk.py tests/test_thesis_calibration.py tests/test_bulk_solver_policy.py tests/test_mounted_bulk.py tests/test_mounted_result_validation.py tests/test_bulk_edger_general.py tests/test_bulk_dream.py
```

The full repository regression collection ran in eight file shards with the development lock and the optional Docker test variable unset. An initial sequential run was interrupted and restarted in shards. The full run recorded 13,222 passed, 792 skipped, 28 failed and four fixture errors. All 32 failures/errors were rerun with the offline locked analysis extras. Eighteen passed on rerun. The remaining ten failures and four errors reproduced on an independent checkout of `dead1ed` with the same test IDs and outcomes. They involved existing Studio/plugin HTTP authentication, startup and worker-ownership tests. These broader regressions are separate from the explicitly enabled historical calibration assertion above.
