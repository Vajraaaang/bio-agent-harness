"""Composable exploratory execution.

Registered operations produce experimental artifacts. They do not qualify an
inferential claim, and they do not execute arbitrary code or contact a network.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from devbench.adaptive.admission import admit_consumer
from devbench.adaptive.ledger import InvestigationLedger
from devbench.adaptive.models import (
    AnalysisSpec,
    DependencyEdge,
    DerivedArtifact,
    ExploratoryCodeSpec,
    GraphSemantics,
    InspectionSpec,
    PlanVersion,
    PlotSpec,
    Port,
    PredictiveModelSpec,
    QuestionContract,
    ResourceGrant,
    StepSpec,
    TransformSpec,
    ValidationSpec,
    seal,
)
from devbench.engine.exploratory_oci import ExploratoryOCI  # noqa: F401 — compatibility re-export
from devbench.engine.provenance import canonical_json

TOLERANCE = 1e-9
LOG1P_SOURCE = "log1p(counts / library_size * 10000)"
CODE_DIGEST = hashlib.sha256(LOG1P_SOURCE.encode("utf-8")).hexdigest()
GRANT = ResourceGrant(cpu_seconds=30, memory_mb=512, numerical_jobs=1)
EXPERIMENTAL_USES = ("exploratory", "checked_observation")


def resolve_code(spec: ExploratoryCodeSpec) -> str | None:
    """Return a registered operation, or None when the frozen code cannot run."""
    if "network" in spec.effects or "host_write" in spec.effects or "package_install" in spec.effects:
        return None
    if spec.code_digest == CODE_DIGEST and spec.interpreter == "registered-python":
        return "log1p_cpm"
    return None


def port_conflict(node: StepSpec, parents: dict[str, DerivedArtifact]) -> str | None:
    return _port_problem(node, parents)


def promotion_allowed(artifact: DerivedArtifact) -> bool:
    decision = admit_consumer(artifact, "qualified_inference")
    return decision.claim_eligible


def inspect_figure(
    svg: str, *, source_artifact_id: str, sample_ids: tuple[str, ...], model_accepts_images: bool
) -> dict:
    """Return a provenance-bound figure reading. A missing image channel is not visual review."""
    bound = source_artifact_id in svg and all(f'data-sample="{sample}"' in svg for sample in sample_ids)
    return {
        "source_artifact_id": source_artifact_id,
        "samples_bound": bound,
        "model_can_receive_image": model_accepts_images,
        "visual_inspection_claimed": False,
        "biological_topology_claimed": False,
    }


def build_chain_plan() -> PlanVersion:
    """Acceptance chain: several artifact kinds plus one blocked inferential branch."""
    edges: list[DependencyEdge] = []

    def link(source: str, target: str, target_port: str) -> None:
        edges.append(
            DependencyEdge(
                edge_id=f"{source}-{target}-{target_port}",
                source_id=source,
                target_id=target,
                kind="compute",
                reason="typed input",
                source_port="value",
                target_port=target_port,
            )
        )

    link("inspect", "transform", "counts")
    link("transform", "model", "matrix")
    link("model", "predict", "model")
    link("transform", "predict", "matrix")
    link("transform", "graph", "matrix")
    link("graph", "scores", "graph")
    link("scores", "members", "scores")
    link("inspect", "check", "counts")
    link("transform", "check", "matrix")
    link("transform", "figure", "matrix")
    link("fit", "follow", "value")
    nodes = (
        _node(
            "inspect",
            InspectionSpec(
                payload_type="inspection",
                selectors=("counts",),
                dimensions=("genes", "samples"),
                known_provenance=("nonnegative-integer-file",),
            ),
            (),
            (),
            "read the count file",
            (Port(name="value", artifact_kind="counts"),),
        ),
        _node(
            "transform",
            TransformSpec(
                payload_type="transform",
                algorithm="log1p_cpm",
                axis_mappings=("genes", "samples"),
                invariants=("nonnegative counts",),
                output_representation="continuous_matrix",
            ),
            ("inspect",),
            (Port(name="counts", artifact_kind="counts"),),
            "library-size transform",
            (Port(name="value", artifact_kind="continuous_matrix"),),
        ),
        _node(
            "model",
            PredictiveModelSpec(
                payload_type="predictive_model",
                training_objective="nearest_centroid",
                label_provenance="supplied_annotation",
                grouping="sample",
                pipeline="log1p_cpm",
                tuning_rule="none",
                partition_policy="in_sample",
                generalization_scope="not_held_out_donors",
            ),
            ("transform",),
            (Port(name="matrix", artifact_kind="continuous_matrix"),),
            "fit centroids on supplied labels",
            (Port(name="value", artifact_kind="model"),),
        ),
        _node(
            "predict",
            TransformSpec(
                payload_type="transform",
                algorithm="apply_centroid",
                axis_mappings=("samples",),
                invariants=("trained centroids",),
                output_representation="predictions",
                learned_state_dependencies=("model",),
            ),
            ("model", "transform"),
            (
                Port(name="model", artifact_kind="model"),
                Port(name="matrix", artifact_kind="continuous_matrix"),
            ),
            "apply centroids in sample",
            (Port(name="value", artifact_kind="predictions"),),
        ),
        _node(
            "graph",
            TransformSpec(
                payload_type="transform",
                algorithm="knn_graph",
                axis_mappings=("samples",),
                invariants=("no self loops",),
                output_representation="graph",
            ),
            ("transform",),
            (Port(name="matrix", artifact_kind="continuous_matrix"),),
            "sample neighborhood graph",
            (Port(name="value", artifact_kind="graph"),),
        ),
        _node(
            "scores",
            TransformSpec(
                payload_type="transform",
                algorithm="degree_score",
                axis_mappings=("samples",),
                invariants=("weights from the graph",),
                output_representation="scores",
            ),
            ("graph",),
            (Port(name="graph", artifact_kind="graph"),),
            "weighted degree",
            (Port(name="value", artifact_kind="scores"),),
        ),
        _node(
            "members",
            TransformSpec(
                payload_type="transform",
                algorithm="rank_membership",
                axis_mappings=("samples",),
                invariants=("primary cohort unchanged",),
                output_representation="membership_view",
            ),
            ("scores",),
            (Port(name="scores", artifact_kind="scores"),),
            "exploratory high-degree view",
            (Port(name="value", artifact_kind="membership_view"),),
        ),
        _node(
            "check",
            ValidationSpec(
                payload_type="validation",
                checks=("recompute_log1p",),
                controls=("source counts",),
                criteria=(f"max_abs<={TOLERANCE}",),
                dependence_limits=("same finite transform",),
                claim_obligations=("no inferential promotion",),
            ),
            ("inspect", "transform"),
            (
                Port(name="counts", artifact_kind="counts"),
                Port(name="matrix", artifact_kind="continuous_matrix"),
            ),
            "recompute the transform from counts",
            (Port(name="value", artifact_kind="table"),),
        ),
        _node(
            "figure",
            PlotSpec(
                payload_type="plot",
                source_artifact_ids=("transform",),
                transformations=("log1p_cpm",),
                encodings=("gene0", "gene1", "sample"),
                diagnostic_question="are sample coordinates bound to the matrix",
                panel_binding="one point per sample",
                verification_policy="source_bound_svg",
            ),
            ("transform",),
            (Port(name="matrix", artifact_kind="continuous_matrix"),),
            "source-bound sample figure",
            (Port(name="value", artifact_kind="figure"),),
        ),
        _node(
            "fit",
            AnalysisSpec(
                payload_type="analysis",
                method_id="unqualified_covariance",
                method_version="0",
                representation="counts",
                cohort="study",
                design="repeated measures",
                contrast="condition",
                normalization="unspecified",
                testing_family="genes",
                qualification_regime="unsupported-covariance",
                exposure_policy="not_qualified",
            ),
            (),
            (),
            "blocked inferential request",
            (Port(name="value", artifact_kind="table"),),
        ),
        _node(
            "follow",
            TransformSpec(
                payload_type="transform",
                algorithm="log1p_cpm",
                axis_mappings=("genes",),
                invariants=("depends on the blocked fit",),
                output_representation="continuous_matrix",
            ),
            ("fit",),
            (Port(name="value", artifact_kind="table"),),
            "descendant of the blocked fit",
            (Port(name="value", artifact_kind="continuous_matrix"),),
        ),
    )
    return seal(
        PlanVersion,
        plan_id="plan-explore",
        revision=0,
        parent_revision=None,
        question_id="q-explore",
        nodes=nodes,
        edges=tuple(edges),
        rationale="exploratory chain beside a blocked inference",
        expected_observation="typed artifacts and an explicit qualification gap",
        claim_consequences=("no qualified expression claim",),
    )


def exploratory_question() -> QuestionContract:
    return seal(
        QuestionContract,
        question_id="q-explore",
        objective="compare derived representations without a qualified inferential claim",
        authorized_data=("counts",),
        population="study samples",
        biological_unit_context="sample",
        intended_claims=(),
        exploratory_envelope="transforms, centroids, graphs, scores, subset views, and figures",
        constraint_sources=("exploratory question",),
    )


def execute_plan(ledger: InvestigationLedger, bridge, session_id: str, source_path: Path) -> None:
    """Retain the prototype entry point while blocking the unavailable backend."""
    raise ImportError("Legacy adaptive exploration is quarantined because its backend source is missing")


def replay_matches(left: Path, right: Path, tolerance: float = TOLERANCE) -> bool:
    """Compare two clean executions by declared numeric tolerance."""
    left_payloads = _load_payloads(left)
    right_payloads = _load_payloads(right)
    if set(left_payloads) != set(right_payloads):
        return False
    for name, payload in left_payloads.items():
        if not _within_tolerance(payload, right_payloads[name], tolerance):
            return False
    return True


def _node(step_id, operation, dependencies, inputs, purpose, outputs=None):
    if outputs is None:
        outputs = (Port(name="value", artifact_kind="counts"),)
    return seal(
        StepSpec,
        step_id=step_id,
        question_id="q-explore",
        plan_id="plan-explore",
        branch_id="explore",
        purpose=purpose,
        expected_information=purpose,
        dependencies=dependencies,
        input_ports=inputs,
        output_ports=outputs,
        operation=operation,
        evidence_use_scope="structural_only",
        source_environment="local-isolated",
        resources=GRANT,
        completion_obligations=tuple(port.name for port in outputs),
    )


def parse_counts(raw: bytes) -> dict:
    value = json.loads(raw)
    counts = value["counts"]
    width = len(value["samples"])
    if len(value["donors"]) != width or len(value["labels"]) != width:
        raise ValueError("SAMPLE_METADATA_MISMATCH")
    for row in counts:
        if len(row) != width or any(type(item) is not int or item < 0 for item in row):
            raise ValueError("COUNTS_NOT_NONNEGATIVE_INTEGERS")
    return {
        "genes": list(value["genes"]),
        "samples": list(value["samples"]),
        "donors": list(value["donors"]),
        "labels": list(value["labels"]),
        "counts": counts,
    }


def _compute(node: StepSpec, parents: dict[str, DerivedArtifact], ledger) -> dict | None:
    loaded = {name: _read_payload(ledger, artifact) for name, artifact in parents.items()}
    algorithm = _algorithm(node)
    if algorithm == "log1p_cpm":
        return _matrix_payload(loaded["counts"], log1p_cpm(loaded["counts"]["counts"]))
    if algorithm == "nearest_centroid":
        return fit_centroid(loaded["matrix"])
    if algorithm == "apply_centroid":
        return apply_centroid(loaded["matrix"], loaded["model"])
    if algorithm == "knn_graph":
        return knn_graph(loaded["matrix"])
    if algorithm == "degree_score":
        return degree_score(loaded["graph"])
    if algorithm == "rank_membership":
        return rank_membership(loaded["scores"])
    if algorithm == "recompute_log1p":
        return recompute_check(loaded["counts"], loaded["matrix"])
    if algorithm == "source_bound_svg":
        return source_bound_svg(loaded["matrix"], parents["matrix"].content_digest)
    return None


def log1p_cpm(counts) -> list:
    array = np.asarray(counts, dtype=np.float64)
    library = array.sum(axis=0, keepdims=True)
    scaled = np.divide(array, library, out=np.zeros_like(array), where=library > 0) * 10_000.0
    return _plain(np.log1p(scaled))


def fit_centroid(matrix_payload: dict) -> dict:
    values = np.asarray(matrix_payload["values"], dtype=np.float64).T
    labels = matrix_payload["labels"]
    classes = sorted(set(labels))
    centroids = []
    for label in classes:
        rows = values[[index for index, item in enumerate(labels) if item == label]]
        centroids.append(rows.mean(axis=0))
    return {
        "classes": classes,
        "centroids": _plain(np.vstack(centroids)),
        "genes": list(matrix_payload["genes"]),
        "label_provenance": "supplied_annotation",
        "generalization": "in_sample_not_held_out",
    }


def apply_centroid(matrix_payload: dict, model_payload: dict) -> dict:
    values = np.asarray(matrix_payload["values"], dtype=np.float64).T
    centroids = np.asarray(model_payload["centroids"], dtype=np.float64)
    distances = np.linalg.norm(values[:, None, :] - centroids[None, :, :], axis=2)
    chosen = distances.argmin(axis=1)
    classes = model_payload["classes"]
    return {
        "samples": list(matrix_payload["samples"]),
        "predicted": [classes[int(index)] for index in chosen],
        "in_sample": True,
        "generalization": "in_sample_not_held_out",
    }


def knn_graph(matrix_payload: dict, k: int = 2) -> dict:
    values = np.asarray(matrix_payload["values"], dtype=np.float64).T
    samples = list(matrix_payload["samples"])
    difference = values[:, None, :] - values[None, :, :]
    distance = np.sqrt(np.sum(difference * difference, axis=2))
    edges = {}
    for index, sample in enumerate(samples):
        others = [other for other in range(len(samples)) if other != index]
        others.sort(key=lambda other: (float(distance[index, other]), samples[other]))
        for other in others[:k]:
            key = tuple(sorted((sample, samples[other])))
            weight = 1.0 / (1.0 + float(distance[index, other]))
            edges[key] = max(edges.get(key, 0.0), weight)
    return {
        "samples": samples,
        "directed": False,
        "self_loops": False,
        "edges": [
            {"source": left, "target": right, "weight": weight} for (left, right), weight in sorted(edges.items())
        ],
    }


def degree_score(graph_payload: dict) -> dict:
    scores = {sample: 0.0 for sample in graph_payload["samples"]}
    for edge in graph_payload["edges"]:
        scores[edge["source"]] += edge["weight"]
        scores[edge["target"]] += edge["weight"]
    return {
        "samples": list(graph_payload["samples"]),
        "scores": [scores[sample] for sample in graph_payload["samples"]],
    }


def rank_membership(score_payload: dict) -> dict:
    order = sorted(
        range(len(score_payload["samples"])),
        key=lambda index: (
            -score_payload["scores"][index],
            score_payload["samples"][index],
        ),
    )
    keep = order[: max(1, len(order) // 2)]
    return {
        "samples": [score_payload["samples"][index] for index in keep],
        "primary_cohort_unchanged": True,
        "rule": "highest_degree_half",
    }


def recompute_check(counts_payload: dict, matrix_payload: dict) -> dict:
    expected = np.asarray(log1p_cpm(counts_payload["counts"]), dtype=np.float64)
    actual = np.asarray(matrix_payload["values"], dtype=np.float64)
    difference = float(np.max(np.abs(expected - actual)))
    return {"max_abs": difference, "tolerance": TOLERANCE, "within_tolerance": difference <= TOLERANCE}


def source_bound_svg(matrix_payload: dict, source_digest: str) -> dict:
    samples = matrix_payload["samples"]
    values = matrix_payload["values"]
    rows = [f'<svg xmlns="http://www.w3.org/2000/svg" data-source="{source_digest}">']
    for index, sample in enumerate(samples):
        rows.append(f'<circle data-sample="{sample}" cx="{values[0][index]:.6f}" cy="{values[1][index]:.6f}" r="2"/>')
    rows.append("</svg>")
    return {"svg": "\n".join(rows), "samples": samples, "source_digest": source_digest}


def _matrix_payload(counts_payload: dict, values) -> dict:
    return {
        "genes": list(counts_payload["genes"]),
        "samples": list(counts_payload["samples"]),
        "donors": list(counts_payload["donors"]),
        "labels": list(counts_payload["labels"]),
        "values": values,
    }


def _artifact(node, attempt_id, parents, payload, body: bytes) -> DerivedArtifact:
    kind = node.output_ports[0].artifact_kind
    parent_ids = tuple(artifact.content_digest for artifact in parents.values())
    graph = None
    membership = None
    selection = "none"
    history = ()
    learned = ()
    if kind == "graph":
        graph = GraphSemantics(
            node_identity="sample",
            node_order_digest=hashlib.sha256(canonical_json(payload["samples"])).hexdigest(),
            directed=False,
            edge_weight_semantics="inverse euclidean distance",
            symmetry_policy="undirected maximum",
            self_loop_policy="forbidden",
            construction_lineage=("log1p_cpm", "knn_k2"),
        )
    if kind == "membership_view":
        membership = parents["scores"].content_digest
        selection = "selected"
        history = ("highest_degree_half",)
    if kind == "predictions":
        learned = (parents["model"].content_digest,)
    identifier = payload.get("samples") or payload.get("genes") or [node.step_id]
    return seal(
        DerivedArtifact,
        kind=kind,
        producer_attempt_id=attempt_id,
        producer_spec_digest=node.content_digest,
        parent_digests=parent_ids,
        axes=_axes(kind),
        stable_id_digest=hashlib.sha256(canonical_json(list(identifier))).hexdigest(),
        dimensions=_dimensions(kind, payload),
        storage_layout="canonical-json",
        units=_units(kind),
        measurement_meaning=_meaning(kind),
        original_population="study samples",
        derived_population="exploratory view" if kind == "membership_view" else "study samples",
        algorithm=_algorithm(node) or node.step_id,
        code_digest=CODE_DIGEST if _algorithm(node) == "log1p_cpm" else None,
        environment="local-isolated",
        parameters_digest=hashlib.sha256(body).hexdigest(),
        randomness="none",
        learned_fit_dependencies=learned,
        selection_history=history,
        permitted_uses=EXPERIMENTAL_USES,
        byte_integrity="verified",
        schema_compatible=True,
        computational_checks=("finite",) if kind == "continuous_matrix" else (),
        scientific_applicability="experimental",
        selection_status=selection,
        qualified_regime=None,
        graph_semantics=graph,
        parent_membership=membership,
    )


def _compute_document(document: dict) -> dict | None:
    algorithm = document["algorithm"]
    if algorithm == "read_counts":
        raw = bytes.fromhex(document["raw_hex"])
        payload = parse_counts(raw)
        payload["file_sha256"] = hashlib.sha256(raw).hexdigest()
        return payload
    parents = document["parents"]
    if algorithm == "log1p_cpm":
        return _matrix_payload(parents["counts"], log1p_cpm(parents["counts"]["counts"]))
    if algorithm == "nearest_centroid":
        return fit_centroid(parents["matrix"])
    if algorithm == "apply_centroid":
        return apply_centroid(parents["matrix"], parents["model"])
    if algorithm == "knn_graph":
        return knn_graph(parents["matrix"])
    if algorithm == "degree_score":
        return degree_score(parents["graph"])
    if algorithm == "rank_membership":
        return rank_membership(parents["scores"])
    if algorithm == "recompute_log1p":
        return recompute_check(parents["counts"], parents["matrix"])
    if algorithm == "source_bound_svg":
        return source_bound_svg(parents["matrix"], document["matrix_digest"])
    return None


def _parent_artifacts(view, node: StepSpec) -> dict[str, DerivedArtifact]:
    by_step = {}
    artifacts = {artifact.content_digest: artifact for artifact in view.artifacts}
    for outcome in view.outcomes:
        for port in outcome.ports:
            if port.status == "fulfilled":
                by_step[outcome.step_id] = artifacts[port.artifact_id]
    found = {}
    for edge in view.active_plan().edges:
        if edge.kind == "compute" and edge.target_id == node.step_id and edge.target_port:
            found[edge.target_port] = by_step[edge.source_id]
    return found


def _port_problem(node: StepSpec, parents: dict[str, DerivedArtifact]) -> str | None:
    for port in node.input_ports:
        parent = parents.get(port.name)
        if parent is None or parent.kind != port.artifact_kind:
            return "TYPE_INCOMPATIBLE"
    expected = _output_kind(node)
    if expected is None or node.output_ports[0].artifact_kind != expected:
        return "TYPE_INCOMPATIBLE"
    return None


def _algorithm(node: StepSpec) -> str | None:
    operation = node.operation
    if operation.payload_type == "inspection":
        return "read_counts"
    if operation.payload_type == "transform":
        return operation.algorithm
    if operation.payload_type == "predictive_model":
        return operation.training_objective
    if operation.payload_type == "validation":
        return operation.checks[0]
    if operation.payload_type == "plot":
        return operation.verification_policy
    if operation.payload_type == "exploratory_code":
        return resolve_code(operation)
    return None


def _output_kind(node: StepSpec) -> str | None:
    return {
        "read_counts": "counts",
        "log1p_cpm": "continuous_matrix",
        "nearest_centroid": "model",
        "apply_centroid": "predictions",
        "knn_graph": "graph",
        "degree_score": "scores",
        "rank_membership": "membership_view",
        "recompute_log1p": "table",
        "source_bound_svg": "figure",
    }.get(_algorithm(node) or "")


def _read_payload(ledger, artifact: DerivedArtifact) -> dict:
    path = _payload_path(ledger, artifact.content_digest)
    return json.loads(path.read_text(encoding="utf-8"))


def _payload_path(ledger, digest: str) -> Path:
    root = ledger_workspace_root(ledger)
    return root / "payloads" / f"{digest}.json"


def ledger_workspace_root(ledger) -> Path:
    bridge_root = getattr(ledger, "workspace_root", None)
    if bridge_root is None:
        raise RuntimeError("WORKSPACE_ROOT")
    return Path(bridge_root)


def remember_workspace(ledger, root: Path) -> None:
    ledger.workspace_root = Path(root).resolve()


def _load_payloads(root: Path) -> dict[str, dict]:
    directory = Path(root).resolve() / "payloads"
    return {path.name: json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))}


def _within_tolerance(left, right, tolerance: float) -> bool:
    if type(left) is not type(right):
        if isinstance(left, list) and isinstance(right, list):
            return _within_tolerance(left, right, tolerance)
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(_within_tolerance(left[key], right[key], tolerance) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_within_tolerance(a, b, tolerance) for a, b in zip(left, right))
    if isinstance(left, float):
        return abs(left - right) <= tolerance
    return left == right


def _plain(value):
    if isinstance(value, np.ndarray):
        return _plain(value.tolist())
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _axes(kind: str) -> tuple[str, ...]:
    if kind in {"counts", "continuous_matrix"}:
        return ("gene", "sample")
    if kind == "graph":
        return ("sample", "sample")
    return ("sample",)


def _dimensions(kind: str, payload: dict) -> tuple[int, ...]:
    if kind in {"counts", "continuous_matrix"}:
        values = payload.get("counts", payload.get("values"))
        return (len(values), len(values[0]))
    if kind == "graph":
        return (len(payload["samples"]), len(payload["edges"]))
    if kind == "model":
        return (len(payload["classes"]), len(payload["genes"]))
    if kind == "figure":
        return (len(payload["samples"]),)
    if kind == "table":
        return (1,)
    return (len(payload["samples"]),)


def _units(kind: str) -> str:
    return {
        "counts": "count",
        "continuous_matrix": "log1p_cpm",
        "model": "centroid",
        "predictions": "class label",
        "graph": "inverse distance",
        "scores": "weighted degree",
        "membership_view": "sample membership",
        "table": "max absolute difference",
        "figure": "svg coordinate",
    }[kind]


def _meaning(kind: str) -> str:
    if kind == "membership_view":
        return "exploratory subset; the primary sample cohort is unchanged"
    if kind == "predictions":
        return "in-sample centroid label; not a held-out or biological claim"
    if kind == "figure":
        return "sample coordinates bound to the transformed matrix"
    return "experimental exploratory product"
