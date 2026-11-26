"""Coordinator-owned protected partitions and scientific ledger custody."""

from copy import deepcopy

from devbench.core.population_data import _annotation, _node, bound_query_file
from devbench.core.protected_partitions import _SHA, digest
from devbench.engine.provenance import canonical_json
from devbench.engine.scientific_authority import _lookup, _row
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict

_MAX_SOURCE = 64 * 1024 * 1024
_FITTED_ANCESTRY = {
    "training_scope",
    "fit_artifact_sha256",
    "training_observation_ids_sha256",
    "training_feature_ids_sha256",
    "training_labels_sha256",
    "configuration_sha256",
}


def _audited_partition_inventory(
    handle,
    source_sha256,
    provenance,
    count_path,
    unit_column,
    label_column,
):
    from devbench.analysis.rna_scrna_input_contract import (
        InheritedAnalysisInventory,
        audit_inherited_anndata,
    )

    audited = audit_inherited_anndata(handle, source_sha256, provenance)
    inventory = InheritedAnalysisInventory(audited["source_sha256"], audited["slots"])
    objects = {
        obj["path"]: obj for slot in inventory.slots.values() for obj in slot["objects"]
    }
    record = objects.get(count_path, {})
    ancestry = record.get("provenance") or {}
    if not record.get("count1_admissible") or _FITTED_ANCESTRY & ancestry.keys():
        raise PermissionError(
            "Held-out partitions require original measurements without fitted ancestry"
        )
    for column in (unit_column, label_column):
        annotation = (objects.get("obs/" + column) or {}).get("provenance") or {}
        if (
            annotation.get("label_origin")
            not in {"orthogonal_measurement", "independent_assignment"}
            or _FITTED_ANCESTRY & annotation.keys()
        ):
            raise PermissionError(
                "Unit and label annotations require independent unfitted provenance"
            )
    cells, genes = record["shape"]
    if cells * genes > 500_000 or min(cells, genes) < 1:
        raise ValueError(
            "Original-count partition exceeds the bounded materialization size"
        )
    return inventory, cells, genes


def _observed_partition(
    handle,
    count_path,
    cells,
    genes,
    unit_column,
    label_column,
    evaluation_units,
):
    obs = _node(handle, "obs")
    raw = _node(handle, "raw") if count_path == "raw/X" else handle
    var = _node(raw, "var")

    def read(group, column, length):
        values, _ = _annotation(_node(group, column), length, 4_000_000)
        if any(type(value) is not str for value in values):
            raise ValueError(
                "Partition identifiers and independent labels "
                "must be nonmissing strings"
            )
        return list(values)

    samples = read(obs, obs.attrs.get("_index", "_index"), cells)
    features = read(var, var.attrs.get("_index", "_index"), genes)
    units = read(obs, unit_column, cells)
    labels = read(obs, label_column, cells)
    if len(set(samples)) != cells or len(set(features)) != genes:
        raise ValueError("Partition observation and feature identifiers must be unique")
    evaluation = set(evaluation_units)
    training = set(units) - evaluation
    if not training or not evaluation or not evaluation <= set(units):
        raise ValueError(
            "Both partitions must contain distinct observed biological units"
        )
    return samples, features, units, labels, training, evaluation


def _partition_rows(matrix, samples, units, labels, selected):
    from scipy import sparse

    rows = []
    for i in sorted(range(len(samples)), key=samples.__getitem__):
        if units[i] in selected:
            values = matrix[i : i + 1, :]
            if sparse.issparse(values):
                values = values.toarray()
            rows.append(
                {
                    "observation": samples[i],
                    "unit": units[i],
                    "label": labels[i],
                    "counts": [int(value) for value in values[0]],
                }
            )
    return rows


def materialize_counts(
    source,
    source_sha256,
    provenance,
    *,
    verify_provenance,
    count_path,
    unit_column,
    label_column,
    evaluation_units,
    training_vault,
    evaluation_vault,
):
    """Rebuild two flat original-count artifacts through a verified H5AD handle.

    A trusted custody verifier authenticates the source-linked measurement and
    annotation provenance. All unused H5AD slots and attributes stay behind.
    """
    import re

    import anndata as ad
    import h5py

    if training_vault.root == evaluation_vault.root or any(
        a in b.parents
        for a, b in (
            (training_vault.root, evaluation_vault.root),
            (evaluation_vault.root, training_vault.root),
        )
    ):
        raise ValueError("Training and evaluation require separate vaults")

    provenance = deepcopy(provenance)
    if (
        not callable(verify_provenance)
        or verify_provenance(source_sha256, deepcopy(provenance)) is not True
    ):
        raise PermissionError("Partitioning requires verified source-linked provenance")
    if count_path != "X" and not re.fullmatch(r"(?:layers/[^/]+|raw/X)", count_path):
        raise ValueError("Select an exact original-count matrix path")
    with (
        bound_query_file(source, source_sha256, _MAX_SOURCE) as stream,
        h5py.File(stream, "r") as handle,
    ):
        inventory, cells, genes = _audited_partition_inventory(
            handle,
            source_sha256,
            provenance,
            count_path,
            unit_column,
            label_column,
        )
        samples, features, units, labels, training, evaluation = _observed_partition(
            handle,
            count_path,
            cells,
            genes,
            unit_column,
            label_column,
            evaluation_units,
        )
        matrix = handle
        for name in count_path.split("/"):
            matrix = _node(matrix, name)
        if isinstance(matrix, h5py.Group):
            matrix = ad.io.sparse_dataset(matrix)

        train_rows = _partition_rows(matrix, samples, units, labels, training)
        eval_rows = _partition_rows(matrix, samples, units, labels, evaluation)
        # Ignore storage layout and user-supplied split/context names in the
        # cohort key. Alternate partitions of this measurement are one family.
        feature_order = sorted(range(genes), key=features.__getitem__)
        by_observation = sorted(
            train_rows + eval_rows, key=lambda row: row["observation"]
        )
        cohort = digest(
            {
                "features": sorted(features),
                "measurements": [
                    [row["observation"], [row["counts"][i] for i in feature_order]]
                    for row in by_observation
                ],
            }
        )

        def write(vault, rows, units):
            return {
                "sha256": vault.put(
                    canonical_json({"features": features, "rows": rows})
                ),
                "units_sha256": digest(sorted(units)),
            }

        return {
            "cohort_sha256": cohort,
            "source_sha256": source_sha256,
            "inventory_sha256": digest(inventory.to_dict()),
            "count_path": count_path,
            "training": write(training_vault, train_rows, training),
            "evaluation": write(evaluation_vault, eval_rows, evaluation),
        }


def embargoed(db, identity):
    rows = db.execute(
        "SELECT * FROM scientific_records WHERE record_id=? "
        "AND kind='execution_event' LIMIT 2",
        ("embargo-" + identity,),
    ).fetchall()
    return any(_row(row)["record"]["event"] == "protected_artifact" for row in rows)


class PartitionAuthority:
    """A cohort-keyed custody record in the same SQLite authority as T11."""

    def __init__(self, ledger, cohort_sha256):
        if not isinstance(cohort_sha256, str) or not _SHA.fullmatch(cohort_sha256):
            raise ValueError("Expected an exact cohort digest")
        self.authority = ledger.scientific
        self.lineage = "partition-" + cohort_sha256

    def _record(self, db, event, identity, state):
        self.authority._append_execution_record(
            db,
            self.lineage,
            {
                "kind": "execution_event",
                "record_id": identity,
                "event": event,
                "state_sha256": digest(state),
                "state": state,
            },
        )

    def _protect(self, db, identities):
        for identity in set(identities):
            if not _SHA.fullmatch(identity):
                raise ValueError("Protected artifacts require exact content hashes")
            if not embargoed(db, identity):
                self._record(
                    db,
                    "protected_artifact",
                    "embargo-" + identity,
                    {"sha256": identity},
                )

    def seal(self, manifest):
        if (
            type(manifest) is not dict
            or set(manifest)
            != {
                "cohort_sha256",
                "source_sha256",
                "inventory_sha256",
                "count_path",
                "training",
                "evaluation",
            }
            or self.lineage != "partition-" + manifest["cohort_sha256"]
            or not all(
                type(manifest[key]) is str and _SHA.fullmatch(manifest[key])
                for key in ("source_sha256", "inventory_sha256")
            )
            or any(
                type(manifest[key]) is not dict
                or set(manifest[key]) != {"sha256", "units_sha256"}
                or not all(
                    type(value) is str and _SHA.fullmatch(value)
                    for value in manifest[key].values()
                )
                for key in ("training", "evaluation")
            )
            or manifest["training"]["units_sha256"]
            == manifest["evaluation"]["units_sha256"]
            or manifest["training"]["sha256"] == manifest["evaluation"]["sha256"]
        ):
            raise ValueError("Invalid exact protected partition manifest")
        with self.authority._execution_tx(self.lineage) as db:
            previous = _lookup(db, self.lineage, "partition-manifest")
            if previous is not None:
                if previous["record"]["state"] != manifest:
                    raise OperationConflict(
                        "The cohort already has an immutable partition"
                    )
                return self._status(db)
            self._record(db, "partition_sealed", "partition-manifest", manifest)
            protected = (manifest["source_sha256"], manifest["evaluation"]["sha256"])
            self._protect(db, protected)
            if self._prior_exposure(db, protected):
                self._record(
                    db,
                    "partition_compromised",
                    "compromised",
                    {"reason": "prior_cohort_exposure"},
                )
                return "compromised"
            return "sealed"

    @staticmethod
    def _prior_exposure(db, identities):
        if db.execute(
            """SELECT 1 FROM scientific_records r
                WHERE r.kind='adaptive_event'
                  AND json_extract(r.payload,'$.event')='dispatch'
                  AND (EXISTS (SELECT 1 FROM
                               json_each(r.payload,'$.state.envelope.pins') p
                               WHERE json_extract(p.value,'$.sha256') IN (?,?))
                       OR EXISTS (SELECT 1 FROM
                                  json_each(r.payload,'$.state.envelope.derived_source_sha256s')
                                  WHERE value IN (?,?))) LIMIT 1""",
            identities * 2,
        ).fetchone():
            return True
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='scientific_disclosures'"
        ).fetchone():
            return bool(
                db.execute(
                    """SELECT 1 FROM scientific_disclosures d
                WHERE json_extract(d.payload,'$.possible')=1 AND (
                    json_extract(d.payload,'$.content_sha256') IN (?,?)
                    OR json_extract(d.payload,'$.scope.content_sha256') IN (?,?)
                    OR EXISTS (SELECT 1 FROM json_each(d.payload,'$.scope.roots')
                               WHERE value IN (?,?))
                    OR EXISTS (SELECT 1 FROM
                               json_each(d.payload,'$.query_page.dependencies')
                               WHERE value IN (?,?))) LIMIT 1""",
                    identities * 4,
                ).fetchone()
            )
        return False

    def _consumption(self, db):
        row = db.execute(
            """SELECT * FROM scientific_records WHERE lineage_id=?
            AND kind='execution_event'
            AND (record_id='consumed' OR record_id GLOB 'consumed-*')
            ORDER BY revision DESC LIMIT 1""",
            (self.lineage,),
        ).fetchone()
        return _row(row)["record"]["state"] if row is not None else None

    def _unlaunched(self, db, token):
        lineage, attempt_id = token.get("lineage_id"), token.get("attempt_id")
        if not lineage or not attempt_id:
            return False
        attempt = self.authority._load_state(db, lineage, attempt_id)
        return (
            attempt is not None
            and attempt["token"] == token
            and attempt["execution_state"] == "noncompletion_confirmed"
            and attempt["cleanup_state"] == "confirmed"
            and attempt["external_job_id"] is None
            and attempt.get("no_container_binding") is True
        )

    def _status(self, db):
        if _lookup(db, self.lineage, "compromised"):
            return "compromised"
        consumed = self._consumption(db)
        if consumed is not None and not self._unlaunched(db, consumed):
            return "consumed"
        if _lookup(db, self.lineage, "authorized"):
            return "authorized"
        return "sealed" if _lookup(db, self.lineage, "partition-manifest") else "absent"

    def authorize(self, lineage, step, inputs, basis):
        """Freeze the evaluator's step, input pins and inherited decision basis."""
        state = self.entitlement(lineage, step, inputs, basis)
        with self.authority._execution_tx(self.lineage) as db:
            if self._status(db) != "sealed" or not self._valid_inputs(db, inputs):
                raise OperationBlocked("protected_partition_unavailable")
            self._record(db, "partition_authorized", "authorized", state)
        return digest(state)

    @staticmethod
    def entitlement(lineage, step, inputs, basis):
        if step.purpose != "protected_evaluation" or not step.held_out:
            raise PermissionError(
                "An evaluation entitlement requires a held-out evaluator"
            )
        return {
            "lineage": lineage,
            "step": step.model_dump(mode="json"),
            "inputs": {
                name: item.model_dump(mode="json") for name, item in inputs.items()
            },
            "basis": basis,
        }

    def _valid_inputs(self, db, inputs):
        manifest = _lookup(db, self.lineage, "partition-manifest")["record"]["state"]
        evaluation = manifest["evaluation"]
        return sum(
            item.sha256 == evaluation["sha256"]
            and item.unit_membership_sha256 == evaluation["units_sha256"]
            for item in inputs.values()
        ) == 1 and all(
            item.learned_scope in {"none", "partition_only"}
            and (
                item.sha256 == evaluation["sha256"]
                or item.unit_membership_sha256 == manifest["training"]["units_sha256"]
                and not embargoed(db, item.sha256)
            )
            for item in inputs.values()
        )

    def check(self, db, lineage, step, inputs, basis, *, token=None, consume=False):
        state = self.entitlement(lineage, step, inputs, basis)
        frozen = _lookup(db, self.lineage, "authorized")
        status = self._status(db)
        if (
            status not in {"authorized", "consumed"}
            or frozen["record"]["state"] != state
            or not self._valid_inputs(db, inputs)
        ):
            raise OperationBlocked("protected_partition_entitlement_mismatch")
        if status == "consumed":
            if token is None or self._consumption(db) != token:
                raise OperationBlocked("protected_partition_consumed")
        elif consume:
            previous = self._consumption(db)
            identity = (
                "consumed" if previous is None else "consumed-" + token["attempt_id"]
            )
            self._record(db, "partition_consumed", identity, token)
        return True

    def check_training_ancestry(self, db, state, inputs):
        record = _lookup(db, self.lineage, "partition-manifest")
        if record is None:
            raise OperationBlocked("protected_partition_unavailable")
        manifest = record["record"]["state"]
        base, held = manifest["training"]["sha256"], manifest["evaluation"]["sha256"]
        pending = [item.version_id for item in inputs.values() if item.sha256 != held]
        visited = set()
        while pending:
            version = pending.pop()
            if version in visited:
                continue
            visited.add(version)
            item, producer = state.artifacts[version]
            if item["sha256"] == base and item["learned_scope"] == "none":
                continue
            if (
                producer is None
                or item["learned_scope"] != "partition_only"
                or embargoed(db, item["sha256"])
            ):
                raise OperationBlocked(
                    "protected_partition_unverified_training_ancestry"
                )
            outcome = state.latest[producer]
            parent = state.dispatches[outcome["attempt_id"]]["envelope"]
            if (
                not outcome.get("accepted_record_id")
                or not parent["step"].get("oci_method_version_id")
                or parent["step"]["held_out"]
                or not parent["pins"]
            ):
                raise OperationBlocked(
                    "protected_partition_unverified_training_ancestry"
                )
            pending.extend(pin["version_id"] for pin in parent["pins"])

    def compromise(self, reason):
        with self.authority._execution_tx(self.lineage) as db:
            status = self._status(db)
            if status not in {"absent", "compromised"}:
                self._record(
                    db, "partition_compromised", "compromised", {"reason": reason}
                )

    def protect_outputs(self, identities):
        with self.authority._execution_tx(self.lineage) as db:
            self._protect(db, identities)


def seal_holdout(partition: PartitionAuthority, manifest: dict) -> str:
    """Seal a held-out manifest through the coordinator's partition authority."""
    if type(partition) is not PartitionAuthority:
        raise PermissionError(
            "Sealing a holdout requires the exact partition authority"
        )
    return partition.seal(manifest)
