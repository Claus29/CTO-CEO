// Ficha de tarea: edición en línea (con control de versión), lista de comprobación, progreso,
// estado y bloqueo, verificación, comentarios, adjuntos, dependencias, tiempo e historial.
import { api } from '../api.js';
import { PRIORITIES } from '../labels.js';
import { can, projectById, store, userById } from '../state.js';
import {
  avatar, busy, confirmDialog, debounce, emptyState, errorBox, field, fmtDate, fmtDateTime, fmtHours, fmtRelative, h, icon, loading,
  menuButton, modal, mondayOf, mount, priorityBadge, richText, selectEl, statusBadge, tabs, toast,
} from '../ui.js';
import {
  activityTimeline, attachmentsSection, codeLinksList, commentsSection, followButton, relationsSection, resolutionHistory, timeWidget, userSelect,
  verificationPanel,
} from './shared.js';
import { changeTaskStatus, loadVersions, recurrenceControl, recurrenceText } from './tasks.js';

export default async function render(ctx) {
  const { root } = ctx;
  const ident = ctx.params.key;
  let item = null;
  let versions = null;
  let tab = 'comments';
  let editingDesc = false;

  const headBox = h('div');
  const alertsBox = h('div', { class: 'col' });
  const descBox = h('div');
  const checkBox = h('div');
  const sideInfo = h('div', { class: 'card' });
  const codeBox = h('div', { class: 'card' });
  const tabsBar = h('div');
  const historyBox = h('div', { class: 'hidden' });
  const resBox = h('div', { class: 'hidden' });
  let sec = null;

  const me = () => store.me.id;
  const touch = () => can('tareas.editar') || item.created_by === me() || item.assignee_id === me();
  const work = () => can('tareas.trabajar');
  const planEdit = () => touch() || work();
  const path = () => `/api/tasks/${encodeURIComponent(item.key)}`;

  async function save(fields, okMsg, btn) {
    try {
      const r = await busy(btn, () => api.patch(path(), { ...fields, version: item.version }), okMsg);
      if (r.item) item = { ...item, ...r.item };
      await reload(true);
      return r;
    } catch (err) {
      if (err.status === 409) await reload(true);
      return null;
    }
  }
  async function setStatus(status) {
    const r = await changeTaskStatus(item, status);
    if (r) await reload(true);
  }

  // ---------- cabecera ----------
  function editTitle(h1) {
    const input = h('input', { class: 'title-edit', value: item.title, maxlength: 200 });
    let done = false;
    const finish = async (commit) => {
      if (done) return; done = true;
      const v = input.value.trim();
      if (commit && v && v !== item.title) await save({ title: v }, 'Título actualizado.');
      else drawHead();
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') finish(true); if (e.key === 'Escape') finish(false); });
    input.addEventListener('blur', () => finish(true));
    h1.replaceWith(input);
    input.focus(); input.select();
  }

  function drawHead() {
    const st = item.status;
    const btn = (label, ic, kind, onClick) => { const b = h('button', { class: `btn ${kind || ''}` }, ic ? icon(ic) : null, label); b.addEventListener('click', () => onClick(b)); return b; };
    const actions = [];
    if (work()) {
      if (st === 'pendiente') actions.push(btn('Empezar', 'play', 'primary', () => setStatus('en_progreso')));
      if (st === 'bloqueada') actions.push(btn('Quitar bloqueo', 'play', '', () => setStatus('en_progreso')));
      if (['pendiente', 'en_progreso', 'bloqueada'].includes(st)) actions.push(btn('Marcar como hecha…', 'check', 'ok', () => setStatus('resuelto')));
      if (['pendiente', 'en_progreso'].includes(st)) actions.push(btn('Bloquear…', 'lock', '', () => setStatus('bloqueada')));
      if (st === 'resuelto') actions.push(btn('Reabrir', 'undo', '', async () => {
        if (await confirmDialog({ title: `Reabrir ${item.key}`, message: 'La tarea volverá a «Pendiente».', confirmLabel: 'Reabrir' })) setStatus('pendiente');
      }));
    }
    const more = menuButton(h('span', { class: 'row', style: { gap: '6px' } }, icon('dots', 15), 'Más'), [
      { label: 'Volver a pendiente', icon: 'undo', onClick: () => setStatus('pendiente'), hidden: !work() || !['en_progreso', 'bloqueada'].includes(st) },
      { label: 'Actualizar el motivo del bloqueo', icon: 'lock', onClick: blockReasonDialog, hidden: !work() || st !== 'bloqueada' },
      { label: 'Asignármela', icon: 'user', onClick: () => save({ assignee_id: me() }, `Te has asignado ${item.key}.`), hidden: !work() || item.assignee_id === me() || st === 'resuelto' },
      { label: 'Copiar la clave', icon: 'copy', onClick: () => navigator.clipboard?.writeText(item.key).then(() => toast(`${item.key} copiada.`, 'ok')).catch(() => toast(item.key, 'info')) },
      { label: 'Eliminar la tarea…', icon: 'trash', danger: true, onClick: deleteTask, hidden: !(can('tareas.eliminar') || item.created_by === me()) },
    ], { class: 'btn' });
    const h1 = h('h1', { class: touch() ? 'editable' : '', title: touch() ? 'Haz clic para cambiar el título' : '' }, item.title);
    if (touch()) h1.addEventListener('click', () => editTitle(h1));
    mount(headBox, h('div', { class: 'detail-head' },
      h('div', { class: 'grow' },
        h('div', { class: 'row' }, h('a', { class: 'small', href: '#/tareas', title: 'Volver a las tareas' }, icon('left', 14)),
          h('span', { class: 'key' }, item.key), h('span', { class: 'project-dot', style: { background: item.project_color || 'var(--muted)' } }),
          h('span', { class: 'small muted' }, item.project_name || projectById(item.project_id)?.name || '')),
        h1,
        h('div', { class: 'ws-badges' }, priorityBadge(item.priority, item.priority_label), statusBadge(st, item.status_label),
          item.overdue ? h('span', { class: 'badge danger' }, 'Vencida') : null,
          item.version_name ? h('span', { class: 'badge info' }, item.version_name) : null,
          item.recurrence ? h('span', { class: 'badge neutral', title: 'Tarea que se repite' }, icon('repeat', 12), recurrenceText(item.recurrence)) : null,
          (item.labels_list || []).map(l => h('a', { class: 'lbl-chip', href: `#/tareas?label=${encodeURIComponent(l)}` }, l)))),
      h('div', { class: 'actions' }, followButton('task', item.id), actions, more)));
  }

  async function deleteTask() {
    const ok = await confirmDialog({
      title: `Eliminar ${item.key}`, danger: true, confirmLabel: 'Eliminar definitivamente',
      message: `Se eliminará «${item.title}» por completo: sus comentarios, adjuntos, tiempo registrado, dependencias y enlaces.\n\nNo se puede deshacer desde el programa (sí está en las copias de seguridad anteriores).`,
    });
    if (!ok) return;
    try {
      await api.del(`/api/tasks/${item.id}`);
      toast(`${item.key} eliminada.`, 'ok');
      location.hash = '#/tareas';
    } catch (err) { toast(err.message, 'error'); }
  }

  function blockReasonDialog() {
    const ta = h('textarea', { rows: 3 }); ta.value = item.block_reason || '';
    modal({ title: `Motivo del bloqueo de ${item.key}`, body: field('¿Qué impide avanzar?', ta, { required: true }), actions: [{ label: 'Cancelar' }, { label: 'Guardar', kind: 'primary', onClick: async () => {
      if (!ta.value.trim()) { toast('Indica el motivo.', 'error'); return false; }
      return (await save({ status: 'bloqueada', block_reason: ta.value.trim() }, 'Motivo actualizado.')) ? undefined : false;
    } }] });
  }

  // ---------- cuerpo ----------
  function drawAlerts() {
    const out = [];
    if (item.status === 'bloqueada') {
      out.push(h('div', { class: 'block-callout' }, h('div', { class: 't' }, icon('lock', 16), 'Bloqueada'),
        item.block_reason ? h('div', { class: 'pre', style: { marginTop: '6px' } }, richText(item.block_reason)) : null));
    }
    if (item.status === 'en_progreso' && item.last_reject_reason) {
      out.push(h('div', { class: 'callout danger' }, h('span', { class: 'lbl' }, 'Se rechazó en la verificación: '), richText(item.last_reject_reason)));
    }
    if (item.status === 'resuelto' && item.resolution_note) {
      out.push(h('div', { class: 'callout ok small' }, h('span', { class: 'lbl' }, 'Nota de resolución: '), richText(item.resolution_note)));
    }
    out.push(verificationPanel(item, 'task', () => reload(true)));
    mount(alertsBox, out);
  }

  function drawDesc() {
    if (editingDesc) return;
    const editBtn = touch() ? h('button', { class: 'btn ghost sm', onclick: startDesc }, icon('edit'), 'Editar') : null;
    mount(descBox, h('div', { class: 'ws-head row' }, h('div', { class: 'section-title grow', style: { margin: 0 } }, 'Descripción'), editBtn),
      item.description ? h('div', { class: 'text-block mt', style: { marginTop: '8px' } }, richText(item.description))
        : h('div', { class: 'ws-empty' }, touch() ? 'Sin descripción. Pulsa «Editar» para añadirla.' : 'Sin descripción.'));
  }
  function startDesc() {
    editingDesc = true;
    const ta = h('textarea', { rows: 8 }); ta.value = item.description || '';
    const saveBtn = h('button', { class: 'btn primary sm' }, 'Guardar');
    const stop = () => { editingDesc = false; drawDesc(); };
    saveBtn.addEventListener('click', async () => {
      if (ta.value.trim() === (item.description || '')) return stop();
      editingDesc = false;
      const r = await save({ description: ta.value.trim() }, 'Descripción guardada.', saveBtn);
      if (!r) editingDesc = true;
    });
    ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) saveBtn.click(); if (e.key === 'Escape') stop(); });
    mount(descBox, h('div', { class: 'section-title', style: { marginTop: 0 } }, 'Descripción'), h('div', { class: 'col' }, ta,
      h('div', { class: 'row end' }, h('button', { class: 'btn sm', onclick: stop }, 'Cancelar'), saveBtn)));
    ta.focus();
  }

  function drawChecklist() {
    const list = item.checklist || [];
    const done = list.filter(i => i.done).length;
    const editable = planEdit() && !['resuelto', 'en_verificacion'].includes(item.status);
    const update = (next, msg) => save({ checklist: next }, msg);
    const add = h('input', { placeholder: 'Añadir un elemento y pulsar Intro', maxlength: 300 });
    add.addEventListener('keydown', async (e) => {
      if (e.key !== 'Enter' || !add.value.trim()) return;
      e.preventDefault();
      const text = add.value.trim();
      add.disabled = true;
      await update([...list, { text, done: false }]);
      add.disabled = false;
    });
    const suggested = list.length ? Math.round((done / list.length) * 100) : null;
    const range = h('input', { type: 'range', min: 0, max: 100, step: 5, value: item.progress || 0, disabled: !editable });
    const pct = h('b', null, `${item.progress || 0} %`);
    range.addEventListener('input', () => { pct.textContent = `${range.value} %`; });
    range.addEventListener('change', () => save({ progress: Number(range.value) }, null));
    mount(checkBox,
      h('div', { class: 'section-title', style: { marginTop: 0 } }, 'Lista de comprobación', list.length ? h('span', { class: 'count' }, ` (${done}/${list.length})`) : null),
      list.length ? h('div', { class: 'checklist' }, list.map((it, idx) => {
        const cb = h('input', { type: 'checkbox', checked: it.done, disabled: !editable });
        cb.addEventListener('change', () => update(list.map((x, i) => (i === idx ? { ...x, done: cb.checked } : x))));
        return h('label', { class: `check-item ${it.done ? 'done' : ''}` }, cb, h('span', { class: 'txt' }, it.text),
          editable ? h('button', { class: 'x', type: 'button', title: 'Quitar', onclick: (e) => { e.preventDefault(); update(list.filter((_, i) => i !== idx), 'Elemento quitado.'); } }, '×') : null);
      })) : h('div', { class: 'ws-empty' }, 'Sin lista de comprobación.'),
      editable ? add : null,
      h('div', { class: 'section-title' }, 'Progreso'),
      h('div', { class: 'progress-line' }, h('div', { class: `progress ${item.progress >= 100 ? 'ok' : ''}` }, h('i', { style: { width: `${item.progress || 0}%` } })), pct),
      editable ? h('div', { class: 'row mt' }, range,
        suggested !== null && suggested !== item.progress ? h('button', { class: 'btn ghost sm nowrap', onclick: (e) => save({ progress: suggested }, 'Progreso actualizado.', e.currentTarget) }, `Usar ${suggested} % (según la lista)`) : null) : null);
  }

  // ---------- panel lateral ----------
  function drawSide() {
    const st = item.status;
    let assignee;
    if (can('tareas.asignar') && st !== 'resuelto') {
      assignee = userSelect(item.assignee_id || '', { projectId: item.project_id });
      assignee.addEventListener('change', () => save({ assignee_id: assignee.value ? Number(assignee.value) : null }, 'Responsable actualizado.'));
    } else {
      assignee = h('span', { class: 'row' }, item.assignee_id ? [avatar(userById(item.assignee_id) || { display_name: item.assignee_name }), item.assignee_name] : h('span', { class: 'muted' }, 'Sin asignar'),
        work() && st !== 'resuelto' && item.assignee_id !== me() ? h('button', { class: 'btn ghost sm', onclick: (e) => save({ assignee_id: me() }, `Te has asignado ${item.key}.`, e.currentTarget) }, 'Asignármela') : null);
    }
    const onChange = (el, key, conv = (v) => v || null, msg = 'Guardado.') => { el.addEventListener('change', () => save({ [key]: conv(el.value) }, msg)); return el; };
    const prio = touch() ? onChange(selectEl(Object.entries(PRIORITIES), item.priority), 'priority', v => v, 'Prioridad actualizada.') : priorityBadge(item.priority, item.priority_label);
    const target = planEdit() ? onChange(h('input', { type: 'date', value: item.target_date || '', class: item.overdue ? 'overdue' : '' }), 'target_date', undefined, 'Fecha objetivo actualizada.')
      : h('span', { class: item.overdue ? 'overdue' : '' }, item.target_date ? fmtDate(item.target_date) : '—');
    const week = planEdit() ? onChange(h('input', { type: 'date', value: item.planned_week || '', title: 'Se usa la semana que contiene el día elegido' }), 'planned_week', v => (v ? mondayOf(v) : null), 'Semana prevista actualizada.')
      : h('span', null, item.planned_week ? `Semana del ${fmtDate(item.planned_week)}` : '—');
    let version = h('span', null, item.version_name || '—');
    if (touch() && versions) {
      version = selectEl([['', 'Sin versión'], ...versions.filter(v => v.status !== 'publicada' || v.id === item.version_id).map(v => [String(v.id), v.name])], item.version_id ? String(item.version_id) : '');
      onChange(version, 'version_id', v => (v ? Number(v) : null), 'Versión actualizada.');
    }
    const est = touch() ? onChange(h('input', { type: 'number', min: 0, step: 0.5, value: item.estimate_hours ?? '', placeholder: 'Horas' }), 'estimate_hours', v => (v === '' ? null : Number(v)), 'Estimación actualizada.')
      : h('span', null, item.estimate_hours ? fmtHours(item.estimate_hours) : '—');
    const labels = touch() ? onChange(h('input', { value: (item.labels_list || []).join(', '), placeholder: 'Separadas por comas' }), 'labels', v => v, 'Etiquetas actualizadas.')
      : h('span', null, (item.labels_list || []).join(', ') || '—');
    let rec = h('span', null, item.recurrence ? recurrenceText(item.recurrence) : 'No se repite');
    if (touch()) {
      rec = recurrenceControl(item.recurrence);
      const fire = () => save({ recurrence: rec.value() }, 'Repetición actualizada.');
      rec.freq.addEventListener('change', fire);
      rec.interval.addEventListener('change', fire);
    }
    mount(sideInfo, h('h3', null, 'Detalles'), h('div', { class: 'kv' },
      h('span', null, 'Responsable'), assignee,
      h('span', null, 'Prioridad'), prio,
      h('span', null, 'Fecha objetivo'), target,
      h('span', null, 'Semana prevista'), week,
      h('span', null, 'Versión'), version,
      h('span', null, 'Estimación'), est,
      h('span', null, 'Etiquetas'), labels,
      h('span', null, 'Repetición'), rec,
      h('span', null, 'Creada'), h('span', { title: fmtDateTime(item.created_at) }, `${item.created_by_name || ''} · ${fmtRelative(item.created_at)}`),
      h('span', null, 'Último cambio'), h('span', { title: fmtDateTime(item.updated_at) }, `${item.updated_by_name || ''} · ${fmtRelative(item.updated_at)}`),
      item.started_at ? [h('span', null, 'Iniciada'), h('span', { title: fmtDateTime(item.started_at) }, fmtRelative(item.started_at))] : null,
      item.completed_at ? [h('span', null, 'Completada'), h('span', { title: fmtDateTime(item.completed_at) }, fmtRelative(item.completed_at))] : null,
      item.source_message ? [h('span', null, 'Origen'), h('a', { class: 'ellipsis', href: `#/chat?m=${item.source_message.id}`, title: item.source_message.title }, 'Mensaje del chat')] : null));
    mount(codeBox, h('h3', null, 'Código (GitHub)'), codeLinksList(item.code_links));
  }

  function drawTabs() {
    mount(tabsBar, tabs([
      { key: 'comments', label: 'Comentarios', icon: 'chat', count: (item.comments || []).length },
      { key: 'history', label: 'Historial', icon: 'activity' },
      { key: 'resolutions', label: 'Resoluciones', icon: 'verify', count: (item.resolutions || []).length },
    ], tab, (k) => { tab = k; drawTabs(); }));
    sec.comments.classList.toggle('hidden', tab !== 'comments');
    historyBox.classList.toggle('hidden', tab !== 'history');
    resBox.classList.toggle('hidden', tab !== 'resolutions');
    if (tab === 'history') mount(historyBox, activityTimeline(item.history));
    if (tab === 'resolutions') mount(resBox, resolutionHistory(item.resolutions));
  }

  function drawAll() {
    ctx.setTitle(`${item.key} · ${item.title}`);
    drawHead(); drawAlerts(); drawDesc(); drawChecklist(); drawSide(); drawTabs();
  }

  // ---------- carga ----------
  const fetchItem = async () => (await api.get(`/api/tasks/${encodeURIComponent(ident)}`)).item;
  let deferred = false;
  const editing = () => {
    const a = document.activeElement;
    return a && ['INPUT', 'SELECT', 'TEXTAREA'].includes(a.tagName) && (sideInfo.contains(a) || headBox.contains(a) || checkBox.contains(a) || descBox.contains(a));
  };
  async function reload(force = false) {
    if (!ctx.alive()) return;
    if (!force && (editing() || editingDesc)) { deferred = true; return; }
    try { item = await fetchItem(); } catch (err) {
      if (err.status === 404) mount(root, emptyState('Esta tarea ya no está disponible', err.message));
      else toast(err.message, 'error');
      return;
    }
    drawAll();
    sec.comments.refresh(item); sec.atts.refresh(item); sec.rel.refresh(item); sec.time.refresh(item);
  }
  root.addEventListener('focusout', () => setTimeout(() => { if (deferred && !editing() && !editingDesc) { deferred = false; reload(); } }, 0));

  mount(root, loading());
  try { item = await fetchItem(); } catch (err) {
    mount(root, err.status === 404 ? emptyState('No se ha encontrado la tarea', err.message) : errorBox(err, () => ctx.refresh()));
    return;
  }
  if (!ctx.alive()) return;
  sec = {
    comments: commentsSection('task', item.id, { data: item, live: false, title: null }),
    atts: attachmentsSection('task', item.id, { data: item, live: false }),
    rel: relationsSection('task', item.id, { data: item, live: false }),
    time: timeWidget('task', item.id, { data: item, live: false }),
  };
  mount(root, headBox, h('div', { class: 'detail' },
    h('div', { class: 'detail-main' }, alertsBox, descBox, h('div', { class: 'card' }, checkBox), sec.atts, h('div', null, tabsBar, sec.comments, historyBox, resBox)),
    h('div', { class: 'detail-side' }, sideInfo, h('div', { class: 'card' }, sec.time), h('div', { class: 'card' }, sec.rel), codeBox)));
  drawAll();
  loadVersions(item.project_id).then(v => { versions = v; if (ctx.alive() && !editing()) drawSide(); });

  const onChange = debounce(() => reload(), 250);
  ctx.on('changes', (d) => {
    if (d.changes.some(c => ['task', 'time'].includes(c.kind) && c.entity_type === 'task' && c.entity_id === item.id)) onChange();
    if (d.changes.some(c => c.kind === 'version')) loadVersions(item.project_id).then(v => { versions = v; if (ctx.alive() && !editing()) drawSide(); });
  });
  ctx.on('reconnected', onChange);
  ctx.on('reset', onChange);
}
