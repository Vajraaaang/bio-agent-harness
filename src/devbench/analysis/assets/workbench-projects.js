/* Project catalog controls. Authentication and full-page switching belong to the host. */
(function (root) {
  'use strict';
  const identity = value => typeof value === 'string' && /^[0-9a-f]{32}$/.test(value);
  const text = value => typeof value === 'string' ? value : '';
  const basename = value => text(value).split('/').pop() || 'Local data';
  function mount(options) {
    options = {...options};
    if (typeof options.request !== 'function' || typeof options.onSwitch !== 'function') throw new Error('Authenticated project requests and switching are required.');
    const doc = options.document || root.document;
    if (doc && typeof doc.getElementById === 'function') {
      const ids = {switcher:'project-switcher',nameElement:'project-name',dialog:'projects-dialog',title:'projects-dialog-title',list:'project-catalog-list',createPanel:'project-create-panel',createForm:'project-create-form',nameInput:'new-project-name',seedSelect:'new-project-source',createButton:'project-create-submit',createOpen:'project-create-open',createBack:'project-create-back',closeButton:'close-projects-dialog',detailsButton:'nav-overview',status:'project-catalog-status',importHost:'project-data-import'};
      for (const [key, id] of Object.entries(ids)) if (!options[key]) options[key] = doc.getElementById(id);
    }
    if (!doc || !options.switcher || !options.dialog || !options.list || !options.createForm || !options.nameInput || !options.seedSelect) throw new Error('Project controls are unavailable.');
    let projects = [], sources = [], current = identity(options.currentProjectId) ? options.currentProjectId : '', busy = false, disposed = false, refreshId = 0, createdPending = null;
    const listeners = [];
    const make = (tag, className, value) => { const element = doc.createElement(tag); if (className) element.className = className; if (value !== undefined) element.textContent = String(value); return element; };
    const listen = (node, event, handler, config) => { if (node && typeof node.addEventListener === 'function') { node.addEventListener(event, handler, config); listeners.push([node, event, handler, config]); } };
    const status = (message, error) => { if (disposed) return; if (options.status) { options.status.textContent = message || ''; options.status.className = 'message' + (error ? ' error' : ''); } if (typeof options.onStatus === 'function') options.onStatus(message || '', Boolean(error)); };
    const setBusy = value => { busy = value; for (const node of [options.createButton, options.createOpen, options.createBack, options.nameInput, options.seedSelect]) if (node) node.disabled = value; if (options.dialog) options.dialog.setAttribute('aria-busy', String(value)); };
    function close() { if (typeof options.dialog.close === 'function' && options.dialog.open) options.dialog.close(); else options.dialog.open = false; options.dialog.hidden = true; options.switcher.setAttribute('aria-expanded', 'false'); }
    function show(create) {
      options.dialog.hidden = false;
      if (typeof options.dialog.showModal === 'function') { if (!options.dialog.open) options.dialog.showModal(); } else options.dialog.open = true;
      options.switcher.setAttribute('aria-expanded', 'true');
      options.list.hidden = Boolean(create); if (options.createPanel) options.createPanel.hidden = !create; if (options.createOpen) options.createOpen.hidden = Boolean(create); if (options.detailsButton) options.detailsButton.hidden = Boolean(create);
      if (options.title) options.title.textContent = create ? 'New project' : 'Projects';
      if (create) { options.nameInput.focus(); loadSources().catch(error => status(error.message, true)); }
    }
    async function switchProject(id) {
      if (!identity(id) || !projects.some(project => project.id === id)) throw new Error('Select an available project.');
      if (busy || disposed) return null;
      if (id === current) { close(); return null; }
      setBusy(true); status('Opening project…');
      try { await options.onSwitch(id); if (!disposed) close(); return id; }
      catch (error) { status(error.message || 'Could not open the project.', true); throw error; }
      finally { if (!disposed) setBusy(false); }
    }
    function renderCatalog() {
      options.list.replaceChildren();
      for (const project of projects) {
        const button = make('button', 'secondary project-catalog-row'); button.type = 'button'; button.setAttribute('aria-current', String(project.id === current)); button.disabled = busy;
        button.append(make('span', 'project-catalog-name', project.name));
        const badges = [];
        if (project.id === current) badges.push('Current');
        if (project.read_only) badges.push('Read only');
        if (badges.length) button.append(make('span', 'project-catalog-meta', badges.join(' · ')));
        button.onclick = () => switchProject(project.id).catch(() => {}); options.list.append(button);
      }
      if (!projects.length) options.list.append(make('p', 'muted', 'No projects are available. Create a project to begin.'));
      const selected = projects.find(project => project.id === current);
      if (selected && options.nameElement) options.nameElement.textContent = selected.name;
      renderImport(selected);
    }
    async function refresh() {
      const stamp = ++refreshId, value = await options.request('/api/projects');
      if (disposed || stamp !== refreshId) return null;
      if (!value || !Array.isArray(value.projects) || !value.projects.every(project => identity(project.id) && text(project.name).trim())) throw new Error('The project catalog is unavailable.');
      projects = value.projects.map(project => ({id: project.id, name: project.name, read_only: project.read_only === true}));
      if (!current) current = value.active_project_id || value.default_project_id || '';
      if (!identity(current) || !projects.some(project => project.id === current)) throw new Error('The selected project is unavailable.');
      renderCatalog(); return {projects: projects.map(project => ({...project})), current};
    }
    function sourceOptions(select, placeholder) {
      if (!select) return; const prior = select.value; select.replaceChildren(); const empty = make('option', '', placeholder); empty.value = ''; select.append(empty);
      for (const source of sources) { const path = text(source.source || source.path); if (!path) continue; const item = make('option', '', text(source.filename) || basename(path)); item.value = path; item.title = path; select.append(item); }
      select.value = sources.some(source => (source.source || source.path) === prior) ? prior : '';
    }
    async function loadSources() {
      const value = await options.request('/api/project-sources'); if (disposed) return [];
      const rows = Array.isArray(value) ? value : value && (value.inputs || value.sources);
      if (!Array.isArray(rows)) throw new Error('Available local files could not be loaded.');
      sources = rows.filter(source => source && text(source.source || source.path));
      sourceOptions(options.seedSelect, 'No data yet'); sourceOptions(importSelect, 'Choose an available local file'); return sources;
    }
    const validSource = value => !value || sources.some(source => (source.source || source.path) === value);
    async function create(event) {
      if (event && typeof event.preventDefault === 'function') event.preventDefault();
      if (busy || disposed) return null;
      const name = text(options.nameInput.value).trim(), source = text(options.seedSelect.value);
      if (!name || name.length > 120 || name.includes('\0')) { status('Enter a project name of 1–120 characters.', true); options.nameInput.focus(); return null; }
      if (!createdPending && !validSource(source)) { status('Choose an available local file.', true); return null; }
      setBusy(true); status('Creating project…');
      try {
        const body = {name}; if (source) body.sources = [source];
        const result = createdPending || await options.request('/api/projects', body); if (disposed) return null;
        const project = result && (result.project || result);
        if (!project || !identity(project.id)) throw new Error('The created project identity is unavailable.');
        createdPending = project;
        await options.onSwitch(project.id); if (!disposed) { createdPending = null; options.nameInput.value = ''; options.seedSelect.value = ''; if (options.createButton) options.createButton.textContent = 'Create project'; close(); } return project;
      } catch (error) { status((createdPending ? 'Project created. Could not open it: ' : '') + (error.message || 'Could not create the project.'), true); if (createdPending && options.createButton) options.createButton.textContent = 'Open project'; return null; }
      finally { if (!disposed) setBusy(false); }
    }
    let importSelect = null, importButton = null, importStatus = null;
    function renderImport(project) {
      const host = options.importHost; if (!host) return;
      host.replaceChildren(); host.hidden = !project || project.read_only;
      importSelect = null; importButton = null; importStatus = null;
      if (host.hidden) return;
      const details = make('details', 'project-data-import'), summary = make('summary', '', 'Add data'), form = make('form', 'project-import-form'), label = make('label', '', 'Available local file');
      importSelect = make('select'); importSelect.setAttribute('aria-label', 'Available local file'); label.append(importSelect); sourceOptions(importSelect, 'Choose an available local file');
      importButton = make('button', 'secondary', 'Add to project'); importButton.type = 'submit';
      importStatus = make('p', 'message'); importStatus.setAttribute('role', 'status');
      form.append(label, importButton, importStatus); details.append(summary, form); host.append(details);
      details.ontoggle = () => { if (details.open) loadSources().catch(error => { if (importStatus) { importStatus.textContent = error.message; importStatus.className = 'message error'; } }); };
      form.onsubmit = importSource;
    }
    async function importSource(event) {
      if (event && typeof event.preventDefault === 'function') event.preventDefault();
      const selected = projects.find(project => project.id === current);
      if (busy || disposed || !selected || selected.read_only) return null;
      const source = text(importSelect && importSelect.value);
      if (!source || !validSource(source)) { if (importStatus) importStatus.textContent = 'Choose an available local file.'; return null; }
      setBusy(true); importButton.disabled = true; importSelect.disabled = true; importStatus.textContent = 'Adding data…';
      try {
        const result = await options.request('/api/project-inputs', {source}); if (disposed) return null;
        if (typeof options.onDataChanged === 'function') await options.onDataChanged(result);
        if (!disposed && importStatus) { importStatus.textContent = 'Data added to this project.'; importStatus.className = 'message'; }
        return result;
      } catch (error) { if (!disposed && importStatus) { importStatus.textContent = error.message || 'Could not add the file.'; importStatus.className = 'message error'; } return null; }
      finally { if (!disposed) { setBusy(false); if (importButton) importButton.disabled = false; if (importSelect) importSelect.disabled = false; } }
    }
    listen(options.switcher, 'click', () => { status(''); show(false); refresh().catch(error => status(error.message, true)); });
    listen(options.closeButton, 'click', close); listen(options.dialog, 'cancel', close);
    listen(options.dialog, 'click', event => { if (event.target === options.dialog && typeof options.dialog.getBoundingClientRect === 'function') { const r = options.dialog.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) close(); } });
    listen(options.createOpen, 'click', () => { status(''); show(true); }); listen(options.createBack, 'click', () => { status(''); show(false); refresh().catch(error => status(error.message, true)); }); listen(options.createForm, 'submit', create);
    const changedDraft = () => { createdPending = null; if (options.createButton) options.createButton.textContent = 'Create project'; };
    listen(options.nameInput, 'input', changedDraft); listen(options.seedSelect, 'change', changedDraft);
    listen(options.detailsButton, 'click', close, {capture: true});
    const api = {refresh, loadSources, create, switchProject, importSource, close, current: () => current, destroy() { disposed = true; refreshId++; for (const [node, event, handler, config] of listeners) if (typeof node.removeEventListener === 'function') node.removeEventListener(event, handler, config); listeners.length = 0; }};
    api.ready = refresh().catch(error => { status(error.message, true); return null; }); return api;
  }
  root.WorkbenchProjects = Object.freeze({mount});
})(typeof window !== 'undefined' ? window : globalThis);
