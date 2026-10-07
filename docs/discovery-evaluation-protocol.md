# Discovery evaluation protocol — Tier 1 (synthetic planted structure)

Date: 2026-10-03. Implementation specification; **no agent campaign, held-out generation, public-data comparison, expert comparison or clinical validation has run under this protocol**. Software verification does not measure agent discovery performance. Historical prompts, sealed receipts and previous failures remain preserved.

## 1. What data do I have?

Default fixtures contain 8,000 opaque genes and 24 raw-count columns. A–E are gamma–Poisson negative-binomial fixtures with independent synthetic specimens explicitly declared as an assistant fixture assumption. F is Poisson-lognormal: eight synthetic donors, four per condition, each with one library sequenced three times. File integrity and synthetic declarations establish neither scientific validity nor authenticated biological independence.

Counts have gene/sample axes; metadata names condition, sex, batch and biological unit through explicit semantic roles. Public artifacts are counts.zarr, metadata.csv, neutral counts.json (`gene_ids`, `sample_ids`, `matrix`), neutral metadata.json (`samples`), answer_schema.json and input_manifest.json. Staging derives expected flat bytes from the generated matrix/metadata and copied schema. A narrow producer creates Zarr v2 headers and chunks with fixed Blosc configuration from the same original typed arrays. The already-frozen immutable buffers are published directly, with no second compression or global codec-thread change; persisted headers, exact file sets, axes and count chunks must match those bounded producer bytes before the fixed decoder runs. Generic metadata-selected codecs are not used in staging. Prepared/published trees remain bound to those expectations, and the returned truth seal is the original producer hash, checked again after lock cleanup. It rejects concurrent content/tree changes and symlink ancestry; later snapshots cannot redefine expected scientific inputs. Rollback removes only unchanged owned entries; uncaptured or foreign temporary bytes can remain in private staging residue after an operational failure. Such failure publishes no completed manifest and authorizes no dispatch. Public manifests bind bytes, axes, declaration, schema and public generator source; family, seed, hidden truth and evaluator paths/hashes stay in the evaluator return only. Both arms consume identical registered visible inputs.

| Presentation | Generating target | Supported distinction |
|---|---|---|
| A | A 40-gene module elevated in four treated specimens | Specific recovery requires both genes and subgroup specimens. Its coarse condition association is valid evidence with no subgroup credit. |
| B | Sixty genes with female log2 ratio +1.5 and male −1.5 | Crossover recovery requires interaction evidence and both directions. Equal-sex arithmetic mean count ratio is about 1.591; mean sex log ratios are zero. |
| C | Fifty condition genes and 200 batch genes in a partially aligned design | Joint condition/batch NB analysis identifies the condition target. Batch associations are valid descriptive findings. |
| D | Two fully confounded labels describe one split | Descriptive split association is valid; a separately identifiable adjusted or causal condition effect is unavailable. |
| E | No planted condition effects | False-positive behavior is stochastic; one BH hit is possible and must remain reported. |
| F-known | Eight donors × three sequencing runs of each same library | Supplied donor/library/run mapping supports summing raw counts per library, then eight-unit analysis. |
| F-unknown | The same counts without verified biological mapping | Missing provenance permits exploratory dependence warnings, not authentication of eight donors from correlation. Withhold biological inference. |

For A, `r=2^1.5` and subgroup fraction `f=1/3` give expected generating mean condition ratio `1+f(r−1)≈1.6095`, log2≈0.6866. For B, `(r+r^-1)/2≈1.5910`, log2≈0.6699, while female-reference male-minus-female interaction is −3 log2 units. These are generating-scale expectations, not exact required observed estimates. Counts and normalization affect estimates.

Known F means **same library resequenced**. Repeated biological specimens require a different analysis. Donor is nested in condition, so `~ donor + condition` is deficient. After validated same-library count aggregation, use `~ condition` on eight biological units. Unknown F supplies no donor or library identity; its unknown status must survive planning and downstream execution.

Future allocation is seven presentations (A, B, C, D, E, F-known, F-unknown) × three held-out seeds = **21 presentations per arm before reruns**. Paired F views may share counts. Generation and agent budget remain pending; this implementation request authorizes neither campaign nor held-outs.

## 2. What question am I investigating?

The objective is synthetic discovery of unexpected structure, executed tests of competing explanations and calibrated restraint. All presentations use a neutral prompt: “Characterize this dataset. What signals, design issues or unexpected structure does it contain? Test competing explanations before concluding. Abstain where the data cannot support a claim.”

Keep claim validity, specific target recovery and model gene-testing evidence separate. Approved direction covers correcting A–F, provenance views, separate naive/design-aware comparators and development oracles. Numerical defaults (8,000 genes, 24 columns, log2FC 1.5, donor SD 0.5, three runs, BH 0.05, overlap/pass thresholds) are assistant choices. Record each value with source_kind/source_ref/status in the freeze manifest. Approval of a direction does not make a default user-prespecified.

Versioned publication schema is `benchmarks/reference_data/discovery_claims.schema.json`; stage its exact bytes as answer_schema.json and bind their SHA-256. The schema/scorer owners control the answer envelope; neither staging nor the protocol silently invents fields. Scientific refusal, null result and operational failure are distinct statuses.

## 3. Which methods are appropriate, and why?

The naive comparator deliberately uses condition only and treats run columns independently, retaining explicit misspecification labels. The design-aware comparator uses only visible metadata and declarations: condition-only for independent specimens; condition/sex interaction with female, male and interaction contrasts; joint condition/batch for C; non-identifiability refusal for D; same-library aggregation for known F; biological-inference refusal for unknown F. Neither arm receives family, seed, truth or evaluator configuration. Choosing model terms from supplied metadata is a frozen baseline rule.

Use a bounded full-panel PyDESeq2 0.5.x adapter with raw count offsets and empirical-Bayes dispersion fitting. Disclose design rank, residual degrees of freedom, conditioning, normalization, contrast geometry, fit warnings and failures. Freeze all gene×contrast tests before fitting; adjust across the declared family without shrinking the denominator after failures. Fitted genes have finite `0≤p≤q≤1`, or explicit unavailable status. No temporal contract expansion, OCI execution or native panel-cap bypass is implied.

B requires both strata and an interaction, not female-only differential expression. C requires actual joint NB output: batch-centered log-CPM t-tests are descriptive development checks. A truth-selected subgroup t-test is a **development oracle signal check**, not a discovery guarantee or proof that agent misses are the agent’s fault. Oracle truth access is isolated from model and comparator inputs. Null development checks do not prove general calibration.

Gene BH evidence does not establish claim-level FDR for adaptively selected modules, specimen clusters or exploratory claims; retain selection-conditional limits. LangGraph is deferred.

## 4. What happened? Evidence and frozen provisional metrics

Current software test results and unresolved failures are recorded in the implementation reports. No agent performance score or verified agent plot is available from these software tests. No plotting assessment is implied. Native smoke and agent-performance gates are **NOT_RUN**; the separately approved native campaign budget is pending. Local deterministic and baseline tests cannot satisfy either gate.

Freeze claim matching and provisional criteria before future held-out generation. Use maximum-cardinality then maximum-weight one-to-one compatible claim/target matching, with stable ties. Separate valid_specific, valid_coarse, unsupported, contradictory, redundant and unverified outcomes. High/moderate confidence asserted discoveries define primary recovery; low confidence explicit hypotheses are reported separately and earn no primary recall. Unsupported low confidence assertions remain visible in sensitivity results. Valid compatible extra claims can be redundant without being false. Gene/member precision diagnostics accompany Jaccard matching; overlap does not certify gene precision or claim-level FDR.

| Metric | Meaning |
|---|---|
| M1 Specific recovery | A requires gene Jaccard≥0.5 **and** specimen Jaccard≥0.7; B requires genes, correct factor/contrast and both directions with interaction evidence; C requires batch-adjusted condition estimand. |
| M2 Unsupported/contradictory discoveries | Primary high/moderate asserted discoveries; report unsupported low-confidence assertions in sensitivity results and explicit hypotheses separately. Coarse valid associations are not automatically false. |
| M3 Decoy attribution | Correctly attribute C batch effects without claiming unsupported adjusted condition effects. D split descriptions remain descriptive. |
| M4 Qualified refusal | D states non-identifiability without incompatible inferential/causal assertions at any confidence. Known F corrects aggregation or withholds; unknown F admits missing provenance. |
| M5 Executed alternatives | Receipts must establish actual alternative execution; merely naming an alternative earns no credit. Blinded AI review remains labelled AI review until qualified independent humans participate. |
| M6 Same-input repeatability | Compare complete canonicalized outputs, including invalid claims. Stable errors remain errors. Two empty outputs provide no positive discovery-repeatability evidence; that component is not applicable. |

M6 is not perturbation robustness; perturbation requires a separate predeclared protocol. Preserve gene-level false positives/FDP separately from claim-level validity. Three null seeds do not calibrate general error rates.

Provisional assistant criteria: M1≥0.67, and report paired performance relative to **each** comparator separately (naive proposed margin +0.33; design-aware proposed margin ≥0, specific-recall noninferiority); E zero unsupported asserted discoveries is an operational criterion, not a BH theorem; median unsupported claims≤1 elsewhere; M3≥0.9; M4≥5/6 as a fraction (equivalently at least 8/9 correct across the nine D/F-known/F-unknown presentations); M6 median≥0.7 on applicable discovery presentations. Missing outputs and operational rejection are measured failures, never exclusions. These software-frozen provisional numerical criteria remain scientifically unvalidated. Freeze final metric denominators and applicability before held-outs; thresholds cannot change after generation.

## 5. What can I conclude, and what remains uncertain?

Implementation acceptance requires execution-interface red→green tests, development full-panel real NB fixtures, hand-built adversarial scorer checks, input/truth boundary tests, schema/publication hash checks, staging rollback/race tests and the synthetic verification suite. Record actual failures and operator interventions. Successful tests establish bounded software behavior, not scientific method optimality or agent discovery skill.

Before any future campaign, freeze source/runtime/generator/schema/scorer/rubric/threshold identities, provenance presentation allocation and separately approved budget. Trusted evaluator generates held-outs afterward, stages public inputs separately from private truth, runs each arm and seals all outputs, blinds review, scores through trusted truth access and replays sealed evidence without new model calls. Qualified expert/public-data/clinical validation remain separate future evidence gates; clinical release stays held.
