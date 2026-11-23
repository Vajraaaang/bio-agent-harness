"""Compatibility alias for the optional evaluation runner."""
import sys

from devbench.evaluation import runner

sys.modules[__name__] = runner
