# 05 — Codex and Model Context Protocol (MCP) Integration Specification

- **Status:** Target Integration & Protocol Specification (Phase 2)
- **Audited Baseline:** `HEAD` `18161fdabd162084732a4dd3c74e1ea42254a81b` (delta from `2026-11-23` audit `ad1d74724e1e4d12a3c0fe6840e6521bd34a445b`; see [01-baseline-audit-and-gaps.md](01-baseline-audit-and-gaps.md))
- **Verified Official Standards Date:** `2026-11-26` (MCP `2025-11-25` & `2026-07-28`; OpenAI Codex docs & `openai/codex` verified live)
- **Companion Specifications:** [README.md](README.md) · [01-baseline-audit-and-gaps.md](01-baseline-audit-and-gaps.md) · [02-problem-space-and-methods.md](02-problem-space-and-methods.md) · [03-adaptive-research-design.md](03-adaptive-research-design.md) · [04-system-architecture-and-governance.md](04-system-architecture-and-governance.md) · [06-milestones-and-evaluation.md](06-milestones-and-evaluation.md)

---

## 1. Verified Official Codex & MCP Baseline

All integration mechanics below are grounded in live-verified official OpenAI Codex and Model Context Protocol specifications (not legacy assumptions).

### 1.1 OpenAI Codex Official Surfaces, Configuration & Security Baseline

| Capability Area | Verified Current Specification | Official Reference |
|---|---|---|
| **Codex Surfaces** | **Codex CLI** (`@openai/codex`, Rust `codex-rs`, interactive TUI + headless `codex exec`); **Codex IDE Extension** (VS Code, Cursor, Windsurf); **Codex Desktop App** (visual multi-agent workspace, standalone and integrated into ChatGPT desktop app); **Codex Cloud / Web** (isolated remote GitHub/GitLab containers — no access to local private files or loopback Studio); **OpenAI Agents SDK** (`MCPServerStdio`, `MCPServerStreamableHttp`, `MCPServerSse`, `HostedMCPTool`). Legacy `codex mcp-server` has migrated toward Codex app-server architecture. | `https://developers.openai.com/codex/mcp`<br>`https://github.com/openai/codex`<br>`https://openai.github.io/openai-agents-python/mcp` |
| **Configuration Hierarchy (`config.toml`)** | Global defaults at `~/.codex/config.toml` (or `$CODEX_HOME/config.toml`), overridden per-repo by `.codex/config.toml` **only when the project is trusted** (`[projects."<path>"] trust_level = "trusted"`). Enterprise/admin policy constraints live in `requirements.toml`. | `https://developers.openai.com/codex/config-reference` |
| **`AGENTS.md` Discovery & 32 KiB Budget** | Assembled once per run from global (`~/.codex/AGENTS.override.md` else `AGENTS.md`, first non-empty) down from Git/project root to CWD. At most one file per directory (`AGENTS.override.md` > `AGENTS.md` > `project_doc_fallback_filenames`). Closer directories override parents. **Hard total cap: `project_doc_max_bytes` defaults to `32,768` bytes (32 KiB) and silently truncates beyond that limit.** | `https://developers.openai.com/codex/config-reference`<br>`https://agents.md` |
| **Codex Skills (`.agents/skills/`)** | Open Agent Skills format: directories under `.agents/skills/` (or `.codex/skills/`, user/admin/system/plugin scopes) containing required `SKILL.md` (YAML frontmatter `name`, `description`) plus optional `references/`, `scripts/`, `assets/`. Progressive disclosure injects only name/description/path upfront; full instructions load on explicit `$skill-name` / `/skills` invocation or implicit match (opt-out via `allow_implicit_invocation: false` in `agents/openai.yaml`). | `https://developers.openai.com/codex/skills` |
| **Codex Plugins & Marketplaces** | Portable **Agent Plugins** format places `plugin.json` at plugin root with `"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"`, `skills`, `mcpServers: "./.mcp.json"`, and OpenAI-specific fields under `extensions["com.openai"]` (`interface`, `hooks`). **`.codex-plugin/plugin.json` (currently used in `integrations/codex/devharness/`) is retained only as a compatibility fallback.** Marketplaces live at `$REPO/.agents/plugins/marketplace.json` or `~/.agents/plugins/marketplace.json` and install via `/plugins` or `codex plugin marketplace add <root>`. | `https://developers.openai.com/codex/plugins`<br>`https://agent-plugins.org/schemas/1.0.0/plugin.schema.json` |
| **MCP Registration (`[mcp_servers.<name>]`)** | Supports **STDIO** (`command`, `args`, `cwd`, `env`, `env_vars` allowlist forwarded from host shell) and **Streamable HTTP** (`url`, `bearer_token_env_var`, OAuth via `codex mcp login <name>` supporting PKCE/CIMD/DCR). Controls: `enabled`, `required`, `startup_timeout_sec` (default **10 s**), `tool_timeout_sec` (default **60 s**), `enabled_tools`, `disabled_tools` (evaluated after `enabled_tools`), `default_tools_approval_mode`, and per-tool `[mcp_servers.<name>.tools.<tool>] approval_mode`. **Exact Codex `approval_mode` semantics:** `auto` (consult MCP `ToolAnnotations`), `prompt` (always prompt user — **blocked/fails under `approval_policy = "never"` / headless `codex exec`**), `writes` (prompt on non-readonly tools), and **`approve` (silently auto-allow without prompting — NEVER set `approve` on review-gated tools)**. CLI: `codex mcp add`, `codex mcp list`, `/mcp`. | `https://developers.openai.com/codex/mcp`<br>`https://developers.openai.com/codex/config-reference` |
| **Sandboxing & Named Permission Profiles** | `sandbox_mode`: `read-only`, `workspace-write` (default; protects `.git`, `.codex`, `.agents` RO; expands via `[sandbox_workspace_write] writable_roots`), `danger-full-access`. OS enforcement: macOS Seatbelt (`sandbox-exec`), Linux Bubblewrap (`bwrap`) + seccomp-BPF + network namespaces (Landlock is legacy fallback `use_legacy_landlock`), Windows native process isolation. Named profiles (`default_permissions`, `[permissions.<id>]` with `extends = ":workspace"`, `filesystem`, `network.unix_sockets`) are testable via `codex sandbox --permission-profile` (already used in [`src/devbench/engine/permissions.py`](../../../src/devbench/engine/permissions.py#L100-L172)). | `https://developers.openai.com/codex/security`<br>`https://developers.openai.com/codex/config-reference` |
| **Approval Policy Breaking Changes** | Valid `approval_policy` values: **`on-request`** (default), **`never`**, and **`{ granular = { sandbox_approval, rules, mcp_elicitations, request_permissions, skill_approval } }`**. **`approval_policy = "untrusted"` is RETIRED/UNSUPPORTED** (causes Codex startup failure; use `[projects."<path>"] trust_level = "untrusted"` instead). **`on-failure` is DEPRECATED.** | `https://developers.openai.com/codex/config-reference`<br>`https://developers.openai.com/codex/security` |
| **CRITICAL Sandbox Exemption for STDIO MCP** | Sandboxed shell commands have outbound network disabled and cannot reach `127.0.0.1` or `/var/run/docker.sock` unless explicitly opened. **Local STDIO MCP servers execute OUTSIDE Codex's command sandbox**, inheriting host user permissions — enabling Docker/Studio custody *without* weakening Codex's shell sandbox, while making the MCP server itself a privileged trust boundary. | `https://developers.openai.com/codex/security`<br>`https://github.com/openai/codex` |

### 1.2 Model Context Protocol (MCP) Official Specification & Python SDK Migration

Verified against `https://modelcontextprotocol.io/specification`, `https://modelcontextprotocol.io/specification/2026-07-28`, `https://modelcontextprotocol.io/specification/2025-11-25`, `https://modelcontextprotocol.io/extensions`, and `https://github.com/modelcontextprotocol/python-sdk`:

1. **`2025-11-25` (Handshake Era — Current Repo SDK Target):**
   - Stateful `initialize` / `notifications/initialized` lifecycle; STDIO and Streamable HTTP (`Mcp-Session-Id`) transports.
   - `outputSchema` + `structuredContent`, `ToolAnnotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint` — explicitly **untrusted hints**, not security contracts), Resources & Templates, Form & URL Elicitation (`elicitation/create`), Progress tokens (`progressToken`), Cancellation, and experimental SEP-1686 Tasks.
   - Currently implemented by `mcp>=1.29.1,<2` pinned in [`pyproject.toml`](../../../pyproject.toml#L25) (`.venv` has `1.29.1`).
2. **`2026-07-28` (Latest Final Spec — Target for `mcp` 2.x Upgrade):**
   - **Stateless Core:** Removes `initialize` handshake and `Mcp-Session-Id`; introduces `server/discover`, per-request `_meta` (`io.modelcontextprotocol/protocolVersion`, `io.modelcontextprotocol/clientCapabilities`), HTTP routing headers (`Mcp-Protocol-Version`, `Mcp-Method`, `Mcp-Name`), cacheable list metadata (`ttlMs`, `cacheScope`), and server-minted explicit handles for cross-call state (SEP-2567).
   - **Subscriptions:** Replaces HTTP GET/SSE and `resources/subscribe` with long-lived POST `subscriptions/listen` (`io.modelcontextprotocol/events`).
   - **Formal Deprecations (SEP-2577):** **Roots (`roots/list`)**, **Sampling (`sampling/createMessage`)**, and protocol **Logging** are deprecated (>= 1-year migration window). **This specification deliberately does NOT depend on Roots or Sampling.**
   - **Official Opt-In Extensions:**
     - **Tasks (`io.modelcontextprotocol/tasks`, SEP-1686 / SEP-2663):** Moved out of core into an extension (`CreateTaskResult`, `tasks/get`, `tasks/result`, `tasks/list`, `tasks/cancel`, `execution.taskSupport`). Python SDK 2.x adds session-scoped task security fixes.
     - **MCP Apps (`io.modelcontextprotocol/ui`):** `ui://` resources with MIME `text/html;profile=mcp-app`, `_meta.ui.resourceUri`, sandboxed iframe JSON-RPC over `postMessage` (`protocolVersion: "2026-01-26"`), deny-by-default CSP (`connectDomains`, `resourceDomains`, `frameDomains`).
     - **Skills (`io.modelcontextprotocol/skills`)** & **Authorization extensions** (OAuth CIMD/DCR/Enterprise).
3. **Migration Plan (`mcp` 1.x → 2.x):**
   - **Phase `M1`:** Stay on `mcp>=1.29.1,<2` (`2025-11-25`) for immediate universal client compatibility while designing all 13 tools to be **stateless-core ready** (explicit server-minted `session_id`, `plan_id`, `run_id`, `artifact_id` handles — already aligned with SEP-2567 — plus explicit `submit`/`status`/`cancel`/`receipt` tools).
   - **Phase `M3`/`M4`:** Upgrade to `mcp>=2.0` once Codex CLI/App, Claude Code, and Cursor all ship stable `2026-07-28` + `io.modelcontextprotocol/tasks` support, layering `tasks/*` as an opt-in adapter over the daemon's existing job supervisor without breaking the 13 core tools.

### 1.3 Explicit Ambiguity & Surface-Parity Flags (Codex CLI vs Desktop App vs IDE)

The following MCP/Codex features exhibit **unverified, partial, or surface-dependent behavior** as of `2026-11-26` and MUST NOT be treated as mandatory dependencies:

| Feature | Observed / Verified Status | Mandatory Design Safeguard |
|---|---|---|
| **MCP Apps (`ui://` / `io.modelcontextprotocol/ui`)** | Supported in Claude Web/Desktop, VS Code/Copilot, M365 Copilot, Goose; marked Partial/Dev-Mode in ChatGPT; **Codex CLI (TUI) cannot render iframes**, and `openai/codex` tracks desktop iframe/guest-attachment edge cases and CLI-vs-App plugin MCP sync drift. | Strictly progressive enhancement (`card_html` readback only). Always emit complete bounded text + `structuredContent` + `studio_url` + CLI `launch_hint`. Never gate approvals on `ui://` buttons. |
| **MCP Elicitation (`elicitation/create`)** | Supported in `codex-rs/rmcp-client` and `app-server-protocol` (`thread/increment_elicitation`), **but headless `codex exec` cannot answer interactive elicitations** and may fail/skip. | Negotiate via `clientCapabilities.elicitation`; default fallback returns `status: "awaiting_review"` with `plan_id`, `plan_sha256`, `studio_url`, and CLI command `devbench approve`. |
| **MCP Resources & Prompts** | `rmcp` implements `list_resources`/`read_resource`, but automatic resource surfacing and slash-command exposure for MCP Prompts remain uneven across CLI vs App-Server (`openai/codex` tracked issues). | Mirror every inspectable artifact/receipt as a bounded tool (`query_artifact_table`, `get_run_receipt`); ship all workflows as **Skills** (`SKILL.md`), never solely as MCP Prompts. |
| **MCP Tasks (`io.modelcontextprotocol/tasks`)** | Compatibility tracked in `openai/codex` and `python-sdk` 2.x, **not verified GA** across Codex CLI, App, Claude Code, and Cursor. | Keep explicit non-blocking `submit_reviewed_run`, `get_run_status`, `cancel_run`, `get_run_receipt` tools permanently. |
| **MCP Progress Notifications** | `progressToken` exists in `rmcp`, but live TUI/App rendering is **unverified**, and Codex's default `tool_timeout_sec = 60` terminates long blocking calls. | Every MCP tool returns in `< 5 s`. Progress is persisted in daemon SQLite + `progress.json`, read via `get_run_status` (and optionally mirrored to `notifications/progress` if requested). |
| **MCP Sampling (`sampling/createMessage`)** | Experimental `thread/sample` in Codex app-server, **formally deprecated in MCP `2026-07-28` (SEP-2577)**. | Do not use. Independent Critic runs via explicit coordinator/adapter invocation. |
| **`codex plugin add` CLI Parity** | `codex plugin marketplace add` is verified in docs and [`test_plugin.py:182`](../../../integrations/codex/tests/test_plugin.py#L182); non-interactive `codex plugin add` vs interactive `/plugins` UI parity varies across Codex releases. | Smoke-test both plugin install and direct `codex mcp add` in isolated `CODEX_HOME` during release verification. |

---

## 2. Supported Clients & Graceful Degradation Matrix

| Client Surface | Transport | Skills / Instructions | MCP Apps (`ui://`) | Elicitation | Private / Clinical Data Policy | Degradation Strategy |
|---|---|---|---|---|---|---|
| **Codex CLI** (`codex`, `codex exec`) | STDIO shim (default) or Loopback Streamable HTTP | `.agents/skills/` + `AGENTS.md` | Not rendered (TUI) | Interactive TUI only (`codex exec` unsupported) | Blocked on MCP; handoff to `--offline-only` Studio / local CLI | Full text + `structuredContent` + `studio_url` + `devbench approve` CLI hint. |
| **Codex Desktop App & IDE Extension** | STDIO shim or Loopback Streamable HTTP (via shared `config.toml` / plugin) | `.agents/skills/` + `AGENTS.md` + Plugin picker | Optional progressive enhancement (flagged partial) | Supported when interactive | Blocked on MCP; handoff to `--offline-only` Studio | Render `ui://` cards if host advertises `io.modelcontextprotocol/ui`; fall back cleanly to Markdown + `studio_url`. |
| **Claude Code** | STDIO (`claude mcp add --transport stdio`) | Compatible `SKILL.md` + `CLAUDE.md` pointer | Partial / varies | Partial | Blocked on MCP; handoff to `--offline-only` Studio | Standard `tools/call` JSON + bounded Markdown summaries. |
| **Cursor** | STDIO (`.cursor/mcp.json`) | `.cursor/rules` / compatible `SKILL.md` | Partial / varies | Partial | Blocked on MCP; handoff to `--offline-only` Studio | Standard `tools/call` JSON + bounded Markdown summaries. |
| **OpenAI Agents SDK** | `MCPServerStdio` / `MCPServerStreamableHttp` | System prompt + `require_approval` hooks | N/A | Via SDK approval callbacks | Synthetic/public automated evaluation (`M5`) only | Strict `outputSchema` parsing + programmatic polling loop. |
| **Generic MCP Client** | STDIO (`2025-11-25` / future `2026-07-28`) | N/A | Fallback text | Fallback `awaiting_review` | Blocked on MCP; handoff to Studio | Zero client-specific extensions required. |
| **Codex Cloud / Web** | **Not supported for local private data** | Repo `AGENTS.md` / `.agents/skills/` only | N/A | N/A | **Hard refused** (remote container cannot hold private local vault) | Only usable for code development/CI on synthetic fixtures, never private RNA-seq research. |

---

## 3. Installation, Configuration & Dynamic Runtime Discovery

### 3.1 Portable Agent Plugin Layout (`integrations/codex/devharness/`)

Upgrade the plugin template to a single canonical root manifest at `integrations/codex/devharness/plugin.json` validating against `https://agent-plugins.org/schemas/1.0.0/plugin.schema.json` (**`ADR-23` / `UD-08`**). By default, `configure.py` emits root `plugin.json` AND deterministically generates `.codex-plugin/plugin.json` as a compatibility mirror (`--no-legacy-codex-manifest` opt-out), guarded by a unit test enforcing field parity (`name`, `version`, `skills`, `mcpServers`) and a sunset trigger to drop the legacy mirror once the pinned minimum Codex CLI/App build resolves root `plugin.json` across 2 consecutive releases:

```text
integrations/codex/devharness/
├── plugin.json                        # Root portable manifest ($schema: agent-plugins.org/schemas/1.0.0)
├── .mcp.json                          # Inactive template {"mcpServers": {}} until configured
├── scripts/
│   └── configure.py                   # Generates machine-local copy + optional marketplace.json
├── skills/                            # Modular progressive-disclosure skills (see Section 3.4)
│   ├── rna-research-core/SKILL.md
│   ├── scrna-snrna-qc-integration/SKILL.md
│   ├── pseudobulk-and-de/SKILL.md
│   ├── composition-and-da/SKILL.md
│   ├── trajectory-and-programs/SKILL.md
│   ├── spatial-and-ccc/SKILL.md
│   ├── method-benchmarking-and-critic/SKILL.md
│   └── private-data-studio/SKILL.md
├── README.md
└── LICENSE
```

**Target Root `plugin.json` Schema:**
```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "devharness",
  "version": "0.3.0",
  "description": "Governed local single-cell, single-nucleus, and general RNA-seq research engine.",
  "skills": "./skills/",
  "mcpServers": "./.mcp.json",
  "extensions": {
    "com.openai": {
      "interface": {
        "displayName": "DevHarness RNA Research",
        "shortDescription": "Inspect RNA datasets, check design identifiability, and run governed scRNA/bulk workflows.",
        "category": "Education & Research",
        "capabilities": ["Read", "Write"],
        "defaultPrompt": [
          "Inspect my single-cell H5AD input and donor metadata.",
          "Check whether my study design supports donor-level differential state testing.",
          "Check available local Python, R, and container runtimes."
        ]
      }
    }
  }
}
```

### 3.2 Project-Scoped vs User-Scoped Setup Flows

1. **Project-Scoped Setup (Recommended for Thesis Investigations):**
   - Place `.codex/config.toml` in the research project (mark `[projects."<abs-path>"] trust_level = "trusted"` in `~/.codex/config.toml`) and `.agents/skills/` for domain skills:
   ```toml
   [mcp_servers.devharness]
   command = "/abs/path/to/bio-agent-harness/.venv/bin/devbench"
   args = [
     "mcp",
     "--input-root", "/abs/path/to/readonly-data",
     "--workspace", "/abs/path/to/investigation-workspace",
     "--no-auto-browser"
   ]
   cwd = "/abs/path/to/investigation-workspace"
   startup_timeout_sec = 15
   tool_timeout_sec = 60
   enabled = true
   # Recommended server-level policy: consult honest ToolAnnotations ("auto") or prompt on writes ("writes").
   # NEVER set approval_mode = "approve" (silently auto-approves!) on execution tools.
   default_tools_approval_mode = "auto"

   [mcp_servers.devharness.env]
   OMP_NUM_THREADS = "1"
   OPENBLAS_NUM_THREADS = "1"
   MKL_NUM_THREADS = "1"
   VECLIB_MAXIMUM_THREADS = "1"
   NUMEXPR_NUM_THREADS = "1"
   NUMBA_NUM_THREADS = "1"
   # Primary runtime uses uv-managed python-build-standalone (patched sqlite3 >= 3.51.3 built in);
   # macOS secondary Homebrew fallback only when using a non-uv system/Conda Python (populated by configure.py when needed):
   # DYLD_LIBRARY_PATH = "/opt/homebrew/opt/sqlite/lib"

   # Optional stricter client-side prompt on every run submission (interactive TUI/App only;
   # note that approval_mode = "prompt" is rejected under headless approval_policy = "never"):
   # [mcp_servers.devharness.tools.submit_reviewed_run]
   # approval_mode = "prompt"
   ```
   - **Two-Layer Approval Interaction (Codex `approval_mode` vs Daemon `approval_required`):**
     - Codex's tool-level `approval_mode` (`auto` | `writes` | `prompt` | `approve`) cannot distinguish an autonomous public `E1`/`E2` Tier 1 QC run from a confirmatory `E5` fit or a `private`-data run on the same `submit_reviewed_run` tool.
     - Therefore, the **recommended default** sets `default_tools_approval_mode = "auto"` (or `"writes"` when operators prefer prompting on every mutating MCP call), while the **daemon enforces authoritative plan-level gating** via `freeze_analysis_plan`'s `approval_required` field:
       - Public/synthetic `E1`/`E2` Tier 1 diagnostics (`approval_required = "none"`): `submit_reviewed_run` enqueues immediately without repetitive prompts.
       - Public/synthetic `E5` confirmatory fits, scientific waivers, or Tier 2 code when policy requires confirmation (`approval_required = "codex_native"` or `"local_studio_or_cli"`): unless an explicit approval grant already exists in `ScientificAuthority` (recorded via `devbench approve`, Companion Studio, or interactive Codex `prompt` + confirmation token), `submit_reviewed_run` returns `category: "awaiting_review"` with `{plan_id, plan_sha256, studio_url, launch_hint: "devbench approve ..."}`.
       - All `private`/`clinical` runs (`approval_required = "local_studio_or_cli"`): **always** held until local `--offline-only` human approval is recorded via Studio or `devbench approve`; Codex client settings can never override this server-side block.
     - Operators who want Codex itself to prompt on *every* run submission in interactive TUI/App sessions may uncomment `approval_mode = "prompt"` on `submit_reviewed_run` (keeping it unset for headless `codex exec` / `approval_policy = "never"` benchmark sweeps).
   - Optional **Loopback Streamable HTTP** connection (when sharing one running workspace daemon across Codex App, IDE, and Workbench):
   ```toml
   [mcp_servers.devharness_daemon]
   url = "http://127.0.0.1:8765/mcp"
   bearer_token_env_var = "DEVHARNESS_DAEMON_TOKEN"
   tool_timeout_sec = 60
   ```
2. **Clean-Machine CLI & Daemon Bootstrap (`devbench` Subcommands):**
   - `devbench mcp --input-root <dir> --workspace <dir> [--no-auto-browser]`: thin STDIO shim launched by Codex/Claude/Cursor; acquires `<workspace>/.devbench-daemon/daemon.lock` (`flock`), checks `daemon.json` PID + `create_time` (reaping stale sockets/files if dead), auto-spawns `devbench daemon start` detached (`start_new_session=True`) if absent, and proxies JSON-RPC over a `0o600` Unix domain socket.
   - `devbench daemon start|status|stop --workspace <dir> [--input-root <dir>] [--http-port <port>] [--offline-only] [--enable-streamable-http] [--idle-timeout-sec <sec>] [--auto-approve-tier2-public]`: explicit daemon management (`M1-1` landed `4b90e76` / `292d16f`, **`ADR-22`**); creates `0o700` `<workspace>/.devbench-daemon/` with `daemon.lock`, `daemon.json`, `0o600` `daemon.token` (256-bit bearer secret), configurable idle auto-shutdown (defaulting to 30 min idle after last active job and client disconnect; `0` disables), and optional out-of-band `auto_approve_tier2_public` flag (**`ADR-19`**).
   - **macOS `AF_UNIX` 104-Byte Limit Mitigation:** Whenever `<workspace>/.devbench-daemon/daemon.sock` would exceed 96 bytes (macOS `sockaddr_un.sun_path` cap is 104 bytes), the daemon binds a short hashed socket at `$XDG_RUNTIME_DIR/devbench-ipc-<uid>/s-<sha256(workspace)[:16]>.sock` (or `$TMPDIR/devbench-ipc-<uid>/s-<sha256(workspace)[:16]>.sock` on macOS, verifying `0o700` owner-only parent dir with `O_NOFOLLOW` and `0o600` socket permissions) and writes its canonical `socket_path` into `<workspace>/.devbench-daemon/daemon.json`.
   - `devbench approve --workspace <dir> --session-id <id> --plan-id <id> --plan-sha256 <sha> [--rationale <text>]`: records actor-attributed approval directly into `ScientificAuthority` from any local terminal.
   - `devbench workbench --workspace <dir> --input-root <dir> [--offline-only] [--port <port>]`: opens/exposes the daemon's authenticated loopback Companion Studio UI.
3. **User-Scoped Plugin / CLI Registration:**
   - Via portable plugin marketplace: `configure.py --python ... --input-root ... --workspace ... --output <marketplace>/devharness --write-marketplace <marketplace>` → `codex plugin marketplace add <marketplace> --json` → install via `/plugins` or `codex plugin add devharness@devharness-local`.
   - Direct CLI registration:
     - **Codex:** `codex mcp add devharness -- "$repo/.venv/bin/devbench" mcp --input-root "$input_root" --workspace "$workspace" --no-auto-browser` *(add `--env "DYLD_LIBRARY_PATH=$DYLD_LIBRARY_PATH"` only when using secondary non-`uv` Homebrew SQLite fallback)*
     - **Claude Code:** `claude mcp add --transport stdio --scope local devharness -- "$repo/.venv/bin/devbench" mcp --input-root "$input_root" --workspace "$workspace" --no-auto-browser`
     - **Cursor:** identical `command`/`args` entry in `.cursor/mcp.json`.
4. **Patched SQLite WAL (`>= 3.51.3`) via `uv`-Managed Python (`ADR-21` / `UD-06`):** Do **NOT** bundle `pysqlite3` or `pysqlite3-binary` (no macOS `arm64` wheels exist on PyPI, and mixing `pysqlite3` with stdlib `sqlite3` used by `langgraph-checkpoint-sqlite` risks mixed-library WAL corruption). Standardize dev, user, and CI setups on a `uv`-managed `python-build-standalone` runtime (`.python-version` + `uv.toml` `python-preference = "only-managed"` + `uv sync --locked --python-preference only-managed`) bundling patched stdlib `sqlite3 >= 3.51.3` identically across macOS `arm64`, Linux `x86_64`/`arm64`, and Windows — eliminating `DYLD_LIBRARY_PATH` and macOS SIP issues. Retain `configure.py`'s macOS Homebrew probe (`/opt/homebrew/opt/sqlite/lib` and Intel `/usr/local/opt/sqlite/lib`) plus optional Linux `DEVBENCH_SQLITE_LIB` strictly as secondary fallback for non-`uv` system/Conda installs, emitting an explicit `uv python install` remediation message if a SIP-protected macOS binary strips `DYLD_LIBRARY_PATH`.

### 3.3 Dynamic Runtime Discovery (`inspect_runtime_capabilities`)

Instead of relying solely on static probe results frozen when `configure.py` ran (which misses newly pulled images, GPU attachment, or R/conda updates), the agent calls `inspect_runtime_capabilities` at session start (or before planning). It reports live availability of:
- Python/uv environment + installed scverse/stats packages (`anndata`, `scanpy`, `scvi-tools`, `mudata`, `spatialdata`, `tiledbsoma`, `pyarrow`, `pydeseq2`),
- R / Bioconductor runtime (`Rscript` version + installed packages when local R is configured),
- Container engines (`DockerBackend`, rootless `PodmanBackend` with cgroups v2 delegation probe status, HPC `ApptainerBackend` with user/net namespace isolation probe status, and opt-in OS fallback `isolation_backend` / `oci_isolated` capability flags per **`ADR-20`**), daemon health, host architecture (`arm64`/`amd64`), and locally present digest-pinned Tier 1 & Tier 2 images,
- Hardware budgets (CPU cores, available RAM, CUDA/MPS GPU availability, free workspace disk),
- Patched SQLite WAL status, Nextflow availability, and running Companion Workbench status.

### 3.4 Modular `.agents/skills/` Decomposition & `AGENTS.md` Budget

To keep `AGENTS.md` strictly `< 8 KiB` target (`< 16 KiB` CI warning, `< 32 KiB` hard cap matching Codex's default `project_doc_max_bytes` ceiling so nested project docs are never silently truncated) and eliminate the monolithic 710-line `references/mcp-payload-contracts.md`, decompose instructions into **8 focused skills**:

| Skill Directory | Progressive-Disclosure Scope |
|---|---|
| `rna-research-core` | 13-tool control-plane workflow, `E0`–`E6`/`E_ABSTAIN` lifecycle, `L1`–`L4` claim discipline, attribution hygiene, non-fabrication, artifact slicing. |
| `scrna-snrna-qc-integration` | Droplet/ambient (`CellBender`/`SoupX`), snRNA intronic/nuclear QC (handling absent mito genes cleanly), `scDblFinder`/Scrublet, donor/SNP forensics (`souporcell`/`Vireo`), `scib-metrics` integration comparison. |
| `pseudobulk-and-de` | `COUNT-1` ancestry, donor-unit design rank/confounding, `edgeR v4` / `dreamlet` / `DESeq2` / `limma-voom`, selective/post-clustering separation (`ClusterDE`), LODO sensitivity. |
| `composition-and-da` | `miloR` neighborhood DA, multi-chain `scCODA` / `pertpy`, compositional reference choice, bulk deconvolution limitations. |
| `trajectory-and-programs` | Pseudotime vs collection time, `tradeSeq`/donor splines, cNMF/gene programs, `ORA`/`fgsea`/`CAMERA`/`decoupleR` tested-universe pinning, non-causal GRN scope. |
| `spatial-and-ccc` | `SpatialData` coordinate frames/units, NB-aware SVG (`SPARK-X`), multi-donor spatial inference, curated LR references (`LIANA`/`CellChat`), coexpression vs physical signaling. |
| `method-benchmarking-and-critic` | Freezing comparison criteria before results (`E4`), Tier 2 exploratory code authoring best practices, scFM vs classical baseline discipline, Independent Critic invocation & Tier 2 → Tier 1 promotion. |
| `private-data-studio` | Sticky private/clinical handling, `--offline-only` Companion Studio handoffs, zero-leakage verification, clinical release hold (`L4`). |

**Single-Source Skill Distribution & Legacy-Tool Lint (`ADR-24`, `ADR-18`):**
- **Source of truth:** Commit the 8 modular skills once under `.agents/skills/<skill-name>/SKILL.md` (natively discovered by Codex CLI/App and modern Cursor).
- **Symlink-free client mirroring (`ADR-24`):** `configure.py --mirror-clients <claude|cursor|all>` (off by default for pure Codex installs) materializes **copies — never symlinks** (preserving `configure.py`'s no-symlink security rule and zip/Windows portability) along with `.devharness-mirror-manifest.json` SHA-256 checksums checked in CI via `configure.py --check-mirrors`: `claude` copies `.agents/skills/*` into `.claude/skills/` (or `~/.claude/skills/`); `cursor` verifies native `.agents/skills/` discovery (copying to `.cursor/skills/` only when targeting legacy Cursor, with zero lossy `.cursor/rules/` translation). Never commit mirrored outputs to git.
- **Legacy-tool CI lint (`ADR-18`):** CI enforces a lint check failing if any file under `.agents/skills/` or general-research `AGENTS.md` cites legacy `devharness_server` tool names (`audit_expression_quality`, `fit_temporal_trajectory`, `infer_lineage`, `infer_regulatory_network`, etc.).

---

## 4. End-to-End Responsibility Matrix

| Component | Owns | Explicitly Forbidden From |
|---|---|---|
| **Codex / LLM** | Framing questions/aims (`E0`), critiquing study design, comparing candidate methods (`E4`), authoring Tier 2 Python/R scripts, synthesizing bounded receipts & negative findings (`E6`). | Reading raw multi-GB matrices into context; bypassing MCP custody via raw shell Docker/file scripts; self-approving `E5` fits or waivers; fabricating failed estimates. |
| **Skills (`.agents/skills/`) & `AGENTS.md`** | Domain methodology, best-practice checklists, progressive-disclosure tool patterns. | Serving as the sole enforcement mechanism for privacy, `COUNT-1`, or sandbox boundaries. |
| **13-Tool MCP Control Plane (`devbench mcp`)** | Strict JSON schema validation (`inputSchema` + `outputSchema`), honest `ToolAnnotations`, `< 5 s` non-blocking dispatch to daemon, bounded `artifact_ref` projections, deterministic error codes. | Hosting monolithic per-assay solver unions; running blocking compute; mutating host files outside the workspace. |
| **Local Research Engine Daemon** | Unified SQLite WAL (`ScientificAuthority` + `AdaptiveWorkflow` + `ThesisAimLifecycle` + `JobStore`), multi-pool async job supervision, out-of-core Vault, `EvidenceBroker` privacy redaction, Independent Critic dispatch. | Exposing unauthenticated loopback endpoints; sending private/clinical data over MCP; running compute without container/sandbox isolation. |
| **Tier 1 & Tier 2 Workers** | Numerical execution (`--network none`, RO mounts, scoped RW stage, RSS/timeout watchdogs, deterministic seeds, content-addressed outputs & plot sidecars). | Accessing host network, `/var/run/docker.sock`, host home/credentials, or embargoed `PartitionVault` holdouts. |
| **Optional Loopback Workbench UI** | Visual QC & interactive Plotly/figure inspection, private/clinical offline-only workflows, operator code/plan review & sign-off, thesis package export. | Auto-launching unsolicited browser windows in headless/SSH/CI sessions; bypassing `EvidenceBroker` or bearer/origin checks. |
| **Human Researcher / Operator** | Biological-unit & data-classification declarations, confirmatory (`E5`) & waiver approvals, visual plot inspection, Tier 2 → Tier 1 promotion sign-off, scientific judgment. | Forced manual clicks on routine public/synthetic exploratory runs. |

---

## 5. Complete Concrete Specification for the 13 MCP Control-Plane Tools

Every tool enforces `additionalProperties: false` on `inputSchema`, declares an explicit JSON `outputSchema`, returns identical JSON in `content[0].text` and `structuredContent`, enforces a `<= 64 KiB` response payload budget (using `artifact_ref` pointers for large outputs), and completes in `< 5 s`.

### 5.1 Shared Primitives: `artifact_ref` & Deterministic Error Envelope

```json
{
  "$defs": {
    "ArtifactRef": {
      "type": "object",
      "additionalProperties": false,
      "required": ["artifact_id", "sha256", "kind", "format", "representation_class", "claim_tier", "size_bytes"],
      "properties": {
        "artifact_id": { "type": "string", "pattern": "^[a-z0-9][a-z0-9._:-]{1,127}$" },
        "sha256": { "type": "string", "pattern": "^[0-9a-f]{64}$" },
        "kind": { "enum": ["expression_matrix", "metadata", "reference", "table", "embedding", "figure", "plot_sidecar", "model_checkpoint", "script", "replay_package"] },
        "format": { "enum": ["h5ad", "zarr", "h5mu", "spatialdata_zarr", "tiledbsoma", "parquet", "csv", "tsv", "json", "png", "svg", "plotly_json", "zip"] },
        "representation_class": { "enum": ["raw_reads_or_droplets", "count_1_raw_counts", "ambient_adjusted_counts", "size_factor_normalized", "integrated_latent_or_graph", "spatial_geometry_and_residuals", "aggregated_unit_counts", "derived_summary", "metadata_only", "reference_only"] },
        "claim_tier": { "enum": ["none", "L1_execution_verified", "L2_statistically_calibrated", "L3_scientifically_supported"] },
        "size_bytes": { "type": "integer", "minimum": 0 },
        "shape": { "type": ["array", "null"], "items": { "type": "integer", "minimum": 0 }, "maxItems": 4 },
        "schema_preview": { "type": ["object", "null"] }
      }
    },
    "ErrorResponse": {
      "type": "object",
      "additionalProperties": false,
      "required": ["error", "reason", "category", "remediation"],
      "properties": {
        "error": { "type": "string" },
        "reason": { "type": "string" },
        "category": { "enum": ["scientific_refusal", "awaiting_information", "awaiting_review", "private_data_local_studio_only", "runtime_prerequisite_missing", "input_scope_violation", "plan_identity_mismatch", "stale_revision_conflict", "resource_quota_exceeded", "invalid_request"] },
        "remediation": { "type": "string", "maxLength": 1000 },
        "studio_url": { "type": ["string", "null"] },
        "launch_hint": { "type": ["string", "null"] },
        "validation_errors": { "type": "array", "maxItems": 16, "items": { "type": "object", "required": ["loc", "type"], "properties": { "loc": { "type": "string" }, "type": { "type": "string" } } } }
      }
    }
  }
}
```

### 5.2 Summary Matrix of All 13 Tools & Honest `ToolAnnotations`

| # | Tool Name | `readOnlyHint` | `destructiveHint` | `idempotentHint` | `openWorldHint` | Optional `ui://` Card | Primary Lifecycle States |
|---|---|:---:|:---:|:---:|:---:|---|---|
| 1 | `inspect_runtime_capabilities` | `true` | `false` | `true` | `false` | — | `E0`, `E4` |
| 2 | `inspect_inputs` | `true` | `false` | `true` | `false` | — | `E0`, `E1` |
| 3 | `register_investigation_input` | `false` | `false` | `true` *(by digest)* | `false` | — | `E1`, `E2`–`E6` *(mid-session)* |
| 4 | `check_answerability` | `true` | `false` | `true` | `false` | — | `E0`, `E1`, `E_ABSTAIN` |
| 5 | `list_kernels_and_methods` | `true` | `false` | `true` | `false` | — | `E0`, `E2`, `E4` |
| 6 | `list_investigation_state` | `true` | `false` | `true` | `false` | — | `E0`–`E6` |
| 7 | `query_artifact_table` | `true` | `false` | `true` | `false` | — | `E2`, `E3`, `E5`, `E6` |
| 8 | `get_run_status` | `true` | `false` | `true` | `false` | `run-card` | `E2`–`E5` |
| 9 | `get_run_receipt` | `true` | `false` | `true` | `false` | `receipt-card` | `E2`–`E6` |
| 10 | `manage_research_aim` | `false` | `false` | `false` *(rev-fenced)* | `false` | `receipt-card` | `E0`, `E3`, `E5`, `E6` |
| 11 | `freeze_analysis_plan` | **`false`** *(fixed!)* | `false` | `false` | `false` | `plan-card` | `E2`–`E5` |
| 12 | `submit_reviewed_run` | `false` | `false` | `true` *(by plan triple)* | `false` | `run-card` | `E2`–`E5` |
| 13 | `cancel_run` | `false` | `false` | `true` | `false` | `run-card` | `E2`–`E5` |

> **Protocol Notes:** (1) `freeze_analysis_plan` replaces `plan_rna_workflow` and corrects `readOnlyHint` from `true` (`plugin_mcp.py:5564`) to **`false`** because freezing a plan writes immutable records to SQLite WAL and creates/updates investigation state. (2) `ToolAnnotations.openWorldHint` is a static boolean in `list_tools()`; `manage_research_aim` statically declares **`openWorldHint: false`** because `query_literature` never makes arbitrary open-world calls — it reads strictly through the host-side allowlisted **Daemon Literature Broker** (operator-approved PubMed/Europe PMC/CrossRef/bioRxiv allowlist with `EvidenceBroker` redaction and offline snapshot fallback, disabled under `private`/`clinical`). (3) Every 32-hex MCP `session_id` maps **1-to-1** to `lineage_id` in `ScientificAuthority`.

### 5.3 Concrete Tool Contracts (`inputSchema` & `outputSchema`)

#### 1. `inspect_runtime_capabilities`
- **Purpose:** Live probe of local Python/R packages, OCI engines & digest-pinned Tier 1/2 images, GPU/CPU/RAM/disk pools, SQLite WAL patch status, and Studio daemon state.
- **`inputSchema`:** `{ "include_images": boolean (default true), "include_packages": boolean (default true) }`
- **`outputSchema`:** `{ "daemon_version": string, "sqlite_wal_patched": boolean, "container_engines": [...], "available_images": [{"image_id", "lane", "architecture", "capabilities"}], "hardware": {"cpu_cores", "available_ram_mb", "gpus": [...], "free_disk_gb"}, "pools": {"cpu_slots", "gpu_slots", "max_concurrent_runs"}, "studio": {"running": boolean, "offline_only": boolean} }`

#### 2. `inspect_inputs`
- **Purpose:** Bounded, read-only, streaming inspection of local files/stores (`.h5ad`, `.zarr`, `.h5mu`, SpatialData, TileDB-SOMA, `.parquet`, `.csv`/`.tsv`, 10x MTX, FASTQ manifests) with **no 100 MB cap** and upgraded `COUNT-1` distribution forensics. Refuses `private`/`clinical`/`unclassified` or `_clinical_name()`-triggered disclosure (`category: "private_data_local_studio_only"`).
- **`inputSchema`:** `{ "inputs": [{"path": string, "file_kind": "expression"|"metadata"|"reference"|"manifest"|"spatial", "data_classification": "synthetic"|"public"|"private"|"clinical"|"unclassified", "clinical_metadata": boolean (default false)}] (1..32 items), "inspect_layers": boolean (default true) }`
- **`outputSchema`:** `{ "inputs": [{"canonical_path", "sha256", "merkle_root_sha256", "format", "size_bytes", "axes": {"observations", "features", "obs_columns" (<=128), "var_columns" (<=64), "layers", "obsm_keys"}, "count_1_forensics": {"integer_valued": boolean, "nonnegative": boolean, "suspected_transformed": boolean, "warnings": [...]}, "data_classification"}] }`

#### 3. `register_investigation_input` *(NEW — fixes `session_input_scope_mismatch`)*
- **Purpose:** Admit new datasets, reference atlases, ligand-receptor databases, gene-set collections, genotype panels, or external replication cohorts into an active investigation mid-session with explicit provenance and sticky classification custody, without invalidating unrelated completed branches.
- **`inputSchema`:** `{ "session_id": string (32-hex) | null, "expected_revision": integer (>=0) | null, "input": {"path": string, "file_kind": "expression"|"metadata"|"reference"|"manifest"|"spatial", "investigation_role": "primary_cohort"|"reference_atlas"|"gene_set_collection"|"ligand_receptor_resource"|"external_replication_cohort"|"genotype_panel"|"prior_study_table"|"follow_up_experiment", "data_classification": "synthetic"|"public"|"private"|"clinical"|"unclassified", "clinical_metadata": boolean (default false)}, "representation_class": enum (Section 5.1), "provenance_declaration": {"origin": "user_declaration"|"metadata_declaration"|"public_registry", "source": string, "biological_unit": string | null, "count_provenance": string | null} }`
- **`outputSchema`:** `{ "session_id": string, "revision": integer, "artifact": ArtifactRef | null, "opaque_artifact": {"artifact_id": string, "sha256": string} | null, "private_tainted": boolean, "category": "registered"|"private_data_local_studio_only", "studio_url": string | null, "launch_hint": string | null }`
- **Privacy Redaction Rule (`EvidenceBroker`):** Whenever the input is classified `private`, `clinical`, or `unclassified`, triggers `_clinical_name()` on path/headers, or matches a previously tainted SHA-256 (`has_private_input`), `artifact` MUST be `null` (zero `shape`, `schema_preview`, `size_bytes`, column names, or paths returned over MCP), `opaque_artifact` carries only `{artifact_id, sha256}`, `private_tainted` is `true`, `category` is `"private_data_local_studio_only"`, and `studio_url`/`launch_hint` direct the operator to `--offline-only` Studio.

#### 4. `check_answerability`
- **Purpose:** Evaluate whether a biological question (mapped to Axes `A0`–`A3`, `B1`–`B12` or **`B_OPEN`**, `C1`–`C2`) is identifiable and answerable given registered inputs, biological-unit hierarchy, forensic verification status, study design formula/contrast, and available Tier 1/Tier 2 runtimes.
- **`inputSchema`:** `{ "session_id": string | null, "question_text": string (1..2000), "axis_b_class": "B1"|...|"B12"|"B_OPEN" | null, "assay": string (supports standard assays plus "open"|"custom" for B_OPEN), "entry_point": string (supports standard entry points plus "open"|"custom" for B_OPEN), "artifact_ids": [string], "design": {"metadata_artifact_id": string, "formula": string, "contrast": {"factor", "numerator", "denominator"} | null, "biological_unit": string, "specimen_column": string | null, "capture_column": string | null, "batch_columns": [string], "forensics_verified": boolean} | null, "candidate_kernels": [string] }`
- **`outputSchema`:** `{ "status": "ready"|"awaiting_information"|"refused"|"tier2_exploratory_only", "reason": string, "epistemic_state": "E0"|"E_ABSTAIN", "axis_b_class": "B1"|...|"B12"|"B_OPEN", "design_diagnostics": {"full_rank": boolean, "rank": integer, "columns": integer, "residual_df": integer, "biological_units_per_group": object, "confounded_terms": [string]} | null, "missing_evidence": [string], "recommended_kernels": [{"kernel_id", "calibration_status", "assumptions", "runtime_ready": boolean}] }`

#### 5. `list_kernels_and_methods` *(NEW — wires `ScientificMethodPlanner` & Registry)*
- **Purpose:** Discover registered Tier 1 curated kernels (repository-wide and workspace-scoped promoted), Tier 2 base images, and user/session-registered exploratory methods, including their canonical `representation_class` contracts, calibration status (`L1`/`L2`), assumptions, and resource profiles.
- **`inputSchema`:** `{ "session_id": string | null, "axis_b_class": "B1"|...|"B12"|"B_OPEN" | null, "assay": string | null, "lane": "tier1_curated"|"tier2_exploratory"|"all" (default "all"), "include_parameter_schema": boolean (default false), "kernel_ids": [string] | null }`
- **`outputSchema`:** `{ "kernels": [{"kernel_id", "version", "lane", "promotion_scope": "repository"|"workspace"|null, "capability_axes", "required_representations", "calibration_status", "calibrated_regimes", "runtime_ready": boolean, "resource_profile", "parameter_schema": object | null}], "tier2_base_images": [{"image_id", "language", "packages_summary", "gpu_supported": boolean}] }`

#### 6. `list_investigation_state` *(NEW — durable continuity across compactions/restarts)*
- **Purpose:** Return a bounded snapshot of an investigation's current graph revision, epistemic state (`E0`–`E6` / `E_ABSTAIN`), aims, active/retired branches, registered artifacts (with stale/current markers), recent runs, and pending holds/approvals.
- **`inputSchema`:** `{ "session_id": string (32-hex), "include_retired_branches": boolean (default false), "artifact_limit": integer (1..100, default 50), "run_limit": integer (1..50, default 20) }`
- **`outputSchema`:** `{ "session_id": string, "revision": integer, "data_classification": string, "redacted": boolean, "epistemic_state": string, "aims": [...], "branches": [{"branch_id", "branch_kind", "status", "parent_branch_id", "aim_id"}], "artifacts": [{"artifact": ArtifactRef | null, "opaque_artifact": {"artifact_id", "sha256"} | null, "status": "current"|"stale"|"embargoed", "producer_step_id"}], "runs": [{"run_id", "plan_id", "status", "lane", "claim_tier"}], "pending_holds": [...], "studio_url": string | null, "launch_hint": string | null }`
- **Privacy Redaction Rule (`EvidenceBroker`):** On `private`/`clinical`-tainted sessions, `list_investigation_state` MUST set `redacted: true`, emit `artifact: null` + opaque `{artifact_id, sha256}` only (zero `shape`, `schema_preview`, `size_bytes`, filenames, or clinical text), strip free-text aim/explanation/finding strings and numeric metrics to opaque IDs/statuses/timestamps, and provide `studio_url` + `launch_hint` for `--offline-only` Studio inspection.

#### 7. `query_artifact_table` *(NEW — bounded out-of-core data inspection)*
- **Purpose:** Server-side bounded pagination, column projection, filtering, sorting, and aggregation over verified Parquet, AnnData `obs`/`var`, JSON summary, or plot-data sidecar artifacts — eliminating inline matrix dumps and `16 MB` truncation while preserving privacy/embargo checks (`private`/`clinical` and embargoed `PartitionVault` artifacts return `category: "private_data_local_studio_only"` or `"input_scope_violation"`).
- **`inputSchema`:** `{ "session_id": string, "artifact_id": string, "artifact_sha256": string (64-hex), "table_slot": "default"|"obs"|"var"|"plot_sidecar" (default "default"), "columns": [string] (max 32) | null, "filters": [{"column", "op": "eq"|"neq"|"lt"|"lte"|"gt"|"gte"|"in"|"contains", "value"}] (max 8), "order_by": [{"column", "descending": boolean}] (max 4), "limit": integer (1..200, default 50), "offset": integer (>=0, default 0), "aggregate": {"group_by": [string], "metrics": [{"column", "fn": "count"|"mean"|"median"|"min"|"max"|"sum"}]} | null }`
- **`outputSchema`:** `{ "artifact_id": string, "artifact_sha256": string, "total_matching_rows": integer, "returned_rows": integer, "truncated": boolean, "columns": [string], "rows": [[...]], "next_offset": integer | null }`

#### 8. `get_run_status`
- **Purpose:** Fast (`< 100 ms`) status read of a queued, running, awaiting-review, completed, failed, interrupted, or cancelled run, including multi-step / sweep / Nextflow / epoch progress, heartbeat age, resource pool placement, and checkpoint info.
- **`inputSchema`:** `{ "session_id": string, "run_id": string }`
- **`outputSchema`:** `{ "session_id": string, "run_id": string, "plan_id": string, "status": "queued"|"running"|"awaiting_review"|"completed"|"failed"|"interrupted"|"cancelled", "execution_lane": "tier1_curated"|"tier2_exploratory", "pool": string, "progress": {"completed": number, "total": number, "unit": string, "stage": string}, "heartbeat_age_sec": number | null, "elapsed_sec": number, "checkpoint_available": boolean, "tasks": [...], "warnings": [string], "studio_url": string | null, "launch_hint": string | null }`

#### 9. `get_run_receipt`
- **Purpose:** Read back the broker-verified, SHA-256 integrity-checked terminal receipt for a completed/failed non-private run, including scalar metrics, bounded preview tables (<= 20 rows/table), `artifact_ref` pointers (tables, figures, plot sidecars), Critic audit status, and explicit limitations.
- **`inputSchema`:** `{ "session_id": string, "run_id": string, "include_figure_preview": boolean (default false) }`
- **`outputSchema`:** `{ "session_id": string, "run_id": string, "plan_id": string, "plan_sha256": string, "status": "completed"|"failed"|"cancelled", "execution_lane": string, "claim_tier": "none"|"L1_execution_verified"|"L2_statistically_calibrated"|"L3_scientifically_supported", "ai_critic_verified": boolean, "clinical_release_allowed": false, "probability_bounds_status": "verified"|"not_evaluated"|"failed", "critic_audit": {"status": "passed"|"flagged"|"not_run", "critic_origin": "deterministic_verifier"|"ai_critic_review"|"human_expert_panel"|"hybrid", "findings": [string]}, "metrics": object, "summary_tables": [...], "outputs": {"<port>": ArtifactRef}, "figure_artifacts": [{"artifact": ArtifactRef, "sidecar": ArtifactRef, "verification_state": "produced_unverified"|"visually_verified"}], "limitations": [string], "replay_bundle_sha256": string, "studio_url": string | null }`

#### 10. `manage_research_aim`
- **Purpose:** Revision-fenced lifecycle management of research aims, branches, competing explanations, waivers, Joint BH multiplicity budgets, independent critic/blinded reviews, host-brokered literature custody, and reproducible thesis package builds.
- **`inputSchema`:** Discriminated union on **14 canonical `action` values**: `list` | `propose_aim` | `revise_aim` *(unlimited revisions; lifts `_MAX_REVISIONS=3`)* | `approve_aim` | `fork_branch` | `retire_branch` | `assess_explanation` | `record_waiver` | `freeze_joint_bh` | `release_joint_bh` | `request_critic_review` | `record_outcome` | `query_literature` | `build_research_package`, each carrying `session_id` and `expected_revision` (for mutations).
  - **`query_literature` contract:** `{ "action": "query_literature", "session_id": string, "expected_revision": integer, "phase": "pre_study"|"post_evidence", "query": string (1..500), "max_results": integer (1..25, default 10), "offline_snapshot_sha256": string (64-hex) | null, "evidence_binding": {"run_id": string, "receipt_sha256": string} | null }` (where `evidence_binding` is required when `phase = "post_evidence"`). Executes via the host-side read-only **Daemon Literature Broker** (strict PubMed/Europe PMC/CrossRef/bioRxiv allowlist, `EvidenceBroker`-redacted query without private identifiers, returning delimited `BEGIN_UNTRUSTED_LITERATURE_DATA` records with DOI/PMID, retrieval UTC timestamp, and retraction status, or resolving against a pinned local snapshot when `--offline-only`). **Hard refused** on `private`/`clinical`-tainted workspaces unless using an offline pinned snapshot.
- **`outputSchema`:** `{ "session_id": string, "revision": integer, "action": string, "status": string, "invalidated_steps": [string], "joint_bh_state": object | null, "literature_records": [...] | null, "critic_review_id": string | null, "package_root_sha256": string | null, "clinical_release_allowed": false, "aims": [...], "studio_url": string | null }`

#### 11. `freeze_analysis_plan` *(replaces monolithic `plan_rna_workflow`)*
- **Purpose:** Freeze an immutable, content-addressed analysis plan for **either** a Tier 1 Curated Kernel **or** a Tier 2 Exploratory Code script/notebook (supporting inline code OR workspace-relative `script_path`, plus optional multi-step `graph_steps` or parameterized `sweep_spec` for LODO/multi-seed sweeps without 40 round trips), bound to exact input artifact digests, aim/branch basis, canonical 5-value `parameter_origins`, resource profile, and optional predecessor dependencies (`depends_on`). Never starts execution.
- **`inputSchema`:**
  ```json
  {
    "type": "object",
    "additionalProperties": false,
    "required": ["session_id", "branch_id", "epistemic_purpose", "objective", "plan_kind", "input_bindings"],
    "properties": {
      "session_id": { "type": "string", "pattern": "^[0-9a-f]{32}$" },
      "branch_id": { "type": "string", "minLength": 1, "maxLength": 128 },
      "branch_kind": { "enum": ["primary", "sensitivity", "competing_hypothesis", "method_benchmark", "exploratory_scratch"] },
      "aim_id": { "type": ["string", "null"] },
      "epistemic_purpose": { "enum": ["qc_diagnostic", "exploratory", "sensitivity_probe", "method_benchmark", "confirmatory"] },
      "objective": { "type": "string", "minLength": 1, "maxLength": 1600 },
      "plan_kind": { "enum": ["tier1_kernel", "tier2_exploratory_code"] },
      "kernel": { "type": ["object", "null"], "required": ["kernel_id", "version", "parameters"], "properties": { "kernel_id": { "type": "string" }, "version": { "type": "string" }, "parameters": { "type": "object" } } },
      "exploratory_code": {
        "type": ["object", "null"],
        "required": ["base_image_id", "language", "expected_outputs"],
        "properties": {
          "base_image_id": { "type": "string" },
          "language": { "enum": ["python", "r"] },
          "source_code": { "type": ["string", "null"], "maxLength": 524288 },
          "script_path": { "type": ["string", "null"], "maxLength": 1024 },
          "parameters": { "type": "object" },
          "expected_outputs": { "type": "array", "maxItems": 32 }
        },
        "oneOf": [{ "required": ["source_code"] }, { "required": ["script_path"] }]
      },
      "sweep_spec": { "type": ["object", "null"], "properties": { "kind": { "enum": ["lodo_donor_drop", "seed_grid", "parameter_grid"] }, "parameter_sets": { "type": "array", "minItems": 1, "maxItems": 64, "items": { "type": "object" } } } },
      "graph_steps": { "type": ["array", "null"], "maxItems": 16, "items": { "type": "object", "required": ["step_key", "plan_kind"] } },
      "input_bindings": { "type": "object", "additionalProperties": { "type": "object", "required": ["artifact_id", "sha256"], "properties": { "artifact_id": { "type": "string" }, "sha256": { "type": "string", "pattern": "^[0-9a-f]{64}$" } } } },
      "parameter_origins": { "type": "object", "additionalProperties": { "enum": ["user_prespecified", "operator_approved", "assistant_proposed", "fixture_default", "derived"] } },
      "depends_on": { "type": "array", "maxItems": 16, "items": { "type": "object", "required": ["plan_id", "plan_sha256"] } },
      "resource_override": { "type": ["object", "null"], "properties": { "cpus": { "type": "integer", "minimum": 1 }, "memory_mb": { "type": "integer", "minimum": 256 }, "timeout_sec": { "type": "integer", "minimum": 30, "maximum": 259200 }, "gpu": { "type": "boolean" } } }
    }
  }
  ```
  - When `script_path` is provided, the daemon resolves it strictly relative to the workspace with `O_NOFOLLOW`, rejects symlinks/outside paths, enforces `<= 512 KiB`, hashes its bytes, and snapshots an immutable copy into the plan record before computing `plan_sha256`.
- **`outputSchema`:** `{ "session_id": string, "plan_id": string, "plan_sha256": string, "status": "planned"|"local_studio_only", "execution_lane": string, "claim_tier_ceiling": "none"|"L1_execution_verified"|"L2_statistically_calibrated"|"L3_scientifically_supported", "approval_required": "none"|"codex_native"|"local_studio_or_cli", "runtime_pin": object, "static_preflight_warnings": [string], "studio_url": string | null, "launch_hint": string | null }`

#### 12. `submit_reviewed_run` *(replaces `execute_reviewed_plan`)*
- **Purpose:** Idempotently enqueue the exact frozen plan (`session_id`, `plan_id`, `plan_sha256`) on the daemon's Job Supervisor. Supports resuming from a verified checkpoint (`resume_from_run_id`). Returns immediately (`< 5 s`) with `run_id` and queue/running status.
- **`inputSchema`:** `{ "session_id": string, "plan_id": string, "plan_sha256": string (64-hex), "resume_from_run_id": string | null }`
- **`outputSchema`:** Same shape as `get_run_status` output (`run_id`, `status`, `pool`, `studio_url`, `launch_hint`).

#### 13. `cancel_run` *(NEW)*
- **Purpose:** Idempotently cancel a queued or running job, triggering graceful `SIGTERM` → `SIGKILL` process-tree teardown and daemon-ID verified OCI container removal (`recover_mounted_container()`).
- **`inputSchema`:** `{ "session_id": string, "run_id": string, "reason": string (1..500) }`
- **`outputSchema`:** `{ "session_id": string, "run_id": string, "status": "cancelled"|"already_terminal", "cleanup_state": "confirmed"|"pending"|"failed", "checkpoint_preserved": boolean }`

---

## 6. Data/Artifact Access, Visualization & Private-Data Handoff Protocol

1. **Zero Raw Matrix Egress:** Neither `inspect_inputs`, `get_run_receipt`, nor `query_artifact_table` ever returns raw cell-by-gene expression matrices. Agents inspect expression results through bounded aggregates, summary tables, and paginated `query_artifact_table` slices (`<= 200` rows, `<= 64 KiB` JSON).
2. **Plot-Data Sidecar Contract:** Every Tier 1 or Tier 2 figure (`png`/`svg`/`plotly_json`) MUST emit a companion structured `plot_sidecar` JSON/Parquet artifact containing the exact underlying plotted summary statistics, sample/donor counts, axis labels, and transformations. The agent and Critic inspect the sidecar via `query_artifact_table` rather than guessing from compressed thumbnails; visual inspection happens in Companion Workbench (or optional `ui://` preview on public data only) and promotes `verification_state` from `produced_unverified` to `visually_verified`.
3. **Private-Data & Clinical Handoff Protocol:**
   - Any input marked `private`, `clinical`, or `unclassified` (default), or matching `_clinical_name()` column/path patterns, or derived from a previously tainted SHA-256 (`has_private_input`), triggers `category: "private_data_local_studio_only"` on MCP read/receipt tools and redacts `register_investigation_input` / `list_investigation_state` to opaque `{artifact_id, sha256}` / `{session_id, revision, redacted: true}` handles only.
   - **Tier 2 Exploratory Code on Private Data:** Supported strictly offline via (a) local authoring/review/approval inside `--offline-only` Studio or (b) schema-blind synthetic-tested templates frozen via MCP and bound/reviewed/executed strictly inside `--offline-only` Studio with **zero MCP output readback** ([04-system-architecture-and-governance.md §4.2](04-system-architecture-and-governance.md#42-tier-2-governed-exploratory-code-sandbox)).
   - The daemon ensures an `--offline-only` loopback Workbench listener is reachable (authenticated via the `0o600` `daemon.token`) and returns `{studio_url, launch_hint}` without leaking any filenames/columns/values.
   - **No Surprise Browser Popups:** Unlike `plugin_mcp.py:5636` (`auto_studio=True` opening the desktop browser on the first tool call), **browser auto-open defaults to OFF (`--no-auto-browser`)** so headless SSH, CI, and CLI-first sessions are never interrupted; users open the printed/card `studio_url` explicitly or opt in via `--auto-open-browser`.

---

## 7. Long-Running Jobs, Concurrency Pools, Checkpointing & Crash Recovery

| Concern | Current Baseline (`18161fd`) | Target Daemon Supervisor (`M1`) |
|---|---|---|
| **Process Lifespan & IPC** | Worker spawned directly by STDIO MCP server; STDIO death risks orphan tracking gaps. | Workspace daemon (`devbench daemon`, `0o700` `.devbench-daemon/` with `flock` `daemon.lock`, `daemon.json`, `0o600` `daemon.token`, and macOS 104-byte-safe short hashed `0o600` `AF_UNIX` socket under `$XDG_RUNTIME_DIR`/`$TMPDIR/devbench-ipc-<uid>/`) owns all workers; STDIO shim reconnects seamlessly across Codex restarts/compactions. |
| **Concurrency** | Hard global 1-job mutex (`local_job_already_active`, `plugin_jobs.py:349`). | Configurable resource pools (`cpu_slots`, `gpu_slots`, `memory_budget_mb`, `max_concurrent_runs` defaulting to `min(4, cores//2)` CPU slots + `1` GPU slot) with fair FIFO/priority queuing so independent aims and sensitivity branches run in parallel. |
| **Timeouts & Deadlines** | Hard 1-hour `SIGALRM` (`MAX_SECONDS = 3600`, `plugin_jobs.py:31`). | Per-profile deadlines: e.g., `300 s` (QC/audit), `14,400 s` / 4 h (integration/scVI/DE), up to `259,200 s` / 72 h (multi-sample FASTQ/STARsolo/Salmon alignment or large sweeps). |
| **Progress & Heartbeats** | Coarse `stage_json` + optional Nextflow TSV. | Workers emit atomic `progress.json` heartbeats (every 10–30 s: epoch, step, RSS, ETA) + Nextflow trace parsing; daemon flags stale heartbeats (`> 3×` interval) without premature kills. |
| **Checkpointing & Resume** | None (`reconcile()` marks dead workers `interrupted` permanently). | Long-running Tier 1/2 jobs write content-addressed intermediate checkpoints (`checkpoint_manifest.json`, e.g., per-sample Salmon quants, scVI epoch weights, Nextflow `-resume` workdir hash) so `submit_reviewed_run(..., resume_from_run_id=...)` resumes cleanly from the last verified checkpoint. |
| **Cancellation & Orphan Cleanup** | No `cancel_run` tool. | `cancel_run` marks cancellation in `ScientificAuthority`, sends `SIGTERM` (15 s grace) → `SIGKILL` to process group, runs `recover_mounted_container()` verified against Docker daemon ID, and records `cleanup_state = "confirmed"`. |
| **Client Notification Strategy** | Polling `get_run_status` only. | Universal `< 100 ms` `get_run_status` polling + Workbench SSE stream + optional MCP `notifications/progress` when `progressToken` is supplied + forward-compatible `io.modelcontextprotocol/tasks` adapter in `mcp` 2.x. |

---

## 8. Four-Layer Integration Verification Strategy & v0.1.0 Retirement

Protocol and integration verification (`L1`) is reported strictly separately from statistical calibration (`L2`) and scientific validity (`L3`; see [06-milestones-and-evaluation.md](06-milestones-and-evaluation.md)).

### 8.1 Four Verification Layers

1. **Layer 1 — MCP Protocol Conformance (`tests/test_mcp_conformance.py`, CI-blocking):**
   - Drives `devbench mcp` over real STDIO (and optional loopback Streamable HTTP) using `mcp.ClientSession`: verifies all 13 tools' `inputSchema` (`additionalProperties: false`, zero type coercion), `outputSchema` validation on every success and error reply, accurate `ToolAnnotations` (including `freeze_analysis_plan` `readOnlyHint=False`), `<= 64 KiB` payload bounds, idempotent `submit_reviewed_run` & `cancel_run`, and graceful degradation when `io.modelcontextprotocol/ui` and `elicitation` are absent.
   - Adds a forward-compatibility matrix test lane when upgrading to `mcp` 2.x (`2026-07-28` + `server/discover` + `io.modelcontextprotocol/tasks`).
2. **Layer 2 — Live Codex CLI & Desktop App Smoke Suite (Isolated `CODEX_HOME`):**
   - Runs in a dedicated temp `CODEX_HOME` against the real `codex` binary (without mutating user `~/.codex`): validates portable root `plugin.json` schema, `configure.py` generation, `codex plugin marketplace add`, `codex plugin add` (when supported by installed Codex build), `codex mcp list`, non-interactive `codex exec` tool discovery & read-only `inspect_runtime_capabilities` / `inspect_inputs`, Skill (`$rna-research-core`) discovery, `AGENTS.md` byte-size assertion (`< 16 KiB` warning / `< 32 KiB` hard fail), and `codex sandbox --permission-profile` boundary probes (`probe_boundary()`).
   - **Three-Tier CI Integration (`ADR-25` / `UD-11`):** Upgrade [`.github/workflows/ci.yml`](../../../.github/workflows/ci.yml#L61-L69) into: **Tier 1 (PR-blocking fast `L1`, `ubuntu-latest` + `macos-latest`, `< 15 min`)** provisioning Python via `uv sync --locked --extra dev --python-preference only-managed` (`ADR-21`, ending the SQLite skip) to run `ruff`, `mypy`, `AGENTS.md` size gate, plugin/mirror parity & legacy-tool lint, full pure-Python + daemon (`test_research_daemon.py`), Layer 1 STDIO MCP, Layer 3 non-Docker refusal/crash, Layer 4 leakage, and full `integrations/codex/tests/test_plugin.py`; **Tier 2 (Nightly / `workflow_dispatch`, `ubuntu-latest` `amd64` + `ubuntu-24.04-arm` `arm64`, `~30–45 min`)** running native Docker + rootless `PodmanBackend` mounted OCI tests, live `arm64` vs `amd64` `ADR-16` parity checks, and isolated `CODEX_HOME` smoke (recording live Codex Desktop App picker checks as explicit release-evidence receipts rather than conflating them with CLI tests); and **Tier 3 (Release-gated / manual)** running `L2`/`L3` calibration and `M5` Scorecards 2–6.
3. **Layer 3 — Refusal, Concurrency, Crash & Recovery Suite:**
   - Tests out-of-root/symlink/hardlink path refusal, confounded design & pseudoreplication refusal, `plan_sha256` tampering refusal, concurrent multi-job pool execution (verifying 2+ jobs run simultaneously while honoring slot caps), mid-run `SIGKILL` of both the STDIO shim (verifying daemon job survival & reconnection) and the worker (verifying checkpoint recovery and zero orphaned Docker containers via `recover_mounted_container()`).
4. **Layer 4 — Privacy, Custody & Leakage Suite:**
   - Plants unique sentinel strings inside `private`, `clinical`, and `PartitionVault`-embargoed expression/metadata/column/filename fixtures; runs all 13 MCP tools, invalid-argument validation errors, `ui://` resource reads, and loopback HTTP unauthenticated/wrong-Origin probes; asserts **zero** sentinel occurrences across MCP stdout, MCP stderr, `validation_errors`, `ui://` HTML, and unauthenticated HTTP replies.

### 8.2 Retirement Plan for Stale v0.1.0 Codex Artifacts (`M0`)

Per [01-baseline-audit-and-gaps.md](01-baseline-audit-and-gaps.md), the following drifted v0.1.0 files under [`integrations/codex/`](../../../integrations/codex/) contradicted the live server at `18161fd` and were retired/rewritten in `M0` (`M0-1` `970069c`):
- **Remove** `integrations/codex/devharness-0.1.0.zip` *(retired in `M0-1`)* (stale pre-built archive targeting old 10-tool `devharness_server`).
- **Remove** stale v0.1.0 snapshot trees under `integrations/codex/validation/`, `integrations/codex/package-evidence.json`, and `integrations/codex/package-evidence.sha256` *(retired in `M0-1`)* (from `2026-09-21`, Python 3.12.1, 10 tools).
- **Replace** `integrations/codex/tests/verify_rna_runtime.py` *(retired in `M0-1`)* (broken 3-arg `configure()` call & `len(tools) == 10` assertion) with the Layer 1–4 conformance & smoke harness.
- **Rewrite** [`integrations/codex/README.md`](../../../integrations/codex/README.md) and fix `mcp-payload-contracts.md` drift (`L301` pathway ranking formula, `L519` optional `cell_type`) as part of decomposing into modular `.agents/skills/`.

---

## 9. Acceptance Criteria (Codex & MCP Integration)

1. **Portable Plugin & Clean Packaging (`M0`/`M1`):** Stale v0.1.0 zip/README/validation/verify script are retired; `integrations/codex/devharness/plugin.json` validates against `https://agent-plugins.org/schemas/1.0.0/plugin.schema.json`; `AGENTS.md` stays `< 8 KiB` target (`< 16 KiB` CI warning, `< 32 KiB` hard cap); modular `.agents/skills/` load cleanly via progressive disclosure.
2. **13-Tool Protocol Conformance (`M1`):** All 13 MCP tools pass real STDIO `ClientSession` conformance with strict `inputSchema`, strict `outputSchema`, honest `ToolAnnotations`, `< 5 s` response latency, `<= 64 KiB` response caps, and deterministic error taxonomy.
3. **Cross-Client Graceful Degradation (`M1`):** Every workflow (`E0`–`E6`) completes end-to-end on a minimal MCP client advertising **no** `io.modelcontextprotocol/ui`, **no** `elicitation`, **no** `tasks`, and **no** `prompts` capability — with zero broken UX or silent hangs.
4. **Daemon Survival & Cancellation (`M1`):** Killing the MCP STDIO shim mid-job leaves the daemon worker running; a fresh client reconnects via `list_investigation_state` + `get_run_status`; `cancel_run` terminates a running container and confirms zero orphan containers.
5. **Verified Isolated Codex Smoke (`M1`):** Automated smoke test in isolated `CODEX_HOME` passes `codex plugin marketplace add`, `codex mcp list`, and tool discovery without mutating host `~/.codex` or using retired `approval_policy = "untrusted"`.
6. **Zero-Leakage Privacy Gate (`M1`):** Layer 4 sentinel leakage test suite passes 100% across all 13 tools, validation errors, stderr, and loopback HTTP endpoints, with `--no-auto-browser` enforced by default.

---

## 10. Unresolved Questions & Open Decisions

| # | Open Decision | Options & Tradeoff | Blocking Milestone |
|---|---|---|---|
| **Q5.1** | **RATIFIED (`ADR-23` / `UD-08`) — Dual-Manifest Compatibility Window** | **Settled (`ADR-23`):** Maintain one canonical template `integrations/codex/devharness/plugin.json` (`agent-plugins.org/schemas/1.0.0` + `extensions["com.openai"]`); `configure.py` emits root `plugin.json` and deterministically generates `.codex-plugin/plugin.json` mirror by default (`--no-legacy-codex-manifest` opt-out), with a field-parity unit test (`name`, `version`, `skills`, `mcpServers`) and a 2-consecutive-release Codex build sunset trigger. | `M1` *(Ratified)* |
| **Q5.2** | **RATIFIED & IMPLEMENTED (`ADR-22` / `UD-09`, `M1-1` `4b90e76` / `292d16f`) — Default Transport** | **Settled & Implemented (`ADR-22`):** `devbench mcp` STDIO shim is the sole default transport; `--enable-streamable-http` is opt-in, protected by per-launch 256-bit `0o600` bearer token and loopback/Origin validation. | `M1` *(Implemented)* |
| **Q5.3** | **`mcp` Python SDK 2.x (`2026-07-28`) Cutover Timing** | Pin exact client-readiness exit criteria (Codex CLI/App + Claude Code + Cursor GA support for `2026-07-28` `server/discover` and `io.modelcontextprotocol/tasks`) before upgrading `pyproject.toml` from `mcp>=1.29.1,<2` to `mcp>=2.0`. | `M3` / `M4` |
| **Q5.4** | **MCP URL-Mode Elicitation for Private-Data Studio Handoffs** | When a client advertises `elicitation.url`, should `freeze_analysis_plan` on private data issue an `elicitation/create` URL redirect to `studio_url`, or stay on the uniform `awaiting_review` / `private_data_local_studio_only` structured return across all clients for predictability? | `M2` / `M3` |
| **Q5.5** | **RATIFIED (`ADR-24` / `UD-10`) — Skill Distribution Across Non-Codex Clients** | **Settled (`ADR-24`):** Commit 8 modular skills once in `.agents/skills/<name>/SKILL.md`; add symlink-free `configure.py --mirror-clients <claude, cursor, all>` (off by default) emitting copies + `.devharness-mirror-manifest.json` (`--check-mirrors` in CI) — `claude` copies to `.claude/skills/`, `cursor` verifies `.agents/skills/` (optional legacy `.cursor/skills/`, no `.cursor/rules/`), never committed to git. | `M1` *(Ratified)* |
