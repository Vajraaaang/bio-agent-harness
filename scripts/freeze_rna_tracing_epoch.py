"""Freeze current software sources for isolated RNA tracing verification.

This is local software preparation; it does not execute scientific workloads.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

PUBLICATION_SCOPE_VERSION = "rna-publication/1"
LIVE_ROUTE_SCOPE_VERSION = "rna-live-route/1"
MEDIATED_ROUTE_SCOPE_VERSION = "rna-live-route/2"
MEDIATED_OBSERVABILITY_SCOPE_VERSION = "rna-live-route/3"
CONTEXT_OBSERVATION_SCOPE_VERSION = "rna-live-route/4"
SIGNEDIN_ROUTE_SCOPE_VERSION = "rna-signedin-route/1"
CONTEXT_READBACK_SCOPE_VERSION = "rna-context-readback/1"
APPROVAL_SETUP_SCOPE_VERSION = "rna-signedin-approval-setup/1"
PRIOR_SOURCE_MANIFEST_SHA256 = "ace3d0cd153c4e3fff7baad36308369b844c0cbebd01f7da48a687c5fec15870"
PRIOR_SELECTION_SHA256 = "f586143a784f6e35af3864b55f6b2f6bfef407e7817dc4017291b1317c2db0ed"
PUBLICATION_MODULES = frozenset("devbench/analysis/" + name + ".py" for name in (
    "rna_publication_profiles", "rna_publication_sources", "rna_reference_extraction",
    "rna_execution_inputs", "rna_comparison_outputs", "rna_publication_metrics",
    "rna_publication_report", "rna_publication_evidence", "rna_publication_comparison",
    "rna_scientific_trial", "rna_scientific_trial_contract", "rna_input_contract",
    "figure_artifacts", "scientific_review", "scientific_review_agent",
    "rna_trial_authority", "method_planner"))
PUBLICATION_SCRIPTS = frozenset({"scripts/run_rna_scientific_trial.py"})
PUBLICATION_OBSERVABILITY_SOURCES = frozenset("devbench/study/" + name + ".py" for name in (
    "rna_hosted_qualification", "rna_hosted_fixtures", "rna_hosted_exporter", "rna_hosted_preflight",
    "rna_hosted_connection", "rna_keychain_native_runtime", "rna_keychain_native_helper",
    "rna_langsmith_verify", "rna_langsmith_binding")) | frozenset({
    "tests/support/rna_hosted_trace_fixtures.py", "tests/test_rna_hosted_exporter_isolation.py"})
PUBLICATION_BASE_TESTS = frozenset("tests/" + name + ".py" for name in (
    "test_rna_publication_evidence", "test_rna_publication_comparison",
    "test_rna_scientific_trial", "test_rna_scientific_trial_contract",
    "test_execution_contract_repair", "test_figure_artifacts",
    "test_scientific_review", "test_scientific_review_agent", "test_completed_scientific_review"))
PUBLICATION_NEW_TESTS = frozenset("tests/" + name + ".py" for name in (
    "test_rna_publication_profiles", "test_rna_publication_sources", "test_rna_reference_extraction",
    "test_rna_execution_inputs", "test_rna_comparison_outputs", "test_rna_publication_metrics",
    "test_rna_publication_report", "test_rna_publication_figures", "test_rna_publication_verification",
    "test_rna_scientific_trial_v2", "test_rna_trial_authority", "test_rna_trial_dispatch_guard",
    "test_rna_publication_authority", "test_rna_publication_comparison_v2",
    "test_rna_assessment_workflow_binding", "test_rna_execution_route_qualification",
    "test_rna_execution_route_observability", "test_rna_hosted_qualification",
    "test_rna_hosted_trace_invariance", "test_rna_langsmith_verify"))
LIVE_ROUTE_SOURCES = frozenset({
    "devbench/study/rna_hosted_workflow_contract.py",
    "devbench/study/rna_hosted_workflow.py", "devbench/study/rna_hosted_tls.py",
    "devbench/study/rna_hosted_connection_contract.py",
    "devbench/study/rna_connection_prepare.py",
    "devbench/study/langsmith_outbox.py", "devbench/study/rna_langsmith_export.py",
    "devbench/study/rna_langsmith_live.py", "devbench/study/rna_langsmith_verify.py",
    "devbench/study/rna_hosted_exporter.py"})
LIVE_ROUTE_TESTS = frozenset("tests/" + name + ".py" for name in (
    "test_rna_hosted_tls", "test_rna_hosted_workflow_contract",
    "test_rna_hosted_workflow_projection", "test_rna_hosted_workflow",
    "test_rna_hosted_workflow_outbox",
    "test_rna_hosted_workflow_errors",
    "test_langsmith_connection_prepare",
    "test_rna_live_route_verification",
    "test_rna_hosted_connection", "test_rna_readback_compatibility",
    "test_rna_langsmith_binding", "test_rna_langsmith_schema",
    "test_rna_hosted_qualification", "test_rna_hosted_trace_invariance",
    "test_rna_langsmith_verify", "test_rna_hosted_exporter_isolation",
    "test_rna_hosted_trace_contract"))
MEDIATED_ROUTE_SOURCES = frozenset("devbench/analysis/" + name + ".py" for name in (
    "rna_mediated_backend", "rna_route_qualification", "method_planner",
    "workflow_operations", "rna_trial_authority", "rna_scientific_trial"))
MEDIATED_ROUTE_TESTS = frozenset({"tests/test_rna_mediated_backend.py", "tests/test_rna_route_qualification.py"})
MEDIATED_OBSERVABILITY_SOURCES = frozenset("devbench/analysis/" + name + ".py" for name in (
    "rna_mediated_observability", "rna_trace_receipts", "rna_trace_projection",
    "rna_trace_contract", "methods"))
MEDIATED_OBSERVABILITY_TESTS = frozenset({
    "tests/test_rna_mediated_observability.py", "tests/test_rna_mediated_capture_registration.py"})
CONTEXT_OBSERVATION_SOURCES = frozenset({"devbench/study/rna_hosted_context_contract.py"})

SIGNEDIN_ROUTE_SOURCES = frozenset({
    "devbench/analysis/codex_signedin.py", "devbench/analysis/codex_job_policy.py",
    "devbench/analysis/codex_job_trace.py", "scripts/prepare_codex_signedin_probe.py"})
SIGNEDIN_ROUTE_TESTS = frozenset("tests/" + name + ".py" for name in (
    "test_codex_signedin", "test_codex_job_budget", "test_rna_signedin_trial",
    "test_codex_job_trace", "test_codex_signedin_probe", "test_codex_signedin_hosted_profile"))


CONTEXT_READBACK_SOURCES = frozenset({
    "devbench/study/rna_hosted_context_readback_contract.py",
    "devbench/study/rna_hosted_context_readback.py"})

APPROVAL_SETUP_SOURCES = frozenset({
    "devbench/analysis/rna_approval_setup.py", "scripts/rna_approval_setup.py",
    "docs/rna-research-approval-setup.md"})



def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def absolute(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Absolute non-symlink path required")
    return path


SIGNEDIN_PRIOR_PINS = {
    "source/full-q12/source-manifest.json": "40c6cf8aa78190f14d6feb6d9b6ede89172615472544809e7621a69934bdb0e1",
    "selections/full-q12/focused.json": "652d2876d197a2c49402e78f2931a280edf9ba6e84f35d23766b7039aa6baa53",
    "selections/full-q12/broad.json": "293bf2d34a734983b6c3046fc7d594f3b5b7f1bb335f9c34b56c8c4d11ba8a0d",
    "selections/full-q12/hosted.json": "93661e6e9a94031e23218a4d606cbbf2f4fb9c0ff8f586238b641837fc358734",
}


def signedin_prior(workspace):
    """Retain exact q12 membership while freezing current, separately named bytes."""
    prior = workspace / "reports/rna-live-route-qualification/2026-10-18-v1"
    records = {}
    for relative, expected in SIGNEDIN_PRIOR_PINS.items():
        path = absolute(prior / relative)
        if path.stat().st_size > 4 * 1024 * 1024 or sha(path) != expected:
            raise ValueError("Sealed signed-in prior source or selection mismatch")
        records[relative] = json.loads(path.read_bytes())
    source = records["source/full-q12/source-manifest.json"]
    selections = {kind: {row["path"] for row in records["selections/full-q12/" + kind + ".json"]["tests"]}
                  for kind in ("focused", "broad", "hosted")}
    return set(source["files"]), selections


def freeze(workspace, report, epoch_name, *, scope="software", scope_version=None):
    if scope not in ("software", "publication-software"):
        raise ValueError("Unknown software verification scope")
    if (scope == "publication-software" and scope_version not in (PUBLICATION_SCOPE_VERSION, LIVE_ROUTE_SCOPE_VERSION, MEDIATED_ROUTE_SCOPE_VERSION, MEDIATED_OBSERVABILITY_SCOPE_VERSION, CONTEXT_OBSERVATION_SCOPE_VERSION, SIGNEDIN_ROUTE_SCOPE_VERSION, CONTEXT_READBACK_SCOPE_VERSION, APPROVAL_SETUP_SCOPE_VERSION)) or (scope == "software" and scope_version is not None):
        raise ValueError("Unsupported or missing publication scope version")
    workspace, report = absolute(workspace), absolute(report)
    if not report.is_relative_to(workspace) or not epoch_name.isascii() or not epoch_name.replace("-", "").isalnum():
        raise ValueError("Invalid epoch destination")
    prior = workspace / "reports/rna-agent-readiness/2026-10-04-independent-subagents-qualification"
    retained = prior / "qualified-3-source-manifest.json"
    selection_path = prior / "qualified-3-test-selection.json"
    if scope == "publication-software" and (sha(retained) != PRIOR_SOURCE_MANIFEST_SHA256 or sha(selection_path) != PRIOR_SELECTION_SHA256):
        raise ValueError("Sealed prior inventory or selection identity mismatch")
    prior_selection = json.loads(selection_path.read_bytes())
    names = set(json.loads(retained.read_bytes())["files"])
    for group in ("devbench/analysis", "devbench/study", "scripts", "tests"):
        for path in (workspace / group).glob("*rna*trac*.py"):
            names.add(str(path.relative_to(workspace)))
    for name in ("devbench/study/langsmith_outbox.py", "tests/test_langsmith_outbox.py",
                 "tests/test_rna_langsmith_export.py", "tests/test_rna_langsmith_live.py",
                 "devbench/study/rna_langsmith_export.py", "devbench/study/rna_langsmith_live.py",
                 "tests/support/rna_trace_fixture_paths.py", "tests/support/__init__.py",
                 "scripts/run_rna_tracing_verification.py", "scripts/build_source_resource.py"):
        if (workspace / name).is_file():
            names.add(name)
    # Only the already-declared bounded software selection is retained.
    broad = sorted(set(prior_selection["new_or_changed_tests"] + prior_selection["prior_96_tests"]))
    new_tests = sorted(name for name in names if name.startswith("tests/test_") and
                       ("rna_trace" in name or "rna_langsmith" in name or "langsmith_outbox" in name))
    focused = sorted(set(new_tests + ["tests/test_langsmith_export.py", "tests/test_rna_investigation_backend.py",
        "tests/test_rna_investigation_budget.py", "tests/test_rna_investigation_replay.py",
        "tests/test_langgraph_investigations.py", "tests/test_analysis_bundle.py",
        "tests/test_source_bundle_verification.py"]))
    if scope == "publication-software":
        publication_tests = PUBLICATION_BASE_TESTS | {name for name in PUBLICATION_NEW_TESTS if (workspace / name).is_file()}
        names.update(name for name in PUBLICATION_MODULES if (workspace / name).is_file())
        names.update(PUBLICATION_SCRIPTS)
        names.update(PUBLICATION_OBSERVABILITY_SOURCES)
        names.update(publication_tests)
        if scope_version in (LIVE_ROUTE_SCOPE_VERSION, MEDIATED_ROUTE_SCOPE_VERSION, MEDIATED_OBSERVABILITY_SCOPE_VERSION, CONTEXT_OBSERVATION_SCOPE_VERSION, SIGNEDIN_ROUTE_SCOPE_VERSION, CONTEXT_READBACK_SCOPE_VERSION, APPROVAL_SETUP_SCOPE_VERSION):
            names.update(LIVE_ROUTE_SOURCES)
            names.update(LIVE_ROUTE_TESTS)
            publication_tests |= LIVE_ROUTE_TESTS
            if scope_version in (MEDIATED_ROUTE_SCOPE_VERSION, MEDIATED_OBSERVABILITY_SCOPE_VERSION, CONTEXT_OBSERVATION_SCOPE_VERSION, SIGNEDIN_ROUTE_SCOPE_VERSION, CONTEXT_READBACK_SCOPE_VERSION, APPROVAL_SETUP_SCOPE_VERSION):
                names.update(MEDIATED_ROUTE_SOURCES)
                names.update(MEDIATED_ROUTE_TESTS)
                publication_tests |= MEDIATED_ROUTE_TESTS
            if scope_version in (MEDIATED_OBSERVABILITY_SCOPE_VERSION, CONTEXT_OBSERVATION_SCOPE_VERSION, SIGNEDIN_ROUTE_SCOPE_VERSION, CONTEXT_READBACK_SCOPE_VERSION, APPROVAL_SETUP_SCOPE_VERSION):
                names.update(MEDIATED_OBSERVABILITY_SOURCES)
                names.update(MEDIATED_OBSERVABILITY_TESTS)
                publication_tests |= MEDIATED_OBSERVABILITY_TESTS
            if scope_version in (CONTEXT_OBSERVATION_SCOPE_VERSION, SIGNEDIN_ROUTE_SCOPE_VERSION, CONTEXT_READBACK_SCOPE_VERSION, APPROVAL_SETUP_SCOPE_VERSION):
                names.update(CONTEXT_OBSERVATION_SOURCES)
        focused = sorted(set(focused) | publication_tests)
        broad = sorted(set(broad) | publication_tests)
    if scope_version in (SIGNEDIN_ROUTE_SCOPE_VERSION, CONTEXT_READBACK_SCOPE_VERSION, APPROVAL_SETUP_SCOPE_VERSION):
        # q12 is membership provenance only; current bytes receive a new identity.
        names, signedin_selections = signedin_prior(workspace)
        names.discard("devbench/analysis/assets/source-bundle.zip")
        names.update(SIGNEDIN_ROUTE_SOURCES | SIGNEDIN_ROUTE_TESTS)
        if scope_version in (CONTEXT_READBACK_SCOPE_VERSION, APPROVAL_SETUP_SCOPE_VERSION):
            names.update(CONTEXT_READBACK_SOURCES)
        if scope_version == APPROVAL_SETUP_SCOPE_VERSION:
            names.update(APPROVAL_SETUP_SOURCES)
        focused = sorted(signedin_selections["focused"] | SIGNEDIN_ROUTE_TESTS)
        broad = sorted(signedin_selections["broad"] | SIGNEDIN_ROUTE_TESTS)
        new_tests = []
    names.update(broad + focused)
    baseline = {}
    for name in sorted(names):
        path = absolute(workspace / name)
        if not path.is_relative_to(workspace) or not path.is_file():
            raise ValueError("Missing declared software file")
        baseline[name] = sha(path)
    epoch = report / "source" / epoch_name
    epoch.mkdir(parents=True, exist_ok=False)
    for name in sorted(names):
        source, target = workspace / name, epoch / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if sha(source) != baseline[name] or sha(target) != baseline[name]:
            raise ValueError("Source changed during freeze")
    # This is source-artifact construction, before the epoch is sealed or its
    # runtime is qualified. Only the same epoch's stdlib source builder runs;
    # no scientific module, source import hook, or older runtime is borrowed.
    if "scripts/build_source_resource.py" in names:
        builder_path = epoch / "scripts/build_colab_notebook.py"
        spec = importlib.util.spec_from_file_location("_rna_epoch_source_artifact_builder", builder_path)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        resource = builder.build_source_bundle(epoch)
        asset_name = "devbench/analysis/assets/source-bundle.zip"
        asset = epoch / asset_name
        with asset.open("xb") as stream:
            stream.write(base64.b64decode(resource["base64"], validate=True))
        baseline[asset_name] = sha(asset)
        names.add(asset_name)
        with (report / ("source-resource-" + epoch_name + ".json")).open("x") as stream:
            json.dump({"source_epoch": str(epoch), "builder": str(builder_path),
                "builder_sha256": sha(builder_path), "resource_sha256": baseline[asset_name],
                "bytes": resource["bytes"], "source_files": resource["files"],
                "qualification_performed": False}, stream, sort_keys=True, indent=2)
    manifest = epoch / "source-manifest.json"
    source_record = {"format": "devbench.rna-tracing-source/1", "files": baseline}
    if scope == "publication-software":
        source_record.update(format="devbench.rna-tracing-source/2", scope=scope, scope_version=scope_version)
    manifest.write_text(json.dumps(source_record, sort_keys=True, indent=2) + "\n")
    selections = report / "selections" / epoch_name
    selections.mkdir(parents=True, exist_ok=False)
    selections_to_write = [("focused", focused), ("broad", sorted(set(broad + new_tests)))]
    if scope_version in (LIVE_ROUTE_SCOPE_VERSION, MEDIATED_ROUTE_SCOPE_VERSION, MEDIATED_OBSERVABILITY_SCOPE_VERSION, CONTEXT_OBSERVATION_SCOPE_VERSION, SIGNEDIN_ROUTE_SCOPE_VERSION, CONTEXT_READBACK_SCOPE_VERSION, APPROVAL_SETUP_SCOPE_VERSION):
        live_tests = LIVE_ROUTE_TESTS
        if scope_version in (MEDIATED_ROUTE_SCOPE_VERSION, MEDIATED_OBSERVABILITY_SCOPE_VERSION, CONTEXT_OBSERVATION_SCOPE_VERSION, SIGNEDIN_ROUTE_SCOPE_VERSION, CONTEXT_READBACK_SCOPE_VERSION, APPROVAL_SETUP_SCOPE_VERSION):
            live_tests |= MEDIATED_ROUTE_TESTS
        if scope_version in (MEDIATED_OBSERVABILITY_SCOPE_VERSION, CONTEXT_OBSERVATION_SCOPE_VERSION, SIGNEDIN_ROUTE_SCOPE_VERSION, CONTEXT_READBACK_SCOPE_VERSION, APPROVAL_SETUP_SCOPE_VERSION):
            live_tests |= MEDIATED_OBSERVABILITY_TESTS
        if scope_version in (SIGNEDIN_ROUTE_SCOPE_VERSION, CONTEXT_READBACK_SCOPE_VERSION, APPROVAL_SETUP_SCOPE_VERSION):
            live_tests = signedin_selections["hosted"] | SIGNEDIN_ROUTE_TESTS
        selections_to_write.append(("hosted", sorted(live_tests | set(new_tests))))
    for kind, tests in selections_to_write:
        value = {"format": "devbench.rna-tracing-selection/1", "scope": "software", "kind": kind,
            "source_manifest_sha256": sha(manifest), "prior_selection_sha256": sha(selection_path),
            "allowed_fixture_classes": ["synthetic", "local"],
            "tests": [{"path": name, "sha256": baseline[name]} for name in tests]}
        if scope == "publication-software":
            value.update(format="devbench.rna-tracing-selection/2", scope=scope, scope_version=scope_version)
        (selections / (kind + ".json")).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    result = {"epoch": str(epoch), "source_manifest_sha256": sha(manifest), "files": len(names),
              "focused_modules": len(focused), "broad_modules": len(set(broad + new_tests)), "prior_modules": len(broad)}
    if scope_version in (LIVE_ROUTE_SCOPE_VERSION, MEDIATED_ROUTE_SCOPE_VERSION, MEDIATED_OBSERVABILITY_SCOPE_VERSION, CONTEXT_OBSERVATION_SCOPE_VERSION, SIGNEDIN_ROUTE_SCOPE_VERSION, CONTEXT_READBACK_SCOPE_VERSION, APPROVAL_SETUP_SCOPE_VERSION):
        result["hosted_modules"] = len(live_tests | set(new_tests))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--epoch", required=True)
    parser.add_argument("--scope", choices=("software", "publication-software"), default="software")
    parser.add_argument("--scope-version")
    args = parser.parse_args()
    print(json.dumps(freeze(args.workspace, args.report, args.epoch, scope=args.scope, scope_version=args.scope_version), sort_keys=True))


if __name__ == "__main__":
    main()
