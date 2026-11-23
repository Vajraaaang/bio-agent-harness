"""Compatibility import for owned coordinator supervision."""
import sys

from devbench.engine import supervision

sys.modules[__name__] = supervision
