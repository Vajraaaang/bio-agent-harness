"""Hostile, artificial JATS and lexical references; no scientific conclusions."""
from copy import deepcopy
import hashlib
import io
import tarfile
import zipfile
import xml.etree.ElementTree as ET

import pytest

from devbench.analysis import rna_reference_extraction as api


JATS = b'''<article article-type="research-article" xmlns:xlink="http://www.w3.org/1999/xlink">
<front><article-meta><title-group><article-title>Artificial source</article-title></title-group></article-meta></front>
<body><sec id="results"><title>Results</title><p id="p1">GENE1 was not increased in patients.</p>
<table-wrap id="t1"><label>Table 1</label><caption><p>Artificial effects.</p></caption>
<table><thead><tr><th id="h1" colspan="2">Effects</th></tr><tr><th>Gene</th><th>Estimate</th></tr></thead>
<tbody><tr><td>GENE1</td><td headers="h1">-1.2</td></tr></tbody></table>
<table-wrap-foot><fn id="f1"><p>No interval reported.</p></fn></table-wrap-foot></table-wrap>
<supplementary-material id="s1" xlink:href="https://example.invalid/supplement.zip"><label>Supplement 1</label></supplementary-material>
</sec></body></article>'''


def extract(body=JATS, **kwargs):
    return api.extract_jats(body, source_sha256=hashlib.sha256(body).hexdigest(), **kwargs)


def test_jats_preserves_ids_xpath_headers_captions_footnotes_and_coordinates():
    record = extract()
    assert record["source_sha256"] == hashlib.sha256(JATS).hexdigest()
    assert record["extractor_version"] == "jats-stdlib/1"
    assert record["sections"][0]["element_id"] == "results"
    table = record["tables"][0]
    assert table["xpath"] == "/article[1]/body[1]/sec[1]/table-wrap[1]"
    assert table["caption"][0]["text"] == "Artificial effects."
    assert table["footnotes"][0]["element_id"] == "f1"
    assert table["headers"][0]["colspan"] == 2
    assert table["cells"][-1]["text"] == "-1.2"
    assert (table["cells"][-1]["row"], table["cells"][-1]["column"]) == (3, 2)
    assert table["cells"][-1]["header_ids"] == ["h1"]
    assert table["requires_review"] is True
    assert record["supplements"][0]["retrieval_authorized"] is False
    assert record["review_status"] == "proposal_requires_independent_review"


def test_image_only_table_keeps_caption_and_abstains_on_numeric_cells():
    body = b'<article xmlns:xlink="http://www.w3.org/1999/xlink"><body><table-wrap id="image"><caption><p>Some effects</p></caption><graphic xlink:href="table.png"/></table-wrap></body></article>'
    table = extract(body)["tables"][0]
    assert table["status"] == "not_assessable_image_only"
    assert table["cells"] == []
    assert table["caption"][0]["text"] == "Some effects"


@pytest.mark.parametrize("body", [
    b'<!DOCTYPE article SYSTEM "file:///etc/passwd"><article/>',
    b'<!DOCTYPE article SYSTEM "https://example.invalid/evil.dtd"><article/>',
    b'<!DOCTYPE article [<!ENTITY x SYSTEM "file:///etc/passwd">]><article>&x;</article>',
    b'<!DOCTYPE article [<!ENTITY x "aaa"><!ENTITY y "&x;&x;&x;">]><article>&y;</article>',
    b'<article xmlns:xi="http://www.w3.org/2001/XInclude"><xi:include href="file:///etc/passwd"/></article>',
    b'<?xml-stylesheet href="https://example.invalid/xsl"?><article/>',
    b'<article xml:base="https://example.invalid/"><body/></article>',
    '<!DOCTYPE article SYSTEM "file:///etc/passwd"><article/>'.encode("utf-16"),
])
def test_xml_external_resolution_entity_expansion_and_non_utf8_requests_rejected(body):
    with pytest.raises(ValueError):
        extract(body)


@pytest.mark.parametrize("body", [b'<article>' + b'<sec>' * 65 + b'</sec>' * 65 + b'</article>',
    b'<article>' + b'<p/>' * 25_001 + b'</article>',
    b'<article><p>' + b'x' * 500_001 + b'</p></article>',
    b'x' * 2_000_001, b'<article><body>', b'<article><p id="a"/><p id="a"/></article>'])
def test_parser_byte_depth_node_text_truncation_and_duplicate_id_bounds(body):
    with pytest.raises(ValueError):
        extract(body)


def test_xpath_growth_is_rejected_before_terminal_record_serialization(monkeypatch):
    # About 4 MiB of repeated paths under the old code, not the hundreds-of-MiB
    # maximal attack: the input and tree stay well inside existing XML limits.
    tag = "x" + "a" * 199
    body = ("<article><body>" + ("<" + tag + ">") * 50 +
        "<sec><p>x</p></sec>" * 200 + ("</" + tag + ">") * 50 + "</body></article>").encode()
    def forbidden(*args, **kwargs):
        pytest.fail("Expanded XPath records reached terminal serialization before budget admission")
    monkeypatch.setattr(api, "_copy", forbidden)
    with pytest.raises(ValueError, match="budget|limit|bound"):
        extract(body)


def test_nested_section_text_is_budgeted_before_join_and_terminal_serialization(monkeypatch):
    body = b"<article><body>" + b"<sec>" * 50 + b"<p>" + b"x" * 80_000 + b"</p>" + b"</sec>" * 50 + b"</body></article>"
    def forbidden(*args, **kwargs):
        pytest.fail("Repeated section text reached terminal serialization before budget admission")
    monkeypatch.setattr(api, "_copy", forbidden)
    with pytest.raises(ValueError, match="budget|limit|bound"):
        extract(body)


def test_foreign_structural_namespace_cannot_emit_unqualified_wrong_xpath():
    body = b'<article><body><x:sec xmlns:x="urn:foreign"><x:p>foreign</x:p></x:sec><sec><p>ordinary</p></sec></body></article>'
    with pytest.raises(ValueError, match="namespace"):
        extract(body)


def test_supported_inline_mathml_and_xlink_remain_nonfetching():
    body = b'<article xmlns:m="http://www.w3.org/1998/Math/MathML" xmlns:xlink="http://www.w3.org/1999/xlink"><body><sec><p>Ratio <m:math><m:mfrac><m:mn>1</m:mn><m:mn>2</m:mn></m:mfrac></m:math>.</p><supplementary-material xlink:href="https://example.invalid/observed"/></sec></body></article>'
    record = extract(body)
    assert record["sections"][0]["xpath"] == "/article[1]/body[1]/sec[1]"
    assert record["supplements"][0]["retrieval_authorized"] is False


def test_mathml_sibling_cannot_shift_emitted_unnamespaced_xpath():
    body = b'<article xmlns:m="http://www.w3.org/1998/Math/MathML"><body><m:math><m:mn>0</m:mn></m:math><math><sec><p>ordinary</p></sec></math></body></article>'
    record = extract(body)
    section = record["sections"][0]
    parsed = ET.fromstring(body)
    relative_xpath = "." + section["xpath"][len("/article[1]"):]
    selected = parsed.find(relative_xpath)
    assert selected is not None and "".join(selected.itertext()) == section["text"] == "ordinary"
    assert section["xpath"] == "/article[1]/body[1]/math[1]/sec[1]"


def test_original_identity_and_extractor_version_are_bound():
    with pytest.raises(ValueError, match="hash"):
        api.extract_jats(JATS, source_sha256="0" * 64)
    with pytest.raises(ValueError, match="version"):
        extract(extractor_version="unreviewed/9")


@pytest.mark.parametrize("content_type,body", [
    ("application/pdf", b'%PDF-1.7 /JavaScript /OpenAction /URI (https://example.invalid/evil)'),
    ("image/png", b'\x89PNG\r\n\x1a\n'),
    ("application/zip", b'PK\x03\x04../outside'),
    ("application/gzip", b'\x1f\x8b' + b'compressed-expansion'),
])
def test_unsupported_pdf_ocr_figures_and_archives_reject_before_parse(content_type, body, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unsupported input reached JATS parser")
    monkeypatch.setattr(api, "extract_jats", forbidden)
    with pytest.raises(ValueError, match="capability"):
        api.extract_reference_asset(body, content_type=content_type, source_sha256=hashlib.sha256(body).hexdigest())


def test_archive_traversal_and_expansion_payloads_are_refused_without_unpacking():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        handle.writestr("../../outside", b"x" * 100_000)
    with pytest.raises(ValueError, match="capability"):
        api.extract_reference_asset(archive.getvalue(), content_type="application/zip",
            source_sha256=hashlib.sha256(archive.getvalue()).hexdigest())
    with pytest.raises(ValueError, match="capability"):
        api.extract_reference_asset(archive.getvalue(), content_type="application/xml",
            source_sha256=hashlib.sha256(archive.getvalue()).hexdigest())
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w") as handle:
        entry = tarfile.TarInfo("../outside")
        entry.type, entry.linkname = tarfile.SYMTYPE, "/etc/passwd"
        handle.addfile(entry)
    with pytest.raises(ValueError, match="capability"):
        api.extract_reference_asset(tar.getvalue(), content_type="application/x-tar",
            source_sha256=hashlib.sha256(tar.getvalue()).hexdigest())
    with pytest.raises(ValueError, match="capability"):
        api.extract_reference_asset(tar.getvalue(), content_type="application/xml",
            source_sha256=hashlib.sha256(tar.getvalue()).hexdigest())


def test_tar_with_xml_looking_filename_is_rejected_before_parser(monkeypatch):
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w", format=tarfile.USTAR_FORMAT) as handle:
        entry = tarfile.TarInfo("<article>")
        entry.size = 1
        handle.addfile(entry, io.BytesIO(b"x"))
    body = tar.getvalue()
    assert body.startswith(b"<article>") and body[257:263] == b"ustar\0"
    def forbidden(*args, **kwargs):
        pytest.fail("TAR payload reached XML parser because its filename looked like XML")
    monkeypatch.setattr(api, "extract_jats", forbidden)
    with pytest.raises(ValueError, match="capability"):
        api.extract_reference_asset(body, content_type="application/xml", source_sha256=hashlib.sha256(body).hexdigest())


@pytest.mark.parametrize("raw,rule,canonical", [("-1.20", "identity", "-1.2"),
    ("−1.2", "unicode_minus_to_ascii", "-1.2"), ("12.5%", "percentage_to_proportion", "0.125")])
def test_reviewed_numeric_transform_preserves_raw_lexeme_and_named_rule(raw, rule, canonical):
    record = api.canonicalize_reference_number(raw, rule=rule, reviewed=True)
    assert record["raw_text"] == raw and record["canonical_decimal"] == canonical
    assert record["rule"] == rule and record["number_type"] == "exact"


@pytest.mark.parametrize("raw,rule", [("−1.2", "identity"), ("−1.2", "unicode_minus_to_ascii"),
    ("12.5%", "percentage_to_proportion"), ("1,234", "identity"), ("1,2", "identity"),
    ("<0.001", "identity"), ("1–2", "identity"), ("1 +/- 0.2", "identity"),
    ("1.2 (0.9, 1.5)", "identity"), ("NaN", "identity"), ("1e999", "identity"),
    ("1 234", "identity"), ("12.5%", "guess_locale")])
def test_unreviewed_ambiguous_or_censored_forms_cannot_be_exact(raw, rule):
    with pytest.raises(ValueError):
        api.canonicalize_reference_number(raw, rule=rule, reviewed=False)


@pytest.mark.parametrize("raw", ["1,234", "1,2", "<0.001", "1–2", "1 +/- 0.2", "1.2 (0.9, 1.5)"])
def test_review_does_not_turn_ambiguous_lexemes_into_exact_values(raw):
    with pytest.raises(ValueError):
        api.canonicalize_reference_number(raw, rule="identity", reviewed=True)


def test_censoring_and_absent_uncertainty_stay_typed_separately():
    censored = api.classify_reference_number("<0.001")
    assert censored["number_type"] == "censored"
    assert censored["operator"] == "<" and censored["bound_decimal"] == "0.001"
    assert censored["canonical_decimal"] is None
    assert api.classify_reference_number("1,2")["number_type"] == "ambiguous"
    assert api.classify_reference_number("1.2")["uncertainty"] is None


def test_numeric_record_replay_binds_original_token_and_rejects_forged_canonical():
    body = "GENE1 estimate −1.2; missing interval.".encode()
    text = body.decode()
    locator = {"start_byte": 0, "end_byte": len(body), "text": text, "location": "fixture"}
    record = api.canonicalize_reference_number("−1.2", rule="unicode_minus_to_ascii", reviewed=True)
    assert api.validate_reference_number_record(record, body=body, locator=locator) == record
    record["canonical_decimal"] = "1.2"
    with pytest.raises(ValueError):
        api.validate_reference_number_record(record, body=body, locator=locator)
    record = api.canonicalize_reference_number("1.2", rule="identity", reviewed=True)
    with pytest.raises(ValueError):
        api.validate_reference_number_record(record, body=body, locator=locator)


def context_review():
    body = b"GENE1 was not increased in patients."
    locator = {"start_byte": 0, "end_byte": len(body), "text": body.decode(), "location": "Results p1"}
    excerpt = {"excerpt_id": "quote1", "source_id": "source1", "locator": locator,
               "visibility": "evaluator", "purpose": "outcome"}
    checks = {key: {"state": "supported", "evidence_locators": [deepcopy(locator)],
                    "explanation": "Artificial independent review."}
              for key in ("entity", "population", "contrast", "measurement", "uncertainty", "negation", "scope")}
    checks["negation"]["state"] = "contradicted"
    review = {"format": api.CONTEXT_REVIEW_FORMAT, "review_id": "review1", "excerpt_id": "quote1",
        "source_sha256": hashlib.sha256(body).hexdigest(), "claim": "GENE1 increased in patients.",
        "proposal_origin": "model_assisted", "proposal_author_id": "workflow-author",
        "reviewed_by": "independent-curator", "checks": checks, "conclusion": "contradicted",
        "limitations": "Caller-declared contextual review is not authenticated scientific approval."}
    return body, excerpt, review


def test_exact_quote_can_be_contextually_contradicted_and_cannot_self_authenticate():
    body, excerpt, review = context_review()
    result = api.validate_context_review(review, excerpt=excerpt, body=body, participant_id="workflow-author")
    assert result["conclusion"] == "contradicted"
    review["conclusion"] = "supported"
    with pytest.raises(ValueError, match="contradict"):
        api.validate_context_review(review, excerpt=excerpt, body=body, participant_id="workflow-author")
    review["conclusion"] = "contradicted"
    review["reviewed_by"] = review["proposal_author_id"]
    with pytest.raises(ValueError, match="independent"):
        api.validate_context_review(review, excerpt=excerpt, body=body, participant_id="workflow-author")


def test_context_review_requires_all_seven_dimensions_and_exact_evidence():
    body, excerpt, review = context_review()
    del review["checks"]["population"]
    with pytest.raises(ValueError):
        api.validate_context_review(review, excerpt=excerpt, body=body, participant_id="workflow-author")
    body, excerpt, review = context_review()
    review["checks"]["population"]["evidence_locators"][0]["text"] = "a fabricated population"
    with pytest.raises(ValueError):
        api.validate_context_review(review, excerpt=excerpt, body=body, participant_id="workflow-author")
