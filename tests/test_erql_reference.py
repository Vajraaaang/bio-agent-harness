"""Opt-in same-image comparison with the immutable P0 direct-R fixtures."""
import csv
import hashlib
import json
import os
import subprocess
import sys

import pytest

from devharness_controlled.contracts import canonical_json_bytes
from devharness_controlled.erql_contracts import AUGMENTED, CANONICAL
from devharness_controlled.qualification import synthetic_approvals
from tests.test_erql_contracts import ROOT, SMOKE, prepare


@pytest.fixture
def tmp_path(workspace_tmp_path):
    """Use a fresh Docker bind source for each reference case."""
    return workspace_tmp_path


@pytest.mark.skipif(os.environ.get("DEVHARN_TEST_ERQL") != "1", reason="requires pinned local Docker image")
@pytest.mark.parametrize("profile,ref_name,ref_sha", [
    (CANONICAL, "parent-reference.csv", "8bb8d37cf4d31b72bc93f26222b5d3870205f06584ad92301161c4c61480a3f2"),
    (AUGMENTED, "diagnostic-chained-reference.csv", "f8e164077ab4ef71777a78255aa0b6c4ba0c9b162974c9fe4d2f982aa66dab40"),
])
def test_signed_cli_against_pinned_direct_r(tmp_path, profile, ref_name, ref_sha):
    from devbench.core.publication_families import FamilyAllocation, FamilySpec
    from devharness_controlled.erql_contracts import bind_erql_families

    # Comparison tolerances are fixture controls, separate from qualification.
    plan, request = prepare(tmp_path, reference=True, profile=profile, add_filtered=True)
    primary = plan.allocation.family(plan.family_id).model_copy(update={"weight": None})
    sibling = FamilySpec(family_id="withheld-sibling", population="unavailable-population",
                         numerator=request["numerator"], denominator=request["denominator"])
    allocation = FamilyAllocation(profile=profile, families=(primary, sibling))
    plan = bind_erql_families(plan, allocation, plan.family_id)
    approvals, signers = synthetic_approvals(plan)
    paths = {}
    for key, value in (
        ("plan", plan.model_dump(mode="json")), ("request", request),
        ("approvals", [item.model_dump(mode="json") for item in approvals]),
        ("trust-registry", [item.model_dump(mode="json") for item in signers]),
    ):
        path = tmp_path / (key + ".json")
        path.write_bytes(canonical_json_bytes(value))
        paths[key] = path
    output = tmp_path / "execution"
    args = [sys.executable, "-m", "devharness_controlled.cli", "execute-erql"]
    for key, path in {**paths, "output": output}.items():
        args += ["--" + key, str(path)]
    completed = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=240)
    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-1500:]
    receipt = json.loads(completed.stdout)
    assert receipt["profile"] == profile and receipt["audit_verification"]["valid"]
    assert receipt["family"] == {"tested": 600, "filtered": 1}
    data = json.loads((output / "native-output/result.json").read_text())
    family_data = json.loads((output / "family-synthesis.json").read_text())
    ready, withheld = family_data["families"]
    assert family_data["allocation_sha256"] == allocation.sha256()
    assert not family_data["publication_eligible"]
    assert ready["status"] == "ready" and ready["weight"] == "1/2" and ready["local_alpha"] == 0.025
    assert withheld["status"] == "withheld" and withheld["unused_weight"] == "1/2"
    assert ready["genes"][-1]["status"] == "filtered_all_zero"
    transport = json.loads((output / "transport.json").read_text())
    assert transport["cleanup"]["removed"] and transport["memory_limit_verified"]
    assert data["genes"][-1]["status"] == "filtered" and data["genes"][-1]["statistics"] is None
    path = SMOKE / ref_name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == ref_sha
    with path.open(newline="") as stream:
        expected = list(csv.DictReader(stream))
    if profile == CANONICAL:
        for gene, direct in zip(ready["genes"][:-1], expected, strict=True):
            q = float(direct["q_local_fixture_only"])
            assert gene["q_local"] == pytest.approx(q, abs=1e-14, rel=1e-10)
            assert gene["q_budget"] == pytest.approx(min(1, q * 2), abs=1e-14, rel=1e-10)
    quantities = {"logFC": "logFC", "logCPM": "logCPM", "F": "F", "p": "PValue"}
    native_vectors = {}
    if profile == CANONICAL:
        quantities.update(df_test="df_test", df_total="df_total")
        direct_path = SMOKE / "receipt.json"
        assert hashlib.sha256(direct_path.read_bytes()).hexdigest() == "3f7be29bb6a61daf34c4fc59fe1032cb3ffdfa4f41172d7a76f3cef641390072"
        direct_receipt = json.loads(direct_path.read_text())
        native_vectors = {name: direct_receipt[name] for name in ("df_prior", "df_residual_adj", "s2_post")}
        assert data["fit"]["top_proportion"] == pytest.approx(direct_receipt["parent_top_proportion"], rel=1e-12)
        assert data["fit"]["dispersion"]["state"] == [direct_receipt["parent_nb_dispersion"]["state"]]
        assert data["fit"]["dispersion"]["value"] == pytest.approx([direct_receipt["parent_nb_dispersion"]["value"]], rel=1e-12)
    max_abs = dict.fromkeys(quantities, 0.0)
    max_abs.update(dict.fromkeys(native_vectors, 0.0))
    for index, (actual, direct) in enumerate(zip(data["genes"][:-1], expected, strict=True)):
        assert actual["gene_id"] == direct.get("gene_id", direct.get(""))
        for key, column in quantities.items():
            observed = actual["statistics"][key]
            expected_value = float(direct[column])
            assert observed["state"] == "finite"
            absolute = 1e-15 if key == "p" else 1e-10
            assert observed["value"] == pytest.approx(expected_value, rel=1e-10, abs=absolute), (actual["gene_id"], key)
            max_abs[key] = max(max_abs[key], abs(observed["value"] - expected_value))
        for key, vector in native_vectors.items():
            expected_state, expected_value = vector["state"], vector["value"]
            if isinstance(expected_state, list):
                expected_state, expected_value = expected_state[index], expected_value[index]
            value = actual["statistics"][key]
            assert value["state"] == expected_state
            if expected_state == "finite":
                assert value["value"] == pytest.approx(expected_value, rel=1e-10, abs=1e-10)
                max_abs[key] = max(max_abs[key], abs(value["value"] - expected_value))
            else:
                assert value["value"] is None
    (tmp_path / "reference-comparison.json").write_text(json.dumps({
        "profile": profile, "reference_sha256": ref_sha, "max_absolute_deltas": max_abs,
        "feature_count": len(expected), "filtered_control": "passed",
        "root_hash": receipt["receipt"]["root_hash"],
    }, indent=2) + "\n")


@pytest.mark.skipif(os.environ.get("DEVHARN_TEST_ERQL") != "1", reason="requires pinned local Docker image")
def test_sparse_zero_group_against_fresh_direct_r(tmp_path):
    from devharness_controlled.erql_contracts import IMAGE
    from devharness_controlled.native_executor import execute_erql_plan
    from devharness_controlled.native_runtime import LocalDocker

    plan, request = prepare(tmp_path, reference=True, stress=True, add_filtered=True)
    source = tmp_path / "request.json"
    source.write_bytes(canonical_json_bytes(request))
    approvals, signers = synthetic_approvals(plan)
    output = tmp_path / "execution"
    receipt = execute_erql_plan(plan, approvals, signers, request_path=source, output_dir=output)
    assert receipt["status"] == "analysis_complete_pending_review", receipt
    actual = json.loads((output / "native-output/result.json").read_text())

    # Independent direct-R path. Use the same signed input without sourcing the worker.
    direct = tmp_path / "direct"
    direct.mkdir()
    script = direct / "stress-reference.R"
    script.write_text("""
        x <- jsonlite::fromJSON("/input/request.json")
        groups <- factor(x$groups)
        counts <- t(x$counts)
        rownames(counts) <- x$feature_ids
        y <- edgeR::DGEList(counts, group=groups)
        keep <- edgeR::filterByExpr(y, group=groups, min.count=10, min.total.count=15, large.n=10, min.prop=.7)
        y <- edgeR::normLibSizes(y[keep,,keep.lib.sizes=FALSE], method="TMM",
            refColumn=NULL, logratioTrim=.3, sumTrim=.05, doWeighting=TRUE, Acutoff=-1e10)
        fit <- edgeR::glmQLFit(y, design=x$design, robust=TRUE, legacy=FALSE, dispersion=NULL,
            abundance.trend=TRUE, prior.count=.125, winsor.tail.p=c(.05,.1), top.proportion=NULL)
        result <- edgeR::glmQLFTest(fit, contrast=x$contrast)
        result$table$df_total <- result$df.total
        write.csv(result$table, "/direct/reference.csv")
    """)
    runtime = LocalDocker(erql=True)
    runtime.isolate_client(tmp_path / "direct-client")
    completed = subprocess.run(runtime.command([
        "run", "--rm", "--pull=never", "--platform", "linux/amd64", "--network", "none",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
        "--memory", "2g", "--cpus", "2", "--pids-limit", "64",
        "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=67108864,mode=1777",
        "--user", f"{os.geteuid()}:{os.getegid()}",
        "--mount", f"type=bind,src={output / 'inputs'},dst=/input,readonly",
        "--mount", f"type=bind,src={direct},dst=/direct",
        "--entrypoint", "/usr/bin/timeout", IMAGE,
        "--signal=KILL", "120s", "Rscript", "--vanilla", "/direct/stress-reference.R",
    ]), env=runtime.env, capture_output=True, text=True, timeout=150)
    assert completed.returncode == 0, completed.stderr[-1500:]
    with (direct / "reference.csv").open(newline="") as stream:
        expected = {row[""]: row for row in csv.DictReader(stream)}
    observed = {row["gene_id"]: row for row in actual["genes"] if row["status"] == "tested"}
    assert observed.keys() == expected.keys()
    deltas = {}
    for key, field in (("p", "PValue"), ("F", "F"), ("df_total", "df_total"), ("logFC", "logFC")):
        deltas[key] = 0.0
        for gene, row in expected.items():
            value = observed[gene]["statistics"][key]["value"]
            reference = float(row[field])
            assert value == pytest.approx(reference, rel=1e-10, abs=1e-300 if key == "p" else 1e-10)
            deltas[key] = max(deltas[key], abs(value - reference))
    target = observed[request["feature_ids"][0]]
    assert target["support"] == {"denominator": 0, "numerator": 4}
    probability = target["statistics"]["p"]["value"]
    assert probability < 1e-8
    (tmp_path / "reference-comparison.json").write_text(json.dumps({
        "profile": CANONICAL, "max_absolute_deltas": deltas, "zero_group_p": probability,
        "tested": len(observed), "root_hash": receipt["receipt"]["root_hash"],
    }, indent=2) + "\n")
