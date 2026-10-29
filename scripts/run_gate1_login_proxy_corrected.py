"""Fixed official-login proxy-selection negative, reusing the released enclosure code.

Only the owned root and container name change. The original supervisor, pinned
client, immutable image, controls, command and fail-closed gates remain exact.
"""
from __future__ import annotations
import hashlib
import importlib.util
from pathlib import Path

WORKSPACE = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
SUPERVISOR = WORKSPACE / 'scripts/run_isolated_codex_protocol.py'
SUPERVISOR_SHA256 = '9e55a98ebf82a4027d63ce51bb38db455d983a55e789916c5314c51aaf1e2373'
ROOT = WORKSPACE / 'reports/rna-native-gate1/2026-10-21-v2/auth-preparation/proxy-compat-v3'
NAME = 'devharness-gate1-login-proxy-v3'


def load_supervisor():
    if (SUPERVISOR.is_symlink() or SUPERVISOR.resolve() != SUPERVISOR
            or hashlib.sha256(SUPERVISOR.read_bytes()).hexdigest() != SUPERVISOR_SHA256):
        raise PermissionError('released_original_supervisor_changed')
    spec = importlib.util.spec_from_file_location('fixed_login_proxy_v3_supervisor', SUPERVISOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT, module.NAME = ROOT, NAME
    return module


def main():
    return load_supervisor().main()


if __name__ == '__main__':
    raise SystemExit(main())
