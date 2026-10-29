"""Local FASTQ admission exercises real metadata/design checks before engines."""
from copy import deepcopy
import csv
import gzip
import hashlib
from pathlib import Path
import os

import numpy as np
import pytest

from devbench.analysis.fastq_contract import preflight_fastq_plan, validate_design
from devbench.analysis import fastq_contract


def rows():
    return [{"sample_id": f"s{i}", "donor_id": f"d{i}", "batch": batch, "condition": condition}
            for i, (batch, condition) in enumerate(
                [("x", "A"), ("x", "A"), ("y", "A"), ("y", "A"),
                 ("x", "B"), ("x", "B"), ("y", "B"), ("y", "B")])]


def write_csv(path, fieldnames, values):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(values)


def plan_fixture(tmp_path):
    root = tmp_path.resolve()
    metadata = root / "metadata.csv"
    write_csv(metadata, list(rows()[0]), rows())
    samples = []
    for row in rows():
        fastq = root / (row["sample_id"] + ".fastq")
        fastq.write_text(f"@{row['sample_id']}\nACGT\n+\nIIII\n")
        samples.append({"sample": row["sample_id"], "fastq_1": str(fastq),
                        "fastq_2": "", "strandedness": "unstranded"})
    sheet = root / "samples.csv"
    write_csv(sheet, list(samples[0]), samples)
    fasta, gtf = root / "reference.fa", root / "reference.gtf"
    fasta.write_text(">chr1\nACGTACGT\n")
    gtf.write_text('chr1\tfixture\texon\t1\t8\t.\t+\t.\tgene_id "g1"; transcript_id "t1";\n')
    return {"samplesheet": str(sheet), "metadata": str(metadata),
            "reference": {"fasta": str(fasta), "gtf": str(gtf)},
            "formula": "~ batch + condition",
            "contrast": {"factor": "condition", "numerator": "B", "denominator": "A"},
            "biological_unit": "donor_id", "batch_columns": ["batch"],
            "analysis_scope": "bulk", "alpha": 0.05}


def design_kwargs():
    return {"formula": "~ batch + condition",
            "contrast": {"factor": "condition", "numerator": "B", "denominator": "A"},
            "biological_unit": "donor_id", "batch_columns": ["batch"]}


def test_valid_local_plan_retains_exact_formula_family_and_input_identities(tmp_path):
    plan = plan_fixture(tmp_path)
    result = preflight_fastq_plan(plan)
    assert result["sample_ids"] == [row["sample_id"] for row in rows()]
    assert result["design"]["formula"] == plan["formula"]
    assert result["design"]["design_columns"] == ["Intercept", "batch[y]", "condition[B]"]
    assert result["design"]["contrast_vector"] == [0.0, 0.0, 1.0]
    assert result["design"]["rank"] == 3
    assert result["design"]["residual_df"] == 5
    assert result["design"]["unit_count"] == 8
    assert result["pins"]["workflow_revision"] == "e7ca46272c8f9d5ceee3f71759f4ba551d3217a4"
    assert len(result["input_files"]) == 12
    for entry in result["input_files"]:
        assert Path(entry["path"]).is_absolute()
        assert hashlib.sha256(Path(entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
    assert result["scientific_correctness"] is None
    assert result["model_execution_authorized"] is False


def test_design_is_preserved_independently_of_metadata_row_order():
    selected = list(reversed(rows()))
    result = validate_design(selected, **design_kwargs())
    matrix = np.asarray(result["design_matrix"])
    assert result["sample_ids"] == [row["sample_id"] for row in selected]
    assert np.array_equal(matrix[:, 1], [row["batch"] == "y" for row in selected])
    assert np.array_equal(matrix[:, 2], [row["condition"] == "B" for row in selected])


@pytest.mark.parametrize("field", ["formula", "contrast", "biological_unit", "batch_columns", "analysis_scope"])
def test_missing_scientific_declarations_refuse(tmp_path, field):
    plan = plan_fixture(tmp_path)
    del plan[field]
    with pytest.raises(ValueError, match=field):
        preflight_fastq_plan(plan)


@pytest.mark.parametrize("formula", ["~ condition", "~ batch * condition", "~ batch + condition + condition",
                                    "~ 0 + condition", "~ C(condition)", "~ condition; print(1)"])
def test_formula_cannot_drop_batch_or_use_unsupported_grammar(formula):
    kwargs = {**design_kwargs(), "formula": formula}
    with pytest.raises(ValueError):
        validate_design(rows(), **kwargs)


def test_confounded_batch_refuses_without_silently_dropping_covariate():
    selected = rows()
    for row in selected:
        row["batch"] = row["condition"]
    with pytest.raises(ValueError, match="confound|rank"):
        validate_design(selected, **design_kwargs())


def test_empty_batch_declaration_cannot_hide_batch_column():
    kwargs = {**design_kwargs(), "formula": "~ condition", "batch_columns": []}
    with pytest.raises(ValueError, match="batch.*declar|undeclared.*batch"):
        validate_design(rows(), **kwargs)


@pytest.mark.parametrize("column", ["donor_id", "subject_id", "cell", "cell_line"])
def test_unique_library_labels_cannot_hide_repeated_biological_identity(column):
    selected = rows()
    for i, row in enumerate(selected):
        row[column] = f"unit{i % 4}"
        row["declared_unit"] = f"unit{i}"
    kwargs = {**design_kwargs(), "biological_unit": "declared_unit"}
    with pytest.raises(ValueError, match="repeated_biological_units:" + column):
        validate_design(selected, **kwargs)


@pytest.mark.parametrize("unit", ["sample_id", "library_id", "barcode", "cell_id", "cell"])
def test_library_or_single_cell_identity_is_not_biological_replication(unit):
    selected = rows()
    for i, row in enumerate(selected):
        row[unit] = f"s{i}"
    with pytest.raises(ValueError, match="biological|membership"):
        validate_design(selected, **{**design_kwargs(), "biological_unit": unit})


@pytest.mark.parametrize("scope", ["single_cell", "pseudobulk", "single-cell", "enrichment"])
def test_only_bulk_analysis_scope_is_admitted(tmp_path, scope):
    plan = plan_fixture(tmp_path)
    plan["analysis_scope"] = scope
    with pytest.raises(ValueError, match="bulk"):
        preflight_fastq_plan(plan)


def test_missing_independent_replicates_refuses():
    selected = rows()[:4] + [rows()[4]]
    with pytest.raises(ValueError, match="replicat"):
        validate_design(selected, **design_kwargs())


@pytest.mark.parametrize("level", ["A", "absent"])
def test_invalid_contrast_refuses(level):
    kwargs = design_kwargs()
    kwargs["contrast"]["numerator"] = level
    with pytest.raises(ValueError, match="contrast"):
        validate_design(rows(), **kwargs)


@pytest.mark.parametrize("field", ["samplesheet", "metadata", "fasta", "gtf", "fastq_1"])
def test_remote_inputs_refuse_before_reading(tmp_path, field):
    plan = plan_fixture(tmp_path)
    if field in {"fasta", "gtf"}:
        plan["reference"][field] = "s3://bucket/input"
    elif field == "fastq_1":
        with Path(plan["samplesheet"]).open(newline="") as stream:
            samples = list(csv.DictReader(stream))
        samples[0]["fastq_1"] = "https://example.org/reads.fastq.gz"
        write_csv(Path(plan["samplesheet"]), list(samples[0]), samples)
    else:
        plan[field] = "s3://bucket/input"
    with pytest.raises(ValueError, match="local|absolute"):
        preflight_fastq_plan(plan)


@pytest.mark.parametrize("strand", ["auto", "unknown", "", "guessed"])
def test_unknown_strandedness_refuses(tmp_path, strand):
    plan = plan_fixture(tmp_path)
    sheet = Path(plan["samplesheet"])
    with sheet.open(newline="") as stream:
        samples = list(csv.DictReader(stream))
    samples[0]["strandedness"] = strand
    write_csv(sheet, list(samples[0]), samples)
    with pytest.raises(ValueError, match="strandedness"):
        preflight_fastq_plan(plan)


def test_metadata_sample_mismatch_refuses(tmp_path):
    plan = plan_fixture(tmp_path)
    selected = rows()
    selected[0]["sample_id"] = "unexpected"
    write_csv(Path(plan["metadata"]), list(selected[0]), selected)
    with pytest.raises(ValueError, match="sample"):
        preflight_fastq_plan(plan)


def test_symlinked_input_refuses(tmp_path):
    plan = plan_fixture(tmp_path)
    link = tmp_path.resolve() / "linked.fa"
    link.symlink_to(plan["reference"]["fasta"])
    plan["reference"]["fasta"] = str(link)
    with pytest.raises(ValueError, match="canonical|symlink"):
        preflight_fastq_plan(plan)


def test_duplicate_csv_header_refuses(tmp_path):
    plan = plan_fixture(tmp_path)
    Path(plan["metadata"]).write_text("sample_id,donor_id,condition,condition,batch\ns0,d0,A,B,x\n")
    with pytest.raises(ValueError, match="header"):
        preflight_fastq_plan(plan)


def test_malformed_fastq_refuses(tmp_path):
    plan = plan_fixture(tmp_path)
    (tmp_path.resolve() / "s0.fastq").write_text("@read\nACGT\n+\nII\n")
    with pytest.raises(ValueError, match="FASTQ"):
        preflight_fastq_plan(plan)


def test_one_file_cannot_be_reused_as_several_biological_samples(tmp_path):
    plan = plan_fixture(tmp_path)
    sheet = Path(plan["samplesheet"])
    with sheet.open(newline="") as stream:
        samples = list(csv.DictReader(stream))
    samples[1]["fastq_1"] = samples[0]["fastq_1"]
    write_csv(sheet, list(samples[0]), samples)
    with pytest.raises(ValueError, match="reused|duplicate"):
        preflight_fastq_plan(plan)


def test_plan_cannot_override_immutable_workflow(tmp_path):
    plan = plan_fixture(tmp_path)
    plan["workflow_revision"] = "latest"
    with pytest.raises(ValueError, match="unsupported|unknown"):
        preflight_fastq_plan(plan)


def test_preflight_does_not_mutate_supplied_plan(tmp_path):
    plan = plan_fixture(tmp_path)
    before = deepcopy(plan)
    preflight_fastq_plan(plan)
    assert plan == before


def test_hardlinked_fastq_cannot_create_another_replicate(tmp_path):
    plan = plan_fixture(tmp_path)
    second = tmp_path.resolve() / "s1.fastq"
    second.unlink()
    os.link(tmp_path.resolve() / "s0.fastq", second)
    with pytest.raises(ValueError, match="Duplicate FASTQ"):
        preflight_fastq_plan(plan)


def test_multiple_lanes_of_one_sample_are_memberships_not_replicates(tmp_path):
    plan = plan_fixture(tmp_path)
    sheet = Path(plan["samplesheet"])
    with sheet.open(newline="") as stream:
        samples = list(csv.DictReader(stream))
    lane = tmp_path.resolve() / "s0_lane2.fastq"
    lane.write_text("@s0lane2\nTGCA\n+\nIIII\n")
    samples.insert(1, {**samples[0], "fastq_1": str(lane)})
    write_csv(sheet, list(samples[0]), samples)
    result = preflight_fastq_plan(plan)
    assert len(result["samples"]) == 9
    assert result["design"]["unit_count"] == len(result["sample_ids"]) == 8


@pytest.mark.parametrize("change", [{"strandedness": "forward"}, {"fastq_2": "second.fastq"}])
def test_one_sample_cannot_mix_lane_layouts(tmp_path, change):
    plan = plan_fixture(tmp_path)
    sheet = Path(plan["samplesheet"])
    with sheet.open(newline="") as stream:
        samples = list(csv.DictReader(stream))
    samples.insert(1, {**samples[0], **change})
    write_csv(sheet, list(samples[0]), samples)
    with pytest.raises(ValueError, match="lanes cannot mix"):
        preflight_fastq_plan(plan)


def test_annotation_contig_must_exist_in_fasta(tmp_path):
    plan = plan_fixture(tmp_path)
    gtf = Path(plan["reference"]["gtf"])
    gtf.write_text(gtf.read_text().replace("chr1", "chr2"))
    with pytest.raises(ValueError, match="contigs"):
        preflight_fastq_plan(plan)


def test_annotation_coordinates_cannot_exceed_reference_contig(tmp_path):
    plan = plan_fixture(tmp_path)
    gtf = Path(plan["reference"]["gtf"])
    gtf.write_text(gtf.read_text().replace("\t1\t8\t", "\t1\t100\t"))
    with pytest.raises(ValueError, match="coordinates"):
        preflight_fastq_plan(plan)


def test_unknown_biological_identity_refuses_even_with_unique_library_ids():
    selected = rows()
    selected[0]["donor_id"] = "unknown"
    with pytest.raises(ValueError, match="unknown"):
        validate_design(selected, **design_kwargs())


def test_non_string_batch_declaration_is_a_retained_value_error():
    with pytest.raises(ValueError, match="batch_columns"):
        validate_design(rows(), **{**design_kwargs(), "batch_columns": [["batch"]]})


def test_zero_residual_degrees_of_freedom_refuse():
    selected = rows()[:2] + rows()[4:6]
    for i, row in enumerate(selected):
        row["batch"] = str(i)
    with pytest.raises(ValueError, match="rank|degrees"):
        validate_design(selected, **design_kwargs())


def test_identity_aliases_are_case_insensitive():
    selected = rows()
    for i, row in enumerate(selected):
        row["Donor_ID"] = f"d{i % 4}"
    with pytest.raises(ValueError, match="repeated_biological_units:Donor_ID"):
        validate_design(selected, **design_kwargs())


def test_batch_aliases_are_case_insensitive():
    selected = rows()
    for row in selected:
        row["Batch"] = row.pop("batch")
    with pytest.raises(ValueError, match="batch.*declar"):
        validate_design(selected, **{**design_kwargs(), "formula": "~ condition", "batch_columns": []})


@pytest.mark.parametrize("marker", ["NA", "not known", "not_known", "missing", "not available"])
def test_common_unknown_identity_markers_do_not_establish_replication(marker):
    selected = rows()
    selected[0]["donor_id"] = marker
    with pytest.raises(ValueError, match="unknown"):
        validate_design(selected, **design_kwargs())


def test_upstream_reference_path_whitespace_is_rejected_before_nextflow(tmp_path):
    plan = plan_fixture(tmp_path)
    ref = Path(plan["reference"]["fasta"])
    spaced = tmp_path.resolve() / "reference file.fa"
    ref.rename(spaced)
    plan["reference"]["fasta"] = str(spaced)
    with pytest.raises(ValueError, match="whitespace"):
        preflight_fastq_plan(plan)


def test_star_salmon_exons_require_transcript_identity(tmp_path):
    plan = plan_fixture(tmp_path)
    gtf = Path(plan["reference"]["gtf"])
    gtf.write_text(gtf.read_text().replace(' transcript_id "t1";', ""))
    with pytest.raises(ValueError, match="transcript_id"):
        preflight_fastq_plan(plan)


def test_total_input_budget_is_checked_before_hashing_large_files(tmp_path, monkeypatch):
    plan = plan_fixture(tmp_path)
    monkeypatch.setattr(fastq_contract, "MAX_INPUT_BYTES", 1)
    monkeypatch.setattr(fastq_contract, "_file_identity", lambda *a, **k: pytest.fail("hash before budget"))
    with pytest.raises(ValueError, match="cumulative-byte"):
        preflight_fastq_plan(plan)


def test_csv_reads_are_bounded_even_if_file_grows_after_stat(tmp_path, monkeypatch):
    plan = plan_fixture(tmp_path)
    monkeypatch.setattr(Path, "read_bytes", lambda *a, **k: pytest.fail("unbounded read_bytes"))
    preflight_fastq_plan(plan)


def test_reference_mutation_between_identity_and_parse_refuses(tmp_path, monkeypatch):
    plan = plan_fixture(tmp_path)
    original = fastq_contract._validate_reference
    def changed(fasta, gtf):
        fasta.write_text(">chr1\nTTTTTTTT\n")
        return original(fasta, gtf)
    monkeypatch.setattr(fastq_contract, "_validate_reference", changed)
    with pytest.raises(ValueError, match="changed"):
        preflight_fastq_plan(plan)


def test_fastq_mutation_between_validation_and_hash_refuses(tmp_path, monkeypatch):
    plan = plan_fixture(tmp_path)
    original = fastq_contract._check_fastq_prefix
    def changed(path):
        original(path)
        path.write_text("@broken\nACGT\n+\nI\n")
    monkeypatch.setattr(fastq_contract, "_check_fastq_prefix", changed)
    with pytest.raises(ValueError, match="changed"):
        preflight_fastq_plan(plan)


def test_constant_batch_cannot_be_silently_removed_from_fitted_formula():
    selected = rows()
    for row in selected:
        row["batch"] = "constant"
    with pytest.raises(ValueError, match="constant|level"):
        validate_design(selected, **design_kwargs())


def test_fasta_sequence_requires_nucleotide_symbols(tmp_path):
    plan = plan_fixture(tmp_path)
    Path(plan["reference"]["fasta"]).write_text(">chr1\nZZZZZZZZ\n")
    with pytest.raises(ValueError, match="FASTA"):
        preflight_fastq_plan(plan)


@pytest.mark.parametrize("suffix", [".fq", ".fastq", ".fq.gz", ".fastq.gz"])
def test_all_pinned_workflow_fastq_suffixes_are_admitted(tmp_path, suffix):
    plan = plan_fixture(tmp_path)
    source = tmp_path.resolve() / "s0.fastq"
    target = tmp_path.resolve() / ("renamed" + suffix)
    data = source.read_bytes()
    target.write_bytes(gzip.compress(data, mtime=0) if suffix.endswith(".gz") else data)
    sheet = Path(plan["samplesheet"])
    sheet.write_text(sheet.read_text().replace(str(source), str(target)))
    assert preflight_fastq_plan(plan)["samples"][0]["fastq_1"] == str(target)


@pytest.mark.parametrize("suffix", [".txt", ".FASTA", ".FASTQ", ".fastq.bgz", ".fastq.zip"])
def test_fastq_suffix_must_match_pinned_upstream_schema(tmp_path, suffix):
    plan = plan_fixture(tmp_path)
    source = tmp_path.resolve() / "s0.fastq"
    target = tmp_path.resolve() / ("renamed" + suffix)
    source.rename(target)
    sheet = Path(plan["samplesheet"])
    sheet.write_text(sheet.read_text().replace(str(source), str(target)))
    with pytest.raises(ValueError, match="FASTQ.*suffix|FASTQ.*extension"):
        preflight_fastq_plan(plan)


@pytest.mark.parametrize("attribute,original", [("gene_id", "g1"), ("transcript_id", "t1")])
@pytest.mark.parametrize("identity", ["   ", "\u00a0"])
def test_gtf_exon_identity_cannot_be_quoted_whitespace(tmp_path, attribute, original, identity):
    plan = plan_fixture(tmp_path)
    gtf = Path(plan["reference"]["gtf"])
    gtf.write_text(gtf.read_text().replace(f'{attribute} "{original}";', f'{attribute} "{identity}";'))
    with pytest.raises(ValueError, match=attribute):
        preflight_fastq_plan(plan)


def test_gtf_nonempty_gene_and_transcript_ids_remain_admitted(tmp_path):
    plan = plan_fixture(tmp_path)
    gtf = Path(plan["reference"]["gtf"])
    gtf.write_text(gtf.read_text().replace('gene_id "g1";', 'gene_id "ENSG000001.2";')
                  .replace('transcript_id "t1";', 'transcript_id "transcript-1.2";'))
    assert preflight_fastq_plan(plan)["reference_audit"]["exon_count"] == 1
