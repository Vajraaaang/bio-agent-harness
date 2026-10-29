# Figure refinement · 28 October 2026

The figure module remains a trusted local renderer of the existing G02/G08 saved
geometry. This refinement changes display behavior and presentation only. It
does not edit scientific files, choose methods, run models, change thresholds or
write through a backend API.

| Requested improvement | Implemented behavior | Verification |
|---|---|---|
| One compact canvas header | Scientific type (`UMAP` / `Cell quality`), short saved panel label and one integrated action toolbar; no repeated context or scope paragraphs | DOM-mounted test verifies one toolbar and zero duplicate context/scope paragraphs |
| Accessible icon actions | Inline SVG stroke icons, `aria-label`, `title` and stable `data-figure-action` values: `pan`, `select`, `lasso`, `zoom-in`, `zoom-out`, `reset`, `fullscreen` | Mounted DOM verifies all action attributes, names, titles and actual SVG children |
| Clean scientific axes | `niceTicks(min,max,5)` uses steps of 1, 2 or 5 × a power of ten; formatted labels remove binary decimal noise and negative zero | Tests cover negative, fractional, tiny and large ranges; canvas and SVG share tick functions |
| Full Retina resolution | Backing canvas uses the complete browser device pixel ratio, with no arbitrary cap | Mounted renderer at DPR 3 produces a 2,100 × 1,365 backing store for a 700 × 455 logical canvas |
| Refined axes and grid | 11 px system font, dashed 3/4 grid using `--grid-line` / `--soft`, crisp axis outlines | Recorded canvas commands verify font, dashes and point strokes |
| Accessible categorical palette | Okabe–Ito six-color display defaults; exact saved category colors remain separately retained in provenance and exported configuration | Pure palette and mounted export tests verify input records remain byte-equivalent JSON |
| Point definition | Every point has a subtle one-pixel white outline; selected cells retain a separate contrasting ring | Canvas command and SVG export assertions |
| Smooth viewport controls | Click zoom/reset interpolates the viewport for 160 ms with ease-out cubic; reduced-motion uses an immediate update | Controlled animation-clock test checks initial, midpoint, final and reduced-motion reset states |
| Responsive gestures | Pointer pan and wheel zoom directly update the viewport; starting a gesture cancels an active animation | Implemented event flow; browser interaction verification remains separate |
| Readable status | Independent `.sf-status-item` spans allow the parent theme to wrap whole phrases | Root owns `.sf-status` flex-wrap / nontruncating CSS |
| Provenance without a JSON dump | Two-column representation/population/palette/source grid; explicit Show JSON and Copy JSON actions | JSON remains available as secondary inspection, never executable code |
| Scientific identities and exports | Exact identity selection, exclusions, original coordinates, saved numeric table and immutable semantics remain preserved | Existing and new selection, actual exporter, CSV, SVG and palette-provenance tests |

The root integration keeps the outer figure picker and presentation version in a
single compact row, hides redundant filenames as secondary metadata, and uses the
module's header for scientific type, panel choice and plot actions. Existing
IDs and backend-verified artifact boundaries remain intact.

New theme hooks are `.sf-icon-button`, `.sf-status-item`, `.sf-kv-grid` and
`.sf-evidence-actions`. The module's main classes remain compatible with the
existing theme. Inline SVG icons are application-owned stroke primitives; no CDN,
font service, icon package or extra CSP permission is required.

Fresh targeted verification: JavaScript syntax check succeeded and
`tests/test_scientific_figure_redesign.py` passed **23 tests**. The DOM harness runs
the actual module with controlled rendering/animation clocks; it does not replace
root's live-browser verification or establish scientific validity. The root
agent runs the requested combined workbench/service/packaging suite after all
writers finish.
