"""Directional pane controls preserve their independent actions."""
from pathlib import Path
import re


def test_header_controls_use_distinct_directional_glyphs_without_a_layout_menu():
    root=Path(__file__).resolve().parents[1]
    page=(root/'devbench/analysis/assets/workbench-ui.html').read_text()
    header=re.search(r'<header class="inspector-head">(.*?)</header>',page,re.S).group(1)
    assert 'id="layout-menu' not in page
    assert 'id="toggle-navigation"' not in header and 'id="toggle-workbench"' not in header
    shell=page.split('<aside id="workspace-rail"',1)[0]
    assert 'id="toggle-navigation"' in shell and 'id="toggle-workbench"' in shell
    assert 'd="m12 5-5 5 5 5"' in shell and 'd="m8 5 5 5-5 5"' in shell
    theme=(root/'devbench/analysis/assets/workbench-theme.css').read_text()
    assert '#toggle-workbench.edge-panel-toggle { right:12px; }' in theme
    assert '.layout[data-rail-collapsed=true] #toggle-navigation { left:16px; }' in theme


def test_pane_control_appearance_is_quiet_and_keyboard_focus_remains_visible():
    root=Path(__file__).resolve().parents[1]
    theme=(root/'devbench/analysis/assets/workbench-theme.css').read_text()
    assert '.panel-toggle:focus:not(:focus-visible) { outline:none; box-shadow:none; }' in theme
    assert '.panel-toggle:focus-visible { outline:2px solid var(--ink);' in theme
    assert '.panel-toggle[aria-expanded=false] .panel-arrow { transform:rotate(180deg); }' in theme
