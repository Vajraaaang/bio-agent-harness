"""Nonadmitting preparation for a reviewed isolated Codex binding.

The configuration and expected root are trusted coordinator dependencies, never
fields accepted from a model or task request. A matching root protects custody;
it does not establish that its evidence is true. Independent review must first
qualify the complete actual boundary. This module cannot launch or admit jobs.
It deliberately has no authentication-cache path, reader, or credential hash.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat

from devharness_core.provenance import canonical_json
from devbench.study.evidence import decode_json


_FORMAT = "devbench.isolated-boundary-acceptance/1"
_SCOPE = "actual_official_chatgpt_device_flow_and_public_proxy"
_EVIDENCE = frozenset(("client", "configuration", "model_catalog", "policy",
    "signin", "public_auth_transport", "public_model_transport", "tool_denials",
    "enclosure", "guest_lifecycle"))
_BINDINGS = frozenset(("source", "runtime", "image", "client", "configuration",
    "model_catalog", "policy", "public_proxy"))
_FIELDS = {"format", "scope", "accepted_at", "expires_at", "model", "bindings", "evidence"}


def _sha(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise PermissionError("isolated_boundary_sha256_required")
    return value


def _absolute(value):
    path = Path(value)
    if (not path.is_absolute() or path.resolve() != path
            or any(p.is_symlink() for p in (path, *path.parents))):
        raise PermissionError("isolated_boundary_absolute_ordinary_path_required")
    return path


def _public_evidence(path, maximum):
    """Read only named redacted acceptance material in the protected root."""
    path = _absolute(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)
                or not 0 < info.st_size <= maximum):
            raise PermissionError("isolated_boundary_protected_bounded_evidence_required")
        body = stream.read(maximum + 1)
    if len(body) > maximum:
        raise PermissionError("isolated_boundary_evidence_bound")
    return body


@dataclass(frozen=True)
class TrustedIsolatedConfiguration:
    """Supplied only by trusted operator setup; this is not a qualification.

    The reserved workspace control directory must remain outside every model,
    scientific executor and artifact mount. The future launcher must verify
    that exclusion against actual mounts before it can be registered.
    """

    root: Path
    workspace_root: Path
    acceptance_sha256: str

    def __post_init__(self):
        root = _absolute(self.root)
        workspace = _absolute(self.workspace_root)
        if not workspace.is_dir():
            raise PermissionError("isolated_boundary_existing_workspace_required")
        control = workspace / ".devbench-native-control"
        if root.is_relative_to(workspace) and root != control:
            raise PermissionError("isolated_boundary_operator_control_root_required")
        info = root.stat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise PermissionError("isolated_boundary_protected_configuration_required")
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "workspace_root", workspace)
        _sha(self.acceptance_sha256)


def _timestamp(value):
    if type(value) is not str or not value.endswith("Z"):
        raise PermissionError("isolated_boundary_utc_timestamp_required")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise PermissionError("isolated_boundary_utc_timestamp_required") from error
    return result


def _validate_manifest(value, *, now):
    if (type(value) is not dict or set(value) != _FIELDS or value["format"] != _FORMAT
            or value["scope"] != _SCOPE):
        raise PermissionError("isolated_boundary_complete_actual_scope_required")
    if (now.tzinfo is None or not _timestamp(value["accepted_at"]) <= now
            < _timestamp(value["expires_at"])):
        raise PermissionError("isolated_boundary_acceptance_expired_or_future")
    if type(value["model"]) is not str or re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value["model"]) is None:
        raise PermissionError("isolated_boundary_explicit_model_required")
    if type(value["bindings"]) is not dict or set(value["bindings"]) != _BINDINGS:
        raise PermissionError("isolated_boundary_complete_bindings_required")
    for digest in value["bindings"].values():
        _sha(digest)
    evidence = value["evidence"]
    if type(evidence) is not dict or set(evidence) != _EVIDENCE:
        raise PermissionError("isolated_boundary_complete_actual_evidence_required")
    for name, descriptor in evidence.items():
        if (type(descriptor) is not dict or set(descriptor) != {"file", "sha256", "bytes"}
                or descriptor["file"] != name + ".evidence.json"
                or type(descriptor["bytes"]) is not int or not 0 < descriptor["bytes"] <= 1_000_000):
            raise PermissionError("isolated_boundary_redacted_evidence_descriptor_required")
        _sha(descriptor["sha256"])
    return value


def _current_runtime():
    # Fixed implementation, not a caller-supplied verifier or report dictionary.
    from .rna_trial_authority import trial_enforcement_source_digest, trial_enforcement_runtime_digest
    return trial_enforcement_source_digest(), trial_enforcement_runtime_digest()


@dataclass(frozen=True)
class NonAdmittingIsolatedPlan:
    """Custody-checked preparation only. No execute method or native admission."""

    configuration: TrustedIsolatedConfiguration
    model: str
    bindings: tuple[tuple[str, str], ...]

    def recheck(self):
        return prepare_isolated_plan(self.configuration)

    def require_native_admission(self):
        raise PermissionError("isolated_boundary_not_registered:reviewed_actual_launcher_required")


def prepare_isolated_plan(configuration):
    """Recheck the external accepted root without running any client or job.

    Evidence is approved redacted material only. Real device-flow completion,
    public transport and guest cleanup must all be reviewed before the operator
    supplies this root. Neither this function nor a successful test replaces
    that independent review. No accepted root is distributed with the package.
    """
    if type(configuration) is not TrustedIsolatedConfiguration:
        raise TypeError("exact_trusted_isolated_configuration_required")
    # Recheck storage protections; frozen Python data alone is not custody.
    TrustedIsolatedConfiguration(configuration.root, configuration.workspace_root, configuration.acceptance_sha256)
    body = _public_evidence(configuration.root / "acceptance.json", 100_000)
    if hashlib.sha256(body).hexdigest() != configuration.acceptance_sha256:
        raise PermissionError("isolated_boundary_acceptance_root_changed")
    value = _validate_manifest(decode_json(body), now=datetime.now(timezone.utc))
    if canonical_json(value) != body:
        raise PermissionError("isolated_boundary_canonical_acceptance_required")
    expected = {"acceptance.json", *(entry["file"] for entry in value["evidence"].values())}
    if {entry.name for entry in configuration.root.iterdir()} != expected:
        raise PermissionError("isolated_boundary_external_root_inventory_changed")
    for descriptor in value["evidence"].values():
        evidence = _public_evidence(configuration.root / descriptor["file"], 1_000_000)
        if len(evidence) != descriptor["bytes"] or hashlib.sha256(evidence).hexdigest() != descriptor["sha256"]:
            raise PermissionError("isolated_boundary_evidence_custody_changed")
    source, runtime = _current_runtime()
    if value["bindings"]["source"] != source or value["bindings"]["runtime"] != runtime:
        raise PermissionError("isolated_boundary_current_source_or_runtime_changed")
    return NonAdmittingIsolatedPlan(configuration, value["model"], tuple(sorted(value["bindings"].items())))
