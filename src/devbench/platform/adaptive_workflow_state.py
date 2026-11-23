"""Adaptive DAG records, topology validation and checkpoint projection."""

from collections import defaultdict, deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Annotated, Literal

from pydantic import Field, model_validator

from devbench.adaptive.models import AdaptiveRecord, ConsumerPurpose, Port
from devbench.core.procedure_profile import ADMITTED_RESEARCH, ProcedureProfile
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict

Identity = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Use = Literal["compute", "claim"]
BasisKind = Literal[
    "fact",
    "membership",
    "learned_state",
    "code",
    "environment",
    "source",
    "review_basis",
    "exposure_basis",
]
RevisionReason = Literal[
    "diagnostic_revisit", "method_change", "design_change", "retry", "claim_revision"
]
RetirementReason = Literal["irrelevant", "duplicate", "superseded", "scope_change"]


class BasisUse(AdaptiveRecord):
    basis_id: Identity
    use: Use = "compute"


class ArtifactVersion(AdaptiveRecord):
    version_id: Identity
    sha256: Digest
    kind: str
    representation: str
    biological_unit: str
    unit_membership_sha256: Digest
    graph_sha256: Digest | None = None
    measurement_history: tuple[str, ...] = ()
    label_origin: Literal["none", "independent", "rna_derived", "unknown"] = "none"
    label_qualification_sha256: Digest | None = None
    selection_status: Literal["none", "selected", "exposed", "unknown"] = "none"
    learned_scope: Literal["none", "partition_only", "full_cohort", "unknown"] = "none"
    permitted_uses: tuple[ConsumerPurpose, ...] = ("exploratory", "checked_observation")

    @model_validator(mode="after")
    def graph_identity(self):
        if (self.kind == "graph") != (self.graph_sha256 is not None):
            raise ValueError("Graph versions require their own graph digest")
        return self


class InputLink(AdaptiveRecord):
    port: Port
    source_id: Identity
    source_port: Identity | None = None  # None pins an immutable artifact version.
    use: Use = "compute"


class AdaptiveStep(AdaptiveRecord):
    step_id: Identity
    branch_id: Identity
    operation_id: Identity
    profile: ProcedureProfile
    environment_sha256: Digest
    parameters_sha256: Digest
    inputs: tuple[InputLink, ...] = Field(default=(), max_length=16)
    outputs: tuple[Port, ...] = Field(min_length=1, max_length=16)
    basis: tuple[BasisUse, ...] = Field(default=(), max_length=32)
    purpose: ConsumerPurpose = "exploratory"
    role: Use = "compute"
    regime: Literal["B-I", "B-P", "S-I", "S-P", "P-P"] | None = None
    primary: bool = False
    comparison: bool = False
    held_out: bool = False
    oci_method_version_id: Digest | None = None
    shared_units: bool = True

    @model_validator(mode="after")
    def declared_capability(self):
        capability = next(
            (
                item
                for item in self.profile.operations
                if item.operation_id == self.operation_id
            ),
            None,
        )
        if capability is None or any(
            port.artifact_kind not in capability.output_kinds for port in self.outputs
        ):
            raise ValueError("Step exceeds the declared procedure operation capability")
        for names in (
            [link.port.name for link in self.inputs],
            [port.name for port in self.outputs],
            [basis.basis_id for basis in self.basis],
        ):
            if len(names) != len(set(names)):
                raise ValueError("Duplicate step port or basis")
        if (
            self.purpose == "qualified_inference"
            and "qualified_procedure_result" not in self.profile.permitted_claims
        ):
            raise ValueError("Primary inference requires a qualified procedure profile")
        claims = self.profile.permitted_claims
        oci_research = (
            ADMITTED_RESEARCH in claims and self.oci_method_version_id is not None
        )
        if (
            oci_research
            or self.purpose == ADMITTED_RESEARCH
            and (
                self.held_out
                or self.regime is None
                or ADMITTED_RESEARCH not in claims
                or "qualified_procedure_result" in claims
            )
        ):
            raise ValueError("Research inference requires a scoped Tier A procedure")
        return self


class ArtifactPin(AdaptiveRecord):
    port: Identity
    version_id: Identity
    sha256: Digest
    graph_sha256: Digest | None


class StepReceipt(AdaptiveRecord):
    job_id: Identity
    request_sha256: Digest
    consumed: tuple[ArtifactPin, ...] = Field(max_length=16)
    status: Literal["succeeded", "failed"]
    outputs: dict[Identity, ArtifactVersion]
    reason: Identity | None = None
    wall_millis: int = Field(default=0, ge=0, strict=True)
    cleanup_state: Literal["confirmed", "unknown", "failed"] = "unknown"
    io_sha256: Digest | None = None

    @model_validator(mode="after")
    def completed(self):
        if len(self.outputs) > 16 or (
            self.status == "failed" and (self.outputs or not self.reason)
        ):
            raise ValueError("Failed receipts cannot supply accepted outputs")
        if len({artifact.version_id for artifact in self.outputs.values()}) != len(
            self.outputs
        ):
            raise ValueError("Output artifact version IDs must be unique")
        return self


def _dag_edges(nodes):
    children = defaultdict(list)
    incoming = dict.fromkeys(nodes, 0)
    for identity, node in nodes.items():
        if identity != node.step_id:
            raise ValueError("DAG step identity mismatch")
        for link in node.inputs:
            if link.source_port is None:
                continue
            source = nodes.get(link.source_id)
            port = (
                next((p for p in source.outputs if p.name == link.source_port), None)
                if source
                else None
            )
            if port is None or port.artifact_kind != link.port.artifact_kind:
                raise ValueError(
                    "DAG edge requires a compatible declared producer port"
                )
            children[link.source_id].append(identity)
            incoming[identity] += 1
    return children, incoming


def validate_dag(nodes, *, maximum_steps):
    """Check typed ports and topologically sort both computational and claim edges."""
    if len(nodes) > maximum_steps:
        raise ValueError("Adaptive DAG exceeds the admitted step bound")
    children, incoming = _dag_edges(nodes)
    frontier = deque(sorted(step for step, count in incoming.items() if not count))
    ordered = []
    while frontier:
        step = frontier.popleft()
        ordered.append(step)
        for child in sorted(children[step]):
            incoming[child] -= 1
            if not incoming[child]:
                frontier.append(child)
    if len(ordered) != len(nodes):
        raise ValueError("Adaptive DAG contains a cycle")
    return tuple(ordered)


@dataclass
class _State:
    nodes: dict = field(default_factory=dict)
    generations: dict = field(default_factory=dict)
    basis: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    derived_parents: dict[str, list[str]] = field(default_factory=dict)
    artifacts: dict = field(default_factory=dict)
    dispatches: dict = field(default_factory=dict)
    outcomes: dict = field(default_factory=dict)
    latest: dict = field(default_factory=dict)
    retired: dict = field(default_factory=dict)
    holds: dict = field(default_factory=dict)
    sweeps: dict = field(default_factory=dict)
    tombstones: dict = field(default_factory=dict)
    claim_stale: dict = field(default_factory=dict)
    stale_versions: set = field(default_factory=set)
    comparison_versions: set = field(default_factory=set)
    controller: dict = field(
        default_factory=lambda: {
            "binding": None,
            "decisions": [],
            "pending": None,
            "outcome": None,
        }
    )

    def checkpoint_state(self):
        document = {
            **vars(self),
            "nodes": {
                key: node.model_dump(mode="json") for key, node in self.nodes.items()
            },
            "latest": [
                [*key, outcome["attempt_id"]] for key, outcome in self.latest.items()
            ],
            "tombstones": [[*key, reason] for key, reason in self.tombstones.items()],
            "claim_stale": [[*key, reason] for key, reason in self.claim_stale.items()],
            "stale_versions": sorted(self.stale_versions),
            "comparison_versions": sorted(self.comparison_versions),
        }
        if not self.derived_parents:
            document.pop("derived_parents")
        return document

    @classmethod
    def from_checkpoint(cls, document):
        return cls(
            **{
                **document,
                "nodes": {
                    key: AdaptiveStep.model_validate(node)
                    for key, node in document["nodes"].items()
                },
                "artifacts": {
                    key: (artifact, None if producer is None else tuple(producer))
                    for key, (artifact, producer) in document["artifacts"].items()
                },
                "latest": {
                    (step, generation): document["outcomes"][attempt]
                    for step, generation, attempt in document["latest"]
                },
                "tombstones": {
                    (step, generation): reason
                    for step, generation, reason in document["tombstones"]
                },
                "claim_stale": {
                    (step, generation): reason
                    for step, generation, reason in document["claim_stale"]
                },
                "stale_versions": set(document["stale_versions"]),
                "comparison_versions": set(document["comparison_versions"]),
            }
        )

    def apply_verified(self, row):
        record = row["record"]
        self.apply(record["event"], record["state"])

    def outcome(self, step_id):
        return self.latest.get((step_id, self.generations[step_id]))

    def artifact_ancestors(
        self,
        versions: Iterable[str],
        *,
        include_execution: bool = False,
    ) -> Iterator[str]:
        pending = list(versions)
        visited: set[str] = set()
        while pending:
            version = pending.pop()
            if version in visited:
                continue
            if len(visited) >= 512:
                raise OperationBlocked("adaptive_artifact_ancestry_exceeds_bound")
            visited.add(version)
            yield version
            pending.extend(self.derived_parents.get(version, ()))
            if include_execution and (producer := self.artifacts[version][1]):
                outcome = self.latest[producer]
                pins = self.dispatches[outcome["attempt_id"]]["envelope"]["pins"]
                pending.extend(pin["version_id"] for pin in pins)

    def _taint(self, versions):
        pending = deque(versions)
        if not pending:
            return
        children = defaultdict(set)
        for version, parents in self.derived_parents.items():
            for parent in parents:
                children[parent].add(version)
        for attempt_id, outcome in self.outcomes.items():
            pins = self.dispatches[attempt_id]["envelope"]["pins"]
            outputs = {item["version_id"] for item in outcome["outputs"].values()}
            for pin in pins:
                children[pin["version_id"]].update(outputs)
        while pending:
            version = pending.popleft()
            if version in self.stale_versions:
                continue
            self.stale_versions.add(version)
            pending.extend(children[version])

    def _invalidation_edges(self, retain_comparisons):
        children = defaultdict(list)
        for step_id, node in self.nodes.items():
            for link in node.inputs:
                if link.source_port is not None:
                    children[link.source_id].append((step_id, link.use))
                elif not (retain_comparisons and node.comparison):
                    _, producer = self.artifacts[link.source_id]
                    if producer is not None:
                        children[producer[0]].append((step_id, link.use))
        return children

    def _stale_input_seeds(self):
        return (
            (step, link.use)
            for step, node in self.nodes.items()
            for link in node.inputs
            if link.source_port is None and link.source_id in self.stale_versions
        )

    def invalidate(self, seeds, reason, *, retain_comparisons=False):
        uses = {}
        children = self._invalidation_edges(retain_comparisons)
        pending = deque(seeds)
        while pending:
            step_id, use = pending.popleft()
            if use == "claim" and self.nodes[step_id].role == "claim":
                use = "compute"
            if uses.get(step_id) == "compute" or uses.get(step_id) == use:
                continue
            uses[step_id] = use
            for child, edge in children[step_id]:
                pending.append(
                    (child, "compute" if use == edge == "compute" else "claim")
                )
            if use == "compute":
                outcome = self.outcome(step_id)
                if outcome and self._invalidate_outputs(outcome, retain_comparisons):
                    pending.extend(self._stale_input_seeds())
        for step_id, use in uses.items():
            key = (step_id, self.generations[step_id])
            if use == "compute":
                self.tombstones[key] = reason
                self.generations[step_id] += 1
            else:
                self.claim_stale[key] = reason

    def _invalidate_outputs(self, outcome, retain_comparisons):
        versions = {value["version_id"] for value in outcome["outputs"].values()}
        if retain_comparisons:
            self.comparison_versions.update(versions)
            # A current-mask derivation must be regenerated even when the old
            # worker output remains available for a declared comparison.
            versions = {
                item
                for item, parents in self.derived_parents.items()
                if versions.intersection(parents)
            }
            if not versions:
                return False
        self._taint(versions)
        return True

    def apply(self, event, record):
        if event == "basis":
            self._apply_basis(record)
        elif event == "artifact":
            self._apply_artifact(record)
        elif event == "step":
            self._apply_step(record)
        elif event == "retire":
            self._apply_retirement(record)
        elif event == "hold":
            self._apply_hold(record)
        elif event == "sweep":
            self.sweeps[record["sweep_id"]] = record["members"]
        elif event == "dispatch":
            self.dispatches[record["token"]["attempt_id"]] = record
        elif event == "outcome":
            self._apply_outcome(record)
        elif event == "controller":
            self._apply_controller(record)
        else:
            raise OperationConflict("Unknown adaptive authority event")

    def _apply_artifact(self, record):
        artifact = record["artifact"]
        version = artifact["version_id"]
        self.artifacts[version] = (artifact, None)
        self.sources[version] = record["basis"]
        if parents := record.get("parents"):
            self.derived_parents[version] = parents

    def _apply_basis(self, record):
        key = record["basis_id"]
        old = self.basis.get(key)
        self.basis[key] = record
        if old is None or old["sha256"] == record["sha256"]:
            return
        seeds = [
            (step, ref.use)
            for step, node in self.nodes.items()
            for ref in node.basis
            if ref.basis_id == key
        ]
        versions = {version for version, refs in self.sources.items() if key in refs}
        for attempt, result in self.outcomes.items():
            pin = self.dispatches[attempt]["envelope"]["basis"].get(key)
            if pin and pin["use"] == "compute" and pin["sha256"] != record["sha256"]:
                versions.update(
                    value["version_id"] for value in result["outputs"].values()
                )
        self._taint(versions)
        seeds.extend(self._stale_input_seeds())
        self.invalidate(seeds, record["kind"] + "_corrected")

    def _apply_step(self, record):
        node = AdaptiveStep.model_validate(record["step"])
        if node.step_id in self.nodes:
            self.invalidate(
                [(node.step_id, "compute")],
                record["reason"],
                retain_comparisons=record["reason"] == "diagnostic_revisit",
            )
        else:
            self.generations[node.step_id] = 1
        self.nodes[node.step_id] = node

    def _apply_retirement(self, record):
        branch = record["branch_id"]
        self.retired[branch] = record
        members = {
            step for step, node in self.nodes.items() if node.branch_id == branch
        }
        seeds = [(step, "compute") for step in sorted(members)]
        scope_revoked = record["reason"] == "scope_change"
        if scope_revoked:
            # A scope change also withdraws earlier versions retained for
            # comparison before the branch was retired.
            self._taint(
                version
                for version, (_, producer) in self.artifacts.items()
                if producer is not None and producer[0] in members
            )
            seeds.extend(self._stale_input_seeds())
        self.invalidate(seeds, record["reason"], retain_comparisons=not scope_revoked)

    def _apply_hold(self, record):
        if record["reason"] is None:
            self.holds.pop(record["step_id"], None)
        else:
            self.holds[record["step_id"]] = record

    def _apply_outcome(self, record):
        attempt = record["attempt_id"]
        envelope = self.dispatches[attempt]["envelope"]
        key = (envelope["step"]["step_id"], envelope["generation"])
        self.outcomes[attempt] = record
        self.latest[key] = record
        for artifact in record["outputs"].values():
            self.artifacts[artifact["version_id"]] = (artifact, key)

    def _apply_controller(self, record):
        if record.get("format") == "devbench.controller-change/1":
            for key in ("binding", "pending", "outcome"):
                if key in record:
                    self.controller[key] = record[key]
            if "decision" in record:
                self.controller["decisions"].append(record["decision"])
        else:
            self.controller = record  # Preserve historical snapshot events.
