"""Compatibility import for the engine method executor."""
import sys
from typing import TYPE_CHECKING

from devbench.engine import method_executor

if TYPE_CHECKING:
    from devbench.engine.method_executor import (
        EXPLORATORY_IMAGE as EXPLORATORY_IMAGE,
        EXPLORATORY_IMAGE_ID as EXPLORATORY_IMAGE_ID,
        LocalDockerExecutor as LocalDockerExecutor,
        _path as _path,
        container_command as container_command,
    )

sys.modules[__name__] = method_executor
