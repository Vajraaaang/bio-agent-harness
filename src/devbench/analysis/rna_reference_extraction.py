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

from .rna_publication_evidence import _canonical_identifier, _copy, _identifier, _keys, _list, _locator, _sha, _text


EXTRACTION_FORMAT = "devbench.rna-jats-extraction/2"
NUMBER_FORMAT = "devbench.rna-reference-number/2"
CONTEXT_REVIEW_FORMAT = "devbench.rna-context-review/2"
EXTRACTOR_VERSION = "jats-stdlib/1"
MAX_XML_BYTES = 2_000_000
MAX_XML_DEPTH = 64
MAX_XML_NODES = 25_000
MAX_XML_TEXT_BYTES = 500_000
MAX_EXTRACTION_BYTES = 1_000_000
LITERATURE_EXTRACTION_FORMAT = "devbench.literature-extraction/1"
PUBMED_PASSAGE_EXTRACTOR_VERSION = "pubmed-passages-stdlib/1"
PMC_PASSAGE_EXTRACTOR_VERSION = "pmc-passages-stdlib/1"
MAX_LITERATURE_PASSAGES = 64
MAX_LITERATURE_PASSAGE_CHARS = 8000
MAX_LITERATURE_SECTION_CHARS = 128
MAX_LITERATURE_NOTICES = 64
MAX_LITERATURE_NOTICE_TEXT_BYTES = 4000
_MATHML_NAMESPACE = "http://www.w3.org/1998/Math/MathML"
_ALI_NAMESPACE = "http://www.niso.org/schemas/ali/1.0/"
_ALI_METADATA_TAGS = {"{" + _ALI_NAMESPACE + "}" + name for name in ("license_ref", "free_to_read")}
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
    def __init__(self, *, allow_ali_metadata=False):
        super().__init__()
        self.depth = self.nodes = self.text_bytes = 0
        self.namespaces, self.math_depth = [], 0
        self.allow_ali_metadata = allow_ali_metadata is True

    def start(self, tag, attrs):
        if self.namespaces and self.namespaces[-1] == _ALI_NAMESPACE:
            raise ValueError("ALI metadata must remain inert leaf elements")
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
        allowed_ali = self.allow_ali_metadata and tag in _ALI_METADATA_TAGS and not self.math_depth
        if (namespace is not None and (namespace != _MATHML_NAMESPACE or _local(tag) in _STRUCTURAL_TAGS)
                and not allowed_ali):
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


def _parse_xml(body, *, _allow_ali_metadata=False):
    if type(body) is not bytes or not body or len(body) > MAX_XML_BYTES:
        raise ValueError("Use bounded raw UTF-8 JATS bytes")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Only UTF-8 XML is supported") from error
    if ("\x00" in text or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.I)
            or re.search(r"<\?(?!xml(?:\s|\?>))", text, re.I)):
        raise ValueError("XML external declarations/entities/resources are disabled")
    encoding = re.match(r"\s*<\?xml[^?]*encoding\s*=\s*['\"]([^'\"]+)['\"]", text.removeprefix("\ufeff"), re.I)
    if encoding and encoding[1].lower() not in {"utf-8", "utf8"}:
        raise ValueError("Only UTF-8 XML declarations are supported")
    try:
        return ET.fromstring(body, parser=ET.XMLParser(target=_BoundedTree(allow_ali_metadata=_allow_ali_metadata)))
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


def _normalize_section_filter(section_filter):
    if section_filter is None:
        return None
    if type(section_filter) is not str or len(section_filter) > MAX_LITERATURE_SECTION_CHARS:
        raise ValueError("Use a bounded literature section filter")
    normalized = " ".join(section_filter.casefold().split())
    if not normalized or len(normalized) > MAX_LITERATURE_SECTION_CHARS:
        raise ValueError("Use a bounded nonempty literature section filter")
    return normalized


def _literature_limits(max_passages, max_passage_chars, passage_offset, section_filter):
    if (type(max_passages) is not int or not 1 <= max_passages <= MAX_LITERATURE_PASSAGES
            or type(max_passage_chars) is not int or not 1 <= max_passage_chars <= MAX_LITERATURE_PASSAGE_CHARS
            or type(passage_offset) is not int or not 0 <= passage_offset <= MAX_XML_NODES):
        raise ValueError("Literature passage count, offset or character bound exceeded")
    return _normalize_section_filter(section_filter)


_XML_SPACE = re.compile(rb"[ \t\r\n]*")
_XML_DECLARATION = re.compile(rb"<\?xml[ \t\r\n][^?]*\?>")
_PUBMED_DOCTYPE = re.compile(
    rb"""<!(?i:DOCTYPE)[ \t\r\n]+PubmedArticleSet[ \t\r\n]+PUBLIC[ \t\r\n]+"""
    rb"""(?P<q>['"])-//NLM//DTD PubMedArticle, [^<>"'\[\]\r\n]{1,120}//EN(?P=q)[ \t\r\n]+"""
    rb"""(?P<u>['"])https?://dtd\.nlm\.nih\.gov/ncbi/pubmed/out/pubmed_[0-9]{6}\.dtd(?P=u)[ \t\r\n]*>"""
)
_PMC_DOCTYPE = re.compile(
    rb"""<!(?i:DOCTYPE)[ \t\r\n]{1,64}article[ \t\r\n]{1,64}"""
    rb"""(?:PUBLIC[ \t\r\n]{1,64}(?P<p>['"])[A-Za-z0-9 \r\n\-()+,./:=?;!*#@$_%]{1,256}"""
    rb"""(?P=p)[ \t\r\n]{1,64}|SYSTEM[ \t\r\n]{1,64})"""
    rb"""(?P<s>['"])[^\x00-\x20\x7f-\xff<>"'\[\]]{1,2048}(?P=s)[ \t\r\n]{0,64}>"""
)


def _literature_doctype_position(body):
    """Find a declaration after the XML prolog's inert miscellaneous items."""
    start = 3 if body.startswith(b"\xef\xbb\xbf") else 0
    xml_declaration = _XML_DECLARATION.match(body, start)
    kept = start if xml_declaration is None else xml_declaration.end()
    position = kept
    while True:
        position = _XML_SPACE.match(body, position).end()
        if body.startswith(b"<!--", position):
            end = body.find(b"-->", position + 4)
            width = 3
        elif body.startswith(b"<?", position):
            end = body.find(b"?>", position + 2)
            width = 2
        else:
            return kept, position
        if end < 0:
            raise ValueError("Malformed XML preamble before literature DOCTYPE")
        position = end + width


def _parse_literature_xml(body, *, pubmed):
    """Use the strict parser after bounded, inert external declarations.

    The external declaration is never fetched or parsed. Internal subsets,
    entities and unsupported declarations remain forbidden. Legacy JATS still
    rejects every DTD. An ignored declaration cannot define named entities.
    """
    if type(body) is not bytes or not body or len(body) > MAX_XML_BYTES:
        raise ValueError("Use bounded raw UTF-8 literature XML bytes")
    try:
        body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Only UTF-8 XML is supported") from error
    parsed_body, external_doctype = body, "absent"
    if re.search(rb"<!\s*DOCTYPE\b", body, re.I):
        if re.search(rb"<!\s*ENTITY\b", body, re.I):
            raise ValueError("XML entities are disabled")
        retained, position = _literature_doctype_position(body)
        declaration = (_PUBMED_DOCTYPE if pubmed else _PMC_DOCTYPE).match(body, position)
        if declaration is None:
            raise ValueError("Unsupported XML external declaration or internal subset")
        # Validate XML prolog syntax without supplying the declaration. Its
        # comments and processing instructions remain inert and cannot enter
        # the article parser, whose post-DOCTYPE protections still apply.
        try:
            ET.fromstring(body[:position] + b"<preamble/>")
        except (ET.ParseError, LookupError) as error:
            raise ValueError("Malformed XML preamble before literature DOCTYPE") from error
        parsed_body = body[:retained] + body[declaration.end():]
        external_doctype = "ignored_ncbi_pubmed" if pubmed else "ignored_jats_external"
    root = _parse_xml(parsed_body, _allow_ali_metadata=not pubmed)
    # Ordinary JATS asset references are counted, never inspected or fetched.
    # Script-bearing elements/attributes and non-JATS loaders remain refused.
    reference_count = 0
    asset_tags = {"graphic", "inline-graphic", "media", "supplementary-material"}
    hyperlink_tags = {"ext-link", "license", "related-article", "uri", "self-uri", "xref"}
    for node in root.iter():
        if _local(node.tag).lower() in {"script", "object", "iframe", "embed", "applet", "style"}:
            raise ValueError("XML embedded executable resources are disabled")
        for key, value in node.attrib.items():
            name = _local(key).lower()
            if name.startswith("on"):
                raise ValueError("XML executable event attributes are disabled")
            if name not in {"href", "src", "data", "codebase"} or not value or value.startswith("#"):
                continue
            if "".join(value.split()).lower().startswith(("javascript:", "vbscript:", "data:")):
                raise ValueError("XML embedded executable resource references are disabled")
            if _local(node.tag) in hyperlink_tags:
                continue
            if not pubmed and _local(node.tag) in asset_tags and name in {"href", "src"}:
                reference_count += 1
                if reference_count > MAX_XML_NODES:
                    raise ValueError("XML external resource reference bound exceeded")
            else:
                raise ValueError("XML external resource references are disabled")
    return root, external_doctype, reference_count


def _literature_paths(root):
    paths, identifiers = {}, set()
    allocated = 0

    def visit(node, path):
        nonlocal allocated
        allocated += len(path.encode("utf-8"))
        if allocated > MAX_EXTRACTION_BYTES:
            raise ValueError("Literature XPath allocation budget exceeded")
        paths[node] = path
        identifier = node.get("id")
        if identifier is not None:
            _text(identifier, 256)
            if identifier in identifiers:
                raise ValueError("Duplicate XML element ID")
            identifiers.add(identifier)
        counts = {}
        for child in node:
            counts[child.tag] = counts.get(child.tag, 0) + 1
            visit(child, path + "/" + _local(child.tag) + "[" + str(counts[child.tag]) + "]")

    visit(root, "/" + _local(root.tag) + "[1]")
    return paths


def _literature_one(parent, path, *, required=False):
    nodes = parent.findall(path)
    if len(nodes) > 1 or (required and not nodes):
        raise ValueError("Missing or ambiguous literature XML structure: " + path)
    return nodes[0] if nodes else None


def _literature_text(nodes, *, maximum=16_000):
    """Metadata may trim outer whitespace; passage text never does."""
    value = None
    for node in nodes:
        size = sum(len(part.encode("utf-8")) for part in node.itertext())
        if size > maximum:
            raise ValueError("Literature metadata text bound exceeded")
        candidate = "".join(node.itertext()).strip() or None
        if value is not None and candidate is not None and candidate != value:
            raise ValueError("Conflicting literature metadata fields")
        value = candidate or value
    return value


def _literature_identifiers(entries, *, expected):
    identifiers = {"pmid": None, "pmcid": None, "doi": None}
    seen_pmid = False
    for field, node in entries:
        if list(node):
            raise ValueError("Article identifiers must be plain XML text")
        value = _literature_text([node], maximum=1000)
        if value is None:
            raise ValueError("Empty article identifier")
        if field == "pmcid" and re.fullmatch(r"[1-9][0-9]{0,11}", value):
            value = "PMC" + value
        value = _canonical_identifier(field, value)
        if field == "pmid" and seen_pmid:
            raise ValueError("Duplicate article PMID")
        seen_pmid = seen_pmid or field == "pmid"
        if identifiers[field] is not None and identifiers[field] != value:
            raise ValueError("Conflicting article identifier: " + field)
        identifiers[field] = value
    for field, value in expected.items():
        if value is not None and identifiers[field] != _canonical_identifier(field, value):
            raise ValueError("Literature expected identifier mismatch: " + field)
    return identifiers


def _literature_date(node):
    if node is None:
        return None
    if node.get("iso-8601-date"):
        return _text(node.get("iso-8601-date"), 256)
    medline = _literature_text(node.findall("MedlineDate"), maximum=256)
    if medline is not None:
        return medline
    parts = [_literature_text([child for child in node if child.tag.lower() == part], maximum=128)
             for part in ("year", "month", "day")]
    year, month, day = parts
    if year is None or re.fullmatch(r"[0-9]{4}", year) is None:
        return " ".join(part for part in parts if part is not None) or None
    if month is None:
        return year if day is None else " ".join(part for part in parts if part is not None)
    names = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december")
    months = {key: index for index, name in enumerate(names, 1) for key in (name, name[:3])}
    month_number = int(month) if month.isascii() and month.isdigit() else months.get(month.lower())
    if month_number is None or not 1 <= month_number <= 12:
        return " ".join(part for part in parts if part is not None)
    date = year + "-" + str(month_number).zfill(2)
    if day is None:
        return date
    if not day.isascii() or not day.isdigit() or not 1 <= int(day) <= 31:
        return " ".join(part for part in parts if part is not None)
    return date + "-" + str(int(day)).zfill(2)


def _literature_json_size(value):
    # Count escaped JSON without first allocating a potentially large copy.
    size = 2
    for char in value:
        code = ord(char)
        size += (2 if char in '\\"\b\f\n\r\t' else 6 if code < 32 else
                 1 if code < 128 else 2 if code < 2048 else 3 if code < 65536 else 4)
    return size


def _literature_result(body, root, candidates, *, metadata, version, external_doctype, external_reference_count,
                       max_passages, max_passage_chars, passage_offset, section_filter):
    paths = _literature_paths(root)
    source_sha256 = hashlib.sha256(body).hexdigest()
    passages, omitted, truncated, retained_body = [], 0, 0, False
    matching = 0
    output_bytes = 4096 + sum(_literature_json_size(value) for value in metadata.values() if type(value) is str)
    for notice in metadata["status"].get("notices", []):
        output_bytes += 256 + sum(_literature_json_size(value) for value in notice.values() if type(value) is str)
    for node, is_body in candidates:
        if not any(part.strip() for part in node.itertext()):
            continue
        matching += 1
        if matching <= passage_offset or len(passages) == max_passages:
            omitted += 1
            continue
        xpath = paths[node]
        output_bytes += 1024 + _literature_json_size(xpath)
        if output_bytes > MAX_EXTRACTION_BYTES:
            raise ValueError("Literature passage output allocation budget exceeded")
        digest, fragments, length, remaining = hashlib.sha256(), [], 0, max_passage_chars
        for fragment in node.itertext():
            digest.update(fragment.encode("utf-8"))
            length += len(fragment)
            if remaining:
                prefix = fragment[:remaining]
                output_bytes += _literature_json_size(prefix) - 2
                if output_bytes > MAX_EXTRACTION_BYTES:
                    raise ValueError("Literature passage output allocation budget exceeded")
                fragments.append(prefix)
                remaining -= len(prefix)
        text = "".join(fragments)
        is_truncated = length > len(text)
        truncated += is_truncated
        retained_body = retained_body or is_body
        passages.append({"passage_id": "passage-" + hashlib.sha256((source_sha256 + "\n" + xpath).encode()).hexdigest()[:24],
            "source_sha256": source_sha256,
            "locator": {"kind": "parsed_xml_text", "xpath": xpath, "start": 0, "end": len(text),
                        "unit": "unicode_codepoint", "transformation": "xml_itertext/1"},
            "text": text, "full_text_sha256": digest.hexdigest(), "truncated": is_truncated})
    available = "unavailable" if not passages else "truncated" if omitted or truncated else "available"
    paragraph_mapping = {"paragraph_selection": "outermost_p_xml_itertext/1"} if version == PMC_PASSAGE_EXTRACTOR_VERSION else {}
    pagination = {}
    if passage_offset or section_filter is not None:
        next_offset = passage_offset + len(passages)
        paragraph_mapping.update(passage_offset=passage_offset, section_filter=section_filter)
        pagination["pagination"] = {"total_matching_passages": matching,
                                    "next_passage_offset": next_offset if next_offset < matching else None}
    unavailable_reason = None
    if not passages:
        if matching:
            unavailable_reason = "passage_offset_out_of_range"
        elif section_filter is not None:
            unavailable_reason = "no_matching_nonempty_passages"
        else:
            unavailable_reason = "no_supported_nonempty_passages"
    return _copy({"format": LITERATURE_EXTRACTION_FORMAT, "source_sha256": source_sha256,
        "extractor_version": version, "access": "full_text" if retained_body else "abstract", **metadata,
        "extraction_mapping": {"transformation": "xml_itertext/1", **paragraph_mapping,
            "parser_normalization": "xml_1.0_line_endings_and_character_references",
            "inert_metadata_namespaces": [_ALI_NAMESPACE] if any(node.tag in _ALI_METADATA_TAGS for node in root.iter()) else [],
            "external_doctype": external_doctype, "max_passages": max_passages, "max_passage_chars": max_passage_chars},
        "external_resources": {"inspection": "not_performed", "reference_count": external_reference_count,
                               "retrieval_authorized": False},
        "passages": passages, "truncation": {"omitted_passages": omitted, "truncated_passages": truncated},
        **pagination, "availability": available, "unavailable_reason": unavailable_reason})


def _pubmed_notices(citation):
    """Retain explicit linked targets without applying a notice to itself."""
    container = _literature_one(citation, "CommentsCorrectionsList")
    notices = []
    if container is None:
        return notices
    for node in container:
        if node.tag != "CommentsCorrections":
            continue
        if len(notices) >= MAX_LITERATURE_NOTICES:
            raise ValueError("PubMed notice count bound exceeded")
        relation = _text(node.get("RefType"), 128)
        pmid_node = _literature_one(node, "PMID")
        pmid = None if pmid_node is None else _literature_identifiers([("pmid", pmid_node)], expected={})["pmid"]
        reference = _literature_one(node, "RefSource")
        reference_source = None
        if reference is not None:
            size = sum(len(fragment.encode("utf-8")) for fragment in reference.itertext())
            if size > MAX_LITERATURE_NOTICE_TEXT_BYTES:
                raise ValueError("PubMed notice reference text bound exceeded")
            reference_source = "".join(reference.itertext())
        notices.append({"relation": relation, "pmid": pmid, "reference_source": reference_source})
    return notices


def _matches_section(section_filter, *labels):
    return any(section_filter == " ".join(label.casefold().split()) for label in labels if label)


def extract_pubmed_passages(body: bytes, *, expected_pmid: str, max_passages: int = 64,
                            max_passage_chars: int = 8000, passage_offset: int = 0,
                            section_filter: str | None = None) -> dict:
    """Retain bounded abstracts from exactly one identity-matched PubMed article.

    Parsed-text coordinates include XML line-ending/entity normalization and
    are never represented as source byte offsets. Exact text is not support.
    Offsets are zero-based over nonempty matches. A section filter selects the
    abstract or an exact normalized AbstractText label or NLM category.
    """
    section_filter = _literature_limits(max_passages, max_passage_chars, passage_offset, section_filter)
    if expected_pmid is None:
        raise ValueError("An exact expected PMID is required")
    expected_pmid = _canonical_identifier("pmid", expected_pmid)
    root, external_doctype, reference_count = _parse_literature_xml(body, pubmed=True)
    articles = list(root.iter("PubmedArticle"))
    if (len(articles) != 1 or root.tag not in {"PubmedArticleSet", "PubmedArticle"}
            or (root.tag == "PubmedArticleSet" and (len(root) != 1 or root[0] is not articles[0]))):
        raise ValueError("Exactly one PubMed article is required")
    article = articles[0]
    citation = _literature_one(article, "MedlineCitation", required=True)
    content = _literature_one(citation, "Article", required=True)
    pmid_node = _literature_one(citation, "PMID", required=True)
    primary = _literature_identifiers([("pmid", pmid_node)], expected={"pmid": expected_pmid})
    pubmed_data = _literature_one(article, "PubmedData")
    entries = []
    if pubmed_data is not None:
        id_list = _literature_one(pubmed_data, "ArticleIdList")
        aliases = {"pubmed": "pmid", "pmid": "pmid", "pmc": "pmcid", "pmcid": "pmcid", "doi": "doi"}
        entries = [(aliases[node.get("IdType")], node) for node in ([] if id_list is None else id_list) if node.get("IdType") in aliases]
    entries.extend(("doi", node) for node in content.findall("ELocationID") if node.get("EIdType") == "doi")
    identifiers = _literature_identifiers(entries, expected={})
    if identifiers["pmid"] is not None and identifiers["pmid"] != primary["pmid"]:
        raise ValueError("Conflicting article PMID")
    identifiers["pmid"] = primary["pmid"]
    notices = _pubmed_notices(citation)
    relations = {notice["relation"] for notice in notices}
    publication_types = {_literature_text([node], maximum=256) for node in content.findall("PublicationTypeList/PublicationType")}
    pub_date = _literature_one(content, "Journal/JournalIssue/PubDate")
    if pub_date is None:
        dates = content.findall("ArticleDate")
        pub_date = dates[0] if dates else None
    metadata = {"identifiers": identifiers, "title": _literature_text(content.findall("ArticleTitle")),
        "publication_date": _literature_date(pub_date),
        "manuscript_version": _literature_text(content.findall("ArticleVersion"), maximum=512),
        "license": _literature_text(content.findall("License")),
        "status": {"retraction": "retracted" if "RetractionIn" in relations or "Retracted Publication" in publication_types else "unknown",
                   "correction": "reported" if "ErratumIn" in relations else "unknown",
                   "coverage": "supplied_pubmed_record_only_no_independent_notice_search", "notices": notices}}
    abstract = _literature_one(content, "Abstract")
    candidates = ((node, False) for node in (() if abstract is None else abstract)
        if node.tag == "AbstractText" and (section_filter in {None, "abstract"}
            or section_filter != "body" and _matches_section(section_filter, node.get("Label"), node.get("NlmCategory"))))
    return _literature_result(body, root, candidates, metadata=metadata, version=PUBMED_PASSAGE_EXTRACTOR_VERSION,
        external_doctype=external_doctype, external_reference_count=reference_count,
        max_passages=max_passages, max_passage_chars=max_passage_chars,
        passage_offset=passage_offset, section_filter=section_filter)


def _literature_section_matches(container, section_filter, label_attribute):
    title = _literature_text(container.findall("title"))
    return _matches_section(section_filter, container.get(label_attribute), title)


def _literature_paragraphs(container, section_filter=None):
    """Select outermost paragraphs; itertext retains nested float/caption text.

    Inner paragraphs are not repeated as separate passages. This text mapping
    neither reconstructs table geometry nor interprets figures or media.
    """
    if container.tag == "p":
        if section_filter is None:
            yield container
        return
    if section_filter is not None and container.tag == "sec":
        if _literature_section_matches(container, section_filter, "sec-type"):
            section_filter = None
    for child in container:
        yield from _literature_paragraphs(child, section_filter)


def extract_pmc_passages(body: bytes, *, expected_pmcid: str, expected_pmid: str | None = None,
                         expected_doi: str | None = None, max_passages: int = 64,
                         max_passage_chars: int = 8000, passage_offset: int = 0,
                         section_filter: str | None = None) -> dict:
    """Extract JATS abstracts and body paragraphs with the strict XML boundary.

    Absent body content never grants full-text access. URLs are inert source
    data; no resource or notice search occurs and notice coverage is unknown.
    Offsets are zero-based over nonempty matches. A filter selects the
    abstract or body, or an exact normalized section heading or type.
    """
    section_filter = _literature_limits(max_passages, max_passage_chars, passage_offset, section_filter)
    if expected_pmcid is None:
        raise ValueError("An exact expected PMCID is required")
    expected_pmcid = _canonical_identifier("pmcid", expected_pmcid)
    root, external_doctype, reference_count = _parse_literature_xml(body, pubmed=False)
    if root.tag != "article" or sum(1 for _ in root.iter("article")) != 1:
        raise ValueError("Exactly one unnamespaced JATS article is required")
    front = _literature_one(root, "front", required=True)
    meta = _literature_one(front, "article-meta", required=True)
    article_body = _literature_one(root, "body")
    aliases = {"pmc": "pmcid", "pmcid": "pmcid", "pmid": "pmid", "pubmed": "pmid", "doi": "doi"}
    entries = [(aliases[node.get("pub-id-type")], node) for node in meta.findall("article-id") if node.get("pub-id-type") in aliases]
    identifiers = _literature_identifiers(entries, expected={"pmcid": expected_pmcid, "pmid": expected_pmid, "doi": expected_doi})
    statuses = {root.get("article-status", "").lower()}
    for node in meta.findall("custom-meta-group/custom-meta"):
        name = _literature_text(node.findall("meta-name"), maximum=256)
        if name is not None and name.lower() in {"pmc-status", "article-status", "retraction-status", "correction-status"}:
            value = _literature_text(node.findall("meta-value"), maximum=256)
            if value is not None:
                statuses.add(value.lower())
    dates = meta.findall("pub-date")
    licenses = meta.findall("permissions/license")
    license_text = _literature_text(licenses)
    if license_text is None and len(licenses) == 1:
        license_text = licenses[0].get("{http://www.w3.org/1999/xlink}href", licenses[0].get("href"))
        if license_text is not None:
            _text(license_text, 16_000)
    metadata = {"identifiers": identifiers, "title": _literature_text(meta.findall("title-group/article-title")),
        "publication_date": _literature_date(dates[0] if dates else None),
        "manuscript_version": _literature_text(meta.findall("article-version"), maximum=512), "license": license_text,
        "status": {"retraction": "retracted" if "retracted" in statuses or root.get("article-type") == "retracted-article" else "unknown",
                   "correction": "reported" if "corrected" in statuses else "unknown",
                   "coverage": "supplied_pmc_record_only_no_independent_notice_search"}}

    def candidates():
        if section_filter != "body":
            for abstract in meta.findall("abstract"):
                selected_filter = section_filter
                if section_filter == "abstract" or (
                        section_filter is not None
                        and _literature_section_matches(abstract, section_filter, "abstract-type")):
                    selected_filter = None
                if next(abstract.iter("p"), None) is None:
                    if selected_filter is None:
                        yield abstract, False
                else:
                    yield from ((node, False) for node in _literature_paragraphs(abstract, selected_filter))
        if article_body is not None and section_filter != "abstract":
            selected_filter = None if section_filter == "body" else section_filter
            yield from ((node, True) for node in _literature_paragraphs(article_body, selected_filter))

    return _literature_result(body, root, candidates(), metadata=metadata, version=PMC_PASSAGE_EXTRACTOR_VERSION,
        external_doctype=external_doctype, external_reference_count=reference_count,
        max_passages=max_passages, max_passage_chars=max_passage_chars,
        passage_offset=passage_offset, section_filter=section_filter)


def _bounded_literature_record(record):
    remaining, nodes = MAX_EXTRACTION_BYTES, 0

    def visit(value, depth=0):
        nonlocal remaining, nodes
        nodes += 1
        if depth > 16 or nodes > 12_000:
            raise ValueError("Literature record nesting or node bound exceeded")
        if type(value) is str:
            if len(value) > MAX_EXTRACTION_BYTES:
                raise ValueError("Literature record string bound exceeded")
            size = _literature_json_size(value)
        elif value is None or type(value) is bool:
            size = 5
        elif type(value) is int and abs(value) <= MAX_XML_NODES:
            size = 8
        elif type(value) in {dict, list}:
            if len(value) > MAX_XML_NODES:
                raise ValueError("Literature record collection bound exceeded")
            size = 2 + len(value) * 2
        else:
            raise ValueError("Use finite bounded literature record values")
        remaining -= size
        if remaining < 0:
            raise ValueError("Literature record output allocation budget exceeded")
        if type(value) is dict:
            for key, child in value.items():
                if type(key) is not str:
                    raise ValueError("Literature record keys must be strings")
                visit(key, depth + 1)
                visit(child, depth + 1)
        elif type(value) is list:
            for child in value:
                visit(child, depth + 1)

    visit(record)


def verify_literature_passage(body: bytes, extraction: dict, passage: dict) -> bool:
    """Replay the entire extraction, then require an exact retained passage.

    This verifies representation integrity, not identity authenticity, complete
    notice coverage, semantic support, scientific merit or clinical validity.
    """
    from devharness_core.provenance import canonical_json

    if type(extraction) is not dict or type(passage) is not dict:
        raise ValueError("Use literature extraction and passage records")
    _bounded_literature_record(extraction)
    _bounded_literature_record(passage)
    if (type(body) is not bytes or not body or len(body) > MAX_XML_BYTES
            or extraction.get("source_sha256") != hashlib.sha256(body).hexdigest()):
        raise ValueError("Literature source hash mismatch")
    try:
        if extraction["format"] != LITERATURE_EXTRACTION_FORMAT:
            raise ValueError("Unsupported literature extraction format")
        mapping, identifiers = extraction["extraction_mapping"], extraction["identifiers"]
        limits = {"max_passages": mapping["max_passages"], "max_passage_chars": mapping["max_passage_chars"],
                  "passage_offset": mapping.get("passage_offset", 0), "section_filter": mapping.get("section_filter")}
        if extraction["extractor_version"] == PUBMED_PASSAGE_EXTRACTOR_VERSION:
            expected = extract_pubmed_passages(body, expected_pmid=identifiers["pmid"], **limits)
        elif extraction["extractor_version"] == PMC_PASSAGE_EXTRACTOR_VERSION:
            expected = extract_pmc_passages(body, expected_pmcid=identifiers["pmcid"], expected_pmid=identifiers["pmid"],
                expected_doi=identifiers["doi"], **limits)
        else:
            raise ValueError("Unsupported literature extractor version")
    except (KeyError, TypeError) as error:
        raise ValueError("Malformed literature extraction record") from error
    if canonical_json(extraction) != canonical_json(expected):
        raise ValueError("Literature extraction differs from replayed source")
    encoded = canonical_json(passage)
    if not any(canonical_json(candidate) == encoded for candidate in expected["passages"]):
        raise ValueError("Literature passage differs from retained extraction")
    return True


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
