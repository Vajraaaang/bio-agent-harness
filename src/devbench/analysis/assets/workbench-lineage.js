/* Bounded ledger entries drive this offline display. */
(function (root) {
  'use strict';
  const SVG = 'http://www.w3.org/2000/svg';
  const NODE_WIDTH = 216;
  const TITLES = {
    scrna_prepare: 'Normalize counts and select features',
    scrna_batch_qc: 'Batch quality control',
    scrna_lane_doublets: 'Capture-lane doublets',
    scrna_integration: 'Evaluate integration',
    bulk_condition_contrast: 'Bulk condition contrast',
    rna_pathway_tested: 'Tested-family pathway analysis',
    rna_pathway_camera: 'CAMERA pathway analysis',
  };
  const label = value => String(value || '').replaceAll('_', ' ');
  const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
  const status = value => label(value || 'recorded');
  const title = row => TITLES[row.operation_id] || label(row.operation_id) || row.step_id;
  const dependencies = row => Array.isArray(row.dependencies) ? row.dependencies : [];

  function layout(steps) {
    const rows = new Map(steps.map(row => [row.step_id, row]));
    const positions = new Map(), columns = new Map(), visiting = new Set();
    function depth(id) {
      if (!rows.has(id) || visiting.has(id)) return -1;
      if (columns.has(id)) return columns.get(id);
      visiting.add(id);
      const result = Math.max(-1, ...dependencies(rows.get(id)).map(depth)) + 1;
      visiting.delete(id);
      columns.set(id, result);
      return result;
    }
    const groups = new Map(), headings = [];
    for (const row of steps) {
      const branch = row.branch_id || 'Workflow';
      if (!groups.has(branch)) groups.set(branch, []);
      groups.get(branch).push(row);
    }
    let y = 0, width = NODE_WIDTH + 38;
    for (const [branch, members] of groups) {
      const lanes = new Map();
      headings.push({branch, y: y + 22});
      let occupied = 0;
      for (const row of members) {
        const col = depth(row.step_id);
        const lane = lanes.get(col) || 0;
        const x = 18 + col * (NODE_WIDTH + 34);
        positions.set(row.step_id, {x, y: y + 36 + lane * 86});
        lanes.set(col, lane + 1);
        occupied = Math.max(occupied, lane + 1);
        width = Math.max(width, x + NODE_WIDTH + 20);
      }
      y += 50 + occupied * 86;
    }
    return {positions, headings, width, height: y};
  }

  function relationships(steps, selected) {
    const parents = new Map(steps.map(row => [row.step_id, dependencies(row)]));
    const children = new Map();
    for (const row of steps) {
      for (const parent of dependencies(row)) {
        if (!children.has(parent)) children.set(parent, []);
        children.get(parent).push(row.step_id);
      }
    }
    function reachable(links) {
      const visited = new Set([selected]), pending = [...(links.get(selected) || [])];
      while (pending.length) {
        const id = pending.pop();
        if (visited.has(id)) continue;
        visited.add(id);
        for (const next of links.get(id) || []) pending.push(next);
      }
      return visited;
    }
    const upstream = reachable(parents), downstream = reachable(children);
    return id => !selected ? 'idle' : id === selected ? 'selected' :
      upstream.has(id) ? 'upstream' : downstream.has(id) ? 'downstream' : 'unrelated';
  }

  function mount({document, host, view, state, inspectQC, openPublication, verifyReplay}) {
    const entries = view.graph.entries || [];
    if (!entries.length) return 0;
    const steps = entries.filter(row => row.entry_kind === 'step');
    const byId = new Map(steps.map(row => [row.step_id, row]));
    if (!byId.has(state.selected)) state.selected = null;

    function element(tag, value, className) {
      const node = document.createElement(tag);
      if (value !== undefined) node.textContent = value;
      if (className) node.className = className;
      return node;
    }
    function button(value, action, className = 'secondary') {
      const control = element('button', value, className);
      control.type = 'button';
      control.onclick = action;
      return control;
    }
    function svg(tag, attributes) {
      const node = document.createElementNS(SVG, tag);
      for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
      return node;
    }
    function diagnostic(row) {
      return view.diagnostic_admission?.[row.step_id] ||
        (row.role === 'check' ? row.status : null);
    }

    const section = element('section', undefined, 'studio-lineage');
    section.setAttribute('data-studio-lineage', '');
    const controls = element('div', undefined, 'actions');
    const body = element('div');
    const inspector = element('section', undefined, 'lineage-inspector');
    inspector.setAttribute('data-lineage-inspector', '');
    const graphControl = button('Graph', () => show('graph'));
    const listControl = button('List', () => show('list'));
    if (steps.length && document.createElementNS) controls.append(graphControl, listControl);
    section.append(controls, body, inspector);

    const targets = new Map(), edges = [];
    let inspectorRevision = 0;
    function highlight(id) {
      const relation = relationships(steps, id);
      for (const [step, target] of targets) {
        target.setAttribute('data-lineage-relation', relation(step));
        target.setAttribute('aria-pressed', String(state.selected === step));
      }
      for (const {edge, from, to} of edges) {
        const onPath = relation(from) === relation(to) ||
          relation(from) === 'selected' || relation(to) === 'selected';
        edge.setAttribute('data-lineage-relation',
          id && onPath && relation(from) !== 'unrelated' && relation(to) !== 'unrelated'
            ? 'related' : id ? 'unrelated' : 'idle');
      }
    }
    function select(id) {
      state.selected = id;
      highlight(id);
      renderInspector();
    }
    function register(control, row) {
      targets.set(row.step_id, control);
      control.setAttribute('data-lineage-step', row.step_id);
      control.setAttribute('data-status', row.status || 'unknown');
      const selectRelations = () => highlight(row.step_id);
      const clearRelations = () => highlight(state.selected);
      for (const event of ['pointerenter', 'focus']) control.addEventListener(event, selectRelations);
      for (const event of ['pointerleave', 'blur']) control.addEventListener(event, clearRelations);
    }
    function rowText(row) {
      const admission = diagnostic(row);
      return [row.step_id, 'step', status(row.status),
        admission && admission !== row.status && 'check ' + status(admission),
        'Revision ' + row.generation, row.reason && label(row.reason),
        row.claim_hold && label(row.claim_hold)].filter(Boolean).join(' · ');
    }
    function inspectReceipt(row, receipt) {
      if (!digest(receipt)) return;
      const descriptor = {
        receipt_sha256: receipt, step_id: row.step_id, generation: row.generation,
        claim_scope: 'Execution receipt',
      };
      if (root.WorkbenchStudio?.inspectReceipt) {
        inspector.append(button('Inspect receipt · ' + receipt.slice(0, 8),
          () => root.WorkbenchStudio.inspectReceipt(descriptor)));
      } else {
        inspector.append(element('code', receipt));
      }
    }
    function renderInspector() {
      const ticket = ++inspectorRevision;
      const row = byId.get(state.selected);
      inspector.replaceChildren();
      inspector.hidden = !row;
      if (!row) return;
      inspector.append(element('h4', title(row)));
      inspector.append(element('p', rowText(row), 'field-note'));
      inspector.append(element('p', [row.branch_id && 'Branch ' + row.branch_id,
        row.purpose && 'Declared use ' + label(row.purpose)].filter(Boolean).join(' · ')));
      if (digest(row.parameters_sha256)) {
        inspector.append(element('p', 'Frozen parameters SHA-256'));
        inspector.append(element('code', row.parameters_sha256));
      }

      const bindings = element('ul');
      for (const input of row.input_ports || []) {
        const description = [input.port, label(input.kind), label(input.representation),
          input.source_id, input.source_port].filter(Boolean).join(' · ');
        bindings.append(element('li', description));
      }
      if (bindings.children.length) inspector.append(element('h5', 'Input bindings'), bindings);
      for (const dependency of dependencies(row)) {
        if (byId.has(dependency)) inspector.append(button('Inspect upstream ' + dependency,
          () => select(dependency)));
        else inspector.append(element('p', 'Upstream on another page · ' + dependency));
      }
      const attempt = entries.findLast(item => item.entry_kind === 'attempt' &&
        item.step_id === row.step_id && item.generation === row.generation);
      inspectReceipt(row, attempt?.receipt_sha256);
      if (inspectQC && row.operation_id === 'scrna_batch_qc' && row.status === 'succeeded') {
        const content = element('section', undefined, 'lineage-qc-evidence');
        inspector.append(button('Review QC', () => inspectQC(row, content,
          () => inspectorRevision === ticket)));
        inspector.append(content);
      }
      if (openPublication && view.publication?.step_id === row.step_id &&
          view.publication?.generation === row.generation) {
        inspector.append(button('Open admitted output for this step', openPublication));
      }
      if (verifyReplay && attempt) {
        inspector.append(button('Verify workflow replay', verifyReplay));
      }
      if (root.WorkbenchStudio?.addContext) {
        inspector.append(button('Send step to chat', () => root.WorkbenchStudio.addContext({
          kind: 'step', id: row.step_id, label: row.step_id + ' · gen ' + row.generation,
          detail: 'Workflow revision ' + view.revision + ' · ' + status(row.status),
        })));
      }
    }

    function addList() {
      const list = element('ul', undefined, 'lineage-list');
      list.setAttribute('data-lineage-list', '');
      for (const row of steps) {
        const item = element('li', undefined, 'lineage-list-row');
        const control = button(rowText(row), () => select(row.step_id));
        register(control, row);
        item.append(control);
        list.append(item);
      }
      if (list.children.length) body.append(list);
    }

    function addGraph() {
      const {positions, headings, width, height} = layout(steps);
      const viewport = element('div', undefined, 'lineage-graph-scroll');
      viewport.setAttribute('data-lineage-graph', '');
      const diagram = svg('svg', {class:'lineage-graph', width, height,
        role:'group', 'aria-label':'Workflow dependency graph'});
      for (const {branch, y} of headings) {
        const heading = svg('text', {x:18, y, class:'lineage-branch'});
        heading.textContent = branch;
        diagram.append(heading);
      }
      for (const row of steps) {
        const to = positions.get(row.step_id);
        for (const upstream of dependencies(row)) {
          const from = positions.get(upstream);
          if (!from) continue;
          const x = from.x + NODE_WIDTH, y = from.y + 33, bend = (x + to.x) / 2;
          const edge = svg('path', {class:'lineage-edge', fill:'none',
            d:`M ${x} ${y} C ${bend} ${y}, ${bend} ${to.y + 33}, ${to.x} ${to.y + 33}`});
          edges.push({edge, from: upstream, to: row.step_id});
          diagram.append(edge);
        }
      }
      for (const row of steps) {
        const {x,y} = positions.get(row.step_id);
        const cell = svg('foreignObject', {x, y, width:NODE_WIDTH, height:70});
        const control = button(undefined, () => select(row.step_id), 'secondary lineage-node');
        control.setAttribute('aria-label', rowText(row));
        const admission = diagnostic(row);
        const hold = [row.reason, row.claim_hold].find(reason => /^Q[-_]/i.test(reason || ''));
        const revision = element('span', row.step_id + ' · ', 'lineage-node-meta');
        revision.append(element('span', 'gen ' + row.generation, 'lineage-generation'));
        const qualifier = [admission && 'check ' + status(admission), label(hold)]
          .filter(Boolean).join(' · ');
        const signals = element('span', undefined, 'lineage-node-meta');
        const badge = element('span', status(row.status), 'studio-status');
        badge.setAttribute('data-status', row.status || 'unknown');
        signals.append(badge);
        if (qualifier) signals.append(element('span', ' · ' + qualifier));
        control.append(element('strong', title(row)), revision, signals);
        register(control, row);
        cell.append(control);
        diagram.append(cell);
      }
      viewport.append(diagram);
      body.append(viewport);
    }

    const trail = new Map();
    for (const row of entries) {
      if (row.entry_kind === 'step') continue;
      if (!trail.has(row.step_id)) trail.set(row.step_id, []);
      trail.get(row.step_id).push(row);
    }
    for (const [stepId, rows] of trail) {
      const details = element('details', undefined, 'lineage-history');
      const superseded = rows.some(row => row.entry_kind === 'tombstone');
      details.append(element('summary', stepId + ' · ' +
        (superseded ? 'Superseded result' : 'Retained history')));
      const history = element('ul');
      for (const row of rows) {
        const description = [row.entry_kind === 'tombstone' ? 'Superseded result' : label(row.entry_kind),
          Number.isInteger(row.generation) && 'Generation ' + row.generation,
          label(row.status), label(row.reason)].filter(Boolean).join(' · ');
        const item = element('li', description);
        if (digest(row.receipt_sha256) && root.WorkbenchStudio?.inspectReceipt) {
          item.append(button('Inspect historical receipt · ' + row.receipt_sha256.slice(0, 8),
            () => root.WorkbenchStudio.inspectReceipt({
              receipt_sha256: row.receipt_sha256, step_id: stepId,
              generation: row.generation, claim_scope: 'historical retained evidence',
            })));
        }
        history.append(item);
      }
      details.append(history);
      section.append(details);
    }

    function show(mode) {
      state.mode = mode === 'graph' && steps.length && document.createElementNS ? 'graph' : 'list';
      graphControl.setAttribute('aria-pressed', String(state.mode === 'graph'));
      listControl.setAttribute('aria-pressed', String(state.mode === 'list'));
      body.replaceChildren();
      targets.clear();
      edges.length = 0;
      if (state.mode === 'graph') addGraph();
      else addList();
      highlight(state.selected);
      renderInspector();
    }
    show(state.mode);
    host.append(section);
    return entries.length;
  }

  root.WorkbenchLineage = Object.freeze({mount, relationships});
})(typeof window !== 'undefined' ? window : globalThis);
