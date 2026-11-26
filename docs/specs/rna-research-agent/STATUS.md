# RNA-seq Research Agent — Implementation Status & Ratified Decisions

- **Spec Suite Root:** [README.md](README.md) · [01](01-baseline-audit-and-gaps.md) · [02](02-problem-space-and-methods.md) · [03](03-adaptive-research-design.md) · [04](04-system-architecture-and-governance.md) · [05](05-codex-and-mcp-integration.md) · [06](06-milestones-and-evaluation.md)
- **Audited Code Baseline:** `18161fdabd162084732a4dd3c74e1ea42254a81b` (Merge PR #13; delta from `ad1d74724e1e4d12a3c0fe6840e6521bd34a445b`)
- **Current Phase:** **`M0` Ready for Implementation** (`0` blocking `M0` open decisions remain)
- **Permanent Non-Goal:** `clinical_release_allowed = false` (`L4` permanently out of scope)

---

## 1. Ratified `M0` Baseline Decisions (Locked — Do Not Reopen Without Concrete Contradiction)

| Decision ID | Source IDs | Summary of Settled Policy |
|---|---|---|
| **`ADR-15`** | `UD-01` (`OQ-01`) | **2×2 `edgeR` Null-FDR Diagnostic Ablation & Label-Blind Regime Routing:** Run fixed 2×2 DGP (`pure NB` vs `LogNormal-compounded NB`, matching `evidence.json` independent `39/400 = 9.75%, p = 7.95e-5` vs paired `38/600 = 6.33%, p = 0.134`) × scale (`N = 192` vs `N = 10,000`) across `edgeR v4`, `limma-voom` (`voom` / `voomWithQualityWeights` + `eBayes(robust = TRUE)`), `DESeq2`, and `dreamlet` without moving the 7.5% ceiling. Enforce pre-specified label-blind routing: `<500` eligible genes -> `edgeR v4` (`legacy = FALSE, robust = TRUE, abundance.trend = FALSE, TMM, filterByExpr`); balanced/paired `n < 15`/arm -> `edgeR v4 QL` (`TMM`) + `limma-voom`; unbalanced/repeated/longitudinal -> `dreamlet`; heterogeneous independent-donor / `n >= 15–20`/arm or confirmed LogNormal-NB failure -> `voomWithQualityWeights` + `eBayes(robust = TRUE)` primary + `edgeR v4` sensitivity (+ rank/permutation at `n >= 20`/arm). Failing regimes remain marked uncalibrated. |
| **`ADR-16`** | `UD-02` (`OQ-02`, `Q2.5`, `OQ-3.4`) | **Same-Arch Bitwise Identity & Cross-Arch (`arm64` vs `amd64`) Hybrid Equivalence:** `exact_match_same_arch` (`sha256` byte match, `0` tol, single-threaded BLAS) + `cross_arch_calibrated_match` (identical pinned packages): discrete fields `0` tol; continuous quantities `rtol = 1e-5`, `atol = 1e-7` (near-zero `abs(x) < 1e-4`); dual `p`/`q` linear `atol = 1e-5` AND `abs(log10(p_1) - log10(p_2)) <= 5e-4` (`p >= 1e-300`), `0 <= p <= q <= 1`, within-arch BH `atol = 1e-14`; 100% set match outside `0.05 +/- 2e-5`, 0 sign flips, boundary band logged. Netlib/FlexiBLAS optional debug oracle only. |
| **`ADR-17`** | `UD-24` (`Q6.1` tiers, `Q6.2`, `Q6.6`) | **Endpoint-Matched Bounds, Sizing, `delta` & Functional Governance Tiers:** Global-null Clopper–Pearson UCB `U <= 0.075` at `n = 1,200–1,350` (`0.060` only if `n >= 6,500` pure synthetic); planted-signal Waudby-Smith & Ramdas (2024, *JRSS-B*, `doi:10.1093/jrsssb/qkad009`) hedged-betting / empirical Bernstein `U <= 0.065–0.070` at `n = 600–1,000`; live-agent `n = 30–60` studies (study-clustered binomial/McNemar/WSR). Hierarchical `delta_global = 0.05` (`delta_S1 = 0.025`, `delta_S2_S6 = 0.025`, secondary diagnostics nominal). NIST AI RMF GOVERN 2.1 / FDA GMLP 3-tier governance: Standard (`>=5`), Small-team (`2–4`, 3 non-overlapping roles), Solo (`1`, capped at `L1`/`L2` until external blinded panel [`>=2` + tie-breaker, Krippendorff's `alpha >= 0.70` or quadratic-weighted `kappa >= 0.70`] adjudicates Scorecard 6 for `L3`). |

---

## 2. `M0` Implementation PR Board

| Wave / Order | PR ID & Branch | Status | Target Files & Scope | Acceptance Gates |
|---|---|---|---|---|
| **Wave 0** *(Sequential Prereq)* | `M0-0` (`main` baseline) | `[done]` | Commit `docs/specs/rna-research-agent/` (`README.md`, `STATUS.md`, `01`–`06`) as clean git baseline. | Spec hygiene |
| **Wave 1** *(Parallel Worktrees)* | `M0-1` (`m0/preflight-and-codex-hygiene`) | `[todo]` | Fix optional `cell_type` preflight (`src/devbench/platform/plugin_rna_solvers.py:408`); fix `mcp-payload-contracts.md` (`L301`, `L519`); retire stale v0.1.0 Codex artifacts (`integrations/codex/{devharness-0.1.0.zip,validation/,package-evidence.*,README.md,tests/verify_rna_runtime.py}`); add tests. | `BA-1`, `M0` #1, #3 |
| **Wave 1** *(Parallel Worktrees)* | `M0-2` (`m0/mcp-protocol-streaming-count1`) | `[todo]` | Add `outputSchema` to `list_tools()`, set `readOnlyHint=False` on `plan_rna_workflow` + opt-in browser popup, remove private-only H5AD streaming asymmetry (`plugin_mcp.py:619`), resolve Kang 38 MB `local_workflow_unavailable` (`L5738`), harden `COUNT-1` vs integer-`log2(1+x)`. | `BA-2(c)`, `BA-3` (`M0`), `M0` #2 |
| **Wave 1** *(Parallel Worktrees)* | `M0-3` (`m0/agents-md-and-adaptive-cleanup`) | `[todo]` | Split Benchmark Mode out of `AGENTS.md` (`< 8 KiB` target, CI `< 16 KiB` / `< 32 KiB`); migrate live `devbench.adaptive.{exploration,policy}` callers (`method_planner.py:330,343`, `exploratory_oci.py:90`, `rna_investigation_backend.py:225-230`) and retire dead quarantined stubs (keep `models.py`, `admission.py`). | `M0` #6, `BA-6` (stubs) |
| **Wave 1** *(Parallel Worktrees)* | `M0-4` (`m0/cross-arch-equivalence-verifier`) | `[todo]` | Implement `ADR-16` same-arch SHA-256 + cross-arch (`arm64`/`amd64`) hybrid equivalence verifier + optional Netlib/FlexiBLAS debug oracle, wired into `tests/thesis_calibration_parity.py`. | `BA-2(b)`, `M0` #5 |
| **Wave 2** *(After `M0-4`; script draftable in Wave 1)* | `M0-5` (`m0/edger-ablation-and-regime-routing`) | `[todo]` | Run `ADR-15` 2×2 DGP×scale diagnostic ablation, commit root-cause memo, implement label-blind regime-routed DE policy (`src/rna_kernels/bulk_solver_policy.py`, `src/devharness_server/r/*.R`, `src/rna_kernels/mounted_bulk.py`), validate on 2,200-cohort panel + `n = 1,200–1,350` global-null panel (`ADR-17`) + `M0-4` parity. | `BA-2(a)`, `M0` #4 |
| **Wave 3** *(Sequential Closeout)* | `M0-closeout` | `[todo]` | Run full `M0` regression/gate verification (`BA-1`, `BA-2`, `BA-3`, `BA-7`, `M0` #1–#6), update `STATUS.md`, lock `M1` kickoff. | All `M0` gates |

---

## 3. Milestone Rollup (`M0`–`M5`)

| Milestone | Status | Dependencies | Target Claim Level | Canonical Acceptance Reference |
|---|---|---|---|---|
| **`M0`** — Hygiene, Bug Fixes & Calibration Triage | **Ready / In Progress** | Baseline `18161fd` | `L1` + `L2` (DE triage) | [06](06-milestones-and-evaluation.md) `M0` #1–#6; [01](01-baseline-audit-and-gaps.md) `BA-1`–`BA-3`, `BA-7` |
| **`M1`** — Local Daemon, 13-Tool MCP, Dual-Lane Sandbox & Portable Codex Plugin | Pending | `M0` | `L1` | [06](06-milestones-and-evaluation.md) `M1` #1–#5; [04](04-system-architecture-and-governance.md) §8; [05](05-codex-and-mcp-integration.md) §9 |
| **`M2`** — Single-Cell / Single-Nucleus Core (`P1`) | Pending | `M0`, `M1` | `L1` + `L2` (`P1`) | [06](06-milestones-and-evaluation.md) `M2` #1–#5; [02](02-problem-space-and-methods.md) §7 |
| **`M3`** — Adaptive Investigation Graph (`E0`–`E6`), Multi-Aim Continuity & Critic | Pending | `M1`, `M2` | `L1` + `L2` | [06](06-milestones-and-evaluation.md) `M3` #1–#4; [03](03-adaptive-research-design.md) `AC-3.1`–`AC-3.10` |
| **`M4`** — Bulk Hardening (`P2`) & Modality Extensions (`P3`) | Pending | `M2` | `L1` + `L2` (`P2`/`P3`) | [06](06-milestones-and-evaluation.md) `M4` #1–#3 |
| **`M5`** — End-to-End Scientific Evaluation (Scorecards 1–6) & External Benchmarks | Pending | `M3`, `M4` | `L3` (`L4` held) | [06](06-milestones-and-evaluation.md) `M5` #1–#3, §5 |

---

## 4. Outstanding Blockers & Open Decisions

- **Blocking `M0` Open Decisions:** **None (`0`).**
- **Open Decisions for `M1`–`M4`:** `UD-03`–`UD-23` in [README.md §6](README.md#L98) (immediate pre-`M1` gates: `UD-03`–`UD-11`).
- **Open `M5` Operational Items (under `UD-24`):** `Q6.3` (pin exact `scBench`, `BixBench`, `BioAgent Bench` commit SHAs) and naming concrete campaign individuals + compute/model spend budgets at `M5` admission lock.
- **Non-Blocking Doc Note (defer to `M1`):** Pre-existing anchor typo at [`05-codex-and-mcp-integration.md:412`](05-codex-and-mcp-integration.md#L412) (`#42-tier-2--governed-exploratory-code-sandbox` -> `#42-tier-2-governed-exploratory-code-sandbox` in [04](04-system-architecture-and-governance.md)).
- **Untracked Outside Spec Suite:** `docs/goal-and-architecture.md` remains untracked on disk and excluded from spec commits.
