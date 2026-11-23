"""Strict records and canonical digests shared by scientific kernels."""

from hashlib import sha256
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from ._hashing import contract_json_bytes

Identifier = Annotated[
    str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Probability = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]


class FrozenContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
        allow_inf_nan=False,
    )


def content_sha256(value: Any) -> str:
    return sha256(contract_json_bytes(value)).hexdigest()
