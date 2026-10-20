// Calendario mensual: fechas objetivo, versiones, fechas importantes, renovaciones, objetivos, ausencias y tiendas.
import { api } from '../api.js';
import { can, projectById, store, withProject } from '../state.js';
import {
  addDays, confirmDialog, DAYS, errorBox, field, fmtDate, h, icon, loadJSON, loading, modal, mondayOf, mount,
  MONTHS, pageHead, saveJSON, toast, todayISO, uid,
} from '../ui.js';
import { daysBetween, multiProject, projectField, requireFields, submit } from './_plan_shared.js';

const TYPES = {
  incident: 'Incidencias', task: 'Tareas', version: 'Versiones', date: 'Fechas importantes', renewal: 'Renovaciones',
  goals: 'Objetivos semanales', absence: 'Ausencias', store: 'Tiendas',
};
const PREF = 'dc.calendar.hidden';
const MAX_PER_DAY = 4;

function hrefFor(ev) {
  switch (ev.type) {
    case 'incident': return `#/incidencias/${ev.key}`;
    case 'task': return `#/tareas/${ev.key}`;
    case 'version': return `#/versiones?id=${ev.id}`;
    case 'renewal': return '#/renovaciones';
    case 'goals': return `#/semana?semana=${ev.week}`;
    case 'absence': return '#/equipo/ausencias';
    case 'store': return '#/tiendas';
    default: return null;
  }
}
const isDone = (ev) => ['resuelto', 'resuelta', 'duplicada', 'publicada'].includes(ev.status);

export default async function render(ctx) {
  const { root } = ctx;
  const canEdit = can('planificacion.editar');
  const q = ctx.query.get('mes');
  let month = q && /^\d{4}-\d{2}$/.test(q) ? q : todayISO().slice(0, 7);
  let events = [], dates = [];
  const hidden = new Set(loadJSON(PREF, []));
  const titleBox = h('h2', { class: 'cal-title' });
  const chipsBox = h('div', { class: 'filters' });
  const calBox = h('div', null, loading());
  const datesBox = h('div');

  const range = () => {
    const first = `${month}-01`;
    const [y, m] = month.split('-').map(Number);
    const last = `${month}-${String(new Date(y, m, 0).getDate()).padStart(2, '0')}`;
    const from = mondayOf(first);
    const to = addDays(mondayOf(last), 6);
    return { first, last, from, to };
  };

  function setMonth(mo) {
    month = mo;
    history.replaceState(null, '', `#/calendario?mes=${mo}`);
    mount(calBox, loading());
    load();
  }
  const shift = (n) => { const [y, m] = month.split('-').map(Number); const d = new Date(y, m - 1 + n, 1); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; };

  async function load() {
    const { from, to } = range();
    const mo = month;
    try {
      const [c, d] = await Promise.all([api.get(`/api/calendar?${withProject({ from, to })}`), api.get('/api/dates')]);
      if (!ctx.alive() || mo !== month) return;
      events = c.events; dates = d.items;
    } catch (err) { mount(calBox, errorBox(err, load)); return; }
    draw();
  }

  function evEl(ev) {
    const cls = ['ev', ev.type, isDone(ev) ? 'done' : '', ev.overdue ? 'late' : ''].filter(Boolean).join(' ');
    const label = `${ev.key ? ev.key + ' ' : ''}${ev.title}`;
    return h('div', { class: cls, title: `${TYPES[ev.type] || ev.type}: ${label}${ev.overdue ? ' (vencida)' : ''}`, role: 'button', tabindex: '0',
      onclick: (e) => { e.stopPropagation(); openEvent(ev); },
      onkeydown: (e) => { if (e.key === 'Enter') { e.stopPropagation(); openEvent(ev); } } }, label);
  }

  function openEvent(ev) {
    if (ev.type === 'date') { dateDetail(dates.find(d => d.id === ev.id) || { id: ev.id, title: ev.title, date: ev.date, description: ev.description }); return; }
    const href = hrefFor(ev);
    if (href) location.hash = href;
  }

  function dayModal(iso, list) {
    const [y, m, d] = iso.split('-').map(Number);
    const dt = new Date(y, m - 1, d);
    modal({ title: `${DAYS[dt.getDay()]}, ${d} de ${MONTHS[m - 1]} de ${y}`,
      body: h('div', { class: 'col' },
        list.length ? list.map(ev => h('div', { class: 'row cal-day-item' }, h('span', { class: 'badge neutral nowrap' }, TYPES[ev.type] || ev.type), h('div', { class: 'grow' }, evEl(ev))))
          : h('div', { class: 'empty small' }, 'Nada este día.')),
      actions: [canEdit ? { label: 'Añadir fecha importante', onClick: () => { setTimeout(() => dateForm(iso), 0); } } : null, { label: 'Cerrar', kind: 'primary' }].filter(Boolean) });
  }

  function dateDetail(d) {
    const p = d.project_id ? projectById(d.project_id) : null;
    modal({ title: d.title,
      body: h('div', { class: 'col' },
        h('div', { class: 'kv' }, h('span', null, 'Fecha'), h('b', null, fmtDate(d.date)),
          h('span', null, 'Proyecto'), h('span', null, p ? `${p.key} · ${p.name}` : 'Todos los proyectos')),
        d.description ? h('div', { class: 'text-block' }, d.description) : null),
      actions: [canEdit ? { label: 'Borrar', kind: 'danger', onClick: () => { setTimeout(() => deleteDate(d), 0); } } : null, { label: 'Cerrar', kind: 'primary' }].filter(Boolean) });
  }

  async function deleteDate(d) {
    if (!await confirmDialog({ title: 'Borrar fecha importante', message: `¿Borrar «${d.title}» (${fmtDate(d.date)})?`, confirmLabel: 'Borrar', danger: true })) return;
    try { await api.del(`/api/dates/${d.id}`); toast('Fecha borrada.', 'ok'); load(); } catch (err) { toast(err.message, 'error'); }
  }

  function dateForm(iso) {
    const title = h('input', { maxlength: 200, placeholder: 'Ej.: Lanzamiento, feria, entrega al cliente…' });
    const date = h('input', { type: 'date', value: iso || todayISO() });
    const desc = h('textarea', { rows: 3, placeholder: 'Opcional' });
    let pid = store.project === 'all' ? '' : String(store.project);
    const proj = projectField(pid, (v) => { pid = v; }, { optional: true });
    if (!multiProject()) proj.el.classList.add('hidden');
    modal({ title: 'Nueva fecha importante',
      body: h('div', { class: 'form-grid' }, field('Título', title, { required: true, cls: 'span2' }), field('Fecha', date, { required: true }), proj.el,
        field('Descripción', desc, { cls: 'span2' })),
      actions: [{ label: 'Cancelar' }, { label: 'Añadir', kind: 'primary', onClick: () => {
        if (!requireFields([[title, 'el título'], [date, 'la fecha']])) return false;
        return submit(() => api.post('/api/dates', { uid: uid(), title: title.value.trim(), date: date.value, description: desc.value.trim(), project_id: pid ? Number(pid) : null }, { queue: true }),
          'Fecha importante añadida.', load);
      } }] });
  }

  function drawChips() {
    const counts = {};
    for (const ev of events) counts[ev.type] = (counts[ev.type] || 0) + 1;
    mount(chipsBox, h('div', { class: 'group' }, h('span', null, 'Mostrar'),
      Object.entries(TYPES).map(([k, label]) => h('button', { class: 'chip' + (hidden.has(k) ? '' : ' on'),
        onclick: () => { hidden.has(k) ? hidden.delete(k) : hidden.add(k); saveJSON(PREF, [...hidden]); draw(); } },
      h('i', { class: `cal-sw ${k}` }), label, counts[k] ? h('span', { class: 'muted tiny' }, counts[k]) : null))));
  }

  function draw() {
    const { from, to } = range();
    const [y, m] = month.split('-').map(Number);
    titleBox.textContent = `${MONTHS[m - 1][0].toUpperCase()}${MONTHS[m - 1].slice(1)} de ${y}`;
    drawChips();
    const byDay = {};
    for (const ev of events) if (!hidden.has(ev.type)) (byDay[ev.date] ||= []).push(ev);
    const today = todayISO();
    const cells = [];
    for (const d of ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']) cells.push(h('div', { class: 'dow' }, d));
    for (let iso = from; iso <= to; iso = addDays(iso, 1)) {
      const list = byDay[iso] || [];
      const day = iso;
      const cls = ['day', iso.slice(0, 7) !== month ? 'other' : '', iso === today ? 'today' : ''].filter(Boolean).join(' ');
      cells.push(h('div', { class: cls, onclick: () => dayModal(day, list), title: 'Ver el día' },
        h('div', { class: 'row between' }, h('span', { class: 'num' }, Number(iso.slice(8))),
          canEdit ? h('button', { class: 'cal-add', title: 'Añadir fecha importante este día', onclick: (e) => { e.stopPropagation(); dateForm(day); } }, icon('plus', 12)) : null),
        list.slice(0, MAX_PER_DAY).map(evEl),
        list.length > MAX_PER_DAY ? h('button', { class: 'cal-more', onclick: (e) => { e.stopPropagation(); dayModal(day, list); } }, `+${list.length - MAX_PER_DAY} más`) : null));
    }
    mount(calBox, h('div', { class: 'cal' }, cells));
    drawDates();
  }

  function drawDates() {
    const today = todayISO();
    const pid = store.project === 'all' ? null : Number(store.project);
    const list = dates.filter(d => !pid || !d.project_id || d.project_id === pid);
    const upcoming = list.filter(d => d.date >= today);
    const past = list.filter(d => d.date < today).reverse().slice(0, 5);
    const row = (d) => {
      const days = daysBetween(today, d.date);
      return h('div', { class: 'list-item', onclick: () => dateDetail(d) },
        icon('flag', 14),
        h('span', { class: 'nowrap small' }, fmtDate(d.date)),
        h('span', { class: 'grow ellipsis' }, d.title),
        multiProject() ? h('span', { class: 'tiny muted' }, d.project_id ? projectById(d.project_id)?.key || '' : 'Todos') : null,
        h('span', { class: 'tiny muted nowrap' }, days === 0 ? 'hoy' : days > 0 ? `en ${days} d` : `hace ${-days} d`),
        canEdit ? h('button', { class: 'btn ghost sm icon', title: 'Borrar', onclick: (e) => { e.stopPropagation(); deleteDate(d); } }, icon('trash')) : null);
    };
    mount(datesBox, h('div', { class: 'list-card mt' },
      h('div', { class: 'card-head' }, h('h3', null, 'Fechas importantes del proyecto'),
        canEdit ? h('button', { class: 'btn sm', onclick: () => dateForm(null) }, icon('plus'), 'Nueva fecha') : null),
      upcoming.length ? h('div', null, upcoming.map(row)) : h('div', { class: 'empty small' }, canEdit ? 'No hay fechas próximas. Añade lanzamientos, ferias, entregas…' : 'No hay fechas próximas.'),
      past.length ? h('details', { class: 'past-dates' }, h('summary', { class: 'small muted' }, 'Fechas pasadas recientes'), past.map(row)) : null));
  }

  mount(root,
    pageHead('Calendario',
      h('div', { class: 'row' },
        h('button', { class: 'btn icon', title: 'Mes anterior', onclick: () => setMonth(shift(-1)) }, icon('left')),
        h('button', { class: 'btn', onclick: () => setMonth(todayISO().slice(0, 7)) }, 'Hoy'),
        h('button', { class: 'btn icon', title: 'Mes siguiente', onclick: () => setMonth(shift(1)) }, icon('right'))),
      canEdit ? h('button', { class: 'btn primary', onclick: () => dateForm(null) }, icon('plus'), 'Fecha importante') : null),
    h('div', { class: 'row mb' }, titleBox),
    chipsBox, calBox, datesBox);
  await load();
  ctx.onChanges(['incident', 'task', 'version', 'date', 'renewal', 'goal', 'absence', 'store'], load);
}
