"""Progressive picker motion keeps real native select controls and reduced-motion support."""
from pathlib import Path
import re

import tinycss2

from devbench.analysis import workbench_ui


def test_picker_motion_is_progressive_and_covers_enter_exit_and_reduced_motion():
    theme = (workbench_ui.ASSET.parent / "workbench-theme.css").read_text()
    rules = tinycss2.parse_stylesheet(theme, skip_comments=True, skip_whitespace=True)
    assert not [rule for rule in rules if rule.type == "error"]
    supported = [rule for rule in rules if rule.type == "at-rule" and rule.lower_at_keyword == "supports"
                 and "appearance: base-select" in tinycss2.serialize(rule.prelude)]
    assert len(supported) == 1
    picker = tinycss2.serialize(supported[0].content)
    assert "::picker(select)" in picker and "@starting-style" in picker
    assert "display 160ms allow-discrete" in picker and "overlay 160ms allow-discrete" in picker
    assert "option:focus-visible" in picker and "option:checked" in picker
    assert "display: inline-flex" in picker and "flex-wrap: nowrap" in picker
    reduced = [tinycss2.serialize(rule.content) for rule in rules if rule.type == "at-rule"
               and rule.lower_at_keyword == "media" and "prefers-reduced-motion:reduce" in tinycss2.serialize(rule.prelude)]
    assert any("::picker(select)" in block and "transition: none!important" in block for block in reduced)


def test_primary_dropdowns_remain_native_selects_with_existing_ids():
    page = workbench_ui.ASSET.read_text()
    for control in ("investigation-dataset", "figure-select", "version-select", "figure-mode", "theme-select"):
        assert re.search(r'<select\b[^>]*\bid="' + re.escape(control) + r'"', page), control
    assert 'role="combobox"' not in page
