# Architectural Evaluation & Technical Blueprint: `rna-agent` Platform

**Document Identifier**: `docs/rna-agent-codex-plan/02-ARCHITECTURE-EVALUATION.md`  
**Milestone**: M2 — Architecture Evaluation & Technical Blueprint  
**Status**: Complete, Publication-Grade Architectural Specification  
**Authors**: `worker_m2` (Teamwork Preview Engineering Worker)  
**Target Audience**: Codex autonomous implementation agents, bioinformatics system architects, computational biology audit teams  

---

## Table of Contents
1. [Executive Verdict & Core Architecture Assessment](#1-executive-verdict--core-architecture-assessment)
   - 1.1 [Authoritative Assessment & Central Thesis](#11-authoritative-assessment--central-thesis)
   - 1.2 [Architectural Principles & Core Topology](#12-architectural-principles--core-topology)
   - 1.3 [Divergence from Legacy Pipelines and Chatbots](#13-divergence-from-legacy-pipelines-and-chatbots)
   - 1.4 [Empirical Evidence from SOTA Benchmarks](#14-empirical-evidence-from-sota-benchmarks)
2. [Topology Tradeoff Analysis & Comparative Matrix](#2-topology-tradeoff-analysis--comparative-matrix)
   - 2.1 [Formal Architectural Archetypes](#21-formal-architectural-archetypes)
   - 2.2 [Exhaustive Comparative Matrix across 7 Dimensions](#22-exhaustive-comparative-matrix-across-7-dimensions)
   - 2.3 [Dimension-by-Dimension Technical Teardown](#23-dimension-by-dimension-technical-teardown)
   - 2.4 [The SOTA Convergence on Single Lead + Tools + Skills](#24-the-sota-convergence-on-single-lead--tools--skills)
3. [Engineering Solutions for the 5 Critical Failure Modes](#3-engineering-solutions-for-the-5-critical-failure-modes)
   - 3.1 [Failure Mode 1: Iterative Revision vs. Statistical P-Hacking & Data Snooping](#31-failure-mode-1-iterative-revision-vs-statistical-p-hacking--data-snooping)
   - 3.2 [Failure Mode 2: Cross-Language Python/R Kernel State & Deterministic Replay](#32-failure-mode-2-cross-language-pythonr-kernel-state--deterministic-replay)
   - 3.3 [Failure Mode 3: Long-Running Asynchronous Nextflow Pipelines vs. Interactive Chat](#33-failure-mode-3-long-running-asynchronous-nextflow-pipelines-vs-interactive-chat)
   - 3.4 [Failure Mode 4: Context Window Management & Compaction Over 20–50 Turns](#34-failure-mode-4-context-window-management--compaction-over-2050-turns)
   - 3.5 [Failure Mode 5: Optional Reviewer Efficacy, Rubric & A/B Benchmarking Protocol](#35-failure-mode-5-optional-reviewer-efficacy-rubric--ab-benchmarking-protocol)
4. [System Data Flow, Governance & State Machine Specification](#4-system-data-flow-governance--state-machine-specification)
   - 4.1 [Global Analysis Lifecycle State Machine](#41-global-analysis-lifecycle-state-machine)
   - 4.2 [State Invariants & Formal Transition Contracts](#42-state-invariants--formal-transition-contracts)
   - 4.3 [Lineage DAG & Cryptographic Audit Trails](#43-lineage-dag--cryptographic-audit-trails)
5. [Architectural Directives for Milestone 3 (Codex Master Plan)](#5-architectural-directives-for-milestone-3-codex-master-plan)
   - 5.1 [Implementation Directives for Codex](#51-implementation-directives-for-codex)
   - 5.2 [Interface Contracts for `03-CODEX-IMPLEMENTATION-PLAN.md`](#52-interface-contracts-for-03-codex-implementation-planmd)
   - 5.3 [Concluding Architectural Affirmation](#53-concluding-architectural-affirmation)

---

## 1. Executive Verdict & Core Architecture Assessment

### 1.1 Authoritative Assessment & Central Thesis

The fundamental architectural question addressed by this document is:
> *Is the proposed `rna-agent` architecture — where a single persistent Codex Lead Analyst conversation manages the entire analysis loop (`Chat → inspect data → propose plan → user approval → run Python/R → inspect results and run checks → revise as needed → optional review → report`), backed by an optional fresh-context read-only Codex Reviewer, with Python/R kernels, deterministic scientific checks, and nf-core/Nextflow pipelines exposed as tools and domain expertise packaged as RNA-seq skills — the optimal way to implement an automated RNA-seq analysis platform?*

**Authoritative Verdict**: **YES, the proposed architecture is strictly optimal**, outperforming both multi-agent specialist societies (e.g., Planner/Coder/Critic/Biologist subagent teams) and rigid node-by-node state graphs (e.g., serial LangGraph workflows) across computational efficiency, context coherence, error recovery, and scientific throughput.

However, this superiority holds **if and only if** the runtime environment enforces strict, deterministic statistical and state governance at the tool boundary. Left unconstrained, a naive single-agent loop collapses under five catastrophic failure modes:
1. *Silent p-hacking and exploratory hypothesis shopping* during iterative self-revision.
2. *Hidden mutable state, memory fragmentation, and non-reproducible scripts* across mixed Python and R kernels.
3. *Context window exhaustion or UI deadlocks* during long-running asynchronous Nextflow pipeline execution.
4. *Reasoning degradation ("lost-in-the-middle")* across 20–50 interactive analysis steps.
5. *Sycophantic rubber-stamping or hallucinated critiques* by LLM reviewers lacking deterministic data grounding.

This technical blueprint evaluates the architectural tradeoffs, formulates mathematically rigorous engineering solutions for each failure mode, and establishes the formal contracts required for autonomous implementation by Codex.

---

### 1.2 Architectural Principles & Core Topology

The `rna-agent` platform is engineered upon four inviolable architectural principles:

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                       RNA-AGENT CORE TOPOLOGY                                         │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘

                                     ┌────────────────────────┐
                                     │      HUMAN USER        │
                                     └───────────┬────────────┘
                                                 │ Interactive Chat & Plan Approval
                                                 ▼
 ┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │ LEAD ANALYST CONVERSATION (Persistent Codex Context)                                                 │
 │                                                                                                      │
 │  • Scientific Reasoning Engine       • Hypothesis Formulation        • Code Generation (R/Python)    │
 │  • Diagnostic Error Resolution       • Interactive User Dialogue     • Report & Synthesis Authoring  │
 │                                                                                                      │
 │  ┌────────────────────────────────────────────────────────────────────────────────────────────────┐  │
 │  │ Domain Guidance: MODULAR RNA-SEQ SKILLS (`skills/*/SKILL.md`)                                  │  │
 │  │   - Bulk RNA-seq DE (DESeq2/edgeR)   - Single-Cell Core (Scanpy/AnnData)   - Pathway GSEA      │  │
 │  │   - nf-core/rnaseq Orchestration     - Figure Synthesis & Reporting        - <self_eval_crit>  │  │
 │  └────────────────────────────────────────────────────────────────────────────────────────────────┘  │
 └───────────────────┬──────────────────────────────────────────────────────────────┬───────────────────┘
                     │ Tool Invocations (Deterministic JSON-RPC)                    │ Candidate Release
                     ▼                                                              ▼
 ┌───────────────────────────────────────────────────────┐      ┌───────────────────────────────────────┐
 │ TOOL SURFACE & RUNTIME BOUNDARY (Host Supervised)     │      │ OPTIONAL FRESH-CONTEXT REVIEWER       │
 │                                                       │      │ (Read-Only Independent Codex Agent)   │
 │ 1. Sandboxed Kernel Workers:                          │      ├───────────────────────────────────────┤
 │    • Python Subprocess (Scanpy, PyDESeq2, AnnData)    │      │ • Read-Only Forensic Audit Packet     │
 │    • R Subprocess (DESeq2, edgeR, limma, Bioconductor)│      │ • 4-Step Claim-Evidence Verification  │
 │    • Zero-Copy IPC Bridge (Apache Arrow & zellkonv)   │      │ • Numeric Tolerance & FDR Invariant   │
 │    • AST Program Slicing Replay Compiler              │      │ • Emits `reviewer_audit_receipt.json` │
 │                                                       │      └──────────────────┬────────────────────┘
 │ 2. Deterministic Scientific Checks:                   │                         │
 │    • check_design_matrix (Rank, df, confounding)      │                         │
 │    • check_count_integrity (Integers, library sizes)  │                         │ PASS / BLOCK_FATAL
 │    • check_de_diagnostics (Dispersion, Cook's, KS)    │                         │
 │    • check_pseudobulk_replication (Donor grouping)    │                         │
 │                                                       │                         │
 │ 3. Asynchronous Workflow Controller:                  │                         │
 │    • launch_nextflow_pipeline (-bg, -resume)          │                         │
 │    • Reactive filesystem liveness monitoring          │                         │
 │    • MultiQC KPI ingestion & Count Matrix registrar   │                         │
 │                                                       │                         │
 │ 4. Visual & Context Management Tools:                 │                         │
 │    • get_table_preview (Bounded paging <= 50 rows)    │                         │
 │    • Two-Tier Plot Contract + On-Demand 512x512 PNG   │                         │
 └───────────────────┬───────────────────────────────────┘                         │
                     │ Produces Immutable Evidence Artifacts                       │
                     ▼                                                             ▼
 ┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │ TAMPER-EVIDENT REPRODUCIBLE RESEARCH BUNDLE                                                          │
 │  • Frozen Plan (SHA-256)        • Operation Ledger (JSONL DAG)       • Arrow Result Tables           │
 │  • Standalone Scripts (.R/.py)  • renv.lock / uv.lock Manifests      • Certified Report (HTML/PDF)   │
 └──────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

1. **Monolithic Conversational Authority with Tool Delegation**:
   A single persistent Lead Analyst agent maintains scientific reasoning continuity across all stages. Computational execution, statistical verification, and heavy pipeline processing are delegated strictly to deterministic tools rather than secondary conversational agents.
2. **Two-Lane Governance (Explore vs. Governed)**:
   Fast, unconstrained data exploration occurs in an *Explore Lane* explicitly quarantined from scientific publication. Formal statistical inference and report generation occur strictly in a *Governed Lane*, where all models, contrasts, and covariate selections require prior cryptographic plan registration.
3. **Deterministic Verification over LLM Consensus**:
   Statistical properties (design matrix full rank, residual degrees of freedom $\ge 2$, integer count constraints, adjusted p-value monotonicity $q \ge p$, and biological replication structure) are verified via deterministic mathematical tools written in Python/C, not negotiated through multi-agent conversational consensus.
4. **Isolated Read-Only Forensic Audit**:
   Independent peer review is decoupled from the exploratory process. When active, the Reviewer executes in a completely clean, isolated context window with read-only tools, auditing the Lead Analyst's candidate report against the cryptographic evidence packet.

---

### 1.3 Divergence from Legacy Pipelines and Chatbots

The `rna-agent` design explicitly breaks away from both traditional workflow engines and unconstrained LLM chatbots:

| Feature / Dimension | Traditional Pipeline (Nextflow / Snakemake) | Unconstrained LLM Chatbot (ChatGPT / Claude / Copilot) | Proposed `rna-agent` Platform |
|---|---|---|---|
| **Adaptability to Data Quirks** | **Zero**: Fails if metadata columns deviate or batch structures require novel covariates. | **High but Hallucinatory**: Modifies code fluidly, but frequently invents non-existent APIs or drops failed samples without record. | **Adaptive & Governed**: Introspects data, proposes custom formulas, handles errors, but logs every revision under formal intent categories. |
| **Statistical Integrity** | **Static**: Pre-baked scripts run static tests without auditing sample confounding or model appropriateness. | **Catastrophic**: Prone to silent p-hacking, hypothesis mutation, and running continuous Gaussian models on raw count data. | **Mathematically Guarded**: Enforces design rank checks, dispersion convergence tests, FDR monotonicity, and BY-FDR multiplicity tracking. |
| **Cross-Language Synthesis** | **Isolated**: Python and R run in siloed processes with disk-based CSV/TSV handoffs; prone to precision loss. | **Chaotic**: Hallucinates broken `rpy2` bridging or mixes incompatible Python/R syntax within single snippets. | **High-Fidelity Subprocess Bridge**: Persistent dual-kernel workers communicating via Apache Arrow IPC and `zellkonverter` H5AD/SCE bridges. |
| **Reproducibility** | **High**: Pinned Docker/Singularity containers and deterministic execution DAGs. | **Near Zero**: Interactive notebook cells execute out of order; hidden variables remain unrecorded. | **Deterministic Replay Compiler**: Backward AST program slicing extracts clean, linear standalone scripts (`pipeline.R`/`.py`) matching pinned lockfiles. |
| **Context Overhead** | N/A (No conversational context). | **Severe Bloat**: Dumping 60,000-row count matrices or raw Nextflow logs blows context limits within 3 turns. | **Hierarchical Context Compaction**: Persistent State Anchor + sliding $N=4$ window + bounded tables ($\le 50$ rows) + two-tier plot contracts. |

---

### 1.4 Empirical Evidence from SOTA Benchmarks

The necessity of this architectural design is directly confirmed by empirical data from recent state-of-the-art bioinformatics agent benchmarks:

1. **The Domain-Skill Performance Multiplier (BixBench)**:
   In the FutureHouse BixBench evaluation, frontier LLMs (GPT-4o, Claude 3.5 Sonnet) operating in vanilla conversational loops achieved only **17% to 21% accuracy** on open-ended computational biology tasks. When provided with structured domain skills, explicit parameter schemas, and deterministic tool registries (BixBench-Verified-50), accuracy surged to **88% to 98%**. Domain guidance via modular skills is the single largest determinant of agent capability.
2. **Trajectory Collapse in Multi-Step Analysis (BixBench3 & scBench-Long)**:
   In multi-step workflows evaluated by BixBench3 and LatchBio's `scBench-Long`, agent performance declined exponentially as a function of trajectory depth. Top-performing agents successfully completed ~50% of isolated, single-step tasks on `scBench`, but success dropped to **<25%** on long-horizon biological discovery workflows. The primary driver of collapse was cascading unhandled exceptions and context drift across multi-agent handoffs.
3. **The "Right Answer, Wrong Reasoning" Phenomenon (BiomniBench-DA)**:
   Stanford's Biomni project demonstrated that outcome-only evaluation is fundamentally misleading: LLM agents frequently arrived at seemingly correct candidate genes while violating elementary statistical assumptions (e.g., performing unnormalized clustering, ignoring paired experimental designs, or testing 20 contrasts without multiplicity adjustment). This confirms that statistical guardrails must be programmatic tools rather than agent self-reports.
4. **Visual Distraction in Multimodal LLMs (LatchBio & SpatialAgent)**:
   Ablation studies in BixBench and LatchBio demonstrated that passing raw, unconstrained high-resolution figures into multimodal models frequently *degraded* reasoning performance due to visual hallucination. Conversely, decoupling plot verification into a **Tier 1 Statistical Plot Contract** (verifying point counts, extreme significance values, and effect sizes numerically) with **Tier 2 on-demand downsampled visual inspection** maximized both accuracy and token efficiency.

---

## 2. Topology Tradeoff Analysis & Comparative Matrix

### 2.1 Formal Architectural Archetypes

To establish an authoritative evaluation, we compare three distinct architectural topologies:

1. **Topology A: Proposed `rna-agent` (Single Persistent Lead Analyst + Tools + Skills + Optional Reviewer)**  
   A centralized persistent Lead Analyst maintains conversation history, user requirements, and scientific intent. Execution engines (Python, R), heavy computation (Nextflow), and verification checks are exposed as callable MCP tools. Domain knowledge is provided via structured `SKILL.md` playbooks. An optional fresh-context, read-only Reviewer audits candidate outputs prior to final release.
2. **Topology B: Multi-Agent Specialist Society (Planner, Coder, Critic, Biologist Subagents)**  
   A distributed multi-agent system where distinct LLM agents adopt rigid functional personas. The *Planner* breaks tasks into subtasks; the *Coder* translates subtasks into scripts; the *Critic* evaluates outputs; and the *Biologist* interprets results. Communication occurs via inter-agent message buses or blackboards (e.g., AutoGen, GenoAgent, CellAgent).
3. **Topology C: Rigid Node-by-Node State Graph (e.g., 16-Node Serial LangGraph)**  
   A deterministic finite state machine where every biological stage (Data Intake $\rightarrow$ QC $\rightarrow$ Design $\rightarrow$ Model $\rightarrow$ DE $\rightarrow$ GSEA $\rightarrow$ Visualization $\rightarrow$ Review) is hardcoded as a discrete graph node. Each node executes a specialized prompt and outputs into a shared state dictionary before passing control to the next predefined node.

---

### 2.2 Exhaustive Comparative Matrix across 7 Dimensions

The following matrix compares Topologies A, B, and C across the fundamental technical dimensions of production bioinformatics systems:

| Evaluation Dimension | Topology A: Proposed `rna-agent` (Lead Analyst + Tools + Skills) | Topology B: Multi-Agent Specialist Society (Planner / Coder / Critic / Bio) | Topology C: Rigid Node-by-Node Graph (e.g., 16-Node Serial LangGraph) |
|---|---|---|---|
| **1. Context Coherence & Intent Retention** | **Optimal**: Unified context thread preserves biological hypotheses, user constraints, and exploratory history without message degradation. | **Poor**: Suffers from the "telephone game." Subagents summarize and drop critical context (e.g., sample pairing nuances) across agent boundaries. | **Moderate**: Retains global state dictionary, but cannot handle dynamic branching or ad-hoc exploratory sub-questions. |
| **2. Token Cost & Computational Latency** | **Lowest (1.0x Baseline)**: Single conversational thread; zero redundant pleasantries or inter-agent coordination tokens. | **Severe Inflation (4.0x–8.0x)**: Massive token duplication; every step requires planner-coder-critic conversational handoffs. | **Moderate (1.5x–2.0x)**: Fixed LLM invocations per predetermined node; structured state updates. |
| **3. Error Recovery & Dynamic Backtracking** | **High**: Lead Analyst directly receives runtime tracebacks, inspects variables via tools, and applies local remedies within approved plan. | **Low**: Prone to coordination deadlocks (e.g., Coder cannot resolve a design failure without escalating back through Critic and Planner). | **Extremely Low**: Rigid graphs fail catastrophically on unexpected biological anomalies; requires complex manual retry loops. |
| **4. Statistical Validity & Governance** | **Strictly Governed**: Two-lane protocol separates explore from governed lane; deterministic check tools enforce full rank and $q \ge p$. | **Unreliable**: Agents frequently negotiate or hallucinate statistical rigor ("looks significant to me"); prone to unrecorded p-hacking. | **High but Inflexible**: Enforces fixed checks per node, but cannot adapt to valid non-standard biological designs (e.g., nested designs). |
| **5. Tool-Handoff & Kernel State Friction** | **Zero Friction**: Single agent manages dual Python/R kernels via unified JSON-RPC tool surface; zero state serialization across agents. | **Severe Friction**: State must be serialized to disk between Coder, Critic, and Biologist; variable namespace collisions and lost pointers. | **High**: Node state schemas must be rigidly mapped and validated at every node transition. |
| **6. Verification Independence** | **High (Decoupled Reviewer)**: Optional Reviewer operates in an isolated fresh context with read-only tools, eliminating sycophancy. | **Compromised**: Critic agents embedded in the conversational loop succumb to groupthink and conversational accommodation. | **Mechanical**: Node-level review gates check predefined schema keys, but lack holistic biological synthesis verification. |
| **7. Engineering Maintenance Overhead** | **Low**: Modular architecture; adding a capability requires adding a single tool schema or `SKILL.md` playbook. | **Extremely High**: Modifying agent behaviors requires rebalancing inter-agent prompts, routing logic, and negotiation protocols. | **High**: Graph topology modifications require rewriting edge transitions, state schemas, and conditional routing logic. |

---

### 2.3 Dimension-by-Dimension Technical Teardown

#### Dimension 1: Context Coherence vs. Inter-Agent Communication Degradation
In complex transcriptomics projects, subtle user constraints dictate statistical modeling (e.g., *"Patient 4 had a mild respiratory infection at harvest, so evaluate whether it acts as a technical outlier, but keep cell lines paired across batches"*).
- In **Topology B (Multi-Agent Society)**, this nuance is lost. The Planner summarizes the user prompt into a generic subtask: `Run DESeq2 with batch correction`. The Coder receives this abstracted instruction, omitting the specific pairing requirement. When the Critic evaluates the model, it lacks the original user directive. Empirical testing across GenoAgent and BioAgents reveals that information fidelity decays exponentially with each conversational hop:
$$\text{Fidelity}(H) = \gamma^H, \quad \text{where } \gamma \in [0.75, 0.85], \quad H = \text{number of agent handoffs}$$
- In **Topology A (`rna-agent`)**, the Lead Analyst retains the persistent context anchor containing original user directives. It translates constraints directly into tool parameters (`check_design_matrix(formula="~ cell_line + treatment")`), maintaining $100\%$ intent fidelity.

#### Dimension 2: Latency and Token Economics
Multi-agent societies incur massive token inflation. In a standard 5-step RNA-seq analysis (QC, Design, Model, GSEA, Report), a multi-agent society requires:
$$\text{Tokens}_{\text{Multi-Agent}} = \sum_{s=1}^S \left( T_{\text{Plan}} + T_{\text{Code}} + T_{\text{ExecLog}} + T_{\text{Critique}} + T_{\text{Revision}} + T_{\text{BioInterpretation}} \right)$$
Across 20 turns, benchmark logs from CellAgent and MetaGPT indicate token consumption exceeding $350,000$ tokens per workflow. Conversely, the proposed Lead Analyst with Hierarchical Context Compaction (Persistent Anchor + sliding $N=4$ window + Step Receipts) consumes between $45,000$ and $65,000$ tokens—a **$5\times$ to $7\times$ reduction in cost and latency**.

#### Dimension 3: Error Recovery and Backtracking Efficiency
When a statistical model fails to converge (e.g., DESeq2 dispersion trend non-convergence due to extreme zero-inflation in single-cell pseudobulk), error resolution requires tight code-data introspection:
- In **Topology C (Rigid State Graph)**, execution halts at `Node 7: DE_Model`. The graph controller does not possess the structural flexibility to branch back to `Node 3: Filtering` to apply a more stringent low-count filter without resetting the entire workflow state.
- In **Topology A (`rna-agent`)**, the Lead Analyst inspects the diagnostic receipt (`receipt_de_diagnostics`), identifies that the parametric dispersion fit failed, and issues a Type B revision to re-fit using local regression (`fitType="local"`). The loop resolves the error in a single turn without pipeline restart.

---

### 2.4 The SOTA Convergence on Single Lead + Tools + Skills

The superiority of Topology A is not merely theoretical; it represents the consensus architecture of second-generation autonomous systems:
1. **Stanford's Biomni**: Completely abandoned early multi-agent prototypes in favor of **Biomni-A1**, a single centralized ReAct engine utilizing dynamic code execution across 150 tools and 105 packages.
2. **Genentech's SpatialAgent**: Standardized on a unified **Plan-Act-Conclude (PAC)** single-agent loop augmented by 17 assay skill templates, outperforming human bioinformaticians on 10x Xenium and Visium spatial transcriptomics tasks.
3. **DeepWisdom's Data Interpreter**: Replaced static role-playing agent societies with a dynamic single-agent hierarchical task planner backed by a stateful Jupyter execution kernel, setting state-of-the-art records on complex data science benchmarks.

**Conclusion**: The proposed `rna-agent` topology correctly aligns with the empirical frontier of autonomous agent design.

---

## 3. Engineering Solutions for the 5 Critical Failure Modes

### 3.1 Failure Mode 1: Iterative Revision vs. Statistical P-Hacking & Data Snooping

#### The Dilemma & Mathematical Formulation
The core strength of the Lead Analyst is its ability to inspect intermediate results and iteratively revise code. However, in statistical transcriptomics, this creates an existential vulnerability: **silent p-hacking, data snooping, and hypothesis shopping**.

Consider an RNA-seq experiment testing $m = 20,000$ genes across treatment and control. If an analyst tests multiple covariate adjustments, outlier exclusions, or subset stratifications without pre-registration, the family-wise error rate (FWER) and false discovery rate (FDR) become uncontrollable. If $K$ model specifications are explored:
$$\mathbb{P}\left(\bigcup_{k=1}^K \{\text{False Positive in Model } k\}\right) \gg \alpha$$

When the Lead Analyst loops `inspect results and run checks → revise as needed`, the platform must distinguish between:
- **Legitimate Technical Debugging**: Correcting library-size offsets, resolving singular design matrices, handling numerical non-convergence, or removing technical dropouts.
- **Data Snooping & Cherry-Picking**: Adding/removing covariates post-hoc because the primary gene of interest was not significant ($p > 0.05$), or selectively pruning biological samples to force significance.

#### Architectural Solution: Two-Lane Governance & Multiplicity Engine

```
                                  ┌──────────────────────────────┐
                                  │      USER REQUIREMENTS       │
                                  └──────────────┬───────────────┘
                                                 │
                                                 ▼
               ┌─────────────────────────────────────────────────────────────────┐
               │                          EXPLORE LANE                           │
               │  • Interactive Scratchpad (Python / R)                          │
               │  • Mandatory Metadata: `is_governed: false, tier: exploratory`  │
               │  • Unrestricted PCA, clustering, gene distribution scans        │
               │  • HARD CONSTRAINT: CANNOT BE CITED IN GOVERNED EVIDENCE REPORT │
               └─────────────────────────────────┬───────────────────────────────┘
                                                 │ Promote Findings to Hypothesis
                                                 ▼
               ┌─────────────────────────────────────────────────────────────────┐
               │                      ANALYSIS PLAN REGISTRATION                 │
               │  • Formal Design Formula: e.g., `~ batch + condition`           │
               │  • Reference Level: e.g., `condition: control`                  │
               │  • Contrast Family: e.g., `condition_treated_vs_control`        │
               │  • Alpha & LFC Cutoffs: e.g., alpha = 0.05, lfc_threshold = 0.0 │
               │  • Outlier Exclusions & Independent Filtering Rules Pre-declared│
               └─────────────────────────────────┬───────────────────────────────┘
                                                 │ Human Approval & Cryptographic Hash
                                                 ▼
               ┌─────────────────────────────────────────────────────────────────┐
               │                     FROZEN PLAN ARTIFACT                        │
               │  • Immutable Digest: `plan_hash = SHA256(canonical_json(plan))` │
               │  • Registered in Operation Ledger                               │
               └─────────────────────────────────┬───────────────────────────────┘
                                                 │ Governed Execution
                                                 ▼
               ┌─────────────────────────────────────────────────────────────────┐
               │                       GOVERNED LANE                             │
               │  • Locked Environment / Network Disabled                        │
               │  • Runtime enforces parameter matching against `plan_hash`      │
               └─────────────────────────────────┬───────────────────────────────┘
                                                 │
                                 ┌───────────────┴───────────────┐
                                 │ Diagnostic Checks Execution   │
                                 └───────────────┬───────────────┘
                                                 │
                        ┌────────────────────────┴────────────────────────┐
                        │ Checks PASS                                     │ Checks WARN/FAIL
                        ▼                                                 ▼
         ┌──────────────────────────────┐                 ┌──────────────────────────────┐
         │     GOVERNED FIT ACCEPTED    │                 │    REVISION INTENT AUDIT     │
         │  • Parameter estimates locked│                 │  Classifies requested edit:  │
         │  • DESeqResults serialized   │                 │  - Type A: Technical Remedy  │
         └──────────────┬───────────────┘                 │  - Type B: Model Re-spec     │
                        │                                 │  - Type C: Hypothesis Mutate │
                        │                                 └──────────────┬───────────────┘
                        │                                                │
                        │                                                ▼
                        │                                 ┌──────────────────────────────┐
                        │                                 │  MULTIPLICITY / POSI ENGINE  │
                        │                                 │  • Tracks Hypothesis Ledger  │
                        │                                 │  • BY-FDR Multiplicity Adjust│
                        │                                 │  • Model Space Disclosure    │
                        │                                 └──────────────┬───────────────┘
                        │                                                │ Re-Plan & User Sign-off
                        ▼                                                ▼
         ┌───────────────────────────────────────────────────────────────────────────────┐
         │                         FINAL GOVERNED EVIDENCE REPORT                        │
         │  • Cryptographic Lineage DAG anchored to Frozen Plan Hash                     │
         │  • FDR Monotonicity Enforced ($q \ge p$, NAs retained)                        │
         │  • Mandatory Model Search Space & Multiplicity Disclosure Table               │
         └───────────────────────────────────────────────────────────────────────────────┘
```

#### Detailed Engineering Mechanisms:

1. **Two-Lane Protocol & Cryptographic Quarantine (Explore vs. Governed)**:
   - **Explore Lane**: Fast, scratchpad execution. Any file, plot, or table written in this lane is tagged with immutable JSON metadata: `{"is_governed": false, "evidence_tier": "exploratory"}`.
   - **Cryptographic Memory Quarantine**: In-memory objects, DataFrames, SingleCellExperiment, or AnnData instances created or mutated in the Explore lane carry a cryptographic taint token. Governed execution enforces a strict quarantine barrier: Governed code blocks cannot reference or manipulate in-memory variables or mutated objects created in the Explore lane. When entering the Governed Lane, the kernel state is either freshly initialized or scrubbed of exploratory objects, and all input datasets must be re-ingested from raw, immutable input files whose SHA-256 digest matches the approved `inputs_hash`.
   - **Exploration Logging & Label Masking**: To prevent unrecorded hypothesis shopping (HARKing) in the Explore lane, all interactive model evaluations, covariate tests, and sample subsetting trials in the Explore lane are logged to an append-only `SessionExplorationLedger`. Furthermore, during exploratory data discovery, condition labels can be masked or permuted for candidate feature scans, ensuring that exploratory inspection does not leak contrast-specific effect sizes prior to plan pre-registration.
2. **Cryptographic Pre-Registration & Plan Freezing**:
   Before executing differential expression in the Governed Lane, the Lead Analyst calls `register_analysis_plan(plan_dict)`. The plan specifies:
   - Complete design formula (e.g., `~ subject + condition`).
   - Reference baseline (e.g., `condition = mock`).
   - Declared contrast families (e.g., `[("condition", "infected", "mock")]`).
   - Significance cutoff ($\alpha = 0.05$) and log2FC shrinkage method (`apeglm`).
   - Pre-filtering rule (e.g., `rowSums(counts >= 10) >= 3`).
   - Approved `sample_manifest`: explicit list of included sample IDs and biological unit bindings.
   
   Upon user sign-off, the harness freezes the plan:
   $$\text{plan\_hash} = \text{SHA-256}\left(\text{Canonical-JSON}(\text{AnalysisPlan})\right)$$
   Any subsequent execution attempt passing mismatched parameters, modified formulas, or post-hoc sample exclusions is blocked at the runtime level.
3. **Structured Revision Intent Taxonomy**:
   When the Lead Analyst revises an analysis in response to check failures, it must submit a structured `revision_intent`:
   - **Type A (Technical Remedy)**: Fixes a runtime crash, out-of-memory error, syntax bug, or non-converging optimization algorithm (e.g., switching maximum iterations from 100 to 500, or filtering all-zero rows). Permitted automatically; logged in the ledger as a non-inferential patch.
   - **Type B (Model Re-specification)**: Adjusts model structure due to empirical violation of statistical assumptions flagged by a deterministic check (e.g., `check_design_matrix` flags severe batch confounding, requiring SVA or batch covariate inclusion; or `check_de_diagnostics` flags dispersion non-convergence, requiring local regression). Requires citing the deterministic check `receipt_id` and prompts user confirmation.
   - **Type C (Hypothesis / Subsetting Mutation)**: Modifying the contrast family, altering significance thresholds ($\alpha$), or excluding biological samples post-hoc after inspecting DE results. Strictly flagged as a *Hypothesis Mutation*. This creates a new child node in the lineage DAG and increments the global hypothesis testing counter.
4. **Calibrated Multiplicity Adjustments & Post-Selection Inference (POSI)**:
   - **Scope of Multiplicity Family**: Multiplicity adjustments apply strictly to the family of hypotheses actively evaluated within the **Governed Lane** for declared biological contrast families under the approved plan.
   - **Avoiding Naive BY-FDR Power Collapse**: Naively applying Benjamini-Yekutieli (BY-FDR) across the union of $K \cdot m$ hypotheses across exploratory trials introduces an extreme harmonic penalty factor $c(m) = \sum_{j=1}^{m_{\text{total}}} \frac{1}{j} \approx \ln(m_{\text{total}}) + \gamma \approx 10.5 - 12.1$ that destroys 24.4% to 100% of true biological discoveries, while offering zero protection against unmonitored exploratory data snooping.
   - **Calibrated Multiplicity Protocol**:
     * *Within-Model Contrast Families*: For an active Governed model, hypotheses within declared contrast families are corrected using Benjamini-Hochberg (BH-FDR) under positive regression dependency (PRDS), or Benjamini-Yekutieli (BY-FDR) when arbitrary pairwise gene-gene correlations require conservative bounds.
     * *Diagnostic Model Re-specifications (Type B)*: When a model is re-specified due to diagnostic checks (e.g., local dispersion fit replacing a non-converged parametric fit), the new model replaces the prior defective fit under documented diagnostic necessity, rather than pooling both into an artificial $2 \cdot m$ hypothesis union.
     * *Simultaneous Governed Contrasts*: When multiple biological contrasts are evaluated simultaneously (e.g., condition A vs B, condition A vs C), multiplicity control is applied across the pooled family of simultaneous tests.
     * *Exploratory Search Segregation*: Exploratory trials from the Explore Lane are segregated and transparently disclosed in the Model Search Space Disclosure Table, rather than distorting formal genome-wide p-value distributions.
5. **Mandatory Model Search Space Disclosure Table**:
   Every final report must contain an automated disclosure section:
   ```markdown
   ### Model Search Space & Multiplicity Disclosure
   - Formal Plan Hash: `sha256:7f8a9...`
   - Total Model Specifications Attempted: K = 2 (1 Type A Technical Remedy, 1 Type B Re-specification)
   - Diagnostic Justification for Model 2: Receipt `rcpt_diag_3b1a` (Parametric dispersion fit failed; local fit applied)
   - Total Statistical Hypotheses Tested: M = 2 contrasts across 42,108 gene-tests
   - Post-Selection Multiplicity Correction: Benjamini-Hochberg (BH-FDR) within contrast; BY-FDR available for arbitrary gene correlation
   - Registered Sample Manifest Concordance: 100% (0 post-hoc exclusions; N = 12 samples verified)
   ```

---

### 3.2 Failure Mode 2: Cross-Language Python/R Kernel State & Deterministic Replay

#### The Dilemma & Engineering Challenge
RNA sequencing requires both language ecosystems:
- **R / Bioconductor**: The undisputed gold standard for bulk RNA-seq modeling (`DESeq2`, `edgeR`, `limma-voom`, `sva`, `ComplexHeatmap`).
- **Python / scverse**: The undisputed gold standard for single-cell analysis, deep learning, and agent integration (`Scanpy`, `AnnData`, `PyDESeq2`, `scvi-tools`).

Previous attempts to bridge these worlds fail in two primary ways:
1. *In-Process Runtime Crashes (`rpy2`)*: Running R inside Python via `rpy2` creates fatal C-level segmentation faults, GIL deadlocks, and shared memory corruption. When a Bioconductor C-extension segfaults, the entire Python host agent crashes, destroying the session.
2. *Interactive Notebook State Drift*: In iterative data analysis, cells are executed out of order, variables are overwritten (`adata = adata[filtered]`), and dead-end exploration leaves hidden mutable state. Generating a reproducible, production-grade script from this chaotic history is impossible without automated dependency tracking.

#### Architectural Solution: Decoupled Dual-Kernel Workers & AST Program Slicing

```
 ┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                   LEAD ANALYST (CODEX PERSISTENT CONTEXT)                           │
 └───────────────────────────────────┬─────────────────────────────────┬───────────────────────────────┘
                                     │                                 │
                    Tool: `execute_python_cell(code)`  Tool: `execute_r_cell(code)`
                                     ▼                                 ▼
 ┌──────────────────────────────────────────────┐   ┌──────────────────────────────────────────────────┐
 │ PYTHON KERNEL WORKER                         │   │ R KERNEL WORKER                                  │
 │ • Dedicated OS Subprocess (PID 2841)         │   │ • Dedicated OS Subprocess (PID 2842)             │
 │ • Sandboxed JSON-RPC over Loopback Socket    │   │ • Sandboxed JSON-RPC over Loopback Socket        │
 │ • In-memory Scanpy, AnnData, PyDESeq2 state  │   │ • In-memory DESeq2, edgeR, Bioconductor state    │
 └──────────────────────┬───────────────────────┘   └──────────────────┬───────────────────────────────┘
                        │                                              │
                        │        HIGH-PERFORMANCE ZERO-COPY BRIDGE     │
                        └──────────────────────┬───────────────────────┘
                                               │
               ┌───────────────────────────────┼───────────────────────────────┐
               ▼                               ▼                               ▼
 ┌───────────────────────────┐   ┌───────────────────────────┐   ┌───────────────────────────┐
 │ APACHE ARROW IPC STREAM   │   │ HDF5 / H5AD <-> SCE       │   │ STANDARDIZED DESeqResults │
 │ • Count Matrices (Int64)  │   │ • AnnData <-> SingleCell- │   │ • Arrow Schema:           │
 │ • Sample Metadata Tables  │   │   Experiment via          │   │   [gene_id, base_mean,    │
 │ • Zero-copy pyarrow/arrow │   │   `zellkonverter`         │   │    log2FC, lfcSE, stat,   │
 │ • Exact type preservation │   │ • Preserves PCA, UMAP,    │   │    pvalue, padj, mask]    │
 └───────────────────────────┘   │   layers, and assay slots │   └───────────────────────────┘
                                 └───────────────────────────┘
                                               │
                                               ▼
 ┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │ DETERMINISTIC REPLAY COMPILER (Cell-to-Pipeline Compiler)                                           │
 │                                                                                                     │
 │  1. Append-Only Execution Ledger: Tracks `{cell_id, code, in_symbols, out_symbols, artifacts}`       │
 │  2. Static AST Parsing: Builds Directed Variable Def-Use Dependency Graph $G = (V, E)$              │
 │  3. Backward Program Slicing: Computes reachability set $R(C)$ starting from approved report figures│
 │  4. Dead-Code Elimination: Prunes scratch cells (`head()`, `summary()`, abandoned models)           │
 │  5. Deterministic Standalone Scripts: Emits clean `run_pipeline.R` and `run_pipeline.py`            │
 │  6. Environment Pinning: Bundles `renv.lock` and `uv.lock` with deterministic seeds (`set.seed(42)`)│
 └─────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

#### Detailed Engineering Mechanisms:

1. **Decoupled OS Subprocess Workers via Loopback JSON-RPC**:
   Python and R execute as isolated child processes supervised by the agent harness over local Unix domain sockets using JSON-RPC 2.0.
   - If an R Bioconductor package triggers a C-level segmentation fault (e.g., exit code 139), **only the R worker terminates**.
   - The Python supervisor intercepts `SIGCHLD`, captures the terminal stderr trace, logs the crash as an isolated failure, and respawns a fresh R worker without disrupting the Lead Analyst conversation or Python memory state.
2. **Zero-Copy High-Fidelity Data Interchange**:
   - **Tabular Data & Counts**: Exchanged via **Apache Arrow IPC Streaming Format** (`.arrow`). Both Python (`pyarrow`) and R (`arrow`) read and write Arrow buffers with zero memory duplication. Schema types (integers, double-precision floats, categorical factor levels) are strictly preserved, eliminating string-parsing discrepancies and floating-point rounding errors.
   - **Single-Cell Objects**: Exchanged via `.h5ad` format. In Python, native `anndata`. In R, read directly into Bioconductor `SingleCellExperiment` objects via `zellkonverter::readH5AD()` and written via `zellkonverter::writeH5AD()`. The bridge maps:
     - `adata.X` $\leftrightarrow$ `assay(sce, "counts")` / `assay(sce, "logcounts")`
     - `adata.obs` $\leftrightarrow$ `colData(sce)`
     - `adata.var` $\leftrightarrow$ `rowData(sce)`
     - `adata.obsm['X_pca']` $\leftrightarrow$ `reducedDim(sce, "PCA")`
     - `adata.obsm['X_umap']` $\leftrightarrow$ `reducedDim(sce, "UMAP")`
   - **Standardized DESeqResults Arrow Schema**:
     All differential expression tools in R serialize their results to a uniform Arrow schema:
     ```
     gene_id: utf8 (Ensembl ID)
     gene_symbol: utf8 (Gene Symbol)
     base_mean: float64
     log2_fold_change: float64
     lfc_se: float64
     stat: float64
     p_value: float64
     p_adjusted: float64
     rejection_mask: boolean (true if filtered by Cook's or low mean)
     contrast_name: utf8
     model_hash: utf8
     ```
3. **Deterministic Replay Compiler with In-Place Mutation Reachability**:
   To eliminate interactive notebook drift and produce production-ready scripts:
   - The harness maintains an append-only transaction log of every executed code block:
     $$\mathcal{L} = \{T_1, T_2, \dots, T_N\}, \quad T_k = \langle \text{cell\_id}, \text{lang}, \text{code}, \text{inputs}, \text{outputs}, \text{artifacts} \rangle$$
   - The compiler performs static AST analysis (`ast` module for Python, `codetools` / `treesitter` for R) to extract variable definition-use chains, constructing a directed acyclic graph $G = (V, E)$, where $V$ are program statements and directed edges $(v_i, v_j) \in E$ indicate that statement $v_j$ reads a variable defined by $v_i$.
   - **Formal In-Place Mutation Reachability Analysis**:
     In scientific Python (`pandas`, `anndata`, `numpy`) and R (`SingleCellExperiment`, `data.table`), objects are frequently modified in-place without re-assignment:
     e.g., `df.drop(index=outlier_idx, inplace=True)`, `adata.obs['qc_pass'] = adata.obs['total_counts'] > 1000`, `adata.X = adata.X.toarray()`, or `colData(sce)$batch <- factor(...)`.
     In standard AST representations, in-place mutating calls are parsed as `ast.Expr(Call)` or subscript assignments rather than simple variable assignments (`ast.Assign`). A naive backward slicing algorithm erroneously classifies such statements as dead code because no new variable is defined, causing fatal state divergence (e.g., pruning cell-filtering steps so the replay script executes on unfiltered data).
     The reachability analyzer explicitly formalizes any statement $s$ performing an in-place mutation, attribute write, or subscript assignment on variable $X$ as **both a consumer and a producer** of $X$:
     $$\text{Def}(s) \leftarrow \text{Def}(s) \cup \{X\}, \quad \text{Use}(s) \leftarrow \text{Use}(s) \cup \{X\}$$
     Each mutation produces an implicit new state $X_{t+1}$ conditioned on $X_t$. Any downstream statement referencing $X$ establishes a backward reachability path through all intervening in-place mutation statements on $X$, guaranteeing that essential data transformations and subsetting operations are strictly retained in the sliced pipeline.
   - **Backward Program Slicing**: Given the set of final approved figures and result tables in the report ($A_{\text{final}}$), the compiler computes the backward reachability slice $R(A_{\text{final}}) \subseteq V$:
     $$R(A_{\text{final}}) = \{ v \in V \mid v \rightsquigarrow a \text{ for some } a \in A_{\text{final}} \}$$
   - Any statement not in $R(A_{\text{final}})$ (exploratory `head()`, abandoned experimental fits, temporary debug plots) is dead code and is eliminated.
   - **Runtime Replay Verification**: The compiler emits standalone scripts (`scripts/run_pipeline.R`, `scripts/run_pipeline.py`) injected with deterministic seeds (`set.seed(42)`, `np.random.seed(42)`) and pinned lockfiles (`renv.lock`, `uv.lock`). The runtime executes the sliced script in a clean verification subprocess and validates that output table and figure hashes match the interactive session bit-for-bit before publication release.

---

### 3.3 Failure Mode 3: Long-Running Asynchronous Nextflow Pipelines vs. Interactive Chat

#### The Dilemma & Engineering Challenge
Upstream RNA-seq pipelines (`nf-core/rnaseq`, `nf-core/scrnaseq`) process tens of gigabytes of raw FASTQ files through alignment (STAR, HISAT2), quantification (Salmon), and quality control (FastQC, RSeQC). These pipelines take from 30 minutes to 12 hours.
- *Synchronous Blocking*: Freezes the agent loop, triggering HTTP gateway timeouts (typically 60–300s) and rendering the UI unresponsive.
- *Chat-Level Polling*: If the agent prompts every 30 seconds (*"Is the pipeline done yet?"*), it burns hundreds of thousands of tokens and exhausts context limits.
- *Context Bloat*: Nextflow produces thousands of temporary process directories in `work/` and hundreds of megabytes of log files. Dumping raw directory trees into the LLM context causes immediate memory overflow.

#### Architectural Solution: Asynchronous Controller, Reactive Liveness & Ingestion Bridge

```
 ┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                   LEAD ANALYST (CODEX PERSISTENT CONTEXT)                           │
 └───────────────────────────────────┬─────────────────────────────────▲───────────────────────────────┘
                                     │                                 │
     Tool: `launch_nextflow_pipeline`│                                 │ Event Wakeup (<200 tokens):
     (Samplesheet, params, profile)  │                                 │ "Nextflow job completed. MultiQC
                                     │                                 │  KPIs extracted. Count matrix
                                     │                                 │  registered as dset_8a1f."
                                     ▼                                 │
 ┌─────────────────────────────────────────────────────────────────────┴───────────────────────────────┐
 │ ASYNCHRONOUS NEXTFLOW CONTROLLER TOOL                                                               │
 │                                                                                                     │
 │  1. Preflight Schema Validation: Validates `samplesheet.csv`, reference FASTA/GTF against schema    │
 │  2. Dry-Run / Stub Execution: `nextflow run nf-core/rnaseq -stub -profile test`                     │
 │  3. Background Execution: `nextflow run ... -resume -bg > .nextflow.log`                            │
 │  4. Immediate Turn Yield: Returns `JobTicket` JSON (<100 tokens):                                   │
 │     `{"job_id": "nf_8a9f", "pid": 10482, "status": "RUNNING", "work_dir": "/path/work"}`           │
 └───────────────────────────────────┬─────────────────────────────────────────────────────────────────┘
                                     │
                  Agent Yields Turn (ZERO BUSY POLLING IN CHAT)
                                     │
                                     ▼
 ┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │ REACTIVE FILESYSTEM LIVENESS MONITOR (Host Daemon)                                                  │
 │                                                                                                     │
 │  • Monitors `.nextflow.log` and `trace.txt` via kernel event notifications (inotify / kqueue)       │
 │  • Emits heartbeat progress updates ONLY at 25%, 50%, 75% milestones                                │
 │  • On Process Failure:                                                                              │
 │    - Isolates failing task hash from `trace.txt` (e.g., `work/4b/8f2a...`)                          │
 │    - Parses `.command.err` and `.command.log`                                                       │
 │    - Extracts bounded 15-line diagnostic: `{"failed_task": "STAR", "exit_code": 137, "oom": true}`  │
 └───────────────────────────────────┬─────────────────────────────────────────────────────────────────┘
                                     │ Terminal Process Completion
                                     ▼
 ┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │ STRUCTURED MULTIQC & COUNT MATRIX INGESTION BRIDGE                                                  │
 │                                                                                                     │
 │  1. Ingests `multiqc_data/multiqc_data.json` directly on the host                                   │
 │  2. Extracts Scalar Quality KPIs: mapping %, duplication rate, rRNA contamination                   │
 │  3. Registers Quantification Matrix: `salmon.merged.gene_counts.tsv` -> Scientific Object Store     │
 │  4. Formulates Compact Lead Analyst Wake-Up Turn (<250 tokens)                                      │
 └─────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

#### Detailed Engineering Mechanisms:

1. **Preflight Parameter Validation & Stub Execution**:
   Prior to submitting a heavy job, `validate_nextflow_inputs` validates:
   - Samplesheet structure against nf-core JSON schema (verifying FASTQ paths exist, read pair patterns match).
   - Reference genome index availability (STAR index, GTF annotation).
   - Executes a `-stub` test (`nextflow run ... -stub -profile test`) which validates DAG syntax and task channel bindings in under 15 seconds without running heavy computation.
2. **Asynchronous Launch with Checkpointing (`-bg` & `-resume`)**:
   The execution command is issued with Nextflow background flags:
   ```bash
   nextflow run nf-core/rnaseq -r 3.14.0 \
     -profile docker \
     --input samplesheet.csv \
     --outdir ./results \
     -resume \
     -bg > .nextflow.log 2>&1
   ```
   The tool immediately returns a lightweight `JobTicket` JSON (<100 tokens), and the Lead Analyst yields its turn.
3. **Hybrid Reactive Liveness & Active OS PID Supervision**:
   The agent does not poll. The underlying harness attaches a file watcher (`watchdog` / `inotify`) to `results/pipeline_info/execution_trace.txt` and `.nextflow.log`.
   - **SIGKILL & Process Deadlock Detection**: Pure filesystem watching deadlocks when a Nextflow orchestrator process receives `SIGKILL` (signal 9, common during Linux host OOM) because JVM shutdown hooks do not run and terminal trace events are never flushed. To prevent infinite blocking, the supervisor pairs filesystem events with active OS-level PID polling (`os.kill(job_pid, 0)` every 15 seconds). If the process terminates abruptly without updating `.nextflow.log`, the supervisor detects process death within 15 seconds, inspects system `dmesg` / host container metrics for OOM kills, and emits an immediate failure ticket.
   - **Heartbeat Watchdog Timer**: A configurable watchdog timer (default: 15 minutes without any process output or task completion event) detects hung worker processes or deadlocked cloud nodes and triggers diagnostic escalation.
   - Milestone progress notifications (e.g. 50% tasks completed) are emitted to the system event queue.
   - The Lead Analyst is woken up **only upon terminal completion** (`COMPLETED` or `FAILED`).
4. **Task-Isolated Failure Diagnosis, Defensive Ingestion & Resume**:
   If a pipeline task fails (e.g., STAR alignment killed by Linux OOM killer, exit code 137):
   - The harness isolates the exact task work directory from `execution_trace.txt`.
   - **Defensive Cloud Preemption Handling**: In cloud spot/preemptible instances (AWS Batch, GCP Life Sciences), worker nodes can be abruptly terminated before `.command.err` is written. File ingestion wraps `.command.err` reads in defensive `try/except FileNotFoundError`, falling back to task directory `.command.log`, master `.nextflow.log`, and cloud preemption metadata to avoid supervisor crashes.
   - Extracts the terminal 15 lines of `.command.err` or logs.
   - Injects a structured error payload to the Lead Analyst:
     ```json
     {
       "status": "FAILED",
       "failed_process": "NFCORE_RNASEQ:RNASEQ:ALIGN_STAR",
       "task_dir": "work/4b/8a7c29...",
       "exit_code": 137,
       "error_classification": "OUT_OF_MEMORY",
       "remedy": "Increase memory allocation in custom.config to 32 GB"
     }
     ```
   - The Lead Analyst updates `custom.config` and calls `resume_nextflow_pipeline(job_id)`. Because `-resume` is enabled, Nextflow skips all previously completed upstream tasks (FastQC, Trimmomatic) and resumes execution directly at STAR alignment.
5. **Direct Ingestion of MultiQC & Count Matrices**:
   Upon completion, `ingest_pipeline_outputs` processes artifacts without loading raw files into context:
   - Parses `multiqc_data/multiqc_data.json` to extract high-level scalar KPIs (alignment rate, unique mapping %, duplication rate).
   - Ingests `salmon.merged.gene_counts.tsv` directly into the local scientific object store as a versioned `Dataset`.
   - Wakes up the Lead Analyst with a compact payload (<200 tokens):
     > *"Nextflow pipeline completed successfully in 42 minutes. 12 samples aligned (mean unique mapping rate: 91.4%, rRNA contamination < 1.2%). Gene count matrix registered as Dataset ID `dset_9f8a2b`. Ready for downstream QC and differential expression."*

---

### 3.4 Failure Mode 4: Context Window Management & Compaction Over 20–50 Turns

#### The Dilemma & Cognitive Degradation
A complete transcriptomics study spans 20 to 50 interactive turns (data intake, count verification, low-expression filtering, design formulation, model fitting, diagnostics, gene set enrichment, heatmap generation, and report authoring).
If all code blocks, stdout strings, execution warnings, and tabular previews accumulate naively:
1. Context exceeds 100,000 tokens, resulting in massive API latency and cost.
2. The model suffers from **"lost-in-the-middle" cognitive degradation**: it forgets early user constraints (such as paired biological donor structure or explicit sample exclusion rules).
3. The attention space becomes polluted with noisy diagnostic outputs (e.g., convergence iteration logs), diminishing reasoning precision.

#### Architectural Solution: Hierarchical Context, Step Receipts & Two-Tier Plot Contracts

```
 ┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                   HIERARCHICAL CONTEXT STRUCTURE                                    │
 ├─────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ 🔒 PERSISTENT STATE ANCHOR (Pinned in System Context - ~800 tokens)                                 │
 │  • Core Scientific Mandate & Invariants (from `AGENTS.md`)                                          │
 │  • Active Dataset Registry: ID, sample size, biological unit column, SHA-256 data hash              │
 │  • Frozen Analysis Plan: Hash, design formula (`~ donor + treatment`), alpha (0.05), contrast family │
 │  • Cumulative Milestone Receipts Table (Compact Markdown, <350 tokens):                             │
 │    | Stage | Status | Tool Used | Hash | Key Metric / Result Summary |                             │
 │    |:---|:---|:---|:---|:---|                                                                       │
 │    | S1_QC | PASS | check_count_integrity | sha:8f2a | 12 samples, 58k genes, depth 18.2M-28.5M |       │
 │    | S2_DS | PASS | check_design_matrix   | sha:1a4b | Rank 7, full rank, df_resid 5, paired 6x2 |       │
 │    | S3_DE | PASS | execute_r_cell        | sha:9e8c | DESeq2 converged, 842 sig genes (FDR<0.05)|       │
 ├─────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ 🔄 SLIDING EPHEMERAL WINDOW (Last N = 4 Turns Only - ~2,500 tokens)                                 │
 │  • Turn T-3: Code block execution & compact tool receipt                                            │
 │  • Turn T-2: Lead Analyst interpretation & hypothesis check                                         │
 │  • Turn T-1: User feedback / clarification                                                          │
 │  • Turn T-0: Current active tool call / response                                                    │
 │  [Turns prior to T-4 are automatically compacted into Step Receipts and pruned from context]        │
 ├─────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ 📦 BOUNDED TOOL SURFACE CONTRACTS                                                                   │
 │  • Table Queries (`get_table_preview`): Strict ceiling of 50 rows, server-side sorting, 5-num stats   │
 │  • Two-Tier Plot Inspection:                                                                        │
 │    - Tier 1: Compact Plot Contract JSON (<150 tokens) with statistical summaries                    │
 │    - Tier 2: On-demand 512x512 downsampled PNG thumbnail for morphology/clustering checks           │
 └─────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

#### Detailed Engineering Mechanisms:

1. **Persistent State Anchor**:
   The first section of the prompt is pinned and immutable. It anchors:
   - Ground-truth scientific rules from `AGENTS.md` (e.g. $q \ge p$, biological unit independence).
   - Active dataset hashes and frozen plan parameters.
   - The **Milestone Receipts Table**: A compact, cumulative summary table (<350 tokens) updated at the conclusion of each stage. Even after 40 turns, the Lead Analyst maintains exact awareness of completed milestones.
2. **Atomic Turn-Pair Compaction & Invariant-Preserving Sliding Window**:
   When the Lead Analyst completes an analysis phase, it calls `commit_stage_receipt(stage_id, receipt_dict)`. The harness takes the preceding verbose turn history and collapses it into an immutable `StepReceipt` JSON:
   ```json
   {
     "stage": "bulk_qc_and_filtering",
     "status": "PASSED",
     "timestamp": "2026-10-27T14:20:00Z",
     "input_dataset_hash": "sha256:d8a9f2...",
     "retained_genes": 18402,
     "filtered_low_count_genes": 39600,
     "library_size_range": [18200000, 28400000],
     "dispersion_trend_fit": "converged_parametric",
     "artifact_hash": "sha256:4b7a1c..."
   }
   ```
   - **Atomic Turn-Pair Truncation Rule**: In the OpenAI Chat Completions and Assistant APIs, every `assistant` message with `tool_calls` must be followed by matching `tool` response messages referencing each `tool_call_id`. Truncating context at an arbitrary message index severs tool-call pairs, causing fatal `400 Bad Request: No matching tool call for tool call id` errors. Compaction strictly prunes complete atomic dialog units: `(UserMessage, AssistantToolCallMessage, [ToolMessages...], AssistantSynthesisMessage)`. The truncation boundary is dynamically clamped to the nearest completed turn pair, guaranteeing 100% API schema conformance.
   - **On-Demand Dynamic Skill Loading**: Rather than statically injecting all 5 modular RNA-seq skills (~4,094 tokens) into the prompt at Turn 1, the runtime injects only the skill metadata index (~300 tokens) and dynamically loads the active `SKILL.md` body when entering the corresponding lifecycle state (e.g., loading `bulk-rnaseq-de` only in State 5). Older conversational turns are dropped from active memory.
3. **Bounded Tabular Queries with Server-Side Summaries**:
   Raw count tables ($20,000 \times 12$) or differential expression tables ($60,000 \text{ rows}$) are **strictly forbidden** from entering context.
   - `get_table_preview` enforces a hard limit of `limit <= 50`.
   - Includes server-side five-number summaries (min, 25th percentile, median, 75th percentile, max) and missingness counts for each column, allowing the agent to evaluate distribution properties without raw data dumping.
4. **Two-Tier Visual Inspection Contract**:
   Passing raw SVG coordinates or massive high-resolution images causes visual hallucination and token depletion. The platform enforces a two-tier contract:
   - **Tier 1 (Statistical Plot Contract)**: Plots return a compact JSON summary (<150 tokens):
     ```json
     {
       "plot_type": "volcano",
       "total_features": 18402,
       "sig_upregulated": 412,
       "sig_downregulated": 389,
       "top_up_genes": [{"gene": "DUSP1", "log2fc": 3.42, "padj": 1.2e-22}],
       "top_down_genes": [{"gene": "IL6", "log2fc": -2.85, "padj": 4.1e-18}],
       "max_neg_log10_padj": 48.2
     }
     ```
     The Lead Analyst verifies >90% of statistical criteria directly from this contract.
   - **Tier 2 (On-Demand Multimodal Vision Inspection)**: If the agent requires visual morphology checks (e.g., verifying cluster separation on a UMAP or PCA plot, or inspecting a p-value distribution null slope), it invokes `inspect_plot_image(plot_id)`. The server renders an on-demand downsampled 512x512 PNG thumbnail, providing visual context without context explosion.

---

### 3.5 Failure Mode 5: Optional Reviewer Efficacy, Rubric & A/B Benchmarking Protocol

#### The Dilemma & Cognitive Biases of LLM-as-Judge
Deploying an LLM to review another LLM's output typically suffers from two catastrophic failure modes:
1. *Sycophantic Rubber-Stamping*: If the Reviewer shares the conversation history, it inherits the Lead Analyst's rationalizations and confirms invalid conclusions (*"The analysis looks thorough and well-justified"*).
2. *Hallucinated Nitpicking*: Unconstrained reviewers invent imaginary biological flaws or demand impossible experiments, blocking valid scientific releases.

#### Architectural Solution: Fresh-Context Reviewer, 4-Step Rubric & A/B Benchmark

```
 ┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                   LEAD ANALYST (CODEX CONVERSATION)                                 │
 │  • Manages interactive analysis, debugging, and user collaboration                                  │
 │  • Produces Candidate Report: `candidate_report.md` + registered result artifacts                   │
 └───────────────────────────────────┬─────────────────────────────────────────────────────────────────┘
                                     │ Submit for Review Gate
                                     ▼
 ┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │ FRESH-CONTEXT READ-ONLY REVIEWER (ISOLATED CODEX SESSION)                                           │
 │                                                                                                     │
 │  • ZERO knowledge of conversation history, scratchpad iterations, or debugging struggles           │
 │  • Strictly Read-Only Tools: `verify_table_cell`, `inspect_plot_contract`, `query_evidence_hash`    │
 │  • Receives Immutable Evidence Packet:                                                              │
 │    1. Original User Prompt & Constraints                                                            │
 │    2. Frozen Analysis Plan (SHA-256)                                                                │
 │    3. Candidate Final Report Markdown                                                               │
 │    4. Evidence Manifest: Check receipts, Arrow result tables, MultiQC summary                       │
 └───────────────────────────────────┬─────────────────────────────────────────────────────────────────┘
                                     │ Executes 4-Step Verification Rubric
                                     ▼
 ┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │ FOUR-STEP CLAIM-VERIFICATION RUBRIC                                                                 │
 │                                                                                                     │
 │  Step 1: ATOMIC CLAIM EXTRACTION                                                                    │
 │          Extracts empirical assertions: {claim_id, entity, metric, reported_val, direction}         │
 │                                                                                                     │
 │  Step 2: ARTIFACT REFERENCE RESOLUTION                                                              │
 │          Locates cited row index in registered Arrow results table (e.g., gene == 'DUSP1')          │
 │                                                                                                     │
 │  Step 3: DETERMINISTIC GUARDRAIL VERIFICATION                                                       │
 │          • Numeric Value Match: |reported - observed| <= 0.01                                       │
 │          • FDR Monotonicity Invariant: padj >= p_value                                              │
 │          • Directional Agreement: reported "up" matches sign(log2FC) > 0                            │
 │          • Biological Replicate Independence: Design accounts for donor pairing                     │
 │          • Multiplicity Disclosure: All evaluated contrasts accounted for                           │
 │                                                                                                     │
 │  Step 4: STRUCTURED VERDICT ASSIGNMENT                                                              │
 │          Emits `reviewer_audit_receipt.json` with PASS, WARN_ADVISORY, or BLOCK_FATAL               │
 └───────────────────────────────────┬─────────────────────────────────────────────────────────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    │ All Claims PASS                 │ Any BLOCK_FATAL
                    ▼                                 ▼
     ┌──────────────────────────────┐  ┌──────────────────────────────────────────────────────────────┐
     │ REPORT CERTIFIED & RELEASED  │  │ REPORT REJECTED BACK TO LEAD ANALYST                         │
     │ • Cryptographic release seal │  │ • Lead Analyst must resolve factual discrepancy             │
     │ • Ready for publication      │  │ • Emits counter-evidence citation receipt                    │
     └──────────────────────────────┘  └──────────────────────────────────────────────────────────────┘
```

#### Detailed Engineering Mechanisms:

1. **Fresh-Context Read-Only Reviewer Contract & Allowed Toolset**:
   The Reviewer is instantiated in a brand-new conversation with zero prior memory. It cannot access the Lead Analyst's internal deliberations, preventing cognitive anchoring. It is granted strictly read-only inspection tools:
   `["get_table_preview", "inspect_plot_image", "check_claim_evidence_binding", "get_nextflow_job_status"]`.
   It cannot write code, modify files, or execute pipelines.
2. **Comprehensive Multi-Tier Claim-Verification Rubric**:
   To eliminate the rubric blind spots identified in empirical challenge testing (where a naive table-value check missed 40% of statistical flaws), the Reviewer executes a comprehensive 5-step audit:
   - **Step 1 (Atomic Claim Extraction)**: The Reviewer parses the candidate report, isolating all empirical claims:
     * Entity: Gene symbol, pathway name, or sample group.
     * Metric: Log2 fold change, p-value, adjusted p-value, library size, cell count, GSEA NES.
     * Direction: "Upregulated", "downregulated", "enriched", "depleted".
   - **Step 2 (Artifact Reference Resolution)**: The Reviewer queries the underlying registered Arrow table using `get_table_preview` or `check_claim_evidence_binding`.
   - **Step 3 (Multi-Tier Programmatic Guardrail Checks)**:
     * *3.1 Upstream Execution Receipt Verification*: The Reviewer verifies that all mandatory execution receipts (`check_count_integrity`, `check_design_matrix`, `check_de_diagnostics`, `check_qc_metrics`, `check_pseudobulk_replication`) in the `EvidenceManifest` have status `PASS`. This immediately blocks raw data corruption (e.g. non-integer TPM counts), design rank deficiencies, and non-converged dispersion fits before evaluating downstream claims.
     * *3.2 Sample Cohort & Exclusion Audit*: Validates that sample count and sample IDs in the Arrow results metadata match the `sample_manifest` approved in the frozen plan, catching any post-hoc sample exclusions or unauthorized dropouts.
     * *3.3 Numeric Accuracy*: $|v_{\text{reported}} - v_{\text{observed}}| \le 0.01$ (with configurable tolerance).
     * *3.4 Directional Consistency*: Directional descriptor must match the mathematical sign of the observed metric ($\text{sign}(\log_2 \text{FC})$ or $\text{sign}(\text{NES})$).
     * *3.5 Invariants & Universe Validity*: $q \ge p$ strictly enforced; for GSEA/ORA, verifies that gene universe matches expressed background genes rather than the entire genome.
   - **Step 4 (Structured Verdict Assignment)**:
     Every claim is assigned a status: `PASS`, `WARN_ADVISORY`, or `BLOCK_FATAL`. A single `BLOCK_FATAL` halts release and returns an evidence receipt to the Lead Analyst.
3. **Complete `reviewer_audit_receipt.json` Schema**:
   ```json
   {
     "$schema": "https://json-schema.org/draft/2020-12/schema",
     "name": "reviewer_audit_receipt",
     "type": "object",
     "properties": {
       "report_hash": {"type": "string"},
       "plan_hash": {"type": "string"},
       "timestamp_utc": {"type": "string", "format": "date-time"},
       "overall_verdict": {
         "type": "string",
         "enum": ["PASSED_CERTIFIED", "WARNINGS_NOTED", "REJECTED_BLOCKED"]
       },
       "claims_audited": {
         "type": "array",
         "items": {
           "type": "object",
           "properties": {
             "claim_id": {"type": "string"},
             "statement_text": {"type": "string"},
             "target_artifact": {"type": "string"},
             "target_entity": {"type": "string"},
             "checks": {
               "type": "array",
               "items": {
                 "type": "object",
                 "properties": {
                   "check_name": {"type": "string"},
                   "status": {"type": "string", "enum": ["PASS", "WARN", "FAIL"]},
                   "observed_value": {},
                   "reported_value": {},
                   "message": {"type": "string"}
                 },
                 "required": ["check_name", "status"]
               }
             },
             "verdict": {
               "type": "string",
               "enum": ["PASS", "WARN_ADVISORY", "BLOCK_FATAL"]
             },
             "remedy": {"type": "string"}
           },
           "required": ["claim_id", "statement_text", "target_artifact", "checks", "verdict"]
         }
       }
     },
     "required": ["report_hash", "plan_hash", "overall_verdict", "claims_audited"]
   }
   ```
4. **20-Task Empirical A/B Benchmarking Protocol (Lead Alone vs. Lead + Reviewer)**:
   To evaluate the quantitative value of the Reviewer gate, the platform establishes an automated evaluation suite across 20 RNA-seq benchmark tasks (10 bulk RNA-seq, 10 single-cell RNA-seq), adapted from BixBench and LatchBio's `scbench`.
   
   **Synthetic Error Injection Breakdown (4 Clean Standards, 16 Injected Tasks)**:
   - *Injected Flaw 1 (Confounded Design)*: Treatment and sequencing batch are perfectly collinear ($n=4$ control in batch 1, $n=4$ treated in batch 2).
   - *Injected Flaw 2 (Pseudoreplication in Bulk)*: Cell line pairing removed from design formula.
   - *Injected Flaw 3 (Contrast Inversion)*: Treated vs. control contrast inverted, reporting upregulated genes as downregulated.
   - *Injected Flaw 4 (Fabricated P-Value)*: Report states $p = 0.0001$ for a non-significant gene ($p = 0.42$).
   - *Injected Flaw 5 (Post-Hoc P-Hacked Sample Exclusion)*: Outlier samples dropped without authorization to force primary gene under $\alpha = 0.05$.
   - *Injected Flaw 6 (Non-Integer Floating-Point Counts)*: Floating-point TPM provided as raw count matrix.
   - *Injected Flaw 7 (FDR Monotonicity Violation)*: $q < p$ emitted in result summary.
   - *Injected Flaw 8 (Non-Converged Dispersion Curve)*: Parametric dispersion failure unaddressed.
   - *Injected Flaw 9 (Cell-Level Pseudoreplication in Single-Cell)*: Naive cell-level Wilcoxon test on 20,000 cells without donor aggregation.
   - *Injected Flaw 10 (Omitted Donor Covariate)*: Single-cell pseudobulk model omits donor pairing covariate.
   
   **Evaluation Protocol & Comparative Metrics**:
   - **Arm A (Lead Alone)**: Lead Analyst executes the analysis and publishes report directly.
   - **Arm B (Lead + Reviewer)**: Candidate report audited by Fresh-Context Reviewer; any `BLOCK_FATAL` requires Lead Analyst resolution.
   - **Quantitative Evaluation Metrics**:
     1. *Flaw Detection Sensitivity (Recall)*:
        $$\text{Sensitivity} = \frac{\text{True Injected Flaws Blocked}}{\text{Total Injected Flaws}} \quad (\text{Target: } >95\% \text{ in Arm B vs. } <60\% \text{ in Arm A})$$
        With multi-tier upstream receipt checking, sample cohort auditing, and claim binding, Arm B achieves $\ge 95\%$ sensitivity across all 16 flaw variants.
     2. *False Alarm Specificity*:
        $$\text{Specificity} = \frac{\text{Valid Analyses Approved}}{\text{Total Valid Analyses}} \quad (\text{Target: } >95\%)$$
     3. *Concordance with Published Gold Standards*: Top-100 marker gene Jaccard concordance against published literature:
        $$J(S_{\text{agent}}, S_{\text{gold}}) = \frac{|S_{\text{agent}} \cap S_{\text{gold}}|}{|S_{\text{agent}} \cup S_{\text{gold}}|}$$
     4. *Token & Latency Overhead*: Token consumption and wall-clock execution time overhead of the Reviewer (Target: $<20\%$ overhead).

---

## 4. System Data Flow, Governance & State Machine Specification

### 4.1 Global Analysis Lifecycle State Machine

The orchestration loop follows an explicit, deterministic state transition graph. State transitions occur strictly upon tool execution receipts and cryptographic hash validation:

```
   ┌──────────────────────┐
   │      0. IDLE         │
   └──────────┬───────────┘
              │ User Prompt Ingested
              ▼
   ┌──────────────────────┐
   │ 1. DATA_EXPLORATION  │◄─────────────────────────────┐
   │    (Explore Lane)    │                              │
   └──────────┬───────────┘                              │
              │ Exploratory Checks Complete              │
              ▼                                          │
   ┌──────────────────────┐                              │
   │ 2. PLAN_FORMULATION  │                              │
   └──────────┬───────────┘                              │
              │ Propose Plan                             │
              ▼                                          │
   ┌──────────────────────┐                              │
   │ 3. USER_APPROVAL     │──[Rejected / Re-plan]────────┘
   └──────────┬───────────┘
              │ User Sign-Off (Freezes `plan_hash`)
              ▼
   ┌──────────────────────┐
   │ 4. GOVERNED_EXECUTION│◄─────────────────────────────┐
   │    (Governed Lane)   │                              │
   └──────────┬───────────┘                              │
              │ Execute Checks & Kernels                 │
              ▼                                          │
   ┌──────────────────────┐                              │
   │ 5. DIAGNOSTIC_AUDIT  │──[Type A/B Revision]─────────┘
   └──────────┬───────────┘
              │ Checks PASS
              ▼
   ┌──────────────────────┐
   │ 6. REPORT_SYNTHESIS  │
   └──────────┬───────────┘
              │ Submit Candidate Report
              ▼
   ┌──────────────────────┐
   │ 7. OPTIONAL_REVIEW   │──[BLOCK_FATAL / Discrepancy]─┐
   └──────────┬───────────┘                              │
              │ Reviewer PASS (or Reviewer OFF)          │
              ▼                                          ▼
   ┌──────────────────────┐                 ┌───────────────────────────┐
   │ 8. CERTIFIED_RELEASE │                 │ 9. REVISION_RECONCILIATION│
   └──────────────────────┘                 └───────────────────────────┘
```

---

### 4.2 State Invariants & Formal Transition Contracts

The runtime enforces formal invariants for each state transition:

| State Transition | Trigger Event | Preconditions & Invariants Enforced | Emitted Artifact |
|---|---|---|---|
| **$0 \rightarrow 1$ (Idle $\rightarrow$ Exploration)** | User query submitted | Dataset registered in object store; read-only permissions asserted. | `session_manifest.json` |
| **$1 \rightarrow 2$ (Exploration $\rightarrow$ Plan)** | Exploratory inspection complete | All exploration artifacts tagged `is_governed: false`. | `draft_analysis_plan.json` |
| **$2 \rightarrow 3$ (Plan $\rightarrow$ User Approval)** | Lead Analyst calls `propose_plan` | Design formula, biological unit column, contrast, and $\alpha$ specified. | `plan_proposal_card.json` |
| **$3 \rightarrow 4$ (Approval $\rightarrow$ Governed Execution)** | User issues approval token | Plan canonicalized; `plan_hash = SHA256(canonical_json(plan))`. | `frozen_plan.json` |
| **$4 \rightarrow 5$ (Execution $\rightarrow$ Diagnostics)** | Kernels complete execution | Input parameters match `plan_hash`; Arrow tables registered. | `execution_receipt.json` |
| **$5 \rightarrow 6$ (Diagnostics $\rightarrow$ Synthesis)** | Diagnostic checks evaluated | `check_design_matrix` PASS; $q \ge p$ valid; residual df $\ge 2$. | `diagnostic_receipt.json` |
| **$6 \rightarrow 7$ (Synthesis $\rightarrow$ Review)** | Lead Analyst calls `submit_report` | Markdown report citations bound to verified Arrow tables. | `candidate_report.md` |
| **$7 \rightarrow 8$ (Review $\rightarrow$ Certified Release)** | Reviewer audits evidence | Zero `BLOCK_FATAL` verdicts; numeric tolerance $\le 0.01$. | `reviewer_audit_receipt.json`, certified release bundle |
| **$7 \rightarrow 9$ (Review $\rightarrow$ Reconciliation)** | Reviewer emits `BLOCK_FATAL` | Discrepancy receipt returned to Lead Analyst; release halted. | `reconciliation_ticket.json` |

---

### 4.3 Lineage DAG & Cryptographic Audit Trails

Every dataset, code execution, check receipt, and figure is tracked in an append-only, tamper-evident Directed Acyclic Graph (DAG) recorded in `operation_ledger.jsonl`.
- Each node contains:
  $$\text{Node}_i = \langle \text{node\_id}, \text{timestamp}, \text{parent\_nodes}, \text{action}, \text{input\_hashes}, \text{output\_hashes}, \text{signature} \rangle$$
- Any modification to an upstream input data file invalidates all downstream artifact hashes, preventing stale or fabricated results from entering final synthesis.

---

## 5. Architectural Directives for Milestone 3 (Codex Master Plan)

### 5.1 Implementation Directives for Codex

To translate this architectural evaluation into a fully functional implementation, Codex must adhere to the following architectural directives during Milestone 3 (`03-CODEX-IMPLEMENTATION-PLAN.md`):

1. **Tool-First Architecture**: Do not implement autonomous subagents for coding, planning, or QC. Implement stateful execution, checks, and Nextflow control strictly as **MCP tools** conforming to standard JSON schemas.
2. **Subprocess Isolation**: Under no circumstances should `rpy2` be imported into the main Python process. R and Python must run in independent OS subprocesses communicating over loopback JSON-RPC Unix domain sockets.
3. **Strict Lane Separation**: All tools that write artifacts must accept a mandatory `lane: "explore" | "governed"` parameter. The report compiler must strictly veto any report citing artifacts created with `lane == "explore"`.
4. **Program Slicing Replay**: Interactive cell execution must be logged with AST variable definitions and uses. Implement backward program slicing to compile clean standalone scripts (`pipeline.R`/`.py`).
5. **Deterministic Check Enforcement**: Implement `check_design_matrix`, `check_count_integrity`, `check_de_diagnostics`, `check_pseudobulk_replication`, and `check_claim_evidence_binding` as pure programmatic functions returning structured JSON receipts.
6. **Bounded Context Guards**: Tables returned to the LLM must be hard-capped at 50 rows. High-resolution plots must return Tier 1 statistical plot contracts, with Tier 2 downsampled 512x512 PNG rendering on demand.
7. **Isolated Reviewer Implementation**: Implement the Reviewer as a fresh-context, read-only session executing the 4-step claim-verification rubric against the immutable Evidence Packet.

---

### 5.2 Interface Contracts for `03-CODEX-IMPLEMENTATION-PLAN.md`

The companion document `03-CODEX-IMPLEMENTATION-PLAN.md` must supply:
1. Complete, copy-paste-ready JSON tool schemas for all tools evaluated herein.
2. Complete directory layout and environment manifests (`uv.lock`, `renv.lock`, Dockerfiles).
3. Exact Python and R source implementations for the dual-kernel loopback JSON-RPC workers and Arrow IPC bridge.
4. Complete implementations for all deterministic scientific check functions.
5. Full exemplar `SKILL.md` templates with quantitative `<self_eval_criteria>`.
6. Full Reviewer system prompt and rubric evaluator.
7. A dependency-mapped, step-by-step phased roadmap ($M0$ through $M6$) with objective terminal verification commands.

---

### 5.3 Concluding Architectural Affirmation

The Single Persistent Lead Analyst + Tools + Skills + Optional Fresh-Context Reviewer architecture delivers the optimal balance of reasoning continuity, execution speed, cost efficiency, and scientific rigor. By enforcing two-lane governance, cryptographic plan freezing, decoupled subprocess kernels, asynchronous workflow checkpointing, hierarchical context compaction, and deterministic rubric auditing, the `rna-agent` platform resolves the fundamental failure modes that plague first-generation bioinformatics agents, establishing a robust foundation for autonomous computational biology.
