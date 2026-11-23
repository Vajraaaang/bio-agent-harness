"""Pure deterministic gates; never shipped into participant environments."""
from .math_invariants import grade_design_matrix_gate, grade_fdr_invariant

__all__ = ["grade_design_matrix_gate", "grade_fdr_invariant"]
