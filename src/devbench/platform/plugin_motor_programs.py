"""Compatibility import for the installed motor-programs example."""

import sys

from examples.motor_programs import programs

sys.modules[__name__] = programs
