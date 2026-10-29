# Current RNA Workbench architecture

The current implementation follows the interactive corrections from 28 October 2026: monochrome chrome, Project → Data + Chats, a rounded chat surface, and a separate workbench for the selected chat's outputs. These corrections supersede the earlier Slate/Indigo direction. Earlier reports remain historical evidence.

The subsequent [project and source-set implementation](projects-and-source-sets.md) adds genuine project creation/switching, isolated storage, multiple dataset attachments, visible retained history and bounded evidence disclosures. Its current verification is under `reports/workbench-projects/2026-10-28/`; the earlier single-project interface reports below remain historical.

## User flow

Choose a discovered local dataset and ask a question. A sandboxed header inspection computes identity and separates observations, user declarations, suggestions and unknowns. Answer necessary context in the thread, review the actual proposed plan, then explicitly start approved execution. Figures, code, tables and provenance remain in the adjacent artifact canvas. Follow-ups retain exact source/selection references and receive their own approval scope.

The native scRNA workflow continues through the existing `WorkbenchService` proposal/review/run APIs. Governed scientific planning uses the existing `ScientificWorkspace`, `SerialRNAWorkflow`, operation ledger and actual review interrupts. The AnnData adapter supplies verified header bindings without inventing a bulk assay, count-value validity, ID uniqueness or biological independence. Unknown method prerequisites remain unknown.

## Components and contracts

| Component | Responsibility |
| --- | --- |
| `workbench_ui.py` | Original local token/Host/Origin boundary, exact asset composition, old routes plus input/investigation routes, fresh per-investigation request scope |
| `workbench_inputs.py` | Descriptor-relative bounded discovery, streaming hashes and supervised header-only profiles |
| `workbench_investigations.py` | Durable revision-checked turns, clarification, source/selection/session binding, explicit review/run, verified synthesis and published artifacts |
| `scientific_workspace.py` / `workflow_entrypoints.py` | Authorized bounded advancement to actual review gates, live ledger projection, existing controlled trials and synthesis readiness |
| `rna_scrna_input_contract.py` | Verified immutable AnnData artifact/header contract and explicit representation/provenance roles |
| `workbench-investigations.js` | Authenticated host-injected coordinator, dataset selection, history, exact selection chip, explicit actions and guarded polling |
| `workbench-conversation.js` | Structured actual turns, compact audits, inline clarification and scientific gate forms, legacy state rendering |
| `scientific-figure.js` | Trusted saved QC/UMAP geometry, exact identities, nice axes, real DPR, unified controls and display-only exports |
| `workbench-artifacts.js` | Explicit size/hash-verified published image/table/notebook previews; no automatic matrix fetch or active document execution |
| `workbench-theme.css` | Native accessible components, semantic themes, segmented canvas, reduced-motion-aware transitions |

Detailed contracts: [input profiles](investigation-inputs.md), [investigation service](investigation-contract.md), [frontend coordinator](investigation-frontend.md), [conversation](investigation-conversation.md), [scientific gates](scientific-gates-conversation.md), [scientific advancement](scientific-auto-advance.md), [AnnData bindings](scrna-scientific-inputs.md), [figures](refinement-figures.md), and [artifact previews](artifact-canvas.md).

## Scientific and operational boundaries

- Inspection and a checksum establish file/header facts, not experimental validity.
- Suggestions from column names never confirm assay, capture identity or independent donors.
- Questions, explicit parameter evidence and defaults retain separate attribution.
- Every analysis preserves existing startup permissions, scientific reviews, resource limits and operator gates. A new question never inherits another question's approval.
- A selected population is explicitly either saved-evidence review or context for a full-dataset plan; the fixed pipeline never pretends to fit a selected subset.
- Scientific operations remain bound to the exact session, input and AnalysisVersion. Stored consent does not renew runtime model grants after restart.
- No live model, private clinical data or cloud dispatch is established by synthetic software checks.
- Native interactive geometry remains QC/UMAP. Other real scientific outputs can be inspected as verified published artifacts; unavailable methods/outputs are not fabricated.

## Verification

The earlier integrated acceptance run passed 392 checks. The subsequent interface corrections passed 171 focused checks and have their own immutable source snapshot under `reports/workbench-refinement/2026-10-28/structured-chat-v8/`. See [the current interface handoff](structured-chat.md) for project/chat navigation, panel controls and model configuration, and [the earlier walkthrough](final-walkthrough.md) for the original numerical browser journeys and qualification limits. Source/runtime guards remain enabled.
