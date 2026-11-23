"""Compile content-addressed scientific parameters from disclosed artifact versions."""

from devbench.core.contrast_design import (
    TIER_A_CONTROLS_REPRESENTATION,
    TIER_A_LENGTHS_REPRESENTATION,
    TIER_A_MOUNTED_REPRESENTATIONS,
    ContrastSpec,
    DesignSpec,
    MountedModelSpec,
    PipelineSpec,
)
from devbench.core.publication_families import FamilySpec
from devharness_core.provenance import canonical_json
from devharness_server.tools_bulk import validate_bulk_contrast_parameters

from .method_planner_contract import pipeline_artifact_versions


def _model_declaration(design, artifacts):
    if "model" not in design:
        return None
    declarations = {
        key: value
        for key, value in design["model"].items()
        if key not in {"lengths_version_id", "controls_version_id"}
    }
    declarations.update(
        {
            f"{role}_sha256": artifacts[role].sha256
            for role in ("lengths", "controls")
            if role in artifacts
        }
    )
    return MountedModelSpec.model_validate_json(canonical_json(declarations))


def compile_bulk_parameters(action, candidates, executor):
    versions = pipeline_artifact_versions(action)
    if (
        len(set(versions.values())) != len(versions)
        or not set(versions.values()) <= candidates.keys()
    ):
        raise PermissionError("Use distinct admitted original pipeline inputs")
    artifacts = {role: candidates[key][0] for role, key in versions.items()}
    expression, metadata, family = (
        artifacts[role] for role in ("expression", "metadata", "family")
    )
    if (
        expression.kind not in {"counts", "continuous_matrix"}
        or metadata.representation != "metadata_csv"
        or family.representation != "family_spec_json"
    ):
        raise ValueError("Bind expression, metadata and family in their roles")
    for role, representation in (
        ("lengths", TIER_A_LENGTHS_REPRESENTATION),
        ("controls", TIER_A_CONTROLS_REPRESENTATION),
    ):
        if role in artifacts and (
            artifacts[role].kind,
            artifacts[role].representation,
        ) != ("table", representation):
            raise ValueError(
                "Bind each auxiliary matrix or manifest to its declared port"
            )
    units = {
        (item.biological_unit, item.unit_membership_sha256)
        for item in artifacts.values()
    }
    if len(units) != 1:
        raise PermissionError("Pipeline input units and membership must agree")
    mounted = expression.representation in TIER_A_MOUNTED_REPRESENTATIONS
    family_limit = 16_000_000 if mounted else 2 * 1024**2
    with executor.training.open(family.sha256) as stream:
        family_raw = stream.read(family_limit + 1)
    if len(family_raw) > family_limit:
        raise ValueError("Pipeline family exceeds its declared artifact bound")
    contrast = ContrastSpec.model_validate(action["contrast"])
    roles = action["metadata_roles"]
    design = action["design"]
    model = _model_declaration(design, artifacts)
    measurement = (
        "tximport_estimated_counts"
        if expression.representation == "tximport_counts_h5"
        else "raw_counts"
        if expression.kind == "counts"
        else "continuous"
    )
    spec = PipelineSpec(
        format=(
            "devbench.tier-a-bulk-pipeline/2"
            if "continuous_covariates" in design
            else "devbench.tier-a-bulk-pipeline/1"
        ),
        design=DesignSpec(
            design["regime"],
            contrast.numerator,
            contrast.denominator,
            technical_factors=tuple(design["technical_factors"]),
            cohort_mode=(
                "joint_multi_condition_model"
                if model and model.comparison != "coefficient"
                else "contrast_pair_subcohort"
            ),
        ),
        contrast=contrast,
        family=FamilySpec.model_validate_json(family_raw),
        measurement=measurement,
        biological_unit=expression.biological_unit,
        condition_column=roles["condition"],
        population_unit_column=roles["biological_unit"],
        specimen_column=roles["specimen"],
        library_column=roles["library"],
        expression_sha256=expression.sha256,
        metadata_sha256=metadata.sha256,
        family_sha256=family.sha256,
        continuous_covariates=tuple(design.get("continuous_covariates", ())),
        expression_layer=design.get("expression_layer"),
        mounted_model=model,
    )
    if mounted:
        from devbench.engine.mounted_kernel_oci import require_mounted_pipeline_runtime

        require_mounted_pipeline_runtime(
            spec, getattr(executor, "mounted_image_id", None),
            representation=expression.representation,
            allow_python_wald=getattr(executor, "allow_python_wald", False),
        )
    else:
        validate_bulk_contrast_parameters(*spec.tool_request())
    return spec, artifacts
