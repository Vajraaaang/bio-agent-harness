"""Bounded Python Code-as-Action API for an assigned participant workspace.

``DEVHARNESS_WORKSPACE_ROOT`` overrides the cwd. Supervised tools use spawn:
put calls in a module-level ``if __name__ == '__main__':`` guard in scripts.
SDK receipts in ``.devharness_trace.jsonl`` are participant-controlled debugging
records, not authenticated tool evidence or a compliance certificate.
"""
from devharness_core.storage import ChunkedExpressionStore

from . import data, stat_models, trajectory, watchdog
from .data import open_expression, read_metadata
from .stat_models import fit_trajectory, validate_design
from .trajectory import compact_manifold, infer_lineage
from .watchdog import audit_quality
from ._discovery import save_discovery

__all__ = ["ChunkedExpressionStore", "data", "stat_models", "trajectory", "watchdog",
           "open_expression", "read_metadata", "validate_design", "fit_trajectory",
           "compact_manifold", "infer_lineage", "audit_quality", "save_discovery"]
