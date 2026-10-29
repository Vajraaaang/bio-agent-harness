# Master Codex Implementation Plan: `rna-agent` Autonomous RNA-Seq Platform

**Document Identifier**: `docs/rna-agent-codex-plan/03-CODEX-IMPLEMENTATION-PLAN.md`  
**Milestone**: M3 — Master Codex Implementation Plan  
**Status**: Publication-Grade, Complete, Copy-Paste-Ready Master Specification  
**Author**: `worker_m3` (Teamwork Preview Engineering Worker)  
**Target Engine**: OpenAI Codex / Autonomous Implementation Agents  
**Reference Framework**: `AGENTS.md` (DevBench Staged Runtime), `01-DEEP-RESEARCH-LATCHBIO-AND-AGENTS.md`, `02-ARCHITECTURE-EVALUATION.md`  
**Date**: October 2026  

---

## Executive Blueprint & Master Architecture

This document provides the definitive, production-grade implementation blueprint for `rna-agent`—an autonomous, statistically governed RNA-seq analysis platform engineered for direct execution by Codex. 

The architecture is built upon the **Single Persistent Lead Analyst** paradigm:
1. A single persistent Codex conversation manages the global scientific discovery loop (`Chat → inspect data → propose plan → user approval → run Python/R → inspect results and run checks → revise as needed → optional review → report`).
2. Heavy computation and statistical inference are delegated strictly to **deterministic tools**: persistent dual-language execution kernels (Python and R), deterministic programmatic scientific checks, and asynchronous Nextflow (`nf-core/rnaseq`, `nf-core/scrnaseq`) runners.
3. Domain expertise, analytical protocols, and quality thresholds are structured as modular **RNA-seq skills** (`skills/`) incorporating Latch-style quantitative `<self_eval_criteria>` numeric guardrails.
4. Final scientific synthesis is audited by an **optional fresh-context read-only Reviewer** that executes a deterministic 4-step claim-evidence verification rubric before report publication.
5. All operations are governed by a **Two-Lane Execution Architecture** separating exploratory sandboxing from cryptographically pre-registered, auditable evidence generation anchored to a SHA-256 lineage DAG.

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                       RNA-AGENT MASTER ARCHITECTURE                                    │
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
 │    • check_qc_metrics (Mapping, rRNA, duplication)    │                         │
 │    • check_de_diagnostics (Dispersion, Cook's, KS)    │                         │
 │    • check_pseudobulk_replication (Donor grouping)    │                         │
 │    • check_claim_evidence_binding (Value/FDR match)   │                         │
 │                                                       │                         │
 │ 3. Asynchronous Workflow Orchestration:               │                         │
 │    • Nextflow Controller (Stub check, async launch)   │                         │
 │    • Reactive Trace Monitor (Zero token polling)      │                         │
 │    • MultiQC KPI & Count Matrix Ingestor              │                         │
 │                                                       │                         │
 │ 4. Visual & Data Inspection Tools:                    │                         │
 │    • Bounded Table Pager (<50 rows, 5-number summary) │                         │
 │    • Two-Tier Plot Inspector (Contract + 512px thumb) │                         │
 └───────────────────┬───────────────────────────────────┘                         │
                     │ Emits Audited Artifacts & Evidence Receipts                 │
                     ▼                                                             ▼
 ┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │ CRYPTOGRAPHIC EVIDENCE DAG & OPERATION LEDGER (SQLite / Merkle Tree)                                 │
 │  - Pre-Registered Plan Hash (SHA-256)        - Step Execution Receipts (Pass/Warn/Block)             │
 │  - Backward Program Slice (Clean Script)     - Verified Publication Release Bundle                   │
 └──────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. System Architecture & Repository Layout

### 1.1 Complete Repository Layout & Directory Tree

The following tree represents the complete, fully specified file layout of the `rna-agent` platform. Every file listed serves an unambiguous, modular role in the architecture.

```
bio-agent-harness/
├── AGENTS.md                                # Authoritative DevBench scientific runtime rules
├── codex.json                               # Master Codex MCP client configuration
├── pyproject.toml                           # Python project metadata, dependencies, and tool settings
├── uv.lock                                  # Pinned deterministic Python dependency lockfile
├── renv.lock                                # Pinned deterministic R / Bioconductor package lockfile
├── install_bioc.R                           # Bioconductor environment initialization script
├── docker-compose.yml                       # Multi-container orchestration (Python, R, Nextflow)
├── docker/
│   ├── Dockerfile.python                    # Sandboxed Python 3.11 execution container
│   ├── Dockerfile.r                         # Sandboxed R 4.3.3 / Bioconductor 3.18 container
│   └── Dockerfile.nextflow                  # Nextflow / nf-core execution controller container
├── rna_agent/
│   ├── __init__.py                          # Package root
│   ├── config.py                            # Platform settings, path resolutions, timeouts
│   ├── server.py                            # MCP Server entrypoint (stdio / SSE transport)
│   ├── core/
│   │   ├── __init__.py
│   │   ├── state_machine.py                 # 9-stage analysis lifecycle controller
│   │   ├── context_manager.py               # Hierarchical context compaction (Anchor + Sliding Window)
│   │   ├── ledger.py                        # SQLite operation ledger & hypothesis tracker
│   │   └── dag.py                           # SHA-256 Merkle DAG lineage tracker
│   ├── kernels/
│   │   ├── __init__.py
│   │   ├── base.py                          # Abstract kernel worker interface
│   │   ├── python_worker.py                 # Subprocess Python kernel (JSON-RPC over loopback)
│   │   ├── r_worker.py                      # Subprocess R kernel controller
│   │   ├── r_worker.R                       # Standalone R loopback JSON-RPC server script
│   │   ├── bridge.py                        # Arrow IPC streaming & zellkonverter H5AD/SCE bridge
│   │   └── supervisor.py                    # Process lifecycle supervisor & crash recovery
│   ├── compiler/
│   │   ├── __init__.py
│   │   ├── ast_tracer.py                    # Python AST & R codetools dependency tracer
│   │   ├── dependency_graph.py              # Symbol def-use graph builder
│   │   └── replay_compiler.py               # Backward program slicer emitting clean standalone scripts
│   ├── checks/
│   │   ├── __init__.py
│   │   ├── base.py                          # Abstract check interface and Receipt builder
│   │   ├── design.py                        # check_design_matrix (QR rank, df, confounding, pairing)
│   │   ├── counts.py                        # check_count_integrity (non-negative integer, NaNs, zeros)
│   │   ├── qc_metrics.py                    # check_qc_metrics (library sizes, rRNA %, duplication %)
│   │   ├── diagnostics.py                   # check_de_diagnostics (dispersion fit, Cook's, KS test)
│   │   ├── pseudobulk.py                    # check_pseudobulk_replication (single-cell donor aggregation)
│   │   └── claim_binding.py                 # check_claim_evidence_binding (numeric match, FDR monotonicity)
│   ├── nextflow/
│   │   ├── __init__.py
│   │   ├── validator.py                     # Preflight schema & samplesheet validator
│   │   ├── runner.py                        # Async background daemon launcher (-bg, -resume)
│   │   ├── monitor.py                       # Filesystem trace & .nextflow.log watcher
│   │   └── ingest.py                        # MultiQC JSON parser & count matrix registrar
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── dispatcher.py                    # MCP tool registration, schema generation, sandboxing
│   │   ├── kernel_tools.py                  # execute_python_cell, execute_r_cell, export_reproducible_pipeline
│   │   ├── check_tools.py                   # MCP wrappers for deterministic scientific checks
│   │   ├── nextflow_tools.py                # MCP wrappers for Nextflow validation, launch, status, ingest
│   │   └── visual_tools.py                  # get_table_preview, inspect_plot_image
│   ├── reviewer/
│   │   ├── __init__.py
│   │   ├── agent.py                         # Fresh-context Reviewer session runner
│   │   ├── prompt.py                        # Strict Reviewer system prompt contract
│   │   ├── packet_builder.py                # Evidence Packet assembler (hashes, receipts, tables)
│   │   ├── claim_extractor.py               # NLP/AST parser extracting atomic empirical assertions
│   │   └── rubric.py                        # 4-step claim verification engine emitting audit receipts
│   └── skills/
│       ├── __init__.py
│       ├── engine.py                        # Skill loader & Markdown parser
│       └── eval_parser.py                   # Programmatic <self_eval_criteria> threshold validator
├── skills/
│   ├── bulk-rnaseq-de/
│   │   ├── SKILL.md                         # Bulk DE playbook with quantitative <self_eval_criteria>
│   │   ├── steps/
│   │   │   ├── 01_intake_and_qc.md          # Step 1: Count integrity, library sizes, RLE size factors
│   │   │   ├── 02_design_and_fit.md         # Step 2: Formula, rank validation, DESeq2 GLM fit
│   │   │   └── 03_shrinkage_and_export.md   # Step 3: apeglm/ashr shrinkage, independent filtering
│   │   └── scripts/
│   │       └── deseq2_standard.R            # Reusable R template for DESeq2 execution
│   ├── scrnaseq-core/
│   │   ├── SKILL.md                         # Single-cell playbook with quantitative <self_eval_criteria>
│   │   ├── steps/
│   │   │   ├── 01_filtering_and_qc.md       # Step 1: Doublet detection, mito/ribo filtering
│   │   │   ├── 02_dimred_and_clusters.md    # Step 2: Highly variable genes, PCA, Harmony, Leiden
│   │   │   └── 03_pseudobulk_de.md          # Step 3: Donor-level aggregation, edgeR/DESeq2 pseudobulk
│   │   └── scripts/
│   │       └── pseudobulk_aggr.py           # AnnData to pseudobulk Arrow aggregation script
│   ├── pathway-enrichment/
│   │   ├── SKILL.md                         # GSEA & ORA playbook with quantitative <self_eval_criteria>
│   │   └── steps/
│   │       ├── 01_ora_clusterprofiler.md    # Over-representation analysis (GO, KEGG, Reactome)
│   │       └── 02_gsea_preranked.md         # Fast pre-ranked GSEA with fgsea and sign verification
│   ├── nfcore-rnaseq-orchestration/
│   │   ├── SKILL.md                         # nf-core/rnaseq execution playbook with self-check criteria
│   │   └── steps/
│   │       ├── 01_samplesheet_prep.md       # Sample sheet syntax, strandedness, paired-end checks
│   │       └── 02_launch_and_monitor.md     # Async launch, profile selection, trace inspection
│   └── figure-synthesis-report/
│       ├── SKILL.md                         # Publication figure synthesis & report generation
│       └── steps/
│           ├── 01_volcano_and_ma.md         # Volcano & MA plot contracts and rendering
│           ├── 02_complex_heatmap.md        # ComplexHeatmap sample clustering & z-score scaling
│           └── 03_report_assembly.md        # Markdown report generation with evidence anchors
├── benchmarks/
│   ├── __init__.py
│   ├── airway_benchmark.py                  # Automated reproduction of Love et al. 2015 Airway
│   ├── ab_evaluation.py                     # 20-task A/B benchmark (Lead Alone vs Lead + Reviewer)
│   ├── synthetic_injectors.py               # Systematic flaw injection (pseudorep, confounding, etc.)
│   └── fixtures/                            # Test count matrices, metadata, and ground-truth statistics
└── tests/
    ├── conftest.py                          # Pytest fixtures, mock processes, test datasets
    ├── test_kernels.py                      # Subprocess kernel lifecycle & Arrow bridge unit tests
    ├── test_scientific_checks.py            # Unit tests for all 6 deterministic check tools
    ├── test_nextflow_runner.py              # Nextflow validator, async launcher, and ingestion tests
    ├── test_data_visual_tools.py            # Bounded table preview & plot contract inspection tests
    ├── test_skills_engine.py                # Skill parser & <self_eval_criteria> threshold validator tests
    ├── test_reviewer.py                     # Reviewer rubric & synthetic flaw detection tests
    └── test_end_to_end_orchestration.py     # Full integration tests for the 9-stage lifecycle
```

---

### 1.2 Component Boundaries & IPC Interfaces

To ensure resilience, modularity, and total immunity against fatal language crashes, the platform isolates components across explicit process boundaries:

1. **Lead Analyst Context (Codex)**:
   - Operates as the interactive central intelligence.
   - Communicates with the platform host solely via standard MCP JSON-RPC over `stdio` (or SSE).
   - Has zero direct OS access; cannot execute arbitrary shell commands or access filesystem paths outside the sandboxed workspace.

2. **Host Supervisor & MCP Server (`rna_agent/server.py`)**:
   - Manages MCP tool routing, parameter validation (via `pydantic.TypeAdapter`), workspace path sandboxing, and execution receipt capture.
   - Maintains the persistent `StateMachine`, `OperationLedger`, and `DAGTracker`.

3. **Subprocess Python Kernel Worker (`rna_agent/kernels/python_worker.py`)**:
   - Runs as an independent OS subprocess executing Python 3.11.
   - Listens on a dedicated Unix domain socket (`/tmp/rna_agent_py.sock`) using loopback JSON-RPC.
   - Memory space contains `Scanpy`, `AnnData`, `PyDESeq2`, `DuckDB`, and `NumPy`.

4. **Subprocess R Kernel Worker (`rna_agent/kernels/r_worker.py`)**:
   - Runs as an independent OS subprocess executing R 4.3.3.
   - Listens on a dedicated loopback TCP port (default `9876`) using a lightweight JSON-RPC loop.
   - Memory space contains `DESeq2`, `edgeR`, `limma`, `BiocParallel`, `ComplexHeatmap`, and `arrow`.
   - **Isolation Guarantee**: If an R package triggers a C-level segmentation fault (`SIGSEGV`, exit code 139), only the R child process terminates. The Python MCP server detects worker death, logs the error, and respawns a clean R worker without crashing the Lead Analyst session.

5. **Cross-Language Zero-Copy IPC Bridge (`rna_agent/kernels/bridge.py`)**:
   - **Tabular & Count Matrices**: Exchanged using **Apache Arrow IPC streaming** (`.arrow` Feather files). Zero-copy read/write in Python (`pyarrow`) and R (`arrow`). Preserves exact 64-bit integer counts, floating-point statistics, and categorical factor levels without text serialization.
   - **Single-Cell AnnData / SingleCellExperiment**: Serialized via HDF5 (`.h5ad`). In Python, native `anndata.read_h5ad()`. In R, imported via Bioconductor's `zellkonverter::readH5AD()` into a `SingleCellExperiment` and vice versa, preserving `.X` counts, `.obs` metadata, `.var` gene annotations, and `.obsm` PCA/UMAP embeddings.

6. **Asynchronous Nextflow Execution Engine (`rna_agent/nextflow/`)**:
   - Nextflow runs as an independent detached background process (`-bg`).
   - Communication is asynchronous: the tool returns an immutable `JobTicket` immediately. Status and liveness are monitored via filesystem trace logs (`trace.txt`, `.nextflow.log`), waking the agent only on milestones or termination.

---

### 1.3 Runtime Containers & Environment Specifications

To ensure exact reproducibility across machines, `rna-agent` specifies three dedicated container runtime environments:

#### 1. Python Analysis Container (`docker/Dockerfile.python`)
```dockerfile
FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libxml2-dev \
    libcurl4-openssl-dev \
    libssl-dev \
    libdeflate-dev \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:0.4.20 /uv /bin/uv

WORKDIR /workspace

# Install pinned Python dependencies via two-step compile and install
COPY pyproject.toml uv.lock ./
RUN uv pip compile pyproject.toml -o requirements.txt && \
    uv pip install --no-cache -r requirements.txt

# Create non-root user
RUN useradd -m -u 1000 bioanalyst && chown -R bioanalyst:bioanalyst /workspace
USER bioanalyst

CMD ["python", "-m", "rna_agent.server"]
```

#### 2. R Bioconductor Container (`docker/Dockerfile.r`)
```dockerfile
FROM bioconductor/bioconductor_docker:RELEASE_3_18

ENV DEBIAN_FRONTEND=noninteractive \
    R_REMOTES_NO_ERRORS_FROM_WARNINGS=true

RUN apt-get update && apt-get install -y --no-install-recommends \
    libfontconfig1-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libfreetype6-dev \
    libpng-dev \
    libtiff5-dev \
    libjpeg-dev \
    cmake \
    libarrow-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Install pinned Bioconductor and CRAN packages
COPY renv.lock install_bioc.R ./
RUN Rscript install_bioc.R

# Install R loopback worker entrypoint
RUN mkdir -p /app
COPY rna_agent/kernels/r_worker.R /app/r_worker.R

# Create non-root user matching host UID
RUN useradd -m -u 1000 rworker && chown -R rworker:rworker /workspace /app
USER rworker

CMD ["Rscript", "/app/r_worker.R"]
```

#### 3. Core Dependency Manifests

**Python Dependencies (`pyproject.toml`)**:
```toml
[project]
name = "rna-agent"
version = "1.0.0"
description = "Autonomous, Statistically Governed RNA-Seq Analysis Platform"
requires-python = ">=3.11"
dependencies = [
    "pydantic>=2.7.0",
    "mcp>=0.1.0",
    "pyarrow>=15.0.0",
    "anndata>=0.10.7",
    "scanpy>=1.10.1",
    "pydeseq2>=0.4.10",
    "duckdb>=0.10.2",
    "numpy>=1.26.4",
    "scipy>=1.13.0",
    "pandas>=2.2.2",
    "scikit-learn>=1.4.2",
    "matplotlib>=3.8.4",
    "seaborn>=0.13.2",
    "plotly>=5.20.0",
    "kaleido>=0.2.1",
    "jsonschema>=4.21.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.1.1",
    "pytest-asyncio>=0.23.6",
    "ruff>=0.4.1",
    "mypy>=1.9.0",
]
```

**R Dependencies (`install_bioc.R`)**:
```R
options(repos = c(CRAN = "https://cloud.r-project.org"))
if (!requireNamespace("BiocManager", quietly = TRUE))
    install.packages("BiocManager")

BiocManager::install(version = "3.18", ask = FALSE)

packages <- c(
    "DESeq2",
    "edgeR",
    "limma",
    "BiocParallel",
    "apeglm",
    "ashr",
    "zellkonverter",
    "SingleCellExperiment",
    "arrow",
    "ComplexHeatmap",
    "clusterProfiler",
    "org.Hs.eg.db",
    "org.Mm.eg.db",
    "fgsea",
    "jsonlite",
    "codetools"
)

BiocManager::install(packages, update = FALSE, ask = FALSE)
```

#### 4. Nextflow Pipeline Controller Container (`docker/Dockerfile.nextflow`)
```dockerfile
FROM eclipse-temurin:17-jdk-jammy

ENV DEBIAN_FRONTEND=noninteractive \
    NXF_VER=23.10.1 \
    NXF_MODE=google \
    JAVA_HOME=/opt/java/openjdk

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    bash \
    procps \
    ca-certificates \
    graphviz \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Install Nextflow binary
RUN curl -s https://get.nextflow.io | bash && \
    mv nextflow /usr/local/bin/ && \
    chmod 755 /usr/local/bin/nextflow

WORKDIR /workspace

# Create non-root runner user with docker group mapping
RUN useradd -m -u 1000 nxfuser && \
    chown -R nxfuser:nxfuser /workspace
USER nxfuser

CMD ["nextflow", "info"]
```

#### 5. Multi-Container Orchestration (`docker-compose.yml`)
```yaml
version: "3.8"

services:
  agent-server:
    build:
      context: .
      dockerfile: docker/Dockerfile.python
    container_name: rna_agent_server
    environment:
      - RNA_AGENT_WORKSPACE=/workspace
      - RNA_AGENT_DATA_DIR=/workspace/data
      - RNA_AGENT_OUTPUT_DIR=/workspace/output
      - RNA_AGENT_PYTHON_SOCKET=/tmp/sockets/rna_agent_py.sock
      - RNA_AGENT_R_PORT=9876
      - RNA_AGENT_LOG_LEVEL=INFO
    volumes:
      - .:/workspace
      - socket-dir:/tmp/sockets
    depends_on:
      - r-worker
    networks:
      - rna-net

  r-worker:
    build:
      context: .
      dockerfile: docker/Dockerfile.r
    container_name: rna_r_worker
    environment:
      - RNA_AGENT_R_PORT=9876
    ports:
      - "9876:9876"
    volumes:
      - .:/workspace
      - socket-dir:/tmp/sockets
    networks:
      - rna-net

  nextflow-runner:
    build:
      context: .
      dockerfile: docker/Dockerfile.nextflow
    container_name: rna_nextflow_runner
    environment:
      - NXF_WORK=/workspace/work
    volumes:
      - .:/workspace
      - /var/run/docker.sock:/var/run/docker.sock
    networks:
      - rna-net

volumes:
  socket-dir:

networks:
  rna-net:
    driver: bridge
```

#### 6. Standalone R Kernel Loopback Server Script (`rna_agent/kernels/r_worker.R`)
```R
#!/usr/bin/env Rscript
# r_worker.R: Subprocess JSON-RPC server listening on a loopback TCP port
suppressPackageStartupMessages({
  library(jsonlite)
  library(arrow)
})

# Define base R null-coalescing operator
`%||%` <- function(x, y) if (is.null(x)) y else x

port <- as.integer(Sys.getenv("RNA_AGENT_R_PORT", "9876"))
server <- serverSocket(port)
cat(sprintf("[r_worker] Listening on 127.0.0.1:%d\n", port))

env <- new.env(parent = globalenv())

while (TRUE) {
  con <- socketAccept(server, open = "r+b", blocking = TRUE)
  line <- readLines(con, n = 1, warn = FALSE)
  if (length(line) == 0 || line == "") {
    close(con)
    next
  }
  
  req <- tryCatch(jsonlite::fromJSON(line), error = function(e) NULL)
  if (is.null(req) || is.null(req$code)) {
    resp <- list(status = "ERROR", stderr = "Invalid JSON-RPC payload")
    writeLines(jsonlite::toJSON(resp, auto_unbox = TRUE), con)
    close(con)
    next
  }
  
  out <- textConnection("stdout_buf", "w", local = TRUE)
  err <- textConnection("stderr_buf", "w", local = TRUE)
  sink(out, type = "output")
  sink(err, type = "message")
  
  res <- tryCatch({
    eval(parse(text = req$code), envir = env)
    "SUCCESS"
  }, error = function(e) {
    cat(as.character(e), file = stderr())
    "FAILED"
  })
  
  sink(type = "message")
  sink(type = "output")
  close(out)
  close(err)
  
  resp <- list(
    cell_id = req$cell_id,
    status = res,
    stdout = paste(stdout_buf, collapse = "\n"),
    stderr = paste(stderr_buf, collapse = "\n"),
    execution_count = as.integer(req$execution_count %||% 1)
  )
  
  writeLines(jsonlite::toJSON(resp, auto_unbox = TRUE), con)
  close(con)
}
```

---

### 1.4 Host Supervisor & `codex.json` Configuration

To connect Codex directly to the `rna-agent` MCP tool server, configure `codex.json` in the workspace root:

```json
{
  "mcpServers": {
    "rna-agent": {
      "command": "python",
      "args": [
        "-m",
        "rna_agent.server"
      ],
      "env": {
        "RNA_AGENT_WORKSPACE": "/Users/vajraang/Documents/ChatGPT/bio-agent-harness",
        "RNA_AGENT_DATA_DIR": "/Users/vajraang/Documents/ChatGPT/bio-agent-harness/data",
        "RNA_AGENT_OUTPUT_DIR": "/Users/vajraang/Documents/ChatGPT/bio-agent-harness/output",
        "RNA_AGENT_PYTHON_SOCKET": "/tmp/rna_agent_py.sock",
        "RNA_AGENT_R_PORT": "9876",
        "RNA_AGENT_LOG_LEVEL": "INFO",
        "RNA_AGENT_MAX_MEMORY_MB": "16384",
        "RNA_AGENT_DEFAULT_TIMEOUT_SEC": "300"
      }
    }
  }
}
```

---

### 1.5 `AGENTS.md` Runtime Guidance Contract

The Lead Analyst must adhere strictly to the runtime guidance codified in `AGENTS.md`. The platform enforces these rules programmatically at the tool interface:

1. **The Inviolable Five-Question Scientific Order**:
   - **Phase 1: What data do I have?** Identify samples, biological donors, technical replicates, sequencing depth, and data-quality concerns. Distinguish raw integer counts from continuous normalized expression. Never fit a count model to floating-point TPM/FPKM or log-transformed values.
   - **Phase 2: What question am I investigating?** Formulate exact contrast hypotheses. Attribute all requirements to explicit user instructions; never invent prespecified constraints or default parameters.
   - **Phase 3: Which methods are appropriate, and why?** Assess model assumptions (negative binomial GLMs for counts; Gaussian splines for continuous expression). Evaluate design rank and degrees of freedom before fitting. Freeze comparison criteria before viewing results.
   - **Phase 4: What happened?** Report observed outputs, verified dispersion curves, and p-value distributions. State when a diagnostic plot or assessment is absent. Never fabricate estimates or substitute a method's self-report for independent validation.
   - **Phase 5: What can I conclude, and what remains uncertain?** Recommend only what the evidence directly supports. Execution smoke, runtime success, or row counts do not establish scientific validity.

2. **Core Statistical & Biological Invariants**:
   - **Biological Units**: Repeated specimens, technical sequencing lanes, or multiple cell lines derived from the same subject cannot be counted as independent biological replicates. Pseudoreplication at the cell level in single-cell assays is strictly forbidden; pseudobulk aggregation by donor is mandatory.
   - **Confounding Invariant**: Completely confounded batch and experimental condition terms cannot yield an identifiable causal effect. Dropping a confounding covariate does not remove the confound.
   - **Multiple Testing Invariant**: All differential tests must report Benjamini-Hochberg (BH) or Benjamini-Yekutieli (BY) adjusted p-values ($q$). Adjusted $q$ must strictly satisfy $q \ge p$ for every gene.
   - **Replicate Degrees of Freedom**: Design matrices must possess residual degrees of freedom $\ge 2$. Models with 0 or 1 residual df are blocked from differential testing.

---

## 2. Orchestration Loop & State Machine Specification

### 2.1 The 9-Stage Analysis Lifecycle

The orchestration loop transitions through nine explicit, sequentially audited states:

```
┌─────────────────┐
│ 1. SESSION_INIT │ ──► Chat intake, metadata discovery, file integrity audit
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 2. DATA_DISCOVERY│ ──► check_count_integrity, get_table_preview, biological unit mapping
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 3. PLAN_PROPOSAL │ ──► Draft AnalysisPlan: hypothesis family, design formula, primary contrast
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 4. USER_APPROVAL │ ──► User inspects and confirms plan; runtime freezes plan_hash (SHA-256)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 5. GOVERNED_EXEC │ ──► Sandboxed execution: execute_python_cell, execute_r_cell, Nextflow
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 6. RUN_CHECKS   │ ──► Deterministic checks: design rank, dispersion fit, Cook's, KS test
└────────┬────────┘
         │
         ├──────────────────────────────────────────┐
         │ Checks WARN / FAIL                       │ Checks PASS
         ▼                                          ▼
┌─────────────────┐                        ┌─────────────────┐
│ 7. REVISION_LOOP│                        │ 8. OPTIONAL_REV │ ──► Fresh-context Reviewer audits
└────────┬────────┘                        └────────┬────────┘      claim-evidence binding
         │                                          │
         │ Resolved (Type A/B/C)                    │ Audited / Approved
         └──────────────────────────────────────────┼─────────────────────────┐
                                                    ▼                         ▼
                                           ┌─────────────────┐       ┌─────────────────┐
                                           │ 9. SYNTHESIS    │       │ PUBLICATION     │
                                           │    & REPORT     │ ──►   │ RELEASE         │
                                           └─────────────────┘       └─────────────────┘
```

---

### 2.2 Formal State Transition Matrix & Guard Conditions

| Source State | Event Trigger | Target State | Guard Conditions / Invariants | Action & Produced Artifact |
|---|---|---|---|---|
| `SESSION_INIT` | `start_session(dataset_paths)` | `DATA_DISCOVERY` | Paths exist, are non-empty, and resolve inside workspace. | Initialize `OperationLedger`, emit `SessionInitReceipt`. |
| `DATA_DISCOVERY` | `audit_dataset(counts, meta)` | `PLAN_PROPOSAL` | `check_count_integrity` passes; sample IDs in counts match metadata. | Generate and pin `DataInspectionReport` into Persistent State Anchor. |
| `PLAN_PROPOSAL` | `propose_analysis_plan(plan)` | `USER_APPROVAL` | Formula specified, reference level declared, contrast defined, $\alpha \in (0, 1)$. | Render structured plan summary table in chat for user review. |
| `USER_APPROVAL` | `user_confirm_plan()` | `GOVERNED_EXEC` | User explicitly approves; `plan_hash = SHA256(canonical_json(plan))` computed. | Freeze `AnalysisPlan` artifact; enter Governed Execution lane. |
| `USER_APPROVAL` | `user_reject_plan(feedback)` | `PLAN_PROPOSAL` | User requests alterations to design or contrast. | Update plan draft with user adjustments; reset revision counter. |
| `GOVERNED_EXEC` | `execute_cells(code_blocks)` | `RUN_CHECKS` | Execution completes with exit code 0; outputs match registered schema. | Record cell AST in dependency graph; emit `ExecutionTrace`. |
| `RUN_CHECKS` | `run_scientific_checks()` | `OPTIONAL_REV` | All mandatory checks return `PASS`; zero `BLOCK_FATAL` receipts. | Compile `EvidenceManifest` and candidate `FinalReport`. |
| `RUN_CHECKS` | `run_scientific_checks()` | `REVISION_LOOP` | Any check emits `WARN` or `BLOCK_FATAL`. | Emit `CheckFailureReceipt`; trigger structured revision taxonomy. |
| `REVISION_LOOP` | `apply_revision(intent, fix)` | `GOVERNED_EXEC` | If Type A: automatic. If Type B: cited check receipt. If Type C: user sign-off. | Record revision intent in `OperationLedger`; re-execute step. |
| `OPTIONAL_REV` | `audit_candidate_report()` | `SYNTHESIS_REPORT`| Reviewer enabled & returns `PASSED_CERTIFIED`, OR Reviewer disabled by config. | Certify report; invoke AST replay compiler to emit clean script. |
| `OPTIONAL_REV` | `audit_candidate_report()` | `REVISION_LOOP` | Reviewer emits `REJECTED_BLOCKED` with counter-evidence citation. | Dispatch `ReviewerBlockReceipt` to Lead Analyst for correction. |
| `SYNTHESIS_REPORT`| `export_release_bundle()` | `PUBLICATION_RELEASE`| Clean standalone script executes successfully and reproduces result hashes. | Emit `PublicationRelease` bundle with signed Merkle root. |

---

### 2.3 Two-Lane Execution & Cryptographic Lineage Tracking

#### 1. Explore Lane vs. Governed Lane
- **Explore Lane**: Fast, interactive scratchpad. All intermediate tables, plots, and summaries created in this lane carry mandatory metadata: `{"is_governed": false, "evidence_tier": "exploratory"}`. Outputs from the Explore lane **cannot** be cited in the final evidence report or exported as publication receipts.
- **Governed Lane**: Operates strictly on a frozen `AnalysisPlan`. All code executed in this lane writes auditable step receipts and records AST dependency hashes in the Merkle DAG.

#### 2. SHA-256 Execution DAG Node Schema
Every step executed in the Governed Lane creates an immutable node in the execution DAG:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "DAGExecutionNode",
  "type": "object",
  "properties": {
    "node_id": {
      "type": "string",
      "description": "Unique identifier for this DAG execution node (e.g. 'node_de_fit_01')."
    },
    "parent_node_ids": {
      "type": "array",
      "items": { "type": "string" },
      "description": "List of parent node IDs establishing direct causal lineage."
    },
    "plan_hash": {
      "type": "string",
      "pattern": "^[a-f0-9]{64}$",
      "description": "SHA-256 hash of the frozen AnalysisPlan."
    },
    "code_hash": {
      "type": "string",
      "pattern": "^[a-f0-9]{64}$",
      "description": "SHA-256 hash of the executed Python or R code string."
    },
    "input_artifact_hashes": {
      "type": "object",
      "additionalProperties": { "type": "string", "pattern": "^[a-f0-9]{64}$" },
      "description": "Dictionary mapping input file names to their SHA-256 content hashes."
    },
    "output_artifact_hashes": {
      "type": "object",
      "additionalProperties": { "type": "string", "pattern": "^[a-f0-9]{64}$" },
      "description": "Dictionary mapping generated artifact names to their SHA-256 content hashes."
    },
    "check_receipt_hashes": {
      "type": "array",
      "items": { "type": "string", "pattern": "^[a-f0-9]{64}$" },
      "description": "List of SHA-256 hashes of deterministic check receipts issued during this step."
    },
    "timestamp_utc": {
      "type": "string",
      "format": "date-time"
    },
    "node_hash": {
      "type": "string",
      "pattern": "^[a-f0-9]{64}$",
      "description": "SHA256(canonical_json({parent_node_ids, plan_hash, code_hash, input_artifact_hashes, output_artifact_hashes, check_receipt_hashes, timestamp_utc}))"
    }
  },
  "required": [
    "node_id",
    "parent_node_ids",
    "plan_hash",
    "code_hash",
    "input_artifact_hashes",
    "output_artifact_hashes",
    "check_receipt_hashes",
    "timestamp_utc",
    "node_hash"
  ]
}
```

---

### 2.4 Complete JSON Schemas for State Machine Lifecycle Artifacts

#### 0. `SessionInitReceipt.schema.json`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "SessionInitReceipt",
  "type": "object",
  "properties": {
    "session_id": { "type": "string" },
    "workspace_root": { "type": "string" },
    "dataset_paths": {
      "type": "array",
      "items": { "type": "string" }
    },
    "ledger_path": { "type": "string" },
    "timestamp_utc": { "type": "string", "format": "date-time" }
  },
  "required": ["session_id", "workspace_root", "dataset_paths", "ledger_path", "timestamp_utc"]
}
```

#### 1. `DataInspectionReport.schema.json`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "DataInspectionReport",
  "type": "object",
  "properties": {
    "dataset_id": { "type": "string" },
    "counts_path": { "type": "string" },
    "metadata_path": { "type": "string" },
    "data_type": { "type": "string", "enum": ["raw_integer_counts", "normalized_continuous_expression"] },
    "sample_count": { "type": "integer", "minimum": 1 },
    "gene_count": { "type": "integer", "minimum": 1 },
    "biological_units_identified": {
      "type": "array",
      "items": { "type": "string" },
      "description": "List of unique biological donor / subject identifiers."
    },
    "library_size_summary": {
      "type": "object",
      "properties": {
        "min": { "type": "number" },
        "median": { "type": "number" },
        "max": { "type": "number" }
      },
      "required": ["min", "median", "max"]
    },
    "all_zero_genes_count": { "type": "integer", "minimum": 0 },
    "technical_replicates_present": { "type": "boolean" },
    "inspection_passed": { "type": "boolean" }
  },
  "required": [
    "dataset_id",
    "counts_path",
    "metadata_path",
    "data_type",
    "sample_count",
    "gene_count",
    "biological_units_identified",
    "library_size_summary",
    "all_zero_genes_count",
    "technical_replicates_present",
    "inspection_passed"
  ]
}
```

#### 2. `AnalysisPlan.schema.json`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "AnalysisPlan",
  "type": "object",
  "properties": {
    "plan_id": { "type": "string" },
    "study_objective": { "type": "string" },
    "design_formula": { "type": "string", "description": "e.g. '~ cell_line + dexamethasone'" },
    "reference_levels": {
      "type": "object",
      "additionalProperties": { "type": "string" },
      "description": "Baseline reference levels for categorical covariates (e.g. {'dex': 'untrt'})."
    },
    "primary_contrast": {
      "type": "object",
      "properties": {
        "factor": { "type": "string" },
        "numerator": { "type": "string" },
        "denominator": { "type": "string" }
      },
      "required": ["factor", "numerator", "denominator"]
    },
    "alpha": { "type": "number", "minimum": 0.001, "maximum": 0.2, "default": 0.05 },
    "lfc_threshold": { "type": "number", "minimum": 0.0, "default": 0.0 },
    "shrinkage_method": { "type": "string", "enum": ["apeglm", "ashr", "normal", "none"] },
    "biological_replicate_column": { "type": "string" },
    "plan_hash": { "type": "string", "pattern": "^[a-f0-9]{64}$" }
  },
  "required": [
    "plan_id",
    "study_objective",
    "design_formula",
    "reference_levels",
    "primary_contrast",
    "alpha",
    "shrinkage_method",
    "biological_replicate_column",
    "plan_hash"
  ]
}
```

#### 3. `StepReceipt.schema.json`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "StepReceipt",
  "type": "object",
  "properties": {
    "step_id": { "type": "string" },
    "stage": { "type": "string", "enum": ["intake", "qc", "design", "model_fit", "diagnostics", "gsea", "synthesis"] },
    "status": { "type": "string", "enum": ["PASS", "WARN", "BLOCK_FATAL"] },
    "inputs_hash": { "type": "string", "pattern": "^[a-f0-9]{64}$" },
    "outputs_hash": { "type": "string", "pattern": "^[a-f0-9]{64}$" },
    "metrics": {
      "type": "object",
      "additionalProperties": { "type": ["string", "number", "boolean", "null"] }
    },
    "timestamp_utc": { "type": "string", "format": "date-time" }
  },
  "required": ["step_id", "stage", "status", "inputs_hash", "outputs_hash", "metrics", "timestamp_utc"]
}
```

#### 4. `RevisionIntentDeclaration.schema.json`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "RevisionIntentDeclaration",
  "type": "object",
  "properties": {
    "revision_id": { "type": "string" },
    "intent_type": {
      "type": "string",
      "enum": ["TYPE_A_TECHNICAL", "TYPE_B_MODEL_RESPECIFICATION", "TYPE_C_HYPOTHESIS_MUTATION"]
    },
    "triggering_check_receipt_id": { "type": "string" },
    "rationale": { "type": "string" },
    "proposed_action": { "type": "string" },
    "user_approval_required": { "type": "boolean" },
    "user_approved": { "type": "boolean" }
  },
  "required": ["revision_id", "intent_type", "triggering_check_receipt_id", "rationale", "proposed_action", "user_approval_required"]
}
```

#### 5. `EvidenceManifest.schema.json`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "EvidenceManifest",
  "type": "object",
  "$defs": {
    "StepReceipt": {
      "type": "object",
      "properties": {
        "step_id": { "type": "string" },
        "stage": {
          "type": "string",
          "enum": ["intake", "qc", "design", "model_fit", "diagnostics", "gsea", "synthesis"]
        },
        "status": {
          "type": "string",
          "enum": ["PASS", "WARN", "BLOCK_FATAL"]
        },
        "inputs_hash": { "type": "string", "pattern": "^[a-f0-9]{64}$" },
        "outputs_hash": { "type": "string", "pattern": "^[a-f0-9]{64}$" },
        "metrics": {
          "type": "object",
          "additionalProperties": { "type": ["string", "number", "boolean", "null"] }
        },
        "timestamp_utc": { "type": "string", "format": "date-time" }
      },
      "required": ["step_id", "stage", "status", "inputs_hash", "outputs_hash", "metrics", "timestamp_utc"]
    }
  },
  "definitions": {
    "StepReceipt": {
      "$ref": "#/$defs/StepReceipt"
    }
  },
  "properties": {
    "manifest_id": { "type": "string" },
    "plan_hash": { "type": "string", "pattern": "^[a-f0-9]{64}$" },
    "root_dag_hash": { "type": "string", "pattern": "^[a-f0-9]{64}$" },
    "input_datasets": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "dataset_id": { "type": "string" },
          "file_path": { "type": "string" },
          "sha256": { "type": "string", "pattern": "^[a-f0-9]{64}$" }
        },
        "required": ["dataset_id", "file_path", "sha256"]
      }
    },
    "registered_results_tables": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "table_id": { "type": "string" },
          "contrast_name": { "type": "string" },
          "file_path": { "type": "string" },
          "row_count": { "type": "integer" },
          "significant_genes_count": { "type": "integer" },
          "sha256": { "type": "string", "pattern": "^[a-f0-9]{64}$" }
        },
        "required": ["table_id", "contrast_name", "file_path", "row_count", "significant_genes_count", "sha256"]
      }
    },
    "step_receipts": {
      "type": "array",
      "items": { "$ref": "#/$defs/StepReceipt" }
    }
  },
  "required": ["manifest_id", "plan_hash", "root_dag_hash", "input_datasets", "registered_results_tables", "step_receipts"]
}
```

#### 6. `CandidateFinalReport.schema.json`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "CandidateFinalReport",
  "type": "object",
  "properties": {
    "report_id": { "type": "string" },
    "plan_hash": { "type": "string", "pattern": "^[a-f0-9]{64}$" },
    "markdown_content": { "type": "string" },
    "structured_claims": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "claim_id": { "type": "string" },
          "entity": { "type": "string" },
          "metric": { "type": "string", "enum": ["log2FoldChange", "padj", "pvalue", "NES", "enrichment_score"] },
          "reported_value": { "type": "number" },
          "reported_direction": { "type": "string", "enum": ["upregulated", "downregulated", "non_significant"] },
          "contrast_name": { "type": "string" },
          "target_artifact": { "type": "string" }
        },
        "required": ["claim_id", "entity", "metric", "reported_value", "reported_direction", "contrast_name", "target_artifact"]
      }
    },
    "model_search_space_disclosure": {
      "type": "object",
      "properties": {
        "total_models_fitted": { "type": "integer", "minimum": 1 },
        "total_contrasts_evaluated": { "type": "integer", "minimum": 1 },
        "multiplicity_correction_applied": { "type": "string" }
      },
      "required": ["total_models_fitted", "total_contrasts_evaluated", "multiplicity_correction_applied"]
    }
  },
  "required": ["report_id", "plan_hash", "markdown_content", "structured_claims", "model_search_space_disclosure"]
}
```

#### 7. `PublicationRelease.schema.json`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "PublicationRelease",
  "type": "object",
  "properties": {
    "release_id": { "type": "string" },
    "study_title": { "type": "string" },
    "plan_hash": { "type": "string", "pattern": "^[a-f0-9]{64}$" },
    "merkle_root_hash": { "type": "string", "pattern": "^[a-f0-9]{64}$" },
    "report_markdown_path": { "type": "string" },
    "clean_reproducible_pipeline_path": { "type": "string" },
    "environment_lock_path": { "type": "string" },
    "evidence_manifest_path": { "type": "string" },
    "reviewer_audit_receipt_path": { "type": ["string", "null"] },
    "created_at_utc": { "type": "string", "format": "date-time" }
  },
  "required": [
    "release_id",
    "study_title",
    "plan_hash",
    "merkle_root_hash",
    "report_markdown_path",
    "clean_reproducible_pipeline_path",
    "environment_lock_path",
    "evidence_manifest_path",
    "created_at_utc"
  ]
}
```

---

## 3. Tool Surface & Formal JSON Schema Contracts

Below are the complete, exhaustive JSON schemas for all tools exposed to Codex. Every property is fully defined with explicit types, constraints, and descriptions. There are zero truncated fields or ellipses.

### 3.1 Persistent Sandboxed Execution Kernels

#### `execute_python_cell`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "execute_python_cell",
  "description": "Execute Python code in the persistent sandboxed Python kernel subprocess. Retains in-memory AnnData, Scanpy, PyDESeq2, and NumPy state across turns.",
  "type": "object",
  "properties": {
    "code": {
      "type": "string",
      "description": "Executable Python code block."
    },
    "cell_id": {
      "type": "string",
      "description": "Unique identifier for this cell turn, used for AST dependency tracking and clean script compilation."
    },
    "lane": {
      "type": "string",
      "enum": ["explore", "governed"],
      "default": "explore",
      "description": "Execution governance lane. 'governed' requires an active approved AnalysisPlan and writes auditable receipts."
    },
    "timeout_sec": {
      "type": "integer",
      "minimum": 5,
      "maximum": 1800,
      "default": 300,
      "description": "Execution timeout in seconds."
    }
  },
  "required": ["code", "cell_id"]
}
```
*Response Contract*:
```json
{
  "type": "object",
  "properties": {
    "cell_id": { "type": "string" },
    "status": { "type": "string", "enum": ["ok", "error"] },
    "stdout": { "type": "string" },
    "stderr": { "type": "string" },
    "execution_count": { "type": "integer" },
    "created_variables": { "type": "array", "items": { "type": "string" } },
    "saved_artifacts": { "type": "array", "items": { "type": "string" } },
    "error_details": {
      "type": "object",
      "properties": {
        "ename": { "type": "string" },
        "evalue": { "type": "string" },
        "traceback": { "type": "array", "items": { "type": "string" } }
      }
    }
  },
  "required": ["cell_id", "status", "stdout", "stderr", "execution_count"]
}
```

#### `execute_r_cell`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "execute_r_cell",
  "description": "Execute R code in the persistent sandboxed R kernel subprocess. Retains in-memory DESeq2, edgeR, limma, and Bioconductor objects across turns.",
  "type": "object",
  "properties": {
    "code": {
      "type": "string",
      "description": "Executable R code block."
    },
    "cell_id": {
      "type": "string",
      "description": "Unique identifier for this cell turn, used for AST dependency tracking and clean script compilation."
    },
    "lane": {
      "type": "string",
      "enum": ["explore", "governed"],
      "default": "explore",
      "description": "Execution governance lane. 'governed' requires an active approved AnalysisPlan."
    },
    "timeout_sec": {
      "type": "integer",
      "minimum": 5,
      "maximum": 1800,
      "default": 300,
      "description": "Execution timeout in seconds."
    }
  },
  "required": ["code", "cell_id"]
}
```
*Response Contract*:
```json
{
  "type": "object",
  "properties": {
    "cell_id": { "type": "string" },
    "status": { "type": "string", "enum": ["ok", "error"] },
    "stdout": { "type": "string" },
    "stderr": { "type": "string" },
    "execution_count": { "type": "integer" },
    "created_variables": { "type": "array", "items": { "type": "string" } },
    "saved_artifacts": { "type": "array", "items": { "type": "string" } },
    "error_details": {
      "type": "object",
      "properties": {
        "message": { "type": "string" },
        "call": { "type": "string" },
        "traceback": { "type": "array", "items": { "type": "string" } }
      }
    }
  },
  "required": ["cell_id", "status", "stdout", "stderr", "execution_count"]
}
```

#### `export_reproducible_pipeline`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "export_reproducible_pipeline",
  "description": "Compile interactive notebook session cells into a clean, standalone, reproducible R or Python pipeline script using backward AST slicing from approved artifacts.",
  "type": "object",
  "properties": {
    "target_artifacts": {
      "type": "array",
      "items": { "type": "string" },
      "description": "List of final artifact paths (e.g. results tables, figures) to slice backward from."
    },
    "output_script_path": {
      "type": "string",
      "description": "Destination path for the exported standalone script (e.g. 'scripts/run_deseq2_pipeline.R')."
    },
    "language": {
      "type": "string",
      "enum": ["r", "python"],
      "description": "Target language for the compiled script."
    },
    "random_seed": {
      "type": "integer",
      "default": 42,
      "description": "Deterministic seed injected at the head of the compiled script."
    }
  },
  "required": ["target_artifacts", "output_script_path", "language"]
}
```
*Export Pipeline Response*:
```json
{
  "type": "object",
  "properties": {
    "status": { "type": "string", "enum": ["SUCCESS", "FAILED"] },
    "output_script_path": { "type": "string" },
    "language": { "type": "string", "enum": ["r", "python"] },
    "total_cells_analyzed": { "type": "integer" },
    "retained_statements_count": { "type": "integer" },
    "pruned_statements_count": { "type": "integer" },
    "script_sha256": { "type": "string", "pattern": "^[a-f0-9]{64}$" },
    "verification_replay_passed": { "type": "boolean" },
    "message": { "type": "string" }
  },
  "required": ["status", "output_script_path", "language", "script_sha256", "verification_replay_passed", "message"]
}
```

---

### 3.2 Deterministic Scientific Check Tools

#### `check_design_matrix`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "check_design_matrix",
  "description": "Audit linear model design matrix before model fitting. Verifies full column rank, residual degrees of freedom >= 2, sample pairing balance, and detects perfect collinearity/confounding.",
  "type": "object",
  "properties": {
    "metadata_path": {
      "type": "string",
      "description": "Path to sample metadata table (CSV, TSV, or Arrow Feather)."
    },
    "design_formula": {
      "type": "string",
      "description": "Linear model design formula (e.g. '~ cell_line + treatment' or '~ batch + condition')."
    },
    "biological_unit_col": {
      "type": "string",
      "description": "Column identifying independent biological units (e.g. 'donor_id', 'patient_id', 'subject')."
    },
    "contrast_factor": {
      "type": "string",
      "description": "Primary experimental factor of interest in the design formula."
    }
  },
  "required": ["metadata_path", "design_formula", "biological_unit_col", "contrast_factor"]
}
```
*Return Receipt Schema*:
```json
{
  "type": "object",
  "properties": {
    "receipt_id": { "type": "string" },
    "status": { "type": "string", "enum": ["PASS", "WARN", "BLOCK_FATAL"] },
    "is_full_rank": { "type": ["boolean", "null"] },
    "matrix_rank": { "type": ["integer", "null"] },
    "column_count": { "type": ["integer", "null"] },
    "sample_count": { "type": ["integer", "null"] },
    "residual_degrees_of_freedom": { "type": ["integer", "null"] },
    "is_confounded": { "type": ["boolean", "null"] },
    "confounded_covariate_pairs": {
      "type": ["array", "null"],
      "items": {
        "type": "array",
        "items": { "type": "string" },
        "minItems": 2,
        "maxItems": 2
      }
    },
    "pairing_balanced": { "type": ["boolean", "null"] },
    "message": { "type": "string" }
  },
  "required": [
    "receipt_id",
    "status",
    "message"
  ]
}
```

#### `check_count_integrity`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "check_count_integrity",
  "description": "Audit raw count matrix integrity. Verifies strictly non-negative integer values, absence of NaNs/Infs, identifies all-zero rows, and validates minimum library size bounds.",
  "type": "object",
  "properties": {
    "counts_path": {
      "type": "string",
      "description": "Path to count matrix file (Arrow Feather, TSV, CSV, or H5AD)."
    },
    "expected_data_type": {
      "type": "string",
      "enum": ["raw_counts", "normalized_log_expression"],
      "default": "raw_counts",
      "description": "Expected data representation. 'raw_counts' enforces integer non-negative constraints."
    },
    "min_library_size": {
      "type": "number",
      "default": 1000000.0,
      "description": "Minimum acceptable total mapped read count per sample."
    }
  },
  "required": ["counts_path"]
}
```
*Return Receipt Schema*:
```json
{
  "type": "object",
  "properties": {
    "receipt_id": { "type": "string" },
    "status": { "type": "string", "enum": ["PASS", "WARN", "BLOCK_FATAL"] },
    "is_integer": { "type": ["boolean", "null"] },
    "contains_negative_values": { "type": ["boolean", "null"] },
    "contains_nan_or_inf": { "type": ["boolean", "null"] },
    "sample_count": { "type": ["integer", "null"] },
    "gene_count": { "type": ["integer", "null"] },
    "all_zero_gene_count": { "type": ["integer", "null"] },
    "low_library_size_samples": { "type": ["array", "null"], "items": { "type": "string" } },
    "library_sizes": { "type": ["object", "null"], "additionalProperties": { "type": "number" } },
    "message": { "type": "string" }
  },
  "required": [
    "receipt_id",
    "status",
    "message"
  ]
}
```

#### `check_qc_metrics`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "check_qc_metrics",
  "description": "Audit upstream sequencing quality metrics against strict scientific thresholds (minimum mapping rate, maximum duplication rate, maximum rRNA contamination, GC bias). Accommodates pseudoaligners (Salmon/Kallisto) where rRNA and duplication metrics are optional/null.",
  "type": "object",
  "properties": {
    "metrics_json_path": {
      "type": "string",
      "description": "Path to extracted MultiQC metrics JSON or parsed QC summary table."
    },
    "min_mapping_rate_percent": {
      "type": "number",
      "default": 70.0,
      "description": "Minimum acceptable overall alignment / mapping rate percentage."
    },
    "max_rrna_percent": {
      "type": "number",
      "default": 5.0,
      "description": "Maximum acceptable ribosomal RNA contamination percentage."
    },
    "max_duplication_rate_percent": {
      "type": "number",
      "default": 60.0,
      "description": "Maximum acceptable sequence duplication rate percentage."
    }
  },
  "required": ["metrics_json_path"]
}
```
*Return Receipt Schema*:
```json
{
  "type": "object",
  "properties": {
    "receipt_id": { "type": "string" },
    "status": { "type": "string", "enum": ["PASS", "WARN", "BLOCK_FATAL"] },
    "samples_assessed": { "type": ["integer", "null"] },
    "failed_samples": {
      "type": ["array", "null"],
      "items": {
        "type": "object",
        "properties": {
          "sample_id": { "type": "string" },
          "failure_reason": { "type": "string" },
          "metric_name": { "type": "string" },
          "observed_value": { "type": "number" },
          "threshold": { "type": "number" }
        },
        "required": ["sample_id", "failure_reason", "metric_name", "observed_value", "threshold"]
      }
    },
    "summary_statistics": {
      "type": ["object", "null"],
      "properties": {
        "mean_mapping_rate": { "type": "number" },
        "mean_rrna_rate": { "type": ["number", "null"] },
        "mean_duplication_rate": { "type": ["number", "null"] }
      },
      "required": ["mean_mapping_rate"]
    },
    "message": { "type": "string" }
  },
  "required": ["receipt_id", "status", "message"]
}
```

#### `check_de_diagnostics`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "check_de_diagnostics",
  "description": "Audit post-fit differential expression diagnostics: verifies negative binomial dispersion curve convergence, evaluates Cook's distance outliers, and runs KS uniformity test on null p-values. Cook's distance metrics are null when auditing edgeR or limma-voom models.",
  "type": "object",
  "properties": {
    "fit_diagnostics_path": {
      "type": "string",
      "description": "Path to saved model diagnostics JSON or RDS file containing dispersion and Cook's estimates."
    },
    "results_table_path": {
      "type": "string",
      "description": "Path to exported DE results Arrow Feather or TSV table."
    },
    "alpha": {
      "type": "number",
      "default": 0.05,
      "description": "Nominal FDR significance threshold."
    }
  },
  "required": ["fit_diagnostics_path", "results_table_path"]
}
```
*Return Receipt Schema*:
```json
{
  "type": "object",
  "properties": {
    "receipt_id": { "type": "string" },
    "status": { "type": "string", "enum": ["PASS", "WARN", "BLOCK_FATAL"] },
    "dispersion_converged": { "type": ["boolean", "null"] },
    "dispersion_fit_type": { "type": ["string", "null"], "enum": ["parametric", "local", "mean", "trended", "spline", "voom_loess", "failed", null] },
    "cooks_outlier_count": { "type": ["integer", "null"] },
    "cooks_outlier_fraction": { "type": ["number", "null"] },
    "pvalue_distribution_shape": {
      "type": ["string", "null"],
      "enum": ["uniform_with_spike", "conservative_slope", "anti_conservative_hill", "bimodal", null]
    },
    "ks_null_uniformity_pvalue": { "type": ["number", "null"] },
    "fdr_monotonicity_passed": { "type": ["boolean", "null"] },
    "fdr_violations_count": { "type": ["integer", "null"] },
    "message": { "type": "string" }
  },
  "required": [
    "receipt_id",
    "status",
    "message"
  ]
}
```

#### `check_pseudobulk_replication`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "check_pseudobulk_replication",
  "description": "Audit single-cell differential expression experimental structure. Blocks cell-level testing (Wilcoxon / t-test) that treats cells as independent observations, mandating donor-level aggregation.",
  "type": "object",
  "properties": {
    "h5ad_path": {
      "type": "string",
      "description": "Path to single-cell AnnData H5AD file."
    },
    "donor_replicate_col": {
      "type": "string",
      "description": "Metadata column identifying independent biological donors/animals (e.g. 'donor_id', 'patient')."
    },
    "cell_type_col": {
      "type": "string",
      "description": "Metadata column identifying cell types or clusters (e.g. 'cell_type', 'leiden')."
    },
    "min_cells_per_donor": {
      "type": "integer",
      "default": 10,
      "description": "Minimum cell count required per donor-celltype combination to retain pseudobulk profile."
    }
  },
  "required": ["h5ad_path", "donor_replicate_col", "cell_type_col"]
}
```
*Return Receipt Schema*:
```json
{
  "type": "object",
  "properties": {
    "receipt_id": { "type": "string" },
    "status": { "type": "string", "enum": ["PASS", "WARN", "BLOCK_FATAL"] },
    "total_cells": { "type": ["integer", "null"] },
    "unique_donors_count": { "type": ["integer", "null"] },
    "cell_types_evaluated": { "type": ["integer", "null"] },
    "cell_level_de_attempted": { "type": ["boolean", "null"] },
    "pseudobulk_aggregation_valid": { "type": ["boolean", "null"] },
    "retained_profiles_count": { "type": ["integer", "null"] },
    "dropped_profiles_count": { "type": ["integer", "null"] },
    "message": { "type": "string" }
  },
  "required": [
    "receipt_id",
    "status",
    "message"
  ]
}
```

#### `check_claim_evidence_binding`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "check_claim_evidence_binding",
  "description": "Forensically cross-check an empirical statement in the report text against the registered results table. Supports generic multi-metric claims (log2FC, padj, pvalue, NES, enrichment score) with numerical tolerance, directional agreement, and FDR monotonicity.",
  "type": "object",
  "properties": {
    "claim_id": {
      "type": "string",
      "description": "Unique identifier for the claim."
    },
    "entity_id": {
      "type": "string",
      "description": "Gene symbol, pathway name, or feature identifier mentioned in the report claim."
    },
    "gene_symbol": {
      "type": ["string", "null"],
      "description": "Alias for entity_id (maintained for backward compatibility)."
    },
    "metric_name": {
      "type": "string",
      "enum": ["log2FoldChange", "padj", "pvalue", "NES", "enrichment_score", "custom"],
      "default": "log2FoldChange",
      "description": "Statistical metric asserted in the claim."
    },
    "reported_value": {
      "type": ["number", "null"],
      "description": "Primary numerical metric value asserted in the claim text (e.g. log2FC, NES, padj)."
    },
    "reported_log2fc": {
      "type": ["number", "null"],
      "description": "Log2 fold change value asserted in the report text (alias for reported_value when metric is log2FoldChange)."
    },
    "reported_padj": {
      "type": ["number", "null"],
      "description": "Adjusted p-value asserted in the report text."
    },
    "reported_direction": {
      "type": ["string", "null"],
      "enum": ["upregulated", "downregulated", "enriched", "depleted", "non_significant", null],
      "description": "Direction of effect claimed in text."
    },
    "results_table_path": {
      "type": "string",
      "description": "Path to authoritative registered differential expression or GSEA table (Arrow Feather or TSV)."
    },
    "contrast_name": {
      "type": "string",
      "description": "Contrast family or gene set collection corresponding to the claim."
    },
    "tolerance": {
      "type": "number",
      "default": 0.01,
      "description": "Absolute numerical tolerance allowed between reported and observed metric value."
    }
  },
  "required": [
    "claim_id",
    "results_table_path",
    "contrast_name"
  ]
}
```
*Return Receipt Schema*:
```json
{
  "type": "object",
  "properties": {
    "receipt_id": { "type": "string" },
    "status": { "type": "string", "enum": ["PASS", "WARN", "BLOCK_FATAL"] },
    "claim_id": { "type": "string" },
    "entity_id": { "type": ["string", "null"] },
    "entity_found": { "type": "boolean" },
    "gene_found": { "type": "boolean" },
    "metric_name": { "type": "string" },
    "observed_value": { "type": ["number", "null"] },
    "reported_value": { "type": ["number", "null"] },
    "observed_log2fc": { "type": ["number", "null"] },
    "observed_padj": { "type": ["number", "null"] },
    "observed_pvalue": { "type": ["number", "null"] },
    "metric_diff": { "type": ["number", "null"] },
    "log2fc_diff": { "type": ["number", "null"] },
    "value_match": { "type": "boolean" },
    "directional_agreement": { "type": "boolean" },
    "fdr_monotonicity_valid": { "type": "boolean" },
    "message": { "type": "string" }
  },
  "required": [
    "receipt_id",
    "status",
    "claim_id",
    "value_match",
    "message"
  ]
}
```

---

### 3.3 Nextflow / nf-core Pipeline Orchestration Tools

#### `validate_nextflow_inputs`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "validate_nextflow_inputs",
  "description": "Preflight validation of Nextflow parameters, samplesheet CSV formatting, and reference genome existence prior to pipeline execution.",
  "type": "object",
  "properties": {
    "pipeline": {
      "type": "string",
      "description": "Nextflow pipeline name or local path (e.g. 'nf-core/rnaseq')."
    },
    "samplesheet_path": {
      "type": "string",
      "description": "Path to input samplesheet.csv."
    },
    "params_json": {
      "type": "object",
      "description": "Dictionary of pipeline execution parameters (e.g. genome, aligner, outdir)."
    }
  },
  "required": ["pipeline", "samplesheet_path", "params_json"]
}
```
*Validation Result Response*:
```json
{
  "type": "object",
  "properties": {
    "valid": { "type": "boolean" },
    "pipeline": { "type": "string" },
    "samples_found": { "type": "integer" },
    "sample_ids": { "type": "array", "items": { "type": "string" } },
    "paired_end": { "type": "boolean" },
    "validation_errors": { "type": "array", "items": { "type": "string" } },
    "validation_warnings": { "type": "array", "items": { "type": "string" } },
    "message": { "type": "string" }
  },
  "required": ["valid", "pipeline", "samples_found", "validation_errors", "validation_warnings", "message"]
}
```

#### `launch_nextflow_pipeline`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "launch_nextflow_pipeline",
  "description": "Launch an asynchronous Nextflow pipeline run in the background with checkpointing (-resume) and execution tracking.",
  "type": "object",
  "properties": {
    "pipeline": {
      "type": "string",
      "description": "Pipeline repository or path (e.g. 'nf-core/rnaseq')."
    },
    "revision": {
      "type": "string",
      "default": "3.14.0",
      "description": "Git release tag or commit SHA."
    },
    "samplesheet_path": {
      "type": "string",
      "description": "Validated samplesheet path."
    },
    "profile": {
      "type": "string",
      "default": "docker",
      "description": "Nextflow execution profile (e.g. 'docker', 'singularity', 'conda')."
    },
    "params_json": {
      "type": "object",
      "description": "Key-value dictionary of pipeline execution parameters."
    },
    "work_dir": {
      "type": "string",
      "description": "Scratch working directory for Nextflow intermediate tasks."
    }
  },
  "required": ["pipeline", "samplesheet_path", "work_dir"]
}
```
*Job Ticket Response*:
```json
{
  "type": "object",
  "properties": {
    "job_id": { "type": "string" },
    "pipeline": { "type": "string" },
    "status": { "type": "string", "enum": ["RUNNING", "SUBMITTED", "FAILED"] },
    "pid": { "type": ["integer", "null"] },
    "work_dir": { "type": ["string", "null"] },
    "trace_file": { "type": ["string", "null"] },
    "log_file": { "type": ["string", "null"] },
    "exit_code": { "type": ["integer", "null"] },
    "error_message": { "type": ["string", "null"] },
    "heartbeat_interval_sec": { "type": "integer", "default": 300 }
  },
  "required": ["job_id", "pipeline", "status"]
}
```

#### `get_nextflow_job_status`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "get_nextflow_job_status",
  "description": "Query progress, task completion percentage, active processes, and error logs of an asynchronous Nextflow job. Incorporates OS-level PID polling for SIGKILL detection and defensive error handling for preempted tasks.",
  "type": "object",
  "properties": {
    "job_id": {
      "type": "string",
      "description": "Job ID returned by launch_nextflow_pipeline."
    }
  },
  "required": ["job_id"]
}
```
*Job Status Response*:
```json
{
  "type": "object",
  "properties": {
    "job_id": { "type": "string" },
    "status": { "type": "string", "enum": ["RUNNING", "COMPLETED", "FAILED", "DEADLOCKED"] },
    "exit_code": { "type": ["integer", "null"] },
    "total_tasks": { "type": "integer" },
    "completed_tasks": { "type": "integer" },
    "failed_tasks": { "type": "integer" },
    "percent_complete": { "type": "number" },
    "active_processes": {
      "type": "array",
      "items": { "type": "string" }
    },
    "last_heartbeat_timestamp_utc": { "type": "string", "format": "date-time" },
    "is_alive": { "type": "boolean" },
    "failed_task_diagnostics": {
      "type": ["array", "null"],
      "items": {
        "type": "object",
        "properties": {
          "task_id": { "type": "string" },
          "process_name": { "type": "string" },
          "tag": { "type": ["string", "null"] },
          "exit_code": { "type": ["integer", "null"] },
          "work_dir": { "type": "string" },
          "error_summary": { "type": "string" }
        },
        "required": ["task_id", "process_name", "work_dir", "error_summary"]
      }
    },
    "message": { "type": "string" }
  },
  "required": [
    "job_id",
    "status",
    "total_tasks",
    "completed_tasks",
    "failed_tasks",
    "percent_complete",
    "is_alive",
    "message"
  ]
}
```

#### `ingest_pipeline_outputs`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "ingest_pipeline_outputs",
  "description": "Ingest completed Nextflow outputs: extracts scalar quality KPIs from multiqc_data.json and registers the quantification count matrix directly to the scientific object store.",
  "type": "object",
  "properties": {
    "job_id": {
      "type": "string",
      "description": "Completed Nextflow job ID."
    },
    "output_dir": {
      "type": "string",
      "description": "Path to Nextflow output publication directory."
    },
    "quantification_type": {
      "type": "string",
      "enum": ["star_salmon", "salmon", "featurecounts", "kallisto"],
      "default": "star_salmon",
      "description": "Quantification tool used by the pipeline."
    }
  },
  "required": ["job_id", "output_dir"]
}
```
*Ingestion Result Response*:
```json
{
  "type": "object",
  "properties": {
    "job_id": { "type": "string" },
    "status": { "type": "string", "enum": ["INGESTED", "PARTIAL", "FAILED"] },
    "multiqc_report_path": { "type": ["string", "null"] },
    "count_matrix_path": { "type": ["string", "null"] },
    "dataset_id": { "type": ["string", "null"] },
    "quantification_type": { "type": "string" },
    "sample_count": { "type": "integer" },
    "feature_count": { "type": "integer" },
    "qc_kpis": {
      "type": "object",
      "properties": {
        "mean_mapping_rate_percent": { "type": ["number", "null"] },
        "mean_assigned_reads": { "type": ["number", "null"] },
        "mean_duplication_rate_percent": { "type": ["number", "null"] },
        "mean_rrna_rate_percent": { "type": ["number", "null"] }
      }
    },
    "message": { "type": "string" }
  },
  "required": ["job_id", "status", "sample_count", "feature_count", "message"]
}
```

---

### 3.4 Visual & Data Inspection Tools

#### `get_table_preview`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "get_table_preview",
  "description": "Fetch a bounded, paginated slice of a tabular artifact or count matrix with server-side column filtering, sorting, and five-number summaries. Bounded to maximum 50 rows to prevent context flooding.",
  "type": "object",
  "properties": {
    "artifact_path": {
      "type": "string",
      "description": "Path to registered Arrow Feather, TSV, or H5AD table."
    },
    "offset": {
      "type": "integer",
      "minimum": 0,
      "default": 0,
      "description": "Row offset for pagination."
    },
    "limit": {
      "type": "integer",
      "minimum": 1,
      "maximum": 50,
      "default": 20,
      "description": "Number of rows to return (clamped strictly at 50)."
    },
    "sort_by": {
      "type": "string",
      "description": "Column name to sort by (e.g. 'padj', 'log2FoldChange')."
    },
    "ascending": {
      "type": "boolean",
      "default": true,
      "description": "Sort order."
    },
    "columns": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Specific subset of column names to retrieve."
    }
  },
  "required": ["artifact_path"]
}
```
*Table Preview Response*:
```json
{
  "type": "object",
  "properties": {
    "artifact_path": { "type": "string" },
    "total_rows": { "type": "integer" },
    "total_columns": { "type": "integer" },
    "offset": { "type": "integer" },
    "limit": { "type": "integer" },
    "columns": { "type": "array", "items": { "type": "string" } },
    "column_types": { "type": "object", "additionalProperties": { "type": "string" } },
    "rows": {
      "type": "array",
      "items": { "type": "object", "additionalProperties": true }
    },
    "column_summaries": {
      "type": "object",
      "additionalProperties": {
        "type": "object",
        "properties": {
          "min": { "type": ["number", "null"] },
          "q25": { "type": ["number", "null"] },
          "median": { "type": ["number", "null"] },
          "q75": { "type": ["number", "null"] },
          "max": { "type": ["number", "null"] },
          "missing_count": { "type": "integer" }
        }
      }
    }
  },
  "required": ["artifact_path", "total_rows", "total_columns", "offset", "limit", "columns", "rows"]
}
```

#### `inspect_plot_image`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "inspect_plot_image",
  "description": "Render a high-contrast downsampled image (512x512 thumbnail or region crop) of an analysis plot for visual multimodal verification without blowing up context.",
  "type": "object",
  "properties": {
    "plot_id": {
      "type": "string",
      "description": "Identifier of the registered plot."
    },
    "view_preset": {
      "type": "string",
      "enum": ["overview_thumbnail", "top_genes_crop", "outlier_detail"],
      "default": "overview_thumbnail",
      "description": "Viewport preset for rendering."
    },
    "format": {
      "type": "string",
      "enum": ["png", "jpeg"],
      "default": "png",
      "description": "Image encoding format."
    }
  },
  "required": ["plot_id"]
}
```
*Inspect Plot Response*:
```json
{
  "type": "object",
  "properties": {
    "plot_id": { "type": "string" },
    "format": { "type": "string", "enum": ["png", "jpeg"] },
    "width": { "type": "integer" },
    "height": { "type": "integer" },
    "image_base64": { "type": "string" },
    "plot_contract_summary": {
      "type": "object",
      "properties": {
        "plot_type": { "type": "string" },
        "feature_count": { "type": "integer" },
        "key_findings": { "type": "array", "items": { "type": "string" } }
      }
    }
  },
  "required": ["plot_id", "format", "width", "height", "image_base64"]
}
```

---

## 4. RNA-seq Skills Architecture (`skills/`)

### 4.1 Modular Skill Structure
Skills provide domain playbooks, execution templates, and quantitative self-evaluation criteria to the Lead Analyst. Each skill is packaged in a standardized directory:

```
skills/<skill-name>/
├── SKILL.md              # Main entrypoint: metadata, playbooks, <self_eval_criteria>, escalation rules
├── steps/                # Step-by-step modular sub-guides
│   ├── 01_phase_name.md
│   └── 02_phase_name.md
├── scripts/              # Tested, canonical R/Python templates
│   └── template.R
└── references/           # Statistical guidelines and citations
```

Below are the complete, publication-grade `SKILL.md` templates for all five core competencies.

---

### 4.2 Exemplar 1: Bulk RNA-seq QC & DE (`skills/bulk-rnaseq-de/SKILL.md`)

```markdown
---
name: bulk-rnaseq-de
description: Gold-standard bulk RNA-seq differential expression analysis using DESeq2 and edgeR with strict statistical validation.
author: rna-agent-team
version: 1.0.0
tools:
  - execute_r_cell
  - check_design_matrix
  - check_count_integrity
  - check_de_diagnostics
  - get_table_preview
  - inspect_plot_image
---

# Bulk RNA-seq Differential Expression Analysis Playbook

## 1. Scientific Objectives
Perform statistically rigorous differential gene expression analysis from raw integer counts, accounting for biological replicates, donor pairing, and confounding covariates.

## 2. Mandatory Analysis Phases
1. **Intake & QC**: Validate integer counts, assess library sizes, compute RLE size factors, and generate variance-stabilized PCA.
2. **Design Formulation**: Formulate linear model formula, verify design matrix rank and residual degrees of freedom ($\ge 2$), and declare biological units.
3. **Model Fitting**: Fit negative binomial GLMs using DESeq2, estimate dispersions via empirical Bayes shrinkage, and evaluate convergence.
4. **Hypothesis Testing & Diagnostics**: Compute Wald or LRT statistics, apply independent filtering, evaluate Cook's distance outliers, and plot p-value histograms.
5. **Shrinkage & Reporting**: Apply apeglm or ashr log2FC shrinkage for gene ranking; export complete result families with FDR q-values and NA masks.

## 3. Strict Pre-Checks
- Raw counts must be strictly non-negative integers (verified via `check_count_integrity`).
- Design matrix must be full column rank ($rank(X) = p$) and residual df must be $\ge 2$ (verified via `check_design_matrix`).
- Biological units must be declared; repeated specimens from the same donor must be paired in the formula (e.g. `~ donor + treatment`).

## 4. Numeric Self-Evaluation Criteria (<self_eval_criteria>)
The Lead Analyst must evaluate and satisfy these quantitative criteria before completing the DE stage:

<self_eval_criteria>
- metric: library_size_minimum
  operator: ">="
  threshold: 5000000
  unit: reads
  rationale: Bulk RNA-seq samples below 5M mapped reads lack statistical power for transcriptome-wide testing.

- metric: design_residual_degrees_of_freedom
  operator: ">="
  threshold: 2
  unit: degrees_of_freedom
  rationale: Models with 0 or 1 residual df cannot reliably estimate dispersion parameters.

- metric: dispersion_convergence_status
  operator: "=="
  threshold: "converged"
  rationale: Non-converging dispersion curves require falling back to local or mean regression.

- metric: cooks_distance_outlier_fraction
  operator: "<="
  threshold: 0.05
  unit: fraction_of_genes
  rationale: Samples with >5% Cook's outliers indicate extreme sample-specific artifacts requiring inspection.

- metric: pvalue_histogram_uniformity_score
  operator: ">="
  threshold: 0.70
  unit: ks_test_pvalue_null_region
  rationale: P-value distribution in [0.2, 1.0] must be approximately uniform; conservative slopes indicate unmodeled batch effects.

- metric: fdr_monotonicity_violation_count
  operator: "=="
  threshold: 0
  rationale: Adjusted p-values must strictly satisfy q >= p for every gene.
</self_eval_criteria>

## 5. Escalation Rules
- If design matrix is rank-deficient (confounded covariates), STOP immediately and report the confounding contingency table.
- If Cook's distance flags an entire sample as an extreme outlier, present the PCA and sample distance heatmap to the user before excluding it.
- If fewer than 5 genes are significant at FDR < 0.05, alert user to verify whether the contrast reference level is inverted.
```

---

### 4.3 Exemplar 2: Single-Cell RNA-seq Core (`skills/scrnaseq-core/SKILL.md`)

```markdown
---
name: scrnaseq-core
description: Single-cell RNA-seq QC, doublet detection, clustering, cell annotation, and donor-level pseudobulk differential expression.
author: rna-agent-team
version: 1.0.0
tools:
  - execute_python_cell
  - execute_r_cell
  - check_count_integrity
  - check_pseudobulk_replication
  - get_table_preview
  - inspect_plot_image
---

# Single-Cell RNA-seq Core Analysis Playbook

## 1. Scientific Objectives
Execute end-to-end single-cell transcriptomics: cell quality filtering, doublet removal, dimensionality reduction, unsupervised clustering, automated cell type annotation, and statistically valid pseudobulk differential expression.

## 2. Mandatory Analysis Phases
1. **Quality Control & Filtering**: Calculate gene counts, UMI counts, and mitochondrial/ribosomal read fractions per cell. Filter low-quality barcodes.
2. **Doublet Detection**: Run Scrublet or DoubletDetection to identify and remove multiplet transcriptomes.
3. **Normalization & Feature Selection**: Normalize total counts per cell, log1p transform, and identify highly variable genes (HVGs).
4. **Dimensionality Reduction & Clustering**: Compute PCA, construct KNN neighborhood graph, perform Harmony/Scanorama batch integration, and cluster via Leiden.
5. **Cell Type Annotation**: Compute cluster marker genes and annotate clusters against reference signatures (e.g. CellMarker, PanglaoDB).
6. **Pseudobulk Differential Expression**: Aggregate counts by donor and cell type; execute negative binomial DE in DESeq2/edgeR.

## 3. Strict Pre-Checks
- Raw AnnData `.X` or `.layers["counts"]` must contain non-negative integer counts.
- **Pseudoreplication Prohibition**: Direct cell-level Wilcoxon / t-tests comparing cells across conditions are strictly blocked. All multi-sample comparisons must aggregate counts to donor pseudobulks.

## 4. Numeric Self-Evaluation Criteria (<self_eval_criteria>)
The Lead Analyst must evaluate and confirm these quantitative criteria before completing single-cell stages:

<self_eval_criteria>
- metric: cell_min_genes
  operator: ">="
  threshold: 200
  unit: genes_per_cell
  rationale: Droplets with fewer than 200 detected genes represent empty beads or broken cell fragments.

- metric: cell_max_mito_percent
  operator: "<="
  threshold: 20.0
  unit: percent
  rationale: High mitochondrial read percentages (>20%) indicate lysed or dying cells in standard mammalian tissues.

- metric: predicted_doublet_removal_fraction
  operator: "<="
  threshold: 0.15
  unit: fraction_of_cells
  rationale: Doublet rates exceeding 15% in standard 10x runs indicate microfluidic overloading or clumping.

- metric: pca_variance_ratio_top50
  operator: ">="
  threshold: 0.25
  unit: cumulative_variance
  rationale: The top 50 principal components must capture at least 25% of total variance before neighbor graph construction.

- metric: min_cells_per_pseudobulk_replicate
  operator: ">="
  threshold: 10
  unit: cells
  rationale: Pseudobulk donor profiles composed of fewer than 10 cells yield noisy estimates and must be dropped.

- metric: donor_replicates_per_condition
  operator: ">="
  threshold: 3
  unit: biological_donors
  rationale: Valid pseudobulk differential expression requires at least 3 biological donors per experimental condition.
</self_eval_criteria>

## 5. Escalation Rules
- If cell viability is poor (>30% of cells filtered out due to high mitochondrial content), notify the user and display QC violin plots.
- If batch integration fails (clusters separate strictly by donor rather than biology), alert user to review covariate balance.
```

---

### 4.4 Exemplar 3: Pathway & GSEA Enrichment (`skills/pathway-enrichment/SKILL.md`)

```markdown
---
name: pathway-enrichment
description: Over-representation analysis (ORA) and pre-ranked Gene Set Enrichment Analysis (GSEA) with strict directional verification.
author: rna-agent-team
version: 1.0.0
tools:
  - execute_r_cell
  - execute_python_cell
  - get_table_preview
  - inspect_plot_image
---

# Pathway & GSEA Enrichment Playbook

## 1. Scientific Objectives
Identify biologically coherent gene sets, pathways, and functional modules enriched among differentially expressed genes, maintaining strict directional consistency between NES signs and reported biological effects.

## 2. Mandatory Analysis Phases
1. **Gene Identifier Mapping**: Map Ensembl / RefSeq identifiers to HGNC symbols and Entrez IDs, reporting mapping loss percentages.
2. **Gene Ranking**: Construct ranking metric for GSEA: $\text{rank} = \text{sign}(\log_2\text{FC}) \times (-\log_{10}(p_{\text{value}}))$.
3. **Pre-Ranked GSEA**: Run `fgsea` or `gseapy` against MSigDB collections (Hallmark, C2 Canonical Pathways, C5 Gene Ontology) with 10,000 permutations.
4. **Over-Representation Analysis (ORA)**: Run hypergeometric / Fisher's exact tests on filtered significant subsets (FDR < 0.05, $|\log_2\text{FC}| > 0.58$), testing upregulated and downregulated sets independently.
5. **Directional Validation**: Verify that Normalized Enrichment Score (NES) signs match the stated direction of pathway activation.

## 3. Strict Pre-Checks
- Identifier mapping loss must not exceed 10% of total tested genes.
- Background universe for ORA must be restricted to genes expressed and tested in the experiment, not the entire genome.

## 4. Numeric Self-Evaluation Criteria (<self_eval_criteria>)
<self_eval_criteria>
- metric: gene_id_mapping_efficiency
  operator: ">="
  threshold: 90.0
  unit: percent
  rationale: Mapping efficiency below 90% introduces systematic bias against uncharacterized or non-standard gene symbols.

- metric: gsea_min_set_size
  operator: ">="
  threshold: 15
  unit: genes
  rationale: Gene sets smaller than 15 genes have insufficient statistical power and yield unstable permutation p-values.

- metric: gsea_max_set_size
  operator: "<="
  threshold: 500
  unit: genes
  rationale: Gene sets larger than 500 genes represent overly broad biological categories lacking functional specificity.

- metric: gsea_permutation_count
  operator: ">="
  threshold: 10000
  unit: permutations
  rationale: At least 10,000 permutations are required to estimate adjusted p-values below 0.001 reliably.

- metric: nes_directional_contradiction_count
  operator: "=="
  threshold: 0
  rationale: Positive NES must correspond to positive log2FC enrichment; sign inversions represent catastrophic reporting errors.
</self_eval_criteria>

## 5. Escalation Rules
- If leading-edge genes contain conflicting directions of effect, highlight the pathway as heterogeneous.
- If zero pathways pass FDR < 0.05, report nominal rankings but explicitly disclose absence of statistically significant enrichment.
```

---

### 4.5 Exemplar 4: nf-core Upstream Processing (`skills/nfcore-rnaseq-orchestration/SKILL.md`)

```markdown
---
name: nfcore-rnaseq-orchestration
description: Automated orchestration, validation, execution, and output ingestion for nf-core/rnaseq and nf-core/scrnaseq.
author: rna-agent-team
version: 1.0.0
tools:
  - validate_nextflow_inputs
  - launch_nextflow_pipeline
  - get_nextflow_job_status
  - ingest_pipeline_outputs
---

# nf-core Upstream Processing Playbook

## 1. Scientific Objectives
Execute reproducible raw read alignment, quality control, and transcript quantification using gold-standard nf-core pipelines without freezing the interactive chat loop.

## 2. Mandatory Analysis Phases
1. **Preflight Validation**: Validate samplesheet structure, check FASTQ file existence and read pair matching, verify reference genome index availability.
2. **Stub / Dry-Run Verification**: Execute `nextflow run ... -stub` to verify container pulling, profile configuration, and parameter syntax.
3. **Background Execution**: Launch pipeline with `-bg` and `-resume`. Return `JobTicket` immediately.
4. **Trace Monitoring**: Tail trace logs in background. Notify agent only on milestones or failure.
5. **Output Ingestion**: Parse `multiqc_data.json` for KPI metrics and register gene count matrix directly into the dataset store.

## 3. Strict Pre-Checks
- FASTQ files must be non-empty and readable.
- Strandedness must be explicitly specified or set to `auto` for detection.

## 4. Numeric Self-Evaluation Criteria (<self_eval_criteria>)
<self_eval_criteria>
- metric: preflight_validation_errors
  operator: "=="
  threshold: 0
  rationale: Nextflow jobs cannot be launched if preflight schema validation flags missing files or invalid parameters.

- metric: mean_alignment_rate
  operator: ">="
  threshold: 70.0
  unit: percent
  rationale: Alignment rates below 70% indicate severe contamination, poor library prep, or mismatched reference genomes.

- metric: mean_assigned_reads_fraction
  operator: ">="
  threshold: 0.60
  unit: fraction
  rationale: At least 60% of aligned reads must be assigned to annotated exons for bulk quantification.

- metric: ribosomal_rna_contamination
  operator: "<="
  threshold: 5.0
  unit: percent
  rationale: rRNA contamination above 5% indicates inefficient poly-A selection or ribo-depletion.
</self_eval_criteria>

## 5. Escalation Rules
- If alignment rate falls below 60% for any sample, STOP and present FASTQC metrics to the user.
- If a pipeline task fails with exit code 137 (OOM), automatically adjust memory in config and resume.
```

---

### 4.6 Exemplar 5: Figure & Report Synthesis (`skills/figure-synthesis-report/SKILL.md`)

```markdown
---
name: figure-synthesis-report
description: Publication-grade figure generation (Volcano, PCA, Heatmaps) and auditable markdown report synthesis with evidence anchors.
author: rna-agent-team
version: 1.0.0
tools:
  - execute_r_cell
  - execute_python_cell
  - get_table_preview
  - inspect_plot_image
  - check_claim_evidence_binding
---

# Figure & Report Synthesis Playbook

## 1. Scientific Objectives
Synthesize publication-ready visual figures and comprehensive discovery reports anchored to cryptographically verified results tables, preventing hallucinations and unsubstantiated claims.

## 2. Mandatory Analysis Phases
1. **Plot Contract Generation**: Export structured plot contracts (Volcano, MA, PCA, ComplexHeatmap) containing summary statistics and WebGL data buffers.
2. **Multimodal Inspection**: Call `inspect_plot_image` to verify layout aesthetics, label placement, and cluster separation.
3. **Atomic Claim Extraction**: Break candidate report narrative into atomic statements linking genes, effect sizes, and p-values.
4. **Evidence Binding Verification**: Run `check_claim_evidence_binding` on all empirical statements.
5. **Model Space Disclosure**: Append mandatory disclosure table recording total models fitted, contrasts evaluated, and multiplicity adjustments.

## 3. Strict Pre-Checks
- All figures must be accompanied by explicit captions describing data transformations, size factor normalization, and shrinkage methods.
- Heatmaps must use z-score scaled variance-stabilized counts, not raw counts.

## 4. Numeric Self-Evaluation Criteria (<self_eval_criteria>)
<self_eval_criteria>
- metric: unanchored_claims_count
  operator: "=="
  threshold: 0
  rationale: Every empirical claim in the report text must cite an exact row in a registered results table.

- metric: claim_numerical_error_count
  operator: "=="
  threshold: 0
  rationale: All reported log2FC and padj values must match underlying data within +/- 0.01 tolerance.

- metric: volcano_top_labeled_genes
  operator: ">="
  threshold: 10
  unit: genes
  rationale: Volcano plots must label at least the top 10 most significant differentially expressed genes.

- metric: heatmap_sample_clustering_present
  operator: "=="
  threshold: true
  rationale: Expression heatmaps must include hierarchical clustering dendrograms on samples and genes.
</self_eval_criteria>

## 5. Escalation Rules
- If a reported statistic does not match data within 0.01 tolerance, halt report generation and alert the Lead Analyst.
```

---

## 5. Optional Fresh-Context Reviewer & Comparative Evaluation Harness

### 5.1 Reviewer Role Definition & System Prompt Contract

The Reviewer operates as a **fresh-context, read-only independent agent**. It has zero conversational memory of the Lead Analyst's internal deliberations, debugging iterations, or code trials. It receives only an immutable **Evidence Packet** and evaluates candidate reports using a deterministic rubric.

```markdown
# FRESH-CONTEXT SCIENTIFIC REVIEWER SYSTEM PROMPT

You are an INDEPENDENT SCIENTIFIC AUDITOR evaluating an automated RNA-seq analysis report.
Your mission is to perform a skeptical forensic audit of all empirical claims, statistical models, and biological conclusions against the registered data artifacts.

## Operating Constraints:
1. You are strictly READ-ONLY. You cannot run arbitrary code, modify files, or alter analysis plans.
2. Rely ONLY on the provided Evidence Packet (data hashes, execution receipts, deterministic check logs, and bounded table queries).
3. Allowed Toolset: You are exclusively bound to these read-only MCP tools:
   - `get_table_preview` (bounded table slices and five-number distributions)
   - `inspect_plot_image` (visual verification of thumbnails/crops)
   - `check_claim_evidence_binding` (multi-metric claim validation against registered Arrow tables)
   - `get_nextflow_job_status` (pipeline trace and error log inspection)
4. Do not assume claims are correct because the Lead Analyst asserts they passed. You must verify them against data.
5. Output your audit exclusively in the structured JSON rubric format (`reviewer_audit_receipt.json`).

## Comprehensive 5-Step Claim & Manifest Verification Rubric:
For every empirical claim in the candidate report:
Step 1: Extract atomic statements (Entity, Metric, Reported Value, Direction, Contrast).
Step 2: Resolve target result artifact and row index (Gene ID, Pathway ID).
Step 3: Multi-Tier Programmatic Checks:
  - 3.1 Upstream Execution Receipts: Verify that all mandatory upstream checks (`check_count_integrity`, `check_design_matrix`, `check_de_diagnostics`, `check_qc_metrics`, `check_pseudobulk_replication`) in EvidenceManifest have status PASS. Blocks non-integer counts, non-converged dispersion, rank deficiencies, and pseudoreplication.
  - 3.2 Sample Cohort & Exclusion Audit: Compare sample manifest in approved frozen plan against sample IDs and sample counts in the final Arrow table. Post-hoc unapproved dropouts trigger immediate BLOCK_FATAL.
  - 3.3 Numeric Accuracy: |reported - observed| <= tolerance (default: 0.01).
  - 3.4 Directional Consistency: Reported direction matches sign of observed metric (sign(log2FC) or sign(NES)).
  - 3.5 Invariants & Universe Validity: padj >= pvalue; for GSEA/ORA, background universe matches expressed genes.
Step 4: Assign verdict per claim: PASS, WARN_ADVISORY, or BLOCK_FATAL.
Step 5: Assign overall report verdict: PASSED_CERTIFIED, WARNINGS_NOTED, or REJECTED_BLOCKED.
```

---

### 5.2 Complete `reviewer_audit_receipt.json` Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "reviewer_audit_receipt",
  "description": "Structured forensic audit receipt emitted by the Fresh-Context Reviewer.",
  "type": "object",
  "properties": {
    "receipt_id": { "type": "string" },
    "report_artifact": { "type": "string" },
    "plan_hash": { "type": "string", "pattern": "^[a-f0-9]{64}$" },
    "root_dag_hash": { "type": "string", "pattern": "^[a-f0-9]{64}$" },
    "audit_timestamp_utc": { "type": "string", "format": "date-time" },
    "overall_verdict": {
      "type": "string",
      "enum": ["PASSED_CERTIFIED", "WARNINGS_NOTED", "REJECTED_BLOCKED"]
    },
    "summary_metrics": {
      "type": "object",
      "properties": {
        "total_claims_audited": { "type": "integer" },
        "claims_passed": { "type": "integer" },
        "claims_warned": { "type": "integer" },
        "claims_blocked": { "type": "integer" }
      },
      "required": ["total_claims_audited", "claims_passed", "claims_warned", "claims_blocked"]
    },
    "claims_audited": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "claim_id": { "type": "string" },
          "statement_text": { "type": "string" },
          "target_artifact": { "type": "string" },
          "target_identifier": { "type": "string" },
          "checks": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "check_name": { "type": "string" },
                "status": { "type": "string", "enum": ["PASS", "WARN", "FAIL"] },
                "observed_value": { "type": ["string", "number", "boolean", "null"] },
                "reported_value": { "type": ["string", "number", "boolean", "null"] },
                "discrepancy": { "type": ["number", "null"] },
                "message": { "type": "string" }
              },
              "required": ["check_name", "status", "message"]
            }
          },
          "verdict": {
            "type": "string",
            "enum": ["PASS", "WARN_ADVISORY", "BLOCK_FATAL"]
          },
          "remedy_instruction": { "type": ["string", "null"] }
        },
        "required": ["claim_id", "statement_text", "target_artifact", "target_identifier", "checks", "verdict"]
      }
    }
  },
  "required": [
    "receipt_id",
    "report_artifact",
    "plan_hash",
    "root_dag_hash",
    "audit_timestamp_utc",
    "overall_verdict",
    "summary_metrics",
    "claims_audited"
  ]
}
```

---

### 5.3 20-Task A/B Evaluation Protocol & Benchmark Suite

To empirically benchmark **Lead Analyst Alone (Arm A)** against **Lead Analyst + Fresh-Context Reviewer (Arm B)**, the platform establishes an automated evaluation suite containing 20 curated tasks with systematic synthetic error injections:

| Task ID | Assay Type | Dataset Origin | Experimental Condition / Injected Flaw | Expected Behavior & Ground Truth Gate |
|---|---|---|---|---|
| `TASK-01` | Bulk RNA-seq | Love et al. 2015 (Airway) | Clean gold standard; 4 cell lines $\times$ Dex treatment | Exact statistical concordance with published Airway benchmarks ($\pm 0.01$ log2FC). |
| `TASK-02` | Bulk RNA-seq | Bottomly et al. 2011 | Clean gold standard; 2 mouse strains, multi-batch | Model adjusts for batch covariate; recovers known strain marker genes. |
| `TASK-03` | scRNA-seq | Kang et al. 2018 (IFN-$\beta$) | Clean gold standard; 8 lupus donors stimulated vs control | Valid donor pseudobulk; recovers ISG15, CXCL10, IFIT1 in monocytes. |
| `TASK-04` | scRNA-seq | PBMC 10k (10x Genomics) | Clean gold standard; cell type clustering & markers | Correct identification of CD4+ T, CD8+ T, B, NK, Monocytes. |
| `TASK-05` | Bulk RNA-seq | Synthetic Airway Variant | **Injected Flaw 1**: Completely confounded batch & condition | Matrix rank deficiency detected; execution blocked with unidentifiable design error. |
| `TASK-06` | Bulk RNA-seq | Synthetic Airway Variant | **Injected Flaw 2**: Cell line pairing removed (pseudoreplication) | Residual df check warns; Reviewer blocks claim of independent donor effects. |
| `TASK-07` | Bulk RNA-seq | Synthetic Airway Variant | **Injected Flaw 3**: Contrast direction inverted in report text | Claim binding check detects sign inversion (log2FC negative vs "upregulated"). |
| `TASK-08` | Bulk RNA-seq | Synthetic Airway Variant | **Injected Flaw 4**: Fabricated p-value ($p=0.0001$ reported for $p=0.45$) | Reviewer value tolerance check detects discrepancy; emits `BLOCK_FATAL`. |
| `TASK-09` | Bulk RNA-seq | Synthetic Airway Variant | **Injected Flaw 5**: Post-hoc sample exclusion to drop p-value below 0.05 | Plan hash mismatch detected; unapproved branch blocked by lineage DAG. |
| `TASK-10` | Bulk RNA-seq | Synthetic Counts | **Injected Flaw 6**: Floating-point TPM provided as raw counts | `check_count_integrity` detects non-integer counts; blocks DESeq2 fitting. |
| `TASK-11` | Bulk RNA-seq | Synthetic Airway Variant | **Injected Flaw 7**: FDR monotonicity violation ($q < p$) | Monotonicity check flags violation; Reviewer blocks report publication. |
| `TASK-12` | Bulk RNA-seq | Synthetic High-Noise | **Injected Flaw 8**: Non-converged dispersion curve ignored | `check_de_diagnostics` warns on failed fit; requires local regression fallback. |
| `TASK-13` | scRNA-seq | Synthetic Kang Variant | **Injected Flaw 9**: Naive cell-level Wilcoxon test on 20,000 cells | `check_pseudobulk_replication` emits `BLOCK_FATAL` for pseudoreplication. |
| `TASK-14` | scRNA-seq | Synthetic Kang Variant | **Injected Flaw 10**: Donor covariate omitted from pseudobulk model | Design check blocks model; requires donor pairing covariate. |
| `TASK-15` | scRNA-seq | Synthetic Kang Variant | **Injected Flaw 11**: Extreme doublet rate (25%) unaddressed | `<self_eval_criteria>` doublet threshold breaches; forces filtering step. |
| `TASK-16` | scRNA-seq | Synthetic Kang Variant | **Injected Flaw 12**: High mito percentage cells retained (>30%) | `<self_eval_criteria>` mito threshold breaches; triggers QC re-filtering. |
| `TASK-17` | Pathway GSEA | Synthetic Airway DE | **Injected Flaw 13**: Contradictory GSEA NES sign vs report conclusion | Reviewer detects pathway activation claimed for negative NES score. |
| `TASK-18` | Pathway GSEA | Synthetic Airway DE | **Injected Flaw 14**: ORA universe set to whole genome instead of expressed | Background universe check warns; recalculates hypergeometric p-values. |
| `TASK-19` | nf-core | Synthetic Samplesheet | **Injected Flaw 15**: FASTQ file path missing in samplesheet | `validate_nextflow_inputs` preflight catches missing file; stops before launch. |
| `TASK-20` | nf-core | Synthetic Execution | **Injected Flaw 16**: Low mapping rate (42%) ignored | `check_qc_metrics` blocks downstream DE; alerts user to inspect read quality. |

#### Evaluation Metrics:
1. **Flaw Detection Sensitivity (Recall)**: $\frac{\text{Flaws Blocked}}{\text{Total Injected Flaws}}$ (Target: $>95\%$ for Arm B vs $<60\%$ for Arm A).
2. **False Alarm Rate (1 - Specificity)**: $\frac{\text{Valid Analyses Erroneously Blocked}}{\text{Total Valid Analyses}}$ (Target: $<5\%$).
3. **Statistical Concordance**: Jaccard similarity of top-50 differentially expressed genes against ground truth.
4. **Token & Latency Overhead**: Ratio of total tokens and elapsed runtime in Arm B relative to Arm A (Target: $<20\%$ overhead).

---

## 6. Phased Implementation Roadmap for Codex (Milestones M0 – M6)

This roadmap is engineered for direct autonomous execution by Codex. Each milestone defines exact file targets, implementation tasks, and objective terminal verification commands.

```
┌─────────────────────────────────────────────────────────────┐
│ M0: Foundations, Sandboxed Kernels & IPC Bridge             │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│ M1: Deterministic Scientific Checks & Statistical Rules     │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│ M2: Visual & Data Inspection Tools                          │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│ M3: Asynchronous Nextflow / nf-core Pipeline Runner         │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│ M4: Modular RNA-seq Skills & Self-Evaluation Engine         │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│ M5: Fresh-Context Reviewer & Evidence Audit Harness         │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│ M6: Lead Analyst Loop, End-to-End Orchestration & Benchmarks│
└─────────────────────────────────────────────────────────────┘
```

---

### Milestone M0: Foundations, Sandboxed Kernels & IPC Bridge

- **Objective**: Build the subprocess dual-kernel execution infrastructure (Python and R), the Arrow IPC serialization bridge, the cell dependency tracer with in-place reachability, and the backward AST replay compiler.
- **Target Files**:
  - `rna_agent/config.py`
  - `rna_agent/kernels/base.py`
  - `rna_agent/kernels/python_worker.py`
  - `rna_agent/kernels/r_worker.py`
  - `rna_agent/kernels/r_worker.R`
  - `rna_agent/kernels/bridge.py`
  - `rna_agent/kernels/supervisor.py`
  - `rna_agent/compiler/ast_tracer.py`
  - `rna_agent/compiler/replay_compiler.py`
  - `tests/test_kernels.py`
- **Implementation Steps**:
  1. Implement `Supervisor` managing persistent Python and R subprocesses over Unix domain sockets using loopback JSON-RPC (Mode A host virtualenvs and Mode B docker-compose).
  2. Implement SIGSEGV and crash recovery in `Supervisor`: cleanly catch child exit codes and respawn worker processes without crashing the host server.
  3. Implement `bridge.py` supporting zero-copy Arrow Feather IPC for count matrices and `zellkonverter` H5AD/SingleCellExperiment serialization.
  4. Implement `ast_tracer.py` with in-place mutation reachability: model mutating calls (`.drop(inplace=True)`, `.obs[...] = ...`) as both consumers and producers ($\text{Def}(s) \leftarrow \text{Def}(s) \cup \{X\}$, $\text{Use}(s) \leftarrow \text{Use}(s) \cup \{X\}$) across Python AST and R `codetools`.
  5. Implement `replay_compiler.py` performing backward program slicing from target output artifacts, pruning unreferenced scratch cells, and emitting clean standalone scripts (`run_pipeline.R` / `run_pipeline.py`) verified against interactive kernel output hashes.
- **Terminal Verification Command**:
  ```bash
  pytest tests/test_kernels.py -v
  ```

---

### Milestone M1: Deterministic Scientific Checks & Statistical Guardrails

- **Objective**: Implement programmatic scientific check tools validating design matrices, raw count integrity, upstream QC metrics, model diagnostics, single-cell pseudobulking, and claim-evidence bindings.
- **Target Files**:
  - `rna_agent/checks/base.py`
  - `rna_agent/checks/design.py`
  - `rna_agent/checks/counts.py`
  - `rna_agent/checks/qc_metrics.py`
  - `rna_agent/checks/diagnostics.py`
  - `rna_agent/checks/pseudobulk.py`
  - `rna_agent/checks/claim_binding.py`
  - `tests/test_scientific_checks.py`
- **Implementation Steps**:
  1. Implement `check_design_matrix` using QR decomposition to verify full rank, detect collinear covariate pairs, verify residual df $\ge 2$, and check sample pairing balance. Return nullable metrics on `BLOCK_FATAL`.
  2. Implement `check_count_integrity` verifying strictly non-negative integers, flagging NaNs/Infs, identifying all-zero rows, and evaluating minimum library size thresholds.
  3. Implement `check_qc_metrics` parsing MultiQC summaries against alignment, rRNA, and duplication rate thresholds, accommodating pseudoaligners (Salmon/Kallisto).
  4. Implement `check_de_diagnostics` auditing dispersion convergence, Cook's distance outlier fractions (nullable for edgeR/limma), KS test uniformity on null p-values, and FDR monotonicity ($q \ge p$).
  5. Implement `check_pseudobulk_replication` blocking cell-level testing and enforcing donor-level aggregation.
  6. Implement `check_claim_evidence_binding` validating multi-metric assertions (log2FC, NES, padj) within numerical tolerance ($\pm 0.01$), directional agreement, and FDR monotonicity between report text and data tables.
- **Terminal Verification Command**:
  ```bash
  pytest tests/test_scientific_checks.py -v
  ```

---

### Milestone M2: Visual & Data Inspection Tools

- **Objective**: Build bounded table preview tools, server-side data pagination, plot contract generators, and on-demand thumbnail image renderers.
- **Target Files**:
  - `rna_agent/tools/visual_tools.py`
  - `rna_agent/tools/data_preview.py`
  - `tests/test_data_visual_tools.py`
- **Implementation Steps**:
  1. Implement `get_table_preview` supporting server-side sorting, column filtering, five-number statistical summaries, and hard row clamping at `limit <= 50`.
  2. Implement `render_plot_view` generating compact JSON plot contracts (Volcano, MA, PCA, ComplexHeatmap) for client-side WebGL rendering.
  3. Implement `inspect_plot_image` generating downsampled 512x512 high-contrast PNG thumbnails using `matplotlib` / `kaleido` for multimodal vision inspection without context flooding.
- **Terminal Verification Command**:
  ```bash
  pytest tests/test_data_visual_tools.py -v
  ```

---

### Milestone M3: Asynchronous Nextflow / nf-core Pipeline Runner

- **Objective**: Build the Nextflow execution controller, preflight schema validator, background process supervisor with SIGKILL/deadlock detection, and MultiQC/count ingestion engine.
- **Target Files**:
  - `rna_agent/nextflow/validator.py`
  - `rna_agent/nextflow/runner.py`
  - `rna_agent/nextflow/monitor.py`
  - `rna_agent/nextflow/ingest.py`
  - `tests/test_nextflow_runner.py`
- **Implementation Steps**:
  1. Implement `validator.py` parsing parameters and samplesheet CSV syntax against nf-core JSON schemas.
  2. Implement `runner.py` launching Nextflow asynchronously with `-bg` and `-resume`, returning an immutable `JobTicket` immediately with failure support.
  3. Implement `monitor.py` pairing filesystem trace watching with active OS PID supervision (`os.kill(pid, 0)` every 15s) for SIGKILL / host OOM detection, a 15-minute watchdog heartbeat timer, and defensive `.command.err` preemption handling.
  4. Implement `ingest.py` extracting scalar QC KPIs from `multiqc_data.json` and registering output count matrices directly into the scientific object store as `Dataset` records.
- **Terminal Verification Command**:
  ```bash
  pytest tests/test_nextflow_runner.py -v
  ```

---

### Milestone M4: Modular RNA-seq Skills & Self-Evaluation Engine

- **Objective**: Package the modular RNA-seq skills library and build the programmatic `<self_eval_criteria>` numeric threshold evaluation engine.
- **Target Files**:
  - `skills/bulk-rnaseq-de/SKILL.md` (and steps)
  - `skills/scrnaseq-core/SKILL.md` (and steps)
  - `skills/pathway-enrichment/SKILL.md` (and steps)
  - `skills/nfcore-rnaseq-orchestration/SKILL.md` (and steps)
  - `skills/figure-synthesis-report/SKILL.md` (and steps)
  - `rna_agent/skills/engine.py`
  - `rna_agent/skills/eval_parser.py`
  - `tests/test_skills_engine.py`
- **Implementation Steps**:
  1. Author complete markdown playbooks and step guides for all 5 skills matching Section 4 specifications.
  2. Implement `eval_parser.py` parsing `<self_eval_criteria>` XML tags and evaluating metric, operator, and threshold bounds against step receipts.
  3. Implement `engine.py` providing dynamic skill injection into the Lead Analyst prompt context based on detected assay types.
- **Terminal Verification Command**:
  ```bash
  pytest tests/test_skills_engine.py -v
  ```

---

### Milestone M5: Fresh-Context Reviewer & Evidence Audit Harness

- **Objective**: Implement the fresh-context Reviewer session runner, Evidence Packet builder, comprehensive 5-step rubric engine, and structured audit receipt generator.
- **Target Files**:
  - `rna_agent/reviewer/agent.py`
  - `rna_agent/reviewer/prompt.py`
  - `rna_agent/reviewer/packet_builder.py`
  - `rna_agent/reviewer/claim_extractor.py`
  - `rna_agent/reviewer/rubric.py`
  - `tests/test_reviewer.py`
- **Implementation Steps**:
  1. Implement `agent.py` spawning an isolated Reviewer conversation bound exclusively to the read-only MCP toolset: `["get_table_preview", "inspect_plot_image", "check_claim_evidence_binding", "get_nextflow_job_status"]`.
  2. Implement `packet_builder.py` assembling the Evidence Packet: data hashes, execution receipts, deterministic check logs, and bounded table queries.
  3. Implement `claim_extractor.py` extracting atomic empirical assertions from markdown report text.
  4. Implement `rubric.py` executing the comprehensive 5-step rubric: upstream check receipt status audit, sample cohort manifest matching, multi-metric value tolerance, FDR monotonicity, directional agreement, and universe validity, emitting `reviewer_audit_receipt.json`.
- **Terminal Verification Command**:
  ```bash
  pytest tests/test_reviewer.py -v
  ```

---

### Milestone M6: Lead Analyst Loop, End-to-End Orchestration & Benchmarks

- **Objective**: Integrate the master 9-stage orchestration lifecycle with atomic turn-pair compaction, reproduce the Love et al. 2015 Airway benchmark, and execute the 20-task A/B evaluation suite.
- **Target Files**:
  - `rna_agent/core/state_machine.py`
  - `rna_agent/core/context_manager.py`
  - `rna_agent/core/ledger.py`
  - `rna_agent/core/dag.py`
  - `rna_agent/server.py`
  - `benchmarks/airway_benchmark.py`
  - `benchmarks/ab_evaluation.py`
  - `benchmarks/synthetic_injectors.py`
  - `tests/test_end_to_end_orchestration.py`
- **Implementation Steps**:
  1. Connect `StateMachine`, `ContextManager` (Persistent State Anchor + sliding $N=4$ window enforcing strict atomic pairing of `tool_calls` and `tool_call_id` to eliminate API 400 Bad Request errors), `OperationLedger`, and `DAGTracker` into `server.py`.
  2. Execute `airway_benchmark.py`: run the complete autonomous analysis on Love et al. 2015 Airway data; verify that exported results match published statistics within $\pm 0.01$ log2FC.
  3. Execute `ab_evaluation.py`: run the 20-task benchmark suite comparing Arm A (Lead Alone) vs Arm B (Lead + Reviewer); assert $\ge 95\%$ flaw detection recall in Arm B.
- **Terminal Verification Command**:
  ```bash
  pytest tests/test_end_to_end_orchestration.py -v
  ```

---

## 7. Verification & Acceptance Gates

To guarantee that Codex implements the platform to publication standards, the implementation must satisfy these four objective acceptance gates:

### Gate 1: Tool Schema Completeness
- All 15 tools exposed in MCP must validate against JSON Schema Draft 2020-12.
- Zero truncated fields, zero empty descriptions, zero undefined object properties.
- Bounded limits enforced on all queries (`limit <= 50`).

### Gate 2: Statistical & Causal Governance
- All linear models verified full rank with residual df $\ge 2$ prior to fitting.
- Negative binomial GLMs applied exclusively to non-negative integer counts.
- Multiple testing enforces Benjamini-Hochberg guarantee where $q \ge p$.
- Pseudoreplication at the cell level in single-cell assays strictly blocked.

### Gate 3: Reproducibility & Lineage Integrity
- Every analysis produces an immutable SHA-256 Merkle DAG root.
- Clean standalone scripts (`run_pipeline.R` / `run_pipeline.py`) compiled via backward AST program slicing execute independently and reproduce exact result hashes.
- Environment locked with pinned `uv.lock` and `renv.lock`.

### Gate 4: Benchmark Verification & Flaw Detection
- Full reproduction of the Love et al. 2015 Airway benchmark with zero manual interventions.
- 20-task A/B benchmark demonstrates $>95\%$ sensitivity to injected statistical and experimental flaws when the Fresh-Context Reviewer is active.

---
**End of Master Codex Implementation Plan**
