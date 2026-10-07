"""The operational HTML ships with the existing source/wheel packaging paths."""
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

from scripts.build_colab_notebook import allowed_bundle_path, build_source_bundle, extract_source_bundle


ROOT = Path(__file__).resolve().parents[1]
ASSET = "devbench/analysis/assets/workbench-ui.html"


def test_existing_source_builder_and_extractor_preserve_the_operational_asset(tmp_path):
    bundle = build_source_bundle(ROOT)
    assert ASSET in bundle["files"], "Operational HTML is missing from the native source bundle"
    extracted = extract_source_bundle(bundle["base64"], tmp_path / "source", bundle["sha256"])
    assert (extracted / ASSET).read_bytes() == (ROOT / ASSET).read_bytes()
    assert extract_source_bundle(bundle["base64"], extracted, bundle["sha256"], verify_only=True) == extracted
    command = [sys.executable, "-c",
        "from pathlib import Path; import sys; sys.path.insert(0, sys.argv[1]); "
        "from devbench.analysis import workbench_ui; "
        "assert Path(workbench_ui.__file__).is_relative_to(Path(sys.argv[1])); "
        "assert workbench_ui.ASSET.is_file(); "
        "assert b'Operator-created plan' in workbench_ui.ASSET.read_bytes()", str(extracted)]
    result = subprocess.run(command, cwd=extracted, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_package_data_retains_existing_entries_and_adds_exact_html_asset():
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text())
    data = configuration["tool"]["setuptools"]["package-data"]["devbench"]
    assert {"tasks/catalog.json", "study/public_sources.json", "study/biology_catalog.json"} <= set(data)
    assert "analysis/assets/workbench-ui.html" in data


@pytest.mark.parametrize("name", ["devbench/analysis/assets/other.html", "devbench/analysis/assets/data.html",
    "devbench/analysis/assets/../workbench-ui.html", "workbench_runs/dashboard-prototype.html"])
def test_ui_source_allowlist_does_not_expand_to_other_html(name):
    assert not allowed_bundle_path(name)
