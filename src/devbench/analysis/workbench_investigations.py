"""Compatibility import for the Workbench investigation controller."""
import sys

from devbench.platform import workbench_investigations

sys.modules[__name__] = workbench_investigations
