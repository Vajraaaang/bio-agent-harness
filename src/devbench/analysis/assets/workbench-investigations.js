/* Local investigation coordinator. Requests use the host's authenticated boundary. */
(function (root) {
  'use strict';
  const ACTIVE = new Set(['queued', 'inspecting', 'planning', 'recording_review', 'preparing', 'running', 'cancel_requested', 'cancelling', 'finalizing']);
  const HUMAN_GATES = new Set(['awaiting_operator_review', 'awaiting_information', 'needs_information', 'approved', 'recovery_required']);
  const DESCRIPTION_TERMINAL = new Set(['recovery_required', 'operation_failed', 'failed', 'cancelled', 'completed']);
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
    const agentAvailable = () => config.proposal_selection?.available === true &&
      (config.proposal_selection.mode === 'recorded_fixture' ||
        config.proposal_selection.mode === 'codex' && config.model_access === true);
    const descriptionAvailable = () => config.investigations && config.investigations.descriptive_retrieval === true;
    const modelStatus = () => agentAvailable() ? '' : 'No model selector is connected. Local read-only descriptions are available with data, and bounded local matrix diagnostics require data execution. Model-planned analyses require a configured selector.';
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
    const unavailableSources = (paths = selectedSources()) => inputsLoaded ? paths.filter(path => !inputs.some(input => (input.source || input.path) === path)) : [];
    const sameSources = (left, right) => left.length === right.length && left.every((path, index) => path === right[index]);
    let messageQueue = [], sendingItem = null, studioDraftThread = 0;
    const parkedQueues = new Map();
    function switchQueue(identity) {
      const previous = current?.id || '';
      if (previous === identity) return;
      if (messageQueue.length) parkedQueues.set(previous, messageQueue);
      else parkedQueues.delete(previous);
      messageQueue = parkedQueues.get(identity) || [];
      parkedQueues.delete(identity);
      renderQueueTray();
    }
    const queueTray = make('div', 'wc-queue-tray');
    queueTray.hidden = true;
    const queueParent = options.questionInput.parentNode;
    const canQueue = typeof queueParent?.insertBefore === 'function';
    if (canQueue) queueParent.insertBefore(queueTray, options.questionInput);
    function renderQueueTray() {
      if (!messageQueue.length) {
        queueTray.hidden = true;
        if (queueTray.replaceChildren) queueTray.replaceChildren();
        return;
      }
      queueTray.hidden = false;
      const header = make('div', 'wc-queue-header');
      const queueStatus = isWorking() ? 'Sends when the chat is ready' :
        messageQueue[0].problem ? 'A message needs attention' :
        HUMAN_GATES.has(current?.status) ? 'Waiting for your review or answer' : 'Ready to send';
      header.append(make('span', '', 'Queued Messages (' + messageQueue.length + ') · ' + queueStatus));
      const list = make('div', 'wc-queue-list');
      messageQueue.forEach(item => {
        const row = make('div', 'wc-queue-item');
        const details = make('div', 'wc-queue-summary');
        details.append(make('span', 'wc-queue-text', item.text));
        if (item.problem) details.append(make('p', 'wc-queue-error', item.problem));
        row.append(details);
        const actions = make('div', 'wc-queue-actions');
        const send = make('button', 'wc-steer-btn', isWorking() ? 'Send next' : item.problem ? 'Retry' : 'Send Now');
        send.type = 'button';
        send.onclick = () => {
          if (item === sendingItem || !messageQueue.includes(item)) return;
          if (!isWorking()) return flushQueue(item);
          messageQueue.splice(messageQueue.indexOf(item), 1);
          messageQueue.unshift(item);
          renderQueueTray();
          notify('Message moved to the front of the queue.');
        };
        const remove = make('button', 'secondary', '✕');
        remove.type = 'button';
        remove.onclick = () => {
          if (item === sendingItem || !messageQueue.includes(item)) return;
          messageQueue.splice(messageQueue.indexOf(item), 1);
          renderQueueTray();
          flushQueue();
        };
        send.disabled = item === sendingItem;
        remove.disabled = item === sendingItem;
        const edit = make('button', 'secondary', 'Edit');
        edit.type = 'button';
        edit.disabled = pending;
        edit.onclick = () => {
          if (pending || !messageQueue.includes(item)) return;
          if (options.questionInput.value.trim()) {notify('Clear the composer before editing a queued message.', true);return;}
          options.questionInput.value = item.draft;
          draftOrigin = copy(item.origin);
          selection = copy(item.fields.selection) || null;
          parentJob = item.parentJob;
          renderInputs(item.sources);
          renderSelection();
          messageQueue.splice(messageQueue.indexOf(item), 1);
          renderQueueTray();
          options.questionInput.focus();
        };
        remove.setAttribute('aria-label', 'Remove queued message');
        actions.append(send, edit, remove);
        row.append(actions);
        list.append(row);
      });
      if (queueTray.replaceChildren) queueTray.replaceChildren(header, list);
    }
    function flushQueue(requestedItem) {
      renderQueueTray();
      if (disposed || isWorking()) return;
      const item = requestedItem || messageQueue[0];
      if (!item || !messageQueue.includes(item)) return;
      if (!requestedItem && (!current || HUMAN_GATES.has(current.status) || item.problem)) return;
      return sendMessage(item, true);
    }
    let adaptiveOverride = null;
    const activeAdaptive = () => adaptiveOverride?.session === current?.scientific_session_id &&
      adaptiveOverride?.activity?.status === 'running' ||
      ['active','queued','running'].includes(current?.adaptive_research?.activity?.current?.status);
    const activeWork = () => Boolean(current && (
      ACTIVE.has(current.status) || current.operation_pending === true || activeAdaptive() ||
      ACTIVE.has(current.job_state?.status) || !DESCRIPTION_TERMINAL.has(current.status) &&
      ['queued','running','cancel_requested'].includes(current.description_operation?.status)));
    const isWorking = () => pending || activeWork();
    const setBusy = (value, activity) => {
      pending = value;
      options.datasetSelect.disabled = value;
      if (inputTrigger) inputTrigger.disabled = value;
      if (options.submitButton) options.submitButton.disabled = value && !canQueue;
      renderAttachments(false);
      if (options.modeSelect) options.modeSelect.disabled = value;
      if (typeof options.onBusy === 'function') options.onBusy(value, activity);
      renderQueueTray();
    };
    function useSources(values) {
      chosenSources = normalizeSources(values); const select = options.datasetSelect, path = chosenSources.length === 1 ? chosenSources[0] : '';
      if (path && !Array.from(select.options || select.children || []).some(option => option.value === path)) { const option = make('option', '', basename(path)); option.value = path; select.append(option); }
      select.value = path;
    }
    function syncAttachmentChecks() { const selected = selectedSources(); for (const [path, checkbox] of attachmentChecks) { checkbox.checked = selected.includes(path); checkbox.disabled = pending || !checkbox.checked && selected.length >= MAX_SOURCES; } }
    function studioRows(selected) {
      const bound = current ? recordSources(current) : [];
      return selected.slice(0, MAX_SOURCES).map(path => {
        const listed = inputs.find(row => (row.source || row.path) === path);
        const index = bound.indexOf(path);
        const observed = sourceProfiles.get(path) || listed?.profile || {};
        const declarations = index < 0 ? {} : bound.length > 1 ?
          current.source_declarations?.[index] : current.declarations;
        const planned = index === 0 && bound.length === 1 ? current.scientific_plan?.context?.task : null;
        const datasetProfile = {...observed, declarations: {
          ...(planned?.assay ? {assay: planned.assay} : {}),
          ...observed.declarations, ...declarations,
        }};
        return {...listed, source: path, filename: listed?.filename || basename(path), profile: datasetProfile};
      });
    }
    function publishStudioContext(rows = studioRows(selectedSources())) {
      const studio = root.WorkbenchContext;
      if (!studio || typeof options.onContext !== 'function') return;
      const sessionId = text(current?.scientific_session_id);
      const revision = current?.revision;
      options.onContext({
        scope: current?.id || 'draft:' + studioDraftThread,
        datasets: rows.map(row => studio.entryForDataset(row, row.profile)).filter(Boolean),
        session: sessionId ? {kind: 'session', id: sessionId, label: sessionId.slice(0, 8),
          detail: Number.isInteger(revision) ? 'Investigation revision ' + revision : ''} : null,
        selection: selection ? {kind: 'selection', id: selection.figure_id + ':' + selection.panel_id,
          label: 'Selected cells (' + selection.observation_ids.length.toLocaleString() + ')',
          detail: 'Saved figure selection'} : null,
        readOnly: config.project_catalog?.read_only === true,
      });
    }
    function renderWelcomeAudit() {
      const welcomeHost = find('welcome-source-audit'), chipsHost = find('welcome-quick-chips');
      const selected = selectedSources(), rows = studioRows(selected);
      publishStudioContext(rows);
      if (chipsHost && root.WorkbenchContext?.renderWayfinders) {
        root.WorkbenchContext.renderWayfinders(chipsHost, {
          datasets: rows,
          readOnly: config.project_catalog?.read_only === true,
          onChooseData: options.onChooseData || (() => (inputTrigger || options.datasetSelect)?.click()),
          onDraft: question => {
            options.questionInput.value = question;
            options.questionInput.focus();
          },
        });
      }
      if (!welcomeHost || !welcomeHost.replaceChildren) return;
      welcomeHost.replaceChildren();
      const activePath = selected[0] || inspectionFocus || '';
      if (!activePath) return;
      const activeProfile = sourceProfiles.get(activePath) || inputs.find(row => (row.source || row.path) === activePath)?.profile;
      const observed = activeProfile?.observed || {};
      const card = make('div', 'wc-source-audit');
      const header = make('div', 'wc-card-header');
      header.append(make('h4', '', basename(activePath)), make('span', 'wc-status-pill', activeProfile ? 'Inspected' : 'Selected'));
      card.append(header);
      if (Number.isFinite(observed.cells) && Number.isFinite(observed.genes)) {
        card.append(make('p', 'wc-audit-summary', observed.cells.toLocaleString('en-US') + ' observations × ' + observed.genes.toLocaleString('en-US') + ' features'));
      }
      if (Array.isArray(observed.obs_columns) && observed.obs_columns.length) {
        card.append(make('p', 'wc-field-reason', 'Observed columns: ' + observed.obs_columns.join(', ') + '.'));
      }
      welcomeHost.append(card);
      if (chipsHost && chipsHost.append && !root.WorkbenchContext?.renderWayfinders) {
        if (chipsHost.replaceChildren) chipsHost.replaceChildren();
        for (const [badge, question] of [
          ['Contrast', 'Which comparisons does this dataset support?'],
          ['Design', 'Which columns identify biological units and groups?'],
          ['QC', 'What quality checks are appropriate for this dataset?']
        ]) {
          const chip = make('button', 'quick-chip');
          chip.type = 'button';
          chip.append(make('span', 'chip-badge', badge), make('span', '', ' ' + question));
          chip.onclick = () => {
            if (!selectedSources().length) setSources([activePath]);
            options.questionInput.value = question;
            options.questionInput.focus();
          };
          chipsHost.append(chip);
        }
      }
    }
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
      renderWelcomeAudit();
      if (rebuild && inputPickerList) {
        inputPickerList.replaceChildren(); attachmentChecks = new Map();
        const choices = inputs.filter(row => row.analysis_size_supported !== false ||
          selected.includes(row.source || row.path));
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
      if (!current && typeof options.onDraftDataset === 'function') {
        const path = selected.length === 1 ? selected[0] : '';
        const inspected = path && (sourceProfiles.get(path) || inputs.find(row => (row.source || row.path) === path)?.profile);
        options.onDraftDataset(path, inspected || null);
      }
    }
    function setSources(values, rebuild = true) {
      if (pending) throw new Error('Wait for the current request before changing attached datasets.');
      const next = normalizeSources(values), changed = !sameSources(selectedSources(), next); useSources(next); draftOrigin = null;
      if (changed) {
        parentJob = '';
        if (selection) { selection = null; renderSelection(); notify('The saved-cell selection was removed because the attached datasets changed.'); }
      }
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
      if (disposed || !later || !activeWork()) return;
      poll = later(() => {
        poll = null;
        refresh().catch(error => notify(error.message, true));
      }, options.pollMilliseconds || 1600);
    }
    function renderSelection() {
      publishStudioContext();
      const host = options.selectionHost; if (!host) return;
      host.replaceChildren(); host.hidden = !selection;
      if (!selection) return;
      const chip = make('div', 'selection-chip'), caption = make('span', '', 'Selected cells (' + selection.observation_ids.length.toLocaleString() + ')');
      caption.setAttribute('title', 'Exact saved observation IDs from run ' + selection.source_job.slice(0, 8) + ' · ' + selection.figure_id + ' · ' + selection.panel_id);
      const actions = make('div', 'selection-quick-actions');
      const markerBtn = make('button', 'secondary selection-quick-btn', 'Markers');
      markerBtn.type = 'button';
      markerBtn.setAttribute('title', 'Draft question to identify marker genes for this selected subpopulation');
      markerBtn.addEventListener('click', () => {
        options.questionInput.value = 'Identify canonical marker genes and characteristic expression signatures for these ' + selection.observation_ids.length.toLocaleString() + ' selected cells.';
        options.questionInput.focus();
        notify('Drafted marker gene question with exact selection attached.');
      });
      const diffBtn = make('button', 'secondary selection-quick-btn', 'Differential expression');
      diffBtn.type = 'button';
      diffBtn.setAttribute('title', 'Draft question to compare expression vs remaining cells');
      diffBtn.addEventListener('click', () => {
        options.questionInput.value = 'Perform differential expression testing comparing these ' + selection.observation_ids.length.toLocaleString() + ' selected cells against background cells.';
        options.questionInput.focus();
        notify('Drafted differential expression question with exact selection attached.');
      });
      actions.append(markerBtn, diffBtn);
      const remove = make('button', 'secondary selection-remove', 'Remove'); remove.type = 'button'; remove.setAttribute('aria-label', 'Remove selected-cell context');
      remove.addEventListener('click', removeSelection);
      chip.append(caption, actions, remove); host.append(chip);
    }
    function removeSelection() {
      const previous = selection;
      selection = null;
      if (previous && !draftOrigin && parentJob === previous.source_job) parentJob = '';
      renderSelection();
      notify('Selected-cell context removed.');
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
        option.disabled = input.analysis_size_supported === false;
      }
      select.value = selected;
      renderAttachments(rebuildAttachments);
      const host = options.inputsContainer; if (!host) return;
      host.replaceChildren();
      const summary = make('p', 'dataset-discovery-summary', inputs.length ? inputs.length + ' files in the configured input folders' : 'No supported files were found in the configured input folders.'); host.append(summary);
      const list = make('div', 'dataset-list');
      for (const input of inputs) {
        const path = text(input.source || input.path); if (!path) continue;
        const filename = text(input.filename) || basename(path);
        const transferOnly = input.analysis_size_supported === false;
        const row = make('div', 'dataset-transfer-row');
        const button = make('button', 'secondary dataset-row'); button.type = 'button'; button.setAttribute('aria-current', String(selectedSet.includes(path)));
        button.disabled = transferOnly;
        button.append(make('span', 'dataset-row-name', filename));
        const parts = [];
        if (Number.isFinite(input.size_bytes)) {
          const gib = input.size_bytes >= 1024 ** 3;
          parts.push((input.size_bytes / (gib ? 1024 ** 3 : 1024)).toLocaleString(undefined, {maximumFractionDigits: 1}) +
            (gib ? ' GiB' : ' KiB'));
        }
        parts.push(transferOnly ? 'Over the analysis size limit' :
          input.profile ? 'Inspected' : input.hash_status === 'computed' || input.sha256 ? 'Identity available' : 'Not inspected');
        button.append(make('span', 'dataset-row-meta', parts.join(' · '))); button.setAttribute('title', path);
        button.addEventListener('click', () => { inspect(path).catch(error => notify(error.message, true)); });
        row.append(button);
        if (typeof input.download_url === 'string' && input.download_url.startsWith('/api/inputs/download?name=')) {
          const download = make('a', 'dataset-download', 'Download');
          download.href = '/api/inputs/download?' + new URLSearchParams({name: filename});
          download.setAttribute('download', filename);
          download.setAttribute('aria-label', 'Download ' + filename);
          row.append(download);
        }
        list.append(row);
      }
      host.append(list);
      if (profile && (selectedSet.includes(text(profile.source || profile.path)) || inputs.some(input => (input.source || input.path) === text(profile.source || profile.path)))) {
        const box = make('section', 'dataset-profile'), heading = make('h3', '', 'Dataset profile'), observed = profile.observed || {}, grid = make('dl', 'wc-kv-grid');
        box.append(heading);
        const rows = [['File', basename(profile.source || profile.path)], ['Observations', observed.cells], ['Features', observed.genes], ['Observation columns', observed.obs_columns], ['Layers', observed.layers], ['Embedding keys', observed.obsm_keys]];
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
      const keys = ['id', 'revision', 'question', 'source', 'sources', 'status', 'job_id', 'created_at', 'updated_at', 'operation_pending', 'scientific_session_id', 'gate', 'request', 'description_operation'];
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
        const statusLabel = row.status === 'completed' && row.request?.category === 'descriptive_retrieval' ? 'Description complete' : label(row.status);
        meta.append(make('span', 'dataset-pill', Array.isArray(row.sources) && row.sources.length > 1 ? row.sources.length + ' datasets' : row.source ? basename(row.source) : 'No dataset'), make('span', 'wc-status-pill wc-pill-' + historyTone(row.status), statusLabel));
        button.append(meta); button.addEventListener('click', () => open(row.id).catch(error => notify(error.message, true))); host.append(button);
      }
    }
    function render(force) {
      if (disposed) return null;
      const state = current && (current.job_state || null);
      const display = copy(current);
      if (display?.workflow_mode === 'adaptive/1' && adaptiveOverride?.session === display.scientific_session_id) {
        const research = display.adaptive_research || {}, activity = adaptiveOverride.activity;
        display.adaptive_research = {...research, activity: {...research.activity,
          ...(activity.status === 'running' ? {current:activity} : {current:null,last:activity})}};
      }
      const fingerprint = JSON.stringify([current && current.id, current && current.revision, current && current.status, state, current && (current.thread_turns || current.turns), current && current.clarification_fields, current && current.artifacts, current && current.synthesis, current && current.synthesis_continuation, current && current.request, current && current.description_operation, current && current.operation_pending, current && current.singlecell_trial, current && current.scientific_plan, current && current.error, current && current.diagnostic_code, current && current.required_action, display?.adaptive_research]);
      if (!force && fingerprint === lastRendered) return null;
      lastRendered = fingerprint;
      let model;
      const conversation = options.conversation || root.WorkbenchConversation;
      if (conversation && typeof conversation.renderInvestigation === 'function') {
        model = conversation.renderInvestigation(options.conversationContainer, {investigation: display, config, jobState: copy(state), onAction: action => { handleAction(action).catch(error => notify(error.message, true)); }});
      } else if (conversation && typeof conversation.render === 'function') {
        model = conversation.render(options.conversationContainer, {state, config, onAction: action => { handleAction(action).catch(error => notify(error.message, true)); }});
      }
      const status = current && current.status;
      const kind = status === 'awaiting_operator_review' ? 'review' : status === 'approved' ? 'run' : ['awaiting_information', 'needs_information'].includes(status) ? 'clarification' : null;
      const gate = model && model.inlineGate || {kind, jobId: current && current.job_id, investigationId: current && current.id, revision: current && current.revision};
      renderWelcomeAudit();
      if (typeof options.onGate === 'function') options.onGate({...gate, investigation: display, jobState: copy(state)});
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
    async function adopt(value, stamp, draftSources) {
      if (disposed || stamp !== undefined && stamp !== generation) return null;
      if (!value || !/^[0-9a-f]{32}$/.test(text(value.id))) throw new Error('The service returned an invalid chat identity.');
      const selectedProject = text(config.project_catalog && config.project_catalog.current_project_id);
      const turns = Array.isArray(value.thread_turns) ? value.thread_turns : Array.isArray(value.turns) ? value.turns : [];
      const projects = [value.project_id, ...turns.flatMap(turn => [turn?.binding?.project_id, turn?.content?.binding?.project_id])].filter(project => text(project));
      if (selectedProject && projects.some(project => project !== selectedProject)) throw new Error('The returned chat belongs to a different project.');
      const changedThread = !current || current.id !== value.id;
      const previous = current?.adaptive_research, research = value.adaptive_research;
      if (!changedThread && previous && research?.status === 'running' &&
          research.session_id === previous?.session_id) {
        value = {...value, adaptive_research:{plan:previous.plan,steps:previous.steps,...research}};
      }
      const chosen = selectedSources();
      const preserveAttachments = !changedThread && !sameSources(recordSources(current), chosen) ||
        pending && Boolean(options.questionInput.value.trim());
      current = copy(value); const paths = draftSources || (preserveAttachments ? chosen : recordSources(current)); useSources(paths);
      for (const item of Array.isArray(current.source_profiles) ? current.source_profiles : []) if (item && text(item.source || item.path)) sourceProfiles.set(item.source || item.path, copy(item));
      profile = paths.length > 1 ? sourceProfiles.get(paths[0]) || null : current.profile || profile; syncHistoryRecord();
      const binding = current.job_id || '';
      if (binding && binding !== jobBinding) { if (typeof options.onJob === 'function') await options.onJob(binding, copy(current)); if (!disposed && (stamp === undefined || stamp === generation)) jobBinding = binding; } else if (!binding) jobBinding = '';
      if (disposed || stamp !== undefined && stamp !== generation) return null;
      const update = () => { renderInputs(paths); renderHistory(); render(); schedule(); };
      if (changedThread && typeof options.transition === 'function') await options.transition(update); else update();
      flushQueue();
      return copy(current);
    }
    async function open(id) {
      if (!/^[0-9a-f]{32}$/.test(text(id))) throw new Error('Use a retained chat identity.');
      if (pending) throw new Error('Wait until the current message is recorded before switching chats.');
      const preservedDraft = text(options.questionInput.value).trim() && selectedSources().length ? {sources: selectedSources(), investigationId: current && current.id || '',
        jobId: parentJob || current && current.job_id || '', jobSource: current?.job_state?.source || current?.source || ''} : null;
      if (preservedDraft && !draftOrigin) draftOrigin = preservedDraft;
      const stamp = ++generation; clearPoll(); notify('Opening chat…');
      try {
        const result = await checkedRequest('/api/investigations/' + id);
        if (stamp !== generation || disposed) return null;
        if (result?.id !== id) throw new Error('The service returned a different chat.');
        switchQueue(id);
        if (!preservedDraft) {selection = null; parentJob = '';}
        renderSelection(); jobBinding = '';
        await adopt(result, stamp, preservedDraft?.sources);
        if (stamp === generation && !disposed) {
          notify(preservedDraft ? 'Your unsent draft remains attached to ' + (preservedDraft.sources.length > 1 ? preservedDraft.sources.length + ' datasets' : basename(preservedDraft.sources[0])) + '.' : '');
        }
        return copy(current);
      } catch (error) {
        schedule();
        throw error;
      }
    }
    function refresh() {
      if (refreshPromise) return refreshPromise;
      if (!current || disposed) return Promise.resolve(null);
      const id = current.id, stamp = generation;
      refreshPromise = checkedRequest('/api/investigations/' + id).then(value => { if (disposed || !current || current.id !== id || stamp !== generation) return null; return adopt(value, stamp); }).catch(error => { schedule(); throw error; }).finally(() => { refreshPromise = null; });
      return refreshPromise;
    }
    function setAdaptiveActivity(session, activity) {
      if (session !== current?.scientific_session_id) return;
      adaptiveOverride = activity ? {session, activity} : null;
      render();
      schedule();
      flushQueue();
    }
    async function mutate(action, fields) {
      if (config.project_catalog?.read_only === true) throw new Error('This project is read-only.');
      if (pending) throw new Error('Wait for the current request to finish.');
      const path = actionPath(), stamp = generation, id = current.id;
      const body = {...copy(fields || {}), action, expected_revision: current.revision};
      const activity = {
        clarify: [3, 'Checking clarifications and preparing the next step'],
        review: [3, 'Recording the operator decision'],
        run: [4, 'Submitting the reviewed trial'],
        cancel: [4, 'Requesting cancellation'],
        resume: [4, 'Requesting recovery'],
        continue_synthesis: [5, 'Requesting the next conclusion step']
      }[action] || (action === 'scientific' && fields.request?.action === 'run_trial' ? [4, 'Submitting the declared trial'] : [3, 'Recording the scientific decision']);
      setBusy(true, activity);
      try { const result = await checkedRequest(path, body); if (disposed || stamp !== generation || !current || current.id !== id) return null; const adopted = await adopt(result, stamp); await loadHistory(); return adopted; }
      catch (error) { await refresh().catch(() => null); notify(error.message, true); throw error; }
      finally { setBusy(false); flushQueue(); }
    }
    function captureMessage(draft) {
      const question = draft.trim(), paths = selectedSources();
      const fields = {question: options.formatContextDraft?.(question) || question, proposal_mode: 'agent'};
      if (!config.investigations?.adaptive_interactive &&
          config.investigations?.analyst_profile === 'analyst_v1' &&
          paths.filter(path => /\.csv(\.gz)?$/i.test(path)).length >= 2 &&
          paths.every(path => /\.(csv|tsv|json)(\.gz)?$/i.test(path))) fields.analyst_profile = 'analyst_v1';
      if (paths.length > 1) { fields.sources = paths.slice(); fields.source_declarations = paths.map(() => ({})); }
      else if (paths.length === 1) fields.source = paths[0];
      if (selection && paths.length !== 1) throw new Error('A saved-cell selection is scoped to one analysis. Remove it or attach a single dataset.');
      if (selection) fields.selection = copy(selection);
      if (options.requirementsProvider) { const requirements = options.requirementsProvider(); if (requirements && requirements.length) fields.user_parameter_requirements = copy(requirements); }
      if (options.scientificContextProvider) { const context = options.scientificContextProvider(); if (context) fields.scientific_context = {...(fields.scientific_context || {}), ...copy(context)}; }
      return {draft, text: question, sources: paths, fields, origin: copy(draftOrigin), parentJob};
    }
    function validateMessage(item) {
      if (config.project_catalog?.read_only === true) throw new Error('Read-only project. Create or select a working project to start a chat.');
      if (item.fields.question.length > 8000) throw new Error('Keep the question within 8,000 characters.');
      if (item.sources.length && !inputsLoaded) throw new Error('Project data is still loading. Wait for the available file list before sending.');
      if (unavailableSources(item.sources).length) throw new Error('Some attached datasets are unavailable. Remove or replace them before sending.');
      if (item.sources.length && !agentAvailable() && !descriptionAvailable()) throw new Error('Connect a model to plan analyses on attached data.');
    }
    function consumeDraft() {
      options.questionInput.value = '';
      draftOrigin = null;
      selection = null;
      parentJob = '';
      renderSelection();
    }
    async function submit() {
      if (disposed) return null;
      const draft = text(options.questionInput.value);
      if (!draft.trim()) { notify('Enter a message first.'); options.questionInput.focus(); return null; }
      let item;
      try {
        item = captureMessage(draft);
        validateMessage(item);
      } catch (error) {
        notify(error.message, true);
        return null;
      }
      if (isWorking()) {
        if (!canQueue) return null;
        if (messageQueue.length >= 8) {notify('Queue up to eight messages at a time.', true);return null;}
        messageQueue.push(item);
        consumeDraft();
        renderQueueTray();
        notify('Message queued. It will send when the chat is ready.');
        return null;
      }
      return sendMessage(item, false);
    }
    async function sendMessage(item, queued) {
      const {sources: paths, origin} = item;
      const fields = copy(item.fields);
      const sameDataset = paths.length && current && sameSources(recordSources(current), paths) &&
        (!origin || origin.investigationId === current.id);
      let endpoint = '/api/investigations';
      if (sameDataset) {
        endpoint += '/' + current.id;
        delete fields.source; delete fields.sources; delete fields.source_declarations; delete fields.analyst_profile;
        fields.action = 'follow_up'; fields.expected_revision = current.revision;
      } else {
        const parent = origin?.investigationId || current?.id;
        const job = origin?.jobId || current?.job_id;
        const jobSource = origin ? origin.jobSource : current?.job_state?.source || current?.source;
        if (parent) fields.parent_investigation = parent;
        if (fields.selection) fields.parent_job = fields.selection.source_job;
        else if (job && paths.includes(jobSource)) fields.parent_job = job;
        else if (!parent && item.parentJob && paths.length === 1) fields.parent_job = item.parentJob;
      }
      try {
        validateMessage(item);
        if (bytes(fields) > MAX_REQUEST_BYTES) throw new Error('The question and exact selection exceed the request limit.');
      } catch (error) {
        if (queued) { item.problem = error.message; renderQueueTray(); }
        notify(error.message, true);
        return null;
      }
      const stamp = ++generation; clearPoll();
      const recording = paths.length ? 'Recording your question and inspecting the attached data…' : 'Recording your question…';
      if (queued) { sendingItem = item; item.problem = null; }
      else consumeDraft();
      setBusy(true, [paths.length ? 1 : 2, recording]); notify(recording);
      try {
        const result = await checkedRequest(endpoint, fields); if (disposed || stamp !== generation) return null;
        const adopted = await adopt(result, stamp);
        if (queued && messageQueue.includes(item)) messageQueue.splice(messageQueue.indexOf(item), 1);
        await loadHistory().catch(() => null);
        notify('Question recorded. Review any requested experimental context in the thread.');
        return adopted;
      } catch (error) {
        if (queued) item.problem = error.message;
        else if (!options.questionInput.value.trim()) {
          options.questionInput.value = item.draft;
          draftOrigin = copy(origin);
          selection = copy(fields.selection) || null;
          parentJob = item.parentJob;
          renderInputs(paths);
          renderSelection();
        } else {
          item.problem = error.message;
          messageQueue.unshift(item);
        }
        notify(error.message, true);
        if (error.status === 409) await refresh().catch(() => null);
        schedule();
        return null;
      } finally {
        sendingItem = null;
        setBusy(false);
        flushQueue();
      }
    }
    function newThread() {
      if (pending) {notify('Wait until the current message is recorded before opening a new chat.', true);return;}
      switchQueue('');
      studioDraftThread++;
      generation++; profileGeneration++; clearPoll(); current = null; jobBinding = ''; selection = null; parentJob = ''; draftOrigin = null; lastRendered = ''; options.questionInput.value = ''; renderQueueTray(); if (options.modeSelect) options.modeSelect.value = 'agent'; renderSelection(); renderHistory(); render(true);
      if (typeof options.onNewThread === 'function') options.onNewThread(); options.questionInput.focus(); notify(modelStatus());
    }
    function setSelection(value) {
      try { if (selectedSources().length > 1) throw new Error('Saved-cell selections belong to one analysis. Attach a single dataset before using this selection.'); selection = normalizeSelection(value); parentJob = selection.source_job; renderAttachments(true); renderSelection(); options.questionInput.focus(); notify(selection.observation_ids.length.toLocaleString() + ' exact cell identities attached. Add a follow-up question.'); return copy(selection); }
      catch (error) { notify(error.message, true); return null; }
    }
    async function handleAction(action) {
      if (!action || disposed) return null;
      if (action.investigationId && (!current || action.investigationId !== current.id) || ['clarify_episode', 'cancel'].includes(action.type) && action.expectedRevision !== undefined && (!current || action.expectedRevision !== current.revision)) throw new Error('The chat changed. Review the current question before applying this action.');
      if (action.type === 'clarify') { const fields = {}; if (typeof action.question === 'string') fields.question = action.question; if (Array.isArray(action.source_declarations)) fields.source_declarations = copy(action.source_declarations); else fields.declarations = copy(action.declarations || {}); if (action.scientificContext || action.scientific_context) fields.scientific_context = copy(action.scientificContext || action.scientific_context); return mutate('clarify', fields); }
      if (action.type === 'review') return review(action);
      if (action.type === 'run') return run({allow_data_execution: action.allow_data_execution === true, allow_model_execution: action.allow_model_execution === true});
      if (action.type === 'clarify_episode') return mutate('clarify_episode', {response: copy(action.response), allow_data_execution: true, allow_model_execution: true});
      if (action.type === 'cancel') return mutate('cancel');
      if (action.type === 'resume' && current && current.job_state && current.job_state.operation_kind === 'agentic_episode' && (current.job_state.handoff || current.job_state.information_request)) {
        render(true);
        const answer = options.conversationContainer.querySelector && options.conversationContainer.querySelector('[name="episode_answer"]');
        if (answer) {if (typeof answer.scrollIntoView === 'function') answer.scrollIntoView({block: 'center'});if (typeof answer.focus === 'function') answer.focus();}
        notify('Answer the experimental-context question and confirm the frozen settings to continue analysis.');return null;
      }
      if (action.type === 'resume') return mutate('resume', {allow_data_execution: true, allow_model_execution: Boolean(current && current.scientific_context && current.scientific_context.allow_model_execution === true)});
      if (action.type === 'continue_synthesis') return mutate(action.type, copy(action.fields));
      if (action.type === 'scientific') {
        const fields = {request: copy(action.request)};
        if (action.operatorReview !== undefined) fields.operator_review = copy(action.operatorReview);
        return mutate('scientific', fields);
      }
      if (action.type === 'navigate' || action.type === 'artifact') { if (typeof options.onCanvas === 'function') await options.onCanvas(action.view || 'results', copy(action)); return null; }
      if (action.type === 'draft') {
        options.questionInput.value = text(action.question); parentJob = text(action.sourceRunId) || current && current.job_id || parentJob;
        if (Array.isArray(action.sources)) useSources(action.sources); else if (action.input && action.input.path) useSources([action.input.path]); renderInputs();
        options.questionInput.focus(); notify(current?.workflow_mode === 'adaptive/1'
          ? 'Follow-up drafted for the current research workflow.'
          : 'Follow-up drafted. Submit it to prepare a separate plan.'); return null;
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
      const fields = {allow_data_execution: true};
      if (current && current.analyst_profile === 'analyst_v1') fields.allow_model_execution = value.allow_model_execution === true;
      return mutate('run', fields);
    }
    function destroy() { disposed = true; generation++; profileGeneration++; clearPoll(); for (const [node, event, fn] of listeners) if (typeof node.removeEventListener === 'function') node.removeEventListener(event, fn); listeners.length = 0; }
    listen(options.datasetSelect, 'change', () => { draftOrigin = null; parentJob = ''; profile = null; useSources(source() ? [source()] : []); renderAttachments(true); inspect(source()).catch(error => notify(error.message, true)); });
    if (options.modeSelect) {
      options.modeSelect.replaceChildren();
      for (const [value, caption] of [['agent', 'Agent']]) { const option = make('option', '', caption); option.value = value; option.disabled = value === 'agent' && !agentAvailable(); options.modeSelect.append(option); }
      options.modeSelect.value = 'agent';
    }
    const api = {current: () => copy(current), isWorking, selectedSources, setSources, refresh, render, setSelection, removeSelection, newThread, open, submit, handleAction, review, run, inspect, reloadInputs: loadInputs, reloadHistory: loadHistory, destroy, setAdaptiveActivity};
    if (options.initialSources !== undefined) useSources(normalizeSources(options.initialSources));
    renderSelection(); render(true);
    api.ready = Promise.allSettled([loadInputs(), loadHistory()]).then(results => {
      const failure = results.find(result => result.status === 'rejected');
      if (failure) notify(failure.reason?.message || 'Could not load chat context.', true);
      else if (!current && !isWorking() && !agentAvailable()) notify(modelStatus());
      return api;
    });
    return api;
  }
  function mountAdaptivePanel({host, request, document = root.document, onModeChange,
    requestFigure, download, taskCentered = false, onActivity, onSettled, onView, onResearch,
    onMotorFigures}) {
    let session = null, generation = 0, view = null, readOnly = false, submitting = false, scope = null;
    let allowOfflineApproval = false;
    let cancelling = false, cancelNotice = null, liveStatus = null, imageURL = null;
    let ledgerOpen = false, activityNotice = null, aimId = null, pluginTimer = null;
    const lineageState = {};
    const make = (tag, value, className) => {
      const node = document.createElement(tag);
      if (value !== undefined) node.textContent = String(value);
      if (className) node.className = className;
      return node;
    };
    const endpoint = () => '/api/scientific/adaptive/' + session;
    const researchHost = root.WorkbenchResearch ? make('div') : null;
    const executionHost = researchHost && make('div', undefined, 'research-adaptive-graph');
    const research = researchHost && root.WorkbenchResearch.mount({
      host: researchHost, request, download, executionHost, onReadback: onResearch, onMutation: async () => {
        await load();
        onSettled?.(session);
      },
    });
    const button = (value, action) => {
      const node = make('button', value, 'secondary');
      node.type = 'button';
      node.onclick = action;
      return node;
    };
    const checkbox = (value, onchange) => {
      const node = make('input');
      node.type = 'checkbox';
      node.checked = false;
      node.setAttribute('aria-label', value);
      node.onchange = onchange;
      const wrapper = make('label', undefined, 'check');
      wrapper.append(node, make('span', value));
      return [node, wrapper];
    };
    function status(message) {
      if (liveStatus) liveStatus.textContent = message;
    }
    function offlinePlans() {
      const plans = view?.private_workflow?.plans;
      return Array.isArray(plans) ? plans : [];
    }
    function pluginWorkflowActive() {
      return [view?.shared_motor_umap, ...offlinePlans()].some(plan =>
        ['queued', 'running'].includes(plan?.status));
    }
    function pollPluginWorkflows() {
      root.clearTimeout(pluginTimer);
      pluginTimer = null;
      if (pluginWorkflowActive())
        pluginTimer = root.setTimeout(() => load(), 1800);
    }
    async function load(cursor = null, retryRevision = true) {
      if (session === null) return;
      const mark = ++generation;
      try {
        const page = await request(endpoint() + (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''));
        if (generation !== mark) return;
        if (!page || page.session_id !== session) throw new Error('The workflow changed. Select the session again.');
        const changed = view?.mode !== page.mode;
        view = page;
        render();
        if (!cursor) onView?.(page);
        if (changed && onModeChange) onModeChange(page.mode);
      } catch (error) {
        if (generation !== mark) return;
        const revisionConflict = !cursor && error.status === 409 &&
          error.message === 'adaptive_view_revision_changed';
        if (revisionConflict) {
          if (pluginWorkflowActive()) {
            status('Workflow changed during refresh. Trying again…');
            return;
          }
          if (retryRevision) return load(null, false);
        }
        if ([403, 404, 409].includes(error.status)) {
          view = {session_id: session, mode: 'adaptive/1', status: 'unavailable'};
          research?.select(null);
          render();
          onView?.(view);
        }
        status(revisionConflict ? 'Workflow changed during refresh. Refresh to see its current state.' :
          error.message || 'The current workflow view is unavailable.');
      } finally {
        if (!cursor && generation === mark) pollPluginWorkflows();
      }
    }
    async function launchReviewedPlan(path, plan) {
      const expected = session, requestScope = scope;
      submitting = true;
      try {
        await request(endpoint() + path, {
          plan_id: plan.plan_id, plan_sha256: plan.plan_sha256, allow_data_execution: true,
        });
        if (session === expected && scope === requestScope) await load();
      } finally {
        if (session === expected && scope === requestScope) submitting = false;
      }
    }
    async function launchMotorUmap() {
      const plan = view?.shared_motor_umap;
      if (readOnly || submitting || plan?.status !== 'awaiting_review' || plan.can_run !== true)
        throw new Error('Refresh Workflows to review the current plan.');
      return launchReviewedPlan('/motor-umap', plan);
    }
    async function launchPrivatePlan(reviewed, displayedView) {
      const plan = offlinePlans().find(item => item?.plan_id === reviewed.plan_id &&
        item.plan_sha256 === reviewed.plan_sha256);
      if (view !== displayedView || (readOnly && !allowOfflineApproval) || submitting ||
          plan?.status !== 'awaiting_review' || plan.can_run !== true)
        throw new Error('Refresh Workflows to review the current plan.');
      return launchReviewedPlan('/plugin-execute', plan);
    }
    async function act(name, data, model, openModel = false, operator = null) {
      const checked = name === 'run_checked_step' ? view?.actions?.run_checked_step : operator;
      const allowed = ['run_checked_step', 'run_operator_step'].includes(name) ? Boolean(checked) :
        openModel ? view?.actions?.model_available === true : view?.actions?.[name] === true;
      if (submitting || cancelling || readOnly || !allowed) return;
      const expected = session, requestScope = scope;
      const useModel = !checked && (openModel || view.actions.model_required === true);
      if (!data.checked || (useModel && !model.checked)) return;
      const body = {action: name, expected_revision: view.revision, allow_data_execution: true,
        ...(checked || {allow_model_execution: useModel})};
      submitting = true;
      activityNotice = {title:useModel ? 'Codex is selecting the next step' : 'Running the approved workflow step',status:'running'};
      onActivity?.(session,activityNotice);
      render();
      status(activityNotice.title);
      let message, failed = false;
      try {
        const result = await request(endpoint(), body);
        message = 'Workflow updated. Steps dispatched in this request: ' + result.steps_dispatched + '.';
      } catch (error) {
        failed = true;
        message = error.status === 409
          ? 'The workflow changed. Review the refreshed revision before granting execution.'
          : error.message || 'The workflow action could not be completed.';
      } finally {
        if (session === expected && scope === requestScope) {
          await load();
          submitting = false;
          render();
          if (session === expected && scope === requestScope && !cancelling && !cancelNotice) {
            activityNotice = {title: failed ? 'Workflow action needs attention' : 'Workflow action recorded',
              detail: message,status:failed ? 'failed' : 'recorded'};
            onActivity?.(session,activityNotice);
            onSettled?.(session);
            status(message);
          }
        }
      }
    }
    function operatorBulk(parent) {
      const bulk = view.operator_bulk;
      if (!bulk?.queue && !bulk?.step_id) return;
      const group = make('section', undefined, 'research-operator-bulk');
      group.append(make('h4', 'Operator Bulk DE'));
      parent.append(group);
      if (bulk.preflight) {
        if (bulk.contrast) group.append(make('p',
          bulk.contrast.numerator + ' vs ' + bulk.contrast.denominator +
          ' · ' + bulk.feature_count + ' tested features in the frozen family', 'field-note'));
        const value = bulk.preflight;
        group.append(make('p', 'Q-DESIGN · rank ' + value.rank + ' / ' + value.columns +
          ' · residual degrees of freedom ' + value.residual_df, 'field-note'));
        if (bulk.completed_reviews?.length) group.append(make('p',
          'Completed reviews · ' + bulk.completed_reviews.map(label).join(', '), 'field-note'));
      }
      const touchpoint = bulk.review?.[0];
      if (readOnly || (!bulk.queue && !touchpoint)) return;
      const form = make('form', undefined, 'research-form');
      const assessment = {};
      if (touchpoint) {
        for (const [key, caption] of [['initial', 'Evidence assessment'], ['final', 'Final review rationale']]) {
          const control = make('textarea');
          control.required = true;
          control.maxLength = 4000;
          const field = make('label', caption);
          field.append(control); form.append(field);
          assessment[key] = control;
        }
      }
      const [consent, field] = checkbox(touchpoint ? 'Confirm this ' + touchpoint +
        ' review for the frozen bulk design' : 'Allow local design preflight for the declared contrast');
      consent.required = true;
      form.append(field);
      const submit = make('button', touchpoint ? 'Complete ' + touchpoint + ' review' : 'Queue Bulk DE');
      submit.type = 'submit';
      form.append(submit);
      form.onsubmit = async event => {
        event.preventDefault();
        if (submitting || cancelling || !consent.checked) return;
        const expected = session, requestScope = scope;
        submitting = true;
        submit.disabled = true;
        let errorMessage = null;
        try {
          await request(endpoint(), {
            action: touchpoint ? 'review_operator_bulk' : 'queue_operator_bulk',
            expected_revision: view.revision, allow_data_execution: true,
            ...(touchpoint ? {touchpoint, assessment: Object.fromEntries(
              Object.entries(assessment).map(([key, input]) => [key, input.value])
            )} : {}),
          });
          if (session === expected && scope === requestScope) await load();
        } catch (error) {
          errorMessage = error.message || 'The bulk action could not be completed.';
        } finally {
          if (session === expected && scope === requestScope) {
            submitting = false;
            render();
            if (errorMessage) status(errorMessage);
          }
        }
      };
      group.append(form);
    }

    async function requestCancellation() {
      if (cancelling || readOnly || view?.actions?.cancel !== true) return;
      const expected = session, requestScope = scope, path = endpoint();
      const current = () => session === expected && scope === requestScope;
      cancelling = true;
      render();
      status('Checking the current workflow before cancellation…');
      let message;
      try {
        const latest = await request(path);
        if (!current() || readOnly) return;
        if (latest.session_id !== expected || latest.actions?.cancel !== true || !Number.isInteger(latest.revision)) {
          throw new Error('Cancellation is unavailable for the current workflow state.');
        }
        const result = await request(path, {action: 'cancel', expected_revision: latest.revision});
        if (!current()) return;
        const campaignNeedsReconciliation = result.reason === 'campaign_cancellation_unverified' ||
          (!result.reason && result.status === 'reconciliation_required' &&
            latest.graph?.execution_block !== 'scientific_cancelled');
        cancelNotice = result.cleanup_status === 'confirmed' ?
          'Cancellation is in effect. Worker cleanup verified.' :
          campaignNeedsReconciliation ?
            'Execution stopped accepting new work. Campaign cancellation requires reconciliation. Worker cleanup is unverified.' :
            'Cancellation requested. Worker cleanup is unverified.';
      } catch (error) {
        message = error.message || 'Cancellation could not be recorded.';
      } finally {
        if (current()) {
          cancelling = false;
          await load();
          if (current()) status(cancelNotice || message || '');
        }
      }
    }
    async function inspectLineageQC(row, target, selected) {
      const expected = generation, identity = session;
      const current = () => generation === expected && selected();
      const query = '?qc_step=' + encodeURIComponent(row.step_id) +
        '&generation=' + encodeURIComponent(row.generation);
      target.replaceChildren(make('p', 'Loading exact QC evidence…'));
      try {
        const detail = await request('/api/scientific/single-cell/' + identity + query);
        if (!current()) return;
        if (detail.session_id !== identity || detail.step_id !== row.step_id ||
            detail.generation !== row.generation) {
          throw new Error('QC evidence belongs to a different workflow generation.');
        }
        target.replaceChildren();
        root.WorkbenchQC.renderEvidence(target, detail);
      } catch (error) {
        if (current()) target.replaceChildren(make('p',
          error.message || 'QC evidence is unavailable.', 'research-hold'));
      }
    }
    function render() {
      if (imageURL !== null) root.URL.revokeObjectURL(imageURL);
      imageURL = null;
      host.replaceChildren();
      liveStatus = null;
      host.hidden = session === null || view?.mode === 'serial';
      if (host.hidden) return;
      const displayedView = view;
      const localWorkflow = root.WorkbenchStudio?.privateWorkflow?.(host, displayedView, {
        readOnly: readOnly && !allowOfflineApproval,
        launch: plan => launchPrivatePlan(plan, displayedView),
      });
      if (localWorkflow) {
        liveStatus = make('p', '', 'message');
        liveStatus.setAttribute('role', 'status');
        host.append(liveStatus);
      }
      if (view?.shared_motor_umap && root.WorkbenchStudio?.motorWorkflow) {
        root.WorkbenchStudio.motorWorkflow(host, view, {
          readOnly, launch: launchMotorUmap,
          navigate: onMotorFigures,
        });
        return;
      }
      if (localWorkflow) return;
      if (readOnly && taskCentered && root.WorkbenchStudio?.pluginWorkflow?.(host, view)) return;
      const details = make(taskCentered ? 'section' : 'details', undefined,
        taskCentered ? 'wc-research-controls' : 'scientific-section');
      if (!taskCentered) {
        details.open = true;
        details.append(make('summary', 'Adaptive workflow'));
      }
      if (readOnly) details.append(make('p', 'Read-only workflow. Execution controls are unavailable.', 'field-note'));
      liveStatus = make('p', '', 'message');
      liveStatus.setAttribute('role', 'status');
      details.append(liveStatus);
      if (research && view?.graph_status === 'available') {
        executionHost.replaceChildren(details);
        host.append(researchHost);
        research.select(session, {aimId, scope, readOnly, revision: view.revision, packages: view.packages});
      } else host.append(details);
      if (!research || view?.graph_status !== 'available') {
        details.append(make('p', 'Clinical release is disabled.', 'field-note'));
      }
      if (cancelNotice || activityNotice) status(cancelNotice || activityNotice.detail || activityNotice.title);
      if (!view || view.graph_status !== 'available') {
        status(view ? 'Current evidence access is required to inspect this workflow.' : 'Loading adaptive workflow…');
        details.append(button('Refresh workflow', () => load()));
        return;
      }
      const graph = view.graph;
      const actionHost = taskCentered ? make('div', undefined, 'wc-research-action') : details;
      const technical = taskCentered ? make('details', undefined, 'wc-research-ledger') : details;
      if (taskCentered) {
        details.append(actionHost);
        technical.open = ledgerOpen;
        technical.ontoggle = () => {ledgerOpen = technical.open;};
        technical.append(make('summary', 'Inspect run lineage and technical records'));
      }
      const revision = Number.isInteger(view.revision) ? 'Ledger revision ' + view.revision : 'Ledger revision unavailable';
      const replay = view.replay_available ? 'Retained replay available' : 'Retained replay unavailable';
      technical.append(make('p', revision + ' · ' + replay, 'field-note'));
      const outcome = graph.controller_outcome;
      if (outcome) {
        technical.append(make('h3', label(outcome.status)));
        if (Number.isInteger(outcome.open_hold_count)) technical.append(make('p', 'Open scoped holds: ' + outcome.open_hold_count));
      }
      operatorBulk(actionHost);
      if (graph.execution_block) actionHost.append(make('p', 'Execution: ' + label(graph.execution_block), 'field-note'));
      technical.append(make('p', 'Scientific qualification pending independent evaluation.', 'field-note'));
      const toolbar = make('div', undefined, 'actions');
      toolbar.append(button('Refresh workflow', () => load()));
      if (graph.next_cursor) toolbar.append(button('Next evidence page', () => load(graph.next_cursor)));
      technical.append(toolbar);
      const checks = Object.entries(view.diagnostic_checks_on_page || {});
      if (checks.length) technical.append(make('p', 'Diagnostic checks on this page · ' + checks.map(([key, value]) => label(key) + ' ' + value).join(' · ')));
      let publicationControl = null, replayControl = null;
      const admitted = /^\/api\/scientific\/publications\/[0-9a-f]{64}\?format=json$/.test(view.publication?.summary);
      const lineCount = root.WorkbenchLineage?.mount({
        document, host: technical, view, state: lineageState,
        inspectQC: root.WorkbenchQC?.renderEvidence ? inspectLineageQC : null,
        openPublication: admitted ? () => publicationControl?.onclick() : null,
        verifyReplay: view.replay_available ? () => replayControl?.onclick() : null,
      }) ?? 0;
      // Standalone integrations may load this module without the packaged graph.
      const entryCount = graph.entries?.length || 0;
      if (!lineCount && entryCount) {
        const list = make('ul');
        for (const row of graph.entries) {
          const kind = row.entry_kind === 'tombstone' ? 'Superseded result' : label(row.entry_kind);
          list.append(make('li', [row.step_id, kind, label(row.status || row.reason)]
            .filter(Boolean).join(' · ')));
        }
        technical.append(list);
      }
      if (!entryCount) technical.append(make('p', 'No workflow steps have been recorded.'));
      if (graph.total_entries > entryCount && Number.isInteger(graph.offset)) {
        technical.append(make('p', 'Entries ' + (graph.offset + 1) + '–' + (graph.offset + entryCount) + ' of ' + graph.total_entries, 'field-note'));
      }
      for (const hold of view.open_review_holds || []) actionHost.append(make('p', 'Review hold on ' + hold.scope.join(', '), 'field-note'));
      if (admitted) {
        const output = make('section');
        const summaryPath = view.publication.summary;
        const figurePath = view.publication.figure;
        let outputGeneration = 0;
        technical.append(output);
        publicationControl = button('Open admitted output', async () => {
          const expected = generation, ticket = ++outputGeneration;
          const current = () => generation === expected && ticket === outputGeneration;
          try {
            const publication = await request(summaryPath);
            if (!current()) return;
            output.replaceChildren(make('h3', 'Available output'));
            output.scrollIntoView?.({block: 'nearest'});
            if (publication.interpretation?.text) output.append(make('p', publication.interpretation.text));
            else output.append(make('p', 'Interpretation is unavailable for this view.', 'field-note'));
            if (publication.observation) output.append(make('p', 'Checked observation: ' + label(publication.observation.check_id)));
            const families = make('ul');
            for (const family of publication.family_inventory || []) {
              const text = [family.family_id, label(family.status), family.complete ? 'Complete family' : 'Incomplete or withheld family'];
              if (Number.isInteger(family.measured)) text.push(family.measured + ' measured');
              if (Number.isInteger(family.failed)) text.push(family.failed + ' failed');
              families.append(make('li', text.join(' · ')));
            }
            if (families.children.length) output.append(families);
            if (publication.figure && requestFigure && /^\/api\/scientific\/publications\/[0-9a-f]{64}\?format=png$/.test(figurePath)) {
              const figure = await requestFigure(figurePath);
              if (!current()) return;
              if (imageURL !== null) root.URL.revokeObjectURL(imageURL);
              imageURL = root.URL.createObjectURL(figure);
              const image = make('img');
              image.src = imageURL;
              image.alt = 'Verified descriptive effect figure';
              output.append(image);
            } else output.append(make('p', 'No verified figure is available.', 'field-note'));
          } catch (error) {
            if (current()) output.replaceChildren(make('p', error.message || 'The admitted output is unavailable.'));
          }
        });
        toolbar.append(publicationControl);
      }
      if (view.replay_available) {
        replayControl = button('Verify retained replay', async () => {
          const expected = session, requestScope = scope;
          try {
            const receipt = await request(endpoint() + '/replay');
            if (session === expected && scope === requestScope) status(receipt.verified ? 'Read-only historical evidence verified. Current scientific use is unavailable.' : 'Historical evidence could not be verified.');
          } catch (error) {
            if (session === expected && scope === requestScope) status(error.message || 'Historical evidence could not be verified.');
          }
        });
        toolbar.append(replayControl);
      }
      if (taskCentered) details.append(technical);
      if (readOnly) return;
      const grants = make('section', undefined, 'lineage-governor');
      const controls = make('div', undefined, 'actions');
      const localCodex = view.campaign_mode === 'local_interactive_codex';
      const advance = button(localCodex ? 'Ask Codex for the next step' : 'Advance workflow', () => act('advance', data, model));
      const openModel = button('Ask Codex for the next step', () => act('advance', data, model, true));
      const approveQc = button('Approve and run QC', () => act('run_checked_step', data, model));
      const manual = (view.actions.run_operator_steps || []).map(step => button(
        'Run ' + step.step_id + ' · generation ' + step.generation,
        () => act('run_operator_step', data, model, false, step)
      ));
      const resume = button('Resume after changed evidence', () => act('resume', data, model));
      const cancelled = view.graph?.execution_block === 'scientific_cancelled';
      const cancel = button(cancelled ? 'Check worker cleanup' : 'Request cancellation', requestCancellation);
      cancel.disabled = cancelling;
      const syncPermissions = () => {
        const granted = data.checked && (!view.actions.model_required || model.checked);
        advance.disabled = submitting || cancelling || !granted;
        resume.disabled = submitting || cancelling || !granted;
        openModel.disabled = submitting || cancelling || !data.checked || !model.checked;
        approveQc.disabled = submitting || cancelling || !data.checked;
        for (const control of manual) control.disabled = approveQc.disabled;
      };
      const [data, dataLabel] = checkbox('Allow data execution for this step', syncPermissions);
      const [model, modelLabel] = checkbox('Allow model selection for this step', syncPermissions);
      const checkedQc = view.actions.run_checked_step;
      const hasControls = !submitting &&
        (checkedQc || manual.length || view.actions.advance || view.actions.resume || view.actions.model_available);
      if (hasControls) {
        grants.append(make('h4', checkedQc ? 'Review the proposed QC step' : view.actions.resume ? 'Approve resumption' : 'Authorize the next step'));
        if (localCodex || view.actions.model_available) grants.append(make('p', 'Single-operator interactive Codex · Multi-agent qualification unestablished', 'field-note'));
        grants.append(make('p', checkedQc ? 'Run the recorded QC plan with its frozen parameters.' : view.actions.model_required || view.actions.model_available
          ? 'Review the data and model permissions for this action.'
          : 'Review the queued step and grant execution for this action.', 'field-note'));
        const steps = (graph.entries || []).filter(row => row.entry_kind === 'step');
        const ready = steps.filter(row => row.status === 'ready');
        const inputs = new Set(ready.flatMap(row => (row.input_ports || [])
          .map(port => port.representation || port.kind)));
        if (inputs.size) grants.append(make('p', 'Ready input representations · ' +
          [...inputs].map(label).join(', '), 'field-note'));
        const pins = make('details');
        pins.append(make('summary', 'Ready steps, frozen parameters and dependents'));
        const first = ready.slice(0, 8);
        for (const row of first) {
          const text = [row.step_id, label(row.operation_id), 'gen ' + row.generation]
            .filter(Boolean);
          pins.append(make('p', text.join(' · '), 'field-note'));
          if (row.parameters_sha256) pins.append(make('code', row.parameters_sha256));
          const relation = root.WorkbenchLineage?.relationships(steps, row.step_id);
          const downstream = relation ? steps.filter(step =>
            relation(step.step_id) === 'downstream').map(step => step.step_id) : [];
          if (downstream.length) pins.append(make('p',
            'Downstream on this page · ' + downstream.join(', '), 'field-note'));
        }
        if (first.length) grants.append(pins);
        grants.append(dataLabel);
        if (!checkedQc && (view.actions.model_required || view.actions.model_available)) grants.append(modelLabel);
        actionHost.append(grants);
      }
      if (hasControls) {
        if (checkedQc) controls.append(approveQc);
        controls.append(...manual);
        if (!checkedQc && view.actions.advance) controls.append(advance);
        if (!checkedQc && view.actions.model_available) controls.append(openModel);
        if (view.actions.resume) controls.append(resume);
      }
      if (view.actions.cancel) controls.append(cancel);
      if (controls.children.length) {
        if (hasControls) grants.append(controls);
        else actionHost.append(controls);
      }
      syncPermissions();
    }
    async function select(identity, options = {}) {
      const next = /^[0-9a-f]{32}$/.test(text(identity)) ? identity : null;
      if (session === next && readOnly === (options.readOnly === true) &&
          allowOfflineApproval === (options.allowOfflineApproval === true) &&
          scope === (options.scope || null) && aimId === (options.aimId || null)) return;
      session = next;
      readOnly = options.readOnly === true;
      allowOfflineApproval = options.allowOfflineApproval === true;
      scope = options.scope || null;
      aimId = options.aimId || null;
      research?.select(null, {scope, readOnly, revision: null});
      generation++;
      view = options.initialView?.session_id === next ? options.initialView : null;
      submitting = false;
      cancelling = false;
      cancelNotice = null;
      activityNotice = null;
      ledgerOpen = false;
      lineageState.selected = null;
      render();
      if (session !== null && !view) await load();
      else pollPluginWorkflows();
    }
    return {select, refresh: () => load(), mode: () => view?.mode};
  }
  root.WorkbenchInvestigations = Object.freeze({mount, mountAdaptivePanel, normalizeSelection});
})(typeof window !== 'undefined' ? window : globalThis);
