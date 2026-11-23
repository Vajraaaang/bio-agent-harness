"""Compatibility import for the shared numerical worker supervisor."""
import sys

from devharness_core import watchdog

sys.modules[__name__] = watchdog
