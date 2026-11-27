"""Governed synthetic bulk L1/L2 acceptance on the pinned mounted runtime."""

import csv
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import h5py
import numpy as np
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError

from devbench.analysis.episode_agent import AdaptiveController
from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
from devbench.analysis.rna_diagnostic_policy import DiagnosticQualificationAuthority
from devbench.analysis.scientific_review_holds import ReviewEntity, ReviewHoldLifecycle
from devbench.core.contrast_design import (
    ContrastSpec,
    DesignSpec,
    MountedModelSpec,
    PipelineSpec,
)
from devbench.core.publication_families import FamilySpec
from devbench.engine.tier_a_executor import TierAExecutor
from devbench.platform.adaptive_workflow import AdaptiveStep, ArtifactVersion
from devbench.platform.workflow_actions import WorkflowActions
from devharness_core.provenance import canonical_json, hash_artifact
from tests.test_admitted_research_inference import _cycle
from tests.test_investigation_invalidation import make
from tests.test_review_hold_lifecycle import binding

_RESEARCH = "admitted_research_inference"


@dataclass(frozen=True)
class _Cohort:
    expression: Path
    metadata: Path
    lengths: Path | None
    gene_ids: tuple[str, ...]
    rows: tuple[dict[str, str], ...]


@pytest.fixture
def pinned_bulk_image():
    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if not image:
        pytest.skip("Set the exact local RNA kernel image ID")
    return image


def _sha(path):
    return hash_artifact(path)["sha256"]


def _digest(value):
    from devbench.core.population_contract import content_sha256

    return content_sha256(value)


def _cohort_rows(paired):
    assignments = (
        [(donor, (donor + arm) % 2, arm) for donor in range(8) for arm in range(2)]
        if paired
        else [
            (batch * 4 + arm * 2 + j, batch, arm)
            for batch in range(3)
            for arm in range(2)
            for j in range(2)
        ]
    )
    return tuple(
        {
            "sample_id": f"sample-{i:02d}",
            "donor": f"donor-{donor:02d}",
            "specimen": f"specimen-{i:02d}",
            "library": f"library-{i:02d}",
            "condition": "case" if arm else "control",
            "batch": f"batch-{batch}",
        }
        for i, (donor, batch, arm) in enumerate(assignments)
    )


def _write_h5(path, values, genes, rows):
    strings = h5py.string_dtype("utf-8")
    with h5py.File(path, "x") as handle:
        handle.create_dataset("gene_names", data=genes, dtype=strings)
        handle.create_dataset(
            "sample_names", data=[row["sample_id"] for row in rows], dtype=strings
        )
        handle.create_dataset(
            "expression", data=values, chunks=(min(256, len(genes)), len(rows))
        )


def _make_cohort(root, n_genes, *, paired=False, latent=False):
    rows = _cohort_rows(paired)
    genes = tuple(f"gene{i:05d}" for i in range(n_genes))
    cases = np.array([row["condition"] == "case" for row in rows])
    first_batch = np.array([row["batch"] == "batch-0" for row in rows])
    rng = np.random.default_rng(2914)
    values = rng.negative_binomial(60, 0.44, (n_genes, len(rows)))
    values[:30, cases] *= 4
    values[90:130, first_batch] *= 2
    values[-1] = 0
    if latent:
        if paired:
            # Fixed donor, batch and condition columns are orthogonal to this signal.
            amplitude = [1.45, 1.2, 0.25, -1.45, -1.1, 0.6, -0.6, -0.35]
            score = np.repeat(amplitude, 2) * np.tile([1, -1], 8)
            values = values.astype(float)
            values[120:600] *= np.exp(1.8 * score)
        else:
            alternating = np.arange(len(rows)) % 2 == 0
            values[150:250, alternating] *= 4
    lengths = None
    if paired:
        lengths = root / "lengths.h5"
        measured = rng.uniform(650, 950, values.shape)
        measured[30:90, cases] *= 3
        values = values * (measured / 800)
        _write_h5(lengths, measured, genes, rows)
    expression = root / "expression.h5"
    _write_h5(expression, values, genes, rows)
    metadata = root / "metadata.csv"
    with metadata.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return _Cohort(expression, metadata, lengths, genes, rows)


def _pipeline(cohort, *, family_id, latent=False):
    paired = cohort.lengths is not None
    model = MountedModelSpec(
        comparison="coefficient",
        units="paired_fixed" if paired else "independent",
        lengths_sha256=_sha(cohort.lengths) if paired else None,
        latent_method="svaseq" if latent else None,
        sv_method="leek" if latent else None,
    )
    family = FamilySpec(
        family_id=family_id,
        population="bulk",
        feature_ids=cohort.gene_ids,
        numerator="case",
        denominator="control",
    )
    return PipelineSpec(
        format="devbench.tier-a-bulk-pipeline/2",
        design=DesignSpec(
            "B-P" if paired else "B-I", "case", "control", technical_factors=("batch",)
        ),
        contrast=ContrastSpec(numerator="case", denominator="control"),
        family=family,
        biological_unit="donor",
        measurement="tximport_estimated_counts" if paired else "raw_counts",
        condition_column="condition",
        population_unit_column="donor",
        specimen_column="specimen",
        library_column="library",
        expression_sha256=_sha(cohort.expression),
        metadata_sha256=_sha(cohort.metadata),
        family_sha256=_digest(family.model_dump(mode="json")),
        mounted_model=model,
    )


def _register_inputs(flow, executor, spec, cohort):
    family = canonical_json(spec.family.model_dump(mode="json"))
    assert executor.training.put(family) == spec.family_sha256
    measurement = spec.measurement
    representations = {
        "expression": ("tximport_counts_h5" if cohort.lengths else "raw_counts_h5"),
        "metadata": "metadata_csv",
        "lengths": "effective_lengths_h5",
    }
    paths = {"expression": cohort.expression, "metadata": cohort.metadata}
    expected = {"expression": spec.expression_sha256, "metadata": spec.metadata_sha256}
    if cohort.lengths:
        paths["lengths"] = cohort.lengths
        expected["lengths"] = spec.mounted_model.lengths_sha256
    result = {}
    for port, path in paths.items():
        identity = executor.training.put_file(path)
        assert identity == expected[port]
        artifact = ArtifactVersion(
            version_id=f"input-{port}",
            sha256=identity,
            kind="counts" if port == "expression" else "table",
            representation=representations[port],
            biological_unit="donor",
            unit_membership_sha256=_digest(cohort.rows),
            measurement_history=(measurement,) if port == "expression" else (),
            permitted_uses=("checked_observation", _RESEARCH),
        )
        flow.register_artifact(artifact)
        result[port] = artifact
    return result


def _insert_fit(flow, executor, spec, artifacts, *, step_id="fit"):
    parameters = executor.register_pipeline(spec)
    node = AdaptiveStep(
        step_id=step_id,
        branch_id="primary",
        operation_id="bulk_condition_contrast",
        regime=spec.design.regime,
        primary=True,
        purpose=_RESEARCH,
        parameters_sha256=parameters,
        environment_sha256=executor.environment_sha256,
        profile={
            "profile_id": "thesis-synthetic-bulk",
            "version": 1,
            "operations": [
                {
                    "operation_id": "bulk_condition_contrast",
                    "output_kinds": ["table"],
                    "max_memory_mb": 2048,
                    "timeout_seconds": 120,
                }
            ],
            "input_representations": [
                item.representation for item in artifacts.values()
            ],
            "biological_units": ["donor"],
            "design_scope": [spec.design.regime],
            "permitted_claims": [_RESEARCH],
        },
        inputs=[
            {
                "port": {"name": port, "artifact_kind": item.kind},
                "source_id": item.version_id,
            }
            for port, item in artifacts.items()
        ],
        outputs=[{"name": "result", "artifact_kind": "table"}],
    )
    flow.insert(node)
    return executor.mounted_design_preflight(parameters)


def _research_actions(
    flow,
    executor,
    artifacts,
    preflight,
    *,
    step_ids=("fit",),
    suffix="",
):
    broker = EvidenceBroker(flow.authority)
    policy = broker.register_policy(AccessPolicy("thesis-synthetic", 1))
    context_id = "controller" + suffix
    broker.register_context(
        context_id,
        principal_id=context_id,
        lineage_id=flow.lineage_id,
        role="controller",
        phase="analysis",
        policy_sha256=policy,
    )
    for port, artifact in artifacts.items():
        source_id = "input-" + port + suffix
        broker.register_source(
            source_id,
            content_sha256=artifact.sha256,
            roots=(artifact.sha256,),
            classification="structural",
        )
        broker.delivery(context_id, source_id).release({"input_role": port})
    lifecycle = ReviewHoldLifecycle(flow.authority, flow.lineage_id)
    lifecycle.register_entities(
        tuple(ReviewEntity(entity_id=step_id, kind="fit") for step_id in step_ids)
    )
    evidence = preflight["sha256"]
    lifecycle.register_evidence(
        "checked-design" + suffix, evidence, relevant_entity_ids=step_ids
    )
    for touchpoint in ("design", "publication"):
        _cycle(
            lifecycle,
            broker,
            touchpoint,
            policy,
            entity_ids=step_ids,
            suffix=suffix,
        )

    def verify_fixture(subject):
        assert preflight["metrics"]["estimable"] is True
        assert preflight["scientific_validity_established"] is False
        return {
            "format": "devbench.rna-research-admission/1",
            "subject": subject,
            "acceptance": "independently_accepted",
            "checks": {"Q-DESIGN": {"outcome": "pass", "evidence_sha256": evidence}},
        }

    controller = object.__new__(AdaptiveController)
    controller.broker = broker
    controller.context_id = context_id
    controller.planning_source_id = None
    return WorkflowActions(
        None,
        flow.ledger,
        actor="synthetic-operator",
        selector=None,
        controller=controller,
        allow_data_execution=True,
        tier_a_executor=executor,
        review_lifecycle=lifecycle,
        review_binding=lambda _: binding(),
        diagnostic_qualification=DiagnosticQualificationAuthority(verify_fixture),
    )


@dataclass(frozen=True)
class _Fit:
    flow: object
    executor: TierAExecutor
    spec: PipelineSpec
    result: dict
    tool: dict
    artifact: ArtifactVersion
    rows: list[dict]
    preflight: dict


def _fit(root, image, cohort, spec):
    root.mkdir()
    flow = make(root)
    executor = TierAExecutor(
        flow.ledger, mounted_image_id=image, allow_data_execution=True
    )
    artifacts = _register_inputs(flow, executor, spec, cohort)
    preflight = _insert_fit(flow, executor, spec, artifacts)
    actions = _research_actions(flow, executor, artifacts, preflight)
    result = actions.perform_adaptive(flow, "fit")
    assert result["status"] == "succeeded", result["reason"]
    assert len(flow.authority.attempts(flow.lineage_id)) == 1
    artifact = flow.current_use("fit", "result", purpose="query", use="claim")
    with executor.open_output(artifact) as stream:
        rows = pq.read_table(stream).to_pylist()
    record = executor.runs / result["attempt_id"] / "io.json"
    tool = json.loads(record.read_bytes())["tool"]
    assert tool["oci"]["image_id"] == image and tool["oci"]["cleanup_confirmed"]
    return _Fit(flow, executor, spec, result, tool, artifact, rows, preflight)


def _assert_family(fit, gene_ids):
    assert len(fit.rows) == len(gene_ids)
    assert _digest([row["gene_id"] for row in fit.rows]) == _digest(gene_ids)
    for row in fit.rows:
        if row["estimate_status"] == "estimated":
            assert 0 <= row["p_value"] <= row["q_value"] <= 1
            assert np.isfinite(row["effect"])
        else:
            assert row["effect"] is row["p_value"] is row["q_value"] is None
            assert row["missing_reason"]
    assert fit.rows[-1]["estimate_status"] != "estimated"
    assert fit.rows[-1]["values"] == []


def _public_answer(fit):
    from devbench.study.biology_answers import HypothesisEstimate

    fields = HypothesisEstimate.model_fields
    rows = [
        {key: value for key, value in row.items() if key in fields} for row in fit.rows
    ]
    for row in rows:
        if row["estimate_status"] == "filtered":
            row["estimate_status"] = "not_tested"
    payload = {
        "kind": "effects",
        "analysis_performed": True,
        "gene_statistics": rows,
        "multiplicity_scope": [
            row["hypothesis_id"] for row in rows if row["p_value"] is not None
        ],
        "selection_conditional_inference": False,
        "biological_unit": "donor",
        "analysis_layer": fit.spec.measurement,
        "effect_definition": "Adjusted case minus control log2 relative expression",
        "reference_condition": "control",
        "condition_number_validated": True,
        "design_rank": fit.tool["method"]["design_rank"],
        "design_column_count": fit.preflight["metrics"]["columns"],
    }
    if fit.spec.measurement == "raw_counts":
        from devbench.study.biology_answers import ComputedConditionContrast

        payload["computed_contrast"] = ComputedConditionContrast.from_measurement(
            "raw_counts",
            "control",
            "case",
        ).model_dump(mode="json")
    return {
        "task_id": fit.spec.family.family_id,
        "input_sha256": fit.spec.expression_sha256,
        "gene_universe_sha256": _digest(fit.spec.family.feature_ids),
        "target_results": [
            {
                "target_id": "mean_contrast",
                "estimand": "Adjusted donor expression contrast",
                "units": "log2 relative expression",
                "contrast": "case minus control",
                "reference_population": "declared synthetic donors",
                "prediction_reference": "observed batches",
                "claim_type": "restricted_estimate",
                "evidence_scope": "population_mean",
                "estimate_status": "estimated",
                "tool_evidence_ids": [fit.result["receipt_sha256"]],
                "payload": payload,
            }
        ],
    }


def _validate_submission(root, fit):
    from devbench.staging import stage_python
    from devbench.study.submission_validation import stage_submission_validator

    root.mkdir()
    stage_python(root, assisted=False, profile="bulk")
    validator = stage_submission_validator(root, "biology", max_answer_bytes=8_000_000)
    answer = root / "final_discovery.json"
    raw = canonical_json(_public_answer(fit))
    answer.write_bytes(raw)
    result = subprocess.run(
        validator.argv,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={"PATH": os.defpath},
    )
    assert result.stderr == ""
    assert answer.read_bytes() == raw
    report = json.loads(result.stdout)
    assert report["scope"] == "public_answer_contract"
    assert result.returncode == (0 if report["valid"] else 1)
    return report


def _verify_thesis(root, fit):
    from devbench.analysis.episode_synthesis import verify_synthesis
    from devbench.analysis.thesis_replay import replay_thesis_package
    from devbench.analysis.thesis_synthesis import (
        ThesisSynthesisSpec,
        write_thesis_package,
    )

    schema = root / "answer_schema.json"
    schema.write_bytes(
        canonical_json(
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["gene_statistics"],
                "properties": {
                    "gene_statistics": {
                        "type": "array",
                        "minItems": len(fit.rows),
                        "maxItems": len(fit.rows),
                        "items": {"type": "object"},
                    },
                },
            }
        )
    )
    specification = ThesisSynthesisSpec.model_validate(
        {
            "question": "Which adjusted differences does the frozen design support?",
            "outcome": "completed",
            "expected_revision": fit.flow.status()["revision"],
            "answer_schema_sha256": _sha(schema),
            "primary_branch_id": "primary",
            "fields": {
                "gene_statistics": {
                    "step_id": "fit",
                    "port": "result",
                    "projection": "tier_a_gene_statistics",
                }
            },
            "data_notes": {
                "declarations": [
                    "Donor identities are synthetic fixture declarations."
                ],
                "unknowns": ["Independent biological validation is absent."],
            },
            "requirement_sources": [
                {
                    "requirement_id": "synthetic-design",
                    "description": "Frozen synthetic condition and donor design",
                    "origin": "fixture_default",
                    "source_sha256": _digest("M4 fixture"),
                }
            ],
        }
    )
    package = root / "thesis"
    seal = write_thesis_package(
        package,
        fit.flow,
        specification,
        answer_schema=schema,
        open_artifact=fit.executor.open_output,
        tier_a_executor=fit.executor,
    )
    assert seal["answer_status"] == "validated"
    assert verify_synthesis(package, seal["root_sha256"], allow_evidence_read=True)[
        "valid"
    ]
    manifest = json.loads((package / "provenance_manifest.json").read_bytes())
    assert manifest["clinical_release_allowed"] is False
    assert (
        manifest["fields"]["gene_statistics"]["receipt_sha256"]
        == fit.result["receipt_sha256"]
    )
    answer = json.loads((package / "final_discovery.json").read_bytes())
    assert len(answer["gene_statistics"]) == len(fit.rows)
    assert answer["gene_statistics"][-1]["p_value"] is None
    replay = replay_thesis_package(
        package,
        seal["root_sha256"],
        output=root / "replay",
        input_root=fit.executor.training.root,
        allow_data_execution=True,
    )
    assert replay["exact_genes_parquet_match"] and replay["steps"] == {
        "fit": fit.artifact.sha256
    }


@pytest.mark.wal
def test_l1_mounted_factorial_bulk_validates_and_replays(
    workspace_tmp_path, pinned_bulk_image
):
    cohort = _make_cohort(workspace_tmp_path, 20_000)
    fit = _fit(
        workspace_tmp_path / "run",
        pinned_bulk_image,
        cohort,
        _pipeline(cohort, family_id="L1"),
    )
    assert fit.preflight["metrics"]["profile_count"] == 12
    assert fit.preflight["metrics"]["declared_unit_count"] == 12
    assert fit.tool["method"]["design_rank"] == 4
    assert fit.tool["method"]["residual_df"] == 8
    assert fit.tool["method"]["solver"] == "edger_v4"
    assert fit.tool["method"]["test"] == "qlf"
    _assert_family(fit, cohort.gene_ids)
    assert np.median([row["effect"] for row in fit.rows[:30]]) > 1
    assert _validate_submission(workspace_tmp_path / "submission", fit)["valid"]
    _verify_thesis(workspace_tmp_path, fit)


@pytest.mark.wal
def test_l2_governed_paired_tximport_keeps_lengths_and_donor_identity(
    workspace_tmp_path,
    pinned_bulk_image,
):
    from devbench.analysis.paired_tximport_svaseq import run_paired_tximport_svaseq

    cohort = _make_cohort(workspace_tmp_path, 800, paired=True, latent=True)
    fit = _fit(
        workspace_tmp_path / "run",
        pinned_bulk_image,
        cohort,
        _pipeline(cohort, family_id="L2"),
    )
    assert fit.preflight["metrics"]["profile_count"] == 16
    assert fit.preflight["metrics"]["declared_unit_count"] == 8
    assert fit.tool["method"]["design_rank"] == 10
    assert fit.tool["method"]["residual_df"] == 6
    assert fit.tool["method"]["solver"] == "edger_v4"
    pins = {row["port"]: row["sha256"] for row in fit.result["consumed"]}
    assert pins["lengths"] == _sha(cohort.lengths)
    _assert_family(fit, cohort.gene_ids)
    assert np.median([row["effect"] for row in fit.rows[:30]]) > 1
    assert abs(np.median([row["effect"] for row in fit.rows[30:90]])) < 0.35
    report = _validate_submission(workspace_tmp_path / "submission", fit)
    assert report["valid"] is False
    assert any(error["path"].endswith(".analysis_layer") for error in report["errors"])
    destination = workspace_tmp_path / "paired-tximport-svaseq.json"
    diagnostic = run_paired_tximport_svaseq(fit.flow, fit.executor, "fit", destination)
    retained = json.loads(destination.read_bytes())
    assert _sha(destination) == diagnostic["sha256"]
    assert retained["primary"]["receipt_sha256"] == fit.result["receipt_sha256"]
    assert retained["primary"]["artifact_sha256"] == fit.artifact.sha256
    assert retained["primary"]["lengths_sha256"] == _sha(cohort.lengths)
    assert retained["diagnostic_only"] is True
    stats = retained["diagnostics"]
    assert stats["method"] == "svaseq" and stats["factor_estimator"] == "leek"
    assert stats["status"] == "completed" and stats["n_factors"] >= 1
    assert stats["design"]["full"]["rank"] == 10
    assert stats["design"]["reduced"]["rank"] == 9
    assert stats["augmented_design"]["full"]["passes"] is True
    assert [row["sample_id"] for row in stats["scores"]] == [
        row["sample_id"] for row in cohort.rows
    ]
    assert (
        fit.flow.current_use("fit", "result", purpose="query", use="claim")
        == fit.artifact
    )
    assert fit.flow.history("fit") == (fit.result,)
    _verify_thesis(workspace_tmp_path, fit)


@pytest.mark.wal
def test_l2_retains_unavailable_paired_diagnostic_with_null_estimates(
    workspace_tmp_path,
    pinned_bulk_image,
):
    from devbench.analysis.paired_tximport_svaseq import run_paired_tximport_svaseq

    cohort = _make_cohort(workspace_tmp_path, 10, paired=True)
    fit = _fit(
        workspace_tmp_path / "run",
        pinned_bulk_image,
        cohort,
        _pipeline(cohort, family_id="L2-low-support"),
    )
    destination = workspace_tmp_path / "unsupported-svaseq.json"
    result = run_paired_tximport_svaseq(fit.flow, fit.executor, "fit", destination)
    stats = result["result"]["diagnostics"]
    assert stats["status"] == "unavailable"
    assert stats["reason"] == "insufficient_residual_or_expressed_support"
    assert stats["n_factors"] is None and stats["scores"] is None
    assert fit.flow.history("fit") == (fit.result,)


def test_l2_refuses_tximport_combined_with_svaseq(workspace_tmp_path):
    cohort = _make_cohort(workspace_tmp_path, 256, paired=True)
    with pytest.raises(ValidationError, match="independent raw-count design"):
        _pipeline(cohort, family_id="L2-unavailable", latent=True)


@pytest.mark.wal
def test_l2_independent_svaseq_is_diagnostic_and_preserves_primary_fit(
    workspace_tmp_path,
    pinned_bulk_image,
):
    cohort = _make_cohort(workspace_tmp_path, 512, latent=True)
    original_metadata = _sha(cohort.metadata)
    baseline = _fit(
        workspace_tmp_path / "baseline",
        pinned_bulk_image,
        cohort,
        _pipeline(cohort, family_id="L2-svaseq"),
    )
    diagnosed = _fit(
        workspace_tmp_path / "diagnosed",
        pinned_bulk_image,
        cohort,
        _pipeline(cohort, family_id="L2-svaseq", latent=True),
    )
    _assert_family(diagnosed, cohort.gene_ids)
    evidence = diagnosed.tool["latent_diagnostics"]
    assert evidence["method"] == "svaseq" and evidence["diagnostic_only"] is True
    assert _digest(diagnosed.rows) == _digest(baseline.rows)
    assert diagnosed.tool["method"] == baseline.tool["method"]
    assert _sha(cohort.metadata) == original_metadata == diagnosed.spec.metadata_sha256
