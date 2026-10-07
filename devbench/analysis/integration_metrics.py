"""All-cell ASW metrics with bounded distance chunks and shared distance work."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.metrics import pairwise_distances_chunked


def _silhouette(sums, counts, codes):
    rows=np.arange(len(codes))
    own_counts=counts[codes]
    a=np.divide(sums[rows,codes],own_counts-1,out=np.zeros(len(codes)),where=own_counts>1)
    means=np.divide(sums,counts[None,:],out=np.full_like(sums,np.inf),where=counts[None,:]>0)
    means[rows,codes]=np.inf
    b=means.min(axis=1)
    denominator=np.maximum(a,b)
    result=np.divide(b-a,denominator,out=np.zeros(len(codes)),where=denominator>0)
    result[own_counts==1]=0.0  # The standard silhouette definition for singleton groups.
    if not np.isfinite(result).all():
        raise ValueError("nonfinite_silhouette_result")
    return result


def full_silhouette_metrics(representation,batches,reference_types,*,working_memory_mb=64):
    """Euclidean ASW on all admitted cells, never on a UMAP or a subsample.

    Batch ASW is the scIB definition: average 1-|s| within each eligible stored
    cell-type group, then weight those groups equally. Cell-type ASW is the
    ordinary signed silhouette against the stored predictions. These are
    reference-dependent proxies, not evidence of experimental cell identity.
    """
    x=np.asarray(representation,dtype=np.float64)
    batches=pd.Series(np.asarray(batches),dtype="object")
    if (x.ndim!=2 or len(x)<2 or len(batches)!=len(x) or batches.isna().any()
            or not np.isfinite(x).all() or not np.isfinite(working_memory_mb) or working_memory_mb<=0):
        raise ValueError("ASW requires finite embeddings, nonmissing batches and a positive memory budget")
    batch_codes,batch_names=pd.factorize(batches.astype(str),sort=True)
    labels=pd.Series([None]*len(x),dtype="object") if reference_types is None else pd.Series(np.asarray(reference_types),dtype="object")
    if len(labels)!=len(x):
        raise ValueError("reference_celltype_identity_length_mismatch")
    type_codes,type_names=pd.factorize(labels,sort=True)
    valid=type_codes>=0
    nb,nt=len(batch_names),len(type_names)
    joint_pairs,joint_valid=np.unique(np.column_stack([type_codes[valid],batch_codes[valid]]),axis=0,return_inverse=True)
    joint_codes=np.full(len(x),-1,dtype=int);joint_codes[valid]=joint_valid
    nj=len(joint_pairs)
    row=np.r_[np.arange(len(x)),np.flatnonzero(valid),np.flatnonzero(valid)]
    col=np.r_[batch_codes,nb+type_codes[valid],nb+nt+joint_valid]
    membership=sparse.csr_matrix((np.ones(len(row)),(row,col)),shape=(len(x),nb+nt+nj))
    group_counts=np.asarray(membership.sum(axis=0)).ravel()
    global_batch=np.full(len(x),np.nan)
    celltype=np.full(len(x),np.nan)
    conditional=np.full(len(x),np.nan)
    batch_available=1<nb<len(x)
    type_available=1<nt<int(valid.sum())
    eligible=[]
    type_reports=[]
    for code,name in enumerate(type_names):
        joints=np.flatnonzero(joint_pairs[:,0]==code)
        count=int((type_codes==code).sum())
        admitted=1<len(joints)<count
        type_reports.append({"reference_celltype":str(name),"cells":count,"batches":len(joints),
            "status":"eligible" if admitted else "ineligible_batch_structure"})
        if admitted:
            eligible.append((code,joints))
    if batch_available or type_available or eligible:
        offset=0
        for distances in pairwise_distances_chunked(x,metric="euclidean",n_jobs=1,working_memory=working_memory_mb):
            stop=offset+len(distances)
            distances[np.arange(len(distances)),np.arange(offset,stop)]=0.0
            sums=np.asarray(distances@membership)
            if batch_available:
                global_batch[offset:stop]=_silhouette(sums[:,:nb],group_counts[:nb],batch_codes[offset:stop])
            in_chunk=np.flatnonzero(valid[offset:stop])
            if type_available and len(in_chunk):
                celltype[offset+in_chunk]=_silhouette(sums[in_chunk,nb:nb+nt],group_counts[nb:nb+nt],type_codes[offset+in_chunk])
            for code,joints in eligible:
                selected=np.flatnonzero(type_codes[offset:stop]==code)
                if not len(selected):
                    continue
                lookup=np.full(nj,-1,dtype=int);lookup[joints]=np.arange(len(joints))
                columns=nb+nt+joints
                conditional[offset+selected]=_silhouette(sums[np.ix_(selected,columns)],
                    group_counts[columns],lookup[joint_codes[offset+selected]])
            offset=stop
    batch_scores=[]
    for code,record in enumerate(type_reports):
        scores=conditional[type_codes==code]
        if record['status']=='eligible':
            value=float(np.mean(1-np.abs(scores)))
            record['batch_ASW']=value
            batch_scores.append(value)
        else:
            record['batch_ASW']=None
    return {"batch_ASW":float(np.mean(batch_scores)) if batch_scores else None,
        "cell_type_ASW":float(celltype[valid].mean()) if type_available else None,
        "batch_silhouette":float(global_batch.mean()) if batch_available else None,
        "cells_evaluated":len(x),"reference_label_cells":int(valid.sum()),
        "constant_embedding":bool(np.all(np.var(x,axis=0)==0)),
        "eligible_reference_types":len(batch_scores),"per_reference_type":type_reports,
        "batch_ASW_eligible_cells":sum(row['cells'] for row in type_reports if row['status']=='eligible'),
        "subsampling":False,"distance":"euclidean","working_memory_MB":working_memory_mb,
        "batch_ASW_definition":"equal_type_mean_of_within_type_mean(1-abs(batch_silhouette))",
        "cell_type_ASW_definition":"mean_signed_silhouette_against_stored_celltype_predictions",
        "batch_ASW_range":[0,1],"cell_type_ASW_range":[-1,1],
        "higher_is_better_for_both":True,
        "scope":"batch_mixing_and_preservation_of_external_prediction_structure_not_annotation_validation"}
