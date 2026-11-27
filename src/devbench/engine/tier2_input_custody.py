"""Verify mountable content, including each file in a directory-format artifact."""

import hashlib
import json
import stat
from itertools import batched

from devbench.analysis.evidence_access import EvidenceBroker

from .partition_authority import embargoed

# These exact bytes carry no specimen, schema, chunk, shape or attribute
# information. The tree root is always private. A separately embargoed marker
# is still refused, including when a different tree reuses its bytes.
_EMPTY_MARKERS = {
    ".zattrs": {hashlib.sha256(raw).hexdigest() for raw in (b"{}", b"{}\n")},
    ".zgroup": {
        hashlib.sha256(raw).hexdigest()
        for raw in (
            b'{"zarr_format":2}',
            b'{"zarr_format": 2}',
            b'{\n    "zarr_format": 2\n}',
            b'{\n  "zarr_format": 2\n}',
            b'{\n  "zarr_format": 2\n}\n',
        )
    },
}


def artifact_digests(inventory, *, marking_private=False):
    yield inventory["sha256"]
    for member in inventory.get("entries", ()):
        if member["kind"] == "file":
            name = member["name"].rsplit("/", 1)[-1]
            if marking_private and member["sha256"] in _EMPTY_MARKERS.get(name, ()):
                continue
            yield member["sha256"]


def guard_digests(db, digests, *, private):
    for identities in batched(dict.fromkeys(digests), 512):
        rows = db.execute(
            "SELECT DISTINCT record_id FROM scientific_records "
            "WHERE kind='execution_event' "
            "AND record_id IN (SELECT 'embargo-' || value FROM json_each(?))",
            (json.dumps(identities),),
        )
        if any(embargoed(db, name.removeprefix("embargo-")) for (name,) in rows):
            raise PermissionError(
                "An embargoed digest cannot enter exploratory execution"
            )
        if not private and EvidenceBroker.has_private_content(db, identities):
            raise PermissionError("private_data_local_studio_only")


def stage_identity(path):
    info = path.lstat()
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise ValueError(
            "A matrix mount requires a canonical regular file or directory"
        )
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns]


def check_stage(vault, directory, pins, *, capture_mount=False):
    for port, pin in pins.items():
        staged = directory / "verified-inputs" / port
        actual = directory / "inputs" / port
        sha = pin["source"]["full_or_merkle_sha256"]
        if pin["stage_stat"] != stage_identity(staged):
            raise ValueError("Exploratory matrix stage changed")
        vault.verify_staged(sha, staged, pin["source"])
        if capture_mount:
            if "mount_stat" in pin:
                raise ValueError("Exploratory matrix mount identity already recorded")
            pin["mount_stat"] = stage_identity(actual)
        if pin.get("mount_stat") != stage_identity(actual):
            raise ValueError("Exploratory matrix bind identity changed")
        vault.verify_staged(sha, actual, pin["source"])
