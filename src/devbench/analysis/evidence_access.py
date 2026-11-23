"""Coordinator-owned access policy and durable reserve-before-delivery broker."""

import hashlib
import json
import re
import uuid
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass

from devbench.core._disclosure_graph import walk_source_ancestry
from devbench.engine.scientific_authority import (
    _ID,
    _SHA,
    DISCLOSURE_SCHEMA,
    _matches,
    check_schema,
)
from devbench.engine.workflow_operations import OperationConflict, _digest, _json

_CLASSES = ("structural", "diagnostic", "target_outcome", "unknown")
_ROLES = {"controller", "challenger", "training", "evaluation", "scientist", "auditor"}
_PHASES = {
    "analysis",
    "design_initial",
    "publication_initial",
    "narrative_reveal",
    "training",
    "evaluation",
    "readback",
}
_STATS = {
    "p",
    "q",
    "p_value",
    "pvalue",
    "q_value",
    "q_local",
    "q_budget",
    "padj",
    "adjusted_p_value",
    "significant",
    "discovery_count",
    "significance",
    "pvalues",
    "qvalues",
    "fdr",
    "statistic",
    "F",
    "confidence_interval",
    "significance_stars",
}
_NORMAL_STATS = {re.sub("[^a-z0-9]", "", value.lower()) for value in _STATS}
_POLICY_SCOPES = ("roots", "units", "populations")


def _has_statistics(fields):
    return any(
        re.sub("[^a-z0-9]", "", field.lower()) in _NORMAL_STATS for field in fields
    )


_SECRET_TAGS = {"controller_narrative", "validation_plan"}
_TRAINING_RESTRICTED_TAGS = {"protected_evaluation", "full_cohort_learned_state"}
_TABLES = {
    "policy": "scientific_access_policies",
    "context": "scientific_access_contexts",
    "source": "scientific_access_sources",
    "reservation": "scientific_disclosures",
}
_INVENTORY_BOUNDS = {
    "policy": (256, "AccessPolicy inventory exceeds its bound"),
    "context": (256, "Context inventory exceeds its bound"),
    "reservation": (4096, "Exposure history exceeds the bounded projection"),
}
_CONTEXT_ANCESTRY_LIMIT = 128
_SOURCE_SCOPE_LIMIT = 64


class AccessDenied(PermissionError):
    """The requested envelope is outside the recipient's current entitlement."""


def _ids(values, *, hashes=False, nonempty=False):
    return (
        type(values) is tuple
        and len(values) <= _SOURCE_SCOPE_LIMIT
        and (not nonempty or bool(values))
        and all(_matches(_SHA if hashes else _ID, value) for value in values)
        and len(values) == len(set(values))
    )


def _max_class(*values):
    return max(values, key=_CLASSES.index)


def _bytes(payload):
    if isinstance(payload, bytes):
        raw = payload
    else:
        raw = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    if len(raw) > 16 * 1024**2:
        raise ValueError("Evidence envelope exceeds the bounded delivery size")
    return raw


def binary_evidence_reference(value, *, expected_sha256):
    """Project only a path-free reference to exact separately authorized custody."""
    fields = {"format", "sha256", "size_bytes", "row_count"}
    if (
        type(value) is not dict
        or value.keys() != fields
        or value["format"] != "parquet"
        or not _matches(_SHA, expected_sha256)
        or value["sha256"] != expected_sha256
        or type(value["size_bytes"]) is not int
        or not 0 < value["size_bytes"] <= 256 * 1024**2
        or type(value["row_count"]) is not int
        or not 0 < value["row_count"] <= 100_000
    ):
        raise ValueError(
            "Binary evidence needs a bounded, hash-bound artifact reference"
        )
    return {key: value[key] for key in sorted(fields)}


def _fields(payload):
    if type(payload) in (bytes, str):
        return None
    fields, pending, count = set(), [payload], 0
    while pending:
        value = pending.pop()
        count += 1
        if count > 1_000_000:
            raise ValueError("Evidence field traversal exceeds its bound")
        if type(value) is dict:
            if any(type(key) is not str for key in value):
                raise ValueError("Evidence keys must be strings")
            fields.update(value)
            pending.extend(value.values())
        elif type(value) in (list, tuple):
            pending.extend(value)
    return fields


@dataclass(frozen=True)
class AccessPolicy:
    policy_id: str
    version: int
    allow_outcomes: bool = False
    allow_statistics: bool = False
    allowed_fields: tuple[str, ...] = ()
    diagnostic_policy_id: str | None = None
    allowed_roots: tuple[str, ...] = ()
    allowed_units: tuple[str, ...] = ()
    allowed_populations: tuple[str, ...] = ()

    def __post_init__(self):
        if (
            not _matches(_ID, self.policy_id)
            or type(self.version) is not int
            or self.version < 1
            or type(self.allow_outcomes) is not bool
            or type(self.allow_statistics) is not bool
            or not _ids(self.allowed_fields)
            or not _ids(self.allowed_roots, hashes=True)
            or not _ids(self.allowed_units)
            or not _ids(self.allowed_populations)
            or self.diagnostic_policy_id is not None
            and not _matches(_SHA, self.diagnostic_policy_id)
        ):
            raise ValueError("Invalid versioned AccessPolicy")

    def document(self):
        fields = {
            name: list(value) if isinstance(value, tuple) else value
            for name, value in asdict(self).items()
        }
        return {"format": "devbench.access-policy/1", **fields}

    def sha256(self):
        return _digest(_json(self.document()))


class EvidenceBroker:
    """Use the existing scientific authority database and outbox."""

    def __init__(self, authority):
        self.authority = authority
        authority.ledger._writable()
        with authority.ledger._database() as db:
            db.execute("BEGIN IMMEDIATE")
            check_schema(db)
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='scientific_disclosure_schema'"
            ).fetchone():
                for sql in DISCLOSURE_SCHEMA.values():
                    db.execute(sql)
                db.execute("INSERT INTO scientific_disclosure_schema VALUES (1)")
                db.execute("INSERT INTO scientific_disclosure_clock VALUES (1,0)")

    @classmethod
    def open_readonly(cls, authority):
        """Inspect an existing disclosure authority without migration or creation."""
        if not authority.ledger.readonly or not authority.ledger.inert:
            raise ValueError(
                "Read-only evidence access requires an inert read-only ledger"
            )
        return cls.open_existing(authority)

    @classmethod
    def open_existing(cls, authority):
        """Read current disclosure policy without creating a schema or a grant."""

        def check(db):
            if (
                not check_schema(db)
                or not db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='scientific_disclosure_"
                    "schema'"
                ).fetchone()
            ):
                raise ValueError("Existing compatible disclosure schema is required")

        authority.ledger._read(check)
        result = cls.__new__(cls)
        result.authority = authority
        return result

    @contextmanager
    def _transaction(self):
        self.authority.ledger._writable()
        with self.authority.ledger._database() as db:
            db.execute("BEGIN IMMEDIATE")
            check_schema(db)
            yield db

    @staticmethod
    def _get(db, kind, identity):
        row = db.execute(
            f"SELECT payload,sha256 FROM {_TABLES[kind]} WHERE identity=?", (identity,)
        ).fetchone()
        if row is None:
            raise AccessDenied("Unregistered evidence " + kind)
        value = json.loads(row[0])
        if _json(value) != row[0] or _digest(row[0]) != row[1]:
            raise OperationConflict("Evidence access record integrity mismatch")
        return value

    @staticmethod
    def _require_inventory_slot(db, kind):
        limit, message = _INVENTORY_BOUNDS[kind]
        if db.execute(
            f"SELECT 1 FROM {_TABLES[kind]} LIMIT 1 OFFSET ?", (limit - 1,)
        ).fetchone():
            raise AccessDenied(message)

    @staticmethod
    def _inventory(db, kind, columns="identity"):
        limit, message = _INVENTORY_BOUNDS[kind]
        rows = db.execute(
            f"SELECT {columns} FROM {_TABLES[kind]} LIMIT ?", (limit + 1,)
        ).fetchall()
        if len(rows) > limit:
            raise AccessDenied(message)
        return rows

    def _put(self, db, kind, identity, value, *, update=False):
        raw = _json(value)
        table = _TABLES[kind]
        if update:
            changed = db.execute(
                f"UPDATE {table} SET payload=?,sha256=? WHERE identity=?",
                (raw, _digest(raw), identity),
            )
            if changed.rowcount != 1:
                raise OperationConflict("Disclosure reservation disappeared")
        else:
            found = db.execute(
                f"SELECT payload FROM {table} WHERE identity=?", (identity,)
            ).fetchone()
            if found is not None:
                if found[0] != raw:
                    raise OperationConflict(
                        "Evidence access identity cannot be rebound"
                    )
                return
            if kind in _INVENTORY_BOUNDS:
                self._require_inventory_slot(db, kind)
            if kind == "source":
                self._copied_source_scope(
                    [*self._source_peers(db, value["content_sha256"]), value]
                )
            if kind == "context":
                self._context_principals(db, value)
            db.execute(
                f"INSERT INTO {table} VALUES (?,?,?)", (identity, raw, _digest(raw))
            )

    def register_policy(self, policy):
        if type(policy) is not AccessPolicy:
            raise ValueError("Use a validated AccessPolicy")
        with self._transaction() as db:
            policies = self._inventory(db, "policy", "identity,payload")
            for identity, raw in policies:
                prior = json.loads(raw)
                if (
                    prior["policy_id"] == policy.policy_id
                    and prior["version"] == policy.version
                    and identity != policy.sha256()
                ):
                    raise OperationConflict("AccessPolicy version cannot be changed")
            self._put(db, "policy", policy.sha256(), policy.document())
        return policy.sha256()

    def register_context(
        self,
        context_id,
        *,
        principal_id,
        lineage_id,
        role,
        phase,
        policy_sha256,
        parents=(),
    ):
        if (
            not all(
                _matches(_ID, value) for value in (context_id, principal_id, lineage_id)
            )
            or role not in _ROLES
            or phase not in _PHASES
            or not _matches(_SHA, policy_sha256)
            or not _ids(parents)
            or context_id in parents
        ):
            raise ValueError("Invalid principal/context/phase binding")
        with self._transaction() as db:
            self._get(db, "policy", policy_sha256)
            if not db.execute(
                (
                    'SELECT 1 FROM scientific_records WHERE lineage_id=? AND '
                    "kind='binding'"
                ),
                (lineage_id,),
            ).fetchone():
                raise AccessDenied(
                    "Context needs an existing scientific lineage binding"
                )
            context = {
                "context_id": context_id,
                "principal_id": principal_id,
                "lineage_id": lineage_id,
                "role": role,
                "phase": phase,
                "policy_sha256": policy_sha256,
                "parents": sorted(parents),
            }
            self._put(db, "context", context_id, context)
        return context_id

    def register_source(
        self,
        source_id,
        *,
        content_sha256,
        roots,
        units=(),
        populations=(),
        tags=(),
        classification="unknown",
        parents=(),
    ):
        if (
            not _matches(_ID, source_id)
            or not _matches(_SHA, content_sha256)
            or not _ids(roots, hashes=True, nonempty=True)
            or not _ids(units)
            or not _ids(populations)
            or not _ids(tags)
            or not _ids(parents)
            or classification not in _CLASSES
        ):
            raise ValueError("Invalid immutable evidence source scope")
        with self._transaction() as db:
            inherited = [self._source(db, parent) for parent in parents]
            merged = {}
            for name, own in (
                ("roots", roots),
                ("units", units),
                ("populations", populations),
                ("tags", tags),
            ):
                # Empty unit/population scope represents the full source scope.
                sets = [list(own), *(row[name] for row in inherited)]
                merged[name] = (
                    []
                    if name in {"units", "populations"} and any(not x for x in sets)
                    else sorted(set().union(*map(set, sets)))
                )
                if len(merged[name]) > _SOURCE_SCOPE_LIMIT:
                    raise ValueError("Evidence ancestry exceeds the bounded scope")
            record = {
                "source_id": source_id,
                "content_sha256": content_sha256,
                **merged,
                "classification": _max_class(
                    classification, *(row["classification"] for row in inherited)
                ),
                "parents": sorted(parents),
            }
            self._put(db, "source", source_id, record)
        return source_id

    def _source_peers(self, db, content_sha256):
        peers = db.execute(
            "SELECT identity FROM scientific_access_sources WHERE "
            "json_extract(payload,'$.content_sha256')=? LIMIT ?",
            (content_sha256, _SOURCE_SCOPE_LIMIT + 1),
        ).fetchall()
        return [self._get(db, "source", identity) for (identity,) in peers]

    @staticmethod
    def _copied_source_scope(rows):
        if len(rows) > _SOURCE_SCOPE_LIMIT:
            raise AccessDenied("Copied source ancestry exceeds its bound")
        merged = {}
        for name in ("roots", "units", "populations", "tags"):
            scopes = [row[name] for row in rows]
            merged[name] = (
                []
                if name in {"units", "populations"} and any(not x for x in scopes)
                else sorted(set().union(*map(set, scopes)))
            )
            if len(merged[name]) > _SOURCE_SCOPE_LIMIT:
                raise AccessDenied("Copied source scope exceeds its bound")
        return {
            **merged,
            "classification": _max_class(*(row["classification"] for row in rows)),
        }

    def _source(self, db, source_id):
        from devbench.engine.partition_authority import embargoed

        source = self._get(db, "source", source_id)
        source.update(
            self._copied_source_scope(self._source_peers(db, source["content_sha256"]))
        )
        if any(
            embargoed(db, identity)
            for identity in (source["content_sha256"], *source["roots"])
        ):
            raise AccessDenied("Protected evaluation custody denies evidence delivery")
        return source

    def _source_ancestry(self, db, identities, *, limit_error):
        def peer_parents(content_sha256):
            for peer in self._source_peers(db, content_sha256):
                yield from peer["parents"]

        return walk_source_ancestry(
            identities,
            lambda identity: self._source(db, identity),
            limit_error=limit_error,
            peer_parents=peer_parents,
        )

    def delivery(self, context_id, source_id):
        return EvidenceDelivery(self, context_id, source_id)

    def check_delivery(self, context_id, source_id, *, lineage_id, role):
        """Revalidate a local read against the current source and recipient scope."""

        def check(db):
            context, policy = self._context_policy(db, context_id)
            if context["lineage_id"] != lineage_id or context["role"] != role:
                raise AccessDenied("Evidence recipient is outside the bound lineage")
            self._check(context, policy, self._source(db, source_id), set())

        self.authority.ledger._read(check)

    def _context_policy(self, db, context_id):
        context = self._get(db, "context", context_id)
        document = self._get(db, "policy", context["policy_sha256"])
        versions = [
            (identity, self._get(db, "policy", identity))
            for (identity,) in self._inventory(db, "policy")
        ]
        latest = max(
            (
                (identity, row)
                for identity, row in versions
                if row["policy_id"] == document["policy_id"]
            ),
            key=lambda pair: pair[1]["version"],
        )[0]
        if latest != context["policy_sha256"]:
            raise AccessDenied("AccessPolicy version is stale")
        fields = {key: value for key, value in document.items() if key != "format"}
        fields = {
            key: tuple(value) if isinstance(value, list) else value
            for key, value in fields.items()
        }
        return context, AccessPolicy(**fields)

    @staticmethod
    def _overlaps(left, right):
        return bool(set(left["roots"]) & set(right["roots"])) and all(
            not left[key] or not right[key] or set(left[key]) & set(right[key])
            for key in ("units", "populations")
        )

    def _context_principals(self, db, context):
        pending = list(context["parents"])
        seen, principals = {context["context_id"]}, {context["principal_id"]}
        while pending:
            identity = pending.pop()
            if identity in seen:
                continue
            seen.add(identity)
            if len(seen) > _CONTEXT_ANCESTRY_LIMIT:
                raise AccessDenied("Context inheritance exceeds its bound")
            parent = self._get(db, "context", identity)
            principals.add(parent["principal_id"])
            pending.extend(parent["parents"])
        return principals

    def _prior(self, db, context, source):
        principals = self._context_principals(db, context)
        contexts = self._inventory(db, "context")
        entitled = {
            row[0]
            for row in contexts
            if self._get(db, "context", row[0])["principal_id"] in principals
        }
        rows = []
        identities = self._inventory(db, "reservation")
        for (identity,) in identities:
            row = self._get(db, "reservation", identity)
            if (
                row["context_id"] in entitled
                and row["possible"]
                and self._overlaps(row["scope"], source)
            ):
                rows.append(row)
        return rows

    @staticmethod
    def _check(context, policy, source, fields):
        for name in _POLICY_SCOPES:
            allowed = getattr(policy, "allowed_" + name)
            if allowed and (not source[name] or set(source[name]) - set(allowed)):
                raise AccessDenied(
                    "Evidence source exceeds its registered " + name + " entitlement"
                )
        tags = set(source["tags"])
        if (
            context["role"] == "challenger"
            and context["phase"] in {"design_initial", "publication_initial"}
            and tags & _SECRET_TAGS
        ):
            raise AccessDenied(
                'Initial challenger context cannot receive controller narrative or '
                'plan handles'
            )
        if context["role"] == "training" and tags & _TRAINING_RESTRICTED_TAGS:
            raise AccessDenied(
                'Training context cannot receive protected evaluation or '
                'full-cohort learned state'
            )
        if not policy.allow_statistics and (
            fields is None
            or _has_statistics(fields)
            or "inferential_statistics" in tags
        ):
            raise AccessDenied(
                "Inferential fields or opaque content are outside this policy"
            )
        if policy.allowed_fields and (
            fields is None or fields - set(policy.allowed_fields)
        ):
            raise AccessDenied("Evidence fields exceed the registered policy")
        if (
            source["classification"] == "diagnostic"
            and policy.diagnostic_policy_id is None
        ):
            raise AccessDenied("Diagnostic access requires a named policy")
        if (
            source["classification"] in {"target_outcome", "unknown"}
            and not policy.allow_outcomes
        ):
            raise AccessDenied("Outcome or unknown evidence is outside this policy")

    def check_query(self, context_id, query):
        with self.authority.ledger._database() as db:
            _, policy = self._context_policy(db, context_id)
        keys = set(query.get("filters", {})) | {query.get("sort_by", "gene_id")}
        if not policy.allow_statistics and (
            query.get("mode") == "aggregate"
            or _has_statistics(keys)
            or any(
                key.lower().startswith(field.lower() + "_")
                for key in keys
                for field in _STATS
            )
        ):
            raise AccessDenied(
                "Significance sorting, filtering or aggregation is outside this policy"
            )

    @staticmethod
    def _query_dependencies(db, dependencies):
        from devbench.engine.partition_authority import embargoed

        if not _ids(dependencies, hashes=True):
            raise AccessDenied("Invalid query dependency digests")
        if any(embargoed(db, identity) for identity in dependencies):
            raise AccessDenied(
                "Protected evaluation custody denies evidence query inputs"
            )

    def query_admission(
        self, context_id, source_id, query_sha256, cursor, *, dependencies=()
    ):
        """Resolve an opaque continuation against its durable issuing reservation."""
        with self.authority.ledger._database() as db:
            db.execute("BEGIN")
            context, policy = self._context_policy(db, context_id)
            self._source(db, source_id)
            self._query_dependencies(db, dependencies)
            if cursor is None:
                return policy, 0
            row = self._get(db, "reservation", cursor)
            page = row.get("query_page", {})
            if (
                row["context_id"] != context_id
                or row["source_id"] != source_id
                or row["policy_sha256"] != context["policy_sha256"]
                or page.get("principal_id") != context["principal_id"]
                or page.get("query_sha256") != query_sha256
                or page.get("next_offset") is None
                or row["state"] not in {"delivery_attempted", "receipt_confirmed"}
            ):
                raise AccessDenied(
                    "Evidence cursor does not match the current authority or query"
                )
            return policy, page["next_offset"]

    def release_query_page(
        self,
        context_id,
        source_id,
        query_sha256,
        policy_sha256,
        build,
        *,
        _guard=None,
        dependencies=(),
        _reservation_id=None,
    ):
        # The receipt includes the reservation ID, so build it before hashing
        # and commit the exact resulting envelope before exposing it.
        payload = None

        def prepare(identity, context, policy, source):
            nonlocal payload
            if context["policy_sha256"] != policy_sha256:
                raise AccessDenied("AccessPolicy changed during evidence query")
            payload = build(identity, context, policy, source)
            return payload, {
                "query_sha256": query_sha256,
                "principal_id": context["principal_id"],
                "next_offset": payload.get("next_offset"),
                "dependencies": list(dependencies),
            }

        def guard(db):
            self._query_dependencies(db, dependencies)
            if _guard is not None:
                _guard(db)

        row = self.reserve(
            context_id,
            source_id,
            None,
            _prepare_page=prepare,
            _guard=guard,
            _query_reservation_id=_reservation_id,
        )
        if row["state"] == "reserved":
            self.mark_attempted(row["reservation_id"], _guard=guard)
        return payload

    def verify_query_page(self, payload):
        """Verify a retained page against the reservation of its exact envelope."""
        row = self.reservation(payload["query_receipt"]["disclosure_reservation_id"])
        return (
            bool(row.get("query_page"))
            and row["state"] in {"delivery_attempted", "receipt_confirmed"}
            and row["content_sha256"] == hashlib.sha256(_bytes(payload)).hexdigest()
        )

    def _reservation_content(
        self,
        db,
        context_id,
        source_id,
        payload,
        *,
        opaque,
        publication,
        query,
    ):
        raw = _bytes(payload)
        from devbench.engine.partition_authority import embargoed

        if embargoed(db, hashlib.sha256(raw).hexdigest()):
            raise AccessDenied("Protected evaluation custody denies evidence delivery")
        semantic = payload
        projection = None
        if publication is not None:
            from .scientific_publication import RenderedPublication

            if type(publication) is not RenderedPublication or opaque or query:
                raise AccessDenied(
                    "Publication requires a trusted renderer-bound projection"
                )
            semantic, projection = publication.verify(
                self,
                context_id,
                source_id,
                raw,
                db,
            )
        if isinstance(payload, bytes) and not opaque and publication is None:
            try:
                semantic = json.loads(payload)
            except (ValueError, UnicodeError):
                pass
        return raw, None if opaque else _fields(semantic), projection

    def _reservation_scope(self, db, context, policy, source, fields):
        prior = self._prior(db, context, source)
        tags = set(source["tags"]).union(*(row["scope"]["tags"] for row in prior))
        if fields is not None:
            tags |= fields & (_SECRET_TAGS | _TRAINING_RESTRICTED_TAGS)
        incoming = deepcopy(source)
        incoming["tags"] = sorted(tags)
        if (
            fields is not None
            and _has_statistics(fields)
            or {"masked_expression", "target_mapping"} <= tags
        ):
            incoming["classification"] = _max_class(
                incoming["classification"],
                "target_outcome",
            )
        self._check(context, policy, incoming, fields)
        return incoming, prior

    def _reuse_reservation(
        self,
        db,
        identity,
        context,
        source,
        *,
        context_id,
        source_id,
        content_sha256,
        query_page,
        stable_query,
        controller_context,
    ):
        if controller_context:
            identity = _digest(
                _json(
                    [
                        "controller-planning-context",
                        context_id,
                        source_id,
                        content_sha256,
                    ]
                )
            )[:32]
            if source["content_sha256"] != content_sha256:
                raise AccessDenied(
                    "Controller planning context differs from its exact registered "
                    "source"
                )
        if (
            not (stable_query or controller_context)
            or not db.execute(
                "SELECT 1 FROM scientific_disclosures WHERE identity=?",
                (identity,),
            ).fetchone()
        ):
            return identity, None
        retained = self._get(db, "reservation", identity)
        if (
            retained["context_id"] != context_id
            or retained["source_id"] != source_id
            or retained["policy_sha256"] != context["policy_sha256"]
            or retained["content_sha256"] != content_sha256
            or retained.get("query_page") != query_page
            or retained["state"]
            not in {
                "reserved",
                "delivery_attempted",
                "receipt_confirmed",
            }
        ):
            raise OperationConflict(
                "Current evidence reservation conflicts with its retained request"
            )
        return identity, retained

    def _record_reservation(
        self,
        db,
        context,
        reservation,
        *,
        query_page,
        publication_projection,
    ):
        if query_page is not None:
            reservation["query_page"] = query_page
        if publication_projection is not None:
            reservation["publication_projection"] = publication_projection
        self._put(db, "reservation", reservation["reservation_id"], reservation)
        db.execute(
            "UPDATE scientific_disclosure_clock SET watermark=watermark+1 "
            "WHERE singleton=1"
        )
        self._event(db, context, "disclosure_reserved", reservation)

    def reserve(
        self,
        context_id,
        source_id,
        payload,
        *,
        expected_watermark=None,
        opaque=False,
        maximum_bytes=None,
        _prepare_page=None,
        _guard=None,
        _publication=None,
        _query_reservation_id=None,
        _controller_context=False,
    ):
        if _query_reservation_id is not None and (
            _prepare_page is None
            or type(_query_reservation_id) is not str
            or re.fullmatch(r"[a-f0-9]{32}", _query_reservation_id) is None
        ):
            raise ValueError(
                "Stable evidence reservations require a bound current query"
            )
        with self._transaction() as db:
            context, policy = self._context_policy(db, context_id)
            source = self._source(db, source_id)
            if _controller_context and (
                context["role"] != "controller"
                or context["phase"] != "analysis"
                or opaque
                or _publication is not None
                or _prepare_page is not None
                or not callable(_guard)
            ):
                raise AccessDenied(
                    'Stable controller delivery requires its current scoped '
                    'planning context'
                )
            if _guard is not None:
                _guard(db)
            identity = _query_reservation_id or uuid.uuid4().hex
            query_page = None
            if _prepare_page is not None:
                if (
                    _query_reservation_id is None
                    or not db.execute(
                        "SELECT 1 FROM scientific_disclosures WHERE identity=?",
                        (identity,),
                    ).fetchone()
                ):
                    self._require_inventory_slot(db, "reservation")
                payload, query_page = _prepare_page(identity, context, policy, source)
            raw, fields, publication_projection = self._reservation_content(
                db,
                context_id,
                source_id,
                payload,
                opaque=opaque,
                publication=_publication,
                query=_prepare_page is not None,
            )
            incoming, prior = self._reservation_scope(
                db,
                context,
                policy,
                source,
                fields,
            )
            content_sha256 = hashlib.sha256(raw).hexdigest()
            identity, retained = self._reuse_reservation(
                db,
                identity,
                context,
                source,
                context_id=context_id,
                source_id=source_id,
                content_sha256=content_sha256,
                query_page=query_page,
                stable_query=_query_reservation_id is not None,
                controller_context=_controller_context,
            )
            if retained is not None:
                return retained
            if _prepare_page is None:
                self._require_inventory_slot(db, "reservation")
            watermark = db.execute(
                "SELECT watermark FROM scientific_disclosure_clock WHERE singleton=1"
            ).fetchone()[0]
            classification = _max_class(
                incoming["classification"], *(row["classification"] for row in prior)
            )
            if maximum_bytes is not None and (
                not opaque
                or type(maximum_bytes) is not int
                or not 0 <= maximum_bytes <= 16 * 1024**2
            ):
                raise ValueError("Invalid bounded stream envelope")
            reservation = {
                "reservation_id": identity,
                "context_id": context_id,
                "source_id": source_id,
                "content_sha256": content_sha256,
                "content_identity_kind": "source_query_envelope"
                if opaque
                else "serialized_content",
                "content_bytes": len(raw) if maximum_bytes is None else maximum_bytes,
                "policy_sha256": context["policy_sha256"],
                "scope": incoming,
                "classification": classification,
                "state": "reserved",
                "possible": True,
                "confirmed": False,
                "receipt_sha256": None,
                "watermark": watermark + 1,
                "recomputed_from_stale_watermark": expected_watermark
                not in (None, watermark),
            }
            self._record_reservation(
                db,
                context,
                reservation,
                query_page=query_page,
                publication_projection=publication_projection,
            )
            return reservation

    def _event(self, db, context, event, reservation):
        self.authority._append_execution_record(
            db,
            context["lineage_id"],
            {
                "kind": "disclosure_event",
                "record_id": "disclosure-" + uuid.uuid4().hex,
                "event": event,
                "state_sha256": _digest(_json(reservation)),
                "state": reservation,
            },
        )

    def reservation(self, reservation_id):
        with self.authority.ledger._database() as db:
            return self._get(db, "reservation", reservation_id)

    def mark_attempted(self, reservation_id, *, _guard=None):
        with self._transaction() as db:
            row = self._get(db, "reservation", reservation_id)
            if row["state"] != "reserved":
                raise OperationConflict("Disclosure delivery was already attempted")
            context, _ = self._context_policy(db, row["context_id"])
            self._source(db, row["source_id"])
            self._query_dependencies(
                db, tuple(row.get("query_page", {}).get("dependencies", ()))
            )
            from devbench.engine.partition_authority import embargoed

            if embargoed(db, row["content_sha256"]):
                raise AccessDenied(
                    "Protected evaluation custody denies evidence delivery"
                )
            if _guard is not None:
                _guard(db)
            row["state"] = "delivery_attempted"
            self._put(db, "reservation", reservation_id, row, update=True)
            self._event(db, context, "disclosure_delivery_attempted", row)
            return row

    def confirm_receipt(
        self, reservation_id, *, receipt_sha256, confirmed_bytes, verify_receipt
    ):
        row = self.reservation(reservation_id)
        if (
            not _matches(_SHA, receipt_sha256)
            or type(confirmed_bytes) is not int
            or not 0 <= confirmed_bytes <= row["content_bytes"]
        ):
            raise ValueError("Receipt must bind a bounded delivered scope")
        proof = {
            "reservation_id": reservation_id,
            "content_sha256": row["content_sha256"],
            "receipt_sha256": receipt_sha256,
            "confirmed_bytes": confirmed_bytes,
        }
        if not callable(verify_receipt) or verify_receipt(deepcopy(proof)) is not True:
            raise AccessDenied("Disclosure receipt is unverified")
        with self._transaction() as db:
            row = self._get(db, "reservation", reservation_id)
            if row["state"] not in {"delivery_attempted", "receipt_confirmed"}:
                raise OperationConflict(
                    "Receipt cannot confirm an unattempted disclosure"
                )
            if confirmed_bytes < row.get("confirmed_bytes", 0):
                raise OperationConflict("Receipt cannot erase confirmed delivery")
            context = self._get(db, "context", row["context_id"])
            row.update(
                state="receipt_confirmed",
                confirmed=True,
                confirmed_bytes=confirmed_bytes,
                receipt_sha256=receipt_sha256,
            )
            self._put(db, "reservation", reservation_id, row, update=True)
            self._event(db, context, "disclosure_receipt_confirmed", row)
            return row

    def prove_no_delivery(self, reservation_id, *, proof_sha256, verify_no_delivery):
        snapshot = self.reservation(reservation_id)
        if (
            not _matches(_SHA, proof_sha256)
            or not callable(verify_no_delivery)
            or verify_no_delivery(deepcopy(snapshot), proof_sha256) is not True
        ):
            raise AccessDenied(
                "A timeout or missing acknowledgment is not proof of no delivery"
            )
        with self._transaction() as db:
            row = self._get(db, "reservation", reservation_id)
            if row["confirmed"]:
                raise OperationConflict("Cannot erase confirmed disclosure")
            if row != snapshot:
                raise OperationConflict(
                    "Disclosure changed while verifying no delivery"
                )
            context = self._get(db, "context", row["context_id"])
            row.update(
                state="no_delivery_proven", possible=False, proof_sha256=proof_sha256
            )
            self._put(db, "reservation", reservation_id, row, update=True)
            self._event(db, context, "disclosure_no_delivery_proven", row)
            return row

    def exposure(self, context_id, source_id):
        with self.authority.ledger._database() as db:
            db.execute("BEGIN")
            context, _ = self._context_policy(db, context_id)
            source = self._source(db, source_id)
            prior = self._prior(db, context, source)
            return {
                "classification": _max_class(
                    "structural", *(row["classification"] for row in prior)
                ),
                "possible_reservations": len(prior),
                "confirmed_reservations": sum(row["confirmed"] for row in prior),
                "inherited_tags": sorted(
                    set().union(*(set(row["scope"]["tags"]) for row in prior))
                ),
                "reservation_ids": [row["reservation_id"] for row in prior],
            }

    def replay_dependencies(self, lineage_id, *, connection):
        """Resolve original exposure ancestry; copied records grant no access."""
        from .scientific_publication import _same_transaction

        _same_transaction(connection, self.authority.ledger)
        contexts, policies, disclosures = {}, {}, {}
        pending = [
            row[0]
            for row in connection.execute(
                "SELECT identity FROM scientific_access_contexts "
                "WHERE json_extract(payload,'$.lineage_id')=? LIMIT 257",
                (lineage_id,),
            )
        ]
        while pending:
            identity = pending.pop()
            if identity in contexts:
                continue
            if len(contexts) >= 256:
                raise ValueError("replay_context_closure_exceeds_bound")
            context = self._get(connection, "context", identity)
            contexts[identity] = context
            pending.extend(context["parents"])
            policies[context["policy_sha256"]] = self._get(
                connection, "policy", context["policy_sha256"]
            )
        for identity in contexts:
            rows = connection.execute(
                "SELECT identity FROM scientific_disclosures "
                "WHERE json_extract(payload,'$.context_id')=? LIMIT 4097",
                (identity,),
            ).fetchall()
            if len(disclosures) + len(rows) > 4096:
                raise ValueError("replay_disclosure_closure_exceeds_bound")
            for (reservation_id,) in rows:
                disclosures[reservation_id] = self._get(
                    connection, "reservation", reservation_id
                )

        def original_source(identity):
            self._source(
                connection, identity
            )  # Protected custody is never inherited from audit scope.
            return self._get(connection, "source", identity)

        sources = dict(
            walk_source_ancestry(
                (record["source_id"] for record in disclosures.values()),
                original_source,
                limit_error=ValueError("replay_source_ancestry_exceeds_bound"),
            )
        )
        result = {
            "contexts": contexts,
            "policies": policies,
            "sources": sources,
            "disclosures": disclosures,
        }
        # _get checked canonical bytes and their original stored digests. Retain
        # those bytes separately so a copied projection cannot rewrite history.
        result["original_records"] = {
            kind: {
                identity: {"payload": _json(value), "sha256": _digest(_json(value))}
                for identity, value in values.items()
            }
            for kind, values in result.items()
        }
        return result


@dataclass(frozen=True)
class EvidenceDelivery:
    broker: EvidenceBroker
    context_id: str
    source_id: str

    def check_query(self, query):
        self.broker.check_query(self.context_id, query)

    def transmit(self, payload, send, *, _guard=None):
        disclosed = deepcopy(payload)
        reservation = self.broker.reserve(
            self.context_id, self.source_id, disclosed, _guard=_guard
        )
        self.broker.mark_attempted(reservation["reservation_id"], _guard=_guard)
        return send(disclosed)

    def release(self, payload, *, _guard=None):
        return self.transmit(payload, lambda value: value, _guard=_guard)
