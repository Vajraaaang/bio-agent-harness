/* Bounded composer context and question suggestions. No workflow writes. */
(function (root) {
  'use strict';

  const MAX_CHIPS = 8;
  const MAX_QUESTION = 8000;
  const PINNABLE = new Set(['aim', 'step', 'selection']);
  const EXACT_ID = /^[a-z0-9][a-z0-9_.:-]{0,199}$/i;
  const SINGLE_CELL = new Set(['scrna', 'scrna_seq', 'single_cell', 'single_cell_rna_seq',
    'snrna', 'snrna_seq', 'single_nucleus', 'single_nucleus_rna_seq']);
  const BULK = new Set(['bulk', 'bulk_rna_seq', 'bulk_rnaseq', 'bulk_rna']);
  const STRINGS = {
    droplets: [
      ['Droplets', 'Inspect droplet barcode ranks and check which inputs qualify for EmptyDrops.'],
      ['QC', 'Review the requirements for per-capture MAD QC and lane doublets.'],
    ],
    single_cell: [
      ['Integration', 'Which batch mixing and biological conservation checks are supported by these data?'],
      ['Pseudobulk', 'Can we compare donor-level pseudobulk states with the recorded samples and labels?'],
      ['Composition', 'Does the donor design support a relative composition analysis with scCODA?'],
    ],
    bulk: [
      ['Design', 'Audit design rank, donor pairing and latent batch structure.'],
      ['Pathways', 'Which condition contrasts and pathway tests (CAMERA or fgsea) do the data support?'],
    ],
    general: [
      ['Design', 'Which columns identify biological units and groups?'],
      ['QC', 'What quality checks are appropriate for this dataset?'],
    ],
  };

  function clean(value, width) {
    return typeof value === 'string' ? value.replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, width) : '';
  }

  const key = value => clean(value, 80).toLowerCase().replace(/[\s-]+/g, '_');
  const datasetId = value => typeof value === 'string' && value.length > 0 &&
    value.length <= 4096 && !value.includes('\0') ? value : '';

  function describe(row, profile = row.profile || {}) {
    const measures = profile.observed || {};
    const declaration = profile.declarations || {};
    const assay = key(row.assay || declaration.assay);
    const rep = key(row.representation || declaration.representation || measures.representation);
    const file = key(row.file_type || measures.file_type).replace(/^\./, '');
    if (rep === 'raw_feature_bc_matrix' || rep === 'raw_droplets') {
      return {route: 'droplets', detail: 'Raw droplet matrix'};
    }
    if (BULK.has(assay)) return {route: 'bulk', detail: 'Bulk RNA'};
    const basename = clean(row.filename, 150) || datasetId(row.source).split('/').pop();
    if (!assay && /^raw_feature_bc_matrix\.(?:h5|hdf5|mtx(?:\.gz)?)$/i.test(basename)) {
      // Filename identifies a candidate for inspection. It grants no provenance.
      return {route: 'droplets', detail: 'Possible droplet input'};
    }
    if (SINGLE_CELL.has(assay)) {
      return {route: 'single_cell', detail: assay.startsWith('snrna') || assay.startsWith('single_nucleus') ? 'Single nucleus' : 'Single cell'};
    }
    if (['h5ad', 'anndata'].includes(file)) return {route: 'single_cell', detail: 'AnnData'};
    return {route: 'general', detail: file.toUpperCase()};
  }

  function entryForDataset(row, profile = row?.profile) {
    if (!row) return null;
    const id = datasetId(row.source || row.path || row.id);
    if (!id) return null;
    const details = describe(row, profile || {});
    const representation = key(row.representation || profile?.declarations?.representation);
    const measurement = key(row.measurement || profile?.declarations?.measurement);
    const badges = [details.detail];
    if (['count_1', 'count_1_raw'].includes(representation)) badges.push('COUNT-1');
    if (['tximport_estimated_counts', 'estimated_counts'].includes(measurement) ||
        ['tximport_estimated_counts', 'tximport_counts_h5'].includes(representation)) badges.push('Estimated counts');
    return {
      kind: 'dataset', id,
      label: clean(row.filename, 150) || id.split('/').pop().slice(0, 150),
      detail: badges.filter(Boolean).join(' · '),
    };
  }

  function element(tag, className, content) {
    const node = root.document.createElement(tag);
    node.className = className;
    if (content !== undefined) node.textContent = content;
    return node;
  }

  function renderWayfinders(host, {datasets = [], onDraft, onChooseData, readOnly = false} = {}) {
    if (!host) return;
    host.replaceChildren();
    if (!datasets.length) {
      const button = element('button', 'quick-chip', 'Choose project data to see answerable questions');
      button.type = 'button';
      if (typeof onChooseData === 'function') {
        button.disabled = readOnly;
        button.addEventListener('click', onChooseData);
        host.append(button);
      } else host.append(element('p', 'muted', button.textContent));
      return;
    }
    const routes = new Set(datasets.slice(0, MAX_CHIPS).map(row => describe(row).route));
    const questions = routes.size === 1 ? STRINGS[routes.values().next().value] : STRINGS.general;
    for (const [label, question] of questions) {
      const button = element('button', 'quick-chip');
      button.type = 'button';
      button.disabled = readOnly || typeof onDraft !== 'function';
      button.append(element('span', 'chip-badge', label), element('span', '', ' ' + question));
      if (!button.disabled) button.addEventListener('click', () => onDraft(question));
      host.append(button);
    }
  }

  function normalize(entry) {
    if (!entry || !['dataset', 'session', ...PINNABLE].includes(entry.kind)) return null;
    const id = entry.kind === 'dataset' ? datasetId(entry.id) : clean(entry.id, 200);
    const label = clean(entry.label, 150);
    if (!id || !label) return null;
    return {kind: entry.kind, id, label, detail: clean(entry.detail, 96),
      generation: Number.isSafeInteger(entry.generation) && entry.generation > 0 ? entry.generation : null,
      version: Number.isSafeInteger(entry.version) && entry.version > 0 ? entry.version : null};
  }

  function mount(host, {onDatasetRemove, onSelectionRemove} = {}) {
    let scope = null, datasets = [], session = null, selection = null, readOnly = false;
    const pins = new Map();
    const labels = {dataset: '@dataset', session: 'Session', aim: '@aim', step: '@step', selection: '@selection'};
    function draw() {
      const items = [...datasets, session, selection, ...pins.values()].filter(Boolean);
      const group = items.map(item => {
        const chip = element('span', 'studio-context-chip');
        chip.setAttribute('data-kind', item.kind);
        chip.append(element('span', 'studio-context-kind', labels[item.kind]), element('span', 'studio-context-label', item.label));
        const detail = [item.detail, item.generation ? 'gen ' + item.generation : '', item.version ? 'v' + item.version : ''].filter(Boolean).join(' · ');
        if (detail) chip.append(element('span', 'studio-context-detail', detail));
        const reference = item.kind + ':' + item.id;
        const remove = pins.get(reference) === item ? () => {pins.delete(reference); draw();} :
          item.kind === 'dataset' && typeof onDatasetRemove === 'function' ? () => onDatasetRemove(item.id) :
          item === selection && typeof onSelectionRemove === 'function' ? onSelectionRemove : null;
        if (remove && !readOnly) {
          const button = element('button', 'secondary studio-context-remove', '×');
          button.type = 'button';
          button.setAttribute('aria-label', 'Remove ' + item.label);
          button.addEventListener('click', remove);
          chip.append(button);
        }
        return chip;
      });
      host.replaceChildren(...group);
      host.hidden = !items.length;
    }
    function sync(state) {
      const nextScope = clean(state.scope, 256) || null;
      const next = Array.isArray(state.datasets) ? state.datasets.slice(0, MAX_CHIPS).map(normalize).filter(row => row?.kind === 'dataset') : [];
      if (nextScope !== scope || next.length !== datasets.length || next.some((item, index) => item.id !== datasets[index].id)) pins.clear();
      scope = nextScope;
      datasets = next;
      session = normalize(state.session);
      selection = normalize(state.selection);
      readOnly = state.readOnly === true;
      draw();
    }
    function add(entry) {
      const item = normalize(entry);
      if (readOnly || !item || !PINNABLE.has(item.kind) || !EXACT_ID.test(item.id)) return false;
      const reference = item.kind + ':' + item.id;
      if (!pins.has(reference) && pins.size >= MAX_CHIPS) return false;
      pins.set(reference, item);
      draw();
      return true;
    }
    function formatDraft(question) {
      if (!pins.size || readOnly) return question;
      // IDs are navigation context. Attached datasets and exact saved-cell
      // selections continue to use their authoritative request fields.
      const context = Array.from(pins.values(), item =>
        '@' + item.kind + ' ' + item.id +
        (item.generation ? ' (generation ' + item.generation + ')' : '') +
        (item.version ? ' (version ' + item.version + ')' : ''));
      const framed = question + '\n\nPinned draft context:\n' + context.map(value => '• ' + value).join('\n');
      if (framed.length > MAX_QUESTION) throw new Error('Shorten the question or remove pinned context before sending.');
      return framed;
    }
    function clear() {
      pins.clear();
      draw();
    }
    draw();
    return Object.freeze({sync, add, formatDraft, clear});
  }

  root.WorkbenchContext = Object.freeze({mount, renderWayfinders, entryForDataset});
})(globalThis);
