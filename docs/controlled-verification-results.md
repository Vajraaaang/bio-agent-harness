# Controlled verification results — 2026-09-07

Version 0.4.2 was executed on the Mac and in [this actual Colab notebook](https://colab.research.google.com/drive/1SjWsgJkCLUD24bzoDeBe_Tk23MkzE9K-). No model participant or private clinical data was used. Clinical release remains blocked. See the [three-page evidence report](../output/pdf/controlled-execution-verification.pdf) and [implementation/qualification boundaries](controlled-execution.md).

| Evidence | Result |
|---|---|
| Final local full suite | 661 passed, 2 upstream Authlib warnings, 317.97 seconds |
| Final local focused checks | 155 passed, 24.62 seconds |
| Actual Colab focused checks | 155 passed, 36.50 seconds, Linux x86_64 / Python 3.13.15 |
| Numerical verification | Four completed runs on each platform; Gaussian and NB each repeated exactly within that platform |
| R comparisons | Gaussian F and fixed-dispersion NB regression agreed; full DESeq2 shrinkage equivalence not tested |
| Downloaded final cloud evidence | 282 file hashes, four manifests and eight synthetic configured-key approval signatures verified |
| Cross-platform maximum p difference | Gaussian 1.4711e-15; NB 7.8689e-13; same input and implementation hashes |
| Historical sealed records | 30 hashes rechecked, unchanged |
| Peak memory / model billing | Peak RSS unknown; 2048 MiB worker cap enforced. No live model inference measured. |

The first Colab attempt failed during ensurepip setup. The second completed Gaussian but failed NB due to inherited `MPLBACKEND=module://matplotlib_inline.backend_inline`. Both failures are retained. The final executor fixes its noninteractive plotting backend and records it in the environment identity. The setup uses pip's separate-interpreter support. The added count regression, unsafe-Zarr-path test and missing-reference test are included in the 155 final focused checks.

Evidence root: `results/controlled-execution-2026-09-07/`. `colab-final.zip` is the downloaded cloud archive; `colab-final-verification.json` records independent local hash/audit/signature checks and platform comparisons. `local-final/` contains the final Mac inputs, plans, approvals, results, R scripts/outputs and DAGs. `colab-attempt-2.zip` retains the failed measurement. `colab-attempts.json` identifies all three notebooks and their outcomes. `final-full-tests.log` and `final-full-tests.xml` retain the full suite result. `dist/` contains the 0.4.2 wheel and source distribution.

These checks establish bounded software behavior on the tested synthetic fixtures. They do not establish analytical or clinical validity, qualified identity/retention/deployment, or regulatory compliance. The intended context of use, complete statistical/assay qualification, independent clinical cohorts and operational quality approval remain open.
