/* Shared, local-only presentation controls for the research studio. */
(function (root) {
  'use strict';

  function sessionLink(search) {
    const query = new URLSearchParams(search || '');
    const id = query.get('session_id');
    const view = query.get('view') || 'overview';
    return /^[a-f0-9]{32}$/.test(id || '') &&
      ['overview', 'findings', 'workflows', 'qc', 'figures', 'code', 'evidence'].includes(view)
      ? {id, view} : null;
  }

  const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
  const label = value => String(value).replaceAll('_', ' ');
  const node = (tag, text) => {
    const element = root.document.createElement(tag);
    if (text != null) element.textContent = text;
    return element;
  };
  const previewTypes = new Set(['code', 'csv', 'tsv', 'json', 'text', 'notebook', 'image']);
  const rawKinds = /(?:^|[_ .-])(counts?|matrix|expression|fastq|h5ad|bam|cram)(?:$|[_ .-])/i;

  function previewDescriptor(record, artifacts) {
    const candidate = record.artifact;
    if (!artifacts || !candidate || record.embargoed === true ||
        candidate.download_status === 'restricted_audit_entitlement_required' ||
        [record.representation, candidate.representation, candidate.artifact_type,
          candidate.name].some(value => typeof value === 'string' && rawKinds.test(value))) return null;
    try {
      const descriptor = artifacts.validateDescriptor(candidate);
      const expected = record.artifact_sha256;
      const kind = artifacts.previewKind(descriptor, 'evidence');
      if (!digest(expected) || descriptor.sha256 !== expected || !previewTypes.has(kind)) return null;
      const limit = kind === 'image' ? artifacts.limits.image_bytes : artifacts.limits.text_bytes;
      return descriptor.size_bytes <= limit ? {descriptor, kind, limit} : null;
    } catch (_) {
      return null;
    }
  }

  function renderPreview(host, data, descriptor, kind, artifacts) {
    if (kind === 'image') {
      const type = String(descriptor.content_type).split(';')[0];
      if (artifacts.rasterKind(data) !== type) throw new Error('The verified bytes are not the declared raster type.');
      const url = root.URL.createObjectURL(new Blob([data], {type}));
      const image = node('img');
      image.src = url;
      image.alt = descriptor.name;
      host.append(image);
      return url;
    }
    const text = new TextDecoder('utf-8', {fatal: true}).decode(data);
    if (kind === 'csv' || kind === 'tsv') {
      const parsed = artifacts.parseTable(text, kind);
      const table = node('table');
      const head = node('thead');
      const columns = node('tr');
      for (const name of parsed.columns) {
        const heading = node('th', name);
        heading.scope = 'col';
        columns.append(heading);
      }
      head.append(columns);
      const body = node('tbody');
      for (const row of parsed.rows) {
        const cells = node('tr');
        for (const item of row) cells.append(node('td', item == null ? '—' : String(item)));
        body.append(cells);
      }
      table.append(head, body);
      host.append(table);
      if (parsed.truncated) host.append(node('p', 'Showing the first ' + parsed.rows.length + ' rows.'));
    } else {
      const preview = kind === 'notebook' ? artifacts.notebookCode(text).source
        : kind === 'json' ? JSON.stringify(artifacts.parseJSON(text), null, 2) : text;
      host.append(node('pre', preview.slice(0, artifacts.limits.code_characters)));
    }
    return null;
  }

  let drawerController = null;
  let contextController = null;

  function mountDrawer(dialog, request) {
    const home = dialog.parentNode;
    const content = dialog.querySelector('[data-studio-receipt-content]');
    const dismiss = dialog.querySelector('[data-studio-receipt-close]');
    let generation = 0;
    let objectURL = null;
    let abort = null;
    let opener = null;

    function stopPreview() {
      generation++;
      abort?.abort();
      abort = null;
    }

    function reset() {
      stopPreview();
      if (objectURL) root.URL.revokeObjectURL(objectURL);
      objectURL = null;
      content.replaceChildren();
    }

    function finishClose() {
      reset();
      if (typeof dialog.hidePopover === 'function' && dialog.matches(':popover-open')) dialog.hidePopover();
      else if (dialog.open) dialog.close();
      dialog.hidden = true;
      dialog.returnValue = '';
      dialog.removeAttribute?.('data-closing');
      dialog.inert = false;
      if (home && dialog.parentNode !== home) home.append(dialog);
    }

    function close(restore = true) {
      if (dialog.hidden) return;
      if (!restore) {
        opener = null;
        finishClose();
        return;
      }
      if (dialog.hasAttribute?.('data-closing')) return;
      stopPreview();
      const stamp = generation;
      const trigger = opener;
      opener = null;
      dialog.setAttribute('data-closing', '');
      dialog.inert = true;
      if (trigger?.isConnected) trigger.focus({preventScroll: true});
      const motion = dialog.getAnimations?.() || [];
      if (!motion.length) {
        finishClose();
        return;
      }
      Promise.allSettled(motion.map(animation => animation.finished)).then(() => {
        if (stamp === generation) finishClose();
      });
    }

    function inspect(record) {
      if (!record || typeof record !== 'object') return;
      const exact = [
        ['Receipt SHA-256', record.receipt_sha256 || record.sha256],
        ['Artifact SHA-256', record.artifact_sha256],
        ['Plan SHA-256', record.plan_sha256],
        ['Package SHA-256', record.package_sha256],
      ].filter(([, value]) => digest(value));
      if (!exact.length) return;
      reset();
      if (dialog.hidden || dialog.hasAttribute?.('data-closing')) {
        opener = root.document.activeElement;
        // A modal makes siblings inert, including popovers outside its subtree.
        const owner = opener?.closest?.('dialog:modal') || home;
        if (owner && dialog.parentNode !== owner) owner.append(dialog);
      }
      dialog.removeAttribute?.('data-closing');
      dialog.inert = false;
      dialog.hidden = false;
      if (typeof dialog.showPopover === 'function') {
        if (!dialog.matches(':popover-open')) dialog.showPopover();
      } else if (!dialog.open) dialog.show();
      const facts = node('dl');
      for (const [name, value] of exact) {
        const details = node('dd');
        const code = node('code', value);
        const copy = node('button', 'Copy SHA-256');
        copy.type = 'button';
        copy.setAttribute('aria-label', 'Copy ' + name);
        copy.onclick = async () => {
          try {
            await root.navigator.clipboard.writeText(value);
            copy.textContent = 'Copied';
          } catch (_) {
            code.tabIndex = 0;
            code.focus();
            copy.textContent = 'Select the SHA-256 to copy';
          }
        };
        details.append(code, copy);
        facts.append(node('dt', name), details);
      }
      const fields = [
        ['Producer', record.step_id],
        ['Generation', Number.isSafeInteger(record.generation) ? record.generation : null],
        ['Representation', record.representation],
        ['Evidence scope', record.claim_scope],
        ['Delivery', record.embargoed === true ? 'Withheld' : record.delivery_status],
      ];
      for (const [name, value] of fields) {
        if (typeof value === 'string' || Number.isSafeInteger(value)) {
          facts.append(node('dt', name), node('dd', label(value)));
        }
      }
      content.append(facts);
      const artifacts = root.WorkbenchArtifactCanvas;
      const preview = previewDescriptor(record, artifacts);
      if (preview && typeof request === 'function') {
        const control = node('button', 'Preview verified artifact');
        control.type = 'button';
        const status = node('p');
        status.setAttribute('role', 'status');
        const host = node('div');
        host.className = 'studio-receipt-preview';
        control.onclick = async () => {
          const stamp = generation;
          control.disabled = true;
          status.textContent = 'Verifying artifact bytes…';
          abort = new AbortController();
          try {
            const response = await request(preview.descriptor.download_path, {signal: abort.signal});
            if (!response?.ok) throw new Error('Verified artifact is unavailable.');
            const data = await artifacts.readBounded(response, preview.limit, () => stamp !== generation);
            await artifacts.verifyBytes(data, preview.descriptor);
            if (stamp !== generation) return;
            objectURL = renderPreview(host, data, preview.descriptor, preview.kind, artifacts);
            status.textContent = 'SHA-256 verified.';
          } catch (error) {
            if (stamp === generation) {
              status.textContent = error.message || 'Verified artifact is unavailable.';
              control.disabled = false;
            }
          }
        };
        content.append(control, status, host);
      }
      dismiss.focus();
    }

    dismiss.addEventListener('click', () => close());
    dialog.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        close();
      }
    });
    drawerController = {inspect, close};
    return drawerController;
  }

  function mountTabIndicator(tabs) {
    if (!root.ResizeObserver || !root.MutationObserver) return;
    const controls = tabs.querySelectorAll('[role=tab]');
    function position() {
      const active = tabs.querySelector('[aria-selected=true]:not([hidden])');
      if (tabs.hidden || !active?.offsetWidth) {
        delete tabs.dataset.indicator;
        return;
      }
      tabs.style.setProperty('--studio-tab-x', (active.offsetLeft + 8) + 'px');
      tabs.style.setProperty('--studio-tab-y', (active.offsetTop + active.offsetHeight - 2) + 'px');
      tabs.style.setProperty('--studio-tab-width', (active.offsetWidth - 16) + 'px');
      tabs.dataset.indicator = '';
    }
    const size = new root.ResizeObserver(position);
    size.observe(tabs);
    controls.forEach(control => size.observe(control));
    new root.MutationObserver(position).observe(tabs, {
      subtree: true, attributes: true, attributeFilter: ['aria-selected', 'hidden'],
    });
    position();
  }

  function revealCanvas(panels) {
    panels.forEach(panel => panel.classList.remove('studio-canvas-reveal'));
    if (root.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    for (const panel of panels) {
      if (panel.hidden) continue;
      // Findings and Evidence can reuse the same panel between selections.
      void panel.offsetWidth;
      panel.classList.add('studio-canvas-reveal');
    }
  }

  function addContext(entry) {
    return contextController?.add(entry);
  }

  function button(text, action) {
    const control = node('button', text);
    control.type = 'button';
    control.className = 'secondary';
    control.onclick = action;
    return control;
  }

  function section(parent, title, className = 'studio-session-card') {
    const card = node('section');
    card.className = className;
    if (title) card.append(node('h3', title));
    parent.append(card);
    return card;
  }

  function disclosure(title, ...content) {
    const details = node('details');
    details.className = 'studio-disclosure';
    details.append(node('summary', title), ...content);
    return details;
  }

  function pill(state, text = label(state)) {
    const badge = node('span', text);
    badge.className = 'studio-status';
    badge.setAttribute('data-status', state);
    return badge;
  }

  function renderSessionRail(host, sessions, selectedId, open) {
    const expanded = host.querySelector?.('.studio-session-history')?.open;
    host.replaceChildren();
    const plugins = (sessions || []).filter(item => item.origin === 'plugin' &&
      /^[a-f0-9]{32}$/.test(item.session_id));
    host.hidden = plugins.length === 0;
    if (host.hidden) return;
    host.append(node('h3', 'Plugin sessions'));
    const seen = new Set(), previous = node('div');
    for (const item of plugins) {
      const entry = button(null, () => open(item.session_id));
      entry.className = 'studio-session-entry';
      entry.setAttribute('aria-current', String(item.session_id === selectedId));
      const name = item.objective?.trim() || item.dataset_label || 'Research session';
      const title = node('strong', name);
      title.className = 'studio-session-name';
      entry.title = [name, item.dataset_label === name ? '' : item.dataset_label].filter(Boolean).join(' · ');
      entry.append(title, pill(item.status, humanize(item.status).replace(/^./, first => first.toUpperCase())));
      const key = item.objective?.trim() && item.dataset_label
        ? JSON.stringify([name.toLowerCase().replace(/[.!?]$/, ''), item.dataset_label]) : null;
      if (key && seen.has(key) && item.session_id !== selectedId) previous.append(entry);
      else host.append(entry);
      if (key) seen.add(key);
    }
    if (previous.children.length) {
      const earlier = disclosure('Earlier sessions', previous);
      earlier.className += ' studio-session-history';
      earlier.open = expanded === true;
      host.append(earlier);
    }
  }

  function markdown(text) {
    const body = node('div');
    body.className = 'studio-markdown';
    const inline = (target, value) => {
      for (const [index, part] of value.split(/`([^`\n]+)`/).entries()) {
        if (part) target.append(index % 2 ? node('code', part) : root.document.createTextNode(part));
      }
      return target;
    };
    let paragraph = [], list = null, code = null;
    const flush = () => {
      if (paragraph.length) body.append(inline(node('p'), paragraph.join(' ')));
      paragraph = [];
    };
    for (const line of String(text || '').slice(0, 65536).split(/\r?\n/)) {
      if (/^```/.test(line)) {
        flush(); list = null;
        if (code !== null) body.append(node('pre', code.join('\n')));
        code = code === null ? [] : null;
      } else if (code !== null) code.push(line);
      else if (/^#{1,6}\s/.test(line)) {
        flush(); list = null;
        const [marks, title] = line.match(/^#+\s*(.*)$/);
        body.append(inline(node(marks.startsWith('###') ? 'h5' : 'h4'), title));
      } else if (/^\s*[-*]\s/.test(line)) {
        flush();
        if (!list) { list = node('ul'); body.append(list); }
        list.append(inline(node('li'), line.replace(/^\s*[-*]\s+/, '')));
      } else if (!line.trim()) {
        flush(); list = null;
      } else {
        list = null;
        paragraph.push(line.trim());
      }
    }
    flush();
    if (code?.length) body.append(node('pre', code.join('\n')));
    return body;
  }

  const asList = value => Array.isArray(value) ? value : [];
  const cellText = value => typeof value === 'boolean' ? (value ? 'Yes' : 'No') :
    typeof value === 'number' ? new Intl.NumberFormat(undefined, {maximumSignificantDigits: 7}).format(value) :
    typeof value === 'string' ? value : '—';
  const humanize = value => label(value || '').replaceAll('-', ' ');
  const verifiedTable = table => table && typeof table.caption === 'string' &&
    asList(table.columns).length && asList(table.rows).some(Array.isArray);
  const metricView = new WeakMap();

  function metricsView(items, animate) {
    const grid = node('div');
    grid.className = 'studio-session-kpis';
    const integers = new Intl.NumberFormat();
    for (const [title, value, suffix = ''] of items) {
      const tile = node('div'), amount = node('strong');
      const format = count => (Number.isSafeInteger(count) ? integers.format(count) : cellText(count)) + suffix;
      if (animate && Number.isSafeInteger(value) && value > 0 && root.requestAnimationFrame &&
          root.matchMedia?.('(prefers-reduced-motion: no-preference)').matches) {
        const exact = node('span', format(value));
        exact.className = 'studio-kpi-final';
        const visual = node('span', format(value));
        visual.setAttribute('aria-hidden', 'true');
        amount.append(exact, visual);
        let start;
        const frame = now => {
          if (!visual.isConnected) return;
          start ??= now;
          const progress = Math.min(1, (now - start) / 300);
          visual.textContent = format(Math.round(value * (1 - (1 - progress) ** 3)));
          if (progress < 1) root.requestAnimationFrame(frame);
        };
        root.requestAnimationFrame(frame);
      } else amount.textContent = format(value);
      tile.append(node('span', title), amount);
      grid.append(tile);
    }
    return grid;
  }

  function retainedFigures(view) {
    return asList(view.retained_comparison?.figures).filter(figure => {
      if (typeof figure?.title !== 'string' || typeof figure.download_path !== 'string') return false;
      const route = /^\/api\/scientific\/adaptive\/([a-f0-9]{32})\/comparison\/[a-f0-9]{64}\.png$/.exec(figure.download_path);
      return route !== null && route[0] === figure.download_path && route[1] === view.session_id;
    });
  }

  function retainedFigureCards(host, view, options) {
    const entries = retainedFigures(view).map(figure => {
      const typed = /^\/api\/scientific\/adaptive\/([a-f0-9]{32})\/comparison\/[a-f0-9]{64}\/plotly$/
        .exec(figure.plotly_path || '');
      const plotly = typed && typed[0] === figure.plotly_path && typed[1] === view.session_id
        ? figure.plotly_path : null;
      return {figure, plotly};
    });
    entries.sort((a, b) => Number(Boolean(b.plotly)) - Number(Boolean(a.plotly)));
    for (const {figure, plotly} of entries) {
      verifiedFigure(host, figure.title, figure.download_path, 'image/png', options, plotly);
    }
  }

  function handoffView(tab, view) {
    if (tab !== 'workflows' || !view.plugin_linked) return tab;
    if (privatePlans(view).some(plan =>
        ['awaiting_review', 'blocked', 'queued', 'running'].includes(plan.status))) return tab;
    return retainedFigures(view).length || (
      view.shared_motor_umap?.status === 'completed' && motorArtifact(view, 'umap')
    ) ? 'figures' : tab;
  }

  function tableView(columns, rows, title) {
    const wrap = node('div');
    wrap.className = 'studio-table-scroll';
    wrap.tabIndex = 0;
    wrap.setAttribute('role', 'region');
    wrap.setAttribute('aria-label', title);
    const table = node('table'), head = node('thead'), body = node('tbody'), header = node('tr');
    table.setAttribute('aria-label', title);
    for (const item of columns) {
      const cell = node('th', item === 'Posterior inclusion probability' ? 'PIP' : item);
      cell.scope = 'col';
      header.append(cell);
    }
    head.append(header);
    for (const row of rows.filter(Array.isArray)) {
      const tr = node('tr');
      columns.forEach((column, index) => {
        const value = row[index];
        const rowHeader = index === 0 && typeof value === 'string' &&
          !/^(?:value|rate|count|cells|p-value|q-value|coefficient|percentage|fraction|%|#)\b/i.test(column);
        const cell = node(rowHeader ? 'th' : 'td');
        if (rowHeader) cell.scope = 'row';
        const genomic = /^(?:gene(?:[ _-](?:id|symbol))?|geneid|symbol|ensembl(?:[ _-]id)?)$/i.test(column);
        if (typeof value === 'string' && (genomic || digest(value))) {
          const badge = node('code', value);
          badge.className = 'studio-mono';
          cell.append(badge);
        } else {
          cell.textContent = cellText(value);
          if (typeof value === 'number') cell.className = 'studio-number';
        }
        tr.append(cell);
      });
      body.append(tr);
    }
    table.append(head, body);
    wrap.append(table);
    return wrap;
  }

  function retainedComparison(host, tab, view, options) {
    const comparison = view.retained_comparison;
    if (tab === 'overview') {
      const panel = section(host, comparison.title);
      const stages = asList(comparison.stages);
      const cellCount = stages.reduce((total, stage) => total + stage.cells, 0);
      panel.append(node('p', `${cellCount.toLocaleString()} cells across ${stages.length} datasets.`),
        disclosure('Input datasets', tableView(['Stage', 'Dataset', 'Cells'], stages
          .map(({label, dataset, cells}) => [label, dataset, cells]), 'Comparison datasets')));
      const captions = asList(comparison.tables).filter(verifiedTable).map(table => table.caption);
      if (captions.length) panel.append(node('p', 'Recorded comparisons: ' + captions.join(' · ') + '.'));
      if (asList(comparison.limitations).length)
        panel.append(node('p', comparison.limitations[0]));
      const links = node('div');
      links.className = 'studio-result-actions';
      links.append(button('View findings', () => options.navigate?.('findings')),
        button('View workflow', () => options.navigate?.('workflows')),
        button('View figures', () => options.navigate?.('figures')));
      panel.append(links);
      return;
    }
    if (tab === 'figures') {
      retainedFigureCards(host, view, options);
    }
    const tables = asList(comparison.tables).filter(verifiedTable);
    for (const table of tables) {
      const values = tableView(table.columns, table.rows, table.caption);
      if (tab === 'figures') host.append(disclosure(table.caption, values));
      else section(host, table.caption).append(values);
    }
    if (tab === 'findings' && asList(comparison.limitations).length) {
      section(host, 'Interpretation').append(...comparison.limitations.map(note => node('p', note)));
    }
  }

  const workflowStatus = {
    awaiting_review: 'Awaiting local approval', queued: 'Queued', running: 'Running',
    completed: 'Completed', failed: 'Failed', interrupted: 'Interrupted',
    blocked: 'Blocked', unavailable: 'Unavailable',
  };
  const motorProgress = {
    verifying_inputs: 'Verifying the three frozen input files.',
    fitting_shared_umap: 'Fitting the shared UMAP and making the marker plot.',
    verifying_outputs: 'Verifying and saving the figures and tables.',
  };
  const motorArtifacts = {
    umap: ['Shared UMAP', 'image/png', 'shared_motor_umap.png'],
    markers: ['Motor-state marker expression', 'image/png', 'motor_state_markers.png'],
    coordinates: ['UMAP coordinates', 'text/csv', 'coordinates.csv'],
    composition: ['stage composition', 'text/csv', 'composition.csv'],
    parameters: ['analysis parameters', 'application/json', 'parameters.json'],
  };
  const motorGallery = new WeakMap();
  const motorLimits = [
    'State counts use the supplied cell annotations. They describe captured cells.',
    'Batch labels are nested within stage, and embryo-level replication is unverified.',
    'Lineage transitions and embryo-level changes in abundance require independent evidence.',
  ];

  function motorArtifact(view, kind) {
    const item = view.shared_motor_umap?.artifacts?.[kind];
    if (!digest(item?.sha256) || !/^[a-f0-9]{32}$/.test(view.session_id || '')) return null;
    const url = '/api/scientific/adaptive/' + view.session_id + '/motor-umap/' + kind + '/' + item.sha256;
    return item.url === url ? url : null;
  }

  function localApproval(panel, caption, launch, failure) {
    const field = node('label'), consent = node('input');
    field.className = 'check';
    consent.type = 'checkbox';
    consent.setAttribute('aria-label', 'Allow local execution for this plan');
    field.append(consent, node('span', 'Allow local execution for this plan'));
    const note = node('p');
    note.setAttribute('role', 'status');
    const run = button(caption, async () => {
      if (!consent.checked || run.disabled) return;
      run.disabled = true;
      consent.disabled = true;
      try {
        await launch();
      } catch (error) {
        note.textContent = error.message || failure;
        consent.disabled = false;
        run.disabled = !consent.checked;
      }
    });
    run.disabled = true;
    consent.onchange = () => { run.disabled = !consent.checked; };
    panel.append(field, run, note);
  }

  function motorWorkflow(host, view, {readOnly, launch, navigate}) {
    const workflow = view.shared_motor_umap;
    const panel = section(host, 'Shared motor-neuron UMAP');
    panel.append(pill(workflow.status, workflowStatus[workflow.status] || 'Unavailable'));
    if (workflow.status === 'completed') {
      const stages = asList(workflow.stages);
      const inputs = asList(workflow.inputs);
      const percent = new Intl.NumberFormat(undefined, {style: 'percent', maximumFractionDigits: 1});
      const captured = inputs.length && inputs.every(input => Number.isSafeInteger(input.cells))
        ? inputs.reduce((total, input) => total + input.cells, 0) : null;
      const totals = [['Captured cells', captured], ['Cells mapped', workflow.cells],
        ['Genes used for PCA', workflow.features]].filter(([, value]) => Number.isSafeInteger(value));
      if (totals.length) panel.append(metricsView(totals, false));
      if (inputs.length) panel.append(tableView(
        ['Stage', 'Input file', 'All captured cells', 'Cells mapped', 'Share mapped'],
        inputs.map(({name, cells}, i) => {
          const selected = stages[i]?.motor_cells;
          const share = Number.isFinite(selected) && cells > 0 && selected <= cells
            ? percent.format(selected / cells) : '—';
          return [stages[i]?.stage, name, cells, selected, share];
        }), 'Inputs and selection',
      ));
      motorCompletedMethod(panel, workflow);
      const files = Object.entries(motorArtifacts).filter(([kind]) => motorArtifact(view, kind));
      if (files.length) {
        const list = node('ul');
        list.className = 'studio-workflow-outputs';
        for (const [kind, [title, , filename]] of files) {
          const item = node('li');
          const checksum = workflow.artifacts[kind].sha256;
          item.append(node('span', filename), digestButton('Inspect ' + title, checksum, {
            artifact_sha256: checksum, step_id: 'Shared motor-neuron UMAP',
            delivery_status: 'verified',
          }));
          list.append(item);
        }
        panel.append(disclosure('Verified outputs', list));
      }
      if (navigate) panel.append(button('View figures', navigate));
      return;
    }
    if (workflow.error) panel.append(node('p', workflow.error));
    if (workflow.status === 'queued' || workflow.status === 'running') {
      panel.append(node('p', motorProgress[workflow.progress] ||
        'The DevHarness plugin is running this workflow locally.'));
      return;
    }
    if (workflow.status !== 'awaiting_review') return;
    panel.append(node('p', 'Fit one UMAP across the e09, e10 and e11 cells with motor-neuron or motor-neuroblast annotations.'));
    const inputs = asList(workflow.inputs);
    if (inputs.length) {
      const rows = inputs.map(({name, cells}, i) => [['e09', 'e10', 'e11'][i], name, cells]);
      panel.append(tableView(['Stage', 'Input file', 'Cells'], rows, 'Frozen inputs'));
    }
    const settings = workflow.parameters;
    if (settings) panel.append(disclosure('Run settings', tableView(
      ['Stage column', 'Annotation column', 'Counts layer'],
      [[settings.stage_column, settings.label_column, settings.counts_layer]], 'Frozen settings',
    )));
    if (workflow.can_run !== true || readOnly || !launch) {
      panel.append(node('p', 'Local execution is unavailable in this Studio workspace.'));
      return;
    }
    localApproval(panel, 'Approve and run shared UMAP', launch, 'Studio could not start the shared UMAP.');
  }

  function privatePlans(view) {
    return asList(view?.private_workflow?.plans).filter(plan =>
      /^[a-f0-9]{32}$/.test(plan?.plan_id || '') && digest(plan.plan_sha256) &&
      typeof plan.operation === 'string' && plan.plan_id !== view.shared_motor_umap?.plan_id);
  }

  function verifiedPrivateRun(plan) {
    return plan.status === 'completed' && digest(plan.graph_execution?.receipt_sha256);
  }

  function privateWorkflow(host, view, {readOnly, launch}) {
    const plans = privatePlans(view);
    for (const plan of plans) {
      const operation = humanize(plan.operation) || 'Analysis';
      const panel = section(host, operation[0].toUpperCase() + operation.slice(1));
      const unverified = plan.status === 'completed' && !verifiedPrivateRun(plan);
      const state = unverified ? 'unavailable' : plan.status;
      panel.append(pill(state, workflowStatus[state] || 'Unavailable'));
      if (typeof plan.objective === 'string' && plan.objective)
        panel.append(node('p', plan.objective));
      if (plan.error || unverified) panel.append(node('p',
        humanize(plan.error || 'verified_receipt_unavailable')));
      const review = disclosure('Frozen plan', tableView(['Plan ID', 'Plan SHA-256'],
        [[plan.plan_id, plan.plan_sha256]], 'Frozen plan'));
      const inputs = asList(plan.inputs).filter(item => typeof item?.name === 'string');
      if (inputs.length) review.append(tableView(['Input', 'Role'], inputs.map(item =>
        [item.name, humanize(item.role)]), 'Frozen inputs'));
      const settings = Object.entries(plan.parameters || {}).filter(([, value]) =>
        typeof value === 'string' || typeof value === 'boolean' || Number.isFinite(value));
      if (settings.length) review.append(tableView(['Parameter', 'Value'], settings.map(([key, value]) =>
        [humanize(key), value]), 'Frozen settings'));
      const dependencies = asList(plan.dependencies).filter(item =>
        /^[a-f0-9]{32}$/.test(item?.plan_id || '') && digest(item.plan_sha256));
      if (dependencies.length) review.append(tableView(['Prerequisite plan', 'Plan SHA-256'],
        dependencies.map(item => [item.plan_id, item.plan_sha256]), 'Frozen dependencies'));
      panel.append(review);
      if (verifiedPrivateRun(plan)) {
        const graph = plan.graph_execution;
        panel.append(digestButton('Inspect graph receipt', graph.receipt_sha256, {
          ...graph, artifact_sha256: graph.result_sha256, delivery_status: 'verified',
        }));
      }
      if (state !== 'awaiting_review') continue;
      if (plan.can_run !== true || readOnly || !launch) {
        panel.append(node('p', 'Local execution is unavailable in this Studio workspace.'));
        continue;
      }
      localApproval(panel, 'Approve and run', () => launch(plan), 'Studio could not start the plan.');
    }
    return plans.length > 0;
  }

  function motorCompletedMethod(panel, workflow) {
    const method = workflow.method;
    if (!method) return;
    const percent = new Intl.NumberFormat(undefined, {
      style: 'percent', maximumFractionDigits: 1,
    });
    const sequence = node('ol');
    sequence.className = 'studio-workflow-steps';
    const step = (title, ...details) => {
      const item = node('li');
      item.append(node('strong', title));
      for (const detail of details.filter(Boolean)) item.append(node('p', detail));
      sequence.append(item);
    };
    const settings = workflow.parameters || {};
    const labels = asList(method.population);
    step('Select motor-labeled cells',
      settings.label_column && 'Used supplied annotations from ' + settings.label_column + '.',
      labels.length && 'Included labels: ' + labels.join(' and ') + '.');
    step('Align genes and normalize counts',
      Number.isSafeInteger(method.common_symbols) &&
        `Matched ${cellText(method.common_symbols)} genes across the inputs by exact symbol.`,
      typeof method.normalization === 'string' &&
        `From the ${settings.counts_layer || 'declared counts'} layer, normalized to ${method.normalization}.`);
    const variance = method.pca_explained_variance_fraction;
    const explained = Number.isFinite(variance) ? ' explaining ' + percent.format(variance) + ' of variance' : '';
    const cutoff = Number.isFinite(method.min_fraction_detected)
      ? ' with detection in at least ' + percent.format(method.min_fraction_detected) + ' of mapped cells' : '';
    step('Select features and fit PCA',
      Number.isSafeInteger(workflow.features) &&
        `Used ${cellText(workflow.features)} genes for PCA${cutoff}.`,
      Number.isSafeInteger(method.pca_dimensions) &&
        `Fit one PCA across all stages with ${method.pca_dimensions} components${explained}.`);
    const umap = method.umap;
    if (Number.isInteger(umap?.n_neighbors) && Number.isFinite(umap.min_dist)) {
      const parameters = [`${umap.n_neighbors} neighbors`, `minimum distance ${umap.min_dist}`];
      if (typeof umap.metric === 'string') parameters.push(umap.metric + ' distance');
      if (Number.isInteger(umap.seed)) parameters.push('seed ' + umap.seed);
      step('Build one shared UMAP', parameters.join(' · ') + '.',
        'All stage panels use the same fitted coordinates and axes.');
    }
    const markers = asList(method.marker_genes_held_out);
    if (markers.length)
      step('Profile marker expression', `Summarized ${markers.length} genes separately from the fit by stage and supplied cell state.`);
    panel.append(node('h4', 'Steps run'), sequence);
    const notes = [];
    if (typeof method.hvg_source === 'string') notes.push(node('p', 'Variable genes: ' + method.hvg_source + '.'));
    if (Number.isFinite(method.pca_gene_sd_floor)) notes.push(node('p', 'PCA gene standard-deviation floor: ' + method.pca_gene_sd_floor + '.'));
    if (markers.length) notes.push(node('p', 'Held-out markers: ' + markers.join(', ') + '.'));
    const missing = asList(method.marker_genes_unavailable);
    if (missing.length) notes.push(node('p', 'Unavailable markers: ' + missing.join(', ') + '.'));
    if (notes.length) panel.append(disclosure('Feature and marker settings', ...notes));
    const limits = asList(method.limitations);
    if (limits.length) panel.append(disclosure('Interpretation limits', ...limits.map(note => node('p', note))));
  }

  function figureGallery(panel) {
    let mounted = motorGallery.get(panel);
    if (!mounted) {
      mounted = {abort: new AbortController(), urls: []};
      motorGallery.set(panel, mounted);
    }
    return mounted;
  }

  function verifiedFigure(panel, caption, url, media, options, plotly) {
    const card = section(panel, caption, 'studio-table-card');
    showFigure(panel, card, caption, plotly && options.plotlyFrame?.(plotly), () => {
      const image = node('img');
      image.className = 'studio-retained-figure';
      image.alt = caption;
      if (!options.artifact) {
        image.src = url;
        card.append(image);
        return;
      }
      const {abort, urls} = figureGallery(panel);
      const status = node('p', 'Loading the verified figure…');
      status.setAttribute('role', 'status');
      card.append(status);
      options.artifact(url, media, abort.signal).then(blob => {
        if (abort.signal.aborted) return;
        image.src = root.URL.createObjectURL(blob);
        urls.push(image.src);
        status.replaceChildren(image);
      }).catch(error => {
        if (!abort.signal.aborted)
          status.textContent = error.message || 'The verified figure is unavailable.';
      });
    });
  }

  function showFigure(panel, card, caption, source, showFallback) {
    if (!source) {
      showFallback();
      return;
    }
    const {signal} = figureGallery(panel).abort;
    const status = node('p', 'Loading the interactive figure…');
    status.setAttribute('role', 'status');
    const frame = node('iframe');
    frame.className = 'studio-plotly-frame';
    frame.dataset.state = 'loading';
    frame.setAttribute('sandbox', 'allow-scripts');
    frame.setAttribute('referrerpolicy', 'no-referrer');
    frame.title = 'Interactive ' + caption;
    const finish = ready => {
      root.clearTimeout(timer);
      root.removeEventListener('message', receive);
      status.remove();
      if (ready) frame.dataset.state = 'ready';
      else {
        frame.remove();
        showFallback();
      }
    };
    const receive = event => {
      if (event.source !== frame.contentWindow) return;
      if (event.data?.type === 'studio-plotly-ready') finish(true);
      else if (event.data?.type === 'studio-plotly-error') finish(false);
    };
    const timer = root.setTimeout(() => finish(false), 20000);
    root.addEventListener('message', receive, {signal});
    signal.addEventListener('abort', () => root.clearTimeout(timer), {once: true});
    frame.src = source;
    card.append(status, frame);
  }

  function motorFigure(panel, view, kind, options) {
    const url = motorArtifact(view, kind);
    if (!url || !options.artifact) return;
    const [caption, media] = motorArtifacts[kind];
    const coordinates = kind === 'umap' ? motorArtifact(view, 'coordinates') : null;
    const plotly = coordinates
      ? coordinates.replace('/coordinates/', '/') + '/plotly' : null;
    verifiedFigure(panel, caption, url, media, options, plotly);
  }

  function motorDownloads(panel, view, options, kinds = Object.keys(motorArtifacts)) {
    if (!options.artifact) return;
    const links = node('div');
    links.className = 'studio-result-actions';
    for (const kind of kinds) {
      const [caption, media, filename] = motorArtifacts[kind];
      const url = motorArtifact(view, kind);
      if (!url) continue;
      links.append(button('Download ' + caption, async () => {
        try {
          const blob = await options.artifact(url, media);
          const objectURL = root.URL.createObjectURL(blob);
          const link = node('a');
          link.href = objectURL;
          link.download = filename;
          link.click();
          root.setTimeout(() => root.URL.revokeObjectURL(objectURL), 1000);
        } catch (error) {
          options.message?.(error.message || 'The requested artifact is unavailable.', true);
        }
      }));
    }
    if (links.children.length) panel.append(disclosure('Downloads', links));
  }

  function localAnalysis(host, tab, view, options) {
    const report = view.local_analysis;
    if (!report) return;
    if (tab === 'figures') {
      for (const item of asList(report.figures)) {
        if (!digest(item.sha256)) continue;
        const url = '/api/scientific/adaptive/' + view.session_id + '/local-analysis/' + item.sha256 + '.png';
        verifiedFigure(host, item.title, url, 'image/png', options);
      }
      return;
    }
    const panel = section(host, report.title);
    panel.append(node('p', report.summary),
      node('p', 'Local analysis of this investigation’s inputs. The shared UMAP has a separate DevHarness receipt.'));
    for (const item of asList(report.findings)) {
      panel.append(node('h4', item.title), node('p', item.text));
    }
    if (tab === 'findings') {
      for (const item of asList(report.tables)) {
        panel.append(node('h4', item.caption), tableView(item.columns, item.rows, item.caption));
      }
    }
    if (tab === 'overview') {
      const actions = node('div');
      actions.className = 'studio-result-actions';
      actions.append(button('View developmental findings', () => options.navigate?.('findings')),
        button('View analysis figures', () => options.navigate?.('figures')));
      panel.append(actions);
    }
  }

  function motorUmapSession(host, tab, view, options) {
    const workflow = view.shared_motor_umap;
    if (workflow.status !== 'completed') {
      const panel = section(host, 'Shared motor-neuron UMAP');
      panel.append(pill(workflow.status, workflowStatus[workflow.status] || 'Unavailable'));
      if (workflow.error) panel.append(node('p', workflow.error));
      panel.append(button('Open Workflows', () => options.navigate?.('workflows')));
      return;
    }
    if (tab === 'figures') {
      for (const kind of ['umap', 'markers']) motorFigure(host, view, kind, options);
      motorDownloads(host, view, options);
      return;
    }
    const title = 'Motor-neuron composition by stage';
    const panel = section(host, tab === 'overview' ? 'Shared motor-neuron UMAP' : title);
    const rows = asList(workflow.stages);
    const percent = new Intl.NumberFormat(undefined, {style: 'percent', maximumFractionDigits: 1});
    if (tab === 'overview') {
      appendMotorOverview(panel, workflow, rows, percent, options);
      return;
    }
    const first = rows[0], last = rows.at(-1);
    if (rows.length > 1 && [first, last].every(row => Number.isFinite(row.neuron_share_of_motor))) {
      const rate = row => `${percent.format(row.neuron_share_of_motor)} at ${row.stage}`;
      panel.append(node('p', `Of the motor-labeled cells, neurons account for ${rate(first)} and ${rate(last)}.`));
    }
    if (rows.length) panel.append(tableView(
      ['Stage', 'All cells', 'Motor-labeled cells', 'Motor share of all cells',
        'Motor neuroblasts', 'Motor neurons', 'Neuron share of motor-labeled cells'],
      rows.map(row => [row.stage, row.all_cells, row.motor_cells,
        Number.isFinite(row.motor_share_of_all) ? percent.format(row.motor_share_of_all) : '—',
        row.neuroblast, row.neuron,
        Number.isFinite(row.neuron_share_of_motor) ? percent.format(row.neuron_share_of_motor) : '—']), title,
    ));
    appendMotorStageChanges(panel, rows);
    const limits = asList(workflow.method?.limitations);
    const interpretation = limits.length ? limits : asList(workflow.limitations);
    panel.append(disclosure('Interpretation', ...(interpretation.length ?
      interpretation : motorLimits).map(note => node('p', note))));
  }

  function appendMotorOverview(panel, workflow, rows, percent, options) {
    panel.append(node('h4', 'Run summary'));
    if (Number.isSafeInteger(workflow.cells) && rows.length) {
      const stages = new Intl.ListFormat().format(rows.map(row => row.stage));
      panel.append(node('p', `Mapped ${cellText(workflow.cells)} cells with motor-neuron or motor-neuroblast annotations across ${stages} on one shared UMAP.`));
    }
    const rates = rows.filter(row => Number.isFinite(row.neuron_share_of_motor) &&
      row.neuron_share_of_motor >= 0 && row.neuron_share_of_motor <= 1);
    if (rates.length > 1) {
      const [first, last] = [rates[0], rates.at(-1)];
      const change = (last.neuron_share_of_motor - first.neuron_share_of_motor) * 100;
      const changeText = change === 0 ? '' :
        ` The change from ${first.stage} to ${last.stage} was ${change > 0 ? '+' : '−'}${cellText(Number(Math.abs(change).toFixed(1)))} percentage points.`;
      const shares = new Intl.ListFormat().format(rates.map(row =>
        percent.format(row.neuron_share_of_motor) + ' at ' + row.stage));
      panel.append(node('h4', 'Key finding'),
        node('p', 'Among motor-labeled cells, the neuron-labeled share was ' + shares + '.' + changeText));
    }
    if (rates.length) {
      const figure = node('figure');
      figure.className = 'studio-motor-composition';
      const stages = node('ul');
      for (const row of rates) {
        const item = node('li');
        const meter = node('meter');
        meter.min = 0;
        meter.max = 1;
        meter.value = row.neuron_share_of_motor;
        const share = percent.format(meter.value);
        meter.setAttribute('aria-label', `Neuron share at ${row.stage}`);
        meter.setAttribute('aria-valuetext', share);
        const counts = Number.isSafeInteger(row.neuron) && Number.isSafeInteger(row.motor_cells)
          ? `${cellText(row.neuron)} of ${cellText(row.motor_cells)}` : '';
        const readout = node('span');
        readout.append(node('strong', share), node('small', counts));
        item.append(node('strong', row.stage), meter, readout);
        stages.append(item);
      }
      figure.append(node('figcaption', 'Neuron-labeled cells by stage'), stages);
      panel.append(figure);
    }
    const method = workflow.method;
    if (Number.isSafeInteger(workflow.features) && Number.isSafeInteger(method?.pca_dimensions)) {
      panel.append(node('h4', 'How to read the UMAP'),
        node('p', `The shared embedding used ${cellText(workflow.features)} genes and ${cellText(method.pca_dimensions)} PCA components. The stage panels show different subsets of that same embedding.`));
    }
    const recorded = asList(method?.limitations);
    const limits = recorded.length ? recorded : asList(workflow.limitations);
    if (limits.length) {
      const notes = node('ul');
      for (const limit of limits) notes.append(node('li', limit));
      notes.className = 'studio-motor-limits';
      panel.append(node('h4', 'Interpretation limits'), notes);
    }
    const actions = node('div');
    actions.className = 'studio-result-actions';
    actions.append(button('View findings', () => options.navigate?.('findings')),
      button('View workflow', () => options.navigate?.('workflows')),
      button('View figures', () => options.navigate?.('figures')));
    panel.append(actions);
  }

  function appendMotorStageChanges(panel, rows) {
    const comparisons = [];
    const signed = new Intl.NumberFormat(undefined, {
      signDisplay: 'exceptZero', maximumFractionDigits: 1,
    });
    for (let i = 1; i < rows.length; i++) {
      const [start, end] = [rows[i - 1], rows[i]];
      if (![start, end].every(row =>
        [row.neuroblast, row.neuron, row.neuron_share_of_motor].every(Number.isFinite))) continue;
      comparisons.push([
        `${start.stage} → ${end.stage}`,
        signed.format(end.neuroblast - start.neuroblast),
        signed.format(end.neuron - start.neuron),
        signed.format((end.neuron_share_of_motor - start.neuron_share_of_motor) * 100) + ' pp',
      ]);
    }
    if (comparisons.length) panel.append(node('h4', 'Changes between stages'),
      tableView(['Comparison', 'Motor neuroblasts', 'Motor neurons', 'Neuron share change'],
        comparisons, 'Changes between stages'),
      node('p', 'Differences use captured-cell counts. pp means percentage points.'));
  }

  function runTables(runs) {
    return runs.flatMap(run => asList(run.summary_tables)
      .map((table, index) => ({table, run, index})).filter(({table}) => verifiedTable(table)));
  }

  function sessionData(view, options) {
    const plugin = view.plugin_session || {};
    const runs = [...asList(plugin.runs), ...privatePlans(view).filter(plan =>
      plan.status !== 'completed' || verifiedPrivateRun(plan))];
    const steps = asList(view.graph?.entries).filter(row => row.entry_kind === 'step');
    const attempts = asList(view.graph?.entries).filter(row => row.entry_kind === 'attempt');
    const failures = ['failed', 'interrupted', 'cancelled', 'rejected'];
    const diagnostics = [...attempts, ...steps, ...runs].filter(row => failures.includes(row.status));
    const failureRows = diagnostics.map(row => [row.step_id || humanize(row.operation),
      humanize(row.status), humanize(row.reason || row.warnings?.[0]), row.generation ?? row.run_id]);
    const completed = runs.filter(run => run.status === 'completed');
    const local = asList(options.localTables).filter(verifiedTable);
    return {plugin, runs, completed, steps, failureRows, sessionId: view.session_id,
      pluginLinked: view.plugin_linked === true,
      packages: asList(view.packages), aims: asList(options.thesis?.aims),
      tables: [...runTables(completed),
        ...local.map(table => ({table}))]};
  }

  function completedSteps(data) {
    return data.pluginLinked ? data.steps.filter(step =>
      ['succeeded', 'completed'].includes(step.status)) : [];
  }

  function pluginRunReport(host, data, options, animate) {
    const run = data.completed[0];
    const steps = completedSteps(data);
    if (!run && !steps.length) return false;
    const report = section(host, 'Run summary');
    if (run) {
      const plan = asList(data.plugin.plans).find(item => item.plan_id === run.plan_id);
      const files = asList(plan?.inputs).map(input => input.name).filter(Boolean);
      const subjects = files.length ? files : [data.plugin.dataset_label].filter(Boolean);
      const operation = humanize(run.operation) || 'Analysis';
      report.append(node('p', operation.charAt(0).toUpperCase() + operation.slice(1) +
        ' completed' + (subjects.length ? ' for ' + new Intl.ListFormat().format(subjects) : '') + '.'));
      const metrics = Object.entries(run.metrics || {}).filter(([, value]) => Number.isFinite(value));
      if (metrics.length) report.append(metricsView(metrics.slice(0, 5)
        .map(([key, value]) => [humanize(key), value]), animate));
      const tables = asList(run.summary_tables).filter(verifiedTable);
      if (tables.length) {
        const list = node('ul');
        for (const table of tables) {
          const item = node('li', table.caption);
          if (table.rows.length === 1 && table.columns.length === 2) {
            const [label, value] = table.rows[0];
            item.append(node('span', ' · ' + cellText(label) + ' ' + cellText(value)));
          }
          list.append(item);
        }
        report.append(node('h4', 'Available results'), list);
      }
      if (asList(run.warnings).includes('verified_receipt_unavailable')) {
        report.append(node('p', 'The run completed. Its verified receipt is unavailable.'));
      }
      const limits = asList(run.limitations);
      if (limits.length) report.append(node('p', limits[0]));
    } else {
      report.append(node('p', 'Completed steps: ' + steps.map(step => humanize(step.step_id)).join(' · ') + '.'));
    }
    const actions = node('div');
    actions.className = 'studio-result-actions';
    actions.append(button('View findings', () => options.navigate?.('findings')),
      button('View workflow', () => options.navigate?.('workflows')));
    report.append(actions);
    return true;
  }

  function pluginWorkflow(host, view) {
    if (view?.plugin_linked !== true) return false;
    const data = sessionData(view, {});
    const steps = completedSteps(data);
    const comparison = view.retained_comparison;
    if (!data.completed.length && !steps.length && !comparison) return false;
    const record = section(host, 'Workflow record');
    for (const run of data.completed) {
      const operation = humanize(run.operation) || 'Analysis';
      const details = disclosure(operation.charAt(0).toUpperCase() + operation.slice(1) +
        (run.run_id ? ' · ' + run.run_id.slice(0, 8) : ''));
      details.open = data.completed[0] === run;
      const plan = asList(data.plugin.plans).find(item => item.plan_id === run.plan_id);
      const inputs = asList(plan?.inputs);
      if (inputs.length) details.append(tableView(['Input', 'Role', 'Format'],
        inputs.map(item => [item.name, humanize(item.role), item.file_type]), 'Run inputs'));
      if (plan?.design_summary && Object.keys(plan.design_summary).length)
        details.append(disclosure('Recorded settings', node('pre', JSON.stringify(plan.design_summary, null, 2))));
      const receipts = asList(run.tool_receipts);
      if (receipts.length) {
        details.append(node('h4', 'Recorded steps'));
        const list = node('ol');
        for (const receipt of receipts) {
          const name = receipt.step || receipt.operation || 'Tool execution';
          const item = node('li', name);
          if (digest(receipt.receipt_sha256)) item.append(digestButton(
            'Inspect receipt', receipt.receipt_sha256, {receipt_sha256: receipt.receipt_sha256, step_id: name},
          ));
          list.append(item);
        }
        details.append(list);
      } else if (asList(run.warnings).includes('verified_receipt_unavailable')) {
        details.append(node('p', 'The verified execution record is unavailable.'));
      }
      const results = asList(run.summary_tables).filter(verifiedTable);
      if (results.length) details.append(node('p', 'Outputs: ' + results.map(table => table.caption).join(' · ') + '.'));
      record.append(details);
    }
    if (steps.length) record.append(tableView(['Step', 'Status'], steps.map(step =>
      [humanize(step.step_id), humanize(step.status)]), 'Completed workflow steps'));
    if (comparison) {
      const stages = asList(comparison.stages);
      if (stages.length) record.append(tableView(['Stage', 'Dataset', 'Cells'], stages.map(stage =>
        [stage.label, stage.dataset, stage.cells]), 'Comparison inputs'));
      const figures = retainedFigures(view);
      if (figures.length) record.append(node('p', 'Retained plots: ' + figures.map(figure => figure.title).join(' · ') + '.'));
    }
    return true;
  }

  function overview(host, data, options, animate) {
    const {packages, completed, failureRows, aims, tables} = data;
    const report = pluginRunReport(host, data, options, animate);
    const qcRows = tables.find(({table}) => table.caption === 'Single-cell QC')?.table.rows;
    const metrics = Object.fromEntries(asList(qcRows).filter(Array.isArray));
    const composition = tables.find(({table}) => table.caption === 'Composition')?.table.rows;
    const input = Number.isFinite(metrics.cells_input) && Number.isFinite(metrics.cells_retained)
      ? ['Cells retained / input', metrics.cells_retained, ' / ' + cellText(metrics.cells_input)] : null;
    const inventory = completed.find(run => Number.isFinite(run.metrics?.genes))?.metrics;
    const figures = [
      input,
      inventory && ['Features', inventory.genes],
      Number.isFinite(inventory?.profiles) && ['Profiles', inventory.profiles],
      composition?.length && ['Biological units by condition', composition.map(([group, count]) => `${group} ${cellText(count)}`).join(' · ')],
    ].filter(Boolean);
    if (figures.length && !report) host.append(metricsView(figures, animate));
    if (packages[0]?.summary_md) {
      section(host, 'Research summary').append(markdown(packages[0].summary_md));
    }
    if (failureRows.length) {
      section(host, 'Workflow diagnostics')
        .append(button('View failed attempts', () => options.navigate?.('findings')));
    }
    if (aims.length) {
      const content = node('div');
      for (const aim of aims) {
        content.append(node('h4', aim.question || aim.aim_id), pill(aim.status));
        for (const explanation of asList(aim.competing_explanations)) {
          content.append(node('p', explanation.hypothesis || explanation.explanation || explanation.description || explanation.explanation_id));
        }
      }
      content.append(button('Review aims in Workflows', () => options.navigate?.('workflows')));
      host.append(disclosure('Aims and competing explanations', content));
    }
    if (!host.children.length) {
      const next = section(host, 'Investigation');
      next.append(node('p', tables.length ? 'Numerical results are available.' : 'Review the workflow to see what is ready to run.'),
        tables.length ? button('View findings', () => options.navigate?.('findings')) :
          button('Open Workflows', () => options.navigate?.('workflows')));
    }
  }

  function findingTables(host, tables) {
    for (const {table, run} of tables) {
      const card = section(host, table.caption);
      const duplicates = tables.some(item => item.table !== table && item.table.caption === table.caption);
      if (duplicates && run?.run_id) card.append(node('p', 'Run ' + run.run_id.slice(0, 8)));
      card.append(tableView(table.columns, table.rows, table.caption));
      if (run?.probability_bounds_status === 'verified')
        card.append(disclosure('Probability check', node('p', 'Verified 0 ≤ p ≤ q ≤ 1.')));
    }
  }

  function findings(host, data) {
    findingTables(host, data.tables);
    if (!data.tables.length)
      section(host, 'Findings').append(node('p', 'No numerical results are available.'));
    if (data.failureRows.length) {
      const failures = disclosure('Failed attempts and diagnostics',
        tableView(['Attempt', 'Status', 'Diagnostic', 'Reference'], data.failureRows, 'Failed attempts and diagnostics'));
      failures.open = !data.tables.length;
      host.append(failures);
    }
  }

  function chartData(table) {
    const rows = table.rows.filter(Array.isArray);
    const validCount = value => Number.isSafeInteger(value) && value >= 0;
    if (table.caption === 'QC retention by group') return {title: 'Cell retention (%)', ceiling: 100,
      values: rows.map(([name, input, retained]) => ({
        name, value: validCount(retained) && validCount(input) && input > 0 && retained <= input
          ? 100 * retained / input : null,
        note: `${cellText(retained)} of ${cellText(input)} cells retained`,
      }))};
    if (table.caption === 'Doublet predictions') return {title: 'Predicted doublets (%)', ceiling: 100,
      values: rows.map(([name, cells, predictions, rate]) => ({
        name, value: Number.isFinite(rate) && rate >= 0 && rate <= 1 ? 100 * rate : null,
        note: validCount(predictions) && validCount(cells) && predictions <= cells
          ? `${cellText(predictions)} of ${cellText(cells)} cells flagged` : '',
      }))};
    if (table.caption === 'Composition effects') {
      const fields = ['Cell type', 'Covariate', 'Posterior inclusion probability', 'Selected', 'Coefficient'];
      const indices = fields.map(name => table.columns.indexOf(name));
      if (indices.includes(-1)) return null;
      const lowerIndex = table.columns.indexOf('Credible interval lower');
      const upperIndex = table.columns.indexOf('Credible interval upper');
      return {title: 'scCODA relative log coefficients', composition: true, values: rows.map(row => {
        const [type, covariate, pip, selected, value] = indices.map(index => row[index]);
        const lower = row[lowerIndex], upper = row[upperIndex];
        return {name: `${type} · ${covariate}`, value, selected,
          interval: Number.isFinite(lower) && Number.isFinite(upper) && lower <= value && value <= upper
            ? [lower, upper] : null,
          note: Number.isFinite(pip) && pip >= 0 && pip <= 1 ? `PIP ${cellText(pip)}` : '',
        };
      })};
    }
    if (table.caption === 'Expression audit') return {title: 'Nonzero features per profile', values: rows
      .filter(([metric]) => /^nonzero_features_(p25|p75|median)$/.test(metric))
      .map(([metric, value]) => ({name: humanize(metric.replace('nonzero_features_', '')), value}))};
    return null;
  }

  function receiptChart(parent, table, options, plotly) {
    const chart = chartData(table);
    if (!chart) return false;
    const rows = chart.values.filter(({value}) => Number.isFinite(value)).slice(0, 16);
    if (!rows.length) return false;
    const canConvert = chart.composition && rows.every(({value, interval}) => interval &&
      [value, ...interval].map(Math.exp).every(result => Number.isFinite(result) && result > 0));
    const card = section(parent, chart.title, 'studio-session-card studio-chart');
    const area = node('div'), tip = node('p');
    area.className = 'studio-chart-area';
    tip.className = 'studio-chart-tip';
    tip.setAttribute('role', 'tooltip');
    tip.hidden = true;
    const add = (tag, attrs, text) => {
      const element = root.document.createElementNS('http://www.w3.org/2000/svg', tag);
      for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, String(value));
      if (text !== undefined) element.textContent = text;
      return element;
    };
    const render = ratio => {
      const transform = ratio ? Math.exp : value => value;
      const neutral = ratio ? 1 : 0;
      const all = rows.flatMap(({value, interval}) => [value, ...(interval || [])].map(transform));
      const smallest = Math.min(neutral, ...all);
      const largest = Math.max(neutral, chart.ceiling || neutral, ...all);
      const scale = Math.max(Math.abs(smallest), Math.abs(largest), 1);
      const offset = value => 212 + 344 * (value / scale - smallest / scale) /
        ((largest / scale - smallest / scale) || 1);
      const title = ratio ? 'scCODA relative fold ratios' : chart.title;
      const height = rows.length * 30 + 28;
      const svg = add('svg', {viewBox: `0 0 720 ${height}`, role: 'group', 'aria-label': title, 'data-studio-chart': 'receipt'});
      const format = value => cellText(value) + (ratio ? '×' : chart.ceiling === 100 ? '%' : '');
      svg.append(add('title', {}, title), add('line', {
        x1: offset(neutral), x2: offset(neutral), y1: 6, y2: height - 19, class: 'studio-chart-axis',
      }), add('text', {x: offset(neutral), y: height - 3, 'text-anchor': 'middle'}, format(neutral)));
      rows.forEach(({name, value, interval, note, selected}, index) => {
        const y = 22 + index * 30, middle = y - 4;
        const measure = transform(value);
        const span = interval?.map(transform);
        const axisX = offset(neutral), valueX = offset(measure);
        const color = measure < neutral ? 'studio-chart-negative' : 'studio-chart-positive';
        const description = [name, format(measure), span ? `Interval ${format(span[0])} to ${format(span[1])}` : '',
          note, typeof selected === 'boolean' ? (selected ? 'Selected' : 'Unselected') : ''].filter(Boolean).join(' · ');
        const mark = add('g', {tabindex: 0, role: 'img', 'aria-label': description, class: 'studio-chart-mark'});
        mark.append(add('rect', {x: 0, y: y - 18, width: 720, height: 27, fill: 'transparent'}));
        if (chart.composition) {
          mark.append(add('line', {x1: axisX, x2: valueX, y1: middle, y2: middle, class: 'studio-chart-axis'}));
          if (span) mark.append(add('line', {x1: offset(span[0]), x2: offset(span[1]), y1: middle,
            y2: middle, stroke: 'currentColor', 'stroke-width': 2}));
          mark.append(add('circle', {cx: valueX, cy: middle, r: selected ? 6 : 4, class: color}));
        } else {
          mark.append(add('rect', {x: Math.min(axisX, valueX), y: y - 12,
            width: Math.max(1.5, Math.abs(valueX - axisX)), height: 14, rx: 3, class: color}));
        }
        mark.append(add('text', {x: 8, y}, String(name).slice(0, 30)),
          add('text', {x: 576, y}, format(measure)));
        const show = () => { tip.textContent = description; tip.hidden = false; };
        const hide = () => { tip.hidden = true; };
        mark.addEventListener('pointerenter', show);
        mark.addEventListener('pointerleave', hide);
        mark.addEventListener('focus', show);
        mark.addEventListener('blur', hide);
        mark.addEventListener('keydown', event => { if (event.key === 'Escape') hide(); });
        svg.append(mark);
      });
      tip.hidden = true;
      area.replaceChildren(svg, tip);
    };
    const plot = node('div');
    card.append(plot, disclosure('Numeric sidecar', node('p', 'Derived from verified numeric receipt table'),
      tableView(table.columns, table.rows, table.caption)));
    showFigure(parent, plot, chart.title, plotly && options.plotlyFrame?.(plotly), () => {
      if (canConvert) {
        const controls = node('div');
        controls.className = 'studio-chart-controls';
        controls.setAttribute('role', 'group');
        controls.setAttribute('aria-label', 'Effect scale');
        const select = ratio => {
          log.setAttribute('aria-pressed', String(!ratio));
          fold.setAttribute('aria-pressed', String(ratio));
          render(ratio);
        };
        const log = button('Log coefficient', () => select(false));
        const fold = button('Fold ratio', () => select(true));
        controls.append(log, fold);
        plot.append(controls);
        select(false);
      } else render(false);
      plot.append(area);
    });
    return true;
  }

  function receiptPlotPath(data, run, index) {
    const route = run?.plotly_paths?.[String(index)];
    if (typeof route !== 'string') return null;
    const match = /^\/api\/scientific\/adaptive\/([a-f0-9]{32})\/receipt-plot\/([a-f0-9]{32})\/(\d{1,3})\/[a-f0-9]{64}\/plotly$/.exec(route);
    return match?.[0] === route && match[1] === data.sessionId &&
      match[2] === run.run_id && match[3] === String(index) ? route : null;
  }

  function figures(host, data, options) {
    let shown = 0;
    for (const {table, run, index} of data.tables)
      if (receiptChart(host, table, options, receiptPlotPath(data, run, index))) shown++;
    for (const run of data.completed) {
      if (!/^data:image\/png;base64,[A-Za-z0-9+/=]{1,48000}$/.test(run.figure_preview || '')) continue;
      const image = node('img');
      image.src = run.figure_preview;
      image.alt = 'Retained figure from ' + humanize(run.operation);
      image.className = 'studio-figure-preview';
      section(host, humanize(run.operation)).append(image);
      shown++;
    }
    if (!shown) section(host, 'Figures').append(node('p', 'No verified figures are available.'));
  }

  function downloadStudioBlob(blob, filename) {
    const url = root.URL.createObjectURL(blob);
    const link = node('a');
    link.href = url;
    link.download = filename;
    link.click();
    root.setTimeout(() => root.URL.revokeObjectURL(url), 1000);
  }

  function copyCode(label, text, pre) {
    return button(label, async event => {
      try { await root.navigator.clipboard.writeText(text); event.target.textContent = 'Copied'; }
      catch (_) { pre.tabIndex = 0; pre.focus(); }
    });
  }

  function recordedRunCode(host, data) {
    if (!data.runs.length) return;
    const panel = section(host, 'Recorded executions');
    const plans = asList(data.plugin.plans);
    for (const run of data.runs) {
      const plan = plans.find(candidate => candidate.plan_id === run.plan_id);
      const request = digest(plan?.plan_sha256) && plan.plan_id
        ? {tool: 'execute_reviewed_plan', arguments: {
          session_id: data.sessionId, plan_id: plan.plan_id, plan_sha256: plan.plan_sha256,
        }} : undefined;
      const record = {
        format: 'devbench.studio-code-record/1',
        operation: run.operation,
        status: run.status,
        run_id: run.run_id,
        plan_id: run.plan_id,
        plan_sha256: plan?.plan_sha256,
        inputs: plan?.inputs,
        settings: plan?.design_summary,
        runtime: plan?.runtime,
        execution_request: request,
        replay_bundle_sha256: run.replay_bundle_sha256,
        execution_steps: run.tool_receipts,
      };
      const facts = [
        ['Operation', record.operation], ['Run ID', record.run_id], ['Status', record.status],
      ];
      if (request) facts.push(['DevHarness tool', request.tool]);
      const outline = disclosure(humanize(run.operation || 'Analysis') +
        (run.run_id ? ' · ' + run.run_id.slice(0, 8) : ''));
      outline.open = run === data.runs[0];
      outline.append(tableView(['Field', 'Recorded value'], facts, 'Execution identity'));
      if (digest(record.plan_sha256)) outline.append(digestButton('Frozen plan', record.plan_sha256,
        {plan_sha256: record.plan_sha256, step_id: run.operation}));
      if (digest(record.replay_bundle_sha256)) outline.append(digestButton(
        'Replay bundle SHA-256', record.replay_bundle_sha256, {artifact_sha256: record.replay_bundle_sha256}));
      const text = JSON.stringify(record, null, 2), pre = node('pre', text);
      outline.append(disclosure('Run record JSON', pre, copyCode('Copy run record', text, pre),
        button('Download run record', () => downloadStudioBlob(
          new Blob([text + '\n'], {type: 'application/json'}), 'devharness-run-record.json',
        ))));
      panel.append(outline);
    }
  }

  function codeView(host, data, options) {
    recordedRunCode(host, data);
    const recorded = asList(data.plugin.plans);
    const plans = recorded.length ? section(host, 'Frozen plans') : null;
    for (const plan of recorded) {
      const text = JSON.stringify(plan, null, 2);
      const pre = node('pre', text);
      plans.append(disclosure(`${humanize(plan.operation || 'Plan')} · ${String(plan.plan_id || '').slice(0, 8)}`,
        node('code', plan.plan_sha256),
        copyCode('Copy plan JSON', text, pre), pre));
    }
    const images = new Set([data.plugin.mounted_image_id,
      ...data.packages.flatMap(pkg => asList(pkg.provenance_summary?.runtime))].filter(Boolean));
    if (images.size) host.append(disclosure('Pinned runtime', ...Array.from(images, image => node('code', image))));
    for (const pkg of data.packages) {
      if (pkg.replay_script) {
        const pre = node('pre', pkg.replay_script);
        host.append(disclosure('Retained replay script', pre,
          copyCode('Copy replay script', pkg.replay_script, pre),
          button('Download replay script', () => downloadStudioBlob(
            new Blob([pkg.replay_script], {type: 'text/plain'}), 'replay.py',
          ))));
      }
    }
    for (const run of data.runs) {
      if (run.nextflow_trace) host.append(disclosure('nextflow-trace.tsv', node('pre', run.nextflow_trace)));
    }
    if (data.completed.length && options.navigate)
      host.append(button('View evidence', () => options.navigate('evidence')));
    if (!host.children.length) section(host, 'Code').append(node('p', 'No recorded code is available.'));
  }

  function digestButton(title, checksum, record) {
    const control = button(title + ' · ', () => drawerController?.inspect(record));
    const token = node('code', checksum.slice(0, 12));
    token.className = 'studio-mono';
    control.title = checksum;
    control.append(token);
    return control;
  }

  function motorDocuments(host, tab, view, options) {
    const workflow = view.shared_motor_umap;
    if (tab === 'code') {
      motorCode(host, view, options);
      const settings = workflow.parameters;
      if (settings) section(host, 'Run settings').append(tableView(
        ['Stage column', 'Annotation column', 'Counts layer'],
        [[settings.stage_column, settings.label_column, settings.counts_layer]], 'Run settings',
      ));
      const method = workflow.method || {}, umap = method.umap || {};
      const recorded = [
        ['Normalization', method.normalization], ['PCA components', method.pca_dimensions],
        ['Genes used for PCA', workflow.features], ['UMAP neighbors', umap.n_neighbors],
        ['UMAP minimum distance', umap.min_dist], ['Distance metric', umap.metric],
        ['UMAP seed', umap.seed],
      ].filter(([, value]) => value != null);
      if (recorded.length) section(host, 'Recorded fitting parameters').append(
        tableView(['Setting', 'Value'], recorded, 'Recorded fitting parameters'));
      if (digest(workflow.plan_sha256)) section(host, 'Frozen plan').append(
        digestButton('Plan SHA-256', workflow.plan_sha256, {
          plan_sha256: workflow.plan_sha256, step_id: 'Shared motor-neuron UMAP plan',
        }),
      );
      const url = motorArtifact(view, 'parameters');
      if (url) motorDownloads(host, view, options, ['parameters']);
      if (options.navigate) host.append(button('View evidence', () => options.navigate('evidence')));
      return;
    }
    const files = Object.entries(motorArtifacts).filter(([kind]) => motorArtifact(view, kind));
    if (files.length) {
      const panel = section(host, 'Verified artifacts');
      for (const [kind, [title]] of files) {
        const checksum = workflow.artifacts[kind].sha256;
        panel.append(digestButton(title, checksum, {
          artifact_sha256: checksum, step_id: 'Shared motor-neuron UMAP', delivery_status: 'verified',
        }));
      }
    } else section(host, 'Evidence').append(node('p', 'Verified artifacts appear after the workflow completes.'));
    host.append(disclosure('Research limits', node('p', 'Clinical release is disabled.'),
      ...(asList(workflow.limitations).length ? workflow.limitations : motorLimits).map(note => node('p', note))));
  }

  function motorCode(host, view, options) {
    const code = view.shared_motor_umap.code;
    if (!code) return;
    const recorded = section(host, 'Recorded entry point');
    recorded.append(node('code', code.entry_point));
    const runtime = code.runtime || {};
    if (runtime.python) recorded.append(node('p', 'Python ' + runtime.python));
    const sourceHash = runtime.implementation_sha256;
    if (digest(sourceHash)) recorded.append(digestButton('Implementation SHA-256',
      sourceHash, {artifact_sha256: sourceHash, step_id: 'Shared motor-neuron UMAP'}));
    if (asList(code.inputs).length) section(host, 'Frozen input order').append(
      tableView(['Stage', 'Input', 'SHA-256'], code.inputs.map(input =>
        [input.stage, input.name, input.sha256]), 'Frozen input order'));
    const versions = Object.entries(runtime.packages || {});
    if (versions.length) host.append(disclosure('Package versions', tableView(
      ['Package', 'Version'], versions, 'Recorded package versions')));
    if (!options.artifact) return;
    for (const [kind, title, filename] of [
      ['source', 'Verified implementation source', 'shared_motor_umap.py'],
      ['replay', 'Generated replay script', 'replay_shared_motor_umap.py'],
    ]) {
      const artifact = code[kind];
      const path = '/api/scientific/adaptive/' + view.session_id + '/motor-umap/code/' +
        kind + '/' + artifact?.sha256;
      if (!digest(artifact?.sha256) || artifact.url !== path) continue;
      const panel = section(host, title);
      if (kind === 'replay' && typeof code.command === 'string') {
        const command = node('pre', code.command);
        panel.append(node('p', 'The script checks input hashes and runtime versions before invoking the recorded entry point.'), command,
          copyCode('Copy replay command', code.command, command));
      }
      const preview = disclosure('Preview code', node('p', 'Loading verified bytes…'));
      preview.ontoggle = async () => {
        if (!preview.open || preview.dataset.loaded) return;
        preview.dataset.loaded = 'true';
        try {
          const blob = await options.artifact(path, 'text/plain');
          preview.lastElementChild.replaceWith(node('pre', await blob.text()));
        } catch (_) {
          preview.lastElementChild.textContent = 'Code preview unavailable.';
        }
      };
      panel.append(preview, button('Download ' + (kind === 'source' ? 'source' : 'replay script'), async () => {
        try { downloadStudioBlob(await options.artifact(path, 'text/plain'), filename); }
        catch (error) { options.message?.(error.message || 'Code download unavailable.', true); }
      }));
    }
  }

  function evidence(host, data, options) {
    const local = asList(options.thesis?.packages).filter(row => row.status === 'stale');
    const packages = data.packages.length || local.length
      ? section(host, data.packages.length ? 'Verified Research Packages' : 'Research packages') : null;
    for (const pkg of data.packages) {
      if (digest(pkg.root_sha256)) packages.append(digestButton(humanize(pkg.outcome || 'Package'),
        pkg.root_sha256, {package_sha256: pkg.root_sha256, step_id: 'Research package', delivery_status: 'verified'}));
      if (pkg.download_path) packages.append(button('Download .zip', () => options.download?.(pkg.download_path)));
      if (pkg.methods_md) packages.append(disclosure('Methods', markdown(pkg.methods_md)));
    }
    if (local.length) root.WorkbenchThesisStudio?.packageTable(packages, local, options.thesis.session_id, {download: options.download, message: options.message});
    const receipts = [];
    const emitted = new Set();
    const appendReceipt = (ref, step, run) => {
      const checksum = ref?.receipt_sha256 || ref?.event_sha256 || ref?.sha256;
      if (!digest(checksum) || emitted.has(checksum)) return;
      emitted.add(checksum);
      receipts.push(digestButton(humanize(step || run?.operation || 'Receipt'), checksum,
        {...ref, receipt_sha256: checksum, step_id: step, delivery_status: 'verified'}));
    };
    for (const run of data.completed) {
      appendReceipt(run.graph_execution, run.graph_execution?.step_id, run);
      for (const [step, ref] of Object.entries(run.tool_receipts || {})) appendReceipt(ref, ref?.step || ref?.tool || step, run);
      if (digest(run.replay_bundle_sha256)) appendReceipt({receipt_sha256: run.replay_bundle_sha256}, 'Replay bundle', run);
    }
    for (const ref of asList(options.localReceipts)) appendReceipt(ref, ref.step_id);
    if (receipts.length) section(host, 'Tool execution receipts').append(...receipts);
    const provenance = [];
    if (data.plugin.mounted_image_id)
      provenance.push(node('p', 'Pinned OCI image'), node('code', data.plugin.mounted_image_id));
    for (const pkg of data.packages) {
      const source = pkg.provenance_summary || {};
      provenance.push(node('h4', 'Original inputs'), node('pre', JSON.stringify(source.original_inputs || [], null, 2)),
        node('h4', 'Derived artifacts'), node('pre', JSON.stringify(source.derived_artifacts || [], null, 2)));
    }
    if (provenance.length) host.append(disclosure('Provenance manifest', ...provenance));
    if (!host.children.length) section(host, 'Evidence').append(node('p', 'No verified receipts or research packages are available.'));
    const limitations = Array.from(new Set(data.runs.flatMap(run => asList(run.limitations))), note => node('p', note));
    host.append(disclosure('Research limits', node('p', 'Clinical release is disabled pending independent scientific review and clinical validation.'),
      ...limitations));
  }

  function renderSession(host, tab, view, options) {
    const views = {overview, findings, figures, code: codeView, evidence};
    const key = view && `${view.session_id}:${tab}`;
    const animate = metricView.get(host) !== key;
    metricView.set(host, key);
    const mounted = motorGallery.get(host);
    if (mounted) {
      mounted.abort.abort();
      for (const url of mounted.urls) root.URL.revokeObjectURL(url);
      motorGallery.delete(host);
    }
    host.replaceChildren();
    host.hidden = !view || !views[tab];
    if (host.hidden) return;
    if (view.shared_motor_umap && ['overview', 'findings', 'figures'].includes(tab)) {
      localAnalysis(host, tab, view, options);
      if (tab === 'figures') retainedFigureCards(host, view, options);
      motorUmapSession(host, tab, view, options);
      if (tab === 'findings') findingTables(host, runTables(privatePlans(view).filter(verifiedPrivateRun)));
      return;
    }
    if (view.shared_motor_umap && ['code', 'evidence'].includes(tab)) {
      motorDocuments(host, tab, view, options);
      return;
    }
    if (typeof view.retained_comparison?.title === 'string' && ['overview', 'findings', 'figures'].includes(tab)) {
      retainedComparison(host, tab, view, options);
      if (tab === 'findings') findingTables(host, runTables(privatePlans(view).filter(verifiedPrivateRun)));
      return;
    }
    const data = sessionData(view, options);
    if (tab === 'overview') overview(host, data, options, animate);
    else if (tab === 'code') codeView(host, data, options);
    else if (tab === 'evidence') evidence(host, data, options);
    else views[tab](host, data, options);
  }

  root.WorkbenchStudio = {
    sessionLink,
    handoffView,
    mountDrawer,
    mountTabIndicator,
    revealCanvas,
    inspectReceipt: record => drawerController?.inspect(record),
    closeReceipt: () => drawerController?.close(false),
    setContext: controller => { contextController = controller; },
    get addContext() { return contextController ? addContext : undefined; },
    motorWorkflow,
    privateWorkflow,
    pluginWorkflow,
    renderSession,
    renderSessionRail,
  };
})(window);
