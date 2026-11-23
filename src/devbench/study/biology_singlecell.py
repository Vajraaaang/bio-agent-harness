"""Gated, in-memory single-cell simulation mechanisms for biology T14--T20.

Import and admission checks use only configuration and the standard library.
These implementations are unqualified simulation code: registration does not
certify a tool, a scorer, empirical biology, or a completed numerical experiment.
There is one expression layer; in particular no spliced/unspliced measurements
or velocity are fabricated. Private truth must never be staged for an agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .biology import BiologyRecipe


_VARIANTS = MappingProxyType({
    "T14": ("state-only", "abundance-only", "rare-responder", "cancellation", "one-donor"),
    "T15": ("linear", "bifurcation", "convergence-history", "convergence-unmarked", "direction-reversed"),
    "T16": ("balanced-capture", "rare-branch", "missing-intermediate", "capture-biased", "proliferation", "age-mismatch"),
    "T17": ("independent-axes", "correlated-axes", "synchronized", "cycle-target"),
    "T18": ("umi-sampling", "non-umi-censoring", "ambient-reference", "ambient-unresolved", "heterotypic-doublet", "homotypic-doublet", "viable-low-rna"),
    "T19": ("expression-only", "annotated", "motif-supported", "decoy-priors"),
    "T20": ("control", "randomized", "variable-efficacy", "indirect-cascade", "double-perturbation", "off-target"),
})
_TARGETS = MappingProxyType({
    "T14": ("cell_state_effect", "captured_abundance"),
    "T15": ("state_topology", "ancestry"),
    "T16": ("sampled_state_geometry", "population_abundance", "transition_dynamics"),
    "T17": ("maturation_order", "cycle_state"),
    "T18": ("measurement_diagnostics", "clean_cell_state"),
    "T19": ("association_activity", "regulon_membership", "motif_occurrence", "binding"),
    "T20": ("assignment_effect", "successful_intervention", "epistasis", "direct_edge"),
})
_STREAMS = ("design", "identifiers", "donor", "cell", "program", "assay", "reference", "artifact", "intervention")


@dataclass(frozen=True)
class SingleCellGenerationPlan:
    case_id: str
    measurement: str
    observation_upper_bound: int
    expression_bytes: int
    stored_numeric_bytes_upper_bound: int
    dense_working_bytes_upper_bound: int
    target_scopes: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class SingleCellBiologyBundle:
    expression: Any  # genes x captured cells; ndarray only after authorization
    gene_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    metadata: tuple[dict[str, Any], ...]
    measurement: str
    public_context: dict[str, Any]
    public_annotations: dict[str, Any]
    private_truth: dict[str, Any]

    @property
    def sample_ids(self) -> tuple[str, ...]:
        """Common transport name; a captured cell is not an independent donor."""
        return self.observation_ids


def supported_biology_singlecell_cases() -> tuple[str, ...]:
    """Exact code-backed cases, without reading a catalog or running a model."""
    return tuple(f"{template}-{variant}" for template, variants in _VARIANTS.items() for variant in variants)


def _target_scopes(case_id: str) -> tuple[tuple[str, str], ...]:
    template, variant = case_id.split("-", 1)
    scopes = dict.fromkeys(_TARGETS[template], "supported")
    if case_id == "T14-one-donor":
        scopes.update(cell_state_effect="refusal", captured_abundance="limited_reference")
    if template == "T15":
        scopes["ancestry"] = "refusal" if variant in {"convergence-unmarked", "direction-reversed"} else "limited_reference"
    if template == "T16":
        scopes["transition_dynamics"] = "refusal"
        scopes["population_abundance"] = "refusal"
        if variant in {"rare-branch", "missing-intermediate"}:
            scopes["sampled_state_geometry"] = "limited_reference"
    if case_id == "T17-synchronized":
        scopes["cycle_state"] = "limited_reference"
    if template == "T18" and variant in {"ambient-unresolved", "homotypic-doublet"}:
        scopes["clean_cell_state"] = "limited_reference"
    if template == "T19":
        scopes.update(regulon_membership="refusal", motif_occurrence="refusal", binding="refusal")
        if variant != "expression-only":
            scopes["regulon_membership"] = "limited_reference"
        if variant in {"motif-supported", "decoy-priors"}:
            scopes["motif_occurrence"] = "limited_reference"
    if template == "T20":
        scopes.update(epistasis="refusal", direct_edge="refusal")
        if variant in {"control", "variable-efficacy", "off-target"}:
            scopes["successful_intervention"] = "refusal"
        if variant == "double-perturbation":
            scopes["epistasis"] = "supported"
    return tuple(scopes.items())


def validate_biology_singlecell_recipe(recipe: BiologyRecipe) -> SingleCellGenerationPlan:
    """Pure admission: no ndarray, seed stream, metadata, files or workers."""
    if not isinstance(recipe, BiologyRecipe):
        raise TypeError("single-cell generation requires a BiologyRecipe")
    if recipe.case_id not in supported_biology_singlecell_cases():
        raise ValueError(f"single-cell case is not implemented: {recipe.case_id}")
    if recipe.template_id != recipe.case_id.split("-", 1)[0]:
        raise ValueError("recipe template and case disagree")
    p = recipe.parameters
    if recipe.generation_seed is None:
        raise ValueError("a coordinator-assigned generation_seed is required")
    if not 64 <= p.num_genes <= 5000:
        raise ValueError("single-cell mechanisms require a complete universe of 64 to 5000 genes")
    if p.cells_per_specimen < 20 or not 3 <= p.cell_type_count <= 5:
        raise ValueError("single-cell mechanisms require at least 20 cells per specimen and 3 to 5 cell types")
    if p.technical_libraries_per_specimen != 1:
        raise ValueError("technical resequencing of the same cell is not implemented")
    if p.stored_expression_layers != 1:
        raise ValueError("exactly one expression layer is implemented; no velocity layers")
    if p.dose_grid:
        raise ValueError("dose-dependent single-cell mechanisms are not implemented")
    if p.effect_programs:
        raise ValueError("custom effect programs are not implemented for these fixed mechanistic single-cell cases")
    if p.time_grid and p.repeated_specimens_per_unit not in {1, len(p.time_grid)}:
        raise ValueError("repeated specimens must match the declared time grid")
    groups = len(p.condition_labels)
    required_groups = 4 if recipe.case_id == "T20-double-perturbation" else (2 if recipe.template_id in {"T14", "T20"} else 1)
    if groups != required_groups:
        raise ValueError(f"this case requires exactly {required_groups} condition groups")
    if recipe.case_id == "T14-one-donor":
        if p.independent_units_per_group != 1:
            raise ValueError("one-donor case requires exactly one independent donor per condition")
    elif p.independent_units_per_group < 3:
        raise ValueError("this case requires at least three independent donors per condition")
    if recipe.case_id == "T16-age-mismatch" and len(p.time_grid) < 3:
        raise ValueError("age mismatch requires at least three declared collection ages")
    positions = max(1, len(p.time_grid), p.repeated_specimens_per_unit)
    donors = groups * p.independent_units_per_group
    observations = donors * positions * p.cells_per_specimen
    if p.num_batches > donors * positions:
        raise ValueError("batch count cannot exceed specimen count")
    # Includes the one count layer and a conservative bound for ALL retained
    # numeric truth/reference objects (including donor effects and memberships).
    expression_bytes = 8 * observations * p.num_genes
    retained = expression_bytes + 8 * (p.num_genes * (donors + 80) + observations * 80)
    working = retained + 8 * p.num_genes * observations * 12
    if retained > p.max_matrix_bytes:
        raise ValueError("all stored numeric layers and truth exceed the bounded matrix budget")
    if working > 512_000_000:
        raise ValueError("dense working set exceeds the 512 MB single-cell generation bound")
    return SingleCellGenerationPlan(
        case_id=recipe.case_id,
        measurement="non_umi_counts" if recipe.case_id == "T18-non-umi-censoring" else "umi_counts",
        observation_upper_bound=observations,
        expression_bytes=expression_bytes,
        stored_numeric_bytes_upper_bound=retained,
        dense_working_bytes_upper_bound=working,
        target_scopes=_target_scopes(recipe.case_id),
    )


def _opaque_ids(rng: Any, count: int, prefix: str) -> tuple[str, ...]:
    # Assigned after permission, without embedding case, gene role or seed.
    result: set[str] = set()
    ordered = []
    while len(ordered) < count:
        value = prefix + "_" + rng.bytes(12).hex()
        if value not in result:
            ordered.append(value)
            result.add(value)
    return tuple(ordered)


def _sample_counts(np: Any, rng: Any, expected: Any, dispersion: float) -> Any:
    if not np.isfinite(expected).all() or np.any(expected < 0) or np.max(expected) > 1e12:
        raise ValueError("count expectation is outside the finite sampling range")
    # Gamma biological/capture heterogeneity followed by Poisson molecule
    # sampling. Zeros arise naturally; there is no universal extra dropout.
    intensity = rng.gamma(1.0 / dispersion, expected * dispersion)
    return rng.poisson(intensity).astype(np.int64, copy=False)


def _enrichment_query_size(num_genes: int) -> int:
    """Fixed configuration rule; no observed values or private programs here."""
    if type(num_genes) is not int or not 64 <= num_genes <= 5000:
        raise ValueError("enrichment selection requires a complete 64 to 5000 gene universe")
    return min(200, max(16, (num_genes + 9) // 10))


def _observed_enrichment_query(np: Any, expression: Any, gene_ids: tuple[str, ...], *, max_working_bytes: int) -> dict[str, Any]:
    """Select from observed counts only, using one bounded float working layer.

    Called only inside the authorized generator. No latent state, loadings,
    activity or annotation table is an input; raw counts are never modified.
    This same-expression selection supports descriptive overlap arithmetic,
    not calibrated enrichment p-values, FDR or biological/causal validation.
    """
    genes, observations = expression.shape
    query_size = _enrichment_query_size(genes)
    if len(gene_ids) != genes or len(set(gene_ids)) != genes:
        raise ValueError("enrichment query requires the complete unique expression gene axis")
    # One genes-by-observations float buffer, vector scratch, and a conservative
    # per-gene allowance for Python ranking keys/indices. Generator admission's
    # retained-layer bound is larger; enforce the same configured cap here too.
    working_bytes = 8 * (genes * observations + 8 * genes + 4 * observations) + observations + 512 * genes
    if working_bytes > max_working_bytes:
        raise ValueError("observed enrichment selection exceeds its working-buffer bound")
    totals = expression.sum(axis=0, dtype=np.float64)
    nonempty = totals > 0
    usable_count = int(np.count_nonzero(nonempty))
    if usable_count < 2:
        raise ValueError("observed enrichment selection requires at least two nonempty libraries")
    normalized = np.array(expression, dtype=np.float64, copy=True)
    np.divide(normalized, totals[None, :], out=normalized, where=nonempty[None, :])
    normalized[:, ~nonempty] = 0.0
    normalized *= 10000.0
    np.log1p(normalized, out=normalized)
    # Translation leaves variance unchanged and makes exactly constant rows
    # exactly zero before averaging, avoiding a roundoff-only variance rank.
    origin = normalized[:, int(np.argmax(nonempty))].copy()
    normalized -= origin[:, None]
    normalized[:, ~nonempty] = 0.0
    means = normalized.sum(axis=1) / usable_count
    # Reuse the same layer for centered sums of squares; excluded empty
    # libraries contribute zero, rather than artificially inflating variance.
    normalized -= means[:, None]
    normalized[:, ~nonempty] = 0.0
    np.square(normalized, out=normalized)
    variances = normalized.sum(axis=1) / usable_count
    if not np.isfinite(variances).all():
        raise ValueError("observed enrichment selection produced nonfinite variances")
    order = sorted(range(genes), key=lambda index: (-float(variances[index]), gene_ids[index]))
    selected = tuple(gene_ids[index] for index in order[:query_size])
    return {
        "enrichment_query_gene_ids": selected,
        "enrichment_background_gene_ids": gene_ids,
        "enrichment_query_selection": {
            "input": "observed counts only",
            "transformation": "log1p(10000 * count / observed library total)",
            "ranking": "decreasing gene variance across nonempty observed libraries",
            "variance_denominator": "number of nonempty observed libraries (ddof=0)",
            "query_count_rule": "min(200, max(16, ceil(number_of_measured_genes / 10)))",
            "query_count": query_size,
            "tie_break": "gene identifier ascending",
            "empty_library_policy": "excluded from variance; require at least two nonempty libraries",
            "nonempty_library_count": usable_count,
            "positive_variance_gene_count": int(np.count_nonzero(variances > 0)),
            "background": "complete measured gene universe, including zero-expression genes",
            "selection_independent_of_tested_expression": False,
            "independent_assay": False,
            "inference_scope": "descriptive annotation overlap conditional on this observed-expression selection",
            "calibrated_p_values_or_fdr": False,
            "causal_evidence": False,
            "working_bytes_upper_bound": working_bytes,
        },
    }


def _stored_numeric_bytes(np: Any, value: Any, seen: set[int] | None = None) -> int:
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    if isinstance(value, np.ndarray):
        return value.nbytes
    if isinstance(value, dict):
        return sum(_stored_numeric_bytes(np, item, seen) for item in value.values())
    if isinstance(value, (tuple, list)):
        return sum(_stored_numeric_bytes(np, item, seen) for item in value)
    return 0


def _shared_process_projection(np: Any, state: dict[str, Any], gene_ids: tuple[str, ...], marker_reference: dict[str, Any]) -> dict[str, Any]:
    """Declare the public feature view and check its conditional mean scope.

    Feature selection uses the public marker dictionary, never query cell-type
    labels. The private profile comparison is a construction check only; no
    empirical integration or full-expression type independence is certified.
    """
    excluded = {gene for entry in marker_reference["markers"] for gene in entry["gene_ids"]}
    if not excluded or not excluded < set(gene_ids):
        raise ValueError("shared-process projection requires a proper marker subset of the complete gene universe")
    retained_indices = [index for index, gene in enumerate(gene_ids) if gene not in excluded]
    retained_profiles = state["profiles"][retained_indices, :]
    if not np.all(retained_profiles == retained_profiles[:, :1]):
        raise ValueError("shared-process projected features still contain type-specific baseline means")
    return {
        "scope": "shared_process_quotient",
        "rule": "exclude_union_of_declared_broad_type_identity_markers",
        "analysis_layer": "counts",
        "excluded_feature_ids": tuple(sorted(excluded)),
        "retained_feature_universe": tuple(gene_ids[index] for index in retained_indices),
        "full_geometry_scope": "not_claimed",
        "all_observations_retained": True,
        "full_counts_preserved": True,
        "marker_reference_id": "cell_annotation_reference",
        "marker_genes_are_nuisance_for_other_questions": False,
    }


def _observed_gap_geometry(np: Any, geometry: dict[str, Any]) -> dict[str, Any]:
    """A disconnected reference on sampled support, with no hidden bridge.

    Full latent topology/time remain in the separate coordinator-only object.
    Per-component rank coordinates have independent orientation; isolated
    observations have no within-component ordering estimate.
    """
    components = geometry["observable_state_index"]
    within_order: list[float | None] = [None] * len(components)
    edges = []
    nodes = []
    for component in np.unique(components):
        members = np.flatnonzero(components == component)
        prefix = f"observed_component_{int(component)}"
        if len(members) == 1:
            nodes.append(prefix + "_point")
            continue
        start, end = prefix + "_start", prefix + "_end"
        nodes.extend((start, end))
        edges.append((start, end))
        ordered = members[np.argsort(geometry["latent_time"][members], kind="stable")]
        for rank, member in enumerate(ordered):
            within_order[int(member)] = rank / (len(ordered) - 1)
    support_contract = {
        "scope": "observed_support_components_only",
        "sampling_declaration": "intermediate sampling coverage is unavailable; no observations establish connections across the omitted support",
        "connectivity_across_gap": "not_identified",
        "ordering_scope": "within_observed_components_only",
        "between_component_order_identifiable": False,
        "ancestry_across_gap_identifiable": False,
        "transition_dynamics_identifiable": False,
        "latent_gap_coordinates_disclosed": False,
        "all_observations_retained": True,
        "full_counts_preserved": True,
    }
    return {
        "graph": tuple(edges), "observed_support_graph": tuple(edges),
        "observed_support_nodes": tuple(nodes),
        "observed_component_ids": components, "branch": components,
        "branch_labels_are_observed_components": True,
        "branch_reference_mask": np.ones(len(components), dtype=bool),
        "within_component_order": tuple(within_order),
        "within_component_order_mask": np.asarray([value is not None for value in within_order], dtype=bool),
        "order_reference_scope": "within_component_rank_only",
        "order_invariances": ("component_label_permutation", "independent_component_reversal"),
        "between_component_order_identifiable": False,
        "unobserved_connectivity_identifiable": False,
        "support_gap_contract": support_contract,
        "observed_support_reference": "disconnected sampled-support components only; no bridge, hidden fork, cross-gap ancestry or between-component order is identified",
        "full_latent_graph_scored": False,
    }


def _layout(np: Any, recipe: BiologyRecipe, rng: dict[str, Any]) -> dict[str, Any]:
    p = recipe.parameters
    positions = max(1, len(p.time_grid), p.repeated_specimens_per_unit)
    donors = p.independent_units_per_group * len(p.condition_labels)
    # A balanced randomized donor allocation; cells never increase donor n.
    donor_condition = rng["design"].permutation(np.repeat(np.arange(len(p.condition_labels)), p.independent_units_per_group))
    specimen_donor = np.repeat(np.arange(donors), positions)
    specimen_position = np.tile(np.arange(positions), donors)
    specimen = np.repeat(np.arange(donors * positions), p.cells_per_specimen)
    donor = specimen_donor[specimen]
    position = specimen_position[specimen]
    batch_order = rng["design"].permutation(donors * positions)
    specimen_batch = np.empty(donors * positions, dtype=np.int64)
    specimen_batch[batch_order] = np.arange(donors * positions) % p.num_batches
    return {
        "donor": donor, "specimen": specimen, "position": position,
        "donor_condition": donor_condition,
        "condition": donor_condition[donor], "batch": specimen_batch[specimen],
        "donor_ids": _opaque_ids(rng["identifiers"], donors, "d"),
        "specimen_ids": _opaque_ids(rng["identifiers"], donors * positions, "s"),
        "library_ids": _opaque_ids(rng["identifiers"], donors * positions, "l"),
        "batch_ids": _opaque_ids(rng["identifiers"], p.num_batches, "b"),
        "condition_allocation_unit": "donor",
    }


def _cell_state(np: Any, recipe: BiologyRecipe, rng: dict[str, Any], layout: dict[str, Any], gene_ids: tuple[str, ...]) -> dict[str, Any]:
    p = recipe.parameters
    g, n, k = p.num_genes, len(layout["donor"]), p.cell_type_count
    # Disjoint marker and response panels prevent annotation from being an
    # answer key for condition effects. The complete feature universe remains.
    shuffled = rng["program"].permutation(g)
    panels = tuple(np.asarray(x, dtype=np.int64) for x in np.array_split(shuffled, 12))
    type_labels = _opaque_ids(rng["identifiers"], k, "annotation")
    profiles = rng["program"].lognormal(np.log(p.reference_mean) - 0.125, 0.5, (g, k))
    # A common basal gene distribution plus distinct cell-type production.
    profiles[:] = profiles.mean(axis=1, keepdims=True)
    marker_panels = tuple(panels[i] for i in range(k))
    for index, panel in enumerate(marker_panels):
        profiles[panel, index] *= 5.0
    fractions = np.full((len(p.condition_labels), k), 1.0 / k)
    if recipe.case_id == "T14-abundance-only":
        fractions[1] *= 0.65
        fractions[1, 0] += 0.35
    if recipe.case_id == "T14-rare-responder":
        fractions[:, -1] = 0.02
        fractions[:, :-1] = 0.98 / (k - 1)
    # Each donor has its own composition, shared by its specimens; random
    # cells are samples, not extra biological replicates.
    donor_fractions = np.empty((len(layout["donor_ids"]), k))
    for donor in range(len(layout["donor_ids"])):
        condition = int(layout["condition"][np.flatnonzero(layout["donor"] == donor)[0]])
        donor_fractions[donor] = rng["donor"].dirichlet(fractions[condition] * 100.0)
    uniforms = rng["cell"].random(n)
    cumulative = np.cumsum(donor_fractions[layout["donor"]], axis=1)
    cell_type = np.minimum((uniforms[:, None] > cumulative).sum(axis=1), k - 1)
    effect = np.zeros((g, k))
    if recipe.case_id in {"T14-state-only", "T14-one-donor"}:
        effect[panels[6], 0] = 1.0
    elif recipe.case_id == "T14-rare-responder":
        effect[panels[6], -1] = 1.5
    elif recipe.case_id == "T14-cancellation":
        # Cancellation is exact in the declared population mean, with
        # opposite concentration shifts; it is NOT +/- equal log fold-change.
        panel = panels[6]
        delta = 0.4 * np.minimum(profiles[panel, 0], profiles[panel, 1])
        effect[panel, 0] = np.log2((profiles[panel, 0] + delta) / profiles[panel, 0])
        effect[panel, 1] = np.log2((profiles[panel, 1] - delta) / profiles[panel, 1])
    expected = profiles[:, cell_type].copy()
    expected *= np.exp2(effect[:, cell_type] * (layout["condition"] > 0)[None, :])
    donor_cell_counts = np.bincount(layout["donor"] * k + cell_type,
                                    minlength=len(layout["donor_ids"]) * k).reshape(-1, k)
    represented_donors = np.stack([
        (donor_cell_counts[layout["donor_condition"] == group] > 0).sum(axis=0)
        for group in range(len(p.condition_labels))
    ])
    captured_counts_by_condition = np.stack([
        donor_cell_counts[layout["donor_condition"] == group].sum(axis=0)
        for group in range(len(p.condition_labels))
    ])
    realized_captured_fractions = captured_counts_by_condition / captured_counts_by_condition.sum(axis=1, keepdims=True)
    return {"expected": expected, "cell_type": cell_type, "profiles": profiles,
            "fractions": fractions, "donor_fractions": donor_fractions,
            "donor_cell_counts": donor_cell_counts, "represented_donors": represented_donors,
            "realized_captured_fractions": realized_captured_fractions,
            "state_effect": effect, "panels": panels, "marker_panels": marker_panels,
            "type_labels": type_labels}


def _trajectory(np: Any, recipe: BiologyRecipe, rng: dict[str, Any], layout: dict[str, Any], state: dict[str, Any], cell_ids: tuple[str, ...]) -> tuple[dict[str, Any], dict[str, Any]]:
    p, case = recipe.parameters, recipe.case_id
    n = len(cell_ids)
    tau = rng["cell"].uniform(0.0, 1.0, n)
    if len(p.time_grid) > 1 and case != "T16-age-mismatch":
        position = layout["position"] / (len(p.time_grid) - 1)
        # Chronology changes the sampling distribution, without identifying
        # an individual cell's latent developmental age or path.
        tau = rng["cell"].beta(1.0 + 5.0 * position, 1.0 + 5.0 * (1.0 - position))
    if case == "T16-missing-intermediate":
        tau = np.where(tau < 0.5, tau * 0.7, 0.7 + (tau - 0.5) * 0.6)
    population_branch_fraction = 0.02 if case == "T16-rare-branch" else 0.5
    proliferation = np.asarray([1.0, 4.0 if case == "T16-proliferation" else 1.0])
    population_weights = np.asarray([1.0 - population_branch_fraction, population_branch_fraction]) * proliferation
    population_weights /= population_weights.sum()
    capture = np.asarray([1.0, 0.15 if case == "T16-capture-biased" else 1.0])
    sampled_weights = population_weights * capture
    sampled_weights /= sampled_weights.sum()
    branch = (rng["cell"].random(n) < sampled_weights[1]).astype(np.int64)
    linear = case == "T15-linear" or recipe.template_id == "T17"
    if linear:
        branch[:] = 0
    convergence = case in {"T15-convergence-history", "T15-convergence-unmarked", "T15-direction-reversed"}
    event = 0.45 if not convergence else 0.55
    # First-order transcript responses to a shared production switch and
    # lineage-specific production after bifurcation. Convergent founder pools
    # undergo first-order plus constant clearance until exhaustion, with zero
    # flux at the nonnegative boundary. This gives an actual finite merge.
    shared_induction = 1.0 - np.exp(-4.0 * tau)
    shared_decay = np.exp(-4.0 * tau)
    if convergence:
        residual_at_merge = np.exp(-10.0 * event)
        branch_program = np.maximum(
            (np.exp(-10.0 * tau) - residual_at_merge) / (1.0 - residual_at_merge), 0.0)
        # Before depletion, dB/dt = -10 B - 10 c, with
        # c = exp(-10*event)/(1-exp(-10*event)); B(0)=1, B(event)=0.
        # Shared maturation continues after the source pools merge; retain
        # that observed common tail as a separate edge in the truth graph.
        edges = (("origin_a", "merged"), ("origin_b", "merged"), ("merged", "terminal"))
    else:
        branch_program = 1.0 - np.exp(-7.0 * np.maximum(tau - event, 0.0))
        edges = (("root", "fork"), ("fork", "fate_a"), ("fork", "fate_b"))
    if linear:
        branch_program[:] = 0.0
        edges = (("root", "terminal"),)
    branch_resolved = np.ones(n, dtype=bool) if linear else (tau < event if convergence else tau > event)
    panels = state["panels"]
    # State-dependent synthesis contributes molecule abundance, then the
    # shared donor/capture/count model observes it. This is not a 2-D embedding.
    for panel, activity in ((panels[7], shared_induction), (panels[8], shared_decay),
                            (panels[9], branch_program * (branch == 0)),
                            (panels[10], branch_program * (branch == 1))):
        state["expected"][panel, :] *= 1.0 + 3.0 * activity[None, :]
    truth = {"latent_time": tau, "branch": branch, "graph": edges,
             "clone_equivalence": 2 * layout["donor"] + branch,
             "topology": "line" if linear else ("convergence" if convergence else "bifurcation"),
             "kinetics": "finite_pool_first_order_and_constant_clearance" if convergence else "first_order_production_switch_and_degradation",
             "lineage_program_concentration": branch_program,
             "merge_time": event if convergence else None,
             "merge_definition": "lineage-specific concentration is exactly zero at and after merge_time" if convergence else None,
             "event_time": event, "population_branch_fractions": population_weights,
             "captured_branch_fractions": sampled_weights, "capture_probability": capture,
             "branch_resolved_mask": branch_resolved,
             "observable_state_index": np.where(branch_resolved, branch + 1, 0),
             "proliferation_multiplier": proliferation,
             "within_branch_order_only": True, "absolute_transition_speed_identifiable": False}
    if case == "T15-direction-reversed":
        # The paired mechanism has exactly the same observation distribution.
        # Time-reversing the positive concentration path changes its required
        # production/degradation schedule and ancestry, not its snapshots.
        truth["latent_time"] = 1.0 - tau
        truth["graph"] = tuple((end, start) for start, end in edges)
        truth["topology"] = "bifurcation"
        truth["event_time"] = 1.0 - event
        truth["merge_time"] = None
        truth["merge_definition"] = None
        truth["kinetics"] = "time_reversed_positive_concentration_path_with_reversed_production_schedule"
    public = {}
    if case in {"T15-linear", "T15-bifurcation", "T15-convergence-history"}:
        records = _opaque_ids(rng["reference"], len(layout["donor_ids"]) * 2, "trace")
        # An explicitly independent retained origin assay. It does not derive
        # labels from expression. A recorded pulse before collection supports
        # clone/origin equivalence only; it does not orient the expression path.
        public["history_markers"] = {
            "measurement": "synthetic_independent_lineage_record_assay",
            "recording_scheme": "founder barcode retained in sampled descendants",
            "founder_pulse_precedes_collection": True,
            "founder_expression_state_measured": False,
            "direction_anchor_available": False,
            "cell_records": tuple({"cell_id": cell, "founder_record": records[int(layout["donor"][i]) * 2 + int(branch[i])]}
                                  for i, cell in enumerate(cell_ids)),
        }
    if recipe.template_id == "T16" and case != "T16-capture-biased":
        public["capture_calibration"] = {
            "sampling_probability": 1.0,
            "scope": "equal relative capture across viable cells within sampled collection windows",
            "absolute_cell_counts_measured": False,
            "unobserved_windows_covered": case != "T16-missing-intermediate",
        }
    return truth, public


def _cycle(np: Any, recipe: BiologyRecipe, rng: dict[str, Any], state: dict[str, Any], trajectory: dict[str, Any], gene_ids: tuple[str, ...]) -> tuple[dict[str, Any], dict[str, Any]]:
    tau = trajectory["latent_time"]
    phase = rng["cell"].uniform(0.0, 2.0 * np.pi, len(tau))
    if recipe.case_id == "T17-correlated-axes":
        phase = (2.0 * np.pi * tau + rng["cell"].normal(0, 0.35, len(tau))) % (2.0 * np.pi)
    elif recipe.case_id == "T17-synchronized":
        phase = rng["cell"].vonmises(0.8, 25.0, len(tau)) % (2.0 * np.pi)
    for panel, phase_shift in ((state["panels"][5], 0.0), (state["panels"][6], np.pi / 2.0)):
        # Positive periodic transcriptional production around a basal rate.
        state["expected"][panel] *= 2.0 + 0.9 * np.cos(phase + phase_shift)[None, :]
    public = {}
    if recipe.case_id == "T17-correlated-axes":
        public["process_markers"] = {
            "source": "independent synthetic process annotation",
            "cycle": tuple(gene_ids[int(i)] for i in state["panels"][5]),
            "maturation": tuple(gene_ids[int(i)] for i in state["panels"][7]),
        }
    return {"phase_radians": phase, "rotation_invariant": True, "reflection_invariant": True,
            "maturation_axis": tau, "cycle_is_not_nuisance_by_default": True}, public


def _regulatory(np: Any, recipe: BiologyRecipe, rng: dict[str, Any], state: dict[str, Any], layout: dict[str, Any], gene_ids: tuple[str, ...]) -> tuple[dict[str, Any], dict[str, Any]]:
    g, n = state["expected"].shape
    regulator_indices = rng["program"].choice(g, size=8, replace=False)
    loadings = np.zeros((g, 4))
    for factor in range(4):
        loadings[state["panels"][6 + factor], factor] = rng["program"].uniform(0.4, 0.9, len(state["panels"][6 + factor]))
        loadings[regulator_indices[factor], factor] += 0.45
    # Candidate TF annotation is independent of program-panel assignment;
    # explicitly remove accidental coefficients for the inactive candidates.
    loadings[regulator_indices[4:], :] = 0.0
    activity = rng["cell"].normal(0.0, 0.65, (4, n))
    activity += rng["donor"].normal(0.0, 0.3, (4, len(layout["donor_ids"])))[:, layout["donor"]]
    state["expected"] *= np.exp(loadings @ activity)
    # TF transcript abundance is a noisy proxy for activity, not a direct-edge
    # intervention. The last four annotated TFs have no planted activity role.
    state["expected"][regulator_indices[:4], :] *= np.exp(rng["cell"].normal(0, 0.5, (4, n)))
    public: dict[str, Any] = {}
    regulon_rows = []
    motif_rows = []
    if recipe.case_id != "T19-expression-only":
        for factor, regulator in enumerate(regulator_indices):
            true_candidates = np.flatnonzero(loadings[:, factor] > 0) if factor < 4 else np.empty(0, dtype=np.int64)
            complementary = np.flatnonzero(loadings[:, factor] == 0) if factor < 4 else np.arange(g)
            if recipe.case_id == "T19-decoy-priors" or not len(true_candidates):
                chosen = rng["reference"].choice(complementary, size=min(8, len(complementary)), replace=False)
            else:
                overlap = rng["reference"].choice(true_candidates, size=min(4, len(true_candidates)), replace=False)
                decoys = rng["reference"].choice(complementary, size=4, replace=False)
                chosen = np.concatenate((overlap, decoys))
            for gene in chosen:
                regulon_rows.append({"tf_id": gene_ids[int(regulator)], "target_id": gene_ids[int(gene)]})
            # Motif occurrence is its own assay table, only partly overlapping
            # curated membership. Neither table is binding or a private graph.
            hit_pool = np.unique(np.concatenate((chosen[:len(chosen) // 2], rng["reference"].choice(g, size=4, replace=False))))
            motif_rows.extend({"tf_id": gene_ids[int(regulator)], "target_id": gene_ids[int(gene)], "hit_count": 1}
                              for gene in hit_pool)
        # Public row order cannot reveal which internal TF slots were active.
        regulon_rows.sort(key=lambda row: (row["tf_id"], row["target_id"]))
        motif_rows.sort(key=lambda row: (row["tf_id"], row["target_id"]))
        public["regulon_annotation"] = {"reference_kind": "synthetic_curated_candidate_membership",
                                       "version": "1", "complete_negative_reference": False,
                                       "memberships": tuple(regulon_rows)}
    if recipe.case_id in {"T19-motif-supported", "T19-decoy-priors"}:
        public["motif_hit_annotation"] = {"reference_kind": "synthetic_sequence_motif_occurrences",
                                          "genome_build": "synthetic_feature_universe_v1",
                                          "version": "1", "binding_measured": False,
                                          "hits": tuple(motif_rows)}
    return {"loadings": loadings, "activity": activity,
            "annotated_tf_indices": regulator_indices,
            "generating_model": "latent common causes with noisy TF transcript proxies",
            "gene_causal_orientation_identifiable": False,
            "curated_reference": tuple(regulon_rows), "motif_reference": tuple(motif_rows)}, public


def _perturbation(np: Any, recipe: BiologyRecipe, rng: dict[str, Any], state: dict[str, Any], layout: dict[str, Any], gene_ids: tuple[str, ...], cell_ids: tuple[str, ...]) -> tuple[dict[str, Any], dict[str, Any]]:
    g, n = state["expected"].shape
    nodes = rng["program"].choice(g, size=4, replace=False)
    a_id, b_id, mediator_id, off_id = (gene_ids[int(i)] for i in nodes)
    group = layout["condition"]
    double = recipe.case_id == "T20-double-perturbation"
    assigned_a = (group == 1) | ((group == 3) if double else False)
    assigned_b = ((group == 2) | (group == 3)) if double else np.zeros(n, dtype=bool)
    efficacy = np.ones(n)
    if recipe.case_id == "T20-variable-efficacy":
        donor_efficacy = rng["intervention"].beta(3.0, 3.0, len(layout["donor_ids"]))
        average = donor_efficacy[layout["donor"]]
        efficacy = rng["intervention"].beta(average * 12.0, (1.0 - average) * 12.0)
    if recipe.case_id == "T20-control":
        efficacy[:] = 0.0
    ea, eb = efficacy * assigned_a, efficacy * assigned_b
    # Acyclic concentration/production structural model. Shared exogenous
    # noise is reused for counterfactual definitions, never inferred from data.
    donor_noise = rng["donor"].uniform(-0.04, 0.04, (3, len(layout["donor_ids"])))[:, layout["donor"]]
    exogenous = donor_noise + rng["cell"].uniform(-0.04, 0.04, (3, n))
    a = 1.2 - ea + exogenous[0]
    b = 1.2 - eb + exogenous[1]
    mediator = 0.5 + 0.6 * a + exogenous[2]
    weights = np.zeros((g, 4))  # A, B, mediator, A*B production inputs
    weights[state["panels"][6], 0] = 0.8
    weights[state["panels"][7], 2] = 0.8
    weights[state["panels"][8], 1] = 0.7
    if recipe.case_id == "T20-indirect-cascade":
        weights[:, 0] = 0.0
        weights[state["panels"][6], 2] = 0.8
    if double:
        weights[state["panels"][9], 3] = 0.9
    # Structural nodes have explicit mechanistic readouts, rather than also
    # receiving an accidental random downstream panel assignment.
    weights[nodes] = 0.0
    inputs = np.vstack((a, b, mediator, a * b))
    rates = 1.0 + weights @ inputs
    rates[nodes[0]] = a
    rates[nodes[1]] = b
    rates[nodes[2]] = mediator
    off_target_weights = np.zeros(g)
    if recipe.case_id == "T20-off-target":
        off_target_weights[state["panels"][10]] = 0.7
        off_target_weights[nodes] = 0.0
        rates += off_target_weights[:, None] * (0.7 * assigned_a)[None, :]
        rates[nodes[3]] += 0.7 * assigned_a
    state["expected"] *= rates

    # Population-mean production factors are analytic for this linear SEM
    # with centered independent exogenous noise. Efficacy is independent of
    # assignment; the variable-efficacy mean is 1/2 by construction.
    mean_efficacy = 0.0 if recipe.case_id == "T20-control" else (0.5 if recipe.case_id == "T20-variable-efficacy" else 1.0)
    factors = []
    for condition in range(len(recipe.parameters.condition_labels)):
        mean_a = 1.2 - mean_efficacy * (condition in ({1, 3} if double else {1}))
        mean_b = 1.2 - mean_efficacy * (double and condition in {2, 3})
        mean_m = 0.5 + 0.6 * mean_a
        row = 1.0 + weights @ np.asarray([mean_a, mean_b, mean_m, mean_a * mean_b])
        row[nodes[0]], row[nodes[1]], row[nodes[2]] = mean_a, mean_b, mean_m
        if recipe.case_id == "T20-off-target" and condition == 1:
            row += 0.7 * off_target_weights
            row[nodes[3]] += 0.7
        factors.append(row)
    factors = np.stack(factors, axis=1)
    effect = np.log2(factors / factors[:, :1])
    # The unknown baseline abundance and independent multiplicative capture
    # cancel in this control-normalized contrast. A raw production-factor
    # difference would depend on an unobserved gene-specific scale.
    epistasis = (factors[:, 3] - factors[:, 1] - factors[:, 2] + factors[:, 0]) / factors[:, 0] if double else None
    edges = [(a_id, mediator_id, 0.6)]
    for source, col in ((a_id, 0), (b_id, 1), (mediator_id, 2)):
        edges.extend((source, gene_ids[int(i)], float(weights[i, col])) for i in np.flatnonzero(weights[:, col]))
    if double:
        for i in np.flatnonzero(weights[:, 3]):
            edges.extend(((a_id, gene_ids[int(i)], float(weights[i, 3])), (b_id, gene_ids[int(i)], float(weights[i, 3]))))
    if recipe.case_id == "T20-off-target":
        edges.extend((off_id, gene_ids[int(i)], float(off_target_weights[i])) for i in np.flatnonzero(off_target_weights))
    guides = tuple({"cell_id": cell, "assigned_targets": (() if recipe.case_id == "T20-control" else
                     tuple(target for yes, target in ((assigned_a[i], a_id), (assigned_b[i], b_id)) if yes)),
                    "assignment": recipe.parameters.condition_labels[int(group[i])]}
                   for i, cell in enumerate(cell_ids))
    public: dict[str, Any] = {"guide_assignments": {"randomization_unit": "donor", "allocation": "balanced randomized",
                                                   "negative_control": recipe.parameters.condition_labels[0], "records": guides}}
    if recipe.case_id not in {"T20-variable-efficacy", "T20-off-target"}:
        # Publish only target identities already declared by randomized guides.
        # In particular, an unused latent B node is not exposed in single-target
        # tasks, and negative-control-only assignments have no target map.
        target_to_column = {} if recipe.case_id == "T20-control" else {a_id: "target_a_reduction"}
        if double:
            target_to_column[b_id] = "target_b_reduction"
        public["efficacy_measurement"] = {
            "assay": "synthetic_achieved_intervention_dose_readout",
            "target_to_column": target_to_column,
            "units": "achieved_intervention_dose_reduction",
            "reference_value": 0.0,
            "full_intervention_value": 1.0,
            "scale_definition": "0.0 means no prescribed intervention; 1.0 means the full prescribed intervention dose, not 100 percent transcript knockdown or elimination of target expression",
            "assay_scope": "simulated readout of achieved prescribed dose, separate from expression counts; not an expression-derived efficacy estimate",
            "evidence_scope": "synthetic_model_declared_achieved_dose_readout",
            "assay_qualification": "not_empirically_qualified",
            "unmapped_columns_scope": "zero control readouts without a target identity; never bind an unmapped column to a gene",
            "identification_assumptions": {
                "randomized_assignment": True,
                "randomization_unit": "donor",
                "consistency_assumed": True,
                "target_specificity_assumed": True,
                "exclusion_restriction_assumed": True,
                "no_between_donor_interference_assumed": True,
                "achieved_dose_observed_for_mapped_targets": True,
                "measurement_is_expression_estimated": False,
                "scope": "conditional_known_simulation_design_not_empirical_certification",
                "specificity_definition": "each guide changes only its prescribed target intervention dose; downstream responses are allowed",
                "exclusion_definition": "randomized guide assignment affects expression only through the prescribed target interventions",
                "consistency_definition": "the same target set and achieved-dose vector define the same intervention across donors",
                "interference_definition": "one donor assignment does not affect another donor measurements",
                "claim_scope": "conditional achieved-intervention response; direct edges and binding remain unidentified",
            },
            "records": tuple({"cell_id": cell, "target_a_reduction": float(ea[i]), "target_b_reduction": float(eb[i])}
                             for i, cell in enumerate(cell_ids)),
        }
    return {"nodes": tuple(gene_ids[int(i)] for i in nodes), "structural_edges": tuple(edges),
            "structural_model": "acyclic linear production with optional bilinear interaction",
            "weights": weights, "efficacy_a": ea, "efficacy_b": eb,
            "assignment_effect_log2": effect, "mean_production_factor": factors,
            "epistasis_control_normalized": epistasis,
            "causal_truth_scope": "known synthetic structural equations and exogenous independence only",
            "direct_edges_identifiable_from_public_data": False}, public


def _artifacts(np: Any, recipe: BiologyRecipe, rng: dict[str, Any], state: dict[str, Any], layout: dict[str, Any], expected: Any) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    case, p = recipe.case_id, recipe.parameters
    g, n = expected.shape
    fraction = np.zeros(n)
    public: dict[str, Any] = {}
    ambient = state["profiles"][:, 0].copy()
    ambient /= ambient.sum()
    if case in {"T18-ambient-reference", "T18-ambient-unresolved"}:
        fraction = 0.05 + 0.30 * rng["artifact"].beta(2.0, 4.0, n)
        total = expected.sum(axis=0)
        expected = expected * (1.0 - fraction)[None, :] + ambient[:, None] * (total * fraction)[None, :]
        if case == "T18-ambient-reference":
            public["ambient_reference"] = {"measurement": "independent empty-droplet UMI aggregate",
                                            "counts": rng["reference"].poisson(ambient * 50000).astype(np.int64),
                                            "capture_batch_scope": "shared synthetic ambient reservoir"}
    counts = _sample_counts(np, rng["assay"], expected, p.dispersion)
    doublet = np.zeros(n, dtype=bool)
    partners = np.full(n, -1, dtype=np.int64)
    if case in {"T18-homotypic-doublet", "T18-heterotypic-doublet"}:
        doublet = rng["artifact"].random(n) < 0.12
        for specimen in range(len(layout["specimen_ids"])):
            members = np.flatnonzero(layout["specimen"] == specimen)
            for cell_type in range(p.cell_type_count):
                selected = members[doublet[members] & (state["cell_type"][members] == cell_type)]
                match = state["cell_type"][members] == cell_type
                pool = members[match if case == "T18-homotypic-doublet" else ~match]
                if len(selected) and not len(pool):
                    raise ValueError("doublet component state has no specimen support; do not fabricate a partner")
                if len(selected):
                    partners[selected] = rng["artifact"].choice(pool, len(selected), replace=True)
        selected = np.flatnonzero(doublet)
        if len(selected):
            # Independent molecule sampling from a second cell of a specified
            # type. A stored observed count column is never copied as a cell.
            second = _sample_counts(np, rng["assay"], expected[:, partners[selected]], p.dispersion)
            counts[:, selected] += second
    censor = np.zeros(n, dtype=bool)
    if case == "T18-non-umi-censoring":
        # A separately declared non-UMI capture/amplification protocol. Low
        # molecule templates may fail amplification; retained templates yield
        # read counts with gene-specific amplification efficiencies.
        amplification = rng["assay"].lognormal(np.log(3.0), 0.4, (g, 1))
        retained = rng["assay"].random((g, n)) < (1.0 - np.exp(-counts / 2.0))
        censor = np.any((counts > 0) & ~retained, axis=0)
        counts = rng["assay"].poisson(counts * amplification * retained).astype(np.int64)
    return counts, {"ambient_fraction": fraction, "ambient_profile": ambient,
                    "doublet_mask": doublet, "second_component_profile_index": partners,
                    "non_umi_censoring_present": censor}, public


def generate_biology_singlecell(recipe: BiologyRecipe, *, allow_data_execution: bool = False) -> SingleCellBiologyBundle:
    """Generate only after literal opt-in; performs no files/network/providers.

    The gate comes before even recipe inspection. A caller must separately
    authorize dataset creation and prevent private_truth reaching benchmark arms.
    """
    if allow_data_execution is not True:
        raise PermissionError("biological data execution requires allow_data_execution=True")
    plan = validate_biology_singlecell_recipe(recipe)
    import numpy as np

    p = recipe.parameters
    # Role and mechanism are not encoded in public identifiers. Distinct child
    # streams separate biological units, cells, assays, priors and interventions.
    role = {"training": 1, "calibration": 2, "evaluation": 3}[recipe.role]
    seed = np.random.SeedSequence([recipe.generation_seed, role, 141520])
    rng = {name: np.random.default_rng(child) for name, child in zip(_STREAMS, seed.spawn(len(_STREAMS)))}
    layout = _layout(np, recipe, rng)
    gene_ids = _opaque_ids(rng["identifiers"], p.num_genes, "g")
    cell_ids = _opaque_ids(rng["identifiers"], plan.observation_upper_bound, "c")
    state = _cell_state(np, recipe, rng, layout, gene_ids)
    annotations: dict[str, Any] = {}
    latent: dict[str, Any] = {}
    if recipe.template_id in {"T15", "T16", "T17"}:
        latent["trajectory"], extra = _trajectory(np, recipe, rng, layout, state, cell_ids)
        annotations.update(extra)
    if recipe.template_id == "T17":
        latent["cycle"], extra = _cycle(np, recipe, rng, state, latent["trajectory"], gene_ids)
        annotations.update(extra)
    if recipe.template_id == "T19":
        latent["regulatory"], extra = _regulatory(np, recipe, rng, state, layout, gene_ids)
        annotations.update(extra)
    if recipe.template_id == "T20":
        latent["perturbation"], extra = _perturbation(np, recipe, rng, state, layout, gene_ids, cell_ids)
        annotations.update(extra)
    if recipe.case_id == "T18-viable-low-rna":
        state["expected"][:, state["cell_type"] == p.cell_type_count - 1] *= 0.18
        state["profiles"][:, -1] *= 0.18
    # Donor effects are gene-specific and shared across that donor's cells.
    donor_effect = rng["donor"].normal(0.0, 0.2 + 0.1 * p.nuisance_intensity,
                                       (p.num_genes, len(layout["donor_ids"])))
    specimen_effect = rng["donor"].normal(0.0, 0.08, (p.num_genes, len(layout["specimen_ids"])))
    batch_effect = rng["donor"].normal(0.0, 0.15 * p.nuisance_intensity, (p.num_genes, p.num_batches))
    capture = rng["cell"].lognormal(-0.5 * 0.4 ** 2, 0.4, len(cell_ids))
    expected = state.pop("expected")
    expected *= np.exp(donor_effect[:, layout["donor"]] + specimen_effect[:, layout["specimen"]] + batch_effect[:, layout["batch"]])
    expected *= capture[None, :]
    expression, latent["measurement"], extra = _artifacts(np, recipe, rng, state, layout, expected)
    annotations.update(extra)
    del expected, specimen_effect, batch_effect
    if recipe.template_id in {"T14", "T15", "T16", "T17"}:
        annotations["cell_annotation_reference"] = {
            "reference_identity": "synthetic_independent_marker_dictionary_v1",
            "reference_scope": "broad types represented in this synthetic feature universe",
            "markers": tuple(sorted(
                ({"label": state["type_labels"][i], "gene_ids": tuple(sorted(gene_ids[int(j)] for j in panel))}
                 for i, panel in enumerate(state["marker_panels"])), key=lambda row: row["label"])),
            "query_cell_labels_supplied": False,
        }
    shared_projection = None
    if recipe.template_id in {"T15", "T16", "T17"}:
        shared_projection = _shared_process_projection(np, state, gene_ids, annotations["cell_annotation_reference"])
    metadata = []
    for index, cell in enumerate(cell_ids):
        row: dict[str, Any] = {"cell_id": cell,
            "donor_id": layout["donor_ids"][int(layout["donor"][index])],
            "specimen_id": layout["specimen_ids"][int(layout["specimen"][index])],
            "library_id": layout["library_ids"][int(layout["specimen"][index])],
            "batch": layout["batch_ids"][int(layout["batch"][index])],
            "capture_batch": layout["batch_ids"][int(layout["batch"][index])],
            "condition": p.condition_labels[int(layout["condition"][index])]}
        if p.time_grid:
            row["collection_age"] = float(p.time_grid[int(layout["position"][index])])
        metadata.append(row)
    targets = _assemble_targets(np, recipe, plan, state, latent,
                                gene_ids=gene_ids, observation_ids=cell_ids)
    if shared_projection is not None:
        projection_targets = {"T15": ("state_topology",), "T16": ("sampled_state_geometry",),
                              "T17": ("maturation_order", "cycle_state")}[recipe.template_id]
        for target_id in projection_targets:
            support_reference = targets[target_id].get("observed_support_reference")
            targets[target_id].update(
                shared_process_projection=shared_projection,
                projection_scope=shared_projection["scope"], projection_rule=shared_projection["rule"],
                excluded_feature_ids=shared_projection["excluded_feature_ids"],
                retained_feature_universe=shared_projection["retained_feature_universe"],
                analysis_layer=shared_projection["analysis_layer"],
                full_geometry_scope=shared_projection["full_geometry_scope"],
                prediction_reference="known simulated shared-process quotient after excluding the public broad-type marker union; all observations retained" + ("; " + support_reference if support_reference else ""),
                projected_baseline_type_invariant=True,
                baseline_invariance_scope="conditional synthetic profiles only; not a claim of full observed type independence",
                full_type_by_state_geometry_scored=False)
    enrichment_query = None
    if recipe.template_id == "T19" and ("regulon_annotation" in annotations or "motif_hit_annotation" in annotations):
        enrichment_query = _observed_enrichment_query(np, expression, gene_ids, max_working_bytes=p.max_matrix_bytes)
        for target_id in ("regulon_membership", "motif_occurrence"):
            if targets[target_id]["scope"] != "refusal":
                targets[target_id].update(enrichment_query)
                targets[target_id].update(
                    numerical_reference_scope="descriptive overlap for the fixed public query and measured-gene background",
                    same_expression_selection=True, calibrated_enrichment_p_values_or_fdr=False,
                    independent_selection_assay=False, causal_evidence=False)
    context = {
        "assay": "synthetic_non_UMI_amplified_read_counts" if plan.measurement == "non_umi_counts" else "synthetic_UMI_molecule_counts",
        "measurement": plan.measurement, "measurement_units": "reads" if plan.measurement == "non_umi_counts" else "captured_unique_molecules",
        "matrix_orientation": "genes_by_captured_cells", "expression_layer": "counts",
        "transformation_history": (), "feature_universe": gene_ids,
        "biological_unit": "donor", "nested_observation_roles": ("donor", "specimen", "library", "cell"),
        "condition_assignment_unit": "donor", "independent_units_per_condition": p.independent_units_per_group,
        "metadata_aliases": {"batch": "capture_batch"},
        "capture_batch_definition": "specimen-level capture allocation; batch and capture_batch identify exactly the same grouping",
        "condition_labels": p.condition_labels, "collection_age_units": p.time_units,
        "repeated_specimens_per_donor": max(1, len(p.time_grid), p.repeated_specimens_per_unit),
        "annotation_ids": tuple(annotations), "requested_targets": _TARGETS[recipe.template_id],
        "absolute_cell_abundance_measured": False, "spliced_unspliced_layers_available": False,
        "mitochondrial_feature_annotation_available": False,
        "population_reference": "viable cells within sampled collection windows",
        "protocol": "molecule sampling without an added dropout component" if plan.measurement == "umi_counts" else "template failure at low molecule count followed by gene-specific amplification and read sampling",
        "limitations": ("cells from one donor are not independent biological replicates",
                        "collection age does not observe latent developmental time",
                        "a single count matrix does not support RNA velocity",
                        "snapshot abundance does not identify transition speed or fate probability"),
    }
    if recipe.template_id in {"T19", "T20"}:
        indices = latent["regulatory"]["annotated_tf_indices"] if recipe.template_id == "T19" else None
        # TF annotation is declared independently of supplied edge priors; it
        # includes inactive TFs and makes no true-active-edge claim.
        context["transcription_factor_annotation"] = tuple(sorted(gene_ids[int(i)] for i in indices)) if indices is not None else ()
        context["regulatory_evidence_limits"] = ("association is not direct causality", "motif occurrence is not measured binding", "unknown public edges are not verified negatives")
    if enrichment_query is not None:
        context.update(enrichment_query)
    if shared_projection is not None:
        context["shared_process_projection"] = shared_projection
        context["projection_interpretation"] = "the shared-process graph or axes describe the declared marker-excluded feature view only; full type-by-process geometry remains biological variation, and type-marker genes remain relevant to other questions"
    if recipe.template_id == "T17":
        context["primary_process"] = "cell_cycle" if recipe.case_id == "T17-cycle-target" else "maturation"
        context["cycle_phase_invariances"] = ("rotation", "reflection")
    if recipe.template_id == "T18":
        context["multiplet_identity_scope"] = "mixed-type captured multiplets have unordered or ambiguous components; one ordered first-component clean-cell label is not observed"
        context["artifact_detection_calibration"] = "unavailable without a separately qualified observation-specific detection reference"
    if recipe.template_id == "T15":
        context["history_evidence_scope"] = "supplied founder records support clone/origin equivalence only; no measured founder state or direction anchor"
    if recipe.case_id == "T20-double-perturbation":
        context["target_contrasts"] = {
            "epistasis": {"formula": "(mu_AB - mu_A - mu_B + mu_control) / mu_control",
                          "units": "dimensionless_control_normalized_mean_nonadditivity",
                          "mean_definition": "population mean expression under randomized assignment"},
        }
    if recipe.template_id == "T16":
        context["target_populations"] = {
            "population_abundance": "branch-resolved viable cells within sampled collection windows; shared precursors do not carry observed future-fate labels",
            "sampled_state_geometry": "all sampled viable cells on observed support",
        }
        # Ask the same observed-support question across sampling variants.
        # The private answer's connectivity must not be announced in guidance.
    if recipe.template_id == "T14" and targets["captured_abundance"]["scope"] == "limited_reference":
        captured_reference = targets["captured_abundance"]
        context["population_reference"] = "observed captured subset"
        context["target_populations"] = {"captured_abundance": "observed captured subset within each condition"}
        context["captured_abundance_reference_scope"] = "descriptive captured-subset proportions; population differences are not supported by one donor per condition"
        context["target_references"] = {
            "captured_abundance": {field: captured_reference[field] for field in
                                   ("estimand", "contrast", "units", "reference_population", "prediction_reference")},
        }
    private = {
        "case_id": recipe.case_id, "generation_seed": recipe.generation_seed, "role": recipe.role,
        "rng_stream_roles": _STREAMS, "truth_scope": "known_simulation_only_not_empirical_validation",
        "targets": targets, "cell_type": state["cell_type"], "type_labels": state["type_labels"],
        "baseline_type_profiles": state["profiles"], "donor_fractions": state["donor_fractions"],
        "donor_cell_counts": state["donor_cell_counts"],
        "donor_effect": donor_effect, "donor_index": layout["donor"],
        "specimen_index": layout["specimen"], "capture_exposure": capture, **latent,
    }
    if shared_projection is not None:
        private["unscored_full_type_by_state"] = {
            "scope": "full_type_by_shared_process_latent_mean_structure",
            "cell_type": state["cell_type"], "cell_type_labels": state["type_labels"],
            "type_baseline_profiles": state["profiles"],
            "shared_process_graph": latent["trajectory"]["graph"],
            "shared_process_branch": latent["trajectory"]["branch"],
            "shared_process_time": latent["trajectory"]["latent_time"],
            "cycle_phase": latent["cycle"]["phase_radians"] if "cycle" in latent else None,
            "scored_by_shared_process_quotient": False,
        }
    if recipe.case_id == "T16-missing-intermediate":
        private["unscored_full_process_geometry"] = {
            "graph": latent["trajectory"]["graph"],
            "latent_time": latent["trajectory"]["latent_time"],
            "branch": latent["trajectory"]["branch"],
            "scope": "known simulation outside observed support; not a scored connectivity reference",
            "scored": False,
        }
    if _stored_numeric_bytes(np, (expression, private, annotations)) > p.max_matrix_bytes:
        raise ValueError("generated numeric layers exceeded the declared total storage bound")
    return SingleCellBiologyBundle(expression, gene_ids, cell_ids, tuple(metadata), plan.measurement, context, annotations, private)


def _assemble_targets(np: Any, recipe: BiologyRecipe, plan: SingleCellGenerationPlan, state: dict[str, Any], latent: dict[str, Any], *, gene_ids: tuple[str, ...], observation_ids: tuple[str, ...]) -> dict[str, Any]:
    targets = {name: {"scope": scope, "expected_refusal": scope == "refusal",
                     "numerical_scoring": {"supported": "eligible_if_estimated", "refusal": "not_identifiable", "limited_reference": "restricted_reference"}[scope],
                     "qualification": "unqualified_simulation_reference",
                     "prediction_reference": "private known simulation; publicly available evidence limits the scorable claim",
                     "required_limitation": "known simulated truth does not identify a claim from unavailable public measurements"}
               for name, scope in plan.target_scopes}
    template = recipe.template_id
    if template == "T14":
        targets["cell_state_effect"].update(effect=state["state_effect"], nonnull=state["state_effect"] != 0,
                                           gene_ids=gene_ids, cell_type_labels=state["type_labels"],
                                           condition_labels=recipe.parameters.condition_labels,
                                           effect_axes=("gene_ids", "cell_type_labels"),
                                           donor_support_axes=("condition_labels", "cell_type_labels"),
                                           contrast=f"{recipe.parameters.condition_labels[1]} versus {recipe.parameters.condition_labels[0]} within each cell type",
                                           effect_units="log2_fold_change_within_type", biological_unit="donor",
                                           observed_donor_support=state["represented_donors"],
                                           observed_type_support_mask=np.all(state["represented_donors"] >= 3, axis=0),
                                           absent_donor_type_is_zero_expression=False)
        abundance = targets["captured_abundance"]
        abundance.update(realized_captured_fractions=state["realized_captured_fractions"],
                                            condition_labels=recipe.parameters.condition_labels,
                                            cell_type_labels=state["type_labels"],
                                            fraction_axes=("condition_labels", "cell_type_labels"),
                                            effect_axes=("cell_type_labels",),
                                            contrast=f"{recipe.parameters.condition_labels[1]} minus {recipe.parameters.condition_labels[0]}",
                                            effect_units="captured_cell_fraction_difference", denominator="all_captured_viable_cells")
        if abundance["scope"] == "limited_reference":
            realized = state["realized_captured_fractions"]
            abundance.update(effect=realized[1] - realized[0],
                             estimand="descriptive cell-type fraction difference within the observed captured subset",
                             reference_scope="observed_captured_subset",
                             reference_population="observed captured subset",
                             prediction_reference="realized captured-cell proportions pooled within each condition",
                             denominator="observed_captured_subset_within_condition",
                             unsupported_simulation_population_fractions=state["fractions"],
                             required_limitation="descriptive captured-subset composition only; one donor per condition does not support population differences")
        else:
            abundance.update(population_fractions=state["fractions"],
                             effect=state["fractions"][1] - state["fractions"][0],
                             reference_scope="population_captured_cell_composition")
    elif template in {"T15", "T16"}:
        geometry = latent["trajectory"]
        key = "state_topology" if template == "T15" else "sampled_state_geometry"
        targets[key].update(graph=geometry["graph"], branch=geometry["branch"], latent_time=geometry["latent_time"],
                            invariances=("node_labels", "unanchored_orientation"), within_branch_only=True,
                            observable_state_index=geometry["observable_state_index"],
                            branch_reference_mask=geometry["branch_resolved_mask"])
        if recipe.case_id == "T16-missing-intermediate":
            targets[key].pop("latent_time")
            targets[key].update(_observed_gap_geometry(np, geometry))
        if template == "T15":
            targets["ancestry"].update(clone_equivalence=geometry["clone_equivalence"],
                                       unidentifiable_cross_donor_origin=geometry["branch"],
                                       unidentifiable_simulation_direction=geometry["graph"],
                                       direction_identifiable=False,
                                       numerical_reference_scope="within_donor_clone_equivalence_only" if targets["ancestry"]["scope"] == "limited_reference" else "no_public_origin_reference",
                                       invariances=("clone_label_permutation",),
                                       limitation="retained founder barcodes support origin equivalence only; pulse precedence without a measured founder state does not orient the expression path")
        else:
            targets["population_abundance"].update(simulation_population_fractions=geometry["population_branch_fractions"],
                                                    denominator="branch_resolved_viable_cells_in_sampled_windows",
                                                    reference_mask=geometry["branch_resolved_mask"],
                                                    full_population_supported=False,
                                                    limitation="Excluding precursor cells requires an independent state root. Equal capture does not orient an unanchored snapshot graph.")
            targets["transition_dynamics"].update(limitation="cross-sectional abundance combines sampling, growth and occupancy; transition rates and fate probabilities are not identified")
    elif template == "T17":
        targets["maturation_order"].update(latent_time=latent["cycle"]["maturation_axis"], orientation_invariant=True)
        targets["cycle_state"].update(phase_radians=latent["cycle"]["phase_radians"], rotation_invariant=True, reflection_invariant=True,
                                      full_cycle_covered=recipe.case_id != "T17-synchronized")
    elif template == "T18":
        targets["measurement_diagnostics"].update(**latent["measurement"])
        cell_type = state["cell_type"]
        identity_reference_mask = np.ones(len(cell_type), dtype=bool)
        multiplets = np.flatnonzero(latent["measurement"]["doublet_mask"])
        partners = latent["measurement"]["second_component_profile_index"][multiplets]
        valid_partner = (partners >= 0) & (partners < len(cell_type))
        # A sum of two different cell types has no observed component order.
        # Keep singlets and same-type multiplets in the single-label reference;
        # neither this eligibility mask nor a latent artifact flag establishes
        # that a particular detector can identify multiplets from expression.
        identity_reference_mask[multiplets] = False
        identity_reference_mask[multiplets[valid_partner]] = cell_type[multiplets[valid_partner]] == cell_type[partners[valid_partner]]
        targets["clean_cell_state"].update(cell_type=state["cell_type"], baseline_type_profiles=state["profiles"],
                                           clean_identity_reference_mask=identity_reference_mask,
                                           clean_identity_numerical_reference_available=recipe.case_id != "T18-ambient-unresolved",
                                           cell_type_labels=state["type_labels"], observation_ids=observation_ids,
                                           reference_unit="captured_observation",
                                           class_label_invariances=("label_permutation",),
                                           identity_reference_scope="multiplet-order eligibility only; unresolved ambient decomposition does not provide a point-identity reference" if recipe.case_id == "T18-ambient-unresolved" else "single-label identity for singlets and same-type components only",
                                           mask_establishes_artifact_identifiability=False,
                                           artifact_detection_calibration="unavailable",
                                           homotypic_doublets_fully_identifiable=False,
                                           low_rna_viable_type=recipe.parameters.cell_type_count - 1 if recipe.case_id == "T18-viable-low-rna" else None)
    elif template == "T19":
        candidate_indices = latent["regulatory"]["annotated_tf_indices"]
        factor_tf_ids = tuple(gene_ids[int(index)] for index in candidate_indices[:4])
        targets["association_activity"].update(loadings=latent["regulatory"]["loadings"], activity=latent["regulatory"]["activity"],
                                               gene_ids=gene_ids, observation_ids=observation_ids,
                                               candidate_tf_ids=tuple(sorted(gene_ids[int(index)] for index in candidate_indices)),
                                               activity_factor_tf_ids=factor_tf_ids,
                                               loadings_column_ids=factor_tf_ids,
                                               loadings_axes=("gene_ids", "loadings_column_ids"),
                                               activity_axes=("activity_factor_tf_ids", "observation_ids"),
                                               numerical_reference_scope="latent_expression_program_subspace_after_equivalence_alignment",
                                               activity_reference_equivalence=("factor_permutation", "factor_sign", "factor_scale", "orthogonal_rotation"),
                                               activity_is_measured=False, causal_activity_identifiable=False,
                                               factor_ids_are_causal_tf_identity=False,
                                               causal_orientation_identifiable=False)
        targets["regulon_membership"].update(reference=latent["regulatory"]["curated_reference"], unknown_edges_are_negatives=False)
        targets["motif_occurrence"].update(reference=latent["regulatory"]["motif_reference"] if recipe.case_id in {"T19-motif-supported", "T19-decoy-priors"} else (), binding_inferred=False)
        targets["binding"].update(limitation="no binding assay was supplied")
    elif template == "T20":
        intervention = latent["perturbation"]
        targets["assignment_effect"].update(effect=intervention["assignment_effect_log2"], nonnull=intervention["assignment_effect_log2"] != 0,
                                            effect_units="log2_mean_production_factor_ratio", randomization_unit="donor")
        targets["successful_intervention"].update(limitation="identified only with the supplied efficacy assay and the specified exclusion assumptions",
                                                  known_simulation_efficacy_a=intervention["efficacy_a"])
        if targets["successful_intervention"]["scope"] == "supported":
            targets["successful_intervention"].update(effect=intervention["assignment_effect_log2"],
                                                      effect_units="log2_mean_production_factor_ratio")
        elif recipe.case_id == "T20-control":
            targets["successful_intervention"]["limitation"] = "negative-control assignments achieved no intended target intervention; successful-intervention positivity is absent"
        if intervention["epistasis_control_normalized"] is not None:
            targets["epistasis"].update(effect=intervention["epistasis_control_normalized"],
                                        effect_units="dimensionless_control_normalized_mean_nonadditivity",
                                        contrast="(mu_AB - mu_A - mu_B + mu_control) / mu_control")
        else:
            targets["epistasis"]["limitation"] = "no randomized two-factor design"
        targets["direct_edge"].update(known_simulation_edges=intervention["structural_edges"],
                                      limitation="randomized total response alone does not distinguish direct edges from mediator cascades or binding")
    for name, target in targets.items():
        target.setdefault("estimand", name)
        target["units"] = target.get("effect_units", "target_specific_reference")
        if target["scope"] == "refusal":
            for field in ("effect", "nonnull"):
                if field in target:
                    target["simulation_" + field] = target.pop(field)
    return targets
