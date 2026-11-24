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
      } else {
        content.append(node('p', 'A verified preview is unavailable for this receipt.'));
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
    card.append(node('h3', title));
    parent.append(card);
    return card;
  }

  function pill(state, text = label(state)) {
    const badge = node('span', text);
    badge.className = 'studio-status';
    badge.setAttribute('data-status', state);
    return badge;
  }

  function renderSessionRail(host, sessions, selectedId, open) {
    host.replaceChildren();
    const plugins = (sessions || []).filter(item => item.origin === 'plugin' &&
      /^[a-f0-9]{32}$/.test(item.session_id));
    host.hidden = plugins.length === 0;
    if (host.hidden) return;
    host.append(node('h3', 'Codex Plugin Sessions'));
    for (const item of plugins) {
      const entry = button(null, () => open(item.session_id));
      entry.className = 'studio-session-entry';
      entry.setAttribute('aria-current', String(item.session_id === selectedId));
      entry.append(node('strong', item.dataset_label || 'Plugin investigation'),
        node('span', item.objective), pill(item.status),
        node('small', `${item.step_count} steps · ${item.aim_count} aims`));
      host.append(entry);
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

  function retainedFigures(view) {
    return asList(view.retained_comparison?.figures).filter(figure => {
      if (typeof figure?.title !== 'string' || typeof figure.download_path !== 'string') return false;
      const route = /^\/api\/scientific\/adaptive\/([a-f0-9]{32})\/comparison\/[a-f0-9]{64}\.png$/.exec(figure.download_path);
      return route !== null && route[0] === figure.download_path && route[1] === view.session_id;
    });
  }

  function handoffView(tab, view) {
    return tab === 'workflows' && view.plugin_linked && retainedFigures(view).length ? 'figures' : tab;
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
        const cell = node('td');
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
    const panel = section(host, comparison.title);
    if (tab === 'overview') {
      const stages = asList(comparison.stages);
      const cellCount = stages.reduce((total, stage) => total + stage.cells, 0);
      panel.append(pill('succeeded', 'Completed offline comparison'),
        node('p', `${cellCount.toLocaleString()} cells across ${stages.length} datasets.`),
        tableView(['Stage', 'Dataset', 'Cells'], stages
          .map(({label, dataset, cells}) => [label, dataset, cells]), 'Comparison datasets'));
      const links = node('div');
      links.className = 'studio-session-meta';
      links.append(button('View findings', () => options.navigate?.('findings')),
        button('View figures', () => options.navigate?.('figures')));
      panel.append(links);
    } else {
      if (tab === 'figures') {
        for (const figure of retainedFigures(view)) {
          const card = section(panel, figure.title, 'studio-table-card');
          const image = node('img');
          image.src = figure.download_path;
          image.alt = figure.title;
          image.className = 'studio-retained-figure';
          card.append(image);
        }
      }
      for (const table of asList(comparison.tables).filter(verifiedTable)) {
        section(panel, table.caption, 'studio-table-card')
          .append(tableView(table.columns, table.rows, table.caption));
      }
      for (const note of asList(comparison.limitations)) panel.append(node('p', note));
    }
  }

  function sessionData(view, options) {
    const plugin = view.plugin_session || {}, runs = asList(plugin.runs);
    const steps = asList(view.graph?.entries).filter(row => row.entry_kind === 'step');
    const attempts = asList(view.graph?.entries).filter(row => row.entry_kind === 'attempt');
    const failures = ['failed', 'interrupted', 'cancelled', 'rejected'];
    const diagnostics = [...attempts, ...steps, ...runs].filter(row => failures.includes(row.status));
    const failureRows = diagnostics.map(row => [row.step_id || humanize(row.operation),
      humanize(row.status), humanize(row.reason || row.warnings?.[0]), row.generation ?? row.run_id]);
    const completed = runs.filter(run => run.status === 'completed');
    const local = asList(options.localTables).filter(verifiedTable);
    return {plugin, runs, completed, steps, failureRows,
      packages: asList(view.packages), aims: asList(options.thesis?.aims),
      tables: [...completed.flatMap(run => asList(run.summary_tables).filter(verifiedTable)
        .map(table => ({table, run}))), ...local.map(table => ({table}))]};
  }

  function overview(host, view, data, options) {
    const {packages, steps, completed, failureRows, aims, tables} = data;
    const metrics = Object.fromEntries(tables.find(({table}) => table.caption === 'Single-cell QC')?.table.rows || []);
    const composition = tables.find(({table}) => table.caption === 'Composition')?.table.rows;
    const input = Number.isFinite(metrics.cells_input) && Number.isFinite(metrics.cells_retained)
      ? `${cellText(metrics.cells_retained)} / ${cellText(metrics.cells_input)}` : null;
    const inventory = completed.find(run => Number.isFinite(run.metrics?.genes))?.metrics;
    const counts = options.counts || {};
    const kpis = node('div');
    kpis.className = 'studio-session-kpis';
    const figures = [
      input && ['Cells retained / input', input],
      inventory && ['Features / profiles', `${cellText(inventory.genes)} / ${cellText(inventory.profiles)}`],
      composition?.length && ['Biological units by condition', composition.map(([group, count]) => `${group} ${cellText(count)}`).join(' · ')],
      ['Completed / failed steps', `${Math.max(steps.filter(row => row.status === 'succeeded').length, completed.length)} / ${counts.failed_step_count ?? new Set(failureRows.map(row => row[0])).size}`],
      ['Active aims', String(aims.length || counts.aim_count || 0)],
      ['Research package', packages.length ? 'Verified' : 'Pending'],
    ];
    for (const [title, value] of figures.filter(Boolean)) {
      const tile = node('div');
      tile.append(node('span', title), node('strong', value));
      kpis.append(tile);
    }
    host.append(kpis, pill('unresolved', 'Clinical release disabled'));
    const summary = section(host, 'Session summary');
    if (packages[0]?.summary_md) summary.append(markdown(packages[0].summary_md));
    else summary.append(node('p', data.plugin.objective || view.session_summary?.objective || 'Review the recorded aims, workflow and evidence for this investigation.'));
    if (failureRows.length) {
      const attention = section(host, 'Failed steps need attention');
      attention.append(tableView(['Attempt', 'Status', 'Diagnostic', 'Reference'], failureRows.slice(0, 4), 'Recent failed attempts'),
        button('Open failed attempts', () => options.navigate?.('findings')));
    }
    if (aims.length) {
      const active = section(host, 'Aims & competing explanations');
      for (const aim of aims) {
        active.append(node('h4', aim.question || aim.aim_id), pill(aim.status));
        for (const explanation of asList(aim.competing_explanations)) {
          active.append(node('p', explanation.hypothesis || explanation.explanation || explanation.description || explanation.explanation_id));
        }
      }
      active.append(button('Review aims in Workflows', () => options.navigate?.('workflows')));
    }
  }

  function findings(host, data) {
    const results = section(host, 'Numerical findings');
    for (const {table, run} of data.tables) {
      const card = section(results, table.caption, 'studio-table-card');
      if (run?.operation) card.append(node('p', humanize(run.operation) + ' · ' + run.run_id?.slice(0, 8)));
      if (run?.probability_bounds_status === 'verified') card.append(pill('succeeded', 'Verified 0 ≤ p ≤ q ≤ 1'));
      card.append(tableView(table.columns, table.rows, table.caption));
    }
    if (!data.tables.length) results.append(node('p', 'No current numerical tables are available. Review the recorded workflow and its evidence holds.'));
    const failures = section(host, 'Failed Attempts & Diagnostics');
    failures.append(data.failureRows.length ? tableView(['Attempt', 'Status', 'Diagnostic', 'Reference'],
      data.failureRows, 'Failed attempts and diagnostics') : node('p', 'No failed attempts are recorded in this view.'));
  }

  function chartData(table) {
    const rows = table.rows.filter(Array.isArray);
    if (table.caption === 'QC retention by group') return ['Cell retention (%)', rows.map(([group, input, retained]) =>
      [group, Number.isFinite(retained) && input > 0 ? 100 * retained / input : null]), 100];
    if (table.caption === 'Doublet predictions') return ['Predicted doublets (%)', rows.map(([capture, , , rate]) =>
      [capture, Number.isFinite(rate) ? 100 * rate : null]), 100];
    if (table.caption === 'Composition effects') return ['scCODA relative log coefficients', rows.map(([type, covariate, pip, , effect]) =>
      [`${type} · ${covariate}`, effect, Number.isFinite(pip) ? `PIP ${cellText(pip)}` : ''])];
    if (table.caption === 'Expression audit') return ['Nonzero features per profile', rows
      .filter(([metric]) => /^nonzero_features_(p25|p75|median)$/.test(metric))
      .map(([metric, value]) => [humanize(metric.replace('nonzero_features_', '')), value])];
    return null;
  }

  function receiptChart(parent, table) {
    const chart = chartData(table);
    if (!chart) return false;
    const [title, values, ceiling] = chart;
    const rows = values.filter(([, value]) => Number.isFinite(value)).slice(0, 16);
    if (!rows.length) return false;
    const smallest = Math.min(0, ...rows.map(([, value]) => value));
    const largest = Math.max(0, ceiling || 0, ...rows.map(([, value]) => value));
    const offset = value => 210 + 340 * (value - smallest) / (largest - smallest || 1);
    const add = (tag, attrs, text) => {
      const element = root.document.createElementNS('http://www.w3.org/2000/svg', tag);
      for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, String(value));
      if (text !== undefined) element.textContent = text;
      return element;
    };
    const card = section(parent, title, 'studio-chart');
    card.append(pill('observed', 'Derived from verified numeric receipt table'));
    const height = rows.length * 29 + 24;
    const svg = add('svg', {viewBox: `0 0 720 ${height}`, role: 'img', 'aria-label': title, 'data-studio-chart': 'receipt'});
    svg.append(add('title', {}, title), add('line', {x1: offset(0), x2: offset(0), y1: 6, y2: height - 8, class: 'studio-chart-axis'}));
    rows.forEach(([name, value, note], index) => {
      const y = 23 + index * 29;
      const bar = add('rect', {x: Math.min(offset(0), offset(value)), y: y - 12,
        width: Math.abs(offset(value) - offset(0)), height: 14, rx: 3,
        class: value < 0 ? 'studio-chart-negative' : 'studio-chart-positive'});
      svg.append(add('text', {x: 8, y}, String(name).slice(0, 32)), bar,
        add('text', {x: 560, y}, [cellText(value), note].filter(Boolean).join(' · ')));
    });
    const sidecar = node('details');
    sidecar.append(node('summary', 'Numeric sidecar'), tableView(table.columns, table.rows, table.caption));
    card.append(svg, sidecar);
    return true;
  }

  function figures(host, data) {
    const panel = section(host, 'Figures from verified receipts');
    let shown = 0;
    for (const run of data.completed) {
      if (!/^data:image\/png;base64,[A-Za-z0-9+/=]{1,48000}$/.test(run.figure_preview || '')) continue;
      const image = node('img');
      image.src = run.figure_preview;
      image.alt = 'Retained figure from ' + humanize(run.operation);
      image.className = 'studio-figure-preview';
      panel.append(image);
      shown++;
    }
    for (const {table} of data.tables) if (receiptChart(panel, table)) shown++;
    if (!shown) panel.append(node('p', 'No receipt-derived figure is available for this revision.'));
  }

  function codeView(host, data) {
    const plans = section(host, 'Frozen plans');
    for (const plan of asList(data.plugin.plans)) {
      const details = node('details');
      const text = JSON.stringify(plan, null, 2);
      const pre = node('pre', text);
      details.append(node('summary', `${humanize(plan.operation || 'Plan')} · ${plan.plan_id}`),
        node('code', plan.plan_sha256), node('p', 'Frozen plan design projection'),
        button('Copy plan JSON', async event => {
          try { await root.navigator.clipboard.writeText(text); event.target.textContent = 'Copied'; }
          catch (_) { pre.tabIndex = 0; pre.focus(); }
        }), pre);
      plans.append(details);
    }
    if (!data.plugin.plans?.length) plans.append(node('p', 'No Plugin plan JSON is retained in this session.'));
    const runtime = section(host, 'Pinned runtime');
    const images = new Set([data.plugin.mounted_image_id,
      ...data.packages.flatMap(pkg => asList(pkg.provenance_summary?.runtime))].filter(Boolean));
    for (const image of images) runtime.append(node('code', image));
    if (!images.size) runtime.append(node('p', 'No pinned OCI image in this view.'));
    for (const pkg of data.packages) {
      if (pkg.replay_script) section(host, 'replay_bundle/replay.py').append(node('pre', pkg.replay_script));
    }
    for (const run of data.runs) {
      if (run.nextflow_trace) section(host, 'nextflow-trace.tsv').append(node('pre', run.nextflow_trace));
    }
  }

  function evidence(host, data, options) {
    const packages = section(host, 'Verified Research Packages');
    for (const pkg of data.packages) {
      packages.append(pill('succeeded', humanize(pkg.outcome || 'Verified')), node('code', pkg.root_sha256));
      if (pkg.download_path) packages.append(button('Download .zip', () => options.download?.(pkg.download_path)));
      if (pkg.methods_md) packages.append(markdown(pkg.methods_md));
    }
    const local = asList(options.thesis?.packages).filter(row => row.status === 'stale');
    if (local.length) root.WorkbenchThesisStudio?.packageTable(packages, local, options.thesis.session_id, {download: options.download, message: options.message});
    if (!data.packages.length && !local.length) packages.append(node('p', 'No verified research package is available for this session.'));
    const receipts = section(host, 'Tool execution receipts');
    const emitted = new Set();
    const appendReceipt = (ref, step, run) => {
      const checksum = ref?.receipt_sha256 || ref?.event_sha256 || ref?.sha256;
      if (!digest(checksum) || emitted.has(checksum)) return;
      emitted.add(checksum);
      const control = button(`${humanize(step || run?.operation || 'Receipt')} · `,
        () => drawerController?.inspect({...ref, receipt_sha256: checksum, step_id: step, delivery_status: 'verified'}));
      const token = node('code', checksum.slice(0, 12));
      token.className = 'studio-mono';
      control.title = checksum;
      control.append(token);
      receipts.append(control);
    };
    for (const run of data.completed) {
      for (const [step, ref] of Object.entries(run.tool_receipts || {})) appendReceipt(ref, ref?.step || ref?.tool || step, run);
      if (digest(run.replay_bundle_sha256)) appendReceipt({receipt_sha256: run.replay_bundle_sha256}, 'Replay bundle', run);
    }
    for (const ref of asList(options.localReceipts)) appendReceipt(ref, ref.step_id);
    if (!emitted.size) receipts.append(node('p', 'No tool receipts are available in this view.'));
    const provenance = section(host, 'Provenance manifest');
    if (data.plugin.mounted_image_id) provenance.append(node('p', 'Pinned OCI image'), node('code', data.plugin.mounted_image_id));
    for (const pkg of data.packages) {
      const source = pkg.provenance_summary || {};
      provenance.append(node('h4', 'Original inputs'), node('pre', JSON.stringify(source.original_inputs || [], null, 2)),
        node('h4', 'Derived artifacts'), node('pre', JSON.stringify(source.derived_artifacts || [], null, 2)));
    }
    if (!data.packages.length && !data.plugin.mounted_image_id) provenance.append(node('p', 'The provenance manifest is available when a package verifies.'));
    const hold = section(host, 'Limitations & clinical hold');
    for (const note of new Set(data.runs.flatMap(run => asList(run.limitations)))) hold.append(node('p', note));
    hold.append(pill('unresolved', 'Clinical release disabled'), node('p', 'Independent scientific review and clinical validation remain separate from package verification.'));
  }

  function renderSession(host, tab, view, options) {
    const views = {overview, findings, figures, code: codeView, evidence};
    host.replaceChildren();
    host.hidden = !view || !views[tab];
    if (host.hidden) return;
    if (typeof view.retained_comparison?.title === 'string' && ['overview', 'findings', 'figures'].includes(tab)) {
      retainedComparison(host, tab, view, options);
      return;
    }
    const data = sessionData(view, options);
    if (tab === 'overview') overview(host, view, data, options);
    else if (tab === 'evidence') evidence(host, data, options);
    else views[tab](host, data);
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
    renderSession,
    renderSessionRail,
  };
})(window);
