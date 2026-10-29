/* Recorded scientific conversation. Presentation only: no network or execution. */
(function (root) {
  'use strict';
  const ACTIVE = new Set(['queued', 'inspecting', 'planning', 'recording_review', 'preparing', 'running', 'cancel_requested', 'finalizing']);
  const FAILURES = new Set(['operation_failed', 'failed', 'recovery_required', 'cancelled', 'interrupted', 'interrupted_service', 'infrastructure_failure', 'execution_failure_unclassified']);
  const PLAN_ATTENTION = new Set(['awaiting_information', 'needs_information', 'refusal', 'invalid_proposal', 'rejected']);
  const LABELS = {
    queued: 'Request queued', inspecting: 'Inspecting input metadata', planning: 'Preparing a proposal',
    awaiting_operator_review: 'Awaiting operator review', recording_review: 'Recording operator decision',
    approved: 'Plan approved', preparing: 'Preparing controlled execution', running: 'Analysis running',
    cancel_requested: 'Cancellation requested', finalizing: 'Finalizing recorded outputs',
    completed_review_passed: 'Analysis completed', completed_review_failed: 'Analysis completed with review findings',
    completed_presentation: 'Presentation completed', awaiting_information: 'More information needed', needs_information: 'More information needed',
    refusal: 'Request outside supported scope', invalid_proposal: 'Proposal could not be validated', rejected: 'Plan rejected',
    operation_failed: 'Operation failed', failed: 'Analysis failed', recovery_required: 'Recovery requires review',
    cancelled: 'Operation cancelled', interrupted: 'Operation interrupted', interrupted_service: 'Service interrupted',
    infrastructure_failure: 'Execution infrastructure failed', execution_failure_unclassified: 'Execution needs investigation'
  };
  const text = value => typeof value === 'string' ? value : '';
  const label = value => LABELS[value] || (text(value).replaceAll('_', ' ').replace(/^./, c => c.toUpperCase()) || 'Status unavailable');
  const navigation = (view, caption) => ({type: 'navigate', view, label: caption});
  const record = (kind, eyebrow, title, body, details) => ({kind, eyebrow, title, body, details});
  function inputReference(state) {
    const input = state && state.proposal && state.proposal.external_input;
    if (input && typeof input === 'object') return JSON.parse(JSON.stringify(input));
    return state && text(state.source) ? {path: state.source} : null;
  }
  function modeFor(state, config) {
    if (state && state.plan_author === 'recorded_fixture') return 'Recorded offline proposal';
    if (state && state.plan_author === 'codex_agent') return 'Recorded agent proposal';
    if (state && state.plan_author === 'operator_created_recorded_plan') return 'Operator-created plan';
    return config.model_access === true ? 'Agent-assisted proposal' : 'Operator-created plan';
  }
  function failureSummary(value) {
    const raw = text(value);
    if (raw.includes('data_sha256_mismatch')) return 'The dataset checksum does not match the registered file identity. Review the input path and checksum before preparing another plan.';
    if (/source.identity.changed|planning_source_identity_changed/i.test(raw)) return 'The application source changed after this plan was prepared. Inspect the retained evidence and prepare a new plan with the current source.';
    return raw.split('\n')[0].slice(0,360);
  }
  function buildModel(state, config) {
    config = config || {};
    const mode = modeFor(state, config);
    const scope = mode === 'Recorded offline proposal'
      ? 'Recorded offline fixture · No live model conversation.'
      : mode === 'Recorded agent proposal'
        ? 'This is a retained bounded proposal, not a live conversation. ' + (Number.isInteger(state.model_sessions_attempted) ? state.model_sessions_attempted + ' model sessions were attempted. ' : '') + 'Follow-ups prepare a separate plan.'
        : mode === 'Agent-assisted proposal'
          ? 'The agent prepares a bounded proposal. Each follow-up prepares a separate plan.'
          : 'No model conversation is requested. Plans are operator-created.';
    const model = {mode, scope, records: [], events: [], attempts: [], suggestions: [], primaryAction: null, summary: null, status: state && text(state.status), workerLiveness: state && text(state.worker_liveness), input: inputReference(state)};
    if (!state) return model;
    const status = text(state.status), proposal = state.proposal;
    model.records.push(record('user', 'Your question', '', text(state.question) || 'No biological question was recorded.'));
    if (model.input) model.inputCaption = text(model.input.path).split('/').pop() || 'Bound input';
    const planning = state.planning_result;
    if (planning && typeof planning === 'object') {
      const decision = planning.decision || {};
      const reason = text(decision.rationale) || text(decision.message) || text(decision.reason) || text(planning.reason);
      model.records.push(record('planner', mode === 'Recorded offline proposal' ? 'Offline planner record' : 'Planner record', label(text(planning.status) || status), reason || 'Inspect the retained plan for the recorded decision and its evidence.', planning));
    } else if (proposal) {
      const parameters = proposal.configuration && proposal.configuration.parameters || {};
      const preview = proposal.preview || {};
      const observed = Number.isFinite(preview.cells) && Number.isFinite(preview.genes)
        ? preview.cells.toLocaleString() + ' cells and ' + preview.genes.toLocaleString() + ' genes observed in the input preview. '
        : '';
      model.records.push(record('plan', 'Analysis plan', 'Ready', observed + 'Review the input, methods and execution limits.', {
        input_preview: preview, workflow: parameters.workflow || null,
        settings: parameters, declarations: proposal.configuration && proposal.configuration.declarations || null
      }));
    }
    if (state.review) {
      model.records.push(record('operator', 'Operator review', label(state.review.decision || 'Decision recorded'), text(state.review.rationale) || 'A decision was recorded.', {
        reviewer: state.review.reviewer || state.review.reviewer_label || null,
        decision: state.review.decision || null, confirmations: state.review.confirmations || null
      }));
    }
    // Each entry maps to one retained transition. No inferred/pending stages are added.
    model.events = (Array.isArray(state.state_history) ? state.state_history : [])
      .filter(event => event && typeof event.to === 'string')
      .map((event, index, events) => ({status: event.to, title: label(event.to), from: text(event.from), at: text(event.at), current: index === events.length - 1 && event.to === status}));
    model.attempts = (Array.isArray(state.execution_history) ? state.execution_history : []).map(attempt => ({
      attempt: Number.isInteger(attempt.attempt) ? attempt.attempt : null,
      status: text(attempt.status), elapsedSeconds: Number.isFinite(attempt.elapsed_sec) ? attempt.elapsed_sec : null
    }));
    model.failureRecord = state.error || (state.execution && state.execution.reason ? {message: state.execution.reason} : null);
    const failure = failureSummary(text(state.error && state.error.message) || text(state.execution && state.execution.reason));
    let title = label(status), body = 'The recorded status is ' + (status || 'unavailable') + '. Inspect the retained operation for details.';
    let action = navigation('execute', 'Inspect operation');
    let tone = 'neutral';
    if (status === 'awaiting_operator_review') {
      body = 'Review the plan and record your decision.';
      action = navigation('review', 'Review analysis plan'); tone = 'attention';
    } else if (status === 'approved') {
      body = config.execution_allowed === true
        ? 'Ready to run. Open the execution controls when you are ready.'
        : 'Approved. Data execution is disabled for this service.';
      action = navigation('execute', 'Open run controls'); tone = 'ready';
    } else if (status === 'completed_review_passed' || status === 'completed_review_failed') {
      body = status === 'completed_review_failed'
        ? 'Inspect the output-check findings alongside the figures. Independent scientific review remains separate.'
        : 'Analysis and output checks completed. Independent scientific review remains separate.';
      action = navigation('results', 'Inspect figures and evidence'); tone = status === 'completed_review_failed' ? 'attention' : 'complete';
    } else if (FAILURES.has(status)) {
      body = (failure ? failure + ' ' : '') + (status === 'recovery_required'
        ? 'Worker liveness and remaining budget must be reviewed. Nothing restarts automatically.'
        : 'The attempt and its available evidence are retained. Inspect the run controls before any eligible recovery action.');
      tone = 'failure';
    } else if (PLAN_ATTENTION.has(status)) {
      body = failure || 'Inspect the recorded decision, address missing information or revise the question within the supported scientific scope.';
      action = navigation('review', 'Inspect decision'); tone = 'attention';
    } else if (ACTIVE.has(status)) {
      body = status === 'cancel_requested'
        ? 'Cancellation requested. The worker has not yet been confirmed stopped.'
        : 'Execution status updates as new controller records arrive.';
      tone = 'running';
    }
    model.summary = {title, body, tone};
    model.primaryAction = action;
    if (state.id) {
      const original = text(state.question);
      const prompts = status === 'completed_review_passed' || status === 'completed_review_failed'
        ? [
          ['Review quality concerns', 'For the input associated with the earlier question “' + original + '”, which QC concerns can this registered workflow inspect?'],
          ['Inspect batch structure', 'For the input associated with the earlier question “' + original + '”, inspect batch structure and describe what the supported integration diagnostics can and cannot establish.']
        ]
        : [['Refine this question', original]];
      model.suggestions = prompts.map(([caption, question]) => ({caption, action: {type: 'draft', question, sourceRunId: state.id, input: inputReference(state)}}));
    }
    return model;
  }
  function element(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = content;
    return node;
  }
  const renderedRuns = new WeakMap();
  const workflowStates = new WeakMap();
  const SCIENTIFIC_SECTIONS = [
    ['Data', 'What data do I have?'],
    ['Question', 'What question am I investigating?'],
    ['Methods', 'Which methods are appropriate, and why?'],
    ['Results', 'What happened?'],
    ['Conclusion', 'What can I conclude, and what remains uncertain?']
  ];
  function preserveConversationView(container, preserve) {
    if (!preserve || typeof container.querySelectorAll !== 'function' || typeof container.closest !== 'function') return () => {};
    function entries() {
      const counts = new Map();
      return Array.from(container.querySelectorAll('details.wc-disclosure,details.wc-raw-json')).map(node => {
        const turn = node.closest('[data-turn-id]'), workflow = node.closest('[data-workflow-id]'), article = node.closest('article');
        const title = node.querySelector('.wc-disclosure-title') || node.querySelector('summary');
        const prefix = (turn && turn.getAttribute('data-turn-id') || workflow && workflow.getAttribute('data-workflow-id') || '') + ':' + (article && article.className || '') + ':' + (title && title.textContent || '');
        const ordinal = counts.get(prefix) || 0;counts.set(prefix, ordinal + 1);return [prefix + ':' + ordinal, node];
      });
    }
    const saved = new Map(entries().map(([key, node]) => [key, {open: node.open, scroll: node.querySelector('.wc-disclosure-body')?.scrollTop || 0}]));
    const active = document.activeElement, focused = active && typeof container.contains === 'function' && container.contains(active) && (active.id || active.getAttribute('id'));
    const selection = focused && typeof active.selectionStart === 'number' ? [active.selectionStart, active.selectionEnd] : null;
    return () => {
      for (const [key, node] of entries()) if (saved.has(key)) {const value = saved.get(key);node.open = value.open;const body = node.querySelector('.wc-disclosure-body');if (body) body.scrollTop = value.scroll;}
      if (!focused || typeof document.getElementById !== 'function') return;
      const field = document.getElementById(focused), section = field && field.closest('details.wc-workflow-section');
      if (field && (!section || section.open)) {field.focus({preventScroll: true});if (selection && typeof field.setSelectionRange === 'function') field.setSelectionRange(...selection);}
    };
  }
  function workflowPhase(model) {
    const gate = model.inlineGate && model.inlineGate.kind;
    if (gate === 'clarification') return 1;
    if (gate === 'review' || gate === 'scientific_review') return 3;
    if (gate === 'run' || gate === 'scientific_execution_review') return 4;
    if (model.synthesis || text(model.status).startsWith('completed')) return 5;
    if (['preparing', 'running', 'cancel_requested', 'finalizing', 'approved'].includes(model.status)) return 4;
    if (model.status === 'planning' || model.status === 'awaiting_operator_review') return 3;
    const steps = (model.turns || []).filter(turn => (!turn.investigation_id || turn.investigation_id === model.id) && turn.role !== 'user' && turn.kind !== 'user_question' && Number.isInteger(turn.step)).map(turn => turn.step);
    return steps.length ? Math.max(1, Math.min(5, steps[steps.length - 1])) : 1;
  }
  function workflowActivity(model, phase) {
    const status = text(model.status), gate = model.inlineGate && model.inlineGate.kind;
    // Only the current question's retained action can locate scientific activity.
    // Earlier workflow steps and record counts are not evidence of live work.
    const actions = (model.turns || []).filter(turn => (!turn.investigation_id || turn.investigation_id === model.id) && ['scientific_action', 'scientific_operation'].includes(turn.kind));
    const turn = actions[actions.length - 1], content = turn && turn.content || {}, operation = content.operation || {};
    const request = operation.request || content.request || {};
    const action = text(operation.action) || text(content.action) || text(request.action);
    const actionStep = request.phase === 'synthesis' || action === 'recommend' || action === 'conclusion_review' ? 5
      : ['run_trial', 'rerun'].includes(action) ? 4 : turn && Number.isInteger(turn.step) ? Math.max(1, Math.min(5, turn.step)) : null;
    if (ACTIVE.has(status)) {
      const step = actionStep || (status === 'inspecting' || status === 'queued' ? 1 : ['planning', 'recording_review'].includes(status) ? 3 : 4);
      if (model.workerLiveness === 'unknown') return {kind: 'attention', step, label: 'Needs attention: Worker liveness unknown'};
      return {kind: 'running', step, label: label(status)};
    }
    // A retained review gate can remain in historical evidence after completion.
    if (status === 'abstained' || status === 'completed' || status.startsWith('completed_') && status !== 'completed_review_failed') return null;
    if (gate || status === 'approved' || status === 'awaiting_operator_review' || PLAN_ATTENTION.has(status) || FAILURES.has(status) || status === 'completed_review_failed') {
      const step = gate ? phase : status === 'completed_review_failed' ? 5 : status === 'approved' ? 4
        : FAILURES.has(status) ? actionStep || phase : PLAN_ATTENTION.has(status) ? actionStep || 3 : phase;
      return {kind: 'attention', step, label: 'Needs attention: ' + (status === 'approved' ? 'Ready to run' : label(status))};
    }
    return null;
  }
  function scientificWorkflow(container, key, options) {
    let state = workflowStates.get(container);
    if (!state || state.key !== key) state = {key, expanded: new Set(), parents: new Set(), nodes: new Map(), parentNodes: new Map(), phase: null, gate: null};
    else {
      for (const [id, node] of state.nodes) {if (node.open) state.expanded.add(id);else state.expanded.delete(id);}
      for (const [id, node] of state.parentNodes) {if (node.open) state.parents.add(id);else state.parents.delete(id);}
    }
    state.nodes = new Map();state.parentNodes = new Map();workflowStates.set(container, state);
    const owner = options.owner, phase = options.phase || 1, gate = options.gate || '';
    if (state.phase !== phase || gate && state.gate !== gate) state.expanded.add(owner + ':' + phase);
    state.phase = phase;state.gate = gate;
    const groups = new Map();
    function group(id) {
      if (groups.has(id)) return groups.get(id);
      const current = id === owner, title = text(options.questions && options.questions.get(id)) || (current ? 'Analysis workflow' : 'Earlier context');
      const panel = element('section', 'wc-workflow-card');panel.setAttribute('data-workflow-id', id);panel.setAttribute('aria-label', current ? 'Scientific workflow' : 'Earlier scientific workflow');
      const header = element('header', 'wc-workflow-header');header.append(element('h3', 'wc-workflow-title', 'Analysis overview'));header.setAttribute('title', title);panel.append(header);
      const sections = [], counts = [], badges = [];
      SCIENTIFIC_SECTIONS.forEach(([caption, question], index) => {
        const number = index + 1, identity = id + ':' + number, details = element('details', 'wc-workflow-section' + (current && number === phase ? ' is-current' : ''));
        const activity = current && options.activity && options.activity.step === number ? options.activity : null;
        if (activity) {details.className += ' is-' + activity.kind;details.setAttribute('data-workflow-state', activity.kind);}
        details.setAttribute('data-scientific-step', String(number));details.open = state.expanded.has(identity);
        const summary = element('summary', 'wc-workflow-tab');summary.setAttribute('title', question + (activity ? ' · ' + activity.label : ''));summary.setAttribute('aria-label', caption + ': ' + question + (activity ? '; ' + activity.label : ''));
        const marker = element('span', 'wc-workflow-marker');marker.setAttribute('aria-hidden', 'true');
        const label = element('h4', 'wc-workflow-label', caption), fullQuestion = element('span', 'wc-visually-hidden', question), badge = element('span', 'wc-workflow-count'), arrow = chevron();
        summary.append(marker, label, fullQuestion, badge, arrow);details.append(summary);
        const body = element('div', 'wc-workflow-body');details.append(body);panel.append(details);
        details.addEventListener('toggle', () => {if (details.open) state.expanded.add(identity);else state.expanded.delete(identity);});
        state.nodes.set(identity, details);sections.push(body);counts.push(0);badges.push(badge);
      });
      if (current) container.append(panel);
      else {
        const parent = element('details', 'wc-parent-workflow');parent.open = state.parents.has(id);parent.setAttribute('data-parent-investigation', id);
        const summary = element('summary', 'wc-parent-workflow-summary');summary.setAttribute('title', title);summary.setAttribute('aria-label', 'Earlier question: ' + title);
        summary.append(chevron(), element('span', 'wc-parent-workflow-label', 'Earlier question'), element('span', 'wc-parent-question', title));parent.append(summary, panel);
        parent.addEventListener('toggle', () => {if (parent.open) state.parents.add(id);else state.parents.delete(id);});
        state.parentNodes.set(id, parent);container.append(parent);
      }
      const value = {sections, counts, badges};groups.set(id, value);return value;
    }
    return {
      append(id, number, node) {const item = group(id), index = Math.max(1, Math.min(5, number || 3)) - 1;item.sections[index].append(node);item.counts[index]++;},
      finish() {
        group(owner);
        for (const item of groups.values()) item.sections.forEach((body, index) => {
          const count = item.counts[index];item.badges[index].textContent = count ? count + (count === 1 ? ' record' : ' records') : '';
          if (!count) body.append(element('p', 'wc-workflow-empty', 'No records yet.'));
        });
      }
    };
  }
  const keyLabel = value => String(value).replaceAll('_', ' ').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/^./, c => c.toUpperCase());
  function chevron() {
    if (typeof document.createElementNS !== 'function') return element('span', 'wc-chevron-fallback', '›');
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'wc-chevron');
    svg.setAttribute('viewBox', '0 0 16 16');svg.setAttribute('width', '16');svg.setAttribute('height', '16');
    svg.setAttribute('aria-hidden', 'true');svg.setAttribute('focusable', 'false');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', 'm6 3 5 5-5 5');path.setAttribute('fill', 'none');
    path.setAttribute('stroke', 'currentColor');path.setAttribute('stroke-width', '1.5');
    path.setAttribute('stroke-linecap', 'round');path.setAttribute('stroke-linejoin', 'round');svg.append(path);
    return svg;
  }
  function disclosureSummary(title, count) {
    const summary = element('summary', 'wc-disclosure-summary');
    summary.append(chevron(), element('span', 'wc-disclosure-title', title));
    if (count) summary.append(element('span', 'wc-status-pill wc-pill-neutral', count));
    return summary;
  }
  function structuredValue(value, depth, budget) {
    if (value === null || value === undefined) return element('span', 'wc-value-empty', 'Not recorded');
    if (typeof value !== 'object') return element('span', 'wc-value', typeof value === 'boolean' ? value ? 'Yes' : 'No' : String(value));
    if (depth >= 5 || budget.rows <= 0) return element('p', 'wc-truncated', 'Additional fields are available in JSON.');
    const entries = Object.entries(value), grid = element('dl', 'wc-kv-grid' + (depth ? ' wc-kv-nested wc-kv-stacked' : ''));
    if (!entries.length) return element('span', 'wc-value-empty', Array.isArray(value) ? 'No entries' : 'No fields recorded');
    for (const [key, item] of entries.slice(0, 30)) {
      if (budget.rows <= 0) break;
      budget.rows--;
      const complex = item !== null && typeof item === 'object';
      const term = element('dt', complex ? 'wc-kv-section-label' : '', Array.isArray(value) ? 'Item ' + (Number(key) + 1) : keyLabel(key));
      const definition = element('dd', complex ? 'wc-kv-section-value' : '');definition.append(structuredValue(item, depth + 1, budget));grid.append(term, definition);
    }
    if (entries.length > 30 || budget.rows <= 0) grid.append(element('dt', 'wc-truncated', 'Additional fields'), element('dd', 'wc-truncated', 'Available in JSON'));
    return grid;
  }
  function createDisclosure(title, value) {
    const details = element('details', 'wc-disclosure');
    details.append(disclosureSummary(title));
    const body = element('div', 'wc-disclosure-body');body.setAttribute('tabindex', '0');body.setAttribute('role', 'region');body.setAttribute('aria-label', title + ' details');body.append(structuredValue(value, 0, {rows: 120}));
    const tools = element('div', 'wc-record-tools'), copy = element('button', 'secondary wc-copy-json', 'Copy JSON');
    copy.type = 'button';copy.setAttribute('aria-label', 'Copy ' + title.toLowerCase() + ' as JSON');
    const message = element('span', 'wc-copy-status');message.setAttribute('role', 'status');
    const raw = element('details', 'wc-raw-inspection');raw.append(element('summary', '', 'View JSON'));
    const rawText = element('textarea', 'wc-raw-json');rawText.readOnly = true;
    rawText.value = JSON.stringify(value, null, 2);rawText.setAttribute('aria-label', title + ' JSON');raw.append(rawText);
    function selectRaw() {
      raw.open = true;rawText.focus();rawText.select();
      message.textContent = 'Select and copy the JSON below.';
    }
    copy.addEventListener('click', () => {
      const clipboard = root.navigator && root.navigator.clipboard;
      if (!clipboard || typeof clipboard.writeText !== 'function') {selectRaw();return;}
      try {Promise.resolve(clipboard.writeText(rawText.value)).then(() => {message.textContent = 'JSON copied.';}, selectRaw);}
      catch (_) {selectRaw();}
    });
    tools.append(copy, message);body.append(tools, raw);details.append(body);return details;
  }
  function cardHeader(title, pill, tone) {
    const header = element('header', 'wc-card-header');header.append(element('h3', 'wc-card-heading', title));
    if (pill) header.append(element('span', 'wc-status-pill wc-pill-' + (tone || 'neutral'), pill));
    return header;
  }
  function animateCards(cards, changedRun) {
    if (!changedRun || typeof root.matchMedia === 'function' && root.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    for (const card of cards) if (typeof card.animate === 'function') card.animate([
      {opacity: 0, transform: 'translateY(4px)'}, {opacity: 1, transform: 'translateY(0)'}
    ], {duration: 180, easing: 'cubic-bezier(0.22, 1, 0.36, 1)'});
  }
  function actionButton(action, onAction, className) {
    const button = element('button', className || 'secondary wc-action', action.label);
    button.type = 'button';
    button.addEventListener('click', () => {
      // Navigation and question drafting are the only supported callbacks.
      if (typeof onAction !== 'function') return;
      if (action.type === 'navigate') onAction({type: action.type, view: action.view});
      if (action.type === 'draft') onAction({type: action.type, question: action.question, sourceRunId: action.sourceRunId, ...(Array.isArray(action.sources) ? {sources: action.sources.slice()} : {input: action.input})});
    });
    return button;
  }
  function renderConversationWorkspace(container, options) {
    options = options || {};
    const model = buildModel(options.state, options.config), runId = options.state && options.state.id;
    const changedRun = Boolean(runId && renderedRuns.get(container) !== runId), cards = [];
    const restoreView = preserveConversationView(container, !changedRun);
    if (runId) renderedRuns.set(container, runId);else if (!options.loading) renderedRuns.set(container, null);
    container.replaceChildren();
    if (!options.state) {
      if (options.loading) {
        const status = element('p', 'wc-loading', 'Loading analysis…');
        status.setAttribute('role', 'status');container.append(status);return model;
      }
      const welcome = element('section', 'wc-welcome');
      welcome.append(element('h3', '', 'What would you like to investigate?'));
      welcome.append(element('p', '', 'Choose project data and ask a question.'));
      container.append(welcome);return model;
    }
    const context = element('div', 'wc-context');
    if (model.inputCaption) {
      const input = element('span', 'wc-input', model.inputCaption);input.setAttribute('title', model.input.path || 'Bound input');context.append(input);
    }
    container.append(context);
    const owner = text(runId) || 'legacy', activity = workflowActivity(model, workflowPhase(model)), phase = activity ? activity.step : workflowPhase(model), flow = scientificWorkflow(container, 'legacy:' + owner, {owner, questions: new Map([[owner, text(options.state.question)]]), phase, activity, gate: model.primaryAction ? model.primaryAction.view + ':' + model.status : ''});
    if (model.input) {
      const input = element('article', 'wc-card wc-recorded-input');input.append(cardHeader('Recorded input'), element('p', '', model.inputCaption), createDisclosure('Input reference', model.input));flow.append(owner, 1, input);cards.push(input);
    }
    for (const item of model.records) {
      const article = element('article', 'wc-card analyst-message wc-message wc-' + item.kind + (item.kind === 'user' ? ' user-message' : ' record-message'));
      const title = item.kind === 'user' ? 'Your question' : item.kind === 'operator' ? 'Operator review' : 'Analysis plan';
      const decision = item.kind === 'operator' && options.state.review && options.state.review.decision;
      const pill = item.kind === 'user' ? '' : item.kind === 'operator' ? ({approve: 'Approved', reject: 'Rejected', needs_information: 'Needs information'}[decision] || item.title) : item.kind === 'plan' ? 'Ready' : item.title;
      const tone = decision === 'approve' || item.kind === 'plan' ? 'good' : decision === 'reject' ? 'bad' : 'neutral';
      article.append(cardHeader(title, pill, tone), element('p', 'wc-message-body', item.body));
      if (item.details) article.append(createDisclosure(item.kind === 'planner' ? 'Decision and evidence' : item.kind === 'operator' ? 'Review details' : 'Input and settings', item.details));
      flow.append(owner, item.kind === 'user' ? 2 : 3, article);cards.push(article);
    }
    if (model.events.length) {
      const execution = element('details', 'wc-disclosure wc-execution');execution.open = ACTIVE.has(model.status) || FAILURES.has(model.status);
      execution.append(disclosureSummary('Recorded activity', model.events.length + ' steps'));
      const body = element('div', 'wc-disclosure-body'), list = element('ol', 'wc-timeline');
      for (const event of model.events) {
        const row = element('li', 'wc-step' + (event.current ? ' is-current' : ''));row.setAttribute('data-status', event.status);
        const marker = element('span', 'wc-step-marker');marker.setAttribute('aria-hidden', 'true');
        const content = element('div', 'wc-step-content');content.append(element('span', 'wc-step-title', event.title));
        if (event.at) {
          const timestamp = element('time', 'wc-step-time', event.at);timestamp.setAttribute('datetime', event.at);content.append(timestamp);
        }
        row.append(marker, content);list.append(row);
      }
      body.append(list);
      if (model.attempts.length) body.append(createDisclosure('Execution attempts', model.attempts));
      execution.append(body);flow.append(owner, 4, execution);
    }
    const summary = element('article', 'wc-card wc-summary wc-tone-' + model.summary.tone);summary.setAttribute('role', 'status');
    summary.append(cardHeader(model.summary.title), element('p', '', model.summary.body));
    if (model.primaryAction) summary.append(actionButton(model.primaryAction, options.onAction, 'record-action wc-primary-action'));
    flow.append(owner, phase, summary);cards.push(summary);
    if (model.failureRecord) flow.append(owner, 4, createDisclosure('Failure evidence', model.failureRecord));
    if (options.revision) flow.append(owner, 4, createDisclosure('Presentation record', {
      id: options.revision.id, status: options.revision.status, error: options.revision.error || null,
      scope: 'Display changes only; the scientific analysis remains fixed.'
    }));
    if (model.suggestions.length) {
      const suggestions = element('section', 'wc-followups');suggestions.append(element('h3', '', 'Continue the chat'));
      const actions = element('div', 'wc-suggestions');
      for (const suggestion of model.suggestions) actions.append(actionButton({...suggestion.action, label: suggestion.caption}, options.onAction, 'secondary wc-suggestion'));
      suggestions.append(actions);flow.append(owner, 5, suggestions);
    }
    flow.append(owner, 3, createDisclosure('Planning source', {mode: model.mode, scope: model.scope}));flow.finish();
    restoreView();animateCards(cards, changedRun);return model;
  }
  function planSummary(state) {
    const proposal = state && state.proposal || {}, configuration = proposal.configuration || {};
    const preview = proposal.preview || {}, parameters = configuration.parameters || {};
    return [
      {label: 'Input preview', value: Number.isFinite(preview.cells) && Number.isFinite(preview.genes) ? preview.cells.toLocaleString('en-US') + ' cells × ' + preview.genes.toLocaleString('en-US') + ' genes' : 'Not recorded'},
      {label: 'Workflow', value: parameters.workflow === 'scrna_qc_integration_annotation/1' ? 'QC, integration, clustering and annotation' : text(parameters.workflow) || 'Not recorded'},
      {label: 'Integration', value: parameters.correction === 'none' ? 'No correction' : text(parameters.correction) || 'Not recorded'},
      {label: 'Resource limits', value: Number.isFinite(configuration.run_timeout_seconds) && Number.isFinite(parameters.memory_mb) ? configuration.run_timeout_seconds.toLocaleString('en-US') + ' s · ' + parameters.memory_mb.toLocaleString('en-US') + ' MiB / worker' : 'Not recorded'}
    ];
  }
  function buildInvestigationModel(investigation, config, jobState) {
    if (!investigation) return {turns: [], profile: null, inlineGate: null, status: '', artifacts: [], synthesis: null};
    const state = jobState || investigation.job_state || {}, status = text(state.status) || text(investigation.status);
    const fields = Array.isArray(investigation.clarification_fields) ? investigation.clarification_fields : [];
    const authorState = {...state};
    if (!authorState.plan_author && investigation.proposal_mode === 'operator') authorState.plan_author = 'operator_created_recorded_plan';
    if (!authorState.plan_author && investigation.proposal_mode === 'agent' && config && config.proposal_selection && config.proposal_selection.mode === 'recorded_fixture') authorState.plan_author = 'recorded_fixture';
    let mode = modeFor(authorState, config || {}), scope = buildModel(authorState, config || {}).scope;
    if (investigation.scientific_session_id && !investigation.job_id) {
      const backend = text(investigation.planning_source && investigation.planning_source.backend);
      mode = backend === 'recorded_fixture' ? 'Offline scientific planning' : backend === 'codex-cli' ? 'Scientific planner' : 'Scientific planning';
      scope = backend === 'recorded_fixture' ? 'Scientific planning used recorded offline decisions; no live model conversation.' : backend === 'codex-cli' ? 'Scientific planner attribution is recorded at dispatch. Consult the retained workflow evidence for actual model activity.' : 'Scientific planning. Planner attribution was not retained for this historical record.';
    }
    let gate = null;
    if (fields.length && ['awaiting_information', 'needs_information'].includes(status)) gate = 'clarification';
    else if (investigation.job_id && (status === 'awaiting_operator_review' || status === 'needs_information' && state.proposal && !state.review_receipt)) gate = 'review';
    else if (investigation.job_id && status === 'approved') gate = 'run';
    else if (!investigation.job_id && investigation.scientific_session_id && ['review', 'execution_review'].includes(investigation.gate)) gate = 'scientific_' + investigation.gate;
    return {
      id: investigation.id, revision: investigation.revision, status, workerLiveness: text(state.worker_liveness) || text(investigation.worker_liveness),
      turns: (Array.isArray(investigation.thread_turns) ? investigation.thread_turns : Array.isArray(investigation.turns) ? investigation.turns : []).filter(turn => turn && typeof turn === 'object'),
      profile: investigation.profile || null, declarations: investigation.declarations || {}, fields,
      sources: Array.isArray(investigation.sources) && investigation.sources.length ? investigation.sources.slice() : investigation.source ? [investigation.source] : [],
      sourceProfiles: Array.isArray(investigation.source_profiles) ? investigation.source_profiles : [],
      sourceDeclarations: Array.isArray(investigation.source_declarations) ? investigation.source_declarations : [],
      scientificContext: investigation.scientific_context || {},
      scientificSessionId: investigation.scientific_session_id || null, scientificGateName: investigation.gate || null,
      scientificGate: investigation.gate_options || null, scientificPlan: investigation.scientific_plan || null, planningSource: investigation.planning_source || null,
      capabilities: {modelExecution: Boolean(config && ((!config.scientific || config.scientific.enabled === false) ? config.model_access === true : config.scientific.model_execution_allowed === true)), autoAdvance: Boolean(config && config.scientific && config.scientific.enabled !== false && config.scientific.scientific_workflow_configured === true), offlinePlanning: Boolean(config && config.scientific && config.scientific.planning_mode === 'recorded_fixture')},
      input: investigation.source, selection: investigation.selection || null,
      jobState: state, metrics: planSummary(state),
      inlineGate: {kind: gate, investigationId: investigation.id, revision: investigation.revision, jobId: investigation.job_id || null},
      artifacts: Array.isArray(investigation.artifacts) ? investigation.artifacts : [],
      synthesis: investigation.synthesis || null, parentJob: investigation.parent_job || null,
      mode, scope
    };
  }
  const clarificationDrafts = new WeakMap();
  function renderClarification(container, model, onAction) {
    const scientificFields = model.fields.some(field => field && field.scope === 'scientific_context');
    const article = element('article', 'wc-card wc-clarification');article.append(cardHeader(scientificFields ? 'Scientific planning context' : 'A little more context', 'Needs information', 'warning'));
    article.append(element('p', '', scientificFields ? 'Classify the attached data from its source records, then choose which planning actions to authorize for this chat.' : model.sources.length > 1 ? 'Confirm the role and experimental details of each attached dataset separately.' : 'Confirm the experimental details needed to prepare a plan. Suggested columns come from this dataset.'));
    const form = element('form', 'wc-clarification-form'), controls = [], contextControls = [], sourceControls = [];
    const reasons = new Map(), sharedReasons = new Map();
    for (const field of model.fields) {
      const reason = field && ['select', 'text', 'boolean'].includes(field.type) ? text(field.reason).trim() : '';
      if (reason) reasons.set(reason, (reasons.get(reason) || 0) + 1);
    }
    for (const [reason, count] of reasons) if (count > 1) sharedReasons.set(reason, 'clarify-' + String(model.id).replace(/[^a-zA-Z0-9_-]/g, '') + '-shared-note-' + sharedReasons.size);
    let draft = clarificationDrafts.get(container), updatePermissions = () => {};
    if (!draft || draft.id !== model.id) {draft = {id: model.id, values: {}};clarificationDrafts.set(container, draft);}
    for (const field of model.fields) {
      if (!field || typeof field.field !== 'string' || !['select', 'text', 'boolean'].includes(field.type)) continue;
      const scientific = field.scope === 'scientific_context', perSource = field.scope === 'source_declarations';
      if (perSource && (!Number.isInteger(field.source_index) || field.source_index < 0 || field.source_index >= model.sources.length)) continue;
      const scope = scientific ? 'scientific_context' : perSource ? 'source_declarations' : 'declarations';
      const key = field.field, prefix = perSource ? field.source_index + ':' : '', draftKey = scope + ':' + prefix + key, group = element('div', 'wc-clarification-field'), id = 'clarify-' + String(model.id).replace(/[^a-zA-Z0-9_-]/g, '') + '-' + (perSource ? 'source-' + field.source_index + '-' : '') + key.replace(/[^a-zA-Z0-9_-]/g, '');
      const caption = element('label', '', text(field.label) || keyLabel(key));caption.setAttribute('for', id);
      let input;
      if (field.type === 'select') {
        input = element('select');
        const empty = element('option', '', 'Choose an option');empty.value = '';input.append(empty);
        for (const option of Array.isArray(field.options) ? field.options : []) {
          if (!option || typeof option.value !== 'string') continue;
          const node = element('option', '', text(option.label) || option.value);node.value = option.value;input.append(node);
        }
      } else {input = element(field.type === 'boolean' ? 'input' : 'textarea');if (field.type === 'boolean') input.type = 'checkbox';}
      input.setAttribute('id', id);input.setAttribute('name', key);input.setAttribute('data-clarification-scope', scope);input.required = field.required === true;
      if (perSource) input.setAttribute('data-source-index', String(field.source_index));
      input.disabled = field.disabled === true;
      const retained = scientific ? model.scientificContext : perSource ? model.sourceDeclarations[field.source_index] || {} : model.declarations;
      const initial = Object.prototype.hasOwnProperty.call(draft.values, draftKey) ? draft.values[draftKey] : retained[key];
      if (field.type === 'boolean') input.checked = initial === true;else input.value = typeof initial === 'string' ? initial : '';
      const change = () => {draft.values[draftKey] = field.type === 'boolean' ? input.checked : input.value;updatePermissions();};
      input.addEventListener('change', change);input.addEventListener('input', change);
      if (field.type === 'boolean') {caption.className = 'check';caption.append(input);group.append(caption);}else group.append(caption, input);
      const reasonText = text(field.reason).trim();
      if (sharedReasons.has(reasonText)) input.setAttribute('aria-describedby', sharedReasons.get(reasonText));
      else if (reasonText) {const reason = element('p', 'wc-field-reason', reasonText);reason.setAttribute('id', id + '-reason');input.setAttribute('aria-describedby', id + '-reason');group.append(reason);}
      const control = {field, input, draftKey};
      if (scientific) contextControls.push(control);else if (perSource) sourceControls.push(control);else controls.push(control);
      form.append(group);
    }
    for (const [reasonText, id] of sharedReasons) {const note = element('p', 'wc-field-reason wc-shared-field-reason', reasonText);note.setAttribute('id', id);form.append(note);}
    const contextControl = field => contextControls.find(control => control.field.field === field);
    const classification = contextControl('data_classification'), grant = contextControl('allow_model_execution'), advance = contextControl('auto_advance'), synthesisGrant = contextControl('auto_synthesis');
    const availability = element('p', 'wc-field-availability');availability.setAttribute('role', 'status');
    const classificationValue = () => classification ? classification.input.value : model.scientificContext.data_classification;
    const offlineAdvanceAllowed = () => model.capabilities.offlinePlanning && classificationValue() === 'synthetic';
    function classifiedForModels() {
      const value = classificationValue();
      return value === 'public' || value === 'synthetic';
    }
    updatePermissions = () => {
      const modelAllowed = model.capabilities.modelExecution && classifiedForModels();
      if (grant) {
        grant.input.disabled = grant.field.disabled === true || !modelAllowed;
        if (grant.input.disabled) {grant.input.checked = false;draft.values[grant.draftKey] = false;}
      }
      const explicitlyGranted = grant ? !grant.input.disabled && grant.input.checked : modelAllowed && model.scientificContext.allow_model_execution === true;
      for (const permission of [advance, synthesisGrant].filter(Boolean)) {
        permission.input.disabled = permission.field.disabled === true || !model.capabilities.autoAdvance || !(explicitlyGranted || offlineAdvanceAllowed());
        if (permission.input.disabled && !(model.sources.length > 1 && permission === advance)) {permission.input.checked = false;draft.values[permission.draftKey] = false;}
      }
      availability.textContent = offlineAdvanceAllowed() && model.capabilities.autoAdvance ? 'Recorded offline planning only; no model calls. Automatic planning stops at required review gates.' : !model.capabilities.modelExecution ? 'Model calls are disabled for this service.' : !classifiedForModels() ? 'Model calls require an explicitly classified public or synthetic dataset.' : advance && !model.capabilities.autoAdvance ? 'Automatic planning is unavailable until a scientific workflow is configured.' : synthesisGrant ? 'Planning and post-execution synthesis permissions are separate. Analysis execution still requires its own review and authorization.' : 'These permissions apply to planning in this chat. Analysis execution still requires its own review and authorization.';
    };
    if (contextControls.length) {updatePermissions();form.append(availability);}
    const submit = element('button', 'wc-clarify-submit', scientificFields ? 'Confirm planning context' : 'Confirm details and prepare plan');submit.type = 'submit';form.append(submit);
    form.addEventListener('submit', event => {
      event.preventDefault();updatePermissions();
      if (typeof form.reportValidity === 'function' && !form.reportValidity()) return;
      const action = {type: 'clarify', investigationId: model.id, expectedRevision: model.revision};
      if (controls.length || model.sources.length <= 1) {
        const declarations = {...model.declarations};
        for (const {field, input} of controls) declarations[field.field] = field.type === 'boolean' ? input.checked === true : input.value;
        action.declarations = declarations;
      }
      if (model.sources.length > 1 || sourceControls.length) {
        const declarations = model.sources.map(() => ({}));
        for (const {field, input} of sourceControls) if (!input.disabled) declarations[field.source_index][field.field] = field.type === 'boolean' ? input.checked === true : input.value;
        action.source_declarations = declarations;
      }
      if (contextControls.length) {
        const context = {...model.scientificContext};
        for (const {field, input} of contextControls) context[field.field] = field.type === 'boolean' ? !input.disabled && input.checked === true : input.value;
        context.allow_model_execution = model.capabilities.modelExecution && ['public', 'synthetic'].includes(context.data_classification) && context.allow_model_execution === true;
        // Requested input-set planning is not a model-sharing or execution grant.
        const requestedAdvance = advance ? advance.input.checked === true : model.scientificContext.auto_advance === true;
        context.auto_advance = model.sources.length > 1 ? requestedAdvance : model.capabilities.autoAdvance && (context.allow_model_execution || model.capabilities.offlinePlanning && context.data_classification === 'synthetic') && context.auto_advance === true;
        if (synthesisGrant || Object.prototype.hasOwnProperty.call(context, 'auto_synthesis')) context.auto_synthesis = model.capabilities.autoAdvance && (context.allow_model_execution || model.capabilities.offlinePlanning && context.data_classification === 'synthetic') && context.auto_synthesis === true;
        action.scientificContext = context;
      }
      if (typeof onAction === 'function') onAction(action);
    });
    article.append(form);return article;
  }
  const scientificRequestKeys = new WeakMap();
  function renderScientificGate(container, model, onAction) {
    const options = model.scientificGate || {}, execution = model.scientificGateName === 'execution_review';
    const card = element('article', 'wc-card wc-scientific-gate');card.setAttribute('data-inline-gate', model.inlineGate.kind);
    card.append(cardHeader(execution ? 'Review trial execution' : 'Review the scientific plan', 'Operator decision', 'warning'));
    const projectionMatches = options.gate === model.scientificGateName && options.session_id === model.scientificSessionId && Number.isInteger(options.workspace_revision);
    const canSubmit = projectionMatches && options.can_submit === true;
    const startupExecution = options.startup_data_execution_allowed === true;
    const choices = Array.isArray(execution ? options.declared_trials : options.proposals) ? execution ? options.declared_trials : options.proposals : [];
    const eligible = choice => execution ? choice && choice.status === 'declared' && choice.eligible !== false : choice && choice.eligible === true;
    const identity = choice => text(execution ? choice.experiment_id : choice.draft_id);
    const blocked = Array.isArray(options.blocked_reasons) ? options.blocked_reasons : [];
    if (!projectionMatches) card.append(element('p', 'wc-gate-blocked', 'Review details are not yet available. Refresh the chat evidence.'));
    for (const reason of blocked) card.append(element('p', 'wc-gate-blocked', text(reason) || text(reason && reason.reason) || text(reason && reason.message)));
    if (execution && !startupExecution) card.append(element('p', 'wc-gate-blocked', 'Data execution is disabled for this service.'));
    if (model.scientificPlan) card.append(createDisclosure('Scientific plan', model.scientificPlan));
    if (options.review_evidence) card.append(createDisclosure('Recorded scientific review', options.review_evidence));
    const form = element('form', 'wc-scientific-gate-form');
    const formId = 'scientific-gate-' + String(model.id).replace(/[^a-zA-Z0-9_-]/g, '');
    const selectLabel = element('label', '', execution ? 'Declared trial' : 'Frozen trial proposal');selectLabel.setAttribute('for', formId + '-trial');
    const select = element('select');select.setAttribute('id', formId + '-trial');select.setAttribute('name', 'trial');select.required = true;
    const empty = element('option', '', execution ? 'Choose a declared trial' : 'Choose a reviewed proposal');empty.value = '';select.append(empty);
    for (const choice of choices) {
      if (!choice || !identity(choice)) continue;
      const option = element('option', '', text(choice.label) || text(choice.spec && choice.spec.name) || identity(choice));
      option.value = identity(choice);option.disabled = !eligible(choice);select.append(option);
    }
    const preview = element('div', 'wc-trial-preview');
    select.addEventListener('change', () => {
      preview.replaceChildren();const choice = choices.find(row => identity(row) === select.value);
      if (!choice) return;
      preview.append(createDisclosure('Frozen trial specification and resource limits', choice.spec || {}));
      if (choice.diagnostics) preview.append(createDisclosure('Trial design diagnostics', choice.diagnostics));
      for (const reason of Array.isArray(choice.blocked_reasons) ? choice.blocked_reasons : []) preview.append(element('p', 'wc-gate-blocked', text(reason)));
    });
    form.append(selectLabel, select, preview);
    const reviewerLabel = element('label', '', 'Operator label');reviewerLabel.setAttribute('for', formId + '-reviewer');
    const reviewer = element('input');reviewer.setAttribute('id', formId + '-reviewer');reviewer.setAttribute('name', 'reviewer');reviewer.required = true;reviewer.maxLength = 200;
    const rationaleLabel = element('label', '', 'Review rationale');rationaleLabel.setAttribute('for', formId + '-rationale');
    const rationale = element('textarea');rationale.setAttribute('id', formId + '-rationale');rationale.setAttribute('name', 'rationale');rationale.required = true;rationale.maxLength = 4000;
    form.append(reviewerLabel, reviewer, rationaleLabel, rationale);
    const confirmations = [];
    for (const requirement of Array.isArray(options.required_confirmations) ? options.required_confirmations : []) {
      const key = typeof requirement === 'string' ? requirement : text(requirement && requirement.key);
      if (!key) continue;
      const labelText = typeof requirement === 'string' ? keyLabel(requirement) : text(requirement.label) || keyLabel(key);
      const caption = element('label', 'check'), input = element('input');input.type = 'checkbox';input.required = true;input.setAttribute('name', 'confirm-' + key);
      caption.append(input, element('span', '', labelText));form.append(caption);confirmations.push({key, input});
    }
    let dataGrant = null;
    if (execution) {
      const caption = element('label', 'check');dataGrant = element('input');dataGrant.type = 'checkbox';dataGrant.required = true;
      dataGrant.setAttribute('name', 'allow_data_execution');dataGrant.disabled = !startupExecution;
      caption.append(dataGrant, element('span', '', 'I authorize data execution for this declared trial within its recorded limits.'));form.append(caption);
    }
    const status = element('p', 'wc-gate-action-message');status.setAttribute('role', 'status');
    const submit = element('button', 'wc-scientific-submit', execution ? 'Run declared trial' : 'Approve and declare trial');submit.type = 'submit';submit.disabled = !canSubmit || execution && !startupExecution;
    form.append(submit, status);
    form.addEventListener('submit', event => {
      event.preventDefault();
      if (!canSubmit || execution && !startupExecution) {status.textContent = 'This gate is blocked. Review the recorded reasons before continuing.';return;}
      const choice = choices.find(row => identity(row) === select.value);
      if (!choice || !eligible(choice) || !choice.spec || typeof choice.spec !== 'object') {status.textContent = 'Choose an eligible recorded trial.';return;}
      if (!text(reviewer.value).trim() || !text(rationale.value).trim() || confirmations.some(row => row.input.checked !== true)) {status.textContent = 'Provide an operator label, rationale and every required confirmation.';return;}
      if (execution && dataGrant.checked !== true) {status.textContent = 'Explicit authorization is required before data execution.';return;}
      if (typeof form.reportValidity === 'function' && !form.reportValidity()) return;
      const request = {action: execution ? 'run_trial' : 'declare_trial', session_id: model.scientificSessionId, expected_revision: options.workspace_revision, actor: reviewer.value.trim()};
      if (execution) {
        request.experiment_id = choice.experiment_id;request.allow_data_execution = true;
        let keys = scientificRequestKeys.get(container);if (!keys) {keys = new Map();scientificRequestKeys.set(container, keys);}
        const key = model.id + ':' + options.workspace_revision + ':' + choice.experiment_id;
        if (!keys.has(key)) {
          if (!root.crypto || typeof root.crypto.randomUUID !== 'function') {status.textContent = 'A request identity could not be created. Refresh in a supported browser.';return;}
          keys.set(key, root.crypto.randomUUID());
        }
        request.idempotency_key = keys.get(key);
      } else request.spec = JSON.parse(JSON.stringify(choice.spec));
      const operatorReview = {reviewer: reviewer.value.trim(), rationale: rationale.value.trim(), confirmations: {}};
      for (const row of confirmations) operatorReview.confirmations[row.key] = true;
      if (typeof onAction === 'function') onAction({type: 'scientific', request, operatorReview});
    });
    card.append(form);return card;
  }
  function auditCard(profile) {
    const card = element('article', 'wc-card wc-data-audit');card.append(cardHeader('Data audit'));
    const observed = profile && profile.observed || {};
    if (observed.file_type === 'input_set' && Array.isArray(observed.sources)) {
      card.append(element('p', 'wc-audit-summary', observed.sources.length + ' attached datasets · inspected separately'));
      const list = element('ol', 'wc-source-audits');
      for (const [index, source] of observed.sources.slice(0, 8).entries()) {
        const item = element('li', 'wc-source-audit'), facts = source.observed || {}, name = text(source.filename) || text(source.source).split('/').pop() || 'Dataset ' + (index + 1);
        item.append(element('h4', '', name));
        const summary = Number.isFinite(facts.cells) && Number.isFinite(facts.genes) ? facts.cells.toLocaleString('en-US') + ' cells × ' + facts.genes.toLocaleString('en-US') + ' genes' : text(facts.file_type) ? keyLabel(facts.file_type) + ' file' : 'File facts available';
        item.append(element('p', 'wc-audit-summary', summary), createDisclosure('File evidence', source));list.append(item);
      }
      card.append(list, createDisclosure('Cross-dataset unknowns', profile.unknowns || []));
      if (observed.sources.length > 8) card.append(createDisclosure('Full input-set evidence', profile));
      return card;
    }
    const input = text(profile && profile.filename) || text(profile && profile.source) || text(profile && profile.input && profile.input.source);
    const summary = [input ? input.split('/').pop() : '', Number.isFinite(observed.cells) && Number.isFinite(observed.genes) ? observed.cells.toLocaleString('en-US') + ' cells × ' + observed.genes.toLocaleString('en-US') + ' genes' : ''].filter(Boolean).join(' · ');
    if (summary) card.append(element('p', 'wc-audit-summary', summary));
    const sections = element('div', 'wc-audit-grid');
    for (const [title, content] of [
      ['Observed', profile && profile.observed], ['Supplied declarations', profile && profile.declarations],
      ['Inferred', profile && profile.inferred], ['Unknowns', profile && profile.unknowns]
    ]) sections.append(createDisclosure(title, content === undefined ? null : content));
    card.append(sections);return card;
  }
  function metricGrid(metrics) {
    const grid = element('dl', 'wc-plan-metrics');
    for (const metric of metrics) {const item = element('div', 'wc-plan-metric');item.append(element('dt', '', metric.label), element('dd', '', metric.value));grid.append(item);}
    return grid;
  }
  function appendSynthesisSections(card, synthesis) {
    const findings = synthesis.supported_findings, section = element('section', 'wc-synthesis-section');
    section.append(element('h4', '', 'Supported findings'));
    const isRecord = findings && typeof findings === 'object' && !Array.isArray(findings);
    const overview = isRecord ? Object.fromEntries(Object.entries(findings).filter(([key]) => key !== 'governed_outputs' && key !== 'trial_results')) : findings;
    if (!isRecord || Object.keys(overview).length) section.append(structuredValue(overview === undefined ? null : overview, 0, {rows: 32}));
    const trials = isRecord && Array.isArray(findings.trial_results) ? findings.trial_results : [];
    for (const trial of trials.slice(0, 6)) {
      const result = element('article', 'wc-trial-result');
      const heading = element('div', 'wc-card-header');heading.append(element('h4', '', text(trial.candidate) || text(trial.experiment_id) || 'Recorded trial'));
      if (trial.status) heading.append(element('span', 'wc-status-pill wc-pill-neutral', keyLabel(trial.status)));
      result.append(heading);
      const metrics = element('div', 'wc-trial-metrics');metrics.append(structuredValue(trial.metric_values === undefined ? null : trial.metric_values, 0, {rows: 30}));result.append(metrics);
      if (Array.isArray(trial.failures) && trial.failures.length) result.append(element('p', 'wc-gate-blocked', trial.failures.length + ' recorded failure' + (trial.failures.length === 1 ? '' : 's')));
      if (Array.isArray(trial.warnings) && trial.warnings.length) result.append(element('p', 'wc-field-reason', trial.warnings.length + ' recorded warning' + (trial.warnings.length === 1 ? '' : 's')));
      result.append(createDisclosure('Trial result evidence', trial));section.append(result);
    }
    if (trials.length > 6) {section.append(element('p', 'wc-truncated', 'Showing 6 of ' + trials.length + ' recorded trial results.'), createDisclosure('All trial results', trials));}
    card.append(section);
    const assessment = synthesis.method_evidence_assessment, method = element('section', 'wc-synthesis-section');method.append(element('h4', '', 'Method and evidence assessment'));
    if (assessment && typeof assessment === 'object' && !Array.isArray(assessment)) {
      const concise = Object.fromEntries(Object.entries(assessment).filter(([key]) => key !== 'comparison_evidence'));
      if (Object.keys(concise).length) method.append(structuredValue(concise, 0, {rows: 32}));
      if (assessment.comparison_evidence !== undefined) {
        const comparison = assessment.comparison_evidence;
        if (comparison && typeof comparison === 'object' && !Array.isArray(comparison)) {
          const facts = Object.fromEntries(Object.entries(comparison).filter(([, value]) => value === null || typeof value !== 'object').slice(0, 6).map(([key, value]) => ['comparison_' + key, value]));
          if (Object.keys(facts).length) method.append(structuredValue(facts, 0, {rows: 8}));
        }
        method.append(createDisclosure('Comparison evidence', comparison));
      }
    } else method.append(structuredValue(assessment === undefined ? null : assessment, 0, {rows: 32}));
    card.append(method);
    const uncertainty = element('section', 'wc-synthesis-section');uncertainty.append(element('h4', '', 'What remains uncertain'));
    if (Array.isArray(synthesis.uncertainties)) {
      const list = element('ul', 'wc-uncertainty-list');
      for (const entry of synthesis.uncertainties.slice(0, 12)) {
        const row = element('li');
        if (typeof entry === 'string') row.textContent = entry;
        else if (entry && typeof entry === 'object') {
          const reason = text(entry.reason) || text(entry.impact) || text(entry.message);
          if (reason) row.textContent = (entry.field ? keyLabel(entry.field) + ': ' : '') + reason;
          else row.append(structuredValue(entry, 0, {rows: 12}));
        } else row.textContent = String(entry);
        list.append(row);
      }
      uncertainty.append(list);
      if (synthesis.uncertainties.length > 12) uncertainty.append(element('p', 'wc-truncated', 'Showing 12 of ' + synthesis.uncertainties.length + ' recorded uncertainties.'), createDisclosure('All remaining uncertainties', synthesis.uncertainties));
    } else uncertainty.append(structuredValue(synthesis.uncertainties === undefined ? null : synthesis.uncertainties, 0, {rows: 32}));
    card.append(uncertainty);
    const evidence = {...(synthesis.evidence && typeof synthesis.evidence === 'object' ? synthesis.evidence : {})};
    if (isRecord && findings.governed_outputs !== undefined) evidence.governed_outputs = findings.governed_outputs;
    if (Object.keys(evidence).length) card.append(createDisclosure('Workflow evidence', evidence));
  }
  function renderInvestigation(container, options) {
    options = options || {};
    const investigation = options.investigation, model = buildInvestigationModel(investigation, options.config, options.jobState);
    if (!investigation) {renderConversationWorkspace(container, {config: options.config, loading: options.loading, onAction: options.onAction});return model;}
    const runKey = 'investigation:' + investigation.id, changedRun = renderedRuns.get(container) !== runKey, cards = [];
    const restoreView = preserveConversationView(container, !changedRun);
    renderedRuns.set(container, runKey);container.replaceChildren();
    const context = element('div', 'wc-context');
    for (const source of model.sources) context.append(element('span', 'wc-input', text(source).split('/').pop()));
    container.append(context);
    const questions = new Map([[investigation.id, text(investigation.question)]]);
    for (const turn of model.turns) if (turn.kind === 'user_question' || turn.role === 'user') {
      const id = turn.investigation_id || investigation.id, question = text(turn.content && turn.content.question);
      if (question && (!questions.get(id) || id !== investigation.id)) questions.set(id, question);
    }
    const activity = workflowActivity(model, workflowPhase(model)), phase = activity ? activity.step : workflowPhase(model), gateKey = [model.inlineGate.kind || '', model.inlineGate.jobId || '', model.scientificSessionId || '', text(investigation.required_action), ...model.fields.map(field => [field.scope || '', field.source_index, field.field].join(':'))].join('|');
    const flow = scientificWorkflow(container, runKey, {owner: investigation.id, questions, phase, activity, gate: gateKey});
    let auditShown = false, planShown = false;
    for (const turn of model.turns) {
      const kind = text(turn.kind) || text(turn.type), content = turn.content && typeof turn.content === 'object' ? turn.content : {};
      const owner = turn.investigation_id || investigation.id, currentTurn = owner === investigation.id;
      const step = kind === 'user_question' || turn.role === 'user' ? 2 : Number.isInteger(turn.step) && turn.step >= 1 && turn.step <= 5 ? turn.step : kind === 'synthesis' ? 5 : ['job_event', 'error', 'scientific_operation'].includes(kind) ? 4 : 3;
      if (kind === 'data_audit') {
        const profile = content.profile || (content.observed ? content : currentTurn ? model.profile || content : content);
        const card = auditCard(profile);card.setAttribute('data-turn-id', text(turn.id));flow.append(owner, 1, card);cards.push(card);if (currentTurn) auditShown = true;continue;
      }
      if (kind === 'clarification' && currentTurn && model.inlineGate.kind === 'clarification') continue;
      if (kind === 'synthesis' && currentTurn && model.synthesis) continue;
      const operatorReceipt = kind === 'plan_review' && (content.review || content.operator_review);
      if (operatorReceipt && typeof operatorReceipt === 'object') {
        const card = element('article', 'wc-card wc-operator-review');
        card.setAttribute('data-turn-id', text(turn.id));card.setAttribute('data-turn-kind', kind);card.setAttribute('data-turn-title', text(turn.title));
        const decision = text(operatorReceipt.decision), decisionLabel = {approve: 'Approved', reject: 'Rejected', needs_information: 'Needs information'}[decision] || (decision ? label(decision) : 'Recorded');
        card.append(cardHeader('Operator review', decisionLabel, decision === 'approve' ? 'good' : decision === 'reject' ? 'bad' : 'neutral'));
        const reviewer = text(operatorReceipt.reviewer) || text(operatorReceipt.reviewer_label);
        if (reviewer) card.append(element('p', 'wc-reviewer', reviewer));
        if (operatorReceipt.rationale) card.append(element('p', 'wc-message-body', text(operatorReceipt.rationale)));
        if (operatorReceipt.confirmations) card.append(createDisclosure('Confirmations', operatorReceipt.confirmations));
        card.append(createDisclosure('Review receipt evidence', {turn_title: text(turn.title), turn_kind: kind, ...content}));
        flow.append(owner, step, card);cards.push(card);continue;
      }
      if (['job_event', 'scientific_step', 'scientific_operation'].includes(kind)) {
        let summary;
        if (kind === 'job_event') summary = label(text(content.status) || text(content.to)) || keyLabel(text(turn.title));
        else {
          const activity = kind === 'scientific_step' ? content.step || {} : content.operation || {};
          const name = text(activity.node) || text(activity.action) || text(turn.title) || (kind === 'scientific_step' ? 'Scientific step' : 'Scientific operation');
          const status = text(activity.status) || text(content.status);
          summary = keyLabel(name) + (status && keyLabel(name).toLowerCase() !== keyLabel(status).toLowerCase() ? ' · ' + keyLabel(status) : '');
        }
        const disclosure = createDisclosure(summary, {turn_title: text(turn.title), turn_kind: kind, ...content});disclosure.className += ' wc-tool-step';
        disclosure.setAttribute('data-turn-id', text(turn.id));disclosure.setAttribute('data-turn-kind', kind);disclosure.setAttribute('data-turn-title', text(turn.title));
        flow.append(owner, step, disclosure);continue;
      }
      if (kind === 'plan_review') {
        const card = element('article', 'wc-card wc-plan-review');card.setAttribute('data-turn-id', text(turn.id));card.setAttribute('data-turn-kind', kind);
        const planStatus = currentTurn ? model.inlineGate.kind === 'review' ? 'Needs review' : label(model.status) : content.status ? label(content.status) : 'Recorded';
        card.append(cardHeader(text(turn.title) || 'Analysis plan', planStatus, currentTurn && model.inlineGate.kind === 'run' ? 'good' : 'neutral'));
        if (content.proposal && typeof content.proposal === 'object') card.append(metricGrid(planSummary({proposal: content.proposal})));
        if (Object.keys(content).length) card.append(createDisclosure('Plan evidence', content));
        flow.append(owner, step, card);cards.push(card);if (content.proposal && (!turn.investigation_id || turn.investigation_id === investigation.id)) planShown = true;continue;
      }
      const card = element('article', 'wc-card wc-turn wc-turn-' + kind + (turn.role === 'user' ? ' user-message' : ''));
      card.setAttribute('data-turn-id', text(turn.id));card.setAttribute('data-turn-kind', kind);
      card.append(cardHeader(kind === 'user_question' ? 'Your question' : text(turn.title) || keyLabel(kind || 'Recorded turn')));
      if (kind === 'user_question') {
        card.append(element('p', 'wc-message-body', text(content.question) || text(investigation.question)));
        const selection = content.selection || ((!turn.investigation_id || turn.investigation_id === investigation.id) ? model.selection : null);
        if (selection) card.append(element('span', 'wc-status-pill wc-pill-neutral', 'Selected cells (' + (Array.isArray(selection.observation_ids) ? selection.observation_ids.length.toLocaleString('en-US') : 'unavailable') + ')'));
      } else if (kind === 'error') {
        card.append(element('p', 'wc-message-body', failureSummary(text(content.message) || text(content.reason)) || 'Inspect the preserved error evidence.'));
        card.append(createDisclosure('Error evidence', content));
      } else {
        const message = text(content.message) || text(content.summary) || text(content.rationale);
        if (message) card.append(element('p', 'wc-message-body', message));
        if (Object.keys(content).length) card.append(createDisclosure(kind === 'clarification_answer' ? 'Supplied answers' : 'Recorded evidence', content));
      }
      flow.append(owner, step, card);cards.push(card);
    }
    // A profile is evidence in its own right; an absent turn never creates a completed stage.
    if (!auditShown && model.profile) {const card = auditCard(model.profile);flow.append(investigation.id, 1, card);cards.push(card);}
    if (!planShown && model.jobState.proposal) {const card = element('article', 'wc-card wc-plan-review');card.append(cardHeader('Recorded plan'), metricGrid(model.metrics));flow.append(investigation.id, 3, card);cards.push(card);}
    if (model.inlineGate.kind === 'clarification') {const card = renderClarification(container, model, options.onAction);flow.append(investigation.id, 1, card);cards.push(card);}
    else if (['scientific_review', 'scientific_execution_review'].includes(model.inlineGate.kind)) {const card = renderScientificGate(container, model, options.onAction);flow.append(investigation.id, model.inlineGate.kind === 'scientific_review' ? 3 : 4, card);cards.push(card);}
    else if (model.inlineGate.kind === 'review' || model.inlineGate.kind === 'run') {
      const gate = element('article', 'wc-card wc-inline-gate');gate.setAttribute('data-inline-gate', model.inlineGate.kind);
      gate.append(cardHeader(model.inlineGate.kind === 'review' ? 'Review and approve the plan' : 'Ready to execute', model.inlineGate.kind === 'run' ? 'Approved' : 'Needs review', model.inlineGate.kind === 'run' ? 'good' : 'warning'));
      gate.append(element('p', '', model.inlineGate.kind === 'review' ? 'Review the assumptions and confirmations below, then record your decision.' : options.config && options.config.execution_allowed ? 'Start the approved analysis using the run controls below.' : 'Data execution is disabled for this service.'));
      flow.append(investigation.id, model.inlineGate.kind === 'review' ? 3 : 4, gate);cards.push(gate);
    }
    if (model.synthesis) {
      const synthesis = element('article', 'wc-card wc-synthesis');synthesis.append(cardHeader('Scientific synthesis'));
      appendSynthesisSections(synthesis, model.synthesis);
      if (model.artifacts.length) synthesis.append(actionButton({type: 'navigate', view: 'results', label: 'Inspect figures and evidence'}, options.onAction, 'secondary wc-artifact-reference'));
      flow.append(investigation.id, 5, synthesis);cards.push(synthesis);
      const followups = element('section', 'wc-followups');followups.append(element('h3', '', 'Continue the chat'));
      const suggestions = element('div', 'wc-suggestions');
      for (const [caption, question] of [
        ['Review quality concerns', 'Which quality concerns can the saved evidence and supported workflow help inspect in this dataset?'],
        ['Inspect batch structure', 'What can the supported batch diagnostics establish for this dataset, and what remains uncertain?'],
        ['Review method scope', 'Which supported methods could address a follow-up question with this dataset, and what additional evidence or metadata would they require?']
      ]) suggestions.append(actionButton({type: 'draft', label: caption, question: model.sources.length > 1 ? question.replace('this dataset', 'these datasets') : question, sourceRunId: investigation.job_id || investigation.parent_job || null, ...(model.sources.length > 1 ? {sources: model.sources.slice()} : {input: text(investigation.source) ? {path: investigation.source} : null})}, options.onAction, 'secondary wc-suggestion'));
      followups.append(suggestions);flow.append(investigation.id, 5, followups);
    } else if (model.artifacts.length) {
      const artifacts = element('article', 'wc-card wc-artifact-card');artifacts.append(cardHeader('Analysis artifacts', model.artifacts.length + ' recorded'));
      artifacts.append(actionButton({type: 'navigate', view: 'results', label: 'Open artifact canvas'}, options.onAction));flow.append(investigation.id, 4, artifacts);cards.push(artifacts);
    }
    flow.append(investigation.id, 3, createDisclosure('Planning source', {mode: model.mode, scope: model.scope, ...(model.planningSource ? {record: model.planningSource} : {})}));
    flow.finish();
    restoreView();animateCards(cards, changedRun);return model;
  }
  root.WorkbenchConversation = Object.freeze({buildModel, render: renderConversationWorkspace, renderConversationWorkspace, failureSummary, planSummary, buildInvestigationModel, renderInvestigation});
})(typeof window !== 'undefined' ? window : globalThis);
