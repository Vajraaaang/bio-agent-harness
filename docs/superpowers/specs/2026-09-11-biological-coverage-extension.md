# Biological coverage extension

## Scope

The six-scenario temporal study is insufficient to establish broad biological inference performance. The requested extension adds richer biological mechanisms, observation models and target-specific grading. It preserves the instruction not to generate or run data. Catalog entries, implemented generators, registered tools, graders, configuration checks and measured performance must be counted separately.

The source basis is the [biology coverage proposal](../../../reports/benchmark-redesign/biology-coverage-proposal.md) and [Single-cell Best Practices review](../../../reports/benchmark-redesign/single-cell-best-practices-review.md). The machine-readable contract currently contains 30 effect families, 20 coherent templates and 97 cases. These counts describe proposed/configured coverage; they do not establish executable support or validation.

## Required cases

Bulk cases cover nonlinear and signed temporal responses, pulse resolution and cycles, condition-by-time interactions versus baseline-only differences, nonlinear dose responses, weak and low-expression signals, mean-null variance changes, confounding and replication ladders, covariate-specific responses, normalization identification, shared expression modules, cell-mixture attribution, observation artifacts and transport across count/continuous generating families.

Single-cell cases cover donor-replicated cell-state effects versus abundance shifts, branch and convergence geometry, rare or incompletely sampled states, competing cellular programs, protocol-specific counts and technical contamination, regulatory evidence levels and randomized perturbation responses. Each case needs a coherent mechanism rather than a new name attached to the old bulk generator.

A hard case must have an informative counterpart where appropriate: identifiable versus confounded contrast, dense versus unresolved pulse, cell-state versus abundance-only change, valid versus absent controls, or observed associations versus intervention evidence. Expected restraint is target-specific. A dataset can support a relative tissue difference without identifying an absolute cell-loss claim or a direct causal edge.

## Architecture and interfaces

- `biology.py` and `biology_catalog.json` define measurement layers, biological units, target/contrast contracts, design requirements, bounded recipes, role separation and explicit runtime support registration.
- `biology_bulk.py` implements the exact bulk cases; `biology_singlecell.py` implements the exact cellular/regulatory cases. Both must reject by default before numerical imports or RNG calls and must not substitute a generic fallback for an unsupported case.
- Bundle output must separate expression, public observation metadata, permitted public reference annotations and coordinator-private truth. Gene/sample identifiers and random streams must not leak membership or answers.
- New target tools, evidence rules, answer schemas and graders must register explicitly. The original temporal fitter cannot certify dose response, cell annotation, composition, lineage or regulatory claims merely because it ran successfully.
- Preparation, three-condition execution, result collection and dashboard views must consume these target contracts without changing historical measured artifacts. Common scientific scoring remains identical across conditions, with raw correctness primary and report acceptance separate.

## Scientific constraints

Keep biological population units separate from cells, libraries and repeated specimens. A donor-as-batch correction is not a substitute for pseudobulk or a supported model for within-donor correlation. Store raw counts separately from normalized, integrated or residual layers; rounding a transformed layer does not recover raw counts.

Sampling zeros do not mandate an extra zero-inflation mechanism. Declare the protocol before selecting an observation model. Evaluate integration using both batch-removal and biology-preservation outcomes; a well-mixed embedding can still erase the target signal. QC should test retention of valid rare populations alongside removal of technical artifacts, without universal tissue-independent cutoffs.

Chronological age is not universal pseudotime truth. Missing intermediates, convergent states, cyclic programs and unsupported root choices require separate targets or explicit non-identifiability. Velocity requires the necessary measured layers and kinetic support. Correlation, supplied regulon membership, motif observations and perturbation responses have different evidence scopes.

## Completion evidence

For each exact case, record generator implementation, target tool, answer contract, grader and export coverage. Pure configuration checks can establish shape, bounds, scope and default-denial contracts. They cannot establish realistic distributions, numerical correctness, calibrated false-discovery rates, or actual agent benefit. No numerical test, generator, public-data import, scientific analysis or model participant may run while the data hold remains active.

Completion of this implementation extension requires the code paths and target-specific integration to exist, independent source review, allowed verification and an honest coverage dashboard. Empirical calibration and live three-condition evaluation remain separate future work; no clinical or regulatory qualification follows from this extension.
