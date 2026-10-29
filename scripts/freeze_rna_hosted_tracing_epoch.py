"""Freeze only declared software sources for a fresh hosted tracing epoch.

Preparation does not run providers, fixtures, or hosted transports. Historical
selection records supply names only; source is copied from the current checkout.
"""
import argparse
import base64
import importlib.util
import json
from pathlib import Path
import shutil

from scripts.freeze_rna_tracing_epoch import absolute, sha

WORKSPACE = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')

NEW_FILES = (
    "devbench/study/rna_hosted_trace_contract.py",
    "devbench/study/rna_langsmith_verify.py",
    "devbench/study/rna_hosted_qualification.py",
    "devbench/study/rna_hosted_fixtures.py",
    "scripts/run_rna_hosted_tracing_verification.py",
    "scripts/freeze_rna_hosted_tracing_epoch.py",
    "scripts/install_rna_hosted_tracing_epoch.py",
    "tests/support/rna_hosted_trace_fixtures.py",
    "devbench/study/rna_hosted_preflight.py",
    "devbench/analysis/rna_publication_evidence.py",
    "devbench/analysis/rna_publication_comparison.py",
    "devbench/analysis/rna_scientific_trial_contract.py",
    "devbench/analysis/rna_scientific_trial.py",
    "scripts/run_rna_scientific_trial.py",
)
HOSTED_TESTS = (
    "tests/test_rna_hosted_trace_contract.py",
    "tests/test_rna_langsmith_verify.py",
    "tests/test_rna_hosted_qualification.py",
    "tests/test_rna_hosted_trace_invariance.py",
    "tests/test_rna_hosted_exporter_isolation.py",
    "tests/test_rna_hosted_preflight.py",
    "tests/test_rna_publication_evidence.py",
    "tests/test_rna_publication_comparison.py",
    "tests/test_rna_scientific_trial_contract.py",
    "tests/test_rna_scientific_trial.py",
)
CHANGED_TESTS = (
    "tests/test_rna_hosted_preflight.py",
    "tests/test_rna_publication_evidence.py",
    "tests/test_rna_publication_comparison.py",
    "tests/test_rna_scientific_trial_contract.py",
    "tests/test_rna_scientific_trial.py",
    "tests/test_method_sources.py",
    "tests/test_rna_hosted_trace_invariance.py",
)
OPTIONAL_FILES = (
    "devbench/study/rna_hosted_exporter.py",
    "devbench/study/rna_langsmith_schema.py",
    "devbench/study/rna_hosted_connection_contract.py",
    "devbench/study/rna_hosted_connection.py",
    "devbench/study/rna_langsmith_binding.py",
    "devbench/study/rna_connection_prepare.py",
    "tests/support/rna_connection_fixtures.py",
    "scripts/prepare_langsmith_connection_test.py",
    "devbench/study/rna_keychain_native_helper.py",
    "devbench/study/rna_keychain_native_runtime.py",
    "tests/fixtures/langsmith_run_request_schema.json",
)
OPTIONAL_TESTS = ("tests/test_rna_langsmith_schema.py",)
WIRE_TESTS = ("tests/test_rna_provider_root_wire.py",)
READBACK_TESTS = ("tests/test_rna_readback_compatibility.py",)
READBACK_SELECTION_TESTS = READBACK_TESTS + (
    "tests/test_rna_langsmith_verify.py",
    "tests/test_rna_langsmith_binding.py",
    "tests/test_rna_langsmith_schema.py",
    "tests/test_rna_hosted_connection.py",
    "tests/test_rna_provider_root_wire.py",
    "tests/test_rna_langsmith_live.py",
)
NATIVE_TESTS = ("tests/test_rna_keychain_native.py",)
NATIVE_CREDENTIAL_TESTS = (
    "tests/test_rna_keychain_native.py",
    "tests/test_rna_hosted_exporter_isolation.py",
    "tests/test_rna_hosted_connection.py",
)
CONNECTION_TESTS = (
    "tests/test_rna_hosted_connection_contract.py",
    "tests/test_rna_hosted_connection.py",
    "tests/test_rna_langsmith_binding.py",
    "tests/test_langsmith_connection_prepare.py",
)
A2B_TESTS = (
    "tests/test_rna_langsmith_verify.py",
    "tests/test_rna_hosted_qualification.py",
    "tests/test_rna_hosted_exporter_isolation.py",
)
ROOT_WIRE_TESTS = A2B_TESTS + OPTIONAL_TESTS + CONNECTION_TESTS + WIRE_TESTS + (
    "tests/test_rna_trace_contract.py",
    "tests/test_rna_trace_projection.py",
    "tests/test_rna_langsmith_export.py",
    "tests/test_rna_langsmith_live.py",
)


def freeze(workspace: Path, report: Path, epoch_name: str) -> dict:
    workspace, report = absolute(workspace), absolute(report)
    if workspace != WORKSPACE:
        raise ValueError("Only the assigned workspace may be frozen")
    if not report.is_relative_to(workspace) or not epoch_name.isascii() or not epoch_name.replace("-", "").isalnum():
        raise ValueError("Invalid epoch destination")
    prior = workspace / "reports/rna-agent-readiness/2026-10-06-langsmith-tracing-implementation-v1"
    retained = prior / "source/rna-tracing-7/source-manifest.json"
    selections = prior / "selections/rna-tracing-7"
    focused_prior = json.loads((selections / "focused.json").read_bytes())
    broad_prior = json.loads((selections / "broad.json").read_bytes())
    provenance = focused_prior["prior_selection_sha256"]
    if broad_prior["prior_selection_sha256"] != provenance:
        raise ValueError("Historical selection provenance disagrees")
    names = set(json.loads(retained.read_bytes())["files"])
    # Rebuild this epoch's generated resource rather than copying the old ZIP.
    names.discard("devbench/analysis/assets/source-bundle.zip")
    names.update(NEW_FILES + HOSTED_TESTS)
    optional_tests = {name for name in OPTIONAL_TESTS if (workspace / name).is_file()}
    connection_tests = {name for name in CONNECTION_TESTS if (workspace / name).is_file()}
    native_tests = {name for name in NATIVE_TESTS if (workspace / name).is_file()}
    wire_tests = {name for name in WIRE_TESTS if (workspace / name).is_file()}
    readback_tests = {name for name in READBACK_TESTS if (workspace / name).is_file()}
    names.update(name for name in OPTIONAL_FILES if (workspace / name).is_file())
    names.update(optional_tests | connection_tests | native_tests | wire_tests | readback_tests)
    focused = sorted({row["path"] for row in focused_prior["tests"]} | set(HOSTED_TESTS) | optional_tests | connection_tests | native_tests | wire_tests | readback_tests)
    broad = sorted({row["path"] for row in broad_prior["tests"]} | set(HOSTED_TESTS) | optional_tests | connection_tests | native_tests | wire_tests | readback_tests)
    baseline = {}
    for name in sorted(names):
        source = absolute(workspace / name)
        if not source.is_relative_to(workspace) or not source.is_file():
            raise ValueError("Missing declared software source: " + name)
        baseline[name] = sha(source)
    epoch = report / "source" / epoch_name
    epoch.mkdir(parents=True, exist_ok=False)
    for name, expected in baseline.items():
        source, target = workspace / name, epoch / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if sha(source) != expected or sha(target) != expected:
            raise ValueError("Checkout changed during freeze: " + name)
    builder_path = epoch / "scripts/build_colab_notebook.py"
    spec = importlib.util.spec_from_file_location("_hosted_epoch_resource_builder", builder_path)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    resource = builder.build_source_bundle(epoch)
    asset_name = "devbench/analysis/assets/source-bundle.zip"
    with (epoch / asset_name).open("xb") as stream:
        stream.write(base64.b64decode(resource["base64"], validate=True))
    baseline[asset_name] = sha(epoch / asset_name)
    manifest = epoch / "source-manifest.json"
    manifest.write_text(json.dumps({"format": "devbench.rna-tracing-source/1", "files": baseline}, sort_keys=True, indent=2) + "\n")
    selection_root = report / "selections" / epoch_name
    selection_root.mkdir(parents=True, exist_ok=False)
    declared_selections = [("changed", sorted(CHANGED_TESTS)), ("focused", focused), ("broad", broad),
                           ("a2b", sorted(set(A2B_TESTS) | optional_tests))]
    if connection_tests == set(CONNECTION_TESTS) and optional_tests == set(OPTIONAL_TESTS):
        declared_selections.append(("connection", sorted(set(A2B_TESTS) | optional_tests | connection_tests)))
    if native_tests == set(NATIVE_TESTS) and set(NATIVE_CREDENTIAL_TESTS) <= names:
        declared_selections.append(("native-credential", sorted(NATIVE_CREDENTIAL_TESTS)))
    if wire_tests == set(WIRE_TESTS) and set(ROOT_WIRE_TESTS) <= names:
        declared_selections.append(("root-wire", sorted(ROOT_WIRE_TESTS)))
    if readback_tests == set(READBACK_TESTS) and set(READBACK_SELECTION_TESTS) <= names:
        declared_selections.append(("readback", sorted(READBACK_SELECTION_TESTS)))
    for kind, tests in declared_selections:
        value = {"format": "devbench.rna-hosted-tracing-selection/1", "scope": "software", "kind": kind,
                 "source_manifest_sha256": sha(manifest), "prior_selection_sha256": provenance,
                 "allowed_fixture_classes": ["synthetic", "local"],
                 "tests": [{"path": name, "sha256": baseline[name]} for name in tests]}
        with (selection_root / (kind + ".json")).open("x") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
    result = {"epoch": str(epoch), "source_manifest_sha256": sha(manifest), "files": len(baseline),
              "focused_modules": len(focused), "broad_modules": len(broad),
              "resource_sha256": baseline[asset_name], "resource_files": resource["files"],
              "checkout_files": {name: digest for name, digest in baseline.items() if name != asset_name}}
    with (report / ("freeze-" + epoch_name + ".json")).open("x") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return {key: value for key, value in result.items() if key != "checkout_files"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--epoch", required=True)
    args = parser.parse_args()
    print(json.dumps(freeze(args.workspace, args.report, args.epoch), sort_keys=True))


if __name__ == "__main__":
    main()
