"""Wire values and execution limits shared by capability discovery and the runners."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

ClaimTier = Literal[
    "none",
    "L1_execution_verified",
    "L2_statistically_calibrated",
    "L3_scientifically_supported",
]
RepresentationClass = Literal[
    "raw_reads_or_droplets",
    "count_1_raw_counts",
    "ambient_adjusted_counts",
    "size_factor_normalized",
    "integrated_latent_or_graph",
    "spatial_geometry_and_residuals",
    "aggregated_unit_counts",
    "derived_summary",
    "metadata_only",
    "reference_only",
]
AxisA = Literal["A0", "A1", "A2", "A3"]
AxisB = Literal[
    "B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "B10", "B11", "B12", "B_OPEN"
]
AxisC = Literal["C1", "C2"]


@dataclass(frozen=True)
class ResourceProfile:
    """Limits for one worker, checked against its trusted registry declaration."""

    cpus: float = 2
    memory_mb: int = 1024
    pids_limit: int = 64
    scratch_mb: int = 64
    timeout_sec: int = 300
    gpu: bool = False

    def __post_init__(self) -> None:
        if type(self.cpus) not in (int, float) or not math.isfinite(self.cpus):
            raise ValueError("CPU quota must be a finite positive number")
        if self.cpus <= 0 or type(self.gpu) is not bool:
            raise ValueError("Invalid CPU or GPU resource request")
        for limit in (
            self.memory_mb,
            self.pids_limit,
            self.scratch_mb,
            self.timeout_sec,
        ):
            if type(limit) is not int or limit < 1:
                raise ValueError("Worker limits must be positive integers")

    def within(self, ceiling: ResourceProfile) -> bool:
        return (
            self.cpus <= ceiling.cpus
            and self.memory_mb <= ceiling.memory_mb
            and self.pids_limit <= ceiling.pids_limit
            and self.scratch_mb <= ceiling.scratch_mb
            and self.timeout_sec <= ceiling.timeout_sec
            and (not self.gpu or ceiling.gpu)
        )


@runtime_checkable
class ContainerBackend(Protocol):
    """Daemon-side mounted worker interface. Receipts report actual isolation."""

    def probe(self) -> dict[str, Any]: ...

    def verify_image(self, image_id: str) -> str: ...

    def run_isolated(
        self,
        stage: Path,
        *,
        request_sha: str,
        image_id: str,
        attempt_id: str,
        worker_module: str,
        resource_profile: ResourceProfile,
        data_classification: str,
        claim_tier: ClaimTier,
        epistemic_purpose: str,
    ) -> dict[str, Any]: ...

    def cleanup(
        self, stage: Path, *, request_sha: str, image_id: str, attempt_id: str
    ) -> dict[str, Any]: ...
