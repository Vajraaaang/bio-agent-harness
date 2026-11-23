(function (root) {
  'use strict';

  const isDigest = text => typeof text === 'string' && /^[a-f0-9]{64}$/.test(text);
  const label = value => String(value || '').replaceAll('_', ' ').replaceAll('-', ' ');
  const lines = input => input.value.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  const unique = values => [...new Set(values)];

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function button(text, handler, className = 'secondary') {
    const element = node('button', text, className);
    element.type = 'button';
    element.addEventListener('click', handler);
    return element;
  }

  function field(parent, text, tag = 'input', initial = '') {
    const wrapper = node('label', null, 'research-field');
    const input = node(tag);
    input.setAttribute('aria-label', text);
    if (tag === 'textarea') input.rows = 3;
    input.value = initial;
    wrapper.append(node('span', text), input);
    parent.append(wrapper);
    return input;
  }

  function select(parent, text, choices, initial) {
    const input = field(parent, text, 'select');
    for (const [value, caption] of choices) {
      const option = node('option', caption);
      option.value = value;
      input.append(option);
    }
    input.value = initial ?? choices[0]?.[0] ?? '';
    return input;
  }

  function details(parent, summary) {
    const section = node('details', null, 'research-disclosure');
    section.append(node('summary', summary));
    parent.append(section);
    return section;
  }

  function required(input, name) {
    const value = input.value.trim();
    if (!value) throw new Error(name + ' is required.');
    return value;
  }

  function sha(input, name) {
    const value = required(input, name);
    if (!isDigest(value)) throw new Error(name + ' must be an exact SHA-256.');
    return value;
  }

  function advancedJSON(input, name) {
    if (!input.value.trim()) return null;
    let data;
    try {
      data = JSON.parse(input.value);
    } catch (_) {
      throw new Error(name + ' must be valid JSON.');
    }
    if (!data || Array.isArray(data) || typeof data !== 'object') {
      throw new Error(name + ' must be a JSON object.');
    }
    return data;
  }

  function repeat(parent, title, addLabel, maximum, initial, fields) {
    const container = node('fieldset', null, 'studio-thesis-repeater');
    const rows = node('div');
    const values = [];
    container.append(node('legend', title), rows);
    parent.append(container);
    const addControl = button(addLabel, () => add({}));
    function render() {
      rows.replaceChildren(...values.map(row => row.element));
      addControl.disabled = values.length >= maximum;
    }
    function add(value) {
      if (values.length >= maximum) return;
      const element = node('div', null, 'studio-thesis-row');
      const row = {element, collect: fields(element, value)};
      element.append(button('Remove ' + addLabel.slice(4).toLowerCase(), () => {
        values.splice(values.indexOf(row), 1);
        render();
      }));
      values.push(row);
      render();
    }
    container.append(addControl);
    initial.forEach(add);
    return () => values.map(row => row.collect());
  }

  function dependencyChoices(form, aims, aim) {
    const known = unique([
      ...(aim?.depends_on || []),
      ...aims.map(item => item.aim_id).filter(identity => identity !== aim?.aim_id),
    ]);
    if (!known.length) return () => [];
    const choices = node('fieldset', null, 'studio-thesis-choices');
    choices.append(node('legend', 'Depends on'));
    const controls = known.map(identity => {
      const option = node('label', null, 'check');
      const box = node('input');
      box.type = 'checkbox';
      box.checked = (aim?.depends_on || []).includes(identity);
      option.append(box, node('span', identity));
      choices.append(option);
      return {box, identity};
    });
    form.append(choices);
    return () => controls.filter(({box}) => box.checked).map(({identity}) => identity);
  }

  function aimDefinition(form, {aims = [], aim = null} = {}) {
    const definition = aim?.declaration || {};
    const primary = node('div', null, 'studio-thesis-fields');
    form.append(primary);
    const id = field(primary, 'Aim ID', 'input', aim?.aim_id);
    id.readOnly = Boolean(aim);
    const kind = select(primary, 'Aim kind', [
      ['exploratory', 'Exploratory'], ['confirmatory', 'Confirmatory'],
    ], aim?.kind);
    const question = field(primary, 'Research question', 'textarea', aim?.question);
    const estimand = field(primary, 'Estimand', 'textarea', aim?.estimand);
    const depend = dependencyChoices(form, aims, aim);
    const review = field(form, 'Require independent blinded review');
    review.type = 'checkbox';
    review.checked = aim?.blinded_review_required === true;
    review.disabled = review.checked;
    const explanationList = repeat(
      form, 'Competing explanations', 'Add competing explanation', 8,
      definition.competing_explanations || [],
      (target, value) => {
        const identity = field(target, 'Explanation ID', 'input', value.explanation_id);
        const description = field(target, 'Explanation', 'textarea', value.explanation);
        const check = field(target, 'Discriminating check', 'textarea', value.discriminating_check);
        return () => ({
          explanation_id: required(identity, 'Explanation ID'),
          explanation: required(description, 'Explanation'),
          discriminating_check: required(check, 'Discriminating check'),
        });
      },
    );
    const input = field(form, 'Input artifact SHA-256', 'textarea',
      (definition.input_sha256s || []).join('\n'));
    const confirmatory = node('fieldset', null, 'studio-thesis-fields');
    confirmatory.append(node('legend', 'Confirmatory multiplicity'));
    form.append(confirmatory);
    const evaluation = field(confirmatory, 'Evaluation artifact SHA-256', 'textarea',
      (definition.evaluation_sha256s || []).join('\n'));
    const family = field(confirmatory, 'Multiplicity family ID', 'input',
      definition.multiplicity?.family_id);
    const hypotheses = field(confirmatory, 'Hypotheses SHA-256', 'input',
      definition.multiplicity?.hypotheses_sha256);
    const fdr = field(confirmatory, 'FDR', 'input', definition.multiplicity?.fdr ?? '');
    fdr.type = 'number';
    fdr.step = 'any';
    fdr.min = '0';
    fdr.max = '1';
    const members = field(confirmatory, 'Other family aim IDs', 'textarea',
      (definition.multiplicity?.aim_ids || []).filter(member => member !== aim?.aim_id).join('\n'));
    const sync = () => { confirmatory.hidden = kind.value !== 'confirmatory'; };
    kind.addEventListener('change', sync);
    sync();
    const advanced = details(form, aim ? 'Advanced JSON' : 'Inputs, dependencies, and multiplicity');
    const advancedLabel = aim ? 'Revised aim declaration (JSON)' : 'Additional aim fields (JSON)';
    const override = field(advanced, advancedLabel, 'textarea');
    return () => {
      const supplied = advancedJSON(override, advancedLabel);
      if (aim && supplied) {
        if (supplied.aim_id !== aim.aim_id) throw new Error('Use this aim’s exact ID.');
        if (review.disabled && supplied.blinded_review_required !== true) {
          throw new Error('Retain this aim’s independent blinded review requirement.');
        }
        return supplied;
      }
      const aimId = required(id, 'Aim ID');
      const result = {
        aim_id: aimId, kind: kind.value, question: required(question, 'Research question'),
        estimand: required(estimand, 'Estimand'), blinded_review_required: review.checked,
      };
      if (supplied) return {...supplied, ...result};
      if (aim && !aim.declaration) {
        throw new Error('Use Advanced JSON to supply the complete retained aim declaration.');
      }
      const dependencies = depend();
      if (dependencies.includes(aimId)) throw new Error('An aim cannot depend on itself.');
      if (dependencies.length) result.depends_on = dependencies;
      const artifacts = unique(lines(input));
      if (artifacts.some(value => !isDigest(value))) {
        throw new Error('Input references must be exact SHA-256 digests.');
      }
      if (artifacts.length) result.input_sha256s = artifacts;
      if (definition.requirement_sources?.length) {
        result.requirement_sources = definition.requirement_sources;
      }
      const explanations = explanationList();
      if (explanations.length) result.competing_explanations = explanations;
      if (kind.value === 'confirmatory') {
        const scoped = unique(lines(evaluation));
        if (!scoped.length || scoped.some(value => !artifacts.includes(value))) {
          throw new Error('Evaluation requires an exact subset of the input artifact digests.');
        }
        const rate = Number(fdr.value);
        if (!(rate > 0 && rate < 1)) throw new Error('FDR must be between 0 and 1.');
        result.evaluation_sha256s = scoped;
        result.multiplicity = {
          family_id: required(family, 'Multiplicity family ID'),
          aim_ids: unique([aimId, ...lines(members)]),
          hypotheses_sha256: sha(hypotheses, 'Hypotheses SHA-256'), fdr: rate,
        };
      }
      return result;
    };
  }

  function receipt(parent, reference, caption, scope = {}) {
    const digest = reference.receipt_sha256 || reference.artifact_sha256;
    if (!isDigest(digest)) return;
    const open = root.WorkbenchStudio?.inspectReceipt;
    const text = caption || digest.slice(0, 8);
    const style = caption ? 'studio-thesis-receipt' : 'studio-thesis-receipt studio-receipt-token';
    const control = typeof open === 'function' ? button(text, () => open({
      ...scope, ...reference, label: scope.label || reference.step_id || 'Thesis receipt',
    }), 'secondary ' + style) : node('code', text, style);
    control.setAttribute('title', digest);
    parent.append(control);
  }

  function explanationAssessment(form, aim, references, scope = {}) {
    const explanation = select(form, 'Competing explanation',
      aim.competing_explanations.map(item => [item.explanation_id,
        item.explanation || item.explanation_id]));
    // Keep the retained assessment vocabulary for the signed evidence contract.
    const conclusion = select(form, 'Conclusion', [
      ['supported', 'Supported'], ['weakened', 'Weakened'], ['inconclusive', 'Inconclusive'],
    ]);
    const rationale = field(form, 'Explanation rationale', 'textarea');
    const choices = node('fieldset', null, 'studio-thesis-choices');
    choices.append(node('legend', 'Current evidence receipt'));
    const controls = references.map((ref, index) => {
      const choice = node('label', null, 'check');
      const input = node('input');
      input.type = 'radio';
      input.name = 'explanation-evidence-' + aim.aim_id;
      input.setAttribute('aria-label', 'Use evidence ' + ref.step_id + ' ' + (index + 1));
      choice.append(input, node('span', ref.step_id));
      choices.append(choice);
      receipt(choices, ref, ref.receipt_sha256.slice(0, 8), scope);
      return {input, ref};
    });
    form.append(choices);
    const json = field(details(form, 'Advanced JSON'), 'Explanation assessment (JSON)', 'textarea');
    return () => {
      const supplied = advancedJSON(json, 'Explanation assessment');
      if (supplied) return supplied;
      const selected = controls.find(({input}) => input.checked);
      if (!selected) throw new Error('Select one current evidence receipt.');
      return {
        explanation_id: explanation.value, conclusion: conclusion.value,
        rationale: required(rationale, 'Explanation rationale'), evidence: selected.ref,
      };
    };
  }

  function reviewers(form) {
    const input = field(form, 'Independent reviewer IDs (one per line)', 'textarea');
    input.required = true;
    const chips = node('ul', null, 'studio-thesis-choices');
    chips.setAttribute('aria-label', 'Assigned reviewer IDs');
    form.append(chips);
    const render = () => chips.replaceChildren(
      ...unique(lines(input)).slice(0, 8).map(value => node('li', value)),
    );
    input.addEventListener('input', render);
    return () => lines(input);
  }

  function pathSegments(input) {
    const value = input.value.trim();
    if (!value) return [];
    const segments = value.split('.').map(value => /^[0-9]+$/.test(value) ? Number(value) : value);
    if (segments.length > 16 || segments.some(part => part === '' ||
        (Number.isInteger(part) && !Number.isSafeInteger(part)))) {
      throw new Error('Use a bounded dot-separated JSON field path.');
    }
    return segments;
  }

  function answerBinding(target) {
    const name = field(target, 'Answer field');
    const step = field(target, 'Source step ID');
    const projection = select(target, 'Field projection', [
      ['json', 'JSON field'], ['tier_a_gene_statistics', 'Retained gene statistics'],
      ['failure_reason', 'Failed attempt reason'],
    ]);
    const port = field(target, 'Output port');
    const path = field(target, 'JSON field path');
    const update = () => {
      port.disabled = projection.value !== 'json';
      path.disabled = projection.value !== 'json';
    };
    projection.addEventListener('change', update);
    update();
    return () => {
      const value = {step_id: required(step, 'Source step ID'), projection: projection.value};
      if (projection.value === 'json') {
        value.port = required(port, 'Output port');
        value.path = pathSegments(path);
      } else if (projection.value === 'tier_a_gene_statistics') value.port = 'result';
      return [required(name, 'Answer field'), value];
    };
  }

  function synthesisSpecification(form, view, readiness) {
    const preflight = node('section', null, 'studio-thesis-preflight');
    preflight.append(node('h5', 'Package preflight'));
    const aims = view.aims || [];
    const counts = groupCounts(aims.map(aim => ({aim, session: view})), readiness);
    if (aims.length) {
      preflight.append(node('p',
        counts[0] + ' ready to include · ' + counts[1] + ' need review · ' +
        counts[2] + ' need information.'));
      if (counts[1] || counts[2]) preflight.append(node('p',
        'Open holds can prevent a completed package. Choose the scope supported by the retained evidence.',
        'research-hold'));
    }
    form.append(preflight);
    const settings = node('div', null, 'studio-thesis-fields');
    form.append(settings);
    const schema = field(settings, 'Answer schema path');
    schema.required = true;
    const identity = field(settings, 'Answer schema SHA-256');
    const question = field(settings, 'Synthesis question', 'textarea');
    const outcome = select(settings, 'Package outcome', [
      ['completed', 'Completed'], ['partial', 'Partial'],
      ['abstained', 'Abstained'], ['refused', 'Refused'],
    ], 'partial');
    const fields = repeat(form, 'Answer bindings', 'Add field binding', 64, [], answerBinding);
    const data = details(form, 'Data notes');
    const notes = ['observations', 'declarations', 'inferences', 'unknowns'].map(name => [
      name, field(data, name[0].toUpperCase() + name.slice(1), 'textarea'),
    ]);
    const follow = repeat(form, 'Follow-up experiments', 'Add follow-up', 20, [], target => {
      const experiment = field(target, 'Experiment', 'textarea');
      const uncertainty = field(target, 'Uncertainty', 'textarea');
      const observable = field(target, 'Observable', 'textarea');
      return () => ({
        experiment: required(experiment, 'Experiment'),
        uncertainty: required(uncertainty, 'Uncertainty'),
        observable: required(observable, 'Observable'),
      });
    });
    const override = field(details(form, 'Advanced JSON'), 'Synthesis specification (JSON)', 'textarea');
    return () => {
      const answer_schema = required(schema, 'Answer schema path');
      const supplied = advancedJSON(override, 'Synthesis specification');
      if (supplied) return {answer_schema, spec: supplied};
      const bindings = fields();
      if (new Set(bindings.map(([name]) => name)).size !== bindings.length) {
        throw new Error('Answer field names must be distinct.');
      }
      const spec = {
        question: required(question, 'Synthesis question'), outcome: outcome.value,
        answer_schema_sha256: sha(identity, 'Answer schema SHA-256'),
      };
      if (bindings.length) spec.fields = Object.fromEntries(bindings);
      const populated = notes.map(([name, input]) => [name, lines(input)])
        .filter(([, values]) => values.length);
      if (populated.length) spec.data_notes = Object.fromEntries(populated);
      const followUp = follow();
      if (followUp.length) spec.follow_up = followUp;
      return {answer_schema, spec};
    };
  }

  const groups = ['Ready to include', 'Needs review', 'Needs information'];

  function groupCounts(records, readiness) {
    return groups.map(state => records.filter(({aim, session}) =>
      readiness ? readiness(aim, session) === state : false).length);
  }

  function kpis(parent, records, sessions, readiness) {
    const counts = groupCounts(records, readiness);
    const panels = records.map(({aim}) => aim.blinded_review).filter(Boolean);
    const packageCount = sessions.reduce((total, session) => total +
      (session.packages || []).filter(item => item.status === 'current').length, 0);
    const reviewCounts = ['passed', 'needs_evidence', 'vetoed'].map(status =>
      panels.filter(panel => panel.status === status).length);
    const container = node('dl', null, 'studio-thesis-kpis');
    [
      ['Total aims', records.length], ...groups.map((state, index) => [state, counts[index]]),
      ['Blinded panels', reviewCounts[0] + ' passed · ' + reviewCounts[1] +
        ' need evidence · ' + reviewCounts[2] + ' vetoed'],
      ['Current verified packages', packageCount],
    ].forEach(([title, value]) => {
      const item = node('div', null, 'studio-thesis-kpi');
      item.append(node('dt', title), node('dd', String(value)));
      container.append(item);
    });
    parent.append(container);
  }

  function status(parent, state, caption) {
    const element = node('span', caption || label(state), 'studio-thesis-status');
    element.setAttribute('data-status', state);
    parent.append(element);
  }

  function answerabilityCell(target, {aim, session}, options) {
    const proofs = options.currentAnswerability(aim, session.answerability);
    if (!proofs.length) target.append(node('span', 'Awaiting an answerability proof'));
    const complete = options.hasVerifiedOutcome(aim);
    const openProofs = proofs.filter(proof => !complete || !proof.missing_measurements?.length);
    for (const proof of openProofs) {
      const body = details(target, proof.step_id + ' · ' + label(proof.status));
      status(body, proof.status);
      if (proof.missing_measurements?.length) {
        body.open = true;
        body.append(node('p', options.formatMeasurements(proof.missing_measurements)));
      }
      if (proof.reason) body.append(node('p', label(proof.reason)));
      receipt(body, {receipt_sha256: proof.proof_sha256, step_id: proof.step_id}, null,
        {session_id: session.id, label: 'Answerability proof'});
    }
    if (openProofs.length !== proofs.length) {
      const history = details(target, 'Earlier measurement holds');
      for (const proof of proofs.filter(item => !openProofs.includes(item))) {
        history.append(node('p', proof.step_id + ' · ' +
          options.formatMeasurements(proof.missing_measurements)));
        receipt(history, {receipt_sha256: proof.proof_sha256, step_id: proof.step_id}, null,
          {session_id: session.id, label: 'Earlier answerability proof'});
      }
    }
  }

  function explanationCell(target, {aim, session}) {
    const explanations = aim.competing_explanations || [];
    if (!explanations.length) {
      target.append(node('span', 'No competing explanations declared'));
      return;
    }
    const stale = explanations.filter(item => item.status === 'stale').length;
    const open = explanations.filter(item => ['unassessed', 'withheld'].includes(item.status)).length;
    const body = details(target, (explanations.length - stale - open) + ' assessed · ' +
      stale + ' stale · ' + open + ' open');
    for (const item of explanations) {
      const row = node('p', item.explanation || item.explanation_id);
      status(row, item.status);
      if (item.reason) row.append(node('span', label(item.reason)));
      if (isDigest(item.evidence?.receipt_sha256)) {
        receipt(row, item.evidence, null,
          {session_id: session.id, label: 'Competing-explanation evidence'});
      }
      body.append(row);
    }
  }

  function reviewCell(target, {aim, session}) {
    const panel = aim.blinded_review;
    if (!panel) {
      target.append(node('span',
        aim.blinded_review_required ? 'Blinded review required' : 'No panel required'));
      return;
    }
    status(target, panel.status);
    if (Number.isInteger(panel.submitted_reviews) && Number.isInteger(panel.assigned_reviewers)) {
      target.append(node('p', panel.submitted_reviews + ' of ' +
        panel.assigned_reviewers + ' assessments'));
    }
    if (panel.unresolved_disagreement) target.append(node('p', 'Reviewer disagreement is unresolved.'));
    if (panel.veto_codes?.length) target.append(node('p', 'Vetoes · ' + panel.veto_codes.map(label).join(', ')));
    if (panel.reason) target.append(node('p', label(panel.reason)));
    for (const identity of panel.receipt_sha256s || []) {
      receipt(target, {receipt_sha256: identity}, null,
        {session_id: session.id, label: 'Blinded assessment'});
    }
  }

  function outcomeCell(target, {aim, session}, options) {
    status(target, aim.status);
    if (aim.reason) target.append(node('p', label(aim.reason), 'research-hold'));
    const references = options.evidenceRefs(aim.outcome?.evidence);
    if (!references.length) return;
    const body = details(target, references.length + (references.length === 1 ? ' receipt' : ' receipts'));
    for (const ref of references) {
      const item = node('div');
      item.append(node('span', ref.step_id + ' · '));
      receipt(item, ref, null,
        {session_id: session.id, label: 'Aim outcome evidence'});
      body.append(item);
    }
  }

  function tableHead(table, columns) {
    const heading = node('thead');
    const row = node('tr');
    for (const title of columns) {
      const cell = node('th', title);
      cell.scope = 'col';
      row.append(cell);
    }
    heading.append(row);
    table.append(heading);
  }

  function packageTable(parent, packages, sessionId, context) {
    const region = node('div', null, 'studio-thesis-matrix-scroll');
    region.tabIndex = 0;
    region.setAttribute('aria-label', 'Scrollable package inventory');
    const table = node('table', null, 'studio-thesis-matrix studio-thesis-packages-table');
    table.setAttribute('aria-label', 'Supporting evidence packages');
    const columns = ['Package', 'Status', 'Receipt', 'Export'];
    tableHead(table, columns);
    const body = node('tbody');
    for (const item of packages) {
      const row = node('tr', null, 'research-item');
      row.setAttribute('data-status', item.status);
      const title = node('th', label(item.outcome) + ' research package', 'studio-thesis-cell');
      title.scope = 'row';
      const cells = columns.slice(1).map(() => node('td', null, 'studio-thesis-cell'));
      status(cells[0], item.status);
      receipt(cells[1], {receipt_sha256: item.root_sha256}, 'SHA-256 ' + item.root_sha256,
        {session_id: sessionId, label: 'Thesis package'});
      if (item.status === 'current' && item.download && context.download) {
        cells[2].append(button('Export research package (.zip)', async () => {
          try {
            await context.download(item.download);
          } catch (error) {
            context.message(error.message || 'The thesis export is unavailable.', true);
          }
        }));
      } else if (item.status === 'stale') {
        cells[2].append(node('p', 'Build from current evidence',
          'research-hold'));
      } else {
        cells[2].append(node('span', 'Export unavailable'));
      }
      row.append(title, ...cells);
      body.append(row);
    }
    table.append(body);
    region.append(table);
    parent.append(region);
  }

  function matrix(parent, records, options) {
    if (!records.length) {
      parent.append(node('p', 'No research aims are recorded.', 'field-note'));
      return;
    }
    const table = node('table', null, 'studio-thesis-matrix');
    table.setAttribute('aria-label', 'Aim Evidence Matrix');
    const titles = ['Aim', 'Answerability', 'Competing explanations', 'Blinded review',
      'Outcome and receipts', 'Action'];
    tableHead(table, titles);
    const body = node('tbody');
    for (const record of records) {
      const {aim, session} = record;
      const row = node('tr');
      const title = node('th', null, 'studio-thesis-cell');
      title.scope = 'row';
      title.append(node('strong', aim.question || aim.aim_id));
      if (aim.question) title.append(node('p', aim.aim_id));
      title.append(node('p', 'Version ' + aim.version + ' · ' + label(aim.kind)));
      if (session.title) title.append(node('p', session.title));
      const cells = titles.slice(1).map(() => node('td', null, 'studio-thesis-cell'));
      answerabilityCell(cells[0], record, options);
      explanationCell(cells[1], record);
      reviewCell(cells[2], record);
      outcomeCell(cells[3], record, options);
      cells[4].append(button('Open aim ' + aim.aim_id, () => options.openAim(record)));
      row.append(title, ...cells);
      body.append(row);
    }
    table.append(body);
    parent.append(table);
  }

  function overview(parent, records, sessions, options) {
    const container = node('section', null, 'studio-thesis-overview');
    container.setAttribute('aria-label', 'Thesis overview');
    kpis(container, records, sessions, options.readiness);
    const controls = node('div', null, 'actions studio-thesis-modes');
    controls.setAttribute('aria-label', 'Thesis view');
    const grid = node('div', null, 'studio-thesis-matrix-scroll');
    grid.tabIndex = 0;
    grid.setAttribute('aria-label', 'Scrollable evidence matrix');
    let built = false;
    let mode = options.mode || 'board';
    const board = button('Board', () => show('board'));
    const evidence = button('Evidence Matrix', () => show('matrix'));
    function show(next) {
      mode = next;
      board.setAttribute('aria-pressed', String(mode === 'board'));
      evidence.setAttribute('aria-pressed', String(mode === 'matrix'));
      if (mode === 'matrix' && !built) {
        matrix(grid, records, options);
        built = true;
      }
      grid.hidden = mode !== 'matrix';
      options.setMode?.(mode);
    }
    controls.append(board, evidence);
    container.append(controls, grid);
    parent.append(container);
    show(mode);
  }

  root.WorkbenchThesisStudio = Object.freeze({
    aimDefinition, explanationAssessment, overview, packageTable, receipt, reviewers,
    synthesisSpecification,
  });
})(typeof window !== 'undefined' ? window : globalThis);
