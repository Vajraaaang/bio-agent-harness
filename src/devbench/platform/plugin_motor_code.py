"""Compatibility import for the installed motor-programs example."""

import sys

from examples.motor_programs import code

sys.modules[__name__] = code
