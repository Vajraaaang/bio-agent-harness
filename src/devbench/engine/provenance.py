"""Compatibility import for the shared execution provenance DAG."""
import sys
from typing import TYPE_CHECKING

from devharness_core import provenance

if TYPE_CHECKING:
    from devharness_core.provenance import ArtifactLimitError as ArtifactLimitError
    from devharness_core.provenance import ExecutionDAG as ExecutionDAG
    from devharness_core.provenance import analysis_fingerprint as analysis_fingerprint
    from devharness_core.provenance import canonical_json as canonical_json
    from devharness_core.provenance import copy_artifact as copy_artifact
    from devharness_core.provenance import evaluate_controls as evaluate_controls
    from devharness_core.provenance import hash_artifact as hash_artifact
    from devharness_core.provenance import verify_execution as verify_execution

sys.modules[__name__] = provenance
