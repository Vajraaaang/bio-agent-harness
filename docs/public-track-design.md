# Public track implementation contract

This track remains separate from synthetic recovery. It must use the same ordinary environment and baseline/optional/enforced definitions, protected coordinator references, actual Codex CLI adapter, bounded supervision and failure-preserving accounting. No live participants are authorized by this design.

## Developmental observations

Use the two existing E-MTAB-6798 brain subsets with their checked source and expression hashes. Their 35 shared sample columns form one underlying dataset, not two independent cohorts. Animal identity remains unverified. Preserve RPKM as continuous expression. The general subset and explicitly selected marker subset have different selection scopes; marker results remain exploratory.

The answer contract reports chronological stage labels and observed library means for every requested gene. References may reproduce these measured summaries. They must not supply latent differential-expression labels, causal networks, universal pseudotime or independent-animal inference. Public task success measures the limited observation contract; complete biological correctness remains unknown.

## SEQC controls

Use the pinned official NYG processed count table and pinned manufacturer ERCC concentration reference. Aggregate lanes belonging to the same technical library, retaining lane membership. Use A–D for the normalization comparison; E/F are ERCC-only and remain outside the endogenous comparison. Vendor library 5, if a future source includes it, retains its shared library identity across sites. No library or lane becomes a patient.

Stage a bounded, outcome-independent endogenous panel plus all 92 exact ERCC identifiers. Split ERCCs into a predeclared calibration subset and a held-out evaluation subset within the publisher's ratio groups. Choose membership with a private retained nonce, not observed recovery. Blind evaluation-control identities so published ratio lookup cannot substitute for analysis. Keep the complete control identity map and held-out nominal ratios outside participant access. All conditions receive the same staged inputs and calibration information.

The answer contract supplies positive normalization factors for all technical libraries, the method and limitations. The coordinator computes held-out ERCC ratio error, titration consistency and technical reproducibility from retained counts and the reported factors. Normalize the irrelevant common scale of factors; reject missing, duplicate or nonfinite factors. Freeze any control eligibility rules before answers. Never select evaluation genes from a participant's favorable outcomes.

RNA mass fractions C=3A:1B and D=1A:3B do not establish equal mRNA content or every endogenous gene's truth. ERCC nominal ratios are limited assay controls and may expose assay bias as well as normalization error. Titration and repeatability diagnostics do not certify clinical validity or biological FDR.

## Current implementation boundary

`public_tracks.py` inspects pinned sources; `public_study.py` prepares protected input/reference bundles; `public_io.py` bounds and binds observed assets; `public_tools.py` supplies observed-only analysis through the receipt broker. `public_answers.py` and `public_grading.py` implement limited reference contracts. `public_execution.py` reuses the permission, supervision and Codex adapter components. `public_reporting.py` preserves failed planned slots and separates public reference outcomes from synthetic correctness. The [workflow guide](public-three-condition-study.md) documents the commands.

Real public-data analyses and agent comparisons remain unperformed. New numerical implementation may be tested on small local software fixtures under the user's current authorization.
