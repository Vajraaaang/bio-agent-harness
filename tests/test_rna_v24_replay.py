"""Portable synthetic replay: integrity, presentation and execution are distinct."""
import hashlib
import io
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from types import ModuleType

import pytest


@pytest.mark.parametrize("command", ["replay-erql", "replay-package"])
def test_replay_cli_is_closed_before_legacy_sweep(monkeypatch, command):
    from devbench.platform import cli

    sweep = ModuleType("experiments.run_sweep")
    def forbidden():
        pytest.fail("Replay reached the legacy benchmark sweep")
    sweep.main = forbidden
    monkeypatch.setitem(sys.modules, "experiments.run_sweep", sweep)
    monkeypatch.setattr(sys, "argv", ["devbench", command, "--help"])
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == 0


@pytest.mark.wal
def test_readonly_broker_requires_existing_disclosure_schema(workspace_tmp_path):
    from devbench.analysis.evidence_access import EvidenceBroker
    from devbench.engine.workflow_operations import WorkflowOperationLedger

    ledger = WorkflowOperationLedger(workspace_tmp_path / "authority.sqlite", scientific=True)
    readonly = WorkflowOperationLedger(ledger.path, readonly=True, inert=True)
    assert callable(getattr(EvidenceBroker, "open_readonly", None)), "Read-only audit admission is required"
    with pytest.raises(ValueError, match="disclosure"):
        EvidenceBroker.open_readonly(readonly.scientific)


def test_package_verification_requires_audit_entitlement_before_read(workspace_tmp_path):
    from devbench.analysis import bundles

    assert callable(getattr(bundles, "verify_replay_package", None)), "Portable inert verification is required"
    with pytest.raises(PermissionError, match="audit"):
        bundles.verify_replay_package(workspace_tmp_path / "absent.zip", "a" * 64)
    assert list(workspace_tmp_path.iterdir()) == []


def _broker(root):
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
    from devbench.engine.workflow_operations import WorkflowOperationLedger

    broker = EvidenceBroker(WorkflowOperationLedger(root / "authority.sqlite", scientific=True).scientific)
    broker.authority.commit("synthetic-replay", expected_revision=0, record={"kind": "binding", "record_id": "capture",
        **dict.fromkeys(("cohort_sha256", "design_sha256", "procedure_sha256", "family_allocation_sha256",
                        "exposure_basis_sha256", "policy_sha256"), "a" * 64)})
    policy = AccessPolicy("replay-readback", 1, allow_outcomes=True, allow_statistics=True)
    broker.register_policy(policy)
    for context, role in (("audit", "auditor"), ("input", "scientist")):
        broker.register_context(context, principal_id="synthetic-operator", lineage_id="synthetic-replay",
            role=role, phase="readback", policy_sha256=policy.sha256())
    return broker


def _cli(root, *args):
    base = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-P", "-m", "devbench.platform.cli", *map(str, args)],
        cwd=base, env={**os.environ, "DEVBENCH_RUNS_ROOT": str(root / ".runs")},
        capture_output=True, text=True, timeout=240)
    prefix = root / ("command-" + str(len(list(root.glob("command-*.json")))))
    prefix.with_suffix(".json").write_text(json.dumps({"args": list(map(str, args)), "code": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr}))
    return result


@pytest.fixture(scope="module")
def fixed_run(module_workspace_tmp_path):
    if os.environ.get("DEVHARN_TEST_ERQL") != "1":
        pytest.skip("Requires the local pinned ERQL image")
    from devbench.analysis.erql_qualification import _fixture
    from devbench.core.contrast_design import DesignSpec
    from devbench.core.publication_families import FamilyAllocation, FamilySpec
    from devharness_controlled.contracts import canonical_json_bytes
    from devharness_controlled.erql_contracts import bind_erql_families, prepare_erql
    from devharness_controlled.qualification import synthetic_approvals

    root = module_workspace_tmp_path
    source = root / "fresh-source"
    source.mkdir(mode=0o700)
    methods, inspection, components, observations, keys, _ = _fixture(source, {"seed": 227014}, {
        "study_id": "t22-fresh-synthetic", "rng_index": 0, "regime": "S-I", "scenario": "sparse_signal"})
    plan, request = prepare_erql(methods, inspection, components=components, observations=observations,
        expected_profiles=keys, design_spec=DesignSpec("S-I", "target", "reference", population="A"),
        condition_column="condition", population_column="population", study_id="t22-fresh-synthetic",
        data_classification="synthetic")
    primary = plan.allocation.family(plan.family_id).model_copy(update={"weight": None})
    sibling = FamilySpec(family_id="withheld-sibling", population="B", numerator="target", denominator="reference")
    plan = bind_erql_families(plan, FamilyAllocation(families=(primary, sibling)), plan.family_id)
    approvals, signers = synthetic_approvals(plan)
    capsule = root / "capsule"
    capsule.mkdir(mode=0o700)
    for name, value in (("plan", plan.model_dump(mode="json")), ("request", request),
            ("approvals", [x.model_dump(mode="json") for x in approvals]),
            ("trust-registry", [x.model_dump(mode="json") for x in signers])):
        (capsule / (name + ".json")).write_bytes(canonical_json_bytes(value))
    result = _cli(root, "run-erql", "--capsule", capsule, "--output", root / "execution",
                  "--scope", "synthetic-verification", "--allow-data-execution")
    assert result.returncode == 0, result.stderr[-2000:] + result.stdout[-2000:]
    return root, capsule, plan


@pytest.fixture(scope="module")
def portable(fixed_run):
    from devbench.analysis.scientific_publication import audit_replay_entitlement
    from devbench.platform.workflow_entrypoints import SyntheticERQLCapsule

    root, capsule, plan = fixed_run
    api = SyntheticERQLCapsule(capsule)
    assert callable(getattr(api, "describe_replay", None)), "Actual capsule package operation is required"
    broker = _broker(root)
    capture_sha = hashlib.sha256((root / "execution/audit/manifest.json").read_bytes()).hexdigest()
    broker.register_source("capture", content_sha256=capture_sha, roots=(plan.source_sha256,),
                           tags=("audit_replay",), classification="target_outcome")
    capture = audit_replay_entitlement(broker.delivery("audit", "capture"), capture_sha)
    descriptor = api.describe_replay(root / "execution", entitlement=capture, snapshot_root=root / ".runs")
    broker.register_source("archive", content_sha256=descriptor["archive_sha256"], roots=(plan.source_sha256, descriptor["root_sha256"]),
                           tags=("audit_replay",), classification="target_outcome")
    entitlement = audit_replay_entitlement(broker.delivery("audit", "archive"), descriptor["archive_sha256"])
    destination = root / "relocated.zip"
    exported = api.export_replay(root / "execution", destination, entitlement=entitlement,
                                 capture_entitlement=capture, snapshot_root=root / ".runs")
    assert exported == descriptor
    return root, capsule, plan, broker, entitlement, destination, descriptor


@pytest.mark.wal
def test_relocated_package_has_complete_bound_inventory_and_separate_input(portable, monkeypatch):
    from devbench.analysis import bundles
    from devbench.analysis.checkpoints import CheckpointStore
    from devbench.engine import execution_snapshot

    root, capsule, plan, broker, entitlement, package, descriptor = portable
    def forbidden(*args, **kwargs):
        pytest.fail("Read-only verification attempted execution or source materialization")
    monkeypatch.setattr(execution_snapshot, "launch", forbidden)
    monkeypatch.setattr(execution_snapshot, "materialize", forbidden)
    monkeypatch.setattr(CheckpointStore, "__init__", forbidden)
    hidden = root / "unavailable-original"
    capsule.rename(hidden)
    try:
        report = bundles.verify_replay_package(package, descriptor["root_sha256"], entitlement=entitlement)
    finally:
        hidden.rename(capsule)
    assert report["verified"] and report["current_use"] == "unavailable"
    assert report["source_data_verification"] == "incomplete_external_input_omitted"
    assert report["publication_eligible"] is False
    assert report["original_status"] == "analysis_complete_pending_review"
    assert report["numerical_stage_launches"] == report["model_sessions"] == 0
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("replay-manifest.json"))
        assert set(archive.namelist()) == set(manifest["members"]) | {"replay-manifest.json"}
        assert manifest["external_inputs"][0]["sha256"] == plan.request_sha256
        assert not any(hashlib.sha256(archive.read(name)).hexdigest() == plan.request_sha256 for name in archive.namelist())
        assert manifest["original_reviews"]["scientific_acceptance"] == "not_recorded"
        assert manifest["literature"]["status"] == "not_recorded"
        assert manifest["family_allocation_sha256"] == plan.allocation.sha256()


@pytest.mark.wal
def test_replay_presentation_is_historical_and_checks_renderer(
    portable, workspace_tmp_path, monkeypatch,
):
    from devbench.analysis import bundles, scientific_plot_revisions

    root, capsule, plan, broker, entitlement, package, descriptor = portable
    assert callable(getattr(bundles, "replay_package_presentation", None)), "Bound geometry replay is required"
    destination = workspace_tmp_path / "presentation"
    result = bundles.replay_package_presentation(
        package, descriptor["root_sha256"], destination, entitlement=entitlement,
    )
    assert result["operation_kind"] == "historical_presentation"
    assert result["publication_eligible"] is False and result["numerical_stage_launches"] == 0
    assert (destination / "figure.svg").read_bytes().startswith(b"<svg")
    monkeypatch.setattr(scientific_plot_revisions, "fixed_renderer_identity", lambda: {"changed": True})
    with pytest.raises(ValueError, match="renderer"):
        bundles.replay_package_presentation(
            package, descriptor["root_sha256"], workspace_tmp_path / "changed",
            entitlement=entitlement,
        )
    assert not (workspace_tmp_path / "changed").exists()


@pytest.mark.wal
def test_fixed_presentation_values_are_bound_to_original_family_evidence(portable, tmp_path, monkeypatch):
    from devbench.analysis import bundles
    from devbench.analysis.scientific_publication import _effect_figure
    from devharness_controlled import native_executor
    _, _, _, _, original_entitlement, package, descriptor = portable
    monkeypatch.setattr(native_executor, "execute_erql_plan", lambda *a, **k: pytest.fail("Presentation refit numerical data"))
    assert bundles.verify_replay_package(package, descriptor["root_sha256"], entitlement=original_entitlement)["verified"]
    original = bundles._replay_archive(package.read_bytes())
    members = dict(original)
    manifest = json.loads(members["replay-manifest.json"])
    publication = json.loads(members["presentation.json"])
    row = next(row for row in publication["table"] if row.get("effect") is not None)
    row["effect"] = 987654.25
    publication["figure"] = _effect_figure(publication["table"], publication["descriptor"]["source_sha256"])
    publication["publication_sha256"] = bundles._sha(bundles._canonical({k: v for k, v in publication.items() if k != "publication_sha256"}))
    members["presentation.json"] = bundles._canonical(publication)
    manifest["members"]["presentation.json"] = {"sha256": bundles._sha(members["presentation.json"]), "size_bytes": len(members["presentation.json"])}
    members["replay-manifest.json"] = bundles._canonical(manifest)
    assert {name for name in members if members[name] != original[name]} == {"presentation.json", "replay-manifest.json"}
    raw, root = bundles._zip_replay(members), bundles._sha(members["replay-manifest.json"])
    broker = _broker(tmp_path)
    audit = _entitle(broker, "changed", bundles._sha(raw), (root,))
    with pytest.raises(ValueError, match="presentation_evidence_mismatch"):
        bundles.verify_replay_package(raw, root, entitlement=audit)
    with pytest.raises(ValueError, match="presentation_evidence_mismatch"):
        bundles.replay_package_presentation(raw, root, tmp_path / "forged", entitlement=audit)
    assert not (tmp_path / "forged").exists()


@pytest.mark.wal
def test_readonly_broker_does_not_create_or_migrate(portable):
    from devbench.analysis.evidence_access import EvidenceBroker
    from devbench.engine.workflow_operations import (
        OperationBlocked,
        WorkflowOperationLedger,
    )
    root, capsule, plan, broker, entitlement, package, descriptor = portable
    ledger = WorkflowOperationLedger(broker.authority.ledger.path, readonly=True, inert=True)
    opened = EvidenceBroker.open_readonly(ledger.scientific)
    assert opened.exposure("audit", "archive")["classification"] == "target_outcome"
    with pytest.raises(OperationBlocked, match="readonly"):
        opened.register_source("forbidden", content_sha256="a" * 64, roots=("b" * 64,))


def _fingerprint(root):
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_mode,
             hashlib.sha256(p.read_bytes()).hexdigest()) for p in root.rglob("*") if p.is_file()}


@pytest.mark.wal
def test_inert_authority_reads_create_no_sidecars_and_reject_live_wal(tmp_path):
    import sqlite3

    from devbench.analysis.evidence_access import EvidenceBroker
    from devbench.engine.workflow_operations import (
        OperationConflict,
        WorkflowOperationLedger,
    )

    broker = _broker(tmp_path)
    path = broker.authority.ledger.path
    before = _fingerprint(tmp_path)
    readonly = WorkflowOperationLedger(path, readonly=True, inert=True)
    EvidenceBroker.open_readonly(readonly.scientific)
    assert _fingerprint(tmp_path) == before
    with pytest.raises(ValueError, match="readonly"):
        WorkflowOperationLedger(path, inert=True)
    with sqlite3.connect(path) as writer:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE t22_probe (value INTEGER)")
        writer.commit()
        assert path.with_name(path.name + "-wal").stat().st_size > 0
        with pytest.raises(OperationConflict, match="WAL|checkpoint"):
            WorkflowOperationLedger(path, readonly=True, inert=True)


@pytest.mark.wal
def test_inert_authority_detects_content_change_before_return(tmp_path):
    from devbench.engine.workflow_operations import (
        OperationConflict,
        WorkflowOperationLedger,
    )
    broker = _broker(tmp_path)
    readonly = WorkflowOperationLedger(broker.authority.ledger.path, readonly=True, inert=True)
    with pytest.raises(OperationConflict, match="changed|WAL|checkpoint"):
        with readonly._database():
            broker.register_source("changed", content_sha256="b" * 64, roots=("a" * 64,))


def _audit_args(broker):
    return ("--audit-ledger", broker.authority.ledger.path, "--audit-context", "audit", "--audit-source", "archive")


@pytest.mark.wal
def test_actual_cli_packages_and_verifies_without_execution(portable):
    root, capsule, plan, broker, entitlement, package, descriptor = portable
    result = _cli(root, "replay-erql", "describe", "--capsule", capsule, "--execution", root / "execution",
        "--audit-ledger", broker.authority.ledger.path, "--audit-context", "audit", "--audit-source", "capture")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == descriptor
    result = _cli(root, "replay-erql", "export", "--capsule", capsule, "--execution", root / "execution",
                  "--output", root / "cli-package.zip", "--capture-source", "capture", *_audit_args(broker))
    assert result.returncode == 0, result.stderr
    assert (root / "cli-package.zip").read_bytes() == package.read_bytes()
    before = _fingerprint(root / ".runs")
    result = _cli(root, "replay-erql", "verify", "--package", package,
                  "--expected-root", descriptor["root_sha256"], *_audit_args(broker))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["operation_kind"] == "evidence_verification"
    assert _fingerprint(root / ".runs") == before


def _grant(plan, descriptor, package):
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("replay-manifest.json"))
    from devbench.analysis.bundles import _canonical
    return {"format": "devbench.erql-replay-grant/1", "scope": "synthetic_numerical_replay",
        "grant_id": "t22-replay", "actor": "synthetic-operator", "package_root_sha256": descriptor["root_sha256"],
        "plan_sha256": plan.sha256(), "request_sha256": plan.request_sha256,
        "source_snapshot_sha256": manifest["source_binding"]["sha256"],
        "environment_sha256": hashlib.sha256(_canonical(manifest["environment"])).hexdigest(),
        "expires_at": time.time() + 300, "max_jobs": 1}


@pytest.mark.wal
def test_numerical_replay_requires_new_explicit_scope_before_launch(portable, tmp_path, monkeypatch):
    from devbench.engine import execution_snapshot
    from devbench.platform import workflow_entrypoints
    root, capsule, plan, broker, entitlement, package, descriptor = portable
    assert callable(getattr(workflow_entrypoints, "replay_fixed_erql", None)), "Authorized numerical replay is required"
    monkeypatch.setattr(execution_snapshot, "launch", lambda *a, **kw: pytest.fail("Early launch"))
    with pytest.raises(PermissionError, match="explicit"):
        workflow_entrypoints.replay_fixed_erql(package, descriptor["root_sha256"], tmp_path / "not-created",
            entitlement=entitlement, input_path=capsule / "request.json", input_delivery=broker.delivery("audit", "archive"),
            grant=_grant(plan, descriptor, package), execution_ledger=tmp_path / "replay.sqlite", allow_data_execution=False)
    assert not (tmp_path / "not-created").exists()


@pytest.mark.wal
def test_actual_cli_numerical_replay_reserves_separate_authority_and_matches(portable):
    root, capsule, plan, broker, entitlement, package, descriptor = portable
    broker.register_source("request", content_sha256=plan.request_sha256, roots=(plan.source_sha256,),
        tags=("original_source_data", "replay_input"), classification="target_outcome")
    grant_path = root / "replay-grant.json"
    grant_path.write_text(json.dumps(_grant(plan, descriptor, package)))
    result = _cli(root, "replay-erql", "numerical", "--package", package, "--expected-root", descriptor["root_sha256"],
        "--input", capsule / "request.json", "--input-context", "input", "--input-source", "request",
        "--grant", grant_path, "--execution-ledger", root / "numeric-authority.sqlite", "--output", root / "numeric-replay",
        "--scope", "synthetic_numerical_replay", "--allow-data-execution", *_audit_args(broker))
    assert result.returncode == 0, result.stderr + result.stdout[-3000:]
    replay = json.loads(result.stdout)
    assert replay["operation_kind"] == "authorized_numerical_replay"
    assert replay["numerical_match"] is True and replay["execution_acceptance"] == "accepted"
    assert replay["scientific_acceptance"] == "not_granted" and replay["publication_eligible"] is False
    assert replay["numerical_stage_launches"] == 1 and replay["model_sessions"] == 0
    transport = json.loads((root / "numeric-replay/execution/transport.json").read_bytes())
    assert transport["launch_authority"]["scope"] == "synthetic_numerical_replay"
    assert transport["cleanup"]["removed"] is True
    assert transport["created"]["Image"] == plan.runtime_image_id


def _rewritten(portable, tmp_path, change):
    from devbench.analysis import bundles
    from devbench.analysis.scientific_publication import AuditReplayEntitlement
    _, _, plan, _, _, package, _ = portable
    members = bundles._replay_archive(package.read_bytes())
    manifest = json.loads(members["replay-manifest.json"])
    change(members, manifest)
    members["replay-manifest.json"] = bundles._canonical(manifest)
    raw = bundles._zip_replay(members)
    broker = _broker(tmp_path)
    broker.register_source("changed-package", content_sha256=bundles._sha(raw),
                           roots=(plan.source_sha256, bundles._sha(members["replay-manifest.json"])),
                           tags=("audit_replay",), classification="target_outcome")
    return raw, bundles._sha(members["replay-manifest.json"]), AuditReplayEntitlement(
        broker.delivery("audit", "changed-package"), bundles._sha(raw))


@pytest.mark.wal
def test_numerical_comparison_bounds_cannot_be_redefined_by_package(portable, tmp_path):
    from devbench.analysis import bundles
    raw, root, entitlement = _rewritten(portable, tmp_path,
        lambda members, manifest: manifest["numerical_comparison"].update(absolute_tolerance=1))
    with pytest.raises(ValueError, match="comparison"):
        bundles.verify_replay_package(raw, root, entitlement=entitlement)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "a//b", "a/./b", "a\\b", "a:b"])
def test_closed_archive_rejects_unsafe_logical_names(name):
    from devbench.analysis import bundles
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(name, b"cannot execute")
    with pytest.raises(ValueError, match="unsafe"):
        bundles._replay_archive(stream.getvalue())


def test_closed_archive_rejects_duplicate_link_and_expansion_bound(monkeypatch):
    import stat

    from devbench.analysis import bundles
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("same", b"1")
        with pytest.warns(UserWarning):
            archive.writestr("same", b"2")
    with pytest.raises(ValueError, match="unsafe"):
        bundles._replay_archive(stream.getvalue())
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        entry = zipfile.ZipInfo("link")
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(entry, b"outside")
    with pytest.raises(ValueError, match="unsafe"):
        bundles._replay_archive(stream.getvalue())
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("oversize", b"x" * 4096)
    monkeypatch.setattr(bundles, "REPLAY_MAX_EXPANDED", 1024)
    with pytest.raises(ValueError, match="bound"):
        bundles._replay_archive(stream.getvalue())


@pytest.fixture
def legacy_archive(tmp_path):
    from devbench.analysis import bundles

    root = tmp_path / "legacy"
    root.mkdir()
    (root / "parameters.json").write_text(json.dumps({
        "execution_tier": "local_supervised", "data_sha256": "a" * 64,
    }))
    (root / "source.zip").write_bytes(b"synthetic source")
    expected_root = bundles.seal_bundle(root, status="completed")["manifest_sha256"]
    archive = tmp_path / "legacy.zip"
    bundles.pack_bundle(root, archive, expected_root)
    return archive, expected_root


def test_legacy_import_reads_archive_receipts_once(legacy_archive, tmp_path, monkeypatch):
    from devbench.analysis import bundles

    archive, expected_root = legacy_archive
    original_open = zipfile.ZipFile.open
    opened = []

    def track(archive_file, member, *args, **kwargs):
        opened.append(member.filename if isinstance(member, zipfile.ZipInfo) else member)
        return original_open(archive_file, member, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", track)
    assert bundles.import_bundle(archive, tmp_path / "imported", expected_root)["verified"]
    assert opened.count("receipts.json") == 1


def test_legacy_import_bounds_receipts_before_extraction(legacy_archive, tmp_path, monkeypatch):
    from devbench.analysis import bundles

    archive, expected_root = legacy_archive
    with zipfile.ZipFile(archive) as stream:
        manifest_size = stream.getinfo("receipts.json").file_size
    monkeypatch.setattr(bundles, "_MAX_BUNDLE_RECEIPTS_BYTES", manifest_size - 1, raising=False)
    output = tmp_path / "imported"
    with pytest.raises(ValueError, match="bundle_archive_exceeds_limits"):
        bundles.import_bundle(archive, output, expected_root)
    assert not output.exists()


def test_legacy_import_bounds_observed_cumulative_bytes(legacy_archive, tmp_path, monkeypatch):
    from devbench.analysis import bundles

    archive, expected_root = legacy_archive
    with zipfile.ZipFile(archive) as stream:
        declared_total = sum(entry.file_size for entry in stream.infolist())
    original_open = zipfile.ZipFile.open

    def extra_byte(archive_file, member, *args, **kwargs):
        with original_open(archive_file, member, *args, **kwargs) as source:
            data = source.read()
        name = member.filename if isinstance(member, zipfile.ZipInfo) else member
        return io.BytesIO(data + (b"x" if name == "source.zip" else b""))

    monkeypatch.setattr(zipfile.ZipFile, "open", extra_byte)
    monkeypatch.setattr(bundles, "_MAX_BUNDLE_EXPANDED_BYTES", declared_total, raising=False)
    output = tmp_path / "imported"
    with pytest.raises(ValueError, match="bundle_archive_exceeds_limits"):
        bundles.import_bundle(archive, output, expected_root)
    assert not output.exists()


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}'])
def test_replay_json_rejects_ambiguity_and_nonfinite(raw):
    from devbench.analysis.bundles import _document
    with pytest.raises(ValueError):
        _document(raw)


@pytest.mark.wal
def test_unrecorded_member_and_changed_source_fail_before_any_output(portable, tmp_path):
    from devbench.analysis import bundles
    raw, root, entitlement = _rewritten(portable, tmp_path,
        lambda members, manifest: members.update({"unrecorded.py": b"raise RuntimeError('never execute')"}))
    before = _fingerprint(tmp_path)
    with pytest.raises(ValueError, match="inventory"):
        bundles.verify_replay_package(raw, root, entitlement=entitlement)
    assert _fingerprint(tmp_path) == before


@pytest.mark.wal
@pytest.mark.parametrize("field,value", [("scope", "fresh_controller_run"), ("expires_at", 1),
    ("source_snapshot_sha256", "f" * 64), ("environment_sha256", "f" * 64), ("max_jobs", 2)])
def test_fresh_replay_scope_rejects_changed_pins_and_budget(portable, tmp_path, field, value):
    from devbench.platform.workflow_entrypoints import validate_erql_replay
    root, capsule, plan, broker, entitlement, package, descriptor = portable
    grant = {**_grant(plan, descriptor, package), field: value}
    before = _fingerprint(tmp_path)
    with pytest.raises(PermissionError, match="grant"):
        validate_erql_replay(package, descriptor["root_sha256"], entitlement=entitlement,
            input_path=capsule / "request.json", input_delivery=broker.delivery("audit", "archive"),
            grant=grant, allow_data_execution=True)
    assert _fingerprint(tmp_path) == before


@pytest.mark.wal
def test_audit_grant_does_not_authorize_original_inputs(portable):
    from devbench.platform.workflow_entrypoints import validate_erql_replay
    root, capsule, plan, broker, entitlement, package, descriptor = portable
    with pytest.raises(PermissionError, match="original-input"):
        validate_erql_replay(package, descriptor["root_sha256"], entitlement=entitlement,
            input_path=capsule / "request.json", input_delivery=broker.delivery("audit", "archive"),
            grant=_grant(plan, descriptor, package), allow_data_execution=True)


@pytest.mark.wal
def test_presentation_refuses_current_withholding_without_writes(portable, tmp_path):
    from devbench.analysis import bundles
    from devbench.analysis.scientific_publication import PublicationGuard
    root, capsule, plan, broker, entitlement, package, descriptor = portable
    guard = PublicationGuard(blocked_hashes=frozenset({plan.source_sha256}), roots=(tmp_path,))
    before = _fingerprint(tmp_path)
    with pytest.raises(PermissionError, match="withheld"):
        bundles.replay_package_presentation(package, descriptor["root_sha256"], tmp_path / "held",
                                            entitlement=entitlement, current_guard=guard)
    assert _fingerprint(tmp_path) == before


@pytest.mark.wal
def test_workspace_current_guard_uses_inert_checkpointed_reads(tmp_path):
    from devbench.analysis.scientific_publication import workspace_guard
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    WorkflowOperationLedger(tmp_path / "rna-workflow-operations.sqlite", scientific=True)
    before = _fingerprint(tmp_path)
    guard = workspace_guard(tmp_path, inert=True)
    assert guard.available
    guard.require("a" * 64)
    assert _fingerprint(tmp_path) == before


@pytest.mark.wal
def test_readonly_broker_rejects_reader_that_can_create_sidecars(tmp_path):
    from devbench.analysis.evidence_access import EvidenceBroker
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    broker = _broker(tmp_path)
    readonly = WorkflowOperationLedger(broker.authority.ledger.path, readonly=True)
    with pytest.raises(ValueError, match="inert"):
        EvidenceBroker.open_readonly(readonly.scientific)


def test_invalid_audit_delivery_fails_closed_before_access(tmp_path):
    from devbench.analysis.bundles import verify_replay_package
    from devbench.analysis.scientific_publication import AuditReplayEntitlement
    with pytest.raises(PermissionError, match="audit"):
        verify_replay_package(tmp_path / "absent.zip", "a" * 64,
                              entitlement=AuditReplayEntitlement(object(), "b" * 64))


@pytest.mark.wal
def test_current_resolver_with_active_wal_refuses_without_output(portable, tmp_path):
    import sqlite3

    from devbench.analysis.bundles import replay_package_presentation
    from devbench.analysis.scientific_publication import PublicationGuard
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    root, capsule, plan, broker, entitlement, package, descriptor = portable
    ledger = WorkflowOperationLedger(tmp_path / "rna-workflow-operations.sqlite", scientific=True)
    with sqlite3.connect(ledger.path) as writer:
        writer.execute("CREATE TABLE t22_guard (value INTEGER)")
        writer.commit()
        before = _fingerprint(tmp_path)
        with pytest.raises(PermissionError, match="withheld"):
            replay_package_presentation(package, descriptor["root_sha256"], tmp_path / "blocked",
                entitlement=entitlement, current_guard=PublicationGuard(roots=(tmp_path,)))
        assert _fingerprint(tmp_path) == before


@pytest.mark.wal
def test_capture_never_opens_omitted_original_input(portable, monkeypatch):
    from devbench.analysis.scientific_publication import AuditReplayEntitlement
    from devbench.platform.workflow_entrypoints import SyntheticERQLCapsule
    root, capsule, plan, broker, entitlement, package, descriptor = portable
    capture_sha = hashlib.sha256((root / "execution/audit/manifest.json").read_bytes()).hexdigest()
    capture = AuditReplayEntitlement(broker.delivery("audit", "capture"), capture_sha)
    forbidden = {str(capsule / "request.json"), str(root / "execution/inputs/request.json"),
                 str(root / "execution/audit/objects" / plan.request_sha256)}
    original = os.open
    def deny(path, *args, **kwargs):
        if str(path) in forbidden:
            pytest.fail("Audit capture opened original numerical input")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, "open", deny)
    assert SyntheticERQLCapsule(capsule).describe_replay(root / "execution", entitlement=capture,
        snapshot_root=root / ".runs") == descriptor


@pytest.mark.wal
def test_denied_typed_audit_entitlement_precedes_package_read(portable, monkeypatch):
    from devbench.analysis.bundles import verify_replay_package
    from devbench.analysis.scientific_publication import AuditReplayEntitlement
    from devbench.study import evidence
    root, capsule, plan, broker, entitlement, package, descriptor = portable
    original, reads = evidence._read, []
    def observe(path, *args, **kwargs):
        if Path(path) == package:
            reads.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(evidence, "_read", observe)
    denied = AuditReplayEntitlement(broker.delivery("input", "archive"), descriptor["archive_sha256"])
    with pytest.raises(PermissionError):
        verify_replay_package(package, descriptor["root_sha256"], entitlement=denied)
    assert reads == []


@pytest.mark.wal
def test_input_entitlement_precedes_original_input_read(portable, monkeypatch):
    from devbench.platform.workflow_entrypoints import validate_erql_replay
    from devbench.study import evidence
    root, capsule, plan, broker, entitlement, package, descriptor = portable
    original, reads = evidence._read, []
    def observe(path, *args, **kwargs):
        if Path(path) == capsule / "request.json":
            reads.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(evidence, "_read", observe)
    with pytest.raises(PermissionError):
        validate_erql_replay(package, descriptor["root_sha256"], entitlement=entitlement,
            input_path=capsule / "request.json", input_delivery=broker.delivery("audit", "archive"),
            grant=_grant(plan, descriptor, package), allow_data_execution=True)
    assert reads == []


@pytest.mark.wal
def test_capture_rejects_unlisted_objects_before_reading_them(portable, monkeypatch):
    from devbench.analysis.scientific_publication import AuditReplayEntitlement
    from devbench.platform.workflow_entrypoints import SyntheticERQLCapsule
    root, capsule, plan, broker, entitlement, package, descriptor = portable
    capture_sha = hashlib.sha256((root / "execution/audit/manifest.json").read_bytes()).hexdigest()
    capture = AuditReplayEntitlement(broker.delivery("audit", "capture"), capture_sha)
    unlisted = root / "execution/audit/objects" / ("f" * 64)
    unlisted.write_bytes(b"unentitled original input must not be opened")
    original, reads = os.open, []
    def observe(path, *args, **kwargs):
        if str(path) == str(unlisted):
            reads.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, "open", observe)
    try:
        with pytest.raises(ValueError, match="inventory"):
            SyntheticERQLCapsule(capsule).describe_replay(root / "execution", entitlement=capture,
                snapshot_root=root / ".runs")
        assert reads == []
    finally:
        unlisted.unlink()


def _investigation(tmp_path):
    from tests.test_scientific_publication_policy import (
        register_adaptive_table,
        register_reviewed_claim,
    )
    registry, table, delivery = register_adaptive_table(tmp_path / "investigation")
    claim = register_reviewed_claim(registry, table)
    registry.export(table.subject_id, delivery=delivery, format="json")
    grants = tmp_path / "grants"
    grants.mkdir(mode=0o700)
    return registry, table, claim, delivery, _broker(grants)


def _entitle(broker, source_id, content_sha256, roots):
    from devbench.analysis.scientific_publication import AuditReplayEntitlement
    broker.register_source(source_id, content_sha256=content_sha256, roots=tuple(roots),
                           tags=("audit_replay",), classification="target_outcome")
    return AuditReplayEntitlement(broker.delivery("audit", source_id), content_sha256)


@pytest.mark.wal
def test_owner_snapshot_retains_actual_attempts_reviews_and_exposures(tmp_path):
    registry, table, claim, delivery, broker = _investigation(tmp_path)
    flow = registry.workflow
    assert callable(getattr(flow, "replay_identity", None)), "Owner-resolved audit identity is required"
    identity = flow.replay_identity()
    entitlement = _entitle(broker, "capture", identity["sha256"], (table.source_sha256,))
    snapshot = flow.replay_snapshot(entitlement=entitlement)
    assert snapshot["capture_identity"] == identity
    assert snapshot["dispatches"][table.attempt_id]["envelope"]
    assert snapshot["outcomes"][table.attempt_id]["accepted_record_id"] == table.accepted_record_id
    assert snapshot["reviews"]["cycles"]["claim-cycle"]["phase"] == "completed"
    assert snapshot["exposures"]["disclosures"]
    assert all(hashlib.sha256(row["payload"].encode()).hexdigest() == row["sha256"] for row in snapshot["original_records"])
    assert snapshot["scientific_acceptance"] == "not_granted"


@pytest.mark.wal
def test_public_replay_originates_from_admitted_registry_release(workspace_tmp_path):
    from devbench.analysis import bundles
    registry, table, claim, delivery, broker = _investigation(workspace_tmp_path)
    assert callable(getattr(registry, "export_replay", None)), "Registered public replay export is required"
    package = registry.export_replay(table.subject_id, delivery=delivery, audience="public")
    report = bundles.verify_public_replay_package(package["bytes"], package["root_sha256"])
    assert report["current_use"] == "unavailable" and report["scientific_acceptance"] == "not_granted"
    with zipfile.ZipFile(io.BytesIO(package["bytes"])) as archive:
        assert set(archive.namelist()) == {"replay-manifest.json", "publication.json", "release-witness.json", "renderer.json"}
        assert registry._read(table.source_path) not in [archive.read(name) for name in archive.namelist()]
        assert "publication_projection" in json.loads(archive.read("release-witness.json"))
    output = workspace_tmp_path / "public-presentation"
    replay = bundles.replay_public_presentation(package["bytes"], package["root_sha256"], output)
    assert replay["operation_kind"] == "historical_presentation"
    assert (output / "figure.svg").is_file()


@pytest.mark.wal
@pytest.mark.parametrize("operation", ["verify", "presentation"])
def test_public_route_rejects_restricted_members_before_decoding(tmp_path, monkeypatch, operation):
    from devbench.analysis import bundles
    from devbench.platform import cli
    from devbench.study import evidence
    registry, table, claim, delivery, broker = _investigation(tmp_path)
    identity = registry.workflow.replay_identity()
    capture = _entitle(broker, "capture", identity["sha256"], (table.source_sha256,))
    attachments = {table.source_sha256: _entitle(broker, "output", table.source_sha256, (table.source_sha256,)),
                   claim.packet_sha256: _entitle(broker, "packet", claim.packet_sha256, (claim.packet_sha256,))}
    raw, receipt = bundles.investigation_replay_package(registry, capture_entitlement=capture, artifact_entitlements=attachments)
    package = tmp_path / "restricted.zip"
    package.write_bytes(raw)
    opened, bulk_reads = [], []
    original_open, original_read = zipfile.ZipFile.open, evidence._read
    def observe_open(self, name, *args, **kwargs):
        opened.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
        return original_open(self, name, *args, **kwargs)
    def observe_read(path, *args, **kwargs):
        if Path(path) == package:
            bulk_reads.append(path)
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, "open", observe_open)
    monkeypatch.setattr(evidence, "_read", observe_read)
    argv = [operation, "--audience", "public", "--package", str(package), "--expected-root", receipt["root_sha256"]]
    if operation == "presentation":
        argv += ["--output", str(tmp_path / "not-created")]
    assert cli.replay_package(argv) != 0
    assert opened == ["replay-manifest.json"] and bulk_reads == []
    assert not (tmp_path / "not-created").exists()


@pytest.mark.wal
def test_public_replay_checks_actual_current_owner_before_new_presentation(tmp_path):
    from devbench.analysis import bundles
    from tests.test_scientific_publication_policy import newer_publication_obligation
    registry, table, claim, delivery, broker = _investigation(tmp_path)
    package = registry.export_replay(table.subject_id, delivery=delivery, audience="public")
    report = bundles.verify_public_replay_package(package["bytes"], package["root_sha256"],
        current_registry=registry, delivery=delivery)
    assert report["current_use"] == "checked_current_projection"
    newer_publication_obligation(registry, table, entity_id="table")
    with pytest.raises((ValueError, PermissionError), match="changed|withheld|publication"):
        bundles.replay_public_presentation(package["bytes"], package["root_sha256"], tmp_path / "not-revived",
            current_registry=registry, delivery=delivery)
    assert not (tmp_path / "not-revived").exists()


@pytest.mark.wal
def test_investigation_missing_raw_attachment_is_explicit_before_open(tmp_path, monkeypatch):
    registry, table, claim, delivery, broker = _investigation(tmp_path)
    identity = registry.workflow.replay_identity()
    capture = _entitle(broker, "capture", identity["sha256"], (table.source_sha256,))
    original, reads = os.open, []
    def observe(path, *args, **kwargs):
        if str(path) in {table.source_path, claim.packet_path}:
            reads.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, "open", observe)
    with pytest.raises(PermissionError, match="required_attachment"):
        registry.describe_replay(capture_entitlement=capture)
    assert reads == []


@pytest.mark.wal
def test_restricted_investigation_package_preserves_exact_original_paths_and_relocates(tmp_path):
    from devbench.analysis import bundles
    registry, table, claim, delivery, broker = _investigation(tmp_path)
    assert callable(getattr(registry, "describe_replay", None)), "Investigation package preflight is required"
    identity = registry.workflow.replay_identity()
    capture = _entitle(broker, "capture", identity["sha256"], (table.source_sha256,))
    attachments = {table.source_sha256: _entitle(broker, "output", table.source_sha256, (table.source_sha256,)),
                   claim.packet_sha256: _entitle(broker, "packet", claim.packet_sha256, (table.source_sha256,))}
    descriptor = registry.describe_replay(capture_entitlement=capture, artifact_entitlements=attachments)
    entitlement = _entitle(broker, "archive", descriptor["archive_sha256"], (descriptor["root_sha256"],))
    package = registry.export_replay(audience="restricted_audit", entitlement=entitlement,
        capture_entitlement=capture, artifact_entitlements=attachments)
    source = registry.ledger.path.parent
    hidden = tmp_path / "offline-original"
    source.rename(hidden)
    try:
        report = bundles.verify_investigation_replay(package["bytes"], package["root_sha256"], entitlement=entitlement)
    finally:
        hidden.rename(source)
    assert report["verified"] and report["current_use"] == "unavailable"
    assert report["mode"] == "investigation_evidence_verification"
    assert report["numerical_replay"] == "unsupported_adaptive_profile"
    with zipfile.ZipFile(io.BytesIO(package["bytes"])) as archive:
        original = archive.read("investigation.json")
        assert table.source_path.encode() in original and claim.packet_path.encode() in original
        manifest = json.loads(archive.read("replay-manifest.json"))
        assert manifest["logical_bindings"][table.source_path]["sha256"] == table.source_sha256
        assert manifest["logical_bindings"][claim.packet_path]["sha256"] == claim.packet_sha256
        assert manifest["original_input_scope"] == "excluded_separate_entitlement_required"


@pytest.mark.wal
def test_investigation_literature_replays_from_bound_members_without_old_paths(
    workspace_tmp_path, monkeypatch
):
    from devbench.analysis import bundles, method_sources
    from devbench.analysis import literature_adapter as lit
    from devbench.analysis.checkpoints import digest
    from devbench.analysis.evidence_access import AccessPolicy
    from devbench.analysis.scientific_result_contract import ClaimSpec
    registry, table, claim, delivery, auditor = _investigation(workspace_tmp_path)
    broker = delivery.broker
    policy = AccessPolicy("t22-literature", 1, allow_outcomes=True)
    broker.register_policy(policy)
    broker.register_context("lit-initial", principal_id="synthetic-reviewer", lineage_id="study", role="challenger",
        phase="publication_initial", policy_sha256=policy.sha256())
    broker.register_context("lit-reveal", principal_id="synthetic-reviewer", lineage_id="study", role="challenger",
        phase="narrative_reveal", policy_sha256=policy.sha256(), parents=("lit-initial",))
    monkeypatch.setattr(lit.LiteratureCapability, "verify", lambda self: self.identity())
    body = b'<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article><ArticleTitle>T22 synthetic fixture</ArticleTitle><Abstract><AbstractText>A freshly constructed software fixture supplies context only.</AbstractText></Abstract></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>'
    calls = []
    def offline(*args, **kwargs):
        calls.append("fixture")
        return None, body, "application/xml"
    monkeypatch.setattr(method_sources, "_fetch_hop", offline)
    capability = lit.LiteratureCapability(
        plugin_root=str(workspace_tmp_path / "plugin")
    )
    service = lit.LiteratureService(
        registry.authority, "study", registry.ledger.path.parent / "literature",
        broker=broker, capability=capability, limits=lit.LiteratureLimits(retries=0),
        allow_network=True, data_scope="synthetic",
    )
    spec = ClaimSpec.model_validate_json(json.dumps(claim.claim))
    binding = lit.LiteratureClaimBinding(claim_id=spec.claim_id, claim_sha256=digest(spec.model_dump(mode="json")),
        evidence_sha256=claim.publication_binding.evidence_sha256,
        review_binding_sha256=claim.publication_binding.sha256(), obligation_id="t22-context-only",
        context=lit.BiologicalContext(organism="synthetic", tissue="fixture", intervention="none", direction="unspecified",
                                      uncertainty="software verification"))
    service.bind_claim(binding)
    fetched = service.execute(lit.LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
        request_id="t22-fetch", context_id="lit-initial", binding=binding)
    frozen = service.replay(fetched["snapshot_id"])
    assessment = lit.LiteratureClaimAssessment(claim_id=spec.claim_id, claim_sha256=binding.claim_sha256,
        evidence_sha256=binding.evidence_sha256, snapshot_id=fetched["snapshot_id"],
        passage_ids=(frozen["extraction"]["passages"][0]["passage_id"],), relation="context",
        source_context=binding.context, rationale="Synthetic custody test only", limitations=("Not a scientific assessment",),
        reviewer_id="synthetic-reviewer")
    observed = service.assess(assessment, binding=binding, claim=spec, context_id="lit-reveal")
    identity = registry.workflow.replay_identity()
    capture = _entitle(auditor, "capture", identity["sha256"], (table.source_sha256,))
    attachments = {table.source_sha256: _entitle(auditor, "output", table.source_sha256, (table.source_sha256,)),
                   claim.packet_sha256: _entitle(auditor, "packet", claim.packet_sha256, (claim.packet_sha256,))}
    for index, ref in enumerate(service.state()["snapshots"].values()):
        raw = Path(ref["path"]).read_bytes()
        attachments[ref["sha256"]] = _entitle(auditor, "lit-snapshot-" + str(index), hashlib.sha256(raw).hexdigest(), (ref["sha256"],))
        for n, response in enumerate(frozen["responses"]):
            attachments[response["content_sha256"]] = _entitle(auditor, f"lit-response-{index}-{n}",
                response["content_sha256"], (ref["sha256"],))
    descriptor = registry.describe_replay(capture_entitlement=capture, artifact_entitlements=attachments)
    audit = _entitle(auditor, "archive", descriptor["archive_sha256"], (descriptor["root_sha256"],))
    package = registry.export_replay(audience="restricted_audit", entitlement=audit,
        capture_entitlement=capture, artifact_entitlements=attachments)
    original = registry.ledger.path.parent
    moved = workspace_tmp_path / "old-workspace-unavailable"
    original.rename(moved)
    monkeypatch.setattr(method_sources, "_fetch_hop", lambda *a, **k: pytest.fail("Replay contacted a provider"))
    try:
        report = bundles.verify_investigation_replay(package["bytes"], descriptor["root_sha256"], entitlement=audit)
    finally:
        moved.rename(original)
    assert report["literature_snapshots_verified"] == 1 and calls == ["fixture"]
    assert report["scientific_acceptance"] == "not_granted"
    assert observed["semantic_entailment_verified"] is False


@pytest.mark.wal
def test_investigation_uses_owner_planner_and_actual_campaign_identity(tmp_path):
    from tests.test_two_agent_investigation_v24 import (
        constructive_setup,
        decision,
        invoke,
    )
    planner, flow, controller, selector, *_ = constructive_setup(tmp_path / "controller", [decision()])
    controller.bind(flow)
    invoke(controller.invoker, planner, selector)
    grants = tmp_path / "grants"
    grants.mkdir(mode=0o700)
    broker = _broker(grants)
    identity = flow.replay_identity(planner=planner)
    capture = _entitle(broker, "capture", identity["sha256"], ("a" * 64,))
    snapshot = flow.replay_snapshot(entitlement=capture, planner=planner)
    assert snapshot["model_campaign"]["campaign_id"] == "constructive"
    assert snapshot["model_reservations"] == flow.ledger.campaign_reservations("constructive")
    assert snapshot["scientific_material"]["plan"]["context"]["task"] == planner.get(flow.lineage_id)["context"]["task"]
    assert snapshot["controller"]["binding"]["campaign_id"] == "constructive"


@pytest.mark.wal
def test_protected_evaluation_lineage_cannot_be_captured_with_audit_grant(tmp_path):
    from tests.test_investigation_invalidation import step
    registry, table, claim, delivery, broker = _investigation(tmp_path)
    registry.workflow.insert(step("held", held_out=True, purpose="protected_evaluation"))
    identity = registry.workflow.replay_identity()
    capture = _entitle(broker, "capture", identity["sha256"], (table.source_sha256,))
    with pytest.raises(PermissionError, match="protected"):
        registry.describe_replay(capture_entitlement=capture)


@pytest.mark.wal
def test_investigation_cannot_rewrite_captured_review_state(tmp_path):
    from devbench.analysis import bundles
    registry, table, claim, delivery, broker = _investigation(tmp_path)
    identity = registry.workflow.replay_identity()
    capture = _entitle(broker, "capture", identity["sha256"], (table.source_sha256,))
    attachments = {table.source_sha256: _entitle(broker, "output", table.source_sha256, (table.source_sha256,)),
                   claim.packet_sha256: _entitle(broker, "packet", claim.packet_sha256, (claim.packet_sha256,))}
    raw, _ = bundles.investigation_replay_package(registry, capture_entitlement=capture, artifact_entitlements=attachments)
    members = bundles._replay_archive(raw)
    manifest = json.loads(members.pop("replay-manifest.json"))
    snapshot = json.loads(members["investigation.json"])
    snapshot["reviews"]["cycles"]["claim-cycle"]["phase"] = "failed"
    members["investigation.json"] = bundles._canonical(snapshot)
    manifest.pop("members")
    changed, receipt = bundles._sealed_replay(members, manifest)
    audit = _entitle(broker, "changed", receipt["archive_sha256"], (receipt["root_sha256"],))
    with pytest.raises(ValueError, match="review|projection"):
        bundles.verify_investigation_replay(changed, receipt["root_sha256"], entitlement=audit)


@pytest.mark.wal
def test_generic_cli_verifies_relocated_public_and_restricted_packages(
    workspace_tmp_path,
):
    workspace = workspace_tmp_path
    registry, table, claim, delivery, broker = _investigation(workspace)
    public = registry.export_replay(table.subject_id, delivery=delivery, audience="public")
    public_path = workspace / "public.zip"
    public_path.write_bytes(public["bytes"])
    identity = registry.workflow.replay_identity()
    capture = _entitle(broker, "capture", identity["sha256"], (table.source_sha256,))
    attachments = {table.source_sha256: _entitle(broker, "output", table.source_sha256, (table.source_sha256,)),
                   claim.packet_sha256: _entitle(broker, "packet", claim.packet_sha256, (claim.packet_sha256,))}
    descriptor = registry.describe_replay(capture_entitlement=capture, artifact_entitlements=attachments)
    audit = _entitle(broker, "archive", descriptor["archive_sha256"], (descriptor["root_sha256"],))
    restricted = registry.export_replay(audience="restricted_audit", entitlement=audit,
        capture_entitlement=capture, artifact_entitlements=attachments)
    restricted_path = workspace / "audit.zip"
    restricted_path.write_bytes(restricted["bytes"])
    original = registry.ledger.path.parent
    moved = workspace / "unavailable"
    original.rename(moved)
    try:
        result = _cli(
            workspace, "replay-package", "verify", "--audience", "public",
            "--package", public_path, "--expected-root", public["root_sha256"],
        )
        assert result.returncode == 0, result.stderr
        result = _cli(
            workspace, "replay-package", "presentation", "--audience", "public",
            "--package", public_path, "--expected-root", public["root_sha256"],
            "--output", workspace / "presentation-cli",
        )
        assert result.returncode == 0, result.stderr
        result = _cli(
            workspace, "replay-package", "verify", "--audience", "restricted_audit",
            "--package", restricted_path, "--expected-root", restricted["root_sha256"],
            *_audit_args(broker),
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["current_use"] == "unavailable"
    finally:
        moved.rename(original)
    assert not (workspace / ".runs").exists()


@pytest.mark.wal
def test_revoked_typed_audit_entitlement_precedes_package_read(portable, tmp_path, monkeypatch):
    from devbench.analysis.bundles import verify_replay_package
    from devbench.engine.partition_authority import PartitionAuthority
    from devbench.study import evidence
    _, _, plan, _, _, package, descriptor = portable
    broker = _broker(tmp_path)
    denied = _entitle(broker, "archive", descriptor["archive_sha256"], (descriptor["root_sha256"],))
    PartitionAuthority(broker.authority.ledger, plan.source_sha256).protect_outputs((descriptor["archive_sha256"],))
    original, reads = evidence._read, []
    def observe(path, *args, **kwargs):
        if Path(path) == package:
            reads.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(evidence, "_read", observe)
    with pytest.raises(PermissionError):
        verify_replay_package(package, descriptor["root_sha256"], entitlement=denied)
    assert reads == []


@pytest.mark.wal
def test_changed_installed_snapshot_cannot_numerically_replay(portable, tmp_path, monkeypatch):
    from devbench.engine import execution_snapshot
    from devbench.platform.workflow_entrypoints import replay_fixed_erql
    root, capsule, plan, broker, entitlement, package, descriptor = portable
    broker.register_source("exact-input-negative", content_sha256=plan.request_sha256, roots=(plan.source_sha256,),
        tags=("original_source_data", "replay_input"), classification="target_outcome")
    monkeypatch.setattr(execution_snapshot, "current_binding", lambda: {"sha256": "f" * 64})
    with pytest.raises(PermissionError, match="exact installed source"):
        replay_fixed_erql(package, descriptor["root_sha256"], tmp_path / "not-executed", entitlement=entitlement,
            input_path=capsule / "request.json", input_delivery=broker.delivery("input", "exact-input-negative"),
            grant=_grant(plan, descriptor, package), execution_ledger=tmp_path / "not-created.sqlite", allow_data_execution=True)
    assert not (tmp_path / "not-executed").exists() and not (tmp_path / "not-created.sqlite").exists()


@pytest.mark.wal
@pytest.mark.skipif(os.environ.get("DEVBENCH_T15_DOCKER") != "1", reason="Requires the pinned exploratory OCI image")
def test_unpublished_intermediate_oci_output_has_owner_resolved_replay(tmp_path):
    from devbench.analysis import bundles
    from devbench.analysis.scientific_publication import PublicationRegistry
    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.engine.methods import MethodService
    from devbench.engine.workflow_operations import LEDGER_NAME
    from tests.test_exploratory_execution_profile import bound_step, register
    from tests.test_investigation_invalidation import artifact, link, make, step
    origin = tmp_path / "origin"
    origin.mkdir(mode=0o700)
    methods = MethodService(origin)
    flow = make(origin, name=LEDGER_NAME)
    runtime = ExploratoryOCI(methods, flow.ledger, [{"threshold": 1}])
    original = b'{"x":[2,4,8]}'
    source = artifact("input").model_copy(update={"sha256": runtime.training.put(original)})
    flow.register_artifact(source)
    method = register(methods, origin / "source", "t22-intermediate", ("data",), ("out",),
        'import json\nfrom pathlib import Path\nx=json.loads(Path("/inputs/data").read_text())["x"]\nPath("/outputs/out.json").write_text(json.dumps({"sum":sum(x)}))\n')
    flow.insert(bound_step(method, step("intermediate", link("data", "input", immutable=True))))
    outcome = flow.dispatch("intermediate", runtime, actor="synthetic-t22")
    assert outcome["status"] == "succeeded"
    registry = PublicationRegistry(flow)
    grants = tmp_path / "grants"
    grants.mkdir(mode=0o700)
    broker = _broker(grants)
    identity = flow.replay_identity()
    capture = _entitle(broker, "capture", identity["sha256"], (source.sha256,))
    output = outcome["outputs"]["out"]
    attachments = {output["sha256"]: _entitle(broker, "intermediate", output["sha256"], (source.sha256,)),
        outcome["receipt_sha256"]: _entitle(broker, "receipt", outcome["receipt_sha256"], (source.sha256,))}
    receipt = json.loads((runtime.runs / outcome["attempt_id"] / "receipt.json").read_bytes())
    attachments[receipt["io_sha256"]] = _entitle(broker, "io", receipt["io_sha256"], (source.sha256,))
    descriptor = registry.describe_replay(capture_entitlement=capture, artifact_entitlements=attachments, runtime_owner=runtime)
    audit = _entitle(broker, "archive", descriptor["archive_sha256"], (descriptor["root_sha256"],))
    package = registry.export_replay(audience="restricted_audit", entitlement=audit, capture_entitlement=capture,
        artifact_entitlements=attachments, runtime_owner=runtime)
    with zipfile.ZipFile(io.BytesIO(package["bytes"])) as archive:
        assert original not in [archive.read(name) for name in archive.namelist()]
    hidden = tmp_path / "old-origin"
    origin.rename(hidden)
    try:
        assert bundles.verify_investigation_replay(package["bytes"], descriptor["root_sha256"], entitlement=audit)["verified"]
    finally:
        hidden.rename(origin)
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    foreign = ExploratoryOCI(MethodService(other), make(other, name=LEDGER_NAME).ledger, [{"threshold": 1}])
    with pytest.raises(PermissionError, match="owner"):
        registry.describe_replay(capture_entitlement=capture, artifact_entitlements=attachments, runtime_owner=foreign)


@pytest.mark.wal
@pytest.mark.parametrize("mutation", ["request", "capture", "usage_total"])
def test_model_capture_bodies_and_usage_cannot_rewrite_retained_accounting(tmp_path, mutation):
    from devbench.analysis import bundles
    from devbench.analysis.scientific_publication import PublicationRegistry
    from tests.test_two_agent_investigation_v24 import (
        constructive_setup,
        decision,
        invoke,
    )
    planner, flow, controller, selector, *_ = constructive_setup(tmp_path / "controller", [decision()])
    controller.bind(flow)
    invoke(controller.invoker, planner, selector)
    registry = PublicationRegistry(flow)
    grants = tmp_path / "grants"
    grants.mkdir(mode=0o700)
    broker = _broker(grants)
    identity = flow.replay_identity(planner=planner)
    capture = _entitle(broker, "capture", identity["sha256"], ("a" * 64,))
    raw, _ = bundles.investigation_replay_package(registry, capture_entitlement=capture, planner=planner)
    members = bundles._replay_archive(raw)
    manifest = json.loads(members.pop("replay-manifest.json"))
    snapshot = json.loads(members["investigation.json"])
    if mutation == "usage_total":
        snapshot["model_campaigns"][0]["verified_spent_tokens"] += 1
        snapshot["model_campaign"]["verified_spent_tokens"] += 1
    else:
        record = next(item for item in snapshot["model_reservations"] if item[mutation] is not None)
        record[mutation]["t22_forged"] = True
    members["investigation.json"] = bundles._canonical(snapshot)
    manifest.pop("members")
    changed, receipt = bundles._sealed_replay(members, manifest)
    audit = _entitle(broker, "changed", receipt["archive_sha256"], (receipt["root_sha256"],))
    with pytest.raises(ValueError, match="model"):
        bundles.verify_investigation_replay(changed, receipt["root_sha256"], entitlement=audit)


def _mixed_model_package(tmp_path):
    from devbench.analysis import bundles
    from devbench.analysis.scientific_publication import PublicationRegistry
    from tests.test_two_agent_investigation_v24 import constructive_setup, decision
    planner, flow, controller, selector, *_ = constructive_setup(tmp_path / "controller", [decision(), decision()])
    controller.bind(flow)
    for node, attempt in (("z-controller", 0), ("a-review-followup", 1)):
        controller.invoker.invoke(selector, {"decision_kind": "adaptive_controller"}, planner.methods.root / node,
            role="controller", purpose="adaptive_controller", node=node, attempt=attempt)
    grants = tmp_path / "grants"
    grants.mkdir(mode=0o700)
    broker = _broker(grants)
    identity = flow.replay_identity()
    capture = _entitle(broker, "capture", identity["sha256"], ("a" * 64,))
    raw, receipt = bundles.investigation_replay_package(PublicationRegistry(flow), capture_entitlement=capture)
    audit = _entitle(broker, "archive", receipt["archive_sha256"], (receipt["root_sha256"],))
    return raw, receipt, audit, broker


@pytest.mark.wal
def test_model_reservations_join_by_stable_identity_not_order(tmp_path):
    from devbench.analysis import bundles
    raw, receipt, audit, _ = _mixed_model_package(tmp_path)
    snapshot = json.loads(bundles._replay_archive(raw)["investigation.json"])
    keys = lambda rows: [(row["campaign_id"], row["node"], row["attempt"]) for row in rows]
    assert keys(snapshot["capture_identity"]["model_reservations"]) != keys(snapshot["model_reservations"])
    assert bundles.verify_investigation_replay(raw, receipt["root_sha256"], entitlement=audit)["verified"]


@pytest.mark.wal
@pytest.mark.parametrize("change", ["missing", "duplicate"])
def test_model_reservation_identity_join_rejects_missing_or_duplicate(tmp_path, change):
    from devbench.analysis import bundles
    raw, _, _, broker = _mixed_model_package(tmp_path)
    members = bundles._replay_archive(raw)
    manifest = json.loads(members.pop("replay-manifest.json"))
    snapshot = json.loads(members["investigation.json"])
    if change == "missing":
        snapshot["model_reservations"].pop()
    else:
        snapshot["model_reservations"][-1] = snapshot["model_reservations"][0]
    members["investigation.json"] = bundles._canonical(snapshot)
    manifest.pop("members")
    changed, receipt = bundles._sealed_replay(members, manifest)
    audit = _entitle(broker, "changed", receipt["archive_sha256"], (receipt["root_sha256"],))
    with pytest.raises(ValueError, match="model"):
        bundles.verify_investigation_replay(changed, receipt["root_sha256"], entitlement=audit)


@pytest.mark.wal
@pytest.mark.parametrize("mutation", ["objective", "basis", "steps"])
def test_scientific_material_matches_original_owner_capture_and_fold(tmp_path, mutation):
    from devbench.analysis import bundles
    from devbench.analysis.scientific_publication import PublicationRegistry
    from tests.test_two_agent_investigation_v24 import constructive_setup, decision
    planner, flow, controller, *_ = constructive_setup(tmp_path / "controller", [decision()])
    controller.bind(flow)
    grants = tmp_path / "grants"
    grants.mkdir(mode=0o700)
    broker = _broker(grants)
    identity = flow.replay_identity(planner=planner)
    capture = _entitle(broker, "capture", identity["sha256"], ("a" * 64,))
    raw, receipt = bundles.investigation_replay_package(PublicationRegistry(flow), capture_entitlement=capture, planner=planner)
    audit = _entitle(broker, "original", receipt["archive_sha256"], (receipt["root_sha256"],))
    report = bundles.verify_investigation_replay(raw, receipt["root_sha256"], entitlement=audit)
    assert report["scientific_material_status"] == "bound_original_owner_capture"
    members = bundles._replay_archive(raw)
    manifest = json.loads(members.pop("replay-manifest.json"))
    snapshot = json.loads(members["investigation.json"])
    if mutation == "objective":
        snapshot["scientific_material"]["plan"]["context"]["task"]["scientific_objective"] = "retrospective claim"
    else:
        snapshot["scientific_material"][mutation] = {"invented": {"not": "an original owner record"}}
    assert snapshot["capture_identity"] == identity
    members["investigation.json"] = bundles._canonical(snapshot)
    manifest.pop("members")
    manifest["scientific_locks"] = bundles._investigation_dependencies(snapshot)[2]
    changed, changed_receipt = bundles._sealed_replay(members, manifest)
    changed_audit = _entitle(broker, "changed", changed_receipt["archive_sha256"], (changed_receipt["root_sha256"],))
    with pytest.raises(ValueError, match="scientific_material"):
        bundles.verify_investigation_replay(changed, changed_receipt["root_sha256"], entitlement=changed_audit)


@pytest.mark.wal
def test_absent_owner_material_is_explicit_unavailable(tmp_path):
    from devbench.analysis import bundles
    raw, receipt, audit, _ = _mixed_model_package(tmp_path)
    report = bundles.verify_investigation_replay(raw, receipt["root_sha256"], entitlement=audit)
    assert report["scientific_material_status"] == "unavailable_no_owner_binding"


def _runtime_attempt_fixture(tmp_path, program):
    from devbench.analysis.scientific_publication import PublicationRegistry
    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.engine.methods import MethodService
    from devbench.engine.workflow_operations import LEDGER_NAME
    from tests.test_exploratory_execution_profile import bound_step, register
    from tests.test_investigation_invalidation import artifact, link, make, step
    origin = tmp_path / "origin"
    origin.mkdir(mode=0o700)
    methods = MethodService(origin)
    flow = make(origin, name=LEDGER_NAME)
    runtime = ExploratoryOCI(methods, flow.ledger, [{"threshold": 1}])
    source = artifact("input").model_copy(update={"sha256": runtime.training.put(b'{"x":[2,4,8]}')})
    flow.register_artifact(source)
    method = register(methods, origin / "source", "t22-receipt-states", ("data",), ("out",), program)
    flow.insert(bound_step(method, step("attempt", link("data", "input", immutable=True))))
    grants = tmp_path / "grants"
    grants.mkdir(mode=0o700)
    return PublicationRegistry(flow), runtime, source, _broker(grants)


@pytest.mark.wal
@pytest.mark.skipif(os.environ.get("DEVBENCH_T15_DOCKER") != "1", reason="Requires the pinned exploratory OCI image")
@pytest.mark.parametrize("partial", [False, True])
def test_failed_and_partial_oci_attempts_require_receipt_and_io_closure(tmp_path, monkeypatch, partial):
    from devbench.analysis import bundles
    program = ('from pathlib import Path\nPath("/outputs/out.json").write_text("{\\"partial\\":true}")\n' if partial else '')
    program += 'raise RuntimeError("fresh synthetic failure retained for replay")\n'
    registry, runtime, source, broker = _runtime_attempt_fixture(tmp_path, program)
    outcome = registry.workflow.dispatch("attempt", runtime, actor="synthetic-t22")
    assert outcome["status"] == "failed"
    capture_id = registry.workflow.replay_identity()
    capture = _entitle(broker, "capture", capture_id["sha256"], (source.sha256,))
    with pytest.raises(ValueError, match="runtime_receipt_owner"):
        registry.describe_replay(capture_entitlement=capture)
    directory = runtime.runs / outcome["attempt_id"]
    receipt = json.loads((directory / "receipt.json").read_bytes())
    attachments = {outcome["receipt_sha256"]: _entitle(broker, "receipt", outcome["receipt_sha256"], (source.sha256,))}
    original_open, denied_reads = os.open, []
    def observe(path, *args, **kwargs):
        if str(path) == str(directory / "io.json"):
            denied_reads.append(path)
        return original_open(path, *args, **kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(os, "open", observe)
        with pytest.raises(PermissionError, match="required_attachment"):
            registry.describe_replay(capture_entitlement=capture, runtime_owner=runtime, artifact_entitlements=attachments)
    assert denied_reads == []
    attachments[receipt["io_sha256"]] = _entitle(broker, "io", receipt["io_sha256"], (source.sha256,))
    raw, descriptor = bundles.investigation_replay_package(registry, capture_entitlement=capture,
        runtime_owner=runtime, artifact_entitlements=attachments)
    audit = _entitle(broker, "archive", descriptor["archive_sha256"], (descriptor["root_sha256"],))
    report = bundles.verify_investigation_replay(raw, descriptor["root_sha256"], entitlement=audit)
    assert report["required_runtime_evidence_complete"] is True
    assert report["attempt_evidence"][0]["outcome_status"] == "failed"
    assert report["attempt_evidence"][0]["receipt_status"] == "bound_original_receipt"
    members = bundles._replay_archive(raw)
    manifest = json.loads(members.pop("replay-manifest.json"))
    snapshot = json.loads(members["investigation.json"])
    snapshot.pop("runtime_references")
    manifest["logical_bindings"] = {}
    manifest.pop("members")
    changed, changed_receipt = bundles._sealed_replay({"investigation.json": bundles._canonical(snapshot)}, manifest)
    changed_audit = _entitle(broker, "changed", changed_receipt["archive_sha256"], (changed_receipt["root_sha256"],))
    with pytest.raises(ValueError, match="runtime_receipt_owner"):
        bundles.verify_investigation_replay(changed, changed_receipt["root_sha256"], entitlement=changed_audit)


@pytest.mark.wal
@pytest.mark.parametrize("state", ["reserved", "uncertain", "cancelled"])
def test_unfinished_attempts_preserve_unknown_vs_never_launched(tmp_path, monkeypatch, state):
    from devbench.analysis import bundles
    registry, runtime, source, broker = _runtime_attempt_fixture(tmp_path, 'raise RuntimeError("must not run")\n')
    def interrupted(*args, **kwargs):
        raise RuntimeError("injected interruption")
    with monkeypatch.context() as scoped:
        if state == "reserved":
            scoped.setattr(registry.workflow.authority, "dispatch_attempt", interrupted)
        else:
            scoped.setattr(runtime, "run", interrupted)
        with pytest.raises(RuntimeError, match="injected interruption"):
            registry.workflow.dispatch("attempt", runtime, actor="synthetic-t22")
    if state == "cancelled":
        registry.workflow.authority.cancel_execution(registry.workflow.lineage_id, actor="synthetic-t22")
    identity = registry.workflow.replay_identity()
    capture = _entitle(broker, "capture", identity["sha256"], (source.sha256,))
    raw, descriptor = bundles.investigation_replay_package(registry, capture_entitlement=capture)
    audit = _entitle(broker, "archive", descriptor["archive_sha256"], (descriptor["root_sha256"],))
    report = bundles.verify_investigation_replay(raw, descriptor["root_sha256"], entitlement=audit)
    row = report["attempt_evidence"][0]
    assert row["receipt_status"] == ("never_launched" if state == "reserved" else "unavailable_pending_or_uncertain")
    assert row["receipt_sha256"] is None and report["required_runtime_evidence_complete"] is (state == "reserved")
    if state == "cancelled":
        assert row["lineage_execution_status"] == "cancelled"
    assert not list(runtime.runs.glob("*/receipt.json"))


@pytest.mark.wal
@pytest.mark.skipif(os.environ.get("DEVBENCH_T15_DOCKER") != "1", reason="Requires the pinned exploratory OCI image")
@pytest.mark.parametrize("held_first", [False, True])
def test_shared_output_bytes_preserve_each_original_attempt_use(tmp_path, monkeypatch, held_first):
    from devbench.analysis import bundles
    from tests.test_exploratory_execution_profile import bound_step, register
    from tests.test_investigation_invalidation import link, step
    program = 'from pathlib import Path\nPath("/outputs/out.json").write_text("{\\"sum\\":14}")\n'
    registry, runtime, source, broker = _runtime_attempt_fixture(tmp_path, program)
    original_run = runtime.run
    if held_first:
        def run_then_hold(envelope, *, token):
            result = original_run(envelope, token=token)
            registry.workflow.hold("attempt", "retained_synthetic_hold", evidence_sha256="f" * 64)
            return result
        monkeypatch.setattr(runtime, "run", run_then_hold)
    first = registry.workflow.dispatch("attempt", runtime, actor="synthetic-t22")
    monkeypatch.setattr(runtime, "run", original_run)
    method = register(runtime.methods, tmp_path / "second-method", "shared-output", ("data",), ("out",), program)
    registry.workflow.insert(bound_step(method, step("second", link("data", "input", immutable=True))))
    second = registry.workflow.dispatch("second", runtime, actor="synthetic-t22")
    assert first["status"] == ("audit_only" if held_first else "succeeded") and second["status"] == "succeeded"
    identity = registry.workflow.replay_identity()
    capture = _entitle(broker, "capture", identity["sha256"], (source.sha256,))
    attachments = {}
    for index, reference in enumerate(runtime.replay_references(registry.workflow)):
        sha = reference["sha256"]
        if sha not in attachments:
            attachments[sha] = _entitle(broker, "artifact-" + str(index), sha, (source.sha256,))
        if reference["role"] == "original_oci_receipt":
            io_sha = json.loads(Path(reference["path"]).read_bytes())["io_sha256"]
            attachments[io_sha] = _entitle(broker, "io-" + str(index), io_sha, (source.sha256,))
    raw, descriptor = bundles.investigation_replay_package(registry, capture_entitlement=capture,
        artifact_entitlements=attachments, runtime_owner=runtime)
    audit = _entitle(broker, "archive", descriptor["archive_sha256"], (descriptor["root_sha256"],))
    report = bundles.verify_investigation_replay(raw, descriptor["root_sha256"], entitlement=audit)
    assert report["verified"] and len(report["attempt_evidence"]) == 2
    members = bundles._replay_archive(raw)
    manifest = json.loads(members["replay-manifest.json"])
    shared_sha = second["outputs"]["out"]["sha256"]
    binding = manifest["logical_bindings"][str(runtime.training.root / shared_sha)]
    assert {(item["attempt_id"], item["role"]) for item in binding["uses"]} == {
        (first["attempt_id"], "unaccepted_output" if held_first else "accepted_output"),
        (second["attempt_id"], "accepted_output")}
    assert [name for name in members if name == "objects/" + shared_sha] == ["objects/" + shared_sha]


@pytest.mark.wal
@pytest.mark.skipif(os.environ.get("DEVBENCH_T15_DOCKER") != "1", reason="Requires the pinned exploratory OCI image")
@pytest.mark.parametrize("entrypoint", ["api", "cli"])
def test_original_input_alias_is_refused_before_attachment_read_or_object_decode(tmp_path, monkeypatch, entrypoint):
    from devbench.analysis import bundles
    from devbench.platform import cli
    program = 'from pathlib import Path\nPath("/outputs/out.json").write_bytes(Path("/inputs/data").read_bytes())\n'
    registry, runtime, source, broker = _runtime_attempt_fixture(tmp_path, program)
    outcome = registry.workflow.dispatch("attempt", runtime, actor="synthetic-t22")
    assert outcome["status"] == "succeeded" and outcome["outputs"]["out"]["sha256"] == source.sha256
    identity = registry.workflow.replay_identity()
    capture = _entitle(broker, "capture", identity["sha256"], (source.sha256,))
    references = runtime.replay_references(registry.workflow)
    attachments = {}
    for index, reference in enumerate(references):
        sha = reference["sha256"]
        attachments[sha] = _entitle(broker, "artifact-" + str(index), sha, (source.sha256,))
        if reference["role"] == "original_oci_receipt":
            io_sha = json.loads(Path(reference["path"]).read_bytes())["io_sha256"]
            attachments[io_sha] = _entitle(broker, "io-" + str(index), io_sha, (source.sha256,))
    input_path = runtime.training.root / source.sha256
    original_open, reads = os.open, []
    def observe(path, *args, **kwargs):
        if str(path) == str(input_path):
            reads.append(path)
        return original_open(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(os, "open", observe)
        with pytest.raises(PermissionError, match="original_input_alias_forbidden"):
            bundles.investigation_replay_package(registry, capture_entitlement=capture,
                artifact_entitlements=attachments, runtime_owner=runtime)
    assert reads == []

    # Construct the adversarial envelope from the fresh real owner records.
    # The input object is the fixture literal, not a product-side input read.
    snapshot = registry.workflow.replay_snapshot(entitlement=capture)
    snapshot["runtime_references"] = references
    bindings, external, locks = bundles._investigation_dependencies(snapshot)
    members, logical, pending = {"investigation.json": bundles._canonical(snapshot)}, {}, list(bindings.items())
    while pending:
        path, reference = pending.pop(0)
        reference = {"uses": [], **reference}
        if path in logical:
            continue
        content = b'{"x":[2,4,8]}' if path == str(input_path) else Path(path).read_bytes()
        content_sha = bundles._sha(content)
        assert content_sha == reference["sha256"]
        member = "objects/" + content_sha
        members[member] = content
        logical[path] = {**reference, "content_sha256": content_sha, "member": member, "size_bytes": len(content)}
        pending.extend(bundles._dependent_replay_references(path, reference, content, snapshot))
    raw, receipt = bundles._sealed_replay(members, {"format": bundles.INVESTIGATION_REPLAY_FORMAT,
        "audience": "restricted_audit", "capture_identity": snapshot["capture_identity"],
        "logical_bindings": logical, "external_dependencies": external, "scientific_locks": locks,
        "attempt_evidence": bundles._attempt_evidence(snapshot),
        "original_input_scope": "excluded_separate_entitlement_required", "protected_evaluation_scope": "excluded",
        "scientific_acceptance": "not_granted", "current_use": "requires_live_owner", "numerical_replay": "unsupported_adaptive_profile"})
    package = tmp_path / "adversarial.zip"
    package.write_bytes(raw)
    audit = _entitle(broker, "archive", receipt["archive_sha256"], (receipt["root_sha256"],))
    opened, zip_open = [], zipfile.ZipFile.open
    def observe_zip(self, name, *args, **kwargs):
        opened.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
        return zip_open(self, name, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, "open", observe_zip)
    if entrypoint == "api":
        with pytest.raises(PermissionError, match="original_input_alias_forbidden"):
            bundles.verify_investigation_replay(package, receipt["root_sha256"], entitlement=audit)
    else:
        assert cli.replay_package(["verify", "--audience", "restricted_audit", "--package", str(package),
            "--expected-root", receipt["root_sha256"], "--audit-ledger", str(broker.authority.ledger.path),
            "--audit-context", "audit", "--audit-source", "archive"]) == 1
    assert not [name for name in opened if name.startswith("objects/")]


def _mutate_required_output_use(
    before, mutation, *, attempt_id, first_path, first_sha
):
    members = dict(before)
    manifest = json.loads(members.pop("replay-manifest.json"))
    snapshot = json.loads(members["investigation.json"])
    reference = next(
        item for item in snapshot["runtime_references"]
        if item["attempt_id"] == attempt_id and item["role"] == "unaccepted_output"
    )
    if mutation == "wrong_port":
        reference["port"] = "unrecorded-port"
    elif mutation == "duplicate_use":
        snapshot["runtime_references"].append(dict(reference))
    else:
        omit_payload = mutation == "omit_use_and_payload"
        if omit_payload:
            snapshot["runtime_references"].remove(reference)
        binding = manifest["logical_bindings"][first_path]
        binding["uses"] = [
            item for item in binding["uses"] if item["attempt_id"] != attempt_id
        ]
        if binding["uses"]:
            binding["role"] = "accepted_output"
        else:
            manifest["logical_bindings"].pop(first_path)
            if omit_payload:
                members.pop("objects/" + first_sha)
    return members, manifest, snapshot


@pytest.mark.wal
@pytest.mark.skipif(os.environ.get("DEVBENCH_T15_DOCKER") != "1", reason="Requires the pinned exploratory OCI image")
@pytest.mark.parametrize("distinct", [False, True])
def test_every_original_output_port_and_use_is_required(tmp_path, monkeypatch, distinct):
    from devbench.analysis import bundles
    from tests.test_exploratory_execution_profile import bound_step, register
    from tests.test_investigation_invalidation import link, step
    program = 'from pathlib import Path\nPath("/outputs/out.json").write_text("{\\"value\\":24}")\n'
    registry, runtime, source, broker = _runtime_attempt_fixture(tmp_path, program)
    original_run = runtime.run
    def held(envelope, *, token):
        receipt = original_run(envelope, token=token)
        registry.workflow.hold("attempt", "retained_output_hold", evidence_sha256="c" * 64)
        return receipt
    with monkeypatch.context() as scoped:
        scoped.setattr(runtime, "run", held)
        first = registry.workflow.dispatch("attempt", runtime, actor="synthetic-t22")
    method = register(runtime.methods, tmp_path / "second-method", "output-closure", ("data",), ("out",),
                      program.replace("24", "25") if distinct else program)
    registry.workflow.insert(bound_step(method, step("second", link("data", "input", immutable=True))))
    second = registry.workflow.dispatch("second", runtime, actor="synthetic-t22")
    assert first["status"] == "audit_only" and second["status"] == "succeeded"
    first_sha = first["staged_outputs"]["out"]["sha256"]
    assert (first_sha != second["outputs"]["out"]["sha256"]) is distinct
    capture_id = registry.workflow.replay_identity()
    capture = _entitle(broker, "capture", capture_id["sha256"], (source.sha256,))
    attachments = {}
    for index, reference in enumerate(runtime.replay_references(registry.workflow)):
        sha = reference["sha256"]
        if sha not in attachments:
            attachments[sha] = _entitle(broker, "artifact-" + str(index), sha, (source.sha256,))
        if reference["role"] == "original_oci_receipt":
            io_sha = json.loads(Path(reference["path"]).read_bytes())["io_sha256"]
            attachments[io_sha] = _entitle(broker, "io-" + str(index), io_sha, (source.sha256,))
    raw, descriptor = bundles.investigation_replay_package(registry, capture_entitlement=capture,
        artifact_entitlements=attachments, runtime_owner=runtime)
    audit = _entitle(broker, "archive", descriptor["archive_sha256"], (descriptor["root_sha256"],))
    report = bundles.verify_investigation_replay(raw, descriptor["root_sha256"], entitlement=audit)
    assert report["verified"] and report["required_runtime_evidence_complete"]
    assert all(row["output_uses"][0]["port"] == "out" for row in report["attempt_evidence"])
    before = bundles._replay_archive(raw)
    first_path = str(runtime.training.root / first_sha)
    observations = {}
    for mutation in ("omit_use_and_payload", "omit_logical_use", "wrong_port", "duplicate_use"):
        members, manifest, snapshot = _mutate_required_output_use(
            before, mutation, attempt_id=first["attempt_id"],
            first_path=first_path, first_sha=first_sha,
        )
        original_snapshot = json.loads(before["investigation.json"])
        assert snapshot["original_records"] == original_snapshot["original_records"]
        assert snapshot["outcomes"] == original_snapshot["outcomes"]
        members["investigation.json"] = bundles._canonical(snapshot)
        manifest.pop("members")
        changed, receipt = bundles._sealed_replay(members, manifest)
        (tmp_path / (mutation + ".zip")).write_bytes(changed)
        changed_audit = _entitle(broker, mutation, receipt["archive_sha256"], (receipt["root_sha256"],))
        with pytest.raises(ValueError, match="runtime_output|logical_dependency") as caught:
            bundles.verify_investigation_replay(changed, receipt["root_sha256"], entitlement=changed_audit)
        observations[mutation] = {"error": str(caught.value), "original_records_unchanged": True,
                                  "original_outcomes_unchanged": True, "root_sha256": receipt["root_sha256"]}
    (tmp_path / "negative-results.json").write_text(json.dumps(observations, indent=2) + "\n")


def _scope_probe_archive(snapshot):
    """Build an adversarial envelope from this test's actual owner records."""
    from devbench.analysis import bundles
    bindings, external, locks = bundles._investigation_dependencies(snapshot)
    members, logical, pending = {"investigation.json": bundles._canonical(snapshot)}, {}, list(bindings.items())
    while pending:
        path, reference = pending.pop(0)
        reference = {"uses": [], **reference}
        if path in logical:
            continue
        raw = Path(path).read_bytes()
        sha = bundles._sha(raw)
        member = "objects/" + sha
        members[member] = raw
        logical[path] = {**reference, "content_sha256": sha, "member": member, "size_bytes": len(raw)}
        pending.extend(bundles._dependent_replay_references(path, reference, raw, snapshot))
    return bundles._sealed_replay(members, {"format": bundles.INVESTIGATION_REPLAY_FORMAT,
        "audience": "restricted_audit", "capture_identity": snapshot["capture_identity"], "logical_bindings": logical,
        "external_dependencies": external, "scientific_locks": locks, "attempt_evidence": bundles._attempt_evidence(snapshot),
        "original_input_scope": "excluded_separate_entitlement_required", "protected_evaluation_scope": "excluded",
        "scientific_acceptance": "not_granted", "current_use": "requires_live_owner", "numerical_replay": "unsupported_adaptive_profile"})


@pytest.mark.wal
@pytest.mark.skipif(os.environ.get("DEVBENCH_T15_DOCKER") != "1", reason="Requires the pinned exploratory OCI image")
@pytest.mark.parametrize("entrypoint", ["api", "cli"])
@pytest.mark.parametrize("mutation", ["source_tags", "source_identity", "context_role", "policy"])
def test_original_access_metadata_is_validated_before_classification_and_decoding(tmp_path, monkeypatch, capsys, entrypoint, mutation):
    import socket
    from copy import deepcopy

    from devbench.analysis import bundles, method_sources
    from devbench.analysis.checkpoints import CheckpointStore
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
    from devbench.platform import cli
    from devharness_controlled import native_executor
    payload = b'{"fresh_original_auxiliary_input":[3,5,7]}'
    program = 'from pathlib import Path\nPath("/outputs/out.json").write_bytes(' + repr(payload) + ')\n'
    registry, runtime, source, broker = _runtime_attempt_fixture(tmp_path, program)
    access = EvidenceBroker(registry.workflow.authority)
    policy = AccessPolicy("auxiliary-policy", 1, allow_outcomes=True, allow_statistics=True)
    access.register_policy(policy)
    access.register_context("auxiliary-context", principal_id="synthetic-scientist", lineage_id=registry.workflow.lineage_id,
        role="scientist", phase="readback", policy_sha256=policy.sha256())
    payload_sha = bundles._sha(payload)
    access.register_source("auxiliary-original", content_sha256=payload_sha, roots=(source.sha256,),
        tags=("original_source_data",), classification="target_outcome")
    reservation = access.reserve("auxiliary-context", "auxiliary-original", payload, opaque=True)
    access.mark_attempted(reservation["reservation_id"])
    assert registry.workflow.dispatch("attempt", runtime, actor="synthetic-t22")["status"] == "succeeded"
    capture_id = registry.workflow.replay_identity()
    capture = _entitle(broker, "capture", capture_id["sha256"], (source.sha256,))
    snapshot = registry.workflow.replay_snapshot(entitlement=capture)
    snapshot["runtime_references"] = runtime.replay_references(registry.workflow)
    original = deepcopy(snapshot)
    raw, descriptor = _scope_probe_archive(snapshot)
    audit = _entitle(broker, "original", descriptor["archive_sha256"], (descriptor["root_sha256"],))
    decoded, original_open = [], zipfile.ZipFile.open
    def observe(self, name, *args, **kwargs):
        decoded.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
        return original_open(self, name, *args, **kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(zipfile.ZipFile, "open", observe)
        with pytest.raises(PermissionError, match="original_input_alias_forbidden"):
            bundles.verify_investigation_replay(raw, descriptor["root_sha256"], entitlement=audit)
    assert not [name for name in decoded if name.startswith("objects/")]
    if mutation == "source_tags":
        snapshot["exposures"]["sources"]["auxiliary-original"]["tags"] = []
    elif mutation == "source_identity":
        snapshot["exposures"]["sources"]["auxiliary-original"]["content_sha256"] = "f" * 64
    elif mutation == "context_role":
        snapshot["exposures"]["contexts"]["auxiliary-context"]["role"] = "auditor"
    else:
        snapshot["exposures"]["policies"][policy.sha256()]["allow_statistics"] = False
    assert snapshot["exposures"]["original_records"] == original["exposures"]["original_records"]
    assert snapshot["capture_identity"] == original["capture_identity"]
    assert snapshot["original_records"] == original["original_records"]
    changed, receipt = _scope_probe_archive(snapshot)
    package = tmp_path / (mutation + ".zip")
    package.write_bytes(changed)
    changed_audit = _entitle(broker, "changed", receipt["archive_sha256"], (receipt["root_sha256"],))
    decoded.clear()
    def forbidden(*args, **kwargs):
        pytest.fail("Metadata verification attempted execution, model/network or checkpoint mutation")
    before = _fingerprint(tmp_path)
    monkeypatch.setattr(zipfile.ZipFile, "open", observe)
    monkeypatch.setattr(native_executor, "execute_erql_plan", forbidden)
    monkeypatch.setattr(CheckpointStore, "run", forbidden)
    monkeypatch.setattr(method_sources, "_fetch_hop", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    if entrypoint == "api":
        with pytest.raises(ValueError, match="original_access_projection_changed"):
            bundles.verify_investigation_replay(package, receipt["root_sha256"], entitlement=changed_audit)
    else:
        assert cli.replay_package(["verify", "--audience", "restricted_audit", "--package", str(package),
            "--expected-root", receipt["root_sha256"], "--audit-ledger", str(broker.authority.ledger.path),
            "--audit-context", "audit", "--audit-source", "changed"]) == 1
        assert "original_access_projection_changed" in capsys.readouterr().err
    assert not [name for name in decoded if name.startswith("objects/")]
    assert _fingerprint(tmp_path) == before
