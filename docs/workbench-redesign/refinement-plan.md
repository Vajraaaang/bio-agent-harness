# Workbench refinement · 28 October 2026

Source: attached refinement brief `4c65f689-f32e-4110-a9a0-34eedc342822/pasted-text-1.txt`. Its Slate/Indigo/semantic-color design explicitly supersedes the previous monochrome direction.

## Owners

- Design agent: workbench-theme.css and refinement-design.md.
- Conversation agent: workbench-conversation.js, its behavior tests and refinement-conversation.md.
- Figure agent: scientific-figure.js, its behavior tests and refinement-figures.md.
- Root: HTML integration, draft/transition/header behavior, integration tests and final verification.

## Required end state

1. Exact supplied light tokens, coherent dark tokens; uppercase sidebar labels only; normal-case card headings/status pills; segmented stage controls; solid primary action hierarchy.
2. Compact outer figure header and integrated canvas toolbar, accessible inline icons/action attributes, no repeated filename/question labels.
3. Actual DPR backing store, tested 1/2/5 nice ticks, dashed grid/11px titles, display-only Okabe-Ito palette and white outlines, wrapping status text.
4. Short run title plus filename/dimensions badge; original question appears in conversation; submitted unchanged drafts clear while active edits survive; structured evidence/transition timeline with secondary raw JSON copy.
5. Reduced-motion guarded View Transitions for stages/runs/versions, supported disclosure CSS, run-change-only 180ms card entry, 160ms cubic viewport button transitions with exact scientific values untouched.
6. Required six-file pytest command passes, relevant UI regression checks pass, browser proof verifies final states. Numerical verification waits for source freeze.

Current status: implementation and local software acceptance complete. The final isolated run passed 392 checks with zero failures/errors/skips; actual native and recorded scientific fixture journeys, responsive screenshots, exact selection/export checks and final source hashes are retained in reports/workbench-refinement/2026-10-28. Baseline manifest records exact hashes and whether copies match the prior final state; figure work had already started during that copy and is marked accordingly. See [the final walkthrough](final-walkthrough.md) for the completed architecture and explicit qualification limits.

## Architectural expansion from the next user attachment

The adopted request in `86773422-dea9-4c77-91e6-c50e6670d2d3/Pasted text.txt` extends this work, rather than cancelling the visual requirements above. Completion now also requires:

- Bounded canonical input discovery and sandboxed read-only inspection, automatic hashes, actual header metadata, and an attributed observed/declarations/inferred/unknowns profile.
- Durable multi-turn `/api/investigations` service preserving exact input, parent and selected-observation bindings; clarification with real columns; intent attribution; existing job/scientific routing; explicit review and run actions; verified synthesis.
- Scientific `advance_to_gate` chaining actual existing operations and ledgers under existing campaign/actor permissions, stopping at required review/execution interrupts; optional authorized synthesis chain.
- Unified composer with dataset/mode/selection context, thread history, inline clarification and real legacy approval/run controls, synchronized Figures/Code/Evidence/Display canvas.
- All existing routes/IDs remain operational. The ten test files named by the latest brief pass after source freeze, with additional security/identity/race and browser integration coverage.

Expanded owners: architecture_audit owns workbench_inputs.py; investigation_backend owns workbench_investigations.py; scientific_auto_advance owns scientific_workspace.py; investigation_frontend owns workbench-investigations.js; conversation_module owns investigation thread rendering. Root owns service/HTTP/package wiring and HTML integration. All writers have disjoint files and must not revert one another.

The actual existing scientific audit was bulk-only. A legitimate verified AnnData contract and narrow audit/planner bind points are now part of the architectural implementation, rather than fabricating a bulk contract or using an ungoverned generic shortcut. Scientific owner also owns workflow_actions.py, method_planner.py and new rna_scrna_input_contract.py. Header-only facts do not establish count validity, identity uniqueness or biological independence.

Independent review found cross-session scientific forwarding; the owner corrected all session and typed-reference binding before dispatch. Further audits cover restart-scoped model grants, explicit selection population use, truthful source-artifact retention, and source rechecks before every chained node.

Offline/no-remote-assets operation, nonce CSP, source/receipt integrity, exact cell identities, approval, admission and resource limits remain authoritative. No live model/cloud/private-data work is authorized by this refinement.
