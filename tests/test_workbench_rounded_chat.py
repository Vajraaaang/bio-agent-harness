"""Rounded chat surfaces preserve the existing responsive and input boundaries."""
from devbench.analysis import workbench_ui


def test_chat_surface_is_inset_rounded_and_keeps_scroll_boundaries():
    theme = (workbench_ui.ASSET.parent / "workbench-theme.css").read_text()
    assert ".inspector { border:1px solid var(--border); border-radius:16px; overflow:hidden; }" in theme
    assert "height:calc(100dvh - 20px); min-height:0; margin-block:10px;" in theme
    assert ".inspector-body { min-height:0; }" in theme
    assert "margin:8px; border-radius:12px; min-height:470px; height:auto;" in theme
    assert "min-height:510px; height:auto;" in theme


def test_composer_is_compact_without_removing_controls_or_error_feedback():
    theme = (workbench_ui.ASSET.parent / "workbench-theme.css").read_text()
    page = workbench_ui.ASSET.read_text()
    assert ".composer-card { border-radius:18px; padding:13px 14px 11px; box-shadow:none; }" in theme
    assert "height:76px; min-height:76px; max-height:160px;" in theme
    assert "resize:vertical;" in theme
    assert ".investigation-composer .dataset-context { display:flex; align-items:center;" in theme
    assert "#save-workspace,.workspace-save-status:not(.error) { display:none; }" in theme
    assert ".workspace-save-status.error { display:block;" in theme
    for identity in ["question", "composer-submit", "composer-message", "save-workspace"]:
        assert 'id="' + identity + '"' in page
    assert "#composer-message { display:none" not in theme
