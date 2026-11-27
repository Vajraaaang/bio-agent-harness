"""Compatibility import for the installed motor-programs example."""

import sys
from typing import TYPE_CHECKING

from examples.motor_programs import programs

if TYPE_CHECKING:
    from examples.motor_programs.programs import (
        motor_program_runtime as motor_program_runtime,
    )
    from examples.motor_programs.programs import (
        run_motor_program_comparison as run_motor_program_comparison,
    )
    from examples.motor_programs.programs import (
        validate_motor_program_profiles as validate_motor_program_profiles,
    )

sys.modules[__name__] = programs
