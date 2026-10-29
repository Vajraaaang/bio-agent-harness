"""Bounded JATS extraction and replayable numeric/context review companions.

Extraction proposes a representation of retained bytes. Neither an exact token
nor a caller-declared independent review authenticates a publication or proves
scientific support. No external resource is resolved and no model is called.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import re
import xml.etree.ElementTree as ET

from .rna_publication_evidence import _copy, _identifier, _keys, _list, _locator, _sha, _text


EXTRACTION_FORMAT = "devbench.rna-jats-extraction/2"
NUMBER_FORMAT = "devbench.rna-reference-number/2"
CONTEXT_REVIEW_FORMAT = "devbench.rna-context-review/2"
EXTRACTOR_VERSION = "jats-stdlib/1"
MAX_XML_BYTES = 2_000_000
MAX_XML_DEPTH = 64
MAX_XML_NODES = 25_000
MAX_XML_TEXT_BYTES = 500_000
MAX_EXTRACTION_BYTES = 1_000_000
_MATHML_NAMESPACE = "http://www.w3.org/1998/Math/MathML"
_STRUCTURAL_TAGS = {"article", "body", "front", "article-meta", "article-id", "article-title", "title-group",
    "sec", "p", "table-wrap", "table", "caption", "title", "label", "fn", "supplementary-material",
    "graphic", "tr", "td", "th", "thead", "tbody", "tfoot"}
_NUMBER_FIELDS = {"format", "raw_text", "number_type", "canonical_decimal", "rule", "reviewed",
                  "operator", "bound_decimal", "uncertainty"}
_DIMENSIONS = {"entity", "population", "contrast", "measurement", "uncertainty", "negation", "scope"}
_NUMERIC = r"[+-]?(?:[0-9]+)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]{1,3})?"


def _decimal(text):
    if type(text) is not str or len(text) > 128 or re.fullmatch(_NUMERIC, text) is None:
        raise ValueError("Use an unambiguous exact decimal lexeme")
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise ValueError("Invalid reference decimal") from error
    if not value.is_finite() or abs(value.adjusted()) > 308:
        raise ValueError("Reference decimal exceeds its finite magnitude bound")
    # Formatting rather than normalize() avoids Decimal context rounding.
    canonical = format(value, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    return "0" if value == 0 else canonical


def canonicalize_reference_number(raw_text, *, rule, reviewed):
    """Retain the lexeme; apply only an explicitly reviewed named transform."""
    _text(raw_text, 512)
    if type(reviewed) is not bool or type(rule) is not str or rule not in {"identity", "unicode_minus_to_ascii", "percentage_to_proportion"}:
        raise ValueError("Use a named reviewed numeric transformation rule")
    if rule != "identity" and reviewed is not True:
        raise ValueError("Numeric transformations require independent review")
    transformed = raw_text
    if rule == "unicode_minus_to_ascii":
        if not raw_text.startswith("−") or raw_text.count("−") != 1:
            raise ValueError("Unicode-minus rule requires one leading U+2212 minus")
        transformed = "-" + raw_text[1:]
    elif rule == "percentage_to_proportion":
        if not raw_text.endswith("%") or raw_text.count("%") != 1:
            raise ValueError("Percentage rule requires an explicit percentage lexeme")
        value = Decimal(_decimal(raw_text[:-1]))
        # scaleb can round with the ambient Decimal context; construct a tuple.
        parts = value.as_tuple()
        transformed = str(Decimal((parts.sign, parts.digits, parts.exponent - 2)))
    canonical = _decimal(transformed)
    return {"format": NUMBER_FORMAT, "raw_text": raw_text, "number_type": "exact",
            "canonical_decimal": canonical, "rule": rule, "reviewed": reviewed,
            "operator": None, "bound_decimal": None, "uncertainty": None}


def classify_reference_number(raw_text):
    """Keep inequalities typed; locales/ranges/uncertainty notation abstain."""
    _text(raw_text, 512)
    match = re.fullmatch(r"(<=|>=|<|>)(" + _NUMERIC + r")", raw_text)
    if match:
        try:
            bound = _decimal(match[2])
        except ValueError:
            pass
        else:
            return {"format": NUMBER_FORMAT, "raw_text": raw_text, "number_type": "censored",
                "canonical_decimal": None, "rule": "identity", "reviewed": False,
                "operator": match[1], "bound_decimal": bound, "uncertainty": None}
    try:
        return canonicalize_reference_number(raw_text, rule="identity", reviewed=False)
    except ValueError:
        return {"format": NUMBER_FORMAT, "raw_text": raw_text, "number_type": "ambiguous",
            "canonical_decimal": None, "rule": None, "reviewed": False,
            "operator": None, "bound_decimal": None, "uncertainty": None}


def validate_reference_number_record(record, *, body=None, locator=None):
    """Replay extraction and, when supplied, verify a standalone source token.

    A containing quote can bind the token without asserting which entity it
    describes; contextual entity/contrast review remains a separate gate.
    """
    record = _copy(record)
    _keys(record, _NUMBER_FIELDS)
    if record["format"] != NUMBER_FORMAT:
        raise ValueError("Unsupported numeric extraction format")
    if record["number_type"] == "exact":
        expected = canonicalize_reference_number(record["raw_text"], rule=record["rule"], reviewed=record["reviewed"])
    else:
        expected = classify_reference_number(record["raw_text"])
    if expected != record:
        raise ValueError("Numeric extraction differs from the replayed original lexeme")
    if (body is None) != (locator is None):
        raise ValueError("Supply both retained bytes and their exact locator")
    if body is not None:
        if type(body) is not bytes or len(body) > 10_000_000:
            raise ValueError("Use bounded retained source bytes")
        _locator(locator, body)
        token = re.escape(record["raw_text"])
        if re.search(r"(?<![\w.,+\-−%<>])" + token + r"(?![\w.,%])", locator["text"]) is None:
            raise ValueError("Original numeric lexeme is absent as a standalone source token")
    return record


def _local(tag):
    return tag.rsplit("}", 1)[-1]


class _BoundedTree(ET.TreeBuilder):
    def __init__(self):
        super().__init__()
        self.depth = self.nodes = self.text_bytes = 0
        self.namespaces, self.math_depth = [], 0

    def start(self, tag, attrs):
        self.depth += 1
        self.nodes += 1
        if self.depth > MAX_XML_DEPTH or self.nodes > MAX_XML_NODES:
            raise ValueError("XML depth or node limit exceeded")
        if (len(tag) > 256 or len(attrs) > 64 or
                sum(len(key.encode()) + len(value.encode()) for key, value in attrs.items()) > 16_000):
            raise ValueError("XML element/attribute bound exceeded")
        if tag.startswith("{http://www.w3.org/2001/XInclude}") or "{http://www.w3.org/XML/1998/namespace}base" in attrs:
            raise ValueError("XML external resources and XInclude are disabled")
        namespace = tag[1:tag.rfind("}")] if tag.startswith("{") else None
        if namespace is not None and (namespace != _MATHML_NAMESPACE or _local(tag) in _STRUCTURAL_TAGS):
            raise ValueError("Unsupported JATS structural namespace")
        if namespace is None and self.math_depth:
            raise ValueError("Mixed structural markup inside MathML has an unsupported namespace")
        self.namespaces.append(namespace)
        self.math_depth += namespace == _MATHML_NAMESPACE
        return super().start(tag, attrs)

    def end(self, tag):
        value = super().end(tag)
        self.math_depth -= self.namespaces.pop() == _MATHML_NAMESPACE
        self.depth -= 1
        return value

    def data(self, data):
        self.text_bytes += len(data.encode("utf-8"))
        if self.text_bytes > MAX_XML_TEXT_BYTES:
            raise ValueError("XML expanded text limit exceeded")
        return super().data(data)

    def doctype(self, name, pubid, system):
        raise ValueError("XML DTDs and entities are disabled")

    def pi(self, target, text=None):
        raise ValueError("XML processing instructions are disabled")


def _parse_xml(body):
    if type(body) is not bytes or not body or len(body) > MAX_XML_BYTES:
        raise ValueError("Use bounded raw UTF-8 JATS bytes")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Only UTF-8 XML is supported") from error
    if ("\x00" in text or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.I)
            or re.search(r"<\?(?!xml(?:\s|\?>))", text, re.I)):
        raise ValueError("XML external declarations/entities/resources are disabled")
    encoding = re.match(r"\s*<\?xml[^?]*encoding\s*=\s*['\"]([^'\"]+)['\"]", text, re.I)
    if encoding and encoding[1].lower() not in {"utf-8", "utf8"}:
        raise ValueError("Only UTF-8 XML declarations are supported")
    try:
        return ET.fromstring(body, parser=ET.XMLParser(target=_BoundedTree()))
    except ET.ParseError as error:
        raise ValueError("Malformed or truncated JATS XML") from error


def extract_jats(body, *, source_sha256, extractor_version=EXTRACTOR_VERSION):
    """Extract sections and XHTML-style tables, retaining unexpanded locators.

    XPath locators identify the parsed representation, not UTF-8 byte spans.
    External graphic/supplement links are observations with no retrieval grant.
    Merged cells retain spans and require reviewed alignment; no figure, OCR,
    OASIS/CALS or embedded-resource execution is supported.
    """
    if type(body) is not bytes or hashlib.sha256(body).hexdigest() != _sha(source_sha256):
        raise ValueError("JATS source hash mismatch")
    if extractor_version != EXTRACTOR_VERSION:
        raise ValueError("Unsupported JATS extractor version")
    root = _parse_xml(body)
    if root.tag != "article":
        raise ValueError("Only the unnamespaced JATS article model is supported")
    paths, ids = {}, set()
    path_bytes, output_bytes = 0, 4096

    def string_bytes(value):
        # Count canonical UTF-8 JSON string bytes without allocating an escaped
        # string or joining source fragments. Quotes/control escapes are included.
        if value is None:
            return 4
        size = 2
        for char in value:
            code = ord(char)
            size += (2 if char in '\\"\b\f\n\r\t' else 6 if code < 32 else
                     1 if code < 128 else 2 if code < 2048 else 3 if code < 65536 else 4)
        return size

    def reserve_output(size):
        nonlocal output_bytes
        if size > MAX_EXTRACTION_BYTES - output_bytes:
            raise ValueError("JATS extraction output allocation budget exceeded")
        output_bytes += size

    reserve_output(string_bytes(root.get("article-type")))

    def index(element, path, length):
        nonlocal path_bytes
        paths[element] = path
        identifier = element.get("id")
        if identifier is not None:
            _text(identifier, 256)
            if identifier in ids:
                raise ValueError("Duplicate JATS element ID")
            ids.add(identifier)
        counts = {}
        for child in element:
            name = _local(child.tag)
            counts[child.tag] = counts.get(child.tag, 0) + 1
            suffix = "/" + name + "[" + str(counts[child.tag]) + "]"
            child_length = length + len(suffix.encode("utf-8"))
            if child_length > MAX_EXTRACTION_BYTES - path_bytes:
                raise ValueError("JATS XPath allocation budget exceeded")
            path_bytes += child_length
            index(child, path + suffix, child_length)
    root_path = "/article[1]"
    path_bytes = len(root_path)
    index(root, root_path, path_bytes)

    def item(element, *, extra_strings=(), copies=1):
        # Fixed headroom covers record keys, scalar fields and list punctuation;
        # dynamic strings and duplicated header records are counted explicitly.
        reserve_output(copies * (256 + string_bytes(element.get("id")) + string_bytes(paths[element]) +
                                sum(string_bytes(value) for value in extra_strings)))
        for fragment in element.itertext():
            reserve_output(copies * (string_bytes(fragment) - 2))
        return {"element_id": element.get("id"), "xpath": paths[element],
                "text": "".join(element.itertext()).strip()}

    def selected(element, tag):
        return [item(node) for node in element if _local(node.tag) == tag]

    article_meta = root.find("front/article-meta")
    article_body = root.find("body")
    content_tags = {"p", "list-item", "td", "th", "disp-formula", "disp-quote", "verse-line"}
    body_has_content = article_body is not None and bool((article_body.text or "").strip() or any(
        _local(node.tag) in content_tags and "".join(node.itertext()).strip() for node in article_body.iter()))
    identity_observations = {"article_ids": [], "article_titles": []}
    if article_meta is not None:
        identity_observations["article_ids"] = [{**item(node, extra_strings=(node.get("pub-id-type"),)), "pub_id_type": node.get("pub-id-type")}
                                                for node in article_meta if node.tag == "article-id"]
        identity_observations["article_titles"] = [item(node) for node in article_meta.findall("title-group/article-title")]
    sections, tables, supplements, warnings = [], [], [], []
    for node in root.iter():
        name = _local(node.tag)
        if name == "sec":
            sections.append({**item(node), "titles": selected(node, "title")})
        elif name == "supplementary-material":
            href = node.get("{http://www.w3.org/1999/xlink}href", node.get("href"))
            supplements.append({**item(node, extra_strings=(href,)), "labels": selected(node, "label"),
                "href": href,
                "retrieval_authorized": False})
        elif name == "table-wrap":
            reserve_output(512 + string_bytes(node.get("id")) + string_bytes(paths[node]))
            explicit = [child for child in node if child.tag == "table"]
            graphics = [item(child) for child in node.iter() if _local(child.tag) == "graphic"]
            table = {"element_id": node.get("id"), "xpath": paths[node], "labels": selected(node, "label"),
                "caption": selected(node, "caption"),
                "footnotes": [item(child) for child in node.iter() if _local(child.tag) == "fn"],
                "graphics": graphics, "cells": [], "headers": [], "requires_review": True,
                "status": "not_assessable_image_only" if graphics else "not_assessable_unsupported_table_model"}
            if len(explicit) > 1:
                raise ValueError("Ambiguous multiple JATS table representations")
            if explicit:
                model = explicit[0]
                if any(child is not model and _local(child.tag) in {"table", "tgroup"} for child in model.iter()):
                    raise ValueError("Nested or CALS tables require a separate capability")
                occupied, merged = set(), False
                for row_number, row in enumerate((child for child in model.iter() if child.tag == "tr"), 1):
                    column = 1
                    for cell in row:
                        if cell.tag not in {"td", "th"}:
                            raise ValueError("Unsupported JATS table row content")
                        while (row_number, column) in occupied:
                            column += 1
                        spans = []
                        for field in ("rowspan", "colspan"):
                            raw = cell.get(field, "1")
                            if re.fullmatch(r"[1-9][0-9]?", raw) is None or int(raw) > 64:
                                raise ValueError("JATS table span limit exceeded")
                            spans.append(int(raw))
                        rowspan, colspan = spans
                        merged = merged or rowspan > 1 or colspan > 1
                        slots = {(r, c) for r in range(row_number, row_number + rowspan)
                                 for c in range(column, column + colspan)}
                        if occupied & slots or len(occupied) + len(slots) > MAX_XML_NODES:
                            raise ValueError("Overlapping or oversized JATS table geometry")
                        occupied.update(slots)
                        header_ids = cell.get("headers", "").split()
                        if len(header_ids) > 32 or any(value not in ids for value in header_ids):
                            raise ValueError("Unknown or oversized JATS header association")
                        value = {**item(cell, extra_strings=header_ids, copies=2 if cell.tag == "th" else 1), "row": row_number, "column": column, "rowspan": rowspan,
                            "colspan": colspan, "kind": "header" if cell.tag == "th" else "data", "header_ids": header_ids}
                        table["cells"].append(value)
                        if cell.tag == "th":
                            table["headers"].append(value)
                        column += colspan
                table["status"] = "structured_requires_review" if table["cells"] else "not_assessable_empty_table"
                if merged:
                    reserve_output(256 + string_bytes(paths[node]))
                    warnings.append({"xpath": paths[node], "reason": "merged_cells_require_reviewed_alignment"})
            tables.append(table)
    return _copy({"format": EXTRACTION_FORMAT, "source_sha256": source_sha256,
        "extractor_version": extractor_version, "article_type": root.get("article-type"),
        "identity_observations": identity_observations,
        "coverage": {"article_body_present": article_body is not None,
            "article_body_nonempty": body_has_content,
            "full_text_completeness": "not_authenticated"},
        "sections": sections, "tables": tables, "supplements": supplements, "warnings": warnings,
        "review_status": "proposal_requires_independent_review",
        "lineage": {"parent_sha256": source_sha256, "transformation": "bounded_jats_structure_and_text"},
        "scope": "structured_source_representation_not_numeric_or_semantic_scientific_validation"})


def extract_reference_asset(body, *, content_type, source_sha256, extractor_version=EXTRACTOR_VERSION):
    """MIME alone does not enable PDF/OCR/figures/archive extraction."""
    if type(body) is not bytes or len(body) > MAX_XML_BYTES:
        raise ValueError("Unsupported or oversized reference extraction capability")
    opening = body.removeprefix(b"\xef\xbb\xbf").lstrip(b" \t\r\n")
    if (type(content_type) is not str or content_type not in {"application/xml", "text/xml", "application/jats+xml"}
            or not opening.startswith(b"<")
            or body[257:263] in {b"ustar\0", b"ustar "}
            or body.startswith((b"%PDF", b"PK", b"\x1f\x8b", b"\x89PNG", b"GIF8", b"\xff\xd8"))):
        raise ValueError("PDF/OCR/figure/archive extraction capability is disabled")
    return extract_jats(body, source_sha256=source_sha256, extractor_version=extractor_version)


def validate_context_review(record, *, excerpt, body, participant_id):
    """Verify custody and seven review dimensions; judgments stay declarations."""
    record = _copy(record)
    _keys(record, {"format", "review_id", "excerpt_id", "source_sha256", "claim", "proposal_origin",
                   "proposal_author_id", "reviewed_by", "checks", "conclusion", "limitations"})
    if record["format"] != CONTEXT_REVIEW_FORMAT:
        raise ValueError("Unsupported contextual support review format")
    _identifier(record["review_id"])
    if record["excerpt_id"] != excerpt["excerpt_id"]:
        raise ValueError("Context review is bound to a different excerpt")
    if type(body) is not bytes or len(body) > 10_000_000 or hashlib.sha256(body).hexdigest() != _sha(record["source_sha256"]):
        raise ValueError("Context review source hash mismatch")
    _locator(excerpt["locator"], body)
    _text(record["claim"], 16_000)
    _text(record["limitations"])
    reviewer = _identifier(record["reviewed_by"])
    author = _identifier(record["proposal_author_id"])
    if reviewer in {author, _identifier(participant_id)}:
        raise ValueError("Context review requires an independent declared reviewer")
    if record["proposal_origin"] not in {"model_assisted", "human", "deterministic"}:
        raise ValueError("Declare the contextual proposal origin")
    _keys(record["checks"], _DIMENSIONS)
    states = []
    for check in record["checks"].values():
        _keys(check, {"state", "evidence_locators", "explanation"})
        if check["state"] not in {"supported", "contradicted", "unknown"}:
            raise ValueError("Declare each contextual dimension separately")
        states.append(check["state"])
        _text(check["explanation"])
        for location in _list(check["evidence_locators"], 8, nonempty=check["state"] != "unknown"):
            _locator(location, body)
    expected = "contradicted" if "contradicted" in states else "not_assessable" if "unknown" in states else "supported"
    if record["conclusion"] != expected:
        raise ValueError("Context conclusion cannot override contradicted or unknown dimensions")
    return record
