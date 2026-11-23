"""Synthetic evaluation entrypoints for the signed ERQL capsule."""
import json
import os
import subprocess
import sys
from contextlib import contextmanager
import http.client
import re
import time

import pytest

from devharness_controlled.contracts import canonical_json_bytes
from devharness_controlled.qualification import synthetic_approvals
from tests.test_erql_contracts import ROOT, prepare


@pytest.fixture
def tmp_path(workspace_tmp_path):
    return workspace_tmp_path


def _capsule(tmp_path, *, paired=False, with_review=True, reference=True):
    from devbench.core.publication_families import FamilyAllocation, FamilySpec
    from devharness_controlled.erql_contracts import bind_erql_families

    plan, request = prepare(tmp_path, reference=reference, add_filtered=True, paired=paired)
    primary = plan.allocation.family(plan.family_id).model_copy(update={"weight": None})
    sibling = FamilySpec(family_id="withheld-sibling", population="unavailable",
                         numerator=request["numerator"], denominator=request["denominator"])
    plan = bind_erql_families(plan, FamilyAllocation(families=(primary, sibling)), plan.family_id)
    approvals, signers = synthetic_approvals(plan)
    capsule = tmp_path / "capsule"
    capsule.mkdir(mode=0o700)
    for name, body in (
        ("plan", plan.model_dump(mode="json")), ("request", request),
        ("approvals", [approval.model_dump(mode="json") for approval in (approvals if with_review else approvals[:1])]),
        ("trust-registry", [signer.model_dump(mode="json") for signer in signers]),
    ):
        (capsule / f"{name}.json").write_bytes(canonical_json_bytes(body))
    return capsule, plan


def _run_cli(tmp_path, capsule, *, scope="synthetic-verification", allow=True):
    argv = [sys.executable, "-m", "devbench.platform.cli", "run-erql",
            "--capsule", str(capsule), "--output", str(tmp_path / "out"), "--scope", scope]
    if allow:
        argv.append("--allow-data-execution")
    return subprocess.run(argv, cwd=ROOT, env={**os.environ, "DEVBENCH_RUNS_ROOT": str(tmp_path / ".runs")},
                          capture_output=True, text=True, timeout=240)


def test_cli_primary_scope_is_rejected_before_accessing_capsule(tmp_path):
    result = _run_cli(tmp_path, tmp_path / "absent", scope="primary")
    assert result.returncode == 2
    assert "invalid choice" in result.stderr and "synthetic-verification" in result.stderr
    assert not (tmp_path / "out").exists()


def test_cli_requires_grant_and_reviewer_approval(tmp_path):
    result = _run_cli(tmp_path, tmp_path / "absent", allow=False)
    assert result.returncode == 2 and "requires --allow-data-execution" in result.stderr
    capsule, _ = _capsule(tmp_path, with_review=False, reference=False)
    result = _run_cli(tmp_path, capsule)
    assert result.returncode == 1 and "one author and one reviewer" in result.stderr
    assert not (tmp_path / "out").exists()


def _assert_candidate(result, plan, output, *, regime):
    from devharness_controlled.erql_contracts import IMAGE

    assert result["scope"] == "synthetic_verification" and result["publication_eligible"] is False
    assert result["status"] == "analysis_complete_pending_review"
    assert result["plan_sha256"] == plan.sha256() and result["profile"] == "ERQL-1.1"
    assert result["diagnostics"] == {"tested": 600, "filtered": 1}
    assert result["execution_code"]["format"] == "devbench.execution-code/1"
    assert re.fullmatch(r"[a-f0-9]{64}", result["audit_root"])
    families = result["families"]
    assert families["allocation_sha256"] == plan.allocation.sha256()
    ready, held = families["families"]
    assert ready["status"] == "ready" and ready["weight"] == "1/2"
    assert ready["counts"]["tested"] == 600 and ready["genes"][-1]["status"] == "filtered_all_zero"
    assert all("q_budget" not in row and "p" not in row for row in ready["genes"])
    raw = json.loads((output / "family-synthesis.json").read_text())["families"][0]
    for row in raw["genes"][:-1]:
        assert row["q_budget"] == pytest.approx(min(1, row["q_local"] * 2))
    publication = result["publication"]
    assert publication["format"] == "devbench.fixed-publication/1"
    assert publication["figure"]["renderer"] == "fixed_effect_points_v2"
    assert publication["interpretation"] is None
    assert held["status"] == "withheld" and held["unused_weight"] == "1/2"
    transport = json.loads((output / "transport.json").read_text())
    assert transport["created"]["Image"] == IMAGE and transport["cleanup"]["removed"]
    assert "/input/erql_1_1.R" in transport["created"]["Config"]["Cmd"]
    assert json.loads((output / "inputs/request.json").read_text())["regime"] == regime


docker = pytest.mark.skipif(os.environ.get("DEVHARN_TEST_ERQL") != "1", reason="requires pinned local Docker image")


@docker
def test_pinned_devbench_cli_executes_signed_capsule(tmp_path):
    capsule, plan = _capsule(tmp_path)
    result = _run_cli(tmp_path, capsule)
    assert result.returncode == 0, result.stderr[-3000:]
    _assert_candidate(json.loads(result.stdout), plan, tmp_path / "out", regime="S-I")


@contextmanager
def _workbench(tmp_path, capsule):
    inputs = tmp_path / "inputs"
    inputs.mkdir(exist_ok=True)
    log = tmp_path / "workbench.log"
    command = [sys.executable, "-m", "devbench.platform.workbench_ui", "--workspace", str(tmp_path / "browser"),
               "--input-root", str(inputs), "--enable-scientific", "--allow-data-execution",
               "--synthetic-erql-capsule", str(capsule), "--port", "0"]
    with log.open("w") as stream:
        process = subprocess.Popen(command, cwd=ROOT, env={**os.environ, "DEVBENCH_RUNS_ROOT": str(tmp_path / ".runs")},
                                   stdout=stream, stderr=subprocess.STDOUT)
        try:
            until = time.monotonic() + 30
            while time.monotonic() < until:
                match = re.search(r"Open http://127\.0\.0\.1:(\d+)/", log.read_text())
                if match:
                    yield int(match[1])
                    return
                if process.poll() is not None:
                    break
                time.sleep(.1)
            pytest.fail(log.read_text()[-3500:])
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=20)


def _http(port, path="/api/scientific/erql-capsule", *, post=False, origin=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Origin": origin} if origin else {}
    if post:
        headers["Content-Type"] = "application/json"
    try:
        conn.request("POST" if post else "GET", path, "{}" if post else None, headers)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


@docker
def test_pinned_workbench_executes_paired_capsule_and_reads_retained_family(tmp_path):
    capsule, plan = _capsule(tmp_path, paired=True)
    output = tmp_path / "browser/synthetic-erql" / plan.sha256()
    with _workbench(tmp_path, capsule) as port:
        assert b"synthetic-erql-run" in _http(port, "/")[1]
        assert json.loads(_http(port)[1])["status"] == "ready"
        assert _http(port, post=True, origin="https://foreign.invalid")[0] == 403
        assert not output.exists()
        origin = f"http://127.0.0.1:{port}"
        source = capsule / "request.json"
        request_bytes = source.read_bytes()
        try:
            source.write_bytes(b"{}")
            assert _http(port, post=True, origin=origin)[0] == 400
            assert not output.exists()
        finally:
            source.write_bytes(request_bytes)
        assert _http(port, post=True, origin=origin)[0] == 202
        until = time.monotonic() + 180
        while time.monotonic() < until:
            status, raw = _http(port)
            assert status == 200, raw[-1000:]
            result = json.loads(raw)
            if result["status"] != "running":
                break
            time.sleep(.1)
        _assert_candidate(result, plan, output, regime="S-P")
        endpoint = "/api/scientific/erql-capsule/publication?format="
        code, exported = _http(port, endpoint + "json")
        assert code == 200 and json.loads(exported) == result["publication"]
        code, exported = _http(port, endpoint + "csv")
        assert code == 200 and exported.splitlines()[0] == b"family_id,gene_id,status,effect"
        code, exported = _http(port, endpoint + "svg")
        assert code == 200 and exported.startswith(b"<svg") and b"q_budget" not in exported
        assert json.loads(_http(port, post=True, origin=origin)[1])["audit_root"] == result["audit_root"]
        family = output / "family-synthesis.json"
        evidence = family.read_bytes()
        try:
            family.write_text("{}")
            assert _http(port)[0] == 400
        finally:
            family.write_bytes(evidence)
        assert json.loads(_http(port)[1])["audit_root"] == result["audit_root"]
