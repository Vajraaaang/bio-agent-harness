"""RNA-seq graph requirements, separate from verified artifact availability.

Capabilities describe supplied inputs; they do not validate an assay, implement
a renderer or authorize a scientific calculation. Only a verified current figure
may enable M2a presentation operations. No numerical work occurs in this module.
"""
from __future__ import annotations


# id, name, required inputs, units, first planned stage, producer status, limits.
# Existing outputs are deliberately distinguished from a complete graph adapter.
_FAMILIES = (
    ("G01", "Library-size / detected-gene / mitochondrial-QC distributions",
     ("cell_qc_metrics", "cell_ids", "qc_filter_membership"), ("cell",), "M2b",
     "existing_qc_metrics",
     "Distribution bins/density and exact membership adapters remain unavailable; no universal QC cutoff."),
    ("G02", "QC scatterplots",
     ("qc_coordinates", "cell_ids", "qc_semantics"), ("cell",), "M2a",
     "existing_qc_metrics",
     "Count source, transformations and zero/missing semantics belong to the verified figure; linked selection is M2b."),
    ("G03", "Doublet / ambient-RNA diagnostics",
     ("doublet_scores", "method_metadata", "cell_ids"), ("cell",), "M2b",
     "partial_doublet_outputs",
     "Doublet flags are not definitive identities; ambient-RNA producer and view adapters remain unavailable."),
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
     "Current latent scores lack a saved explained-variance artifact; sample PCA additionally requires the G04 contract."),
    ("G08", "UMAP / t-SNE",
     ("embedding_coordinates", "cell_ids", "embedding_configuration", "representation_metadata"),
     ("cell",), "M2a", "umap_only",
     "Only UMAP has a current producer. t-SNE remains unavailable. Coordinates do not establish global distances or biological validation."),
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
     "Candidate enrichment tools do not establish a compatible pathway producer; membership and scores support associations, not causal activation."),
    ("G18", "Pseudotime / trajectory plots",
     ("fitted_trajectory", "observation_ids", "root_and_branch_metadata", "trajectory_model_metadata"),
     ("cell", "observation"), "E1", "dedicated_contract_required",
     "Temporal tools require a dedicated workbench adapter; inferred pseudotime is distinct from collection age, elapsed time and proven lineage."),
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


def figure_catalog(available_families=(), capabilities=()):
    """Return fresh requirements and current eligibility for all 22 families.

    ``available_families`` must come from verified figure artifacts, not from
    requested graph names. ``capabilities`` only improves missing-input reasons;
    claimed prerequisites never promote an unimplemented renderer.
    """
    available, supplied = set(available_families), set(capabilities)
    records = []
    for family, name, required, units, stage, producer, limits in _FAMILIES:
        implemented = family in {"G02", "G08"}
        eligible = implemented and family in available
        missing = [] if eligible else [item for item in required if item not in supplied]
        if eligible:
            reason = None
        elif implemented:
            reason = "verified_figure_artifact_required"
            missing.append("verified_figure_artifact")
        else:
            reason = "missing_required_inputs" if missing else "renderer_not_implemented"
        records.append({
            "id": family,
            "name": name,
            "required_inputs": list(required),
            "statistical_units": list(units),
            "planned_stage": stage,
            "producer_status": producer,
            "renderer_status": "implemented" if implemented else "planned",
            "verification_level": "verified_figure_artifact" if eligible else "requirements_only",
            "eligibility": {"eligible": eligible, "reason": reason, "missing_inputs": missing},
            "allowed_operations": ["inspect_evidence", "presentation_revision", "portable_render_replay"] if eligible else [],
            "supported_subtypes": ["qc_scatter"] if family == "G02" else ["umap"] if family == "G08" else [],
            "limitations": limits,
        })
    return records
