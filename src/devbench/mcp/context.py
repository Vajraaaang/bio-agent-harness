"""The daemon's investigation, artifact custody, and disclosure projections."""

from __future__ import annotations

import json
from uuid import uuid4

from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
from devbench.engine.scientific_authority import _row
from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.plugin_jobs import canonical, digest

PUBLIC = frozenset({"synthetic", "public"})
PRIVATE = "private_data_local_studio_only"
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS mcp_sessions (
        session_id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
        classification TEXT NOT NULL, epistemic_state TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS mcp_admissions (
        request_sha256 TEXT PRIMARY KEY, session_id TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS mcp_private_digests (sha256 TEXT PRIMARY KEY)""",
    """CREATE TABLE IF NOT EXISTS mcp_noncount_roots (sha256 TEXT PRIMARY KEY)""",
    """CREATE TABLE IF NOT EXISTS mcp_artifacts (
        session_id TEXT NOT NULL, artifact_id TEXT NOT NULL, sha256 TEXT NOT NULL,
        ref_json TEXT NOT NULL, path TEXT NOT NULL, detail_json TEXT NOT NULL,
        classification TEXT NOT NULL, status TEXT NOT NULL, producer_step_id TEXT,
        PRIMARY KEY(session_id,artifact_id))""",
    """CREATE INDEX IF NOT EXISTS mcp_artifact_digest ON mcp_artifacts(sha256)""",
    """CREATE TABLE IF NOT EXISTS mcp_artifact_roots (
        session_id TEXT NOT NULL, artifact_id TEXT NOT NULL, sha256 TEXT NOT NULL,
        PRIMARY KEY(session_id,artifact_id,sha256))""",
    """CREATE TABLE IF NOT EXISTS mcp_broker_sources (
        session_id TEXT NOT NULL, sha256 TEXT NOT NULL, source_id TEXT NOT NULL,
        context_id TEXT NOT NULL, PRIMARY KEY(session_id,sha256,source_id))""",
    """CREATE TABLE IF NOT EXISTS mcp_branches (
        session_id TEXT NOT NULL, branch_id TEXT NOT NULL, branch_kind TEXT NOT NULL,
        status TEXT NOT NULL, parent_branch_id TEXT, aim_id TEXT,
        PRIMARY KEY(session_id,branch_id))""",
    """CREATE TABLE IF NOT EXISTS mcp_aims (
        session_id TEXT NOT NULL, aim_id TEXT NOT NULL, version INTEGER NOT NULL,
        status TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(session_id,aim_id))""",
    """CREATE TABLE IF NOT EXISTS mcp_holds (
        session_id TEXT NOT NULL, hold_id TEXT NOT NULL, status TEXT NOT NULL,
        body TEXT NOT NULL, PRIMARY KEY(session_id,hold_id))""",
)
_CLASSIFICATIONS = ("synthetic", "public", "unclassified", "private", "clinical")


def classification_floor(*values):
    return max(values, key=_CLASSIFICATIONS.index)


_ROOTS_SQL = """WITH RECURSIVE ancestry(sha256) AS (
    SELECT value FROM json_each(?) UNION
    SELECT sha256 FROM mcp_artifacts WHERE session_id=? UNION
    SELECT r.sha256 FROM ancestry t
    JOIN mcp_artifacts a ON a.sha256=t.sha256
    JOIN mcp_artifact_roots r ON r.session_id=a.session_id
        AND r.artifact_id=a.artifact_id
) """


def roots_query(db, sid, digests, query):
    return db.execute(_ROOTS_SQL + query, (json.dumps(list(digests)), sid))


def guarded_digest(db, sid, digests):
    """Re-evaluate inherited privacy, across all stored aliases, in one snapshot."""
    if sid:
        session = db.execute(
            "SELECT classification FROM mcp_sessions WHERE session_id=?", (sid,)
        ).fetchone()
        if session and session[0] not in PUBLIC:
            return True
    return bool(
        roots_query(
            db,
            sid,
            digests,
            "SELECT 1 FROM ancestry JOIN mcp_private_digests USING(sha256) LIMIT 1",
        ).fetchone()
    )


def check_embargo(db, digests):
    for row in roots_query(
        db,
        None,
        digests,
        "SELECT s.* FROM ancestry a JOIN scientific_records s "
        "ON s.record_id='embargo-'||a.sha256 AND s.kind='execution_event'",
    ):
        if _row(row)["record"]["event"] == "protected_artifact":
            raise OperationBlocked("input_scope_violation")


def artifact_record(row):
    if row is None:
        raise OperationBlocked("input_scope_violation")
    return {
        "ref": json.loads(row["ref_json"]),
        "path": row["path"],
        "detail": json.loads(row["detail_json"]),
        "classification": row["classification"],
        "status": row["status"],
        "producer_step_id": row["producer_step_id"],
    }


def store_artifact(db, sid, ref, path, classification, detail, producer=None):
    db.execute(
        """INSERT INTO mcp_artifacts VALUES (?,?,?,?,?,?,?,'current',?)
           ON CONFLICT(session_id,artifact_id) DO NOTHING""",
        (
            sid,
            ref["artifact_id"],
            ref["sha256"],
            canonical(ref).decode(),
            str(path),
            canonical(detail).decode(),
            classification,
            producer,
        ),
    )


class ControlPlane:
    def __init__(self, plugin, supervisor=None, config=None, studio_url=None):
        self.plugin = plugin
        self.jobs = plugin.jobs
        self.workspace = self.jobs.workspace
        self.input_root = plugin.input_root
        self.supervisor = supervisor
        self.config = config or {}
        self.offline_only = self.config.get("offline_only", False)
        self.studio_url = studio_url
        self.authority = self.jobs.authority
        self.broker = EvidenceBroker(self.authority)
        with self.authority.daemon_transaction() as db:
            for ddl in _SCHEMA:
                db.execute(ddl)

    def session(self, sid, db=None):
        if db is None:
            return self.authority.daemon_read(lambda conn: self.session(sid, conn))
        row = db.execute(
            "SELECT * FROM mcp_sessions WHERE session_id=?", (sid,)
        ).fetchone()
        if row is None:
            raise OperationBlocked("input_scope_violation")
        return {**dict(row), "revision": self.authority._revision(db, sid)}

    def event(self, db, sid, event, body):
        self.authority._event(db, sid, event, body)
        revision = self.authority._revision(db, sid)
        db.execute(
            "UPDATE mcp_sessions SET revision=? WHERE session_id=?", (revision, sid)
        )
        return revision

    def artifact(self, sid, aid, sha=None, db=None):
        if db is None:
            return self.authority.daemon_read(
                lambda conn: self.artifact(sid, aid, sha, conn)
            )
        record = artifact_record(
            db.execute(
                "SELECT * FROM mcp_artifacts WHERE session_id=? AND artifact_id=?",
                (sid, aid),
            ).fetchone()
        )
        if sha is not None and sha != record["ref"]["sha256"]:
            raise OperationBlocked("plan_identity_mismatch")
        if record["status"] != "current":
            raise OperationBlocked("input_scope_violation")
        self.check_embargo(
            [
                record["ref"]["sha256"],
                record["detail"].get("source_sha256") or record["ref"]["sha256"],
            ],
            db=db,
        )
        if (
            record["detail"].get("count_1_verified")
            and roots_query(
                db,
                None,
                [record["ref"]["sha256"]],
                "SELECT 1 FROM ancestry JOIN mcp_noncount_roots USING(sha256) LIMIT 1",
            ).fetchone()
        ):
            record["detail"]["count_1_verified"] = False
        return record

    def protected(self, sid=None, digests=(), db=None):
        hashes = tuple(digests)
        if db is None:
            return self.authority.daemon_read(
                lambda conn: self.protected(sid, hashes, conn)
            )
        if guarded_digest(db, sid, hashes):
            return True
        # Historical Studio plans can independently raise the classification floor.
        roots = {
            row[0]
            for row in roots_query(db, sid, hashes, "SELECT sha256 FROM ancestry")
        }
        return self.jobs.has_private_input(roots) if roots else False

    def require_public(self, sid=None, digests=()):
        if self.protected(sid, digests):
            raise OperationBlocked(PRIVATE)
        self.check_embargo(digests)

    def check_embargo(self, digests, db=None):
        if db is None:
            return self.authority.daemon_read(
                lambda conn: self.check_embargo(digests, conn)
            )
        check_embargo(db, digests)

    def handoff(self, sid, private=False):
        origin = self.studio_url
        if private and not self.offline_only:
            origin = None
        url = (
            origin.rstrip("/") + "/?session_id=" + sid + "&view=workflows"
            if origin
            else None
        )
        return {
            "studio_url": url,
            "launch_hint": "devbench workbench --workspace <workspace> "
            "--input-root <input-root> "
            "--offline-only --enable-scientific --allow-data-execution"
            if private
            else None,
        }

    def _create(self, db, sid, ref, classification):
        sha = ref["sha256"]
        db.execute(
            "INSERT INTO mcp_sessions VALUES (?,0,?,'E1')", (sid, classification)
        )
        db.execute(
            "INSERT INTO mcp_branches VALUES (?,'primary','primary','active',"
            "NULL,NULL)",
            (sid,),
        )
        basis = {
            "cohort_sha256": sha,
            "design_sha256": digest({}),
            "procedure_sha256": digest([]),
            "family_allocation_sha256": digest([]),
            "exposure_basis_sha256": digest([sha]),
            "policy_sha256": digest({"classification": classification}),
        }
        self.authority._append_execution_record(
            db, sid, {"kind": "binding", "record_id": "intake-" + sid, **basis}
        )

    def _enroll_source(self, db, sid, ref, classification, *, parents=(), members=()):
        sha, aid = ref["sha256"], ref["artifact_id"]
        identifiers = sorted({*parents, *members} - {sha})
        db.executemany(
            "INSERT OR IGNORE INTO mcp_artifact_roots VALUES (?,?,?)",
            ((sid, aid, root) for root in identifiers),
        )
        sources = []
        for parent in set(parents):
            sources.extend(
                r[0]
                for r in db.execute(
                    "SELECT source_id FROM mcp_broker_sources "
                    "WHERE session_id=? AND sha256=?",
                    (sid, parent),
                )
            )
        source = "mcp-" + digest([sha, sorted(sources)])
        policy = AccessPolicy("mcp-" + sid, 1)
        self.broker._put(db, "policy", policy.sha256(), policy.document())
        context = "mcp-" + sid
        self.broker._put(
            db,
            "context",
            context,
            {
                "context_id": context,
                "principal_id": "mcp-scientist",
                "lineage_id": sid,
                "role": "scientist",
                "phase": "readback",
                "policy_sha256": policy.sha256(),
                "parents": [],
            },
        )
        self.broker._put(
            db,
            "source",
            source,
            {
                "source_id": source,
                "content_sha256": sha,
                "classification": "structural",
                "parents": sorted(set(sources)),
                "roots": sorted({sha, *parents}),
                "units": [],
                "populations": [],
                "tags": [],
            },
        )
        db.execute(
            "INSERT OR IGNORE INTO mcp_broker_sources VALUES (?,?,?,?)",
            (sid, sha, source, context),
        )
        if classification not in PUBLIC:
            db.execute("INSERT OR IGNORE INTO mcp_private_digests VALUES (?)", (sha,))

    def check_source(self, db, sid, sha):
        self.check_embargo([sha], db)
        found = False
        for row in db.execute(
            "SELECT source_id,context_id FROM mcp_broker_sources "
            "WHERE session_id=? AND sha256=?",
            (sid, sha),
        ):
            self.broker._check_delivery(
                db, row["context_id"], row["source_id"], sid, "scientist"
            )
            found = True
        if not found:
            raise OperationBlocked("input_scope_violation")

    def admit(self, sid, expected_revision, ref, path, classification, detail):
        detail = dict(detail)
        members = sorted(set(detail.pop("member_sha256s", ())))
        key = digest([ref["sha256"], str(path), classification, detail])
        with self.authority.daemon_transaction() as db:
            if sid is None:
                found = db.execute(
                    "SELECT session_id FROM mcp_admissions WHERE request_sha256=?",
                    (key,),
                ).fetchone()
                sid = found[0] if found else None
            if sid is None:
                if expected_revision not in (None, 0):
                    raise OperationBlocked("stale_revision_conflict")
                sid = uuid4().hex
                self._create(db, sid, ref, classification)
                db.execute("INSERT INTO mcp_admissions VALUES (?,?)", (key, sid))
                expected_revision = None
            state = self.session(sid, db)
            sha = ref["sha256"]
            roots = {sha, detail.get("source_sha256", sha), *members}
            self.check_embargo(roots, db)
            existing = db.execute(
                "SELECT * FROM mcp_artifacts WHERE session_id=? AND artifact_id=?",
                (sid, ref["artifact_id"]),
            ).fetchone()
            floor = classification_floor(state["classification"], classification)
            if self.protected(sid, roots, db) and floor in PUBLIC:
                floor = "private"
            revision = self._update_admission(
                db,
                sid,
                expected_revision,
                state,
                existing,
                ref,
                path,
                classification,
                detail,
                floor,
                roots,
            )
            if ref["kind"] == "expression_matrix" and (
                ref["representation_class"]
                not in {"count_1_raw_counts", "aggregated_unit_counts"}
                or detail.get("count_1_forensics", {}).get("suspected_transformed")
            ):
                db.executemany(
                    "INSERT OR IGNORE INTO mcp_noncount_roots VALUES (?)",
                    [(sha,), (detail.get("source_sha256", sha),)],
                )
            if classification not in PUBLIC:
                db.executemany(
                    "INSERT OR IGNORE INTO mcp_private_digests VALUES (?)",
                    [(x,) for x in roots],
                )
            private = self.protected(sid, roots, db)
            if existing:
                ref = json.loads(existing["ref_json"])
            if not private:
                self.check_source(db, sid, sha)
            from .server import check_deadline

            check_deadline()
        return {
            "session_id": sid,
            "revision": revision,
            "artifact": None if private else ref,
            "opaque_artifact": {"artifact_id": ref["artifact_id"], "sha256": sha}
            if private
            else None,
            "private_tainted": private,
            "category": PRIVATE if private else "registered",
            **self.handoff(sid, private),
        }

    def _update_admission(
        self,
        db,
        sid,
        expected,
        state,
        existing,
        ref,
        path,
        classification,
        detail,
        floor,
        roots,
    ):
        if existing and existing["sha256"] != ref["sha256"]:
            raise OperationBlocked("plan_identity_mismatch")
        from .server import check_deadline

        check_deadline()
        if existing and classification in PUBLIC and floor in PUBLIC:
            retained = json.loads(existing["ref_json"])
            if ref["representation_class"] != retained["representation_class"] or (
                detail.get("provenance_declaration")
                != json.loads(existing["detail_json"]).get("provenance_declaration")
            ):
                raise OperationBlocked("plan_identity_mismatch")
        if (
            existing
            and floor == state["classification"]
            and (classification in PUBLIC or self.protected(None, [ref["sha256"]], db))
        ):
            return state["revision"]
        if expected is not None and expected != state["revision"]:
            raise OperationBlocked("stale_revision_conflict")
        self._enroll_source(db, sid, ref, classification, members=roots)
        store_artifact(db, sid, ref, path, classification, detail)
        db.execute(
            "UPDATE mcp_sessions SET classification=? WHERE session_id=?", (floor, sid)
        )
        return self.event(
            db,
            sid,
            "mcp_input_admitted",
            {
                "artifact_id": ref["artifact_id"],
                "sha256": ref["sha256"],
                "representation_class": ref["representation_class"],
                "classification": floor,
                "declaration_sha256": digest(detail),
            },
        )
