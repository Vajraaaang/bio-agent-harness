# Bulk participant runtime staging

`biology-run --participant-runtime-profile bulk` selects the installed bulk
scientific dependencies and their transitive requirements for every condition.
It is an opt-in setting for broker-based bulk studies. The default `host`
profile retains legacy staging behavior; cellular studies and standalone
preflight-only runs do not silently adopt the bulk profile.

The direct requirements live in `devbench/runtime_dependencies.py`. Selection
uses installed distribution metadata, version constraints and active extras.
Wheel records retain package metadata and native library directories. Shared
namespace packages are partitioned into the selected distributions' children
(including AnnData's required helpers), without exposing optional Scanpy/Torch
packages. An ordinary package shared with an unselected distribution is rejected.
Missing dependencies fail before dispatch; staging never installs dependencies
or falls back to the full host environment. Editable hooks, coordinator modules
and grader source remain excluded.

The coordinator records the chosen profile, installed distribution versions,
staged entries and the existing shared runtime fingerprint. Baseline, optional
and enforced participants receive the same ordinary Python dependencies. Tool
access still differs only through the existing broker policy.

The existing limits are unchanged: 250,000,000 bytes per fingerprinted file,
2,000,000,000 bytes and 200,000 files per fingerprint root, frozen time/RSS/thread
budgets, protected-input permissions, and mandatory per-attempt permission and
model-identity checks. This selects fewer dependencies; it does not exempt large
libraries from hashing or relax read grants. Runtime roots are still hashed in
full, including compiled extensions and package metadata.

The public answer validator includes the public `ComputedConditionContrast`
definition used by the current biology answer model. That is a packaging repair;
the source answer schema, scientific thresholds and evaluator remain unchanged.
It supplies contract diagnostics only, never grading feedback or references.

Before live execution, qualify the installed runtime with the staging/import,
validator-parity, full-fingerprint, supervision and permission checks. Verify
that all three conditions have the same runtime identity under their actual
permission profiles. A passing offline check does not establish live-agent
performance. Preserve each failed run and freeze a separate configuration and
results directory for a separately authorized run.
