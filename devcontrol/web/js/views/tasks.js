// Tareas: vista lista y tablero kanban con arrastrar y soltar, filtros, alta rápida y exportación.
import { api } from '../api.js';
import { PRIORITIES, TASK_STATUSES } from '../labels.js';
import { activeProjects, can, defaultProjectId, store, userById, withProject } from '../state.js';
import {
  avatar, busy, confirmDialog, debounce, emptyState, errorBox, field, fmtDate, fmtHours, h, icon, loadJSON, loading, modal, mondayOf,
  mount, pageHead, priorityBadge, promptDialog, saveJSON, selectEl, statusBadge, table, toast, todayISO, uid, addDays,
} from '../ui.js';
import { followButton, projectSelect, userSelect } from './shared.js';

const VIEW_KEY = 'dc.tasks.view';
const MODE_KEY = 'dc.tasks.mode';
const FILTER_KEYS = ['q', 'assignee', 'priority', 'version_id', 'label', 'week', 'status', 'to_verify'];
const COLUMNS = ['pendiente', 'en_progreso', 'bloqueada', 'en_verificacion', 'resuelto'];
const DONE_LIMIT = 15;
export const RECURRENCE = { daily: 'Cada día', weekly: 'Cada semana', monthly: 'Cada mes', yearly: 'Cada año', after_days: 'N días después de completarla' };

const tpath = (t) => `/api/tasks/${encodeURIComponent(t.key || t.id)}`;
const isMe = (id) => id === store.me?.id;

export function recurrenceText(r) {
  if (!r) return '';
  const n = r.interval || 1;
  if (r.freq === 'after_days') return `${n} ${n === 1 ? 'día' : 'días'} después de completarla`;
  const units = { daily: ['día', 'días'], weekly: ['semana', 'semanas'], monthly: ['mes', 'meses'], yearly: ['año', 'años'] }[r.freq] || ['', ''];
  return n === 1 ? RECURRENCE[r.freq] : `Cada ${n} ${units[1]}`;
}

// ---------- cambios de estado (los usa también la ficha de la tarea) ----------
function resolveTaskDialog(task) {
  return new Promise((resolve) => {
    let result = null;
    const note = h('textarea', { rows: 4, placeholder: 'Opcional: qué se ha hecho y cómo comprobarlo' });
    const needs = store.settings.verification_required !== false && !isMe(task.created_by);
    modal({
      title: `Marcar ${task.key} como hecha`,
      body: h('div', { class: 'col gap-lg' },
        h('div', { class: `callout ${needs ? 'info' : 'ok'} small` }, needs
          ? `Pasará a «En verificación»: ${task.created_by_name || 'quien la creó'} comprobará el resultado antes de cerrarla.`
          : 'Se cerrará directamente como resuelta.'),
        field('Nota de resolución', note)),
      actions: [{ label: 'Cancelar' }, { label: needs ? 'Enviar a verificación' : 'Marcar como hecha', kind: 'ok', onClick: () => { result = note.value.trim(); } }],
      onClose: () => resolve(result),
    });
  });
}

// Devuelve la tarea actualizada o null si se canceló o falló (el error ya se ha mostrado).
export async function changeTaskStatus(task, status) {
  if (task.status === status) return null;
  if (task.status === 'en_verificacion') {
    toast(`${task.key} está pendiente de verificación: verifícala o recházala desde su ficha.`, 'warn');
    return null;
  }
  const send = (target, body = {}) => api.patch(tpath(task), { version: task.version, status: target, ...body });
  const run = async (fn, ok) => { try { return (await busy(null, fn, ok)).item || null; } catch { return null; } };
  if (status === 'bloqueada') {
    const reason = await promptDialog({ title: `Bloquear ${task.key}`, label: '¿Qué impide avanzar? Se avisará a quien la creó.', multiline: true, confirmLabel: 'Bloquear' });
    if (!reason) return null;
    return run(() => send('bloqueada', { block_reason: reason }), `${task.key} bloqueada.`);
  }
  if (status === 'resuelto' || status === 'en_verificacion') {
    const note = await resolveTaskDialog(task);
    if (note === null) return null;
    const needs = store.settings.verification_required !== false && !isMe(task.created_by);
    const ok = needs ? `${task.key} enviada a verificación.` : `${task.key} completada.`;
    try {
      const r = await send('resuelto', { resolution_note: note });
      toast(ok, 'ok');
      return r.item;
    } catch (err) {
      if (err.code === 'dependencias_abiertas') {
        if (await confirmDialog({ title: 'Tiene dependencias abiertas', message: `${err.message}\n\n¿Quieres darla por hecha igualmente?`, confirmLabel: 'Sí, darla por hecha' })) {
          return run(() => send('resuelto', { resolution_note: note, ignore_dependencies: true }), ok);
        }
        return null;
      }
      toast(err.message, 'error');
      return null;
    }
  }
  const msg = { pendiente: `${task.key} vuelve a «Pendiente».`, en_progreso: task.status === 'bloqueada' ? `Bloqueo quitado: ${task.key} en progreso.` : `${task.key} en progreso.` }[status];
  return run(() => send(status), msg);
}

// ---------- editor de lista de comprobación ----------
export function checklistEditor(items = []) {
  const list = h('div', { class: 'col', style: { gap: '4px' } });
  const rows = [];
  const addRow = (text = '', done = false) => {
    const input = h('input', { value: text, placeholder: 'Paso o elemento a comprobar' });
    const row = h('div', { class: 'row' }, input, h('button', { class: 'btn ghost sm icon', type: 'button', title: 'Quitar', onclick: () => { row.remove(); rows.splice(rows.indexOf(entry), 1); } }, icon('x')));
    const entry = { input, done };
    rows.push(entry);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addRow().input.focus(); } });
    list.appendChild(row);
    return entry;
  };
  items.forEach(i => addRow(i.text, i.done));
  const el = h('div', { class: 'checklist-edit' }, list, h('button', { class: 'btn ghost sm', type: 'button', style: { alignSelf: 'flex-start' }, onclick: () => addRow().input.focus() }, icon('plus'), 'Añadir elemento'));
  el.value = () => rows.map(r => ({ text: r.input.value.trim(), done: r.done })).filter(r => r.text);
  return el;
}

export function recurrenceControl(rec) {
  const freq = selectEl([['', 'No se repite'], ...Object.entries(RECURRENCE)], rec?.freq || '');
  const interval = h('input', { type: 'number', min: 1, max: 365, value: rec?.interval || 1, title: 'Cada cuántos' });
  const sync = () => interval.classList.toggle('hidden', !freq.value);
  freq.addEventListener('change', sync); sync();
  const el = h('div', { class: 'rec-row' }, freq, interval);
  el.value = () => (freq.value ? { freq: freq.value, interval: Math.max(1, Number(interval.value) || 1) } : null);
  el.freq = freq; el.interval = interval;
  return el;
}

export async function loadVersions(projectId) {
  if (!can('planificacion.ver')) return [];
  try { return (await api.get(`/api/versions?${projectId && projectId !== 'all' ? `project=${projectId}` : withProject()}`)).items; } catch { return []; }
}

// ---------- alta rápida ----------
export function newTaskModal({ onCreated, preset = {} } = {}) {
  const opUid = uid();
  let pid = preset.project_id || defaultProjectId();
  const title = h('input', { maxlength: 200, value: preset.title || '', placeholder: 'Qué hay que hacer' });
  const desc = h('textarea', { rows: 3, placeholder: 'Detalles (opcional). Usa @usuario para avisar a alguien.' });
  desc.value = preset.description || '';
  const proj = projectSelect(pid);
  const prio = selectEl(Object.entries(PRIORITIES), preset.priority || 'media');
  let assignee;
  if (can('tareas.asignar')) assignee = userSelect(preset.assignee_id ?? store.me.id, { projectId: pid });
  else assignee = selectEl([['', 'Sin asignar'], [String(store.me.id), `${store.me.display_name} (yo)`]], String(store.me.id));
  const target = h('input', { type: 'date', value: preset.target_date || '' });
  const week = h('input', { type: 'date', value: preset.planned_week || '' });
  const versionSel = h('select', null, h('option', { value: '' }, 'Sin versión'));
  const est = h('input', { type: 'number', min: 0, step: 0.5, placeholder: 'Horas' });
  const labels = h('input', { placeholder: 'Separadas por comas' });
  const checklist = checklistEditor([]);
  const rec = recurrenceControl(null);
  const fillVersions = async () => {
    const vs = await loadVersions(pid);
    mount(versionSel, h('option', { value: '' }, 'Sin versión'), vs.filter(v => v.status !== 'publicada').map(v => h('option', { value: v.id }, v.name)));
    versionField.classList.toggle('hidden', !vs.length);
  };
  const versionField = field('Versión', versionSel);
  proj.addEventListener('change', () => { pid = Number(proj.value); assignee.setProject?.(pid); fillVersions(); });
  fillVersions();
  const create = async (open) => {
    if (!title.value.trim()) { toast('Escribe el título de la tarea.', 'error'); title.focus(); return false; }
    const body = {
      uid: opUid, project_id: Number(pid), title: title.value.trim(), description: desc.value.trim(), priority: prio.value,
      assignee_id: assignee.value ? Number(assignee.value) : null, target_date: target.value || null,
      planned_week: week.value || null, version_id: versionSel.value ? Number(versionSel.value) : null,
      estimate_hours: est.value === '' ? null : Number(est.value), labels: labels.value, checklist: checklist.value(), recurrence: rec.value(),
      source_message_id: preset.source_message_id || undefined,
    };
    try {
      const r = await busy(null, () => api.post('/api/tasks', body, { queue: true }));
      if (r._queued) { onCreated && onCreated(null); return undefined; }
      toast(`Tarea ${r.item.key} creada.`, 'ok');
      onCreated && onCreated(r.item);
      if (open) location.hash = `#/tareas/${r.item.key}`;
      return undefined;
    } catch { return false; }
  };
  modal({
    title: 'Nueva tarea', wide: true,
    body: h('div', { class: 'form-grid' },
      field('Título', title, { required: true, cls: 'span2' }),
      field('Descripción', desc, { cls: 'span2' }),
      activeProjects().length > 1 ? field('Proyecto', proj, { required: true }) : null,
      field('Prioridad', prio),
      field('Responsable', assignee),
      field('Fecha objetivo', target),
      field('Semana prevista', week, { hint: 'Elige cualquier día: se usa la semana que lo contiene.' }),
      versionField,
      field('Estimación (horas)', est),
      field('Etiquetas', labels),
      field('Repetición', rec, { hint: 'Al completarla se crea la siguiente automáticamente.' }),
      h('div', { class: 'span2' }, field('Lista de comprobación', checklist))),
    actions: [{ label: 'Cancelar' }, { label: 'Crear y abrir', onClick: () => create(true) }, { label: 'Crear tarea', kind: 'primary', onClick: () => create(false) }],
  });
}

// ---------- vista ----------
export default async function render(ctx) {
  const { root } = ctx;
  const hasQuery = FILTER_KEYS.some(k => ctx.query.has(k));
  let f = hasQuery ? Object.fromEntries(FILTER_KEYS.filter(k => ctx.query.get(k)).map(k => [k, ctx.query.get(k)])) : { ...loadJSON(VIEW_KEY, {}) };
  let mode = loadJSON(MODE_KEY, 'board');
  let items = [];
  let versions = [];
  let reqToken = 0;
  let dragging = null;

  const search = h('input', { type: 'search', placeholder: 'Buscar por clave, título o descripción…', value: f.q || '' });
  const filtersBox = h('div', { class: 'filters ws-filters' });
  const countBox = h('div', { class: 'ws-count' });
  const body = h('div');
  const modeSeg = h('div', { class: 'seg' });

  const persist = () => {
    saveJSON(VIEW_KEY, f);
    const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
    history.replaceState(null, '', `#/tareas${qs ? `?${qs}` : ''}`);
  };
  const set = (patch) => {
    f = { ...f, ...patch };
    for (const k of Object.keys(f)) if (!f[k]) delete f[k];
    persist(); drawFilters(); load();
  };

  function drawMode() {
    mount(modeSeg, [['board', 'Tablero', 'board'], ['list', 'Lista', 'menu']].map(([k, l, ic]) =>
      h('button', { class: mode === k ? 'on' : '', onclick: () => { mode = k; saveJSON(MODE_KEY, mode); drawMode(); drawFilters(); draw(); } }, icon(ic, 14), l)));
  }

  function drawFilters() {
    const who = h('select', { onchange: (e) => set({ assignee: e.target.value }) }, h('option', { value: '' }, 'Cualquiera'),
      h('option', { value: 'me' }, 'Mis tareas'), h('option', { value: 'none' }, 'Sin asignar'), store.users.map(u => h('option', { value: u.id }, u.display_name)));
    who.value = f.assignee || '';
    const prio = selectEl([['', 'Todas'], ...Object.entries(PRIORITIES)], f.priority || '', { onchange: (e) => set({ priority: e.target.value }) });
    const ver = selectEl([['', 'Todas'], ['none', 'Sin versión'], ...versions.map(v => [String(v.id), v.name])], f.version_id || '', { onchange: (e) => set({ version_id: e.target.value }) });
    const labelsList = [...new Set(items.flatMap(t => t.labels_list || []))].sort();
    const label = h('input', { class: 'label-input', list: 'task-labels', placeholder: 'Etiqueta', value: f.label || '', onchange: (e) => set({ label: e.target.value.trim().toLowerCase() }) });
    const week = h('input', { type: 'date', value: f.week || '', title: 'Semana prevista (cualquier día de la semana)', onchange: (e) => set({ week: e.target.value ? mondayOf(e.target.value) : '' }) });
    const thisWeek = mondayOf(todayISO());
    const statusSel = mode === 'list' ? selectEl([['', 'Todos'], ['pendiente,en_progreso,bloqueada,en_verificacion', 'Sin terminar'], ...Object.entries(TASK_STATUSES)], f.status || '', { onchange: (e) => set({ status: e.target.value }) }) : null;
    if (statusSel && f.status && ![...statusSel.options].some(o => o.value === f.status)) { statusSel.appendChild(h('option', { value: f.status }, f.status)); statusSel.value = f.status; }
    const active = FILTER_KEYS.filter(k => k !== 'q' && f[k] && (mode === 'list' || k !== 'status')).length;
    mount(filtersBox, h('div', { class: 'frow' },
      h('div', { class: 'group' }, h('span', null, 'Responsable'), who),
      h('div', { class: 'group' }, h('span', null, 'Prioridad'), prio),
      versions.length ? h('div', { class: 'group' }, h('span', null, 'Versión'), ver) : null,
      statusSel ? h('div', { class: 'group' }, h('span', null, 'Estado'), statusSel) : null,
      h('div', { class: 'group' }, h('span', null, 'Etiqueta'), label, h('datalist', { id: 'task-labels' }, labelsList.map(l => h('option', { value: l })))),
      h('div', { class: 'group' }, h('span', null, 'Semana'), week,
        h('button', { class: 'chip' + (f.week === thisWeek ? ' on' : ''), onclick: () => set({ week: f.week === thisWeek ? '' : thisWeek }) }, 'Esta'),
        h('button', { class: 'chip' + (f.week === addDays(thisWeek, 7) ? ' on' : ''), onclick: () => set({ week: f.week === addDays(thisWeek, 7) ? '' : addDays(thisWeek, 7) }) }, 'Próxima')),
      f.to_verify ? h('button', { class: 'chip on', onclick: () => set({ to_verify: '' }) }, 'Por verificar ×') : null,
      active ? h('button', { class: 'btn ghost sm', onclick: () => { search.value = ''; f = {}; persist(); drawFilters(); load(); } }, icon('x'), 'Quitar filtros') : null));
  }

  const params = () => {
    const p = {};
    for (const k of FILTER_KEYS) if (f[k] && !(mode === 'board' && k === 'status')) p[k] = f[k];
    return p;
  };

  // ---------- tarjeta y tablero ----------
  function card(t) {
    const draggable = can('tareas.trabajar') && t.status !== 'en_verificacion';
    const done = (t.checklist || []).filter(i => i.done).length, total = (t.checklist || []).length;
    const c = h('div', { class: `kcard ${t.status === 'bloqueada' ? 'blocked' : ''} ${t.status === 'resuelto' ? 'done' : ''} ${draggable ? '' : 'nodrag'}`, draggable: draggable ? 'true' : null,
      onclick: () => { location.hash = `#/tareas/${t.key}`; } },
    h('div', { class: 'row' }, h('a', { class: 'k', href: `#/tareas/${t.key}`, onclick: (e) => e.stopPropagation() }, t.key), h('span', { class: 'grow' }), priorityBadge(t.priority, t.priority_label)),
    h('div', { class: 't' }, t.title),
    t.status === 'bloqueada' && t.block_reason ? h('div', { class: 'reason ellipsis', title: t.block_reason }, icon('lock', 11), ' ', t.block_reason) : null,
    t.open_blockers ? h('div', { class: 'tiny warn-text' }, `Espera a ${t.open_blockers} ${t.open_blockers === 1 ? 'elemento' : 'elementos'}`) : null,
    t.status === 'en_verificacion' ? h('div', { class: 'tiny', style: { color: 'var(--purple)' } }, `Pendiente de verificar por ${t.created_by_name || 'quien la creó'}`) : null,
    h('div', { class: 'meta' },
      t.target_date ? h('span', { class: t.overdue ? 'overdue' : '' }, icon('calendar', 11), ' ', fmtDate(t.target_date)) : null,
      total ? h('span', { title: 'Lista de comprobación' }, icon('check', 11), ` ${done}/${total}`) : null,
      t.estimate_hours ? h('span', null, fmtHours(t.estimate_hours)) : null,
      t.recurrence ? h('span', { title: recurrenceText(t.recurrence) }, icon('repeat', 11)) : null,
      (t.labels_list || []).slice(0, 3).map(l => h('span', { class: 'lbl-chip' }, l)),
      t.assignee_id ? avatar(userById(t.assignee_id) || { display_name: t.assignee_name }) : null),
    t.progress && t.status !== 'resuelto' ? h('div', { class: 'progress' }, h('i', { style: { width: `${t.progress}%` } })) : null);
    if (draggable) {
      c.addEventListener('dragstart', (e) => { dragging = t; c.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', t.key); });
      c.addEventListener('dragend', () => { dragging = null; c.classList.remove('dragging'); root.querySelectorAll('.kcol.over').forEach(x => x.classList.remove('over')); });
    }
    return c;
  }

  async function moveTo(t, status) {
    if (t.status === status) return;
    const limits = store.settings.wip_limits || {};
    const lim = limits[status];
    const count = items.filter(x => x.status === status).length;
    if (lim && count >= lim && !await confirmDialog({ title: 'Límite de trabajo en curso', message: `La columna «${TASK_STATUSES[status]}» ya tiene ${count} tareas y el límite acordado es ${lim}. Terminar antes de empezar más ayuda a acabar antes.\n\n¿Moverla igualmente?`, confirmLabel: 'Mover igualmente' })) return;
    const r = await changeTaskStatus(t, status);
    if (r) load();
  }

  function drawBoard() {
    const limits = store.settings.wip_limits || {};
    const byCol = Object.fromEntries(COLUMNS.map(c => [c, []]));
    for (const t of items) (byCol[t.status] || byCol.pendiente).push(t);
    byCol.resuelto.sort((a, b) => (b.completed_at || '').localeCompare(a.completed_at || ''));
    const cols = COLUMNS.map(st => {
      const list = byCol[st];
      const shown = st === 'resuelto' ? list.slice(0, DONE_LIMIT) : list;
      const lim = limits[st];
      const over = lim && list.length > lim;
      const col = h('div', { class: `kcol ${st === 'bloqueada' ? 'blocked' : ''} ${st === 'en_verificacion' ? 'verify' : ''} ${st === 'resuelto' ? 'done' : ''} ${over ? 'wip-over' : ''}` },
        h('div', { class: 'kcol-head' }, st === 'bloqueada' ? icon('lock', 15) : st === 'en_verificacion' ? icon('verify', 15) : st === 'resuelto' ? icon('check', 15) : null,
          TASK_STATUSES[st], h('span', { class: 'n', title: lim ? `Límite de trabajo en curso: ${lim}` : '' }, lim ? `${list.length} / ${lim}` : list.length)),
        h('div', { class: 'kcol-body' }, shown.map(card),
          !list.length ? h('div', { class: 'kcol-more' }, st === 'bloqueada' ? 'Nada bloqueado' : 'Arrastra aquí') : null,
          list.length > shown.length ? h('div', { class: 'kcol-more' }, `y ${list.length - shown.length} más (usa la vista de lista)`) : null));
      col.addEventListener('dragover', (e) => { if (dragging) { e.preventDefault(); col.classList.add('over'); } });
      col.addEventListener('dragleave', (e) => { if (!col.contains(e.relatedTarget)) col.classList.remove('over'); });
      col.addEventListener('drop', (e) => { e.preventDefault(); col.classList.remove('over'); const t = dragging; dragging = null; if (t) moveTo(t, st); });
      return col;
    });
    mount(body, h('div', { class: 'kanban cols-5' }, cols),
      can('tareas.trabajar') ? h('div', { class: 'tiny muted mt' }, 'Arrastra las tarjetas para cambiar su estado. Al soltarlas en «En verificación» o «Resuelto» se marcan como hechas (pasan a verificación si las creó otra persona).') : null);
  }

  function drawList() {
    if (!items.length) { mount(body, emptyState('No hay tareas', can('tareas.crear') ? 'Crea una con «Nueva tarea».' : 'Prueba a quitar algún filtro.')); return; }
    const t = table([
      { label: 'Clave', render: (t) => h('span', { class: 'nowrap', style: { fontWeight: 600 } }, h('span', { class: 'project-dot', style: { background: t.project_color || 'var(--muted)', marginRight: '6px' } }), t.key) },
      { label: 'Título', cls: 'title-cell', render: (t) => h('div', null, t.title, (t.labels_list || []).map(l => h('span', { class: 'lbl-chip', style: { marginLeft: '6px' } }, l)),
        h('div', { class: 'sub' }, [t.version_name, t.planned_week ? `Semana del ${fmtDate(t.planned_week)}` : '', t.recurrence ? recurrenceText(t.recurrence) : '',
          t.status === 'bloqueada' && t.block_reason ? `Bloqueo: ${t.block_reason}` : ''].filter(Boolean).join(' · '))) },
      { label: 'Prioridad', render: (t) => priorityBadge(t.priority, t.priority_label) },
      { label: 'Estado', render: (t) => statusBadge(t.status, t.status_label) },
      { label: 'Responsable', render: (t) => t.assignee_id ? h('span', { class: 'who' }, avatar(userById(t.assignee_id) || { display_name: t.assignee_name }), t.assignee_name) : h('span', { class: 'muted small' }, 'Sin asignar') },
      { label: 'Progreso', cls: 'hide-sm', render: (t) => h('div', { class: 'prog' }, h('div', { class: `progress ${t.progress >= 100 ? 'ok' : ''}` }, h('i', { style: { width: `${t.progress || 0}%` } })), `${t.progress || 0}%`) },
      { label: 'Objetivo', render: (t) => t.target_date ? h('span', { class: `nowrap ${t.overdue ? 'overdue' : 'small'}` }, fmtDate(t.target_date)) : h('span', { class: 'muted' }, '—') },
      { label: 'Estim.', cls: 'hide-sm', render: (t) => h('span', { class: 'small' }, t.estimate_hours ? fmtHours(t.estimate_hours) : '—') },
    ], items, {
      onRow: (t) => { location.hash = `#/tareas/${t.key}`; },
      rowClass: (t) => [t.status === 'bloqueada' ? 'is-blocked' : '', t.priority === 'critica' && t.status !== 'resuelto' ? 'is-critical' : '', t.status === 'resuelto' ? 'is-resolved' : ''].join(' '),
    });
    t.querySelector('table')?.classList.add('task-table');
    mount(body, t);
  }

  function draw() {
    const open = items.filter(t => t.status !== 'resuelto').length;
    mount(countBox, `${items.length} ${items.length === 1 ? 'tarea' : 'tareas'}${items.length ? ` · ${open} sin terminar` : ''}`);
    if (mode === 'board') drawBoard(); else drawList();
  }

  async function load() {
    const token = ++reqToken;
    if (!items.length) mount(body, loading());
    try {
      const r = await api.get(`/api/tasks?${withProject(params())}`);
      if (token !== reqToken || !ctx.alive()) return;
      items = r.items;
      draw();
    } catch (err) {
      if (token === reqToken) mount(body, errorBox(err, load));
    }
  }

  search.addEventListener('input', debounce(() => { f.q = search.value.trim(); if (!f.q) delete f.q; persist(); load(); }, 250));
  const exportBtn = can('exportar') ? h('button', { class: 'btn', title: 'Exportar las tareas filtradas a CSV (Excel)' }, icon('download'), 'Exportar CSV') : null;
  exportBtn?.addEventListener('click', () => busy(exportBtn, async () => {
    const r = await api.save(`/api/export/tasks.csv?${withProject(params())}`, { folder: 'exportaciones' });
    toast(`Exportado a Exportaciones: ${r.name || 'tareas.csv'}`, 'ok');
  }).catch(() => {}));
  const openNew = (preset) => newTaskModal({ onCreated: () => load(), preset });
  drawMode();
  mount(root,
    pageHead('Tareas', modeSeg, store.project !== 'all' ? followButton('project', store.project, { label: 'Seguir el proyecto' }) : null,
      exportBtn, can('tareas.crear') ? h('button', { class: 'btn primary', onclick: () => openNew() }, icon('plus'), 'Nueva tarea') : null),
    h('div', { class: 'toolbar' }, h('div', { class: 'search' }, icon('search'), search)),
    filtersBox, countBox, body);
  drawFilters();
  const wantsNew = ctx.query.get('nueva') === '1';
  persist();
  await load();
  loadVersions(store.project).then(v => { versions = v; if (ctx.alive() && !filtersBox.contains(document.activeElement)) drawFilters(); });
  if (wantsNew && can('tareas.crear')) openNew({ title: ctx.query.get('title') || '', source_message_id: ctx.query.get('source_message_id') || undefined });
  ctx.onChanges(['task', 'user', 'project', 'version'], load);
}
