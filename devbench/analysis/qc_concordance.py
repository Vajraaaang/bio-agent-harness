"""Compare newly computed QC with stored values without hiding differences."""
import numpy as np
import pandas as pd

QC_FIELDS=("pct_counts_mt","total_counts","n_genes_by_counts","log1p_total_counts","pct_counts_in_top_20_genes")


def _distribution(values):
    finite=np.asarray(values,dtype=float)
    finite=finite[np.isfinite(finite)]
    return {"finite_cells":len(finite),"median":float(np.median(finite)) if len(finite) else None,
            "p95":float(np.quantile(finite,.95)) if len(finite) else None}


def compare_qc(reference,recomputed):
    if (not reference.index.is_unique or not recomputed.index.is_unique
            or not reference.index.equals(recomputed.index)):
        raise ValueError("QC concordance requires the same full input cell identities in order")
    fields={}
    for name in QC_FIELDS:
        if name not in reference or name not in recomputed:
            fields[name]={"status":"unavailable_column","in_reference":name in reference,"in_recomputed":name in recomputed}
            continue
        old=pd.to_numeric(reference[name],errors='coerce').to_numpy(dtype=float)
        new=pd.to_numeric(recomputed[name],errors='raise').to_numpy(dtype=float)
        paired=np.isfinite(old)&np.isfinite(new)
        differences=new[paired]-old[paired]
        integer_metric=name in {"total_counts","n_genes_by_counts"}
        matches=(new[paired]==old[paired]) if integer_metric else np.isclose(new[paired],old[paired],rtol=1e-6,atol=1e-6)
        fields[name]={"status":"compared" if paired.any() else "unavailable_no_finite_pairs",
            "paired_finite_cells":int(paired.sum()),"matching_cells":int(matches.sum()),
            "mismatched_cells":int((~matches).sum()),
            "agreement_fraction":float(matches.mean()) if len(matches) else None,
            "mean_absolute_difference":float(np.mean(np.abs(differences))) if len(differences) else None,
            "maximum_absolute_difference":float(np.max(np.abs(differences))) if len(differences) else None,
            "mean_signed_difference_new_minus_stored":float(np.mean(differences)) if len(differences) else None,
            "missing_or_nonfinite_reference_cells":int((~np.isfinite(old)).sum()),
            "non_numeric_reference_cells":int((reference[name].notna().to_numpy()&np.isnan(old)).sum()),
            "missing_or_nonfinite_recomputed_cells":int((~np.isfinite(new)).sum()),
            "reference_distribution":_distribution(old),"recomputed_distribution":_distribution(new),
            "comparison_tolerance":{"rtol":0 if integer_metric else 1e-6,"atol":0 if integer_metric else 1e-6}}
    return {"scope":"concordance_with_stored_QC_not_a_truth_test","cells":len(reference),"fields":fields,
            "mismatches_are_findings_not_acceptance_failures":True}
