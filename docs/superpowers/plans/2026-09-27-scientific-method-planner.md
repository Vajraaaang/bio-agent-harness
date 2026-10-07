# Scientific method planner implementation plan

**Goal:** Implement the supplied open-world planner MVP over the existing method service, preserving existing execution and review interfaces.

**Architecture:** Freeze registry-blind scientific discovery before resolving immutable method versions. The coordinator owns applicability, trial admission, measured results, approval scope and immutable recipes; model responses never grant execution or trust. Reuse MethodService, its OCI executor, content-addressed artifacts and execution DAG, and the protected Codex selector.

**Validation:** Synthetic fixtures only. No real datasets, external model execution, package installation, or promotion of existing user methods is part of this implementation run.

- [ ] Extend optional method scientific metadata without changing old contract identities.
- [ ] Add strict discovery/resolution/recommendation schemas and two-pass coordinator with frozen candidates and explicit unavailable methods.
- [ ] Reject failed/unknown preconditions and invalid subsets before execution; freeze inputs, parameters and diagnostic metrics; retain observed successes and failures through existing provenance.
- [ ] Add distinct exact-subject approval scopes, experimental recipes and reviewed, versioned promotion without changing prior analyses.
- [ ] Wire the existing Codex selector and methods CLI; document offline and live boundaries and provide a synthetic request example.
- [ ] Run focused regressions, the controlled synthetic verification suite, and a final diff review against saved baseline files.

The user supplied the design and requested implementation. No additional design approval is needed. Existing unrelated files and root planning documents remain untouched.
