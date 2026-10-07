"""Frozen public-data plans and protected preparation; no implicit execution."""
from __future__ import annotations

import csv
import gzip
import hashlib
import hmac
import json
from pathlib import Path
import secrets
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .contracts import CONDITIONS, FrozenContract, Identifier, RunBudget, Sha256, content_sha256, canonical_json_bytes

DatasetId = Literal["developmental_general", "developmental_markers", "seqc_controls"]
DATASETS = {"developmental_general": ("E-MTAB-6798", "bulk_brain_subset"),
            "developmental_markers": ("E-MTAB-6798", "bulk_brain_markers"),
            "seqc_controls": ("GSE47774", "seqc")}


class PublicStudyPlan(FrozenContract):
    record_format: Literal["devbench.public-study/1"] = "devbench.public-study/1"
    study_id: Identifier
    blinding_nonce: Sha256 = Field(repr=False)
    datasets: tuple[DatasetId, ...] = Field(min_length=1,max_length=3)
    conditions: tuple[Literal["baseline"],Literal["optional"],Literal["enforced"]] = CONDITIONS
    repetitions: Annotated[int,Field(ge=1,le=100)] = 1
    requested_model: Annotated[str,Field(min_length=1,max_length=200)] | None = None
    budget: RunBudget = Field(default_factory=lambda: RunBudget(cpu_threads=1))
    endogenous_genes: Annotated[int,Field(ge=32,le=2000)] = 512
    calibration_per_ratio_group: Annotated[int,Field(ge=2,le=10)] = 6
    minimum_evaluation_control_counts: Annotated[int,Field(ge=1,le=10000)] = 20
    maximum_control_log2_error: Annotated[float,Field(gt=0,allow_inf_nan=False)] = 0.5
    developmental_absolute_tolerance: Annotated[float,Field(gt=0,allow_inf_nan=False)] = 1e-6
    calibration_status: Literal["provisional_uncalibrated"] = "provisional_uncalibrated"
    status: Literal["planned_not_run"] = "planned_not_run"

    @model_validator(mode="after")
    def distinct_sources(self):
        if len(set(self.datasets))!=len(self.datasets):
            raise ValueError("Public dataset selections must be distinct")
        return self

    def sha256(self):
        return content_sha256(self.model_dump(mode="json"))


def default_public_plan(study_id="public-three-arm-v1", **kwargs):
    kwargs.setdefault("datasets", tuple(DATASETS))
    return PublicStudyPlan(study_id=study_id, blinding_nonce=secrets.token_hex(32),
                           **kwargs)


def public_identity(plan, domain, value):
    return hmac.new(bytes.fromhex(plan.blinding_nonce), canonical_json_bytes({"domain":domain,"value":value}), hashlib.sha256).hexdigest()


def schedule_public_attempts(plan):
    plan = PublicStudyPlan.model_validate_json(plan.model_dump_json())
    rows=[]
    for block,dataset in enumerate(plan.datasets):
        for repetition in range(1,plan.repetitions+1):
            shift=(block+repetition-1)%3
            conditions=list(plan.conditions[shift:]+plan.conditions[:shift])
            for condition in conditions:
                rows.append({"attempt_id":"public-attempt-"+public_identity(plan,"attempt",[dataset,repetition,condition])[:24],
                    "task_id":public_identity(plan,"task",dataset),"dataset_id":dataset,"condition":condition,
                    "repetition":repetition,"plan_identity":plan.sha256(),"execution_order":len(rows)+1})
    return rows


class PreparedPublicTask(FrozenContract):
    record_format: Literal["devbench.prepared-public-task/1"] = "devbench.prepared-public-task/1"
    root: str
    dataset_id: DatasetId
    source_id: Identifier
    task_id: Sha256
    plan_identity: Sha256
    input_sha256: Sha256
    reference_sha256: Sha256
    preparation_sha256: Sha256
    independence_id: Identifier


def _write(path, value):
    raw=canonical_json_bytes(value)
    if len(raw)>16_000_000:
        raise ValueError("Public preparation JSON exceeds its bound")
    with path.open("xb") as stream:stream.write(raw)
    return hashlib.sha256(raw).hexdigest()


def _materialize(plan, dataset_id, output, values, genes, metadata, calibration, reference, sources, *, allow_data_execution=False):
    """Trusted internal writer; caller has already authorized and loaded inputs."""
    if allow_data_execution is not True:
        raise PermissionError("Public preparation is disabled")
    plan=PublicStudyPlan.model_validate_json(plan.model_dump_json())
    if dataset_id not in plan.datasets:
        raise ValueError("Dataset is absent from the frozen public plan")
    from .permissions import require_protected_storage
    from .public_io import input_inventory, load_public_inputs
    from .biology_datasets import _seal
    import numpy as np
    import pandas as pd
    from devharness_core.storage import ChunkedExpressionStore
    output=Path(output)
    if any(path.is_symlink() for path in (output,*output.parents)):
        raise ValueError("Prepared public paths must not contain symlinks")
    output=require_protected_storage(output)
    if (not isinstance(values,np.ndarray) or values.shape!=(len(genes),len(metadata)) or values.nbytes>16_000_000
            or not np.isfinite(values).all() or np.any(values<0)):
        raise ValueError("Public preparation requires a bounded aligned nonnegative matrix")
    samples=[row["sample_id"] for row in metadata]
    if len(set(samples))!=len(samples) or len(set(genes))!=len(genes):
        raise ValueError("Prepared public axes must be distinct")
    kind="seqc_normalization" if dataset_id=="seqc_controls" else "developmental_observation"
    if kind=="seqc_normalization" and values.dtype.kind not in "iu":
        raise ValueError("SEQC preparation does not convert non-count values into counts")
    if kind=="developmental_observation" and values.dtype.kind!="f":
        raise ValueError("Developmental RPKM requires floating-point storage")
    output.mkdir(mode=0o700,parents=True,exist_ok=False)
    inputs=output/"inputs";inputs.mkdir(mode=0o700)
    private=output/"private";private.mkdir(mode=0o700)
    frame=pd.DataFrame(values,index=genes,columns=samples,copy=False)
    with ChunkedExpressionStore.from_dataframe_to_zarr(frame,inputs/"counts.zarr",chunk_shape=(min(128,len(genes)),min(32,len(samples)))):pass
    with (inputs/"metadata.csv").open("x",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(metadata[0]));writer.writeheader();writer.writerows(metadata)
    task={"record_format":"devbench.public-inputs/1","kind":kind,"task_id":public_identity(plan,"task",dataset_id),
        "source_id":DATASETS[dataset_id][0],"measurement":"raw_counts" if kind=="seqc_normalization" else "continuous",
        "gene_ids":list(genes),"sample_ids":samples,"complete_biological_truth":False,
        "scope":"technical_library_controls" if kind=="seqc_normalization" else "descriptive_observed_libraries",
        "selection_scope":"exploratory_marker_panel" if dataset_id=="developmental_markers" else "predeclared_panel"}
    if kind=="developmental_observation":
        task["stage_labels"]=sorted({row["stage"] for row in metadata},key=lambda value:float(value[1:]))
        task["expression_units"]="RPKM"
    else:
        task.update(expression_units="read_counts",mixture_mass_fractions={"A":[1.,0.],"B":[0.,1.],"C":[.75,.25],"D":[.25,.75]},
                    independent_patient_count=0,unit="technical_library",factor_scale="relative; common scale cancels")
    _write(inputs/"task_inputs.json",task);_write(inputs/"calibration.json",calibration)
    _,_,_,_,input_hash=load_public_inputs(inputs,allow_data_execution=True)
    reference=dict(reference)
    reference.update(record_format="devbench.public-reference/1",task_id=task["task_id"],input_sha256=input_hash,
                     kind=kind,source_id=task["source_id"],complete_biological_truth=False)
    reference_hash=_write(private/"reference.json",reference)
    preparation={"plan_identity":plan.sha256(),"dataset_id":dataset_id,"input_sha256":input_hash,
                 "reference_sha256":reference_hash,"sources":sources,"input_files":input_inventory(inputs)}
    preparation_hash=_write(private/"preparation.json",preparation)
    result=PreparedPublicTask(root=str(output),dataset_id=dataset_id,source_id=task["source_id"],task_id=task["task_id"],
        plan_identity=plan.sha256(),input_sha256=input_hash,reference_sha256=reference_hash,preparation_sha256=preparation_hash,
        independence_id="public-"+content_sha256([task["source_id"],"shared_source_materials_and_specimens"])[:24])
    _seal(output)
    return result


def prepare_public_task(plan, dataset_id, cache_root, output, *, allow_data_execution=False):
    if allow_data_execution is not True:
        raise PermissionError("Public data preparation is disabled")
    from .public_tracks import inspect_developmental_subset, inspect_seqc_cache, ERCC_ID, _hash
    from .evidence import _read,decode_json
    import numpy as np
    plan=PublicStudyPlan.model_validate_json(plan.model_dump_json())
    if dataset_id not in plan.datasets:raise ValueError("Dataset is absent from the frozen public plan")
    cache_root=Path(cache_root)
    if not cache_root.is_absolute() or ".." in cache_root.parts or any(p.is_symlink() for p in (cache_root,*cache_root.parents)):
        raise ValueError("Public cache root must be absolute without symlinks")
    cache=cache_root/DATASETS[dataset_id][1]
    if dataset_id!="seqc_controls":
        from benchmarks.public_data import expression_digest
        from devharness_core.storage import ChunkedExpressionStore
        info=inspect_developmental_subset(cache,allow_public_data_read=True)
        manifest=decode_json(_read(cache/"source_manifest.json",1_000_000))
        if expression_digest(cache/"processed_counts.zarr")!=manifest["expression_sha256"]:
            raise ValueError("Developmental expression differs from the retained source digest")
        with ChunkedExpressionStore(cache/"processed_counts.zarr") as store:
            genes=list(store.gene_names);samples=list(store.sample_names)
            if len(genes)*len(samples)*8>16_000_000:raise ValueError("Developmental subset exceeds preparation budget")
            values=np.concatenate([block for _,block in store.iter_gene_chunks(chunk_size=128)]).astype(float)
        with (cache/"processed_metadata.csv").open(newline="") as stream:original=list(csv.DictReader(stream))
        if samples != [row["sample_id"] for row in original] or genes != manifest["selection"]["genes"]:
            raise ValueError("Developmental source axes differ from the retained selection")
        metadata=[{"sample_id":row["sample_id"],"stage":row["embryonic_age"],"organ":row["organ"],
                   "batch":row["batch"],"specimen_label":row["specimen_id"],"independent_animal_id":""} for row in original]
        stages=sorted({row["stage"] for row in metadata},key=lambda value:float(value[1:]))
        reference={"stage_labels":stages,"gene_ids":genes,"means":np.column_stack([
            values[:,[index for index,row in enumerate(metadata) if row["stage"]==stage]].mean(axis=1) for stage in stages]).tolist(),
            "reference_scope":"observed_library_means_only","independent_animal_count":None}
        if (expression_digest(cache/"processed_counts.zarr")!=manifest["expression_sha256"]
                or _hash(cache/"processed_metadata.csv",5_000_000)!=info["metadata_sha256"]
                or _hash(cache/"source_manifest.json",1_000_000)!=info["source_manifest_sha256"]):
            raise ValueError("Developmental source changed during preparation")
        return _materialize(plan,dataset_id,output,values,genes,metadata,{},reference,info,allow_data_execution=True)
    info=inspect_seqc_cache(cache,allow_public_data_read=True)
    with (cache/"ERCC_Controls_Analysis.txt").open(newline="") as stream:
        control_rows={row["ERCC ID"]:row for row in csv.DictReader(stream,delimiter="\t")}
    calibration_ids=set()
    for group in sorted({row["subgroup"] for row in control_rows.values()}):
        members=[gene for gene,row in control_rows.items() if row["subgroup"]==group]
        if len(members)<=plan.calibration_per_ratio_group:raise ValueError("Ratio group lacks a held-out control")
        calibration_ids.update(sorted(members,key=lambda gene:public_identity(plan,"control-split",gene))[:plan.calibration_per_ratio_group])
    with gzip.open(info["counts_path"],"rt") as stream:
        reader=csv.reader(stream,delimiter="\t");header=next(reader)
        endogenous=[row[0] for row in reader if not ERCC_ID.fullmatch(row[0])]
    selected=set(sorted(endogenous,key=lambda gene:public_identity(plan,"feature-selection",gene))[:plan.endogenous_genes]) | set(control_rows)
    lanes=[(index,row) for index,row in enumerate(info["sample_metadata"]) if row["mixture"] in "ABCD"]
    libraries=sorted({row["library_id"] for _,row in lanes})
    lookup={library:i for i,library in enumerate(libraries)}
    metadata=[]
    for library in libraries:
        members=[row for _,row in lanes if row["library_id"]==library];first=members[0]
        metadata.append({"sample_id":library,"library_id":library,"mixture":first["mixture"],"site":first["site"],
            "platform":first["platform"],"lane_count":str(len(members)),"lane_members":";".join(row["sample_id"] for row in members),
            "replicate_kind":"technical","independent_patient_id":""})
    aliases={gene:"g_"+public_identity(plan,"feature-alias",gene)[:24] for gene in selected}
    ordered=sorted(selected,key=lambda gene:aliases[gene]);positions={gene:i for i,gene in enumerate(ordered)}
    values=np.zeros((len(ordered),len(libraries)),dtype=np.int64)
    with gzip.open(info["counts_path"],"rt") as stream:
        reader=csv.reader(stream,delimiter="\t");next(reader)
        for row in reader:
            if row[0] not in selected:continue
            totals=[0]*len(libraries)
            for index,meta in lanes:totals[lookup[meta["library_id"]]]+=int(row[index+1])
            if max(totals)>2**53:raise ValueError("Aggregated library counts exceed exact transport range")
            values[positions[row[0]]]=totals
    amounts={};ratios={};eligible=[]
    from .public_tracks import MIXTURES
    for gene,row in control_rows.items():
        a=float(row["concentration in Mix 1 (attomoles/ul)"]);b=float(row["concentration in Mix 2 (attomoles/ul)"])
        alias=aliases[gene]
        if gene in calibration_ids:
            amounts[alias]={meta["sample_id"]:MIXTURES[meta["mixture"]]["A"]*a+MIXTURES[meta["mixture"]]["B"]*b for meta in metadata}
        else:
            ratios[alias]=a/b
            observed=values[positions[gene]]
            totals=[int(observed[[i for i,meta in enumerate(metadata) if meta["mixture"]==group]].sum()) for group in ("A","B")]
            if min(totals)>0 and sum(totals)>=plan.minimum_evaluation_control_counts:eligible.append(alias)
    calibration={"record_format":"devbench.public-calibration/1","input_units":"nominal_relative_ERCC_amount",
                 "expected_inputs":amounts,"scope":"calibration_subset_only; stable gene-specific recovery across mixtures is an assumption"}
    reference={"reference_scope":"held_out_nominal_ERCC_ratios_and_technical_reproducibility",
        "evaluation_ratios_A_over_B":ratios,"eligible_evaluation_controls":sorted(eligible),
        "eligibility_rule":{"minimum_raw_A_plus_B_counts":plan.minimum_evaluation_control_counts,"positive_A_and_B_required":True},
        "endogenous_gene_ids":[aliases[gene] for gene in ordered if gene not in control_rows],
        "private_feature_map":{aliases[gene]:gene for gene in ordered},"independent_patient_count":0}
    if (_hash(Path(info["counts_path"]),20_000_000)!=info["source_sha256"]
            or _hash(cache/"ERCC_Controls_Analysis.txt",1_000_000)!=info["control_reference_sha256"]):
        raise ValueError("SEQC sources changed during preparation")
    return _materialize(plan,dataset_id,output,values,[aliases[gene] for gene in ordered],metadata,calibration,reference,info,allow_data_execution=True)


def verify_public_task(bundle, plan, *, allow_data_execution=False):
    if allow_data_execution is not True:raise PermissionError("Public bundle verification is disabled")
    from .public_io import input_inventory
    from .evidence import _read,decode_json
    from .biology_datasets import _sealed
    bundle=PreparedPublicTask.model_validate_json(bundle.model_dump_json())
    if bundle.plan_identity!=plan.sha256() or bundle.dataset_id not in plan.datasets or bundle.task_id!=public_identity(plan,"task",bundle.dataset_id):
        raise ValueError("Prepared public task does not match the frozen plan")
    from .permissions import require_protected_storage
    root=Path(bundle.root)
    if ".." in root.parts or any(p.is_symlink() for p in (root,*root.parents)):
        raise ValueError("Prepared public root must be canonical without symlinks")
    require_protected_storage(root)
    _sealed(root)
    if {p.name for p in root.iterdir()}!={"inputs","private"} or {p.name for p in (root/"private").iterdir()}!={"reference.json","preparation.json"}:
        raise ValueError("Unexpected prepared public bundle members")
    preparation=_read(root/"private/preparation.json",16_000_000)
    reference=_read(root/"private/reference.json",16_000_000)
    if hashlib.sha256(preparation).hexdigest()!=bundle.preparation_sha256 or hashlib.sha256(reference).hexdigest()!=bundle.reference_sha256:
        raise ValueError("Private public-track receipts changed")
    retained=decode_json(preparation);ref=decode_json(reference);files=input_inventory(root/"inputs")
    expected_source=DATASETS[bundle.dataset_id][0]
    if (bundle.source_id!=expected_source
            or bundle.independence_id!="public-"+content_sha256([expected_source,"shared_source_materials_and_specimens"])[:24]
            or any(retained.get(key)!=getattr(bundle,key) for key in ("plan_identity","dataset_id","input_sha256","reference_sha256"))
            or any(ref.get(key)!=getattr(bundle,key) for key in ("task_id","input_sha256","source_id"))):
        raise ValueError("Prepared public identity bindings disagree")
    if files!=retained["input_files"] or content_sha256(files)!=bundle.input_sha256:
        raise ValueError("Prepared public inputs changed")
    return bundle
