"""Offline visual controls preserve their labels, navigation and motion boundaries."""

import re

from devbench.analysis import workbench_ui
from tests.test_workbench_conversation_redesign import run_js
from tests.test_workbench_workflow_accordion import SETUP


def test_outline_icons_remain_decorative_and_keep_existing_controls():
    page = workbench_ui.ASSET.read_text()
    ids = (
        "nav-register",
        "nav-overview",
        "nav-data",
        "nav-analyses",
        "nav-runs",
        "nav-library",
        "nav-files",
        "evidence-toggle",
        "canvas-figures",
        "canvas-code",
        "canvas-evidence",
        "canvas-display",
    )
    for identity in ids:
        match = re.search(
            rf'<button\b[^>]*id="{identity}"[^>]*>(.*?)</button>',
            page,
            re.S,
        )
        assert match is not None, identity
        button = match.group(1)
        assert '<svg class="ui-icon"' in button, identity
        assert 'aria-hidden="true" focusable="false"' in button, identity
        caption = re.sub(r"<[^>]*>", "", button).strip()
        assert caption, identity
        if identity == "nav-register":
            assert caption == "New investigation"
    assert "el('nav-analyses').textContent=" not in page


def test_type_and_click_motion_stay_local_and_respect_immediate_dragging():
    theme = (workbench_ui.ASSET.parent / "workbench-theme.css").read_text()
    assert '--font-ui: "SF Pro Text","Helvetica Neue"' in theme
    assert "font: 13px/1.65 var(--font-ui)" in theme
    assert "transform 120ms var(--motion-ease)" in theme
    assert (
        ".layout[data-resizing=true],.layout[data-resizing=true] * "
        "{ transition: none!important; }"
    ) in theme
    assert "@media(prefers-reduced-motion:reduce)" in theme
    assert "transform: none;" in theme
    assert "transition:grid-template-columns 160ms var(--motion-ease)" in theme
    assert "@import" not in theme and "https://" not in theme


def test_normal_composer_hides_model_jargon_and_has_no_operator_fallback_welcome():
    page = workbench_ui.ASSET.read_text()
    assert '<label class="plan-mode" for="investigation-mode" hidden>' in page
    options = re.search(
        r'<select id="investigation-mode">(.*?)</select>',
        page,
        re.S,
    ).group(1)
    assert 'value="agent" selected' in options and 'value="operator"' not in options
    assert '<p id="composer-scope" class="composer-scope" hidden>' in page
    welcome = re.search(
        r'<div class="analyst-welcome">(.*?)</div>',
        page,
        re.S,
    ).group(1)
    assert "What would you like to investigate?" in welcome
    assert "Choose project data and ask a question." in welcome
    assert "operator" not in welcome.lower() and "flow-steps" not in welcome


def test_plan_metrics_use_a_compact_grid_with_labeled_numeric_tiles():
    theme = (workbench_ui.ASSET.parent / "workbench-theme.css").read_text()
    grid = re.search(r"\.wc-plan-metrics\s*\{([^{}]*)\}", theme).group(1)
    assert "repeat(auto-fill, minmax(145px, 1fr))" in grid
    assert "gap: 6px" in grid
    tile = re.search(r"\.wc-plan-metric\s*\{([^{}]*)\}", theme).group(1)
    assert "var(--soft)" in tile and "1px solid var(--border)" in tile
    assert re.search(r"\.wc-plan-metric dt\s*\{[^{}]*font-size: 11px", theme)
    value = re.search(r"\.wc-plan-metric dd\s*\{([^{}]*)\}", theme).group(1)
    assert "font-size: 13px" in value and "font-variant-numeric: tabular-nums" in value


def test_elevation_tokens_and_selected_stage_keep_workbench_chrome_monochrome():
    theme = (workbench_ui.ASSET.parent / "workbench-theme.css").read_text()
    for tier in ("#fafafa", "#ffffff", "#f2f2f2", "#0f0f0f", "#161616", "#1f1f1f",
                 "#262626"):
        assert tier in theme
    for base in ("0,0,0", "255,255,255"):
        for opacity in ("0.08", "0.16"):
            assert f"rgba({base},{opacity})" in theme
    for red, green, blue in re.findall(r"rgba?\((\d+)[, ]+(\d+)[, ]+(\d+)", theme):
        assert red == green == blue
    assert not re.search(r"rgba\([^)]*,\s+", theme)
    hex_colors = re.findall(
        r"#([0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})\b", theme, re.I
    )
    for value in hex_colors:
        width = 1 if len(value) <= 4 else 2
        red, green, blue = (
            value.lower()[index:index + width] for index in (0, width, 2 * width)
        )
        assert red == green == blue, f"#{value}"
    selected = re.search(
        r"\.wc-stage-pill\[aria-selected=true\]:not\(\.is-running\)\s*\{([^{}]*)\}",
        theme,
    ).group(1)
    assert "background:var(--raised)" in selected and "color:var(--ink)" in selected


def test_both_saved_plot_renderers_use_one_export_disclosure():
    theme = (workbench_ui.ASSET.parent / "workbench-theme.css").read_text()
    assert ".bf-export-menu" in theme
    for name in ("bulk-scientific-figure.js", "scientific-plot-workbench.js"):
        script = (workbench_ui.ASSET.parent / name).read_text()
        assert "createExportMenu(doc)" in script
        assert re.search(r"exportMenu\.append\(button\('Vector SVG'", script)
        for action in ("PNG · 2100 px", "View record JSON", "Vector PDF"):
            assert action in script
        assert re.search(r"All (saved|source) rows CSV", script)
    script = (workbench_ui.ASSET.parent / "scientific-plot-workbench.js").read_text()
    assert "Presentation bundle" in script
    run_js(SETUP + r"""
const B = require('./src/devbench/analysis/assets/bulk-scientific-figure.js');
const menu = B.createExportMenu(document);
const [summary, options] = menu.element.children;
assert.equal(menu.element.tagName, 'details');
assert.equal(summary.tagName, 'summary');
assert.equal(summary.textContent, 'Export');
assert.equal(menu.element.open, false);
const button = new Node('button');
menu.append(button);
assert.deepEqual(options.children, [button]);
summary.focus = () => { summary.focused = true; };
menu.element.open = true;
menu.element.keydown({key:'Escape',preventDefault(){}});
assert.equal(menu.element.open, false);
assert.equal(summary.focused, true);
""")


def test_raster_gallery_shortens_digest_and_copies_the_retained_hash():
    run_js(SETUP + r"""
const A = require('./src/devbench/analysis/assets/workbench-artifacts.js');
const digest = 'abcd'.repeat(16), copied = [], status = [];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:{
  clipboard:{async writeText(value){copied.push(value);}},
}});
const figure = A.rasterGallery(document,{
  url:'blob:fixture',alt:'Saved raster',status:'Render verified',sha256:digest,
  onStatus:value=>status.push(value),
});
assert.equal(figure.tagName, 'figure');
assert.equal(byClass(figure,'wa-figure-mat')[0].children[0].alt, 'Saved raster');
const footer = byClass(figure,'wa-figure-footer')[0];
assert.equal(footer.tagName, 'figcaption');
assert.equal(footer.children[0].textContent,'Render verified');
const copy = byClass(footer,'wa-figure-digest')[0];
assert.equal(copy.title,digest);
assert.equal(copy.textContent,'SHA-256 abcdabcdabcd…');
copy.click().then(()=>{
  assert.deepEqual(copied, [digest]);
  assert.deepEqual(status, ['SHA-256 copied.']);
});
""")


def test_population_conclusion_exposes_a_single_scientific_review_action():
    run_js(SETUP + r"""
const evidence={direct_answer:'Retained contrast',structured_findings:[],trials:[]};
const investigation={id:'chat',revision:1,question:'Compare groups',turns:[],
  synthesis_continuation:{status:'review_required',eligible:false,
    population_evidence:evidence},
};
const reviewButtons=()=>walk(container).filter(node=>node.tagName==='button' &&
  node.textContent?.startsWith('Inspect scientific review'));
render(investigation);
assert.equal(reviewButtons().length,1);
reviewButtons()[0].click();
assert.deepEqual(actions.at(-1),{type:'navigate',view:'evidence'});
render({...investigation,revision:2,synthesis:{population_evidence:evidence}});
assert.equal(reviewButtons().length,1);
render({...investigation,revision:3,synthesis:{population_evidence:evidence},
  synthesis_continuation:{...investigation.synthesis_continuation,eligible:true,
    trials:['Trial A'],model_scope:'local_verification',next_node:'verify'},
});
assert.equal(reviewButtons().length,1);
""")
