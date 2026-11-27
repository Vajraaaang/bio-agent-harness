"""Compatibility import for the installed motor-programs example."""

import sys
from typing import TYPE_CHECKING

from examples.motor_programs import umap

if TYPE_CHECKING:
    from examples.motor_programs.umap import (
        _check_scope as _check_scope,
    )
    from examples.motor_programs.umap import (
        _studio_receipt as _studio_receipt,
    )
    from examples.motor_programs.umap import (
        _studio_record as _studio_record,
    )
    from examples.motor_programs.umap import (
        plan_shared_motor_umap as plan_shared_motor_umap,
    )
    from examples.motor_programs.umap import (
        run_shared_motor_umap_plan as run_shared_motor_umap_plan,
    )
    from examples.motor_programs.umap import (
        studio_code_artifact as studio_code_artifact,
    )
    from examples.motor_programs.umap import (
        studio_launch as studio_launch,
    )

sys.modules[__name__] = umap
