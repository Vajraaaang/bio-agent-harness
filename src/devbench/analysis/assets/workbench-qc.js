(function (root) {
  'use strict';

  const stages = [
    ['Droplets', ['scrna_droplets']],
    ['Per-Capture QC', ['scrna_batch_qc']],
    ['Lane Doublets', ['scrna_lane_doublets']],
    ['Normalize & HVG', ['scrna_prepare']],
    ['Integration', ['scrna_integrate', 'scrna_integrate_qualified']],
    ['Pseudobulk / Markers', ['bulk_condition_contrast', 'scrna_nb_countsplit',
      'scrna_nb_fold_cluster', 'scrna_nb_fold_pseudobulk', 'scrna_nb_marker_fit']],
    ['Composition (scCODA)', ['scrna_sccoda']],
    ['Trajectories', ['fit_donor_trajectory']],
    ['Pathways', ['rna_pathway_camera', 'rna_pathway_tested', 'rna_pathway_marker_ora',
      'rna_pathway_nb_marker_ora']],
  ];
  const statusOrder = [
    'failed', 'refused', 'blocked', 'running', 'ready', 'succeeded', 'retired',
  ];
  const metricNames = {
    log1p_total_counts: 'Log1p counts',
    log1p_n_genes_by_counts: 'Log1p genes',
    pct_counts_mt: 'Mito %',
  };
  const labels = {
    raw_counts_h5ad: 'COUNT-1 raw',
    annotated_h5ad: 'Annotated H5AD',
    normalized_expression: 'Normalized expression',
    tximport_counts_h5: 'Estimated counts',
  };
  const label = value => String(value ?? '').replaceAll('_', ' ');
  const number = new Intl.NumberFormat('en-US', {maximumFractionDigits: 3});
  const show = value => Number.isFinite(value) ? number.format(value) : '—';
  const percent = value => show(value * 100) + '%';

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text != null) element.textContent = String(text);
    if (className) element.className = className;
    return element;
  }

  function control(text, activate) {
    const item = node('button', text, 'secondary');
    item.type = 'button';
    item.addEventListener('click', activate);
    return item;
  }

  function renderStages(parent, view) {
    const rail = node('ol', null, 'studio-qc-stage-rail');
    rail.setAttribute('role', 'list');
    rail.setAttribute('aria-label', 'Single-cell workflow stages');
    rail.tabIndex = 0;
    const steps = [...(view.steps || []), ...(view.stage_steps || [])];
    for (const [title, operations] of stages) {
      const item = node('li', null, 'studio-qc-stage');
      const current = steps.filter(step => operations.includes(step.operation));
      const reasons = new Set((view.operations || []).filter(step =>
        operations.includes(step.id) && step.available !== true && step.reason,
      ).map(step => step.reason));
      const statuses = new Map();
      for (const step of current) {
        const status = step.status || 'unknown';
        statuses.set(status, (statuses.get(status) || 0) + 1);
      }
      item.dataset.status = statusOrder.find(status => statuses.has(status)) ||
        (reasons.size ? 'blocked' : 'unknown');
      item.append(node('strong', title));
      if (!statuses.size) {
        const value = reasons.size ? 'Prerequisites pending' : 'Not recorded';
        const badge = node('small', value, 'research-status studio-status');
        badge.setAttribute('data-status', item.dataset.status);
        item.append(badge);
      }
      for (const [status, count] of statuses) {
        const badge = node('small', label(status) + (count > 1 ? ' · ' + count : ''),
          'research-status studio-status');
        badge.setAttribute('data-status', status);
        item.append(badge);
      }
      for (const reason of reasons) item.append(node('small', label(reason)));
      rail.append(item);
    }
    parent.append(rail);
  }

  function validRetention(input, kept) {
    return Number.isSafeInteger(input) && Number.isSafeInteger(kept) &&
      input > 0 && kept >= 0 && kept <= input;
  }

  function metric(parent, title, value) {
    const cell = node('div', null, 'studio-qc-kpi');
    cell.append(node('dt', title), node('dd', value));
    parent.append(cell);
  }

  function captureRows(report) {
    const groups = new Map();
    for (const item of report.thresholds || []) {
      if (typeof item.batch !== 'string') continue;
      if (!groups.has(item.batch)) groups.set(item.batch, {thresholds: []});
      groups.get(item.batch).thresholds.push(item);
    }
    if (report.qc_group_key && report.group_retention?.group_key === report.qc_group_key) {
      for (const item of report.group_retention.groups || []) {
        if (typeof item.group !== 'string') continue;
        if (!groups.has(item.group)) groups.set(item.group, {thresholds: []});
        groups.get(item.group).retention = item;
      }
    }
    return groups;
  }

  function appendRetention(cell, input, kept) {
    if (!validRetention(input, kept)) {
      cell.append(node('span', 'Retention unavailable'));
      return false;
    }
    const ratio = kept / input;
    const meter = node('meter', null, 'studio-qc-retention');
    meter.min = 0;
    meter.max = 1;
    meter.value = ratio;
    meter.setAttribute('aria-hidden', 'true');
    cell.append(node('span', show(input) + ' → ' + show(kept)), meter,
      node('small', percent(ratio)));
    return true;
  }

  function thresholdCell(thresholds) {
    const cell = node('td');
    for (const {metric, lower, upper} of thresholds) {
      const name = metricNames[metric] || label(metric);
      const bound = Number.isFinite(lower) ? show(lower) + '–' + show(upper)
        : '≤ ' + show(upper);
      cell.append(node('div', name + ' ' + bound));
    }
    if (!thresholds.length) cell.textContent = 'No adaptive cutoff recorded';
    return cell;
  }

  function renderThresholds(host, report) {
    const table = node('table', null, 'studio-qc-table');
    table.append(node('caption', 'Per-capture QC thresholds'));
    const headings = node('tr');
    for (const value of ['Capture', 'Cells in → kept', 'Min genes', 'MAD limit',
      'Max mito %', 'Exclusions', 'Observed cutoffs']) {
      const title = node('th', value);
      title.scope = 'col';
      headings.append(title);
    }
    const head = node('thead');
    head.append(headings);
    const body = node('tbody');
    for (const [capture, {thresholds, retention}] of captureRows(report)) {
      const row = node('tr');
      const name = node('th', capture);
      name.scope = 'row';
      const counts = node('td');
      const input = retention?.cells_input, kept = retention?.cells_retained;
      const hasRetention = appendRetention(counts, input, kept);
      const mitoLimits = [report.max_mito_percent,
        ...thresholds.filter(item => item.metric === 'pct_counts_mt')
          .map(item => item.upper)].filter(Number.isFinite);
      row.append(name, counts, node('td', show(report.min_genes)),
        node('td', show(report.mad_limit)),
        node('td', mitoLimits.length ? show(Math.min(...mitoLimits)) : '—'),
        node('td', hasRetention ? show(input - kept) : '—'), thresholdCell(thresholds));
      body.append(row);
    }
    if (!body.children.length) {
      const row = node('tr');
      const empty = node('td', 'No per-capture thresholds were retained.');
      empty.colSpan = 7;
      row.append(empty);
      body.append(row);
    }
    table.append(head, body);
    const wrapper = node('div', null, 'studio-qc-table-wrap');
    wrapper.setAttribute('role', 'region');
    wrapper.setAttribute('aria-label', 'QC threshold table');
    wrapper.tabIndex = 0;
    wrapper.append(table);
    host.append(wrapper);
    if (report.thresholds_total > (report.thresholds || []).length ||
      report.group_retention?.groups_total > (report.group_retention?.groups || []).length) {
      host.append(node('p', 'Showing the bounded QC report preview.', 'field-note'));
    }
  }

  function renderEvidence(host, detail) {
    const report = detail.report || {};
    const kpis = node('dl', null, 'studio-qc-kpis');
    metric(kpis, 'Cells input', show(report.cells_input));
    const kept = show(report.cells_retained);
    metric(kpis, 'Cells retained', validRetention(report.cells_input, report.cells_retained)
      ? kept + ' · ' + percent(report.cells_retained / report.cells_input) : kept);
    metric(kpis, 'Cells excluded', show(report.cells_excluded));
    metric(kpis, 'Method', report.method || '—');
    metric(kpis, 'QC group key', report.qc_group_key || '—');
    metric(kpis, 'Mito features', show(report.mitochondrial_features));
    const digest = detail.receipt_sha256;
    if (/^[a-f0-9]{64}$/.test(digest || '')) {
      const receipt = node('div', null, 'studio-qc-kpi');
      const value = node('dd', null, 'studio-qc-receipt-value');
      const hash = node('code', digest.slice(0, 12));
      hash.setAttribute('title', digest);
      value.append(hash);
      if (root.WorkbenchStudio?.inspectReceipt) {
        value.append(control('Inspect QC receipt', () => root.WorkbenchStudio.inspectReceipt({
          receipt_sha256: digest, step_id: detail.step_id,
          generation: detail.generation, representation: 'scrna_qc_report_json',
          artifact_sha256: detail.artifact_sha256, session_id: detail.session_id,
        })));
      }
      receipt.append(node('dt', 'Receipt SHA-256'), value);
      kpis.append(receipt);
    }
    host.append(kpis);
    renderThresholds(host, report);
    if (root.WorkbenchStudio?.addContext && detail.step_id) {
      host.append(control('Ask chat about this QC step', () => root.WorkbenchStudio.addContext({
        kind: 'step', label: 'QC · ' + detail.step_id, id: detail.step_id,
        generation: detail.generation, receipt_sha256: detail.receipt_sha256,
      })));
    }
  }

  function parameterValue(value) {
    return value == null ? 'Not set' : typeof value === 'object'
      ? JSON.stringify(value) : String(value);
  }

  function renderRevisionPreview(host, original, read, impact) {
    const diff = node('section', null, 'studio-qc-diff');
    const impactPanel = node('section', null, 'studio-qc-impact');
    impactPanel.append(node('h6', 'Downstream impact'));
    if (impact?.steps?.length) {
      const rows = node('ul');
      for (const step of impact.steps) {
        const effect = step.effect === 'superseded'
          ? 'Generation ' + step.generation + ' superseded' : 'Claim evidence will become stale';
        rows.append(node('li', step.step_id + ' · ' + effect));
      }
      impactPanel.append(rows);
      if (impact.total > impact.steps.length) {
        impactPanel.append(node('p', show(impact.total - impact.steps.length) +
          ' additional affected steps are outside this preview.', 'field-note'));
      }
    } else {
      impactPanel.append(node('p', impact
        ? 'No dependent workflow steps are affected.'
        : 'Downstream impact is unavailable for this readback.', 'field-note'));
    }
    impactPanel.append(node('p', 'A new workflow revision makes earlier thesis packages stale.',
      'field-note'));
    host.append(diff, impactPanel);
    const update = () => {
      diff.replaceChildren(node('h6', 'Parameter changes'));
      impactPanel.removeAttribute?.('data-pending-revision');
      try {
        const next = read();
        const changed = Object.keys(next).filter(key =>
          JSON.stringify(next[key]) !== JSON.stringify(original[key]));
        if (!changed.length) diff.append(node('p', 'Change a setting to review the difference.'));
        const rows = node('dl');
        for (const key of changed) {
          rows.append(node('dt', label(key)), node('dd',
            parameterValue(original[key]) + ' → ' + parameterValue(next[key])));
        }
        if (changed.length) diff.append(rows);
        if (changed.length && impact) impactPanel.setAttribute('data-pending-revision', 'true');
      } catch (error) {
        diff.append(node('p', error.message, 'research-hold'));
      }
    };
    update();
    return update;
  }

  function portDetails(host, port, control, choices) {
    const summary = node('p', null, 'field-note');
    host.append(summary);
    const refresh = () => {
      const selected = control.value === '' ? null : choices[Number(control.value)];
      const kind = selected?.kind || port.kind;
      const representation = selected?.representation || port.representation;
      summary.textContent = [label(kind), labels[representation] || label(representation)]
        .filter(Boolean).join(' · ');
    };
    control.addEventListener('change', refresh);
    refresh();
  }

  const descriptions = {
    scrna_droplets: 'Distinguish cells from ambient counts in a raw droplet assay.',
    scrna_batch_qc: 'Set per-capture thresholds against the original count layer.',
    scrna_lane_doublets: 'Estimate doublets separately within each declared capture lane.',
    scrna_prepare: 'Select variable features and prepare an integration representation.',
    scrna_integrate: 'Measure batch mixing and preservation of cell structure.',
    scrna_integrate_qualified: 'Check integration against reviewed labels and graph evidence.',
    scrna_nb_countsplit: 'Create independent count folds for marker evaluation.',
    scrna_nb_fold_cluster: 'Group cells using the discovery count fold.',
    scrna_nb_fold_pseudobulk: 'Aggregate held-out counts by donor and cluster.',
    scrna_nb_marker_fit: 'Estimate markers from the held-out donor profiles.',
    scrna_sccoda: 'Compare relative abundance using donor-level cell-type counts.',
  };
  const operationDescription = id => descriptions[id];
  root.WorkbenchQC = {
    renderStages, renderEvidence, renderRevisionPreview, portDetails, operationDescription,
  };
})(globalThis);
