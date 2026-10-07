"""Post-analysis annotation scorecards with explicit reference evidence scope.

Reference labels are consumed only after the analysis bundle has been sealed.
The separate scorecard never rewrites that bundle or reaches fitting workers.
This is a trusted coordinator workflow, not an OS boundary for hostile agents.
"""
from __future__ import annotations

from collections import Counter
import csv
import json
from pathlib import Path
import shutil
from typing import Annotated, Literal

import pandas as pd
from pydantic import Field, model_validator

from devbench.study.contracts import FrozenContract, Identifier, Sha256, content_sha256

from .checkpoints import atomic_json, sha256


INTERPRETATIONS = {
    "simulation": "simulation_label_recovery",
    "external_predictions": "concordance_with_external_predictions",
    "experimental_sort": "agreement_with_supplied_experimentally_sorted_labels",
}
UNKNOWN = {"", "uncertain", "unknown", "unassigned", "__QC_EXCLUDED__"}


class AnnotationPolicy(FrozenContract):
    """Coordinator-frozen mapping; never learned from the scored confusion matrix."""
    record_format: Literal['devbench.annotation-policy/1']='devbench.annotation-policy/1'
    policy_id: Identifier
    version: Identifier
    owner: Literal['coordinator']='coordinator'
    frozen_before_answers: Literal[True]=True
    ontology_version: Identifier
    ontology_sha256: Sha256
    labels: Annotated[tuple[Identifier, ...],Field(min_length=1,max_length=2048)]
    parents: Annotated[tuple[tuple[Identifier,Identifier], ...],Field(max_length=4096)]
    synonyms: Annotated[tuple[tuple[Identifier,Identifier], ...],Field(max_length=4096)]=()
    requested_labels: Annotated[tuple[Identifier, ...],Field(min_length=1,max_length=2048)]
    unresolved_labels: Annotated[tuple[Identifier, ...],Field(max_length=64)]=('uncertain','unknown')
    mixed_labels: Annotated[tuple[Identifier, ...],Field(max_length=64)]=('mixed',)
    coarse_credit: Annotated[float,Field(ge=0,lt=1,allow_inf_nan=False)]=.5
    finer_credit: Literal[0]=0
    unresolved_credit: Literal[0]=0
    mixed_credit: Literal[0]=0

    @model_validator(mode='after')
    def ontology(self):
        labels=set(self.labels);aliases=[alias for alias,_ in self.synonyms]
        if (len(labels)!=len(self.labels) or len(set(self.parents))!=len(self.parents)
                or len(set(self.requested_labels))!=len(self.requested_labels)
                or not set(self.requested_labels)<=labels):
            raise ValueError('Duplicate or unknown annotation policy labels')
        if any(child not in labels or parent not in labels for child,parent in self.parents):
            raise ValueError('Unknown ontology parent')
        special=(*self.unresolved_labels,*self.mixed_labels)
        if (len(set(aliases))!=len(aliases) or set(aliases)&labels or
                any(target not in labels for _,target in self.synonyms) or
                len(set(special))!=len(special) or set(special)&(labels|set(aliases))):
            raise ValueError('Duplicate, ambiguous or unknown annotation aliases')
        parent_map={node:set() for node in labels}
        for child,parent in self.parents:parent_map[child].add(parent)
        for node in labels:
            visited=set();pending=list(parent_map[node])
            while pending:
                item=pending.pop()
                if item==node:raise ValueError('Annotation ontology contains a cycle')
                if item in visited:continue
                visited.add(item);pending.extend(parent_map[item])
        return self


def annotation_policy_sha256(policy):
    return content_sha256(policy.model_dump(mode='json'))


def score_labels_with_policy(reference, predictions, *, evidence_type, policy, expected_policy_sha256):
    """Trusted post-answer comparison with an externally retained policy digest.

    Hierarchy compatibility is scoped concordance, not independently established
    cell identity. Finer predictions need separate evidence and receive no credit.
    """
    policy=AnnotationPolicy.model_validate_json(policy.model_dump_json())
    if annotation_policy_sha256(policy)!=expected_policy_sha256:
        raise ValueError('Frozen annotation policy digest mismatch')
    legacy=score_labels(reference,predictions,evidence_type=evidence_type)
    aliases=dict(policy.synonyms);labels=set(policy.labels)
    def canonical(label):return aliases.get(label,label)
    if any(canonical(str(label)) not in policy.requested_labels for label in reference):
        raise ValueError('Reference does not match prespecified annotation granularity')
    if any(str(label) not in labels|set(aliases)|set(policy.unresolved_labels)|set(policy.mixed_labels) for label in predictions):
        raise ValueError('Prediction contains unknown annotation labels')
    parent_map={node:set() for node in labels}
    for child,parent in policy.parents:parent_map[child].add(parent)
    def ancestors(node):
        result=set();pending=list(parent_map[node])
        while pending:
            item=pending.pop()
            if item not in result:result.add(item);pending.extend(parent_map[item])
        return result
    relations=Counter({key:0 for key in ('exact','synonymous','compatible_coarser',
        'compatible_finer','contradictory','unresolved','mixed','excluded')})
    credit=0.;full=0
    for cell,label in reference.items():
        expected=canonical(str(label))
        if cell not in predictions.index:relation='excluded'
        else:
            raw=str(predictions.loc[cell])
            if raw in policy.unresolved_labels:relation='unresolved'
            elif raw in policy.mixed_labels:relation='mixed'
            else:
                predicted=canonical(raw)
                if predicted==expected:
                    relation='exact' if raw==str(label) else 'synonymous';credit+=1;full+=1
                elif predicted in ancestors(expected):relation='compatible_coarser';credit+=policy.coarse_credit
                elif expected in ancestors(predicted):relation='compatible_finer'
                else:relation='contradictory'
        relations[relation]+=1
    return {'record_format':'devbench.annotation-policy-score/1','policy_sha256':expected_policy_sha256,
        'ontology_version':policy.ontology_version,'ontology_sha256':policy.ontology_sha256,
        'requested_labels':list(policy.requested_labels),'legacy_exact':legacy,'relations':dict(relations),
        'policy_credit_fraction':credit/len(reference),'full_credit_at_requested_granularity_fraction':full/len(reference),
        'unsupported_finer_predictions':relations['compatible_finer'],
        'denominator_scope':'all reference observations including abstentions, mixed labels and exclusions',
        'scientific_acceptance_gate':None,'expert_validation':'unmet','population_inference_performed':False}


def _implementation_hashes():
    root = Path(__file__).parent
    return {name: sha256(root/name) for name in ("scorecard.py","bundles.py","streaming.py","checkpoints.py")}


def score_labels(reference, predictions, *, evidence_type):
    if evidence_type not in INTERPRETATIONS:
        raise ValueError("Declare simulation, external_predictions or experimental_sort evidence")
    if (reference.empty or not reference.index.is_unique or not predictions.index.is_unique
            or not predictions.index.isin(reference.index).all()):
        raise ValueError("reference_prediction_identity_mismatch")
    if reference.isna().any() or any(str(v).strip().lower() in {x.lower() for x in UNKNOWN} for v in reference):
        raise ValueError("unknown_reference_labels_cannot_be_scored_as_ground_truth")
    if predictions.isna().any() or any(not str(v).strip() or str(v)=="__QC_EXCLUDED__" for v in predictions):
        raise ValueError("Missing prediction labels must be explicitly recorded as uncertain")
    reference = reference.astype(str)
    predictions = predictions.astype(str)
    excluded = ~reference.index.isin(predictions.index)
    aligned = predictions.reindex(reference.index, fill_value="__QC_EXCLUDED__")
    called = ~aligned.str.lower().isin({x.lower() for x in UNKNOWN})
    agreement = aligned == reference
    correct = int(agreement.sum())
    counts = Counter(zip(reference, aligned))
    per_label = []
    for label in sorted(reference.unique()):
        support = int((reference == label).sum())
        TP = counts[(label, label)]
        predicted = int((aligned == label).sum())
        precision = TP/predicted if predicted else 0.0
        recall = TP/support
        per_label.append({"reference_label": label, "reference_cells": support,
            "called_cells_with_label": predicted, "correct": TP, "precision": precision,
            "recall_all_reference_cells": recall,
            "F1_all_reference_cells": 2*precision*recall/(precision+recall) if precision+recall else 0.0,
            "QC_excluded": int(((reference == label)&excluded).sum())})
    retained = len(predictions)
    return {"interpretation": INTERPRETATIONS[evidence_type], "reference_cells": len(reference),
        "retained_cells": retained, "QC_excluded_reference_cells": int(excluded.sum()),
        "uncertain_retained_cells": int((~called & ~excluded).sum()), "called_cells": int(called.sum()),
        "retention_fraction": retained/len(reference),
        "call_coverage_of_retained_cells": float(called.sum()/retained) if retained else None,
        "correct_label_fraction_of_all_reference_cells": correct/len(reference),
        "label_agreement_retained_including_uncertain": correct/retained if retained else None,
        "label_agreement_on_called_cells": float(agreement[called].mean()) if called.any() else None,
        "macro_F1_all_reference_cells": sum(v["F1_all_reference_cells"] for v in per_label)/len(per_label),
        "per_label": per_label,
        "confusion_counts": [{"reference": expected, "prediction": predicted, "cells": number}
                             for (expected,predicted),number in sorted(counts.items())],
        "denominator_scope": "QC exclusions and abstentions remain in all-reference-cell denominators",
        "confidence_intervals": None, "population_inference_performed": False}


def _read_manifest(path):
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size > 128_000:
        raise ValueError("Use a bounded regular absolute reference-manifest file")
    def unique_pairs(pairs):
        result = {}
        for key,value in pairs:
            if key in result:
                raise ValueError("Duplicate reference-manifest keys are forbidden")
            result[key]=value
        return result
    manifest = json.loads(path.read_text(), object_pairs_hook=unique_pairs)
    required = {"format", "evidence_type", "labels_file", "labels_sha256", "dataset_id",
                "source_description", "full_input_coverage_required"}
    if (not required.issubset(manifest) or manifest["format"] != "devbench.analysis.annotation-reference/1"
            or manifest["evidence_type"] not in INTERPRETATIONS
            or type(manifest["full_input_coverage_required"]) is not bool
            or any(not isinstance(manifest[k],str) or not manifest[k].strip()
                   for k in ("dataset_id", "source_description", "labels_file", "labels_sha256"))):
        raise ValueError("Invalid annotation reference manifest")
    if manifest["evidence_type"] == "experimental_sort" and any(
            not isinstance(manifest.get(k),str) or not manifest[k].strip()
            for k in ("experimental_method", "source_url")):
        raise ValueError("Experimental labels require their stated sorting method and source citation")
    relative = Path(manifest["labels_file"])
    labels = path.parent/relative
    if (relative.is_absolute() or ".." in relative.parts or labels.is_symlink()
            or not labels.resolve().is_relative_to(path.parent.resolve()) or not labels.is_file()
            or labels.stat().st_size > 32*1024**2):
        raise ValueError("Reference labels must be a bounded regular CSV beside the manifest")
    if sha256(labels) != manifest["labels_sha256"]:
        raise ValueError("reference_labels_checksum_mismatch")
    return manifest, labels


def _seal_scorecard(output, parent_root, *, status):
    paths = sorted(p for p in output.rglob("*") if p.is_file()
                   and p not in {output/"receipts.json",output/"root-receipt.json"})
    manifest = {"format": "devbench.analysis.annotation-scorecard/1", "parent_analysis_root": parent_root,
                "status": status, "artifacts": {p.relative_to(output).as_posix(): sha256(p) for p in paths},
                "scope": "tamper_evidence_against_a_separately_retained_root_not_authenticated_approval"}
    atomic_json(output/"receipts.json", manifest)
    receipt = {"manifest_sha256": sha256(output/"receipts.json"), "artifacts": len(paths)}
    atomic_json(output/"root-receipt.json", receipt)
    return receipt


def verify_scorecard(output, expected_root):
    output = Path(output)
    if not output.is_absolute() or output.is_symlink() or sha256(output/"receipts.json") != expected_root:
        raise ValueError("scorecard_root_mismatch")
    manifest = json.loads((output/"receipts.json").read_text())
    if manifest.get("format") != "devbench.analysis.annotation-scorecard/1":
        raise ValueError("unknown_scorecard_format")
    for name, expected in manifest["artifacts"].items():
        path = output/name
        if (Path(name).is_absolute() or ".." in Path(name).parts or path.is_symlink()
                or not path.resolve().is_relative_to(output.resolve()) or not path.is_file() or sha256(path) != expected):
            raise ValueError("scorecard_artifact_mismatch:"+name)
    observed = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
    if observed != set(manifest["artifacts"]) | {"receipts.json", "root-receipt.json"}:
        raise ValueError("scorecard_inventory_mismatch")
    return {"verified": True, "artifacts": len(manifest["artifacts"]),
            "parent_analysis_root": manifest["parent_analysis_root"], "status": manifest["status"]}


def score_bundle(bundle, expected_root, reference_manifest, output, *, expected_reference_sha256=None):
    from .bundles import verify_bundle
    from .streaming import read_table

    bundle, reference_manifest, output = map(Path, (bundle, reference_manifest, output))
    if (not bundle.is_absolute() or not output.is_absolute() or output.is_relative_to(bundle)
            or output.resolve().is_relative_to(bundle.resolve()) or output.exists()):
        raise ValueError("Scorecards require a new absolute directory outside the sealed analysis")
    output.mkdir(parents=True)
    try:
        implementation = _implementation_hashes()
        admitted = verify_bundle(bundle, expected_root)
        if admitted["status"] != "completed_review_passed":
            raise ValueError("Annotation scoring requires an analysis with passing numerical review")
        observed_ref_hash = sha256(reference_manifest)
        if expected_reference_sha256 is not None and observed_ref_hash != expected_reference_sha256:
            raise ValueError("reference_manifest_changed_during_analysis")
        reference, labels_file = _read_manifest(reference_manifest)
        recorded_reference = json.loads((bundle/"parameters.json").read_text()).get("reference_manifest_sha256")
        if recorded_reference is not None and recorded_reference != observed_ref_hash:
            raise ValueError("reference_manifest_differs_from_prespecified_analysis_digest")
        with labels_file.open(newline="") as handle:
            if next(csv.reader(handle), None) != ["cell_id", "label"]:
                raise ValueError("Reference CSV header must be cell_id,label")
        labels = read_table(labels_file)
        if (list(labels.columns) != ["label"] or labels.empty or not labels.index.is_unique
                or labels.index.isna().any()):
            raise ValueError("Use a two-column cell_id,label CSV with unique cell identities")
        qc = read_table(bundle/"checkpoints/qc/metrics.csv")
        predictions = read_table(bundle/"checkpoints/annotation/predictions.csv")
        if not predictions.index.equals(qc.index[qc.qc_pass.to_numpy(dtype=bool)]):
            raise ValueError("annotation_prediction_identity_mismatch")
        if (not labels.index.isin(qc.index).all() or (reference["full_input_coverage_required"]
                and set(labels.index) != set(qc.index))):
            raise ValueError("reference_input_identity_coverage_mismatch")
        selected = predictions.loc[predictions.index.isin(labels.index), "cell_type"]
        metrics = score_labels(labels["label"], selected, evidence_type=reference["evidence_type"])
        result = {"format": "devbench.analysis.annotation-scorecard/1", "status": "scored",
            "analysis_root_sha256": expected_root, "analysis_execution_tier": admitted["execution_tier"],
            "reference_manifest_sha256": observed_ref_hash, "reference_labels_sha256": reference["labels_sha256"],
            "reference_evidence_type": reference["evidence_type"], "dataset_id": reference["dataset_id"],
            "reference_status": "coordinator_declared_not_independently_authenticated",
            "reference_timing": "digest_recorded_before_analysis" if recorded_reference else "posthoc_reference_not_prespecified",
            "replay_reference_manifest": "reference/"+reference_manifest.name,
            "implementation_sha256": implementation,
            "input_cells": len(qc), "reference_coverage_of_input": len(labels)/len(qc),
            "unlabelled_input_cells": len(qc)-len(labels), "metrics": metrics,
            "population_inference_performed": False, "clinical_validation_claimed": False,
            "scientific_acceptance_gate": None,
            "limits": ["A score is not an automatic scientific pass or qualified clinical validation.",
                "Reference provenance is declared by the coordinator; its citation is not independently adjudicated here.",
                "Cells are not independent biological replicates; no per-cell confidence interval is asserted.",
                "No label mapping is learned from the scored reference or fitted confusion matrix.",
                "Truth is read after the analysis is sealed in this trusted workflow; this is not hostile-agent OS isolation."]}
        # Preserve manifest bytes and its relative label path, so moving the
        # scorecard does not change a digest registered before fitting.
        reference_copy = output/"reference"/reference_manifest.name
        labels_copy = output/"reference"/reference["labels_file"]
        reference_copy.parent.mkdir(parents=True)
        labels_copy.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(reference_manifest, reference_copy)
        shutil.copyfile(labels_file, labels_copy)
        shutil.copyfile(Path(__file__), output/"scorer-source.py")
        atomic_json(output/"scoring-source.json", {"implementation_sha256": implementation,
            "scope": "filesystem_source_hashes_checked_before_and_after_scoring; not_platform_attestation"})
        atomic_json(output/"environment.json", _observed_versions())
        # Detect mutation during reads/copying instead of assigning an old hash to new labels.
        if (sha256(reference_manifest) != observed_ref_hash or sha256(reference_copy) != observed_ref_hash
                or sha256(labels_file) != reference["labels_sha256"] or sha256(labels_copy) != reference["labels_sha256"]):
            raise ValueError("reference_changed_during_scoring")
        if _implementation_hashes() != implementation or sha256(output/"scorer-source.py") != implementation["scorecard.py"]:
            raise ValueError("scoring_implementation_changed_during_measurement")
        atomic_json(output/"scorecard.json", result)
        _seal_scorecard(output, expected_root, status="scored")
        return result
    except Exception as error:
        atomic_json(output/"failure.json", {"type": type(error).__name__, "message": str(error)[:2000],
            "status": "not_scored", "parent_analysis_root": expected_root})
        _seal_scorecard(output, expected_root, status="not_scored")
        raise


def _observed_versions():
    import platform
    from importlib.metadata import version
    return {"python": platform.python_version(), "platform": platform.platform(),
            "packages": {name: version(name) for name in ("pandas","numpy","anndata")},
            "scope": "observed_scoring_environment_not_the_original_analysis_runtime"}


def scoring_worker(bundle, expected_root, reference_manifest, output, expected_reference_sha256=None):
    from .resources import configure_threads, StageMeasurement
    configure_threads()
    measured = StageMeasurement("annotation_reference_scoring")
    try:
        with measured:
            result = score_bundle(bundle, expected_root, reference_manifest, output,
                                  expected_reference_sha256=expected_reference_sha256)
    finally:
        if (Path(output)/"receipts.json").is_file():
            atomic_json(Path(output).with_name(Path(output).name+".measurement.json"), {
                "scorecard_manifest_sha256": sha256(Path(output)/"receipts.json"),
                "measurement": measured.result,
                "scope": "separate_supervised_scoring_process_not_original_analysis_resource_use"})
    return {"scorecard_directory": output, "status": result["status"],
            "interpretation": result["metrics"]["interpretation"], "measurement": measured.result}
