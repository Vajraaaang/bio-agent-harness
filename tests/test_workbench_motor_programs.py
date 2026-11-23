import anndata as ad
import h5py
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix

from devbench.analysis.checkpoints import digest
from devbench.analysis.workbench_motor_programs import (
    MARKERS,
    audit_motor_programs,
    compare_motor_programs,
    motor_program_csv,
)
from devbench.analysis.workbench_request_contract import MOTOR_PROGRAM_TOPIC, classify_request


def test_full_layer_scores_candidates_and_preserves_abstentions(tmp_path):
    observed = {"layers": ["counts"], "cells": 5, "genes": len(MARKERS)}
    programs = [
        ("Isl1", "Tubb3", "Phox2b", "Tbx20"),
        ("Isl2", "Elavl3", "Mnx1", "Lhx3"),
        ("Isl1", "Tubb3", "Phox2b", "Tbx20", "Mnx1", "Chat"),
        ("Isl1", "Tubb3", "Phox2b"),
        ("Isl1", "Tubb3", "Phox2b", "Tbx20"),
    ]
    matrix = csr_matrix(np.array([[int(gene in row) for gene in MARKERS] for row in programs]))
    counts = []
    for day in (9, 10):
        adata = ad.AnnData(matrix, obs=pd.DataFrame({
            "age": [f"e{day}"] * 5,
            "celltypes": ["Neuron: Motor neuron"] * 4 + ["Other"],
            "predicted_labels": ["Other"] * 5,
        }, index=[f"cell-{i}" for i in range(5)]), var=pd.DataFrame({
            "gene_ids": [f"ENSMUSG{i:011}" for i in range(len(MARKERS))],
        }, index=list(MARKERS)))
        adata.layers["counts"] = matrix.copy()
        path = tmp_path / f"day-{day}.h5ad"
        adata.write_h5ad(path)
        with h5py.File(path, "r") as handle:
            counts.append(audit_motor_programs(handle, observed, 100))
    assert [row[1] for row in counts[0]["rows"]] == [0, 1, 1, 1, 1, 1]
    profiles = [{"filename": f"day-{day}.h5ad", "sha256": str(day) * 32, "observed": observed} for day in (9, 10)]
    answers = [{"sha256": profile["sha256"], "tables": [table]}
               for profile, table in zip(profiles, counts, strict=True)]
    steps, tables, _, status = compare_motor_programs(profiles, answers)
    assert status == "completed" and steps[-1]["status"] == "needs evidence"
    assert tables[0]["rows"][0][1:] == [20.0, 20.0, 0.0]
    assert compare_motor_programs(profiles, answers[::-1])[-1] == "partial"
    answer = {"tables": [dict(counts[0], source_sha256=profiles[0]["sha256"])]}
    binding = {"result_ref": None}
    ref = digest({"binding": binding, "answer": answer})
    answer["binding"] = {"result_ref": ref}
    record = {"status": "completed", "request": {"motor_program_comparison": True},
              "description_operation": {"result_ref": ref},
              "source_profiles": profiles, "turns": [{"kind": "dataset_answer", "content": answer}]}
    exported, name = motor_program_csv(record, ref, profiles[0]["sha256"])
    assert name == "day-9-motor-candidates.csv" and len(exported.splitlines()) == 6
    with pytest.raises(KeyError, match="outside"):
        motor_program_csv(record, ref, "0" * 64)
    answer["tables"][0]["denominator"] = 1
    with pytest.raises(ValueError, match="receipt"):
        motor_program_csv(record, ref, profiles[0]["sha256"])
    counts[0]["row_codes_base64"] = "AAAAAAAA"
    with pytest.raises(ValueError, match="source totals"):
        compare_motor_programs(profiles, answers)


def test_hindbrain_label_request_has_specific_local_route():
    request = classify_request("Identify and label the different hindbrain motor neuron subtypes across days 9–11")
    assert request["descriptive_topics"] == [MOTOR_PROGRAM_TOPIC]
    assert request["remaining_action"] is None
