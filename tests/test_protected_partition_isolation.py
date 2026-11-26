import hashlib

import pytest

from devbench.core.protected_partitions import PartitionVault


def test_vault_checks_content_and_refuses_symlink(tmp_path):
    vault = PartitionVault(tmp_path.resolve() / "vault")
    digest = vault.put(b"synthetic training")
    assert digest == hashlib.sha256(b"synthetic training").hexdigest()
    with vault.open(digest) as source:
        assert source.read() == b"synthetic training"
    path = vault.root / digest
    path.unlink()
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"synthetic training")
    path.symlink_to(sentinel)
    with pytest.raises((OSError, ValueError)):
        with vault.open(digest):
            pytest.fail("opened a symlink")


import json

from devbench.core.protected_partitions import digest
from devbench.engine.partition_authority import PartitionAuthority, embargoed, materialize_counts
from devbench.engine.workflow_operations import OperationConflict
from tests.test_investigation_invalidation import artifact, link, make, step, wal


def make_vaults(tmp_path):
    return (PartitionVault(tmp_path.resolve() / "train"), PartitionVault(tmp_path.resolve() / "holdout"))


def test_h5ad_donor_partition_strips_full_cohort_state_and_denies_links(tmp_path):
    ad = pytest.importorskip("anndata")
    import h5py
    import numpy as np
    import pandas as pd
    from scipy.sparse import csr_matrix

    training, evaluation = make_vaults(tmp_path)
    source = tmp_path.resolve() / "synthetic.h5ad"
    counts = csr_matrix([[1, 2], [3, 2], [9, 5], [7, 6], [2, 4]])
    data = ad.AnnData(np.log1p(counts.toarray()), obs=pd.DataFrame({
        "donor": ["a", "a", "b", "b", "c"], "label": ["left", "left", "right", "right", "right"],
        "cluster": ["FULL_COHORT"] * 5,
    }, index=["s1", "s2", "held1", "held2", "s5"]), var=pd.DataFrame(index=["g1", "g2"]))
    data.layers["counts"] = counts
    data.raw = ad.AnnData(counts, obs=data.obs, var=data.var)
    data.obsm["X_pca"] = np.ones((5, 2))
    data.obsp["connectivities"] = csr_matrix(np.ones((5, 5)))
    data.uns["scaling"] = {"mean": [4, 3]}
    data.write_h5ad(source)

    def build(count_path, eval_units=("b",), verified=True):
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        provenance = [
            {"path": name, "artifact_sha256": source_sha, "source": "synthetic fixture", **fields}
            for name, fields in {
                "X": {"representation": "normalized_expression", "training_scope": "full_cohort"},
                "obsm/X_pca": {"training_scope": "full_cohort", "fit_artifact_sha256": digest("pca")},
                "layers/counts": {"representation": "original_counts"},
                "raw/X": {"representation": "original_counts"},
                "obs/donor": {"label_origin": "independent_assignment"},
                "obs/label": {"label_origin": "orthogonal_measurement"},
            }.items()]
        return materialize_counts(source, source_sha, provenance, verify_provenance=lambda *_: verified,
            count_path=count_path, unit_column="donor", label_column="label", evaluation_units=eval_units,
            training_vault=training, evaluation_vault=evaluation)

    manifest = build("layers/counts")
    with training.open(manifest["training"]["sha256"]) as stream:
        raw = stream.read()
        assert b"held1" not in raw and b"FULL_COHORT" not in raw and b"X_pca" not in raw
        assert [row["unit"] for row in json.loads(raw)["rows"]] == ["a", "a", "c"]
    with evaluation.open(manifest["evaluation"]["sha256"]) as stream:
        assert {row["observation"] for row in json.load(stream)["rows"]} == {"held1", "held2"}
    with pytest.raises(FileNotFoundError):
        with training.open(manifest["evaluation"]["sha256"]):
            pass
    assert build("raw/X")["cohort_sha256"] == manifest["cohort_sha256"]
    assert build("layers/counts", eval_units=("a",))["cohort_sha256"] == manifest["cohort_sha256"]
    with pytest.raises(PermissionError, match="fitted ancestry"):
        build("X")
    with pytest.raises(PermissionError, match="verified"):
        build("layers/counts", verified=False)
    with h5py.File(source, "r+") as handle:
        handle["obsm"]["external"] = h5py.ExternalLink("/unbound/synthetic-sentinel.h5", "/values")
    with pytest.raises(ValueError, match="external or symbolic"):
        build("layers/counts")


def sealed_fixture(ledger, training, evaluation):
    cohort = digest("synthetic cohort")
    manifest = {"cohort_sha256": cohort, "source_sha256": digest("full source"),
        "inventory_sha256": digest("inventory"), "count_path": "X",
        "training": {"sha256": training, "units_sha256": digest(["a", "c"])},
        "evaluation": {"sha256": evaluation, "units_sha256": digest(["b"])}}
    partition = PartitionAuthority(ledger, cohort)
    assert partition.seal(manifest) == "sealed"
    return partition, manifest


@wal
def test_embargo_entitlement_and_contamination_are_content_bound(tmp_path):
    from devbench.analysis.episode_evidence_queries import query_result
    from devbench.analysis.evidence_access import (
        AccessDenied,
        AccessPolicy,
        EvidenceBroker,
    )
    from devbench.engine.methods import MethodService
    from devbench.engine.partition_authority import seal_holdout
    from devbench.engine.workflow_operations import LEDGER_NAME
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

    root = tmp_path.resolve()
    flow = make(root, name=LEDGER_NAME)
    methods = MethodService(root)
    source = root / "evaluation.json"
    source.write_text('{"labels": ["synthetic"]}')
    saved = methods.import_artifact(source, artifact_type="json", actor="tester")
    partition, manifest = sealed_fixture(
        flow.ledger, digest("training"), saved["sha256"]
    )
    held = artifact("evaluation", permitted_uses=("exploratory", "protected_evaluation")).model_copy(
        update={"sha256": manifest["evaluation"]["sha256"], "unit_membership_sha256": manifest["evaluation"]["units_sha256"]})
    flow.register_artifact(held)
    flow.insert(step("snoop", link("data", "evaluation", immutable=True)))
    assert flow.status()["blocked"]["snoop"] == "adaptive_protected_partition_mount_denied"
    flow.register_artifact(held.model_copy(update={"version_id": "renamed-evaluation"}))
    flow.insert(step("copy", link("data", "renamed-evaluation", immutable=True)))
    assert flow.status()["blocked"]["copy"] == "adaptive_protected_partition_mount_denied"

    protected = AdaptiveWorkflow(flow.ledger, "study", partition_check=partition)
    evaluator = step("evaluator", link("held", "evaluation", immutable=True), held_out=True,
                     purpose="protected_evaluation", oci_method_version_id=digest("method"))
    protected.insert(evaluator)
    assert "entitlement" in protected.status()["blocked"]["evaluator"]
    with pytest.raises(PermissionError, match="protected partition custody"):
        flow.authorize_protected_step("evaluator")
    protected.authorize_protected_step("evaluator")
    assert "evaluator" in protected.ready()
    with pytest.raises(PermissionError, match="exact evaluator entitlement"):
        methods.artifact_path(saved["artifact_id"])

    token = {"attempt": "synthetic"}
    with partition.authority._execution_tx(partition.lineage) as db:
        assert partition.check(db, "study", evaluator, {"held": held}, {}, token=token, consume=True)
        assert embargoed(db, held.sha256)
    assert "consumed" in protected.status()["blocked"]["evaluator"]
    copied = PartitionAuthority(flow.ledger, manifest["cohort_sha256"])
    assert seal_holdout(copied, manifest) == "consumed"

    class AlternatePartition(PartitionAuthority):
        pass

    with pytest.raises(PermissionError, match="exact partition authority"):
        alternate = AlternatePartition(flow.ledger, manifest["cohort_sha256"])
        seal_holdout(alternate, manifest)
    with pytest.raises(OperationConflict, match="immutable"):
        seal_holdout(copied, {**manifest, "training": {**manifest["training"], "sha256": digest("alternate")}})

    broker = EvidenceBroker(flow.authority)
    policy = AccessPolicy("read", 1, allow_outcomes=True, allow_statistics=True)
    broker.register_policy(policy)
    broker.register_context("read", principal_id="analyst", lineage_id="study", role="scientist",
                            phase="readback", policy_sha256=policy.sha256())
    broker.register_source("innocent", content_sha256=digest("innocent"), roots=(digest("innocent root"),))
    with pytest.raises(AccessDenied, match="query inputs"):
        query_result({}, {"mode": "counts", "gene_ids": ["g1"]},
                     bindings={"counts_sha256": held.sha256, "counts_path": "/synthetic/no-read.csv"},
                     evidence_delivery=broker.delivery("read", "innocent"))
    broker.register_source("copied-bytes", content_sha256=held.sha256, roots=(digest("new root"),))
    broker.register_source("derived-plot", content_sha256=digest("plot"), roots=(held.sha256,))
    for source in ("copied-bytes", "derived-plot"):
        with pytest.raises(AccessDenied, match="Protected evaluation"):
            broker.query_admission("read", source, digest("query"), None)
    copied.compromise("verified exposure")
    assert seal_holdout(partition, manifest) == "compromised"


@wal
def test_declared_training_scope_without_verified_ancestry_is_blocked(tmp_path):
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

    flow = make(tmp_path)
    partition, manifest = sealed_fixture(flow.ledger, digest("training"), digest("evaluation"))
    guarded = AdaptiveWorkflow(flow.ledger, "study", partition_check=partition)
    held = artifact("held", permitted_uses=("protected_evaluation",)).model_copy(
        update={"sha256": manifest["evaluation"]["sha256"], "unit_membership_sha256": manifest["evaluation"]["units_sha256"]})
    fitted = artifact("full-cohort-pca", learned_scope="full_cohort",
                      permitted_uses=("protected_evaluation",)).model_copy(
        update={"unit_membership_sha256": manifest["training"]["units_sha256"]})
    mislabeled = fitted.model_copy(update={"version_id": "unproven", "learned_scope": "partition_only"})
    for item in (held, fitted, mislabeled):
        guarded.register_artifact(item)
    for name in (fitted.version_id, mislabeled.version_id):
        guarded.insert(step("score-" + name, link("held", "held", immutable=True),
                            link("pca", name, immutable=True), shared_units=False, held_out=True,
                            purpose="protected_evaluation", oci_method_version_id=digest("method")))
    assert "full_cohort" in guarded.status()["blocked"]["score-full-cohort-pca"]
    assert "ancestry" in guarded.status()["blocked"]["score-unproven"]


@wal
def test_no_binding_recovery_releases_one_shot_for_exact_retry(tmp_path, monkeypatch):
    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.engine.methods import MethodService
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from devbench.platform.workflow_entrypoints import LEDGER_NAME

    base = make(tmp_path.resolve(), name=LEDGER_NAME)
    partition, manifest = sealed_fixture(base.ledger, digest("training"), digest("evaluation"))
    flow = AdaptiveWorkflow(base.ledger, "study", partition_check=partition)
    held = artifact("held-retry", permitted_uses=("protected_evaluation",)).model_copy(update={
        "sha256": manifest["evaluation"]["sha256"], "unit_membership_sha256": manifest["evaluation"]["units_sha256"]})
    flow.register_artifact(held)
    flow.insert(step("evaluate", link("held", held.version_id, immutable=True), held_out=True,
                     purpose="protected_evaluation", oci_method_version_id=digest("method")))
    flow.authorize_protected_step("evaluate")
    worker = ExploratoryOCI(MethodService(tmp_path.resolve()), flow.ledger, (), partition=partition)
    monkeypatch.setattr(ExploratoryOCI, "validate", lambda *_: None)

    def interrupted(self, envelope, *, token):
        directory = self._directory(token)
        directory.mkdir()
        (directory / "lease").touch()
        if len(flow.authority.attempts("study")) > 1:
            self._write(directory / "binding.json", {"event": "container_created", "container_id": "c" * 64})
        raise RuntimeError("synthetic interruption")

    monkeypatch.setattr(ExploratoryOCI, "run", interrupted)
    with pytest.raises(RuntimeError):
        flow.dispatch("evaluate", worker, actor="tester")
    token = flow.authority.attempts("study")[0]["token"]
    assert worker.recover(flow, token)["cleanup_state"] == "confirmed"
    assert partition.seal(manifest) == "authorized"
    assert "evaluate" in flow.ready()
    with pytest.raises(RuntimeError):
        flow.dispatch("evaluate", worker, actor="tester")
    retry = next(attempt["token"] for attempt in flow.authority.attempts("study") if attempt["token"] != token)
    with pytest.raises((ValueError, RuntimeError)):
        worker.recover(flow, retry)
    assert partition.seal(manifest) == "consumed"
    assert flow.authority.attempt(retry)["execution_state"] == "submission_uncertain"


@wal
def test_embargo_lookup_uses_compatibly_installed_index(tmp_path):
    flow = make(tmp_path.resolve())
    identity = digest("indexed sentinel")
    partition = PartitionAuthority(flow.ledger, digest("index cohort"))
    with flow.ledger._database() as db:
        db.execute("DROP INDEX IF EXISTS scientific_records_record_id_kind")
    partition.protect_outputs((identity,))
    with flow.ledger._database() as db:
        assert embargoed(db, identity)
        plan = db.execute("EXPLAIN QUERY PLAN SELECT * FROM scientific_records "
                          "WHERE record_id=? AND kind='execution_event' LIMIT 2",
                          ("embargo-" + identity,)).fetchall()
        assert any("scientific_records_record_id_kind" in row[3] and "SEARCH" in row[3] for row in plan)
