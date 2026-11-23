/* Saved scientific marks only: no fitting, rebinning, interpolation or downsampling. */
(function (root) {
  'use strict';
  const FORMAT = 'devbench.scientific-plot-workspace/1';
  const LIMITS = {panels: 256, rows: 1000000, marks: 1000000, vertices: 4000000,
    image_bytes: 8 * 1024 * 1024, image_pixels: 4000000, image_dimension: 16384, workspace_image_bytes: 64 * 1024 * 1024,
    workspace_image_pixels: 16000000, context_marks: 256};
  const PALETTES = root.ScientificPalettes || (typeof module !== 'undefined' && module.exports ? require(__dirname + '/scientific-palettes.js') : null);
  if (!PALETTES) throw new Error('ScientificPalettes must be loaded before scientific-plot-panels.js.');
  const SHAPES = ['circle', 'square', 'triangle', 'diamond'];
  const PATTERNS = ['solid', 'hollow', 'diagonal', 'cross', 'dots', 'horizontal', 'vertical', 'crosshatch'];
  const finite = v => typeof v === 'number' && Number.isFinite(v);
  const plain = v => v !== null && typeof v === 'object' && !Array.isArray(v) && (Object.getPrototypeOf(v) === Object.prototype || Object.getPrototypeOf(v) === null);
  const string = (v, empty = false) => typeof v === 'string' && (empty || v.length > 0) && v.length <= 10000 && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\ud800-\udfff\ufffe\uffff]/u.test(v);
  const fail = message => { throw new Error(message); };
  const esc = value => String(value).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;'}[c]));
  const num = value => { if (!finite(value)) fail('A screen coordinate is not finite.'); return String(Object.is(value, -0) ? 0 : value); };
  const labelNumber = value => Math.abs(value) >= 10000 || (value !== 0 && Math.abs(value) < .001) ? value.toExponential(2) : String(Number(value.toPrecision(4)));
  const strings = values => Array.isArray(values) && values.every(v => string(v, true));
  const nullable = value => value === null || finite(value);
  const hex = value => typeof value === 'string' && /^#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(value);

  function jsonValue(value, depth = 0, active = new Set(), imageTexts = new Set()) {
    if (depth > 40) fail('Saved JSON nesting is too deep.');
    if (value === null || typeof value === 'boolean' || finite(value)) return;
    if (typeof value === 'string') { if (!imageTexts.has(value) && !string(value, true)) fail('Saved text is invalid or too long.'); return; }
    if ((!plain(value) && !Array.isArray(value)) || active.has(value)) fail('Saved metadata must be finite JSON without cycles.');
    active.add(value);
    for (const [key, item] of Object.entries(value)) { if (!string(key, true)) fail('Invalid JSON property.'); jsonValue(item, depth + 1, active, imageTexts); }
    active.delete(value);
  }
  const exactFields = (value, fields) => plain(value) && Object.keys(value).length === fields.length && fields.every(k => Object.hasOwn(value, k));
  const imageText = value => string(value) && value.length <= 500;
  function imageCorners(payload) {
    const [a, b, c, d, e, f] = payload.pixel_to_data;
    const corners = [[0, 0], [payload.width, 0], [payload.width, payload.height], [0, payload.height]].map(([u, v]) => [a * u + c * v + e, b * u + d * v + f]);
    if (corners.some(p => !p.every(finite))) fail('Image registration corners must be finite.');
    return corners;
  }
  function imageBytes(payload) {
    const encoded = payload.png_base64;
    if (typeof encoded !== 'string' || !encoded.length || encoded.length % 4 || encoded.length > Math.ceil(LIMITS.image_bytes / 3) * 4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(encoded)) fail('A bounded canonical base64 PNG is required.');
    let binary;
    try { binary = root.atob(encoded); } catch (_) { fail('The saved PNG base64 is invalid.'); }
    if (root.btoa(binary) !== encoded || binary.length > LIMITS.image_bytes) fail('The saved PNG encoding is not canonical or exceeds its byte budget.');
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < bytes.length; i++) bytes[i] = binary.charCodeAt(i);
    if (bytes.length < 45 || [137, 80, 78, 71, 13, 10, 26, 10].some((v, i) => bytes[i] !== v)) fail('The image must be a PNG with its original intrinsic dimensions.');
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    let offset = 8, headers = 0, data = false, ended = false;
    while (offset + 12 <= bytes.length) {
      const length = view.getUint32(offset), type = String.fromCharCode(...bytes.subarray(offset + 4, offset + 8));
      if (!/^[A-Za-z]{4}$/.test(type) || length > bytes.length - offset - 12) fail('The PNG chunk structure is truncated or invalid.');
      if (headers === 0 && type !== 'IHDR') fail('The PNG must begin with its intrinsic header.');
      if (['acTL', 'fcTL', 'fdAT'].includes(type)) fail('Animated PNG is not a static registered tissue reference.');
      if (type === 'IHDR') {
        headers++;
        if (headers !== 1 || offset !== 8 || length !== 13 || view.getUint32(offset + 8) !== payload.width || view.getUint32(offset + 12) !== payload.height) fail('PNG dimensions disagree with the saved registration.');
        const depth = bytes[offset + 16], color = bytes[offset + 17], depths = {0: [1, 2, 4, 8, 16], 2: [8, 16], 3: [1, 2, 4, 8], 4: [8, 16], 6: [8, 16]};
        if (!depths[color]?.includes(depth) || bytes[offset + 18] !== 0 || bytes[offset + 19] !== 0 || ![0, 1].includes(bytes[offset + 20])) fail('The PNG header has an unsupported depth, color or compression declaration.');
      }
      if (type === 'IDAT' && length > 0) data = true;
      offset += length + 12;
      if (type === 'IEND') { if (length !== 0 || offset !== bytes.length) fail('The PNG has an invalid terminator or trailing bytes.'); ended = true; break; }
    }
    if (!ended || !data || headers !== 1) fail('A complete static PNG with image data is required.');
    return bytes;
  }
  function validateImage(payload) {
    if (!exactFields(payload, ['png_base64', 'sha256', 'width', 'height', 'pixel_to_data', 'coordinate_unit', 'coordinate_system_id', 'registration_id', 'source']) || typeof payload.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(payload.sha256)) fail('Registered tissue images require exact fields and a SHA-256 identity.');
    if (![payload.width, payload.height].every(v => Number.isInteger(v) && v > 0 && v <= LIMITS.image_dimension) || payload.width * payload.height > LIMITS.image_pixels) fail('The intrinsic image dimensions exceed the bounded pixel budget.');
    const matrix = payload.pixel_to_data;
    if (!Array.isArray(matrix) || matrix.length !== 6 || !matrix.every(finite) || !finite(matrix[0] * matrix[3] - matrix[1] * matrix[2]) || matrix[0] * matrix[3] - matrix[1] * matrix[2] === 0) fail('Pixel-to-data registration must be a finite nonsingular affine transform.');
    if (!['um', 'µm', 'micrometers', 'mm', 'millimeters', 'nm', 'cm', 'm'].includes(payload.coordinate_unit) || !imageText(payload.coordinate_system_id) || !imageText(payload.registration_id)) fail('Physical coordinate units, coordinate-system identity and registration identity are required.');
    const source = payload.source;
    if (!exactFields(source, ['id', 'kind', 'sample_id', 'section_id', 'biological_unit_id']) || !['synthetic', 'retained'].includes(source.kind) || !['id', 'sample_id', 'section_id', 'biological_unit_id'].every(k => imageText(source[k]))) fail('The tissue reference requires explicit source, sample, section and biological-unit identities.');
    imageCorners(payload);
    return imageBytes(payload);
  }
  const assetCaches = new WeakMap();
  function disposeCache(workspace, cache) {
    if (!cache) return;
    cache.disposed = true;
    for (const asset of cache.assets.values()) { asset.bitmap?.close?.(); if (asset.url) root.URL.revokeObjectURL(asset.url); asset.bitmap = null; asset.url = null; }
    if (assetCaches.get(workspace) === cache) assetCaches.delete(workspace);
  }
  function disposeAssets(workspace) { disposeCache(workspace, assetCaches.get(workspace)); }
  async function loadAssets(workspace) {
    prepareWorkspace(workspace);
    let cache = assetCaches.get(workspace);
    if (!cache) { cache = {assets: new Map(), disposed: false}; assetCaches.set(workspace, cache); }
    const images = new Map();
    for (const panel of workspace.panels) for (const mark of panel.marks) if (mark.type === 'image') images.set(mark.image.sha256, mark.image);
    try {
      await Promise.all([...images.values()].map(payload => {
        const existing = cache.assets.get(payload.sha256);
        if (existing) {
          if (existing.pngBase64 !== payload.png_base64) fail('An image changed without a new content hash.');
          return existing.promise;
        }
        const asset = {pngBase64: payload.png_base64, width: payload.width, height: payload.height, bitmap: null, url: null, verified: false, promise: null};
        cache.assets.set(payload.sha256, asset);
        asset.promise = (async () => {
          const bytes = validateImage(payload), subtle = root.crypto?.subtle || (typeof module !== 'undefined' && module.exports ? require('node:crypto').webcrypto.subtle : null);
          if (!subtle) fail('SHA-256 verification is unavailable in this browser context.');
          const digest = new Uint8Array(await subtle.digest('SHA-256', bytes)), hash = [...digest].map(v => v.toString(16).padStart(2, '0')).join('');
          if (hash !== payload.sha256) fail('Tissue image SHA-256 does not match the saved original bytes.');
          if (cache.disposed) fail('Tissue image loading was disposed.');
          const blob = new root.Blob([bytes], {type: 'image/png'}); let bitmap;
          if (typeof root.createImageBitmap === 'function') bitmap = await root.createImageBitmap(blob, {imageOrientation: 'none'});
          else if (typeof root.Image === 'function' && root.URL?.createObjectURL) {
            asset.url = root.URL.createObjectURL(blob);
            bitmap = await new Promise((resolve, reject) => { const image = new root.Image(); image.onload = () => resolve(image); image.onerror = () => reject(new Error('The saved PNG could not be decoded.')); image.src = asset.url; });
          } else fail('Native PNG decoding is unavailable; image assets cannot be drawn.');
          if (cache.disposed || (bitmap.naturalWidth ?? bitmap.width) !== payload.width || (bitmap.naturalHeight ?? bitmap.height) !== payload.height) { bitmap.close?.(); fail('Decoded image dimensions changed or image loading was disposed.'); }
          asset.bitmap = bitmap; asset.verified = true;
          if (asset.url) { root.URL.revokeObjectURL(asset.url); asset.url = null; }
        })();
        return asset.promise;
      }));
    } catch (error) { disposeCache(workspace, cache); throw error; }
    return {images: images.size, sha256: [...images.keys()]};
  }
  function imageAsset(model, payload) {
    const cache = assetCaches.get(model.workspace), asset = cache?.assets.get(payload.sha256);
    if (!asset?.verified || !asset.bitmap || cache.disposed || asset.pngBase64 !== payload.png_base64 || asset.width !== payload.width || asset.height !== payload.height) fail('Await loadAssets(workspace) before drawing or exporting registered images.');
    return asset;
  }
  function imageScreenTransform(payload, geometry) {
    const [a, b, c, d, e, f] = payload.pixel_to_data, bounds = geometry.bounds;
    const sx = geometry.pw / (bounds[1] - bounds[0]), sy = -geometry.ph / (bounds[3] - bounds[2]);
    const matrix = [a * sx, b * sy, c * sx, d * sy, geometry.sx(e), geometry.sy(f)];
    if (!matrix.every(finite)) fail('Registered image screen geometry is not finite.');
    return matrix;
  }
  function paletteName(value) {
    const name = String(value).toLowerCase().replace(/[_\s-]/g, '');
    if (['viridis', 'magma', 'coolwarm'].includes(name)) return name;
    if (['diverging', 'bluewhitered', 'bluered', 'rdbur'].includes(name)) return 'RdBu_r';
    if (name === 'okabeito') return 'OkabeIto';
    fail('The saved color palette is not supported.');
  }
  function validateScales(panel) {
    for (const key of ['color_scale', 'size_scale']) {
      const s = panel[key];
      if (s === null) continue;
      if (!plain(s) || !string(s.label) || !string(s.unit, true) || !Array.isArray(s.domain) || s.domain.length !== 2 || !s.domain.every(finite) || s.domain[0] > s.domain[1]) fail('A saved scale needs a label, unit and finite increasing domain.');
      if (key === 'size_scale') {
        if (s.domain[0] !== 0 || s.domain[1] <= 0 || !finite(s.max_radius) || s.max_radius <= 0 || s.max_radius > 100) fail('Area-size scales need a zero-based domain and a bounded positive radius.');
      } else {
        const palette = paletteName(s.palette);
        if (s.center !== null && s.center !== 0) fail('A diverging scale must be centered at zero.');
        const diverging = ['RdBu_r', 'coolwarm'].includes(palette);
        if ((diverging && s.center !== 0) || (s.center === 0 && (Math.abs(s.domain[0] + s.domain[1]) > Math.max(1e-12, Math.abs(s.domain[1]) * 1e-10) || !diverging))) fail('Diverging domains must be symmetric around zero.');
      }
    }
  }
  function validateMark(mark, keys, panel) {
    if (!plain(mark) || !Array.isArray(mark.keys) || !string(mark.group, true) || new Set(mark.keys).size !== mark.keys.length || mark.keys.some(k => !keys.has(k))) fail('Every mark needs exact, unique saved row keys and a group.');
    if (mark.type === 'image') {
      if (!exactFields(mark, ['type', 'keys', 'group', 'role', 'context', 'image']) || mark.keys.length || mark.group !== 'tissue_reference' || mark.role !== 'tissue_reference' || mark.context !== true) fail('Image marks must be explicit unlinked tissue-reference context.');
      validateImage(mark.image);
      if (panel.axes.x.unit !== mark.image.coordinate_unit || panel.axes.y.unit !== mark.image.coordinate_unit || panel.axes.x.transform !== 'identity' || panel.axes.y.transform !== 'identity') fail('Image registration must match both physical coordinate axes without a second transform.');
      return;
    }
    const fields = ['type', 'keys', 'group', 'style', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'width', 'height', 'rx', 'ry', 'radius', 'points', 'text', 'anchor', 'shape', 'value', 'size_value'];
    if (Object.keys(mark).some(k => !fields.includes(k))) fail('Only declared scientific geometry fields are supported.');
    const required = {point: ['x', 'y', 'radius'], line: ['x1', 'y1', 'x2', 'y2'], arrow: ['x1', 'y1', 'x2', 'y2'], rect: ['x', 'y', 'width', 'height'], ellipse: ['x', 'y', 'rx', 'ry'], text: ['x', 'y']};
    if (Object.hasOwn(required, mark.type)) {
      if (!required[mark.type].every(key => finite(mark[key]))) fail('Mark geometry must be finite.');
    } else if (['polyline', 'polygon', 'arc'].includes(mark.type)) {
      const n = mark.points?.length;
      if (!Array.isArray(mark.points) || !n || n > LIMITS.vertices || mark.points.some(p => !Array.isArray(p) || p.length !== 2 || !p.every(finite)) || (mark.type === 'polygon' && n < 3) || (mark.type === 'polyline' && n < 2) || (mark.type === 'arc' && n !== 3 && n !== 4)) fail('Saved polygon, line or Bezier vertices are invalid.');
    } else fail('Unsupported scientific mark type.');
    if (mark.type === 'point' && (mark.radius < 0 || mark.radius > 1000 || (mark.shape !== undefined && !SHAPES.includes(mark.shape)))) fail('Point radius or shape is invalid.');
    if (mark.type === 'rect' && (mark.width < 0 || mark.height < 0 || !finite(mark.x + mark.width) || !finite(mark.y + mark.height))) fail('Rectangle dimensions are invalid.');
    if (mark.type === 'ellipse' && (mark.rx < 0 || mark.ry < 0 || ![mark.x - mark.rx, mark.x + mark.rx, mark.y - mark.ry, mark.y + mark.ry].every(finite))) fail('Ellipse dimensions are invalid.');
    if (mark.type === 'text' && (!string(mark.text, true) || (mark.anchor !== undefined && !['start', 'middle', 'end'].includes(mark.anchor)))) fail('Text mark content or anchor is invalid.');
    if (mark.value !== undefined && !nullable(mark.value)) fail('Saved color values must be finite or null.');
    if (panel.color_scale && finite(mark.value) && (mark.value < panel.color_scale.domain[0] || mark.value > panel.color_scale.domain[1])) fail('Saved color values must fit the declared color domain.');
    if (mark.size_value !== undefined && (!finite(mark.size_value) || !panel.size_scale || mark.type !== 'point' || mark.size_value < panel.size_scale.domain[0] || mark.size_value > panel.size_scale.domain[1])) fail('Saved point size must be within the declared area-scale domain.');
    if (mark.style !== undefined) {
      const s = mark.style;
      if (!plain(s) || Object.keys(s).some(k => !['fill', 'stroke', 'stroke_width', 'opacity', 'dash'].includes(k))) fail('Only declared non-executable mark styles are supported.');
      if (['fill', 'stroke'].some(k => s[k] !== undefined && s[k] !== 'none' && !hex(s[k]))) fail('Mark colors must be literal hex colors or none.');
      if (s.stroke_width !== undefined && (!finite(s.stroke_width) || s.stroke_width < 0 || s.stroke_width > 100)) fail('Stroke width is invalid.');
      if (s.opacity !== undefined && (!finite(s.opacity) || s.opacity < 0 || s.opacity > 1)) fail('Opacity must be between zero and one.');
      if (s.dash !== undefined && (!Array.isArray(s.dash) || s.dash.length > 20 || s.dash.some(v => !finite(v) || v < 0 || v > 1000))) fail('Stroke dash lengths are invalid.');
    }
  }
  function prepareWorkspace(w) {
    if (!plain(w) || w.format !== FORMAT || !plain(w.source) || !/^[a-f0-9]{64}$/.test(w.source.sha256) || !string(w.source.run_id) || !['retained', 'synthetic'].includes(w.source.kind) || !string(w.title) || !Array.isArray(w.panels) || !w.panels.length || w.panels.length > LIMITS.panels || !Array.isArray(w.entities) || !strings(w.limitations)) fail('A bounded saved scientific workspace with explicit source identity is required.');
    const imageTexts = new Set(), imageHashes = new Map(); let contextMarks = 0;
    for (const p of w.panels) if (Array.isArray(p?.marks)) for (const m of p.marks) if (m?.type === 'image') {
      if (++contextMarks > LIMITS.context_marks) fail('Registered image context marks exceed the workspace context budget.');
      const bytes = validateImage(m.image);
      imageTexts.add(m.image.png_base64);
      const previous = imageHashes.get(m.image.sha256);
      if (previous && previous.text !== m.image.png_base64) fail('A saved image hash refers to different PNG bytes.');
      imageHashes.set(m.image.sha256, {text: m.image.png_base64, bytes: bytes.length, pixels: m.image.width * m.image.height});
    }
    if ([...imageHashes.values()].reduce((n, image) => n + image.bytes, 0) > LIMITS.workspace_image_bytes) fail('Registered image assets exceed the workspace byte budget.');
    if ([...imageHashes.values()].reduce((n, image) => n + image.pixels, 0) > LIMITS.workspace_image_pixels) fail('Unique registered images exceed the workspace decoded-pixel budget.');
    jsonValue(w, 0, new Set(), imageTexts);
    const panelIds = new Set(), entities = new Set(); let totalRows = 0, totalMarks = 0, totalVertices = 0;
    for (const entity of w.entities) {
      const key = JSON.stringify([entity.kind, entity.id]);
      if (!plain(entity) || !string(entity.kind) || !string(entity.id) || !string(entity.label, true) || !plain(entity.details) || !strings(entity.warnings) || entities.has(key)) fail('Linked entities must have unique typed identities and saved details.');
      entities.add(key);
    }
    for (const p of w.panels) {
      if (!plain(p) || !string(p.id) || panelIds.has(p.id) || !string(p.capability) || !string(p.title) || !string(p.entity_kind) || !Array.isArray(p.rows) || !Array.isArray(p.marks) || !plain(p.axes) || !strings(p.annotations) || !strings(p.limitations)) fail('Panel identity, saved rows, marks and scientific semantics are required.');
      panelIds.add(p.id); totalRows += p.rows.length; totalMarks += p.marks.length;
      if (totalRows > LIMITS.rows || totalMarks > LIMITS.marks) fail('Saved marks exceed the bounded display budget; no implicit sampling is permitted.');
      for (const axis of ['x', 'y']) {
        const a = p.axes[axis], categories = p[axis + '_categories'];
        if (!plain(a) || !string(a.label) || !string(a.unit, true) || !string(a.transform) || !strings(categories) || new Set(categories).size !== categories.length) fail('Declared axes, transforms and exact category labels are required.');
      }
      validateScales(p);
      const keys = new Set(); let coordinateRows = 0, excludedRows = 0;
      for (const r of p.rows) {
        if (!plain(r) || !string(r.id) || !string(r.key) || keys.has(r.key) || !string(r.entity_kind) || !nullable(r.x) || !nullable(r.y) || !string(r.group, true) || !['recorded', 'excluded'].includes(r.status) || !strings(r.warnings) || !plain(r.details) || (r.reason !== null && !string(r.reason))) fail('Saved row identity, geometry, status or evidence is invalid.');
        if ((r.x === null || r.y === null) && !string(r.reason)) fail('Absent row geometry must retain its recorded reason.');
        if (r.status === 'recorded' && (r.x === null || r.y === null || r.reason !== null)) fail('Recorded rows cannot claim absent or excluded geometry.');
        if (r.status === 'excluded' && !string(r.reason)) fail('Excluded rows must retain a recorded reason.');
        if (r.link !== null && (!plain(r.link) || !string(r.link.kind) || !string(r.link.id))) fail('Each biological link must be explicitly typed or null.');
        keys.add(r.key); if (finite(r.x) && finite(r.y)) coordinateRows++; if (r.status === 'excluded') excludedRows++;
      }
      for (const mark of p.marks) { validateMark(mark, keys, p); totalVertices += mark.points?.length || 1; }
      if (totalVertices > LIMITS.vertices) fail('Saved vertices exceed the display budget; no implicit simplification is permitted.');
      if (!Array.isArray(p.reference_lines) || p.reference_lines.some(l => !plain(l) || !['x', 'y'].includes(l.axis) || !finite(l.value) || !string(l.label, true))) fail('Reference lines require a declared axis, finite value and label.');
      const a = p.availability, c = p.coverage;
      if (!plain(a) || !['available', 'unavailable'].includes(a.status) || (a.status === 'unavailable' ? !string(a.reason) : a.reason !== null)) fail('Panel availability requires an explicit saved status and reason.');
      if (!plain(c) || c.source_rows !== p.rows.length || c.coordinate_rows !== coordinateRows || c.missing_rows !== excludedRows || c.mark_count !== p.marks.length || c.truncated !== false) fail('Panel coverage must exactly account for every saved row and mark.');
    }
    return w;
  }

  function fullBounds(panel) {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    const addX = x => { if (!finite(x)) fail('Saved X extent cannot be represented.'); x0 = Math.min(x0, x); x1 = Math.max(x1, x); };
    const addY = y => { if (!finite(y)) fail('Saved Y extent cannot be represented.'); y0 = Math.min(y0, y); y1 = Math.max(y1, y); };
    const add = p => { addX(p[0]); addY(p[1]); };
    for (const r of panel.rows) if (finite(r.x) && finite(r.y)) add([r.x, r.y]);
    for (const m of panel.marks) {
      if (m.type === 'image') for (const corner of imageCorners(m.image)) add(corner);
      else if (['point', 'text'].includes(m.type)) add([m.x, m.y]);
      else if (['line', 'arrow'].includes(m.type)) { add([m.x1, m.y1]); add([m.x2, m.y2]); }
      else if (m.type === 'rect') { add([m.x, m.y]); add([m.x + m.width, m.y + m.height]); }
      else if (m.type === 'ellipse') { add([m.x - m.rx, m.y - m.ry]); add([m.x + m.rx, m.y + m.ry]); }
      else for (const p of m.points) add(p);
    }
    for (const l of panel.reference_lines) (l.axis === 'x' ? addX : addY)(l.value);
    if (panel.x_categories.length) { addX(0); addX(panel.x_categories.length - 1); }
    if (panel.y_categories.length) { addY(0); addY(panel.y_categories.length - 1); }
    if (x0 === Infinity) { x0 = 0; x1 = 1; } if (y0 === Infinity) { y0 = 0; y1 = 1; }
    const dx = x1 - x0 || Math.max(1, Math.abs(x0) * .1), dy = y1 - y0 || Math.max(1, Math.abs(y0) * .1);
    const result = [x0 - dx * .06, x1 + dx * .06, y0 - dy * .08, y1 + dy * .08];
    if (!result.every(finite) || !finite(result[1] - result[0]) || !finite(result[3] - result[2]) || result[0] >= result[1] || result[2] >= result[3]) fail('Saved coordinate range cannot be represented accurately.');
    return result;
  }
  function project(workspace, panelId) {
    const w = prepareWorkspace(workspace), p = w.panels.find(p => p.id === panelId);
    if (!p) fail('Unknown saved scientific panel.');
    const axisLabel = a => a.label + (a.unit ? ' · ' + a.unit : '') + (a.transform === 'identity' ? '' : ' · saved ' + a.transform);
    return {kind: p.id, capability: p.capability, title: p.title, entityKind: p.entity_kind, source: w.source, workspace: w,
      rows: p.rows.map(r => ({...r, entityKind: r.entity_kind})), axes: {x: axisLabel(p.axes.x), y: axisLabel(p.axes.y)},
      bounds: fullBounds(p), connections: [], referenceLines: p.reference_lines.slice(), limitations: [...w.limitations, ...p.limitations],
      unavailable: p.availability.status === 'available' ? null : p.availability.reason, scientificPanel: p, scientific: true,
      xLabels: p.x_categories.slice(), yLabels: p.y_categories.slice(), colorScale: p.color_scale, sizeScale: p.size_scale,
      equalAspect: p.marks.some(m => m.type === 'image') || ['spatial_expression', 'rna_velocity', 'saved_embedding', 'embedding_hexbin', 'embedding_density_contours', 'embedding_density_centroids'].includes(p.capability) ||
        (p.axes.x.unit === 'embedding_coordinate' && p.axes.y.unit === 'embedding_coordinate') ||
        (p.capability === 'multimodal_features' && p.axes.x.unit === p.axes.y.unit && p.axes.x.transform === p.axes.y.transform)};
  }

  const categoryCache = new WeakMap();
  function categories(model) {
    if (!categoryCache.has(model)) {
      const names = [...new Set([...model.rows.map(r => r.group), ...model.scientificPanel.marks.filter(m => m.type !== 'image').map(m => m.group)].filter(Boolean))].sort();
      categoryCache.set(model, new Map(names.map((name, i) => {
        const block = Math.floor(i / 8), shape = SHAPES[(block + Math.floor(block / 8)) % SHAPES.length], pattern = PATTERNS[block % PATTERNS.length];
        return [name, {label: name, group: name, color: PALETTES.categorical(i).color, shape, pattern, dash: block ? [3 + (block % 4) * 2, 2 + Math.floor(block / 4) % 4] : [], index: i}];
      })));
    }
    return categoryCache.get(model);
  }
  function groupStyle(model, group) { return categories(model).get(group) || {label: group, group, color: '#596b76', shape: 'circle', pattern: 'solid', dash: [], index: -1}; }
  function scaleColor(scale, value) {
    const palette = paletteName(scale.palette), [lo, hi] = scale.domain;
    const t = lo === hi ? .5 : Math.max(0, Math.min(1, (value - lo) / (hi - lo)));
    return PALETTES.sample(palette, t);
  }
  function color(model, mark) {
    return mark.style?.fill && mark.style.fill !== 'none' ? mark.style.fill : model.colorScale && paletteName(model.colorScale.palette) !== 'OkabeIto' && finite(mark.value) ? scaleColor(model.colorScale, mark.value) : groupStyle(model, mark.group).color;
  }
  function radius(model, mark) {
    if (mark.size_value === undefined) return mark.radius;
    return model.sizeScale.domain[1] === 0 ? 0 : model.sizeScale.max_radius * Math.sqrt(mark.size_value / model.sizeScale.domain[1]);
  }
  function selectedKeys(model, state) {
    if (state.selectedLink) return new Set(model.rows.filter(row => row.link !== null && row.link.kind === state.selectedLink.kind && row.link.id === state.selectedLink.id).map(row => row.key));
    if (state.selectedKeys) return new Set(state.selectedKeys);
    if (state.selectedKey != null) return new Set([state.selectedKey]);
    if (state.selectedId != null) { const matches = model.rows.filter(r => r.id === state.selectedId); return new Set(matches.length === 1 ? [matches[0].key] : []); }
    return new Set();
  }
  function filteredMarks(model, state = {}, scope = 'current') {
    if (!['complete', 'current'].includes(scope)) fail('Choose a complete or current-view scope.');
    const allowed = new Set(), keys = scope === 'complete' || state.keys == null ? null : new Set(state.keys), selected = state.status === 'selected';
    const selection = scope === 'current' && selected ? selectedKeys(model, state) : null;
    // Viewport-cropped geometry.rows is deliberately never used for membership.
    // Saved aggregate/path geometry is omitted whole when a member is filtered.
    for (const row of model.rows) {
      let keep = true;
      if (scope === 'current') {
        if (keys && !keys.has(row.key)) keep = false;
        if (state.group && state.group !== 'all' && row.group !== state.group) keep = false;
        if (state.status === 'warnings' && !row.warnings.length) keep = false;
        else if (selected && !selection.has(row.key)) keep = false;
        else if (state.status && !['all', 'warnings', 'selected'].includes(state.status) && row.status !== state.status) keep = false;
      }
      if (keep) allowed.add(row.key);
    }
    const marks = [], omittedMarks = [];
    model.scientificPanel.marks.forEach((mark, index) => {
      if (mark.type === 'image') { marks.push({mark, index}); return; }
      let reason = null;
      if (mark.keys.some(k => !allowed.has(k))) reason = 'filtered_member';
      else if (scope === 'current' && !mark.keys.length && state.group && state.group !== 'all' && mark.group && mark.group !== state.group) reason = 'filtered_group';
      if (reason) omittedMarks.push({index, type: mark.type, keys: mark.keys.slice(), reason}); else marks.push({mark, index});
    });
    return {marks, omittedMarks, allowedKeys: [...allowed]};
  }
  function markStyle(model, mark) {
    const closed = ['point', 'polygon', 'rect', 'ellipse', 'text'].includes(mark.type), base = color(model, mark), s = mark.style || {}, category = groupStyle(model, mark.group);
    return {fill: s.fill || (closed ? base : 'none'), stroke: s.stroke || (mark.type === 'text' ? 'none' : base), width: s.stroke_width ?? 1,
      opacity: s.opacity ?? 1, dash: s.dash || (closed ? [] : category.dash), shape: mark.shape || category.shape,
      pattern: closed && mark.type !== 'text' && s.fill === undefined && !(model.colorScale && finite(mark.value)) ? category.pattern : 'solid'};
  }
  function pixelPadding(model) {
    const padding = {left: 0, right: 0, bottom: 0, top: 0};
    for (const mark of model.scientificPanel.marks) {
      if (mark.type === 'image') continue;
      const style = markStyle(model, mark), closed = ['point', 'polygon', 'rect', 'ellipse', 'text'].includes(mark.type);
      const stroked = style.stroke !== 'none' && (style.width > 0 || mark.type === 'arrow'), stroke = stroked ? style.width / 2 : 0;
      const filled = closed && style.fill !== 'none' && style.pattern !== 'hollow';
      if (style.opacity === 0 || (!filled && !stroked)) continue;
      let left = stroke, right = stroke, bottom = stroke, top = stroke;
      if (mark.type === 'point') {
        const r = radius(model, mark);
        let x = r, low = r, high = r;
        if (style.shape === 'square') x = low = high = r * Math.sqrt(Math.PI) / 2;
        else if (style.shape === 'diamond') x = low = high = r * Math.sqrt(Math.PI / 2);
        else if (style.shape === 'triangle') { x = r * Math.sqrt(Math.PI / Math.sqrt(3)); high = r * Math.sqrt(4 * Math.PI / (3 * Math.sqrt(3))); low = high / 2; }
        left += x; right += x; bottom += low; top += high;
      } else if (mark.type === 'arrow') left = right = bottom = top = Math.max(stroke, 7 + style.width);
      else if (mark.type === 'text') {
        const width = 11 * Array.from(mark.text).length;
        if (mark.anchor === 'middle') { left += width / 2; right += width / 2; }
        else if (mark.anchor === 'end') left += width;
        else right += width;
        bottom += 3; top += 11;
      }
      padding.left = Math.max(padding.left, left + 1); padding.right = Math.max(padding.right, right + 1);
      padding.bottom = Math.max(padding.bottom, bottom + 1); padding.top = Math.max(padding.top, top + 1);
    }
    return padding;
  }
  function paddedBounds(model, plotWidthCss, plotHeightCss) {
    if (![plotWidthCss, plotHeightCss].every(v => finite(v) && v > 0)) fail('A finite positive plot box is required for full-view glyph padding.');
    const p = pixelPadding(model), b = model.bounds;
    if (plotWidthCss <= p.left + p.right || plotHeightCss <= p.bottom + p.top) fail('The full plot box must grow to fit the saved glyphs; their radii are not reduced.');
    let sx = (plotWidthCss - p.left - p.right) / (b[1] - b[0]), sy = (plotHeightCss - p.bottom - p.top) / (b[3] - b[2]);
    if (model.equalAspect) sx = sy = Math.min(sx, sy);
    const result = [b[0] - p.left / sx, b[1] + p.right / sx, b[2] - p.bottom / sy, b[3] + p.top / sy];
    if (!result.every(finite) || !finite(result[1] - result[0]) || !finite(result[3] - result[2])) fail('The padded full-view range cannot be represented.');
    return result;
  }
  function screenMark(model, mark, g) {
    const result = {...mark};
    for (const key of ['x', 'x1', 'x2']) if (finite(mark[key])) result[key] = g.sx(mark[key]);
    for (const key of ['y', 'y1', 'y2']) if (finite(mark[key])) result[key] = g.sy(mark[key]);
    if (mark.points) result.points = mark.points.map(p => [g.sx(p[0]), g.sy(p[1])]);
    if (mark.type === 'point') result.radius = radius(model, mark);
    if (mark.type === 'rect') { const x2 = g.sx(mark.x + mark.width), y2 = g.sy(mark.y + mark.height); result.x = Math.min(result.x, x2); result.y = Math.min(result.y, y2); result.width = Math.abs(x2 - g.sx(mark.x)); result.height = Math.abs(y2 - g.sy(mark.y)); }
    if (mark.type === 'ellipse') { result.rx = Math.abs(g.sx(mark.x + mark.rx) - result.x); result.ry = Math.abs(g.sy(mark.y + mark.ry) - result.y); }
    for (const key of ['x', 'x1', 'x2', 'y', 'y1', 'y2', 'radius', 'width', 'height', 'rx', 'ry']) if (result[key] !== undefined && !finite(result[key])) fail('Screen geometry is outside the finite drawing range.');
    if (result.points?.some(p => !p.every(finite))) fail('Screen vertices are outside the finite drawing range.');
    return result;
  }
  // Every point glyph has area pi*r^2, even when categories use different shapes.
  function pointVertices(p, shape) {
    const x = p.x, y = p.y, r = p.radius;
    if (shape === 'square') { const a = r * Math.sqrt(Math.PI) / 2; return [[x - a, y - a], [x + a, y - a], [x + a, y + a], [x - a, y + a]]; }
    if (shape === 'diamond') { const a = r * Math.sqrt(Math.PI / 2); return [[x, y - a], [x + a, y], [x, y + a], [x - a, y]]; }
    const a = r * Math.sqrt(4 * Math.PI / (3 * Math.sqrt(3)));
    return [[x, y - a], [x + Math.sqrt(3) * a / 2, y + a / 2], [x - Math.sqrt(3) * a / 2, y + a / 2]];
  }
  function arrowHead(p, width) {
    const dx = p.x2 - p.x1, dy = p.y2 - p.y1, length = Math.hypot(dx, dy);
    if (!length) return [];
    const ux = dx / length, uy = dy / length, size = Math.min(length, 7 + width), half = size * .45;
    return [[p.x2, p.y2], [p.x2 - ux * size - uy * half, p.y2 - uy * size + ux * half], [p.x2 - ux * size + uy * half, p.y2 - uy * size - ux * half]];
  }
  function polygonPath(ctx, points, close) { ctx.moveTo(...points[0]); for (const p of points.slice(1)) ctx.lineTo(...p); if (close) ctx.closePath(); }
  function canvasPath(ctx, p, style) {
    ctx.beginPath();
    if (p.type === 'point') {
      if (style.shape === 'circle') ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
      else if (style.shape === 'square') { const a = p.radius * Math.sqrt(Math.PI) / 2; ctx.rect(p.x - a, p.y - a, 2 * a, 2 * a); }
      else polygonPath(ctx, pointVertices(p, style.shape), true);
    } else if (p.type === 'rect') ctx.rect(p.x, p.y, p.width, p.height);
    else if (p.type === 'ellipse') ctx.ellipse(p.x, p.y, p.rx, p.ry, 0, 0, Math.PI * 2);
    else if (p.type === 'line' || p.type === 'arrow') { ctx.moveTo(p.x1, p.y1); ctx.lineTo(p.x2, p.y2); }
    else if (p.type === 'arc') { ctx.moveTo(...p.points[0]); const values = p.points.slice(1).flat(); if (p.points.length === 3) ctx.quadraticCurveTo(...values); else ctx.bezierCurveTo(...values); }
    else polygonPath(ctx, p.points, p.type === 'polygon');
  }
  function pixelBounds(p) {
    if (p.type === 'point') { const r = p.radius * 1.6; return [p.x - r, p.y - r, p.x + r, p.y + r]; }
    if (p.type === 'rect') return [p.x, p.y, p.x + p.width, p.y + p.height];
    if (p.type === 'ellipse') return [p.x - p.rx, p.y - p.ry, p.x + p.rx, p.y + p.ry];
    const xs = p.points.map(v => v[0]), ys = p.points.map(v => v[1]);
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (let i = 0; i < xs.length; i++) { x0 = Math.min(x0, xs[i]); x1 = Math.max(x1, xs[i]); y0 = Math.min(y0, ys[i]); y1 = Math.max(y1, ys[i]); }
    return [x0, y0, x1, y1];
  }
  function canvasPattern(ctx, p, style, g) {
    if (style.pattern === 'solid' || style.pattern === 'hollow') return;
    const raw = pixelBounds(p), b = [Math.max(raw[0], g.m.left), Math.max(raw[1], g.m.top), Math.min(raw[2], g.width - g.m.right), Math.min(raw[3], g.height - g.m.bottom)];
    if (b[0] >= b[2] || b[1] >= b[3]) return;
    ctx.save(); canvasPath(ctx, p, style); ctx.clip(); ctx.strokeStyle = '#ffffff'; ctx.fillStyle = '#ffffff'; ctx.lineWidth = 1; ctx.setLineDash([]); ctx.beginPath();
    const startX = Math.floor(b[0] / 8) * 8, startY = Math.floor(b[1] / 8) * 8;
    if (style.pattern === 'dots') {
      for (let x = startX; x <= b[2] + 8; x += 8) for (let y = startY; y <= b[3] + 8; y += 8) { ctx.moveTo(x + 1, y); ctx.arc(x, y, 1, 0, Math.PI * 2); }
      ctx.fill();
    } else {
      if (['horizontal', 'cross', 'crosshatch'].includes(style.pattern)) for (let y = startY; y <= b[3]; y += 8) { ctx.moveTo(b[0], y); ctx.lineTo(b[2], y); }
      if (['vertical', 'cross', 'crosshatch'].includes(style.pattern)) for (let x = startX; x <= b[2]; x += 8) { ctx.moveTo(x, b[1]); ctx.lineTo(x, b[3]); }
      if (['diagonal', 'crosshatch'].includes(style.pattern)) { const height = b[3] - b[1]; for (let x = Math.floor((b[0] - height) / 8) * 8; x <= b[2]; x += 8) { ctx.moveTo(x, b[3]); ctx.lineTo(x + height, b[1]); } }
      ctx.stroke();
    }
    ctx.restore();
  }
  function drawMarks(ctx, model, geometry, state = {}) {
    const view = filteredMarks(model, state), drawnKeys = new Set();
    for (const {mark} of view.marks) {
      if (mark.type === 'image') {
        const asset = imageAsset(model, mark.image), matrix = imageScreenTransform(mark.image, geometry);
        ctx.save(); ctx.transform(...matrix); ctx.drawImage(asset.bitmap, 0, 0); ctx.restore(); continue;
      }
      const p = screenMark(model, mark, geometry), s = markStyle(model, mark);
      ctx.save(); ctx.globalAlpha = s.opacity; if (s.fill !== 'none') ctx.fillStyle = s.fill; if (s.stroke !== 'none') ctx.strokeStyle = s.stroke; ctx.lineWidth = s.width; ctx.setLineDash(s.dash); ctx.lineJoin = 'round'; ctx.lineCap = 'round';
      if (p.type === 'text') { ctx.font = '11px sans-serif'; ctx.textAlign = p.anchor === 'middle' ? 'center' : p.anchor || 'start'; ctx.textBaseline = 'alphabetic'; if (s.fill !== 'none') ctx.fillText(p.text, p.x, p.y); if (s.stroke !== 'none' && s.width) ctx.strokeText(p.text, p.x, p.y); }
      else {
        canvasPath(ctx, p, s); if (s.fill !== 'none' && s.pattern !== 'hollow') ctx.fill(); if (s.stroke !== 'none' && s.width) ctx.stroke();
        if (s.fill !== 'none') canvasPattern(ctx, p, s, geometry);
        if (p.type === 'arrow' && s.stroke !== 'none') { const points = arrowHead(p, s.width); if (points.length) { ctx.beginPath(); polygonPath(ctx, points, true); ctx.fillStyle = s.stroke; ctx.fill(); } }
      }
      ctx.restore(); for (const key of mark.keys) drawnKeys.add(key);
    }
    const screenPoints = [], b = geometry.bounds;
    for (const row of model.rows) if (drawnKeys.has(row.key) && finite(row.x) && finite(row.y) && row.x >= b[0] && row.x <= b[1] && row.y >= b[2] && row.y <= b[3]) screenPoints.push({x: geometry.sx(row.x), y: geometry.sy(row.y), row});
    return {screenPoints, drawnMarks: view.marks.length, omittedMarks: view.omittedMarks};
  }

  function svgPattern(id, style) {
    const paths = {diagonal: 'M -2 2 L 2 -2 M 0 8 L 8 0 M 6 10 L 10 6', horizontal: 'M 0 0 H 8', vertical: 'M 0 0 V 8', cross: 'M 0 0 H 8 M 0 0 V 8', crosshatch: 'M 0 0 H 8 M 0 0 V 8 M 0 8 L 8 0'};
    return '<pattern id="' + id + '" patternUnits="userSpaceOnUse" width="8" height="8"><rect width="8" height="8" fill="' + style.fill + '"/>' + (style.pattern === 'dots' ? '<circle cx="4" cy="4" r="1" fill="#ffffff"/>' : '<path d="' + paths[style.pattern] + '" fill="none" stroke="#ffffff" stroke-width="1"/>') + '</pattern>';
  }
  function svgStyle(style, fill) {
    return ' fill="' + (fill ?? (style.pattern === 'hollow' ? 'none' : style.fill)) + '" stroke="' + style.stroke + '" stroke-width="' + num(style.width) + '" opacity="' + num(style.opacity) + '" stroke-linejoin="round" stroke-linecap="round"' + (style.dash.length ? ' stroke-dasharray="' + style.dash.map(num).join(' ') + '"' : '');
  }
  const svgPoints = points => points.map(p => p.map(num).join(',')).join(' ');
  const svgNamespace = model => 'scientific-' + model.source.sha256 + '-' + Array.from(model.kind, c => c.codePointAt(0).toString(16)).join('-');
  function svgPrimitive(p, s, fill) {
    const style = svgStyle(s, fill);
    if (p.type === 'point') {
      if (s.shape === 'circle') return '<circle cx="' + num(p.x) + '" cy="' + num(p.y) + '" r="' + num(p.radius) + '"' + style + '/>';
      if (s.shape === 'square') { const a = p.radius * Math.sqrt(Math.PI) / 2; return '<rect x="' + num(p.x - a) + '" y="' + num(p.y - a) + '" width="' + num(2 * a) + '" height="' + num(2 * a) + '"' + style + '/>'; }
      return '<polygon points="' + svgPoints(pointVertices(p, s.shape)) + '"' + style + '/>';
    }
    if (p.type === 'line' || p.type === 'arrow') {
      let result = '<line x1="' + num(p.x1) + '" y1="' + num(p.y1) + '" x2="' + num(p.x2) + '" y2="' + num(p.y2) + '"' + style + '/>';
      const head = p.type === 'arrow' ? arrowHead(p, s.width) : [];
      if (head.length) result += '<polygon data-arrowhead="true" points="' + svgPoints(head) + '" fill="' + s.stroke + '" opacity="' + num(s.opacity) + '"/>';
      return result;
    }
    if (p.type === 'polyline' || p.type === 'polygon') return '<' + p.type + ' points="' + svgPoints(p.points) + '"' + style + '/>';
    if (p.type === 'rect') return '<rect x="' + num(p.x) + '" y="' + num(p.y) + '" width="' + num(p.width) + '" height="' + num(p.height) + '"' + style + '/>';
    if (p.type === 'ellipse') return '<ellipse cx="' + num(p.x) + '" cy="' + num(p.y) + '" rx="' + num(p.rx) + '" ry="' + num(p.ry) + '"' + style + '/>';
    if (p.type === 'arc') return '<path d="M ' + p.points[0].map(num).join(' ') + (p.points.length === 3 ? ' Q ' : ' C ') + p.points.slice(1).flat().map(num).join(' ') + '"' + style + '/>';
    return '<text x="' + num(p.x) + '" y="' + num(p.y) + '" text-anchor="' + (p.anchor || 'start') + '" font-size="11" font-family="sans-serif"' + style + '>' + esc(p.text) + '</text>';
  }
  function svgMarks(model, geometry, state = {}, scope = 'current') {
    const view = filteredMarks(model, state, scope), parts = [], patterns = new Map(), namespace = svgNamespace(model);
    for (const {mark, index} of view.marks) {
      if (mark.type === 'image') {
        const asset = imageAsset(model, mark.image), matrix = imageScreenTransform(mark.image, geometry), payload = mark.image;
        parts.push('<g data-mark-index="' + index + '" data-mark-type="image" data-source-keys="[]" data-group="tissue_reference"><image data-role="tissue_reference" data-image-sha256="' + payload.sha256 + '" data-registration-id="' + esc(payload.registration_id) + '" width="' + payload.width + '" height="' + payload.height + '" transform="matrix(' + matrix.map(num).join(' ') + ')" preserveAspectRatio="none" style="image-orientation:none" href="data:image/png;base64,' + asset.pngBase64 + '"/></g>');
        continue;
      }
      const p = screenMark(model, mark, geometry), s = markStyle(model, mark); let fill;
      if (!['solid', 'hollow'].includes(s.pattern)) {
        const key = s.pattern + ':' + s.fill;
        if (!patterns.has(key)) patterns.set(key, {id: namespace + '-mark-pattern-' + patterns.size, style: s});
        fill = 'url(#' + patterns.get(key).id + ')';
      }
      parts.push('<g data-mark-index="' + index + '" data-mark-type="' + mark.type + '" data-source-keys="' + esc(JSON.stringify(mark.keys)) + '" data-group="' + esc(mark.group) + '">' + svgPrimitive(p, s, fill) + '</g>');
    }
    return (patterns.size ? '<defs>' + [...patterns.values()].map(p => svgPattern(p.id, p.style)).join('') + '</defs>' : '') + parts.join('');
  }
  function legendCategories(model, state, scope) {
    const grouped = new Map(), records = filteredMarks(model, state, scope).marks.filter(r => r.mark.type !== 'image').map(({mark, index}) => ({mark, index, style: markStyle(model, mark)}));
    const pointShapes = new Set(model.scientificPanel.marks.filter(mark => mark.type === 'point' && mark.group).map(mark => markStyle(model, mark).shape));
    records.forEach(({mark, index, style: recordedStyle}) => {
      if (!mark.group) return;
      let style = recordedStyle;
      const closed = ['point', 'rect', 'ellipse', 'polygon', 'text'].includes(mark.type);
      let fill = style.pattern === 'hollow' ? 'none' : style.fill;
      const ink = closed && fill !== 'none' ? 'fill' : 'stroke';
      if (style.opacity === 0 || (ink === 'stroke' && (style.stroke === 'none' || style.width === 0))) return;
      const continuous = Boolean(model.colorScale && paletteName(model.colorScale.palette) !== 'OkabeIto' && finite(mark.value) && mark.style?.[ink] === undefined);
      // Continuous color alone needs only its colorbar. A second encoding still
      // needs a neutral, explicitly labelled key for its shape, dash or outline.
      if (continuous) {
        const saved = mark.style || {};
        const shapeChannel = mark.type === 'point' && (mark.shape !== undefined || pointShapes.size > 1);
        const outlineChannel = closed && saved.stroke !== undefined && saved.stroke !== 'none' && style.width > 0;
        const otherChannel = style.pattern !== 'solid' || style.dash.length > 0 || (saved.opacity !== undefined && saved.opacity !== 1) || (saved.stroke_width !== undefined && saved.stroke_width !== 1 && style.width > 0 && style.stroke !== 'none');
        if (!shapeChannel && !outlineChannel && !otherChannel) return;
        style = ink === 'fill' ? {...style, fill: '#d5dce0', stroke: saved.stroke ?? (style.stroke === 'none' ? 'none' : '#d5dce0')} : {...style, stroke: '#596b76'};
        fill = style.pattern === 'hollow' ? 'none' : style.fill;
      }
      const symbol = ['line', 'arrow', 'polyline', 'arc'].includes(mark.type) ? 'line' : 'point';
      const signature = JSON.stringify([fill, style.stroke, style.shape, style.pattern, symbol, style.width, style.opacity, style.dash, continuous]);
      if (!grouped.has(mark.group)) grouped.set(mark.group, new Map());
      const variants = grouped.get(mark.group);
      if (!variants.has(signature)) variants.set(signature, {...style, fill, color: ink === 'fill' ? style.fill : style.stroke,
        group: mark.group, symbol, continuous, types: [], markIndices: []});
      const entry = variants.get(signature);
      entry.markIndices.push(index); if (!entry.types.includes(mark.type)) entry.types.push(mark.type);
    });
    const result = [];
    for (const name of categories(model).keys()) {
      const variants = [...(grouped.get(name)?.values() || [])];
      variants.forEach((entry, i) => {
        const detail = entry.types.join('/') + (entry.symbol === 'point' ? ' · ' + entry.shape : '');
        result.push({...entry, index: result.length, label: name + (variants.length > 1 ? ' · ' + detail + ' · ' + (i + 1) + '/' + variants.length : '') + (entry.continuous ? ' (color: continuous scale)' : '')});
      });
    }
    return result;
  }
  function legend(model, state = {}, scope = 'complete') {
    const c = model.colorScale && paletteName(model.colorScale.palette) !== 'OkabeIto' ? model.colorScale : null, s = model.sizeScale;
    const colorLegend = c ? {...c, stops: Array.from({length: 256}, (_, i) => { const value = c.domain[0] + (c.domain[1] - c.domain[0]) * i / 255; return {offset: i / 255, value, color: scaleColor(c, value)}; })} : null;
    const values = s ? [...new Set([s.domain[0], s.domain[1] * .25, s.domain[1] * .5, s.domain[1]].filter(v => v >= s.domain[0]))] : [];
    const sizeLegend = s ? {...s, area_proportional: true, values: values.map(value => { const r = s.domain[1] === 0 ? 0 : s.max_radius * Math.sqrt(value / s.domain[1]); return {value, radius: r, area: Math.PI * r * r}; })} : null;
    return {color: colorLegend, size: sizeLegend, categories: legendCategories(model, state, scope)};
  }
  function legendTextLines(value, width) {
    // One em per Unicode code point safely bounds the 11px legend font, including
    // unbroken identifiers. The same layout is used for measurement and SVG.
    const limit = Math.max(1, Math.floor(width / 11)), lines = [];
    for (const paragraph of String(value).split('\n')) {
      const characters = Array.from(paragraph);
      if (!characters.length) lines.push('');
      for (let start = 0; start < characters.length;) {
        let end = Math.min(characters.length, start + limit);
        if (end < characters.length) {
          const segment = characters.slice(start, end).join(''), space = segment.lastIndexOf(' ');
          if (space > limit / 2) end = start + space + 1;
        }
        lines.push(characters.slice(start, end).join('')); start = end;
      }
    }
    return lines;
  }
  function legendLayout(model, width, state = {}, scope = 'complete') {
    if (!finite(width) || width < 80) fail('Legend width must be finite and at least 80 pixels.');
    const description = legend(model, state, scope), items = []; let top = 0;
    const text = (x, y, value, anchor = 'start') => items.push({type: 'text', x, y, value, anchor});
    const paragraph = (value, offset = 0) => {
      const lines = legendTextLines(value, width - offset);
      lines.forEach((line, i) => text(offset, top + 12 + i * 15, line));
      top += lines.length * 15 + 5;
    };
    if (description.color) {
      const c = description.color; paragraph(c.label + (c.unit ? ' · ' + c.unit : ''));
      items.push({type: 'color', y: top, scale: c}); top += 28;
      text(0, top, labelNumber(c.domain[0])); text(width, top, labelNumber(c.domain[1]), 'end');
      if (c.center === 0) { if (width < 140) top += 15; text(width / 2, top, '0', 'middle'); }
      top += 16;
    }
    if (description.size) {
      const s = description.size, factor = Math.min(1, (width - 4) / (2 * s.max_radius)), rMax = s.max_radius * factor;
      paragraph(s.label + (s.unit ? ' · ' + s.unit : '') + ' · proportional area' + (factor < 1 ? ' · legend glyphs scaled' : ''));
      const sideLabels = width >= 2 * rMax + 100;
      for (const value of s.values) {
        const r = value.radius * factor, height = Math.max(14, 2 * r), cx = sideLabels ? rMax + 2 : width / 2, cy = top + height / 2;
        items.push({type: 'size', x: cx, y: cy, radius: r});
        if (sideLabels) text(2 * rMax + 16, cy + 4, labelNumber(value.value));
        else text(cx, top + height + 14, labelNumber(value.value), 'middle');
        top += height + (sideLabels ? 10 : 28);
      }
      top += 8;
    }
    for (const category of description.categories) {
      const glyphHalf = 8 + category.width / 2, indent = 2 * glyphHalf + 8;
      const lines = legendTextLines(category.label, width - indent), height = Math.max(23, lines.length * 15 + 5, 2 * glyphHalf + 6);
      items.push({type: 'category', x: glyphHalf, y: top + Math.max(8, glyphHalf), category});
      lines.forEach((line, i) => text(indent, top + 12 + i * 15, line)); top += height;
    }
    return {items, height: top};
  }
  function legendHeight(model, placement = {}) { return legendLayout(model, placement.width ?? 260, placement.state, placement.scope).height; }
  function svgLegend(model, placement = {}) {
    const x = placement.x ?? 0, y = placement.y ?? 0, width = placement.width ?? 260;
    if (![x, y, width].every(finite)) fail('Legend placement must be finite.');
    const layout = legendLayout(model, width, placement.state, placement.scope), parts = [], namespace = svgNamespace(model);
    for (const item of layout.items) {
      if (item.type === 'text') parts.push('<text x="' + num(x + item.x) + '" y="' + num(y + item.y) + '" text-anchor="' + item.anchor + '" font-family="sans-serif" font-size="11" fill="#243744">' + esc(item.value) + '</text>');
      else if (item.type === 'color') for (let i = 0; i < 256; i++) parts.push('<rect x="' + num(x + i * width / 256) + '" y="' + num(y + item.y) + '" width="' + num(i === 255 ? width / 256 : width / 256 + .01) + '" height="12" fill="' + item.scale.stops[i].color + '"/>');
      else if (item.type === 'size') parts.push('<circle cx="' + num(x + item.x) + '" cy="' + num(y + item.y) + '" r="' + num(item.radius) + '" fill="none" stroke="#405766"/>');
      else {
        const category = item.category, style = {...category}; let fill;
        if (!['solid', 'hollow'].includes(style.pattern)) { const id = namespace + '-legend-pattern-' + category.index; parts.push('<defs>' + svgPattern(id, style) + '</defs>'); fill = 'url(#' + id + ')'; }
        const glyph = category.symbol === 'line' ? {type: 'line', x1: x + item.x - 8, y1: y + item.y, x2: x + item.x + 8, y2: y + item.y} : {type: 'point', x: x + item.x, y: y + item.y, radius: 4};
        parts.push('<g data-legend-group="' + esc(category.group) + '" data-legend-variant="' + category.index + '">' + svgPrimitive(glyph, style, fill) + '</g>');
      }
    }
    return '<g data-scientific-legend="true" data-legend-height="' + num(layout.height) + '">' + parts.join('') + '</g>';
  }
  const api = {prepareWorkspace, project, drawMarks, svgMarks, legend, svgLegend, legendHeight, color, filteredMarks,
    loadAssets, disposeAssets, paddedBounds, pixelPadding, limits: LIMITS};
  root.ScientificPlotPanels = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
