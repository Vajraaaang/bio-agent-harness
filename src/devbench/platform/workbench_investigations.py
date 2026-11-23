"""Durable question threads over existing guarded workbench/scientific actions.

This controller adds context and a read model, not a scientific engine or grant.
Every execution still belongs to the original reviewed controller. Header
observations and name-based suggestions never become biological declarations.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from devbench.analysis.checkpoints import atomic_json, digest
from devbench.analysis.method_planner_contract import validate_planning_metadata
from devbench.analysis.workbench_request_contract import (
    MOTOR_PROGRAM_TOPIC,
    classify_request,
    matrix_audit_intents,
    split_pinned_context,
)
from devbench.core._files import read_bounded_json
from devbench.engine.workflow_operations import (
    OperationBlocked,
    OperationConflict,
)

from .workbench_investigation_synthesis import WorkbenchInvestigationSynthesis
from .workbench_ui_projection import (
    WorkbenchInvestigationProjection,
    _investigation_event_page,
)

FORMAT = "devbench.investigation/1"
MAX_RECORD_BYTES = 2 * 1024**2
MAX_INVESTIGATIONS = 100
MAX_TURNS = 256
MAX_ANCESTORS = 16
MAX_SELECTION = 1000
DECLARATIONS = {
    "assay",
    "measurement",
    "count_provenance",
    "capture_unit_key",
    "capture_units_confirmed",
    "annotation_provenance",
}
SCIENTIFIC_CONFIRMATIONS = {
    "question_and_scope": "I reviewed the question and proposed scientific scope.",
    "input_provenance": "I reviewed the exact data version and provenance "
    "declarations.",
    "settings_and_limits": "I reviewed the proposed parameters and resource limits.",
    "interpretation_limits": "I reviewed the interpretation and validation "
    "limitations.",
    "trial_criteria": "I reviewed the frozen comparison criteria and selected trial.",
}
INTENTS = {
    "qc_and_integration",
    "batch_effect_audit",
    "method_selection",
    "differential_contrast",
    "trajectory_or_regulon",
}
_IDENTITY = re.compile(r"[0-9a-f]{32}")
_HASH = re.compile(r"[0-9a-f]{64}")
_INVESTIGATION_STATE_SQL = """CREATE TABLE workbench_investigations_v1 (
    identity TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK(revision>0),
    payload BLOB NOT NULL, sha256 TEXT NOT NULL)"""


def _now():
    return datetime.now(UTC).isoformat()


def _copy(value):
    encoded = json.dumps(value, allow_nan=False)
    if len(encoded.encode()) > MAX_RECORD_BYTES:
        raise ValueError("Investigation record exceeds its retained size bound")
    return json.loads(encoded)


def _text(value, name, limit=8000):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > limit
        or "\x00" in value
    ):
        raise ValueError("Use a bounded nonempty " + name)
    return value.strip()


def _identity(value, name="investigation identity"):
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise ValueError("Use an exact " + name)
    return value


def classify_question(question):
    """A transparent local routing inference; never a user method requirement."""
    text = question.lower()
    request = classify_request(question)
    if request["category"] == "descriptive_retrieval":
        kind = "dataset_description"
    elif re.search(r"\b(?:find|compute|identify|discover|test)\b.*\bmarkers?\b", text):
        kind = "differential_contrast"
    elif re.search(
        r"best method|method selection|compare methods|which method|choose.*method",
        text,
    ):
        kind = "method_selection"
    elif re.search(
        r"pseudotime|trajectory|lineage|regulon|regulatory network|motif", text
    ):
        kind = "trajectory_or_regulon"
    elif (
        re.search(
            r"differential|fold.change|contrast|expression changes|gene expression "
            r"between",
            text,
        )
        or re.search(r"compar|between|versus|\bvs\b|difference|changes", text)
        and re.search(
            r"gene expression|expression|composition|populations|abundance|cell.types?",
            text,
        )
    ):
        kind = "differential_contrast"
    elif re.search(
        r"causal|causality|causation|cause.*(?:effect|change)|causes|caused", text
    ):
        kind = "method_selection"
    elif re.search(r"batch|integration|integrate", text):
        kind = "batch_effect_audit"
    elif re.search(
        r"\bqc\b|quality|mitochond|cluster|annotation|annotate|umap|cell.type", text
    ):
        kind = "qc_and_integration"
    else:
        kind = "method_selection"
    return {
        "kind": kind,
        "source": "local_question_classification",
        "inferred": True,
        "requires_confirmation": True,
        "basis": "Question text routing only; method selection and scientific "
        "assumptions require review.",
    }


def _context(value):
    allowed = {
        "data_classification",
        "allow_model_execution",
        "allow_data_execution",
        "auto_advance",
        "auto_synthesis",
        "study_id",
        "dataset_ref",
        "analysis_ref",
        "session_id",
        "plan_request",
        "advance_request",
        "rna_input_contract",
        "rna_execution_inputs_ref",
        "rna_execution_workspace",
        "scientific_contract",
        "counts_layer",
        "biological_independence",
        "raw_droplet_provenance",
        "selection_scope",
        "bulk_episode_declarations",
        "batch_col",
        "condition_col",
    }
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("Use declared structured scientific-context fields")
    if value.get("data_classification", "unclassified") not in {
        "unclassified",
        "synthetic",
        "public",
        "private",
    }:
        raise ValueError("Use a supported explicit input classification")
    for key in (
        "allow_model_execution",
        "allow_data_execution",
        "auto_advance",
        "auto_synthesis",
    ):
        if key in value and type(value[key]) is not bool:
            raise ValueError("Scientific campaign grants require explicit booleans")
    _validate_context_bindings(value)
    if value.get("selection_scope", "saved_evidence_only") not in {
        "saved_evidence_only",
        "context_only_full_dataset",
    }:
        raise ValueError(
            "Use saved evidence or an explicitly confirmed full-dataset selection scope"
        )
    if "bulk_episode_declarations" in value:
        from devbench.analysis.workbench_episode_intake import (
            validate_bulk_declarations,
        )

        validate_bulk_declarations(value["bulk_episode_declarations"])
    return _copy(value)


def _validate_context_bindings(value):
    for key in (
        "dataset_ref",
        "analysis_ref",
        "plan_request",
        "advance_request",
        "rna_input_contract",
        "rna_execution_inputs_ref",
        "scientific_contract",
        "biological_independence",
        "raw_droplet_provenance",
    ):
        if key == "biological_independence" and value.get(key) is None:
            continue
        if key in value and not isinstance(value[key], dict):
            raise ValueError("Use structured scientific version/binding references")
    if "counts_layer" in value:
        _text(value["counts_layer"], "explicit measurement layer", 200)
    for key in ("batch_col", "condition_col"):
        if key in value:
            _text(value[key], "explicit metadata role", 200)


def question_requirements(question, supplied, parameters):
    """Retain literal user assignments; defaults never become requirements."""
    values = validate_planning_metadata(user_parameter_requirements=supplied)[
        "user_parameter_requirements"
    ]
    for item in values:
        if item["evidence"] not in question:
            raise ValueError(
                "Parameter requirement evidence must match the submitted question "
                "exactly"
            )
    by_name = {item["name"]: item for item in values}
    names = "|".join(
        re.escape(key) for key in sorted(parameters, key=len, reverse=True)
    )
    value_pattern = (
        r"(\"[^\"\n]{1,200}\"|'[^'\n]{1,200}'|"
        r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|"
        r"[A-Za-z_][A-Za-z0-9_-]{0,199})"
    )
    matches = list(
        re.finditer(r"\b(" + names + r")\s*[:=]\s*" + value_pattern, question, re.I)
    )
    numeric_names = "|".join(
        re.escape(key)
        for key in parameters
        if "_" in key and type(parameters[key]) in (int, float)
    )
    if numeric_names:
        matches += list(
            re.finditer(
                r"\b(" + numeric_names + r")\s+(?:of\s+|to\s+)?"
                r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)",
                question,
                re.I,
            )
        )
    for match in matches:
        name, raw = match.group(1).lower(), match.group(2)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw.strip("'\"")
        requirement = {"name": name, "value": value, "evidence": match.group(0)}
        if name in by_name and by_name[name]["value"] != value:
            raise ValueError(
                "Conflicting explicit parameter requirements in this question"
            )
        by_name.setdefault(name, requirement)
    return validate_planning_metadata(
        user_parameter_requirements=list(by_name.values())
    )["user_parameter_requirements"]


def _grant_fields(*, advance=False):
    fields = [
        {
            "field": "data_classification",
            "scope": "scientific_context",
            "label": "Dataset sharing scope",
            "type": "select",
            "required": True,
            "options": [
                {"value": value, "label": value}
                for value in ("unclassified", "synthetic", "public", "private")
            ],
            "guidance": "File location and shape do not establish publicness. Private "
            "and unclassified inputs remain local.",
        },
        {
            "field": "allow_model_execution",
            "scope": "scientific_context",
            "label": "Allow model-assisted planning for this campaign",
            "type": "boolean",
            "required": False,
            "options": [],
            "guidance": "Requires a public/synthetic declaration and separately "
            "enabled matching server model scope.",
        },
    ]
    if advance:
        fields += [
            {
                "field": "auto_advance",
                "scope": "scientific_context",
                "label": "Advance planning to the next review gate",
                "type": "boolean",
                "required": False,
                "options": [],
                "guidance": "Runs existing planning nodes; trial and execution review "
                "remain mandatory.",
            },
            {
                "field": "auto_synthesis",
                "scope": "scientific_context",
                "label": "Advance verified results through recommendation and "
                "conclusion review",
                "type": "boolean",
                "required": False,
                "options": [],
                "guidance": "Separate post-execution grant; only runs after all "
                "controlled trials are complete and verified.",
            },
        ]
    return fields


class InvestigationService(
    WorkbenchInvestigationSynthesis, WorkbenchInvestigationProjection
):
    """One local, revision-checked thread owner using the service's permissions."""

    def __init__(self, app, inputs):
        self.app, self.inputs = app, inputs
        self.root = app._path("investigations")
        self._ledger_owners = app._path("investigation-ledger-owners")
        self._lock = threading.RLock()
        self._workflow_ledger = None
        # An old checkpoint cannot renew external campaign consent after restart.
        self._campaign_grants = {}
        self._current_grants = {}
        self._description_controls = {}
        if self.root.exists():
            if self.root.is_symlink() or not self.root.is_dir():
                raise ValueError(
                    "Investigation storage must be a regular protected directory"
                )
            paths = list(self.root.iterdir())
            if len(paths) > MAX_INVESTIGATIONS or any(
                not _IDENTITY.fullmatch(p.stem) or p.suffix != ".json" for p in paths
            ):
                raise ValueError(
                    "Invalid or over-budget retained investigation inventory"
                )
        for identity in self._record_ids():
            self._load(identity, verify_delivery=False)

    def _path(self, identity):
        return self.app._path("investigations", _identity(identity) + ".json")

    def _open_ledger(self, *, write=False):
        """Use the governed workspace's existing workflow authority."""
        scientific = getattr(self.app, "scientific", None)
        open_ledger = getattr(scientific, "open_workflow_ledger", None)
        if not callable(open_ledger):
            return None
        ledger = self._workflow_ledger
        if ledger is not None and (not write or not ledger.readonly):
            return ledger
        ledger = open_ledger(write=write)
        self._workflow_ledger = ledger
        return ledger

    @staticmethod
    def _record_table(db, *, create=False):
        row = db.execute(
            "SELECT sql FROM sqlite_master WHERE name='workbench_investigations_v1'",
        ).fetchone()
        if row is None:
            if create:
                db.execute(_INVESTIGATION_STATE_SQL)
            return create
        if row[0] != _INVESTIGATION_STATE_SQL:
            raise OperationConflict("Unsupported Workbench investigation state schema")
        return True

    def _ledger_record(self, db, identity):
        if not self._record_table(db):
            return None
        row = db.execute(
            "SELECT revision, substr(payload,1,?), sha256 "
            "FROM workbench_investigations_v1 WHERE identity=?",
            (MAX_RECORD_BYTES + 1, identity),
        ).fetchone()
        if row is not None and (
            type(row[1]) is not bytes
            or len(row[1]) > MAX_RECORD_BYTES
            or hashlib.sha256(row[1]).hexdigest() != row[2]
        ):
            raise OperationConflict("Workbench investigation state integrity mismatch")
        return row

    def _record_ids(self):
        identities = {path.stem for path in self.root.glob("*.json")}
        if self._ledger_owners.exists():
            identities.update(
                _identity(path.name) for path in self._ledger_owners.iterdir()
            )
        ledger = self._open_ledger()
        if ledger is not None:

            def indexed(db):
                if not self._record_table(db):
                    return []
                return db.execute(
                    "SELECT identity FROM workbench_investigations_v1 LIMIT ?",
                    (MAX_INVESTIGATIONS + 1,),
                ).fetchall()

            identities.update(_identity(row[0]) for row in ledger._read(indexed))
        if len(identities) > MAX_INVESTIGATIONS:
            raise ValueError(
                "Investigation limit reached; preserve history and use a fresh "
                "workspace"
            )
        return sorted(identities)

    def _owner_path(self, identity):
        return self.app._path("investigation-ledger-owners", _identity(identity))

    @staticmethod
    def _owner_binding(ledger):
        metadata = ledger.path.stat()
        return {
            "format": "devbench.workbench-investigation-owner/1",
            "ledger_sha256": digest(
                [str(ledger.path), metadata.st_dev, metadata.st_ino]
            ),
        }

    def _check_ledger_owner(self, identity, ledger, row):
        owner = self._owner_path(identity)
        if row is None and not owner.exists():
            return
        if (
            row is None
            or not owner.is_file()
            or (read_bounded_json(owner, 1024) != self._owner_binding(ledger))
        ):
            raise OperationConflict("Workbench investigation ledger ownership changed")

    def _retain_ledger_owner(self, identity, ledger):
        self._ledger_owners.mkdir(mode=0o700, exist_ok=True)
        atomic_json(self._owner_path(identity), self._owner_binding(ledger))
        # Persist ownership before SQLite can publish the migrated revision.
        for path in (self._ledger_owners, self._ledger_owners.parent):
            descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _legacy_bytes(self, identity):
        path = self._path(identity)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError as error:
            raise KeyError("Unknown investigation") from error
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > MAX_RECORD_BYTES
            ):
                raise ValueError("Investigation records must be bounded regular files")
            return handle.read(MAX_RECORD_BYTES + 1)

    def _load(self, identity, *, verify_delivery=True):
        identity = _identity(identity)
        ledger = self._open_ledger()
        row = (
            ledger._read(lambda db: self._ledger_record(db, identity))
            if ledger
            else None
        )
        self._check_ledger_owner(identity, ledger, row)
        raw = row[1] if row is not None else self._legacy_bytes(identity)
        value = self._decode_retained_record(raw, identity)
        if row is not None and row[0] != value["revision"]:
            raise OperationConflict(
                "Workbench investigation revision integrity mismatch"
            )
        self._validate_retained_bindings(value)
        for turn in value["turns"]:
            if not isinstance(turn, dict) or turn.get("investigation_id") != identity:
                raise ValueError("Invalid retained investigation turn")
            _identity(turn.get("id"), "turn identity")
        self._verify_retained_proposals(value)
        if verify_delivery:
            self._require_adaptive_intake(value, ledger)
        return _copy(value)

    @staticmethod
    def _decode_retained_record(raw, identity):
        def reject_nonfinite(_):
            raise ValueError("Nonfinite investigation record")

        try:
            value = json.loads(raw, parse_constant=reject_nonfinite)
        except (json.JSONDecodeError, UnicodeError) as error:
            raise ValueError("Invalid retained investigation record") from error
        if (
            not isinstance(value, dict)
            or value.get("format") != FORMAT
            or value.get("id") != identity
            or type(value.get("revision")) is not int
            or not 1 <= value["revision"] < 2**53
            or not isinstance(value.get("turns"), list)
            or len(value["turns"]) > MAX_TURNS
            or not isinstance(value.get("question"), str)
            or not isinstance(value.get("source", False), (str, type(None)))
        ):
            raise ValueError("Invalid retained investigation envelope")
        if (
            not isinstance(value.get("status"), str)
            or not isinstance(value.get("declarations"), dict)
            or not isinstance(value.get("scientific_context"), dict)
            or not isinstance(value.get("scientific_operations"), list)
            or value.get("profile") is not None
            and not isinstance(value["profile"], dict)
        ):
            raise ValueError("Invalid retained investigation state")
        return value

    def _validate_retained_bindings(self, value):
        _identity(value.get("thread_id"), "thread identity")
        if (
            value.get("project_id") is not None
            and value["project_id"] != self._project_id()
        ):
            raise ValueError("Retained investigation belongs to another project")
        if value.get("parent_investigation") is not None:
            _identity(value["parent_investigation"])
        if value.get("job_id") is not None:
            _identity(value["job_id"], "job identity")
        if value["source"] is None:
            if any(value.get(key) for key in (
                "profile", "sources", "selection", "job_id", "scientific_session_id",
            )):
                raise ValueError("A planning-only investigation cannot bind data")
            return
        if not self._within_input_roots(value["source"]):
            raise ValueError(
                "Retained investigation input escaped the configured roots"
            )
        if "sources" in value:
            self._validate_retained_source_set(value)

    def _within_input_roots(self, source):
        path = Path(source)
        return (
            path.is_absolute()
            and path == path.resolve()
            and any(path.is_relative_to(root) for root in self.app.input_roots)
        )

    def _validate_retained_source_set(self, value):
        from devbench.analysis.rna_input_set_contract import (
            MAX_SOURCES,
            source_set_identity,
        )

        sources = value["sources"]
        if (
            type(sources) is not list
            or not 2 <= len(sources) <= MAX_SOURCES
            or any(type(source) is not str for source in sources)
            or len(set(sources)) != len(sources)
            or sources[0] != value["source"]
            or type(value.get("source_profiles")) is not list
            or type(value.get("source_declarations")) is not list
            or len(value["source_declarations"]) != len(sources)
            or any(type(item) is not dict for item in value["source_declarations"])
            or any(type(item) is not dict for item in value["source_profiles"])
        ):
            raise ValueError("Invalid retained ordered input set")
        if any(not self._within_input_roots(source) for source in sources):
            raise ValueError("Retained input-set source escaped the configured roots")
        if value.get("profile") is None:
            return
        profiles, input_set_sha256 = (
            value["source_profiles"],
            value.get("input_set_sha256"),
        )
        if (
            len(profiles) != len(sources)
            or [row.get("source") for row in profiles] != sources
            or source_set_identity(profiles) != input_set_sha256
            or value["profile"].get("sha256") != input_set_sha256
        ):
            raise ValueError("Retained input-set identity does not match every source")

    @staticmethod
    def _verify_retained_proposals(value):
        if value.get("analyst_profile") == "analyst_v1":
            state = value.get("job_state") or {}
            proposals = [
                state.get("proposal"),
                *[
                    turn.get("content", {}).get("proposal")
                    for turn in value["turns"]
                    if turn.get("kind") == "plan_review"
                ],
            ]
            for proposal in proposals:
                if isinstance(proposal, dict) and (
                    proposal.get("metadata_projection")
                    or proposal.get("configuration", {})
                    .get("public_task", {})
                    .get("gene_family")
                ):
                    from devbench.analysis.episode_proposals import verify_proposal_view

                    if not state.get("proposal_receipt"):
                        raise ValueError(
                            "Proposal family reference requires its sealed receipt"
                        )
                    verify_proposal_view(proposal, state["proposal_receipt"])

    def _save(self, record):
        retained = _copy(record)
        retained["revision"] += 1
        retained["updated_at"] = _now()
        # atomic_json writes indented JSON plus a final newline. Enforce the
        # reader's byte bound on that exact representation before replacing it.
        raw = (json.dumps(retained, indent=2, allow_nan=False) + "\n").encode()
        if len(raw) > MAX_RECORD_BYTES:
            raise ValueError("Investigation record exceeds its retained size bound")
        ledger = self._open_ledger(write=True)
        if ledger is None:
            self._check_ledger_owner(retained["id"], None, None)
            self.root.mkdir(mode=0o700, exist_ok=True)
            self.app._path("investigations")
            atomic_json(self._path(retained["id"]), retained)
        else:
            identity = _identity(retained["id"])
            with ledger._database() as db:
                db.execute("BEGIN IMMEDIATE")
                self._record_table(db, create=True)
                existing = self._ledger_record(db, identity)
                self._check_ledger_owner(identity, ledger, existing)
                if existing is not None:
                    revision = existing[0]
                elif record["revision"]:
                    revision = json.loads(self._legacy_bytes(identity))["revision"]
                else:
                    revision = 0
                if record["revision"] != revision:
                    raise OperationConflict("Workbench investigation revision changed")
                if existing is None:
                    self._retain_ledger_owner(identity, ledger)
                db.execute(
                    "INSERT INTO workbench_investigations_v1 VALUES (?,?,?,?) "
                    "ON CONFLICT(identity) DO UPDATE SET revision=excluded.revision, "
                    "payload=excluded.payload, sha256=excluded.sha256",
                    (
                        identity,
                        retained["revision"],
                        raw,
                        hashlib.sha256(raw).hexdigest(),
                    ),
                )
        record.update(revision=retained["revision"], updated_at=retained["updated_at"])

    def _turn(self, record, kind, title, content, *, step, role="workbench"):
        if len(record["turns"]) >= MAX_TURNS:
            raise ValueError(
                "Investigation turn limit reached; start a linked follow-up"
            )
        record["turns"].append(
            {
                "format": "devbench.investigation-event/1",
                "sequence": len(record["turns"]) + 1,
                "id": uuid.uuid4().hex,
                "investigation_id": record["id"],
                "binding": _copy(content.get("binding", self._binding(record))),
                "kind": kind,
                "at": _now(),
                "step": step,
                "role": role,
                "title": title,
                "content": _copy(content),
            }
        )

    def _project_id(self, supplied=None):
        trusted = (
            self.app.workspace_state()["project"]["id"]
            if hasattr(self.app, "workspace_state")
            else digest({"local_workspace": str(self.app.workspace)})[:32]
        )
        if supplied is not None and supplied != trusted:
            raise ValueError(
                "Investigation project does not match the selected workspace"
            )
        return trusted

    def _binding(
        self, record, *, result_ref=None, population_scope="full_source_metadata"
    ):
        profile = record.get("profile") or {}
        set_hash = profile.get("sha256") if record.get("sources") else None
        source_hash = (
            None
            if record.get("sources")
            else profile.get("sha256") or record.get("expected_source_sha256")
        )
        selection = record.get("selection")
        population = {
            "kind": (
                "unbound" if record["source"] is None
                else "selection_context" if selection else "all_observations"
            ),
            "scope": population_scope,
            "selection_sha256": digest(selection) if selection else None,
            "selected_observation_n": len(selection["observation_ids"])
            if selection
            else None,
        }
        if record.get("singlecell_trial"):
            study = record["singlecell_trial"]
            specification = study["population_specification"]
            population = {
                "kind": "supplied_populations",
                "scope": "stage_specific_declared_unit_contrasts",
                "specification_sha256": study["specification_sha256"],
                "groups": _copy(specification["populations"]),
                "biological_unit_column": specification["biological_unit_column"],
                "stage_column": specification["stage_column"],
                "stage_levels": _copy(specification["stage_levels"]),
                "independence_verified": False,
            }
        population["population_sha256"] = digest(
            {"source_sha256": source_hash, "input_set_sha256": set_hash, **population}
        )
        return {
            "source": record["source"],
            "source_sha256": source_hash,
            "input_kind": (
                "none" if record["source"] is None
                else "source_set" if record.get("sources") else "single_source"
            ),
            "input_set_sha256": set_hash,
            "source_binding_status": "verified" if profile else "pending",
            "sources": [
                {"source": item["source"], "sha256": item["sha256"]}
                for item in record.get("source_profiles", [])
            ],
            "project_id": record.get("project_id"),
            "question_id": record.get("question_id", record["id"]),
            "question_sha256": digest(record["question"]),
            "population": population,
            "operation_id": (record.get("description_operation") or {}).get(
                "operation_id"
            ),
            "result_ref": result_ref
            or (record.get("description_operation") or {}).get("result_ref"),
        }

    def _description_lifecycle(self, record, state):
        operation = record["description_operation"]
        operation.update(status=state, updated_at=_now())
        pending = state in {"queued", "running", "cancel_requested"}
        native_pending = bool(
            record.get("job_id")
            and record["status"]
            in {
                "queued",
                "inspecting",
                "planning",
                "preparing",
                "running",
                "cancelling",
            }
        )
        scientific_pending = bool(
            record.get("scientific_operations") and record.get("operation_pending")
        )
        record["operation_pending"] = pending or native_pending or scientific_pending
        purely_descriptive = (record.get("request") or {}).get(
            "category"
        ) == "descriptive_retrieval" and not record["request"].get("cell_type_comparison")
        stages = [
            {
                "step": step,
                "status": (
                    "not_applicable"
                    if purely_descriptive and step in {3, 5}
                    else "running"
                    if step == 1 and pending
                    else "completed"
                    if step == 2 or step == 1 and record.get("profile")
                    else "completed"
                    if step == 4 and record.get("descriptive_answer")
                    else "completed"
                    if step == 3 and operation.get("comparison_steps")
                    else "completed"
                    if step == 5 and record.get("synthesis") and state == "completed"
                    else "awaiting_information"
                    if step == 3 and record["status"] == "awaiting_information"
                    else "not_run"
                ),
            }
            for step in range(1, 6)
        ]
        self._turn(
            record,
            "lifecycle",
            "Dataset inspection " + state.replace("_", " "),
            {
                "state": state,
                "operation_id": operation["operation_id"],
                "binding": self._binding(record),
                "stages": stages,
                "investigation_status": record["status"],
            },
            step=1 if pending else 4,
        )

    def _remember_campaign_grant(self, record, *, explicit_fields=None):
        context = record["scientific_context"]
        fields = set(context) if explicit_fields is None else set(explicit_fields)
        current = self._current_grants.setdefault(record["id"], {})
        for key in ("allow_model_execution", "allow_data_execution"):
            if key in fields:
                current[key] = context.get(key) is True
        if record.get("scientific_session_id"):
            return
        if context.get("auto_synthesis") is True:
            self._campaign_grants[record["id"]] = {
                key: context.get(key)
                for key in (
                    "auto_synthesis",
                    "allow_model_execution",
                    "data_classification",
                )
            }
        else:
            self._campaign_grants.pop(record["id"], None)

    def _capture_planning_source(self, record, session_id, capture_event):
        """Snapshot configured source at a real binding/dispatch, not on reads."""
        history = record.setdefault("planning_source_history", [])
        previous = next(
            (row for row in history if row["session_id"] == session_id), None
        )
        if previous is not None:
            record["planning_source"] = _copy(previous)
            return
        configured = getattr(self.app.scientific, "selector_config", None)
        configured = configured if isinstance(configured, dict) else {}
        keys = {
            "backend",
            "model_execution",
            "requested_model",
            "model",
            "reasoning_effort",
            "version",
        }
        source = {
            "source": "configured_scientific_planner_at_dispatch",
            "capture_event": capture_event,
            "session_id": session_id,
            "captured_at": _now(),
            "backend": configured.get("backend")
            if configured.get("backend") in {"recorded_fixture", "codex-cli"}
            else "unavailable",
            "configuration": {
                key: _copy(value) for key, value in configured.items() if key in keys
            },
            "configuration_sha256": digest(configured),
        }
        history.append(source)
        record["planning_source"] = _copy(source)

    def list(self):
        with self._lock:
            summaries = []
            for identity in self._record_ids():
                record = self._load(identity)
                status = record["status"]
                if record.get("job_id"):
                    status = self.app.status(record["job_id"])["status"]
                summaries.append(
                    {
                        key: _copy(record.get(key))
                        for key in (
                            "id",
                            "thread_id",
                            "parent_investigation",
                            "question",
                            "source",
                            "job_id",
                            "scientific_session_id",
                            "workflow_mode",
                            "updated_at",
                            "revision",
                            "project_id",
                            "request",
                            "description_operation",
                            "operation_pending",
                        )
                    }
                )
                summaries[-1].update(
                    status=status,
                    dataset=(record.get("profile") or {}).get(
                        "filename", Path(record["source"]).name
                    ),
                    cells=(record.get("profile") or {})
                    .get("observed", {})
                    .get("cells"),
                    genes=(record.get("profile") or {})
                    .get("observed", {})
                    .get("genes"),
                )
                if record.get("sources"):
                    summaries[-1].update(
                        sources=_copy(record["sources"]),
                        input_set_sha256=record.get("input_set_sha256"),
                    )
            return {
                "format": "devbench.investigations/1",
                "investigations": sorted(
                    summaries, key=lambda row: row["updated_at"], reverse=True
                ),
            }

    def _declarations(self, supplied, profile, *, partial=False):
        if not isinstance(supplied, dict) or set(supplied) - DECLARATIONS:
            raise ValueError("Use only the declared scientific provenance fields")
        result = _copy(supplied)
        for key, value in result.items():
            if key == "capture_units_confirmed":
                if type(value) is not bool:
                    raise ValueError("Capture confirmation must be an explicit boolean")
            elif not isinstance(value, str) or len(value) > 4000 or "\x00" in value:
                raise ValueError("Use bounded textual provenance declarations")
        capture = result.get("capture_unit_key")
        if capture and capture not in profile["observed"].get("obs_columns", []):
            raise ValueError("Capture-unit choice must be an observed metadata column")
        return result

    def _clarifications(self, record):
        if record.get("sources"):
            return self._source_set_clarifications(record)
        supplied, observed = record["declarations"], record["profile"]["observed"]
        labels = {
            "assay": "Assay",
            "measurement": "Measurement",
            "count_provenance": "Count provenance",
            "capture_unit_key": "Capture-unit column",
            "capture_units_confirmed": "Confirm capture-unit identity",
            "annotation_provenance": "Annotation provenance or known limitation",
        }
        fields = []
        fixed_candidate = observed.get("file_type") in {
            "h5ad",
            ".h5ad",
            "anndata",
        } and supplied.get("assay", "scrna") in {"", "scrna", "snrna"}
        required = ["assay", "measurement", "count_provenance"]
        if fixed_candidate:
            required += [
                "capture_unit_key",
                "capture_units_confirmed",
                "annotation_provenance",
            ]
        for key in required:
            if (
                supplied.get(key) is True
                if key == "capture_units_confirmed"
                else bool(supplied.get(key, "").strip())
            ):
                continue
            choices = {
                "assay": ["scrna", "snrna", "bulk"],
                "measurement": [
                    "umi_counts",
                    "normalized_expression",
                    "non_umi_counts",
                ],
                "capture_unit_key": observed.get("obs_columns", []),
            }.get(key)
            fields.append(
                {
                    "field": key,
                    "label": labels[key],
                    "type": "boolean"
                    if key == "capture_units_confirmed"
                    else "select"
                    if choices is not None
                    else "text",
                    "options": [
                        {"value": item, "label": item} for item in choices or []
                    ],
                    "required": True,
                    "guidance": "Confirm from experimental records; file structure and "
                    "column names do not establish biological meaning.",
                }
            )
        return fields

    def _inspect_source_set(self, record, expected=None):
        from devbench.analysis.rna_input_set_contract import (
            aggregate_profile,
            validate_source_declarations,
        )

        previous = record.get("source_profiles", [])
        profiles = []
        for index, source in enumerate(record["sources"]):
            profile = _copy(self.inputs.inspect(source))
            required_hash = (
                expected[index]
                if expected
                else previous[index]["sha256"]
                if index < len(previous)
                else None
            )
            if required_hash is not None and profile["sha256"] != required_hash:
                raise ValueError(
                    "An input-set source changed; start a new question against its new "
                    "version"
                )
            profiles.append(profile)
            record["source_profiles"] = _copy(profiles)
        if len({profile["sha256"] for profile in profiles}) != len(profiles):
            raise ValueError(
                "Repeated dataset content cannot be counted as another independent "
                "source"
            )
        record["source_declarations"] = [
            validate_source_declarations(value, profile["observed"])
            for value, profile in zip(
                record["source_declarations"], profiles, strict=False
            )
        ]
        record["profile"] = aggregate_profile(profiles)
        record["input_set_sha256"] = record["profile"]["sha256"]

    def _source_set_clarifications(self, record):
        fields = []
        labels = {
            "role": "Source role",
            "assay": "Assay",
            "measurement": "Measurement",
            "count_provenance": "Measurement provenance",
            "capture_unit_key": "Capture-unit column",
            "capture_units_confirmed": "Confirm capture-unit identity",
            "annotation_provenance": "Annotation provenance or known limitation",
            "counts_layer": "Measurement layer",
        }
        for index, (profile, supplied) in enumerate(
            zip(record["source_profiles"], record["source_declarations"], strict=False)
        ):
            observed = profile["observed"]
            role_pending = (
                record.get("analyst_profile") == "analyst_v1"
                and not supplied.get("role")
                and observed.get("file_type") in {"csv", "tsv", "csv.gz", "tsv.gz"}
            )
            required = ["role"]
            if supplied.get("role") == "expression" or role_pending:
                required += ["assay", "measurement", "count_provenance"]
                if observed.get("file_type") == "h5ad":
                    required += ["counts_layer"]
                    if supplied.get("assay") in {"scrna", "snrna"}:
                        required += [
                            "capture_unit_key",
                            "capture_units_confirmed",
                            "annotation_provenance",
                        ]
            choices = {
                "role": ["expression", "metadata", "annotations", "reference", "other"],
                "assay": ["scrna", "snrna", "bulk"],
                "measurement": [
                    "umi_counts",
                    "normalized_expression",
                    "non_umi_counts",
                ],
                "capture_unit_key": observed.get("obs_columns", []),
                "counts_layer": ["X", *observed.get("layers", [])],
            }
            for key in required:
                if (
                    supplied.get(key) is True
                    if key == "capture_units_confirmed"
                    else bool(supplied.get(key, "").strip())
                ):
                    continue
                options = choices.get(key)
                fields.append(
                    {
                        "field": key,
                        "scope": "source_declarations",
                        "source_index": index,
                        "source": record["sources"][index],
                        "label": profile["filename"] + " · " + labels[key],
                        "type": "boolean"
                        if key == "capture_units_confirmed"
                        else "select"
                        if options is not None
                        else "text",
                        "options": [
                            {"value": value, "label": value} for value in options or []
                        ],
                        "required": not role_pending or key == "role",
                        **(
                            {"requires_source_role": "expression"}
                            if role_pending and key != "role" else {}
                        ),
                        "guidance": "This declaration applies only to this source. "
                        "File names do not establish assay, alignment or biological "
                        "independence.",
                    }
                )
        return fields

    def _selection(self, selection, parent_job, source, sha256):
        if selection is None:
            return None
        required = {
            "figure_id",
            "panel_id",
            "observation_ids",
            "source_job",
            "parent_root",
            "display_only",
        }
        if (
            not isinstance(selection, dict)
            or set(selection) != required
            or selection["display_only"] is not True
        ):
            raise ValueError("Use an exact display-selection reference")
        source_job = _identity(selection["source_job"], "source job identity")
        if parent_job != source_job:
            raise ValueError("Selection source must match its explicit parent job")
        ids = selection["observation_ids"]
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= MAX_SELECTION
            or not all(
                isinstance(item, str) and 0 < len(item) <= 200 and "\x00" not in item
                for item in ids
            )
            or len(ids) != len(set(ids))
            or len(json.dumps(selection).encode()) > 50000
        ):
            raise ValueError(
                "Select at most 1000 unique bounded observation identities; no "
                "selection is truncated"
            )
        state = self.app.status(source_job)
        if state.get("source") != source:
            raise ValueError("Selection input differs from the investigation input")
        figures = self.app.figures(source_job)
        if figures["parent_root"] != selection["parent_root"]:
            raise ValueError(
                "Selection parent receipt does not match the verified source"
            )
        figure = next(
            (
                item
                for item in figures["figures"]
                if item["figure_id"] == selection["figure_id"]
            ),
            None,
        )
        if (
            not figure
            or figure.get("analysis_binding", {}).get("input_sha256") != sha256
        ):
            raise ValueError(
                "Selection figure is not bound to this exact input version"
            )
        payload = self._figure_payload(source_job, figure)
        panel = next(
            (
                item
                for item in payload["panels"]
                if item["panel_id"] == selection["panel_id"]
            ),
            None,
        )
        if panel is None:
            raise ValueError("Selection panel is not present in the verified figure")
        population = {
            row["observation_id"]
            for row in panel["rows"]
            if row.get("status") == "rendered"
        }
        if not set(ids) <= population:
            raise ValueError(
                "Selection includes identities absent from the saved plotted population"
            )
        return {
            **_copy(selection),
            "scope": "context_only_not_a_population_analysis_grant",
        }

    def _figure_payload(self, job_id, figure):
        path, _, _ = self.app.download(job_id, figure["plot_data"]["path"])
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 16 * 1024**2:
                raise ValueError("Selection figure payload must be bounded and regular")
            raw = stream.read(16 * 1024**2 + 1)
        if len(raw) > 16 * 1024**2:
            raise ValueError("Selection figure payload exceeds its bound")
        expected = figure["plot_data"].get("sha256")
        if expected is not None and hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("Selection figure data changed after receipt verification")
        return json.loads(raw)

    def _creation_sources(self, body):
        supplied = body.get("sources")
        if "sources" not in body:
            if "source" not in body:
                return self._no_sources(body)
            sources = [str(self.inputs.source_path(body["source"]))]
        else:
            if (
                type(supplied) is not list
                or len(supplied) > 8
                or any(type(value) is not str for value in supplied)
            ):
                raise ValueError("Attach one to eight explicit dataset sources")
            if not supplied:
                return self._no_sources(body)
            sources = [str(self.inputs.source_path(value)) for value in supplied]
            if len(set(sources)) != len(sources):
                raise ValueError("A dataset source cannot be attached twice")
            if "source" in body and (
                len(sources) != 1
                or str(self.inputs.source_path(body["source"])) != sources[0]
            ):
                raise ValueError(
                    "Use source or the ordered sources list with matching identities"
                )
        if len(sources) > 1 and any(
            body.get(name) for name in ("declarations", "expected_sha256", "selection")
        ):
            raise ValueError(
                "Multiple datasets require per-source declarations and hashes. "
                "A saved cell selection can bind only one dataset."
            )
        declarations = body.get("source_declarations", [{} for _ in sources])
        if (
            type(declarations) is not list
            or len(declarations) != len(sources)
            or any(type(value) is not dict for value in declarations)
        ):
            raise ValueError("Supply one declaration record for each ordered source")
        hashes = body.get("expected_sha256s")
        if hashes is not None and (
            type(hashes) is not list
            or len(hashes) != len(sources)
            or any(
                type(value) is not str or _HASH.fullmatch(value) is None
                for value in hashes
            )
        ):
            raise ValueError(
                "Expected source hashes must match the complete ordered source list"
            )
        return sources, declarations, hashes

    @staticmethod
    def _no_sources(body):
        if "source" in body or any(body.get(field) for field in (
            "declarations", "source_declarations", "expected_sha256",
            "expected_sha256s", "selection", "parent_job", "analyst_profile",
        )):
            raise ValueError("Attach a dataset before supplying data bindings")
        return [], [], None

    def _creation_record(
        self, body, question, project_id, context_inheritance, sources, hashes
    ):
        if body.get("analyst_profile") not in {None, "analyst_v1"}:
            raise ValueError("Use the registered analyst_v1 investigation profile")
        if body.get("selection") is not None and not isinstance(
            body["selection"], dict
        ):
            raise ValueError("Use an exact structured figure selection")
        mode = body.get("proposal_mode", "operator")
        if mode not in {"operator", "agent"}:
            raise ValueError("Use operator or agent proposal mode")
        requirements = question_requirements(
            question,
            body.get("user_parameter_requirements"),
            self.app.parameters,
        )
        parent_job = body.get("parent_job") or (body.get("selection") or {}).get(
            "source_job"
        )
        if parent_job is not None:
            _identity(parent_job, "parent job identity")
            if self.app.status(parent_job).get("source") not in sources:
                raise ValueError("Parent job must use this investigation input")

        identity = uuid.uuid4().hex
        expected = hashes[0] if hashes and len(sources) == 1 else None
        record = {
            "format": FORMAT,
            "id": identity,
            "thread_id": identity,
            "project_id": project_id,
            "question_id": uuid.uuid4().hex,
            "request": classify_request(question),
            "expected_source_sha256": body.get("expected_sha256", expected),
            "revision": 0,
            "created_at": _now(),
            "updated_at": _now(),
            "status": "inspecting",
            "question": question,
            "source": sources[0] if sources else None,
            "proposal_mode": mode,
            "job_id": None,
            "job_state": None,
            "parent_job": parent_job,
            "parent_investigation": None,
            "children": [],
            "selection": None,
            "profile": None,
            "declarations": {},
            "clarification_fields": [],
            "context_inheritance": _copy(context_inheritance)
            if context_inheritance
            else None,
            "intent": classify_question(question),
            "user_parameter_requirements": requirements,
            "requirements_source": "explicit_request_records_only",
            "scientific_context": _context(body.get("scientific_context") or {}),
            "scientific_operations": [],
            "scientific_session_id": None,
            "turns": [],
            "artifacts": [],
            "synthesis": None,
        }
        if body.get("analyst_profile"):
            record["analyst_profile"] = body["analyst_profile"]
        return record

    def create(
        self,
        body,
        *,
        context_inheritance=None,
        defer_description=False,
        project_id=None,
    ):
        allowed = {
            "question",
            "source",
            "sources",
            "expected_sha256",
            "expected_sha256s",
            "declarations",
            "source_declarations",
            "proposal_mode",
            "parent_job",
            "parent_investigation",
            "selection",
            "user_parameter_requirements",
            "scientific_context",
            "analyst_profile",
        }
        if (
            not isinstance(body, dict)
            or set(body) - allowed
            or "question" not in body
        ):
            raise ValueError("Investigation creation requires a question")
        question, pins = split_pinned_context(
            _text(body["question"], "biological question")
        )
        project_id = self._project_id(project_id)
        sources, source_declarations, expected_hashes = self._creation_sources(body)
        record = self._creation_record(
            body,
            question,
            project_id,
            context_inheritance,
            sources,
            expected_hashes,
        )
        if pins:
            record["pinned_context"] = pins
        if len(sources) > 1:
            record.update(
                sources=sources,
                source_profiles=[],
                source_declarations=_copy(source_declarations),
                input_set_sha256=None,
                expected_source_sha256s=_copy(expected_hashes),
            )
            if context_inheritance and record["scientific_context"].get("dataset_ref"):
                record["managed_metadata"] = True
        with self._lock:
            return self._create_locked(record, body, expected_hashes, defer_description)

    def _create_locked(self, record, body, expected_hashes, defer_description):
        if len(self._record_ids()) >= MAX_INVESTIGATIONS:
            raise ValueError(
                "Investigation limit reached; preserve history and "
                "use a fresh workspace"
            )
        parent = (
            self._load(body["parent_investigation"])
            if body.get("parent_investigation")
            else None
        )
        if parent:
            record.update(
                thread_id=parent["thread_id"], parent_investigation=parent["id"]
            )
        self._turn(
            record,
            "user_question",
            "Your question",
            {
                "question": record["question"],
                "parent_job": record["parent_job"],
                **({"pinned_context": record["pinned_context"]}
                   if record.get("pinned_context") else {}),
            },
            step=2,
            role="user",
        )
        if record["context_inheritance"]:
            self._turn(
                record,
                "context_inheritance",
                "Data context retained",
                record["context_inheritance"],
                step=1,
            )
        if record["source"] is not None:
            self._remember_campaign_grant(record)
        self._save(record)
        describing = bool(record["source"] and record["request"]["descriptive_topics"])
        if describing:
            self._begin_description_attempt(record, deferred=defer_description)
            self._save(record)
        if describing and defer_description:
            self._submit_description(record, body, expected_hashes)
        else:
            self._initialize_and_save(record, body, expected_hashes)
        if parent:
            parent["children"].append(record["id"])
            self._save(parent)
        return self._response(record)

    def _initialize_and_save(self, record, body, expected_hashes):
        try:
            self._initialize(record, body, expected_hashes)
        except (ValueError, PermissionError, RuntimeError, OSError, KeyError) as error:
            self._failure(record, error)
        if record.get("description_operation"):
            self._description_lifecycle(
                record,
                "failed" if record["status"] == "operation_failed" else "completed",
            )
        self._save(record)

    def _submit_description(self, record, body, expected_hashes):
        executor = getattr(self.app, "_executor", None)
        if executor is None:
            raise RuntimeError(
                "Deferred descriptions require the native Workbench executor"
            )
        identity = record["id"]
        control = {"stop": threading.Event(), "future": None}
        self._description_controls[identity] = control
        try:
            control["future"] = executor.submit(
                self._describe_async, identity, _copy(body), expected_hashes
            )
        except BaseException:
            self._description_controls.pop(identity, None)
            raise

    def _initialize(self, record, body, expected_hashes, *, description_only=False):
        if record["source"] is None:
            self._prepare_without_data(record)
            return
        inspected = _copy(record)
        if inspected.get("sources"):
            try:
                self._inspect_source_set(inspected, expected_hashes)
            except (ValueError, OSError, RuntimeError, PermissionError):
                if record.get("profile") is None:
                    # Preserve retry fingerprints without publishing unadmitted headers.
                    record["source_profiles"] = [
                        {key: item[key] for key in ("source", "sha256", "size_bytes")}
                        for item in inspected["source_profiles"]
                    ]
                raise
            profile = inspected["profile"]
        else:
            profile = _copy(self.inputs.inspect(record["source"]))
            expected = body.get(
                "expected_sha256", expected_hashes[0] if expected_hashes else None
            )
            if expected is not None and (
                not isinstance(expected, str)
                or _HASH.fullmatch(expected) is None
                or expected != profile["sha256"]
            ):
                raise ValueError(
                    "Observed input checksum does not match the supplied expected "
                    "identity"
                )
            inspected["profile"] = profile
            source_declarations = body.get("source_declarations", [{}])
            inspected["declarations"] = self._declarations(
                body.get("declarations", source_declarations[0]), profile
            )
            inspected["selection"] = self._selection(
                body.get("selection"),
                record.get("parent_job"),
                record["source"],
                profile["sha256"],
            )
        self._seed_adaptive_intake(inspected)
        record.update(inspected)
        record["turns"][0]["content"]["selection"] = _copy(record["selection"])
        self._turn(
            record,
            "data_audit",
            "Dataset inspection",
            {
                "observed": profile["observed"],
                "declarations": record["declarations"],
                "inferred": profile.get("inferred", {}),
                "unknowns": profile.get("unknowns", []),
                "input": {
                    key: profile[key] for key in ("source", "sha256", "size_bytes")
                },
            },
            step=1,
        )
        if record.get("sources"):
            record["turns"][-1]["content"].update(
                source_profiles=_copy(record["source_profiles"]),
                source_declarations=_copy(record["source_declarations"]),
                input_set_sha256=record["input_set_sha256"],
            )
        self._turn(
            record,
            "question_scope",
            "Question and scope",
            {
                "question": record["question"],
                "intent": record["intent"],
                "request": record["request"],
                "user_parameter_requirements": record["user_parameter_requirements"],
                "parameter_origin": "Explicit requirements are separate from the "
                "frozen service profile.",
            },
            step=2,
        )
        self._prepare(record, description_only=description_only)

    def _describe_async(self, identity, body, expected_hashes):
        from devharness_core.watchdog import WorkerCancelled, cancel_workers_on

        with self._lock:
            control = self._description_controls[identity]
            record = self._load(identity)
            if control["stop"].is_set():
                return
            record["status"] = "inspecting"
            self._description_lifecycle(record, "running")
            self._save(record)
        scope = {"workers": []}
        try:
            with cancel_workers_on(control["stop"]) as scope:
                self._initialize(record, body, expected_hashes, description_only=True)
                if control["stop"].is_set():
                    raise WorkerCancelled("Dataset description was cancelled")
        except BaseException as error:
            with self._lock:
                # Keep the persisted cancellation request and its ordered events.
                current = self._load(identity)
                if control["stop"].is_set():
                    record = current
                workers = scope["workers"]
                cleanup = all(
                    worker.get("cleanup", {}).get("verified_stopped") is True
                    for worker in workers
                )
                if isinstance(error, WorkerCancelled) and cleanup:
                    record.update(status="cancelled", error=None)
                    self._description_lifecycle(record, "cancelled")
                else:
                    self._failure(
                        record,
                        error
                        if cleanup
                        else RuntimeError("Description worker cleanup is unverified"),
                    )
                    self._description_lifecycle(record, "failed")
                record["description_operation"]["worker_cleanup_verified"] = cleanup
                self._save(record)
        else:
            with self._lock:
                if control["stop"].is_set():
                    record = self._load(identity)
                    record["status"] = "cancelled"
                    self._description_lifecycle(record, "cancelled")
                else:
                    # Continue the existing scientific intake only after the
                    # descriptive worker has stopped, while cancellation and
                    # other mutations remain serialized by the original lock.
                    record["operation_pending"] = False
                    if record["request"]["remaining_action"] is not None:
                        try:
                            self._prepare(record)
                        except (
                            ValueError,
                            PermissionError,
                            RuntimeError,
                            OSError,
                            KeyError,
                        ) as error:
                            self._failure(record, error)
                    self._description_lifecycle(record, "completed")
                record["description_operation"]["worker_cleanup_verified"] = all(
                    worker.get("cleanup", {}).get("verified_stopped") is True
                    for worker in scope["workers"]
                )
                self._save(record)
        finally:
            with self._lock:
                self._description_controls.pop(identity, None)

    def _begin_description_attempt(self, record, *, deferred):
        record.pop("descriptive_answer", None)
        record["synthesis"] = None
        record["description_operation"] = {
            "operation_id": uuid.uuid4().hex,
            "created_at": _now(),
            "kind": "bounded_descriptive_retrieval",
            "result_ref": None,
        }
        record["status"] = "queued" if deferred else "inspecting"
        self._description_lifecycle(record, "queued" if deferred else "running")

    def _defer_descriptive_clarification(self, record, body):
        """Retain the new request, then revalidate/read outside the service lock."""
        question = _text(
            body.get("question", record["question"]),
            "revised biological question",
        )
        self._revise_question_and_context(record, body, question, reclassify=True)
        init_body, expected_hashes = self._deferred_clarification_sources(record, body)
        self._turn(
            record,
            "clarification_answer",
            "Requested context revision",
            {
                "question": question,
                "declarations": body.get("declarations", {}),
                "source_declarations": body.get("source_declarations"),
                "user_parameter_requirements": record["user_parameter_requirements"],
            },
            step=2,
            role="user",
        )
        self._begin_description_attempt(record, deferred=True)
        self._save(record)
        self._submit_description(record, init_body, expected_hashes)
        return self._response(record)

    def _deferred_clarification_sources(self, record, body):
        init_body = {"question": record["question"]}
        if record.get("sources"):
            if body.get("declarations"):
                raise ValueError("Clarify each attached source independently")
            answers = self._source_declaration_answers(record, body)
            record["source_declarations"] = [
                {**old, **new}
                for old, new in zip(
                    record["source_declarations"],
                    answers,
                    strict=True,
                )
            ]
            known = record.get("source_profiles", [])
            expected = record.get("expected_source_sha256s")
            if expected is None and len(known) == len(record["sources"]):
                expected = [item["sha256"] for item in known]
            return init_body, expected

        declarations = body.get("declarations", {})
        if "source_declarations" in body or not isinstance(declarations, dict):
            raise ValueError("Use a single declaration object for this dataset")
        init_body["expected_sha256"] = (record.get("profile") or {}).get(
            "sha256"
        ) or record.get("expected_source_sha256")
        init_body["declarations"] = {**record["declarations"], **declarations}
        if record.get("selection"):
            init_body["selection"] = {
                key: value
                for key, value in record["selection"].items()
                if key != "scope"
            }
        return init_body, None

    def _failure(self, record, error):
        record["status"] = "operation_failed"
        if record.get("profile") is None and record.get("context_inheritance"):
            for key in record["context_inheritance"].get("inherited_fields", []):
                record["scientific_context"].pop(key, None)
            record["context_inheritance"]["validated"] = False
            self._campaign_grants.pop(record["id"], None)
        record["error"] = {"type": type(error).__name__, "message": str(error)[:2000]}
        self._turn(
            record,
            "error",
            "Action needs attention",
            record["error"],
            step=1 if record["profile"] is None else 3,
        )

    def _handle_dataset_description(self, record):
        from devbench.analysis.workbench_descriptions import make_description

        request = record["request"]
        profiles = record.get("source_profiles") or [record["profile"]]
        answers = []
        for profile in profiles:
            if hasattr(self.inputs, "describe"):
                content = self.inputs.describe(
                    profile["source"],
                    expected_sha256=profile["sha256"],
                    topics=request["descriptive_topics"],
                )
            else:
                # Header-only adapters can report verified header facts, never
                # silently open a second unguarded input or invent annotations.
                refreshed = self.inputs.inspect(profile["source"])
                if refreshed["sha256"] != profile["sha256"]:
                    raise ValueError("Input changed before descriptive answer")
                content = make_description(
                    profile["observed"],
                    request["descriptive_topics"],
                    filename=profile["filename"],
                    limitations=["This input adapter exposes bounded headers only."],
                )
            answers.append(content)
        content = answers[0]
        if len(answers) > 1:
            content = {
                **content,
                "message": "Descriptive evidence for the attached source set; rows "
                "from different sources are not summed as biological units.",
                "filename": "Attached source set",
                "sha256": None,
                "observed": {"file_type": "source_set", "cells": None, "genes": None},
                "input_set_sha256": record["input_set_sha256"],
                "tables": [
                    {
                        **table,
                        "table_title": item["filename"] + " · " + table["table_title"],
                        "source": profiles[index]["source"],
                        "source_sha256": profiles[index]["sha256"],
                    }
                    for index, item in enumerate(answers)
                    for table in item["tables"]
                ],
                "facts": [
                    {**fact, "source_sha256": profiles[index]["sha256"]}
                    for index, item in enumerate(answers)
                    for fact in item["facts"]
                ],
                "limitations": list(
                    dict.fromkeys(
                        text for item in answers for text in item["limitations"]
                    )
                ),
                "completion": "completed"
                if all(item["completion"] == "completed" for item in answers)
                else "partial"
                if any(item["completion"] != "unavailable" for item in answers)
                else "unavailable",
            }
        if request.get("motor_program_comparison"):
            from devbench.analysis.workbench_motor_programs import compare_motor_programs

            steps, tables, message, completion = compare_motor_programs(profiles, answers)
            content.update(message=message, completion=completion, tables=[*tables, *content["tables"]])
            content["limitations"].extend([
                "Existing motor annotations anchor the screen. A strong motor program outside those annotations is recorded separately.",
                "Branchial and visceral motor neurons share the core marker program. Named nuclei and hindbrain origin require independent evidence.",
                "Observed candidate proportions depend on expression detection, sampling, depth and annotation. Developmental effects and statistical significance remain untested.",
            ])
            record["description_operation"]["comparison_steps"] = steps
        elif comparison := request.get("cell_type_comparison"):
            from devbench.analysis.workbench_cell_type_comparison import compare_cell_types

            steps, tables, message, completion = compare_cell_types(
                profiles, answers, comparison["declared_days"],
            )
            content.update(
                message=message, completion=completion,
                tables=[*tables, *content["tables"]],
            )
            if "stored_count_layer_audit" in request["descriptive_topics"]:
                qc = [
                    next((table for table in answer["tables"]
                          if table.get("source_ref") == "layers/counts"), None)
                    for answer in answers
                ]
                completed = sum(table is not None for table in qc)
                steps.insert(1, {
                    "title": "Process and validate stored count matrices",
                    "status": "completed" if completed == len(profiles) else "partial",
                    "detail": (
                        f"Read and validated the stored counts in {completed} of "
                        f"{len(profiles)} files and recalculated depth and detected features."
                    ),
                })
                if completed < len(profiles):
                    content["completion"] = "partial"
                content["limitations"].append(
                    "Count diagnostics verify the numeric representation of the stored "
                    "layer. Count provenance and biological sample identity remain unverified."
                )
            content["limitations"].append(
                "These are shifts in proportions of stored labels. Cell annotations "
                "and biological replication were not independently validated; no "
                "developmental effect or statistical significance was estimated."
            )
            record["description_operation"]["comparison_steps"] = steps
        if record.get("selection"):
            content["limitations"].append(
                "These are full-source metadata summaries. Selected rows were not "
                "recomputed or treated as a separate population."
            )
        if request["remaining_action"] and content["completion"] == "completed":
            content["completion"] = "partial"
        result_ref = digest({"binding": self._binding(record), "answer": content})
        content["binding"] = self._binding(record, result_ref=result_ref)
        record["description_operation"]["result_ref"] = result_ref
        self._turn(
            record,
            "methods",
            "Motor-neuron marker scoring and comparison"
            if request.get("motor_program_comparison") else "Descriptive composition comparison"
            if request.get("cell_type_comparison") else "Descriptive metadata inspection",
            {
                "method": "trusted_supervised_read_only_description",
                "scope": (
                    "Bounded reads of the named stored counts layer and observation "
                    "labels. No filtering or model session."
                    if {"stored_count_layer_audit", MOTOR_PROGRAM_TOPIC} & set(request["descriptive_topics"])
                    else "Bounded stored dimensions and selected annotation values; "
                    "no matrix computation or model session."
                ),
            },
            step=3,
        )
        self._turn(record, "dataset_answer", "Dataset answer", content, step=4)
        record["descriptive_answer"] = {
            "result_ref": result_ref,
            "question_sha256": digest(record["question"]),
            "source_sha256": record["profile"]["sha256"],
            "completion": content["completion"],
        }
        if request["remaining_action"] is not None:
            return
        if content["completion"] != "completed":
            record.update(
                status="awaiting_information",
                required_action="descriptive_evidence_unavailable",
                clarification_fields=[],
            )
            return
        self._turn(
            record,
            "conclusion",
            "Conclusion",
            {
                "message": content["message"],
                "uncertainties": content["limitations"],
                "binding": content["binding"],
            },
            step=5,
        )
        record.update(
            status="completed",
            clarification_fields=[],
            synthesis={
                "supported_findings": {
                    "dataset": content["filename"],
                    "observations": content["observed"].get("cells"),
                    "features": content["observed"].get("genes"),
                    "summary": content["message"],
                },
                "uncertainties": content["limitations"],
            },
        )

    def _require_adaptive_intake(self, record, ledger):
        lineage = record.get("adaptive_lineage")
        if lineage is None:
            return
        if ledger is None or lineage.get("lineage_id") != record["id"]:
            raise OperationConflict(
                "Workbench investigation ledger delivery is unavailable"
            )
        from devbench.analysis.evidence_access import EvidenceBroker

        broker = EvidenceBroker.open_existing(ledger.scientific)
        broker.check_delivery(
            lineage["context_id"],
            lineage["source_id"],
            lineage_id=record["id"],
            role="scientist",
        )
        audit = record.get("descriptive_matrix_audit")
        if audit is not None:
            from .workbench_matrix_audit import verify_matrix_audit_disclosure

            verify_matrix_audit_disclosure(broker, lineage, audit)

    def _seed_adaptive_intake(self, record):
        """Retain this input scope and its structural delivery in the host ledger."""
        ledger = self._open_ledger(write=True)
        if ledger is None:
            return

        from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker

        from .adaptive_workflow import ADAPTIVE_MODE, AdaptiveWorkflow

        identity = record["id"]
        context = record["scientific_context"]
        if not context.get("counts_layer"):
            for requirement in record["user_parameter_requirements"]:
                if requirement["name"] == "counts_layer" and isinstance(
                    requirement["value"], str
                ):
                    context["counts_layer"] = requirement["value"]
                    break
        intake = {
            "format": "devbench.workbench-intake/1",
            "investigation_id": identity,
            "source_sha256": record["profile"]["sha256"],
            "question_sha256": digest(record["question"]),
            "design_sha256": digest(
                {
                    "declarations": record["declarations"],
                    "source_declarations": record.get("source_declarations"),
                    "explicit_requirements": record["user_parameter_requirements"],
                    "counts_layer": context.get("counts_layer"),
                    "biological_independence": context.get("biological_independence"),
                    "selection_scope": context.get("selection_scope"),
                    "selection": record.get("selection"),
                    "batch_col": context.get("batch_col"),
                    "condition_col": context.get("condition_col"),
                    "bulk_episode_declarations": context.get(
                        "bulk_episode_declarations"
                    ),
                }
            ),
        }
        intake_sha256 = digest(intake)
        if (record.get("adaptive_lineage") or {}).get("intake_sha256") == intake_sha256:
            self._require_adaptive_intake(record, ledger)
            return
        roots = tuple(
            dict.fromkeys(
                [
                    intake["source_sha256"],
                    *(row["sha256"] for row in record.get("source_profiles", [])),
                ]
            )
        )
        policy = AccessPolicy(
            "workbench-intake-" + digest(roots)[:32],
            1,
            allowed_roots=roots,
        )
        authority = ledger.scientific
        broker = EvidenceBroker(authority)
        policy_sha256 = broker.register_policy(policy)
        exposure_basis = digest(
            {"intake_sha256": intake_sha256, "policy_sha256": policy_sha256}
        )
        binding_id = f"workbench-{record['revision']}-{intake_sha256[:32]}"
        authority.commit(
            identity,
            expected_revision=ledger._read(
                lambda db: authority._revision(db, identity)
            ),
            record={
                "kind": "binding",
                "record_id": binding_id,
                "cohort_sha256": intake["source_sha256"],
                "design_sha256": intake["design_sha256"],
                "procedure_sha256": digest({"scope": "structural_intake"}),
                "family_allocation_sha256": digest({"families": []}),
                "policy_sha256": policy_sha256,
                "exposure_basis_sha256": exposure_basis,
            },
        )
        flow = AdaptiveWorkflow(ledger, identity)
        flow.record_basis("workbench-intake", "source", intake_sha256)
        flow.record_basis("workbench-exposure", "exposure_basis", exposure_basis)

        context_id = f"workbench-{identity}-{policy_sha256[:16]}"
        source_id = f"workbench-intake-{intake_sha256}"
        broker.register_context(
            context_id,
            principal_id="investigation-service",
            lineage_id=identity,
            role="scientist",
            phase="readback",
            policy_sha256=policy_sha256,
        )
        broker.register_source(
            source_id,
            content_sha256=intake_sha256,
            roots=roots,
            classification="structural",
        )
        delivery = broker.delivery(context_id, source_id)
        reservation = delivery.broker.reserve(
            delivery.context_id, delivery.source_id, intake
        )
        broker.mark_attempted(reservation["reservation_id"])
        if record.pop("descriptive_matrix_audit", None) is not None:
            record["synthesis"] = None
        record["adaptive_lineage"] = {
            "mode": ADAPTIVE_MODE,
            "lineage_id": identity,
            "binding_id": binding_id,
            "context_id": context_id,
            "source_id": source_id,
            "intake_sha256": intake_sha256,
            "intake": intake,
            "exposure_basis_sha256": exposure_basis,
            "disclosure_reservation_id": reservation["reservation_id"],
        }

    def _matrix_audit(self, record, intents):
        from devbench.analysis.workbench_episode_intake import (
            MatrixIntakeNeedsInformation,
        )
        from devharness_core.watchdog import worker_limits

        from .workbench_matrix_audit import (
            MatrixAuditNeedsMeasurement,
            matrix_audit_incomplete,
            matrix_audit_summary,
            run_matrix_audit,
        )

        prior = record.get("descriptive_matrix_audit")
        if prior is not None:
            self._apply_matrix_status(record, prior)
            return
        profiles = record.get("source_profiles") or [record["profile"]]

        def verify_sources():
            for profile in profiles:
                if (
                    self.inputs.inspect(profile["source"])["sha256"]
                    != profile["sha256"]
                ):
                    raise ValueError("Matrix audit input changed after its intake")

        try:
            with worker_limits(
                deadline=time.monotonic() + self.app.run_timeout_seconds,
                max_mem_mb=self.app.parameters["memory_mb"],
            ):
                audit = run_matrix_audit(
                    record,
                    intents=intents,
                    ledger=self._open_ledger(write=True),
                    workspace=self.app.workspace,
                    verify_sources=verify_sources,
                    maximum_bytes=MAX_RECORD_BYTES // 3,
                )
        except MatrixIntakeNeedsInformation as error:
            record.update(
                status="awaiting_information",
                required_action="matrix_intake",
                clarification_fields=error.fields,
            )
            content = {"reason": str(error), "fields": error.fields}
            if isinstance(error, MatrixAuditNeedsMeasurement):
                content.update(
                    fact_provenance=error.fact_provenance, warnings=error.warnings
                )
            self._turn(
                record, "clarification", "Confirm matrix input scope", content, step=2
            )
            return

        record["descriptive_matrix_audit"] = audit
        result_ref = digest(audit)
        self._turn(
            record,
            "methods",
            "Descriptive matrix audit",
            {
                "tool_receipts": audit["tool_receipts"],
                "exposure_basis_sha256": audit["exposure_basis_sha256"],
            },
            step=3,
        )
        self._turn(
            record,
            "dataset_answer",
            "Descriptive matrix diagnostics",
            {
                "message": matrix_audit_summary(audit),
                "completion": "partial"
                if matrix_audit_incomplete(audit)
                else "completed",
                "binding": self._binding(
                    record, result_ref=result_ref, population_scope="full_source_matrix"
                ),
                "matrix_audit_id": audit["audit_id"],
            },
            step=4,
        )
        self._apply_matrix_status(record, audit)

    @staticmethod
    def _apply_matrix_status(record, audit):
        from .workbench_matrix_audit import matrix_audit_incomplete

        incomplete = matrix_audit_incomplete(audit)
        record["status"] = "awaiting_information" if incomplete else "completed"
        if incomplete:
            record["required_action"] = "review_matrix_quality"

    def _response(self, record):
        view = super()._response(record)
        audit = record.get("descriptive_matrix_audit")
        if audit is None:
            return view
        from .workbench_matrix_audit import matrix_audit_projection

        self._require_adaptive_intake(record, self._open_ledger())
        projection = matrix_audit_projection(audit)
        for turn in [*view["turns"], *view["thread_turns"]]:
            if turn["content"].get("matrix_audit_id") == audit["audit_id"]:
                turn["content"].update(projection)
        return _copy(view)

    def _attach_current_adaptive_plan(self, record):
        """Join a Workbench investigation to an already admitted controller scope."""
        context = record["scientific_context"]
        session_id = context.get("session_id")
        binding = getattr(self.app, "_adaptive_bindings", {}).get(session_id)
        if binding is None or binding.controller is None:
            return False
        plan, ledger = binding.require_owner(self.app.scientific)
        local = self._open_ledger()
        if local is None or ledger.path != local.path:
            raise OperationConflict(
                "Adaptive plan requires the same investigation ledger"
            )
        anchors = plan["scientific_anchors"]
        datasets = anchors["dataset_versions"]
        if len(datasets) != 1:
            raise ValueError("Adaptive plan must bind an exact dataset version")
        self._bound_plan_request(
            record,
            {
                "action": "create_plan",
                "dataset_ref": datasets[0],
                "analysis_ref": anchors["analysis_version"],
            },
        )
        if plan.get("user_parameter_requirements", []) != record[
            "user_parameter_requirements"
        ] or plan.get("data_classification") != context.get(
            "data_classification", "unclassified"
        ):
            raise PermissionError(
                "Adaptive plan does not match the investigation scope"
            )
        record["scientific_session_id"] = session_id
        record["adaptive_lineage"]["planner_session_id"] = session_id
        record["adaptive_lineage"]["execution_mode"] = "adaptive/1"
        record["status"] = "awaiting_information"
        record["required_action"] = "adaptive_step"
        return True

    def _attach_follow_up_workflow(self, record):
        """Link a new question to the existing experiment without reauthorizing it."""
        from .workbench_interactive import ensure_adaptive_binding

        inherited = record.get("context_inheritance") or {}
        session_id = inherited.get("retained_workflow_session_id")
        if (
            not session_id or not self._uses_interactive_runtime(record)
            or inherited.get("validated") is False
            or inherited.get("overridden_fields")
            or set(inherited.get("explicit_request_fields", ())) - {"selection_scope"}
            or record.get("selection")
        ):
            return False
        binding = ensure_adaptive_binding(self.app, session_id)
        if binding is None:
            return False
        parent = self._load(inherited["parent_investigation"])
        if (
            parent.get("scientific_session_id") != session_id
            or parent["profile"]["sha256"] != record["profile"]["sha256"]
            or [item["sha256"] for item in parent.get("source_profiles", ())]
            != [item["sha256"] for item in record.get("source_profiles", ())]
            or parent.get("source_declarations") != record.get("source_declarations")
        ):
            return False
        record["scientific_session_id"] = session_id
        record["scientific_context"]["session_id"] = session_id
        record["adaptive_lineage"].update(
            planner_session_id=session_id, execution_mode="adaptive/1",
        )
        record.update(status="adaptive_workflow", workflow_mode="adaptive/1")
        self._turn(
            record, "scientific_action", "Linked to the current research workflow",
            {
                "session_id": session_id,
                "scope": "New question; prior evidence and reviews keep their scope.",
            },
            step=2,
        )
        return True

    def _prepare_without_data(self, record):
        request = record["request"]["remaining_action"]
        guidance = (
            "Attach a dataset and its sample or donor metadata. Confirm the "
            "independent biological units, conditions, and comparison before fitting."
            if request == "inferential_analysis"
            else "Attach a dataset to check its measurements and biological units. "
            "Include experimental metadata if the question depends on groups or QC."
        )
        record.update(
            status="awaiting_information", required_action="attach_dataset"
        )
        self._turn(
            record, "clarification", "Data needed to investigate this question",
            {"guidance": guidance}, step=2,
        )

    def _prepare(self, record, *, description_only=False):
        record.pop("required_action", None)
        record.pop("diagnostic_code", None)
        record.pop("error", None)
        record["clarification_fields"] = []
        request = record.setdefault("request", classify_request(record["question"]))
        self._seed_adaptive_intake(record)
        if self._prepare_description(
            record, request, description_only=description_only
        ):
            return
        if record.get("sources") and self._uses_interactive_runtime(record):
            from devbench.analysis.rna_input_set_contract import MAX_SOURCE_BYTES

            if any(
                profile["size_bytes"] > MAX_SOURCE_BYTES
                for profile in record["source_profiles"]
            ):
                self._ask_to_clarify_request(
                    record, request,
                    input_limit_mb=MAX_SOURCE_BYTES // 1_000_000,
                )
                return
        self._bind_singlecell_scope(record)
        if (
            self._resolve_selection_scope(record)
            or self._attach_follow_up_workflow(record)
        ):
            return
        fields = self._clarifications(record)
        if self._uses_interactive_runtime(record):
            if not fields and record.get("sources"):
                from .workbench_interactive import bulk_intake_fields

                fields.extend(bulk_intake_fields(record))
            fields.extend(self._interactive_intake_fields(record))
        if fields:
            record.update(status="awaiting_information", clarification_fields=fields)
            self._turn(
                record,
                "clarification",
                "Confirm the dataset context",
                {"fields": fields},
                step=1,
            )
            return
        if self._attach_current_adaptive_plan(record):
            return
        if record.get("analyst_profile") == "analyst_v1":
            self._prepare_analyst_episode(record)
        elif self._uses_fixed_profile(record) and not self._uses_interactive_runtime(
            record
        ):
            self._prepare_fixed_profile(record)
        else:
            self._prepare_scientific_plan(record)

    def _prepare_description(self, record, request, *, description_only):
        intents = matrix_audit_intents(record["question"])
        action = request["remaining_action"]
        if MOTOR_PROGRAM_TOPIC in request["descriptive_topics"]:
            if self.app.allow_data_execution is not True:
                self._record_diagnostic(record, "data_execution_disabled")
                return True
            if record["scientific_context"].get("allow_data_execution") is not True:
                field = {
                    "field": "allow_data_execution", "scope": "scientific_context",
                    "label": "Run the local motor-neuron marker screen",
                    "type": "boolean", "required": True,
                    "guidance": "Read the full stored counts from each attached file. "
                    "The output contains provisional labels and preserves unresolved cells.",
                }
                record.update(status="awaiting_information", clarification_fields=[field])
                self._turn(
                    record, "clarification", "Approve the local marker screen",
                    {"fields": [field], "guidance": field["guidance"]}, step=3,
                )
                return True
        if "stored_count_layer_audit" in request["descriptive_topics"] and (
            self.app.allow_data_execution is not True
            or record["scientific_context"].get("allow_data_execution") is not True
        ):
            self._record_diagnostic(record, "data_execution_disabled")
            return True
        if (
            intents
            and action == "exploratory_computation"
            and not request.get("scope_requires_clarification")
            and not record.get("selection")
        ):
            diagnostic = self._matrix_audit_diagnostic(record)
            if diagnostic is None:
                self._matrix_audit(record, intents)
            else:
                self._record_diagnostic(record, diagnostic)
            return True
        if request["descriptive_topics"]:
            self._retrieve_description(record)
            if action is None or description_only:
                return True
        if action == "clarify_intent" or (
            action == "exploratory_computation"
            and request.get("matrix_computation_required")
            and not intents
        ):
            self._ask_to_clarify_request(record, request)
            return True
        return False

    def _matrix_audit_diagnostic(self, record):
        if getattr(self.app, "scientific", None) is None:
            return "scientific_workspace_not_enabled"
        context = record["scientific_context"]
        if (
            self.app.allow_data_execution is not True
            or context.get("allow_data_execution", True) is not True
        ):
            return "data_execution_disabled"
        if not record.get("adaptive_lineage"):
            return "adaptive_runtime_not_bound"
        return None

    def _record_diagnostic(self, record, code):
        record.update(
            status="awaiting_information", required_action=code,
            diagnostic_code=code,
        )
        self._turn(
            record, "clarification", "Workbench configuration needed",
            {"code": code}, step=2,
        )

    def _retrieve_description(self, record):
        previous = record.get("descriptive_answer") or {}
        if (
            previous.get("question_sha256") == digest(record["question"])
            and previous.get("source_sha256") == record["profile"]["sha256"]
        ):
            return
        if not record.get("description_operation"):
            record["description_operation"] = {
                "operation_id": uuid.uuid4().hex,
                "created_at": _now(),
                "kind": "bounded_descriptive_retrieval",
                "result_ref": None,
            }
        self._handle_dataset_description(record)

    def _ask_to_clarify_request(self, record, request, *, input_limit_mb=None):
        matrix_requested = (
            request["remaining_action"] == "exploratory_computation"
            and request.get("matrix_computation_required")
        )
        if input_limit_mb is not None:
            guidance = (
                f"Joint planning accepts up to {input_limit_mb} MB per source. "
                "Choose a local summary of the individual files or use a reviewed "
                "input set that fits the joint planning limit."
            )
        elif matrix_requested:
            guidance = (
                "For a bounded matrix diagnostic, ask about nonzero genes per "
                "profile, batch effects or PCA on the full source."
            )
        else:
            guidance = (
                "Choose what to compare and which cells or samples to include. "
                "For a first look, ask about the stored cell-type labels or "
                "dataset structure."
            )
        field = {
            "field": "question",
            "scope": "request",
            "label": "Question to investigate",
            "type": "text",
            "required": True,
            "options": [],
            "guidance": guidance,
        }
        record.update(
            status="awaiting_information",
            required_action=(
                "input_set_size_limit" if input_limit_mb is not None
                else "bounded_matrix_computation" if matrix_requested
                else "clarify_intent"
            ),
            clarification_fields=[field],
        )
        self._turn(
            record,
            "clarification",
            "Clarify the next action",
            {"fields": [field], "guidance": guidance},
            step=2,
        )

    def _bind_singlecell_scope(self, record):
        from .workbench_singlecell import apply_singlecell_trial

        already_bound = record.get("singlecell_trial")
        if (
            apply_singlecell_trial(
                record, getattr(self.app, "singlecell_trial_binding", None)
            )
            and not already_bound
        ):
            # Bind a current explicit opt-in to the matching startup declaration.
            # A retained record alone has no grant to bind after restart.
            grant = self._campaign_grants.get(record["id"])
            if grant is not None and grant["data_classification"] is None:
                grant["data_classification"] = record["scientific_context"][
                    "data_classification"
                ]
            self._turn(
                record,
                "question_scope",
                "Frozen single-cell study scope",
                {
                    "message": "The matching startup study supplies the exact "
                    "population, stage and biological-unit declarations. Current "
                    "campaign and execution grants remain separate.",
                    "binding": {
                        key: record["singlecell_trial"][key]
                        for key in (
                            "project_id",
                            "source_sha256",
                            "question_sha256",
                            "request_sha256",
                        )
                    },
                    "populations": record["singlecell_trial"][
                        "population_specification"
                    ]["populations"],
                    "stages": record["singlecell_trial"]["population_specification"][
                        "stage_levels"
                    ],
                    "biological_independence": record["scientific_context"][
                        "biological_independence"
                    ],
                    "choice_origin": "assistant_proposed",
                },
                step=2,
            )

    def _resolve_selection_scope(self, record):
        if not record.get("selection"):
            return False
        scope = record["scientific_context"].get("selection_scope")
        if scope is None:
            field = {
                "field": "selection_scope",
                "scope": "scientific_context",
                "label": "How should this selection be used?",
                "type": "select",
                "required": True,
                "options": [
                    {
                        "value": "saved_evidence_only",
                        "label": "Review selected saved evidence",
                    },
                    {
                        "value": "context_only_full_dataset",
                        "label": "Prepare a full-dataset plan; selection is context "
                        "only",
                    },
                ],
                "guidance": "The registered pipeline cannot silently fit a selected "
                "subpopulation. Choose saved evidence or explicitly confirm the full "
                "dataset.",
            }
            record.update(
                status="awaiting_information",
                clarification_fields=[field],
                required_action="selection_scope",
            )
            self._turn(
                record,
                "clarification",
                "Confirm the analysis population",
                {
                    "fields": [field],
                    "selected_observation_n": len(
                        record["selection"]["observation_ids"]
                    ),
                },
                step=2,
            )
            return True
        if scope == "saved_evidence_only":
            self._selected_evidence(record)
            return True
        record["population_scope"] = {
            "analysis_population": "full_registered_dataset",
            "selection_use": "context_only",
            "confirmation_source": "explicit_selection_scope_request",
            "selected_observation_n": len(record["selection"]["observation_ids"]),
        }
        return False

    def _prepare_analyst_episode(self, record):
        compiled = self.app.agentic_episodes.prepare(record)
        if record.get("clarification_fields"):
            self._turn(
                record,
                "clarification",
                "Confirm the biological units and contrast",
                {
                    "fields": record["clarification_fields"],
                    "scope": "explicit experimental declarations required",
                },
                step=2,
            )
        elif compiled["status"] == "ready":
            self._turn(
                record,
                "plan_preparation",
                "Preparing an analyst proposal",
                {
                    "job_id": record["job_id"],
                    "intake_binding": compiled["binding"],
                    "scope": "A model suggestion requires separate research review "
                    "and execution grants.",
                },
                step=3,
            )
        else:
            self._turn(
                record,
                "scope_limit",
                "Analyst scope requires attention",
                compiled,
                step=3,
            )

    def _uses_interactive_runtime(self, record):
        scientific = getattr(self.app, "scientific", None)
        return (
            getattr(self.app, "interactive_adaptive", False) is True
            and scientific is not None
            and scientific.scientific_workflow is None
            and not record.get("singlecell_trial")
            and record.get("analyst_profile") != "analyst_v1"
        )

    def _interactive_intake_fields(self, record):
        context = record["scientific_context"]
        fields = []
        if self.app.allow_model_execution and "data_classification" not in context:
            fields.extend(_grant_fields())
        observed = record["profile"]["observed"]
        if (
            observed.get("file_type") == "h5ad"
            and record["declarations"].get("assay", "scrna") in {"scrna", "snrna"}
        ):
            if not context.get("counts_layer"):
                fields.append(self._measurement_layer_field(observed))
            if "biological_independence" not in context and observed.get("obs_columns"):
                fields.extend(self._biological_unit_fields(observed["obs_columns"]))
        return fields

    @staticmethod
    def _measurement_layer_field(observed):
        return {
            "field": "counts_layer",
            "scope": "scientific_context",
            "label": "Measurement layer",
            "type": "select",
            "required": True,
            "options": [
                {"value": value, "label": value}
                for value in ["X", *observed.get("layers", [])]
            ],
            "guidance": "Choose the stored layer described by the measurement "
            "provenance. A layer name alone does not establish raw counts.",
        }

    @staticmethod
    def _biological_unit_fields(columns):
        return [
            {
                "field": "unit_column", "scope": "biological_independence",
                "label": "Biological unit column", "type": "select",
                "required": False,
                "options": [{"value": name, "label": name} for name in columns],
                "guidance": "Choose a column from experimental records. Leave it "
                "blank if the biological units are unknown.",
            },
            {
                "field": "evidence", "scope": "biological_independence",
                "label": "Evidence for biological unit identity", "type": "text",
                "required": False,
                "guidance": "Record how samples or cells map to donors, subjects, "
                "or other biological units.",
            },
            {
                "field": "independent", "scope": "biological_independence",
                "label": "Confirm the biological units are independently sampled",
                "type": "boolean", "required": False,
            },
        ]

    def _uses_fixed_profile(self, record):
        profile, declarations = record["profile"], record["declarations"]
        observed = profile["observed"]
        context = record["scientific_context"]
        layer = context.get("counts_layer")
        return (
            record["intent"]["kind"] in {"qc_and_integration", "batch_effect_audit"}
            and observed.get("file_type") in {"h5ad", ".h5ad", "anndata"}
            and declarations.get("assay") == "scrna"
            and declarations.get("measurement") == "umi_counts"
            and declarations.get("capture_unit_key")
            in observed.get("obs_columns", [])
            and (layer is None or layer in ["X", *observed.get("layers", [])])
        )

    def _prepare_fixed_profile(self, record):
        profile, declarations = record["profile"], record["declarations"]
        overrides = self._fixed_input_parameters(record)
        effective = {**self.app.parameters, **overrides}
        if effective.get("counts_layer") is None:
            effective["counts_layer"] = "X"
        unmet = [
            row
            for row in record["user_parameter_requirements"]
            if effective.get(row["name"]) != row["value"]
        ]
        if unmet:
            record["status"] = "awaiting_information"
            record["required_action"] = "scientific_context"
            self._turn(
                record,
                "clarification",
                "These requirements need a governed method plan",
                {
                    "unmatched_user_requirements": unmet,
                    "guidance": "The fixed workbench profile cannot silently change "
                    "scientific parameters.",
                },
                step=3,
            )
            return
        selector = getattr(self.app, "selector_config", None) or {}
        if (
            record["proposal_mode"] == "agent"
            and hasattr(self.app, "configuration")
            and not self.app.configuration()["proposal_selection"]["available"]
        ):
            self._record_diagnostic(record, "model_selector_not_configured")
            return
        if (
            record["proposal_mode"] == "agent"
            and selector.get("backend") == "codex-cli"
        ):
            context = record["scientific_context"]
            classification = context.get("data_classification", "unclassified")
            scope = getattr(self.app, "model_data_scope", "local_only")
            if (
                classification not in {"synthetic", "public"}
                or scope != classification
                or context.get("allow_model_execution") is not True
                or self.app.allow_model_execution is not True
            ):
                fields = _grant_fields()
                record.update(
                    status="awaiting_information",
                    clarification_fields=fields,
                    required_action="authorize_model_planning",
                )
                self._turn(
                    record,
                    "clarification",
                    "Confirm model planning access",
                    {
                        "fields": fields,
                        "configured_scope": scope,
                        "guidance": "Live proposal selection has not started. The "
                        "declared data scope, current campaign grant and server "
                        "permission must all match.",
                    },
                    step=3,
                )
                return
        # The controller's private keyword namespaces idempotency by this
        # investigation; reviewed plans from another thread cannot be reused.
        response = self.app.register(
            {
                "source": record["source"],
                "expected_sha256": profile["sha256"],
                "question": record["question"],
                "declarations": declarations,
                "proposal_mode": record["proposal_mode"],
            },
            investigation_id=record["id"],
            **({"input_parameters": overrides} if overrides else {}),
        )
        record.update(job_id=response["id"], status=response["status"])
        self._turn(
            record,
            "plan_preparation",
            "Preparing an analysis plan",
            {
                "job_id": response["id"],
                "proposal_mode": record["proposal_mode"],
                "population_scope": record.get(
                    "population_scope",
                    {"analysis_population": "full_registered_dataset"},
                ),
                "scope": "Registered scRNA QC, integration, clustering and available "
                "annotation; not comparative method superiority.",
            },
            step=3,
        )
        self._sync_job(record)

    def _fixed_input_parameters(self, record):
        context, parameters = record["scientific_context"], self.app.parameters
        capture = record["declarations"]["capture_unit_key"]
        previous_capture = parameters.get("capture_key") or parameters.get("batch_key")
        batch = context.get("batch_col") or (
            capture if capture != previous_capture else parameters.get("batch_key")
        )
        column_names = record["profile"]["observed"].get("obs_columns", [])
        if context.get("batch_col") and batch not in column_names:
            raise ValueError("The declared batch column is absent from AnnData")
        overrides = {
            name: column for name, column, previous in (
                ("capture_key", capture, previous_capture),
                ("batch_key", batch, parameters.get("batch_key")),
            ) if column != previous
        }
        if "counts_layer" in context:
            layer = None if context["counts_layer"] == "X" else context["counts_layer"]
            if layer != parameters.get("counts_layer"):
                overrides["counts_layer"] = layer
        return overrides

    def _prepare_scientific_plan(self, record):
        scientific = getattr(self.app, "scientific", None)
        context = record["scientific_context"]
        if not context.get("counts_layer"):
            explicit_layer = next(
                (
                    row
                    for row in record["user_parameter_requirements"]
                    if row["name"] == "counts_layer" and isinstance(row["value"], str)
                ),
                None,
            )
            if explicit_layer:
                context["counts_layer"] = explicit_layer["value"]
        request = context.get("plan_request")
        if scientific is None:
            self._record_diagnostic(record, "scientific_workspace_not_enabled")
            record["required_action"] = "configure_scientific_workspace"
            return
        if request is None:
            if not self._bind_scientific_measurements(record, scientific, context):
                return
            request = self._default_scientific_plan_request(record, scientific, context)
        self._create_or_resume_scientific_plan(record, scientific, context, request)

    def _bind_scientific_measurements(self, record, scientific, context):
        profile, declarations = record["profile"], record["declarations"]
        self._bootstrap_scientific_context(record)
        if record.get("singlecell_trial"):
            from devbench.core.population_contract import TOOL_NAMES

            from .scrna_population_admission import CONTRAST, PARQUET_CONTRAST
            from .workbench_singlecell import population_input_binding

            registered = (
                PARQUET_CONTRAST if name == CONTRAST else name for name in TOOL_NAMES
            )
            versions = [
                scientific.methods(write=True).register_dispatcher(
                    name, actor="scoped-singlecell-registry"
                )["version_id"]
                for name in registered
            ]
            record["singlecell_method_versions"] = versions
            dataset = scientific.store().resolve(context["dataset_ref"])
            context["rna_input_contract"] = population_input_binding(
                record, dataset["manifest"]["members"]["anndata"]["artifact_id"]
            )
        elif record.get("sources"):
            dataset = scientific.store().resolve(context["dataset_ref"])
            context["rna_input_contract"] = self._input_set_binding(record, dataset)
        elif profile["observed"].get("file_type") == "h5ad" and declarations.get(
            "assay"
        ) in {"scrna", "snrna"}:
            if not context.get("counts_layer"):
                field = self._measurement_layer_field(profile["observed"])
                record.update(
                    status="awaiting_information",
                    clarification_fields=[field],
                    required_action="measurement_layer",
                )
                self._turn(
                    record,
                    "clarification",
                    "Confirm the measurement layer",
                    {"fields": [field]},
                    step=1,
                )
                return False
            if context["counts_layer"] not in [
                "X",
                *profile["observed"].get("layers", []),
            ]:
                raise ValueError(
                    "Measurement layer must be present in the inspected AnnData header"
                )
            dataset = scientific.store().resolve(context["dataset_ref"])
            members = dataset["manifest"]["members"]
            artifact = (
                members["anndata"]
                if set(members) == {"anndata"}
                else members["dataset"]
            )
            context["rna_input_contract"] = {
                "format": "devbench.scrna-input-binding/1",
                "anndata_artifact_id": artifact["artifact_id"],
                "declarations": {
                    **declarations,
                    "counts_layer": context["counts_layer"],
                    "biological_independence": context.get("biological_independence"),
                },
            }
        return True

    def _default_scientific_plan_request(self, record, scientific, context):
        request = {
            "action": "create_plan",
            "actor": "investigation-controller",
            "expected_revision": scientific.catalog()["revision"],
            "analysis_ref": record["scientific_context"]["analysis_ref"],
            "dataset_ref": record["scientific_context"]["dataset_ref"],
            "evaluation_purpose": "method_selection",
            "user_parameter_requirements": record["user_parameter_requirements"],
        }
        for key in (
            "rna_input_contract",
            "rna_execution_inputs_ref",
            "rna_execution_workspace",
            "scientific_contract",
        ):
            if key in context:
                request[key] = context[key]
        return request

    def _create_or_resume_scientific_plan(self, record, scientific, context, request):
        request = self._bound_plan_request(record, request)
        binding_key = digest(
            {
                key: value
                for key, value in request.items()
                if key not in {"actor", "expected_revision"}
            }
        )
        if record.get("scientific_plan_key") != binding_key:
            result = scientific.action(request)
            record["scientific_session_id"] = result["session_id"]
            record["scientific_plan_key"] = binding_key
            self._capture_planning_source(
                record, result["session_id"], "session_created"
            )
            self._turn(
                record,
                "scientific_action",
                "Scientific plan created",
                {
                    "action": "create_plan",
                    "result": result,
                    "planning_source": record["planning_source"],
                },
                step=3,
            )
        else:
            result = scientific._plan(record["scientific_session_id"])
        context["session_id"] = record["scientific_session_id"]
        if record.get("adaptive_lineage"):
            record["adaptive_lineage"]["planner_session_id"] = context["session_id"]
        if self._uses_interactive_runtime(record):
            self._bind_interactive_session(record, scientific, context)
            return
        advance = record["scientific_context"].get("advance_request")
        if advance:
            advance = {
                **_copy(advance),
                "action": "advance_to_gate",
                "session_id": result["session_id"],
                "expected_revision": scientific.catalog()["revision"],
            }
            self._scientific(record, advance)
            return
        self._advance_or_gate(record, result)
        self._project_scientific_plan(record)

    def _bind_interactive_session(self, record, scientific, context):
        from .workbench_interactive import bind_investigation_session

        sid = context["session_id"]
        grant = self._current_grants.get(record["id"], {})
        binding = bind_investigation_session(
            self.app, record, scientific._plan(sid), self._open_ledger(write=True),
            allow_model_execution=grant.get("allow_model_execution") is True,
        )
        record["adaptive_lineage"]["execution_mode"] = "adaptive/1"
        record["status"] = "adaptive_workflow"
        record["workflow_mode"] = "adaptive/1"
        record["campaign_mode"] = binding.campaign_mode
        record["clarification_fields"] = []
        record.pop("required_action", None)
        self._project_scientific_plan(record)

    def _selected_evidence(self, record):
        """Describe only verified saved geometry, never an inferred expression fit."""
        selection = record["selection"]
        validated = self._selection(
            {
                key: selection[key]
                for key in (
                    "figure_id",
                    "panel_id",
                    "observation_ids",
                    "source_job",
                    "parent_root",
                    "display_only",
                )
            },
            record["parent_job"],
            record["source"],
            record["profile"]["sha256"],
        )
        figures = self.app.figures(selection["source_job"])
        figure = next(
            row
            for row in figures["figures"]
            if row["figure_id"] == selection["figure_id"]
        )
        payload = self._figure_payload(selection["source_job"], figure)
        panel = next(
            row for row in payload["panels"] if row["panel_id"] == selection["panel_id"]
        )
        ids = set(validated["observation_ids"])
        rows = [row for row in panel["rows"] if row["observation_id"] in ids]
        colors = {}
        for row in rows:
            color = str(row.get("color", "unavailable"))
            colors[color] = colors.get(color, 0) + 1
        findings = {
            "selected_observation_n": len(rows),
            "observation_ids": validated["observation_ids"],
            "source_job": selection["source_job"],
            "parent_root": selection["parent_root"],
            "figure_id": selection["figure_id"],
            "panel_id": selection["panel_id"],
            "saved_color_counts": colors,
            "saved_axes": {key: panel.get(key) for key in ("x_axis", "y_axis")},
        }
        for axis in ("x", "y"):
            values = [
                row[axis]
                for row in rows
                if type(row.get(axis)) in (int, float) and math.isfinite(row[axis])
            ]
            if values:
                findings["saved_" + axis + "_range"] = {
                    "min": min(values),
                    "max": max(values),
                }
        record["population_scope"] = {
            "analysis_population": "selected_saved_observations",
            "selection_use": "saved_evidence_only",
            "new_analysis_executed": False,
        }
        record["synthesis"] = {
            "supported_findings": findings,
            "method_evidence_assessment": {
                "method": "Read-only summary of verified saved figure data",
                "new_analysis_executed": False,
                "evidence_floor": "Exact saved identities, coordinates and recorded "
                "colors; no expression matrix, differential test, model or scientific "
                "review was run.",
            },
            "uncertainties": [
                {
                    "field": "expression_and_effects",
                    "reason": "Saved embedding geometry cannot establish differential "
                    "expression, population-specific effects, biological independence "
                    "or causality.",
                }
            ],
        }
        record["status"] = "completed_saved_evidence"
        self._turn(
            record, "synthesis", "Selected saved evidence", record["synthesis"], step=5
        )

    def _bootstrap_scientific_context(self, record):
        """Create metadata from observations/question, without invented biology."""
        scientific, context = self.app.scientific, record["scientific_context"]
        metadata = self._observed_dataset_metadata(record)

        def apply(action_name, **fields):
            result = scientific.action(
                {
                    "action": action_name,
                    "actor": "investigation-controller",
                    "expected_revision": scientific.catalog()["revision"],
                    **fields,
                }
            )
            self._turn(
                record,
                "scientific_action",
                action_name.replace("_", " "),
                {
                    "action": action_name,
                    "result": result,
                    "scope": "Question and observed-input metadata; no model or "
                    "analysis execution.",
                },
                step=1 if action_name == "register_dataset" else 2,
            )
            return result

        if not scientific.catalog()["initialized"]:
            apply("initialize")
        if context.get("dataset_ref") and not context.get("study_id"):
            version = scientific.store().resolve(context["dataset_ref"])
            context["study_id"] = scientific.store().get(
                "dataset", version["dataset_id"]
            )["study_id"]
        if not context.get("study_id"):
            context["study_id"] = apply(
                "create_study",
                name="Investigation " + record["id"][:8],
                aim=record["question"],
            )["study_id"]
            self._save(record)

        self._bind_scientific_dataset(record, context, metadata, apply)
        if (
            not context.get("analysis_ref")
            or record.get("managed_analysis")
            and record.get("analysis_question") != record["question"]
        ):
            result = apply(
                "create_analysis",
                study_id=context["study_id"],
                name="Question " + record["id"][:8],
                objective=record["question"],
                computational_task=record["intent"]["kind"],
            )
            context["analysis_ref"] = result["ref"]
            record.update(managed_analysis=True, analysis_question=record["question"])
            self._save(record)
        catalog = scientific.catalog()
        record["scientific_options"] = {
            "studies": [
                {"study_id": row["study_id"], "name": row["name"]}
                for row in catalog.get("studies", [])[:100]
            ],
            "datasets": [
                {"ref": row["ref"], "name": row["name"]}
                for row in catalog.get("datasets", [])[:100]
            ],
            "analyses": [
                {"ref": row["ref"], "name": row["name"]}
                for row in catalog.get("analyses", [])[:100]
            ],
        }

    def _observed_dataset_metadata(self, record):
        context, profile = record["scientific_context"], record["profile"]
        classification = context.get("data_classification", "unclassified")
        if classification not in {"unclassified", "synthetic", "public", "private"}:
            raise ValueError(
                "Declare unclassified, synthetic, public or private input scope"
            )
        for key in ("allow_model_execution", "auto_advance", "auto_synthesis"):
            if key in context and type(context[key]) is not bool:
                raise ValueError("Scientific campaign grants must be explicit booleans")
        unknowns = [
            {
                "field": row["field"],
                "impact": row.get("reason", row.get("impact", "Not established")),
                "blocking": row.get("blocking", False),
            }
            for row in profile.get("unknowns", [])
        ]
        inherited = (record.get("context_inheritance") or {}).get(
            "inherited_fields", []
        )
        origin = (
            "retained_parent_declaration"
            if "data_classification" in inherited
            else "caller_declaration"
            if "data_classification" in context
            else "unclassified_default"
        )
        observed = profile["observed"]
        if self._uses_interactive_runtime(record) and record.get("sources"):
            observed = dict(observed)
            observed["sources"] = [
                {key: value for key, value in item.items() if key != "source"}
                for item in observed.get("sources", [])
            ]
        metadata = {
            "observed": observed,
            "inferred": profile.get("inferred", {}),
            "unknowns": unknowns,
            "supplied_declarations": record["declarations"],
            "data_classification": classification,
            "classification_source": origin,
        }
        if record.get("sources"):
            metadata.update(
                source_declarations=_copy(record["source_declarations"]),
                input_set_sha256=record["input_set_sha256"],
            )
        return metadata

    def _bind_scientific_dataset(self, record, context, metadata, apply):
        scientific, profile = self.app.scientific, record["profile"]
        prior = context.get("dataset_ref")
        if record.get("sources") and prior:
            retained = scientific.store().resolve(prior)["manifest"]["metadata"]

            def comparable(value):
                return {
                    key: item
                    for key, item in value.items()
                    if key != "classification_source"
                }

            if comparable(retained) == comparable(metadata):
                metadata = _copy(retained)
            record.setdefault(
                "metadata_key",
                digest(
                    {
                        "metadata": retained,
                        "input_sha256": profile["sha256"],
                    }
                ),
            )
            record["managed_metadata"] = True
        metadata_key = digest({"metadata": metadata, "input_sha256": profile["sha256"]})
        if prior and (
            not record.get("managed_metadata")
            or record.get("metadata_key") == metadata_key
        ):
            return

        def member(source, profile):
            artifact_type = {"h5ad": "anndata", "json": "json"}.get(
                profile["observed"].get("file_type"),
                "table",
            )
            return {"path": source, "artifact_type": artifact_type}

        if record.get("sources"):
            members = {
                f"source_{index}": member(source, item)
                for index, (source, item) in enumerate(
                    zip(record["sources"], record["source_profiles"], strict=True),
                    1,
                )
            }
        else:
            name = "anndata" if record.get("singlecell_trial") else "dataset"
            members = {name: member(record["source"], profile)}
        fields = {
            "study_id": context["study_id"],
            "name": profile["filename"],
            "members": members,
            "metadata": metadata,
        }
        if prior:
            fields["dataset_id"] = prior["object_id"]
        context["dataset_ref"] = apply("register_dataset", **fields)["ref"]
        record.update(managed_metadata=True, metadata_key=metadata_key)
        self._save(record)

    def _advance_or_gate(self, record, plan):
        context, scientific = record["scientific_context"], self.app.scientific
        configuration = scientific.configuration()
        fields = _grant_fields(advance=True)
        if record.get("singlecell_trial"):
            fields = [
                field for field in fields if field["field"] != "data_classification"
            ]
            fields.insert(
                0,
                {
                    "field": "allow_data_execution",
                    "scope": "scientific_context",
                    "label": "Allow bounded pre-fit study inspection for this campaign",
                    "type": "boolean",
                    "required": True,
                    "options": [],
                    "guidance": "Current authorization for registered membership, "
                    "design and pseudobulk count checks; final inference retains its "
                    "separate trial review/run gate.",
                },
            )
        record.update(
            status="awaiting_information",
            clarification_fields=fields,
            required_action="scientific_authorization",
        )
        classification = context.get("data_classification", "unclassified")
        offline = (
            configuration.get("planning_mode") == "recorded_fixture"
            and classification == "synthetic"
        )
        live = (
            classification in {"synthetic", "public"}
            and context.get("allow_model_execution") is True
            and self.app.allow_model_execution is True
            and getattr(self.app, "model_data_scope", None) == classification
        )
        if record.get("singlecell_trial"):
            live = (
                live
                and self._current_grants.get(record["id"], {}).get(
                    "allow_model_execution"
                )
                is True
            )
        if not configuration.get("scientific_workflow_configured"):
            record["required_action"] = "configure_scientific_workflow"
            record["diagnostic_code"] = "scientific_workflow_not_configured"
        elif record.get("singlecell_trial") and not (
            context.get("allow_data_execution") is True
            and self.app.allow_data_execution is True
            and self._current_grants.get(record["id"], {}).get("allow_data_execution")
            is True
        ):
            record["required_action"] = "authorize_data_execution"
        elif not (
            {"bulk_rna_input", "scrna_input", "rna_input_set"}
            & plan["context"]["dataset_summary"].get("observed", {}).keys()
            or "rna_execution_inputs" in plan
        ):
            record["required_action"] = "scientific_input_binding"
        elif context.get("auto_advance") is True and (offline or live):
            request = {
                "action": "advance_to_gate",
                "session_id": record["scientific_session_id"],
                "expected_revision": scientific.catalog()["revision"],
                "actor": "investigation-controller",
                "idempotency_key": record["id"]
                + ":planning:"
                + str(record["revision"]),
                "allow_model_execution": bool(live),
                "phase": "planning",
            }
            if record.get("singlecell_trial"):
                request["allow_data_execution"] = (
                    context.get("allow_data_execution") is True
                )
                request["method_version_ids"] = record["singlecell_method_versions"]
            self._scientific(record, request)
            record["clarification_fields"] = []
            return
        self._turn(
            record,
            "clarification",
            "Review scientific planning context",
            {
                "fields": fields,
                "required_action": record["required_action"],
                "references": {
                    key: context.get(key)
                    for key in ("study_id", "dataset_ref", "analysis_ref", "session_id")
                },
                "guidance": "The question is anchored to observed data. Planning still "
                "needs its actual registered input contract, configured adapters and "
                "explicit campaign grants; no replacement QC analysis was run.",
            },
            step=3,
        )

    def _bound_plan_request(self, record, request):
        if not isinstance(request, dict) or request.get("action") != "create_plan":
            raise ValueError(
                "Scientific context requires an explicit create_plan request with "
                "exact version references"
            )
        scientific = self.app.scientific
        # The selected scientific dataset must contain the exact audited input.
        store, methods = scientific.store(), scientific.methods()
        dataset = store.resolve(request["dataset_ref"])
        hashes = [
            methods.get("artifact", value).get("sha256")
            for value in store.input_bindings([dataset["version_id"]]).values()
        ]
        analysis = store.resolve(request["analysis_ref"])
        input_matches = record["profile"]["sha256"] in hashes
        if record.get("sources"):
            input_matches = len(hashes) == len(record["sources"]) and set(hashes) == {
                profile["sha256"] for profile in record["source_profiles"]
            }
        if (
            not input_matches
            or analysis["definition"].get("task", {}).get("scientific_objective")
            != record["question"]
        ):
            raise ValueError(
                "Scientific plan context must bind this exact input and question"
            )
        record["scientific_context"].update(
            dataset_ref=_copy(request["dataset_ref"]),
            analysis_ref=_copy(request["analysis_ref"]),
        )
        request = _copy(request)
        if record.get("sources"):
            expected_binding = self._input_set_binding(record, dataset)
            if request.get("rna_input_contract", expected_binding) != expected_binding:
                raise ValueError(
                    "Scientific plan must retain every ordered input-set member and "
                    "its declarations"
                )
            request["rna_input_contract"] = expected_binding
            record["scientific_context"]["rna_input_contract"] = _copy(expected_binding)
        elif record.get("singlecell_trial"):
            from .workbench_singlecell import population_input_binding

            expected_binding = population_input_binding(
                record, dataset["manifest"]["members"]["anndata"]["artifact_id"]
            )
            if request.get("rna_input_contract", expected_binding) != expected_binding:
                raise ValueError(
                    "Scientific plan must preserve the exact frozen population/stage "
                    "input specification"
                )
            request["rna_input_contract"] = expected_binding
            record["scientific_context"]["rna_input_contract"] = _copy(expected_binding)
        request["user_parameter_requirements"] = record["user_parameter_requirements"]
        return request

    def _input_set_binding(self, record, dataset):
        from devbench.analysis.rna_input_set_contract import BINDING_FORMAT

        members = dataset["manifest"]["members"]
        expected_names = [
            "source_" + str(index) for index in range(1, len(record["sources"]) + 1)
        ]
        if set(members) != set(expected_names):
            raise ValueError(
                "Scientific dataset must bind exactly the complete named source set"
            )
        result = []
        for name, profile, declarations in zip(
            expected_names,
            record["source_profiles"],
            record["source_declarations"],
            strict=False,
        ):
            identity = members[name]["artifact_id"]
            artifact = self.app.scientific.methods().get("artifact", identity)
            if (
                artifact["sha256"] != profile["sha256"]
                or artifact["size_bytes"] != profile["size_bytes"]
            ):
                raise ValueError(
                    "Scientific member order or source byte identity differs from the "
                    "inspected input set"
                )
            result.append(
                {
                    "name": name,
                    "label": profile["filename"][:200],
                    "artifact_id": identity,
                    "declarations": _copy(declarations),
                }
            )
        return {"format": BINDING_FORMAT, "members": result}

    def _sync_job(self, record):
        if not record.get("job_id"):
            return
        if record.get("analyst_profile") == "analyst_v1":
            self._sync_episode(record)
            return
        state = self.app.status(record["job_id"])
        record["job_state"] = _copy(state)
        record["status"] = state["status"]
        existing = {
            turn["content"].get("event_key")
            for turn in record["turns"]
            if turn["kind"] == "job_event"
        }
        for index, event in enumerate(state.get("state_history", [])):
            key = digest({"job_id": record["job_id"], "index": index, "event": event})
            if key not in existing:
                self._turn(
                    record,
                    "job_event",
                    event["to"].replace("_", " "),
                    {"job_id": record["job_id"], "event_key": key, **event},
                    step=4
                    if event["to"]
                    in {"preparing", "running", "finalizing", "cancel_requested"}
                    or event["to"].startswith("completed_")
                    else 3,
                )
        if state.get("proposal"):
            key = digest(state["proposal"])
            if record.get("plan_key") != key:
                record["plan_key"] = key
                self._turn(
                    record,
                    "plan_review",
                    "Analysis plan",
                    {
                        "job_id": state["id"],
                        "proposal": state["proposal"],
                        "status": state["status"],
                    },
                    step=3,
                )
        if state["status"].startswith("completed_review_"):
            results = self.app.results(state["id"])
            figures = self.app.figures(state["id"])
            evidence_key = digest({"results": results, "root": figures["parent_root"]})
            if record.get("evidence_key") != evidence_key:
                record["evidence_key"] = evidence_key
                record["artifacts"] = [
                    {**item, "job_id": state["id"]}
                    for item in results.get("artifacts", [])
                ]
                record["figures"] = figures["figures"]
                report = results.get("report", {})
                record["synthesis"] = {
                    "supported_findings": {
                        "execution_status": state["status"],
                        "metrics": {
                            key: report[key]
                            for key in ("cells_input", "cells_retained", "HVG_count")
                            if key in report
                        },
                        "deterministic_checks": results.get("deterministic_review", {}),
                        "bundle_root": figures["parent_root"],
                    },
                    "method_evidence_assessment": {
                        "method": state.get("proposal", {})
                        .get("configuration", {})
                        .get("parameters", {})
                        .get("workflow"),
                        "selection_basis": "Existing registered profile selected for "
                        "the reviewed scope; no comparative method evaluation was "
                        "performed.",
                        "evidence_floor": "Verified execution artifacts and "
                        "deterministic output checks; independent scientific review "
                        "is separate.",
                        "parameter_requirements": record["user_parameter_requirements"],
                    },
                    "uncertainties": [
                        *record["profile"].get("unknowns", []),
                        {
                            "field": "experimental_declarations",
                            "reason": "Experimental identity and assay provenance are "
                            "operator declarations, not independent validation.",
                        },
                        {
                            "field": "biological_interpretation",
                            "reason": "Cell-level descriptive results do not "
                            "establish independent biological replication, causal "
                            "effects, method superiority or clinical validity.",
                        },
                    ],
                }
                record["synthesis"]["method_evidence_assessment"][
                    "population_scope"
                ] = record.get(
                    "population_scope",
                    {"analysis_population": "full_registered_dataset"},
                )
                self._turn(
                    record,
                    "synthesis",
                    "Results and remaining uncertainty",
                    record["synthesis"],
                    step=5,
                )

    def _sync_episode(self, record):
        state = self.app.agentic_episodes.status(record["job_id"])
        if (
            state.get("investigation_id") != record["id"]
            or state.get("thread_id") != record["thread_id"]
        ):
            raise ValueError("Episode job does not bind this investigation thread")
        record.update(job_state=_copy(state), status=state["status"])
        existing = {turn["content"].get("event_key") for turn in record["turns"]}
        for index, event in enumerate(state.get("state_history", [])):
            key = digest({"job_id": state["id"], "index": index, "event": event})
            if key not in existing:
                self._turn(
                    record,
                    "job_event",
                    event["to"].replace("_", " "),
                    {"job_id": state["id"], "event_key": key, **event},
                    step=4 if event["to"] == "running" else 3,
                )
        for index, selection in enumerate(state.get("agent_selections", []), 1):
            key = digest(
                {"job_id": state["id"], "selection": index, "kind": "analyst_action"}
            )
            if key not in existing:
                chosen = {
                    name: value
                    for name, value in selection.items()
                    if name
                    not in {
                        "query_result",
                        "query_receipt",
                        "query_outcome",
                        "status",
                        "state",
                    }
                }
                self._turn(
                    record,
                    "analyst_action",
                    "Analyst selected "
                    + str((selection.get("action") or {}).get("kind", "action")),
                    {
                        "job_id": state["id"],
                        "event_key": key,
                        "selection_number": index,
                        **chosen,
                    },
                    step=4,
                    role="assistant",
                )
            query_key = digest(
                {"job_id": state["id"], "selection": index, "kind": "analyst_query"}
            )
            if selection.get("query_result") is not None and query_key not in existing:
                self._turn(
                    record,
                    "analyst_query",
                    "Retained evidence query result",
                    {
                        "job_id": state["id"],
                        "event_key": query_key,
                        "selection_number": index,
                        "query_result": selection["query_result"],
                        "query_receipt_ref": selection.get("query_receipt_ref"),
                    },
                    step=4,
                    role="assistant",
                )
            elif (
                selection.get("query_outcome") is not None and query_key not in existing
            ):
                outcome = selection["query_outcome"]
                self._turn(
                    record,
                    "analyst_query",
                    "Retained evidence query rejected",
                    {
                        "job_id": state["id"],
                        "event_key": query_key,
                        "selection_number": index,
                        "action": selection["action"],
                        "query_outcome": outcome,
                        "status": outcome["status"],
                        "message": outcome["message"],
                        "reason": outcome["reason"],
                        "selection_ref": selection.get("selection_ref"),
                    },
                    step=4,
                    role="assistant",
                )
        self._retain_episode_outputs(record, state)

    def _retain_episode_outputs(self, record, state):
        if state.get("proposal") and record.get("plan_key") != digest(
            state["proposal"]
        ):
            record["plan_key"] = digest(state["proposal"])
            self._turn(
                record,
                "plan_review",
                "Analysis plan",
                {
                    "job_id": state["id"],
                    "proposal": state["proposal"],
                    "status": state["status"],
                    "confirmations": [
                        "question_and_contrast",
                        "input_provenance",
                        "biological_units_and_design",
                        "limitations",
                    ],
                },
                step=3,
            )
        if state.get("episode_receipt"):
            key = digest(state["episode_receipt"])
            if record.get("evidence_key") != key:
                record["evidence_key"] = key
                record["artifacts"] = [
                    {
                        "kind": "episode_output",
                        "name": name,
                        "job_id": state["id"],
                        **state.get("output_descriptors", {}).get(name, {}),
                        "content_type": "text/markdown"
                        if name == "summary"
                        else "application/json",
                        "download_url": "/api/agentic-episodes/"
                        + state["id"]
                        + "/download/"
                        + name,
                    }
                    for name in state.get("download_names", [])
                ]
                record["synthesis"] = {
                    **(state.get("synthesis") or {}),
                    "supported_findings": state.get("summary"),
                    "execution_verification": state.get("episode_verification"),
                    "uncertainties": [
                        "Independent scientific review and biological agreement have "
                        "not been established."
                    ],
                }
                self._turn(
                    record,
                    "synthesis",
                    "Episode findings and uncertainty",
                    record["synthesis"],
                    step=5,
                )

    def _scientific(self, record, request, operator_review=None):
        if record.get("analyst_profile") == "analyst_v1":
            raise RuntimeError(
                "The episode is the sole scientific decision driver for this "
                "investigation"
            )
        scientific = getattr(self.app, "scientific", None)
        if scientific is None or not isinstance(request, dict):
            raise ValueError(
                "Scientific workspace is unavailable or its request is invalid"
            )
        request = self._validated_scientific_request(record, request)
        name = request["action"]
        if name in {"declare_trial", "run_trial"}:
            self._require_trial_gate(record, request, operator_review)
        if operator_review is not None:
            request = self._reviewed_trial_request(request, operator_review)
        result = scientific.action(_copy(request))
        self._retain_scientific_action(record, request, result, operator_review)

    def _validated_scientific_request(self, record, request):
        if request.get("allow_model_execution") is True and record[
            "scientific_context"
        ].get("data_classification") not in {"synthetic", "public"}:
            raise PermissionError(
                "Model planning requires an explicit public or synthetic dataset "
                "declaration"
            )
        if request.get("allow_model_execution") is True and (
            self.app.allow_model_execution is not True
            or getattr(self.app, "model_data_scope", None)
            != record["scientific_context"]["data_classification"]
        ):
            raise PermissionError(
                "Scientific model planning requires current startup permission and the "
                "exact declared server data scope"
            )
        if request.get("action") == "create_plan":
            request = self._bound_plan_request(record, request)
        if "session_id" in request and request["session_id"] != record.get(
            "scientific_session_id"
        ):
            raise ValueError(
                "Scientific action must use this investigation's exact planner session"
            )
        for key in ("dataset_ref", "analysis_ref"):
            if key in request and request[key] != record["scientific_context"].get(key):
                raise ValueError(
                    "Scientific action must use this investigation's exact " + key
                )
        allowed = {
            "create_plan",
            "advance_to_gate",
            "discovery",
            "candidate_review",
            "implementation",
            "design_review",
            "conclusion_review",
            "science",
            "resolve",
            "design_trials",
            "draft_method",
            "recommend",
            "declare_trial",
            "run_trial",
            "rerun",
        }
        if request.get("action") not in allowed:
            raise ValueError(
                "Use this investigation's planning or controlled-trial actions; "
                "metadata setup uses its bound context"
            )
        name = request.get("action")
        if request.get("phase") == "synthesis" or name in {
            "recommend",
            "conclusion_review",
        }:
            raise PermissionError(
                "Use the bound post-run synthesis continuation for this investigation."
            )
        return request

    def _require_trial_gate(self, record, request, operator_review):
        name = request["action"]
        self._project_scientific_plan(record)
        gate = record["gate_options"]
        if not gate["can_submit"] or gate["gate"] != (
            "review" if name == "declare_trial" else "execution_review"
        ):
            raise RuntimeError(
                "This scientific controller is not at the verified operator gate for "
                "this action"
            )
        if name == "declare_trial":
            matches = [
                row
                for row in gate["proposals"]
                if row["eligible"] and row["spec"] == request.get("spec")
            ]
            if len(matches) != 1:
                raise ValueError(
                    "Choose one exact eligible frozen scientific trial proposal"
                )
            if operator_review is None:
                raise ValueError(
                    "Scientific trial declaration requires an explicit operator review "
                    "record"
                )
        elif not any(
            row["eligible"] and row["experiment_id"] == request.get("experiment_id")
            for row in gate["declared_trials"]
        ):
            raise ValueError(
                "Execution must match the next exact eligible declared trial"
            )

    @staticmethod
    def _reviewed_trial_request(request, operator_review):
        name = request["action"]
        from devbench.analysis.episode_proposals import _review_decision

        if name not in {"declare_trial", "run_trial"}:
            raise ValueError(
                "Operator trial review metadata belongs only to declaration or "
                "execution actions"
            )
        if not isinstance(operator_review, dict) or set(operator_review) != {
            "reviewer",
            "rationale",
            "confirmations",
        }:
            raise ValueError(
                "Operator review requires reviewer, rationale and explicit "
                "confirmations"
            )
        confirmations = (
            set(SCIENTIFIC_CONFIRMATIONS) if name == "declare_trial" else set()
        )
        _review_decision(
            "approve",
            operator_review["reviewer"],
            operator_review["rationale"],
            operator_review["confirmations"],
            required_confirmations=confirmations,
        )
        return {**_copy(request), "actor": operator_review["reviewer"].strip()}

    def _retain_scientific_action(self, record, request, result, operator_review):
        name = request["action"]
        if result.get("session_id"):
            record["scientific_session_id"] = result["session_id"]
        if record.get("scientific_session_id"):
            self._capture_planning_source(
                record,
                record["scientific_session_id"],
                "session_created"
                if name == "create_plan"
                else "bound_action_dispatched",
            )
        if result.get("operation_id"):
            record["scientific_operations"].append(result["operation_id"])
            record.pop("gate", None)
            record.pop("required_action", None)
        if result.get("analysis_ref"):
            self._retain_scientific_analysis_ref(record, result, name)
        if operator_review is not None:
            self._turn(
                record,
                "plan_review",
                "Scientific trial approved"
                if name == "declare_trial"
                else "Controlled execution authorized",
                {
                    "session_id": record["scientific_session_id"],
                    "operator_review": operator_review,
                    "selected_trial": (request.get("spec") or {}).get(
                        "id", request.get("experiment_id")
                    ),
                    "scientific_analysis_ref": result.get("analysis_ref"),
                    "actor_authenticated": False,
                },
                step=3 if name == "declare_trial" else 4,
                role="operator",
            )
        record["status"] = (
            "planning" if request.get("action") != "run_trial" else "preparing"
        )
        self._turn(
            record,
            "scientific_action",
            "Scientific action recorded",
            {
                "action": request.get("action"),
                "result": result,
                "planning_source": record.get("planning_source"),
            },
            step=4
            if name in {"run_trial", "rerun"}
            else 5
            if request.get("phase") == "synthesis"
            or name in {"recommend", "conclusion_review"}
            else 3,
        )
        self._project_scientific_plan(record)

    def _retain_scientific_analysis_ref(self, record, result, name):
        scientific = self.app.scientific
        previous = record["scientific_context"].get("analysis_ref")
        if (
            previous
            and result["analysis_ref"].get("object_id") != previous["object_id"]
        ):
            raise ValueError(
                "Scientific action returned an unrelated Analysis identity"
            )
        if name == "declare_trial":
            plan = scientific._plan(record["scientific_session_id"])
            expected = plan.get("scientific_trial_definitions", {}).get(
                result.get("experiment", {}).get("id")
            )
            if expected != result["analysis_ref"]:
                raise ValueError(
                    "Declared scientific analysis version differs from its exact "
                    "planner trial definition"
                )
        record["scientific_context"]["analysis_ref"] = _copy(result["analysis_ref"])

    def _scientific_catalog(self):
        # Status reads can coincide with the ledger's short completion commit.
        for delay in (0.01, 0.03, 0.05):
            try:
                return self.app.scientific.catalog()
            except OperationBlocked as error:
                if error.state != "ledger_busy":
                    raise
                time.sleep(delay)
        return self.app.scientific.catalog()

    def _sync_scientific(self, record):
        if record.get("analyst_profile") == "analyst_v1":
            return
        scientific = getattr(self.app, "scientific", None)
        if scientific is None or not record.get("scientific_session_id"):
            return
        catalog = self._scientific_catalog()
        for operation in self._synthesis_operations(record, catalog):
            if operation["operation_id"] not in record["scientific_operations"]:
                record["scientific_operations"].append(operation["operation_id"])
        if record.get("singlecell_trial"):
            self._sync_singlecell_evidence(record)
        self._sync_scientific_steps(record, catalog)
        for identity in record["scientific_operations"]:
            operation = next(
                (
                    row
                    for row in catalog.get("operations", [])
                    if row["operation_id"] == identity
                ),
                None,
            )
            if operation is None:
                record["status"] = "recovery_required"
                continue
            self._sync_scientific_operation(record, operation)
        record["operation_pending"] = any(
            row.get("operation_id") in record["scientific_operations"]
            and row.get("status") in {"queued", "running"}
            for row in catalog.get("operations", [])
        )
        if record.get("scientific_cancel_requested"):
            record["status"] = (
                "cancelling" if record["operation_pending"] else "cancelled"
            )
            return
        if (
            record["status"] == "recovery_required"
            and not record["operation_pending"]
            and any(
                step["session_id"] == record["scientific_session_id"]
                and step["node"] == "conclusion_review"
                and step["attempt"] == 1
                and step["status"] == "completed"
                and step["controller_status"] == "review_required"
                for step in catalog.get("workflow_steps", [])
            )
        ):
            record.update(
                status="awaiting_information",
                required_action="inspect_scientific_review",
                scientific_blocker_reason="The reconciled conclusion review is "
                "blocked under the retained review policy.",
            )
        self._project_scientific_plan(record)

    def _sync_scientific_steps(self, record, catalog):
        for step in catalog.get("workflow_steps", []):
            if step.get("session_id") != record.get("scientific_session_id"):
                continue
            key = digest(step)
            if not any(
                turn["kind"] == "scientific_step"
                and turn["content"].get("evidence_key") == key
                for turn in record["turns"]
            ):
                self._turn(
                    record,
                    "scientific_step",
                    step["node"].replace("_", " "),
                    {"evidence_key": key, "step": step},
                    step=4
                    if step["node"] == "run"
                    else 5
                    if step["node"] in {"verify", "recommend", "conclusion_review"}
                    else 3,
                )

    def _sync_scientific_operation(self, record, operation):
        key = digest(operation)
        if any(
            turn["kind"] == "scientific_operation"
            and turn["content"].get("evidence_key") == key
            for turn in record["turns"]
        ):
            return
        value = operation.get("value") or {}
        self._turn(
            record,
            "scientific_operation",
            operation["action"].replace("_", " "),
            {"evidence_key": key, "operation": operation},
            step=4
            if operation["action"] in {"run_trial", "rerun"}
            else 5
            if (operation.get("request") or {}).get(
                "phase", value.get("requested_phase", value.get("phase"))
            )
            in {"synthesis", "conclusion_review"}
            else 3,
        )
        if value.get("status", operation["status"]) in {
            "failed",
            "recovery_required",
            "operation_failed",
            "infrastructure_failure",
        }:
            self._project_scientific_failure(record, operation, key)
        if operation["status"] == "completed":
            self._apply_completed_scientific_operation(record, value)
        else:
            record["status"] = operation["status"]

    def _apply_completed_scientific_operation(self, record, value):
        if value.get("results"):
            self._bind_scientific_artifacts(record, value)
        record["status"] = value.get("status", "awaiting_information")
        if value.get("gate"):
            record["gate"] = value["gate"]
        if value.get("required_action"):
            record["required_action"] = value["required_action"]
            record["scientific_blocker_reason"] = value.get("reason")
        if value.get("requested_phase", value.get("phase")) in {
            "synthesis",
            "conclusion_review",
        } and value.get("status") in {"completed", "abstained", "review_required"}:
            self._retain_scientific_synthesis(record, value)

    def _retain_scientific_synthesis(self, record, value):
        scientific = self.app.scientific
        plan = scientific._plan(record["scientific_session_id"])
        assessment = scientific.planner().evidence_assessment(
            record["scientific_session_id"]
        )
        trial_rows = [
            {
                key: result[key]
                for key in (
                    "experiment_id",
                    "candidate",
                    "status",
                    "metric_values",
                    "warnings",
                    "failures",
                    "provenance",
                )
                if key in result
            }
            for result in plan.get("results", [])
        ]
        compact_trials = [
            {
                key: result[key]
                for key in (
                    "experiment_id",
                    "candidate",
                    "status",
                    "metric_values",
                    "warnings",
                    "failures",
                )
                if key in result
            }
            for result in trial_rows
        ]
        synthesis = {
            "supported_findings": {
                "workflow_status": value.get("status"),
                "scientific_qualification": value.get(
                    "scientific_qualification", "not_established"
                ),
                "trial_results": compact_trials,
            },
            "method_evidence_assessment": {
                "source": "Existing governed scientific workflow",
                "status": value.get("status"),
                "comparison_evidence": assessment,
            },
            "uncertainties": self._scientific_uncertainties(plan, assessment),
            "evidence": {"governed_outputs": _copy(value), "trial_results": trial_rows},
        }
        if record.get("singlecell_trial"):
            synthesis["population_evidence"] = self._population_conclusion(
                record, trial_rows
            )
        recommendation_selection = next(
            (
                row
                for row in reversed(plan.get("selection_history", []))
                if row.get("phase") == "method_recommendation"
            ),
            None,
        )
        if recommendation_selection:
            usage = (
                recommendation_selection.get("model_usage")
                or recommendation_selection.get("evidence")
                or {}
            )
            synthesis["evidence"]["recommendation_model_usage"] = {
                key: usage[key]
                for key in (
                    "normalized_total_tokens",
                    "model_sessions_attempted",
                    "tokens",
                    "actual_model",
                    "cost_usd",
                )
                if key in usage
            }
        record["synthesis"] = synthesis
        self._turn(
            record, "synthesis", "Scientific synthesis and review", synthesis, step=5
        )

    def _project_scientific_failure(self, record, operation, evidence_key):
        value = operation.get("value") or {}
        phase = value.get("phase") or operation.get("action") or "scientific_workflow"
        reason = str(
            value.get("reason")
            or operation.get("error")
            or "The recorded operation requires inspection before continuing."
        )[:2000]
        isolation_failure = phase in {
            "candidate_review",
            "design_review",
            "conclusion_review",
        } and reason.endswith(
            "ValueError: Reviewer selector private storage must match its fresh run"
        )
        failure = {
            "type": "ScientificWorkflowBlocked",
            "source": "retained_scientific_operation",
            "message": "Scientific workflow stopped at "
            + phase.replace("_", " ")
            + ". Review the retained diagnostic before continuing.",
            "technical_details": reason,
            "phase": phase,
            "operation_id": operation["operation_id"],
            "required_action": value.get("required_action", "inspect_and_reconcile"),
            "evidence_key": evidence_key,
        }
        if isolation_failure:
            failure.update(
                type="ReviewWorkspaceIsolation",
                message="Scientific review could not start because the review "
                "workspace isolation setup needs repair. No review model call was "
                "started for this phase.",
                review_model_sessions_attempted=0,
                numerical_fit_started=False
                if phase in {"candidate_review", "design_review"}
                else None,
            )
            if phase in {"candidate_review", "design_review"}:
                failure["message"] += " No inference fit has run."
        record["error"] = _copy(failure)
        self._turn(
            record,
            "error",
            "Scientific workflow needs attention",
            failure,
            step=5 if phase == "conclusion_review" else 3,
        )

    def _sync_singlecell_evidence(self, record):
        from .workbench_singlecell import read_singlecell_run, scoped_singlecell_runs

        scientific = self.app.scientific
        plan = scientific._plan(record["scientific_session_id"])
        methods = scientific.methods()
        seen = {
            turn["content"].get("event_key")
            for turn in record["turns"]
            if turn["kind"] == "singlecell_evidence"
        }
        for identity in scoped_singlecell_runs(methods, plan):
            try:
                run = methods.get("run", identity)
            except ValueError:
                continue  # Persisted child intent precedes its method-run row.
            key = digest(
                {
                    "run_id": identity,
                    "status": run["status"],
                    "outputs": run.get("outputs"),
                    "error": run.get("error"),
                }
            )
            preview_id = (run.get("outputs") or {}).get("summary") or (
                run.get("outputs") or {}
            ).get("result")
            cached = next(
                (
                    item
                    for item in record["artifacts"]
                    if item.get("artifact_id") == preview_id
                ),
                None,
            )
            if cached is not None:
                cached["download_status"] = "restricted_audit_entitlement_required"
            refresh_availability = run["status"] == "succeeded" and (
                cached is None or "plot_availability" not in cached
            )
            if key in seen and not refresh_availability:
                continue
            content = read_singlecell_run(
                methods, run, record["singlecell_trial"], self._binding(record)
            )
            content["event_key"] = key
            self._retain_singlecell_artifacts(record, content)
            stage = content["tool_name"].removeprefix("scrna_population_")
            if key not in seen:
                self._turn(
                    record,
                    "singlecell_evidence",
                    "Single-cell " + stage + " evidence",
                    content,
                    step=1 if stage == "audit" else 3 if stage == "design" else 4,
                )

    @staticmethod
    def _retain_singlecell_artifacts(record, content):
        for artifact in content["artifact_refs"]:
            artifact["download_url"] = (
                "/api/scientific/artifacts/" + artifact["artifact_id"]
            )
            suffix = artifact.get("suffix", ".json")
            content_type = {
                ".h5": "application/x-hdf5",
                ".parquet": "application/vnd.apache.parquet",
            }.get(
                suffix,
                "application/json",
            )
            descriptor = {
                **artifact,
                "name": content["tool_name"]
                + " · "
                + artifact["artifact_id"][:12]
                + suffix,
                "download_path": artifact["download_url"],
                "content_type": content_type,
                "download_status": "restricted_audit_entitlement_required",
                "scientific_session_id": record["scientific_session_id"],
            }
            retained = next(
                (
                    item
                    for item in record["artifacts"]
                    if item.get("artifact_id") == artifact["artifact_id"]
                ),
                None,
            )
            if retained is None:
                record["artifacts"].append(descriptor)
            elif artifact.get("plot_availability") is not None:
                if retained.get("sha256") != artifact["sha256"]:
                    raise ValueError(
                        "Retained artifact descriptor changed before plot availability "
                        "refresh"
                    )
                retained["plot_availability"] = _copy(artifact["plot_availability"])
            if retained is not None:
                retained["download_status"] = descriptor["download_status"]

    def _bind_scientific_artifacts(self, record, value):
        published = record.setdefault("scientific_results", [])
        unavailable = record.setdefault("artifact_unavailable", [])
        for result in value["results"]:
            reference = result.get("ref")
            try:
                resolved = self._resolve_scientific_result(record, value, reference)
                metadata = {
                    "ref": reference,
                    "run_id": resolved["run_id"],
                    "attempt_id": resolved["attempt_id"],
                    "validation_id": resolved["validation_id"],
                    "review_status": resolved["review"].get(
                        "review_status", "candidate"
                    ),
                }
                if not any(row["ref"] == reference for row in published):
                    published.append(metadata)
                for identity in resolved["artifact_ids"]:
                    try:
                        self._retain_scientific_artifact(
                            record, resolved, reference, identity
                        )
                    except (ValueError, OSError, KeyError) as error:
                        item = {
                            "scientific_ref": reference,
                            "artifact_id": identity,
                            "reason": str(error)[:2000],
                        }
                        if item not in unavailable:
                            unavailable.append(item)
            except (ValueError, OSError, KeyError) as error:
                item = {"scientific_ref": reference, "reason": str(error)[:2000]}
                if item not in unavailable:
                    unavailable.append(item)

    def _resolve_scientific_result(self, record, value, reference):
        scientific = self.app.scientific
        resolved = scientific.resolve(reference)
        if "withheld" in {resolved.get("status"), resolved.get("publication_status")}:
            raise PermissionError("publication_source_withheld")
        # The run trigger binds the join. Expose only projected result metadata.
        run = scientific.store().get("run", resolved["run_id"])
        request = run["trigger"]["request"]
        if run["trigger"].get("kind") == "run_trial":
            if request.get("session_id") != record["scientific_session_id"]:
                raise ValueError(
                    "Published result belongs to another scientific planner session"
                )
        elif any(
            request.get(key) != record["scientific_context"].get(key)
            for key in ("analysis_ref", "dataset_ref")
        ):
            raise ValueError(
                "Published rerun result differs from this exact dataset/analysis "
                "binding"
            )
        if value.get("run_id") != resolved["run_id"]:
            raise ValueError(
                "Published result differs from its completed scientific invocation"
            )
        return resolved

    def _retain_scientific_artifact(self, record, resolved, reference, identity):
        scientific = self.app.scientific
        _, content_type, filename = scientific._linked_artifact(identity)
        artifact = scientific.methods().get("artifact", identity)
        descriptor = {
            "artifact_id": identity,
            "name": filename,
            "content_type": content_type,
            **{
                key: artifact[key]
                for key in ("artifact_type", "size_bytes", "sha256", "suffix")
            },
            "scientific_ref": reference,
            "scientific_session_id": record["scientific_session_id"],
            "scientific_run_id": resolved["run_id"],
            "download_path": "/api/scientific/artifacts/" + identity,
            "download_status": "restricted_audit_entitlement_required",
        }
        saved_view = next(
            (
                item
                for item in record["artifacts"]
                if item.get("artifact_id") == identity
                and item.get("sha256") == artifact["sha256"]
                and item.get("plot_availability") is not None
            ),
            None,
        )
        if saved_view is not None:
            descriptor["plot_availability"] = _copy(saved_view["plot_availability"])
        existing = next(
            (
                row
                for row in record["artifacts"]
                if row.get("artifact_id") == identity
                and row.get("scientific_ref") == reference
            ),
            None,
        )
        if existing is None:
            record["artifacts"].append(descriptor)
        else:
            existing["download_status"] = descriptor["download_status"]

    def _population_trial_previews(self, record, trials):
        from devbench.study.evidence import _read, decode_json

        from .workbench_singlecell import (
            read_singlecell_run,
            unavailable_population_estimates,
        )

        study = record["singlecell_trial"]
        methods = self.app.scientific.methods()
        results = []
        for trial in trials:
            run = methods.get("run", trial["provenance"]["run_id"])
            view = read_singlecell_run(methods, run, study, self._binding(record))
            if (
                view["tool_name"] != "scrna_population_contrast"
                or view["status"] != "completed"
            ):
                continue
            summary = decode_json(
                _read(methods.artifact_path(run["outputs"]["summary"]), 256_000)
            )
            design = summary.get("design") or {}
            diagnostics = design.get("diagnostics") or {}
            result_id = run["outputs"]["result"]
            unavailable = unavailable_population_estimates(methods, result_id, summary)
            results.append(
                {
                    "trial": trial["experiment_id"],
                    "summary": view["summary"],
                    "tables": view["tables"],
                    "stage_summaries": (summary.get("summary") or {}).get(
                        "stage_summaries"
                    )
                    or [],
                    "testing_family": summary.get("testing_family"),
                    "unavailable_estimates": unavailable,
                    "design_diagnostics": {
                        key: diagnostics[key]
                        for key in (
                            "raw_condition_number",
                            "standardized_condition_number",
                            "condition_number_limit",
                            "rank",
                            "residual_df",
                            "design_condition_policy",
                        )
                        if key in diagnostics
                    },
                    "parameter_origin": design.get("parameter_origin"),
                    "result_artifact_id": result_id,
                    "plot_capabilities": next(
                        (
                            ref["plot_availability"]["capabilities"]
                            for ref in view["artifact_refs"]
                            if "plot_availability" in ref
                        ),
                        [],
                    ),
                    "limitations": view["limitations"],
                }
            )
        return results

    def _population_conclusion(self, record, trials):
        specification = record["singlecell_trial"]["population_specification"]
        results = self._population_trial_previews(record, trials)
        if not results:
            return None

        stages = specification["stage_levels"]
        populations = specification["populations"]
        unit = specification["biological_unit_column"]
        # Positive coefficients are for the second supplied population.
        comparison = f"{populations[1]['label']} versus {populations[0]['label']}"
        findings, answers = [], []
        for result in results:
            stage_answers = []
            for stage in result["stage_summaries"]:
                label = stage["stage"]
                estimated = stage["estimated_n"]
                detected = stage["q_below_0_05_n"]
                if not estimated:
                    observation = "No gene estimates were available."
                else:
                    observation = (
                        f"{detected} of {estimated} estimated genes had BH q < 0.05."
                    )
                stage_answers.append(f"{label}: {observation.rstrip('.')}")
                leaders = [row for row in stage["top_genes"] if row["q_value"] < 0.05][
                    :2
                ]
                if leaders:
                    effects = ", ".join(
                        f"{row['gene_id']} (log₂FC {row['log2_fold_change']:+.2f}, "
                        f"BH q {row['q_value']:.2e})"
                        for row in leaders
                    )
                    observation += " Leading estimates: " + effects + "."
                findings.append(
                    {
                        "title": (
                            f"{label} · {detected} genes below the exploratory q "
                            "threshold"
                            if estimated
                            else f"{label} · Estimates unavailable"
                        ),
                        "stage": label,
                        "observation": observation,
                        "meaning": f"Fold changes compare {comparison} within {label}.",
                        "figure_link": f"↗ {label} Volcano",
                    }
                )
            units = result["summary"].get("declared_biological_unit_count")
            scope = (
                f" with blocking across {units} declared units ({unit})"
                if units
                else f" with blocking by {unit}"
            )
            if stage_answers:
                answers.append(
                    f"The fitted contrast compared {comparison}{scope}. "
                    + "; ".join(stage_answers)
                    + "."
                )
        cross_stage = len(stages) > 1
        limits = [
            "The fit uses supplied population labels and declared biological units. "
            "A single procedure does not establish comparative method performance or "
            "clinical validity."
        ]
        if cross_stage:
            findings.append(
                {
                    "title": f"Cross-stage shift ({' → '.join(stages)}) is untested",
                    "observation": "The embryo-blocked model estimated a population "
                    "contrast within each stage. The fit report contains no test "
                    "comparing those coefficients across stages.",
                    "meaning": "A direct contrast between the stage-specific "
                    "population coefficients would test whether the expression "
                    "difference changes across stages.",
                    "figure_link": None,
                }
            )
            limits.insert(
                0,
                "A claim about developmental change requires a direct comparison of "
                "the stage-specific population coefficients.",
            )
        return {
            "question": specification["question"],
            "requirement_source": specification["requirement_source"],
            "procedure": specification["procedure"],
            "choice_origin": specification["choice_origin"],
            "biological_unit_column": unit,
            "measurement": specification["measurement"],
            "declared_stages": stages,
            "trials": results,
            "direct_answer": " ".join(answers)
            if len(answers) == 1
            else "Separate fitted trials are retained below."
            if answers
            else None,
            "structured_findings": findings,
            "execution_status": "Fit complete",
            "answer_completeness": "Stage interaction untested"
            if cross_stage
            else "Within-stage estimates available"
            if answers
            else "Summary unavailable",
            "scope_limits": limits,
            "unanswered_parts": ["Stage-by-population interaction"]
            if cross_stage
            else [],
            "next_step": (
                "Directly compare the stage-specific population coefficients for "
                f"{comparison} across {' and '.join(stages)}."
                if cross_stage and answers
                else None
            ),
        }

    def get(self, identity):
        with self._lock:
            record = self._load(identity)
            before = digest(record)
            try:
                self._sync_job(record)
                self._sync_scientific(record)
            except OperationBlocked as error:
                if error.state != "ledger_busy":
                    self._failure(record, error)
            except (
                ValueError,
                PermissionError,
                RuntimeError,
                OSError,
                KeyError,
            ) as error:
                if record.get("error") != {
                    "type": type(error).__name__,
                    "message": str(error)[:2000],
                }:
                    self._failure(record, error)
            if (
                not record.get("job_id")
                and not record["scientific_operations"]
                and record["status"] in {"inspecting", "queued", "cancelling"}
                and identity not in self._description_controls
            ):
                record["status"] = "recovery_required"
                record["operation_pending"] = False
                record["required_action"] = (
                    "Review the interrupted input operation; nothing was restarted."
                )
                if record.get("description_operation"):
                    self._description_lifecycle(record, "recovery_required")
            if digest(record) != before:
                self._save(record)
            return self._response(record)

    def events(self, identity, since=0):
        with self._lock:
            record = self._load(identity)
            try:
                self._sync_job(record)
                self._sync_scientific(record)
            except Exception:
                pass
            return _investigation_event_page(record, since)

    def action(self, identity, body, *, defer_description=False, project_id=None):
        if not isinstance(body, dict) or type(body.get("expected_revision")) is not int:
            raise ValueError(
                "Investigation actions require an integer expected_revision"
            )
        with self._lock:
            record = self._load(identity)
            self._project_id(project_id)
            name = body.get("action")
            if name == "continue_synthesis":
                operation = self._enqueue_synthesis(record, body)
                return {
                    **self.get(identity),
                    "synthesis_operation_id": operation["operation_id"],
                }
            if body["expected_revision"] != record["revision"]:
                raise RuntimeError(
                    "Investigation changed; refresh before applying this action"
                )
            self._validate_action_fields(record, name, body)
            if identity in self._description_controls:
                if name != "cancel":
                    raise RuntimeError(
                        "Wait for or cancel the pending descriptive operation "
                        "before another action"
                    )
                return self._cancel_description(
                    record, self._description_controls[identity]
                )
            result = self._apply_action(
                record, body, defer_description=defer_description
            )
            if result is not None:
                return result
            self._sync_action_state(record, name)
            self._save(record)
            return self._response(record)

    def _validate_action_fields(self, record, name, body):
        fields = {
            "clarify": {
                "declarations",
                "source_declarations",
                "question",
                "user_parameter_requirements",
                "scientific_context",
            },
            "review": {"reviewer", "decision", "rationale", "confirmations"},
            "run": {"allow_data_execution"},
            "follow_up": {
                "question",
                "selection",
                "proposal_mode",
                "user_parameter_requirements",
                "scientific_context",
            },
            "scientific": {"request", "operator_review"},
        }
        if record.get("description_operation"):
            fields["cancel"] = set()
        if record.get("scientific_session_id") and (
            record.get("singlecell_trial")
            or name == "cancel"
            and self._synthesis_descriptor(record)
        ):
            fields["cancel"] = set()
        if record.get("analyst_profile") == "analyst_v1":
            fields.update(
                run={"allow_data_execution", "allow_model_execution"},
                cancel=set(),
                resume={"response", "allow_data_execution", "allow_model_execution"},
                clarify_episode={
                    "response",
                    "allow_data_execution",
                    "allow_model_execution",
                },
            )
        if name not in fields or set(body) - (
            {"action", "expected_revision"} | fields[name]
        ):
            raise ValueError(
                "Use only the declared fields for this investigation action"
            )

    def _apply_action(self, record, body, *, defer_description):
        match body["action"]:
            case "clarify":
                return self._clarify_investigation(
                    record, body, defer_description=defer_description
                )
            case "follow_up":
                return self._follow_up(
                    record, body, defer_description=defer_description
                )
            case "review":
                self._review_native_plan(record, body)
            case "run":
                self._run_native_plan(record, body)
            case "scientific":
                self._scientific(
                    record, body.get("request"), body.get("operator_review")
                )
            case "cancel":
                return self._cancel_investigation(record)
            case "resume" | "clarify_episode":
                return self._resume_episode(record, body)
        return None

    def _cancel_description(self, record, control):
        control["stop"].set()
        cancelled = control["future"].cancel()
        record["status"] = "cancelled" if cancelled else "cancelling"
        self._description_lifecycle(
            record, "cancelled" if cancelled else "cancel_requested"
        )
        if cancelled:
            record["description_operation"]["worker_cleanup_verified"] = True
            self._description_controls.pop(record["id"], None)
        self._save(record)
        return self._response(record)

    def _cancel_investigation(self, record):
        if record.get("scientific_session_id"):
            from devbench.engine.workflow_operations import WorkflowOperationLedger

            from .workflow_entrypoints import LEDGER_NAME

            ledger = WorkflowOperationLedger(self.app.scientific.root / LEDGER_NAME)
            ledger.cancel(record["scientific_session_id"])
            record["scientific_cancel_requested"] = True
            record["status"] = (
                "cancelling" if record.get("operation_pending") else "cancelled"
            )
            self._turn(
                record,
                "execution_request",
                "Scientific workflow stop requested",
                {
                    "message": (
                        "The existing graph cancellation ledger is set. "
                        "Active supervised work must stop and verify cleanup; "
                        "no later graph phase may dispatch."
                    ),
                    "session_id": record["scientific_session_id"],
                    "status": record["status"],
                },
                step=4,
                role="user",
            )
            self._save(record)
            return self._response(record)
        if not record.get("job_id"):
            raise RuntimeError("No active descriptive operation can be cancelled")
        result = self.app.cancel(record["job_id"])
        record["status"] = result["status"]
        self._turn(
            record,
            "execution_request",
            "Episode stop requested",
            result,
            step=4,
            role="user",
        )
        return None

    def _follow_up(self, record, body, *, defer_description):
        if record.get("profile") is None:
            raise RuntimeError(
                "A linked follow-up requires a verified parent source identity"
            )
        context, inheritance = self._follow_up_context(
            record, body.get("scientific_context", {})
        )
        followup = {
            "question": body.get("question"),
            "source": record["source"],
            "expected_sha256": record["profile"]["sha256"],
            "declarations": record["declarations"],
            "proposal_mode": body.get("proposal_mode", record["proposal_mode"]),
            "parent_job": record.get("job_id") or record.get("parent_job"),
            "parent_investigation": record["id"],
            "selection": body.get("selection"),
            "user_parameter_requirements": body.get("user_parameter_requirements", []),
            "scientific_context": context,
        }
        if record.get("sources"):
            for name in ("source", "declarations", "expected_sha256"):
                followup.pop(name)
            followup.update(
                sources=_copy(record["sources"]),
                source_declarations=_copy(record["source_declarations"]),
                expected_sha256s=[
                    profile["sha256"] for profile in record["source_profiles"]
                ],
            )
        if record.get("analyst_profile") == "analyst_v1":
            followup["analyst_profile"] = record["analyst_profile"]
        return self.create(
            followup,
            context_inheritance=inheritance,
            defer_description=defer_description,
            project_id=record.get("project_id"),
        )

    def _clarify_investigation(self, record, body, *, defer_description):
        if record.get("job_id") or record["status"] not in {
            "awaiting_information",
            "operation_failed",
            "recovery_required",
        }:
            raise RuntimeError(
                "Clarification can only revise an unbound investigation. "
                "Use a new follow-up for retained plans."
            )
        question = _text(
            body.get("question", record["question"]), "revised biological question"
        )
        source_answers = body.get("source_declarations", [])
        fields = record.get("clarification_fields", ())
        if (
            record["status"] == "awaiting_information"
            and fields
            and all(field.get("scope") == "request" for field in fields)
            and question == record["question"]
            and body.get("declarations", {}) == {}
            and body.get("scientific_context", {}) == {}
            and "user_parameter_requirements" not in body
            and source_answers in ([], [{} for _ in record.get("sources", [])])
        ):
            return self._response(record)
        if record["source"] is None:
            self._revise_question_and_context(record, body, question)
            self._turn(
                record, "user_question", "Your revised question",
                {"question": record["question"]}, step=2, role="user",
            )
            self._prepare_without_data(record)
            return None
        if defer_description and classify_request(question)["descriptive_topics"]:
            return self._defer_descriptive_clarification(record, body)
        supplied, source_answers = self._clarify_declarations(record, body)
        self._revise_question_and_context(record, body, question)
        content = {
            "declarations": supplied,
            "question": record["question"],
            "user_parameter_requirements": record["user_parameter_requirements"],
        }
        if source_answers is not None:
            content["source_declarations"] = _copy(source_answers)
        if supplied or any(source_answers or ()) or (
            "scientific_context" in body or "user_parameter_requirements" in body
        ):
            self._turn(
                record, "clarification_answer", "Input declarations recorded",
                content, step=1, role="user",
            )
        elif "question" in body:
            self._turn(
                record, "user_question", "Your revised question",
                {"question": record["question"]}, step=2, role="user",
            )
        describing = bool(record.get("request", {}).get("descriptive_topics"))
        if describing:
            self._begin_description_attempt(record, deferred=False)
        try:
            self._prepare(record)
        except (ValueError, PermissionError, RuntimeError, OSError, KeyError) as error:
            self._failure(record, error)
        if describing:
            self._description_lifecycle(
                record,
                "failed" if record["status"] == "operation_failed" else "completed",
            )
        return None

    def _clarify_declarations(self, record, body):
        if record.get("sources"):
            if body.get("declarations"):
                raise ValueError("Clarify each attached source independently")
            self._inspect_source_set(record, record.get("expected_source_sha256s"))
            source_answers = self._source_declaration_answers(record, body)
            from devbench.analysis.rna_input_set_contract import (
                validate_source_declarations,
            )

            record["source_declarations"] = [
                validate_source_declarations({**old, **answer}, profile["observed"])
                for old, answer, profile in zip(
                    record["source_declarations"],
                    source_answers,
                    record["source_profiles"],
                    strict=True,
                )
            ]
            return {}, source_answers
        if "source_declarations" in body:
            raise ValueError("Use legacy declarations for a single dataset")
        refreshed = _copy(self.inputs.inspect(record["source"]))
        expected = (record.get("profile") or {}).get("sha256") or record.get(
            "expected_source_sha256"
        )
        if expected is not None and refreshed["sha256"] != expected:
            raise ValueError(
                "Input changed; start a new question against its new source version"
            )
        record["profile"] = refreshed
        supplied = self._declarations(
            body.get("declarations", {}), refreshed, partial=True
        )
        record["declarations"].update(supplied)
        return supplied, None

    @staticmethod
    def _source_declaration_answers(record, body):
        answers = body.get("source_declarations", [{} for _ in record["sources"]])
        if type(answers) is not list or len(answers) != len(record["sources"]):
            raise ValueError(
                "Supply one partial declaration record per attached source"
            )
        if any(type(answer) is not dict for answer in answers):
            raise ValueError("Per-source answers must be declaration objects")
        return answers

    def _revise_question_and_context(self, record, body, question, *, reclassify=False):
        question, pins = split_pinned_context(question)
        if "question" in body or reclassify:
            if question != record["question"]:
                record["question_id"] = uuid.uuid4().hex
            record.update(
                question=question,
                intent=classify_question(question),
                request=classify_request(question),
            )
        if "question" in body:
            record.pop("pinned_context", None)
            if pins:
                record["pinned_context"] = pins
        if "question" in body or "user_parameter_requirements" in body:
            record["user_parameter_requirements"] = question_requirements(
                question,
                body.get("user_parameter_requirements"),
                self.app.parameters,
            )
        if "scientific_context" in body:
            updates = _context(body["scientific_context"])
            if "bulk_episode_declarations" in updates:
                updates["bulk_episode_declarations"] = {
                    **record["scientific_context"].get("bulk_episode_declarations", {}),
                    **updates["bulk_episode_declarations"],
                }
            record["scientific_context"].update(updates)
            self._remember_campaign_grant(record, explicit_fields=updates)

    def _review_native_plan(self, record, body):
        if not record.get("job_id"):
            raise RuntimeError(
                "An existing native plan is required before operator review"
            )
        request = {
            key: body.get(key)
            for key in ("reviewer", "decision", "rationale", "confirmations")
        }
        controller = (
            self.app.agentic_episodes
            if record.get("analyst_profile") == "analyst_v1"
            else self.app
        )
        result = controller.review(record["job_id"], request)
        record["status"] = result["status"]
        self._turn(
            record,
            "plan_review",
            "Operator decision submitted",
            {
                "job_id": record["job_id"],
                "review": request,
                "result": result,
            },
            step=3,
            role="operator",
        )

    def _run_native_plan(self, record, body):
        if body.get("allow_data_execution") is not True or not record.get("job_id"):
            raise PermissionError(
                "Execution requires an explicit per-run request "
                "and a reviewed native plan"
            )
        if record.get("analyst_profile") == "analyst_v1":
            result = self.app.agentic_episodes.run(record["job_id"], body)
        else:
            result = self.app.run(record["job_id"])
        record["status"] = result["status"]
        self._turn(
            record,
            "execution_request",
            "Controlled execution requested",
            {
                "job_id": record["job_id"],
                "result": result,
                "allow_data_execution": True,
                "scope": (
                    "This recorded request only. Original startup permission and "
                    "operator "
                    "approval remain required."
                ),
            },
            step=4,
            role="operator",
        )

    def _resume_episode(self, record, body):
        state = self.app.agentic_episodes.status(record["job_id"])
        if (
            body["action"] == "clarify_episode"
            and not state.get("handoff")
            and state["status"] == "awaiting_information"
            and state.get("information_request")
            and not state.get("operation_pending")
            and not state.get("proposal_receipt")
        ):
            return self._start_clarified_episode(record, body)
        result = self.app.agentic_episodes.run(
            record["job_id"], body, response=body.get("response")
        )
        record["status"] = result["status"]
        self._turn(
            record,
            "clarification_answer",
            "Episode clarification submitted",
            {"job_id": record["job_id"], "response": body.get("response")},
            step=4,
            role="user",
        )
        return None

    def _start_clarified_episode(self, record, body):
        from devbench.analysis.episode_handoff import validate_clarification

        response = body.get("response")
        validate_clarification(response)
        # Preserve the sealed planning response as a separate linked plan.
        context = {
            key: _copy(value)
            for key, value in record["scientific_context"].items()
            if key in {"bulk_episode_declarations", "data_classification"}
        }
        if body.get("allow_model_execution") is True:
            context["allow_model_execution"] = True
        self._turn(
            record,
            "clarification_answer",
            "Planning clarification supplied",
            {
                "job_id": record["job_id"],
                "response": response,
                "scope": (
                    "New linked investigation. "
                    "The sealed planning response is preserved."
                ),
            },
            step=2,
            role="user",
        )
        self._save(record)
        return self.create(
            {
                "question": record["question"]
                + "\n\nOperator clarification: "
                + response["message"],
                "sources": _copy(record["sources"]),
                "source_declarations": _copy(record["source_declarations"]),
                "expected_sha256s": [
                    profile["sha256"] for profile in record["source_profiles"]
                ],
                "parent_investigation": record["id"],
                "parent_job": record["job_id"],
                "proposal_mode": record["proposal_mode"],
                "analyst_profile": "analyst_v1",
                "user_parameter_requirements": _copy(
                    record["user_parameter_requirements"]
                ),
                "scientific_context": context,
            }
        )

    def _sync_action_state(self, record, name):
        if record.get("analyst_profile") == "analyst_v1" and name in {
            "review",
            "run",
            "cancel",
            "resume",
            "clarify_episode",
        }:
            # Read authoritative handoff state after a cancellation acknowledgement.
            record["job_state"] = _copy(
                self.app.agentic_episodes.status(record["job_id"])
            )
            record["status"] = record["job_state"]["status"]

    def _follow_up_context(self, parent, supplied):
        """Reuse immutable data context, never a question, approval or grant."""
        supplied = _context(supplied)
        inherited = self._inheritable_data_context(parent)
        if (
            not inherited.get("study_id")
            and parent.get("scientific_session_id")
            and self.app.scientific is not None
        ):
            plan = self.app.scientific._plan(parent["scientific_session_id"])
            study = plan.get("scientific_anchors", {}).get("study_id")
            if study:
                inherited["study_id"] = study
        context = _context({**inherited, **supplied})
        attribution = {
            "source": "retained_parent_data_bindings",
            "parent_investigation": parent["id"],
            "inherited_fields": sorted(set(inherited) - set(supplied)),
            "overridden_fields": sorted(set(inherited) & set(supplied)),
            "explicit_request_fields": sorted(supplied),
            "scope": "Retained data context; the new question and method requirements "
            "remain separately attributed. Existing reviews and execution permissions "
            "retain their original scopes.",
        }
        if parent.get("workflow_mode") == "adaptive/1":
            attribution.update(retained_workflow_session_id=parent["scientific_session_id"])
        return context, attribution

    @staticmethod
    def _inheritable_data_context(parent):
        previous = parent["scientific_context"]
        plan_request = previous.get("plan_request") or {}
        data_fields = {
            "study_id",
            "dataset_ref",
            "rna_input_contract",
            "counts_layer",
            "data_classification",
            "biological_independence",
            "raw_droplet_provenance",
        }
        inherited = {}
        for key in data_fields:
            if key in previous:
                inherited[key] = _copy(previous[key])
            elif key in plan_request:
                inherited[key] = _copy(plan_request[key])
        if (inherited.get("rna_input_contract") or {}).get(
            "format"
        ) == "devbench.scrna-population-input-binding/1":
            # This contract includes a question, protected population predicates
            # and a proposed procedure. A follow-up retains data declarations;
            # only an exact current startup match can bind a fresh study route.
            inherited.pop("rna_input_contract")
        if parent.get("analyst_profile") == "analyst_v1" and previous.get(
            "bulk_episode_declarations"
        ):
            # Reuse declared data identity and design. Leave prior contrasts and grants.
            inherited["bulk_episode_declarations"] = {
                key: _copy(value)
                for key, value in previous["bulk_episode_declarations"].items()
                if key not in {"contrast_numerator", "contrast_denominator"}
            }
        binding = inherited.get("rna_input_contract") or {}
        if binding.get("format") == "devbench.scrna-input-binding/1":
            declarations = binding.get("declarations", {})
            for key in ("counts_layer", "biological_independence"):
                if key not in inherited and key in declarations:
                    inherited[key] = _copy(declarations[key])
        return inherited
