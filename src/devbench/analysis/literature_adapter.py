"""Admitted PubMed/PMC evidence service with immutable custody and scoped use.

The installed Literature clients are identity pins, never executed here: their
general request/environment paths do not receive selector or sandbox authority.
The coordinator uses the existing bounded HTTPS transport and safe extractors.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Literal
import uuid

from pydantic import Field, model_validator

from devbench.study.contracts import FrozenContract, Identifier, Sha256
from devbench.study.evidence import _read, decode_json
from devbench.engine.scientific_authority import (
    _check_lineage, _row, append_scientific_projection_event, load_scientific_projection,
    require_same_ledger_transaction,
)
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
from .checkpoints import atomic_json, digest
from .episode_proposals import _absolute
from .evidence_access import EvidenceBroker
from .scientific_result_contract import (
    BiologicalContext, LiteratureClaimBinding, LiteratureClaimAssessment, LiteratureQueryHandle, ClaimSpec,
)
from .rna_publication_sources import literature_provider_url, decode_literature_search, decode_literature_pmc_metadata
from .rna_reference_extraction import (
    MAX_LITERATURE_SECTION_CHARS, MAX_XML_NODES, _normalize_section_filter,
    extract_pmc_passages, extract_pubmed_passages,
)


PINNED_CLIENTS = {
    "skills/ncbi-entrez-skill/scripts/ncbi_entrez.py": "62ab008b58600ea7d79f305501012481b604648ce45c88e52d4e9f5265289f90",
    "skills/ncbi-pmc-skill/scripts/ncbi_pmc.py": "1fd4f29f45f724edb4a4abb14ab73b390d435becde011999488b095a3c0716c7",
    "scripts/literature_source_contract.py": "8ba81cf09f9ef69cf4b9bdd8a9b7cd4cf32a143e404d5f71b5cb57ca4fd7a1cd",
    "references/source-links.json": "354b0771fce7f1ac65d027aefed631520d212c167fd99af543cad5c49610e599",
}
_COUNTERS = ("requests", "pages", "bytes_reserved", "bytes_observed", "millis_reserved", "millis_observed", "unknown_transfers")


class LiteratureCapability(FrozenContract):
    plugin_root: str
    version: Literal["0.1.5"] = "0.1.5"

    def identity(self) -> dict:
        return {"plugin": "life-sciences-literature", "version": self.version,
                "client_sha256": dict(PINNED_CLIENTS), "wrapper": "devbench.literature-adapter/1"}

    def verify(self) -> dict:
        root = _absolute(self.plugin_root)
        for name, expected in PINNED_CLIENTS.items():
            path = root.joinpath(*name.split("/"))
            if path.resolve() != path or hashlib.sha256(_read(path, 100_000)).hexdigest() != expected:
                raise ValueError("Pinned Literature client identity is unavailable or changed")
        return self.identity()


class LiteratureLimits(FrozenContract):
    requests: int = Field(default=32, ge=1, le=256)
    pages: int = Field(default=32, ge=1, le=256)
    response_bytes: int = Field(default=2_000_000, ge=1024, le=2_000_000)
    total_bytes: int = Field(default=16_000_000, ge=1024, le=64_000_000)
    seconds: float = Field(default=120.0, gt=0, le=3600, allow_inf_nan=False)
    retries: int = Field(default=1, ge=0, le=2)
    max_passages: int = Field(default=64, ge=1, le=64)
    max_passage_chars: int = Field(default=8000, ge=1, le=8000)


class LiteratureRequest(FrozenContract):
    operation: Literal["search", "fetch", "status", "replay"]
    provider: Literal["pubmed", "pmc"]
    identifier: str | None = None
    query: str | None = Field(default=None, min_length=1, max_length=2000)
    version: int | None = Field(default=None, ge=1, le=9999)
    offset: int = Field(default=0, ge=0, le=9999)
    limit: int = Field(default=10, ge=1, le=20)
    snapshot_id: Sha256 | None = None
    passage_offset: int = Field(default=0, ge=0, le=MAX_XML_NODES)
    section_filter: str | None = Field(default=None, min_length=1, max_length=MAX_LITERATURE_SECTION_CHARS)

    @model_validator(mode="after")
    def registered(self):
        if self.passage_offset or self.section_filter is not None:
            if self.operation != "fetch":
                raise ValueError("Passage selection requires an article fetch")
            object.__setattr__(self, "section_filter", _normalize_section_filter(self.section_filter))
        if self.operation == "replay":
            if self.snapshot_id is None or any(value is not None for value in (self.identifier, self.query, self.version)) or self.offset:
                raise ValueError("Frozen replay names only an exact snapshot")
        else:
            if self.snapshot_id is not None:
                raise ValueError("Network operations cannot select a replay snapshot")
            if self.operation != "search":
                from .rna_publication_evidence import _canonical_identifier
                identity = _canonical_identifier("pmid" if self.provider == "pubmed" else "pmcid", self.identifier)
                if identity is None:
                    raise ValueError("Literature singleton requires an exact identifier")
                object.__setattr__(self, "identifier", identity)
            self.url()
        return self

    def url(self) -> str:
        return literature_provider_url(self.provider, self.operation, identifier=self.identifier, query=self.query,
                                       version=self.version, offset=self.offset, limit=self.limit)

    def record(self) -> dict:
        """Keep default requests in their original persisted representation."""
        omitted = {key for key in ("passage_offset", "section_filter") if getattr(self, key) in (0, None)}
        return self.model_dump(mode="json", exclude=omitted)


def _state(db, lineage_id):
    return _projection(db, lineage_id)[0]


def _projection(db, lineage_id):
    return load_scientific_projection(db, lineage_id, "literature", initial=_initial_state,
        apply=lambda state, row: _apply_event(state, row["record"]))


def _initial_state():
    state = {key: {} for key in ("operations", "snapshots", "heads", "claims", "assessments", "retracted", "notices")}
    state.update(grant=None, accounting=dict.fromkeys(_COUNTERS, 0), events=0)
    return state


def _apply_event(state, event):
    if event["event"] != "literature" or event["state_sha256"] != digest(event["state"]):
        raise OperationConflict("Literature ledger evidence changed")
    change = event["state"]
    if state["grant"] is not None and state["grant"] != change["grant"]:
        raise OperationConflict("Literature grant cannot reset across actors or resumes")
    state["grant"] = change["grant"]
    for section, values in change["updates"].items():
        state[section].update(values)
    state["events"] += 1


def replay_literature_records(records):
    """Fold retained Literature events without a live service or network grant."""
    state = _initial_state()
    for event in records:
        _apply_event(state, event)
    return state


def _claim_key(binding: LiteratureClaimBinding) -> str:
    return binding.claim_id + ":" + binding.review_binding_sha256


def _source_key(request: LiteratureRequest) -> str:
    key = {"provider": request.provider, "identifier": request.identifier, "version": request.version,
           "operation": request.operation}
    if request.operation == "search":
        key.update(query=request.query, offset=request.offset, limit=request.limit)
    elif request.passage_offset or request.section_filter is not None:
        key.update(passage_offset=request.passage_offset, section_filter=request.section_filter)
    return digest(key)


def _publication_key(request: LiteratureRequest) -> str | None:
    return None if request.operation == "search" else digest([request.provider, request.identifier, request.version])


def _semantic(value):
    """Exclude custody transport identities, preserving scientific text/status/version."""
    if isinstance(value, dict):
        return {key: _semantic(item) for key, item in value.items() if key not in
                {"source_sha256", "retrieved_at", "snapshot_id", "path", "disclosure_reservation_id"}}
    if isinstance(value, (list, tuple)):
        return [_semantic(item) for item in value]
    return value


def delimit_literature_prompt_data(value):
    """Mark PubMed and PMC payloads in a prompt-only projection."""
    if isinstance(value, (list, tuple)):
        return [delimit_literature_prompt_data(item) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("format") == "devbench.literature-result/1":
        source_keys = ("extraction", "metadata", "search", "responses")
        return {
            **{key: item for key, item in value.items() if key not in source_keys},
            "untrusted_literature_data": [
                "BEGIN_UNTRUSTED_LITERATURE_DATA",
                {key: value[key] for key in source_keys if key in value},
                "END_UNTRUSTED_LITERATURE_DATA",
            ],
        }
    return {key: delimit_literature_prompt_data(item) for key, item in value.items()}


def _fence(authority, db, lineage_id):
    row = db.execute("SELECT * FROM scientific_records WHERE lineage_id=? AND kind='binding' ORDER BY revision DESC LIMIT 1",
                     (lineage_id,)).fetchone()
    if row is None:
        raise OperationConflict("Literature requires a scientific binding")
    binding = _row(row)
    execution = authority._load_state(db, lineage_id)
    return {"binding_sha256": binding["sha256"], "binding_id": binding["record"]["record_id"],
            "epoch": None if execution is None else execution["epoch"],
            "cancelled": execution is not None and execution["status"] == "cancelled"}


def _status_view(extraction, metadata):
    if metadata is not None:
        content_status = extraction["status"] if extraction is not None else {}
        identifiers = dict(extraction["identifiers"]) if extraction is not None else {}
        identifiers.update({key: value for key, value in metadata["resolved_identifiers"].items() if value is not None})
        return {"identifiers": identifiers, "retraction": "retracted" if
                metadata["reported_retracted"] or content_status.get("retraction") == "retracted" else "not_reported",
            "correction": content_status.get("correction", "unknown"), "coverage": metadata["notice_coverage"], "version": metadata["publication_version"],
            "manuscript": metadata["version_type"], "license": metadata["license_code"], "title": metadata["title"],
            "notices": content_status.get("notices", []),
            "document_checksums": [item["md5"] for item in metadata["observed_links"] if item["kind"] == "xml_url"]}
    if extraction is not None:
        return {"identifiers": extraction["identifiers"], **extraction["status"],
            "version": extraction["manuscript_version"], "license": extraction["license"],
            "title": extraction["title"], "publication_date": extraction["publication_date"],
            "document_sha256": extraction["source_sha256"]}
    return None


def _identifier_keys(status):
    return tuple(sorted(key + ":" + value.casefold() for key, value in (status or {}).get("identifiers", {}).items() if value))


def _notice_updates(state, status):
    retracted = dict.fromkeys(_identifier_keys(status), True) if status["retraction"] == "retracted" else {}
    notices = {}
    for notice in status.get("notices", []):
        target = notice.get("pmid")
        if target is None:
            continue
        if notice["relation"] == "RetractionOf":
            retracted["pmid:" + target] = True
        if notice["relation"] in {"RetractionOf", "ErratumFor"}:
            affected = ("pmid:" + target,)
        elif notice["relation"] in {"RetractionIn", "ErratumIn"}:
            affected = _identifier_keys(status)
        else:
            continue
        identity = digest({"notice": notice, "source": status["identifiers"]})
        for key in affected:
            notices[key] = sorted({*state["notices"].get(key, []), *notices.get(key, []), identity})
    return retracted, notices


def _implementation_identity():
    root = Path(__file__).resolve().parent
    return {name: hashlib.sha256(_read(root / name, 1_000_000)).hexdigest() for name in
        ("literature_adapter.py", "rna_reference_extraction.py", "rna_publication_evidence.py", "rna_publication_sources.py", "method_sources.py")}


def _retained_snapshot(ref, root, maximum):
    root = _absolute(root)
    path = _absolute(ref["path"])
    if not path.is_relative_to(root):
        raise ValueError("Literature snapshot escaped its retained custody root")
    snapshot = decode_json(_read(path, 4_000_000))
    if digest(snapshot) != ref["sha256"]:
        raise ValueError("Literature snapshot bytes changed")
    for capture in snapshot["responses"]:
        path = _absolute(capture["path"])
        if not path.is_relative_to(root):
            raise ValueError("Literature response escaped custody")
        body = _read(path, maximum)
        if len(body) != capture["size_bytes"] or hashlib.sha256(body).hexdigest() != capture["content_sha256"]:
            raise ValueError("Original Literature response bytes changed")
    if any(capture not in snapshot["responses"] for capture in snapshot["decode_responses"]):
        raise ValueError("Literature extraction uses an uncaptured response")
    return snapshot


def _decode_frozen_response(request, responses, limits, resolve):
    body = resolve(responses[-1]["path"], responses[-1]["content_sha256"], limits.response_bytes)
    if request.operation == "search":
        return None, decode_literature_search(body, provider=request.provider, offset=request.offset, limit=request.limit), None
    options = dict(max_passages=limits.max_passages, max_passage_chars=limits.max_passage_chars,
                   passage_offset=request.passage_offset, section_filter=request.section_filter)
    if request.provider == "pubmed":
        return extract_pubmed_passages(body, expected_pmid=request.identifier, **options), None, None
    metadata = decode_literature_pmc_metadata(resolve(responses[0]["path"], responses[0]["content_sha256"], 1_000_000),
        expected_pmcid=request.identifier, expected_version=request.version)
    if request.operation == "status":
        return None, None, metadata
    xml_links = [item for item in metadata["observed_links"] if item["kind"] == "xml_url"]
    if len(xml_links) != 1 or hashlib.md5(body).hexdigest() != xml_links[0]["md5"]:
        raise ValueError("PMC XML differs from the exact metadata checksum")
    extraction = extract_pmc_passages(body, expected_pmcid=request.identifier,
        expected_pmid=metadata["resolved_identifiers"]["pmid"], expected_doi=metadata["resolved_identifiers"]["doi"], **options)
    if extraction["title"] != metadata["title"]:
        raise ValueError("PMC content and status titles differ")
    return extraction, None, metadata


def verify_frozen_snapshot(reference, resolve_member, *, limits, implementation_sha256):
    """Replay retained Literature from digest-bound logical members, with no service or network."""
    if not callable(resolve_member) or implementation_sha256 != _implementation_identity():
        raise ValueError("Frozen Literature implementation changed or resolver unavailable")
    limits = LiteratureLimits.model_validate(limits)
    value = decode_json(resolve_member(reference["path"], reference["sha256"], 4_000_000))
    if digest(value) != reference["sha256"]:
        raise ValueError("Frozen Literature snapshot identity changed")
    for response in value["responses"]:
        raw = resolve_member(response["path"], response["content_sha256"], limits.response_bytes)
        if len(raw) != response["size_bytes"] or hashlib.sha256(raw).hexdigest() != response["content_sha256"]:
            raise ValueError("Frozen Literature original response changed")
    if any(response not in value["responses"] for response in value["decode_responses"]):
        raise ValueError("Frozen Literature decoder uses an unrecorded response")
    if ("document_signature" in reference and
            reference["document_signature"] != digest([item["content_sha256"] for item in value["decode_responses"]])):
        raise ValueError("Frozen Literature document revision changed")
    if value["failure"] is None:
        request = LiteratureRequest.model_validate_json(json.dumps(value["request"]))
        observed = _decode_frozen_response(request, value["decode_responses"], limits, resolve_member)
        if observed != (value["extraction"], value["search"], value["metadata"]):
            raise ValueError("Frozen Literature extraction no longer reproduces")
    return value


def _claim_evidence_basis(state, binding):
    completed, pending = set(), set()
    document = binding.model_dump(mode="json") if isinstance(binding, LiteratureClaimBinding) else binding
    for item in state["operations"].values():
        if item["spec"]["claim_binding"] != document:
            continue
        if item["status"] == "pending":
            pending.add(item["identity"])
        elif item["result"]["status"] != "unavailable":
            completed.add(state["snapshots"][item["result"]["snapshot_id"]]["content_identity_sha256"])
    return digest({"completed": sorted(completed), "pending": sorted(pending)})


_CONTEXT_NAMESPACES = {"organism": "ncbitaxon", "tissue": "uberon", "intervention": "chebi"}
_CONTEXT_ALIASES = {
    "organism": {
        "human": "ncbitaxon:9606", "humans": "ncbitaxon:9606", "homo sapiens": "ncbitaxon:9606",
        "house mouse": "ncbitaxon:10090", "mus musculus": "ncbitaxon:10090",
        "norway rat": "ncbitaxon:10116", "rattus norvegicus": "ncbitaxon:10116",
    },
    "tissue": {"lung": "uberon:0002048", "liver": "uberon:0002107"},
    "intervention": {"dexamethasone": "chebi:41879"},
    "direction": {
        "increase": "increased", "upregulated": "increased", "up-regulated": "increased",
        "decrease": "decreased", "reduced": "decreased", "downregulated": "decreased",
        "down-regulated": "decreased",
    },
}
_UNKNOWN_CONTEXT = {"", "unknown", "not reported", "unspecified"}


def _context_term(field, value):
    """Resolve exact names and OBO identifiers within the declared dimension.

    The aliases use NCBI Taxonomy, Uberon and ChEBI identities. Ancestors,
    species groups, mixtures, formulations and qualified labels stay distinct.
    """
    if value is None:
        return None
    normalized = " ".join(value.casefold().split())
    if normalized in _UNKNOWN_CONTEXT:
        return None
    namespace = _CONTEXT_NAMESPACES.get(field)
    if namespace is not None:
        identifier = normalized.removeprefix("https://purl.obolibrary.org/obo/")
        identifier = identifier.removeprefix("http://purl.obolibrary.org/obo/").replace("_", ":", 1)
        prefix, _, accession = identifier.partition(":")
        if prefix == namespace and accession.isascii() and accession.isdecimal():
            return identifier
    return _CONTEXT_ALIASES.get(field, {}).get(normalized, normalized)


def _context_alignment(target: BiologicalContext, source: BiologicalContext) -> dict[str, str]:
    alignment = {}
    for field in type(target).model_fields:
        expected, observed = (_context_term(field, getattr(context, field)) for context in (target, source))
        alignment[field] = ("unknown" if expected is None or observed is None else
                            "match" if expected == observed else "mismatch")
    return alignment


class LiteratureService:
    """Host capability sharing the investigation's durable authority and budgets."""

    def __init__(self, authority, lineage_id, root, *, broker, capability, limits=None,
                 allow_network=False, data_scope=None):
        _check_lineage(lineage_id)
        if type(broker) is not EvidenceBroker or broker.authority.ledger.path != authority.ledger.path:
            raise TypeError("Literature custody and disclosures require the same scientific authority")
        self.authority, self.ledger, self.lineage_id, self.broker = authority, authority.ledger, lineage_id, broker
        self.root = _absolute(root)
        from devbench.study.permissions import require_protected_storage
        require_protected_storage(self.root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.capability = LiteratureCapability.model_validate(capability)
        self.limits = LiteratureLimits.model_validate(limits) if limits is not None else LiteratureLimits()
        self.allow_network, self.data_scope = allow_network is True, data_scope
        self.grant = {"format": "devbench.literature-grant/1", "root": str(self.root),
                      "capability": self.capability.identity(), "limits": self.limits.model_dump(mode="json"),
                      "implementation_sha256": _implementation_identity()}

    def state(self):
        with self.ledger._database() as db:
            db.execute("BEGIN")
            return _state(db, self.lineage_id)

    def _write(self, action, build):
        with self.authority._execution_tx(self.lineage_id) as db:
            if self.authority._revision(db, self.lineage_id) == 0:
                raise OperationConflict("Literature service requires an existing scientific lineage")
            state, head = _projection(db, self.lineage_id)
            if state["grant"] is not None and state["grant"] != self.grant:
                raise OperationConflict("Literature grant cannot reset across actors or resumes")
            snapshot = head.snapshot(lambda: state)
            updates, result = build(state, db)
            value = {"grant": self.grant, "action": action, "updates": updates}
            append_scientific_projection_event(self.authority, db, self.lineage_id, "literature", {"kind": "execution_event",
                "record_id": "literature-" + uuid.uuid4().hex, "event": "literature",
                "state": value, "state_sha256": digest(value)}, snapshot)
            return deepcopy(result)

    def bind_claim(self, binding):
        binding = LiteratureClaimBinding.model_validate(binding)
        from .scientific_review_holds import ReviewHoldLifecycle
        def bind(state, db):
            entities = ReviewHoldLifecycle(self.authority, self.lineage_id)._state(db)["entities"]
            if binding.claim_id not in entities or entities[binding.claim_id]["kind"] != "claim":
                raise ValueError("Literature use requires a registered claim dependency")
            key = _claim_key(binding)
            record = {"binding": binding.model_dump(mode="json"), "assessment_id": None}
            if key in state["claims"]:
                if state["claims"][key]["binding"] != record["binding"]:
                    raise ValueError("Claim binding identity cannot be reused")
                return {}, state["claims"][key]
            return {"claims": {key: record}}, record
        return self._write("bind_claim", bind)

    def _context(self, context_id):
        with self.ledger._database() as db:
            db.execute("BEGIN")
            context, policy = self.broker._context_policy(db, context_id)
            if context["lineage_id"] != self.lineage_id or context["role"] not in {"controller", "challenger"}:
                raise PermissionError("Literature requires this investigation's controller or challenger")
            if context["phase"] not in {"analysis", "publication_initial", "narrative_reveal"}:
                raise PermissionError("Literature interpretation follows numerical evidence; no pre-fit or sandbox browsing")
            return context, policy

    def _reserve_hop(self, request_id, maximum, seconds):
        def reserve(state, db):
            if state["operations"][request_id]["status"] != "pending":
                raise OperationConflict("Literature operation is no longer pending")
            current = _fence(self.authority, db, self.lineage_id)
            if current["cancelled"] or current != state["operations"][request_id]["fence"]:
                raise OperationBlocked("literature_execution_fence_changed")
            counts = dict(state["accounting"])
            counts["requests"] += 1; counts["pages"] += 1
            counts["bytes_reserved"] += maximum; counts["millis_reserved"] += math.ceil(seconds * 1000)
            if (counts["requests"] > self.limits.requests or counts["pages"] > self.limits.pages
                    or max(counts["bytes_reserved"], counts["bytes_observed"]) > self.limits.total_bytes
                    or max(counts["millis_reserved"], counts["millis_observed"]) > self.limits.seconds * 1000):
                raise OperationBlocked("literature_shared_budget_exhausted")
            return {"accounting": counts}, counts
        return self._write("reserve_request", reserve)

    def _capture(self, url, request_id, attempt, position, *, xml):
        from .rna_publication_evidence import _retrieve_approved_text
        directory = self.root / "captures" / digest(request_id)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        maximum = min(self.limits.response_bytes, 2_000_000 if xml else 1_000_000)
        remaining = self.limits.seconds - max(self.state()["accounting"]["millis_reserved"],
                                            self.state()["accounting"]["millis_observed"]) / 1000
        if remaining <= 0:
            raise OperationBlocked("literature_shared_time_exhausted")
        seconds = min(10.0, remaining)
        start = time.monotonic(); observed = 0; entered = False; completed = False
        types = {"application/xml", "text/xml", "application/jats+xml"} if xml else {"application/json"}
        if url.startswith("https://pmc-oa-opendata.s3.amazonaws.com/"):
            types |= {"application/octet-stream", "binary/octet-stream"}
        def reserve(url, cap):
            nonlocal entered
            self._reserve_hop(request_id, cap + 1, seconds)
            entered = True
        try:
            record = _retrieve_approved_text(url, [url], directory / f"{attempt}-{position}.response",
                allow_network=self.allow_network, max_bytes=maximum, timeout_seconds=seconds,
                on_request=reserve,
                content_types=types)
            observed = record["size_bytes"]
            completed = True
            return record
        finally:
            if entered:
                elapsed = math.ceil((time.monotonic() - start) * 1000)
                def finish(state, db):
                    counts = dict(state["accounting"])
                    counts["bytes_observed"] += observed; counts["millis_observed"] += elapsed
                    counts["unknown_transfers"] += int(not completed)
                    return {"accounting": counts}, None
                self._write("observe_transfer", finish)

    def _decode(self, request, responses):
        return _decode_frozen_response(request, responses, self.limits,
                                       lambda path, identity, maximum: _read(_absolute(path), maximum))

    def execute(self, request, *, request_id, context_id, binding):
        request = LiteratureRequest.model_validate(request)
        binding = LiteratureClaimBinding.model_validate(binding)
        from pydantic import TypeAdapter
        TypeAdapter(Identifier).validate_python(request_id)
        context, _ = self._context(context_id)
        key = _claim_key(binding)
        spec = {"request": request.record(), "context_id": context_id,
                "claim_binding": binding.model_dump(mode="json")}
        identity = digest(spec)
        current = self.state()
        if key not in current["claims"] or current["claims"][key]["binding"] != spec["claim_binding"]:
            raise ValueError("Literature action requires its exact registered claim binding")
        if request_id in current["operations"]:
            prior = current["operations"][request_id]
            if prior["identity"] != identity:
                raise ValueError("Literature request identity cannot be reused")
            if prior["status"] == "pending":
                raise OperationBlocked("literature_request_uncertain_reconcile_without_redispatch")
            return self._deliver(prior["result"], request, context_id, binding)
        if request.operation == "replay":
            snapshot = self.replay(request.snapshot_id)
            if snapshot["request"]["provider"] != request.provider:
                raise ValueError("Replay provider differs from frozen evidence")
            eligible_contexts = {context_id}
            if context["role"] == "challenger" and context["phase"] == "narrative_reveal":
                eligible_contexts.update(context["parents"])
            issued = any((item.get("result") or {}).get("snapshot_id") == request.snapshot_id
                         and item["spec"]["context_id"] in eligible_contexts for item in current["operations"].values())
            if not issued:
                raise PermissionError("Frozen Literature handle was not issued to this context")
            return self._deliver(self._projection(snapshot, request.snapshot_id, binding), request, context_id, binding)
        def start(state, db):
            if request_id in state["operations"]:
                raise OperationConflict("Literature request was concurrently reserved")
            value = {"identity": identity, "spec": spec, "status": "pending", "result": None,
                     "fence": _fence(self.authority, db, self.lineage_id)}
            return {"operations": {request_id: value}}, None
        self._write("begin_request", start)
        captures, all_captures, failure, extraction, search, metadata = [], [], None, None, None, None
        failure_detail = None
        try:
            if not self.allow_network or self.data_scope not in {"public", "synthetic"}:
                raise PermissionError("Literature network use requires explicit public/synthetic disclosure authority")
            self.capability.verify()
            if _implementation_identity() != self.grant["implementation_sha256"]:
                raise ValueError("Admitted Literature implementation changed")
            for attempt in range(self.limits.retries + 1):
                try:
                    captures = []
                    if request.provider == "pmc" and request.operation == "fetch":
                        url = literature_provider_url("pmc", "status", identifier=request.identifier, version=request.version)
                        captures.append(self._capture(url, request_id, attempt, 0, xml=False))
                        all_captures.append(captures[-1])
                        observed = decode_literature_pmc_metadata(_read(_absolute(captures[-1]["path"]), 1_000_000),
                            expected_pmcid=request.identifier, expected_version=request.version)
                        if observed["reported_retracted"]:
                            ids = _identifier_keys(_status_view(None, observed))
                            def accept_status(state, db):
                                fence = _fence(self.authority, db, self.lineage_id)
                                if fence["cancelled"] or fence != state["operations"][request_id]["fence"]:
                                    raise OperationBlocked("literature_execution_fence_changed")
                                return {"retracted": dict.fromkeys(ids, True)}, None
                            self._write("observed_retraction", accept_status)
                        xml_links = [item for item in observed["observed_links"] if item["kind"] == "xml_url"]
                        if len(xml_links) != 1 or xml_links[0]["url"].split("?", 1)[0] != request.url():
                            raise ValueError("PMC metadata does not offer the exact admitted article XML")
                    captures.append(self._capture(request.url(), request_id, attempt, len(captures),
                        xml=request.provider == "pubmed" and request.operation != "search" or request.provider == "pmc" and request.operation == "fetch"))
                    all_captures.append(captures[-1])
                    extraction, search, metadata = self._decode(request, captures)
                    self.capability.verify()
                    if _implementation_identity() != self.grant["implementation_sha256"]:
                        raise ValueError("Admitted Literature implementation changed during retrieval")
                    break
                except (OSError, TimeoutError):
                    if attempt >= self.limits.retries:
                        raise
        except Exception as error:
            failure, failure_detail = type(error).__name__, str(error)[:1000]
        status = ("unavailable" if failure or extraction is not None and extraction["availability"] == "unavailable" else
                  "truncated" if (extraction is not None and extraction["availability"] == "truncated"
                                  or search is not None and search["truncated"]) else "available")
        snapshot = {"format": "devbench.literature-snapshot/1", "request": spec["request"],
            "capability": self.capability.identity(), "responses": all_captures,
            "decode_responses": captures, "extraction": extraction,
            "search": search, "metadata": metadata, "status": status, "failure": failure, "failure_detail": failure_detail,
            "source_key": _source_key(request), "publication_key": _publication_key(request),
            "access": extraction["access"] if extraction is not None and request.operation == "fetch" else "metadata",
            "semantic_entailment_verified": False,
            "fence": self.state()["operations"][request_id]["fence"]}
        snapshot_id = digest(snapshot)
        directory = self.root / "snapshots"; directory.mkdir(mode=0o700, exist_ok=True)
        target = directory / (snapshot_id + ".json")
        if target.exists():
            if decode_json(_read(target, 4_000_000)) != snapshot:
                raise OperationConflict("Immutable Literature snapshot identity changed")
        else:
            atomic_json(target, snapshot)
        response_identities = [item["content_sha256"] for item in captures]
        signature = digest({"responses": response_identities,
                            "extraction": extraction, "metadata": metadata, "search": search, "status": status})
        document_signature = digest(response_identities) if request.operation == "fetch" else None
        disclosed_extraction = extraction
        if request.operation == "status" and extraction is not None:
            disclosed_extraction = {key: value for key, value in extraction.items() if key != "passages"}
        search_content = None if search is None else {**search, "identifiers": sorted(search["identifiers"])}
        # Different query wording/receipt ordering alone is not new counterevidence.
        semantic = digest({"provider": request.provider, "access": snapshot["access"], "extraction": _semantic(disclosed_extraction),
                           "metadata": _semantic(metadata), "search": search_content, "status": status})
        status_view = _status_view(extraction, metadata)
        result = self._projection(snapshot, snapshot_id, binding)
        def finish(state, db):
            operation = dict(state["operations"][request_id]); operation.update(status="completed", result=result)
            reference = {"path": str(target), "sha256": snapshot_id, "signature": signature,
                "content_identity_sha256": semantic, "source_key": snapshot["source_key"],
                "publication_key": snapshot["publication_key"], "status": status,
                "status_signature": digest(status_view), "identifiers": list(_identifier_keys(status_view))}
            if document_signature is not None:
                reference["document_signature"] = document_signature
            updates = {"operations": {request_id: operation}, "snapshots": {snapshot_id: reference}}
            current = _fence(self.authority, db, self.lineage_id)
            if failure is None and current == snapshot["fence"] and not current["cancelled"]:
                updates["heads"] = {snapshot["source_key"]: signature}
                if document_signature is not None:
                    updates["heads"]["document:" + snapshot["publication_key"]] = document_signature
                if status_view is not None:
                    updates["heads"]["status:" + snapshot["publication_key"]] = digest(status_view)
                    retracted, notices = _notice_updates(state, status_view)
                    if retracted:
                        updates["retracted"] = retracted
                    if notices:
                        updates["notices"] = notices
                use = dict(state["claims"][key])
                assessed = state["assessments"].get(use["assessment_id"])
                known = set() if assessed is None else {state["snapshots"][item]["content_identity_sha256"]
                    for item in assessed["dependencies"]}
                if assessed is not None and semantic not in known:
                    use["assessment_id"] = None
                    updates["claims"] = {key: use}
            return updates, None
        self._write("capture_complete", finish)
        return self._deliver(result, request, context_id, binding)

    def _projection(self, snapshot, snapshot_id, binding):
        extraction = snapshot["extraction"]
        if extraction is not None and snapshot["request"]["operation"] == "status":
            extraction = {key: value for key, value in extraction.items() if key != "passages"}
        return {"format": "devbench.literature-result/1", "snapshot_id": snapshot_id, "status": snapshot["status"],
            "affected_claim_ids": [binding.claim_id], "failure": snapshot["failure"], "access": snapshot["access"],
            "extraction": extraction, "metadata": snapshot["metadata"], "search": snapshot["search"],
            "responses": [{key: item[key] for key in ("requested_url", "final_url", "content_sha256", "retrieved_at", "content_type", "size_bytes")}
                          for item in snapshot["responses"]],
            "scope": "untrusted_external_evidence_not_claim_support_or_numerical_validation"}

    def _deliver(self, result, request, context_id, binding):
        _, policy = self._context(context_id)
        query_sha256 = digest(request.record())
        source_id = "literature-source-" + digest([result["snapshot_id"], binding.model_dump(mode="json")])
        # Unknown publication status is not unknown experimental-data exposure.
        self.broker.register_source(source_id, content_sha256=digest(result), roots=(digest(binding.model_dump(mode="json")),),
                                    classification="structural")
        def build(reservation, context, current, source):
            return {**deepcopy(result), "query_receipt": {"format": "devbench.literature-query-receipt/1",
                "disclosure_reservation_id": reservation, "query_sha256": query_sha256,
                "snapshot_id": result["snapshot_id"], "policy_sha256": current.sha256(),
                "claim_binding_sha256": digest(binding.model_dump(mode="json")),
                "content_identity_sha256": self.state()["snapshots"][result["snapshot_id"]]["content_identity_sha256"]}}
        def guard(db):
            state = _state(db, self.lineage_id)
            ref = state["snapshots"][result["snapshot_id"]]
            snapshot = _retained_snapshot(ref, self.root, self.limits.response_bytes)
            current = _fence(self.authority, db, self.lineage_id)
            if current["cancelled"] or current != snapshot["fence"]:
                raise OperationBlocked("literature_execution_fence_changed")
            if result["status"] != "unavailable":
                _require_snapshot_current(state, ref)
        try:
            return self.broker.release_query_page(context_id, source_id, query_sha256,
                                                   policy.sha256(), build, _guard=guard)
        except OperationBlocked:
            def blocked(state, db):
                operations = {identity: {**item, "status": "blocked", "result": {
                    **item["result"], "status": "unavailable", "failure": "OperationBlocked"}}
                    for identity, item in state["operations"].items()
                    if (item.get("result") or {}).get("snapshot_id") == result["snapshot_id"]
                    and item["spec"]["context_id"] == context_id
                    and item["spec"]["claim_binding"] == binding.model_dump(mode="json")}
                return {"operations": operations}, None
            self._write("delivery_blocked", blocked)
            raise

    def replay(self, snapshot_id, *, require_current=False):
        from pydantic import TypeAdapter
        TypeAdapter(Sha256).validate_python(snapshot_id)
        state = self.state(); ref = state["snapshots"].get(snapshot_id)
        if ref is None:
            raise ValueError("Unknown frozen Literature snapshot")
        value = _retained_snapshot(ref, self.root, self.limits.response_bytes)
        if value["failure"] is None:
            request = LiteratureRequest.model_validate_json(json.dumps(value["request"]))
            extraction, search, metadata = self._decode(request, value["decode_responses"])
            if (extraction, search, metadata) != (value["extraction"], value["search"], value["metadata"]):
                raise ValueError("Frozen Literature extraction no longer reproduces")
        if require_current:
            if state["grant"]["implementation_sha256"] != _implementation_identity():
                raise ValueError("Literature implementation binding is stale")
            _require_snapshot_current(state, ref)
            with self.ledger._database() as db:
                db.execute("BEGIN")
                current = _fence(self.authority, db, self.lineage_id)
            if current["cancelled"] or current != value["fence"]:
                raise ValueError("Literature snapshot execution fence is stale")
        return value

    def assess(self, assessment, *, binding, claim, context_id):
        """Bind an attributed interpretation; exact text is not an entailment certificate."""
        assessment = LiteratureClaimAssessment.model_validate(assessment)
        binding, claim = LiteratureClaimBinding.model_validate(binding), ClaimSpec.model_validate(claim)
        context, _ = self._context(context_id)
        if context["role"] != "challenger" or context["phase"] != "narrative_reveal":
            raise PermissionError("Claim support is assessed after independent evidence-first review")
        if (claim.claim_id != binding.claim_id or digest(claim.model_dump(mode="json")) != binding.claim_sha256
                or (assessment.claim_id, assessment.claim_sha256, assessment.evidence_sha256)
                != (binding.claim_id, binding.claim_sha256, binding.evidence_sha256)):
            raise ValueError("Literature assessment differs from the exact claim/evidence revision")
        snapshot = self.replay(assessment.snapshot_id, require_current=True)
        state = self.state(); ref = state["snapshots"][assessment.snapshot_id]
        eligible_contexts = {context_id, *context["parents"]}
        if not any((item.get("result") or {}).get("snapshot_id") == assessment.snapshot_id
                   and item["spec"]["context_id"] in eligible_contexts for item in state["operations"].values()):
            raise PermissionError("Snapshot was not disclosed to this assessment context")
        extraction = snapshot["extraction"]
        if snapshot["access"] not in {"abstract", "full_text"} or extraction is None:
            raise ValueError("Metadata and search success are not passage support")
        passages = {item["passage_id"] for item in extraction["passages"]}
        if len(set(assessment.passage_ids)) != len(assessment.passage_ids) or not set(assessment.passage_ids) <= passages:
            raise ValueError("Assessment cites an absent or mismatched exact passage")
        alignment = _context_alignment(binding.context, assessment.source_context)
        reasons = ["source_integrity_is_not_semantic_entailment"]
        applicable = all(value == "match" for value in alignment.values())
        if not applicable:
            reasons.append("biological_context_mismatch_or_unknown")
        status = _status_view(extraction, snapshot["metadata"])
        retracted = any(state["retracted"].get(key) for key in ref["identifiers"])
        if retracted:
            reasons.append("known_retracted_source")
        if status["coverage"] != "comprehensive_authoritative_notice_coverage":
            reasons.append("status_coverage_unknown")
        if any(item["status"] == "completed" and item["spec"]["claim_binding"] == binding.model_dump(mode="json")
               and item["result"]["status"] == "unavailable" for item in state["operations"].values()):
            reasons.append("additional_retrieval_unavailable_frozen_evidence_only")
        if snapshot["access"] == "abstract":
            reasons.append("abstract_only")
        if snapshot["status"] == "truncated":
            reasons.append("source_passages_truncated")
        eligible = (assessment.relation == "supports" and applicable and not retracted
                    and not (binding.requires_verified_status and "status_coverage_unknown" in reasons))
        record = {"format": "devbench.literature-assessment/1", "assessment": assessment.model_dump(mode="json"),
            "binding": binding.model_dump(mode="json"), "alignment": alignment, "eligible": eligible,
            "limitations": reasons, "semantic_entailment_verified": False, "authorship_authenticated": False,
            "actor_context": context_id, "snapshot_signature": ref["signature"], "status_signature": ref["status_signature"],
            "fence": snapshot["fence"],
            "notice_basis": {key: state["notices"].get(key, []) for key in ref["identifiers"]},
            "evidence_basis": _claim_evidence_basis(state, binding),
            "dependencies": sorted({assessment.snapshot_id, *{item["result"]["snapshot_id"] for item in state["operations"].values()
                if item["status"] == "completed" and item["spec"]["claim_binding"] == binding.model_dump(mode="json")
                and item["spec"]["context_id"] in eligible_contexts and item["result"]["status"] != "unavailable"}})}
        identity = digest(record)
        def save(current, db):
            if _claim_evidence_basis(current, binding) != record["evidence_basis"]:
                raise OperationConflict("Literature claim evidence changed during assessment")
            _require_snapshot_current(current, ref)
            _retained_snapshot(ref, self.root, self.limits.response_bytes)
            if (record["notice_basis"] != {key: current["notices"].get(key, []) for key in ref["identifiers"]}
                    or eligible and any(current["retracted"].get(key) for key in ref["identifiers"])):
                raise OperationConflict("Literature publication status changed during assessment")
            fence = _fence(self.authority, db, self.lineage_id)
            if fence["cancelled"] or fence != snapshot["fence"]:
                raise OperationBlocked("literature_execution_fence_changed")
            use = dict(current["claims"][_claim_key(binding)])
            if use["binding"] != binding.model_dump(mode="json"):
                raise ValueError("Literature claim revision changed")
            use["assessment_id"] = identity
            return {"assessments": {identity: record}, "claims": {_claim_key(binding): use}}, {**record, "assessment_id": identity}
        return self._write("assess_claim", save)


def _require_snapshot_current(state, ref):
    if state["heads"].get(ref["source_key"]) != ref["signature"]:
        raise ValueError("Literature source snapshot is stale")
    if ("document_signature" in ref and
            state["heads"].get("document:" + ref["publication_key"]) != ref["document_signature"]):
        raise ValueError("Literature source snapshot is stale")
    if ref["publication_key"] is not None and state["heads"].get("status:" + ref["publication_key"]) != ref["status_signature"]:
        raise ValueError("Literature publication status snapshot is stale")


def literature_use_current(lifecycle, entity_id, review_binding_sha256, *, connection=None, assessment_id=None):
    """Claim-only publication guard; absent Literature obligations preserve legacy uses."""
    if connection is None:
        with lifecycle.ledger._database() as db:
            db.execute("BEGIN")
            return literature_use_current(lifecycle, entity_id, review_binding_sha256, connection=db, assessment_id=assessment_id)
    require_same_ledger_transaction(connection, lifecycle.ledger)
    state = _state(connection, lifecycle.lineage_id)
    review = lifecycle._state(connection)
    relevant = [use for use in state["claims"].values() if entity_id is None
                or entity_id in lifecycle._scope(review, (use["binding"]["claim_id"],))]
    current_uses = [use for use in relevant if use["binding"]["review_binding_sha256"] == review_binding_sha256]
    if assessment_id is not None and not any(use["binding"]["claim_id"] == entity_id
            and use["assessment_id"] == assessment_id for use in current_uses):
        return False
    if {use["binding"]["claim_id"] for use in relevant} - {use["binding"]["claim_id"] for use in current_uses}:
        return False
    for use in current_uses:
        binding = use["binding"]
        assessment = state["assessments"].get(use["assessment_id"])
        if assessment is None or not assessment["eligible"]:
            return False
        if state["grant"]["implementation_sha256"] != _implementation_identity():
            return False
        if assessment["evidence_basis"] != _claim_evidence_basis(state, binding):
            return False
        fence = _fence(lifecycle.authority, connection, lifecycle.lineage_id)
        if fence["cancelled"] or fence != assessment["fence"]:
            return False
        ref = state["snapshots"][assessment["assessment"]["snapshot_id"]]
        try:
            _require_snapshot_current(state, ref)
            for snapshot_id in assessment["dependencies"]:
                dependency = state["snapshots"][snapshot_id]
                _require_snapshot_current(state, dependency)
                _retained_snapshot(dependency, state["grant"]["root"], state["grant"]["limits"]["response_bytes"])
        except (ValueError, OSError):
            return False
        if any(state["retracted"].get(key) for key in ref["identifiers"]):
            return False
        if assessment["notice_basis"] != {key: state["notices"].get(key, []) for key in ref["identifiers"]}:
            return False
        if any(item["status"] == "pending" and item["spec"]["claim_binding"] == binding for item in state["operations"].values()):
            return False
    return True


@dataclass(frozen=True)
class EntitledLiteratureQuery:
    """Independent retrieval authority kept in the host, never in selector JSON."""
    handle_id: str
    service: LiteratureService
    context_id: str
    bindings: tuple[LiteratureClaimBinding, ...]

    def __post_init__(self):
        if type(self.service) is not LiteratureService or not self.bindings:
            raise TypeError("Use a coordinator-owned Literature service and exact claims")
        context, policy = self.service._context(self.context_id)
        if context["role"] != "challenger" or context["phase"] != "publication_initial":
            raise PermissionError("Independent counter-search requires the initial challenger phase")
        if len({item.claim_id for item in self.bindings}) != len(self.bindings):
            raise ValueError("Duplicate Literature claim entitlement")
        descriptor = self.descriptor()
        self.service.broker.register_source(descriptor.source_id, content_sha256=descriptor.grant_sha256,
            roots=tuple(sorted({digest(item.model_dump(mode="json", exclude={"review_binding_sha256"})) for item in self.bindings})),
            classification="structural")

    def descriptor(self):
        _, policy = self.service._context(self.context_id)
        identity = digest({"grant": self.service.grant, "context_id": self.context_id,
                           "bindings": [item.model_dump(mode="json", exclude={"review_binding_sha256"}) for item in self.bindings]})
        return LiteratureQueryHandle(handle_id=self.handle_id, source_id="literature-grant-" + identity,
            context_id=self.context_id, policy_sha256=policy.sha256(), grant_sha256=identity,
            capability_sha256=digest(self.service.capability.identity()), claims=self.bindings)

    def query(self, request, *, claim_id, request_id, context_id=None):
        binding = next((item for item in self.bindings if item.claim_id == claim_id), None)
        if binding is None:
            raise PermissionError("Literature query is outside the entitled claim scope")
        context_id = self.context_id if context_id is None else context_id
        context, _ = self.service._context(context_id)
        if context_id != self.context_id and (context["role"] != "challenger"
                or context["phase"] != "narrative_reveal" or self.context_id not in context["parents"]):
            raise PermissionError("Literature reveal context lacks the entitled initial review ancestry")
        return self.service.execute(LiteratureRequest.model_validate_json(json.dumps(request)),
            request_id=request_id, context_id=context_id, binding=binding)

    def assess(self, assessment, *, claim, context_id):
        typed = LiteratureClaimAssessment.model_validate_json(json.dumps(assessment))
        binding = next((item for item in self.bindings if item.claim_id == typed.claim_id), None)
        if binding is None:
            raise PermissionError("Literature assessment is outside the entitled claim scope")
        return self.service.assess(typed, binding=binding, claim=claim, context_id=context_id)


@dataclass(frozen=True)
class EntitledControllerLiteratureQuery(EntitledLiteratureQuery):
    """Targeted controller retrieval; independent assessment stays challenger-only."""

    def __post_init__(self):
        if type(self.service) is not LiteratureService or not self.bindings:
            raise TypeError("Use a coordinator-owned Literature service and exact claims")
        context, _ = self.service._context(self.context_id)
        if context["role"] != "controller" or context["phase"] != "analysis":
            raise PermissionError("Controller Literature requires its analysis context")
        if len({item.claim_id for item in self.bindings}) != len(self.bindings):
            raise ValueError("Duplicate Literature claim entitlement")
        descriptor = self.descriptor()
        self.service.broker.register_source(descriptor.source_id, content_sha256=descriptor.grant_sha256,
            roots=tuple(sorted({digest(item.model_dump(mode="json", exclude={"review_binding_sha256"}))
                                for item in self.bindings})), classification="structural")

    def query(self, request, *, claim_id, request_id, context_id=None):
        if context_id not in {None, self.context_id}:
            raise PermissionError("Controller Literature cannot change its entitled context")
        return super().query(request, claim_id=claim_id, request_id=request_id, context_id=self.context_id)

    def assess(self, assessment, *, claim, context_id):
        raise PermissionError("Controller retrieval cannot assess claim support or clear a review hold")
