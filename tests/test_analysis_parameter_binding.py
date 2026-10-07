"""Workbench configuration and resume integrity; small local fixtures only."""
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("overrides", [{"min_gene": 3}, {"memory_mib": 256},
    {"workflow": "unregistered"}, {"execution_tier": "colab"}])
def test_unknown_or_reserved_overrides_are_not_silently_accepted(overrides):
    from devbench.analysis.workbench import default_parameters
    with pytest.raises(ValueError, match="parameter|override"):
        default_parameters(data_sha256="a" * 64, **overrides)


@pytest.mark.parametrize("overrides", [
    {"mad_limit": 0}, {"mad_limit": -1}, {"mad_limit": float("nan")}, {"mad_limit": True},
    {"max_mito_percent": -1}, {"max_mito_percent": 101}, {"max_mito_percent": float("nan")},
    {"max_mito_percent": True}, {"resolution": float("nan")}, {"resolution": float("inf")},
    {"resolution": True}, {"batch_key": ""}, {"counts_layer": "../counts"}, {"symbol_column": []},
    {"model_path": "models/model.pkl"}, {"model_sha256": "a" * 64},
    {"model_path": "../outside.pkl", "model_sha256": "a" * 64},
    {"model_path": "/absolute.pkl", "model_sha256": "a" * 64},
    {"model_path": "models\\outside.pkl", "model_sha256": "a" * 64}, {"parent_root": "not-a-hash"},
])
def test_invalid_parameters_fail_before_input_access(overrides):
    from devbench.analysis.workbench import default_parameters
    with pytest.raises(ValueError):
        default_parameters(data_sha256="a" * 64, **overrides)


def _prepared(root):
    from tests.test_analysis_singlecell import counts_fixture
    from devbench.analysis.checkpoints import sha256
    from devbench.analysis.workbench import default_parameters, prepare_bundle
    source = root / "input.h5ad"
    counts_fixture().write_h5ad(source)
    parameters = default_parameters(data_sha256=sha256(source), counts_layer=None,
        min_genes=3, mad_limit=None, n_top_genes=30, n_pcs=8, seed=128,
        stage_timeout_seconds=90, memory_mb=1024, correction="none")
    bundle = root / "bundle"
    prepare_bundle(source, bundle, parameters)
    return source, bundle


def test_valid_optional_settings_and_existing_defaults_are_preserved():
    from devbench.analysis.workbench import default_parameters
    values = default_parameters(data_sha256="a" * 64, mad_limit=None, max_mito_percent=0,
        counts_layer=None, model_path="models/custom.pkl", model_sha256="b" * 64, parent_root="c" * 64)
    assert values["memory_mb"] == 8192 and values["stage_timeout_seconds"] == 600
    assert values["growth_window_seconds"] == 0 and values["scvi_timeout_seconds"] == 3600
    assert values["mad_limit"] is None and values["max_mito_percent"] == 0
    assert values["model_path"] == "models/custom.pkl"


def test_changed_qc_parameters_cannot_reuse_a_finished_checkpoint(tmp_path):
    from devbench.analysis.workbench import _stage_worker
    _, bundle = _prepared(tmp_path)
    _stage_worker(str(bundle), "qc")
    receipt = (bundle / "checkpoints/qc/receipt.json").read_bytes()
    parameters = json.loads((bundle / "parameters.json").read_text())
    parameters["min_genes"] = 999
    (bundle / "parameters.json").write_text(json.dumps(parameters))
    with pytest.raises(ValueError, match="parameters.*changed|configuration.*changed"):
        _stage_worker(str(bundle), "qc")
    assert (bundle / "checkpoints/qc/receipt.json").read_bytes() == receipt


@pytest.mark.parametrize("entry", ["run_stage", "stage_cli", "execute_bundle"])
def test_changed_limits_are_rejected_before_launching_any_process(tmp_path, monkeypatch, entry):
    from devbench.analysis import workbench
    from devharness_core import watchdog
    from devbench.analysis import notebooks
    _, bundle = _prepared(tmp_path)
    parameters = json.loads((bundle / "parameters.json").read_text())
    parameters["memory_mb"] *= 2
    (bundle / "parameters.json").write_text(json.dumps(parameters))
    def forbidden(*args, **kwargs):
        pytest.fail("Changed resource limits reached an execution boundary")
    monkeypatch.setattr(workbench.subprocess, "run", forbidden)
    monkeypatch.setattr(watchdog, "run_sandboxed", forbidden)
    monkeypatch.setattr(notebooks, "execute_notebook", forbidden)
    with pytest.raises(ValueError, match="parameters.*changed|configuration.*changed"):
        if entry == "run_stage":
            workbench.run_stage(bundle, "qc")
        elif entry == "stage_cli":
            workbench.main(["--_stage", "qc", "--bundle", str(bundle)])
        else:
            workbench.execute_bundle(bundle)


def test_joint_parameter_and_identity_change_does_not_relabel_old_checkpoints(tmp_path):
    from devbench.analysis.workbench import _stage_worker, run_stage
    _, bundle = _prepared(tmp_path)
    _stage_worker(str(bundle), "qc")
    parameters = json.loads((bundle / "parameters.json").read_text())
    identity = json.loads((bundle / "checkpoint-identity.json").read_text())
    parameters["min_genes"] = 999
    identity["parameters"] = parameters
    (bundle / "parameters.json").write_text(json.dumps(parameters))
    (bundle / "checkpoint-identity.json").write_text(json.dumps(identity))
    with pytest.raises(ValueError, match="checkpoint.*identity|configuration.*changed"):
        run_stage(bundle, "qc")


def test_invalid_prepare_is_rejected_before_runtime_or_data_reads(tmp_path, monkeypatch):
    from devbench.analysis import workbench, runtime
    parameters = workbench.default_parameters(data_sha256="a" * 64)
    parameters["typo_parameter"] = True
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid prepared configuration reached a runtime or data read")
    monkeypatch.setattr(runtime, "observe_runtime", forbidden)
    monkeypatch.setattr(runtime, "verify_data_identity", forbidden)
    with pytest.raises(ValueError, match="parameter"):
        workbench.prepare_bundle(tmp_path / "absent.h5ad", tmp_path / "must-not-create", parameters)
    assert not (tmp_path / "must-not-create").exists()


@pytest.mark.parametrize("destination", ["parameters.json", "source/devbench/analysis/workbench.py",
    "models/.hidden.pkl", "models/nested/model.pkl"])
def test_model_asset_cannot_overwrite_control_source_or_unreceipted_paths(tmp_path, destination):
    from devbench.analysis.checkpoints import sha256
    from devbench.analysis.workbench import default_parameters, prepare_bundle
    from tests.test_analysis_singlecell import counts_fixture
    source = tmp_path / "data.h5ad"
    counts_fixture().write_h5ad(source)
    asset = tmp_path / "fixture-model-bytes"
    asset.write_bytes(b"opaque test asset; never loaded as a classifier")
    parameters = default_parameters(data_sha256=sha256(source), counts_layer=None)
    parameters.update(model_path=destination, model_sha256=sha256(asset))
    with pytest.raises(ValueError, match="model_path"):
        prepare_bundle(source, tmp_path / "must-not-create", parameters, model_path=asset)
    assert not (tmp_path / "must-not-create").exists()
