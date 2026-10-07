"""Configuration-only permission contracts; no data or sandbox processes."""
import importlib.util
from pathlib import Path
import tomllib

import pytest


def module():
    assert importlib.util.find_spec("devbench.study.permissions"), "Study permissions module is missing"
    from devbench.study import permissions
    return permissions


def boundary():
    return module().PermissionBoundary(
        workspace=Path("/opt/devbench/participants/run_1"),
        private_roots=(Path("/opt/devbench/coordinator"),),
        runtime_roots=(Path("/opt/python/lib"),),
        protected_paths=(Path("/opt/devbench/participants/run_1/inputs"),),
        socket_path=Path("/opt/devbench/participants/run_1/control/tools.sock"),
    )


def test_profile_denies_ambient_reads_and_grants_only_declared_paths():
    settings=tomllib.loads("\n".join(boundary().config_overrides()))
    profile=settings["permissions"]["devbench_study"]
    fs=profile["filesystem"]
    assert fs[":root"] == "deny"
    assert fs[":minimal"] == "read"
    assert fs["/opt/devbench/coordinator"] == "deny"
    assert fs["/opt/devbench/participants/run_1"] == "write"
    assert fs["/opt/devbench/participants/run_1/inputs"] == "read"
    assert fs["/opt/devbench/participants/run_1/control"] == "read"
    assert settings["approval_policy"] == "never"
    assert settings["web_search"] == "disabled"
    assert profile["network"]["enabled"] is True
    assert profile["network"]["domains"] == {}
    assert profile["network"]["allow_upstream_proxy"] is False
    assert settings["features"]["network_proxy"] is True


def test_runtime_grants_cannot_expose_private_state():
    with pytest.raises(ValueError, match="private"):
        module().PermissionBoundary(workspace=Path("/opt/participant"),
            private_roots=(Path("/opt/private"),), runtime_roots=(Path("/opt"),))


def test_complete_stdlib_inventory_allows_listing_but_denies_unadmitted_packages(tmp_path, monkeypatch):
    from devbench.study.permissions import PermissionBoundary
    import sysconfig
    import tomllib
    stdlib = tmp_path / "python3.12"
    stdlib.mkdir()
    (stdlib / "encodings").mkdir()
    (stdlib / "os.py").write_text("# standard library fixture")
    for name in ("site-packages", "dist-packages", "__pycache__"):
        (stdlib / name).mkdir()
    monkeypatch.setattr(sysconfig, "get_path", lambda name: str(stdlib))
    boundary = PermissionBoundary(workspace=Path("/opt/devbench/participant"),
        runtime_roots=(stdlib / "encodings", stdlib / "os.py"))
    fs = tomllib.loads("\n".join(boundary.config_overrides()))["permissions"]["devbench_study"]["filesystem"]
    assert fs[str(stdlib)] == {".": "read", "site-packages": "deny", "dist-packages": "deny"}
    assert str(stdlib / "encodings") not in fs
    assert fs[":root"] == "deny"


def test_partial_stdlib_inventory_does_not_gain_parent_read(tmp_path, monkeypatch):
    from devbench.study.permissions import PermissionBoundary
    import sysconfig
    import tomllib
    stdlib = tmp_path / "python3.12"
    stdlib.mkdir()
    (stdlib / "encodings").mkdir()
    (stdlib / "unadmitted.py").write_text("# not admitted")
    monkeypatch.setattr(sysconfig, "get_path", lambda name: str(stdlib))
    boundary = PermissionBoundary(workspace=Path("/opt/devbench/participant"), runtime_roots=(stdlib / "encodings",))
    fs = tomllib.loads("\n".join(boundary.config_overrides()))["permissions"]["devbench_study"]["filesystem"]
    assert str(stdlib) not in fs


def test_protected_inputs_must_be_inside_the_participant_workspace():
    with pytest.raises(ValueError, match="workspace"):
        module().PermissionBoundary(workspace=Path("/opt/participant"),
            protected_paths=(Path("/opt/external"),))


def test_relative_paths_are_rejected_before_resolution():
    with pytest.raises(ValueError, match="absolute"):
        module().PermissionBoundary(workspace=Path("relative"))


def test_exec_flags_do_not_enable_escalation_or_legacy_broad_sandbox():
    args=boundary().exec_arguments()
    assert "--strict-config" in args
    assert "--ignore-user-config" in args
    assert "--ignore-rules" in args
    assert "--approve-for-me" not in args
    assert "--sandbox" not in args
    assert not any("dangerously" in item for item in args)
    assert "features.hooks=false" in args
    assert "features.memories=false" in args
    assert "features.multi_agent=false" in args


def test_baseline_keeps_network_disabled():
    from dataclasses import replace
    settings=tomllib.loads("\n".join(replace(boundary(),socket_path=None).config_overrides()))
    assert settings["permissions"]["devbench_study"]["network"] == {"enabled":False}
