"""Durable question threads over existing guarded workbench/scientific actions.

This controller adds context and a read model, not a scientific engine or grant.
Every execution still belongs to the original reviewed controller. Header
observations and name-based suggestions never become biological declarations.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import uuid

from .checkpoints import atomic_json, digest
from .method_planner_contract import validate_planning_metadata


FORMAT = "devbench.investigation/1"
MAX_RECORD_BYTES = 2 * 1024**2
MAX_INVESTIGATIONS = 100
MAX_TURNS = 256
MAX_ANCESTORS = 16
MAX_SELECTION = 1000
DECLARATIONS = {"assay", "measurement", "count_provenance", "capture_unit_key",
                "capture_units_confirmed", "annotation_provenance"}
SCIENTIFIC_CONFIRMATIONS = {
    "question_and_scope": "I reviewed the question and proposed scientific scope.",
    "input_provenance": "I reviewed the exact data version and provenance declarations.",
    "settings_and_limits": "I reviewed the proposed parameters and resource limits.",
    "interpretation_limits": "I reviewed the interpretation and validation limitations.",
    "trial_criteria": "I reviewed the frozen comparison criteria and selected trial."}
INTENTS = {"qc_and_integration", "batch_effect_audit", "method_selection",
           "differential_contrast", "trajectory_or_regulon"}
_IDENTITY = re.compile(r"[0-9a-f]{32}")
_HASH = re.compile(r"[0-9a-f]{64}")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _copy(value):
    encoded = json.dumps(value, allow_nan=False)
    if len(encoded.encode()) > MAX_RECORD_BYTES:
        raise ValueError("Investigation record exceeds its retained size bound")
    return json.loads(encoded)


def _text(value, name, limit=8000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError("Use a bounded nonempty " + name)
    return value.strip()


def _identity(value, name="investigation identity"):
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise ValueError("Use an exact " + name)
    return value


def classify_question(question):
    """A transparent local routing inference; never a user method requirement."""
    text = question.lower()
    if re.search(r"best method|method selection|compare methods|which method|choose.*method", text):
        kind = "method_selection"
    elif re.search(r"pseudotime|trajectory|lineage|regulon|regulatory network|motif", text):
        kind = "trajectory_or_regulon"
    elif (re.search(r"differential|fold.change|contrast|expression changes|gene expression between", text)
          or re.search(r"compar|between|versus|\bvs\b|difference|changes", text)
          and re.search(r"gene expression|expression|composition|populations|abundance|cell.types?", text)):
        kind = "differential_contrast"
    elif re.search(r"causal|causality|causation|cause.*(?:effect|change)|causes|caused", text):
        kind = "method_selection"
    elif re.search(r"batch|integration|integrate", text):
        kind = "batch_effect_audit"
    elif re.search(r"\bqc\b|quality|mitochond|cluster|annotation|annotate|umap|cell.type", text):
        kind = "qc_and_integration"
    else:
        kind = "method_selection"
    return {"kind": kind, "source": "local_question_classification", "inferred": True,
            "requires_confirmation": True,
            "basis": "Question text routing only; method selection and scientific assumptions require review."}


def _context(value):
    allowed = {"data_classification", "allow_model_execution", "auto_advance", "auto_synthesis", "study_id", "dataset_ref", "analysis_ref", "session_id",
        "plan_request", "advance_request", "rna_input_contract", "rna_execution_inputs_ref", "rna_execution_workspace", "scientific_contract",
        "counts_layer", "biological_independence", "selection_scope"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("Use declared structured scientific-context fields")
    if value.get("data_classification", "unclassified") not in {"unclassified", "synthetic", "public", "private"}:
        raise ValueError("Use a supported explicit input classification")
    for key in ("allow_model_execution", "auto_advance", "auto_synthesis"):
        if key in value and type(value[key]) is not bool:
            raise ValueError("Scientific campaign grants require explicit booleans")
    for key in ("dataset_ref", "analysis_ref", "plan_request", "advance_request", "rna_input_contract", "rna_execution_inputs_ref", "scientific_contract", "biological_independence"):
        if key == "biological_independence" and value.get(key) is None:
            continue
        if key in value and not isinstance(value[key], dict):
            raise ValueError("Use structured scientific version/binding references")
    if "counts_layer" in value:
        _text(value["counts_layer"], "explicit measurement layer", 200)
    if value.get("selection_scope", "saved_evidence_only") not in {"saved_evidence_only", "context_only_full_dataset"}:
        raise ValueError("Use saved evidence or an explicitly confirmed full-dataset selection scope")
    return _copy(value)


def question_requirements(question, supplied, parameters):
    """Retain literal user assignments; defaults never become requirements."""
    values = validate_planning_metadata(user_parameter_requirements=supplied)["user_parameter_requirements"]
    for item in values:
        if item["evidence"] not in question:
            raise ValueError("Parameter requirement evidence must match the submitted question exactly")
    by_name = {item["name"]: item for item in values}
    names = "|".join(re.escape(key) for key in sorted(parameters, key=len, reverse=True))
    value_pattern = r"(\"[^\"\n]{1,200}\"|'[^'\n]{1,200}'|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|[A-Za-z_][A-Za-z0-9_-]{0,199})"
    matches = list(re.finditer(r"\b(" + names + r")\s*[:=]\s*" + value_pattern, question, re.I))
    numeric_names = "|".join(re.escape(key) for key in parameters if "_" in key and type(parameters[key]) in (int, float))
    if numeric_names:
        matches += list(re.finditer(r"\b(" + numeric_names + r")\s+(?:of\s+|to\s+)?([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)", question, re.I))
    for match in matches:
        name, raw = match.group(1).lower(), match.group(2)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw.strip("'\"")
        requirement = {"name": name, "value": value, "evidence": match.group(0)}
        if name in by_name and by_name[name]["value"] != value:
            raise ValueError("Conflicting explicit parameter requirements in this question")
        by_name.setdefault(name, requirement)
    return validate_planning_metadata(user_parameter_requirements=list(by_name.values()))["user_parameter_requirements"]


def _grant_fields(*, advance=False):
    fields = [
        {"field": "data_classification", "scope": "scientific_context", "label": "Dataset sharing scope", "type": "select", "required": True,
         "options": [{"value": value, "label": value} for value in ("unclassified", "synthetic", "public", "private")],
         "reason": "File location and shape do not establish publicness. Private and unclassified inputs remain local."},
        {"field": "allow_model_execution", "scope": "scientific_context", "label": "Allow model-assisted planning for this campaign", "type": "boolean", "required": False,
         "options": [], "reason": "Requires a public/synthetic declaration and separately enabled matching server model scope."}]
    if advance:
        fields += [
            {"field": "auto_advance", "scope": "scientific_context", "label": "Advance planning to the next review gate", "type": "boolean", "required": False,
             "options": [], "reason": "Runs existing planning nodes; trial and execution review remain mandatory."},
            {"field": "auto_synthesis", "scope": "scientific_context", "label": "Advance verified results through recommendation and conclusion review", "type": "boolean", "required": False,
             "options": [], "reason": "Separate post-execution grant; only runs after all controlled trials are complete and verified."}]
    return fields


class InvestigationService:
    """One local, revision-checked thread owner using the service's permissions."""

    def __init__(self, app, inputs):
        self.app, self.inputs = app, inputs
        self.root = app._path("investigations")
        self._lock = threading.RLock()
        # An old checkpoint cannot renew external campaign consent after restart.
        self._campaign_grants = {}
        if self.root.exists():
            if self.root.is_symlink() or not self.root.is_dir():
                raise ValueError("Investigation storage must be a regular protected directory")
            paths = list(self.root.iterdir())
            if len(paths) > MAX_INVESTIGATIONS or any(not _IDENTITY.fullmatch(p.stem) or p.suffix != ".json" for p in paths):
                raise ValueError("Invalid or over-budget retained investigation inventory")
            for path in paths:
                self._load(path.stem)

    def _path(self, identity):
        return self.app._path("investigations", _identity(identity) + ".json")

    def _load(self, identity):
        path = self._path(identity)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError as error:
            raise KeyError("Unknown investigation") from error
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_RECORD_BYTES:
                raise ValueError("Investigation records must be bounded regular files")
            raw = handle.read(MAX_RECORD_BYTES + 1)
        try:
            value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite investigation record")))
        except (json.JSONDecodeError, UnicodeError) as error:
            raise ValueError("Invalid retained investigation record") from error
        if (not isinstance(value, dict) or value.get("format") != FORMAT or value.get("id") != identity
                or type(value.get("revision")) is not int or not 1 <= value["revision"] < 2**53
                or not isinstance(value.get("turns"), list) or len(value["turns"]) > MAX_TURNS
                or not isinstance(value.get("question"), str) or not isinstance(value.get("source"), str)):
            raise ValueError("Invalid retained investigation envelope")
        if (not isinstance(value.get("status"), str) or not isinstance(value.get("declarations"), dict)
                or not isinstance(value.get("scientific_context"), dict) or not isinstance(value.get("scientific_operations"), list)
                or value.get("profile") is not None and not isinstance(value["profile"], dict)):
            raise ValueError("Invalid retained investigation state")
        _identity(value.get("thread_id"), "thread identity")
        if value.get("parent_investigation") is not None:
            _identity(value["parent_investigation"])
        if value.get("job_id") is not None:
            _identity(value["job_id"], "job identity")
        path_source = Path(value["source"])
        if not path_source.is_absolute() or path_source != path_source.resolve() or not any(path_source.is_relative_to(root) for root in self.app.input_roots):
            raise ValueError("Retained investigation input escaped the configured roots")
        if "sources" in value:
            from .rna_input_set_contract import MAX_SOURCES, source_set_identity
            sources = value["sources"]
            if (type(sources) is not list or not 2 <= len(sources) <= MAX_SOURCES
                    or any(type(source) is not str for source in sources) or len(set(sources)) != len(sources)
                    or sources[0] != value["source"] or type(value.get("source_profiles")) is not list
                    or type(value.get("source_declarations")) is not list or len(value["source_declarations"]) != len(sources)
                    or any(type(item) is not dict for item in value["source_declarations"])
                    or any(type(item) is not dict for item in value["source_profiles"])):
                raise ValueError("Invalid retained ordered input set")
            for source in sources:
                path = Path(source)
                if not path.is_absolute() or path != path.resolve() or not any(path.is_relative_to(root) for root in self.app.input_roots):
                    raise ValueError("Retained input-set source escaped the configured roots")
            if value.get("profile") is not None:
                if (len(value["source_profiles"]) != len(sources)
                        or [row.get("source") for row in value["source_profiles"]] != sources
                        or source_set_identity(value["source_profiles"]) != value.get("input_set_sha256")
                        or value["profile"].get("sha256") != value["input_set_sha256"]):
                    raise ValueError("Retained input-set identity does not match every source")
        for turn in value["turns"]:
            if not isinstance(turn, dict) or turn.get("investigation_id") != identity:
                raise ValueError("Invalid retained investigation turn")
            _identity(turn.get("id"), "turn identity")
        return _copy(value)

    def _save(self, record):
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.app._path("investigations")
        record["revision"] += 1
        record["updated_at"] = _now()
        atomic_json(self._path(record["id"]), _copy(record))

    def _turn(self, record, kind, title, content, *, step, role="workbench"):
        if len(record["turns"]) >= MAX_TURNS:
            raise ValueError("Investigation turn limit reached; start a linked follow-up")
        record["turns"].append({"id": uuid.uuid4().hex, "investigation_id": record["id"],
            "kind": kind, "at": _now(), "step": step, "role": role, "title": title,
            "content": _copy(content)})

    def _remember_campaign_grant(self, record):
        context = record["scientific_context"]
        if context.get("auto_synthesis") is True:
            self._campaign_grants[record["id"]] = {key: context.get(key) for key in
                ("auto_synthesis", "allow_model_execution", "data_classification")}
        else:
            self._campaign_grants.pop(record["id"], None)

    def _capture_planning_source(self, record, session_id, capture_event):
        """Snapshot configured source at a real binding/dispatch, not on reads."""
        history = record.setdefault("planning_source_history", [])
        previous = next((row for row in history if row["session_id"] == session_id), None)
        if previous is not None:
            record["planning_source"] = _copy(previous)
            return
        configured = getattr(self.app.scientific, "selector_config", None)
        configured = configured if isinstance(configured, dict) else {}
        keys = {"backend", "model_execution", "requested_model", "model", "reasoning_effort", "version"}
        source = {"source": "configured_scientific_planner_at_dispatch", "capture_event": capture_event,
            "session_id": session_id, "captured_at": _now(),
            "backend": configured.get("backend") if configured.get("backend") in {"recorded_fixture", "codex-cli"} else "unavailable",
            "configuration": {key: _copy(value) for key, value in configured.items() if key in keys},
            "configuration_sha256": digest(configured)}
        history.append(source)
        record["planning_source"] = _copy(source)

    @staticmethod
    def _scientific_uncertainties(plan, assessment):
        rows = _copy(plan["context"]["dataset_summary"].get("unknowns", []))
        seen = {(row.get("field"), row.get("reason", row.get("impact"))) for row in rows}

        def add(field, reason):
            if not isinstance(reason, str) or not reason.strip() or (field, reason) in seen:
                return
            seen.add((field, reason))
            rows.append({"field": field, "reason": reason, "source": "verified_comparison_assessment"})

        for reason in assessment.get("limitations", []):
            add("comparison_evidence", reason)
        for candidate in assessment.get("candidates", []):
            for reason in candidate.get("reasons", []):
                add("comparison_evidence", str(candidate.get("candidate", "Recorded candidate")) + ": " + reason)
        if assessment.get("independent_scientific_validation") is False:
            add("independent_scientific_validation", "Independent scientific validation is not established by these execution and comparison checks.")
        return rows

    def _response(self, record):
        ancestors, seen, current = [], {record["id"]}, record
        while current.get("parent_investigation"):
            parent = current["parent_investigation"]
            if parent in seen or len(ancestors) >= MAX_ANCESTORS:
                raise ValueError("Investigation ancestry is cyclic or exceeds the thread budget")
            current = self._load(parent)
            if current["thread_id"] != record["thread_id"]:
                raise ValueError("Investigation ancestry has a mismatched thread identity")
            seen.add(parent)
            ancestors.append(current)
        turns = [turn for item in reversed(ancestors) for turn in item["turns"]] + record["turns"]
        if len(turns) > MAX_TURNS:
            raise ValueError("Combined thread exceeds its retained turn budget")
        return _copy({**record, "thread_turns": turns})

    def list(self):
        with self._lock:
            summaries = []
            for path in sorted(self.root.glob("*.json")) if self.root.exists() else []:
                record = self._load(path.stem)
                status = record["status"]
                if record.get("job_id"):
                    status = self.app.status(record["job_id"])["status"]
                summaries.append({key: _copy(record.get(key)) for key in ("id", "thread_id", "parent_investigation", "question", "source", "job_id", "updated_at", "revision")})
                summaries[-1].update(status=status, dataset=(record.get("profile") or {}).get("filename", Path(record["source"]).name),
                    cells=(record.get("profile") or {}).get("observed", {}).get("cells"), genes=(record.get("profile") or {}).get("observed", {}).get("genes"))
                if record.get("sources"):
                    summaries[-1].update(sources=_copy(record["sources"]), input_set_sha256=record.get("input_set_sha256"))
            return {"format": "devbench.investigations/1", "investigations": sorted(summaries, key=lambda row: row["updated_at"], reverse=True)}

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
        labels = {"assay": "Assay", "measurement": "Measurement", "count_provenance": "Count provenance",
                  "capture_unit_key": "Capture-unit column", "capture_units_confirmed": "Confirm capture-unit identity",
                  "annotation_provenance": "Annotation provenance or known limitation"}
        fields = []
        fixed_candidate = (observed.get("file_type") in {"h5ad", ".h5ad", "anndata"}
            and supplied.get("assay", "scrna") in {"", "scrna", "snrna"})
        required = ["assay", "measurement", "count_provenance"]
        if fixed_candidate:
            required += ["capture_unit_key", "capture_units_confirmed", "annotation_provenance"]
        for key in required:
            if (supplied.get(key) is True if key == "capture_units_confirmed" else bool(supplied.get(key, "").strip())):
                continue
            choices = {"assay": ["scrna", "snrna", "bulk"], "measurement": ["umi_counts", "normalized_expression", "non_umi_counts"],
                       "capture_unit_key": observed.get("obs_columns", [])}.get(key)
            fields.append({"field": key, "label": labels[key], "type": "boolean" if key == "capture_units_confirmed" else "select" if choices is not None else "text",
                "options": [{"value": item, "label": item} for item in choices or []], "required": True,
                "reason": "Confirm from experimental records; file structure and column names do not establish biological meaning."})
        return fields

    def _inspect_source_set(self, record, expected=None):
        from .rna_input_set_contract import aggregate_profile, validate_source_declarations
        previous = record.get("source_profiles", [])
        profiles = []
        for index, source in enumerate(record["sources"]):
            profile = _copy(self.inputs.inspect(source))
            required_hash = expected[index] if expected else previous[index]["sha256"] if index < len(previous) else None
            if required_hash is not None and profile["sha256"] != required_hash:
                raise ValueError("An input-set source changed; start a new question against its new version")
            profiles.append(profile)
            record["source_profiles"] = _copy(profiles)
        if len({profile["sha256"] for profile in profiles}) != len(profiles):
            raise ValueError("Repeated dataset content cannot be counted as another independent source")
        record["source_declarations"] = [validate_source_declarations(value, profile["observed"])
            for value, profile in zip(record["source_declarations"], profiles)]
        record["profile"] = aggregate_profile(profiles)
        record["input_set_sha256"] = record["profile"]["sha256"]

    def _source_set_clarifications(self, record):
        fields = []
        labels = {"role": "Source role", "assay": "Assay", "measurement": "Measurement",
            "count_provenance": "Measurement provenance", "capture_unit_key": "Capture-unit column",
            "capture_units_confirmed": "Confirm capture-unit identity", "annotation_provenance": "Annotation provenance or known limitation",
            "counts_layer": "Measurement layer"}
        for index, (profile, supplied) in enumerate(zip(record["source_profiles"], record["source_declarations"])):
            observed = profile["observed"]
            required = ["role"]
            if supplied.get("role") == "expression":
                required += ["assay", "measurement", "count_provenance"]
                if observed.get("file_type") == "h5ad":
                    required += ["counts_layer"]
                    if supplied.get("assay") in {"scrna", "snrna"}:
                        required += ["capture_unit_key", "capture_units_confirmed", "annotation_provenance"]
            choices = {"role": ["expression", "metadata", "annotations", "reference", "other"],
                "assay": ["scrna", "snrna", "bulk"], "measurement": ["umi_counts", "normalized_expression", "non_umi_counts"],
                "capture_unit_key": observed.get("obs_columns", []), "counts_layer": ["X", *observed.get("layers", [])]}
            for key in required:
                if (supplied.get(key) is True if key == "capture_units_confirmed" else bool(supplied.get(key, "").strip())):
                    continue
                options = choices.get(key)
                fields.append({"field": key, "scope": "source_declarations", "source_index": index,
                    "source": record["sources"][index], "label": profile["filename"] + " · " + labels[key],
                    "type": "boolean" if key == "capture_units_confirmed" else "select" if options is not None else "text",
                    "options": [{"value": value, "label": value} for value in options or []], "required": True,
                    "reason": "This declaration applies only to this source. File names do not establish assay, alignment or biological independence."})
        return fields

    def _selection(self, selection, parent_job, source, sha256):
        if selection is None:
            return None
        required = {"figure_id", "panel_id", "observation_ids", "source_job", "parent_root", "display_only"}
        if not isinstance(selection, dict) or set(selection) != required or selection["display_only"] is not True:
            raise ValueError("Use an exact display-selection reference")
        source_job = _identity(selection["source_job"], "source job identity")
        if parent_job != source_job:
            raise ValueError("Selection source must match its explicit parent job")
        ids = selection["observation_ids"]
        if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_SELECTION
                or not all(isinstance(item, str) and 0 < len(item) <= 200 and "\x00" not in item for item in ids)
                or len(ids) != len(set(ids)) or len(json.dumps(selection).encode()) > 50000):
            raise ValueError("Select at most 1000 unique bounded observation identities; no selection is truncated")
        state = self.app.status(source_job)
        if state.get("source") != source:
            raise ValueError("Selection input differs from the investigation input")
        figures = self.app.figures(source_job)
        if figures["parent_root"] != selection["parent_root"]:
            raise ValueError("Selection parent receipt does not match the verified source")
        figure = next((item for item in figures["figures"] if item["figure_id"] == selection["figure_id"]), None)
        if not figure or figure.get("analysis_binding", {}).get("input_sha256") != sha256:
            raise ValueError("Selection figure is not bound to this exact input version")
        payload = self._figure_payload(source_job, figure)
        panel = next((item for item in payload["panels"] if item["panel_id"] == selection["panel_id"]), None)
        if panel is None:
            raise ValueError("Selection panel is not present in the verified figure")
        population = {row["observation_id"] for row in panel["rows"] if row.get("status") == "rendered"}
        if not set(ids) <= population:
            raise ValueError("Selection includes identities absent from the saved plotted population")
        return {**_copy(selection), "scope": "context_only_not_a_population_analysis_grant"}

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

    def create(self, body, *, context_inheritance=None):
        allowed = {"question", "source", "sources", "expected_sha256", "expected_sha256s", "declarations", "source_declarations", "proposal_mode", "parent_job", "parent_investigation",
                   "selection", "user_parameter_requirements", "scientific_context"}
        if not isinstance(body, dict) or set(body) - allowed or "question" not in body or not ({"source", "sources"} & body.keys()):
            raise ValueError("Investigation creation requires question and source with declared optional context")
        question = _text(body["question"], "biological question")
        supplied_sources = body.get("sources")
        if "sources" in body:
            if (type(supplied_sources) is not list or not 1 <= len(supplied_sources) <= 8
                    or any(type(value) is not str for value in supplied_sources)):
                raise ValueError("Attach one to eight explicit dataset sources")
            sources = [str(self.inputs.source_path(value)) for value in supplied_sources]
            if len(set(sources)) != len(sources):
                raise ValueError("A dataset source cannot be attached twice")
            if "source" in body and (len(sources) != 1 or str(self.inputs.source_path(body["source"])) != sources[0]):
                raise ValueError("Use source or the ordered sources list without contradictory identities")
        else:
            sources = [str(self.inputs.source_path(body["source"]))]
        source, multiple = sources[0], len(sources) > 1
        if multiple and (body.get("declarations") or body.get("expected_sha256") or body.get("selection")):
            raise ValueError("Multiple datasets require per-source declarations and hashes; a single saved cell selection cannot stand for the set")
        source_declarations = body.get("source_declarations", [{} for _ in sources])
        if (type(source_declarations) is not list or len(source_declarations) != len(sources)
                or any(type(value) is not dict for value in source_declarations)):
            raise ValueError("Supply one declaration record for each ordered source")
        expected_hashes = body.get("expected_sha256s")
        if expected_hashes is not None and (type(expected_hashes) is not list or len(expected_hashes) != len(sources)
                or any(type(value) is not str or _HASH.fullmatch(value) is None for value in expected_hashes)):
            raise ValueError("Expected source hashes must match the complete ordered source list")
        if body.get("selection") is not None and not isinstance(body["selection"], dict):
            raise ValueError("Use an exact structured figure selection")
        mode = body.get("proposal_mode", "operator")
        if mode not in {"operator", "agent"}:
            raise ValueError("Use operator or agent proposal mode")
        requirements = question_requirements(question, body.get("user_parameter_requirements"), self.app.parameters)
        parent_job = body.get("parent_job") or (body.get("selection") or {}).get("source_job")
        if parent_job is not None:
            _identity(parent_job, "parent job identity")
            if self.app.status(parent_job).get("source") not in sources:
                raise ValueError("Parent job must use this investigation input")
        with self._lock:
            if self.root.exists() and len(list(self.root.iterdir())) >= MAX_INVESTIGATIONS:
                raise ValueError("Investigation limit reached; preserve history and use a fresh workspace")
            parent = self._load(body["parent_investigation"]) if body.get("parent_investigation") else None
            identity = uuid.uuid4().hex
            record = {"format": FORMAT, "id": identity, "thread_id": parent["thread_id"] if parent else identity,
                "revision": 0, "created_at": _now(), "updated_at": _now(), "status": "inspecting",
                "question": question, "source": source, "proposal_mode": mode, "job_id": None, "job_state": None,
                "parent_job": parent_job, "parent_investigation": parent["id"] if parent else None, "children": [],
                "selection": None, "profile": None, "declarations": {}, "clarification_fields": [],
                "context_inheritance": _copy(context_inheritance) if context_inheritance else None,
                "intent": classify_question(question), "user_parameter_requirements": requirements,
                "requirements_source": "explicit_request_records_only", "scientific_context": _context(body.get("scientific_context") or {}),
                "scientific_operations": [], "scientific_session_id": None, "turns": [], "artifacts": [], "synthesis": None}
            if multiple:
                record.update(sources=sources, source_profiles=[], source_declarations=_copy(source_declarations), input_set_sha256=None)
                if context_inheritance and record["scientific_context"].get("dataset_ref"):
                    record["managed_metadata"] = True
            self._turn(record, "user_question", "Your question", {"question": question, "parent_job": parent_job}, step=2, role="user")
            if context_inheritance:
                self._turn(record, "context_inheritance", "Data context retained", context_inheritance, step=1)
            self._remember_campaign_grant(record)
            self._save(record)
            try:
                if multiple:
                    self._inspect_source_set(record, expected_hashes)
                    profile = record["profile"]
                else:
                    profile = _copy(self.inputs.inspect(source))
                    expected = body.get("expected_sha256", expected_hashes[0] if expected_hashes else None)
                    if expected is not None and (not isinstance(expected, str) or _HASH.fullmatch(expected) is None or expected != profile["sha256"]):
                        raise ValueError("Observed input checksum does not match the supplied expected identity")
                    record["profile"] = profile
                    record["declarations"] = self._declarations(body.get("declarations", source_declarations[0]), profile)
                    record["selection"] = self._selection(body.get("selection"), parent_job, source, profile["sha256"])
                record["turns"][0]["content"]["selection"] = _copy(record["selection"])
                self._turn(record, "data_audit", "Dataset inspection", {"observed": profile["observed"], "declarations": record["declarations"],
                    "inferred": profile.get("inferred", {}), "unknowns": profile.get("unknowns", []),
                    "input": {key: profile[key] for key in ("source", "sha256", "size_bytes")}}, step=1)
                if multiple:
                    record["turns"][-1]["content"].update(source_profiles=_copy(record["source_profiles"]),
                        source_declarations=_copy(record["source_declarations"]), input_set_sha256=record["input_set_sha256"])
                self._turn(record, "question_scope", "Question and scope", {"question": question, "intent": record["intent"],
                    "user_parameter_requirements": requirements, "parameter_origin": "Explicit requirements are separate from the frozen service profile."}, step=2)
                self._prepare(record)
            except (ValueError, PermissionError, RuntimeError, OSError, KeyError) as error:
                self._failure(record, error)
            self._save(record)
            if parent:
                parent["children"].append(identity)
                self._save(parent)
            return self._response(record)

    def _failure(self, record, error):
        record["status"] = "operation_failed"
        record["error"] = {"type": type(error).__name__, "message": str(error)[:2000]}
        self._turn(record, "error", "Action needs attention", record["error"], step=1 if record["profile"] is None else 3)

    def _prepare(self, record):
        record.pop("required_action", None)
        record.pop("error", None)
        record["clarification_fields"] = []
        profile, declarations = record["profile"], record["declarations"]
        if record.get("selection"):
            scope = record["scientific_context"].get("selection_scope")
            if scope is None:
                field = {"field": "selection_scope", "scope": "scientific_context", "label": "How should this selection be used?", "type": "select", "required": True,
                    "options": [{"value": "saved_evidence_only", "label": "Review selected saved evidence"},
                        {"value": "context_only_full_dataset", "label": "Prepare a full-dataset plan; selection is context only"}],
                    "reason": "The registered pipeline cannot silently fit a selected subpopulation. Choose saved evidence or explicitly confirm the full dataset."}
                record.update(status="awaiting_information", clarification_fields=[field], required_action="selection_scope")
                self._turn(record, "clarification", "Confirm the analysis population", {"fields": [field], "selected_observation_n": len(record["selection"]["observation_ids"])}, step=2)
                return
            if scope == "saved_evidence_only":
                self._selected_evidence(record)
                return
            record["population_scope"] = {"analysis_population": "full_registered_dataset", "selection_use": "context_only",
                "confirmation_source": "explicit_selection_scope_request", "selected_observation_n": len(record["selection"]["observation_ids"])}
        fields = self._clarifications(record)
        if fields:
            record.update(status="awaiting_information", clarification_fields=fields)
            self._turn(record, "clarification", "Confirm the dataset context", {"fields": fields}, step=1)
            return
        fixed_route = (record["intent"]["kind"] in {"qc_and_integration", "batch_effect_audit"}
            and profile["observed"].get("file_type") in {"h5ad", ".h5ad", "anndata"}
            and declarations.get("assay") == "scrna" and declarations.get("measurement") == "umi_counts"
            and declarations.get("capture_unit_key") == self.app.parameters.get("batch_key"))
        if fixed_route:
            unmet = [row for row in record["user_parameter_requirements"] if self.app.parameters.get(row["name"]) != row["value"]]
            if unmet:
                record["status"] = "awaiting_information"
                record["required_action"] = "scientific_context"
                self._turn(record, "clarification", "These requirements need a governed method plan", {"unmatched_user_requirements": unmet,
                    "reason": "The fixed workbench profile cannot silently change scientific parameters."}, step=3)
                return
            selector = getattr(self.app, "selector_config", None) or {}
            if record["proposal_mode"] == "agent" and selector.get("backend") == "codex-cli":
                context = record["scientific_context"]
                classification = context.get("data_classification", "unclassified")
                scope = getattr(self.app, "model_data_scope", "local_only")
                if (classification not in {"synthetic", "public"} or scope != classification
                        or context.get("allow_model_execution") is not True or self.app.allow_model_execution is not True):
                    fields = _grant_fields()
                    record.update(status="awaiting_information", clarification_fields=fields, required_action="authorize_model_planning")
                    self._turn(record, "clarification", "Confirm model planning access", {"fields": fields,
                        "configured_scope": scope, "reason": "Live proposal selection has not started. The declared data scope, current campaign grant and server permission must all match."}, step=3)
                    return
            # The controller's private keyword namespaces idempotency by this
            # investigation; reviewed plans from another thread cannot be reused.
            response = self.app.register({"source": record["source"], "expected_sha256": profile["sha256"],
                "question": record["question"], "declarations": declarations, "proposal_mode": record["proposal_mode"]}, investigation_id=record["id"])
            record.update(job_id=response["id"], status=response["status"])
            self._turn(record, "plan_preparation", "Preparing an analysis plan", {"job_id": response["id"],
                "proposal_mode": record["proposal_mode"], "population_scope": record.get("population_scope", {"analysis_population": "full_registered_dataset"}),
                "scope": "Registered scRNA QC, integration, clustering and available annotation; not comparative method superiority."}, step=3)
            self._sync_job(record)
            return
        scientific = getattr(self.app, "scientific", None)
        context = record["scientific_context"]
        if not context.get("counts_layer"):
            explicit_layer = next((row for row in record["user_parameter_requirements"] if row["name"] == "counts_layer" and isinstance(row["value"], str)), None)
            if explicit_layer:
                context["counts_layer"] = explicit_layer["value"]
        request = context.get("plan_request")
        if scientific is None:
            record["status"] = "awaiting_information"
            record["required_action"] = "configure_scientific_workspace"
            self._turn(record, "clarification", "A governed scientific plan is required", {"intent": record["intent"],
                "reason": "This question or dataset is outside the fixed scRNA profile. Enable the governed scientific workspace; no substitute QC analysis was started.",
                "scientific_workspace_enabled": False}, step=3)
            return
        if request is None:
            self._bootstrap_scientific_context(record)
            if record.get("sources"):
                dataset = scientific.store().resolve(context["dataset_ref"])
                context["rna_input_contract"] = self._input_set_binding(record, dataset)
            elif profile["observed"].get("file_type") == "h5ad" and declarations.get("assay") in {"scrna", "snrna"}:
                if not context.get("counts_layer"):
                    field = {"field": "counts_layer", "scope": "scientific_context", "label": "Measurement layer", "type": "select", "required": True,
                        "options": [{"value": value, "label": value} for value in ["X", *profile["observed"].get("layers", [])]],
                        "reason": "Choose the stored layer whose measurement semantics your provenance declaration describes. X is not selected by default."}
                    record.update(status="awaiting_information", clarification_fields=[field], required_action="measurement_layer")
                    self._turn(record, "clarification", "Confirm the measurement layer", {"fields": [field]}, step=1)
                    return
                if context["counts_layer"] not in ["X", *profile["observed"].get("layers", [])]:
                    raise ValueError("Measurement layer must be present in the inspected AnnData header")
                dataset = scientific.store().resolve(context["dataset_ref"])
                artifact = dataset["manifest"]["members"]["dataset"]
                context["rna_input_contract"] = {"format": "devbench.scrna-input-binding/1", "anndata_artifact_id": artifact["artifact_id"],
                    "declarations": {**declarations, "counts_layer": context["counts_layer"],
                        "biological_independence": context.get("biological_independence")}}
            request = {"action": "create_plan", "actor": "investigation-controller", "expected_revision": scientific.catalog()["revision"],
                "analysis_ref": record["scientific_context"]["analysis_ref"], "dataset_ref": record["scientific_context"]["dataset_ref"],
                "evaluation_purpose": "method_selection", "user_parameter_requirements": record["user_parameter_requirements"]}
            for key in ("rna_input_contract", "rna_execution_inputs_ref", "rna_execution_workspace", "scientific_contract"):
                if key in context:
                    request[key] = context[key]
        request = self._bound_plan_request(record, request)
        binding_key = digest({key: value for key, value in request.items() if key not in {"actor", "expected_revision"}})
        if record.get("scientific_plan_key") != binding_key:
            result = scientific.action(request)
            record["scientific_session_id"] = result["session_id"]
            record["scientific_plan_key"] = binding_key
            self._capture_planning_source(record, result["session_id"], "session_created")
            self._turn(record, "scientific_action", "Scientific plan created", {"action": "create_plan", "result": result,
                "planning_source": record["planning_source"]}, step=3)
        else:
            result = scientific._plan(record["scientific_session_id"])
        context["session_id"] = record["scientific_session_id"]
        advance = record["scientific_context"].get("advance_request")
        if advance:
            advance = {**_copy(advance), "action": "advance_to_gate", "session_id": result["session_id"],
                       "expected_revision": scientific.catalog()["revision"]}
            self._scientific(record, advance)
            return
        self._advance_or_gate(record, result)
        self._project_scientific_plan(record)

    def _scientific_cursor(self, scientific, session_id):
        """Observe and validate an existing graph; never initialize or resume it."""
        from .workflow_entrypoints import CHECKPOINT_NAME, LEDGER_NAME
        from .workflow_operations import WorkflowOperationLedger
        from .workflow_actions import WorkflowActions
        from .workflow_state import validate_workflow_state
        checkpoint, ledger_path = (scientific.root / name for name in (CHECKPOINT_NAME, LEDGER_NAME))
        if not checkpoint.exists() or not ledger_path.exists():
            return None
        if checkpoint.is_symlink() or ledger_path.is_symlink():
            raise ValueError("Scientific gate state cannot be a symlink")
        from .langgraph_workflow import SerialRNAWorkflow
        planner = scientific.planner()
        ledger = WorkflowOperationLedger(ledger_path, readonly=True)
        actions = WorkflowActions(planner, ledger, actor="investigation-gate-projection", selector=scientific.selector,
            scientific_workflow=scientific.scientific_workflow)
        with SerialRNAWorkflow(actions, checkpoint, stepwise=True) as workflow:
            snapshot = workflow.graph.get_state(workflow._config(session_id))
            if not snapshot.values:
                return None
            state = validate_workflow_state(snapshot.values)
            actions.check(state)
            following = list(snapshot.next)
            if len(following) == 1 and following[0] in {"review", "execution_review"}:
                actions.check_node_entry(following[0], state)
            return {"next": following, "phase": state["phase"], "status": state["status"],
                "trial_ids": state["trial_ids"], "trial_index": state["trial_index"], "failure": state.get("failure")}

    def _project_scientific_plan(self, record, catalog=None):
        scientific = getattr(self.app, "scientific", None)
        session_id = record.get("scientific_session_id")
        if scientific is None or session_id is None:
            return
        catalog = catalog or scientific.catalog()
        plan = scientific._plan(session_id)
        keys = ("session_id", "context", "scientific_candidates", "candidates", "trial_designs", "experiments", "recommendation",
                "scientific_reviews", "scientific_trial_definitions", "user_parameter_requirements", "evaluation_purpose", "limits")
        record["scientific_plan"] = {key: _copy(plan[key]) for key in keys if key in plan}
        pending = any(row.get("operation_id") in record["scientific_operations"] and row.get("status") in {"queued", "running"}
                      for row in catalog.get("operations", []))
        cursor, errors = None, []
        if pending:
            errors.append("A recorded scientific operation is still active.")
        else:
            try:
                cursor = self._scientific_cursor(scientific, session_id)
            except (ValueError, OSError, RuntimeError) as error:
                errors.append(str(error)[:2000])
        gate = cursor["next"][0] if cursor and len(cursor["next"]) == 1 and cursor["next"][0] in {"review", "execution_review"} else None
        valid_gate = bool(gate and cursor["status"] in {"ready", "review_required"} and not errors)
        if gate:
            record["gate"] = gate
        if not valid_gate and not errors:
            errors.append("The verified scientific controller is not at an operator review gate.")
        designs = plan.get("trial_designs", [])
        proposals = (designs[-1].get("proposals", []) if designs else [])
        declared = plan.get("experiments", [])
        options = {"gate": gate, "session_id": session_id, "workspace_revision": catalog["revision"],
            "controller": cursor, "data_execution_allowed": self.app.allow_data_execution,
            "startup_data_execution_allowed": self.app.allow_data_execution,
            "required_confirmations": [{"key": key, "label": value} for key, value in SCIENTIFIC_CONFIRMATIONS.items()] if gate == "review" else [],
            "review_evidence": {"workflow_steps": [step for step in catalog.get("workflow_steps", []) if step.get("session_id") == session_id],
                "scientific_reviews": plan.get("scientific_reviews", [])}, "proposals": [], "declared_trials": [],
            "blocked_reasons": errors, "can_submit": False}
        for spec in proposals:
            reasons = list(errors)
            if gate != "review":
                reasons.append("Trial declaration requires the verified planning review gate.")
            if any(row["id"] == spec["id"] for row in declared):
                reasons.append("This exact trial has already been declared.")
            options["proposals"].append({"draft_id": spec["id"], "spec": _copy(spec), "eligible": valid_gate and not reasons,
                "blocked_reasons": reasons, "diagnostics": {"review_scope": "Existing graph binding and predecessor scientific-review receipts", "resource_limits": spec.get("resource_limits"),
                    "criteria": spec.get("metrics", []), "metric_sources": spec.get("metric_sources", {}), "subset_validity": spec.get("subset_validity")}})
        next_trial = (cursor["trial_ids"][cursor["trial_index"]] if cursor and cursor["trial_index"] < len(cursor["trial_ids"]) else None)
        for trial in declared:
            reasons = list(errors)
            if gate != "execution_review" or trial["id"] != next_trial:
                reasons.append("Execution requires the next exact declared trial at its execution-review gate.")
            if trial.get("status") != "declared":
                reasons.append("This trial is not a fresh declared execution.")
            if not self.app.allow_data_execution:
                reasons.append("Data execution is disabled for this service.")
            options["declared_trials"].append({"experiment_id": trial["id"], "status": trial.get("status"), "spec": _copy(trial),
                "analysis_ref": plan.get("scientific_trial_definitions", {}).get(trial["id"]),
                "eligible": valid_gate and not reasons, "blocked_reasons": reasons})
        rows = options["proposals"] if gate == "review" else options["declared_trials"] if gate == "execution_review" else []
        options["can_submit"] = any(row["eligible"] for row in rows)
        if valid_gate and not options["can_submit"]:
            options["blocked_reasons"].append("No eligible frozen trial is available at this gate.")
        record["gate_options"] = _copy(options)
        if valid_gate and not pending:
            record["status"] = "awaiting_operator_review" if gate == "review" else "approved"

    def _selected_evidence(self, record):
        """Describe only verified saved geometry, never an inferred expression fit."""
        selection = record["selection"]
        validated = self._selection({key: selection[key] for key in ("figure_id", "panel_id", "observation_ids", "source_job", "parent_root", "display_only")},
            record["parent_job"], record["source"], record["profile"]["sha256"])
        figures = self.app.figures(selection["source_job"])
        figure = next(row for row in figures["figures"] if row["figure_id"] == selection["figure_id"])
        payload = self._figure_payload(selection["source_job"], figure)
        panel = next(row for row in payload["panels"] if row["panel_id"] == selection["panel_id"])
        ids = set(validated["observation_ids"])
        rows = [row for row in panel["rows"] if row["observation_id"] in ids]
        colors = {}
        for row in rows:
            color = str(row.get("color", "unavailable"))
            colors[color] = colors.get(color, 0) + 1
        findings = {"selected_observation_n": len(rows), "observation_ids": validated["observation_ids"],
            "source_job": selection["source_job"], "parent_root": selection["parent_root"], "figure_id": selection["figure_id"],
            "panel_id": selection["panel_id"], "saved_color_counts": colors,
            "saved_axes": {key: panel.get(key) for key in ("x_axis", "y_axis")}}
        for axis in ("x", "y"):
            values = [row[axis] for row in rows if type(row.get(axis)) in (int, float) and math.isfinite(row[axis])]
            if values:
                findings["saved_" + axis + "_range"] = {"min": min(values), "max": max(values)}
        record["population_scope"] = {"analysis_population": "selected_saved_observations", "selection_use": "saved_evidence_only", "new_analysis_executed": False}
        record["synthesis"] = {"supported_findings": findings,
            "method_evidence_assessment": {"method": "Read-only summary of verified saved figure data", "new_analysis_executed": False,
                "evidence_floor": "Exact saved identities, coordinates and recorded colors; no expression matrix, differential test, model or scientific review was run."},
            "uncertainties": [{"field": "expression_and_effects", "reason": "Saved embedding geometry cannot establish differential expression, population-specific effects, biological independence or causality."}]}
        record["status"] = "completed_saved_evidence"
        self._turn(record, "synthesis", "Selected saved evidence", record["synthesis"], step=5)

    def _bootstrap_scientific_context(self, record):
        """Create metadata from observations/question, without invented biology."""
        scientific, context, profile = self.app.scientific, record["scientific_context"], record["profile"]
        classification = context.get("data_classification", "unclassified")
        if classification not in {"unclassified", "synthetic", "public", "private"}:
            raise ValueError("Declare unclassified, synthetic, public or private input scope")
        for key in ("allow_model_execution", "auto_advance", "auto_synthesis"):
            if key in context and type(context[key]) is not bool:
                raise ValueError("Scientific campaign grants must be explicit booleans")
        unknowns = [{"field": row["field"], "impact": row.get("reason", row.get("impact", "Not established")),
                     "blocking": row.get("blocking", False)} for row in profile.get("unknowns", [])]
        metadata = {"observed": profile["observed"], "inferred": profile.get("inferred", {}),
            "unknowns": unknowns, "supplied_declarations": record["declarations"],
            "data_classification": classification, "classification_source": "retained_parent_declaration" if
                "data_classification" in (record.get("context_inheritance") or {}).get("inherited_fields", []) else
                "caller_declaration" if "data_classification" in context else "unclassified_default"}
        if record.get("sources"):
            metadata.update(source_declarations=_copy(record["source_declarations"]), input_set_sha256=record["input_set_sha256"])

        def apply(action_name, **fields):
            result = scientific.action({"action": action_name, "actor": "investigation-controller",
                "expected_revision": scientific.catalog()["revision"], **fields})
            self._turn(record, "scientific_action", action_name.replace("_", " "), {"action": action_name, "result": result,
                "scope": "Question and observed-input metadata; no model or analysis execution."}, step=1 if action_name == "register_dataset" else 2)
            return result

        if not scientific.catalog()["initialized"]:
            apply("initialize")
        if context.get("dataset_ref") and not context.get("study_id"):
            version = scientific.store().resolve(context["dataset_ref"])
            context["study_id"] = scientific.store().get("dataset", version["dataset_id"])["study_id"]
        if not context.get("study_id"):
            context["study_id"] = apply("create_study", name="Investigation " + record["id"][:8], aim=record["question"])["study_id"]
            self._save(record)
        if record.get("sources") and context.get("dataset_ref"):
            retained_metadata = scientific.store().resolve(context["dataset_ref"])["manifest"]["metadata"]
            comparable = lambda value: {key: item for key, item in value.items() if key != "classification_source"}
            if comparable(retained_metadata) == comparable(metadata):
                metadata = _copy(retained_metadata)
            record.setdefault("metadata_key", digest({"metadata": retained_metadata, "input_sha256": profile["sha256"]}))
            record["managed_metadata"] = True
        metadata_key = digest({"metadata": metadata, "input_sha256": profile["sha256"]})
        if not context.get("dataset_ref") or record.get("managed_metadata") and record.get("metadata_key") != metadata_key:
            prior = context.get("dataset_ref")
            artifact_type = "anndata" if profile["observed"].get("file_type") == "h5ad" else "json" if profile["observed"].get("file_type") == "json" else "table"
            members = {"dataset": {"path": record["source"], "artifact_type": artifact_type}}
            if record.get("sources"):
                members = {"source_" + str(index): {"path": source,
                    "artifact_type": "anndata" if item["observed"].get("file_type") == "h5ad" else
                        "json" if item["observed"].get("file_type") == "json" else "table"}
                    for index, (source, item) in enumerate(zip(record["sources"], record["source_profiles"]), 1)}
            fields = {"study_id": context["study_id"], "name": profile["filename"],
                "members": members, "metadata": metadata}
            if prior:
                fields["dataset_id"] = prior["object_id"]
            context["dataset_ref"] = apply("register_dataset", **fields)["ref"]
            record.update(managed_metadata=True, metadata_key=metadata_key)
            self._save(record)
        if not context.get("analysis_ref") or record.get("managed_analysis") and record.get("analysis_question") != record["question"]:
            result = apply("create_analysis", study_id=context["study_id"], name="Question " + record["id"][:8],
                objective=record["question"], computational_task=record["intent"]["kind"])
            context["analysis_ref"] = result["ref"]
            record.update(managed_analysis=True, analysis_question=record["question"])
            self._save(record)
        catalog = scientific.catalog()
        record["scientific_options"] = {"studies": [{"study_id": row["study_id"], "name": row["name"]} for row in catalog.get("studies", [])[:100]],
            "datasets": [{"ref": row["ref"], "name": row["name"]} for row in catalog.get("datasets", [])[:100]],
            "analyses": [{"ref": row["ref"], "name": row["name"]} for row in catalog.get("analyses", [])[:100]]}

    def _advance_or_gate(self, record, plan):
        context, scientific = record["scientific_context"], self.app.scientific
        configuration = scientific.configuration()
        fields = _grant_fields(advance=True)
        record.update(status="awaiting_information", clarification_fields=fields, required_action="scientific_authorization")
        classification = context.get("data_classification", "unclassified")
        offline = configuration.get("planning_mode") == "recorded_fixture" and classification == "synthetic"
        live = (classification in {"synthetic", "public"} and context.get("allow_model_execution") is True
                and self.app.allow_model_execution is True and getattr(self.app, "model_data_scope", None) == classification)
        if not configuration.get("scientific_workflow_configured"):
            record["required_action"] = "configure_scientific_workflow"
        elif not ({"bulk_rna_input", "scrna_input", "rna_input_set"} & plan["context"]["dataset_summary"].get("observed", {}).keys() or "rna_execution_inputs" in plan):
            record["required_action"] = "scientific_input_binding"
        elif context.get("auto_advance") is True and (offline or live):
            request = {"action": "advance_to_gate", "session_id": record["scientific_session_id"],
                "expected_revision": scientific.catalog()["revision"], "actor": "investigation-controller",
                "idempotency_key": record["id"] + ":planning:" + str(record["revision"]),
                "allow_model_execution": bool(live), "phase": "planning"}
            self._scientific(record, request)
            record["clarification_fields"] = []
            return
        self._turn(record, "clarification", "Review scientific planning context", {"fields": fields,
            "required_action": record["required_action"], "references": {key: context.get(key) for key in ("study_id", "dataset_ref", "analysis_ref", "session_id")},
            "reason": "The question is anchored to observed data. Planning still needs its actual registered input contract, configured adapters and explicit campaign grants; no replacement QC analysis was run."}, step=3)

    def _bound_plan_request(self, record, request):
        if not isinstance(request, dict) or request.get("action") != "create_plan":
            raise ValueError("Scientific context requires an explicit create_plan request with exact version references")
        scientific = self.app.scientific
        # The selected scientific dataset must contain the exact audited input.
        store, methods = scientific.store(), scientific.methods()
        dataset = store.resolve(request["dataset_ref"])
        hashes = [methods.get("artifact", value).get("sha256") for value in store.input_bindings([dataset["version_id"]]).values()]
        analysis = store.resolve(request["analysis_ref"])
        input_matches = record["profile"]["sha256"] in hashes
        if record.get("sources"):
            input_matches = (len(hashes) == len(record["sources"])
                and set(hashes) == {profile["sha256"] for profile in record["source_profiles"]})
        if not input_matches or analysis["definition"].get("task", {}).get("scientific_objective") != record["question"]:
            raise ValueError("Scientific plan context must bind this exact input and question")
        record["scientific_context"].update(dataset_ref=_copy(request["dataset_ref"]), analysis_ref=_copy(request["analysis_ref"]))
        request = _copy(request)
        if record.get("sources"):
            expected_binding = self._input_set_binding(record, dataset)
            if request.get("rna_input_contract", expected_binding) != expected_binding:
                raise ValueError("Scientific plan must retain every ordered input-set member and its declarations")
            request["rna_input_contract"] = expected_binding
            record["scientific_context"]["rna_input_contract"] = _copy(expected_binding)
        request["user_parameter_requirements"] = record["user_parameter_requirements"]
        return request

    def _input_set_binding(self, record, dataset):
        from .rna_input_set_contract import BINDING_FORMAT
        members = dataset["manifest"]["members"]
        expected_names = ["source_" + str(index) for index in range(1, len(record["sources"]) + 1)]
        if set(members) != set(expected_names):
            raise ValueError("Scientific dataset must bind exactly the complete named source set")
        result = []
        for name, profile, declarations in zip(expected_names, record["source_profiles"], record["source_declarations"]):
            identity = members[name]["artifact_id"]
            artifact = self.app.scientific.methods().get("artifact", identity)
            if artifact["sha256"] != profile["sha256"] or artifact["size_bytes"] != profile["size_bytes"]:
                raise ValueError("Scientific member order or source byte identity differs from the inspected input set")
            result.append({"name": name, "label": profile["filename"][:200], "artifact_id": identity,
                "declarations": _copy(declarations)})
        return {"format": BINDING_FORMAT, "members": result}

    def _sync_job(self, record):
        if not record.get("job_id"):
            return
        state = self.app.status(record["job_id"])
        record["job_state"] = _copy(state)
        record["status"] = state["status"]
        existing = {turn["content"].get("event_key") for turn in record["turns"] if turn["kind"] == "job_event"}
        for index, event in enumerate(state.get("state_history", [])):
            key = digest({"job_id": record["job_id"], "index": index, "event": event})
            if key not in existing:
                self._turn(record, "job_event", event["to"].replace("_", " "), {"job_id": record["job_id"], "event_key": key, **event},
                    step=4 if event["to"] in {"preparing", "running", "finalizing", "cancel_requested"} or event["to"].startswith("completed_") else 3)
        if state.get("proposal"):
            key = digest(state["proposal"])
            if record.get("plan_key") != key:
                record["plan_key"] = key
                self._turn(record, "plan_review", "Analysis plan", {"job_id": state["id"], "proposal": state["proposal"], "status": state["status"]}, step=3)
        if state["status"].startswith("completed_review_"):
            results = self.app.results(state["id"])
            figures = self.app.figures(state["id"])
            evidence_key = digest({"results": results, "root": figures["parent_root"]})
            if record.get("evidence_key") != evidence_key:
                record["evidence_key"] = evidence_key
                record["artifacts"] = [{**item, "job_id": state["id"]} for item in results.get("artifacts", [])]
                record["figures"] = figures["figures"]
                report = results.get("report", {})
                record["synthesis"] = {"supported_findings": {"execution_status": state["status"],
                    "metrics": {key: report[key] for key in ("cells_input", "cells_retained", "HVG_count") if key in report},
                    "deterministic_checks": results.get("deterministic_review", {}), "bundle_root": figures["parent_root"]},
                    "method_evidence_assessment": {"method": state.get("proposal", {}).get("configuration", {}).get("parameters", {}).get("workflow"),
                        "selection_basis": "Existing registered profile selected for the reviewed scope; no comparative method evaluation was performed.",
                        "evidence_floor": "Verified execution artifacts and deterministic output checks; independent scientific review is separate.",
                        "parameter_requirements": record["user_parameter_requirements"]},
                    "uncertainties": [*record["profile"].get("unknowns", []),
                        {"field": "experimental_declarations", "reason": "Experimental identity and assay provenance are operator declarations, not independent validation."},
                        {"field": "biological_interpretation", "reason": "Cell-level descriptive results do not establish independent biological replication, causal effects, method superiority or clinical validity."}]}
                record["synthesis"]["method_evidence_assessment"]["population_scope"] = record.get("population_scope", {"analysis_population": "full_registered_dataset"})
                self._turn(record, "synthesis", "Results and remaining uncertainty", record["synthesis"], step=5)

    def _scientific(self, record, request, operator_review=None):
        scientific = getattr(self.app, "scientific", None)
        if scientific is None or not isinstance(request, dict):
            raise ValueError("Scientific workspace is unavailable or its request is invalid")
        if request.get("allow_model_execution") is True and record["scientific_context"].get("data_classification") not in {"synthetic", "public"}:
            raise PermissionError("Model planning requires an explicit public or synthetic dataset declaration")
        if request.get("allow_model_execution") is True and (self.app.allow_model_execution is not True
                or getattr(self.app, "model_data_scope", None) != record["scientific_context"]["data_classification"]):
            raise PermissionError("Scientific model planning requires current startup permission and the exact declared server data scope")
        if request.get("action") == "create_plan":
            request = self._bound_plan_request(record, request)
        if "session_id" in request and request["session_id"] != record.get("scientific_session_id"):
            raise ValueError("Scientific action must use this investigation's exact planner session")
        for key in ("dataset_ref", "analysis_ref"):
            if key in request and request[key] != record["scientific_context"].get(key):
                raise ValueError("Scientific action must use this investigation's exact " + key)
        allowed = {"create_plan", "advance_to_gate", "discovery", "candidate_review", "implementation", "design_review", "conclusion_review",
            "science", "resolve", "design_trials", "draft_method", "recommend", "declare_trial", "run_trial", "rerun"}
        if request.get("action") not in allowed:
            raise ValueError("Use this investigation's planning or controlled-trial actions; metadata setup uses its bound context")
        name = request.get("action")
        if name in {"declare_trial", "run_trial"}:
            self._project_scientific_plan(record)
            gate = record["gate_options"]
            if not gate["can_submit"] or gate["gate"] != ("review" if name == "declare_trial" else "execution_review"):
                raise RuntimeError("This scientific controller is not at the verified operator gate for this action")
            if name == "declare_trial":
                matches = [row for row in gate["proposals"] if row["eligible"] and row["spec"] == request.get("spec")]
                if len(matches) != 1:
                    raise ValueError("Choose one exact eligible frozen scientific trial proposal")
                if operator_review is None:
                    raise ValueError("Scientific trial declaration requires an explicit operator review record")
            elif not any(row["eligible"] and row["experiment_id"] == request.get("experiment_id") for row in gate["declared_trials"]):
                raise ValueError("Execution must match the next exact eligible declared trial")
        if operator_review is not None:
            from .episode_proposals import _review_decision
            if name not in {"declare_trial", "run_trial"}:
                raise ValueError("Operator trial review metadata belongs only to declaration or execution actions")
            if not isinstance(operator_review, dict) or set(operator_review) != {"reviewer", "rationale", "confirmations"}:
                raise ValueError("Operator review requires reviewer, rationale and explicit confirmations")
            confirmations = set(SCIENTIFIC_CONFIRMATIONS) if name == "declare_trial" else set()
            _review_decision("approve", operator_review["reviewer"], operator_review["rationale"],
                operator_review["confirmations"], required_confirmations=confirmations)
            request = {**_copy(request), "actor": operator_review["reviewer"].strip()}
        result = scientific.action(_copy(request))
        if result.get("session_id"):
            record["scientific_session_id"] = result["session_id"]
        if record.get("scientific_session_id"):
            self._capture_planning_source(record, record["scientific_session_id"], "session_created" if name == "create_plan" else "bound_action_dispatched")
        if result.get("operation_id"):
            record["scientific_operations"].append(result["operation_id"])
            record.pop("gate", None)
            record.pop("required_action", None)
        if result.get("analysis_ref"):
            previous = record["scientific_context"].get("analysis_ref")
            if previous and result["analysis_ref"].get("object_id") != previous["object_id"]:
                raise ValueError("Scientific action returned an unrelated Analysis identity")
            if name == "declare_trial":
                plan = scientific._plan(record["scientific_session_id"])
                expected = plan.get("scientific_trial_definitions", {}).get(result.get("experiment", {}).get("id"))
                if expected != result["analysis_ref"]:
                    raise ValueError("Declared scientific analysis version differs from its exact planner trial definition")
            record["scientific_context"]["analysis_ref"] = _copy(result["analysis_ref"])
        if operator_review is not None:
            self._turn(record, "plan_review", "Scientific trial approved" if name == "declare_trial" else "Controlled execution authorized",
                {"session_id": record["scientific_session_id"], "operator_review": operator_review,
                    "selected_trial": (request.get("spec") or {}).get("id", request.get("experiment_id")),
                    "scientific_analysis_ref": result.get("analysis_ref"), "actor_authenticated": False}, step=3 if name == "declare_trial" else 4, role="operator")
        record["status"] = "planning" if request.get("action") != "run_trial" else "preparing"
        self._turn(record, "scientific_action", "Scientific action recorded", {"action": request.get("action"), "result": result,
            "planning_source": record.get("planning_source")},
            step=4 if name in {"run_trial", "rerun"} else 5 if request.get("phase") == "synthesis" or name in {"recommend", "conclusion_review"} else 3)
        self._project_scientific_plan(record)

    def _sync_scientific(self, record):
        scientific = getattr(self.app, "scientific", None)
        if scientific is None or not record["scientific_operations"]:
            return
        catalog = scientific.catalog()
        for step in catalog.get("workflow_steps", []):
            if step.get("session_id") != record.get("scientific_session_id"):
                continue
            key = digest(step)
            if not any(turn["kind"] == "scientific_step" and turn["content"].get("evidence_key") == key for turn in record["turns"]):
                self._turn(record, "scientific_step", step["node"].replace("_", " "),
                    {"evidence_key": key, "step": step}, step=4 if step["node"] in {"run", "verify"} else 5 if step["node"] in {"recommend", "conclusion_review"} else 3)
        for identity in record["scientific_operations"]:
            operation = next((row for row in catalog.get("operations", []) if row["operation_id"] == identity), None)
            if operation is None:
                record["status"] = "recovery_required"
                continue
            key = digest(operation)
            if any(turn["kind"] == "scientific_operation" and turn["content"].get("evidence_key") == key for turn in record["turns"]):
                continue
            value = operation.get("value") or {}
            self._turn(record, "scientific_operation", operation["action"].replace("_", " "), {"evidence_key": key, "operation": operation},
                step=4 if operation["action"] in {"run_trial", "rerun"} else 5 if value.get("requested_phase", value.get("phase")) == "synthesis" else 3)
            if operation["status"] == "completed":
                if value.get("results"):
                    self._bind_scientific_artifacts(record, value)
                record["status"] = value.get("status", "awaiting_information")
                if value.get("gate"):
                    record["gate"] = value["gate"]
                if value.get("required_action"):
                    record["required_action"] = value["required_action"]
                    record["scientific_blocker_reason"] = value.get("reason")
                if value.get("requested_phase", value.get("phase")) == "synthesis":
                    plan = scientific._plan(record["scientific_session_id"])
                    assessment = scientific.planner().evidence_assessment(record["scientific_session_id"])
                    trial_rows = [{key: result[key] for key in ("experiment_id", "candidate", "status", "metric_values", "warnings", "failures", "provenance") if key in result}
                                  for result in plan.get("results", [])]
                    compact_trials = [{key: result[key] for key in ("experiment_id", "candidate", "status", "metric_values", "warnings", "failures") if key in result}
                                      for result in trial_rows]
                    record["synthesis"] = {"supported_findings": {"workflow_status": value.get("status"),
                        "scientific_qualification": value.get("scientific_qualification", "not_established"), "trial_results": compact_trials},
                        "method_evidence_assessment": {"source": "Existing governed scientific workflow", "status": value.get("status"), "comparison_evidence": assessment},
                        "uncertainties": self._scientific_uncertainties(plan, assessment),
                        "evidence": {"governed_outputs": _copy(value), "trial_results": trial_rows}}
                    self._turn(record, "synthesis", "Scientific synthesis and review", record["synthesis"], step=5)
            elif operation["status"] in {"failed", "recovery_required"}:
                record["status"] = operation["status"]
            else:
                record["status"] = operation["status"]
        record["operation_pending"] = any(row.get("operation_id") in record["scientific_operations"]
            and row.get("status") in {"queued", "running"} for row in catalog.get("operations", []))
        self._maybe_synthesize(record)
        self._project_scientific_plan(record)

    def _bind_scientific_artifacts(self, record, value):
        scientific = self.app.scientific
        published = record.setdefault("scientific_results", [])
        unavailable = record.setdefault("artifact_unavailable", [])
        for result in value["results"]:
            reference = result.get("ref")
            try:
                resolved = scientific.resolve(reference)
                run = resolved["run"]
                request = run["trigger"]["request"]
                if run["trigger"].get("kind") == "run_trial":
                    if request.get("session_id") != record["scientific_session_id"]:
                        raise ValueError("Published result belongs to another scientific planner session")
                elif request.get("analysis_ref") != record["scientific_context"].get("analysis_ref") or request.get("dataset_ref") != record["scientific_context"].get("dataset_ref"):
                    raise ValueError("Published rerun result differs from this exact dataset/analysis binding")
                if value.get("run_id") != resolved["run_id"]:
                    raise ValueError("Published result differs from its completed scientific invocation")
                metadata = {"ref": reference, "run_id": resolved["run_id"], "attempt_id": resolved["attempt_id"],
                    "validation_id": resolved["validation_id"], "review_status": resolved["review"].get("review_status", "candidate")}
                if not any(row["ref"] == reference for row in published):
                    published.append(metadata)
                for identity in resolved["artifact_ids"]:
                    try:
                        _, content_type, filename = scientific.download(identity)
                        artifact = scientific.methods().get("artifact", identity)
                        descriptor = {"artifact_id": identity, "name": filename, "content_type": content_type,
                            **{key: artifact[key] for key in ("artifact_type", "size_bytes", "sha256", "suffix")},
                            "scientific_ref": reference, "scientific_session_id": record["scientific_session_id"],
                            "scientific_run_id": resolved["run_id"], "download_path": "/api/scientific/artifacts/" + identity}
                        if not any(row.get("artifact_id") == identity and row.get("scientific_ref") == reference for row in record["artifacts"]):
                            record["artifacts"].append(descriptor)
                    except (ValueError, OSError, KeyError) as error:
                        item = {"scientific_ref": reference, "artifact_id": identity, "reason": str(error)[:2000]}
                        if item not in unavailable:
                            unavailable.append(item)
            except (ValueError, OSError, KeyError) as error:
                item = {"scientific_ref": reference, "reason": str(error)[:2000]}
                if item not in unavailable:
                    unavailable.append(item)

    def _maybe_synthesize(self, record):
        context = record["scientific_context"]
        if (not context.get("auto_synthesis") or record.get("auto_synthesis_requested")
                or record.get("operation_pending") or not record.get("scientific_session_id")):
            return
        scientific = self.app.scientific
        from .workflow_entrypoints import synthesis_ready
        if not synthesis_ready(scientific.planner(), record["scientific_session_id"],
                scientific_workflow=scientific.scientific_workflow, selector=scientific.selector):
            return
        grant = self._campaign_grants.get(record["id"])
        classification = context.get("data_classification", "unclassified")
        configuration = scientific.configuration()
        offline = configuration.get("planning_mode") == "recorded_fixture" and classification == "synthetic"
        live = (classification in {"synthetic", "public"} and context.get("allow_model_execution") is True
                and self.app.allow_model_execution is True and getattr(self.app, "model_data_scope", None) == classification)
        if grant is None or grant != {key: context.get(key) for key in grant} or not (offline or live):
            record.update(status="awaiting_information", required_action="renew_synthesis_authorization", clarification_fields=_grant_fields(advance=True))
            return
        request = {"action": "advance_to_gate", "session_id": record["scientific_session_id"],
            "expected_revision": scientific.catalog()["revision"], "actor": "investigation-controller", "phase": "synthesis",
            "idempotency_key": record["id"] + ":synthesis", "allow_model_execution": bool(live)}
        # Persist intent before dispatch. A crash/error never causes an automatic
        # retry or a renewed model grant; operators inspect the retained outcome.
        record["auto_synthesis_requested"] = True
        self._turn(record, "scientific_action", "Post-execution synthesis authorized", {"request": request,
            "scope": "The explicitly granted synthesis phase after verified controlled trials."}, step=5, role="operator")
        self._save(record)
        try:
            self._scientific(record, request)
        except (ValueError, PermissionError, RuntimeError, OSError, KeyError) as error:
            self._failure(record, error)

    def get(self, identity):
        with self._lock:
            record = self._load(identity)
            before = digest(record)
            try:
                self._sync_job(record)
                self._sync_scientific(record)
            except (ValueError, PermissionError, RuntimeError, OSError, KeyError) as error:
                if record.get("error") != {"type": type(error).__name__, "message": str(error)[:2000]}:
                    self._failure(record, error)
            if not record.get("job_id") and not record["scientific_operations"] and record["status"] in {"inspecting", "queued"}:
                record["status"] = "recovery_required"
                record["required_action"] = "Review the interrupted input operation; nothing was restarted."
            if digest(record) != before:
                self._save(record)
            return self._response(record)

    def action(self, identity, body):
        if not isinstance(body, dict) or type(body.get("expected_revision")) is not int:
            raise ValueError("Investigation actions require an integer expected_revision")
        with self._lock:
            record = self._load(identity)
            if body["expected_revision"] != record["revision"]:
                raise RuntimeError("Investigation changed; refresh before applying this action")
            name = body.get("action")
            fields = {"clarify": {"declarations", "source_declarations", "question", "user_parameter_requirements", "scientific_context"},
                "review": {"reviewer", "decision", "rationale", "confirmations"}, "run": {"allow_data_execution"},
                "follow_up": {"question", "selection", "proposal_mode", "user_parameter_requirements", "scientific_context"},
                "scientific": {"request", "operator_review"}}
            if name not in fields or set(body) - ({"action", "expected_revision"} | fields[name]):
                raise ValueError("Use only the declared fields for this investigation action")
            if name == "follow_up":
                context, inheritance = self._follow_up_context(record, body.get("scientific_context", {}))
                followup = {"question": body.get("question"), "source": record["source"],
                    "declarations": record["declarations"], "proposal_mode": body.get("proposal_mode", record["proposal_mode"]),
                    "parent_job": record.get("job_id") or record.get("parent_job"), "parent_investigation": record["id"],
                    "selection": body.get("selection"), "user_parameter_requirements": body.get("user_parameter_requirements", []),
                    "scientific_context": context}
                if record.get("sources"):
                    if record.get("profile") is None:
                        raise RuntimeError("Complete the source-set inspection before linking a follow-up")
                    followup.pop("source"); followup.pop("declarations")
                    followup.update(sources=_copy(record["sources"]), source_declarations=_copy(record["source_declarations"]),
                        expected_sha256s=[profile["sha256"] for profile in record["source_profiles"]])
                response = self.create(followup, context_inheritance=inheritance)
                return response
            if name == "clarify":
                if record.get("job_id") or record["status"] not in {"awaiting_information", "operation_failed", "recovery_required"}:
                    raise RuntimeError("Clarification can only revise an unbound investigation; use a new follow-up for retained plans")
                source_answers = None
                if record.get("sources"):
                    if body.get("declarations"):
                        raise ValueError("Clarify each attached source independently")
                    self._inspect_source_set(record)
                    source_answers = body.get("source_declarations", [{} for _ in record["sources"]])
                    if type(source_answers) is not list or len(source_answers) != len(record["sources"]):
                        raise ValueError("Supply one partial declaration record per attached source")
                    from .rna_input_set_contract import validate_source_declarations
                    record["source_declarations"] = [validate_source_declarations({**old, **answer}, profile["observed"])
                        for old, answer, profile in zip(record["source_declarations"], source_answers, record["source_profiles"])
                        if type(answer) is dict]
                    if len(record["source_declarations"]) != len(record["sources"]):
                        raise ValueError("Per-source answers must be declaration objects")
                    supplied = {}
                else:
                    if "source_declarations" in body:
                        raise ValueError("Use legacy declarations for a single dataset")
                    if record["profile"] is None:
                        record["profile"] = _copy(self.inputs.inspect(record["source"]))
                    supplied = self._declarations(body.get("declarations", {}), record["profile"], partial=True)
                    record["declarations"].update(supplied)
                if "question" in body:
                    record["question"] = _text(body["question"], "revised biological question")
                    record["intent"] = classify_question(record["question"])
                if "scientific_context" in body:
                    record["scientific_context"].update(_context(body["scientific_context"]))
                    self._remember_campaign_grant(record)
                if "user_parameter_requirements" in body:
                    record["user_parameter_requirements"] = question_requirements(record["question"], body["user_parameter_requirements"], self.app.parameters)
                elif "question" in body:
                    record["user_parameter_requirements"] = question_requirements(record["question"], None, self.app.parameters)
                self._turn(record, "clarification_answer", "Dataset context confirmed", {"declarations": supplied,
                    "question": record["question"], "user_parameter_requirements": record["user_parameter_requirements"]}, step=1, role="user")
                if source_answers is not None:
                    record["turns"][-1]["content"]["source_declarations"] = _copy(source_answers)
                try:
                    self._prepare(record)
                except (ValueError, PermissionError, RuntimeError, OSError, KeyError) as error:
                    self._failure(record, error)
            elif name == "review":
                if not record.get("job_id"):
                    raise RuntimeError("An existing native plan is required before operator review")
                request = {key: body.get(key) for key in ("reviewer", "decision", "rationale", "confirmations")}
                result = self.app.review(record["job_id"], request)
                record["status"] = result["status"]
                self._turn(record, "plan_review", "Operator decision submitted", {"job_id": record["job_id"], "review": request, "result": result}, step=3, role="operator")
            elif name == "run":
                if body.get("allow_data_execution") is not True or not record.get("job_id"):
                    raise PermissionError("Execution requires an explicit per-run request and a reviewed native plan")
                result = self.app.run(record["job_id"])
                record["status"] = result["status"]
                self._turn(record, "execution_request", "Controlled execution requested", {"job_id": record["job_id"], "result": result,
                    "allow_data_execution": True, "scope": "This recorded request only; original startup permission and operator approval remain required."}, step=4, role="operator")
            elif name == "scientific":
                self._scientific(record, body.get("request"), body.get("operator_review"))
            self._save(record)
            return self._response(record)

    def _follow_up_context(self, parent, supplied):
        """Reuse immutable data context, never a question, approval or grant."""
        supplied = _context(supplied)
        previous = parent["scientific_context"]
        plan_request = previous.get("plan_request") or {}
        data_fields = {"study_id", "dataset_ref", "rna_input_contract", "counts_layer", "data_classification", "biological_independence"}
        inherited = {}
        for key in data_fields:
            if key in previous:
                inherited[key] = _copy(previous[key])
            elif key in plan_request:
                inherited[key] = _copy(plan_request[key])
        binding = inherited.get("rna_input_contract") or {}
        if binding.get("format") == "devbench.scrna-input-binding/1":
            declarations = binding.get("declarations", {})
            for key in ("counts_layer", "biological_independence"):
                if key not in inherited and key in declarations:
                    inherited[key] = _copy(declarations[key])
        if not inherited.get("study_id") and parent.get("scientific_session_id") and self.app.scientific is not None:
            plan = self.app.scientific._plan(parent["scientific_session_id"])
            study = plan.get("scientific_anchors", {}).get("study_id")
            if study:
                inherited["study_id"] = study
        context = _context({**inherited, **supplied})
        attribution = {"source": "retained_parent_data_bindings", "parent_investigation": parent["id"],
            "inherited_fields": sorted(set(inherited) - set(supplied)), "overridden_fields": sorted(set(inherited) & set(supplied)),
            "explicit_request_fields": sorted(supplied),
            "scope": "Data context only; the new question, method requirements, planning session, analysis objective, reviews and execution/model permissions are separate."}
        return context, attribution
