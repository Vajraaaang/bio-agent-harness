# RNA publication grounding and local trial preparation

The implemented APIs verify publication identity declarations, retained source
bytes, verbatim excerpts, exact workflow bindings and curated scalar comparisons.
They do not establish publisher authenticity, semantic support, scientific
optimality, authenticated approval or participant isolation. No real publication
or dataset was selected for the implementation request.

## What data do I have?

Publication inputs are explicit DOI/PMID/PMCID identifiers, title and version,
approved public text URLs, read-only absolute local source paths, source hashes,
and byte-offset locators. Sources declare whether they contain full text,
abstracts, metadata or extracted text. Metadata is not full-text evidence.

Trial preparation reuses registered bulk gene-by-sample JSON, capped at
100,000,000 bytes, 100,000 genes and 128 samples. Biological-unit, group,
measurement, chronological and batch declarations accompany observed input
checks. Unique labels do not establish biological independence. Inferential
readiness rejects missing/false independence declarations, repeated units,
invalid contrasts and deficient designs, including declared batch confounding
when a caller omits batch from its proposed covariates. These necessary checks
are not independent biological validation. General AnnData/scRNA support is
not added by these APIs.

## What question am I investigating?

The actual question is retained with its source file, byte count, hash and
excerpt. The task objective must match that question. Parameter requirement
records must cite the retained question source; a literature default or fixture
choice is not converted into a user requirement. Exact reproduction,
independent method selection and external validation are distinct objectives.

The implementation request supplies no scientific question, publication,
accession, data access decision, model or scientific trial allocation. Those
inputs must be supplied and independently reviewed before a real trial.

## Which methods are appropriate, and why?

Use `rna_publication_evidence.crossref_doi_metadata_url` to construct one exact
identifier lookup. `retrieve_crossref_doi_metadata` requires explicit network
permission and approval of that exact URL; both ordinary JSON and Crossref's
documented vendor JSON are supported. The existing safe transport applies
public-address checks, HTTPS, bounded bytes/time, exclusive output writes and
URL approval before every redirect. Retrieval is disabled by default. This is
exact DOI lookup, not title-based discovery or a deployed API qualification.
PMID/PMCID matching is local; NCBI retrieval and PDF/figure extraction are absent.

`resolve_publication` rejects missing, ambiguous, unrelated or conflicting
candidates. Metadata lookup exposes update notices and links as observations;
it does not infer an article version, full-text access or absence of retraction.
Provider links grant no retrieval authority. The primary interface references
are [Crossref REST metadata](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)
and [Crossref update/retraction metadata](https://www.crossref.org/documentation/retrieve-metadata/retraction-watch/).

`prepare_publication_evidence` seals source hashes, identity locators and exact
UTF-8 excerpt byte ranges. Reloading rechecks the read-only original files.
Participant projection includes permitted question/data/method excerpts and
excludes original file paths and evaluator outcome excerpts. Public or prior
result exposure remains explicit; projection alone does not establish blinding
or OS denial of source access. Curator identity is declared, not authenticated.

`GroundedSelector` adds that projection to the existing planner context. A
publication-derived candidate must cite the matched identifier and a verified
verbatim method excerpt. Quote integrity does not verify its interpretation.
Bounded rejected structured responses are retained through the existing
immutable method artifact service; unavailable provider transport bytes and
oversized responses are not presented as retained evidence.

Question-to-workflow orchestration reuses `ScientificMethodPlanner`: scientific
alternatives, capability resolution, trial design and governed declarations.
No new arbitrary-command or provider execution framework is introduced.
Generation and execution have separate bindings and approvals. Local records
never authenticate their own permissions. Live workflow generation is blocked
because exact campaign binding is not implemented; native admission remains disabled. The
in-process execution API requires a trusted injected `TrialAuthority`; no
production authority or mediated isolation qualification is shipped here.

## What happened?

The CLI supports preparation and revalidation only. Its installed parser can
be inspected without running a model or scientific tool:

```sh
/Users/vajraang/Documents/ChatGPT/bio-agent-harness/.venv-rna-hosted-qualification-hosted-4/bin/python -I -B -m scripts.run_rna_scientific_trial --help
```

Preparation requires an existing governed method workspace and complete closed
records; it returns the actual absolute validation invocation and an externally
retained seal. Source, data, workflow, criteria, target, expiry or allocation
drift prevents dispatch. Accepted wall-time allocation conservatively includes
every candidate in each proposed experiment.

`prepare_publication_comparison` freezes question/data/workflow/exposure hashes,
the independent curator declaration, matched gene/statistic/units/contrast/cohort/
preprocessing/family, required endpoints, tolerances, uncertainty and abstention
criteria. Outcome numbers need complete exact ASCII decimal/exponent tokens in
evaluator-held excerpts. Unicode signs, locale/grouping formats and percentage
confidence-level normalization currently fail closed; they are not guessed.

`compare_publication_outputs` checks the separately retained output seal and
reports agreement, differences, incomparability, missingness, failures and
abstention against the frozen denominator. A failed output cannot carry a
successful-looking estimate. Scalar comparisons report unassessed plots,
interventions and reference exposure. They do not render/review publication
figures, validate a complete fitted p/q family or independently prove seal
chronology. The implementation report records actual software test outcomes;
no real scientific trial outputs or plot assessment exist for this request.

## What can I conclude, and what remains uncertain?

These controls reject invented quote bytes and mismatched local bindings. An
independent evaluator must still verify semantic support, cohort/endpoint
compatibility, extraction, actual outputs, uncertainty and objective-appropriate
plots. Hashes and declared roles are not signatures or reviewer qualifications.
Publication agreement is not universal truth, causal evidence or optimality.

Hosted target/schema/credential-channel qualification, authenticated operator
approvals, preventive aggregate provider controls and actual participant
isolation remain separate gates. T14/Harmony dependencies must be disclosed and
block dependent trial paths until separately resolved. Clinical release remains
held pending qualified independent validation and operational approval controls.
