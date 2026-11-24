"""Signed local policy history and a cooperating-process launch guard.

Administrator roots must be supplied through an independent trusted channel.
The local owner can replace the entire store; retain checkpoints independently.
Construction may recover a SQLite hot journal. Exported snapshot verification
is read-only and neither authenticates humans nor authorizes a new execution.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote
import uuid

from .authority_contracts import (AUTHORITY_JSON_LIMIT_BYTES, AUTHORITY_VERIFICATION_SCOPE,
    AuthorityAdministrator, SignedAuthorityPolicy, parse_signed_policy_bytes, parse_administrators_bytes,
    verify_policy_signature, authorize_signers, _key_fingerprint)
from .contracts import canonical_json_bytes, parse_json_bytes
from .native_contracts import is_governed_plan, parse_native_plan_bytes
from .run_registry import _path, _private_directory, _private_file, _identifier, _timestamp, _text, _digest
from .trust import _aware_time, TrustedSigner
from .sqlite_schema import require_exact_schema

MAX_POLICIES = 4096
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_DATABASE_BYTES = 128 * 1024 * 1024
MAX_RECORD_BYTES = AUTHORITY_JSON_LIMIT_BYTES + 2048
_SCHEMA = {
    "authority_metadata": "CREATE TABLE authority_metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), schema_version TEXT NOT NULL CHECK(schema_version='local_authority_store/1'), registry_id TEXT NOT NULL UNIQUE, authority_id TEXT NOT NULL, created_at TEXT NOT NULL)",
    "policies": "CREATE TABLE policies (revision INTEGER PRIMARY KEY CHECK(revision BETWEEN 1 AND 4096), record_json TEXT NOT NULL, record_sha256 TEXT NOT NULL UNIQUE)",
}
for _table in ("authority_metadata", "policies"):
    for _action in ("UPDATE", "DELETE"):
        _name=f"{_table}_no_{_action.lower()}"
        _SCHEMA[_name]=f"CREATE TRIGGER {_name} BEFORE {_action} ON {_table} BEGIN SELECT RAISE(ABORT,'append-only authority'); END"


class AuthorityBusyError(RuntimeError):
    """A policy change or dispatch is already in its local critical section."""


def _require(condition, message):
    if not condition:raise ValueError(message)


def _sha(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _now(value=None):
    return _aware_time(datetime.now(timezone.utc) if value is None else value)


def _stamp(value):
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _metadata(value):
    _require(type(value) is dict and set(value)=={"schema_version","registry_id","authority_id","created_at"}, "Invalid authority metadata fields")
    _require(value["schema_version"]=="local_authority_store/1", "Unsupported authority metadata")
    _identifier(value["registry_id"]);_text(value["authority_id"],"authority_id");_timestamp(value["created_at"])
    return value


def _administrators(values):
    _require(isinstance(values,(list,tuple)) and values,"Explicit administrator trust is required")
    raw=[v.model_dump(mode="json") if isinstance(v,AuthorityAdministrator) else v for v in values]
    return parse_administrators_bytes(canonical_json_bytes(raw))


def _history(snapshot):
    _require(type(snapshot) is dict and set(snapshot)=={"schema_version","metadata","records","tip_sha256","clinical_release_allowed","scope"}, "Invalid authority snapshot fields")
    _require(len(canonical_json_bytes(snapshot))<=MAX_SNAPSHOT_BYTES, "Authority snapshot exceeds bound")
    _require(snapshot["schema_version"]=="authority_history/1" and snapshot["clinical_release_allowed"] is False
             and snapshot["scope"]==AUTHORITY_VERIFICATION_SCOPE, "Unsupported authority snapshot scope")
    metadata=_metadata(snapshot["metadata"]);records=snapshot["records"]
    _require(type(records) is list and len(records)<=MAX_POLICIES,"Invalid authority history length")
    previous=None;at=_timestamp(metadata["created_at"]);issued=None;envelopes=[]
    for sequence,record in enumerate(records,1):
        _require(type(record) is dict and set(record)=={"schema_version","registry_id","authority_id","applied_at","signed_policy","record_sha256"}, "Invalid authority record fields")
        _require(len(canonical_json_bytes(record))<=MAX_RECORD_BYTES, "Authority record exceeds bound")
        _require(record["schema_version"]=="authority_policy_record/1"
                 and record["registry_id"]==metadata["registry_id"] and record["authority_id"]==metadata["authority_id"], "Authority record identity mismatch")
        _require(record["record_sha256"]==_sha({k:v for k,v in record.items() if k!="record_sha256"}), "Authority record hash mismatch")
        envelope=parse_signed_policy_bytes(canonical_json_bytes(record["signed_policy"]));policy=envelope.policy
        applied=_timestamp(record["applied_at"])
        _require(applied>=at and (issued is None or policy.issued_at>=issued), "Authority times run backwards")
        _require(policy.authority_id==metadata["authority_id"] and policy.registry_id==metadata["registry_id"] and policy.revision==sequence
                 and policy.previous_sha256==previous, "Broken authority revision/predecessor chain")
        previous=record["record_sha256"];at=applied;issued=policy.issued_at;envelopes.append(envelope)
    _require(snapshot["tip_sha256"]==previous,"Authority snapshot tip mismatch")
    return metadata,envelopes


def _checkpoint(snapshot):
    _history(snapshot)
    value={"schema_version":"authority_checkpoint/1","metadata":snapshot["metadata"],
           "entries":[{"revision":i,"record_sha256":r["record_sha256"]} for i,r in enumerate(snapshot["records"],1)],
           "tip_sha256":snapshot["tip_sha256"],"clinical_release_allowed":False}
    return {**value,"checkpoint_sha256":_sha(value)}


def verify_authority_snapshot(snapshot, administrators, *, expected_checkpoint=None):
    """Check historical signatures and an optional externally retained prefix."""
    metadata,envelopes=_history(snapshot)
    administrators=_administrators(administrators)
    _require(any(a.authority_id==metadata["authority_id"] and a.registry_id==metadata["registry_id"] for a in administrators),
             "Administrator trust does not cover this authority registry")
    receipts=[verify_policy_signature(envelope,administrators,verified_at=_timestamp(record["applied_at"]))
              for envelope,record in zip(envelopes,snapshot["records"])]
    if expected_checkpoint is not None:
        expected=expected_checkpoint;current=_checkpoint(snapshot)
        _require(type(expected) is dict and set(expected)==set(current), "Invalid authority checkpoint fields")
        _require(expected["checkpoint_sha256"]==_sha({k:v for k,v in expected.items() if k!="checkpoint_sha256"}), "Authority checkpoint hash mismatch")
        _require(expected["schema_version"]==current["schema_version"] and expected["metadata"]==metadata
                 and expected["clinical_release_allowed"] is False,"Authority checkpoint identity mismatch")
        entries=expected["entries"]
        _require(type(entries) is list and len(entries)<=len(current["entries"])
                 and entries==current["entries"][:len(entries)]
                 and expected["tip_sha256"]==(entries[-1]["record_sha256"] if entries else None),
                 "Authority checkpoint rollback or changed prefix")
    return {"schema_version":"authority_history_verification/1","valid":True,"authority_id":metadata["authority_id"],
            "registry_id":metadata["registry_id"],"tip_sha256":snapshot["tip_sha256"],"policy_signatures_verified":len(receipts),
            "signature_receipts":receipts,"scope":AUTHORITY_VERIFICATION_SCOPE,"clinical_release_allowed":False}


def authorize_snapshot(plan, signers, administrators, snapshot, *, now=None, expected_checkpoint=None):
    """Authorize an exact governed plan at this clock, using the supplied snapshot."""
    plan=parse_native_plan_bytes(plan.canonical_bytes())
    _require(is_governed_plan(plan),"An explicit governed native plan is required")
    _require(isinstance(signers,(list,tuple)) and len(signers)==2,"Exactly two distinct approval signers are required")
    signers=[TrustedSigner.model_validate(signer) for signer in signers]
    _require({s.role for s in signers}=={"author","reviewer"} and len({s.subject_id for s in signers})==2
             and len({s.key_id for s in signers})==2 and len({_key_fingerprint(s.public_key_pem) for s in signers})==2,
             "Authority requires a distinct author/reviewer pair")
    history=verify_authority_snapshot(snapshot,administrators,expected_checkpoint=expected_checkpoint)
    _require(snapshot["records"] and plan.authority_id==history["authority_id"],"No policy for this governed authority")
    _require(plan.authority_tip_sha256==snapshot["tip_sha256"],"Authority tip changed; plan must be approved again")
    at=_now(now)
    _require(at>=_timestamp(snapshot["records"][-1]["applied_at"]),"Authorization predates policy application")
    envelope=parse_signed_policy_bytes(canonical_json_bytes(snapshot["records"][-1]["signed_policy"]))
    policy_receipt=verify_policy_signature(envelope,administrators,verified_at=at)
    grants=authorize_signers(envelope.policy,plan.study_id,signers,at)
    return {"schema_version":"authority_decision/1","plan_sha256":plan.sha256(),"authority_id":plan.authority_id,
            "authority_tip_sha256":history["tip_sha256"],"registry_id":history["registry_id"],
            "snapshot_sha256":_sha(snapshot),"checked_at":_stamp(at),"policy_signature":policy_receipt,"grants":grants,
            "scope":AUTHORITY_VERIFICATION_SCOPE,"clinical_release_allowed":False}


class AuthorityStore:
    """Private local signed history, bounded to4096 policies/128MiB and8MiB snapshots."""

    def __init__(self, directory):
        self.root=_path(directory);info=_private_directory(self.root)
        self._directory_identity=(info.st_dev,info.st_ino)
        self.database_path=self.root/"authority.sqlite3";self.lock_path=self.root/"authority.lock"
        info=_private_file(self.database_path);self._database_identity=(info.st_dev,info.st_ino)
        info=_private_file(self.lock_path,maximum=0);self._lock_identity=(info.st_dev,info.st_ino)
        # SQLite may recover its atomic hot journal here; no history rows are edited.
        with self._lock(exclusive=True),self._connection(write=True):pass

    @classmethod
    def create(cls, directory, authority_id, *, now=None):
        root=_path(directory);_text(authority_id,"authority_id");at=_now(now)
        root.mkdir(mode=0o700);_private_directory(root)
        value=cls.__new__(cls);value.root=root
        info=root.stat();value._directory_identity=(info.st_dev,info.st_ino)
        value.database_path=root/"authority.sqlite3";value.lock_path=root/"authority.lock"
        for path in (value.database_path,value.lock_path):
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
            try:os.fsync(fd)
            finally:os.close(fd)
        info=value.database_path.stat();value._database_identity=(info.st_dev,info.st_ino)
        info=value.lock_path.stat();value._lock_identity=(info.st_dev,info.st_ino)
        with value._lock(exclusive=True),value._connection(write=True,initializing=True) as connection:
            for sql in _SCHEMA.values():connection.execute(sql)
            connection.execute("INSERT INTO authority_metadata VALUES (1,?,?,?,?)",("local_authority_store/1",uuid.uuid4().hex,authority_id,_stamp(at)))
            value._schema(connection)
        value._sync_directory();return value

    def _sync_directory(self):
        fd=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:os.fsync(fd)
        finally:os.close(fd)

    @property
    def registry_id(self):
        with self._lock(exclusive=False),self._connection() as connection:
            return self._schema(connection)["registry_id"]

    def _paths(self):
        _require(_path(self.root)==self.root,"Authority path changed")
        info=_private_directory(self.root)
        _require((info.st_dev,info.st_ino)==self._directory_identity,"Authority directory replaced")
        for path,identity,limit in ((self.database_path,self._database_identity,MAX_DATABASE_BYTES),(self.lock_path,self._lock_identity,0)):
            info=_private_file(path,maximum=limit)
            _require((info.st_dev,info.st_ino)==identity,"Authority file replaced")
        allowed={self.database_path.name,self.lock_path.name}
        for suffix in ("-journal","-wal","-shm"):
            path=Path(str(self.database_path)+suffix);_private_file(path,missing=True);allowed.add(path.name)
        _require({p.name for p in self.root.iterdir()}<=allowed,"Unexpected authority store member")

    @contextmanager
    def _lock(self, *, exclusive):
        self._paths()
        fd=os.open(self.lock_path,os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        try:
            info=os.fstat(fd);_require((info.st_dev,info.st_ino)==self._lock_identity,"Authority lock replaced while opening")
            try:fcntl.flock(fd,(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
            except BlockingIOError as error:raise AuthorityBusyError("Authority change/dispatch critical section is busy") from error
            self._paths();yield;self._paths()
        finally:os.close(fd)

    @contextmanager
    def _connection(self, *, write=False, initializing=False):
        self._paths()
        connection=sqlite3.connect(f"file:{quote(str(self.database_path),safe='/')}?mode={'rw' if write else 'ro'}",uri=True,timeout=5,isolation_level=None)
        connection.row_factory=sqlite3.Row
        try:
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,1024*1024)
            connection.execute("PRAGMA trusted_schema=OFF")
            _require(connection.execute("PRAGMA journal_mode").fetchone()[0]=="delete","Authority requires DELETE journal mode")
            if write:
                connection.execute("PRAGMA synchronous=FULL")
                size=connection.execute("PRAGMA page_size").fetchone()[0]
                connection.execute(f"PRAGMA max_page_count={MAX_DATABASE_BYTES//size}")
                connection.execute("BEGIN IMMEDIATE")
            else:connection.execute("PRAGMA query_only=ON");connection.execute("BEGIN")
            if not initializing:self._schema(connection)
            yield connection
            self._paths();connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:connection.execute("ROLLBACK")
            raise
        finally:connection.close()

    @staticmethod
    def _schema(connection):
        require_exact_schema(connection,_SCHEMA)
        rows=connection.execute("SELECT * FROM authority_metadata LIMIT 2").fetchall()
        _require(len(rows)==1 and rows[0]["singleton"]==1,"Invalid authority metadata row")
        return _metadata({k:rows[0][k] for k in rows[0].keys() if k!="singleton"})

    def _capture(self, connection):
        metadata=self._schema(connection);records=[];size=0
        for row in connection.execute("SELECT * FROM policies ORDER BY revision LIMIT ?",(MAX_POLICIES+1,)):
            raw=row["record_json"].encode("utf-8");size+=len(raw)
            _require(len(records)<MAX_POLICIES and len(raw)<=MAX_RECORD_BYTES and size<=MAX_SNAPSHOT_BYTES,"Authority history exceeds bound")
            record=parse_json_bytes(raw)
            _require(row["record_sha256"]==record.get("record_sha256") and row["revision"]==len(records)+1,"Authority row identity mismatch")
            records.append(record)
        snapshot={"schema_version":"authority_history/1","metadata":metadata,"records":records,
                  "tip_sha256":records[-1]["record_sha256"] if records else None,"clinical_release_allowed":False,"scope":AUTHORITY_VERIFICATION_SCOPE}
        _history(snapshot);return snapshot

    def capture(self):
        """Capture bounded structural evidence; signature trust is checked separately."""
        with self._lock(exclusive=False),self._connection() as connection:return self._capture(connection)

    def publish(self, envelope, administrators, *, expected_tip, now=None):
        at=_now(now)
        raw=envelope.canonical_bytes() if isinstance(envelope,SignedAuthorityPolicy) else canonical_json_bytes(envelope)
        envelope=parse_signed_policy_bytes(raw)
        verify_policy_signature(envelope,administrators,verified_at=at)
        if expected_tip is not None:_digest(expected_tip)
        with self._lock(exclusive=True),self._connection(write=True) as connection:
            snapshot=self._capture(connection);verify_authority_snapshot(snapshot,administrators)
            _require(snapshot["tip_sha256"]==expected_tip,"Authority tip changed before publication")
            policy=envelope.policy;metadata=snapshot["metadata"]
            _require(policy.authority_id==metadata["authority_id"] and policy.registry_id==metadata["registry_id"]
                     and policy.revision==len(snapshot["records"])+1
                     and policy.previous_sha256==expected_tip,"Authority revision or predecessor mismatch")
            record={"schema_version":"authority_policy_record/1","registry_id":metadata["registry_id"],
                    "authority_id":metadata["authority_id"],"applied_at":_stamp(at),"signed_policy":envelope.model_dump(mode="json")}
            record["record_sha256"]=_sha(record)
            candidate={**snapshot,"records":[*snapshot["records"],record],"tip_sha256":record["record_sha256"]}
            verify_authority_snapshot(candidate,administrators)
            connection.execute("INSERT INTO policies VALUES (?,?,?)",(policy.revision,canonical_json_bytes(record).decode(),record["record_sha256"]))
            result=_checkpoint(candidate)
        self._sync_directory();return result

    def checkpoint(self, administrators):
        snapshot=self.capture();verify_authority_snapshot(snapshot,administrators);return _checkpoint(snapshot)

    @contextmanager
    def launch_guard(self, plan, signers, administrators, *, now=None, expected_checkpoint=None):
        """Hold a shared lock until the caller has completed its dispatch boundary."""
        with self._lock(exclusive=False):
            with self._connection() as connection:snapshot=self._capture(connection)
            decision=authorize_snapshot(plan,signers,administrators,snapshot,now=now,expected_checkpoint=expected_checkpoint)
            yield {"snapshot":snapshot,"decision":decision}
