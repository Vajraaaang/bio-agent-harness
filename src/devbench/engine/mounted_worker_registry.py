"""Trusted daemon declarations for digest-pinned, mounted Tier 1 workers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from types import MappingProxyType

from devbench.core.execution_contracts import ResourceProfile
from devharness_controlled.native_contracts import M2_CUDA_IMAGES, M2_QC_IMAGES


@dataclass(frozen=True)
class MountedWorker:
    module: str
    ceiling: ResourceProfile
    defaults: ResourceProfile = field(default_factory=ResourceProfile)
    # A local callable must accept (inputs, outputs, request_sha256).
    local_function: str | None = None
    image_ids: frozenset[str] = frozenset()
    local_options: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)+", self.module):
            raise ValueError("Invalid registered worker module")
        if self.local_function is not None and not self.local_function.isidentifier():
            raise ValueError("Invalid registered local worker function")
        if not self.defaults.within(self.ceiling):
            raise ValueError("Worker defaults exceed the registry resource ceiling")
        if not isinstance(self.image_ids, frozenset) or any(
            not re.fullmatch("sha256:[a-f0-9]{64}", value) for value in self.image_ids
        ):
            raise ValueError("Invalid registered worker image pins")


class MountedWorkerRegistry:
    """Constructed by trusted daemon setup, never from a run's script or parameters."""

    def __init__(self, workers: Iterable[MountedWorker]):
        entries = {}
        for worker in workers:
            if worker.module in entries:
                raise ValueError("Duplicate mounted worker")
            entries[worker.module] = worker
        self.workers = MappingProxyType(entries)

    def worker(self, module: str) -> MountedWorker:
        try:
            return self.workers[module]
        except KeyError as error:
            raise ValueError(
                "Mounted Tier A requires a registered worker module"
            ) from error

    def admit(self, module, profile, image_id, *, local=False):
        worker = self.worker(module)
        if not isinstance(profile, ResourceProfile) or not profile.within(
            worker.ceiling
        ):
            raise ValueError("Worker request exceeds the registry resource ceiling")
        if not local and worker.image_ids and image_id not in worker.image_ids:
            raise ValueError("Worker image differs from the registry pin")
        if local and worker.local_function is None:
            raise ValueError("Worker has no approved local implementation")
        return worker


# Installed declarations may be extended with a separately trusted registry at
# daemon startup. Each request still needs its own authorized digest and limits.
_CPU = ResourceProfile(32, 65536, 2048, 16384, 72 * 3600)
_SCRNA = ResourceProfile(32, 65536, 4096, 32768, 72 * 3600, gpu=True)
BUILTIN_WORKERS = MountedWorkerRegistry(
    (
        MountedWorker(
            "devbench.engine.mounted_population_qc",
            _CPU,
            local_function="run_mounted_population_qc",
        ),
        MountedWorker(
            "rna_kernels.mounted_bulk",
            _CPU,
            local_function="run_mounted_bulk",
            local_options=(("claim_scope", "checked_observation"),),
        ),
        MountedWorker(
            "rna_kernels.mounted_scrna", _SCRNA, local_function="run_mounted_scrna"
        ),
        MountedWorker(
            "rna_kernels.mounted_m2",
            _SCRNA,
            image_ids=frozenset((*M2_QC_IMAGES.values(), *M2_CUDA_IMAGES.values())),
        ),
        MountedWorker(
            "devharness_server.tools_scrna_composition",
            _CPU,
            local_function="run_mounted_composition",
        ),
        MountedWorker(
            "devharness_server.tools_rna_pathways",
            _CPU,
            local_function="run_mounted_pathways",
        ),
    )
)
