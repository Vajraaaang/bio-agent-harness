"""Offline visual controls preserve their labels, navigation and motion boundaries."""
import re
from devbench.analysis import workbench_ui


def test_outline_icons_remain_decorative_and_keep_existing_controls():
    page = workbench_ui.ASSET.read_text()
    for identity in ["nav-register", "nav-overview", "nav-data", "nav-analyses", "nav-runs", "nav-library", "nav-files", "evidence-toggle", "canvas-figures", "canvas-code", "canvas-evidence", "canvas-display"]:
        button = re.search(r'<button\b[^>]*id="' + identity + r'"[^>]*>(.*?)</button>', page, re.S).group(1)
        assert '<svg class="ui-icon"' in button, identity
        assert 'aria-hidden="true" focusable="false"' in button, identity
        if identity == "nav-register":
            assert re.search(r'<button\b[^>]*id="nav-register"[^>]*aria-label="New chat"[^>]*title="New chat"', page)
        else:
            assert re.sub(r'<.*?>', '', button).strip(), identity
    assert "el('nav-analyses').textContent=" not in page


def test_type_and_click_motion_stay_local_and_respect_immediate_dragging():
    theme = (workbench_ui.ASSET.parent / "workbench-theme.css").read_text()
    page = workbench_ui.ASSET.read_text()
    assert '--font-ui: "SF Pro Text","Helvetica Neue"' in theme
    assert 'font: 13px/1.65 var(--font-ui)' in theme
    assert 'transform 120ms var(--motion-ease)' in theme
    assert '.layout[data-resizing=true],.layout[data-resizing=true] * { transition: none!important; }' in theme
    assert '@media(prefers-reduced-motion:reduce)' in theme
    assert 'transform: none;' in theme
    assert 'transition:grid-template-columns 160ms var(--motion-ease)' in theme
    assert '@import' not in theme and 'https://' not in theme


def test_normal_composer_hides_model_jargon_and_has_no_operator_fallback_welcome():
    page = workbench_ui.ASSET.read_text()
    assert '<label class="plan-mode" for="investigation-mode" hidden>' in page
    options = re.search(r'<select id="investigation-mode">(.*?)</select>', page, re.S).group(1)
    assert 'value="agent" selected' in options and 'value="operator"' not in options
    assert '<p id="composer-scope" class="composer-scope" hidden>' in page
    welcome = re.search(r'<div class="analyst-welcome">(.*?)</div>', page, re.S).group(1)
    assert 'What would you like to investigate?' in welcome
    assert 'Choose project data and ask a question.' in welcome
    assert 'operator' not in welcome.lower() and 'flow-steps' not in welcome
