"""Local controlled-analysis CLI; no credential handling or clinical release command."""
import argparse
from contextlib import contextmanager
import json
import os
import sqlite3
import stat
from pathlib import Path

from devharness_controlled.contracts import load_plan, parse_json_bytes
from devharness_controlled.executor import execute_plan, execution_identity
from devharness_core.provenance import verify_execution
from devharness_controlled.verification import verify_controlled_execution


@contextmanager
def _authority_export_targets(destinations, store_root):
    """Preflight all destinations and pin existing canonical parent directories."""
    handles=[]
    try:
        if len(set(destinations))!=len(destinations):
            raise ValueError("Authority export paths must be distinct")
        for path in destinations:
            if path.resolve()!=path or path.is_relative_to(store_root):
                raise ValueError("Authority exports require canonical paths outside the live store")
            descriptor=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
            handles.append((path,descriptor))
            if path.exists() or path.is_symlink():
                raise ValueError("Authority export destination already exists")
        yield handles
    finally:
        for _,descriptor in handles:
            os.close(descriptor)


def _write_authority_export(target,value):
    from .contracts import canonical_json_bytes
    path,parent=target
    pinned=os.fstat(parent); current=path.parent.stat()
    if path.parent.resolve()!=path.parent or (pinned.st_dev,pinned.st_ino)!=(current.st_dev,current.st_ino):
        raise ValueError("Authority export parent changed")
    descriptor=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=parent)
    with os.fdopen(descriptor,"wb") as handle:
        handle.write(canonical_json_bytes(value));handle.flush();os.fsync(handle.fileno())
    os.fsync(parent)


def _json_file(path, *, checkpoint=False, max_bytes=None):
    limit = max_bytes if max_bytes is not None else (4*1024*1024 if checkpoint else 1024*1024)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("JSON evidence must be a regular file within its size limit")
        raw = handle.read(limit+1)
        after = os.fstat(handle.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(raw) > limit:
            raise ValueError("JSON evidence changed or exceeded its read limit")
    if checkpoint:
        from .verification import _json
        return _json(raw)
    return parse_json_bytes(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("identity", help="Print code/environment hashes for a new plan")
    commands.add_parser("native-identity", help="Print installed native candidate/container identities; does not download images")
    execute = commands.add_parser("execute", help="Execute an already signed synthetic/public plan")
    for option in ("plan", "approvals", "trust-registry", "counts", "metadata", "output"):
        execute.add_argument("--" + option, type=Path, required=True)
    native = commands.add_parser("execute-native", help="Execute an already signed native synthetic/public candidate plan")
    for option in ("plan", "approvals", "trust-registry", "counts", "metadata", "full-design", "reduced-design", "output"):
        native.add_argument("--" + option, type=Path, required=True)
    operated = commands.add_parser("submit-native", help="Register and execute a signed native request once")
    for option in ("plan", "approvals", "trust-registry", "counts", "metadata", "full-design", "reduced-design", "output", "registry"):
        operated.add_argument("--" + option, type=Path, required=True)
    for target in (native,operated):
        for option in ("authority-store","authority-administrators","authority-checkpoint"):
            target.add_argument("--"+option,type=Path)
    operated.add_argument("--request-id", required=True)
    operated.add_argument("--actor-id", required=True)
    recovery = commands.add_parser("recover-native", help="Reconcile an interrupted request without rerunning analysis")
    for option in ("registry", "trust-registry", "output"):
        recovery.add_argument("--" + option, type=Path, required=True)
    recovery.add_argument("--attempt-id", required=True)
    recovery.add_argument("--actor-id", required=True)
    recovery.add_argument("--checkpoint", type=Path)
    recovery.add_argument("--authority-administrators",type=Path)
    status = commands.add_parser("registry-status", help="Read one registered attempt")
    status.add_argument("--registry", type=Path, required=True)
    status.add_argument("--attempt-id", required=True)
    checkpoint = commands.add_parser("registry-checkpoint", help="Export a registry anchor for independent retention")
    checkpoint.add_argument("--registry", type=Path, required=True)
    checkpoint.add_argument("--output", type=Path, required=True)
    registry_verify = commands.add_parser("registry-verify", help="Verify registry structure and optional historical anchor")
    registry_verify.add_argument("--registry", type=Path, required=True)
    registry_verify.add_argument("--checkpoint", type=Path)
    verify = commands.add_parser("verify", help="Verify controlled evidence using explicit receipt and signer trust")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--receipt", type=Path, required=True)
    verify.add_argument("--trust-registry", type=Path, required=True)
    verify.add_argument("--authority-administrators",type=Path)
    authority_init=commands.add_parser("authority-init",help="Initialize an empty private authority store; grants still require a signed policy")
    authority_init.add_argument("--registry",type=Path,required=True)
    authority_init.add_argument("--authority-id",required=True)
    authority_publish=commands.add_parser("authority-publish",help="Append an already administrator-signed policy with explicit compare-and-swap")
    for option in ("registry","policy","administrators","output"):
        authority_publish.add_argument("--"+option,type=Path,required=True)
    authority_publish.add_argument("--expected-tip",help="Current record SHA-256; omit only for the initial policy")
    authority_export=commands.add_parser("authority-export",help="Export verified authority history and a checkpoint for independent retention")
    for option in ("registry","administrators","output","checkpoint-output"):
        authority_export.add_argument("--"+option,type=Path,required=True)
    authority_verify=commands.add_parser("authority-verify",help="Read-only signature/history verification with external administrator trust")
    for option in ("snapshot","administrators"):
        authority_verify.add_argument("--"+option,type=Path,required=True)
    authority_verify.add_argument("--checkpoint",type=Path)
    integrity = commands.add_parser("verify-integrity", help="Check hashes only; does not validate a controlled execution")
    integrity.add_argument("--manifest", type=Path, required=True)
    integrity.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    publication_started=False
    published_checkpoint=None
    try:
        authority_arguments={}
        if args.command in {"execute-native","submit-native"}:
            if args.authority_store is not None:authority_arguments["authority_store_dir"]=args.authority_store.absolute()
            if args.authority_administrators is not None:authority_arguments["authority_administrators"]=_json_file(args.authority_administrators,max_bytes=65536)
            if args.authority_checkpoint is not None:authority_arguments["authority_checkpoint"]=_json_file(args.authority_checkpoint,checkpoint=True)
        if args.command == "identity":
            result = execution_identity()
            success = True
        elif args.command == "native-identity":
            from .native_runtime import native_identity
            result, success = native_identity(), True
        elif args.command == "execute-native":
            from .native_contracts import load_native_plan
            from .native_executor import execute_native_plan
            result = execute_native_plan(load_native_plan(args.plan.absolute()), _json_file(args.approvals),
                                         _json_file(args.trust_registry), counts_path=args.counts.absolute(),
                                         metadata_path=args.metadata.absolute(), full_design_path=args.full_design.absolute(),
                                         reduced_design_path=args.reduced_design.absolute(), output_dir=args.output.absolute(),**authority_arguments)
            success = result["status"] == "analysis_complete_pending_review"
        elif args.command == "submit-native":
            from .native_contracts import load_native_plan
            from .operated_native import submit_native_plan
            result = submit_native_plan(load_native_plan(args.plan.absolute()), _json_file(args.approvals),
                                        _json_file(args.trust_registry), registry_dir=args.registry.absolute(),
                                        request_id=args.request_id, actor_id=args.actor_id,
                                        counts_path=args.counts.absolute(), metadata_path=args.metadata.absolute(),
                                        full_design_path=args.full_design.absolute(), reduced_design_path=args.reduced_design.absolute(),
                                        output_dir=args.output.absolute(),**authority_arguments)
            success = result["status"] == "completed" or (result["status"] == "existing_request"
                                                          and result["attempt"]["status"] == "completed")
        elif args.command == "recover-native":
            from .native_recovery import recover_native_attempt
            result = recover_native_attempt(args.registry.absolute(), args.attempt_id, _json_file(args.trust_registry),
                                            actor_id=args.actor_id, output_dir=args.output.absolute(),
                                            expected_checkpoint=_json_file(args.checkpoint, checkpoint=True) if args.checkpoint else None,
                                            **({"authority_administrators":_json_file(args.authority_administrators,max_bytes=65536)} if args.authority_administrators else {}))
            success = result.get("status") in {"reconciled", "terminal_observation"}
        elif args.command in {"registry-status", "registry-checkpoint", "registry-verify"}:
            from .run_registry import RunRegistry
            from .executor import _save
            path = args.registry.absolute()
            if not (path / "runs.sqlite3").is_file():
                raise ValueError("Existing registry is required for read/checkpoint operations")
            registry = RunRegistry(path)
            if args.command == "registry-status":
                result = registry.get(args.attempt_id)
            elif args.command == "registry-checkpoint":
                result = registry.export_checkpoint()
                _save(args.output.absolute(), result)
            else:
                result = registry.verify_integrity(_json_file(args.checkpoint, checkpoint=True) if args.checkpoint else None)
            success = True
        elif args.command.startswith("authority-"):
            from .authority_contracts import parse_signed_policy_bytes
            from .authority_store import AuthorityStore, MAX_SNAPSHOT_BYTES, verify_authority_snapshot, _checkpoint
            from .executor import _save
            if args.command=="authority-init":
                store=AuthorityStore.create(args.registry.absolute(),args.authority_id)
                result={"status":"empty_no_grants","metadata":store.capture()["metadata"],"clinical_release_allowed":False}
            elif args.command=="authority-verify":
                result=verify_authority_snapshot(_json_file(args.snapshot,checkpoint=True,max_bytes=MAX_SNAPSHOT_BYTES),
                    _json_file(args.administrators,max_bytes=65536),
                    expected_checkpoint=_json_file(args.checkpoint,checkpoint=True) if args.checkpoint else None)
            else:
                store=AuthorityStore(args.registry.absolute())
                administrators=_json_file(args.administrators,max_bytes=65536)
                destinations=[args.output.absolute()]
                if args.command=="authority-export":destinations.append(args.checkpoint_output.absolute())
                with _authority_export_targets(destinations,store.root) as targets:
                    if args.command=="authority-publish":
                        from .contracts import canonical_json_bytes
                        policy=parse_signed_policy_bytes(canonical_json_bytes(_json_file(args.policy,max_bytes=65536)))
                        publication_started=True
                        result=store.publish(policy,administrators,expected_tip=args.expected_tip)
                        published_checkpoint=result
                        _write_authority_export(targets[0],result)
                    else:
                        snapshot=store.capture();result=verify_authority_snapshot(snapshot,administrators)
                        _write_authority_export(targets[0],snapshot)
                        _write_authority_export(targets[1],_checkpoint(snapshot))
            success=True
        elif args.command == "verify":
            result = verify_controlled_execution(args.manifest.absolute(), _json_file(args.receipt)["root_hash"],
                                                 _json_file(args.trust_registry),
                                                 **({"authority_administrators":_json_file(args.authority_administrators,max_bytes=65536)} if args.authority_administrators else {}))
            success = result["valid"]
        elif args.command == "verify-integrity":
            result = verify_execution(args.manifest.resolve(), _json_file(args.receipt)["root_hash"])
            result["scope"] = "hash integrity only; controlled workflow not verified"
            success = result["valid"]
        else:
            result = execute_plan(load_plan(args.plan.absolute()), _json_file(args.approvals),
                                  _json_file(args.trust_registry), counts_path=args.counts.absolute(),
                                  metadata_path=args.metadata.absolute(), output_dir=args.output.absolute())
            success = result["status"] == "analysis_complete_pending_review"
    except (ValueError, TypeError, OSError, KeyError, RuntimeError, sqlite3.Error) as error:
        result, success = {"status": "operation_failed", "error": str(error),
                           "execution_may_have_started": args.command in {"execute", "execute-native", "submit-native", "recover-native"},
                           "clinical_release_allowed": False}, False
        if args.command=="authority-publish":
            result["publication_may_have_committed"]=publication_started
            if published_checkpoint is not None:
                result.update(status="published_export_failed",published_checkpoint=published_checkpoint)
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
