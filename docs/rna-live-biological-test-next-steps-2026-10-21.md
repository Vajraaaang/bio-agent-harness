# Next: qualify the route, then run one live public biological test

Prepared October 21, 2026, America/Los_Angeles. Read the [handoff](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/docs/rna-live-biological-test-handoff-2026-10-21.md) and [AGENTS.md](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/AGENTS.md) first.

**End goal:** a research-grade transcriptomics agent/workbench whose scientific judgment, reproducibility, uncertainty and publication-grounded claims can be independently assessed. The immediate milestone is one live-model-assisted, governed reproduction of one published processed-data analysis. One success cannot establish general expert performance, harness benefit, external biological validation or clinical readiness.

**Do next:** assign an execution-route owner to close Gate 1 and, in parallel, a publication/data owner to prepare Gate 4. Independent reviewers assess those lanes without editing the same files. One coordinator owns any admitted execution. Do not spend another cycle repeating already-passing metadata checks or expanding the platform roadmap while the concrete native blocker remains unresolved.

This is the work sequence, not a live launch command or grant. Existing approvals may be reused only when their exact subjects remain valid and unconsumed. Never reuse the old readback approvals or activate a HOLD packet by editing flags.

## Gate 1 — Make the signed-in native boundary real

**Owner:** runtime/gateway implementation. **Review:** independent security and integration reviewers. **First blocking priority.**

1. Inspect the current implementation and the exact retained native failure before changing it. Verify current official Codex documentation and the installed client's supported integration/authentication method. The last measured client was CLI 0.147.0; do not assume that remains current or that a newer version solves the boundary.
2. Preserve the exit-134 failure and assess its relevance to the chosen route. Investigate through a separately bounded, reviewed diagnostic or qualify a supported replacement boundary with actual positive and owned negative controls. The old root cause may remain unknown if a distinct current route independently qualifies; disclose that uncertainty. Diagnostic controls alone do not authorize live generation. Stop on ambiguous launch/usage state; do not blindly increase deadlines, disable safeguards, inspect host secrets or replay the old canary.
3. Establish complete control of the relevant filesystem and credential paths, command networking, native tools, rules/instructions, skills, MCP, apps/plugins and delegation. Preserve applicable managed controls. Prove the effective boundary for the actual official client; configuration declarations and a read-only setting are insufficient. Do not start unknown connectors merely to inventory them.
4. Implement/register the accepted production boundary verifier and launcher on the existing gateway/backend path. Bind the actual runtime, client components, configuration, evidence, policy, acceptance and expiry; recheck them at admission and dispatch. Preserve MethodService, ToolDispatcher, ledger and approval interfaces.
5. If the supported client cannot establish the required boundary, record the precise missing interface/evidence and the smallest supported alternative or explicit policy decision needed. Keep native admission blocked. Do not silently switch to Platform-key billing, use private endpoints, patch in cached OAuth tokens or substitute an out-of-ledger app/chat task.

**Done when:** reviewed actual positive/negative controls and installed integration prove the exact boundary, the production verifier/launcher accept only that bound evidence, and tampering, stale identity, unauthorized tools/paths, concurrency, failure and uncertain-usage cases fail closed. A static acceptance object or test double is not completion.

## Gate 2 — Verify the changed installation and prepare fresh authority

**Owner:** coordinator/release verification, with independent SPEC then QUALITY review.

- Freeze the changed source, build/install an ordinary wheel, and verify source/runtime/RECORD/module-origin custody through the existing verification machinery. Run appropriate changed-path tests and the required controlled synthetic verification suite. Reuse unaffected exact evidence; preserve historical failures. Do not run source-tree pytest or editable imports as installed-artifact proof.
- Treat the October 20 S6 installation and its 2,486 selected passes as a baseline, not qualification for changed code. Do not pool old/new results or relabel the old broad non-green gate.
- Prepare a new synthetic signed-in probe with current source, runtime, client/auth mode, boundary, model, roles, purpose and budget. Changes to these bindings invalidate old subjects. Inspect the implemented parser/interfaces before constructing a command; do not guess an execution CLI from an old plan.
- Reuse the existing Keychain research signer and public reference. Do not create another key or ask again for an “existing issuer.” Deliberately accept its public fingerprint and inject the public verification root through trusted configuration, under the existing authorization and local-research identity limits. Do not infer institutional/human-signature authority from agent-created material.
- Implement and independently review the missing purpose-bound grant-signing bridge using the existing Keychain key. The bootstrap accepts only its fixed possession challenge; the public approval helper prepares unsigned subjects and verifies detached signatures, but cannot sign. The bridge must accept only an exact deliberately approved grant subject, keep private material out of files/arguments/environment/logs, reject arbitrary messages and unapproved payloads, and reuse the existing signature/subject verifier. Prepare this implementation in parallel where useful; do not sign a live grant before its subject is qualified and approved.
- Produce the fresh, matching `workflow_generation` grant through that accepted path. Key creation/possession proof, blanket consent and a disabled public proposal are not that grant. Keep numerical authority absent.

**Budget record:** distinguish parent generation jobs from internal model requests and optional reported tokens. The tested default profile is at most three serial jobs, 300 seconds campaign time, 75 seconds/job, 32 KiB stdin and 128 KiB retained combined output/job, with no coordinator retries/resumes/children. Reconfirm its applicability to the fresh probe and record any necessary matching budget acceptance before dispatch. It offers no preventive cap on provider-internal requests/retries, tokens, subscription credits or spend, and no hard memory/disk guarantee. If any unsupported cap is required, reject admission rather than presenting post-run accounting as enforcement. The former API-request/token/$12 ceiling is inapplicable.

**Done when:** the exact changed installed artifact has accepted verification, and an unexpired, unconsumed generation grant matches the qualified probe subject and honest enforceable limits. No numerical or study authority is implied.

## Gate 3 — Run one bounded synthetic live-generation qualification

**Owner:** one execution coordinator. **Review:** independent execution/provenance and observability reviewers.

- Execute the one admitted synthetic campaign through the official signed-in client and existing protected generation path. Preserve the actual prompts/schemas under their privacy policy, structured captures, job identities, launch intents, elapsed times, failures and optional/unknown usage. Prevent API-key fallback and unallocated model stages.
- Verify the three protected planning-stage outputs and their exact bindings to the workflow and ledger. Model self-report, successful exit or a plausible response alone is insufficient.
- Deliver and independently read back **this new generation's** LangSmith tree. Correlate workflow, job, capture, artifact and trace IDs; verify actual provider content/hierarchy/status. Record redaction, observer failures and delivery uncertainty. Declare any performance criteria before measuring them.
- Stop at the admitted bounds or unexpected/uncertain state. No automatic retry/resume. Retry telemetry only under a separate matching action; never rerun numerical work just to recreate traces.

**Done when:** actual native generation, authorization/accounting boundaries, structured capture and same-workflow LangSmith delivery/readback have independent accepted evidence. The earlier 3/3 S4-through-S6 metadata readback cannot satisfy this gate. Synthetic success qualifies only its tested scope; it is not the real biological test.

## Gate 4 — Prepare the single real biological case in parallel

**Owner:** publication/data assessment. **Review:** independent source, statistical-design and scientific reviewers. No model generation or numerical execution is needed to settle source/identity questions.

Current assistant candidate: Love et al., *RNA-Seq workflow: gene-level exploratory analysis and differential expression*, F1000Research 2015, 4:1070, version 1, DOI `10.12688/f1000research.7035.1`. The user selected **exact reproduction of one published processed-data analysis**; this particular candidate still needs its explicit scoped acceptance. Choose and name the exact section/chunks and endpoints rather than promising the whole paper.

Follow the scientific order:

1. **What data?** Verify the original `airway` 0.102.0 processed object, documented as 64,102 genes × eight libraries from four paired donor-derived cell lines. Inspect actual measurements, integer/count semantics, sample/feature identifiers, pairing, contrast direction, design rank, annotation and processing lineage. Eight libraries are not eight independent donors. Separate publication declarations, observed data, inferences and unknowns; assess batch/confounding and missing information before fitting.
2. **What question?** Freeze the exact published processed-data analysis and the reproduction claim. Do not describe the prior unselected Himes proposal, a modern reanalysis, external validation or best-method selection as the requested task. Record why each constrained method/parameter comes from the authors' procedure versus an explicit user requirement or an assistant proposal.
3. **Which methods, and why?** Assess the original method's assumptions and suitability, then verify the original code and versioned runtime can execute faithfully. Do not substitute a convenient existing Python method or modern data/package version and call it exact reproduction. Use the existing governed method/OCI interface; if a necessary adapter is missing, implement and qualify only that adapter. If exact reproduction is infeasible, retain the reason and obtain an explicit scope/candidate decision rather than silently downgrading the claim.
4. **What will be compared?** Freeze endpoint identities, gene/statistical universes, contrast/units, preprocessing/filtering, adjustment/shrinkage, missingness/failure rules, justified numerical tolerances and required figures before results. Retain full eligible tested families; do not recompute BH only on matched/significant genes or treat a selected published list as exhaustive truth. Record reference exposure and the independent review's limits.
5. **What conclusions could this support?** Prespecify the difference between execution success, numerical reproduction, publication agreement and biological interpretation. Agreement with the same original data/code is not external validation or proof that the method is optimal.

### Narrow acquisition exception already authorized

The user explicitly permitted the specific official `airway_0.102.0.tar.gz` package solely to extract original processed counts, with no BAM use. The historical [acquisition assessment](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-public-study-readiness/2026-10-19-v1/original-input-acquisition-blocker.json) contains the original URL, code commit and reported metadata, but its “no exception” statement predates that answer. Keep it unchanged and use the superseding clarification in the handoff.

Revalidate the exact public asset/version and the permitted acquisition boundary. Retain bounded archive/member provenance and cryptographic hashes; extract only the approved processed object and necessary metadata, with path/type/size checks. Do not extract/analyze BAM examples, acquire FASTQ, run package scripts indiscriminately or substitute today's 63,677-row matrix. Keep inputs read-only and expression matrices loaded into pandas below 100 MB. Acquisition permission does not authorize model or numerical execution.

**Done when:** one publication/analysis, original processed input, original code and reproducible runtime have verified identities; the actual paired design is defensible; the route/adapter/renderers support that public-data scope; and an immutable `AssessmentIntent` freezes comparison rules and exposure before workflow generation.

Keep the authority layers distinct: the signed-in branch of [trial authority](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/rna_trial_authority.py) recognizes synthetic **or public** classification, but native admission is still blocked. The [public approval setup helper](/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/rna_approval_setup.py) intentionally issues unsigned subjects/verifies signatures for **synthetic generation only** and rejects public requests. A reviewed public-study grant-issuance path and qualification of the actual public scientific execution adapter are separate missing bridges. Qualify the exact intended scope; do not widen the helper indiscriminately, reuse a synthetic-only proof or relabel public data as synthetic.

## Gate 5 — Approve, generate and execute the real case once

**Owner:** one coordinator; independent reviewers remain advisory and separately scoped.

1. Require Gates 1–4 for the relevant scope. Finalize a separate real-study allocation: all attributable generation stages/jobs, actual numerical tool/executor run count, setup and analysis wall time, memory, scratch/output accounting, concurrency and failure behavior. One “workflow” is not a maximum run count. Verify the chosen executor actually enforces claimed controls.
2. Preserve the conditional 30-minute / 4-GiB / 2-GiB numerical ceiling as inactive until an exact enforceable allocation and matching execution grant exist. Do not transfer the synthetic allowance or obsolete API-token-dollar budget. Use already-valid matching consent; seek only genuinely missing decisions as one concrete reviewable record.
3. Obtain/verify the separate study **generation** grant against the frozen intent and exact qualified source/runtime/client. Generate through the existing planner/backend/ledger. Finalize the immutable `AssessmentPlan` afterward with workflow and derived-input hashes, without changing the frozen comparison rules.
4. Review that actual workflow and obtain/verify the distinct **numerical-execution** grant. Dispatch the one admitted study through MethodService and registered MCP tools or trusted ToolDispatcher. Do not use an alternative executor, mutable input or guessed CLI to evade a gate.
5. Preserve actual outputs, failures, deadlines and operator interventions. Stop on an admission or identity failure. No automatic rerun, substituted method, fabricated missing result or retroactive tolerance change.

## Gate 6 — Inspect and report the actual biology-facing outcome

- Independently verify numerical outputs against the original analysis using the frozen endpoints/tolerances and full statistical families. Retain applicable effects, p-values, BH q-values, uncertainty, failed estimates and exclusions; missing estimates remain missing, and q-values must not be smaller than their p-values.
- Produce the protocol's actual diagnostic/result figures, their underlying plot data and verified PNG/PDF exports where required. Inspect the plots; record absent or failed figures explicitly. No generic success screenshot can replace scientific outputs.
- Report methodological adequacy, reproduction/publication agreement, discrepancies and biological interpretation separately. An independent model-agent review is advisory, not an expert human panel or clinical validation. Report exposure and unresolved uncertainties.
- Correlate run/artifact IDs with the real study's LangSmith traces and verify delivery/readback. Preserve the scientific run if telemetry fails; repair/retry only telemetry under appropriate authority.
- Seal inputs, original code/runtime, approvals, workflow, numerical results, figures, comparisons, reviews and trace evidence in a post-run bundle. Actual output hashes belong here, not in a backfilled pre-run plan.

**Milestone completion:** deliver one actual governed public-data run with independently checked outputs, figures, provenance, a scoped reproduction decision and honest limitations. A mismatch or failure is a retained outcome, not permission to claim a pass. Report a narrow reproduction success only if every required comparison supports it. Broader scientific capability, harness benefit and clinical release remain separate future evaluations.

## Keep the first test focused

Do not require completion of HPC/notebook integrations, every RNA-seq profile, a large multi-study benchmark, a universal score or the clinical-production roadmap before this research pilot. Do require the actual route, public-data method scope, enforceable allocations, distinct grants, evidence capture and independent checks above. Finish the smallest defensible live biological test before expanding scope.
