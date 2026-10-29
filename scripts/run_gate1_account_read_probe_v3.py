"""Prepared-only fresh epoch4 account binding; requires real accepted auth v5.

No private path metadata or Docker operation occurs during import/preparation.
The immutable offline account controller is reused, with an extra public-proof
crosslink before its metadata lease. No synthetic success can authorize access.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
CONTROLLER=W/'scripts/run_gate1_account_read_probe.py'
CONTROLLER_SHA='998fcbf0fd3f9c202174805f69e6b999a5c01fdd1703636fae650b000a1f6b19'
AUTH_SHIM=W/'scripts/run_gate1_personal_device_auth_v5.py'
AUTH_SHIM_SHA='3d95ec1a2390b010bf355e9878ae2b4ff66d01585c79568c09329fbf50ca0826'
AUTH_CONTROLLER=W/'scripts/run_gate1_personal_device_auth_v2.py'
AUTH_CONTROLLER_SHA='c358a916324090e6f836f0de72a59f407bc03d3038956e9f2305b87341cb908b'
REPORTS=W/'reports/rna-native-gate1/2026-10-21-v2'
ROOT=REPORTS/'provider/account-probe/epoch4-attempt-v3'
PRIVATE=W/'.devbench-codex-auth/epoch4'
AUTH=PRIVATE/'codex-home/auth.json'
DEVICE_ROOT=REPORTS/'auth-preparation/login-attempt-v5'
DEVICE_RESULT=DEVICE_ROOT/'result.json'
DEVICE_REVIEW=REPORTS/'reviews/personal-device-auth-postrun-v5.json'
DEVICE_RELEASE=DEVICE_ROOT/'release.json'


def public_json(enclosure,path):
    enclosure.digest(path,65536)  # Public ordinary-source guard before reading.
    return json.loads(path.read_text())


def crosslink_epoch(enclosure):
    proof=public_json(enclosure,DEVICE_RESULT)
    release=public_json(enclosure,DEVICE_RELEASE)
    if(proof.get('private_root')!=str(PRIVATE)
        or any(proof.get(key)is not True for key in ['private_epoch_initialization_attempted','private_epoch_created','private_epoch_preserved_without_inspection'])
        or release.get('accepted')is not True or release.get('scope')!='official-personal-device-auth-login-only'
        or any(release.get(key)is not False for key in ['live_generation_allowed','biological_execution_allowed','native_admission_qualified'])):
        raise PermissionError('exact_successful_auth_private_epoch_crosslink_required')
    pins=release.get('source_sha256')
    if(type(pins)is not dict or pins.get(str(AUTH_SHIM))!=AUTH_SHIM_SHA
        or pins.get(str(AUTH_CONTROLLER))!=AUTH_CONTROLLER_SHA
        or enclosure.digest(AUTH_SHIM)!=AUTH_SHIM_SHA or enclosure.digest(AUTH_CONTROLLER)!=AUTH_CONTROLLER_SHA):
        raise PermissionError('exact_successful_auth_source_identity_required')


def prepared_controller():
    if(CONTROLLER.resolve()!=CONTROLLER or CONTROLLER.is_symlink()or not CONTROLLER.is_file()
        or CONTROLLER.stat().st_size>100000 or hashlib.sha256(CONTROLLER.read_bytes()).hexdigest()!=CONTROLLER_SHA):
        raise PermissionError('fixed_reviewed_account_controller_changed')
    spec=importlib.util.spec_from_file_location('fixed_fresh_account_epoch4',CONTROLLER)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.ROOT=ROOT;module.PRIVATE=PRIVATE;module.AUTH=AUTH
    module.DEVICE_RESULT=DEVICE_RESULT;module.DEVICE_REVIEW=DEVICE_REVIEW
    module.NAME='devharness-gate1-account-read-epoch4-v3'
    original_authorize=module.authorize_auth
    def authorize_bound_epoch(enclosure):
        actual_proof=original_authorize(enclosure)  # Strict real login/status + independent hash review first.
        crosslink_epoch(enclosure)  # Literal epoch and source identity BEFORE any private lstat.
        return actual_proof
    module.authorize_auth=authorize_bound_epoch
    return module


def main():
    if len(sys.argv)!=1:raise PermissionError('fixed_no_argument_fresh_account_read_required')
    return prepared_controller().main()


if __name__=='__main__':raise SystemExit(main())
