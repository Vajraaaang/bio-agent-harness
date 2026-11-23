"""Offline checks for the bounded adaptive bulk pipeline proposal."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from devbench_engine.adapters import to_openai_strict_schema
from devbench_engine.lifecycle import strict_schema_compatible


def _proposal():
    return {
        "kind": "propose_pipeline_step",
        "step_id": "case-control-fit",
        "branch_id": "bulk-research",
        "expression_version_id": "expression-v1",
        "metadata_version_id": "metadata-v1",
        "family_version_id": "family-v1",
        "design": {"regime": "B-I", "technical_factors": ["capture_batch"]},
        "contrast": {"numerator": "case", "denominator": "control"},
        "metadata_roles": {
            "condition": "condition",
            "biological_unit": "donor_id",
            "specimen": "specimen_id",
            "library": "library_id",
        },
        "rationale": "Evaluate the case-control comparison on the frozen family.",
        "evidence_ids": [],
    }


def test_pipeline_proposal_is_a_compact_strict_controller_action():
    from devbench.analysis.method_planner_contract import (
        ADAPTIVE_CONTROLLER_SCHEMA,
        validate_adaptive_controller_decision,
    )

    action = _proposal()
    assert strict_schema_compatible(ADAPTIVE_CONTROLLER_SCHEMA)
    projection = to_openai_strict_schema(ADAPTIVE_CONTROLLER_SCHEMA)
    Draft202012Validator(projection).validate({"action": action})
    assert validate_adaptive_controller_decision({"action": action}) == action
    for field, value in (("feature_ids", ["g1"]), ("formula", "~ arbitrary")):
        invalid = deepcopy(action)
        invalid["design"][field] = value
        with pytest.raises(ValueError):
            validate_adaptive_controller_decision(invalid)


def test_binary_family_axis_retains_legacy_erql_limit():
    from devbench.core.publication_families import FamilyAllocation, FamilySpec

    family = FamilySpec(
        family_id="measured",
        population="bulk",
        numerator="case",
        denominator="reference",
        feature_ids=tuple(f"g{i}" for i in range(20_000)),
    )
    assert len(family.feature_ids) == 20_000
    with pytest.raises(ValueError, match="Historical ERQL allocation"):
        FamilyAllocation(families=(family,))


@pytest.mark.wal
@pytest.mark.parametrize(
    ("primary", "mode"),
    [
        (True, "legacy"),
        (False, "legacy"),
        (True, "mounted"),
        (False, "mounted"),
        (True, "tximport"),
    ],
)
def test_compiler_retains_complete_family_only_in_the_content_store(
    workspace_tmp_path,
    primary,
    mode,
):
    from devbench.analysis.episode_agent import AdaptiveController
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
    from devbench.core.contrast_design import PipelineSpec
    from devbench.core.protected_partitions import PartitionVault
    from devbench.core.publication_families import FamilySpec
    from devbench.engine.partition_authority import PartitionAuthority
    from devbench.engine.workflow_operations import OperationConflict
    from devbench.platform.adaptive_workflow import ArtifactVersion
    from devbench.study.contracts import content_sha256
    from devharness_core.provenance import canonical_json
    from devharness_server.tools_bulk import validate_bulk_contrast_parameters
    from tests.test_investigation_invalidation import digest, make

    workflow = make(workspace_tmp_path)
    broker = EvidenceBroker(workflow.authority)
    policy = broker.register_policy(AccessPolicy("compiler", 1))
    broker.register_context(
        "compiler-context",
        principal_id="compile-user",
        lineage_id=workflow.lineage_id,
        role="controller",
        phase="analysis",
        policy_sha256=policy,
    )
    store = PartitionVault(workspace_tmp_path / "objects")
    family = FamilySpec(
        family_id="case-control",
        population="bulk",
        numerator="case",
        denominator="control",
        feature_ids=tuple(f"feature-{index:06d}" for index in range(8192)),
        primary=primary,
        weight="1" if primary else "0",
    )
    family_bytes = canonical_json(family.model_dump(mode="json"))
    auxiliary = ()
    earlier_tables = ()
    if mode == "tximport":
        auxiliary = (
            ("lengths-v1", b"effective length matrix", "table", "effective_lengths_h5"),
        )
        earlier_tables = tuple(
            (f"candidate-{i:02}", f"metadata-{i}".encode(), "table", "metadata_csv")
            for i in range(19)
        )
    for identity, raw, kind, representation in (
        (
            "expression-v1",
            b"registered expression fixture",
            "continuous_matrix" if mode == "mounted" else "counts",
            {"mounted": "continuous_h5ad", "tximport": "tximport_counts_h5"}.get(
                mode, "COUNT-1"
            ),
        ),
        ("metadata-v1", b"registered metadata fixture", "table", "metadata_csv"),
        ("family-v1", family_bytes, "table", "family_spec_json"),
        *auxiliary,
        *earlier_tables,
    ):
        sha256 = store.put(raw)
        workflow.register_artifact(
            ArtifactVersion(
                version_id=identity,
                sha256=sha256,
                kind=kind,
                representation=representation,
                biological_unit="donor",
                unit_membership_sha256=digest("declared units"),
                measurement_history=("raw",) if kind == "counts" else (),
                permitted_uses=("admitted_research_inference",),
            )
        )
        broker.register_source(
            identity,
            content_sha256=sha256,
            roots=(digest("cohort root"),),
            classification="structural",
        )

    def register_pipeline(spec):
        return store.put(canonical_json(spec.model_dump(mode="json")))

    executor = SimpleNamespace(
        ledger=workflow.ledger,
        training=store,
        environment_sha256=digest("local environment"),
        mounted_image_id="sha256:" + "1" * 64,
        register_pipeline=register_pipeline,
    )
    controller = object.__new__(AdaptiveController)
    controller.invoker = SimpleNamespace(session_id=workflow.lineage_id)
    controller.broker = broker
    controller.context_id = "compiler-context"
    controller.action_catalog = {}
    action = _proposal()
    if mode == "mounted":
        action["design"].update(
            continuous_covariates=["age"], expression_layer="normalized"
        )
    if mode == "tximport":
        action["design"].update(
            continuous_covariates=["age"],
            expression_layer=None,
            model={
                "comparison": "lrt",
                "units": "independent",
                "condition_weights": [],
                "reduced_terms": ["age", "capture_batch"],
                "lengths_version_id": "lengths-v1",
                "latent_method": None,
                "sv_method": None,
                "n_factors": None,
                "controls_version_id": None,
            },
        )
    expected_auxiliary = {row[0] for row in auxiliary}
    expected_versions = {
        "expression-v1",
        "metadata-v1",
        "family-v1",
        *expected_auxiliary,
        *(row[0] for row in earlier_tables),
    }
    revision = workflow.status()["revision"]
    candidates = controller.pipeline_input_candidates(
        workflow,
        expected_revision=revision,
    )
    assert {row["artifact"]["version_id"] for row in candidates} == expected_versions
    assert all(row["artifact"]["sha256"] in row["dependencies"] for row in candidates)
    assert all(len(canonical_json(row)) < 1024 for row in candidates)

    step = controller.compile_pipeline_step(
        workflow,
        action,
        executor=executor,
        expected_revision=revision,
    )
    assert step.purpose == "admitted_research_inference"
    assert step.primary is primary
    assert step.comparison is (not primary)
    assert step.oci_method_version_id is None
    assert {link.source_id for link in step.inputs} == {
        "expression-v1",
        "metadata-v1",
        *expected_auxiliary,
    }
    assert controller.action_catalog == {}
    with store.open(step.parameters_sha256) as handle:
        body = handle.read()
    assert len(body) > 65_536
    spec = PipelineSpec.model_validate_json(body)
    assert spec.family == family
    assert content_sha256(spec.model_dump(mode="json")) == step.parameters_sha256
    if mode == "mounted":
        assert spec.format == "devbench.tier-a-bulk-pipeline/2"
        assert spec.continuous_covariates == ("age",)
        assert spec.expression_layer == "normalized"
    elif mode == "tximport":
        assert spec.measurement == "tximport_estimated_counts"
        assert spec.mounted_model.artifact_digests() == {
            "lengths": digest_from_candidates(candidates, "lengths-v1"),
        }
        assert spec.mounted_model.comparison == "lrt"
    else:
        assert "continuous_covariates" not in spec.model_dump(mode="json")
        public_task, options = validate_bulk_contrast_parameters(*spec.tool_request())
        assert public_task["gene_ids"] == list(family.feature_ids)
        assert options["batch_cols"] == ["capture_batch"]
    assert len(canonical_json(step.model_dump(mode="json"))) < 4096
    workflow.insert(step)
    assert workflow.projection_snapshot()["steps"][0]["step_id"] == step.step_id
    with pytest.raises(OperationConflict, match="evidence changed"):
        controller.compile_pipeline_step(
            workflow,
            action,
            executor=executor,
            expected_revision=revision,
        )
    invalid = deepcopy(action)
    invalid["contrast"] = {"numerator": "control", "denominator": "case"}
    with pytest.raises(ValueError, match="same directed contrast"):
        controller.compile_pipeline_step(workflow, invalid, executor=executor)

    partition = PartitionAuthority(workflow.ledger, digest("protected cohort"))
    partition.protect_outputs([spec.family_sha256])
    offered = controller.pipeline_input_candidates(workflow)
    assert {row["artifact"]["version_id"] for row in offered} == (
        expected_versions - {"family-v1"}
    )
    with pytest.raises(PermissionError, match="distinct admitted original"):
        controller.compile_pipeline_step(workflow, action, executor=executor)


def digest_from_candidates(candidates, version_id):
    return next(
        row["artifact"]["sha256"]
        for row in candidates
        if row["artifact"]["version_id"] == version_id
    )
