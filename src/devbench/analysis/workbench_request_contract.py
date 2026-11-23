"""Versioned local routing hints. Classification never grants execution."""

from __future__ import annotations

import re

FORMAT = "devbench.workbench-request/1"

CELL_TYPE_COMPARISON_QUESTION = (
    "Compare stored cell-type proportions across the attached datasets using "
    "the celltypes column"
)
COUNT_AUDIT_PREFIX = "Audit the stored counts layer and "
_CELL_TYPE_COMPARISON = re.compile(
    "(" + re.escape(COUNT_AUDIT_PREFIX.casefold()) + ")?"
    + re.escape(CELL_TYPE_COMPARISON_QUESTION.casefold())
    + r"(?:\. attachment order corresponds to days ([0-9]{1,4}(?:, [0-9]{1,4}){1,7}))?\.?$"
)
MOTOR_PROGRAM_TOPIC = "mouse_hindbrain_motor_candidates"


def _motor_program_request(text):
    return (
        re.search(r"\bhindbrain\b", text)
        and re.search(r"\bmotor neurons?\b", text)
        and re.search(r"\b(?:label|identify|classify|subtypes?|different)\b", text)
    )

_NONZERO = r"(?:expressed|detected|non[ \u2010-\u2015-]?zero)"
_FULL_SOURCE = r"(?: (?:in|on|for) (?:the|this|my) (?:dataset|data|matrix|anndata))?"
_BATCH_EFFECT = r"batch effects?"
_PRINCIPAL_COMPONENTS = r"(?:pca|principal component analysis)"
_PCA_COMMAND = (
    r"(?:(?:show(?: me)?|plot|run|perform|compute|generate|produce|visuali[sz]e) )?"
    r"(?:a |the )?"
)
_CLAUSE_BREAK = re.compile(
    r"\b(?:and\s+then|and|then|also|while|along with|as well as|followed by)\b"
    r"|;|[.!?](?=\s|$)"
)
_COURTESY = re.compile(r"^(?:(?:can|could|would) you |please )+|,? please$")
_METADATA_CONTINUATION = re.compile(
    r"(?:the )?(?:genes|cells|samples|features|observations|rows|columns|"
    r"layers|batches|groups|biological units)"
    r"(?: (?:are )?(?:stored|present|available))?"
    r"(?: (?:are )?in (?:the |this )?(?:dataset|data|matrix|anndata))?"
)
_MATRIX_SUMMARY = re.compile(
    r"\b(?:how many|count(?: the number of)?|(?:number|count) of) "
    rf"(?:distinct |unique )?(?:{_NONZERO} genes\b|genes (?:are )?{_NONZERO}\b)"
)
_PIN_LINE = re.compile(
    r"• @(?:aim|step|selection) [a-z0-9][a-z0-9_.:-]{0,199}"
    r"(?: \(generation [1-9][0-9]{0,15}\))?"
    r"(?: \(version [1-9][0-9]{0,15}\))?",
    flags=re.IGNORECASE | re.ASCII,
)
_PROFILE_MATRIX_COUNT = re.compile(
    r"(?:(?:(?:(?:report|show)(?: me)? )?how many|count(?: the number of)?|"
    r"(?:report|show|give me) (?:the )?(?:count|number) of) "
    rf"(?:the )?(?:{_NONZERO} genes|genes (?:are )?{_NONZERO})|"
    rf"(?:report|show|give me) (?:the )?{_NONZERO} gene counts?) "
    r"(?:per|for (?:each|every)|in (?:each|every)) "
    rf"(?:samples?|profiles?|observations?|cells?){_FULL_SOURCE}"
)
_DESCRIPTIVE_BATCH = re.compile(
    r"(?:(?:is there|are there|check (?:whether|if) there (?:is|are))"
    r"(?: (?:any )?(?:evidence|signs) of)? |"
    r"(?:do (?:we|you) see|what evidence is there of) |"
    r"(?:check(?: for)?|look for|audit|describe|assess|examine|show|summarize) )?"
    rf"(?:a |the |any |potential )?{_BATCH_EFFECT}{_FULL_SOURCE}"
)
_DESCRIPTIVE_PCA = re.compile(
    rf"{_PCA_COMMAND}(?:batch[ -]colou?red {_PRINCIPAL_COMPONENTS}(?: plot)?|"
    rf"{_PRINCIPAL_COMPONENTS}(?: plot)? "
    r"(?:(?:(?:of samples )?(?:colou?red|grouped|stratified|label(?:l)?ed) )?by batch|"
    r"with (?:samples |points )?(?:colou?red|grouped|label(?:l)?ed) by batch|"
    r"to (?:check for|look for|assess|visuali[sz]e) (?:a |potential )?"
    rf"{_BATCH_EFFECT}))"
    rf"{_FULL_SOURCE}"
)
_PLAIN_PCA = re.compile(
    rf"{_PCA_COMMAND}{_PRINCIPAL_COMPONENTS}(?: plot)?{_FULL_SOURCE}"
)
_INFERENTIAL = re.compile(
    r"best method|method selection|compare methods|which.*method|choose.*method|"
    r"pseudotime|trajectory|lineage|regulon|regulatory network|motif|"
    r"differential|fold.change|contrast|expression changes|causal|causality|causation|"
    r"cause.*(?:effect|change)|causes|caused|"
    r"\bestimate\b.+\b(?:minus|versus|vs)\b|"
    r"\bgenes?\b.*\b(?:change[ds]?|differ|upregulated|downregulated)\b|"
    r"\b(?:pathways?|enrichment|pseudobulk|sccoda|deseq2|edger|comparisons|"
    r"composition analysis|supported methods)\b"
)


def split_pinned_context(question):
    """Separate the composer’s bounded navigation metadata from the question."""
    prompt, marker, suffix = question.rpartition("\n\nPinned draft context:\n")
    pins = suffix.split("\n")
    if marker and prompt.strip() and 1 <= len(pins) <= 8 and all(
        _PIN_LINE.fullmatch(line) for line in pins
    ):
        return prompt.strip(), pins
    return question, []


def _clauses(text):
    return [
        normalized
        for clause in _CLAUSE_BREAK.split(text)
        if (normalized := " ".join(clause.strip(" ,.!?").split()))
    ]


def _matrix_intent(clause):
    clause = _COURTESY.sub("", clause)
    for intent, pattern in (
        ("nonzero_by_profile", _PROFILE_MATRIX_COUNT),
        ("batch_association", _DESCRIPTIVE_BATCH),
        ("batch_pca", _DESCRIPTIVE_PCA),
        ("pca", _PLAIN_PCA),
    ):
        if pattern.fullmatch(clause):
            return intent
    return None


def matrix_audit_intents(question):
    """Require every clause to match a bounded diagnostic on the entire source."""
    prompt, _ = split_pinned_context(question)
    intents = tuple(_matrix_intent(clause) for clause in _clauses(prompt.casefold()))
    return tuple(dict.fromkeys(intents)) if all(intents) else ()


def supports_matrix_profile_audit(question):
    return bool(matrix_audit_intents(question))


def _descriptive_topics(text, *, matrix_summary, stored):
    topics = []
    if not matrix_summary and re.search(
        r"how many (?:genes|cells|samples|features|observations|rows|columns|"
        r"libraries)|"
        r"(?:number|count) of (?:genes|cells|samples|features|observations)|"
        r"\b(dimensions?|shape|size)\b", text
    ):
        topics.append("dimensions")
    if re.search(
        r"how many batches|(?:number|count) of batches|"
        r"(?:list|show|which|what).*\bbatches\b|\bper\s+batch\b", text
    ):
        topics.append("batches")
    if re.search(
        r"(?:what|which|list|show).*\b(columns?|annotations?|metadata)\b", text
    ):
        topics.append("columns")
    if re.search(r"(?:what|which|list|show).*\blayers?\b", text):
        topics.append("layers")
    if re.search(
        r"(?:details?|information|breakdown|distribution|tell me|describe|summarize).*"
        r"\b(subpopulations?|cell[ _]?types?|clusters?|classes?|subtypes?|"
        r"annotations?)\b|"
        r"(?:what|which|how many|list|show).*"
        r"\b(subpopulations?|cell[ _]?types?|clusters?|classes?|subtypes?)\b", text
    ):
        topics.append("annotations")
    if stored and re.search(r"\b(markers?|clusters?|results?)\b", text):
        topics.append("stored_results")
    if re.fullmatch(r"(?:describe|summarize|inspect|overview)(?: (?:the|this|my))?"
                    r"(?: (?:data|dataset))?[.!?]?", text):
        topics.extend(("dimensions", "columns", "layers"))
    return list(dict.fromkeys(topics))


def _is_inferential(text, *, stored):
    # Retrieval of stored markers does not request a new fit.
    markers = re.search(r"\bmarkers?\b", text)
    return bool(
        _INFERENTIAL.search(text)
        or re.search(r"compar|between|versus|\bvs\b|difference|changes", text)
        and re.search(
            r"gene expression|expression|composition|populations|abundance|cell.types?",
            text,
        )
        or markers
        and (
            not stored
            or re.search(
                r"\b(find|compute|calculate|identify|test|discover)\b.*markers?", text
            )
        )
    )


def _is_exploratory(text, topics):
    computation_text = re.sub(
        r"how many batches|(?:number|count) of batches|"
        r"(?:list|show|which|what)\s+(?:are\s+)?(?:the\s+)?batches", "", text
    )
    return bool(
        re.search(
            r"\b(?:emptydrops|droplet barcode ranks?|doublets?|batch mixing|"
            r"batch diagnostics?|design rank|latent batch structure)\b|"
            r"\b(?:cell types?|cells?|clusters?)\s+express(?:ing|es)?\b",
            text,
        )
        or re.search(
            r"\b(?:run|perform|compute|calculate|find|create|recompute|generate|apply)\b[^.!?;]*"
            r"\b(?:qc|quality|clustering|clusters?|annotations?|umap|integration|"
            r"batch correction)\b",
            text,
        )
        or re.search(
            r"\bnormaliz(?:e|ation|ing)\b|\bpca\b|\bt[ -]?sne\b|"
            r"\bembedding\b|\btransform\b.*expression", text
        )
        or re.search(
            r"\bqc\b|quality|mitochond|integrat|batch effect|correct.*batch|cluster|"
            r"annotate|umap",
            computation_text,
        )
        and not ("annotations" in topics or "stored_results" in topics)
    )


def _remaining_action(
    text, clauses, topics, *, stored, matrix_requested, unresolved_scope,
):
    if _is_inferential(text, stored=stored):
        return "inferential_analysis"
    if matrix_requested or _is_exploratory(text, topics):
        return "exploratory_computation"
    if not topics or unresolved_scope:
        return "clarify_intent"
    if len(clauses) > 1 and any(
        not _METADATA_CONTINUATION.fullmatch(clause)
        and classify_request(clause)["remaining_action"] == "clarify_intent"
        for clause in clauses
    ):
        return "clarify_intent"
    return None


def classify_request(question):
    prompt, _ = split_pinned_context(question)
    text = prompt.casefold().strip()
    if _motor_program_request(text):
        return {
            "format": FORMAT,
            "category": "descriptive_retrieval",
            "descriptive_topics": [MOTOR_PROGRAM_TOPIC],
            "remaining_action": None,
            "routing_hint": "local_question_classification",
            "matrix_computation_required": False,
            "scope_requires_clarification": False,
            "authority": "Local motor-neuron candidate scoring with anatomical review held.",
            "motor_program_comparison": True,
            "cell_type_comparison": {"declared_days": []},
        }
    comparison = _CELL_TYPE_COMPARISON.fullmatch(text)
    if comparison:
        days = [int(day) for day in comparison[2].split(", ")] if comparison[2] else []
        topics = ["stored_celltypes", "stored_age", "stored_sample_type"]
        if comparison[1]:
            topics.append("stored_count_layer_audit")
        return {
            "format": FORMAT,
            "category": "descriptive_retrieval",
            "descriptive_topics": topics,
            "remaining_action": None,
            "routing_hint": "local_question_classification",
            "matrix_computation_required": False,
            "scope_requires_clarification": False,
            "authority": "Local descriptive comparison only.",
            "cell_type_comparison": {"declared_days": days},
        }
    clauses = _clauses(text)
    matrix_summary = any(_MATRIX_SUMMARY.search(clause) for clause in clauses)
    matrix_intents = [_matrix_intent(clause) for clause in clauses]
    matrix_requested = matrix_summary or any(matrix_intents)
    stored = bool(re.search(r"\b(stored|saved|existing|previous|retained)\b", text))
    topics = [] if clauses and all(matrix_intents) else _descriptive_topics(
        text, matrix_summary=matrix_summary, stored=stored
    )
    unresolved_scope = bool(topics and re.search(
        r"\bunique\b.*\bgenes?\b|\bgenes?\b.*\bunique\b|\bselected\b|"
        r"\b(?:in|from|within|for)\s+(?:the\s+)?batch\b", text
    ))
    remaining = _remaining_action(
        text, clauses, topics, stored=stored, matrix_requested=matrix_requested,
        unresolved_scope=unresolved_scope,
    )
    category = (
        "ambiguous_or_mixed" if (topics and remaining) or remaining == "clarify_intent"
        else remaining or "descriptive_retrieval"
    )
    return {
        "format": FORMAT,
        "category": category,
        "descriptive_topics": topics,
        "remaining_action": remaining,
        "routing_hint": "local_question_classification",
        "matrix_computation_required": matrix_requested,
        "scope_requires_clarification": unresolved_scope,
        "authority": (
            "Routing hint only; no biological declaration, model permission or "
            "execution grant."
        ),
    }
