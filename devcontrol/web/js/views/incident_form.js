// Alta de incidencia: plantilla de campos según la categoría, avisos de posibles duplicados,
// borrador local, adjuntos y envío con cola sin conexión.
import { api } from '../api.js';
import { CATEGORIES, ENVIRONMENTS, FREQUENCIES, INCIDENT_STATUSES, PRIORITIES, REPORT_FIELDS } from '../labels.js';
import { activeProjects, can, defaultProjectId, store } from '../state.js';
import {
  debounce, draftKey, field, fmtSize, h, icon, loadJSON, mount, pageHead, priorityBadge, saveJSON, selectEl, statusBadge, toast, todayISO, uid,
} from '../ui.js';
import { projectSelect, uploadFiles, userSelect } from './shared.js';

const DRAFT = draftKey('incident_form');
const LONG = ['steps', 'expected', 'observed'];
const PLACEHOLDERS = {
  steps: '1. Abrir la app\n2. Ir a…\n3. Pulsar…', expected: 'Qué debería pasar', observed: 'Qué pasa en realidad (mensajes de error, pantalla…)',
  device: 'iPhone 13, Pixel 7, PC Windows…', os_version: 'iOS 17.4, Android 14, Windows 11…', app_version: '2.3.1 (build 145)',
};

export default async function render(ctx) {
  const { root } = ctx;
  const draft = loadJSON(DRAFT, null) || {};
  const q = ctx.query;
  const state = {
    uid: draft.uid || uid(),
    project_id: draft.project_id || defaultProjectId(),
    title: q.get('title') || draft.title || '',
    description: q.get('description') || draft.description || '',
    priority: draft.priority || 'media',
    category: draft.category || 'errores',
    report: draft.report || {},
    na: draft.na || {},
    assignee_id: draft.assignee_id || '',
    target_date: draft.target_date || '',
    labels: draft.labels || '',
    estimate_hours: draft.estimate_hours || '',
    found_build_id: draft.found_build_id || '',
    source_message_id: q.get('source_message_id') || draft.source_message_id || '',
  };
  let files = [];
  let missing = new Set();
  const hadDraft = !!(draft.title || draft.description);

  const saveDraft = debounce(() => saveJSON(DRAFT, state), 300);
  const clearDraftAll = () => { try { localStorage.removeItem(DRAFT); } catch { /* ignorado */ } };

  // ---------- controles básicos ----------
  const projects = activeProjects();
  const projSel = projectSelect(state.project_id);
  const title = h('input', { value: state.title, maxlength: 200, placeholder: 'Resumen corto del problema (p. ej. «La app se cierra al pagar con tarjeta»)' });
  const dupBox = h('div', { class: 'dup-hint' });
  const desc = h('textarea', { rows: 5, placeholder: 'Describe el problema con detalle. Puedes mencionar a alguien con @usuario o enlazar otras incidencias (APP-012).' });
  desc.value = state.description;
  const prio = selectEl(Object.entries(PRIORITIES), state.priority);
  const cat = selectEl(Object.entries(CATEGORIES), state.category);
  const target = h('input', { type: 'date', value: state.target_date, min: todayISO() });
  const labels = h('input', { value: state.labels, placeholder: 'login, pagos, ios (separadas por comas)' });
  const estimate = h('input', { type: 'number', min: 0, step: 0.5, value: state.estimate_hours, placeholder: 'Horas' });
  const canAssignOthers = can('incidencias.asignar');
  const canSelf = can('incidencias.trabajar');
  let assignee = null;
  if (canAssignOthers) assignee = userSelect(state.assignee_id, { projectId: state.project_id });
  else if (canSelf) assignee = selectEl([['', 'Sin asignar'], [String(store.me.id), `${store.me.display_name} (yo)`]], String(state.assignee_id || ''));
  const buildSel = h('select', null, h('option', { value: '' }, 'No lo sé / no aplica'));
  const prioHint = h('span');

  // ---------- plantilla según la categoría ----------
  const tplBox = h('div', { class: 'form-grid' });
  const moreBox = h('div', { class: 'form-grid' });
  const tplNote = h('div', { class: 'sub' });
  const controls = {};
  function reportControl(k) {
    let c;
    if (k === 'environment') c = selectEl([['', '—'], ...Object.entries(ENVIRONMENTS)], state.report[k] || '');
    else if (k === 'frequency') c = selectEl([['', '—'], ...Object.entries(FREQUENCIES)], state.report[k] || '');
    else if (LONG.includes(k)) { c = h('textarea', { rows: k === 'steps' ? 4 : 3, placeholder: PLACEHOLDERS[k] || '' }); c.value = state.report[k] || ''; }
    else c = h('input', { value: state.report[k] || '', placeholder: PLACEHOLDERS[k] || '' });
    c.addEventListener('input', () => { state.report[k] = c.value; missing.delete(k); c.closest('.tpl-field')?.classList.remove('missing'); saveDraft(); });
    c.addEventListener('change', () => { state.report[k] = c.value; saveDraft(); });
    return c;
  }
  function tplField(k, kind) {
    const c = controls[k] || (controls[k] = reportControl(k));
    const wrap = h('div', { class: `tpl-field ${LONG.includes(k) ? 'span2' : ''}` });
    const isNa = Object.prototype.hasOwnProperty.call(state.na, k);
    const naCheck = h('input', { type: 'checkbox', checked: isNa });
    const naReason = h('input', { type: 'text', class: 'na-reason' + (isNa ? '' : ' hidden'), placeholder: '¿Por qué no aplica?', value: state.na[k] || '' });
    const sync = () => {
      wrap.classList.toggle('is-na', naCheck.checked);
      wrap.classList.toggle('missing', missing.has(k));
      c.disabled = naCheck.checked;
      naReason.classList.toggle('hidden', !naCheck.checked);
    };
    naCheck.addEventListener('change', () => {
      if (naCheck.checked) { state.na[k] = naReason.value; setTimeout(() => naReason.focus(), 0); } else delete state.na[k];
      missing.delete(k); sync(); saveDraft();
    });
    naReason.addEventListener('input', () => { state.na[k] = naReason.value; saveDraft(); });
    sync();
    mount(wrap, h('label', { class: 'field' }, h('span', { class: kind === 'required' ? 'req' : '' }, REPORT_FIELDS[k],
      kind === 'recommended' ? h('span', { class: 'tag-rec' }, 'Recomendado') : null), c),
    kind !== 'optional' ? h('div', { class: 'na' }, h('label', { class: 'check' }, naCheck, 'No aplicable'), naReason) : null);
    return wrap;
  }
  function template() {
    const tpls = store.settings.report_templates || {};
    return tpls[state.category] || { required: [], recommended: [] };
  }
  function drawTemplate() {
    const tpl = template();
    const req = tpl.required || [], rec = (tpl.recommended || []).filter(k => !req.includes(k));
    const rest = Object.keys(REPORT_FIELDS).filter(k => !req.includes(k) && !rec.includes(k));
    mount(tplNote, req.length
      ? `Para «${CATEGORIES[state.category]}» son obligatorios: ${req.map(k => REPORT_FIELDS[k]).join(', ')}. Si alguno no aplica, márcalo e indica el motivo.`
      : `Para «${CATEGORIES[state.category]}» no hay datos obligatorios, pero cuantos más detalles, antes se arregla.`);
    mount(tplBox, [...req.map(k => tplField(k, 'required')), ...rec.map(k => tplField(k, 'recommended'))]);
    mount(moreBox, rest.map(k => tplField(k, 'optional')));
    moreDetails.classList.toggle('hidden', !rest.length);
  }
  const moreDetails = h('details', { class: 'mt' }, h('summary', { class: 'small', style: { cursor: 'pointer' } }, 'Más datos (opcionales)'), h('div', { class: 'mt' }, moreBox));

  // ---------- posibles duplicados ----------
  let simToken = 0;
  const checkSimilar = debounce(async () => {
    const text = title.value.trim();
    const token = ++simToken;
    if (text.length < 4) { mount(dupBox); return; }
    try {
      const r = await api.get(`/api/incidents/similar?q=${encodeURIComponent(text)}`);
      if (token !== simToken) return;
      mount(dupBox, r.items.length ? h('div', { class: 'callout warn' },
        h('div', { class: 'small' }, h('b', null, '¿Ya existe? '), 'Estas incidencias se parecen. Ábrelas para comprobarlo (tu borrador se conserva):'),
        r.items.map(i => h('a', { class: 'list-item', href: `#/incidencias/${i.key}` }, h('span', { class: 'key' }, i.key),
          h('span', { class: 'grow ellipsis' }, i.title), priorityBadge(i.priority, PRIORITIES[i.priority]), statusBadge(i.status, INCIDENT_STATUSES[i.status])))) : null);
    } catch { /* la comprobación es opcional */ }
  }, 400);

  // ---------- builds del proyecto ----------
  async function loadBuilds() {
    if (!can('calidad.ver')) return;
    try {
      const r = await api.get(`/api/builds?project=${state.project_id}`);
      mount(buildSel, h('option', { value: '' }, 'No lo sé / no aplica'),
        r.items.map(b => h('option', { value: b.id }, `${b.number} · ${b.platform_label || b.platform}${b.version_name ? ` · ${b.version_name}` : ''}${b.channel_label ? ` (${b.channel_label})` : ''}`)));
      buildSel.value = r.items.some(b => String(b.id) === String(state.found_build_id)) ? String(state.found_build_id) : '';
      buildField.classList.toggle('hidden', !r.items.length);
    } catch { buildField.classList.add('hidden'); }
  }
  const buildField = field('Build en la que se encontró', buildSel, { hint: 'Ayuda a saber desde cuándo ocurre.' });
  buildField.classList.add('hidden');

  // ---------- adjuntos ----------
  const fileInput = h('input', { type: 'file', multiple: true, class: 'hidden' });
  const pills = h('div', { class: 'file-pills' });
  const drawFiles = () => mount(pills, files.map((f, i) => h('span', { class: 'file-pill' }, icon('clip', 13), h('span', { title: f.name }, `${f.name} (${fmtSize(f.size)})`),
    h('button', { type: 'button', title: 'Quitar', onclick: () => { files.splice(i, 1); drawFiles(); } }, '×'))));
  const addFiles = (list) => { files.push(...list); drawFiles(); };
  fileInput.addEventListener('change', () => { addFiles([...fileInput.files]); fileInput.value = ''; });
  const drop = h('div', { class: 'dropzone', tabindex: '0', onclick: () => fileInput.click(),
    ondragover: (e) => { e.preventDefault(); drop.classList.add('over'); }, ondragleave: () => drop.classList.remove('over'),
    ondrop: (e) => { e.preventDefault(); drop.classList.remove('over'); addFiles([...(e.dataTransfer?.files || [])]); } },
  h('span', { class: 'dz-icon' }, icon('upload')), 'Arrastra capturas o vídeos, pégalos con Ctrl+V en cualquier parte del formulario o haz clic');
  const uploadList = h('div', { class: 'upload-list' });

  // ---------- eventos ----------
  const bind = (el, key, ev = 'input') => el.addEventListener(ev, () => { state[key] = el.value; saveDraft(); });
  bind(title, 'title'); bind(desc, 'description'); bind(target, 'target_date', 'change'); bind(labels, 'labels'); bind(estimate, 'estimate_hours');
  title.addEventListener('input', checkSimilar);
  const updPrioHint = () => mount(prioHint, state.priority === 'critica' ? h('span', { class: 'tiny danger-text' }, 'Se avisará con urgencia a todo el equipo técnico.') : null);
  prio.addEventListener('change', () => { state.priority = prio.value; updPrioHint(); saveDraft(); });
  cat.addEventListener('change', () => { state.category = cat.value; missing.clear(); drawTemplate(); saveDraft(); });
  assignee?.addEventListener('change', () => { state.assignee_id = assignee.value; saveDraft(); });
  buildSel.addEventListener('change', () => { state.found_build_id = buildSel.value; saveDraft(); });
  projSel.addEventListener('change', () => {
    state.project_id = Number(projSel.value); state.found_build_id = '';
    if (assignee?.setProject) assignee.setProject(state.project_id);
    loadBuilds(); saveDraft();
  });

  const submitBtn = h('button', { class: 'btn primary', type: 'submit' }, icon('check'), 'Crear incidencia');
  const discardBtn = h('button', { class: 'btn ghost', type: 'button', onclick: () => {
    clearDraftAll(); toast('Borrador descartado.', 'info'); ctx.refresh();
  } }, 'Descartar borrador');

  function validate() {
    if (!state.title.trim()) { toast('Escribe un título.', 'error'); title.focus(); return false; }
    if (!state.project_id) { toast('No tienes ningún proyecto disponible. Pide a un administrador que te añada a uno.', 'error'); return false; }
    const tpl = template();
    missing = new Set((tpl.required || []).filter(k => !(state.report[k] || '').trim() && !(Object.prototype.hasOwnProperty.call(state.na, k) && (state.na[k] || '').trim())));
    const naNoReason = Object.entries(state.na).filter(([, v]) => !(v || '').trim()).map(([k]) => k);
    naNoReason.forEach(k => missing.add(k));
    if (missing.size) {
      drawTemplate();
      toast(`Faltan datos: ${[...missing].map(k => REPORT_FIELDS[k]).join(', ')}. Rellénalos o márcalos como «no aplicable» con el motivo.`, 'error');
      tplBox.querySelector('.missing textarea, .missing input, .missing select')?.focus();
      return false;
    }
    return true;
  }

  async function submit(e) {
    e.preventDefault();
    if (!validate()) return;
    const report = {};
    for (const k of Object.keys(REPORT_FIELDS)) report[k] = Object.prototype.hasOwnProperty.call(state.na, k) ? '' : (state.report[k] || '').trim();
    const body = {
      uid: state.uid, project_id: Number(state.project_id), title: state.title.trim(), description: state.description.trim(),
      priority: state.priority, category: state.category, ...report,
      na_fields: Object.fromEntries(Object.entries(state.na).map(([k, v]) => [k, v.trim()])),
      assignee_id: state.assignee_id ? Number(state.assignee_id) : null, target_date: state.target_date || null,
      labels: state.labels, estimate_hours: state.estimate_hours === '' ? null : Number(state.estimate_hours),
      found_build_id: state.found_build_id ? Number(state.found_build_id) : null,
      source_message_id: state.source_message_id ? Number(state.source_message_id) : undefined,
    };
    submitBtn.disabled = true;
    try {
      const r = await api.post('/api/incidents', body, { queue: true });
      const queued = !!r._queued;
      if (files.length) {
        toast(`Subiendo ${files.length} ${files.length === 1 ? 'archivo' : 'archivos'}…`, 'info');
        await uploadFiles(files, queued ? { owner_type: 'incident', owner_uid: state.uid } : { owner_type: 'incident', owner_id: r.item.id }, uploadList);
      }
      clearDraftAll();
      if (queued) {
        toast('Sin conexión: la incidencia se ha guardado y se enviará al reconectar.', 'warn');
        location.hash = '#/incidencias';
      } else {
        toast(r.duplicate ? `Ya se había creado como ${r.item.key}.` : `Incidencia ${r.item.key} creada.`, 'ok');
        location.hash = `#/incidencias/${r.item.key}`;
      }
    } catch (err) {
      if (err.code === 'reporte_incompleto' && err.data?.missing) { missing = new Set(err.data.missing); drawTemplate(); }
      toast(err.message, 'error');
    } finally { submitBtn.disabled = false; }
  }

  const form = h('form', { class: 'inc-form', onsubmit: submit, onpaste: (e) => {
    const f = [...(e.clipboardData?.files || [])];
    if (f.length) { e.preventDefault(); addFiles(f); toast(`${f.length === 1 ? 'Archivo añadido' : `${f.length} archivos añadidos`} a los adjuntos.`, 'ok'); }
  } },
  h('div', { class: 'card' }, h('h3', null, 'Qué pasa'), h('div', { class: 'sub' }, 'Lo imprescindible para que el equipo lo entienda.'),
    h('div', { class: 'form-grid' },
      projects.length > 1 ? field('Proyecto', projSel, { required: true, cls: 'span2' }) : null,
      h('div', { class: 'span2' }, field('Título', title, { required: true }), dupBox),
      field('Descripción', desc, { cls: 'span2' }),
      h('div', { class: 'col' }, field('Prioridad', prio, { required: true }), prioHint),
      field('Categoría', cat, { required: true, hint: 'La categoría decide qué datos se piden abajo.' }))),
  h('div', { class: 'card' }, h('h3', null, 'Datos del reporte'), tplNote, tplBox, moreDetails),
  h('div', { class: 'card' }, h('h3', null, 'Organización'), h('div', { class: 'sub' }, 'Opcional: quién se encarga, para cuándo y cómo encontrarla después.'),
    h('div', { class: 'form-grid' },
      assignee ? field('Responsable', assignee) : null,
      field('Fecha objetivo', target),
      field('Etiquetas', labels, { hint: 'Separadas por comas.' }),
      field('Estimación (horas)', estimate),
      buildField)),
  h('div', { class: 'card' }, h('h3', null, 'Adjuntos'), h('div', { class: 'sub' }, 'Capturas de pantalla, vídeos o registros. Se suben al crear la incidencia.'),
    drop, fileInput, pills, uploadList),
  h('div', { class: 'form-actions' },
    h('span', { class: 'grow' }, hadDraft ? 'Se ha recuperado tu borrador.' : 'El borrador se guarda solo en este equipo mientras escribes.'),
    hadDraft ? discardBtn : null,
    h('a', { class: 'btn', href: '#/incidencias' }, 'Cancelar'), submitBtn));

  mount(root, pageHead('Nueva incidencia'), form);
  drawTemplate();
  updPrioHint();
  loadBuilds();
  if (state.title) checkSimilar();
  title.focus();
}
