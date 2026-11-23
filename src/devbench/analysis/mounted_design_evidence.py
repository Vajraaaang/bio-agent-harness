"""Produce structural design evidence without opening expression matrices."""

import csv
import io

from rna_kernels.mounted_bulk import _metadata


def mounted_design_evidence(spec, metadata, *, parameters_sha256):
    reader = csv.DictReader(io.StringIO(metadata.decode("utf-8")), strict=True)
    sample_ids = []
    for row in reader:
        sample_ids.append(row.get("sample_id"))
        if len(sample_ids) > 4096:
            raise ValueError("Mounted design exceeds its declared sample bound")
    if not sample_ids or None in sample_ids or len(set(sample_ids)) != len(sample_ids):
        raise ValueError("Mounted design needs distinct declared sample IDs")
    request = {
        "condition_column": spec.condition_column,
        "unit_column": spec.population_unit_column,
        "technical_factors": list(spec.design.technical_factors),
        "continuous_covariates": list(spec.continuous_covariates),
        "model": spec.mounted_model.request(),
        "numerator": spec.contrast.numerator,
        "denominator": spec.contrast.denominator,
    }
    _, condition_number, design = _metadata(metadata, sample_ids, request)
    samples, columns = design.design.shape
    return {
        "format": "devbench.mounted-design-evidence/1",
        "family": "Q-DESIGN",
        "parameters_sha256": parameters_sha256,
        "metadata_sha256": spec.metadata_sha256,
        "expression_sha256": spec.expression_sha256,
        "scope": "declared_metadata_axis_structural_preflight",
        "metrics": {
            "rank": columns,
            "columns": columns,
            "residual_df": samples - columns,
            "condition_number": condition_number,
            "estimable": True,
            "profile_count": samples,
            "declared_unit_count": len(set(design.unit_ids)),
            "reduced_columns": None
            if design.reduced is None
            else design.reduced.shape[1],
        },
        "scientific_validity_established": False,
    }
