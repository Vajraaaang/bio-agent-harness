"""Shared immutable runtime limits and study-compatible JSON encoding."""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from devbench.core._hashing import contract_json_bytes as canonical_json_bytes


class FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True,
                              validate_default=True, allow_inf_nan=False)


class RunBudget(FrozenContract):
    """Identical resource ceilings for every study condition."""

    timeout_sec: Annotated[int, Field(ge=1, le=86_400)] = 600
    cpu_threads: Annotated[int, Field(ge=1, le=128)] = 2
    max_memory_mb: Annotated[int, Field(ge=64, le=1_048_576)] = 4096
    token_budget: Annotated[int, Field(ge=1, le=1_000_000_000)] | None = None
