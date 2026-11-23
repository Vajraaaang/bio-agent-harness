"""Compatibility import for population admission and preflight."""
import sys

from devbench.platform import scrna_population_admission

sys.modules[__name__] = scrna_population_admission
