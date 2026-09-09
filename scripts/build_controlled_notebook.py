"""Build the synthetic controlled-workflow Colab notebook; no agents or login flow."""
import argparse
import inspect
import json
from pathlib import Path

from scripts.build_colab_notebook import allowed_bundle_path, build_source_bundle, extract_source_bundle, _read_source_file


def build_notebook(project):
    bundle = build_source_bundle(project)
    cells = []
    def cell(kind, source):
        record = {"cell_type": kind, "metadata": {}, "source": source.splitlines(keepends=True)}
        if kind == "code":
            record.update(execution_count=None, outputs=[])
        cells.append(record)

    cell("markdown", """# DevHarness 0.4.17 — controlled numerical verification

This notebook tests a signed fixed-design execution workflow using synthetic data
and ephemeral test keys. It does not qualify a clinical assay or authorize patient
use. No Codex/Claude/model calls, account credentials or private clinical inputs
are used. Google Drive is not mounted. The source bundle is allowlisted and hashed.

Run all cells on a CPU runtime. Setup installs dependencies into a separate Python
environment, then runs adversarial tests and real supervised Gaussian/NB analyses.
Reference comparisons use R when installed and otherwise report unavailable.
R NB comparison shares fitted dispersions/offsets and therefore does not validate
empirical Bayes shrinkage. Numerical tolerance is a software check, not a clinical limit.
Colab runtime duration and availability are not guaranteed; download evidence before
the ephemeral runtime ends. All clinical-release flags remain false.
Native plan/result and isolation-contract tests are included. Native edgeR/DESeq2
container execution requires a separately available local Docker daemon and pinned
image; this notebook does not install Docker or claim that native path runs in Colab.
Endpoint contrast/interval and supervision-race regression tests are included;
their fixtures are software checks, not native coverage measurements.
Governed authority, export, expiry and recovery regression tests are included.
Signed fixture keys do not establish authenticated human identities or clinical approval.
Prespecified reference-normalization contracts are tested; biological reference
stability and native interval calibration remain unqualified.
Local stdio MCP tests cover strict input validation and structured JSON responses.
These protocol checks make no model-service calls.
Evidence-reader regressions reject named pipes without waiting for a writer.
This does not impose a deadline on ordinary filesystem I/O.
Experimental Gaussian selection tests exercise the supervised API, SDK and real
local MCP protocol; the fixed-plan numerical demo remains a separate workflow.
These checks do not establish selection calibration under unequal variances.
Input-preservation tests check streaming byte/member limits and exclusive output
reservation. They do not establish ordinary-filesystem latency bounds or host isolation.

## 1. Source and isolated dependency environment
""")
    bootstrap = '''# %%
import os
for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "1"
import base64, hashlib, io, json, stat, subprocess, sys, tempfile, time, venv, zipfile
from pathlib import Path, PurePosixPath
from IPython.display import display, Markdown, HTML
BASE = Path(tempfile.mkdtemp(prefix="devharness-controlled-", dir="/content"))
'''
    bootstrap += inspect.getsource(allowed_bundle_path) + "\n" + inspect.getsource(_read_source_file) + "\n" + inspect.getsource(extract_source_bundle)
    bootstrap += f"\nSOURCE_BUNDLE_BASE64 = {bundle['base64']!r}\nSOURCE_BUNDLE_SHA256 = {bundle['sha256']!r}\n"
    bootstrap += '''PROJECT = extract_source_bundle(SOURCE_BUNDLE_BASE64, BASE / "source", SOURCE_BUNDLE_SHA256)
VENV = BASE / "venv"
venv.EnvBuilder(with_pip=False).create(VENV)
PYTHON = VENV / "bin" / "python"
setup_started = time.monotonic()
with (BASE / "setup.log").open("w") as log:
    install = subprocess.run([sys.executable, "-m", "pip", "--python", str(PYTHON), "install", "--quiet", "--disable-pip-version-check", "-e", str(PROJECT) + "[dev]"], stdout=log, stderr=subprocess.STDOUT, timeout=600)
print("Source SHA-256:", SOURCE_BUNDLE_SHA256)
print("Setup seconds:", round(time.monotonic() - setup_started, 2), "exit:", install.returncode)
if install.returncode:
    print((BASE / "setup.log").read_text()[-6000:])
install.check_returncode()
'''
    cell("code", bootstrap)
    cell("markdown", "## 2. Adversarial contracts and execution tests\nA failure stops measurement; the log and JUnit record remain in the runtime.\n")
    cell("code", '''# %%
tests = [PROJECT / "tests" / name for name in ("test_controlled_contracts.py", "test_controlled_execution.py", "test_controlled_verification.py", "test_gaussian_inference.py", "test_native_contracts.py", "test_native_results.py", "test_native_runtime.py", "test_native_execution.py", "test_native_lifecycle.py", "test_run_registry.py", "test_operated_native.py", "test_operated_cli.py", "test_native_recovery.py", "test_native_effects.py", "test_effect_coverage.py")]
tests += [PROJECT / "tests" / name for name in ("test_authority_contracts.py", "test_authority_store.py", "test_authority_cli.py", "test_governed_native_contracts.py", "test_governed_native_execution.py", "test_governed_dispatch_verification.py", "test_native_authority_launch.py", "test_ed25519_key_admission.py", "test_sqlite_schema_guards.py", "test_native_coverage_collection.py")]
tests += [PROJECT / "tests" / name for name in ("test_normalization_reference.py", "test_reference_native_contracts.py")]
tests += [PROJECT / "tests" / "test_mcp_protocol.py"]
tests += [PROJECT / "tests" / "test_temporal_evidence.py"]
tests += [PROJECT / "tests" / "test_compactor.py"]
tests += [PROJECT / "tests" / "test_evidence_file_types.py"]
tests += [PROJECT / "tests" / name for name in ("test_selection_mc.py", "test_selection_mc_api.py")]
tests += [PROJECT / "tests" / "test_bounded_artifacts.py"]
tests += [PROJECT / "tests" / "test_temporal_tasks.py"]
tests += [PROJECT / "tests" / name for name in ("test_audit_binding.py", "test_audit_cli.py", "test_audit_runner.py")]
tests += [PROJECT / "tests" / "test_source_bundle_verification.py"]
tests += [PROJECT / "tests" / "test_runtime_identity.py"]
tests += [PROJECT / "tests" / name for name in ("test_code_action_audit.py", "test_code_action_audit_limits.py", "test_clinical_audit_regressions.py")]
with (BASE / "tests.log").open("w") as log:
    checks = subprocess.run([str(PYTHON), "-m", "pytest", *map(str, tests), "-q", "--junitxml=" + str(BASE / "tests.xml")], cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT, timeout=300)
print((BASE / "tests.log").read_text()[-10000:])
checks.check_returncode()
''')
    cell("markdown", "## 3. Real fixed-plan execution and independent references\nTwo input types, each executed twice in fresh supervised workers.\n")
    cell("code", '''# %%
RUN = BASE / "verification"
with (BASE / "verification.log").open("w") as log:
    measurement = subprocess.run([str(PYTHON), "-m", "devharness_controlled.qualification", "--output", str(RUN)], cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT, timeout=300)
print("Verification process exit:", measurement.returncode)
if (RUN / "report.md").exists():
    display(Markdown((RUN / "report.md").read_text()))
else:
    print((BASE / "verification.log").read_text()[-6000:])
''')
    cell("markdown", "## 4. Verify each receipt and export retained evidence\nReceipt verification detects changes against the saved root. Keep that root in an independent trusted location for protection against a full store rewrite.\n")
    cell("code", '''# %%
import html, shutil
summary = json.loads((RUN / "summary.json").read_text())
verifications = []
for receipt in sorted(RUN.glob("*-*/receipt.json")):
    # Fixture trust is supplied explicitly. These keys are synthetic verification
    # keys, not independently enrolled clinical signers.
    proc = subprocess.run([str(PYTHON), "-m", "devharness_controlled.cli", "verify", "--manifest", str(receipt.parent / "audit" / "manifest.json"), "--receipt", str(receipt), "--trust-registry", str(receipt.parent / "trust-registry.json")], capture_output=True, text=True, timeout=30)
    record = json.loads(proc.stdout)
    verifications.append({"run": receipt.parent.name, **record})
print("AUDIT_VERIFICATION=" + json.dumps(verifications, sort_keys=True))
compact = {kind: {"status": case["outcome"]["status"], "repeat_identical": case.get("repeat_numerical_sha256_equal"), "projection_max_p_error": case.get("projection_max_p_error"), "r_reference": case.get("r_reference")} for kind, case in summary["cases"].items()}
print("NUMERICAL_COMPARISONS=" + json.dumps(compact, sort_keys=True))
source_verification = {"scope": "expected bundled source members after tests and numerical execution",
                       "bundle_sha256": SOURCE_BUNDLE_SHA256, "verified": False, "errors": []}
try:
    extract_source_bundle(SOURCE_BUNDLE_BASE64, PROJECT, SOURCE_BUNDLE_SHA256, verify_only=True)
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(SOURCE_BUNDLE_BASE64, validate=True))) as archive:
        source_verification["verified_member_sha256"] = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()}
    source_verification["verified"] = True
except Exception as error:
    source_verification["errors"].append(type(error).__name__ + ": " + str(error))
print("SOURCE_VERIFICATION=" + json.dumps({"verified": source_verification["verified"], "bundle_sha256": SOURCE_BUNDLE_SHA256, "verified_files": len(source_verification.get("verified_member_sha256", {})), "errors": source_verification["errors"]}, sort_keys=True))
EXPORT = BASE / "export"
EXPORT.mkdir()
for name in ("setup.log", "tests.log", "tests.xml", "verification.log"):
    shutil.copyfile(BASE / name, EXPORT / name)
shutil.copytree(RUN, EXPORT / "verification")
(EXPORT / "colab-source.sha256").write_text(SOURCE_BUNDLE_SHA256 + "\\n")
(EXPORT / "source-verification.json").write_text(json.dumps(source_verification, indent=2))
(EXPORT / "audits.json").write_text(json.dumps(verifications, indent=2))
(EXPORT / "devbench_results.json").write_text(json.dumps(summary, indent=2))
report_html = "<!doctype html><html lang='en'><meta charset='utf-8'><title>Controlled software verification</title><style>body{max-width:1000px;margin:3rem auto;font:16px/1.6 system-ui;padding:1rem}pre{white-space:pre-wrap}</style><body><h1>Controlled software verification</h1><pre>" + html.escape((RUN / "report.md").read_text()) + "</pre><h2>Independent comparisons</h2><pre>" + html.escape(json.dumps(compact, indent=2)) + "</pre></body></html>"
(EXPORT / "devbench_results.html").write_text(report_html)
inventory = {path.relative_to(EXPORT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(EXPORT.rglob("*")) if path.is_file()}
(EXPORT / "SHA256SUMS.json").write_text(json.dumps(inventory, indent=2))
ARCHIVE = Path(shutil.make_archive(str(BASE / "devharness-colab-evidence"), "zip", EXPORT))
print("EVIDENCE_ZIP_SHA256=" + hashlib.sha256(ARCHIVE.read_bytes()).hexdigest())
print("EVIDENCE_ZIP=" + str(ARCHIVE))
display(HTML(report_html))
print("COMPLETE_VERIFICATION_PASSED=" + str(measurement.returncode == 0 and source_verification["verified"] and len(verifications) == 4 and all(record["valid"] for record in verifications)))
# Keep the evidence downloadable even on a failed/incomplete numerical run.
''')
    cell("markdown", "## 5. Download the evidence archive\nContains synthetic inputs, public verification keys, source objects, exact results, logs and SHA-256 receipts. No private keys are saved.\n")
    cell("code", '''# %%
from google.colab import files
files.download(str(ARCHIVE))
''')
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python"}, "colab": {"name": "controlled_verification.ipynb"},
        "devharness_source_sha256": bundle["sha256"]}, "cells": cells}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    notebook = build_notebook(args.project)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(notebook, indent=1) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
