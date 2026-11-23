"""Compatibility import for the shared units implementation."""
import sys
from typing import TYPE_CHECKING

from rna_kernels import units

if TYPE_CHECKING:
    from rna_kernels.units import (
        BIOLOGICAL_UNIT_COLUMNS as BIOLOGICAL_UNIT_COLUMNS,
        CANDIDATE_BIOLOGICAL_UNIT_COLUMNS as CANDIDATE_BIOLOGICAL_UNIT_COLUMNS,
        UNKNOWN_UNIT_MARKERS as UNKNOWN_UNIT_MARKERS,
        biological_identity_audit as biological_identity_audit,
        declared_unit_counts as declared_unit_counts,
        validate_expression_selection as validate_expression_selection,
    )

sys.modules[__name__] = units
