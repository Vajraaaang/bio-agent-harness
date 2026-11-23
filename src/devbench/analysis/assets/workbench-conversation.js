/* Recorded scientific conversation. Presentation only: no network or execution. */
(function (root) {
  'use strict';
  const ACTIVE = new Set(['queued', 'inspecting', 'planning', 'recording_review', 'preparing', 'running', 'cancel_requested', 'cancelling', 'finalizing']);
  const FAILURES = new Set(['operation_failed', 'failed', 'recovery_required', 'cancelled', 'interrupted', 'interrupted_service', 'infrastructure_failure', 'execution_failure_unclassified']);
  const PLAN_ATTENTION = new Set(['awaiting_information', 'needs_information', 'refusal', 'invalid_proposal', 'rejected']);
  const LABELS = {
    queued: 'Request queued', inspecting: 'Inspecting input metadata', planning: 'Preparing a proposal',
    awaiting_operator_review: 'Awaiting operator review', recording_review: 'Recording operator decision',
    approved: 'Plan approved', preparing: 'Preparing controlled execution', running: 'Analysis running',
    cancel_requested: 'Cancellation requested', cancelling: 'Cancellation requested', finalizing: 'Finalizing recorded outputs',
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
  const diagnostics = new Map([
    ['sqlite_wal_upgrade_required', 'Scientific workflows require patched SQLite 3.51.3 or later, or a documented backport. On macOS, set DYLD_LIBRARY_PATH to the directory with the patched SQLite library before restarting.'],
    ['model_selector_not_configured', 'Model-planned analysis requires a configured selector. Start the Workbench with a selector or ask for a local dataset description.'],
    ['scientific_workspace_not_enabled', 'Restart the Workbench with --enable-scientific to use governed diagnostics and analyses.'],
    ['data_execution_disabled', 'Restart the Workbench with --allow-data-execution and review any required run authorization.'],
    ['scientific_workflow_not_configured', 'This question needs a configured scientific workflow. Load the workflow for the attached data before proceeding.'],
    ['adaptive_runtime_not_bound', 'This question has no bound adaptive runtime. Confirm the input declarations and connect the adaptive runtime before advancing.'],
  ]);
  function failureSummary(value) {
    const raw = text(value);
    if (diagnostics.has(raw)) return diagnostics.get(raw);
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
      const authoring = planning.authoring_receipt || {};
      const details = {decision_kind: text(decision.kind) || null, authoring_root: text(authoring.root_hash) || null};
      model.records.push(record('planner', mode === 'Recorded offline proposal' ? 'Offline planner record' : 'Planner record',
        label(text(planning.status) || status), label(text(decision.kind) || 'Recorded planning status'), details));
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
  const evidenceTablePages = new WeakMap();
  const inlineActivityStates = new WeakMap();
  const ACTIVITY_PHASES = {audit: 'Inspecting data', question: 'Checking the question', discovery: 'Reviewing method sources', generate: 'Preparing a plan', candidate_review: 'Reviewing proposed methods', resolve: 'Checking method requirements', implementation: 'Preparing implementation', design: 'Checking design', design_review: 'Reviewing design', review: 'Awaiting plan review', declare: 'Recording the reviewed trial', execution_review: 'Awaiting run approval', run: 'Running analysis', verify: 'Checking outputs', recommend: 'Preparing a conclusion', conclusion_review: 'Reviewing the conclusion'};
  const ACTIVITY_TOOLS = {scrna_population_audit: 'Inspecting populations', scrna_population_design: 'Checking design', scrna_population_pseudobulk: 'Preparing pseudobulk', scrna_population_contrast: 'Running population contrasts'};
  const ACTIVITY_ACTIONS = {create_plan: 'Preparing a plan', advance_to_gate: 'Scientific planning', run_trial: 'Running analysis', rerun: 'Rerunning analysis', ...ACTIVITY_PHASES};
  function activityState(value) {
    if (['queued', 'reserved'].includes(value)) return 'queued';
    if (['active', 'running', 'started', 'executing', 'inspecting', 'planning', 'preparing', 'finalizing', 'recording_review'].includes(value)) return 'running';
    if (['completed', 'complete', 'succeeded', 'completed_review_passed', 'completed_review_failed', 'completed_presentation'].includes(value)) return 'completed';
    if (['failed', 'operation_failed', 'refused', 'refusal', 'execution_failure_unclassified', 'infrastructure_failure'].includes(value)) return 'failed';
    if (['cancel_requested', 'cancelling'].includes(value)) return 'stopping';
    if (value === 'cancelled') return 'cancelled';
    if (['recovery_required', 'uncertain', 'interrupted', 'interrupted_service'].includes(value)) return 'recovery';
    if (['awaiting_information', 'needs_information', 'awaiting_operator_review', 'approved', 'review_required'].includes(value)) return 'waiting';
    if (value === 'ready') return 'available';
    return null;
  }
  function buildActivityModel(model, now) {
    now = Number.isFinite(now) ? now : Date.now();
    const events = [], latest = new Map(), starts = new Map(), transitions = new Set(), artifactIds = new Set();
    const timestamp = value => typeof value === 'string' && Number.isFinite(Date.parse(value)) ? value : '';
    const turns = (model.turns || []).filter(turn => !turn.investigation_id || turn.investigation_id === model.id);
    function add(turn, group, task, value, observedAt, explicitStart) {
      const state = activityState(value);if (!state) return;
      const at = timestamp(observedAt), previous = latest.get(group), signature = group + ':' + state + ':' + at + ':' + task;
      if (transitions.has(signature) || previous && previous.state === state && previous.task === task) return;
      transitions.add(signature);
      if (state === 'running') starts.set(group, timestamp(explicitStart) || at);
      const prefix = {queued: 'Queued', running: 'Started', completed: 'Finished', failed: 'Failed', cancelled: 'Cancelled', stopping: 'Stop requested', recovery: 'Recovery required', waiting: 'Waiting', available: 'Recorded'}[state];
      const row = {id: text(turn.id) || group + ':' + state + ':' + at, group, task, state, at, label: prefix + ' · ' + task, startedAt: timestamp(explicitStart) || starts.get(group) || ''};
      events.push(row);latest.set(group, row);
    }
    for (const turn of turns) {
      const content = turn.content || {}, operation = content.operation || {}, step = content.step || {};
      if (turn.kind === 'lifecycle') add(turn, 'description:' + (content.operation_id || turn.id), 'Inspecting dataset metadata', content.state, turn.at, content.started_at);
      else if (turn.kind === 'scientific_operation') add(turn, 'operation:' + (operation.operation_id || turn.id), ACTIVITY_ACTIONS[operation.action] || 'Scientific operation', operation.status, operation.finished_at || turn.at, operation.started_at);
      else if (turn.kind === 'scientific_action' && content.result?.operation_id) add(turn, 'operation:' + content.result.operation_id, ACTIVITY_ACTIONS[content.action] || 'Scientific operation', content.result.status, turn.at, content.result.started_at);
      else if (turn.kind === 'scientific_step') add(turn, 'step:' + (step.session_id || model.scientificSessionId || model.id) + ':' + text(step.node) + ':' + (step.attempt || 0), ACTIVITY_PHASES[step.node] || 'Scientific step', step.status, turn.at, step.started_at);
      else if (turn.kind === 'singlecell_evidence') add(turn, 'method:' + (content.binding?.operation_id || turn.id), ACTIVITY_TOOLS[content.tool_name] || 'Single-cell analysis', content.status, turn.at, content.started_at);
      else if (turn.kind === 'job_event') add(turn, 'job:' + (content.job_id || model.inlineGate?.jobId || model.id), 'Native analysis', content.status || content.to, content.at || turn.at, content.started_at);
      else if (turn.kind === 'error') {const group = content.operation_id ? 'operation:' + content.operation_id : 'error:' + turn.id;add(turn, group, latest.get(group)?.task || 'Recorded operation', 'failed', turn.at);}
      for (const ref of Array.isArray(content.artifact_refs) ? content.artifact_refs : []) if (text(ref.artifact_id)) artifactIds.add(ref.artifact_id);
    }
    if (!turns.some(turn => turn.kind === 'job_event')) for (const [index, event] of (Array.isArray(model.jobState?.state_history) ? model.jobState.state_history : []).entries()) add({id: 'job-history-' + index + '-' + text(event.at)}, 'job:' + (model.inlineGate?.jobId || model.id), 'Native analysis', event.to, event.at);
    for (const artifact of model.artifacts || []) if (text(artifact.artifact_id)) artifactIds.add(artifact.artifact_id);
    const pending = Array.from(latest.values()).filter(row => ['running', 'queued', 'recovery'].includes(row.state));
    const candidate = pending.sort((a, b) => (Date.parse(a.at) || 0) - (Date.parse(b.at) || 0)).at(-1);
    const status = text(model.status);let state = 'idle', caption = 'No active operation';
    if (model.workerLiveness === 'unknown' || ['recovery_required', 'interrupted', 'interrupted_service'].includes(status)) {state = 'recovery';caption = 'Recovery requires review';}
    else if (status === 'cancelled') {state = 'cancelled';caption = 'Operation cancelled';}
    else if (FAILURES.has(status) || ['refusal', 'rejected', 'invalid_proposal'].includes(status)) {state = 'failed';caption = label(status);}
    else if (['cancel_requested', 'cancelling'].includes(status)) {state = 'stopping';caption = 'Cancellation requested';}
    else if (model.synthesisContinuation?.status === 'active') {state = 'working';caption = synthesisStatusLabel(model.synthesisContinuation);}
    else if (model.synthesisContinuation?.status === 'pending') {state = 'waiting';caption = synthesisStatusLabel(model.synthesisContinuation);}
    else if (status === 'queued') {state = 'queued';caption = 'Queued';}
    else if (PLAN_ATTENTION.has(status) || ['awaiting_operator_review', 'approved', 'information_requested'].includes(status)) {state = 'waiting';caption = status === 'approved' ? 'Ready for run approval' : label(status);}
    else if (status === 'completed' || status.startsWith('completed_') || status === 'abstained') {state = 'complete';caption = model.request?.category === 'descriptive_retrieval' ? 'Description complete' : 'Recorded work completed';}
    else if (ACTIVE.has(status)) {
      if (candidate?.state === 'queued') {state = 'queued';caption = 'Queued';}
      else if (candidate?.state === 'recovery') {state = 'recovery';caption = 'Recovery requires review';}
      else {state = 'working';caption = candidate?.task || ({inspecting: 'Inspecting data', planning: 'Preparing a plan', recording_review: 'Recording review', preparing: 'Preparing analysis', running: 'Working', finalizing: 'Finalizing outputs'})[status] || 'Working';}
    }
    let elapsed = '';
    if (state === 'working' && candidate?.startedAt && now >= Date.parse(candidate.startedAt)) {
      const seconds = Math.floor((now - Date.parse(candidate.startedAt)) / 1000);
      elapsed = seconds < 60 ? seconds + ' s elapsed' : Math.floor(seconds / 60) + ' m ' + String(seconds % 60).padStart(2, '0') + ' s elapsed';
    }
    return {state, label: caption, working: state === 'working', events, steps: latest.size,
      started: events.filter(row => row.state === 'running').length, completed: Array.from(latest.values()).filter(row => row.state === 'completed').length, artifacts: artifactIds.size, elapsed};
  }
  function inlineActivity(container, key, model, compact = false) {
    let state = inlineActivityStates.get(container);
    if (!state || state.key !== key || renderedRuns.get(container) !== key) {
      const answer = element('div', 'wc-public-answer'), host = element('section', 'wc-inline-activity'), trail = element('div', 'wc-conversation-trail');
      host.setAttribute('aria-label', 'Recorded activity');
      const status = element('div', 'wc-activity-status'), icon = element('span', 'wc-activity-icon', '•'), caption = element('span', 'wc-activity-text'), elapsed = element('span', 'wc-activity-elapsed');
      icon.setAttribute('aria-hidden', 'true');caption.setAttribute('aria-hidden', 'true');elapsed.setAttribute('aria-hidden', 'true');elapsed.setAttribute('title', 'Elapsed since the first retained running record for this step.');status.append(icon, caption, elapsed);
      const live = element('span', 'wc-visually-hidden wc-activity-announcement');live.setAttribute('role', 'status');live.setAttribute('aria-live', 'polite');live.setAttribute('aria-atomic', 'true');
      const log = element('details', 'wc-activity-log'), summary = element('summary', 'wc-activity-summary'), list = element('ol', 'wc-activity-list');
      summary.setAttribute('id', 'activity-summary-' + model.id);log.append(summary, list);host.append(status, live, log);
      state = {key, answer, host, trail, status, caption, elapsed, live, log, summary, list, signature: '', eventSignature: '', mode: null};inlineActivityStates.set(container, state);container.replaceChildren(answer, host, trail);
    }
    const activity = buildActivityModel(model), signature = JSON.stringify([activity.state, activity.label, activity.events.map(row => row.id), activity.artifacts]);
    state.host.hidden = !activity.events.length && !['working', 'queued', 'stopping', 'failed', 'recovery', 'cancelled'].includes(activity.state) && !activity.artifacts;
    state.status.className = 'wc-activity-status' + (activity.working ? ' wc-activity-working' : '');state.status.setAttribute('data-activity-state', activity.state);
    state.caption.className = 'wc-activity-text' + (activity.working ? ' wc-shimmer-text' : '');
    if (state.caption.textContent !== activity.label) {state.caption.textContent = activity.label;state.caption.setAttribute('data-activity-label', activity.label);}
    if (state.elapsed.textContent !== activity.elapsed) state.elapsed.textContent = activity.elapsed;
    state.log.hidden = !activity.events.length && !activity.artifacts;
    state.summary.textContent = activity.steps + (activity.steps === 1 ? ' step' : ' steps') + ' · ' + activity.artifacts + (activity.artifacts === 1 ? ' artifact' : ' artifacts');
    if (state.mode !== activity.state) {
      if (activity.state === 'complete') state.log.open = false;
      else if (['failed', 'cancelled', 'recovery', 'waiting'].includes(activity.state) ||
        !compact && state.mode === null && ['working', 'queued', 'stopping'].includes(activity.state)) state.log.open = true;
      state.mode = activity.state;
    }
    const eventSignature = JSON.stringify(activity.events);
    if (state.eventSignature !== eventSignature) {
      const scroll = state.list.scrollTop || 0;state.list.replaceChildren();
      for (const row of activity.events) {
        const item = element('li', 'wc-activity-row');
        item.setAttribute('data-activity-event-id', row.id);
        item.setAttribute('data-activity-event-state', row.state);
        const marker = element('span', 'wc-activity-row-marker', row.state === 'completed' ? '✓' : row.state === 'failed' ? '!' : '·');
        marker.setAttribute('aria-hidden', 'true');
        const textNode = element('span', 'wc-exec-label', row.label);
        const chevron = element('span', 'wc-exec-chevron', '›');
        chevron.setAttribute('aria-hidden', 'true');
        item.append(marker, textNode, chevron);
        if (row.at) {
          const time = element('time', 'wc-activity-time', row.at.slice(11, 19));
          time.setAttribute('datetime', row.at);
          item.append(time);
        }
        state.list.append(item);
      }
      state.list.scrollTop = scroll;state.eventSignature = eventSignature;
    }
    if (state.signature !== signature) {state.live.textContent = activity.label + (activity.events.length ? '. ' + activity.events.at(-1).label + '.' : '');state.signature = signature;}
    state.answer.replaceChildren();state.trail.replaceChildren();return state;
  }
  const SCIENTIFIC_SECTIONS = [
    ['Data', 'What data do I have?'],
    ['Question', 'What question am I investigating?'],
    ['Methods', 'Which methods are appropriate, and why?'],
    ['Results', 'What happened?'],
    ['Conclusion', 'What can I conclude, and what remains uncertain?']
  ];
  const INVESTIGATION_SECTIONS = [
    ['Define an aim', 'Data and research question'],
    ['Plan & run', 'Methods, execution and recovery'],
    ['Review findings', 'Results, interpretation and uncertainty']
  ];
  function investigationStage(step, execution = false) {
    return step <= 2 ? 1 : step === 3 || execution ? 2 : 3;
  }
  const stageCaptions = grouped => grouped
    ? INVESTIGATION_SECTIONS.map(([caption]) => caption)
    : SCIENTIFIC_SECTIONS.map(([caption], index) => (index + 1) + '. ' + caption);

  function visibleStages(raw, activity, phase) {
    const groups = [raw.slice(0, 2), [raw[2]], raw.slice(3)];
    if (phase === 2 && activity?.step === 4) groups[1].push(raw[3]);
    return groups.map(group => group.find(status =>
      ['failed', 'cancelled', 'awaiting_information', 'running', 'queued'].includes(status.status)
    ) || [...group].reverse().find(status =>
      !['not_run', 'not_applicable'].includes(status.status)
    ) || group[0]);
  }
  function preserveConversationView(container, preserve) {
    if (!preserve || typeof container.querySelectorAll !== 'function' || typeof container.closest !== 'function') return () => {};
    function entries() {
      const counts = new Map();
      return Array.from(container.querySelectorAll('details.wc-disclosure,details.wc-raw-json,details.wc-public-assumptions,details.wc-research-feed,.wc-answer-table')).map(node => {
        const turn = node.closest('[data-turn-id]'), workflow = node.closest('[data-workflow-id]'), article = node.closest('article');
        const title = node.querySelector('.wc-disclosure-title') || node.querySelector('summary');
        const prefix = node.getAttribute('id') || (turn && turn.getAttribute('data-turn-id') || workflow && workflow.getAttribute('data-workflow-id') || '') + ':' + (article && article.className || '') + ':' + (title && title.textContent || '');
        const ordinal = counts.get(prefix) || 0;counts.set(prefix, ordinal + 1);return [prefix + ':' + ordinal, node];
      });
    }
    const saved = new Map(entries().map(([key, node]) => {const body = node.querySelector('.wc-disclosure-body') || node;return [key, {open: node.open, scroll: body.scrollTop || 0, left: body.scrollLeft || 0}];}));
    const scrolls = [];
    for (let node = container; node; node = node.parentElement) if (Number.isFinite(node.scrollTop) || Number.isFinite(node.scrollLeft)) scrolls.push([node, node.scrollTop || 0, node.scrollLeft || 0]);
    const active = document.activeElement, focused = active && typeof container.contains === 'function' && container.contains(active) && (active.id || active.getAttribute('id'));
    const selection = focused && typeof active.selectionStart === 'number' ? [active.selectionStart, active.selectionEnd] : null;
    return () => {
      for (const [key, node] of entries()) if (saved.has(key)) {const value = saved.get(key);node.open = value.open;const body = node.querySelector('.wc-disclosure-body') || node;body.scrollTop = value.scroll;body.scrollLeft = value.left;}
      if (focused && typeof document.getElementById === 'function') {
        const field = document.getElementById(focused), section = field && field.closest('details.wc-workflow-section');
        if (field && (!section || section.open)) {field.focus({preventScroll: true});if (selection && typeof field.setSelectionRange === 'function') field.setSelectionRange(...selection);}
      }
      for (const [node, top, left] of scrolls) {node.scrollTop = top;node.scrollLeft = left;}
    };
  }
  function workflowPhase(model) {
    const descriptionStatus = model.descriptionOperation && model.descriptionOperation.status;
    if (model.request?.cell_type_comparison && descriptionStatus) {
      return descriptionStatus === 'completed' ? 5 : 3;
    }
    if (descriptionStatus && descriptionStatus !== 'completed') return 1;
    if (descriptionStatus === 'completed' && !model.request?.remaining_action) return 4;
    const gate = model.inlineGate && model.inlineGate.kind;
    if (gate === 'clarification') return model.fields?.length && model.fields.every(field => field.scope === 'request') ? 2 : 1;
    if (gate === 'episode_clarification') return 4;
    if (gate === 'review' || gate === 'scientific_review') return 3;
    if (gate === 'run' || gate === 'scientific_execution_review') return 4;
    if (model.adaptiveResearch) {
      return ['running', 'queued', 'active'].includes(model.adaptiveResearch.activity?.current?.status) ? 3
        : text(researchFinding(model.adaptiveResearch).answer) ? 5 : 3;
    }
    if (model.matrixAudit) return 4;
    if (model.synthesisContinuation) return 5;
    const singlecell = (model.turns || []).filter(turn => (!turn.investigation_id || turn.investigation_id === model.id) && turn.kind === 'singlecell_evidence').at(-1);
    if (singlecell && !model.synthesis && !ACTIVE.has(model.status)) return singlecell.step || 4;
    if (model.synthesis || text(model.status).startsWith('completed')) return 5;
    if (['preparing', 'running', 'cancel_requested', 'finalizing', 'approved'].includes(model.status)) return 4;
    if (model.status === 'planning' || model.status === 'awaiting_operator_review') return 3;
    const steps = (model.turns || []).filter(turn => (!turn.investigation_id || turn.investigation_id === model.id) && turn.role !== 'user' && turn.kind !== 'user_question' && Number.isInteger(turn.step)).map(turn => turn.step);
    return steps.length ? Math.max(1, Math.min(5, steps[steps.length - 1])) : 1;
  }
  function workflowActivity(model, phase) {
    const status = text(model.status), gate = model.inlineGate && model.inlineGate.kind;
    const current = model.adaptiveResearch?.activity?.current;
    if (current?.title && ['running', 'active', 'queued'].includes(current.status)) {
      return {kind: current.status === 'queued' ? 'queued' : 'running', step: 3,
        label: current.title};
    }
    const continuation = model.synthesisContinuation;
    if (continuation && !['completed', 'abstained'].includes(continuation.status)) {
      return {kind: continuation.status === 'active' ? 'running' : 'attention', step: 5,
        label: synthesisStatusLabel(continuation)};
    }
    const descriptionStatus = model.descriptionOperation && (FAILURES.has(status) && ['queued', 'running', 'cancel_requested'].includes(model.descriptionOperation.status) ? status === 'cancelled' ? 'cancelled' : 'failed' : model.descriptionOperation.status);
    if (['queued', 'running', 'cancel_requested'].includes(descriptionStatus)) return {kind: descriptionStatus === 'queued' ? 'queued' : descriptionStatus === 'running' ? 'running' : 'attention', step: model.request?.cell_type_comparison ? 3 : 1, label: descriptionStatus === 'queued' ? 'Description queued' : descriptionStatus === 'running' ? model.request?.cell_type_comparison ? 'Running local cell-type comparison' : 'Reading saved dataset information' : 'Cancellation requested'};
    if (['failed', 'cancelled'].includes(descriptionStatus)) return {kind: 'attention', step: 1, label: descriptionStatus === 'failed' ? 'Description failed' : 'Description cancelled'};
    if (descriptionStatus === 'completed' && !model.request?.remaining_action && PLAN_ATTENTION.has(status)) return {kind: 'attention', step: 4, label: 'Description incomplete'};
    if (model.matrixAudit && status === 'awaiting_information') return {kind: 'attention', step: 4, label: 'Matrix diagnostic incomplete'};
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
      if (status === 'queued' || buildActivityModel(model).state === 'queued') return {kind: 'queued', step, label: 'Request queued'};
      if (['cancel_requested', 'cancelling'].includes(status)) return {kind: 'attention', step, label: label(status)};
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
  function investigationStages(model, activity) {
    const labels = {not_run: 'Not run', not_applicable: 'Not applicable', observed: 'Observed', available: 'Available', queued: 'Queued', running: 'Running', cancel_requested: 'Cancellation requested', awaiting_information: 'Awaiting information', failed: 'Failed', cancelled: 'Cancelled', completed: 'Completed'};
    const stages = Array.from({length: 5}, () => ({status: 'not_run', label: labels.not_run}));
    const set = (step, status, caption) => {status = ({complete: 'completed', not_started: 'not_run'})[status] || status;if (Number.isInteger(step) && step >= 1 && step <= 5 && labels[status]) stages[step - 1] = {status, label: caption || labels[status]};};
    const turns = model.turns.filter(turn => !turn.investigation_id || turn.investigation_id === model.id);
    if (model.profile) set(1, 'observed');
    for (const turn of turns) {
      if (turn.kind === 'lifecycle') for (const stage of Array.isArray(turn.content?.stages) ? turn.content.stages : []) if (stage && typeof stage === 'object') set(stage.step, stage.status);
      if (turn.kind === 'data_audit') set(1, 'observed');
      if (turn.kind === 'user_question' || turn.kind === 'question_scope') set(2, 'available');
      if (['plan_review', 'method_assessment'].includes(turn.kind)) set(3, 'available');
      if (turn.kind === 'dataset_answer') set(4, 'available');
      if (turn.kind === 'synthesis') set(5, 'available');
      if (turn.kind === 'singlecell_evidence') {
        const content = turn.content || {}, step = Number.isInteger(turn.step) ? turn.step : 4;
        const status = ['failed', 'refused', 'cancelled'].includes(content.status) ? content.status === 'cancelled' ? 'cancelled' : 'failed' : ['queued', 'running'].includes(content.status) ? content.status : ['completed', 'succeeded'].includes(content.status) ? content.tool_name === 'scrna_population_audit' ? 'observed' : content.tool_name === 'scrna_population_contrast' ? 'completed' : 'available' : 'available';
        set(step, status);
      }
    }
    if ((model.jobState.proposal || model.scientificPlan) && stages[2].status === 'not_run') set(3, 'available');
    if (model.artifacts.length && stages[3].status === 'not_run') set(4, 'available');
    if (model.synthesis) set(5, 'available');
    if (model.matrixAudit) {
      set(3, 'available', 'Descriptive methods');
      if (model.status === 'completed') set(4, 'completed', 'Matrix diagnostics complete');
      set(5, 'not_applicable', 'Descriptive evidence only');
    }
    const operation = model.descriptionOperation, remaining = model.request?.remaining_action;
    if (operation) {
      if (['queued', 'running', 'cancel_requested', 'failed', 'cancelled'].includes(operation.status)) set(1, operation.status);
      if (operation.status === 'completed') {
        const answer = turns.filter(turn => turn.kind === 'dataset_answer').at(-1)?.content;
        if (answer) set(4, remaining || answer.completion !== 'completed' ? 'available' : 'completed', remaining ? 'Descriptive part available' : answer.completion === 'completed' ? 'Description complete' : 'Description ' + (answer.completion === 'unavailable' ? 'unavailable' : 'partial'));
      }
      if (!remaining && !model.request?.cell_type_comparison) {set(3, 'not_applicable', 'Not applicable · retrieval');set(5, 'not_applicable', 'Not applicable · no scientific conclusion');}
      else if (!model.synthesis) set(5, 'not_run');
      if (FAILURES.has(model.status) && ['queued', 'running', 'cancel_requested'].includes(operation.status)) set(1, model.status === 'cancelled' ? 'cancelled' : 'failed', label(model.status));
    }
    if (activity && (!operation || operation.status === 'completed')) {
      const status = activity.kind === 'running' ? 'running' : activity.kind === 'queued' ? 'queued' : ['cancel_requested', 'cancelling'].includes(model.status) ? 'cancel_requested' : FAILURES.has(model.status) ? 'failed' : 'awaiting_information';
      set(activity.step, status, activity.label.replace(/^Needs attention: /, ''));
    }
    return stages;
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
      const header = element('header', 'wc-workflow-header');header.append(element('h3', 'wc-workflow-title', options.history ? 'Recorded workflow' : 'Analysis overview'));header.setAttribute('title', title);panel.append(header);
      const sections = [], counts = [], badges = [];
      const supportedSections = options.grouped ? INVESTIGATION_SECTIONS : SCIENTIFIC_SECTIONS;
      supportedSections.forEach(([caption, question], index) => {
        const number = index + 1, identity = id + ':' + number, details = element('details', 'wc-workflow-section' + (current && number === phase ? ' is-current' : ''));
        const activity = current && options.activity && options.activity.step === number ? options.activity : null;
        const stage = current && options.stages && options.stages[index];
        if (activity) {details.className += ' is-' + activity.kind;details.setAttribute('data-workflow-state', activity.kind);}
        if (stage) details.setAttribute('data-workflow-status', stage.status);
        details.setAttribute('data-scientific-step', String(number));details.open = state.expanded.has(identity);
        const summary = element('summary', 'wc-workflow-tab');summary.setAttribute('title', question + (activity ? ' · ' + activity.label : stage ? ' · ' + stage.label : ''));summary.setAttribute('aria-label', caption + ': ' + question + (activity ? '; ' + activity.label : stage ? '; ' + stage.label : ''));
        const marker = element('span', 'wc-workflow-marker');marker.setAttribute('aria-hidden', 'true');
        const label = element('h4', 'wc-workflow-label' + (activity && activity.kind === 'running' ? ' wc-shimmer-text' : ''), caption), fullQuestion = element('span', 'wc-visually-hidden', question), badge = element('span', 'wc-workflow-count'), arrow = chevron();
        summary.append(marker, label, fullQuestion);
        if (stage) summary.append(element('span', 'wc-workflow-stage-status', stage.label));
        summary.append(badge, arrow);details.append(summary);
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
      append(id, number, node, execution = false) {const item = group(id), index = (options.grouped ? investigationStage(number || 3, execution) : Math.max(1, Math.min(5, number || 3))) - 1;item.sections[index].append(node);item.counts[index]++;},
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
  function createDisclosure(title, value, open = false) {
    const details = element('details', 'wc-disclosure');
    if (open) details.open = true;
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
      if (typeof onAction !== 'function') return;
      if (action.type === 'navigate') onAction({type: action.type, view: action.view});
      if (action.type === 'artifact') onAction({type: action.type, view: action.view, artifactId: action.artifactId});
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
      const sourceAuditHost = element('div', 'welcome-source-audit');
      sourceAuditHost.setAttribute('id', 'welcome-source-audit');
      welcome.append(sourceAuditHost);
      const chipsHost = element('div', 'quick-question-chips');
      chipsHost.setAttribute('id', 'welcome-quick-chips');
      chipsHost.setAttribute('aria-label', 'Suggested investigations');
      welcome.append(chipsHost);
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
      execution.append(body);flow.append(owner, 4, execution, true);
    }
    const summary = element('article', 'wc-card wc-summary wc-tone-' + model.summary.tone);summary.setAttribute('role', 'status');
    summary.append(cardHeader(model.summary.title), element('p', '', model.summary.body));
    if (model.primaryAction) summary.append(actionButton(model.primaryAction, options.onAction, 'record-action wc-primary-action'));
    flow.append(owner, phase, summary);cards.push(summary);
    if (model.failureRecord) flow.append(owner, 4, createDisclosure('Failure evidence', model.failureRecord), true);
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
    if (proposal.format === 'devbench.episode-proposal/1') {
      const task=configuration.public_task || {}, limits=configuration.limits || {};
      return [
        {label:'Input preview',value:(Array.isArray(task.gene_ids) ? task.gene_ids.length.toLocaleString('en-US')+' genes' : Number.isSafeInteger(task.gene_family?.count)&&task.gene_family.count>0 ? task.gene_family.count.toLocaleString('en-US')+' genes' : 'Gene count unavailable')+' · '+(text(task.measurement)||'Measurement unavailable')},
        {label:'Workflow',value:'Bulk condition contrast · '+(text(task.questions?.[0]?.contrast)||'Contrast unavailable')},
        {label:'Design',value:text(task.context?.dependence)||'Dependence not declared'},
        {label:'Resource limits',value:Number.isFinite(limits.timeout_seconds)&&Number.isFinite(limits.memory_mb) ? limits.timeout_seconds.toLocaleString('en-US')+' s · '+limits.memory_mb.toLocaleString('en-US')+' MiB / worker' : 'Resource limits unavailable'}
      ];
    }
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
    if (investigation.analyst_profile === 'analyst_v1' && state.operation_kind === 'agentic_episode') {
      mode = 'Scientific analyst';scope = 'This thread shows recorded diagnostic, query and decision actions. Execution verification and independent scientific review remain separate.';
    }
    if (investigation.request?.category === 'descriptive_retrieval') {
      mode = 'Local dataset retrieval';scope = 'Stored metadata and retained results are read locally. This description is not a scientific analysis or validation of stored labels.';
    }
    const matrixAudit = Boolean(investigation.descriptive_matrix_audit);
    const matrixIntake = investigation.required_action === 'matrix_intake';
    if (matrixAudit || matrixIntake) {
      mode = 'Local matrix diagnostics';
      scope = 'The recorded diagnostics describe the selected measurement layer and observed profiles.';
    }
    const seenTurns = new Set();
    const turns = (Array.isArray(investigation.thread_turns) ? investigation.thread_turns : Array.isArray(investigation.turns) ? investigation.turns : []).filter(turn => {
      if (!turn || typeof turn !== 'object') return false;
      if (!text(turn.id)) return true;
      const identity = (text(turn.investigation_id) || investigation.id) + ':' + turn.id;
      if (seenTurns.has(identity)) return false;
      seenTurns.add(identity);return true;
    });
    let gate = null;
    if (state.operation_kind === 'agentic_episode' && ['awaiting_information', 'information_requested', 'cancelled'].includes(status) && (state.information_request || state.handoff)) gate = 'episode_clarification';
    else if (fields.length && ['awaiting_information', 'needs_information'].includes(status)) gate = 'clarification';
    else if (investigation.job_id && (status === 'awaiting_operator_review' || status === 'needs_information' && state.proposal && !state.review_receipt)) gate = 'review';
    else if (investigation.job_id && status === 'approved') gate = 'run';
    else if (!investigation.job_id && investigation.scientific_session_id && ['review', 'execution_review'].includes(investigation.gate)) gate = 'scientific_' + investigation.gate;
    return {
      id: investigation.id, questionId: investigation.question_id || investigation.id, revision: investigation.revision, question: text(investigation.question), status, workerLiveness: text(state.worker_liveness) || text(investigation.worker_liveness),
      turns, request: investigation.request || null, descriptionOperation: investigation.description_operation || null, error: investigation.error || state.error || null,
      diagnostic: diagnostics.get(investigation.diagnostic_code) || diagnostics.get(investigation.required_action),
      profile: investigation.profile || null, declarations: investigation.declarations || {}, fields,
      sources: Array.isArray(investigation.sources) && investigation.sources.length ? investigation.sources.slice() : investigation.source ? [investigation.source] : [],
      sourceProfiles: Array.isArray(investigation.source_profiles) ? investigation.source_profiles : [],
      sourceDeclarations: Array.isArray(investigation.source_declarations) ? investigation.source_declarations : [],
      scientificContext: investigation.scientific_context || {},
      scientificSessionId: investigation.scientific_session_id || null, scientificGateName: investigation.gate || null,
      adaptive: investigation.workflow_mode === 'adaptive/1',
      adaptiveResearch: investigation.adaptive_research || null,
      singlecellTrial: investigation.singlecell_trial || null, operationPending: investigation.operation_pending === true,
      scientificGate: investigation.gate_options || null, scientificPlan: investigation.scientific_plan || null, planningSource: investigation.planning_source || null,
      capabilities: {dataExecution: Boolean(config && config.execution_allowed === true), modelExecution: Boolean(config && ((!config.scientific || config.scientific.enabled === false) ? config.model_access === true : config.scientific.model_execution_allowed === true)), autoAdvance: Boolean(config && config.scientific && config.scientific.enabled !== false && config.scientific.scientific_workflow_configured === true), offlinePlanning: Boolean(config && config.scientific && config.scientific.planning_mode === 'recorded_fixture')},
      input: investigation.source, selection: investigation.selection || null,
      jobState: state, analystProfile: investigation.analyst_profile || null, metrics: planSummary(state),
      inlineGate: {kind: gate, investigationId: investigation.id, revision: investigation.revision, jobId: investigation.job_id || null},
      artifacts: Array.isArray(investigation.artifacts) ? investigation.artifacts : [],
      matrixAudit, matrixIntake, readOnly: config?.project_catalog?.read_only === true,
      synthesis: matrixAudit || investigation.synthesis?.publication_status === 'withheld' ? null : investigation.synthesis || null,
      synthesisContinuation: matrixAudit ? null : investigation.synthesis_continuation || null, parentJob: investigation.parent_job || null,
      mode, scope
    };
  }
  const clarificationDrafts = new WeakMap();
  function renderClarification(container, model, onAction) {
    const scientificFields = model.fields.some(field => ['scientific_context', 'biological_independence'].includes(field?.scope)), requestFields = model.fields.length && model.fields.every(field => field.scope === 'request');
    const article = element('article', 'wc-card wc-clarification wc-question-card');article.append(cardHeader(requestFields ? 'Clarify your question' : scientificFields ? 'Scientific planning context' : 'A little more context', 'Needs information', 'warning'));
    article.append(element('p', '', model.matrixIntake ? 'Confirm the requested input details for this local diagnostic.' : requestFields ? 'Edit the question to name the outcome you want to examine. The attached files stay in this chat.' : scientificFields ? model.fields.some(field => field.scope === 'scientific_context' && field.field === 'data_classification') ? 'Classify the attached data from its source records, then choose which planning actions to authorize for this chat.' : 'Choose which planning actions to authorize for this chat. Supplied data and experimental facts remain recorded.' : model.sources.length > 1 ? 'Confirm the role and experimental details of each attached dataset separately.' : 'Confirm the experimental details needed to prepare a plan. Suggested columns come from this dataset.'));
    if (model.matrixIntake) {
      const retained = model.turns.filter(turn => (!turn.investigation_id || turn.investigation_id === model.id) && turn.kind === 'clarification').at(-1)?.content;
      appendLimitations(article, retained?.warnings);
      if (retained?.fact_provenance) article.append(createDisclosure('Observed and declared input facts', retained.fact_provenance));
    }
    const form = element('form', 'wc-clarification-form'), controls = [], contextControls = [], sourceControls = [], bulkControls = [], unitControls = [], requestControls = [];
    const reasons = new Map(), sharedReasons = new Map(), questionPages = [];
    for (const field of model.fields) {
      const reason = field && ['select', 'text', 'boolean'].includes(field.type) ? text(field.guidance || field.reason).trim() : '';
      if (reason) reasons.set(reason, (reasons.get(reason) || 0) + 1);
    }
    for (const [reason, count] of reasons) if (count > 1) sharedReasons.set(reason, 'clarify-' + String(model.id).replace(/[^a-zA-Z0-9_-]/g, '') + '-shared-note-' + sharedReasons.size);
    let draft = clarificationDrafts.get(container), updatePermissions = () => {};
    if (!draft || draft.id !== model.id) {draft = {id: model.id, values: {}, page: 0};clarificationDrafts.set(container, draft);}
    for (const field of model.fields) {
      if (!field || typeof field.field !== 'string' || !['select', 'text', 'boolean'].includes(field.type)) continue;
      const scientific = field.scope === 'scientific_context', perSource = field.scope === 'source_declarations', bulk = field.scope === 'bulk_episode_declarations', request = field.scope === 'request';
      const biologicalUnit = field.scope === 'biological_independence';
      if (request && field.field !== 'question') continue;
      if (perSource && (!Number.isInteger(field.source_index) || field.source_index < 0 || field.source_index >= model.sources.length)) continue;
      const scope = request || bulk || scientific || biologicalUnit || perSource ? field.scope : 'declarations';
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
      const retained = request ? {question: model.question} : bulk ? model.scientificContext.bulk_episode_declarations || {} : biologicalUnit ? model.scientificContext.biological_independence || {} : scientific ? model.scientificContext : perSource ? model.sourceDeclarations[field.source_index] || {} : model.declarations;
      const initial = Object.prototype.hasOwnProperty.call(draft.values, draftKey) ? draft.values[draftKey] : retained[key];
      if (field.type === 'boolean') input.checked = initial === true;else input.value = typeof initial === 'string' ? initial : '';
      const change = () => {draft.values[draftKey] = field.type === 'boolean' ? input.checked : input.value;updatePermissions();};
      input.addEventListener('change', change);input.addEventListener('input', change);
      if (field.type === 'boolean') {caption.className = 'check';caption.append(input);group.append(caption);}else group.append(caption, input);
      const reasonText = text(field.guidance || field.reason).trim();
      if (sharedReasons.has(reasonText)) input.setAttribute('aria-describedby', sharedReasons.get(reasonText));
      else if (reasonText) {const reason = element('p', 'wc-field-reason', reasonText);reason.setAttribute('id', id + '-reason');input.setAttribute('aria-describedby', id + '-reason');group.append(reason);}
      let fallback = null;
      if (bulk && field.options_by_condition && field.type === 'select') {
        fallback = element('input');fallback.type = 'text';fallback.maxLength = 200;
        fallback.hidden = true;fallback.disabled = true;
        fallback.setAttribute('aria-label', caption.textContent);
        fallback.addEventListener('input', () => {draft.values[draftKey] = fallback.value;});
        group.append(fallback);
      }
      const control = {field, input, draftKey, group, fallback};
      if (request) requestControls.push(control);else if (bulk) bulkControls.push(control);else if (biologicalUnit) unitControls.push(control);else if (scientific) contextControls.push(control);else if (perSource) sourceControls.push(control);else controls.push(control);
      form.append(group);
      questionPages.push({group, input, caption, field, label: caption.textContent, change});
    }
    for (const [reasonText, id] of sharedReasons) {const note = element('p', 'wc-field-reason wc-shared-field-reason', reasonText);note.setAttribute('id', id);form.append(note);}
    const contextControl = field => contextControls.find(control => control.field.field === field);
    const classification = contextControl('data_classification'), dataGrant = contextControl('allow_data_execution'), grant = contextControl('allow_model_execution'), advance = contextControl('auto_advance'), synthesisGrant = contextControl('auto_synthesis');
    const availability = element('p', 'wc-field-availability');availability.setAttribute('role', 'status');
    const classificationValue = () => classification ? classification.input.value : model.scientificContext.data_classification;
    const offlineAdvanceAllowed = () => model.capabilities.offlinePlanning && classificationValue() === 'synthetic';
    function classifiedForModels() {
      const value = classificationValue();
      return value === 'public' || value === 'synthetic';
    }
    updatePermissions = () => {
      for (const {field, input, group} of sourceControls) {
        if (!field.requires_source_role) continue;
        const role = sourceControls.find(control => control.field.field === 'role' && control.field.source_index === field.source_index);
        const enabled = role?.input.value === field.requires_source_role;
        input.disabled = !enabled || field.disabled === true;
        input.required = enabled;
        group.hidden = !enabled;
      }
      const condition = bulkControls.find(control => control.field.field === 'condition_column')?.input.value ||
        model.scientificContext.bulk_episode_declarations?.condition_column;
      for (const {field, input, fallback, draftKey} of bulkControls) {
        if (!field.options_by_condition || field.type !== 'select') continue;
        const retainedCondition = model.scientificContext.bulk_episode_declarations?.condition_column;
        const offered = field.options_by_condition?.[condition] ||
          (retainedCondition === condition && field.options);
        const options = Array.isArray(offered) ? offered : [];
        if (input.conditionBinding === condition) continue;
        const editable = Boolean(condition && !options.length && fallback);
        const selected = input.value, empty = element('option', '', 'Choose an option');empty.value = '';
        input.replaceChildren(empty, ...options.map(choice => {
          const option = element('option', '', text(choice.label) || choice.value);option.value = choice.value;return option;
        }));
        input.value = options.some(choice => choice.value === selected) ? selected : '';
        input.hidden = editable;input.disabled = editable;input.required = !editable && field.required === true;
        if (fallback) {
          fallback.hidden = !editable;fallback.disabled = !editable;fallback.required = editable && field.required === true;
          if (editable) fallback.value = typeof draft.values[draftKey] === 'string' ? draft.values[draftKey] : selected;
        }
        input.conditionBinding = condition;
      }
      const modelAllowed = model.capabilities.modelExecution && classifiedForModels();
      if (dataGrant) {
        dataGrant.input.disabled = dataGrant.field.disabled === true || !model.capabilities.dataExecution;
        if (dataGrant.input.disabled) {dataGrant.input.checked = false;draft.values[dataGrant.draftKey] = false;}
      }
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
    updatePermissions();
    if (contextControls.length) form.append(availability);
    const submit = element('button', 'wc-clarify-submit', requestFields ? 'Update question' : scientificFields ? 'Confirm planning context' : 'Confirm details and prepare plan');submit.type = 'submit';
    const revisedQuestion = requestControls.find(({field}) => field.field === 'question');
    if (requestFields && revisedQuestion) {
      const syncQuestion = () => {
        const value = revisedQuestion.input.value.trim();
        submit.disabled = !value || value === model.question.trim();
      };
      revisedQuestion.input.addEventListener('input', syncQuestion);
      revisedQuestion.input.addEventListener('change', syncQuestion);
      syncQuestion();
      if (model.sources.length > 1 && model.sources.every(path => /\.h5ad$/i.test(path))) {
        const suggestion = element('button', 'secondary', 'Draft a local cell-type comparison');
        suggestion.type = 'button';
        suggestion.addEventListener('click', () => {
          revisedQuestion.input.value = 'Compare stored cell-type proportions across the attached datasets using the celltypes column.';
          draft.values[revisedQuestion.draftKey] = revisedQuestion.input.value;
          syncQuestion();
          revisedQuestion.input.focus?.();
        });
        form.append(suggestion);
      }
    }
    form.append(submit);
    const paged = questionPages.length > 1 && model.sources.length === 1 && !contextControls.length && !unitControls.length;
    let showPage, missingAnswer;
    if (paged) {
      const pager = element('div', 'wc-clarification-pager'), chips = element('div', 'wc-answered-chips');
      const heading = element('div', 'wc-question-top'), progress = element('span', 'wc-question-step-badge'), hint = element('span');
      progress.setAttribute('aria-live', 'polite');heading.append(progress, hint);
      form.prepend?.(chips, heading);
      for (const page of questionPages) {
        const {field, input, group, caption, change} = page;
        const choices = Array.isArray(field.options) ? field.options.filter(option => typeof option?.value === 'string' && option.value) : [];
        if (field.type !== 'select' || !choices.length || choices.length > 9 || input.disabled) continue;
        const list = element('div', 'wc-option-list');
        list.setAttribute('role', 'group');list.setAttribute('aria-label', page.label);
        input.hidden = true;input.required = false;
        caption.removeAttribute?.('for');
        page.buttons = choices.map((option, index) => {
          const button = element('button', 'secondary wc-option-item');button.type = 'button';
          button.setAttribute('aria-keyshortcuts', String(index + 1));
          const copy = element('span', 'wc-option-main');
          copy.append(element('span', 'wc-option-label', (text(option.label) || option.value) + (option.recommended === true ? ' (Recommended)' : '')));
          if (text(option.reason)) copy.append(element('span', 'wc-option-desc', option.reason));
          button.append(element('kbd', 'wc-option-key', String(index + 1)), copy);
          button.addEventListener('click', () => {input.value = option.value;change();updateChoices();});
          list.append(button);return button;
        });
        const updateChoices = () => page.buttons.forEach((button, index) => {
          const selected = choices[index].value === input.value;
          button.setAttribute('aria-pressed', String(selected));
          button.className = 'secondary wc-option-item' + (selected ? ' is-selected' : '');
        });
        updateChoices();group.append(list);
      }
      const next = element('button', 'secondary', 'Next question');next.type = 'button';
      const back = element('button', 'secondary', 'Back');back.type = 'button';
      const error = element('p', 'wc-field-reason');error.setAttribute('role', 'status');
      const focusPage = page => (page.buttons?.find(button => button.getAttribute('aria-pressed') === 'true') || page.buttons?.[0] || page.input).focus();
      missingAnswer = page => page.buttons && page.field.required && !page.input.value;
      showPage = (index, focus) => {
        draft.page = index;
        const page = questionPages[index];
        questionPages.forEach(({group}, position) => {group.hidden = position !== index;});
        progress.textContent = 'Clarification ' + (index + 1) + ' of ' + questionPages.length;
        hint.textContent = page.buttons ? '1–' + page.buttons.length + ' to select · Enter to continue' : '';
        submit.hidden = index !== questionPages.length - 1;next.hidden = !submit.hidden;back.hidden = index === 0;
        error.textContent = '';chips.replaceChildren();
        questionPages.slice(0, index).forEach(({input, field, label}, position) => {
          const answer = field.type === 'boolean' ? input.checked ? 'Yes' : 'No' : (Array.isArray(field.options) && field.options.find(option => option?.value === input.value)?.label) || input.value;
          const chip = element('button', 'wc-answered-chip', label + (answer ? ' · ' + answer : ''));
          chip.type = 'button';chip.addEventListener('click', () => showPage(position, true));chips.append(chip);
        });
        if (focus) focusPage(page);
      };
      back.addEventListener('click', () => showPage(draft.page - 1, true));
      next.addEventListener('click', () => {
        const page = questionPages[draft.page];
        if (missingAnswer(page)) {error.textContent = 'Choose an option to continue.';focusPage(page);return;}
        if (typeof page.input.reportValidity !== 'function' || page.input.reportValidity()) showPage(draft.page + 1, true);
      });
      form.addEventListener('keydown', event => {
        if (event.isComposing || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || ['INPUT', 'SELECT', 'TEXTAREA'].includes(event.target.tagName)) return;
        const buttons = questionPages[draft.page].buttons;
        if (!buttons) return;
        const choice = /^[1-9]$/.test(event.key) && buttons[Number(event.key) - 1];
        if (choice) {event.preventDefault();choice.click();choice.focus();}
        if (event.key === 'Enter' && buttons.includes(event.target) && event.target.getAttribute('aria-pressed') === 'true') {
          event.preventDefault();if (submit.hidden) next.click();else submit.click();
        }
      });
      pager.append(back, next);form.append(pager, error);
      showPage(Math.min(draft.page || 0, questionPages.length - 1), false);
    }
    form.addEventListener('submit', event => {
      event.preventDefault();updatePermissions();
      if (submit.disabled) return;
      if (paged && draft.page !== questionPages.length - 1) return;
      if (paged) {
        const unanswered = questionPages.findIndex(missingAnswer);
        if (unanswered !== -1) {showPage(unanswered, true);return;}
      }
      if (typeof form.reportValidity === 'function' && !form.reportValidity()) return;
      const action = {type: 'clarify', investigationId: model.id, expectedRevision: model.revision};
      for (const {input} of requestControls) action.question = input.value;
      if (!requestFields && (controls.length || model.sources.length <= 1)) {
        const declarations = {...model.declarations};
        for (const {field, input} of controls) declarations[field.field] = field.type === 'boolean' ? input.checked === true : input.value;
        action.declarations = declarations;
      }
      if (!requestFields && (model.sources.length > 1 || sourceControls.length)) {
        const declarations = model.sources.map(() => ({}));
        for (const {field, input} of sourceControls) if (!input.disabled) declarations[field.source_index][field.field] = field.type === 'boolean' ? input.checked === true : input.value;
        action.source_declarations = declarations;
      }
      if (contextControls.length) {
        const context = {...model.scientificContext};
        for (const {field, input} of contextControls) context[field.field] = field.type === 'boolean' ? !input.disabled && input.checked === true : input.value;
        context.allow_model_execution = model.capabilities.modelExecution && ['public', 'synthetic'].includes(context.data_classification) && context.allow_model_execution === true;
        if (dataGrant || Object.prototype.hasOwnProperty.call(context, 'allow_data_execution')) context.allow_data_execution = model.capabilities.dataExecution && context.allow_data_execution === true;
        // Requested input-set planning is not a model-sharing or execution grant.
        const requestedAdvance = advance ? advance.input.checked === true : model.scientificContext.auto_advance === true;
        context.auto_advance = model.sources.length > 1 ? requestedAdvance : model.capabilities.autoAdvance && (context.allow_model_execution || model.capabilities.offlinePlanning && context.data_classification === 'synthetic') && context.auto_advance === true;
        if (synthesisGrant || Object.prototype.hasOwnProperty.call(context, 'auto_synthesis')) context.auto_synthesis = model.capabilities.autoAdvance && (context.allow_model_execution || model.capabilities.offlinePlanning && context.data_classification === 'synthetic') && context.auto_synthesis === true;
        action.scientificContext = context;
      }
      if (bulkControls.length) {
        const declarations = {...(model.scientificContext.bulk_episode_declarations || {})};
        for (const {field, input, fallback} of bulkControls) declarations[field.field] = field.type === 'boolean' ? input.checked === true : fallback && !fallback.disabled ? fallback.value : input.value;
        action.scientificContext = {...model.scientificContext, ...(action.scientificContext || {}), bulk_episode_declarations: declarations};
      }
      if (unitControls.length) {
        const unit = {};
        for (const {field, input} of unitControls) unit[field.field] = field.type === 'boolean' ? input.checked === true : input.value.trim();
        if ((!unit.unit_column || !unit.evidence) && (unit.unit_column || unit.evidence || unit.independent)) {
          availability.textContent = 'Choose a biological unit column and provide its evidence, or leave all unit fields blank.';
          if (!contextControls.length) form.append(availability);
          return;
        }
        action.scientificContext = {...(action.scientificContext || {}), biological_independence: unit.unit_column ? unit : null};
      }
      if (typeof onAction === 'function') onAction(action);
    });
    article.append(form);return article;
  }
  function renderEpisodeClarification(container, model, onAction) {
    const card = element('article', 'wc-card wc-clarification');card.setAttribute('data-inline-gate', 'episode_clarification');
    card.append(cardHeader('The analyst needs experimental context', 'Needs information', 'warning'));
    const request = model.jobState.information_request;
    const message = typeof request === 'string' ? request : text(request && request.message);
    if (message) card.append(element('p', 'wc-message-body', message));
    const form = element('form', 'wc-clarification-form'), id = 'episode-answer-' + String(model.id).replace(/[^a-zA-Z0-9_-]/g, '');
    const draftId = model.id + ':episode:' + (text(model.jobState.handoff && model.jobState.handoff.root_hash) || JSON.stringify(request));
    let draft = clarificationDrafts.get(container);
    if (!draft || draft.id !== draftId) {draft = {id: draftId, values: {}};clarificationDrafts.set(container, draft);}
    const caption = element('label', '', 'Answer from the experimental records');caption.setAttribute('for', id);
    const answer = element('textarea');answer.setAttribute('id', id);answer.setAttribute('name', 'episode_answer');answer.required = true;answer.maxLength = 8000;answer.value = draft.values.episode_answer || '';
    answer.addEventListener('input', () => {draft.values.episode_answer = answer.value;});
    const confirmation = element('label', 'check'), checkbox = element('input');checkbox.type = 'checkbox';checkbox.required = true;checkbox.setAttribute('name', 'preserves_frozen_inputs_and_settings');checkbox.checked = draft.values.episode_preserves === true;
    checkbox.addEventListener('change', () => {draft.values.episode_preserves = checkbox.checked;});
    confirmation.append(checkbox, element('span', '', 'This answer preserves the frozen inputs, biological units, contrast and analysis settings.'));
    form.append(caption, answer, confirmation, element('p', 'wc-field-reason', 'Changing those declarations requires a linked follow-up. Continuing allows the analyst to use this answer within the retained episode.'));
    const submit = element('button', 'wc-clarify-submit', 'Answer and continue analysis');submit.type = 'submit';form.append(submit);
    form.addEventListener('submit', event => {event.preventDefault();if (typeof form.reportValidity === 'function' && !form.reportValidity()) return;if (!answer.value.trim() || !checkbox.checked) return;if (typeof onAction === 'function') onAction({type: 'clarify_episode', investigationId: model.id, expectedRevision: model.revision, response: {message: answer.value.trim(), preserves_frozen_inputs_and_settings: true}});});
    card.append(form);return card;
  }
  const scientificRequestKeys = new WeakMap();
  function renderScientificGate(container, model, onAction) {
    const options = model.scientificGate || {}, execution = model.scientificGateName === 'execution_review';
    const card = element('article', 'wc-card wc-scientific-gate');card.setAttribute('data-inline-gate', model.inlineGate.kind);
    card.append(cardHeader(execution ? 'Review trial execution' : 'Review the scientific plan', 'Operator decision', 'warning'));
    const projectionMatches = options.gate === model.scientificGateName && options.session_id === model.scientificSessionId && Number.isInteger(options.workspace_revision);
    const canSubmit = !model.readOnly && projectionMatches && options.can_submit === true;
    const startupExecution = options.startup_data_execution_allowed === true && model.capabilities.dataExecution;
    const choices = Array.isArray(execution ? options.declared_trials : options.proposals) ? execution ? options.declared_trials : options.proposals : [];
    const eligible = choice => execution ? choice && choice.status === 'declared' && choice.eligible !== false : choice && choice.eligible === true;
    const identity = choice => text(execution ? choice.experiment_id : choice.draft_id);
    const blocked = Array.isArray(options.blocked_reasons) ? options.blocked_reasons : [];
    if (model.readOnly) card.append(element('p', 'wc-gate-blocked', 'Read-only project'));
    if (!projectionMatches) card.append(element('p', 'wc-gate-blocked', 'Review details are not yet available. Refresh the chat evidence.'));
    for (const reason of blocked) card.append(element('p', 'wc-gate-blocked', text(reason) || text(reason && reason.reason) || text(reason && reason.message)));
    if (execution && !startupExecution) card.append(element('p', 'wc-gate-blocked', 'Data execution is disabled for this service.'));
    const firstChoice = choices.find(eligible) || choices[0];
    const summaryGrid = element('div', 'proposal-summary');
    const summarize = choice => {
      const spec = choice?.spec && typeof choice.spec === 'object' ? choice.spec : {};
      const params = spec.params && typeof spec.params === 'object' ? spec.params : {};
      const stages = Array.isArray(params.stages) ? params.stages.join(' & ') : '';
      const populations = Array.isArray(params.populations) ? params.populations.map(population => text(population.label)).filter(Boolean).join(' vs ') : '';
      const tile = (labelText, valueText) => {
        const div = element('div');
        div.append(element('span', '', labelText), element('strong', '', valueText));
        return div;
      };
      summaryGrid.replaceChildren(
        tile('1. Contrast & Scope', [populations, stages].filter(Boolean).join(' · ') || 'See frozen trial specification'),
        tile('2. Biological Replicates', text(params.biological_unit_column) || 'See recorded design diagnostics'),
        tile('3. Estimator & FDR', text(params.procedure) || 'See frozen trial specification'),
        tile('4. Qualification Limits', 'Single-method run · comparative & clinical claims held')
      );
    };
    if (firstChoice && !execution) {
      card.append(element('p', 'wc-gate-intro', 'Review the frozen scope and record your decision. Starting a trial requires a separate execution gate.'), summaryGrid);
      summarize(firstChoice);
    }
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
      if (!execution) summarize(choice || firstChoice);
      if (!choice) return;
      preview.append(createDisclosure('Frozen trial specification and resource limits', choice.spec || {}));
      if (choice.diagnostics) preview.append(createDisclosure('Trial design diagnostics', choice.diagnostics));
      for (const reason of Array.isArray(choice.blocked_reasons) ? choice.blocked_reasons : []) preview.append(element('p', 'wc-gate-blocked', text(reason)));
    });
    form.append(selectLabel, select);
    const reviewerLabel = element('label', '', 'Operator label');reviewerLabel.setAttribute('for', formId + '-reviewer');
    const reviewer = element('input');reviewer.setAttribute('id', formId + '-reviewer');reviewer.setAttribute('name', 'reviewer');reviewer.required = true;reviewer.maxLength = 200;
    const rationaleLabel = element('label', '', 'Review rationale');rationaleLabel.setAttribute('for', formId + '-rationale');
    const rationale = element('textarea');rationale.setAttribute('id', formId + '-rationale');rationale.setAttribute('name', 'rationale');rationale.required = true;rationale.maxLength = 4000;
    const operator = element('details', 'wc-operator');
    const operatorBadge = element('summary', 'wc-operator-badge', 'Set operator label');
    operator.append(operatorBadge, reviewerLabel, reviewer);
    form.append(operator, rationaleLabel, rationale);
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
    try {reviewer.value = text(root.localStorage?.getItem('devbench.operator_id')).slice(0, 200);} catch (_) {}
    const remembered = reviewer.value;
    const updateOperator = () => {
      const value = reviewer.value.trim();
      operatorBadge.textContent = value ? 'Operator: ' + value + (value === remembered ? ' · Remembered' : '') : 'Set operator label';
    };
    operator.open = !remembered;updateOperator();reviewer.addEventListener('input', updateOperator);
    const rationalePreset = element('button', 'secondary', 'Use scope review prompt');
    rationalePreset.type = 'button';
    rationalePreset.addEventListener('click', () => {
      if (!rationale.value.trim()) rationale.value = 'I reviewed the frozen scope and recorded the applicable confirmations below.';
      rationale.focus();
    });
    form.append(rationalePreset);
    form.append(submit, status, preview);
    if (execution && !startupExecution) {
      selectLabel.hidden = select.hidden = rationaleLabel.hidden = rationale.hidden = rationalePreset.hidden = true;
      for (const {input} of confirmations) if (input.parentNode) input.parentNode.hidden = true;
      if (dataGrant.parentNode) dataGrant.parentNode.hidden = true;
    }
    form.addEventListener('submit', event => {
      event.preventDefault();
      if (!canSubmit || execution && !startupExecution) {status.textContent = 'This gate is blocked. Review the recorded reasons before continuing.';return;}
      const choice = choices.find(row => identity(row) === select.value);
      if (!choice || !eligible(choice) || !choice.spec || typeof choice.spec !== 'object') {status.textContent = 'Choose an eligible recorded trial.';return;}
      if (!text(reviewer.value).trim() || !text(rationale.value).trim() || rationale.value.includes('[describe which assumptions and limits you checked]') || confirmations.some(row => row.input.checked !== true)) {status.textContent = 'Provide an operator label, rationale and every required confirmation.';return;}
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
      try {root.localStorage?.setItem('devbench.operator_id', operatorReview.reviewer);} catch (_) {}
      if (typeof onAction === 'function') onAction({type: 'scientific', request, operatorReview});
    });
    card.append(form);
    if (model.scientificPlan) card.append(createDisclosure('Scientific plan', model.scientificPlan));
    if (options.review_evidence) card.append(createDisclosure('Recorded scientific review', options.review_evidence, false));
    return card;
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
        const summary = Number.isFinite(facts.cells) && Number.isFinite(facts.genes) ? facts.cells.toLocaleString('en-US') + ' observations × ' + facts.genes.toLocaleString('en-US') + ' features' : text(facts.file_type) ? keyLabel(facts.file_type) + ' file' : 'File facts available';
        item.append(element('p', 'wc-audit-summary', summary), createDisclosure('File evidence', source));list.append(item);
      }
      card.append(list, createDisclosure('Cross-dataset unknowns', profile.unknowns || []));
      if (observed.sources.length > 8) card.append(createDisclosure('Full input-set evidence', profile));
      return card;
    }
    const input = text(profile && profile.filename) || text(profile && profile.source) || text(profile && profile.input && profile.input.source);
    const summary = [input ? input.split('/').pop() : '', Number.isFinite(observed.cells) && Number.isFinite(observed.genes) ? observed.cells.toLocaleString('en-US') + ' observations × ' + observed.genes.toLocaleString('en-US') + ' features' : ''].filter(Boolean).join(' · ');
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
  function evidenceTable(rows, columns) {
    if (!Array.isArray(rows) || !rows.length) return null;
    const names = Array.isArray(columns) && columns.every(name => typeof name === 'string') ? columns.slice(0, 16) : Array.from(new Set(rows.slice(0, 50).flatMap(row => row && typeof row === 'object' && !Array.isArray(row) ? Object.keys(row) : []))).slice(0, 16);
    if (!names.length) return null;
    const host = element('div', 'wa-table-scroll');host.setAttribute('tabindex', '0');host.setAttribute('role', 'region');host.setAttribute('aria-label', 'Recorded scientific evidence table');
    const table = element('table', 'wa-artifact-table'), header = element('tr');
    for (const name of names) {const cell = element('th', '', keyLabel(name));cell.setAttribute('scope', 'col');header.append(cell);}
    const head = element('thead');head.append(header);table.append(head);const body = element('tbody');
    for (const row of rows.slice(0, 50)) {
      const line = element('tr');for (let i = 0; i < names.length; i++) {
        const value = Array.isArray(row) ? row[i] : row && row[names[i]];
        line.append(element('td', '', value === null ? 'Not estimated' : value === undefined ? 'Not supplied' : typeof value === 'object' ? JSON.stringify(value) : String(value)));
      }body.append(line);
    }
    table.append(body);host.append(table);
    if (rows.length > 50) host.append(element('p', 'wc-truncated', 'Showing 50 of ' + rows.length + ' recorded rows; inspect the complete artifact for all evidence.'));
    return host;
  }
  function appendSourceBinding(card, binding) {
    const source = text(binding.source).split('/').pop() || 'Source not recorded';
    const members = Array.isArray(binding.sources) ? binding.sources : [];
    const sourceSet = binding.input_kind === 'source_set' || members.length > 1, hash = sourceSet ? text(binding.input_set_sha256) || text(binding.source_sha256) : text(binding.source_sha256), pending = binding.source_binding_status === 'pending';
    card.append(element('p', 'wc-answer-source', (sourceSet ? 'Source set: ' + members.length + ' files' : 'Source: ' + source) + (pending ? ' · Source identity pending' : hash ? ' · SHA-256 ' + hash.slice(0, 12) : ' · Source hash not recorded')));
    if (members.length > 1) for (const member of members) card.append(element('p', 'wc-answer-source', 'Member: ' + text(member.source) + ' · SHA-256 ' + (text(member.sha256).slice(0, 12) || 'not recorded')));
  }
  function appendEvidenceTables(card, tables, identity, pages, binding, nullLabel) {
    const count = value => Number.isFinite(value) ? value.toLocaleString('en-US') : text(value) || 'not recorded';
    for (const [index, item] of (Array.isArray(tables) ? tables : []).entries()) {
      if (!item || typeof item !== 'object') continue;
      const columns = Array.isArray(item.columns) ? item.columns.filter(name => typeof name === 'string') : [], rows = Array.isArray(item.rows) ? item.rows : [];
      const caption = text(item.table_title) || text(item.caption) || 'Stored dataset values', host = element('div', 'wa-table-scroll wc-answer-table');
      const tableId = 'answer-table-' + identity + '-' + index;
      host.setAttribute('id', tableId);
      host.setAttribute('tabindex', '0');host.setAttribute('role', 'region');host.setAttribute('aria-label', caption);
      const table = element('table', 'wa-artifact-table');table.append(element('caption', '', caption));
      const head = element('thead'), header = element('tr');
      for (const name of columns.slice(0, 16)) {const cell = element('th', '', name);cell.setAttribute('scope', 'col');header.append(cell);}
      head.append(header);table.append(head);const body = element('tbody');
      const pageCount = Math.max(1, Math.ceil(rows.length / 50));let page = Math.min(pages?.get(tableId) || 0, pageCount - 1);
      const navigation = element('div', 'wc-table-paging'), previous = element('button', 'secondary', 'Previous rows'), next = element('button', 'secondary', 'Next rows'), position = element('span', 'wc-answer-counts'), clipped = element('p', 'wc-truncated');
      position.setAttribute('role', 'status');position.setAttribute('aria-live', 'polite');
      for (const [button, suffix, delta] of [[previous, 'previous', -1], [next, 'next', 1]]) {button.type = 'button';button.setAttribute('id', tableId + '-' + suffix);button.setAttribute('aria-label', button.textContent + ' of ' + caption);button.addEventListener('click', () => {page = Math.max(0, Math.min(pageCount - 1, page + delta));pages?.set(tableId, page);renderRows();});}
      function renderRows() {
        body.replaceChildren();const start = page * 50, end = Math.min(rows.length, start + 50);
        for (const row of rows.slice(start, end)) {
          const line = element('tr');
          for (let column = 0; column < Math.min(columns.length, 16); column++) {
            const value = Array.isArray(row) ? row[column] : undefined;
            line.append(element('td', '', value === null ? nullLabel : value === undefined ? 'Not supplied' : typeof value === 'object' ? 'Unsupported value' : String(value)));
          }
          body.append(line);
        }
        previous.disabled = page === 0;next.disabled = page === pageCount - 1;
        position.textContent = 'Rows ' + (rows.length ? start + 1 : 0) + '–' + end + ' of ' + rows.length;
        clipped.textContent = 'Showing ' + (end - start) + ' of ' + rows.length + ' rows and ' + Math.min(16, columns.length) + ' of ' + columns.length + ' columns. ' + (pageCount > 1 ? 'Use the row controls for other returned values. ' : '') + 'The exact returned result is retained in evidence.';
      }
      renderRows();
      table.append(body);host.append(table);
      if (text(item.source_sha256)) {
        const members = (Array.isArray(binding?.sources) ? binding.sources : []).filter(member => member.sha256 === item.source_sha256);
        const path = members.some(member => member.source === item.source) ? item.source : members.length === 1 ? members[0].source : '';
        host.append(element('p', 'wc-answer-source', (path ? 'Table source: ' + path : 'Table source content') + ' · SHA-256 ' + item.source_sha256.slice(0, 12)));
      }
      if (pageCount > 1) {navigation.append(previous, position, next);host.append(navigation);}
      if (!rows.length) host.append(element('p', 'wc-field-reason', 'No rows were returned.'));
      host.append(element('p', 'wc-answer-counts', 'Denominator: ' + count(item.denominator) + ' · Missing: ' + count(item.missing_count)));
      if (item.truncated === true) host.append(element('p', 'wc-truncated', 'Source result is truncated; the returned rows do not cover the complete result.'));
      if (rows.length > 50 || columns.length > 16) host.append(clipped);
      card.append(host);
    }
  }
  function appendLimitations(card, limitations) {
    if (Array.isArray(limitations) && limitations.length) {
      const list = element('ul', 'wc-answer-limitations');card.append(element('h4', '', 'Limitations'));
      for (const limitation of limitations) if (typeof limitation === 'string') list.append(element('li', '', limitation));
      card.append(list);
    }
  }
  function appendMatrixPca(card, content) {
    const plot = content.plot, renderer = root.BulkScientificFigure;
    if (plot?.kind !== 'batch_pca' || !Array.isArray(plot.variance_ratio)) return;
    if (plot.variance_ratio.length !== 2) {
      card.append(element('p', 'wc-answer-source', 'One component is available in the PCA scores table.'));
      return;
    }
    const title = 'Descriptive PCA of expression by batch';
    const rows = content.tables?.[plot.table_index]?.rows || [];
    try {
      const axes = Object.fromEntries(['x', 'y'].map((axis, index) => [axis, {
        label: 'PC' + (index + 1) + ' (' + renderer.formatNumber(plot.variance_ratio[index] * 100) + '% variance)',
        unit: 'PCA score', transform: 'identity',
      }]));
      const numericRows = rows.map(([profile, batch, x, y]) => ({
        entity_id: profile, group_id: batch, x: {status: 'exact', value: x}, y: {status: 'exact', value: y},
      }));
      const model = renderer.prepareNumeric({
        format: 'devbench.figure/2', figure_id: title, plot_kind: 'sample_scatter',
      }, {
        format: 'devbench.figure-data/2', source_ref: {sha256: content.binding?.result_ref},
        data: {format: 'devbench.numeric-plot-source/2', plot_kind: 'sample_scatter', entity_kind: 'sample',
          axes, rows: numericRows, membership: numericRows.map(row => row.entity_id)},
      });
      const figure = element('figure', 'wc-matrix-plot'), image = element('img');
      image.setAttribute('alt', title);
      image.setAttribute('src', 'data:image/svg+xml,' + encodeURIComponent(renderer.toSVG(model)));
      const sampling = plot.sampling === 'evenly_spaced_in_expression_order' ? ' · evenly spaced in expression order' : '';
      figure.append(image, element('figcaption', 'wc-answer-source', rows.length + ' of ' +
        plot.source_sample_count + ' observed profiles · ' + plot.gene_count + ' genes in PCA' + sampling));
      card.append(figure);
    } catch (_) {
      card.append(element('p', 'wc-answer-source', 'The PCA plot is unavailable. See the retained scores table.'));
    }
  }
  function appendDatasetAnswer(card, content, identity, pages) {
    const binding = content.binding || {}, population = binding.population || {};
    if (text(content.message)) card.append(element('p', 'wc-message-body', content.message));
    appendSourceBinding(card, binding);
    let populationLabel = population.kind === 'all_observations' ? 'All observations' : population.kind === 'selection_context' ? 'Selection context' + (Number.isSafeInteger(population.selected_observation_n) ? ': ' + population.selected_observation_n.toLocaleString('en-US') + ' saved observations' : '') : 'Population not recorded';
    if (population.scope === 'full_source_metadata') {
      populationLabel += Array.isArray(content.tables) && content.tables.some(table => table.source_ref === 'layers/counts')
        ? ' · stored count layer and observation labels' : ' · full-source metadata';
    }
    if (population.scope === 'full_source_matrix') populationLabel += ' · full-source matrix';
    card.append(element('p', 'wc-answer-population', populationLabel));
    if (Array.isArray(content.matrix_metrics) && content.matrix_metrics.length) {
      card.append(metricGrid(content.matrix_metrics.map(metric => ({
        label: text(metric.label), value: String(metric.value ?? 'Unavailable'),
      }))));
    }
    appendMatrixPca(card, content);
    appendEvidenceTables(card, content.tables, identity, pages, binding, content.matrix_audit_id ? 'Not estimated' : 'Missing');
    if (Array.isArray(content.facts) && content.facts.length) {
      const facts = element('dl', 'wc-kv-grid wc-answer-facts');
      for (const fact of content.facts.slice(0, 20)) {
        if (!fact || typeof fact !== 'object') continue;
        const origins = {observed: 'Observed', user_declared: 'User declared', inferred: 'Inferred', unknown: 'Unknown'};
        const value = element('dd');value.append(structuredValue(fact.value, 0, {rows: 20}));
        facts.append(element('dt', '', (text(fact.name) || 'Fact') + ' · ' + (origins[fact.origin] || 'Origin not recorded')), value);
      }
      card.append(facts);
      if (content.facts.length > 20) card.append(element('p', 'wc-truncated', 'Showing 20 of ' + content.facts.length + ' facts; all returned facts are retained in answer evidence.'));
    }
    appendLimitations(card, content.limitations);
    card.append(createDisclosure('Exact answer evidence', content));
  }
  function appendSinglecellEvidence(card, content, identity, pages, artifacts, onAction, populationEvidence) {
    const summary = content.summary && typeof content.summary === 'object' ? content.summary : null;
    const isContrastKPI = content.tool_name === 'scrna_population_contrast' && summary && summary.attempted_hypothesis_count;
    if (isContrastKPI) {
      const est = summary.estimated_hypothesis_count || 0, att = summary.attempted_hypothesis_count || 1;
      const unest = summary.not_estimable_hypothesis_count;
      card.append(element('h3', 'wc-results-headline', est + ' of ' + att + ' stage-specific hypotheses estimated'));
      const population = content.binding?.population;
      const fittedAnswer = text(populationEvidence?.direct_answer);
      if (fittedAnswer) card.append(element('p', 'wc-answer-population', fittedAnswer));
      else if (text(population?.biological_unit_column)) card.append(element('p', 'wc-answer-population', 'Within-stage estimates · Biological unit column: ' + population.biological_unit_column));
      const kpi = element('div', 'wc-kpi-grid');
      const tile1 = element('div', 'wc-kpi-tile'), tile2 = element('div', 'wc-kpi-tile'), tile3 = element('div', 'wc-kpi-tile'), tile4 = element('div', 'wc-kpi-tile');
      tile1.append(element('span', 'wc-kpi-label', 'Hypotheses Estimated'), element('span', 'wc-kpi-val', est + ' / ' + att));
      tile2.append(element('span', 'wc-kpi-label', 'Capture Units'), element('span', 'wc-kpi-val', Number.isInteger(summary.declared_biological_unit_count) ? summary.declared_biological_unit_count + ' declared units' : 'Unavailable'));
      tile3.append(element('span', 'wc-kpi-label', 'Design Identifiability'), element('span', 'wc-kpi-val', Number.isInteger(summary.rank) && Number.isInteger(summary.residual_df) ? 'Rank ' + summary.rank + ' · residual df ' + summary.residual_df : 'See diagnostics'));
      tile4.append(element('span', 'wc-kpi-label', 'Withheld / Unestimable'), element('span', 'wc-kpi-val', Number.isInteger(unest) ? String(unest) : 'Unavailable'));
      kpi.append(tile1, tile2, tile3, tile4);
      const segWrap = element('div', 'wc-seg-bar-wrap');
      const segLabels = element('div', 'wc-seg-bar-labels');
      segLabels.append(element('span', '', est + ' estimated · BH correction applied'), element('span', '', Number.isInteger(unest) ? unest + ' unestimable' : ''));
      const segBar = element('div', 'wc-seg-bar'), segFill = element('div', 'wc-seg-fill');
      segFill.style.width = Math.min(100, Math.round((est / att) * 100)) + '%';
      segBar.append(segFill);
      segWrap.append(segLabels, segBar);
      card.append(kpi, segWrap);
      const refs = Array.isArray(content.artifact_refs) ? content.artifact_refs : [];
      const resultId = populationEvidence?.trials?.[0]?.result_artifact_id;
      if (resultId && artifacts.some(artifact => artifact.artifact_id === resultId)) {
        card.append(actionButton({type: 'artifact', view: 'evidence', artifactId: resultId, label: 'Inspect the complete fit'}, onAction, 'secondary'));
      } else if (refs.some(ref => artifacts.some(artifact => artifact.artifact_id === ref.artifact_id))) {
        card.append(actionButton({type: 'navigate', view: 'evidence', label: 'Open full result artifacts'}, onAction));
      }
      const details = element('details', 'wc-disclosure');
      details.append(element('summary', '', 'Stage contrast tables, replicate profiles & diagnostics'));
      const detailsBody = element('div', 'wc-activity-log');
      if (text(content.message)) detailsBody.append(element('p', 'wc-message-body', content.message));
      appendSourceBinding(detailsBody, content.binding || {});
      appendEvidenceTables(detailsBody, content.tables, identity, pages, content.binding, 'Not estimated');
      appendLimitations(detailsBody, content.limitations);
      if (refs.length) detailsBody.append(element('p', 'wc-field-reason', refs.length + ' retained artifact' + (refs.length === 1 ? '' : 's') + ' · Exact identities are in the evidence.'));
      detailsBody.append(createDisclosure('Complete execution counters & diagnostics', summary));
      detailsBody.append(createDisclosure('Exact single-cell evidence', content));
      details.append(detailsBody);
      card.append(details);
      return;
    }
    if (text(content.message)) card.append(element('p', 'wc-message-body', content.message));
    appendSourceBinding(card, content.binding || {});
    const population = content.binding?.population;
    if (population && text(population.scope)) card.append(element('p', 'wc-answer-population', 'Population scope: ' + population.scope.replaceAll('_', ' ') + (text(population.biological_unit_column) ? ' · Unit column: ' + population.biological_unit_column : '')));
    if (summary) card.append(structuredValue(content.summary, 0, {rows: 48}));
    appendEvidenceTables(card, content.tables, identity, pages, content.binding, 'Not estimated');
    appendLimitations(card, content.limitations);
    const refs = Array.isArray(content.artifact_refs) ? content.artifact_refs : [];
    if (refs.length) card.append(element('p', 'wc-field-reason', refs.length + ' retained artifact' + (refs.length === 1 ? '' : 's') + ' · Exact identities are in the evidence.'));
    if (refs.some(ref => artifacts.some(artifact => artifact.artifact_id === ref.artifact_id))) card.append(actionButton({type: 'navigate', view: 'evidence', label: 'Open full result artifacts'}, onAction));
    card.append(createDisclosure('Exact single-cell evidence', content));
  }
  function descriptionStatusCard(model, onAction) {
    const operation = model.descriptionOperation;
    if (!operation || operation.status === 'completed' && !model.request?.remaining_action) return null;
    const status = FAILURES.has(model.status) && ['queued', 'running', 'cancel_requested'].includes(operation.status) ? model.status === 'recovery_required' ? 'recovery_required' : model.status === 'cancelled' ? 'cancelled' : 'failed' : operation.status, names = {queued: 'Description queued', running: 'Reading saved dataset information', cancel_requested: 'Cancellation requested', cancelled: 'Description cancelled', failed: 'Description failed', completed: 'Descriptive part available', recovery_required: 'Description recovery requires review'};
    const messages = {queued: 'The local description is queued.', running: 'The local reader is inspecting the bound source.', cancel_requested: 'Cancellation was requested. The reader has not yet confirmed it stopped.', cancelled: 'The local description stopped. Retained events remain available.', failed: 'The local description failed. Inspect the retained error and source evidence.', completed: 'The descriptive part is available. The remaining request has its own requirements.', recovery_required: 'Reader liveness is unknown. Retained activity does not establish an active worker; inspect recovery evidence before starting another operation.'};
    const card = element('article', 'wc-card wc-description-status');card.setAttribute('role', 'status');card.setAttribute('aria-live', 'polite');
    card.append(cardHeader(names[status] || 'Description status', '', 'neutral'), element('p', 'wc-message-body', messages[status] || 'Inspect the recorded description lifecycle.'));
    if (status === 'failed') {
      const error = model.turns.filter(turn => (!turn.investigation_id || turn.investigation_id === model.id) && turn.kind === 'error').at(-1)?.content;
      const reason = failureSummary(text(error?.message) || text(error?.reason));
      if (reason) card.append(element('p', 'wc-message-body', reason));
    }
    if (model.request?.remaining_action) card.append(element('p', 'wc-field-reason', 'Remaining request: ' + model.request.remaining_action.replaceAll('_', ' ') + ' · ' + label(model.status)));
    if (status === 'queued' || status === 'running') {
      const cancel = element('button', 'secondary wc-cancel-description', 'Cancel description');cancel.type = 'button';cancel.setAttribute('id', 'cancel-description-' + model.id);
      cancel.addEventListener('click', () => {if (typeof onAction === 'function') onAction({type: 'cancel', investigationId: model.id, expectedRevision: model.revision});});card.append(cancel);
    }
    return card;
  }
  function scopedScientificStatusCard(model, onAction) {
    if (!model.singlecellTrial || !model.scientificSessionId || model.inlineGate.jobId) return null;
    const stopping = model.status === 'cancelling', terminal = FAILURES.has(model.status) || model.status === 'completed' || model.status.startsWith('completed_');
    const pending = model.operationPending || ACTIVE.has(model.status);
    if (!pending && !FAILURES.has(model.status)) return null;
    const card = element('article', 'wc-card wc-scientific-status');card.setAttribute('role', 'status');card.setAttribute('aria-live', 'polite');
    card.append(cardHeader(stopping ? 'Stopping scientific operation' : model.status === 'cancelled' ? 'Scientific operation cancelled' : 'Scientific operation', label(model.status)));
    card.append(element('p', 'wc-message-body', stopping ? 'Cancellation was requested. The operation has not yet confirmed it stopped.' : terminal ? 'The retained status and available evidence remain visible below.' : 'The scoped scientific operation has a current recorded request. Stop it to request cancellation; retained evidence is preserved.'));
    if (pending && !terminal && !stopping) {
      const stop = element('button', 'secondary', 'Stop scientific operation');stop.type = 'button';stop.setAttribute('id', 'stop-scientific-' + model.id);
      stop.addEventListener('click', () => {if (typeof onAction === 'function') onAction({type: 'cancel', investigationId: model.id, expectedRevision: model.revision});});card.append(stop);
    }
    return card;
  }
  function synthesisStatusLabel(continuation) {
    if (['failed', 'scientific_refusal', 'recovery_required'].includes(continuation.status)) return 'Conclusion interrupted';
    const active = continuation.status === 'active', node = continuation.next_node;
    const pendingLabels = {verify: 'Fit complete · Verification pending', recommend: 'Recommendation pending', conclusion_review: 'Conclusion review pending'};
    const activeLabels = {verify: 'Verifying saved results', recommend: 'Synthesizing findings', conclusion_review: 'Reviewing conclusion'};
    return (active ? activeLabels : pendingLabels)[node] || 'Conclusion';
  }
  function synthesisContinuationCard(model, onAction, showReviewLink) {
    const continuation = model.synthesisContinuation;
    if (!continuation || ['completed', 'abstained'].includes(continuation.status)) return null;
    const active = continuation.status === 'active', node = continuation.next_node;
    const card = element('article', 'wc-card wc-synthesis-continuation');
    card.append(cardHeader(synthesisStatusLabel(continuation), active ? 'Active' : keyLabel(continuation.status)));
    const diagnosticNotice = {
      failed: 'The trial stopped with a recorded failure.',
      scientific_refusal: 'The trial stopped with a recorded scientific refusal.',
      recovery_required: 'The trial stopped and requires recovery.'
    }[continuation.status];
    if (diagnosticNotice) {
      card.append(element('p', 'wc-gate-blocked', diagnosticNotice));
      if (continuation.reason) card.append(element('p', '', continuation.reason));
      if (continuation.review?.rationale) card.append(element('p', '', continuation.review.rationale));
      if (continuation.review?.missing_evidence) card.append(element('p', 'wc-gate-blocked', continuation.review.missing_evidence));
      if (continuation.required_action) card.append(element('p', '', keyLabel(continuation.required_action)));
      card.append(actionButton({type: 'navigate', view: 'evidence', label: 'Inspect diagnostic receipts'}, onAction));
      return card;
    }
    if (continuation.population_evidence) {
      appendAdaptiveConclusion(card, continuation.population_evidence, onAction, showReviewLink);
      const reviewStatus = continuation.status === 'review_required'
        ? 'Scientific review is required. The fitted estimates below the exploratory threshold remain available as scoped evidence.'
        : 'Review status: ' + keyLabel(continuation.status) + '.';
      card.append(element('p', 'wc-field-reason', reviewStatus));
      if (!continuation.eligible) return card;
    }
    if (node && node !== 'verify' && !active) card.append(element('p', '', 'Verified results · Conclusion pending'));
    card.append(element('p', '', 'The fit is saved. Verification and conclusion review use the retained results.'));
    if (continuation.reason) card.append(element('p', 'wc-gate-blocked', continuation.reason));
    if (continuation.review?.rationale) card.append(element('p', '', continuation.review.rationale));
    if (continuation.review?.missing_evidence) card.append(element('p', 'wc-gate-blocked', continuation.review.missing_evidence));
    if (continuation.required_action) card.append(element('p', '', keyLabel(continuation.required_action)));
    if (!continuation.population_evidence && continuation.steps?.length) card.append(actionButton({type: 'navigate', view: 'evidence', label: 'Inspect verification and review receipts'}, onAction));
    if (!continuation.eligible) return card;
    const disclosure = element('details', 'wc-evidence-disclosure');
    disclosure.append(element('summary', '', 'Review and continue to conclusion'));
    const form = element('form', 'wc-inline-gate');
    form.append(element('p', '', 'Data classification: ' + keyLabel(continuation.data_classification)
      + '. Trials: ' + continuation.trials.join(', ') + '. Next: ' + keyLabel(node) + '.'));
    form.append(createDisclosure('Bound result artifacts', continuation.evidence_artifacts));
    const scope = continuation.model_scope;
    form.append(element('p', '', scope === 'external'
      ? 'Verification uses saved results. Recommendation and conclusion review may disclose retained evidence to the configured model service.'
      : scope === 'recorded_fixture' ? 'The configured fixture supplies recorded synthesis responses. No external model is called.'
      : 'This action permits local verification of retained evidence.'));
    const reviewer = element('input', '');reviewer.required = true;reviewer.maxLength = 120;reviewer.name = 'reviewer_label';
    const reviewerLabel = element('label', '', 'Reviewer label');reviewerLabel.append(reviewer);form.append(reviewerLabel);
    const consent = element('input', '');consent.type = 'checkbox';consent.required = true;
    const consentLabel = element('label', '', scope === 'local_verification' ? ' Authorize local verification' : ' Authorize synthesis for these saved results');
    consentLabel.append(consent);form.append(consentLabel);
    const submit = element('button', 'primary', 'Continue');submit.type = 'submit';form.append(submit);
    let requestKey;
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (submit.disabled || !consent.checked || !reviewer.value.trim() || typeof onAction !== 'function') return;
      requestKey ||= globalThis.crypto.randomUUID();
      submit.disabled = true;
      try {
        await onAction({type: 'continue_synthesis', investigationId: model.id, fields: {
          expected_revision: continuation.expected_revision, evidence_key: continuation.evidence_key,
          idempotency_key: requestKey, allow_model_execution: scope !== 'local_verification', reviewer_label: reviewer.value.trim()
        }});
      } catch (_) {
        // The investigation controller displays request errors.
      } finally {
        submit.disabled = false;
      }
    });
    disclosure.append(form);card.append(disclosure);
    return card;
  }
  function appendAnalystAction(card, content) {
    const selection = content.selection || content.action || {}, decision = content.decision_record || content.decision || {};
    const message = text(selection.message) || text(selection.rationale) || text(decision.rationale) || text(content.rationale) || text(content.message);
    if (message) card.append(element('p', 'wc-message-body', message));
    else if (text(decision.concern)) card.append(element('p', 'wc-message-body', decision.concern));
    const expected = text(decision.expected_discriminating_observation) || text(decision.expected_check);
    if (expected) card.append(element('p', 'wc-field-reason', 'Expected check: ' + expected));
    const request = selection.query || selection.request || selection.options;
    if (request) card.append(structuredValue(request, 0, {rows: 24}));
    const result = content.result || content.query_result || decision.result;
    if (result && typeof result === 'object') {
      const rows = Array.isArray(result) ? result : result.rows || result.gene_statistics || result.findings;
      const table = evidenceTable(rows, result.columns);
      if (table) card.append(table);else card.append(structuredValue(result, 0, {rows: 32}));
    }
    card.append(createDisclosure('Exact analyst action evidence', content));
  }
  function appendAdaptiveConclusion(card, evidence, onAction, showReviewLink = true) {
    // 1. Status Badges
    const badgeRow = element('div', 'wc-conclusion-badges');
    badgeRow.append(
      element('span', 'wc-status-pill wc-pill-neutral is-complete', evidence.execution_status || 'Execution status unavailable'),
      element('span', 'wc-status-pill wc-pill-neutral is-untested', evidence.answer_completeness || 'Answer completeness unavailable')
    );
    card.append(badgeRow);

    const directAnswer = text(evidence.direct_answer);
    if (directAnswer) {
      const lead = element('p', 'wc-conclusion-answer', directAnswer);
      if (evidence.unanswered_parts?.includes('Stage-by-population interaction')) lead.append(' The change in the population effect across stages remains untested.');
      card.append(lead);
    }
    const findings = Array.isArray(evidence.structured_findings) ? evidence.structured_findings : [];
    const findingsGrid = element('div', 'wc-findings-grid');
    for (const f of findings) {
      const fCard = element('article', 'wc-finding-card');
      const header = element('div', 'wc-finding-header');
      header.append(element('h4', 'wc-finding-title', f.title));
      if (f.figure_link) {
        const link = element('button', 'wc-finding-link', f.figure_link);
        link.type = 'button';
        link.addEventListener('click', () => {
          if (typeof onAction === 'function') onAction({type: 'navigate', view: 'results', stage: f.stage});
        });
        header.append(link);
      }
      fCard.append(header, element('p', 'wc-finding-body', f.observation + ' ' + (f.meaning || '')));
      findingsGrid.append(fCard);
    }
    if (findings.length) card.append(findingsGrid);
    if (Array.isArray(evidence.scope_limits) && evidence.scope_limits.length) {
      const limits = element('section', 'wc-limits-note');
      limits.append(element('strong', '', 'Scope & limits'));
      for (const limit of evidence.scope_limits) limits.append(element('p', '', text(limit)));
      card.append(limits);
    }
    if (text(evidence.next_step)) {
      const nextRow = element('div', 'wc-next-step-row');
      nextRow.append(element('span', '', evidence.next_step),
        actionButton({type: 'draft', question: evidence.next_step, label: 'Draft interaction test'}, onAction));
      card.append(nextRow);
    }

    // 6. Expandable Methods, Diagnostics & Evidence (<details class="wc-disclosure">)
    const details = element('details', 'wc-disclosure');
    details.append(element('summary', '', 'Methods, model diagnostics & evidence receipts'));
    const detailsBody = element('div', 'wc-activity-log');

    // Retain full historical sections inside methods drawer
    for (const [title, value] of [
      ['Data and declared units', {measurement: evidence.measurement, biological_unit_column: evidence.biological_unit_column,
        declared_stages: evidence.declared_stages, observed: (evidence.trials || []).map(trial => Object.fromEntries(
          Object.entries(trial.summary || {}).filter(([key]) => ['observation_count', 'feature_count', 'declared_biological_unit_count'].includes(key))))}],
      ['Question', {question: evidence.question, requirement_source: evidence.requirement_source}],
      ['Frozen method', {procedure: evidence.procedure, choice_origin: evidence.choice_origin,
        parameter_origin: (evidence.trials || []).map(trial => trial.parameter_origin)}]
    ]) {
      const section = element('section', 'wc-synthesis-section');
      section.append(element('h4', '', title), structuredValue(value, 0, {rows: 25}));
      if (title === 'Data and declared units') section.append(element('p', '', 'Cells are subsamples of the supplied biological units. Independence and annotation provenance rely on the supplied declarations.'));
      if (title === 'Frozen method' && evidence.procedure === 'embryo_fixed_effect_stage_population_nb_v1') {
        section.append(element('p', '', 'The frozen procedure sums counts by biological unit, stage and population. It uses biological-unit blocking, RLE offsets, empirical-Bayes negative-binomial dispersion estimates and Wald tests. Within-stage contrasts do not test a stage interaction.'));
      }
      detailsBody.append(section);
    }

    for (const trial of evidence.trials || []) {
      const section = element('section', 'wc-synthesis-section');
      section.append(element('h4', '', 'Verified results · ' + trial.trial),
        createDisclosure('Design diagnostics and condition-number conventions', trial.design_diagnostics),
        createDisclosure('Multiplicity and unavailable estimates', {testing_family: trial.testing_family,
          unavailable_estimates: trial.unavailable_estimates}),
        createDisclosure('Contrast support and gene previews', trial.tables),
        actionButton({type: 'artifact', view: 'evidence', artifactId: trial.result_artifact_id, label: 'Inspect the complete result'}, onAction, 'secondary'));
      if (!trial.plot_capabilities?.length) section.append(element('p', '', 'No verified plot is available.'));
      else section.append(actionButton({type: 'navigate', view: 'results', label: 'View saved plots'}, onAction, 'secondary'));
      appendLimitations(section, trial.limitations);
      detailsBody.append(section);
    }

    details.append(detailsBody);
    card.append(details);
    if (showReviewLink) card.append(actionButton({type: 'navigate', view: 'evidence', label: 'Inspect scientific review'}, onAction));
  }
  const retainedDigest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);

  function appendReceiptChip(host, source, scope, artifacts = []) {
    const studio = root.WorkbenchStudio;
    if (typeof studio?.inspectReceipt !== 'function' || !source || typeof source !== 'object') return;
    const identity = retainedDigest(source.receipt_sha256) ? source.receipt_sha256 : '';
    const digestValue = source.artifact_sha256 ||
      (typeof source.receipt_id === 'string' ? source.result_sha256 : null);
    const digest = retainedDigest(digestValue) ? digestValue : '';
    if (!identity && !digest) return;
    const step = text(source.step_id || source.node || source.tool).slice(0, 200);
    const reference = {
      ...(identity ? {receipt_sha256: identity} : {}),
      ...(digest ? {artifact_sha256: digest} : {}),
      ...(step ? {step_id: step} : {}),
      ...(Number.isInteger(source.generation) ? {generation: source.generation} : {}),
      ...(scope ? {claim_scope: scope} : {}),
    };
    const descriptor = digest ? artifacts.find(item => item?.sha256 === digest) : null;
    if (descriptor && typeof root.WorkbenchArtifactCanvas?.validateDescriptor === 'function') {
      try {reference.artifact = root.WorkbenchArtifactCanvas.validateDescriptor(descriptor);} catch (_) {}
    }
    const button = element('button', 'secondary studio-receipt-chip',
      (step || (identity ? 'Receipt' : 'Artifact')) + ' · ' + (identity || digest).slice(0, 8));
    button.type = 'button';
    button.addEventListener('click', () => studio.inspectReceipt(reference));
    host.append(button);
  }

  function appendEpistemicStrip(host, model) {
    const synthesis = model.synthesis || {}, population = synthesis.population_evidence || {};
    const findings = Array.isArray(synthesis.findings) ? synthesis.findings : [];
    const analyst = Array.isArray(synthesis.analyst_findings) ? synthesis.analyst_findings :
      Array.isArray(synthesis.analyst?.findings) ? synthesis.analyst.findings : [];
    const scopes = new Set([synthesis.claim_scope, ...findings.slice(0, 20).map(
      finding => finding?.claim_scope || finding?.source?.claim_scope)]);
    const observed = Boolean(Object.keys(model.profile?.observed || {}).length) ||
      analyst.slice(0, 20).some(row => row?.observations?.length);
    const declared = Object.keys(model.declarations || {}).length ||
      Object.keys(model.profile?.declarations || {}).length ||
      model.sourceDeclarations?.some(row => Object.keys(row || {}).length);
    const confirmed = synthesis.evidence?.status === 'verified' &&
      [...scopes].some(scope => ['admitted_research_inference', 'qualified_inference'].includes(scope));
    const exploratory = analyst.length || [...scopes].some(scope =>
      ['exploratory_only', 'exploratory_marker_ranking', 'candidate_research_inference'].includes(scope));
    const uncertain = (synthesis.uncertainties || synthesis.limitations || population.scope_limits)?.length ||
      model.profile?.unknowns?.length;
    const strip = element('div', 'studio-epistemic-strip');
    for (const [visible, caption, status] of [
      [observed, 'Observed', 'observed'], [declared, 'Supplied declaration', 'declared'],
      [confirmed, 'Confirmatory inference', 'supported'],
      [exploratory, 'Exploratory evidence', 'exploratory_only'],
      [uncertain, 'Open uncertainties & limitations', 'awaiting_information'],
    ]) {
      if (visible) {
        const badge = element('span', 'studio-status', caption);
        badge.setAttribute('data-status', status);
        strip.append(badge);
      }
    }
    if (strip.children.length) host.append(strip);
  }

  function appendSynthesisSections(card, synthesis, artifacts) {
    const analyst = synthesis.analyst || {}, assessments = Array.isArray(synthesis.analyst_findings) ? synthesis.analyst_findings : Array.isArray(analyst.findings) ? analyst.findings : [];
    const stoppingReason = text(analyst.stopping_reason) || text(synthesis.reason);
    if (stoppingReason || text(analyst.disposition)) {
      const section = element('section', 'wc-synthesis-section');section.append(cardHeader('Scientific stopping reason', text(analyst.disposition) ? keyLabel(analyst.disposition) : 'Recorded conclusion', 'neutral'));
      if (stoppingReason) section.append(element('p', 'wc-message-body', stoppingReason));card.append(section);
    }
    if (assessments.length) {
      const section = element('section', 'wc-synthesis-section');section.append(element('h4', '', 'Gene assessments · exploratory evidence'));
      for (const finding of assessments.slice(0, 20)) {
        if (!finding || typeof finding !== 'object') continue;
        const row = element('article', 'wc-trial-result');row.append(cardHeader(text(finding.gene_id) || text(finding.hypothesis_id) || text(finding.target_id) || 'Recorded assessment', keyLabel(finding.disposition || 'Recorded'), 'neutral'));
        if (text(finding.qualification)) row.append(element('p', 'wc-message-body', finding.qualification));
        for (const [key, title] of [['reporting_scope', 'Reporting scope'], ['qualification_origin', 'Qualification source']]) if (text(finding[key])) row.append(element('p', 'wc-field-reason', title + ': ' + keyLabel(finding[key])));
        if (Array.isArray(finding.evidence_refs) && finding.evidence_refs.length) row.append(element('p', 'wc-field-reason', 'Evidence citations: ' + finding.evidence_refs.filter(ref => typeof ref === 'string').join(', ')));
        if (finding.source) appendReceiptChip(row, finding.source, finding.reporting_scope, artifacts);
        if (Array.isArray(finding.observations) && finding.observations.length) {row.append(element('h4', '', 'Receipt-derived observations'));const table = evidenceTable(finding.observations, ['field_ref', 'value']);if (table) row.append(table);}
        if (finding.declared_pairing) row.append(structuredValue({declared_pairing: finding.declared_pairing}, 0, {rows: 12}));
        section.append(row);
      }
      if (assessments.length > 20) section.append(element('p', 'wc-truncated', 'Showing 20 of ' + assessments.length + ' gene assessments.'));
      card.append(section);
    }
    if (Array.isArray(analyst.followup_proposals) && analyst.followup_proposals.length) {
      const section = element('section', 'wc-synthesis-section');section.append(element('h4', '', 'Proposed follow-ups · not executed'));
      if (text(analyst.proposal_scope)) section.append(element('p', 'wc-field-reason', keyLabel(analyst.proposal_scope)));
      for (const proposal of analyst.followup_proposals.slice(0, 5)) {
        if (!proposal || typeof proposal !== 'object') continue;
        const row = element('article', 'wc-trial-result');row.append(cardHeader(text(proposal.question) || 'Proposed follow-up', keyLabel(proposal.status || 'proposed'), 'neutral'));
        row.append(structuredValue(Object.fromEntries(Object.entries(proposal).filter(([key]) => !['question', 'status'].includes(key))), 0, {rows: 20}));section.append(row);
      }
      card.append(section);
    }
    if (Array.isArray(synthesis.findings) || assessments.length) {
      const section = element('section', 'wc-synthesis-section');section.append(element('h4', '', 'Evidence-supported findings'));
      if (!synthesis.findings?.length && !assessments.length) section.append(element('p', '', 'No supported numerical findings were retained.'));
      for (const finding of (synthesis.findings || []).slice(0, 20)) {
        if (!finding || typeof finding !== 'object') continue;
        const row = element('article', 'wc-trial-result');row.append(element('h4', '', text(finding.target_id) || 'Recorded target'));
        const estimate = finding.estimate;
        if (estimate && typeof estimate === 'object') row.append(evidenceTable([estimate]) || structuredValue(estimate, 0, {rows: 24}));
        if (finding.replication) row.append(structuredValue({replication: finding.replication}, 0, {rows: 24}));
        if (finding.source) {
          appendReceiptChip(row, finding.source, finding.claim_scope || finding.source.claim_scope, artifacts);
          row.append(structuredValue({exact_evidence: finding.source}, 0, {rows: 32}));
        }
        section.append(row);
      }
      if (synthesis.findings?.length || !assessments.length) card.append(section);
      for (const [title, value] of [['Evidence verification and scope', synthesis.evidence], ['What remains uncertain', synthesis.uncertainties || synthesis.limitations], ['Target coverage', synthesis.targets]]) if (value !== undefined) {const section = element('section', 'wc-synthesis-section');section.append(element('h4', '', title), structuredValue(value, 0, {rows: 40}));card.append(section);}
      card.append(createDisclosure('Exact synthesis evidence', synthesis));return;
    }
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
  function publicPlanningCard(model) {
    const plan = model.scientificPlan || {}, preliminary = plan.preliminary_explanation?.response;
    if (preliminary && Array.isArray(preliminary.hypotheses)) {
      const card = element('article', 'wc-card wc-public-proposal');card.append(cardHeader('Preliminary explanation', 'Not a final conclusion', 'neutral'));
      for (const hypothesis of preliminary.hypotheses.slice(0, 4)) {
        if (!hypothesis || typeof hypothesis !== 'object') continue;
        if (text(hypothesis.statement)) card.append(element('p', 'wc-message-body', hypothesis.statement));
        appendLimitations(card, hypothesis.limitations);
      }
      for (const item of (Array.isArray(preliminary.ambiguity_items) ? preliminary.ambiguity_items : []).slice(0, 2)) if (text(item.discriminating_criterion)) card.append(element('p', 'wc-field-reason', item.discriminating_criterion));
      appendLimitations(card, preliminary.uncertainty);
      if (Array.isArray(preliminary.absent_assessments) && preliminary.absent_assessments.length) {card.append(element('h4', '', 'Not yet assessed'));const list = element('ul', 'wc-answer-limitations');for (const item of preliminary.absent_assessments.slice(0, 16)) if (typeof item === 'string') list.append(element('li', '', item));card.append(list);}
      return card;
    }
    const candidates = plan.scientific_candidates;
    if (!Array.isArray(candidates)) return null;
    const historical = Boolean(model.synthesis || plan.experiments?.length), card = element('article', 'wc-card wc-public-proposal');card.append(cardHeader(historical ? 'Recorded proposal' : 'Proposed approach', historical ? 'Proposal record' : 'Pending scientific review', 'neutral'));
    card.append(element('p', 'wc-message-body', candidates.length ? 'The retained planning response proposes ' + candidates.length + (candidates.length === 1 ? ' method.' : ' methods.') + (historical ? ' Its assumptions and choices remain part of the recorded plan.' : ' These assumptions and choices still need review.') : 'The retained planning response did not propose a scientifically plausible method.'));
    for (const [index, candidate] of candidates.slice(0, 4).entries()) {
      if (!candidate || typeof candidate !== 'object') continue;
      const section = element('section', 'wc-public-candidate');section.append(element('h4', '', text(candidate.name) || 'Proposed method'));
      if (text(candidate.method_family)) section.append(element('p', 'wc-message-body', candidate.method_family));
      section.append(element('p', 'wc-field-reason', 'Planner assessment · Data fit: ' + (text(candidate.dataset_fit) || 'not recorded') + ' · Question fit: ' + (text(candidate.objective_fit) || 'not recorded')));
      const details = element('details', 'wc-public-assumptions'), summary = element('summary', '', 'Assumptions and proposed parameters'), identity = 'proposal-assumptions-' + model.id + '-' + index;
      details.setAttribute('id', identity);summary.setAttribute('id', identity + '-summary');details.append(summary);
      for (const assumption of (Array.isArray(candidate.assumptions) ? candidate.assumptions : []).slice(0, 8)) {
        const row = element('div', 'wc-public-assumption');row.append(element('p', 'wc-message-body', text(assumption.statement)), element('span', 'wc-status-pill wc-pill-neutral', keyLabel(text(assumption.status) || 'unknown')));
        if (text(assumption.evidence)) row.append(element('p', 'wc-field-reason', assumption.evidence));details.append(row);
      }
      for (const parameter of (Array.isArray(candidate.parameter_candidates) ? candidate.parameter_candidates : []).slice(0, 8)) {
        const row = element('div', 'wc-public-parameter');row.append(element('p', 'wc-field-reason', (text(parameter.name) || 'Parameter') + ' · ' + keyLabel(text(parameter.origin) || 'origin not recorded')), structuredValue(parameter.value, 0, {rows: 16}));
        if (text(parameter.rationale)) row.append(element('p', 'wc-field-reason', parameter.rationale));details.append(row);
      }
      if ((candidate.assumptions?.length || 0) > 8 || (candidate.parameter_candidates?.length || 0) > 8) details.append(element('p', 'wc-truncated', 'Showing the first eight assumptions and parameters from this retained proposal.'));
      appendLimitations(details, (Array.isArray(candidate.known_failure_modes) ? candidate.known_failure_modes : []).slice(0, 8));
      if (text(candidate.source?.reference)) details.append(element('p', 'wc-field-reason', 'Retained source: ' + candidate.source.reference));
      section.append(details);card.append(section);
    }
    if (candidates.length > 4) card.append(element('p', 'wc-truncated', 'Showing 4 of ' + candidates.length + ' retained proposals. The complete plan remains in the scientific evidence.'));
    return card;
  }
  function scientificReviewFeedbackCard(content, artifacts, onAction) {
    const needsEvidence = ['insufficient_evidence', 'not_assessed'].includes(content.status), card = element('article', 'wc-card wc-scientific-review-feedback');
    card.append(cardHeader(needsEvidence ? 'Scientific review needs evidence' : 'Scientific review', needsEvidence ? 'Needs evidence' : keyLabel(text(content.status) || 'not recorded'), 'neutral'));
    const kinds = {candidate_suitability: 'Candidate suitability', comparison_design: 'Comparison design', results_conclusions: 'Results and conclusions'};
    card.append(element('p', 'wc-field-reason', (kinds[content.review_kind] || 'Recorded review') + ' · Workflow: ' + (text(content.workflow_status) || 'not recorded')));
    if (text(content.rationale)) card.append(element('p', 'wc-message-body', content.rationale));
    function list(title, values, limit) {
      const items = (Array.isArray(values) ? values : []).filter(value => typeof value === 'string');if (!items.length) return;
      const rows = element('ul', 'wc-answer-limitations');card.append(element('h4', '', title));
      for (const item of items.slice(0, limit)) rows.append(element('li', '', item));card.append(rows);
      if (items.length > limit) card.append(element('p', 'wc-truncated', 'Showing ' + limit + ' of ' + items.length + ' items; the exact review evidence retains the full list.'));
    }
    list('Missing evidence', content.missing_evidence, 16);
    if (text(content.action_consequences)) card.append(element('p', 'wc-message-body', content.action_consequences));
    list('Competing explanations', content.competing_explanations, 8);
    if (text(content.next_action)) card.append(element('h4', '', 'Next step'), element('p', 'wc-message-body', content.next_action));
    appendSourceBinding(card, content.binding || {});
    if (artifacts.length) card.append(actionButton({type: 'navigate', view: 'evidence', label: 'Inspect saved evidence'}, onAction));
    const evidence = {};
    for (const key of ['format', 'review_kind', 'status', 'workflow_status', 'rationale', 'missing_evidence', 'action_consequences', 'competing_explanations', 'evidence_refs', 'evidence_ids', 'next_action', 'binding', 'event_key']) if (content[key] !== undefined) evidence[key] = content[key];
    for (const key of ['model_sessions_attempted', 'tokens']) if (content[key] === null || Number.isFinite(content[key])) evidence[key] = content[key];
    card.append(createDisclosure('Exact scientific review evidence', evidence));return card;
  }
  function appendPublicMessage(host, model, answerTurn, hasPublicPlan) {
    const turns = model.turns.filter(turn => !turn.investigation_id || turn.investigation_id === model.id);
    let identity = '';
    let message = model.diagnostic || diagnostics.get(model.error?.code) || '';
    for (const turn of message ? [] : turns.slice().reverse()) {
      if (!['dataset_answer', 'singlecell_evidence', 'clarification', 'conclusion', 'error', 'public_message', 'scientific_review_feedback'].includes(turn.kind)) continue;
      if (turn.kind === 'scientific_review_feedback') break;
      if (turn === answerTurn || hasPublicPlan && ['dataset_answer', 'singlecell_evidence', 'conclusion'].includes(turn.kind)) break;
      const content = turn.content || {};
      message = diagnostics.get(content.diagnostic_code) || diagnostics.get(content.code) || diagnostics.get(content.reason) ||
        (turn.kind === 'error' ? failureSummary(text(content.message) || text(content.reason)) :
          text(content.message) || (turn.kind === 'clarification' ? text(content.guidance || content.reason) : ''));
      if (message) {identity = text(turn.id);break;}
    }
    if (!message && FAILURES.has(model.status)) message = failureSummary(text(model.error?.message));
    if (!message && !hasPublicPlan && !answerTurn) message = ({review: 'The recorded plan is ready for your review below.', scientific_review: 'The proposed scientific trial is ready for your review below.', scientific_execution_review: 'The reviewed trial is ready for explicit run approval.', run: 'The approved plan is ready for explicit run approval.'})[model.inlineGate.kind] || '';
    if (!message || message === text(answerTurn?.content?.message)) return '';
    const card = element('article', 'wc-public-message');if (identity) card.setAttribute('data-public-turn-id', identity);
    card.append(element('p', 'wc-message-body', message));host.append(card);return message;
  }
  function appendResearchFeed(host, activity, identity) {
    const events = Array.isArray(activity?.events) ?
      activity.events.slice(-12).filter(row => text(row?.title)) : [];
    for (const event of [activity?.last, activity?.current]) {
      if (!text(event?.title)) continue;
      const latest = events.at(-1);
      if (!latest || latest.title !== event.title || latest.status !== event.status ||
          latest.detail !== event.detail) events.push(event);
    }
    const latest = text(activity?.current?.title) ? activity.current : events.at(-1);
    if (!latest) return false;
    const recent = events.slice(-12);
    const feed = element('details', 'wc-research-feed');
    feed.open = false;
    if (text(identity)) feed.setAttribute('id', identity);
    feed.setAttribute('data-activity-state',
      ['active','running'].includes(latest.status) ? 'running' : 'settled');
    const summary = element('summary');
    const content = element('span', 'wc-research-feed-current');
    const dot = element('span', 'wc-research-feed-dot');
    dot.setAttribute('aria-hidden','true');
    content.setAttribute('role','status');
    content.append(element('span', 'wc-research-feed-label', 'Workflow activity'),
      element('strong', '', latest.title));
    summary.append(dot, content, element('span', 'wc-research-feed-count',
      recent.length + (recent.length === 1 ? ' event' : ' events')));
    const timeline = element('ol', 'wc-research-feed-events');
    for (const event of recent) {
      const row = element('li', 'wc-research-feed-event');
      row.append(element('strong', '', event.title));
      if (text(event.status)) row.append(element('span', 'wc-research-step-status', keyLabel(event.status)));
      if (text(event.detail)) row.append(element('p', 'wc-research-detail', event.detail));
      timeline.append(row);
    }
    feed.append(summary,timeline);
    host.append(feed);
    return true;
  }

  function appendResearchActivity(host, research, view, onAction, feedVisible) {
    const activity = research.activity || {}, next = research.next_action || {};
    const running = ['active', 'running'].includes(activity.current?.status);
    const items = [
      ['Current activity', feedVisible ? null : activity.current?.title, activity.current?.detail, activity.current?.status],
      ['Last recorded event', activity.last?.title, activity.last?.detail, activity.last?.status],
      ['Next decision', running ? null : next.label, next.detail, next.status],
    ];
    const list = element('dl', 'wc-research-status');
    list.setAttribute('aria-label', 'Workflow progress');
    for (const [heading, value, detail, status] of items) {
      if (!text(value)) continue;
      const row = element('div', 'wc-research-status-row');
      if (text(status)) row.setAttribute('data-status', status);
      row.append(element('dt', '', heading), element('dd', '', value));
      if (text(detail)) row.append(element('dd', 'wc-research-detail', detail));
      list.append(row);
    }
    if (list.children.length) host.append(list);
    if (view === 'overview' && onAction) {
      host.append(actionButton({type:'navigate',view:'workflows',label:'Open plan and run controls'},onAction));
    }
  }

  function appendResearchPlan(host, research) {
    const plan = research.plan || {};
    const article = element('article', 'wc-research-card');
    article.append(element('h3', '', text(plan.title) || 'Analysis plan'));
    article.append(element('p', '', text(plan.summary) ||
      'An analysis plan will appear here when a planning decision is recorded.'));
    if (text(plan.method)) article.append(element('p', 'wc-research-detail', plan.method));
    const proposed = Array.isArray(plan.steps) ? plan.steps.filter(item => text(item?.title)) : [];
    if (proposed.length) {
      const list = element('ol', 'wc-research-proposed');
      for (const item of proposed) {
        const row = element('li', '', item.title);
        if (text(item.description)) row.append(element('p', 'wc-research-detail', item.description));
        list.append(row);
      }
      article.append(list);
    }
    host.append(article);
    const steps = Array.isArray(research.steps) ? research.steps.filter(item => text(item?.title || item?.operation)) : [];
    if (!steps.length) return;
    const run = element('section', 'wc-research-card');
    const ledger = element('ol', 'wc-research-run');
    run.append(element('h3', '', 'Recorded run steps'), ledger);
    for (const item of steps) {
      const row = element('li', 'wc-research-run-step');
      row.append(element('strong', '', item.title || keyLabel(item.operation)),
        element('span', 'wc-research-step-status', keyLabel(item.status || 'recorded')));
      if (text(item.detail)) row.append(element('p', 'wc-research-detail', item.detail));
      ledger.append(row);
    }
    host.append(run);
  }

  function researchFinding(research) {
    return research?.checked_observation || research?.findings || {};
  }

  function appendResearchFindings(host, research, view, onAction, compact) {
    const finding = researchFinding(research);
    const answer = text(finding.answer);
    const article = element('article', 'wc-research-card wc-research-answer');
    article.append(element('h3', '', answer ? 'Answer to your question' : 'Findings'));
    if (answer) article.append(element('p', 'wc-research-lead', answer));
    else article.append(element('p', '', text(finding.summary) || (finding.status === 'blocked'
      ? 'A workflow constraint is holding this answer. Review the plan for details.'
      : 'An answer will appear when the workflow has checked evidence for this question.')));
    const metrics = Array.isArray(finding.metrics) ? finding.metrics.filter(row =>
      text(row?.label) && (text(row.value) || typeof row.value === 'number' && Number.isFinite(row.value))) : [];
    if (!compact && metrics.length) article.append(metricGrid(metrics));
    const limits = [research.limitations, finding.limitations].flatMap(value => Array.isArray(value) ? value.filter(item => text(item)) : []);
    if (limits.length) {
      article.append(element('h4', '', 'Scope and limitations'));
      const list = element('ul', 'wc-research-limits');
      for (const item of new Set(limits)) list.append(element('li', '', item));
      article.append(list);
    }
    const receipts = Array.isArray(finding.evidence) ? finding.evidence.filter(row =>
      /^[0-9a-f]{64}$/.test(text(row?.sha256)) || text(row?.label)) : [];
    if (!compact && receipts.length) {
      const details = element('details', 'wc-research-evidence');
      details.append(element('summary', '', 'Supporting evidence'));
      for (const receipt of receipts) {
        const row = element('p', 'wc-research-detail', text(receipt.label) || 'Receipt SHA-256');
        if (/^[0-9a-f]{64}$/.test(text(receipt.sha256))) row.append(element('code', '', receipt.sha256));
        details.append(row);
      }
      article.append(details);
    }
    if (answer && view !== 'evidence' && onAction) {
      article.append(actionButton({type:'navigate',view:'evidence',label:'Inspect supporting records'},onAction));
    }
    host.append(article);
  }

  function renderAdaptiveResearch(host, research, options = {}) {
    const {view = 'overview', onAction, activityFeed = false, feedId, compact = false} = options;
    const surface = element('section', 'wc-research-surface');
    surface.setAttribute('aria-label', view === 'plan' ? 'Investigation plan and progress' : 'Current research evidence');
    const feedVisible = view === 'plan' && activityFeed &&
      appendResearchFeed(surface,research.activity,feedId);
    if (view === 'plan' || view === 'overview') appendResearchActivity(surface, research, view, onAction, feedVisible);
    if (view === 'plan' || view === 'overview' && research.plan) appendResearchPlan(surface, research);
    if (view === 'findings' || view === 'evidence' || view === 'overview' && researchFinding(research).answer) {
      appendResearchFindings(surface, research, view, onAction, compact);
    }
    host.replaceChildren(surface);
    return surface;
  }

  function investigationStageViews(inline, phase, options) {
    const buttons = options.stageButtons;
    buttons[0].closest('nav')?.setAttribute('role', 'tablist');
    const panels = buttons.map((button, index) => {
      const panel = element('section', 'wc-stage-content');
      panel.setAttribute('id', 'wc-stage-content-' + (index + 1));
      panel.setAttribute('role', 'tabpanel');
      panel.setAttribute('aria-labelledby', button.id);
      panel.tabIndex = 0;
      return panel;
    });
    inline.answer.append(...panels);
    if (inline.phase !== phase) inline.selectedStage = phase;
    inline.phase = phase;
    function show(stage) {
      inline.selectedStage = stage;
      panels.forEach((panel, index) => {
        const selected = index + 1 === stage, button = buttons[index];
        panel.hidden = !selected;
        button.setAttribute('role', 'tab');
        button.setAttribute('aria-selected', String(selected));
        button.setAttribute('aria-controls', 'wc-stage-content-' + (index + 1));
        button.tabIndex = selected ? 0 : -1;
      });
    }
    buttons.forEach((button, index) => {
      button.disabled = false;
      button.onclick = () => {
        const stage = index + 1;
        show(stage);
        if (typeof options.onAction === 'function') {
          const linked = options.adaptive ? ['overview','workflows','findings'] : ['results','evidence','findings'];
          options.onAction({type: 'navigate', view: buttons.length === 3 ? linked[stage-1] : (stage === 5 ? 'evidence' : 'results'), workflowStage: stage});
        }
      };
      button.onkeydown = event => {
        const length = buttons.length;
        const target = {ArrowLeft: (index + length - 1) % length, ArrowRight: (index + 1) % length, Home: 0, End: length - 1}[event.key];
        if (!Number.isInteger(target) || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
        event.preventDefault();
        buttons[target].focus();
        buttons[target].click();
      };
    });
    show(inline.selectedStage || phase);
    return panels;
  }
  function renderInvestigation(container, options) {
    options = options || {};
    const investigation = options.investigation, model = buildInvestigationModel(investigation, options.config, options.jobState);
    const grouped = options.stageButtons?.length === 3;
    if (!investigation) {
      if (options.historyContainer) options.historyContainer.replaceChildren();
      for (const button of options.stageButtons || []) {
        button.onclick = null;
        button.onkeydown = null;
        button.disabled = true;
        button.removeAttribute('role');
        button.removeAttribute('tabindex');
        button.removeAttribute('aria-controls');
        button.removeAttribute('aria-selected');
      }
      const stageBar = typeof document !== 'undefined' && typeof document.getElementById === 'function' ? document.getElementById('wc-stage-bar') : null;
      if (stageBar && typeof document.getElementById === 'function') {
        stageBar.removeAttribute('role');
        const stageLabels = [null, ...stageCaptions(grouped)];
        for (let i = 1; i < stageLabels.length; i++) {
          const pill = document.getElementById('wc-stage-pill-' + i);
          if (pill) { pill.className = 'wc-stage-pill' + (i === 1 ? ' is-active' : ''); pill.textContent = stageLabels[i]; pill.setAttribute('aria-current', i === 1 ? 'step' : 'false'); }
        }
      }
      renderConversationWorkspace(container, {config: options.config, loading: options.loading, onAction: options.onAction});
      return model;
    }
    const runKey = 'investigation:' + investigation.id, changedRun = renderedRuns.get(container) !== runKey, cards = [];
    const scroller = typeof container.closest === 'function' ? container.closest('.inspector-body') : null;
    const following = scroller && Number.isFinite(scroller.clientHeight) && Number.isFinite(scroller.scrollHeight) && scroller.scrollHeight - scroller.clientHeight - scroller.scrollTop < 48 && !(typeof container.contains === 'function' && container.contains(document.activeElement));
    let tableState = evidenceTablePages.get(container);
    if (!tableState || tableState.key !== runKey) {tableState = {key: runKey, pages: new Map()};evidenceTablePages.set(container, tableState);}
    const restoreView = preserveConversationView(container, !changedRun);
    const inline = inlineActivity(container, runKey, model, Boolean(options.stageButtons?.length));
    renderedRuns.set(container, runKey);
    const context = element('div', 'wc-context');
    for (const source of model.sources) context.append(element('span', 'wc-input', text(source).split('/').pop()));
    inline.answer.append(context);
    if (text(model.question)) {
      const userBubble = element('div', 'wc-user-bubble', model.question);
      inline.answer.append(userBubble);
    }
    const answeredChips = element('div', 'wc-answered-chips');
    const bioCol = text(model.declarations?.biological_unit_column) || text(model.profile?.declarations?.biological_unit_column);
    if (bioCol) {
      const chip = element('span', 'wc-answered-chip', '✓ Unit: ');
      chip.append(element('strong', '', bioCol));
      answeredChips.append(chip);
    }
    if (model.scientificContext?.data_classification) {
      const chip = element('span', 'wc-answered-chip', '✓ Data: ');
      chip.append(element('strong', '', keyLabel(model.scientificContext.data_classification)));
      answeredChips.append(chip);
    }
    if (answeredChips.children.length) inline.answer.append(answeredChips);
    const feedbackTurn = model.turns.filter(turn => (!turn.investigation_id || turn.investigation_id === model.id) && turn.kind === 'scientific_review_feedback' && (!(turn.content?.binding?.question_id || turn.binding?.question_id) || (turn.content?.binding?.question_id || turn.binding?.question_id) === model.questionId)).at(-1);
    const blockedReview = feedbackTurn?.content?.workflow_status === 'blocked';
    const planCard = publicPlanningCard(model), reviewedSynthesis = !model.synthesisContinuation || ['completed', 'abstained', 'review_required', 'pending'].includes(model.synthesisContinuation.status), visibleSynthesis = reviewedSynthesis && !blockedReview && model.synthesis && model.request?.category !== 'descriptive_retrieval';
    const publicPlan = !blockedReview && !visibleSynthesis && (model.scientificPlan?.preliminary_explanation || !model.scientificPlan?.experiments?.length) ? planCard : null;
    const showFitConclusion = Boolean(model.synthesisContinuation?.population_evidence &&
      !model.synthesisContinuation.eligible && model.synthesisContinuation.status !== 'active');
    const answerTurn = !blockedReview && !publicPlan && !visibleSynthesis && !showFitConclusion ? model.turns.filter(turn => (!turn.investigation_id || turn.investigation_id === model.id) && ['dataset_answer', 'singlecell_evidence'].includes(turn.kind)).at(-1) : null;
    if (publicPlan && !options.stageButtons?.length) inline.answer.append(publicPlan);
    const questions = new Map([[investigation.id, text(investigation.question)]]);
    for (const turn of model.turns) if (turn.kind === 'user_question' || turn.role === 'user') {
      const id = turn.investigation_id || investigation.id, question = text(turn.content && turn.content.question);
      if (question && (!questions.get(id) || id !== investigation.id)) questions.set(id, question);
    }
    const activity = workflowActivity(model, workflowPhase(model));
    const phaseStep = options.stageButtons && model.inlineGate.kind === 'clarification' ? 2 : activity ? activity.step : workflowPhase(model);
    const execution = phaseStep === 4 && (ACTIVE.has(model.status) || FAILURES.has(model.status) ||
      ['run', 'scientific_execution_review', 'episode_clarification'].includes(model.inlineGate.kind));
    const phase = grouped ? investigationStage(phaseStep, execution) : phaseStep;
    const visibleActivity = grouped && activity ? {...activity, step: investigationStage(activity.step, execution)} : activity;
    const gateKey = [model.inlineGate.kind || '', model.inlineGate.jobId || '', model.scientificSessionId || '', text(investigation.required_action), ...model.fields.map(field => [field.scope || '', field.source_index, field.field].join(':'))].join('|');
    const scientificStages = investigationStages(model, activity);
    const stages = grouped ? visibleStages(scientificStages, activity, phase) : scientificStages;
    const stageBar = typeof document !== 'undefined' && typeof document.getElementById === 'function' ? document.getElementById('wc-stage-bar') : null;
    if (stageBar && typeof stageBar.querySelectorAll === 'function') {
      const stageLabels = [null, ...stageCaptions(grouped)];
      for (let i = 1; i < stageLabels.length; i++) {
        const pill = document.getElementById('wc-stage-pill-' + i);
        if (!pill) continue;
        const isRunningStage = visibleActivity?.kind === 'running' && visibleActivity.step === i;
        pill.setAttribute('aria-label', stageLabels[i]);
        pill.setAttribute('aria-current', i === phase ? 'step' : 'false');
        if (isRunningStage) {
          pill.className = 'wc-stage-pill is-running';
          pill.replaceChildren(element('span', 'wc-shimmer-text', stageLabels[i] + ' · working'));
        } else {
          const status = stages[i - 1]?.status;
          const done = status === 'completed' || i < phase && ['observed', 'available'].includes(status);
          pill.className = 'wc-stage-pill' + (i === phase ? ' is-active' : done ? ' is-done' : '');
          pill.textContent = (done && i !== phase ? '✓ ' : '') + stageLabels[i];
        }
      }
    }
    const staged = Boolean(options.stageButtons?.length && options.historyContainer);
    const panels = staged ? investigationStageViews(inline, phase, {...options, adaptive:model.adaptive}) : null;
    if (staged) {
      model.inlineGate.workflowStage = inline.selectedStage;
      model.inlineGate.workflowPhase = phase;
      if (!['working', 'queued', 'stopping', 'failed', 'recovery', 'cancelled'].includes(inline.status.getAttribute('data-activity-state'))) inline.host.hidden = true;
    }
    const trail = staged ? options.historyContainer : inline.trail;
    if (staged) {inline.trail.hidden = true;trail.replaceChildren();}
    const flow = scientificWorkflow(trail, runKey, {owner: investigation.id, questions, phase, activity: visibleActivity, stages, grouped, gate: gateKey, history: staged});
    const present = (step, node, headline = false, execution = false) => {
      if (staged) panels[(grouped ? investigationStage(step, execution) : step) - 1].append(node);
      else if (headline) inline.answer.append(node);
      else flow.append(investigation.id, step, node, execution);
    };
    const answerReference = () => element('p', 'wc-workflow-reference', 'Current answer is shown above the overview.');
    const planStep = model.scientificPlan?.preliminary_explanation ? 5 : 3;
    if (planCard && (!publicPlan || staged)) {
      if (staged) present(planStep, planCard);else flow.append(investigation.id, planStep, planCard);
    }
    const latestAudit = model.turns.filter(turn => (!turn.investigation_id || turn.investigation_id === model.id) && turn.kind === 'data_audit').at(-1);
    const latestContrast = model.turns.filter(turn => (!turn.investigation_id || turn.investigation_id === model.id) &&
      turn.kind === 'singlecell_evidence' && turn.content?.tool_name === 'scrna_population_contrast').at(-1);
    const latestPlan = model.turns.filter(turn => (!turn.investigation_id || turn.investigation_id === model.id) && turn.kind === 'plan_review').at(-1);
    const clarificationHistory = new Map();
    let auditShown = false, planShown = false;
    for (const turn of model.turns) {
      const kind = text(turn.kind) || text(turn.type), content = turn.content && typeof turn.content === 'object' ? turn.content : {};
      const owner = turn.investigation_id || investigation.id, currentTurn = owner === investigation.id;
      const step = kind === 'user_question' || turn.role === 'user' ? 2 : Number.isInteger(turn.step) && turn.step >= 1 && turn.step <= 5 ? turn.step : kind === 'synthesis' ? 5 : ['job_event', 'error', 'scientific_operation', 'analyst_action', 'analyst_query'].includes(kind) ? 4 : 3;
      if (kind === 'clarification_answer' && (model.adaptive || model.sources.length > 1)) {
        let history = clarificationHistory.get(owner);
        if (!history) {
          const card = element('article', 'wc-card wc-context-history');
          card.setAttribute('data-turn-id', text(turn.id));
          card.setAttribute('data-turn-kind', kind);
          history = element('details', 'wc-disclosure');
          history.append(element('summary', '', 'Review supplied context'));
          card.append(cardHeader('Dataset context'), history);
          clarificationHistory.set(owner, history);
          if (staged && currentTurn) present(2, card);
          else flow.append(owner, 2, card);
          cards.push(card);
        }
        const evidence = structuredValue({recorded_at: turn.at, ...content}, 0, {rows:24});
        evidence.setAttribute('data-turn-id', text(turn.id));
        history.append(evidence);
        continue;
      }
      if (kind === 'scientific_review_feedback') {
        const card = scientificReviewFeedbackCard(content, turn === feedbackTurn ? model.artifacts : [], options.onAction);card.setAttribute('data-turn-id', text(turn.id));card.setAttribute('data-turn-kind', kind);
        const reviewStep = content.review_kind === 'results_conclusions' ? 5 : 3;
        if (turn === feedbackTurn) {present(reviewStep, card, true);if (!staged) flow.append(owner, reviewStep, element('p', 'wc-workflow-reference', 'Current review feedback is shown above the overview.'));}else flow.append(owner, reviewStep, card);
        cards.push(card);continue;
      }
      if (kind === 'dataset_answer') {
        const card = element('article', 'wc-card wc-dataset-answer');card.setAttribute('data-turn-id', text(turn.id));card.setAttribute('data-turn-kind', kind);
        const comparison = currentTurn && model.request?.cell_type_comparison;
        const title = comparison ? model.request.motor_program_comparison ? 'Motor-neuron candidate programs' : 'Cell-type comparison' : content.matrix_audit_id ? 'Matrix diagnostics' : 'Dataset description';
        const status = comparison ? model.status === 'completed' ? 'Descriptive comparison complete' : 'Results need review' : content.matrix_audit_id ? content.completion === 'completed' ? 'Diagnostics complete' : 'Partial diagnostics' : content.completion === 'completed' ? 'Description complete' : content.completion === 'partial' ? 'Partial description' : 'Description unavailable';
        card.append(cardHeader(title, status, content.completion === 'completed' ? 'good' : 'neutral'));
        appendDatasetAnswer(card, content, owner + '-' + text(turn.id), tableState.pages);if (turn === answerTurn || staged && currentTurn) {present(comparison ? 5 : 4, card, true);if (!staged) flow.append(owner, 4, answerReference());}else flow.append(owner, 4, card);cards.push(card);continue;
      }
      if (kind === 'singlecell_evidence') {
        const titles = {scrna_population_audit: 'Population audit', scrna_population_design: 'Study design', scrna_population_pseudobulk: 'Pseudobulk preparation', scrna_population_contrast: 'Population contrast'};
        const card = element('article', 'wc-card wc-singlecell-evidence');card.setAttribute('data-turn-id', text(turn.id));card.setAttribute('data-turn-kind', kind);
        card.append(cardHeader(titles[content.tool_name] || 'Single-cell evidence', label(text(content.status)), ['failed', 'refused'].includes(content.status) ? 'bad' : 'neutral'));
        const evidence = turn === latestContrast ? model.synthesisContinuation?.population_evidence : null;
        appendSinglecellEvidence(card, content, owner + '-' + text(turn.id), tableState.pages, currentTurn ? model.artifacts : [], options.onAction, evidence);
        if (staged && evidence) {
          const conclusion = element('button', 'secondary', 'Read the conclusion →');conclusion.type = 'button';
          conclusion.addEventListener('click', () => {const lastStage = options.stageButtons.at(-1);lastStage.click();lastStage.focus();});
          card.append(conclusion);
        }
        if (turn === answerTurn || staged && turn === latestContrast) {present(step, card, true);if (!staged) flow.append(owner, step, answerReference());}else flow.append(owner, step, card);cards.push(card);continue;
      }
      if (kind === 'lifecycle') {
        const disclosure = createDisclosure('Recorded description activity · ' + label(text(content.state)), content);
        disclosure.setAttribute('data-turn-id', text(turn.id));disclosure.setAttribute('data-turn-kind', kind);flow.append(owner, 1, disclosure);continue;
      }
      if (kind === 'data_audit') {
        const profile = content.profile || (content.observed ? content : currentTurn ? model.profile || content : content);
        const card = auditCard(profile);card.setAttribute('data-turn-id', text(turn.id));if (staged && turn === latestAudit) present(1, card);else flow.append(owner, 1, card);cards.push(card);if (currentTurn) auditShown = true;continue;
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
        if (staged && (!planCard || planStep !== 3) && turn === latestPlan) present(3, card);else flow.append(owner, step, card);cards.push(card);continue;
      }
      if (['job_event', 'scientific_step', 'scientific_operation'].includes(kind)) {
        let summary;
        if (kind === 'job_event') summary = label(text(content.status) || text(content.to)) || keyLabel(text(turn.title));
        else {
          const activity = kind === 'scientific_step' ? content.step || {} : content.operation || {};
          const name = text(activity.node) || text(activity.action) || text(turn.title) || (kind === 'scientific_step' ? 'Scientific step' : 'Scientific operation');
          let status = text(activity.status) || text(content.status);
          if (['active', 'running', 'queued', 'reserved', 'dispatched'].includes(status)) {
            const terminal = model.turns.find(candidate => {
              if (candidate.kind !== kind || candidate.investigation_id !== turn.investigation_id) return false;
              const later = kind === 'scientific_step' ? candidate.content?.step : candidate.content?.operation;
              if (!later || !['completed', 'failed', 'cancelled', 'recovery_required'].includes(later.status)) return false;
              return kind === 'scientific_step' ? later.session_id === activity.session_id && later.node === activity.node && later.attempt === activity.attempt
                : Boolean(activity.operation_id) && later.operation_id === activity.operation_id;
            });
            if (terminal) status = 'finished · ' + (kind === 'scientific_step' ? terminal.content.step.status : terminal.content.operation.status);
            else if (!model.operationPending) status = 'interrupted';
          }
          summary = keyLabel(name) + (status && keyLabel(name).toLowerCase() !== keyLabel(status).toLowerCase() ? ' · ' + keyLabel(status) : '');
        }
        const receiptNode = kind === 'scientific_step' && ['verify', 'recommend', 'conclusion_review'].includes(content.step?.node);
        const disclosure = receiptNode ? element('div', 'wc-tool-step') :
          createDisclosure(summary, {turn_title: text(turn.title), turn_kind: kind, ...content});
        if (receiptNode) disclosure.append(actionButton({type: 'navigate', view: 'evidence', label: summary}, options.onAction));
        else disclosure.className += ' wc-tool-step';
        disclosure.setAttribute('data-turn-id', text(turn.id));disclosure.setAttribute('data-turn-kind', kind);disclosure.setAttribute('data-turn-title', text(turn.title));
        flow.append(owner, step, disclosure, !receiptNode);continue;
      }
      if (kind === 'plan_review') {
        const card = element('article', 'wc-card wc-plan-review');card.setAttribute('data-turn-id', text(turn.id));card.setAttribute('data-turn-kind', kind);
        const planStatus = currentTurn ? model.inlineGate.kind === 'review' ? 'Needs review' : label(model.status) : content.status ? label(content.status) : 'Recorded';
        card.append(cardHeader(text(turn.title) || 'Analysis plan', planStatus, currentTurn && model.inlineGate.kind === 'run' ? 'good' : 'neutral'));
        if (content.proposal && typeof content.proposal === 'object') card.append(metricGrid(planSummary({proposal: content.proposal})));
        if (Object.keys(content).length) card.append(createDisclosure('Plan evidence', content));
        if (staged && (!planCard || planStep !== 3) && turn === latestPlan) present(3, card);else flow.append(owner, step, card);cards.push(card);if (content.proposal && (!turn.investigation_id || turn.investigation_id === investigation.id)) planShown = true;continue;
      }
      const card = element('article', 'wc-card wc-turn wc-turn-' + kind + (turn.role === 'user' ? ' user-message' : ''));
      card.setAttribute('data-turn-id', text(turn.id));card.setAttribute('data-turn-kind', kind);
      card.append(cardHeader(kind === 'user_question' ? 'Your question' : text(turn.title) || keyLabel(kind || 'Recorded turn')));
      if (kind === 'user_question') {
        card.append(element('p', 'wc-message-body', text(content.question) || text(investigation.question)));
        const selection = content.selection || ((!turn.investigation_id || turn.investigation_id === investigation.id) ? model.selection : null);
        if (selection) card.append(element('span', 'wc-status-pill wc-pill-neutral', 'Selected cells (' + (Array.isArray(selection.observation_ids) ? selection.observation_ids.length.toLocaleString('en-US') : 'unavailable') + ')'));
      } else if (kind === 'analyst_action' || kind === 'analyst_query') {
        appendAnalystAction(card, content);
      } else if (kind === 'error') {
        card.append(element('p', 'wc-message-body', failureSummary(text(content.message) || text(content.reason)) || 'Inspect the preserved error evidence.'));
        card.append(createDisclosure('Error evidence', content));
      } else {
        const message = text(content.message) || text(content.summary) || text(content.rationale);
        if (message) card.append(element('p', 'wc-message-body', message));
        if (Object.keys(content).length) card.append(createDisclosure(kind === 'clarification_answer' ? 'Supplied answers' : 'Recorded evidence', content));
      }
      if (staged && currentTurn && ['question_scope', 'clarification_answer', 'error'].includes(kind)) present(kind === 'error' ? (grouped ? 3 : step) : 2, card);
      else flow.append(owner, step, card);
      cards.push(card);
    }
    // A profile is evidence in its own right; an absent turn never creates a completed stage.
    const description = descriptionStatusCard(model, options.onAction);
    if (description) {present(model.request?.cell_type_comparison ? 3 : model.descriptionOperation.status === 'completed' ? 4 : 1, description);cards.push(description);}
    const comparisonSteps = model.descriptionOperation?.comparison_steps;
    if (model.request?.cell_type_comparison && Array.isArray(comparisonSteps)) {
      const card = element('article', 'wc-card wc-research-card');
      const rows = element('ol', 'wc-research-run');
      for (const step of comparisonSteps) {
        const row = element('li', 'wc-research-run-step');
        row.append(element('strong', '', step.title), element('span', 'wc-research-step-status', keyLabel(step.status)),
          element('p', 'wc-research-detail', step.detail));
        rows.append(row);
      }
      card.append(cardHeader('Local analysis steps'), rows);
      present(3, card);cards.push(card);
    }
    const synthesisShowsReview = reviewedSynthesis && model.synthesis?.population_evidence && model.request?.category !== 'descriptive_retrieval';
    const continuation = synthesisContinuationCard(model, options.onAction, !synthesisShowsReview);
    const adaptiveContinuationVisible = !model.synthesis && showFitConclusion;
    if (continuation && (!model.synthesis || !reviewedSynthesis || continuation.eligible)) {
      if (adaptiveContinuationVisible) {
        present(5, continuation, true);
        if (!staged) flow.append(investigation.id, 5, answerReference());
      } else {
        present(5, continuation);
      }
      cards.push(continuation);
    }
    const scientificStatus = scopedScientificStatusCard(model, options.onAction);
    if (scientificStatus) {present(phaseStep, scientificStatus, false, execution);cards.push(scientificStatus);}
    if (model.adaptive) {
      const research = model.adaptiveResearch || {};
      for (const [step, view] of [[3, 'plan'], [5, 'findings']]) {
        const card = element('div', 'wc-adaptive-stage');
        renderAdaptiveResearch(card, research, {
          view, activityFeed:true, feedId:'wc-adaptive-feed-'+investigation.id, compact:true,
          onAction: options.onAction
        });
        present(step, card, true);
      }
    }
    if (!auditShown && model.profile) {const card = auditCard(model.profile);present(1, card);cards.push(card);}
    if (!planShown && model.jobState.proposal) {const card = element('article', 'wc-card wc-plan-review');card.append(cardHeader('Recorded plan'), metricGrid(model.metrics));present(3, card);cards.push(card);}
    if (model.inlineGate.kind === 'clarification') {const card = renderClarification(container, model, options.onAction);present(staged || model.fields.length && model.fields.every(field => field.scope === 'request') ? 2 : 1, card);cards.push(card);}
    else if (model.inlineGate.kind === 'episode_clarification') {const card = renderEpisodeClarification(container, model, options.onAction);present(4, card, false, true);cards.push(card);}
    else if (['scientific_review', 'scientific_execution_review'].includes(model.inlineGate.kind)) {const card = renderScientificGate(container, model, options.onAction);present(model.inlineGate.kind === 'scientific_review' ? 3 : 4, card, false, true);cards.push(card);}
    else if (model.inlineGate.kind === 'review' || model.inlineGate.kind === 'run') {
      const gate = element('article', 'wc-card wc-inline-gate');gate.setAttribute('data-inline-gate', model.inlineGate.kind);
      gate.append(cardHeader(model.inlineGate.kind === 'review' ? 'Review and approve the plan' : 'Ready to execute', model.inlineGate.kind === 'run' ? 'Approved' : 'Needs review', model.inlineGate.kind === 'run' ? 'good' : 'warning'));
      gate.append(element('p', '', model.inlineGate.kind === 'review' ? 'Review the assumptions and confirmations below, then record your decision.' : options.config && options.config.execution_allowed ? 'Start the approved analysis using the run controls below.' : 'Data execution is disabled for this service.'));
      present(model.inlineGate.kind === 'review' ? 3 : 4, gate, false, true);cards.push(gate);
    }
    if (model.synthesis && model.request?.category === 'descriptive_retrieval') {
      const comparison = Boolean(model.request.cell_type_comparison);
      const summary = element('article', 'wc-card wc-description-summary');
      summary.append(cardHeader(comparison ? 'Interpretation scope' : 'Description summary'),
        element('p', 'wc-message-body', comparison
          ? 'These results describe the attached files. A developmental effect requires verified biological replication.'
          : 'This summary covers retrieved dataset information. No scientific analysis or validation is established.'));
      appendEpistemicStrip(summary, model);
      summary.append(createDisclosure('Retained description summary', model.synthesis));
      present(4, summary);cards.push(summary);
    } else if (model.synthesis && reviewedSynthesis) {
      const synthesis = element('article', 'wc-card wc-synthesis');synthesis.append(cardHeader(model.synthesisContinuation?.status === 'abstained' ? 'Reviewed abstention'
        : model.synthesisContinuation?.status === 'completed' ? 'Reviewed conclusion' : 'Scientific synthesis'));
      appendEpistemicStrip(synthesis, model);
      if (model.synthesis.population_evidence) {
        appendReceiptChip(synthesis, model.synthesis.population_evidence, model.synthesis.claim_scope, model.artifacts);
        appendAdaptiveConclusion(synthesis, model.synthesis.population_evidence, options.onAction);
      } else {
        if (model.synthesisContinuation?.status === 'abstained') {
          const trials = model.synthesis.supported_findings?.trial_results;
          synthesis.append(element('p', '', trials?.length === 1
            ? 'One frozen candidate was evaluated. A comparative recommendation requires alternatives evaluated under matched criteria.'
            : 'The reviewed evidence did not support a recommendation.'));
        }
        appendSynthesisSections(synthesis, model.synthesis, model.artifacts);
        if (model.synthesisContinuation?.review) synthesis.append(createDisclosure('Conclusion review assessment', model.synthesisContinuation.review));
        for (const receipt of model.synthesisContinuation?.steps || []) {
          if (receipt.status !== 'completed') continue;
          appendReceiptChip(synthesis, receipt, receipt.claim_scope, model.artifacts);
          const title = keyLabel(receipt.node) + ' receipt';
          const link = element('a', 'secondary', title);
          const receiptId = 'synthesis-receipt-' + model.id + '-' + receipt.node;
          link.setAttribute('href', '#' + receiptId);
          const detail = createDisclosure(title, receipt);detail.setAttribute('id', receiptId);
          link.addEventListener('click', event => {
            event.preventDefault();detail.open = true;
            if (typeof detail.scrollIntoView === 'function') detail.scrollIntoView({block: 'nearest'});
          });
          synthesis.append(link, detail);
        }
      }
      if (model.artifacts.length) synthesis.append(actionButton({type: 'navigate', view: 'results', label: 'Inspect figures and evidence'}, options.onAction, 'secondary wc-artifact-reference'));
      present(5, synthesis, visibleSynthesis);if (visibleSynthesis && !staged) flow.append(investigation.id, 5, answerReference());cards.push(synthesis);
      const followups = element('section', 'wc-followups');followups.append(element('h3', '', 'Continue the chat'));
      const suggestions = element('div', 'wc-suggestions');
      for (const [caption, question] of [
        ['Review quality concerns', 'Which quality concerns can the saved evidence and supported workflow help inspect in this dataset?'],
        ['Inspect batch structure', 'What can the supported batch diagnostics establish for this dataset, and what remains uncertain?'],
        ['Review method scope', 'Which supported methods could address a follow-up question with this dataset, and what additional evidence or metadata would they require?']
      ]) suggestions.append(actionButton({type: 'draft', label: caption, question: model.sources.length > 1 ? question.replace('this dataset', 'these datasets') : question, sourceRunId: investigation.job_id || investigation.parent_job || null, ...(model.sources.length > 1 ? {sources: model.sources.slice()} : {input: text(investigation.source) ? {path: investigation.source} : null})}, options.onAction, 'secondary wc-suggestion'));
      followups.append(suggestions);present(5, followups);
    } else if (model.artifacts.length && !(staged && latestContrast)) {
      const artifacts = element('article', 'wc-card wc-artifact-card');artifacts.append(cardHeader('Analysis artifacts', model.artifacts.length + ' recorded'));
      artifacts.append(actionButton({type: 'navigate', view: 'results', label: 'Open artifact canvas'}, options.onAction));present(4, artifacts);cards.push(artifacts);
    }
    flow.append(investigation.id, 3, createDisclosure(model.request?.category === 'descriptive_retrieval' ? 'Retrieval scope' : 'Planning source', {mode: model.mode, scope: model.scope, ...(model.planningSource ? {record: model.planningSource} : {})}));
    const publicMessage = appendPublicMessage(inline.answer, model, answerTurn, Boolean(publicPlan || visibleSynthesis || adaptiveContinuationVisible || feedbackTurn));
    flow.finish();
    if (staged) panels.forEach((panel, index) => {
      const stageActivity = grouped ? visibleActivity : activity;
      if (!panel.children.length && !(stageActivity?.step === index + 1 && ['running', 'queued'].includes(stageActivity.kind))) {
        panel.append(element('p', 'muted', 'No records for this stage yet.'));
      }
    });
    const publicSignature = JSON.stringify([answerTurn?.id, feedbackTurn?.id, publicPlan ? model.scientificPlan?.preliminary_explanation || model.scientificPlan?.scientific_candidates : null, visibleSynthesis ? model.synthesis : adaptiveContinuationVisible ? model.synthesisContinuation.population_evidence.direct_answer : null, researchFinding(model.adaptiveResearch).answer, publicMessage]);
    const reveal = changedRun || following && inline.publicSignature !== publicSignature;inline.publicSignature = publicSignature;
    restoreView();
    if (reveal && scroller && typeof inline.answer.getBoundingClientRect === 'function') {
      scroller.scrollTop += inline.answer.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
    }
    animateCards(cards, changedRun);return model;
  }
  root.WorkbenchConversation = Object.freeze({buildModel, buildActivityModel, render: renderConversationWorkspace, renderConversationWorkspace, failureSummary, planSummary, buildInvestigationModel, renderInvestigation, renderAdaptiveResearch});
})(typeof window !== 'undefined' ? window : globalThis);
