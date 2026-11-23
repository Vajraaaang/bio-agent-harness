"""Compatibility import for engine episode_codex."""
import sys
from typing import TYPE_CHECKING

from devbench.engine import episode_codex

if TYPE_CHECKING:
    from devbench.engine.episode_codex import (
        CodexActionSelector as CodexActionSelector,
        _METHOD_PLANNER_CONTRACTS as _METHOD_PLANNER_CONTRACTS,
        _canonical as _canonical,
        _read_evidence as _read_evidence,
        analyst_action_schema as analyst_action_schema,
        selection_contract as selection_contract,
        selection_instruction as selection_instruction,
    )

sys.modules[__name__] = episode_codex
