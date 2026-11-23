"""Compatibility import for the shared normalization implementation."""
import sys
from typing import TYPE_CHECKING

from rna_kernels import normalization

if TYPE_CHECKING:
    from rna_kernels.normalization import (
        MAX_BLOCK_BYTES as MAX_BLOCK_BYTES,
        MAX_PANEL_BYTES as MAX_PANEL_BYTES,
        MIN_NORMALIZED_MEAN_COUNT as MIN_NORMALIZED_MEAN_COUNT,
        calibrate_count_family as calibrate_count_family,
        calibrate_counts as calibrate_counts,
        calibration_panel as calibration_panel,
        count_like as count_like,
        expression_summary as expression_summary,
        log1p_counts_per_10k as log1p_counts_per_10k,
        low_count_mask as low_count_mask,
        median_ratio_factors as median_ratio_factors,
        select_calibration_panel_indices as select_calibration_panel_indices,
    )

sys.modules[__name__] = normalization
