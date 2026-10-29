# Workbench visual refinement

The October 28 user design replaces the earlier monochrome request. The theme uses the supplied Slate, Indigo, Teal, Amber and Rose light tokens and a corresponding dark token set. `--line` aliases `--border`; `--body` supplies intermediate Slate body text. All assets remain local and the theme adds no external requests or inline scripts.

## Hierarchy and controls

Cards use 13px, weight 600 normal-case headers. Label tracking and uppercase transforms are removed from general content; only left navigation section headers (`.rail .local-label` and `.rail-jobs > .eyebrow`) retain uppercase tracking. Renderers must provide normal-case text: CSS does not rewrite literal uppercase strings.

Buttons default to a surface with a subtle border. Solid Indigo is limited to `#composer-submit`, `#register-button`, `#run-button`, and explicit `button[data-primary=true]` or `.wc-primary-action[data-wc-action=execute]` hooks. A primary hook means the current action prepares or runs a plan; renderers must not assign it merely because an action is prominent. A conditional approval action may set `data-primary=true` when appropriate. `aria-current=page` drives the inset segmented `.selection-nav` active tab; `aria-selected=true` is also supported.

## Conversation rendering contract

- `.wc-card-header` arranges `.wc-card-heading` and `.wc-status-pill` without changing the underlying record.
- `.wc-status-pill.wc-pill-good`, `warning`, `bad`, and `active` use matching semantic tokens; `wc-tone-success`, `attention`, `failure`, and `active` are supported aliases. These tones must come from recorded state, not inferred scientific quality. Neutral pills remain Slate. Existing `.wc-summary.wc-tone-*` supports `ready`/`complete`, `attention`, `failure`, and `running`.
- Direct child pills may also derive colors from parent `data-status`: `complete`/`approved`, `needs_information`/`awaiting_review`, `failed`/`rejected`, or `running`. This is a display contract, not an approval or efficacy assertion.
- `.wc-disclosure` contains a direct summary with `.wc-disclosure-summary` and an inline `.wc-chevron` SVG. A right-pointing chevron rotates 90 degrees when opened. Native summary semantics and keyboard operation remain intact. `.wc-disclosure-body` scrolls independently above 440px.
- `.wc-kv-grid` is a definition list with alternating `dt` and `dd` children. `.wc-kv-nested` contains nested lists or grids. Long record identifiers may wrap; raw inspection stays bounded by `.wc-raw-json`.
- `.wc-record-tools`, `.wc-copy-json`, `.wc-raw-inspection`, and `.wc-raw-json` support explicit raw inspection and copying without expanding JSON by default.
- Timeline rows use `.wc-step-marker`, `.wc-step-content`, and `.wc-step-time`. `.is-current` highlights the recorded current step.

## Figure rendering contract

The right pane has a compact outer `.figure-toolbar` for figure/version/mode and export actions. The renderer uses `.sf-heading` for the saved-panel selector and `.sf-toolbar` for integrated plot controls. `.sf-icon-button` is a 29px button with a 16px inline SVG; the renderer supplies an accessible name, title, action attributes, and `aria-pressed` for interaction modes. CSS never hides accessible labels or changes selection semantics.

`.sf-status` and `.sf-counts` wrap at natural word boundaries and grow vertically. Exact observation identifiers inside tables may wrap independently. The renderer owns device pixel ratio, ticks, point styling, and categorical colors. The continuous `.sf-colorbar` remains paired with the renderer's blue sequential scale, rather than recoloring the legend separately from the data.

## Motion and layout

Disclosure height/opacity transitions run for 180ms with `cubic-bezier(0.22,1,0.36,1)` behind an `interpolate-size` support guard. Browsers without support retain native details behavior. View Transition pseudo-elements use the same duration and easing. Reduced motion disables these transitions, including details content; JavaScript must also guard View Transition and WAAPI entry animation.

Desktop ordering remains navigation, conversation, figures. Laptops retain the three panes at narrower widths; below 860px conversation sits above figures beside navigation, and below 540px all panes stack with navigation first. There are no visual requirements to alter saved data, plan settings, review state, or provenance.
