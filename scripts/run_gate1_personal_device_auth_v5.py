"""Fresh human device flow for a future explicitly ready human.

The immutable reviewed controller is reused without changing inner controls or
budgets. Only the owned attempt root, resource names and fresh epoch differ.
No private state is inspected during preparation or import.
"""
from __future__ import annotations
import hashlib
import importlib.util
from pathlib import Path
import sys

W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
CONTROLLER=W/'scripts/run_gate1_personal_device_auth_v2.py'
CONTROLLER_SHA='c358a916324090e6f836f0de72a59f407bc03d3038956e9f2305b87341cb908b'
ROOT=W/'reports/rna-native-gate1/2026-10-21-v2/auth-preparation/login-attempt-v5'
NAMES={key:'devharness-gate1-auth-v5-'+key for key in ['inside','outside','proxy','probe','client']}
PRIVATE=W/'.devbench-codex-auth/epoch4'


def prepared_controller():
    if(CONTROLLER.resolve()!=CONTROLLER or CONTROLLER.is_symlink()or not CONTROLLER.is_file()
        or CONTROLLER.stat().st_size>100000 or hashlib.sha256(CONTROLLER.read_bytes()).hexdigest()!=CONTROLLER_SHA):
        raise PermissionError('fixed_reviewed_device_auth_controller_changed')
    spec=importlib.util.spec_from_file_location('fixed_fresh_device_auth_v5',CONTROLLER)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.ROOT=ROOT;module.NAMES=NAMES;module.PRIVATE=PRIVATE
    return module


def main():
    if len(sys.argv)!=1:raise PermissionError('fixed_no_argument_fresh_device_auth_required')
    return prepared_controller().main()


if __name__=='__main__':raise SystemExit(main())
