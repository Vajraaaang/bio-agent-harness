# Conversation refinement

The conversation now uses normal-case card headings and compact status pills. The original question appears in its question card, plan metadata lives in a structured accordion, and exact retained controller transitions form a vertical timeline. The module keeps its bounded proposal, offline fixture and operator-created source distinctions. Scientific scope appears once in the conversation footer; completed-state copy separately identifies the limit of independent scientific review.

## Integration

The existing API is unchanged:

```javascript
window.WorkbenchConversation.render(container, {
  state, config, loading: Boolean(selected && !state),
  revision: selectedRevisionRecord, onAction
});
```

`renderConversationWorkspace` is an alias of the same function. `buildModel` and `failureSummary` remain available. The host continues to own composer drafts, question deduplication, stage transitions, dataset context and every execution/API action. Callback types remain strictly `navigate` and `draft`; no follow-up auto-submits, approves or executes.

## CSS contract

| Selector | Intended styling |
| --- | --- |
| `.wc-card` | Scientific conversation card; WAAPI entry target. No perpetual CSS entry animation. |
| `.wc-card-header` | Horizontal header aligned to baseline, modest bottom gap. |
| `.wc-card-heading` | Normal-case 13px, weight 600, zero letter spacing. |
| `.wc-status-pill` | Small readable state pill aligned to header end. |
| `.wc-pill-good`, `.wc-pill-bad`, `.wc-pill-neutral` | Teal, rose, neutral semantic variants. Label text conveys state independent of color. |
| `.wc-disclosure` | Bordered native details accordion with restrained rounding. |
| `.wc-disclosure-summary` | Flex summary row; hide default marker in CSS, leave actual SVG chevron visible. |
| `.wc-disclosure-title` | Accordion title, normal case. |
| `.wc-chevron` | 16px SVG; rotate on direct parent details `[open]`, transition 180ms. Avoid a descendant selector that also rotates nested closed accordions. |
| `.wc-disclosure-body` | Padded accordion content. |
| `.wc-kv-grid` | Native definition list; two columns (`minmax(0, .8fr) minmax(0, 1.2fr)` or equivalent), readable key/value alignment. |
| `.wc-kv-grid > dt`, `.wc-kv-grid > dd` | Direct children form pairs. Reset default `dd` margin; allow wrapping and long input references. |
| `.wc-kv-nested` | Nested definition list within a parent definition; compact inset styling. |
| `.wc-value`, `.wc-value-empty`, `.wc-truncated` | Primitive, unavailable, bounded-view labels. |
| `.wc-record-tools` | Small row for copy control and status. |
| `.wc-copy-json`, `.wc-copy-status` | Secondary small button and accessible copy result. |
| `.wc-raw-inspection` | Collapsed secondary JSON view, not a competing main accordion. |
| `.wc-raw-json` | Readonly textarea; monospace, scrollable, bounded visual height. |
| `.wc-execution` | Recorded-activity accordion; summary includes a steps-count pill. |
| `.wc-timeline` | Semantic ordered vertical timeline. |
| `.wc-step` | One exact retained transition, with `data-status` and optional `.is-current`. |
| `.wc-step-marker`, `.wc-step-content` | Non-color-only marker and text column. |
| `.wc-step-title`, `.wc-step-time` | State text and actual source timestamp. |
| `.wc-scope`, `.wc-authorship`, `.wc-scientific-scope` | Single source/scope footer. Keep readable, secondary hierarchy. |

No `.eyebrow`, `.message-label` or `.wc-label` is emitted by the module. The existing generic analyst/user/record message classes remain for host compatibility.

## Structured evidence and raw access

Disclosures render objects/arrays through native `dl`/`dt`/`dd`, with at most 120 fields per disclosure, depth five and 30 entries per object/array. Truncation is explicit and leaves the complete source available in the collapsed JSON view. Data is inserted with `textContent`; no source-provided HTML or executable code is interpreted.

Each accordion has an actual inline SVG chevron built through the SVG namespace, not a text triangle. `Copy JSON` uses the browser clipboard when available. If unavailable, rejected or synchronously blocked, the secondary readonly JSON textarea opens and receives focus/selection for manual copy. This requires no CSP change, external dependency, `eval`, inline handler or document-wide copy command.

## Motion

A `WeakMap` tracks the last active `state.id` per conversation container. Newly rendered `.wc-card` nodes receive a 180ms opacity/4px-translation WAAPI animation only when that run identity changes. Background updates to the same run never trigger card-entry animation. `prefers-reduced-motion: reduce` disables entry motion. Missing WAAPI simply leaves the content visible.

The host stylesheet owns accordion `::details-content` animation, reduced-motion rules and any stage View Transitions. The module does not navigate or start View Transitions itself.

## Validation

Eleven module checks pass, including the existing eight behavior checks and three added checks covering structured safe DOM, raw-JSON fallback, bounded recursive records, normal-case headings/state pills, run-identity motion and reduced-motion behavior. The three added checks failed before implementation, then passed. `node --check` also passes.

The host must still run the requested combined suite and browser checks. Local DOM/module checks do not establish scientific correctness, live model efficacy or complete browser accessibility.
