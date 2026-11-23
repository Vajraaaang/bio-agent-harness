"""Compatibility import for the core population study specification."""
import sys

from devbench.core import population_contract

sys.modules[__name__] = population_contract
