"""Immutable, local-only scientific image catalog for governed exploration.

Scientific references are catalog keys binding an architecture to a Docker image
configuration digest (which also commits to all layer diff IDs). They do not
require a registry tag or pull. Historical plans pin a registry manifest *and*
its separately recorded image ID.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from types import MappingProxyType

EXPLORATORY_IMAGE = (
    "python@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159"
)
EXPLORATORY_IMAGE_ID = (
    "sha256:ff710677e697054c516f12c53c92166f1aef075f21b2a593f4c615e522112b4e"
)


@dataclass(frozen=True)
class ExploratoryImage:
    base_image_id: str
    image_id: str
    language: str
    executable: str
    architecture: str
    legacy: bool = False

    @property
    def reference(self) -> str:
        if self.legacy:
            return EXPLORATORY_IMAGE
        return f"{self.base_image_id}@{self.image_id}"


_PINS = (
    ExploratoryImage(
        "devharness-explore-py",
        "sha256:7691a936b4be9151d22c73a2430be21581810ae6baa6e11adb7aba727332e7f7",
        "python",
        "python3.13",
        "arm64",
    ),
    ExploratoryImage(
        "devharness-explore-py",
        "sha256:4a66b4c2fffc074f7731e98206e3bfd5981d36b59aa136ec6fb2a154ec2ea270",
        "python",
        "python3.13",
        "amd64",
    ),
    ExploratoryImage(
        "devharness-explore-r",
        "sha256:ad182b2b28b051d0bde455f0efe63106efef40ad75cab48e518b04128e6957de",
        "r",
        "Rscript",
        "arm64",
    ),
    ExploratoryImage(
        "devharness-explore-r",
        "sha256:0cfadd399855b8fd9a45a047519a64787330660ced53f6d3d9cb1a0fd5186154",
        "r",
        "Rscript",
        "amd64",
    ),
)
_LEGACY = ExploratoryImage(
    "legacy-python", EXPLORATORY_IMAGE_ID, "python", "python", "arm64", legacy=True
)
_ARCH = {"aarch64": "arm64", "x86_64": "amd64"}.get(
    platform.machine().lower(), platform.machine().lower()
)
EXPLORATORY_IMAGES = MappingProxyType(
    {pin.base_image_id: pin for pin in _PINS if pin.architecture == _ARCH}
)


def exploratory_image(
    image_or_alias: str, *, language: str | None = None
) -> ExploratoryImage:
    """Resolve aliases at freeze time and exact content pins during replay."""
    pin = EXPLORATORY_IMAGES.get(image_or_alias)
    if pin is None:
        pin = next(
            (
                p
                for p in (*_PINS, _LEGACY)
                if image_or_alias in (p.reference, p.image_id)
            ),
            None,
        )
    if pin is None or (language is not None and pin.language != language):
        raise ValueError(
            "Exploratory execution requires a curated image for the script language"
        )
    return pin
