"""Wire contracts for the thirteen research tools (ratified specification §5)."""

from __future__ import annotations

from functools import cache

from jsonschema import Draft202012Validator, validators

PAYLOAD_BYTES = 64 * 1024
SCRIPT_BYTES = 512 * 1024
TEXT = {"type": "string"}
BOOL = {"type": "boolean"}
NUMBER = {"type": "number"}
OBJECT = {"type": "object"}
SESSION = {"type": "string", "pattern": "^[0-9a-f]{32}$"}
SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
ID = {"type": "string", "minLength": 1, "maxLength": 128}


def enum(*values):
    return {"enum": list(values)}


def integer(low=0, high=None):
    spec = {"type": "integer", "minimum": low}
    if high is not None:
        spec["maximum"] = high
    return spec


def string(limit, minimum=1):
    return {"type": "string", "minLength": minimum, "maxLength": limit}


def array(items, limit=None, minimum=0):
    spec = {"type": "array", "items": items}
    if limit is not None:
        spec["maxItems"] = limit
    if minimum:
        spec["minItems"] = minimum
    return spec


def nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


def obj(fields, required=None):
    return {
        "type": "object",
        "properties": fields,
        "required": list(fields if required is None else required),
        "additionalProperties": False,
    }


def mapping(values):
    return {"type": "object", "additionalProperties": values}


def ref(name):
    return {"$ref": "#/$defs/" + name}


def default(schema, value):
    return {**schema, "default": value}


REPRESENTATIONS = enum(
    "raw_reads_or_droplets",
    "count_1_raw_counts",
    "ambient_adjusted_counts",
    "size_factor_normalized",
    "integrated_latent_or_graph",
    "spatial_geometry_and_residuals",
    "aggregated_unit_counts",
    "derived_summary",
    "metadata_only",
    "reference_only",
)
CLAIM = enum(
    "none",
    "L1_execution_verified",
    "L2_statistically_calibrated",
    "L3_scientifically_supported",
)
CLASSIFICATION = enum("synthetic", "public", "private", "clinical", "unclassified")
FILE_KIND = enum("expression", "metadata", "reference", "manifest", "spatial")
ROLE = enum(
    "primary_cohort",
    "reference_atlas",
    "gene_set_collection",
    "ligand_receptor_resource",
    "external_replication_cohort",
    "genotype_panel",
    "prior_study_table",
    "follow_up_experiment",
)
LANE = enum("tier1_curated", "tier2_exploratory")
PLAN_KIND = enum("tier1_kernel", "tier2_exploratory_code")
AXIS = enum(*(f"B{i}" for i in range(1, 13)), "B_OPEN")
ORIGINS = enum(
    "user_prespecified",
    "operator_approved",
    "assistant_proposed",
    "fixture_default",
    "derived",
)
BRANCH = enum(
    "primary",
    "sensitivity",
    "competing_hypothesis",
    "method_benchmark",
    "exploratory_scratch",
)
PURPOSE = enum(
    "qc_diagnostic",
    "exploratory",
    "sensitivity_probe",
    "method_benchmark",
    "confirmatory",
)
HANDOFF = {"studio_url": nullable(TEXT), "launch_hint": nullable(TEXT)}
CATEGORIES = enum(
    "scientific_refusal",
    "awaiting_information",
    "awaiting_review",
    "private_data_local_studio_only",
    "runtime_prerequisite_missing",
    "input_scope_violation",
    "plan_identity_mismatch",
    "stale_revision_conflict",
    "resource_quota_exceeded",
    "invalid_request",
)
ARTIFACT = obj(
    {
        "artifact_id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9._:-]{1,127}$"},
        "sha256": SHA,
        "kind": enum(
            "expression_matrix",
            "metadata",
            "reference",
            "table",
            "embedding",
            "figure",
            "plot_sidecar",
            "model_checkpoint",
            "script",
            "replay_package",
        ),
        "format": enum(
            "h5ad",
            "zarr",
            "h5mu",
            "spatialdata_zarr",
            "tiledbsoma",
            "parquet",
            "csv",
            "tsv",
            "json",
            "png",
            "svg",
            "plotly_json",
            "zip",
        ),
        "representation_class": REPRESENTATIONS,
        "claim_tier": CLAIM,
        "size_bytes": integer(),
        "shape": nullable(array(integer(), 4)),
        "schema_preview": nullable(OBJECT),
    },
    (
        "artifact_id",
        "sha256",
        "kind",
        "format",
        "representation_class",
        "claim_tier",
        "size_bytes",
    ),
)
OPAQUE = obj({"artifact_id": ID, "sha256": SHA})
ERROR = obj(
    {
        "error": TEXT,
        "reason": TEXT,
        "category": CATEGORIES,
        "remediation": string(1000),
        **HANDOFF,
        "validation_errors": array(obj({"loc": TEXT, "type": TEXT}), 16),
    },
    ("error", "reason", "category", "remediation"),
)
FILE_FIELDS = {
    "path": string(4096),
    "file_kind": FILE_KIND,
    "data_classification": default(CLASSIFICATION, "unclassified"),
    "clinical_metadata": default(BOOL, False),
}
DESIGN = obj(
    {
        "metadata_artifact_id": ID,
        "formula": string(1024),
        "contrast": nullable(
            obj({"factor": TEXT, "numerator": TEXT, "denominator": TEXT})
        ),
        "biological_unit": ID,
        "specimen_column": nullable(ID),
        "capture_column": nullable(ID),
        "batch_columns": array(ID, 32),
        "forensics_verified": BOOL,
    },
    ("metadata_artifact_id", "formula", "biological_unit"),
)
BINDING = obj({"run_id": ID, "receipt_sha256": SHA})
PLAN_ID = {"session_id": SESSION, "plan_id": ID, "plan_sha256": SHA}
RUN_ID = {"session_id": SESSION, "run_id": ID}
KERNEL = obj({"kernel_id": ID, "version": ID, "parameters": OBJECT})
CODE = obj(
    {
        "base_image_id": ID,
        "language": enum("python", "r"),
        "source_code": string(SCRIPT_BYTES),
        "script_path": string(1024),
        "parameters": OBJECT,
        "expected_outputs": array({}, 32),
    },
    ("base_image_id", "language", "expected_outputs"),
)
CODE["oneOf"] = [{"required": ["source_code"]}, {"required": ["script_path"]}]
STEPS = array(
    obj(
        {
            "step_key": ID,
            "plan_kind": PLAN_KIND,
            "kernel": nullable(KERNEL),
            "exploratory_code": nullable(CODE),
            "input_bindings": mapping(OPAQUE),
            "depends_on": array(ID, 16),
            "parameters": OBJECT,
        },
        ("step_key", "plan_kind"),
    ),
    16,
)
FREEZE = obj(
    {
        "session_id": SESSION,
        "branch_id": ID,
        "branch_kind": BRANCH,
        "aim_id": nullable(ID),
        "epistemic_purpose": PURPOSE,
        "objective": string(1600),
        "plan_kind": PLAN_KIND,
        "kernel": nullable(KERNEL),
        "exploratory_code": nullable(CODE),
        "input_bindings": mapping(OPAQUE),
        "parameter_origins": mapping(ORIGINS),
        "sweep_spec": nullable(
            obj(
                {
                    "kind": enum("lodo_donor_drop", "seed_grid", "parameter_grid"),
                    "parameter_sets": array(OBJECT, 64, 1),
                }
            )
        ),
        "graph_steps": nullable(STEPS),
        "depends_on": array(obj({"plan_id": ID, "plan_sha256": SHA}), 16),
        "resource_override": nullable(
            obj(
                {
                    "cpus": integer(1),
                    "memory_mb": integer(256),
                    "timeout_sec": integer(30, 259200),
                    "gpu": BOOL,
                },
                (),
            )
        ),
    },
    (
        "session_id",
        "branch_id",
        "epistemic_purpose",
        "objective",
        "plan_kind",
        "input_bindings",
    ),
)
FREEZE["oneOf"] = [
    {
        "properties": {
            "plan_kind": {"const": "tier1_kernel"},
            "kernel": KERNEL,
            "exploratory_code": {"type": "null"},
        },
        "required": ["kernel"],
    },
    {
        "properties": {
            "plan_kind": {"const": "tier2_exploratory_code"},
            "kernel": {"type": "null"},
            "exploratory_code": CODE,
        },
        "required": ["exploratory_code"],
    },
]


def aim_actions():
    # Each action owns its own field set; extra fields are rejected within the arm.
    rationale = {"rationale": string(1600)}
    aim = {"aim_id": ID}
    payloads = {
        "list": ({}, ()),
        "propose_aim": ({"definition": OBJECT, **rationale}, ("definition",)),
        "revise_aim": (
            {**aim, "definition": OBJECT, **rationale},
            ("aim_id", "definition"),
        ),
        "approve_aim": ({**aim, "aim_version_sha256": SHA, **rationale}, ("aim_id",)),
        "fork_branch": (
            {
                "parent_branch_id": ID,
                "branch_id": ID,
                "branch_kind": BRANCH,
                **aim,
                **rationale,
            },
            ("parent_branch_id", "branch_id", "branch_kind"),
        ),
        "retire_branch": ({"branch_id": ID, **rationale}, ("branch_id",)),
        "assess_explanation": (
            {**aim, "assessment": OBJECT, **rationale},
            ("aim_id", "assessment"),
        ),
        "record_waiver": (
            {**aim, **rationale, "plan_id": ID, "plan_sha256": SHA, "hold_id": ID},
            ("plan_id", "plan_sha256", "hold_id", "rationale"),
        ),
        "freeze_joint_bh": ({"plan": OBJECT, **rationale}, ("plan",)),
        "release_joint_bh": ({"family_id": ID, **rationale}, ("family_id",)),
        "request_critic_review": (
            {**aim, "plan_id": ID, "gate": enum(1, 2), **rationale},
            ("aim_id",),
        ),
        "record_outcome": (
            {**aim, "outcome": OBJECT, **rationale},
            ("aim_id", "outcome"),
        ),
        "query_literature": (
            {
                "phase": enum("pre_study", "post_evidence"),
                "query": string(500),
                "max_results": default(integer(1, 25), 10),
                "offline_snapshot_sha256": nullable(SHA),
                "evidence_binding": nullable(BINDING),
            },
            ("phase", "query"),
        ),
        "build_research_package": ({"spec": OBJECT, **rationale}, ()),
    }
    variants = []
    fields = {}
    for action, (props, required) in payloads.items():
        shape = {
            "action": {"const": action},
            "session_id": SESSION,
            "expected_revision": integer(),
            **props,
        }
        keys = ["action", "session_id", *required]
        if action != "list":
            keys.append("expected_revision")
        variant = obj(shape, keys)
        if action == "query_literature":
            variant.update(
                {
                    "if": {"properties": {"phase": {"const": "post_evidence"}}},
                    "then": {
                        "required": ["evidence_binding"],
                        "properties": {"evidence_binding": BINDING},
                    },
                }
            )
        variants.append(variant)
        fields.update(shape)
    fields["action"] = enum(*payloads)
    return {
        **obj(fields, ("action", "session_id")),
        "oneOf": variants,
        "discriminator": {"propertyName": "action"},
    }


INPUTS = {
    "inspect_runtime_capabilities": obj(
        {
            "include_images": default(BOOL, True),
            "include_packages": default(BOOL, True),
        },
        (),
    ),
    "inspect_inputs": obj(
        {
            "inputs": array(obj(FILE_FIELDS, ("path", "file_kind")), 32, 1),
            "inspect_layers": default(BOOL, True),
        },
        ("inputs",),
    ),
    "register_investigation_input": obj(
        {
            "session_id": nullable(SESSION),
            "expected_revision": nullable(integer()),
            "input": obj(
                {**FILE_FIELDS, "investigation_role": ROLE},
                ("path", "file_kind", "investigation_role"),
            ),
            "representation_class": REPRESENTATIONS,
            "provenance_declaration": obj(
                {
                    "origin": enum(
                        "user_declaration", "metadata_declaration", "public_registry"
                    ),
                    "source": string(2000),
                    "biological_unit": nullable(TEXT),
                    "count_provenance": nullable(TEXT),
                },
                ("origin", "source"),
            ),
        },
        ("input", "representation_class", "provenance_declaration"),
    ),
    "check_answerability": obj(
        {
            "session_id": nullable(SESSION),
            "question_text": string(2000),
            "axis_b_class": nullable(AXIS),
            "assay": ID,
            "entry_point": ID,
            "artifact_ids": array(ID, 128),
            "design": nullable(DESIGN),
            "candidate_kernels": array(ID, 128),
        },
        ("question_text", "assay", "entry_point"),
    ),
    "list_kernels_and_methods": obj(
        {
            "session_id": nullable(SESSION),
            "axis_b_class": nullable(AXIS),
            "assay": nullable(ID),
            "lane": default(enum(*LANE["enum"], "all"), "all"),
            "include_parameter_schema": default(BOOL, False),
            "kernel_ids": nullable(array(ID, 128)),
        },
        (),
    ),
    "list_investigation_state": obj(
        {
            "session_id": SESSION,
            "include_retired_branches": default(BOOL, False),
            "artifact_limit": default(integer(1, 100), 50),
            "run_limit": default(integer(1, 50), 20),
        },
        ("session_id",),
    ),
    "query_artifact_table": obj(
        {
            "session_id": SESSION,
            "artifact_id": ID,
            "artifact_sha256": SHA,
            "table_slot": default(
                enum("default", "obs", "var", "plot_sidecar"), "default"
            ),
            "columns": nullable(array(ID, 32)),
            "filters": array(
                obj(
                    {
                        "column": ID,
                        "op": enum(
                            "eq", "neq", "lt", "lte", "gt", "gte", "in", "contains"
                        ),
                        "value": {},
                    }
                ),
                8,
            ),
            "order_by": array(obj({"column": ID, "descending": BOOL}), 4),
            "limit": default(integer(1, 200), 50),
            "offset": default(integer(), 0),
            "aggregate": nullable(
                obj(
                    {
                        "group_by": array(ID, 32),
                        "metrics": array(
                            obj(
                                {
                                    "column": ID,
                                    "fn": enum(
                                        "count", "mean", "median", "min", "max", "sum"
                                    ),
                                }
                            ),
                            32,
                            1,
                        ),
                    }
                )
            ),
        },
        ("session_id", "artifact_id", "artifact_sha256"),
    ),
    "get_run_status": obj(RUN_ID),
    "get_run_receipt": obj(
        {**RUN_ID, "include_figure_preview": default(BOOL, False)}, RUN_ID
    ),
    "manage_research_aim": aim_actions(),
    "freeze_analysis_plan": FREEZE,
    "submit_reviewed_run": obj(
        {**PLAN_ID, "resume_from_run_id": nullable(ID)}, PLAN_ID
    ),
    "cancel_run": obj({**RUN_ID, "reason": string(500)}),
}
STATUS = obj(
    {
        **RUN_ID,
        "plan_id": ID,
        "status": enum(
            "queued",
            "running",
            "awaiting_review",
            "completed",
            "failed",
            "interrupted",
            "cancelled",
        ),
        "execution_lane": LANE,
        "pool": TEXT,
        "progress": obj(
            {"completed": NUMBER, "total": NUMBER, "unit": TEXT, "stage": TEXT}
        ),
        "heartbeat_age_sec": nullable(NUMBER),
        "elapsed_sec": NUMBER,
        "checkpoint_available": BOOL,
        "tasks": array(OBJECT),
        "warnings": array(TEXT),
        **HANDOFF,
    }
)
FORENSICS = obj(
    {
        "integer_valued": BOOL,
        "nonnegative": BOOL,
        "suspected_transformed": BOOL,
        "warnings": array(TEXT),
    }
)
AXES = obj(
    {
        "observations": nullable(integer()),
        "features": nullable(integer()),
        "obs_columns": array(TEXT, 128),
        "var_columns": array(TEXT, 64),
        "layers": array(TEXT),
        "obsm_keys": array(TEXT),
    }
)
KERNEL_ITEM = obj(
    {
        "kernel_id": ID,
        "version": ID,
        "lane": LANE,
        "promotion_scope": nullable(enum("repository", "workspace")),
        "capability_axes": array(TEXT),
        "required_representations": array(REPRESENTATIONS),
        "calibration_status": TEXT,
        "calibrated_regimes": array(TEXT),
        "runtime_ready": BOOL,
        "resource_profile": OBJECT,
        "parameter_schema": nullable(OBJECT),
    }
)
SUCCESS = {
    "inspect_runtime_capabilities": obj(
        {
            "daemon_version": TEXT,
            "sqlite_wal_patched": BOOL,
            "container_engines": array(OBJECT),
            "available_images": array(
                obj(
                    {
                        "image_id": TEXT,
                        "lane": LANE,
                        "architecture": TEXT,
                        "capabilities": array(TEXT),
                    }
                )
            ),
            "hardware": obj(
                {
                    "cpu_cores": integer(1),
                    "available_ram_mb": integer(),
                    "gpus": array({}),
                    "free_disk_gb": NUMBER,
                }
            ),
            "pools": obj(
                {
                    "cpu_slots": integer(),
                    "gpu_slots": integer(),
                    "max_concurrent_runs": integer(),
                }
            ),
            "studio": obj({"running": BOOL, "offline_only": BOOL}),
            "packages": OBJECT,
        },
        (
            "daemon_version",
            "sqlite_wal_patched",
            "container_engines",
            "available_images",
            "hardware",
            "pools",
            "studio",
        ),
    ),
    "inspect_inputs": obj(
        {
            "inputs": array(
                obj(
                    {
                        "canonical_path": TEXT,
                        "sha256": nullable(SHA),
                        "merkle_root_sha256": nullable(SHA),
                        "format": TEXT,
                        "size_bytes": integer(),
                        "axes": AXES,
                        "count_1_forensics": FORENSICS,
                        "data_classification": CLASSIFICATION,
                    }
                ),
                32,
            )
        }
    ),
    "register_investigation_input": obj(
        {
            "session_id": SESSION,
            "revision": integer(),
            "artifact": nullable(ref("ArtifactRef")),
            "opaque_artifact": nullable(OPAQUE),
            "private_tainted": BOOL,
            "category": enum("registered", "private_data_local_studio_only"),
            **HANDOFF,
        }
    ),
    "check_answerability": obj(
        {
            "status": enum(
                "ready", "awaiting_information", "refused", "tier2_exploratory_only"
            ),
            "reason": TEXT,
            "epistemic_state": enum("E0", "E_ABSTAIN"),
            "axis_b_class": AXIS,
            "design_diagnostics": nullable(
                obj(
                    {
                        "full_rank": BOOL,
                        "rank": integer(),
                        "columns": integer(),
                        "residual_df": {"type": "integer"},
                        "biological_units_per_group": mapping(integer()),
                        "confounded_terms": array(TEXT),
                    }
                )
            ),
            "missing_evidence": array(TEXT),
            "recommended_kernels": array(
                obj(
                    {
                        "kernel_id": ID,
                        "calibration_status": TEXT,
                        "assumptions": array(TEXT),
                        "runtime_ready": BOOL,
                    }
                )
            ),
        }
    ),
    "list_kernels_and_methods": obj(
        {
            "kernels": array(KERNEL_ITEM),
            "tier2_base_images": array(
                obj(
                    {
                        "image_id": TEXT,
                        "language": TEXT,
                        "packages_summary": TEXT,
                        "gpu_supported": BOOL,
                    }
                )
            ),
        }
    ),
    "list_investigation_state": obj(
        {
            "session_id": SESSION,
            "revision": integer(),
            "data_classification": CLASSIFICATION,
            "redacted": BOOL,
            "epistemic_state": TEXT,
            "aims": array(OBJECT),
            "branches": array(OBJECT),
            "artifacts": array(
                obj(
                    {
                        "artifact": nullable(ref("ArtifactRef")),
                        "opaque_artifact": nullable(OPAQUE),
                        "status": enum("current", "stale", "embargoed"),
                        "producer_step_id": nullable(ID),
                    }
                )
            ),
            "runs": array(OBJECT),
            "pending_holds": array(OBJECT),
            **HANDOFF,
        }
    ),
    "query_artifact_table": obj(
        {
            "artifact_id": ID,
            "artifact_sha256": SHA,
            "total_matching_rows": integer(),
            "returned_rows": integer(0, 200),
            "truncated": BOOL,
            "columns": array(TEXT),
            "rows": array(array({}), 200),
            "next_offset": nullable(integer()),
        }
    ),
    "get_run_status": STATUS,
    "get_run_receipt": obj(
        {
            **PLAN_ID,
            "run_id": ID,
            "status": enum("completed", "failed", "cancelled"),
            "execution_lane": LANE,
            "claim_tier": CLAIM,
            "ai_critic_verified": BOOL,
            "clinical_release_allowed": {"const": False},
            "probability_bounds_status": enum("verified", "not_evaluated", "failed"),
            "critic_audit": obj(
                {
                    "status": enum("passed", "flagged", "not_run"),
                    "critic_origin": enum(
                        "deterministic_verifier",
                        "ai_critic_review",
                        "human_expert_panel",
                        "hybrid",
                    ),
                    "findings": array(TEXT),
                }
            ),
            "metrics": OBJECT,
            "summary_tables": array(
                obj({"rows": array({}, 20)}, ("rows",)) | {"additionalProperties": True}
            ),
            "outputs": mapping(ref("ArtifactRef")),
            "figure_artifacts": array(
                obj(
                    {
                        "artifact": ref("ArtifactRef"),
                        "sidecar": ref("ArtifactRef"),
                        "verification_state": enum(
                            "produced_unverified", "visually_verified"
                        ),
                    }
                )
            ),
            "limitations": array(TEXT),
            "replay_bundle_sha256": SHA,
            "studio_url": nullable(TEXT),
        }
    ),
    "manage_research_aim": obj(
        {
            "session_id": SESSION,
            "revision": integer(),
            "action": TEXT,
            "status": TEXT,
            "invalidated_steps": array(ID),
            "joint_bh_state": nullable(OBJECT),
            "literature_records": nullable(array(OBJECT)),
            "critic_review_id": nullable(ID),
            "package_root_sha256": nullable(SHA),
            "clinical_release_allowed": {"const": False},
            "aims": array(OBJECT),
            "studio_url": nullable(TEXT),
        }
    ),
    "freeze_analysis_plan": obj(
        {
            **PLAN_ID,
            "status": enum("planned", "local_studio_only"),
            "execution_lane": LANE,
            "claim_tier_ceiling": CLAIM,
            "approval_required": enum("none", "codex_native", "local_studio_or_cli"),
            "runtime_pin": OBJECT,
            "static_preflight_warnings": array(TEXT),
            **HANDOFF,
        }
    ),
    "submit_reviewed_run": STATUS,
    "cancel_run": obj(
        {
            **RUN_ID,
            "status": enum("cancelled", "already_terminal"),
            "cleanup_state": enum("confirmed", "pending", "failed"),
            "checkpoint_preserved": BOOL,
        }
    ),
}


@cache
def output_schema(name):
    return {
        "type": "object",
        "oneOf": [SUCCESS[name], ref("ErrorResponse")],
        "$defs": {"ArtifactRef": ARTIFACT, "ErrorResponse": ERROR},
    }


StrictValidator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda _, value: type(value) is int
    ),
)


@cache
def validator(name, *, output=False):
    return StrictValidator(output_schema(name) if output else INPUTS[name])
