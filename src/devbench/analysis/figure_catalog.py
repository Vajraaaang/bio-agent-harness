"""RNA-seq plot implementations, dataset availability and verification.

Names and claimed prerequisites never establish available scientific data. The
legacy figure reader supplies current verified G02/G08 artifacts; the scientific
workspace reader supplies validated, digest-bound workspaces and explicit test
evidence. None of these software checks establishes scientific validity.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re


# id, name, required inputs, units, first planned stage, producer status, limits.
# Existing outputs are deliberately distinguished from a complete graph adapter.
_FAMILIES = (
    ("G01", "Library-size / detected-gene / mitochondrial-QC distributions",
     ("cell_qc_metrics", "cell_ids", "qc_filter_membership"), ("cell",), "M2b",
     "existing_qc_metrics",
     "Only the listed saved cell-metric distribution adapters are implemented; exact membership and declared thresholds are required, with no universal QC cutoff."),
    ("G02", "QC scatterplots",
     ("qc_coordinates", "cell_ids", "qc_semantics"), ("cell",), "M2a",
     "existing_qc_metrics",
     "Count source, transformations and zero/missing semantics belong to the verified figure; linked selection is M2b."),
    ("G03", "Doublet / ambient-RNA diagnostics",
     ("doublet_scores", "method_metadata", "cell_ids"), ("cell",), "M2b",
     "partial_doublet_outputs",
     "Doublet calls and ambient-RNA measurements require distinct saved evidence; neither a score nor a supplied correction establishes validated cell identity or decontamination."),
    ("G04", "Sample correlation / sample PCA",
     ("sample_matrix", "normalization_metadata", "sample_metadata", "independent_biological_units"),
     ("biological_sample",), "M2c", "verified_sample_adapter_required",
     "Requires audited independent-sample or pseudobulk membership; cells cannot substitute for biological replication."),
    ("G05", "Mean-variance / dispersion diagnostics",
     ("model_diagnostics", "gene_ids", "model_metadata"), ("gene",), "M2c",
     "model_fields_unverified",
     "Only actual means, variance/dispersion and fitted trends are admissible; effect tables cannot supply invented diagnostics."),
    ("G06", "Residual / design diagnostics",
     ("design_matrix", "model_residuals", "fitted_values", "independent_biological_units"),
     ("biological_sample", "gene"), "M2c", "partial_design_validation",
     "Existing design validators do not establish residual availability; full rank alone does not resolve confounding."),
    ("G07", "PCA",
     ("pca_scores", "pca_loadings", "explained_variance", "feature_ids", "transformation_metadata"),
     ("cell", "biological_sample"), "M2b", "partial_scores_without_variance",
     "Cell PCA scree requires saved explained variance; sample scores, loadings and biplots additionally require the G04 sample contract."),
    ("G08", "UMAP / t-SNE",
     ("embedding_coordinates", "cell_ids", "embedding_configuration", "representation_metadata"),
     ("cell",), "M2a", "umap_only",
     "The legacy figure route supplies UMAP only; t-SNE requires its own saved method/configuration through an implemented subtype. Coordinates do not establish global distances or biological validation."),
    ("G09", "Feature expression plot",
     ("explicit_expression_layer", "gene_ids", "cell_ids", "normalization_metadata"),
     ("cell", "gene"), "M2b", "bounded_layer_adapter_required",
     "HVG-only selected matrices cannot satisfy arbitrary marker requests; missing expression differs from measured zero."),
    ("G10", "Marker dot plot",
     ("explicit_expression_layer", "gene_ids", "group_membership", "expression_threshold", "denominator_policy"),
     ("group_gene",), "M2b", "aggregate_adapter_required",
     "Requires actual numerator/denominator memberships, mean basis and scaling; donor pooling must be disclosed."),
    ("G11", "Violin / box / ridge expression plots",
     ("explicit_expression_layer", "gene_ids", "group_membership", "distribution_parameters"),
     ("cell", "biological_sample"), "M2b", "distribution_adapter_required",
     "M2b is descriptive; KDE bandwidth, box convention and zero/missing handling remain calculation parameters. Cells do not create independent replicates."),
    ("G12", "Heatmap",
     ("bounded_expression_matrix", "feature_ids", "observation_or_group_ids", "scale_and_order_metadata"),
     ("gene_observation", "group_gene"), "M2b", "matrix_adapter_required",
     "Aggregation/order/clustering require exported calculations; row-scaled colors do not imply absolute expression."),
    ("G13", "MA plot",
     ("verified_gene_results", "average_abundance", "effect_definition", "tested_family", "independent_biological_units"),
     ("gene",), "M2c", "verified_result_adapter_required",
     "Actual abundance, signed effects and shrinkage/testing definitions are required; failed/filtered genes retain their status."),
    ("G14", "Volcano plot",
     ("verified_gene_results", "effect_definition", "tested_family", "estimable_contrast", "independent_biological_units"),
     ("gene",), "M2c", "verified_result_adapter_required",
     "T14 effect_definition_mismatch remains unresolved for the affected state-only route; p/q clipping cannot fabricate significance."),
    ("G15", "Replicate-level expression / effect plot",
     ("sample_expression_or_effects", "sample_membership", "pairing_metadata", "independent_biological_units"),
     ("biological_sample",), "M2c", "verified_sample_adapter_required",
     "Donor/specimen identities and genuine pairing are required; estimates and confidence intervals appear only if actually produced."),
    ("G16", "Cell proportion / differential-abundance plots",
     ("sample_cell_counts", "sample_denominators", "sample_membership", "independent_biological_units"),
     ("biological_sample", "sample_group"), "M2b", "sample_composition_adapter_required",
     "M2b covers descriptive sample composition only. Inferential DA additionally requires a compatible replicated-method contract in M2c; sampled composition is not absolute tissue abundance."),
    ("G17", "Enrichment / gene-set plots",
     ("verified_enrichment_results", "gene_and_species_ids", "database_version", "tested_universe", "set_membership"),
     ("gene_set", "biological_sample"), "M2c", "compatible_producer_unverified",
     "Saved enrichment inputs must satisfy the listed adapters; tool availability alone supplies no compatible pathway result. Membership and scores support associations, not causal activation."),
    ("G18", "Pseudotime / trajectory plots",
     ("fitted_trajectory", "observation_ids", "root_and_branch_metadata", "trajectory_model_metadata"),
     ("cell", "observation"), "E1", "dedicated_contract_required",
     "Each temporal subtype requires its own saved outputs; a supplied curve does not supply observed biological replicates or intervals. Inferred pseudotime is distinct from collection age, elapsed time and proven lineage."),
    ("G19", "Velocity fields",
     ("spliced_expression", "unspliced_expression", "fitted_velocity_kinetics", "gene_level_diagnostics"),
     ("cell", "gene"), "E1", "unavailable",
     "Ordinary gene counts do not supply spliced/unspliced evidence; arrows are model-dependent estimates."),
    ("G20", "Splice / isoform / junction views",
     ("junction_or_transcript_evidence", "genomic_coordinates", "strand_and_genome_build", "sample_replicates"),
     ("genomic_event", "transcript", "biological_sample"), "E2", "unavailable",
     "Gene counts do not supply junction or sashimi evidence; dedicated sequence inputs and methods require separate authorization."),
    ("G21", "Spatial maps",
     ("spatial_coordinates", "coordinate_transforms", "expression_values", "spatial_unit_ids"),
     ("spot", "cell", "region"), "E2", "unavailable",
     "Coordinates, registration and physical scale require an explicit spatial contract; image/segmentation evidence is required where applicable."),
    ("G22", "Perturbation / cross-species comparison views",
     ("experiment_specific_contract", "guide_control_or_species_identity", "replicate_or_versioned_orthology_evidence"),
     ("biological_sample", "gene", "cell"), "E2", "unavailable",
     "Perturbation and cross-species views need separate adapters: audited guides/controls/replicates or versioned one-to-many orthology; predictions are not observed effects."),
)


# Explicit semantic mappings, not capability-name prefix inference. These IDs
# describe saved-data adapters, not fitting engines or entire canonical families.
# A registered adapter is implemented only if it is also in the live registry.
_SCIENTIFIC_SUBTYPES = {
    "qc_violins": (("G01",), ()),
    "qc_ridges": (("G01",), ()),
    "qc_joint_scatter": (("G02",), ()),
    "depth_histogram": (("G01",), ()),
    "depth_ecdf": (("G01",), ()),
    "batch_boxplot": (("G01",), ()),
    "doublet_diagnostics": (("G03", "G08"), ()),
    "ambient_rna_diagnostics": (("G03",), ()),
    "bulk_pca": (("G04", "G07"), ()),
    "bulk_mds": (("G04",), ("sample_mds",)),
    "bulk_sample_correlation_heatmap": (("G04",), ()),
    "bulk_sample_distance_heatmap": (("G04",), ()),
    "bulk_dispersion": (("G05",), ()),
    "bulk_mean_variance": (("G05",), ()),
    "hvg_scatter": (("G05",), ()),
    "bulk_cooks_distance": (("G06",), ()),
    "bulk_leave_one_pair_out": (("G06",), ()),
    "sva_association": (("G06",), ()),
    "bulk_residual_diagnostics": (("G06",), ()),
    "bulk_design_diagnostics": (("G06",), ()),
    "scrna_pca_scree": (("G07",), ()),
    "integration_diagnostics": (("G08",), ("batch_bioconservation",)),
    "embedding_density_centroids": (("G08",), ()),
    "saved_embedding": (("G08",), ()),
    "embedding_hexbin": (("G08",), ()),
    "embedding_density_contours": (("G08",), ()),
    "feature_expression": (("G09",), ()),
    "marker_dotplot": (("G10",), ()),
    "expression_violins": (("G11",), ()),
    "expression_boxplots": (("G11",), ()),
    "expression_ridges": (("G11",), ()),
    "expression_heatmap": (("G12",), ()),
    "module_heatmap": (("G12",), ()),
    "bulk_ma_comparison": (("G13",), ()),
    "bulk_volcano": (("G14",), ()),
    "bulk_paired_expression": (("G15",), ()),
    "donor_composition": (("G16",), ()),
    "batch_composition": (("G16",), ()),
    "gsea_running": (("G17",), ()),
    "enrichment_dotplot": (("G17",), ()),
    "motif_enrichment": (("G17",), ("motif_enrichment",)),
    "temporal_spline": (("G18",), ()),
    "saved_temporal_curve": (("G18",), ()),
    "temporal_model_comparison": (("G18",), ()),
    "pseudotime_monotonicity": (("G18",), ()),
    "lineage_schematic": (("G18",), ()),
    "rna_velocity": (("G19",), ()),
    "splice_tracks": (("G20",), ()),
    "genomic_effect": (("G20",), ()),
    "spatial_expression": (("G21",), ()),
    "spatial_tissue_overlay": (("G21",), ()),
    "spatial_segmentation": (("G21",), ()),
    "perturbation_control": (("G22",), ()),
    "orthology_comparison": (("G22",), ()),
    "bulk_library_qc": ((), ("bulk_library_qc", "bulk_normalization_qc")),
    "bulk_pvalue_histogram": ((), ("pvalue_distribution",)),
    "bulk_independent_filtering": ((), ("independent_filter_sensitivity",)),
    "bulk_concordance": ((), ("cross_method_concordance", "wald_lrt_concordance")),
    "bulk_upset_dispositions": ((), ("method_set_upset", "method_dispositions")),
    "classifier_confidence_histogram": ((), ("classifier_confidence",)),
    "regulatory_network": ((), ("regulatory_network",)),
    "repertoire_abundance": ((), ("repertoire_vdj",)),
    "repertoire_overlap": ((), ("repertoire_vdj",)),
    "repertoire_diversity": ((), ("repertoire_vdj",)),
    "multimodal_features": ((), ("multimodal",)),
    "multimodal_heatmap": ((), ("multimodal",)),
    "resource_curve": ((), ("operational_resource",)),
    "execution_dag": ((), ("execution_dag",)),
    "scatter_marginals": ((), ("scatter_marginals",)),
    "measurement_correlation": ((), ("measurement_correlation",)),
    "scrna_population_cell_counts": ((), ("scrna_population_prefit",)),
    "scrna_population_contrast_support": ((), ("scrna_population_prefit",)),
}

_NAMED_GAPS = {
    "ambient_rna": {
        "canonical_ids": ("G03",), "audit_requirement_ids": ("S09",),
        "implemented_by": ("ambient_rna_diagnostics",),
        "description": "Ambient-RNA diagnostics require their own saved evidence and adapter; doublet scores do not supply them.",
    },
    "residual_design": {
        "canonical_ids": ("G06",), "audit_requirement_ids": ("S29",),
        "implemented_by": ("bulk_residual_diagnostics", "bulk_design_diagnostics"),
        "description": "Residual-versus-fitted and design diagnostics remain distinct from Cook's distance, leave-one-pair-out influence and surrogate associations.",
    },
}

_SUBTYPE_AUDIT_IDS = {
    "bulk_mean_variance": ("B10",),
    "bulk_dispersion": ("B03", "B11", "B12", "B13"),
    "ambient_rna_diagnostics": ("S09",),
    "bulk_residual_diagnostics": ("S29",),
    "bulk_design_diagnostics": ("S29",),
    "spatial_expression": ("D21",),
    "spatial_tissue_overlay": ("D21",),
    "spatial_segmentation": ("D21",),
    "scatter_marginals": ("F18",),
    "measurement_correlation": ("Q10",),
}

_EXTENSION_NAMES = {
    "sample_mds": "Sample MDS",
    "batch_bioconservation": "Batch integration / biological-conservation diagnostics",
    "motif_enrichment": "Promoter motif enrichment",
    "bulk_library_qc": "Bulk sample library QC",
    "bulk_normalization_qc": "Bulk normalization QC",
    "pvalue_distribution": "P-value distributions",
    "independent_filter_sensitivity": "Independent-filter sensitivity",
    "cross_method_concordance": "Cross-method concordance",
    "wald_lrt_concordance": "Wald / LRT concordance",
    "method_set_upset": "Method selection-set intersections",
    "method_dispositions": "Method decision dispositions",
    "classifier_confidence": "Classifier confidence distributions",
    "regulatory_network": "Regulatory association networks",
    "repertoire_vdj": "Repertoire / V(D)J views",
    "multimodal": "Multimodal views",
    "operational_resource": "Operational resource measurements",
    "execution_dag": "Execution dependency graph",
    "scatter_marginals": "Scatter marginal distributions",
    "measurement_correlation": "Measured-variable correlation matrix",
    "scrna_population_prefit": "Supplied-population pre-fit diagnostics",
}

_VERIFICATION_CHECKS = frozenset({"source_binding", "axis_units", "full_range", "browser_rendering", "exports"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_OPERATIONS = ("inspect_evidence", "presentation_revision", "portable_render_replay")


def scientific_plot_workspace_ref(workspace):
    """Return a consistency binding after the trusted reader has checked custody.

    The digest covers sorted, compact UTF-8 JSON, not the bytes of a source file.
    This function validates the workspace schema; it does not authenticate source
    files or create software verification evidence. Do not accept a reference
    from untrusted artifact metadata in place of a trusted reader's reference.
    """
    from .scientific_plot_contract import validate_workspace

    validate_workspace(workspace)
    raw = json.dumps(workspace, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return {
        "format": "devbench.scientific-plot-workspace-ref/1",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "digest_encoding": "sorted_compact_utf8_json",
        "source": deepcopy(workspace["source"]),
    }


def _bound_panels(workspace, workspace_ref, registered):
    if workspace is None:
        if workspace_ref is not None:
            raise ValueError("scientific_plot_workspace_binding_requires_workspace")
        return {}, None
    if workspace_ref is None:
        raise ValueError("scientific_plot_workspace_binding_required")
    actual = scientific_plot_workspace_ref(workspace)
    if workspace_ref != actual:
        raise ValueError("scientific_plot_workspace_binding_mismatch")
    grouped = {}
    for panel in workspace["panels"]:
        capability = panel["capability"]
        if capability not in registered:
            raise ValueError("scientific_plot_workspace_renderer_not_implemented:" + capability)
        grouped.setdefault(capability, []).append(panel)
    return grouped, actual


def _verification_records(records, implementation_sha256, registered):
    """Validate explicit reader-supplied evidence, never a capability-name flag."""
    if implementation_sha256 is not None and (not isinstance(implementation_sha256, str)
                                             or not _SHA256.fullmatch(implementation_sha256)):
        raise ValueError("scientific_plot_verification_implementation_binding_invalid")
    output = {}
    for record in records:
        required = {"capability", "scope", "status", "tested_workspace_sha256", "source_kind",
                    "implementation_sha256", "checks", "evidence_refs"}
        if not isinstance(record, dict) or set(record) != required:
            raise ValueError("scientific_plot_verification_record_invalid")
        capability = record["capability"]
        if (not isinstance(capability, str) or capability not in registered or capability in output
                or implementation_sha256 is None or record["implementation_sha256"] != implementation_sha256
                or not isinstance(record["tested_workspace_sha256"], str)
                or not _SHA256.fullmatch(record["tested_workspace_sha256"])
                or record["source_kind"] not in {"synthetic", "retained"}
                or record["scope"] != "software_rendering" or record["status"] != "passed"):
            raise ValueError("scientific_plot_verification_binding_invalid")
        checks = record["checks"]
        if (not isinstance(checks, dict) or not _VERIFICATION_CHECKS <= checks.keys()
                or any(value != "passed" for value in checks.values())):
            raise ValueError("scientific_plot_verification_checks_incomplete")
        references = record["evidence_refs"]
        if not isinstance(references, list) or not references:
            raise ValueError("scientific_plot_verification_evidence_required")
        for reference in references:
            if (not isinstance(reference, dict) or set(reference) != {"path", "sha256"}
                    or not isinstance(reference["path"], str) or not reference["path"].strip()
                    or not isinstance(reference["sha256"], str) or not _SHA256.fullmatch(reference["sha256"])):
                raise ValueError("scientific_plot_verification_evidence_invalid")
        output[capability] = deepcopy(record)
    return output


def scientific_plot_catalog(workspace=None, *, workspace_ref=None, implementation_sha256=None,
                            verification_evidence=(), requested_capabilities=()):
    """Describe exact builders and independent source/verification eligibility.

    This function performs no fitting and reads no paths. The trusted service
    supplies ``workspace_ref`` after source membership/hash checks, the current
    ``implementation_sha256`` over its plotting implementation, and the evidence
    ledger after the named software checks. Evidence binds the implementation
    and the tested fixture, independently from the current dataset. A validated
    workspace alone establishes data availability, not verified rendering.
    """
    # Keep imports lazy: the suite imports the adapters and their shared contract.
    from .scientific_plot_suite import capability_catalog

    registered = {row["id"]: row for row in capability_catalog()}
    unmapped = registered.keys() - _SCIENTIFIC_SUBTYPES.keys()
    if unmapped:
        raise ValueError("scientific_plot_catalog_mapping_required:" + ",".join(sorted(unmapped)))
    gaps = {name: value for name, value in _NAMED_GAPS.items()
            if not all(capability in registered for capability in value["implemented_by"])}
    panels, binding = _bound_panels(workspace, workspace_ref, registered)
    evidence = _verification_records(verification_evidence, implementation_sha256, registered)
    requested = set(requested_capabilities)
    if any(not isinstance(name, str) or not name for name in requested):
        raise ValueError("scientific_plot_requested_capability_invalid")
    records = []
    for name in sorted(set(_SCIENTIFIC_SUBTYPES) | set(gaps) | requested):
        implementation = registered.get(name)
        family_ids, extensions = _SCIENTIFIC_SUBTYPES.get(name, ((), ()))
        gap = _NAMED_GAPS.get(name, {})
        family_ids = gap.get("canonical_ids", family_ids)
        supplied_panels = panels.get(name, [])
        available_panels = [p["id"] for p in supplied_panels
                            if p["availability"]["status"] == "available" and p["marks"]]
        available = bool(available_panels)
        required = list(implementation["required_inputs"]) if implementation else []
        missing = [] if supplied_panels else list(required)
        verified = name in evidence
        if implementation is None:
            data_reason = reason = "renderer_not_implemented"
            state = "not_yet_implemented"
        elif not available:
            data_reason = reason = "required_saved_inputs_absent" if not supplied_panels else "no_renderable_saved_values"
            state = "implemented_but_unavailable"
        else:
            data_reason = None
            reason = None if verified else "software_verification_required"
            state = "implemented_and_verified" if verified else "implemented_unverified"
        records.append({
            "id": name, "canonical_ids": list(family_ids), "extension_ids": list(extensions),
            "extensions": [{"id": value, "name": _EXTENSION_NAMES[value]} for value in extensions],
            "description": implementation["description"] if implementation else gap.get("description", "No registered saved-data plotting adapter."),
            "required_inputs": required, "optional_inputs": list(implementation["optional_inputs"]) if implementation else [],
            "implementation_status": "implemented" if implementation else "not_yet_implemented",
            "verification_status": "verified" if verified else "not_verified",
            "verification_evidence": deepcopy(evidence.get(name)),
            "implementation_verification": {"status": "verified" if verified else "not_verified",
                                            "implementation_sha256": implementation_sha256,
                                            "evidence": deepcopy(evidence.get(name))},
            "status": state, "implementation_scope": "listed_subtype_only",
            "audit_requirement_ids": list(gap.get("audit_requirement_ids", _SUBTYPE_AUDIT_IDS.get(name, ()))),
            "data_availability": {"available": available, "reason": data_reason, "missing_inputs": missing,
                                  "panel_ids": [p["id"] for p in supplied_panels], "available_panel_ids": available_panels,
                                  "workspace_sha256": binding["sha256"] if supplied_panels else None},
            "eligibility": {"eligible": available and verified, "reason": reason, "missing_inputs": list(missing)},
            "allowed_operations": list(_OPERATIONS) if available and verified else [],
            "scientific_validity_established": False,
        })
    return records


def figure_catalog(available_families=(), capabilities=(), *, scientific_workspace=None,
                   scientific_workspace_ref=None, implementation_sha256=None, verification_evidence=()):
    """Return fresh requirements and current eligibility for all 22 families.

    ``available_families`` retains the verified legacy G02/G08 reader interface.
    Claimed ``capabilities`` only improve generic missing-input explanations.
    New eligibility is per subtype, from a bound workspace and explicit software
    evidence; a single subtype does not assert a complete broad family.
    """
    available, supplied = set(available_families), set(capabilities)
    scientific = scientific_plot_catalog(scientific_workspace, workspace_ref=scientific_workspace_ref,
                                        implementation_sha256=implementation_sha256, verification_evidence=verification_evidence)
    records = []
    for family, name, required, units, stage, producer, limits in _FAMILIES:
        legacy = family in {"G02", "G08"}
        subtypes = [row for row in scientific if family in row["canonical_ids"]]
        implemented_subtypes = [row["id"] for row in subtypes if row["implementation_status"] == "implemented"]
        legacy_subtypes = ["qc_scatter"] if family == "G02" else ["umap"] if family == "G08" else []
        eligible_subtypes = [row["id"] for row in subtypes if row["eligibility"]["eligible"]]
        legacy_eligible = legacy and family in available
        if legacy_eligible:
            eligible_subtypes.extend(legacy_subtypes)
        implemented = legacy or bool(implemented_subtypes)
        eligible = bool(eligible_subtypes)
        missing = [] if eligible else [item for item in required if item not in supplied]
        if eligible:
            reason = None
        elif any(row["data_availability"]["available"] for row in subtypes):
            reason, missing = "software_verification_required", []
        elif legacy:
            reason = "verified_figure_artifact_required"
            missing.append("verified_figure_artifact")
        else:
            reason = "missing_required_inputs" if missing else "verified_scientific_plot_workspace_required" if implemented else "renderer_not_implemented"
        records.append({
            "id": family,
            "name": name,
            "required_inputs": list(required),
            "statistical_units": list(units),
            "planned_stage": stage,
            "producer_status": "saved_input_plot_adapters_available" if implemented_subtypes else producer,
            "legacy_producer_status": producer,
            "renderer_status": "implemented" if implemented else "planned",
            "verification_level": "verified_figure_artifact" if legacy_eligible else "verified_scientific_plot_workspace" if eligible else "requirements_only",
            "eligibility": {"eligible": eligible, "reason": reason, "missing_inputs": missing},
            "allowed_operations": list(_OPERATIONS) if eligible else [],
            "supported_subtypes": legacy_subtypes + implemented_subtypes,
            "verified_subtypes": ([row["id"] for row in subtypes if row["verification_status"] == "verified"]
                                  + (legacy_subtypes if legacy_eligible else [])),
            "eligible_subtypes": eligible_subtypes,
            "unimplemented_subtypes": [row["id"] for row in subtypes if row["implementation_status"] == "not_yet_implemented"],
            "implementation_scope": "listed_subtypes_only",
            "subtypes": subtypes,
            "limitations": limits,
        })
    return records
