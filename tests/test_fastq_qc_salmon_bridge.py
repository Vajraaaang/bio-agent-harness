"""Synthetic local FASTQ admission, fractional quantification and offset handoff."""

import csv
import json
from hashlib import sha256
from pathlib import Path

import pytest

from devbench.analysis import fastq_execution as execution
from devbench.analysis import fastq_planner_binding as binding


def write_table(path, rows, delimiter="\t"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        csv.writer(stream, delimiter=delimiter).writerows(rows)


def read_table(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.reader(stream, delimiter="\t"))


def quant(path, rows):
    write_table(path, [("Name", "Length", "EffectiveLength", "TPM", "NumReads"), *rows])


def tx2gene(root):
    path = root / "tx2gene.tsv"
    write_table(
        path, [("transcript_id", "gene_id"), ("t2", "g1"), ("t1", "g1"), ("t3", "g2")]
    )
    return {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest()}


def fixture_inputs(root):
    source = root / "inputs"
    source.mkdir()
    samples, metadata = [], []
    for index in range(8):
        sample = f"sample{index}"
        reads = source / f"{sample}.fastq"
        reads.write_text(f"@read{index}\nACGT\n+\nIIII\n")
        samples.append((sample, str(reads), "", "unstranded"))
        metadata.append(
            (sample, f"donor{index}", "A" if index < 4 else "B", str(index % 2))
        )
    sheet, design = source / "samples.csv", source / "metadata.csv"
    write_table(
        sheet,
        [("sample", "fastq_1", "fastq_2", "strandedness"), *samples],
        delimiter=",",
    )
    write_table(
        design, [("sample_id", "donor", "condition", "batch"), *metadata], delimiter=","
    )
    fasta, gtf = source / "reference.fa", source / "reference.gtf"
    fasta.write_text(">chr1\nACGTACGT\n")
    gtf.write_text(
        'chr1\tfixture\texon\t1\t8\t.\t+\t.\tgene_id "g1"; transcript_id "t1";\n'
    )
    plan = {
        "samplesheet": str(sheet),
        "metadata": str(design),
        "reference": {"fasta": str(fasta), "gtf": str(gtf)},
        "formula": "~ batch + condition",
        "contrast": {"factor": "condition", "numerator": "B", "denominator": "A"},
        "biological_unit": "donor",
        "biological_unit_type": "donor",
        "batch_columns": ["batch"],
        "analysis_scope": "bulk",
    }
    return plan, source, tx2gene(source)


def test_salmon_tximport_handoff_preserves_fractions_and_weighted_lengths(tmp_path):
    root = tmp_path / "quantification"
    quant(
        root / "salmon" / "a" / "quant.sf",
        [
            ("t1", 200, 120, 10, 1.5),
            ("t2", 250, 200, 30, 2.75),
            ("t3", 250, 150, 0, 0),
        ],
    )
    quant(
        root / "salmon" / "b" / "quant.sf",
        [
            ("t2", 250, 210, 0, 0),
            ("t1", 200, 110, 0, 0),
            ("t3", 250, 170, 0, 0),
        ],
    )
    result = execution.merge_salmon_gene_counts(
        root, ["a", "b"], tx2gene(tmp_path), tmp_path
    )
    count_rows = read_table(Path(result["count_matrix"]["path"]))
    lengths = read_table(Path(result["effective_lengths"]["path"]))
    assert count_rows[0] == lengths[0] == ["gene_id", "a", "b"]
    assert [row[0] for row in count_rows[1:]] == ["g1", "g2"]
    assert [float(value) for value in count_rows[1][1:]] == [4.25, 0.0]
    assert [float(value) for value in lengths[1][1:]] == pytest.approx([180, 180])
    assert [float(value) for value in lengths[2][1:]] == [160, 160]
    provenance = json.loads(Path(result["salmon_quant_provenance"]["path"]).read_text())
    assert (
        provenance["count_source"]
        == "Salmon_tximport_estimated_counts_with_length_offsets"
    )
    assert provenance["counts_from_abundance"] == "no"
    assert (
        provenance["tx2gene"]["sha256"]
        == sha256((tmp_path / "tx2gene.tsv").read_bytes()).hexdigest()
    )


@pytest.mark.parametrize(
    "mode",
    [
        "unmapped",
        "duplicate",
        "unexpected_sample",
        "missing_sample",
        "bad_digest",
        "linked_quant",
    ],
)
def test_incomplete_or_unpinned_salmon_quantification_does_not_publish(tmp_path, mode):
    root = tmp_path / "quant"
    path = root / "salmon" / "a" / "quant.sf"
    rows = [("t1", 200, 80, 1, 0.25)]
    if mode == "unmapped":
        rows.append(("unmapped", 100, 80, 1, 0.25))
    if mode == "duplicate":
        rows += rows
    quant(path, rows)
    mapping = tx2gene(tmp_path)
    if mode == "bad_digest":
        mapping["sha256"] = "0" * 64
    if mode == "unexpected_sample":
        quant(root / "salmon" / "other" / "quant.sf", rows)
    if mode == "linked_quant":
        external = tmp_path / "outside.sf"
        path.rename(external)
        path.symlink_to(external)
    with pytest.raises(ValueError):
        execution.merge_salmon_gene_counts(
            root, ["a", "b"] if mode == "missing_sample" else ["a"], mapping, tmp_path
        )
    assert not list(tmp_path.glob("salmon_*"))


def test_trace_counts_actual_observed_rows_and_ignores_partial_write(tmp_path):
    trace = tmp_path / "nextflow-trace.tsv"
    trace.write_text(
        "task_id\tprocess\ttag\tstatus\texit\trealtime\n"
        "1\tNFCORE_RNASEQ:FASTQC\ta\tCOMPLETED\t0\t1.2s\n"
        "2\tNFCORE_RNASEQ:SALMON_QUANT\ta\tFAILED\t1\t2s\n"
        "3\tNFCORE_RNASEQ:SALMON_QUANT\tb\tCACHE"
    )
    progress = execution.read_nextflow_trace(trace)
    assert (progress["completed"], progress["total"]) == (1, 2)
    assert progress["tasks"][1] == {
        "task_id": 2,
        "process": "NFCORE_RNASEQ:SALMON_QUANT",
        "tag": "a",
        "status": "FAILED",
        "exit": 1,
        "realtime": "2s",
    }
    assert execution.read_nextflow_trace(tmp_path / "absent.tsv")["total"] == 0
    link = tmp_path / "linked.tsv"
    link.symlink_to(trace)
    with pytest.raises(ValueError):
        execution.read_nextflow_trace(link)


def test_qc_salmon_runs_normal_entry_point_and_retains_artifacts(tmp_path, monkeypatch):
    plan, src, mapping = fixture_inputs(tmp_path)
    calls = []
    monkeypatch.setattr(
        execution, "_nextflow_identity", lambda *_a, **_k: {"path": "/bin/nextflow"}
    )
    monkeypatch.setattr(execution, "_runtime_profile", lambda: "docker")
    monkeypatch.setattr(
        execution,
        "_check_model_for_design",
        lambda *a: pytest.fail("STAR model entered"),
    )
    monkeypatch.setattr(
        execution,
        "merge_star_gene_counts",
        lambda *a: pytest.fail("STAR merge entered"),
    )

    def fake_engine(command, *, cwd, log_path, timeout_sec, on_started):
        calls.append(command)
        on_started(12345)
        params = json.loads((cwd / "params.json").read_text())
        assert "extra_star_align_args" not in params
        assert "--skip_alignment" in command
        assert command[command.index("--pseudo_aligner") + 1] == "salmon"
        assert command[command.index("-with-trace") + 1] == str(
            cwd / "nextflow-trace.tsv"
        )
        (cwd / "engine-runtime.json").write_text(
            json.dumps(
                {
                    "workflow": execution.WORKFLOW["name"],
                    "commit_id": execution.WORKFLOW["commit"],
                    "nextflow_version": execution.NEXTFLOW_VERSION,
                    "success": True,
                    "exit_status": 0,
                    "session_id": "11111111-2222-4333-8444-555555555555",
                }
            )
        )
        for index in range(8):
            quant(
                Path(params["outdir"]) / "salmon" / f"sample{index}" / "quant.sf",
                [("t1", 150, 100, 1, index + 0.25)],
            )
        return {"exit_code": 0, "timed_out": False}

    monkeypatch.setattr(execution, "_execute_command", fake_engine)
    summary = execution.run_fastq_plan(
        plan,
        tmp_path / "output",
        execution_profile="qc-salmon",
        input_root=src,
        tx2gene=mapping,
        quantification_only=True,
    )
    record = json.loads(Path(summary["result_manifest"]).read_text())
    assert summary["status"] == "completed", record
    assert len(calls) == 1
    assert set(record["artifacts"]) == {
        "count_matrix",
        "effective_lengths",
        "salmon_quant_provenance",
    }
    assert record["count_model_execution"] == "not_run"
    assert record["scientific_correctness"] is None


def test_manifest_revalidates_local_scope_and_map_before_execution(tmp_path):
    plan, src, mapping = fixture_inputs(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executable = tmp_path / "nextflow"
    executable.write_text("synthetic fixture")
    controls = {
        "resources": {"cpus": 2, "memory_gb": 6},
        "timeout_seconds": 60,
        "execution_profile": "qc-salmon",
        "input_root": str(src),
        "tx2gene": mapping,
        "mounted_image_id": None,
        "quantification_only": True,
        "nextflow_path": str(executable),
        "nextflow_sha256": sha256(executable.read_bytes()).hexdigest(),
    }
    manifest = binding.build_fastq_manifest(
        plan, workspace=workspace, execution=controls
    )
    assert manifest["preflight_outcome"]["status"] == "passed"
    assert any(
        item["kind"] == "tx2gene" and item["sha256"] == mapping["sha256"]
        for item in manifest["input_files"]
    )
    Path(mapping["path"]).write_text("changed mapped identity\n")
    with pytest.raises(ValueError, match="changed"):
        binding.validate_fastq_manifest(manifest)


def test_mounted_de_handoff_preserves_offsets_and_rejects_bad_probabilities(
    tmp_path, monkeypatch
):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from devbench.analysis.fastq_method_adapter import _verify_products
    from devbench.engine import mounted_kernel_oci
    from devharness_controlled.native_contracts import limma_voom_kernel_images
    from devharness_core.storage import ChunkedExpressionStore
    from rna_kernels.mounted_bulk import _fit_mounted_inputs, _request

    plan, source, mapping = fixture_inputs(tmp_path)
    frozen = execution.preflight_fastq_plan(plan, input_root=source)
    model = execution._check_salmon_model(
        frozen, sorted(limma_voom_kernel_images())[0], {"cpus": 2}
    )
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    for i, sample in enumerate(frozen["sample_ids"]):
        quant(
            workflow / "quant" / "salmon" / sample / "quant.sf",
            [
                ("t1", 250, 100 + i, 1, 2.25 + i),
                ("t3", 250, 150, 3, 0.125 + i),
            ],
        )
    quant_artifacts = execution.merge_salmon_gene_counts(
        workflow / "quant", frozen["sample_ids"], mapping, workflow
    )

    def intercept(matrix, cohort, request, _outputs, _primary, _runtime, lengths):
        assert request["measurement"] == "tximport_estimated_counts"
        assert matrix.sample_names == frozen["sample_ids"]
        assert cohort.design.shape[1] == frozen["design"]["rank"]
        assert cohort.contrast.tolist() == [0.0, 1.0, 0.0]
        assert next(matrix.iter_gene_chunks(2))[1][0, :2].tolist() == [2.25, 3.25]
        with ChunkedExpressionStore(lengths) as effective:
            assert next(effective.iter_gene_chunks(2))[1][0, :2].tolist() == [
                100.0,
                101.0,
            ]
        return ("stub",) * 7

    monkeypatch.setattr("rna_kernels.mounted_bulk._fit_general", intercept)
    bad_probability = False

    def fake_container(stage, *, request_sha, image_id, attempt_id, timeout, memory_mb):
        assert image_id == model["image_id"]
        inputs, outputs = stage / "inputs", stage / "outputs"
        request = _request(inputs, request_sha)
        _fit_mounted_inputs(
            inputs, outputs, request, ["g1", "g2"], True, "bioconductor"
        )
        parquet = outputs / "genes.parquet"
        pq.write_table(
            pa.table(
                {
                    "gene_id": ["g1", "g2"],
                    "effect": [1.25, None],
                    "p_value": [0.05, None],
                    "q_value": [0.04 if bad_probability else 0.1, None],
                    "estimate_status": ["estimated", "filtered"],
                    "missing_reason": [None, "low_count"],
                }
            ),
            parquet,
        )
        (outputs / "summary.json").write_text(
            json.dumps(
                {
                    "format": "devbench.mounted-bulk-summary/1",
                    "request_sha256": request_sha,
                    "genes_sha256": sha256(parquet.read_bytes()).hexdigest(),
                    "row_count": 2,
                    "measurement": "tximport_estimated_counts",
                    "solver": "edger_v4",
                    "test": "qlf",
                    "design_rank": 3,
                    "residual_df": 5,
                    "solver_version": model["version"],
                    "normalization": {"method": model["normalization"]},
                }
            )
        )
        return {"cleanup_confirmed": True}

    monkeypatch.setattr(mounted_kernel_oci, "run_mounted_container", fake_container)
    resources = {"memory_gb": 2}
    fit = execution._run_salmon_model(
        quant_artifacts,
        frozen,
        workflow / "count-model",
        model,
        deadline=execution.time.monotonic() + 60,
        resources=resources,
    )
    published = execution._publish(
        workflow, quant_artifacts["count_matrix"], fit, model, quant_artifacts
    )
    record = {
        "status": "completed",
        "software_acceptance": True,
        "count_model_execution": "verified",
        "count_source": execution.SALMON_COUNT_SOURCE,
        "formula": plan["formula"],
        "contrast": plan["contrast"],
        "artifacts": published,
    }
    manifest = {
        "frozen_preflight": frozen,
        "execution": {"execution_profile": "qc-salmon", "tx2gene": mapping},
    }
    assert _verify_products(record, manifest, workflow) == {
        key: Path(value["path"]) for key, value in published.items()
    }
    assert read_table(Path(fit["differential_expression"]["path"]))[1] == [
        "g1",
        "1.25",
        "0.05",
        "0.1",
        "estimated",
        "",
    ]
    bad_probability = True
    with pytest.raises(ValueError, match="probability"):
        execution._run_salmon_model(
            quant_artifacts,
            frozen,
            workflow / "failing-model",
            model,
            deadline=execution.time.monotonic() + 60,
            resources=resources,
        )


def test_external_sample_is_rejected_before_opening_its_bytes(tmp_path, monkeypatch):
    from devbench.analysis import fastq_contract

    plan, source, mapping = fixture_inputs(tmp_path)
    external = tmp_path / "outside.fastq"
    external.write_text("@outside\nACGT\n+\nIIII\n")
    sheet = Path(plan["samplesheet"])
    sheet.write_text(
        sheet.read_text().replace(str(source / "sample0.fastq"), str(external))
    )
    monkeypatch.setattr(
        fastq_contract,
        "_check_fastq_prefix",
        lambda *args: pytest.fail("read bytes opened outside admission"),
    )
    recorded = execution.run_fastq_plan(
        plan,
        tmp_path / "output",
        execution_profile="qc-salmon",
        input_root=source,
        tx2gene=mapping,
        quantification_only=True,
        preflight_only=True,
    )
    assert recorded["engine_launch_count"] == 0
    assert "outside the assigned local input root" in recorded["reason"]


def test_trace_caps_progress_to_observed_task_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(execution, "MAX_TRACE_TASKS", 1)
    trace = tmp_path / "nextflow-trace.tsv"
    trace.write_text(
        "task_id\tprocess\ttag\tstatus\texit\trealtime\n"
        "1\tFASTQC\ta\tCACHED\t-\t-\n"
        "2\tSALMON\tb\tCOMPLETED\t0\t1s\n"
    )
    progress = execution.read_nextflow_trace(trace)
    assert (progress["total"], progress["completed"], progress["truncated"]) == (
        1,
        1,
        True,
    )



def test_plugin_freezes_fastq_with_the_authoritative_input_root(
    workspace_tmp_path, monkeypatch
):
    from devbench.analysis import fastq_contract
    from devbench.platform.plugin_mcp import _freeze_fastq

    plan, source, mapping = fixture_inputs(workspace_tmp_path)
    manifest = source / "manifest.json"
    encoded = json.dumps(plan).encode()
    manifest.write_bytes(encoded)
    profiles = [
        {
            "role": "manifest",
            "source": str(manifest),
            "sha256": sha256(encoded).hexdigest(),
        }
    ]
    preflight = fastq_contract.preflight_fastq_plan

    def verify_scope(request, *, input_root=None):
        assert input_root == source
        return preflight(request, input_root=input_root)

    monkeypatch.setattr(fastq_contract, "preflight_fastq_plan", verify_scope)
    frozen = _freeze_fastq(source, profiles, {"tx2gene": mapping})
    assert len(frozen["inventory"]) == 13

    metadata = Path(plan["metadata"])
    with metadata.open(newline="", encoding="utf-8") as stream:
        header, *rows = csv.reader(stream)
    write_table(
        metadata,
        [(*header, "patient_id"), *[(*row, "synthetic") for row in rows]],
        delimiter=",",
    )
    assert _freeze_fastq(source, profiles, {"tx2gene": mapping}) == {"private": True}
