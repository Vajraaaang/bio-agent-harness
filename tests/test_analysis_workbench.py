import json
from pathlib import Path
import shutil
import zipfile

import pytest

pytest.importorskip("scanpy")


@pytest.fixture(scope="module")
def portable_bundle(tmp_path_factory):
    from tests.test_analysis_singlecell import counts_fixture
    from devbench.analysis.checkpoints import sha256
    from devbench.analysis.workbench import default_parameters, prepare_bundle, execute_bundle
    folder = tmp_path_factory.mktemp("portable-scRNA")
    source = folder/"data.h5ad"
    data = counts_fixture()
    data.obs["leiden"] = ["0"]*40+["1"]*40
    data.obs["celltypes"] = ["T cell"]*40+["B cell"]*40
    data.obs["conf_score"] = .7
    data.write_h5ad(source)
    parameters = default_parameters(data_sha256=sha256(source), counts_layer=None,
        min_genes=3, mad_limit=None, n_top_genes=30, n_pcs=8, seed=128,
        markers={"T cell": ["Cd3d", "Cd3e", "Trac", "Il7r"],
                 "B cell": ["Ms4a1", "Cd79a", "Cd79b", "Cd19"]})
    bundle = folder/"bundle"
    prepare_bundle(source, bundle, parameters)
    result = execute_bundle(bundle)
    return source, bundle, result


def test_full_notebook_bundle_passes_reviewer_and_has_no_data_copy(portable_bundle):
    import nbformat
    source, root, result = portable_bundle
    assert result["review_passed"], json.loads((root/"reviewer-report.json").read_text())
    assert result["verified"]
    report = json.loads((root/"report.json").read_text())
    assert report["execution_tier"] == "local_supervised"
    assert set(report["conditions"]) == {"uncorrected", "harmony"}
    assert report["annotation"]["status"] == "completed"
    for path in ("notebook.ipynb", "environment.lock", "summary.md", "reviewer-report.json",
                 "receipts.json", "root-receipt.json", "figures/harmony.png"):
        assert (root/path).is_file()
    notebook = nbformat.read(root/"notebook.ipynb", as_version=4)
    assert all("/Users/" not in cell.source and "/content/" not in cell.source for cell in notebook.cells)
    with zipfile.ZipFile(result["archive"]) as archive:
        assert "bindings.json" not in archive.namelist()
        assert not any(name.endswith("data.h5ad") for name in archive.namelist())


def test_bundle_import_retains_tier_and_reexecutes_from_relative_source(portable_bundle, tmp_path):
    from devbench.analysis.bundles import import_bundle, verify_bundle
    from devbench.analysis.workbench import prepare_bundle, execute_bundle
    source, original, result = portable_bundle
    imported = tmp_path/"imported"
    receipt = import_bundle(result["archive"], imported, result["manifest_sha256"], data_path=source)
    assert receipt["execution_tier"] == "local_supervised"
    verify_bundle(imported, result["manifest_sha256"])
    parameters = json.loads((imported/"parameters.json").read_text())
    parameters.pop("source_sha256")
    parameters["parent_root"] = result["manifest_sha256"]
    replay = tmp_path/"replay"
    prepare_bundle(source, replay, parameters, project=imported/"source")
    repeated = execute_bundle(replay)
    assert repeated["review_passed"]
    assert json.loads((replay/"receipts.json").read_text())["parent_root"] == result["manifest_sha256"]
    assert json.loads((original/"report.json").read_text()) == json.loads((replay/"report.json").read_text())


def test_integrity_cannot_be_claimed_after_output_tampering(portable_bundle, tmp_path):
    from devbench.analysis.bundles import import_bundle, verify_bundle
    _, _, result = portable_bundle
    root = tmp_path/"corrupted"
    import_bundle(result["archive"], root, result["manifest_sha256"])
    with (root/"summary.md").open("a") as handle:
        handle.write("Proven clinical validity\n")
    with pytest.raises(ValueError, match="artifact_mismatch"):
        verify_bundle(root, result["manifest_sha256"])


def test_archive_path_escape_is_rejected_before_extraction(tmp_path):
    from devbench.analysis.bundles import import_bundle
    archive = tmp_path/"bad.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../escape", "unsafe")
    with pytest.raises(ValueError, match="unsafe_bundle"):
        import_bundle(archive, tmp_path/"output", "0"*64)
    assert not (tmp_path/"output").exists()


def test_unsupported_prompt_is_not_silently_claimed_as_solved():
    from devbench.analysis.workbench import default_parameters
    with pytest.raises(ValueError, match="Unsupported prompt"):
        default_parameters(prompt="infer a clinical treatment recommendation", data_sha256="a"*64)
