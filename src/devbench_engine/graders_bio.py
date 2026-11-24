"""Set and ordered-cascade agreement without a model judge."""
import math
from numbers import Real
from typing import Dict, List, Optional, Tuple

Grade = Tuple[bool, float, str]


def grade_label_set_jaccard(actual_list: List, expected_list: List, threshold: float = .6,
                            expected_count: Optional[int] = None) -> Tuple[bool, float, str]:
    if not 0 <= threshold <= 1:
        raise ValueError("threshold must be in [0, 1]")
    if expected_count is not None and (not isinstance(expected_count, int) or expected_count < 0):
        raise ValueError("expected_count must be a nonnegative integer")
    if not isinstance(actual_list, list) or not isinstance(expected_list, list):
        return False, 0.0, "Expected lists of labels"
    try:
        actual, expected = set(actual_list), set(expected_list)
    except TypeError:
        return False, 0.0, "Labels must be hashable"
    if expected_count is not None and (len(actual_list) != expected_count or len(actual) != expected_count):
        return False, 0.0, f"Expected exactly {expected_count} distinct labels"
    score = len(actual & expected) / len(actual | expected) if actual | expected else 1.0
    return score >= threshold, score, f"Jaccard={score:.6g}; threshold={threshold:.6g}"


def grade_longest_subsequence(actual_sequence: List, expected_sequence: List,
                              pass_threshold: float = .75) -> Tuple[bool, float, str]:
    if not 0 <= pass_threshold <= 1:
        raise ValueError("pass_threshold must be in [0, 1]")
    if not isinstance(actual_sequence, list) or not isinstance(expected_sequence, list):
        return False, 0.0, "Expected ordered lists"
    # O(min(n,m)) memory, supporting tuple/list cascade elements as well as labels.
    long, short = sorted((actual_sequence, expected_sequence), key=len, reverse=True)
    previous = [0] * (len(short) + 1)
    for value in long:
        row = [0]
        for j, other in enumerate(short, 1):
            row.append(previous[j - 1] + 1 if value == other else max(previous[j], row[-1]))
        previous = row
    score = previous[-1] / max(len(actual_sequence), len(expected_sequence), 1)
    return score >= pass_threshold, score, f"LCS={previous[-1]}; normalized agreement={score:.6g}"


def _threshold(value: float, name: str = "threshold") -> None:
    if not _finite_number(value) or not 0 <= value <= 1:
        raise ValueError(name + " must be finite and in [0, 1]")


def _finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, Real):
        return False
    try:
        return math.isfinite(value)
    except (ValueError, OverflowError):
        return False


def _finite_map(values: dict) -> bool:
    return (isinstance(values, dict) and bool(values) and
            all(isinstance(key, str) and key for key in values) and
            all(_finite_number(value) for value in values.values()))


def grade_pseudotime(actual: Dict[str, float], expected: Dict[str, float], threshold: float = .8,
                     actual_branches: Optional[dict] = None, expected_branches: Optional[dict] = None,
                     min_coverage: float = 1.0) -> Grade:
    """ID-aligned Spearman agreement; optional label-invariant adjusted Rand index.

    Tied times receive average ranks. Constant/singleton trajectories carry no
    ordering evidence and fail. A supplied branch partition must cover all
    matched IDs; the score is the smaller of nonnegative Spearman and ARI.
    """
    from scipy.stats import spearmanr
    from sklearn.metrics import adjusted_rand_score
    _threshold(threshold)
    _threshold(min_coverage, "min_coverage")
    if not _finite_map(actual) or not _finite_map(expected):
        return False, 0.0, "Pseudotime requires nonempty ID maps of finite numbers"
    matched = sorted(actual.keys() & expected.keys())
    coverage = len(matched) / len(expected)
    if len(matched) < 2 or coverage < min_coverage:
        return False, 0.0, f"Insufficient matched IDs; coverage={coverage:.6g}"
    a, b = [actual[key] for key in matched], [expected[key] for key in matched]
    if len(set(a)) < 2 or len(set(b)) < 2:
        return False, 0.0, "Constant pseudotime has undefined Spearman ordering"
    rho = float(spearmanr(a, b).statistic)
    if not math.isfinite(rho):
        return False, 0.0, "Spearman correlation is undefined"
    score = max(0.0, min(1.0, rho))
    detail = f"Spearman={rho:.6g}; matched={len(matched)}; coverage={coverage:.6g}"
    if actual_branches is not None or expected_branches is not None:
        if not isinstance(actual_branches, dict) or not isinstance(expected_branches, dict):
            return False, 0.0, "Both branch partitions are required"
        if any(key not in actual_branches or key not in expected_branches for key in matched):
            return False, 0.0, "Branch assignments must cover every matched ID"
        # Encode independently: arbitrary labels are categories, never numbers
        # on which an RMSE or a distance is meaningful.
        try:
            partitions = []
            for labels in (actual_branches, expected_branches):
                code, encoded = {}, []
                for key in matched:
                    label = labels[key]
                    if not isinstance(label, (str, int)) or isinstance(label, bool) or label == "":
                        raise ValueError("Branch labels must be nonempty strings or integers")
                    encoded.append(code.setdefault(label, len(code)))
                partitions.append(encoded)
            ari = float(adjusted_rand_score(*partitions))
        except (TypeError, ValueError) as exc:
            return False, 0.0, str(exc)
        score = min(score, max(0.0, ari))
        detail += f"; ARI={ari:.6g} (label-invariant)"
    return score >= threshold, score, detail


def _lineage_graph(value: dict, require_tree: bool = False):
    import networkx as nx
    if not isinstance(value, dict) or not isinstance(value.get("nodes"), list) or not isinstance(value.get("edges"), list):
        raise ValueError("Graph requires nodes and directed edges lists")
    if not value["nodes"] or len(value["nodes"]) > 10000 or len(value["edges"]) > 50000:
        raise ValueError("Graph requires between 1 and 10000 nodes and at most 50000 edges")
    graph = nx.DiGraph()
    for node in value["nodes"]:
        identifier = node.get("id") if isinstance(node, dict) else node
        label = node.get("label", identifier) if isinstance(node, dict) else identifier
        if any(not isinstance(item, str) or not item for item in (identifier, label)):
            raise ValueError("Node IDs and labels must be nonempty strings")
        if identifier in graph:
            raise ValueError("Duplicate node ID")
        graph.add_node(identifier, label=label)
    for edge in value["edges"]:
        if isinstance(edge, dict):
            source, target = edge.get("source"), edge.get("target")
        elif isinstance(edge, (list, tuple)) and len(edge) == 2:
            source, target = edge
        else:
            raise ValueError("Edges require source and target")
        if not isinstance(source, str) or not isinstance(target, str) or source not in graph or target not in graph:
            raise ValueError("Every edge endpoint must be a declared node")
        if graph.has_edge(source, target):
            raise ValueError("Duplicate directed edge")
        graph.add_edge(source, target)
    if not nx.is_directed_acyclic_graph(graph):
        raise ValueError("Lineage graph must be a directed acyclic graph; cycles are invalid")
    if require_tree and not nx.is_arborescence(graph):
        raise ValueError("Lineage tree must be connected with exactly one parent per non-root node")
    return graph


def _exact_ged_worker(actual: dict, expected: dict) -> float:
    import networkx as nx
    # NetworkX's internal timeout returns a best-so-far distance with no
    # optimality flag. Deliberately exhaust its exact search under the *outer*
    # supervised hard deadline; only normal completion certifies optimality.
    distance = nx.graph_edit_distance(_lineage_graph(actual), _lineage_graph(expected),
                                      node_match=lambda a, b: a["label"] == b["label"])
    if distance is None or not math.isfinite(distance):
        raise ValueError("Exact edit distance could not be established")
    return float(distance)


def grade_lineage_graph(actual: dict, expected: dict, threshold: float = .8,
                        method: str = "label_edit", timeout_sec: float = 5.0,
                        max_nodes: int = 8, require_tree: bool = False) -> Grade:
    """Compare directed labeled DAGs, optionally requiring rooted trees.

    ``label_edit`` fixes node identity: unit insert/delete for node IDs and
    directed edges, and unit label replacement on shared IDs. It is explicitly
    NOT unrestricted graph edit distance. ``exact`` permits node correspondence
    and unit label substitution using exhaustive NetworkX GED on small graphs.
    A hard worker deadline yields inability, never an approximate exact score.
    Both normalize by the combined node+edge counts (the delete-all/add-all cost).
    """
    from devharness_core.watchdog import HarnessResourceInterrupt, RemoteWorkerError, run_sandboxed
    _threshold(threshold)
    if method not in ("label_edit", "exact"):
        raise ValueError("method must be label_edit or exact")
    if not _finite_number(timeout_sec) or timeout_sec <= 0:
        raise ValueError("timeout_sec must be finite and positive")
    if isinstance(max_nodes, bool) or not isinstance(max_nodes, int) or not 1 <= max_nodes <= 10:
        raise ValueError("max_nodes must be between 1 and 10 for exact search")
    try:
        a, b = _lineage_graph(actual, require_tree), _lineage_graph(expected, require_tree)
    except (ValueError, TypeError) as exc:
        return False, 0.0, "Invalid lineage graph: " + str(exc)
    if method == "label_edit":
        distance = (len(set(a) ^ set(b)) + len(set(a.edges) ^ set(b.edges)) +
                    sum(a.nodes[node]["label"] != b.nodes[node]["label"] for node in set(a) & set(b)))
        metric = "label-preserving node/edge edit cost (not unrestricted GED)"
    else:
        if max(len(a), len(b)) > max_nodes:
            return False, 0.0, "Unable to certify exact GED: graph exceeds small-graph node budget"
        try:
            distance = run_sandboxed(_exact_ged_worker, (actual, expected), timeout_sec=timeout_sec)
        except (HarnessResourceInterrupt, RemoteWorkerError) as exc:
            return False, 0.0, "Unable to certify exact GED within worker budget: " + str(exc)[:160]
        metric = "exact directed labeled GED (unit edits; exhaustive search completed)"
    denominator = len(a) + len(b) + a.number_of_edges() + b.number_of_edges()
    score = max(0.0, 1.0 - distance / max(denominator, 1))
    return score >= threshold, score, f"{metric}; distance={distance:.6g}; similarity={score:.6g}"


def _edge_key(edge: dict, signed: bool) -> tuple:
    if isinstance(edge, dict):
        tf, target, sign = edge.get("tf"), edge.get("target"), edge.get("sign")
    elif isinstance(edge, (list, tuple)) and len(edge) == (3 if signed else 2):
        tf, target = edge[:2]
        sign = edge[2] if signed else None
    else:
        raise ValueError("Network edges require tf and target fields")
    if not isinstance(tf, str) or not tf or not isinstance(target, str) or not target:
        raise ValueError("TF and target must be nonempty gene names")
    if signed:
        sign = {"activation": 1, "repression": -1, "+": 1, "-": -1}.get(sign, sign)
        if isinstance(sign, bool) or sign not in (-1, 1):
            raise ValueError("Signed evaluation requires explicit activation (+1) or repression (-1)")
        return tf, target, sign
    return tf, target


def grade_network_precision_recall(actual: List[dict], expected: List[dict],
                                    candidate_universe: List[dict], threshold: float = .5,
                                    signed: bool = False) -> Grade:
    """Non-interpolated PR-AUC (average precision) over a fixed measured universe.

    The caller must supply assessed edges, including negatives. Edges absent
    from a curated positive-only database are not automatically valid negatives.
    Omitted predictions tie below every submitted score; no measured candidate
    is removed from evaluation. Direction and optional sign are part of identity.
    """
    from sklearn.metrics import average_precision_score
    _threshold(threshold)
    try:
        if any(not isinstance(rows, list) or len(rows) > 100000 for rows in (actual, expected, candidate_universe)):
            raise ValueError("Network inputs must be lists of at most 100000 edges")
        universe = [_edge_key(edge, signed) for edge in candidate_universe]
        universe_set = set(universe)
        positives = [_edge_key(edge, signed) for edge in expected]
        if len(universe_set) != len(universe) or len(set(positives)) != len(positives):
            raise ValueError("Universe and reference must not contain duplicate edges")
        if not set(positives).issubset(universe_set):
            raise ValueError("Every expected edge must be in the assessed candidate universe")
        if not positives or len(positives) == len(universe):
            raise ValueError("PR-AUC requires assessed positives and negatives in the candidate universe")
        scores = {}
        for edge in actual:
            key = _edge_key(edge, signed)
            value = edge.get("score") if isinstance(edge, dict) else None
            if key in scores or key not in universe_set:
                raise ValueError("Predictions must be distinct assessed candidate edges")
            if not _finite_number(value):
                raise ValueError("Edge scores must be finite numbers")
            scores[key] = value
        if not scores:
            raise ValueError("No scored predictions were supplied")
    except (TypeError, ValueError) as exc:
        return False, 0.0, str(exc)
    # Rank-compress first so even the minimum finite float has a safe lower
    # sentinel. This preserves order and ties without overflow.
    ranks = {score: rank + 1 for rank, score in enumerate(sorted(set(scores.values())))}
    positive_set = set(positives)
    labels = [int(edge in positive_set) for edge in universe]
    predictions = [ranks[scores[edge]] if edge in scores else 0 for edge in universe]
    score = float(average_precision_score(labels, predictions))
    return score >= threshold, score, (f"Average precision (non-interpolated PR-AUC)={score:.6g}; "
        f"assessed edges={len(universe)}; positives={len(positives)}; signed={signed}")


def grade_effect_calibration(actual: Dict[str, float], expected: Dict[str, float],
                             absolute_tolerance: float = .25, relative_tolerance: float = .1,
                             threshold: float = .8) -> Grade:
    """Fraction of matched fold-change effects within abs_tol + rel_tol*|truth|.

    Actual and expected must use the same declared scale (for example log2FC)
    and gene IDs. Tolerances are supplied by the task, never a universal gene-
    specific effect threshold. This scores numerical calibration, not causality.
    """
    _threshold(threshold)
    for name, value in (("absolute_tolerance", absolute_tolerance), ("relative_tolerance", relative_tolerance)):
        if not _finite_number(value) or value < 0:
            raise ValueError(name + " must be finite and nonnegative")
    if not _finite_map(actual) or not _finite_map(expected) or set(actual) != set(expected):
        return False, 0.0, "Effect maps require identical nonempty gene IDs and finite numbers on the same scale"
    errors = [abs(actual[gene] - truth) for gene, truth in expected.items()]
    limits = [absolute_tolerance + relative_tolerance * abs(truth) for truth in expected.values()]
    # Inputs are finite but subtraction can overflow at opposite float extrema.
    if not all(math.isfinite(value) for value in errors + limits):
        return False, 0.0, "Effect errors or tolerance limits overflowed finite arithmetic"
    calibrated = sum(error <= limit or math.isclose(error, limit, rel_tol=1e-12, abs_tol=0)
                     for error, limit in zip(errors, limits))
    score = calibrated / len(expected)
    return score >= threshold, score, (f"Calibrated effects={calibrated}/{len(expected)}; "
        f"maximum absolute error={max(errors):.6g}; tolerance=absolute+relative*abs(expected)")
