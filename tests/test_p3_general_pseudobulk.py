"""Synthetic coverage for donor-level mounted pseudobulk preparation."""

import hashlib
import json
import os

import h5py
import numpy as np
import pandas as pd
import pytest
from anndata import AnnData
from scipy import sparse

from devbench.analysis.checkpoints import digest
from devbench.analysis.integration_metrics import ExternalLabelEvidence
from devbench.core.contrast_design import (
    ContrastSpec,
    MountedConditionWeight,
    MountedModelSpec,
)
from devbench.platform.scrna_general_pseudobulk import (
    GeneralPseudobulkSpec,
    register_general_pseudobulk,
    stage_general_pseudobulk,
)
from devharness_core.provenance import canonical_json, hash_artifact


def _sha(path):
    return hash_artifact(path)["sha256"]


def _source(
    root,
    *,
    paired=False,
    missing=False,
    confounded=False,
    bad=False,
    vary_age=False,
    layout="csr",
    filtered_family=False,
    mitochondrial=False,
):
    groups = ("reference", "case") if paired else ("reference", "case", "recovery")
    rows = []
    for group_index, group in enumerate(groups):
        for index in range(4):
            donor = f"donor{index}" if paired else f"donor-{group}-{index}"
            sample = f"specimen-{group}-{index}"
            batch = group if confounded else f"batch{(group_index + index) % 2}"
            age = 34 + 1.25 * index + 0.5 * group_index + (index % 3) * 0.125
            for population in ("P", "N"):
                if missing and (group, index, population) == ("reference", 0, "P"):
                    continue
                size = 2 if index == 1 else 21
                rows.extend(
                    [
                        dict(
                            donor=donor,
                            specimen=sample,
                            group=group,
                            population=population,
                            batch=batch,
                            age=age,
                            batch_alias=batch,
                        )
                    ]
                    * size
                )
    obs = pd.DataFrame(rows, index=[f"cell{i}" for i in range(len(rows))])
    if vary_age:
        obs.loc[obs.index[0], "age"] += 1
    gene_ids = [f"gene{i}" for i in range(48)]
    if mitochondrial:
        gene_ids[0] = "MT-ND1"
    var = pd.DataFrame(index=gene_ids)
    data = np.random.default_rng(732).negative_binomial(24, 0.45, (len(obs), len(var)))
    data[obs["group"].eq("case"), :8] *= 3
    if filtered_family:
        data[:, -2] = 0
        data[obs["group"].eq("reference"), -1] = 0
    if bad:
        data[0, 1] = -1
    adata = AnnData(sparse.csr_matrix(data.shape), obs=obs, var=var)
    adata.layers["counts"] = sparse.csr_matrix(data) if layout == "csr" else data
    path = root / "source.h5ad"
    adata.write_h5ad(path)
    return path, obs, var, data, groups


def _spec(source, groups, *, regime="S-I", comparison="lrt", population="P"):
    return GeneralPseudobulkSpec(
        source_sha256=_sha(source),
        assay="scrna",
        measurement="umi_counts",
        count_provenance="Synthetic original quantification fixture",
        family_id="stage-test",
        regime=regime,
        unit_column="donor",
        specimen_column="specimen",
        condition_column="group",
        population_column="population",
        groups=groups,
        population=population,
        contrast=ContrastSpec(numerator="case", denominator="reference"),
        technical_factors=("batch",),
        model=MountedModelSpec(
            comparison=comparison,
            units="independent" if regime == "S-I" else "paired_fixed",
        ),
    )


def _profiles(staged):
    return pd.read_csv(staged.directory / "metadata.csv")


def test_paired_staging_counts_every_cell_above_legacy_limit(workspace_tmp_path):
    cells_per_profile = 12_501
    groups = ("reference", "case")
    profiles = [(f"donor-{i}", group) for i in range(4) for group in groups]
    n_cells = len(profiles) * cells_per_profile
    obs = pd.DataFrame(
        {
            "donor": np.repeat([donor for donor, _ in profiles], cells_per_profile),
            "group": np.repeat([group for _, group in profiles], cells_per_profile),
            "specimen": np.repeat(
                [f"{donor}-{group}" for donor, group in profiles], cells_per_profile
            ),
            "batch": np.repeat([donor for donor, _ in profiles], cells_per_profile),
            "population": ["P"] * n_cells,
        },
        index=[f"cell-{i}" for i in range(n_cells)],
    )
    adata = AnnData(
        sparse.csr_matrix((n_cells, 2)), obs=obs,
        var=pd.DataFrame(index=["gene-one", "gene-two"]),
    )
    counts = np.column_stack((
        np.ones(n_cells, dtype=np.int32), obs["group"].eq("case"),
    ))
    adata.layers["counts"] = sparse.csr_matrix(counts)
    source = workspace_tmp_path / "large-paired.h5ad"
    adata.write_h5ad(source)

    spec = _spec(source, groups, regime="S-P", comparison="coefficient")
    declared = {**spec.model_dump(), "candidate_aliases": ("batch",)}
    stage = stage_general_pseudobulk(
        source, GeneralPseudobulkSpec.model_validate(declared),
        workspace_tmp_path / "capsule",
    )

    assert stage.pipeline is not None
    assert stage.summary["source_cells"] == n_cells
    assert stage.pipeline.design.technical_factors == ()
    assert len(stage.summary["alias_proofs"]) == len(profiles) // 2 - 1
    assert stage.summary["specification"]["technical_factors"] == ["batch"]
    profile_table = _profiles(stage)
    assert len(profile_table) == len(profiles)
    assert profile_table["cell_count"].eq(cells_per_profile).all()
    with h5py.File(stage.directory / "expression.h5", "r") as handle:
        np.testing.assert_array_equal(
            handle["expression"][:],
            np.vstack((
                profile_table["cell_count"],
                profile_table["condition"].eq("case") * cells_per_profile,
            )),
        )


def test_paired_candidate_alias_cannot_remove_a_condition_confounded_factor(
    workspace_tmp_path,
):
    source, _, _, _, groups = _source(workspace_tmp_path, paired=True, confounded=True)
    spec = _spec(source, groups, regime="S-P", comparison="coefficient")
    declared = {**spec.model_dump(), "candidate_aliases": ("batch",)}
    result = stage_general_pseudobulk(
        source, GeneralPseudobulkSpec.model_validate(declared),
        workspace_tmp_path / "held",
    )
    assert result.pipeline is None
    assert result.summary["status"] == "held"
    assert "alias_proofs" not in result.summary
    assert any("rank" in reason for reason in result.summary["refusals"])


@pytest.mark.parametrize("layout", ["csr", "dense"])
def test_multigroup_stages_exact_observed_profiles_and_keeps_low_cell_counts(
    workspace_tmp_path,
    layout,
):
    source, obs, var, counts, groups = _source(
        workspace_tmp_path, missing=True, layout=layout
    )
    stage = stage_general_pseudobulk(
        source,
        _spec(source, groups),
        workspace_tmp_path / "capsule",
    )
    assert stage.pipeline is not None
    assert stage.pipeline.mounted_model.comparison == "lrt"
    assert stage.pipeline.family.feature_ids == tuple(var.index)
    assert stage.pipeline.design.technical_factors == ("batch",)
    assert stage.summary["missing_profile_count"] == 1
    assert stage.summary["low_cell_profile_count"] == 3
    profiles = _profiles(stage)
    assert len(profiles) == 11
    assert set(profiles["condition"]) == set(groups)
    with h5py.File(stage.directory / "expression.h5", "r") as handle:
        assert handle.attrs["source_sha256"] == _sha(source)
        assert handle.attrs["membership_sha256"] == stage.summary["membership_sha256"]
        assert list(handle["sample_names"].asstr()[:]) == profiles["sample_id"].tolist()
        assert list(handle["gene_names"].asstr()[:]) == list(var.index)
        assert handle["missing_profiles/specimen"].asstr()[:].tolist() == [
            "specimen-reference-0",
        ]
        for index, profile in enumerate(profiles.itertuples(index=False)):
            mask = obs["specimen"].eq(profile.specimen) & obs["population"].eq("P")
            np.testing.assert_array_equal(
                handle["expression"][:, index],
                counts[mask].sum(axis=0),
            )


def test_paired_keeps_distinct_specimens_and_donor_blocks(workspace_tmp_path):
    source, _, _, _, groups = _source(workspace_tmp_path, paired=True)
    stage = stage_general_pseudobulk(
        source,
        _spec(source, groups, regime="S-P", comparison="coefficient"),
        workspace_tmp_path / "paired",
    )
    assert stage.pipeline is not None and stage.pipeline.design.regime == "B-P"
    assert stage.pipeline.mounted_model.units == "paired_fixed"
    profiles = _profiles(stage)
    assert profiles["specimen"].nunique() == len(profiles) == 8
    assert profiles.groupby("donor")["condition"].nunique().tolist() == [2] * 4


def test_weighted_contrast_keeps_verified_numeric_and_categorical_factors(
    workspace_tmp_path,
):
    source, obs, _, _, groups = _source(workspace_tmp_path)
    weights = tuple(
        MountedConditionWeight(level=group, weight=weight)
        for group, weight in zip(groups, (-0.5, 1.0, -0.5), strict=True)
    )
    spec = _spec(source, groups).model_copy(
        update={
            "continuous_covariates": ("age",),
            "model": MountedModelSpec(
                comparison="contrast", units="independent", condition_weights=weights
            ),
        }
    )
    stage = stage_general_pseudobulk(source, spec, workspace_tmp_path / "weighted")
    assert stage.pipeline is not None
    assert stage.pipeline.continuous_covariates == ("age",)
    assert stage.pipeline.mounted_model.condition_weights == weights
    assert stage.summary["design"]["rank"] == 5
    profiles = _profiles(stage)
    ages = obs.groupby("specimen")["age"].first()
    np.testing.assert_array_equal(profiles["age"], profiles["specimen"].map(ages))


def test_aliased_and_within_profile_terms_cannot_be_dropped(workspace_tmp_path):
    source, _, _, _, groups = _source(workspace_tmp_path, vary_age=True)
    spec = _spec(source, groups)
    aliased = stage_general_pseudobulk(
        source,
        spec.model_copy(update={"technical_factors": ("batch", "batch_alias")}),
        workspace_tmp_path / "aliased",
    )
    assert aliased.pipeline is None
    assert any("rank" in reason for reason in aliased.summary["refusals"])
    assert {"batch", "batch_alias"} <= set(_profiles(aliased).columns)
    varying = stage_general_pseudobulk(
        source,
        spec.model_copy(update={"continuous_covariates": ("age",)}),
        workspace_tmp_path / "varying",
    )
    assert varying.pipeline is None
    assert varying.summary["refusals"] == [
        "Nonconstant or missing profile-level factor age"
    ]


@pytest.mark.parametrize(
    ("input_options", "regime", "reason"),
    [
        ({"confounded": True}, "S-I", "rank"),
        ({"paired": True}, "S-I", "independent"),
        ({"paired": True, "missing": True}, "S-P", "pair"),
    ],
)
def test_design_refusal_retains_observed_counts_without_a_fit_plan(
    workspace_tmp_path,
    input_options,
    regime,
    reason,
):
    source, _, _, _, groups = _source(workspace_tmp_path, **input_options)
    stage = stage_general_pseudobulk(
        source,
        _spec(source, groups, regime=regime, comparison="lrt"),
        workspace_tmp_path / "held",
    )
    assert stage.pipeline is None
    assert stage.summary["status"] == "held"
    assert any(reason in value.lower() for value in stage.summary["refusals"])
    assert (stage.directory / "expression.h5").is_file()
    assert not (stage.directory / "pipeline.json").exists()


def _labels(root, source, obs, *, origin="orthogonal_measurement"):
    labels = root / "labels.json"
    labels.write_bytes(canonical_json(obs["population"].to_dict()))
    proof = root / "qualification.json"
    proof.write_bytes(
        canonical_json(
            {
                "format": "devbench.integration-label-verification/1",
                "actor": "synthetic-reviewer",
                "rationale": "Synthetic independent cell identities",
                "label_origin": origin,
                "source_sha256": digest("independent-label-source"),
                "labels_sha256": _sha(labels),
                "selected_sha256": _sha(source),
                "cell_axis_sha256": digest(list(obs.index)),
            }
        )
    )
    return ExternalLabelEvidence(labels, proof, _sha(proof))


def _population_pair_spec(source):
    return GeneralPseudobulkSpec(
        source_sha256=_sha(source),
        assay="scrna",
        measurement="umi_counts",
        count_provenance="Synthetic original quantification fixture",
        family_id="populations",
        regime="P-P",
        unit_column="donor",
        specimen_column="specimen",
        condition_column="group",
        population_column="population",
        groups=("P", "N"),
        condition="reference",
        contrast=ContrastSpec(numerator="N", denominator="P"),
        model=MountedModelSpec(comparison="coefficient", units="paired_fixed"),
    )


def test_population_pairs_require_qualified_source_bound_labels(workspace_tmp_path):
    source, obs, _, _, _ = _source(workspace_tmp_path, paired=True)
    spec = _population_pair_spec(source)
    destination = workspace_tmp_path / "population-pairs"
    with pytest.raises(ValueError, match="label_qualification_required"):
        stage_general_pseudobulk(source, spec, destination)
    same_rna = _labels(
        workspace_tmp_path,
        source,
        obs,
        origin="expression_based_study_annotation",
    )
    with pytest.raises(ValueError, match="label_origin_unqualified"):
        stage_general_pseudobulk(source, spec, destination, label_evidence=same_rna)
    wrong_obs = obs.assign(population=obs["population"].map({"P": "N", "N": "P"}))
    wrong_labels = _labels(workspace_tmp_path, source, wrong_obs)
    with pytest.raises(ValueError, match="differ from the supplied observation axis"):
        stage_general_pseudobulk(source, spec, destination, label_evidence=wrong_labels)
    evidence = _labels(workspace_tmp_path, source, obs)
    stage = stage_general_pseudobulk(source, spec, destination, label_evidence=evidence)
    assert stage.pipeline is not None and stage.pipeline.design.regime == "B-P"
    assert stage.summary["label_qualification"]["qualification_sha256"] == (
        evidence.qualification_sha256
    )
    profiles = _profiles(stage)
    assert len(profiles) == 8
    assert profiles.groupby("donor")["specimen"].nunique().tolist() == [1] * 4
    assert set(profiles["condition"]) == {"P", "N"}


def test_corrupt_count_or_source_hash_never_publishes(workspace_tmp_path):
    source, _, _, _, groups = _source(workspace_tmp_path, bad=True)
    spec = _spec(source, groups)
    destination = workspace_tmp_path / "invalid"
    with pytest.raises(ValueError, match="nonnegative integers"):
        stage_general_pseudobulk(source, spec, destination)
    with pytest.raises(ValueError, match="source_sha256"):
        stage_general_pseudobulk(
            source,
            spec.model_copy(update={"source_sha256": "0" * 64}),
            destination,
        )
    assert not destination.exists()


def _tier_a_envelope(flow, executor, parameters, inputs, regime, *, primary=True):
    from devbench.platform.adaptive_workflow_state import AdaptiveStep

    for artifact in inputs.values():
        flow.register_artifact(artifact)
    step = AdaptiveStep(
        step_id="pseudobulk-fit",
        branch_id="pseudobulk",
        operation_id="bulk_condition_contrast",
        regime=regime,
        primary=primary,
        comparison=not primary,
        parameters_sha256=parameters,
        environment_sha256=executor.environment_sha256,
        purpose="admitted_research_inference",
        profile={
            "profile_id": "synthetic-pseudobulk",
            "version": 1,
            "operations": [
                {
                    "operation_id": "bulk_condition_contrast",
                    "output_kinds": ["table"],
                    "max_memory_mb": 2048,
                    "timeout_seconds": 60,
                }
            ],
            "input_representations": ["raw_counts_h5", "metadata_csv"],
            "biological_units": ["donor"],
            "design_scope": [regime],
            "permitted_claims": ["admitted_research_inference"],
        },
        inputs=[
            {
                "port": {"name": name, "artifact_kind": artifact.kind},
                "source_id": artifact.version_id,
            }
            for name, artifact in inputs.items()
        ],
        outputs=[{"name": "result", "artifact_kind": "table"}],
    )
    flow.insert(step)
    return {
        "mode": "adaptive/1",
        "lineage_id": flow.lineage_id,
        "actor": "fixture",
        "generation": 1,
        "basis": {},
        "environment_sha256": executor.environment_sha256,
        "design_review": {"research_admission": {"fixture": "trusted gate fixture"}},
        "step": step.model_dump(mode="json", exclude={"oci_method_version_id"}),
        "inputs": {
            name: artifact.model_dump(mode="json") for name, artifact in inputs.items()
        },
        "pins": [
            {
                "port": name,
                "version_id": artifact.version_id,
                "sha256": artifact.sha256,
                "graph_sha256": artifact.graph_sha256,
            }
            for name, artifact in sorted(inputs.items())
        ],
    }


@pytest.mark.wal
@pytest.mark.parametrize("paired", [False, True])
def test_pseudobulk_plan_registers_and_runs_the_mounted_lrt(workspace_tmp_path, paired):
    import pyarrow.parquet as pq

    from devbench.engine.tier_a_executor import TierAExecutor
    from tests.test_investigation_invalidation import make
    from tests.test_tier_a_executor import _submit

    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if not image:
        pytest.skip("Set the exact local RNA kernel image ID")
    source, _, _, _, groups = _source(workspace_tmp_path, paired=paired)
    staged = stage_general_pseudobulk(
        source,
        _spec(source, groups, regime="S-P" if paired else "S-I"),
        workspace_tmp_path / "capsule",
    )
    flow = make(workspace_tmp_path)
    executor = TierAExecutor(
        flow.ledger, mounted_image_id=image, allow_data_execution=True
    )
    parameters_sha256, inputs = register_general_pseudobulk(executor, staged)
    evidence = executor.mounted_design_preflight(parameters_sha256)
    assert evidence["metrics"]["declared_unit_count"] == (4 if paired else 12)
    assert evidence["metrics"]["columns"] == (6 if paired else 4)
    assert evidence["scientific_validity_established"] is False
    envelope = _tier_a_envelope(
        flow, executor, parameters_sha256, inputs, "B-P" if paired else "B-I"
    )
    token, receipt = _submit(flow, executor, envelope)
    assert receipt.status == "succeeded", receipt.reason
    assert executor.load_receipt(envelope, attempt_id=token["attempt_id"]) == receipt
    io_record = executor.runs / token["attempt_id"] / "io.json"
    method = json.loads(io_record.read_bytes())["tool"]["method"]
    assert method["solver"] == "edger_v4" and method["test"] == "lrt"
    assert method["test_df"] == (1 if paired else 2)
    with executor.open_output(receipt.outputs["result"]) as stream:
        table = pq.read_table(stream)
    assert table.num_rows == 48
    probabilities, adjusted = table["p_value"].to_numpy(), table["q_value"].to_numpy()
    assert np.all((0 <= probabilities) & (probabilities <= adjusted) & (adjusted <= 1))
    with executor.training.open(staged.provenance_sha256) as stream:
        manifest = json.load(stream)
    assert manifest["source_sha256"] == _sha(source)


def _voom_spec(source, groups, paired):
    weights = (
        () if paired else tuple(
            MountedConditionWeight(level=group, weight=weight)
            for group, weight in zip(groups, (-1.0, 1.0, 0.0), strict=True)
        )
    )
    return _spec(source, groups, regime="S-P" if paired else "S-I").model_copy(
        update={"model": MountedModelSpec(
            comparison="coefficient" if paired else "contrast",
            units="paired_fixed" if paired else "independent",
            condition_weights=weights,
            sensitivity_method="limma_voom",
        )}
    )


def test_pseudobulk_voom_freezes_a_nonprimary_branch(workspace_tmp_path):
    from devbench.core.contrast_design import PipelineSpec

    source, _, _, _, groups = _source(workspace_tmp_path)
    legacy_model = _spec(source, groups).model.model_dump(mode="json")
    assert "sensitivity_method" not in legacy_model
    staged = stage_general_pseudobulk(
        source, _voom_spec(source, groups, False), workspace_tmp_path / "capsule",
    )
    plan = staged.pipeline
    assert plan is not None, staged.summary["refusals"]
    assert plan.family.primary is False
    assert plan.mounted_model is not None
    assert plan.mounted_model.request()["sensitivity_method"] == "limma_voom"
    family = plan.family.model_copy(update={"primary": True})
    with pytest.raises(ValueError, match="limma-voom sensitivity"):
        PipelineSpec.model_validate(plan.model_copy(update={"family": family}))
    with pytest.raises(ValueError, match="pinned limma-voom runtime"):
        plan.require_mounted_runtime(
            "sha256:" + "f" * 64, representation="raw_counts_h5",
        )


def test_pseudobulk_voom_refuses_an_incomplete_donor_pair(workspace_tmp_path):
    source, _, _, _, groups = _source(workspace_tmp_path, paired=True, missing=True)
    staged = stage_general_pseudobulk(
        source, _voom_spec(source, groups, True), workspace_tmp_path / "capsule",
    )
    assert staged.pipeline is None
    assert any("incomplete donor pair" in row for row in staged.summary["refusals"])


@pytest.mark.wal
@pytest.mark.parametrize("paired", [False, True])
def test_pseudobulk_voom_retains_checked_sensitivity_receipt(
    workspace_tmp_path, paired, monkeypatch,
):
    import pyarrow.parquet as pq
    from scipy.stats import false_discovery_control

    from devbench.engine.tier_a_executor import TierAExecutor
    from devharness_controlled.native_contracts import limma_voom_kernel_images
    from tests.test_investigation_invalidation import make
    from tests.test_tier_a_executor import _submit

    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if not image:
        pytest.skip("Set the exact local limma-voom RNA kernel image ID")
    if image not in limma_voom_kernel_images():
        pytest.skip("Select the pinned limma-voom RNA kernel image")
    source, _, _, _, groups = _source(
        workspace_tmp_path, paired=paired, filtered_family=True,
    )
    staged = stage_general_pseudobulk(
        source, _voom_spec(source, groups, paired), workspace_tmp_path / "capsule",
    )
    flow = make(workspace_tmp_path)
    executor = TierAExecutor(
        flow.ledger, mounted_image_id=image, allow_data_execution=True,
    )
    parameters, inputs = register_general_pseudobulk(executor, staged)
    envelope = _tier_a_envelope(
        flow, executor, parameters, inputs, "B-P" if paired else "B-I", primary=False,
    )
    token, receipt = _submit(flow, executor, envelope)
    assert receipt.status == "succeeded", receipt.reason
    assert executor.load_receipt(envelope, attempt_id=token["attempt_id"]) == receipt
    record = executor.runs / token["attempt_id"] / "io.json"
    method = json.loads(record.read_bytes())["tool"]["method"]
    assert (method["solver"], method["test"], method["test_df"]) == (
        "limma_voom", "moderated_t", 1,
    )
    assert method["analysis_role"] == "nonprimary_sensitivity"
    assert method["normalization"] == "full_family_RLE"
    with executor.open_output(receipt.outputs["result"]) as stream:
        table = pq.read_table(stream)
    assert table.num_rows == 48
    p = table["p_value"].to_numpy()
    q = table["q_value"].to_numpy()
    assert table["estimate_status"].to_pylist()[-2:] == ["filtered", "failed"]
    assert np.isnan(p[-2:]).all() and np.isnan(q[-2:]).all()
    fitted = p[:-2]
    expected = false_discovery_control(np.append(fitted, 1.0))[:-1]
    np.testing.assert_allclose(q[:-2], expected, atol=1e-14)
    assert np.all((0 <= fitted) & (fitted <= q[:-2]) & (q[:-2] <= 1))
    assert np.median(table["effect"].to_numpy()[:8]) > 1
    if not paired:
        monkeypatch.setattr(flow, "_review_basis", lambda *_: envelope["design_review"])
        result = flow.complete(token, receipt, verify=executor.verify)
        assert result["status"] == "succeeded", result
        _replay_voom_comparison(workspace_tmp_path, flow, executor, receipt)


def _replay_voom_comparison(root, flow, executor, receipt):
    from devbench.analysis.thesis_replay import replay_thesis_package
    from devbench.analysis.thesis_synthesis import (
        ThesisSynthesisSpec,
        write_thesis_package,
    )

    schema = root / "answer_schema.json"
    schema.write_bytes(canonical_json({"type": "object"}))
    base = {
        "question": "How sensitive is the prespecified donor contrast?",
        "outcome": "partial",
        "expected_revision": flow.status()["revision"],
        "answer_schema_sha256": hashlib.sha256(schema.read_bytes()).hexdigest(),
    }
    options = {
        "answer_schema": schema,
        "open_artifact": executor.open_output,
        "tier_a_executor": executor,
    }
    invalid = ThesisSynthesisSpec.model_validate({
        **base,
        "fields": {"gene_statistics": {
            "step_id": "pseudobulk-fit", "port": "result",
            "projection": "tier_a_gene_statistics",
        }},
    })
    with pytest.raises(ValueError, match="Primary gene statistics"):
        write_thesis_package(root / "invalid", flow, invalid, **options)
    package = root / "comparison"
    spec = ThesisSynthesisSpec.model_validate(base)
    sealed = write_thesis_package(package, flow, spec, **options)
    replay = replay_thesis_package(
        package, sealed["root_sha256"], output=root / "replayed",
        input_root=executor.training.root, allow_data_execution=True,
    )
    assert replay["exact_genes_parquet_match"]
    assert replay["steps"] == {"pseudobulk-fit": receipt.outputs["result"].sha256}


@pytest.mark.wal
def test_register_refuses_changed_capsule_before_registering_pipeline(
    workspace_tmp_path,
):
    from devbench.engine.tier_a_executor import TierAExecutor
    from tests.test_investigation_invalidation import make

    source, _, _, _, groups = _source(workspace_tmp_path)
    staged = stage_general_pseudobulk(
        source,
        _spec(source, groups),
        workspace_tmp_path / "capsule",
    )
    metadata = staged.directory / "metadata.csv"
    metadata.chmod(0o600)
    metadata.write_bytes(metadata.read_bytes() + b"changed.csv\n")
    executor = TierAExecutor(make(workspace_tmp_path).ledger)
    with pytest.raises(ValueError, match="artifact changed: metadata.csv"):
        register_general_pseudobulk(executor, staged)
    assert not list(executor.training.root.iterdir())
