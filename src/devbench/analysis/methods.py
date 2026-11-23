"""Compatibility import for the engine method service."""
import sys
from typing import TYPE_CHECKING

from devbench.engine import methods

if TYPE_CHECKING:
    from devbench.engine.methods import (
        MethodService as MethodService,
        _digest as _digest,
        _label as _label,
        _now as _now,
        _reason as _reason,
    )

sys.modules[__name__] = methods
