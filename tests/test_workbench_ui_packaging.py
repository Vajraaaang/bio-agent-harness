"""Operational HTML and studio modules ship through source and wheel custody."""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from scripts.build_colab_notebook import (
    allowed_bundle_path,
    build_source_bundle,
    extract_source_bundle,
)
from scripts.verify_wheel_custody import MILESTONE_WHEEL_MEMBERS

ROOT = Path(__file__).resolve().parents[1]
ASSET = "src/devbench/analysis/assets/workbench-ui.html"
STUDIO = (
    "workbench-studio.js",
    "workbench-context.js",
    "workbench-lineage.js",
    "workbench-qc.js",
    "workbench-thesis-studio.js",
    "workbench-studio.css",
)
DIRECTORY = "src/devbench/analysis/assets"


def test_existing_source_builder_and_extractor_preserve_the_operational_asset(
    workspace_tmp_path,
):
    bundle = build_source_bundle(ROOT)
    expected = (ASSET, *(f"{DIRECTORY}/{name}" for name in STUDIO))
    assert set(expected) <= set(bundle["files"])
    extracted = extract_source_bundle(
        bundle["base64"], workspace_tmp_path / "source", bundle["sha256"]
    )
    for asset in expected:
        assert (extracted / asset).read_bytes() == (ROOT / asset).read_bytes()
    assert extract_source_bundle(
        bundle["base64"], extracted, bundle["sha256"], verify_only=True
    ) == extracted
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; "
        "source_root = Path(sys.argv[1]) / 'src'; "
        "sys.path.insert(0, str(source_root)); "
        "from devbench.analysis import workbench_ui; "
        "assert Path(workbench_ui.__file__).is_relative_to(source_root); "
        "assert workbench_ui.ASSET == Path(sys.argv[2]); "
        "assert b'Operator-created plan' in workbench_ui.ASSET.read_bytes()",
        str(extracted),
        str(extracted / ASSET),
    ]
    result = subprocess.run(
        command, cwd=extracted, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr


def test_package_data_retains_existing_entries_and_adds_exact_html_asset():
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text())
    data = configuration["tool"]["setuptools"]["package-data"]["devbench"]
    retained = {
        "tasks/catalog.json",
        "study/public_sources.json",
        "study/biology_catalog.json",
        "analysis/assets/workbench-ui.html",
    }
    assert retained | {f"analysis/assets/{name}" for name in STUDIO} <= set(data)
    assert {
        f"devbench/analysis/assets/{name}" for name in STUDIO
    } <= set(MILESTONE_WHEEL_MEMBERS)


def test_studio_modules_and_styles_render_under_the_page_nonce():
    from devbench.platform.workbench_ui import render_page

    nonce = "studio-custody-nonce"
    page = render_page(nonce)
    assert f'<script nonce="{nonce}">' in page
    assert f'<style nonce="{nonce}">' in page
    for name in STUDIO:
        source = (ROOT / DIRECTORY / name).read_text().strip()
        assert source in page
        assert "https://" not in source and "@import" not in source
    assert '<dialog id="receipt-drawer" popover="manual"' in page
    assert '<meta name="color-scheme" content="light dark">' in page


def test_render_page_composes_research_module_in_the_nonce_script():
    from devbench.platform.workbench_ui import render_page

    page = render_page("research-nonce")
    script_start = page.split('<script nonce="research-nonce">', 1)[1]
    script = script_start.split("</script>", 1)[0]
    source = (ROOT / DIRECTORY / "workbench-research.js").read_text().strip()
    assert source in script
    assert "/*__WORKBENCH_RESEARCH_MODULE__*/" not in page


def test_workspace_state_accepts_thesis_location():
    from devbench.platform.workbench_ui import _validate_ui_state

    _validate_ui_state({"view": "thesis"})


@pytest.mark.parametrize(
    "name",
    [
        "devbench/analysis/assets/other.html",
        "devbench/analysis/assets/data.html",
        "devbench/analysis/assets/../workbench-ui.html",
        "workbench_runs/dashboard-prototype.html",
    ],
)
def test_ui_source_allowlist_does_not_expand_to_other_html(name):
    assert not allowed_bundle_path(name)
