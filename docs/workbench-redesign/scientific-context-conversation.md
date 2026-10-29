# Inline scientific planning context

The investigation clarification renderer now handles backend-provided scientific context fields without asking the user to enter JSON. Biological declarations and planning permissions remain separate.

## Contract

The durable record provides:

- `scientific_context`: retained explicit planning context.
- `clarification_fields`: existing field metadata, with `scope: 'scientific_context'` for `data_classification`, `allow_model_execution` and `auto_advance`. Fields without that scope continue to update biological `declarations`.
- Data-classification select options are provided by the backend. The renderer does not infer a classification from an observed filename, column or dataset profile.

An explicit clarification submission emits:

```javascript
{
  type: 'clarify',
  investigationId,
  expectedRevision,
  declarations,        // assay/measurement/provenance/capture declarations only
  scientificContext    // classification and planning grants only
}
```

The frontend coordinator converts `scientificContext` to the backend action key `scientific_context`. Legacy biological-only submissions omit the extra callback field and keep their previous contract.

## Permission controls

The model-permission checkbox starts unchecked unless an explicit retained value exists for the same investigation. It is available only when the service exposes `config.scientific.model_execution_allowed === true` and the user has explicitly classified the input as `public` or `synthetic`. Private, unclassified and missing classifications disable and clear the checkbox. Changing classification does not grant permission.

The automatic-planning checkbox requires `config.scientific.scientific_workflow_configured === true`, plus one of:

1. An explicit allowed model-planning grant for public/synthetic input.
2. The exact configured `planning_mode === 'recorded_fixture'`, an explicitly classified synthetic input, and a separate explicit automatic-planning checkbox. This path leaves model permission false and is labeled recorded offline planning.

Service flags cannot be enabled through these controls. The form recomputes permissions on classification changes and submission, so a disabled or inappropriate model checkbox cannot leak a true value through a stale draft. Server enforcement remains authoritative. Automatic planning stops at mandatory review gates; it does not imply permission to execute an analysis.

## Presentation

The same native form/structured-disclosure system is reused. A scientific-context clarification card has the heading `Scientific planning context`, normal form labels and concise availability text. New `.wc-field-availability` text uses `role=status`; no inline styling, CSP change, remote dependency or raw-JSON entry is introduced.

## Verification

The conversation module suite passes 20 tests and `node --check` passes. The three new checks failed before implementation and cover separate declaration/context payloads, explicit public/synthetic model grants, private-input revocation, service flag enforcement despite stale/tampered checkbox values, and explicitly authorized synthetic offline advancement without a model grant. Backend and browser verification remain part of the integrated host's checks.
