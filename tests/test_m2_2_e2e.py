"""Run M2 integration and selective inference through the public research tools."""

import hashlib
import json

import anndata as ad
import numpy as np
import pandas as pd
import pytest

from devbench.mcp.registry import pins
from devbench.mcp.server import dispatch
from rna_kernels.m2_contract import WAVE2_AXES
from tests.test_m2_1_e2e import _plan, _result, _run
from tests.test_mcp_13_tool_control_plane import _ok
from tests.test_research_daemon import _admission

pytest_plugins = ["tests.test_m2_1_e2e"]
pytestmark = pytest.mark.wal


def test_wave2_discovery(plane):
    rows = _ok(plane, "list_kernels_and_methods", {"assay": "snrna_seq"})["kernels"]
    observed = {r["kernel_id"]: r for r in rows}
    for name, axes in WAVE2_AXES.items():
        row = observed[name]
        assert tuple(row["capability_axes"]) == axes
        assert row["calibration_status"] == "uncalibrated_L1"
        assert row["supports_plot_sidecar"]
        if name in {"scrna_integrate_qualified", "scrna_cross_species_integrate"}:
            assert "integrated_latent_or_graph" in row["output_representations"]


def _data(seed=20261127):
    rng = np.random.default_rng(seed)
    n, g = 192, 48
    return ad.AnnData(
        rng.negative_binomial(4, 0.25, size=(n, g)),
        obs=pd.DataFrame(
            {
                "unit": [f"u{i}" for i in range(n)],
                "donor": [f"d{i % 8}" for i in range(n)],
                "batch": ["b0"] * n,
                "cell_type": np.where(np.arange(n) % 2, "glia", "neurons"),
            },
            index=[f"cell-{i}" for i in range(n)],
        ),
        var=pd.DataFrame(index=[f"g{i}" for i in range(g)]),
    )


def _register(plane, reg, path, representation):
    entry = _admission(path)
    entry["input"].update(
        file_kind="reference" if representation == "reference_only" else "metadata",
        investigation_role="reference_atlas",
    )
    entry.update(session_id=reg["session_id"], representation_class=representation)
    result = _ok(plane, "register_investigation_input", entry)["artifact"]
    return {k: result[k] for k in ("artifact_id", "sha256")}


def _require_image(plane):
    live = _ok(plane, "inspect_runtime_capabilities", {"include_packages": False})
    if not set(pins.M2_WAVE2_IMAGES.values()).intersection(
        r["image_id"] for r in live["available_images"]
    ):
        pytest.skip("Pinned wave-two OCI image is unavailable")


def test_pooled_r1_markers_refused_at_freeze(plane):
    data = _data()
    data.obs["species_taxon_id"] = np.resize([9606, 10090], data.n_obs)
    path = plane.input_root / "pooled.h5ad"
    data.write_h5ad(path)
    reg = _ok(plane, "register_investigation_input", _admission(path))
    result = dispatch(
        plane,
        "freeze_analysis_plan",
        _plan(reg, "scrna_clusterde", {"batch_key": "batch"}),
    )
    assert (
        result.isError and result.structuredContent["category"] == "scientific_refusal"
    )


@pytest.mark.parametrize(
    "name", ["scrna_clusterde", "scrna_cadet", "scrna_nb_countsplit"]
)
def test_selective_marker_custody(plane, name):
    _require_image(plane)
    data = _data()
    if name == "scrna_cadet":
        # Each independently generated NB row represents one biological unit.
        data.obs["donor"] = data.obs["unit"]
    path = plane.input_root / "null.h5ad"
    data.write_h5ad(path)
    reg = _ok(plane, "register_investigation_input", _admission(path))
    params = {
        "scrna_clusterde": {"batch_key": "batch"},
        "scrna_cadet": {"target_feature": "g0", "unit_key": "unit"},
        "scrna_nb_countsplit": {},
    }[name]
    plan = _plan(reg, name, params)
    genes = data.var_names.tolist()
    if name != "scrna_clusterde":
        if name == "scrna_cadet":
            port = "reference"
            evidence = {
                "format": "devbench.cadet-reference/1",
                "null_regime": "independent_nb_features_and_units",
                "feature_ids": genes,
                "mean": [12.0] * len(genes),
                "theta": [4.0] * len(genes),
                "covariance": np.eye(len(genes)).tolist(),
                "origin": "known",
                "source_sha256": hashlib.sha256(
                    b"frozen independent generator"
                ).hexdigest(),
            }
        else:
            port = "split_dispersion"
            evidence = {
                "format": "devbench.nb-split-dispersion/1",
                "analysis_counts_sha256": reg["artifact"]["sha256"],
                "source_sha256": hashlib.sha256(b"generating theta").hexdigest(),
                "source_receipt_sha256": hashlib.sha256(
                    b"frozen independent generator"
                ).hexdigest(),
                "origin": "known",
                "prior_free": True,
                "feature_ids": genes,
                "theta": [4.0] * len(genes),
            }
        config = plane.input_root / (port + ".json")
        from rna_kernels.canonical import canonical_json

        config.write_bytes(canonical_json(evidence))
        plan["input_bindings"][port] = _register(plane, reg, config, "reference_only")
    receipt = _run(plane, plan)
    assert receipt["outputs"]["plot_sidecar"]["kind"] == "plot_sidecar"
    result = json.loads(_result(plane, reg, receipt).read_bytes())
    assert result["status"] == "completed"


def _register_json(plane, reg, name, value, rep):
    path = plane.input_root / (name + ".json")
    path.write_text(json.dumps(value, allow_nan=False))
    return _register(plane, reg, path, rep)


@pytest.mark.parametrize(
    ("cross", "method"), [(False, "pca"), (True, "pca"), (True, "harmony")]
)
def test_qualified_embedding_through_mcp(plane, cross, method):
    from devbench.analysis.checkpoints import digest

    _require_image(plane)
    data = _data()
    data.obs["batch"] = np.where(np.arange(len(data)) % 4 < 2, "b1", "b2")
    if cross:
        data.obs["species_taxon_id"] = np.where(data.obs.batch == "b1", 9606, 10090)
    path = plane.input_root / "query.h5ad"
    data.write_h5ad(path)
    reg = _ok(plane, "register_investigation_input", _admission(path))
    names = data.obs_names.tolist()
    labels = _register_json(
        plane,
        reg,
        "labels",
        dict(zip(names, data.obs.cell_type, strict=True)),
        "metadata_only",
    )
    qualification = _register_json(
        plane,
        reg,
        "qualification",
        {
            "format": "devbench.integration-label-verification/1",
            "actor": "synthetic-reviewer",
            "rationale": "Labels assigned by an independent generator.",
            "label_origin": "independent_assignment",
            "source_sha256": hashlib.sha256(b"independent labels").hexdigest(),
            "labels_sha256": labels["sha256"],
            "selected_sha256": reg["artifact"]["sha256"],
            "cell_axis_sha256": digest(names),
        },
        "reference_only",
    )
    name = "scrna_cross_species_integrate" if cross else "scrna_integrate_qualified"
    args = {"n_pcs": 4, "method": method}
    if cross:
        args["regime"] = "close_mammals"
    plan = _plan(reg, name, args)
    plan["input_bindings"].update(labels=labels, label_qualification=qualification)
    if cross:
        groups = {
            f"o{i}": {"9606": [f"g{i}"], "10090": [f"g{i + 20}"]} for i in range(20)
        }
        groups["o0"]["10090"].append("g40")
        homology = {
            "orthogroups": groups,
            "manifest": {
                "homology_policy": "ensembl_in_paralogs",
                "ensembl_release": "112",
                "assembly_ids": ["GRCh38", "GRCm39"],
                "species_taxon_ids": [9606, 10090],
                "paralog_collapse_rule": "include_in_paralogs_one2many_max_expr",
            },
        }
        plan["input_bindings"]["homology"] = _register_json(
            plane, reg, "homology", homology, "reference_only"
        )
    receipt = _run(plane, plan)
    embedding = receipt["outputs"]["embedding"]
    assert embedding["representation_class"] == "integrated_latent_or_graph"
    info = json.loads(_result(plane, reg, receipt).read_bytes())
    assert info["method"] == method
    assert info["benchmark"]["backend"].startswith("scib-metrics/")
    if cross:
        assert "strict_1to1" in info
    marker = _plan(reg, "scrna_clusterde", {"batch_key": "batch"})
    marker["input_bindings"]["expression"] = {
        k: embedding[k] for k in ("artifact_id", "sha256")
    }
    refusal = dispatch(plane, "freeze_analysis_plan", marker)
    assert (
        refusal.isError
        and refusal.structuredContent["category"] == "scientific_refusal"
    )


@pytest.mark.parametrize("solver", ["marker_panel", "scarches"])
def test_annotation_reference_and_scfm_at_freeze(plane, solver):
    _require_image(plane)
    data = _data()
    data.obs["donor"] = ["d" + str(i % 3) for i in range(len(data))]
    path = plane.input_root / "query.h5ad"
    data.write_h5ad(path)
    reg = _ok(plane, "register_investigation_input", _admission(path))
    data = _data(seed=20261128)
    data.obs_names = ["ref-" + c for c in data.obs_names]
    data.obs["donor"] = "ref-" + data.obs.donor.astype(str)
    atlas = plane.input_root / "atlas.h5ad"
    data.write_h5ad(atlas)
    reference = _register(plane, reg, atlas, "reference_only")
    params = {
        "method": "marker_panel",
        "panels": {"glia": ["g0", "g1"], "neurons": ["g2", "g3"]},
        "label_provenance": "frozen_external_reference",
        "truth_key": "cell_type",
        "calibration_donors": ["d0", "d1"],
    }
    if solver == "scarches":
        params.pop("panels")
        params.update(method=solver, reference_epochs=1, label_epochs=1, query_epochs=1)
    plan = _plan(reg, "scrna_annotate_cells", params)
    plan["input_bindings"]["reference"] = reference
    receipt = _run(plane, plan)
    result = json.loads(_result(plane, reg, receipt).read_bytes())
    assert result["method"] == solver
    assert result["label_provenance"] == "frozen_external_reference"
    assert 0 <= result["abstention_rate"] <= 1 and not result["e5_promoted"]
    for method in ["scarches", "popv", "scGPT"]:
        changed = {**params, "method": method}
        changed.pop("panels", None)
        candidate = _plan(reg, "scrna_annotate_cells", changed)
        candidate["input_bindings"]["reference"] = reference
        candidate["epistemic_purpose"] = "confirmatory"
        assert dispatch(plane, "freeze_analysis_plan", candidate).isError


def test_cross_species_bulk_sample_table_refused(plane):
    counts = plane.input_root / "cohort.csv"
    counts.write_text("gene,S1,S2,S3,S4\ng1,2,7,3,11\ng2,11,5,2,9\n")
    reg = _ok(plane, "register_investigation_input", _admission(counts))
    samples = plane.input_root / "samples.tsv"
    samples.write_text(
        "sample_id\tdonor\tcondition\tspecies_taxon_id\n"
        "S1\tD1\tcontrol\t9606\nS2\tD2\tcontrol\t9606\n"
        "S3\tD3\tcase\t10090\nS4\tD4\tcase\t10090\n"
    )
    params = dict(
        biological_unit="donor",
        biological_unit_column="donor",
        condition_column="condition",
        numerator="case",
        denominator="control",
    )
    plan = _plan(reg, "bulk_de", params)
    plan["input_bindings"]["metadata"] = _register(plane, reg, samples, "metadata_only")
    packet = dispatch(plane, "freeze_analysis_plan", plan)
    assert (
        packet.isError and packet.structuredContent["category"] == "scientific_refusal"
    )


@pytest.mark.parametrize("altered", ["sidecar", "source_digest"])
def test_mutated_homology_receipt_refused(tmp_path, altered):
    from devbench.engine.workflow_operations import OperationBlocked
    from devbench.mcp.registry_m2_wave2 import verify_homology
    from rna_kernels.scrna_cross_species import run_m2
    from tests.test_integration_scib_and_cross_species import fixtures

    paths, _ = fixtures(tmp_path)
    params = {"regime": "close_mammals"}
    outputs = run_m2("scrna_cross_species_integrate", paths, params, tmp_path)
    files = {k: {"path": v} for k, v in outputs.items()}
    names = {k: v.name for k, v in paths.items()}
    verify_homology(tmp_path, names, files, params)
    if altered == "source_digest":
        result = json.loads(outputs["result"].read_bytes())
        result["homology_source_sha256"] = "a" * 64
        outputs["result"].write_text(json.dumps(result))
    else:
        side = json.loads(outputs["plot_sidecar"].read_bytes())
        side["CrossSpeciesHomologyManifest"]["ensembl_release"] = "tampered"
        outputs["plot_sidecar"].write_text(json.dumps(side))
    with pytest.raises(OperationBlocked, match="plan_identity_mismatch"):
        verify_homology(tmp_path, names, files, params)


def test_recoded_supervision_refused_before_training(tmp_path):
    from devbench.analysis.checkpoints import digest
    from rna_kernels.scrna_cross_species import run_m2
    from tests.test_integration_scib_and_cross_species import fixtures

    paths, _ = fixtures(tmp_path)
    data = ad.read_h5ad(paths["expression"])
    labels = json.loads(paths["labels"].read_bytes())
    data.obs["reused"] = ["renamed-" + labels[c] for c in data.obs_names]
    data.write_h5ad(paths["expression"])
    q = json.loads(paths["label_qualification"].read_bytes())
    q.update(
        selected_sha256=hashlib.sha256(paths["expression"].read_bytes()).hexdigest(),
        cell_axis_sha256=digest(list(data.obs_names)),
    )
    paths["label_qualification"].write_text(json.dumps(q))
    with pytest.raises(ValueError, match="Scoring labels"):
        run_m2(
            "scrna_integrate_qualified",
            paths,
            {"training_label_key": "reused", "method": "scanvi"},
            tmp_path,
        )


@pytest.mark.parametrize("key", ["organism_ontology_term_id", "Species"])
def test_pooled_qualified_integration_refused_at_freeze(plane, key):
    from tests.test_integration_scib_and_cross_species import fixtures

    paths, _ = fixtures(plane.input_root)
    data = ad.read_h5ad(paths["expression"])
    data.obs.rename(columns={"species_taxon_id": key}, inplace=True)
    data.write_h5ad(paths["expression"])
    reg = _ok(plane, "register_investigation_input", _admission(paths["expression"]))
    qualification = json.loads(paths["label_qualification"].read_bytes())
    qualification["selected_sha256"] = reg["artifact"]["sha256"]
    plan = _plan(reg, "scrna_integrate_qualified")
    plan["input_bindings"].update(
        labels=_register(plane, reg, paths["labels"], "metadata_only"),
        label_qualification=_register_json(
            plane, reg, "qualification", qualification, "reference_only"
        ),
    )
    refused = dispatch(plane, "freeze_analysis_plan", plan)
    assert (
        refused.isError
        and refused.structuredContent["category"] == "scientific_refusal"
    )
