/* Local investigation coordinator. Requests use the host's authenticated boundary. */
(function (root) {
  'use strict';
  const ACTIVE = new Set(['queued', 'inspecting', 'planning', 'recording_review', 'preparing', 'running', 'cancel_requested', 'finalizing']);
  const MAX_REQUEST_BYTES = 65536;
  const MAX_SOURCES = 8;
  const copy = value => value == null ? value : JSON.parse(JSON.stringify(value));
  const text = value => typeof value === 'string' ? value : '';
  const bytes = value => typeof TextEncoder === 'function' ? new TextEncoder().encode(JSON.stringify(value)).byteLength : JSON.stringify(value).length * 3;
  const basename = value => text(value).split('/').pop() || 'Dataset';
  const label = value => text(value).replaceAll('_', ' ').replace(/^./, char => char.toUpperCase()) || 'Status unavailable';
  function normalizeSources(values) {
    if (!Array.isArray(values) || values.length > MAX_SOURCES || values.some(value => typeof value !== 'string' || !value || value.length > 4096) || new Set(values).size !== values.length) throw new Error('Attach up to eight distinct project datasets.');
    return values.slice();
  }
  const recordSources = record => record ? normalizeSources(Array.isArray(record.sources) && record.sources.length ? record.sources : record.source ? [record.source] : []) : [];
  function normalizeSelection(value) {
    if (!value || value.display_only !== true) throw new Error('Only an explicit saved-figure selection can be attached.');
    const ids = value.observation_ids;
    if (!Array.isArray(ids) || !ids.length || ids.length > 1000 || !ids.every(id => typeof id === 'string' && id.length > 0 && id.length <= 200) || new Set(ids).size !== ids.length) {
      throw new Error('Attach at most 1,000 unique saved observation IDs. Export a larger selection instead.');
    }
    const result = {figure_id: text(value.figure_id), panel_id: text(value.panel_id), observation_ids: ids.slice(), source_job: text(value.source_job || value.context && value.context.job_id), parent_root: text(value.parent_root || value.context && value.context.parent_root), display_only: true};
    if (!result.figure_id || !result.panel_id || !/^[0-9a-f]{32}$/.test(result.source_job) || !/^[0-9a-f]{64}$/.test(result.parent_root)) throw new Error('A selection needs its exact figure, panel, source run and verified parent identity.');
    if (bytes(result) > 50000) throw new Error('This selection exceeds the attachment limit. Export its exact IDs or choose a smaller selection.');
    return result;
  }
  function mount(options) {
    options = options || {};
    if (typeof options.request !== 'function') throw new Error('An authenticated request function is required.');
    const doc = options.document || root.document;
    if (!doc || !options.questionInput || !options.datasetSelect || !options.conversationContainer) throw new Error('Question, dataset and conversation elements are required.');
    const request = options.request, config = options.config || {};
    const find = id => doc.getElementById ? doc.getElementById(id) : null;
    const inputTrigger = options.inputTrigger || find('dataset-attachments-trigger'), inputCaption = options.inputCaption || find('dataset-attachments-caption'), inputPickerList = options.inputPickerList || find('dataset-attachments-list'), inputChips = options.inputChips || find('dataset-attachment-chips');
    const agentAvailable = () => Boolean(config.proposal_selection && config.proposal_selection.mode === 'codex' && config.proposal_selection.available === true && config.model_access === true);
    const later = options.setTimeout || root.setTimeout && root.setTimeout.bind(root), cancel = options.clearTimeout || root.clearTimeout && root.clearTimeout.bind(root);
    let current = null, inputs = [], history = [], profile = null, selection = null, pending = false, disposed = false;
    let poll = null, generation = 0, profileGeneration = 0, lastRendered = '', lastHistory = '', jobBinding = '', parentJob = '', refreshPromise = null, draftOrigin = null;
    let chosenSources = [], attachmentChecks = new Map(), inspectionFocus = '', inputsLoaded = false;
    const sourceProfiles = new Map(), inspectionStamps = new Map();
    const listeners = [];
    const make = (tag, className, value) => { const node = doc.createElement(tag); if (className) node.className = className; if (value !== undefined) node.textContent = String(value); return node; };
    const listen = (node, event, fn) => { if (!node) return; node.addEventListener(event, fn); listeners.push([node, event, fn]); };
    const notify = (message, error) => { if (!disposed && typeof options.onStatus === 'function') options.onStatus(message, Boolean(error)); };
    const source = () => text(options.datasetSelect.value);
    const selectedSources = () => chosenSources.length > 1 ? chosenSources.slice() : source() ? [source()] : [];
    const unavailableSources = () => inputsLoaded ? selectedSources().filter(path => !inputs.some(input => (input.source || input.path) === path)) : [];
    const sameSources = (left, right) => left.length === right.length && left.every((path, index) => path === right[index]);
    const setBusy = value => { pending = value; options.datasetSelect.disabled = value; if (inputTrigger) inputTrigger.disabled = value; renderAttachments(false); if (options.modeSelect) options.modeSelect.disabled = value; if (options.submitButton) options.submitButton.disabled = value; if (typeof options.onBusy === 'function') options.onBusy(value); };
    function useSources(values) {
      chosenSources = normalizeSources(values); const select = options.datasetSelect, path = chosenSources.length === 1 ? chosenSources[0] : '';
      if (path && !Array.from(select.options || select.children || []).some(option => option.value === path)) { const option = make('option', '', basename(path)); option.value = path; select.append(option); }
      select.value = path;
    }
    function syncAttachmentChecks() { const selected = selectedSources(); for (const [path, checkbox] of attachmentChecks) { checkbox.checked = selected.includes(path); checkbox.disabled = pending || !checkbox.checked && selected.length >= MAX_SOURCES; } }
    function renderAttachments(rebuild) {
      const selected = selectedSources();
      if (inputTrigger) (inputCaption || inputTrigger).textContent = selected.length ? selected.length + (selected.length === 1 ? ' dataset' : ' datasets') : 'Add datasets';
      if (inputChips) {
        inputChips.replaceChildren();
        for (const path of selected) {
          const unavailable = inputsLoaded && !inputs.some(input => (input.source || input.path) === path);
          const chip = make('span', 'dataset-attachment-chip' + (unavailable ? ' is-unavailable' : '')), name = make('span', '', basename(path) + (unavailable ? ' · Unavailable' : '')), remove = make('button', 'secondary', '×'); remove.type = 'button'; remove.disabled = pending; remove.setAttribute('aria-label', 'Remove ' + basename(path)); name.title = path;
          remove.onclick = () => { setSources(selectedSources().filter(value => value !== path)); if (inputTrigger) inputTrigger.focus(); }; chip.append(name, remove); inputChips.append(chip);
        }
      }
      if (rebuild && inputPickerList) {
        inputPickerList.replaceChildren(); attachmentChecks = new Map();
        const choices = inputs.slice();
        for (const path of selected) if (!choices.some(row => (row.source || row.path) === path)) choices.push({source: path, filename: basename(path), retained: true});
        for (const input of choices) {
          const path = text(input.source || input.path); if (!path) continue;
          const row = make('label', 'dataset-attachment-option'), checkbox = make('input'); checkbox.type = 'checkbox'; checkbox.setAttribute('aria-label', 'Attach ' + basename(path));
          const caption = make('span', '', text(input.filename) || basename(path)); caption.title = path; row.append(checkbox, caption); attachmentChecks.set(path, checkbox);
          checkbox.onchange = () => { try { const paths = selectedSources(); setSources(checkbox.checked ? paths.includes(path) ? paths : [...paths, path] : paths.filter(value => value !== path), false); if (checkbox.checked) inspect(path).catch(error => notify(error.message, true)); } catch (error) { syncAttachmentChecks(); notify(error.message, true); } };
          inputPickerList.append(row);
        }
        if (!choices.length) inputPickerList.append(make('p', 'empty', 'Add data to this project to attach it here.'));
      }
      syncAttachmentChecks();
    }
    function setSources(values, rebuild = true) {
      if (pending) throw new Error('Wait for the current request before changing attached datasets.');
      const next = normalizeSources(values), changed = !sameSources(selectedSources(), next); useSources(next); draftOrigin = null;
      if (changed && selection) { selection = null; parentJob = ''; renderSelection(); notify('The saved-cell selection was removed because the attached datasets changed.'); }
      renderAttachments(rebuild); return next.slice();
    }
    function checkedRequest(path, body) {
      if (body !== undefined && bytes(body) > MAX_REQUEST_BYTES) return Promise.reject(new Error('The question and exact selection exceed the request limit. Export the selection or shorten the question; no IDs were removed.'));
      return request(path, body);
    }
    function actionPath() {
      if (!current || !/^[0-9a-f]{32}$/.test(current.id)) throw new Error('Select a chat first.');
      return '/api/investigations/' + current.id;
    }
    function clearPoll() { if (poll !== null && cancel) cancel(poll); poll = null; }
    function schedule() {
      clearPoll();
      if (!disposed && current && (ACTIVE.has(current.status) || current.operation_pending === true || current.job_state && ACTIVE.has(current.job_state.status)) && later) poll = later(() => { poll = null; refresh().catch(error => notify(error.message, true)); }, options.pollMilliseconds || 1600);
    }
    function renderSelection() {
      const host = options.selectionHost; if (!host) return;
      host.replaceChildren(); host.hidden = !selection;
      if (!selection) return;
      const chip = make('div', 'selection-chip'), caption = make('span', '', 'Selected cells (' + selection.observation_ids.length.toLocaleString() + ')');
      caption.setAttribute('title', 'Exact saved observation IDs from run ' + selection.source_job.slice(0, 8) + ' · ' + selection.figure_id + ' · ' + selection.panel_id);
      const remove = make('button', 'secondary selection-remove', 'Remove'); remove.type = 'button'; remove.setAttribute('aria-label', 'Remove selected-cell context');
      remove.addEventListener('click', () => { selection = null; renderSelection(); notify('Selected-cell context removed.'); });
      chip.append(caption, remove); host.append(chip);
    }
    function renderInputs(preferredSource, rebuildAttachments = true) {
      const select = options.datasetSelect;
      if (preferredSource !== undefined) useSources(Array.isArray(preferredSource) ? preferredSource : preferredSource ? [preferredSource] : []);
      const selectedSet = selectedSources(), selected = selectedSet.length === 1 ? selectedSet[0] : '';
      select.replaceChildren();
      const empty = make('option', '', inputs.length ? 'Choose a dataset' : 'No registered input files found'); empty.value = ''; select.append(empty);
      const choices = inputs.slice();
      for (const path of selectedSet) if (!choices.some(row => (row.source || row.path) === path)) choices.push({source: path, filename: basename(path), retained: true});
      for (const input of choices) {
        const path = text(input.source || input.path); if (!path) continue;
        const option = make('option', '', text(input.filename) || basename(path)); option.value = path; select.append(option);
      }
      select.value = selected;
      renderAttachments(rebuildAttachments);
      const host = options.inputsContainer; if (!host) return;
      host.replaceChildren();
      const summary = make('p', 'dataset-discovery-summary', inputs.length ? inputs.length + ' files in the configured input folders' : 'No supported files were found in the configured input folders.'); host.append(summary);
      const list = make('div', 'dataset-list');
      for (const input of inputs) {
        const path = text(input.source || input.path); if (!path) continue;
        const button = make('button', 'secondary dataset-row'); button.type = 'button'; button.setAttribute('aria-current', String(selectedSet.includes(path)));
        button.append(make('span', 'dataset-row-name', text(input.filename) || basename(path)));
        const parts = [];
        if (Number.isFinite(input.size_bytes)) parts.push((input.size_bytes / 1024).toLocaleString(undefined, {maximumFractionDigits: 1}) + ' KiB');
        parts.push(input.profile ? 'Inspected' : input.hash_status === 'computed' || input.sha256 ? 'Identity available' : 'Not inspected');
        button.append(make('span', 'dataset-row-meta', parts.join(' · '))); button.setAttribute('title', path);
        button.addEventListener('click', () => { inspect(path).catch(error => notify(error.message, true)); }); list.append(button);
      }
      host.append(list);
      if (profile && (selectedSet.includes(text(profile.source || profile.path)) || inputs.some(input => (input.source || input.path) === text(profile.source || profile.path)))) {
        const box = make('section', 'dataset-profile'), heading = make('h3', '', 'Dataset profile'), observed = profile.observed || {}, grid = make('dl', 'wc-kv-grid');
        box.append(heading);
        const rows = [['File', basename(profile.source || profile.path)], ['Cells', observed.cells], ['Genes', observed.genes], ['Observation columns', observed.obs_columns], ['Layers', observed.layers], ['Embedding keys', observed.obsm_keys]];
        for (const [name, value] of rows) if (value !== undefined && value !== null) grid.append(make('dt', '', name), make('dd', '', Array.isArray(value) ? value.join(', ') || 'None recorded' : value));
        box.append(grid);
        if (Array.isArray(profile.unknowns) && profile.unknowns.length) { const list = make('ul', 'dataset-unknowns'); for (const unknown of profile.unknowns.slice(0, 12)) list.append(make('li', '', typeof unknown === 'string' ? unknown : text(unknown.message || unknown.field))); box.append(list); }
        host.append(box);
      }
    }
    function historyTone(status) { return ['completed_review_passed', 'completed_review_failed', 'completed'].includes(status) ? 'good' : ['awaiting_operator_review', 'awaiting_information', 'needs_information', 'approved'].includes(status) ? 'warning' : ACTIVE.has(status) ? 'active' : ['failed', 'operation_failed', 'refusal', 'rejected'].includes(status) ? 'bad' : 'neutral'; }
    function publishHistory() {
      const fingerprint = JSON.stringify(history);
      if (fingerprint === lastHistory) return;
      lastHistory = fingerprint;
      if (typeof options.onHistory === 'function') options.onHistory(copy(history));
    }
    function syncHistoryRecord() {
      if (!current) return;
      const keys = ['id', 'revision', 'question', 'source', 'sources', 'status', 'job_id', 'created_at', 'updated_at', 'operation_pending', 'scientific_session_id', 'gate'];
      const summary = {};
      for (const key of keys) if (current[key] !== undefined) summary[key] = copy(current[key]);
      const index = history.findIndex(row => row.id === current.id);
      if (index < 0) history = [summary, ...history];
      else history = history.map((row, position) => position === index ? {...row, ...summary} : row);
      publishHistory();
    }
    function renderHistory() {
      const host = options.historyContainer; if (!host) return;
      host.replaceChildren();
      if (!history.length) { host.append(make('p', 'empty', 'Your chats will appear here.')); return; }
      for (const row of history.slice(0, 40)) {
        if (!/^[0-9a-f]{32}$/.test(text(row.id))) continue;
        const button = make('button', 'investigation-thread'); button.type = 'button'; button.setAttribute('aria-current', String(current && row.id === current.id));
        button.append(make('span', 'investigation-thread-title', text(row.question) || 'Untitled chat'));
        const meta = make('span', 'investigation-thread-meta');
        meta.append(make('span', 'dataset-pill', Array.isArray(row.sources) && row.sources.length > 1 ? row.sources.length + ' datasets' : basename(row.source)), make('span', 'wc-status-pill wc-pill-' + historyTone(row.status), label(row.status)));
        button.append(meta); button.addEventListener('click', () => open(row.id).catch(error => notify(error.message, true))); host.append(button);
      }
    }
    function render(force) {
      if (disposed) return null;
      const state = current && (current.job_state || null);
      const fingerprint = JSON.stringify([current && current.id, current && current.revision, current && current.status, state && state.status, current && (current.thread_turns || current.turns), current && current.synthesis]);
      if (!force && fingerprint === lastRendered) return null;
      lastRendered = fingerprint;
      let model;
      const conversation = options.conversation || root.WorkbenchConversation;
      if (conversation && typeof conversation.renderInvestigation === 'function') {
        model = conversation.renderInvestigation(options.conversationContainer, {investigation: copy(current), config, jobState: copy(state), onAction: action => { handleAction(action).catch(error => notify(error.message, true)); }});
      } else if (conversation && typeof conversation.render === 'function') {
        model = conversation.render(options.conversationContainer, {state, config, onAction: action => { handleAction(action).catch(error => notify(error.message, true)); }});
      }
      const status = current && current.status;
      const kind = status === 'awaiting_operator_review' ? 'review' : status === 'approved' ? 'run' : ['awaiting_information', 'needs_information'].includes(status) ? 'clarification' : null;
      const gate = model && model.inlineGate || {kind, jobId: current && current.job_id, investigationId: current && current.id, revision: current && current.revision};
      if (typeof options.onGate === 'function') options.onGate({...gate, investigation: copy(current), jobState: copy(state)});
      return model;
    }
    async function loadHistory() {
      const result = await checkedRequest('/api/investigations'); if (disposed) return;
      history = Array.isArray(result && result.investigations) ? result.investigations : []; publishHistory(); renderHistory();
    }
    async function loadInputs() {
      const result = await checkedRequest('/api/inputs'); if (disposed) return;
      inputs = Array.isArray(result && result.inputs) ? result.inputs : []; inputsLoaded = true; renderInputs();
      if (unavailableSources().length) notify('Some attached datasets are unavailable in this project. Remove or replace them before sending; your draft is preserved.', true);
      else if (result && result.truncated) notify('The input list reached its bounded scan limit. Inspect an exact allowed path in dataset settings.');
    }
    async function inspect(path) {
      if (disposed || !path) { profile = null; renderInputs(); return null; }
      const stamp = ++profileGeneration; inspectionStamps.set(path, stamp); inspectionFocus = path; notify('Inspecting ' + basename(path) + '…');
      const result = await checkedRequest('/api/inputs/inspect', {source: path});
      if (disposed || inspectionStamps.get(path) !== stamp || !selectedSources().includes(path) && !inputs.some(input => (input.source || input.path) === path)) return null;
      sourceProfiles.set(path, copy(result)); if (inspectionFocus === path) profile = result;
      const existing = inputs.find(row => (row.source || row.path) === path);
      if (existing) { existing.profile = copy(result); existing.sha256 = result.sha256 || existing.sha256; }
      renderInputs(undefined, false); notify(basename(path) + ' metadata inspected. Experimental declarations still need your confirmation.'); return copy(result);
    }
    async function adopt(value, stamp) {
      if (disposed || stamp !== undefined && stamp !== generation) return null;
      if (!value || !/^[0-9a-f]{32}$/.test(text(value.id))) throw new Error('The service returned an invalid chat identity.');
      const changedThread = !current || current.id !== value.id;
      current = copy(value); const paths = recordSources(current); useSources(paths);
      for (const item of Array.isArray(current.source_profiles) ? current.source_profiles : []) if (item && text(item.source || item.path)) sourceProfiles.set(item.source || item.path, copy(item));
      profile = paths.length > 1 ? sourceProfiles.get(paths[0]) || null : current.profile || profile; syncHistoryRecord();
      const binding = current.job_id || '';
      if (binding && binding !== jobBinding) { if (typeof options.onJob === 'function') await options.onJob(binding, copy(current)); if (!disposed && (stamp === undefined || stamp === generation)) jobBinding = binding; } else if (!binding) jobBinding = ''; 
      if (disposed || stamp !== undefined && stamp !== generation) return null;
      const update = () => { renderInputs(paths); renderHistory(); render(); schedule(); };
      if (changedThread && typeof options.transition === 'function') await options.transition(update); else update();
      return copy(current);
    }
    async function open(id) {
      if (!/^[0-9a-f]{32}$/.test(text(id))) throw new Error('Use a retained chat identity.');
      const preservedDraft = text(options.questionInput.value).trim() && selectedSources().length ? {sources: selectedSources(), investigationId: current && current.id || '', jobId: parentJob || current && current.job_id || ''} : null;
      if (preservedDraft && !draftOrigin) draftOrigin = preservedDraft;
      const stamp = ++generation; clearPoll(); selection = null; parentJob = ''; renderSelection(); notify('Opening chat…');
      const result = await checkedRequest('/api/investigations/' + id);
      if (stamp !== generation || disposed) return null;
      jobBinding = '';
      await adopt(result, stamp);
      if (preservedDraft && stamp === generation && !disposed) { renderInputs(preservedDraft.sources); notify('Your unsent draft remains attached to ' + (preservedDraft.sources.length > 1 ? preservedDraft.sources.length + ' datasets' : basename(preservedDraft.sources[0])) + '.'); } else notify('');
      return copy(current);
    }
    function refresh() {
      if (refreshPromise) return refreshPromise;
      if (!current || disposed) return Promise.resolve(null);
      const id = current.id, stamp = generation;
      refreshPromise = checkedRequest('/api/investigations/' + id).then(value => { if (disposed || !current || current.id !== id || stamp !== generation) return null; return adopt(value, stamp); }).catch(error => { schedule(); throw error; }).finally(() => { refreshPromise = null; });
      return refreshPromise;
    }
    async function mutate(action, fields) {
      if (pending) throw new Error('Wait for the current request to finish.');
      const path = actionPath(), stamp = generation, id = current.id;
      const body = {action, expected_revision: current.revision, ...copy(fields || {})};
      setBusy(true);
      try { const result = await checkedRequest(path, body); if (disposed || stamp !== generation || !current || current.id !== id) return null; const adopted = await adopt(result, stamp); await loadHistory(); return adopted; }
      catch (error) { await refresh().catch(() => null); notify(error.message, true); throw error; }
      finally { setBusy(false); }
    }
    async function submit() {
      if (pending || disposed) return null;
      if (config.project_catalog && config.project_catalog.read_only === true) { notify('Read-only project. Create or select a working project to start a chat.', true); return null; }
      const draft = text(options.questionInput.value), question = draft.trim(), paths = selectedSources();
      if (!question) { notify('Ask a biological question to begin.'); options.questionInput.focus(); return null; }
      if (question.length > 8000) { notify('Keep the question within 8,000 characters.', true); return null; }
      if (!paths.length) { notify('Choose a dataset or attach several project files for this question.'); (inputTrigger || options.datasetSelect).focus(); return null; }
      if (!inputsLoaded) { notify('Project data is still loading. Wait for the available file list before sending.', true); return null; }
      if (unavailableSources().length) { notify('Some attached datasets are unavailable in this project. Remove or replace them before sending; your draft is preserved.', true); return null; }
      const mode = 'agent';
      if (!agentAvailable()) { notify('Connect a model to send a message.', true); return null; }
      const fields = {question, proposal_mode: mode};
      if (paths.length > 1) { fields.sources = paths.slice(); fields.source_declarations = paths.map(() => ({})); } else fields.source = paths[0];
      if (paths.length > 1) fields.scientific_context = {auto_advance: true};
      if (selection && paths.length > 1) { notify('A saved-cell selection is scoped to one analysis. Remove it or attach a single dataset.', true); return null; }
      if (selection) fields.selection = copy(selection);
      if (options.requirementsProvider) { const requirements = options.requirementsProvider(); if (requirements && requirements.length) fields.user_parameter_requirements = copy(requirements); }
      if (options.scientificContextProvider) { const context = options.scientificContextProvider(); if (context) fields.scientific_context = {...(fields.scientific_context || {}), ...copy(context)}; }
      const sameDataset = current && sameSources(recordSources(current), paths) && (!draftOrigin || draftOrigin.investigationId === current.id);
      const relevantParent = (job, originSources) => Boolean(job && (paths.length === 1 || originSources.some(path => paths.includes(path))));
      let endpoint = '/api/investigations';
      if (sameDataset) { endpoint += '/' + current.id; delete fields.source; delete fields.sources; delete fields.source_declarations; fields.action = 'follow_up'; fields.expected_revision = current.revision; }
      else if (draftOrigin) { if (draftOrigin.investigationId) fields.parent_investigation = draftOrigin.investigationId; if (relevantParent(draftOrigin.jobId, draftOrigin.sources || [])) fields.parent_job = draftOrigin.jobId; }
      else if (current) { fields.parent_investigation = current.id; if (relevantParent(current.job_id, recordSources(current))) fields.parent_job = current.job_id; }
      else if ((parentJob || selection) && paths.length === 1) fields.parent_job = parentJob || selection.source_job;
      const selectionAtSubmit = selection;
      const stamp = ++generation; clearPoll(); setBusy(true); notify('Recording your question and inspecting the attached data…');
      try {
        const result = await checkedRequest(endpoint, fields); if (disposed || stamp !== generation) return null;
        if (options.questionInput.value === draft) { options.questionInput.value = ''; draftOrigin = null; }
        if (selection === selectionAtSubmit) { selection = null; parentJob = ''; } renderSelection();
        await adopt(result, stamp); await loadHistory(); notify('Question recorded. Review any requested experimental context in the thread.'); return copy(current);
      } catch (error) { notify(error.message, true); schedule(); return null; }
      finally { setBusy(false); }
    }
    function newThread() {
      generation++; profileGeneration++; clearPoll(); current = null; jobBinding = ''; selection = null; parentJob = ''; draftOrigin = null; lastRendered = ''; options.questionInput.value = ''; if (options.modeSelect) options.modeSelect.value = 'agent'; renderSelection(); renderHistory(); render(true);
      if (typeof options.onNewThread === 'function') options.onNewThread(); options.questionInput.focus(); notify('');
    }
    function setSelection(value) {
      try { if (selectedSources().length > 1) throw new Error('Saved-cell selections belong to one analysis. Attach a single dataset before using this selection.'); selection = normalizeSelection(value); parentJob = selection.source_job; renderAttachments(true); renderSelection(); options.questionInput.focus(); notify(selection.observation_ids.length.toLocaleString() + ' exact cell identities attached. Add a follow-up question.'); return copy(selection); }
      catch (error) { notify(error.message, true); return null; }
    }
    async function handleAction(action) {
      if (!action || disposed) return null;
      if (action.type === 'clarify') { const fields = {}; if (Array.isArray(action.source_declarations)) fields.source_declarations = copy(action.source_declarations); else fields.declarations = copy(action.declarations || {}); if (action.scientificContext || action.scientific_context) fields.scientific_context = copy(action.scientificContext || action.scientific_context); return mutate('clarify', fields); }
      if (action.type === 'review') return review(action);
      if (action.type === 'run') return run({allow_data_execution: action.allow_data_execution === true});
      if (action.type === 'scientific') {
        const fields = {request: copy(action.request)};
        if (action.operatorReview !== undefined) fields.operator_review = copy(action.operatorReview);
        return mutate('scientific', fields);
      }
      if (action.type === 'navigate' || action.type === 'artifact') { if (typeof options.onCanvas === 'function') await options.onCanvas(action.view || 'results', copy(action)); return null; }
      if (action.type === 'draft') {
        options.questionInput.value = text(action.question); parentJob = text(action.sourceRunId) || current && current.job_id || parentJob;
        if (Array.isArray(action.sources)) useSources(action.sources); else if (action.input && action.input.path) useSources([action.input.path]); renderInputs();
        options.questionInput.focus(); notify('Follow-up drafted. Submit it to prepare a separate plan.'); return null;
      }
      if (action.type === 'open') return open(action.investigationId);
      return null;
    }
    function review(value) {
      value = value || {};
      return mutate('review', {reviewer: text(value.reviewer), decision: text(value.decision), rationale: text(value.rationale), confirmations: copy(value.confirmations || {})});
    }
    function run(value) {
      if (!value || value.allow_data_execution !== true) return Promise.reject(new Error('Starting an analysis requires an explicit run action with data-execution permission.'));
      return mutate('run', {allow_data_execution: true});
    }
    function destroy() { disposed = true; generation++; profileGeneration++; clearPoll(); for (const [node, event, fn] of listeners) if (typeof node.removeEventListener === 'function') node.removeEventListener(event, fn); listeners.length = 0; }
    listen(options.datasetSelect, 'change', () => { draftOrigin = null; profile = null; useSources(source() ? [source()] : []); renderAttachments(true); inspect(source()).catch(error => notify(error.message, true)); });
    if (options.modeSelect) {
      options.modeSelect.replaceChildren();
      for (const [value, caption] of [['agent', 'Agent']]) { const option = make('option', '', caption); option.value = value; option.disabled = value === 'agent' && !agentAvailable(); options.modeSelect.append(option); }
      options.modeSelect.value = 'agent';
    }
    const api = {current: () => copy(current), selectedSources, setSources, refresh, render, setSelection, newThread, open, submit, handleAction, review, run, inspect, reloadInputs: loadInputs, reloadHistory: loadHistory, destroy};
    if (options.initialSources !== undefined) useSources(normalizeSources(options.initialSources));
    renderSelection(); render(true);
    api.ready = Promise.allSettled([loadInputs(), loadHistory()]).then(results => { for (const result of results) if (result.status === 'rejected') notify(result.reason && result.reason.message || 'Could not load chat context.', true); return api; });
    return api;
  }
  root.WorkbenchInvestigations = Object.freeze({mount, normalizeSelection});
})(typeof window !== 'undefined' ? window : globalThis);
