# RNA Workbench: integrated investigation thread and scientific canvas

The workbench now starts with a discovered dataset and a question. It computes file identity and inspects metadata in a bounded worker, asks for unresolved experimental declarations in the conversation, presents the actual plan, and requires separate review and execution actions. Verified figures, saved notebook code, tables and provenance remain beside the thread. Follow-ups retain attributed data bindings while receiving fresh analysis and approval scopes.

The later Slate/Indigo design brief supersedes the earlier monochrome direction. The application retains its native HTML/JavaScript/Python stack, local fonts, nonce CSP and loopback authentication. No frontend framework, CDN, external chart runtime or remote deployment was introduced.

## What changed

- **Data context:** authenticated `/api/inputs` and `/api/inputs/inspect` discover allowed H5AD/JSON/CSV/TSV inputs, compute streaming hashes, and return observed/declaration/inferred/unknown profiles. Canonical descriptor-relative admission rejects symlinks, special files, escapes and changed identities. Header inspection does not load expression matrices or establish experimental validity.
- **Durable investigations:** `/api/investigations` retains revision-checked questions, clarification, parent context, actual lifecycle events, approvals, outputs and synthesis. Existing job and scientific routes remain intact. A new question cannot reuse another investigation's approved job.
- **Scientific chain:** `advance_to_gate` drives the real registered workflow and ledger to mandatory review interrupts. It preserves current startup/campaign permissions, exact session/version bindings, per-node source checks, idempotency and uncertain outcomes. A verified AnnData adapter joins the existing RNA graph without fabricating bulk data or inferring biological independence from headers.
- **Inline control:** the existing native review form and run/cancel/recovery controls remain real DOM nodes and retain their IDs. Scientific trial review and execution have separate explicit cards. New native jobs reset decision/rationale/confirmations while same-job edits survive. Scientific children inherit data context, not model/execution grants or approvals.
- **Canvas and conversation:** compact real step disclosures replace repeated status cards; structured evidence replaces primary JSON dumps; full receipts remain available behind styled disclosures. The header uses run/data context, submitted unchanged drafts clear and persist, and planner attribution uses retained source metadata rather than current configuration guesses.
- **Figures:** one coherent figure header contains selection/version/view, panel selection, accessible SVG plot actions and export controls. QC/UMAP retain exact saved identities and values, real device-pixel-ratio backing, clean 1/2/5 ticks, dashed grids, Okabe-Ito display colors and white point outlines. Original colors/data remain in provenance.
- **Published outputs:** scientific result references and artifacts are exact-session/project bound. Explicit previews verify size/SHA-256 and safely display bounded raster, CSV/TSV/JSON tables or notebook code. Matrices and active documents are not automatically fetched or executed.
- **Native motion:** reduced-motion-aware View Transitions cover navigation/version changes; supported disclosures animate; cards enter only on a changed run; zoom/reset interpolate the viewport for 160 ms without changing saved coordinates.

The detailed interfaces are linked from [the current architecture index](README.md).

## Verification

The final isolated acceptance run passed **392 tests**, with zero failures, errors or skips, in **284.80 seconds**. It includes all ten files named by the latest architecture brief, the original six-file refinement set, new input/investigation/artifact/AnnData/advancement tests, and broader scientific workspace, method planner and entry-point regressions. JUnit is retained as `reports/workbench-refinement/2026-10-28/isolated-final-verification.xml`.

The one AnnData warning is intentional: a duplicate-ID fixture demonstrates that header inspection does not claim ID uniqueness. JavaScript syntax checks passed for the composed nonce-protected page and the individual modules; stylesheet parsing passed.

Independent review reproduced and verified fixes for cross-session scientific forwarding and a scientific startup data-scope mismatch. Ten post-fix scope spies blocked mismatched/disabled/private requests before forwarding while allowing the explicitly authorized matching scopes. These are software control checks, not live-model or scientific qualification.

## Actual browser evidence

1. A discovered 80-cell/60-gene synthetic H5AD supplied its hash and metadata automatically. Clarification offered the actual `batch` column. No terminal checksum was needed.
2. Native review occurred in the thread, with fresh unchecked confirmations. A separate run action completed the real existing numerical workflow as `completed_review_passed`, with verified QC/UMAP and notebook artifacts and zero model sessions.
3. A real box gesture selected 40 exact saved IDs. The composer attached a removable `Selected cells (40)` chip. A follow-up explicitly chose saved-evidence review; it reported 40 selected observations without executing a new analysis or pretending the fixed pipeline fit a subset. The canvas retained an explicitly labeled source run.
4. The native SVG download parsed with 80 point circles and clean labels `10, 12, 14` and `-10, 0, 10, 20`. Actual canvas dimensions were approximately twice CSS dimensions at DPR 2. The code tab displayed a verified saved notebook beginning with execution count 1.
5. A separate, clearly labeled recorded scientific software fixture completed nine actual planning nodes and stopped at the real review interrupt. Browser approval declared the exact trial; a separate data checkbox/run action authorized execution. The explicitly granted synthesis phase reached `abstained` because the comparative evidence floor was not met. Its published JSON artifact passed the browser size/hash check. No scientific plot was produced by that fixture; native QC/UMAP plots came from the numerical scRNA run.
6. A scientific follow-up retained the exact DatasetVersion and RNA binding, created a different AnalysisVersion and session, and waited for new authorization with zero child operations. Historical grant fields were not inherited.
7. Light/dark views, native notebook display, source-bound history, inline gates, dataset inspection and published output preview were inspected. DOM checks found no horizontal page overflow at 1440, 1280, 1024 and 390 CSS pixels. The final short-screen CSS keeps the 720px rail at 720px content height with its brand visible; mobile reading order is navigation → conversation → canvas. Temporary viewport overrides were cleared.

Screenshots, SVG export, responsive measurements and browser record readbacks are retained in `reports/workbench-refinement/2026-10-28/`.

## Performance observations

The refined renderer displayed all 50,000 and 100,000 deterministic synthetic points without sampling. At 1280×720/DPR 2, observed mount/first-draw times were 62.0/90.7 ms, redraws including scheduling 42.1/40.0 ms, and exact polygon identity checks passed for 25,000/50,000 selected IDs. These single browser observations are not sustained FPS, heap profiling, biological workloads or evidence of a speedup over a static image. Raw observations remain in `performance/browser-observations.json`.

## Source isolation and preserved failures

Concurrent edits to unrelated `community_workflow.py` and `method_executor.py` changed the checkout-wide source identity during validation. Existing guards correctly refused mixed-source operations. Those edits were neither reverted nor modified by this task.

Acceptance and the open preview therefore use a frozen, allowlisted source snapshot. `delivery-source-v2` contains the captured current source plus the final short-viewport stylesheet patch; its source SHA-256 is `829e2a3f6f6073b4858276179d27097b41f17b23c7d1b9b486fa6828a3e18222`. The 27 workbench source/test files match their canonical repository counterparts. Guards remain enabled; no runtime identity was reset to bypass a failed check.

Source maps and before/after hashes are retained in `delivery-source-manifest-v2.json` and `snapshot-source-manifest-v2.json`. Earlier source-drift failures, the actual generic JSON-admission failure, interrupted verification, and a first isolated test run missing an empty fixture-parent directory remain preserved. Creating that empty staging directory corrected the test environment; no historical evidence was copied or altered.

## Limits

This is local, single-user software. Live providers require the configured selector, matching startup data scope and fresh campaign permission; no live-provider qualification was performed. Scientific input assumptions, missing design/contrast information, unavailable methods and unmet prerequisites still require clarification or abstention. A successful workflow does not establish an optimal method, biological accuracy, causal validity or clinical readiness.

Native interactive geometry remains QC/UMAP. Other genuinely published outputs have safe bounded previews. Selected-context requests are limited to 1,000 unique IDs/50 KB; larger exact sets can be exported without truncation. Text previews are capped at 256 KiB, raster previews at 8 MiB and browser artifact downloads at 64 MiB. Forty-row observation pagination and a 200-row generic preview bound DOM work. Performance above the measured workloads, long-session memory use, full assistive-technology compliance and production/multi-user operations remain unqualified.
