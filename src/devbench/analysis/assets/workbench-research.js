(function (root) {
  'use strict';

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text != null) element.textContent = String(text);
    if (className) element.className = className;
    return element;
  }

  const label = value => String(value || '').replaceAll('_', ' ').replaceAll('-', ' ');
  const record = value => value && typeof value === 'object' && !Array.isArray(value);
  const digest = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
  const evidenceRefs = entries => (Array.isArray(entries) ? entries : []).filter(
    ref => record(ref) && typeof ref.step_id === 'string' && digest(ref.receipt_sha256) &&
      (ref.artifact_sha256 == null || digest(ref.artifact_sha256)),
  );
  const operationNames = {
    scrna_droplets: 'Call cells from raw droplets',
    scrna_batch_qc: 'Batch quality control',
    scrna_lane_doublets: 'Detect doublets by capture lane',
    scrna_prepare: 'Prepare counts for integration',
    scrna_integrate: 'Evaluate integration',
    scrna_nb_countsplit: 'Split counts for marker testing',
    scrna_nb_fold_cluster: 'Cluster the discovery fold',
    scrna_sccoda: 'Fit compositional abundance',
  };
  const operationName = item => item.label || operationNames[item.operation || item.id] ||
    label(item.operation || item.id);
  const actionText = {
    revise_aim: 'Revise aim',
    approve_aim: 'Approve aim',
    assess_explanation: 'Assess competing explanation',
    open_blinded_review: 'Open blinded review',
    submit_blinded_review: 'Submit assessment',
    record_outcome: 'Record outcome',
    synthesize: 'Build & Export Research Package (.zip)',
  };

  function button(text, handler, disabled = false) {
    const control = node('button', text, 'secondary');
    control.type = 'button';
    control.disabled = disabled;
    control.addEventListener('click', handler);
    return control;
  }

  function field(form, text, tag = 'input', value = '', required = false) {
    const container = node('label', null, 'research-field');
    container.append(node('span', text));
    const input = node(tag);
    input.setAttribute('aria-label', text);
    input.required = required;
    if (tag === 'textarea') input.rows = 3;
    if (tag !== 'select') input.value = value;
    container.append(input);
    form.append(container);
    return input;
  }

  function option(input, value, text) {
    const item = node('option', text);
    item.value = value;
    input.append(item);
  }

  function object(input, name) {
    let parsed;
    try {
      parsed = JSON.parse(input.value || '{}');
    } catch (_) {
      throw new Error(name + ' must be valid JSON.');
    }
    if (!record(parsed)) throw new Error(name + ' must be a JSON object.');
    return parsed;
  }

  function disclosure(parent, title, className) {
    const detail = node('details', null, className || 'research-disclosure');
    detail.append(node('summary', title));
    parent.append(detail);
    return detail;
  }

  function muted(parent, text) {
    parent.append(node('p', text, 'field-note'));
  }

  function renderHold(parent, reason) {
    if (reason) parent.append(node('p', label(reason), 'research-hold'));
  }

  function row(parent, title, state, lines) {
    const article = node('article', null, 'research-item studio-science-card');
    const header = node('header', null, 'research-item-title');
    const badge = node('span', label(state || 'unknown'), 'research-status studio-status');
    badge.setAttribute('data-status', state || 'unknown');
    header.append(node('h5', title), badge);
    article.dataset.status = state || 'unknown';
    article.append(header);
    for (const line of lines.filter(Boolean)) muted(article, line);
    parent.append(article);
    return article;
  }

  function permission(form, wording) {
    const container = node('label', null, 'check research-permission');
    const input = node('input');
    input.type = 'checkbox';
    input.required = true;
    container.append(input, node('span', wording));
    form.append(container);
    return input;
  }

  function submission(target, text, build, context) {
    const submit = node('button', text);
    submit.type = 'submit';
    target.append(submit);
    target.addEventListener('submit', event => {
      event.preventDefault();
      try {
        context.mutate(build());
      } catch (error) {
        context.message(error.message, true);
      }
    });
  }

  function scalarParameters(parent, schema, initial = {}) {
    const values = [];
    for (const [name, property] of Object.entries(schema?.properties || {})) {
      const choices = property.anyOf || [property];
      const shape = choices.find(item => item.type !== 'null');
      const nullable = choices.some(item => item.type === 'null');
      if (!shape || !['string', 'integer', 'number', 'boolean'].includes(shape.type)) continue;
      const required = (schema.required || []).includes(name);
      const fallback = Object.hasOwn(initial, name) ? initial[name] : property.default;
      const select = Array.isArray(shape.enum) || shape.type === 'boolean';
      const input = field(parent, label(name), select ? 'select' : 'input', fallback ?? '', required);
      if (select) {
        if (!required) option(input, '', nullable ? 'Not set' : 'Use default');
        for (const value of shape.enum || [true, false]) option(input, String(value), label(value));
        input.value = fallback == null ? '' : String(fallback);
      } else if (shape.type === 'number' || shape.type === 'integer') {
        input.type = 'number';
        input.step = shape.type === 'integer' ? '1' : 'any';
        if (Number.isFinite(shape.minimum)) input.min = String(shape.minimum);
        if (Number.isFinite(shape.maximum)) input.max = String(shape.maximum);
      }
      values.push({name, input, shape, nullable, hasInitial: Object.hasOwn(initial, name)});
    }
    return () => {
      const selected = {};
      for (const {name, input, shape, nullable, hasInitial} of values) {
        if (input.value === '') {
          if (nullable && hasInitial) selected[name] = null;
          continue;
        }
        const raw = input.value.trim();
        selected[name] = shape.type === 'integer' || shape.type === 'number' ? Number(raw) :
          shape.type === 'boolean' ? raw === 'true' : raw;
      }
      return selected;
    };
  }

  function operationChooser(parent, view, context) {
    const form = node('form', null, 'research-form studio-qc-composer');
    const basics = node('div', null, 'studio-qc-layout');
    const operation = field(basics, 'Single-cell operation', 'select');
    const available = (view.operations || []).filter(item => item.available === true);
    if (!available.length) {
      muted(parent, 'No curated steps are available with the current runtime.');
      return;
    }
    for (const step of available) option(operation, step.id, operationName(step));
    const identifier = field(basics, 'Step ID', 'input', '', true);
    const branch = field(basics, 'Branch ID', 'input', 'single-cell', true);
    const unit = field(basics, 'Biological unit', 'input', '', true);
    const description = node('p', null, 'field-note');
    form.append(basics, description);
    const ports = node('div', null, 'research-ports');
    const parameterFields = node('div', null, 'research-parameters');
    form.append(ports, parameterFields);
    const advanced = disclosure(form, 'Parameters and contract');
    const parameters = field(advanced, 'Parameters (JSON)', 'textarea', '{}');
    const schema = node('pre', '', 'research-json');
    advanced.append(schema);
    let bindings = [], typedParameters = () => ({});
    function select() {
      const spec = available.find(entry => entry.id === operation.value);
      if (!spec) return;
      if (!identifier.value) identifier.value = spec.id;
      description.textContent = root.WorkbenchQC?.operationDescription(spec.id) ||
        operationName(spec);
      ports.replaceChildren();
      parameterFields.replaceChildren();
      typedParameters = scalarParameters(parameterFields, spec.parameters_schema);
      bindings = [];
      for (const port of spec.inputs || []) {
        const card = node('section', null, 'studio-qc-port');
        ports.append(card);
        const control = field(card, label(port.name) + ' source', 'select', '', !port.optional);
        option(control, '', 'Choose current evidence');
        const sources = (view.sources || []).filter(source =>
          (!port.representation || source.representation === port.representation) &&
          (!port.kind || source.kind === port.kind));
        sources.forEach((source, index) => option(control, String(index), source.id + (source.port ? ' · ' + source.port : '')));
        bindings.push({port, control, sources});
        control.addEventListener('change', () => {
          const source = control.value !== '' && sources[Number(control.value)];
          if (source && source.biological_unit && !unit.value) unit.value = source.biological_unit;
        });
        root.WorkbenchQC?.portDetails(card, port, control, sources);
        if (!sources.length) muted(card, 'Current input required for ' + label(port.name) + '.');
      }
      schema.textContent = spec.parameters_schema ? JSON.stringify(spec.parameters_schema, null, 2) : '';
    }
    operation.addEventListener('change', () => {
      identifier.value = '';
      parameters.value = '{}';
      select();
    });
    select();
    const consent = permission(form, 'Allow data execution for this workflow change');
    submission(form, 'Queue single-cell step', () => {
      if (!consent.checked || context.readOnly || !context.current()) {
        throw new Error('Confirm data execution for the current workflow change.');
      }
      const sources = {};
      for (const {port, control, sources: choices} of bindings) {
        if (control.value === '' && port.optional) continue;
        const source = control.value !== '' && choices[Number(control.value)];
        if (!source) throw new Error('Choose current evidence for ' + label(port.name) + '.');
        sources[port.name] = {id: source.id, port: source.port ?? null};
      }
      return {action: 'queue', operation: operation.value, step_id: identifier.value.trim(),
        branch_id: branch.value.trim(), biological_unit: unit.value.trim(),
        parameters: {...typedParameters(), ...object(parameters, 'Parameters')},
        sources, allow_data_execution: true};
    }, context);
    parent.append(form);
  }

  function renderQc(parent, view, step, context) {
    const panel = node('section', null, 'research-qc studio-qc');
    parent.append(panel);
    const generation = step.generation;
    const stepId = step.id;
    const review = button('Review QC', async () => {
      review.disabled = true;
      panel.replaceChildren(node('p', 'Loading exact QC evidence…'));
      try {
        const detail = await context.qc(stepId, generation);
        if (!context.current()) return;
        panel.replaceChildren(node('h5', 'QC evidence'));
        if (root.WorkbenchQC) root.WorkbenchQC.renderEvidence(panel, detail);
        else if (detail.receipt_sha256) muted(panel, 'Receipt ' + detail.receipt_sha256);
        const evidence = disclosure(panel, 'QC diagnostics and thresholds');
        evidence.append(node('pre', JSON.stringify(detail.report || {}, null, 2), 'research-json'));
        if (context.readOnly || !step.can_revise_qc || view.actions?.revise_qc !== true) return;
        const form = node('form', null, 'research-form');
        const original = detail.parameters || {};
        const schema = (view.operations || []).find(item => item.id === step.operation)?.parameters_schema;
        const fields = node('div', null, 'studio-qc-layout');
        form.append(fields);
        const revised = scalarParameters(fields, schema, original);
        const advanced = disclosure(form, 'Advanced QC parameters');
        const parameters = field(advanced, 'Revised QC parameters (JSON)', 'textarea', '{}');
        const read = () => ({...original, ...revised(), ...object(parameters, 'QC parameters')});
        const update = root.WorkbenchQC?.renderRevisionPreview(
          form, original, read, detail.revision_impact);
        if (update) form.addEventListener('input', update);
        const consent = permission(form, 'Allow this QC revision to reopen downstream analysis');
        submission(form, 'Queue QC reanalysis', () => {
          if (!consent.checked || context.readOnly || !context.current()) {
            throw new Error('Confirm reopening downstream analysis for this QC revision.');
          }
          return {action: 'revise_qc', step_id: stepId, generation,
            parameters: read(), allow_data_execution: true};
        }, context);
        panel.append(form);
      } catch (error) {
        if (context.current()) panel.replaceChildren(node('p', error.message || 'QC evidence is unavailable.', 'research-hold'));
      } finally {
        review.disabled = false;
      }
    });
    review.dataset.qcReview = stepId;
    review.dataset.generation = generation;
    parent.insertBefore(review, panel);
  }

  function renderSingleCell(parent, view, context) {
    const workflows = view.steps || [];
    root.WorkbenchQC?.renderStages(parent, view);
    if (context.readOnly) muted(parent, 'Read-only workflow.');
    if (workflows.length) {
      parent.append(node('h4', 'Single-cell steps'));
      for (const step of workflows) {
        const stage = row(parent, operationName(step) || step.id, step.status, [
          step.id ? 'Step ' + step.id : '',
          Number.isInteger(step.generation) ? 'Generation ' + step.generation : '',
          step.dependencies?.length ? 'Depends on ' + step.dependencies.map(dep => record(dep) ? dep.id || dep.step_id : dep).join(', ') : '',
        ]);
        stage.dataset.stepId = step.id;
        stage.dataset.generation = step.generation;
        renderHold(stage, step.reason);
        for (const stale of step.stale_generations || []) {
          muted(stage, 'Superseded generation ' + stale.generation +
            (stale.reason ? ' · ' + label(stale.reason) : ''));
        }
        if (step.qc_report_available || step.can_revise_qc) {
          renderQc(stage, view, step, context);
        }
      }
    } else muted(parent, 'No curated single-cell steps are recorded for this workflow.');
    if (view.inventory_truncated) muted(parent, 'This view shows a bounded set of current entries.');
    const unavailable = (view.operations || []).filter(item => item.available !== true);
    if (unavailable.length) {
      const section = disclosure(parent, 'Unavailable steps and prerequisites');
      for (const step of unavailable) renderHold(section, operationName(step) + ' · ' + (step.reason || 'Runtime unavailable'));
    }
    if (view.actions?.queue === true && !context.readOnly) {
      operationChooser(disclosure(parent, 'Queue single-cell step'), view, context);
    } else if (!context.readOnly) {
      renderHold(parent, view.action_reasons?.queue || 'Queuing requires a bound curated runtime and data access.');
    }
  }

  function actionEnabled(view, context, key) {
    return !context.readOnly && view.actions?.[key]?.enabled === true;
  }

  function aimDefinition(form, aim) {
    const id = field(form, 'Aim ID', 'input', aim?.aim_id || '', true);
    const kind = field(form, 'Aim kind', 'select');
    option(kind, 'exploratory', 'Exploratory');
    option(kind, 'confirmatory', 'Confirmatory');
    kind.value = aim?.kind || 'exploratory';
    const question = field(form, 'Research question', 'textarea', aim?.question || '', true);
    const estimand = field(form, 'Estimand', 'textarea', aim?.estimand || '', true);
    const blinded = field(form, 'Require independent blinded review');
    blinded.type = 'checkbox';
    blinded.checked = aim?.blinded_review_required === true;
    const advanced = disclosure(form, 'Inputs, dependencies, and multiplicity');
    const declaration = field(advanced, 'Additional aim fields (JSON)', 'textarea', '{}');
    const confirmatory = value => {
      if (value === 'confirmatory') advanced.open = true;
    };
    kind.addEventListener('change', () => confirmatory(kind.value));
    confirmatory(kind.value);
    return () => ({...object(declaration, 'Additional aim fields'),
      aim_id: id.value.trim(), kind: kind.value, question: question.value.trim(),
      estimand: estimand.value.trim(), blinded_review_required: blinded.checked});
  }

  function outcomeDeclaration(form, aim, advancedLabel) {
    const status = field(form, 'Outcome status', 'select');
    for (const [value, caption] of [
      ['supported', 'Supported finding'],
      ['no_significant_finding', 'No significant finding'],
      ['unresolved', 'Unresolved'],
      ['refused', 'Refused'],
      ['failed', 'Failed'],
    ]) option(status, value, caption);
    status.value = 'unresolved';
    const rationale = field(form, 'Outcome rationale', 'textarea');
    const uncertainty = field(form, 'Uncertainty', 'textarea');
    const references = evidenceRefs(aim.evidence);
    const evidence = field(form, 'Evidence receipts', 'select');
    evidence.multiple = true;
    evidence.size = Math.max(2, Math.min(references.length, 5));
    evidence.disabled = references.length === 0;
    references.forEach((ref, index) => option(evidence, String(index),
      ref.step_id + ' · ' + ref.receipt_sha256.slice(0, 12) +
        (ref.artifact_sha256 ? ' · artifact ' + ref.artifact_sha256.slice(0, 12) : '')));
    if (!references.length) muted(form, 'No current evidence receipts are available.');
    const advanced = disclosure(form, 'Advanced outcome declaration');
    const declaration = field(advanced, advancedLabel, 'textarea');
    return () => {
      if (declaration.value.trim()) return object(declaration, advancedLabel);
      if (!rationale.value.trim() || !uncertainty.value.trim()) {
        throw new Error('Record an outcome rationale and uncertainty.');
      }
      const selected = Array.from(evidence.selectedOptions, item => references[Number(item.value)]);
      return {status: status.value, rationale: rationale.value.trim(),
        uncertainty: uncertainty.value.trim(), evidence: selected};
    };
  }

  function thesisForm(target, text, context, collect) {
    const form = node('form', null, 'research-form');
    const build = collect(form);
    submission(form, text, build, context);
    target.append(form);
  }

  function renderThesisAction(parent, view, aim, key, context, collect) {
    if (!actionEnabled(view, context, key)) return;
    thesisForm(disclosure(parent, actionText[key]), actionText[key], context, form => {
      const payload = collect(form);
      const subject = key === 'revise_aim' ? {} : {aim_id: aim.aim_id};
      if (!['revise_aim', 'approve_aim'].includes(key)) subject.version = aim.version;
      return () => ({action: key, ...subject, ...payload()});
    });
  }

  function currentAnswerability(aim, proofs) {
    const latest = new Map();
    // The readback lists ledger proofs in revision order.
    for (const proof of proofs || []) {
      if (proof.aim_id === aim.aim_id && proof.aim_version === aim.version) {
        latest.set(proof.step_id, proof);
      }
    }
    return Array.from(latest.values());
  }

  const formatMeasurements = groups => groups.map(
    group => Array.isArray(group) ? group.map(label).join(' or ') : label(group),
  ).join(', ');

  function hasVerifiedOutcome(aim) {
    return aim.approved && (!aim.blinded_review_required || aim.blinded_review?.status === 'passed') &&
      ['supported', 'no_significant_finding'].includes(aim.status) &&
      aim.outcome?.status === aim.status && evidenceRefs(aim.outcome.evidence).length > 0;
  }

  function thesisMatrixOptions(openAim) {
    return {readiness, currentAnswerability, formatMeasurements, hasVerifiedOutcome,
      evidenceRefs, openAim};
  }

  function renderThesis(parent, packagesHost, view, context) {
    const studio = root.WorkbenchThesisStudio;
    const aims = view.aims || [];
    const session = {id: view.session_id, answerability: view.answerability, packages: view.packages};
    const cards = new Map();
    function openAim(aim) {
      const card = cards.get(aim.aim_id);
      if (!card) return;
      card.scrollIntoView?.({block: 'nearest'});
      card.focus?.({preventScroll: true});
    }
    if (context.readOnly) muted(parent, 'Read-only project');
    if (studio && !context.portfolio) {
      studio.overview(parent, aims.map(aim => ({aim, session})), [session],
        thesisMatrixOptions(({aim}) => openAim(aim)));
    }
    muted(parent, 'Answerability is evaluated by aim and measurement across 20 RNA-seq problem families.');
    if (!aims.length) muted(parent, 'Add an aim to check answerability and review the study design.');
    for (const aim of aims) {
      const card = row(parent, aim.question || aim.aim_id, aim.status, [
        aim.question ? aim.aim_id : '', label(aim.kind), 'Aim version ' + aim.version,
        aim.depends_on?.length ? 'Depends on ' + aim.depends_on.join(', ') : '',
        aim.invalidated_by ? 'Superseded by ' + aim.invalidated_by : '',
      ]);
      card.setAttribute('data-aim-id', aim.aim_id);
      cards.set(aim.aim_id, card);
      card.tabIndex = -1;
      if (aim.estimand) muted(card, aim.estimand);
      renderHold(card, aim.reason);
      if (aim.blinded_review_required) muted(card, 'Independent blinded review required.');
      if (aim.blinded_review) {
        const panel = aim.blinded_review;
        muted(card, 'Blinded panel · ' + label(panel.status || panel.reason));
        if (Number.isInteger(panel.submitted_reviews) && Number.isInteger(panel.assigned_reviewers)) {
          muted(card, panel.submitted_reviews + ' of ' + panel.assigned_reviewers + ' assessments received');
        }
        if (panel.unresolved_disagreement) renderHold(card, 'Reviewer disagreement is unresolved.');
        if (panel.veto_codes?.length) renderHold(card, 'Review vetoes: ' + panel.veto_codes.map(label).join(', '));
        if (panel.reason) renderHold(card, panel.reason);
      }
      for (const explanation of aim.competing_explanations || []) {
        muted(card, explanation.explanation_id + ' · ' + label(explanation.status));
        renderHold(card, explanation.reason);
      }
      const resolved = hasVerifiedOutcome(aim);
      for (const item of currentAnswerability(aim, view.answerability)) {
        if (resolved && item.missing_measurements?.length) continue;
        muted(card, 'Answerability · ' + label(item.status) + ' · aim version ' + item.aim_version);
        if (item.status === 'ready' && item.reason) {
          card.append(node('p', label(item.reason), 'field-note research-answerability-ready'));
        } else renderHold(card, item.reason);
        if (item.missing_measurements?.length) muted(card, 'Missing measurements: ' + formatMeasurements(item.missing_measurements));
      }
      const evidence = evidenceRefs(aim.outcome?.evidence);
      if (evidence.length) {
        const detail = disclosure(card, 'Outcome evidence');
        muted(detail, label(aim.outcome.status));
        for (const ref of evidence) thesisReceipt(detail, ref, session.id);
      }
      const currentEvidence = evidenceRefs(aim.evidence);
      if (currentEvidence.length) {
        const detail = disclosure(card, 'Current evidence receipts');
        for (const ref of currentEvidence) thesisReceipt(detail, ref, session.id);
      }
      renderThesisAction(card, view, aim, 'revise_aim', context, form => {
        muted(form, 'Include the complete next declaration, including its dependencies and review requirements.');
        if (studio) {
          const definition = studio.aimDefinition(form, {aims, aim});
          const rationale = field(form, 'Revision rationale', 'textarea', '', true);
          return () => ({definition: definition(), rationale: rationale.value.trim()});
        }
        const input = field(form, 'Revised aim declaration (JSON)', 'textarea', '', true);
        const rationale = field(form, 'Revision rationale', 'textarea', '', true);
        return () => {
          const definition = object(input, 'Revised aim declaration');
          if (definition.aim_id !== aim.aim_id) throw new Error('Use this aim’s exact ID.');
          if (aim.blinded_review_required && definition.blinded_review_required !== true) {
            throw new Error('Retain this aim’s independent blinded review requirement.');
          }
          return {definition, rationale: rationale.value.trim()};
        };
      });
      if (!aim.approved && !['stale', 'invalidated', 'refused'].includes(aim.status)) {
        renderThesisAction(card, view, aim, 'approve_aim', context, form => {
          const rationale = field(form, 'Approval rationale', 'textarea', '', true);
          return () => ({rationale: rationale.value.trim()});
        });
      }
      if (!aim.approved) continue;
      if (aim.competing_explanations?.length) renderThesisAction(card, view, aim, 'assess_explanation', context, form => {
        if (studio) {
          const assessment = studio.explanationAssessment(form, aim, currentEvidence,
            {session_id: view.session_id, label: 'Competing-explanation evidence'});
          return () => ({assessment: assessment()});
        }
        const data = field(form, 'Explanation assessment (JSON)', 'textarea', '{}', true);
        return () => ({assessment: object(data, 'Explanation assessment')});
      });
      if (aim.blinded_review_required) {
        renderThesisAction(card, view, aim, 'open_blinded_review', context, form => {
          const packet = field(form, 'Masked packet path', 'input', '', true);
          const reviewers = studio ? studio.reviewers(form) : (() => {
            const input = field(form, 'Independent reviewer IDs (one per line)', 'textarea', '', true);
            return () => input.value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
          })();
          const outcome = outcomeDeclaration(form, aim, 'Proposed outcome (JSON)');
          const rationale = field(form, 'Review rationale', 'textarea', '', true);
          return () => ({masked_packet: packet.value.trim(), reviewers: reviewers(),
            outcome: outcome(), rationale: rationale.value.trim()});
        });
        renderThesisAction(card, view, aim, 'submit_blinded_review', context, form => {
          const assessment = field(form, 'Verified assessment path', 'input', '', true);
          return () => ({assessment_file: assessment.value.trim()});
        });
      }
      renderThesisAction(card, view, aim, 'record_outcome', context, form => {
        const outcome = outcomeDeclaration(form, aim, 'Aim outcome (JSON)');
        return () => ({outcome: outcome()});
      });
    }
    if (actionEnabled(view, context, 'propose_aim')) {
      thesisForm(disclosure(parent, 'New aim'), 'Propose aim', context, form => {
        const definition = studio ? studio.aimDefinition(form, {aims}) : aimDefinition(form);
        return () => ({action: 'propose_aim', definition: definition()});
      });
    }
    const readinessList = node('ul', null, 'research-readiness');
    for (const aim of aims) readinessList.append(node('li',
      (aim.question || aim.aim_id) + ' · ' + readiness(aim, session)));
    if (aims.length) packagesHost.append(readinessList);
    const stale = aims.some(aim => aim.status === 'stale' || aim.invalidated_by) ||
      view.packages?.some(item => item.status === 'stale') && !view.packages?.some(item => item.download);
    if (actionEnabled(view, context, 'synthesize')) {
      thesisForm(disclosure(packagesHost, stale ? 'Build from current evidence' : actionText.synthesize),
        actionText.synthesize, context, form => {
        if (studio) {
          const specification = studio.synthesisSpecification(form, view, readiness);
          return () => ({action: 'synthesize', ...specification()});
        }
        const schema = field(form, 'Answer schema path', 'input', '', true);
        const spec = field(form, 'Synthesis specification (JSON)', 'textarea', '{}', true);
        return () => ({action: 'synthesize', answer_schema: schema.value.trim(), spec: object(spec, 'Synthesis specification')});
      });
    }
    if (view.packages?.length) {
      const packages = node('section', null, 'studio-thesis-packages');
      packages.append(node('h4', 'Supporting evidence packages'));
      packagesHost.append(packages);
      if (studio) studio.packageTable(packages, view.packages, session.id, context);
      else for (const packageRecord of view.packages) {
        const section = row(packages, label(packageRecord.outcome) + ' thesis package',
          packageRecord.status, []);
        thesisReceipt(section, {receipt_sha256: packageRecord.root_sha256}, session.id);
        if (packageRecord.download && context.download) {
          section.append(button('Export research package (.zip)', async () => {
            try {
              await context.download(packageRecord.download);
            } catch (error) {
              context.message(error.message || 'The thesis export is unavailable.', true);
            }
          }));
        } else if (packageRecord.status === 'stale') {
          renderHold(section, 'Build from current evidence');
        }
      }
    }
    const disabled = Object.entries(view.actions || {}).filter(([, action]) => action?.enabled === false && action.reason);
    if (disabled.length) {
      const section = disclosure(parent, 'Unavailable thesis actions and prerequisites');
      for (const [name, action] of disabled) renderHold(section, (actionText[name] || label(name)) + ' · ' + action.reason);
    }
    if (context.focusedAim) openAim({aim_id: context.focusedAim});
  }

  function thesisReceipt(parent, ref, sessionId) {
    const caption = (ref.step_id ? ref.step_id + ' · receipt ' : 'SHA-256 ') +
      ref.receipt_sha256 + (ref.artifact_sha256 ? ' · artifact ' + ref.artifact_sha256 : '');
    if (root.WorkbenchThesisStudio) {
      root.WorkbenchThesisStudio.receipt(parent, ref, caption, {session_id: sessionId});
    } else muted(parent, caption);
  }

  function actionDraftKey(action) {
    return [action.action, action.aim_id || action.definition?.aim_id || '',
      action.step_id || ''].join(':');
  }

  function mount({host, request, onMutation, download, executionHost, onReadback}) {
    const status = node('p', '', 'message');
    status.setAttribute('role', 'status');
    const body = node('div', null, 'research-workflows');
    host.append(status, body);
    let current = null, nonce = 0, views = {}, pending = false, draft = null;
    const endpoint = (route) => '/api/scientific/' + route + '/' + current.id;
    function band(key, title) {
      const section = node('section', null, 'research-workflow-band');
      section.setAttribute('data-workflow-band', key);
      section.append(node('h3', title));
      body.append(section);
      return section;
    }
    function message(value, error = false) {
      status.textContent = value;
      status.classList.toggle('error', error);
    }
    function showDraft() {
      if (!draft || current.readOnly) return;
      const section = disclosure(body, 'Retained unsaved draft · ' + label(draft.values.action));
      muted(section, 'Copy your draft into the appropriate form after checking the current evidence.');
      const saved = field(section, 'Retained unsaved draft (JSON)', 'textarea',
        JSON.stringify(draft.values, null, 2));
      saved.readOnly = true;
    }
    async function load() {
      if (!current?.id) return;
      const token = ++nonce;
      body.replaceChildren();
      const design = band('design', 'Aims, answerability & study design');
      const execution = band('execution', 'Execution, QC & lineage');
      const packages = band('package', 'Verification & research package');
      if (executionHost) execution.append(executionHost);
      muted(packages, 'Clinical release is disabled.');
      for (const retained of current.pluginPackages) {
        const card = row(packages, 'Verified research package', retained.outcome, []);
        thesisReceipt(card, {receipt_sha256: retained.root_sha256}, current.id);
        if (retained.download_path && download) card.append(button('Export Package (.zip)', async () => {
          try { await download(retained.download_path); }
          catch (error) { message(error.message, true); }
        }));
      }
      message('Loading current research evidence…');
      const routes = ['thesis', 'single-cell'];
      const results = await Promise.allSettled(routes.map(route => request(endpoint(route))));
      if (token !== nonce) return;
      views = {};
      const context = route => ({
        current: () => token === nonce, readOnly: current.readOnly, message,
        mutate: action => token === nonce && mutate(route, action),
        download, focusedAim: current.aimId,
        qc: (step, generation) => request(endpoint('single-cell') +
          '?qc_step=' + encodeURIComponent(step) + '&generation=' + encodeURIComponent(generation)),
      });
      for (const [index, route] of routes.entries()) {
        const result = results[index];
        const page = result.status === 'fulfilled' ? result.value : null;
        if (page?.session_id === current.id && page.status === 'available') {
          views[route] = page;
          if (Number.isInteger(page.revision) && page.revision > (current.revision ?? -1)) {
            current.revision = page.revision;
          }
          if (route === 'thesis') renderThesis(design, packages, page, context(route));
          else renderSingleCell(execution, page, context(route));
        } else {
          renderHold(route === 'thesis' ? design : execution,
            page?.reason || page?.status || 'Current research evidence is unavailable.');
        }
      }
      message('');
      showDraft();
      onReadback?.(current.id, views);
    }
    async function mutate(route, action) {
      const view = views[route];
      if (pending || !current?.id || current.readOnly || !Number.isInteger(view?.revision)) return;
      const selected = current;
      pending = true;
      body.setAttribute('aria-busy', 'true');
      message('Recording research action…');
      let text, failed = false, conflict = false;
      try {
        const result = await request(endpoint(route), {...action, expected_revision: view.revision});
        if (result.package) {
          const {root_sha256, package_id} = result.package;
          if (/^[0-9a-f]{32}$/.test(package_id || '') && download) {
            await download(endpoint('thesis') + '/package/' + package_id);
          }
          text = ['Research package built', digest(root_sha256) && 'SHA-256 ' + root_sha256.slice(0, 12),
            /^[0-9a-f]{32}$/.test(package_id || '') && 'package ' + package_id].filter(Boolean).join(' · ');
        } else text = 'Recorded at workflow revision ' + result.revision + '.';
      } catch (error) {
        failed = true;
        conflict = error.status === 409;
        text = error.message || 'The research action was refused.';
      } finally {
        pending = false;
        body.removeAttribute('aria-busy');
      }
      if (current !== selected) return;
      if (conflict) {
        const grants = new Set(['allow_data_execution', 'allow_model_execution', 'expected_revision']);
        draft = {
          key: actionDraftKey(action),
          values: Object.fromEntries(Object.entries(action).filter(([key]) => !grants.has(key))),
        };
        for (const input of body.querySelectorAll?.('.research-permission input') || []) {
          input.checked = false;
        }
        message(label(text) + '. Reload current evidence to review the latest revision. Your draft is retained.', true);
        status.append(button('Reload current evidence', load));
        return;
      }
      if (!failed && draft?.key === actionDraftKey(action)) draft = null;
      if (!failed) await load();
      message(text, failed);
      if (!failed) onMutation?.();
    }
    return {
      select(id, options = {}) {
        const pluginPackages = (options.packages || []).filter(item =>
          item.download_path?.startsWith('/api/scientific/adaptive/'));
        const packageKey = JSON.stringify(pluginPackages.map(item =>
          [item.package_id, item.root_sha256, item.download_path]));
        if (current?.id === id && current?.scope === options.scope &&
            current?.revision === options.revision && current?.readOnly === options.readOnly &&
            current?.aimId === options.aimId && current?.packageKey === packageKey) return;
        if (current?.id !== id || current?.scope !== options.scope ||
            current?.aimId !== options.aimId || options.readOnly) draft = null;
        current = id ? {id, ...options, pluginPackages, packageKey} : null;
        nonce++;
        views = {};
        if (current) return load();
        body.replaceChildren();
        message('');
      },
    };
  }

  function readiness(aim, session) {
    if (hasVerifiedOutcome(aim)) return 'Ready to include';
    if (currentAnswerability(aim, session.answerability).some(item => item.missing_measurements?.length)) {
      return 'Needs information';
    }
    if (!aim.approved || (aim.blinded_review_required && aim.blinded_review?.status !== 'passed')) {
      return 'Needs review';
    }
    return 'Needs information';
  }

  function mountProject({host, request, download, onOpen}) {
    const toolbar = node('div', null, 'actions research-project-toolbar');
    const status = node('p', '', 'message');
    status.setAttribute('role', 'status');
    const summary = node('div', null, 'thesis-aim-groups');
    const selected = node('section', null, 'thesis-current-session');
    selected.hidden = true;
    host.append(toolbar, status, summary, selected);
    let scope = '', generation = 0, chosen = null, focusedAim = null, readOnly = false;
    let portfolioMode = 'board';
    const panel = mount({
      host: selected, request, download,
      onMutation: () => refresh(),
    });

    toolbar.append(button('Refresh aims and packages', () => refresh()));

    function open(session, aim = null) {
      if (onOpen) return onOpen(session.id, aim?.aim_id);
      chosen = session.id;
      focusedAim = aim?.aim_id ?? null;
      selected.hidden = false;
      panel.select(session.id, {scope, readOnly, revision: session.revision, aimId: focusedAim});
      selected.scrollIntoView?.({block: 'nearest'});
    }

    function group(parent, title, records) {
      const section = node('section', null, 'thesis-aim-group');
      section.append(node('h3', title));
      if (!records.length) muted(section, 'No current aims in this group.');
      for (const {session, aim} of records) {
        const card = row(section, aim.question || aim.aim_id, aim.status, [
          session.title, 'Aim version ' + aim.version,
          aim.blinded_review?.status ? 'Blinded review · ' + label(aim.blinded_review.status) : '',
        ]);
        renderHold(card, aim.reason);
        if (title !== 'Ready to include') {
          for (const proof of currentAnswerability(aim, session.answerability)) {
            if (proof.missing_measurements?.length) {
              renderHold(card, 'Missing measurements · ' + formatMeasurements(proof.missing_measurements));
            }
          }
        }
        card.append(button('Open aim ' + aim.aim_id, () => open(session, aim)));
      }
      parent.append(section);
    }

    async function refresh() {
      const ticket = ++generation;
      status.textContent = 'Loading aims and packages in this project…';
      try {
        const catalog = await request('/api/scientific');
        const sessions = [];
        for (const plan of catalog?.plans || []) {
          if (!/^[0-9a-f]{32}$/.test(plan.session_id || '')) continue;
          if (ticket !== generation) return;
          const title = plan.context?.task?.scientific_objective || 'Planning session ' + plan.session_id.slice(0, 8);
          try {
            const view = await request('/api/scientific/thesis/' + plan.session_id);
            if (view?.session_id === plan.session_id && view.status === 'available') {
              sessions.push({id: plan.session_id, title, revision: view.revision,
                aims: view.aims || [], answerability: view.answerability || [],
                packages: view.packages || []});
            }
          } catch (_) {
            // The current project can contain sessions without a readable thesis binding.
          }
        }
        if (ticket !== generation) return;
        if (sessions.length === 1 && chosen !== sessions[0].id) {
          chosen = sessions[0].id;
          focusedAim = null;
        }
        summary.replaceChildren();
        const records = sessions.flatMap(session => session.aims.map(aim => ({session, aim})));
        const board = node('div', null, 'studio-thesis-board');
        root.WorkbenchThesisStudio?.overview(summary, records, sessions, {
          ...thesisMatrixOptions(({session, aim}) => open(session, aim)),
          mode: portfolioMode,
          setMode(mode) {
            portfolioMode = mode;
            board.hidden = mode !== 'board';
          },
        });
        summary.append(board);
        for (const title of ['Ready to include', 'Needs review', 'Needs information']) {
          group(board, title, records.filter(({aim, session}) => readiness(aim, session) === title));
        }
        if (sessions.length) {
          const controls = node('section', null, 'studio-thesis-build');
          controls.append(node('h3', 'Build and export'));
          const choice = field(controls, 'Investigation', 'select');
          for (const session of sessions) option(choice, session.id, session.title);
          if (sessions.some(session => session.id === chosen)) choice.value = chosen;
          controls.append(button('Open investigation workflow', () => {
            const current = sessions.find(session => session.id === choice.value);
            if (current) open(current);
          }));
          summary.append(controls);
        }
        const current = sessions.find(session => session.id === chosen);
        if (current && !onOpen) {
          selected.hidden = false;
          panel.select(current.id, {scope, readOnly, revision: current.revision,
            aimId: focusedAim});
        } else if (chosen) {
          chosen = null;
          focusedAim = null;
          selected.hidden = true;
          panel.select(null, {scope, readOnly, revision: null});
        }
        status.textContent = sessions.length ? '' : 'No investigations currently have accessible research aims or packages.';
      } catch (error) {
        if (ticket === generation) status.textContent = error.message || 'The thesis workspace is unavailable.';
      }
    }

    return {
      select({projectId, readOnly: nextReadOnly}) {
        if (scope !== projectId) {
          generation++;
          chosen = null;
          focusedAim = null;
          portfolioMode = 'board';
          summary.replaceChildren();
          selected.hidden = true;
          panel.select(null, {scope: projectId, readOnly: nextReadOnly, revision: null});
        }
        scope = projectId;
        readOnly = nextReadOnly;
        return refresh();
      },
    };
  }

  root.WorkbenchResearch = Object.freeze({mount, mountProject});
})(typeof window !== 'undefined' ? window : globalThis);
