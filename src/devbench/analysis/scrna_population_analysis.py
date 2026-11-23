"""Compatibility import for the platform supplied-population tool."""
import sys

from devbench.platform import scrna_population_analysis

sys.modules[__name__] = scrna_population_analysis
