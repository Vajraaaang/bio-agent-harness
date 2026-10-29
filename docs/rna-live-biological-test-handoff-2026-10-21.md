# Handoff: RNA workbench and first live biological test

Prepared October 21, 2026, America/Los_Angeles. Companion: [what happens next](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/docs/rna-live-biological-test-next-steps-2026-10-21.md).

## Project end goal

Build a research-grade scientific agent and workbench that helps researchers analyze transcriptomic data with the judgment expected of an experienced bioinformatician: understand the data and biological units, frame the question, assess appropriate methods, execute reproducibly, diagnose failures, compare findings with publication evidence, and communicate uncertainty. Every claim should be inspectable; consequential scientific acceptance remains subject to human oversight. Evaluate whether the harness improves scientific reliability through reproducible comparisons rather than assuming that successful execution proves benefit.

The product should support different RNA-seq questions through a shared evidence framework and question-specific methods, endpoints and comparison rules. The first bulk-data reproduction is an integration milestone, not the product boundary. Remote data, HPC, notebooks and broader assay support are later milestones. See the [research end-goal plan](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/docs/superpowers/plans/2026-10-01-expert-scientific-judgment.md).

The separate [long-term clinical ambition](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/docs/clinical-production-goal.md) is a production-grade, audit-ready transcriptomics platform for regulatory biomarker validation. Clinical validity, qualified operations, authenticated human signatures and clinical release remain unestablished. Completing that entire roadmap is not a prerequisite for a properly scoped public-data research test, and that test cannot establish clinical readiness.

## Immediate objective and authorization

The user wants a live biological test soon. The recommended next milestone is **one live-model-assisted reproduction of one explicitly identified published analysis using the authors' original processed data and code**, through the existing governed workflow. This is a computational public-data test, not a wet-lab experiment, entire-paper reproduction, best-method competition or external biological validation.

The explicit processed-analysis scope is recorded in [user decisions](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-public-study-readiness/2026-10-19-v1/user-decisions-2026-10-19.json). No specific publication/input was accepted in that record. The latest request prioritizes getting to the test; these documents do not supply missing qualification evidence or purpose-specific execution grants. No live generation or real study ran on the selected signed-in route at the latest sealed checkpoint.

## Latest verified checkpoint

The measurements below are from October 20, not new October 21 runs. During this documentation task, all 30 direct references in the closure index were rehashed without a mismatch. That check does not refresh client authentication, runtime qualification or hosted performance.

Start with the [engineering report](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-context-readback/2026-10-19-v1/implementation-and-status-v1.md) and [sealed closure index](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-context-readback/2026-10-19-v1/engineering-closure-evidence-index-v1.json), SHA-256 `87b7834c1d2282096c98842b59af1c24192d82a26b1446a17d2f2644d69f4d57`.

| Area | Verified evidence | Practical limit |
| --- | --- | --- |
| Installed software | 2,486 passing cases across the exact 66 selected modules; zero failures/errors/skips; controlled synthetic checks and independent SPEC/QUALITY acceptance. | Selected software evidence, not a clean broad-suite, native-route or scientific-validation claim. Historical broad nonpasses remain preserved. |
| Installation | Ordinary wheel at `/Users/vajraang/Documents/ChatGPT/bio-agent-harness/r/s6`; frozen source at `/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-context-readback/2026-10-19-v1/installed-verification/source/signedin-8`. | Source, wheel, dependencies, RECORD and origin bindings must remain exact for reuse. |
| Official Codex client | CLI 0.147.0; two fresh metadata inspections agreed on ChatGPT sign-in. No raw auth output or auth-stream hash retained. | Mode verification does not establish model availability, person identity or packet confinement. Refresh through the official client when needed. |
| Signed-in integration | Explicit ChatGPT route, durable job accounting, protected planning stages, captures, separate grants and provenance; no selected-route Platform-key fallback. | Native admission deliberately remains blocked. All five boundary flags are false; no accepted production verifier/launcher is registered. |
| LangSmith | One original exporter child, one credential resolution, four complete HTTP 200 GETs, 11,022 response-body bytes, 3/3 exact context matches. | Current readback of retained **S4 metadata through the S6 reader**. No new-generation trace, original-SLA recovery or full hosted-performance qualification. |
| Research signer | New Ed25519 material created through macOS Keychain; possession challenge independently checked. | Agent-provisioned, user-authorized local research material; no active issuer enrollment or generation grant, and no institutional/human-signature claim. Do not repeat bootstrap. |
| Fresh probe preparation | Four bounded official version/status commands; synthetic packet remained HOLD; no generation, hosted calls, numerical runs or grants. | No live model probe was executed. Both purpose approvals and numerical allocation remain null. |

The [fresh HOLD packet](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-context-readback/2026-10-19-v1/signedin-probe-preparation/packet-v1/probe-packet.HOLD.json) has SHA-256 `43db697af2a5fc21deb8d4a66c6575063ca489a6670b44a1ec5ed4dfe12e213e`, generation binding `234d41ade3a6d60cd793b7dd8b39256bba9345aed59163a2def1890f099122bd`. Its `allow_model_execution` is false. Rebuild and prepare a new matching subject after any boundary/source change; never activate it by flipping booleans.

The [public signer reference](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-context-readback/2026-10-19-v1/research-signer-bootstrap/issuer-reference.HOLD.json) identifies `local-research-a84f56d983ea4bdca67595dba896371c`, SPKI SHA-256 `64ae78857b77b36abda3fd10f7b72e567142aaa00413fb742f9138e7084039f0`. Private material stays with Keychain; this public reference is not a grant. The bootstrap signs only a fixed possession challenge, and the public approval helper only prepares/verifies grant material. A reviewed, purpose-bound grant-signing bridge is still needed; neither existing helper can already issue a grant with this key.

## Architecture to reuse

| Component | Responsibility and connection |
| --- | --- |
| Publication grounding and assessment | Freeze publication identity, source bytes/excerpts, data lineage, exposure and question-specific comparison intent. Byte correspondence is distinct from semantic support. |
| ScientificMethodPlanner / trial coordinator | Feed the protected `method_scientific_planning`, `method_capability_resolution` and `method_trial_design` stages through the signed-in backend. Preserve scientific assessment before implementation availability. These are logical generation stages, not proof of three independently qualified autonomous agents. |
| Gateway, campaign ledger and authority | Check exact source/runtime/client/subject bindings, reserve launch intents, enforce ledger concurrency, and consume purpose-specific authority. Model output cannot grant itself numerical permission. |
| MethodService and registered MCP / trusted ToolDispatcher | Run admitted tools or governed method steps, retain immutable inputs/outputs and execution evidence. Do not create a second executor or bypass them with standalone analysis. |
| Comparison, figures and scientific review | Independently assess actual outputs against frozen endpoints; retain complete statistical families, plot data and verified exports. Advisory model opinions cannot replace numerical checks or scientific acceptance. |
| Observer, outbox and exporter | Project permitted metadata to LangSmith in the original protected child; correlate trace/artifact IDs and verify actual delivery/readback. Retry telemetry separately without rerunning numerical work. |

Relevant interfaces: [backend](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/codex_signedin.py), [public approval setup](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/rna_approval_setup.py), [scientific trial](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/rna_scientific_trial.py), [trial authority](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/rna_trial_authority.py), [readback contract](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/study/rna_hosted_context_readback_contract.py), [readback engine](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/study/rna_hosted_context_readback.py).

## Limits and retained decisions

Tested coordinator defaults for a future admitted campaign are three parent generation jobs, shared-ledger concurrency one, 300 seconds campaign time, 75 seconds/job, 32 KiB stdin prompt/schema and 128 KiB combined retained stdout/stderr/events per job; no coordinator retries, resumes or children. They do not cap hidden model context, provider-internal requests/retries, tokens, subscription credits, spend, remote cancellation, or hard memory/disk use. Reported tokens are optional post-run evidence. Unsupported preventive limits must remain blocked rather than advertised.

The former three-API-request / 828,288-token / $12 proposal does not transfer to subscription-authenticated Codex. The conditional numerical ceiling of 30 minutes, 4 GiB memory and 2 GiB scratch/output remains inactive; exact run counts, setup/wall-time accounting and enforceable executor controls still need a bound execution allocation. Generation and numerical grants remain separate. Standing user approval of recommended decisions does not manufacture either signature or qualification evidence; reuse valid matching consent instead of repeatedly asking the same questions.

Current assistant candidate: Love et al., *RNA-Seq workflow: gene-level exploratory analysis and differential expression*, F1000Research 2015, 4:1070, version 1, DOI `10.12688/f1000research.7035.1`; one prepared-`airway` downstream analysis. Required historical input is `airway` **0.102.0**, documented as 64,102 genes × eight libraries from four paired donor-derived cell lines. These are publication declarations until acquired metadata is checked. A current 63,677-row object is not an identical substitute. Exact analysis chunks, runtime compatibility, local matrix identity and study admission remain unfinished.

**Superseding clarification:** the historical [acquisition blocker](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-public-study-readiness/2026-10-19-v1/original-input-acquisition-blocker.json) predates the user's later answer **“Permit this package-only exception.”** That allows the specific official historical package solely to extract original processed counts, with no BAM use. It is not general permission for BAM/FASTQ acquisition, raw processing, package-script execution or a study run. Preserve the old record unchanged. The older Himes partial-reanalysis proposal was not selected.

## Handover rules

Read [AGENTS.md](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/AGENTS.md) first. Preserve dirty workspace changes, original failures, sealed evidence and expired/consumed approvals. Do not inspect host credentials, grader files or private goldens. Let the official Codex client manage its sign-in; never extract OAuth caches or forward sign-in credentials to the Responses API.

The native sandbox canary's exit 134 remains unexplained. Stock read-only mode, an upgrade, static booleans, a successful fixture, a key or historical metadata readback cannot establish a complete native boundary. The unreleased OS-only diagnostic also retains CFPreferences access and is not a qualification proof. See the [official-surface assessment](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-signedin-codex-route/2026-10-19-v1/client-evidence/metadata-inventory-assessment-2026-10-20.json).

Proceed with the companion next-step gates. This handoff changes no code, credentials, approvals or runtime state and launches no study.
