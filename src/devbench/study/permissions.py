"""Compatibility import for the engine permission boundary."""
import sys
from typing import TYPE_CHECKING

from devbench.engine import permissions

if TYPE_CHECKING:
    from devbench.engine.permissions import (
        PermissionBoundary as PermissionBoundary,
        _toml as _toml,
        platform_scratch_roots as platform_scratch_roots,
        probe_boundary as probe_boundary,
        require_protected_storage as require_protected_storage,
    )

sys.modules[__name__] = permissions
