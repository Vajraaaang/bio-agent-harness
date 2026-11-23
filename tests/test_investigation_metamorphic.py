"""Fresh public synthetic controls through current production methods.

These are engineering invariants, not independent F1-F7 or X8/X9 qualification.
No historical reference matrix, private answer, model, or provider is loaded.
"""
import hashlib
import json
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest


def digest(value):
    raw = value if isinstance(value, bytes) else str(value).encode()
    return hashlib.sha256(raw).hexdigest()


def _authority(root):
    from devbench.engine.workflow_operations import LEDGER_NAME, WorkflowOperationLedger
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    ledger = WorkflowOperationLedger(root / LEDGER_NAME, scientific=True)
    binding = {"kind": "binding", "record_id": "binding", **{key: digest("t23-" + key) for key in
        ("cohort_sha256", "design_sha256", "procedure_sha256", "family_allocation_sha256", "exposure_basis_sha256", "policy_sha256")}}
    ledger.scientific.commit("synthetic-study", expected_revision=0, record=binding)
    ledger.scientific.commit("synthetic-study", expected_revision=1, record={"kind": "grant", "record_id": "local-engineering-grant",
        "binding_id": "binding", "grant_sha256": digest("authorized bounded T23 synthetic engineering")})
    ledger.scientific.activate_execution("synthetic-study", expected_revision=2, binding_id="binding",
        grant_id="local-engineering-grant", max_jobs=12, authorize=lambda basis: {
            "authorized": True, "authorization_sha256": digest("T23 local engineering only"), "expires_at": time.time() + 600})
    return AdaptiveWorkflow(ledger, "synthetic-study")


def _fresh_erql(directory, counts, *, order=None, rename=False, reverse=False, feature_order=None):
    import anndata as ad
    import numpy as np
    import pandas as pd

    from devbench.analysis.methods import MethodService
    from devbench.analysis.rna_scrna_input_contract import (
        INVENTORY_BINDING_FORMAT,
        inspect_registered_scrna_input,
    )
    from devbench.core.contrast_design import DesignSpec
    from devbench.core.count_profiles import (
        CountComponent,
        CountObservation,
        ProfileKey,
    )
    from devharness_controlled.erql_contracts import prepare_erql

    directory.mkdir(mode=0o700)
    order = list(range(len(counts))) if order is None else order
    feature_order = list(range(counts.shape[1])) if feature_order is None else feature_order
    ids = [f"{'renamed' if rename else 'unit'}-{i:02}" for i in order]
    genes = [f"gene-{i:03}" for i in feature_order]
    groups = ["reference" if i < 4 else "treated" for i in order]
    matrix = counts[np.ix_(order, feature_order)]
    ann = ad.AnnData(np.log1p(matrix), obs=pd.DataFrame({"donor": ids, "condition": groups,
        "population": "synthetic-population"}, index=ids), var=pd.DataFrame(index=genes))
    ann.raw = ad.AnnData(matrix, obs=ann.obs.copy(), var=ann.var.copy())
    source = directory / "fresh-counts.h5ad"
    ann.write_h5ad(source)
    methods = MethodService(directory / "methods")
    artifact = methods.import_artifact(source, artifact_type="anndata", actor="t23-engineering")
    inspection = inspect_registered_scrna_input(methods, {"format": INVENTORY_BINDING_FORMAT,
        "anndata_artifact_id": artifact["artifact_id"], "declarations": {"assay": "scrna", "measurement": "umi_counts",
        "counts_layer": "raw/X", "capture_unit_key": "donor", "capture_units_confirmed": True,
        "count_provenance": "Fresh seeded integer synthetic counts", "annotation_provenance": "Fixture assigned population",
        "biological_independence": {"independent": True, "unit_column": "donor", "evidence": "Synthetic units, not real donors"}},
        "inherited_provenance": [{"path": "raw/X", "artifact_sha256": artifact["sha256"],
            "source": "Fresh synthetic generator", "representation": "original_counts"},
            {"path": "obs/population", "artifact_sha256": artifact["sha256"], "source": "Fixture assigned population",
             "label_origin": "independent_assignment"}]})
    keys = tuple(ProfileKey(unit, unit, group, "synthetic-population") for unit, group in zip(ids, groups))
    components = tuple(CountComponent(unit, unit, unit, "t23-synthetic-features", unit, 0, 1,
        digest(unit), "umi_counts") for unit in ids)
    observations = tuple(CountObservation(unit, key, unit, cell_id=unit) for unit, key in zip(ids, keys))
    spec = DesignSpec("S-I", "reference" if reverse else "treated", "treated" if reverse else "reference",
                      population="synthetic-population")
    return prepare_erql(methods, inspection, components=components, observations=observations, expected_profiles=keys,
        design_spec=spec, condition_column="condition", population_column="population", study_id="t23-fresh-synthetic",
        data_classification="synthetic", profile="ERQL-1.1")


@pytest.fixture(scope="module")
def erql_results(tmp_path_factory):
    if os.environ.get("DEVHARN_TEST_ERQL") != "1":
        pytest.skip("explicit pinned local ERQL opt-in required")
    import numpy as np

    from devharness_controlled.contracts import canonical_json_bytes
    from devharness_controlled.native_executor import execute_erql_plan
    from devharness_controlled.qualification import synthetic_approvals
    root = tmp_path_factory.mktemp("fresh-t23-erql")
    rng = np.random.default_rng(231114)
    means = rng.uniform(35, 200, 160)
    counts = rng.negative_binomial(15, 15 / (15 + means), size=(8, 160)).astype("int64")
    counts[4:, :25] *= 2
    results = {}
    for name, changes in (("baseline", {}), ("permuted", {"order": [7, 2, 4, 0, 6, 3, 1, 5], "rename": True,
        "feature_order": list(reversed(range(160)))}), ("reversed", {"reverse": True})):
        directory = root / name
        plan, request = _fresh_erql(directory, counts, **changes)
        source = directory / "request.json"
        source.write_bytes(canonical_json_bytes(request))
        approvals, signers = synthetic_approvals(plan)
        output = directory / "actual-erql"
        receipt = execute_erql_plan(plan, approvals, signers, request_path=source, output_dir=output)
        assert receipt["status"] == "analysis_complete_pending_review", receipt
        data = json.loads((output / "native-output/result.json").read_text())
        family = json.loads((output / "family-synthesis.json").read_text())["families"][0]
        transport = json.loads((output / "transport.json").read_text())
        assert transport["cleanup"]["removed"] and transport["memory_limit_verified"]
        results[name] = {"request": request, "path": directory, "receipt": receipt,
            "genes": {g["gene_id"]: g for g in data["genes"]}, "family": {g["gene_id"]: g for g in family["genes"]}}
    return root, results


def test_row_feature_permutation_and_id_renaming_preserve_actual_erql_statistics(erql_results):
    _, results = erql_results
    left, right = results["baseline"], results["permuted"]
    assert set(left["genes"]) == set(right["genes"])
    for gene in left["genes"]:
        a, b = left["genes"][gene], right["genes"][gene]
        assert a["status"] == b["status"]
        for quantity in ("logFC", "F", "p"):
            assert a["statistics"][quantity]["value"] == pytest.approx(b["statistics"][quantity]["value"], rel=1e-8, abs=1e-10)
        assert left["family"][gene]["q_local"] == pytest.approx(right["family"][gene]["q_local"], rel=1e-8, abs=1e-10)


def test_contrast_sign_reversal_preserves_actual_erql_p_and_q(erql_results):
    _, results = erql_results
    left, right = results["baseline"], results["reversed"]
    for gene in left["genes"]:
        a, b = left["genes"][gene]["statistics"], right["genes"][gene]["statistics"]
        assert a["logFC"]["value"] == pytest.approx(-b["logFC"]["value"], rel=1e-10, abs=1e-12)
        assert a["p"]["value"] == pytest.approx(b["p"]["value"], rel=1e-10, abs=1e-12)
        assert left["family"][gene]["q_local"] == pytest.approx(right["family"][gene]["q_local"], rel=1e-10, abs=1e-12)


def test_fresh_same_image_direct_r_matches_current_worker(erql_results):
    import csv
    import subprocess

    from devharness_controlled.erql_contracts import IMAGE
    from devharness_controlled.native_runtime import LocalDocker
    root, results = erql_results
    baseline = results["baseline"]
    direct = root / "fresh-direct-r"
    direct.mkdir(mode=0o700)
    script = direct / "reference.R"
    script.write_text('''x <- jsonlite::fromJSON("/input/request.json")
y <- edgeR::DGEList(t(x$counts), group=factor(x$groups))
rownames(y) <- x$feature_ids
keep <- edgeR::filterByExpr(y, group=factor(x$groups), min.count=10, min.total.count=15, large.n=10, min.prop=.7)
y <- edgeR::normLibSizes(y[keep,,keep.lib.sizes=FALSE], method="TMM", refColumn=NULL,
    logratioTrim=.3, sumTrim=.05, doWeighting=TRUE, Acutoff=-1e10)
fit <- edgeR::glmQLFit(y, design=x$design, robust=TRUE, legacy=FALSE, dispersion=NULL,
    abundance.trend=TRUE, prior.count=.125, winsor.tail.p=c(.05,.1), top.proportion=NULL)
test <- edgeR::glmQLFTest(fit, contrast=x$contrast)
table <- test$table
table$q_local <- p.adjust(table$PValue, method="BH")
write.csv(table, "/direct/reference.csv")
''')
    runtime = LocalDocker(erql=True)
    runtime.isolate_client(root / "direct-client")
    command = runtime.command(["run", "--rm", "--pull=never", "--platform", "linux/amd64", "--network", "none",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true", "--memory", "2g",
        "--cpus", "2", "--pids-limit", "64", "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=67108864,mode=1777",
        "--user", f"{os.geteuid()}:{os.getegid()}", "--mount", f"type=bind,src={baseline['path']},dst=/input,readonly",
        "--mount", f"type=bind,src={direct},dst=/direct", "--entrypoint", "/usr/bin/timeout", IMAGE,
        "--signal=KILL", "120s", "Rscript", "--vanilla", "/direct/reference.R"])
    completed = subprocess.run(command, env=runtime.env, capture_output=True, text=True, timeout=150)
    (direct / "stdout.log").write_text(completed.stdout)
    (direct / "stderr.log").write_text(completed.stderr)
    (direct / "invocation.json").write_text(json.dumps({"command": command, "returncode": completed.returncode,
        "script_sha256": digest(script.read_bytes()), "scope": "fresh_synthetic_engineering_only"}))
    assert completed.returncode == 0, completed.stderr[-1500:]
    with (direct / "reference.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len(baseline["genes"])
    for row in rows:
        gene = row[""]
        actual = baseline["genes"][gene]["statistics"]
        for key, reference in (("logFC", "logFC"), ("F", "F"), ("p", "PValue")):
            assert actual[key]["value"] == pytest.approx(float(row[reference]), rel=1e-10, abs=1e-12)
        assert baseline["family"][gene]["q_local"] == pytest.approx(float(row["q_local"]), rel=1e-10, abs=1e-12)


def test_duplicate_profiles_never_add_independent_donors():
    from devbench.core.contrast_design import DesignSpec, compile_contrast_design
    from devbench.core.count_profiles import CountProfile, CountProfiles, ProfileKey
    profiles = tuple(CountProfile(f"profile-{i}", ProfileKey(f"donor-{i}", f"specimen-{i}",
        "reference" if i < 4 else "treated", "population"), "observed", 1, 1, (20 + i, 50 + i),
        70 + 2 * i, (), (), None) for i in range(8))
    base = CountProfiles(("g1", "g2"), "reference", "umi_counts", (), profiles)
    spec = DesignSpec("S-I", "treated", "reference", population="population")
    clean = compile_contrast_design(base, spec)
    duplicates = tuple(replace(row, profile_id="copy-" + row.profile_id,
        key=replace(row.key, specimen_id="copy-" + row.key.specimen_id)) for row in profiles)
    changed = compile_contrast_design(replace(base, profiles=profiles + duplicates), spec)
    assert clean.unit_counts == changed.unit_counts == (("reference", 4), ("treated", 4))
    assert "repeated_specimens_or_duplicate_profiles" in changed.refusal_codes
    two_units = tuple(replace(row, key=replace(row.key, unit_id=f"{row.key.condition}-unit-{i % 2}"))
                      for i, row in enumerate(profiles))
    underpowered = compile_contrast_design(replace(base, profiles=two_units), spec)
    assert underpowered.unit_counts == (("reference", 2), ("treated", 2))
    assert "insufficient_independent_units_per_level" in underpowered.refusal_codes


@pytest.mark.parametrize("variant", ["complete_confounding", "partial_overlap", "missing_population"])
def test_f2_f4_real_design_controls_retain_confounding_and_missingness(variant):
    from devbench.core.contrast_design import DesignSpec, compile_contrast_design
    from devbench.core.count_profiles import CountProfile, CountProfiles, ProfileKey
    profiles = []
    for i in range(8):
        batch = ("b1" if i < 4 else "b2") if variant == "complete_confounding" else ("b1" if i % 3 else "b2")
        missing = variant == "missing_population" and i == 6
        profiles.append(CountProfile(f"p-{i}", ProfileKey(f"donor-{i}", f"specimen-{i}",
            "reference" if i < 4 else "treated", "population"), "absent" if missing else "observed",
            0 if missing else 1, 0 if missing else 1, None if missing else (30 + i, 60 + i),
            None if missing else 90 + 2 * i, (), (("batch", batch),), None))
    panel = CountProfiles(("g1", "g2"), "synthetic-reference", "umi_counts", (), tuple(profiles))
    result = compile_contrast_design(panel, DesignSpec("S-I", "treated", "reference", population="population", technical_factors=("batch",)))
    if variant == "partial_overlap":
        assert result.status == "ready" and result.estimable
    elif variant == "complete_confounding":
        assert result.status == "held" and result.estimable is False
        assert any(column.factor == "batch" for column in result.columns)
    else:
        assert result.status == "held" and "incomplete_target_coverage" in result.refusal_codes
        assert profiles[6].counts is None and profiles[6].status == "absent"


@pytest.mark.wal
def test_f3_copied_rna_labels_do_not_gain_primary_authority(tmp_path):
    from devbench.platform.adaptive_workflow import AdaptiveStep, ArtifactVersion
    flow = _authority(tmp_path)
    for name, origin in (("orthogonal", "independent"), ("rna", "rna_derived"), ("copied-rna", "rna_derived")):
        flow.register_artifact(ArtifactVersion(version_id=name, sha256=digest("same labels"), kind="membership_view",
            representation="membership_view", biological_unit="donor", unit_membership_sha256=digest("same units"), label_origin=origin))
        flow.insert(AdaptiveStep(step_id=name, branch_id=name, operation_id="test", profile={"profile_id": "t23-label-control", "version": 1,
            "operations": [{"operation_id": "test", "output_kinds": ["table"], "max_memory_mb": 256, "timeout_seconds": 30}],
            "input_representations": ["membership_view"], "biological_units": ["donor"], "permitted_claims": ["checked_observation"]},
            environment_sha256=digest("runtime"), parameters_sha256=digest("parameters"),
            inputs=({"port": {"name": "labels", "artifact_kind": "membership_view"}, "source_id": name},),
            outputs=({"name": "out", "artifact_kind": "table"},), primary=True, regime="P-P"))
    blocked = flow.status()["blocked"]
    assert "RNA_labels" in blocked["rna"] and blocked["copied-rna"] == blocked["rna"]
    # Clean labels pass the label gate; later procedure/review gates stay blocked.
    assert "qualified_procedure" in blocked["orthogonal"]


@pytest.mark.wal
def test_f5_actual_result_query_reordering_retains_full_family(erql_results, tmp_path):
    from copy import deepcopy

    from devbench.analysis.episode_evidence_queries import query_result
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
    from devbench.study.contracts import canonical_json_bytes
    _, actual = erql_results
    family = actual["baseline"]["family"]
    rows = [{"gene_id": name, "hypothesis_id": name, "estimate_status": "estimated",
        "values": [gene["statistics"]["logFC"]["value"]], "p_value": gene["statistics"]["p"]["value"],
        "q_value": family[name]["q_local"]} for name, gene in actual["baseline"]["genes"].items()]
    result = {"targets": [{"target_id": "fresh-family", "feature_ids": [r["gene_id"] for r in rows],
        "payload": {"gene_statistics": rows, "multiplicity_scope": [r["hypothesis_id"] for r in rows]}}]}
    flow = _authority(tmp_path)
    broker = EvidenceBroker(flow.authority)
    policy = AccessPolicy("t23-query", 1, allow_outcomes=True, allow_statistics=True)
    broker.register_policy(policy)
    broker.register_context("query", principal_id="scientist", lineage_id="synthetic-study", role="scientist", phase="readback", policy_sha256=policy.sha256())
    summaries = []
    for name, source in (("original", result), ("reordered", deepcopy(result))):
        if name == "reordered":
            source["targets"][0]["payload"]["gene_statistics"].reverse()
        broker.register_source(name, content_sha256=digest(canonical_json_bytes(source)), roots=(digest("actual ERQL family"),), classification="target_outcome")
        delivery = broker.delivery("query", name)
        summary = query_result(source, {"mode": "family_summary", "target_id": "fresh-family"}, evidence_delivery=delivery)
        summaries.append(summary["directional_summary"])
        page = query_result(source, {"sort_by": "p_value", "limit": 3}, evidence_delivery=delivery)
        assert page["matched_rows"] == len(rows) and len(page["rows"]) == 3
        assert page["query_receipt"]
    assert summaries[0]["counts"] == summaries[1]["counts"]
    assert summaries[0]["effects"] == summaries[1]["effects"]


@pytest.mark.wal
def test_f6_new_context_and_source_copy_cannot_erase_actual_exposure(tmp_path):
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
    flow = _authority(tmp_path)
    broker = EvidenceBroker(flow.authority)
    policy = AccessPolicy("exposure", 1, allow_outcomes=True, allow_statistics=True)
    broker.register_policy(policy)
    broker.register_context("initial", principal_id="scientist", lineage_id="synthetic-study", role="controller", phase="analysis", policy_sha256=policy.sha256())
    broker.register_source("result", content_sha256=digest("fresh synthetic exposed value"), roots=(digest("root"),),
        units=("unit",), classification="target_outcome")
    broker.delivery("initial", "result").release({"effect": 1.25})
    broker.register_context("renamed", principal_id="new-session", lineage_id="synthetic-study", role="challenger",
        phase="publication_initial", policy_sha256=policy.sha256(), parents=("initial",))
    broker.register_source("copied", content_sha256=digest("fresh synthetic exposed value"), roots=(digest("root"),),
        units=("unit",), classification="structural", parents=("result",))
    exposure = broker.exposure("renamed", "copied")
    assert exposure["classification"] == "target_outcome" and exposure["possible_reservations"] >= 1


def _register_oci(methods, directory, name, program):
    from devbench.engine.method_executor import EXPLORATORY_IMAGE
    directory.mkdir(mode=0o700)
    source, lock = directory / "source.py", directory / "environment.lock"
    source.write_text(program)
    lock.write_text("t23-fresh-synthetic-runtime=1\n")
    contract = {"format": "devharness.method/1", "method_id": "t23." + name, "name": name,
        "lifecycle": "custom", "implementation_trust": "unreviewed", "inputs": {"data": {"artifact_type": "json", "required": True}},
        "parameters": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
        "outputs": {"out": {"artifact_type": "json", "path": "/outputs/out.json", "required": True}},
        "implementation": {"kind": "oci", "source_sha256": digest(source.read_bytes()), "entrypoint": ["python", "/method/main.py"]},
        "environment": {"image": EXPLORATORY_IMAGE, "lock_sha256": digest(lock.read_bytes())},
        "execution": {"cpu": 1, "memory_mb": 256, "timeout_seconds": 10, "pids": 32, "max_output_bytes": 100000,
                      "network": "deny"},
        "validation": {"comparator": "json", "invariants": [], "counts_layer": "X", "qc_metrics": []},
        "provenance": {"origin": "fresh_t23_synthetic_engineering"}}
    return methods.register_method(contract, source=source, environment_lock=lock, actor="t23-engineering")


def _oci_step(name, record, source, *, immutable=False, basis=(), role="compute"):
    from devbench.core.protected_partitions import digest as structured_digest
    from devbench.engine.method_executor import EXPLORATORY_IMAGE_ID
    from devbench.platform.adaptive_workflow import AdaptiveStep
    return AdaptiveStep(step_id=name, branch_id=name, operation_id="transform", profile={
        "profile_id": "t23-synthetic-oci", "version": 1, "operations": [{"operation_id": "transform",
        "output_kinds": ["table"], "max_memory_mb": 256, "timeout_seconds": 30}], "input_representations": ["table"],
        "biological_units": ["donor"], "permitted_claims": ["checked_observation", "exploratory_finding"]},
        environment_sha256=structured_digest({**record["contract"]["environment"], "image_id": EXPLORATORY_IMAGE_ID}),
        parameters_sha256=structured_digest({}), oci_method_version_id=record["version_id"],
        inputs=({"port": {"name": "data", "artifact_kind": "table"}, "source_id": source,
                 "source_port": None if immutable else "out", "use": "claim" if role == "claim" else "compute"},),
        outputs=({"name": "out", "artifact_kind": "table"},), basis=basis, role=role)


@pytest.fixture
def oci_flow(tmp_path):
    if os.environ.get("DEVBENCH_T15_DOCKER") != "1":
        pytest.skip("explicit pinned local OCI opt-in required")
    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.engine.methods import MethodService
    from devbench.platform.adaptive_workflow import ArtifactVersion
    flow = _authority(tmp_path)
    methods = MethodService(tmp_path)
    worker = ExploratoryOCI(methods, flow.ledger, [{}])
    raw = b'{"values":[3,7,11,19],"units":["u1","u2","u3","u4"]}'
    root = ArtifactVersion(version_id="fresh-input", sha256=worker.training.put(raw), kind="table", representation="table",
        biological_unit="donor", unit_membership_sha256=digest("u1,u2,u3,u4"))
    flow.register_artifact(root)
    program = '''import json
from pathlib import Path
def main():
    value = json.loads(Path("/inputs/data").read_text())
    Path("/outputs/out.json").write_text(json.dumps(value, sort_keys=True))
if __name__ == "__main__":
    main()
'''
    record = _register_oci(methods, tmp_path / "method-copy", "copy", program)
    return flow, worker, record, tmp_path


def _run(flow, worker, name):
    outcome = flow.dispatch(name, worker, actor="t23-engineering")
    assert outcome["status"] == "succeeded", outcome
    assert outcome["receipt_sha256"]
    return outcome


@pytest.mark.wal
def test_donor_correction_invalidates_actual_oci_dependents(oci_flow):
    from devbench.engine.workflow_operations import OperationBlocked
    flow, worker, record, _ = oci_flow
    flow.record_basis("donors", "fact", digest("four originally declared synthetic units"))
    flow.insert(_oci_step("qc", record, "fresh-input", immutable=True))
    flow.insert(_oci_step("aggregate", record, "qc", basis=({"basis_id": "donors", "use": "compute"},)))
    flow.insert(_oci_step("downstream", record, "aggregate"))
    for name in ("qc", "aggregate", "downstream"):
        _run(flow, worker, name)
    qc = flow.current_use("qc", "out", purpose="cache")
    downstream = flow.current_use("downstream", "out", purpose="cache")
    flow.record_basis("donors", "fact", digest("two sample labels corrected to the same donor"))
    assert flow.current_use("qc", "out", purpose="cache") == qc
    for purpose in ("cache", "query", "export", "replay-current-use"):
        with pytest.raises(OperationBlocked):
            flow.current_use("downstream", "out", purpose=purpose)
    assert flow.history("downstream")[0]["outputs"]["out"]["sha256"] == downstream.sha256
    assert flow.ready() == ("aggregate",)


@pytest.mark.wal
def test_claim_only_basis_hold_preserves_actual_oci_computation(oci_flow):
    from devbench.engine.workflow_operations import OperationBlocked
    flow, worker, record, _ = oci_flow
    flow.record_basis("interpretation", "review_basis", digest("initial explanation"))
    flow.insert(_oci_step("compute", record, "fresh-input", immutable=True,
        basis=({"basis_id": "interpretation", "use": "claim"},)))
    flow.insert(_oci_step("claim", record, "compute", role="claim"))
    _run(flow, worker, "compute")
    _run(flow, worker, "claim")
    before = flow.current_use("compute", "out", purpose="cache")
    flow.record_basis("interpretation", "review_basis", digest("claim-only counterevidence"))
    assert flow.current_use("compute", "out", purpose="cache") == before
    with pytest.raises(OperationBlocked, match="claim"):
        flow.current_use("compute", "out", purpose="export", use="claim")
    assert flow.ready() == ("claim",)


@pytest.mark.wal
def test_failed_and_omitted_actual_sweep_members_do_not_improve_completeness(oci_flow):
    from devbench.engine.methods import MethodService
    from devbench.engine.workflow_operations import OperationConflict
    flow, worker, record, root = oci_flow
    failure = _register_oci(MethodService(root), root / "method-fail", "intentional-failure",
        'if __name__ == "__main__":\n    raise RuntimeError("fresh synthetic F7 failure")\n')
    flow.insert(_oci_step("clean", record, "fresh-input", immutable=True))
    flow.insert(_oci_step("failed", failure, "fresh-input", immutable=True))
    flow.declare_sweep("prospective-sweep", {"clean": None, "failed": None, "omitted": "explicit_engineering_budget_omission"})
    _run(flow, worker, "clean")
    outcome = flow.dispatch("failed", worker, actor="t23-engineering")
    assert outcome["status"] == "failed" and outcome["receipt_sha256"]
    before = flow.projection_snapshot()
    assert {r["step_id"]: r["status"] for r in before["sweep_members"]} == {"clean": "succeeded", "failed": "failed", "omitted": "omitted"}
    with pytest.raises(OperationConflict, match="immutable"):
        flow.declare_sweep("prospective-sweep", {"clean": None})
    assert flow.projection_snapshot()["sweep_members"] == before["sweep_members"]


@pytest.mark.wal
def test_identical_literature_bytes_preserve_evidence_identity(
    workspace_tmp_path, monkeypatch
):
    from devbench.analysis import literature_adapter as literature
    from devbench.analysis import method_sources
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
    from devbench.analysis.scientific_review_holds import (
        ReviewEntity,
        ReviewHoldLifecycle,
    )
    flow = _authority(workspace_tmp_path)
    broker = EvidenceBroker(flow.authority)
    policy = AccessPolicy("t23-literature", 1, allow_outcomes=True, allow_statistics=True)
    broker.register_policy(policy)
    broker.register_context("reader", principal_id="synthetic-reviewer", lineage_id="synthetic-study",
        role="challenger", phase="publication_initial", policy_sha256=policy.sha256())
    lifecycle = ReviewHoldLifecycle(flow.authority, "synthetic-study")
    lifecycle.register_entities((ReviewEntity(entity_id="claim", kind="claim"),))
    body = b'''<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>231114</PMID><Article>
<ArticleTitle>Fresh synthetic T23 control</ArticleTitle><Abstract><AbstractText>Fixture material only; no biological finding.</AbstractText></Abstract>
</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>'''
    calls = []
    def offline_transport(host, target, deadline, maximum, **kwargs):
        calls.append((host, target))
        return None, body, "application/xml"
    # Only the transport/capability is stubbed; all capture, extraction, custody,
    # accounting and disclosure logic is the real current LiteratureService.
    monkeypatch.setattr(method_sources, "_fetch_hop", offline_transport)
    monkeypatch.setattr(literature.LiteratureCapability, "verify", lambda self: {"version": "0.1.5", "client_sha256": literature.PINNED_CLIENTS})
    capability = literature.LiteratureCapability(
        plugin_root=str(workspace_tmp_path / "synthetic-plugin")
    )
    service = literature.LiteratureService(
        flow.authority, "synthetic-study", workspace_tmp_path / "literature",
        broker=broker, capability=capability,
        allow_network=True, data_scope="synthetic",
    )
    binding = literature.LiteratureClaimBinding(claim_id="claim", claim_sha256=digest("synthetic claim"),
        evidence_sha256=digest("synthetic evidence"), review_binding_sha256=digest("synthetic review"), obligation_id="literature",
        context=literature.BiologicalContext(organism="synthetic", tissue="synthetic", intervention="none", direction="unknown", uncertainty="unknown"))
    service.bind_claim(binding)
    request = literature.LiteratureRequest(operation="fetch", provider="pubmed", identifier="231114")
    first = service.execute(request, request_id="first", context_id="reader", binding=binding)
    second = service.execute(request, request_id="repeated", context_id="reader", binding=binding)
    assert first["status"] == second["status"] == "available"
    state = service.state()
    identities = {row["content_identity_sha256"] for row in state["snapshots"].values()}
    assert len(identities) == 1
    assert state["accounting"]["requests"] == 2 and len(calls) == 2
    # Re-exposure consumes another disclosure while remaining the same evidence.
    assert first["query_receipt"]["disclosure_reservation_id"] != second["query_receipt"]["disclosure_reservation_id"]
    reference = state["snapshots"][first["snapshot_id"]]
    grant = state["grant"]
    def resolve(path, expected, maximum):
        raw = Path(path).read_bytes()
        assert len(raw) <= maximum
        return raw
    literature.verify_frozen_snapshot(reference, resolve, limits=grant["limits"], implementation_sha256=grant["implementation_sha256"])
    def changed_response(path, expected, maximum):
        raw = resolve(path, expected, maximum)
        return raw.replace(b"Fixture material", b"Changed material") if raw.startswith(b"<Pubmed") else raw
    with pytest.raises(ValueError, match="original response changed"):
        literature.verify_frozen_snapshot(reference, changed_response, limits=grant["limits"], implementation_sha256=grant["implementation_sha256"])
